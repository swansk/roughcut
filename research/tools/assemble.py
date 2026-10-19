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
and applied with `lut3d` inside the part's own `-vf` — or, when the grade is a balance
alone, as three 1D tables (`colour.balance_lutrgb`, no cube) — so it costs no
generation of quality. An HDR source (iPhone HLG) is tone mapped to SDR 709 *before* the scale,
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
import concurrent.futures
import functools
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from fractions import Fraction
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import colour, edits, effects, fx    # noqa: E402  (after sys.path)
from roughcut import tracks as tracksmod           # noqa: E402
from roughcut import vfx as vfxmod                 # noqa: E402

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
    # `nvenc_cq`: the NVENC quality that stands in for the x264 setting (see `video_codec`)
    "preview": {"preset": "veryfast", "crf": "20", "audio_bitrate": "192k", "nvenc_cq": 27},
    "delivery": {"preset": "slow", "crf": "18", "audio_bitrate": "256k"},
    # The Export dialog's YouTube 1080p (REDESIGN-GOAL dimension 8): the bin's own frame
    # rate like `delivery` (YouTube wants the native rate), capped at 1920x1080 and
    # never upscaled, with YouTube's recommended 384 kbps stereo AAC.
    "youtube": {"preset": "medium", "crf": "18", "audio_bitrate": "384k", "nvenc_cq": 25},
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


# ------------------------------------------------- the GPU (verdict 6, punch 7)
#
# A 4-minute Quick export took 17 minutes, and most of it was not the encoder: decoding
# 4K GoPro HEVC and scaling it to 1080p on the CPU is ~4x real time on a busy box
# (measured, 6 s of CLIP_01 at load 25: 25.3 s in software, 14.7 s with NVDEC and the
# CPU scale, 2.9 s with NVDEC + `scale_cuda`). So a part's source is decoded and scaled
# on the GPU when it can be (`gpu_input`), and the Quick and YouTube presets encode with
# NVENC (`video_codec`). Neither changes what the film is:
#   * the decode is bit-exact either way; the GPU path states what the CPU path did
#     implicitly — ffmpeg skips autorotate on hardware frames, so a 180° camera gets its
#     `hflip,vflip` here, and a full-range (`yuvj`) source is put in limited range by
#     the LUT chain's explicit ranges, or by `FULL_TO_LIMITED` when it has no LUT;
#   * NVENC runs at the CQ that measured no worse than the x264 setting it stands in
#     for (1080p59.94 slices of CLIP_01 and CLIP_06 against a lossless conform, SSIM /
#     PSNR): Quick's veryfast crf 20 = 0.982 / 42.7 dB at 10-14 Mbps, NVENC p5 cq 27 =
#     0.984 / 43.4 dB at 12-17 Mbps; YouTube's medium crf 18 = 0.987 / 44.7 dB at
#     16-20 Mbps, NVENC cq 25 = 0.987 / 44.6-45.1 dB at 16-22 Mbps. Match Sequence and
#     Master stay on x264 (crf 18 at medium / slow): the quality presets.
# Anything the GPU path cannot take (HDR to tone map, a codec or pixel format NVDEC does
# not decode, a 90° rotation, no CUDA) takes the CPU path; a part whose GPU decode fails
# is cut again on the CPU; and NVENC failing for good re-cuts the film with x264, because
# every part of one film must come from one encoder (the join stream-copies them, and the
# MP4 carries the first part's SPS). `ROUGHCUT_GPU=0` turns all of it off.
NVENC_CODEC = "h264_nvenc"
GPU_CODECS = {"h264", "hevc"}
GPU_PIX = {"yuv420p", "yuvj420p", "nv12", "yuv420p10le", "p010le"}
# full-range 8-bit → limited, as swscale's yuvj420p → yuv420p does (rounded), and the
# frames re-tagged limited: a frame still tagged full is converted AGAIN by the next
# filter that negotiates (measured: `overlay` took a V2 clip 4.7 code values darker)
FULL_TO_LIMITED = ("lutyuv=y='16+val*219/255+0.5':u='128+(val-128)*224/255+0.5':"
                   "v='128+(val-128)*224/255+0.5',setparams=range=tv")


def gpu_allowed() -> bool:
    return os.environ.get("ROUGHCUT_GPU", "").strip().lower() not in ("0", "off", "no", "false")


@functools.lru_cache(maxsize=None)
def nvenc_ok() -> bool:
    """NVENC encodes here (probed once: a fifth of a second of black)."""
    if not gpu_allowed():
        return False
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                            "color=black:s=320x180:r=30:d=0.2", "-c:v", NVENC_CODEC,
                            "-f", "null", "-"], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


@functools.lru_cache(maxsize=None)
def cuda_ok() -> bool:
    """CUDA frames upload, scale and download here (probed once)."""
    if not gpu_allowed():
        return False
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-init_hw_device", "cuda=gpu",
                            "-filter_hw_device", "gpu", "-f", "lavfi", "-i",
                            "color=black:s=320x180:r=30:d=0.1", "-vf",
                            "format=nv12,hwupload,scale_cuda=160:90:format=yuv420p,"
                            "hwdownload,format=yuv420p", "-f", "null", "-"],
                           capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


_DECODE_FACTS: dict[str, dict] = {}


def decode_facts(src: Path) -> dict:
    """codec, pixel format, range and rotation of a source's first video stream."""
    key = str(src)
    if key not in _DECODE_FACTS:
        r = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "stream=codec_name,pix_fmt,color_range:stream_side_data=rotation",
                 "-of", "json", key])
        try:
            st = (json.loads(r.stdout or "{}").get("streams") or [{}])[0]
        except ValueError:
            st = {}
        rot = 0
        for sd in st.get("side_data_list") or []:
            if "rotation" in sd:
                rot = int(round(float(sd["rotation"])))
        pix = st.get("pix_fmt") or ""
        _DECODE_FACTS[key] = {
            "codec": st.get("codec_name"), "pix_fmt": pix, "rotation": rot,
            "full": st.get("color_range") == "pc" or pix.startswith("yuvj")}
    return _DECODE_FACTS[key]


def gpu_input(src: Path, orient: str, normalise: str | None, video: dict) -> dict | None:
    """How this source decodes on the GPU — `{"flip": 180° to undo, "full": full range}`
    — or None for the CPU path."""
    if not video.get("gpu_decode") or normalise:
        return None
    f = decode_facts(src)
    if f["codec"] not in GPU_CODECS or f["pix_fmt"] not in GPU_PIX:
        return None
    rot = 0 if orient == "none" else f["rotation"] % 360
    if rot not in (0, 180):
        return None
    return {"flip": rot == 180, "full": f["full"]}


def input_args(src: Path, orient: str, gpu: dict | None) -> list[str]:
    """The flags before a source's `-i`: the GPU decode, and `-display_rotation 0` under
    `orient: none` — and always on the GPU path, which applies the rotation itself."""
    out = []
    if gpu:
        out += ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
    if orient == "none" or gpu:
        out += ["-display_rotation", "0"]           # must precede -i; see module docstring
    return out


def conform_chain(video: dict, gpu: dict | None, normalise: str | None,
                  speed: float = 1.0, restart: bool = False) -> list[str]:
    """Normalise (CPU path), the retime, then the scale / pad / fps conform. On the GPU
    path the scale is `scale_cuda` straight off the decoder, then the frames come down."""
    w, h = video["w"], video["h"]
    # An export's own Frame Size (`--size`, verdict 9 punch 5) is scaled with Lanczos, on
    # the GPU and the CPU alike: a 4K GoPro frame taken to 1280×720 is a 3× reduction, and
    # Lanczos keeps the detail bicubic softens without the aliasing a cheaper kernel leaves
    # (Premiere's "Maximum Render Quality" for a frame-size change). The presets' own
    # frames keep the bicubic they always had, so no existing export changes by a pixel.
    algo = video.get("scaler") or "bicubic"
    chain = []
    if gpu:
        chain += [f"scale_cuda={w}:{h}:force_original_aspect_ratio=decrease:"
                  f"format=yuv420p:interp_algo={algo}", "hwdownload", "format=yuv420p"]
        if gpu["flip"]:
            chain += ["hflip", "vflip"]
    elif normalise:
        chain.append(normalise)
    if abs(speed - 1.0) > 1e-9:
        chain.append(f"setpts=(PTS-STARTPTS)/{speed:g}")
    elif restart:
        chain.append("setpts=PTS-STARTPTS")
    if not gpu:
        chain.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease"
                     + (f":flags={algo}" if video.get("scaler") else ""))
    chain += [f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2", f"fps={video['fps']}"]
    return chain


# A target bitrate (`--bitrate`, the Export dialog's Bitrate; verdict 9 punch 5) is
# Premiere's "VBR, 1 pass": the encoder aims at the target over each part, peaks capped at
# 1.5× it and the buffer two seconds of it, so the file's size is the target times its
# length (± what the rate control lands). Without one, the profile's quality (crf / cq).
BITRATE_PEAK = 1.5
BITRATE_BUFFER_S = 2.0
BITRATE_MIN_KBPS, BITRATE_MAX_KBPS = 100, 400_000


def video_codec(video: dict) -> list[str]:
    """The part's video encoder: NVENC at the profile's measured CQ (a keyframe at least
    every 250 frames, as x264's default and the export's size model assume), or x264;
    at a target bitrate (`bitrate_kbps`) either one aims at it instead of a quality."""
    kbps = video.get("bitrate_kbps")
    rate = ([f"{kbps}k", "-maxrate", f"{round(kbps * BITRATE_PEAK)}k",
             "-bufsize", f"{round(kbps * BITRATE_BUFFER_S)}k"] if kbps else None)
    if video.get("encoder") == "nvenc":
        quality = (["-b:v", *rate] if rate else
                   ["-cq", str(video["nvenc_cq"]), "-b:v", "0", "-maxrate", "200M",
                    "-bufsize", "400M"])
        return ["-c:v", NVENC_CODEC, "-preset", "p5", "-tune", "hq", "-rc", "vbr",
                *quality, "-spatial-aq", "1", "-g", "250", "-bf", "3",
                "-b_ref_mode", "middle", "-profile:v", "high"]
    if rate:
        return ["-c:v", "libx264", "-preset", video["preset"], "-b:v", *rate]
    return ["-c:v", "libx264", "-preset", video["preset"], "-crf", video["crf"]]


def render_jobs(asked: int | None = None) -> int:
    """Parts encoded at once: `--jobs`, else `ROUGHCUT_RENDER_JOBS`, else half the cores.
    (The export's size sample asks for all its slices at once: each is a quarter second,
    so what it waits on is each ffmpeg's start and seek, not the cores.)"""
    try:
        n = asked or int(os.environ.get("ROUGHCUT_RENDER_JOBS", "0"))
    except ValueError:
        n = 0
    return max(1, n or (os.cpu_count() or 2) // 2)


class EncoderLost(RuntimeError):
    """NVENC failed a part twice: the film is re-cut with x264."""


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


# ------------------------------------------------------ keyframes (verdict 5, punch 4)
#
# The schema (judge/LOOP.md, wave 6; lane KEYUI writes it): `controls.keys = {prop:
# [[t, v], ...]}`, t = seconds from the shot's start in FILM time (after its speed),
# sorted, linear between keys and held outside them. A keyed property's keys win over
# its static value. Inside a part every filter sees film time from the shot's start:
# the picture after the retime and the `fps` conform starts at frame 0 = 0 s, and the
# sound after `atempo` and `aresample=first_pts=0` at sample 0 = 0 s.

KEYED_PROPS = ("position_x", "position_y", "scale", "rotation", "opacity", "volume_db",
               # Lumetri › Vignette (wave 15, VIGNETTE): `vignette_graph`
               *colour.VIGNETTE_FIELDS)


def keys_of(controls: dict | None, prop: str) -> list[tuple[float, float]] | None:
    """The property's keys as sorted (t, v) pairs, or None when it is not keyed."""
    raw = ((controls or {}).get("keys") or {}).get(prop)
    if not raw:
        return None
    pts = sorted(((float(t), float(v)) for t, v in raw), key=lambda p: p[0])   # stable: a step keeps its order
    return pts or None


def value_at(keys: list[tuple[float, float]], t: float) -> float:
    """The keyed value at film time `t` (seconds from the shot's start): linear between
    the two keys either side, the first key's value before it, the last's after it."""
    if t < keys[0][0]:
        return keys[0][1]
    # the last key at or before t (of two keys at one instant, the later one: a step)
    i = max(k for k, (tk, _) in enumerate(keys) if tk <= t)
    if i == len(keys) - 1:
        return keys[-1][1]
    (t0, v0), (t1, v1) = keys[i], keys[i + 1]
    return v0 + (v1 - v0) * (t - t0) / (t1 - t0)


def lerp_expr(keys: list[tuple[float, float]], tvar: str) -> str:
    """`value_at` as an ffmpeg expression of `tvar`: the first value plus, per pair of
    keys, its rise times `clip((t - t0) / (t1 - t0), 0, 1)` — which is the held value
    outside the keys and the straight line between them."""
    terms = [f"{keys[0][1]:.6g}"]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if abs(v1 - v0) < 1e-12:
            continue
        if t1 - t0 < 1e-9:
            terms.append(f"({v1 - v0:.6g})*gte({tvar},{t1:.6g})")
        else:
            terms.append(f"({v1 - v0:.6g})*clip(({tvar}-{t0:.6g})/{t1 - t0:.6g},0,1)")
    return "(" + "+".join(terms) + ")"


def static_prop(controls: dict | None, prop: str) -> float:
    """A keyable property's static (un-keyed) value, in the keys' own units."""
    c = controls or {}
    pos = c.get("position") or [0.5, 0.5]
    if prop in colour.VIGNETTE_FIELDS:
        return ctl_num(c, prop, colour.VIGNETTE_FIELDS[prop][2])
    return {"position_x": float(pos[0]), "position_y": float(pos[1]),
            "scale": ctl_num(c, "scale", 100.0), "rotation": ctl_num(c, "rotation", 0.0),
            "opacity": ctl_num(c, "opacity", 100.0),
            "volume_db": ctl_num(c, "volume_db", 0.0)}[prop]


def held_value(keys: list[tuple[float, float]], a: float, b: float) -> float | None:
    """The keys' value when it is the same at every instant of [a, b], else None. The
    keys are linear between them and held outside, so the value is constant there exactly
    when it is the same at both ends, at every key inside and on both sides of a step (two
    keys at one instant): those are the only places it can turn."""
    vals = [value_at(keys, a), value_at(keys, b)]
    vals += [v for t, v in keys if a <= t <= b]
    vals += [value_at(keys, t) for t, _ in keys if a < t < b]
    return vals[0] if max(vals) - min(vals) < 1e-9 else None


def held_controls(controls: dict | None, a: float, b: float) -> dict | None:
    """`controls` with every keyed property whose value never changes over the clip's
    times [a, b] (one key — a single click on a stopwatch —, keys of equal value, keys
    that all lie before or after the part) replaced by that value as the static control
    (verdict 7, blocker 2: one Opacity key wrote an empty `sendcmd` file and ffmpeg
    refused the graph, so every export failed). The render then draws it exactly as the
    static control, with no per-frame warp or command file. A property that changes
    inside [a, b] stays keyed. Returns `controls` itself when nothing is held (the EDL's
    own dict is never modified)."""
    keys = (controls or {}).get("keys") or {}
    held = {}
    for prop in KEYED_PROPS:
        k = keys_of(controls, prop)
        if k:
            v = held_value(k, a, b)
            if v is not None:
                held[prop] = v
    if not held:
        return controls
    out = {kk: vv for kk, vv in controls.items() if kk != "keys"}
    rest = {p: v for p, v in keys.items() if p not in held}
    if rest:
        out["keys"] = rest
    if "position_x" in held or "position_y" in held:
        pos = list(out.get("position") or [0.5, 0.5])
        pos[0] = held.get("position_x", pos[0])
        pos[1] = held.get("position_y", pos[1])
        out["position"] = pos
    for prop in ("scale", "rotation", "opacity", "volume_db", *colour.VIGNETTE_FIELDS):
        if prop in held:
            out[prop] = held[prop]
    return out


def prop_expr(controls: dict | None, prop: str, tvar: str) -> str:
    keys = keys_of(controls, prop)
    return lerp_expr(keys, tvar) if keys else f"{static_prop(controls, prop):.6g}"


# ------------------------------------------------- Lumetri tone (verdict 5, punch 4)

TONE_PROPS = ("highlights", "shadows", "whites", "blacks")


def tone_curve(x: float, highlights: float = 0.0, shadows: float = 0.0,
               whites: float = 0.0, blacks: float = 0.0) -> float:
    """Lumetri Basic's Highlights / Shadows / Whites / Blacks, per channel: THE formula,
    the same as grade.js's shader (lane KEYUI, wave 6). `x` is one RGB channel on 0..1
    after the LUT and Contrast; every slider is -100..100 and 0 is off. With b, s, h, w =
    blacks, shadows, highlights, whites / 100:

        x = clamp(x, 0, 1)
        y = x + 0.25·b·(1-x)³ + 1.5·s·x·(1-x)² + 1.5·h·x²·(1-x) + 0.25·w·x³

    so shadows ±100 moves a channel at 1/3 by ±0.222 and highlights ±100 one at 2/3 by
    ±0.222, leaving 0 and 1 where they were; blacks ±100 moves 0 by ±0.25 and whites ±100
    moves 1 by ±0.25. Not clamped here: Saturation follows, then the clamp to 0..1. With
    all four at 0 the step (and its clamp) is skipped, as the shader skips it."""
    x = min(1.0, max(0.0, float(x)))
    b, s = blacks / 100.0, shadows / 100.0
    h, w = highlights / 100.0, whites / 100.0
    ix = 1.0 - x
    return (x + 0.25 * b * ix ** 3 + 1.5 * s * x * ix * ix + 1.5 * h * x * x * ix
            + 0.25 * w * x ** 3)


def tone_of(controls: dict | None) -> dict[str, float] | None:
    tone = {p: ctl_num(controls, p, 0.0) for p in TONE_PROPS}
    return None if all(abs(v) < 1e-9 for v in tone.values()) else tone


def grade_channel(c: float, controls: dict | None) -> float:
    """One RGB channel (0..1, after the LUT) through grade.js's per-channel steps:
    Contrast `(c - 0.5)·(1 + contrast/200) + 0.5`, then `tone_curve` when a tone slider
    is set. Saturation (a mix across channels) comes after it."""
    y = (c - 0.5) * (1.0 + ctl_num(controls, "contrast", 0.0) / 200.0) + 0.5
    tone = tone_of(controls)
    return tone_curve(y, **tone) if tone else y


def channel_table(controls: dict | None) -> list[int]:
    """`grade_channel` for every 8-bit code value, rounded and clipped to 0..255."""
    return [int(min(255, max(0, math.floor(255.0 * grade_channel(v / 255.0, controls) + 0.5))))
            for v in range(256)]


def table_expr(table: list[int], var: str = "val") -> str:
    """A 256-entry table as one ffmpeg expression: a balanced tree of `if(lt(val, m),
    left, right)`, a run of equal entries collapsed to its value."""
    def tree(lo: int, hi: int) -> str:
        if all(v == table[lo] for v in table[lo:hi + 1]):
            return str(table[lo])
        mid = (lo + hi + 1) // 2
        return f"if(lt({var},{mid}),{tree(lo, mid - 1)},{tree(mid, hi)})"
    return tree(0, len(table) - 1)


REC709 = (0.2126, 0.7152, 0.0722)


def saturation_mixer(sat: float) -> str | None:
    """grade.js's `mix(luma709(y), y, sat)` as a `colorchannelmixer` matrix."""
    if abs(sat - 1.0) < 1e-9:
        return None
    rows = []
    for i, ch in enumerate("rgb"):
        for j, src in enumerate("rgb"):
            rows.append(f"{ch}{src}={(1 - sat) * REC709[j] + (sat if i == j else 0):.6g}")
    return "colorchannelmixer=" + ":".join(rows)


def adjust_vf(controls: dict | None) -> str | None:
    """Lumetri contrast, tone and saturation, after the LUT, as grade.js's shader does
    them in RGB: `y = (c - 0.5) * (1 + contrast/200) + 0.5`, the tone curve when a tone
    slider is set (`tone_curve`), then a mix toward Rec.709 luma by saturation/100.

    Without tone, in the part's limited-range BT.709 YUV that is exactly: luma about its
    middle (125.5) times k, and both chroma about 128 times k × s (a per-channel contrast
    about 0.5 scales R-Y and B-Y by k; the mix toward luma keeps luma and scales them by
    s), clipped to the legal range as the shader clamps: one `lutyuv`. The tone curve is
    per channel and not linear, so with it the picture goes to RGB (BT.709, limited in),
    through one `lutrgb` table of `grade_channel` and a `colorchannelmixer` saturation,
    and back to limited BT.709 4:2:0. (8-bit RGB clamps a channel the tone pushed past
    1 before the saturation mix; the shader clamps only after it.)"""
    k = 1.0 + ctl_num(controls, "contrast", 0.0) / 200.0
    sat = ctl_num(controls, "saturation", 100.0) / 100.0
    creative = creative_vf(controls)
    if tone_of(controls) or creative:
        chain = ["scale=in_range=tv:in_color_matrix=bt709:flags=accurate_rnd+full_chroma_int",
                 "format=gbrp"]
        if tone_of(controls) or abs(k - 1.0) > 1e-9:
            t = table_expr(channel_table(controls))
            chain.append(f"lutrgb=r='{t}':g='{t}':b='{t}'")
        mix = saturation_mixer(sat)
        if mix:
            chain.append(mix)
        chain += creative
        chain += ["scale=out_range=tv:out_color_matrix=bt709:flags=accurate_rnd",
                  "format=yuv420p"]
        return ",".join(chain)
    if abs(k - 1.0) < 1e-9 and abs(sat - 1.0) < 1e-9:
        return None
    ks = k * sat
    return (f"lutyuv=y='clip((val-125.5)*{k:.6g}+125.5,16,235)'"
            f":u='clip((val-128)*{ks:.6g}+128,16,240)'"
            f":v='clip((val-128)*{ks:.6g}+128,16,240)'")


# ------------------------------------- Lumetri beyond Basic (redesign wave 11, LUMETRI)
#
# Curves, Color Wheels and HSL Secondary, after Basic Correction's saturation, as
# grade.js's shader draws them; THE formula is `roughcut/colour.py`'s (its comment block
# "Lumetri beyond Basic"). Curves and wheels are a function of each channel alone: one
# `lutrgb` of `colour.creative_table` (three tables, one per channel, 8-bit as the tone's
# is). The HSL Secondary mixes channels: a 33³ cube of `colour.hsl_secondary` through a
# tetrahedral `lut3d`, written once per key into HSL_CUBE_DIR (content-addressed, so a
# re-render and every part with the same key share it).

HSL_CUBE_DIR = Path(tempfile.gettempdir()) / "roughcut-hsl"


def creative_lutrgb(controls: dict | None) -> str | None:
    """Curves then wheels as one `lutrgb`, or None when neither is set."""
    tab = colour.creative_table(controls, 256)
    if tab is None:
        return None
    codes = [[int(min(255, max(0, math.floor(255.0 * v + 0.5)))) for v in row] for row in tab]
    return "lutrgb=" + ":".join(f"{c}='{table_expr(t)}'" for c, t in zip("rgb", codes))


def hsl_cube(hsl: dict) -> Path:
    """The HSL Secondary as a 33³ `.cube`, written once per key."""
    import hashlib
    key = hashlib.sha1(json.dumps(hsl, sort_keys=True).encode()).hexdigest()[:16]
    path = HSL_CUBE_DIR / f"hsl-{key}.cube"
    if not path.exists():
        HSL_CUBE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        colour.write_cube(colour.cube_table(lambda x: colour.hsl_secondary(x, hsl), 33), tmp,
                          title="roughcut hsl secondary")
        os.replace(tmp, path)
    return path


def creative_vf(controls: dict | None) -> list[str]:
    """The filters of Lumetri's Curves, Color Wheels and HSL Secondary on 8-bit planar
    RGB, in that order; [] when none is set."""
    out = []
    lr = creative_lutrgb(controls)
    if lr:
        out.append(lr)
    hsl = colour.hsl_of((controls or {}).get("hsl"))
    if hsl:
        out.append(f"lut3d=file={hsl_cube(hsl)}:interp=tetrahedral")
    return out


# ------------------------------------------- Lumetri › Vignette (redesign wave 15, VIGNETTE)
#
# THE formula is roughcut/colour.py's ("Lumetri › Vignette"); grade.js's shader is its
# twin. The export applies the formula's last line in Y'CbCr, where darkening or
# lightening an R'G'B' pixel by s is the same affine map of its Y'CbCr —
# Y' = 16 + (Y − 16)(1 − s) + 219·s·[Amount > 0], C' = 128 + (C − 128)(1 − s) — so the
# picture never leaves 4:2:0 for it: one `lut2` of the picture and a mask frame (a table
# of 256 × 256 per plane, built once), the mask's Y at each pixel and its Cb = Cr the mean
# of each 2 × 2. The usual vignette's shape is still: its mask is the weight w itself
# (255·w), written once per frame size and shape under VIGNETTE_DIR (content-addressed;
# lut2 holds the mask's one frame for the whole part), and the Amount is in lut2's
# expressions — re-set on the frames where a keyed Amount changes (`sendcmd`, the table
# rebuilt). A keyed shape (Midpoint, Roundness or Feather changing over the part) is a
# mask per frame instead, signed (128 + 127·(Amount/5)·w), drawn at a quarter of the frame
# (at most VIGNETTE_KEYED_MAX_W wide) beside the part and scaled up: the mask is smooth,
# a feather of 0 is the one place the scale softens it, by a few pixels. It runs on the
# graded picture (Lumetri's last step) before the video effects, the designed effects and
# Motion, as Premiere's Lumetri Color sits above the effects below it in the stack.

VIGNETTE_DIR = Path(tempfile.gettempdir()) / "roughcut-vignette"
VIGNETTE_KEYED_DIV, VIGNETTE_KEYED_MAX_W = 4, 480
VIGNETTE_SHAPE = colour.VIGNETTE_FIELDS.keys() - {"vignette_amount"}


def vignette_at(controls: dict | None, t: float) -> tuple[float, float, float, float]:
    """(amount, midpoint, roundness, feather) at film time `t` from the shot's start:
    each one's keys when it has them, else its static value."""
    return tuple(value_at(keys_of(controls, k), t) if keys_of(controls, k) else static_prop(controls, k)
                 for k in colour.VIGNETTE_FIELDS)


def _mask_planes(code: np.ndarray) -> bytes:
    """A mask's codes (0..255 floats, H × W, both even) as one Y'CbCr 4:2:0 frame."""
    y = np.clip(np.floor(code + 0.5), 0, 255).astype(np.uint8)
    hh, ww = code.shape
    c = code.reshape(hh // 2, 2, ww // 2, 2).mean(axis=(1, 3))
    c = np.clip(np.floor(c + 0.5), 0, 255).astype(np.uint8)
    return y.tobytes() + c.tobytes() + c.tobytes()


def _y4m_head(w: int, h: int, rate: Fraction) -> bytes:
    return f"YUV4MPEG2 W{w} H{h} F{rate.numerator}:{rate.denominator} Ip A1:1 C420mpeg2\n".encode()


def vignette_still(w: int, h: int, shape: tuple[float, float, float]) -> Path:
    """The weight 255·w of a still shape (midpoint, roundness, feather) at w × h: one mask
    frame, written once per frame size and shape."""
    import hashlib
    key = hashlib.sha1(json.dumps([w, h, *[round(v, 6) for v in shape]]).encode()).hexdigest()[:16]
    path = VIGNETTE_DIR / f"vig-{key}.y4m"
    if not path.exists():
        VIGNETTE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_bytes(_y4m_head(w, h, Fraction(1)) + b"FRAME\n"
                        + _mask_planes(255.0 * colour.vignette_mask(w, h, *shape)))
        os.replace(tmp, path)
    return path


def vignette_exprs(amount: float) -> tuple[str, str]:
    """lut2's Y and C expressions of the picture x and the mask y = 255·w for an Amount
    (no commas: `sendcmd` takes them as they are)."""
    k = abs(amount) / 5.0
    lift = 219.0 * k if amount > 0 else 0.0
    return (f"16.5+(x-16)*(1-{k:.6g}*y/255)+{lift:.6g}*y/255",
            f"128.5+(x-128)*(1-{k:.6g}*y/255)")


def vignette_graph(controls: dict | None, w: int, h: int, rate: Fraction, frames: int,
                   vin: str, vout: str, tag: str, dest: Path | None) -> str | None:
    """Lumetri › Vignette from `[vin]` to `[vout]` (Y'CbCr 4:2:0), or None when it draws
    nothing. A keyed one writes its commands (a keyed Amount) or its masks (a keyed
    shape) beside `dest`."""
    c = held_controls(controls, 0.0, (max(1, frames) - 1) / float(rate))
    if not colour.vignette_on(c):
        return None
    per_frame = [vignette_at(c, n / float(rate)) for n in range(max(1, frames))]
    if all(abs(v[0]) < 1e-9 for v in per_frame):
        return None
    if not any(keys_of(c, k) for k in VIGNETTE_SHAPE):
        y_e, c_e = vignette_exprs(per_frame[0][0])
        src = f"movie=filename={filter_path(vignette_still(w, h, per_frame[0][1:]))}"
        cmd = ""
        if keys_of(c, "vignette_amount") and dest is not None:
            lines, last = [], round(per_frame[0][0], 4)
            for n, v in enumerate(per_frame[1:], 1):
                a = round(v[0], 4)
                if a == last:
                    continue
                ye, ce = vignette_exprs(a)
                lines.append(f"{(n - 0.5) / float(rate):.6f} [enter] lut2@{tag} c0 {ye}, "
                             f"[enter] lut2@{tag} c1 {ce}, [enter] lut2@{tag} c2 {ce};")
                last = a
            if lines:
                cf = dest.with_name(f"{dest.stem}.{tag}.cmd")
                cf.write_text("\n".join(lines) + "\n", encoding="utf-8")
                cmd = f"sendcmd=f={filter_path(cf)},"
        return (f"{src}[{tag}m];[{vin}]{cmd}null[{tag}p];"
                f"[{tag}p][{tag}m]lut2@{tag}=c0='{y_e}':c1='{c_e}':c2='{c_e}'[{vout}]")
    if dest is None:
        return None
    mw = max(2, min(w // VIGNETTE_KEYED_DIV, VIGNETTE_KEYED_MAX_W) // 2 * 2)
    mh = max(2, round(mw * h / w / 2) * 2)
    u = (2.0 * (np.arange(mw) + 0.5) / mw - 1.0)[None, :]
    v = (2.0 * (np.arange(mh) + 0.5) / mh - 1.0)[:, None]
    path = dest.with_name(f"{dest.stem}.{tag}.y4m")
    last, plane = None, b""
    with open(path, "wb") as fh:
        fh.write(_y4m_head(mw, mh, rate))
        for vals in per_frame:
            if vals != last:
                a, m, r, f = vals
                plane = _mask_planes(128.0 + 127.0 * (a / 5.0) * colour.vignette_weight(u, v, w / h, m, r, f))
                last = vals
            fh.write(b"FRAME\n" + plane)
    sig = "abs(y-128)/127"
    return (f"movie=filename={filter_path(path)},scale={w}:{h}:flags=bilinear,format=yuv420p[{tag}m];"
            f"[{vin}][{tag}m]lut2@{tag}=c0='16.5+(x-16)*(1-{sig})+219*gt(y,128)*(y-128)/127'"
            f":c1='128.5+(x-128)*(1-{sig})':c2='128.5+(x-128)*(1-{sig})'[{vout}]")


def vignette_describe(c: dict) -> str | None:
    """The render log's phrase for the shot's vignette, or None."""
    if not colour.vignette_on(c):
        return None
    vals = vignette_at(c, 0.0)
    keyed = [k.split("_", 1)[1] for k in colour.VIGNETTE_FIELDS if keys_of(c, k)]
    return ("vignette " + " ".join(f"{n} {v:g}" for n, v in zip(colour.VIGNETTE_PARTS, vals))
            + (f" (keyed {'/'.join(keyed)})" if keyed else ""))


def volume_db(controls: dict | None) -> float:
    """The shot's static Volume; 0 when it is keyed (`volume_vf` then carries it)."""
    if keys_of(controls, "volume_db"):
        return 0.0
    return ctl_num(controls, "volume_db", 0.0)


def volume_vf(controls: dict | None) -> str | None:
    """A keyed Volume: `volume` evaluated per audio frame at the film's `t`, the dB
    ramp as a gain (`pow(10, dB/20)`). Placed after the retime, so `t` is film time."""
    keys = keys_of(controls, "volume_db")
    if not keys:
        return None
    return f"volume=volume='pow(10,{lerp_expr(keys, 't')}/20)':eval=frame"


def dim_vf(opacity: float) -> str:
    """Opacity over black: in limited YUV, a straight blend toward (16, 128, 128)."""
    a = f"{opacity:.6g}"
    return f"lutyuv=y='16+(val-16)*{a}':u='128+(val-128)*{a}':v='128+(val-128)*{a}'"


def motion_graph(controls: dict | None, w: int, h: int, vin: str, vout: str,
                 tag: str = "mo") -> str | None:
    """Motion and Opacity as a filter-graph fragment from `[vin]` to `[vout]`, or None
    at the defaults (`tag` names its inner pads: a Transform effect's graph is a second
    one in the same part). The monitor's CSS is `translate((x-0.5)·W, (y-0.5)·H) rotate(r)
    scale(s)` about the picture's centre: a source point p lands at
    `q = (x·W, y·H) + s·R(r)·(p − centre)`. Only the part of the source that can land
    in the frame is cropped (so a 1000 % scale never builds a 19200-wide frame), scaled,
    rotated (corners black) and overlaid on black where its own centre lands."""
    lay = motion_layout(controls, w, h)
    if lay is None:
        return None
    if lay == "dim":
        return f"[{vin}]{dim_vf(motion_opacity(controls))}[{vout}]"
    if lay == "empty":
        return f"[{vin}]drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill[{vout}]"
    chain, ox, oy = lay
    op = motion_opacity(controls)
    if op < 1.0 - 1e-9:
        chain = chain + [dim_vf(op)]
    return (f"[{vin}]split[{tag}_c][{tag}_f];"
            f"[{tag}_c]drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill[{tag}_bg];"
            f"[{tag}_f]{','.join(chain)}[{tag}_fg];"
            f"[{tag}_bg][{tag}_fg]overlay=x={ox}:y={oy}:eof_action=pass,format=yuv420p[{vout}]")


def motion_opacity(controls: dict | None) -> float:
    return max(0.0, min(1.0, ctl_num(controls, "opacity", 100.0) / 100.0))


def motion_layout(controls: dict | None, w: int, h: int, fill: str = "black"):
    """Static Motion as pieces: None at the defaults, "dim" when only the Opacity is
    set, "empty" when nothing of the picture lands in the frame (scale 0, opacity 0),
    else `(chain, ox, oy)`: the crop / scale / rotate (corners `fill`) that makes the
    moved picture and where its top-left lands. `motion_graph` lays it on black (V1);
    `overlay_controls_graph` lays it, with alpha, over what is under a V2+ clip."""
    c = controls or {}
    pos = c.get("position") or [0.5, 0.5]
    px, py = float(pos[0]), float(pos[1])
    s = ctl_num(c, "scale", 100.0) / 100.0
    rot = ctl_num(c, "rotation", 0.0)
    op = motion_opacity(c)
    turned = abs(math.remainder(rot, 360.0)) > 1e-9
    moved = abs(px - 0.5) > 1e-9 or abs(py - 0.5) > 1e-9 or abs(s - 1.0) > 1e-9 or turned
    dim = op < 1.0 - 1e-9
    if not moved and not dim:
        return None
    if not moved:
        return "dim"
    if s <= 1e-6 or op <= 1e-6:
        return "empty"
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
        return "empty"                      # nothing of the picture lands in the frame
    cw = min(w - x0, (x1 - x0 + 1) // 2 * 2)
    ch = min(h - y0, (y1 - y0 + 1) // 2 * 2)
    sw = max(2, int(round(cw * s / 2)) * 2)
    sh = max(2, int(round(ch * s / 2)) * 2)
    chain = [f"crop={cw}:{ch}:{x0}:{y0}", f"scale={sw}:{sh}"]
    rw, rh = sw, sh
    if turned:
        rw = int(math.ceil((sw * abs(cos) + sh * abs(sin)) / 2)) * 2
        rh = int(math.ceil((sw * abs(sin) + sh * abs(cos)) / 2)) * 2
        chain.append(f"rotate={th:.9f}:ow={rw}:oh={rh}:c={fill}")
    # where the crop's own centre lands; the rotated box is centred on it
    ex, ey = x0 + cw / 2.0 - cx, y0 + ch / 2.0 - cy
    qx = tx + s * (cos * ex - sin * ey)
    qy = ty + s * (sin * ex + cos * ey)
    ox, oy = int(round(qx - rw / 2.0)), int(round(qy - rh / 2.0))
    return chain, ox, oy


MOTION_PROPS = ("position_x", "position_y", "scale", "rotation")
PERSPECTIVE_PAD = 8          # black border the warp samples from outside the picture


def keyed_motion_graph(controls: dict | None, w: int, h: int, rate: Fraction,
                       vin: str, vout: str, *, t0: float = 0.0, alpha: bool = False) -> str:
    """Motion with keyed properties, as one per-frame warp: the same transform as
    `motion_graph` — a source point p lands at `(x·W, y·H) + s·R(r)·(p − centre)` — with
    x, y, s and r each `lerp_expr` of the frame's film time (`in / rate`: `in` is the
    warp's input frame count, frame 0 the shot's first). `perspective` (sense=source)
    takes, for each output corner, the source point that lands there: the inverse
    transform of the corner. The picture gets an 8 px black border first, so a sample
    from outside it reads black (the warp clamps to the edge) and the border is cropped
    off after; the scale is held at >= 2 % so no coordinate overflows the warp's fixed
    point. `t0`: the clip's time at the warp's first frame (a V2+ piece that starts
    inside its clip). `alpha`: a V2+ clip's picture, laid over what is under it — the
    border and everything outside the picture transparent (yuva420p), not black."""
    P = PERSPECTIVE_PAD
    # `in` is 1 on the warp's first frame (ffmpeg counts the frame on its way in), so
    # film time is (in - 1) / rate; test_render_keyframes pins frame 0 to the first key
    T = f"((in-1)*{float(1 / rate):.12g}+{t0:.9g})" if t0 else f"((in-1)*{float(1 / rate):.12g})"
    px = prop_expr(controls, "position_x", T)
    py = prop_expr(controls, "position_y", T)
    sc = f"max({prop_expr(controls, 'scale', T)}/100,0.02)"
    th = f"({prop_expr(controls, 'rotation', T)}*PI/180)"
    cx, cy = w / 2.0, h / 2.0
    coords = []
    for k, (ox, oy) in enumerate(((0, 0), (w + 2 * P, 0), (0, h + 2 * P),
                                  (w + 2 * P, h + 2 * P))):
        # the warp's coordinates are pixel indices; the transform's are continuous (a
        # pixel's centre at i + 0.5): output index o is frame point o + 0.5 - P, and a
        # source point p is index p - 0.5 + P in the bordered picture
        dx = f"({ox + 0.5 - P:g}-{px}*{w})"
        dy = f"({oy + 0.5 - P:g}-{py}*{h})"
        sx = f"{cx + P - 0.5:g}+(cos({th})*{dx}+sin({th})*{dy})/{sc}"
        sy = f"{cy + P - 0.5:g}+(cos({th})*{dy}-sin({th})*{dx})/{sc}"
        coords.append(f"x{k}='{sx}':y{k}='{sy}'")
    if alpha:
        return (f"[{vin}]format=yuva420p,pad={w + 2 * P}:{h + 2 * P}:{P}:{P}:black@0,"
                f"perspective={':'.join(coords)}:interpolation=linear:sense=source:eval=frame,"
                f"crop={w}:{h}:{P}:{P},format=yuva420p[{vout}]")
    return (f"[{vin}]pad={w + 2 * P}:{h + 2 * P}:{P}:{P}:black,"
            f"perspective={':'.join(coords)}:interpolation=linear:sense=source:eval=frame,"
            f"crop={w}:{h}:{P}:{P},format=yuv420p[{vout}]")


def opacity_commands(keys: list[tuple[float, float]], frames: int, rate: Fraction,
                     dest: Path, target: str = "lutyuv@kop", *, alpha: bool = False,
                     t0: float = 0.0) -> tuple[str, bool]:
    """A keyed Opacity's per-frame values as a `sendcmd` file: the blend toward black
    (`dim_vf`'s maths) re-set on the frame where it changes, a half frame early so a
    timestamp's rounding never misses it. Returns the frame-0 opacity's `lutyuv` and
    whether there is any change to send: when there is none the file is not written (an
    empty `sendcmd` file is an error to ffmpeg — verdict 7, blocker 2) and the caller
    draws the `lutyuv` alone. `alpha` (a V2+ clip): the opacity scales the picture's
    alpha instead, so it mixes with what is under it. `t0`: the clip's time at frame 0."""
    def exprs(a: float) -> dict[str, str]:
        a = max(0.0, min(1.0, a))
        if alpha:
            return {"a": f"val*{a:.6g}"}
        return {"y": f"16+(val-16)*{a:.6g}", "u": f"128+(val-128)*{a:.6g}",
                "v": f"128+(val-128)*{a:.6g}"}
    lines, last = [], None
    for k in range(max(1, frames)):
        a = round(value_at(keys, t0 + k / float(rate)) / 100.0, 5)
        if a == last:
            continue
        if last is not None:
            at = (k - 0.5) / float(rate)
            lines.append(f"{at:.6f} " + ", ".join(
                f"[enter] {target} {c} '{e}'" for c, e in exprs(a).items()) + ";")
        else:
            first = a
        last = a
    if lines:
        dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f"{target}=" + ":".join(f"{c}='{e}'" for c, e in exprs(first).items()), bool(lines)


def filter_path(p: Path) -> str:
    """A file path as a quoted filter option value."""
    s = str(p)
    if "'" in s:
        raise RuntimeError(f"a quote in a work path cannot go into a filter graph: {s}")
    return f"'{s}'"


def controls_graph(controls: dict | None, w: int, h: int, rate: Fraction, frames: int,
                   vin: str, vout: str, cmd_file: Path, tag: str = "mo") -> str | None:
    """Motion and Opacity from `[vin]` to `[vout]`, keyed or not. Nothing keyed: the
    static `motion_graph`. Motion keyed: `keyed_motion_graph`, then the Opacity. Opacity
    keyed: the static motion at full opacity, then the blend toward black re-set per
    frame by `sendcmd` (`opacity_commands`, written to `cmd_file`). A keyed property
    that holds one value over the part's frames is drawn as that static value
    (`held_controls`; verdict 7, blocker 2)."""
    controls = held_controls(controls, 0.0, (max(1, frames) - 1) / float(rate))
    op_keys = keys_of(controls, "opacity")
    moved = any(keys_of(controls, p) for p in MOTION_PROPS)
    if not moved and not op_keys:
        return motion_graph(controls, w, h, vin, vout, tag)
    graph, cur, km = [], vin, f"k{tag}"
    if moved:
        graph.append(keyed_motion_graph(controls, w, h, rate, cur, km))
        cur = km
    else:
        still = motion_graph({k: v for k, v in (controls or {}).items() if k != "opacity"},
                             w, h, cur, km, tag)
        if still:
            graph.append(still)
            cur = km
    if op_keys:
        target = "lutyuv@kop" if tag == "mo" else f"lutyuv@{tag}op"
        lut, changes = opacity_commands(op_keys, frames, rate, cmd_file, target)
        cmd = f"sendcmd=f={filter_path(cmd_file)}," if changes else ""
        graph.append(f"[{cur}]{cmd}{lut}[{vout}]")
    else:
        op = max(0.0, min(1.0, ctl_num(controls, "opacity", 100.0) / 100.0))
        graph.append(f"[{cur}]{dim_vf(op) if op < 1.0 - 1e-9 else 'null'}[{vout}]")
    return ";".join(graph)


def overlay_controls_graph(controls: dict | None, w: int, h: int, rate: Fraction,
                           frames: int, vin: str, vout: str, cmd_file: Path,
                           t0: float, tag: str):
    """A V2+ clip's own Motion and Opacity (verdict 6, punch 8), from its conformed
    picture `[vin]` to `[vout]`, composited OVER what is under it — never blended toward
    black as V1's are: outside the moved picture is transparent and the Opacity scales
    its alpha (yuva420p), so a clip at 50 % is half itself and half V1. The same maths as
    V1's (`motion_layout`, `keyed_motion_graph`, `opacity_commands`; keys win), `t0` the
    clip's time at this piece's first frame. Returns `(graph, x, y)` — where the
    picture's top-left lands — or None at the defaults (the opaque full-frame overlay as
    before), or "empty" when nothing of it shows (scale 0, opacity 0, off the frame). A
    keyed property that holds one value over the piece is its static value
    (`held_controls`; verdict 7, blocker 2)."""
    c = held_controls(controls, t0, t0 + (max(1, frames) - 1) / float(rate)) or {}
    op_keys = keys_of(c, "opacity")
    graph, cur, x, y = [], vin, 0, 0
    if any(keys_of(c, p) for p in MOTION_PROPS):
        graph.append(keyed_motion_graph(c, w, h, rate, cur, f"{tag}m", t0=t0, alpha=True))
        cur = f"{tag}m"
    else:
        lay = motion_layout({k: v for k, v in c.items() if k != "opacity"}, w, h,
                            fill="black@0")
        if lay == "empty":
            return "empty"
        if lay not in (None, "dim"):
            chain, x, y = lay
            graph.append(f"[{cur}]format=yuva420p,{','.join(chain)}[{tag}m]")
            cur = f"{tag}m"
    if op_keys:
        lut, changes = opacity_commands(op_keys, frames, rate, cmd_file, f"lutyuv@{tag}op",
                                        alpha=True, t0=t0)
        cmd = f"sendcmd=f={filter_path(cmd_file)}," if changes else ""
        graph.append(f"[{cur}]format=yuva420p,{cmd}{lut}[{vout}]")
    else:
        op = motion_opacity(c)
        if op <= 1e-6:
            return "empty"
        if op < 1.0 - 1e-9:
            graph.append(f"[{cur}]format=yuva420p,lutyuv=a='val*{op:.6g}'[{vout}]")
        elif not graph:
            return None
        else:
            graph.append(f"[{cur}]null[{vout}]")
    return ";".join(graph), x, y


# ---------------------------------------------- video effects (verdict 10, punch 8)
#
# A shot's Crop / Transform / Gaussian Blur stack (`controls.vfx`; roughcut/vfx.py has
# the schema and THE formulas, app/static/effects.js draws the same on the Program), on
# its graded picture, top of the stack first — before its designed effects and its
# Motion / Opacity, as Premiere renders its fixed effects (Motion, Opacity) after the
# standard ones. A bypassed effect (`off`) is not drawn. A transition's neighbour handle
# takes none of them, as it takes no Motion. A keyed parameter is drawn per frame (film
# time from the shot's first frame, as Motion's keys); one that holds a single value over
# the part is drawn as that value.

def _vfx_param(controls: dict | None, e: dict, key: str, frames: int,
               rate: Fraction) -> tuple[float | None, list[tuple[float, float]] | None]:
    """A parameter over the part: `(value, None)` when it is one value for every frame,
    else `(None, keys)`."""
    ks = vfxmod.keys_of(controls, e, key)
    if ks:
        v = held_value(ks, 0.0, (max(1, frames) - 1) / float(rate))
        return (v, None) if v is not None else (None, ks)
    return float(vfxmod.value(e, key)), None


def crop_graph(controls: dict | None, e: dict, w: int, h: int, rate: Fraction, frames: int,
               vin: str, vout: str, tag: str) -> str | None:
    """Crop: the sides black, `l = Left·W/100` … from each edge; Edge Feather a linear ramp
    `f = Feather·W/1920` px wide inside them. Hard and still: four `drawbox`es. Feathered
    or keyed: the mask `rx(x)·ry(y)` (vfx.py's formula) built per frame from a one-pixel
    row and column of `geq` — cheap at any frame size — and the picture laid by it over
    black."""
    sides = ("left", "top", "right", "bottom", "feather")
    vals = {k: _vfx_param(controls, e, k, frames, rate) for k in sides}
    keyed = any(ks for _, ks in vals.values())
    if not keyed:
        L, T, R, B, F = (vals[k][0] for k in sides)
        if max(L, T, R, B, F) <= 0:
            return None
        if F <= 0:
            if L + R >= 100 or T + B >= 100:
                return f"[{vin}]drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill[{vout}]"
            lp, rp = round(L * w / 100), round(R * w / 100)
            tp, bp = round(T * h / 100), round(B * h / 100)
            boxes = []
            if lp > 0:
                boxes.append(f"drawbox=x=0:y=0:w={lp}:h={h}:color=black:t=fill")
            if rp > 0:
                boxes.append(f"drawbox=x={w - rp}:y=0:w={rp}:h={h}:color=black:t=fill")
            if tp > 0:
                boxes.append(f"drawbox=x=0:y=0:w={w}:h={tp}:color=black:t=fill")
            if bp > 0:
                boxes.append(f"drawbox=x=0:y={h - bp}:w={w}:h={bp}:color=black:t=fill")
            return f"[{vin}]{','.join(boxes) or 'null'}[{vout}]"
    tv = f"(N*{float(1 / rate):.12g})"

    def val(k: str) -> str:
        v, ks = vals[k]
        return lerp_expr(ks, tv) if ks else f"{v:.6g}"
    f = f"max({val('feather')}*{w / vfxmod.REF_W:.9g},0.001)"
    lx, rx = f"({val('left')}*{w / 100:.9g})", f"({w}-{val('right')}*{w / 100:.9g})"
    ty, by = f"({val('top')}*{h / 100:.9g})", f"({h}-{val('bottom')}*{h / 100:.9g})"
    mx = f"255*clip((X+0.5-{lx})/{f},0,1)*clip(({rx}-X-0.5)/{f},0,1)"
    my = f"255*clip((Y+0.5-{ty})/{f},0,1)*clip(({by}-Y-0.5)/{f},0,1)"
    t = tag
    return (f"[{vin}]split=4[{t}p][{t}q][{t}r][{t}s];"
            f"[{t}q]crop={w}:2:0:0,format=gray,crop={w}:1:0:0,geq=lum='{mx}',"
            f"scale={w}:{h}:flags=neighbor[{t}mx];"
            f"[{t}r]crop=2:{h}:0:0,format=gray,crop=1:{h}:0:0,geq=lum='{my}',"
            f"scale={w}:{h}:flags=neighbor[{t}my];"
            f"[{t}mx][{t}my]blend=all_mode=multiply[{t}m];"
            f"[{t}p][{t}m]alphamerge[{t}fg];"
            f"[{t}s]drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill[{t}bg];"
            f"[{t}bg][{t}fg]overlay=format=yuv420,format=yuv420p[{vout}]")


def transform_graph(controls: dict | None, e: dict, w: int, h: int, rate: Fraction,
                    frames: int, vin: str, vout: str, tag: str, cmd_file: Path) -> str | None:
    """Transform: Motion's own maths (`controls_graph`: the static crop / scale / rotate
    over black, or the keyed per-frame warp; its Opacity over black, static or by
    `sendcmd`) with the effect's parameters and keys, under its own pad names."""
    synth: dict = {}
    keys: dict = {}
    for k in ("position_x", "position_y", "scale", "rotation", "opacity"):
        ks = vfxmod.keys_of(controls, e, k)
        if ks:
            keys[k] = [[t, v] for t, v in ks]
    px, py = float(vfxmod.value(e, "position_x")), float(vfxmod.value(e, "position_y"))
    if (px, py) != (0.5, 0.5):
        synth["position"] = [px, py]
    for k in ("scale", "rotation", "opacity"):
        synth[k] = float(vfxmod.value(e, k))
    if keys:
        synth["keys"] = keys
    return controls_graph(synth, w, h, rate, frames, vin, vout, cmd_file, tag)


def blur_graph(controls: dict | None, e: dict, w: int, h: int, rate: Fraction, frames: int,
               vin: str, vout: str, tag: str, cmd_file: Path) -> str | None:
    """Gaussian Blur: `gblur` of σ = Blurriness·W/1920 px across, down or both, on the
    picture padded with black (outside the frame reads black: the edges soften toward it,
    Premiere's default with Repeat Edge Pixels off) and cropped back. Keyed: σ re-set on
    the frames where it changes (`sendcmd`), the pad sized for the largest."""
    v, ks = _vfx_param(controls, e, "blurriness", frames, rate)
    dims = vfxmod.value(e, "dimensions")
    k = w / vfxmod.REF_W

    def sig(b: float) -> tuple[float, float]:
        s = max(0.0, b) * k
        return (s, s) if dims == "both" else (s, 0.0) if dims == "horizontal" else (0.0, s)
    top = max(x for _, x in ks) if ks else v
    if top * k <= 0.05:
        return None
    pad = int(math.ceil(3.0 * top * k)) + 2
    pad += pad % 2
    hs, vs = sig(ks[0][1] if ks else v)
    cmd = ""
    if ks:
        lines, last = [], None
        for n in range(max(1, frames)):
            s = tuple(round(x, 4) for x in sig(value_at(ks, n / float(rate))))
            if s == last:
                continue
            if last is not None:
                lines.append(f"{(n - 0.5) / float(rate):.6f} [enter] gblur@{tag} sigma {s[0]:g}, "
                             f"[enter] gblur@{tag} sigmaV {s[1]:g};")
            else:
                hs, vs = s
            last = s
        if lines:
            cmd_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            cmd = f"sendcmd=f={filter_path(cmd_file)},"
    return (f"[{vin}]pad={w + 2 * pad}:{h + 2 * pad}:{pad}:{pad}:black,{cmd}"
            f"gblur@{tag}=sigma={hs:.6g}:sigmaV={vs:.6g}:steps=3,"
            f"crop={w}:{h}:{pad}:{pad}[{vout}]")


def vfx_graph(controls: dict | None, w: int, h: int, rate: Fraction, frames: int,
              vin: str, vout: str, dest: Path) -> str | None:
    """The shot's video effects, top of the stack first, from `[vin]` to `[vout]`; None
    when none of them draws (none, all bypassed, all at their defaults)."""
    parts: list[str] = []
    cur = vin
    for i, e in enumerate(vfxmod.stack(controls)):
        if e.get("off"):
            continue
        tag, out = f"vx{i}", f"vx{i}o"
        cmd = dest.with_name(f"{dest.stem}.{tag}.cmd")
        if e["kind"] == "crop":
            g = crop_graph(controls, e, w, h, rate, frames, cur, out, tag)
        elif e["kind"] == "transform":
            g = transform_graph(controls, e, w, h, rate, frames, cur, out, tag, cmd)
        else:
            g = blur_graph(controls, e, w, h, rate, frames, cur, out, tag, cmd)
        if g:
            parts.append(g)
            cur = out
    if not parts:
        return None
    parts.append(f"[{cur}]null[{vout}]")
    return ";".join(parts)


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
                 controls: dict | None = None, gpu: dict | None = None) -> str:
    """The part's `-vf`: normalise (HDR → SDR 709, before anything sees the frame),
    the retime when the shot has a speed (`setpts` — so the *frame count* changes,
    not the rate: the `fps` conform after it duplicates or drops to the film's rate;
    ahead of the conform rather than after it so a 60 fps source slowed to half
    keeps its own frames instead of doubling decimated ones), the scale/pad/fps
    conform, then either the LUT chain — which states the range on both sides and
    ends in its own `format=yuv420p` — or the plain `format=yuv420p`; then the
    shot's Lumetri contrast and saturation (`adjust_vf`), after the table as on the
    monitor. With `gpu` (`gpu_input`) the decode and the scale are the GPU's
    (`conform_chain`); a full-range source with no LUT is put in limited range here,
    which the CPU path's `format=yuv420p` did by converting from `yuvj420p`."""
    chain = conform_chain(video, gpu, normalise, speed)
    if gpu and gpu["full"] and not lut:
        chain.append(FULL_TO_LIMITED)
    chain.append(lut if lut else "format=yuv420p")
    adj = adjust_vf(controls)
    if adj:
        chain.append(adj)
    return ",".join(chain)


def encode_part(src: Path, t_in: float, t_out: float, gain_db: float, dest: Path,
                orient: str, video: dict, *, normalise: str | None = None,
                lut: str | None = None, speed: float = 1.0, controls: dict | None = None,
                shot_effects: list[tuple[dict, list[dict]]] | None = None,
                workdir: Path | None = None, span: tuple[int, int] | None = None,
                overlays: list[dict] | None = None, v1_hidden: bool = False,
                gpu_decode: bool = True, blends: list[dict] | None = None) -> None:
    """One part: the conform, the grade, the shot's effects (`fx.part_graph`: a
    sprite `.mov` per event overlaid at its second, the effect's synth `.wav` delayed
    and mixed), its Motion and Opacity and its Volume (static or keyed), then the V2+
    clips over it for the frames they cover (`overlays`, from `overlay_pieces`, bottom
    to top; V1 black under them when `v1_hidden`), through one `-filter_complex`.
    With `span` (its frames on the film's grid) the picture is exactly that many
    frames (the last one held if the source runs short) and the sound exactly that
    long; without it the part is `-t` seconds as it always was. Everything about the
    part that the join relies on (codec, tags, timescale, `-dn`, `-write_tmcd 0`) is
    the same for every part. The sprite inputs go after the source's `-i`; the
    source's own read limit is an input `-t` ahead of it.

    `blends` (`transition_blends`): this part's half of a transition at either edit —
    the neighbouring shot's handle, through its own conform and grade, mixed in over the
    frames of the window that fall in this part (a Cross Dissolve), and/or its sound
    under a constant-power crossfade (an audio transition). Under V2+."""
    extra: list[str] = []
    vcur, acur = "vbase", "abase"
    rate = rate_of(video["fps"])
    frames = (span[1] - span[0]) if span is not None else max(
        1, math.ceil(part_seconds(t_in, t_out, speed) * rate))
    # a keyed property that holds one value over the part is its static value (one
    # stopwatch click is one key: verdict 7, blocker 2) — the sound's window is the part
    controls = held_controls(controls, 0.0, frames / float(rate))
    vol = volume_vf(controls)
    gpu = gpu_input(src, orient, normalise, video) if gpu_decode else None
    # the video effects (verdict 10 punch 8) on the graded picture, ahead of the designed
    # effects and Motion: `vfx_graph` from the conform's [vsrc] to [vbase]
    vg = vfx_graph(controls, video["w"], video["h"], rate, frames, "vsrc", "vbase", dest)
    # Lumetri › Vignette (wave 15, VIGNETTE): the last of the grade, before the effects
    graded = "vsrc" if vg else "vbase"
    vig = vignette_graph(controls, video["w"], video["h"], rate, frames, "vlum", graded, "vig", dest)
    graph = [f"[0:v]{video_filter(video, normalise, lut, speed, controls, gpu)}"
             f"[{'vlum' if vig else graded}]",
             f"[0:a]{audio_filter(gain_db + volume_db(controls), speed)}"
             f"{',' + vol if vol else ''}[abase]"]
    if vig:
        graph.append(vig)
    if vg:
        graph.append(vg)
    if shot_effects:
        extra, fxg, vcur, acur = fx.part_graph(shot_effects, video["w"], video["h"],
                                               video["fps"], workdir or dest.parent)
        if fxg:
            graph.append(fxg)
    mg = controls_graph(controls, video["w"], video["h"], rate, frames, vcur, "vmo",
                        dest.with_name(dest.stem + ".opacity.cmd"))
    if mg:
        graph.append(mg)
        vcur = "vmo"
    # transitions (verdict 6 punch 6; the kinds: verdict 8 punch 5): `apply_blends`
    vcur, acur = apply_blends(graph, extra, blends or [], video, vcur, acur, frames, rate)
    if v1_hidden:
        # V1's eye off: its picture (and its effects) black under whatever V2+ holds
        graph.append(f"[{vcur}]drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill[v1off]")
        vcur = "v1off"
    n_in = 1 + extra.count("-i")
    for j, o in enumerate(overlays or []):
        # V2+ inside the part (verdict 5, punch 6): the overlay's own range for the frames
        # it covers here, through the same conform, laid over V1 from its frame — with
        # its own Motion and Opacity, over V1 by alpha (verdict 6, punch 8)
        off, n = o["offset"], o["frames"]
        ctl = overlay_controls_graph(o.get("controls"), video["w"], video["h"], rate, n,
                                     f"ovr{j}", f"ovc{j}",
                                     dest.with_name(f"{dest.stem}.ov{j}.opacity.cmd"),
                                     float(o.get("t0") or 0.0), f"o{j}")
        if ctl == "empty":
            continue                            # nothing of it shows: not even decoded
        ogpu = gpu_input(o["src"], orient, o.get("normalise"), video) if gpu_decode else None
        extra += [*input_args(o["src"], orient, ogpu),
                  "-ss", f"{o['src_in']:.4f}", "-t", f"{o['src_len'] + 1.0:.4f}",
                  "-i", str(o["src"])]
        chain = conform_chain(video, ogpu, o.get("normalise"), o["speed"], restart=True)
        if ogpu and ogpu["full"]:
            chain.append(FULL_TO_LIMITED)
        chain += ["format=yuv420p", f"tpad=stop_mode=clone:stop_duration=2,trim=end_frame={n}"]
        shift = f"setpts=PTS-STARTPTS+{off}/({fps_text(rate)})/TB"
        x = y = 0
        if ctl is None:
            graph.append(f"[{n_in}:v]{','.join(chain + [shift])}[ov{j}]")
        else:
            cg, x, y = ctl
            graph += [f"[{n_in}:v]{','.join(chain)}[ovr{j}]", cg, f"[ovc{j}]{shift}[ov{j}]"]
        t0 = (off - 0.25) / float(rate)
        t1 = (off + n - 0.5) / float(rate)
        graph.append(f"[{vcur}][ov{j}]overlay={x}:{y}:eof_action=pass:"
                     f"enable='between(t,{t0:.6f},{t1:.6f})'[vov{j}]")
        vcur = f"vov{j}"
        n_in += 1
    tail: list[str] = []
    if span is not None:
        samples = samples_at(span[1], rate) - samples_at(span[0], rate)
        graph.append(f"[{vcur}]tpad=stop_mode=clone:stop_duration=2,"
                     f"trim=end_frame={frames},setpts=PTS-STARTPTS[vout]")
        graph.append(f"[{acur}]apad,atrim=end_sample={samples},asetpts=PTS-STARTPTS[aout]")
        vcur, acur = "vout", "aout"
    else:
        tail = ["-t", f"{part_seconds(t_in, t_out, speed):.3f}"]
    cmd = ["ffmpeg", "-v", "error", "-y", "-nostdin", *input_args(src, orient, gpu)]
    cmd += ["-ss", f"{t_in:.3f}", "-t", f"{max(0.05, t_out - t_in) + 1.0:.3f}",
            "-i", str(src), *extra, *tail,
            "-filter_complex", ";".join(graph),
            "-map", f"[{vcur}]", "-map", f"[{acur}]", "-dn",   # drop GoPro's telemetry track
            *video_codec(video),
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
        raise RuntimeError(f"cut failed {src.name} {t_in}-{t_out}"
                           f"{' (GPU decode)' if gpu else ''}: {r.stderr[-600:]}")


# ------------------------------------------------ transitions (verdict 6, punch 6)
#
# A centre-aligned transition of 2h film seconds at the edit between parts k and k+1
# covers the film frames [C - hf, C + hf), C the edit's frame and hf = round(h·rate):
# the last hf frames of part k and the first hf of part k+1. Each part mixes in the
# OTHER shot's handle for its half — part k the incoming shot's media before its in,
# part k+1 the outgoing shot's media past its out — so the parts stay one per shot, are
# still stream-copied end to end, and the film's length does not change. The incoming
# shot's weight at film frame f is x = (f - (C - hf)) / 2hf: 0 at the window's first
# frame, exactly 0.5 on the edit's frame, the incoming shot whole from C + hf on (the
# Program monitor mixes by the same x). The kinds' maths are tracks.py's (the Effects
# bin, verdict 8 punch 5):
#   Cross Dissolve  a linear mix of the two graded, conformed frames (Y'CbCr, per plane);
#   Film Dissolve   the same weights in linear light: both frames to R'G'B' (BT.709),
#                   g(v) = v^2.2, mixed, back — only the window's frames are converted;
#   Dip to Black / White   no handle at all: each part blends its OWN frames toward K
#                   (Y' 16 / 235, Cb Cr 128: drawbox's black / white), part k's K weight
#                   2x, part k+1's 2 - 2x — K alone on the edit's frame;
#   sound           Constant Power cos / sin, Constant Gain 1 - x / x, Exponential Fade
#                   e(1 - x) / e(x), e(u) = (100^u - 1) / 99.
# A handle shorter than hf frames is padded with REPEATED frames (Premiere's
# "insufficient media"): the incoming shot's first frame before its media starts, the
# outgoing shot's last frame after it ends (its sound is silence there).
# A one-sided transition (a clip's start or end) lies inside its part: the picture blends
# toward K (black; white for Dip to White; Film Dissolve fades in linear light, a scale
# of p^(1/2.2)) from its first frame / to its last, the sound along the kind's curve.
# The neighbour's handle takes its own grade (LUT), Lumetri and speed; not its Motion,
# Opacity or effects (a dissolve between two moved shots mixes the moved one with the
# other's unmoved handle).

FILM_G = tracksmod.FILM_GAMMA


def _handle_input(graph: list[str], bl: dict, video: dict, k: int, frames: int,
                  rate: Fraction, tag: str) -> None:
    """The neighbour's handle (input k) as `[tag]`, on the part's grid: `pre` repeated
    first frames (missing media before an incoming in), `take` frames of media, its last
    frame repeated after (missing media past an outgoing out), placed from frame `offset`."""
    off, n = bl["offset"], bl["frames"]
    chain = video_filter(video, bl.get("normalise"), bl.get("lut"), bl["speed"],
                         bl.get("controls"), None)
    head = f"[{k}:v]{chain}"
    # its Lumetri › Vignette (wave 15) too, at the value it has at that end of its shot
    hc = bl.get("controls")
    if colour.vignette_on(hc):
        vals = vignette_at(hc, 0.0 if bl.get("incoming") else 1e9)
        vig = vignette_graph(dict(zip(colour.VIGNETTE_FIELDS, vals)), video["w"], video["h"],
                             rate, 1, f"{tag}l", f"{tag}g", f"{tag}v", None)
        if vig:
            graph += [f"{head}[{tag}l]", vig]
            head = f"[{tag}g]null"
    pad_s = (frames / float(rate)) + 2.0
    take, pre = bl.get("take", n), bl.get("pre", 0)
    graph.append(f"{head},tpad=stop_mode=clone:stop_duration=2,trim=end_frame={take},"
                 f"setpts=PTS-STARTPTS,tpad=start={off + pre}:start_mode=clone:"
                 f"stop_mode=clone:stop_duration={pad_s:.3f}[{tag}]")


def _frame_no(rate: Fraction) -> str:
    # the frame's own number on the part's grid (blend's N counts from 1 on ffmpeg 6)
    return f"floor(T*{float(rate):.6f}+0.5)"


def blend_video(graph: list[str], bl: dict, video: dict, k: int, vcur: str,
                frames: int, rate: Fraction, j: int) -> str:
    """Mix input k (the neighbour's handle) into `vcur` over the part's frames
    [off, off + n): the other's weight is x (it comes in) or 1 - x (it goes out).
    Cross Dissolve per Y'CbCr plane; Film Dissolve in linear light (only those frames
    leave Y'CbCr: split, mixed in R'G'B', laid back over the part)."""
    off, n, n0, tot = bl["offset"], bl["frames"], bl["n0"], bl["total"]
    _handle_input(graph, bl, video, k, frames, rate, f"tro{j}")
    x = f"clip(({_frame_no(rate)}-({n0}))/{tot},0,1)"
    w = x if bl["incoming"] else f"(1-{x})"
    t0 = (off - 0.25) / float(rate)
    t1 = (off + n - 0.5) / float(rate)
    if bl.get("vkind") == "film_dissolve":
        to_rgb = "scale=in_color_matrix=bt709:in_range=tv,format=gbrp"
        to_yuv = "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p"
        g = FILM_G
        graph += [
            f"[{vcur}]split[trs{j}][trw{j}]",
            f"[trw{j}]trim=start_frame={off}:end_frame={off + n},{to_rgb}[trwa{j}]",
            f"[tro{j}]trim=start_frame={off}:end_frame={off + n},{to_rgb}[trwb{j}]",
            f"[trwa{j}][trwb{j}]blend=all_expr='255*pow((1-{w})*pow(A/255,{g})"
            f"+({w})*pow(B/255,{g}),1/{g})'[trwm{j}]",
            f"[trwm{j}]{to_yuv}[trwy{j}]",
            f"[trs{j}][trwy{j}]overlay=0:0:eof_action=pass[trv{j}]",
        ]
        return f"trv{j}"
    graph.append(f"[{vcur}][tro{j}]blend=all_expr='A+({w})*(B-A)':shortest=1:"
                 f"enable='between(t,{t0:.6f},{t1:.6f})'[trv{j}]")
    return f"trv{j}"


def toward_video(graph: list[str], bl: dict, vcur: str, rate: Fraction, j: int) -> str:
    """The part's own frames [f0, f1) blended toward K (black or white) by the weight
    expression `wk` of the frame number (a dip's half, a one-sided fade)."""
    f0, f1 = bl["f0"], bl["f1"]
    wk = bl["wk"].replace("N", _frame_no(rate))
    t0 = (f0 - 0.25) / float(rate)
    t1 = (f1 - 0.5) / float(rate)
    graph += [f"[{vcur}]split[tka{j}][tkb{j}]",
              f"[tkb{j}]drawbox=x=0:y=0:w=iw:h=ih:color={bl['colour']}:t=fill[tkk{j}]",
              f"[tka{j}][tkk{j}]blend=all_expr='A+({wk})*(B-A)':shortest=1:"
              f"enable='between(t,{t0:.6f},{t1:.6f})'[tkv{j}]"]
    return f"tkv{j}"


def gain_expr(akind: str, x: str, incoming: bool) -> str:
    """The amplitude of the incoming (or outgoing) side at x for an audio kind."""
    u = x if incoming else f"(1-{x})"
    if akind == "constant_gain":
        return u
    if akind == "exponential_fade":
        b = tracksmod.EXP_BASE
        return f"((pow({b:g},{u})-1)/{b - 1:g})"
    return f"sin(PI/2*{x})" if incoming else f"cos(PI/2*{x})"


def blend_audio(graph: list[str], bl: dict, k: int, acur: str, rate: Fraction,
                j: int) -> str:
    """The part's sound and input k's (the neighbour's handle) under the kind's
    crossfade: x by the part's own clock, the base at the outgoing / incoming curve of
    it, the other at the complement, laid from its first sample (`adelay` in samples) and
    summed. A handle with no media is silence (`mute`)."""
    off, n, n0, tot = bl["offset"], bl["frames"], bl["n0"], bl["total"]
    pre, take = bl.get("pre", 0), bl.get("take", n)
    ak = bl.get("akind", "audio_crossfade")
    fr = float(rate)
    x = f"clip((t*{fr:.6f}-({n0}))/{tot},0,1)"
    base = gain_expr(ak, x, incoming=not bl["incoming"])
    ns = samples_at(off + pre + take, rate) - samples_at(off + pre, rate)
    delay = samples_at(off + pre, rate)
    # the other's own clock starts `delay` samples into the part
    xo = f"clip(((t+{delay / AUDIO_RATE:.6f})*{fr:.6f}-({n0}))/{tot},0,1)"
    other = "0" if bl.get("mute") else gain_expr(ak, xo, incoming=bl["incoming"])
    # stereo first, as the encode's `-ac 2` would make it (a mono source's level kept)
    graph.append(f"[{acur}]asetpts=PTS-STARTPTS,aformat=channel_layouts=stereo,"
                 f"aeval=exprs='val(0)*{base}|val(1)*{base}':channel_layout=stereo[trb{j}]")
    graph.append(f"[{k}:a]{audio_filter(bl['gain_db'], bl['speed'])},asetpts=PTS-STARTPTS,"
                 "aformat=channel_layouts=stereo,"
                 f"apad,atrim=end_sample={max(1, ns)},"
                 f"aeval=exprs='val(0)*{other}|val(1)*{other}':channel_layout=stereo,"
                 f"adelay=delays={delay}S:all=1[tra{j}]")
    graph.append(f"[trb{j}][tra{j}]amix=inputs=2:normalize=0:duration=first:"
                 f"dropout_transition=0[trm{j}]")
    return f"trm{j}"


def fade_audio(graph: list[str], bl: dict, acur: str, rate: Fraction, j: int) -> str:
    """A one-sided audio transition: the part's sound over frames [f0, f1) along the
    kind's fade-in (start) or fade-out (end) curve, whole outside it."""
    fr = float(rate)
    f0, f1 = bl["f0"], bl["f1"]
    x = f"clip((t*{fr:.6f}-({f0}))/{f1 - f0},0,1)"
    g = gain_expr(bl["akind"], x, incoming=bl["side"] == "start")
    graph.append(f"[{acur}]asetpts=PTS-STARTPTS,aformat=channel_layouts=stereo,"
                 f"aeval=exprs='val(0)*{g}|val(1)*{g}':channel_layout=stereo[tfa{j}]")
    return f"tfa{j}"


def apply_blends(graph: list[str], extra: list[str], blends: list[dict], video: dict,
                 vcur: str, acur: str, frames: int, rate: Fraction) -> tuple[str, str]:
    """encode_part's transitions: each blend in turn (inputs appended to `extra`)."""
    n_in = 1 + extra.count("-i")
    for j, bl in enumerate(blends or []):
        mode = bl.get("mode", "mix")
        if mode == "toward":
            vcur = toward_video(graph, bl, vcur, rate, j)
            continue
        if mode == "fade":
            acur = fade_audio(graph, bl, acur, rate, j)
            continue
        # a transition's half (verdict 6, punch 6): the neighbour's handle as input n_in
        extra += ["-ss", f"{bl['src_in']:.4f}", "-t", f"{bl['src_len'] + 1.0:.4f}",
                  "-i", str(bl["src"])]
        if bl["kind"] == "video":
            vcur = blend_video(graph, bl, video, n_in, vcur, frames, rate, j)
        else:
            acur = blend_audio(graph, bl, n_in, acur, rate, j)
        n_in += 1
    return vcur, acur


def _p_expr(p: str, vkind: str) -> str:
    """The picture's share p of a fade toward K: Film Dissolve's p^(1/2.2) (a fade from
    black in linear light is a scale of the R'G'B' values by that), else p."""
    return f"pow({p},1/{FILM_G})" if vkind == "film_dissolve" else p


def transition_blends(edl: dict, segments: list[dict], spans: list[tuple[int, int]],
                      rate: Fraction, sources: list[Path], looks: list[dict],
                      gains: list[float]) -> tuple[list[list[dict]], list[str]]:
    """Each part's halves of the transitions at its two edits (`tracksmod.
    transition_halves`, which names the frames the media cannot cover: those are
    repeated), its one-sided fades (`tracksmod.transition_fades`), and the lines to print.
    `looks[i]` is part i's {normalise, lut, controls}; `gains[i]` its level (dB)."""
    lens: dict[str, float | None] = {}

    def clip_len(clip: str) -> float | None:
        if clip not in lens:
            i = next(k for k, s in enumerate(segments) if s["clip"] == clip)
            try:
                lens[clip] = probe_duration(sources[i])
            except Exception:                      # noqa: BLE001 — no length: no handle
                lens[clip] = None
        return lens[clip]

    trs = edl.get("transitions") or []
    halves = tracksmod.transition_halves(segments, trs, edits.dur, clip_len)
    out: list[list[dict]] = [[] for _ in segments]
    said: list[str] = []
    fr = float(rate)
    for k, c in enumerate(halves):
        a, b = segments[k], segments[k + 1]
        cut = spans[k][1]
        for key in ("video", "audio"):
            kind = c["kinds"].get(key)
            if kind is None:
                continue
            h = c[key]
            # whole frames inside the shots (the board's rule too)
            n = math.floor(h * rate + 1e-6) if kind in c["short"] else round(h * rate)
            hf = min(n, spans[k][1] - spans[k][0], spans[k + 1][1] - spans[k + 1][0])
            asked = c["asked"].get(kind)
            name = tracksmod.TRANSITION_KINDS[kind]
            if hf < 1:
                said.append(f"{name} at {cut / rate:.2f}s ({a['clip']} → {b['clip']}): "
                            "too short for a frame — a cut")
                continue
            tot = 2 * hf
            fa = spans[k][1] - spans[k][0]
            rep = c["repeat"].get(kind)
            note = ""
            if kind in tracksmod.DIP_KINDS:
                colour = "white" if kind == "dip_to_white" else "black"
                out[k].append({"kind": "video", "mode": "toward", "colour": colour,
                               "f0": fa - hf, "f1": fa,
                               "wk": f"clip((N-({fa - hf}))/{hf},0,1)"})
                out[k + 1].append({"kind": "video", "mode": "toward", "colour": colour,
                                   "f0": 0, "f1": hf, "wk": f"clip(1-N/{hf},0,1)"})
            else:
                sa, sb = edits.speed_of(a), edits.speed_of(b)
                span_s = hf / fr
                # media in whole frames: before b's in, past a's out (the rest repeats)
                la = clip_len(a["clip"])
                tail_f = (math.floor(max(0.0, (la - float(a["out"])) / sa) * fr + 1e-6)
                          if la is not None else 0)
                head_f = math.floor(max(0.0, float(b["in"]) / sb) * fr + 1e-6)
                take_b = hf if head_f >= hf else max(1, head_f)
                take_a = hf if tail_f >= hf else max(1, tail_f)
                common = {"kind": key, "total": tot, "frames": hf}
                if key == "video":
                    common["vkind"] = kind
                else:
                    common["akind"] = kind
                # part k (outgoing): the incoming shot's media before its in, over its last hf
                out[k].append({
                    **common, "incoming": True, "src": sources[k + 1],
                    "src_in": max(0.0, float(b["in"]) - (min(take_b, head_f) / fr) * sb),
                    "src_len": take_b / fr * sb, "take": take_b, "pre": hf - take_b,
                    "mute": head_f == 0,
                    "speed": sb, "offset": fa - hf, "n0": fa - hf,
                    "gain_db": gains[k + 1], **looks[k + 1]})
                # part k+1 (incoming): the outgoing shot's media past its out, over its first hf
                a_in = float(a["out"]) if tail_f > 0 else max(0.0, float(a["out"]) - sa / fr)
                out[k + 1].append({
                    **common, "incoming": False, "src": sources[k],
                    "src_in": a_in, "src_len": take_a / fr * sa, "take": take_a, "pre": 0,
                    "mute": tail_f == 0,
                    "speed": sa, "offset": 0, "n0": -hf,
                    "gain_db": gains[k], **looks[k]})
                if rep or head_f < hf or tail_f < hf:
                    miss = max(0, hf - head_f) + max(0, hf - tail_f)
                    note = (f" — insufficient media: {miss} repeated frame"
                            f"{'s' if miss != 1 else ''}"
                            + (" (silence)" if key == "audio" else ""))
            short = (f" — shortened from {asked:.2f}s to fit the shots"
                     if kind in c["short"] else "")
            said.append(f"{name} {tot / rate:.2f}s at {cut / rate:.2f}s "
                        f"({a['clip']} → {b['clip']}){short}{note}")
    # one-sided: a clip's start or end
    fades = tracksmod.transition_fades(segments, trs, edits.dur)
    for k, f in enumerate(fades):
        frames = spans[k][1] - spans[k][0]
        for side in ("start", "end"):
            for key, (kind, s) in f[side].items():
                n = min(frames, round(s * rate))
                name = tracksmod.TRANSITION_KINDS[kind]
                if n < 1:
                    continue
                f0, f1 = (0, n) if side == "start" else (frames - n, frames)
                if key == "video":
                    colour = "white" if kind == "dip_to_white" else "black"
                    p = (f"clip(N/{n},0,1)" if side == "start"
                         else f"clip(({frames - 1}-N)/{n},0,1)")
                    out[k].append({"kind": "video", "mode": "toward", "colour": colour,
                                   "f0": f0, "f1": f1, "wk": f"(1-{_p_expr(p, kind)})"})
                else:
                    out[k].append({"kind": "audio", "mode": "fade", "akind": kind,
                                   "side": side, "f0": f0, "f1": f1})
                said.append(f"{name} {n / rate:.2f}s at the {side} of shot {k + 1} "
                            f"({segments[k]['clip']}): "
                            + ("fades " + ("from " if side == "start" else "to ")
                               + ("white" if kind == "dip_to_white" else "black")
                               if key == "video" else
                               ("fade in" if side == "start" else "fade out")))
    return out, said


def cut_part(video: dict, **kw) -> None:
    """`encode_part`, robust to the GPU: a part whose GPU decode fails is cut again on
    the CPU, and an NVENC part that fails twice raises `EncoderLost` (the film is then
    re-cut with x264: one film, one encoder)."""
    try:
        encode_part(video=video, **kw)
        return
    except RuntimeError as exc:
        first = exc
    if video.get("encoder") != "nvenc" and not video.get("gpu_decode"):
        raise first
    time.sleep(0.5)                 # an NVENC session another process held may be free now
    try:
        encode_part(video=video, gpu_decode=False, **kw)
    except RuntimeError as exc:
        if video.get("encoder") == "nvenc":
            raise EncoderLost(str(exc)) from exc
        raise


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


def overlay_pieces(edl: dict, spans: list[tuple[int, int]], rate: Fraction,
                   footage: Path, generated_dir: Path | None,
                   probes: dict) -> tuple[list[list[dict]], list[str]]:
    """V2 and the added video tracks (V3…) cut into the parts they cover (verdict 5,
    punch 6: an overlay used to cost a second encode of the whole film). Bottom to top:
    a higher track covers a lower one, a hidden track's clips are left out
    (`tracksmod.video_layers`). Each clip is laid on the film's frame grid — its frames
    `round(at·rate)` up to `round((at + length)·rate)`, cut at the film's end — and each
    part gets, per clip that overlaps it, the source range for exactly the frames it
    covers there (`src_in`, `src_len` in clip seconds, `offset` frames into the part,
    `frames` long), with the clip's speed and its HDR normalise. Picture only: an
    overlay carries no sound. Returns (pieces per part, the lines to print)."""
    trk = edl.get("tracks") or {}
    pieces: list[list[dict]] = [[] for _ in spans]
    said: list[str] = []
    end = spans[-1][1] if spans else 0
    for j, o in enumerate(tracksmod.video_layers(trk, edl.get("overlays") or [])):
        at = float(o["at"])
        spd = edits.speed_of(o)
        oa = max(0, round(at * rate))
        ob = min(end, round((at + edits.dur(o)) * rate))
        if ob - oa < 1 or (ob - oa) / rate <= 0.05:
            continue
        src = source_of(footage, generated_dir, o["clip"])
        if not src.exists():
            raise SystemExit(f"missing footage for {o.get('track') or 'V2'}: {src}")
        normalise = None if edits.is_generated(o["clip"]) else colour.normalise_vf(
            shot_probe(src, None, probes))
        hit = []
        for i, (a, b) in enumerate(spans):
            fa, fb = max(a, oa), min(b, ob)
            if fb <= fa:
                continue
            into = fa / float(rate) - at
            pieces[i].append({
                "src": src, "normalise": normalise, "speed": spd,
                "src_in": float(o["in"]) + max(0.0, into) * spd,
                "src_len": (fb - fa) / float(rate) * spd,
                "offset": fa - a, "frames": fb - fa,
                # its own Effect Controls, keys timed from its first frame on the film
                "controls": o.get("controls") or None, "t0": (fa - oa) / float(rate),
                "label": f"{o.get('track') or 'V2'} {j:02d}"})
            hit.append(i)
        ctl = f" · {describe_controls(o['controls'])}" if o.get("controls") else ""
        said.append(f"{o.get('track') or 'V2'} {j:02d} {o['clip']} {o['in']:.1f}-{o['out']:.1f} "
                    f"over {oa / rate:.2f}-{ob / rate:.2f}s, inside part"
                    f"{'s' if len(hit) > 1 else ''} {', '.join(str(i) for i in hit)}{ctl}")
    return pieces, said


def tracks_pass(film: Path, edl: dict, segments: list[dict], parts: list[Path],
                video: dict, orient: str, footage: Path, generated_dir: Path | None,
                workdir: Path, film_s: float | None = None, *,
                final_audio: bool = True) -> list[str]:
    """The sequence's audio tracks over the concatenated film (roughcut/tracks.py), in
    ONE sound-only pass and only when they ask for something the plain concat is not.
    The picture is always stream-copied: V2+ and V1's eye are composited inside the
    parts (`overlay_pieces`, `encode_part`), never by re-encoding the whole film.

      * A1 — the camera sound — muted (or soloed out), at a gain, or with a shot's sound
        unlinked and moved (`a_off`): A1 is rebuilt from the parts' own audio (already
        levelled, retimed, carrying the effects' sounds), each delayed to its film
        second and mixed; muted, it is silence the film's length.
      * A3… — the added audio tracks' clips, each at its track's gain, mixed with A1.

    A rebuilt A1 is encoded here, from the parts' PCM — the film's one AAC encode —
    unless `final_audio` is False (a bed goes under it next: PCM, and the bed's pass
    encodes). Returns the lines to print."""
    trk = edl.get("tracks") or {}
    plan = tracksmod.a1_plan(segments, trk, edits.dur)
    # the film's own length — whole frames on the grid (`frame_spans`) — not the sum of
    # the shots' seconds, which a `-t` would cut the last frame short of
    total = float(film_s) if film_s is not None else plan["total"]
    # the added audio tracks' clips (A3…), each at its track's gain, mixed with A1
    extra_audio = []
    for c, gain_db in tracksmod.audio_layers(trk, edl.get("audio") or []):
        length = min(edits.dur(c), total - float(c["at"]))
        if length > 0.05:
            extra_audio.append((c, length, gain_db))
    if not plan["needed"] and not extra_audio:
        return []
    said: list[str] = []
    inputs: list[str] = ["-i", str(film)]
    graph: list[str] = []
    n = 1
    vmap, vcodec = "0:v:0", ["-c:v", "copy"]
    amap, acodec = "0:a:0", ["-c:a", "copy"]
    if extra_audio and not plan["needed"]:
        # A1 as cut is the film's own sound; the added tracks mix over it
        graph.append("[0:a]anull[a1]")
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
    if extra_audio:
        labels = ["[a1]"]
        for j, (c, length, gain_db) in enumerate(extra_audio):
            src = source_of(footage, generated_dir, c["clip"])
            if not src.exists():
                raise SystemExit(f"missing footage for {c['track']}: {src}")
            spd = edits.speed_of(c)
            inputs += ["-ss", f"{float(c['in']):.3f}", "-t", f"{length * spd:.3f}", "-i", str(src)]
            ms = int(round(float(c["at"]) * 1000))
            tempo = f",{atempo_chain(spd)}" if abs(spd - 1.0) > 1e-9 else ""
            graph.append(f"[{n}:a]aresample={AUDIO_RATE},asetpts=PTS-STARTPTS{tempo},"
                         f"volume={gain_db:.2f}dB,adelay={ms}|{ms}[x{j}]")
            labels.append(f"[x{j}]")
            n += 1
            said.append(f"{c['track']} {j:02d} {c['clip']} {c['in']:.1f}-{c['out']:.1f} at "
                        f"{float(c['at']):.2f}s, {gain_db:+.1f} dB")
        graph.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:"
                     f"dropout_transition=0,apad,atrim=0:{max(0.01, total):.3f}[amix]")
        amap = "[amix]"
        acodec = audio_codec(video, final_audio)
    out = film.with_name(film.stem + ".sound.mp4")    # picture copied, sound re-laid
    cmd = ["ffmpeg", "-v", "error", "-y", "-nostdin", *inputs,
           "-filter_complex", ";".join(graph), "-map", vmap, "-map", amap, "-dn",
           *vcodec, *acodec, "-t", f"{max(0.01, total):.3f}",
           "-video_track_timescale", str(video["timescale"]), "-write_tmcd", "0",
           "-movflags", "+faststart", str(out)]
    r = run(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"sound pass failed: {r.stderr[-600:]}")
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


def frame_size(text: str | None) -> tuple[int, int] | None:
    """`--size WxH` (`1280x720`, `1280×720`): an even width and height, or None."""
    m = re.fullmatch(r"\s*(\d{2,5})\s*[x×]\s*(\d{2,5})\s*", str(text or ""))
    if not m:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    if not (16 <= w <= 8192 and 16 <= h <= 8192):
        return None
    return w // 2 * 2, h // 2 * 2                    # even dims: yuv420p needs it


def resolve_video_profile(name: str, footage: Path, segments: list[dict],
                          fps_override: str | None = None,
                          size_override: tuple[int, int] | None = None,
                          bitrate_kbps: int | None = None) -> dict:
    """The concrete {w, h, fps, preset, crf, audio_bitrate, timescale} for `cut()`.
    `fps_override` (`--fps`, the sequence's rate the board asks an export for) wins
    over the profile's own rate; `size_override` (`--size`, the Export dialog's Frame
    Size) over its frame, scaled with Lanczos; `bitrate_kbps` (`--bitrate`) over its
    quality, as a VBR target (`video_codec`)."""
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
    extra: dict = {}
    if size_override:
        w, h = size_override
        extra["scaler"] = "lanczos"
    if bitrate_kbps:
        extra["bitrate_kbps"] = int(max(BITRATE_MIN_KBPS, min(BITRATE_MAX_KBPS, bitrate_kbps)))
    num, _, den = fps.partition("/")
    timescale = int(num) if den else int(num) * 1000   # keep sub-frame PTS precision
    # the GPU (verdict 6, punch 7): NVENC for the presets that name a CQ, when it encodes
    # here; the GPU decode and scale for every profile, when CUDA works here
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:   # ~1 s, not ~2
        nv, cu = pool.submit(nvenc_ok), pool.submit(cuda_ok)
        encoder = "nvenc" if base.get("nvenc_cq") and nv.result() else "x264"
        gpu_decode = cu.result()
    return {**base, "fps": fps, "w": w, "h": h, "timescale": timescale,
            "encoder": encoder, "gpu_decode": gpu_decode, **extra}


def describe_encoder(video: dict) -> str:
    kbps = video.get("bitrate_kbps")
    if video.get("encoder") == "nvenc":
        enc = f"{NVENC_CODEC} " + (f"VBR target {kbps / 1000:g} Mbps" if kbps
                                   else f"cq {video['nvenc_cq']}")
    else:
        enc = f"libx264 preset {video['preset']}, " + (f"VBR target {kbps / 1000:g} Mbps" if kbps
                                                       else f"crf {video['crf']}")
    if video.get("scaler"):
        enc += f", {video['scaler']} scale"
    return enc + (", GPU decode" if video.get("gpu_decode") else "")


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


FAILED_SHOT = "failed shot: "     # the board reads this line (server._render_job)


GENERIC_FFMPEG = ("Error parsing global options", "Error initializing complex filters",
                  "Error opening output", "Conversion failed", "initializing filters")


def ffmpeg_reason(text: str) -> str:
    """ffmpeg's own word on a failure, short enough to read: the first specific line of
    its stderr (not the generic "Error parsing global options" it ends with), with the
    filter graph it quotes cut out and its `[fc#0 @ 0x…]` prefixes dropped."""
    out = []
    for ln in text.splitlines():
        ln = re.sub(r"^cut failed [^:]*:\s*", "", ln.strip())
        ln = re.sub(r"^\[[^\]]*@ 0x[0-9a-f]+\]\s*", "", ln)
        ln = re.sub(r" in filtergraph description \S+", "", ln)
        ln = re.sub(r"'[^']{60,}'", "'…'", ln)
        if ln:
            out.append(ln)
    specific = [ln for ln in out if not ln.startswith(GENERIC_FFMPEG)]
    return ((specific or out or [""])[0])[:200]


def say_failed_shot(job: dict, exc: BaseException, spans: list, rate: Fraction) -> None:
    """One line, for whoever ran this render, naming the shot whose part failed: its
    place in the EDL, id, clip and range, where it starts in the film, and ffmpeg's own
    last word on it. Verdict 7: an export failed with "the render failed — see the log"
    and nothing said which shot or what to do; the board's Export dialog now names it
    and offers Go to shot."""
    seg = job["seg"]
    i = job["index"]
    reason = ffmpeg_reason(str(exc)) or type(exc).__name__
    over = [f"{o.get('label', 'V2')} {Path(o['src']).name}"
            for o in (job["kw"].get("overlays") or []) if o.get("src")]
    print(FAILED_SHOT + json.dumps({
        "index": i, "id": seg.get("id"), "clip": seg.get("clip"),
        "in": seg.get("in"), "out": seg.get("out"),
        "film_s": round(float(spans[i][0] / rate), 4),
        "overlays": over, "reason": reason[-200:]}), flush=True)


def describe_controls(c: dict) -> str:
    """One line for the render log: the shot's Effect Controls that are not default."""
    bits = []
    if c.get("position"):
        bits.append(f"position {c['position'][0]:.3f},{c['position'][1]:.3f}")
    for k, unit in (("scale", "%"), ("rotation", "°"), ("opacity", "%"),
                    ("volume_db", " dB"), ("contrast", ""), ("saturation", ""),
                    ("highlights", ""), ("shadows", ""), ("whites", ""), ("blacks", "")):
        if c.get(k) is not None:
            bits.append(f"{k} {c[k]:g}{unit}")
    for k in KEYED_PROPS:
        keys = keys_of(c, k)
        if keys:
            bits.append(f"{k} keyed ×{len(keys)} "
                        + "→".join(f"{v:g}@{t:g}s" for t, v in keys))
    if c.get("curves"):
        bits.append("curves " + "/".join(k for k in colour.CURVE_CHANNELS if c["curves"].get(k)))
    if c.get("wheels"):
        bits.append("wheels " + "/".join(k for k in colour.WHEEL_NAMES if c["wheels"].get(k)))
    if c.get("hsl"):
        bits.append("hsl secondary")
    vd = vignette_describe(c)
    if vd:
        bits.append(vd)
    if c.get("fx_off"):
        bits.append(f"fx off {len(c['fx_off'])}")
    if c.get("fx_order"):
        bits.append("fx order")
    if c.get("vfx"):
        bits.append(vfxmod.describe(c))
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
    ap.add_argument("--size", default=None,
                    help="the film's frame size, WxH (`1280x720`), over the profile's own: "
                         "the Export dialog's Frame Size. Scaled with Lanczos, the picture "
                         "fitted inside it and padded black when the aspect differs.")
    ap.add_argument("--bitrate", type=float, default=None,
                    help="a target video bitrate in Mbps (VBR, 1 pass; peaks 1.5x it) over "
                         "the profile's quality: the Export dialog's Bitrate")
    ap.add_argument("--jobs", type=int, default=None,
                    help="parts encoded at once (default: ROUGHCUT_RENDER_JOBS, else half "
                         "the cores)")
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
    size = frame_size(args.size) if args.size else None
    if args.size and size is None:
        print(f"error: --size {args.size!r} is not WxH (16-8192 each)", file=sys.stderr)
        return 2
    if args.bitrate is not None and not (0.1 <= args.bitrate <= 400):
        print(f"error: --bitrate {args.bitrate:g} is not 0.1-400 Mbps", file=sys.stderr)
        return 2
    video = resolve_video_profile(args.profile, args.footage, segments, args.fps, size,
                                  round(args.bitrate * 1000) if args.bitrate else None)
    rate = rate_of(video["fps"])
    spans = frame_spans(segments, rate)
    film_s = float(spans[-1][1] / rate) if spans else 0.0
    print(f"variant {edl['variant']} — {edl['title']}: {len(segments)} segments, "
          f"{planned:.1f}s planned, orient={orient}")
    quality = (f"target {video['bitrate_kbps'] / 1000:g} Mbps" if video.get("bitrate_kbps")
               else f"crf {video['crf']}")
    print(f"profile {args.profile}: {video['w']}x{video['h']} @ {video['fps']}fps, "
          f"preset {video['preset']}, {quality}, audio {video['audio_bitrate']} · "
          f"encoder {describe_encoder(video)}")

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
    # V2+ inside the parts that cover them, V1's eye in every part (verdict 5, punch 6)
    trk = edl.get("tracks") or {}
    v1_hidden = not tracksmod.visible(trk, "V1")
    pieces, overlay_said = overlay_pieces(edl, spans, rate, args.footage,
                                          args.generated_dir, probes)
    if v1_hidden:
        print("tracks: V1 hidden: black under V2")
    for line in overlay_said:
        print(f"tracks: {line}")
    try:
        jobs: list[dict] = []               # each part's encode, prepared in film order
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
                # A balance alone (the auto's, a hand's) is three 1D tables, ~2.5x
                # cheaper at 4K than the 33³ cube a match or a look still goes through.
                bal = colour.balance_only(res)
                cube = None if bal else colour.write_cube(
                    colour.cube_table(res["fn"], 33), workdir / f"part_{i:03d}.cube",
                    title=f"roughcut {seg.get('id') or i}")
                lut = colour.lut_vf(cube, colour.in_range(probe), balance=bal)
            # The shot's accepted effects (INTAKE M12) fold into this same encode;
            # an event outside the shot's range is simply not drawn. In a retimed
            # part (INTAKE M13) an event's `t_part` moves with the picture.
            # Effect Controls' stack decides which are drawn and in what order.
            shot_effects = [(e, retimed_events(fx.events_in_shot(e, seg), speed))
                            for e in ordered_effects(edl, seg)]
            shot_effects = [(e, evs) for e, evs in shot_effects if evs]
            controls = seg.get("controls") or None
            jobs.append({
                "kw": dict(src=src, t_in=seg["in"], t_out=seg["out"], gain_db=gain,
                           dest=dest, orient=orient, normalise=normalise, lut=lut,
                           speed=speed, controls=controls,
                           shot_effects=shot_effects or None,
                           workdir=workdir / f"part_{i:03d}_fx", span=spans[i],
                           overlays=pieces[i], v1_hidden=v1_hidden),
                "index": i, "seg": seg, "generated": generated, "res": res, "probe": probe,
                "shot_effects": shot_effects})

        # Transitions (verdict 6, punch 6): each part's halves, mixed inside it
        blends, trans_said = transition_blends(
            edl, segments, spans, rate, [j["kw"]["src"] for j in jobs],
            [{"normalise": j["kw"]["normalise"], "lut": j["kw"]["lut"],
              "controls": j["kw"]["controls"]} for j in jobs],
            [j["kw"]["gain_db"] + volume_db(j["kw"]["controls"]) for j in jobs])
        for job, bl in zip(jobs, blends):
            if bl:
                job["kw"]["blends"] = bl
        for line in trans_said:
            print(f"transition: {line}")

        def one(job: dict) -> float:
            try:
                cut_part(video, **job["kw"])
            except EncoderLost:
                raise                               # the film is cut again with x264
            except Exception as exc:
                say_failed_shot(job, exc, spans, rate)
                raise
            dest = job["kw"]["dest"]
            assert_no_rotation(dest, orient)        # catch it at the part, not the film
            return probe_duration(dest)

        # The parts in parallel (verdict 6, punch 7), up to half the cores: each is its
        # own ffmpeg with its own `-progress` file, and their sound stays PCM (the film's
        # one AAC encode is still the join's or a later pass's).
        n_jobs = min(render_jobs(args.jobs), max(1, len(jobs)))

        def cut_all() -> list[float]:
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=n_jobs)
            try:
                return list(pool.map(one, jobs))
            finally:
                pool.shutdown(wait=True, cancel_futures=True)   # a failure stops the queue

        try:
            actuals = cut_all()
        except EncoderLost as exc:
            # one film, one encoder: every part again with x264
            print(f"encoder: {NVENC_CODEC} failed ({str(exc)[-160:]}); "
                  f"the film is cut again with libx264")
            video = {**video, "encoder": "x264"}
            actuals = cut_all()
        print(f"parts: {len(jobs)} cut {n_jobs} at a time · {describe_encoder(video)}")
        for i, (job, actual) in enumerate(zip(jobs, actuals)):
            seg, speed, gain = job["seg"], job["kw"]["speed"], job["kw"]["gain_db"]
            controls, shot_effects = job["kw"]["controls"], job["shot_effects"]
            parts.append(job["kw"]["dest"])
            sp = f" ×{speed:g}" if abs(speed - 1.0) > 1e-9 else ""
            print(f"  {i:02d} {seg['clip']} {seg['in']:6.1f}-{seg['out']:6.1f}{sp} "
                  f"({actual:5.2f}s, {gain:+5.1f}dB)  {(seg.get('why') or '')[:56]}")
            print(f"      colour: {'generated — as made' if job['generated'] else describe_colour(job['res'], job['kw']['normalise'], job['probe'])}")
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

        # The audio tracks (roughcut/tracks.py): A1's mute / gain / unlinked offsets and
        # A3+ — one sound-only pass (the picture copied), only when one is not the default.
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
                                        speech=speech, audio_bitrate=video["audio_bitrate"])
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
