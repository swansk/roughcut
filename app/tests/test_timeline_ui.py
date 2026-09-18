"""INTAKE M9 I9.1 — the promoted timeline's foundation, driven in a real browser.

The module under test is `app/static/timeline.js` (`window.tl`): the ruler and its
global time scale, zoom and fit, blocks keyed by the server's ids, selection by click /
⇧-click / ⌘-click, the scrub that cues the monitor, and the undo / redo stack and edit
API the other lanes (I9.2–I9.4) build on. The API is exercised the way those lanes will
call it — `tl.begin` / `tl.setRange` / `tl.commit`, `tl.split`, `tl.move`,
`tl.snapsFor` — and the assertions are made against the EDL on disk, the monitor's
element and app.js's own `segs` / `sel`, not against the module's word for it.

Same fixture pattern as test_ui_flow.py: the real uvicorn server on a real port, the
synthetic three-clip bin, the EDL re-seeded per test (two shots, 2 s each). Skipped when
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


def ids(page) -> list[str]:
    return page.evaluate("segs.map(s => s.id)")


def block_ids(page) -> list[str]:
    return page.evaluate(
        "[...document.querySelectorAll('#tl .blk')].map(b => b.dataset.id)")


def wait_saved(page) -> None:
    page.wait_for_function(
        "document.querySelector('#saveState').textContent.startsWith('saved')",
        timeout=8000)


def on_disk(project) -> list[dict]:
    return json.loads(Path(project["edl"]).read_text(encoding="utf-8"))["segments"]


# ------------------------------------------------------------------ the picture

def test_the_ruler_and_the_total_match_the_seed_edl(page):
    """Two shots of 2 s: the film is 0:04.0 on the ruler's end mark, on the transport,
    and the ruler's labels count the seconds at the fit zoom."""
    assert page.locator("#tl .tl-total").inner_text() == "0:04.0"
    assert page.locator("#posTotal").inner_text() == "0:04.0"
    labels = page.evaluate(
        "[...document.querySelectorAll('#tl .tl-ruler .major label')].map(l => l.textContent)")
    assert labels[:5] == ["0:00", "0:01", "0:02", "0:03", "0:04"], labels
    # the end mark sits at 4 s of film
    assert page.evaluate("parseFloat(document.querySelector('#tl .tl-end').style.left)") \
        == pytest.approx(page.evaluate("tl.timeToX(4)"), abs=0.5)
    # the seed's first shot opens mid-sentence and cuts a line off: a mark on each edge
    assert page.locator("#tl .blk").nth(0).locator(".warn:not([hidden])").count() == 2
    assert page.locator("#tl .blk").nth(1).locator(".warn:not([hidden])").count() == 0


def test_blocks_carry_the_servers_ids_and_fit_the_view(page):
    """`data-id` is the server's id, never an index; at the fit zoom the two 2 s shots are
    the same width and together fill the viewport. (The fixture re-seeds the EDL without
    ids after the server opened it, so the ids are the ones the page was handed at boot
    and the first save persists — the board's own ids, not a re-read's fresh mint.)"""
    page_ids = ids(page)
    assert all(i.startswith("g") and len(i) == 11 for i in page_ids), page_ids
    assert block_ids(page) == page_ids
    page.evaluate("touch()")
    wait_saved(page)
    server_ids = [s["id"] for s in
                  page.evaluate("fetch('/api/project').then(r => r.json())")["segments"]]
    assert server_ids == page_ids
    widths = page.evaluate(
        "[...document.querySelectorAll('#tl .blk')].map(b => b.getBoundingClientRect().width)")
    assert widths[0] == pytest.approx(widths[1], abs=1)
    view = page.evaluate("document.querySelector('#tl .tl-view').clientWidth")
    assert sum(widths) == pytest.approx(view - 60, abs=2)   # 12 px before 0:00, 48 after the end
    assert page.evaluate("tl.state.zoom") == pytest.approx((view - 60) / 4, rel=0.01)
    assert page.evaluate("tl.byId(segs[1].id).clip") == "CLIP_B.MP4"
    assert page.evaluate("tl.indexOf(segs[1].id)") == 1
    assert page.evaluate("tl.filmStart(segs[1].id)") == 2.0


def test_plus_doubles_the_zoom_and_backslash_fits(page):
    z0 = page.evaluate("tl.state.zoom")
    page.keyboard.press("+")
    assert page.evaluate("tl.state.zoom") == pytest.approx(2 * z0, rel=0.01)
    assert page.evaluate("document.querySelector('#tl .tl-canvas').offsetWidth") \
        > page.evaluate("document.querySelector('#tl .tl-view').clientWidth")
    page.keyboard.press("-")
    assert page.evaluate("tl.state.zoom") == pytest.approx(z0, rel=0.01)
    page.keyboard.press("+")
    page.keyboard.press("+")
    page.keyboard.press("\\")
    assert page.evaluate("tl.state.zoom") == pytest.approx(z0, rel=0.01)
    assert page.evaluate("document.querySelector('#tl .tl-view').scrollLeft") == 0
    # and the API says the same thing the keys do
    assert page.evaluate("tl.zoomTo(100)") == 100
    assert page.evaluate("tl.fit()") == pytest.approx(z0, rel=0.01)


# ------------------------------------------------------------------ selection

def test_a_click_on_a_block_selects_it_and_its_card_and_the_card_selects_the_block(page):
    page.locator("#tl .blk").nth(1).click()
    page.evaluate("pauseCut()")                 # the click also plays from there
    second = ids(page)[1]
    assert page.locator("#tl .blk.sel").count() == 1
    assert page.locator("#tl .blk.sel").get_attribute("data-id") == second
    assert page.evaluate("[...tl.state.sel]") == [second]
    assert page.evaluate("sel") == 1, "app.js's index follows the timeline's id"
    assert page.locator(".seg.sel .clip").inner_text() == "CLIP_B"
    # the other way: a card click selects the block
    page.locator(".seg").first.locator(".clip").click()
    assert page.evaluate("sel") == 0
    assert page.locator("#tl .blk.sel").get_attribute("data-id") == ids(page)[0]
    assert page.evaluate("tl.state.anchor") == ids(page)[0]


def test_shift_click_selects_the_range_and_cmd_click_toggles(page):
    page.locator("#library .cand").first.click()        # a third shot, after the first
    assert page.locator("#tl .blk").count() == 3
    blocks = page.locator("#tl .blk")
    blocks.nth(0).click()
    page.evaluate("pauseCut()")
    blocks.nth(2).click(modifiers=["Shift"])
    assert page.locator("#tl .blk.sel").count() == 3
    assert page.evaluate("tl.state.sel.size") == 3
    assert not page.evaluate("player.playing"), "a ⇧-click selects, it does not play"
    blocks.nth(1).click(modifiers=["Control"])          # toggle the middle one out
    assert page.locator("#tl .blk.sel").count() == 2
    assert page.evaluate("[...tl.state.sel].sort()") == sorted([ids(page)[0], ids(page)[2]])
    # a click on the empty ruler clears the selection, on the timeline and the cards
    page.locator("#tl .tl-ruler").click(position={"x": 4, "y": 6})
    assert page.locator("#tl .blk.sel").count() == 0
    assert page.evaluate("tl.state.anchor") is None
    assert page.locator(".seg.sel").count() == 0


# ------------------------------------------------------------------ scrub and playhead

def test_a_click_on_the_ruler_cues_the_monitor_paused_at_that_film_time(page):
    """3 s of film is 1 s into shot 2 (CLIP_B 0.0–2.0): the monitor parks there, paused,
    the playhead sits at 3 s, and space resumes from that point rather than the top."""
    x = page.evaluate("tl.timeToX(3.0)")
    page.locator("#tl .tl-ruler").click(position={"x": x, "y": 8})
    assert page.evaluate("player.idx") == 1
    assert not page.evaluate("player.playing")
    assert page.evaluate("tl.state.playhead") == pytest.approx(3.0, abs=0.02)
    assert page.locator("#pos").inner_text() == "0:03.0"
    assert page.evaluate("document.querySelector('.screen video.live').id") == \
        page.evaluate("liveVideo().id")
    page.wait_for_function(
        "Math.abs(document.querySelector('.screen video.live').currentTime - 1.0) < 0.1",
        timeout=10000)
    head = page.evaluate("parseFloat(document.querySelector('#tl .tl-head').style.left)")
    assert head == pytest.approx(x, abs=1.5)
    page.keyboard.press("Space")
    page.wait_for_function("player.playing && player.idx === 1", timeout=10000)
    page.wait_for_function(
        "document.querySelector('.screen video.live').currentTime > 1.05", timeout=10000)
    assert page.evaluate("document.querySelector('.screen video.live').currentTime") < 2.0


def test_the_playhead_follows_playback_across_the_whole_film(page):
    page.locator("#playCut").click()
    page.wait_for_function("player.idx === 1", timeout=15000)      # handed over to B
    page.wait_for_function("tl.state.playhead >= 2.2", timeout=10000)
    t = page.evaluate("tl.state.playhead")
    assert 2.2 <= t <= 4.0
    head = page.evaluate("parseFloat(document.querySelector('#tl .tl-head').style.left)")
    assert head == pytest.approx(page.evaluate(f"tl.timeToX({t})"), abs=8)
    assert page.locator("#tl .blk.live").get_attribute("data-id") == ids(page)[1]


# ------------------------------------------------------------------ the edit API

def test_set_range_reaches_the_disk_and_undo_redo_walk_it_back_and_forth(page, project):
    first = ids(page)[0]
    changed = page.evaluate(
        f"() => {{ tl.begin('trim'); const c = tl.setRange('{first}', null, 3.5); "
        f"tl.commit(); return c; }}")
    assert changed is True
    assert page.evaluate("segs[0].out") == 3.5
    assert page.locator("#total").inner_text() == "0:04.5"
    assert page.locator("#tl .tl-total").inner_text() == "0:04.5"
    assert page.locator("#undo").get_attribute("title").startswith("undo: trim")
    wait_saved(page)
    assert on_disk(project)[0]["out"] == 3.5

    page.keyboard.press("Control+z")
    assert page.evaluate("segs[0].out") == 3.0
    assert page.locator("#redo").get_attribute("title").startswith("redo: trim")
    wait_saved(page)
    assert on_disk(project)[0]["out"] == 3.0

    page.keyboard.press("Control+Shift+z")
    assert page.evaluate("segs[0].out") == 3.5
    wait_saved(page)
    assert on_disk(project)[0]["out"] == 3.5
    # the ids survived the round trip
    assert [s["id"] for s in on_disk(project)] == ids(page)

    # clamped the way nudge() clamps: never past the clip, never shorter than 0.2 s
    page.evaluate(f"tl.setRange('{first}', 2.95, 99)")
    assert page.evaluate("[segs[0].in, segs[0].out]") == [2.95, 6.0]
    page.evaluate(f"tl.setRange('{first}', 5.95, 6.0)")
    assert page.evaluate("[segs[0].in, segs[0].out]") == [5.8, 6.0]


def test_a_split_makes_two_shots_that_survive_a_save_with_distinct_ids(page, project):
    first, second = ids(page)
    new = page.evaluate(f"tl.split('{first}', 1.0)")     # 1 s of film = 2.0 s into CLIP_A
    assert new.startswith("tmp-")
    assert page.evaluate("segs.map(s => [s.clip, s.in, s.out])") == [
        ["CLIP_A.MP4", 1.0, 2.0], ["CLIP_A.MP4", 2.0, 3.0], ["CLIP_B.MP4", 0.0, 2.0]]
    assert block_ids(page) == [first, new, second]
    assert page.locator(".seg").count() == 3
    # the save strips the temporary id, the server mints one, the board re-keys
    page.wait_for_function("segs.every(s => s.id && s.id.startsWith('g'))", timeout=8000)
    after = ids(page)
    assert after[0] == first and after[2] == second and len(set(after)) == 3
    assert block_ids(page) == after
    assert [s["id"] for s in on_disk(project)] == after
    assert page.evaluate(f"tl.byId('{new}').in") == 2.0, "the old tmp id still resolves"
    assert page.evaluate(f"tl.indexOf('{new}')") == 1
    # too close to an edge is refused, and is not an edit
    assert page.evaluate(f"tl.split('{first}', 0.1)") is None
    assert page.locator(".seg").count() == 3
    # one undo takes the split back — with the real ids on the shot that stays
    page.keyboard.press("Control+z")
    assert page.evaluate("segs.map(s => [s.clip, s.in, s.out])") == [
        ["CLIP_A.MP4", 1.0, 3.0], ["CLIP_B.MP4", 0.0, 2.0]]
    assert ids(page) == [first, second]


def test_move_reorders_and_the_ids_travel_with_the_shots(page, project):
    a, b = ids(page)
    assert page.evaluate(f"tl.move(['{b}'], '{a}')") is True
    assert ids(page) == [b, a]
    assert page.evaluate("segs.map(s => s.clip)") == ["CLIP_B.MP4", "CLIP_A.MP4"]
    assert block_ids(page) == [b, a]
    assert page.locator(".seg").first.locator(".clip").inner_text() == "CLIP_B"
    wait_saved(page)
    assert [(s["id"], s["clip"]) for s in on_disk(project)] == [
        (b, "CLIP_B.MP4"), (a, "CLIP_A.MP4")]
    page.evaluate(f"tl.move(['{b}'], null)")            # null = to the end
    assert ids(page) == [a, b]
    page.keyboard.press("Control+z")
    assert ids(page) == [b, a]
    assert page.evaluate(f"tl.move(['{b}'], '{a}')") is False, "already there: no entry"


def test_remove_and_insert_keep_the_board_and_the_bin_in_step(page):
    a, b = ids(page)
    page.evaluate(f"tl.remove(['{a}'])")
    assert ids(page) == [b]
    assert page.locator(".seg").count() == 1
    assert page.evaluate("tl.state.anchor") == b
    new = page.evaluate("tl.insert({clip: 'CLIP_C.MP4', in: 1.0, out: 2.5, why: 'third'}, null)")
    assert new.startswith("tmp-")
    assert page.evaluate("segs.map(s => s.clip)") == ["CLIP_B.MP4", "CLIP_C.MP4"]
    assert page.evaluate("tl.state.anchor") == new
    assert page.evaluate("sel") == 1
    page.wait_for_function("segs.every(s => s.id && s.id.startsWith('g'))", timeout=8000)
    assert page.evaluate(f"tl.indexOf('{new}')") == 1
    assert page.locator("#tl .tl-total").inner_text() == "0:03.5"


def test_snaps_for_resolves_with_the_clips_sentences_and_is_cached(page):
    d = page.evaluate("tl.snapsFor('CLIP_A.MP4')")
    assert d["clip"] == "CLIP_A.MP4"
    assert [s["text"] for s in d["sentences"]] == ["hello there", "how are you", "goodbye"]
    assert d["sentences"][0]["cut_in"] == 0.25 and d["pads"] == {"head": 0.25, "tail": 0.45}
    assert page.evaluate("tl.snapsFor('CLIP_A.MP4') === tl.snapsFor('CLIP_A.MP4')")


def test_the_cards_edits_share_the_stack_with_the_timeline(page):
    """pushUndo() in app.js is the module's begin/commit now: a card's trim button and a
    timeline edit undo in one order, and the tooltip says which is next."""
    page.locator(".seg").first.locator("button", has_text="+").nth(1).click()   # out +0.25
    first = ids(page)[0]
    page.evaluate(f"tl.move(['{first}'], null)")
    assert page.evaluate("segs.map(s => s.clip)") == ["CLIP_B.MP4", "CLIP_A.MP4"]
    assert page.locator("#undo").get_attribute("title").startswith("undo: move")
    page.locator("#undo").click()
    assert page.evaluate("segs.map(s => s.clip)") == ["CLIP_A.MP4", "CLIP_B.MP4"]
    assert page.locator("#undo").get_attribute("title").startswith("undo: trim")
    page.keyboard.press("u")
    assert page.evaluate("segs[0].out") == 3.0
    assert page.locator("#redo").get_attribute("title").startswith("redo: trim")
    page.locator("#redo").click()
    assert page.evaluate("segs[0].out") == 3.25
