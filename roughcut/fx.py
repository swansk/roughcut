"""Effects the model designs and the renderer draws (INTAKE M12).

Karl, 2026-09-20: *"I have a clip that shows me hitting rocks with my skis, and I tell the
AI 'Add call of duty hit markers where my skis are with the sound effect'. AI then goes
and adds separate overlaid video with the effect (which it also generates itself) and
the audio. Human can iterate with the AI to make it better, but AI also tests / verifies
that the DoD is complete … Also include options for human to draw references on a
keyframe."*

The four rules of docs/EFFECTS.md still govern this module, with one of them turned
inside out:

- **Rule 1 holds: the model never writes ffmpeg.** It writes an *effect* — a closed,
  validated vocabulary — and this module owns every string that reaches ffmpeg.
- **Rule 2 holds: nothing here is in pixels.** Anchors are fractions of the frame,
  sizes are fractions of the frame width, times are seconds. The same effect draws the
  same way on the 720p proxy in the monitor and on the 4K master.
- **Rule 4 is turned inside out: the asset is generated, not fetched.** The old design
  said the model cannot invent a hitmarker PNG. It can invent a hitmarker *program*:
  the overlay is a small procedural sprite (shapes in a unit box plus keyframed scale /
  opacity / rotation / offset) and the sound is a small procedural synth patch (tone,
  noise, click and sweep layers with envelopes). Both are drawn by code in this module
  — PIL and numpy on the render side, a 2D canvas and WebAudio in the monitor — from
  one JSON. Nothing is downloaded, nothing needs a licence, and a "make it red and
  bigger" is a change to numbers, not a new file.
- **Rule 3 holds: precision comes from narrowing.** The note and the transcript locate
  the window; the onset track (10 Hz, already in every audio sidecar) finds the
  impacts inside it (`onset_peaks`); a strip of frames around each peak lets a
  *priced* model call place the anchor on the skis (`place`); the human nudges. A
  reference the human draws on a frame is the strongest signal of all: its marks
  become the anchors directly, no call needed, and the model only designs the look.

An effect is keyed to a **shot** (segment id) and its events sit at **clip seconds**,
like witnesses: a rock strike is a moment in the footage, and trimming the shot's
in-point does not move it. At render an event outside the shot's range is simply not
drawn; a shot that leaves the cut takes its effects with it (they stay in the EDL,
inert, and the board says so).

Verification is a checklist the machine runs, not a claim: every event inside the
shot, every anchor inside the frame, the sound and the overlay starting together,
each event on an onset peak (when the note names an impact), and — after a proof
render of the one shot on the proxy — a measured transient in the part's audio and a
measured change in the part's picture at each event. A priced sixth check asks the
model whether the marker sits on the skis.

This file is the contract between the server (`/api/fx`, the lead), the render lane
(`render_overlay_*`, `synth_sound`, `part_graph`, `verify` — lane `agent/fx-core`) and
the board (`/fx.js` draws the same spec on the monitor — lane `agent/fx-ui`).
Validation and the pure helpers are complete here; the functions marked *lane* raise
until their lane lands.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
import uuid
import wave
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from roughcut import edits as editsmod

# ---------------------------------------------------------------- the vocabulary

SHAPES = ("line", "circle", "ring", "rect", "polygon", "text")
TRACKS = ("scale", "opacity", "rotate", "dx", "dy")
LAYERS = ("tone", "noise", "click", "sweep")
WAVES = ("sine", "square", "saw", "triangle")
NOISES = ("white", "pink")
STATUSES = ("proposed", "accepted", "removed")   # removed: out of the cut, kept for Restore

MAX_EVENTS = 24
MAX_SHAPES = 24
MAX_LAYERS = 6
MAX_KEYS = 16                # keyframes per track
MAX_TEXT = 80                    # with line breaks: a title of up to four lines
MAX_TEXT_LINES = 4
REVEALS = ("typewriter", "fade")
MAX_REPEAT = 400
MIN_DURATION, MAX_DURATION = 0.05, 30.0         # a tick, or a title that holds
MIN_SIZE, MAX_SIZE = 0.02, 1.6          # of the frame width; 1.6 covers the frame
MIN_GAIN_DB, MAX_GAIN_DB = -30.0, 6.0
NUDGE_S = 1 / 24                        # one frame at 24 fps — the human's step
ONSET_HZ = 10                           # the audio sidecar's frame_hz
ONSET_TOL_S = 0.15                      # "on an onset peak" — 100 ms resolution + one hop

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def new_id() -> str:
    return "fx_" + uuid.uuid4().hex[:8]


# ---------------------------------------------------------------- validation

def _num(v: Any, name: str, lo: float, hi: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} is not a number: {v!r}")
    if not (lo <= f <= hi):
        raise ValueError(f"{name} out of range: {f} (allowed {lo}–{hi})")
    return round(f, 4)


def _point(v: Any, name: str, lo: float = -1.5, hi: float = 1.5) -> list[float]:
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise ValueError(f"{name} is not an [x, y] pair: {v!r}")
    return [_num(v[0], f"{name}[0]", lo, hi), _num(v[1], f"{name}[1]", lo, hi)]


def _colour(v: Any, name: str) -> str:
    if not isinstance(v, str) or not _HEX.match(v):
        raise ValueError(f"{name} is not a #rrggbb colour: {v!r}")
    return v.lower()


def validate_shape(raw: Any) -> dict:
    """One shape in the sprite's unit box: x and y run -1..1 across the box (the box is
    `size` × frame width on screen, square), so a shape may poke a little past it
    (±1.5) but not draw across the frame."""
    if not isinstance(raw, dict):
        raise ValueError(f"shape is not an object: {raw!r}")
    kind = raw.get("type")
    if kind not in SHAPES:
        raise ValueError(f"unknown shape type {kind!r} (allowed {', '.join(SHAPES)})")
    out: dict[str, Any] = {"type": kind,
                           "color": _colour(raw.get("color", "#ffffff"), "shape.color"),
                           "opacity": _num(raw.get("opacity", 1.0), "shape.opacity", 0, 1)}
    # when the shape is there, inside the effect: from `start`, to `end` (or the effect's
    # end), with a `fade` in and out of that many seconds — a title's lines can arrive
    # one after another, a bar can leave before the text does
    start = _num(raw.get("start", 0.0), "shape.start", 0, MAX_DURATION)
    if start:
        out["start"] = start
    if raw.get("end") is not None:
        end = _num(raw["end"], "shape.end", 0, MAX_DURATION)
        if end <= start:
            raise ValueError(f"shape.end {end} is not after its start {start}")
        out["end"] = end
    fade = _num(raw.get("fade", 0.0), "shape.fade", 0, 5.0)
    if fade:
        out["fade"] = fade
    if kind in ("line", "ring", "circle", "rect", "polygon"):
        out["width"] = _num(raw.get("width", 0.08), "shape.width", 0, 0.5)
    if kind in ("circle", "rect", "polygon"):
        out["fill"] = bool(raw.get("fill", kind == "circle"))
    if kind == "line":
        out["from"] = _point(raw.get("from"), "line.from")
        out["to"] = _point(raw.get("to"), "line.to")
    elif kind in ("circle", "ring"):
        out["x"], out["y"] = _point(raw.get("at", [0, 0]), "circle.at")
        out["r"] = _num(raw.get("r", 0.5), "circle.r", 0.01, 1.5)
        if kind == "ring":
            out["r2"] = _num(raw.get("r2", out["r"] * 0.7), "ring.r2", 0.0, out["r"])
    elif kind == "rect":
        out["x"], out["y"] = _point(raw.get("at", [0, 0]), "rect.at")
        out["w"] = _num(raw.get("w", 1.0), "rect.w", 0.01, 3.0)
        out["h"] = _num(raw.get("h", 1.0), "rect.h", 0.01, 3.0)
        out["rotate"] = _num(raw.get("rotate", 0.0), "rect.rotate", -360, 360)
    elif kind == "polygon":
        pts = raw.get("points")
        if not isinstance(pts, list) or not (3 <= len(pts) <= 32):
            raise ValueError("polygon.points needs 3–32 points")
        out["points"] = [_point(p, "polygon.point") for p in pts]
    elif kind == "text":
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
            raise ValueError(f"text.text must be 1–{MAX_TEXT} characters")
        lines = [l.strip() for l in text.replace("\r", "").split("\n")]
        lines = [l for l in lines if l]
        if not lines or len(lines) > MAX_TEXT_LINES:
            raise ValueError(f"text.text needs 1–{MAX_TEXT_LINES} lines")
        out["text"] = "\n".join(lines)
        out["x"], out["y"] = _point(raw.get("at", [0, 0]), "text.at")
        # the cap height of one line as a fraction of the BOX side — the same on the
        # master and on the monitor (the two used to disagree by a factor of two)
        out["h"] = _num(raw.get("h", 0.2), "text.h", 0.02, 1.0)
        out["bold"] = bool(raw.get("bold", True))
        out["fit"] = bool(raw.get("fit", True))            # shrink to the box's width
        reveal = raw.get("reveal")
        if reveal is not None:
            if reveal not in REVEALS:
                raise ValueError(f"unknown text.reveal {reveal!r} (allowed {', '.join(REVEALS)})")
            out["reveal"] = reveal
            out["cps"] = _num(raw.get("cps", 14), "text.cps", 2, 60)
    return out


def shape_alpha(shape: dict, t: float, duration: float) -> float:
    """How present a shape is at `t` seconds into the effect: 0 before `start` and
    after `end`, ramping over `fade` at both ends, 1 between."""
    start = float(shape.get("start", 0.0))
    end = float(shape.get("end", duration))
    if t < start or t > end:
        return 0.0
    fade = float(shape.get("fade", 0.0))
    if fade <= 0:
        return 1.0
    return max(0.0, min(1.0, (t - start) / fade, (end - t) / fade))


def text_at(shape: dict, t: float) -> tuple[str, float]:
    """The text a shape shows at `t` and an extra alpha: a `typewriter` reveal shows
    the first `cps × (t − start)` characters (a line break counts as one and lands
    the next line), a `fade` reveal ramps alpha over the first 0.4 s; else the whole
    text at alpha 1."""
    text = str(shape.get("text") or "")
    reveal = shape.get("reveal")
    since = t - float(shape.get("start", 0.0))
    if reveal == "typewriter":
        n = int(max(0.0, since) * float(shape.get("cps", 14)))
        return text[:n], 1.0
    if reveal == "fade":
        return text, max(0.0, min(1.0, since / 0.4))
    return text, 1.0


def validate_track(raw: Any, name: str, duration: float, lo: float, hi: float) -> list[list[float]]:
    """Keyframes `[[t, value], …]` sorted by t within 0..duration; 1–MAX_KEYS of them."""
    if not isinstance(raw, list) or not (1 <= len(raw) <= MAX_KEYS):
        raise ValueError(f"anim.{name} needs 1–{MAX_KEYS} keyframes")
    keys = []
    for k in raw:
        if not isinstance(k, (list, tuple)) or len(k) != 2:
            raise ValueError(f"anim.{name} keyframe is not [t, value]: {k!r}")
        keys.append([_num(k[0], f"anim.{name}.t", 0, duration), _num(k[1], f"anim.{name}.v", lo, hi)])
    keys.sort(key=lambda k: k[0])
    return keys


_TRACK_RANGE = {"scale": (0.0, 6.0), "opacity": (0.0, 1.0), "rotate": (-1440.0, 1440.0),
                "dx": (-1.0, 1.0), "dy": (-1.0, 1.0)}


def validate_overlay(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("overlay is not an object")
    duration = _num(raw.get("duration", 0.35), "overlay.duration", MIN_DURATION, MAX_DURATION)
    shapes = raw.get("shapes")
    if not isinstance(shapes, list) or not (1 <= len(shapes) <= MAX_SHAPES):
        raise ValueError(f"overlay.shapes needs 1–{MAX_SHAPES} shapes")
    out = {"duration": duration,
           "size": _num(raw.get("size", 0.12), "overlay.size", MIN_SIZE, MAX_SIZE),
           "shapes": [validate_shape(s) for s in shapes],
           "anim": {}}
    anim = raw.get("anim") or {}
    if not isinstance(anim, dict):
        raise ValueError("overlay.anim is not an object")
    for name, track in anim.items():
        if name not in TRACKS:
            raise ValueError(f"unknown anim track {name!r} (allowed {', '.join(TRACKS)})")
        lo, hi = _TRACK_RANGE[name]
        out["anim"][name] = validate_track(track, name, duration, lo, hi)
    if "flash" in raw and raw["flash"] is not None:
        # a whole-frame tint for a few frames: the hit's "sting"
        f = raw["flash"]
        if not isinstance(f, dict):
            raise ValueError("overlay.flash is not an object")
        out["flash"] = {"color": _colour(f.get("color", "#ff0000"), "flash.color"),
                        "opacity": _num(f.get("opacity", 0.15), "flash.opacity", 0, 0.6),
                        "duration": _num(f.get("duration", 0.08), "flash.duration", 0.02, MAX_DURATION)}
    return out


def validate_layer(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"sound layer is not an object: {raw!r}")
    kind = raw.get("type")
    if kind not in LAYERS:
        raise ValueError(f"unknown sound layer {kind!r} (allowed {', '.join(LAYERS)})")
    out: dict[str, Any] = {"type": kind,
                           "gain": _num(raw.get("gain", 0.6), "layer.gain", 0, 1),
                           "attack": _num(raw.get("attack", 0.002), "layer.attack", 0, 1),
                           "decay": _num(raw.get("decay", 0.12), "layer.decay", 0.005, 3.0)}
    if kind in ("tone", "sweep"):
        out["freq"] = _num(raw.get("freq", 1200), "layer.freq", 20, 12000)
        wave = raw.get("wave", "sine")
        if wave not in WAVES:
            raise ValueError(f"unknown wave {wave!r} (allowed {', '.join(WAVES)})")
        out["wave"] = wave
        if kind == "sweep":
            out["freq_end"] = _num(raw.get("freq_end", out["freq"] / 4), "layer.freq_end", 20, 12000)
    elif kind == "noise":
        colour = raw.get("color", "white")
        if colour not in NOISES:
            raise ValueError(f"unknown noise colour {colour!r} (allowed {', '.join(NOISES)})")
        out["color"] = colour
    for f in ("hp", "lp"):
        if f in raw and raw[f] is not None:
            out[f] = _num(raw[f], f"layer.{f}", 20, 20000)
    return out


def validate_sound(raw: Any) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("sound is not an object")
    layers = raw.get("layers")
    if not isinstance(layers, list) or not (1 <= len(layers) <= MAX_LAYERS):
        raise ValueError(f"sound.layers needs 1–{MAX_LAYERS} layers")
    out = {"duration": _num(raw.get("duration", 0.18), "sound.duration", MIN_DURATION, MAX_DURATION),
           "gain_db": _num(raw.get("gain_db", -6), "sound.gain_db", MIN_GAIN_DB, MAX_GAIN_DB),
           "layers": [validate_layer(l) for l in layers]}
    rep = raw.get("repeat")
    if rep is not None:
        # the same hit again and again — a typewriter's clatter, a heartbeat, a
        # ticking — `every` seconds apart, `count` times, with a little `jitter`
        # (a fraction of `every`) and a gain that runs to `gain_end` by the last one
        if not isinstance(rep, dict):
            raise ValueError("sound.repeat is not an object")
        every = _num(rep.get("every", 0.08), "repeat.every", 0.02, 2.0)
        count = int(_num(rep.get("count", 10), "repeat.count", 1, MAX_REPEAT))
        out["repeat"] = {"every": every, "count": count,
                         "jitter": _num(rep.get("jitter", 0.15), "repeat.jitter", 0, 1),
                         "gain_end": _num(rep.get("gain_end", 1.0), "repeat.gain_end", 0, 1)}
        tail = max(l["attack"] + l["decay"] for l in out["layers"])
        out["duration"] = round(min(MAX_DURATION, every * (count - 1) + tail + 0.05), 3)
    return out


def validate_events(raw: Any, clip_duration: float | None) -> list[dict]:
    if not isinstance(raw, list) or not (1 <= len(raw) <= MAX_EVENTS):
        raise ValueError(f"events needs 1–{MAX_EVENTS} entries")
    out = []
    hi = clip_duration if clip_duration else 1e6
    for e in raw:
        if not isinstance(e, dict):
            raise ValueError(f"event is not an object: {e!r}")
        ev = {"t": _num(e.get("t"), "event.t", 0, hi),
              "x": _num(e.get("x", 0.5), "event.x", 0, 1),
              "y": _num(e.get("y", 0.5), "event.y", 0, 1)}
        if e.get("strength") is not None:
            ev["strength"] = _num(e["strength"], "event.strength", 0, 1)
        if isinstance(e.get("label"), str) and e["label"].strip():
            ev["label"] = e["label"].strip()[:40]
        out.append(ev)
    out.sort(key=lambda ev: ev["t"])
    return out


def validate_effect(raw: Any, segments: list[dict], clips: dict[str, dict] | None = None,
                    *, keep_meta: bool = True) -> dict:
    """The whole effect, checked hard. `segments` are the cut's shots (with ids);
    `clips` the project's clip map (for the clip's duration). Raises ValueError with a
    sentence a person can act on; returns a clean copy with only known fields."""
    if not isinstance(raw, dict):
        raise ValueError("effect is not an object")
    # The edits (INTAKE M13): operations on the cut, validated against it; the effect's
    # shot may then be one the edits create (`new:n`), so the shot is looked up in the
    # cut AS IT WOULD BE after them.
    ops = None
    after = list(segments)
    if raw.get("edits"):
        ops = editsmod.validate_ops(raw["edits"], segments, clips)
        after = editsmod.apply_ops(segments, clips, ops)["segments"]
    shot = raw.get("shot")
    seg = next((s for s in after if str(s.get("id")) == str(shot)), None)
    if seg is None and ops and not shot:
        # an edit-only proposal with no shot named: it belongs to the first shot it changed
        changed = editsmod.apply_ops(segments, clips, ops)["changed"]
        shot = changed[0] if changed else None
        seg = next((s for s in after if str(s.get("id")) == str(shot)), None)
    if seg is None:
        raise ValueError(f"effect names a shot that is not in the cut: {shot!r}")
    clip = raw.get("clip") or seg["clip"]
    if clip != seg["clip"]:
        raise ValueError(f"effect's clip {clip!r} is not the shot's clip {seg['clip']!r}")
    duration = None
    if clips and clip in clips:
        duration = clips[clip].get("duration") or clips[clip].get("duration_s")
    if duration is None and editsmod.is_generated(clip):
        duration = float(seg["out"])                  # a generated clip is exactly its range
    name = raw.get("name") or "effect"
    if not isinstance(name, str):
        raise ValueError("name is not text")
    status = raw.get("status", "proposed")
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    out: dict[str, Any] = {
        "id": str(raw.get("id") or new_id()),
        "shot": str(shot),
        "clip": clip,
        "name": name.strip()[:40] or "effect",
        "note": str(raw.get("note") or "")[:600],
        "why": str(raw.get("why") or "")[:400],
        "events": (validate_events(raw.get("events"), duration)
                   if (raw.get("overlay") or raw.get("sound") or raw.get("events")) else []),
        "overlay": validate_overlay(raw.get("overlay")) if raw.get("overlay") else None,
        "sound": validate_sound(raw.get("sound")),
        "status": status,
    }
    if out["overlay"] is None and out["sound"] is None and not ops:
        raise ValueError("an effect needs an overlay, a sound, or edits to the cut")
    if (out["overlay"] or out["sound"]) and not out["events"]:
        raise ValueError("an overlay or a sound needs at least one event")
    if ops:
        out["edits"] = ops
        out["edit_words"] = editsmod.apply_ops(segments, clips, ops)["words"]
    if raw.get("limits"):
        out["limits"] = str(raw["limits"])[:300]      # what the effect could not do
    if keep_meta:
        for k in ("created", "reference", "verify", "history", "window", "previous"):
            if raw.get(k) is not None:
                out[k] = raw[k]
    return out


# ---------------------------------------------------------------- pure helpers

def sample_track(track: list[list[float]] | None, t: float, default: float) -> float:
    """Linear interpolation over `[[t, v], …]`; before the first key the first value,
    after the last the last. `None` → default."""
    if not track:
        return default
    if t <= track[0][0]:
        return track[0][1]
    for (t0, v0), (t1, v1) in zip(track, track[1:]):
        if t0 <= t <= t1:
            if t1 == t0:
                return v1
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return track[-1][1]


def pose_at(overlay: dict, t: float) -> dict:
    """The sprite's scale, opacity, rotation (degrees) and offset (fractions of the
    frame width) at `t` seconds into the effect. Both renderers call this."""
    a = overlay.get("anim") or {}
    return {"scale": sample_track(a.get("scale"), t, 1.0),
            "opacity": sample_track(a.get("opacity"), t, 1.0),
            "rotate": sample_track(a.get("rotate"), t, 0.0),
            "dx": sample_track(a.get("dx"), t, 0.0),
            "dy": sample_track(a.get("dy"), t, 0.0)}


def onset_peaks(onset: list[float], hz: float, t0: float, t1: float, *,
                min_gap_s: float = 0.25, top: int = 8,
                floor: float | None = None) -> list[dict]:
    """Local maxima of the onset track inside [t0, t1], strongest first, at least
    `min_gap_s` apart: `[{"t": seconds, "strength": 0..1}, …]`. `floor` (default: the
    window's mean + one standard deviation) drops the wind bed. The 100 ms accuracy of
    EFFECTS.md Rule 3, for free."""
    if not onset or hz <= 0:
        return []
    i0 = max(0, int(t0 * hz))
    i1 = min(len(onset), int(t1 * hz) + 1)
    win = onset[i0:i1]
    if len(win) < 3:
        return []
    if floor is None:
        mean = sum(win) / len(win)
        var = sum((v - mean) ** 2 for v in win) / len(win)
        if var ** 0.5 < 1e-6:
            # a flat window has no peaks: with floor == mean every sample would
            # be "≥ the floor and ≥ both neighbours" and the whole bed came back
            return []
        floor = mean + var ** 0.5
    cands = []
    for i in range(1, len(win) - 1):
        v = win[i]
        if v >= floor and v >= win[i - 1] and v >= win[i + 1]:
            cands.append((v, i0 + i))
    cands.sort(reverse=True)
    picked: list[dict] = []
    for v, i in cands:
        t = round(i / hz, 2)
        if all(abs(t - p["t"]) >= min_gap_s for p in picked):
            picked.append({"t": t, "strength": round(min(1.0, v), 3)})
        if len(picked) >= top:
            break
    picked.sort(key=lambda p: p["t"])
    return picked


def nearest_onset(onset: list[float], hz: float, t: float, *, tol_s: float = ONSET_TOL_S) -> float | None:
    """The time of the strongest onset sample within ±tol_s of `t`, or None."""
    if not onset or hz <= 0:
        return None
    i0 = max(0, int((t - tol_s) * hz))
    i1 = min(len(onset) - 1, int((t + tol_s) * hz + 0.5))
    if i1 < i0:
        return None
    best = max(range(i0, i1 + 1), key=lambda i: onset[i])
    return round(best / hz, 2)


def events_in_shot(effect: dict, seg: dict) -> list[dict]:
    """The events that fall inside the shot's range, each with `t_part` — seconds from
    the shot's in-point — added. An event outside is not drawn."""
    out = []
    for ev in effect.get("events", []):
        if seg["in"] <= ev["t"] < seg["out"]:
            out.append({**ev, "t_part": round(ev["t"] - seg["in"], 4)})
    return out


def effects_for_shot(edl: dict, seg: dict) -> list[dict]:
    """The EDL's accepted effects on this shot."""
    return [e for e in (edl.get("effects") or [])
            if str(e.get("shot")) == str(seg.get("id")) and e.get("status", "accepted") == "accepted"]


def price(*, design: bool = True, place_frames: int = 0, look_frames: int = 0) -> float:
    """What a call costs before it is made, in USD, the way Find and the visual pass
    price their buttons: one text call for the design, plus a frame-strip call for
    placing (~$0.02 a frame at 640 px) and one for the model's look at the proof."""
    usd = 0.05 if design else 0.0
    usd += 0.02 * place_frames + (0.03 + 0.02 * look_frames if look_frames else 0.0)
    return round(usd, 2)


# ---------------------------------------------------------------- lane: fx-core
# The functions below are the render lane's. Their signatures are the contract; the
# server and the board are written against them.
#
# Conventions the two renderers share (this file and /fx.js):
#   * the frame is (0,0) top-left, x right, y down; anchors are fractions of it
#   * the sprite's box is `size × frame width` pixels square, centred on the anchor
#     plus the pose's (dx, dy) — both fractions of the frame *width*
#   * shapes are in -1..1 across the box; `rotate` is degrees, clockwise positive
#     (a canvas `ctx.rotate` with y down); a line's `width` is a fraction of the box
#   * a text's `h` is in box units (h = 0.5 is a quarter of the box high)
#   * one PNG per frame, `ceil(duration × fps)` of them, the first at t = 0

_DEJAVU = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf")
_DEJAVU_REGULAR = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans.ttf")
_SUPER = 2                                  # draw at 2×, downsample with LANCZOS


def _fps_value(fps: Any) -> float:
    """`24`, `23.976` or `"24000/1001"` → frames per second as a float. The part's
    profile carries its rate as a fraction string; the proof passes an int."""
    if isinstance(fps, str) and "/" in fps:
        num, _, den = fps.partition("/")
        return float(num) / float(den)
    return float(fps)


def _font(px: int, bold: bool = True):
    from PIL import ImageFont
    px = max(4, int(px))
    for candidate in (_DEJAVU if bold else _DEJAVU_REGULAR) + _DEJAVU:
        try:
            return ImageFont.truetype(candidate, px)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=px)
    except TypeError:                                  # Pillow < 10.1
        return ImageFont.load_default()


def _rgba(colour: str, opacity: float) -> tuple[int, int, int, int]:
    c = colour.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16),
            int(round(255 * max(0.0, min(1.0, opacity)))))


def _draw_shape(layer, shape: dict, c: float, unit: float, t: float = 0.0,
                duration: float = MAX_DURATION) -> None:
    """One shape onto its own transparent layer: `c` is the layer's centre (px), `unit`
    the pixels per box unit (half the box side, already supersampled); `t` is the
    time into the effect, for a shape's start / end / fade and a text's reveal."""
    import math
    from PIL import ImageDraw

    alpha = shape_alpha(shape, t, duration)
    if alpha <= 0:
        return
    text, talpha = ("", 1.0)
    if shape["type"] == "text":
        text, talpha = text_at(shape, t)
        if not text.strip():
            return
    d = ImageDraw.Draw(layer)
    colour = _rgba(shape["color"], shape["opacity"] * alpha * talpha)

    def px(p):
        return (c + p[0] * unit, c + p[1] * unit)

    width = max(1, int(round(shape.get("width", 0.08) * 2 * unit)))   # fraction of the box
    kind = shape["type"]
    if kind == "line":
        a, b = px(shape["from"]), px(shape["to"])
        d.line([a, b], fill=colour, width=width)
        r = width / 2
        for (x, y) in (a, b):                                  # round caps
            d.ellipse([x - r, y - r, x + r, y + r], fill=colour)
    elif kind == "circle":
        x, y = px((shape["x"], shape["y"]))
        r = shape["r"] * unit
        if shape.get("fill", True):
            d.ellipse([x - r, y - r, x + r, y + r], fill=colour)
        else:
            d.ellipse([x - r, y - r, x + r, y + r], outline=colour, width=width)
    elif kind == "ring":
        x, y = px((shape["x"], shape["y"]))
        r, r2 = shape["r"] * unit, shape["r2"] * unit
        d.ellipse([x - r, y - r, x + r, y + r], fill=colour)
        if r2 > 0:
            d.ellipse([x - r2, y - r2, x + r2, y + r2], fill=(0, 0, 0, 0))
    elif kind in ("rect", "polygon"):
        if kind == "rect":
            hw, hh = shape["w"] / 2, shape["h"] / 2
            a = math.radians(shape.get("rotate", 0.0))
            ca, sa = math.cos(a), math.sin(a)
            pts = []
            for (u, v) in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)):
                pts.append(px((shape["x"] + u * ca - v * sa, shape["y"] + u * sa + v * ca)))
        else:
            pts = [px(p) for p in shape["points"]]
        if shape.get("fill", False):
            d.polygon(pts, fill=colour)
        else:
            d.polygon(pts, outline=colour, width=width)
    elif kind == "text":
        # h is a fraction of the BOX side (2 units), like a stroke's width
        size = max(4, int(shape["h"] * 2 * unit))
        font = _font(size, shape.get("bold", True))
        lines = str(shape["text"]).split("\n")
        full = [l for l in text.split("\n")]          # what shows now (a reveal may cut it)
        if shape.get("fit", True):
            # the widest FULL line has to fit the box's width, so a typewriter line
            # does not change size as it types
            widest = max((font.getlength(l) for l in lines), default=0)
            limit = 1.9 * unit                        # 95 % of the box side
            if widest > limit and widest > 0:
                size = max(4, int(size * limit / widest))
                font = _font(size, shape.get("bold", True))
        x, y = px((shape["x"], shape["y"]))
        gap = size * 1.18
        top = y - gap * (len(lines) - 1) / 2
        for i, line in enumerate(full):
            if line:
                d.text((x, top + i * gap), line, fill=colour, font=font, anchor="mm")


def _sprite_image(overlay: dict, box_px: float, pose: dict, t: float = 0.0):
    """The sprite at one pose as an RGBA image (already downsampled), plus the offset
    of its centre inside it. `box_px` is the unscaled box side in output pixels."""
    from PIL import Image

    half = box_px * pose["scale"] / 2                         # output px per box unit
    ext = int(math.ceil(half * 2.6 + 3))                      # ±1.5 units, rotated, plus caps
    side = 2 * ext
    canvas = Image.new("RGBA", (side * _SUPER, side * _SUPER), (0, 0, 0, 0))
    c = ext * _SUPER
    unit = half * _SUPER
    if unit >= 0.5:
        for shape in overlay["shapes"]:
            if shape_alpha(shape, t, overlay["duration"]) <= 0:
                continue
            layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            _draw_shape(layer, shape, c, unit, t, overlay["duration"])
            canvas.alpha_composite(layer)
        if pose["rotate"]:
            canvas = canvas.rotate(-pose["rotate"], resample=Image.BICUBIC, center=(c, c))
    sprite = canvas.resize((side, side), Image.LANCZOS)
    if pose["opacity"] < 1.0:
        a = sprite.getchannel("A").point(lambda v: int(v * pose["opacity"]))
        sprite.putalpha(a)
    return sprite, ext


def render_overlay_frames(overlay: dict, w: int, h: int, fps: float, out_dir: Path,
                          *, anchor: tuple[float, float] = (0.5, 0.5)) -> list[Path]:
    """Rasterise the sprite for one event into RGBA PNG frames `out_dir/f_%04d.png`,
    full-frame (w × h) with the sprite at `anchor` (fractions of the frame), one file
    per frame for `overlay.duration` seconds at `fps`. Geometry: the sprite's box is
    `overlay.size × w` pixels square; shapes are in -1..1 across it; `pose_at(t)`
    scales, fades, rotates and offsets it. A `flash` tints the whole frame for its
    duration. PIL, anti-aliased (draw at 2× and downsample)."""
    from PIL import Image

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rate = _fps_value(fps)
    n = max(1, int(math.ceil(overlay["duration"] * rate - 1e-6)))
    box_px = overlay["size"] * w
    flash = overlay.get("flash")
    paths: list[Path] = []
    for i in range(n):
        t = i / rate
        pose = pose_at(overlay, t)
        if flash and t < flash["duration"]:
            frame = Image.new("RGBA", (w, h), _rgba(flash["color"], flash["opacity"]))
        else:
            frame = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        if pose["opacity"] > 0 and pose["scale"] > 0:
            sprite, ext = _sprite_image(overlay, box_px, pose, t)
            cx = anchor[0] * w + pose["dx"] * w
            cy = anchor[1] * h + pose["dy"] * w
            x0, y0 = int(round(cx)) - ext, int(round(cy)) - ext
            # clip the sprite to the frame: alpha_composite needs a non-negative dest
            left, top = max(0, -x0), max(0, -y0)
            right = min(sprite.width, w - x0)
            bottom = min(sprite.height, h - y0)
            if right > left and bottom > top:
                part = sprite.crop((left, top, right, bottom))
                frame.alpha_composite(part, dest=(x0 + left, y0 + top))
        p = out_dir / f"f_{i:04d}.png"
        frame.save(p, "PNG", compress_level=1)
        paths.append(p)
    return paths


def render_overlay_mov(overlay: dict, w: int, h: int, fps: float, out: Path,
                       *, anchor: tuple[float, float] = (0.5, 0.5)) -> Path:
    """The frames above as one `.mov` with alpha (`-c:v png` or `qtrle`) so a part's
    ffmpeg graph can `overlay` it with `enable=between(t, …)`. Returns `out`."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    frames_dir = Path(tempfile.mkdtemp(prefix=out.stem + "_frames_", dir=out.parent))
    try:
        render_overlay_frames(overlay, w, h, fps, frames_dir, anchor=anchor)
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-nostdin", "-framerate", str(fps),
             "-start_number", "0", "-i", str(frames_dir / "f_%04d.png"),
             "-c:v", "png", "-pix_fmt", "rgba", str(out)],
            capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"overlay mov failed: {r.stderr[-300:]}")
    finally:
        shutil.rmtree(frames_dir, ignore_errors=True)
    return out


# ---- the synth

def _one_pole(x, coef: float, gain: float):
    """y[n] = coef·y[n-1] + gain·x[n], in blocks: the closed form over a block is a
    cumulative sum scaled by powers of `coef`, carried in from the block before, so
    a three-second patch does not mean a 144k-iteration Python loop."""
    n = len(x)
    y = np.empty(n)
    if coef <= 0.0:
        y[:] = gain * x
        return y
    # a^-k must stay finite: block so that a^-(B-1) ≤ 1e12
    B = max(1, min(4096, int(12 * math.log(10) / -math.log(coef)) if coef < 1 else 4096))
    ks = np.arange(B)
    a_neg = coef ** -ks.astype(float)             # a^-k
    a_pos = coef ** (ks + 1).astype(float)        # a^(k+1), for the carry
    carry = 0.0
    for s in range(0, n, B):
        xb = gain * x[s:s + B]
        m = len(xb)
        acc = np.cumsum(xb * a_neg[:m]) * (coef ** ks[:m].astype(float))
        yb = acc + carry * a_pos[:m]
        y[s:s + m] = yb
        carry = yb[-1]
    return y


def _lowpass(x, fc: float, sr: int):
    a = math.exp(-2 * math.pi * fc / sr)
    return _one_pole(x, a, 1 - a)


def _highpass(x, fc: float, sr: int):
    return x - _lowpass(x, fc, sr)


def _envelope(t, attack: float, decay: float):
    """Linear attack to 1, then an exponential decay reaching -60 dB at `decay`
    seconds after the attack ends."""
    env = np.ones_like(t)
    if attack > 0:
        env = np.minimum(1.0, t / attack)
    after = np.clip(t - attack, 0, None)
    env = env * np.exp(-math.log(1000.0) * after / max(decay, 1e-4))
    return env


def _wave(phase, wave: str):
    """`phase` in cycles."""
    frac = phase - np.floor(phase)
    if wave == "square":
        return np.where(frac < 0.5, 1.0, -1.0)
    if wave == "saw":
        return 2.0 * frac - 1.0
    if wave == "triangle":
        return 2.0 * np.abs(2.0 * frac - 1.0) - 1.0
    return np.sin(2 * np.pi * phase)


def _layer_signal(layer: dict, t, sr: int, rng) -> Any:
    kind = layer["type"]
    if kind == "tone":
        return _wave(layer["freq"] * t, layer["wave"])
    if kind == "sweep":
        f0, f1 = layer["freq"], layer["freq_end"]
        dur = max(float(t[-1]), 1e-4) if len(t) else 1e-4
        ratio = f1 / f0
        if abs(ratio - 1.0) < 1e-6:
            phase = f0 * t
        else:                                       # ∫ f0·ratio^(t/D) dt
            phase = f0 * dur / math.log(ratio) * (ratio ** (t / dur) - 1.0)
        return _wave(phase, layer["wave"])
    if kind == "noise":
        white = rng.uniform(-1.0, 1.0, len(t))
        if layer["color"] == "pink":
            # Paul Kellet's economy pink: three one-poles summed (≈ -3 dB/octave)
            b0 = _one_pole(white, 0.99765, 0.0990460)
            b1 = _one_pole(white, 0.96300, 0.2965164)
            b2 = _one_pole(white, 0.57000, 1.0526913)
            pink = b0 + b1 + b2 + white * 0.1848
            return pink / 4.0
        return white
    # click: a one-sample impulse — the envelope alone is what is heard
    sig = np.zeros(len(t))
    if len(sig):
        sig[0] = 1.0
    return sig


def synth_sound(sound: dict, out: Path, *, sr: int = 48000) -> Path:
    """Render the synth patch to a 16-bit stereo WAV at `sr`: each layer is an envelope
    (attack, exponential decay) over a tone (wave, freq), a sweep (freq → freq_end), a
    noise (white / pink) or a click (one-sample impulse through the decay), summed with
    its gain, optional one-pole hp/lp, then `gain_db`, peak-limited to -1 dBFS. numpy
    only. Returns `out`."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = max(1, int(round(sound["duration"] * sr)))
    rng = np.random.default_rng(1234)              # the same patch renders the same bytes
    rep = sound.get("repeat")
    if rep:
        # one hit, then the same hit `count` times `every` seconds apart with a little
        # jitter and a gain that runs to `gain_end`: a clatter, a heartbeat, a ticking
        tail = max(l["attack"] + l["decay"] for l in sound["layers"]) + 0.05
        hit = synth_sound({**sound, "duration": min(MAX_DURATION, tail), "gain_db": 0.0,
                           "repeat": None}, out.with_suffix(".hit.wav"), sr=sr)
        with wave.open(str(hit), "rb") as wf:
            frames = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2")[::2] / 32767.0
        try:
            hit.unlink()
        except OSError:
            pass
        mix = np.zeros(n)
        count = int(rep["count"])
        for k in range(count):
            at = k * rep["every"] + rng.uniform(-0.5, 0.5) * rep["jitter"] * rep["every"]
            i0 = int(round(max(0.0, at) * sr))
            if i0 >= n:
                break
            g = 1.0 + (rep["gain_end"] - 1.0) * (k / max(1, count - 1))
            m = min(len(frames), n - i0)
            mix[i0:i0 + m] += g * frames[:m]
        mix *= 10 ** (sound["gain_db"] / 20)
        limit = 10 ** (-1 / 20)
        peak = float(np.max(np.abs(mix))) if n else 0.0
        if peak > limit:
            mix *= limit / peak
        pcm = (np.clip(mix, -1, 1) * 32767).astype("<i2")
        with wave.open(str(out), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(np.repeat(pcm, 2).tobytes())
        return out
    t = np.arange(n) / sr
    mix = np.zeros(n)
    for layer in sound["layers"]:
        sig = _layer_signal(layer, t, sr, rng)
        if layer["type"] == "click":
            # the impulse *through* the decay: a step that dies at -60 dB by `decay`
            sig = np.exp(-math.log(1000.0) * t / max(layer["decay"], 1e-4))
        else:
            sig = sig * _envelope(t, layer["attack"], layer["decay"])
        if layer.get("hp"):
            sig = _highpass(sig, layer["hp"], sr)
        if layer.get("lp"):
            sig = _lowpass(sig, layer["lp"], sr)
        mix += layer["gain"] * sig
    mix *= 10 ** (sound["gain_db"] / 20)
    limit = 10 ** (-1 / 20)                        # -1 dBFS
    peak = float(np.max(np.abs(mix))) if n else 0.0
    if peak > limit:
        mix *= limit / peak
    pcm = (np.clip(mix, -1, 1) * 32767).astype("<i2")
    stereo = np.repeat(pcm, 2)                     # the same signal both sides
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(stereo.tobytes())
    return out


# ---- the part's graph

def part_graph(effects: list[tuple[dict, list[dict]]], w: int, h: int, fps: float,
               workdir: Path, *, vin: str = "vbase", ain: str = "abase") -> tuple[list[str], str, str, str]:
    """For one part: `effects` is `[(effect, events_in_shot(effect, seg)), …]`. Renders
    every event's overlay `.mov` and every effect's sound `.wav` into `workdir`, and
    returns `(extra_inputs, graph, vout, aout)`: the `-i` arguments to append after the
    part's source, a `filter_complex` fragment that reads `[vin]`/`[ain]` and writes
    `[vout]`/`[aout]` — an `overlay` per event with `setpts` delaying the sprite to
    `t_part` and `enable=between(t, t_part, t_part + duration)`, an `adelay` per
    event and one `amix` (`normalize=0`) — and the two output labels. Input indexes
    start at 1 (the part's source is input 0). No effects → `([], "", vin, ain)`."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    extra: list[str] = []
    vparts: list[str] = []
    aparts: list[str] = []
    mix_inputs: list[str] = []
    cur = vin
    idx = 1
    rate = _fps_value(fps)
    for effect, events in effects:
        if not events:
            continue
        overlay = effect["overlay"]
        eid = str(effect.get("id") or "fx")
        for j, ev in enumerate(events):
            t0 = float(ev["t_part"])
            mov = workdir / f"{eid}_ev{j:02d}.mov"
            render_overlay_mov(overlay, w, h, rate, mov, anchor=(ev["x"], ev["y"]))
            extra += ["-i", str(mov)]
            lab = f"fxv{idx}"
            nxt = f"fxo{idx}"
            vparts.append(f"[{idx}:v]setpts=PTS-STARTPTS+{t0:.4f}/TB[{lab}]")
            vparts.append(f"[{cur}][{lab}]overlay=0:0:eof_action=pass"
                          f":enable='between(t,{t0:.4f},{t0 + overlay['duration']:.4f})'[{nxt}]")
            cur = nxt
            idx += 1
        if effect.get("sound"):
            wav = workdir / f"{eid}_sound.wav"
            synth_sound(effect["sound"], wav)
            extra += ["-i", str(wav)]
            # one WAV per effect, split to one delayed copy per event; amix wants
            # every input at one rate and layout
            conv = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
            if len(events) > 1:
                outs = "".join(f"[fxs{idx}_{j}]" for j in range(len(events)))
                aparts.append(f"[{idx}:a]{conv},asplit={len(events)}{outs}")
                heads = [f"[fxs{idx}_{j}]" for j in range(len(events))]
                chain = ""
            else:
                heads = [f"[{idx}:a]"]
                chain = conv + ","
            for j, ev in enumerate(events):
                samples = int(round(float(ev["t_part"]) * 48000))
                lab = f"fxa{idx}_{j}"
                aparts.append(f"{heads[j]}{chain}adelay={samples}S|{samples}S[{lab}]")
                mix_inputs.append(f"[{lab}]")
            idx += 1
    if not vparts and not aparts:
        return [], "", vin, ain
    vout, aout = vin, ain
    if vparts:
        # rename the last overlay's label to the fixed output label
        vparts[-1] = vparts[-1][: vparts[-1].rfind("[")] + "[vout]"
        vout = "vout"
    if mix_inputs:
        aparts.append(f"[{ain}]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[fxabase]")
        aparts.append(f"[fxabase]{''.join(mix_inputs)}amix=inputs={len(mix_inputs) + 1}"
                      f":normalize=0:duration=first[aout]")
        aout = "aout"
    return extra, ";".join(vparts + aparts), vout, aout


# ---- frames and strips for the model's eyes

def frames_for(proxy: Path, times: list[float], out_dir: Path, *, width: int = 640) -> list[Path]:
    """One JPEG per time from the proxy (`ffmpeg -ss t -i proxy -frames:v 1`), `width`
    px wide, named `t_<seconds>.jpg`. Returns the paths in the order of `times`."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for t in times:
        p = out_dir / f"t_{float(t):.2f}.jpg"
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-nostdin", "-ss", f"{float(t):.3f}",
             "-i", str(proxy), "-frames:v", "1", "-vf", f"scale={int(width)}:-2",
             "-q:v", "3", str(p)], capture_output=True, text=True)
        if r.returncode != 0 or not p.exists():
            raise RuntimeError(f"frame at {t:.2f}s failed: {r.stderr[-300:]}")
        paths.append(p)
    return paths


def contact_strip(frames: list[Path], labels: list[str], out: Path, *, cols: int = 4) -> Path:
    """The frames tiled with their labels burnt in (PIL), for a placing call and for
    the proof look. Returns `out`."""
    from PIL import Image, ImageDraw

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    images = [Image.open(p).convert("RGB") for p in frames]
    if not images:
        raise ValueError("contact_strip needs at least one frame")
    cw = max(im.width for im in images)
    ch = max(im.height for im in images)
    cols = max(1, min(cols, len(images)))
    rows = (len(images) + cols - 1) // cols
    pad = 4
    sheet = Image.new("RGB", (cols * (cw + pad) + pad, rows * (ch + pad) + pad), (18, 18, 18))
    font = _font(max(12, ch // 16))
    for i, (im, label) in enumerate(zip(images, labels + [""] * (len(images) - len(labels)))):
        x = pad + (i % cols) * (cw + pad)
        y = pad + (i // cols) * (ch + pad)
        sheet.paste(im, (x, y))
        d = ImageDraw.Draw(sheet)
        text = str(label)
        if text:
            bbox = d.textbbox((x + 6, y + 4), text, font=font)
            d.rectangle([bbox[0] - 4, bbox[1] - 2, bbox[2] + 4, bbox[3] + 2], fill=(0, 0, 0))
            d.text((x + 6, y + 4), text, fill=(255, 255, 255), font=font)
    sheet.save(out, "JPEG", quality=85)
    return out


# ---- the model calls

_EXAMPLE_EFFECT = {
    "name": "hit markers",
    "why": "a Call-of-Duty hit marker on each rock strike the onset track found",
    "events": [{"t": 152.34, "x": 0.52, "y": 0.68, "strength": 0.9, "label": "first rock"}],
    "overlay": {
        "duration": 0.35, "size": 0.12,
        "shapes": [
            {"type": "line", "from": [-1, -1], "to": [-0.3, -0.3], "width": 0.1, "color": "#ffffff"},
            {"type": "line", "from": [1, -1], "to": [0.3, -0.3], "width": 0.1, "color": "#ffffff"},
            {"type": "line", "from": [-1, 1], "to": [-0.3, 0.3], "width": 0.1, "color": "#ffffff"},
            {"type": "line", "from": [1, 1], "to": [0.3, 0.3], "width": 0.1, "color": "#ffffff"}],
        "anim": {"scale": [[0, 1.4], [0.06, 1.0]],
                 "opacity": [[0, 1], [0.23, 1], [0.35, 0]]},
        "flash": {"color": "#ff0000", "opacity": 0.15, "duration": 0.08}},
    "sound": {
        # the Call-of-Duty tick is a click, not a beep: an impulse, a very short high
        # ping and a breath of high noise, 90 ms in all (Karl, 2026-09-20: "the hit
        # noise is not the clicky one from COD")
        "duration": 0.09, "gain_db": -4,
        "layers": [{"type": "click", "attack": 0.0, "decay": 0.03, "gain": 0.9},
                   {"type": "tone", "wave": "triangle", "freq": 3200, "attack": 0.001, "decay": 0.035, "gain": 0.5},
                   {"type": "noise", "color": "white", "hp": 4000, "attack": 0.0, "decay": 0.02, "gain": 0.35}]},
}


_EXAMPLE_TITLE = {
    "name": "SEND IT title",
    "why": "one title over the drop-in, held for the run-up, gone before the landing",
    "events": [{"t": 40.2, "x": 0.5, "y": 0.22, "label": "the drop-in"}],
    "overlay": {
        "duration": 1.8, "size": 0.9,
        "shapes": [
            {"type": "rect", "at": [0, 0], "w": 2.0, "h": 0.5, "fill": True, "color": "#0b0b0f", "opacity": 0.55},
            {"type": "text", "text": "SEND IT", "at": [0, 0], "h": 0.34, "bold": True, "color": "#ffffff"}],
        "anim": {"scale": [[0, 1.25], [0.12, 1.0]],
                 "opacity": [[0, 0], [0.1, 1], [1.5, 1], [1.8, 0]],
                 "dy": [[0, 0.02], [0.12, 0]]}},
    "sound": {
        "duration": 0.9, "gain_db": -8,
        "layers": [{"type": "sweep", "wave": "sine", "freq": 300, "freq_end": 2400, "attack": 0.05, "decay": 0.6, "gain": 0.5},
                   {"type": "noise", "color": "pink", "hp": 800, "attack": 0.08, "decay": 0.5, "gain": 0.45}]},
}


_EXAMPLE_TYPEWRITER = {
    "name": "typed title slide",
    "why": "a black slide that types the title line by line with a clatter, holds, then fades to the footage",
    "limits": "an effect cannot add two seconds to the shot — extend the shot's in-point on the timeline for that",
    "events": [{"t": 187.09, "x": 0.5, "y": 0.5, "label": "the title"}],
    "overlay": {
        "duration": 7.0, "size": 1.6,
        "shapes": [
            {"type": "rect", "at": [0, 0], "w": 3, "h": 3, "fill": True, "color": "#050507", "opacity": 1.0,
             "end": 7.0, "fade": 1.5},
            {"type": "text", "text": "2026 BLIZZARD\nKillington, VT\nwith the boys", "at": [0, 0], "h": 0.07,
             "bold": True, "color": "#f2efe6", "reveal": "typewriter", "cps": 12, "start": 0.4, "end": 7.0, "fade": 1.0}],
        "anim": {}},
    "sound": {
        "gain_db": -12,
        "layers": [{"type": "click", "attack": 0.0, "decay": 0.02, "gain": 0.8},
                   {"type": "tone", "wave": "triangle", "freq": 2600, "attack": 0.001, "decay": 0.015, "gain": 0.35},
                   {"type": "noise", "color": "white", "hp": 3000, "attack": 0.0, "decay": 0.012, "gain": 0.3}],
        "repeat": {"every": 0.0833, "count": 38, "jitter": 0.25, "gain_end": 0.9}},
}


_EXAMPLE_SLIDE = {
    "name": "opening title on a black slide",
    "why": "a 3 s black slide put before the first shot, the title typed on it, the slide's last second fading is the footage arriving",
    "edits": [{"op": "generate", "kind": "black", "seconds": 3.0, "before": "g1a2b3c4d5e"}],
    "shot": "new:1", "clip": "gen_black_x.mp4",
    "events": [{"t": 0.0, "x": 0.5, "y": 0.5, "label": "the slide"}],
    "overlay": {"duration": 3.0, "size": 1.6,
                "shapes": [{"type": "text", "text": "2026 BLIZZARD\nKillington, VT", "at": [0, 0], "h": 0.07, "bold": True,
                            "color": "#f2efe6", "reveal": "typewriter", "cps": 12, "start": 0.3}],
                "anim": {}},
    "sound": {"gain_db": -12,
              "layers": [{"type": "click", "attack": 0.0, "decay": 0.02, "gain": 0.8}],
              "repeat": {"every": 0.0833, "count": 26, "jitter": 0.25, "gain_end": 0.9}},
}
_EXAMPLE_SLOWMO = {
    "name": "slow motion on the landing",
    "why": "the landing at 0.4× from the take-off to the ride-away; the cut is otherwise untouched",
    "edits": [{"op": "speed", "shot": "g1a2b3c4d5e", "rate": 0.4, "from": 72.8, "to": 74.6}],
    "events": [], "overlay": None, "sound": None,
}


def _vocabulary() -> str:
    """The closed vocabulary with its ranges, from the constants above, so the system
    text can never drift from what `validate_effect` accepts."""
    MIN_SPEED_S, MAX_SPEED_S = f"{editsmod.MIN_SPEED:g}", f"{editsmod.MAX_SPEED:g}"
    MIN_GEN_S_S, MAX_GEN_S_S = f"{editsmod.MIN_GEN_S:g}", f"{editsmod.MAX_GEN_S:g}"
    return f"""You design one video + audio effect for a ski film, as JSON in a closed vocabulary.
The renderer draws it from the numbers; you never write ffmpeg, filenames or pixels.
An effect can be anything the vocabulary can say: a marker on an impact, a title or a
caption, a tint or a vignette over the whole frame, a ring or a glow on a thing, a
flash on a cut, a stamp that holds for seconds — with a tick, a whoosh, a riser, a
thud, or no sound at all. The note decides; the examples below are two of many.
- An INSTANT effect (a marker, a flash, a stamp) has one event per moment, a short
  duration, and often sits on an impact the audio found.
- A CONTINUOUS effect (a title, a tint, a vignette, a caption) has ONE event at the
  moment it should start, a duration that covers how long it holds (up to
  {MAX_DURATION:g} s), and its anchor is where it sits — the centre (0.5, 0.5) with a
  size of 1.6 covers the frame.
- A FOLLOWING effect (a ring on a skier, a glow on a thing) is one event with dx / dy
  keyframes that track the thing across the frame over its duration.

COORDINATES
- The frame: (0, 0) is the top-left corner, (1, 1) the bottom-right. An event's anchor
  x, y are fractions of the frame (0..1).
- The sprite's box: a square, `size` × the frame width across (size {MIN_SIZE}–{MAX_SIZE}),
  centred on the anchor. Inside it shapes use x and y from -1 to 1 across the box
  (y down); a shape may poke a little past (±1.5) but never across the frame.
- Times are seconds in the source clip; a `duration` is seconds.

EFFECT = {{"name": "≤40 chars", "why": "one sentence", "events": [...], "overlay": {{...}} | null, "sound": {{...}} | null, "edits": [...] | omit, "limits": "what the note asked that nothing here can do, or omit"}}

EDITS — operations on the cut itself, applied when the human accepts (the cut is listed
under THE CUT with each shot's id; times are clip seconds; a new shot an op creates is
"new:1", "new:2", … in the order the ops create them, and the effect's "shot" may be one
of those — a title on a slide you generate):
  {{"op": "extend", "shot": id, "in": Δs (negative = earlier), "out": Δs}}    lengthen or shorten a shot
  {{"op": "set_range", "shot": id, "in": t, "out": t}}
  {{"op": "split", "shot": id, "at": t}}                                        the second half is new:n
  {{"op": "speed", "shot": id, "rate": {MIN_SPEED_S}–{MAX_SPEED_S}, "from": t, "to": t}}   slow motion / speed-up of a range (the middle piece is new:n; omit from/to for the whole shot)
  {{"op": "generate", "kind": "black"|"colour"|"still", "seconds": {MIN_GEN_S_S}–{MAX_GEN_S_S}, "color": "#rrggbb", "from_shot": id, "at": t, "before": id | null (the end) | "after": id}}   a new clip in the cut (a slide, a freeze frame)
  {{"op": "freeze", "shot": id, "at": t, "seconds": s}}                           a freeze frame inside a shot
  {{"op": "insert", "clip": name, "in": t, "out": t, "before": id | null | "after": id}}   footage into the cut
  {{"op": "remove", "shot": id}}   {{"op": "move", "shot": id, "before": id | "after": id}}
  An effect with edits and no overlay is fine ("slow motion on the jump" is one edit).
  Edits change the cut; the overlay and the sound change pixels and the mix. Use each
  for what it is for: a title slide before a shot is a generated black clip plus a text
  effect on it; a fade-in of the video is an overlay; slow motion is an edit.

SIZES that read well (with size 1.0, the box as wide as the frame): a headline line h 0.10–0.14, a subtitle 0.06–0.08, a caption 0.04–0.05; a full-frame slide is a filled rect w 3 h 3 at size 1.6. Stack lines with \\n in ONE text shape, never several shapes at the same spot.

WHAT AN EFFECT CANNOT DO: change the shot's length, speed or framing (no extra seconds, no slow motion, no zoom, no trim), move or cut footage, or use a recording. Say so in "limits" in one sentence and do the rest.

EVENTS (1–{MAX_EVENTS}): {{"t": seconds, "x": 0..1, "y": 0..1, "strength": 0..1 (optional), "label": "≤40 chars" (optional)}}

OVERLAY: {{"duration": {MIN_DURATION}–{MAX_DURATION} s, "size": {MIN_SIZE}–{MAX_SIZE}, "shapes": [1–{MAX_SHAPES}], "anim": {{...}}, "flash": {{...}} | null}}
  shapes (all take "color": "#rrggbb" and "opacity": 0..1; "width" 0–0.5 is a fraction of the box):
    {{"type": "line", "from": [x, y], "to": [x, y], "width"}}
    {{"type": "circle", "at": [x, y], "r": 0.01–1.5, "fill": true|false, "width"}}
    {{"type": "ring", "at": [x, y], "r": 0.01–1.5, "r2": 0–r (the hole)}}
    {{"type": "rect", "at": [x, y], "w": 0.01–3, "h": 0.01–3, "rotate": degrees, "fill", "width"}}
    {{"type": "polygon", "points": [[x, y] × 3–32], "fill", "width"}}
    {{"type": "text", "text": "≤{MAX_TEXT} chars, up to {MAX_TEXT_LINES} lines with \\n", "at": [x, y], "h": 0.02–1.0 (one line's height as a fraction of the box side), "bold": true|false, "fit": true (shrink to the box's width), "reveal": "typewriter"|"fade" (optional), "cps": 2–60 (characters per second for typewriter)}}
  every shape may carry "start" (seconds into the effect, default 0), "end" (optional) and "fade" (seconds, in and out): a title's lines can arrive one after another, a bar can leave before the text
  anim: keyframe tracks, each `[[t, value], …]` (1–{MAX_KEYS} keys, t within 0..duration, linear between keys):
    "scale" 0–6 (1 = the box), "opacity" 0–1, "rotate" degrees (clockwise), "dx" / "dy" -1..1 (fractions of the frame width)
  flash: {{"color": "#rrggbb", "opacity": 0–0.6, "duration": 0.02–1.0}} — tints the whole frame briefly

SOUND (or null for a silent effect): {{"duration": {MIN_DURATION}–{MAX_DURATION} s, "gain_db": {MIN_GAIN_DB:g}–{MAX_GAIN_DB:g}, "layers": [1–{MAX_LAYERS}]}}
  every layer: "gain" 0–1, "attack" 0–1 s (linear), "decay" 0.005–3 s (exponential, to -60 dB), optional "hp" / "lp" 20–20000 Hz (one-pole)
  optional "repeat": {{"every": 0.02–2 s, "count": 1–{MAX_REPEAT}, "jitter": 0–1 (of every), "gain_end": 0–1}} — the same hit again and again (a typewriter's clatter: a click every 1/cps s for as many characters; a heartbeat; a ticking); the sound's duration is then computed
    {{"type": "tone", "wave": "sine"|"square"|"saw"|"triangle", "freq": 20–12000}}
    {{"type": "sweep", "wave": …, "freq": 20–12000, "freq_end": 20–12000}}  (exponential glide)
    {{"type": "noise", "color": "white"|"pink"}}
    {{"type": "click"}}  (a one-sample impulse through the decay)

The answer is JSON only — one object, no prose, no code fence."""


def _system_design() -> str:
    return (_vocabulary() + "\n\nWORKED EXAMPLE — a Call-of-Duty hit marker: four short white lines in an X with a "
            "gap in the middle, scale 1.4 → 1.0 in 60 ms, opacity to 0 over the last 120 ms, a 15 % "
            "red flash for 80 ms; its sound the clicky tick — an impulse, a 3.2 kHz triangle ping "
            "decaying in 35 ms and a breath of high noise for 20 ms, 90 ms in all, at -4 dB — not a "
            "beep:\n" + json.dumps(_EXAMPLE_EFFECT, indent=1)
            + "\n\nA SECOND EXAMPLE — a title: 'SEND IT' over a dark bar at the top of the frame, "
              "held 1.8 s with a quick settle and a fade, a rising whoosh under it:\n"
            + json.dumps(_EXAMPLE_TITLE, indent=1)
            + "\n\nA THIRD EXAMPLE — a typed title slide: a black slide, three lines typed at 12 "
              "characters a second with a click per character (repeat every 1/12 s, one per "
              "character), the slide fading to the footage over its last 1.5 s, and a `limits` "
              "line for what the note asked that an effect cannot do:\n"
            + json.dumps(_EXAMPLE_TYPEWRITER, indent=1)
            + "\n\nA FOURTH EXAMPLE — the cut changes too: a black slide the edits generate before shot "
              "g1a2b3c4d5e, the title typed on that new slide (shot new:1, its clip is the generated "
              "file — write any name, the renderer fills it in), and a FIFTH, an edit with no overlay at "
              "all, slow motion on a range:\n"
            + json.dumps(_EXAMPLE_SLIDE, indent=1) + "\n" + json.dumps(_EXAMPLE_SLOWMO, indent=1)
            + "\n\nMark only the moments the note names, inside the window the editor gave when there "
              "is one; when in doubt, fewer events. The audio's impacts are candidates for INSTANT "
              "effects only; a continuous effect starts where the note says and ignores them. A "
              "transient is not the note's event unless that event could have made it.")


def _transcript_in(transcript: list[dict] | None, t0: float, t1: float) -> list[dict]:
    out = []
    for line in transcript or []:
        try:
            s, e = float(line.get("start", 0)), float(line.get("end", 0))
        except (TypeError, ValueError):
            continue
        if e >= t0 and s <= t1 and str(line.get("text") or "").strip():
            out.append(line)
    return out


def build_design_prompt(note: str, seg: dict, clip: dict, *, peaks: list[dict],
                        transcript: list[dict] | None = None,
                        reference: dict | None = None,
                        cut: list[dict] | None = None) -> tuple[str, str]:
    """`(system, prompt)` for the design call: the note, the shot (clip, in/out, why),
    the transcript lines in the shot, the onset peaks as candidate impact times, and —
    when the human drew one — the reference's goal text and its mark centroids as the
    anchors. The model answers one JSON effect (`events`, `overlay`, `sound`, `name`,
    `why`) in this module's vocabulary, which the caller validates. The system text
    states the vocabulary with ranges, that anchors are fractions of the frame, that
    the sprite box is -1..1, and gives one worked example (a hit marker: four short
    white lines in an X with a gap in the middle, scale 1.4 → 1.0 in 60 ms, opacity
    to 0 over the last 120 ms, a 15 % red flash for 80 ms; a sound of a 1.8 kHz
    square tone decaying in 60 ms plus a white noise burst with a 1.5 kHz high-pass
    decaying in 90 ms, -6 dB)."""
    t0, t1 = float(seg["in"]), float(seg["out"])
    lines = [f"THE NOTE: {note.strip()}", "",
             f"THE SHOT: id {seg.get('id')} · clip {seg.get('clip')} from {t0:.2f}s to {t1:.2f}s"
             + (f" (clip length {float(clip['duration']):.1f}s)" if clip and clip.get("duration") else "")
             + (f" at {editsmod.speed_of(seg):g}×" if editsmod.speed_of(seg) != 1 else "")
             + (f" — why it is in the cut: {seg['why']}" if seg.get("why") else "")]
    if cut:
        lines += ["", "THE CUT (shot · id · clip · range · film length), the shot above marked ►:"]
        for k, s in enumerate(cut):
            mark = "►" if str(s.get("id")) == str(seg.get("id")) else " "
            lines.append(f"  {mark} {k + 1:2d} · {s.get('id')} · {s.get('clip')} · "
                         f"{float(s['in']):.2f}–{float(s['out']):.2f}s · {editsmod.dur(s):.1f}s"
                         + (f" · {editsmod.speed_of(s):g}×" if editsmod.speed_of(s) != 1 else ""))
    said = _transcript_in(transcript if transcript is not None else (clip or {}).get("transcript"), t0, t1)
    if said:
        lines += ["", "SAID IN THE SHOT:"]
        lines += [f"  {float(l.get('start', 0)):.1f}s  {str(l.get('text')).strip()}" for l in said[:12]]
    if peaks:
        lines += ["", "SHARP MOMENTS THE AUDIO FOUND (onset peaks, seconds in the clip, strength 0..1) — "
                      "candidates for an instant effect when the note names an impact; a continuous "
                      "effect starts where the note says:"]
        lines += [f"  t={p['t']:.2f}  strength={p.get('strength', 0):.2f}" for p in peaks]
    else:
        lines += ["", "The audio found no sharp moments in the shot; place events by the note and the shot's range."]
    if reference and reference.get("marks"):
        marks = ", ".join(f"({m[0]:.3f}, {m[1]:.3f})" for m in reference["marks"])
        lines += ["", "THE HUMAN DREW A REFERENCE on a frame"
                      + (f" at {float(reference['t']):.2f}s" if reference.get("t") is not None else "")
                      + (f": \"{reference['goal']}\"" if reference.get("goal") else "")
                      + f". The marks' centres, as fractions of the frame: {marks}. These ARE the anchors: "
                        "one event per mark at those x, y — design the look and the sound, do not move them."]
    lines += ["", "Every event's t must lie inside the shot. Answer with one JSON effect object only."]
    return _system_design(), "\n".join(lines)


def build_place_prompt(note: str, effect: dict, candidates: list[dict], strip: Path) -> tuple[str, str]:
    """`(system, prompt)` for the placing call: the strip image (frames at each
    candidate time, labelled), the note, and the question — for each frame, is the
    impact visible, and where in the frame (x, y as fractions) is the thing the note
    names (the skis)? Answers `{"events": [{"t", "x", "y", "hit": bool, "why"}]}`."""
    system = ("You place a video effect by looking at frames from a ski film. Coordinates are "
              "fractions of the frame: (0, 0) top-left, (1, 1) bottom-right. Answer JSON only.")
    lines = [f"THE NOTE: {note.strip()}",
             f"THE EFFECT: {effect.get('name', 'effect')} — {effect.get('why', '')}".rstrip(" —"),
             "",
             f"The image is a contact strip of {len(candidates)} frames, each labelled with its "
             "index and its time in the clip, in reading order:"]
    for i, c in enumerate(candidates):
        lines.append(f"  [{i}] t={float(c['t']):.2f}s")
    lines += ["",
              "For EACH frame answer: is the moment the note describes visible in it (\"hit\"), "
              "and where in that frame is the thing the note names (x, y as fractions of the "
              "frame — the point the effect should sit on; the centre if it belongs to the whole "
              "frame)? Keep t exactly as labelled.",
              "",
              'Answer: {"events": [{"t": seconds, "x": 0..1, "y": 0..1, "hit": true|false, '
              '"why": "a few words"}, …]} — one entry per frame, JSON only.']
    return system, "\n".join(lines)


def build_revise_prompt(effect: dict, note: str) -> tuple[str, str]:
    """`(system, prompt)` for an iteration: the current effect JSON and the note ("make
    them red and bigger", "one hit only, the big one"). Same answer shape as design."""
    current = {k: effect.get(k) for k in ("name", "why", "events", "overlay", "sound")}
    prompt = ("THE CURRENT EFFECT:\n" + json.dumps(current, indent=1)
              + f"\n\nTHE NOTE: {note.strip()}\n\n"
              "Return the whole effect again with the note applied — the same shape (name, why, "
              "events, overlay, sound). Keep every event's t, x and y unless the note asks to "
              "move, add or drop events. Answer with one JSON object only.")
    return _system_design(), prompt


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ask(system: str, prompt: str, images: list[Path] | None = None) -> Any:
    from roughcut import config, inference
    r = inference.complete(prompt, role=config.ROLE_JUDGE, images=list(images or ()), system=system)
    return inference.extract_json(r.raw or str(r.content))


def _reference_events(reference: dict, peaks: list[dict], onset: list[float], hz: float,
                      seg: dict) -> list[dict]:
    """The human's marks as events: x, y from the marks; t the nearest onset peak to the
    reference frame's time (within a second), else the frame's time itself."""
    t0, t1 = float(seg["in"]), float(seg["out"])
    t_ref = reference.get("t")
    t = float(t_ref) if t_ref is not None else (t0 + t1) / 2
    if peaks:
        nearest = min(peaks, key=lambda p: abs(p["t"] - t))
        if abs(nearest["t"] - t) <= 1.0:
            t = nearest["t"]
    elif onset:
        snapped = nearest_onset(onset, hz, t)
        if snapped is not None:
            t = snapped
    t = min(max(t, t0), max(t0, t1 - NUDGE_S))
    return [{"t": round(t, 3), "x": float(m[0]), "y": float(m[1])} for m in reference["marks"]]


def design(note: str, seg: dict, clip: dict, sidecar: dict | None, *,
           reference: dict | None = None, place: bool = False,
           proxy: Path | None = None, workdir: Path | None = None,
           segments: list[dict] | None = None, clips: dict | None = None,
           window: tuple[float, float] | None = None) -> dict:
    """The whole design step, through `roughcut.inference` (judge role): candidate
    impacts from `onset_peaks` over the shot's range — or over `window`, the human's
    own (t0, t1) in clip seconds, when given: Karl's first live effect put markers
    across a 20 s shot whose rocks were only at the end, because nothing let him say
    so — (or the reference's marks when there is one), the design call, validation,
    and — when `place` and a proxy are given — `frames_for` at the candidates,
    `contact_strip`, the placing call, and the events re-anchored from its answer
    (dropping candidates the model says are not hits, keeping at least one). Events
    outside the window are dropped (at least one stays). Returns a validated effect
    with `status: proposed`, `created` set, `window` when given, and
    `history: [{"note", "at"}]`."""
    sidecar = sidecar or {}
    onset = list((sidecar.get("tracks") or {}).get("onset") or [])
    hz = float(sidecar.get("frame_hz") or ONSET_HZ)
    t0, t1 = float(seg["in"]), float(seg["out"])
    if window is not None:
        w0, w1 = max(t0, float(window[0])), min(t1, float(window[1]))
        if w1 > w0:
            t0, t1 = w0, w1
            note = (f"{note}\n\nOnly between {t0:.2f}s and {t1:.2f}s of the clip: the editor marked "
                    f"that window, and there are no hits outside it.")
    peaks = onset_peaks(onset, hz, t0, t1)
    has_marks = bool(reference and reference.get("marks"))
    transcript = (clip or {}).get("transcript")
    if transcript is None:
        transcript = sidecar.get("transcript")
    system, prompt = build_design_prompt(note, seg, clip, peaks=peaks, transcript=transcript,
                                         reference=reference if has_marks else None,
                                         cut=segments)
    answer = _ask(system, prompt)
    if not isinstance(answer, dict):
        raise ValueError("the design answer is not an object")
    events = answer.get("events") if isinstance(answer.get("events"), list) else []
    if has_marks:
        # the drawing is the strongest signal: its marks are the anchors, the model
        # only designed the look
        anchored = _reference_events(reference, peaks, onset, hz, seg)
        for a, e in zip(anchored, events):
            if isinstance(e, dict):
                for k in ("strength", "label"):
                    if e.get(k) is not None:
                        a[k] = e[k]
        events = anchored
    elif not events:
        events = ([{"t": p["t"], "x": 0.5, "y": 0.5, "strength": p["strength"]} for p in peaks[:3]]
                  or [{"t": round((t0 + t1) / 2, 3), "x": 0.5, "y": 0.5}])
    edits_raw = answer.get("edits") if isinstance(answer.get("edits"), list) else None
    if not answer.get("overlay") and not answer.get("sound") and not edits_raw:
        raise ValueError("the design answer has no overlay, no sound and no edits")
    shot_id = answer.get("shot") if edits_raw and str(answer.get("shot") or "").startswith("new:") else seg.get("id")
    effect = {
        "id": new_id(), "shot": shot_id, "clip": answer.get("clip") if str(shot_id).startswith("new:") else seg["clip"],
        "edits": edits_raw,
        "name": answer.get("name") or "effect", "note": note,
        "why": answer.get("why") or "",
        "events": events, "overlay": answer.get("overlay"), "sound": answer.get("sound"),
        "status": "proposed",
        "limits": answer.get("limits") or None,
    }
    if window is not None and not str(shot_id).startswith("new:"):
        inside = [e for e in events if isinstance(e, dict) and e.get("t") is not None
                  and t0 <= float(e["t"]) <= t1]
        effect["events"] = inside or [{"t": max(t0, min(t1, float(events[0]["t"]))), "x": 0.5, "y": 0.5}] if events else effect["events"]
    effect = validate_effect(effect, segments or [seg], clips, keep_meta=False)
    if window is not None:
        effect["window"] = [round(t0, 3), round(t1, 3)]
    if place and proxy is not None and not has_marks:
        wd = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="fx_place_"))
        wd.mkdir(parents=True, exist_ok=True)
        times = [ev["t"] for ev in effect["events"]]
        frames = frames_for(Path(proxy), times, wd / "frames")
        labels = [f"[{i}] t={t:.2f}s" for i, t in enumerate(times)]
        strip = contact_strip(frames, labels, wd / "strip.jpg")
        psys, pprompt = build_place_prompt(note, effect, effect["events"], strip)
        placed = _ask(psys, pprompt, images=[strip])
        rows = placed.get("events") if isinstance(placed, dict) else None
        if isinstance(rows, list) and rows:
            kept = []
            for ev in effect["events"]:
                row = min((r for r in rows if isinstance(r, dict) and r.get("t") is not None),
                          key=lambda r: abs(float(r["t"]) - ev["t"]), default=None)
                if row is None or abs(float(row["t"]) - ev["t"]) > 0.5:
                    kept.append(ev)                       # unanswered: leave it as designed
                    continue
                new = dict(ev)
                try:
                    new["x"] = min(1.0, max(0.0, float(row.get("x", ev["x"]))))
                    new["y"] = min(1.0, max(0.0, float(row.get("y", ev["y"]))))
                except (TypeError, ValueError):
                    pass
                if isinstance(row.get("why"), str) and row["why"].strip() and not new.get("label"):
                    new["label"] = row["why"].strip()[:40]
                if row.get("hit") is False:
                    new["_drop"] = True
                kept.append(new)
            survivors = [e for e in kept if not e.get("_drop")]
            if not survivors:                             # keep at least one: the strongest
                survivors = [max(kept, key=lambda e: e.get("strength") or 0.0)]
            for e in survivors:
                e.pop("_drop", None)
            effect["events"] = survivors
            effect = validate_effect(effect, segments or [seg], clips, keep_meta=False)
    created = _now()
    effect["created"] = created
    effect["history"] = [{"note": note, "at": created}]
    return effect


def revise(effect: dict, note: str, segments: list[dict], clips: dict | None = None) -> dict:
    """One iteration: the revise call, validated, id and events kept unless the note
    changed them, `history` appended. Returns the new proposed effect."""
    system, prompt = build_revise_prompt(effect, note)
    answer = _ask(system, prompt)
    if not isinstance(answer, dict):
        raise ValueError("the revise answer is not an object")
    new = {k: v for k, v in effect.items() if k != "verify"}
    for k in ("name", "why", "overlay"):
        if answer.get(k) is not None:
            new[k] = answer[k]
    if "sound" in answer:
        new["sound"] = answer["sound"]
    if isinstance(answer.get("edits"), list):
        new["edits"] = answer["edits"]
    if isinstance(answer.get("events"), list) and answer["events"]:
        new["events"] = answer["events"]
    new["status"] = "proposed"
    new["note"] = note
    new = validate_effect(new, segments, clips)
    new["history"] = list(effect.get("history") or []) + [{"note": note, "at": _now()}]
    return new


# ---- the checks

def _decode_audio(part: Path):
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", str(part), "-map", "0:a:0", "-vn",
         "-f", "s16le", "-ac", "1", "-ar", "48000", "-"], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"audio decode failed: {r.stderr.decode(errors='replace')[-300:]}")
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float64) / 32768.0


def _rms_db(x) -> float:
    if len(x) == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(x * x)))
    return round(20 * math.log10(max(rms, 1e-6)), 2)


def audio_transient_at(part: Path, t: float, *, win_s: float = 0.04) -> dict:
    """Decode the part's audio around `t` (ffmpeg → s16le → numpy) and return
    `{"peak_db": …, "before_db": …, "rise_db": …}`: the RMS in ±win_s around t against
    the RMS of the 300 ms before it. A sound effect that landed shows as a rise."""
    sr = 48000
    x = _decode_audio(Path(part))
    c = int(round(t * sr))
    w = max(1, int(round(win_s * sr)))
    at = x[max(0, c - w):c + w]
    before = x[max(0, c - w - int(0.3 * sr)):max(0, c - w)]
    # the peak is the loudest 5 ms inside the window, not the window's mean: a click
    # that dies in 60 ms is a transient, and averaging it over 80 ms with the silence
    # before it is how a hit that clearly landed reads as "no rise"
    sub, hop = int(0.005 * sr), int(0.001 * sr)
    peak_db = max((_rms_db(at[i:i + sub]) for i in range(0, max(1, len(at) - sub + 1), hop)),
                  default=_rms_db(at))
    before_db = _rms_db(before)
    return {"peak_db": peak_db, "before_db": before_db, "rise_db": round(peak_db - before_db, 2)}


def _probe_wh(path: Path) -> tuple[int, int]:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height", "-of", "json", str(path)],
                       capture_output=True, text=True)
    s = json.loads(r.stdout)["streams"][0]
    return int(s["width"]), int(s["height"])


def _decode_frame(path: Path, t: float, w: int, h: int):
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{t:.4f}", "-i", str(path),
         "-map", "0:v:0", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True)
    need = w * h * 3
    if r.returncode != 0 or len(r.stdout) < need:
        raise RuntimeError(f"frame decode failed at {t:.2f}s: {r.stderr.decode(errors='replace')[-300:]}")
    return np.frombuffer(r.stdout[:need], dtype=np.uint8).reshape(h, w, 3)


def frame_change_at(part: Path, base: Path, t: float) -> dict:
    """Decode one frame from each at `t` and return `{"changed": fraction of pixels
    that differ by > 24/255 in any channel, "bbox": [x0, y0, x1, y1] fractions}`. An
    overlay that drew shows as a change; its bbox says where."""
    w, h = _probe_wh(Path(part))
    a = _decode_frame(Path(part), t, w, h).astype(np.int16)
    bw, bh = _probe_wh(Path(base))
    b = _decode_frame(Path(base), t, bw, bh).astype(np.int16)
    if (bw, bh) != (w, h):
        raise ValueError(f"part {w}x{h} and base {bw}x{bh} differ in size")
    mask = np.any(np.abs(a - b) > 24, axis=2)
    changed = float(mask.mean())
    if not mask.any():
        return {"changed": 0.0, "bbox": None}
    # the bbox is of the *dense* change — pixels whose four neighbours changed too —
    # so a few stray pixels of encode noise on a sharp edge do not stretch it across
    # the frame; only when nothing is dense does the raw mask stand in
    core = (mask[1:-1, 1:-1] & mask[:-2, 1:-1] & mask[2:, 1:-1]
            & mask[1:-1, :-2] & mask[1:-1, 2:])
    if core.any():
        ys, xs = np.nonzero(core)
        ys, xs = ys + 1, xs + 1
    else:
        ys, xs = np.nonzero(mask)
    bbox = [round(float(xs.min()) / w, 4), round(float(ys.min()) / h, 4),
            round(float(xs.max() + 1) / w, 4), round(float(ys.max() + 1) / h, 4)]
    return {"changed": round(changed, 6), "bbox": bbox}


def verify(effect: dict, seg: dict, *, onset: list[float] | None = None,
           hz: float = ONSET_HZ, part: Path | None = None, base: Path | None = None,
           impact: bool = True) -> dict:
    """The definition of done as a checklist the machine runs. Returns
    `{"ok": bool, "at": iso, "checks": [{"key", "label", "ok", "detail"}, …]}` with:
    `in_shot` (every event inside the shot's range), `in_frame` (every anchor inside
    0..1 with the sprite's box not more than half off the edge), `sync` (the sound and
    the overlay share `t`, and the sound is not longer than the overlay + 0.25 s),
    `on_onset` (each event within ONSET_TOL_S of an onset peak — only when `impact`
    and an onset track is given; otherwise reported as skipped, not failed),
    `audio_landed` and `picture_landed` (when `part` and `base` are given: a rise of
    ≥ 6 dB at each event and a change of ≥ 0.02 % of the frame at each event whose
    bbox contains the anchor). `ok` is every non-skipped check passing."""
    checks: list[dict] = []
    events = effect.get("events") or []
    overlay = effect.get("overlay") or {}
    sound = effect.get("sound")
    t0, t1 = float(seg["in"]), float(seg["out"])

    def add(key: str, label: str, ok: bool | None, detail: str) -> None:
        checks.append({"key": key, "label": label, "ok": ok, "detail": detail})

    # in_shot
    outside = [ev["t"] for ev in events if not (t0 <= ev["t"] < t1)]
    add("in_shot", "every event inside the shot",
        not outside and bool(events),
        (f"{len(events)} event(s) within {t0:.2f}–{t1:.2f}s" if not outside and events
         else f"outside the shot: {', '.join(f'{t:.2f}s' for t in outside)}" if outside
         else "no events"))

    # in_frame: the box is centred on the anchor + the pose's offset; dy is a width
    # fraction, so it is scaled by the 16:9 aspect to compare with y
    anim = overlay.get("anim") or {}
    sample_ts = {0.0} | {k[0] for tr in ("dx", "dy") for k in (anim.get(tr) or [])}
    bad = []
    for ev in events:
        for ts in sample_ts:
            pose = pose_at(overlay, ts)
            cx = ev["x"] + pose["dx"]
            cy = ev["y"] + pose["dy"] * 16 / 9
            if not (0 <= cx <= 1 and 0 <= cy <= 1):
                bad.append(f"{ev['t']:.2f}s at ({cx:.2f}, {cy:.2f})")
                break
    add("in_frame", "every anchor inside the frame", not bad,
        "every anchor inside 0..1 with the box no more than half off" if not bad
        else "off the frame: " + ", ".join(bad))

    # sync
    if sound:
        over = float(sound["duration"]) - (float(overlay.get("duration", 0)) + 0.25)
        add("sync", "the sound and the picture start together", over <= 0,
            f"sound {sound['duration']:.2f}s, picture {overlay.get('duration', 0):.2f}s, one t per event"
            if over <= 0 else f"the sound runs {over:.2f}s past the picture + 0.25s")
    else:
        add("sync", "the sound and the picture start together", None, "skipped: no sound")

    # on_onset
    if impact and onset:
        peaks = onset_peaks(onset, hz, t0 - 0.5, t1 + 0.5, top=MAX_EVENTS)
        off = []
        for ev in events:
            near = min((abs(p["t"] - ev["t"]) for p in peaks), default=None)
            if near is None or near > ONSET_TOL_S:
                snapped = nearest_onset(onset, hz, ev["t"])
                off.append(f"{ev['t']:.2f}s" + (f" (nearest sample {snapped:.2f}s)" if snapped is not None else ""))
        add("on_onset", "each impact on an onset peak", not off,
            f"every event within {ONSET_TOL_S * 1000:.0f} ms of a peak" if not off
            else "not on a peak: " + ", ".join(off))
    else:
        add("on_onset", "each impact on an onset peak", None,
            "skipped: " + ("the note names no impact" if not impact else "no onset track"))

    # audio_landed / picture_landed
    if part is not None and base is not None and Path(part).exists() and Path(base).exists():
        evs = events_in_shot(effect, seg)
        if sound:
            # two rises, either counts: the part against its own 300 ms before (a
            # transient is there) and the part against the base at the same instant
            # (the transient is *ours* — the footage's own rock strike does not fool it)
            low, seen = [], []
            for ev in evs:
                m = audio_transient_at(Path(part), ev["t_part"])
                b = audio_transient_at(Path(base), ev["t_part"])
                vs_base = round(m["peak_db"] - b["peak_db"], 2)
                seen.append(f"{ev['t']:.2f}s +{m['rise_db']:.1f}/{vs_base:+.1f} dB")
                if max(m["rise_db"], vs_base) < 6.0:
                    low.append(seen[-1])
            add("audio_landed", "the sound is in the proof", not low,
                f"≥ 6 dB rise at every event ({len(evs)}): " + ", ".join(seen) if not low
                else "no rise (vs before / vs base): " + ", ".join(low))
        else:
            add("audio_landed", "the sound is in the proof", None, "skipped: no sound")
        miss = []
        dur = float(overlay.get("duration", MIN_DURATION))
        for ev in evs:
            ts = ev["t_part"] + min(0.1, dur * 0.4)
            m = frame_change_at(Path(part), Path(base), ts)
            bb = m["bbox"]
            inside = bool(bb) and (bb[0] - 0.01 <= ev["x"] <= bb[2] + 0.01) and (bb[1] - 0.01 <= ev["y"] <= bb[3] + 0.01)
            if m["changed"] < 0.0002 or not inside:
                miss.append(f"{ev['t']:.2f}s changed {m['changed'] * 100:.3f}%"
                            + (f" bbox {bb}" if bb else " nothing drawn"))
        add("picture_landed", "the marker is in the proof", not miss,
            f"a change at every event's anchor ({len(evs)})" if not miss
            else "not drawn where expected: " + ", ".join(miss))
    else:
        add("audio_landed", "the sound is in the proof", None, "skipped: no proof render")
        add("picture_landed", "the marker is in the proof", None, "skipped: no proof render")

    ok = all(c["ok"] is not False for c in checks)
    return {"ok": ok, "at": _now(), "checks": checks}


# ---------------------------------------------------------------- files on disk

def fx_dir(work: Path, footage_name: str) -> Path:
    """Where a bin's effects live between proposal and acceptance, and their renders:
    `work/fx/<bin>/<id>.json`, `…/<id>/sound.wav`, `…/<id>/proof.mp4`, `…/<id>/strip.jpg`,
    `…/<id>/ref.png`."""
    d = work / "fx" / footage_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def load(d: Path, fx_id: str) -> dict | None:
    p = d / f"{fx_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def save(d: Path, effect: dict) -> Path:
    p = d / f"{effect['id']}.json"
    p.write_text(json.dumps(effect, indent=1), encoding="utf-8")
    return p
