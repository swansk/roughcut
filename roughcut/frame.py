"""The shape of a cut's frame: ONE source of truth (wave 25, lane VERTICAL; WORDS-22, FINISH-14).

A cut has a frame shape: 16:9 (today's, the default; every cut with no `frame` key reads as it),
9:16 and 1:1. The EDL keeps it as `frame` — the string `"9:16"` or `"1:1"`, absent for 16:9 —
beside the sequence's timebase `fps`. Everything that draws or measures the frame reads it
through here:

  * the Program monitor (app/static/frame.js is this file's twin; a test runs both on the same
    numbers),
  * the renderer (research/tools/assemble.py: the frame's size and the fill-and-crop of a
    shot),
  * the effects check (roughcut/fx.py, fxcheck.py: the frame the words are measured against,
    the proof's frame),
  * the Export dialog's sizes (app/server.py),
  * the prompt-box AI's "vertical version" (roughcut/actions.py).

The reframe. A shot in a 9:16 or 1:1 frame is FILLED, not pillarboxed: the largest window of
the source that has the frame's aspect, scaled to the frame. The shot's Effect Controls move
that window (`controls.reframe_x`, `reframe_y`: 0..100 %, 50 the centre, the share of the free
room to the left / above the window) and shrink it (`reframe_zoom`: 100..400 %). `window()`
is the one function both the monitor and the file take their crop from.
"""

from __future__ import annotations

from typing import Any

DEFAULT = "16:9"
SHAPES = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1)}
LABELS = {"16:9": "Landscape 16:9", "9:16": "Vertical 9:16", "1:1": "Square 1:1"}
# the controls a shot's reframe is kept in (server.CONTROL_RANGES has their ranges)
REFRAME_KEYS = ("reframe_x", "reframe_y", "reframe_zoom")
ZOOM_MIN, ZOOM_MAX = 100.0, 400.0


def shape_value(raw: Any) -> str | None:
    """A frame shape from a request ("9:16", "9x16", " Vertical "), or None when it is not one."""
    if raw is None or isinstance(raw, bool):
        return None
    t = str(raw).strip().lower().replace("x", ":").replace("/", ":")
    if t in ("vertical", "portrait", "reel", "reels", "story", "tiktok", "shorts"):
        return "9:16"
    if t in ("square",):
        return "1:1"
    if t in ("landscape", "horizontal", "wide", "widescreen"):
        return "16:9"
    return t if t in SHAPES else None


def validate(raw: Any) -> str | None:
    """A save's `frame`: None leaves the stored shape; else the shape, or a ValueError."""
    if raw is None:
        return None
    v = shape_value(raw)
    if v is None:
        raise ValueError(f"frame {raw!r} is not a frame shape ({', '.join(SHAPES)})")
    return v


def shape_of(edl: dict | None) -> str:
    """The cut's shape: what it stores, 16:9 when it stores none (or something unreadable)."""
    v = shape_value((edl or {}).get("frame"))
    return v or DEFAULT


def aspect(shape: str | None) -> float:
    w, h = SHAPES.get(shape or DEFAULT, SHAPES[DEFAULT])
    return w / h


def is_wide(shape: str | None) -> bool:
    """True for today's 16:9: nothing about such a cut changes."""
    return (shape or DEFAULT) == DEFAULT


def reshape(shape: str | None, w: int, h: int) -> tuple[int, int]:
    """The frame of `shape` whose SHORT side is the 16:9 frame `w × h`'s height — Quick's
    1920×1080 becomes 1080×1920 (9:16) or 1080×1080 (1:1) — the sizes the Export dialog
    names. 16:9 is returned as it came."""
    s = shape or DEFAULT
    if s == DEFAULT:
        return int(w), int(h)
    short = int(h)
    if s == "1:1":
        return short, short
    long = max(2, int(round(short * 16 / 9 / 2)) * 2)
    return short, long                      # 9:16: narrow and tall


def proof_size(shape: str | None) -> tuple[int, int]:
    """The frame the effects' proof and its free check are made on: 720 on the short side."""
    return reshape(shape, 1280, 720)


def reframe_of(controls: dict | None) -> tuple[float, float, float]:
    """A shot's reframe as `(x, y, zoom)`: x and y fractions (0..1, 0.5 the centre), zoom a
    factor (1..4)."""
    c = controls or {}

    def num(k: str, d: float) -> float:
        try:
            return float(c.get(k, d))
        except (TypeError, ValueError):
            return d
    x = min(1.0, max(0.0, num("reframe_x", 50.0) / 100.0))
    y = min(1.0, max(0.0, num("reframe_y", 50.0) / 100.0))
    z = min(ZOOM_MAX, max(ZOOM_MIN, num("reframe_zoom", 100.0))) / 100.0
    return x, y, z


def window(src_w: float, src_h: float, shape: str | None, x: float = 0.5, y: float = 0.5,
           zoom: float = 1.0) -> tuple[float, float, float, float]:
    """The part of a `src_w × src_h` picture the frame shows, as `(left, top, width, height)`
    in fractions of the picture. At zoom 1 it is the largest window with the frame's aspect
    (the shot filled); a zoom shrinks it about its own size; `x` and `y` slide it through
    the room that leaves, 0 at the left / top edge of the picture, 1 at the right / bottom."""
    sa = (src_w / src_h) if src_h else 16 / 9
    fa = aspect(shape)
    if sa >= fa:
        ww, wh = fa / sa, 1.0
    else:
        ww, wh = 1.0, sa / fa
    z = max(1.0, float(zoom))
    ww, wh = ww / z, wh / z
    return (min(1.0, max(0.0, x)) * (1 - ww), min(1.0, max(0.0, y)) * (1 - wh), ww, wh)


def fill_filters(w: int, h: int, x: float, y: float, zoom: float) -> list[str]:
    """The ffmpeg filters after the cover scale (`scale=w:h:force_original_aspect_ratio=
    increase`, which the caller made) that cut `window()` out of it: the zoom, then a crop
    of the frame's size at the window's place. Expressed on the picture's own size, so they
    need no source dimensions; the same numbers as `window` (a window of fraction ww of a
    picture iw wide is w px, left edge x·(iw − w))."""
    out: list[str] = []
    if zoom > 1.0 + 1e-9:
        out.append(f"scale=w='trunc(iw*{zoom:.6f}/2)*2':h='trunc(ih*{zoom:.6f}/2)*2'")
    out.append(f"crop={w}:{h}:x='(iw-{w})*{x:.6f}':y='(ih-{h})*{y:.6f}'")
    return out
