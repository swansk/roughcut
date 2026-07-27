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

# The sidechain has to *duck*, not attenuate. Measured on the real Copper cut: its loud
# passages sit near -13 dBFS RMS and its quiet ones near -25, so a threshold below that
# quiet floor triggers permanently and simply turns the bed down for the whole film —
# which is what the first version did, and why the bed was inaudible rather than subtle.
DUCK = "sidechaincompress=threshold=0.06:ratio=4:attack=20:release=350:makeup=2"
MIX = "amix=inputs=2:normalize=0:duration=first"


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


def music_filter(spec: dict, film_s: float) -> str:
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
    # asplit because the film audio is both a mix input and the sidechain key.
    return (f"{bed};[0:a]asplit=2[dry][key];"
            f"[bed][key]{DUCK}[ducked];[dry][ducked]{MIX}[aout]")


def bed_only_filter(spec: dict, film_s: float) -> str:
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
    return f"{bed};[0:a]anull[key];[bed][key]{DUCK}[aout]"


def resolve_asset(asset: str, assets_root: Path | None) -> Path:
    p = Path(asset).expanduser()
    if not p.is_absolute():
        p = (assets_root or Path(".")) / p
    if not p.exists():
        raise FileNotFoundError(f"asset not found: {p}")
    return p


def add_music(film: Path, spec: dict, out: Path,
              assets_root: Path | None = None, bed_only: bool = False) -> dict:
    """Mix a bed under an assembled film. The video stream is **copied**."""
    s = resolve_gain(music_spec(spec), resolve_asset(spec["asset"], assets_root))
    track = resolve_asset(s["asset"], assets_root)
    film_s = probe_duration(film)
    graph = (bed_only_filter(s, film_s) if bed_only else music_filter(s, film_s))
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
    return {**s, "asset_path": str(track), "film_s": round(film_s, 2)}
