# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow>=10"]
# ///
"""Audit orientation across a bin: one representative frame per clip, on one sheet.

Orientation cannot be read off the container — B1 carries `rotation=-90` that renders
sideways on one clip and nothing at all on another, and some footage is simply shot the
wrong way up with no metadata to say so. So the only reliable procedure is to look at a
frame from every clip and decide.

Frames are extracted with `-noautorotate` (raw stored orientation, no container rotation
applied) so what you see is what the decoder actually holds. Each cell is labelled with the
clip name and its rotation side-data, and the emitted JSON is a template for the per-clip
`orient_override` decisions that ingest will consume.

Usage:
    uv run orient_audit.py BIN_DIR -o OUTDIR
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".mts", ".webm"}
LABEL_H = 30
PAD = 3
SAMPLE_FRACTION = 0.15   # sample 15% in; first frames are often black or lens-covered


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def probe(video: Path) -> tuple[float, float, int, int]:
    """Return (duration_s, rotation_deg, width, height) from the stored stream."""
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "format=duration",
             "-show_entries", "stream=width,height",
             "-show_entries", "stream_side_data=rotation",
             "-of", "json", str(video)])
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200])
    data = json.loads(r.stdout or "{}")
    duration = float(data.get("format", {}).get("duration", 0.0) or 0.0)
    stream = (data.get("streams") or [{}])[0]
    width, height = int(stream.get("width", 0)), int(stream.get("height", 0))
    rotation = 0.0
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = float(sd["rotation"])
            break
    return duration, rotation, width, height


def grab_frame(video: Path, at: float, out: Path, width: int) -> bool:
    # -noautorotate before -i: show the stored pixels, not the container's opinion of them
    r = run(["ffmpeg", "-v", "error", "-y", "-noautorotate", "-ss", f"{at:.2f}",
             "-i", str(video), "-frames:v", "1", "-vf", f"scale={width}:-2",
             "-q:v", "3", str(out)])
    return r.returncode == 0 and out.exists()


def load_font(size: int):
    for c in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(c).exists():
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()


def main() -> int:
    ap = argparse.ArgumentParser(description="One frame per clip, for orientation review.")
    ap.add_argument("bin_dir", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--width", type=int, default=320)
    args = ap.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            print(f"error: {tool} not on PATH", file=sys.stderr)
            return 2

    videos = sorted(p for p in args.bin_dir.iterdir()
                    if p.suffix.lower() in VIDEO_SUFFIXES)
    if not videos:
        print(f"error: no videos in {args.bin_dir}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    font, small = load_font(13), load_font(11)
    entries, tiles = [], []

    with tempfile.TemporaryDirectory() as td:
        for i, video in enumerate(videos, start=1):
            try:
                duration, rotation, w, h = probe(video)
            except RuntimeError as exc:
                print(f"skip {video.name}: {exc}", file=sys.stderr)
                continue
            frame = Path(td) / f"{i:03d}.jpg"
            if not grab_frame(video, duration * SAMPLE_FRACTION, frame, args.width):
                print(f"skip {video.name}: frame grab failed", file=sys.stderr)
                continue
            tiles.append((video.name, rotation, Image.open(frame).copy()))
            entries.append({
                "clip": video.name,
                "duration_s": round(duration, 2),
                "stored_size": [w, h],
                "rotation_metadata": rotation,
                # Filled in after looking at the sheet. 'auto' honours the container,
                # 'none' ignores it, cw/ccw/180 rotate the stored pixels explicitly.
                "orient_override": None,
            })

        if not tiles:
            print("error: no frames extracted", file=sys.stderr)
            return 1

        tw, th = tiles[0][2].size
        cell_w, cell_h = tw + 2 * PAD, th + LABEL_H + 2 * PAD
        cols = min(args.cols, len(tiles))
        rows = (len(tiles) + cols - 1) // cols
        canvas = Image.new("RGB", (cols * cell_w, rows * cell_h), (24, 24, 27))
        draw = ImageDraw.Draw(canvas)

        for k, (name, rotation, img) in enumerate(tiles):
            r, c = divmod(k, cols)
            x, y = c * cell_w + PAD, r * cell_h + PAD
            if img.size != (tw, th):
                img = img.resize((tw, th))
            canvas.paste(img, (x, y))
            draw.text((x + 3, y + th + 2), f"{k + 1:02d}  {name}", fill=(240, 240, 240), font=font)
            rot_txt = f"rotation={rotation:g}" if rotation else "no rotation metadata"
            draw.text((x + 3, y + th + 16), rot_txt,
                      fill=(255, 170, 90) if rotation else (150, 150, 150), font=small)

        sheet = args.out / "orientation_audit.jpg"
        canvas.save(sheet, quality=88)

    manifest = args.out / "orientation.json"
    manifest.write_text(json.dumps({"bin": args.bin_dir.name, "clips": entries}, indent=2),
                        encoding="utf-8")
    rotated = sum(1 for e in entries if e["rotation_metadata"])
    print(f"{len(entries)} clips, {rotated} carrying rotation metadata")
    print(f"sheet:    {sheet}")
    print(f"manifest: {manifest}  (orient_override is null until reviewed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
