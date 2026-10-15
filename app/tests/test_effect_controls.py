"""Redesign wave 2, lane EFFECTS — Effect Controls as Premiere has it (REDESIGN-GOAL
dimension 7, verdict 2 punch 4).

Under test, in a real browser on the synthetic three-clip bin: `/modules/effects.js`
(the sections Motion / Opacity / Time Remapping / Volume / Lumetri Color / the designed
effects' stack, each value a scrubbable number with a reset), its live preview on the
Program monitor (a CSS transform / opacity on the picture's layers, the live video's
volume, and the colour through `/grade.js`'s shader — `grade.probe()` reads the drawn
pixels), one undo entry per change, and persistence: the segment's `controls` (validated
by server.py's `clean_controls`) and the colour block's hand balance, both surviving a
reload.

Every latency is measured inside the page: the time from the keydown that commits a
value (or the click on a nudge) to the first animation frame whose picture shows it.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api",
                                     reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

LIMIT_MS = 150

HIT = {
    "name": "hit markers", "why": "", "note": "",
    "events": [{"t": 1.5, "x": 0.5, "y": 0.7}],
    "overlay": {"duration": 0.35, "size": 0.12,
                "shapes": [{"type": "line", "from": [-1, -1], "to": [-0.3, -0.3], "width": 0.1}],
                "anim": {"opacity": [[0, 1], [0.35, 0]]}},
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(project):
    import uvicorn
    import server

    original = project["edl"].read_text(encoding="utf-8")
    server.configure(project["edl"], project["footage"], project["sidecars"],
                     project["work"], proxies=False, assets=project["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in project["stems"]])
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_level="error"))
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
    project["edl"].write_text(original, encoding="utf-8")


def _seed(project, effects=None) -> None:
    body = {
        "variant": "T", "title": "test cut", "orient": "none", "story": "",
        "target_s": [5, 20],
        "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first", "id": "shot-a"},
                     {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second", "id": "shot-b"}],
    }
    if effects:
        body["effects"] = effects
    Path(project["edl"]).write_text(json.dumps(body, indent=1), encoding="utf-8")


def _open(pw, live_server):
    browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(live_server)
    pg.wait_for_selector("#tl .blk")
    pg.wait_for_function("typeof C !== 'undefined' && C && C.shots && window.effects", timeout=8000)
    pg._errors = errors
    return browser, pg


@pytest.fixture
def page(live_server, project):
    _seed(project)
    with sync_playwright() as pw:
        browser, pg = _open(pw, live_server)
        yield pg
        assert pg._errors == [], pg._errors
        browser.close()


def select_first(page) -> str:
    """Select shot 1 and park the Program monitor on it, its frame drawn."""
    page.locator("#tl .blk").nth(0).click()
    page.wait_for_selector("#inspector .ec [data-sec=motion]")
    page.evaluate("cueAt(0, segs[0].in + 0.5)")
    page.wait_for_function(
        "liveVideo() && liveVideo().readyState >= 2 && grade.shot === segs[0].id", timeout=10000)
    return page.evaluate("segs[0].id")


def wait_saved(page) -> None:
    page.wait_for_function(
        "document.querySelector('#saveState').dataset.state === 'saved'", timeout=8000)


def api(page, path: str):
    return page.evaluate("async (u) => (await fetch(u)).json()", path)


def arm(page, cond: str) -> None:
    """Start the clock at the next keydown / click (capture phase, before any handler)
    and stop it at the first animation frame where `cond` holds."""
    page.evaluate("""(cond) => {
      const P = window.__probe = { t0: null, t1: null, base: null };
      const test = new Function('P', 'return (' + cond + ')');
      P.base = (window.grade && grade.probe) ? grade.probe() : null;
      const start = () => { if (P.t0 == null) { P.t0 = performance.now(); requestAnimationFrame(loop); } };
      const loop = () => {
        if (test(P)) { P.t1 = performance.now(); return; }
        if (performance.now() - P.t0 < 3000) requestAnimationFrame(loop);
      };
      document.addEventListener('keydown', (e) => { if (e.key === 'Enter') start(); }, { capture: true, once: true });
      document.addEventListener('click', start, { capture: true, once: true });
    }""", cond)


def elapsed(page) -> float:
    page.wait_for_function("window.__probe.t1 != null", timeout=4000)
    return page.evaluate("window.__probe.t1 - window.__probe.t0")


def type_value(page, key: str, value: float) -> None:
    """Click the value (no drag: it opens for typing), type, and Enter — as a person does."""
    page.locator(f"#inspector .ec-v[data-k={key}]").click()
    inp = page.locator(f"#inspector .ec-v.editing[data-k={key}]")
    inp.fill(str(value))
    inp.press("Enter")


def picture_changed() -> str:
    return ("(() => { const p = grade.probe(); return !!p && !!P.base"
            " && Math.abs(p[0]-P.base[0]) + Math.abs(p[1]-P.base[1]) + Math.abs(p[2]-P.base[2]) > 1.5; })()")


def needs_webgl(page) -> None:
    if not page.evaluate("grade.ready"):
        pytest.skip("no WebGL context in this browser")


# ------------------------------------------------------------------ the panel

def test_the_sections_are_premieres_and_the_head_is_timecode(page):
    select_first(page)
    names = page.locator("#inspector .ec-sec .ec-twirl span").all_inner_texts()
    assert names == ["Motion", "Opacity", "Time Remapping", "Volume", "Lumetri Color",
                     "Designed effects"]
    rows = page.locator("#inspector .ec-row .ec-name").all_inner_texts()
    for want in ("Position", "Scale", "Rotation", "Opacity", "Speed", "Level",
                 "Temperature", "Tint", "Exposure", "Contrast", "Saturation"):
        assert want in rows, (want, rows)
    head = page.locator("#ecWhat").inner_text()
    assert head == "CLIP_A · 00:00:01:00 – 00:00:03:00", head
    # the values Premiere shows by default
    assert page.locator("#inspector .ec-v[data-k=posx]").input_value() == "960.0"
    assert page.locator("#inspector .ec-v[data-k=posy]").input_value() == "540.0"
    assert page.locator("#inspector .ec-v[data-k=scale]").input_value() == "100.0"
    assert page.locator("#inspector .ec-v[data-k=opacity]").input_value() == "100.0 %"
    assert page.locator("#inspector .ec-v[data-k=volume_db]").input_value() == "0.00 dB"
    # a row that the export does not draw yet says so on hover
    assert "export does not include it yet" in page.locator(
        "#inspector .ec-row[data-row=opacity]").get_attribute("title")
    # a twirl folds its section, and stays folded on the next shot
    page.locator("#inspector [data-twirl=motion]").click()
    assert page.locator("#inspector .ec-row[data-row=scale]").is_hidden()
    page.locator("#tl .blk").nth(1).click()
    page.wait_for_function("document.querySelector('#ecWhat').textContent.startsWith('CLIP_B')")
    assert page.locator("#inspector .ec-row[data-row=scale]").is_hidden()


# ------------------------------------------------------------------ live, within 150 ms

@pytest.mark.parametrize("key,value,cond", [
    ("posx", 1200, "getComputedStyle(liveVideo()).transform !== 'none'"),
    ("posy", 300, "getComputedStyle(liveVideo()).transform !== 'none'"),
    ("scale", 150, "getComputedStyle(liveVideo()).transform.startsWith('matrix(1.5')"),
    ("rotation", 90, "getComputedStyle(liveVideo()).transform.startsWith('matrix(0')"),
    ("opacity", 40, "getComputedStyle(liveVideo()).opacity === '0.4'"
                    " && getComputedStyle(document.querySelector('#gradeCanvas')).opacity === '0.4'"),
    ("volume_db", -6, "Math.abs(liveVideo().volume - Math.pow(10, -6 / 20)) < 1e-3"),
])
def test_motion_opacity_volume_show_on_the_program_within_150_ms(page, key, value, cond):
    select_first(page)
    page.locator(f"#inspector .ec-v[data-k={key}]").click()
    inp = page.locator(f"#inspector .ec-v.editing[data-k={key}]")
    inp.fill(str(value))
    arm(page, cond)
    inp.press("Enter")
    ms = elapsed(page)
    assert ms < LIMIT_MS, f"{key}: {ms:.0f} ms"
    assert page.evaluate(f"effects.value('{key}')") == pytest.approx(value, abs=0.01)


@pytest.mark.parametrize("key,value", [
    ("temperature", 60), ("tint", -60), ("exposure", 0.5), ("contrast", 80), ("saturation", 10),
])
def test_lumetri_changes_the_program_pixels_within_150_ms(page, key, value):
    select_first(page)
    needs_webgl(page)
    page.locator(f"#inspector .ec-v[data-k={key}]").click()
    inp = page.locator(f"#inspector .ec-v.editing[data-k={key}]")
    inp.fill(str(value))
    arm(page, picture_changed())
    inp.press("Enter")
    ms = elapsed(page)
    assert ms < LIMIT_MS, f"{key}: {ms:.0f} ms"


def test_warmer_shows_within_150_ms_not_after_the_save(page):
    """Verdict 2: "warmer" took 1012 ms (a save, a refetch and a LUT bake). The shader
    now previews the balance the LUT was not baked with, at once."""
    select_first(page)
    needs_webgl(page)
    page.wait_for_function("grade.lut != null", timeout=8000)
    arm(page, "(() => { const p = grade.probe(); return !!p && !!P.base && P.base[2] - p[2] > 1.0; })()")   # less blue
    page.locator('#inspector [data-act="cwarm"]').click()
    ms = elapsed(page)
    assert ms < LIMIT_MS, f"warmer: {ms:.0f} ms"
    # and the picture does not jump back when the save's LUT arrives
    after = page.evaluate("grade.probe()")
    wait_saved(page)
    page.wait_for_function("grade.lut && grade.lut.balance && grade.lut.balance.source === 'hand'",
                           timeout=8000)
    settled = page.evaluate("grade.probe()")
    assert all(abs(a - b) < 1.5 for a, b in zip(after, settled)), (after, settled)


def test_a_drag_scrubs_the_value_live_and_is_one_undo(page):
    select_first(page)
    v = page.locator("#inspector .ec-v[data-k=scale]")
    box = v.bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    for dx in range(10, 110, 10):
        page.mouse.move(x + dx, y)
    # live during the drag, before the mouse is let go
    assert page.evaluate("effects.value('scale')") == pytest.approx(110.0, abs=0.5)
    assert page.evaluate("getComputedStyle(liveVideo()).transform").startswith("matrix(1.1")
    page.mouse.up()
    assert page.locator("#undo").get_attribute("title").startswith("undo: scale")
    page.keyboard.press("Control+z")
    assert page.evaluate("effects.value('scale')") == 100
    assert page.evaluate("getComputedStyle(liveVideo()).transform") == "none"


# ------------------------------------------------------------------ undo

def test_each_change_is_one_undo_entry(page):
    select_first(page)
    type_value(page, "opacity", 50)
    type_value(page, "scale", 120)
    type_value(page, "temperature", 40)
    assert page.locator("#undo").get_attribute("title").startswith("undo: temperature")
    page.keyboard.press("Control+z")
    assert page.evaluate("effects.value('temperature')") == pytest.approx(0, abs=0.6)
    assert page.evaluate("effects.value('scale')") == 120
    page.keyboard.press("Control+z")
    assert page.evaluate("effects.value('scale')") == 100
    assert page.evaluate("effects.value('opacity')") == 50
    page.keyboard.press("Control+z")
    assert page.evaluate("effects.value('opacity')") == 100
    assert page.evaluate("segs[0].controls") is None
    # the reset arrow is one entry too
    type_value(page, "rotation", 30)
    page.locator("#inspector .ec-row[data-row=rotation] .ec-reset").click()
    assert page.evaluate("effects.value('rotation')") == 0
    page.keyboard.press("Control+z")
    assert page.evaluate("effects.value('rotation')") == 30


def test_arrow_keys_step_a_value(page):
    select_first(page)
    page.locator("#inspector .ec-v[data-k=opacity]").focus()
    page.keyboard.press("ArrowDown")
    assert page.evaluate("effects.value('opacity')") == 99
    page.keyboard.press("Shift+ArrowDown")
    assert page.evaluate("effects.value('opacity')") == 89


# ------------------------------------------------------------------ persistence

def test_values_survive_a_reload(live_server, project):
    _seed(project)
    with sync_playwright() as pw:
        browser, pg = _open(pw, live_server)
        sid = select_first(pg)
        for key, value in (("posx", 1100), ("posy", 500), ("scale", 80), ("rotation", -15),
                           ("opacity", 70), ("volume_db", -3), ("contrast", 25),
                           ("saturation", 140), ("temperature", 30), ("exposure", 0.25)):
            type_value(pg, key, value)
        wait_saved(pg)
        saved = api(pg, "/api/project")
        c = saved["segments"][0]["controls"]
        assert c == {"position": [pytest.approx(1100 / 1920, abs=1e-4), pytest.approx(500 / 1080, abs=1e-4)],
                     "scale": 80, "rotation": -15, "opacity": 70, "volume_db": -3,
                     "contrast": 25, "saturation": 140}, c
        assert "controls" not in saved["segments"][1]
        bal = saved["colour"]["shots"][sid]["balance"]
        assert bal["gain"][0] > bal["gain"][2] and bal["exposure"] == pytest.approx(2 ** 0.25, abs=1e-3)
        before = {k: pg.evaluate(f"effects.value('{k}')") for k in
                  ("posx", "posy", "scale", "rotation", "opacity", "volume_db", "contrast",
                   "saturation", "temperature", "exposure")}
        pg.reload()
        pg.wait_for_selector("#tl .blk")
        pg.wait_for_function("typeof C !== 'undefined' && C && C.shots && window.effects", timeout=8000)
        select_first(pg)
        after = {k: pg.evaluate(f"effects.value('{k}')") for k in before}
        for k in before:
            assert after[k] == pytest.approx(before[k], abs=0.05), (k, before[k], after[k])
        # and the monitor shows them again without a touch
        assert pg.evaluate("getComputedStyle(liveVideo()).opacity") == "0.7"
        assert pg._errors == []
        browser.close()


def test_the_server_rejects_controls_it_does_not_know(live_server, project):
    import httpx
    _seed(project)
    seg = {"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "id": "shot-a"}
    for bad in ({"opacity": 500}, {"glow": 1}, {"position": [0.5]}, {"fx_off": [3]}):
        r = httpx.put(f"{live_server}/api/project", json={"segments": [{**seg, "controls": bad}]})
        assert r.status_code == 400, (bad, r.text)
    r = httpx.put(f"{live_server}/api/project",
                  json={"segments": [{**seg, "controls": {"opacity": 100, "scale": 50}}]})
    assert r.status_code == 200
    # the default is the absence of the key, as speed 1× is
    assert httpx.get(f"{live_server}/api/project").json()["segments"][0]["controls"] == {"scale": 50}


# ------------------------------------------------------------------ the fx stack

def test_the_fx_stack_bypasses_and_orders_designed_effects(live_server, project):
    effects = [{**HIT, "id": "fx-one", "name": "hit one", "shot": "shot-a", "clip": "CLIP_A.MP4",
                "status": "accepted"},
               {**HIT, "id": "fx-two", "name": "hit two", "shot": "shot-a", "clip": "CLIP_A.MP4",
                "status": "accepted"}]
    _seed(project, effects)
    with sync_playwright() as pw:
        browser, pg = _open(pw, live_server)
        select_first(pg)
        pg.wait_for_function("document.querySelectorAll('#inspector .ec-fxrow').length === 2",
                             timeout=8000)
        assert pg.locator("#inspector .ec-fxrow .ec-name").all_inner_texts() == ["hit one", "hit two"]
        pg.locator("#inspector .ec-fxrow[data-fx=fx-one] [data-fxact=bypass]").click()
        assert pg.evaluate("segs[0].controls.fx_off") == ["fx-one"]
        assert pg.locator("#inspector .ec-fxrow[data-fx=fx-one]").get_attribute("class").endswith("off")
        pg.locator("#inspector .ec-fxrow[data-fx=fx-one] [data-fxact=down]").click()
        assert pg.locator("#inspector .ec-fxrow .ec-name").all_inner_texts() == ["hit two", "hit one"]
        assert pg.locator("#undo").get_attribute("title").startswith("undo: fx order")
        wait_saved(pg)
        c = api(pg, "/api/project")["segments"][0]["controls"]
        assert c == {"fx_off": ["fx-one"], "fx_order": ["fx-two", "fx-one"]}, c
        pg.keyboard.press("Control+z")
        assert pg.locator("#inspector .ec-fxrow .ec-name").all_inner_texts() == ["hit one", "hit two"]
        pg.keyboard.press("Control+z")
        assert pg.evaluate("segs[0].controls") is None
        assert pg._errors == []
        browser.close()
