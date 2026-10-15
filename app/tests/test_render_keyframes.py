"""Keyframes and Lumetri tone render as the schema says (verdict 5, punch 4; wave 6 lane
RENDER2).

The schema (judge/LOOP.md, wave 6): `segments[i].controls.keys = {prop: [[t, v], ...]}`,
t = seconds from the shot's start in film time, sorted, linear between keys and held
outside them; Lumetri Basic adds highlights / shadows / whites / blacks (-100..100).

A cut of two-second shots, all the same two seconds of CLIP_A, is rendered twice the way
the board renders — `assemble.py` as a subprocess, the Match Sequence profile so the
frame is the fixture's own 320×180 at 24 fps — once with each shot carrying keys or a
tone slider and once without. Each is measured off the two films at the same frame, so
the fixture's own motion cancels out. Render side only: the EDL validation of `keys` is
lane KEYUI's.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

TOOLS = Path(__file__).resolve().parents[2] / "research" / "tools"
sys.path.insert(0, str(TOOLS))
W, H, FPS = 320, 180, 24
SHOT_S = 2

CONTROLS = [
    None,                                                          # 0 plain
    {"keys": {"opacity": [[0, 0], [1, 100]]}},                     # 1 fade up over 1 s
    {"keys": {"scale": [[0, 100], [1, 50]]}},                      # 2 shrink to half
    {"keys": {"volume_db": [[0, -30], [1, 0]]}},                   # 3 sound ramps up
    {"highlights": -100},                                          # 4
    {"shadows": 100},                                              # 5
    {"keys": {"rotation": [[0, 0], [1, 180]]}},                    # 6 a half turn
    {"keys": {"position_x": [[0, 0.5], [1, 0.75]]}},               # 7 slides right
    {"opacity": 50, "keys": {"scale": [[0, 100], [0.5, 100]]}},    # 8 keyed motion + static opacity
]


def _edl(with_controls: bool) -> dict:
    segs = []
    for i, c in enumerate(CONTROLS):
        s = {"id": f"s{i}", "clip": "CLIP_A.MP4", "in": 1.0, "out": 1.0 + SHOT_S}
        if with_controls and c:
            s["controls"] = c
        segs.append(s)
    return {"variant": "K", "title": "keys", "orient": "none", "target_s": [1, 30],
            "segments": segs}


def _render(edl: dict, root: Path, name: str, project: dict) -> tuple[Path, str]:
    edl_path = root / f"{name}.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    out = root / f"{name}.mp4"
    r = subprocess.run(["uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(edl_path),
                        "--footage", str(project["footage"]), "--profile", "match",
                        "-o", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return out, r.stdout


@pytest.fixture(scope="module")
def films(project, tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("keys")
    with_, log = _render(_edl(True), root, "with", project)
    base, _ = _render(_edl(False), root, "base", project)
    return {"with": with_, "base": base, "log": log}


def frame(path: Path, shot: int, t: float) -> np.ndarray:
    """The frame `t` seconds into `shot` as (Y, U, V) planes, full resolution."""
    k = shot * SHOT_S * FPS + int(round(t * FPS))
    # an output -ss drops every frame before it: a quarter frame early is frame k itself
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
                        "-ss", f"{max(0.0, (k - 0.25) / FPS):.4f}", "-frames:v", "1", "-f", "rawvideo",
                        "-pix_fmt", "yuv444p", "-"], capture_output=True, check=True)
    a = np.frombuffer(r.stdout, np.uint8).astype(np.float64)
    assert a.size == 3 * W * H, a.size
    return a.reshape(3, H, W)


def mad(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(np.abs(a - b)))


def mean_volume(path: Path, start: float, length: float) -> float:
    r = subprocess.run(["ffmpeg", "-v", "info", "-nostdin", "-ss", f"{start:.3f}",
                        "-t", f"{length:.3f}", "-i", str(path), "-af", "volumedetect",
                        "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"mean_volume: (-?[\d.]+) dB", r.stderr)
    assert m, r.stderr[-400:]
    return float(m.group(1))


# ---------------------------------------------------------------- Opacity

def test_two_opacity_keys_are_half_the_picture_at_the_midpoint(films):
    """0 → 100 over 1 s: the frame at 0.5 s is 50 % of the picture by luma (±10 %),
    black at the first key and the whole picture after the last."""
    got, ref = frame(films["with"], 1, 0.5), frame(films["base"], 1, 0.5)
    ratio = float((got[0] - 16).mean() / (ref[0] - 16).mean())
    assert 0.45 <= ratio <= 0.55, ratio
    assert mad(got[1:] - 128, (ref[1:] - 128) * 0.5) < 2.5
    assert (frame(films["with"], 1, 0.0)[0] - 16).mean() < 3            # the first key: black
    quarter = frame(films["with"], 1, 0.25)[0] - 16
    assert 0.18 <= quarter.mean() / (frame(films["base"], 1, 0.25)[0] - 16).mean() <= 0.32
    assert mad(frame(films["with"], 1, 1.5), frame(films["base"], 1, 1.5)) < 1.0   # held after


# ---------------------------------------------------------------- Motion

def _ring(y: np.ndarray, s: float) -> np.ndarray:
    """The pixels outside the centred box a picture scaled by `s` covers (2 px margin)."""
    bw, bh = W * s / 2, H * s / 2
    m = np.ones_like(y, dtype=bool)
    m[int(H / 2 - bh) - 2:int(np.ceil(H / 2 + bh)) + 2, int(W / 2 - bw) - 2:int(np.ceil(W / 2 + bw)) + 2] = False
    return y[m]


def test_a_scale_key_animates(films):
    """100 % → 50 % over 1 s: the whole picture at the first key, 75 % at 0.5 s and half
    size, centred on black, from the last key on."""
    start = frame(films["with"], 2, 0.0)
    assert mad(start, frame(films["base"], 2, 0.0)) < 3, mad(start, frame(films["base"], 2, 0.0))
    for t, s in ((0.5, 0.75), (1.5, 0.5)):
        y = frame(films["with"], 2, t)[0]
        ring = _ring(y, s)
        assert ring.mean() < 18 and ring.max() < 40, (t, ring.mean(), ring.max())
        assert y[H // 2 - 10:H // 2 + 10, W // 2 - 10:W // 2 + 10].mean() > 30     # the picture is there
    # at the last key it is the static Scale 50's picture: the reference at half size
    got, ref = frame(films["with"], 2, 1.5)[0], frame(films["base"], 2, 1.5)[0]
    half = ref.reshape(H // 2, 2, W // 2, 2).mean(axis=(1, 3))
    assert mad(got[45:135, 80:240], half) < 10, mad(got[45:135, 80:240], half)


def test_a_rotation_key_turns_the_picture_about_its_centre(films):
    got, ref = frame(films["with"], 6, 1.5), frame(films["base"], 6, 1.5)
    inner = (slice(None), slice(4, H - 4), slice(4, W - 4))
    turned = ref[:, ::-1, ::-1]
    assert mad(got[inner], turned[inner]) < 6, mad(got[inner], turned[inner])
    assert mad(got[inner], ref[inner]) > 15
    assert mad(frame(films["with"], 6, 0.0), frame(films["base"], 6, 0.0)) < 3


def test_a_position_key_slides_the_picture(films):
    got, ref = frame(films["with"], 7, 1.5), frame(films["base"], 7, 1.5)
    assert got[0][:, :78].mean() < 18                     # the left quarter is black
    assert mad(got[0][:, 82:], ref[0][:, 2:240]) < 4, mad(got[0][:, 82:], ref[0][:, 2:240])
    mid = frame(films["with"], 7, 0.5)[0]                  # 0.625: an eighth of the frame over
    assert mid[:, :38].mean() < 18 and mid[:, 44:].mean() > 30


def test_keyed_motion_keeps_a_static_opacity(films):
    got, ref = frame(films["with"], 8, 1.0), frame(films["base"], 8, 1.0)
    inner = (slice(4, H - 4), slice(4, W - 4))
    assert mad((got[0] - 16)[inner], ((ref[0] - 16) * 0.5)[inner]) < 3


# ---------------------------------------------------------------- Volume

def test_a_volume_key_ramps_the_sound(films):
    """-30 dB → 0 dB over 1 s: the shot's first 0.3 s is far quieter than the reference,
    its second half as loud."""
    t0 = 3 * SHOT_S
    early = mean_volume(films["with"], t0 + 0.02, 0.25) - mean_volume(films["base"], t0 + 0.02, 0.25)
    late = mean_volume(films["with"], t0 + 1.2, 0.7) - mean_volume(films["base"], t0 + 1.2, 0.7)
    assert early < -15, early
    assert abs(late) <= 1.0, late
    mid = mean_volume(films["with"], t0 + 0.45, 0.1) - mean_volume(films["base"], t0 + 0.45, 0.1)
    assert -20 < mid < -10, mid                            # -15 dB at 0.5 s, on the line


# ---------------------------------------------------------------- Lumetri tone

def test_highlights_down_darkens_the_bright_end_of_the_histogram(films):
    got, ref = frame(films["with"], 4, 1.0)[0], frame(films["base"], 4, 1.0)[0]
    bright, dark = ref > 170, ref < 50
    assert bright.sum() > 500 and dark.sum() > 500
    assert (got[bright] - ref[bright]).mean() < -8, (got[bright] - ref[bright]).mean()
    assert abs((got[dark] - ref[dark]).mean()) < 4
    assert np.percentile(got, 90) < np.percentile(ref, 90) - 5


def test_shadows_up_lifts_the_dark_end_of_the_histogram(films):
    got, ref = frame(films["with"], 5, 1.0)[0], frame(films["base"], 5, 1.0)[0]
    shadow, bright = (ref > 30) & (ref < 110), ref > 200
    assert shadow.sum() > 500
    assert (got[shadow] - ref[shadow]).mean() > 8, (got[shadow] - ref[shadow]).mean()
    if bright.sum() > 100:
        assert abs((got[bright] - ref[bright]).mean()) < 4
    assert np.percentile(got, 25) > np.percentile(ref, 25) + 5


def test_the_log_names_the_keys(films):
    assert "opacity keyed ×2 0@0s→100@1s" in films["log"], films["log"]
    assert "highlights -100" in films["log"]


# ---------------------------------------------------------------- the pieces

def test_value_at_and_its_expression_agree():
    import assemble
    keys = [(0.5, 10.0), (1.0, 30.0), (1.0, 50.0), (2.0, 0.0)]
    assert assemble.value_at(keys, 0.0) == 10.0             # held before the first key
    assert assemble.value_at(keys, 0.75) == pytest.approx(20.0)
    assert assemble.value_at(keys, 1.5) == pytest.approx(25.0)
    assert assemble.value_at(keys, 9.0) == 0.0               # held after the last
    expr = assemble.lerp_expr(keys, "T")
    # evaluate ffmpeg's expression in Python: clip/gte are its only functions here
    env = {"clip": lambda x, a, b: min(b, max(a, x)), "gte": lambda a, b: float(a >= b)}
    for t in (0.0, 0.5, 0.75, 0.99, 1.0, 1.01, 1.5, 2.0, 3.0):
        assert eval(expr, {**env, "T": t}) == pytest.approx(assemble.value_at(keys, t)), t
    assert assemble.keys_of({"keys": {"scale": [[1, 50], [0, 100]]}}, "scale") == [(0, 100), (1, 50)]
    assert assemble.keys_of({"keys": {}}, "scale") is None
    assert assemble.volume_db({"volume_db": -6, "keys": {"volume_db": [[0, -3]]}}) == 0.0
    assert "eval=frame" in assemble.volume_vf({"keys": {"volume_db": [[0, -3], [1, 0]]}})


def test_the_tone_curve_is_grade_js_formula():
    """KEYUI's shader (grade.js), per channel: y = x + 0.25·b·(1−x)³ + 1.5·s·x(1−x)²
    + 1.5·h·x²(1−x) + 0.25·w·x³, after contrast, before saturation."""
    import assemble
    tc = assemble.tone_curve
    for x in (0.0, 0.3, 0.5, 0.9, 1.0):
        assert tc(x) == pytest.approx(x)                     # all zero: identity
    for kw in ({"shadows": 100}, {"shadows": -100}, {"highlights": 100}, {"highlights": -100}):
        assert tc(0.0, **kw) == pytest.approx(0.0) and tc(1.0, **kw) == pytest.approx(1.0)
    assert tc(1 / 3, shadows=100) - 1 / 3 == pytest.approx(0.2222, abs=1e-4)
    assert tc(1 / 3, shadows=-100) - 1 / 3 == pytest.approx(-0.2222, abs=1e-4)
    assert tc(2 / 3, highlights=100) - 2 / 3 == pytest.approx(0.2222, abs=1e-4)
    assert tc(0.0, blacks=100) == pytest.approx(0.25) and tc(1.0, whites=-100) == pytest.approx(0.75)
    assert tc(1.4, shadows=50) == pytest.approx(1.0)        # clamped to 0..1 first
    # the render's table: contrast, then the curve, per 8-bit channel value
    c = {"contrast": 50, "shadows": 100}
    table = assemble.channel_table(c)
    for v in (0, 40, 85, 128, 200, 255):
        y = (v / 255 - 0.5) * 1.25 + 0.5
        want = min(255, max(0, 255 * tc(y, shadows=100)))
        assert abs(table[v] - want) <= 0.5 + 1e-9, (v, table[v], want)
    assert assemble.adjust_vf({"contrast": 50}).startswith("lutyuv=")      # no tone: YUV as before
    vf = assemble.adjust_vf({"highlights": -40, "saturation": 50})
    assert "format=gbrp,lutrgb=r='if(lt(val," in vf and "colorchannelmixer=" in vf, vf[:200]
    assert assemble.saturation_mixer(1.0) is None


@pytest.mark.parametrize("controls,want", [
    ({"shadows": 100}, 1 / 3 + 0.2222), ({"shadows": -100}, 1 / 3 - 0.2222),
    ({"highlights": 100}, 1 / 3 + 1.5 * (1 / 9) * (2 / 3)), ({"blacks": 100}, 1 / 3 + 0.25 * (8 / 27)),
])
def test_a_grey_at_a_third_moves_as_the_shader_says(project, tmp_path, controls, want):
    """A flat grey at RGB 1/3 (limited Y 89) through the export: its luma is the formula's
    value (grade.probe-equivalent maths), within 2 code values."""
    foot = tmp_path / "footage"
    foot.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi", "-i",
                    "color=c=0x555555:size=320x180:rate=24:duration=2", "-f", "lavfi", "-i",
                    "anullsrc=r=48000:cl=stereo", "-t", "2", "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", "-color_range", "tv", "-colorspace", "bt709", "-c:a", "aac",
                    str(foot / "GREY.MP4")], check=True, capture_output=True)
    edl = {"variant": "G", "title": "grey", "orient": "none", "segments": [
        {"id": "g", "clip": "GREY.MP4", "in": 0.0, "out": 1.0, "controls": controls}]}
    src = tmp_path / "g.json"
    src.write_text(json.dumps(edl), encoding="utf-8")
    out = tmp_path / "g.mp4"
    r = subprocess.run(["uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(src), "--footage",
                        str(foot), "--profile", "match", "-o", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    planes = frame(out, 0, 0.5)
    y = planes[0][20:-20, 20:-20].mean()
    assert abs(y - (16 + 219 * want)) <= 2.0, (controls, y, 16 + 219 * want)
    assert abs(planes[1:].mean() - 128) < 1.5                 # a grey stays grey


# ------------------------------------------- a keyed property that never changes value
#
# Verdict 7, blocker 2: one click on Opacity's stopwatch stores ONE key, `opacity_commands`
# wrote an empty `sendcmd` file for it and ffmpeg refused the graph ("Invalid argument"),
# so every export with such a shot failed. A keyed property whose value is the same on
# every frame of the part (one key, keys of equal value, keys that all lie past the
# part's end) now renders exactly as the static value does. Each case is rendered next to
# the same cut with the static control in its place, and must be that picture (and, for
# the Volume, that loudness); on V1 and on a V2+ clip.

HELD = [
    # (keyed controls, the static controls it must equal)
    ({"keys": {"opacity": [[0.5, 60]]}}, {"opacity": 60}),                      # 0 one key
    ({"keys": {"opacity": [[0, 60], [1, 60]]}}, {"opacity": 60}),               # 1 two equal keys
    ({"keys": {"opacity": [[3, 60], [4, 10]]}}, {"opacity": 60}),               # 2 held over the part
    ({"keys": {"opacity": [[0.5, 100]]}}, None),                                # 3 one key at 100
    ({"keys": {"opacity": [[0.5, 0]]}}, {"opacity": 0}),                        # 4 one key at 0
    ({"keys": {"scale": [[0.5, 50]]}}, {"scale": 50}),                          # 5
    ({"keys": {"scale": [[0, 50], [1, 50]]}}, {"scale": 50}),                   # 6
    ({"keys": {"scale": [[3, 50], [4, 100]]}}, {"scale": 50}),                  # 7
    ({"keys": {"rotation": [[0.5, 180]]}}, {"rotation": 180}),                  # 8
    ({"keys": {"position_x": [[0.5, 0.75]]}}, {"position": [0.75, 0.5]}),      # 9
    ({"keys": {"position_y": [[0, 0.3], [1, 0.3]]}}, {"position": [0.5, 0.3]}), # 10
    ({"keys": {"scale": [[0, 50]], "opacity": [[0, 60]]}},
     {"scale": 50, "opacity": 60}),                                             # 11 both, one key each
    ({"keys": {"volume_db": [[0.5, -20]]}}, {"volume_db": -20}),                # 12
    ({"keys": {"volume_db": [[0, -20], [1, -20]]}}, {"volume_db": -20}),        # 13
    ({"keys": {"volume_db": [[3, -20], [4, 0]]}}, {"volume_db": -20}),          # 14
]


def _held_edl(keyed: bool) -> dict:
    segs = []
    for i, (k, s) in enumerate(HELD):
        seg = {"id": f"h{i}", "clip": "CLIP_A.MP4", "in": 1.0, "out": 1.0 + SHOT_S}
        c = k if keyed else s
        if c:
            seg["controls"] = c
        segs.append(seg)
    return {"variant": "H", "title": "held keys", "orient": "none", "target_s": [1, 60],
            "segments": segs}


@pytest.fixture(scope="module")
def held(project, tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("held")
    keyed, log = _render(_held_edl(True), root, "keyed", project)
    static, _ = _render(_held_edl(False), root, "static", project)
    base, _ = _render(_edl(False), root, "base", project)
    return {"keyed": keyed, "static": static, "base": base, "log": log}


@pytest.mark.parametrize("shot", [i for i, (k, _) in enumerate(HELD)
                                  if "volume_db" not in k["keys"]])
def test_a_held_key_renders_the_static_picture_on_v1(held, shot):
    for t in (0.1, 1.0, 1.9):
        got, want = frame(held["keyed"], shot, t), frame(held["static"], shot, t)
        assert mad(got, want) < 1.0, (HELD[shot][0], t, mad(got, want))
    # and it is the control's picture, not the plain shot (one key at 100 is the plain shot)
    plain = frame(held["base"], 0, 1.0)
    if HELD[shot][1] is None:
        assert mad(frame(held["keyed"], shot, 1.0), plain) < 1.0
    else:
        assert mad(frame(held["keyed"], shot, 1.0), plain) > 5.0, HELD[shot]


def test_a_held_opacity_is_60_percent_of_the_picture(held):
    ref = frame(held["base"], 0, 1.0)
    for shot in (0, 1, 2):
        got = frame(held["keyed"], shot, 1.0)
        ratio = float((got[0] - 16).mean() / (ref[0] - 16).mean())
        assert 0.57 <= ratio <= 0.63, (shot, ratio)
    assert (frame(held["keyed"], 4, 1.0)[0] - 16).mean() < 2      # one key at 0: black


@pytest.mark.parametrize("shot", [12, 13, 14])
def test_a_held_volume_key_is_the_static_volume(held, shot):
    t0 = shot * SHOT_S
    got = mean_volume(held["keyed"], t0 + 0.2, 1.6)
    want = mean_volume(held["static"], t0 + 0.2, 1.6)
    plain = mean_volume(held["base"], 0.2, 1.6)
    assert abs(got - want) <= 0.5, (shot, got, want)
    assert got < plain - 15, (shot, got, plain)


def test_a_constant_key_is_folded_to_its_static_value():
    """`held_controls`: a key whose value never changes over the part becomes the static
    value (Motion's warp and Opacity's sendcmd are not built for it); one that changes,
    even past the part, stays keyed; the part's window is what decides."""
    import assemble
    c = {"opacity": 20, "keys": {"opacity": [[0.5, 60]], "scale": [[0, 100], [1, 50]],
                                 "position_x": [[3, 0.2], [4, 0.9]],
                                 "volume_db": [[0, -6], [1, -6]]}}
    got = assemble.held_controls(c, 0.0, 2.0)
    assert got["opacity"] == 60 and got["position"] == [0.2, 0.5] and got["volume_db"] == -6
    assert got["keys"] == {"scale": [[0, 100], [1, 50]]}
    assert c["keys"]["opacity"] == [[0.5, 60]]                     # the EDL's own is untouched
    # a piece of the clip that starts past the ramp: the scale is held at 50 there
    late = assemble.held_controls(c, 1.5, 3.0)
    assert late["scale"] == 50 and "keys" not in late
    # a ramp inside the window stays keyed
    assert "position_x" in assemble.held_controls(c, 2.0, 5.0)["keys"]
    # a step at one instant inside the window is a change
    step = {"keys": {"opacity": [[1, 60], [1, 20]]}}
    assert assemble.held_controls(step, 0.0, 2.0) == step
    assert assemble.held_controls(None, 0.0, 2.0) is None
    # the sendcmd file is never empty: a series that does not change writes none
    import tempfile
    from fractions import Fraction
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "op.cmd"
        lut, changes = assemble.opacity_commands([(0.5, 60.0)], 48, Fraction(24), p)
        assert not changes and not p.exists() and "0.6" in lut
        lut, changes = assemble.opacity_commands([(0.0, 0.0), (1.0, 100.0)], 48, Fraction(24), p)
        assert changes and p.read_text(encoding="utf-8").strip()


# ---- V2+: the same, composited over V1 (red under blue)

V2_HELD = [
    ({"keys": {"opacity": [[0.5, 60]]}}, {"opacity": 60}),                      # 0 one key
    ({"keys": {"opacity": [[0, 60], [1, 60]]}}, {"opacity": 60}),               # 1 two equal keys
    ({"keys": {"opacity": [[3, 60], [4, 0]]}}, {"opacity": 60}),                # 2 held over the clip
    ({"keys": {"opacity": [[0.5, 100]]}}, None),                                # 3 one key at 100
    ({"keys": {"scale": [[0.5, 50]], "opacity": [[0.2, 60]]}},
     {"scale": 50, "opacity": 60}),                                             # 4 both
    ({"keys": {"scale": [[0, 50], [1, 50]]}}, {"scale": 50}),                   # 5
]
V2_LEN, V2_GAP = 1.5, 2.0       # each overlay 1.5 s, one every 2 s from film 0.25


def _colour_clip(path: Path, colour: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi", "-i",
                    f"color=c={colour}:size={W}x{H}:rate={FPS}:duration=14", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=14", "-c:v", "libx264", "-preset",
                    "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
                   check=True, capture_output=True)


def _v2_edl(keyed: bool) -> dict:
    ovs = []
    for i, (k, s) in enumerate(V2_HELD):
        o = {"id": f"o{i}", "clip": "BLUE.MP4", "in": 1.0, "out": 1.0 + V2_LEN,
             "at": 0.25 + i * V2_GAP}
        c = k if keyed else s
        if c:
            o["controls"] = c
        ovs.append(o)
    return {"variant": "V", "title": "held overlay keys", "orient": "none", "target_s": [1, 60],
            "segments": [{"id": "v1", "clip": "RED.MP4", "in": 0.0, "out": 12.5}],
            "overlays": ovs}


@pytest.fixture(scope="module")
def v2_held(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("v2held")
    foot = root / "footage"
    foot.mkdir()
    _colour_clip(foot / "RED.MP4", "red")
    _colour_clip(foot / "BLUE.MP4", "blue")
    proj = {"footage": foot}
    keyed, log = _render(_v2_edl(True), root, "keyed", proj)
    static, _ = _render(_v2_edl(False), root, "static", proj)
    return {"keyed": keyed, "static": static, "log": log}


def _rgb_at(path: Path, t: float) -> np.ndarray:
    k = int(round(t * FPS))
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
                        "-ss", f"{max(0.0, (k - 0.25) / FPS):.4f}", "-frames:v", "1",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).astype(np.float64).reshape(H, W, 3)


@pytest.mark.parametrize("i", range(len(V2_HELD)))
def test_a_held_key_on_a_v2_clip_renders_the_static_picture(v2_held, i):
    t = 0.25 + i * V2_GAP + V2_LEN / 2
    got, want = _rgb_at(v2_held["keyed"], t), _rgb_at(v2_held["static"], t)
    assert float(np.mean(np.abs(got - want))) < 1.5, (V2_HELD[i][0], float(np.mean(np.abs(got - want))))
    red = _rgb_at(v2_held["static"], 0.1)[H // 2, W // 2]
    centre = got[H // 2 - 6:H // 2 + 6, W // 2 - 6:W // 2 + 6].reshape(-1, 3).mean(axis=0)
    blue = np.array([0.0, 0.0, 255.0])
    if V2_HELD[i][1] is None:                                   # opaque, the whole frame
        assert np.all(np.abs(centre - _rgb_at(v2_held["keyed"], t)[5, 5]) < 6)
        assert centre[2] > 200 and centre[0] < 40, centre
    elif V2_HELD[i][1].get("opacity"):
        # 60 % blue over red: a mix, never blue blended toward black
        assert centre[0] > 0.3 * red[0] and centre[2] > 0.45 * 255, (centre, red)
        assert abs(centre[0] - 0.4 * red[0]) < 26 and abs(centre[2] - (0.6 * blue[2] + 0.4 * red[2])) < 26
    if V2_HELD[i][1] and V2_HELD[i][1].get("scale") == 50:      # half size: V1 at the corner
        assert np.all(np.abs(got[5, 5] - red) < 12), (got[5, 5], red)
