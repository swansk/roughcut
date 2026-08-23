"""What happens in the footage, in priority order.

Karl, on the revision the visual pass produced: *"You missed some cool jumps — this is
likely due to limited keyframe analysis and the lack of a workflow / algorithm that
applies sort / priority following a granular keyframe analysis on the first pass."*

Both halves of that are right, and they are different faults.

**Limited keyframe analysis.** The visual pass samples every 4s. A jump lasts 1–2s, so
whether it is seen at all is a coin toss on phase — the sheet either catches the
airborne frame or it catches the approach and the landing and calls the stretch
"skiing". Nothing about the sheet interval can be tuned to fix that: at 1s the pass
would cost four times as much over 44 minutes of footage, for frames that are almost
all snow.

**No sort.** Even where the coarse pass *did* see an event, everything it saw arrived
at the Ask as one undifferentiated list per clip — a backflip and a wide shot of a
valley are two lines that look the same. A model reading forty such lines has no way
to know which three matter.

So this module is the narrowing docs/EFFECTS.md rule 3 describes, applied to finding
events rather than to timing a known one:

1. **A local motion track** — `scdet`'s mean absolute frame difference over the 720p
   proxy at 10 Hz. Free, ~6s per 5-minute clip, no model call. Combined with the R8
   onset track the audio sidecars have carried since July, it says *something happened
   here* with 100 ms resolution.
2. **Candidate windows** — the peaks of that combined track, which is where a fine
   visual read is worth paying for.
3. **A fine read** — 1s sampling across ±4s of a candidate, one sheet per window
   (`visual_pass.py --windows`), which resolves what the coarse pass could only imply.
4. **A rank** — kind × notable × corroboration × confirmation, so the top of the list
   is what the Ask and the board should look at first.

The tracks are deliberately compared as **robust z-scores**, not raw values: a POV ski
clip is high-motion everywhere and a lift-cabin clip is not, so an absolute threshold
would find every event in one clip and none in the other. Median/MAD asks the only
question that generalises — *is this unusual for this clip*.
"""

from __future__ import annotations

import re
import statistics
import subprocess
from pathlib import Path

# 10 Hz to match the audio sidecars' track rate exactly (R8), so the two can be
# compared sample for sample without resampling either.
MOTION_HZ = 10.0
# Frame difference is a whole-frame statistic; 160px carries it fine and is what makes
# the scan cost seconds instead of minutes. Detail is the fine read's job, not this.
MOTION_WIDTH = 160

# ±0.5s of smoothing before peak-picking. An impact is one or two frames of the track;
# without smoothing the peak-picker chases single-sample noise, and with much more the
# 1–2s events this exists to find are averaged into the run around them.
SMOOTH_HALF = 5

WINDOW_HALF_S = 4.0        # ±4s around a peak — the fine read's window
MIN_SEPARATION_S = 6.0     # two peaks closer than this are the same event


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


_MAFD = re.compile(r"lavfi\.scd\.mafd=([0-9.]+)")


def motion_track(video: Path, hz: float = MOTION_HZ,
                 width: int = MOTION_WIDTH) -> list[float]:
    """Mean absolute frame difference at `hz`, one ffmpeg pass, nothing to install.

    `scdet` is a scene-change detector, used here for the number it computes on the
    way rather than for its verdict: with `t=0` it flags nothing and annotates every
    frame with `mafd`, which is exactly the "how much did the picture change" track
    this needs. Measured on Killington's proxies: 318s of 720p in ~6s.

    Raises RuntimeError if ffmpeg fails — a silently empty track would read as a clip
    where nothing ever happens, which is the worst possible failure for a detector.
    """
    r = _run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(video), "-an",
              "-vf", f"fps={hz:g},scale={width}:-2,scdet=s=0:t=0,metadata=print:file=-",
              "-f", "null", "-"])
    if r.returncode != 0:
        raise RuntimeError(f"motion scan failed for {video.name}: {r.stderr[-300:]}")
    return [float(m.group(1)) for m in _MAFD.finditer(r.stdout)]


def robust_z(vals: list[float]) -> list[float]:
    """Median/MAD standardisation. Mean/σ would be dragged around by the very spikes
    this is trying to find — one crash inflates σ enough to hide the next one."""
    if not vals:
        return []
    med = statistics.median(vals)
    mad = statistics.median([abs(v - med) for v in vals])
    scale = 1.4826 * mad or 1e-6
    return [(v - med) / scale for v in vals]


def smooth(vals: list[float], half: int = SMOOTH_HALF) -> list[float]:
    if half <= 0 or not vals:
        return list(vals)
    n = len(vals)
    out = []
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        out.append(sum(vals[lo:hi]) / (hi - lo))
    return out


def excitement(motion: list[float], onset: list[float],
               half: int = SMOOTH_HALF) -> list[float]:
    """One track saying "something happened here", from the picture and the sound.

    Combined with `max`, not a sum, on purpose. The two signals answer the same
    question through different physics and either one alone is sufficient evidence: a
    chase-cam jump barely moves the audio, and a landing heard as a crunch can happen
    just off the bottom of frame. Summing would demand both and find neither.
    """
    zm = smooth(robust_z(motion), half)
    zo = smooth(robust_z(onset), half)
    n = min(len(zm), len(zo)) if zo else len(zm)
    if not zo:
        return zm
    return [max(zm[i], zo[i]) for i in range(n)]


def peak_in(track: list[float], start: float, end: float,
            hz: float = MOTION_HZ) -> tuple[float, float]:
    """The largest value of `track` inside [start, end), and when it happened.

    This is the corroboration term: given a window somebody else proposed — a moment
    from the visual pass, a segment in the cut — how unusual is the picture and sound
    inside it. Returns (0.0, start) for a window off the end of the track.
    """
    lo, hi = max(0, int(start * hz)), min(len(track), int(round(end * hz)) + 1)
    if lo >= hi:
        return 0.0, round(start, 2)
    best = max(range(lo, hi), key=lambda i: track[i])
    return round(track[best], 3), round(best / hz, 2)


def candidate_windows(track: list[float], *, hz: float = MOTION_HZ,
                      half_width_s: float = WINDOW_HALF_S,
                      min_separation_s: float = MIN_SEPARATION_S,
                      limit: int = 8, duration: float | None = None,
                      floor_z: float = 1.5) -> list[dict]:
    """The `limit` most unusual moments in a clip, as windows to look at closely.

    Recall-first by design. These windows are not claims that something happened —
    they are the places worth spending a model call to find out, and a whiteout, a
    lens wipe and a backflip all spike the same track. The fine read is what tells
    them apart; this only has to put the backflip on the list.

    Overlapping windows are merged rather than paid for twice: a landing 3s after a
    take-off is one event and one sheet, and the sheet is cheaper than the pair.
    """
    if not track:
        return []
    end_of_clip = duration if duration is not None else len(track) / hz
    sep = max(1, int(min_separation_s * hz))
    picked: list[int] = []
    for i in sorted(range(len(track)), key=lambda j: -track[j]):
        if track[i] < floor_z:
            break
        if all(abs(i - j) >= sep for j in picked):
            picked.append(i)
        if len(picked) >= limit:
            break
    picked.sort()

    windows: list[dict] = []
    for i in picked:
        at = i / hz
        start, end = max(0.0, at - half_width_s), min(end_of_clip, at + half_width_s)
        if windows and start <= windows[-1]["end"]:
            prev = windows[-1]
            prev["end"] = max(prev["end"], end)
            if track[i] > prev["z"]:
                prev["z"], prev["at"] = round(track[i], 3), round(at, 2)
            continue
        windows.append({"start": round(start, 2), "end": round(end, 2),
                        "at": round(at, 2), "z": round(track[i], 3)})
    return windows


def scan_clip(proxy: Path, onset: list[float] | None = None, *,
              hz: float = MOTION_HZ, limit: int = 8,
              half_width_s: float = WINDOW_HALF_S,
              duration: float | None = None) -> dict:
    """Motion track + onset → an excitement track and the windows worth looking at."""
    motion = motion_track(proxy, hz=hz)
    track = excitement(motion, onset or [])
    return {
        "clip": proxy.name, "hz": hz, "samples": len(track), "track": track,
        "windows": candidate_windows(track, hz=hz, limit=limit,
                                     half_width_s=half_width_s, duration=duration),
    }
