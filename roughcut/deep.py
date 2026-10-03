"""Look deeper — keyframes where the picture changes, read in order, the gaps reasoned out.

Karl, 2026-10-03 (#4): *"make it clearer how the videos are indexed by the agent (e.g.
showing granularity), and make it easier to run deeper keyframe-based analysis (w/ AI
interpolating as needed between frames to really understand what is going on)."*

Both earlier looks read **contact sheets**: thumbnails in a grid, every 4 s at 320 px for
the coarse pass, every 1 s at 480 px for the close look. A grid is a poor way to see
motion. R10 measured what that costs on helmet and chest mounts — both passes read
camera roll as flips — and R11 found the other half: the one real fall in CLIP_11
(143.5-149: a glove across the lens, spray, *skis against the sky at 145.0-145.5*, tree
canopy from below) was scored by both passes and by eye as a rolled POV with nothing in
it. Re-read at 0.5 s it was obvious. The close look is "a good auditor and a poor
detector", and the reason is the sheet, not the reader.

So this look is different in kind, not just denser:

1. **Keyframes, not a grid.** Frames are chosen where the picture changes — the peaks
   and turns of the free 10 Hz motion track (`events.motion_track`) — plus a uniform
   floor so a still stretch is never unsampled, capped. Each is its own 640 px JPEG with
   its timestamp, handed to the model in order, so it sees the frame at a resolution a
   person could read and knows exactly how far apart two frames are.
2. **The motion between them, as numbers.** Between every two frames the prompt says
   how much the picture changed and where the peak was. A gap with a 30-point spike in it
   is a gap something happened in, whatever the two frames on either side show.
3. **Interpolation, said out loud.** The answer is a beat-by-beat account in which every
   beat is either `seen` on a named frame or `inferred` between two named bracketing
   frames, with why. That is what lets a human check the reasoning — and it is the
   honest form of "the AI interpolating between frames": the model does not get to
   blur the two.
4. **More frames where it is unsure — once.** The model may name up to NEED_FRAMES_MAX
   timestamps where its inference is weakest; if it does and the budget allows, one
   follow-up call adds exactly those frames and takes the final answer. Bounded: never a
   loop, because an open-ended "look again" is a slower way to spend money.

The result is a `<stem>.deep.json` beside the coarse and close sidecars — a paid
observation like them, never rewritten except to add a span — and `roughcut.events`
treats it as the strongest single look there is.

Every number here is **provisional** (argued, not measured): the cap, the floor, the
width, the price. The deep look is new; the first live runs are what measure it.
"""

from __future__ import annotations

import json
import math
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from . import config, inference

DEEP_ROLE = config.ROLE_JUDGE          # the deep tier (Karl, 2026-10-03: opus for depth)
PROMPT_VERSION = 1

# --- the span ---------------------------------------------------------------
# Twenty seconds is a fall and its aftermath, or a jump with its run-in and landing —
# the size of thing worth a close account. Longer, and 24 frames thin out to the coarse
# pass's density with a dearer reader; the editor can always look again further on.
MAX_SPAN_S = 20.0
MIN_SPAN_S = 1.0
# A moment from the seen tab is looked at with this much either side: an event's own
# span is the reader's guess, and the useful frames are often just outside it (the
# run-in, the aftermath).
PAD_S = 2.0

# --- the frames -------------------------------------------------------------
# 24 frames: at 640 px each is ~310 image tokens on the API, so the frames are a small
# part of the call; the cap is about how many separate frames a reader can hold in order,
# and about the CLI reading each from disk. Provisional.
FRAME_CAP = 24
# Half the cap goes to the uniform floor, so a span with one huge spike still has its
# quiet seconds sampled; the floor is never denser than one frame a second, because
# below that the motion track (10 Hz) is the better judge of where to look.
FLOOR_SHARE = 0.5
FLOOR_MIN_S = 1.0
# Two frames closer than this show the same thing at 30 fps on ski footage.
MIN_GAP_S = 0.3
# A turn in the motion track must stand this far (robust z, within the span) above its
# neighbourhood to earn a frame. Low on purpose: the floor already covers the span; these
# frames are there to land on the change itself.
MOTION_MIN_Z = 1.0
FRAME_WIDTH = 640
NEED_FRAMES_MAX = 8
# A model's timestamps are snapped to the nearest frame within this; a frame it names
# further than this from any frame does not exist.
SNAP_S = 0.06

# The vocabulary the coarse and close passes already use, so the rank reads all three.
KINDS = ("action", "fall", "crash", "jump", "reaction", "faces", "scenery",
         "pov-gear", "junk")
MOUNTS = ("helmet", "chest", "handheld", "pole", "static", "unknown")

# --- the price --------------------------------------------------------------
# No deep call has been measured yet. The fixed part of one CLI call that reads images
# from disk is the close look's measured $0.073 per window on the small tier (most of it
# is the prompt and the CLI's own context, not the picture), scaled to the deep tier.
# On top, each frame: 640x360 ≈ 307 image tokens, counted READS_PER_FRAME times because
# the CLI opens frames with a tool call and every later turn carries the earlier images
# again. Over-estimates on purpose; the ledger will say by how much.
CALL_USD_SMALL = 0.073
FRAME_TOKENS = 307
READS_PER_FRAME = 4
# Wall clock, for the bar: the close look's ~35 s per sheet, plus time per frame read.
CALL_S = 40.0
FRAME_S = 2.0


def price(frames: int, *, model: str | None = None) -> float:
    """Projected USD for one call reading `frames` frames."""
    model = model or config.model_for(DEEP_ROLE)
    per_mtok_in = config.price_per_mtok(model)[0]
    base = CALL_USD_SMALL * config.price_scale(model)
    return round(base + frames * FRAME_TOKENS * READS_PER_FRAME * per_mtok_in / 1e6, 4)


def quote(frames: int, *, model: str | None = None) -> dict:
    """What the button says: the likely price (one call) and the bound (the follow-up
    with up to NEED_FRAMES_MAX more frames). The budget is checked against the bound."""
    one = price(frames, model=model)
    two = one + price(frames + NEED_FRAMES_MAX, model=model)
    return {"frames": frames, "calls": 1, "max_calls": 2,
            "projected_usd": round(one, 2), "max_usd": round(two, 2),
            "eta_s": round(CALL_S + frames * FRAME_S, 1),
            "max_eta_s": round(2 * CALL_S + (2 * frames + NEED_FRAMES_MAX) * FRAME_S, 1),
            "model": model or config.model_for(DEEP_ROLE)}


# ------------------------------------------------------------------ the span

def clamp_span(start: float, end: float, duration: float | None) -> tuple[float, float]:
    """The span a look will read: inside the clip, at least MIN_SPAN_S, at most
    MAX_SPAN_S (a longer ask keeps its start — the editor dragged from there)."""
    start, end = float(start), float(end)
    if end < start:
        start, end = end, start
    if duration is not None and duration > 0:
        start, end = max(0.0, min(start, duration)), max(0.0, min(end, duration))
    start = max(0.0, start)
    if end - start > MAX_SPAN_S:
        end = start + MAX_SPAN_S
    if end - start < MIN_SPAN_S:
        mid = (start + end) / 2
        start, end = max(0.0, mid - MIN_SPAN_S / 2), mid + MIN_SPAN_S / 2
        if duration is not None and duration > 0 and end > duration:
            end, start = duration, max(0.0, duration - MIN_SPAN_S)
    return round(start, 2), round(end, 2)


# ------------------------------------------------------------------ keyframes

def _smooth(vals: list[float], half: int = 1) -> list[float]:
    n = len(vals)
    return [sum(vals[max(0, i - half):min(n, i + half + 1)])
            / (min(n, i + half + 1) - max(0, i - half)) for i in range(n)]


def keyframes(mafd: list[float] | None, start: float, end: float, *,
              hz: float = 10.0, cap: int = FRAME_CAP, min_gap: float = MIN_GAP_S,
              floor_s: float | None = None) -> list[dict]:
    """The frames worth reading in [start, end]: a uniform floor, then the motion turns.

    Returns `[{"t", "why", "z"}]` in time order, `why` one of `floor` / `peak` / `turn`
    / `fill`. A **peak** is where the picture changes fastest — the impact, the whip; a
    **turn** is where it settles or starts to — the frame just before, the frame just
    after. Both matter for interpolation: the model can only reason about a gap whose two
    ends it has seen. A **fill** spends what the cap has left on the gaps with the most
    change in them. With no motion track, the fill is even.
    """
    span = max(0.0, end - start)
    cap = max(2, int(cap))
    last = max(start, end - 0.05)            # a frame at the very end may be past EOF
    step = floor_s or max(FLOOR_MIN_S, span / max(1, int(cap * FLOOR_SHARE)))
    n_floor = int(math.floor(span / step + 1e-9)) + 1
    out: list[dict] = []
    for k in range(n_floor):
        t = min(last, start + k * step)
        if out and t - out[-1]["t"] < min_gap / 2:
            continue
        out.append({"t": round(t, 2), "why": "floor", "z": 0.0})
    if out and last - out[-1]["t"] >= min_gap:
        out.append({"t": round(last, 2), "why": "floor", "z": 0.0})
    out = out[:cap]

    track = list(mafd or [])
    lo, hi = max(0, int(math.ceil(start * hz))), min(len(track) - 1, int(end * hz))
    if hi - lo >= 2:
        seg = _smooth(track[lo:hi + 1], 1)
        med = statistics.median(seg)
        scale = 1.4826 * statistics.median([abs(v - med) for v in seg]) or 1e-6
        cands: list[tuple[float, float, str]] = []
        for i in range(1, len(seg) - 1):
            a, b, c = seg[i - 1], seg[i], seg[i + 1]
            kind = "peak" if (b >= a and b > c) else "turn" if (b <= a and b < c) else ""
            if not kind:
                continue
            near = seg[max(0, i - 10):i + 11]
            z = abs(b - statistics.median(near)) / scale
            if z >= MOTION_MIN_Z:
                cands.append((z, (lo + i) / hz, kind))
        cands.sort(key=lambda c: -c[0])
        for z, t, kind in cands:
            if len(out) >= cap:
                break
            if t < start or t > last:
                continue
            if all(abs(t - f["t"]) >= min_gap for f in out):
                out.append({"t": round(t, 2), "why": kind, "z": round(z, 2)})
    out.sort(key=lambda f: f["t"])
    # Whatever the cap has left goes where the picture changed most per frame: split the
    # gap with the most motion in it (gap x mean change), again, until the cap. Measured
    # on CLIP_11 141-155 (R11's fall): the floor and the turns leave 145.0-145.5 — the
    # skis against the sky — between 144.5 and 145.7; the fill puts a frame in it.
    while len(out) < cap and len(out) >= 2:
        best, at = 0.0, -1
        for i in range(len(out) - 1):
            gap = out[i + 1]["t"] - out[i]["t"]
            if gap < 2 * min_gap:
                continue
            m = motion_between(track, out[i]["t"], out[i + 1]["t"], hz) if track else None
            weight = gap * (m["mean"] if m else 1.0)
            if weight > best:
                best, at = weight, i
        if at < 0:
            break
        mid = round((out[at]["t"] + out[at + 1]["t"]) / 2, 2)
        out.insert(at + 1, {"t": mid, "why": "fill", "z": 0.0})
    return out


def motion_between(mafd: list[float] | None, a: float, b: float,
                   hz: float = 10.0) -> dict | None:
    """How much the picture changed between two frames: mean, and the peak and when."""
    track = list(mafd or [])
    lo, hi = max(0, int(math.ceil(a * hz))), min(len(track), int(math.floor(b * hz)) + 1)
    if hi <= lo:
        return None
    vals = track[lo:hi]
    k = max(range(len(vals)), key=lambda i: vals[i])
    return {"mean": round(sum(vals) / len(vals), 1), "peak": round(vals[k], 1),
            "peak_at": round((lo + k) / hz, 2)}


def frame_name(t: float) -> str:
    return f"t{t:07.2f}.jpg"


def extract_frames(proxy: Path, times: list[float], out_dir: Path,
                   width: int = FRAME_WIDTH) -> list[Path]:
    """One JPEG per timestamp, from the proxy (already oriented; 720p is plenty for a
    640 px frame). Cached by name: a frame is free, but there is no reason to cut it
    twice. Raises RuntimeError rather than handing the model a missing frame."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for t in times:
        p = out_dir / frame_name(t)
        if not p.exists() or p.stat().st_size == 0:
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{t:.3f}",
                 "-i", str(proxy), "-frames:v", "1", "-vf", f"scale={width}:-2",
                 "-q:v", "3", str(p)], capture_output=True, text=True, check=False)
            if r.returncode != 0 or not p.exists() or p.stat().st_size == 0:
                raise RuntimeError(f"could not cut the frame at {t:.2f}s from "
                                   f"{proxy.name}: {r.stderr[-200:]}")
        paths.append(p)
    return paths


# ------------------------------------------------------------------ the read

SYSTEM = (
    "You are a careful video analyst. You are given individual frames from one short "
    "span of one clip, in time order, each with its exact timestamp, and numbers for "
    "how much the picture changed between consecutive frames. You reconstruct what "
    "happens continuously across the span. You never blur what you saw with what you "
    "infer: every claim is either seen on a named frame or inferred between two named "
    "frames, and an inference says why. The clips are mostly from action cameras worn "
    "on a helmet or chest: the wearer is rarely in frame, their gloves, poles and ski "
    "tips often are, and the horizon tilts and rolls with their head.")

SCHEMA = {
    "beats": [{"start": 141.3, "end": 142.0, "basis": "seen | inferred",
               "frame": 141.3, "between": [141.3, 142.6],
               "what": "one sentence: what happens in this beat",
               "why": "inferred beats only: what in the two frames and the motion "
                      "between them supports it"}],
    "events": [{"kind": " | ".join(KINDS), "start": 143.4, "end": 145.5,
                "confidence": "high | medium | low", "notable": True,
                "what": "what the frames show", "frames": [143.6, 145.2]}],
    "camera": {"mount": " | ".join(MOUNTS),
               "roll": "what the horizon does, and whether that is the camera or the "
                       "rider",
               "evidence": "the frames that decide it"},
    "unsure": ["what you could not settle, and between which frames"],
    "need_frames": [144.3],
    "summary": "two sentences: what happens across the span",
}

PROMPT = """These are {n} frames from {clip}, covering {start:.2f}s to {end:.2f}s of the \
clip, in time order. They are individual frames, not a contact sheet: each file is one \
frame, named by its timestamp in clip seconds. Most were chosen where the picture changes \
(the motion track's peaks and turns), the rest evenly so no stretch goes unseen.

The frames, and between each pair how much the picture changed (mean absolute frame \
difference at 10 Hz on a 160 px copy; this clip's median is {median:.1f}, so a number \
several times that is a big change):

{listing}

Reconstruct what happens across the whole span, continuously, as **beats** in order:

* A beat is `seen` when a frame shows it. Give that frame's timestamp in `frame`; the
  beat's start and end sit at or around that frame.
* A beat is `inferred` when it happens between two frames you can see. Give the two
  bracketing frames' timestamps in `between` (the earlier first), keep the beat's start
  and end inside them, and say in `why` what in those two frames and the motion number
  between them makes you believe it. A large change between two frames that look alike
  is the camera moving; a large change between two that look different is something
  happening — say which.
* Cover the span. Do not invent detail the frames cannot support; "the camera turns
  away between 143.6 and 144.3" is a good inferred beat.

Then the **events** — {kinds} — each with start and end in clip seconds, `confidence`,
`notable` (true for events and for people; never for scenery, junk or pov-gear) and the
frames that show it.

The **camera**: what it is mounted on, and what the horizon does. On a helmet or chest
mount the camera rolls, not the rider: a tilted or upside-down horizon is camera roll
**unless the ground under the skis says otherwise** — skis or boots against the sky, the
snow above the wearer's own ski tips, the wearer's gear pointing up at trees from below.
Look at the skis before calling a flip; look at the skis before calling it nothing.

`unsure`: what you could not settle, and between which frames.
{need}
All times are clip seconds between {start:.2f} and {end:.2f}. Name only frames from the
list above."""

NEED_ASK = """
`need_frames`: if a gap matters and you cannot infer across it with confidence, list up
to {max} timestamps (clip seconds, inside the span, not already in the list) where one
more frame would settle it. You will get exactly those frames once, and then give your
final answer. An empty list is the normal answer when the frames are enough.
"""

NEED_FINAL = """
`need_frames` must be empty: this is your final answer. The frames marked **asked for**
were added because your first reading asked for them; your first reading was:

{previous}
"""


def _listing(frames: list[dict], mafd: list[float] | None, hz: float,
             asked: set[float]) -> str:
    rows = []
    for i, f in enumerate(frames):
        why = {"floor": "even", "peak": "motion peak", "turn": "motion turn",
               "fill": "where the change was densest",
               "asked": "asked for"}.get(f["why"], f["why"])
        tag = "**asked for**" if f["t"] in asked else why
        rows.append(f"F{i + 1:02d}  t={f['t']:.2f}s  file {frame_name(f['t'])}  ({tag})")
        if i + 1 < len(frames):
            m = motion_between(mafd, f["t"], frames[i + 1]["t"], hz)
            gap = frames[i + 1]["t"] - f["t"]
            rows.append(f"      ↓ {gap:.2f}s" + (
                f", change mean {m['mean']:.1f}, peak {m['peak']:.1f} at {m['peak_at']:.2f}s"
                if m else ", no motion track"))
    return "\n".join(rows)


def build_prompt(clip: str, start: float, end: float, frames: list[dict],
                 mafd: list[float] | None, hz: float = 10.0, *,
                 previous: dict | None = None, asked: list[float] | None = None) -> str:
    asked_set = {round(float(t), 2) for t in (asked or [])}
    med = statistics.median(mafd) if mafd else 0.0
    need = (NEED_FINAL.format(previous=json.dumps(
        {k: previous.get(k) for k in ("beats", "events", "camera", "unsure", "summary")},
        indent=1)[:6000]) if previous is not None
        else NEED_ASK.format(max=NEED_FRAMES_MAX))
    return PROMPT.format(n=len(frames), clip=clip, start=start, end=end, median=med,
                         listing=_listing(frames, mafd, hz, asked_set),
                         kinds=", ".join(f"`{k}`" for k in KINDS), need=need)


# ------------------------------------------------------------------ validation

def _num(x: Any, what: str) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        raise ValueError(f"{what} is not a number: {x!r}") from None
    if not math.isfinite(v):
        raise ValueError(f"{what} is not finite")
    return v


def _frame(x: Any, times: list[float], what: str) -> float:
    t = _num(x, what)
    near = min(times, key=lambda f: abs(f - t))
    if abs(near - t) > SNAP_S:
        raise ValueError(f"{what} {t:g} is not one of the frames "
                         f"({', '.join(f'{f:g}' for f in times)})")
    return near


def validate(payload: Any, start: float, end: float, times: list[float], *,
             allow_need: bool = True) -> dict:
    """Strict about time and about the line between seen and inferred.

    Anything wrong raises ValueError, which `inference.complete` turns into its one
    bounded re-ask: a beat outside the span, a `seen` beat on a frame that does not
    exist, an `inferred` beat without two real bracketing frames around it or without a
    reason, an event of a kind outside the vocabulary. A deep look that blurs the two is
    the failure this whole module is for, so it is not repaired here — it is refused.
    """
    if not isinstance(payload, dict):
        raise ValueError("expected an object")
    times = sorted(round(float(t), 2) for t in times)
    if not times:
        raise ValueError("no frames")
    lo, hi = start - SNAP_S, end + SNAP_S

    def inside(a: float, b: float, what: str) -> None:
        if not (lo <= a <= b <= hi):
            raise ValueError(f"{what} {a:g}-{b:g} is outside the span "
                             f"{start:g}-{end:g} (or ends before it starts)")

    beats_in = payload.get("beats")
    if not isinstance(beats_in, list) or not beats_in:
        raise ValueError("'beats' must be a non-empty list")
    beats = []
    for i, b in enumerate(beats_in):
        if not isinstance(b, dict):
            raise ValueError(f"beat {i} is not an object")
        a, z = _num(b.get("start"), f"beat {i} start"), _num(b.get("end"), f"beat {i} end")
        inside(a, z, f"beat {i}")
        basis = str(b.get("basis", "")).strip().lower()
        what = str(b.get("what", "")).strip()[:300]
        if not what:
            raise ValueError(f"beat {i} says nothing")
        row: dict = {"start": round(a, 2), "end": round(z, 2), "basis": basis, "what": what}
        if basis == "seen":
            row["frame"] = _frame(b.get("frame"), times, f"beat {i} frame")
        elif basis == "inferred":
            br = b.get("between")
            if not isinstance(br, (list, tuple)) or len(br) != 2:
                raise ValueError(f"inferred beat {i} must name two bracketing frames "
                                 f"in 'between'")
            f0 = _frame(br[0], times, f"beat {i} bracketing frame")
            f1 = _frame(br[1], times, f"beat {i} bracketing frame")
            if not f0 < f1:
                raise ValueError(f"inferred beat {i}: the bracketing frames must be two "
                                 f"different frames, earlier first")
            if a < f0 - SNAP_S or z > f1 + SNAP_S:
                raise ValueError(f"inferred beat {i} {a:g}-{z:g} is not between its "
                                 f"frames {f0:g} and {f1:g}")
            why = str(b.get("why", "")).strip()[:300]
            if not why:
                raise ValueError(f"inferred beat {i} must say why")
            row.update(between=[f0, f1], why=why)
        else:
            raise ValueError(f"beat {i} basis must be 'seen' or 'inferred', not {basis!r}")
        beats.append(row)
    beats.sort(key=lambda r: (r["start"], r["end"]))

    events = []
    for i, e in enumerate(payload.get("events") or []):
        if not isinstance(e, dict):
            raise ValueError(f"event {i} is not an object")
        kind = str(e.get("kind", "")).strip().lower()
        if kind not in KINDS:
            raise ValueError(f"event {i} kind {kind!r} is not one of {', '.join(KINDS)}")
        a, z = _num(e.get("start"), f"event {i} start"), _num(e.get("end"), f"event {i} end")
        inside(a, z, f"event {i}")
        conf = str(e.get("confidence", "")).strip().lower()
        frames = sorted({_frame(f, times, f"event {i} frame")
                         for f in (e.get("frames") or [])})
        events.append({"kind": kind, "start": round(a, 2), "end": round(z, 2),
                       "confidence": conf if conf in ("high", "medium", "low") else "medium",
                       "notable": bool(e.get("notable")) and kind not in (
                           "scenery", "junk", "pov-gear"),
                       "what": str(e.get("what", "")).strip()[:300], "frames": frames})

    cam = payload.get("camera") if isinstance(payload.get("camera"), dict) else {}
    mount = str(cam.get("mount", "")).strip().lower()
    camera = {"mount": mount if mount in MOUNTS else "unknown",
              "roll": str(cam.get("roll", "")).strip()[:300],
              "evidence": str(cam.get("evidence", "")).strip()[:300]}

    unsure = [str(u).strip()[:200] for u in (payload.get("unsure") or [])
              if str(u).strip()][:10]

    need: list[float] = []
    if allow_need:
        for x in (payload.get("need_frames") or [])[:NEED_FRAMES_MAX]:
            t = _num(x, "need_frames entry")
            if not (start <= t <= end):
                raise ValueError(f"need_frames {t:g} is outside the span {start:g}-{end:g}")
            t = round(t, 2)
            if all(abs(t - f) > SNAP_S for f in times) and all(
                    abs(t - n) > SNAP_S for n in need):
                need.append(t)
    return {"beats": beats, "events": events, "camera": camera, "unsure": unsure,
            "need_frames": sorted(need), "summary": str(payload.get("summary", ""))[:600]}


# ------------------------------------------------------------------ the look

def look(proxy: Path, clip: str, start: float, end: float, *, frames_dir: Path,
         mafd: list[float] | None, hz: float = 10.0, role: str = DEEP_ROLE,
         may_follow_up: Callable[[float], bool] | None = None,
         on_step: Callable[[str, str], None] | None = None) -> dict:
    """Read one span: choose and cut the keyframes, one call, at most one follow-up.

    `may_follow_up(usd)` is asked before the second call with its projected price; the
    server answers from the budget cap. `on_step(key, detail)` reports the milestones
    (`frames`, `read`, `more`) for the progress bar. Returns the span's record for
    `<stem>.deep.json`; raises inference.InferenceError when the read fails.
    """
    step = on_step or (lambda key, detail: None)
    frames = keyframes(mafd, start, end, hz=hz)
    extract_frames(proxy, [f["t"] for f in frames], frames_dir)
    step("frames", f"{len(frames)} frames cut — reading them in order")
    times = [f["t"] for f in frames]
    paths = [frames_dir / frame_name(t) for t in times]
    first = inference.complete(
        build_prompt(clip, start, end, frames, mafd, hz), role=role, images=paths,
        schema=SCHEMA, system=SYSTEM,
        validate=lambda p: validate(p, start, end, times, allow_need=True))
    calls = [first]
    result = first.content
    asked: list[float] = []
    step("read", "read once")
    if result["need_frames"]:
        want = result["need_frames"][:NEED_FRAMES_MAX]
        cost = price(len(frames) + len(want), model=first.model)
        if may_follow_up is None or may_follow_up(cost):
            step("asked", f"it asked for {len(want)} more frame"
                          f"{'' if len(want) == 1 else 's'} — reading again")
            asked = want
            frames = sorted(frames + [{"t": t, "why": "asked", "z": 0.0} for t in want],
                            key=lambda f: f["t"])
            extract_frames(proxy, want, frames_dir)
            times = [f["t"] for f in frames]
            paths = [frames_dir / frame_name(t) for t in times]
            second = inference.complete(
                build_prompt(clip, start, end, frames, mafd, hz, previous=result,
                             asked=asked),
                role=role, images=paths, schema=SCHEMA, system=SYSTEM,
                validate=lambda p: validate(p, start, end, times, allow_need=False))
            calls.append(second)
            result = second.content
            step("more", "read again with the frames it asked for")
    return {
        "start": round(start, 2), "end": round(end, 2),
        "frames": [{"t": f["t"], "why": f["why"], "file": frame_name(f["t"])}
                   for f in frames],
        "asked": asked, "asked_but_refused": (result.get("need_frames") or [])
        if not asked else [],
        "followup": bool(asked),
        "beats": result["beats"], "events": result["events"],
        "camera": result["camera"], "unsure": result["unsure"],
        "summary": result["summary"],
        "model": calls[-1].model, "role": role, "calls": len(calls),
        "projected_usd": round(sum(c.projected_usd for c in calls), 4),
        "input_tokens": sum(c.input_tokens for c in calls),
        "output_tokens": sum(c.output_tokens for c in calls),
        "read_at": round(time.time(), 3), "prompt_version": PROMPT_VERSION,
        "params": {"cap": FRAME_CAP, "floor_share": FLOOR_SHARE, "floor_min_s": FLOOR_MIN_S,
                   "min_gap_s": MIN_GAP_S, "width": FRAME_WIDTH, "hz": hz,
                   "need_frames_max": NEED_FRAMES_MAX,
                   "provisional": "argued, not measured (INTAKE M15)"},
    }


# ------------------------------------------------------------------ on disk

SUFFIX = ".deep.json"


def path_for(visual_dir: Path, stem: str) -> Path:
    return visual_dir / f"{stem}{SUFFIX}"


def load(visual_dir: Path, stem: str) -> dict:
    """The clip's deep looks, or {}. Never an exception — the coverage strip and the
    rank both read this on every request."""
    p = path_for(visual_dir, stem)
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def covering(deep: dict, start: float, end: float, tol: float = 0.25) -> dict | None:
    """A span already read that contains [start, end] — never re-bought (unless forced)."""
    for r in deep.get("spans") or []:
        if r.get("start", 0) - tol <= start and end <= r.get("end", 0) + tol:
            return r
    return None


def store(visual_dir: Path, stem: str, clip: str, record: dict) -> dict:
    """Add one span to the clip's file. A forced re-read of the same span replaces it;
    every other span stays — each one was paid for."""
    d = load(visual_dir, stem)
    spans = [r for r in d.get("spans") or []
             if not (abs(r.get("start", -1) - record["start"]) < 0.05
                     and abs(r.get("end", -1) - record["end"]) < 0.05)]
    spans.append(record)
    spans.sort(key=lambda r: r["start"])
    out = {"clip": clip, "mode": "deep", "spans": spans,
           "projected_usd": round(sum(r.get("projected_usd", 0.0) for r in spans), 4)}
    visual_dir.mkdir(parents=True, exist_ok=True)
    tmp = path_for(visual_dir, stem).with_suffix(".tmp")
    tmp.write_text(json.dumps(out, indent=1), encoding="utf-8")
    tmp.replace(path_for(visual_dir, stem))
    return out


# ------------------------------------------------------------------ coverage

def _downsample(vals: list[float], hz: float, points: int = 400) -> dict:
    """Max-pooled: a sparkline that averaged a one-sample spike away would hide exactly
    the thing the motion lane is there to show."""
    if not vals:
        return {"hz": hz, "step_s": None, "values": []}
    k = max(1, math.ceil(len(vals) / points))
    return {"hz": hz, "step_s": round(k / hz, 3),
            "values": [round(max(vals[i:i + k]), 1) for i in range(0, len(vals), k)]}


def _sheet_frames(d: dict, duration: float | None) -> tuple[list[float], bool]:
    """The frames a coarse sidecar read: recorded when it says, else derived from its
    interval (contact_sheet.py samples at 0, i, 2i …) and said to be derived."""
    if d.get("frames_sampled"):
        return [float(t) for t in d["frames_sampled"]], True
    iv = (d.get("params") or {}).get("interval_s")
    if not iv or not duration:
        return [], False
    n = int(duration // float(iv)) + 1
    return [round(k * float(iv), 2) for k in range(n) if k * float(iv) < duration], False


def _windows(fine: dict) -> list[list[float]]:
    seen, out = set(), []
    for w in fine.get("windows_read") or []:
        key = (round(float(w[0]), 1), round(float(w[1]), 1))
        if key not in seen:
            seen.add(key)
            out.append([float(w[0]), float(w[1])])
    return sorted(out)


def coverage(clip: str, *, duration: float | None, audio: dict, coarse: dict,
             fine: dict, deep: dict, motion: list[float] | None, hz: float,
             moments: list[dict], asr_model: str | None = None) -> dict:
    """Which seconds of a clip each layer of the index looked at, how densely, with what,
    and what each concluded. From the files only — nothing here spends or measures.

    Every number comes from the sidecar's own record of how it was made. Where a sidecar
    does not record something (the model, for files written before it was recorded; the
    frame times of the oldest coarse sidecars) the field says so — `None` and a flag —
    rather than this guessing from today's configuration, which may not be what ran.
    """
    duration = duration or audio.get("duration_s")
    transcript = audio.get("transcript") or []
    heard = {
        "read": bool(audio),
        "utterances": [[round(float(u["start"]), 2), round(float(u["end"]), 2)]
                       for u in transcript if "start" in u and "end" in u],
        "words": sum(len(u.get("words") or []) for u in transcript),
        "candidates": [[round(float(c["t"]), 2), round(float(c.get("end", c["t"])), 2)]
                       for c in audio.get("candidates") or [] if "t" in c],
        "track_hz": audio.get("frame_hz"),
        "model": asr_model,
    }
    cp = coarse.get("params") or {}
    cframes, crec = _sheet_frames(coarse, duration)
    coarse_l = {
        "read": bool(coarse), "interval_s": cp.get("interval_s"),
        "cols": cp.get("cols"), "rows": cp.get("rows"), "width": cp.get("width"),
        "role": cp.get("role"), "model": cp.get("model"),
        "prompt_version": cp.get("prompt_version"),
        "frames": cframes, "frames_recorded": crec,
        "sheets_read": coarse.get("sheets_read"), "sheets_total": coarse.get("sheets_total"),
        "projected_usd": coarse.get("projected_usd"),
    }
    fp = fine.get("params") or {}
    wins = _windows(fine)
    fframes = [float(t) for t in fine.get("frames_sampled") or []]
    frec = bool(fframes)
    if not fframes and fp.get("interval_s"):
        iv = float(fp["interval_s"])
        fframes = sorted({round(w[0] + k * iv, 2) for w in wins
                          for k in range(int((w[1] - w[0]) // iv) + 1)})
    close_l = {
        "read": bool(fine), "interval_s": fp.get("interval_s"), "cols": fp.get("cols"),
        "rows": fp.get("rows"), "width": fp.get("width"), "role": fp.get("role"),
        "model": fp.get("model"), "windows": wins, "frames": fframes,
        "frames_recorded": frec, "projected_usd": fine.get("projected_usd"),
    }
    deep_l = {
        "read": bool(deep.get("spans")),
        "spans": [{"start": r["start"], "end": r["end"],
                   "frames": [f["t"] for f in r.get("frames") or []],
                   "asked": r.get("asked") or [], "followup": bool(r.get("followup")),
                   "model": r.get("model"), "role": r.get("role"),
                   "beats": len(r.get("beats") or []),
                   "inferred": sum(1 for b in r.get("beats") or []
                                   if b.get("basis") == "inferred"),
                   "events": len(r.get("events") or []),
                   "camera": (r.get("camera") or {}).get("mount"),
                   "summary": r.get("summary", ""),
                   "projected_usd": r.get("projected_usd")}
                  for r in deep.get("spans") or []],
        "projected_usd": deep.get("projected_usd"),
        "cap": FRAME_CAP, "width": FRAME_WIDTH, "max_span_s": MAX_SPAN_S,
        "role": DEEP_ROLE,
    }
    motion_l = {"cached": bool(motion), **_downsample(list(motion or []), hz)}
    return {
        "clip": clip, "duration": duration,
        "layers": {"heard": heard, "coarse": coarse_l, "close": close_l, "deep": deep_l,
                   "motion": motion_l},
        "moments": [{"start": m["start"], "end": m["end"], "kind": m.get("kind", ""),
                     "what": m.get("what", ""), "source": m.get("source", ""),
                     "status": (m.get("why_ranked") or {}).get("confirmation", ""),
                     "score": m.get("score"), "notable": bool(m.get("notable"))}
                    for m in moments],
        "unusable": [{"start": float(u["start"]), "end": float(u["end"]),
                      "why": u.get("why", "")}
                     for u in list(coarse.get("unusable") or [])
                     + list(fine.get("unusable") or [])],
    }


def summary(cov: dict) -> dict:
    """The compact form for a grid of clip cards: spans and ticks, no motion track and no
    words — the open screen draws a mini strip per card from one request for the bin."""
    L = cov["layers"]
    return {"clip": cov["clip"], "duration": cov["duration"],
            "heard": L["heard"]["utterances"],
            "coarse": {"interval_s": L["coarse"]["interval_s"],
                       "frames": L["coarse"]["frames"]},
            "close": L["close"]["windows"],
            "deep": [[s["start"], s["end"]] for s in L["deep"]["spans"]],
            "moments": [{"start": m["start"], "end": m["end"], "kind": m["kind"],
                         "status": m["status"]} for m in cov["moments"]
                        if m["notable"] or m["source"] == "deep look"]}
