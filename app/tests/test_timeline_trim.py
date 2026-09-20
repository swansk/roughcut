"""INTAKE M9 I9.2 + I9.7 — trims by drag, the magnet, and the edge columns, driven in a
real browser.

The module under test is `app/static/timeline-trim.js`, built on the foundation
(`timeline.js`, `window.tl`). At every interior cut an edge column, 16 px centred on the
cut line, the lane's full height, split by height — the top half extends or shortens the
shot and the rest of the film moves (a ripple trim), the bottom half rolls the cut into the
neighbour, ⇧ flips the two before or during the drag; the film's first in and last out
keep a full-height handle of their own; slips by ⌥-drag; `,` / `.` nudges of the active
edge; the hover cue, the ghost of the pushed shot and the one-line hint; and the magnet —
snapping to the playhead, a sentence's padded cut point, a word start, with a snap line
that says what it took, `S` to turn it off, ⌘/ctrl to suspend it. The gestures are real
pointer events through playwright's mouse; the assertions are made against app.js's
`segs`, the EDL on disk, the transport's total and the undo stack — not the module's word
for it.

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
        pg.wait_for_selector("#tl .blk .tl-h.in")           # the lane has decorated the blocks
        pg.wait_for_selector("#tl .tl-edge .q.t.l")         # and laid the column over the cut
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


def hover(page, loc) -> tuple[float, float]:
    """The pointer over the middle of an element, nothing pressed."""
    reveal(page)
    box = loc.bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    return x, y


def edge(page, i: int):
    """The edge column over the cut after shot i (I9.7)."""
    return page.locator("#tl .tl-edge").nth(i)


def quadrant(page, i: int, row: str, side: str):
    """One hit target of an edge column: row `t` (extend) or `b` (roll), side `l` / `r`
    of the cut line."""
    return edge(page, i).locator(f".q.{row}.{side}")


def out_handle(page, i: int):
    """Shot i's out: the block's own full-height handle at the film's last out, else the
    top-left quadrant of the column over the cut (I9.7 folded the interior handles in)."""
    if i == page.locator("#tl .blk").count() - 1:
        return page.locator("#tl .blk").nth(i).locator(".tl-h.out")
    return quadrant(page, i, "t", "l")


def in_handle(page, i: int):
    """Shot i's in: the block's own handle at the film's first in, else the top-right
    quadrant of the column over the cut before it."""
    if i == 0:
        return page.locator("#tl .blk").nth(i).locator(".tl-h.in")
    return quadrant(page, i - 1, "t", "r")


def roll_zone(page, i: int):
    """The bottom half of the column over the cut after shot i — either side rolls."""
    return quadrant(page, i, "b", "l")


# ------------------------------------------------------------------ ripple, roll, slip

def test_dragging_the_out_handle_ripple_trims_and_one_undo_restores(page, project):
    """0.3 s of pointer travel on shot 1's out — the top-left quadrant of the column over
    the cut: its out moves 3.0 → 3.3, shot 2 is untouched and slides later (the total is
    4.3 s on the ruler and the transport), the EDL on disk follows, and the whole drag is
    one undo entry labelled `trim`. The block's own out handle is hidden: an interior
    edge belongs to the column; the film's first in keeps its handle."""
    z = zoom(page)
    blk = page.locator("#tl .blk").nth(0)
    assert blk.locator(".tl-h.in").is_visible(), "the film's first in keeps its handle"
    assert blk.locator(".tl-h.out").is_hidden(), "an interior out is the edge column's"
    assert page.locator("#tl .tl-edge").count() == 1
    assert page.locator("#tl .tl-edge .q").count() == 4
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
    """The bottom half of the column over the cut between shots 1 and 2: 0.4 s to the
    right gives shot 1's out and shot 2's in the same 0.4 s; the sum of the lengths is
    still 4.0 s."""
    z = zoom(page)
    roll = roll_zone(page, 0)
    assert page.locator("#tl .tl-edge").count() == 1
    assert page.evaluate("getComputedStyle(document.querySelector('#tl .tl-edge .q.b.l')).cursor") \
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


# ------------------------------------------------------------------ the edge columns (I9.7)

def test_the_top_half_of_a_cut_extends_the_shot_and_pushes_the_rest(page):
    """The column over the cut between shots 1 and 2: 16 px centred on the line, the
    lane's full height, split at half. Its top-left quadrant is shot 1's out — +0.3 s
    grows the shot, shot 2's range is untouched and its film start slides by 0.3, the
    total grows by 0.3, one ⌘Z restores. The top-right quadrant is shot 2's in."""
    z = zoom(page)
    col, blk = edge(page, 0).bounding_box(), page.locator("#tl .blk").nth(0).bounding_box()
    assert col["width"] == pytest.approx(16, abs=0.5)
    assert col["height"] == pytest.approx(blk["height"], abs=0.5)
    assert col["x"] + 8 == pytest.approx(blk["x"] + blk["width"], abs=0.5), "centred on the cut"
    q = quadrant(page, 0, "t", "l").bounding_box()
    assert q["width"] == pytest.approx(8, abs=0.5) and q["height"] == pytest.approx(col["height"] / 2, abs=0.5)
    assert q["y"] == pytest.approx(col["y"], abs=0.5), "the top half"
    drag(page, quadrant(page, 0, "t", "l"), 0.3 * z)
    assert page.evaluate("segs[0].out") == pytest.approx(3.3, abs=0.02)
    assert page.evaluate("segs[0].in") == 1.0
    assert ranges(page)[1] == [0.0, 2.0], "the neighbour's range is untouched"
    total = film_total(page)
    assert total == pytest.approx(4.3, abs=0.02)
    assert page.locator("#tl .tl-total").inner_text() == f"0:{total:04.1f}"
    assert page.evaluate("tl.filmStart(segs[1].id)") == pytest.approx(2.3, abs=0.02), "shot 2 slid later"
    assert page.locator("#undo").get_attribute("title").startswith("undo: trim")
    page.keyboard.press("Control+z")
    assert ranges(page) == [[1.0, 3.0], [0.0, 2.0]]
    assert page.locator("#undo").is_disabled(), "one entry for the drag"
    # right of the line, the top half is shot 2's in: +0.4 s shortens shot 2, shot 1 holds
    drag(page, quadrant(page, 0, "t", "r"), 0.4 * z)
    r = ranges(page)
    assert r[0] == [1.0, 3.0]
    assert r[1][0] == pytest.approx(0.4, abs=0.02) and r[1][1] == 2.0
    assert film_total(page) == pytest.approx(3.6, abs=0.02)
    page.keyboard.press("Control+z")
    assert ranges(page) == [[1.0, 3.0], [0.0, 2.0]]


def test_the_bottom_half_rolls_the_cut(page):
    """Either bottom quadrant rolls: shot 1's out and shot 2's in take the same delta, the
    sum of the lengths holds, the entry is `roll`."""
    z = zoom(page)
    q = quadrant(page, 0, "b", "r").bounding_box()
    col = edge(page, 0).bounding_box()
    assert q["y"] == pytest.approx(col["y"] + col["height"] / 2, abs=0.5), "the bottom half"
    drag(page, quadrant(page, 0, "b", "r"), 0.4 * z)
    r = ranges(page)
    assert r[0][1] == pytest.approx(3.4, abs=0.02)
    assert r[1][0] == pytest.approx(0.4, abs=0.02)
    assert r[1][0] == pytest.approx(r[0][1] - 3.0, abs=1e-9)
    assert film_total(page) == pytest.approx(4.0, abs=1e-9)
    assert page.locator("#undo").get_attribute("title").startswith("undo: roll")
    page.keyboard.press("Control+z")
    assert ranges(page) == [[1.0, 3.0], [0.0, 2.0]]


def test_shift_flips_extend_and_roll_before_and_during_a_drag(page):
    """⇧ on the top half rolls; ⇧ on the bottom half extends. Mid-drag, ⇧ pressed turns
    the extend in flight into a roll — the cut back as the press found it, the travel so
    far re-applied — and released turns it back; the column's cue and the tooltip follow;
    the one undo entry is labelled by what the drag ended as."""
    z = zoom(page)
    drag(page, quadrant(page, 0, "t", "l"), 0.4 * z, modifiers=["Shift"])
    r = ranges(page)
    assert r[0][1] == pytest.approx(3.4, abs=0.02) and r[1][0] == pytest.approx(0.4, abs=0.02)
    assert film_total(page) == pytest.approx(4.0, abs=1e-9)
    assert page.locator("#undo").get_attribute("title").startswith("undo: roll")
    page.keyboard.press("Control+z")
    assert ranges(page) == [[1.0, 3.0], [0.0, 2.0]]
    drag(page, quadrant(page, 0, "b", "l"), 0.3 * z, modifiers=["Shift"])
    assert page.evaluate("segs[0].out") == pytest.approx(3.3, abs=0.02)
    assert ranges(page)[1] == [0.0, 2.0]
    assert film_total(page) == pytest.approx(4.3, abs=0.02)
    assert page.locator("#undo").get_attribute("title").startswith("undo: trim")
    page.keyboard.press("Control+z")
    assert ranges(page) == [[1.0, 3.0], [0.0, 2.0]]
    # mid-drag: an extend in flight...
    col = page.locator("#tl .tl-edge").first
    drag(page, quadrant(page, 0, "t", "l"), 0.4 * z, release=False)
    page.wait_for_function("Math.abs(segs[0].out - 3.4) < 0.02 && segs[1].in === 0", timeout=3000)
    assert page.evaluate("tl.trim.dragging") == "trim"
    assert "the rest moves" in page.locator("#tl .tl-tip").inner_text()
    assert col.get_attribute("class") == "tl-edge lit top left ext"
    # ...becomes a roll when ⇧ goes down, without letting go
    page.keyboard.down("Shift")
    page.wait_for_function("Math.abs(segs[1].in - 0.4) < 0.02", timeout=3000)
    assert page.evaluate("tl.trim.dragging") == "roll"
    assert film_total(page) == pytest.approx(4.0, abs=1e-9)
    assert page.locator("#tl .tl-tip").inner_text().startswith("roll")
    assert col.get_attribute("class") == "tl-edge lit top left roll"
    # ...and an extend again when it comes up
    page.keyboard.up("Shift")
    page.wait_for_function("segs[1].in === 0 && Math.abs(segs[0].out - 3.4) < 0.02", timeout=3000)
    assert page.evaluate("tl.trim.dragging") == "trim"
    assert col.get_attribute("class") == "tl-edge lit top left ext"
    page.mouse.up()
    assert ranges(page)[1] == [0.0, 2.0]
    assert film_total(page) == pytest.approx(4.4, abs=0.02)
    assert page.locator("#undo").get_attribute("title").startswith("undo: trim")
    page.keyboard.press("Control+z")
    assert ranges(page) == [[1.0, 3.0], [0.0, 2.0]]
    assert page.locator("#undo").is_disabled(), "the flips left nothing else on the stack"


def test_the_last_out_extends_over_the_whole_height(page):
    """The film's last out has no neighbour to roll into: shot 2's out keeps the block's
    own full-height handle — a press at its bottom extends too — and no column sits past
    the end. The tooltip says what moves: the end."""
    z = zoom(page)
    h = out_handle(page, 1)
    blk = page.locator("#tl .blk").nth(1).bounding_box()
    box = h.bounding_box()
    assert box["height"] == pytest.approx(blk["height"], abs=3), "the whole height"
    assert box["x"] + box["width"] == pytest.approx(blk["x"] + blk["width"], abs=2), "flush with the edge"
    assert page.locator("#tl .tl-edge").count() == 1
    reveal(page)
    box = h.bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] - 3
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 0.5 * z, y, steps=6)
    page.wait_for_function("Math.abs(segs[1].out - 2.5) < 0.02", timeout=3000)
    tip = page.locator("#tl .tl-tip").inner_text()
    assert tip.startswith("out 2.5") and "+0.5" in tip and "the end moves" in tip, tip
    page.mouse.up()
    assert ranges(page)[0] == [1.0, 3.0]
    assert page.evaluate("segs[1].out") == pytest.approx(2.5, abs=0.02)
    assert film_total(page) == pytest.approx(4.5, abs=0.02)
    assert page.locator("#undo").get_attribute("title").startswith("undo: trim")


def test_hovering_a_cut_lights_the_half_shows_the_ghost_and_the_hint(page):
    """Top half, left of the line: the column wears the lit extend cue for shot 1, the
    ghost of shot 2 sits 0.5 s later, the hint under the timeline is up and the counter
    in localStorage says one. Right of the line it is shot 2's in, with nothing after
    shot 2 to push — the ghost is the film's end, moving. Bottom half: the roll bar, no
    ghost. ⇧ while hovering flips the cue. Off the column: nothing lit, the hint gone.
    After five cut hovers the hint waits for a 600 ms dwell."""
    z = zoom(page)
    col = page.locator("#tl .tl-edge").first
    ghost = page.locator("#tl .tl-edge-ghost")
    hint = page.locator("#tl .tl-hint")
    assert col.get_attribute("class") == "tl-edge"
    assert ghost.is_hidden() and hint.is_hidden()
    assert page.evaluate("localStorage.getItem('roughcut.tl.edgeHintSeen')") is None
    hover(page, quadrant(page, 0, "t", "l"))
    assert col.get_attribute("class") == "tl-edge lit top left ext"
    assert page.evaluate("tl.trim.hover") == {"row": "t", "side": "l", "mode": "trim"}
    assert ghost.is_visible()
    b1 = page.evaluate("(() => { const b = document.querySelectorAll('#tl .blk')[1];"
                       " return [parseFloat(b.style.left), parseFloat(b.style.width)]; })()")
    assert page.evaluate("parseFloat(document.querySelector('#tl .tl-edge-ghost').style.left)") \
        == pytest.approx(b1[0] + 0.5 * z, abs=0.5), "shot 2, 0.5 s later"
    assert page.evaluate("parseFloat(document.querySelector('#tl .tl-edge-ghost').style.width)") \
        == pytest.approx(b1[1], abs=0.5)
    assert "end" not in ghost.get_attribute("class")
    assert hint.is_visible()
    text = hint.inner_text()
    assert "top edge" in text and "extend or shorten" in text and "the rest moves" in text, text
    assert "bottom edge" in text and "roll the cut" in text and "⇧ flips" in text, text
    assert page.evaluate("localStorage.getItem('roughcut.tl.edgeHintSeen')") == "1"
    assert page.evaluate("getComputedStyle(document.querySelector('#tl .tl-edge .q.t.l')).cursor") \
        == "col-resize"
    # right of the line: shot 2's in — the film's end is what moves
    hover(page, quadrant(page, 0, "t", "r"))
    assert col.get_attribute("class") == "tl-edge lit top right ext"
    assert ghost.is_visible() and "end" in ghost.get_attribute("class")
    assert page.evaluate("localStorage.getItem('roughcut.tl.edgeHintSeen')") == "1", \
        "the same column, one hover"
    # the bottom half: a roll, no ghost
    hover(page, quadrant(page, 0, "b", "l"))
    assert col.get_attribute("class") == "tl-edge lit bot left roll"
    assert ghost.is_hidden()
    assert page.evaluate("tl.trim.hover")["mode"] == "roll"
    # ⇧ flips the cue where the hand is
    page.keyboard.down("Shift")
    assert col.get_attribute("class") == "tl-edge lit bot left ext"
    assert ghost.is_visible()
    page.keyboard.up("Shift")
    assert col.get_attribute("class") == "tl-edge lit bot left roll"
    # off the column: unlit, the hint gone
    hover(page, page.locator("#tl .blk").nth(0))
    assert col.get_attribute("class") == "tl-edge"
    assert ghost.is_hidden() and hint.is_hidden()
    assert page.evaluate("tl.trim.hover") is None
    # after five, a dwell: the next hover shows nothing at once, then the hint at 600 ms
    page.evaluate("localStorage.setItem('roughcut.tl.edgeHintSeen', '5')")
    hover(page, quadrant(page, 0, "t", "l"))
    assert hint.is_hidden()
    page.wait_for_selector("#tl .tl-hint.on", timeout=2000)
    assert page.evaluate("localStorage.getItem('roughcut.tl.edgeHintSeen')") == "6"


def test_the_tooltip_says_the_rest_moves_on_an_extend_and_roll_on_a_roll(page):
    z = zoom(page)
    drag(page, quadrant(page, 0, "t", "l"), 0.3 * z, release=False)
    page.wait_for_function("Math.abs(segs[0].out - 3.3) < 0.02", timeout=3000)
    tip = page.locator("#tl .tl-tip").inner_text()
    assert tip.startswith("out 3.3") and "+0.3" in tip and "the rest moves" in tip, tip
    page.keyboard.press("Escape")
    page.mouse.up()
    drag(page, quadrant(page, 0, "b", "l"), 0.4 * z, release=False)
    page.wait_for_function("Math.abs(segs[1].in - 0.4) < 0.02", timeout=3000)
    tip = page.locator("#tl .tl-tip").inner_text()
    assert tip.startswith("roll · CLIP_A out 3.4") and "CLIP_B in 0.4" in tip, tip
    page.keyboard.press("Escape")
    page.mouse.up()
    assert ranges(page) == [[1.0, 3.0], [0.0, 2.0]]
    # shortening reads as a minus
    drag(page, quadrant(page, 0, "t", "l"), -0.3 * z, release=False)
    page.wait_for_function("Math.abs(segs[0].out - 2.7) < 0.02", timeout=3000)
    tip = page.locator("#tl .tl-tip").inner_text()
    assert tip.startswith("out 2.7") and "−0.3" in tip and "the rest moves" in tip, tip
    page.keyboard.press("Escape")
    page.mouse.up()
    assert ranges(page) == [[1.0, 3.0], [0.0, 2.0]]


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
    page.evaluate("dock.open('ask')")   # the dock's tool (INTAKE M11)
    page.locator("#story").fill("")
    page.locator("#story").press(",")
    assert page.evaluate("segs[0].in") == dragged
