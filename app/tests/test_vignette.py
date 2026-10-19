"""Lumetri › Vignette: the schema, THE formula and the export (redesign wave 15, lane
VIGNETTE; verdict 11 punch 7, REDESIGN-GOAL dimensions 7 and 15).

Verdict 11: "there is no Lumetri vignette, so the AI fakes one with rings, which darkens
the centre 12 %". Premiere's Lumetri Color ends in a Vignette section — Amount, Midpoint,
Roundness, Feather — and so does Effect Controls now. THE formula is roughcut/colour.py's
("Lumetri › Vignette"); grade.js's shader draws it on the Program (test_vignette_ui.py)
and research/tools/assemble.py `vignette_graph` in the export, measured here off the
rendered film against the formula itself, pixel for pixel: a still vignette, a lightening
one, a keyed Amount (its `sendcmd`), a keyed shape (a mask per frame), and a vignetted
shot's handle inside a Cross Dissolve. The centre is never touched (the formula keeps
it: d < c − h there), so the exported centre is the source's within ±2 %.

The fixture: GREY a flat 0x9a9a9a, 320 × 180 at 24 fps.
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

from roughcut import colour  # noqa: E402

W, H, FPS, CLIP_S = 320, 180, 24, 6.0
GREY = 0x9a


# ------------------------------------------------------------------ the schema

def test_the_server_keeps_the_four_numbers_and_their_keys_and_refuses_the_rest():
    import server
    c = server.clean_controls({"vignette_amount": -1.5, "vignette_midpoint": 50, "vignette_roundness": 20,
                               "vignette_feather": 70.00001,
                               "keys": {"vignette_amount": [[0, 0], [1, -2]], "vignette_midpoint": [[0, 30]]}})
    assert c == {"vignette_amount": -1.5, "vignette_roundness": 20.0, "vignette_feather": 70.0,
                 "keys": {"vignette_amount": [[0.0, 0.0], [1.0, -2.0]], "vignette_midpoint": [[0.0, 30.0]]}}
    # the defaults are not kept: a vignette at rest is no controls at all
    assert server.clean_controls({"vignette_amount": 0, "vignette_midpoint": 50,
                                  "vignette_roundness": 0, "vignette_feather": 50}) is None
    for bad, why in (({"vignette_amount": -6}, "-5..5"), ({"vignette_feather": 101}, "0..100"),
                     ({"vignette_roundness": -101}, "-100..100"),
                     ({"keys": {"vignette_amount": [[0, 7]]}}, "-5..5")):
        with pytest.raises(ValueError, match=why):
            server.clean_controls(bad)
    # Lumetri is V1's: a clip on V2+ renders as shot
    for raw in ({"vignette_amount": -1}, {"keys": {"vignette_amount": [[0, -1]]}}):
        with pytest.raises(ValueError, match="Lumetri Color applies to V1"):
            server.clean_controls(raw, overlay=True)


# ------------------------------------------------------------------ THE formula

def test_the_formula_never_touches_the_centre_and_takes_the_corners_whole():
    rng = np.random.default_rng(7)
    for _ in range(200):
        m, r, f = rng.uniform(0, 100), rng.uniform(-100, 100), rng.uniform(0, 100)
        w = colour.vignette_mask(320, 180, m, r, f)
        cy, cx = slice(90 - 11, 90 + 11), slice(160 - 20, 160 + 20)     # the middle eighth
        assert w[cy, cx].max() == 0.0, (m, r, f)
        assert 0.0 <= w.min() and w.max() <= 1.0
    # at the defaults (midpoint 50, feather 50) the corners take the whole Amount, the
    # middle of each edge most of it, and the falloff only grows outward
    w = colour.vignette_mask(320, 180, 50, 0, 50)
    assert w[0, 0] == pytest.approx(1.0, abs=0.02) and w[-1, -1] == pytest.approx(1.0, abs=0.02)
    assert 0.6 < w[90, 0] < 0.95 and 0.6 < w[0, 160] < 0.95
    row = w[90, 160:]
    assert np.all(np.diff(row) >= -1e-12)
    # Roundness +100 is a circle: as far across as down from the centre is the same weight
    circ = colour.vignette_mask(320, 180, 30, 100, 40)
    assert circ[90, 160 + 60] == pytest.approx(circ[90 + 60, 160], abs=0.02)
    # Roundness −100 hugs the frame: the middle of the edges nearly as strong as a corner
    rect = colour.vignette_mask(320, 180, 50, -100, 0)
    assert rect[90, 0] == pytest.approx(1.0) and rect[0, 160] == pytest.approx(1.0)
    # Feather 0 is a hard edge, Feather 100 a long ramp
    hard, soft = colour.vignette_mask(320, 180, 50, 0, 0), colour.vignette_mask(320, 180, 50, 0, 100)
    ramp = lambda w: float(((w > 0.05) & (w < 0.95)).mean())        # noqa: E731
    assert ramp(hard) < 0.01 and ramp(soft) > 0.3, (ramp(hard), ramp(soft))   # one pixel's edge
    # a lower Midpoint reaches further in
    assert (colour.vignette_mask(320, 180, 20, 0, 50) > 0.5).mean() > (w > 0.5).mean()
    # the last line: toward black (−) or white (+), by |amount|/5 of the weight
    px = np.full((1, 1, 3), 0.6)
    assert colour.vignette_apply(px, -2, np.ones((1, 1)))[0, 0, 0] == pytest.approx(0.36)
    assert colour.vignette_apply(px, 2.5, np.ones((1, 1)))[0, 0, 0] == pytest.approx(0.8)
    assert colour.vignette_apply(px, -5, np.zeros((1, 1)))[0, 0, 0] == pytest.approx(0.6)


def test_the_shader_twin_is_the_formula():
    """grade.js `vigU` (the shader's uniforms) and the shader's own lines, run in node on the
    same points, give THE formula's weight."""
    js = (ROOT / "app" / "static" / "grade.js").read_text(encoding="utf-8")
    a = js.index("    function vigU(v, w, h) {")
    b = js.index("\n    }\n", a) + 7
    pts = [(u, v) for u in (-0.99, -0.6, -0.2, 0.0, 0.35, 0.8, 0.97) for v in (-0.97, -0.5, 0.0, 0.4, 0.9)]
    cases = [(-2, 50, 0, 50), (1.5, 30, 100, 20), (-4, 70, -100, 0), (-1, 0, -40, 100), (3, 100, 60, 75)]
    prog = js[a:b] + """
      const smooth = (e0, e1, x) => { const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0))); return t * t * (3 - 2 * t); };
      const out = [];
      for (const c of CASES) {
        const q = vigU(c, 320, 180);
        for (const [u, v] of PTS) {
          const d = Math.pow(Math.pow(q[0] * Math.abs(u), q[1]) + Math.pow(Math.abs(v), q[1]), 1 / q[1]) * q[2];
          out.push(smooth(q[4], q[5], d));
        }
      }
      console.log(JSON.stringify(out));"""
    prog = prog.replace("CASES", json.dumps(cases)).replace("PTS", json.dumps(pts))
    r = subprocess.run(["node", "-e", prog], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    want = [float(colour.vignette_weight(u, v, 320 / 180, m, rr, f)) for (_, m, rr, f) in cases for (u, v) in pts]
    assert np.allclose(got, want, atol=1e-9), max(abs(x - y) for x, y in zip(got, want))


# ------------------------------------------------------------------ the export

def _clip(path: Path) -> None:
    subprocess.run(
        ["nice", "-n", "19", "ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i", f"color=c=0x{GREY:02x}{GREY:02x}{GREY:02x}:size={W}x{H}:rate={FPS}:duration={CLIP_S}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={CLIP_S}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "12",
         "-c:a", "aac", "-t", str(CLIP_S), str(path)], check=True, capture_output=True)


@pytest.fixture(scope="module")
def proj(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("vignette")
    footage = root / "footage"
    footage.mkdir()
    _clip(footage / "GREY.MP4")
    return {"root": root, "footage": footage}


def render(proj: dict, segments: list[dict], name: str, **extra) -> tuple[Path, str]:
    edl = {"variant": "T", "title": "vignette", "orient": "none", "story": "", "target_s": [2, 20],
           "segments": segments, **extra}
    path = proj["root"] / f"{name}.json"
    path.write_text(json.dumps(edl), encoding="utf-8")
    out = proj["root"] / f"{name}.mp4"
    r = subprocess.run(["nice", "-n", "19", "uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(path),
                        "--footage", str(proj["footage"]), "--profile", "match", "--fps", str(FPS),
                        "-o", str(out)],
                       capture_output=True, text=True, env={**os.environ, "ROUGHCUT_GPU": "0"})
    assert r.returncode == 0, r.stdout + r.stderr
    return out, r.stdout


def frame(path: Path, n: int) -> np.ndarray:
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
                        "-vf", f"select=eq(n\\,{n})", "-frames:v", "1", "-vsync", "0",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).astype(np.float64).reshape(H, W, 3)


def seg(i: int, controls: dict | None = None) -> dict:
    s = {"id": f"s{i}", "clip": "GREY.MP4", "in": 1.0, "out": 3.0, "why": "grey"}
    if controls:
        s["controls"] = controls
    return s


def centre(a: np.ndarray) -> float:
    return float(a[90 - 11:90 + 11, 160 - 20:160 + 20].mean())


def against(plain: np.ndarray, got: np.ndarray, vals: tuple) -> tuple[float, float]:
    """(mean, max) |export − THE formula on the plain frame| in code values."""
    a, m, r, f = vals
    want = colour.vignette_apply(plain / 255.0, a, colour.vignette_mask(W, H, m, r, f)) * 255.0
    err = np.abs(got - want)
    return float(err.mean()), float(err.max())


def test_a_still_vignette_exports_as_the_formula_draws_it(proj):
    """Amount −2 at the defaults, a lightening one with a hard-edged near-rectangle, and a
    circle: each exported frame against THE formula on the unvignetted shot, pixel for
    pixel (mean under 1 code value, the worst under 6 — 4:2:0 at the steepest edge), and
    the centre the source's within ±2 %."""
    cases = [(-2.0, 50.0, 0.0, 50.0), (2.5, 30.0, -100.0, 20.0), (-3.0, 40.0, 100.0, 60.0)]
    segs = [seg(0)] + [seg(i + 1, dict(zip(colour.VIGNETTE_FIELDS, c))) for i, c in enumerate(cases)]
    film, log = render(proj, segs, "still")
    plain = frame(film, 24)
    assert "vignette amount -2 midpoint 50 roundness 0 feather 50" in log, log
    for i, vals in enumerate(cases, 1):
        got = frame(film, 48 * i + 24)
        mean, worst = against(plain, got, vals)
        print(f"\n{vals}: mean {mean:.2f} · worst {worst:.1f} · centre {centre(got):.1f} of {centre(plain):.1f}"
              f" · corner {got[2, 2, 0]:.0f}")
        assert mean < 1.0 and worst < 6.0, (vals, mean, worst)
        assert abs(centre(got) - centre(plain)) <= 0.02 * centre(plain), vals
    # amount −2: the corners are 60 % of the grey, as THE formula says
    got = frame(film, 72)
    assert got[1, 1, 0] == pytest.approx(GREY * 0.6, abs=3)


def test_a_keyed_amount_and_a_keyed_shape_follow_their_keys_frame_by_frame(proj):
    """A keyed Amount 0 → −5 over 1.5 s (the still mask, lut2 re-set by `sendcmd` where it
    changes) and a keyed Midpoint 0 → 100 (a mask per frame): frame n against THE formula
    at the keys' value at n / 24 s."""
    segs = [seg(0),
            seg(1, {"vignette_amount": -5, "keys": {"vignette_amount": [[0, 0], [1.5, -5]]}}),
            seg(2, {"vignette_amount": -3, "keys": {"vignette_midpoint": [[0, 0], [1.5, 100]]}})]
    film, log = render(proj, segs, "keyed")
    assert "keyed amount" in log and "keyed midpoint" in log, log
    plain = frame(film, 24)
    for k in (0, 12, 18, 36, 47):
        t = k / FPS
        a = -5.0 * min(1.0, t / 1.5)
        mean, worst = against(plain, frame(film, 48 + k), (a, 50.0, 0.0, 50.0))
        assert mean < 1.0 and worst < 7.0, (k, a, mean, worst)
        m = 100.0 * min(1.0, t / 1.5)
        mean, worst = against(plain, frame(film, 96 + k), (-3.0, m, 0.0, 50.0))
        assert mean < 1.0 and worst < 8.0, (k, m, mean, worst)


def test_a_vignetted_shot_s_handle_carries_its_vignette_through_a_dissolve(proj):
    """A Cross Dissolve from a vignetted shot (Amount −5) to a plain one: on the edit's
    frame the corner is half the vignetted corner and half the plain grey, as the Program
    mixes the two graded pictures."""
    segs = [seg(0, {"vignette_amount": -5}), seg(1)]
    film, _ = render(proj, segs, "dissolve",
                     transitions=[{"a": "s0", "b": "s1", "kind": "cross_dissolve", "dur": 1.0}])
    edit = frame(film, 48)
    assert edit[1, 1, 0] == pytest.approx(GREY / 2, abs=4), edit[1, 1]
    assert centre(edit) == pytest.approx(GREY, abs=2)
    assert frame(film, 72)[1, 1, 0] == pytest.approx(GREY, abs=2)
