"""The workspace (redesign wave 1, REDESIGN-GOAL dimension 4; verdict 1 punch 3 and the
client half of punch 5), driven in a real browser.

Karl, 2026-10-14: the board "is not something that I feel meets 'premier pro quality'".
A Premiere user has to be able to name every panel from a screenshot: Project top left,
the Source and Program monitors across the top, the Timeline along the bottom, Effect
Controls down the right. At 1440×900 and 1920×1080:

  * the five titled panels are visible and none overlaps another;
  * each splitter resizes its panels and the size survives a reload;
  * the page never scrolls, no image is ever drawn broken, and nothing throws;
  * the Project panel lists every clip (list and icon views), a click opens the clip in
    the Source monitor, whose In/Out marks are kept per clip and published as
    `window.rcSource`; Insert puts the range in the sequence;
  * the menu bar reaches every tool the old rail held (Selects, Assistant, Audio,
    Effects, Export);
  * with no proxies at all, the monitors wait calmly ("preparing preview…") and never
    show a media error.

Same fixture pattern as test_dock_ui.py: the real uvicorn server on a real port, the
synthetic three-clip bin, the EDL re-seeded per test, a fresh browser per test so the
remembered layout never leaks between tests.
"""

from __future__ import annotations

import json
import shutil
import socket
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

VIEWPORTS = [(1440, 900), (1920, 1080)]
PANELS = {"#pProject": "Project", "#pSource": "Source", "#pProgram": "Program",
          "#pTimeline": "Timeline", "#pInspector": "Effect Controls"}
SEED = {"variant": "T", "title": "test cut", "orient": "none", "story": "",
        "target_s": [5, 20],
        "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
                     {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}]}


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
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_level="error"))
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
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        yield b
        b.close()


def _open(browser, base, project, size, seed=SEED):
    Path(project["edl"]).write_text(json.dumps(seed, indent=1), encoding="utf-8")
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]})
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.goto(base)
    if seed["segments"]:
        pg.wait_for_selector("#tl .blk", timeout=15000)
    pg.wait_for_selector("#projList [data-clip]", timeout=15000)
    return pg


def box(pg, sel):
    return pg.evaluate(f"""(() => {{ const r = document.querySelector('{sel}').getBoundingClientRect();
        return {{x: r.left, y: r.top, w: r.width, h: r.height, r: r.right, b: r.bottom}}; }})()""")


BROKEN_JS = """() => [...document.images].filter((img) => {
    const r = img.getBoundingClientRect(), s = getComputedStyle(img);
    const shown = r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'
      && img.offsetParent !== null;
    return shown && img.complete && img.naturalWidth === 0;
  }).map((img) => img.id || img.className || img.getAttribute('src'))"""


def _overlap(a, b) -> float:
    w = min(a["r"], b["r"]) - max(a["x"], b["x"])
    h = min(a["b"], b["b"]) - max(a["y"], b["y"])
    return max(0.0, w) * max(0.0, h)


# ---------------------------------------------------------------- the five panels

@pytest.mark.parametrize("size", VIEWPORTS, ids=lambda s: f"{s[0]}x{s[1]}")
def test_five_titled_panels_fill_the_window_without_overlap_or_scroll(live_server, project, browser, size):
    pg = _open(browser, live_server, project, size)
    pg.locator("#tl .blk").first.click()                   # a shot in Effect Controls
    pg.wait_for_timeout(1500)                              # posters settle
    boxes = {}
    for sel, title in PANELS.items():
        assert pg.locator(sel).is_visible(), sel
        tab = pg.locator(f"{sel} .ws-head .ws-tab").first
        assert tab.inner_text().strip().startswith(title), (sel, tab.inner_text())
        b = box(pg, sel)
        assert b["w"] >= 150 and b["h"] >= 140, (sel, b)
        assert b["x"] >= 0 and b["y"] >= 0 and b["r"] <= size[0] + 0.5 and b["b"] <= size[1] + 0.5, (sel, b)
        boxes[sel] = b
    names = list(boxes)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            assert _overlap(boxes[a], boxes[b]) < 1, (a, b, boxes[a], boxes[b])
    # Premiere's arrangement: Project left of Source left of Program, all above the
    # Timeline; Effect Controls right of everything
    assert boxes["#pProject"]["r"] <= boxes["#pSource"]["x"] <= boxes["#pProgram"]["x"]
    assert max(boxes[s]["b"] for s in ("#pProject", "#pSource", "#pProgram")) <= boxes["#pTimeline"]["y"]
    assert boxes["#pInspector"]["x"] >= boxes["#pProgram"]["r"]
    assert boxes["#pInspector"]["x"] >= boxes["#pTimeline"]["r"]
    # the window is the workspace: no page scroll either way
    s = pg.evaluate("[document.documentElement.scrollHeight, innerHeight,"
                    " document.documentElement.scrollWidth, innerWidth]")
    assert s[0] <= s[1] and s[2] <= s[3], s
    # the selected shot's strip is in Effect Controls, in view
    assert pg.locator("#inspector .shot").is_visible()
    assert pg.evaluate(BROKEN_JS) == []
    assert pg.errors == []
    pg.context.close()


SPLITS = [("proj", "#pProject", "w", 60), ("src", "#pSource", "w", 60),
          ("insp", "#pInspector", "w", -60), ("top", "#pTimeline", "h", -60)]


@pytest.mark.parametrize("size", VIEWPORTS, ids=lambda s: f"{s[0]}x{s[1]}")
def test_each_splitter_resizes_and_the_size_survives_a_reload(live_server, project, browser, size):
    pg = _open(browser, live_server, project, size)
    want = {}
    for key, panel, dim, d in SPLITS:
        before = box(pg, panel)[dim]
        hb = pg.locator(f".ws-split[data-split={key}]").bounding_box()
        x, y = hb["x"] + hb["width"] / 2, hb["y"] + hb["height"] / 2
        dx, dy = (d, 0) if dim == "w" else (0, d)
        pg.mouse.move(x, y)
        pg.mouse.down()
        pg.mouse.move(x + dx / 2, y + dy / 2, steps=3)
        pg.mouse.move(x + dx, y + dy, steps=3)
        pg.mouse.up()
        after = box(pg, panel)[dim]
        grow = -d if key in ("insp", "top") else d       # dragged left / up: those panels grow
        assert after == pytest.approx(before + grow, abs=3), (key, before, after)
    want = {panel + dim: box(pg, panel)[dim] for _k, panel, dim, _d in SPLITS}
    # the timeline re-fit inside its panel; nothing about the cut changed
    assert pg.locator("#tl .blk").count() == 2
    s = pg.evaluate("[document.documentElement.scrollHeight, innerHeight]")
    assert s[0] <= s[1], s
    pg.reload()
    pg.wait_for_selector("#tl .blk", timeout=15000)
    for key, panel, dim, _d in SPLITS:
        assert box(pg, panel)[dim] == pytest.approx(want[panel + dim], abs=2), (key, panel)
    # double-click puts one back; the menu's reset puts them all back
    pg.locator(".ws-split[data-split=proj]").dblclick()
    assert box(pg, "#pProject")["w"] == pytest.approx(want["#pProjectw"] - 60, abs=3)
    pg.locator("#menubar .mtop", has_text="Window").click()
    pg.locator("#menubar button[data-cmd=reset]").click()
    assert box(pg, "#pInspector")["w"] == pytest.approx(want["#pInspectorw"] - 60, abs=3)
    assert pg.errors == []
    pg.context.close()


# ---------------------------------------------------------------- project and source

def test_the_project_panel_lists_the_footage_and_opens_a_clip_in_the_source_monitor(live_server, project, browser):
    pg = _open(browser, live_server, project, (1440, 900))
    rows = pg.locator("#projList tr.row")
    assert rows.count() == 3
    assert [r.split()[0] for r in rows.all_inner_texts()] == ["CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"]
    head = pg.locator("#projList th").all_inner_texts()
    assert head[:2] == ["Name", "Duration"] and "Status" in head, head
    assert pg.evaluate("tl.tc(6)") in rows.first.inner_text()     # durations in timecode
    assert pg.locator("#projCount").inner_text() == "3 items"
    # the search box filters
    pg.locator("#projQ").fill("clip_b")
    assert pg.locator("#projList tr.row").count() == 1
    pg.locator("#projQ").fill("")
    # a click opens the clip in Source, and the Source monitor plays it
    pg.locator("#projList tr.row", has_text="CLIP_C").click()
    assert pg.locator("#srcTitle").inner_text() == "Source: CLIP_C.MP4"
    pg.wait_for_function("document.querySelector('#srcVideo').readyState >= 2", timeout=10000)
    assert pg.evaluate("rcSource.clip") == "CLIP_C.MP4"
    # I / O with the Source monitor focused mark the clip; the timeline's I / O are not touched
    pg.locator("#pSource .ws-head").click()
    assert pg.evaluate("dock.focused()") == "source"
    pg.evaluate("document.querySelector('#srcVideo').currentTime = 1.0")
    pg.wait_for_function("Math.abs(document.querySelector('#srcVideo').currentTime - 1) < 0.01")
    pg.keyboard.press("i")
    pg.evaluate("document.querySelector('#srcVideo').currentTime = 3.0")
    pg.wait_for_function("Math.abs(document.querySelector('#srcVideo').currentTime - 3) < 0.01")
    pg.keyboard.press("o")
    src = pg.evaluate("({...rcSource})")
    assert src["clip"] == "CLIP_C.MP4" and src["inPoint"] == pytest.approx(1.0) and src["outPoint"] == pytest.approx(3.0), src
    assert pg.locator("#srcDur").inner_text() == "In–Out " + pg.evaluate("tl.tc(2)")
    assert pg.locator("#srcBar .range").is_visible()
    # the marks are the clip's: another clip has none, and coming back finds them
    pg.locator("#projList tr.row", has_text="CLIP_A").click()
    assert pg.evaluate("rcSource.inPoint") is None
    pg.reload()
    pg.wait_for_selector("#projList [data-clip]")
    pg.wait_for_function("rcSource.clip === 'CLIP_A.MP4'", timeout=10000)   # the last clip reopens
    pg.locator("#projList tr.row", has_text="CLIP_C").click()
    assert pg.evaluate("[rcSource.inPoint, rcSource.outPoint]") == [1.0, 3.0]
    # Insert puts the In–Out range into the sequence at the playhead, one undo
    pg.wait_for_selector("#tl .blk")
    n = pg.evaluate("segs.length")
    pg.evaluate("tl.seek(0)")
    pg.locator("#source button[data-src=insert]").click()
    pg.wait_for_function(f"segs.length === {n + 1}")
    assert pg.evaluate("segs.map(s => [s.clip, s.in, s.out])")[0] == ["CLIP_C.MP4", 1.0, 3.0]
    pg.keyboard.press("Control+z")
    pg.wait_for_function(f"segs.length === {n}")
    # the icon view: a thumbnail per clip, remembered
    pg.locator("#projView button[data-view=icon]").click()
    assert pg.locator("#projList .picon").count() == 3
    pg.reload()
    pg.wait_for_selector("#projList .picon")
    assert pg.evaluate("dock.project.view()") == "icon"
    pg.wait_for_timeout(1000)
    assert pg.evaluate(BROKEN_JS) == []
    assert pg.errors == []
    pg.context.close()


def test_the_menu_bar_reaches_every_tool_the_rail_held(live_server, project, browser):
    pg = _open(browser, live_server, project, (1440, 900))
    for item, tool, shows in [("Selects", "bin", "#library"), ("Assistant", "ask", "#askPanel"),
                              ("Audio", "sound", "#musicPanel"), ("Effects", "fx", "#fx"),
                              ("Export", "out", "#outPanel"), ("Effect Controls", "inspect", "#inspector"),
                              ("Project", "project", "#projList")]:
        pg.locator("#menubar .mtop", has_text="Window").click()
        pg.locator("#menubar .menu.open .mdrop button", has_text=item).first.click()
        assert pg.evaluate("dock.current()") == tool, item
        assert pg.locator(shows).is_visible(), item
        assert not pg.locator("#menubar .menu.open").count()
    # a tab click does the same, one tab shown per panel group
    pg.locator(".ws-tab[data-tool=sound]").click()
    assert pg.locator("#musicPanel").is_visible() and not pg.locator("#inspector").is_visible()
    assert pg.locator("#projList").is_visible()           # the other group kept its tab
    # Esc closes an open menu and nothing else
    pg.locator("#menubar .mtop", has_text="File").click()
    assert pg.locator("#menubar .menu.open").count() == 1
    pg.keyboard.press("Escape")
    assert pg.locator("#menubar .menu.open").count() == 0
    assert pg.evaluate("segs.length") == 2
    assert pg.errors == []
    pg.context.close()


def test_an_empty_sequence_offers_its_first_action_in_the_timeline_panel(live_server, project, browser):
    pg = _open(browser, live_server, project, (1440, 900), seed={**SEED, "segments": []})
    pg.wait_for_selector("#tlEmpty .empty", timeout=15000)
    assert pg.locator("#tlEmpty #firstCut").is_visible()
    assert pg.locator("#progEmpty").is_visible()
    assert "sequence is empty" in pg.locator("#inspector").inner_text()
    for sel in PANELS:
        assert pg.locator(sel).is_visible(), sel
    assert pg.errors == []
    pg.context.close()


# ---------------------------------------------------------------- no proxies: a calm wait

def test_without_proxies_the_monitors_wait_calmly_and_no_image_is_broken(live_server, project, browser, tmp_path):
    """Verdict 1: "CLIP_02: that proxy would not open — it may still be building (code 4) —
    MEDIA_ELEMENT_ERROR" over the program monitor, and every bin card a torn image with its
    alt text. A bin with no proxies at all now shows neutral frames and "preparing
    preview…", and no media error, anywhere."""
    import server
    work = tmp_path / "work"
    work.mkdir()
    server.configure(project["edl"], project["footage"], project["sidecars"], work,
                     proxies=False, assets=project["assets"])
    try:
        pg = _open(browser, live_server, project, (1440, 900))
        pg.locator("#tl .blk").first.click()
        pg.locator("#playCut").click()
        pg.locator("#projList tr.row", has_text="CLIP_B").click()
        # Either the server plays something before the proxy (the MEDIA lane's original or
        # 360p preview) or the monitor waits calmly — never an error, never black.
        for mon, wait, vids in (("#pProgram", "#monWait", "#pv0, #pv1"), ("#pSource", "#srcWait", "#srcVideo")):
            pg.wait_for_function(
                f"""!document.querySelector('{wait}').hidden
                    || [...document.querySelectorAll('{vids}')].some(v => v.readyState >= 2)""",
                timeout=15000)
            if pg.locator(wait).is_visible():
                assert "preparing preview" in pg.locator(mon).inner_text(), mon
                # the poster (or the neutral frame) is up behind the line, not a black box
                assert pg.evaluate(f"[...document.querySelectorAll('{mon} img')].some(i => !i.hidden && i.naturalWidth > 0)"), mon
        pg.wait_for_timeout(1500)
        text = pg.locator("body").inner_text()
        for bad in ("MEDIA_ELEMENT_ERROR", "code 4", "would not open", "DEMUXER", "PIPELINE_ERROR"):
            assert bad not in text, bad
        assert pg.evaluate(BROKEN_JS) == []
        assert pg.errors == []
        pg.context.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)
        server.configure(project["edl"], project["footage"], project["sidecars"],
                         project["work"], proxies=False, assets=project["assets"])
