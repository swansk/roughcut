# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Count the lines a cut clips: every shot boundary inside a sentence, every out-point
with no beat after the last word, every quick reply left outside the shot.

Karl's rule (2026-11-11): never cut off audio; let people finish their sentences and
wait for the reaction; run long rather than clip. This is the deterministic measure of
it, on any cut: an EDL (`segments`), a plan, or an ask record (`plan.segments`).

    uv run research/tools/speech_cuts.py CUT.json --audio WORK/audio/BIN [--json]

Sentences come from the ASR sidecars (`roughcut/sentences.py`: punctuation, utterance
ends, pauses of 1 s, speaker changes). Kinds:

  mid_in    the shot opens after a sentence began            (a clipped line)
  mid_out   the shot ends before a sentence ended            (a clipped line)
  no_beat   it ends < 0.3 s after the last word               (sounds clipped)
  reaction  a short reply starting < 1.5 s after the line is left out
  voice     (hint only, not counted) the voice-activity track is on for >= 0.3 s in
            the 1 s after the out-point with no word transcribed there — often a laugh
            or an untranscribed reply, sometimes wind or the camera mic

Two shots of the same clip that play one after the other and meet are one stretch, so
the boundary between them is not a cut.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import sentences as sm  # noqa: E402


def load_segments(path: Path) -> list[dict]:
    d = json.loads(path.read_text())
    if isinstance(d, dict) and isinstance(d.get("plan"), dict):
        d = d["plan"]
    segs = d.get("segments") if isinstance(d, dict) else d
    return [s for s in segs or [] if isinstance(s, dict) and "in" in s and "out" in s]


def load_clips(audio: Path, names: set[str]) -> tuple[dict, dict]:
    clips, tracks = {}, {}
    for n in names:
        f = audio / f"{Path(n).stem}.audio.json"
        if not f.exists():
            continue
        d = json.loads(f.read_text())
        clips[n] = {"clip": n, "duration": d.get("duration_s"), "transcript": d.get("transcript") or []}
        tracks[n] = (d.get("tracks") or {}).get("vad"), float(d.get("frame_hz") or 10)
    return clips, tracks


def voice_hints(segments: list[dict], clips: dict, tracks: dict) -> list[dict]:
    out = []
    for i, s in enumerate(segments):
        vad, hz = tracks.get(s["clip"], (None, 10.0))
        if not vad:
            continue
        nxt = segments[i + 1] if i + 1 < len(segments) else None
        if nxt and sm._runs_on(s, nxt):
            continue
        t0 = float(s["out"])
        words = sm._words(clips[s["clip"]]["transcript"])
        frames = [k for k in range(int(t0 * hz), min(len(vad), int((t0 + 1.0) * hz)))
                  if vad[k] and not any(w["t"] - 0.1 <= k / hz <= w["e"] + 0.1 for w in words)]
        if len(frames) / hz >= 0.3:
            out.append({"shot": i + 1, "clip": s["clip"], "out": round(t0, 2),
                        "voice_s": round(len(frames) / hz, 1)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cut", type=Path)
    ap.add_argument("--audio", type=Path, required=True, help="the bin's audio sidecar dir")
    ap.add_argument("--target", type=float, nargs=2, help="aim lo hi (s), for the length line")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    segs = load_segments(a.cut)
    clips, tracks = load_clips(a.audio, {s["clip"] for s in segs})
    m = sm.measure(segs, clips)
    m["voice_hints"] = voice_hints(segs, clips, tracks)
    m["length_s"] = round(sum((float(s["out"]) - float(s["in"])) / float(s.get("speed") or 1.0)
                              for s in segs), 1)
    if a.json:
        print(json.dumps(m, indent=1))
        return 0
    print(f"{a.cut.name}: {m['shots']} shots ({m['speech_shots']} with speech), "
          f"{m['length_s']}s"
          + (f" (aim {a.target[0]:.0f}-{a.target[1]:.0f}s)" if a.target else ""))
    print(f"  clipped lines (mid_in + mid_out): {m['clipped']}   by kind: {m['counts']}")
    for p in m["problems"]:
        extra = f"  reply: {p['reply']!r}" if p.get("reply") else ""
        print(f"  shot {p['shot']:2d} {p['clip']} {p['in']:.2f}-{p['out']:.2f} {p['kind']:8s} "
              f"at {p['at']:.2f}  line {p['sentence']}: {p['line'][:70]!r}{extra}")
    if m["voice_hints"]:
        print(f"  voice right after the out-point, untranscribed (hint, not counted): "
              f"{len(m['voice_hints'])} shots: "
              + ", ".join(f"shot {h['shot']} ({h['voice_s']}s)" for h in m["voice_hints"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
