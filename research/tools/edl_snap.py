# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Snap EDL cut points to conversational boundaries using the transcript.

A cut chosen from a contact sheet lands wherever the 0.5s sample grid happened to
fall, which routinely means starting mid-word or dropping the reply that makes a
line make sense. The transcript already knows where utterances begin and end, so
the boundaries can be derived rather than eyeballed.

Three passes per segment, each recorded so the result is auditable:

  head    if `in` lands inside an utterance, move back to its start (someone is
          already mid-sentence when the shot opens)
  tail    if `out` lands inside an utterance, extend to its end (never cut a line
          off mid-delivery)
  closure if another utterance begins within `--gap` of the new end, absorb it —
          this is the response that gives an exchange its closure. Repeats while
          the conversation keeps going, bounded by `--max-extend`.

Growth is capped because "let the conversation finish" and "keep the cut tight"
are in genuine tension; the cap is where that trade-off is made explicit rather
than hidden inside a heuristic.

Usage:
    uv run edl_snap.py IN.json --sidecars ~/work/audio -o OUT.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

PAD_HEAD = 0.25             # breath before the first word
PAD_TAIL = 0.45             # let the last word land before cutting


def load_transcript(sidecars: Path, clip: str) -> list[dict]:
    p = sidecars / f"{Path(clip).stem}.audio.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))["transcript"]


def clip_duration(sidecars: Path, clip: str) -> float:
    p = sidecars / f"{Path(clip).stem}.audio.json"
    if not p.exists():
        return float("inf")
    return float(json.loads(p.read_text(encoding="utf-8"))["duration_s"])


def snap(seg: dict, transcript: list[dict], duration: float, gap: float,
         max_extend: float) -> tuple[dict, list[str]]:
    t_in, t_out = float(seg["in"]), float(seg["out"])
    orig_in, orig_out = t_in, t_out
    notes: list[str] = []

    # head — opening mid-utterance
    for u in transcript:
        if u["start"] < t_in < u["end"]:
            moved = max(0.0, u["start"] - PAD_HEAD)
            if t_in - moved <= max_extend:
                t_in = moved
                notes.append(f"head -{orig_in - t_in:.2f}s to '{u['text'][:38]}'")
            break

    # tail — cutting a line off mid-delivery
    for u in transcript:
        if u["start"] < t_out < u["end"]:
            moved = min(duration, u["end"] + PAD_TAIL)
            if moved - t_out <= max_extend:
                t_out = moved
                notes.append(f"tail +{t_out - orig_out:.2f}s to finish '{u['text'][:38]}'")
            break

    # closure — absorb the reply, and the reply to the reply.
    # Only for segments that actually carry speech. A shot chosen as a silent beat
    # (a ski pass, a held landscape) has no conversation to finish, and reaching
    # forward to the next utterance would grow it into dialogue the editor did not
    # ask for — found by test_snap_leaves_clean_boundaries_alone.
    carries_speech = any(u["end"] > t_in and u["start"] < t_out for u in transcript)
    while carries_speech:
        nxt = next((u for u in transcript if u["start"] >= t_out - PAD_TAIL), None)
        if nxt is None or nxt["start"] - (t_out - PAD_TAIL) > gap:
            break
        cand = min(duration, nxt["end"] + PAD_TAIL)
        if cand - orig_out > max_extend or cand <= t_out:
            break
        t_out = cand
        notes.append(f"closure +{nxt['end'] - nxt['start']:.2f}s '{nxt['text'][:38]}'")

    out = dict(seg)
    out["in"], out["out"] = round(t_in, 2), round(t_out, 2)
    if notes:
        out["snapped_from"] = [round(orig_in, 2), round(orig_out, 2)]
    return out, notes


def main() -> int:
    ap = argparse.ArgumentParser(description="Snap EDL cuts to conversational boundaries.")
    ap.add_argument("edl", type=Path)
    ap.add_argument("--sidecars", type=Path, required=True)
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--gap", type=float, default=1.2,
                    help="absorb a following utterance starting within this many seconds")
    ap.add_argument("--max-extend", type=float, default=6.0,
                    help="cap on how far one boundary may move")
    args = ap.parse_args()

    edl = json.loads(args.edl.read_text(encoding="utf-8"))
    before = sum(s["out"] - s["in"] for s in edl["segments"])

    snapped, changed = [], 0
    for seg in edl["segments"]:
        transcript = load_transcript(args.sidecars, seg["clip"])
        dur = clip_duration(args.sidecars, seg["clip"])
        new, notes = snap(seg, transcript, dur, args.gap, args.max_extend)
        snapped.append(new)
        if notes:
            changed += 1
            print(f"{seg['clip']} {seg['in']:6.1f}-{seg['out']:6.1f} -> "
                  f"{new['in']:6.1f}-{new['out']:6.1f}")
            for n in notes:
                print(f"      {n}")

    edl["segments"] = snapped
    edl["snapped"] = {"gap_s": args.gap, "max_extend_s": args.max_extend,
                      "pad_head_s": PAD_HEAD, "pad_tail_s": PAD_TAIL}
    after = sum(s["out"] - s["in"] for s in snapped)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(edl, indent=1), encoding="utf-8")
    print(f"\n{changed}/{len(snapped)} segments adjusted; "
          f"{before:.1f}s -> {after:.1f}s ({after - before:+.1f}s)")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
