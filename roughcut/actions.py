"""The action vocabulary: every edit the pro tools make, as data the AI can write
(REDESIGN-GOAL dimension 15, Karl's K3 d).

Karl, 2026-10-16: *"While I CAN use the pro tools here (good), AI should also be capable
of all of it."* So the AI does not get a second, private way to change the cut. Its
answer is a list of the SAME operations the Timeline, Effect Controls, Lumetri, the
Effects bin and Export apply when a human uses them; `app/static/actions.js`
(`rcActions.apply`) runs each one through the very `window.tl` / `window.effects` /
colour / Export call the human's gesture makes, all inside ONE `tl.begin` … `tl.commit`
(one undo entry), and only after the editor has reviewed it op by op. This module is
the server's half: the schema the model is shown, `validate()` (each op checked against
the cut AS IT WILL BE after the ops before it — a razor's new half can be named by the
op after it), `describe()` (one line per op for the review panel) and `simulate()` (the
cut after the accepted ops, drawn as the ghost lane before anything lands).

Units. `at` / `t` / `by` (roll) are FILM seconds (the sequence's clock, after every
shot's speed). `in` / `out` / `by` (slip) are CLIP seconds (the source's own clock, the
only place a range lives). `id` is a shot id the server sent (`ids` a list of them). A
shot an earlier op in the same list makes is named `new:1`, `new:2`, … in the order the
ops that make shots (razor, insert, overwrite, append) appear in the list.

THE OPS (each maps to the human path named after the arrow)

  Timeline
    insert        {clip, in, out, at}        → tl.insertAt   Premiere's Insert (,): the
                                                 shot under `at` splits, the rest moves later
    overwrite     {clip, in, out, at}        → tl.overwrite  Premiere's Overwrite (.)
    append        {clip, in, out, after?}    → tl.insert     after shot `after`, else at the end
    move          {ids, before?}             → tl.move       before shot `before`, else to the end
    razor         {id?, at}                  → tl.split      the Razor (C) at film time `at`
                                                 (the shot under `at` when no id)
    ripple_delete {ids}                      → tl.remove     the gap closes
    trim          {id, in?, out?}            → tl.setRange   a ripple trim of either edge
    roll          {a, b, by}                 → the Rolling Edit tool (N): the edit between
                                                 shot a and the shot after it, b, moves `by`
                                                 film seconds (+ later); the film keeps its length
    slip          {id, by}                   → the Slip tool (Y): in and out move together by
                                                 `by` clip seconds; the length holds
    speed         {id, rate}                 → tl.setSpeed   0.1–4 (0.5 = half speed)
    marker        {at}                       → tl.addMarker  (M)
    in_out        {in?, out?}                → tl.setSeqIn / setSeqOut (I / O); null clears
    track_add     {kind: video|audio}        → tl.addTrack   Sequence › Add Track
    overlay       {clip, in, out, at, track?} → tl.addOverlay  a clip on V2 (or an added
                                                 V3…/A3… track) at film time `at`
  Effect Controls (a V1 shot's id; Motion and Opacity also take a V2+ clip's id)
    set           {id, prop, value}          → effects.set   the value as the panel shows it
    keys          {id, prop, keys: [[t, v], …]} → the stopwatch (effects.animate), then the
                                                 value typed at each `t` (film seconds from
                                                 the shot's first frame) — effects.set with
                                                 the playhead there
  Lumetri Color
    curve         {id, channel, points}      → effects.curve  channel master|r|g|b;
                                                 points [[x, y], …] on 0..1, x increasing
    wheel         {id, range, x, y, level?}  → effects.wheel  range shadows|midtones|
                                                 highlights; (x, y) the puck in the unit disc
                                                 (x = toward blue, y = toward red); level the
                                                 lightness slider -100..100
    look          {ids | "all", name, strength?}  the Creative look: a library look's name,
                                                 "none" (off), or "film" (a shot back to the
                                                 film's look); strength 0..1
    match         {ids, to: previous|reference, reference?}  Color Match: to the shot
                                                 before, or to the reference shot (`reference`
                                                 makes that shot the reference first)
  Transitions (the Effects bin's seven)
    transition    {a, b, kind, dur?}         → tl.addTransition  centred on the edit
                                                 between shot a and the next shot b
    fade          {id, edge: start|end, kind, dur?}  → tl.addFade  one-sided at a shot's edge
  Designed Effects
    design_effect {id, note}                 → the Designed Effects tool, on that shot, with
                                                 that note: its own proposal and review follow
  Export
    export        {preset, range: all|in_out, name?, size?, fps?, bitrate?}  → the Export
                                                 dialog's Export, its Frame Size / Frame
                                                 Rate / Bitrate set first; it runs only
                                                 when the editor accepts it

A whole-cut rewrite ("make it a 30 s teaser") is not an op list: the assistant answers
`kind: "plan"` and the existing Ask path proposes the new cut (roughcut/assist.py).

Nothing here writes anything. `validate` refuses an op with the reason a person reads
("shot g1234 is not in the cut", "Exposure is −0.415 to +0.585 stops; 2 is outside it")
and keeps going, so one bad op never sinks the rest of the answer.
"""

from __future__ import annotations

import copy
import math
from typing import Any, Iterable

from . import tracks as tracksmod

MAX_OPS = 24
MIN_LEN = 0.2                     # s — the timeline's shortest shot (tl MIN_LEN)
MIN_SPEED, MAX_SPEED = 0.1, 4.0
MAX_KEYS = 64
MAX_CURVE_POINTS = 16
NOTE_MAX = 400
FRAME_W, FRAME_H = 1920, 1080     # Effect Controls' Position is in a 1920×1080 frame

# The op names, by family, in the order the review panel and the prompt list them.
FAMILIES: dict[str, tuple[str, ...]] = {
    "timeline": ("insert", "overwrite", "append", "move", "razor", "ripple_delete", "trim",
                 "roll", "slip", "speed", "marker", "in_out", "track_add", "overlay"),
    "effect_controls": ("set", "keys"),
    "lumetri": ("curve", "wheel", "look", "match"),
    "transitions": ("transition", "fade"),
    "designed_effects": ("design_effect",),
    "export": ("export",),
}
OPS: tuple[str, ...] = tuple(op for ops in FAMILIES.values() for op in ops)
FAMILY_OF = {op: fam for fam, ops in FAMILIES.items() for op in ops}
# the ops that make a new V1 shot the ops after them may name as new:<k>
MAKES_SHOT = ("razor", "insert", "overwrite", "append")
# the ops that change the V1 cut (drawn as the ghost lane before they land)
CUT_OPS = ("insert", "overwrite", "append", "move", "razor", "ripple_delete", "trim", "roll",
           "slip", "speed")

# Effect Controls' properties as the panel shows them (app/static/effects.js PROPS):
# key → (label, lo, hi, unit). Lumetri Basic's Temperature / Tint / Exposure are the
# colour block's hand balance; the rest are the segment's `controls`.
SET_PROPS: dict[str, tuple[str, float, float, str]] = {
    "posx": ("Position X", -10.0 * FRAME_W, 10.0 * FRAME_W, " px"),
    "posy": ("Position Y", -10.0 * FRAME_H, 10.0 * FRAME_H, " px"),
    "scale": ("Scale", 0.0, 1000.0, ""),
    "rotation": ("Rotation", -3600.0, 3600.0, "°"),
    "opacity": ("Opacity", 0.0, 100.0, " %"),
    "volume_db": ("Volume", -60.0, 6.0, " dB"),
    "temperature": ("Temperature", -100.0, 100.0, ""),
    "tint": ("Tint", -100.0, 100.0, ""),
    "exposure": ("Exposure", -0.415, 0.585, ""),
    "contrast": ("Contrast", -100.0, 100.0, ""),
    "highlights": ("Highlights", -100.0, 100.0, ""),
    "shadows": ("Shadows", -100.0, 100.0, ""),
    "whites": ("Whites", -100.0, 100.0, ""),
    "blacks": ("Blacks", -100.0, 100.0, ""),
    "saturation": ("Saturation", 0.0, 200.0, ""),
    "hsl_hue": ("HSL key hue", 0.0, 360.0, "°"),
    "hsl_width": ("HSL hue range", 0.0, 180.0, "°"),
    "hsl_sat_lo": ("HSL sat min", 0.0, 100.0, ""),
    "hsl_sat_hi": ("HSL sat max", 0.0, 100.0, ""),
    "hsl_light_lo": ("HSL light min", 0.0, 100.0, ""),
    "hsl_light_hi": ("HSL light max", 0.0, 100.0, ""),
    "hsl_soft": ("HSL softness", 0.0, 100.0, ""),
    "hsl_shift": ("HSL hue shift", -180.0, 180.0, "°"),
    "hsl_saturation": ("HSL saturation", 0.0, 200.0, ""),
    "hsl_temperature": ("HSL temperature", -100.0, 100.0, ""),
    "hsl_tint": ("HSL tint", -100.0, 100.0, ""),
}
# the stopwatch's properties (keyframes); a V2+ clip takes these and nothing else
KEY_PROPS = ("posx", "posy", "scale", "rotation", "opacity", "volume_db")
OVERLAY_PROPS = ("posx", "posy", "scale", "rotation", "opacity")
CURVE_CHANNELS = {"master": "RGB master", "r": "red", "g": "green", "b": "blue"}
WHEELS = {"shadows": "Shadows", "midtones": "Midtones", "highlights": "Highlights"}
TRANSITION_KINDS = dict(tracksmod.TRANSITION_KINDS)
TRANSITION_MIN_S, TRANSITION_MAX_S = tracksmod.TRANSITION_MIN_S, tracksmod.TRANSITION_MAX_S
# Export Settings › Video (verdict 9, punch 5), the keys the Export dialog's selects carry
# and the server renders (app/server.py `export_choice`): Frame Size — the footage's own
# frame, or a height at the sequence's aspect; Frame Rate — the sequence's, or one of
# these as the rational the file is written at; a target bitrate in Mbps (VBR, 1 pass).
EXPORT_SIZES = {"match": "Match Sequence", "2160": "2160p", "1080": "1080p", "720": "720p",
                "540": "540p"}
EXPORT_RATES = {"match": None, "23.976": "24000/1001", "25": "25", "29.97": "30000/1001",
                "59.94": "60000/1001"}
EXPORT_BITRATE_MIN, EXPORT_BITRATE_MAX = 0.5, 200.0


class Refused(ValueError):
    """One op cannot be applied; the sentence is what the review panel says."""


# ---------------------------------------------------------------- the schema the model sees

def schema_text() -> str:
    """The op reference block for the model's prompt (roughcut/assist.py)."""
    props = ", ".join(f"{k} ({v[1]:g}..{v[2]:g}{v[3].strip() and ' ' + v[3].strip()})"
                      for k, v in SET_PROPS.items())
    kinds = ", ".join(f"{k} ({v})" for k, v in TRANSITION_KINDS.items())
    sizes = "|".join(f'"{k}"' for k in EXPORT_SIZES)
    rates = "|".join(f'"{k}"' for k in EXPORT_RATES)
    return f"""\
Every op is a JSON object with "op" and its fields. Film seconds (`at`, `t`, roll's `by`)
count from the start of the sequence, after each shot's speed; clip seconds (`in`, `out`,
slip's `by`) count in the source clip. `id` / `ids` name shots by the ids listed above. A
shot made by an earlier op in your list is `new:1`, `new:2`, … in the order the ops that
make shots (razor, insert, overwrite, append) appear. At most {MAX_OPS} ops.

Timeline
  {{"op":"insert","clip","in","out","at"}}       insert a clip range at film time `at`; the shot there splits, the rest moves later
  {{"op":"overwrite","clip","in","out","at"}}    lay a clip range over the film from `at` for its length
  {{"op":"append","clip","in","out","after"?}}   add a clip range after shot `after` (omit: at the end)
  {{"op":"move","ids":[…],"before"?}}            move shots before shot `before` (omit: to the end)
  {{"op":"razor","id"?,"at"}}                    cut a shot in two at film time `at` (≥ {MIN_LEN} s from its edges)
  {{"op":"ripple_delete","ids":[…]}}             remove shots; the gap closes
  {{"op":"trim","id","in"?,"out"?}}              new clip in and/or out point of a shot (≥ {MIN_LEN} s long, inside its clip)
  {{"op":"roll","a","b","by"}}                   move the edit between shot a and the next shot b by `by` film seconds (+ later)
  {{"op":"slip","id","by"}}                      slide a shot's in and out together by `by` clip seconds
  {{"op":"speed","id","rate"}}                   playback rate {MIN_SPEED:g}–{MAX_SPEED:g} (0.5 = slow motion at half speed)
  {{"op":"marker","at"}}                         a sequence marker
  {{"op":"in_out","in"?,"out"?}}                 the sequence In / Out (film seconds; null clears one)
  {{"op":"track_add","kind":"video"|"audio"}}    add a track (V3…, A3…)
  {{"op":"overlay","clip","in","out","at","track"?}}  a clip over the cut on V2 (or an added track) at film time `at`
Effect Controls
  {{"op":"set","id","prop","value"}}             one property, in the panel's units: {props}
  {{"op":"keys","id","prop","keys":[[t,v],…]}}   keyframes (t = film seconds from the shot's first frame, before its last frame; v in the prop's units) on {", ".join(KEY_PROPS)}
Lumetri Color
  {{"op":"curve","id","channel":"master"|"r"|"g"|"b","points":[[x,y],…]}}   2–{MAX_CURVE_POINTS} points on 0..1, x increasing; [[0,0],[1,1]] is no change
  {{"op":"wheel","id","range":"shadows"|"midtones"|"highlights","x","y","level"?}}   the puck inside the unit circle (x toward blue, y toward red, ~0.05–0.2 is a visible push); level -100..100
  {{"op":"look","ids":[…] or "all","name","strength"?}}   a look from the library, "none" for no look, "film" for a shot to take the film's; strength 0..1
  {{"op":"match","ids":[…],"to":"previous"|"reference","reference"?}}   Color Match to the shot before, or to the reference shot
Transitions
  {{"op":"transition","a","b","kind","dur"?}}    centred on the edit between shot a and the next shot b; kinds: {kinds}; dur {TRANSITION_MIN_S:g}–{TRANSITION_MAX_S:g} s (default 1)
  {{"op":"fade","id","edge":"start"|"end","kind","dur"?}}   one-sided at a shot's first or last frames (a fade from / to black is dip_to_black)
Designed Effects
  {{"op":"design_effect","id","note"}}           ask the effect designer for a drawn effect on a shot (titles, markers, graphics); its own review follows
Export
  {{"op":"export","preset","range":"all"|"in_out","name"?,"size"?,"fps"?,"bitrate"?}}   render a file (runs only once the editor accepts it). The preset's own frame, rate and quality unless you set them: size {sizes} (the frame height, at the sequence's aspect; "match" is the footage's own frame), fps {rates}, bitrate a target in Mbps ({EXPORT_BITRATE_MIN:g}–{EXPORT_BITRATE_MAX:g}). "A 720p review copy" is quick with "size":"720"
"""


# ---------------------------------------------------------------- small helpers

def _f(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _r(v: float, nd: int = 4) -> float:
    return round(float(v) + 0.0, nd)


def _jround(x: float) -> int:
    """JavaScript's Math.round (half up), so a frame count here is the board's."""
    return math.floor(x + 0.5)


def speed_of(seg: dict) -> float:
    s = _f(seg.get("speed"))
    return s if s is not None and MIN_SPEED <= s <= MAX_SPEED else 1.0


def dur(seg: dict) -> float:
    return (float(seg["out"]) - float(seg["in"])) / speed_of(seg)


def grid_rate(fps: float) -> float:
    """The edit grid: 29.97 is 30000/1001 (timeline.js gridRate)."""
    r = float(fps or 24.0)
    n = round(r * 1.001)
    if abs(r - round(r)) > 0.004 and abs(r - n / 1.001) < 0.004:
        return n / 1.001
    return r


def timecode(t: float, fps: float) -> str:
    """HH:MM:SS:FF, non-drop at the nominal rate (timeline.js `tc`)."""
    r = float(fps or 24.0)
    nominal = max(1, round(r))
    n = _jround(max(0.0, float(t or 0.0)) * r)
    ff = n % nominal
    n //= nominal
    ss = n % 60
    n //= 60
    mm = n % 60
    hh = n // 60
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


def _stem(clip: str) -> str:
    c = str(clip)
    if c.startswith("gen_"):
        parts = c.split("_")
        return f"a {parts[1]} slide" if len(parts) > 2 else "a generated clip"
    return c.rsplit(".", 1)[0]


def _secs(v: float) -> str:
    return f"{v:.2f} s"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _signed(v: float, nd: int = 2, unit: str = "") -> str:
    s = f"{abs(v):.{nd}f}"
    return f"{'−' if v < 0 else '+'}{s}{unit}"


def _num_text(prop: str, v: float) -> str:
    label, lo, hi, unit = SET_PROPS[prop]
    if prop == "exposure":
        return _signed(v, 2)
    if prop in ("temperature", "tint", "contrast", "highlights", "shadows", "whites",
                "blacks", "hsl_shift", "hsl_temperature", "hsl_tint") and v != 0:
        return _signed(v, 1 if abs(v) < 10 and v != int(v) else 0) + unit
    if prop == "volume_db":
        return _signed(v, 1, " dB") if v else "0 dB"
    return (f"{v:g}" if abs(v - round(v)) > 1e-9 else f"{int(round(v))}") + unit


# ---------------------------------------------------------------- the cut, simulated

class _Sim:
    """The sequence as the ops before this one leave it: V1's shots, the markers, In / Out,
    the tracks, V2+'s clips and the transitions — the facts an op is checked against."""

    def __init__(self, state: dict):
        # the sequence's timebase (verdict 10, punch 3); None while it has none — a
        # sequence that has never held a clip takes its first clip's (`adopt`), as the
        # timeline does
        self.timebase = tracksmod.timebase_value(state.get("fps"))
        self.fps = float(self.timebase or 24.0)
        self.rate = grid_rate(self.fps)
        self.segs: list[dict] = []
        for s in state.get("segments") or []:
            if not isinstance(s, dict) or s.get("clip") is None:
                continue
            try:
                a, b = float(s["in"]), float(s["out"])
            except (KeyError, TypeError, ValueError):
                continue
            seg = {"id": str(s.get("id") or ""), "clip": str(s["clip"]), "in": a, "out": b}
            if speed_of(s) != 1.0:
                seg["speed"] = speed_of(s)
            if isinstance(s.get("controls"), dict):
                seg["controls"] = copy.deepcopy(s["controls"])
            if s.get("why"):
                seg["why"] = str(s["why"])
            self.segs.append(seg)
        self.clips: dict[str, float | None] = {}
        self.clip_rates: dict[str, float | None] = {}
        for name, c in (state.get("clips") or {}).items():
            d = _f((c or {}).get("duration")) if isinstance(c, dict) else _f(c)
            self.clips[str(name)] = d
            self.clip_rates[str(name)] = (tracksmod.timebase_value(c.get("fps"))
                                          if isinstance(c, dict) else None)
        self.markers = sorted(t for t in (_f(m) for m in state.get("markers") or []) if t is not None)
        self.seq_in = _f(state.get("in"))
        self.seq_out = _f(state.get("out"))
        tr = state.get("tracks")
        names = set(tr.keys()) if isinstance(tr, dict) else set()
        self.tracks = set(tracksmod.NAMES) | {n for n in names if n in tracksmod.EXTRA}
        self.overlays = {str(o.get("id")): dict(o) for o in state.get("overlays") or []
                         if isinstance(o, dict) and o.get("id")}
        self.transitions = [dict(t) for t in state.get("transitions") or [] if isinstance(t, dict)]
        self.looks = set(state.get("looks") or [])
        self.presets: dict[str, str] = dict(state.get("presets") or {})
        colour = state.get("colour") if isinstance(state.get("colour"), dict) else {}
        self.reference = colour.get("reference")
        self.dead: set[str] = set()        # new:<k> of a creating op that was refused
        self._anon = 0
        # the cut as the editor sees it when the review opens: every line names a shot by
        # its number there and its clip ("shot 2 (CLIP_B)"), and a shot an op makes by
        # what made it ("shot 1's second half", "the new CLIP_C shot")
        self.orig = {s["id"]: (i, s["clip"]) for i, s in enumerate(self.segs)}
        self.made: dict[str, str] = {}

    # ---- reading
    def index(self, sid: str) -> int:
        for i, s in enumerate(self.segs):
            if s["id"] == sid:
                return i
        return -1

    def shot(self, sid: Any, what: str = "id") -> dict:
        if sid is None or sid == "":
            raise Refused(f"needs `{what}`: a shot id")
        sid = str(sid)
        if sid in self.dead:
            raise Refused(f"{sid} would be made by an op that was refused")
        i = self.index(sid)
        if i < 0:
            raise Refused(f"shot {sid} is not in the cut")
        return self.segs[i]

    def label(self, sid: str) -> str:
        sid = str(sid)
        if sid in self.orig:
            i, clip = self.orig[sid]
            return f"shot {i + 1} ({_stem(clip)})"
        if sid in self.made:
            return self.made[sid]
        return f"shot {sid}"

    def start(self, sid: str) -> float:
        t = 0.0
        for s in self.segs:
            if s["id"] == sid:
                return t
            t += dur(s)
        return -1.0

    def total(self) -> float:
        return sum(dur(s) for s in self.segs)

    def frame(self) -> float:
        return 1.0 / self.rate

    def on_grid(self, t: float) -> float:
        return _r(_jround(max(0.0, t) * self.rate) / self.rate)

    # ---- the edit grid (timeline.js gridRange / setRange), so the review's simulated cut
    # is the one the board's Accept makes: every edit point a whole sequence frame
    def adopt(self, clip: str) -> None:
        """A sequence with no timebase takes the first clip put in it (timeline.js
        `adoptRate`; server.keep_timebase)."""
        if self.timebase is not None:
            return
        r = self.clip_rates.get(clip)
        if r:
            self.timebase, self.fps, self.rate = r, float(r), grid_rate(r)

    def grid_range(self, a: float, b: float, spd: float, keep: str | None,
                   max_out: float | None) -> tuple[float, float]:
        """A clip range on the sequence's grid, as timeline.js `gridRange` makes it: a
        `keep` edge ('in' / 'out') stays exactly where it is; at 1× a moved edge goes to
        its nearest frame, retimed the shot's length in the film is whole frames at its
        speed; never past `max_out`, never shorter than MIN_LEN."""
        r, s = self.rate, spd or 1.0
        hi = max_out if max_out is not None else 1e9

        def top(x: float) -> float:
            return _r(math.floor(hi * r + 1e-6) / r) if x > hi + 1e-9 else x

        if s == 1:
            a2 = a if keep == "in" else self.on_grid(a)
            b2 = b if keep == "out" else top(self.on_grid(b))
            if b2 - a2 < MIN_LEN - 1e-9:
                if keep == "out":
                    a2 = _r(math.floor((b2 - MIN_LEN) * r + 1e-6) / r)
                else:
                    b2 = top(_r(math.ceil((a2 + MIN_LEN) * r - 1e-6) / r))
            return max(0.0, a2), b2
        n_min = max(1, math.ceil((MIN_LEN / s) * r - 1e-6))
        if keep == "out":
            n = max(n_min, _jround(((b - a) / s) * r))
            while n > n_min and b - (n / r) * s < -1e-9:
                n -= 1
            return _r(max(0.0, b - (n / r) * s)), b
        a2 = a if keep == "in" else self.on_grid(a)
        n = max(n_min, _jround(((b - a2) / s) * r))
        while n > n_min and a2 + (n / r) * s > hi + 1e-9:
            n -= 1
        return a2, _r(a2 + (n / r) * s)

    def set_range(self, seg: dict, tin: float | None, tout: float | None) -> tuple[float, float]:
        """The range timeline.js `setRange(id, tin, tout)` gives `seg`: clamped into its
        media, the untouched edge kept, on the grid."""
        d = self.clip_len(seg["clip"])
        dur_ = d if d is not None else 1e9
        a = seg["in"] if tin is None else float(tin)
        b = seg["out"] if tout is None else float(tout)
        a = min(max(a, 0.0), max(0.0, dur_ - MIN_LEN))
        b = min(max(b, a + MIN_LEN), dur_)
        keep = ("out" if tout is None else "in" if tin is None
                else "in" if a == seg["in"] else "out" if b == seg["out"] else None)
        return self.grid_range(a, b, speed_of(seg), keep, dur_)

    def clip_len(self, clip: str) -> float | None:
        return self.clips.get(clip)

    def anon(self) -> str:
        self._anon += 1
        return f"new:part{self._anon}"

    # ---- the timeline's arithmetic (timeline.js cutAt / splitPoint)
    def split_point(self, seg: dict, film_off: float) -> float:
        n = _jround(max(0.0, film_off) * self.rate)
        return _r(seg["in"] + (n / self.rate) * speed_of(seg))

    def cut_at(self, t: float, mode: str) -> int:
        start = 0.0
        for i, s in enumerate(self.segs):
            d = dur(s)
            if t <= start + 1e-6:
                return i
            if t < start + d - 1e-6:
                ct = self.split_point(s, t - start)
                head, tail = ct - s["in"], s["out"] - ct
                if head >= MIN_LEN - 1e-9 and tail >= MIN_LEN - 1e-9:
                    second = {**copy.deepcopy(s), "id": self.anon(), "in": ct}
                    second.get("controls", {}).pop("keys", None)
                    s["out"] = ct
                    self.segs.insert(i + 1, second)
                    return i + 1
                if mode == "in" and head >= MIN_LEN - 1e-9:
                    s["out"] = ct
                    return i + 1
                if mode == "out" and tail >= MIN_LEN - 1e-9:
                    s["in"] = ct
                    return i
                return i if (t - start) < d / 2 else i + 1
            start += d
        return len(self.segs)

    def start_of_index(self, i: int) -> float:
        return sum(dur(s) for s in self.segs[:i])


# ---------------------------------------------------------------- field checks

def _need(raw: dict, key: str) -> Any:
    if key not in raw or raw[key] is None:
        raise Refused(f"needs `{key}`")
    return raw[key]


def _number(raw: dict, key: str, lo: float, hi: float, what: str | None = None,
            unit: str = "") -> float:
    v = _f(_need(raw, key))
    if v is None:
        raise Refused(f"`{key}` is not a number: {raw.get(key)!r}")
    if not (lo - 1e-9 <= v <= hi + 1e-9):
        name = what or key
        raise Refused(f"{name} is {lo:g} to {hi:g}{unit}; {v:g} is outside it")
    return v


def _ids(raw: dict, key: str = "ids", sim: _Sim | None = None) -> list[str]:
    v = raw.get(key)
    if v is None and key == "ids" and raw.get("id") is not None:
        v = [raw["id"]]
    if isinstance(v, (str, int)):
        v = [v]
    if not isinstance(v, list) or not v:
        raise Refused(f"needs `{key}`: a list of shot ids")
    out: list[str] = []
    for x in v:
        sid = str(x)
        if sim is not None:
            sim.shot(sid, key)
        if sid not in out:
            out.append(sid)
    return out


def _clip_range(raw: dict, sim: _Sim) -> tuple[str, float, float]:
    clip = str(_need(raw, "clip"))
    if clip not in sim.clips:
        raise Refused(f"there is no clip {clip!r} in the project")
    a = _f(_need(raw, "in"))
    b = _f(_need(raw, "out"))
    if a is None or b is None:
        raise Refused("`in` and `out` must be numbers (clip seconds)")
    length = sim.clip_len(clip)
    if a < -1e-6:
        raise Refused(f"in {a:g} s is before the clip starts")
    if length is not None and b > length + 1e-3:
        raise Refused(f"out {b:g} s is past the end of {_stem(clip)} ({length:.2f} s)")
    if b - a < MIN_LEN - 1e-9:
        raise Refused(f"the range is shorter than {MIN_LEN} s")
    # on the sequence's grid, inside the clip's media (timeline.js `gridded`); an empty
    # sequence first takes this clip's timebase
    sim.adopt(clip)
    a, b = sim.grid_range(max(0.0, a), b if length is None else min(b, length), 1.0, None,
                          length)
    return clip, a, b


def _film_time(raw: dict, key: str, sim: _Sim, *, past_end: bool = False) -> float:
    t = _f(_need(raw, key))
    if t is None:
        raise Refused(f"`{key}` is not a number (film seconds)")
    end = sim.total()
    if t < -1e-6 or (not past_end and t > end + sim.frame() / 2):
        raise Refused(f"{key} {t:g} s is outside the sequence (0 to {end:.2f} s)")
    return sim.on_grid(min(max(0.0, t), end) if not past_end else max(0.0, t))


def _anchor(raw: dict, key: str, sim: _Sim) -> str | None:
    v = raw.get(key)
    if v is None or v == "":
        return None
    sim.shot(v, key)
    return str(v)


# ---------------------------------------------------------------- one op: check, describe, apply
#
# Each checker takes the raw op, the simulated cut and the op's placeholder (new:<k> for
# an op that makes a shot), returns the clean op and the line that describes it, and
# applies it to the simulation — so the next op is checked against the cut it will meet.

def _c_insert(raw: dict, sim: _Sim, ph: str | None) -> tuple[dict, str]:
    op = raw["op"]
    clip, a, b = _clip_range(raw, sim)
    at = _film_time(raw, "at", sim)
    clean = {"op": op, "clip": clip, "in": a, "out": b, "at": at, "makes": ph}
    words = "Insert" if op == "insert" else "Overwrite with"
    line = f"{words} {_stem(clip)} ({a:.2f}–{b:.2f} s) at {timecode(at, sim.fps)}"
    seg = {"id": ph, "clip": clip, "in": a, "out": b}
    if ph:
        sim.made[ph] = f"the new {_stem(clip)} shot"
    if op == "insert":
        i = sim.cut_at(at, "insert")
        sim.segs.insert(i, seg)
    else:
        i = sim.cut_at(at, "in")
        end = sim.cut_at(sim.start_of_index(i) + dur(seg), "out")
        sim.segs[i:max(i, end)] = [seg]
    return clean, line


def _c_append(raw: dict, sim: _Sim, ph: str | None) -> tuple[dict, str]:
    clip, a, b = _clip_range(raw, sim)
    after = _anchor(raw, "after", sim)
    clean = {"op": "append", "clip": clip, "in": a, "out": b, "after": after, "makes": ph}
    where = f"after {sim.label(after)}" if after else "at the end"
    line = f"Add {_stem(clip)} ({a:.2f}–{b:.2f} s) {where}"
    seg = {"id": ph, "clip": clip, "in": a, "out": b}
    if ph:
        sim.made[ph] = f"the new {_stem(clip)} shot"
    i = sim.index(after) + 1 if after else len(sim.segs)
    sim.segs.insert(i, seg)
    return clean, line


def _c_move(raw: dict, sim: _Sim, ph: str | None) -> tuple[dict, str]:
    ids = _ids(raw, "ids", sim)
    before = _anchor(raw, "before", sim)
    if before in ids:
        raise Refused("a shot cannot move before itself")
    moving = [s for s in sim.segs if s["id"] in ids]
    rest = [s for s in sim.segs if s["id"] not in ids]
    at = len(rest) if before is None else next(i for i, s in enumerate(rest) if s["id"] == before)
    new = rest[:at] + moving + rest[at:]
    if [s["id"] for s in new] == [s["id"] for s in sim.segs]:
        raise Refused("the shots are already there")
    who = ", ".join(sim.label(i) for i in ids)
    line = f"Move {who} " + (f"before {sim.label(before)}" if before else "to the end")
    sim.segs[:] = new
    return {"op": "move", "ids": ids, "before": before}, line


def _c_razor(raw: dict, sim: _Sim, ph: str | None) -> tuple[dict, str]:
    at = _film_time(raw, "at", sim)
    sid = raw.get("id")
    if sid in (None, ""):
        t0 = 0.0
        for s in sim.segs:
            if t0 - 1e-6 <= at < t0 + dur(s) - 1e-6:
                sid = s["id"]
                break
            t0 += dur(s)
        if sid in (None, ""):
            raise Refused(f"there is no shot under {timecode(at, sim.fps)}")
    seg = sim.shot(sid)
    start = sim.start(seg["id"])
    ct = sim.split_point(seg, at - start)
    if not (ct >= seg["in"] + MIN_LEN - 1e-9 and ct <= seg["out"] - MIN_LEN + 1e-9):
        if not (start - 1e-6 <= at <= start + dur(seg) + 1e-6):
            raise Refused(f"{timecode(at, sim.fps)} is not inside {sim.label(seg['id'])}")
        raise Refused(f"{timecode(at, sim.fps)} is within {MIN_LEN} s of an edge of "
                      f"{sim.label(seg['id'])}: nothing to cut off")
    line = f"Razor {sim.label(seg['id'])} at {timecode(at, sim.fps)}"
    second = {**copy.deepcopy(seg), "id": ph, "in": ct}
    second.get("controls", {}).pop("keys", None)
    seg["out"] = ct
    sim.segs.insert(sim.index(seg["id"]) + 1, second)
    if ph:
        base = sim.label(seg["id"])
        sim.made[ph] = f"{base.split(' (')[0]}'s second half" if seg["id"] in sim.orig else f"the second half of {base}"
    return {"op": "razor", "id": seg["id"], "at": at, "makes": ph}, line


def _c_ripple_delete(raw: dict, sim: _Sim, ph: str | None) -> tuple[dict, str]:
    ids = _ids(raw, "ids", sim)
    who = ", ".join(sim.label(i) for i in ids)
    line = f"Ripple delete {who}"
    sim.segs[:] = [s for s in sim.segs if s["id"] not in ids]
    return {"op": "ripple_delete", "ids": ids}, line


def _c_trim(raw: dict, sim: _Sim, ph: str | None) -> tuple[dict, str]:
    seg = sim.shot(raw.get("id"))
    if raw.get("in") is None and raw.get("out") is None:
        raise Refused("needs `in` or `out` (clip seconds)")
    length = sim.clip_len(seg["clip"])
    a = seg["in"] if raw.get("in") is None else _f(raw["in"])
    b = seg["out"] if raw.get("out") is None else _f(raw["out"])
    if a is None or b is None:
        raise Refused("`in` and `out` must be numbers (clip seconds)")
    if a < -1e-6:
        raise Refused(f"in {a:g} s is before {_stem(seg['clip'])} starts")
    if length is not None and b > length + 1e-3:
        raise Refused(f"out {b:g} s is past the end of {_stem(seg['clip'])} ({length:.2f} s)")
    if b - a < MIN_LEN - 1e-9:
        raise Refused(f"the shot would be shorter than {MIN_LEN} s")
    # the board's tl.setRange: the edge not named stays, the moved one on the grid
    a, b = sim.set_range(seg, None if raw.get("in") is None else max(0.0, a),
                         None if raw.get("out") is None else b)
    if abs(a - seg["in"]) < 1e-6 and abs(b - seg["out"]) < 1e-6:
        raise Refused(f"{sim.label(seg['id'])} is already {a:.2f}–{b:.2f} s")
    parts = []
    if abs(a - seg["in"]) >= 1e-6:
        parts.append(f"in {seg['in']:.2f} → {a:.2f} s")
    if abs(b - seg["out"]) >= 1e-6:
        parts.append(f"out {seg['out']:.2f} → {b:.2f} s")
    line = f"Trim {sim.label(seg['id'])}: {', '.join(parts)}"
    clean = {"op": "trim", "id": seg["id"]}
    if raw.get("in") is not None:
        clean["in"] = a
    if raw.get("out") is not None:
        clean["out"] = b
    seg["in"], seg["out"] = a, b
    return clean, line


def _c_roll(raw: dict, sim: _Sim, ph: str | None) -> tuple[dict, str]:
    L = sim.shot(raw.get("a"), "a")
    R = sim.shot(raw.get("b"), "b")
    if sim.index(R["id"]) != sim.index(L["id"]) + 1:
        raise Refused(f"{sim.label(R['id'])} does not come right after {sim.label(L['id'])}: "
                      "a roll moves the edit between two neighbours")
    by = _f(_need(raw, "by"))
    if by is None or abs(by) < 1e-6:
        raise Refused("`by` must be a non-zero number of film seconds")
    sl, sr = speed_of(L), speed_of(R)
    dl = sim.clip_len(L["clip"])
    lo = max((L["in"] + MIN_LEN - L["out"]) / sl, -R["in"] / sr)
    hi = min(((dl if dl is not None else 1e9) - L["out"]) / sl, (R["out"] - MIN_LEN - R["in"]) / sr)
    n = _jround(by * sim.rate)
    v = n / sim.rate
    if v > hi + 1e-9 or v < lo - 1e-9:
        room = hi if by > 0 else -lo
        raise Refused(f"the edit can roll at most {max(0.0, room):.2f} s "
                      f"{'later' if by > 0 else 'earlier'} (the media runs out)")
    line = (f"Roll the edit between {sim.label(L['id'])} and {sim.label(R['id'])} "
            f"by {_signed(v, 2, ' s')}")
    # actions.js applies a roll as two tl.setRange calls, each edge on the grid
    L["in"], L["out"] = sim.set_range(L, None, L["out"] + v * sl)
    R["in"], R["out"] = sim.set_range(R, R["in"] + v * sr, None)
    return {"op": "roll", "a": L["id"], "b": R["id"], "by": _r(v)}, line


def _c_slip(raw: dict, sim: _Sim, ph: str | None) -> tuple[dict, str]:
    seg = sim.shot(raw.get("id"))
    by = _f(_need(raw, "by"))
    if by is None or abs(by) < 1e-6:
        raise Refused("`by` must be a non-zero number of clip seconds")
    length = sim.clip_len(seg["clip"])
    lo, hi = -seg["in"], ((length if length is not None else 1e9) - seg["out"])
    if by < lo - 1e-6 or by > hi + 1e-6:
        room = hi if by > 0 else -lo
        raise Refused(f"{sim.label(seg['id'])} can slip at most {max(0.0, room):.2f} s "
                      f"{'later' if by > 0 else 'earlier'} in {_stem(seg['clip'])}")
    # whole frames (actions.js `toFrames`), applied as the board's tl.setRange
    n = _jround(by * sim.rate)
    if n / sim.rate > hi + 1e-9:
        n = math.floor(hi * sim.rate + 1e-9)
    if n / sim.rate < lo - 1e-9:
        n = math.ceil(lo * sim.rate - 1e-9)
    if n == 0:
        raise Refused(f"{sim.label(seg['id'])} cannot slip less than one frame")
    by = n / sim.rate
    line = f"Slip {sim.label(seg['id'])} by {_signed(by, 2, ' s')}"
    seg["in"], seg["out"] = sim.set_range(seg, seg["in"] + by, seg["out"] + by)
    return {"op": "slip", "id": seg["id"], "by": _r(by)}, line


def _c_speed(raw: dict, sim: _Sim, ph: str | None) -> tuple[dict, str]:
    seg = sim.shot(raw.get("id"))
    rate = _number(raw, "rate", MIN_SPEED, MAX_SPEED, "speed", "×")
    rate = round(rate, 2)
    if abs(rate - speed_of(seg)) < 1e-9:
        raise Refused(f"{sim.label(seg['id'])} already plays at {rate:g}×")
    line = f"{_cap(sim.label(seg['id']))}: Speed {round(rate * 100):g} %"
    if rate == 1.0:
        seg.pop("speed", None)
    else:
        seg["speed"] = rate
    # tl.setSpeed: the in stays, the shot's length in the film whole frames at the rate
    seg["in"], seg["out"] = sim.grid_range(seg["in"], seg["out"], rate, "in",
                                           sim.clip_len(seg["clip"]))
    return {"op": "speed", "id": seg["id"], "rate": rate}, line


def _c_marker(raw: dict, sim: _Sim, ph: str | None) -> tuple[dict, str]:
    at = _film_time(raw, "at", sim)
    if any(abs(m - at) < 0.5 / sim.fps for m in sim.markers):
        raise Refused(f"there is a marker at {timecode(at, sim.fps)} already")
    sim.markers = sorted(sim.markers + [at])
    return {"op": "marker", "at": at}, f"Marker at {timecode(at, sim.fps)}"


def _c_in_out(raw: dict, sim: _Sim, ph: str | None) -> tuple[dict, str]:
    if "in" not in raw and "out" not in raw:
        raise Refused("needs `in` and/or `out` (film seconds, null clears)")
    clean: dict = {"op": "in_out"}
    new_in, new_out = sim.seq_in, sim.seq_out
    if "in" in raw:
        new_in = None if raw["in"] is None else _film_time(raw, "in", sim)
        clean["in"] = new_in
    if "out" in raw:
        new_out = None if raw["out"] is None else _film_time(raw, "out", sim)
        clean["out"] = new_out
    if new_in is not None and new_out is not None and new_out <= new_in:
        raise Refused("the sequence Out must come after its In")
    bits = []
    if "in" in clean:
        bits.append(f"In {timecode(new_in, sim.fps)}" if new_in is not None else "In cleared")
    if "out" in clean:
        bits.append(f"Out {timecode(new_out, sim.fps)}" if new_out is not None else "Out cleared")
    sim.seq_in, sim.seq_out = new_in, new_out
    return clean, "Sequence " + ", ".join(bits)


def _c_track_add(raw: dict, sim: _Sim, ph: str | None) -> tuple[dict, str]:
    kind = raw.get("kind")
    if kind not in ("video", "audio"):
        raise Refused("`kind` must be video or audio")
    pool = tracksmod.EXTRA_AUDIO if kind == "audio" else tracksmod.EXTRA_VIDEO
    name = next((n for n in pool if n not in sim.tracks), None)
    if name is None:
        raise Refused(f"no more {kind} tracks: {pool[-1]} is the last")
    sim.tracks.add(name)
    return {"op": "track_add", "kind": kind}, f"Add a {kind} track ({name})"


def _c_overlay(raw: dict, sim: _Sim, ph: str | None) -> tuple[dict, str]:
    clip, a, b = _clip_range(raw, sim)
    t = _f(_need(raw, "at"))
    if t is None or t < 0:
        raise Refused("`at` must be a film time ≥ 0")
    at = sim.on_grid(t)
    track = str(raw.get("track") or "V2")
    if track != "V2" and track not in sim.tracks:
        raise Refused(f"there is no track {track} (add it first with track_add)")
    if track in ("V1", "A1", "A2"):
        raise Refused(f"{track} does not take a clip laid over the cut: use V2, V3… or A3…")
    line = f"{_stem(clip)} ({a:.2f}–{b:.2f} s) on {track} at {timecode(at, sim.fps)}"
    oid = sim.anon()
    sim.overlays[oid] = {"id": oid, "clip": clip, "in": a, "out": b, "at": at, "track": track}
    clean = {"op": "overlay", "clip": clip, "in": a, "out": b, "at": at}
    if track != "V2":
        clean["track"] = track
    return clean, line


def _target(raw: dict, sim: _Sim, overlay_ok: bool) -> tuple[str, dict, bool]:
    sid = raw.get("id")
    if sid is not None and overlay_ok and str(sid) in sim.overlays:
        return str(sid), sim.overlays[str(sid)], True
    seg = sim.shot(sid)
    return seg["id"], seg, False


def _who(sim: _Sim, sid: str, ov: bool) -> str:
    if ov:
        o = sim.overlays.get(sid) or {}
        return f"{o.get('track') or 'V2'} clip {_stem(o.get('clip', sid))}"
    return _cap(sim.label(sid))


def _c_set(raw: dict, sim: _Sim, ph: str | None) -> tuple[dict, str]:
    prop = raw.get("prop")
    if prop not in SET_PROPS:
        raise Refused(f"unknown property {prop!r} (allowed: {', '.join(SET_PROPS)})")
    sid, seg, ov = _target(raw, sim, overlay_ok=True)
    if ov and prop not in OVERLAY_PROPS:
        raise Refused(f"a clip on V2+ takes Motion and Opacity only, not {SET_PROPS[prop][0]}")
    label, lo, hi, unit = SET_PROPS[prop]
    v = _number(raw, "value", lo, hi, label, unit)
    v = _r(v, 3)
    line = f"{_who(sim, sid, ov)}: {label} {_num_text(prop, v)}"
    return {"op": "set", "id": sid, "prop": prop, "value": v}, line


def _c_keys(raw: dict, sim: _Sim, ph: str | None) -> tuple[dict, str]:
    prop = raw.get("prop")
    if prop not in KEY_PROPS:
        raise Refused(f"{prop!r} has no stopwatch (keyframes go on {', '.join(KEY_PROPS)})")
    sid, seg, ov = _target(raw, sim, overlay_ok=True)
    if ov and prop not in OVERLAY_PROPS:
        raise Refused("a clip on V2+ has no Volume")
    keys = raw.get("keys")
    if not isinstance(keys, list) or not keys:
        raise Refused("needs `keys`: [[t, value], …]")
    if len(keys) > MAX_KEYS:
        raise Refused(f"at most {MAX_KEYS} keyframes on a property")
    label, lo, hi, unit = SET_PROPS[prop]
    length = dur(seg)
    last = max(0.0, _jround((length - 1.0 / sim.rate) * sim.rate) / sim.rate)
    clean: list[list[float]] = []
    for k in keys:
        if not isinstance(k, (list, tuple)) or len(k) != 2:
            raise Refused("each key is [t, value]")
        t, v = _f(k[0]), _f(k[1])
        if t is None or v is None:
            raise Refused("each key is [t, value], both numbers")
        if t < -1e-6 or t > last + 0.5 / sim.rate:
            raise Refused(f"a key at {t:g} s is outside the shot (0 to {last:.2f} s from its first frame)")
        if not (lo - 1e-9 <= v <= hi + 1e-9):
            raise Refused(f"{label} is {lo:g} to {hi:g}{unit}; {v:g} is outside it")
        tg = _r(_jround(max(0.0, t) * sim.rate) / sim.rate, 6)
        if any(abs(c[0] - tg) < 0.5 / sim.rate for c in clean):
            raise Refused(f"two keys on the frame at {tg:.2f} s")
        clean.append([tg, _r(v, 3)])
    clean.sort(key=lambda x: x[0])
    vals = " → ".join(_num_text(prop, c[1]) for c in clean[:4]) + (" …" if len(clean) > 4 else "")
    span = f" over {clean[-1][0] - clean[0][0]:.2f} s" if len(clean) > 1 else f" at {clean[0][0]:.2f} s"
    line = f"{_who(sim, sid, ov)}: {label} keyframes {vals}{span} ({len(clean)} key{'s' if len(clean) != 1 else ''})"
    return {"op": "keys", "id": sid, "prop": prop, "keys": clean}, line


def _c_curve(raw: dict, sim: _Sim, ph: str | None) -> tuple[dict, str]:
    seg = sim.shot(raw.get("id"))
    ch = raw.get("channel", "master")
    if ch not in CURVE_CHANNELS:
        raise Refused(f"`channel` must be one of {', '.join(CURVE_CHANNELS)}")
    pts = raw.get("points")
    if not isinstance(pts, list) or not (2 <= len(pts) <= MAX_CURVE_POINTS):
        raise Refused(f"a curve is 2 to {MAX_CURVE_POINTS} points [[x, y], …]")
    clean: list[list[float]] = []
    for p in pts:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise Refused("each curve point is [x, y]")
        x, y = _f(p[0]), _f(p[1])
        if x is None or y is None or not (0 <= x <= 1) or not (0 <= y <= 1):
            raise Refused("curve points are on 0..1")
        if clean and x <= clean[-1][0] + 1e-9:
            raise Refused("curve points go left to right (x strictly increasing)")
        clean.append([_r(x), _r(y)])
    line = f"{_cap(sim.label(seg['id']))}: Lumetri {CURVE_CHANNELS[ch]} curve, {len(clean)} points"
    if clean == [[0.0, 0.0], [1.0, 1.0]]:
        line = f"{_cap(sim.label(seg['id']))}: Lumetri {CURVE_CHANNELS[ch]} curve back to straight"
    return {"op": "curve", "id": seg["id"], "channel": ch, "points": clean}, line


def _c_wheel(raw: dict, sim: _Sim, ph: str | None) -> tuple[dict, str]:
    seg = sim.shot(raw.get("id"))
    rng = raw.get("range")
    if rng not in WHEELS:
        raise Refused(f"`range` must be one of {', '.join(WHEELS)}")
    x, y = _f(raw.get("x", 0)), _f(raw.get("y", 0))
    if x is None or y is None:
        raise Refused("`x` and `y` must be numbers (the puck)")
    if math.hypot(x, y) > 1.0 + 1e-9:
        raise Refused("the puck must stay inside the wheel (x² + y² ≤ 1)")
    clean = {"op": "wheel", "id": seg["id"], "range": rng, "x": _r(x), "y": _r(y)}
    words = f"puck ({x:+.2f}, {y:+.2f})"
    if raw.get("level") is not None:
        lv = _number(raw, "level", -100, 100, "the lightness")
        clean["level"] = _r(lv, 1)
        words += f", lightness {_signed(lv, 0)}"
    line = f"{_cap(sim.label(seg['id']))}: Lumetri {WHEELS[rng]} wheel {words}"
    return clean, line


def _c_look(raw: dict, sim: _Sim, ph: str | None) -> tuple[dict, str]:
    ids_raw = raw.get("ids", raw.get("id"))
    name = raw.get("name")
    if name is None or name == "":
        raise Refused("needs `name`: a look, \"none\" or \"film\"")
    name = str(name)
    whole = ids_raw == "all"
    if name == "film" and whole:
        raise Refused("\"film\" is for a shot to take the film's look; the film's own look is a name or \"none\"")
    if name not in ("none", "film") and sim.looks and name not in sim.looks:
        raise Refused(f"there is no look {name!r} (the library has {', '.join(sorted(sim.looks))})")
    clean: dict = {"op": "look", "ids": "all" if whole else _ids({"ids": ids_raw}, "ids", sim), "name": name}
    if raw.get("strength") is not None:
        clean["strength"] = _r(_number(raw, "strength", 0, 1, "the look's strength"), 3)
    who = "The film" if whole else _cap(", ".join(sim.label(i) for i in clean["ids"]))
    what = ("no look" if name == "none" else "the film's look" if name == "film" else f"look {name}")
    line = f"{who}: {what}" + (f" at {clean['strength']:.2f}" if "strength" in clean else "")
    return clean, line


def _c_match(raw: dict, sim: _Sim, ph: str | None) -> tuple[dict, str]:
    ids = _ids(raw, "ids", sim)
    to = raw.get("to", "reference")
    if to not in ("previous", "reference"):
        raise Refused("`to` must be previous or reference")
    clean: dict = {"op": "match", "ids": ids, "to": to}
    ref = raw.get("reference")
    if ref not in (None, ""):
        sim.shot(ref, "reference")
        if str(ref) in ids:
            raise Refused("a shot cannot be matched to itself")
        clean["reference"] = str(ref)
        sim.reference = str(ref)
    who = _cap(", ".join(sim.label(i) for i in ids))
    if to == "previous":
        line = f"{who}: Color Match to the shot before"
    else:
        r = sim.reference if sim.reference and sim.index(sim.reference) >= 0 else (
            sim.segs[0]["id"] if sim.segs else None)
        line = f"{who}: Color Match to the reference" + (f", {sim.label(r)}" if r else "")
    return clean, line


def _transition_kind(raw: dict) -> str:
    kind = raw.get("kind")
    if kind not in TRANSITION_KINDS:
        raise Refused(f"unknown transition {kind!r} (the Effects bin has {', '.join(TRANSITION_KINDS)})")
    return kind


def _c_transition(raw: dict, sim: _Sim, ph: str | None) -> tuple[dict, str]:
    kind = _transition_kind(raw)
    a = sim.shot(raw.get("a"), "a")
    b = sim.shot(raw.get("b"), "b")
    if sim.index(b["id"]) != sim.index(a["id"]) + 1:
        raise Refused(f"{sim.label(b['id'])} does not come right after {sim.label(a['id'])}: "
                      "a transition sits on the edit between two neighbours")
    d = 1.0 if raw.get("dur") is None else _number(raw, "dur", TRANSITION_MIN_S, TRANSITION_MAX_S,
                                                   "a transition's length", " s")
    line = (f"{TRANSITION_KINDS[kind]} between {sim.label(a['id'])} and {sim.label(b['id'])}, "
            f"{d:.2f} s")
    return {"op": "transition", "a": a["id"], "b": b["id"], "kind": kind, "dur": _r(d, 3)}, line


def _c_fade(raw: dict, sim: _Sim, ph: str | None) -> tuple[dict, str]:
    kind = _transition_kind(raw)
    seg = sim.shot(raw.get("id"))
    edge = raw.get("edge")
    if edge not in ("start", "end"):
        raise Refused("`edge` must be start or end")
    d = 1.0 if raw.get("dur") is None else _number(raw, "dur", TRANSITION_MIN_S, TRANSITION_MAX_S,
                                                   "a fade's length", " s")
    line = f"{TRANSITION_KINDS[kind]} at the {edge} of {sim.label(seg['id'])}, {d:.2f} s"
    return {"op": "fade", "id": seg["id"], "edge": edge, "kind": kind, "dur": _r(d, 3)}, line


def _c_design_effect(raw: dict, sim: _Sim, ph: str | None) -> tuple[dict, str]:
    seg = sim.shot(raw.get("id"))
    note = str(raw.get("note") or "").strip()
    if not note:
        raise Refused("needs `note`: what the effect is")
    note = note[:NOTE_MAX]
    line = f"Designed effect on {sim.label(seg['id'])}: “{note}” (its own review follows)"
    return {"op": "design_effect", "id": seg["id"], "note": note}, line


# What editors call the NTSC rates: "30 fps" off a GoPro is 29.97, "60" 59.94, "24" 23.976.
_NOMINAL_RATES = {24.0: "23.976", 30.0: "29.97", 60.0: "59.94"}


def export_size_key(v: Any) -> str:
    """Frame Size from what the model wrote ("720", 720, "720p", "1280x720", "match")."""
    t = str(v).strip().lower().replace("×", "x")
    if t in ("match", "source", "sequence", "match sequence", "match source"):
        return "match"
    t = {"4k": "2160", "uhd": "2160", "fhd": "1080", "full hd": "1080"}.get(t, t)
    if "x" in t:
        t = t.split("x", 1)[1]
    t = t.rstrip("p").strip()
    if t in EXPORT_SIZES and t != "match":
        return t
    raise Refused(f"Frame Size is {', '.join(EXPORT_SIZES)} (got {v!r})")


def export_rate_key(v: Any) -> str:
    """Frame Rate from what the model wrote (29.97, "29.97", "30000/1001", 30, "match")."""
    t = str(v).strip().lower().removesuffix("fps").strip()
    if t in ("match", "source", "sequence"):
        return "match"
    if t in EXPORT_RATES:
        return t
    num, _, den = t.partition("/")
    f = _f(num)
    d = _f(den) if den else 1.0
    if f is not None and d:
        x = f / d
        for key, rational in EXPORT_RATES.items():
            if rational:
                n2, _, d2 = rational.partition("/")
                if abs(x - float(n2) / float(d2 or 1)) < 0.01:
                    return key
        nominal = _NOMINAL_RATES.get(round(x, 3))
        if nominal:
            return nominal
    raise Refused(f"Frame Rate is {', '.join(EXPORT_RATES)} (got {v!r})")


def export_bitrate_mbps(v: Any) -> float:
    """A target bitrate in Mbps (8, "8", "8 Mbps")."""
    t = str(v).strip().lower()
    for unit in ("mbps", "mb/s", "m"):
        t = t.removesuffix(unit).strip()
    f = _f(t)
    if f is None or not (EXPORT_BITRATE_MIN <= f <= EXPORT_BITRATE_MAX):
        raise Refused(f"`bitrate` is a target in Mbps, {EXPORT_BITRATE_MIN:g}–{EXPORT_BITRATE_MAX:g} "
                      f"(got {v!r})")
    return _r(f, 3)


def _c_export(raw: dict, sim: _Sim, ph: str | None) -> tuple[dict, str]:
    preset = raw.get("preset") or "quick"           # the dialog's own default
    if sim.presets and preset not in sim.presets:
        raise Refused(f"unknown preset {preset!r} (Export has {', '.join(sim.presets)})")
    rng = raw.get("range", "all")
    if rng not in ("all", "in_out"):
        raise Refused("`range` must be all or in_out")
    if rng == "in_out" and sim.seq_in is None and sim.seq_out is None:
        raise Refused("the sequence has no In / Out to export (set them first with in_out)")
    if not sim.segs:
        raise Refused("the sequence is empty: nothing to export")
    clean: dict = {"op": "export", "preset": str(preset), "range": rng}
    if raw.get("name"):
        clean["name"] = str(raw["name"]).strip()[:80]
    # Export Settings › Video (verdict 9, punch 5): what is not said is the preset's own
    video: list[str] = []
    if raw.get("size") not in (None, ""):
        clean["size"] = export_size_key(raw["size"])
        video.append("Match Sequence frame" if clean["size"] == "match"
                     else EXPORT_SIZES[clean["size"]])
    if raw.get("fps") not in (None, ""):
        clean["fps"] = export_rate_key(raw["fps"])
        video.append("the sequence's frame rate" if clean["fps"] == "match"
                     else f"{clean['fps']} fps")
    if raw.get("bitrate") not in (None, ""):
        clean["bitrate"] = export_bitrate_mbps(raw["bitrate"])
        video.append(f"{clean['bitrate']:g} Mbps target")
    label = sim.presets.get(str(preset), str(preset))
    where = "Entire Sequence" if rng == "all" else "Sequence In/Out"
    line = (f"Export {label}" + (f" at {', '.join(video)}" if video else "") + f", {where}"
            + (f" → {clean['name']}.mp4" if clean.get("name") else "") + ", on accept")
    return clean, line


CHECK = {
    "insert": _c_insert, "overwrite": _c_insert, "append": _c_append, "move": _c_move,
    "razor": _c_razor, "ripple_delete": _c_ripple_delete, "trim": _c_trim, "roll": _c_roll,
    "slip": _c_slip, "speed": _c_speed, "marker": _c_marker, "in_out": _c_in_out,
    "track_add": _c_track_add, "overlay": _c_overlay, "set": _c_set, "keys": _c_keys,
    "curve": _c_curve, "wheel": _c_wheel, "look": _c_look, "match": _c_match,
    "transition": _c_transition, "fade": _c_fade, "design_effect": _c_design_effect,
    "export": _c_export,
}
assert set(CHECK) == set(OPS)


# ---------------------------------------------------------------- the public face

def _run(ops: Any, state: dict) -> tuple[list[dict], list[dict], list[str], _Sim, list[int]]:
    sim = _Sim(state)
    accepted: list[dict] = []
    refused: list[dict] = []
    lines: list[str] = []
    index: list[int] = []              # each accepted op's index in `ops`
    if not isinstance(ops, list):
        return [], [{"i": None, "op": None, "why": "ops must be a list"}], [], sim, []
    made = 0
    for i, raw in enumerate(ops):
        name = raw.get("op") if isinstance(raw, dict) else None
        ph = None
        if name in MAKES_SHOT:
            made += 1
            ph = f"new:{made}"
        try:
            if not isinstance(raw, dict):
                raise Refused("not an op object")
            if name not in CHECK:
                raise Refused(f"unknown op {name!r}")
            if len(accepted) >= MAX_OPS:
                raise Refused(f"more than {MAX_OPS} ops in one answer")
            clean, line = CHECK[name](raw, sim, ph)
        except Refused as exc:
            if ph:
                sim.dead.add(ph)
            refused.append({"i": i, "op": name, "why": str(exc)})
            continue
        if clean.get("makes") is None:
            clean.pop("makes", None)
        accepted.append(clean)
        lines.append(line)
        index.append(i)
    return accepted, refused, lines, sim, index


def validate(ops: Any, state: dict) -> tuple[list[dict], list[dict]]:
    """The ops checked in order against the cut they will meet: `(accepted, refused)`.
    An accepted op is clean (numbers rounded onto the frame grid, ids strings, an op that
    makes a shot carrying `makes: "new:<k>"`); a refused one is `{i, op, why}` with `i`
    its index in `ops`. `state` is the sequence the model was shown:
    `{segments, clips: {name: {duration}}, fps, markers, in, out, tracks, overlays,
    transitions, colour, looks: [names], presets: {key: label}}`."""
    accepted, refused, _, _, _ = _run(ops, state)
    return accepted, refused


def describe(op: dict, state: dict) -> str:
    """One line for one op against `state` ("Razor shot 3 at 00:00:12:04"), or the
    reason it cannot be applied."""
    accepted, refused, lines, _, _ = _run([op], state)
    return lines[0] if lines else f"cannot apply: {refused[0]['why']}"


def simulate(ops: Any, state: dict) -> dict:
    """The sequence after the accepted ops — V1's shots (new ones `new:<k>`, a remainder
    an insert split off `new:part<n>`), the markers and the In / Out — for the ghost lane."""
    _, _, _, sim, _ = _run(ops, state)
    return {"segments": [dict(s) for s in sim.segs], "markers": sim.markers,
            "in": sim.seq_in, "out": sim.seq_out}


def check(ops: Any, state: dict) -> dict:
    """Everything the review needs in one pass: the accepted ops, a line per op, each
    accepted op's index in `ops` (`index`), the refusals and the simulated cut
    (`preview`, only when an op changes V1)."""
    accepted, refused, lines, sim, index = _run(ops, state)
    out = {"ops": accepted, "lines": lines, "refused": refused, "index": index,
           "families": sorted({FAMILY_OF[o["op"]] for o in accepted})}
    if any(o["op"] in CUT_OPS for o in accepted):
        out["preview"] = {"segments": [dict(s) for s in sim.segs]}
    return out


def state_from(payload: dict, *, looks: Iterable[str] = (), presets: dict | None = None) -> dict:
    """The validation state from GET /api/project's payload (server.project_payload)."""
    return {
        "segments": payload.get("segments") or [],
        # each clip's length, and its own rate when known (a first clip sets the timebase
        # of a sequence that has none)
        "clips": {k: {"duration": (v or {}).get("duration"),
                      **({"fps": v["fps"]} if (v or {}).get("fps") else {})}
                  for k, v in (payload.get("clips") or {}).items()},
        # the sequence's timebase, None while it has none (verdict 10, punch 3)
        "fps": payload.get("fps"),
        "markers": payload.get("markers") or [],
        "in": payload.get("in"), "out": payload.get("out"),
        "tracks": payload.get("tracks") or {},
        "overlays": payload.get("overlays") or [],
        "transitions": payload.get("transitions") or [],
        "colour": payload.get("colour") or {},
        "looks": list(looks),
        "presets": dict(presets or {}),
    }
