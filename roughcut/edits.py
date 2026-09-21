"""Edits the model can propose to the cut itself (INTAKE M13).

Karl, 2026-09-20: *"I am more concerned about the effect tool itself, it needs to be able
to make changes like this and even broader ones like slow motion or extension or creating
new clips etc."* An effect (roughcut/fx.py) draws over a shot and adds sound; it cannot
lengthen a shot, slow it, or put a new clip in front of it. This module is the closed
vocabulary for those changes — **operations on the cut**, keyed by shot id, in clip
seconds — with validation and a pure `apply_ops` that turns a list of them into the new
segments. Nothing here writes a file: the server applies a validated list on Accept
(one write, one undo entry), makes any generated clip it names, and mints ids for the
new shots. The model never writes ffmpeg, filenames or ids; it writes operations.

Two facts the rest of the app learns from this module:

- **A shot has a `speed`** (0.1–4, absent = 1). Its length in the film is
  `(out − in) / speed`; the monitor plays it at that rate; the render retimes the part.
  `dur(seg)` is the one place that arithmetic lives on the Python side.
- **A generated clip is a real file** named `gen_<kind>_<key>.mp4` under
  `work/generated/<bin>/` — black, a colour, or a freeze frame taken from a shot — made by
  the server when an `apply` names it, and listed among the project's clips so the
  monitor plays it and the render cuts it like any other. `generated_name()` is
  deterministic from the parameters, so the same slide is one file however often it is
  proposed.

The ops (every one names a `shot` by id unless said otherwise; times are clip seconds):

  {"op": "extend",    "shot", "in": Δs, "out": Δs}          move the in and/or out point by
                                                            a delta (negative in = earlier)
  {"op": "set_range", "shot", "in": t, "out": t}            the range outright
  {"op": "split",     "shot", "at": t}                      two shots; the second is new
  {"op": "speed",     "shot", "rate": 0.1–4, "from": t?, "to": t?}
                                                            the whole shot, or a range of it
                                                            (split around it, the middle
                                                            piece takes the rate)
  {"op": "generate",  "kind": "black"|"colour"|"still", "seconds": 0.2–30,
                      "color": "#rrggbb"?, "from_shot": id?, "at": t? (still: the frame),
                      "before": id | null (null = the end) | "after": id}
                                                            a new clip in the cut
  {"op": "freeze",    "shot", "at": t, "seconds": 0.2–30}   a freeze frame inside a shot:
                                                            split at t, a still of that
                                                            frame between the halves
  {"op": "insert",    "clip", "in": t, "out": t, "before": id | null | "after": id}
                                                            footage into the cut
  {"op": "remove",    "shot"}
  {"op": "move",      "shot", "before": id | null | "after": id}

New shots get placeholder ids `new:1`, `new:2`, … in the result, which the server maps
to minted ids and reports back as `id_map`, so a proposal that names them (an effect on
the generated slide) can be re-keyed.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

OPS = ("extend", "set_range", "split", "speed", "generate", "freeze", "insert", "remove", "move")
KINDS = ("black", "colour", "still")
MIN_SPEED, MAX_SPEED = 0.1, 4.0
MIN_GEN_S, MAX_GEN_S = 0.2, 30.0
MIN_SHOT_S = 0.2                 # a piece shorter than this is a mistake, not a shot
MAX_OPS = 24
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


# ---------------------------------------------------------------- the one arithmetic

def speed_of(seg: dict) -> float:
    try:
        s = float(seg.get("speed") or 1.0)
    except (TypeError, ValueError):
        return 1.0
    return s if MIN_SPEED <= s <= MAX_SPEED else 1.0


def dur(seg: dict) -> float:
    """The shot's length in the film: its clip range at its speed."""
    return (float(seg["out"]) - float(seg["in"])) / speed_of(seg)


def validate_speed(v: Any) -> float | None:
    """A segment's `speed` field as the server keeps it: None for 1 (absent), else a
    float in range. Raises ValueError."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"speed is not a number: {v!r}")
    if not (MIN_SPEED <= f <= MAX_SPEED):
        raise ValueError(f"speed {f} is out of range ({MIN_SPEED}–{MAX_SPEED})")
    return None if abs(f - 1.0) < 1e-9 else round(f, 3)


# ---------------------------------------------------------------- generated clips

def generated_name(kind: str, seconds: float, *, color: str | None = None,
                   from_clip: str | None = None, at: float | None = None) -> str:
    """`gen_<kind>_<key>.mp4`, the same for the same parameters."""
    key = hashlib.sha1(
        f"{kind}|{seconds:.2f}|{color or ''}|{from_clip or ''}|{'' if at is None else f'{at:.2f}'}".encode()
    ).hexdigest()[:8]
    return f"gen_{kind}_{key}.mp4"


def is_generated(clip: str) -> bool:
    return str(clip).startswith("gen_")


# ---------------------------------------------------------------- validation

def _num(v: Any, name: str, lo: float, hi: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} is not a number: {v!r}")
    if not (lo <= f <= hi):
        raise ValueError(f"{name} out of range: {f} (allowed {lo}–{hi})")
    return round(f, 3)


def _shot(ops_seen: dict[str, dict], sid: Any, name: str) -> dict:
    seg = ops_seen.get(str(sid))
    if seg is None:
        raise ValueError(f"{name} names a shot that is not in the cut: {sid!r}")
    return seg


def _clip_len(clips: dict | None, clip: str) -> float | None:
    if not clips or clip not in clips:
        return None
    c = clips[clip]
    d = c.get("duration") if isinstance(c, dict) else None
    try:
        return float(d) if d else None
    except (TypeError, ValueError):
        return None


def _anchor(raw: dict, by_id: dict[str, dict], name: str) -> tuple[str | None, str | None]:
    """`before` (an id, or None for the end) or `after` (an id). Exactly one."""
    has_before = "before" in raw
    after = raw.get("after")
    if after is not None:
        _shot(by_id, after, f"{name}.after")
        return None, str(after)
    if has_before:
        before = raw.get("before")
        if before is None:
            return None, None            # the end
        _shot(by_id, before, f"{name}.before")
        return str(before), None
    return None, None                    # unsaid: the end


def validate_ops(raw: Any, segments: list[dict], clips: dict | None = None) -> list[dict]:
    """The list of operations, checked against the cut (`segments`, with ids) and the
    clips (for their lengths). Raises ValueError with a sentence. Returns clean ops.
    Ops are checked in order against the cut as it would be after the ones before
    (a split's second half can be spoken of as `new:1`), by running `apply_ops`."""
    if not isinstance(raw, list) or not (1 <= len(raw) <= MAX_OPS):
        raise ValueError(f"edits needs 1–{MAX_OPS} operations")
    clean: list[dict] = []
    for i, op in enumerate(raw):
        if not isinstance(op, dict):
            raise ValueError(f"edit {i + 1} is not an object")
        kind = op.get("op")
        if kind not in OPS:
            raise ValueError(f"edit {i + 1}: unknown op {kind!r} (allowed {', '.join(OPS)})")
        out: dict[str, Any] = {"op": kind}
        if kind in ("extend", "set_range", "split", "speed", "freeze", "remove", "move"):
            if op.get("shot") in (None, ""):
                raise ValueError(f"edit {i + 1} ({kind}) needs a shot")
            out["shot"] = str(op["shot"])
        if kind == "extend":
            out["in"] = _num(op.get("in", 0.0), "extend.in", -600, 600)
            out["out"] = _num(op.get("out", 0.0), "extend.out", -600, 600)
            if not out["in"] and not out["out"]:
                raise ValueError(f"edit {i + 1}: extend moves nothing")
        elif kind == "set_range":
            out["in"] = _num(op.get("in"), "set_range.in", 0, 1e6)
            out["out"] = _num(op.get("out"), "set_range.out", 0, 1e6)
            if out["out"] - out["in"] < MIN_SHOT_S:
                raise ValueError(f"edit {i + 1}: set_range is shorter than {MIN_SHOT_S}s")
        elif kind == "split":
            out["at"] = _num(op.get("at"), "split.at", 0, 1e6)
        elif kind == "speed":
            out["rate"] = _num(op.get("rate"), "speed.rate", MIN_SPEED, MAX_SPEED)
            if op.get("from") is not None or op.get("to") is not None:
                out["from"] = _num(op.get("from"), "speed.from", 0, 1e6)
                out["to"] = _num(op.get("to"), "speed.to", 0, 1e6)
                if out["to"] - out["from"] < MIN_SHOT_S:
                    raise ValueError(f"edit {i + 1}: the speed range is shorter than {MIN_SHOT_S}s")
        elif kind == "generate":
            g = op.get("kind")
            if g not in KINDS:
                raise ValueError(f"edit {i + 1}: unknown generate kind {g!r} (allowed {', '.join(KINDS)})")
            out["kind"] = g
            out["seconds"] = _num(op.get("seconds", 2.0), "generate.seconds", MIN_GEN_S, MAX_GEN_S)
            if g == "colour":
                c = op.get("color", "#000000")
                if not isinstance(c, str) or not _HEX.match(c):
                    raise ValueError(f"edit {i + 1}: generate.color is not #rrggbb")
                out["color"] = c.lower()
            if g == "still":
                if op.get("from_shot") in (None, ""):
                    raise ValueError(f"edit {i + 1}: a still needs from_shot and at")
                out["from_shot"] = str(op["from_shot"])
                out["at"] = _num(op.get("at"), "generate.at", 0, 1e6)
            if "before" in op:
                out["before"] = None if op["before"] is None else str(op["before"])
            if op.get("after") is not None:
                out["after"] = str(op["after"])
        elif kind == "freeze":
            out["at"] = _num(op.get("at"), "freeze.at", 0, 1e6)
            out["seconds"] = _num(op.get("seconds", 1.5), "freeze.seconds", MIN_GEN_S, MAX_GEN_S)
        elif kind == "insert":
            clip = op.get("clip")
            if not isinstance(clip, str) or not clip:
                raise ValueError(f"edit {i + 1}: insert needs a clip")
            if clips is not None and clip not in clips:
                raise ValueError(f"edit {i + 1}: insert names a clip the project does not have: {clip!r}")
            out["clip"] = clip
            out["in"] = _num(op.get("in"), "insert.in", 0, 1e6)
            out["out"] = _num(op.get("out"), "insert.out", 0, 1e6)
            if out["out"] - out["in"] < MIN_SHOT_S:
                raise ValueError(f"edit {i + 1}: insert is shorter than {MIN_SHOT_S}s")
            if "before" in op:
                out["before"] = None if op["before"] is None else str(op["before"])
            if op.get("after") is not None:
                out["after"] = str(op["after"])
        elif kind == "move":
            if "before" in op:
                out["before"] = None if op["before"] is None else str(op["before"])
            if op.get("after") is not None:
                out["after"] = str(op["after"])
            if "before" not in out and "after" not in out:
                raise ValueError(f"edit {i + 1}: move needs before or after")
        if isinstance(op.get("why"), str) and op["why"].strip():
            out["why"] = op["why"].strip()[:200]
        clean.append(out)
    # the whole list must apply
    apply_ops(segments, clips, clean)
    return clean


# ---------------------------------------------------------------- apply (pure)

def _copy(seg: dict) -> dict:
    return {k: v for k, v in seg.items()}


def apply_ops(segments: list[dict], clips: dict | None, ops: list[dict]) -> dict:
    """The cut after the ops, without touching anything: `{"segments": [...],
    "id_map": {"new:1": None, …}, "generated": [{"clip", "kind", "seconds", "color",
    "from_clip", "at"}, …], "changed": [ids], "words": [one line per op]}`. New shots
    carry placeholder ids `new:n`; `id_map` lists them (values None — the server fills
    them in when it mints). A generated clip's `clip` name is deterministic. Raises
    ValueError when an op does not fit the cut as it stands at that point."""
    segs = [_copy(s) for s in segments]
    by_id: dict[str, dict] = {str(s.get("id")): s for s in segs}
    id_map: dict[str, None] = {}
    generated: list[dict] = []
    changed: list[str] = []
    words: list[str] = []
    counter = [0]

    def fresh() -> str:
        counter[0] += 1
        pid = f"new:{counter[0]}"
        id_map[pid] = None
        return pid

    def index_of(sid: str) -> int:
        for i, s in enumerate(segs):
            if str(s.get("id")) == sid:
                return i
        raise ValueError(f"shot {sid!r} is not in the cut")

    def clip_len(clip: str) -> float | None:
        n = _clip_len(clips, clip)
        if n is None and is_generated(clip):
            for g in generated:
                if g["clip"] == clip:
                    return g["seconds"]
        return n

    def place(seg: dict, op: dict, name: str) -> None:
        before, after = _anchor(op, by_id, name)
        if after is not None:
            segs.insert(index_of(after) + 1, seg)
        elif before is not None:
            segs.insert(index_of(before), seg)
        else:
            segs.append(seg)
        by_id[str(seg["id"])] = seg

    def split_at(seg: dict, at: float, name: str) -> dict:
        if not (seg["in"] + MIN_SHOT_S <= at <= seg["out"] - MIN_SHOT_S):
            raise ValueError(f"{name}: {at:.2f}s is not inside the shot with {MIN_SHOT_S}s to spare "
                             f"({seg['in']:.2f}–{seg['out']:.2f})")
        second = _copy(seg)
        second["in"] = round(at, 3)
        second["id"] = fresh()
        second.pop("why", None)
        seg["out"] = round(at, 3)
        segs.insert(index_of(str(seg["id"])) + 1, second)
        by_id[second["id"]] = second
        return second

    for op in ops:
        kind = op["op"]
        if kind == "extend":
            seg = _shot(by_id, op["shot"], "extend")
            new_in = round(float(seg["in"]) + op["in"], 3)
            new_out = round(float(seg["out"]) + op["out"], 3)
            n = clip_len(seg["clip"])
            if new_in < 0:
                raise ValueError(f"extend: the in-point would be before the clip starts "
                                 f"({new_in:.2f}s); the clip has {float(seg['in']):.2f}s of head")
            if n is not None and new_out > n + 1e-6:
                raise ValueError(f"extend: the out-point would pass the clip's end ({n:.2f}s)")
            if new_out - new_in < MIN_SHOT_S:
                raise ValueError("extend: the shot would be shorter than a shot can be")
            seg["in"], seg["out"] = new_in, new_out
            changed.append(str(seg["id"]))
            words.append(f"extend shot {seg['clip']}: in {op['in']:+.2f}s, out {op['out']:+.2f}s")
        elif kind == "set_range":
            seg = _shot(by_id, op["shot"], "set_range")
            n = clip_len(seg["clip"])
            if n is not None and op["out"] > n + 1e-6:
                raise ValueError(f"set_range: {op['out']:.2f}s passes the clip's end ({n:.2f}s)")
            seg["in"], seg["out"] = op["in"], op["out"]
            changed.append(str(seg["id"]))
            words.append(f"set shot {seg['clip']} to {op['in']:.2f}–{op['out']:.2f}s")
        elif kind == "split":
            seg = _shot(by_id, op["shot"], "split")
            second = split_at(seg, op["at"], "split")
            changed += [str(seg["id"]), second["id"]]
            words.append(f"split shot {seg['clip']} at {op['at']:.2f}s")
        elif kind == "speed":
            seg = _shot(by_id, op["shot"], "speed")
            target = seg
            if "from" in op:
                f, t = op["from"], op["to"]
                if f < seg["in"] - 1e-6 or t > seg["out"] + 1e-6:
                    raise ValueError(f"speed: the range {f:.2f}–{t:.2f}s is not inside the shot "
                                     f"({seg['in']:.2f}–{seg['out']:.2f})")
                if f > seg["in"] + MIN_SHOT_S:
                    target = split_at(seg, f, "speed")
                if t < target["out"] - MIN_SHOT_S:
                    split_at(target, t, "speed")
            target["speed"] = validate_speed(op["rate"])
            if target["speed"] is None:
                target.pop("speed", None)
            changed.append(str(target["id"]))
            words.append(f"{seg['clip']} at {op['rate']:g}× " + (f"from {op['from']:.2f} to {op['to']:.2f}s" if "from" in op else "for the whole shot"))
        elif kind in ("generate", "freeze"):
            if kind == "freeze":
                src = _shot(by_id, op["shot"], "freeze")
                second = split_at(src, op["at"], "freeze")
                g = {"kind": "still", "seconds": op["seconds"], "color": None,
                     "from_clip": src["clip"], "at": op["at"]}
                name = generated_name("still", g["seconds"], from_clip=g["from_clip"], at=g["at"])
                g["clip"] = name
                generated.append(g)
                seg = {"clip": name, "in": 0.0, "out": g["seconds"], "id": fresh(),
                       "why": f"freeze frame of {src['clip']} at {op['at']:.2f}s"}
                segs.insert(index_of(second["id"]), seg)
                by_id[seg["id"]] = seg
                changed.append(seg["id"])
                words.append(f"freeze {src['clip']} at {op['at']:.2f}s for {op['seconds']:g}s")
            else:
                g = {"kind": op["kind"], "seconds": op["seconds"], "color": op.get("color"),
                     "from_clip": None, "at": None}
                if op["kind"] == "still":
                    src = _shot(by_id, op["from_shot"], "generate.from_shot")
                    if not (src["in"] - 1e-6 <= op["at"] <= src["out"] + 1e-6):
                        raise ValueError("generate: the still's frame is not inside from_shot")
                    g["from_clip"], g["at"] = src["clip"], op["at"]
                name = generated_name(g["kind"], g["seconds"], color=g["color"],
                                      from_clip=g["from_clip"], at=g["at"])
                g["clip"] = name
                if not any(x["clip"] == name for x in generated):
                    generated.append(g)
                label = {"black": "black", "colour": f"colour {g['color']}", "still": "a still"}[g["kind"]]
                seg = {"clip": name, "in": 0.0, "out": g["seconds"], "id": fresh(),
                       "why": op.get("why") or f"{label} slide, {g['seconds']:g}s"}
                place(seg, op, "generate")
                changed.append(seg["id"])
                words.append(f"a {label} clip of {g['seconds']:g}s")
        elif kind == "insert":
            n = clip_len(op["clip"])
            if n is not None and op["out"] > n + 1e-6:
                raise ValueError(f"insert: {op['out']:.2f}s passes the clip's end ({n:.2f}s)")
            seg = {"clip": op["clip"], "in": op["in"], "out": op["out"], "id": fresh(),
                   "why": op.get("why") or ""}
            place(seg, op, "insert")
            changed.append(seg["id"])
            words.append(f"insert {op['clip']} {op['in']:.2f}–{op['out']:.2f}s")
        elif kind == "remove":
            seg = _shot(by_id, op["shot"], "remove")
            segs.pop(index_of(str(seg["id"])))
            by_id.pop(str(seg["id"]), None)
            words.append(f"remove shot {seg['clip']}")
        elif kind == "move":
            seg = _shot(by_id, op["shot"], "move")
            segs.pop(index_of(str(seg["id"])))
            by_id.pop(str(seg["id"]), None)
            place(seg, op, "move")
            changed.append(str(seg["id"]))
            words.append(f"move shot {seg['clip']}")
    if not segs:
        raise ValueError("the edits would leave an empty cut")
    return {"segments": segs, "id_map": id_map, "generated": generated,
            "changed": changed, "words": words}
