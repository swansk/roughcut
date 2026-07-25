# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow>=10"]
# ///
"""Build timestamped contact sheets from video.

Two consumers, one tool:
  * R7 policy sessions, which reason over coarse visual samples of a whole bin
  * human labeling, which is a fast visual pass rather than a scrubbing session

Frames are extracted with ffmpeg and composited with Pillow. Labels are drawn here
rather than with ffmpeg's drawtext because static ffmpeg builds routinely ship without
it; the JSON index is authoritative regardless, so a sheet whose labels are unreadable
is still machine-interpretable.

Usage:
    uv run contact_sheet.py VIDEO_OR_DIR -o OUTDIR [--interval 2] [--cols 6] [--rows 5]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".mts", ".webm"}
LABEL_H = 18
PAD = 2


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def probe_duration(video: Path) -> float:
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video)])
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"ffprobe failed for {video}: {r.stderr.strip()[:200]}")
    return float(r.stdout.strip())


def probe_rotation(video: Path) -> float:
    """Rotation side-data in degrees, or 0.0 if absent.

    Recorded rather than trusted: GoPro clips in the benchmark bins carry
    rotation=-90 whose application by ffmpeg yields sideways frames, while other
    clips in the same bin carry none. Orientation is verified by looking, not by
    believing the container.
    """
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream_side_data=rotation", "-of", "csv=p=0", str(video)])
    text = r.stdout.strip().splitlines()
    for line in text:
        line = line.strip().rstrip(",")
        try:
            return float(line)
        except ValueError:
            continue
    return 0.0


def extract_frames(video: Path, workdir: Path, interval: float, width: int,
                   orient: str) -> list[Path]:
    pattern = str(workdir / "f_%05d.jpg")
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if orient == "none":
        cmd.append("-noautorotate")     # must precede -i to affect decoding
    cmd += ["-i", str(video), "-vf", f"fps=1/{interval},scale={width}:-2",
            "-q:v", "3", pattern]
    r = run(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed for {video}: {r.stderr.strip()[:300]}")
    return sorted(workdir.glob("f_*.jpg"))


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        p = Path(candidate)
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def hms(t: float) -> str:
    m, s = divmod(int(t), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


@dataclass
class Cell:
    index: int
    t: float
    row: int
    col: int


@dataclass
class Sheet:
    path: str
    grid: list[int]
    cells: list[dict]


def build_sheets(video: Path, out_dir: Path, interval: float, cols: int, rows: int,
                 width: int, label: str | None, orient: str) -> dict:
    duration = probe_duration(video)
    rotation = probe_rotation(video)
    name = label or video.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        frames = extract_frames(video, Path(td), interval, width, orient)
        if not frames:
            raise RuntimeError(f"no frames extracted from {video}")

        thumb_w, thumb_h = Image.open(frames[0]).size
        cell_w, cell_h = thumb_w + 2 * PAD, thumb_h + LABEL_H + 2 * PAD
        font = load_font(12)
        per_sheet = cols * rows
        sheets: list[Sheet] = []

        for s_idx in range(0, len(frames), per_sheet):
            chunk = frames[s_idx:s_idx + per_sheet]
            n_rows = (len(chunk) + cols - 1) // cols
            canvas = Image.new("RGB", (cols * cell_w, n_rows * cell_h), (24, 24, 27))
            draw = ImageDraw.Draw(canvas)
            cells: list[Cell] = []

            for k, frame_path in enumerate(chunk):
                gi = s_idx + k                      # global frame index
                t = gi * interval                   # sample timestamp
                r, c = divmod(k, cols)
                x, y = c * cell_w + PAD, r * cell_h + PAD
                canvas.paste(Image.open(frame_path), (x, y))
                draw.text((x + 3, y + thumb_h + 3), f"{gi:03d}  {hms(t)}",
                          fill=(240, 240, 240), font=font)
                cells.append(Cell(index=gi, t=round(t, 2), row=r, col=c))

            sheet_path = out_dir / f"{name}_sheet_{len(sheets) + 1:03d}.jpg"
            canvas.save(sheet_path, quality=88)
            sheets.append(Sheet(path=sheet_path.name, grid=[cols, n_rows],
                                cells=[asdict(x) for x in cells]))

    return {
        "source": video.name,
        "label": name,
        "duration_s": round(duration, 2),
        "interval_s": interval,
        "rotation_metadata": rotation,
        "orient_mode": orient,
        "thumb_size": [thumb_w, thumb_h],
        "n_samples": sum(len(s.cells) for s in sheets),
        "sheets": [asdict(s) for s in sheets],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Build timestamped contact sheets from video.")
    ap.add_argument("input", type=Path, help="video file or directory of videos")
    ap.add_argument("-o", "--out", type=Path, required=True, help="output directory")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between samples")
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--rows", type=int, default=5)
    ap.add_argument("--width", type=int, default=320, help="thumbnail width in px")
    ap.add_argument("--neutralize", action="store_true",
                    help="label sheets clip_NN instead of the filename (R7 integrity control)")
    ap.add_argument("--orient", choices=("auto", "none"), default="auto",
                    help="'auto' honours container rotation metadata; 'none' ignores it "
                         "(-noautorotate). GoPro bins here need 'none' — their rotation "
                         "side-data yields sideways frames. Always eyeball the first sheet.")
    args = ap.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            print(f"error: {tool} not found on PATH", file=sys.stderr)
            return 2

    if args.input.is_dir():
        videos = sorted(p for p in args.input.iterdir()
                        if p.suffix.lower() in VIDEO_SUFFIXES)
    else:
        videos = [args.input]
    if not videos:
        print(f"error: no video files found in {args.input}", file=sys.stderr)
        return 2

    index = {"interval_s": args.interval, "grid": [args.cols, args.rows], "clips": []}
    for i, video in enumerate(videos, start=1):
        label = f"clip_{i:02d}" if args.neutralize else None
        try:
            entry = build_sheets(video, args.out, args.interval, args.cols,
                                 args.rows, args.width, label, args.orient)
        except RuntimeError as exc:
            print(f"skip {video.name}: {exc}", file=sys.stderr)
            continue
        # Under --neutralize the real filename must not leak into the index the
        # session can read; it is written to a separate mapping file instead.
        if args.neutralize:
            entry.pop("source")
        index["clips"].append(entry)
        print(f"{entry['label']}: {entry['n_samples']} samples, "
              f"{len(entry['sheets'])} sheet(s)")

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")

    if args.neutralize:
        mapping = {f"clip_{i:02d}": v.name for i, v in enumerate(videos, start=1)}
        (args.out.parent / f"{args.out.name}_MAPPING.json").write_text(
            json.dumps(mapping, indent=2), encoding="utf-8")
        print(f"mapping written outside {args.out.name}/ — keep it out of any policy session")

    print(f"wrote {args.out / 'index.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
