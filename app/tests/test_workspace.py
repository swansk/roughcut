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


# ---------------------------------------------------------------- the finish (verdict 2, punch 9)
#
# "Looks like a shipped pro tool" (REDESIGN-GOAL dimension 9, scored 4 in verdict 2): no
# truncated chrome ("not inde…", "Indexi… 9.", a tab cut to "Exp"), no browser-blue
# underlined links, no ▶ parked on the picture, a Program picture that fills its panel,
# and /open cards with the Project panel's posters.

# Two jobs running in the app bar at once, the most it has to hold.
FAKE_JOBS = [
    {"id": "fake-index", "kind": "index", "state": "running", "label": "Indexing the footage",
     "pct": 9.4, "eta_s": 400, "eta_source": "measured", "milestone": "heard 3/12", "detail": "",
     "elapsed_s": 30},
    {"id": "fake-render", "kind": "render", "state": "running", "label": "Rendering the quick look",
     "pct": 86, "eta_s": 20, "eta_source": "measured", "milestone": "encoding", "detail": "",
     "elapsed_s": 60},
]

# Every element under the app bar, a panel's tab strip or a Project row, and every
# monitor readout, whose content is wider than its box. The app bar may shorten a text
# that has no room (a job's name, Next's sentence) only on purpose: ellipsized, with the
# whole text in a hover. Tabs, list cells and readouts may not shorten at all.
OVERFLOW_JS = """() => {
  const shown = (el) => { const s = getComputedStyle(el);
    return el.getClientRects().length && s.display !== 'none' && s.visibility !== 'hidden'; };
  const out = [];
  const check = (sel, mayEllipsize) => {
    for (const el of document.querySelectorAll(sel)) {
      if (!shown(el) || el.clientWidth === 0 || el.scrollWidth <= el.clientWidth + 0.5) continue;
      const titled = el.closest('[title]');
      if (mayEllipsize && getComputedStyle(el).textOverflow === 'ellipsis' && titled && titled.title.trim()) continue;
      out.push(`${sel} ${el.id || el.className}: "${el.textContent.trim().slice(0, 40)}" ${el.scrollWidth} > ${el.clientWidth}`);
    }
  };
  check('header, header *', true);
  check('.ws-tabs, .ws-tab, .ws-tab *', false);
  check('.plist td, .plist th', false);
  check('.transport, .transport *', false);
  return out;
}"""


def _clips_with_one_unindexed(route):
    """/api/clips as the server says it, with CLIP_C not yet indexed: the longest word
    the Project panel's Status column has to fit."""
    resp = route.fetch()
    body = resp.json()
    for c in body.get("clips", []):
        if c["clip"] == "CLIP_C.MP4":
            c["analysed"] = False
    route.fulfill(response=resp, body=json.dumps(body), content_type="application/json")


@pytest.mark.parametrize("size", VIEWPORTS, ids=lambda s: f"{s[0]}x{s[1]}")
def test_no_chrome_is_cut_off(live_server, project, browser, size):
    Path(project["edl"]).write_text(json.dumps(SEED, indent=1), encoding="utf-8")
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]})
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.route("**/api/jobs", lambda r: r.fulfill(status=200, content_type="application/json",
                                                 body=json.dumps({"jobs": FAKE_JOBS})))
    pg.route("**/api/clips", _clips_with_one_unindexed)
    pg.goto(live_server)
    pg.wait_for_selector("#tl .blk", timeout=15000)
    pg.wait_for_selector("#projList tr.row", timeout=15000)
    pg.wait_for_function("document.querySelectorAll('#progress .job').length === 2", timeout=10000)
    assert pg.locator("#progress").is_visible()
    assert "not indexed" in pg.locator("#projList tr.row", has_text="CLIP_C").inner_text()
    # badges on two of Effect Controls' neighbours, the widest that tab strip gets
    pg.evaluate("dock.badge('fx', 3); dock.badge('ask', 1)")
    pg.locator("#tl .blk").first.click()
    pg.wait_for_timeout(800)
    assert pg.evaluate(OVERFLOW_JS) == []
    # every tab of every panel is wholly inside its strip
    clipped = pg.evaluate("""() => [...document.querySelectorAll('.ws-tab')].filter(t => t.getClientRects().length)
        .filter(t => { const s = t.closest('.ws-tabs').getBoundingClientRect(), r = t.getBoundingClientRect();
                       return r.left < s.left - 0.5 || r.right > s.right + 0.5; })
        .map(t => t.textContent.trim())""")
    assert clipped == []
    # the job's numbers are never the part that gives: "9% · ~7 min left", whole
    nums = pg.locator("#progress .job[data-job=fake-index] .jnums")
    assert nums.is_visible() and nums.inner_text().startswith("9%"), nums.inner_text()
    # the app bar's children sit side by side in one row, none over another
    kids = pg.evaluate("""() => [...document.querySelectorAll('header > *')].filter(e => e.getClientRects().length)
        .map(e => { const r = e.getBoundingClientRect(); return [e.id || e.className, r.left, r.right, r.top, r.bottom]; })""")
    for (a, b) in zip(kids, kids[1:]):
        assert a[2] <= b[1] + 0.5, (a, b)
    assert all(k[4] <= 50 for k in kids), kids
    # icon-only buttons say what they do on hover
    bare = pg.evaluate("""() => [...document.querySelectorAll('header button, .ws-panel button')]
        .filter(b => b.getClientRects().length && (b.innerText || '').trim().length <= 2
                     && !b.title && !b.getAttribute('aria-label'))
        .map(b => b.id || b.className || b.outerHTML.slice(0, 60))""")
    assert bare == []
    assert errors == []
    ctx.close()


UA_LINK_JS = """() => [...document.querySelectorAll('a')].filter(a => a.getClientRects().length).map(a => {
    const s = getComputedStyle(a);
    return {text: a.textContent.trim().slice(0, 40), color: s.color, line: s.textDecorationLine}; })"""
UA_LINK_COLOURS = {"rgb(0, 0, 238)", "rgb(85, 26, 139)", "rgb(0, 0, 255)", "rgb(238, 0, 0)"}


def test_no_link_wears_the_browsers_blue_underline(live_server, project, browser):
    """Verdict 2: "Open a footage folder", "The footage →" and "keep moments on the pass →"
    were default blue underlined links. A link on the board is one of its own text buttons."""
    seen = []
    pg = _open(browser, live_server, project, (1440, 900))
    for tool in ("project", "bin", "inspect", "fx", "sound", "ask", "out"):
        pg.evaluate(f"dock.open('{tool}')")
        pg.wait_for_timeout(250)
        seen += pg.evaluate(UA_LINK_JS)
    pg.context.close()
    # the empty sequence's first action, and the empty bin's
    pg = _open(browser, live_server, project, (1440, 900), seed={**SEED, "segments": []})
    pg.wait_for_selector("#tlEmpty .empty", timeout=15000)
    seen += pg.evaluate(UA_LINK_JS)
    pg.evaluate("""(() => { const b = document.querySelector('#projList');
        b.innerHTML = '<div class="pempty">No footage in this bin yet. <a href="/open">Open a footage folder</a></div>'; })()""")
    seen += pg.evaluate(UA_LINK_JS)
    assert any(s["text"] == "Open a footage folder" for s in seen)
    for s in seen:
        assert s["color"] not in UA_LINK_COLOURS, s
        assert "underline" not in s["line"], s
    pg.context.close()


def test_the_program_picture_fills_its_panel_at_1440x900(live_server, project, browser):
    """Rubric log 2026-10-15 (wave 2): the replacement for the dropped "Program >= 425 px
    tall" check. In the default layout at 1440×900 the Program picture is at least 380 px
    wide and the black letterbox above and below it is at most 30 % of its room (verdict 2
    measured 389×219 in a 447 px tall room: 51 %). The ▶ is not parked on the picture: it
    shows only while the pointer is over a paused picture."""
    pg = _open(browser, live_server, project, (1440, 900))
    m = pg.evaluate("""() => { const room = document.querySelector('#pProgram .screenbox').getBoundingClientRect(),
        pic = document.querySelector('#pProgram .screen').getBoundingClientRect();
        return {w: pic.width, h: pic.height, rw: room.width, rh: room.height,
                left: pic.left - room.left, right: room.right - pic.right,
                top: pic.top - room.top, bottom: room.bottom - pic.bottom}; }""")
    assert m["w"] >= 380, m
    assert 1 - m["h"] / m["rh"] <= 0.30, m
    assert abs(m["left"] - m["right"]) <= 1 and abs(m["top"] - m["bottom"]) <= 1, m
    # the Source monitor is the same size (Premiere's two monitors, side by side)
    assert box(pg, "#pSource")["w"] == pytest.approx(box(pg, "#pProgram")["w"], abs=2)
    # the ▶: there at rest for the pointer, invisible until it is over the picture
    big = pg.locator("#bigPlay")
    assert big.count() == 1 and not pg.evaluate("document.querySelector('#bigPlay').hidden")
    pg.mouse.move(5, 5)
    pg.wait_for_timeout(250)
    assert pg.evaluate("getComputedStyle(document.querySelector('#bigPlay')).opacity") == "0"
    pic = box(pg, "#pProgram .screen")
    pg.mouse.move(pic["x"] + pic["w"] / 2, pic["y"] + pic["h"] / 2)
    pg.wait_for_timeout(300)
    assert pg.evaluate("getComputedStyle(document.querySelector('#bigPlay')).opacity") == "1"
    # a layout the editor dragged still wins over the defaults
    pg.evaluate("localStorage.setItem('roughcut.ws.layout', JSON.stringify({proj: 0.2, insp: 0.245, src: null, top: 0.6}))")
    pg.reload()
    pg.wait_for_selector("#tl .blk", timeout=15000)
    assert box(pg, "#pProgram")["h"] > 480
    assert pg.errors == []
    pg.context.close()


def test_open_cards_show_the_project_panels_posters(live_server, project, browser, tmp_path):
    """Verdict 2: /open still showed blank stripes for thumbnails after 6 min while the
    Project panel had every poster. On a fresh work dir (no proxies, no posters yet) every
    /open card has a loaded picture within 10 s, at the Project panel's own URL."""
    import server
    work = tmp_path / "work"
    work.mkdir()
    server.configure(project["edl"], project["footage"], project["sidecars"], work,
                     proxies=False, assets=project["assets"])
    try:
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        t0 = time.time()
        pg.goto(live_server + "/open")
        pg.wait_for_selector("#sessions .card", timeout=10000)
        pg.wait_for_function("""[...document.querySelectorAll('#sessions .card')].every(c => {
            const i = c.querySelector('.frame img'); return i && i.complete && i.naturalWidth > 0; })""",
                             timeout=max(1000, int((10 - (time.time() - t0)) * 1000)))
        cards = pg.evaluate("""() => [...document.querySelectorAll('#sessions .card')].map(c => [c.dataset.clip,
            (c.querySelector('.frame img') || {}).getAttribute ? c.querySelector('.frame img').getAttribute('src') : null,
            c.querySelectorAll('.ph').length])""")
        assert len(cards) == 3, cards
        for clip, src, ph in cards:
            assert src == f"/media/poster/{clip.rsplit('.', 1)[0]}.jpg?t=0.00" and ph == 0, (clip, src, ph)
        # the same URL the Project panel's icon view asks for
        board = ctx.new_page()
        board.goto(live_server)
        board.wait_for_selector("#projList [data-clip]", timeout=15000)
        board.locator("#projView button[data-view=icon]").click()
        icons = board.evaluate("[...document.querySelectorAll('#projList .picon img')].map(i => i.getAttribute('src'))")
        assert sorted(icons) == sorted(src for _c, src, _p in cards)
        assert errors == []
        ctx.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)
        server.configure(project["edl"], project["footage"], project["sidecars"],
                         project["work"], proxies=False, assets=project["assets"])


def test_the_workspace_styles_are_a_module_not_an_inline_block(live_server):
    """Punch 9: the workspace CSS moved out of index.html to /modules/workspace.css."""
    import urllib.request
    html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text(encoding="utf-8")
    assert "<style" not in html
    assert 'href="/modules/workspace.css"' in html
    with urllib.request.urlopen(live_server + "/modules/workspace.css") as r:
        assert r.headers["content-type"].startswith("text/css")
        css = r.read().decode("utf-8")
    assert "#ws {" in css and ".ws-tab" in css
