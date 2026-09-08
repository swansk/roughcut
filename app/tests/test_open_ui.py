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
        pg.wait_for_function(
            "window.sheet && sheet.state.clips && sheet.state.status && sheet.state.index",
            timeout=15000)
        yield pg
        browser.close()


def budget(bin_server, usd: float) -> None:
    """The cap the index checks before every priced stage, for this module's server."""
    from roughcut import config
    bin_server["mp"].setattr(config, "budget_usd", lambda: usd)


def api(page, path: str) -> dict:
    return page.evaluate(f"fetch('{path}').then(r => r.json())")


def rows(page) -> list[dict]:
    """The index table as the page shows it: stem, the chip classes, the state."""
    return page.evaluate("""() => {
        const cells = Array.from(document.querySelectorAll('#rows > span'));
        const out = [];
        for (let i = 4; i < cells.length; i += 4) {
            out.push({clip: cells[i].textContent,
                      chips: Array.from(cells[i + 1].querySelectorAll('.stg')).map(c => c.className.replace('stg', '').trim()),
                      priority: cells[i + 2].textContent,
                      state: cells[i + 3].textContent});
        }
        return out;
    }""")


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


# --------------------------------------------------------- the controls (I5.3)

def test_the_look_is_priced_before_the_button_that_buys_it(page):
    status = api(page, "/api/status")
    price = page.locator("#priceLine").inner_text()
    assert f"~${status['visual']['projected_usd']:.2f}" in price, price
    assert "3 clips not yet looked at" in price
    assert "3 sheets" in page.locator("#priceDetail").inner_text()
    b = status["backend"]
    assert page.locator("#budgetLine").inner_text() == f"${b['spent_usd']:.2f} of ${b['budget_usd']:.2f}"
    btn = page.locator("#indexBtn")
    assert btn.is_enabled()
    assert "Index the footage" in btn.inner_text() and "$" in btn.inner_text()
    # the order defaults to the design's, and capture order is one click away
    assert "on" in page.locator("#order button[data-order=priority]").get_attribute("class")
    assert page.locator("#order button[data-order=capture]").is_enabled()
    # the slider is not faked: the page says why it is missing
    assert "not wired yet" in page.locator("#sliderHint").inner_text()
    # nothing has run: no progress, no table, no pause, nothing for the pass
    for sel in ("#progress", "#index", "#paused"):
        assert page.locator(sel).is_hidden(), sel
    assert page.locator("#openPass").get_attribute("aria-disabled") == "true"
    assert page.locator("#openPass").get_attribute("href") == "/floor"
    assert page.locator("#links a[href='/']").count() == 1


def test_index_the_footage_runs_the_journal_and_the_cap_pauses_the_priced_stages(page, bin_server):
    """One click runs a real journal walk (tools stubbed): the free stages finish, the
    budget cap holds the priced ones, and the screen says so with a way to resume."""
    budget(bin_server, 0.0)
    page.locator("#order button[data-order=capture]").click()
    page.locator("#indexBtn").click()
    page.wait_for_function("document.querySelector('#indexBtn').disabled", timeout=3000)
    page.wait_for_selector("#paused:not([hidden])", timeout=60000)
    assert "budget cap" in page.locator("#pausedWhy").inner_text()
    # the run ends on its own with the priced stages waiting; the button comes back
    page.wait_for_function("!document.querySelector('#indexBtn').disabled", timeout=60000)
    assert "Index what isn't done" in page.locator("#indexBtn").inner_text()
    assert page.locator("#resume").is_enabled()
    ix = api(page, "/api/index")
    assert ix["order"] == "capture" and ix["paused_priced"] is True and ix["released"] == []
    # the table: every clip's free stages done, the priced ones queued, in capture order
    table = rows(page)
    assert [r["clip"] for r in table] == ["CLIP_A", "CLIP_B", "CLIP_C"]
    for r in table:
        assert r["chips"] == ["ok", "skip", "ok", "ok", "", "", ""], r
        assert r["state"] == "queued" and r["priority"] != "—", r
    assert page.locator("#progCount").inner_text() == "0 of 3 released"
    assert "priced stages paused" in page.locator("#progMeta").inner_text()
    assert "Index paused" in page.locator("#indexTitle").inner_text()
    assert "3 queued" in page.locator("#indexCounts").inner_text()
    assert page.locator("#journalLog div").count() >= 1
    # the sheet caught up: proxies exist now, and the journal's word is on every picture
    assert page.locator(".card img").count() == 3 and page.locator(".card .ph").count() == 0
    assert page.locator(".card .badge.queued").count() == 3
    # the cap must not take the floor away: with the free stages done the floor's own
    # word (`/api/clips` `released`) says the pass may show them — picks from the words —
    # so the link opens even though the journal's strict release list is empty
    assert page.locator("#openPass").get_attribute("aria-disabled") == "false"
    assert "3 clips" in page.locator("#openPass").inner_text()
    assert page.evaluate("sheet.state.polls") >= 1, "the page polled while the run was going"


def test_resume_priced_stages_releases_every_clip_and_opens_the_pass(page, bin_server):
    budget(bin_server, 15.0)
    page.wait_for_selector("#paused:not([hidden])", timeout=5000)
    page.locator("#resume").click()
    page.wait_for_function(
        "document.querySelector('#progCount').textContent.startsWith('3 of 3')", timeout=90000)
    page.wait_for_function("!document.querySelector('#indexBtn').disabled", timeout=30000)
    assert page.locator("#paused").is_hidden()
    table = rows(page)
    assert len(table) == 3
    for r in table:
        assert r["chips"] == ["ok", "skip", "ok", "ok", "ok", "ok", "ok"], r
        assert r["state"] == "released", r
    assert "Indexed" in page.locator("#indexTitle").inner_text()
    assert "3 released" in page.locator("#indexCounts").inner_text()
    meta = page.locator("#progMeta").inner_text()
    assert "100% of stages" in meta and "$" in meta, meta
    assert page.locator("#progBar").evaluate("el => el.style.width") == "100%"
    # the pass opens on what is released — the link, and the step in the header
    link = page.locator("#openPass")
    assert link.get_attribute("aria-disabled") == "false"
    assert link.inner_text() == "Open the pass on 3 clips →"
    assert page.locator("#stepPass").get_attribute("aria-disabled") == "false"
    assert page.locator(".card .badge.released").count() == 3
    flags = page.locator(".card .flags").first.inner_text()
    assert "looked" in flags and "released" in flags, flags
    # and the price line has nothing left to sell
    assert "nothing left to buy" in page.locator("#priceLine").inner_text()
    assert sorted(api(page, "/api/index")["released"]) == ["CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"]


def test_a_second_run_while_one_is_going_is_refused_not_doubled(page):
    """The server answers 409 to a second POST; the page says so and keeps polling the
    one that is running. Simulated: the button is re-enabled by hand mid-request."""
    page.evaluate("""() => {
        const real = window.fetch.bind(window);
        window.fetch = (url, opts) => (opts && opts.method === 'POST' && url === '/api/index')
            ? Promise.resolve(new Response(JSON.stringify({detail: 'the index is already running'}),
                                           {status: 409, headers: {'content-type': 'application/json'}}))
            : real(url, opts);
    }""")
    page.locator("#indexBtn").click()
    page.wait_for_function(
        "document.querySelector('#toast').textContent.includes('already running')", timeout=5000)
    assert page.locator("#indexBtn").is_enabled()
