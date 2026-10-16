"""Lumetri beyond Basic Correction: the formula, the schema and the render (redesign wave
11, lane LUMETRI; verdict 8 punch 4, REDESIGN-GOAL dimension 7).

Curves (RGB master + R, G, B), Color Wheels (Shadows, Midtones, Highlights + lightness)
and HSL Secondary (a key and its correction) ride the segment's `controls`; THE formula
is roughcut/colour.py's ("Lumetri beyond Basic"), drawn by grade.js's shader on the board
and by research/tools/assemble.py in the export. Here: the validators server.py's
`clean_controls` calls, the formula's own properties, and flat colour fields through the
real export, measured against the formula (the board half is test_lumetri_ui.py).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "research" / "tools"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(ROOT / "app"))

from roughcut import colour  # noqa: E402

W, H, FPS = 320, 180, 24


# ------------------------------------------------------------------ the schema

@pytest.mark.parametrize("bad", [
    "nope", {"luma": [[0, 0], [1, 1]]}, {"master": [[0, 0]]}, {"master": [[0, 0], [1, 1.2]]},
    {"master": [[0, 0], [0.5, 0.5], [0.5, 0.6], [1, 1]]}, {"master": [[0.6, 0], [0.4, 1]]},
    {"r": [[0, 0], [1, True]]}, {"g": [[0, 0], [1, float("nan")]]}, {"b": [[0, 0, 0], [1, 1]]},
    {"master": [[i / 20, i / 20] for i in range(21)]},
])
def test_bad_curves_are_refused(bad):
    with pytest.raises(ValueError):
        colour.clean_curves(bad)


@pytest.mark.parametrize("bad", [
    [0, 0, 0], {"lift": [0, 0, 0]}, {"shadows": [0.9, 0.9, 0]}, {"midtones": [0, 0, 101]},
    {"highlights": [0, 0]}, {"shadows": ["0", 0, 0]},
])
def test_bad_wheels_are_refused(bad):
    with pytest.raises(ValueError):
        colour.clean_wheels(bad)


@pytest.mark.parametrize("bad", [
    {"glow": 1}, {"hue": 400}, {"width": -1}, {"saturation": 250}, {"shift": 181},
    {"temperature": -101}, {"sat_lo": 60, "sat_hi": 40}, {"light_lo": 80, "light_hi": 20},
    {"tint": "10"}, [1],
])
def test_bad_hsl_is_refused(bad):
    with pytest.raises(ValueError):
        colour.clean_hsl(bad)


def test_defaults_are_the_absence_of_the_key():
    assert colour.clean_curves({"master": [[0, 0], [1, 1]]}) is None
    assert colour.clean_curves({"r": [[0, 0], [0.5, 0.6], [1, 1]]}) == {"r": [[0, 0], [0.5, 0.6], [1, 1]]}
    assert colour.clean_wheels({"shadows": [0, 0, 0], "midtones": [0.1, -0.2, 5]}) == {"midtones": [0.1, -0.2, 5]}
    assert colour.clean_hsl({"hue": 0, "width": 30, "shift": 0}) is None
    # a key set up before its correction is kept (the render does nothing with it)
    assert colour.clean_hsl({"hue": 120}) == {"hue": 120}
    assert colour.hsl_of({"hue": 120}) is None
    assert colour.clean_hsl({"hue": 120, "shift": -30}) == {"hue": 120, "shift": -30}


def test_server_clean_controls_takes_them_and_refuses_them_on_v2():
    import server
    c = server.clean_controls({"curves": {"master": [[0, 0], [0.5, 0.7], [1, 1]]},
                               "wheels": {"midtones": [0, 0.5, 0]}, "hsl": {"shift": 90},
                               "contrast": 10})
    assert c == {"contrast": 10, "curves": {"master": [[0, 0], [0.5, 0.7], [1, 1]]},
                 "wheels": {"midtones": [0, 0.5, 0]}, "hsl": {"shift": 90}}
    for bad in ({"curves": {"master": [[0, 0], [1, 2]]}}, {"wheels": {"shadows": [1, 1, 0]}},
                {"hsl": {"saturation": -1}}):
        with pytest.raises(ValueError):
            server.clean_controls(bad)
    with pytest.raises(ValueError, match="V1"):
        server.clean_controls({"curves": {"master": [[0, 0], [0.5, 0.7], [1, 1]]}}, overlay=True)
    assert server.clean_controls({"opacity": 50}, overlay=True) == {"opacity": 50}


# ------------------------------------------------------------------ the formula

def test_a_curve_passes_through_its_points_and_holds_past_its_ends():
    pts = [[0.1, 0.2], [0.4, 0.5], [0.7, 0.6], [0.9, 0.95]]
    x = np.array([p[0] for p in pts])
    assert np.allclose(colour.curve_eval(pts, x), [p[1] for p in pts])
    assert np.allclose(colour.curve_eval(pts, np.array([0.0, 0.05, 0.95, 1.0])), [0.2, 0.2, 0.95, 0.95])
    v = np.linspace(0, 1, 257)
    assert np.allclose(colour.curve_eval([[0, 0], [1, 1]], v), v)
    # monotone points give a monotone curve: PCHIP never overshoots
    y = colour.curve_eval(pts, v)
    assert np.all(np.diff(y) >= -1e-12)
    # an S: a dip and a rise, no ringing past the points
    s = colour.curve_eval([[0, 0], [0.25, 0.15], [0.75, 0.85], [1, 1]], v)
    assert s.min() >= 0 and s.max() <= 1 and np.all(np.diff(s) >= -1e-12)


def test_wheels_push_the_vectorscope_way_and_lightness_is_grey():
    # the puck up is red (+Cr), right is blue (+Cb); every push has zero luma
    for cb, cr in ((0, 1), (1, 0), (-0.6, -0.6)):
        o = colour.wheel_rgb(cb, cr)
        assert abs(colour.KR * o[0] + (1 - colour.KR - colour.KB) * o[1] + colour.KB * o[2]) < 1e-12
    assert colour.wheel_rgb(0, 1)[0] > 0 and colour.wheel_rgb(1, 0)[2] > 0
    grey = np.full((1, 3), 0.5)
    up = colour.creative_channels(grey, {"wheels": {"midtones": [0, 0, 50]}})
    assert np.allclose(up, up[0, 0]) and up[0, 0] > 0.5                  # lightness: grey stays grey
    warm = colour.creative_channels(grey, {"wheels": {"midtones": [0, 0.6, 0]}})[0]
    assert warm[0] > 0.5 > warm[1] and abs(warm[2] - 0.5) < 1e-9     # toward red: +R, −G
    # shadows move the darks more than the brights; highlights the other way round
    lo, hi = np.array([[0.1] * 3]), np.array([[0.9] * 3])
    sh = {"wheels": {"shadows": [0, 0, 60]}}
    assert colour.creative_channels(lo, sh)[0, 0] - 0.1 > colour.creative_channels(hi, sh)[0, 0] - 0.9
    hl = {"wheels": {"highlights": [0, 0, -60]}}
    assert 0.1 - colour.creative_channels(lo, hl)[0, 0] < 0.9 - colour.creative_channels(hi, hl)[0, 0]


def test_the_hsl_secondary_keys_one_hue_and_keeps_luma():
    red, blue, grey = [1.0, 0, 0], [0, 0, 1.0], [0.5, 0.5, 0.5]
    rgb = np.array([red, blue, grey])
    out = colour.hsl_secondary(rgb, {"hue": 0, "shift": 120})
    assert out[0, 1] > out[0, 0] and out[0, 1] > out[0, 2]            # red turned green
    assert np.allclose(out[1:], rgb[1:])                               # blue and grey untouched
    luma = lambda c: colour.KR * c[0] + (1 - colour.KR - colour.KB) * c[1] + colour.KB * c[2]
    soft_red = np.array([[0.7, 0.3, 0.3]])                             # in gamut after the turn
    assert abs(luma(colour.hsl_secondary(soft_red, {"hue": 0, "shift": 120})[0]) - luma(soft_red[0])) < 1e-6
    desat = colour.hsl_secondary(rgb, {"hue": 0, "saturation": 0})[0]
    assert np.ptp(desat) < 0.01                                        # red to grey
    # the soft edge: a hue just outside the range is partly keyed, far outside not at all
    near = colour.hsl_secondary(np.array([[1.0, 0.65, 0]]), {"hue": 0, "width": 30, "soft": 50, "saturation": 0})
    far = colour.hsl_secondary(np.array([[0, 1.0, 0]]), {"hue": 0, "width": 30, "soft": 50, "saturation": 0})
    assert 0 < np.ptp(near) < 1 and np.allclose(far, [[0, 1, 0]])


# ------------------------------------------------------------------ the render

def _flat(foot: Path, name: str, rgb_hex: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi", "-i",
                    f"color=c=0x{rgb_hex}:size={W}x{H}:rate={FPS}:duration=2", "-f", "lavfi", "-i",
                    "anullsrc=r=48000:cl=stereo", "-t", "2", "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", "-color_range", "tv", "-colorspace", "bt709", "-c:a", "aac",
                    str(foot / f"{name}.MP4")], check=True, capture_output=True)


def _render(tmp: Path, foot: Path, segments: list[dict]) -> Path:
    edl = {"variant": "L", "title": "lumetri", "orient": "none", "segments": segments}
    src = tmp / "l.json"
    src.write_text(json.dumps(edl), encoding="utf-8")
    out = tmp / "l.mp4"
    r = subprocess.run(["uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(src), "--footage",
                        str(foot), "--profile", "match", "-o", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return out


def _rgb(path: Path, t: float) -> np.ndarray:
    k = int(round(t * FPS))
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
                        "-ss", f"{max(0.0, (k - 0.25) / FPS):.4f}", "-frames:v", "1",
                        "-vf", "scale=in_range=tv:out_range=pc:in_color_matrix=bt709",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
    a = np.frombuffer(r.stdout, np.uint8).astype(np.float64).reshape(H, W, 3)
    return a[20:-20, 20:-20].reshape(-1, 3).mean(axis=0)


CASES = [
    {"curves": {"master": [[0, 0], [0.5, 0.7], [1, 1]]}},
    {"curves": {"r": [[0, 0], [0.5, 0.35], [1, 1]], "b": [[0, 0.1], [1, 1]]}},
    {"wheels": {"midtones": [0, 0.7, 0]}},
    {"wheels": {"shadows": [0.5, 0, -40], "highlights": [0, 0, 30]}},
    {"curves": {"master": [[0, 0], [0.4, 0.3], [1, 1]]}, "wheels": {"midtones": [-0.4, 0, 20]},
     "contrast": 20, "saturation": 120},
]


def _expected(rgb255: np.ndarray, controls: dict) -> np.ndarray:
    """THE formula on a flat colour, after Basic (contrast, saturation) as grade.js has it."""
    import assemble
    x = rgb255 / 255.0
    y = np.array([assemble.grade_channel(v, controls) for v in x])
    sat = assemble.ctl_num(controls, "saturation", 100.0) / 100.0
    lum = colour.KR * y[0] + (1 - colour.KR - colour.KB) * y[1] + colour.KB * y[2]
    y = np.clip(lum + sat * (y - lum), 0, 1)
    y = colour.creative_channels(y[None], controls)
    y = colour.hsl_secondary(y, (controls or {}).get("hsl"))
    return y[0] * 255.0


def test_curves_and_wheels_export_as_the_formula_says(tmp_path):
    """A flat grey and a flat orange through each case, rendered as the board renders:
    each channel within 2.5 code values of THE formula on the plain export's colour."""
    foot = tmp_path / "footage"
    foot.mkdir()
    _flat(foot, "GREY", "707070")
    _flat(foot, "ORANGE", "c86432")
    segs = []
    for i, c in enumerate([None] + CASES):
        for clip in ("GREY", "ORANGE"):
            s = {"id": f"{clip}{i}", "clip": f"{clip}.MP4", "in": 0.0, "out": 1.0}
            if c:
                s["controls"] = c
            segs.append(s)
    out = _render(tmp_path, foot, segs)
    plain = {"GREY": _rgb(out, 0.5), "ORANGE": _rgb(out, 1.5)}
    for i, c in enumerate(CASES, start=1):
        for j, clip in enumerate(("GREY", "ORANGE")):
            got = _rgb(out, (2 * i + j) + 0.5)
            want = _expected(plain[clip], c)
            assert np.all(np.abs(got - want) <= 2.5), (c, clip, got, want, plain[clip])


def test_the_hsl_secondary_exports_through_its_cube(tmp_path):
    foot = tmp_path / "footage"
    foot.mkdir()
    _flat(foot, "RED", "e01010")
    _flat(foot, "BLUE", "1010e0")
    hsl = {"hue": 0, "width": 30, "shift": 120, "saturation": 80}
    out = _render(tmp_path, foot, [
        {"id": "r0", "clip": "RED.MP4", "in": 0.0, "out": 1.0},
        {"id": "r1", "clip": "RED.MP4", "in": 0.0, "out": 1.0, "controls": {"hsl": hsl}},
        {"id": "b1", "clip": "BLUE.MP4", "in": 0.0, "out": 1.0, "controls": {"hsl": hsl}},
        {"id": "b0", "clip": "BLUE.MP4", "in": 0.0, "out": 1.0}])
    red, red2, blue2, blue = (_rgb(out, t + 0.5) for t in range(4))
    want = _expected(red, {"hsl": hsl})
    assert np.all(np.abs(red2 - want) <= 255 * 0.03), (red2, want)    # the cube: ±3 %
    assert red2[1] > red2[0]                                           # red turned green
    assert np.all(np.abs(blue2 - blue) <= 2.0), (blue2, blue)          # outside the key: as shot
    vf = __import__("assemble").adjust_vf({"hsl": hsl})
    assert "lut3d=file=" in vf and "hsl-" in vf, vf


def test_adjust_vf_chains_in_premieres_order():
    import assemble
    vf = assemble.adjust_vf({"contrast": 20, "saturation": 120, "curves": {"master": [[0, 0], [0.5, 0.6], [1, 1]]},
                             "wheels": {"shadows": [0, 0, 10]}, "hsl": {"shift": 30}})
    i_basic = vf.index("lutrgb=r=")
    i_sat = vf.index("colorchannelmixer=")
    i_creative = vf.index("lutrgb=", i_sat)
    i_hsl = vf.index("lut3d=")
    assert i_basic < i_sat < i_creative < i_hsl, vf[:300]
    assert assemble.adjust_vf({"contrast": 50}).startswith("lutyuv=")   # Basic alone: as before
    assert assemble.adjust_vf({"hsl": {"hue": 90}}) is None              # a key with no correction
    assert "curves master" in assemble.describe_controls({"curves": {"master": [[0, 0], [1, 0.9]]}})
