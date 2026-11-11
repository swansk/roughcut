"""Find that thinks (wave 25, lane FIND2): the model step above the instant hits.

Today's Find is instant words + picture matches (roughcut/find.py: its ranking is the gate's and does not
change). The Copper finder's audit says where that is not enough: "restroom on the plane" cannot reach the
spoken "porcelain throne" story, "a wide view of the mountains" gets "nothing" while a Found moment says
scenery, and "matches the picture" is printed on nearly every row so it tells her nothing. She also saw the
prompt-box AI find a jump to the frame by looking at the footage. So, after the instant hits are shown:

  1. `expand`   one text call widens the query (synonyms, what it could look like or sound like, whether
               it is about the picture), cached per query.
  2. `judge`   one text call reads the top candidates' own text (description, moments, transcript lines,
               the first pass's reasons) against the question and gives each hit a strength in words
               (strong / possible / weak) and a one-line why. "Nothing" is said only when the model,
               having read them, agrees, and never over a strong hit.
  3. `scan`    when the question is about the picture and nothing is strong: an offer to look through the
               footage (a bounded frame scan with roughcut/look.py, a frame a second over the clips the
               candidates came from), priced and counted first, with a Stop. The scan's answer is rows
               with the frames' times.

Nothing here changes `find.lexical`; the model's rows are ADDED above or beside it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from . import config, find, inference, look

MAX_TERMS = 8
JUDGE_TOP = 12
STRENGTHS = ("strong", "possible", "weak")
SCAN_FRAMES_CAP = 90          # ten sheets, as the prompt box's scan
SCAN_FPS = 1.0
SCAN_CLIPS = 4

# ------------------------------------------------------------------ rows: strength in words, whole clips

def is_whole_clip(row: dict, clips: dict[str, dict]) -> bool:
    """A hit that names no moment: it covers the clip, or most of a long one."""
    dur = float((clips.get(row["clip"]) or {}).get("duration") or 0.0)
    span = float(row["end"]) - float(row["start"])
    return span >= max(15.0, 0.7 * dur) if dur else span >= 30.0


def heuristic_strength(row: dict) -> str:
    """A strength for an instant row, from the evidence the instant layers have (no model):
    two sources agreeing or an exact spoken line is strong; one clearing its floor is possible."""
    ev = row.get("evidence") or []
    srcs = {str(e.get("source")).split("+")[0] for e in ev} | set(str(row.get("source", "")).split("+"))
    base = float(row.get("base") or 0.0)
    local = max([float(e.get("score") or 0.0) for e in ev if "local" in str(e.get("source"))], default=0.0)
    words = srcs & {"heard", "seen", "felt", "noted", "described"}
    if "heard" in srcs and base >= 0.99:
        return "strong"
    if words and local >= find.LOCAL_FLOOR:
        return "strong"
    if words and base >= find.LEXICAL_FLOOR:
        return "strong" if base >= 0.7 else "possible"
    if local >= find.LOCAL_FLOOR:
        return "possible"
    return "weak"


def why_line(row: dict) -> str:
    """The instant row's one line: what matched, in words (never "matches the picture")."""
    what = str(row.get("what") or "").strip()
    src = str(row.get("source") or "")
    first = re.split(r"(?<=[.!?])\s+| · ", what)[0][:150] if what else ""
    if src.startswith("heard"):
        return f"said here: “{first}”"
    if "noted" in src:
        return f"in the notes: {first}"
    if "described" in src:
        return f"the clip's description: {first}"
    if src == "local" or what.startswith("looks like"):
        return "the frames look like the question (no words match)"
    return first


def prepare(rows: list[dict], clips: dict[str, dict], *, demote: bool = True) -> list[dict]:
    """Instant rows as the board shows them: strength and one line, and a whole-clip hit ranks
    under every hit that names a moment unless it is the only strong one. (`demote=False` keeps the
    instant order: the judge must read the best candidates, not the ones the display order left on top.)"""
    out = []
    for r in rows:
        r = dict(r)
        r["whole"] = is_whole_clip(r, clips)
        r["strength"] = r.get("strength") or heuristic_strength(r)
        r["line"] = r.get("line") or why_line(r)
        out.append(r)
    return demote_whole(out) if demote else out


_ORDER = {"strong": 0, "possible": 1, "weak": 2}


def demote_whole(rows: list[dict]) -> list[dict]:
    """Stable: a whole-clip hit (it names no moment) ranks under every hit that names one at the
    same strength; a strong whole-clip hit still ranks over a possible or weak moment hit, so it
    keeps the top when it is the only strong one. Rows are ordered by strength first."""
    return sorted(rows, key=lambda r: (_ORDER.get(r.get("strength"), 1), bool(r.get("whole"))))


# ------------------------------------------------------------------ 1. expand

EXPAND_SCHEMA = {
    "terms": ["words or short phrases the thing could be called, or said as"],
    "about_picture": True,
    "notes": "one clause",
}
EXPAND_SYSTEM = (
    "You help a film editor search raw footage (a ski trip, a travel day, friends). Given what they are "
    "looking for, you list other ways it could be put into words: synonyms, what a person in the clip "
    "might SAY about it (slang, jokes), and what it would LOOK like in a transcript or a description of "
    "the frames. You also say whether the question is about something you would only see "
    "(a jump, a view, someone holding a thing) rather than hear.")


def expand_prompt(query: str) -> str:
    return f"""The editor is searching for: {query.strip()}

Return `terms`: up to {MAX_TERMS} short words or phrases (1-4 words each) that would match a transcript line or a
description of the same moment in other words. Include slang and indirect ways people say it ("the porcelain
throne" for a toilet), the kinds of scene it belongs to ("scenery", "view" for a landscape), and the gear or
action words a description would use ("airborne", "in the air", "off a jump" for a jump). Not the query itself.
`about_picture` is true when the moment would be found by looking at the frames (a jump, a view, someone
holding or wearing a thing) and false when it is mostly something said."""


def validate_expand(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("expected an object")
    terms = []
    for t in payload.get("terms") or []:
        t = re.sub(r"\s+", " ", str(t)).strip()[:60]
        if t and t.lower() not in {x.lower() for x in terms}:
            terms.append(t)
    return {"terms": terms[:MAX_TERMS], "about_picture": bool(payload.get("about_picture")),
            "notes": str(payload.get("notes") or "")[:160]}


def expand(query: str, cache_dir: Path | None = None) -> dict:
    """The widened query, cached per query (a file in the bin's work folder). -> {terms,
    about_picture, notes, cached, usage}."""
    key = re.sub(r"\s+", " ", query.strip().lower())
    cache: dict = {}
    path = Path(cache_dir) / "expansions.json" if cache_dir else None
    if path and path.exists():
        try:
            cache = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
    if key in cache:
        return {**cache[key], "cached": True, "usage": {}}
    r = inference.complete(expand_prompt(query), role=config.ROLE_ANALYSIS, schema=EXPAND_SCHEMA,
                           system=EXPAND_SYSTEM, validate=validate_expand, retries=1, isolated=True)
    out = dict(r.content)
    if path:
        cache[key] = out
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    return {**out, "cached": False,
            "usage": {"projected_usd": r.projected_usd, "model": r.model, "latency_ms": r.latency_ms}}


def widen(rows: list[dict], query: str, terms: list[str], clips: dict[str, dict], per_term: int = 3) -> list[dict]:
    """The instant rows plus the words matches the expanded terms find (find.lexical per term, the
    best few each), those whose window an existing row already covers left out. Rows added this
    way carry `via` (the term)."""
    out = [dict(r) for r in rows]
    for term in terms:
        for r in find.lexical(term, clips)[:per_term]:
            if any(o["clip"] == r["clip"] and r["start"] <= o["end"] + 1.0 and r["end"] >= o["start"] - 1.0 for o in out):
                continue
            out.append({**r, "via": term, "label": find._label(r.get("source", "")),
                        "evidence": [find._evidence(r, r.get("source", ""), len(out))]})
    return out


# ------------------------------------------------------------------ 2. judge

JUDGE_SCHEMA = {
    "ranking": [{"n": 1, "strength": "strong | possible | weak", "why": "one short line: what is there"}],
    "nothing": False,
    "notes": "one sentence",
}
JUDGE_SYSTEM = (
    "You help a film editor find a moment in raw footage. You are given numbered candidate hits, each with "
    "the text the index holds about that stretch (the clip's description, what was found in it, the words "
    "said, the first pass's notes). You judge whether each shows or says what the editor asked for, using "
    "only that text. You never invent timestamps or candidates.")


def candidate_text(row: dict, clip: dict, *, pad: float = 2.0) -> str:
    """What the index holds about this hit's stretch, as plain text for the judge."""
    s, e = float(row["start"]) - pad, float(row["end"]) + pad
    vis = clip.get("visual") or {}
    bits = []
    desc = str(vis.get("description") or vis.get("summary") or "").strip()
    if desc:
        bits.append(f"clip: {desc[:330]}")
    ms = [m for m in (vis.get("moments") or []) + (vis.get("unsupported") or [])
          if m.get("what") and float(m.get("end", 0)) >= s and float(m.get("start", 0)) <= e][:3]
    for m in ms:
        bits.append(f"found at {m['start']:.0f}s ({m.get('kind', '')}): {str(m['what'])[:220]}")
    said = [u for u in clip.get("transcript") or [] if float(u.get("end", 0)) >= s and float(u.get("start", 0)) <= e][:4]
    if said:
        bits.append("said: " + " / ".join(str(u.get("text", "")).strip() for u in said)[:260])
    for n in [n for n in clip.get("notes") or [] if float(n.get("end", 0)) >= s and float(n.get("start", 0)) <= e][:2]:
        bits.append(f"note: {str(n.get('text', ''))[:200]}")
    return " | ".join(bits) or "(no text about this stretch)"


def judge_prompt(query: str, rows: list[dict], clips: dict[str, dict], terms: list[str]) -> str:
    lines = []
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. {r['clip']} {r['start']:.1f}-{r['end']:.1f} s\n   {candidate_text(r, clips.get(r['clip']) or {})}")
    alt = f"\nIt could also be said as: {', '.join(terms)}." if terms else ""
    return f"""The editor is looking for: {query.strip()}{alt}

## Candidates
{chr(10).join(lines)}

## What to return
For every number, `strength`: "strong" if the text says or shows what the editor asked for, "possible" if it
plausibly does but the text does not settle it, "weak" if it is a look-alike or a different thing. `why` is one
short plain line saying what is there (not "matches the question"). A word that only sounds alike, or a thing
the text says is NOT there ("no falls"), is weak. `nothing` is true only if no candidate fits: never true when
any is strong. `notes` is one sentence."""


def validate_judge(payload: Any, n: int) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("ranking"), list):
        raise ValueError("expected an object with a 'ranking' list")
    seen: dict[int, dict] = {}
    for r in payload["ranking"]:
        if not isinstance(r, dict):
            continue
        try:
            k = int(r["n"])
        except (KeyError, TypeError, ValueError):
            continue
        st = str(r.get("strength", "")).strip().lower()
        if 1 <= k <= n and st in STRENGTHS:
            seen.setdefault(k, {"strength": st, "why": re.sub(r"\s+", " ", str(r.get("why", ""))).strip()[:160]})
    if not seen:
        raise ValueError("no candidate was judged")
    return {"ranking": seen, "nothing": bool(payload.get("nothing")), "notes": str(payload.get("notes", ""))[:240]}


def judge(query: str, rows: list[dict], clips: dict[str, dict], terms: list[str]) -> dict:
    cands = rows[:JUDGE_TOP]
    if not cands:
        return {"rows": [], "nothing": True, "notes": "", "usage": {}}
    r = inference.complete(judge_prompt(query, cands, clips, terms), role=config.ROLE_ANALYSIS, schema=JUDGE_SCHEMA,
                           system=JUDGE_SYSTEM, validate=lambda p: validate_judge(p, len(cands)), retries=1,
                           isolated=True)
    v = r.content
    out = apply_judge(rows, v)
    out["usage"] = {"projected_usd": r.projected_usd, "model": r.model, "latency_ms": r.latency_ms}
    return out


def apply_judge(rows: list[dict], verdict: dict) -> dict:
    """The judged list: strong, then possible, then weak (stable inside each; a whole-clip hit
    under any moment hit of the same strength unless it is the only strong one); rows past the judged
    ones keep their place after. `nothing` is True only if the model said so AND no row is strong."""
    judged, rest = [], []
    for i, r in enumerate(rows, 1):
        v = verdict["ranking"].get(i)
        r = dict(r)
        if v:
            r["strength"], r["line"], r["judged"] = v["strength"], v["why"] or r.get("line", ""), True
            judged.append(r)
        else:
            rest.append(r)
    order = {"strong": 0, "possible": 1, "weak": 2}
    judged = demote_whole(judged)
    strong = any(r["strength"] == "strong" for r in judged)
    nothing = bool(verdict.get("nothing")) and not strong and not any(r["strength"] == "possible" for r in judged)
    return {"rows": judged + rest, "nothing": nothing, "notes": verdict.get("notes", "")}


# ------------------------------------------------------------------ 3. the picture scan

def scan_clips(rows: list[dict], clips: dict[str, dict], n: int = SCAN_CLIPS) -> list[str]:
    seen: list[str] = []
    for r in rows:
        if r["clip"] not in seen and (clips.get(r["clip"]) or {}).get("duration"):
            seen.append(r["clip"])
        if len(seen) >= n:
            break
    return seen


def scan_request(names: list[str], clips: dict[str, dict], cap: int = SCAN_FRAMES_CAP) -> dict:
    """look.plan's request: every named clip at about a frame a second (thinner if the clips
    together would pass the cap), in 30 s chunks (look.MAX_SPAN_S)."""
    total = sum(float(clips[c]["duration"]) for c in names if c in clips) or 1.0
    dens = "coarse"          # 1 frame a second; look.plan thins every range together when the cap bites
    looks = []
    for c in names:
        dur = float(clips[c]["duration"])
        t = 0.0
        while t < dur - 0.05:
            looks.append({"clip": c, "in": t, "out": max(t + 0.1, min(dur - 0.15, t + look.MAX_SPAN_S)), "density": dens, "reason": "searching"})
            t += look.MAX_SPAN_S
    return {"scan": False, "looks": looks}


def scan_plan(names: list[str], clips: dict[str, dict], cap: int = SCAN_FRAMES_CAP) -> dict:
    """The frames a scan would cut, bounded: look.plan on clip ranges. `words` says the count."""
    req = scan_request(names, clips, cap)
    # look.clean_request caps MAX_ITEMS, but plan() itself takes the items as given
    p = look.plan({"segments": []}, req, look_cap=cap)
    p["clips"] = names
    return p


SCAN_SCHEMA = {
    "found": [{"clip": "GX010490.MP4", "start": 20.0, "end": 20.3, "strength": "strong | possible",
               "why": "what the frames show"}],
    "nothing": False,
}
SCAN_SYSTEM = (
    "You help a film editor find a moment in raw footage by looking at contact sheets: frames sampled about "
    "a second apart, each labelled with its clip and time. You report the frames that show what the editor "
    "asked for, with the label times. You never guess times between labels and you never report what you cannot see.")


def scan_prompt(query: str, sheets_block: str) -> str:
    return f"""The editor is looking for: {query.strip()}

The attached sheets:
{sheets_block}

Return `found`: each stretch of frames that shows it, with `clip` (the name in the label), `start` and `end` (label
times, seconds; one frame is fine: start = end), `strength` ("strong" if you can see it plainly in the named
frames, "possible" if it might be), and `why` (what the frames show, one line). If it is not in any frame, `found`
is empty and `nothing` is true."""


def validate_scan(payload: Any, durations: dict[str, float]) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("found", []), list):
        raise ValueError("expected an object with a 'found' list")
    rows = []
    for m in payload.get("found") or []:
        if not isinstance(m, dict):
            continue
        clip = next((c for c in durations if Path(c).stem.lower() == Path(str(m.get("clip", ""))).stem.lower()), None)
        try:
            a, b = float(m["start"]), float(m["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if clip is None or not (0 <= a <= b <= durations[clip] + 1.0):
            continue
        st = str(m.get("strength", "possible")).lower()
        rows.append({"clip": clip, "start": round(a - 0.5 if a == b else a, 2), "end": round(b + 0.5 if a == b else b, 2),
                     "strength": st if st in ("strong", "possible") else "possible",
                     "why": re.sub(r"\s+", " ", str(m.get("why", ""))).strip()[:160]})
    return {"found": rows, "nothing": bool(payload.get("nothing")) and not rows}


def scan(query: str, plan_: dict, clips: dict[str, dict], proxy_of: Callable[[str], Path], frames_dir: Path,
         out_dir: Path, *, stop: Callable[[], bool] | None = None,
         on_progress: Callable[[int, int], None] | None = None) -> dict:
    """Cut the frames (Stopped between frames), read them in one call. -> {rows, nothing, usage}."""
    sheets = look.build_sheets(plan_, proxy_of, frames_dir, out_dir, stop=stop, on_progress=on_progress)
    durations = {c: float(clips[c]["duration"]) for c in plan_.get("clips") or [] if c in clips}
    block = look.sheets_block({"ranges": []}, sheets)
    r = inference.complete(scan_prompt(query, block), role=config.ROLE_ANALYSIS, images=[Path(s["file"]) for s in sheets],
                           schema=SCAN_SCHEMA, system=SCAN_SYSTEM, validate=lambda p: validate_scan(p, durations),
                           retries=1, isolated=True)
    res = r.content
    rows = [{"clip": m["clip"], "start": m["start"], "end": m["end"], "what": m["why"], "line": m["why"],
             "why": "looked through the frames", "source": "looked", "label": "looked at the frames",
             "strength": m["strength"], "judged": True, "score": 2.0 if m["strength"] == "strong" else 1.0,
             "evidence": []} for m in res["found"]]
    return {"rows": rows, "nothing": res["nothing"], "frames": len(plan_["frames"]),
            "usage": {"projected_usd": r.projected_usd, "model": r.model, "latency_ms": r.latency_ms}}
