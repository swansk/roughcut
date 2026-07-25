# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Ask for a revision from the terminal — the same path the app's Ask button uses.

Exists so a live revision can be run and judged without the web app in the loop,
which matters when verifying the model call itself: if this works and the button
does not, the fault is in the web layer, and vice versa.

    uv run research/tools/ask.py \
        --edl ~/work/edl/B1-variantB-snapped.json \
        --sidecars ~/work/audio \
        --note "tighten the opening, more skiing in the middle" \
        --story "the running joke is a gallon of milk..." \
        -o ~/work/edl/B-proposal.json

Prints the proposal and the token/cost accounting; writes an EDL only with -o, and
never touches the input EDL.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import inference, revise      # noqa: E402


def load_clips(sidecars: Path) -> dict[str, dict]:
    clips: dict[str, dict] = {}
    for p in sorted(sidecars.glob("*.audio.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        clips[d["clip"]] = {
            "clip": d["clip"], "duration": d["duration_s"],
            "transcript": d.get("transcript", []),
            "summary": {k: d["summary"].get(k) for k in
                        ("speech_fraction", "wind_dominant_fraction", "audio_usable")},
        }
    return clips


def main() -> int:
    ap = argparse.ArgumentParser(description="Ask for a revised edit.")
    ap.add_argument("--edl", type=Path, required=True)
    ap.add_argument("--sidecars", type=Path, required=True)
    # --note-file/--story-file exist because passing prose through
    # `wsl -- bash -lc '...'` from Windows silently truncates it at the first space
    # (see docs/HANDOFF.md). A file has no quoting layer to get mangled.
    ap.add_argument("--note", help="the change you want, in plain language")
    ap.add_argument("--note-file", type=Path, help="read the note from a file")
    ap.add_argument("--story", default="")
    ap.add_argument("--story-file", type=Path, help="read the story from a file")
    ap.add_argument("-o", "--out", type=Path, help="write the proposed EDL here")
    ap.add_argument("--target", type=float, nargs=2, default=(120.0, 180.0))
    args = ap.parse_args()

    note = (args.note_file.expanduser().read_text(encoding="utf-8").strip()
            if args.note_file else (args.note or "")).strip()
    if not note:
        raise SystemExit("need --note or --note-file")
    story = (args.story_file.expanduser().read_text(encoding="utf-8").strip()
             if args.story_file else args.story)

    edl = json.loads(args.edl.expanduser().read_text(encoding="utf-8"))
    clips = load_clips(args.sidecars.expanduser())
    if not clips:
        raise SystemExit(f"no sidecars in {args.sidecars}")

    before = edl["segments"]
    story = story or edl.get("story", "")
    # Echoed in full and word-counted: a note truncated by a shell quoting layer is
    # otherwise invisible until the answer comes back wrong.
    print(f"asking about {len(before)} segments over {len(clips)} clips "
          f"({sum(s['out'] - s['in'] for s in before):.1f}s)")
    print(f"note  ({len(note.split())} words): {note}")
    print(f"story ({len(story.split())} words): {story[:80]}{'...' if story else ''}")

    prompt_chars = len(revise.build_prompt(before, clips, story, note,
                                           (args.target[0], args.target[1])))
    print(f"\ncalling the model (~{prompt_chars // 4} input tokens). "
          f"This is one call and typically takes 30-90s — no output until it "
          f"returns.", flush=True)

    t0 = time.time()
    try:
        plan = revise.propose(before, clips, story, note,
                              target=(args.target[0], args.target[1]))
    except inference.InferenceError as exc:
        print(f"\nfailed after {time.time() - t0:.0f}s: {exc}", file=sys.stderr)
        return 1

    after = plan["segments"]
    total_before = sum(s["out"] - s["in"] for s in before)
    total_after = sum(s["out"] - s["in"] for s in after)

    print(f"--- proposal: {len(after)} segments, {total_after:.1f}s "
          f"(was {len(before)}, {total_before:.1f}s) ---")
    for i, s in enumerate(after, 1):
        print(f"  {i:2d}. {s['clip']:<14} {s['in']:6.2f}-{s['out']:6.2f} "
              f"({s['out'] - s['in']:5.1f}s)  {s.get('why', '')[:64]}")
    print(f"\nnotes: {plan.get('notes', '')}")

    u = plan.get("usage", {})
    print(f"\n{u.get('model')} on {u.get('backend')}: "
          f"{u.get('input_tokens')}→{u.get('output_tokens')} tokens, "
          f"${u.get('projected_usd', 0):.4f} projected, {u.get('latency_ms')}ms")

    if args.out:
        out_edl = dict(edl)
        out_edl["segments"] = after
        out_edl["story"] = story
        out_edl["revision_note"] = note
        out_edl["revision_rationale"] = plan.get("notes", "")
        args.out.expanduser().parent.mkdir(parents=True, exist_ok=True)
        args.out.expanduser().write_text(json.dumps(out_edl, indent=1),
                                         encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
