"""The sequence's tracks: V1/V2 + A1/A2 (REDESIGN-GOAL dimension 5, verdict 2 punch 3).

The cut stays a magnetic list of shots — the EDL's `segments`, in film order — and that
list IS two tracks at once: each shot is a picture on **V1** and, linked to it, the same
clip's camera sound on **A1**. A trim, a razor, a ripple delete or a move acts on the
shot, so it acts on both halves for free; that is what "linked" means here.

What a shot may carry beyond its range (all optional, absent = the default):

  link: false        the pair is unlinked (Premiere's Alt-click / Unlink): the A1 half can
                     then be moved on its own. Relinking keeps whatever offset it has, as
                     Premiere keeps an out-of-sync pair (and shows the offset).
  a_off: seconds     where the A1 half sits relative to its V1 half, in film seconds
                     (positive = later). 0 is in sync. Rounded to 0.01, |a_off| <= 600.

Around the segments, the EDL gains four keys (each absent = the default below):

  tracks:   {"V1": {"lock", "eye", "target"}, "V2": {...},
             "A1": {"lock", "mute", "solo", "target", "gain_db"},
             "A2": {"lock", "mute", "solo", "target", "gain_db"}}
            The track headers' toggles. `eye` false hides a video track from the
            render; `mute` silences an audio track; any `solo` silences every audio
            track that is not soloed; `gain_db` (-60..+12) is the track's volume;
            `lock` refuses edits on the board; `target` is where `,` / `.` put a clip.
  overlays: [{"id", "clip", "in", "out", "at", "speed"?, "why"?}]
            V2: clips laid OVER the cut at film time `at` (picture only — their sound is
            not used; the cut's sound is A1's). The render composites each one over V1,
            full frame, for its own length.
  markers:  [film seconds | {"t", "name"?, "comment"?, "colour"?, "dur"?}]
            sequence markers (the ruler's flags), sorted, one per time. A plain marker
            (no name, no comment, Green, no duration) is its bare time, as every EDL
            before verdict 12 has it; any other is an object carrying only what is not
            the default — Premiere's Marker dialog: a name, comments, a colour (Green Red
            Purple Orange Yellow White Blue Cyan) and a duration in film seconds.
  in / out: film seconds or null           the sequence's In / Out
  fps:      the sequence's timebase        fixed: from its first clip or Sequence
                                           Settings, never from the shots ("the timebase")

A2 is the music bed (`effects_music`): its mute, solo and gain apply to the bed.

Added tracks (Sequence › Add Track, verdict 4 punch 9): V3…V9 and A3…A9. A track that
exists has its entry in `tracks` (stored whole, so an untouched added track survives a
save). A clip on an added video track is an overlay with `track: "V3"` (V2 is the
absent default); the render composites the video tracks bottom to top (V2, then V3, …),
so a higher track covers a lower one, as Premiere stacks them. An added audio track's
clips are a fifth key:

  audio:    [{"id", "clip", "in", "out", "at", "track": "A3", "speed"?, "why"?}]
            each clip's own sound laid at film second `at` on its track, mixed with A1
            at the track's gain (under the bed); mute / solo as A1's.

`validate_*` raise ValueError with the sentence a 400 carries; nothing here touches a
file. `audible()` and `a1_plan()` are what the render reads.
"""

from __future__ import annotations

import math
import re
from typing import Any

VIDEO = ("V1", "V2")
AUDIO = ("A1", "A2")
NAMES = VIDEO + AUDIO
EXTRA_VIDEO = tuple(f"V{n}" for n in range(3, 10))     # Sequence › Add Track
EXTRA_AUDIO = tuple(f"A{n}" for n in range(3, 10))
EXTRA = EXTRA_VIDEO + EXTRA_AUDIO
MIN_GAIN_DB, MAX_GAIN_DB = -60.0, 12.0
MAX_OFFSET_S = 600.0
MIN_LEN_S = 0.2
MAX_MARKERS = 500
# Premiere's marker colours, in its Marker dialog's order; Green is the default
MARKER_COLOURS = ("green", "red", "purple", "orange", "yellow", "white", "blue", "cyan")
MARKER_NAME_MAX = 120
MARKER_COMMENT_MAX = 1000
MAX_MARKER_DUR_S = 24 * 3600.0
_ID = re.compile(r"^[A-Za-z0-9:_-]{1,40}$")


def default_tracks() -> dict:
    """Every track as a fresh sequence has it: visible, unlocked, audible at 0 dB; V1 and
    A1 targeted (a source clip goes to V1 with its sound on A1)."""
    return {
        "V1": {"lock": False, "eye": True, "target": True},
        "V2": {"lock": False, "eye": True, "target": False},
        "A1": {"lock": False, "mute": False, "solo": False, "target": True, "gain_db": 0.0},
        "A2": {"lock": False, "mute": False, "solo": False, "target": False, "gain_db": 0.0},
    }


def _finite(v: Any, what: str) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} is not a number")
    if not math.isfinite(x):
        raise ValueError(f"{what} is not a finite number")
    return x


def extra_default(name: str) -> dict:
    """An added track as Add Track makes it: visible / audible, unlocked, not targeted."""
    if name in EXTRA_VIDEO:
        return {"lock": False, "eye": True, "target": False}
    return {"lock": False, "mute": False, "solo": False, "target": False, "gain_db": 0.0}


def track_order(name: str) -> tuple[int, int]:
    """Video before audio, then by number: V2 under V3, A1 before A3."""
    return (0 if name[0] == "V" else 1, int(name[1:]))


def validate_tracks(raw: Any) -> dict:
    """The headers' state, complete: unknown tracks or keys are a ValueError, missing ones
    take the default. Returns only what differs from the default, so an untouched
    sequence saves no `tracks` key at all — except an added track (V3…, A3…), kept
    whole: its entry is what says it exists."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("tracks is not an object")
    base = default_tracks()
    for name, spec in raw.items():
        if name in EXTRA and spec is not None:
            base[name] = extra_default(name)
    out: dict = {}
    for name, spec in raw.items():
        if name not in base:
            raise ValueError(f"unknown track {name!r} (the tracks are {', '.join(NAMES)},"
                             " and V3–V9 / A3–A9 once added)")
        if spec is None:
            continue
        if name in EXTRA:
            out[name] = dict(base[name])
        if not isinstance(spec, dict):
            raise ValueError(f"track {name} is not an object")
        for k, v in spec.items():
            if k not in base[name]:
                raise ValueError(f"track {name} has no {k!r} (it has {', '.join(base[name])})")
            if k == "gain_db":
                g = _finite(v, f"{name}.gain_db")
                if not MIN_GAIN_DB <= g <= MAX_GAIN_DB:
                    raise ValueError(f"{name}.gain_db {g:g} is outside {MIN_GAIN_DB:g}..{MAX_GAIN_DB:g} dB")
                v = round(g, 1)
            elif not isinstance(v, bool):
                raise ValueError(f"{name}.{k} is not true or false")
            if v != base[name][k] or name in EXTRA:
                out.setdefault(name, {})[k] = v
    return out


def resolve_tracks(stored: Any) -> dict:
    """The full header state from what the EDL stores (only the differences, and every
    added track whole)."""
    full = default_tracks()
    if isinstance(stored, dict):
        for name in sorted((n for n in stored if n in EXTRA), key=track_order):
            full[name] = extra_default(name)
        for name, spec in stored.items():
            if name in full and isinstance(spec, dict):
                for k, v in spec.items():
                    if k in full[name]:
                        full[name][k] = v
    return full


def validate_link(seg_in: dict, seg_out: dict) -> None:
    """Copy a segment's `link` / `a_off` into the cleaned one, validated."""
    if "link" in seg_in and seg_in["link"] is not None:
        if not isinstance(seg_in["link"], bool):
            raise ValueError("link is not true or false")
        if seg_in["link"] is False:
            seg_out["link"] = False
    if seg_in.get("a_off") not in (None, 0, 0.0):
        off = _finite(seg_in["a_off"], "a_off")
        if abs(off) > MAX_OFFSET_S:
            raise ValueError(f"a_off {off:g} s is more than {MAX_OFFSET_S:g} s out of sync")
        off = round(off, 4)          # 4 places: frame-grid values (29.97) survive the save
        if off:
            seg_out["a_off"] = off


def validate_overlays(raw: Any, speed_validator=None) -> list[dict]:
    """V2's clips: `{id?, clip, in, out, at, speed?, why?}`, ranges in clip seconds, `at`
    in film seconds. A missing or duplicate id is minted by the caller (None here)."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("overlays is not a list")
    out, seen = [], set()
    for i, o in enumerate(raw):
        if not isinstance(o, dict):
            raise ValueError(f"overlay {i + 1} is not an object")
        clip = o.get("clip")
        if not isinstance(clip, str) or not clip or "/" in clip or ".." in clip:
            raise ValueError(f"overlay {i + 1} names no clip")
        a = round(_finite(o.get("in"), f"overlay {i + 1} in"), 4)
        b = round(_finite(o.get("out"), f"overlay {i + 1} out"), 4)
        at = round(_finite(o.get("at"), f"overlay {i + 1} at"), 4)
        if a < 0 or b - a < MIN_LEN_S - 1e-9:
            raise ValueError(f"overlay {i + 1} ({clip}) is shorter than {MIN_LEN_S} s or starts before 0")
        if at < 0:
            raise ValueError(f"overlay {i + 1} ({clip}) starts before the film does")
        clean = {"clip": clip, "in": a, "out": b, "at": at}
        trk = o.get("track")
        if trk not in (None, "", "V2"):
            if trk not in EXTRA_VIDEO:
                raise ValueError(f"overlay {i + 1} ({clip}) is on {trk!r}, not a video track")
            clean["track"] = trk
        if speed_validator is not None:
            sp = speed_validator(o.get("speed"))
            if sp is not None:
                clean["speed"] = sp
        if isinstance(o.get("why"), str) and o["why"]:
            clean["why"] = o["why"][:300]
        sid = o.get("id")
        clean["id"] = sid if isinstance(sid, str) and _ID.match(sid) and sid not in seen else None
        if clean["id"]:
            seen.add(clean["id"])
        out.append(clean)
    return out


def validate_audio_clips(raw: Any, speed_validator=None) -> list[dict]:
    """The added audio tracks' clips: `{id?, clip, in, out, at, track, speed?, why?}` —
    an overlay's shape on A3…A9. A missing or duplicate id is minted by the caller."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("audio is not a list")
    out = validate_overlays([{k: v for k, v in c.items() if k != "track"}
                             if isinstance(c, dict) else c for c in raw], speed_validator)
    for i, (c, clean) in enumerate(zip(raw, out)):
        trk = c.get("track")
        if trk not in EXTRA_AUDIO:
            raise ValueError(f"audio clip {i + 1} ({clean['clip']}) is on {trk!r}, "
                             "not an added audio track (A3–A9)")
        clean["track"] = trk
    return out


def marker_colour(v: Any) -> str:
    """A marker colour by Premiere's name, any case ("Red", "red"): one of MARKER_COLOURS."""
    c = str(v).strip().lower() if isinstance(v, str) else ""
    if c not in MARKER_COLOURS:
        raise ValueError("a marker's colour is one of "
                         + ", ".join(x.title() for x in MARKER_COLOURS))
    return c


def marker_value(m: Any) -> float | dict:
    """One marker, cleaned: its bare time (4 places) when it is a plain one, else
    `{t, name?, comment?, colour?, dur?}` with only what is not the default. Accepts
    `color` / `duration` as an op spells them."""
    if not isinstance(m, dict):
        x = round(_finite(m, "a marker"), 4)
        if x < 0:
            raise ValueError("a marker is before the film starts")
        return x
    t = round(_finite(m.get("t"), "a marker"), 4)
    if t < 0:
        raise ValueError("a marker is before the film starts")
    out: dict = {"t": t}
    name = m.get("name")
    if name is not None:
        if not isinstance(name, str):
            raise ValueError("a marker's name is not text")
        name = " ".join(name.split())
        if len(name) > MARKER_NAME_MAX:
            raise ValueError(f"a marker's name is longer than {MARKER_NAME_MAX} characters")
        if name:
            out["name"] = name
    comment = m.get("comment")
    if comment is not None:
        if not isinstance(comment, str):
            raise ValueError("a marker's comment is not text")
        comment = comment.strip()
        if len(comment) > MARKER_COMMENT_MAX:
            raise ValueError(f"a marker's comment is longer than {MARKER_COMMENT_MAX} characters")
        if comment:
            out["comment"] = comment
    colour = m.get("colour", m.get("color"))
    if colour is not None:
        c = marker_colour(colour)
        if c != "green":
            out["colour"] = c
    d = m.get("dur", m.get("duration"))
    if d is not None:
        d = round(_finite(d, "a marker's duration"), 4)
        if d < 0 or d > MAX_MARKER_DUR_S:
            raise ValueError("a marker's duration is 0 or more film seconds, under a day")
        if d > 0:
            out["dur"] = d
    return out if len(out) > 1 else t


def marker_time(m: Any) -> float | None:
    """A marker's time from either shape (None when it has none)."""
    v = m.get("t") if isinstance(m, dict) else m
    if isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def validate_markers(raw: Any) -> list[float | dict]:
    """Sequence markers, sorted and de-duplicated to the hundred-thousandth: a plain one
    its bare time, a named / commented / coloured / long one an object (`marker_value`).
    At one time a marker with a name, comment, colour or duration is never lost to a bare
    duplicate; otherwise the later one wins."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("markers is not a list")
    if len(raw) > MAX_MARKERS:
        raise ValueError(f"more than {MAX_MARKERS} markers")
    vals: dict[float, float | dict] = {}
    for m in raw:
        v = marker_value(m)
        t = v["t"] if isinstance(v, dict) else v
        if isinstance(vals.get(t), dict) and not isinstance(v, dict):
            continue
        vals[t] = v
    return [vals[t] for t in sorted(vals)]


def validate_point(v: Any, what: str) -> float | None:
    if v is None:
        return None
    x = round(_finite(v, what), 4)
    if x < 0:
        raise ValueError(f"{what} is before the film starts")
    return x


# ---------------------------------------------------------------- the timebase
#
# Verdict 10, punch 3: "the sequence's frame rate is derived from its shots and flips
# 29.97 ↔ 59.94 as you edit". A Premiere sequence has a timebase, fixed in Sequence
# Settings: a new sequence takes its first clip's ("New Sequence From Clip"), and adding
# or removing shots never changes it. The EDL keeps it as `fps`: frames per second to 3
# places (29.97 is 30000/1001, 59.94 is 60000/1001, 23.976 is 24000/1001 — the grid's
# exact rates). Absent only while a sequence has never held a clip; an EDL from before
# the key gets one once, when it is opened (server.ensure_timebase), the rate it was
# showing then. Sequence Settings offers Premiere's common timebases; a sequence made
# from a clip of another rate (a 119.88 phone clip) keeps that one.

TIMEBASES = (23.976, 24.0, 25.0, 29.97, 30.0, 50.0, 59.94, 60.0)
MIN_RATE, MAX_RATE = 1.0, 240.0


def timebase_value(v: Any) -> float | None:
    """A frame rate as the EDL keeps it — 30000/1001, 29.97002997 and "29.97" are all
    29.97 — or None when `v` is not one (missing, nonsense, outside 1–240)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        text = str(v).strip()
        if "/" in text:
            num, _, den = text.partition("/")
            x = float(num) / float(den)
        else:
            x = float(text)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if not math.isfinite(x) or not (MIN_RATE <= x <= MAX_RATE):
        return None
    for t in TIMEBASES:
        if abs(x - t) < 0.005:
            return t
    return round(x, 3)


def validate_timebase(raw: Any) -> float | None:
    """A save's `fps` (Sequence › Sequence Settings…): None leaves the stored timebase as
    it is — a sequence never loses its rate — else the rate, or a ValueError."""
    if raw is None:
        return None
    v = timebase_value(raw)
    if v is None:
        raise ValueError(f"fps {raw!r} is not a frame rate ({MIN_RATE:g}–{MAX_RATE:g})")
    return v


def exact_rate(fps: Any) -> float | None:
    """The timebase as the frame grid counts it: 29.97 → 30000/1001, 25 → 25."""
    v = timebase_value(fps)
    if v is None:
        return None
    n = round(v * 1.001)
    if abs(v - round(v)) > 0.004 and abs(v - n / 1.001) < 0.004:
        return n / 1.001
    return v


# ---------------------------------------------------------------- what the render reads

def audible(tracks: dict, name: str) -> bool:
    """Whether an audio track is heard: not muted, and — when any track is soloed — soloed."""
    t = resolve_tracks(tracks)
    if name not in t or t[name]["mute"]:
        return False
    if any(t[a]["solo"] for a in t if a[0] == "A"):
        return bool(t[name]["solo"])
    return True


def visible(tracks: dict, name: str) -> bool:
    t = resolve_tracks(tracks)
    return name in t and bool(t[name]["eye"])


def video_layers(tracks: dict, overlays: list[dict]) -> list[dict]:
    """The overlays the render composites, bottom to top: by track (V2, V3, …), then by
    film time; a clip on a hidden track or on a track that does not exist is left out."""
    t = resolve_tracks(tracks)
    keep = [o for o in overlays or []
            if (o.get("track") or "V2") in t and t[o.get("track") or "V2"].get("eye", True)]
    return sorted(keep, key=lambda o: (track_order(o.get("track") or "V2"), float(o["at"])))


def audio_layers(tracks: dict, clips: list[dict]) -> list[tuple[dict, float]]:
    """The added audio tracks' clips the render mixes, each with its track's gain in dB:
    clips on a muted (or soloed-out) track, or a track that does not exist, are left out."""
    t = resolve_tracks(tracks)
    out = []
    for c in clips or []:
        name = c.get("track")
        if name in t and audible(tracks, name):
            out.append((c, float(t[name].get("gain_db") or 0.0)))
    return out


def a_off(seg: dict) -> float:
    try:
        x = float(seg.get("a_off") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return x if math.isfinite(x) else 0.0


def a1_plan(segments: list[dict], tracks: dict, dur) -> dict:
    """How A1 is laid: `{"needed": bool, "audible": bool, "gain_db": float,
    "placements": [(start_film_s, trim_head_s)]}` — one per segment, the film second its
    sound starts at and how much of its head falls before 0 (a negative offset on the first
    shot). `needed` is False when A1 is exactly the concatenation of the parts (in sync,
    audible, 0 dB): the render then leaves the audio as it was cut."""
    t = resolve_tracks(tracks)
    gain = float(t["A1"]["gain_db"] or 0.0)
    hear = audible(tracks, "A1")
    placements, start, moved = [], 0.0, False
    for s in segments:
        off = a_off(s)
        if abs(off) > 1e-9:
            moved = True
        at = start + off
        placements.append((max(0.0, at), max(0.0, -at)))
        start += dur(s)
    return {"needed": (not hear) or abs(gain) > 1e-9 or moved,
            "audible": hear, "gain_db": gain, "placements": placements, "total": start}


def a1_filter(n_parts: int, plan: dict, first_input: int = 1) -> str:
    """The `-filter_complex` that lays A1 from the cut parts (inputs `first_input` …):
    each part's own audio (already levelled, retimed and carrying its effects' sounds)
    trimmed of any head before 0, delayed to its film second, all mixed without
    normalising, the track's gain applied, padded and cut to the film's length.
    Output label `[a1]`."""
    total = max(0.01, plan["total"])
    chains, labels = [], []
    for k in range(n_parts):
        at, head = plan["placements"][k]
        ms = int(round(at * 1000))
        c = f"[{first_input + k}:a]"
        if head > 0:
            c += f"atrim=start={head:.3f},asetpts=PTS-STARTPTS,"
        c += f"adelay={ms}|{ms}[p{k}]"
        chains.append(c)
        labels.append(f"[p{k}]")
    mix = (f"{''.join(labels)}amix=inputs={n_parts}:normalize=0:dropout_transition=0"
           if n_parts > 1 else f"{labels[0]}anull")
    gain = plan["gain_db"]
    tail = f",volume={gain:.2f}dB" if abs(gain) > 1e-9 else ""
    chains.append(f"{mix}{tail},apad,atrim=0:{total:.3f},aresample=48000[a1]")
    return ";".join(chains)


# ---------------------------------------------------------------- transitions
#
# Premiere's transitions at an edit point (verdict 6 punch 6; the Effects bin: verdict 8
# punch 5). The EDL's
#
#   transitions: [{"a": id, "b": id, "kind": <kind>, "dur": seconds, "align": "center"}]
#
# names the edit by the two shots on either side of it (`a` right before `b` in
# `segments`), so a transition stays on its cut through a trim, a split elsewhere or an
# insert elsewhere, and leaves with it when either shot is deleted or the two are no
# longer neighbours (Premiere deletes a transition whose clips are moved apart).
# Centre-aligned: the transition spans dur/2 of film either side of the cut and the
# film's length does not change, so a dissolve eats dur/2 of each shot's *handles* —
# media past the outgoing shot's out and before the incoming shot's in. Where a shot has
# less than that the transition keeps its length and the missing frames are REPEATED
# (the outgoing shot's last frame, the incoming shot's first), as Premiere does with its
# "insufficient media — repeated frames" warning (`transition_halves` names them), and
# the box is drawn hatched.
#
# One-sided (a clip's start or end, Premiere's single-sided transition):
#
#   {"a": null, "b": id, "kind": ..., "dur": s, "align": "start"}   the first dur of b
#   {"a": id, "b": null, "kind": ..., "dur": s, "align": "end"}     the last dur of a
#
# lies inside the shot and needs no handle: a video kind fades the picture from (start)
# or to (end) black — white for Dip to White — and an audio kind fades the sound in or
# out along its curve.
#
# One video and one audio transition per edge: a centred one holds the end of `a` and the
# start of `b`, a one-sided one its own edge; a later one on an edge replaces the earlier.
#
# The kinds and their maths (x the incoming shot's weight across the window: 0 at its
# first frame, exactly 0.5 on the edit's frame, `transition_x`):
#   cross_dissolve   out = (1 − x)·A + x·B, per channel (Premiere's Cross Dissolve)
#   film_dissolve    the same mix in linear light: out = g⁻¹((1 − x)·g(A) + x·g(B)),
#                    g(v) = v^2.2 on R'G'B' in 0-1 (Premiere's Film Dissolve)
#   dip_to_black     x < ½: out = A + 2x·(K − A); x ≥ ½: out = K + (2x − 1)·(B − K),
#   dip_to_white     K black (Y' 16, Cb Cr 128) or white (Y' 235): K alone on the edit's frame
#   audio_crossfade  Constant Power: A at cos(πx/2), B at sin(πx/2) (−3 dB each at ½)
#   constant_gain    Constant Gain: A at 1 − x, B at x (−6.02 dB each at ½)
#   exponential_fade Exponential Fade: B at e(x) = (100^x − 1)/99, A at e(1 − x)
#                    (−20.8 dB each at ½)

VIDEO_KINDS = {"cross_dissolve": "Cross Dissolve", "dip_to_black": "Dip to Black",
               "dip_to_white": "Dip to White", "film_dissolve": "Film Dissolve"}
AUDIO_KINDS = {"audio_crossfade": "Constant Power", "constant_gain": "Constant Gain",
               "exponential_fade": "Exponential Fade"}
TRANSITION_KINDS = {**VIDEO_KINDS, **AUDIO_KINDS}
DIP_KINDS = ("dip_to_black", "dip_to_white")      # no handle: each shot fades to K itself
TRANSITION_DEFAULT_S = 1.0          # Premiere's default duration
TRANSITION_MIN_S = 0.04
TRANSITION_MAX_S = 10.0
FILM_GAMMA = 2.2
EXP_BASE = 100.0


def family(kind: str) -> str:
    """'video' for a picture transition, 'audio' for a sound one."""
    return "video" if kind in VIDEO_KINDS else "audio"


def audio_gains(kind: str, x: float) -> tuple[float, float]:
    """(outgoing, incoming) amplitude at x for an audio kind (the maths above)."""
    x = min(1.0, max(0.0, float(x)))
    if kind == "constant_gain":
        return 1.0 - x, x
    if kind == "exponential_fade":
        e = lambda u: (EXP_BASE ** u - 1.0) / (EXP_BASE - 1.0)  # noqa: E731
        return e(1.0 - x), e(x)
    return math.cos(math.pi / 2 * x), math.sin(math.pi / 2 * x)


def _slots(t: dict) -> set:
    if t["align"] == "start":
        return {("start", t["b"])}
    if t["align"] == "end":
        return {("end", t["a"])}
    return {("end", t["a"]), ("start", t["b"])}


def validate_transitions(raw: Any, segments: list[dict], ids: dict | None = None,
                         dur=None) -> list[dict]:
    """The transitions a save keeps: each `{a, b, kind, dur, align?}` validated (a bad
    kind, duration or alignment raises ValueError — the 400's sentence), its shot ids
    renamed through `ids` (a save's `{tmp-N: minted}`), a centred one kept only while `a`
    is right before `b` in `segments` (a pair a reorder or a delete split is dropped, not
    an error), a one-sided one while its shot is in the cut. A later transition on an
    edge of the same family (video / audio) replaces an earlier one. With `dur` (the film
    length of a shot), a centred one is no longer than twice the shorter shot and a
    one-sided one no longer than its shot."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("transitions is not a list")
    ids = ids or {}
    order = {s.get("id"): k for k, s in enumerate(segments) if isinstance(s, dict)}
    kept: list[dict] = []
    for i, t in enumerate(raw):
        if not isinstance(t, dict):
            raise ValueError(f"transition {i + 1} is not an object")
        kind = t.get("kind")
        if kind not in TRANSITION_KINDS:
            raise ValueError(f"transition {i + 1}: kind {kind!r} is not one of "
                             + ", ".join(TRANSITION_KINDS))
        d = _finite(t.get("dur", TRANSITION_DEFAULT_S), f"transition {i + 1} duration")
        if not TRANSITION_MIN_S - 1e-9 <= d <= TRANSITION_MAX_S + 1e-9:
            raise ValueError(f"transition {i + 1}: {d:g} s is outside "
                             f"{TRANSITION_MIN_S:g}–{TRANSITION_MAX_S:g} s")
        a, b = t.get("a"), t.get("b")
        a, b = ids.get(a, a), ids.get(b, b)
        sided = {(True, True): "center", (False, True): "start", (True, False): "end"}.get(
            (isinstance(a, str), isinstance(b, str)))
        if sided is None or a is not None and not isinstance(a, str) \
                or b is not None and not isinstance(b, str):
            raise ValueError(f"transition {i + 1} does not name the two shots of its edit "
                             "(or one shot, for a clip's start or end)")
        align = t.get("align") or sided
        if align != sided:
            raise ValueError(f"transition {i + 1}: align {align!r} does not fit it — "
                             "'center' between two shots, 'start' with only b, 'end' with "
                             "only a")
        ka, kb = order.get(a), order.get(b)
        if sided == "center":
            if ka is None or kb is None or kb != ka + 1:
                continue                              # no longer an edit: it went with it
            if dur is not None:
                d = min(d, 2 * dur(segments[ka]), 2 * dur(segments[kb]))
        else:
            k = kb if sided == "start" else ka
            if k is None:
                continue                              # its shot left the cut
            if dur is not None:
                d = min(d, dur(segments[k]))
        new = {"a": a, "b": b, "kind": kind, "dur": round(max(TRANSITION_MIN_S, d), 4),
               "align": sided}
        fam, slots = family(kind), _slots(new)
        kept = [x for x in kept if family(x["kind"]) != fam or not (_slots(x) & slots)]
        kept.append(new)

    def pos(t: dict) -> tuple:
        k = order[t["a"]] + 1 if t["a"] is not None else order[t["b"]]
        return (k, {"end": 0, "center": 1, "start": 2}[t["align"]], t["kind"])
    return sorted(kept, key=pos)


def prune_transitions(transitions: Any, segments: list[dict]) -> list[dict]:
    """What is left of stored transitions after the cut changed under them (a save that
    sent no `transitions`, an accepted proposal): only the ones still on an edit."""
    try:
        return validate_transitions(transitions or [], segments)
    except ValueError:
        return []


def transition_x(f: float, cut: float, half: float) -> float:
    """The incoming shot's weight at film time (or frame) f of a centred window of
    half-length `half` around `cut`: 0 at its start, ½ on the cut, 1 at its end."""
    return min(1.0, max(0.0, (f - (cut - half)) / (2 * half))) if half > 0 else 1.0


def _edge_has(transitions: list[dict], seg_id: str, edge: str, fam: str) -> bool:
    for t in transitions or []:
        if family(t.get("kind", "")) != fam:
            continue
        al = t.get("align", "center")
        if edge == "start" and t.get("b") == seg_id and al in ("center", "start"):
            return True
        if edge == "end" and t.get("a") == seg_id and al in ("center", "end"):
            return True
    return False


def transition_halves(segments: list[dict], transitions: list[dict], dur,
                      clip_len) -> list[dict]:
    """Per edit (between segments k and k+1, k = 0 … n-2): `{"video": h, "audio": h,
    "kinds": {"video": kind, "audio": kind}, "asked": {kind: dur}, "short": [kind, …],
    "repeat": {kind: {"a": s, "b": s}}}` — h the half-duration in film seconds the render
    and the Program use, 0 for none. A half is cut down only to what each shot has left
    beside the transition at its other edge (half its film length when there is one, all
    of it otherwise): `short` names those. Media is never a limit: where the outgoing
    shot has less than h past its out (`clip_len(clip) - out`, at its speed) or the
    incoming less than h before its in, `repeat[kind]` says how many film seconds of each
    side are its last / first frame repeated (Premiere's "insufficient media — repeated
    frames"). A dip needs no handle, so it never repeats."""
    order = {s.get("id"): k for k, s in enumerate(segments)}
    cuts = [{"video": 0.0, "audio": 0.0, "kinds": {}, "asked": {}, "short": [], "repeat": {}}
            for _ in range(max(0, len(segments) - 1))]
    for t in transitions or []:
        if t.get("align", "center") != "center":
            continue
        k = order.get(t.get("a"))
        if k is None or k + 1 >= len(segments) or segments[k + 1].get("id") != t.get("b"):
            continue
        fam = family(t["kind"])
        cuts[k]["asked"][t["kind"]] = float(t["dur"])
        cuts[k]["kinds"][fam] = t["kind"]
        cuts[k][fam] = float(t["dur"]) / 2
    for k, c in enumerate(cuts):
        a, b = segments[k], segments[k + 1]
        sa, sb = speed(a), speed(b)
        la = clip_len(a["clip"])
        tail = max(0.0, (la - float(a["out"])) / sa) if la is not None else 0.0
        head = max(0.0, float(b["in"]) / sb)
        for fam in ("video", "audio"):
            h = c[fam]
            kind = c["kinds"].get(fam)
            if h <= 0 or kind is None:
                continue
            # a shot shares itself between the transitions at its two edges
            room_a = dur(a) / (2 if _edge_has(transitions, a.get("id"), "start", fam) else 1)
            room_b = dur(b) / (2 if _edge_has(transitions, b.get("id"), "end", fam) else 1)
            got = max(0.0, min(h, room_a, room_b))
            if got < h - 1e-6:
                c["short"].append(kind)
            c[fam] = got
            if kind not in DIP_KINDS:
                rep_a, rep_b = max(0.0, got - tail), max(0.0, got - head)
                if rep_a > 1e-6 or rep_b > 1e-6:
                    c["repeat"][kind] = {"a": round(rep_a, 6), "b": round(rep_b, 6)}
    return cuts


def transition_fades(segments: list[dict], transitions: list[dict], dur) -> list[dict]:
    """The one-sided transitions, per segment k: `{"start": {fam: (kind, s)}, "end":
    {fam: (kind, s)}}` — s the film seconds the fade lasts at the shot's start or end,
    cut down to the shot (half of it when its other edge has one of the family too)."""
    order = {s.get("id"): k for k, s in enumerate(segments)}
    out = [{"start": {}, "end": {}} for _ in segments]
    for t in transitions or []:
        al = t.get("align")
        if al not in ("start", "end"):
            continue
        sid = t.get("b") if al == "start" else t.get("a")
        k = order.get(sid)
        if k is None:
            continue
        fam = family(t["kind"])
        other = "end" if al == "start" else "start"
        room = dur(segments[k]) / (2 if _edge_has(transitions, sid, other, fam) else 1)
        out[k][al][fam] = (t["kind"], max(0.0, min(float(t["dur"]), room)))
    return out


def speed(seg: dict) -> float:
    try:
        s = float(seg.get("speed") or 1.0)
    except (TypeError, ValueError):
        return 1.0
    return s if 0.1 <= s <= 4 else 1.0
