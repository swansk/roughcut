# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2", "torch", "transformers>=4.40"]
# ///
"""Tier B audio event tagging — laughter, whoops, cheering (AUDIO.md's known gap).

R8 closed with this as the largest remaining audio gap, and Karl's review of the first
cuts sharpened why: the vocal reactions are what make the ski clips work, and ASR only
catches them by luck (it transcribed one "Whoo" as a word and dropped the rest, because
a whoop is not speech).

Model is AST fine-tuned on AudioSet (527 classes), run over the whole bin. AudioSet is
exactly the right vocabulary here — Laughter, Cheering, Whoop, Yell, Shout are all
first-class labels in it.

**Temporal resolution, honestly stated.** AST's positional embeddings are fixed to a
10.24s input, so a single forward pass says "laughter somewhere in these ten seconds",
which is far too coarse to cut on. Windows are therefore hopped by `--hop` (1s default)
and each second's score is the mean over every window containing it. An event present
for one second raises only the windows that contain it, so the averaged track peaks over
the event rather than smearing uniformly — localisation lands around +/-2s, good enough
to flag a candidate that the transcript and Tier A tracks then refine. It is not good
enough to place a cut point directly, and nothing here pretends otherwise.

Usage:
    uv run audio_events.py DIR_OR_CLIP --sidecars ~/work/audio [--report] [--hop 1.0]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

SR = 16000
WIN_S = 10.24               # AST's native input length; not a free parameter
MODEL = "MIT/ast-finetuned-audioset-10-10-0.4593"
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".mts", ".webm"}

# AudioSet labels worth acting on, grouped by what they mean for the edit.
# `reaction` drives candidates; `quality` describes whether audio is usable.
CLASS_GROUPS = {
    "reaction": [
        "Laughter", "Giggle", "Snicker", "Belly laugh", "Chuckle, chortle",
        "Cheering", "Applause", "Whoop", "Yell", "Shout", "Screaming",
        "Children shouting", "Whistling", "Crowd",
    ],
    "quality": ["Wind", "Wind noise (microphone)", "Rustling leaves", "Silence"],
}
# Scored separately: useful context, but not a reason to cut.
CONTEXT = ["Speech", "Conversation", "Music", "Vehicle", "Skiing"]


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, check=False)


def decode(video: Path) -> np.ndarray:
    """Mono 16 kHz float32 — same front end as audio_analyze.py."""
    r = run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(video),
             "-map", "0:a:0", "-ac", "1", "-ar", str(SR), "-f", "s16le", "pipe:1"])
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"decode failed: {r.stderr.decode(errors='replace')[-200:]}")
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0


class Tagger:
    def __init__(self, device: str):
        import torch
        from transformers import AutoFeatureExtractor, AutoModelForAudioClassification
        self.torch = torch
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        self.fx = AutoFeatureExtractor.from_pretrained(MODEL)
        self.model = AutoModelForAudioClassification.from_pretrained(MODEL).to(device).eval()
        self.labels = self.model.config.id2label
        print(f"events: AST on {device}, {len(self.labels)} classes")

    def window_scores(self, x: np.ndarray, hop_s: float, batch: int = 16) -> np.ndarray:
        """(n_windows, n_classes) sigmoid scores over hopped windows."""
        torch = self.torch
        win = int(WIN_S * SR)
        hop = int(hop_s * SR)
        if len(x) < win:                       # pad short clips to one full window
            x = np.pad(x, (0, win - len(x)))
        starts = list(range(0, max(1, len(x) - win + 1), hop))
        out = []
        for i in range(0, len(starts), batch):
            chunk = [x[s:s + win] for s in starts[i:i + batch]]
            feats = self.fx(chunk, sampling_rate=SR, return_tensors="pt")
            with torch.no_grad():
                logits = self.model(feats.input_values.to(self.device)).logits
            out.append(torch.sigmoid(logits).cpu().numpy())
        return np.concatenate(out, axis=0), starts


def per_second_track(scores: np.ndarray, starts: list[int], duration_s: float,
                     col: int) -> np.ndarray:
    """Mean of every window covering each second — see the docstring on resolution."""
    n = max(1, int(np.ceil(duration_s)))
    total = np.zeros(n)
    count = np.zeros(n)
    win_s = int(WIN_S)
    for k, s in enumerate(starts):
        a = s // SR
        b = min(n, a + win_s)
        total[a:b] += scores[k, col]
        count[a:b] += 1
    return total / np.maximum(count, 1)


def peaks(track: np.ndarray, thr: float, min_gap: int = 3) -> list[int]:
    idx = np.flatnonzero(track > thr)
    return [int(p) for i, p in enumerate(idx) if i == 0 or p - idx[i - 1] >= min_gap]


def main() -> int:
    ap = argparse.ArgumentParser(description="AudioSet event tagging over a bin.")
    ap.add_argument("input", type=Path)
    ap.add_argument("--sidecars", type=Path, required=True)
    ap.add_argument("--hop", type=float, default=1.0)
    ap.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    ap.add_argument("--threshold", type=float, default=0.15,
                    help="per-class score to call an event")
    ap.add_argument("--report", action="store_true",
                    help="rank detections and print; do not touch the sidecars")
    ap.add_argument("--skip", default="")
    args = ap.parse_args()

    if args.input.is_dir():
        videos = sorted(p for p in args.input.iterdir()
                        if p.suffix.lower() in VIDEO_SUFFIXES)
    else:
        videos = [args.input]
    skip = {s.strip().upper() for s in args.skip.split(",") if s.strip()}
    videos = [v for v in videos if v.stem.upper() not in skip]

    tagger = Tagger(args.device)
    want: dict[str, int] = {}
    for i, name in tagger.labels.items():
        if any(name in g for g in CLASS_GROUPS.values()) or name in CONTEXT:
            want[name] = i
    missing = [n for g in CLASS_GROUPS.values() for n in g if n not in want]
    if missing:
        print(f"note: labels absent from this model: {missing}", file=sys.stderr)

    all_events: list[dict] = []
    for video in videos:
        side = args.sidecars / f"{video.stem}.audio.json"
        if not side.exists():
            print(f"skip {video.name}: no sidecar", file=sys.stderr)
            continue
        data = json.loads(side.read_text(encoding="utf-8"))
        x = decode(video)
        duration = len(x) / SR
        scores, starts = tagger.window_scores(x, args.hop)

        events: list[dict] = []
        for name, col in want.items():
            group = next((g for g, names in CLASS_GROUPS.items() if name in names), None)
            if group is None:
                continue
            track = per_second_track(scores, starts, duration, col)
            for t in peaks(track, args.threshold):
                events.append({"start": float(t), "end": float(min(duration, t + 2)),
                               "label": name, "group": group,
                               "conf": round(float(track[t]), 3)})
        events.sort(key=lambda e: -e["conf"])
        for e in events:
            all_events.append({"clip": video.name, **e})

        top = [e for e in events if e["group"] == "reaction"][:3]
        print(f"{video.name}: {len(events):3d} events  " +
              "  ".join(f"{e['label']}@{e['start']:.0f}s={e['conf']:.2f}" for e in top))

        if not args.report:
            data["events"] = events
            # Reactions become candidates; quality events never do.
            reaction = [e for e in events if e["group"] == "reaction"]
            cands = data.get("candidates", [])
            for e in reaction:
                cands.append({
                    "t": round(e["start"], 2), "end": round(e["end"], 2),
                    "why": f"audio event: {e['label']} (conf {e['conf']:.2f}, +/-2s)",
                    "score": round(min(0.95, 0.45 + e["conf"]), 3),
                })
            cands.sort(key=lambda c: -c["score"])
            data["candidates"] = cands
            side.write_text(json.dumps(data, indent=1), encoding="utf-8")

    for group in ("reaction", "quality"):
        print(f"\n=== top {group} detections across the bin ===")
        rows = sorted((e for e in all_events if e["group"] == group),
                      key=lambda e: -e["conf"])
        if not rows:
            print("  (none)")
        for e in rows[:20]:
            print(f"  {e['conf']:.3f}  {e['clip']:<14} {e['start']:6.0f}s  {e['label']}")
    if args.report:
        print("\n(--report: sidecars untouched)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
