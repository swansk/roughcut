"""INTAKE M10 I10.4 — the monitor shows the grade, the inspector controls it, in a real
browser.

Under test: `app/static/grade.js` (the WebGL LUT canvas over the monitor's live video,
`window.grade`, the `G` key) and app.js's Colour block in the inspector — the witness
line, the per-shot look and strength, the nudges, the film row. Every assertion is made
against the EDL the server holds (`GET /api/project` after the save), `/api/colour`'s
resolution and `/api/lut/{id}`'s word on the baked LUT — never against the block's own
display.

Same fixture pattern as test_timeline_ui.py: the real uvicorn server on a real port, the
synthetic three-clip bin, the EDL re-seeded per test with no colour block. Skipped when
playwright is absent.

`/grade.js` is served by a route this file registers when the server has none — the
server file is not this lane's to edit; the route mirrors `/switcher.js` and belongs in
`app/server.py` at integration.
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


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _ensure_grade_route(server) -> None:
    """A stand-in for the one-line route server.py needs for the new static file."""
    if any(getattr(r, "path", "") == "/grade.js" for r in server.app.routes):
        return
    from fastapi import Response

    @server.app.get("/grade.js")
    def gradejs() -> Response:
        p = server.HERE / "static" / "grade.js"
        return Response(p.read_text(encoding="utf-8"),
                        media_type="application/javascript", headers=server.NO_STORE)


@pytest.fixture(scope="module")
def live_server(project):
    import uvicorn
    import server

    original = project["edl"].read_text(encoding="utf-8")
    server.configure(project["edl"], project["footage"], project["sidecars"],
                     project["work"], proxies=False, assets=project["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in project["stems"]])
    _ensure_grade_route(server)

    port = _free_port()
    config = uvicorn.Config(server.app, host="127.0.0.1", port=port,
                            log_level="error")
    srv = uvicorn.Server(config)
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


@pytest.fixture
def page(live_server, project):
    # Seeded with ids, as a cut on disk has them (minted and persisted at open): the
    # LUT is fetched by id before anything is saved, and a read never writes.
    seed = json.dumps({
        "variant": "T", "title": "test cut", "orient": "none", "story": "",
        "target_s": [5, 20],
        "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first", "id": "shot-a"},
                     {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second", "id": "shot-b"}],
    }, indent=1)
    Path(project["edl"]).write_text(seed, encoding="utf-8")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        pg = browser.new_page(viewport={"width": 1280, "height": 900})
        pg.goto(live_server)
        pg.wait_for_selector("#tl .blk")
        # the looks and the shots' resolution arrive before the first paint
        pg.wait_for_function("typeof C !== 'undefined' && C && C.looks && C.looks.length > 0",
                             timeout=8000)
        yield pg
        browser.close()


def ids(page) -> list[str]:
    return page.evaluate("segs.map(s => s.id)")


def wait_saved(page) -> None:
    page.wait_for_function(
        "document.querySelector('#saveState').textContent.startsWith('saved ')",
        timeout=8000)


def api(page, live_server, path: str) -> dict:
    return page.evaluate(
        "async (u) => (await fetch(u)).json()", f"{live_server}{path}")


def select_first(page) -> str:
    page.locator("#tl .blk").nth(0).click()
    page.wait_for_selector("#inspector .shot .colour")
    return ids(page)[0]


def colour_on_disk(page, live_server) -> dict:
    return api(page, live_server, "/api/project")["colour"]


# ------------------------------------------------------------------ the monitor

def test_the_monitor_has_the_grade_canvas_and_g_flips_it(page):
    """The canvas sits in the monitor's screen with the two videos; G toggles
    window.grade.enabled and the toast says which way it went."""
    assert page.locator("#player .screen canvas#gradeCanvas").count() == 1
    assert page.evaluate("typeof window.grade") == "object"
    assert page.evaluate("grade.enabled") is True
    page.keyboard.press("g")
    assert page.evaluate("grade.enabled") is False
    assert page.locator("#toast").inner_text().startswith("grade off")
    page.keyboard.press("g")
    assert page.evaluate("grade.enabled") is True
    assert page.locator("#toast").inner_text().startswith("grade on")
    # the keys line under the monitor says so
    assert "grade" in page.locator("#player .transport .hint").last.inner_text()


def test_the_monitor_fetches_the_shot_it_is_on(page, live_server):
    """Playing shot 1 points the grade at its id; the LUT it holds is the server's."""
    first = ids(page)[0]
    page.keyboard.press("Enter")             # play this shot only
    page.wait_for_function("grade.shot != null", timeout=8000)
    assert page.evaluate("grade.shot") == first
    if not page.evaluate("grade.ready"):
        pytest.skip("no WebGL context in this browser — the LUT is not fetched without one")
    page.wait_for_function("grade.lut != null", timeout=8000)
    lut = api(page, live_server, f"/api/lut/{first}?n=17")
    assert page.evaluate("grade.lut.identity") == lut["identity"]
    if not lut["identity"]:
        assert page.evaluate("grade.lut.n") == 17
        # a non-identity LUT is drawn: the canvas is on and sized to the proxy
        page.wait_for_function(
            "document.querySelector('#gradeCanvas').classList.contains('on')", timeout=8000)


# ------------------------------------------------------------------ the inspector

def test_selecting_a_shot_shows_the_colour_block(page):
    """The block carries a witness line and a look select listing the library."""
    select_first(page)
    witness = page.locator("#inspector .colour .witness").inner_text()
    assert witness, "no witness line"
    assert any(w in witness for w in ("white:", "no white reference", "not measured", "hand-set"))
    options = page.evaluate(
        "[...document.querySelectorAll('#inspector .clook option')].map(o => o.value)")
    assert options[0] == ""                       # the film's
    for name in ("crisp", "alpine", "filmic"):
        assert name in options, options
    assert page.locator("#inspector .clook option").first.inner_text().startswith("— film's")
    film_options = page.evaluate(
        "[...document.querySelectorAll('#inspector .cfilmlook option')].map(o => o.value)")
    assert film_options[:1] == [""] and "alpine" in film_options


def test_choosing_a_look_for_the_shot_saves_the_override(page, live_server):
    """`filmic` on shot 1 → colour.shots[id].look on disk, and the LUT bakes it."""
    sid = select_first(page)
    page.locator("#inspector .clook").select_option("filmic")
    wait_saved(page)
    saved = colour_on_disk(page, live_server)
    assert saved["shots"][sid]["look"] == "filmic", saved
    lut = api(page, live_server, f"/api/lut/{sid}?n=5")
    assert lut["look"] == "filmic" and lut["identity"] is False
    # the other shot is untouched: no override, the film's (none)
    other = ids(page)[1]
    assert other not in saved.get("shots", {})
    by_id = {s["id"]: s for s in api(page, live_server, "/api/colour")["shots"]}
    assert by_id[sid]["look"] == "filmic"
    assert by_id[other]["look"] is None
    # per-shot strength rides the same override
    page.locator("#inspector .cstrength").evaluate(
        "el => { el.value = '0.8'; el.dispatchEvent(new Event('change', { bubbles: true })); }")
    wait_saved(page)
    assert colour_on_disk(page, live_server)["shots"][sid]["strength"] == 0.8


def test_the_film_row_off_makes_every_shot_the_identity(page, live_server):
    """mode off, no film look → colour.mode == 'off' and every shot's LUT is the identity."""
    select_first(page)
    page.locator("#inspector .cmode").select_option("off")
    wait_saved(page)
    saved = colour_on_disk(page, live_server)
    assert saved["mode"] == "off" and saved["look"] is None
    shots = api(page, live_server, "/api/colour")["shots"]
    assert shots and all(s["identity"] is True for s in shots), shots
    # the witness now says as shot; the auto checkbox is moot
    assert "as shot" in page.locator("#inspector .colour .witness").inner_text()
    assert page.locator("#inspector .cauto").is_disabled()
    # and the film look select puts a look over the whole film
    page.locator("#inspector .cfilmlook").select_option("alpine")
    wait_saved(page)
    saved = colour_on_disk(page, live_server)
    assert saved["look"] == "alpine"
    shots = api(page, live_server, "/api/colour")["shots"]
    assert all(s["look"] == "alpine" and s["identity"] is False for s in shots)


def test_warmer_writes_a_balance_override_and_reset_drops_it(page, live_server):
    """warmer → colour.shots[id].balance with gain[0] > gain[2]; reset removes it."""
    sid = select_first(page)
    page.locator('#inspector [data-act="cwarm"]').click()
    wait_saved(page)
    saved = colour_on_disk(page, live_server)
    bal = saved["shots"][sid]["balance"]
    assert bal["gain"][0] > bal["gain"][2], bal
    assert bal["source"] == "hand"
    by_id = {s["id"]: s for s in api(page, live_server, "/api/colour")["shots"]}
    assert by_id[sid]["balance"]["source"] == "hand"
    assert page.locator("#inspector .colour .witness").inner_text().startswith("hand-set")
    # brighter steps the exposure on the same override
    exposure = bal["exposure"]
    page.locator('#inspector [data-act="cbright"]').click()
    wait_saved(page)
    assert colour_on_disk(page, live_server)["shots"][sid]["balance"]["exposure"] \
        == pytest.approx(exposure + 0.05, abs=1e-3)
    page.locator('#inspector [data-act="creset"]').click()
    wait_saved(page)
    saved = colour_on_disk(page, live_server)
    assert "balance" not in saved.get("shots", {}).get(sid, {}), saved


def test_match_and_reference_are_saved_by_id(page, live_server):
    """set as reference → colour.reference; match ← previous on shot 2 → shots[id].match."""
    first = select_first(page)
    page.locator('#inspector [data-act="csetref"]').click()
    wait_saved(page)
    assert colour_on_disk(page, live_server)["reference"] == first
    page.locator("#tl .blk").nth(1).click()
    second = ids(page)[1]
    page.wait_for_function(
        "document.querySelector('#inspector .n').textContent.startsWith('SHOT 2')")
    page.locator('#inspector [data-act="cmatchprev"]').click()
    wait_saved(page)
    saved = colour_on_disk(page, live_server)
    assert saved["shots"][second]["match"] == "previous"
    assert saved["reference"] == first
    page.locator('#inspector [data-act="cmatchprev"]').click()      # again: off
    wait_saved(page)
    assert "match" not in colour_on_disk(page, live_server).get("shots", {}).get(second, {})


def test_a_trim_keeps_the_colour_block(page, live_server):
    """The colour block rides the one save path: a trim after a look is set does not
    lose it, and nothing on the timeline changed shape."""
    sid = select_first(page)
    page.locator("#inspector .clook").select_option("crisp")
    wait_saved(page)
    page.keyboard.press("]")                  # in-point 0.25 s later, through app.js
    wait_saved(page)
    saved = colour_on_disk(page, live_server)
    assert saved["shots"][sid]["look"] == "crisp"
    segs = api(page, live_server, "/api/project")["segments"]
    assert segs[0]["in"] == pytest.approx(1.25)
    assert page.locator("#tl .blk").count() == 2
