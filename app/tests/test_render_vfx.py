"""Video effects render as Effect Controls says (wave 14, lane VFX; verdict 10, punch 8;
REDESIGN-GOAL dimension 7): the schema, its validation on save, and the export.

`controls.vfx` is a shot's stack of Premiere's video effects — Blur & Sharpen › Gaussian
Blur, Distort › Transform, Transform › Crop — with their keys in `controls.keys` as
`<param>@<id>` (roughcut/vfx.py has the schema and THE formulas; app/server.py
`clean_controls` validates it on every save). research/tools/assemble.py `vfx_graph`
draws them on the graded picture, top of the stack first, before the designed effects
and Motion. Rendered the way the board renders — assemble.py as a subprocess, the Match
Sequence profile, so the frame is the fixture's own 320 × 180 at 24 fps — and measured
off the film: a 10 % left Crop is a 32 px black band (±2), a feather a ramp, a Gaussian
Blur takes the pattern's detail down (a one-axis one only along its axis), a Transform's
Scale and Opacity, a bypassed effect not drawn, the stack's order, and keyed Left and
Blurriness frame by frame. The board's half is test_video_effects.py.

The fixture: CLIP_A solid red, CLIP_B a test pattern (detail for the blur to soften),
CLIP_C solid green, 320 × 180 at 24 fps.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT))
TOOLS = ROOT / "research" / "tools"
sys.path.insert(0, str(TOOLS))

from roughcut import vfx  # noqa: E402

W, H, FPS, CLIP_S = 320, 180, 24, 6.0
SEGS = [{"id": "s1", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "red"},
        {"id": "s2", "clip": "CLIP_B.MP4", "in": 1.0, "out": 3.0, "why": "pattern"},
        {"id": "s3", "clip": "CLIP_C.MP4", "in": 1.0, "out": 3.0, "why": "green"}]
EDL = {"variant": "T", "title": "video effects", "orient": "none", "story": "",
       "target_s": [2, 20], "segments": SEGS}


# ------------------------------------------------------------------ the schema

def test_the_schema_cleans_what_differs_and_refuses_what_is_not_an_effect():
    got = vfx.clean([{"id": "crop", "kind": "crop", "params": {"left": 10, "top": 0, "feather": 20.00001}},
                     {"id": "gaussian_blur", "kind": "gaussian_blur",
                      "params": {"blurriness": 0, "dimensions": "both"}, "off": True},
                     {"id": "transform", "kind": "transform", "params": {"position_x": 0.5, "scale": 50}}])
    assert got == [{"id": "crop", "kind": "crop", "params": {"left": 10.0, "feather": 20.0}},
                   {"id": "gaussian_blur", "kind": "gaussian_blur", "off": True},
                   {"id": "transform", "kind": "transform", "params": {"scale": 50.0}}]
    assert vfx.clean([]) is None and vfx.clean(None) is None
    for bad, why in (
            ([{"id": "x", "kind": "wipe"}], "unknown video effect"),
            ([{"id": "crop", "kind": "crop", "params": {"left": 101}}], "Left must be 0..100"),
            ([{"id": "crop", "kind": "crop", "params": {"zoom": 1}}], "no parameter 'zoom'"),
            ([{"id": "crop", "kind": "crop"}, {"id": "crop", "kind": "crop"}], "both 'crop'"),
            ([{"id": "Crop!", "kind": "crop"}], "needs an id"),
            ([{"id": "gaussian_blur", "kind": "gaussian_blur", "params": {"dimensions": "diagonal"}}], "one of"),
            ([{"id": "crop", "kind": "crop", "colour": 1}], "no field 'colour'"),
            ([{"id": f"crop_{i}", "kind": "crop"} for i in range(2, 20)], "at most 16")):
        with pytest.raises(ValueError, match=why):
            vfx.clean(bad)
    stack = got
    keys = vfx.clean_keys({"left@crop": [[0, 0], [1.5, 30]], "left@gone": [[0, 1]],
                           "position_x@transform": [[0, 0.25], [1, 0.75]]}, stack)
    assert keys == {"left@crop": [[0.0, 0.0], [1.5, 30.0]], "position_x@transform": [[0.0, 0.25], [1.0, 0.75]]}
    for bad, why in (({"left@crop": [[0, 120]]}, "0..100"), ({"zoom@crop": [[0, 1]]}, "no keyframes"),
                     ({"left@crop": [[1, 1], [0.5, 2]]}, "sorted")):
        with pytest.raises(ValueError, match=why):
            vfx.clean_keys(bad, stack)
    assert vfx.next_id("crop", ["crop", "crop_2"]) == "crop_3" and vfx.next_id("crop", []) == "crop"


def test_the_server_keeps_the_stack_and_its_keys_with_motions():
    import server
    c = server.clean_controls({"scale": 120, "vfx": [{"id": "crop", "kind": "crop", "params": {"left": 10}}],
                               "keys": {"opacity": [[0, 100], [1, 0]], "left@crop": [[0, 0], [1, 25]]}})
    assert c == {"scale": 120.0, "vfx": [{"id": "crop", "kind": "crop", "params": {"left": 10.0}}],
                 "keys": {"opacity": [[0.0, 100.0], [1.0, 0.0]], "left@crop": [[0.0, 0.0], [1.0, 25.0]]}}
    # a key whose effect was cleared goes with it; nothing else is touched
    assert server.clean_controls({"keys": {"left@crop": [[0, 5]]}}) is None
    with pytest.raises(ValueError, match="video effects apply to V1"):
        server.clean_controls({"vfx": [{"id": "crop", "kind": "crop"}]}, overlay=True)
    with pytest.raises(ValueError, match="unknown video effect"):
        server.clean_controls({"vfx": [{"id": "x", "kind": "mosaic"}]})


# ------------------------------------------------------------------ the fixture

def _clip(path: Path, src: str) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i", f"{src}{':' if '=' in src else '='}size={W}x{H}:rate={FPS}:duration={CLIP_S}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={CLIP_S}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "12",
         "-c:a", "aac", "-t", str(CLIP_S), str(path)], check=True, capture_output=True)


def _sidecar(stem: str) -> dict:
    return {
        "clip": f"{stem}.MP4", "duration_s": CLIP_S, "sample_rate": 16000, "frame_hz": 10,
        "tracks": {"speech_band_db": [-30.0] * 60, "flatness": [0.05] * 60,
                   "onset": [0.2] * 60, "vad": [0] * 60},
        "transcript": [], "events": [],
        "summary": {"speech_fraction": 0.0, "wind_dominant_fraction": 0.0,
                    "integrated_lufs": -18.0, "peak_dbfs": -3.0, "audio_usable": True,
                    "n_words": 0, "analysis_s": 0.1},
        "candidates": [],
    }


@pytest.fixture(scope="module")
def proj(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("vfx")
    footage, sidecars, work, assets = (root / d for d in ("footage", "sidecars", "work", "assets"))
    for d in (footage, sidecars, work, assets / "music"):
        d.mkdir(parents=True)
    for stem, src in (("CLIP_A", "color=c=red"), ("CLIP_B", "testsrc2"), ("CLIP_C", "color=c=green")):
        _clip(footage / f"{stem}.MP4", src)
        (sidecars / f"{stem}.audio.json").write_text(json.dumps(_sidecar(stem)), encoding="utf-8")
    edl = root / "edl.json"
    edl.write_text(json.dumps(EDL, indent=1), encoding="utf-8")
    return {"root": root, "footage": footage, "sidecars": sidecars, "work": work,
            "assets": assets, "edl": edl}


# ------------------------------------------------------------------ the export

def render(proj: dict, edl: dict, name: str) -> tuple[Path, str]:
    path = proj["root"] / f"{name}.json"
    path.write_text(json.dumps(edl), encoding="utf-8")
    out = proj["root"] / f"{name}.mp4"
    r = subprocess.run(["nice", "-n", "19", "uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(path),
                        "--footage", str(proj["footage"]), "--profile", "match",
                        "--fps", str(FPS), "-o", str(out)],
                       capture_output=True, text=True, env={**os.environ, "ROUGHCUT_GPU": "0"})
    assert r.returncode == 0, r.stdout + r.stderr
    return out, r.stdout


def frame(path: Path, n: int) -> np.ndarray:
    """Film frame n of a render, RGB float."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
                        "-vf", f"select=eq(n\\,{n})", "-frames:v", "1", "-vsync", "0",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).astype(np.float64).reshape(H, W, 3)


def luma(a: np.ndarray) -> np.ndarray:
    return 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]


def band(a: np.ndarray, rows=None) -> int:
    """The black band's width on the picture's left, in its own pixels: the columns from
    the left whose every middle row is black (luma < 40)."""
    y = luma(a)
    h = y.shape[0]
    mid = y[rows] if rows is not None else y[h // 4: 3 * h // 4]
    dark = (mid < 40).all(axis=0)
    n = 0
    while n < len(dark) and dark[n]:
        n += 1
    return n


def detail(a: np.ndarray, axis: int = 1) -> float:
    """The picture's sharpness: the energy of the steps between neighbours along `axis`
    (a blur spreads an edge's step over its width: the steps' sum holds, their squares'
    falls as 1/σ)."""
    return float((np.diff(luma(a), axis=axis) ** 2).mean())


def one(seg_controls: dict, clip: str = "CLIP_A.MP4") -> dict:
    return {**EDL, "segments": [{**SEGS[0], "clip": clip, "controls": seg_controls}]}


def test_the_export_crops_blurs_and_transforms(proj):
    """Each effect alone, its numbers measured off the exported frame (frame 12, mid-shot):
    a 10 % left Crop is a 32 px black band (±2) and the rest is the clip; a feathered
    Crop ramps; a Gaussian Blur takes the pattern's detail down, a horizontal one only
    across; a Transform's Scale 50 leaves a black frame a quarter wide round the picture
    and its Opacity 50 is half the red; a bypassed effect is not drawn."""
    plain, _ = render(proj, one(None, "CLIP_B.MP4"), "plain")
    crop, log = render(proj, one({"vfx": [{"id": "crop", "kind": "crop", "params": {"left": 10}}]}), "crop")
    a = frame(crop, 12)
    assert abs(band(a) - 32) <= 2, band(a)
    assert a[90, 40][0] > 200 and a[90, 40][1] < 60, a[90, 40]          # the red is there past it
    assert "Crop left 10" in log, log
    fe, _ = render(proj, one({"vfx": [{"id": "crop", "kind": "crop", "params": {"left": 10, "feather": 120}}]}), "feather")
    f = luma(frame(fe, 12))[90]
    # Feather 120 px of a 1920 frame = 20 px here: dark at the line, half way up 10 px in, full past 20
    assert f[30] < 25 and 0.3 < (f[42] - 16) / (f[100] - 16) < 0.7 and abs(f[56] - f[100]) < 6, f[28:60]
    # a feather alone softens the frame's own edges (vfx.py: every edge, cropped or not)
    fo, _ = render(proj, one({"vfx": [{"id": "crop", "kind": "crop", "params": {"feather": 120}}]}), "feather-only")
    g = luma(frame(fo, 12))
    assert g[90, 0] < 0.2 * g[90, 100] and g[0, 160] < 0.2 * g[90, 160] and abs(g[90, 40] - g[90, 160]) < 6
    blur, _ = render(proj, one({"vfx": [{"id": "gaussian_blur", "kind": "gaussian_blur",
                                         "params": {"blurriness": 18}}]}, "CLIP_B.MP4"), "blur")
    p0, b0 = frame(plain, 12), frame(blur, 12)
    assert detail(b0) < 0.5 * detail(p0) and detail(b0, 0) < 0.5 * detail(p0, 0), (detail(b0), detail(p0))
    hb, _ = render(proj, one({"vfx": [{"id": "gaussian_blur", "kind": "gaussian_blur",
                                       "params": {"blurriness": 18, "dimensions": "horizontal"}}]},
                             "CLIP_B.MP4"), "blurh")
    vb, _ = render(proj, one({"vfx": [{"id": "gaussian_blur", "kind": "gaussian_blur",
                                       "params": {"blurriness": 18, "dimensions": "vertical"}}]},
                             "CLIP_B.MP4"), "blurv")
    h0, v0 = frame(hb, 12), frame(vb, 12)
    # each softens its own direction: across, the horizontal one far more; down, the vertical
    assert detail(h0) < 0.25 * detail(p0) and detail(v0, 0) < 0.25 * detail(p0, 0)
    assert detail(h0, 0) > 2 * detail(v0, 0) and detail(v0) > 2 * detail(h0), \
        (detail(h0), detail(h0, 0), detail(v0), detail(v0, 0))
    tr, _ = render(proj, one({"vfx": [{"id": "transform", "kind": "transform",
                                       "params": {"scale": 50, "opacity": 50}}]}), "transform")
    t0 = frame(tr, 12)
    assert luma(t0[:, :76]).max() < 30 and luma(t0[:40]).max() < 30 and luma(t0[50:130, 84:236]).min() > 20, \
        "scale 50: a black frame round it"
    assert abs(t0[90, 160][0] - 0.5 * a[90, 160][0]) < 25, (t0[90, 160], a[90, 160])
    off, _ = render(proj, one({"vfx": [{"id": "crop", "kind": "crop", "params": {"left": 10}, "off": True}]}), "off")
    assert band(frame(off, 12)) == 0


def test_the_stack_order_and_keys_render(proj):
    """Crop then Transform crops the picture and moves the cropped picture; Transform
    then Crop crops the moved frame — two different pictures, as in Premiere. A keyed
    Left goes 0 → 50 % over the shot: no band on its first frame, half the frame black
    on its last; a keyed Blurriness sharp to soft."""
    crop = {"id": "crop", "kind": "crop", "params": {"left": 50}}
    move = {"id": "transform", "kind": "transform", "params": {"position_x": 0.75}}
    a, _ = render(proj, one({"vfx": [crop, move]}), "crop-move")
    b, _ = render(proj, one({"vfx": [move, crop]}), "move-crop")
    fa, fb = frame(a, 12), frame(b, 12)
    # crop first: the right half of the picture, moved a quarter right → black to 3/4 · W
    assert band(fa) == pytest.approx(0.75 * W, abs=3), band(fa)
    # move first: the picture starts at W/4, and the left half of the FRAME is cropped
    assert band(fb) == pytest.approx(0.5 * W, abs=3), band(fb)
    keyed, _ = render(proj, one({"vfx": [{"id": "crop", "kind": "crop"}],
                                 "keys": {"left@crop": [[0, 0], [47 / 24, 50]]}}), "keyed")
    assert band(frame(keyed, 0)) == 0
    assert band(frame(keyed, 47)) == pytest.approx(W / 2, abs=2)
    assert band(frame(keyed, 24)) == pytest.approx(W * 0.5 * 24 / 47, abs=2)
    kb, log = render(proj, one({"vfx": [{"id": "gaussian_blur", "kind": "gaussian_blur"}],
                                "keys": {"blurriness@gaussian_blur": [[0, 0], [47 / 24, 30]]}}, "CLIP_B.MP4"), "kblur")
    assert detail(frame(kb, 40)) < 0.6 * detail(frame(kb, 0)), (detail(frame(kb, 40)), detail(frame(kb, 0)))
    assert "Gaussian Blur blurriness keyed ×2" in log
