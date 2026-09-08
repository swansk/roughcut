"""The bin: the editor's selects, and the floor's verdicts, in the EDL.

Selection is a first-class artifact here rather than a note the model reconstructs. The
EDL gains two keys beside `segments`, both ignored by `assemble.py`:

    "selects": [ {id, clip, start, end, why, note, hero, witnesses, tags,
                  source, created, used_in: [segment ids],
                  clip_duration?, missing?} ]
    "floor":   { "verdicts": [ {clip, start, end, verdict: reject|later, note, at} ],
                 "position": {round, index, order},
                 "order": "rank" }

Every entry is a **range on clip time**. Picks come and go as the index gets finer;
these survive, and picks.attach_verdicts re-attaches them by overlap.

`clip_duration` is the clip's length when it was known at creation — the fingerprint
`relink` uses to find a select's footage again after a rename (decision 4: clips may be
added, and moved, at any time). `missing: True` marks a select whose clip is not in the
folder right now; it is never deleted, only flagged.

Pure functions over the EDL dict. The server reads, applies, writes atomically.
"""

from __future__ import annotations

import hashlib
import time

# Two keeps in one clip that share this much of the shorter one are the same moment and
# merge; a string-out must never play footage twice.
MERGE_MIN_OVERLAP = 0.5
VERDICTS = ("pick", "reject", "later", "clear")
# Two clips whose lengths differ by less than this are the same footage for relinking:
# GoPro files are cut on GOP boundaries, so a copy or a rename keeps the length to the
# frame, and two genuinely different clips of the same length to 50 ms are rare enough
# that "exactly one match" is the rule and anything else stays missing.
RELINK_TOLERANCE_S = 0.05


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def _ratio(a: tuple[float, float], b: tuple[float, float]) -> float:
    shorter = max(1e-6, min(a[1] - a[0], b[1] - b[0]))
    return _overlap(a, b) / shorter


def _touching(s: dict, clip: str, start: float, end: float,
              minimum: float = MERGE_MIN_OVERLAP) -> bool:
    return (s.get("clip") == clip
            and _ratio((float(s["start"]), float(s["end"])), (start, end)) >= minimum)


def select_id(clip: str, start: float, end: float, created: float) -> str:
    h = hashlib.sha1(f"{clip}|{start:.2f}|{end:.2f}|{created:.3f}".encode()).hexdigest()
    return f"k_{h[:8]}"


def ensure(edl: dict) -> dict:
    """The keys, present and well-formed, on the dict passed in."""
    edl.setdefault("selects", [])
    floor = edl.setdefault("floor", {})
    floor.setdefault("verdicts", [])
    floor.setdefault("position", {"round": 1, "index": 0, "order": "rank"})
    return edl


def new_select(clip: str, start: float, end: float, *, why: str = "", note: str = "",
               hero: bool = False, witnesses: list | None = None,
               tags: list | None = None, source: str = "floor",
               created: float | None = None,
               clip_duration: float | None = None) -> dict:
    """One select. `clip_duration` is recorded when the caller knows it (the server
    does; a pure caller may not) — it is what `relink` matches on later."""
    if not (0.0 <= float(start) < float(end)):
        raise ValueError(f"select range {start}-{end} is not a range")
    created = time.time() if created is None else float(created)
    item = {"id": select_id(clip, float(start), float(end), created), "clip": clip,
            "start": round(float(start), 2), "end": round(float(end), 2),
            "why": str(why)[:300], "note": str(note)[:600], "hero": bool(hero),
            "witnesses": list(witnesses or [])[:12], "tags": list(tags or [])[:8],
            "source": source, "created": round(created, 3), "used_in": []}
    if clip_duration is not None and float(clip_duration) > 0:
        item["clip_duration"] = round(float(clip_duration), 3)
    return item


def apply_verdict(edl: dict, clip: str, start: float, end: float, verdict: str, *,
                  why: str = "", note: str = "", hero: bool = False,
                  witnesses: list | None = None, tags: list | None = None,
                  source: str = "floor", clip_duration: float | None = None) -> dict:
    """One verdict on one range. Mutates and returns `edl`.

    `pick` adds a select, merging any keep in the same clip that overlaps it (union of
    the ranges, notes joined, hero if either was) and clearing any reject/later on those
    seconds. `reject` / `later` record the verdict and remove an overlapping select — a
    later reject of kept seconds is the human changing their mind. `clear` removes both.
    `clip_duration`, when the caller knows it, is recorded on the keep for `relink`; a
    merge keeps whichever side knew it.
    """
    if verdict not in VERDICTS:
        raise ValueError(f"unknown verdict {verdict!r}")
    start, end = float(start), float(end)
    if not (0.0 <= start < end):
        raise ValueError(f"range {start}-{end} is not a range")
    ensure(edl)
    floor = edl["floor"]
    # Anything already said about these seconds gives way to this verdict.
    floor["verdicts"] = [v for v in floor["verdicts"]
                         if not _touching(v, clip, start, end, minimum=0.01)]
    overlapping = [s for s in edl["selects"] if _touching(s, clip, start, end)]
    edl["selects"] = [s for s in edl["selects"] if s not in overlapping]

    if verdict == "pick":
        merged = new_select(clip, start, end, why=why, note=note, hero=hero,
                            witnesses=witnesses, tags=tags, source=source,
                            clip_duration=clip_duration)
        for s in overlapping:
            merged["start"] = min(merged["start"], float(s["start"]))
            merged["end"] = max(merged["end"], float(s["end"]))
            merged["hero"] = merged["hero"] or bool(s.get("hero"))
            if "clip_duration" not in merged and s.get("clip_duration"):
                merged["clip_duration"] = s["clip_duration"]
            if s.get("note") and s["note"] not in merged["note"]:
                merged["note"] = " / ".join(x for x in (merged["note"], s["note"]) if x)
            if not merged["why"] and s.get("why"):
                merged["why"] = s["why"]
            merged["used_in"] = sorted(set(merged["used_in"]) | set(s.get("used_in", [])))
            merged["created"] = min(merged["created"], float(s.get("created", merged["created"])))
        merged["id"] = select_id(clip, merged["start"], merged["end"], merged["created"])
        edl["selects"].append(merged)
        edl["selects"].sort(key=lambda s: (s["clip"], s["start"]))
    elif verdict in ("reject", "later"):
        # `why` is kept on a reject too: the Survey's batch reject says "other take of
        # <cluster>", and a floor that cannot say why it rejected something is a floor
        # that cannot be revisited.
        floor["verdicts"].append({"clip": clip, "start": round(start, 2),
                                  "end": round(end, 2), "verdict": verdict,
                                  "why": str(why)[:300],
                                  "note": str(note)[:600], "at": round(time.time(), 3)})
    return edl


def note(edl: dict, clip: str, start: float, end: float, text: str) -> dict:
    """Attach a note to whatever verdict covers these seconds; a note on undecided
    seconds becomes a `later` so it is never lost."""
    ensure(edl)
    start, end = float(start), float(end)
    text = str(text).strip()[:600]
    for s in edl["selects"]:
        if _touching(s, clip, start, end):
            s["note"] = text
            return edl
    for v in edl["floor"]["verdicts"]:
        if _touching(v, clip, start, end):
            v["note"] = text
            return edl
    return apply_verdict(edl, clip, start, end, "later", note=text)


def used_in(edl: dict) -> dict:
    """Which segments each select became. Recomputed from the timeline, never trusted
    from before: a shot deleted by hand must stop counting as a use."""
    ensure(edl)
    segments = edl.get("segments") or []
    for s in edl["selects"]:
        span = (float(s["start"]), float(s["end"]))
        s["used_in"] = [seg.get("id") or f"s{i}" for i, seg in enumerate(segments)
                        if seg.get("clip") == s["clip"]
                        and _ratio(span, (float(seg["in"]), float(seg["out"]))) >= 0.5]
    return edl


def sync_timeline(edl: dict) -> dict:
    """Everything the bin must learn from a saved timeline. Called on every save.

    Two things. `used_in` is recomputed. Then any shot on the timeline that no keep
    covers becomes a keep with `source: "hand"` — the shot's own range and `why`, no
    note — so a shot placed by hand in the cutting room is in the bin like any other,
    and the next ask from the bin knows about it (docs/INTAKE.md I1.3; design §5).

    "Covers" is `used_in`'s test — the shot and the keep share half of the shorter one
    — so a shot that already counts as a keep's use is never adopted twice, and a shot
    that trimmed inside a keep stays that keep's use rather than becoming a second one.
    Adoption goes through `apply_verdict("pick")`: placing seconds in the film outranks
    a reject or a later on them. Idempotent — the adopted keep covers its shot exactly.
    """
    used_in(edl)
    for seg in list(edl.get("segments") or []):
        try:
            clip = seg["clip"]
            start, end = float(seg["in"]), float(seg["out"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0.0 <= start < end):
            continue
        if any(_touching(s, clip, start, end) for s in edl["selects"]):
            continue
        apply_verdict(edl, clip, start, end, "pick", why=str(seg.get("why") or ""),
                      source="hand")
    return used_in(edl)


def relink(edl: dict, clips: dict[str, dict]) -> dict:
    """Find each select's footage in the folder as it is now. Mutates and returns `edl`.

    `clips` is `{clip: {"duration": seconds}}` for what is on disk. A select whose clip is
    there is left alone (a `missing` flag from an earlier pass is cleared, and a select
    that never learned its clip's length learns it now, so it can be relinked later). A
    select whose clip is not there is flagged `missing: True` — never dropped: a keep is
    the editor's work, the file is the thing that wandered. If exactly one clip on disk
    is within RELINK_TOLERANCE_S of the select's recorded `clip_duration`, the select is
    re-pointed at that clip and `missing` cleared; no match or several leaves it missing,
    because guessing between two same-length clips would silently put the wrong footage
    in the film. `used_in` and the id are kept: it is the same select, found again.
    """
    ensure(edl)
    durations = {}
    for clip, info in (clips or {}).items():
        try:
            d = float((info or {}).get("duration") or 0.0)
        except (TypeError, ValueError):
            d = 0.0
        durations[clip] = d
    for s in edl["selects"]:
        if s.get("clip") in durations:
            s.pop("missing", None)
            if "clip_duration" not in s and durations[s["clip"]] > 0:
                s["clip_duration"] = round(durations[s["clip"]], 3)
            continue
        want = s.get("clip_duration")
        matches = []
        if want:
            matches = [c for c, d in durations.items()
                       if d > 0 and abs(d - float(want)) <= RELINK_TOLERANCE_S]
        if len(matches) == 1:
            s["clip"] = matches[0]
            s.pop("missing", None)
        else:
            s["missing"] = True
    edl["selects"].sort(key=lambda s: (s["clip"], s["start"]))
    return edl


def strung_out_s(edl: dict) -> float:
    return round(sum(float(s["end"]) - float(s["start"])
                     for s in (edl.get("selects") or [])), 2)


def summary(edl: dict) -> dict:
    """What the HUD and the closing card show: counts, never a verdict on sufficiency."""
    ensure(edl)
    selects = edl["selects"]
    verdicts = edl["floor"]["verdicts"]
    return {
        "moments": len(selects),
        "heroes": sum(1 for s in selects if s.get("hero")),
        "notes": sum(1 for s in selects if s.get("note"))
                 + sum(1 for v in verdicts if v.get("note")),
        "later": sum(1 for v in verdicts if v.get("verdict") == "later"),
        "rejected": sum(1 for v in verdicts if v.get("verdict") == "reject"),
        "strung_out_s": strung_out_s(edl),
        "used": sum(1 for s in selects if s.get("used_in")),
        "position": edl["floor"].get("position"),
    }


def validate_selects(payload, clips: dict[str, dict]) -> list[dict]:
    """A whole replacement list, checked the way a plan is: known clips, real ranges."""
    if not isinstance(payload, list):
        raise ValueError("selects must be a list")
    clean = []
    for i, s in enumerate(payload):
        if not isinstance(s, dict):
            raise ValueError(f"select {i} is not an object")
        clip = s.get("clip")
        try:
            start, end = float(s["start"]), float(s["end"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"select {i} has non-numeric start/end") from None
        if clip not in clips:
            # A select whose footage is currently missing round-trips untouched: the
            # bin editor must be able to save without losing the keep whose file
            # wandered. Anything else naming an unknown clip is a mistake.
            if s.get("missing") is True and 0.0 <= start < end:
                kept = dict(s)
                kept["start"], kept["end"] = round(start, 2), round(end, 2)
                clean.append(kept)
                continue
            raise ValueError(f"select {i} names unknown clip {clip!r}")
        duration = float(clips[clip].get("duration") or 0.0)
        if not (0.0 <= start < end <= (duration + 0.05 if duration else end)):
            raise ValueError(f"select {i} range {start}-{end} outside {clip}")
        item = new_select(clip, start, min(end, duration) if duration else end,
                          why=s.get("why", ""), note=s.get("note", ""),
                          hero=bool(s.get("hero")), witnesses=s.get("witnesses"),
                          tags=s.get("tags"), source=str(s.get("source", "floor")),
                          created=s.get("created"), clip_duration=duration or None)
        if s.get("id"):
            item["id"] = str(s["id"])[:40]
        item["used_in"] = list(s.get("used_in") or [])
        clean.append(item)
    return clean
