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
import math
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import colour, edits, effects, fx    # noqa: E402  (after sys.path)
from roughcut import tracks as tracksmod           # noqa: E402

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
    # The Export dialog's YouTube 1080p (REDESIGN-GOAL dimension 8): the bin's own frame
    # rate like `delivery` (YouTube wants the native rate), capped at 1920x1080 and
    # never upscaled, with YouTube's recommended 384 kbps stereo AAC.
    "youtube": {"preset": "medium", "crf": "18", "audio_bitrate": "384k"},
    # The Export dialog's Match Sequence (verdict 3 punch 10): the sequence's own frame
    # size and rate, nothing capped and nothing upscaled, crf 18 at medium.
    "match": {"preset": "medium", "crf": "18", "audio_bitrate": "320k"},
}
YOUTUBE_MAX_W, YOUTUBE_MAX_H = 1920, 1080
MATCH_MAX_W, MATCH_MAX_H = 8192, 8192
AUDIO_RATE = 48000

# The sound is AAC-encoded exactly once (verdict 4, punch 6). Every part carries its
# sound as 16-bit PCM — lossless, so levelling, retiming and the effects' sounds cost no
# generation — and the join encodes it, unless a later pass re-lays it anyway (A1 rebuilt
# from the parts, a music bed mixed under it): then the join writes PCM too and that pass
# is the one encode. An AAC part re-encoded at the join was two generations, and a third
# with a bed under it.
AUDIO_PCM = ["-c:a", "pcm_s16le", "-ac", "2", "-ar", str(AUDIO_RATE)]


def audio_codec(video: dict, final: bool) -> list[str]:
    """The film's sound: AAC at the profile's bitrate when this pass is the last to
    touch it, PCM when a later pass will encode it."""
    if final:
        return ["-c:a", "aac", "-b:a", video["audio_bitrate"], "-ac", "2"]
    return AUDIO_PCM

# `--progress`: every part's encode writes ffmpeg's `-progress` beside it
# (`part_007.progress`), so whoever asked for the render can read how many seconds of
# the film are encoded rather than guess from a clock (Export dialog, verdict 2 punch 5).
PART_PROGRESS = False


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    if PART_PROGRESS and cmd[:1] == ["ffmpeg"] and Path(cmd[-1]).name.startswith("part_"):
        cmd = ["ffmpeg", "-progress", str(Path(cmd[-1]).with_suffix(".progress")),
               *cmd[1:]]
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


def atempo_chain(speed: float) -> str:
    """The audio's retime for a shot at `speed`, as `atempo` factors within its
    0.5–2 window: 0.25 is `atempo=0.5,atempo=0.5`, 4 is `atempo=2,atempo=2`, 0.1 is
    `atempo=0.5,atempo=0.5,atempo=0.5,atempo=0.8`. Empty at 1."""
    if abs(speed - 1.0) < 1e-9:
        return ""
    factors: list[float] = []
    rest = float(speed)
    while rest < 0.5 - 1e-9:
        factors.append(0.5)
        rest /= 0.5
    while rest > 2.0 + 1e-9:
        factors.append(2.0)
        rest /= 2.0
    if abs(rest - 1.0) > 1e-9:
        factors.append(rest)
    return ",".join(f"atempo={f:g}" for f in factors)


def audio_filter(gain_db: float, speed: float = 1.0) -> str:
    tempo = atempo_chain(speed)
    return (f"volume={gain_db:.2f}dB," + (f"{tempo}," if tempo else "")
            + "aresample=48000:first_pts=0")


def part_seconds(t_in: float, t_out: float, speed: float = 1.0) -> float:
    """What the part lasts in the film — `edits.dur` for a range at a speed."""
    return edits.dur({"in": t_in, "out": t_out, "speed": speed})


def retimed_events(events: list[dict], speed: float) -> list[dict]:
    """An effect's events inside a retimed part: `t_part` is seconds from the shot's
    in-point in *clip* time; in a part at `speed` that instant lands at `t_part /
    speed` of output. The overlay and the sound keep their own durations — a hit
    marker in slow motion is still a hit marker, not a slow one."""
    if abs(speed - 1.0) < 1e-9:
        return events
    return [{**ev, "t_part": round(float(ev["t_part"]) / speed, 4)} for ev in events]


# ------------------------------------------------- the frame grid (verdict 3, CFR)
#
# The journey test found exports that were variable frame rate (r_frame_rate 120/1,
# avg 23.89 on a 24 fps sequence): every part was `-t` seconds long, which is not a
# whole number of frames, and its sound ran a few ms past its picture, so the concat
# demuxer started the next part's picture after a gap. Now every part is a whole
# number of frames — counted off the film's frame grid, so the rounding never adds up
# along the cut — its sound exactly that long, and the join lays the pictures end to
# end by those lengths and mixes the sound as one stream. The file is constant frame
# rate at the rate asked for, and its length is the sequence's to half a frame.

def rate_of(fps: str | float) -> Fraction:
    """`"30000/1001"`, `"24"`, `29.97` → the exact rate. A decimal NTSC rate (23.976,
    29.97, 59.94, 47.952, 119.88) is the /1001 one it stands for; anything else is
    read as written."""
    text = str(fps).strip()
    if "/" in text:
        num, _, den = text.partition("/")
        return Fraction(int(num), int(den))
    f = float(text)
    if abs(f - round(f)) < 1e-6:
        return Fraction(int(round(f)))
    ntsc = round(f * 1.001)
    if abs(f - ntsc / 1.001) < 0.002:
        return Fraction(ntsc * 1000, 1001)
    return Fraction(f).limit_denominator(1001)


def fps_text(rate: Fraction) -> str:
    """The rate as ffmpeg and `-r` take it: `24`, `30000/1001`."""
    return str(rate.numerator) if rate.denominator == 1 else f"{rate.numerator}/{rate.denominator}"


def frame_spans(segments: list[dict], rate: Fraction) -> list[tuple[int, int]]:
    """Each shot's (first frame, end frame) in the film, off its cumulative seconds:
    a shot is `end - first` frames and the film `spans[-1][1]`, which is the
    sequence's length rounded to the nearest frame — never a frame per cut off."""
    spans, t = [], 0.0
    for seg in segments:
        a = round(t * rate)
        t += edits.dur(seg)
        spans.append((a, max(a + 1, round(t * rate))))
    return spans


def samples_at(frame: int, rate: Fraction) -> int:
    """Audio samples from the film's start to `frame`, on the same grid."""
    return round(frame * AUDIO_RATE / rate)


# ---------------------------------------------- Effect Controls (verdict 3, punch 3)
#
# A segment's `controls` (app/static/effects.js; validated by server.clean_controls):
# position [x, y] fractions of the frame with 0.5 the centre, scale %, rotation
# degrees clockwise, opacity 0..100, volume_db, contrast -100..100, saturation 0..200,
# fx_off and fx_order. The Program monitor shows them as a CSS transform/opacity on
# the picture's layers and grade.js's shader; the render does the same maths here.

def ctl_num(controls: dict | None, key: str, default: float) -> float:
    v = (controls or {}).get(key)
    return default if v is None else float(v)


def adjust_vf(controls: dict | None) -> str | None:
    """Lumetri contrast and saturation, after the LUT, as grade.js's shader does them
    in RGB: `y = (c - 0.5) * (1 + contrast/200) + 0.5`, then a mix toward Rec.709 luma
    by saturation/100. In the part's limited-range BT.709 YUV that is exactly: luma
    about its middle (125.5) times k, and both chroma about 128 times k × s (a
    per-channel contrast about 0.5 scales R-Y and B-Y by k; the mix toward luma keeps
    luma and scales them by s). Clipped to the legal range, as the shader clamps."""
    k = 1.0 + ctl_num(controls, "contrast", 0.0) / 200.0
    sat = ctl_num(controls, "saturation", 100.0) / 100.0
    if abs(k - 1.0) < 1e-9 and abs(sat - 1.0) < 1e-9:
        return None
    ks = k * sat
    return (f"lutyuv=y='clip((val-125.5)*{k:.6g}+125.5,16,235)'"
            f":u='clip((val-128)*{ks:.6g}+128,16,240)'"
            f":v='clip((val-128)*{ks:.6g}+128,16,240)'")


def volume_db(controls: dict | None) -> float:
    return ctl_num(controls, "volume_db", 0.0)


def dim_vf(opacity: float) -> str:
    """Opacity over black: in limited YUV, a straight blend toward (16, 128, 128)."""
    a = f"{opacity:.6g}"
    return f"lutyuv=y='16+(val-16)*{a}':u='128+(val-128)*{a}':v='128+(val-128)*{a}'"


def motion_graph(controls: dict | None, w: int, h: int, vin: str, vout: str) -> str | None:
    """Motion and Opacity as a filter-graph fragment from `[vin]` to `[vout]`, or None
    at the defaults. The monitor's CSS is `translate((x-0.5)·W, (y-0.5)·H) rotate(r)
    scale(s)` about the picture's centre: a source point p lands at
    `q = (x·W, y·H) + s·R(r)·(p − centre)`. Only the part of the source that can land
    in the frame is cropped (so a 1000 % scale never builds a 19200-wide frame), scaled,
    rotated (corners black) and overlaid on black where its own centre lands."""
    c = controls or {}
    pos = c.get("position") or [0.5, 0.5]
    px, py = float(pos[0]), float(pos[1])
    s = ctl_num(c, "scale", 100.0) / 100.0
    rot = ctl_num(c, "rotation", 0.0)
    op = max(0.0, min(1.0, ctl_num(c, "opacity", 100.0) / 100.0))
    turned = abs(math.remainder(rot, 360.0)) > 1e-9
    moved = abs(px - 0.5) > 1e-9 or abs(py - 0.5) > 1e-9 or abs(s - 1.0) > 1e-9 or turned
    dim = op < 1.0 - 1e-9
    if not moved and not dim:
        return None
    if not moved:
        return f"[{vin}]{dim_vf(op)}[{vout}]"
    black = f"[{vin}]drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill[{vout}]"
    if s <= 1e-6 or op <= 1e-6:
        return black
    th = math.radians(rot)
    cos, sin = round(math.cos(th), 12), round(math.sin(th), 12)
    tx, ty, cx, cy = px * w, py * h, w / 2.0, h / 2.0

    def to_src(qx: float, qy: float) -> tuple[float, float]:
        dx, dy = (qx - tx) / s, (qy - ty) / s
        return cx + cos * dx + sin * dy, cy - sin * dx + cos * dy

    corners = [to_src(qx, qy) for qx, qy in ((0, 0), (w, 0), (0, h), (w, h))]
    x0 = max(0, int(math.floor(min(p[0] for p in corners))) - 2) // 2 * 2
    y0 = max(0, int(math.floor(min(p[1] for p in corners))) - 2) // 2 * 2
    x1 = min(w, int(math.ceil(max(p[0] for p in corners))) + 2)
    y1 = min(h, int(math.ceil(max(p[1] for p in corners))) + 2)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return black                        # nothing of the picture lands in the frame
    cw = min(w - x0, (x1 - x0 + 1) // 2 * 2)
    ch = min(h - y0, (y1 - y0 + 1) // 2 * 2)
    sw = max(2, int(round(cw * s / 2)) * 2)
    sh = max(2, int(round(ch * s / 2)) * 2)
    chain = [f"crop={cw}:{ch}:{x0}:{y0}", f"scale={sw}:{sh}"]
    rw, rh = sw, sh
    if turned:
        rw = int(math.ceil((sw * abs(cos) + sh * abs(sin)) / 2)) * 2
        rh = int(math.ceil((sw * abs(sin) + sh * abs(cos)) / 2)) * 2
        chain.append(f"rotate={th:.9f}:ow={rw}:oh={rh}:c=black")
    if dim:
        chain.append(dim_vf(op))
    # where the crop's own centre lands; the rotated box is centred on it
    ex, ey = x0 + cw / 2.0 - cx, y0 + ch / 2.0 - cy
    qx = tx + s * (cos * ex - sin * ey)
    qy = ty + s * (sin * ex + cos * ey)
    ox, oy = int(round(qx - rw / 2.0)), int(round(qy - rh / 2.0))
    return (f"[{vin}]split[mo_c][mo_f];"
            f"[mo_c]drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill[mo_bg];"
            f"[mo_f]{','.join(chain)}[mo_fg];"
            f"[mo_bg][mo_fg]overlay=x={ox}:y={oy}:eof_action=pass,format=yuv420p[{vout}]")


def ordered_effects(edl: dict, seg: dict) -> list[dict]:
    """The shot's accepted effects as its Effect Controls stack has them: a bypassed
    one (`fx_off`) is not drawn, and the rest go in `fx_order` (the board's own rank:
    an id in the order by its place, any other after them in the EDL's order). The
    first is drawn first, so the last is on top."""
    c = seg.get("controls") or {}
    off = set(c.get("fx_off") or [])
    order = list(c.get("fx_order") or [])
    mine = [e for e in fx.effects_for_shot(edl, seg) if str(e.get("id")) not in off]

    def rank(e: dict) -> float:
        eid = str(e.get("id"))
        return order.index(eid) if eid in order else 1e6 + mine.index(e)
    return sorted(mine, key=rank)


def video_filter(video: dict, normalise: str | None = None,
                 lut: str | None = None, speed: float = 1.0,
                 controls: dict | None = None) -> str:
    """The part's `-vf`: normalise (HDR → SDR 709, before anything sees the frame),
    the retime when the shot has a speed (`setpts` — so the *frame count* changes,
    not the rate: the `fps` conform after it duplicates or drops to the film's rate;
    ahead of the conform rather than after it so a 60 fps source slowed to half
    keeps its own frames instead of doubling decimated ones), the scale/pad/fps
    conform, then either the LUT chain — which states the range on both sides and
    ends in its own `format=yuv420p` — or the plain `format=yuv420p`; then the
    shot's Lumetri contrast and saturation (`adjust_vf`), after the table as on the
    monitor."""
    chain = []
    if normalise:
        chain.append(normalise)
    if abs(speed - 1.0) > 1e-9:
        chain.append(f"setpts=(PTS-STARTPTS)/{speed:g}")
    chain += [f"scale={video['w']}:{video['h']}:force_original_aspect_ratio=decrease",
              f"pad={video['w']}:{video['h']}:(ow-iw)/2:(oh-ih)/2",
              f"fps={video['fps']}"]
    chain.append(lut if lut else "format=yuv420p")
    adj = adjust_vf(controls)
    if adj:
        chain.append(adj)
    return ",".join(chain)


def encode_part(src: Path, t_in: float, t_out: float, gain_db: float, dest: Path,
                orient: str, video: dict, *, normalise: str | None = None,
                lut: str | None = None, speed: float = 1.0, controls: dict | None = None,
                shot_effects: list[tuple[dict, list[dict]]] | None = None,
                workdir: Path | None = None, span: tuple[int, int] | None = None) -> None:
    """One part: the conform, the grade, the shot's effects (`fx.part_graph`: a
    sprite `.mov` per event overlaid at its second, the effect's synth `.wav` delayed
    and mixed), its Motion and Opacity and its Volume, through one `-filter_complex`.
    With `span` (its frames on the film's grid) the picture is exactly that many
    frames (the last one held if the source runs short) and the sound exactly that
    long; without it the part is `-t` seconds as it always was. Everything about the
    part that the join relies on (codec, tags, timescale, `-dn`, `-write_tmcd 0`) is
    the same for every part. The sprite inputs go after the source's `-i`; the
    source's own read limit is an input `-t` ahead of it."""
    extra: list[str] = []
    vcur, acur = "vbase", "abase"
    graph = [f"[0:v]{video_filter(video, normalise, lut, speed, controls)}[vbase]",
             f"[0:a]{audio_filter(gain_db + volume_db(controls), speed)}[abase]"]
    if shot_effects:
        extra, fxg, vcur, acur = fx.part_graph(shot_effects, video["w"], video["h"],
                                               video["fps"], workdir or dest.parent)
        if fxg:
            graph.append(fxg)
    mg = motion_graph(controls, video["w"], video["h"], vcur, "vmo")
    if mg:
        graph.append(mg)
        vcur = "vmo"
    tail: list[str] = []
    if span is not None:
        rate = rate_of(video["fps"])
        frames = span[1] - span[0]
        samples = samples_at(span[1], rate) - samples_at(span[0], rate)
        graph.append(f"[{vcur}]tpad=stop_mode=clone:stop_duration=2,"
                     f"trim=end_frame={frames},setpts=PTS-STARTPTS[vout]")
        graph.append(f"[{acur}]apad,atrim=end_sample={samples},asetpts=PTS-STARTPTS[aout]")
        vcur, acur = "vout", "aout"
    else:
        tail = ["-t", f"{part_seconds(t_in, t_out, speed):.3f}"]
    cmd = ["ffmpeg", "-v", "error", "-y", "-nostdin"]
    if orient == "none":
        cmd += ["-display_rotation", "0"]           # must precede -i; see module docstring
    cmd += ["-ss", f"{t_in:.3f}", "-t", f"{max(0.05, t_out - t_in) + 1.0:.3f}",
            "-i", str(src), *extra, *tail,
            "-filter_complex", ";".join(graph),
            "-map", f"[{vcur}]", "-map", f"[{acur}]", "-dn",   # drop GoPro's telemetry track
            "-c:v", "libx264", "-preset", video["preset"], "-crf", video["crf"],
            "-fps_mode", "cfr",
            *COLOUR_TAGS,
            # PCM, not AAC: the film's sound is encoded once, at the end (`AUDIO_PCM`)
            *AUDIO_PCM,
            "-video_track_timescale", str(video["timescale"]),
            # -dn drops the source telemetry stream; -write_tmcd stops the mov muxer
            # from synthesising a fresh timecode track out of the source metadata.
            "-write_tmcd", "0",
            "-movflags", "+faststart", str(dest)]
    r = run(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"cut failed {src.name} {t_in}-{t_out}: {r.stderr[-600:]}")


def cut(src: Path, t_in: float, t_out: float, gain_db: float, dest: Path,
        orient: str, video: dict, normalise: str | None = None,
        lut: str | None = None, speed: float = 1.0, *, controls: dict | None = None,
        span: tuple[int, int] | None = None) -> None:
    encode_part(src, t_in, t_out, gain_db, dest, orient, video, normalise=normalise,
                lut=lut, speed=speed, controls=controls, span=span)


def cut_with_effects(src: Path, t_in: float, t_out: float, gain_db: float, dest: Path,
                     orient: str, video: dict, shot_effects: list[tuple[dict, list[dict]]],
                     workdir: Path, normalise: str | None = None,
                     lut: str | None = None, speed: float = 1.0, *,
                     controls: dict | None = None,
                     span: tuple[int, int] | None = None) -> None:
    """`cut()` for a part that carries effects (INTAKE M12). In a retimed part
    (INTAKE M13) the events are already in output seconds — see `retimed_events` —
    and the base chains carry the shot's speed."""
    encode_part(src, t_in, t_out, gain_db, dest, orient, video, normalise=normalise,
                lut=lut, speed=speed, controls=controls, shot_effects=shot_effects,
                workdir=workdir, span=span)


def join_parts(parts: list[Path], spans: list[tuple[int, int]], video: dict,
               out: Path, workdir: Path, *, final_audio: bool = True) -> None:
    """The film from its parts: the pictures stream-copied end to end through the
    concat demuxer, each placed by its `duration` on the frame grid (its own
    microseconds, rounded off the cumulative time so nothing drifts), and the sound
    re-joined as one stream — each part's own, exactly its frames long — and encoded
    once. The parts' sound used to ride the concat too, and a part whose AAC ran a few
    ms past its picture pushed every later picture off the grid. The parts' sound is
    PCM, so this is the sound's one AAC encode — or, with `final_audio=False` (a later
    pass re-lays the sound), PCM again and no encode at all."""
    rate = rate_of(video["fps"])
    lines = []
    for p, (a, b) in zip(parts, spans):
        us = round(b * 1_000_000 / rate) - round(a * 1_000_000 / rate)
        lines.append(f"file '{p}'\nduration {us / 1e6:.6f}\n")
    listfile = workdir / "concat.txt"
    listfile.write_text("".join(lines), encoding="utf-8")
    inputs: list[str] = []
    graph: list[str] = []
    for k, (p, (a, b)) in enumerate(zip(parts, spans)):
        inputs += ["-i", str(p)]
        n = samples_at(b, rate) - samples_at(a, rate)
        graph.append(f"[{k + 1}:a]aresample={AUDIO_RATE},apad,atrim=end_sample={n},"
                     f"asetpts=PTS-STARTPTS[a{k}]")
    graph.append("".join(f"[a{k}]" for k in range(len(parts)))
                 + f"concat=n={len(parts)}:v=0:a=1[aout]")
    total = (spans[-1][1] - spans[0][0]) / rate if spans else 0
    r = run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "concat", "-safe", "0",
             "-i", str(listfile), *inputs, "-filter_complex", ";".join(graph),
             # the concat demuxer starts the picture at the parts' AAC priming
             # (0.021 s) after the sound; the first frame is the film's zero
             "-map", "0:v:0", "-map", "[aout]", "-c:v", "copy",
             "-bsf:v", "setts=pts=PTS-STARTPTS:dts=DTS-STARTPTS",
             *audio_codec(video, final_audio),
             "-t", f"{float(total):.6f}", "-write_tmcd", "0",
             "-movflags", "+faststart", str(out)])
    if r.returncode != 0:
        raise RuntimeError(f"concat failed: {r.stderr[-600:]}")


def tracks_pass(film: Path, edl: dict, segments: list[dict], parts: list[Path],
                video: dict, orient: str, footage: Path, generated_dir: Path | None,
                workdir: Path, film_s: float | None = None, *,
                final_audio: bool = True) -> list[str]:
    """The sequence's tracks over the concatenated film (roughcut/tracks.py), in ONE pass
    and only when the tracks ask for something the plain concat is not:

      * A1 — the camera sound — muted (or soloed out), at a gain, or with a shot's sound
        unlinked and moved (`a_off`): A1 is rebuilt from the parts' own audio (already
        levelled, retimed, carrying the effects' sounds), each delayed to its film
        second and mixed; muted, it is silence the film's length.
      * V2 — `overlays` — each clip cut like a part (the same conform) and composited
        over V1, full frame, from its film second for its length; picture only.
      * V1's eye off: V1 is black under whatever V2 holds.

    The video is copied when only the sound changed. A rebuilt A1 is encoded here, from
    the parts' PCM — the film's one AAC encode — unless `final_audio` is False (a bed
    goes under it next: PCM, and the bed's pass encodes). Returns the lines to print."""
    trk = edl.get("tracks") or {}
    plan = tracksmod.a1_plan(segments, trk, edits.dur)
    # the film's own length — whole frames on the grid (`frame_spans`) — not the sum of
    # the shots' seconds, which a `-t` would cut the last frame short of
    total = float(film_s) if film_s is not None else plan["total"]
    v1_eye = tracksmod.visible(trk, "V1")
    overlays = []
    if tracksmod.visible(trk, "V2"):
        for o in edl.get("overlays") or []:
            length = min(edits.dur(o), total - float(o["at"]))
            if length > 0.05:
                overlays.append((o, length))
    if not plan["needed"] and v1_eye and not overlays:
        return []
    said: list[str] = []
    inputs: list[str] = ["-i", str(film)]
    graph: list[str] = []
    n = 1
    vmap, vcodec = "0:v:0", ["-c:v", "copy"]
    if overlays or not v1_eye:
        base = "[0:v]"
        if not v1_eye:
            graph.append("[0:v]drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill[vb]")
            base = "[vb]"
            said.append("V1 hidden: black under V2")
        for j, (o, length) in enumerate(overlays):
            src = source_of(footage, generated_dir, o["clip"])
            if not src.exists():
                raise SystemExit(f"missing footage for V2: {src}")
            dest = workdir / f"v2_{j:03d}.mp4"
            normalise = None if edits.is_generated(o["clip"]) else colour.normalise_vf(
                shot_probe(src, None, {}))
            spd = edits.speed_of(o)
            cut(src, o["in"], min(o["out"], o["in"] + length * spd), 0.0, dest, orient,
                video, normalise=normalise, speed=spd,
                span=(0, max(1, round(length * rate_of(video["fps"])))))
            inputs += ["-i", str(dest)]
            at = float(o["at"])
            graph.append(f"[{n}:v]setpts=PTS-STARTPTS+{at:.3f}/TB[o{j}]")
            graph.append(f"{base}[o{j}]overlay=0:0:eof_action=pass:"
                         f"enable='between(t,{at:.3f},{at + length:.3f})'[v{j}]")
            base = f"[v{j}]"
            n += 1
            said.append(f"V2 {j:02d} {o['clip']} {o['in']:.1f}-{o['out']:.1f} over "
                        f"{at:.2f}-{at + length:.2f}s")
        graph.append(f"{base}format=yuv420p[vout]")
        vmap = "[vout]"
        vcodec = ["-c:v", "libx264", "-preset", video["preset"], "-crf", video["crf"],
                  "-fps_mode", "cfr", "-r", str(video["fps"]), *COLOUR_TAGS]
    amap, acodec = "0:a:0", ["-c:a", "copy"]
    if plan["needed"]:
        if plan["audible"]:
            first = n
            for part in parts:
                inputs += ["-i", str(part)]
                n += 1
            graph.append(tracksmod.a1_filter(len(parts), plan, first_input=first))
            moved = sum(1 for s in segments if abs(tracksmod.a_off(s)) > 1e-9)
            said.append(f"A1 rebuilt: {moved} shot(s) out of sync, gain {plan['gain_db']:+.1f} dB")
        else:
            graph.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{max(0.01, total):.3f}[a1]")
            said.append("A1 muted: the film's sound is silence")
        amap = "[a1]"
        acodec = audio_codec(video, final_audio)
    out = film.with_name(film.stem + ".tracks.mp4")
    cmd = ["ffmpeg", "-v", "error", "-y", "-nostdin", *inputs,
           "-filter_complex", ";".join(graph), "-map", vmap, "-map", amap, "-dn",
           *vcodec, *acodec, "-t", f"{max(0.01, total):.3f}",
           "-video_track_timescale", str(video["timescale"]), "-write_tmcd", "0",
           "-movflags", "+faststart", str(out)]
    r = run(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"tracks pass failed: {r.stderr[-600:]}")
    out.replace(film)
    return said


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


def source_of(footage: Path, generated_dir: Path | None, clip: str) -> Path:
    """Where a segment's clip is: a generated clip (INTAKE M13, `gen_*.mp4`) lives in
    `--generated-dir`, everything else in `--footage`."""
    if edits.is_generated(clip) and generated_dir is not None:
        return generated_dir / clip
    return footage / clip


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
    # Generated clips (a slide, a freeze frame) are made at a proxy's size and rate and
    # conform to whatever the footage is; they get no vote. A cut of nothing but
    # slides takes the preview profile's frame.
    clips = sorted({s["clip"] for s in segments if not edits.is_generated(s["clip"])})
    if not clips:
        if segments:
            return FPS, W, H
        raise SystemExit("delivery profile needs at least one segment to probe")
    probed = [probe_video(footage / c) for c in clips]
    fps = Counter(f for f, _, _ in probed).most_common(1)[0][0]
    w, h = Counter((w, h) for _, w, h in probed).most_common(1)[0][0]
    return fps, w, h


def delivery_resolution(native_w: int, native_h: int,
                        max_w: int = DELIVERY_MAX_W, max_h: int = DELIVERY_MAX_H) -> tuple[int, int]:
    """Capped at DELIVERY_MAX_W x DELIVERY_MAX_H, never upscaled past the bin's own
    native size. A source already at or below 4K (Killington is 3840x2160; the test
    suite's synthetic clips are 320x180) must render at its own resolution, not be
    stretched up to fill a bigger canvas that carries no more real detail."""
    scale = min(1.0, max_w / native_w, max_h / native_h)
    w = max(2, int(native_w * scale) // 2 * 2)      # even dims: yuv420p needs it
    h = max(2, int(native_h * scale) // 2 * 2)
    return w, h


def resolve_video_profile(name: str, footage: Path, segments: list[dict],
                          fps_override: str | None = None) -> dict:
    """The concrete {w, h, fps, preset, crf, audio_bitrate, timescale} for `cut()`.
    `fps_override` (`--fps`, the sequence's rate the board asks an export for) wins
    over the profile's own rate; the frame size is the profile's either way."""
    base = PROFILES[name]
    if name == "delivery":
        fps, native_w, native_h = bin_profile(footage, segments)
        w, h = delivery_resolution(native_w, native_h)
    elif name == "youtube":
        fps, native_w, native_h = bin_profile(footage, segments)
        w, h = delivery_resolution(native_w, native_h, YOUTUBE_MAX_W, YOUTUBE_MAX_H)
    elif name == "match":
        fps, native_w, native_h = bin_profile(footage, segments)
        w, h = delivery_resolution(native_w, native_h, MATCH_MAX_W, MATCH_MAX_H)
    else:
        fps, w, h = FPS, W, H
    if fps_override:
        fps = fps_text(rate_of(fps_override))
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


def describe_controls(c: dict) -> str:
    """One line for the render log: the shot's Effect Controls that are not default."""
    bits = []
    if c.get("position"):
        bits.append(f"position {c['position'][0]:.3f},{c['position'][1]:.3f}")
    for k, unit in (("scale", "%"), ("rotation", "°"), ("opacity", "%"),
                    ("volume_db", " dB"), ("contrast", ""), ("saturation", "")):
        if c.get(k) is not None:
            bits.append(f"{k} {c[k]:g}{unit}")
    if c.get("fx_off"):
        bits.append(f"fx off {len(c['fx_off'])}")
    if c.get("fx_order"):
        bits.append("fx order")
    return " · ".join(bits) or "defaults"


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
    ap.add_argument("--generated-dir", type=Path, default=None,
                    help="where the generated clips live (gen_*.mp4, INTAKE M13): a "
                         "segment whose clip is one reads its source here, ungraded")
    ap.add_argument("--parts-dir", type=Path, default=None,
                    help="write the per-segment parts here instead of a temp dir, so "
                         "a caller can count them as progress")
    ap.add_argument("--profile", choices=tuple(PROFILES), default="preview",
                    help="preview (default): today's fixed 1920x1080 @ 24000/1001, "
                         "veryfast/crf20 — what the board's quick versions use. "
                         "delivery: the bin's own majority frame rate and up to 4K "
                         "resolution (never upscaled), preset slow, crf 18. youtube: "
                         "the same capped at 1080p. match: the bin's own size and rate.")
    ap.add_argument("--fps", default=None,
                    help="the film's frame rate (`30000/1001`, `24`, `29.97`), over the "
                         "profile's own: the board passes the sequence's rate for an "
                         "export. The file is constant frame rate at it either way.")
    ap.add_argument("--progress", action="store_true",
                    help="write ffmpeg -progress beside each part (part_NNN.progress) "
                         "so a caller can read encoded seconds")
    args = ap.parse_args()
    global PART_PROGRESS
    PART_PROGRESS = args.progress

    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            print(f"error: {tool} not found on PATH", file=sys.stderr)
            return 2

    edl = json.loads(args.edl.read_text(encoding="utf-8"))
    segments = edl["segments"]
    orient = edl.get("orient", "auto")
    planned = sum(edits.dur(s) for s in segments)      # a shot at a speed is longer or shorter
    video = resolve_video_profile(args.profile, args.footage, segments, args.fps)
    rate = rate_of(video["fps"])
    spans = frame_spans(segments, rate)
    film_s = float(spans[-1][1] / rate) if spans else 0.0
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
        src = source_of(args.footage, args.generated_dir, s["clip"])
        if not src.exists():
            raise SystemExit(f"missing footage: {src}")
    # The grade, resolved for every shot before any part is cut: the reference shot
    # may sit later in the film than the shots that match to it.
    looks = colour.load_looks(args.assets)
    resolved = resolve_all(edl, segments, args.colour_dir, looks)
    probes: dict = {}
    try:
        for i, seg in enumerate(segments):
            generated = edits.is_generated(seg["clip"])
            src = source_of(args.footage, args.generated_dir, seg["clip"])
            speed = edits.speed_of(seg)
            gain = clip_gain(args.sidecars, seg["clip"]) if args.sidecars else 0.0
            dest = workdir / f"part_{i:03d}.mp4"
            res, clip_colour = resolved[i]
            probe = shot_probe(src, clip_colour, probes)
            # Normalise always, grade optionally: an HLG source is tone mapped to SDR
            # 709 whatever `colour.mode` says. After it the LUT's input is limited.
            # A generated clip (INTAKE M13) is neither: it was made SDR at a proxy's
            # size, has no colour file and takes no grade — a black slide stays the
            # black it was asked for, a freeze frame the frame the monitor showed.
            normalise = None if generated else colour.normalise_vf(probe)
            lut = None
            if not generated and not res["identity"]:
                cube = colour.write_cube(colour.cube_table(res["fn"], 33),
                                         workdir / f"part_{i:03d}.cube",
                                         title=f"roughcut {seg.get('id') or i}")
                lut = colour.lut_vf(cube, colour.in_range(probe))
            # The shot's accepted effects (INTAKE M12) fold into this same encode;
            # an event outside the shot's range is simply not drawn. In a retimed
            # part (INTAKE M13) an event's `t_part` moves with the picture.
            # Effect Controls' stack decides which are drawn and in what order.
            shot_effects = [(e, retimed_events(fx.events_in_shot(e, seg), speed))
                            for e in ordered_effects(edl, seg)]
            shot_effects = [(e, evs) for e, evs in shot_effects if evs]
            controls = seg.get("controls") or None
            encode_part(src, seg["in"], seg["out"], gain, dest, orient, video,
                        normalise=normalise, lut=lut, speed=speed, controls=controls,
                        shot_effects=shot_effects or None,
                        workdir=workdir / f"part_{i:03d}_fx", span=spans[i])
            assert_no_rotation(dest, orient)        # catch it at the part, not the film
            actual = probe_duration(dest)
            parts.append(dest)
            rate = f" ×{speed:g}" if abs(speed - 1.0) > 1e-9 else ""
            print(f"  {i:02d} {seg['clip']} {seg['in']:6.1f}-{seg['out']:6.1f}{rate} "
                  f"({actual:5.2f}s, {gain:+5.1f}dB)  {(seg.get('why') or '')[:56]}")
            print(f"      colour: {'generated — as made' if generated else describe_colour(res, normalise, probe)}")
            if controls:
                print(f"      controls: {describe_controls(controls)}")
            for e, evs in shot_effects:
                print(f"      fx: {e.get('name', 'effect')} × {len(evs)} at "
                      + ", ".join(f"{ev['t_part']:.2f}s" for ev in evs))

        # Film-wide effects run after the picture is assembled: the video stream is
        # copied through, so the bed costs no generation of quality. Resolved before
        # the join because it decides which pass encodes the sound (`AUDIO_PCM`).
        music = (edl.get("effects_music") or {}) if isinstance(edl, dict) else {}
        if args.music:
            music = {**music, "asset": str(args.music)}
        # A2 is the bed (roughcut/tracks.py): muted or soloed out, there is no bed; its
        # gain is added to the bed's own level.
        a2 = tracksmod.resolve_tracks(edl.get("tracks"))["A2"]
        if music.get("asset") and not tracksmod.audible(edl.get("tracks") or {}, "A2"):
            print("music: A2 is muted — no bed")
            music = {}
        elif music.get("asset") and abs(float(a2["gain_db"] or 0)) > 1e-9:
            spec = effects.resolve_gain(effects.music_spec(music),
                                        effects.resolve_asset(music["asset"], args.assets))
            g = float(spec["gain_db"]) + float(a2["gain_db"])
            music = {**music, "gain_db": round(max(effects.MIN_GAIN_DB,
                                                   min(effects.MAX_GAIN_DB, g)), 2)}
        # Who encodes the sound, once: the bed's pass, else the tracks pass when it
        # re-lays A1 from the parts, else the join.
        relays_a1 = tracksmod.a1_plan(segments, edl.get("tracks") or {}, edits.dur)["needed"]
        encoder = "music" if music.get("asset") else "tracks" if relays_a1 else "join"

        args.out.parent.mkdir(parents=True, exist_ok=True)
        join_parts(parts, spans, video, args.out, workdir, final_audio=encoder == "join")

        assert_no_rotation(args.out, orient)

        # The tracks (roughcut/tracks.py): A1's mute / gain / unlinked offsets, V2 over
        # V1, V1's eye — one pass, only when one of them is not the default.
        for line in tracks_pass(args.out, edl, segments, parts, video, orient,
                                args.footage, args.generated_dir, workdir, film_s,
                                final_audio=encoder == "tracks"):
            print(f"tracks: {line}")
        assert_no_rotation(args.out, orient)

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
