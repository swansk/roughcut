"""Blur & Sharpen › Sharpen (redesign wave 15, lane VIGNETTE; verdict 11 punch 7,
REDESIGN-GOAL dimensions 7 and 15).

Premiere's Effects bin has Sharpen beside Gaussian Blur; so does this one. One parameter,
Sharpen Amount: THE formula is roughcut/vfx.py's — an unsharp mask on the frame's own
pixels, out = x + (Amount/100)·(x − B∗x) with B the 3 × 3 binomial blur, one 3 × 3 kernel
(`vfx.sharpen_kernel`). The Program runs it as an SVG feConvolveMatrix on the picture's
layers (effects.js `vfxLook`); the export as `convolution` on each Y'CbCr plane
(research/tools/assemble.py `sharpen_graph`). Measured: the export is THE kernel on the
plain export's planes (mean |Δ| under a code value); the picture's detail rises in the
Program and in the export; a keyed Amount follows its keys; the bin's double-click and a
typed Amount are one undo each. The AI's `effect` op with kind `sharpen` is
test_actions_ui.py's (AI op = hand op) and test_actions.py's.

The fixture is test_render_vfx.py's: CLIP_B a test pattern, 320 × 180 at 24 fps.
"""

from __future__ import annotations

import json
import socket
import subprocess
import threading
import time

import numpy as np
import pytest

from test_render_vfx import EDL, SEGS, detail, frame, luma, proj, render, vfx  # noqa: F401

H, W = 180, 320


# ------------------------------------------------------------------ the schema

def test_sharpen_is_a_video_effect_with_one_amount():
    assert vfx.KINDS["sharpen"].folder == "Blur & Sharpen"
    assert [(p.key, p.label, p.lo, p.hi, p.default) for p in vfx.KINDS["sharpen"].params] == \
        [("amount", "Sharpen Amount", 0.0, 500.0, 0.0)]
    assert vfx.clean([{"id": "sharpen", "kind": "sharpen", "params": {"amount": 0}}]) == \
        [{"id": "sharpen", "kind": "sharpen"}]
    assert vfx.clean([{"id": "sharpen", "kind": "sharpen", "params": {"amount": 150}}]) == \
        [{"id": "sharpen", "kind": "sharpen", "params": {"amount": 150.0}}]
    with pytest.raises(ValueError, match="0..500"):
        vfx.clean([{"id": "sharpen", "kind": "sharpen", "params": {"amount": 501}}])
    k = vfx.sharpen_kernel(100)
    assert sum(k) == pytest.approx(1.0) and k[4] == pytest.approx(1.75) and k[0] == pytest.approx(-1 / 16)
    assert vfx.sharpen_kernel(0) == [0, 0, 0, 0, 1, 0, 0, 0, 0]
    import server
    assert server.clean_controls({"vfx": [{"id": "sharpen", "kind": "sharpen", "params": {"amount": 80}}],
                                  "keys": {"amount@sharpen": [[0, 0], [1, 80]]}}) == \
        {"vfx": [{"id": "sharpen", "kind": "sharpen", "params": {"amount": 80.0}}],
         "keys": {"amount@sharpen": [[0.0, 0.0], [1.0, 80.0]]}}


# ------------------------------------------------------------------ the export

def yuv_planes(path, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
                        "-vf", f"select=eq(n\\,{n})", "-frames:v", "1", "-vsync", "0",
                        "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"], capture_output=True, check=True)
    b = np.frombuffer(r.stdout, np.uint8).astype(np.float64)
    y = b[:W * H].reshape(H, W)
    u = b[W * H:W * H + W * H // 4].reshape(H // 2, W // 2)
    v = b[W * H + W * H // 4:].reshape(H // 2, W // 2)
    return y, u, v


def convolve(p: np.ndarray, k: list[float]) -> np.ndarray:
    """A 3 × 3 kernel with the edge pixels repeated, clipped to 8 bits as ffmpeg's is."""
    q = np.pad(p, 1, mode="edge")
    h, w = p.shape
    out = sum(k[3 * j + i] * q[j:j + h, i:i + w] for j in range(3) for i in range(3))
    return np.clip(np.floor(out + 0.5), 0, 255)


def one(controls: dict | None) -> dict:
    return {**EDL, "segments": [{**SEGS[1], "controls": controls}]}


def test_the_export_is_the_kernel_on_every_plane(proj):
    plain, _ = render(proj, one(None), "sh-plain")
    sharp, log = render(proj, one({"vfx": [{"id": "sharpen", "kind": "sharpen", "params": {"amount": 120}}]}),
                        "sh-120")
    assert "Sharpen amount 120" in log, log
    k = vfx.sharpen_kernel(120)
    for got, base in zip(yuv_planes(sharp, 12), yuv_planes(plain, 12)):
        err = np.abs(got - convolve(base, k))
        print(f"\nplane {got.shape}: mean |export − kernel(plain)| {err.mean():.2f}, worst {err.max():.0f}")
        assert err.mean() < 1.5, err.mean()
    d0, d1 = detail(frame(plain, 12)), detail(frame(sharp, 12))
    print(f"detail: plain {d0:.0f} → sharpened {d1:.0f} ({d1 / d0:.2f}×)")
    assert d1 > 1.4 * d0


def test_a_keyed_amount_sharpens_frame_by_frame(proj):
    film, _ = render(proj, one({"vfx": [{"id": "sharpen", "kind": "sharpen"}],
                                "keys": {"amount@sharpen": [[0.0, 0.0], [1.0, 200.0]]}}), "sh-keyed")
    plain, _ = render(proj, one(None), "sh-plain2")
    ds = [detail(frame(film, n)) / detail(frame(plain, n)) for n in (0, 12, 24, 36)]
    print("\nkeyed: detail ×", [round(x, 2) for x in ds])
    assert ds[0] == pytest.approx(1.0, abs=0.03) and ds[0] < ds[1] < ds[2] and ds[2] == pytest.approx(ds[3], abs=0.12)
    k = vfx.sharpen_kernel(100)                       # half way: Amount 100 at 0.5 s
    got, base = yuv_planes(film, 12)[0], yuv_planes(plain, 12)[0]
    assert np.abs(got - convolve(base, k)).mean() < 1.5


# ------------------------------------------------------------------ the board

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(proj):
    import uvicorn
    import server
    server.configure(proj["edl"], proj["footage"], proj["sidecars"], proj["work"],
                     proxies=False, visual=proj["work"] / "no-visual", assets=proj["assets"])
    server.ensure_proxies(["CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"])
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


@pytest.fixture()
def board(live_server, proj):
    playwright_api = pytest.importorskip("playwright.sync_api")
    proj["edl"].write_text(json.dumps(EDL, indent=1), encoding="utf-8")
    with playwright_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.add_init_script("try { localStorage.setItem('roughcut.tour', 'seen') } catch (e) {}")
        pg.goto(live_server)
        pg.wait_for_selector("#tl .blk")
        pg.wait_for_function("segs.length === 3 && window.effects && !!effects.vfxAdd")
        yield pg
        assert not errors, errors
        browser.close()


def settle_program(pg, t: float):
    pg.evaluate(f"tl.seek({t})")
    pg.wait_for_function("(() => { const v = liveVideo(); return v && v.readyState >= 2 && !v.seeking; })()",
                         timeout=20000)
    pg.wait_for_timeout(500)


def program(pg) -> np.ndarray:
    png = pg.locator("#player .screen").screenshot()
    box = pg.locator("#player .screen").bounding_box()
    w, h = int(round(box["width"])), int(round(box["height"]))
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-f", "png_pipe", "-i", "-",
                        "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       input=png, capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).astype(np.float64).reshape(h, w, 3)


def picture(a: np.ndarray) -> np.ndarray:
    """The picture inside the monitor (its letterbox rows and pillar columns cut away)."""
    y = luma(a)
    rows = np.where(y.max(axis=1) > 20)[0]
    cols = np.where(y.max(axis=0) > 20)[0]
    return a[rows[0] + 2:rows[-1] - 1, cols[0] + 2:cols[-1] - 1]


def type_value(pg, key: str, value: float):
    pg.locator(f"#inspector .ec-v[data-k='{key}']").click()
    inp = pg.locator(f"#inspector .ec-v.editing[data-k='{key}']")
    inp.fill(str(value))
    inp.press("Enter")


def saved(pg, proj, want, timeout=8.0) -> dict:
    pg.evaluate("flushSave()")
    t0 = time.time()
    while time.time() - t0 < timeout:
        edl = json.loads(proj["edl"].read_text(encoding="utf-8"))
        if want(edl):
            return edl
        time.sleep(0.2)
        pg.evaluate("flushSave()")
    raise AssertionError(proj["edl"].read_text(encoding="utf-8"))


def test_sharpen_from_the_bin_sharpens_the_program_and_the_export(board, proj):
    """The bin lists Sharpen in Blur & Sharpen; a double-click with shot 2 selected puts it
    on (one undo, "undo: Sharpen"), its section has Sharpen Amount with a stopwatch; typing
    150 (one undo) raises the Program's detail at once (an SVG feConvolveMatrix of THE
    kernel) and the export of the saved cut is as crisp; Ctrl+Z twice takes it all back."""
    pg = board
    pg.locator("#tl .blk[data-id=s2]").click()
    settle_program(pg, 2.5)
    before = picture(program(pg))
    pg.locator(".ws-tab[data-tool=effects]").click()
    pg.wait_for_selector("#fxbin .fxb-item")
    if not pg.locator("#fxbin .fxb-item[data-vfx]").count():
        pg.locator("#fxbin .fxb-folder[data-folder=veffects]").click()
    items = pg.eval_on_selector_all("#fxbin .fxb-item[data-vfx]", "els => els.map(e => e.dataset.vfx)")
    assert items[:2] == ["gaussian_blur", "sharpen"], items
    pg.locator("#fxbinQ").fill("sharp")
    assert [t.strip() for t in pg.locator("#fxbin .fxb-item .nm").all_inner_texts()] == ["Gaussian Blur", "Sharpen"]
    pg.locator("#fxbinQ").fill("")
    pg.locator("#fxbin .fxb-item[data-vfx=sharpen]").dblclick()
    assert pg.evaluate("effects.vfx(tl.byId('s2'))") == [{"id": "sharpen", "kind": "sharpen"}]
    assert pg.locator("#undo").get_attribute("title").startswith("undo: Sharpen")
    sec = pg.locator("#inspector .ec-vsec[data-kind=sharpen]")
    sec.wait_for()
    assert sec.locator(".ec-row .ec-name").all_inner_texts() == ["Sharpen Amount"]
    assert sec.locator(".ec-sw").count() == 1
    type_value(pg, "amount@sharpen", 150)
    assert pg.locator("#undo").get_attribute("title").startswith("undo: sharpen sharpen amount")
    assert "url(" in pg.evaluate("getComputedStyle(liveVideo()).filter")
    km = pg.evaluate("document.querySelector('#ecvs0 feConvolveMatrix').getAttribute('kernelMatrix')")
    assert [float(x) for x in km.split()] == pytest.approx(vfx.sharpen_kernel(150), abs=1e-4)
    settle_program(pg, 2.5)
    after = picture(program(pg))
    d0, d1 = detail(before), detail(after)
    edl = saved(pg, proj, lambda e: (e["segments"][1].get("controls") or {}).get("vfx"))
    assert edl["segments"][1]["controls"] == {"vfx": [{"id": "sharpen", "kind": "sharpen", "params": {"amount": 150.0}}]}
    plain, _ = render(proj, {**edl, "segments": [{**edl["segments"][1], "controls": None}]}, "sh-b-plain")
    out, _ = render(proj, {**edl, "segments": edl["segments"][1:2]}, "sh-b")
    e0, e1 = detail(frame(plain, 36)), detail(frame(out, 36))
    print(f"\nProgram detail {d0:.0f} → {d1:.0f} ({d1 / d0:.2f}×) · export {e0:.0f} → {e1:.0f} ({e1 / e0:.2f}×)")
    assert d1 > 1.3 * d0 and e1 > 1.3 * e0
    pg.evaluate("document.activeElement && document.activeElement.blur()")
    pg.keyboard.press("Control+z")
    assert pg.evaluate("effects.vfx(tl.byId('s2'))") == [{"id": "sharpen", "kind": "sharpen"}]
    pg.keyboard.press("Control+z")
    assert pg.evaluate("effects.vfx(tl.byId('s2'))") == []
    pg.wait_for_function("getComputedStyle(liveVideo()).filter === 'none'")
