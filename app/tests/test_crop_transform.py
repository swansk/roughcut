"""Crop and Transform as Premiere has them (redesign wave 19, lane EFFECTS2; verdict 15
punch 8, REDESIGN-GOAL dimensions 7 and 15).

Verdict 15: "five video effects in all"; Crop and Transform, Premiere's two most-used, in the
Effects bin and in Effect Controls with keyframes, rendering identically in the export, and
AI ops. They were in the bin since wave 14 (VFX) with Left / Top / Right / Bottom / Edge
Feather and Position / Scale / Rotation / Opacity; what was missing:

  * Premiere's parameters: Crop's Zoom; the Transform's Anchor Point, Uniform Scale with
    Scale Width, Skew and Skew Axis (roughcut/vfx.py has THE formulas);
  * the Program drew them as CSS on the picture's layers (a clip-path, a mask, a transform)
    while the export drew them with ffmpeg's scale and rotate: a Crop above a Gaussian Blur
    showed its edge sharp, an Edge Feather under a Transform hard, the Transform's resampling
    was the compositor's. Now the grade canvas's shader draws them as stages after the grade,
    in the stack's order (grade.js `geoStage`), and the export's Transform and Zoom are the
    same bilinear warp (research/tools/assemble.py `affine_warp`).

Measured here, in real Chromium on a still test pattern (640 × 360 at 24 fps, colour off so
the grade is the identity on both sides):

  formula   vfx.py's `transform_affine` is the formula, point by point (rotation clockwise,
            +Skew leans the top right, the Anchor Point lands on Position)
  program   the Program's parked picture (`grade.sample` at the frame's size: the renderer's
            own draw) against the export's frame at four film times — a still Crop + Transform
            stack, a Transform then a zoomed Crop, and a keyed Rotation + keyed Left at two
            times — mean |Δ| in luma under 2.0 code values (test_vfx_playback's bar for the
            export against THE formula; its chroma is 4:2:0), no whole pixel off, the canvas
            wearing no CSS clip or transform
  panel     Effect Controls' rows in Premiere's order, the checkboxes and the stopwatch one
            undo each, the Program following the keys
  play      a Crop + Transform shot plays at the rate a plain shot does (the canvas's draws a
            second by the frame clock, the same plays interleaved: within 15 %)

The AI-op == hand-op EDL test for the new parameters is test_actions_ui.py's
`test_video_effects_crop_zoom_and_the_transforms_anchor_and_skew`.
"""

from __future__ import annotations

import json
import math
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "research" / "tools"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))
from roughcut import vfx  # noqa: E402

W, H, FPS, CLIP_S = 640, 360, 24, 6.0
BAR = 2.0                     # mean |Program − export| in luma, code values (test_vfx_playback's export bar)
STACK_A = [{"id": "crop", "kind": "crop", "params": {"left": 10, "top": 5, "right": 20, "feather": 60}},
           {"id": "transform", "kind": "transform",
            "params": {"anchor_x": 0.3, "anchor_y": 0.4, "position_x": 0.55, "position_y": 0.45, "scale": 80,
                       "scale_width": 120, "uniform": "off", "skew": 12, "skew_axis": 20, "rotation": 15,
                       "opacity": 85}}]
STACK_B = [{"id": "transform", "kind": "transform", "params": {"rotation": -20, "scale": 110}},
           {"id": "crop", "kind": "crop", "params": {"left": 15, "right": 5, "top": 10, "bottom": 20, "zoom": "on"}}]
STACK_C = [{"id": "transform", "kind": "transform", "params": {"scale": 90}},
           {"id": "crop", "kind": "crop"}]
KEYS_C = {"rotation@transform": [[0.0, 0.0], [47 / 24, 45.0]], "left@crop": [[0.0, 0.0], [47 / 24, 30.0]]}
SEGS = [{"id": "p", "clip": "STILL.MP4", "in": 1.0, "out": 3.0, "why": "plain"},
        {"id": "a", "clip": "STILL.MP4", "in": 1.0, "out": 3.0, "why": "crop + transform",
         "controls": {"vfx": STACK_A}},
        {"id": "b", "clip": "STILL.MP4", "in": 1.0, "out": 3.0, "why": "transform + zoom",
         "controls": {"vfx": STACK_B}},
        {"id": "c", "clip": "STILL.MP4", "in": 1.0, "out": 3.0, "why": "keyed",
         "controls": {"vfx": STACK_C, "keys": KEYS_C}},
        # (a Lumetri Saturation on both, so the grade canvas is the picture of each)
        {"id": "m", "clip": "MOVE.MP4", "in": 0.5, "out": 3.5, "why": "moving, plain",
         "controls": {"saturation": 101}},
        {"id": "n", "clip": "MOVE.MP4", "in": 0.5, "out": 3.5, "why": "moving, crop + transform",
         "controls": {"saturation": 101, "vfx": STACK_A}}]


def _edl() -> dict:
    return {"variant": "T", "title": "crop + transform", "orient": "none", "story": "", "target_s": [2, 30],
            "segments": json.loads(json.dumps(SEGS)), "colour": {"mode": "off"}}


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


def _clip(path: Path, still: bool) -> None:
    """testsrc2 softened a little (a half-pixel difference in where a sample lands is then a
    small one in the picture, a wrong map a large one), held on its first frame for the still;
    near-lossless, so the proxy is the picture."""
    src = (f"testsrc2=size={W}x{H}:rate={FPS}:duration={CLIP_S}"
           + (",trim=end_frame=1,loop=loop=-1:size=1" if still else "")
           + f",gblur=sigma=1.2,trim=duration={CLIP_S}")
    subprocess.run(
        ["nice", "-n", "19", "ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi", "-i", src,
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={CLIP_S}",
         "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "4", "-g", "12",
         "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
         "-c:a", "aac", "-shortest", str(path)], check=True, capture_output=True)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def proj(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("crop-transform")
    footage, sidecars, work, assets = (root / d for d in ("footage", "sidecars", "work", "assets"))
    for d in (footage, sidecars, work, assets / "music"):
        d.mkdir(parents=True)
    for stem, still in (("STILL", True), ("MOVE", False)):
        _clip(footage / f"{stem}.MP4", still)
        (sidecars / f"{stem}.audio.json").write_text(json.dumps(_sidecar(stem)), encoding="utf-8")
    edl = root / "edl.json"
    edl.write_text(json.dumps(_edl(), indent=1), encoding="utf-8")
    return {"root": root, "footage": footage, "sidecars": sidecars, "work": work, "assets": assets, "edl": edl}


@pytest.fixture(scope="module")
def live_server(proj):
    import uvicorn
    import server

    server.configure(proj["edl"], proj["footage"], proj["sidecars"], proj["work"], proxies=False,
                     visual=proj["work"] / "no-visual", assets=proj["assets"])
    server.ensure_proxies(["STILL.MP4", "MOVE.MP4"])
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started, "server did not start"
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="module")
def browser(live_server):
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        yield b
        b.close()


@pytest.fixture
def page(browser, live_server, proj):
    proj["edl"].write_text(json.dumps(_edl(), indent=1), encoding="utf-8")
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "shader" in m.text else None)
    pg.add_init_script("try { localStorage.clear(); localStorage.setItem('roughcut.tour', 'seen'); } catch (e) {}")
    pg.goto(live_server)
    pg.wait_for_selector("#tl .tl-lane[data-lane=V1] .blk")
    pg.wait_for_function(f"typeof C !== 'undefined' && C && C.shots && segs.length === {len(SEGS)}"
                         " && window.effects && window.grade && grade.ready", timeout=15000)
    yield pg
    ctx.close()
    assert errors == [], errors


# ------------------------------------------------------------------ the formula

def test_the_transform_is_the_formula_and_the_schema_keeps_premieres_parameters():
    """THE formula, point by point: Rotation turns clockwise on screen, +Skew leans the top
    right, the Anchor Point lands on Position, Scale Width scales across only with Uniform
    Scale off; the schema keeps the new parameters and refuses a Skew past Premiere's 70°."""
    def at(F, x, y):
        return F[0] * x + F[2] * y + F[4], F[1] * x + F[3] * y + F[5]
    F = vfx.transform_affine({"rotation": 90}, 100, 100)
    assert at(F, 100, 50) == pytest.approx((50, 100))                     # right edge → bottom
    F = vfx.transform_affine({"skew": 45}, 100, 100)
    assert at(F, 50, 0) == pytest.approx((100, 0)) and at(F, 50, 100) == pytest.approx((0, 100))
    F = vfx.transform_affine({"anchor_x": 0.25, "anchor_y": 0.75, "position_x": 0.6, "position_y": 0.4,
                              "scale": 50, "rotation": 33}, 200, 100)
    assert at(F, 50, 75) == pytest.approx((120, 40))                       # the anchor on Position
    F = vfx.transform_affine({"scale": 50, "scale_width": 200, "uniform": "off"}, 100, 100)
    assert at(F, 100, 100) == pytest.approx((150, 75))
    F = vfx.transform_affine({"scale": 50, "scale_width": 200}, 100, 100)   # uniform: Scale only
    assert at(F, 100, 100) == pytest.approx((75, 75))
    # Skew along an axis of 90°: a slant down instead of across (the axis turns it)
    F = vfx.transform_affine({"skew": 45, "skew_axis": 90}, 100, 100)
    assert at(F, 100, 50)[1] - at(F, 0, 50)[1] == pytest.approx(100) and at(F, 50, 100)[0] == pytest.approx(50)
    z = vfx.crop_zoom_affine(10, 20, 30, 40, 100, 100)
    assert at(z, 10, 20) == pytest.approx((0, 0)) and at(z, 70, 60) == pytest.approx((100, 100))
    assert vfx.crop_zoom_affine(60, 0, 40, 0, 100, 100) is None
    got = vfx.clean(STACK_A + [{"id": "crop_2", "kind": "crop", "params": {"zoom": "on", "left": 5}}])
    assert got[1]["params"] == {k: float(v) if not isinstance(v, str) else v for k, v in STACK_A[1]["params"].items()}
    assert got[2]["params"] == {"left": 5.0, "zoom": "on"}
    assert vfx.clean([{"id": "transform", "kind": "transform", "params": {"uniform": "on", "skew": 0}}]) == \
        [{"id": "transform", "kind": "transform"}]
    for bad, why in (([{"id": "transform", "kind": "transform", "params": {"skew": 71}}], "Skew must be -70..70"),
                     ([{"id": "transform", "kind": "transform", "params": {"uniform": "maybe"}}], "one of on, off")):
        with pytest.raises(ValueError, match=why):
            vfx.clean(bad)
    keys = vfx.clean_keys({"anchor_x@transform": [[0, 0.1234567], [1, 0.7654321]], "skew@transform": [[0, 0], [1, 30]]},
                          got)
    assert keys == {"anchor_x@transform": [[0.0, 0.123457], [1.0, 0.765432]], "skew@transform": [[0.0, 0.0], [1.0, 30.0]]}
    import server
    c = server.clean_controls({"vfx": STACK_B, "keys": {"left@crop": [[0, 0], [1, 25]]}})
    assert c["vfx"][1]["params"]["zoom"] == "on" and c["keys"] == {"left@crop": [[0.0, 0.0], [1.0, 25.0]]}
    # the render log names them
    assert vfx.describe({"vfx": STACK_B}) == "Transform scale 110, rotation -20; Crop left 15, top 10, right 5, bottom 20, zoom on"


# ------------------------------------------------------------------ the Program is the export

def render(proj: dict, footage: Path, name: str) -> Path:
    out = proj["root"] / f"{name}.mp4"
    r = subprocess.run(["nice", "-n", "19", "uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(proj["edl"]),
                        "--footage", str(footage), "--profile", "match", "--fps", str(FPS), "-o", str(out)],
                       capture_output=True, text=True, env={**os.environ, "ROUGHCUT_GPU": "0"})
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return out


def export_frame(path: Path, n: int) -> np.ndarray:
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
                        "-vf", f"select=eq(n\\,{n}),scale=in_range=tv:out_range=pc:in_color_matrix=bt709",
                        "-frames:v", "1", "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).astype(np.float64).reshape(H, W, 3)


def park(pg, i: int, n: int) -> None:
    """The playhead on shot i's film frame n (its keys evaluated there), the monitor settled."""
    pg.evaluate(f"tl.seek(tl.filmStart(segs[{i}].id) + {n} / {FPS} + 0.0005)")
    pg.wait_for_function(
        f"liveVideo() && liveVideo().readyState >= 2 && !liveVideo().seeking && grade.shot === segs[{i}].id",
        timeout=15000)
    pg.wait_for_timeout(500)


def sample(pg) -> np.ndarray:
    """The Program's picture at the frame's own size (grade.sample: the renderer's own draw)."""
    s = pg.evaluate(f"(() => {{ const s = grade.sample({W}); return s ? {{ w: s.w, h: s.h, d: Array.from(s.data) }} : null; }})()")
    assert s and s["w"] == W and s["h"] == H, s and (s["w"], s["h"])
    return np.asarray(s["d"], np.float64).reshape(H, W, 4)[::-1, :, :3]


def luma(a: np.ndarray) -> np.ndarray:
    return 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]


def best_shift(a: np.ndarray, b: np.ndarray) -> list[int]:
    """The whole-pixel shift of b (±1 each way) that lies closest to a, in luma: [0, 0] when
    the two are drawn by the same map."""
    la, lb = luma(a)[2:-2, 2:-2], luma(b)
    err = {(dx, dy): float(np.abs(la - np.roll(np.roll(lb, dy, 0), dx, 1)[2:-2, 2:-2]).mean())
           for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
    return list(min(err, key=err.get))


def test_the_programs_crop_and_transform_are_the_exports_frame(page, proj):
    """Parked, the Program's picture at the frame's size is the export's frame — a still Crop
    (feathered) + Transform (anchor, non-uniform scale, skew, rotation, opacity), a Transform
    then a zoomed Crop, and a keyed Rotation and Left at two times: mean |Δ| under 2.0 code
    values each, the export rendered from the very file the Program plays (its proxy: the same
    decoded picture on both sides) and from the source, in luma (the export's chroma is 4:2:0),
    and no whole pixel off. The canvas wears no CSS clip or transform: its shader draws them."""
    pg = page
    pfoot = proj["root"] / "proxy-footage"
    pfoot.mkdir(exist_ok=True)
    pdir = next(proj["work"].glob("proxies/*/v*"))
    for stem in ("STILL", "MOVE"):
        link = pfoot / f"{stem}.MP4"
        if not link.exists():
            link.symlink_to(pdir / f"{stem}.mp4")
    from_proxy = render(proj, pfoot, "ct-proxy")
    from_source = render(proj, proj["footage"], "ct-source")
    cases = [("plain", 0, 24, []), ("crop+transform", 1, 24, ["crop", "xf"]), ("transform+zoom", 2, 24, ["xf", "crop"]),
             ("keyed t=0.5", 3, 12, ["xf", "crop"]), ("keyed t=1.5", 3, 36, ["xf", "crop"])]
    facts = {}
    for name, i, n, kinds in cases:
        park(pg, i, n)
        got = sample(pg)
        if kinds:
            st = pg.evaluate("grade.adjust && grade.adjust.vfx")
            assert st and [s["k"] for s in st] == kinds, (name, st)
            css = pg.evaluate("(() => { const c = getComputedStyle(document.querySelector('#gradeCanvas'));"
                              " return [c.clipPath, c.transform, c.maskImage, document.querySelector('#gradeCanvas').classList.contains('on')]; })()")
            assert css == ["none", "none", "none", True], (name, css)
        film = 48 * i + n
        ep, es = export_frame(from_proxy, film), export_frame(from_source, film)
        facts[name] = {"luma_mean": round(float(np.abs(luma(got) - luma(ep)).mean()), 3),
                       "luma_mean_source": round(float(np.abs(luma(got) - luma(es)).mean()), 3),
                       "rgb_mean": round(float(np.abs(got - ep).mean()), 3), "shift": best_shift(got, ep)}
        print(f"\nEFFECTS2 {name}: {json.dumps(facts[name])}", flush=True)
    for name, f in facts.items():
        # in luma, as the export's own bar is (its chroma is 4:2:0: the plain picture's RGB
        # already differs by its own ~3 code values at the colour bars' edges, the record says)
        assert f["luma_mean"] < BAR and f["luma_mean_source"] < BAR, (name, f)
        assert f["shift"] == [0, 0], (name, f)              # no part of a pixel off: the same map
    # the keyed shot moves between its two times, in the Program as in the export
    a, b = export_frame(from_proxy, 48 * 3 + 12), export_frame(from_proxy, 48 * 3 + 36)
    assert np.abs(a - b).mean() > 10


def test_a_crop_above_a_blur_and_a_feather_under_a_transform_are_drawn_as_the_export(page, proj):
    """The two cases verdict 14's CSS could not draw: a Crop above a Gaussian Blur (the export
    blurs the crop's edge) and an Edge Feather under a rotated Transform (the export's edge is
    soft) — the Program's picture is the export's frame."""
    pg = page
    edl = _edl()
    edl["segments"][1]["controls"] = {"vfx": [
        {"id": "crop", "kind": "crop", "params": {"left": 20, "right": 20}},
        {"id": "gaussian_blur", "kind": "gaussian_blur", "params": {"blurriness": 20}}]}
    edl["segments"][2]["controls"] = {"vfx": [
        {"id": "transform", "kind": "transform", "params": {"rotation": 25, "scale": 70}},
        {"id": "crop", "kind": "crop", "params": {"feather": 150}}]}
    proj["edl"].write_text(json.dumps(edl, indent=1), encoding="utf-8")
    pg.reload()
    pg.wait_for_function(f"typeof C !== 'undefined' && C && C.shots && segs.length === {len(SEGS)}"
                         " && window.effects && window.grade && grade.ready", timeout=15000)
    pfoot = proj["root"] / "proxy-footage"
    out = render(proj, pfoot, "ct-blur-feather")
    for i in (1, 2):
        park(pg, i, 24)
        got = sample(pg)
        ex = export_frame(out, 48 * i + 24)
        d = float(np.abs(luma(got) - luma(ex)).mean())
        # the soft edges themselves: a column across the crop's edge, a row across the feather
        edge = float(np.abs(luma(got)[180, 100:160] - luma(ex)[180, 100:160]).mean())
        print(f"\nEFFECTS2 order {i}: mean {d:.3f}, at the edge {edge:.3f}", flush=True)
        assert d < BAR and edge < 3 * BAR, (i, d, edge)
    proj["edl"].write_text(json.dumps(_edl(), indent=1), encoding="utf-8")


# ------------------------------------------------------------------ Effect Controls

def type_value(pg, key: str, value: float):
    pg.locator(f"#inspector .ec-v[data-k='{key}']").click()
    inp = pg.locator(f"#inspector .ec-v.editing[data-k='{key}']")
    inp.fill(str(value))
    inp.press("Enter")


def undo_title(pg) -> str:
    return pg.locator("#undo").get_attribute("title") or ""


def test_effect_controls_show_premieres_rows_and_each_change_is_one_undo(page, proj):
    """Transform: Anchor Point, Position, Uniform Scale, Scale, Skew, Skew Axis, Rotation,
    Opacity; unchecking Uniform Scale (one undo) shows Scale Height and Scale Width. Crop: Left,
    Top, Right, Bottom, Zoom, Edge Feather; its Zoom box is one undo and stretches the Program.
    The Anchor Point is a pair in a 1920 × 1080 frame's pixels with one stopwatch; keyed, the
    Program follows the playhead."""
    pg = page
    pg.locator("#tl .blk[data-id=p]").click()
    pg.evaluate("effects.vfxAdd('transform', ['p'], { reveal: true })")
    sec = pg.locator("#inspector .ec-vsec[data-kind=transform]")
    sec.wait_for()
    names = lambda s: [t.strip() for t in s.locator(".ec-row .ec-name").all_inner_texts()]  # noqa: E731
    assert names(sec) == ["Anchor Point", "Position", "Uniform Scale", "Scale", "Skew", "Skew Axis", "Rotation", "Opacity"]
    assert sec.locator(".ec-sw").count() == 7                          # every number has a stopwatch
    box = sec.locator("input.ec-vcheck[data-vfxsel=uniform]")
    assert box.is_checked()
    box.click()
    assert undo_title(pg).startswith("undo: transform uniform scale"), undo_title(pg)
    sec = pg.locator("#inspector .ec-vsec[data-kind=transform]")
    assert names(sec) == ["Anchor Point", "Position", "Uniform Scale", "Scale Height", "Scale Width", "Skew",
                          "Skew Axis", "Rotation", "Opacity"]
    type_value(pg, "scale_width@transform", 50)
    type_value(pg, "ancx@transform", 480)
    type_value(pg, "skew@transform", 10)
    assert undo_title(pg).startswith("undo: transform skew"), undo_title(pg)
    e = pg.evaluate("effects.vfx(tl.byId('p'))[0]")
    assert e["params"] == {"uniform": "off", "scale_width": 50, "anchor_x": 0.25, "skew": 10}, e
    pg.wait_for_function("grade.adjust && grade.adjust.vfx && grade.adjust.vfx[0].k === 'xf'")
    st = pg.evaluate("grade.adjust.vfx[0]")
    assert (st["sx"], st["sy"], st["ax"], st["sk"]) == (0.5, 1, 0.25, 10), st
    # the Anchor Point's stopwatch: one key at the shot's start, one at 1 s, the Program between
    pg.evaluate("tl.seek(tl.filmStart('p'))")
    pg.locator("#inspector .ec-sw[data-sw='anc@transform']").click()
    assert undo_title(pg).startswith("undo: animate transform anchor point"), undo_title(pg)
    pg.evaluate("tl.seek(tl.filmStart('p') + 1)")
    type_value(pg, "ancx@transform", 960)
    assert pg.evaluate("effects.keys('anchor_x@transform')") == [[0, 0.25], [1, 0.5]]
    assert pg.evaluate("effects.keys('anchor_y@transform')") == [[0, 0.5], [1, 0.5]]
    pg.evaluate("tl.seek(tl.filmStart('p') + 0.5)")
    pg.wait_for_function("grade.adjust && grade.adjust.vfx && Math.abs(grade.adjust.vfx[0].ax - 0.375) < 1e-3")
    # Crop with Zoom: the box stretched to the frame
    pg.evaluate("effects.vfxAdd('crop', ['p'])")
    crop = pg.locator("#inspector .ec-vsec[data-kind=crop]")
    crop.wait_for()
    assert names(crop) == ["Left", "Top", "Right", "Bottom", "Zoom", "Edge Feather"]
    type_value(pg, "left@crop", 25)
    crop.locator("input.ec-vcheck[data-vfxsel=zoom]").click()
    assert undo_title(pg).startswith("undo: crop zoom"), undo_title(pg)
    assert pg.evaluate("effects.vfx(tl.byId('p'))[1]") == {"id": "crop", "kind": "crop", "params": {"left": 25, "zoom": "on"}}
    pg.wait_for_function("grade.adjust && grade.adjust.vfx && grade.adjust.vfx.length === 2 && grade.adjust.vfx[1].z === 1")
    pg.evaluate("document.activeElement && document.activeElement.blur()")
    pg.keyboard.press("Control+z")                                     # the Zoom off again
    assert pg.evaluate("effects.vfx(tl.byId('p'))[1]") == {"id": "crop", "kind": "crop", "params": {"left": 25}}
    assert not pg.locator("#inspector input.ec-vcheck[data-vfxsel=zoom]").is_checked()
    pg.evaluate("flushSave()")
    t0 = time.time()
    while time.time() - t0 < 8:
        seg = json.loads(proj["edl"].read_text())["segments"][0]
        if len(((seg.get("controls") or {}).get("vfx") or [])) == 2:
            break
        time.sleep(0.2)
        pg.evaluate("flushSave()")
    c = json.loads(proj["edl"].read_text())["segments"][0]["controls"]
    assert c["vfx"][0]["params"] == {"uniform": "off", "scale_width": 50.0, "anchor_x": 0.25, "skew": 10.0}, c
    assert c["keys"] == {"anchor_x@transform": [[0.0, 0.25], [1.0, 0.5]], "anchor_y@transform": [[0.0, 0.5], [1.0, 0.5]]}, c


# ------------------------------------------------------------------ playing

RECORD = """() => {
  window.__cv = []; window.__on = true;
  const t0 = performance.now();
  const look = (ft) => {
    if (!window.__on) return;
    window.__cv.push({ ft: ft - t0, draws: grade.draws, shot: grade.shot, playing: player.playing });
    requestAnimationFrame(look);
  };
  requestAnimationFrame(look);
}"""


def play(pg, i: int, secs: float = 2.0) -> dict:
    """Play shot i from 0.3 s in for `secs`: the canvas's draws a second inside it, by the
    frame clock (the draw count each animation frame sees), and its longest hold."""
    pg.evaluate(f"cueAt({i}, segs[{i}].in + 0.3)")
    pg.wait_for_function("liveVideo() && liveVideo().readyState >= 3 && !liveVideo().seeking", timeout=20000)
    pg.wait_for_timeout(600)
    pg.evaluate(RECORD)
    pg.keyboard.press("Space")
    pg.wait_for_timeout(int(secs * 1000))
    pg.keyboard.press("Space")
    pg.evaluate("window.__on = false")
    sid = pg.evaluate(f"segs[{i}].id")
    cv = [s for s in pg.evaluate("window.__cv") if s["playing"] and s["shot"] == sid]
    mid = pg.evaluate("(() => { const c = getComputedStyle(document.querySelector('#gradeCanvas'));"
                      " return { on: document.querySelector('#gradeCanvas').classList.contains('on'),"
                      " clip: c.clipPath, vfx: grade.adjust && grade.adjust.vfx }; })()")
    pg.evaluate("pauseCut()")
    if len(cv) < 10:
        return {"rate": 0.0, "hold": None, "n": len(cv), "page_fps": 0.0, "mid": mid}
    changes = [b["ft"] for a, b in zip(cv, cv[1:]) if b["draws"] != a["draws"]]
    span = (cv[-1]["ft"] - cv[0]["ft"]) / 1000.0
    holds = [b - a for a, b in zip(changes, changes[1:])]
    return {"rate": round(len(changes) / span, 2) if span > 0 else 0.0, "hold": round(max(holds), 1) if holds else None,
            "n": len(cv), "page_fps": round(len(cv) / span, 1) if span > 0 else 0.0, "mid": mid}


def test_a_crop_and_transform_shot_plays_at_a_plain_shots_rate(page):
    """The moving clip graded (a Lumetri Saturation: the canvas is its picture), then graded
    with the Crop + Transform stack (its shader draws them too), the same plays interleaved:
    the effected shot's canvas draws as many frames a second as the plain one's, within 15 %
    (a CSS clip-path and mask on the canvas was the software compositor's on every frame).
    Judged on a round where the page itself kept up (its animation frames at 20 a second or
    more and the plain shot drawn at 80 % of its 24 fps); a box that never lets it is skipped,
    visibly (test_vfx_playback's rule), with the numbers."""
    pg = page
    cores, load = os.cpu_count() or 4, os.getloadavg()[0]
    if load > 2 * cores:
        pytest.skip(f"box already at load {load:.1f} on {cores} cores: not a measurement of the app")
    rounds = []
    for _ in range(4):
        pair = {}
        for name, i in (("plain", 4), ("fx", 5)):
            pair[name] = r = play(pg, i)
            print(f"\nEFFECTS2 play {name}: {r['rate']} draws/s, longest hold {r['hold']} ms, page {r['page_fps']} fps,"
                  f" load {os.getloadavg()[0]:.1f}", flush=True)
        rounds.append(pair)
        fx = pair["fx"]
        assert fx["mid"]["on"] and fx["mid"]["clip"] == "none", fx["mid"]
        assert [s["k"] for s in fx["mid"]["vfx"]] == ["crop", "xf"], fx["mid"]
        if all(r["page_fps"] >= 20 for r in pair.values()) and pair["plain"]["rate"] >= 0.8 * FPS:
            break
    ok = [p for p in rounds if all(r["page_fps"] >= 20 for r in p.values()) and p["plain"]["rate"] >= 0.8 * FPS]
    if not ok:
        pytest.skip(f"the page never kept up in {len(rounds)} rounds (load {os.getloadavg()[0]:.1f}): "
                    f"{[(p['plain']['rate'], p['fx']['rate'], p['plain']['page_fps']) for p in rounds]}")
    p = ok[-1]
    assert p["fx"]["rate"] >= 0.85 * p["plain"]["rate"], rounds
