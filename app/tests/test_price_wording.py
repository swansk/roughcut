"""No visible text says money was spent on the subscription backend (REDESIGN-GOAL S).

The standing decision: on `claude_cli` (Karl's Claude plan, every price response
`charged: false`) no price reads as spending money — the dollars are an API-equivalent
estimate, said as one ("~$0.27 API-equivalent · free on your plan") with the estimate's
meaning in the hover. Verdict 1 found "Indexing · 0 of 12 ready · about 0:10 left ·
spent $0.27" on /open (open.js `spentLine` in the progress line). On `anthropic_api`
(`charged: true`) the money is real and "spent" is the honest word.

This serves the suite's synthetic bin from its own work dir (a real journal on disk, so
/open's at-rest "how it was indexed" line shows), pins this project's spend at $0.27,
fakes a running index by rewriting GET /api/index in the page (the existing UI tests mock
/api/flow the same way), and reads `document.body.innerText` — only what is drawn — on
/, /open (at rest and mid-index) and /floor.

    uv run --with pytest --with fastapi --with uvicorn --with httpx --with numpy \
        --with playwright pytest app/tests/test_price_wording.py -q
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import threading
import time

import pytest

playwright_api = pytest.importorskip("playwright.sync_api",
                                     reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

SPENT = re.compile(r"\bspent\b", re.I)
USD = 0.27


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live(project, tmp_path_factory):
    """A live server on the synthetic bin with its own work dir: the cut copied in (so
    the board draws), a journal reconciled from the sidecars on disk (so /open is past
    "not indexed yet"), and this project's spend pinned at $0.27."""
    import uvicorn
    import server

    work = tmp_path_factory.mktemp("price")
    edl = work / "cut.edl.json"
    shutil.copy(project["edl"], edl)
    server.configure(edl, project["footage"], project["sidecars"], work, proxies=False)
    j = server.load_journal()
    j.reconcile(server.index_files())
    j.save()
    mp = pytest.MonkeyPatch()
    mp.setattr(server, "project_spent", lambda: USD)
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
    mp.undo()


@pytest.fixture
def browser(live):
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def api_backend(monkeypatch):
    """The billing backend: `charged: true`, and a key so the banner stays quiet."""
    monkeypatch.setenv("ROUGHCUT_BACKEND", "anthropic_api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")


def visible(page) -> str:
    return page.evaluate("document.body.innerText")


def running_index(page) -> None:
    """GET /api/index says a run is going, with the journal's real progress: the page
    then shows its one progress line, money and all."""
    def handle(route):
        resp = route.fetch()
        data = resp.json()
        data["running"] = True
        data["job"] = "fake"
        if data.get("progress"):
            data["progress"]["eta_s"] = 10
        route.fulfill(response=resp, body=json.dumps(data),
                      headers={**resp.headers, "content-type": "application/json"})
    page.route("**/api/index", handle)


def open_page(browser, url: str):
    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg._price_errors = errors                       # read back by the tests
    return pg


def at_open(browser, live, *, running: bool):
    pg = open_page(browser, live)
    if running:
        running_index(pg)
    pg.goto(f"{live}/open")
    pg.wait_for_function(
        "window.sheet && sheet.state.clips && sheet.state.status && sheet.state.index",
        timeout=20000)
    if running:
        pg.wait_for_selector("#progress:not([hidden])", timeout=10000)
        pg.wait_for_function("/\\$0\\.27/.test(document.querySelector('#progLine').textContent)",
                             timeout=10000)
    else:
        pg.wait_for_selector("#howLine:not([hidden])", timeout=10000)
        pg.wait_for_function("/\\$0\\.27/.test(document.querySelector('#howLine').textContent)",
                             timeout=10000)
    return pg


def at_board(browser, live):
    pg = open_page(browser, live)
    pg.goto(f"{live}/")
    pg.wait_for_selector("#tl .blk", timeout=20000)
    pg.wait_for_function("window.flowBar && flowBar.state()", timeout=20000)
    return pg


def at_floor(browser, live):
    pg = open_page(browser, live)
    pg.goto(f"{live}/floor")
    pg.wait_for_function("window.floor && floor.state", timeout=20000)
    pg.wait_for_function("window.flowBar && flowBar.state()", timeout=20000)
    return pg


# ------------------------------------------------------------- the subscription


def test_the_index_progress_line_never_says_spent_on_the_plan(browser, live):
    pg = at_open(browser, live, running=True)
    line = pg.locator("#progLine").inner_text()
    assert not SPENT.search(line), line
    assert "~$0.27 API-equivalent · free on your plan" in line, line
    # the estimate's meaning is in the hover
    title = pg.locator("#progLine").get_attribute("title") or ""
    assert "free on your Claude plan" in title and "$0.27" in title, title
    text = visible(pg)
    assert not SPENT.search(text), [ln for ln in text.splitlines() if SPENT.search(ln)]
    assert not pg._price_errors, pg._price_errors


def test_the_how_line_at_rest_never_says_spent_on_the_plan(browser, live):
    pg = at_open(browser, live, running=False)
    line = pg.locator("#howLine").inner_text()
    assert not SPENT.search(line), line
    assert "~$0.27 API-equivalent · free on your plan" in line, line
    assert pg.locator("#budgetLine").inner_text() == "$0.27"      # the number itself is kept
    assert "free on your Claude plan" in (pg.locator("#howLine").get_attribute("title") or "")
    text = visible(pg)
    assert not SPENT.search(text), [ln for ln in text.splitlines() if SPENT.search(ln)]
    assert not pg._price_errors, pg._price_errors


@pytest.mark.parametrize("where", ["board", "floor"])
def test_the_board_and_the_pass_never_say_spent_on_the_plan(browser, live, where):
    pg = at_board(browser, live) if where == "board" else at_floor(browser, live)
    st = pg.evaluate("fetch('/api/status').then(r => r.json())")
    assert st["backend"]["charged"] is False and st["backend"]["spent_usd"] == USD
    text = visible(pg)
    assert not SPENT.search(text), [ln for ln in text.splitlines() if SPENT.search(ln)]
    assert not pg._price_errors, pg._price_errors


@pytest.mark.xfail(strict=False, reason="switcher.js (#placeSpent) says 'spent $x on this "
                   "project' on every backend; not this lane's file — requested of its owner")
def test_the_bin_menu_never_says_spent_on_the_plan(browser, live):
    pg = at_board(browser, live)
    pg.locator("#hdBin").click()
    pg.wait_for_function("document.querySelector('#placeSpent').textContent !== ''", timeout=5000)
    text = visible(pg)
    assert not SPENT.search(text), [ln for ln in text.splitlines() if SPENT.search(ln)]


# ------------------------------------------------------------- the API key


def test_on_the_api_backend_spent_is_the_honest_word(browser, live, api_backend):
    pg = at_open(browser, live, running=True)
    line = pg.locator("#progLine").inner_text()
    assert re.search(r" · spent \$0\.27$", line), line
    assert "free on your plan" not in line, line
    assert "billed to the API key" in (pg.locator("#progLine").get_attribute("title") or "")
    pg.unroute("**/api/index")
    pg.close()
    pg = at_open(browser, live, running=False)
    line = pg.locator("#howLine").inner_text()
    assert re.search(r"· spent \$0\.27 ▸$", line), line
    assert "free on your plan" not in line, line
