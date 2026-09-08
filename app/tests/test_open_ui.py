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

import json
import socket
import threading
import time

import pytest

playwright_api = pytest.importorskip("playwright.sync_api",
                                     reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

from conftest import _make_clip  # noqa: E402
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
        # a fake microphone, so holding the mic (or V) records — the floor's arrangement
        browser = pw.chromium.launch(args=[
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
        ])
        pg = browser.new_page(viewport={"width": 1280, "height": 900})
        pg.goto(f"{bin_server['url']}/open")
        pg.wait_for_function(
            "window.sheet && sheet.state.clips && sheet.state.status && sheet.state.index"
            " && sheet.state.themes",
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
    # the slider rests on the project's interval — the tool's default, said in words
    assert page.locator("#interval").is_enabled() and page.locator("#interval").input_value() == "0"
    assert page.locator("#intervalWord").inner_text() == "a frame every 4 s · sees the run, misses the moment"
    assert "not wired" not in page.locator("#sliderHint").inner_text()
    # nothing has run: no progress, no table, no pause, nothing for the pass
    for sel in ("#progress", "#index", "#paused"):
        assert page.locator(sel).is_hidden(), sel
    assert page.locator("#openPass").get_attribute("aria-disabled") == "true"
    assert page.locator("#openPass").get_attribute("href") == "/floor"
    assert page.locator("#links a[href='/']").count() == 1


def test_the_slider_reprices_live_from_by_interval_without_a_round_trip(page, monkeypatch):
    """The design's one control: four stops, coarse to fine, each said in words, and
    the price re-pricing from `by_interval` as the thumb moves — no fetch per move.
    The clips are made 100 s long in the live server so the stops differ: a sheet of
    30 frames covers 120 s at 4 s (one sheet a clip) and 30 s at 1 s (four)."""
    import server
    monkeypatch.setattr(server, "clip_duration", lambda clip: 100.0)
    page.evaluate("sheet.refresh()")
    page.wait_for_function(
        "sheet.state.status.visual.by_interval['1'] !== sheet.state.status.visual.by_interval['4']",
        timeout=10000)
    v = api(page, "/api/status")["visual"]
    assert v["intervals"] == [4.0, 3.0, 2.0, 1.0] and v["interval_s"] == 4.0
    assert v["by_interval"]["1"] > v["by_interval"]["2"] > v["by_interval"]["4"]
    slider = page.locator("#interval")
    assert slider.is_enabled() and slider.get_attribute("max") == "3" and slider.input_value() == "0"
    assert page.locator("#stops span").all_inner_texts() == ["4 s", "3 s", "2 s", "1 s"]
    assert "on" in page.locator("#stops span").first.get_attribute("class")
    assert page.locator("#intervalWord").inner_text() == "a frame every 4 s · sees the run, misses the moment"
    assert f"~${v['by_interval']['4']:.2f}" in page.locator("#priceLine").inner_text()
    assert "3 sheets at a frame every 4 s" in page.locator("#priceDetail").inner_text()
    hint = page.locator("#sliderHint").inner_text()
    assert "re-prices live" in hint and "not wired" not in hint, hint
    # move the thumb: the words, the price line and the button re-price with no request
    hits: list[str] = []
    page.on("request", lambda r: hits.append(r.url) if "/api/" in r.url else None)
    slider.focus()
    page.keyboard.press("ArrowRight")                                    # 3 s
    assert slider.input_value() == "1"
    assert page.locator("#intervalWord").inner_text() == "a frame every 3 s · sees the approach"
    assert f"~${v['by_interval']['3']:.2f}" in page.locator("#priceLine").inner_text()
    page.keyboard.press("End")                                           # 1 s, the far end
    assert slider.input_value() == "3" and page.evaluate("sheet.interval()") == 1
    assert page.locator("#intervalWord").inner_text() == "every 1 s · sees the landing"
    fine = f"~${v['by_interval']['1']:.2f}"
    assert fine in page.locator("#priceLine").inner_text()
    assert fine in page.locator("#indexBtn").inner_text()
    assert "on" in page.locator("#stops span").last.get_attribute("class")
    detail = page.locator("#priceDetail").inner_text()
    assert "a frame every 1 s" in detail and f"{v['fine_calls']} windows" in detail, detail
    assert hits == [], hits
    page.keyboard.press("Home")
    assert slider.input_value() == "0" and page.evaluate("sheet.interval()") == 4
    # a bin the sheets have partly read: the interval applies to the rest, and says so
    page.evaluate("""() => { const v = sheet.state.status.visual;
        v.done = 1; v.pending = v.pending.slice(1); sheet.renderControls(); }""")
    hint = page.locator("#sliderHint").inner_text()
    assert "applies to the 2 clips not yet looked at" in hint and "1 clip already looked at" in hint, hint
    assert "2 clips not yet looked at" in page.locator("#priceLine").inner_text()


def test_index_the_footage_runs_the_journal_and_the_cap_pauses_the_priced_stages(page, bin_server, project):
    """One click runs a real journal walk (tools stubbed): the free stages finish, the
    budget cap holds the priced ones, and the screen says so with a way to resume.
    The slider's stop rides with the click and the project keeps it."""
    budget(bin_server, 0.0)
    page.locator("#order button[data-order=capture]").click()
    page.locator("#interval").focus()
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")                                    # 2 s
    assert page.evaluate("sheet.interval()") == 2
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
    # the interval went with the POST, the EDL carries it, and the slider shows the
    # project's word — nothing looked yet, so it is still the human's to move
    assert ix["interval_s"] == 2.0
    assert edl(bin_server, project)["look"] == {"interval_s": 2.0}
    assert page.evaluate("sheet.state.interval") is None
    assert page.locator("#interval").input_value() == "2" and page.locator("#interval").is_enabled()
    assert page.locator("#intervalWord").inner_text() == "a frame every 2 s · sees the air"
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
    # and the price line has nothing left to sell: the slider is off and says why
    assert "nothing left to buy" in page.locator("#priceLine").inner_text()
    assert page.locator("#interval").is_disabled()
    hint = page.locator("#sliderHint").inner_text()
    assert "nothing left to re-price" in hint and "every 2 s" in hint, hint
    assert page.locator("#intervalWord").inner_text() == ""
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


# ----------------------------------------------------------- the themes (I5.2)
#
# The proposal is one judge-role call, so the backend is scripted the way test_themes.py
# scripts it — the live server runs in this process, so `inference`'s module state is
# shared with it. Every write goes to the EDL that configure(None, …) scaffolded under
# the work dir; the tests read that file, never the page's word on it.

PROPOSAL = {
    "themes": [{"theme": "the greeting", "why": "every clip opens on it",
                "clips": ["CLIP_A.MP4", "CLIP_B.MP4"], "lines": ["hello there"]},
               {"theme": "saying goodbye", "why": "a payoff", "clips": ["CLIP_C.MP4"],
                "lines": ["goodbye"]}],
    "names": ["Spenny"], "notes": "people meeting and parting",
}


def scripted(payload: dict, delay: float = 0.0):
    """A backend that answers `payload` after `delay` seconds and remembers the request."""
    from roughcut import config, inference

    class Scripted:
        name = "scripted"
        seen: list = []

        def complete(self, request):
            Scripted.seen.append(request)
            time.sleep(delay)
            text = json.dumps(payload)
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend="scripted", model=config.model_for(request.role),
                                    projected_usd=1e-4, latency_ms=1, raw=text)

    return Scripted


def edl(bin_server, project) -> dict:
    path = bin_server["work"] / "projects" / f"{project['footage'].name}.edl.json"
    return json.loads(path.read_text(encoding="utf-8"))


def wait_edl(bin_server, project, pred, timeout=8.0) -> dict:
    deadline = time.time() + timeout
    while True:
        d = edl(bin_server, project)
        if pred(d):
            return d
        if time.time() > deadline:
            raise AssertionError(f"EDL never satisfied the predicate: {json.dumps(d)[:800]}")
        time.sleep(0.05)


def chips(page, sel: str) -> list[dict]:
    return page.evaluate(f"""() => Array.from(document.querySelectorAll('{sel} .chip')).map(c => ({{
        text: c.textContent.trim(), on: c.getAttribute('aria-checked') === 'true'}}))""")


def test_the_proposal_is_priced_before_the_button_and_themes_never_score(page):
    t = api(page, "/api/themes")
    assert t["analysed"] == 3 and t["themes"] == [] and t["job"] is None
    price = page.locator("#themesPrice").inner_text()
    assert f"~${t['projected_usd']:.2f}" in price and "one call over the transcripts" in price, price
    assert page.locator("#proposeBtn").is_enabled()
    assert page.locator("#mic").is_visible()
    assert page.locator("#story").get_attribute("placeholder") == "what is this film about? (optional)"
    hint = page.locator("#themesHint").inner_text()
    assert "never score" in hint and "order the index" in hint and "lift and tag" in hint, hint
    for sel in ("#themesEdit", "#themesKept", "#themesRunning"):
        assert page.locator(sel).is_hidden(), sel
    assert "done" not in page.locator("[data-step=themes]").get_attribute("class")
    # a bin the audio pass has not heard: the section says so and the button is disabled
    page.evaluate("() => { sheet.state.themes.analysed = 0; sheet.renderThemes(); }")
    assert "has not listened yet" in page.locator("#themesPrice").inner_text()
    assert page.locator("#proposeBtn").is_disabled()


def test_a_discarded_proposal_writes_nothing(page, bin_server, project):
    from roughcut import inference
    S = scripted(PROPOSAL)
    inference.set_backend(S())
    inference.reset_spend()
    try:
        page.locator("#proposeBtn").click()
        page.wait_for_selector("#themesEdit:not([hidden])", timeout=15000)
        assert len(chips(page, "#chips")) == 2
        page.locator("#chips .chip").first.click()        # untick one, add one, then throw it all away
        page.locator("#addTheme").fill("ski patrol")
        page.locator("#addTheme").press("Enter")
        assert len(chips(page, "#chips")) == 3
        page.locator("#discardBtn").click()
    finally:
        inference.set_backend(None)
    page.wait_for_function("document.querySelector('#toast').textContent.includes('nothing was written')")
    assert page.locator("#themesEdit").is_hidden() and page.locator("#themesAsk").is_visible()
    assert page.locator("#themesKept").is_hidden()
    d = edl(bin_server, project)
    assert not d.get("themes") and not d.get("names"), d
    assert api(page, "/api/themes")["themes"] == []


def test_a_finished_proposal_survives_a_reload_and_an_absent_recogniser_hides_the_mic(
        page, bin_server, project, monkeypatch):
    """GET /api/themes carries `last` (the last finished proposal) and `dictation`: a page
    reloaded after the call answered shows the chips instead of pricing again, and the
    mic never appears when the server says the recogniser is absent — no hold, no 501."""
    from roughcut import dictate, inference
    S = scripted(PROPOSAL)
    inference.set_backend(S())
    inference.reset_spend()
    try:
        page.locator("#proposeBtn").click()
        page.wait_for_selector("#themesEdit:not([hidden])", timeout=15000)
        n = len(chips(page, "#chips"))
        assert n == 2
        monkeypatch.setattr(dictate, "available", lambda: False)
        page.reload()
        page.wait_for_function("window.sheet && sheet.state.themes", timeout=15000)
        page.wait_for_selector("#themesEdit:not([hidden])", timeout=5000)
        assert len(chips(page, "#chips")) == n, "the last proposal came back as chips"
        assert page.evaluate("sheet.state.dictation") is False
        assert page.locator("#mic").is_hidden()
        assert api(page, "/api/themes")["themes"] == [], "still not the EDL's word"
        page.locator("#discardBtn").click()
        page.wait_for_function("document.querySelector('#toast').textContent.includes('nothing was written')")
        # Discard is the proposal's other answer: the server forgets it, a reload asks afresh
        page.wait_for_function("fetch('/api/themes').then(r => r.json()).then(d => d.last === null)",
                               timeout=5000)
        page.reload()
        page.wait_for_function("window.sheet && sheet.state.themes", timeout=15000)
        assert page.locator("#proposeBtn").is_visible() and page.locator("#themesEdit").is_hidden()
    finally:
        inference.set_backend(None)


def test_propose_shows_chips_with_counts_and_keep_writes_exactly_the_ticked_ones(page, bin_server, project):
    from roughcut import inference
    S = scripted(PROPOSAL, delay=1.0)                    # long enough for "listening…" to show
    inference.set_backend(S())
    inference.reset_spend()
    try:
        page.locator("#story").fill("two people talking")
        page.locator("#proposeBtn").click()
        page.wait_for_selector("#themesRunning:not([hidden])", timeout=5000)
        assert "listening" in page.locator("#themesRunning").inner_text()
        assert page.locator("#themesAsk").is_hidden()
        page.wait_for_selector("#themesEdit:not([hidden])", timeout=20000)
    finally:
        inference.set_backend(None)
    assert len(S.seen) == 1 and "two people talking" in S.seen[0].prompt
    assert chips(page, "#chips") == [{"text": "✓ the greeting · 2 clips", "on": True},
                                     {"text": "✓ saying goodbye · 1 clip", "on": True}]
    names = page.locator("#nameChips")
    assert "people" in names.inner_text()
    assert chips(page, "#nameChips") == [{"text": "✓ Spenny", "on": True}]
    assert "people meeting and parting" in page.locator("#themesNotes").inner_text()
    # hover: the quoted line and the why
    page.locator("#chips .chip").nth(1).hover()
    why = page.locator("#themeWhy").inner_text()
    assert "goodbye" in why and "a payoff" in why, why
    # untick one, add one of your own; nothing has reached the EDL yet
    page.locator("#chips .chip").nth(1).click()
    page.locator("#addTheme").fill("the milk joke")
    page.locator("#addTheme").press("Enter")
    assert chips(page, "#chips") == [{"text": "✓ the greeting · 2 clips", "on": True},
                                     {"text": "+ saying goodbye · 1 clip", "on": False},
                                     {"text": "✓ the milk joke", "on": True}]
    assert page.locator("#addTheme").input_value() == ""
    assert "nothing reaches the EDL until Keep" in page.locator("#keepHint").inner_text()
    assert not edl(bin_server, project).get("themes")
    page.locator("#keepBtn").click()
    page.wait_for_selector("#themesKept:not([hidden])", timeout=5000)
    d = edl(bin_server, project)
    assert d["themes"] == ["the greeting", "the milk joke"], d["themes"]
    assert d["names"] == ["Spenny"] and d["story"] == "two people talking"
    # the resting state: the kept chips, change, propose again with its price
    assert [c["text"] for c in chips(page, "#keptChips")] == ["✓ the greeting", "✓ the milk joke"]
    assert [c["text"] for c in chips(page, "#keptNames")] == ["✓ Spenny"]
    assert "~$" in page.locator("#againBtn").inner_text()
    assert page.locator("#changeBtn").is_visible()
    assert "done" in page.locator("[data-step=themes]").get_attribute("class")
    assert api(page, "/api/themes")["themes"] == ["the greeting", "the milk joke"]


def test_change_reopens_the_kept_chips_and_keep_writes_what_is_left_ticked(page, bin_server, project):
    page.wait_for_selector("#themesKept:not([hidden])", timeout=5000)
    page.locator("#changeBtn").click()
    page.wait_for_selector("#themesEdit:not([hidden])", timeout=3000)
    assert chips(page, "#chips") == [{"text": "✓ the greeting", "on": True},
                                     {"text": "✓ the milk joke", "on": True}]
    page.locator("#chips .chip").first.click()
    page.locator("#discardBtn").click()               # a change discarded changes nothing
    page.wait_for_selector("#themesKept:not([hidden])", timeout=3000)
    assert edl(bin_server, project)["themes"] == ["the greeting", "the milk joke"]
    page.locator("#changeBtn").click()
    page.locator("#chips .chip").first.click()
    page.locator("#keepBtn").click()
    page.wait_for_selector("#themesKept:not([hidden])", timeout=5000)
    d = edl(bin_server, project)
    assert d["themes"] == ["the milk joke"] and d["names"] == ["Spenny"], d
    assert [c["text"] for c in chips(page, "#keptChips")] == ["✓ the milk joke"]


# ----------------------------------------------------------------- dictation

def test_the_mic_hides_on_a_501_and_typing_stays(page, monkeypatch, bin_server, project):
    """Dictation is real on this tree (M4), so its absence is simulated — the server runs
    in this process. The hold records and posts; the 501 hides the mic; V is a letter."""
    from roughcut import dictate
    monkeypatch.setattr(dictate, "available", lambda: False)
    hits: list[int] = []
    page.on("response", lambda r: hits.append(r.status) if "/api/dictate" in r.url else None)
    mic = page.locator("#mic")
    assert mic.is_visible()
    box = mic.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.wait_for_function(
        "document.querySelector('#dictState').textContent.includes('listening')", timeout=3000)
    page.wait_for_timeout(700)
    page.mouse.up()
    page.wait_for_function(
        "document.querySelector('#toast').textContent.includes('dictation not built yet')",
        timeout=10000)
    assert hits == [501], hits
    assert mic.is_hidden()
    assert page.evaluate("sheet.state.dictation") is False
    # typing stays, v included, and the story is saved when the field settles
    page.locator("#story").fill("")                   # the field opens on the EDL's story
    page.locator("#story").click()
    page.keyboard.type("very silly skiing")
    assert page.locator("#story").input_value() == "very silly skiing"
    page.evaluate("document.querySelector('#story').blur()")
    wait_edl(bin_server, project, lambda d: d.get("story") == "very silly skiing")


def test_holding_V_in_the_story_dictates_into_it_and_a_tap_types(page, monkeypatch, bin_server, project):
    """The success path with the recogniser stubbed on the server: the recording goes
    through POST /api/dictate for real and the stub's text lands in the field."""
    from roughcut import dictate
    monkeypatch.setattr(dictate, "available", lambda: True)
    monkeypatch.setattr(dictate, "transcribe", lambda path, names=None: {
        "text": "my friends and me skiing", "latency_ms": 800, "model": "stub", "duration_s": 0.7})
    story = page.locator("#story")
    story.fill("")
    story.click()
    page.keyboard.press("v")                          # a tap types the letter
    assert story.input_value() == "v"
    page.keyboard.down("v")                           # a hold speaks
    page.wait_for_function(
        "document.querySelector('#dictState').textContent.includes('listening')", timeout=3000)
    page.wait_for_timeout(700)
    page.keyboard.up("v")
    page.wait_for_function(
        "document.querySelector('#story').value.includes('my friends and me skiing')", timeout=10000)
    assert story.input_value() == "v my friends and me skiing"
    assert "0.8 s" in page.locator("#dictState").inner_text()
    assert page.locator("#mic").is_visible()
    wait_edl(bin_server, project, lambda d: d.get("story") == "v my friends and me skiing")


# ------------------------------------------------------------ the picker (I5.4)
#
# Opening another bin re-points the module's live server (the same configure() main()
# runs), so these come last and the second one puts the server back the way the
# fixture had it. The other bin is a sibling of the project's footage folder — the
# server lists the folders of video next door on its own.

def other_bin(project, name: str):
    folder = project["footage"].parent / name
    folder.mkdir(exist_ok=True)
    if not (folder / "GX01.MP4").exists():
        _make_clip(folder / "GX01.MP4")
    return folder


def picker_rows(page) -> list[dict]:
    return page.evaluate("""() => Array.from(document.querySelectorAll('#pickerList .prow')).map(b => ({
        name: b.querySelector('b').textContent, clips: b.querySelector('.n').textContent,
        flags: Array.from(b.querySelectorAll('.flag')).map(f => f.textContent), path: b.title,
        current: b.classList.contains('current')}))""")


def open_picker(page) -> None:
    page.wait_for_selector("#picker:not([hidden])", timeout=3000)
    page.wait_for_function("document.querySelectorAll('#pickerList .prow').length > 0", timeout=5000)


def test_the_bin_name_opens_a_picker_and_a_running_job_refuses_the_switch(page, project):
    import server
    from roughcut import progress
    other = other_bin(project, "picker-bin")
    name = page.locator("#hdBin")
    assert name.inner_text() == project["footage"].name
    assert page.locator("#picker").is_hidden()
    page.keyboard.press("o")                              # the key, outside any field
    open_picker(page)
    assert name.get_attribute("aria-expanded") == "true"
    table = picker_rows(page)
    assert table[0]["name"] == project["footage"].name and table[0]["current"]
    assert table[0]["clips"] == "3 clips" and table[0]["path"] == str(project["footage"])
    assert table[0]["flags"] == (["journal"] if api(page, "/api/index")["exists"] else ["new"])
    by = {r["name"]: r for r in table}
    assert by["picker-bin"] == {"name": "picker-bin", "clips": "1 clip", "flags": ["new"],
                                "path": str(other), "current": False}
    assert "sidecars" not in by, "a folder without video is not a bin"
    text = page.locator("#picker").inner_text()
    assert "no browsing dialog" in text and "open a folder" in page.locator("#pickerPath").get_attribute("placeholder")
    # Esc closes it; the name reopens it
    page.keyboard.press("Escape")
    assert page.locator("#picker").is_hidden() and name.get_attribute("aria-expanded") == "false"
    name.click()
    open_picker(page)
    # a folder with no video: the server's 400, said, and the panel stays
    page.locator("#pickerPath").fill(str(project["sidecars"]))
    page.locator("#pickerPath").press("Enter")
    page.wait_for_function("document.querySelector('#toast').textContent.includes('no video')", timeout=5000)
    assert page.locator("#picker").is_visible()
    # a job still running: the 409, said, and the bin unchanged
    server.INDEXES["stuck"] = progress.Job("index", "Indexing", id="stuck", state="running")
    try:
        page.locator("#pickerList .prow", has_text="picker-bin").click()
        page.wait_for_function(
            "document.querySelector('#toast').textContent.includes('still running')", timeout=5000)
    finally:
        server.INDEXES.clear()
    assert page.locator("#picker").is_visible()
    assert name.inner_text() == project["footage"].name
    assert page.locator(".card").count() == 3
    assert api(page, "/api/status")["footage"] == str(project["footage"])


def test_opening_another_bin_reloads_the_whole_page_for_it(page, project, bin_server):
    """A row is POST /api/projects/open; on 200 the page reloads everything — header,
    sheet, themes, index — for the new bin, and the way back is a path typed in."""
    import server
    other = other_bin(project, "picker-bin")
    try:
        page.locator("#hdBin").click()
        open_picker(page)
        page.locator("#pickerList .prow", has_text="picker-bin").click()
        page.wait_for_function(
            "document.querySelector('#toast').textContent.includes('opened picker-bin')", timeout=10000)
        assert "new project" in page.locator("#toast").inner_text()
        page.wait_for_function(
            "sheet.state.clips && sheet.state.clips.footage.endsWith('picker-bin')"
            " && sheet.state.status && sheet.state.index && sheet.state.themes", timeout=10000)
        assert page.locator("#picker").is_hidden()
        assert page.locator("#hdBin").inner_text() == "picker-bin"
        assert page.locator("#binName").inner_text() == "picker-bin"
        assert "1 clip" in page.locator("#binMeta").inner_text()
        cards = page.locator(".card")
        assert cards.count() == 1 and cards.first.locator(".cap b").inner_text() == "GX01"
        assert "not yet" in cards.first.locator(".flags").inner_text()     # nobody has listened here
        assert page.locator(".badge").count() == 0                          # and there is no journal
        for sel in ("#index", "#progress", "#paused", "#themesKept", "#themesEdit"):
            assert page.locator(sel).is_hidden(), sel
        assert page.locator("#stepPass").get_attribute("aria-disabled") == "true"
        assert page.locator("#story").input_value() == ""
        assert "has not listened yet" in page.locator("#themesPrice").inner_text()
        assert "1 clip not yet looked at" in page.locator("#priceLine").inner_text()
        assert page.locator("#interval").is_enabled()
        assert "Index the footage" in page.locator("#indexBtn").inner_text()
        s = api(page, "/api/status")
        assert s["footage"] == str(other) and s["clips"] == 1
        assert page.locator("#stepPass").get_attribute("href") == "/floor"
        # and back, by its path typed into the field: the first bin's own EDL, not a new one
        page.keyboard.press("o")
        open_picker(page)
        assert [r["name"] for r in picker_rows(page)][0] == "picker-bin"
        page.locator("#pickerPath").fill(str(project["footage"]))
        page.locator("#pickerPath").press("Enter")
        page.wait_for_function(
            f"document.querySelector('#toast').textContent === 'opened {project['footage'].name}'", timeout=10000)
        page.wait_for_function("sheet.state.clips && sheet.state.clips.clips.length === 3", timeout=10000)
        assert page.locator("#hdBin").inner_text() == project["footage"].name
        assert page.locator(".card").count() == 3
        assert api(page, "/api/status")["footage"] == str(project["footage"])
    finally:
        server.INDEXES.clear()
        server.configure(None, project["footage"], project["sidecars"], bin_server["work"],
                         proxies=False, visual=None)
