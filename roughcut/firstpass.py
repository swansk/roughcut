"""The first pass, as one re-runnable step (Karl's K1, REDESIGN-GOAL dimension 2).

Karl, 2026-10-14: *"it is not clear how to re run the first pass."* The pass was three
pieces on three screens — the index on /open, the culling on /floor, "Make a first cut"
on the board (gone the moment a clip was on the timeline) — and after a manual edit the
only way back was a typed Ask that never said what it would keep. Here it is one thing:
index → picks → a proposed rough cut, run as one job, re-runnable at every state, and a
re-run keeps the editor's own work exactly.

This module is the pure half: provenance (which shots of the cut are the editor's own —
trimmed, razored, moved or added by hand), lock enforcement on a proposal, the per-shot
diff a re-run reports, and the cost wording. The server (`/api/firstpass`) runs the job
and reads the files.

**Provenance is a diff, not a log.** Every accepted proposal records a *base* in the
EDL (`provenance.bases`): the shots it put on the timeline, by id and range, and their
order. A shot is the editor's own when it is not in the base (added — or a razor piece
of a base shot), when its range or speed differs from the base (trimmed), or when it
left the base's order (moved). An undo that puts a shot back exactly is the machine's
again, with no event tracking to get wrong; and the board saves the whole cut on every
edit, so a hook on each gesture would see nothing a diff does not.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import config

PROV_KEY = "provenance"
BASES_KEPT = 10
EPS = 0.005

MODES = ("run", "rerun")

# How a shot became the editor's own, in the order the panel lists them.
HOW = ("add", "razor", "trim", "speed", "move", "grade")
HOW_WORDS = {"add": "added by hand", "razor": "razored", "trim": "trimmed",
             "speed": "speed changed", "move": "moved", "grade": "graded"}

# A shot's grade, for provenance (the journey, wave 3: a shot only colour-graded by hand
# was not "the editor's own", so a re-run could replace it and take the grade with it):
# its override in the EDL's colour block (balance, look, strength, auto, match) and the
# Lumetri values Effect Controls keeps on the segment.
GRADE_KEYS = ("auto", "balance", "look", "strength", "match")
GRADE_CONTROLS = ("contrast", "saturation")


def grade_of(colour: dict | None, seg: dict) -> str | None:
    """The shot's grade as a comparable string, None when it has none of its own."""
    shots = (colour or {}).get("shots") if isinstance(colour, dict) else None
    own = (shots or {}).get(seg.get("id")) if isinstance(shots, dict) else None
    g = {k: own[k] for k in GRADE_KEYS if isinstance(own, dict) and own.get(k) is not None}
    ctl = seg.get("controls") if isinstance(seg.get("controls"), dict) else {}
    lum = {k: ctl[k] for k in GRADE_CONTROLS if ctl.get(k) is not None}
    if lum:
        g["lumetri"] = lum
    return json.dumps(g, sort_keys=True) if g else None


def _rng(s: dict) -> tuple[str, float, float, float]:
    return (str(s.get("clip")), round(float(s["in"]), 2), round(float(s["out"]), 2),
            float(s.get("speed") or 1.0))


def _same(a: dict, b: dict, *, speed: bool = True) -> bool:
    return (a.get("clip") == b.get("clip")
            and abs(float(a["in"]) - float(b["in"])) < EPS
            and abs(float(a["out"]) - float(b["out"])) < EPS
            and (not speed or abs(float(a.get("speed") or 1.0)
                                  - float(b.get("speed") or 1.0)) < 1e-6))


def _segments(edl: dict) -> list[dict]:
    return [s for s in edl.get("segments") or []
            if isinstance(s, dict) and "in" in s and "out" in s]


# ------------------------------------------------------------------ provenance


def bases(edl: dict) -> list[dict]:
    prov = edl.get(PROV_KEY)
    return list((prov or {}).get("bases") or []) if isinstance(prov, dict) else []


def record_base(edl: dict, segments: list[dict], *, job: str | None,
                hand: dict[str, list[str]] | None = None, at: float | None = None,
                colour: dict | None = None) -> dict:
    """An accepted proposal becomes the newest base: its shots are the machine's, except
    the ids in `hand` (shots a re-run kept because the editor made them — they stay the
    editor's, with how). Each shot's grade is recorded too, from `colour` (the colour
    block the accepting save writes; default the EDL's), so a grade changed by hand
    later is a hand edit. Mutates and returns `edl`."""
    colour = edl.get("colour") if colour is None else colour
    grades = {}
    hand = {str(k): list(v) for k, v in (hand or {}).items()}
    shots = {}
    order = []
    for s in segments:
        sid = s.get("id")
        if not sid:
            continue
        order.append(sid)
        # the cut's own values (4 places, the frame grid's), never rounded to hundredths
        shots[sid] = {"clip": s.get("clip"), "in": round(float(s["in"]), 4),
                      "out": round(float(s["out"]), 4),
                      "speed": float(s.get("speed") or 1.0)}
        grades[sid] = grade_of(colour, s)
    base = {"job": job, "at": round(at if at is not None else time.time(), 3),
            "shots": shots, "order": order, "hand": hand, "grades": grades}
    prov = edl.get(PROV_KEY) if isinstance(edl.get(PROV_KEY), dict) else {}
    prov["bases"] = ([base] + bases(edl))[:BASES_KEPT]
    # a range rejected once and now accepted exactly: the editor changed their mind
    if prov.get("rejected"):
        prov["rejected"] = [r for r in prov["rejected"]
                            if not any(_same(r, s, speed=False) for s in segments
                                       if isinstance(s, dict) and "in" in s)]
    edl[PROV_KEY] = prov
    return edl


def is_accept(saved: list[dict], plan_segments: list[dict]) -> bool:
    """Whether a save is the Accept of this plan: the same shots, in the same order,
    with the same ranges (the board's Accept saves `pendingPlan.segments` verbatim)."""
    plan = [s for s in plan_segments or [] if isinstance(s, dict) and "in" in s]
    return bool(plan) and len(plan) == len(saved) and all(
        _same(a, b, speed=False) for a, b in zip(saved, plan))


def plan_changes(plan: dict) -> list[dict]:
    """The shots a proposal changes or adds (not the ones it keeps as they are), by
    the diff the server stored on it: the first pass's own, else the notes check's."""
    segs = [x for x in plan.get("segments") or [] if isinstance(x, dict) and "in" in x]
    diff = ((plan.get("firstpass") or {}).get("diff") or plan.get("diff") or {})
    rows = diff.get("shots") or []
    if len(rows) != len(segs):                  # no usable diff: every shot is the plan's
        return segs
    return [x for x, r in zip(segs, rows) if r.get("status") not in ("kept",)]


def partial_hand(saved: list[dict], plan: dict, prior: dict[str, list[str]]) -> dict:
    """A partial Accept (the editor took some of a proposal's shots — verdict 3 punch 7):
    which shots of the cut it saved stay the editor's own. A shot the proposal changed
    or added, and that the editor accepted, is the machine's; every other shot — one
    the proposal kept as it was, a change the editor rejected (the shot stays as it
    was), a removal the editor refused — keeps the provenance it had (`prior`, by id:
    the hand edits as they stood)."""
    changes = plan_changes(plan)
    hand = {}
    for s in saved:
        sid = s.get("id")
        if not sid or sid not in prior:
            continue
        if any(_same(s, c, speed=False) for c in changes):
            continue                            # accepted from the proposal
        hand[sid] = list(prior[sid])
    return hand


def _lcs(a: list[str], b: list[str]) -> set[str]:
    """The ids of a longest common subsequence of two id orders."""
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = dp[i + 1][j + 1] + 1 if a[i] == b[j] else max(dp[i + 1][j],
                                                                       dp[i][j + 1])
    out, i, j = set(), 0, 0
    while i < n and j < m:
        if a[i] == b[j]:
            out.add(a[i])
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return out


def manual_edits(edl: dict, plans: list[list[dict]] | None = None) -> list[dict]:
    """The shots of the cut that are the editor's own, in cut order:
    `{id, pos, clip, in, out, how: [...], words}` — `how` from HOW.

    With no base (a cut made before provenance existed, or by hand), a shot counts as
    the machine's when some stored proposal (`plans`, the asks on disk) holds exactly
    its range, and as the editor's otherwise: nothing claims it, so nothing may
    replace it silently."""
    segs = _segments(edl)
    known = bases(edl)
    base = known[0] if known else None
    out = []
    if base is None:
        machine = [s for plan in (plans or []) for s in plan
                   if isinstance(s, dict) and "in" in s and "out" in s]
        for i, s in enumerate(segs):
            if not any(_same(s, p, speed=False) for p in machine):
                out.append(_row(s, i, ["add"]))
        return out
    shots: dict = base.get("shots") or {}
    hand: dict = base.get("hand") or {}
    # a base recorded before grades were (no "grades"): a grade cannot be told apart
    grades: dict | None = base.get("grades") if isinstance(base.get("grades"), dict) else None
    colour = edl.get("colour")
    alias = _realias(segs, shots)
    cur_ids = [alias.get(s.get("id"), s.get("id")) for s in segs]
    common = [x for x in base.get("order") or [] if x in cur_ids]
    in_order = _lcs(common, [x for x in cur_ids if x in common])
    for i, s in enumerate(segs):
        sid = alias.get(s.get("id"), s.get("id"))
        how: list[str] = []
        b = shots.get(sid) if sid else None
        if sid in hand:
            how += [h for h in hand[sid] if h in HOW]
            if b is not None and not _same(s, b, speed=False):
                how.append("trim")
        elif b is None:
            # a razor piece: inside the base range of a base shot of the same clip
            # whose own id is still in the cut (cut-from, not replaced)
            parent = next((sh for pid, sh in shots.items()
                           if pid in cur_ids and pid != sid and sh["clip"] == s.get("clip")
                           and float(s["in"]) >= sh["in"] - EPS
                           and float(s["out"]) <= sh["out"] + EPS), None)
            if parent is None and any(_same(s, sh, speed=False) for sh in shots.values()):
                how = []                          # a base range under a fresh id (copy)
            else:
                how.append("razor" if parent is not None else "add")
        else:
            if not _same(s, b, speed=False):
                pieces = [t for t in segs if t is not s
                          and not shots.get(alias.get(t.get("id"), t.get("id")))
                          and t.get("clip") == s.get("clip")
                          and float(t["in"]) >= b["in"] - EPS
                          and float(t["out"]) <= b["out"] + EPS]
                how.append("razor" if pieces else "trim")
            if abs(float(s.get("speed") or 1.0) - float(b.get("speed") or 1.0)) > 1e-6:
                how.append("speed")
        if sid and sid in common and sid not in in_order:
            how.append("move")
        if (b is not None and grades is not None
                and grade_of(colour, s) != grades.get(sid)):
            how.append("grade")
        if how:
            out.append(_row(s, i, sorted(set(how), key=HOW.index)))
    return out


def _realias(segs: list[dict], shots: dict) -> dict[str, str]:
    """The cut's shots that are base shots under a fresh id, `{id now: base id}`.

    Verdict 4: after a partial Accept the board saved the cut again before it had re-keyed
    its temporary ids, so the server minted a second set: every shot's id left the base,
    and the one shot the editor then trimmed read "added by hand". A base shot whose id
    is gone from the cut is found again by its footage — first a shot holding its range
    exactly, then a shot of the same clip covering most of it (that shot, trimmed). A
    shot whose id is in the base, and a razor piece of a base shot still in the cut, are
    never re-aliased."""
    ids = {s.get("id") for s in segs}
    lost = {bid: sh for bid, sh in shots.items() if bid not in ids}
    fresh = [s for s in segs if s.get("id") and s.get("id") not in shots]
    if not lost or not fresh:
        return {}
    present = [sh for bid, sh in shots.items() if bid in ids]
    alias: dict[str, str] = {}
    for s in fresh:                                   # the same range, any id
        hit = next((bid for bid, sh in lost.items() if bid not in alias.values()
                    and _same(s, sh, speed=False)), None)
        if hit:
            alias[s["id"]] = hit
    for s in fresh:                                   # most of its footage: trimmed
        if s["id"] in alias or any(
                sh["clip"] == s.get("clip") and float(s["in"]) >= sh["in"] - EPS
                and float(s["out"]) <= sh["out"] + EPS for sh in present):
            continue
        best, best_r = None, 0.5
        for bid, sh in lost.items():
            if bid in alias.values() or sh.get("clip") != s.get("clip"):
                continue
            r = _overlap_ratio(s, sh)
            if r >= best_r:
                best, best_r = bid, r
        if best:
            alias[s["id"]] = best
    return alias


# What a generated clip is called where the editor reads a clip's name (verdict 9, punch
# 3: the re-run panel listed "gen_black_78a418d8 · added by hand"). Premiere's own names
# for the same things: Black Video and Color Matte; a still by the clip it was taken
# from. The file keeps its `gen_<kind>_<key>` name; only the words change.
GENERATED_NAMES = {"black": "Black Video", "colour": "Color Matte", "color": "Color Matte",
                   "still": "Still"}


def clip_name(clip, spec: dict | None = None) -> str:
    """A clip as the editor reads it: a footage clip by its name without the extension,
    a generated one by what it is (`GENERATED_NAMES`; a still with the name of the clip
    it was taken from, when `spec` — the generated file's record — says which)."""
    c = str(clip or "")
    if not c.startswith("gen_"):
        return _stem(c)
    kind = (spec or {}).get("kind") or (c.split("_")[1] if c.count("_") >= 2 else "")
    name = GENERATED_NAMES.get(kind, "Generated clip")
    src = (spec or {}).get("from_clip")
    if kind == "still" and src:
        return f"{_stem(src)} {name}"
    return name


def _row(s: dict, i: int, how: list[str]) -> dict:
    """A shot as the panel lists it. `in` / `out` are the cut's own values, exactly
    (verdict 4: rounding them to hundredths moved kept shots off the frame grid
    wherever a row was read back as a range); the board rounds for display. `name` is
    the clip as the editor reads it (`clip_name`)."""
    return {"id": s.get("id"), "pos": i + 1, "clip": s.get("clip"),
            "name": clip_name(s.get("clip")),
            "in": float(s["in"]), "out": float(s["out"]),
            "how": how, "words": ", ".join(HOW_WORDS[h] for h in how)}


# ------------------------------------------------------------------ rejections
#
# Verdict 4: the editor ✗-rejected CLIP_01 120.9-128.6 in a proposal, accepted the rest,
# and the re-run a minute later added it back as "new" — its notes even presented it as
# a choice. A rejection is part of the cut's history, like a base: the ranges the editor
# turned down are kept on the EDL (`provenance.rejected`), the re-run's brief names them
# as "do not propose", and its proposal is held to that after the model answers.

REJECTED_KEPT = 50
REJECT_MATCH = 0.5          # overlap / the longer of the two: mostly the same moment


def rejections(edl: dict) -> list[dict]:
    """The ranges the editor rejected in a review, newest first: `{clip, in, out, at,
    job}`."""
    prov = edl.get(PROV_KEY)
    rows = (prov or {}).get("rejected") if isinstance(prov, dict) else None
    return [r for r in rows or [] if isinstance(r, dict) and r.get("clip")
            and isinstance(r.get("in"), (int, float)) and isinstance(r.get("out"), (int, float))]


def rejected_ranges(plan: dict, partial: dict | None) -> list[dict]:
    """The proposed ranges a partial Accept left out, from the answer (`partial`:
    `rejected` — the proposal's shot numbers, 0-based — and the board's own `ranges`,
    each held to a shot the proposal really made). A rejected shot the proposal kept
    as it is (or only moved) is not a range turned down: it is still in the cut."""
    if not isinstance(partial, dict):
        return []
    segs = [s for s in plan.get("segments") or [] if isinstance(s, dict) and "in" in s]
    diff = ((plan.get("firstpass") or {}).get("diff") or plan.get("diff") or {})
    rows = diff.get("shots") or []
    rows = rows if len(rows) == len(segs) else [{}] * len(segs)

    def made(k: int) -> bool:               # a range this proposal made, not the cut's
        return rows[k].get("status") not in ("kept",)

    picked: list[dict] = []
    for k in partial.get("rejected") or []:
        if isinstance(k, int) and 0 <= k < len(segs) and made(k):
            picked.append(segs[k])
    for r in partial.get("ranges") or []:
        if not isinstance(r, dict):
            continue
        try:
            want = {"clip": r.get("clip"), "in": float(r["in"]), "out": float(r["out"])}
        except (KeyError, TypeError, ValueError):
            continue
        k = next((k for k, s in enumerate(segs) if _same(want, s, speed=False)), None)
        if k is not None and made(k):
            picked.append(segs[k])
    out: list[dict] = []
    for s in picked:
        row = {"clip": s.get("clip"), "in": round(float(s["in"]), 4),
               "out": round(float(s["out"]), 4)}
        if not any(_same(row, o, speed=False) for o in out):
            out.append(row)
    return out


def record_rejections(edl: dict, ranges: list[dict], *, job: str | None,
                      at: float | None = None) -> bool:
    """Add `ranges` to the cut's rejections (newest first, each range once). Mutates
    `edl`; True when something was added."""
    if not ranges:
        return False
    now = round(at if at is not None else time.time(), 3)
    old = rejections(edl)
    new = [{**r, "at": now, "job": job} for r in ranges
           if not any(_same(r, o, speed=False) for o in old)]
    if not new:
        return False
    prov = edl.get(PROV_KEY) if isinstance(edl.get(PROV_KEY), dict) else {}
    prov["rejected"] = (new + old)[:REJECTED_KEPT]
    edl[PROV_KEY] = prov
    return True


def _clock(t: float) -> str:
    m, s = divmod(max(0.0, float(t)), 60)
    return f"{int(m)}:{s:04.1f}"


def _stem(clip) -> str:
    return Path(str(clip)).stem


def rejected_brief(rejected: list[dict]) -> str:
    """The re-run brief's paragraph: the ranges not to propose again."""
    if not rejected:
        return ""
    rows = "\n".join(f"  - {r['clip']} {float(r['in']):.2f}-{float(r['out']):.2f}"
                     for r in rejected)
    return ("The editor REJECTED these ranges when reviewing an earlier proposal. Do not "
            "propose them again, nor a shot that is mostly the same moment (unless it is "
            "already in the current edit):\n" + rows)


def _echoes(s: dict, c: dict) -> bool:
    """`s` is the cut's shot `c` as the model typed it back (hundredths)."""
    return (s.get("clip") == c.get("clip")
            and abs(float(s["in"]) - float(c["in"])) <= ECHO_EPS
            and abs(float(s["out"]) - float(c["out"])) <= ECHO_EPS)


ECHO_EPS = 0.006            # under half a frame at 60 fps; hundredths round by ≤ 0.005


def _covers(s: dict, r: dict) -> bool:
    if s.get("clip") != r.get("clip"):
        return False
    a0, a1, b0, b1 = float(s["in"]), float(s["out"]), float(r["in"]), float(r["out"])
    longer = max(1e-6, max(a1 - a0, b1 - b0))
    return max(0.0, min(a1, b1) - max(a0, b0)) / longer >= REJECT_MATCH


def drop_rejected(proposed: list[dict], current: list[dict],
                  rejected: list[dict]) -> tuple[list[dict], list[dict]]:
    """The proposal without the ranges the editor rejected: a proposed shot that is
    mostly a rejected range is dropped — or, when it continues a shot of the cut (same
    id), that shot stays as it is now (the editor turned the change down, not the
    shot). A shot that is in the cut now is never touched: the cut is the editor's
    latest word. Returns `(segments, left_out)`, `left_out` = `{clip, in, out, at,
    action: "dropped" | "kept as it is", id?}`."""
    if not rejected:
        return [dict(s) for s in proposed], []
    cur = [c for c in current if isinstance(c, dict) and "in" in c and "out" in c]
    by_id = {c.get("id"): c for c in cur if c.get("id")}
    out: list[dict] = []
    left: list[dict] = []
    for s in proposed:
        if not (isinstance(s, dict) and "in" in s and "out" in s) or any(
                _echoes(s, c) for c in cur):
            out.append(dict(s))
            continue
        hit = next((r for r in rejected if _covers(s, r)), None)
        if hit is None:
            out.append(dict(s))
            continue
        row = {"clip": s.get("clip"), "in": float(s["in"]), "out": float(s["out"]),
               "rejected": {"in": hit["in"], "out": hit["out"], "at": hit.get("at")}}
        was = by_id.get(s.get("id"))
        if was is not None and not any(o.get("id") == was.get("id") for o in out):
            out.append(dict(was))
            row.update(action="kept as it is", id=was.get("id"))
        else:
            row["action"] = "dropped"
        left.append(row)
    if not any(isinstance(o, dict) and "in" in o for o in out):
        # nothing else proposed: an empty cut is no proposal — keep it, and say so
        return [dict(s) for s in proposed], [{**r, "action": "flagged"} for r in left]
    return out, left


def rejected_lines(left: list[dict]) -> list[str]:
    """One line of the notes per rejected range the proposal brought back."""
    lines = []
    for r in left:
        what = (f"{clip_name(r['clip'])} {_clock(r['in'])}–{_clock(r['out'])}")
        if r.get("action") == "flagged":
            lines.append(f"Check: {what} — you rejected this shot before.")
        elif r.get("action") == "kept as it is":
            lines.append(f"Left out: {what} — you rejected this change before, so the "
                         "shot stays as it is.")
        else:
            lines.append(f"Left out: {what} — you rejected this shot before, so it stays "
                         "out.")
    return lines


def resolve_keep(keep, segments: list[dict], manual: list[dict], mode: str) -> list[str]:
    """`keep` as the request gave it — a list of shot ids, "manual", "none" or absent —
    as the ids to lock. Absent means "manual" on a re-run and nothing on a run.
    Raises ValueError naming an id the cut does not have."""
    ids = [s.get("id") for s in segments if s.get("id")]
    if keep is None:
        keep = "manual" if mode == "rerun" else "none"
    if keep == "manual":
        return [r["id"] for r in manual if r.get("id")]
    if keep in ("none", "", []):
        return []
    if not isinstance(keep, list) or not all(isinstance(k, str) for k in keep):
        raise ValueError('keep must be a list of shot ids, "manual" or "none"')
    unknown = [k for k in keep if k not in ids]
    if unknown:
        raise ValueError(f"no shot {unknown[0]!r} in the cut")
    return [i for i in ids if i in set(keep)]


# ------------------------------------------------------------------ the proposal


def _overlap_ratio(a: dict, b: dict) -> float:
    a0, a1, b0, b1 = float(a["in"]), float(a["out"]), float(b["in"]), float(b["out"])
    shorter = max(1e-6, min(a1 - a0, b1 - b0))
    return max(0.0, min(a1, b1) - max(a0, b0)) / shorter


def enforce_locks(proposed: list[dict], current: list[dict], locked: list[str]) -> list[dict]:
    """The plan with every locked shot exactly as it is now — same id, clip, in, out,
    speed and reason — whatever the model did with it: a locked shot it changed is put
    back, one it dropped is re-inserted after the nearest shot that preceded it in the
    cut, a proposed shot that re-uses most of a locked shot's footage is dropped (it
    would play the moment twice), and the locked shots keep their order relative to
    each other."""
    lock = [c for c in current if c.get("id") in set(locked)]
    lock_ids = [c["id"] for c in lock]
    by_id = {c["id"]: c for c in lock}
    out: list[dict] = []
    for s in proposed:
        sid = s.get("id")
        if sid in by_id:
            if not any(o.get("id") == sid for o in out):
                out.append(dict(by_id[sid]))
            continue
        if any(s.get("clip") == c.get("clip") and _overlap_ratio(s, c) >= 0.5 for c in lock):
            continue
        out.append(dict(s))
    present = {o.get("id") for o in out}
    cur_ids = [c.get("id") for c in current]
    for c in lock:
        if c["id"] in present:
            continue
        at = 0
        for prev in reversed(cur_ids[:cur_ids.index(c["id"])]):
            k = next((n for n, o in enumerate(out) if o.get("id") == prev), None)
            if k is not None:
                at = k + 1
                break
        out.insert(at, dict(c))
        present.add(c["id"])
    # locked shots in their own relative order, in the slots they occupy
    slots = [n for n, o in enumerate(out) if o.get("id") in by_id]
    for n, sid in zip(slots, [i for i in lock_ids if i in present]):
        out[n] = dict(by_id[sid])
    return out


def rerun_diff(current: list[dict], proposed: list[dict], locked: list[str],
               manual: list[dict] | None = None) -> dict:
    """Per proposed shot `kept` / `replaced` / `new`, and the current shots it drops.
    `kept` = in the cut now with the same range (`locked` when the editor's lock held
    it); `replaced` = continues a shot of the cut (same id) with a different range
    (`was` is the old range); `new` = nothing in the cut now. A dropped shot is in
    `removed` with status `replaced`. `sentence` says it in one line."""
    from .revise import shot_diff        # one diff, the notes check's own

    d = shot_diff(current, proposed)
    how = {r["id"]: r for r in manual or [] if r.get("id")}
    lock = set(locked)
    shots = []
    for r in d["shots"]:
        row = dict(r)
        row["status"] = {"kept": "kept", "changed": "replaced", "new": "new"}[r["status"]]
        row["locked"] = r.get("id") in lock and r["status"] == "kept"
        if r.get("id") in how:
            row["hand"] = how[r["id"]]["how"]
        shots.append(row)
    removed = [{**r, "status": "replaced", "hand": how.get(r.get("id"), {}).get("how")}
               for r in d["removed"]]
    counts = {"kept": sum(1 for r in shots if r["status"] == "kept"),
              "locked": sum(1 for r in shots if r["locked"]),
              "replaced": sum(1 for r in shots if r["status"] == "replaced") + len(removed),
              "new": sum(1 for r in shots if r["status"] == "new"),
              "removed": len(removed)}
    return {"shots": shots, "removed": removed, "counts": counts,
            "sentence": diff_sentence(counts)}


def _n(k: int, word: str) -> str:
    return f"{k} {word}{'' if k == 1 else 's'}"


def diff_sentence(c: dict) -> str:
    bits = []
    if c.get("locked"):
        bits.append(f"keeps your {_n(c['locked'], 'hand-edited shot')} exactly")
    other = int(c.get("kept", 0)) - int(c.get("locked", 0))
    if other:
        bits.append(f"keeps {_n(other, 'other shot')} as they are")
    if c.get("replaced"):
        bits.append(f"replaces {_n(c['replaced'], 'shot')}")
    if c.get("new"):
        bits.append(f"adds {_n(c['new'], 'new shot')}")
    return ("This " + ", ".join(bits) + ".") if bits else "No change to the cut."


# ------------------------------------------------------------------ cost


def cost_words(usd: float) -> dict:
    """A price as the board words it (`rcCost.tag`): on the subscription backend nothing
    is billed — "free on your plan", the API-equivalent estimate in the hover; on the
    API backend the estimate itself."""
    usd = round(max(0.0, float(usd or 0.0)), 2)
    charged = config.charges_money()
    terms = config.cost_terms()
    return {**terms, "usd": usd,
            "words": f"~${usd:.2f}" if charged else "free on your plan",
            "hover": (f"~${usd:.2f}, billed per call on the API" if charged
                      else f"~${usd:.2f} if it went through the API — no charge on your plan")}


# ------------------------------------------------------------------ a run that ended early
#
# Verdict 3: after Stop the panel said "Not run on this bin yet." A stopped or failed run
# is a state the editor reads — when, what finished, and what a re-run picks up — and
# never the never-run state.


def finished_words(counts: dict | None) -> str:
    """"heard 12/12 · looked 1/12" — what the index had finished."""
    c = counts or {}
    n = int(c.get("clips") or 0)
    if not n:
        return ""
    return (f"heard {int(c.get('listened') or 0)}/{n} · "
            f"looked {int(c.get('looked') or 0)}/{int(c.get('look_need') or n)}")


def resume_words(counts: dict | None, step: str | None = None) -> str:
    """What a re-run does first and then: "looks at 11 more, then picks, then the cut".
    The index keeps what finished, so a re-run starts where this one stopped."""
    c = counts or {}
    n = int(c.get("clips") or 0)
    todo = []
    heard, looked = int(c.get("listened") or 0), int(c.get("looked") or 0)
    need = int(c.get("look_need") or n)
    if n and heard < n:
        todo.append(f"hears {n - heard} more")
    if need and looked < need:
        todo.append(f"looks at {need - looked} more")
    if step == "cut" and not todo:
        return "writes the cut again (the index and the picks are done)"
    todo += ["then picks", "then the cut"] if todo else ["picks", "then the cut"]
    return ", ".join(todo)


def plan_note(locked: int, mode: str, extra: str = "",
              rejected: list[dict] | None = None) -> str:
    """The note a re-run sends with the cut (the Ask's own call needs one), with the
    ranges the editor rejected before (`rejected_brief`)."""
    lead = ("Re-run the first pass: rebuild this cut as the first pass would, from the "
            "index, the ranked events and the editor's picks, for the target length.")
    if locked:
        lead += (f" {locked} shot{'s are' if locked != 1 else ' is'} LOCKED (the editor's "
                 "own edits): return each exactly as it is; replace, re-order or drop "
                 "anything else.")
    else:
        lead += " Nothing is locked: replace whatever the material serves better."
    no = rejected_brief(rejected or [])
    if no:
        lead += f"\n\n{no}"
    extra = (extra or "").strip()
    return f"{lead}\n\nThe editor adds: {extra}" if extra else lead


def record_path(work: Path, bin_name: str) -> Path:
    return Path(work) / "firstpass" / f"{bin_name}.json"
