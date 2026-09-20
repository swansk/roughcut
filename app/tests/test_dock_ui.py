"""INTAKE M11 — the dock, driven in a real browser.

The board's right column is a dock: a rail of tools (Bin · Ask · Sound · Out) and one
panel the height of the viewport that scrolls inside itself, so the page never scrolls
for it and the monitor never leaves. The Bin is the default tool and a labelled grid:
a card per keep with the pass's labels as chips, the same chips as a filter, the search
box filtering as you type, and adding at the playhead (the button, Enter, a drop). The
keys are an overlay on `?`; the project's facts are a popover.

Same fixture pattern as test_ui_flow.py: the real uvicorn server on a real port, the
synthetic three-clip bin, the EDL re-seeded per test (two shots, 2 s each), a fresh
browser per test so the dock's remembered tool never leaks between tests. Skipped when
playwright is absent.
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
def live_server(project):
    import uvicorn
    import server

    original = project["edl"].read_text(encoding="utf-8")
    server.configure(project["edl"], project["footage"], project["sidecars"],
                     project["work"], proxies=False, assets=project["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in project["stems"]])

    port = _free_port()
    config = uvicorn.Config(server.app, host="127.0.0.1", port=port,
                            log_level="error")
    srv = uvicorn.Server(config)
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
def page(live_server, project):
    seed = json.dumps({
        "variant": "T", "title": "test cut", "orient": "none", "story": "",
        "target_s": [5, 20],
        "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
                     {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}],
    }, indent=1)
    Path(project["edl"]).write_text(seed, encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        pg = browser.new_page(viewport={"width": 1280, "height": 900})
        pg.goto(live_server)
        pg.wait_for_selector("#tl .blk")
        yield pg
        browser.close()


def _put_selects(page, selects: list[dict]) -> dict:
    """The bin, written the way the pass writes it (PUT /api/selects)."""
    out = page.evaluate("""(selects) => fetch('/api/selects', {
        method: 'PUT', headers: {'content-type': 'application/json'},
        body: JSON.stringify({selects})}).then(r => r.json())""", selects)
    assert out.get("ok"), out
    return out


KEEPS = [
    {"clip": "CLIP_A.MP4", "start": 4.0, "end": 5.5, "hero": True, "why": "the goodbye",
     "tags": ["banter"]},
    {"clip": "CLIP_C.MP4", "start": 0.5, "end": 2.0, "why": "a crash", "tags": ["crash"],
     "witnesses": [{"kind": "seen", "text": "a crash", "at": 1.0}]},
    # already in the cut: shot 2 is CLIP_B 0–2
    {"clip": "CLIP_B.MP4", "start": 0.0, "end": 2.0, "why": "the reply",
     "tags": ["banter", "crash"]},
]


def _with_bin(page) -> None:
    _put_selects(page, KEEPS)
    page.reload()
    page.wait_for_selector("#library .keep")


def shots(page) -> list[list]:
    return page.evaluate("segs.map(s => [s.clip, s.in, s.out])")


def rect(page, sel: str) -> dict:
    return page.evaluate(
        f"(() => {{ const r = document.querySelector('{sel}').getBoundingClientRect();"
        f" return {{top: r.top, bottom: r.bottom, height: r.height}}; }})()")


# ---------------------------------------------------------------- the dock itself

def test_the_dock_fits_the_viewport_and_never_asks_the_page_to_scroll(page):
    """The old sidebar was ~2,900 px "sticky" beside a 900 px viewport. The dock is
    exactly the viewport's height under the header, whatever the header's height is,
    and its panel scrolls inside itself."""
    hd = rect(page, "header")
    d = rect(page, "#dock")
    assert d["top"] >= hd["bottom"], (d, hd)
    assert d["bottom"] <= 900 + 1, d
    assert d["height"] >= 320
    # the header's height reached the stylesheet
    assert page.evaluate(
        "getComputedStyle(document.documentElement).getPropertyValue('--hd').trim()"
    ) == f"{round(hd['height'])}px"          # offsetHeight rounds
    assert page.evaluate("getComputedStyle(document.querySelector('#tools')).overflowY") == "auto"


def test_the_dock_resizes_by_its_left_edge_and_the_timeline_refits(page):
    """Drag the dock's left edge: the dock takes the width, the main column and the
    timeline's view take the rest, the width is remembered; double-click resets."""
    w0 = page.evaluate("dock.width()")
    view0 = page.evaluate("document.querySelector('#tl .tl-view').getBoundingClientRect().width")
    hb = page.locator("#dockHandle").bounding_box()
    x = hb["x"] + hb["width"] / 2
    y = hb["y"] + 200
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x - 60, y, steps=4)
    page.mouse.move(x - 120, y, steps=4)
    page.mouse.up()
    w1 = page.evaluate("dock.width()")
    assert w1 == pytest.approx(w0 + 120, abs=3), (w0, w1)
    assert page.evaluate("document.querySelector('#dock').getBoundingClientRect().width") == pytest.approx(w1, abs=2)
    page.wait_for_function(
        f"document.querySelector('#tl .tl-view').getBoundingClientRect().width < {view0} - 100")
    assert page.locator("#tl .blk").count() == 2       # the timeline re-fit, nothing lost
    page.reload()
    page.wait_for_selector("#tl .blk")
    assert page.evaluate("dock.width()") == w1          # remembered
    page.locator("#dockHandle").dblclick()
    assert page.evaluate("dock.width()") == w0
    # the icons and labels are on the rail
    assert page.locator("#rail .tool svg").count() == 4
    assert [t.strip().lower() for t in page.locator("#rail .tool span").all_inner_texts()] == ["bin", "ask", "sound", "out"]


def test_the_rail_opens_one_tool_at_a_time_and_remembers_it(page):
    assert page.evaluate("dock.current()") == "bin"
    assert page.locator("#library").is_visible()
    assert not page.locator("#musicPanel").is_visible()
    page.locator("#rail .tool[data-tool=sound]").click()
    assert page.evaluate("dock.current()") == "sound"
    assert page.locator("#musicPanel").is_visible()
    assert not page.locator("#library").is_visible()
    assert "on" in page.locator("#rail .tool[data-tool=sound]").get_attribute("class")
    assert "on" not in page.locator("#rail .tool[data-tool=bin]").get_attribute("class")
    # the choice survives a reload in this browser
    page.reload()
    page.wait_for_selector("#tl .blk")
    assert page.evaluate("dock.current()") == "sound"
    # an unknown tool is refused, the open one stays
    assert page.evaluate("dock.open('effects')") is False
    assert page.evaluate("dock.current()") == "sound"
    # reveal() opens the tool holding an element in a closed tool
    assert page.evaluate("dock.reveal('#story')") is True
    assert page.evaluate("dock.current()") == "ask"
    assert page.locator("#askPanel").is_visible()
    assert page.locator("#story").is_visible()


def test_the_ask_tool_says_why_it_is_empty_on_an_empty_timeline(page):
    page.evaluate("dock.open('ask')")
    assert page.locator("#askPanel").is_visible()
    assert not page.locator("#askEmpty").is_visible()
    page.evaluate("tl.select([tl.idAt(0), tl.idAt(1)])")
    page.keyboard.press("x")
    page.wait_for_selector("#inspector .empty")
    assert not page.locator("#askPanel").is_visible()
    assert page.locator("#askEmpty").is_visible()
    assert page.locator("#story").is_visible()       # the story is still there to write


# ---------------------------------------------------------------- the bin

def test_the_bin_is_a_labelled_grid_with_the_labels_as_a_filter(page):
    _with_bin(page)
    assert "grid" in page.locator("#library").get_attribute("class")
    cards = page.locator("#library .keep")
    assert cards.count() == 3
    assert page.locator("#rail .tool[data-tool=bin] .badge").inner_text() == "3"
    # heroes first; the labels ride on the card
    first = cards.first
    assert "CLIP_A" in first.inner_text()
    assert [c.strip() for c in first.locator(".chips .chip").all_inner_texts()] == ["★ hero", "banter"]
    assert "+ add" in first.inner_text()
    assert "in the cut · shot 2" in page.locator("#library .keep", has_text="CLIP_B").inner_text()
    # the filter row carries every label the bin has, with counts
    chips = [c.strip() for c in page.locator("#binFilter .chip").all_inner_texts()]
    # tags first, then the kinds of evidence the keeps rest on, then where they are
    assert chips == ["★ hero 1", "banter 2", "crash 2", "seen 1", "in the cut 1", "not yet 2"]
    assert [c.strip() for c in page.locator("#library .keep", has_text="CLIP_C")
            .locator(".chips .chip").all_inner_texts()] == ["crash", "seen"]
    page.locator("#binFilter .chip", has_text="seen").click()
    assert cards.count() == 1
    page.locator("#binFilter .chip", has_text="seen").click()
    page.locator("#binFilter .chip", has_text="crash").click()
    assert cards.count() == 2
    assert "2 of 3 keeps match" in page.locator("#libHint").inner_text()
    assert "on" in page.locator("#binFilter .chip", has_text="crash").get_attribute("class")
    page.locator("#binFilter .chip", has_text="crash").click()      # the same chip clears it
    assert cards.count() == 3
    page.locator("#binFilter .chip", has_text="not yet").click()
    assert cards.count() == 2
    assert "CLIP_B" not in page.locator("#library").inner_text()
    # a chip on a card is the same filter
    page.locator("#library .keep", has_text="CLIP_C").locator(".chip", has_text="crash").click()
    assert cards.count() == 2
    assert [t.strip() for t in page.locator("#binFilter .chip.on").all_inner_texts()] == ["crash 2"]
    page.locator("#binFilter .chip.on").click()
    assert cards.count() == 3
    # the search box filters as you type; Find is one keypress further
    page.locator("#findQ").fill("goodbye")
    page.wait_for_function("document.querySelectorAll('#library .keep').length === 1")
    assert "CLIP_A" in page.locator("#library .keep").inner_text()
    page.locator("#findQ").fill("")
    page.wait_for_function("document.querySelectorAll('#library .keep').length === 3")
    # the heard tab is the old list, not a grid, and the filter row goes with the bin
    page.locator("#libTabs .tab", has_text="heard").click()
    assert "grid" not in (page.locator("#library").get_attribute("class") or "")
    assert not page.locator("#binFilter").is_visible()


def test_adding_from_the_bin_lands_at_the_playhead(page):
    """Karl, 2026-09-20: what adding a keep does by default is insert at the playhead —
    the cut point nearest it, the lanes' own rule, one undo entry. The button, the
    Enter key and (in test_timeline_lanes) a drop all land the same way."""
    _with_bin(page)
    # the playhead at 1.2 s of film: the nearest cut is at 2.0, so before shot 2
    page.evaluate("tl.seek(1.2)")
    page.locator("#library .keep", has_text="CLIP_C").locator("button.add").click()
    assert shots(page) == [["CLIP_A.MP4", 1.0, 3.0], ["CLIP_C.MP4", 0.5, 2.0],
                           ["CLIP_B.MP4", 0.0, 2.0]]
    assert page.locator("#undo").get_attribute("title").startswith("undo: insert")
    assert page.evaluate("[...tl.state.sel]") == [page.evaluate("segs[1].id")]
    # the card flipped to where it is in the cut, before any save landed
    assert "in the cut · shot 2" in page.locator("#library .keep", has_text="CLIP_C").inner_text()
    assert page.locator("#total").inner_text() == "0:05.5"
    # near the end of the film the nearest cut is the end: it appends
    page.evaluate("tl.seek(5.3)")
    # (the save after the first add wrote shot 1 into the bin as a keep of its own, so
    # "CLIP_A" now names two cards — the hero is the goodbye)
    card = page.locator("#library .keep", has_text="the goodbye")
    card.click()                                       # select
    assert "sel" in card.get_attribute("class")
    page.keyboard.press("Enter")                       # add it at the playhead
    assert shots(page)[-1] == ["CLIP_A.MP4", 4.0, 5.5]
    assert page.evaluate("segs.length") == 4
    # one undo each
    page.keyboard.press("Control+z")
    assert page.evaluate("segs.length") == 3
    page.keyboard.press("Control+z")
    assert page.evaluate("segs.length") == 2
    # the same rule for a heard row
    page.locator("#libTabs .tab", has_text="heard").click()
    page.evaluate("tl.seek(0.3)")                      # nearest cut: the head of the film
    page.locator("#library .cand").first.click()
    assert page.evaluate("segs.length") == 3
    assert shots(page)[1] == ["CLIP_A.MP4", 1.0, 3.0]


def test_space_on_a_selected_keep_plays_it_in_the_bin_not_the_monitor(page):
    _with_bin(page)
    card = page.locator("#library .keep", has_text="CLIP_C")
    card.click()
    assert not page.locator("#findPlayer").is_visible()
    page.keyboard.press(" ")
    assert page.locator("#findPlayer").is_visible()
    src = page.get_attribute("#findVideo", "src") or ""
    assert "CLIP_C" in src and "#t=0.50" in src, src
    assert page.evaluate("player.playing") is False      # the cut did not start
    assert "a crash" in page.locator("#findWhat").inner_text()
    # double-click does the same
    page.evaluate("document.querySelector('#findPlayer').style.display = 'none'")
    card.dblclick()
    assert page.locator("#findPlayer").is_visible()


# ---------------------------------------------------------------- keys and project

def test_the_keys_are_an_overlay_on_question_mark(page):
    assert not page.locator("#keysOverlay").is_visible()
    page.keyboard.press("?")
    assert page.locator("#keysOverlay").is_visible()
    # timeline-keys.js found the panel by its heading and put its own section in it
    assert page.locator("#keysOverlay #tlKeys").is_visible()
    assert "timeline" in page.locator("#keysOverlay").inner_text().lower()   # the h3 is uppercased by CSS
    page.keyboard.press("Escape")
    assert not page.locator("#keysOverlay").is_visible()
    assert page.evaluate("segs.length") == 2             # Esc closed the map, nothing else
    page.locator("#keysBtn").click()
    assert page.locator("#keysOverlay").is_visible()
    page.keyboard.press("?")
    assert not page.locator("#keysOverlay").is_visible()
    # not while typing
    page.locator("#findQ").focus()
    page.keyboard.type("?")
    assert not page.locator("#keysOverlay").is_visible()
    assert page.locator("#findQ").input_value() == "?"


def test_the_project_facts_are_a_popover(page):
    assert not page.locator("#projectPop").is_visible()
    page.locator("#projectBtn").click()
    assert page.locator("#projectPop").is_visible()
    assert "clip" in page.locator("#project").inner_text()
    page.keyboard.press("Escape")
    assert not page.locator("#projectPop").is_visible()
    page.locator("#projectBtn").click()
    page.locator("#total").click()                      # a click elsewhere closes it
    assert not page.locator("#projectPop").is_visible()
