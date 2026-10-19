"""Video effects: Premiere's Effects panel › Video Effects (verdict 10, punch 8).

Premiere's standard effects, in its folders and with its parameters:

  Blur & Sharpen › Gaussian Blur   Blurriness, Blur Dimensions
  Blur & Sharpen › Sharpen         Sharpen Amount (wave 15, VIGNETTE; verdict 11 punch 7)
  Distort › Transform              Position, Scale, Rotation, Opacity (its own, apart from
                                   Motion, as Premiere has both)
  Transform › Crop                 Left, Top, Right, Bottom (%), Edge Feather

A shot carries them in its `controls` (validated by app/server.py `clean_controls`, which
calls `clean` and `clean_keys` here):

    controls.vfx = [{"id": "crop", "kind": "crop", "params": {"left": 10}, "off": true}, …]

  - the list is the shot's stack, top to bottom: the order the render applies them in,
    after Lumetri Color and before the shot's designed effects and its Motion / Opacity
    (Premiere renders the standard effects first and its fixed effects, Motion and
    Opacity, last);
  - `id` names the effect within its shot: the kind, then `_2`, `_3`… for a second and a
    third of the same kind (`effects.js` and `actions.py` mint them the same way);
  - `params` holds only what differs from the default; `off: true` is the fx toggle off
    (bypassed: kept, not drawn).

Keyframes ride `controls.keys` with Motion's, so a trim, a razor or a speed change moves
them with the media (timeline.js `keysWindow`, edits.py `anchor_keys`): the prop is
`<param>@<effect id>` — `left@crop`, `position_x@transform`, `blurriness@gaussian_blur_2`
— and the values are in the parameter's own units (the same `[[t, v], …]` schema, t film
seconds from the shot's first frame).

Units. Crop's sides are percent of the frame; Edge Feather and Blurriness are pixels of a
1920 × 1080 frame (Effect Controls' frame, as Position's numbers are), so the same numbers
look the same at any export size. Blurriness is the Gaussian's standard deviation. The
Transform's position is a fraction of the frame (0.5 = centre), as Motion's is.

THE FORMULAS (the Program's CSS in app/static/effects.js and the export's filters in
research/tools/assemble.py `vfx_graph` are both these):

  Crop       a point (x, y) of the frame (pixel centres, a W × H frame) keeps
             m = rx(x)·ry(y) of itself over black, with, for feather f = F·W/1920 px and
             the sides l = L·W/100 … r = W − R·W/100:
               rx(x) = clamp((x − l)/f, 0, 1) · clamp((r − x)/f, 0, 1)  (f = 0: a hard edge)
             and ry likewise on the top and bottom (a feather softens all four edges, an
             uncropped side's at the frame's own edge).
  Transform  a point p of the picture lands at (x·W, y·H) + s·R(θ)·(p − centre), clockwise
             θ, s = Scale / 100 (Motion's own maths); outside the moved picture is black,
             and the picture is Opacity % over black.
  Blur       a Gaussian of σ = Blurriness·W/1920 px across, down or both (Blur
             Dimensions); outside the frame reads black (Premiere's default, Repeat Edge
             Pixels off: the edges soften toward black).
  Sharpen    an unsharp mask on the frame's own pixels: with a = Sharpen Amount/100 and B
             the 3 × 3 binomial blur ([1 2 1]ᵀ[1 2 1]/16), out = x + a·(x − B∗x), per
             channel, the frame's edge pixels repeated past it — one 3 × 3 kernel,
             (1 + a)·δ − a·B: corners −a/16, sides −2a/16, centre 1 + 12a/16
             (`sharpen_kernel`). The Program runs it as an SVG feConvolveMatrix on R'G'B';
             the export as `convolution` on each Y'CbCr plane (the same linear map; the
             chroma at its own 4:2:0 size). On the frame's own pixel grid, as Premiere's
             is: the Program's monitor, smaller than the frame, shows it a little coarser.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

MAX_EFFECTS = 16
MAX_KEYS = 64
KEY_T_MAX = 24 * 3600.0
REF_W, REF_H = 1920, 1080          # Effect Controls' frame: Feather and Blurriness are its px


@dataclass(frozen=True)
class Param:
    key: str
    label: str
    lo: float
    hi: float
    default: float
    unit: str = ""
    places: int = 3                 # what the EDL keeps


@dataclass(frozen=True)
class Kind:
    kind: str
    name: str
    folder: str                     # its folder in Video Effects
    params: tuple[Param, ...]
    choices: dict[str, tuple[str, ...]] = field(default_factory=dict)   # first = default
    what: str = ""


KINDS: dict[str, Kind] = {
    "gaussian_blur": Kind(
        "gaussian_blur", "Gaussian Blur", "Blur & Sharpen",
        (Param("blurriness", "Blurriness", 0.0, 500.0, 0.0, " px", 3),),
        {"dimensions": ("both", "horizontal", "vertical")},
        "softens the picture: a Gaussian blur, across, down or both"),
    "sharpen": Kind(
        "sharpen", "Sharpen", "Blur & Sharpen",
        (Param("amount", "Sharpen Amount", 0.0, 500.0, 0.0, "", 3),),
        {}, "crispens the picture's edges: an unsharp mask, Sharpen Amount 100 adds the detail once more"),
    "transform": Kind(
        "transform", "Transform", "Distort",
        (Param("position_x", "Position X", -10.0, 10.0, 0.5, "", 6),
         Param("position_y", "Position Y", -10.0, 10.0, 0.5, "", 6),
         Param("scale", "Scale", 0.0, 1000.0, 100.0, "", 3),
         Param("rotation", "Rotation", -3600.0, 3600.0, 0.0, "°", 3),
         Param("opacity", "Opacity", 0.0, 100.0, 100.0, " %", 3)),
        {}, "moves, scales, turns and fades the picture, apart from Motion"),
    "crop": Kind(
        "crop", "Crop", "Transform",
        (Param("left", "Left", 0.0, 100.0, 0.0, " %", 3),
         Param("top", "Top", 0.0, 100.0, 0.0, " %", 3),
         Param("right", "Right", 0.0, 100.0, 0.0, " %", 3),
         Param("bottom", "Bottom", 0.0, 100.0, 0.0, " %", 3),
         Param("feather", "Edge Feather", 0.0, 1000.0, 0.0, " px", 3)),
        {}, "trims the frame's edges to black, with a soft edge if you feather it"),
}
CHOICE_LABELS = {"dimensions": {"both": "Horizontal and Vertical", "horizontal": "Horizontal",
                                "vertical": "Vertical"}}
ID_RE = re.compile(r"^[a-z_]{1,24}(?:_\d{1,2})?$")


def param(kind: str, key: str) -> Param | None:
    k = KINDS.get(kind)
    return next((p for p in k.params if p.key == key), None) if k else None


def key_prop(key: str, eid: str) -> str:
    """The `controls.keys` name of an effect's keyed parameter."""
    return f"{key}@{eid}"


def split_prop(prop: str) -> tuple[str, str] | None:
    """`left@crop` → ("left", "crop"); None for Motion's own props."""
    if "@" not in str(prop):
        return None
    key, _, eid = str(prop).partition("@")
    return key, eid


def next_id(kind: str, taken: set[str] | list[str]) -> str:
    """The id a new effect of `kind` gets on a shot that has `taken`: the kind, then
    `<kind>_2`, `<kind>_3`… (effects.js `vfxId` is the twin)."""
    taken = set(taken)
    if kind not in taken:
        return kind
    n = 2
    while f"{kind}_{n}" in taken:
        n += 1
    return f"{kind}_{n}"


def _num(v: Any, what: str) -> float:
    if isinstance(v, bool):
        raise ValueError(f"{what} must be a number")
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number") from None
    if not math.isfinite(f):
        raise ValueError(f"{what} must be a finite number")
    return f


def clean_params(kind: str, raw: Any) -> dict:
    """One effect's `params`, validated; only what differs from the default."""
    k = KINDS[kind]
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{k.name}: params must be an object")
    known = {p.key for p in k.params} | set(k.choices)
    extra = set(raw) - known
    if extra:
        raise ValueError(f"{k.name} has no parameter {sorted(extra)[0]!r}")
    out: dict = {}
    for p in k.params:
        if raw.get(p.key) is None:
            continue
        v = _num(raw[p.key], f"{k.name} {p.label}")
        if not (p.lo - 1e-9 <= v <= p.hi + 1e-9):
            raise ValueError(f"{k.name} {p.label} must be {p.lo:g}..{p.hi:g}")
        v = round(min(p.hi, max(p.lo, v)) + 0.0, p.places)
        if v != p.default:
            out[p.key] = v
    for name, options in k.choices.items():
        v = raw.get(name)
        if v is None:
            continue
        if v not in options:
            raise ValueError(f"{k.name} {name} must be one of {', '.join(options)}")
        if v != options[0]:
            out[name] = v
    return out


def clean(raw: Any) -> list[dict] | None:
    """A shot's `controls.vfx`, validated; None when it has no effect."""
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("vfx must be a list of effects")
    if len(raw) > MAX_EFFECTS:
        raise ValueError(f"at most {MAX_EFFECTS} video effects on a shot")
    out: list[dict] = []
    seen: set[str] = set()
    for e in raw:
        if not isinstance(e, dict):
            raise ValueError("each video effect is an object {id, kind, params?, off?}")
        extra = set(e) - {"id", "kind", "params", "off"}
        if extra:
            raise ValueError(f"a video effect has no field {sorted(extra)[0]!r}")
        kind = e.get("kind")
        if kind not in KINDS:
            raise ValueError(f"unknown video effect {kind!r} (the bin has {', '.join(KINDS)})")
        eid = e.get("id")
        if not (isinstance(eid, str) and ID_RE.match(eid)):
            raise ValueError(f"a {KINDS[kind].name} needs an id like {kind!r}")
        if eid in seen:
            raise ValueError(f"two video effects are both {eid!r}")
        seen.add(eid)
        item: dict = {"id": eid, "kind": kind}
        params = clean_params(kind, e.get("params"))
        if params:
            item["params"] = params
        off = e.get("off")
        if off not in (None, False, True):
            raise ValueError("off must be true or false")
        if off:
            item["off"] = True
        out.append(item)
    return out or None


def clean_keys(raw: Any, effects: list[dict] | None) -> dict | None:
    """The effects' keyframes in `controls.keys` (props `<param>@<id>`), validated. A key
    whose effect is not on the shot any more is dropped (its effect was cleared); a key on
    a parameter its kind does not have, or out of its range, is an error."""
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise ValueError("keys must be an object of {prop: [[t, v], ...]}")
    kinds = {e["id"]: e["kind"] for e in effects or []}
    out: dict = {}
    for prop, lst in raw.items():
        sp = split_prop(prop)
        if sp is None:
            raise ValueError(f"{prop!r} is not a video effect's parameter")
        key, eid = sp
        if eid not in kinds:
            continue
        p = param(kinds[eid], key)
        if p is None:
            raise ValueError(f"{KINDS[kinds[eid]].name} has no keyframes for {key!r}")
        if not isinstance(lst, list):
            raise ValueError(f"keys.{prop} must be a list of [t, v]")
        if len(lst) > MAX_KEYS:
            raise ValueError(f"keys.{prop} has more than {MAX_KEYS} keys")
        clean_list: list = []
        for k in lst:
            if not (isinstance(k, list) and len(k) == 2):
                raise ValueError(f"keys.{prop}: each key is [t, v]")
            t = _num(k[0], f"keys.{prop} t")
            v = _num(k[1], f"keys.{prop} value")
            if not (0.0 <= t <= KEY_T_MAX):
                raise ValueError(f"keys.{prop}: t must be 0..{KEY_T_MAX:g} s from the shot's start")
            if not (p.lo - 1e-9 <= v <= p.hi + 1e-9):
                raise ValueError(f"keys.{prop}: value must be {p.lo:g}..{p.hi:g}")
            t = round(t, 6)
            if clean_list and t <= clean_list[-1][0]:
                raise ValueError(f"keys.{prop}: times must be sorted and distinct")
            clean_list.append([t, round(min(p.hi, max(p.lo, v)), 6 if key.startswith("position") else 3)])
        if clean_list:
            out[prop] = clean_list
    return out or None


# ---------------------------------------------------------------- reading a shot's stack

def stack(controls: dict | None) -> list[dict]:
    """The shot's video effects, top to bottom, bypassed ones included."""
    v = (controls or {}).get("vfx")
    return [e for e in v if isinstance(e, dict) and e.get("kind") in KINDS] if isinstance(v, list) else []


def value(effect: dict, key: str) -> float | str:
    """A parameter's static value (keys aside): the effect's own, else the default."""
    k = KINDS[effect["kind"]]
    params = effect.get("params") or {}
    if key in k.choices:
        return params.get(key, k.choices[key][0])
    p = param(effect["kind"], key)
    return float(params.get(key, p.default))


def keys_of(controls: dict | None, effect: dict, key: str) -> list[tuple[float, float]] | None:
    raw = ((controls or {}).get("keys") or {}).get(key_prop(key, effect["id"]))
    if not raw:
        return None
    return sorted(((float(t), float(v)) for t, v in raw), key=lambda q: q[0]) or None


def describe(controls: dict | None) -> str:
    """One phrase for the render log: the stack as it is drawn."""
    bits = []
    for e in stack(controls):
        k = KINDS[e["kind"]]
        vals = []
        for p in k.params:
            ks = keys_of(controls, e, p.key)
            v = value(e, p.key)
            if ks:
                vals.append(f"{p.key} keyed ×{len(ks)}")
            elif v != p.default:
                vals.append(f"{p.key} {v:g}")
        for name, options in k.choices.items():
            if value(e, name) != options[0]:
                vals.append(f"{name} {value(e, name)}")
        bits.append(f"{k.name}{' (off)' if e.get('off') else ''}"
                    + (f" {', '.join(vals)}" if vals else ""))
    return "; ".join(bits)


def sharpen_kernel(amount: float) -> list[float]:
    """Sharpen's 3 × 3 kernel, row by row, for a Sharpen Amount (THE formula above)."""
    a = max(0.0, float(amount)) / 100.0
    c, e = -a / 16.0, -2.0 * a / 16.0
    return [c, e, c, e, 1.0 + 12.0 * a / 16.0, e, c, e, c]
