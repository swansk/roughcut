"""REDESIGN-GOAL dimension 5 (verdict 1, punch 7 and the markers / timecode of punch 6):
Premiere's keyboard on the timeline, driven in a real browser.

`,` inserts and `.` overwrites at the playhead from the source (the Source monitor's
`window.rcSource`, else the bin's selected clip); ⌥← / ⌥→ and Ctrl+← / Ctrl+→ trim the
selected edit by a frame; V C B N Y A switch the tools and each tool acts; ⇧Del ripple
deletes; I / O mark the sequence; M adds a marker, ⇧M / Ctrl+⇧M walk them; the ruler,
the readout and the end mark read HH:MM:SS:FF at the sequence rate; the plain wheel
scrolls and Alt / Ctrl + wheel zoom; the map says Ctrl, not ⌘, off a Mac. Every
assertion is on the timeline's state (`segs`, `tl.state`), not the module's word.

Same fixture pattern as test_timeline_keys.py: the real uvicorn server on a real port,
the synthetic three-clip bin (6 s clips), the EDL re-seeded per test with two shots of
2 s — CLIP_A 1.0–3.0, CLIP_B 0.0–2.0. Skipped when playwright is absent.
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

FPS = 23.976
TC = re.compile(r"^\d\d:\d\d:\d\d:\d\d$")


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
    config = uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error")
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
        pg.wait_for_selector("#tlKeys", state="attached")
        pg.evaluate("localStorage.clear()")          # markers and in / out start empty
        pg.evaluate("dock.open('bin')")   # the workspace opens on Project; these drive its Selects tab
        pg.evaluate("tl.setMarkers([]); tl.setSeqIn(null); tl.setSeqOut(null)")
        yield pg
        browser.close()


def ranges(page) -> list[list]:
    return page.evaluate("segs.map(s => [s.clip, s.in, s.out])")


def total(page) -> float:
    return page.evaluate("tl.total()")


def undo_title(page) -> str:
    return page.locator("#undo").get_attribute("title") or ""


def toast(page, text: str) -> None:
    page.wait_for_function(
        f"document.querySelector('#toast').textContent.includes({json.dumps(text)})", timeout=3000)


def source(page, clip: str, a: float | None, b: float | None) -> None:
    """The Source monitor's selection, as the workspace lane publishes it."""
    page.evaluate(f"window.rcSource = {json.dumps({'clip': clip, 'inPoint': a, 'outPoint': b})}")


def reveal(page) -> None:
    page.evaluate("document.querySelector('#tl').scrollIntoView({block: 'end'})")


def centre(page, loc) -> tuple[float, float]:
    reveal(page)
    box = loc.bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


# ------------------------------------------------------------------ , and .

def test_comma_inserts_the_source_at_the_playhead_and_pushes_the_rest(page):
    """Premiere's insert: the shot under the playhead splits, the source's In–Out goes
    in between, the rest of the film moves later by its length. One undo entry."""
    source(page, "CLIP_C.MP4", 0.5, 2.0)
    page.evaluate("tl.seek(1.0)")                    # 1 s into CLIP_A's shot = clip 2.0
    page.keyboard.press(",")
    assert ranges(page) == [["CLIP_A.MP4", 1.0, 2.0], ["CLIP_C.MP4", 0.5, 2.0],
                            ["CLIP_A.MP4", 2.0, 3.0], ["CLIP_B.MP4", 0.0, 2.0]]
    assert total(page) == pytest.approx(5.5)
    assert undo_title(page).startswith("undo: insert")
    assert page.evaluate("tl.state.anchor === segs[1].id"), "the new shot is selected"
    assert page.evaluate("tl.state.playhead") == pytest.approx(2.5, abs=0.01), \
        "the playhead lands after the inserted shot"
    page.keyboard.press("Control+z")
    assert ranges(page) == [["CLIP_A.MP4", 1.0, 3.0], ["CLIP_B.MP4", 0.0, 2.0]], "one undo"
    # no In / Out on the source: the whole clip
    source(page, "CLIP_C.MP4", None, None)
    page.evaluate("tl.seek(2.0)")                    # on the cut: no split
    page.keyboard.press(",")
    assert ranges(page) == [["CLIP_A.MP4", 1.0, 3.0], ["CLIP_C.MP4", 0.0, 6.0],
                            ["CLIP_B.MP4", 0.0, 2.0]]


def test_period_overwrites_the_range_from_the_playhead(page):
    """Premiere's overwrite: the source covers the film from the playhead for its own
    length — the shot it starts in keeps its head, the one it ends in loses its head —
    and the film's length holds; past the end it appends."""
    source(page, "CLIP_C.MP4", 0.5, 2.0)             # 1.5 s
    page.evaluate("tl.seek(1.0)")
    page.keyboard.press(".")
    assert ranges(page) == [["CLIP_A.MP4", 1.0, 2.0], ["CLIP_C.MP4", 0.5, 2.0],
                            ["CLIP_B.MP4", 0.5, 2.0]]
    assert total(page) == pytest.approx(4.0), "an overwrite inside the film keeps its length"
    assert undo_title(page).startswith("undo: overwrite")
    assert page.evaluate("tl.state.playhead") == pytest.approx(2.5, abs=0.01)
    # a range that swallows whole shots removes them
    page.keyboard.press("Control+z")
    source(page, "CLIP_C.MP4", 0.0, 4.0)             # 4 s from 0: the whole film
    page.evaluate("tl.seek(0)")
    page.keyboard.press(".")
    assert ranges(page) == [["CLIP_C.MP4", 0.0, 4.0]]
    page.keyboard.press("Control+z")
    # at the end of the film it appends
    source(page, "CLIP_C.MP4", 0.5, 2.0)
    page.evaluate("tl.seek(tl.total())")
    page.keyboard.press(".")
    assert ranges(page)[-1] == ["CLIP_C.MP4", 0.5, 2.0]
    assert total(page) == pytest.approx(5.5)


def test_comma_takes_the_bins_selected_clip_without_a_source_monitor(page):
    """No Source monitor selection: the clip selected in the bin goes in, its range the
    one "+ add" would use. Nothing selected anywhere: a toast says what to do."""
    page.evaluate("delete window.rcSource")
    page.evaluate("tl.seek(1.0)")
    page.evaluate("document.querySelectorAll('#library .keep.sel').forEach(r => r.classList.remove('sel'))")
    page.keyboard.press(",")
    toast(page, "select a clip in the bin")
    assert len(ranges(page)) == 2
    out = page.evaluate("""() => fetch('/api/selects', {method: 'PUT',
        headers: {'content-type': 'application/json'},
        body: JSON.stringify({selects: [{clip: 'CLIP_C.MP4', start: 0.5, end: 2.0, why: 'a crash'}]})
      }).then(r => r.json())""")
    assert out.get("ok"), out
    page.reload()
    page.wait_for_selector("#library .keep")
    page.evaluate("dock.open('bin')")
    card = page.locator('#library .keep[title^="CLIP_C"]')
    card.click()
    assert "sel" in (card.get_attribute("class") or "")
    page.evaluate("document.activeElement && document.activeElement.blur()")
    page.evaluate("tl.seek(2.0)")
    page.keyboard.press(",")
    assert ranges(page) == [["CLIP_A.MP4", 1.0, 3.0], ["CLIP_C.MP4", 0.5, 2.0],
                            ["CLIP_B.MP4", 0.0, 2.0]]
    toast(page, "from the bin")


# ------------------------------------------------------------------ edge nudges

def test_alt_and_ctrl_arrows_trim_the_selected_edit_by_a_frame(page):
    """The 1-frame edge nudge moved off `,` `.` to Premiere's trim keys. A frame is one
    at the sequence rate (23.976): 0.0417 s, kept at the foundation's 0.01 s."""
    first = page.evaluate("segs[0].id")
    page.evaluate(f"tl.select(['{first}'])")         # the active edge: the selected shot's out
    page.keyboard.press("Alt+ArrowRight")
    assert page.evaluate("segs[0].out") == round(3.0 + 1 / FPS, 2)
    assert undo_title(page).startswith("undo: nudge")
    page.keyboard.press("Alt+ArrowLeft")
    assert page.evaluate("segs[0].out") == 3.0
    page.keyboard.press("Control+ArrowLeft")
    assert page.evaluate("segs[0].out") == round(3.0 - 1 / FPS, 2)
    page.keyboard.press("Control+ArrowRight")
    assert page.evaluate("segs[0].out") == 3.0
    page.keyboard.press("Alt+Shift+ArrowRight")      # five frames
    assert page.evaluate("segs[0].out") == round(3.0 + 5 / FPS, 2)
    assert page.evaluate("segs[0].in") == 1.0, "only the active edge moves"
    page.keyboard.press("Control+z")
    assert page.evaluate("segs[0].out") == 3.0
    # the plain arrows still move the playhead only, a frame at the sequence rate
    page.evaluate("tl.seek(1.0)")
    page.keyboard.press("ArrowRight")
    assert page.evaluate("tl.state.playhead") == pytest.approx(1.0 + 1 / FPS, abs=0.002)
    assert page.evaluate("segs[0].out") == 3.0


# ------------------------------------------------------------------ the tools

def test_v_c_b_n_y_a_switch_the_tool_and_the_button_says_which(page):
    btn = page.locator("#tl .tl-toolbtn")
    for key, tool, glyph in [("b", "ripple", "⇥"), ("n", "roll", "⇹"), ("y", "slip", "↔"),
                             ("a", "track", "⇉"), ("v", "select", "↖")]:
        page.keyboard.press(key)
        assert page.evaluate("tl.state.tool") == tool, key
        assert f"tl-tool-{tool}" in page.locator("#tl").get_attribute("class")
        assert btn.inner_text() == glyph
        assert f"({key.upper()})" in btn.get_attribute("title")
    # the button opens the six as a menu; a pick switches
    btn.click()
    page.locator("#tl .tl-toolmenu button[data-tool=roll]").click()
    assert page.evaluate("tl.state.tool") == "roll"
    assert page.locator("#tl .tl-toolmenu").is_hidden()


def test_c_cuts_at_the_playhead_and_the_razor_cuts_where_clicked(page):
    page.evaluate("tl.seek(1.0)")
    page.keyboard.press("c")                         # the default action C always had
    assert page.evaluate("tl.state.tool") == "razor"
    assert ranges(page)[:2] == [["CLIP_A.MP4", 1.0, 2.0], ["CLIP_A.MP4", 2.0, 3.0]]
    # a click on CLIP_B's shot cuts it at the click
    blk = page.locator("#tl .blk").nth(2)
    x, y = centre(page, blk)
    page.mouse.click(x, y)
    assert len(ranges(page)) == 4
    a, b = ranges(page)[2:]
    assert a[0] == b[0] == "CLIP_B.MP4" and a[2] == b[1] and 0.6 < a[2] < 1.4
    assert undo_title(page).startswith("undo: split")
    # Ctrl+K cuts at the playhead without changing the tool
    page.keyboard.press("v")
    page.evaluate("tl.select([]); tl.seek(0.5)")
    page.keyboard.press("Control+k")
    assert len(ranges(page)) == 5 and page.evaluate("tl.state.tool") == "select"


def test_a_selects_forward_and_shift_delete_ripple_deletes_it(page):
    page.evaluate("tl.seek(1.0)")
    page.keyboard.press("Control+k")                 # three shots
    page.keyboard.press("a")
    x, y = centre(page, page.locator("#tl .blk").nth(1))
    page.mouse.click(x, y)
    ids = page.evaluate("segs.map(s => s.id)")
    assert sorted(page.evaluate("[...tl.state.sel]")) == sorted(ids[1:]), \
        "the clicked shot and every one after"
    page.keyboard.press("Shift+Delete")
    assert ranges(page) == [["CLIP_A.MP4", 1.0, 2.0]]
    page.keyboard.press("Control+z")
    assert len(ranges(page)) == 3


def test_b_and_n_make_both_halves_of_an_edge_trim_or_roll(page):
    """With the selection tool the top half extends and the bottom rolls; the ripple
    edit tool makes both halves trim, the rolling edit tool both roll."""
    def mode_over(row: str) -> str:
        x, y = centre(page, page.locator(f"#tl .tl-edge").nth(0).locator(f".q.{row}.l"))
        page.mouse.move(x, y)
        page.wait_for_function("tl.trim.hover", timeout=3000)
        return page.evaluate("tl.trim.hover.mode")

    assert (mode_over("t"), mode_over("b")) == ("trim", "roll")
    page.keyboard.press("b")
    assert (mode_over("t"), mode_over("b")) == ("trim", "trim")
    page.keyboard.press("n")
    assert (mode_over("t"), mode_over("b")) == ("roll", "roll")
    # a roll with N from the top half: the cut moves, the film's length holds
    page.evaluate("tl.trim.setMagnet(false)")
    page.mouse.move(0, 0)
    x, y = centre(page, page.locator("#tl .tl-edge").nth(0).locator(".q.t.l"))
    z = page.evaluate("tl.state.zoom")
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 0.4 * z, y, steps=6)
    page.mouse.up()
    assert total(page) == pytest.approx(4.0, abs=0.011)
    assert page.evaluate("segs[0].out") == pytest.approx(3.4, abs=0.05)
    assert page.evaluate("segs[1].in") == pytest.approx(0.4, abs=0.05)
    assert undo_title(page).startswith("undo: roll")


def test_y_slips_a_shot_by_dragging_its_body(page):
    page.keyboard.press("y")
    z = page.evaluate("tl.state.zoom")
    x, y = centre(page, page.locator("#tl .blk").nth(0))
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 0.5 * z, y, steps=6)
    page.mouse.up()
    a_in, a_out = page.evaluate("[segs[0].in, segs[0].out]")
    assert a_in == pytest.approx(1.5, abs=0.05) and a_out - a_in == pytest.approx(2.0, abs=0.011)
    assert [r[0] for r in ranges(page)] == ["CLIP_A.MP4", "CLIP_B.MP4"], "nothing moved"
    assert undo_title(page).startswith("undo: slip")


# ------------------------------------------------------------------ marks and markers

def test_i_and_o_mark_the_sequence_and_show_on_the_ruler(page):
    page.evaluate("tl.seek(0.5)")
    page.keyboard.press("i")
    page.evaluate("tl.seek(3.0)")
    page.keyboard.press("o")
    assert page.evaluate("tl.state.seqIn") == pytest.approx(0.5, abs=0.5 / FPS)
    assert page.evaluate("tl.state.seqOut") == pytest.approx(3.0, abs=0.5 / FPS)
    io = page.locator("#tl .tl-ruler .tl-inout")
    assert io.count() == 1 and "has-in" in io.get_attribute("class")
    assert io.get_attribute("title").startswith("sequence in 00:00:00:12")
    assert page.locator("#picker").count() == 0 or page.locator("#picker").is_hidden(), \
        "O is Mark Out on the board, not the project picker"
    page.keyboard.press("Alt+i")
    assert page.evaluate("tl.state.seqIn") is None
    page.keyboard.press("Control+Shift+x")
    assert page.evaluate("tl.state.seqOut") is None
    assert page.locator("#tl .tl-ruler .tl-inout").count() == 0


def test_m_adds_a_marker_and_shift_m_jumps_to_it(page):
    page.evaluate("tl.seek(1.5)")
    page.keyboard.press("m")
    page.evaluate("tl.seek(3.0)")
    page.keyboard.press("m")
    marks = page.evaluate("tl.markers()")
    assert len(marks) == 2
    assert marks[0] == pytest.approx(1.5, abs=0.5 / FPS) and marks[1] == pytest.approx(3.0, abs=0.5 / FPS)
    assert page.locator("#tl .tl-ruler .tl-marker").count() == 2
    page.keyboard.press("m")                         # one marker per frame
    assert len(page.evaluate("tl.markers()")) == 2
    page.evaluate("tl.seek(0.2)")
    page.keyboard.press("Shift+M")
    assert page.evaluate("tl.state.playhead") == pytest.approx(marks[0], abs=0.011)
    page.keyboard.press("Shift+M")
    assert page.evaluate("tl.state.playhead") == pytest.approx(marks[1], abs=0.011)
    page.keyboard.press("Control+Shift+M")           # previous
    assert page.evaluate("tl.state.playhead") == pytest.approx(marks[0], abs=0.011)
    # a click on a marker goes there
    page.evaluate("tl.seek(0)")
    page.locator("#tl .tl-ruler .tl-marker").nth(1).click()
    assert page.evaluate("tl.state.playhead") == pytest.approx(marks[1], abs=0.011)
    # they outlive a reload
    page.reload()
    page.wait_for_selector("#tl .tl-ruler .tl-marker")
    assert page.evaluate("tl.markers()") == pytest.approx(marks)
    # Ctrl+Alt+M clears the one at the playhead
    page.evaluate(f"tl.seek({marks[0]})")
    page.keyboard.press("Control+Alt+m")
    assert page.evaluate("tl.markers()") == pytest.approx([marks[1]])


# ------------------------------------------------------------------ timecode

def test_the_ruler_the_readout_and_the_end_read_timecode(page):
    tc = page.locator("#tl .tl-tc")
    assert TC.match(tc.inner_text()), tc.inner_text()
    page.evaluate("tl.seek(2.0)")
    assert tc.inner_text() == "00:00:02:00"
    page.evaluate("tl.seek(1.5)")
    assert tc.inner_text() == "00:00:01:12"          # 36 frames at 23.976 → 1 s 12 f
    page.keyboard.press("ArrowRight")
    assert tc.inner_text() == "00:00:01:13"
    labels = page.evaluate(
        "[...document.querySelectorAll('#tl .tl-ruler .major label')].map(l => l.textContent)")
    assert all(TC.match(x) for x in labels), labels
    assert labels[:3] == ["00:00:00:00", "00:00:01:00", "00:00:02:00"], labels
    assert page.locator("#tl .tl-total").inner_text() == "00:00:04:00"
    # zoomed in far enough, the ruler counts frames
    page.evaluate("tl.zoomTo(3000)")
    labels = page.evaluate(
        "[...document.querySelectorAll('#tl .tl-ruler .major label')].map(l => l.textContent)")
    assert labels[:2] == ["00:00:00:00", "00:00:00:01"], labels
    # the sequence rate is the project's when it says one
    page.evaluate("tl.setFps(30)")
    page.evaluate("tl.seek(1.5)")
    assert tc.inner_text() == "00:00:01:15"


# ------------------------------------------------------------------ the wheel

def test_the_wheel_scrolls_and_alt_or_ctrl_wheel_zooms(page):
    page.evaluate("tl.zoomTo(tl.state.zoom * 4); tl.el.view.scrollLeft = 0")
    z0 = page.evaluate("tl.state.zoom")
    x, y = centre(page, page.locator("#tl .tl-view"))
    page.mouse.move(x, y)
    page.mouse.wheel(0, 200)
    page.wait_for_function("tl.el.view.scrollLeft > 0", timeout=3000)
    assert page.evaluate("tl.state.zoom") == z0, "a plain wheel scrolls, never zooms"
    page.keyboard.down("Alt")
    page.mouse.wheel(0, -100)
    page.keyboard.up("Alt")
    z1 = page.evaluate("tl.state.zoom")
    assert z1 > z0, "Alt + wheel zooms in"
    page.keyboard.down("Control")
    page.mouse.wheel(0, 100)
    page.keyboard.up("Control")
    assert page.evaluate("tl.state.zoom") < z1, "Ctrl + wheel zooms out"


# ------------------------------------------------------------------ the map

def test_the_map_says_ctrl_on_linux_and_lists_the_premiere_keys(page):
    assert not page.evaluate("tl.isMac")
    keys = page.evaluate("[...document.querySelectorAll('#tlKeys kbd')].map(k => k.textContent)")
    for k in [",", ".", "V", "C", "B", "N", "Y", "A", "M", "Shift+M", "Ctrl+Shift+M", "I", "O",
              "Alt+←", "Ctrl+Z", "Ctrl+Shift+Z", "Ctrl+K", "Shift+Del"]:
        assert k in keys, f"{k} missing from the map"
    text = page.locator("#tlKeys").inner_text()
    assert "⌘" not in text and "⌥" not in text
    assert "insert" in text and "overwrite" in text and "marker" in text
    page.keyboard.press("?")
    page.wait_for_selector("#keysOverlay:not([hidden])")
    assert "⌘" not in page.locator("#keyTable").inner_text()
    page.evaluate("tl.seek(1.0)")
    page.keyboard.press("Escape")
    page.keyboard.press("Control+k")
    assert "Ctrl+Z" in undo_title(page) and "⌘" not in undo_title(page)
