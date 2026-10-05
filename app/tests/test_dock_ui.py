"""INTAKE M11 — the dock, driven in a real browser.

The board's right column is a dock: a rail of tools (Bin · Ask · Sound · FX · Film) and one
panel the height of the viewport that scrolls inside itself, so the page never scrolls
for it and the monitor never leaves. The Bin is the default tool and a labelled grid:
a card per keep with the pass's labels as chips, the same chips as a filter, the search
box filtering as you type, and adding at the playhead (the button, Enter, a drop). The
keys are an overlay on `?`, one table grouped by task (INTAKE M16); the project's facts
are the switcher's menu now, not a popover of the board's.

Same fixture pattern as test_ui_flow.py: the real uvicorn server on a real port, the
synthetic three-clip bin, the EDL re-seeded per test (two shots, 2 s each), a fresh
browser per test so the dock's remembered tool never leaks between tests. Skipped when
playwright is absent.
"""

from __future__ import annotations

import json
import re
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

def test_the_cli_banner_shows_above_the_board_and_the_dock_still_fits(page):
    """Karl, 2026-10-03: the CLI needing him must be obvious. The banner arrives after
    the dock has measured the header; the dock re-measures, so the page still never
    scrolls — and Check again clears it once the CLI is fine."""
    import server
    server.BACKEND.update(fix={"kind": "login", "title": "The Claude CLI needs you to sign in",
                               "command": "claude auth login", "why": "signed out",
                               "detail": "Not logged in"})
    try:
        page.evaluate("window.cliFix.poll()")
        page.wait_for_selector("#cliFix:not([hidden])")
        assert "claude auth login" in page.inner_text("#cliFix")
        # the banner is the one place it is said: no model pill in the header (M16 I16.1)
        assert page.locator("#backend").count() == 0
        page.wait_for_function(
            "document.querySelector('#dock').getBoundingClientRect().bottom <= 901")
        banner = rect(page, "#cliFix")
        assert banner["top"] < rect(page, "header")["top"], "the banner is above the header"
        assert rect(page, "#dock")["top"] >= rect(page, "header")["bottom"]
    finally:
        server.BACKEND.update(fix=None)
    page.evaluate("window.cliFix.poll()")
    page.wait_for_selector("#cliFix[hidden]", state="attached")


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
    assert page.locator("#rail .tool svg").count() == page.locator("#rail .tool").count() == 5
    assert [t.strip().lower() for t in page.locator("#rail .tool span").all_inner_texts()] == ["bin", "ask", "sound", "fx", "film"]


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


def test_the_ask_tool_is_the_one_sentence_and_the_change(page):
    """INTAKE M16 I16.4: one sentence about the film, in the same words everywhere —
    "What is this film about?" — and "Ask for a change" with what the cut aims for
    beside it. Gone: the Story heading and "The thing the agent is worst at…", the
    second button (Cut from the bin), and on an empty timeline "no cut yet — the
    board's empty state is where…" (the empty state is right there). On an empty
    timeline the sentence is the empty state's, asked once: the tool's own field steps
    aside for it, and one line says where it is (the tool is not left blank)."""
    page.evaluate("dock.open('ask')")
    assert page.locator("#askPanel").is_visible()
    tool = page.locator("#tools section[data-tool=ask]")
    assert "What is this film about?" in tool.inner_text()
    assert "worst at" not in tool.inner_text()
    assert tool.locator("button").count() == 1
    assert page.locator("#askAims").inner_text() == "aims for 0:05–0:20"   # target_s [5, 20]
    page.evaluate("tl.select([tl.idAt(0), tl.idAt(1)])")
    page.keyboard.press("x")
    page.wait_for_selector("#inspector .empty")
    assert not page.locator("#askPanel").is_visible()
    assert page.locator("#askEmpty").count() == 0
    assert not page.locator("#story").is_visible()   # the empty state's field asks it
    assert page.locator("#askFirst").inner_text() == \
        "No cut yet: the sentence and the first cut are under the timeline."
    # and the empty state's field is the same sentence: typing in one is the other
    page.locator("#firstNote").fill("two friends talking")
    assert page.evaluate("document.querySelector('#story').value") == "two friends talking"


# ---------------------------------------------------------------- the bin

def test_the_bin_is_a_labelled_grid_with_the_labels_as_a_filter(page):
    """INTAKE M16 I16.4: the bin opens on "not in the cut · N" (C10), two cards a row,
    each labelled by the note, else the line spoken in it, else what was seen — no clip
    name, times or chips on a card; "+ add" only on the card under the pointer or the
    selected one; the rail badge is the not-in-the-cut count. The evidence chips and
    the heard / seen tabs are under "more found ▸"."""
    _with_bin(page)
    assert "grid" in page.locator("#library").get_attribute("class")
    cards = page.locator("#library .keep")
    assert cards.count() == 2                      # CLIP_B's keep is shot 2: not here
    assert page.locator("#binTabs .tab").all_inner_texts() == ["not in the cut · 2", "all 3"]
    assert page.locator("#rail .tool[data-tool=bin] .badge").inner_text() == "2"
    # heroes first; the label is the line spoken inside the keep
    first = cards.first
    assert first.locator(".w").inner_text() == "★ “goodbye”"
    assert "1.5 s" in first.inner_text()
    assert "CLIP_A" not in first.inner_text() and first.locator(".chip").count() == 0
    assert first.get_attribute("title").startswith("CLIP_A 0:04.0–0:05.5")
    assert not first.locator("button.add").is_visible()
    first.hover()
    assert first.locator("button.add").is_visible()
    # the instruction paragraph and the Find button are gone; one box
    assert page.locator("#libHint").inner_text() == ""
    assert page.locator("#findGo").count() == 0
    # all: the keep in the cut says where it is
    page.locator("#binTabs .tab[data-tab=all]").click()
    assert cards.count() == 3
    assert "in the cut · shot 2" in page.locator('#library .keep[title^="CLIP_B"]').inner_text()
    # more found ▸: the labels as a filter, with counts; the tabs above already say where
    assert not page.locator("#binFilter").is_visible()
    page.locator("#moreFound").click()
    chips = [c.strip() for c in page.locator("#binFilter .chip").all_inner_texts()]
    assert chips == ["★ hero 1", "banter 2", "crash 2", "seen 1"]
    page.locator("#binFilter .chip", has_text="seen").click()
    assert cards.count() == 1
    page.locator("#binFilter .chip", has_text="seen").click()
    page.locator("#binFilter .chip", has_text="crash").click()
    assert cards.count() == 2
    assert "2 of 3 keeps match" in page.locator("#libHint").inner_text()
    page.locator("#binFilter .chip", has_text="crash").click()      # the same chip clears it
    assert cards.count() == 3
    # the box filters as you type
    page.locator("#findQ").fill("goodbye")
    page.wait_for_function("document.querySelectorAll('#library .keep').length === 1")
    assert page.locator("#library .keep").get_attribute("title").startswith("CLIP_A")
    page.locator("#findQ").fill("")
    page.wait_for_function("document.querySelectorAll('#library .keep').length === 3")
    # the heard tab is the old list, not a grid, and the filter row goes with the keeps
    page.locator("#libTabs .tab", has_text="heard").click()
    assert "grid" not in (page.locator("#library").get_attribute("class") or "")
    assert not page.locator("#binFilter").is_visible()
    # closing more found goes back to the keeps
    page.locator("#moreFound").click()
    assert "grid" in page.locator("#library").get_attribute("class")


def test_enter_finds_and_the_priced_search_sits_under_any_find(page):
    """One find box: Enter finds a moment anywhere; under the result, plain and priced,
    "Not it? Ask the model · ~$x" — hidden until a find has run."""
    assert page.locator("#findDeep").is_hidden()
    page.locator("#findQ").fill("goodbye")
    page.locator("#findQ").press("Enter")
    page.wait_for_selector("#findResults .cand", timeout=15000)
    deep = page.locator("#findDeep")
    assert deep.is_visible()
    assert deep.inner_text().startswith("Not it? Ask the model · ~$")
    assert "primary" not in (deep.get_attribute("class") or "")
    row = page.locator("#findResults .cand").first
    assert "goodbye" in row.inner_text() and "CLIP_" not in row.inner_text()


def test_a_find_says_one_count_and_its_rows_can_be_told_apart(page):
    """A find over the Bin: the same line heard in three clips read as three identical
    rows ("hello there · 1.5 s"), and under them the keeps said "0 of 1 keeps match" and
    "no keep matches — clear the chip or the words above" with no chip set. Each row now
    has the picture at its moment and where in its clip it is; an empty keep grid says it
    once, naming what is set."""
    _put_selects(page, KEEPS[:1])                          # the goodbye: no "hello" in it
    page.reload()
    page.wait_for_selector("#library .keep")
    page.locator("#findQ").fill("hello")                   # heard in all three clips
    page.locator("#findQ").press("Enter")
    page.wait_for_selector("#findResults .cand", timeout=15000)
    page.wait_for_function("document.querySelector('#library .hint')", timeout=5000)
    rows = page.locator("#findResults .cand")
    assert rows.count() == 3, rows.count()
    srcs = [rows.nth(i).locator("img.still").get_attribute("src") for i in range(3)]
    assert len(set(srcs)) == 3 and all("t=" in s for s in srcs), srcs     # a picture each
    assert re.fullmatch(r"at \d+:\d\d · \d+\.\d s", rows.first.locator(".t").inner_text())
    assert page.locator("#libHint").inner_text() == ""                 # not "0 of 2"
    assert page.locator("#library .hint").inner_text() == "no keep matches — clear the words above"
    page.evaluate("binChip = 'tag:crash'; renderLibrary()")
    assert page.locator("#library .hint").inner_text() == "no keep matches — clear the chip or the words above"
    page.locator("#findQ").fill("")
    page.evaluate("binChip = 'tag:nothing-has-this'; renderLibrary()")
    assert page.locator("#library .hint").inner_text() == "no keep matches — clear the chip"
    page.evaluate("binChip = null; renderLibrary()")


def test_adding_from_the_bin_lands_at_the_playhead(page):
    """Karl, 2026-09-20: what adding a keep does by default is insert at the playhead —
    the cut point nearest it, the lanes' own rule, one undo entry. The button, the
    Enter key and (in test_timeline_lanes) a drop all land the same way."""
    _with_bin(page)
    # the playhead at 1.2 s of film: the nearest cut is at 2.0, so before shot 2
    page.evaluate("tl.seek(1.2)")
    card = page.locator('#library .keep[title^="CLIP_C"]')
    card.hover()                                       # + add shows on the hovered card (M16)
    card.locator("button.add").click()
    assert shots(page) == [["CLIP_A.MP4", 1.0, 3.0], ["CLIP_C.MP4", 0.5, 2.0],
                           ["CLIP_B.MP4", 0.0, 2.0]]
    assert page.locator("#undo").get_attribute("title").startswith("undo: insert")
    # one evaluate: the autosave may re-key the new shot between two round trips
    assert page.evaluate("[...tl.state.sel][0] === segs[1].id") is True
    # the card left "not in the cut" at once, before any save landed; all says where it is
    assert page.locator('#library .keep[title^="CLIP_C"]').count() == 0
    page.locator("#binTabs .tab[data-tab=all]").click()
    assert "in the cut · shot 2" in page.locator('#library .keep[title^="CLIP_C"]').inner_text()
    page.locator("#binTabs .tab[data-tab=out]").click()
    assert page.locator("#total").inner_text() == "0:05.5"
    # near the end of the film the nearest cut is the end: it appends
    page.evaluate("tl.seek(5.3)")
    # (the save after the first add wrote shot 1 into the bin as a keep of its own, so
    # "CLIP_A" now names two cards — the hero is the goodbye)
    card = page.locator('#library .keep[title*="the goodbye"]')
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
    # the same rule for a heard row (under more found, M16)
    page.locator("#moreFound").click()
    page.locator("#libTabs .tab", has_text="heard").click()
    page.evaluate("tl.seek(0.3)")                      # nearest cut: the head of the film
    page.locator("#library .cand").first.click()
    assert page.evaluate("segs.length") == 3
    assert shots(page)[1] == ["CLIP_A.MP4", 1.0, 3.0]


def test_space_on_a_selected_keep_plays_it_in_the_bin_not_the_monitor(page):
    _with_bin(page)
    card = page.locator('#library .keep[title^="CLIP_C"]')
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
    # one table grouped by task (INTAKE M16 I16.4), the timeline's rows read from its
    # own table: no second section, no module tags, no row twice
    groups = page.eval_on_selector_all("#keyTable th", "els => els.map(e => e.textContent)")
    assert groups == ["Play", "Trim and cut", "Move around", "The bin"], groups
    assert not page.locator("#keysOverlay #tlKeys").is_visible()   # mounted, not shown
    text = page.locator("#keysOverlay").inner_text()
    assert "razor" in text                         # a row from window.tlKeys.KEYS
    for tag in ("· foundation", "· trim", "· edges"):
        assert tag not in text, tag
    rows = page.eval_on_selector_all(
        "#keyTable tr:not(:has(th))", "els => els.map(e => e.textContent.replace(/\\s+/g, ' '))")
    assert len(rows) == len(set(rows)), rows
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


def test_the_rail_names_its_tools_and_badges_only_what_waits(page):
    """INTAKE M16 I16.4: the rail reads Bin · Ask · Sound · FX · Film (the tool's key
    stays `out`, so #tool=out and the flow keep working), and a badge is only for what
    waits on Karl — the old Out badge counted every film ever made. The Project ▾
    popover is gone (its facts are the switcher's menu)."""
    names = page.eval_on_selector_all("#rail .tool", "els => els.map(e => e.textContent.trim())")
    assert names == ["Bin", "Ask", "Sound", "FX", "Film"], names
    assert page.locator("#rail .tool[data-tool=out] .badge").count() == 0
    assert page.locator("#projectBtn").count() == 0 and page.locator("#projectPop").count() == 0
    page.evaluate("location.hash = '#tool=out'")
    page.wait_for_function("dock.current() === 'out'", timeout=5000)
