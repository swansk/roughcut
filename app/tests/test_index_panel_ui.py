"""The Index panel, driven in Chromium (wave 17, Karl's K4; REDESIGN-GOAL rubric log
2026-10-19, bar (a)).

Karl, 2026-10-19: *"it is still not clear to me what indexing was run over the existing
footage, or how to re run / tune that"*. His answer: a docked panel in the workspace that
shows, per clip, which stages ran, when, at what look interval and cost, and re-runs or
re-tunes a clip, a selection or the bin, the price shown first.

Driven like an editor: Window › Index opens the panel; each clip's stages are read off
its row; one clip's look is re-tuned to 2 s and re-run (the price on the foot before Run,
the journal on disk and the row after); a selection's hearing is re-run; a slow run is
stopped from the panel. The bin is the suite's three clips, indexed beforehand with every
tool stubbed (test_index), and every model call scripted — none is real.
"""

from __future__ import annotations

import json
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import inference, journal  # noqa: E402
from test_firstpass import Scripted  # noqa: E402
from test_index import _stub_tools  # noqa: E402
from test_index_panel import stub_hearer, stub_looker  # noqa: E402

PANEL = '[data-testid="index-panel"]'
ROW = '[data-testid="index-row"]'
PRICE = '[data-testid="index-price"]'
RUN = '[data-testid="index-rerun"]'
IV = '[data-testid="index-interval"]'
WHAT = '[data-testid="index-what"]'
STOP = '[data-testid="index-stop"]'


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live(tmp_path, project, monkeypatch):
    """A bin indexed end to end (stubbed tools), served for real on its own copy of the
    sidecars; the looker and the hearer record what they were asked."""
    import uvicorn
    import server

    model = Scripted()
    inference.set_backend(model)
    monkeypatch.setenv("ROUGHCUT_BACKEND", "claude_cli")
    side = tmp_path / "sidecars"
    shutil.copytree(project["sidecars"], side)
    server.configure(None, project["footage"], side, tmp_path, proxies=False, visual=None)
    _stub_tools(server, monkeypatch, tmp_path)
    calls = {"looks": [], "heard": []}
    stub_looker(server, monkeypatch, tmp_path, calls["looks"], sleep=0.3)
    stub_hearer(server, monkeypatch, tmp_path, calls["heard"])
    # CLIP_B as a ten-minute clip to the pricing (its file is six seconds): five sheets
    # at 4 s, ten at 2 s, so the price moves with the interval the way it does on real
    # footage
    server.STATE.setdefault("durations", {})["CLIP_B.MP4"] = 600.0
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started
    base = f"http://127.0.0.1:{port}"
    job = httpx.post(base + "/api/index", json={}, timeout=20).json()["job"]
    deadline = time.time() + 60
    while httpx.get(f"{base}/api/job/{job}", timeout=20).json()["state"] == "running":
        assert time.time() < deadline, "the fixture's index did not finish"
        time.sleep(0.1)
    calls["looks"].clear()
    calls["heard"].clear()
    yield {"url": base, "server": server, "calls": calls, "monkeypatch": monkeypatch,
           "tmp": tmp_path}
    try:
        httpx.post(base + "/api/index/cancel", timeout=20)
    except httpx.HTTPError:
        pass
    srv.should_exit = True
    th.join(timeout=10)
    inference.set_backend(None)


@pytest.fixture
def page(live):
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(live["url"] + "/")
        pg.wait_for_function("window.rcIndex && window.dock && window.tl", timeout=30000)
        yield pg
        assert errors == [], errors
        b.close()


def _journal(live) -> journal.Journal:
    return journal.Journal.load(live["server"].journal_path())


def _states(pg, clip: str) -> dict:
    return pg.evaluate(
        "(c) => Object.fromEntries([...document.querySelectorAll("
        "`[data-testid=index-row][data-clip=\"${c}\"] [data-testid=index-stage]`)]"
        ".map(td => [td.dataset.stage, td.dataset.state]))", clip)


def _cell(pg, clip: str, stage: str):
    return pg.locator(f'{ROW}[data-clip="{clip}"] [data-testid=index-stage][data-stage="{stage}"]')


def _wait_cell(pg, clip, stage, state, timeout=30000):
    pg.wait_for_function(
        "([c, s, st]) => { const td = document.querySelector("
        "`[data-testid=index-row][data-clip=\"${c}\"] [data-testid=index-stage][data-stage=\"${s}\"]`);"
        " return td && td.dataset.state === st; }", arg=[clip, stage, state], timeout=timeout)


def _wait_idle(live, timeout=60):
    deadline = time.time() + timeout
    while httpx.get(live["url"] + "/api/index", timeout=20).json()["running"]:
        assert time.time() < deadline, "the re-run did not end"
        time.sleep(0.1)


def _open_from_the_menu(pg):
    pg.locator("#menubar .mtop", has_text="Window").click()
    pg.locator('#menubar button[data-cmd="w:index"]').click()
    pg.wait_for_selector(PANEL, state="visible", timeout=5000)
    pg.wait_for_function("document.querySelectorAll('[data-testid=index-row]').length === 3",
                         timeout=15000)


def test_the_panel_shows_what_ran_retunes_a_clip_and_reruns_a_selection(page, live):
    pg, server, calls = page, live["server"], live["calls"]
    _open_from_the_menu(pg)
    assert pg.evaluate("dock.current()") == "index"
    assert pg.locator('.ws-tab[data-tool=index]').is_visible()

    # what ran on each clip, read off its row: every stage, as the journal says
    j = _journal(live)
    for clip in ("CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"):
        st = _states(pg, clip)
        assert st == {"asr": "done", "look": "done", "close": "done", "telemetry": "skipped",
                      "proxy": "done"}, (clip, st)
        assert j.state(clip, "telemetry") == "skipped"
        # the look's interval is in its cell, when it ran in its hover
        assert _cell(pg, clip, "look").inner_text().strip() == "✓ 4 s"
        assert "done" in _cell(pg, clip, "look").get_attribute("title")
    summary = pg.locator('[data-testid="index-summary"]').inner_text()
    assert summary.startswith("3 clips · heard 3 · looked 3 at 4 s · close looks 3"), summary
    assert summary.endswith("free on your plan"), summary

    # re-tune CLIP_B's look: select it, Look, every 2 s — the price shows before Run
    pg.locator(f'{ROW}[data-clip="CLIP_B.MP4"]').click()
    pg.wait_for_function("rcIndex.state().selection.join() === 'CLIP_B.MP4'")
    assert pg.locator('[data-testid="index-detail"]').inner_text().startswith("CLIP_B")
    pg.locator(WHAT).select_option("look")
    unit = server.VISUAL_USD_PER_SHEET
    pg.wait_for_function("(u) => document.querySelector('[data-testid=index-price]').dataset.usd"
                         " === String(Math.round(5 * u * 100) / 100)", arg=unit, timeout=5000)
    pg.locator(IV).select_option("2")
    want = httpx.post(live["url"] + "/api/index/price", timeout=20, json={
        "clips": ["CLIP_B.MP4"], "stages": ["look"], "interval_s": 2}).json()["usd"]
    assert want == pytest.approx(10 * unit, abs=0.01)
    pg.wait_for_function("(u) => { const p = document.querySelector('[data-testid=index-price]');"
                         " return p.dataset.usd === String(u) && p.dataset.local === ''; }",
                         arg=want, timeout=5000)
    price = pg.locator(PRICE)
    assert price.inner_text() == "free on your plan"          # section S, on the plan
    assert f"${want:.2f}" in price.get_attribute("title")     # the API estimate in the hover
    assert calls["looks"] == [], "nothing runs before Run"
    before = _journal(live).clips["CLIP_B.MP4"]["stages"]["look"]["finished"]
    pg.locator(RUN).click()
    # it runs, in the row and in the header's job row
    pg.wait_for_function(
        "() => { const td = document.querySelector('[data-testid=index-row][data-clip=\"CLIP_B.MP4\"]"
        " [data-stage=look]'); return td && ['queued', 'running'].includes(td.dataset.state); }",
        timeout=10000)
    pg.wait_for_selector('#progress .job[data-kind="index"]', timeout=10000)
    _wait_cell(pg, "CLIP_B.MP4", "look", "done")
    pg.wait_for_function("() => document.querySelector('[data-testid=index-row][data-clip=\"CLIP_B.MP4\"]"
                         " [data-stage=look]').textContent.trim() === '✓ 2 s'", timeout=15000)
    _wait_idle(live)
    assert calls["looks"] == [{"only": ["CLIP_B"], "force": True, "interval": 2.0}]
    j = _journal(live)
    lb = j.clips["CLIP_B.MP4"]["stages"]["look"]
    assert lb["state"] == "done" and lb["finished"] > before and lb["force"] is False
    assert server.read_edl()["look"]["clips"] == {"CLIP_B.MP4": 2.0}
    assert json.loads((server.STATE["visual"] / "CLIP_B.visual.json").read_text())["params"]["interval_s"] == 2.0
    assert _cell(pg, "CLIP_A.MP4", "look").inner_text().strip() == "✓ 4 s", "only CLIP_B"
    assert pg.locator(f'{ROW}[data-clip="CLIP_B.MP4"] .tuned').inner_text() == "2 s"
    pg.wait_for_function("document.querySelector('[data-testid=index-summary]').textContent"
                         ".includes('looked 3 (2 at 4 s, 1 at 2 s)')", timeout=10000)

    # re-run a selection's hearing: CLIP_A, then Ctrl-click CLIP_C
    before = {c: _journal(live).clips[c]["stages"]["asr"]["finished"] for c in ("CLIP_A.MP4", "CLIP_C.MP4")}
    b_heard = _journal(live).clips["CLIP_B.MP4"]["stages"]["asr"]
    pg.locator(f'{ROW}[data-clip="CLIP_A.MP4"]').click()
    pg.locator(f'{ROW}[data-clip="CLIP_C.MP4"]').click(modifiers=["Control"])
    pg.wait_for_function("rcIndex.state().selection.sort().join() === 'CLIP_A.MP4,CLIP_C.MP4'")
    pg.locator(WHAT).select_option("asr")
    assert not pg.locator(IV).is_visible(), "the interval goes with a look"
    pg.wait_for_function("document.querySelector('[data-testid=index-price]').dataset.usd === '0'"
                         " && document.querySelector('[data-testid=index-price]').dataset.local === ''",
                         timeout=5000)
    assert pg.locator(PRICE).inner_text() == "free"
    assert pg.locator('[data-testid="index-scope"]').inner_text() == "2 clips"
    pg.locator(RUN).click()
    pg.wait_for_function("rcIndex.state().running === true || "
                         "document.querySelectorAll('[data-testid=index-row] [data-stage=asr][data-state=done]').length === 3",
                         timeout=10000)
    _wait_idle(live)
    pg.evaluate("rcIndex.refresh()")
    _wait_cell(pg, "CLIP_A.MP4", "asr", "done")
    assert calls["heard"] == [{"stems": ["CLIP_A", "CLIP_C"], "force": True}]
    assert calls["looks"] == [{"only": ["CLIP_B"], "force": True, "interval": 2.0}], "no look bought"
    j = _journal(live)
    for c in ("CLIP_A.MP4", "CLIP_C.MP4"):
        assert j.clips[c]["stages"]["asr"]["finished"] > before[c]
    assert j.clips["CLIP_B.MP4"]["stages"]["asr"] == b_heard

    # the old open screen's "Index what isn't done", for the whole bin: all of it is done
    pg.evaluate("rcIndex.select([])")
    pg.locator(WHAT).select_option("missing")
    assert pg.locator('[data-testid="index-scope"]').inner_text() == "all 3 clips"
    pg.wait_for_function("document.querySelector('[data-testid=index-price]').textContent === 'nothing to run'",
                         timeout=5000)
    assert pg.locator(RUN).is_disabled()


def test_a_run_is_stopped_from_the_panel(page, live):
    pg, server = page, live["server"]
    slow: list = []
    stub_looker(server, live["monkeypatch"], live["tmp"], slow, sleep=60)
    pg.evaluate("rcIndex.open(['CLIP_A.MP4'])")
    pg.wait_for_selector(PANEL, state="visible")
    pg.wait_for_function("rcIndex.state().selection.join() === 'CLIP_A.MP4' && "
                         "document.querySelectorAll('[data-testid=index-row]').length === 3")
    pg.locator(WHAT).select_option("look")
    pg.locator(IV).select_option("1")
    pg.wait_for_function("document.querySelector('[data-testid=index-price]').dataset.local === ''")
    pg.locator(RUN).click()
    _wait_cell(pg, "CLIP_A.MP4", "look", "running")
    stop = pg.locator(STOP)
    assert stop.is_visible()
    # the header's job row has it too, with its own Stop
    pg.wait_for_selector('#progress .job[data-kind="index"]', timeout=10000)
    t0 = time.time()
    stop.click()
    pg.wait_for_function("rcIndex.state().running === false", timeout=15000)
    assert time.time() - t0 < 10
    _wait_cell(pg, "CLIP_A.MP4", "look", "done")              # the old look stands
    cell = _cell(pg, "CLIP_A.MP4", "look")
    assert cell.inner_text().strip() == "✓ 4 s ↻"
    assert "asked again" in cell.get_attribute("title")
    assert not stop.is_visible()
    j = _journal(live)
    st = j.clips["CLIP_A.MP4"]["stages"]["look"]
    assert st["state"] == "queued" and st["force"] is True, st
    assert json.loads((server.STATE["visual"] / "CLIP_A.visual.json").read_text())["params"]["interval_s"] == 4.0
    jobs = httpx.get(live["url"] + "/api/jobs", timeout=20).json()["jobs"]
    assert not any(x["kind"] == "index" and x["state"] == "running" for x in jobs)


# ------------------------------------------------------------------ taught and tied in (wave 18)

CARD_JS = """() => { const c = document.querySelector('[data-testid="ai-hover"]');
  if (!c || c.hidden) return null;
  return {for: c.dataset.for, name: c.querySelector('.hc-name').textContent,
          what: c.querySelector('.hc-what').textContent,
          cost: c.querySelector('[data-testid="ai-hover-cost"]').textContent,
          change: c.querySelector('[data-testid="ai-hover-change"]').textContent}; }"""


def _hover_card(pg, sel):
    pg.mouse.move(2, 890)
    pg.wait_for_timeout(450)                           # past the warm-up window
    pg.locator(sel).hover(force=True)
    pg.wait_for_function("(() => { const c = document.querySelector('[data-testid=ai-hover]');"
                         " return c && !c.hidden; })()", timeout=3000)
    return pg.evaluate(CARD_JS)


def test_run_has_the_ai_hover_card_what_it_replaces_and_its_price(page, live):
    """Verdict 14, punch 6: Run is a model call when it looks; it had a plain title. Its AI
    hover card says what it runs, what it costs on this backend (free on the plan with the
    API's estimate; the price itself on the API) and what it changes: the clip's looked-at
    moments replaced, its picks re-ranked, its close looks kept from the old look."""
    pg, server = page, live["server"]
    pg.evaluate("rcIndex.open(['CLIP_B.MP4'])")
    pg.wait_for_selector(PANEL, state="visible")
    pg.wait_for_function("rcIndex.state().selection.join() === 'CLIP_B.MP4' && "
                         "document.querySelectorAll('[data-testid=index-row]').length === 3")
    pg.locator(WHAT).select_option("look")
    pg.locator(IV).select_option("2")
    want = httpx.post(live["url"] + "/api/index/price", timeout=20, json={
        "clips": ["CLIP_B.MP4"], "stages": ["look"], "interval_s": 2}).json()["usd"]
    pg.wait_for_function("(u) => { const p = document.querySelector('[data-testid=index-price]');"
                         " return p.dataset.usd === String(u) && p.dataset.local === ''; }",
                         arg=want, timeout=5000)
    card = _hover_card(pg, RUN)
    assert card["for"] == "index-rerun", card
    assert card["name"] == "Look again — CLIP_B", card
    assert "one frame every 2 s (10 sheets)" in card["what"], card
    assert card["cost"] == f"free on your Claude plan — about ${want:.2f} estimated cost if using the API", card
    assert card["change"].startswith("Replaces CLIP_B’s looked-at moments (the 4 s look → 2 s); its picks are"
                                     " re-ranked; its close looks are kept from the old look"), card
    assert "The cut does not change" in card["change"], card
    # a free stage: no model call
    pg.locator(WHAT).select_option("asr")
    pg.wait_for_function("document.querySelector('[data-testid=index-price]').dataset.usd === '0'"
                         " && document.querySelector('[data-testid=index-price]').dataset.local === ''",
                         timeout=5000)
    card = _hover_card(pg, RUN)
    assert card["name"] == "Hear again — CLIP_B" and card["cost"] == "none — no model call", card
    assert card["change"].startswith("Replaces CLIP_B’s transcript; its picks are re-ranked."), card
    # on the API: the price itself, on the foot and in the card, before anything runs
    live["monkeypatch"].setenv("ROUGHCUT_BACKEND", "anthropic_api")
    pg.evaluate("rcIndex.refresh()")
    pg.wait_for_function("rcIndex.state().selection.join() === 'CLIP_B.MP4'")
    pg.locator(WHAT).select_option("look")
    pg.locator(IV).select_option("2")
    pg.wait_for_function("(u) => { const p = document.querySelector('[data-testid=index-price]');"
                         " return p.dataset.usd === String(u) && p.dataset.local === '' && p.textContent.startsWith('~$'); }",
                         arg=want, timeout=8000)
    assert pg.locator(PRICE).inner_text() == f"~${want:.2f}"
    card = _hover_card(pg, RUN)
    assert card["cost"] == f"about ${want:.2f}, billed to the API key", card
    assert live["calls"]["looks"] == [], "a hover runs nothing"


def test_the_first_pass_panels_index_step_opens_the_index_panel(page, live):
    """Verdict 14, punch 6: the first-pass panel's Index step did not lead to the panel that
    shows it. It links "each clip in the Index panel": the first-pass panel closes and the
    Index panel opens in the Project group."""
    pg = page
    pg.evaluate("dock.open('project')")
    pg.evaluate("rcFirstPass.open()")
    link = pg.locator('[data-testid="firstpass-index-link"]')
    link.wait_for(state="visible", timeout=10000)
    assert link.inner_text() == "each clip in the Index panel"
    assert pg.locator('[data-testid="firstpass-panel"] li[data-step=index] [data-testid="firstpass-index-link"]').count() == 1
    link.click()
    pg.wait_for_selector(PANEL, state="visible", timeout=5000)
    assert pg.evaluate("dock.current()") == "index"
    assert pg.locator('[data-testid="firstpass-panel"]').is_hidden()
    pg.wait_for_function("document.querySelectorAll('[data-testid=index-row]').length === 3", timeout=10000)


def test_a_fresh_browsers_walk_through_points_at_the_index_tab(live, first_run_context):
    """Verdict 14, punch 6: no step of the walk-through mentioned the Index panel (Karl's K4:
    "not clear what indexing was run … or how to re run / tune that"). On a first visit the
    first-pass step says where it is, and its link opens it with the walk-through still up;
    the AI half's "one menu" step names the index's new look."""
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = first_run_context(b, viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(live["url"] + "/")
        pg.wait_for_selector('[data-testid="tour"]', timeout=20000)
        pg.locator('[data-testid="tour-next"]').click()
        pg.wait_for_function("document.querySelector('[data-testid=tour]').dataset.key === 'firstpass'")
        body = pg.locator('[data-testid="tour"] .rt-body').inner_text()
        assert "Index tab" in body and "what was heard and looked at on each clip" in body, body
        assert "re-tune a clip" in body, body
        pg.evaluate("dock.open('project')")
        pg.locator('[data-testid="tour-index-link"]').click()
        pg.wait_for_selector(PANEL, state="visible", timeout=5000)
        assert pg.evaluate("dock.current()") == "index"
        assert pg.evaluate("rcTour.active") is True and pg.locator('[data-testid="tour"]').is_visible()
        pg.locator('[data-testid="tour-skip"]').click()
        pg.evaluate("rcTour.start('ai')")
        for _ in range(2):
            pg.locator('[data-testid="tour-next"]').click()
        pg.wait_for_function("document.querySelector('[data-testid=tour]').dataset.key === 'menu'")
        assert "a new look at the index" in pg.locator('[data-testid="tour"] .rt-body').inner_text()
        assert errors == [], errors
        b.close()
