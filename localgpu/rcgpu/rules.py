"""The decisions, with no heavy imports (so the app's tests can read them): which questions a
telemetry moment is asked, and what a P(yes) means."""
from __future__ import annotations

FALL_THRESHOLD = 0.2       # P(yes) for "falls": from the bake-off AUROC; 0.2-0.9 is Claude's band


def questions_for(cand: dict) -> list[str]:
    """Which questions a telemetry candidate is asked (bake-off run_matrix): an air candidate
    is asked 'air' (advisory, never settles); every other kind 'fall'. The bake-off also asked
    impacts 'air' — a coin flip (AUROC 0.4-0.6) — which is 1/3 of the GPU time for no evidence."""
    if cand.get("questions"):             # an eval window names its own
        return list(cand["questions"])
    return ["air"] if cand.get("kind") == "air" else ["fall"]


def verdict(q: str, p: float | None) -> str:
    """'likely' / 'unlikely' for a fall; 'advisory' for anything else — **an air is never
    settled locally**; 'unchecked' when there was nothing to read."""
    if p is None:
        return "unchecked"
    if q != "fall":
        return "advisory"
    return "likely" if p >= FALL_THRESHOLD else "unlikely"
