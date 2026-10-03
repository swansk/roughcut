"""The flow bar in a real browser, on all three screens (INTAKE M14).

Karl, 2026-10-03 (#3): "make the flow through various stages make more sense in the
UI." The three screens had three navigation schemes; they now carry one bar, drawn by
/flow.js from GET /api/flow. These tests drive each screen and check that it is the
same bar — the same seven stages in the same states — that a click goes to the screen
where a stage is done (on the board, with the right dock tool open), and that the old
bars are gone rather than sitting beside it.
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

KEYS = ["footage", "index", "brief", "pass", "cut", "polish", "render"]


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


def bar(page) -> list[tuple[str, str]]:
    page.wait_for_selector("#flow [data-stage=render]", timeout=15000)
    return page.eval_on_selector_all(
        "#flow .fs", "els => els.map(e => [e.dataset.stage, e.dataset.state])")


def open_screen(browser, url: str):
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    pg.goto(url)
    return pg


def test_one_bar_on_all_three_screens_with_the_same_stages(browser, live):
    seen = {}
    here = {}
    for path in ("/", "/floor", "/open"):
        pg = open_screen(browser, live + path)
        seen[path] = [tuple(x) for x in bar(pg)]
        here[path] = pg.eval_on_selector_all(
            "#flow .fs.here", "els => els.map(e => e.dataset.stage)")
        # the old bars are gone, not hidden beside the new one
        assert pg.locator("#steps").count() == 0, path
        assert pg.locator(".step").count() == 0, path
        assert pg.locator("#screens").count() == 0, path
        assert pg.locator("#flowNext").count() == 1, path
        # one row: the bar never wraps onto a second line
        h = pg.eval_on_selector("#flow", "el => el.getBoundingClientRect().height")
        assert h <= 26, (path, h)
        pg.close()
    assert [k for k, _ in seen["/"]] == KEYS
    assert seen["/"] == seen["/floor"] == seen["/open"], seen
    assert here == {"/": ["cut", "polish", "render"], "/floor": ["pass"],
                    "/open": ["footage", "index", "brief"]}


def test_a_stage_on_another_screen_navigates_and_opens_the_dock_tool(browser, live):
    pg = open_screen(browser, live + "/open")
    bar(pg)
    pg.locator("#flow [data-stage=render]").click()
    pg.wait_for_url("**/#tool=out", timeout=10000)
    pg.wait_for_selector("#tl .blk", timeout=15000)
    pg.wait_for_function("window.dock && dock.current() === 'out'", timeout=5000)
    # on the board itself, a board stage opens its tool without leaving the page
    pg.evaluate("window.__stay = 1")
    pg.locator("#flow [data-stage=cut]").click()
    pg.wait_for_function("dock.current() === 'ask'", timeout=5000)
    assert pg.evaluate("window.__stay") == 1, "the page did not reload"
    # and the pass is a screen away
    pg.locator("#flow [data-stage=pass]").click()
    pg.wait_for_url("**/floor", timeout=10000)
    assert [k for k, _ in bar(pg)] == KEYS
    pg.close()


def test_next_on_the_board_presses_render_because_render_is_free(browser, live):
    """No render of this cut exists, so Next is *Render the cut* — free, on this
    screen, so the chip presses the board's own Render button rather than navigating."""
    import server

    pg = open_screen(browser, live + "/")
    pg.wait_for_selector("#tl .blk", timeout=15000)
    bar(pg)
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
