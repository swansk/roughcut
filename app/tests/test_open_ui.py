"""The open screen, driven in a real browser (docs/INTAKE.md M5).

What matters on this screen is what the human sees before anything is spent: the
folder as a contact sheet with its free flags, a price before the button, and the
journal's per-clip word while the index runs. So, like test_floor_ui.py, this drives
Chromium against the live server — configured the way `main()` configures it for a bin
nobody has cut yet — with the index's tools stubbed the way test_index.py stubs them,
so a click on the button runs a real journal walk in this process.

The synthetic bin (conftest.py) is three 6 s clips with audio sidecars already on disk:
listened, not yet looked, no proxies, no telemetry, one session.

Skipped, not failed, when playwright is absent:

    uv run --with pytest --with fastapi --with uvicorn --with httpx \
        --with playwright pytest app/tests/test_open_ui.py -q
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

playwright_api = pytest.importorskip("playwright.sync_api",
                                     reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

from test_index import _stub_tools  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def bin_server(project, tmp_path_factory):
    """A live server on a fresh work dir: no EDL, no proxies, no journal — the bin as
    it is the first time the folder is opened. The index's tools are stubbed for the
    module (the real ones mean a GPU and model calls per sheet)."""
    import uvicorn
    import server

    work = tmp_path_factory.mktemp("open")
    server.configure(None, project["footage"], project["sidecars"], work,
                     proxies=False, visual=None)
    mp = pytest.MonkeyPatch()
    _stub_tools(server, mp, work)

    port = _free_port()
    config = uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started, "server did not start"
    yield {"url": f"http://127.0.0.1:{port}", "mp": mp, "work": work}
    srv.should_exit = True
    thread.join(timeout=10)
    mp.undo()


@pytest.fixture
def page(bin_server):
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        pg = browser.new_page(viewport={"width": 1280, "height": 900})
        pg.goto(f"{bin_server['url']}/open")
        pg.wait_for_function("window.sheet && sheet.state.clips", timeout=15000)
        yield pg
        browser.close()


# ------------------------------------------------------------ the sheet (I5.1)

def test_the_folder_reads_as_a_contact_sheet(page, project):
    assert page.locator("#binName").inner_text() == project["footage"].name
    meta = page.locator("#binMeta").inner_text()
    assert "3 clips" in meta and "0:18" in meta and "1 session" in meta, meta
    assert page.locator("#binTele").inner_text() == "telemetry 0/3"
    # one session, its clips in capture order
    assert page.locator(".session").count() == 1
    head = page.locator(".session .lbl").inner_text().lower()     # the label is uppercased by CSS
    assert "session 1" in head and "3 clips" in head and "0:18" in head, head
    cards = page.locator(".card")
    assert cards.count() == 3
    assert [cards.nth(i).locator(".cap b").inner_text() for i in range(3)] == \
        ["CLIP_A", "CLIP_B", "CLIP_C"]
    first = cards.first
    assert first.locator(".tc").inner_text() == "0:06"
    # the free flags: heard, not looked, no sensor stream in a synthetic file
    flags = first.locator(".flags").inner_text()
    assert "listened" in flags and "no telemetry" in flags, flags
    assert "looked" not in flags and "released" not in flags
    # no proxy yet: a placeholder, never a broken image
    assert first.locator(".ph").count() == 1 and first.locator("img").count() == 0
    # no journal on this bin yet: nothing claims a state on the picture
    assert page.locator(".badge").count() == 0
    # the step strip: heard, and nothing for the pass to show yet
    assert "done" in page.locator("[data-step=listen]").get_attribute("class")
    assert page.locator("#stepPass").get_attribute("aria-disabled") == "true"
    # the legend names every flag it uses
    legend = page.locator("#legend").inner_text()
    for word in ("listened", "not yet", "telemetry", "looked", "released", "parked", "missing"):
        assert word in legend, word


def test_the_journals_word_is_derived_the_way_the_journal_derives_it(page):
    word = lambda j: page.evaluate("(j) => sheet.journalWord(j)", j)  # noqa: E731
    done = {s: "done" for s in ("probe", "asr", "proxy", "look", "close", "picks")}
    assert word(None) is None
    assert word({"missing": True, "parked": None, "stages": {**done, "telemetry": "skipped"}}) == "missing"
    assert word({"missing": False, "parked": {"error": "x"}, "stages": {**done, "telemetry": "skipped"}}) == "parked"
    assert word({"missing": False, "parked": None, "stages": {**done, "telemetry": "skipped"}}) == "released"
    assert word({"missing": False, "parked": None, "stages": {**done, "telemetry": "skipped", "look": "running"}}) == "indexing"
    assert word({"missing": False, "parked": None, "stages": {**done, "telemetry": "skipped", "proxy": "failed"}}) == "retrying"
    assert word({"missing": False, "parked": None, "stages": {**done, "telemetry": "skipped", "look": "queued"}}) == "queued"
