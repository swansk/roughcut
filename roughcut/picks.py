"""Picks: the moments the index proposes for the cutting room floor.

A pick is a window of one clip with a reason and its **witnesses** — who saw it, what was
heard, what a sensor felt, which theme it serves — assembled from evidence the pipeline
already produces: the R8 speech candidates in the audio sidecars, the ranked events file
the visual pass derives, an optional telemetry summary, and the editor's themes. It is a
place to *look*, never a cut: the floor plays it, the human keeps or rejects it, and only a
keep becomes a select in the bin.

Three rules from the design, all learned the hard way earlier in this project:

  * **A witness carries its state.** A contact-sheet claim is a claim (R10): it is
    `claimed` until a closer look agreed (`audited`) or disagreed (`contradicted`). The
    reason line is built only from witnesses that agree, and a contradiction is kept
    visible as `conflict` rather than resolved away.
  * **The telemetry witness says numbers, never event names**, until R11 measures it
    (docs/INTAKE.md, M6). It cannot promote a pick on its own.
  * **Verdicts anchor to clip time, not to pick ids.** A finer index produces new picks;
    the human's keeps, rejects and notes re-attach by overlap, so nothing is orphaned.

Pure: reads dicts, returns dicts. The server adds URLs; the floor renders.
"""

from __future__ import annotations

import hashlib
from typing import Iterable

from .find import _matched, _tokens

# A pick this short plays whole; a longer one previews the moment ±PREVIEW_HALF_S and the
# human holds space to keep watching. What they watch is what they keep.
PREVIEW_WHOLE_S = 8.0
PREVIEW_HALF_S = 3.0
# Air around a spoken candidate, so playback lands a beat before the line.
PAD_HEAD_S = 0.5
PAD_TAIL_S = 0.5
# Windows on one clip closer than this are one moment.
MERGE_GAP_S = 3.0
# A stored verdict re-attaches to a pick when they share at least this fraction of the
# shorter of the two ranges.
REATTACH_MIN_OVERLAP = 0.5
ROUND_SIZE = 40

# What each witness kind is worth on its own, before corroboration. Seen events arrive
# with the events.py score (kind × notable × corroboration × confirmation), which already
# knows about contradiction; heard candidates carry the R8 score in 0–1.
HEARD_GAIN = 0.8
THEME_BONUS = 0.25
# Two independent kinds agreeing is the only strong evidence available (R10); each extra
# kind beyond the first is worth this much.
CORROBORATION_BONUS = 0.3
# A pick whose only seen witness was contradicted keeps a floor, not a zero: the words
# may still carry it, but the sheet's claim must not.
CONTRADICTED_FACTOR = 0.35

STATE_BY_CONFIRMATION = {
    "confirmed": "audited",
    "unseen": "claimed",
    "unsupported": "contradicted",
    "contradicted": "contradicted",
}


def pick_id(clip: str, start: float, end: float) -> str:
    h = hashlib.sha1(f"{clip}|{start:.2f}|{end:.2f}".encode()).hexdigest()
    return f"p_{h[:8]}"


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def _overlap_ratio(a: tuple[float, float], b: tuple[float, float]) -> float:
    shorter = max(1e-6, min(a[1] - a[0], b[1] - b[0]))
    return _overlap(a, b) / shorter


def _lines_in(transcript: list[dict], start: float, end: float) -> str:
    said = [str(u.get("text", "")).strip() for u in transcript or []
            if float(u.get("end", 0)) > start and float(u.get("start", 0)) < end]
    return " / ".join(s for s in said if s)


# ----------------------------------------------------------------- witnesses


def heard_witnesses(clip: str, c: dict) -> list[dict]:
    """R8's speech candidates as witnesses: a window around the line, the line itself."""
    out = []
    duration = float(c.get("duration") or 0.0)
    for cand in c.get("candidates") or []:
        try:
            t = float(cand["t"])
        except (KeyError, TypeError, ValueError):
            continue
        end = float(cand.get("end") or t + 3.0)
        start, stop = max(0.0, t - PAD_HEAD_S), end + PAD_TAIL_S
        if duration:
            stop = min(duration, stop)
        text = _lines_in(c.get("transcript") or [], t, end) or str(cand.get("why", ""))
        out.append({"kind": "heard", "clip": clip, "start": round(start, 2),
                    "end": round(stop, 2), "at": round(t, 2), "text": text,
                    "state": None, "score": float(cand.get("score") or 0.0)})
    return out


def seen_witnesses(clip: str, events: Iterable[dict]) -> list[dict]:
    """The ranked events as witnesses, each carrying the state its confirmation earned."""
    out = []
    for e in events:
        if e.get("clip") != clip:
            continue
        why = e.get("why_ranked") or {}
        status = why.get("confirmation", "unseen")
        state = STATE_BY_CONFIRMATION.get(status, "claimed")
        at = why.get("peak_at") if why.get("corroboration_z", 0) >= 2.0 else e["start"]
        out.append({"kind": "seen", "clip": clip, "start": float(e["start"]),
                    "end": float(e["end"]), "at": round(float(at), 2),
                    "text": str(e.get("what", "")).strip(), "state": state,
                    "event_kind": str(e.get("kind", "")), "notable": bool(e.get("notable")),
                    "score": float(e.get("score") or 0.0), "rank": e.get("rank")})
    return out


def felt_witnesses(clip: str, telemetry: dict | None) -> list[dict]:
    """Telemetry peaks as witnesses — numbers only, no names, no state. Optional: a clip
    without a telemetry summary contributes nothing and loses nothing (INTAKE M6)."""
    out = []
    for p in (telemetry or {}).get("peaks") or []:
        try:
            at = float(p["at"])
        except (KeyError, TypeError, ValueError):
            continue
        value = p.get("value")
        unit = p.get("unit", "")
        text = f"{value:g} {unit}".strip() if isinstance(value, (int, float)) else str(value)
        out.append({"kind": "felt", "clip": clip, "start": round(at - 1.0, 2),
                    "end": round(at + 1.0, 2), "at": round(at, 2),
                    "text": text, "state": None, "score": 0.0})
    return out


def theme_hits(themes: list[str], witnesses: list[dict]) -> list[str]:
    """Which themes this pick's own words and sights answer for."""
    hits = []
    for theme in themes or []:
        q = _tokens(theme)
        if not q:
            continue
        for w in witnesses:
            if w["kind"] in ("heard", "seen") and _matched(q, w.get("text", "")):
                hits.append(theme)
                break
    return hits


# ---------------------------------------------------------------------- build


def _merge(witnesses: list[dict]) -> list[list[dict]]:
    """Group one clip's witnesses into moments: sorted by start, joined while they
    overlap or sit within MERGE_GAP_S of each other."""
    groups: list[list[dict]] = []
    for w in sorted(witnesses, key=lambda x: (x["start"], x["end"])):
        if groups and w["start"] - max(g["end"] for g in groups[-1]) <= MERGE_GAP_S:
            groups[-1].append(w)
        else:
            groups.append([w])
    return groups


def _pick_from(clip: str, group: list[dict], duration: float,
               themes: list[str] | None) -> dict:
    start = min(w["start"] for w in group)
    end = max(w["end"] for w in group)
    if duration:
        end = min(end, duration)
    seen = [w for w in group if w["kind"] == "seen"]
    heard = [w for w in group if w["kind"] == "heard"]
    felt = [w for w in group if w["kind"] == "felt"]
    agreeing_seen = [w for w in seen if w["state"] != "contradicted"]
    contradicted = [w for w in seen if w["state"] == "contradicted"]

    # Score: the strongest witness, then corroboration across kinds, then the theme.
    base = 0.0
    if agreeing_seen:
        base = max(w["score"] for w in agreeing_seen)
    elif contradicted:
        base = max(w["score"] for w in contradicted) * CONTRADICTED_FACTOR
    heard_best = max((w["score"] for w in heard), default=0.0) * HEARD_GAIN
    score = max(base, heard_best)
    # Corroboration across kinds. The felt witness counts only as a *freefall run* (R11:
    # the one telemetry shape clean enough to carry a small weight); an impact peak is a
    # number shown on the witness and never moves the rank.
    kinds_present = {w["kind"] for w in group if w["kind"] != "felt"}
    if any(w["kind"] == "felt" and "freefall" in str(w.get("text", "")) for w in felt):
        kinds_present.add("felt")
    score += CORROBORATION_BONUS * max(0, len(kinds_present) - 1)
    hits = theme_hits(themes or [], group)
    if hits:
        score += THEME_BONUS

    # The reason: built from what agrees; the conflict stated, never resolved.
    parts = []
    if agreeing_seen:
        best = max(agreeing_seen, key=lambda w: w["score"])
        parts.append(best["text"])
    if heard:
        parts.append(f"\"{heard[0]['text']}\"")
    if felt:
        parts.append("telemetry: " + ", ".join(w["text"] for w in felt))
    why = " — ".join(p for p in parts if p)
    conflict = ""
    if contradicted:
        claim = max(contradicted, key=lambda w: w["score"])
        conflict = (f"the sheet claimed \"{claim['text']}\" ({claim.get('event_kind', '')}) "
                    f"— a closer look did not support it")

    # The anchor is the strongest witness's moment; the preview is what plays by default.
    strongest = max(group, key=lambda w: (w["kind"] == "seen" and w["state"] == "audited",
                                          w["score"]))
    anchor = float(strongest["at"])
    length = end - start
    if length <= PREVIEW_WHOLE_S:
        preview = [round(start, 2), round(end, 2)]
    else:
        preview = [round(max(start, anchor - PREVIEW_HALF_S), 2),
                   round(min(end, anchor + PREVIEW_HALF_S), 2)]
    kind = (max(agreeing_seen, key=lambda w: w["score"])["event_kind"]
            if agreeing_seen else "speech" if heard else "felt" if felt else "seen")
    return {
        "id": pick_id(clip, start, end), "clip": clip,
        "start": round(start, 2), "end": round(end, 2), "anchor": round(anchor, 2),
        "preview": preview, "why": why, "conflict": conflict, "kind": kind,
        "score": round(score, 3), "tags": hits,
        "witnesses": [{k: v for k, v in w.items() if k != "clip"} for w in group],
        "verdict": None, "hero": False, "note": "",
    }


def build(clips: dict[str, dict], events: list[dict], *,
          themes: list[str] | None = None,
          telemetry: dict[str, dict] | None = None,
          verdicts: list[dict] | None = None,
          selects: list[dict] | None = None) -> list[dict]:
    """Every pick in the bin, best first, with any stored verdicts re-attached.

    `clips` is the model-facing dict the server already builds (clip → duration,
    transcript, candidates, visual); `events` is `events.load(...)`. Everything else is
    optional and absent on a bin that has never been looked at or felt.
    """
    out: list[dict] = []
    for clip, c in clips.items():
        witnesses = (heard_witnesses(clip, c)
                     + seen_witnesses(clip, events or [])
                     + felt_witnesses(clip, (telemetry or {}).get(clip)))
        # A felt peak with nothing else near it is not a pick: numbers alone cannot
        # promote (INTAKE M6). It only ever joins a moment somebody else claimed.
        groups = [g for g in _merge(witnesses) if any(w["kind"] != "felt" for w in g)]
        duration = float(c.get("duration") or 0.0)
        out += [_pick_from(clip, g, duration, themes) for g in groups]
    out.sort(key=lambda p: (-p["score"], p["clip"], p["start"]))
    for i, p in enumerate(out, 1):
        p["rank"] = i
    return attach_verdicts(out, verdicts or [], selects or [])


def attach_verdicts(picks: list[dict], verdicts: list[dict],
                    selects: list[dict]) -> list[dict]:
    """Re-attach stored keeps and rejects to whatever picks exist now, by overlap.

    Keeps win over rejects when both overlap a pick — a keep is the more deliberate act,
    and a later reject of the same seconds removes the select (selects.apply_verdict).
    """
    for p in picks:
        span = (p["start"], p["end"])
        best_keep = None
        for s in selects:
            if s.get("clip") != p["clip"]:
                continue
            r = _overlap_ratio(span, (float(s["start"]), float(s["end"])))
            if r >= REATTACH_MIN_OVERLAP and (best_keep is None or r > best_keep[0]):
                best_keep = (r, s)
        if best_keep:
            s = best_keep[1]
            p["verdict"] = "pick"
            p["hero"] = bool(s.get("hero"))
            p["note"] = str(s.get("note", ""))
            p["select_id"] = s.get("id")
            continue
        for v in verdicts:
            if v.get("clip") != p["clip"]:
                continue
            if _overlap_ratio(span, (float(v["start"]), float(v["end"]))) >= REATTACH_MIN_OVERLAP:
                p["verdict"] = v.get("verdict")
                p["note"] = str(v.get("note", ""))
                break
    return picks


# --------------------------------------------------------------------- rounds


def order(picks: list[dict], mode: str = "rank") -> list[dict]:
    """`rank` (best first) or `clip` (chronological through the bin, then time)."""
    if mode == "clip":
        return sorted(picks, key=lambda p: (p["clip"], p["start"]))
    return sorted(picks, key=lambda p: p.get("rank", 0))


def rounds(picks: list[dict], size: int = ROUND_SIZE) -> list[list[dict]]:
    """Undecided picks in rounds of `size`. A round's queue is frozen by the floor when
    it starts; this only says how many there are."""
    pending = [p for p in picks if not p.get("verdict")]
    return [pending[i:i + size] for i in range(0, len(pending), size)] or [[]]
