"""Video effects in the Effects bin (wave 14, lane VFX; verdict 10, punch 8; REDESIGN-GOAL
dimensions 7 and 15).

Premiere's Effects panel › Video Effects: Blur & Sharpen › Gaussian Blur, Distort ›
Transform, Transform › Crop. Each is dragged from the bin onto a clip (or onto Effect
Controls, or double-clicked with a clip selected) into that clip's Effect Controls stack,
below Lumetri Color, with its fx toggle, order, reset, clear and a stopwatch on every
number; the Program shows it at once (CSS on the picture's layers, effects.js `vfxLook`;
Gaussian Blur and Sharpen in the grade canvas's shader since wave 16, test_vfx_playback.py)
and the export draws the same (research/tools/assemble.py `vfx_graph`; roughcut/vfx.py
has the schema and THE formulas; test_render_vfx.py the export's own measures). Done,
verdict 10's words: a 10 % left Crop shows a black band in the Program and in the
exported frame (±2 px); a Gaussian Blur visibly blurs both; one undo per drop or drag
(the AI-op = hand-op EDL test is test_actions_ui.py's
`test_video_effects_crop_transform_and_blur`).
"""

from __future__ import annotations

import json
import socket
import subprocess
import threading
import time

import numpy as np
import pytest

from test_render_vfx import (EDL, W, band, detail, frame, luma, proj, render,  # noqa: F401
                             vfx)

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
        pg.goto(live_server)
        pg.wait_for_selector("#tl .blk")
        pg.wait_for_function("segs.length === 3 && window.effects && !!effects.vfxAdd")
        yield pg
        assert not errors, errors
        browser.close()


def on_disk(proj) -> dict:
    return json.loads(proj["edl"].read_text(encoding="utf-8"))


def saved(pg, proj, want, timeout=8.0) -> dict:
    pg.evaluate("flushSave()")
    t0 = time.time()
    while time.time() - t0 < timeout:
        edl = on_disk(proj)
        if want(edl):
            return edl
        time.sleep(0.2)
        pg.evaluate("flushSave()")
    raise AssertionError(on_disk(proj)["segments"])


def open_bin(pg):
    """The Effects tab, its Video Effects folder opened (it starts folded, as Premiere's)."""
    pg.locator(".ws-tab[data-tool=effects]").click()
    pg.wait_for_selector("#fxbin .fxb-item")
    if not pg.locator("#fxbin .fxb-item[data-vfx]").count():
        pg.locator("#fxbin .fxb-folder[data-folder=veffects]").click()
    pg.wait_for_selector("#fxbin .fxb-item[data-vfx=crop]")


def drag(pg, name: str, x: float, y: float):
    """A real mouse drag of the bin's item `name` to client (x, y) (Chromium's own drag)."""
    it = pg.locator("#fxbin .fxb-item", has_text=name).last.bounding_box()
    pg.mouse.move(it["x"] + it["width"] / 2, it["y"] + it["height"] / 2)
    pg.mouse.down()
    pg.mouse.move(it["x"] + it["width"] / 2 + 20, it["y"] + it["height"] / 2, steps=4)
    pg.mouse.move(x, y, steps=12)
    pg.mouse.up()


def block_xy(pg, sid: str) -> tuple[float, float]:
    b = pg.locator(f"#tl .blk[data-id={sid}]").bounding_box()
    return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2


def type_value(pg, key: str, value: float):
    """Premiere's way: click the blue number, type, Enter."""
    pg.locator(f"#inspector .ec-v[data-k='{key}']").click()
    inp = pg.locator(f"#inspector .ec-v.editing[data-k='{key}']")
    inp.fill(str(value))
    inp.press("Enter")


def settle_program(pg, t: float):
    pg.evaluate(f"tl.seek({t})")
    pg.wait_for_function("(() => { const v = liveVideo(); return v && v.readyState >= 2 && !v.seeking; })()",
                         timeout=20000)
    pg.wait_for_timeout(500)


def program(pg) -> np.ndarray:
    """The Program's picture as shown, at its own display size."""
    png = pg.locator("#player .screen").screenshot()
    box = pg.locator("#player .screen").bounding_box()
    w, h = int(round(box["width"])), int(round(box["height"]))
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-f", "png_pipe", "-i", "-",
                        "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       input=png, capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).astype(np.float64).reshape(h, w, 3)


def test_the_bin_lists_premieres_video_effects_and_finds_them(board):
    pg = board
    pg.locator(".ws-tab[data-tool=effects]").click()
    vf = pg.locator("#fxbin .fxb-folder[data-folder=veffects]")
    assert vf.get_attribute("aria-expanded") == "false"          # folded, as Premiere's folders start
    assert pg.locator("#fxbin .fxb-item[data-vfx]").count() == 0
    open_bin(pg)
    folders = [t.strip() for t in pg.locator("#fxbin .fxb-folder .nm").all_inner_texts()]
    assert folders == ["Audio Transitions", "Crossfade", "Video Effects", "Blur & Sharpen", "Distort",
                       "Transform", "Video Transitions", "Dissolve"], folders
    items = pg.eval_on_selector_all("#fxbin .fxb-item[data-vfx]", "els => els.map(e => [e.dataset.vfx, e.textContent.trim()])")
    # (Sharpen beside Gaussian Blur: wave 15, VIGNETTE)
    assert items == [["gaussian_blur", "Gaussian Blur"], ["sharpen", "Sharpen"], ["transform", "Transform"],
                     ["crop", "Crop"]]
    q = pg.locator("#fxbinQ")
    q.fill("gaussian")
    assert [t.strip() for t in pg.locator("#fxbin .fxb-item .nm").all_inner_texts()] == ["Gaussian Blur"]
    q.fill("blur")                            # the folder's name matches: all of Blur & Sharpen
    assert [t.strip() for t in pg.locator("#fxbin .fxb-item .nm").all_inner_texts()] == ["Gaussian Blur", "Sharpen"]
    q.fill("transform")                       # Premiere's: the Transform folder (Crop) and Distort's Transform
    assert [t.strip() for t in pg.locator("#fxbin .fxb-item .nm").all_inner_texts()] == ["Transform", "Crop"]
    q.fill("")
    # the bin's kinds are vfx.py's, with its ranges and defaults (one schema, two languages)
    js = pg.evaluate("effects.VFX")
    assert set(js) == set(vfx.KINDS)
    for kind, K in vfx.KINDS.items():
        assert js[kind]["name"] == K.name and js[kind]["folder"] == K.folder
        got = {p["key"]: (p["lo"], p["hi"], p["dflt"]) for p in js[kind]["params"]}
        assert got == {p.key: (p.lo, p.hi, p.default) for p in K.params}, kind
        assert {k: [o[0] for o in c["options"]] for k, c in js[kind]["choices"].items()} == \
            {k: list(v) for k, v in K.choices.items()}


def test_a_crop_dropped_on_a_clip_is_a_black_band_in_the_program_and_the_export(board, proj):
    """Verdict 10's done: Crop dragged onto shot 1 (one undo, "undo: Crop"), Left typed 10
    (one undo) — the Program's left 10 % black within 2 px, and the export's (32 of 320
    px) too; the panel shows Premiere's rows; Ctrl+Z twice takes it all back."""
    pg = board
    open_bin(pg)
    drag(pg, "Crop", *block_xy(pg, "s1"))
    assert pg.evaluate("effects.vfx(tl.byId('s1'))") == [{"id": "crop", "kind": "crop"}]
    assert pg.locator("#undo").get_attribute("title").startswith("undo: Crop")
    # Premiere's fx badge on the clip, and on that clip only
    pg.wait_for_selector("#tl .blk[data-id=s1].has-vfx")
    assert pg.locator("#tl .blk.has-vfx").count() == 1
    # the drop selected the clip and opened its section in Effect Controls, Premiere's rows
    sec = pg.locator("#inspector .ec-vsec[data-kind=crop]")
    sec.wait_for()
    assert sec.locator(".ec-vname").inner_text() == "Crop"
    assert sec.locator(".ec-row .ec-name").all_inner_texts() == ["Left", "Top", "Right", "Bottom", "Edge Feather"]
    assert sec.locator(".ec-sw").count() == 5                  # a stopwatch each
    type_value(pg, "left@crop", 10)
    assert pg.locator("#undo").get_attribute("title").startswith("undo: crop left")
    settle_program(pg, 0.5)
    pic = program(pg)
    w = pic.shape[1]
    assert abs(band(pic) - 0.10 * w) <= 2, (band(pic), w)
    assert pic[pic.shape[0] // 2, int(0.2 * w)][0] > 150          # red past the band
    edl = saved(pg, proj, lambda e: (e["segments"][0].get("controls") or {}).get("vfx"))
    assert edl["segments"][0]["controls"] == {"vfx": [{"id": "crop", "kind": "crop", "params": {"left": 10.0}}]}
    out, _ = render(proj, {**edl, "segments": edl["segments"][:1]}, "board-crop")
    assert abs(band(frame(out, 12)) - 32) <= 2
    # one undo per change: the value, then the drop
    pg.evaluate("document.activeElement && document.activeElement.blur()")
    pg.keyboard.press("Control+z")
    assert pg.evaluate("effects.vfx(tl.byId('s1'))") == [{"id": "crop", "kind": "crop"}]
    pg.keyboard.press("Control+z")
    assert pg.evaluate("effects.vfx(tl.byId('s1'))") == []
    settle_program(pg, 0.5)
    assert band(program(pg)) == 0
    assert pg.locator("#inspector .ec-vsec").count() == 0


def test_a_gaussian_blur_double_clicked_blurs_the_program_and_the_export(board, proj):
    """Double-click with shot 2 selected (the bin's other way in, one undo); a drag of
    Blurriness is one undo and blurs the Program as it goes; the export is as soft."""
    pg = board
    pg.locator("#tl .blk[data-id=s2]").click()
    settle_program(pg, 2.5)
    sharp = program(pg)
    open_bin(pg)
    pg.locator("#fxbin .fxb-item[data-vfx=gaussian_blur]").dblclick()
    assert pg.evaluate("effects.vfx(tl.byId('s2'))") == [{"id": "gaussian_blur", "kind": "gaussian_blur"}]
    assert pg.locator("#undo").get_attribute("title").startswith("undo: Gaussian Blur")
    v = pg.locator("#inspector .ec-v[data-k='blurriness@gaussian_blur']")
    box = v.bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    pg.mouse.move(x, y)
    pg.mouse.down()
    for dx in range(20, 320, 20):                 # 0.1 a pixel: 30 px of Blurriness
        pg.mouse.move(x + dx, y)
    assert pg.evaluate("effects.value('blurriness@gaussian_blur')") == pytest.approx(30, abs=1)
    # live, mid-drag: the grade canvas's shader blurs it (wave 16, VFXPLAY), at σ = Blurriness
    pg.wait_for_function("grade.adjust && grade.adjust.vfx && grade.adjust.vfx[0].k === 'blur'")
    st = pg.evaluate("grade.adjust.vfx[0]")
    assert st["sx"] == st["sy"] == pytest.approx(30, abs=1), st
    pg.mouse.up()
    assert pg.locator("#undo").get_attribute("title").startswith("undo: gaussian blur blurriness")
    settle_program(pg, 2.5)
    soft = program(pg)
    assert detail(soft) < 0.6 * detail(sharp), (detail(soft), detail(sharp))
    edl = saved(pg, proj, lambda e: (e["segments"][1].get("controls") or {}).get("vfx"))
    p = edl["segments"][1]["controls"]["vfx"][0]["params"]["blurriness"]
    plain, _ = render(proj, {**edl, "segments": [{**edl["segments"][1], "controls": None}]}, "b-plain")
    out, _ = render(proj, {**edl, "segments": edl["segments"][1:2]}, "b-blur")
    assert p == pytest.approx(30, abs=1)
    assert detail(frame(out, 36)) < 0.6 * detail(frame(plain, 36))
    # Blur Dimensions: a select, one undo
    pg.locator("#inspector select[data-vfxsel=dimensions]").select_option("vertical")
    assert pg.locator("#undo").get_attribute("title").startswith("undo: gaussian blur blur dimensions")
    assert pg.evaluate("effects.vfx(tl.byId('s2'))[0].params.dimensions") == "vertical"


def test_a_transform_dropped_on_effect_controls_moves_the_picture_apart_from_motion(board, proj):
    """Dropped on Effect Controls it goes on the clip the panel shows; its Scale 50 and
    Position put the picture in the frame's right half in the Program and the export,
    with Motion's own Scale still 100 (Premiere has both)."""
    pg = board
    pg.locator("#tl .blk[data-id=s3]").click()
    open_bin(pg)
    ec = pg.locator("#inspector").bounding_box()
    drag(pg, "Transform", ec["x"] + ec["width"] / 2, ec["y"] + 60)
    assert pg.evaluate("effects.vfx(tl.byId('s3'))") == [{"id": "transform", "kind": "transform"}]
    sec = pg.locator("#inspector .ec-vsec[data-kind=transform]")
    assert sec.locator(".ec-row .ec-name").all_inner_texts() == ["Position", "Scale", "Rotation", "Opacity"]
    type_value(pg, "scale@transform", 50)
    type_value(pg, "posx@transform", 1440)
    assert pg.evaluate("effects.value('scale')") == 100            # Motion untouched
    settle_program(pg, 4.5)
    pic = program(pg)
    w = pic.shape[1]
    green = luma(pic[pic.shape[0] // 2])
    assert green[int(0.3 * w)] < 30 and green[int(0.75 * w)] > 60, green[::40]
    edl = saved(pg, proj, lambda e: (e["segments"][2].get("controls") or {}).get("vfx"))
    assert edl["segments"][2]["controls"]["vfx"][0]["params"] == {"position_x": 0.75, "scale": 50.0}
    out, _ = render(proj, {**edl, "segments": edl["segments"][2:3]}, "t-move")
    g = luma(frame(out, 12)[90])
    assert g[int(0.3 * W)] < 30 and g[int(0.75 * W)] > 60


def test_the_sections_buttons_and_stopwatch_are_one_undo_each(board, proj):
    """fx off / on, ▼ / ▲, reset and Clear on two effects, each one undo; a stopwatch on
    Left keys it under `left@crop` with Motion's keys, and the Program follows the keys."""
    pg = board
    pg.locator("#tl .blk[data-id=s1]").click()
    open_bin(pg)
    pg.locator("#fxbin .fxb-item[data-vfx=crop]").dblclick()
    pg.locator("#fxbin .fxb-item[data-vfx=gaussian_blur]").dblclick()
    order = lambda: [e["id"] for e in pg.evaluate("effects.vfx(tl.byId('s1'))")]  # noqa: E731
    assert order() == ["crop", "gaussian_blur"]
    secs = pg.locator("#inspector .ec-vsec")
    assert [t.strip() for t in secs.locator(".ec-vname").all_inner_texts()] == ["Crop", "Gaussian Blur"]
    undo = lambda: pg.locator("#undo").get_attribute("title")  # noqa: E731
    pg.locator("#inspector .ec-vsec[data-vfx=crop] [data-vfxact=down]").click()
    assert order() == ["gaussian_blur", "crop"] and undo().startswith("undo: effect order")
    pg.locator("#inspector .ec-vsec[data-vfx=crop] [data-vfxact=off]").click()
    assert pg.evaluate("effects.vfx(tl.byId('s1'))[1].off") is True and undo().startswith("undo: Crop off")
    assert pg.locator("#inspector .ec-vsec[data-vfx=crop]").get_attribute("class").split().count("off") == 1
    pg.locator("#inspector .ec-vsec[data-vfx=crop] [data-vfxact=off]").click()
    assert "off" not in pg.evaluate("effects.vfx(tl.byId('s1'))[1]")
    pg.locator("#inspector .ec-vsec[data-vfx=gaussian_blur] [data-vfxact=off]").click()
    pg.locator("#inspector .ec-vsec[data-vfx=crop] [data-vfxact=off]").click()
    pg.wait_for_function("!document.querySelector('#tl .blk[data-id=s1]').classList.contains('has-vfx')")
    pg.evaluate("document.activeElement && document.activeElement.blur()")
    pg.keyboard.press("Control+z")                 # the fx badge comes back with an effect on
    pg.wait_for_selector("#tl .blk[data-id=s1].has-vfx")
    pg.keyboard.press("Control+z")
    assert all("off" not in e for e in pg.evaluate("effects.vfx(tl.byId('s1'))"))
    # the stopwatch: two keys on Left, the monitor follows them
    pg.evaluate("tl.seek(0)")
    pg.locator("#inspector .ec-sw[data-sw='left@crop']").click()
    assert undo().startswith("undo: animate crop left")
    pg.evaluate("tl.seek(1.5)")
    type_value(pg, "left@crop", 40)
    assert pg.evaluate("effects.keys('left@crop')") == [[0, 0], [1.5, 40]]
    pg.evaluate("tl.seek(0.75)")
    assert pg.evaluate("effects.vfxLook().clip") == "polygon(20% 0%, 100% 0%, 100% 100%, 20% 100%)"
    edl = saved(pg, proj, lambda e: ((e["segments"][0].get("controls") or {}).get("keys") or {}).get("left@crop"))
    assert edl["segments"][0]["controls"]["keys"] == {"left@crop": [[0.0, 0.0], [1.5, 40.0]]}
    # reset: its values and keys back, one undo; Clear: off the clip, one undo
    pg.locator("#inspector .ec-vsec[data-vfx=crop] [data-vfxact=reset]").click()
    assert undo().startswith("undo: reset Crop") and pg.evaluate("effects.keys('left@crop')") == []
    pg.locator("#inspector .ec-vsec[data-vfx=gaussian_blur] [data-vfxact=clear]").click()
    assert order() == ["crop"] and undo().startswith("undo: clear Gaussian Blur")
    pg.evaluate("document.activeElement && document.activeElement.blur()")
    pg.keyboard.press("Control+z")
    assert order() == ["gaussian_blur", "crop"]
    pg.keyboard.press("Control+z")
    assert pg.evaluate("effects.keys('left@crop')") == [[0, 0], [1.5, 40]]


def test_v1_only_and_a_locked_track_refuses(board):
    """A V2 clip takes no video effect (it renders as shot), and a locked V1 refuses the
    drop as it refuses every edit."""
    pg = board
    assert pg.evaluate("effects.vfxAdd('crop', ['nope'])") == []
    pg.evaluate("tl.setTrack('V1', 'lock', true)")
    open_bin(pg)
    drag(pg, "Crop", *block_xy(pg, "s1"))
    assert pg.evaluate("effects.vfx(tl.byId('s1'))") == []
    pg.evaluate("tl.setTrack('V1', 'lock', false)")
    drag(pg, "Crop", *block_xy(pg, "s1"))
    assert pg.evaluate("effects.vfx(tl.byId('s1'))") == [{"id": "crop", "kind": "crop"}]
