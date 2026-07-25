# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "numpy>=2", "faster-whisper>=1.1",
#   # ctranslate2's CUDA backend links these by soname; pip-installing them
#   # avoids a system CUDA toolkit (and a sudo password we don't have).
#   "nvidia-cublas-cu12", "nvidia-cudnn-cu12>=9",
# ]
# ///
"""Audio analysis pass: Tier A DSP + ASR, one JSON sidecar per clip.

Implements docs/AUDIO.md. Audio is the fast pass — it answers *when* something
happens so the expensive visual pass knows where to look.

The wind trap governs the whole design: a GoPro at speed produces enormous
broadband low-frequency noise, so full-band RMS ranks every traverse above the
best line anyone said all day. Nothing here scores on full-band energy. Speech
is separated from wind by three independent properties, combined as an AND:

    band     speech lives in 300 Hz-3.4 kHz, wind below ~200 Hz
    flatness wind is noise-like (flat spectrum); voice is harmonic
    rhythm   voice is syllabic (~4 Hz envelope modulation); wind is sustained

Wind level is reported as a *quality* signal (is this audio usable in the edit)
and is deliberately kept out of the interest score.

Usage:
    uv run audio_analyze.py VIDEO_OR_DIR -o OUTDIR [--no-asr] [--device auto]
"""

from __future__ import annotations

import argparse
import ctypes
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".mts", ".webm"}

SR = 16000
N_FFT = 1024                # 64 ms window
HOP = 160                   # 100 Hz analysis rate
OUT_HZ = 10                 # sidecar track rate; DECIM analysis frames per track frame
DECIM = 10

BAND_LOW = (20.0, 200.0)        # wind
BAND_SPEECH = (300.0, 3400.0)   # voice
BAND_HIGH = (4000.0, 7800.0)
BAND_FLAT = (250.0, 6000.0)     # flatness measured where the decision matters
BAND_ONSET = (250.0, 7800.0)

# --- detector parameters, measured in R8 against the ASR transcript ---------
# Operating point: P=0.59 R=0.62 on Copper. That is weak — see R8 for why the
# DSP speech detector is a *quality track* here and not a candidate source.
SPEECH_DB_MIN = -30.0       # speech-band level, dBFS
FLATNESS_MAX = 0.10         # above this the frame is noise-like
MOD_MIN = 0.35              # 2-8 Hz share of the envelope spectrum
SPEECH_SCORE_MIN = 0.5      # speech_score above this counts as speech
MIN_SPEECH_RUN_S = 0.6
WIND_DOM_DB = 12.0          # low-band exceeds speech-band by this much
WIND_DOM_FLAT = 0.40
SILENCE_DB = -60.0
ONSET_Z_MIN = 8.0           # R8: z>3 fires 15x/min on Copper, which is flooding
ONSET_PCT_FLOOR = 97.0      # and must also be a large flux in absolute terms
ONSET_WINDOW_S = 5.0

# Things people say *because* something just happened. Split by strength after
# R8: on Copper, "yeah" and "dude" are the ambient register of the whole trip
# (they appear throughout 40 seconds of banter about a carton of milk), so
# scoring them as reactions promoted filler to the top of the candidate list.
# A marker has to be surprising to be evidence.
MARKERS_STRONG = [
    r"\boh my go(d|sh)\b", r"\bholy \w+", r"\bdid you see\b", r"\bno way\b",
    r"\bthat was (sick|insane|crazy|nuts|awesome|amazing|so good)\b",
    r"\bthat('s| is) (sick|crazy|insane|unreal)\b", r"\bnice one\b",
    r"\blet'?s go+\b", r"\bwhoa+\b", r"\bwoo+h?\b", r"\bwhoo+\b",
    r"\bare you (okay|alright)\b", r"\byou good\b", r"\bwatch (this|out)\b",
    r"\b(full )?send it\b", r"\bgoes so hard\b", r"\bso fucking hard\b",
]
MARKERS_WEAK = [r"\bsick\b", r"\binsane\b", r"\bunreal\b", r"\bnasty\b",
                r"\bjesus\b", r"\bgod damn\b", r"\bwild\b"]
MARKER_RE_STRONG = [re.compile(p, re.I) for p in MARKERS_STRONG]
MARKER_RE_WEAK = [re.compile(p, re.I) for p in MARKERS_WEAK]

# Whisper emits these over noise with nothing said. Dropping them costs a real
# utterance occasionally; keeping them poisons the candidate list constantly.
HALLUCINATIONS = {
    "thank you.", "thanks for watching!", "thank you for watching.",
    "you", "bye.", ".", "so", "okay.", "oh.", "[music]", "thanks.",
    "subscribe!", "please subscribe.", "thank you very much.",
}


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, check=False)


def has_audio(video: Path) -> bool:
    r = run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
             "stream=index", "-of", "csv=p=0", str(video)])
    return bool(r.stdout.strip())


def decode(video: Path) -> tuple[np.ndarray, float | None]:
    """Mono 16 kHz float32, plus integrated LUFS of the *original* stream.

    One decode, two outputs: the resample chain for analysis and an ebur128 pass
    on the untouched stream, so loudness reflects what will actually be cut in.
    """
    cmd = [
        "ffmpeg", "-v", "info", "-nostdin", "-y", "-i", str(video),
        "-map", "0:a:0", "-ac", "1", "-ar", str(SR), "-f", "s16le", "pipe:1",
        "-map", "0:a:0", "-filter:a", "ebur128=peak=true", "-f", "null", "-",
    ]
    r = run(cmd)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"ffmpeg decode failed: {r.stderr.decode(errors='replace')[-300:]}")
    x = np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0
    log = r.stderr.decode(errors="replace")
    m = re.search(r"Integrated loudness:\s*\n\s*I:\s*(-?[\d.]+)\s*LUFS", log)
    return x, (float(m.group(1)) if m else None)


def band_slice(freqs: np.ndarray, lo: float, hi: float) -> slice:
    i0 = int(np.searchsorted(freqs, lo))
    i1 = int(np.searchsorted(freqs, hi))
    return slice(i0, max(i1, i0 + 1))


def stft_power(x: np.ndarray, block: int = 4096) -> np.ndarray:
    """Normalised power spectra, one row per analysis frame.

    Scaled so a full-scale sinusoid reads 0 dBFS after the /2 in `to_db`.
    Blocked to keep peak memory bounded on long clips.
    """
    win = np.hanning(N_FFT).astype(np.float32)
    norm = float((2.0 / win.sum()) ** 2)
    n_frames = 1 + max(0, (len(x) - N_FFT) // HOP)
    out = np.empty((n_frames, N_FFT // 2 + 1), dtype=np.float32)
    base = np.arange(N_FFT)[None, :]
    for s in range(0, n_frames, block):
        e = min(s + block, n_frames)
        idx = base + HOP * np.arange(s, e)[:, None]
        spec = np.fft.rfft(x[idx] * win, axis=1)
        out[s:e] = (spec.real ** 2 + spec.imag ** 2) * norm
    return out


def to_db(power: np.ndarray) -> np.ndarray:
    return 10.0 * np.log10(np.maximum(power / 2.0, 1e-12))


def modulation_index(env: np.ndarray) -> np.ndarray:
    """Share of envelope energy in the syllabic band, per output frame.

    Speech modulates its own loudness at roughly 4 Hz. Wind, being sustained,
    puts its envelope energy near DC. Computed on 1 s windows of the 100 Hz
    envelope, hopped by one output frame.
    """
    n_out = len(env) // DECIM
    w = 100                                     # 1.0 s at the analysis rate
    if n_out == 0 or len(env) < w:
        return np.zeros(max(n_out, 0), dtype=np.float32)
    win = np.hanning(w).astype(np.float32)
    freqs = np.fft.rfftfreq(w, d=1.0 / (SR / HOP))
    syl = band_slice(freqs, 2.0, 8.0)
    tot = band_slice(freqs, 0.5, 20.0)
    starts = np.clip(np.arange(n_out) * DECIM - w // 2, 0, len(env) - w)
    idx = np.arange(w)[None, :] + starts[:, None]
    seg = env[idx]
    seg = seg - seg.mean(axis=1, keepdims=True)
    p = np.abs(np.fft.rfft(seg * win, axis=1)) ** 2
    return (p[:, syl].sum(1) / (p[:, tot].sum(1) + 1e-20)).astype(np.float32)


def sig(x: np.ndarray, scale: float) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.asarray(x, dtype=np.float64) / scale))


@dataclass
class Tracks:
    speech_band_db: np.ndarray
    low_band_db: np.ndarray
    high_band_db: np.ndarray
    flatness: np.ndarray
    onset: np.ndarray
    modulation: np.ndarray
    speech_score: np.ndarray
    wind_dominant: np.ndarray

    def as_json(self) -> dict:
        r = {"speech_band_db": 1, "low_band_db": 1, "high_band_db": 1,
             "flatness": 3, "onset": 3, "modulation": 3, "speech_score": 3}
        out = {k: np.round(getattr(self, k), n).tolist() for k, n in r.items()}
        out["wind_dominant"] = self.wind_dominant.astype(int).tolist()
        return out


def tier_a(x: np.ndarray) -> Tracks:
    p = stft_power(x)
    freqs = np.fft.rfftfreq(N_FFT, d=1.0 / SR)
    s_low, s_sp = band_slice(freqs, *BAND_LOW), band_slice(freqs, *BAND_SPEECH)
    s_hi, s_flat = band_slice(freqs, *BAND_HIGH), band_slice(freqs, *BAND_FLAT)
    s_ons = band_slice(freqs, *BAND_ONSET)

    e_low, e_sp, e_hi = p[:, s_low].sum(1), p[:, s_sp].sum(1), p[:, s_hi].sum(1)

    pf = p[:, s_flat]
    flat_fine = np.exp(np.log(pf + 1e-20).mean(1)) / (pf.mean(1) + 1e-20)

    mag = np.sqrt(p[:, s_ons])
    flux = np.zeros(len(p), dtype=np.float32)
    flux[1:] = np.maximum(mag[1:] - mag[:-1], 0).sum(1) / (mag[1:].sum(1) + 1e-9)

    n = len(p) // DECIM
    if n == 0:
        raise RuntimeError("clip too short for analysis")
    cut = n * DECIM

    def mean_db(e: np.ndarray) -> np.ndarray:
        return to_db(e[:cut].reshape(n, DECIM).mean(1))

    sp_db, low_db, hi_db = mean_db(e_sp), mean_db(e_low), mean_db(e_hi)
    flatness = flat_fine[:cut].reshape(n, DECIM).mean(1)
    onset = flux[:cut].reshape(n, DECIM).max(1)
    mod = modulation_index(e_sp)[:n]

    # AND of three independent discriminators: loud enough in band, structured
    # rather than noise-like, and syllabic. Taken as a minimum rather than a
    # product so that wind cannot buy its way past a failed term by being very
    # loud, and so score > 0.5 is exactly the hard rule R8 calibrated.
    score = np.minimum(np.minimum(sig(sp_db - SPEECH_DB_MIN, 3.0),
                                  sig(FLATNESS_MAX - flatness, 0.03)),
                       sig(mod - MOD_MIN, 0.05))

    wind = (low_db - sp_db > WIND_DOM_DB) & (flatness > WIND_DOM_FLAT)
    return Tracks(sp_db.astype(np.float32), low_db.astype(np.float32),
                  hi_db.astype(np.float32), flatness.astype(np.float32),
                  onset.astype(np.float32), mod.astype(np.float32),
                  score.astype(np.float32), wind)


def runs_above(v: np.ndarray, thr: float, min_len: int) -> list[tuple[int, int]]:
    mask = np.concatenate(([False], v > thr, [False]))
    edges = np.flatnonzero(np.diff(mask.astype(np.int8)))
    return [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2]) if b - a >= min_len]


def onset_spikes(onset: np.ndarray) -> list[int]:
    """Frames where flux jumps well above its local level.

    Compared against a rolling median rather than a global one: an impact
    during a windy traverse is still an impact, and a global threshold would
    either drown in the loud clips or fire constantly in the quiet ones. The
    absolute floor is there because MAD collapses in steady audio, which turns
    an unremarkable ripple into a large z.

    Deliberately sparse. There are no impact labels to calibrate against, so
    the threshold is set where the rule produces a handful of hints per bin
    (~1/min) rather than at a measured optimum — a false hint costs the visual
    pass real attention, and speech candidates cover the same clips anyway.
    """
    w = int(ONSET_WINDOW_S * OUT_HZ)
    if len(onset) < 2 * w + 1:
        return []
    pad = np.pad(onset, w, mode="edge")
    idx = np.arange(len(onset))[:, None] + np.arange(2 * w + 1)[None, :]
    local = pad[idx]
    med = np.median(local, axis=1)
    mad = np.median(np.abs(local - med[:, None]), axis=1) + 1e-6
    z = (onset - med) / (1.4826 * mad)
    floor = float(np.percentile(onset, ONSET_PCT_FLOOR))
    peaks = np.flatnonzero((z > ONSET_Z_MIN) & (onset >= floor))
    # one per burst
    return [int(p) for i, p in enumerate(peaks) if i == 0 or p - peaks[i - 1] > OUT_HZ]


def marker_hits(text: str) -> tuple[int, int]:
    return (sum(1 for m in MARKER_RE_STRONG if m.search(text)),
            sum(1 for m in MARKER_RE_WEAK if m.search(text)))


def build_candidates(tr: Tracks, transcript: list[dict], has_asr: bool) -> list[dict]:
    """Timestamps worth pointing the visual pass at.

    Speech candidates come from the transcript, not from the DSP detector.
    R8 measured that detector at P=0.59/R=0.62 against ASR, so using it
    alongside a transcript would add roughly one false candidate for every
    true one while telling us strictly less — the words are the signal. It
    remains the fallback when ASR is off.
    """
    cands: list[dict] = []

    if has_asr:
        for seg in transcript:
            n_words = len(seg["words"])
            strong, weak = marker_hits(seg["text"])
            score = 0.45 if n_words >= 3 else 0.35
            score += 0.35 * min(strong, 2) + 0.10 * min(weak, 2)
            if n_words >= 8:
                score += 0.05
            if seg["no_speech_prob"] > 0.4:
                score -= 0.1
            kind = "interest marker" if strong else ("weak marker" if weak else "speech")
            why = f"{kind}: {seg['text'].strip()[:60]}"
            cands.append({"t": round(seg["start"], 2), "end": round(seg["end"], 2),
                          "why": why, "score": round(min(score, 1.0), 3)})
    else:
        for a, b in runs_above(tr.speech_score, SPEECH_SCORE_MIN,
                               int(MIN_SPEECH_RUN_S * OUT_HZ)):
            mean = float(tr.speech_score[a:b].mean())
            cands.append({"t": round(a / OUT_HZ, 2), "end": round(b / OUT_HZ, 2),
                          "why": "speech-like DSP (no ASR; P~0.59, see R8)",
                          "score": round(0.30 + 0.25 * mean, 3)})

    for f in onset_spikes(tr.onset):
        cands.append({"t": round(f / OUT_HZ, 2), "end": round((f + 10) / OUT_HZ, 2),
                      "why": "onset spike (impact/sudden event)", "score": 0.45})

    cands.sort(key=lambda c: c["t"])
    merged: list[dict] = []
    for c in cands:
        if merged and c["t"] - merged[-1]["t"] < 1.5:
            prev = merged[-1]
            prev["end"] = max(prev["end"], c["end"])
            score = max(prev["score"], c["score"])
            # Two *different* signals agreeing is evidence; two speech segments
            # in a row is just someone talking. Without this distinction the
            # bonus compounds and a long conversation outranks a reaction.
            if c["why"].split(":")[0] != prev["why"].split(":")[0]:
                score += 0.05
            prev["score"] = round(min(1.0, score), 3)
            if c["why"] not in prev["why"]:
                prev["why"] += " + " + c["why"]
        else:
            merged.append(dict(c))
    merged.sort(key=lambda c: -c["score"])
    return merged


def _preload_cuda_libs() -> None:
    """ctranslate2 dlopens cuBLAS/cuDNN by soname; pip puts them off the loader
    path. Loading them here makes the CUDA backend work without a system CUDA
    install. Failures are silent — the caller falls back to CPU."""
    import site
    for root in map(Path, site.getsitepackages()):
        for so in sorted((root / "nvidia").glob("*/lib/*.so*")):
            try:
                ctypes.CDLL(str(so), mode=ctypes.RTLD_GLOBAL)
            except OSError:
                pass


class Asr:
    def __init__(self, model_size: str, device: str):
        from faster_whisper import WhisperModel
        order = ([("cuda", "float16"), ("cpu", "int8")] if device == "auto"
                 else [(device, "float16" if device == "cuda" else "int8")])
        last: Exception | None = None
        for dev, ct in order:
            try:
                if dev == "cuda":
                    _preload_cuda_libs()
                self.model = WhisperModel(model_size, device=dev, compute_type=ct)
                self.device, self.compute_type = dev, ct
                print(f"asr: {model_size} on {dev}/{ct}")
                return
            except Exception as exc:                    # noqa: BLE001 - reported below
                last = exc
                print(f"asr: {dev} unavailable ({str(exc)[:120]})", file=sys.stderr)
        raise RuntimeError(f"could not load whisper: {last}")

    def transcribe(self, x: np.ndarray) -> list[dict]:
        segments, _ = self.model.transcribe(
            x, language="en", word_timestamps=True, vad_filter=True,
            beam_size=5, condition_on_previous_text=False,
        )
        out: list[dict] = []
        for s in segments:
            text = s.text.strip()
            if not text or text.lower() in HALLUCINATIONS or s.no_speech_prob > 0.6:
                continue
            out.append({
                "start": round(s.start, 2), "end": round(s.end, 2), "text": text,
                "no_speech_prob": round(float(s.no_speech_prob), 3),
                "words": [{"w": w.word.strip(), "t": round(w.start, 2),
                           "p": round(float(w.probability), 2)} for w in (s.words or [])],
            })
        return out


def vad_track(transcript: list[dict], n: int) -> np.ndarray:
    """Speech reference derived from ASR spans (10 Hz), for R8 calibration."""
    v = np.zeros(n, dtype=np.int8)
    for s in transcript:
        v[int(s["start"] * OUT_HZ):min(n, int(np.ceil(s["end"] * OUT_HZ)))] = 1
    return v


def analyse(video: Path, asr: Asr | None) -> dict:
    t0 = time.time()
    x, lufs = decode(video)
    tr = tier_a(x)
    n = len(tr.speech_score)

    transcript = asr.transcribe(x) if asr else []
    vad = vad_track(transcript, n)

    speech = tr.speech_score > SPEECH_SCORE_MIN
    silent = tr.speech_band_db < SILENCE_DB
    peak = float(20 * np.log10(max(float(np.abs(x).max()), 1e-9)))
    wind_frac = float(tr.wind_dominant.mean())

    return {
        "clip": video.name,
        "duration_s": round(len(x) / SR, 2),
        "sample_rate": SR,
        "frame_hz": OUT_HZ,
        "params": {"speech_db_min": SPEECH_DB_MIN, "flatness_max": FLATNESS_MAX,
                   "mod_min": MOD_MIN, "speech_score_min": SPEECH_SCORE_MIN,
                   "wind_dom_db": WIND_DOM_DB, "onset_z_min": ONSET_Z_MIN,
                   "onset_pct_floor": ONSET_PCT_FLOOR},
        "tracks": {**tr.as_json(), "vad": vad.tolist()},
        "transcript": transcript,
        "events": [],                       # Tier B tagging not built yet (laughter/whoops)
        "summary": {
            "speech_fraction": round(float(speech.mean()), 4),
            "asr_speech_fraction": round(float(vad.mean()), 4),
            "wind_dominant_fraction": round(wind_frac, 4),
            "silence_fraction": round(float(silent.mean()), 4),
            "audio_usable": bool(wind_frac < 0.8 and peak > -40.0),
            "peak_dbfs": round(peak, 2),
            "integrated_lufs": lufs,
            "n_words": sum(len(s["words"]) for s in transcript),
            "analysis_s": round(time.time() - t0, 1),
        },
        "candidates": build_candidates(tr, transcript, asr is not None),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Tier A DSP + ASR audio analysis.")
    ap.add_argument("input", type=Path, help="video file or directory of videos")
    ap.add_argument("-o", "--out", type=Path, required=True, help="output directory")
    ap.add_argument("--no-asr", action="store_true", help="Tier A DSP only")
    ap.add_argument("--model", default="large-v3", help="faster-whisper model")
    ap.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    ap.add_argument("--skip", default="", help="comma-separated stems to exclude (junk clips)")
    ap.add_argument("--force", action="store_true", help="re-analyse clips with a sidecar")
    args = ap.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            print(f"error: {tool} not found on PATH", file=sys.stderr)
            return 2

    if args.input.is_dir():
        videos = sorted(p for p in args.input.iterdir()
                        if p.suffix.lower() in VIDEO_SUFFIXES)
    else:
        videos = [args.input]
    skip = {s.strip().upper() for s in args.skip.split(",") if s.strip()}
    videos = [v for v in videos if v.stem.upper() not in skip]
    if not videos:
        print(f"error: no videos to analyse in {args.input}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    asr = None if args.no_asr else Asr(args.model, args.device)

    clips = []
    for video in videos:
        dest = args.out / f"{video.stem}.audio.json"
        if dest.exists() and not args.force:
            clips.append(json.loads(dest.read_text(encoding="utf-8")))
            print(f"{video.name}: cached")
            continue
        if not has_audio(video):
            print(f"skip {video.name}: no audio stream", file=sys.stderr)
            continue
        try:
            result = analyse(video, asr)
        except RuntimeError as exc:
            print(f"skip {video.name}: {exc}", file=sys.stderr)
            continue
        dest.write_text(json.dumps(result, indent=1), encoding="utf-8")
        clips.append(result)
        s = result["summary"]
        print(f"{video.name}: {result['duration_s']:6.1f}s  "
              f"speech {s['speech_fraction']:.2f}  wind {s['wind_dominant_fraction']:.2f}  "
              f"words {s['n_words']:4d}  cands {len(result['candidates']):3d}  "
              f"({s['analysis_s']}s)")

    top = sorted(
        ({"clip": c["clip"], **cand} for c in clips for cand in c["candidates"]),
        key=lambda c: -c["score"])[:60]
    index = {
        "n_clips": len(clips),
        "total_s": round(sum(c["duration_s"] for c in clips), 1),
        "asr": None if asr is None else {"model": args.model, "device": asr.device},
        "clips": [{"clip": c["clip"], "duration_s": c["duration_s"], **c["summary"]}
                  for c in clips],
        "top_candidates": top,
    }
    (args.out / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    print(f"wrote {args.out / 'index.json'} ({len(clips)} clips, {index['total_s']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
