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

import json
import re
import statistics
import subprocess
import time
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


# ------------------------------------------------------------------- the rank
#
# What a moment is worth, before any evidence about whether it is real. Ordered by
# the brief — *"favour moments with people, reactions and speech over empty scenery"* —
# with events above people because events are the thing a transcript structurally
# cannot see, which is the entire reason the visual pass exists.
KIND_WEIGHT = {
    "fall": 1.0, "crash": 1.0, "jump": 0.9,
    "reaction": 0.6, "faces": 0.5,
    "action": 0.3, "scenery": 0.1,
    # Not a low score: a floor. A camera artefact is not a weak moment, it is a
    # moment that must never be offered, and R10 measured that on this footage
    # artefacts are what the biggest frame differences actually are.
    "junk": 0.0,
}
UNKNOWN_KIND = 0.3

NOTABLE = 1.0            # the reader thought this one mattered
NOT_NOTABLE = 0.55

# How much a motion or onset peak inside the window is allowed to add. Capped low on
# purpose: R10 measured that on POV ski footage the largest frame differences are whip
# pans, glove wipes and whiteouts, so corroboration says "worth checking", never
# "therefore real". A term that could double a score would rank the artefacts first.
CORROBORATION_GAIN = 0.4
CORROBORATION_FULL_Z = 4.0

# The confirmation term — the only one carrying real evidence, because it is the only
# one where two independent looks at the same seconds agree or disagree.
CONFIRMED = 1.5          # a close look found an event of the same family here
UNSEEN = 1.0             # nobody has looked closely; the claim stands unaudited
UNSUPPORTED = 0.6        # looked at 1s and found nothing to report
CONTRADICTED = 0.35      # looked at 1s and found a camera artefact instead

UNUSABLE_PENALTY = 0.4   # you cannot cut here, however good the moment was
# How much of a moment a fine window must cover before its silence counts as evidence.
# Half, not more: a window is centred on the sharpest change in the moment, so it sees
# the part that the claim is about. At 0.6 the CLIP_04 "fall 264-272" escaped audit by
# one tenth of a second of coverage, and it is the exact claim the audit refutes.
COVERED = 0.5

HOT = {"fall", "crash", "jump"}


def overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def _covered(moment: dict, spans: list) -> float:
    """What fraction of a moment lies inside any of `spans`."""
    length = max(1e-6, moment["end"] - moment["start"])
    seen = sum(overlap((moment["start"], moment["end"]), (float(s[0]), float(s[1])))
               for s in spans)
    return min(1.0, seen / length)


def confirmation(moment: dict, fine: dict) -> str:
    """Did a close look at these seconds agree, disagree, or never happen?

    This is the term R10 was written to justify. Adjudicated by eye against the
    frames, *neither* pass's `kind` is trustworthy on this footage on its own: the
    4s pass called a tilted follow-cam "airborne across 8 consecutive frames" and the
    1s pass called a chairlift passenger "skier airborne off jump feature". What does
    survive is agreement and disagreement between two independent looks — so the
    ranking leans on that and not on the label.
    """
    windows = fine.get("windows_read") or []
    if not windows or _covered(moment, windows) < COVERED:
        return "unseen"
    span = (moment["start"], moment["end"])
    hot_here = [m for m in fine.get("moments", [])
                if m.get("kind") in HOT and overlap(span, (m["start"], m["end"])) > 0]
    if hot_here:
        return "confirmed"
    junk = [m for m in fine.get("moments", [])
            if m.get("kind") == "junk" and overlap(span, (m["start"], m["end"])) > 0]
    if junk or _covered(moment, [(u["start"], u["end"])
                                for u in fine.get("unusable", [])]) > 0.3:
        return "contradicted"
    return "unsupported"


CONFIRMATION_WEIGHT = {"confirmed": CONFIRMED, "unseen": UNSEEN,
                       "unsupported": UNSUPPORTED, "contradicted": CONTRADICTED}


def score_moment(moment: dict, *, track: list[float] | None = None,
                 hz: float = MOTION_HZ, unusable: list | None = None,
                 status: str = "unseen") -> dict:
    """kind x notable x corroboration x confirmation x usable, and the evidence for it.

    Every term is returned alongside the number. A ranking whose reasoning is not
    visible is a ranking nobody can argue with, and this project has already learned
    once (the `why` field on a segment) that the reason is what a human checks the
    machine against.
    """
    kind = (moment.get("kind") or "").strip().lower()
    weight = KIND_WEIGHT.get(kind, UNKNOWN_KIND)
    notable = NOTABLE if moment.get("notable") else NOT_NOTABLE
    z, at = (peak_in(track, moment["start"], moment["end"], hz)
             if track else (0.0, moment["start"]))
    corr = 1.0 + CORROBORATION_GAIN * min(1.0, max(0.0, z) / CORROBORATION_FULL_Z)
    blocked = _covered(moment, [(u["start"], u["end"]) for u in (unusable or [])])
    usable = UNUSABLE_PENALTY if blocked > 0.5 else 1.0
    score = weight * notable * corr * CONFIRMATION_WEIGHT[status] * usable
    return {"score": round(score, 3), "kind": kind, "why_ranked": {
        "kind_weight": weight, "notable": bool(moment.get("notable")),
        "corroboration_z": z, "peak_at": at, "confirmation": status,
        "unusable_overlap": round(blocked, 2)}}


def rank_clip(clip: str, coarse: dict, fine: dict | None = None,
              track: list[float] | None = None, hz: float = MOTION_HZ) -> list[dict]:
    """One clip's moments, coarse and fine together, each with a score and its reason.

    A fine moment supersedes the coarse moment it audits rather than joining it: they
    are two accounts of the same seconds, and the closer look is the better one. The
    coarse claim survives in `superseded` so the disagreement stays visible.
    """
    fine = fine or {}
    unusable = list(coarse.get("unusable", [])) + list(fine.get("unusable", []))
    out: list[dict] = []

    fine_spans = [(m["start"], m["end"]) for m in fine.get("moments", [])]
    for m in coarse.get("moments", []):
        status = confirmation(m, fine)
        if status == "confirmed" and _covered(m, fine_spans) > 0.0:
            # Two looks agree; the fine one carries the tighter timestamps, so it is
            # the one that goes on the list. Dropping the coarse copy here is what
            # keeps a confirmed event from occupying two slots in the top ten.
            continue
        out.append({"clip": clip, "start": m["start"], "end": m["end"],
                    "what": m.get("what", ""), "notable": bool(m.get("notable")),
                    "source": "sheet",
                    "frames": [float(f) for f in (m.get("frames") or [])],
                    "confidence": m.get("confidence", ""),
                    "demoted": m.get("demoted", ""),
                    **score_moment(m, track=track, hz=hz, unusable=unusable,
                                   status=status)})
    for m in fine.get("moments", []):
        # A fine moment landing on a coarse claim of the same family is two looks
        # agreeing, which is the strongest evidence available here.
        span = (m["start"], m["end"])
        agrees = any(c.get("kind") in HOT and overlap(span, (c["start"], c["end"])) > 0
                     for c in coarse.get("moments", [])
                     if (m.get("kind") or "") in HOT)
        out.append({"clip": clip, "start": m["start"], "end": m["end"],
                    "what": m.get("what", ""), "notable": bool(m.get("notable")),
                    "source": "close look",
                    "frames": [float(f) for f in (m.get("frames") or [])],
                    "confidence": m.get("confidence", ""),
                    "demoted": m.get("demoted", ""),
                    **score_moment(m, track=track, hz=hz, unusable=unusable,
                                   status="confirmed" if agrees else "unseen")})
    return [e for e in out if e["score"] > 0.0]


def rank_bin(per_clip: dict[str, dict]) -> list[dict]:
    """Every clip's events in one order, which is the order that matters.

    Deliberately a **bin-wide** list. The question the Ask and the board both have is
    "what are the biggest things in this footage", and that cannot be assembled out of
    per-clip files without re-deriving it on every read.
    """
    out: list[dict] = []
    for clip, d in per_clip.items():
        out += rank_clip(clip, d.get("coarse", {}), d.get("fine"), d.get("track"),
                         d.get("hz", MOTION_HZ))
    out.sort(key=lambda e: (-e["score"], e["clip"], e["start"]))
    for i, e in enumerate(out, 1):
        e["rank"] = i
    return out


def merge_moments(coarse: list[dict], fine: dict | None) -> list[dict]:
    """The clip inventory's "what is visible" lines, with the close look folded in.

    Where a fine window audited a coarse moment, the fine account replaces it. That is
    the point of paying for the second look: an inventory that still lists a backflip
    the close read found to be a glove over the lens teaches the Ask the wrong thing.
    """
    fine = fine or {}
    if not fine.get("moments") and not fine.get("windows_read"):
        return list(coarse)
    kept = [dict(m) for m in coarse
            if confirmation(m, fine) in ("unseen", "unsupported")]
    kept += [{**m, "fine": True} for m in fine.get("moments", [])]
    kept.sort(key=lambda m: m["start"])
    return kept


# ------------------------------------------------------------------- on disk
#
# The rank lives in **one file per bin**, beside the visual sidecars, rather than as an
# `events` list inside each of them. Four reasons, in the order they mattered:
#
#   1. The question it answers is bin-wide. "The twelve biggest things in this footage"
#      is what the Ask needs and what the board's *seen* tab shows; assembling that out
#      of twelve per-clip files means re-deriving the sort on every read.
#   2. It is **derived**, and the sidecars are **paid observations**. Every weight here
#      will change; rewriting a file that cost model calls in order to change a
#      multiplier risks the one property that stops the pass re-buying work — that the
#      sidecar's existence means the clip has been read.
#   3. The join has three sources (coarse sidecar, fine sidecar, motion track) and only
#      one of them is per clip.
#   4. It can be deleted at any time and rebuilt for nothing.

EVENTS_FILE = "events.json"
MOTION_SUFFIX = ".motion.json"


def motion_cache(visual_dir: Path, stem: str) -> list[float]:
    p = visual_dir / f"{stem}{MOTION_SUFFIX}"
    if not p.exists():
        return []
    d = json.loads(p.read_text(encoding="utf-8"))
    return d.get("mafd", []) if d.get("hz") == MOTION_HZ else []


def write_motion_cache(visual_dir: Path, stem: str, clip: str,
                       mafd: list[float]) -> None:
    visual_dir.mkdir(parents=True, exist_ok=True)
    (visual_dir / f"{stem}{MOTION_SUFFIX}").write_text(
        json.dumps({"clip": clip, "hz": MOTION_HZ, "width": MOTION_WIDTH,
                    "mafd": [round(v, 3) for v in mafd]}), encoding="utf-8")


def _onset(audio_dir: Path, stem: str) -> list[float]:
    p = audio_dir / f"{stem}.audio.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8")).get("tracks", {}).get("onset", [])


def gather(visual_dir: Path, audio_dir: Path) -> dict[str, dict]:
    """Everything known about each clip that has been looked at, keyed by clip name.

    Degrades rather than fails: no motion cache means corroboration comes from the
    onset track alone, no onset track means it comes from the picture alone, and
    neither means every moment scores its corroboration term at 1.0 — weaker, still
    ordered, never an exception in front of the board.
    """
    per_clip: dict[str, dict] = {}
    for p in sorted(visual_dir.glob("*.visual.json")):
        stem = p.name[: -len(".visual.json")]
        coarse = json.loads(p.read_text(encoding="utf-8"))
        fine_path = visual_dir / f"{stem}.fine.json"
        fine = (json.loads(fine_path.read_text(encoding="utf-8"))
                if fine_path.exists() else {})
        track = excitement(motion_cache(visual_dir, stem), _onset(audio_dir, stem))
        per_clip[coarse.get("clip") or f"{stem}.MP4"] = {
            "coarse": coarse, "fine": fine, "track": track, "hz": MOTION_HZ}
    return per_clip


def build(visual_dir: Path, audio_dir: Path) -> dict:
    per_clip = gather(visual_dir, audio_dir)
    ranked = rank_bin(per_clip)
    return {
        "built": round(time.time(), 3),
        "clips": len(per_clip),
        "events": ranked,
        "params": {"kind_weight": KIND_WEIGHT, "not_notable": NOT_NOTABLE,
                   "corroboration_gain": CORROBORATION_GAIN,
                   "confirmation": CONFIRMATION_WEIGHT,
                   "unusable_penalty": UNUSABLE_PENALTY,
                   "provisional": "R10 — weights argued, not fitted"},
    }


def write(visual_dir: Path, payload: dict) -> Path:
    visual_dir.mkdir(parents=True, exist_ok=True)
    path = visual_dir / EVENTS_FILE
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return path


def load(visual_dir: Path) -> list[dict]:
    """The ranked events, or an empty list. Never an exception: the board asks for this
    on every poll and a half-written or hand-edited file must not take the app down."""
    path = visual_dir / EVENTS_FILE
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("events", [])
    except (ValueError, OSError):
        return []
