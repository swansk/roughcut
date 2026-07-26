# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Look at the footage — a prototype visual pass over contact sheets.

Every cut this project has made was chosen from words. Karl, after the first
Killington cut: *"the analysis missed some critical moments that would have required
video analysis (and some sound) — like me falling into a river."* The evidence is
sharper than that: the cut opens on him **talking about** falling in, three minutes
after it happened, from the same clip that contains the fall. A transcript records
people narrating events, never the events.

So: sample the clip onto contact sheets, hand each sheet to a model, and ask what
happens in it. Output is one `<stem>.visual.json` per clip, in the same spirit as the
audio sidecars — a description of *when* something visible happens, for the pass that
chooses shots.

Provisional by construction. Sheet density, thumbnail resolution and model tier are
RQ-1/RQ-7 in the SPEC and unmeasured; the values here are a starting point, not an
answer, and anything built on them is `done*` until those studies run.

Usage:
    uv run visual_pass.py VIDEO_OR_DIR -o OUTDIR [--interval 4] [--only CLIP_01]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import config, inference   # noqa: E402  (after sys.path)

HERE = Path(__file__).resolve().parent
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".mts", ".webm"}

SYSTEM = (
    "You are a video assistant looking at a contact sheet: frames sampled from one "
    "clip at a fixed interval, in reading order, each labelled with its timestamp in "
    "the clip. You report what is visibly happening and when. You are precise about "
    "time and conservative about certainty: you say what you can see, and you say so "
    "when a frame is too dark, too blurred or too close to read."
)

SCHEMA = {
    "moments": [{"start": 12.0, "end": 20.0, "what": "one sentence on what happens",
                 "kind": "action | fall | crash | jump | reaction | faces | scenery | "
                         "junk", "notable": True}],
    "unusable": [{"start": 0.0, "end": 4.0, "why": "black / lens covered / unreadable"}],
    "summary": "two sentences on what this clip is, as footage",
}

PROMPT = """This contact sheet is {n} frames from {clip}, sampled every {interval:g}s, \
covering {start:.0f}s to {end:.0f}s of the clip. Frames read left to right, top to \
bottom, and each is labelled with its timestamp.

Report what happens, as moments with start and end times in **clip seconds**.

What matters most, in order:
1. **Events** — someone falling, crashing, jumping, going into water, losing gear.
   These are the moments a transcript cannot see: people narrate them later, if at
   all, and the words never land at the time the thing happened.
2. **People and reactions** — faces, someone filming someone else, a group together.
3. **Sustained action** — a continuous run or ride, worth keeping whole.

Also flag anything **unusable**: black frames, a lens covered by a glove, a shot so
blurred or close that nothing reads.

Be conservative. If consecutive frames only imply an event rather than show it, say so
in `what` rather than asserting it. Do not invent a timestamp you cannot point at."""


def build_sheets(video: Path, out_dir: Path, interval: float, cols: int, rows: int,
                 orient: str) -> list[dict]:
    """Delegate to contact_sheet.py rather than reimplementing it — it already handles
    orientation, labels and the JSON index, and the sheets stay human-viewable."""
    cmd = ["uv", "run", "--quiet", str(HERE / "contact_sheet.py"), str(video),
           "-o", str(out_dir), "--interval", str(interval), "--cols", str(cols),
           "--rows", str(rows), "--orient", orient]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"contact sheet failed: {r.stderr[-400:]}")
    index = json.loads((out_dir / "index.json").read_text(encoding="utf-8"))
    clip = next(c for c in index["clips"] if c.get("source") == video.name)
    return [{"path": out_dir / s["path"], "cells": s["cells"]}
            for s in clip["sheets"]]


def read_sheet(sheet: Path, clip: str, cells: list[dict], interval: float,
               role: str) -> dict:
    times = [float(c["t"]) for c in cells]
    frames = cells
    prompt = PROMPT.format(n=len(frames), clip=clip, interval=interval,
                           start=min(times), end=max(times))
    result = inference.complete(
        prompt, role=role, images=[sheet], schema=SCHEMA, system=SYSTEM,
        validate=lambda p: validate(p, min(times), max(times) + interval))
    out = result.content
    out["usage"] = {"input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "projected_usd": result.projected_usd, "model": result.model,
                    "latency_ms": result.latency_ms}
    return out


def validate(payload, lo: float, hi: float) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("expected an object")
    moments = payload.get("moments")
    if not isinstance(moments, list):
        raise ValueError("'moments' must be a list")
    clean = []
    for i, m in enumerate(moments):
        try:
            start, end = float(m["start"]), float(m["end"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"moment {i} has non-numeric start/end") from None
        # Timestamps outside the sheet are the failure that matters: a moment the
        # model placed where it could not have seen anything is worse than no moment.
        if not (lo - 2.0 <= start <= end <= hi + 2.0):
            raise ValueError(f"moment {i} {start}-{end} outside this sheet ({lo}-{hi})")
        clean.append({"start": round(start, 1), "end": round(end, 1),
                      "what": str(m.get("what", ""))[:300],
                      "kind": str(m.get("kind", ""))[:24],
                      "notable": bool(m.get("notable", False))})
    unusable = [{"start": float(u["start"]), "end": float(u["end"]),
                 "why": str(u.get("why", ""))[:120]}
                for u in payload.get("unusable", []) if isinstance(u, dict)]
    return {"moments": clean, "unusable": unusable,
            "summary": str(payload.get("summary", ""))[:600]}


def analyse(video: Path, out_dir: Path, interval: float, cols: int, rows: int,
            orient: str, role: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="visual-") as tmp:
        sheets = build_sheets(video, Path(tmp), interval, cols, rows, orient)
        print(f"{video.name}: {len(sheets)} sheet(s)", flush=True)
        moments, unusable, summaries, failed, spend = [], [], [], [], 0.0
        for s in sheets:
            try:
                got = read_sheet(s["path"], video.name, s["cells"], interval, role)
            except inference.InferenceError as exc:
                # One stalled call must not throw away the sheets that already
                # succeeded, nor the clips after this one. A batch that costs money
                # per unit has to bank what it has: the first run of this tool died on
                # a 300s timeout and discarded a sheet that had already been paid for.
                failed.append({"sheet": s["path"].name, "error": str(exc)[:200]})
                print(f"  {s['path'].name}: FAILED — {str(exc)[:120]}", flush=True)
                continue
            moments += got["moments"]
            unusable += got["unusable"]
            summaries.append(got["summary"])
            spend += got["usage"]["projected_usd"]
            print(f"  {s['path'].name}: {len(got['moments'])} moments "
                  f"(${got['usage']['projected_usd']:.4f})", flush=True)
    moments.sort(key=lambda m: m["start"])
    return {"clip": video.name, "moments": moments, "unusable": unusable,
            "summary": " ".join(summaries)[:1200], "projected_usd": round(spend, 4),
            "failed_sheets": failed,
            "sheets_read": len(sheets) - len(failed), "sheets_total": len(sheets),
            "params": {"interval_s": interval, "cols": cols, "rows": rows,
                       "role": role, "provisional": "RQ-1/RQ-7 unmeasured"}}


def main() -> int:
    ap = argparse.ArgumentParser(description="Prototype visual pass over contact sheets.")
    ap.add_argument("input", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--interval", type=float, default=4.0)
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--rows", type=int, default=5)
    ap.add_argument("--orient", choices=("auto", "none"), default="auto")
    ap.add_argument("--only", default="", help="comma-separated stems to include")
    ap.add_argument("--force", action="store_true", help="re-read clips already done")
    ap.add_argument("--timeout", type=int, default=600,
                    help="per-call seconds. Reading a sheet is slower than a text "
                         "call — the CLI has to open a ~2000px image — and the 300s "
                         "default killed the first full run")
    ap.add_argument("--role", default=config.ROLE_ANALYSIS,
                    choices=(config.ROLE_ANALYSIS, config.ROLE_JUDGE,
                             config.ROLE_SKELETON))
    args = ap.parse_args()
    os.environ.setdefault("ROUGHCUT_CALL_TIMEOUT_S", str(args.timeout))

    if args.input.is_dir():
        videos = sorted(p for p in args.input.iterdir()
                        if p.suffix.lower() in VIDEO_SUFFIXES)
    else:
        videos = [args.input]
    only = {s.strip().upper() for s in args.only.split(",") if s.strip()}
    if only:
        videos = [v for v in videos if v.stem.upper() in only]
    if not videos:
        print("error: nothing to analyse", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    total = 0.0
    for video in videos:
        dest = args.out / f"{video.stem}.visual.json"
        if dest.exists() and not args.force:
            # Cached like the audio pass. These calls cost real money, so re-running
            # the tool over a bin must not silently re-buy work already on disk.
            print(f"{video.name}: cached", flush=True)
            continue
        try:
            result = analyse(video, args.out, args.interval, args.cols, args.rows,
                             args.orient, args.role)
        except (RuntimeError, OSError) as exc:
            print(f"{video.name}: SKIPPED — {exc}", file=sys.stderr, flush=True)
            continue
        dest.write_text(json.dumps(result, indent=1), encoding="utf-8")
        total += result["projected_usd"]
        notable = [m for m in result["moments"] if m["notable"]]
        print(f"{video.name}: {len(result['moments'])} moments, {len(notable)} notable, "
              f"{len(result['unusable'])} unusable, "
              f"{result['sheets_read']}/{result['sheets_total']} sheets  "
              f"(${result['projected_usd']:.4f})", flush=True)
    print(f"\ntotal projected ${total:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
