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
  markers:  [film seconds]                 sequence markers (the ruler's flags)
  in / out: film seconds or null           the sequence's In / Out

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


def validate_markers(raw: Any) -> list[float]:
    """Sequence markers: film seconds, >= 0, sorted, de-duplicated to the hundredth.
    Accepts `{t: …}` objects as well as numbers (the client's older shape)."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("markers is not a list")
    if len(raw) > MAX_MARKERS:
        raise ValueError(f"more than {MAX_MARKERS} markers")
    vals = set()
    for m in raw:
        t = m.get("t") if isinstance(m, dict) else m
        x = round(_finite(t, "a marker"), 4)
        if x < 0:
            raise ValueError("a marker is before the film starts")
        vals.add(x)
    return sorted(vals)


def validate_point(v: Any, what: str) -> float | None:
    if v is None:
        return None
    x = round(_finite(v, what), 4)
    if x < 0:
        raise ValueError(f"{what} is before the film starts")
    return x


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
