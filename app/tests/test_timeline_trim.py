"""INTAKE M9 I9.2 — trims by drag and the magnet, driven in a real browser.

The module under test is `app/static/timeline-trim.js`, built on the foundation
(`timeline.js`, `window.tl`): ripple trims by an edge handle, rolls by the zone over a
cut, slips by ⌥-drag, `,` / `.` nudges of the active edge, and the magnet — snapping to
the playhead, a sentence's padded cut point, a word start, with a snap line that says
what it took, `S` to turn it off, ⌘/ctrl to suspend it. The gestures are real pointer
events through playwright's mouse; the assertions are made against app.js's `segs`, the
EDL on disk, the transport's total and the undo stack — not the module's word for it.

Same fixture pattern as test_timeline_ui.py: the real uvicorn server on a real port,
the synthetic three-clip bin (utterances at 0.5–2.0, 2.4–4.0, 5.0–5.6 with the cut pads
0.25 / 0.45, so a sentence `cut_out` sits at 2.45, 4.45 and 6.0), the EDL re-seeded per
test (two shots, 2 s each). Skipped when playwright is absent.
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
        # The jobs strip lives in the sticky header. In the whole suite the shared work
        # dir carries every render test_server made, so the strip grows to most of the
        # viewport and keeps re-laying itself as their review copies settle — and the
        # mouse, which works in viewport coordinates, lands on it instead of the lane.
        # Nothing here is about jobs: the strip is hidden for these tests.
        pg.add_style_tag(content="#progress { display: none !important; }")
        pg.wait_for_selector("#tl .blk .tl-h.out")          # the lane has decorated the blocks
        pg.wait_for_selector("#tl .tl-roll")
        # the magnet's points are fetched at mount; wait for the server's answer
        pg.evaluate("Promise.all(segs.map(s => tl.snapsFor(s.clip)))")
        yield pg
        browser.close()


def ids(page) -> list[str]:
    return page.evaluate("segs.map(s => s.id)")


def ranges(page) -> list[list[float]]:
    return page.evaluate("segs.map(s => [s.in, s.out])")


def film_total(page) -> float:
    return page.evaluate("segs.reduce((a, s) => a + (s.out - s.in), 0)")


def wait_saved(page) -> None:
    page.wait_for_function(
        "document.querySelector('#saveState').textContent.startsWith('saved')",
        timeout=8000)


def on_disk(project) -> list[dict]:
    return json.loads(Path(project["edl"]).read_text(encoding="utf-8"))["segments"]


def zoom(page) -> float:
    return page.evaluate("tl.state.zoom")


def reveal(page) -> None:
    """The mouse works in viewport coordinates and does not scroll; the timeline is
    brought to the viewport's bottom edge, clear of the sticky header."""
    page.evaluate("document.querySelector('#tl').scrollIntoView({block: 'end'})")


def press(page, loc, modifiers=()) -> tuple[float, float]:
    """Pointer down on the middle of an element; returns where."""
    reveal(page)
    box = loc.bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    for m in modifiers:
        page.keyboard.down(m)
    page.mouse.move(x, y)
    page.mouse.down()
    return x, y


def drag(page, loc, dx: float, modifiers=(), release=True) -> tuple[float, float]:
    """A real drag: down on the element, dx px to the right in steps, up."""
    x, y = press(page, loc, modifiers)
    page.mouse.move(x + dx, y, steps=6)
    if release:
        page.mouse.up()
        for m in modifiers:
            page.keyboard.up(m)
    return x + dx, y


def out_handle(page, i: int):
    return page.locator("#tl .blk").nth(i).locator(".tl-h.out")


def in_handle(page, i: int):
    return page.locator("#tl .blk").nth(i).locator(".tl-h.in")


# ------------------------------------------------------------------ ripple, roll, slip

def test_dragging_the_out_handle_ripple_trims_and_one_undo_restores(page, project):
    """0.3 s of pointer travel on shot 1's out handle: its out moves 3.0 → 3.3, the film
    closes up (the total is 4.3 s on the ruler and the transport), the EDL on disk
    follows, and the whole drag is one undo entry labelled `trim`."""
    z = zoom(page)
    assert page.locator("#tl .blk").nth(0).locator(".tl-h").count() == 2
    drag(page, out_handle(page, 0), 0.3 * z)
    assert page.evaluate("segs[0].out") == pytest.approx(3.3, abs=0.02)
    assert page.evaluate("segs[0].in") == 1.0
    assert page.evaluate("segs[1].in") == 0.0 and page.evaluate("segs[1].out") == 2.0
    total = film_total(page)
    assert total == pytest.approx(4.3, abs=0.02)
    assert page.locator("#tl .tl-total").inner_text() == f"0:{total:04.1f}"
    assert page.locator("#total").inner_text() == f"0:{total:04.1f}"
    assert page.locator("#undo").get_attribute("title").startswith("undo: trim")
    assert page.locator("#tl .blk.sel").get_attribute("data-id") == ids(page)[0]
    assert page.locator("#tl .tl-tip").is_hidden(), "the tooltip goes with the drag"
    wait_saved(page)
    assert on_disk(project)[0]["out"] == page.evaluate("segs[0].out")

    page.keyboard.press("Control+z")
    assert ranges(page) == [[1.0, 3.0], [0.0, 2.0]]
    assert page.locator("#tl .tl-total").inner_text() == "0:04.0"
    assert page.locator("#redo").get_attribute("title").startswith("redo: trim")
    wait_saved(page)
    assert on_disk(project)[0]["out"] == 3.0


def test_dragging_the_in_handle_moves_the_in_point_and_the_film_closes_up(page):
    z = zoom(page)
    drag(page, in_handle(page, 0), 0.4 * z)
    assert page.evaluate("segs[0].in") == pytest.approx(1.4, abs=0.02)
    assert page.evaluate("segs[0].out") == 3.0
    assert film_total(page) == pytest.approx(3.6, abs=0.02)
    assert page.evaluate("tl.filmStart(segs[1].id)") == pytest.approx(1.6, abs=0.02)
    # and it cannot cross the out: 0.2 s is the floor
    drag(page, in_handle(page, 0), 5 * z)
    assert page.evaluate("segs[0].in") == 2.8
    assert page.evaluate("segs[0].out") == 3.0


def test_a_boundary_drag_rolls_the_cut_and_the_film_length_holds(page):
    """The zone over the cut between shots 1 and 2: 0.4 s to the right gives shot 1's
    out and shot 2's in the same 0.4 s; the sum of the lengths is still 4.0 s."""
    z = zoom(page)
    roll = page.locator("#tl .tl-roll")
    assert roll.count() == 1
    assert page.evaluate("getComputedStyle(document.querySelector('#tl .tl-roll')).cursor") \
        == "col-resize"
    drag(page, roll, 0.4 * z)
    r = ranges(page)
    assert r[0][1] == pytest.approx(3.4, abs=0.02)
    assert r[1][0] == pytest.approx(0.4, abs=0.02)
    assert r[1][0] - 0.0 == pytest.approx(r[0][1] - 3.0, abs=1e-9), "the same delta"
    assert film_total(page) == pytest.approx(4.0, abs=1e-9)
    assert page.locator("#tl .tl-total").inner_text() == "0:04.0"
    assert page.locator("#undo").get_attribute("title").startswith("undo: roll")
    page.keyboard.press("Control+z")
    assert ranges(page) == [[1.0, 3.0], [0.0, 2.0]]
    # to the left, the right shot's in stops at 0 — and takes the left shot's out with it
    drag(page, roll, -1.5 * z)
    r = ranges(page)
    assert r[1][0] == 0.0 and r[0][1] == 3.0, r
    assert film_total(page) == pytest.approx(4.0, abs=1e-9)


def test_alt_drag_slips_the_shot_keeping_its_length(page):
    z = zoom(page)
    body = page.locator("#tl .blk").nth(0)
    drag(page, body, 0.5 * z, modifiers=["Alt"])
    r = ranges(page)
    assert r[0][0] == pytest.approx(1.5, abs=0.02) and r[0][1] == pytest.approx(3.5, abs=0.02)
    assert r[0][1] - r[0][0] == pytest.approx(2.0, abs=1e-9)
    assert r[1] == [0.0, 2.0]
    assert film_total(page) == pytest.approx(4.0, abs=1e-9)
    assert page.locator("#undo").get_attribute("title").startswith("undo: slip")
    # clamped to the clip: 6 s long, so the range stops at 4.0–6.0
    drag(page, body, 5 * z, modifiers=["Alt"])
    assert ranges(page)[0] == [4.0, 6.0]
    page.keyboard.press("Control+z")
    page.keyboard.press("Control+z")
    assert ranges(page) == [[1.0, 3.0], [0.0, 2.0]]


def test_a_press_on_a_handle_without_travel_is_a_click_and_esc_cancels_a_drag(page):
    first = ids(page)[0]
    x, y = press(page, out_handle(page, 0))
    page.mouse.move(x + 1, y)
    page.mouse.up()
    assert ranges(page) == [[1.0, 3.0], [0.0, 2.0]]
    assert page.locator("#tl .blk.sel").get_attribute("data-id") == first
    assert page.locator("#undo").is_disabled(), "a click is not an edit"
    # Esc mid-drag puts the cut back and leaves nothing on the stack
    z = zoom(page)
    drag(page, out_handle(page, 0), 0.3 * z, release=False)
    page.wait_for_function("Math.abs(segs[0].out - 3.3) < 0.02", timeout=3000)
    assert page.locator("#tl .tl-tip").is_visible()
    assert "out 3.3" in page.locator("#tl .tl-tip").inner_text()
    page.keyboard.press("Escape")
    assert ranges(page) == [[1.0, 3.0], [0.0, 2.0]]
    assert page.locator("#tl .tl-tip").is_hidden()
    page.mouse.up()
    assert ranges(page) == [[1.0, 3.0], [0.0, 2.0]]
    assert page.locator("#undo").is_disabled()


# ------------------------------------------------------------------ the magnet

def test_a_drag_ending_near_a_sentence_cut_out_snaps_to_it_exactly(page):
    """CLIP_A's second utterance ends at 4.0; with the 0.45 s tail pad its `cut_out` is
    4.45. An out handle released 4 px short of it lands on 4.45 exactly, the snap line
    across the timeline says `sentence`, and the tooltip says what it took."""
    z = zoom(page)
    drag(page, out_handle(page, 0), 1.45 * z - 4, release=False)
    page.wait_for_selector("#tl .tl-snapline:not([hidden])", timeout=3000)
    assert page.locator("#tl .tl-snapline label").inner_text() == "sentence"
    assert page.evaluate("parseFloat(document.querySelector('#tl .tl-snapline').style.left)") \
        == pytest.approx(page.evaluate("tl.timeToX(3.45)"), abs=0.5)     # film 0 + (4.45 − 1.0)
    assert "snap: sentence" in page.locator("#tl .tl-tip").inner_text()
    # the dragged block shows its clip's sentence points as ticks
    assert page.locator("#tl .blk").nth(0).locator(".tl-tick.sentence").count() >= 2
    page.mouse.up()
    assert page.evaluate("segs[0].out") == 4.45
    assert page.locator("#tl .tl-snapline").is_hidden()
    assert page.locator("#tl .blk .tl-tick").count() == 0
    page.keyboard.press("Control+z")
    assert page.evaluate("segs[0].out") == 3.0


def test_the_magnets_priority_is_the_playhead_then_sentences_then_words(page):
    z = zoom(page)
    # the playhead at 3.2 s of film is 4.2 s into CLIP_A: an out released 3 px off it
    page.evaluate("tl.setPlayhead(3.2)")
    drag(page, out_handle(page, 0), 1.2 * z + 3, release=False)
    page.wait_for_selector("#tl .tl-snapline:not([hidden])", timeout=3000)
    assert page.locator("#tl .tl-snapline label").inner_text() == "playhead"
    page.mouse.up()
    assert page.evaluate("segs[0].out") == 4.2
    page.keyboard.press("Control+z")
    # a word starts at 3.5: nothing else within 8 px, so the word takes it
    page.evaluate("tl.setPlayhead(0)")
    drag(page, out_handle(page, 0), 0.5 * z + 3, release=False)
    page.wait_for_selector("#tl .tl-snapline:not([hidden])", timeout=3000)
    assert page.locator("#tl .tl-snapline label").inner_text() == "word"
    page.mouse.up()
    assert page.evaluate("segs[0].out") == 3.5
    page.keyboard.press("Control+z")
    # an in edge snaps to a sentence's cut_in (2.4 − 0.25 = 2.15), not its cut_out
    drag(page, in_handle(page, 0), 1.15 * z + 4, release=False)
    page.wait_for_selector("#tl .tl-snapline:not([hidden])", timeout=3000)
    assert page.locator("#tl .tl-snapline label").inner_text() == "sentence"
    page.mouse.up()
    assert page.evaluate("segs[0].in") == 2.15


def test_s_turns_the_magnet_off_and_ctrl_suspends_it(page):
    z = zoom(page)
    ind = page.locator("#tl .tl-magnet")
    assert ind.inner_text() == "magnet · on"
    page.keyboard.press("s")
    assert ind.inner_text() == "magnet · off"
    assert page.evaluate("localStorage.getItem('roughcut.tl.magnet')") == "0"
    drag(page, out_handle(page, 0), 1.45 * z - 4, release=False)
    page.wait_for_function("segs[0].out > 4.3", timeout=3000)
    assert page.locator("#tl .tl-snapline").is_hidden()
    page.mouse.up()
    out = page.evaluate("segs[0].out")
    assert out != 4.45 and out == pytest.approx(4.45 - 4 / z, abs=0.011)
    page.keyboard.press("Control+z")
    page.keyboard.press("S")
    assert ind.inner_text() == "magnet · on"
    assert page.evaluate("localStorage.getItem('roughcut.tl.magnet')") == "1"
    # ⌘/ctrl held while dragging suspends it for that drag only
    drag(page, out_handle(page, 0), 1.45 * z - 4, modifiers=["Control"])
    out = page.evaluate("segs[0].out")
    assert out != 4.45 and out == pytest.approx(4.45 - 4 / z, abs=0.011)
    assert ind.inner_text() == "magnet · on"
    # the indicator is a button too
    reveal(page)
    ind.click()
    assert ind.inner_text() == "magnet · off"


# ------------------------------------------------------------------ nudges

def test_comma_and_period_nudge_the_active_edge_by_a_frame_or_a_second(page):
    """With nothing dragged yet the active edge is the selected shot's out; `,` takes a
    frame (1/30 s, kept at the foundation's 0.01 s), `⇧.` a second. After an in handle
    is dragged, that edge is the one the keys move."""
    first = ids(page)[0]
    page.evaluate(f"tl.select(['{first}'])")
    page.keyboard.press(",")
    assert page.evaluate("segs[0].out") == 2.97
    assert page.locator("#undo").get_attribute("title").startswith("undo: nudge")
    page.keyboard.press(".")
    assert page.evaluate("segs[0].out") == 3.0
    page.keyboard.press("Shift+.")
    assert page.evaluate("segs[0].out") == 4.0
    page.keyboard.press("Shift+,")
    assert page.evaluate("segs[0].out") == 3.0
    assert page.evaluate("segs[0].in") == 1.0
    z = zoom(page)
    drag(page, in_handle(page, 0), 0.3 * z)
    dragged = page.evaluate("segs[0].in")
    assert dragged == pytest.approx(1.3, abs=0.02)
    page.keyboard.press(",")
    assert page.evaluate("segs[0].in") == round(dragged - 1 / 30, 2)
    assert page.evaluate("segs[0].out") == 3.0
    assert page.evaluate("tl.trim.active.edge") == "in"
    page.keyboard.press("Control+z")
    assert page.evaluate("segs[0].in") == dragged
    # inside an input the keys are the input's
    page.locator("#story").fill("")
    page.locator("#story").press(",")
    assert page.evaluate("segs[0].in") == dragged
