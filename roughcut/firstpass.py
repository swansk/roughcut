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

import time
from pathlib import Path

from . import config

PROV_KEY = "provenance"
BASES_KEPT = 10
EPS = 0.005

MODES = ("run", "rerun")

# How a shot became the editor's own, in the order the panel lists them.
HOW = ("add", "razor", "trim", "speed", "move")
HOW_WORDS = {"add": "added by hand", "razor": "razored", "trim": "trimmed",
             "speed": "speed changed", "move": "moved"}


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
                hand: dict[str, list[str]] | None = None, at: float | None = None) -> dict:
    """An accepted proposal becomes the newest base: its shots are the machine's, except
    the ids in `hand` (shots a re-run kept because the editor made them — they stay the
    editor's, with how). Mutates and returns `edl`."""
    hand = {str(k): list(v) for k, v in (hand or {}).items()}
    shots = {}
    order = []
    for s in segments:
        sid = s.get("id")
        if not sid:
            continue
        order.append(sid)
        shots[sid] = {"clip": s.get("clip"), "in": round(float(s["in"]), 2),
                      "out": round(float(s["out"]), 2),
                      "speed": float(s.get("speed") or 1.0)}
    base = {"job": job, "at": round(at if at is not None else time.time(), 3),
            "shots": shots, "order": order, "hand": hand}
    prov = edl.get(PROV_KEY) if isinstance(edl.get(PROV_KEY), dict) else {}
    prov["bases"] = ([base] + bases(edl))[:BASES_KEPT]
    edl[PROV_KEY] = prov
    return edl


def is_accept(saved: list[dict], plan_segments: list[dict]) -> bool:
    """Whether a save is the Accept of this plan: the same shots, in the same order,
    with the same ranges (the board's Accept saves `pendingPlan.segments` verbatim)."""
    plan = [s for s in plan_segments or [] if isinstance(s, dict) and "in" in s]
    return bool(plan) and len(plan) == len(saved) and all(
        _same(a, b, speed=False) for a, b in zip(saved, plan))


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
    cur_ids = [s.get("id") for s in segs]
    common = [x for x in base.get("order") or [] if x in cur_ids]
    in_order = _lcs(common, [x for x in cur_ids if x in common])
    for i, s in enumerate(segs):
        sid = s.get("id")
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
                pieces = [t for t in segs if t is not s and not shots.get(t.get("id"))
                          and t.get("clip") == s.get("clip")
                          and float(t["in"]) >= b["in"] - EPS
                          and float(t["out"]) <= b["out"] + EPS]
                how.append("razor" if pieces else "trim")
            if abs(float(s.get("speed") or 1.0) - float(b.get("speed") or 1.0)) > 1e-6:
                how.append("speed")
        if sid and sid in common and sid not in in_order:
            how.append("move")
        if how:
            out.append(_row(s, i, sorted(set(how), key=HOW.index)))
    return out


def _row(s: dict, i: int, how: list[str]) -> dict:
    return {"id": s.get("id"), "pos": i + 1, "clip": s.get("clip"),
            "in": round(float(s["in"]), 2), "out": round(float(s["out"]), 2),
            "how": how, "words": ", ".join(HOW_WORDS[h] for h in how)}


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


def plan_note(locked: int, mode: str, extra: str = "") -> str:
    """The note a re-run sends with the cut (the Ask's own call needs one)."""
    lead = ("Re-run the first pass: rebuild this cut as the first pass would, from the "
            "index, the ranked events and the editor's picks, for the target length.")
    if locked:
        lead += (f" {locked} shot{'s are' if locked != 1 else ' is'} LOCKED (the editor's "
                 "own edits): return each exactly as it is; replace, re-order or drop "
                 "anything else.")
    else:
        lead += " Nothing is locked: replace whatever the material serves better."
    extra = (extra or "").strip()
    return f"{lead}\n\nThe editor adds: {extra}" if extra else lead


def record_path(work: Path, bin_name: str) -> Path:
    return Path(work) / "firstpass" / f"{bin_name}.json"
