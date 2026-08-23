"""Boundary polish: finish the word, and stop trailing off.

Karl on the Killington revision proposal: *"Dual issue on clip length, both with
similar frequency: (A) clip is too long and we trail off on conversation; (B) clip
is too short and you clip words, like POCKET PI[CLIP] — should finish PIZZA."*

Both are the same defect seen from two sides. The model picks in/out points by
reading a transcript, so its boundaries are only ever as good as the timestamps in
it — and those timestamps describe *when a word was decoded*, not when the sound
of it stops or when the shot has finished being interesting. Measured on this bin,
a word's own energy runs a median 0.12s and up to 0.26s past the end Whisper gives
it, which is why an out-point one frame after the last word still sounds like a
cut through it.

This is **not** `research/tools/edl_snap.py`, and it deliberately does much less.
Snap's third pass absorbs the *next* utterance, and the next one after that, to
give an exchange closure; that grew shots by whole lines and Karl preferred the
un-snapped cut for its boundaries (HANDOFF, "findings that must not be
re-litigated": *do not auto-snap an originated plan*; whole thoughts beat tight
boundaries). Polish never reaches for a line the model did not choose. It moves a
boundary by at most `MAX_EXTEND_S` and only ever:

  B  **finishes a word the out-point cuts through** — or lands within
     `NEAR_WORD_S` after, which sounds the same — by extending to that word's end
     plus a tail pad; and the same for an in-point that opens mid-word.
  A  **trims dead air** when the out-point sits more than `TRAIL_S` past the last
     word spoken in the shot and nothing notable is visible over that stretch.

Two shots are left exactly alone, on purpose:

  * a shot with **no speech in it at all** — a held landing, a lift-line beat. It
    was chosen for its picture, so there is no "last word" to trim to and nothing
    to finish. Guessing at its length is how a tool starts feeling untrustworthy.
  * a stretch the visual sidecar marks **unusable**. A boundary is never moved into
    or across one: the whole point of the visual pass is knowing where the picture
    is black, blurred or pointed at a glove, and trading a clipped word for four
    unwatchable frames is not a trade.

Pure: it reads a plan and the analysed clips and returns a new plan. Nothing on
disk is touched — least of all the EDL, which is Karl's cut.
"""

from __future__ import annotations

# Padding. PAD_TAIL/PAD_HEAD are edl_snap.py's numbers, kept identical so the two
# paths in the app cannot disagree about where a line ends — and now with a
# measurement behind them: over eight isolated line-ends on Killington the last
# word's own energy falls 12 dB below its peak a median 0.12s after the `e` in the
# sidecar, at most 0.26s (CLIP_07 307.74 "boys?"). 0.45s clears the longest of
# those and still lets the line land rather than stop dead.
PAD_HEAD = 0.25             # breath before the first word
PAD_TAIL = 0.45             # let the last word land before cutting

# An out-point this soon after a word ends is heard as cutting through it, because
# the word is still decaying — the "POCKET PI" case is 0.12s after `e`.
NEAR_WORD_S = 0.30
# Past this much silence the shot has stopped being about what was said. 1.5s is
# well beyond a conversational beat and beyond PAD_TAIL + the longest measured
# decay, so a shot that merely breathes is never mistaken for one that trails off.
TRAIL_S = 1.5
# No boundary moves further than this. A polish that can move a cut by seconds is
# a re-edit, and re-editing is what the Ask is for.
MAX_EXTEND_S = 1.2
MIN_SEG_S = 0.5             # never polish a shot down to nothing
UNUSABLE_EPS = 0.1          # a zero-length "black frame" still blocks a boundary


def _word_spans(transcript: list[dict]) -> list[tuple[float, float, str]]:
    """Every spoken word as (start, end, text), in time order.

    `e` (word end) is written by the current audio pass. Older sidecars — and the
    test fixtures — carry only `t`, so the end is inferred from the next word's
    start, bounded, and the last word falls back to the utterance end. That
    fallback is exactly why the pass now records `e`: an utterance end is a
    decoder artefact and is routinely earlier than the sound.
    """
    out: list[tuple[float, float, str]] = []
    for u in transcript or []:
        try:
            u_start, u_end = float(u["start"]), float(u["end"])
        except (KeyError, TypeError, ValueError):
            continue
        words = u.get("words") or []
        if not words:
            # No word timing at all: the utterance is the only span we have.
            out.append((u_start, u_end, str(u.get("text", "")).strip()))
            continue
        for i, w in enumerate(words):
            try:
                t = float(w["t"])
            except (KeyError, TypeError, ValueError):
                continue
            end = w.get("e")
            if end is None:
                nxt = words[i + 1].get("t") if i + 1 < len(words) else None
                end = min(float(nxt), t + 0.8) if nxt is not None else u_end
            out.append((t, max(float(end), t), str(w.get("w", "")).strip()))
    out.sort()
    return out


def _unusable_spans(visual: dict | None) -> list[tuple[float, float]]:
    spans = []
    for u in (visual or {}).get("unusable") or []:
        try:
            a, b = float(u["start"]), float(u["end"])
        except (KeyError, TypeError, ValueError):
            continue
        spans.append((a, max(b, a + UNUSABLE_EPS)))
    return spans


def _notable_spans(visual: dict | None) -> list[tuple[float, float]]:
    """Only the moments the reader marked notable protect a trailing stretch.

    Every clip in this bin is 34-92% covered by visual moments, so "overlaps any
    moment" would veto every trim there is. `notable` is the field the visual pass
    already uses to mean *this is worth looking at* — it renders as `!` in the Ask
    prompt — and it covers 10-71%, which is the discrimination the rule needs: a
    crash holds the shot, "wide snowy slope with bare trees" does not.
    """
    spans = []
    for m in (visual or {}).get("moments") or []:
        if not m.get("notable"):
            continue
        try:
            spans.append((float(m["start"]), float(m["end"])))
        except (KeyError, TypeError, ValueError):
            continue
    return spans


def _overlaps(a: float, b: float, spans: list[tuple[float, float]]) -> bool:
    lo, hi = (a, b) if a <= b else (b, a)
    return any(lo < e and hi > s for s, e in spans)


def polish_segment(seg: dict, clip: dict, floor: float = 0.0,
                   ceiling: float | None = None) -> tuple[dict, str]:
    """One shot. Returns the (possibly new) segment and a short reason, or "".

    `floor`/`ceiling` are the room this shot has to grow into — the neighbouring
    boundaries of other shots taken from the *same* clip. Without them, extending
    one shot's out over the next shot's in would play half a second of the same
    audio twice, which is a worse artefact than the one being fixed.
    """
    t_in, t_out = float(seg["in"]), float(seg["out"])
    orig = (t_in, t_out)
    duration = float(clip.get("duration") or 0.0) or t_out
    hi = duration if ceiling is None else min(duration, ceiling)
    words = _word_spans(clip.get("transcript") or [])
    unusable = _unusable_spans(clip.get("visual"))
    reasons: list[str] = []

    # Words heard in the shot. None at all means this was chosen as picture, and a
    # boundary rule derived from speech has no business touching it.
    inside = [w for w in words if w[0] < t_out and w[1] > t_in]
    if not inside:
        return dict(seg), ""

    # --- head: the shot opens with someone already mid-word --------------------
    opening = next((w for w in words if w[0] < t_in - 0.01 < w[1]), None)
    if opening is not None:
        moved = round(max(floor, opening[0] - PAD_HEAD), 2)
        if (t_in - moved <= MAX_EXTEND_S and moved < t_in
                and not _overlaps(moved, t_in, unusable)):
            reasons.append(f"in −{t_in - moved:.2f}s to the start of "
                           f"{opening[2][:24]!r}")
            t_in = moved

    # --- B: the out-point cuts a word, or lands so close it sounds like it ------
    last = max(inside, key=lambda w: w[1])
    clipped = next((w for w in words if w[0] < t_out < w[1]), None)
    if clipped is None and 0.0 <= t_out - last[1] <= NEAR_WORD_S:
        clipped = last
    if clipped is not None:
        moved = round(min(hi, clipped[1] + PAD_TAIL), 2)
        if (moved - t_out <= MAX_EXTEND_S and moved > t_out
                and not _overlaps(t_out, moved, unusable)):
            reasons.append(f"out +{moved - t_out:.2f}s to finish "
                           f"{clipped[2][:24]!r}")
            t_out = moved

    # --- A: the shot keeps running long after anyone stopped talking -----------
    elif t_out - last[1] > TRAIL_S:
        moved = round(last[1] + PAD_TAIL, 2)
        held = _notable_spans(clip.get("visual"))
        if (moved > t_in + MIN_SEG_S and not _overlaps(moved, t_out, held)
                and not _overlaps(moved - UNUSABLE_EPS, moved, unusable)):
            reasons.append(f"out −{t_out - moved:.2f}s: {t_out - last[1]:.1f}s of "
                           f"dead air after {last[2][:24]!r}")
            t_out = moved

    if not reasons or (t_in, t_out) == orig or t_out - t_in < MIN_SEG_S:
        return dict(seg), ""
    out = dict(seg)
    out["in"], out["out"] = round(t_in, 2), round(t_out, 2)
    out["polished_from"] = [round(orig[0], 2), round(orig[1], 2)]
    out["polish_why"] = "; ".join(reasons)
    return out, out["polish_why"]


def polish(segments: list[dict], clips: dict[str, dict]) -> list[dict]:
    """Polish every shot in a plan. Pure — the input list is not mutated."""
    out: list[dict] = []
    for i, seg in enumerate(segments):
        clip = clips.get(seg.get("clip"))
        if not clip:
            out.append(dict(seg))
            continue
        # Room to grow: the nearest boundary of another shot cut from this clip.
        same = [s for j, s in enumerate(segments)
                if j != i and s.get("clip") == seg["clip"]]
        floor = max((float(s["out"]) for s in same
                     if float(s["out"]) <= float(seg["in"]) + 0.01), default=0.0)
        ceiling = min((float(s["in"]) for s in same
                       if float(s["in"]) >= float(seg["out"]) - 0.01), default=None)
        polished, _ = polish_segment(seg, clip, floor=floor, ceiling=ceiling)
        out.append(polished)
    return out


def summarise(before: list[dict], after: list[dict]) -> str:
    """One line for the plan's notes, so the change is declared rather than felt."""
    changed = [s for s in after if s.get("polished_from")]
    if not changed:
        return ""
    finished = sum(1 for s in changed if "to finish" in s.get("polish_why", ""))
    trimmed = sum(1 for s in changed if "dead air" in s.get("polish_why", ""))
    opened = sum(1 for s in changed if "to the start of" in s.get("polish_why", ""))
    bits = []
    if finished:
        bits.append(f"{finished} finished a word the cut ran through")
    if trimmed:
        bits.append(f"{trimmed} lost trailing dead air")
    if opened:
        bits.append(f"{opened} opened on a whole word")
    b = sum(float(s["out"]) - float(s["in"]) for s in before)
    a = sum(float(s["out"]) - float(s["in"]) for s in after)
    return (f"Boundary polish adjusted {len(changed)} of {len(after)} shots "
            f"({', '.join(bits)}); {b:.1f}s → {a:.1f}s.")


def polish_plan(plan: dict, clips: dict[str, dict]) -> dict:
    """Apply polish to a validated plan and declare it in the plan's notes."""
    before = plan.get("segments") or []
    after = polish(before, clips)
    line = summarise(before, after)
    out = dict(plan)
    out["segments"] = after
    if line:
        notes = (plan.get("notes") or "").strip()
        out["notes"] = f"{notes}\n\n{line}".strip() if notes else line
    return out
