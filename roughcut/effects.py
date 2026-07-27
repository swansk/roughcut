"""Effects that change the pixels and the mix, per docs/EFFECTS.md.

The rule that governs this module: **callers pass validated parameters, never filter
strings.** Every ffmpeg fragment is built here, from numbers whose ranges are checked.
A model proposes `{"kind": "music", "gain_db": -18, "duck": true}`; it never proposes
`sidechaincompress=threshold=...`, because that is executable text arriving by way of a
transcript, and because an invented filter either crashes the render or silently does
something nobody asked for.

Film-wide effects (music) run *after* the concat with the video stream copied, so they
cost no picture quality. Per-shot effects (sfx, overlay — not built yet) will fold into
the part encode that assemble.py already performs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

MAX_GAIN_DB = 12.0          # a bed louder than the film is a bug, not a choice
MIN_GAIN_DB = -60.0

# Where a bed should sit. Films here are mastered to -16 LUFS (assemble.TARGET_LUFS),
# so -24 puts the music about 8 dB under the picture before any ducking — present in
# the gaps, never competing with a line.
TARGET_BED_LUFS = -24.0

# Ducking on the film's own amplitude, kept only as a fallback for a film whose speech
# regions are unknown. Karl, on hearing it: *"the audio is fading in and out quite a bit
# in a disturbing way… some annoying spots where people are talking and the music is
# distracting."* Both complaints are the same fault — on this footage a ski scrape, a
# pole plant and a gust of wind are all louder than a voice, so an amplitude-keyed
# compressor breathes constantly and still fails to get out of the way of dialogue.
DUCK = "sidechaincompress=threshold=0.06:ratio=4:attack=20:release=350:makeup=2"
MIX = "amix=inputs=2:normalize=0:duration=first"

# Ducking on *speech*, which is what was actually meant. The regions come from the
# transcripts that have been on disk since R8 — the same "the answer is already
# measured" as the onset track. A key built only from speech means the bed moves when
# someone talks and at no other time, so it can move further and more slowly: a deep,
# lazy duck reads as mixing, where a shallow fast one reads as pumping.
# How far the bed drops under a line, in dB — the number a person actually means.
# 10-14 dB is the usual range for music under dialogue; past ~20 it stops being a duck
# and becomes a mute, which draws attention to itself every time it recovers.
SPEECH_DUCK_DB = 12.0
_DUCK_RATIO = 4.0           # gentle knee; depth comes from the key level, see below
_DUCK_THRESHOLD = 0.03


def speech_duck(duck_db: float = SPEECH_DUCK_DB) -> tuple[str, float]:
    """The compressor, and the key amplitude that produces `duck_db` of reduction.

    Ratio is *not* the depth control, which cost an hour to learn: with a key that is a
    full-scale square wave sitting 28 dB over the threshold, the compressor is pinned
    and ratios of 6 and 12 differ by 2 dB. Reduction is

        duck_db ≈ (key over threshold, in dB) × (1 − 1/ratio)

    so with the ratio fixed, the honest knob is the key's own level — solve for it and
    the requested depth is what comes out.

    Attack and release stay long on purpose: with a key that fires only on speech there
    is nothing to chase, and a lazy envelope reads as mixing where a twitchy one reads
    as pumping.
    """
    over_db = max(0.0, duck_db) / (1.0 - 1.0 / _DUCK_RATIO)
    key = min(1.0, _DUCK_THRESHOLD * (10 ** (over_db / 20.0)))
    return (f"sidechaincompress=threshold={_DUCK_THRESHOLD}:ratio={_DUCK_RATIO:g}:"
            f"attack=80:release=900:makeup=1"), key
SPEECH_PAD_S = 0.35        # open before the first word, close after the last
SPEECH_MERGE_S = 1.2       # closer than this and the bed never gets back up in time


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def probe_duration(p: Path) -> float:
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(p)])
    return float(r.stdout.strip().strip(",")) if r.stdout.strip() else 0.0


def integrated_lufs(path: Path) -> float | None:
    """Measured loudness of a track. One ffmpeg pass, and the difference between a bed
    you can hear and one you cannot."""
    r = run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path),
             "-af", "ebur128=framelog=quiet", "-f", "null", "-"])
    for line in r.stderr.splitlines():
        if "I:" in line and "LUFS" in line:
            try:
                return float(line.split("I:")[1].split("LUFS")[0])
            except ValueError:
                return None
    return None


def music_spec(raw: dict) -> dict:
    """Validate a music effect. Strict, for the reason `validate_plan` is strict.

    `gain_db` is deliberately *optional*: a raw gain is meaningless without knowing how
    loud the track is. The first version defaulted to -18 dB and got applied to a
    -6.8 LUFS master, which put the bed 25 dB under the film — inaudible. Left unset,
    the gain is computed from the track's measured loudness, the same way assemble.py
    matches clip levels from the LUFS in the audio sidecars instead of guessing.
    """
    if not raw.get("asset"):
        raise ValueError("music effect needs an 'asset'")
    gain = raw.get("gain_db")
    if gain is not None:
        gain = float(gain)
        if not (MIN_GAIN_DB <= gain <= MAX_GAIN_DB):
            raise ValueError(f"gain_db {gain} outside {MIN_GAIN_DB}..{MAX_GAIN_DB}")
    return {
        "asset": str(raw["asset"]),
        "gain_db": gain,                                # None → measure and compute
        "bed_lufs": float(raw.get("bed_lufs", TARGET_BED_LUFS)),
        "duck": bool(raw.get("duck", True)),
        "duck_db": float(raw.get("duck_db", SPEECH_DUCK_DB)),
        "fade_in": max(0.0, float(raw.get("fade_in", 1.5))),
        "fade_out": max(0.0, float(raw.get("fade_out", 4.0))),
    }


def resolve_gain(spec: dict, track: Path) -> dict:
    """Fill in `gain_db` from the track's measured loudness when it was not given."""
    s = dict(spec)
    if s.get("gain_db") is not None:
        return s
    measured = integrated_lufs(track)
    if measured is None:
        s["gain_db"] = -18.0                    # unmeasurable: fall back, and say so
        s["measured_lufs"] = None
        return s
    s["measured_lufs"] = round(measured, 1)
    s["gain_db"] = round(max(MIN_GAIN_DB, min(MAX_GAIN_DB,
                                              s["bed_lufs"] - measured)), 2)
    return s


def speech_regions(segments: list[dict], sidecars: Path,
                   pad: float = SPEECH_PAD_S,
                   merge: float = SPEECH_MERGE_S) -> list[tuple[float, float]]:
    """Where people talk, in **film** time, from the transcripts.

    Each segment contributes the utterances that fall inside its in/out, shifted to
    where that segment lands in the finished cut. Overlapping and near-adjacent regions
    merge, because a bed that dips between two sentences of the same answer is the
    pumping being complained about.
    """
    out: list[tuple[float, float]] = []
    cursor = 0.0
    for seg in segments:
        span = float(seg["out"]) - float(seg["in"])
        p = sidecars / f"{Path(seg['clip']).stem}.audio.json"
        if p.exists():
            import json                       # local: keeps the module import-light
            for u in json.loads(p.read_text(encoding="utf-8")).get("transcript", []):
                start, end = float(u["start"]), float(u["end"])
                lo = max(start, float(seg["in"]))
                hi = min(end, float(seg["out"]))
                if hi > lo:
                    out.append((cursor + lo - float(seg["in"]) - pad,
                                cursor + hi - float(seg["in"]) + pad))
        cursor += span

    out.sort()
    merged: list[tuple[float, float]] = []
    for lo, hi in out:
        lo = max(0.0, lo)
        if merged and lo - merged[-1][1] <= merge:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def speech_key(regions: list[tuple[float, float]], film_s: float,
               level: float = 0.8) -> str:
    """A synthetic sidechain key: full scale while anyone is speaking, silent otherwise.

    Built with `aevalsrc` rather than by writing a WAV, so the whole thing stays one
    ffmpeg invocation and no temporary audio has to be managed.
    """
    terms = "+".join(f"between(t,{lo:.2f},{min(hi, film_s):.2f})"
                     for lo, hi in regions if lo < film_s)
    expr = terms or "0"
    return f"aevalsrc='{level:.4f}*min(1,{expr})':d={film_s:.3f}:s=48000[key]"


def _bed_chain(s: dict, film_s: float) -> str:
    """`[1:a]` (the music) → `[bed]`, looped and hard-trimmed to the film.

    Looping so a short track does not end the bed early; trimming so a long one does
    not run past the picture.
    """
    return (f"[1:a]aloop=loop=-1:size=2e9,atrim=0:{film_s:.3f},"
            f"asetpts=N/SR/TB,volume={s['gain_db']:.2f}dB,"
            f"afade=t=in:st=0:d={s['fade_in']:.2f},"
            f"afade=t=out:st={max(0.0, film_s - s['fade_out']):.3f}:"
            f"d={s['fade_out']:.2f}[bed]")


def _resolved(spec: dict, film_s: float) -> dict:
    """Graphs need a concrete gain; callers may hand in a spec that has none."""
    s = music_spec(spec)
    if s["gain_db"] is None:
        s["gain_db"] = s["bed_lufs"] - (-16.0)   # assume a film at TARGET_LUFS
    return s


def music_filter(spec: dict, film_s: float,
                 speech: list[tuple[float, float]] | None = None) -> str:
    """Filter graph for a bed under the finished film.

    Ducked by default, and that default carries weight: R8 established that in this
    footage the *words* are the signal, so a bed at a flat level buries the thing the
    cut was built around. `sidechaincompress` keyed on the film's own audio pulls the
    music down under speech and lets it back up in the gaps.
    """
    s = _resolved(spec, film_s)
    bed = _bed_chain(s, film_s)
    if not s["duck"]:
        return f"{bed};[0:a][bed]{MIX}[aout]"
    if speech:
        # Keyed on speech alone: the bed moves when someone talks and at no other time.
        duck, key_level = speech_duck(s["duck_db"])
        return (f"{bed};{speech_key(speech, film_s, key_level)};"
                f"[bed][key]{duck}[ducked];[0:a][ducked]{MIX}[aout]")
    # asplit because the film audio is both a mix input and the sidechain key.
    return (f"{bed};[0:a]asplit=2[dry][key];"
            f"[bed][key]{DUCK}[ducked];[dry][ducked]{MIX}[aout]")


def bed_only_filter(spec: dict, film_s: float,
                    speech: list[tuple[float, float]] | None = None) -> str:
    """The bed as it will be heard, with the film *not* mixed in under it.

    For measurement: comparing the bed's own level during the film's loud passages
    against the quiet ones is the only honest check on ducking, because by ear "the
    sidechain is working" and "the track is quiet just here" are indistinguishable.

    Composed from the same pieces as `music_filter` rather than carved out of its
    string. The first version did the latter and shipped a graph missing a separator —
    which is the failure this module exists to prevent, committed by the module itself.
    """
    s = _resolved(spec, film_s)
    bed = _bed_chain(s, film_s)
    if not s["duck"]:
        return f"{bed};[bed]anull[aout]"
    if speech:
        duck, key_level = speech_duck(s["duck_db"])
        return (f"{bed};{speech_key(speech, film_s, key_level)};"
                f"[bed][key]{duck}[aout]")
    return f"{bed};[0:a]anull[key];[bed][key]{DUCK}[aout]"


def resolve_asset(asset: str, assets_root: Path | None) -> Path:
    p = Path(asset).expanduser()
    if not p.is_absolute():
        p = (assets_root or Path(".")) / p
    if not p.exists():
        raise FileNotFoundError(f"asset not found: {p}")
    return p


def add_music(film: Path, spec: dict, out: Path,
              assets_root: Path | None = None, bed_only: bool = False,
              speech: list[tuple[float, float]] | None = None) -> dict:
    """Mix a bed under an assembled film. The video stream is **copied**."""
    s = resolve_gain(music_spec(spec), resolve_asset(spec["asset"], assets_root))
    track = resolve_asset(s["asset"], assets_root)
    film_s = probe_duration(film)
    graph = (bed_only_filter(s, film_s, speech) if bed_only
             else music_filter(s, film_s, speech))
    cmd = ["ffmpeg", "-v", "error", "-y", "-nostdin", "-i", str(film), "-i", str(track),
           "-filter_complex", graph, "-map", "[aout]"]
    if not bed_only:
        cmd += ["-map", "0:v:0", "-c:v", "copy"]
    else:
        cmd += ["-vn"]
    cmd += ["-c:a", "aac", "-b:a", "192k", "-ac", "2", "-write_tmcd", "0",
            "-movflags", "+faststart", str(out)]
    r = run(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"music pass failed: {r.stderr[-400:]}")
    return {**s, "asset_path": str(track), "film_s": round(film_s, 2),
            "keyed_on": "speech" if speech else "amplitude",
            "speech_regions": len(speech or [])}
