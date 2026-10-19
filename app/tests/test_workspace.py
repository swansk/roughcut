"""The workspace (redesign wave 1, REDESIGN-GOAL dimension 4; verdict 1 punch 3 and the
client half of punch 5), driven in a real browser.

Karl, 2026-10-14: the board "is not something that I feel meets 'premier pro quality'".
A Premiere user has to be able to name every panel from a screenshot. Since wave 4
(verdict 4 punch 3) the arrangement is Premiere's Editing workspace: the Source and
Program monitors share the top row, half each; Project bottom left, the Timeline bottom
centre, Effect Controls bottom right. At 1440×900 and 1920×1080:

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
    # Premiere's Editing arrangement: Source left of Program across the top row, the two
    # sharing its whole width; Project | Timeline | Effect Controls below them
    src, prog = boxes["#pSource"], boxes["#pProgram"]
    assert src["r"] <= prog["x"] and abs(src["y"] - prog["y"]) < 1
    assert src["w"] + prog["w"] >= size[0] - 8 - 4 - 1, (src, prog)      # #ws padding, one splitter
    low = [boxes[s] for s in ("#pProject", "#pTimeline", "#pInspector")]
    assert max(src["b"], prog["b"]) <= min(b["y"] for b in low)
    assert low[0]["r"] <= low[1]["x"] and low[1]["r"] <= low[2]["x"], low
    assert all(abs(b["y"] - low[0]["y"]) < 1 for b in low), low
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
    # Premiere's columns, every one (a narrow panel scrolls them sideways: wave 13 BINLOOK2)
    assert head[:2] == ["Name", "Frame Rate"] and "Media Duration" in head and "Status" in head, head
    assert pg.evaluate("tl.tc(6)") in rows.first.inner_text()     # durations in timecode
    # Premiere's count: every item in the bin, its three clips and its one sequence (wave 12
    # BINLOOK: the cut is a sequence in the bin)
    pg.wait_for_function("document.querySelector('#projCount').textContent === '4 items'", timeout=5000)
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
                              ("Audio", "sound", "#musicPanel"), ("Designed Effects", "fx", "#fx"),
                              ("Effects", "effects", "#fxbin"),
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
    # one AI action (verdict 4, punch 10): before a first pass has run, the pass's own
    pg.wait_for_selector("#tlEmpty .fp-empty-btn:visible", timeout=5000)
    assert pg.locator("#tlEmpty #firstCut").is_hidden()
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
# monitor readout, whose content is wider than its box. None may shorten at all: the
# app bar's old exemption (a job's name or Next's sentence ellipsized with a hover
# title) let the one blue button read "Next Make t…" (verdict 3 ruled it lenient, punch
# 8; REDESIGN-GOAL rubric log 2026-10-15 wave 3). The app bar collapses instead: Next
# to its icon, a job to its icon and %.
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
  check('header, header *', false);
  check('.ws-tabs, .ws-tab, .ws-tab *', false);
  check('.plist td, .plist th', false);
  check('.transport, .transport *', false);
  return out;
}"""


def _clips_with_one_unindexed(route):
    """/api/clips as the server says it, with CLIP_C not yet indexed (the longest word the
    Project panel's Status column has to fit), CLIP_A's preview building and CLIP_B's
    queued: verdict 9 (punch 3) read "preview q…" there at 1440, so every Status word is
    in this screen, and the overflow check reads each whole."""
    resp = route.fetch()
    body = resp.json()
    for c in body.get("clips", []):
        if c["clip"] == "CLIP_C.MP4":
            c["analysed"] = False
        c["media"] = {"CLIP_A.MP4": "building", "CLIP_B.MP4": "queued"}.get(c["clip"], c.get("media"))
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
    states = pg.evaluate("[...document.querySelectorAll('#projList tr.row td.st')].map(td => td.textContent)")
    assert sorted(states) == ["building", "not indexed", "queued"], states
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


# Verdict 12, punch 8: with Selects open (the tour's step 3 opens it) the Timeline is the
# narrowest the default layout makes it, and its header read "Sequ…" for the scope chip,
# "Ask the A" for the prompt, beside "A new cut is ready · Review"; its ruler labels read
# as one run. The panel's own header row, its AI prompt and the line under it, whole.
TL_HEAD_JS = """() => {
  const box = (e) => { const r = e.getBoundingClientRect();
    return {l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width}; };
  const shown = (e) => e && e.getClientRects().length && getComputedStyle(e).display !== 'none';
  const cut = [...document.querySelectorAll('#pTimeline > .ws-head, #pTimeline > .ws-head *, #aiUnder, #aiUnder *')]
    .filter(e => shown(e) && e.clientWidth > 0 && e.scrollWidth > e.clientWidth + 0.5 && !(e instanceof SVGElement))
    .map(e => `${e.id || e.className}: "${e.textContent.trim().slice(0, 40)}" ${e.scrollWidth} > ${e.clientWidth}`);
  const chip = document.querySelector('[data-testid=ai-scope]'), l = chip.querySelector('.aip-scope-l');
  const input = document.querySelector('[data-testid=ai-prompt-input]');
  const field = input.closest('.aip-field'), st = document.querySelector('[data-testid=ai-status]');
  const title = document.querySelector('#tlTitle'), lbl = title.querySelector('.lbl');
  return {cut, chip: l.textContent, chipWhole: l.scrollWidth <= l.clientWidth + 0.5, chipBox: box(chip),
          input: input.getBoundingClientRect().width, field: box(field), panel: box(document.querySelector('#pTimeline')),
          title: box(title), titleWhole: lbl.scrollWidth <= lbl.clientWidth + 0.5, placeholder: input.placeholder,
          status: shown(st) ? {...box(st), text: st.innerText, where: st.parentElement.id} : null,
          tlw: dock.layout().tlw};
}"""

RULER_JS = """() => [...document.querySelectorAll('#tl .tl-ruler .major label')]
  .filter(l => l.getClientRects().length).map(l => { const r = l.getBoundingClientRect();
    return [l.textContent, r.left, r.right]; }).sort((a, b) => a[1] - b[1])"""


def _tl_head_whole(h, where) -> None:
    assert h["cut"] == [], (where, h["cut"])
    assert h["chipWhole"] and "…" not in h["chip"], (where, h)          # the chip's words whole
    assert h["input"] >= 160, (where, h)                               # room to type
    assert h["titleWhole"], (where, h)                                 # "Timeline: <cut>" whole
    p, f = h["panel"], h["field"]
    assert f["l"] >= p["l"] - 0.5 and f["r"] <= p["r"] + 0.5, (where, h)
    assert h["title"]["r"] <= h["chipBox"]["l"], (where, h)
    s = h["status"]
    if s:
        # the line is whole and inside the panel, beside the field or under the header
        assert s["l"] >= p["l"] - 0.5 and s["r"] <= p["r"] + 0.5, (where, s)
        if s["where"] == "aiPrompt":
            assert s["r"] <= f["l"] + 0.5 and s["l"] >= h["title"]["r"] - 0.5, (where, s, h)
        else:
            assert s["where"] == "aiUnder" and s["t"] >= f["b"] - 0.5, (where, s, h)


def _ruler_apart(pg, where) -> list:
    """The ruler's labels, each with >= 20 px of clear ruler before the next (timeline.js
    LABEL_GAP: 24; the old fixed 84 px step left 17 beside a 67 px label, which read as one
    run)."""
    labels = pg.evaluate(RULER_JS)
    for (a, b) in zip(labels, labels[1:]):
        assert b[1] - a[2] >= 20, (where, "ruler labels run together", a, b, labels)
    return labels


@pytest.mark.parametrize("size", [(1440, 900), (1920, 1080)], ids=lambda s: f"{s[0]}x{s[1]}")
def test_with_selects_open_the_timeline_header_and_ruler_are_whole(live_server, project, browser, size):
    """The overflow check of `test_no_chrome_is_cut_off`, with Selects open (verdict 12, punch
    8): nothing cut, the chip whole, the prompt's input >= 160 px, the Timeline title whole;
    the Timeline is never narrower than its header row needs (dock.js `tlHeadNeed`). The AI's
    line (focused: the price before ↵; an answer: "✓ Answered · Show") is whole, beside the
    field when the header holds it, else in its row under the header. The ruler's labels
    never overlap, at the fit zoom and zoomed from 2 to 3000 px/s."""
    Path(project["edl"]).write_text(json.dumps(SEED, indent=1), encoding="utf-8")
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]})
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.route("**/api/jobs", lambda r: r.fulfill(status=200, content_type="application/json",
                                                 body=json.dumps({"jobs": FAKE_JOBS})))
    pg.route("**/api/clips", _clips_with_one_unindexed)
    # the AI's answer, scripted (no model call): an answer in words
    pg.route("**/api/assist", lambda r: r.fulfill(status=200, content_type="application/json",
                                                   body=json.dumps({"job": "ws12"}))
             if r.request.method == "POST" else r.continue_())
    pg.route("**/api/assist/ws12", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"status": "done", "kind": "answer", "text": "A ski film.", "summary": "a ski film"})))
    pg.goto(live_server)
    pg.wait_for_selector("#tl .blk", timeout=15000)
    pg.wait_for_selector("#projList tr.row", timeout=15000)
    pg.locator(".ws-tab[data-tool=bin]").click()
    pg.wait_for_selector('#pProject section[data-tool="bin"]:not([hidden])', timeout=5000)
    pg.wait_for_timeout(600)
    assert pg.evaluate(OVERFLOW_JS) == []
    h = pg.evaluate(TL_HEAD_JS)
    _tl_head_whole(h, (size, "selects"))
    assert h["chip"] == "Sequence" and h["placeholder"].startswith("Ask the AI"), h
    need = pg.evaluate("""() => { const t = document.querySelector('#tlTitle'), l = t.querySelector('.lbl');
        return t.getBoundingClientRect().width + Math.max(0, l.scrollWidth - l.clientWidth) + rcAI.least(); }""")
    assert h["panel"]["r"] - h["panel"]["l"] >= need, (h, need)
    # a selected shot's chip, the longest scope words, whole too
    pg.locator("#tl .blk").first.click()
    pg.wait_for_function("document.querySelector('[data-testid=ai-scope]').innerText.trim().startsWith('Shot 1')",
                         timeout=3000)
    _tl_head_whole(pg.evaluate(TL_HEAD_JS), (size, "selection"))
    pg.evaluate("tl.select([])")
    # focused: the price before ↵, whole
    pg.keyboard.press("Control+j")
    pg.wait_for_function("(document.querySelector('[data-testid=ai-status] .aip-cost') || {}).textContent",
                         timeout=5000)
    pg.wait_for_timeout(200)
    h = pg.evaluate(TL_HEAD_JS)
    _tl_head_whole(h, (size, "focused"))
    assert h["status"] and h["status"]["text"].strip() == "↵ ask · free on your plan", h
    # an answer: the result line, whole, with its Show
    pg.keyboard.type("what is this film about")
    pg.keyboard.press("Enter")
    pg.wait_for_selector("[data-testid=ai-result]", state="visible", timeout=5000)
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)
    h = pg.evaluate(TL_HEAD_JS)
    _tl_head_whole(h, (size, "answered"))
    assert h["status"] and "Answered" in h["status"]["text"] and "Show" in h["status"]["text"], h
    # at 1440 the header has no room for it beside the field: under the header; at 1920 beside it
    assert h["status"]["where"] == ("aiUnder" if size[0] == 1440 else "aiPrompt"), h
    assert pg.evaluate(OVERFLOW_JS) == []
    # the ruler: labels apart at the fit zoom and at every zoom from 2 to 3000 px/s
    assert len(_ruler_apart(pg, (size, "fit"))) >= 2
    for z in (2, 5, 9, 17, 33, 70, 150, 400, 1000, 3000):
        pg.evaluate(f"tl.zoomTo({z})")
        pg.wait_for_timeout(60)
        _ruler_apart(pg, (size, z))
    assert errors == []
    pg.unroute_all(behavior="ignoreErrors")
    ctx.close()


# ---------------------------------------------------------------- the app bar (verdict 3, punch 8)
#
# One first action, nothing clipped. At 1440×900, in the four states the judge saw the
# bar in: a fresh bin (never run), mid-index, mid-first-pass and exporting, each mocked
# the way the server says it (/api/flow, /api/firstpass and /api/jobs answered in place),
# no element under `header` is wider inside than its box; on the fresh bin exactly one
# button is blue, and it is "Run first pass" (Next does not offer "Index the footage"
# beside it). `ROUGHCUT_HEADER_SHOTS=<dir>` saves the bar of each state as a PNG.

INDEX_JOB = {"id": "hd-index", "kind": "index", "state": "running", "label": "Indexing the footage",
             "pct": 9.4, "eta_s": 4800, "eta_source": "measured", "milestone": "heard 3/12",
             "detail": "CLIP_04 · looking", "elapsed_s": 300}
RENDER_JOB = {"id": "hd-export", "kind": "render", "state": "running",
              "label": "Exporting — Master 4K", "pct": 41, "eta_s": 200, "eta_source": "measured",
              "milestone": "encoding", "detail": "frame 1210 of 2950", "elapsed_s": 140}
ASK_JOB = {"id": "hd-ask", "kind": "ask", "state": "running", "label": "Cutting from your note",
           "pct": 40.0, "eta_s": 30.0, "milestone": "choosing the shots", "detail": "3 shots decided",
           "elapsed_s": 20.0}

FP_COUNTS = {"index": {"clips": 12, "listened": 3, "looked": 0, "look_need": 12,
                       "words": "heard 3/12 · looked 0/12"}}

# state → (seed segments, Next's action, the first pass's state patch, running jobs)
HEADER_STATES = {
    "fresh": ([], {"stage": "index", "sentence": "Index the footage — free on your plan",
                   "verb": "Index", "screen": "/open", "tool": None, "href": "/open", "kind": "go"},
              {"state": "never-run"}, []),
    "mid-index": (SEED["segments"],
                  {"stage": "index", "sentence": "Indexing — heard 3/12 · looked 0/12 (9%); the pass "
                   "opens on the first clip released", "verb": "Watch", "screen": "/open",
                   "tool": None, "href": "/open", "kind": "wait"},
                  {"state": "never-run"}, [INDEX_JOB]),
    "mid-first-pass": (SEED["segments"],
                       {"stage": "index", "sentence": "Indexing — heard 3/12 · looked 0/12 (9%); "
                        "the pass opens on the first clip released", "verb": "Watch",
                        "screen": "/open", "tool": None, "href": "/open", "kind": "wait"},
                       {"state": "running", "stage": "index", "counts": FP_COUNTS},
                       [INDEX_JOB, ASK_JOB]),
    "exporting": (SEED["segments"],
                  {"stage": "cut", "sentence": "Refine — trim, or ask for a change", "verb": "Refine",
                   "screen": "/", "tool": "ask", "href": "/#tool=ask", "kind": "go"},
                  {"state": "done"}, [RENDER_JOB]),
}


def _patched(patch):
    def handler(route):
        resp = route.fetch()
        body = resp.json()
        body.update(patch(body))
        route.fulfill(response=resp, body=json.dumps(body), content_type="application/json")
    return handler


@pytest.mark.parametrize("state", list(HEADER_STATES))
def test_the_app_bar_clips_nothing_and_a_fresh_bin_has_one_first_action(live_server, project,
                                                                          browser, state):
    import os
    segments, nxt, fp, jobs = HEADER_STATES[state]
    Path(project["edl"]).write_text(json.dumps({**SEED, "segments": segments}, indent=1),
                                    encoding="utf-8")
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.route("**/api/flow", _patched(lambda b: {"next": nxt, "running": bool(jobs)}))
    pg.route("**/api/firstpass", _patched(lambda b: fp))
    pg.route("**/api/jobs", lambda r: r.fulfill(status=200, content_type="application/json",
                                                 body=json.dumps({"jobs": jobs})))
    # the backend probe, not this host's CLI: a banner is test_flow_ui's
    pg.route("**/api/backend", lambda r: r.fulfill(status=200, content_type="application/json",
                                                    body=json.dumps({"state": "ok", "fix": None})))
    pg.goto(live_server)
    pg.wait_for_selector("#projList tr.row", timeout=15000)
    pg.wait_for_function("window.flowBar && flowBar.state() && window.rcFirstPass"
                         f" && rcFirstPass.state() && rcFirstPass.state().state === '{fp['state']}'",
                         timeout=15000)
    # while a pass runs, the jobs it is made of (its index, the Ask writing its cut) are
    # read on its control, not a pill each (verdict 10, punch 9: app.js `passJob`)
    running = fp.get("state") == "running"
    pills = [j for j in jobs if not (running and (j["kind"] == "index" or j.get("firstpass")))]
    pg.wait_for_function(f"document.querySelectorAll('#progress .job').length === {len(pills)}",
                         timeout=10000)
    pg.wait_for_timeout(800)
    shots = os.environ.get("ROUGHCUT_HEADER_SHOTS")
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
        pg.locator("header").screenshot(path=str(Path(shots) / f"header-{state}.png"))
    assert pg.evaluate(OVERFLOW_JS) == []
    bar = pg.evaluate("""() => { const h = document.querySelector('header').getBoundingClientRect();
        return {h: h.height, w: document.documentElement.scrollWidth, kids: [...document.querySelectorAll('header > *')]
          .filter(e => e.getClientRects().length).map(e => { const r = e.getBoundingClientRect();
            return [e.id || e.className, r.left, r.right, r.bottom]; })}; }""")
    assert bar["w"] <= 1440 and bar["h"] <= 50, bar
    for (a, b) in zip(bar["kids"], bar["kids"][1:]):
        assert a[2] <= b[1] + 0.5, (a, b)                 # side by side, none over another
    blue = pg.eval_on_selector_all(".is-next", "els => els.map(e => e.id || e.dataset.testid || e.className)")
    chip = pg.locator("#flowNext")
    label = pg.locator("#firstpass .fp-label").inner_text()
    if state == "fresh":
        # one first action: Run first pass, blue; Next does not offer the index beside it
        assert label == "Run first pass", label
        assert blue == ["firstpass-button"], blue
        assert chip.count() == 0 and pg.evaluate("flowBar.yielded()")
        assert "Index the footage" not in pg.locator("header").inner_text()
    else:
        # every job is an icon and its %, the name in its hover
        for j in pills:
            row = pg.locator(f"#progress .job[data-job={j['id']}]")
            assert row.locator(".jnums").inner_text().startswith(f"{round(j['pct'])}%")
            assert not row.locator(".jname").is_visible()
            assert row.get_attribute("title").startswith(j["label"])
        assert len(blue) <= 1, blue
        if chip.count():
            # Next is whole when its words fit, else its icon with the sentence in the
            # hover — and an icon is never the blue button (verdict 4, punch 7)
            assert nxt["sentence"] in chip.get_attribute("title")
            if "compact" in chip.get_attribute("class"):
                assert chip.inner_text().strip() == ""
                assert "flowNext" not in blue, blue
            else:
                assert chip.inner_text().strip().startswith(("Next", "Now")), chip.inner_text()
    if state == "mid-index":
        # an index started elsewhere: Next is still drawn ("Now: Indexing — …")
        assert chip.count() == 1
    if state == "mid-first-pass":
        # the pass's own label is the news; Next has nothing to add beside it
        # (lane PASS: "First pass · n/12", n = clips heard AND looked at — 0 here)
        assert label == "First pass · 0/12", label
        assert chip.count() == 0 and blue == [], blue
        # one readout per job family: the pass's index is its control's time left, the
        # editor's own Ask keeps its pill
        assert [j["id"] for j in pills] == ["hd-ask"]
        assert pg.locator('[data-testid="firstpass-left"]').inner_text() == "~80 min left"
        assert pg.locator("#progress .job[data-job=hd-index]").count() == 0
    assert errors == []
    pg.unroute_all(behavior="ignoreErrors")
    ctx.close()


PREVIEW_JOB = {"id": "hd-previews", "kind": "index", "state": "running",
               "label": "Building previews", "pct": 8.0, "eta_s": 5400, "eta_source": "measured",
               "milestone": "previews 1/12", "detail": "CLIP_02", "elapsed_s": 600}


def test_a_preview_job_never_makes_the_primary_button_wordless(live_server, project, browser):
    """Verdict 4, punch 7: the preview build ran the whole Killington session and the
    header collapsed Next to a blue "→" with no words for all of it. With only a preview
    job running at 1440×900 the one blue button has at least two visible words, and
    nothing under `header` overflows. Squeezed until Next cannot fit, it drops out of
    blue rather than becoming a blue arrow."""
    Path(project["edl"]).write_text(json.dumps(SEED, indent=1), encoding="utf-8")
    nxt = {"stage": "cut", "sentence": "Refine — trim, or ask for a change", "verb": "Refine",
           "screen": "/", "tool": "ask", "href": "/#tool=ask", "kind": "go"}
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.route("**/api/flow", _patched(lambda b: {"next": nxt, "running": True}))
    pg.route("**/api/firstpass", _patched(lambda b: {"state": "done"}))
    pg.route("**/api/jobs", lambda r: r.fulfill(status=200, content_type="application/json",
                                                 body=json.dumps({"jobs": [PREVIEW_JOB]})))
    pg.route("**/api/backend", lambda r: r.fulfill(status=200, content_type="application/json",
                                                    body=json.dumps({"state": "ok", "fix": None})))
    pg.goto(live_server)
    pg.wait_for_selector("#tl .blk", timeout=15000)
    pg.wait_for_function("document.querySelectorAll('#progress .job').length === 1", timeout=10000)
    pg.wait_for_function("window.flowBar && flowBar.state() && document.getElementById('flowNext')",
                         timeout=10000)
    pg.wait_for_timeout(600)
    blue = pg.evaluate("""() => [...document.querySelectorAll('.is-next')].map(e => ({id: e.id,
        words: (e.innerText || '').trim().split(/\\s+/).filter(Boolean)}))""")
    assert len(blue) == 1, blue
    assert len(blue[0]["words"]) >= 2, blue
    assert "compact" not in pg.locator("#flowNext").get_attribute("class")
    assert pg.evaluate(OVERFLOW_JS) == []
    # no room at all: the chip is its icon, and the icon is not blue
    pg.evaluate("document.getElementById('flow').style.maxWidth = '40px'; flowBar.mark()")
    pg.wait_for_function("document.getElementById('flowNext').classList.contains('compact')",
                         timeout=3000)
    blue = pg.eval_on_selector_all(".is-next", "els => els.map(e => (e.innerText || '').trim())")
    assert all(len(b.split()) >= 2 for b in blue), blue
    assert not pg.locator("#flowNext.is-next").count()
    assert errors == []
    pg.unroute_all(behavior="ignoreErrors")
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


PICTURE_JS = """() => { const room = document.querySelector('#pProgram .screenbox').getBoundingClientRect(),
    pic = document.querySelector('#pProgram .screen').getBoundingClientRect(),
    met = document.querySelector('#meters').getBoundingClientRect();
    return {w: pic.width, h: pic.height, rw: room.width, rh: room.height,
            left: pic.left - room.left, right: room.right - pic.right,
            top: pic.top - room.top, bottom: room.bottom - pic.bottom,
            mode: rcMeters.mode(), ml: met.left, mt: met.top, mw: met.width, mh: met.height,
            pr: pic.right, pt: pic.top, pb: pic.bottom}; }"""

# the default layout's Program picture floor, per viewport (rubric log, 2026-10-15 wave 4)
PICTURE_FLOOR = {(1440, 900): 560, (1920, 1080): 800}


@pytest.mark.parametrize("size", VIEWPORTS, ids=lambda s: f"{s[0]}x{s[1]}")
def test_the_program_picture_fills_its_panel(live_server, project, browser, size):
    """Rubric log 2026-10-15: wave 2 set the floor at 380 px wide and <= 30 % letterbox at
    1440×900 (verdict 2 measured 389×219 in a 447 px tall room: 51 %). Verdict 4 punch 3:
    still a quarter of the screen each. Wave 4 gives the two monitors the whole top row,
    as Premiere's Editing workspace does: in the default layout the Program picture is
    >= 560 px wide at 1440×900 and >= 800 px at 1920×1080, its letterbox <= 30 % of its
    room, centred in that room, with the audio meters standing vertical beside it. The ▶
    is not parked on the picture: it shows only while the pointer is over a paused
    picture."""
    pg = _open(browser, live_server, project, size)
    pg.wait_for_function("window.rcMeters && rcMeters.mode() === 'v'", timeout=5000)
    m = pg.evaluate(PICTURE_JS)
    assert m["w"] >= PICTURE_FLOOR[size], m
    assert 1 - m["h"] / m["rh"] <= 0.30, m
    assert abs(m["left"] - m["right"]) <= 1 and abs(m["top"] - m["bottom"]) <= 1, m
    # the meters: vertical, beside the picture (right of it, as tall as it), never over it
    assert m["mode"] == "v" and m["mw"] >= 24 and m["ml"] >= m["pr"] - 0.5, m
    assert m["mt"] <= m["pt"] + 1 and m["mt"] + m["mh"] >= m["pb"] - 1, m
    # the Source monitor is the same size (Premiere's two monitors, side by side)
    assert box(pg, "#pSource")["w"] == pytest.approx(box(pg, "#pProgram")["w"], abs=2)
    # a selected shot does not resize it
    pg.locator("#tl .blk").first.click()
    pg.wait_for_timeout(400)
    assert pg.evaluate(PICTURE_JS)["w"] == pytest.approx(m["w"], abs=0.5)
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
    pg.evaluate("localStorage.setItem('roughcut.ws.layout', JSON.stringify({v: 2, proj: 0.2, insp: 0.245, src: null, top: 0.6}))")
    pg.reload()
    pg.wait_for_selector("#tl .blk", timeout=15000)
    assert box(pg, "#pProgram")["h"] == pytest.approx(0.6 * (size[1] - 36 - 8 - 4), abs=3)
    assert pg.errors == []
    pg.context.close()


def test_a_layout_saved_before_the_new_arrangement_keeps_its_widths_once(live_server, project, browser):
    """Wave 4 changed what the top row is (the monitors' whole row, not a quarter each).
    A layout saved before it (no version) keeps the widths it gave Project and Effect
    Controls, drops its top-row height and Source width once so the bigger monitors reach
    that browser, and is kept whole under roughcut.ws.layout.v1; the layout saved after
    it is version 2 and is honoured as it is."""
    old = {"proj": 0.3, "insp": 0.3, "src": 0.2, "top": 0.3}
    pg = _open(browser, live_server, project, (1440, 900))
    pg.evaluate(f"localStorage.setItem('roughcut.ws.layout', JSON.stringify({json.dumps(old)}))")
    pg.reload()
    pg.wait_for_selector("#tl .blk", timeout=15000)
    W = 1440 - 8
    assert box(pg, "#pProject")["w"] == pytest.approx(0.3 * W, abs=2)
    assert box(pg, "#pInspector")["w"] == pytest.approx(0.3 * W, abs=2)
    assert box(pg, "#pSource")["w"] == pytest.approx(box(pg, "#pProgram")["w"], abs=2)
    assert pg.evaluate(PICTURE_JS)["w"] >= PICTURE_FLOOR[(1440, 900)]
    assert json.loads(pg.evaluate("localStorage.getItem('roughcut.ws.layout.v1')")) == old
    saved = json.loads(pg.evaluate("localStorage.getItem('roughcut.ws.layout')"))
    assert saved == {"v": 2, "proj": 0.3, "insp": 0.3}, saved
    # once: a second load reads the version-2 layout as it is
    pg.reload()
    pg.wait_for_selector("#tl .blk", timeout=15000)
    assert json.loads(pg.evaluate("localStorage.getItem('roughcut.ws.layout')")) == saved
    assert box(pg, "#pProject")["w"] == pytest.approx(0.3 * W, abs=2)
    assert pg.errors == []
    pg.context.close()


@pytest.mark.parametrize("size", VIEWPORTS, ids=lambda s: f"{s[0]}x{s[1]}")
def test_selects_opens_wide_enough_for_its_media_in_out_and_reason(live_server, project, browser, size):
    """Verdict 4's note: at 1440 the Project group was too narrow for the Selects list's
    Media In and Reason columns (app.js shows them from a 440 / 560 px list).
    While Selects is the group's open tab the group is wide enough for them, taken from
    the Timeline (which keeps >= 460 px); the Project tab gives the width back."""
    pg = _open(browser, live_server, project, size)
    own = box(pg, "#pProject")["w"]
    tl0 = box(pg, "#pTimeline")["w"]
    pg.locator(".ws-tab[data-tool=bin]").click()
    pg.wait_for_timeout(300)
    assert pg.evaluate("document.querySelector('#library').clientWidth") >= 560
    assert box(pg, "#pTimeline")["w"] >= 460
    head = pg.locator("#library table.sel-list th").all_inner_texts()
    # Media Out is In plus Duration, and in the row's hover: from a 640 px list (verdict 5,
    # punch 7 gave its room to a one-line name and a Reason of >= 180 px)
    assert {"Media In", "Duration", "Reason"} <= {h.strip() for h in head}, head
    s = pg.evaluate("[document.documentElement.scrollWidth, innerWidth]")
    assert s[0] <= s[1], s
    pg.locator(".ws-tab[data-tool=project]").click()
    pg.wait_for_timeout(300)
    assert box(pg, "#pProject")["w"] == pytest.approx(own, abs=1)
    assert box(pg, "#pTimeline")["w"] == pytest.approx(tl0, abs=1)
    assert pg.errors == []
    pg.context.close()


def test_the_source_monitors_poster_is_the_versioned_one(live_server, project, browser):
    """Lane POLISH versioned poster URLs in the file name (`<stem>.v2.jpg`, the server's
    poster_url) so a browser drops a cached pre-ORIENT frame. The Source monitor builds its
    poster from the clip's own `poster` URL, swapping only `?t=`, never an unversioned one."""
    pg = _open(browser, live_server, project, (1440, 900))
    pg.evaluate("dock.source.marks['CLIP_B.MP4'] = [1.5, null]; dock.project.select('CLIP_B.MP4')")
    src = pg.get_attribute("#srcPoster", "src")
    want = pg.evaluate("dock.project.clips().find(c => c.clip === 'CLIP_B.MP4').poster").split("?")[0]
    assert ".v2.jpg" in want and src == f"{want}?t=1.50", (src, want)
    pg.evaluate("dock.project.view('icon')")
    icons = pg.evaluate("[...document.querySelectorAll('#projList .picon img')].map(i => i.getAttribute('src'))")
    assert icons and all(".v2.jpg?t=" in i for i in icons), icons
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
            # versioned (lead's wave-4 queue): a browser holding a pre-v2 poster drops it
            assert src == (f"/media/poster/{clip.rsplit('.', 1)[0]}.{server.PICTURE_V}.jpg"
                           "?t=1.00") and ph == 0, (clip, src, ph)
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


def test_effect_controls_opens_on_properties_with_the_assistant_folded(live_server, project, browser):
    """Verdict 4, punch 10: Effect Controls opened on AI chrome — ▶ play, Ask, a yellow
    Look deeper, Remove, a quote cut off with "…", "why? ▸". They sit in one collapsed
    "Assistant" section at its foot now; opened, nothing in it (or anywhere in the panel)
    is wider than its box at 1440, and undo reads Ctrl+Z off a Mac."""
    pg = _open(browser, live_server, project, (1440, 900))
    pg.locator("#tl .blk").nth(1).click()
    pg.wait_for_selector("#inspector .shot details.assist", timeout=5000)
    for sel in ("[data-act=play]", "[data-act=ask]", "[data-act=del]", "[data-disc=why]", ".why", ".said"):
        assert pg.locator(f"#inspector {sel}").first.is_hidden(), sel
    summary = pg.locator("#inspector details.assist > summary")
    assert summary.is_visible() and summary.inner_text().strip().lower().endswith("assistant")
    # at the foot: below every Effect Controls section
    secs = pg.evaluate("""[...document.querySelectorAll('#inspector .shot .sbody > *')]
        .filter(e => e.getClientRects().length).map(e => e.className)""")
    assert secs[-1] == "assist", secs
    summary.click()
    pg.wait_for_selector("#inspector [data-act=ask]:visible")
    said = pg.locator("#inspector .said")
    assert said.is_visible() and said.inner_text().startswith("“")
    # every leaf (the judge's measure): nothing cut off inside its own box
    over = pg.evaluate("""[...document.querySelectorAll('#inspector *')].filter(e => !e.children.length && e.getClientRects().length
        && e.clientWidth > 0 && e.scrollWidth > e.clientWidth + 0.5 && !['SELECT', 'INPUT', 'TEXTAREA'].includes(e.tagName))
        .map(e => `${e.className}: ${e.textContent.trim().slice(0, 30)}`)""")
    assert over == [], over
    # the open state carries to the next shot, as Premiere's twirl-downs do
    pg.locator("#tl .blk").nth(0).click()
    pg.wait_for_selector("#inspector [data-act=ask]:visible")
    titles = pg.evaluate("[...document.querySelectorAll('#inspector [title]')].map(e => e.title).join(' | ')")
    assert "⌘" not in titles and "Ctrl+Z" in titles, titles
    assert pg.errors == []
    pg.context.close()


def test_poster_urls_carry_the_picture_version(live_server, project):
    """Posters are served immutable for a year, so the URL itself names the picture
    cache's version: a browser holding a pre-ORIENT (upside down) poster asks again.
    /api/clips, /api/project and every timeline poster built from them carry it."""
    import urllib.request
    import server
    Path(project["edl"]).write_text(json.dumps(SEED, indent=1), encoding="utf-8")
    with urllib.request.urlopen(live_server + "/api/clips") as r:
        clips = json.loads(r.read())["clips"]
    with urllib.request.urlopen(live_server + "/api/project") as r:
        proj = json.loads(r.read())
    v = f".{server.PICTURE_V}.jpg"
    assert clips and all(v in c["poster"] for c in clips), [c["poster"] for c in clips]
    used = [c for c in proj["clips"].values() if not c["clip"].startswith("gen_")]
    assert used and all(v in c["poster"] for c in used), [c["poster"] for c in used]
    # the endpoint ignores the version and still answers with the frame
    with urllib.request.urlopen(live_server + clips[0]["poster"]) as r:
        assert r.headers["content-type"] == "image/jpeg"


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


RUN_FIRST_PASS_JS = """() => [...document.querySelectorAll('button, a[href], [role=button]')]
    .filter((b) => { const r = b.getBoundingClientRect(), s = getComputedStyle(b);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'
        && /Run first pass/.test(b.textContent); })
    .map((b) => b.closest('header') ? 'header' : b.closest('#pTimeline') ? 'timeline'
      : b.closest('#pProgram') ? 'program' : b.id || b.className)"""


def test_the_cold_screen_offers_run_first_pass_twice_and_says_what_comma_does(
        live_server, project, browser):
    """Verdict 5, punch 9: cold, "Run first pass" showed three times (the header, the
    Timeline card and the Program monitor), the Program read "… or press ," and a dark
    stripe sat under the V1 header. Now: the header's and the Timeline's, no more; both
    empty states say "press , (Insert)"; the track header column is one colour down to
    the panel's foot."""
    import os
    pg = _open(browser, live_server, project, (1440, 900), seed={**SEED, "segments": []})
    pg.wait_for_selector("#tlEmpty .fp-empty-btn:visible", timeout=15000)
    pg.wait_for_function("window.rcFirstPass && rcFirstPass.state()", timeout=15000)
    pg.wait_for_timeout(500)
    shots = os.environ.get("ROUGHCUT_COLD_SHOTS")
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(Path(shots) / "cold-1440.png"))
    where = pg.evaluate(RUN_FIRST_PASS_JS)
    assert sorted(where) == ["header", "timeline"], where
    assert "press , (Insert)" in pg.locator("#progEmpty").inner_text()
    assert "press , (Insert)" in pg.locator("#tlEmpty .empty").inner_text()
    # the track header column under V1: the seam down to A1's header is the column's own
    # surface, never the sequence area's near-black showing through
    seam = pg.evaluate(SEAM_JS)
    assert seam["gap"] >= 0, seam
    assert seam["seam"] >= seam["head"] - 6, seam          # no dark band between V1 and A1
    assert pg.errors == []
    pg.context.close()


SEAM_JS = r"""() => {
    const heads = [...document.querySelectorAll('#tl .tl-thead')];
    const named = (n) => heads.find((h) => h.querySelector('.tname')
      && h.querySelector('.tname').textContent.trim() === n);
    const rv = named('V1').getBoundingClientRect(), ra = named('A1').getBoundingClientRect();
    const lum = (c) => { const m = c.match(/[\d.]+/g).map(Number);
      return m.length > 3 && m[3] === 0 ? null : (m[0] + m[1] + m[2]) / 3; };
    // the first painted layer under the point: a background colour, or a gradient's
    // (the sequence area's near-black is #tlBody's background-image)
    const paint = (x, y) => { for (const e of document.elementsFromPoint(x, y)) {
      const s = getComputedStyle(e), g = s.backgroundImage.match(/rgba?\([^)]*\)/);
      const l = lum(g ? g[0] : s.backgroundColor); if (l != null) return l; } return null; };
    const x = rv.left + rv.width / 2;
    return {gap: ra.top - rv.bottom, seam: paint(x, (rv.bottom + ra.top) / 2),
            head: paint(x, rv.top + 3)}; }"""


NTSC = 30000 / 1001


def _source_marks(pg, clip: str, a: float, b: float) -> dict:
    """CLIP open in the Source monitor, its In at `a` and its Out at `b` — I and O pressed
    with the Source monitor focused, as an editor marks them."""
    pg.locator("#projList tr.row", has_text=clip.split(".")[0]).click()
    pg.wait_for_function("document.querySelector('#srcVideo').readyState >= 1", timeout=10000)
    pg.locator("#pSource .ws-head").click()
    for t, key in ((a, "i"), (b, "o")):
        pg.evaluate(f"document.querySelector('#srcVideo').currentTime = {t}")
        pg.wait_for_function(f"Math.abs(document.querySelector('#srcVideo').currentTime - {t}) < 0.002")
        pg.keyboard.press(key)
    return pg.evaluate("({...rcSource})")


def test_source_in_out_land_on_the_clips_own_frames(live_server, project, browser):
    """Verdict 5, punch 8: a Source In/Out on a 29.97 clip in a 59.94 sequence landed on
    the sequence's frames (CLIP_11 127.3439 s, 0.4 of its own frame off). The marks are
    on the clip's own grid — `in × 29.97` a whole number — and `,` keeps them there."""
    pg = _open(browser, live_server, project, (1440, 900))
    pg.evaluate("tl.setFps(59.94)")                         # a 59.94 sequence
    pg.locator("#projList tr.row", has_text="CLIP_C").click()
    pg.evaluate("dock.source.clip.fps = 29.97")              # a 29.97 clip (the server's 3 places)
    src = _source_marks(pg, "CLIP_C.MP4", 1.2345, 3.4567)
    for k in ("inPoint", "outPoint"):
        f = src[k] * NTSC
        assert abs(f - round(f)) < 0.01, (k, src[k], f)       # on a 29.97 frame
    assert src["inPoint"] == pytest.approx(1.2345, abs=0.5 / NTSC)
    assert src["outPoint"] == pytest.approx(3.4567, abs=0.5 / NTSC)
    # `,` at the head: the shot's in and out are the marks, still on the clip's frames
    pg.evaluate("document.activeElement && document.activeElement.blur()")
    pg.locator("#pTimeline .ws-head").click()
    pg.evaluate("tl.seek(0)")
    n = pg.evaluate("segs.length")
    pg.keyboard.press(",")
    pg.wait_for_function(f"segs.length === {n + 1}", timeout=5000)
    seg = pg.evaluate("({clip: segs[0].clip, in: segs[0].in, out: segs[0].out})")
    assert seg["clip"] == "CLIP_C.MP4", seg
    for k in ("in", "out"):
        f = seg[k] * NTSC
        assert abs(f - round(f)) < 0.01, (k, seg[k], f)
    assert seg["in"] == pytest.approx(src["inPoint"], abs=1e-4)
    assert seg["out"] == pytest.approx(src["outPoint"], abs=1e-4)
    assert pg.errors == []
    pg.context.close()


@pytest.mark.parametrize("key", [".", ","])
def test_overwrite_or_insert_past_the_end_leaves_a_gap(live_server, project, browser, key):
    """Verdict 5, punch 8: `.` with the playhead past the film's end clamped to the end.
    As in Premiere the playhead sits past the end, and `.` (or `,`) there leaves a gap of
    exactly that length on V1, the clip after it."""
    pg = _open(browser, live_server, project, (1440, 900))
    tot = pg.evaluate("tl.total()")
    src = _source_marks(pg, "CLIP_C.MP4", 1.0, 3.0)
    assert [src["inPoint"], src["outPoint"]] == [1.0, 3.0]
    pg.locator("#pTimeline .ws-head").click()
    pg.evaluate(f"tl.seek({tot} + 10)")
    assert pg.evaluate("tl.state.playhead") == pytest.approx(tot + 10, abs=1e-6), "past the end"
    pg.wait_for_timeout(200)                                  # a render keeps it there
    assert pg.evaluate("tl.state.playhead") == pytest.approx(tot + 10, abs=1e-6)
    n = pg.evaluate("segs.length")
    pg.keyboard.press(key)
    pg.wait_for_function(f"segs.length === {n + 2}", timeout=8000)
    gap, shot = pg.evaluate("[segs[segs.length - 2], segs[segs.length - 1]]"
                            ".map(s => ({clip: s.clip, in: s.in, out: s.out, gap: !!s.gap}))")
    assert gap["gap"] is True and gap["out"] - gap["in"] == pytest.approx(10, abs=0.5 / 24), gap
    assert shot == {"clip": "CLIP_C.MP4", "in": 1.0, "out": 3.0, "gap": False}, shot
    assert pg.evaluate("tl.total()") == pytest.approx(tot + 10 + 2, abs=0.05)
    # one undo takes the gap and the clip away together
    pg.keyboard.press("Control+z")
    pg.wait_for_function(f"segs.length === {n}", timeout=5000)
    assert pg.errors == []
    pg.context.close()
