"""The open screen, driven in a real browser (docs/INTAKE.md M5, M16 I16.2).

What matters on this screen is what the human sees before anything is spent: the
folder as a contact sheet, the price on the button, one progress line while the index
runs, and afterwards a quiet page — a badge only where something is wrong, and one line
on how it was indexed that opens to the per-clip table. So, like test_floor_ui.py, this drives
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
from pathlib import Path

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

    from roughcut import dictate

    work = tmp_path_factory.mktemp("open")
    server.configure(None, project["footage"], project["sidecars"], work,
                     proxies=False, visual=None)
    mp = pytest.MonkeyPatch()
    _stub_tools(server, mp, work)
    # the recogniser is installed for the module, whatever this machine has: the mic's
    # tests simulate its absence on purpose, and the page hides the mic when it is absent
    mp.setattr(dictate, "available", lambda: True)

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
            " && sheet.state.brief",
            timeout=15000)
        yield pg
        browser.close()


def budget(bin_server, usd: float) -> None:
    """The cap the index checks before every priced stage, for this module's server.
    `server.budget_cap()` is what the pause rule and the status line read — the
    environment, else the setting saved under --work, else the default — so pinning
    `config.budget_usd` alone would be ignored once the settings tests have saved one."""
    import server
    bin_server["mp"].setattr(server, "budget_cap", lambda: usd)


def money(b: dict) -> str:
    """The one money number on /open (INTAKE I16.0g): this project's spend, and the cap
    only when there is one."""
    cap = b.get("budget_usd")
    return f"${b['spent_usd']:.2f}" + (f" of ${cap:.2f} cap" if cap is not None else "")


def api(page, path: str) -> dict:
    return page.evaluate(f"fetch('{path}').then(r => r.json())")


def flow_state(page, stage: str) -> str:
    """The flow's word on one stage (INTAKE M14), from the server: the step bar that
    drew it is off every screen (M16 decision 2), Next alone stays."""
    return next(s["state"] for s in api(page, "/api/flow")["stages"] if s["key"] == stage)


def in_view(page, sel: str) -> bool:
    """Whether an element is drawn inside the 900 px window, without scrolling."""
    return page.evaluate(f"""() => {{ const el = document.querySelector({sel!r});
        if (!el) return false; const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && r.top >= 0 && r.bottom <= innerHeight; }}""")


def open_how(page) -> None:
    """Open the per-clip table behind the how-it-was-indexed line."""
    page.wait_for_selector("#howLine:not([hidden])", timeout=10000)
    if page.locator("#how").is_hidden():
        page.locator("#howLine").click()
    page.wait_for_selector("#how:not([hidden])", timeout=3000)


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
    # one header row (INTAKE M16 C1): the bin · cut, Next, ? and settings — no brand
    hd = page.locator("#hd")
    assert hd.bounding_box()["height"] <= 50
    assert page.locator("#hd #hdBin").count() == 1 and page.locator("#hd #flow").count() == 1
    assert page.locator("#hd #keysBtn").inner_text() == "?"
    assert page.locator("#hd #settingsBtn").inner_text() == "settings"
    assert "ROUGHCUT" not in hd.inner_text() and page.locator(".brand").count() == 0
    # a new bin: the headline says it is not indexed yet; no second bin name, no telemetry
    assert page.locator("#headline").inner_text() == "3 clips · 0:18 · not indexed yet"
    for gone in ("#binName", "#binTele", "#legend", "#openPass", "#passHint", "#links",
                 "#settingsLine", "#indexHint", "#priceDetail", "#sliderHint"):
        assert page.locator(gone).count() == 0, gone
    assert page.locator("a[href='/']").count() == 0, "no '← the cut board'"
    # one day, its clips in capture order: picture, length, name — no flags
    assert page.locator(".session").count() == 1
    head = page.locator(".session .lbl").inner_text().lower()     # the label is uppercased by CSS
    assert head.endswith("· 3 clips") and "session" not in head, head
    cards = page.locator(".card")
    assert cards.count() == 3
    assert [cards.nth(i).locator(".cap b").inner_text() for i in range(3)] == \
        ["CLIP_A", "CLIP_B", "CLIP_C"]
    first = cards.first
    assert first.locator(".tc").inner_text() == "0:06"
    assert first.locator(".flags, .flag").count() == 0
    assert first.inner_text().strip().split() == ["0:06", "CLIP_A"], first.inner_text()
    # no proxy yet: a placeholder with no words, never a broken image
    assert first.locator(".ph").count() == 1 and first.locator("img").count() == 0
    # not indexed is not wrong: no badge on any card, no coverage strip
    assert page.locator(".badge").count() == 0 and page.locator(".dv-mini").count() == 0
    # setup: the sentence, how closely it looks, and the one priced button — in view
    assert page.locator("label[for=story]").inner_text() == "What is this film about?"
    assert page.locator("#lookWord").inner_text() == "Looks at a frame every 4 s"
    assert page.locator("#look").is_hidden(), "the slider waits behind 'change'"
    btn = page.locator("#indexBtn")
    assert in_view(page, "#indexBtn")
    assert set(btn.get_attribute("data-next-for").split()) == {"index", "footage"}
    # no blue but Next's: no primary button is left on the page (C2)
    assert page.locator(".primary, a.btn").count() == 0
    assert flow_state(page, "pass") == "waiting"
    assert flow_state(page, "index") == "ready"
    # one blue (C2): Next's target when it is on this screen, else the chip. These
    # clips were all heard (the fixture's sidecars), so Next is the first cut, elsewhere
    page.wait_for_function("""() => { const n = window.flowBar && flowBar.state() && flowBar.state().next;
        const b = [...document.querySelectorAll('.is-next')];
        return !!n && n.stage === 'cut' && b.length === 1 && b[0].id === 'flowNext'; }""",
                           timeout=10000)
    # when Next is the index — a bin nothing was heard in — Index the footage is the blue
    nxt = {"stage": "index", "sentence": "Index the footage", "verb": "Index", "screen": "/open",
           "href": "/open", "kind": "go"}
    page.route("**/api/flow", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"stages": [], "blockers": [], "running": False, "next": nxt})))
    try:
        page.evaluate("flowBar.poll()")
        page.wait_for_function("""() => { const b = [...document.querySelectorAll('.is-next')];
            return b.length === 1 && b[0].id === 'indexBtn'; }""", timeout=5000)
    finally:
        page.unroute("**/api/flow")
        page.evaluate("flowBar.poll()")
    # nothing ran: no progress line, no pause, no how-it-was-indexed line
    for sel in ("#progress", "#paused", "#howLine", "#how"):
        assert page.locator(sel).is_hidden(), sel


def test_the_keys_sit_behind_question_mark(page):
    keys = page.locator("#keys")
    assert keys.is_hidden()
    page.keyboard.press("?")
    assert keys.is_visible() and page.locator("#keysBtn").get_attribute("aria-expanded") == "true"
    hd = page.locator("#hd").bounding_box()
    assert keys.bounding_box()["y"] >= hd["y"] + hd["height"], "it hangs under the header"
    text = keys.inner_text()
    for word in ("switch bin or cut", "hold to speak", "settings"):
        assert word in text, text
    page.keyboard.press("Escape")
    assert keys.is_hidden()
    page.locator("#keysBtn").click()
    assert keys.is_visible()
    page.locator("#headline").click()                       # a click elsewhere puts it away
    assert keys.is_hidden()


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


def test_an_empty_folder_offers_nothing_to_index(page):
    assert page.evaluate("sheet.phase()") == "setup"
    page.evaluate("""() => { sheet.state.clips = {...sheet.state.clips, clips: [], total_s: 0};
        sheet.renderSheet(); }""")
    assert page.evaluate("sheet.phase()") == "empty"
    assert page.locator("#empty").is_visible() and page.locator("#setup").is_hidden()


# --------------------------------------------------------- the controls (I5.3)

def test_the_look_is_priced_on_the_button_that_buys_it(page):
    status = api(page, "/api/status")
    btn = page.locator("#indexBtn")
    assert btn.is_enabled()
    assert btn.inner_text() == f"Index the footage · ~${status['visual']['projected_usd']:.2f}"
    assert "priced" in btn.get_attribute("class") and "primary" not in btn.get_attribute("class")
    b = status["backend"]
    assert page.locator("#budgetLine").inner_text() == money(b)
    # no backend or model line: the CLI banner (/cli.js) speaks when the CLI needs Karl
    assert page.locator("#backendLine").count() == 0
    assert b["model"] not in page.locator("body").inner_text()
    # "change" opens the slider, resting on the project's interval, said in words
    page.locator("#lookChange").click()
    assert page.locator("#look").is_visible()
    assert page.locator("#interval").is_enabled() and page.locator("#interval").input_value() == "0"
    assert page.locator("#intervalWord").inner_text() == "sees the run, misses the moment"
    page.locator("#lookChange").click()
    assert page.locator("#look").is_hidden()
    # the order is a setting now, next to the cap and the workers
    assert page.locator("#order").is_hidden()
    page.locator("#settingsBtn").click()
    assert page.locator("#settings #order").is_visible()
    assert "on" in page.locator("#order button[data-order=priority]").get_attribute("class")
    assert page.locator("#order button[data-order=capture]").is_enabled()
    page.keyboard.press("Escape")
    # nothing has run: no progress, no table, no pause
    for sel in ("#progress", "#how", "#paused"):
        assert page.locator(sel).is_hidden(), sel


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
    page.locator("#lookChange").click()
    slider = page.locator("#interval")
    assert slider.is_enabled() and slider.get_attribute("max") == "3" and slider.input_value() == "0"
    assert page.locator("#stops span").all_inner_texts() == ["4 s", "3 s", "2 s", "1 s"]
    assert "on" in page.locator("#stops span").first.get_attribute("class")
    assert page.locator("#intervalWord").inner_text() == "sees the run, misses the moment"
    assert f"~${v['by_interval']['4']:.2f}" in page.locator("#indexBtn").inner_text()
    # move the thumb: the words and the button re-price with no request
    hits: list[str] = []
    page.on("request", lambda r: hits.append(r.url) if "/api/" in r.url else None)
    slider.focus()
    page.keyboard.press("ArrowRight")                                    # 3 s
    assert slider.input_value() == "1"
    assert page.locator("#intervalWord").inner_text() == "sees the approach"
    assert page.locator("#lookWord").inner_text() == "Looks at a frame every 3 s"
    assert f"~${v['by_interval']['3']:.2f}" in page.locator("#indexBtn").inner_text()
    page.keyboard.press("End")                                           # 1 s, the far end
    assert slider.input_value() == "3" and page.evaluate("sheet.interval()") == 1
    assert page.locator("#intervalWord").inner_text() == "sees the landing"
    assert f"~${v['by_interval']['1']:.2f}" in page.locator("#indexBtn").inner_text()
    assert "on" in page.locator("#stops span").last.get_attribute("class")
    assert hits == [], hits
    page.keyboard.press("Home")
    assert slider.input_value() == "0" and page.evaluate("sheet.interval()") == 4
    # nothing left to look at: the line and its slider go, the button has no price
    page.evaluate("""() => { const v = sheet.state.status.visual;
        v.pending = []; sheet.renderControls(); }""")
    assert page.locator("#lookLine").is_hidden() and page.locator("#look").is_hidden()
    assert "$" not in page.locator("#indexBtn").inner_text()


# ------------------------------------- settings: workers + the cap (I5.3's other half)
#
# The drawer behind the header's settings button, against the real `GET/PUT
# /api/settings`. These run before the index tests: the cap they save is the one
# `/api/status` shows until `budget()` pins `server.budget_cap` for the run, and they
# leave the workers and the cap at the defaults.

STAGES_WITH_WORKERS = ["probe", "asr", "proxy", "sheet", "picks"]


def worker_fields(page) -> dict[str, str]:
    return page.evaluate("""() => Object.fromEntries(Array.from(
        document.querySelectorAll('#workers input[data-stage]')).map(f => [f.dataset.stage, f.value]))""")


def open_settings(page) -> dict:
    """Open the drawer and wait for the server's answer to be on it."""
    page.wait_for_selector("#settings:not([hidden])", timeout=3000)
    page.wait_for_function("sheet.state.settings && !document.querySelector('#settingsForm').hidden",
                           timeout=5000)
    # the drawer paints the last answer first and then asks again; a test reads the
    # server's word now, not whichever of the two landed before it looked
    return page.evaluate("async () => { await sheet.refreshSettings(); return sheet.state.settings; }")


def test_the_gear_or_comma_opens_settings_and_a_saved_cap_moves_the_budget_line(page):
    assert page.locator("#settings").is_hidden()
    gear = page.locator("#settingsBtn")
    assert gear.get_attribute("aria-expanded") == "false"
    page.keyboard.press(",")                              # the key, outside any field
    s = open_settings(page)
    assert gear.get_attribute("aria-expanded") == "true"
    hd = page.locator("#hd").bounding_box()
    assert page.locator("#settings").bounding_box()["y"] >= hd["y"] + hd["height"]
    # the current values, straight from GET /api/settings
    got = api(page, "/api/settings")
    assert got["budget_usd"] == s["budget_usd"] and got["workers"] == s["workers"]
    assert set(got["workers"]) == set(STAGES_WITH_WORKERS)
    assert got["source"]["budget_usd"] in ("settings", "default")
    cap = page.locator("#capField")
    # no cap is the default (INTAKE M16 decision 7), said by the radio, the field empty
    assert got["budget_usd"] is None
    assert page.locator("#capNone").is_checked() and not page.locator("#capOn").is_checked()
    assert cap.is_enabled() and cap.input_value() == ""
    assert "no cap" in page.locator("#capHint").inner_text()
    assert page.locator("#settingsLine").count() == 0, "no summary of the drawer under it"
    assert page.locator("#capSpent").count() == 0, "one money number: the drawer does not repeat it"
    assert "ROUGHCUT_BUDGET_USD" not in page.locator("#capHint").inner_text()
    assert worker_fields(page) == {k: str(v) for k, v in got["workers"].items()}
    assert page.locator("#workers .wrow").count() == 5
    hints = page.locator("#workers .whint").all_inner_texts()
    assert any("model loads once" in h for h in hints) and any("spends faster, not better" in h for h in hints)
    assert any("4K encodes" in h for h in hints), hints
    assert page.locator("#settingsRunning").is_hidden()           # nothing is running
    assert page.locator("#settingsGone").is_hidden()
    # Esc closes; the gear opens it again; the gear again closes it
    page.keyboard.press("Escape")
    assert page.locator("#settings").is_hidden() and gear.get_attribute("aria-expanded") == "false"
    gear.click()
    open_settings(page)
    gear.click()
    assert page.locator("#settings").is_hidden()
    # a cap of 5, saved: the toast says what changed, the budget line re-reads, the
    # server keeps it and says it came from settings
    page.keyboard.press(",")
    open_settings(page)
    cap.fill("5")
    assert page.locator("#capOn").is_checked(), "typing a number is choosing a cap"
    page.locator("#settingsSave").click()
    page.wait_for_function("document.querySelector('#toast').textContent.startsWith('saved')", timeout=5000)
    toast = page.locator("#toast").inner_text()
    assert "cap none → $5.00" in toast, toast
    page.wait_for_function("document.querySelector('#budgetLine').textContent.endsWith('of $5.00 cap')", timeout=5000)
    got = api(page, "/api/settings")
    assert got["budget_usd"] == 5.0 and got["source"]["budget_usd"] == "settings"
    assert api(page, "/api/status")["backend"]["budget_usd"] == 5.0
    assert "this project's spend" in page.locator("#capHint").inner_text()
    assert page.locator("#settings").is_visible(), "saving leaves the drawer open"
    # nothing else changed, and saving again says so
    assert got["workers"] == s["workers"]
    page.locator("#settingsSave").click()
    page.wait_for_function("document.querySelector('#toast').textContent.includes('nothing changed')", timeout=5000)
    page.keyboard.press("Escape")


def test_a_worker_count_is_clamped_in_the_ui_and_a_bad_one_sent_on_purpose_shows_the_400(page):
    page.locator("#settingsBtn").click()
    s = open_settings(page)
    sheet = page.locator("#workers input[data-stage=sheet]")
    row = page.locator("#workers .wrow[data-stage=sheet]")
    # typed out of range: clamped when the field settles; the steppers stop at the ends
    sheet.fill("12")
    sheet.press("Tab")
    assert sheet.input_value() == "8"
    row.locator("button[data-d='1']").click()
    assert sheet.input_value() == "8"
    sheet.fill("0")
    sheet.press("Tab")
    assert sheet.input_value() == "1"
    row.locator("button[data-d='-1']").click()
    assert sheet.input_value() == "1"
    row.locator("button[data-d='1']").click()
    row.locator("button[data-d='1']").click()
    assert sheet.input_value() == "3"
    page.locator("#settingsSave").click()
    page.wait_for_function("document.querySelector('#toast').textContent.includes('sheet workers')", timeout=5000)
    assert f"sheet workers {s['workers']['sheet']} → 3" in page.locator("#toast").inner_text()
    assert api(page, "/api/settings")["workers"]["sheet"] == 3
    # a bad value the UI would never send, sent on purpose: the server's 400, said on the
    # drawer, and nothing written
    statuses: list[int] = []
    page.on("response", lambda r: statuses.append(r.status) if r.url.endswith("/api/settings") and r.request.method == "PUT" else None)
    for bad in ({"workers": {"sheet": 12}}, {"workers": {"grade": 1}}, {"budget_usd": 0}):
        statuses.clear()
        assert page.evaluate("(b) => sheet.saveSettings(b)", bad) is None
        page.wait_for_selector("#settingsErr:not([hidden])", timeout=5000)
        err = page.locator("#settingsErr").inner_text()
        assert err.startswith("not saved:") and len(err) > len("not saved: "), err
        assert statuses == [400], (bad, statuses)
    got = api(page, "/api/settings")
    assert got["workers"]["sheet"] == 3 and got["budget_usd"] == 5.0
    # the UI never sends the bad one: a stepper at 8 stays 8 through Save
    sheet.fill("8")
    page.locator("#settingsSave").click()
    page.wait_for_function("document.querySelector('#toast').textContent.includes('3 → 8')", timeout=5000)
    assert page.locator("#settingsErr").is_hidden()
    assert api(page, "/api/settings")["workers"]["sheet"] == 8
    page.keyboard.press("Escape")


def test_a_cap_from_the_environment_disables_the_field_and_reset_fills_the_defaults(page, monkeypatch):
    """ROUGHCUT_BUDGET_USD in the live server's process wins over the saved cap: the
    field is disabled with the reason, the budget line shows the environment's number,
    and Save still writes the workers. Reset fills the defaults in; Save applies them."""
    monkeypatch.setenv("ROUGHCUT_BUDGET_USD", "7.5")
    page.keyboard.press(",")
    s = open_settings(page)
    assert s["source"]["budget_usd"] == "env" and s["budget_usd"] == 7.5
    cap = page.locator("#capField")
    assert cap.is_disabled() and float(cap.input_value()) == 7.5
    hint = page.locator("#capHint").inner_text()
    assert "ROUGHCUT_BUDGET_USD" in hint and "wins" in hint, hint
    page.evaluate("sheet.refresh()")
    assert page.locator("#capNone").is_disabled() and page.locator("#capOn").is_checked()
    page.wait_for_function("document.querySelector('#budgetLine').textContent.endsWith('of $7.50 cap')", timeout=5000)
    # Reset: the defaults fill the form, the hint says Save applies them, nothing written yet
    page.locator("#settingsReset").click()
    assert worker_fields(page) == {k: str(v) for k, v in s["defaults"]["workers"].items()}
    assert "Save applies" in page.locator("#settingsState").inner_text()
    assert api(page, "/api/settings")["workers"]["sheet"] == 8
    page.locator("#settingsSave").click()
    page.wait_for_function("document.querySelector('#toast').textContent.includes('sheet workers 8 → 2')", timeout=5000)
    got = api(page, "/api/settings")
    assert got["workers"] == s["defaults"]["workers"]
    assert got["budget_usd"] == 7.5 and got["source"]["budget_usd"] == "env", "the cap was not sent"
    page.keyboard.press("Escape")
    # the environment gone, the saved cap is the word again
    monkeypatch.delenv("ROUGHCUT_BUDGET_USD")
    got = api(page, "/api/settings")
    assert got["budget_usd"] == 5.0 and got["source"]["budget_usd"] == "settings"
    # and the cap back to its default for the tests that follow, through the form — the
    # drawer re-reads on opening, so `defaults` is the server's word with no environment
    page.keyboard.press(",")
    s = open_settings(page)
    assert s["source"]["budget_usd"] == "settings" and s["defaults"]["budget_usd"] is None
    assert page.locator("#capField").is_enabled()
    page.locator("#settingsReset").click()
    assert page.locator("#capNone").is_checked() and page.locator("#capField").input_value() == ""
    page.locator("#settingsSave").click()
    page.wait_for_function("document.querySelector('#toast').textContent.includes('cap $5.00 → none')", timeout=5000)
    assert api(page, "/api/settings")["budget_usd"] is None
    page.wait_for_function("!document.querySelector('#budgetLine').textContent.includes('cap')", timeout=5000)
    page.keyboard.press("Escape")


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


def test_index_the_footage_runs_the_journal_and_the_cap_pauses_the_priced_stages(page, bin_server, project):
    """One click runs a real journal walk (tools stubbed): one progress line while it
    goes, the free stages finish, the budget cap holds the priced ones, and the screen
    says so with a way to resume. The slider's stop rides with the click and the project
    keeps it; the order is the settings drawer's."""
    budget(bin_server, 0.0)
    page.locator("#settingsBtn").click()
    page.locator("#order button[data-order=capture]").click()
    page.keyboard.press("Escape")
    page.locator("#lookChange").click()
    page.locator("#interval").focus()
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")                                    # 2 s
    assert page.evaluate("sheet.interval()") == 2
    page.locator("#indexBtn").click()
    # after the click: one progress line in the button's place
    page.wait_for_selector("#progress:not([hidden])", timeout=5000)
    assert page.locator("#setup").is_hidden()
    assert page.locator("#progLine").inner_text().startswith("Indexing")
    page.wait_for_selector("#paused:not([hidden])", timeout=60000)
    assert page.locator("#progress").is_hidden()
    why = page.locator("#pausedWhy").inner_text()
    assert "budget cap" in why and "Raise or remove the cap" in why, why
    # the run ended with only the paused looks left: Resume is the one thing to do
    assert page.locator("#setup").is_hidden(), "nothing for Index to do while the looks wait"
    assert page.locator("#resume").is_enabled()
    assert page.locator("#resume").get_attribute("data-next-for") == "index"
    ix = api(page, "/api/index")
    assert ix["order"] == "capture" and ix["paused_priced"] is True and ix["released"] == []
    # the interval went with the POST, the EDL carries it, and the page follows the
    # project's word — nothing looked yet
    assert ix["interval_s"] == 2.0
    assert edl(bin_server, project)["look"] == {"interval_s": 2.0}
    assert page.evaluate("sheet.state.interval") is None and page.evaluate("sheet.interval()") == 2
    # the paused box in the editor's words, the price on the button
    title = page.locator("#pausedTitle").inner_text()
    assert title == "Looks paused on 3 clips", title
    assert "lead" not in page.locator("#paused").inner_text()
    assert page.locator("#resume").inner_text().startswith("Resume · ~$")
    # the sheet caught up: proxies exist now, pictures from mid-clip, and the one badge
    # on every card is the thing that waits
    assert page.locator(".card img").count() == 3 and page.locator(".card .ph").count() == 0
    assert "t=3.00" in page.locator(".card img").first.get_attribute("src")
    assert page.locator(".card .badge").count() == 3
    assert page.locator(".card .badge.waiting").first.inner_text().lower() == "look paused"
    # how it was indexed, one line: heard, nothing looked at yet, this project's spend
    line = page.locator("#howLine").inner_text()
    assert line.startswith("every word heard · spent $") and line.endswith("▸"), line
    assert "a frame every" not in line and "close looks" not in line, line
    # and behind it, the table: every clip's free stages done, the priced ones queued,
    # in capture order — on the pass from their words, their looks held by the pause
    open_how(page)
    table = rows(page)
    assert [r["clip"] for r in table] == ["CLIP_A", "CLIP_B", "CLIP_C"]
    for r in table:
        assert r["chips"] == ["ok", "skip", "ok", "ok", "", "", ""], r
        assert r["state"] == "on the pass · look paused" and r["priority"] != "—", r
    assert "Looks paused" in page.locator("#indexTitle").inner_text()
    assert page.locator("#indexCounts").inner_text() == "3 look paused"
    assert page.locator("#journalLog div").count() >= 1
    page.locator("#howLine").click()
    assert page.locator("#how").is_hidden()
    # the flow says it waits on the editor, and its action is the Resume on this page
    # (Next's own choice may be the CLI banner's on a machine without the CLI)
    st = next(x for x in api(page, "/api/flow")["stages"] if x["key"] == "index")
    assert st["state"] == "needs-you" and st["needs"]["action"]["stage"] == "index", st
    assert page.evaluate("sheet.state.polls") >= 1, "the page polled while the run was going"
    # how closely to look is still Karl's before Resume spends on it: the look line sits
    # in the paused box (Index's box is gone), and the slider re-prices Resume
    assert page.locator("#paused #lookLine").is_visible()
    assert page.locator("#lookWord").inner_text() == "Looks at a frame every 2 s"
    ix = api(page, "/api/index")
    assert page.locator("#resume").inner_text() == f"Resume · ~${ix['waiting']['usd']:.2f}"
    page.locator("#lookChange").click()
    assert page.locator("#paused #interval").is_visible() and page.locator("#interval").is_enabled()
    page.locator("#interval").focus()
    page.keyboard.press("ArrowRight")                                    # 1 s
    assert page.evaluate("sheet.interval()") == 1
    by = api(page, "/api/status")["visual"]["by_interval"]
    want = ix["waiting"]["usd"] + by["1"] - by["2"]
    assert page.locator("#resume").inner_text() == f"Resume · ~${want:.2f}"
    # (the fixture's 6 s clips are one sheet at any stop; a longer bin's are not)
    page.evaluate("""() => { sheet.state.status.visual.by_interval = {'4': 1, '3': 1.5, '2': 2, '1': 3.5};
        sheet.renderControls(); }""")
    assert page.locator("#resume").inner_text() == f"Resume · ~${ix['waiting']['usd'] + 1.5:.2f}"


def test_resume_releases_every_clip_and_the_page_goes_quiet(page, bin_server):
    budget(bin_server, 15.0)
    page.wait_for_selector("#paused:not([hidden])", timeout=5000)
    page.locator("#resume").click()
    page.wait_for_function(
        "sheet.state.index && sheet.state.index.exists && !sheet.state.index.running"
        " && sheet.state.index.progress.released === 3", timeout=90000)
    page.wait_for_selector("#howLine:not([hidden])", timeout=10000)
    page.evaluate("sheet.refresh()")
    # done: nothing to do here, nothing wrong — no box, no button, no badge
    for sel in ("#paused", "#setup", "#progress"):
        assert page.locator(sel).is_hidden(), sel
    assert page.locator(".card .badge").count() == 0
    assert page.locator("#headline").inner_text().startswith("3 clips · 0:18 · ")
    assert "not indexed" not in page.locator("#headline").inner_text()
    # how it was indexed, in one line honest to the sidecars, with the one money number
    b = api(page, "/api/status")["backend"]
    assert b["spent_usd"] > 0, "the looks this run bought are this project's spend"
    page.wait_for_function(f"document.querySelector('#budgetLine').textContent === {money(b)!r}", timeout=5000)
    line = page.locator("#howLine").inner_text()
    assert line == f"every word heard · a frame every 2 s · close looks on all 3 · spent {money(b)} ▸", line
    open_how(page)
    table = rows(page)
    assert len(table) == 3
    for r in table:
        assert r["chips"] == ["ok", "skip", "ok", "ok", "ok", "ok", "ok"], r
        assert r["state"] == "released", r
    assert "Indexed" in page.locator("#indexTitle").inner_text()
    assert "3 released" in page.locator("#indexCounts").inner_text()
    assert page.locator("#openPass").count() == 0, "Next and the switcher go to the pass"
    assert sorted(api(page, "/api/index")["released"]) == ["CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"]


def test_a_problem_shows_as_one_badge_and_junk_is_answered_on_the_card(page, bin_server, project, monkeypatch):
    """Badges only where something is wrong (I16.2): a clip the audio pass has not
    heard, and junk? with Junk / Keep right on the card — POST /api/junk, free."""
    import server
    page.wait_for_selector("#howLine:not([hidden])", timeout=10000)
    word = lambda c: page.evaluate("(c) => (sheet.badgeOf(c) || {}).text || null", c)  # noqa: E731
    base = {"clip": "X.MP4", "analysed": True, "junk": "clean", "journal": None}
    assert word(base) is None
    assert word({**base, "analysed": False}) == "not heard yet"
    assert word({**base, "junk": "proposed"}) == "junk?"
    assert word({**base, "journal": {"missing": True, "parked": None, "stages": {}}}) == "missing"
    assert word({**base, "analysed": False,
                 "journal": {"missing": False, "parked": {"error": "x"}, "stages": {}}}) == "parked"
    # a proposal on a real card, answered there
    monkeypatch.setattr(server, "junk_rows", lambda edl=None, measure=False: [
        {"clip": "CLIP_B.MP4", "state": "proposed", "proposed": True, "verdict": None,
         "reasons": ["black"]}])
    page.evaluate("sheet.refresh()")
    card = page.locator('.card[data-clip="CLIP_B.MP4"]')
    page.wait_for_selector('.card[data-clip="CLIP_B.MP4"] .badge.junk', timeout=5000)
    assert card.locator(".badge").inner_text().lower() == "junk?"
    assert card.locator("button").all_inner_texts() == ["Junk", "Keep"]
    posted: list[dict] = []
    page.on("request", lambda r: posted.append(r.post_data_json)
            if r.url.endswith("/api/junk") and r.method == "POST" else None)
    card.locator("button[data-junk=keep]").click()
    page.wait_for_function("document.querySelector('#toast').textContent.includes('CLIP_B kept')", timeout=5000)
    assert posted == [{"clip": "CLIP_B.MP4", "verdict": "keep"}]
    assert (edl(bin_server, project).get("junk") or {}).get("CLIP_B.MP4", {}).get("verdict") == "keep"
    page.evaluate("fetch('/api/junk', {method: 'POST', headers: {'content-type': 'application/json'},"
                  " body: JSON.stringify({clip: 'CLIP_B.MP4', verdict: null})})")


# ------------------------------------------------- the sentence (M16 decision 8)
#
# The themes step went: the one sentence about the film is the brief, its words tag
# moments for free (roughcut/picks.py), and names stay as dictation's vocabulary, never
# shown. Every write goes to the EDL that configure(None, …) scaffolded under the work
# dir; the tests read that file, never the page's word on it.

PROPOSAL = {
    "themes": [{"theme": "the greeting", "why": "every clip opens on it",
                "clips": ["CLIP_A.MP4", "CLIP_B.MP4"], "lines": ["hello there"]},
               {"theme": "saying goodbye", "why": "a payoff", "clips": ["CLIP_C.MP4"],
                "lines": ["goodbye"]}],
    "names": ["Spenny"], "notes": "people meeting and parting",
}


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


def test_the_themes_step_is_gone_and_a_waiting_proposal_shows_nothing(page):
    """A proposal paid for before the step went still sits on disk (Killington's, from
    Sep 8): the page shows none of it — no price to propose, no chips, no names, no
    Keep / Discard, no 'the transcripts' one sentence'."""
    import server
    path = server.proposal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": "sep8", "proposal": PROPOSAL}), encoding="utf-8")
    try:
        page.reload()
        page.wait_for_function("window.sheet && sheet.state.brief && sheet.state.clips", timeout=15000)
        assert api(page, "/api/themes")["last"]["id"] == "sep8"
        for gone in ("#proposeBtn", "#themesPrice", "#chips", "#nameChips", "#addTheme", "#keepBtn",
                     "#discardBtn", "#themesNotes", "#themesHint", "#keptChips", "#againBtn"):
            assert page.locator(gone).count() == 0, gone
        body = page.locator("body").inner_text()
        assert "theme" not in body.lower(), body
        for word in ("Spenny", "people meeting", "the greeting", "Keep"):
            assert word not in body, word
        assert page.locator("#story").get_attribute("placeholder") == "optional — a sentence is enough"
    finally:
        path.unlink(missing_ok=True)


def test_the_sentence_is_saved_and_its_words_tag_moments_for_free(page, bin_server, project):
    """One field (C7); the EDL's `story` when it settles; and its content words tag the
    pass's moments with no model call — "people" and "saying" find nothing, "goodbye"
    finds the candidate every clip ends on."""
    from roughcut import inference
    calls: list = []

    class Refuse:
        name = "refuse"

        def complete(self, request):
            calls.append(request)
            raise inference.InferenceError("no model call is expected here")

    inference.set_backend(Refuse())
    try:
        story = page.locator("#story")
        story.fill("people saying goodbye")
        page.evaluate("document.querySelector('#story').blur()")
        wait_edl(bin_server, project, lambda d: d.get("story") == "people saying goodbye")
        picks = api(page, "/api/picks")["picks"]
        tagged = [p for p in picks if p["tags"]]
        assert tagged and all(p["tags"] == ["goodbye"] for p in tagged), [p["tags"] for p in picks]
        assert {p["clip"] for p in tagged} == {"CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"}
        assert all(p["start"] <= 5.0 < p["end"] for p in tagged)
        assert calls == []
    finally:
        inference.set_backend(None)
        page.locator("#story").fill("")
        page.evaluate("document.querySelector('#story').blur()")
        wait_edl(bin_server, project, lambda d: d.get("story") == "")


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


def test_an_absent_recogniser_hides_the_mic_before_any_hold(page, monkeypatch):
    """GET /api/themes carries `dictation`: the mic never appears when the server says
    the recogniser is absent — no hold, no 501."""
    from roughcut import dictate
    assert page.locator("#mic").is_visible()
    monkeypatch.setattr(dictate, "available", lambda: False)
    page.reload()
    page.wait_for_function("window.sheet && sheet.state.brief", timeout=15000)
    assert page.evaluate("sheet.state.dictation") is False
    assert page.locator("#mic").is_hidden()


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


# ------------------------------------------------------------ the cuts
#
# The same control the picker lives behind lists the bin's cuts and saves a copy of
# the one on the board (/switcher.js, shared with the pass and the board). The copy
# is deleted again at the end so the picker tests below see the bin as they expect.

def cut_rows(page) -> list[dict]:
    return page.evaluate("""() => Array.from(document.querySelectorAll('#cutList .crow')).map(r => ({
        name: r.querySelector('b').textContent, facts: r.querySelector('.n').textContent,
        flags: Array.from(r.querySelectorAll('.flag')).map(f => f.textContent),
        acts: Array.from(r.querySelectorAll('.act')).map(a => a.dataset.act),
        current: r.classList.contains('current')}))""")


def test_a_copy_of_the_cut_is_saved_from_the_header_and_the_page_moves_to_it(page, project):
    page.locator("#hdBin").click()
    open_picker(page)
    assert page.locator("#copyName").evaluate("el => document.activeElement === el")
    assert cut_rows(page) == [{"name": "main", "facts": "empty", "flags": [],
                               "acts": ["rename"], "current": True}]
    # the menu's first rows are the places (M16 decision 2): the footage, the pass, the cut
    assert page.locator("#placeList .place").count() == 3
    page.locator("#copyName").fill("try the river first")
    page.locator("#copyGo").click()
    page.wait_for_function(
        "document.querySelector('#toast').textContent.includes('saved a copy as try the river first')",
        timeout=10000)
    page.wait_for_function(
        "document.querySelector('#hdBin .cutname').textContent === 'try the river first'", timeout=10000)
    assert page.locator("#picker").is_hidden()
    assert api(page, "/api/status")["cut"] == "try the river first"
    copy_path = api(page, "/api/cuts")["current"]["path"]
    assert Path(copy_path).parent.name == project["footage"].name
    # the list has two now, the one on the board first, and the copy says where it came from
    page.keyboard.press("o")
    open_picker(page)
    rows = cut_rows(page)
    assert [r["name"] for r in rows] == ["try the river first", "main"]
    assert rows[0]["current"] and rows[0]["flags"][0] == "from main" and rows[0]["acts"] == ["rename"]
    assert rows[1]["acts"] == ["rename", "delete"]
    # back to main by its row (the one not on the board — "from main" is on the copy's
    # row too); then the copy to the trash (a confirm, accepted)
    page.locator("#cutList .crow:not(.current)").click()
    page.wait_for_function(
        "document.querySelector('#hdBin .cutname').textContent === 'main'", timeout=10000)
    assert api(page, "/api/status")["cut"] == "main"
    page.keyboard.press("o")
    open_picker(page)
    page.once("dialog", lambda d: d.accept())
    page.locator("#cutList .crow", has_text="try the river first").locator(".act[data-act=delete]").click()
    page.wait_for_function("document.querySelectorAll('#cutList .crow').length === 1", timeout=10000)
    assert not Path(copy_path).exists()
    assert (Path(copy_path).parent / "trash").is_dir()
    page.keyboard.press("Escape")
    assert page.locator("#picker").is_hidden()


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
    assert page.locator("#hdBin b").inner_text() == project["footage"].name
    assert page.locator("#hdBin .cutname").inner_text() == "main"     # the cut it is on
    assert page.locator("#picker").count() == 0 or page.locator("#picker").is_hidden()
    page.keyboard.press("o")                              # the key, outside any field
    open_picker(page)
    assert name.get_attribute("aria-expanded") == "true"
    table = picker_rows(page)
    assert table[0]["name"] == project["footage"].name and table[0]["current"]
    assert table[0]["clips"] == "3 clips" and table[0]["path"] == str(project["footage"])
    # a bin that is fine says nothing (M16: status only when something is wrong)
    assert table[0]["flags"] == ([] if api(page, "/api/index")["exists"] else ["new"])
    by = {r["name"]: r for r in table}
    assert by["picker-bin"] == {"name": "picker-bin", "clips": "1 clip", "flags": ["new"],
                                "path": str(other), "current": False}
    assert "sidecars" not in by, "a folder without video is not a bin"
    text = page.locator("#picker").inner_text()
    assert "no browsing dialog" not in text      # M16: the explanation went; the field says it
    assert "open a folder" in page.locator("#pickerPath").get_attribute("placeholder")
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
    assert page.locator("#hdBin b").inner_text() == project["footage"].name
    assert page.locator(".card").count() == 3
    assert api(page, "/api/status")["footage"] == str(project["footage"])


def test_opening_another_bin_reloads_the_whole_page_for_it(page, project, bin_server):
    """A row is POST /api/projects/open; on 200 the page reloads everything — header,
    sheet, the sentence, index — for the new bin, and the way back is a path typed in."""
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
            " && sheet.state.status && sheet.state.index && sheet.state.brief", timeout=10000)
        assert page.locator("#picker").is_hidden()
        page.wait_for_function("document.querySelector('#hdBin b').textContent === 'picker-bin'",
                               timeout=10000)
        assert page.locator("#hdBin .cutname").inner_text() == "main"
        # a new bin is setup again: not indexed yet, no badge, the price on the button
        assert page.locator("#headline").inner_text().startswith("1 clip · ")
        assert page.locator("#headline").inner_text().endswith("· not indexed yet")
        cards = page.locator(".card")
        assert cards.count() == 1 and cards.first.locator(".cap b").inner_text() == "GX01"
        assert page.locator(".badge").count() == 0                          # and there is no journal
        for sel in ("#how", "#howLine", "#progress", "#paused"):
            assert page.locator(sel).is_hidden(), sel
        assert page.locator("#story").input_value() == ""
        assert page.locator("#setup").is_visible()
        assert page.locator("#lookWord").inner_text().startswith("Looks at a frame every")
        assert page.locator("#indexBtn").inner_text().startswith("Index the footage · ~$")
        s = api(page, "/api/status")
        assert s["footage"] == str(other) and s["clips"] == 1
        page.wait_for_function("window.flowBar && flowBar.state()"
                               " && flowBar.state().stages[0].counts.clips === 1", timeout=10000)
        # and back, by its path typed into the field: the first bin's own EDL, not a new one
        page.keyboard.press("o")
        open_picker(page)
        # the way to the pass is the menu's place row now (M16: the step bar is gone)
        assert page.locator("#placeList .place[href='/floor']").count() == 1
        assert [r["name"] for r in picker_rows(page)][0] == "picker-bin"
        page.locator("#pickerPath").fill(str(project["footage"]))
        page.locator("#pickerPath").press("Enter")
        page.wait_for_function(
            f"document.querySelector('#toast').textContent === 'opened {project['footage'].name} · main'",
            timeout=10000)
        page.wait_for_function("sheet.state.clips && sheet.state.clips.clips.length === 3", timeout=10000)
        # the switcher repaints its name after the page has reloaded itself
        page.wait_for_function(
            f"document.querySelector('#hdBin b').textContent === {project['footage'].name!r}", timeout=10000)
        assert page.locator(".card").count() == 3
        assert api(page, "/api/status")["footage"] == str(project["footage"])
    finally:
        server.INDEXES.clear()
        server.configure(None, project["footage"], project["sidecars"], bin_server["work"],
                         proxies=False, visual=None)
