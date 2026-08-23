# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Cut an EDL into a rendered video. Throwaway prototype tooling — no OTIO, no index.

Each segment is extracted and re-encoded to identical parameters, then concatenated
with the concat demuxer. Re-encoding every segment is wasteful but it is the only
way to get frame-accurate cuts across GOP boundaries, and a 2-minute rough cut is
cheap enough that the waste does not matter.

Two things here are not defaults and must not be "cleaned up":

  * `-display_rotation 0` before every input when the EDL says `orient: none`. B1's
    rotation side-data is spurious; honouring it yields sideways frames.

    `-noautorotate` is NOT sufficient and shipped a rotated cut once. It stops ffmpeg
    *applying* the rotation, but the display matrix is still copied to the output
    stream — and since concat with `-c copy` takes stream properties from the first
    part, one clip's spurious metadata silently rotates the entire film. Variant A
    began with GX010474 (rotation=-90) and played sideways end to end; variant B began
    with a clip carrying no side data and was fine. `-metadata:s:v:0 rotate=0` does not
    fix it either — it is a no-op against display-matrix side data in ffmpeg 7.
    `assert_no_rotation` below is the guard that makes this impossible to ship again.
  * Loudness is matched by applying a fixed per-clip gain derived from the integrated
    LUFS already measured in the R8 audio sidecars, rather than running loudnorm per
    segment. A per-segment loudnorm re-levels each cut independently, which pumps
    across a cut list where one clip supplies five consecutive segments.

Usage:
    uv run assemble.py EDL.json --footage ~/footage/copper-02-2026 \
        --sidecars ~/work/audio -o out.mp4
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import effects            # noqa: E402  (after sys.path)

TARGET_LUFS = -16.0
MAX_GAIN_DB = 12.0          # refuse to amplify near-silence into hiss
W, H, FPS = 1920, 1080, "24000/1001"

# `preview` is the fast path the board's quick versions use — unchanged from what
# this file always did. `delivery` trades render time for the two things a 1080p24
# proxy of the cut throws away: motion resolution (the source's own frame rate,
# conformed rather than decimated) and detail (up to 4K, never upscaled past what
# the bin actually shot). `slow`/`crf 18`/`256k` over the preview numbers is a
# measured call, not a guess — see the commit that introduced this profile for the
# SSIM/PSNR/VMAF numbers behind it.
DELIVERY_MAX_W, DELIVERY_MAX_H = 3840, 2160
PROFILES = {
    "preview": {"preset": "veryfast", "crf": "20", "audio_bitrate": "192k"},
    "delivery": {"preset": "slow", "crf": "18", "audio_bitrate": "256k"},
}


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def clip_gain(sidecars: Path, clip: str) -> float:
    """dB to bring this clip to TARGET_LUFS, from the measured integrated loudness."""
    p = sidecars / f"{Path(clip).stem}.audio.json"
    if not p.exists():
        return 0.0
    lufs = json.loads(p.read_text(encoding="utf-8"))["summary"].get("integrated_lufs")
    if lufs is None:
        return 0.0
    return max(-MAX_GAIN_DB, min(MAX_GAIN_DB, TARGET_LUFS - float(lufs)))


def cut(src: Path, t_in: float, t_out: float, gain_db: float, dest: Path,
        orient: str, video: dict) -> None:
    cmd = ["ffmpeg", "-v", "error", "-y", "-nostdin"]
    if orient == "none":
        cmd += ["-display_rotation", "0"]           # must precede -i; see module docstring
    cmd += [
        "-ss", f"{t_in:.3f}", "-i", str(src), "-t", f"{t_out - t_in:.3f}",
        "-vf", f"scale={video['w']}:{video['h']}:force_original_aspect_ratio=decrease,"
               f"pad={video['w']}:{video['h']}:(ow-iw)/2:(oh-ih)/2,"
               f"fps={video['fps']},format=yuv420p",
        "-af", f"volume={gain_db:.2f}dB,aresample=48000:first_pts=0",
        "-map", "0:v:0", "-map", "0:a:0", "-dn",    # drop GoPro's telemetry track
        "-c:v", "libx264", "-preset", video["preset"], "-crf", video["crf"],
        "-c:a", "aac", "-b:a", video["audio_bitrate"], "-ac", "2",
        "-video_track_timescale", str(video["timescale"]),
        # -dn drops the source telemetry stream; -write_tmcd stops the mov muxer
        # from synthesising a fresh timecode track out of the source metadata.
        "-write_tmcd", "0",
        "-movflags", "+faststart", str(dest),
    ]
    r = run(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"cut failed {src.name} {t_in}-{t_out}: {r.stderr[-400:]}")


def probe_video(path: Path) -> tuple[str, int, int]:
    """(r_frame_rate, width, height) of a clip's first video stream — what the
    delivery profile conforms a bin to, instead of the preview profile's fixed
    1920x1080 @ 24000/1001.

    JSON, not `-of csv=p=0`: csv emits fields in ffprobe's own internal stream-dump
    order, not the order named after `-show_entries`, so unpacking it positionally
    silently reads the wrong column (this shipped once — width landed in the fps
    variable and `int()` on a fraction string is how it was caught).
    """
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate,width,height",
             "-of", "json", str(path)])
    s = json.loads(r.stdout)["streams"][0]
    return s["r_frame_rate"], int(s["width"]), int(s["height"])


def bin_profile(footage: Path, segments: list[dict]) -> tuple[str, int, int]:
    """Majority native fps and resolution across the clips this EDL actually uses.

    `delivery` conforms every segment to one frame rate and one frame size — concat
    with `-c copy` requires it, same as the preview profile's fixed 1920x1080 @
    24000/1001 does today — but a fixed constant would either upscale a bin shot
    smaller than 4K or throw away a majority-60fps bin's own motion in favour of a
    guess. The majority of what the bin actually *is* stands in for that guess, the
    same way a mixed 30/60fps bin already forces one rate under the preview profile.

    Measured on Killington (research, 2026-08-23): 9 of 12 clips shoot 30000/1001,
    3 shoot 60000/1001 — every source r_frame_rate carries GoPro's own /1001 NTSC
    denominator, so both the existing 30→24 and 60→24 decimations, and delivery's
    60→30, land on an exact rational ratio (5:4 and 5:2 and 2:1 respectively): a
    regular drop cadence, not the erratic one a mismatched denominator would give.
    """
    clips = sorted({s["clip"] for s in segments})
    if not clips:
        raise SystemExit("delivery profile needs at least one segment to probe")
    probed = [probe_video(footage / c) for c in clips]
    fps = Counter(f for f, _, _ in probed).most_common(1)[0][0]
    w, h = Counter((w, h) for _, w, h in probed).most_common(1)[0][0]
    return fps, w, h


def delivery_resolution(native_w: int, native_h: int) -> tuple[int, int]:
    """Capped at DELIVERY_MAX_W x DELIVERY_MAX_H, never upscaled past the bin's own
    native size. A source already at or below 4K (Killington is 3840x2160; the test
    suite's synthetic clips are 320x180) must render at its own resolution, not be
    stretched up to fill a bigger canvas that carries no more real detail."""
    scale = min(1.0, DELIVERY_MAX_W / native_w, DELIVERY_MAX_H / native_h)
    w = max(2, int(native_w * scale) // 2 * 2)      # even dims: yuv420p needs it
    h = max(2, int(native_h * scale) // 2 * 2)
    return w, h


def resolve_video_profile(name: str, footage: Path, segments: list[dict]) -> dict:
    """The concrete {w, h, fps, preset, crf, audio_bitrate, timescale} for `cut()`."""
    base = PROFILES[name]
    if name == "delivery":
        fps, native_w, native_h = bin_profile(footage, segments)
        w, h = delivery_resolution(native_w, native_h)
    else:
        fps, w, h = FPS, W, H
    num, _, den = fps.partition("/")
    timescale = int(num) if den else int(num) * 1000   # keep sub-frame PTS precision
    return {**base, "fps": fps, "w": w, "h": h, "timescale": timescale}


def rotation_of(p: Path) -> str:
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream_side_data=rotation", "-of", "csv=p=0", str(p)])
    return r.stdout.strip().strip(",")


def assert_no_rotation(p: Path, orient: str) -> None:
    """A rendered cut must carry no rotation side data, whatever the source did.

    Under `orient: none` the metadata was spurious and must not survive; under
    `orient: auto` it was correct and has been baked into the pixels, so a matrix
    left behind would apply it a second time. Either way its presence is a bug,
    and it is invisible until someone plays the file — which is how it shipped.
    """
    rot = rotation_of(p)
    if rot:
        raise RuntimeError(
            f"{p.name} carries rotation={rot} (orient={orient}). The first segment's "
            f"display matrix has leaked into the concatenated output; players will "
            f"rotate the whole cut.")


def probe_duration(p: Path) -> float:
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(p)])
    return float(r.stdout.strip()) if r.stdout.strip() else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description="Render an EDL to a video file.")
    ap.add_argument("edl", type=Path)
    ap.add_argument("--footage", type=Path, required=True)
    ap.add_argument("--sidecars", type=Path, help="R8 audio sidecars, for loudness matching")
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--keep-parts", action="store_true", help="leave segment files on disk")
    ap.add_argument("--music", type=Path, default=None,
                    help="audio track to lay under the film; overrides the EDL's own "
                         "`effects_music.asset`")
    ap.add_argument("--assets", type=Path, default=None,
                    help="root for relative asset paths (see docs/EFFECTS.md)")
    ap.add_argument("--parts-dir", type=Path, default=None,
                    help="write the per-segment parts here instead of a temp dir, so "
                         "a caller can count them as progress")
    ap.add_argument("--profile", choices=tuple(PROFILES), default="preview",
                    help="preview (default): today's fixed 1920x1080 @ 24000/1001, "
                         "veryfast/crf20 — what the board's quick versions use. "
                         "delivery: the bin's own majority frame rate and up to 4K "
                         "resolution (never upscaled), preset slow, crf 18.")
    args = ap.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            print(f"error: {tool} not found on PATH", file=sys.stderr)
            return 2

    edl = json.loads(args.edl.read_text(encoding="utf-8"))
    segments = edl["segments"]
    orient = edl.get("orient", "auto")
    planned = sum(s["out"] - s["in"] for s in segments)
    video = resolve_video_profile(args.profile, args.footage, segments)
    print(f"variant {edl['variant']} — {edl['title']}: {len(segments)} segments, "
          f"{planned:.1f}s planned, orient={orient}")
    print(f"profile {args.profile}: {video['w']}x{video['h']} @ {video['fps']}fps, "
          f"preset {video['preset']}, crf {video['crf']}, audio {video['audio_bitrate']}")

    if args.parts_dir:
        # Each part appears as it is cut, so whoever asked for this render can count
        # files instead of parsing prose — and the count stays right if this output
        # format changes.
        workdir = args.parts_dir
        workdir.mkdir(parents=True, exist_ok=True)
        for stale in workdir.glob("part_*.mp4"):
            stale.unlink()
    else:
        workdir = Path(tempfile.mkdtemp(prefix="roughcut-"))
    parts: list[Path] = []
    try:
        for i, seg in enumerate(segments):
            src = args.footage / seg["clip"]
            if not src.exists():
                raise SystemExit(f"missing footage: {src}")
            gain = clip_gain(args.sidecars, seg["clip"]) if args.sidecars else 0.0
            dest = workdir / f"part_{i:03d}.mp4"
            cut(src, seg["in"], seg["out"], gain, dest, orient, video)
            assert_no_rotation(dest, orient)        # catch it at the part, not the film
            actual = probe_duration(dest)
            parts.append(dest)
            print(f"  {i:02d} {seg['clip']} {seg['in']:6.1f}-{seg['out']:6.1f} "
                  f"({actual:5.2f}s, {gain:+5.1f}dB)  {seg['why'][:56]}")

        listfile = workdir / "concat.txt"
        listfile.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        r = run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "concat", "-safe", "0",
                 "-i", str(listfile), "-c", "copy", "-write_tmcd", "0",
                 "-movflags", "+faststart", str(args.out)])
        if r.returncode != 0:
            raise RuntimeError(f"concat failed: {r.stderr[-400:]}")

        assert_no_rotation(args.out, orient)

        # Film-wide effects run here, after the picture is assembled: the video stream
        # is copied through, so the bed costs no generation of quality.
        music = (edl.get("effects_music") or {}) if isinstance(edl, dict) else {}
        if args.music:
            music = {**music, "asset": str(args.music)}
        if music.get("asset"):
            scored = args.out.with_name(args.out.stem + ".scored.mp4")
            # The transcripts know where the talking is; ducking on the film's
            # amplitude instead makes the bed breathe on every ski scrape.
            speech = (effects.speech_regions(segments, args.sidecars)
                      if args.sidecars else None)
            applied = effects.add_music(args.out, music, scored, args.assets,
                                        speech=speech)
            scored.replace(args.out)
            assert_no_rotation(args.out, orient)
            how = "flat"
            if applied["duck"]:
                how = f"ducked, keyed on {applied['keyed_on']}"
                if applied["speech_regions"]:
                    how += f" ({applied['speech_regions']} speech regions)"
            print(f"music: {Path(applied['asset_path']).name} at "
                  f"{applied['gain_db']}dB — {how}")

        final = probe_duration(args.out)
        drift = final - planned
        print(f"\nwrote {args.out}  {final:.2f}s (planned {planned:.1f}s, "
              f"drift {drift:+.2f}s)")
        if abs(drift) > 1.0:
            print("warning: >1s drift between planned and rendered duration",
                  file=sys.stderr)
    finally:
        if args.keep_parts:
            print(f"parts kept in {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
