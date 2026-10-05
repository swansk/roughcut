"""Next in a real browser, on all three screens (INTAKE M14, M16).

Karl, 2026-10-03 (#3): "make the flow through various stages make more sense in the
UI." M14 gave every screen one bar of seven stages and a Next chip, drawn by /flow.js
from GET /api/flow. M16 (Karl, 2026-10-04, "take things away") took the bar off every
screen (decision 2) and kept Next as the one "what now": the chip is the only thing in
`#flow`, the screen's one blue button is Next's target or the chip (decision 3), and a
round of the pass reads "N left". These tests drive each screen and check exactly that.
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


def blue(page) -> list[str]:
    return page.eval_on_selector_all(
        ".is-next", "els => els.map(e => e.id || e.dataset.mark || e.tagName)")


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


MARKED_JS = """([stage, fx, id]) => {
  const b = document.createElement('button');
  b.type = 'button'; b.id = id; b.dataset.mark = id; b.dataset.nextFor = stage;
  if (fx) b.dataset.fx = fx;
  b.textContent = id;
  b.addEventListener('click', (e) => { window.__pressed = (window.__pressed || []).concat(id); });
  document.body.prepend(b);
}"""


def test_next_on_the_board_presses_the_quick_look_because_it_is_free(browser, live):
    """Free, on this screen: the chip opens the film tool and presses the button the
    board marks for the render stage (`data-next-for="render"`, the quick look) — never
    one it did not mark, and nothing is rendered by this test."""
    import server

    pg = open_screen(browser, live + "/")
    pg.wait_for_selector("#tl .blk", timeout=15000)
    chip(pg)
    nxt = pg.evaluate("flowBar.state().next")
    if nxt["stage"] != "render":
        pytest.skip(f"another test left this bin's next at {nxt['stage']}")
    assert nxt["click"] == '[data-next-for~="render"]' and nxt["tool"] == "out"
    # Every marked button stops here; the board's own (once the film tool carries one)
    # counts as pressed like the test's.
    pg.evaluate("""() => document.addEventListener('click', (e) => {
        const b = e.target.closest('[data-next-for~="render"]');
        if (!b) return;
        window.__pressed = (window.__pressed || []).concat(b.id || 'board');
        e.stopImmediatePropagation(); e.preventDefault(); }, true)""")
    if not pg.evaluate("!!document.querySelector('[data-next-for~=render]')"):
        pg.evaluate(MARKED_JS, ["render", None, "quickLook"])
    pg.locator("#flowNext").click()
    pg.wait_for_function("(window.__pressed || []).length === 1", timeout=5000)
    pg.wait_for_function("dock.current() === 'out'", timeout=5000)
    assert not [r for r in server.RENDERS.values() if r["state"] == "running"]
    pg.close()


def test_one_blue_button_on_nexts_target_else_on_the_chip(browser, live):
    """M16 decision 3. `.is-next` is the only primary style, and /flow.js puts it on
    exactly one element: the first visible one a screen marked for Next's stage, else
    the chip — and a button that was blue before loses it."""
    pg = open_screen(browser, live + "/")
    pg.wait_for_selector("#tl .blk", timeout=15000)
    chip(pg)
    stage = pg.evaluate("flowBar.state().next.stage")
    pg.wait_for_function("document.querySelectorAll('.is-next').length === 1", timeout=5000)
    assert blue(pg) == ["flowNext"] or len(blue(pg)) == 1
    # a stale blue left by anyone is taken off
    pg.evaluate("document.querySelector('#hdBin').classList.add('is-next')")
    # a marked button for another stage stays plain; Next's own takes the blue
    pg.evaluate(MARKED_JS, ["index" if stage != "index" else "pass", None, "other"])
    pg.evaluate(MARKED_JS, [stage, None, "mine"])
    pg.evaluate("flowBar.mark()")
    assert blue(pg) == ["mine"]
    assert pg.evaluate("getComputedStyle(document.querySelector('#mine')).backgroundColor") \
        == "rgb(110, 168, 254)"
    assert pg.evaluate("getComputedStyle(document.querySelector('#other')).backgroundColor") \
        != "rgb(110, 168, 254)"
    # hidden, it is not on this screen: the chip has it again (the observer, unprompted)
    pg.evaluate("document.querySelector('#mine').hidden = true")
    pg.wait_for_function("document.querySelector('#flowNext').classList.contains('is-next')",
                         timeout=3000)
    assert blue(pg) == ["flowNext"]
    pg.close()


def test_when_next_names_an_effect_only_its_card_is_blue(browser, live):
    """Next carries `target.fx` for a waiting effect (I16.0a); of the cards marked for
    polish, only the one whose `data-fx` is that effect takes the blue."""
    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    fake = {"stages": [], "blockers": [], "running": False,
            "next": {"stage": "polish", "sentence": "1 effect proposed — accept or discard",
                     "verb": "Answer", "screen": "/", "tool": "fx", "kind": "go",
                     "href": "/#tool=fx&shot=s2&fx=fx_b", "target": {"shot": "s2", "fx": "fx_b"}}}
    pg.route("**/api/flow", lambda r: r.fulfill(status=200, content_type="application/json",
                                                 body=json.dumps(fake)))
    pg.goto(live + "/open")
    chip(pg)
    pg.evaluate(MARKED_JS, ["polish", "fx_a", "cardA"])
    pg.evaluate(MARKED_JS, ["polish", "fx_b", "cardB"])
    pg.evaluate("flowBar.mark()")
    assert blue(pg) == ["cardB"]
    # without its card on screen, the chip
    pg.evaluate("document.querySelector('#cardB').remove()")
    pg.evaluate("flowBar.mark()")
    assert blue(pg) == ["flowNext"]
    pg.close()


def test_mid_round_the_pass_says_how_many_are_left_and_nothing_is_blue(browser, live):
    """M16 C3: while a round of the pass has undecided moments, the chip on /floor is
    the round ("N left"), plain — the choice is P / X / U — and a click on it does not
    take Karl off the pass."""
    pg = open_screen(browser, live + "/floor")
    pg.wait_for_function("typeof F === 'object' && F.queue.length > 0 && F.mode === 'pass'",
                         timeout=15000)
    n = pg.evaluate("F.queue.filter((p) => !p.verdict).length")
    assert n > 0
    pg.wait_for_function(
        "(n) => (document.querySelector('#flowNext') || {}).innerText === n + ' left'",
        arg=n, timeout=5000)
    assert blue(pg) == []
    pg.evaluate("window.__stay = 1")
    pg.locator("#flowNext").click()
    pg.wait_for_timeout(300)
    assert pg.evaluate("window.__stay") == 1 and pg.url.endswith("/floor")
    # one decided (in the page's state only — nothing is written): the count follows
    pg.evaluate("F.queue.find((p) => !p.verdict).verdict = 'later'")
    if n > 1:
        pg.wait_for_function(
            "(n) => document.querySelector('#flowNext').innerText === n + ' left'",
            arg=n - 1, timeout=3000)
    # the round over (the closing card): Next is Next again, and one thing is blue
    pg.evaluate("F.queue.forEach((p) => { p.verdict = p.verdict || 'later'; }); F.mode = 'card'")
    pg.wait_for_function("document.querySelector('#flowNext').innerText.startsWith('Next')",
                         timeout=3000)
    pg.wait_for_function("document.querySelectorAll('.is-next').length === 1", timeout=3000)
    pg.close()
