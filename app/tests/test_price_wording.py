"""No visible text says money was spent on the subscription backend (REDESIGN-GOAL S).

The standing decision: on `claude_cli` (Karl's Claude plan, every price response
`charged: false`) no price reads as spending money — the dollars are an API-equivalent
estimate, said as one ("~$0.27 API-equivalent · free on your plan") with the estimate's
meaning in the hover. Verdict 1 found "Indexing · 0 of 12 ready · about 0:10 left ·
spent $0.27" on /open (open.js `spentLine` in the progress line). On `anthropic_api`
(`charged: true`) the money is real and "spent" is the honest word.

This serves the suite's synthetic bin from its own work dir (a real journal on disk),
pins this project's spend at $0.27, and reads `document.body.innerText` — only what is
drawn — on the board, its bin menu and File › Project Settings… (where this project's
spend is said beside its cap since wave 17 FOLDIN folded /open's settings drawer into the
editor; /open and /floor are gone, and the index's own money line is lane INDEXPANEL's
Index panel's).

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


def open_page(browser, url: str):
    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg._price_errors = errors                       # read back by the tests
    return pg


def at_board(browser, live):
    pg = open_page(browser, live)
    pg.goto(f"{live}/")
    pg.wait_for_selector("#tl .blk", timeout=20000)
    pg.wait_for_function("window.flowBar && flowBar.state()", timeout=20000)
    return pg


# ------------------------------------------------------------- the subscription


def at_settings(browser, live):
    """File › Project Settings…, open, with this project's spend read in."""
    pg = at_board(browser, live)
    pg.locator("#menubar .menu[data-menu=file] .mtop").click()
    pg.locator("[data-testid=menu-project-settings]").click()
    pg.wait_for_selector("[data-testid=project-settings][open]", timeout=5000)
    pg.wait_for_function("/\\$0\\.27/.test(document.querySelector('[data-testid=ps-spent]').textContent)",
                         timeout=10000)
    return pg


def test_project_settings_never_says_spent_on_the_plan(browser, live):
    """/open's progress and how lines (verdict 1) carried this project's money; the
    settings that cap it are File › Project Settings… now, where it is said beside them."""
    pg = at_settings(browser, live)
    line = pg.locator("[data-testid=ps-spent]").inner_text()
    assert not SPENT.search(line), line
    assert line == "This project: ~$0.27 API-equivalent · free on your plan", line
    # the estimate's meaning is in the hover
    title = pg.locator("[data-testid=ps-spent]").get_attribute("title") or ""
    assert "free on your Claude plan" in title and "$0.27" in title, title
    text = visible(pg)
    assert not SPENT.search(text), [ln for ln in text.splitlines() if SPENT.search(ln)]
    assert not pg._price_errors, pg._price_errors


def test_the_board_never_says_spent_on_the_plan(browser, live):
    pg = at_board(browser, live)
    st = pg.evaluate("fetch('/api/status').then(r => r.json())")
    assert st["backend"]["charged"] is False and st["backend"]["spent_usd"] == USD
    text = visible(pg)
    assert not SPENT.search(text), [ln for ln in text.splitlines() if SPENT.search(ln)]
    assert not pg._price_errors, pg._price_errors


def test_the_bin_menu_never_says_spent_on_the_plan(browser, live):
    pg = at_board(browser, live)
    pg.locator("#hdBin").click()
    pg.wait_for_function("document.querySelector('#placeSpent').textContent !== ''", timeout=5000)
    text = visible(pg)
    assert not SPENT.search(text), [ln for ln in text.splitlines() if SPENT.search(ln)]


# ------------------------------------------------------------- the API key


def test_on_the_api_backend_spent_is_the_honest_word(browser, live, api_backend):
    pg = at_settings(browser, live)
    line = pg.locator("[data-testid=ps-spent]").inner_text()
    assert line == "This project: spent $0.27", line
    assert "free on your plan" not in line, line
    assert "billed to the API key" in (pg.locator("[data-testid=ps-spent]").get_attribute("title") or "")
    pg.close()
