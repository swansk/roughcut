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

# ---------------------------------------------------------------- the vocabulary

SHAPES = ("line", "circle", "ring", "rect", "polygon", "text")
TRACKS = ("scale", "opacity", "rotate", "dx", "dy")
LAYERS = ("tone", "noise", "click", "sweep")
WAVES = ("sine", "square", "saw", "triangle")
NOISES = ("white", "pink")
STATUSES = ("proposed", "accepted")

MAX_EVENTS = 24
MAX_SHAPES = 24
MAX_LAYERS = 6
MAX_KEYS = 16                # keyframes per track
MAX_TEXT = 24
MIN_DURATION, MAX_DURATION = 0.05, 3.0
MIN_SIZE, MAX_SIZE = 0.02, 0.6          # of the frame width
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
        out["text"] = text.strip()
        out["x"], out["y"] = _point(raw.get("at", [0, 0]), "text.at")
        out["h"] = _num(raw.get("h", 0.5), "text.h", 0.05, 1.5)
        out["bold"] = bool(raw.get("bold", True))
    return out


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
                        "duration": _num(f.get("duration", 0.08), "flash.duration", 0.02, 1.0)}
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
    return {"duration": _num(raw.get("duration", 0.18), "sound.duration", MIN_DURATION, MAX_DURATION),
            "gain_db": _num(raw.get("gain_db", -6), "sound.gain_db", MIN_GAIN_DB, MAX_GAIN_DB),
            "layers": [validate_layer(l) for l in layers]}


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
    shot = raw.get("shot")
    seg = next((s for s in segments if str(s.get("id")) == str(shot)), None)
    if seg is None:
        raise ValueError(f"effect names a shot that is not in the cut: {shot!r}")
    clip = raw.get("clip") or seg["clip"]
    if clip != seg["clip"]:
        raise ValueError(f"effect's clip {clip!r} is not the shot's clip {seg['clip']!r}")
    duration = None
    if clips and clip in clips:
        duration = clips[clip].get("duration") or clips[clip].get("duration_s")
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
        "events": validate_events(raw.get("events"), duration),
        "overlay": validate_overlay(raw.get("overlay")),
        "sound": validate_sound(raw.get("sound")),
        "status": status,
    }
    if keep_meta:
        for k in ("created", "reference", "verify", "history"):
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


def _draw_shape(layer, shape: dict, c: float, unit: float) -> None:
    """One shape onto its own transparent layer: `c` is the layer's centre (px), `unit`
    the pixels per box unit (half the box side, already supersampled)."""
    import math
    from PIL import ImageDraw

    d = ImageDraw.Draw(layer)
    colour = _rgba(shape["color"], shape["opacity"])

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
        font = _font(int(shape["h"] * unit), shape.get("bold", True))
        x, y = px((shape["x"], shape["y"]))
        d.text((x, y), shape["text"], fill=colour, font=font, anchor="mm")


def _sprite_image(overlay: dict, box_px: float, pose: dict):
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
            layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            _draw_shape(layer, shape, c, unit)
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
            sprite, ext = _sprite_image(overlay, box_px, pose)
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
    t = np.arange(n) / sr
    rng = np.random.default_rng(1234)              # the same patch renders the same bytes
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


def frames_for(proxy: Path, times: list[float], out_dir: Path, *, width: int = 640) -> list[Path]:
    """One JPEG per time from the proxy (`ffmpeg -ss t -i proxy -frames:v 1`), `width`
    px wide, named `t_<seconds>.jpg`. Returns the paths in the order of `times`."""
    raise NotImplementedError("lane fx-core")



def contact_strip(frames: list[Path], labels: list[str], out: Path, *, cols: int = 4) -> Path:
    """The frames tiled with their labels burnt in (PIL), for a placing call and for
    the proof look. Returns `out`."""
    raise NotImplementedError("lane fx-core")



def build_design_prompt(note: str, seg: dict, clip: dict, *, peaks: list[dict],
                        transcript: list[dict] | None = None,
                        reference: dict | None = None) -> tuple[str, str]:
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
    raise NotImplementedError("lane fx-core")



def build_place_prompt(note: str, effect: dict, candidates: list[dict], strip: Path) -> tuple[str, str]:
    """`(system, prompt)` for the placing call: the strip image (frames at each
    candidate time, labelled), the note, and the question — for each frame, is the
    impact visible, and where in the frame (x, y as fractions) is the thing the note
    names (the skis)? Answers `{"events": [{"t", "x", "y", "hit": bool, "why"}]}`."""
    raise NotImplementedError("lane fx-core")



def build_revise_prompt(effect: dict, note: str) -> tuple[str, str]:
    """`(system, prompt)` for an iteration: the current effect JSON and the note ("make
    them red and bigger", "one hit only, the big one"). Same answer shape as design."""
    raise NotImplementedError("lane fx-core")



def design(note: str, seg: dict, clip: dict, sidecar: dict | None, *,
           reference: dict | None = None, place: bool = False,
           proxy: Path | None = None, workdir: Path | None = None,
           segments: list[dict] | None = None, clips: dict | None = None) -> dict:
    """The whole design step, through `roughcut.inference` (judge role): candidate
    impacts from `onset_peaks` over the shot's range (or the reference's marks when
    there is one), the design call, validation, and — when `place` and a proxy are
    given — `frames_for` at the candidates, `contact_strip`, the placing call, and the
    events re-anchored from its answer (dropping candidates the model says are not
    hits, keeping at least one). Returns a validated effect with `status: proposed`,
    `created` set, and `history: [{"note", "at"}]`."""
    raise NotImplementedError("lane fx-core")



def revise(effect: dict, note: str, segments: list[dict], clips: dict | None = None) -> dict:
    """One iteration: the revise call, validated, id and events kept unless the note
    changed them, `history` appended. Returns the new proposed effect."""
    raise NotImplementedError("lane fx-core")



def audio_transient_at(part: Path, t: float, *, win_s: float = 0.04) -> dict:
    """Decode the part's audio around `t` (ffmpeg → s16le → numpy) and return
    `{"peak_db": …, "before_db": …, "rise_db": …}`: the RMS in ±win_s around t against
    the RMS of the 300 ms before it. A sound effect that landed shows as a rise."""
    raise NotImplementedError("lane fx-core")



def frame_change_at(part: Path, base: Path, t: float) -> dict:
    """Decode one frame from each at `t` and return `{"changed": fraction of pixels
    that differ by > 24/255 in any channel, "bbox": [x0, y0, x1, y1] fractions}`. An
    overlay that drew shows as a change; its bbox says where."""
    raise NotImplementedError("lane fx-core")



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
    raise NotImplementedError("lane fx-core")



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
