"""INTAKE M9 I9.3 — keyboard-first editing on the timeline, driven in a real browser.

The module under test is `app/static/timeline-keys.js`: JKL shuttle on the monitor,
↑/↓ to the previous / next cut, Home / End, the arrow frame steps, I / O marks on the
clip in Find with ↵ inserting the range, and the Timeline section of the Keys panel
rendered from the module's own table. The assertions are made against the monitor's
<video>, app.js's `segs` / `sel` / `player`, and the EDL on disk — not the module's word.

Same fixture pattern as test_timeline_ui.py: the real uvicorn server on a real port, the
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
        pg.wait_for_selector("#tlKeys")             # the keys module has mounted
        yield pg
        browser.close()


def ids(page) -> list[str]:
    return page.evaluate("segs.map(s => s.id)")


def ranges(page) -> list[list]:
    return page.evaluate("segs.map(s => [s.clip, s.in, s.out])")


def wait_saved(page) -> None:
    page.wait_for_function(
        "document.querySelector('#saveState').textContent.startsWith('saved')",
        timeout=8000)


def on_disk(project) -> list[dict]:
    return json.loads(Path(project["edl"]).read_text(encoding="utf-8"))["segments"]


def live_time(page) -> float:
    return page.evaluate("liveVideo().currentTime")


def cue(page, film_t: float) -> None:
    """Park the monitor at a film time through the foundation and wait for the buffer to
    have actually seeked there (metadata may still be opening)."""
    page.evaluate(f"tl.seek({film_t})")
    at = page.evaluate(f"tl.shotAt({film_t})")
    page.wait_for_function(
        f"Math.abs(liveVideo().currentTime - {at['clipT']}) < 0.1", timeout=10000)


# ------------------------------------------------------------------ transport

def test_l_plays_the_cut_and_stacks_the_rate_and_k_pauses(page):
    page.keyboard.press("l")
    page.wait_for_function("player.playing && player.idx === 0", timeout=10000)
    assert page.evaluate("liveVideo().playbackRate") == 1
    page.keyboard.press("l")
    assert page.evaluate("liveVideo().playbackRate") == 2
    # the other buffer holds the next shot: it must hand over at the same speed
    assert page.evaluate("player.vids.map(v => v.playbackRate)") == [2, 2]
    assert page.evaluate("player.vids.map(v => v.defaultPlaybackRate)") == [2, 2]
    page.keyboard.press("l")
    page.keyboard.press("l")
    page.keyboard.press("l")
    assert page.evaluate("liveVideo().playbackRate") == 8, "8× is the ceiling"
    assert page.evaluate("tlKeys.shuttle.rate") == 8
    page.keyboard.press("k")
    assert not page.evaluate("player.playing")
    assert page.evaluate("player.vids.map(v => v.playbackRate)") == [1, 1]
    assert page.evaluate("tlKeys.shuttle.rate") == 0
    # space after a shuttle plays at 1×, not at the rate the shuttle left behind
    page.keyboard.press("l")
    page.keyboard.press("l")
    page.keyboard.press("Space")            # app.js's: pause
    page.wait_for_function("!player.playing", timeout=5000)
    page.keyboard.press("Space")            # and play again — at 1×
    page.wait_for_function("player.playing", timeout=10000)
    assert page.evaluate("liveVideo().playbackRate") == 1
    page.evaluate("pauseCut()")


def test_j_drives_the_monitor_backwards_and_crosses_the_cut(page):
    cue(page, 3.0)                          # 1.0 s into CLIP_B
    page.keyboard.press("j")
    assert page.evaluate("tlKeys.shuttle.rate") == -1
    assert not page.evaluate("player.playing")
    page.wait_for_timeout(300)
    t = live_time(page)
    assert t < 0.95, f"expected currentTime to have moved back from 1.0, got {t}"
    assert page.evaluate("tl.state.playhead") < 2.95
    assert page.locator("#pos").inner_text() != "0:03.0"
    page.keyboard.press("j")                # again: stacks
    assert page.evaluate("tlKeys.shuttle.rate") == -2
    # reversing past CLIP_B's in-point lands on CLIP_A's out-point (3.0) and carries on
    # down from there — never from the proxy's own in-point, which the reload's media
    # fragment reports until the park has run
    page.wait_for_function("player.idx === 0", timeout=10000)
    page.wait_for_function(
        "liveVideo().readyState >= 1 && liveVideo().currentTime < 2.6", timeout=10000)
    assert live_time(page) > 2.0
    assert page.evaluate("tl.state.playhead") == pytest.approx(live_time(page) - 1.0, abs=0.05)
    page.keyboard.press("k")
    assert page.evaluate("tlKeys.shuttle.rate") == 0
    t = live_time(page)
    page.wait_for_timeout(150)
    assert live_time(page) == pytest.approx(t, abs=0.02), "K stops the reverse drive"
    # L after a reverse plays forward at 1× from where the reverse stopped
    page.keyboard.press("l")
    page.wait_for_function("player.playing", timeout=10000)
    assert page.evaluate("liveVideo().playbackRate") == 1
    page.evaluate("pauseCut()")


def test_k_held_with_l_plays_at_one_x_while_held(page):
    page.keyboard.down("k")
    page.keyboard.down("l")
    page.wait_for_function("player.playing", timeout=10000)
    assert page.evaluate("liveVideo().playbackRate") == 1
    page.keyboard.up("l")
    assert not page.evaluate("player.playing"), "released: paused"
    page.keyboard.up("k")
    assert not page.evaluate("player.playing")


# ------------------------------------------------------------------ navigation

def test_down_and_up_walk_the_cuts_and_home_end_the_film(page):
    a, b = ids(page)
    page.keyboard.press("ArrowDown")
    assert page.evaluate("tl.state.playhead") == pytest.approx(2.0, abs=0.01)
    assert page.evaluate("[...tl.state.sel]") == [b]
    assert page.evaluate("sel") == 1
    assert page.evaluate("player.idx") == 1 and not page.evaluate("player.playing")
    page.keyboard.press("ArrowDown")        # no next cut: the end of the film
    assert page.evaluate("tl.state.playhead") == pytest.approx(4.0, abs=0.01)
    page.keyboard.press("ArrowUp")          # back to this shot's head first
    assert page.evaluate("tl.state.playhead") == pytest.approx(2.0, abs=0.01)
    assert page.evaluate("[...tl.state.sel]") == [b]
    page.keyboard.press("ArrowUp")          # then the previous cut
    assert page.evaluate("tl.state.playhead") == pytest.approx(0.0, abs=0.01)
    assert page.evaluate("[...tl.state.sel]") == [a]
    page.keyboard.press("End")
    assert page.evaluate("tl.state.playhead") == pytest.approx(4.0, abs=0.01)
    assert page.evaluate("tl.state.anchor") == b
    page.keyboard.press("Home")
    assert page.evaluate("tl.state.playhead") == pytest.approx(0.0, abs=0.01)
    assert page.evaluate("tl.state.anchor") == a
    assert page.locator("#tl .blk.sel").get_attribute("data-id") == a


def test_arrows_step_the_playhead_and_leave_the_selection_alone(page):
    a, b = ids(page)
    page.evaluate(f"tl.select(['{a}'])")
    page.evaluate("tl.seek(1.0)")
    page.keyboard.press("ArrowRight")
    assert page.evaluate("tl.state.playhead") == pytest.approx(1.0 + 1 / 30, abs=0.002)
    page.keyboard.press("Shift+ArrowRight")
    assert page.evaluate("tl.state.playhead") == pytest.approx(2.0 + 1 / 30, abs=0.002)
    page.keyboard.press("ArrowLeft")
    assert page.evaluate("tl.state.playhead") == pytest.approx(2.0, abs=0.002)
    page.keyboard.press("Shift+ArrowLeft")
    assert page.evaluate("tl.state.playhead") == pytest.approx(1.0, abs=0.002)
    assert page.evaluate("[...tl.state.sel]") == [a], "the arrows move the playhead only"
    assert page.evaluate("player.idx") == 0 and not page.evaluate("player.playing")
    page.keyboard.press("Shift+ArrowLeft")
    page.keyboard.press("Shift+ArrowLeft")
    assert page.evaluate("tl.state.playhead") == 0, "clamped at the top"






# ------------------------------------------------------------------ marks on a clip

def test_i_o_and_enter_on_a_playing_clip_insert_the_marked_range(page):
    a, b = ids(page)
    # playing the cut: I says where marks go; O is still the switcher's project picker,
    # and Esc still closes that picker rather than clearing the selection
    page.keyboard.press("i")
    page.wait_for_function(
        "document.querySelector('#toast').textContent.includes('mark on a clip')", timeout=3000)
    assert page.locator("#tlMarks").is_hidden()
    page.evaluate(f"tl.select(['{a}'])")
    page.keyboard.press("o")
    page.wait_for_function("!document.querySelector('#picker').hidden", timeout=5000)
    assert page.evaluate("[tlKeys.marks.in, tlKeys.marks.out]") == [None, None]
    page.evaluate("document.activeElement && document.activeElement.blur()")
    page.keyboard.press("Escape")
    page.wait_for_function("document.querySelector('#picker').hidden", timeout=5000)
    assert page.evaluate("[...tl.state.sel]") == [a], "Esc went to the picker, not the selection"

    page.locator("#findQ").fill("goodbye")
    page.locator("#findGo").click()
    page.wait_for_selector("#findResults .cand", timeout=15000)
    page.locator("#findResults .cand").first.click()
    page.wait_for_selector("#findPlayer:visible")
    page.wait_for_function(
        "document.querySelector('#findVideo').readyState >= 1", timeout=15000)
    page.evaluate("document.activeElement && document.activeElement.blur()")
    clip = page.evaluate("findSel.clip")
    page.evaluate("document.querySelector('#findVideo').currentTime = 1.0")
    page.keyboard.press("i")
    assert page.evaluate("tlKeys.marks.in") == 1.0
    assert page.locator("#tlMarks").is_visible()
    assert page.locator("#tlMarks .tick.in").is_visible()
    assert page.locator("#tlMarks .tick.out").is_hidden()
    page.evaluate("document.querySelector('#findVideo').currentTime = 2.5")
    page.keyboard.press("o")
    assert page.evaluate("[tlKeys.marks.in, tlKeys.marks.out]") == [1.0, 2.5]
    assert page.locator("#tlMarks .tick.out").is_visible()
    assert "↵" in page.locator("#tlMarks").inner_text()

    page.evaluate(f"tl.select(['{a}'])")
    page.keyboard.press("Enter")
    assert page.evaluate("segs.length") == 3
    assert page.evaluate("segs.map(s => [s.clip, s.in, s.out])")[1] == [clip, 1.0, 2.5]
    assert page.evaluate("segs[1].why") == "hello there", "the transcript line inside the range"
    assert page.evaluate("tl.state.anchor") == page.evaluate("segs[1].id")
    assert not page.evaluate("player.playing"), "↵ with marks inserts; it does not play the shot"
    assert page.locator("#tlMarks").is_hidden()
    assert page.evaluate("[tlKeys.marks.in, tlKeys.marks.out]") == [None, None]
    assert page.locator("#undo").get_attribute("title").startswith("undo: insert")

    # ⌥O clears one mark; Esc clears both
    page.evaluate("document.querySelector('#findVideo').currentTime = 0.5")
    page.keyboard.press("i")
    page.evaluate("document.querySelector('#findVideo').currentTime = 3.0")
    page.keyboard.press("o")
    page.keyboard.press("Alt+o")
    assert page.evaluate("[tlKeys.marks.in, tlKeys.marks.out]") == [0.5, None]
    page.keyboard.press("Escape")
    assert page.locator("#tlMarks").is_hidden()
    # without marks, ↵ is still app.js's: play this shot only
    page.keyboard.press("Enter")
    page.wait_for_function("player.playing && player.single", timeout=10000)
    page.evaluate("pauseCut()")


# ------------------------------------------------------------------ the map, the guards

def test_the_keys_panel_lists_the_timeline_keys_from_the_table(page):
    keys = page.evaluate(
        "[...document.querySelectorAll('#tlKeys kbd')].map(k => k.textContent)")
    for k in ["J", "K", "L", "↑", "↓", "Home", "End", "←", "→", "I", "O", "↵",
              "⌘Z", "⌘⇧Z", "Esc", ",", ".", "S", "+", "−", "\\", "?"]:
        assert k in keys, f"{k} missing from the map"
    text = page.locator("#tlKeys").inner_text()
    assert "shuttle" in text and "magnet" in text
    assert page.evaluate("tlKeys.KEYS.length") == \
        page.locator("#tlKeys .row").count(), "rendered from the table, row for row"
    # the board's own line no longer says j/k move the selection
    panel = page.locator("#tlKeys").locator("..")
    assert "j/k move" not in panel.inner_text()
    assert "↑/↓ move" in panel.inner_text()
    page.keyboard.press("?")
    assert "tl-flash" in (panel.get_attribute("class") or "")


def test_keys_are_ignored_while_typing(page):
    a, b = ids(page)
    page.locator("#findQ").fill("")
    page.locator("#findQ").focus()
    page.keyboard.type("jl")
    assert not page.evaluate("player.playing")
    assert page.locator("#findQ").input_value() == "jl"
    page.locator("#story").focus()
    page.keyboard.press("ArrowDown")
    assert page.evaluate("tl.state.playhead") == 0
    page.locator(".seg").first.locator(".why").focus()
    page.keyboard.press("l")
    assert not page.evaluate("player.playing")
