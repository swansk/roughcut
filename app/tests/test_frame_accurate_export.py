"""A shot opens on the frame its in names (wave 16, lane FRAMEACC; REDESIGN-GOAL
dimensions 5 and 8).

Lane INOUT measured it at 59.94: an In/Out export whose In was at a shot's frame 7, 8 or
10 opened on 8, 9 or 11. The render read every source with `-ss <in to 3 places>`, which
keeps the frames from that time on, so whenever the rounding landed past the frame's
exact start the shot lost its first frame — 7 ins in 16 at 59.94, 29.97 and 23.976 (none
at 25, whose frames fall on 3-place times). The V2+ overlays and a dissolve's handles had
the same read, and a V2 clip was laid with `setpts=…+off/(rate)/TB`, which setpts
truncates: 42 frames at 59.94 evaluated to 41.99999…, and the clip came a frame early.

assemble.py now seeks a few frames early (`seek_plan`), moves the timestamps so the in is
0, and its conform's `fps` (`start_time=0:round=up`) puts on each film frame the last
source frame at or before that frame's time: the in's own frame first, then each one on —
at a speed, across rates, from an in kept to 4 places or 3. The sound starts at the in.

The fixture clips are lossless with a keyframe every 8 frames (a seek lands both on and
between keyframes); every frame is its own grey level, so the film's luma says which
source frame it is. Renders are real (assemble.py, the Match Sequence profile at the
sequence's rate, as `/api/render` runs it) on the CPU path (ROUGHCUT_GPU=0); no model.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "research" / "tools"
sys.path.insert(0, str(TOOLS))

RATES = ["60000/1001", "30000/1001", "24000/1001", "25"]
CLIP_S = 6.0
W, H = 32, 18                      # the frames are read back this small (flat fields)


def _rate(text: str) -> Fraction:
    num, _, den = text.partition("/")
    return Fraction(int(num), int(den or 1))


def _tag(rate: str) -> str:
    return rate.replace("/", "_")


def _make(path: Path, rate: str, geq: str) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i", f"color=black:size=320x180:rate={rate}:duration={CLIP_S},"
                              f"format=yuv420p,geq={geq}",
         "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo,atrim=0:{CLIP_S}",
         "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-g", "8",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", f"{CLIP_S}", str(path)],
        check=True, capture_output=True)


# every frame its own luma: 16 + 7·(N mod 32), or 16 + 14·(N mod 16) for a frame read
# through a dissolve's mix (half of it is 7 a frame again)
GREYS = {"N7": "lum='16+mod(N\\,32)*7':cb=128:cr=128",
         "N14": "lum='16+mod(N\\,16)*14':cb=128:cr=128",
         "BLK": "lum=16:cb=128:cr=128",
         "RED": "lum=82:cb=90:cr=240"}


@pytest.fixture(scope="module")
def footage(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("frameacc") / "footage"
    root.mkdir()
    for rate in RATES:
        for stem, geq in GREYS.items():
            _make(root / f"{stem}_{_tag(rate)}.MP4", rate, geq)
    return root


def _render(footage: Path, edl: dict, rate: str, name: str) -> list[float]:
    """The EDL rendered at `rate`; the mean luma of each of the film's frames."""
    d = footage.parent / name
    d.mkdir(exist_ok=True)
    edl = {"variant": "F", "title": name, "orient": "none", "fps": float(_rate(rate)), **edl}
    (d / "edl.json").write_text(json.dumps(edl), encoding="utf-8")
    film = d / "film.mp4"
    r = subprocess.run(["uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(d / "edl.json"),
                        "--footage", str(footage), "--profile", "match", "--fps", rate,
                        "--jobs", "2", "-o", str(film)],
                       capture_output=True, text=True, env={**os.environ, "ROUGHCUT_GPU": "0"})
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    raw = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(film),
                          "-vf", f"scale={W}:{H}", "-pix_fmt", "yuv420p", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    n, size = W * H, W * H * 3 // 2
    return [sum(raw[i:i + n]) / n for i in range(0, len(raw), size)]


def _r(x, places: int = 4) -> float:
    return round(float(x), places)


def _at_or_before(t: Fraction, src: Fraction) -> int:
    """The source frame on screen at source time `t`: the last one starting at or before it."""
    return int(t * src + Fraction(1, 1000))


def _which(y: float, step: float) -> int:
    return round((y - 16) / step)


def _shots(stem: str, rate: str, src_rate: str | None = None, speed: Fraction = Fraction(1),
           places: int = 4, frames: int = 3) -> tuple[dict, list[tuple[int, int]]]:
    """16 shots of `stem`, shot k's in at its frame k (to `places`), `frames` film frames
    each; and (film frame, the source frame it must be) for every frame of them."""
    R, S = _rate(rate), _rate(src_rate or rate)
    clip = f"{stem}_{_tag(src_rate or rate)}.MP4"
    segs, want = [], []
    for k in range(16):
        segs.append({"id": f"s{k}", "clip": clip, "in": _r(Fraction(k) / S, places),
                     "out": _r(Fraction(k) / S + frames * speed / R, places),
                     **({"speed": float(speed)} if speed != 1 else {})})
        for j in range(frames):
            want.append((frames * k + j, _at_or_before(Fraction(k) / S + j * speed / R, S)))
    return {"segments": segs}, want


def _check(y: list[float], want: list[tuple[int, int]], step: float = 7.0,
           weight: float = 1.0) -> list[tuple[int, int, int]]:
    return [(f, w, _which(y[f], step * weight)) for f, w in want
            if _which(y[f], step * weight) != w]


# ---------------------------------------------------------------- the read (no render)

def test_a_read_is_sought_early_and_the_conform_starts_on_the_ins_frame(footage):
    import assemble
    src = footage / "N7_60000_1001.MP4"
    ss, lead = assemble.seek_plan(src, 0.1168)            # frame 7 at 59.94 (0.116783)
    assert float(ss) == pytest.approx(0.1168 - 3 * 1001 / 60000, abs=1e-6)
    assert lead == pytest.approx(3 * 1001 / 60000, abs=1e-6)
    assert assemble.seek_plan(src, 0.02) == ("0.000000", 0.02)      # never before the head
    video = {"w": 320, "h": 180, "fps": "60000/1001"}
    chain = assemble.conform_chain(video, None, None, lead=lead)
    assert chain[0] == f"setpts=PTS-{lead + assemble.SEEK_TOL_S:.6f}/TB"
    assert chain[-1] == "fps=60000/1001:start_time=0:round=up"
    assert assemble.conform_chain(video, None, None, 2.0, lead=lead)[0] == (
        f"setpts=(PTS-{lead + assemble.SEEK_TOL_S:.6f}/TB)/2")
    # the sound from the in itself, re-timed to 0
    assert assemble.audio_lead(lead) == f"atrim=start={lead:.6f},asetpts=PTS-{lead:.6f}/TB,"
    assert assemble.audio_lead(0.0) == ""
    # without a lead the read is as it was (a caller that seeks at the in itself)
    assert assemble.conform_chain(video, None, None)[-1] == "fps=60000/1001"


# ---------------------------------------------------------------- the film

@pytest.mark.parametrize("rate", RATES)
def test_every_v1_shot_opens_on_its_ins_frame(footage, rate):
    """Ins at frames 0-15 (the edit grid's 4 places): every frame of every shot is the
    source frame it names. Before: 27/48 right at 59.94, 29.97 and 23.976."""
    edl, want = _shots("N7", rate)
    y = _render(footage, edl, rate, f"v1_{_tag(rate)}")
    assert len(y) == 48
    assert _check(y, want) == []


@pytest.mark.parametrize("rate", RATES)
def test_every_v2_clip_opens_on_its_ins_frame_at_its_own_frame(footage, rate):
    """V1 one red shot; V2 clips at frames 6k, 3 frames each, in at frame k. Before:
    25/48 right at 59.94, 28 at 29.97, 18 at 23.976 — the read's lost frame, and a clip
    laid a frame early where `off/(rate)/TB` truncated."""
    R = _rate(rate)
    clip = f"N7_{_tag(rate)}.MP4"
    ovs, want = [], []
    for k in range(16):
        ovs.append({"id": f"o{k}", "clip": clip, "in": _r(k / R), "out": _r((k + 3) / R),
                    "at": _r(6 * k / R)})
        want += [(6 * k + j, k + j) for j in range(3)]
    edl = {"segments": [{"id": "r", "clip": f"RED_{_tag(rate)}.MP4", "in": 0.0,
                         "out": _r(96 / R)}], "overlays": ovs}
    y = _render(footage, edl, rate, f"v2_{_tag(rate)}")
    assert len(y) == 96
    assert _check(y, want) == []
    red = [f for f in range(96) if f % 6 >= 3]
    assert all(_which(y[f], 7) == 9 for f in red)          # Y 82: V1 between them


@pytest.mark.parametrize("rate", RATES)
def test_a_dissolves_handles_open_on_their_frames(footage, rate):
    """Pairs joined by an 8-frame Cross Dissolve (4 frames a side), the pairs by cuts.
    Incoming: black ⇒ N14 in at frame k — N14's handle is its frames k-4…k-1 under
    black's last 4 (its weight j/8: the last is frame k-1 at 3/8), and the shot's own
    first frame (k) at 1/2. Outgoing: N14 out at frame k ⇒ black — its handle starts on
    frame k, at 1/2 on black's first frame. Before: 6/24 and 1-2/12 right at 59.94,
    29.97 and 23.976."""
    R = _rate(rate)
    n14, blk = f"N14_{_tag(rate)}.MP4", f"BLK_{_tag(rate)}.MP4"
    segs, trs, want_in, want_out = [], [], [], []
    f = 0
    for k in range(4, 16):
        a = {"id": f"a{k}", "clip": blk, "in": _r(8 / R), "out": _r(16 / R)}
        b = {"id": f"b{k}", "clip": n14, "in": _r(k / R), "out": _r((k + 8) / R)}
        segs += [a, b]
        trs.append({"id": f"t{k}", "a": a["id"], "b": b["id"], "kind": "cross_dissolve",
                    "dur": _r(8 / R), "align": "center"})
        want_in.append(((f + 7, k - 1), 3 / 8))
        want_in.append(((f + 8, k), 1 / 2))
        f += 16
    y = _render(footage, {"segments": segs, "transitions": trs}, rate, f"din_{_tag(rate)}")
    bad = [(fr, w, _which(y[fr], 14 * wt)) for (fr, w), wt in want_in
           if _which(y[fr], 14 * wt) != w]
    assert bad == [], bad
    segs, trs, f = [], [], 0
    for k in range(4, 16):
        a = {"id": f"a{k}", "clip": n14, "in": _r((k - 4) / R), "out": _r(k / R)}
        b = {"id": f"b{k}", "clip": blk, "in": _r(8 / R), "out": _r(16 / R)}
        segs += [a, b]
        trs.append({"id": f"t{k}", "a": a["id"], "b": b["id"], "kind": "cross_dissolve",
                    "dur": _r(8 / R), "align": "center"})
        want_out.append((f + 4, k))
        f += 12
    y = _render(footage, {"segments": segs, "transitions": trs}, rate, f"dout_{_tag(rate)}")
    assert _check(y, want_out, 14, 0.5) == []


@pytest.mark.parametrize("case", ["three_places", "half_speed", "double_speed",
                                  "59.94_in_23.976", "24_in_25"])
def test_old_ins_speeds_and_other_rates_take_the_frame_at_or_before(footage, case):
    """An in kept to 3 places (EDLs before the edit grid); a shot at ½× and 2× (before,
    every 2× shot opened a frame late: 0/48); a clip in a sequence of another rate —
    each film frame the last source frame at or before its time, the Program monitor's
    <video> rule (before: 0/48 for 59.94 in 23.976, 11/48 for 24 in 25)."""
    rate, kw = {"three_places": ("60000/1001", {"places": 3}),
                "half_speed": ("60000/1001", {"speed": Fraction(1, 2)}),
                "double_speed": ("24000/1001", {"speed": Fraction(2)}),
                "59.94_in_23.976": ("24000/1001", {"src_rate": "60000/1001"}),
                "24_in_25": ("25", {"src_rate": "24"})}[case]
    src = kw.get("src_rate")
    if src and not (footage / f"N7_{_tag(src)}.MP4").exists():
        _make(footage / f"N7_{_tag(src)}.MP4", src, GREYS["N7"])
    edl, want = _shots("N7", rate, **kw)
    y = _render(footage, edl, rate, f"x_{case.replace('.', '')}")
    assert len(y) == 48
    assert _check(y, want) == []
