"""The export renders what Effect Controls shows (verdict 3, punch 3; REDESIGN-GOAL
dimension 7), on the frame grid it says (the journey's CFR finding).

A cut of one-second shots, all the same second of CLIP_A, is rendered twice the way the
board renders — `assemble.py` as a subprocess, the Match Sequence profile so the frame is
the fixture's own 320×180 at its own 24 fps — once with each shot carrying one property
in `controls` and once without any. Each property is then measured off the two films at
the same frame: Motion (scale, position, rotation) and Opacity by pixels, Lumetri
contrast and saturation by the shader's own maths (app/static/grade.js), Volume by
`volumedetect`, the designed effects' bypass and order by the effect's pixels. The film
itself is constant frame rate at the sequence's rate, a whole number of its frames.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import edits, fx  # noqa: E402

TOOLS = Path(__file__).resolve().parents[2] / "research" / "tools"
sys.path.insert(0, str(TOOLS))
W, H, FPS = 320, 180, 24

# one property per shot; the same second of the same clip under every one of them
CONTROLS = [
    None,                                   # 0 plain
    {"scale": 50},                          # 1
    {"position": [0.75, 0.5]},              # 2  a quarter of the frame to the right
    {"rotation": 180},                      # 3
    {"opacity": 50},                        # 4
    {"contrast": 100},                      # 5  k = 1.5
    {"saturation": 0},                      # 6
    {"volume_db": -6},                      # 7
    None,                                   # 8  the red block, on
    {"fx_off": ["fx_red"]},                 # 9  the red block, bypassed
    {"fx_order": ["fx_green", "fx_red"]},   # 10 red drawn last: red on top
    None,                                   # 11 the EDL's order: green on top
]


def _block(colour: str) -> dict:
    return {"duration": 0.8, "size": 0.3,
            "shapes": [{"type": "rect", "at": [0, 0], "w": 2.0, "h": 2.0, "fill": True,
                        "color": colour, "opacity": 1.0}]}


def _edl(with_controls: bool) -> dict:
    segs = []
    for i, c in enumerate(CONTROLS):
        s = {"id": f"s{i}", "clip": "CLIP_A.MP4", "in": 1.0, "out": 2.0, "why": f"shot {i}"}
        if with_controls and c:
            s["controls"] = c
        segs.append(s)
    edl = {"variant": "C", "title": "controls", "orient": "none", "target_s": [1, 30],
           "segments": segs}
    if with_controls:
        effects = []
        for sid in ("s8", "s9", "s10", "s11"):
            for eid, colour in (("fx_red", "#ff0000"), ("fx_green", "#00ff00")):
                if eid == "fx_green" and sid in ("s8", "s9"):
                    continue
                e = {"id": eid, "shot": sid, "name": eid, "status": "accepted",
                     "events": [{"t": 1.1, "x": 0.5, "y": 0.5}], "overlay": _block(colour)}
                effects.append(fx.validate_effect(e, segs))
        edl["effects"] = effects
    return edl


def _render(edl: dict, root: Path, name: str, project: dict) -> tuple[Path, str]:
    edl_path = root / f"{name}.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    out = root / f"{name}.mp4"
    cmd = ["uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(edl_path),
           "--footage", str(project["footage"]), "--profile", "match", "-o", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return out, r.stdout


@pytest.fixture(scope="module")
def films(project, tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("controls")
    with_, log = _render(_edl(True), root, "with", project)
    base, _ = _render(_edl(False), root, "base", project)
    return {"with": with_, "base": base, "log": log}


def frame(path: Path, shot: int) -> np.ndarray:
    """The shot's middle frame as (Y, U, V) planes, full resolution (yuv444p)."""
    t = (shot * FPS + FPS // 2 + 0.5) / FPS
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-ss", f"{t:.4f}",
                        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "yuv444p", "-"],
                       capture_output=True, check=True)
    a = np.frombuffer(r.stdout, np.uint8).astype(np.float64)
    assert a.size == 3 * W * H, a.size
    return a.reshape(3, H, W)


def mad(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(np.abs(a - b)))


def mean_volume(path: Path, shot: int) -> float:
    r = subprocess.run(["ffmpeg", "-v", "info", "-nostdin", "-ss", f"{shot + 0.15:.3f}",
                        "-t", "0.7", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"mean_volume: (-?[\d.]+) dB", r.stderr)
    assert m, r.stderr[-400:]
    return float(m.group(1))


def probe(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                        "-show_entries", "stream=r_frame_rate,avg_frame_rate,nb_read_frames,width,height",
                        "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)["streams"][0]


# ---------------------------------------------------------------- the film

def test_the_film_is_constant_frame_rate_on_the_sequence_grid(films):
    for name in ("with", "base"):
        p = probe(films[name])
        assert p["r_frame_rate"] == p["avg_frame_rate"] == "24/1", p
        assert int(p["nb_read_frames"]) == FPS * len(CONTROLS), p
        assert (int(p["width"]), int(p["height"])) == (W, H), p


def test_the_log_names_each_shots_controls(films):
    assert "controls: scale 50%" in films["log"]
    assert "controls: rotation 180°" in films["log"]
    assert "controls: volume_db -6 dB" in films["log"]


def test_a_shot_without_controls_is_untouched(films):
    assert mad(frame(films["with"], 0), frame(films["base"], 0)) < 1.0


# ---------------------------------------------------------------- Motion

def test_scale_50_is_the_picture_at_half_size_in_the_middle_on_black(films):
    got, ref = frame(films["with"], 1), frame(films["base"], 1)
    y = got[0]
    # outside the centred 160×90 box: black
    ring = np.concatenate([y[:, :76].ravel(), y[:, 244:].ravel(), y[:41, :].ravel(), y[139:, :].ravel()])
    assert ring.mean() < 18 and ring.max() < 30, (ring.mean(), ring.max())
    # inside: the reference at half size
    half = ref[0].reshape(H // 2, 2, W // 2, 2).mean(axis=(1, 3))
    assert mad(y[45:135, 80:240], half) < 8, mad(y[45:135, 80:240], half)
    assert mad(y[45:135, 80:240], ref[0][45:135, 80:240]) > 2 * mad(y[45:135, 80:240], half)


def test_position_moves_the_picture_by_frame_fractions(films):
    got, ref = frame(films["with"], 2), frame(films["base"], 2)
    assert got[0][:, :78].mean() < 18                     # the left quarter is black
    assert mad(got[0][:, 82:], ref[0][:, 2:240]) < 3, mad(got[0][:, 82:], ref[0][:, 2:240])


def test_rotation_turns_the_picture_about_its_centre(films):
    got, ref = frame(films["with"], 3), frame(films["base"], 3)
    turned = ref[:, ::-1, ::-1]
    inner = (slice(None), slice(4, H - 4), slice(4, W - 4))
    assert mad(got[inner], turned[inner]) < 4, mad(got[inner], turned[inner])
    assert mad(got[inner], ref[inner]) > 15


# ---------------------------------------------------------------- Opacity, Lumetri

def test_opacity_50_is_half_the_picture_over_black(films):
    got, ref = frame(films["with"], 4), frame(films["base"], 4)
    assert mad(got[0] - 16, (ref[0] - 16) * 0.5) < 2.5
    assert mad(got[1:] - 128, (ref[1:] - 128) * 0.5) < 2.5


def test_contrast_is_the_shaders_maths(films):
    got, ref = frame(films["with"], 5), frame(films["base"], 5)
    want = np.clip((ref[0] - 125.5) * 1.5 + 125.5, 16, 235)
    assert mad(got[0], want) < 3, mad(got[0], want)
    assert got[0].std() > 1.3 * ref[0].std()
    # the per-channel contrast scales the colour differences by k too
    assert mad(got[1:] - 128, np.clip((ref[1:] - 128) * 1.5, -112, 112)) < 3


def test_saturation_0_is_grey(films):
    got, ref = frame(films["with"], 6), frame(films["base"], 6)
    assert np.abs(ref[1:] - 128).mean() > 8                # testsrc2 is colourful
    assert np.abs(got[1:] - 128).mean() < 1.5
    assert mad(got[0], ref[0]) < 2                          # luma kept (a mix toward Rec.709 luma)


# ---------------------------------------------------------------- Volume

def test_volume_minus_6_db_is_6_db_quieter(films):
    got, ref = mean_volume(films["with"], 7), mean_volume(films["base"], 7)
    assert abs((got - ref) - (-6.0)) <= 1.0, (got, ref)
    # and a shot with no volume is as loud as the reference
    assert abs(mean_volume(films["with"], 6) - mean_volume(films["base"], 6)) <= 0.5


# ---------------------------------------------------------------- the designed effects

def _centre(planes: np.ndarray) -> tuple[float, float]:
    """(Cr − 128, Cb − 128) around the frame's centre: red has Cr high, green has both low."""
    c = planes[:, H // 2 - 10:H // 2 + 10, W // 2 - 10:W // 2 + 10]
    return float(c[2].mean() - 128), float(c[1].mean() - 128)


def test_fx_off_removes_the_effects_pixels(films):
    on, off = frame(films["with"], 8), frame(films["with"], 9)
    assert _centre(on)[0] > 60, _centre(on)                 # the red block is drawn
    assert mad(off, frame(films["base"], 9)) < 1.0         # bypassed: the shot as shot


def test_fx_order_puts_the_last_in_the_stack_on_top(films):
    reordered, default = _centre(frame(films["with"], 10)), _centre(frame(films["with"], 11))
    assert reordered[0] > 60, reordered                    # red on top
    assert default[0] < -40, default                       # green on top (the EDL's order)


# ---------------------------------------------------------------- the pieces

def test_the_filter_pieces():
    import assemble
    assert assemble.adjust_vf(None) is None
    assert assemble.adjust_vf({"contrast": 0, "saturation": 100}) is None
    vf = assemble.adjust_vf({"contrast": 100, "saturation": 50})
    assert "(val-125.5)*1.5+125.5" in vf and "(val-128)*0.75+128" in vf, vf
    assert assemble.motion_graph(None, 320, 180, "a", "b") is None
    assert assemble.motion_graph({"opacity": 100, "position": [0.5, 0.5]}, 320, 180, "a", "b") is None
    assert assemble.motion_graph({"opacity": 25}, 320, 180, "a", "b").startswith("[a]lutyuv=")
    assert "drawbox" in assemble.motion_graph({"scale": 0}, 320, 180, "a", "b")
    # moved off the frame entirely: black, and nothing huge is built for a 1000 % scale
    assert "crop" not in assemble.motion_graph({"position": [5, 5]}, 320, 180, "a", "b")
    big = assemble.motion_graph({"scale": 1000}, 1920, 1080, "a", "b")
    m = re.search(r"scale=(\d+):(\d+)", big)
    assert m and int(m.group(1)) <= 1920 + 60 and int(m.group(2)) <= 1080 + 60, big
    assert assemble.rate_of("29.97") == assemble.rate_of("30000/1001")
    assert assemble.fps_text(assemble.rate_of(59.94)) == "60000/1001"
    assert assemble.fps_text(assemble.rate_of(23.976)) == "24000/1001"
    assert assemble.fps_text(assemble.rate_of("24")) == "24"
    # a frame grid off the cumulative seconds: no shot drifts the film
    segs = [{"clip": "a", "in": 0, "out": 1.01}] * 10
    spans = assemble.frame_spans(segs, assemble.rate_of(24))
    assert spans[-1][1] == round(10.1 * 24) and all(a < b for a, b in spans)


def test_edits_carry_controls_and_a_razor_gives_both_halves_their_own():
    segs = [{"id": "a", "clip": "CLIP_A.MP4", "in": 0.0, "out": 4.0,
             "controls": {"scale": 50, "fx_off": ["x"]}}]
    ops = edits.validate_ops([{"op": "split", "shot": "a", "at": 2.0}], segs)
    out = edits.apply_ops(segs, None, ops)["segments"]
    assert [s["controls"] for s in out] == [{"scale": 50, "fx_off": ["x"]}] * 2
    out[1]["controls"]["scale"] = 75
    out[1]["controls"]["fx_off"].append("y")
    assert out[0]["controls"] == {"scale": 50, "fx_off": ["x"]}
    assert segs[0]["controls"] == {"scale": 50, "fx_off": ["x"]}
    moved = edits.apply_ops(segs, None, edits.validate_ops(
        [{"op": "extend", "shot": "a", "in": 0.0, "out": -1.0}], segs))["segments"]
    assert moved[0]["controls"] == {"scale": 50, "fx_off": ["x"]}
