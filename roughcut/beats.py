"""Where a music track's beats are, and a cut moved onto them (2026-11-20).

A film cut to music reads as edited when its cuts land on the music's beats; one cut by
people talking reads as cut off when a cut lands inside a line. This module does the first
without ever doing the second.

- `analyse(path)` finds a track's tempo and beat times (numpy only: a log-mel
  spectral-flux onset envelope, the tempo from its autocorrelation, the beats by dynamic
  programming over it — Ellis 2007, "Beat Tracking by Dynamic Programming"), and which
  beat of four the bar starts on (the phase with the most low-band energy).
  `cached(path)` keeps the answer beside the track (`<folder>/.beats/<file>.json`).
- `grid(beats, film_s)` is those beats on the film's clock: the bed runs from the
  sequence's 0 and loops (app.js `cueBed`, effects.py `_bed_chain`).
- `snap(segments, grid, ...)` moves each cut of a film onto a beat when it can: first by a
  ROLL (the edit moves, the film keeps its length), else by a RIPPLE of the outgoing
  shot's out-point. A move is made only when it keeps every rule the cut already keeps:
  inside the clip's media, never shorter than `MIN_SHOT_S`, never a shot the editor cut by
  hand (`hand`), and never a new clipped line (`problems(cut)` — roughcut.sentences'
  measure of the whole cut — must not gain a problem). Karl, 2026-11-11: "never cut off audio"; a beat is not worth
  a sentence.

Units: beat and grid times are seconds (track / film); a shot's `in` / `out` are CLIP
seconds, its film length `(out - in) / speed`.
"""
from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

SR = 22050
HOP = 256
N_FFT = 1024
FPS = SR / HOP                      # 86.13 onset frames a second: 11.6 ms a frame
BPM_MIN, BPM_MAX = 55.0, 215.0
BPM_PRIOR = 120.0
PRIOR_OCTAVES = 0.9                 # the tempo prior's width with no hint
HINT_OCTAVES = 0.18                 # and with one (the catalogue's bpm)
TIGHTNESS = 100.0                   # Ellis's alpha: how much a beat off the period costs
VERSION = 2                         # bump to drop every cached analysis

MAX_SHIFT_S = 0.45                  # the furthest a cut moves to reach a beat
MIN_SHOT_S = 0.7                    # a shot a snap leaves is never shorter than this
ON_BEAT_S = 0.05                    # a cut within this of a beat is on it (1.5 frames at 30)
DOWN_BONUS_S = 0.14                 # a bar's first beat is preferred as if this much nearer
HALF_BONUS_S = 0.07                 # and its third beat (the half bar) as if this much


class BeatError(RuntimeError):
    """The track could not be read or has no beat to find."""


# ---------------------------------------------------------------- the track's beats

def decode(path: Path, sr: int = SR) -> np.ndarray:
    """The track as mono float32 at `sr` (ffmpeg)."""
    try:
        out = subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr),
             "-f", "f32le", "-"], capture_output=True, timeout=180, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BeatError(f"could not read {Path(path).name}: {exc}") from exc
    if out.returncode != 0 or len(out.stdout) < sr * 4:
        raise BeatError(f"could not read {Path(path).name}: {out.stderr.decode('utf-8', 'replace')[-200:]}")
    return np.frombuffer(out.stdout, dtype=np.float32)


def _mel_bank(n_bands: int = 40, sr: int = SR, n_fft: int = N_FFT) -> np.ndarray:
    def mel(f):
        return 2595.0 * np.log10(1.0 + f / 700.0)

    def inv(m):
        return 700.0 * (10.0 ** (m / 2595.0) - 1.0)
    edges = inv(np.linspace(mel(30.0), mel(sr / 2.0), n_bands + 2))
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    bank = np.zeros((n_bands, len(freqs)), dtype=np.float32)
    for i in range(n_bands):
        lo, mid, hi = edges[i], edges[i + 1], edges[i + 2]
        up = (freqs - lo) / max(mid - lo, 1e-9)
        down = (hi - freqs) / max(hi - mid, 1e-9)
        bank[i] = np.clip(np.minimum(up, down), 0.0, None)
    return bank


def _spectrogram(y: np.ndarray) -> np.ndarray:
    n = 1 + max(0, (len(y) - N_FFT) // HOP)
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    win = np.hanning(N_FFT).astype(np.float32)
    out = np.empty((n, N_FFT // 2 + 1), dtype=np.float32)
    for a in range(0, n, 4096):                       # in blocks: a 7 minute track is 36k frames
        out[a:a + 4096] = np.abs(np.fft.rfft(y[idx[a:a + 4096]] * win, axis=1))
    return out


def onset_envelopes(y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(all bands, low bands): how much louder each band got since the frame before,
    summed, with the slow drift taken out. One value per `HOP` samples."""
    mel = np.log1p(100.0 * (_spectrogram(y) @ _mel_bank().T))
    flux = np.maximum(0.0, mel[1:] - mel[:-1])
    flux = np.vstack([np.zeros((1, flux.shape[1]), dtype=flux.dtype), flux])

    def tidy(e: np.ndarray) -> np.ndarray:
        k = int(FPS * 1.0) | 1
        e = e - np.convolve(e, np.ones(k) / k, mode="same")
        e = np.maximum(e, 0.0)
        sd = float(e.std())
        return (e / sd if sd > 1e-9 else e).astype(np.float64)
    return tidy(flux.mean(axis=1)), tidy(flux[:, :5].mean(axis=1))      # low: under ~200 Hz


def tempo(env: np.ndarray, hint_bpm: float | None = None) -> tuple[float, float]:
    """(bpm, confidence 0..1) from the envelope's autocorrelation, weighted toward
    `hint_bpm` (else 120) so a half or double is not picked for being just as periodic."""
    n = len(env)
    if n < FPS * 8:
        raise BeatError("the track is too short to find a beat in")
    size = 1 << int(math.ceil(math.log2(2 * n)))
    spec = np.fft.rfft(env, size)
    ac = np.fft.irfft(spec * np.conj(spec))[:n]
    ac = ac / max(ac[0], 1e-9)
    lo, hi = int(FPS * 60.0 / BPM_MAX), int(FPS * 60.0 / BPM_MIN)
    lags = np.arange(lo, hi + 1)
    bpm = 60.0 * FPS / lags
    centre, width = (hint_bpm, HINT_OCTAVES) if hint_bpm else (BPM_PRIOR, PRIOR_OCTAVES)
    prior = np.exp(-0.5 * (np.log2(bpm / centre) / width) ** 2)
    # a real beat period also shows at twice and three times its lag
    score = ac[lags].copy()
    for m in (2, 3):
        ok = lags * m < n
        score[ok] += 0.5 * ac[lags[ok] * m]
    score = score * prior
    k = int(np.argmax(score))
    best = float(lags[k])
    if 0 < k < len(lags) - 1:                           # parabolic: lags are whole frames
        a, b, c = score[k - 1], score[k], score[k + 1]
        den = a - 2 * b + c
        if abs(den) > 1e-12:
            best += float(0.5 * (a - c) / den)
    conf = float(np.clip(ac[lags[k]], 0.0, 1.0))
    return 60.0 * FPS / best, conf


def track(env: np.ndarray, bpm: float) -> np.ndarray:
    """Beat frames: the sequence that is both on the envelope's peaks and one period
    apart (Ellis 2007)."""
    period = 60.0 * FPS / bpm
    n = len(env)
    lo, hi = int(round(period / 2)), int(round(period * 2))
    back = np.arange(-hi, -lo + 1)
    cost = -TIGHTNESS * (np.log(-back / period) ** 2)
    score = env.copy()
    prev = np.full(n, -1, dtype=np.int64)
    for t in range(hi, n):
        cand = score[t + back] + cost
        k = int(np.argmax(cand))
        if cand[k] > 0:
            score[t] = env[t] + cand[k]
            prev[t] = t + back[k]
    # the last beat: the best score in the final period
    tail = max(0, n - int(period))
    t = tail + int(np.argmax(score[tail:]))
    beats = [t]
    while prev[t] >= 0:
        t = int(prev[t])
        beats.append(t)
    return np.array(beats[::-1], dtype=np.int64)


def _trim_silence(beats: np.ndarray, env: np.ndarray) -> np.ndarray:
    """Beats the tracker carried through silence at either end are not beats."""
    if len(beats) < 8:
        return beats
    w = int(FPS * 0.07)
    strength = np.array([env[max(0, b - w):b + w + 1].max() for b in beats])
    floor = 0.1 * float(np.median(strength))
    keep = np.flatnonzero(strength > floor)
    if len(keep) < 4:
        return beats
    return beats[keep[0]:keep[-1] + 1]


def analyse(path: Path, hint_bpm: float | None = None) -> dict:
    """{"bpm", "confidence", "beats": [s, …], "down": i, "bar": 4, "duration_s"}: the
    track's beat times in seconds; `beats[down::bar]` are the bar's first beats (a guess:
    the phase of four with the most bass)."""
    y = decode(Path(path))
    env, low = onset_envelopes(y)
    bpm, conf = tempo(env, hint_bpm)
    frames = _trim_silence(track(env, bpm), env)
    if len(frames) < 8:
        raise BeatError(f"no steady beat found in {Path(path).name}")
    times = frames / FPS + (N_FFT / 2) / SR             # a frame's centre
    med = float(np.median(np.diff(times)))
    w = int(FPS * 0.05)
    lows = np.array([low[max(0, f - w):f + w + 1].max() for f in frames])
    phase = [float(lows[p::4].mean()) for p in range(4)]
    down = int(np.argmax(phase))
    return {"version": VERSION, "bpm": round(60.0 / med, 2), "confidence": round(conf, 3),
            "beats": [round(float(t), 3) for t in times], "down": down, "bar": 4,
            "down_confidence": round(max(phase) / (sum(phase) / 4.0 + 1e-9), 2),
            "duration_s": round(len(y) / SR, 3)}


def cached(path: Path, hint_bpm: float | None = None) -> dict:
    """`analyse(path)`, kept in `<folder>/.beats/<file>.json` and reused while the file
    is the same size and age."""
    path = Path(path)
    st = path.stat()
    key = f"{st.st_size}|{int(st.st_mtime)}|{VERSION}"
    side = path.parent / ".beats" / (path.name + ".json")
    try:
        got = json.loads(side.read_text(encoding="utf-8"))
        if got.get("key") == key:
            return got
    except (OSError, ValueError):
        pass
    got = {**analyse(path, hint_bpm), "key": key}
    try:
        side.parent.mkdir(exist_ok=True)
        tmp = side.with_name("." + side.name + ".part")
        tmp.write_text(json.dumps(got), encoding="utf-8")
        tmp.replace(side)
    except OSError:
        pass
    return got


# ---------------------------------------------------------------- the film's beats

def grid(found: dict, film_s: float) -> list[tuple[float, int]]:
    """[(film seconds, weight), …] over a film `film_s` long plus a little: the bed starts
    at the sequence's 0 and loops every `duration_s`. Weight 2 is a bar's first beat, 1 its
    half bar, 0 the beats between."""
    beats, d = found.get("beats") or [], float(found.get("duration_s") or 0.0)
    if not beats or d <= 0:
        return []
    down, bar = int(found.get("down") or 0), int(found.get("bar") or 4)
    out, k = [], 0
    while k * d < film_s + MAX_SHIFT_S + 1.0:
        for i, b in enumerate(beats):
            t = b + k * d
            if t > film_s + MAX_SHIFT_S + 1.0:
                break
            ph = (i - down) % bar
            out.append((round(t, 4), 2 if ph == 0 else 1 if bar % 2 == 0 and ph == bar // 2 else 0))
        k += 1
    return out


def _speed(seg: dict) -> float:
    try:
        v = float(seg.get("speed") or 1.0)
    except (TypeError, ValueError):
        return 1.0
    return v if v > 0 else 1.0


def _dur(seg: dict) -> float:
    return (float(seg["out"]) - float(seg["in"])) / _speed(seg)


def cut_times(segments: list[dict]) -> list[float]:
    """The film time of each cut: the end of every shot but the last."""
    t, out = 0.0, []
    for s in segments[:-1]:
        t += _dur(s)
        out.append(t)
    return out


def _nearest(t: float, times: np.ndarray) -> float:
    if not len(times):
        return math.inf
    k = int(np.searchsorted(times, t))
    return float(min(abs(times[j] - t) for j in (k - 1, k) if 0 <= j < len(times)))


def measure(segments: list[dict], beat_grid: list[tuple[float, int]]) -> dict:
    """How much of the cut is on the beat: {"cuts", "on", "share", "median_off_s"}."""
    times = np.array([b for b, _ in beat_grid])
    offs = [_nearest(c, times) for c in cut_times(segments)]
    on = sum(1 for o in offs if o <= ON_BEAT_S)
    return {"cuts": len(offs), "on": on, "share": round(on / len(offs), 3) if offs else 0.0,
            "median_off_s": round(float(np.median(offs)), 3) if offs else 0.0}


def snap(segments: list[dict], beat_grid: list[tuple[float, int]], *,
         durations: dict[str, float] | None = None,
         hand: Iterable[str] = (),
         problems: Callable[[list[dict]], set] | None = None,
         fps: float | None = None,
         max_shift_s: float = MAX_SHIFT_S,
         length_slack_s: float = 1.5) -> tuple[list[dict], list[dict]]:
    """The cut with each edit moved onto a beat where the rules allow; and what moved:
    [{"cut": i, "a": id, "b": id, "how": "roll" | "ripple", "by": film s, "at": film s,
    "down": bool}, …]. `segments` is not modified.

    `durations`: clip seconds by clip name (a move never reaches past the media).
    `hand`: ids the editor cut by hand; neither edge of one moves.
    `problems(segments)`: the set of line problems a whole cut has; a move that adds one
    is not made. `fps`: the sequence's rate; a moved cut lands on its frame grid.
    `length_slack_s`: how far ripples may take the film's length from where it started."""
    segs = [dict(s) for s in segments]
    hand = set(hand or ())
    if len(segs) < 2 or not beat_grid:
        return segs, []
    if problems is not None:
        try:
            problems(segs)
        except Exception:  # noqa: BLE001: no way to tell a clipped line: nothing moves
            return segs, []
    times = np.array([b for b, _ in beat_grid])
    downs = np.array([int(d) for _, d in beat_grid])
    frame = 1.0 / fps if fps else 0.0
    on_s = max(ON_BEAT_S, frame * 0.75)
    moved: list[dict] = []
    drift = 0.0

    def quant(t: float) -> float:
        return round(round(t * fps) / fps, 6) if fps else t

    def bad(seg: dict, was: dict) -> bool:
        if _dur(seg) < min(MIN_SHOT_S, _dur(was)) - 1e-6:
            return True
        end = (durations or {}).get(seg.get("clip"))
        return float(seg["in"]) < -1e-6 or (end is not None and float(seg["out"]) > float(end) + 1e-6)

    def known() -> set:
        return set(problems(segs)) if problems is not None else set()

    def clips_a_line(i: int, a2: dict, b2: dict | None, base: set) -> bool:
        if problems is None:
            return False
        trial = list(segs)
        trial[i] = a2
        if b2 is not None:
            trial[i + 1] = b2
        try:
            return bool(set(problems(trial)) - base)
        except Exception:  # noqa: BLE001: nothing to judge by: leave the cut alone
            return True

    start = 0.0
    for i in range(len(segs) - 1):
        a, b = segs[i], segs[i + 1]
        cut = start + _dur(a)
        off = _nearest(cut, times)
        if off <= on_s or a.get("id") in hand or not all(k in a for k in ("in", "out")):
            start = cut
            continue
        lo, hi = np.searchsorted(times, cut - max_shift_s), np.searchsorted(times, cut + max_shift_s)
        cands = sorted(range(lo, hi),
                       key=lambda j: abs(times[j] - cut) - (0.0, HALF_BONUS_S, DOWN_BONUS_S)[min(2, int(downs[j]))])
        done = False
        base = known() if cands else set()
        for j in cands:
            by = quant(float(times[j])) - cut
            if abs(by) < 1e-6:
                continue
            a2 = {**a, "out": round(float(a["out"]) + by * _speed(a), 4)}
            if bad(a2, a):
                continue
            # a roll first: the film keeps its length and every later cut stays put
            if b.get("id") not in hand and "in" in b:
                b2 = {**b, "in": round(float(b["in"]) + by * _speed(b), 4)}
                if not bad(b2, b) and not clips_a_line(i, a2, b2, base):
                    segs[i], segs[i + 1] = a2, b2
                    moved.append({"cut": i, "a": a.get("id"), "b": b.get("id"), "how": "roll",
                                  "by": round(by, 4), "at": round(cut + by, 4), "down": int(downs[j]) >= 2})
                    done = True
                    break
            if abs(drift + by) <= length_slack_s and not clips_a_line(i, a2, None, base):
                segs[i] = a2
                drift += by
                moved.append({"cut": i, "a": a.get("id"), "b": b.get("id"), "how": "ripple",
                              "by": round(by, 4), "at": round(cut + by, 4), "down": int(downs[j]) >= 2})
                done = True
                break
        start = start + _dur(segs[i]) if done else cut
    return segs, moved


def words(before: dict, after: dict, bpm: float | None = None) -> str:
    """One line for the proposal's notes: what cutting to the beat did."""
    if not after.get("cuts"):
        return ""
    tail = f" ({bpm:.0f} bpm)" if bpm else ""
    if after["on"] <= before["on"]:
        return (f"Cut to the music{tail}: {after['on']} of {after['cuts']} cuts are on a beat; the rest "
                f"would have clipped a line or left the footage.")
    return (f"Cut to the music{tail}: {after['on']} of {after['cuts']} cuts now land on a beat "
            f"(was {before['on']}); none was moved into a line.")
