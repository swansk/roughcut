"""Next in a real browser, on all three screens (INTAKE M14, M16).

Karl, 2026-10-03 (#3): "make the flow through various stages make more sense in the
UI." M14 gave every screen one bar of seven stages and a Next chip, drawn by /flow.js
from GET /api/flow. M16 (Karl, 2026-10-04, "take things away") took the bar off every
screen (decision 2) and kept Next as the one "what now": the chip is the only thing in
`#flow`. These tests drive each screen and check exactly that.
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


@pytest.fixture(scope="module")
def live(project):
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


@pytest.fixture
def browser(live, project):
    seed = json.dumps({
        "variant": "T", "title": "test cut", "orient": "none", "story": "",
        "target_s": [5, 20],
        "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
                     {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}],
    }, indent=1)
    Path(project["edl"]).write_text(seed, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        yield b
        b.close()


def test_the_bar_script_is_served(client):
    r = client.get("/flow.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert "/api/flow" in r.text


def chip(page) -> str:
    page.wait_for_selector("#flowNext", timeout=15000)
    page.wait_for_function("window.flowBar && flowBar.state()", timeout=15000)
    return page.locator("#flowNext").inner_text().strip()


def open_screen(browser, url: str):
    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    pg.goto(url)
    return pg


def test_no_step_bar_only_the_next_chip_on_all_three_screens(browser, live):
    """M16 decision 2: the seven-step bar comes off every screen; Next stays. `#flow`
    holds the chip and nothing else, on one line, and it says the same thing on the
    board and the open screen (the pass says the round, while one is open)."""
    said = {}
    for path in ("/", "/floor", "/open"):
        pg = open_screen(browser, live + path)
        said[path] = chip(pg)
        assert pg.locator("#flow a").count() == 1, path
        assert pg.locator("#flow .fs, #flow [data-stage], #flow .sep").count() == 0, path
        # the older bars are still gone too
        for gone in ("#steps", ".step", "#screens"):
            assert pg.locator(gone).count() == 0, (path, gone)
        h = pg.eval_on_selector("#flow", "el => el.getBoundingClientRect().height")
        assert h <= 26, (path, h)
        pg.close()
    assert said["/"] == said["/open"], said
    assert said["/"].startswith("Next"), said


def test_next_from_another_screen_lands_on_the_board_with_its_tool_open(browser, live):
    """No render of this cut exists, so Next is the film's quick look: from /open the
    chip goes to the board with the film tool ('out') open."""
    pg = open_screen(browser, live + "/open")
    chip(pg)
    nxt = pg.evaluate("flowBar.state().next")
    if nxt["stage"] != "render":
        pytest.skip(f"another test left this bin's next at {nxt['stage']}")
    assert nxt["href"] == "/#tool=out"
    pg.locator("#flowNext").click()
    pg.wait_for_url("**/#tool=out", timeout=10000)
    pg.wait_for_selector("#tl .blk", timeout=15000)
    pg.wait_for_function("window.dock && dock.current() === 'out'", timeout=5000)
    pg.close()


def test_next_on_the_board_presses_render_because_render_is_free(browser, live):
    """No render of this cut exists, so Next is *Render the cut* — free, on this
    screen, so the chip presses the board's own Render button rather than navigating."""
    import server

    pg = open_screen(browser, live + "/")
    pg.wait_for_selector("#tl .blk", timeout=15000)
    chip(pg)
    nxt = pg.evaluate("flowBar.state().next")
    if nxt["stage"] != "render":
        pytest.skip(f"another test left this bin's next at {nxt['stage']}")
    assert nxt["click"] == "#render"
    pg.evaluate("window.__clicked = 0; document.querySelector('#render')"
                ".addEventListener('click', (e) => { window.__clicked++; e.stopImmediatePropagation(); }, true)")
    pg.locator("#flowNext").click()
    assert pg.evaluate("window.__clicked") == 1
    assert not [r for r in server.RENDERS.values() if r["state"] == "running"]
    pg.close()
