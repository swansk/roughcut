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

**Two densities, one tool.** At 4s a jump is a coin toss on phase — the sheet catches
the approach and the landing and calls the stretch "skiing". Sampling the whole bin at
1s would cost four times as much for frames that are almost all snow, so `--windows`
takes a short list of places worth looking at closely (from `roughcut.events`' free
motion scan, or by hand) and reads one sheet per window at whatever interval is asked.
The fine read is written to `<stem>.fine.json`, beside the coarse `<stem>.visual.json`
and never over it: they are different observations of the same clip, both paid for.

Usage:
    uv run visual_pass.py VIDEO_OR_DIR -o OUTDIR [--interval 4] [--only CLIP_01]
    uv run visual_pass.py VIDEO_OR_DIR -o OUTDIR --interval 1 --width 480 --cols 3 \
        --windows 'CLIP_07:104,168,268;CLIP_11:252-260'
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

# The close look. Same schema, same conservatism, different question: the coarse pass
# has already said "something is happening around here" and the only thing worth
# reporting now is *what*, with a start and end tight enough to cut on.
FINE_PROMPT = """This contact sheet is {n} frames from {clip}, sampled every \
{interval:g}s, covering {start:.0f}s to {end:.0f}s of the clip — a close look at one \
short window that a motion and audio scan flagged as unusual. Frames read left to \
right, top to bottom, and each is labelled with its timestamp.

Something changes sharply somewhere in this window. Say what it is, as moments with
start and end times in **clip seconds**, tight to what you can actually see: a jump
that leaves the ground at 105 and lands at 107 is `105-107`, not `104-112`.

The scan cannot tell an event from an artefact, so the useful answers include the
boring ones:

1. **An event** — a jump, a fall, a crash, someone going down, gear coming off. Give
   it the tightest start and end the frames support, and say in `what` which frame
   shows the peak of it (the highest point, the impact).
2. **A camera artefact** — a whip pan, a lens wipe, a glove, a whiteout, the camera
   being picked up or put down. Mark these `junk` and list the stretch as unusable.
   A window that turns out to be nothing is a useful answer, not a failed one.
3. **Nothing in particular** — ordinary riding, standing around, scenery. Say that
   plainly with `notable: false` rather than promoting it.

Do not report a moment outside {start:.0f}-{end:.0f}s; you cannot see outside it."""


def build_sheets(video: Path, out_dir: Path, interval: float, cols: int, rows: int,
                 orient: str, width: int = 320,
                 window: tuple[float, float] | None = None) -> list[dict]:
    """Delegate to contact_sheet.py rather than reimplementing it — it already handles
    orientation, labels and the JSON index, and the sheets stay human-viewable."""
    cmd = ["uv", "run", "--quiet", str(HERE / "contact_sheet.py"), str(video),
           "-o", str(out_dir), "--interval", str(interval), "--cols", str(cols),
           "--rows", str(rows), "--width", str(width), "--orient", orient]
    if window is not None:
        cmd += ["--start", f"{window[0]:.3f}", "--end", f"{window[1]:.3f}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"contact sheet failed: {r.stderr[-400:]}")
    index = json.loads((out_dir / "index.json").read_text(encoding="utf-8"))
    clip = next(c for c in index["clips"] if c.get("source") == video.name)
    return [{"path": out_dir / s["path"], "cells": s["cells"]}
            for s in clip["sheets"]]


def read_sheet(sheet: Path, clip: str, cells: list[dict], interval: float,
               role: str, template: str = PROMPT) -> dict:
    times = [float(c["t"]) for c in cells]
    frames = cells
    prompt = template.format(n=len(frames), clip=clip, interval=interval,
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
            orient: str, role: str, width: int = 320,
            windows: list[tuple[float, float]] | None = None) -> dict:
    """Read a clip — whole, or window by window.

    One sheet is one call either way; the only difference is what the frames cover and
    which question is asked of them. Windows get a subdirectory each because
    contact_sheet.py names its output after the clip, so two windows of one clip would
    otherwise write the same filename.
    """
    template = FINE_PROMPT if windows else PROMPT
    with tempfile.TemporaryDirectory(prefix="visual-") as tmp:
        sheets: list[dict] = []
        if windows:
            for i, w in enumerate(windows):
                for s in build_sheets(video, Path(tmp) / f"w{i:02d}", interval, cols,
                                      rows, orient, width, w):
                    sheets.append({**s, "window": [round(w[0], 2), round(w[1], 2)]})
        else:
            sheets = build_sheets(video, Path(tmp), interval, cols, rows, orient, width)
        print(f"{video.name}: {len(sheets)} sheet(s)", flush=True)
        moments, unusable, summaries, failed, spend = [], [], [], [], 0.0
        read: list[list[float]] = []
        for s in sheets:
            try:
                got = read_sheet(s["path"], video.name, s["cells"], interval, role,
                                 template)
            except inference.InferenceError as exc:
                # One stalled call must not throw away the sheets that already
                # succeeded, nor the clips after this one. A batch that costs money
                # per unit has to bank what it has: the first run of this tool died on
                # a 300s timeout and discarded a sheet that had already been paid for.
                failed.append({"sheet": s["path"].name, "error": str(exc)[:200]})
                print(f"  {s['path'].name}: FAILED — {str(exc)[:120]}", flush=True)
                continue
            # Which windows were actually paid for. A second stage can then add
            # windows without re-buying these, and the ranker can tell "looked at and
            # found nothing" from "never looked at" — they mean opposite things.
            if s.get("window"):
                read.append(s["window"])
            moments += got["moments"]
            unusable += got["unusable"]
            summaries.append(got["summary"])
            spend += got["usage"]["projected_usd"]
            print(f"  {s['path'].name}: {len(got['moments'])} moments "
                  f"(${got['usage']['projected_usd']:.4f})", flush=True)
    moments.sort(key=lambda m: m["start"])
    out = {"clip": video.name, "moments": moments, "unusable": unusable,
           "summary": " ".join(summaries)[:1200], "projected_usd": round(spend, 4),
           "failed_sheets": failed,
           "sheets_read": len(sheets) - len(failed), "sheets_total": len(sheets),
           "params": {"interval_s": interval, "cols": cols, "rows": rows,
                      "width": width, "role": role,
                      "provisional": "RQ-1/RQ-7 unmeasured"}}
    if windows:
        out["mode"] = "fine"
        out["windows_read"] = read
    return out


def merge_fine(previous: dict, fresh: dict) -> dict:
    """Fold a new batch of windows into a fine sidecar already on disk.

    The fine pass is incremental by nature — a second stage adds three more windows to
    a clip that has had five read — and every window on disk was paid for. Merging
    rather than overwriting is the coarse pass's cache rule, one level down.
    """
    if not previous:
        return fresh
    moments = list(previous.get("moments", [])) + list(fresh.get("moments", []))
    moments.sort(key=lambda m: m["start"])
    return {**fresh,
            "moments": moments,
            "unusable": previous.get("unusable", []) + fresh.get("unusable", []),
            "summary": " ".join(x for x in (previous.get("summary", ""),
                                            fresh.get("summary", "")) if x)[:1200],
            "projected_usd": round(previous.get("projected_usd", 0.0)
                                   + fresh.get("projected_usd", 0.0), 4),
            "failed_sheets": (previous.get("failed_sheets", [])
                              + fresh.get("failed_sheets", [])),
            "sheets_read": previous.get("sheets_read", 0) + fresh["sheets_read"],
            "sheets_total": previous.get("sheets_total", 0) + fresh["sheets_total"],
            "windows_read": (previous.get("windows_read", [])
                             + fresh.get("windows_read", []))}


def parse_windows(spec: str, around: float) -> dict[str, list[tuple[float, float]]]:
    """`CLIP_07:104,168-196;CLIP_11:252`, or a JSON file holding the same thing.

    A bare number is a *centre* and becomes ±`around`; `a-b` is a span taken as given.
    Both forms exist because both callers do: the motion scan produces spans, and a
    human reading a sidecar has a single timestamp in their hand.
    """
    text = spec
    path = Path(spec).expanduser()
    if path.exists():
        text = path.read_text(encoding="utf-8")

    def span(item) -> tuple[float, float]:
        if isinstance(item, dict):
            return float(item["start"]), float(item["end"])
        if isinstance(item, (list, tuple)):
            return float(item[0]), float(item[1])
        return max(0.0, float(item) - around), float(item) + around

    out: dict[str, list[tuple[float, float]]] = {}
    if text.lstrip().startswith("{"):
        for stem, items in json.loads(text).items():
            out[stem.upper()] = [span(it) for it in items]
        return out

    for chunk in text.split(";"):
        stem, _, times = chunk.strip().partition(":")
        spans = []
        for item in times.split(","):
            item = item.strip()
            if not item:
                continue
            a, sep, b = item.partition("-")
            spans.append((float(a), float(b)) if sep else span(float(item)))
        if spans:
            out[stem.strip().upper()] = spans
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Prototype visual pass over contact sheets.")
    ap.add_argument("input", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--interval", type=float, default=4.0)
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--rows", type=int, default=5)
    ap.add_argument("--width", type=int, default=320, help="thumbnail width in px")
    ap.add_argument("--orient", choices=("auto", "none"), default="auto")
    ap.add_argument("--only", default="", help="comma-separated stems to include")
    ap.add_argument("--windows", default="",
                    help="read only these windows, one sheet each, and write "
                         "<stem>.fine.json instead of <stem>.visual.json. Either "
                         "'CLIP_07:104,168-196;CLIP_11:252' or a JSON file "
                         "{'CLIP_07': [[100,108], 168]}. A bare number is a centre")
    ap.add_argument("--around", type=float, default=4.0,
                    help="half-width in seconds for a window given as a centre")
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
    windows = parse_windows(args.windows, args.around) if args.windows else {}
    if windows:
        videos = [v for v in videos if v.stem.upper() in windows]
    if not videos:
        print("error: nothing to analyse", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    total = 0.0
    for video in videos:
        suffix = "fine" if windows else "visual"
        dest = args.out / f"{video.stem}.{suffix}.json"
        previous = (json.loads(dest.read_text(encoding="utf-8"))
                    if dest.exists() and windows and not args.force else {})
        want = windows.get(video.stem.upper(), []) if windows else None
        if want is not None and not args.force:
            # Cached per *window*, not per clip: the second stage adds windows to a
            # clip that already has some, and each one on disk was paid for.
            already = {tuple(w) for w in previous.get("windows_read", [])}
            want = [w for w in want
                    if not any(abs(w[0] - a) < 0.5 and abs(w[1] - b) < 0.5
                               for a, b in already)]
            if not want:
                print(f"{video.name}: cached ({len(already)} window(s))", flush=True)
                continue
        elif want is None and dest.exists() and not args.force:
            # Cached like the audio pass. These calls cost real money, so re-running
            # the tool over a bin must not silently re-buy work already on disk.
            print(f"{video.name}: cached", flush=True)
            continue
        try:
            result = analyse(video, args.out, args.interval, args.cols, args.rows,
                             args.orient, args.role, args.width, want)
        except (RuntimeError, OSError) as exc:
            print(f"{video.name}: SKIPPED — {exc}", file=sys.stderr, flush=True)
            continue
        spent = result["projected_usd"]          # this run's, not the file's running sum
        if previous:
            result = merge_fine(previous, result)
        dest.write_text(json.dumps(result, indent=1), encoding="utf-8")
        total += spent
        notable = [m for m in result["moments"] if m["notable"]]
        print(f"{video.name}: {len(result['moments'])} moments, {len(notable)} notable, "
              f"{len(result['unusable'])} unusable, "
              f"{result['sheets_read']}/{result['sheets_total']} sheets  "
              f"(${result['projected_usd']:.4f})", flush=True)
    print(f"\ntotal projected ${total:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
