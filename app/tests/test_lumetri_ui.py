"""Lumetri beyond Basic on the board: Curves, Color Wheels, HSL Secondary and Lumetri
Scopes (redesign wave 11, lane LUMETRI; verdict 8 punch 4, REDESIGN-GOAL dimension 7).

In real Chromium on flat colour clips (GREY, RED, BLUE), the punch's done criteria:

  * a curve point dragged and a wheel pushed each move `grade.probe()` within 150 ms of
    the gesture (from the input that lands the change to the frame that shows it: `arm`),
    and the export of the cut the board saved moves by the same amount (each channel
    within 3 % of full scale of the preview's move);
  * every drag is one undo entry;
  * a solid grey's waveform is a flat line at its IRE, and a pure colour's vectorscope
    point lands at its hue — live while parked, throttled while playing;
  * the server refuses values out of range; a V2+ clip takes no Lumetri;
  * the shader's Curves + Wheels table is THE formula's (roughcut/colour.py) code for code.
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
from roughcut import colour  # noqa: E402

W, H, FPS, CLIP_S = 320, 180, 24, 4.0
LIMIT_MS = 150
LIMIT_FRAMES = 9                # 150 ms at 60 Hz
COLOURS = {"GREY": "808080", "RED": "ff0000", "BLUE": "0000ff"}
SEGS = [{"id": "g1", "clip": "GREY.MP4", "in": 0.0, "out": 2.0, "why": "grey"},
        {"id": "g2", "clip": "GREY.MP4", "in": 0.0, "out": 2.0, "why": "grey again"},
        {"id": "r1", "clip": "RED.MP4", "in": 0.0, "out": 2.0, "why": "red"},
        {"id": "b1", "clip": "BLUE.MP4", "in": 0.0, "out": 2.0, "why": "blue"}]


def _clip(path: Path, hexrgb: str) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i", f"color=c=0x{hexrgb}:size={W}x{H}:rate={FPS}:duration={CLIP_S}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={CLIP_S}",
         # encoded with the BT.709 matrix it is tagged with (a plain `-pix_fmt` converts by
         # BT.601), so the board and the export decode the same colour
         "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
         "-c:v", "libx264", "-preset", "ultrafast", "-color_range", "tv", "-colorspace", "bt709",
         "-color_primaries", "bt709", "-color_trc", "bt709", "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True)


def _sidecar(stem: str) -> dict:
    return {
        "clip": f"{stem}.MP4", "duration_s": CLIP_S, "sample_rate": 16000, "frame_hz": 10,
        "tracks": {"speech_band_db": [-30.0] * 40, "flatness": [0.05] * 40,
                   "onset": [0.2] * 40, "vad": [0] * 40},
        "transcript": [], "events": [],
        "summary": {"speech_fraction": 0.0, "wind_dominant_fraction": 0.0,
                    "integrated_lufs": -18.0, "peak_dbfs": -3.0, "audio_usable": True,
                    "n_words": 0, "analysis_s": 0.1},
        "candidates": [],
    }


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def proj(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("lumetri")
    footage, sidecars, work, assets = (root / d for d in ("footage", "sidecars", "work", "assets"))
    for d in (footage, sidecars, work, assets / "music"):
        d.mkdir(parents=True)
    for stem, hexrgb in COLOURS.items():
        _clip(footage / f"{stem}.MP4", hexrgb)
        (sidecars / f"{stem}.audio.json").write_text(json.dumps(_sidecar(stem)), encoding="utf-8")
    edl = root / "edl.json"
    return {"root": root, "footage": footage, "sidecars": sidecars, "work": work,
            "assets": assets, "edl": edl}


def _seed(proj, segments=None, overlays=None) -> None:
    # the auto's balance off: the board and the export both start from the clip's own colour
    body = {"variant": "T", "title": "lumetri", "orient": "none", "story": "", "target_s": [2, 20],
            "segments": json.loads(json.dumps(segments or SEGS)), "colour": {"mode": "off"}}
    if overlays:
        body["overlays"] = overlays
    proj["edl"].write_text(json.dumps(body, indent=1), encoding="utf-8")


@pytest.fixture(scope="module")
def live_server(proj):
    import uvicorn
    import server
    _seed(proj)
    server.configure(proj["edl"], proj["footage"], proj["sidecars"], proj["work"],
                     proxies=False, assets=proj["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in COLOURS])
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


def _open(pw, url):
    browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "shader" in m.text else None)
    pg.add_init_script("try { localStorage.clear(); } catch (e) {}")
    pg.goto(url)
    pg.wait_for_selector("#tl .blk")
    pg.wait_for_function("typeof C !== 'undefined' && C && C.shots && window.effects && window.scopes", timeout=8000)
    pg._errors = errors
    return browser, pg


@pytest.fixture
def page(live_server, proj):
    _seed(proj)
    with sync_playwright() as pw:
        browser, pg = _open(pw, live_server)
        yield pg
        assert pg._errors == [], pg._errors
        browser.close()


def park(page, i: int, at: float = 0.5) -> str:
    """Select shot i and park the Program on it, its frame drawn."""
    page.locator("#tl .blk").nth(i).click()
    page.wait_for_selector("#inspector .ec [data-sec=lumetri]")
    page.evaluate(f"cueAt({i}, segs[{i}].in + {at})")
    page.wait_for_function(
        f"liveVideo() && liveVideo().readyState >= 2 && grade.shot === segs[{i}].id && grade.probe()",
        timeout=10000)
    return page.evaluate(f"segs[{i}].id")


def twirl(page, sub: str) -> None:
    """Open Lumetri Color › `sub` (Curves, Color Wheels, HSL Secondary are closed at first)."""
    if not page.locator(f"#inspector .ec-sub[data-sub={sub}]").evaluate("e => e.classList.contains('open')"):
        page.locator(f"#inspector [data-twirl={sub}]").click()
    page.locator(f"#inspector .ec-sub[data-sub={sub}] .ec-ssb").scroll_into_view_if_needed()


def undo_title(page) -> str:
    return page.locator("#undo").get_attribute("title") or ""


def wait_saved(page) -> None:
    page.wait_for_function("document.querySelector('#saveState').dataset.state === 'saved'", timeout=8000)


def arm(page, value: str, moved: float = 3.0) -> None:
    """Time a gesture from the input event in which its change lands to the frame that shows
    it. The clock starts at the timeStamp (when the browser took the input) of the latest
    pointermove, button down, whose handling changed the control's value (`value`, a JS
    expression read in a listener on window, after the board's own handler); it stops at the
    start of the first animation frame whose picture (`grade.probe()`) has moved more than
    `moved` code values on any channel from the probe taken now — read on the clock BEFORE
    that frame's probe, whose own draw and read-back are the test's, not the board's.

    Verdict 12, punch 9 (flaked at load 8-12: 152-323 ms against 150). The clock started at
    the drag's first move and stopped after the stopping frame's probe. Both clocks on the
    same gestures at load 18-21 (the browser at nice 19; 8 of each):
      * the old: 26-226 ms, the wheel's six-step push 80-226. Its first step (a sixth of the
        push) moved the grey 2-3 code values, under the threshold, so the picture crossed it
        on the 2nd-4th step, and every step is a Playwright `mouse.move`, a CDP round trip
        from the test process; and the probe's own GPU draw and read-back, 6-190 ms, was
        inside the clock;
      * this one: 1-31 ms.
    So the gestures here land their change in their first step, the clock times only the
    board (its handler, its preview, the next frame), and `frames_flow` waits out a box that
    is not giving the browser its frames before the gesture starts."""
    page.evaluate("""([moved, value]) => {
      const val = new Function('return JSON.stringify(' + value + ')');
      const P = window.__lm = { t0: null, t1: null, base: grade.probe(), draws: grade.draws, p: null,
                                v: val(), last: null, changes: 0, started: null };
      const crossed = (p) => !!p && p.some((v, i) => Math.abs(v - P.base[i]) > moved);
      const loop = () => {
        const now = performance.now();
        const p = grade.probe();
        if (P.last != null && crossed(p)) {
          P.t0 = P.last; P.t1 = now; P.p = p; P.drew = grade.draws - P.draws; return;
        }
        if (now - P.started < 3000) requestAnimationFrame(loop);
      };
      const landed = (e) => {
        if (!(e.buttons & 1)) return;
        const v = val();
        if (v === P.v) return;
        P.v = v; P.last = e.timeStamp; P.changes += 1;
        if (P.started == null) { P.started = performance.now(); requestAnimationFrame(loop); }
      };
      window.addEventListener('pointermove', landed);
      P.stop = () => window.removeEventListener('pointermove', landed);
    }""", [moved, value])


def frames_flow(page, n: int = 5, gap_ms: float = 50, within_ms: float = 5000) -> None:
    """Wait out the box (verdict 12, punch 9): until the page has drawn `n` frames in a row,
    each within `gap_ms` of the last, or `within_ms` has passed. On a box past its cores the
    browser (at the suite's nice 19) can go 100-250 ms without a frame; a gesture timed
    through such a gap times the box."""
    page.evaluate("""([n, gap, within]) => new Promise((ok) => {
      const t0 = performance.now();
      let last = null, run = 0;
      const tick = (ts) => {
        if (last != null) run = ts - last <= gap ? run + 1 : 0;
        last = ts;
        if (run >= n || performance.now() - t0 > within) ok(run); else requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    })""", [n, gap_ms, within_ms])


def armed_ms(page) -> float:
    page.wait_for_function("window.__lm.t1 != null", timeout=4000)
    return page.evaluate("(() => { window.__lm.stop(); return Math.max(0, window.__lm.t1 - window.__lm.t0); })()")


def within_limit(ms, what: str) -> None:
    """The 150 ms bar. Over it on a box already past twice its cores, the number says how
    busy the box is, not how the board answers: skipped, visibly, with the number and the
    load (test_tx_playback's `_check`, the suite's precedent). Anywhere else it fails."""
    cores, load = os.cpu_count() or 4, os.getloadavg()[0]
    if ms is not None and ms > LIMIT_MS and load > 2 * cores:
        pytest.skip(f"{what}: {ms:.0f} ms at load {load:.1f} on {cores} cores (past twice the cores): "
                    "a measure of the box, not the board")
    assert ms is not None and ms <= LIMIT_MS, (what, ms, f"load {load:.1f}")


def drag(page, x0, y0, x1, y1, steps=6) -> None:
    page.mouse.move(x0, y0)
    page.mouse.down()
    for k in range(1, steps + 1):
        page.mouse.move(x0 + (x1 - x0) * k / steps, y0 + (y1 - y0) * k / steps)
    page.mouse.up()


# ------------------------------------------------------------------ the export

def render_saved(proj, live_server, name: str) -> Path:
    """The cut exactly as the board saved it, through assemble.py as the board renders."""
    import httpx
    edl = httpx.get(f"{live_server}/api/project").json()
    path = proj["root"] / f"{name}.json"
    assert (edl.get("colour") or {}).get("mode") == "off"
    path.write_text(json.dumps(edl), encoding="utf-8")
    out = proj["root"] / f"{name}.mp4"
    r = subprocess.run(["uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(path),
                        "--footage", str(proj["footage"]), "--profile", "match", "-o", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return out


def frame_rgb(path: Path, t: float) -> np.ndarray:
    k = int(round(t * FPS))
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
                        "-ss", f"{max(0.0, (k - 0.25) / FPS):.4f}", "-frames:v", "1",
                        "-vf", "scale=in_range=tv:out_range=pc:in_color_matrix=bt709",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
    a = np.frombuffer(r.stdout, np.uint8).astype(np.float64).reshape(H, W, 3)
    return a[20:-20, 20:-20].reshape(-1, 3).mean(axis=0)


def same_move(preview: np.ndarray, export: np.ndarray) -> bool:
    """The export moved as the preview did: every channel within 3 % of full scale."""
    return bool(np.all(np.abs(preview - export) <= 0.03 * 255))


# ------------------------------------------------------------------ Curves

def test_a_curve_point_dragged_moves_the_program_and_the_export_alike(page, proj, live_server):
    park(page, 0)
    twirl(page, "curves")
    svg = page.locator("#inspector [data-cgraph]")
    b = svg.bounding_box()
    assert b["width"] > 120 and abs(b["width"] - b["height"]) < 2, b      # a square graph
    before = undo_title(page)
    frames_flow(page)
    arm(page, "effects.curve('master')")
    # press on the diagonal at x = 0.5: a new point there; drag it up to y = 0.72 (its first
    # step, a sixth of the way, lifts the grey ~9 code values: the change lands at once)
    x = b["x"] + 0.5 * b["width"]
    drag(page, x, b["y"] + 0.5 * b["height"], x, b["y"] + (1 - 0.72) * b["height"])
    ms = armed_ms(page)
    within_limit(ms, "a curve point dragged")
    assert page.evaluate("window.__lm.drew") >= 0
    pts = page.evaluate("effects.curve('master')")
    assert len(pts) == 3 and pts[1][0] == pytest.approx(0.5, abs=0.02) and pts[1][1] == pytest.approx(0.72, abs=0.02), pts
    # drawn as Premiere draws it: three points and the curve through them
    assert page.locator("#inspector .lm-cpt").count() == 3
    base = np.array(page.evaluate("window.__lm.base"))
    after = np.array(page.evaluate("grade.probe()"))
    assert np.all(after - base > 25), (base, after)                      # the mid grey lifted
    # one drag, one undo entry
    assert undo_title(page).startswith("undo: add curve point"), undo_title(page)
    page.keyboard.press("Control+z")
    assert page.evaluate("effects.curve('master')") == [[0, 0], [1, 1]]
    assert undo_title(page) == before
    page.keyboard.press("Control+Shift+z")
    assert len(page.evaluate("effects.curve('master')")) == 3
    # a drag of the point it made is its own single entry
    cy = b["y"] + (1 - pts[1][1]) * b["height"]
    drag(page, x, cy, x, cy + 0.04 * b["height"])
    assert undo_title(page).startswith("undo: move curve point")
    page.keyboard.press("Control+z")
    assert page.evaluate("effects.curve('master')[1][1]") == pytest.approx(pts[1][1], abs=1e-3)
    wait_saved(page)
    shown = np.array(page.evaluate("grade.probe()"))
    # the export of the cut the board saved: g1 carries the curve, g2 is the same grey without it
    film = render_saved(proj, live_server, "curve")
    with_, without = frame_rgb(film, 1.0), frame_rgb(film, 3.0)
    dp, de = shown - base, with_ - without
    print(f"\ncurve: preview Δ {np.round(dp, 2)} · export Δ {np.round(de, 2)}")
    assert same_move(dp, de), (dp, de)


def test_ctrl_click_deletes_a_curve_point_and_channels_are_their_own(page):
    park(page, 0)
    twirl(page, "curves")
    assert page.evaluate("effects.curve('r', [[0, 0], [0.5, 0.3], [1, 1]])")
    page.locator("#inspector .lm-ch[data-ch=r]").click()
    assert page.locator("#inspector [data-cgraph]").get_attribute("data-ch") == "r"
    assert page.locator("#inspector .lm-cpt").count() == 3
    p = np.array(page.evaluate("grade.probe()"))
    assert p[0] < p[1] - 20, p                                          # red pulled down
    pt = page.locator("#inspector .lm-cpt").nth(1)
    pt.click(modifiers=["Control"])
    assert page.evaluate("effects.curve('r')") == [[0, 0], [1, 1]]
    assert undo_title(page).startswith("undo: delete curve point")
    page.keyboard.press("Control+z")
    assert len(page.evaluate("effects.curve('r')")) == 3
    # a double-click puts the channel back to the diagonal, one entry
    svg = page.locator("#inspector [data-cgraph]")
    b = svg.bounding_box()
    page.mouse.dblclick(b["x"] + 0.9 * b["width"], b["y"] + 0.9 * b["height"])
    assert page.evaluate("effects.curve('r')") == [[0, 0], [1, 1]]


# ------------------------------------------------------------------ Color Wheels

def test_a_wheel_push_moves_the_program_and_the_export_alike(page, proj, live_server):
    park(page, 0)
    twirl(page, "wheels")
    disc = page.locator("#inspector [data-wd=midtones]")
    b = disc.bounding_box()
    assert b["width"] > 40, b
    before = undo_title(page)
    frames_flow(page)
    arm(page, "effects.wheel('midtones')")
    cx, cy = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
    # push toward red: up (and a little left, where red sits on the vectorscope), in two
    # steps, so the first (half the push) lands a change the picture shows (a sixth moved the
    # grey 2-3 code values, under the threshold)
    drag(page, cx, cy, cx - 0.06 * b["width"], cy - 0.3 * b["height"], steps=2)
    ms = armed_ms(page)
    within_limit(ms, "a wheel pushed")
    cb, cr, li = page.evaluate("effects.wheel('midtones')")
    assert cr == pytest.approx(0.6, abs=0.05) and cb == pytest.approx(-0.12, abs=0.05) and li == 0
    base = np.array(page.evaluate("window.__lm.base"))
    after = np.array(page.evaluate("grade.probe()"))
    assert after[0] - base[0] > 10 and base[1] - after[1] > 2, (base, after)     # warmer: +R, −G
    assert undo_title(page).startswith("undo: midtones wheel")
    page.keyboard.press("Control+z")
    assert page.evaluate("effects.wheel('midtones')") == [0, 0, 0]
    assert undo_title(page) == before
    page.keyboard.press("Control+Shift+z")
    # the lightness slider: a drag, one entry
    sl = page.locator("#inspector [data-wl=shadows]")
    sb = sl.bounding_box()
    sx = sb["x"] + sb["width"] / 2
    drag(page, sx, sb["y"] + sb["height"] / 2, sx, sb["y"] + 0.2 * sb["height"], steps=4)
    li = page.evaluate("effects.wheel('shadows')[2]")
    assert li > 30, li
    assert undo_title(page).startswith("undo: shadows lightness")
    page.keyboard.press("Control+z")
    assert page.evaluate("effects.wheel('shadows')") == [0, 0, 0]
    page.keyboard.press("Control+Shift+z")
    # a double-click centres the puck, keeping its lightness
    page.evaluate("effects.wheel('shadows', [0.3, 0.2, 40])")
    page.locator("#inspector [data-wd=shadows]").dblclick()
    assert page.evaluate("effects.wheel('shadows')") == [0, 0, 40]
    wait_saved(page)
    shown = np.array(page.evaluate("grade.probe()"))
    film = render_saved(proj, live_server, "wheel")
    with_, without = frame_rgb(film, 1.0), frame_rgb(film, 3.0)
    dp, de = shown - base, with_ - without
    print(f"\nwheel: preview Δ {np.round(dp, 2)} · export Δ {np.round(de, 2)}")
    assert same_move(dp, de), (dp, de)


# ------------------------------------------------------------------ HSL Secondary

def test_the_hsl_secondary_turns_the_keyed_red_and_exports_it(page, proj, live_server):
    park(page, 2)                                                     # RED
    twirl(page, "hsl")
    base = np.array(page.evaluate("grade.probe()"))
    for key, v in (("hsl_width", 25), ("hsl_shift", 120), ("hsl_saturation", 70)):
        assert page.evaluate(f"effects.set('{key}', {v})"), key
    page.wait_for_function("(() => { const p = grade.probe(); return p && p[1] > p[0]; })()", timeout=2000)
    shown = np.array(page.evaluate("grade.probe()"))
    assert undo_title(page).startswith("undo: hsl saturation")
    assert page.locator("#inspector .ec-sl[data-sl=hsl_shift]").input_value() == "120"
    # the same key leaves blue as it is
    page.evaluate("effects.set('hsl_shift', 120, segs[3])")
    park(page, 3)
    blue = np.array(page.evaluate("grade.probe()"))
    page.evaluate("grade.enabled = false")
    raw_blue = np.array(page.evaluate("grade.probe()"))
    page.evaluate("grade.enabled = true")
    assert np.all(np.abs(blue - raw_blue) <= 2), (blue, raw_blue)
    wait_saved(page)
    film = render_saved(proj, live_server, "hsl")
    import httpx
    hsl = httpx.get(f"{live_server}/api/project").json()["segments"][2]["controls"]["hsl"]
    assert hsl == {"width": 25, "shift": 120, "saturation": 70}
    # the export's red shot against the clip's own red, the same way the preview moved
    got, plain = frame_rgb(film, 5.0), frame_rgb(proj["footage"] / "RED.MP4", 1.0)
    print(f"\nhsl: preview {np.round(shown, 1)} (from {np.round(base, 1)}) · export {np.round(got, 1)} (from {np.round(plain, 1)})")
    assert same_move(shown - base, got - plain), (shown, base, got, plain)


# ------------------------------------------------------------------ Lumetri Scopes

def open_scopes(page) -> None:
    page.locator("#scopesTab").click()
    page.wait_for_function("scopes.on && scopes.stats()", timeout=4000)


def fresh_stats(page, after: float):
    page.wait_for_function(f"scopes.stats() && scopes.stats().at > {after}", timeout=4000)
    return page.evaluate("scopes.stats()")


def test_a_solid_grey_is_a_flat_line_at_its_ire(page):
    park(page, 0)
    open_scopes(page)
    assert page.locator("#scopes").is_visible() and not page.locator("#srcScreen").is_visible()
    s = fresh_stats(page, 0)
    probe = page.evaluate("grade.probe()")
    ire = (0.2126 * probe[0] + 0.7152 * probe[1] + 0.0722 * probe[2]) / 255 * 100
    assert abs(ire - 128 / 255 * 100) < 1.5, ire                        # the fixture's 0x808080
    assert s["wave"]["max"] - s["wave"]["min"] <= 1.0, s["wave"]        # flat: one line
    assert all(abs(lo - ire) <= 1.0 and abs(hi - ire) <= 1.0 for lo, hi in s["wave"]["cols"]), s["wave"]["cols"][:4]
    assert s["vec"]["meanChroma"] < 0.01, s["vec"]                      # a grey sits at the centre
    # live while parked: a curve on the grey moves the line within 150 ms. Timed in the page
    # from the change, on the frames the page draws: the scopes sample on the frame after
    # the Program draws, so the line moves 2-3 frames after the change; a throttled sample
    # (PLAY_HZ, as while playing) takes 12. Verdict 12, punch 9: three CDP round trips used
    # to sit inside this clock, and the rest is the board's frame work at the suite's nice 19
    # behind the other loops — 80-159 ms in 2-3 frames at load 11-14 (the scopes' own GPU
    # read-back 42-99 ms of it), 152-425 ms at load 10-25 in 10 of 20 neighbourhood runs.
    # So: within 150 ms of frames (LIMIT_FRAMES at 60 Hz) always, and within 150 ms of the
    # clock on a box under its cores.
    frames_flow(page)
    r = page.evaluate("""() => new Promise((ok) => {
      const t0 = performance.now();
      effects.curve('master', [[0, 0], [0.5, 0.3], [1, 1]]);
      let frames = 0;
      const loop = () => {
        frames += 1;
        const s = scopes.stats(), ms = performance.now() - t0;
        if (s && s.wave.max < 40) ok({ms, frames});
        else if (ms < 3000) requestAnimationFrame(loop);
        else ok({ms: null, frames});
      };
      requestAnimationFrame(loop);
    })""")
    load, cores = os.getloadavg()[0], os.cpu_count() or 4
    assert r["ms"] is not None and r["frames"] <= LIMIT_FRAMES, (r, f"load {load:.1f}")
    if load <= cores:
        assert r["ms"] <= LIMIT_MS, (r, f"load {load:.1f}")
    s2 = page.evaluate("scopes.stats()")
    assert s2["wave"]["max"] - s2["wave"]["min"] <= 1.0
    want = colour.curve_eval([[0, 0], [0.5, 0.3], [1, 1]], np.array([128 / 255]))[0] * 100
    assert abs(s2["wave"]["mean"] - want) <= 1.5, (s2["wave"], want)
    # and the Source tab brings the Source monitor back
    page.locator("#srcTitle").click()
    assert not page.locator("#scopes").is_visible() and not page.evaluate("scopes.on")


@pytest.mark.parametrize("i,name,rgb", [(2, "RED", (1, 0, 0)), (3, "BLUE", (0, 0, 1))])
def test_a_pure_colour_lands_at_its_hue_on_the_vectorscope(page, i, name, rgb):
    park(page, i)
    open_scopes(page)
    s = fresh_stats(page, 0)
    y = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    want = math.degrees(math.atan2((rgb[0] - y) / 1.5748, (rgb[2] - y) / 1.8556)) % 360
    got = s["vec"]["angle"]
    assert abs((got - want + 180) % 360 - 180) <= 3, (name, got, want)
    assert s["vec"]["chroma"] > 0.4, s["vec"]                           # out by its target
    # the hue shifted by the HSL Secondary moves the point by as much
    if name == "RED":
        page.evaluate("effects.set('hsl_shift', -40)")
        page.wait_for_function(f"Math.abs(scopes.stats().vec.angle - {want - 40}) < 4", timeout=2000)


def test_the_scopes_are_throttled_while_playing(page):
    park(page, 0, at=0.1)
    open_scopes(page)
    fresh_stats(page, 0)
    n0 = page.evaluate("scopes.samples")
    page.locator("#playCut").click()
    page.wait_for_function("player.playing", timeout=4000)
    time.sleep(2.0)
    n = page.evaluate("scopes.samples") - n0
    page.locator("#playCut").click()
    assert 3 <= n <= 13, n                                               # ~5 a second, not 60


# ------------------------------------------------------------------ the formula, twice

TABLE_CASES = [
    {"curves": {"master": [[0, 0], [0.5, 0.72], [1, 1]]}},
    {"curves": {"master": [[0, 0.05], [0.3, 0.2], [0.7, 0.85], [1, 0.95]], "g": [[0, 0], [0.6, 0.5], [1, 1]]}},
    {"wheels": {"midtones": [-0.12, 0.6, 0]}},
    {"wheels": {"shadows": [0.4, -0.3, 20], "highlights": [-0.2, 0.1, -30], "midtones": [0, 0, 15]},
     "curves": {"b": [[0, 0], [0.25, 0.4], [1, 1]]}},
]


def test_the_shaders_table_is_the_formulas_code_for_code(page):
    for c in TABLE_CASES:
        js = np.array(page.evaluate("(c) => grade.lumetri.table(c)", c)).reshape(256, 4)[:, :3].T
        py = np.floor(colour.creative_table(c, 256) * 255 + 0.5)
        assert np.max(np.abs(js - py)) <= 1, (c, np.max(np.abs(js - py)))
        assert np.mean(js == py) > 0.99, c


# ------------------------------------------------------------------ the record

def test_values_survive_a_reload_and_the_panel_draws_them(live_server, proj):
    _seed(proj)
    with sync_playwright() as pw:
        browser, pg = _open(pw, live_server)
        park(pg, 0)
        pg.evaluate("effects.curve('g', [[0, 0], [0.4, 0.55], [1, 1]])")
        pg.evaluate("effects.wheel('highlights', [0.2, -0.1, 10])")
        pg.evaluate("effects.set('hsl_hue', 200)")
        pg.evaluate("effects.set('hsl_tint', 30)")
        wait_saved(pg)
        browser.close()
    import httpx
    c = httpx.get(f"{live_server}/api/project").json()["segments"][0]["controls"]
    assert c == {"curves": {"g": [[0, 0], [0.4, 0.55], [1, 1]]}, "wheels": {"highlights": [0.2, -0.1, 10]},
                 "hsl": {"hue": 200, "tint": 30}}, c
    with sync_playwright() as pw:
        browser, pg = _open(pw, live_server)
        park(pg, 0)
        assert pg.evaluate("effects.curve('g')") == [[0, 0], [0.4, 0.55], [1, 1]]
        assert pg.evaluate("effects.wheel('highlights')") == [0.2, -0.1, 10]
        assert pg.evaluate("effects.value('hsl_hue')") == 200
        twirl(pg, "wheels")
        left = pg.locator("#inspector [data-wd=highlights] .lm-puck").evaluate("e => e.style.left")
        assert float(left.rstrip("%")) == pytest.approx(60), left
        # Lumetri's reset takes all of it back, one entry
        pg.locator("#inspector .ec-sreset[data-sreset=lumetri]").click()
        assert pg.evaluate("segs[0].controls") is None
        pg.keyboard.press("Control+z")
        assert pg.evaluate("effects.wheel('highlights')") == [0.2, -0.1, 10]
        assert pg._errors == [], pg._errors
        browser.close()


def test_the_server_refuses_values_out_of_range(live_server, proj):
    import httpx
    _seed(proj)
    seg = {"clip": "GREY.MP4", "in": 0.0, "out": 2.0, "id": "g1"}
    for bad in ({"curves": {"master": [[0, 0], [1, 1.5]]}}, {"curves": {"luma": [[0, 0], [1, 1]]}},
                {"curves": {"r": [[0.5, 0.5], [0.4, 0.6]]}}, {"wheels": {"shadows": [0.8, 0.8, 0]}},
                {"wheels": {"midtones": [0, 0, 150]}}, {"wheels": {"gain": [0, 0, 0]}},
                {"hsl": {"hue": 361}}, {"hsl": {"saturation": 201}}, {"hsl": {"sat_lo": 80, "sat_hi": 20}},
                {"hsl": {"glow": 1}}):
        r = httpx.put(f"{live_server}/api/project", json={"segments": [{**seg, "controls": bad}]})
        assert r.status_code == 400, (bad, r.text)
    ok = {"curves": {"master": [[0, 0], [0.5, 0.6], [1, 1]], "r": [[0, 0], [1, 1]]},
          "wheels": {"shadows": [0, 0, 0], "midtones": [0.1, 0.2, 5]}, "hsl": {"width": 30, "shift": 45}}
    r = httpx.put(f"{live_server}/api/project", json={"segments": [{**seg, "controls": ok}]})
    assert r.status_code == 200, r.text
    assert httpx.get(f"{live_server}/api/project").json()["segments"][0]["controls"] == {
        "curves": {"master": [[0, 0], [0.5, 0.6], [1, 1]]}, "wheels": {"midtones": [0.1, 0.2, 5]},
        "hsl": {"shift": 45}}


def test_a_v2_clip_takes_no_lumetri(live_server, proj):
    """V2EC's rule: the panel of a V2+ clip has no Lumetri, the API refuses a colour edit
    on it, and the server refuses Curves / Wheels / HSL in an overlay's controls."""
    import httpx
    ov = [{"id": "o1", "clip": "BLUE.MP4", "in": 0.0, "out": 1.0, "at": 6.5, "track": "V2"}]
    _seed(proj, overlays=ov)
    with sync_playwright() as pw:
        browser, pg = _open(pw, live_server)
        pg.wait_for_function("tl.overlayById && tl.overlayById('o1')", timeout=5000)
        before = undo_title(pg)
        assert pg.evaluate("effects.curve('master', [[0, 0], [0.5, 0.8], [1, 1]], {id: 'o1'})") is False
        assert pg.evaluate("effects.wheel('midtones', [0, 0.5, 0], {id: 'o1'})") is False
        assert pg.evaluate("effects.set('hsl_shift', 90, {id: 'o1'})") is False
        assert pg.evaluate("tl.overlayById('o1').controls") is None
        assert undo_title(pg) == before
        assert pg._errors == [], pg._errors
        browser.close()
    r = httpx.put(f"{live_server}/api/project", json={
        "segments": SEGS, "overlays": [{**ov[0], "controls": {"curves": {"master": [[0, 0], [0.5, 0.8], [1, 1]]}}}]})
    assert r.status_code == 400 and "V1" in r.text, r.text
