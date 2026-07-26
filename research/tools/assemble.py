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
from pathlib import Path

TARGET_LUFS = -16.0
MAX_GAIN_DB = 12.0          # refuse to amplify near-silence into hiss
W, H, FPS = 1920, 1080, "24000/1001"


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
        orient: str) -> None:
    cmd = ["ffmpeg", "-v", "error", "-y", "-nostdin"]
    if orient == "none":
        cmd += ["-display_rotation", "0"]           # must precede -i; see module docstring
    cmd += [
        "-ss", f"{t_in:.3f}", "-i", str(src), "-t", f"{t_out - t_in:.3f}",
        "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
               f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps={FPS},format=yuv420p",
        "-af", f"volume={gain_db:.2f}dB,aresample=48000:first_pts=0",
        "-map", "0:v:0", "-map", "0:a:0", "-dn",    # drop GoPro's telemetry track
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-ac", "2",
        "-video_track_timescale", "24000",
        # -dn drops the source telemetry stream; -write_tmcd stops the mov muxer
        # from synthesising a fresh timecode track out of the source metadata.
        "-write_tmcd", "0",
        "-movflags", "+faststart", str(dest),
    ]
    r = run(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"cut failed {src.name} {t_in}-{t_out}: {r.stderr[-400:]}")


def music_filter(spec: dict, film_s: float) -> str:
    """Filter graph for a music bed under the finished film. See docs/EFFECTS.md.

    Ducked by default, and that default matters more than it looks: R8 established
    that in this footage the *words* carry the film, so a bed at a flat level buries
    the thing the cut was built around. `sidechaincompress` keyed on the film's own
    audio pulls the music down under speech and lets it back up in the gaps.

    The music is looped to the length of the film and then hard-trimmed, so a short
    track does not end the bed early and a long one does not run past the picture.
    """
    gain = float(spec.get("gain_db", -18.0))
    fade_in = max(0.0, float(spec.get("fade_in", 1.5)))
    fade_out = max(0.0, float(spec.get("fade_out", 4.0)))
    duck = bool(spec.get("duck", True))

    # [1:a] is the music input; [0:a] the film's own audio.
    bed = (f"[1:a]aloop=loop=-1:size=2e9,atrim=0:{film_s:.3f},"
           f"asetpts=N/SR/TB,volume={gain:.2f}dB,"
           f"afade=t=in:st=0:d={fade_in:.2f},"
           f"afade=t=out:st={max(0.0, film_s - fade_out):.3f}:d={fade_out:.2f}[bed]")
    if not duck:
        return f"{bed};[0:a][bed]amix=inputs=2:normalize=0:duration=first[aout]"
    # asplit because the film audio is both a mix input and the sidechain key.
    return (f"{bed};[0:a]asplit=2[dry][key];"
            f"[bed][key]sidechaincompress=threshold=0.02:ratio=6:attack=15:"
            f"release=400:makeup=1[ducked];"
            f"[dry][ducked]amix=inputs=2:normalize=0:duration=first[aout]")


def add_music(film: Path, spec: dict, assets: Path | None, out: Path) -> None:
    """Mix a bed under an already-assembled film.

    The video stream is **copied**, so a music pass costs nothing in picture quality —
    the reason film-wide effects run after the concat rather than per part.
    """
    track = Path(spec["asset"])
    if not track.is_absolute():
        track = (assets or Path(".")) / track
    if not track.exists():
        raise SystemExit(f"error: music asset not found: {track}")
    film_s = probe_duration(film)
    r = run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-i", str(film), "-i", str(track),
             "-filter_complex", music_filter(spec, film_s),
             "-map", "0:v:0", "-map", "[aout]", "-c:v", "copy",
             "-c:a", "aac", "-b:a", "192k", "-ac", "2",
             "-write_tmcd", "0", "-movflags", "+faststart", str(out)])
    if r.returncode != 0:
        raise RuntimeError(f"music pass failed: {r.stderr[-400:]}")


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
    args = ap.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            print(f"error: {tool} not found on PATH", file=sys.stderr)
            return 2

    edl = json.loads(args.edl.read_text(encoding="utf-8"))
    segments = edl["segments"]
    orient = edl.get("orient", "auto")
    planned = sum(s["out"] - s["in"] for s in segments)
    print(f"variant {edl['variant']} — {edl['title']}: {len(segments)} segments, "
          f"{planned:.1f}s planned, orient={orient}")

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
            cut(src, seg["in"], seg["out"], gain, dest, orient)
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
            add_music(args.out, music, args.assets, scored)
            scored.replace(args.out)
            assert_no_rotation(args.out, orient)
            print(f"music: {Path(music['asset']).name} at "
                  f"{music.get('gain_db', -18)}dB, "
                  f"{'ducked under speech' if music.get('duck', True) else 'flat'}")

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
