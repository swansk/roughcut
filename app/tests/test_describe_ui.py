"""DESCRIBE (wave 23), driven in Chromium: what the index found, readable; Find that names its clip,
opens it in the Source monitor and says "nothing like that" when there is nothing.

Copper's editors: "the transcript I could not find at all" (FINDER-02, -08), "a hit has no clip
name and the preview sits below the fold" (FINDER-01, -11), "a dog in the snow returns 13 matches"
(FINDER-12). The same fixture pattern as test_dock_ui: the real server on a real port, the
suite's three-clip bin, a fresh browser per test, no model call.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(project):
    import uvicorn
    import server

    vis = project["work"] / "visual-describe-ui"
    vis.mkdir(exist_ok=True)
    (vis / "CLIP_A.visual.json").write_text(json.dumps({
        "clip": "CLIP_A.MP4", "description": "Two friends say hello in a car park, one holds a milk jug.",
        "summary": "Two friends say hello in a car park, one holds a milk jug.",
        "moments": [{"start": 1.0, "end": 2.0, "what": "a man laughs", "kind": "reaction",
                     "notable": True, "confidence": "high", "frames": [1.0]}],
        "unusable": [], "params": {"prompt_version": 4}}), encoding="utf-8")
    original = project["edl"].read_text(encoding="utf-8")
    server.configure(project["edl"], project["footage"], project["sidecars"], project["work"],
                     proxies=False, visual=vis, assets=project["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in project["stems"]])
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    th.join(timeout=10)
    project["edl"].write_text(original, encoding="utf-8")
    for f in vis.glob("*"):
        f.unlink()


@pytest.fixture
def page(live_server, project):
    # the editor's own words are on disk; start every test without them
    for f in (project["work"] / "visual-describe-ui").glob("*.mine.json"):
        f.unlink()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(live_server)
        pg.wait_for_function("window.rcIndex && window.dock && window.tl", timeout=30000)
        yield pg
        assert errors == [], errors
        browser.close()


def _open_found(pg, clip="CLIP_A.MP4"):
    pg.evaluate("(c) => rcIndex.open([c])", clip)
    pg.wait_for_selector('[data-testid="index-transcript-line"]', timeout=15000)


def test_the_index_shows_what_it_heard_and_found(page):
    _open_found(page)
    lines = page.locator('[data-testid="index-transcript-line"]')
    assert lines.count() == 3
    assert "hello there" in lines.nth(0).inner_text() and lines.nth(0).inner_text().startswith("0:00")
    assert "goodbye" in lines.nth(2).inner_text() and lines.nth(2).inner_text().startswith("0:05")
    desc = page.locator('[data-testid="index-found-desc"]').inner_text()
    assert "milk jug" in desc
    mom = page.locator('[data-testid="index-found-moment"]')
    assert mom.count() == 1 and "a man laughs" in mom.first.inner_text()


def test_a_click_on_a_line_cues_the_clip_in_the_source_monitor(page):
    _open_found(page)
    page.locator('[data-testid="index-transcript-line"]').nth(2).click()
    page.wait_for_function("window.rcSource && rcSource.clip === 'CLIP_A.MP4'", timeout=8000)
    page.wait_for_function("document.querySelector('#srcVideo').readyState >= 1", timeout=15000)
    page.wait_for_function("Math.abs(document.querySelector('#srcVideo').currentTime - 5.0) < 0.3", timeout=8000)


def test_the_source_monitor_opens_its_clips_record(page):
    """No always-visible button (the board's word budget): the Clip menu and the monitor's
    right-click menu open the clip's transcript."""
    page.evaluate("dock.project.select('CLIP_B.MP4')")
    page.wait_for_function("window.rcSource && rcSource.clip === 'CLIP_B.MP4'", timeout=8000)
    assert page.locator("#srcFound").count() == 0
    page.locator('.menu[data-menu="clip"] .mtop').click()
    page.locator('[data-testid="clip-transcript"]').click()
    page.wait_for_selector('[data-testid="index-transcript-line"]', timeout=15000)
    assert page.locator('[data-testid="index-found"]').get_attribute("data-clip") == "CLIP_B.MP4"
    page.evaluate("dock.project.select('CLIP_C.MP4')")
    page.wait_for_function("window.rcSource && rcSource.clip === 'CLIP_C.MP4'", timeout=8000)
    page.locator("#srcScreen").click(button="right")
    page.locator('[data-testid="src-transcript"]').click()
    page.wait_for_function("document.querySelector('[data-testid=index-found]') && document.querySelector('[data-testid=index-found]').dataset.clip === 'CLIP_C.MP4'", timeout=15000)


def test_the_editor_can_correct_a_description_and_it_sticks(page, project):
    _open_found(page)
    page.locator('[data-testid="index-found-edit"]').click()
    ta = page.locator('[data-testid="index-found-text"]')
    ta.fill("A chairlift ride, the bar down.")
    page.locator('[data-testid="index-found-save"]').click()
    page.wait_for_function(
        "document.querySelector('[data-testid=index-found-desc]')"
        " && document.querySelector('[data-testid=index-found-desc]').innerText.includes('chairlift ride')", timeout=8000)
    assert "yours" in page.locator('[data-testid="index-found-desc"]').inner_text().lower()
    assert (project["work"] / "visual-describe-ui" / "CLIP_A.mine.json").exists()
    # the Project column reads the same words
    page.reload()
    page.wait_for_function("window.rcIndex && window.dock", timeout=30000)
    summary = page.evaluate("fetch('/api/project').then(r => r.json()).then(p => p.clips['CLIP_A.MP4'].visual.summary)")
    assert summary == "A chairlift ride, the bar down."


def _find(page, query):
    page.evaluate("dock.open('bin')")
    page.locator("#findQ").fill(query)
    page.locator("#findQ").press("Enter")


def test_a_find_hit_names_its_clip_and_opens_in_the_source_monitor(page):
    _find(page, "goodbye")
    page.wait_for_selector("#findResults .cand", timeout=15000)
    rows = page.locator("#findResults .cand")
    names = {rows.nth(i).locator('[data-testid="find-clip"]').inner_text() for i in range(rows.count())}
    assert names == {"CLIP_A", "CLIP_B", "CLIP_C"}
    rows.nth(1).click()
    clip = rows.nth(1).get_attribute("data-clip")
    page.wait_for_function("(c) => window.rcSource && rcSource.clip === c", arg=clip, timeout=8000)
    assert page.evaluate("[rcSource.inPoint, rcSource.outPoint]")[0] == pytest.approx(4.5, abs=0.1)
    page.wait_for_function("Math.abs(document.querySelector('#srcVideo').currentTime - 4.5) < 0.4", timeout=15000)
    # not a preview under the list: that player stays closed
    assert not page.locator("#findPlayer").is_visible()
    # and the chosen row carries its own add button
    assert rows.nth(1).locator('[data-testid="find-add"]').is_visible()


def test_a_find_with_nothing_above_the_floor_says_so(page):
    _find(page, "a porcupine in the snow")
    page.wait_for_selector('[data-testid="find-nothing"]', timeout=15000)
    assert "Nothing" in page.locator('[data-testid="find-nothing"]').inner_text()
    assert page.locator('[data-testid="find-nothing"]').count() == 1      # one line, not two
    assert page.locator("#findState").inner_text() == ""


def test_a_running_find_shows_that_it_is_running(page):
    page.evaluate("""() => {
        window.__find = fetch;
        window.fetch = (u, o) => String(u).includes('/api/find') && o && o.method === 'POST'
          ? new Promise((res) => setTimeout(() => window.__find(u, o).then(res), 2500))
          : window.__find(u, o);
    }""")
    _find(page, "goodbye")
    page.wait_for_selector('[data-testid="find-running"]', timeout=3000)
    txt = page.locator("#findState").inner_text()
    assert "searching" in txt and txt.rstrip().endswith("s")
    page.wait_for_selector("#findResults .cand", timeout=15000)
    assert page.locator('[data-testid="find-running"]').count() == 0


def test_a_selects_row_names_its_clip_in_a_narrow_panel(page):
    """FINDER-01: a pick read "Oh" or "Steady." with no clip name anywhere in the row (the Clip
    column needs 740 px; the Selects panel is narrower)."""
    out = page.evaluate("""() => fetch('/api/selects', {method: 'PUT',
        headers: {'content-type': 'application/json'},
        body: JSON.stringify({selects: [{clip: 'CLIP_A.MP4', start: 4.0, end: 5.5, why: 'the goodbye'}]})})
        .then(r => r.json())""")
    assert out.get("ok"), out
    page.reload()
    page.evaluate("dock.open('bin')")
    page.wait_for_selector("#library .keep")
    if page.locator("#library table.sel-list th", has_text="Clip").count() == 0:
        assert page.locator("#library .keep").first.locator('[data-testid="keep-clip"]').inner_text() == "CLIP_A"
    # the label the older tests read is untouched by it
    assert "CLIP_A" not in page.locator("#library .keep .w").first.inner_text()


def test_a_slow_answer_to_an_older_search_does_not_land_under_the_newer_question(page):
    """COLD-14: "a dog" was queued behind the GPU; its guess ("looks like “a dog”") appeared under
    the banner of the next search ("someone drinking from the milk jug")."""
    page.evaluate("""() => {
        window.__f = fetch;
        window.fetch = (u, o) => {
          if (String(u).includes('/api/find') && o && o.method === 'POST' && JSON.parse(o.body).query === 'a dog') {
            return new Promise((res) => setTimeout(() => window.__f(u, o).then(res), 4000));
          }
          return window.__f(u, o);
        };
    }""")
    _find(page, "a dog")
    page.wait_for_selector('[data-testid="find-running"]', timeout=3000)
    page.locator("#findQ").fill("goodbye")
    page.locator("#findQ").press("Enter")
    page.wait_for_selector("#findResults .cand", timeout=15000)
    page.wait_for_timeout(5000)                       # the dog answer lands now, and is dropped
    txt = page.locator("#findResults").inner_text()
    assert "goodbye" in txt.lower() and "dog" not in txt.lower()
    assert page.locator("#findResults .cand").count() >= 1
