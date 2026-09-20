# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2", "pillow>=10"]
# ///
"""Cut an EDL into a rendered video. Throwaway prototype tooling — no OTIO, no index.

Each segment is extracted and re-encoded to identical parameters, then concatenated
with the concat demuxer. Re-encoding every segment is wasteful but it is the only
way to get frame-accurate cuts across GOP boundaries, and a 2-minute rough cut is
cheap enough that the waste does not matter.

The grade rides on that re-encode (INTAKE M10): each shot's colour is resolved the
way the board's inspector resolves it (`roughcut.colour.resolve_shot` over the clip's
`<stem>.colour.json` from `--colour-dir`), baked to one 33³ `.cube` next to the part
and applied with `lut3d` inside the part's own `-vf`, so it costs no generation of
quality. An HDR source (iPhone HLG) is tone mapped to SDR 709 *before* the scale,
grade or no grade — normalising is not a grade, and an HLG part must never reach the
concat un-tone-mapped. Every part is tagged limited-range bt709 so concat sees
identical stream properties; the picture was always limited 709, just untagged.

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
from roughcut import colour, effects, fx    # noqa: E402  (after sys.path)

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


# Every part is tagged as what it is — limited-range BT.709 — so the concat (which
# copies stream properties from the first part) sees identical streams whether or
# not a part went through the LUT chain. Verified not to touch a single pixel on
# ffmpeg 7.0.2: the tagged and untagged encodes of the same source hash identical.
COLOUR_TAGS = ["-color_range", "tv", "-colorspace", "bt709",
               "-color_primaries", "bt709", "-color_trc", "bt709"]


def video_filter(video: dict, normalise: str | None = None,
                 lut: str | None = None) -> str:
    """The part's `-vf`: normalise (HDR → SDR 709, before anything sees the frame),
    the scale/pad/fps conform, then either the LUT chain — which states the range on
    both sides and ends in its own `format=yuv420p` — or the plain `format=yuv420p`."""
    chain = []
    if normalise:
        chain.append(normalise)
    chain += [f"scale={video['w']}:{video['h']}:force_original_aspect_ratio=decrease",
              f"pad={video['w']}:{video['h']}:(ow-iw)/2:(oh-ih)/2",
              f"fps={video['fps']}"]
    chain.append(lut if lut else "format=yuv420p")
    return ",".join(chain)


def cut(src: Path, t_in: float, t_out: float, gain_db: float, dest: Path,
        orient: str, video: dict, normalise: str | None = None,
        lut: str | None = None) -> None:
    cmd = ["ffmpeg", "-v", "error", "-y", "-nostdin"]
    if orient == "none":
        cmd += ["-display_rotation", "0"]           # must precede -i; see module docstring
    cmd += [
        "-ss", f"{t_in:.3f}", "-i", str(src), "-t", f"{t_out - t_in:.3f}",
        "-vf", video_filter(video, normalise, lut),
        "-af", f"volume={gain_db:.2f}dB,aresample=48000:first_pts=0",
        "-map", "0:v:0", "-map", "0:a:0", "-dn",    # drop GoPro's telemetry track
        "-c:v", "libx264", "-preset", video["preset"], "-crf", video["crf"],
        *COLOUR_TAGS,
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


def cut_with_effects(src: Path, t_in: float, t_out: float, gain_db: float, dest: Path,
                     orient: str, video: dict, shot_effects: list[tuple[dict, list[dict]]],
                     workdir: Path, normalise: str | None = None,
                     lut: str | None = None) -> None:
    """`cut()` for a part that carries effects (INTAKE M12): the same conform and the
    same encode, but through one `-filter_complex` — `[0:v]<video_filter>[vbase]` and
    `[0:a]volume,aresample[abase]`, then `fx.part_graph`'s fragment (a sprite `.mov`
    per event overlaid at its clip second, the effect's synth `.wav` delayed and mixed)
    — mapped from its `[vout]`/`[aout]`. Everything about the part that the concat
    relies on (codec, tags, timescale, `-dn`, `-write_tmcd 0`) is identical, so the
    film still copies. The sprite inputs go between the source `-i` and `-t`: after an
    `-i`, a `-t` is an input option for the NEXT input and would cut the sprites short."""
    extra, graph, vout, aout = fx.part_graph(shot_effects, video["w"], video["h"],
                                             video["fps"], workdir)
    if not graph:
        return cut(src, t_in, t_out, gain_db, dest, orient, video, normalise, lut)
    fc = (f"[0:v]{video_filter(video, normalise, lut)}[vbase];"
          f"[0:a]volume={gain_db:.2f}dB,aresample=48000:first_pts=0[abase];{graph}")
    cmd = ["ffmpeg", "-v", "error", "-y", "-nostdin"]
    if orient == "none":
        cmd += ["-display_rotation", "0"]           # must precede -i; see module docstring
    cmd += ["-ss", f"{t_in:.3f}", "-i", str(src), *extra, "-t", f"{t_out - t_in:.3f}",
            "-filter_complex", fc, "-map", f"[{vout}]", "-map", f"[{aout}]", "-dn",
            "-c:v", "libx264", "-preset", video["preset"], "-crf", video["crf"],
            *COLOUR_TAGS,
            "-c:a", "aac", "-b:a", video["audio_bitrate"], "-ac", "2",
            "-video_track_timescale", str(video["timescale"]),
            "-write_tmcd", "0",
            "-movflags", "+faststart", str(dest)]
    r = run(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"cut with effects failed {src.name} {t_in}-{t_out}: {r.stderr[-400:]}")


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


# ------------------------------------------------------------------ colour (M10)

def load_colour_file(colour_dir: Path | None, clip: str) -> dict | None:
    """The clip's `<stem>.colour.json` from `--colour-dir`, or None: unmeasured, or
    no colour dir at all. Either way the shot still takes the film's look; it just
    gets no balance (`colour.resolve_shot` reads None as "the camera's picture")."""
    if colour_dir is None:
        return None
    p = colour_dir / f"{Path(clip).stem}.colour.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


def resolve_all(edl: dict, segments: list[dict], colour_dir: Path | None,
                looks: dict[str, dict]) -> list[tuple[dict, dict | None]]:
    """Every shot resolved, indexed like `segments`, in the order the board's
    `server.resolve_colour` uses: the film's `colour.reference` shot first (else the
    first shot with a summary becomes the reference), `previous` the shot resolved
    before. Keyed by index rather than id so segments without ids (older EDLs) never
    collapse onto one another. Returns `(res, clip_colour)` per shot."""
    ref_id = (edl.get("colour") or {}).get("reference")
    order = sorted(range(len(segments)),
                   key=lambda i: 0 if ref_id and segments[i].get("id") == ref_id else 1)
    out: list = [None] * len(segments)
    reference = previous = None
    for i in order:
        seg = segments[i]
        cc = load_colour_file(colour_dir, seg["clip"])
        res = colour.resolve_shot(edl, seg, cc, looks, reference=reference,
                                  previous=previous)
        ctx = colour.shot_context(res)
        if reference is None and ctx:
            reference = ctx
        previous = ctx or previous
        out[i] = (res, cc)
    return out


def shot_probe(src: Path, clip_colour: dict | None, cache: dict) -> dict:
    """The clip's colour tags: the colour file's own probe when it carries one, else
    ffprobe once per clip. Needed for every part, graded or not — the normalise
    step and the LUT's input range both come from it."""
    if clip_colour and clip_colour.get("probe"):
        return clip_colour["probe"]
    if src not in cache:
        cache[src] = colour.probe(src)
    return cache[src]


def describe_colour(res: dict, normalise: str | None, probe: dict) -> str:
    """One line for the render log: what the part got, in the colourist's order."""
    bits = []
    b = res.get("balance")
    if b:
        bits.append(f"balance {b.get('source', 'auto')} ×{float(b['exposure']):.2f}")
    m = res.get("match")
    if m:
        bits.append(f"match dL{m['dL']:+.1f} da{m['da']:+.1f} db{m['db']:+.1f}")
    if res.get("look"):
        bits.append(f"look {res['look']} {res['strength']:.2f}")
    if not bits:
        bits.append("as shot")
    if normalise:
        bits.append(f"normalised {probe.get('hdr') or 'hdr'}")
    return " · ".join(bits)


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
    ap.add_argument("--colour-dir", type=Path, default=None,
                    help="per-clip colour files (<stem>.colour.json, INTAKE M10); the "
                         "EDL's `colour` block is resolved against them per shot")
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
        for stale in list(workdir.glob("part_*.mp4")) + list(workdir.glob("part_*.cube")):
            stale.unlink()
    else:
        workdir = Path(tempfile.mkdtemp(prefix="roughcut-"))
    parts: list[Path] = []
    for s in segments:
        if not (args.footage / s["clip"]).exists():
            raise SystemExit(f"missing footage: {args.footage / s['clip']}")
    # The grade, resolved for every shot before any part is cut: the reference shot
    # may sit later in the film than the shots that match to it.
    looks = colour.load_looks(args.assets)
    resolved = resolve_all(edl, segments, args.colour_dir, looks)
    probes: dict = {}
    try:
        for i, seg in enumerate(segments):
            src = args.footage / seg["clip"]
            gain = clip_gain(args.sidecars, seg["clip"]) if args.sidecars else 0.0
            dest = workdir / f"part_{i:03d}.mp4"
            res, clip_colour = resolved[i]
            probe = shot_probe(src, clip_colour, probes)
            # Normalise always, grade optionally: an HLG source is tone mapped to SDR
            # 709 whatever `colour.mode` says. After it the LUT's input is limited.
            normalise = colour.normalise_vf(probe)
            lut = None
            if not res["identity"]:
                cube = colour.write_cube(colour.cube_table(res["fn"], 33),
                                         workdir / f"part_{i:03d}.cube",
                                         title=f"roughcut {seg.get('id') or i}")
                lut = colour.lut_vf(cube, colour.in_range(probe))
            # The shot's accepted effects (INTAKE M12) fold into this same encode;
            # an event outside the shot's range is simply not drawn.
            shot_effects = [(e, fx.events_in_shot(e, seg)) for e in fx.effects_for_shot(edl, seg)]
            shot_effects = [(e, evs) for e, evs in shot_effects if evs]
            if shot_effects:
                cut_with_effects(src, seg["in"], seg["out"], gain, dest, orient, video,
                                 shot_effects, workdir / f"part_{i:03d}_fx",
                                 normalise=normalise, lut=lut)
            else:
                cut(src, seg["in"], seg["out"], gain, dest, orient, video,
                    normalise=normalise, lut=lut)
            assert_no_rotation(dest, orient)        # catch it at the part, not the film
            actual = probe_duration(dest)
            parts.append(dest)
            print(f"  {i:02d} {seg['clip']} {seg['in']:6.1f}-{seg['out']:6.1f} "
                  f"({actual:5.2f}s, {gain:+5.1f}dB)  {seg['why'][:56]}")
            print(f"      colour: {describe_colour(res, normalise, probe)}")
            for e, evs in shot_effects:
                print(f"      fx: {e.get('name', 'effect')} × {len(evs)} at "
                      + ", ".join(f"{ev['t_part']:.2f}s" for ev in evs))

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
