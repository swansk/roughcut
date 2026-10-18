"""The Project panel as Premiere's (redesign wave 12, lane BINLOOK; REDESIGN-GOAL dimension 16's
look, Karl's K3 a: "I still don't like the way the footage and pass look"), driven in a real
browser on the suite's three-clip fixture:

  * List view: Premiere's columns, sorted by a click on any heading (the arrow on it), again
    the other way; 21 px rows, numbers right-aligned in timecode, no cell ever cut; ↑ / ↓
    walk the rows and Enter opens the clip in the Source monitor;
  * Icon view: a poster per clip, and moving across one scrubs through the clip; the bottom
    bar's slider sizes the posters;
  * the bin's cuts are sequences in it (the open one marked), and a double-click opens one;
  * a proposal waiting for review is a sequence too ("First pass", its "proposed" badge),
    and a double-click shows its ghost lane and review;
  * the pass's picks are subclips under their clip with their why; one dragged onto the
    Timeline goes in as its range;
  * `rc:clips-changed` re-reads the bin; an empty bin says "Import media to start" and its
    Import calls `window.rcImport.open()`.

The server and the browser follow test_workspace.py's pattern; this module has its own work
dir, so the cut it copies never reaches another module's listing.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

SEED = {"variant": "T", "title": "test cut", "orient": "none", "story": "",
        "target_s": [5, 20],
        "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
                     {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}]}
CLIPS = ["CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"]

# The pass's picks as GET /api/picks?order=clip answers once a first pass has picked.
PICKS = {"firstpass_picked": True, "rounds": 1, "round_size": 8, "released": [], "summary": {},
         "position": {}, "themes": [], "dictation": False, "looked": {}, "picks": [
             {"id": "pick_a1", "clip": "CLIP_A.MP4", "start": 0.5, "end": 2.0, "anchor": 1.0,
              "why": "speech: hello there — said to the camera", "kind": "speech", "verdict": None,
              "note": "", "tags": [], "witnesses": [], "score": 0.6, "rank": 2},
             {"id": "pick_c1", "clip": "CLIP_C.MP4", "start": 1.0, "end": 3.5, "anchor": 2.0,
              "why": "a jump off the lip, landed clean (frames 0:02 · 0:03)", "kind": "jump",
              "verdict": None, "note": "", "tags": [], "witnesses": [], "score": 0.9, "rank": 1},
             {"id": "pick_c2", "clip": "CLIP_C.MP4", "start": 4.0, "end": 5.0, "anchor": 4.5,
              "why": "rejected on the floor", "kind": "jump", "verdict": "reject", "note": "",
              "tags": [], "witnesses": [], "score": 0.2, "rank": 3}]}

# A first pass's proposal waiting for review, as GET /api/asks/latest answers it.
PROPOSAL = {"pending": True, "record": {"job": "fp_binlook", "plan": {
    "segments": [{"clip": "CLIP_C.MP4", "in": 1.0, "out": 3.5, "why": "the jump"},
                 {"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0, "why": "hello"}],
    "notes": "Opens on the jump, then the hello.",
    "firstpass": {"diff": {"sentence": "proposes 2 shots, 0:04"}}}}}

# /api/clips with every column telling the three clips apart (the sort test): A 30 fps
# 1920×1080 6 s indexed, from 01:00:00:00, 48 kHz stereo; B 24 fps 320×180 4 s heard, no
# timecode, 44.1 kHz mono; C 60 fps 1280×720 5 s not indexed, from 00:00:10:00, 48 kHz 5.1.
FACTS = {"CLIP_A.MP4": {"fps": 30.0, "width": 1920, "height": 1080, "duration": 6.0,
                        "analysed": True, "looked": True, "captured": 3000.0,
                        "tc_start": "01:00:00:00", "audio": {"channels": 2, "rate": 48000}},
         "CLIP_B.MP4": {"fps": 24.0, "width": 320, "height": 180, "duration": 4.0,
                        "analysed": True, "looked": False, "captured": 1000.0,
                        "tc_start": None, "audio": {"channels": 1, "rate": 44100}},
         "CLIP_C.MP4": {"fps": 60.0, "width": 1280, "height": 720, "duration": 5.0,
                        "analysed": False, "looked": False, "captured": 2000.0,
                        "tc_start": "00:00:10:00", "audio": {"channels": 6, "rate": 48000}}}
# the timecode a FACTS clip starts at, in seconds as it reads (B has none: its own 0)
TC_S = {"CLIP_A.MP4": 3600.0, "CLIP_B.MP4": 0.0, "CLIP_C.MP4": 10.0}
# what the pass saw in each (the Description column), through /api/project
SUMMARY = {"CLIP_A.MP4": "snow falling on the lift", "CLIP_B.MP4": "a chairlift ride",
           "CLIP_C.MP4": "moguls, then a jump"}

OVERFLOW_JS = """() => [...document.querySelectorAll('#projList .plist td, #projList .plist th')]
  .filter((el) => el.getClientRects().length && el.scrollWidth > el.clientWidth + 0.5)
  .map((el) => `${el.tagName} ${el.className}: "${el.textContent.trim().slice(0, 30)}" ${el.scrollWidth} > ${el.clientWidth}`)"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(project, tmp_path_factory):
    import uvicorn
    import server

    original = project["edl"].read_text(encoding="utf-8")
    work = tmp_path_factory.mktemp("binlook-work")
    server.configure(project["edl"], project["footage"], project["sidecars"],
                     work, proxies=False, assets=project["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in project["stems"]])
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
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


# lane TEACH's first-run walk-through stays shut in these browsers (its key, per the lead)
NO_TOUR = "try { localStorage.setItem('roughcut.tour', 'seen') } catch (e) {}"


def _context(browser, size):
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]})
    ctx.add_init_script(NO_TOUR)
    return ctx


def _json(route, body):
    route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


def _open(browser, base, project, size=(1440, 900), proj=None, picks=False, proposal=False,
          init=None, seed=SEED, wait=True):
    Path(project["edl"]).write_text(json.dumps(seed, indent=1), encoding="utf-8")
    ctx = _context(browser, size)
    if proj:
        ctx.add_init_script("try { localStorage.setItem('roughcut.ws.layout', "
                            f"JSON.stringify({{v: 2, proj: {proj}}})) }} catch (e) {{}}")
    if init:
        ctx.add_init_script(init)
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    if picks:
        pg.route("**/api/picks*", lambda r: _json(r, PICKS))
    if proposal:
        pg.route("**/api/asks/latest", lambda r: _json(r, PROPOSAL))
        pg.route("**/api/asks/answer", lambda r: _json(r, {"ok": True}))
    pg.goto(base)
    if wait:
        pg.wait_for_selector("#projList [data-clip]", timeout=15000)
        if seed["segments"]:
            pg.wait_for_selector("#tl .blk", timeout=15000)
    return pg


def _clip_order(pg):
    return pg.evaluate("[...document.querySelectorAll('#projList tr.row')].map(r => r.dataset.clip)")


def _faked_facts(route):
    resp = route.fetch()
    body = resp.json()
    for c in body.get("clips", []):
        c.update(FACTS.get(c["clip"], {}))
    route.fulfill(response=resp, body=json.dumps(body), content_type="application/json")


def _faked_summaries(route):
    resp = route.fetch()
    body = resp.json()
    for name, c in (body.get("clips") or {}).items():
        if name in SUMMARY:
            c["visual"] = {**(c.get("visual") or {}), "summary": SUMMARY[name]}
    route.fulfill(response=resp, body=json.dumps(body), content_type="application/json")


# ---------------------------------------------------------------- List view

def _expect(col: str, way: int) -> list[str]:
    """The clip order a sort by `col` gives: by its value, that way round; equal values by
    name, A to Z (dock.js sortItems)."""
    def val(name):
        f = FACTS[name]
        return {"name": name, "fps": f["fps"], "start": TC_S[name],
                "end": TC_S[name] + f["duration"] - 1 / f["fps"],
                "dur": f["duration"], "size": f["width"] * f["height"],
                "audio": f["audio"]["rate"] * 100 + f["audio"]["channels"],
                "state": "indexed" if f["analysed"] and f["looked"] else "heard" if f["analysed"] else "not indexed",
                "note": SUMMARY[name], "shot": f["captured"]}[col]
    by_name = sorted(CLIPS)
    return sorted(by_name, key=val, reverse=way < 0)


def test_the_list_sorts_by_every_column_both_ways(live_server, project, browser):
    pg = _open(browser, live_server, project, size=(3200, 1300), proj=0.42, wait=False)
    pg.route("**/api/clips", _faked_facts)
    pg.route("**/api/project", _faked_summaries)
    pg.reload()
    pg.wait_for_selector("#projList tr.row", timeout=15000)
    pg.wait_for_function("document.querySelector('#projList tr.row td.note') && "
                         "document.querySelector('#projList tr.row td.note').textContent.length > 0", timeout=10000)
    heads = pg.locator("#projList th[data-testid=project-sort]")
    cols = [heads.nth(i).get_attribute("data-col") for i in range(heads.count())]
    texts = heads.all_inner_texts()
    # Premiere's columns, in Premiere's order, every one of them at this width
    assert texts == ["Name", "Frame Rate", "Media Start", "Media End", "Media Duration", "Video Info",
                     "Audio Info", "Status", "Description", "Captured"], texts
    # the default: Name, A to Z, the arrow on it
    assert pg.evaluate("dock.project.sort()") == {"col": "name", "dir": 1}
    assert "asc" in pg.locator("#projList th[data-col=name]").get_attribute("class")
    for col in cols:
        th = pg.locator(f"#projList th[data-col={col}]")
        if pg.evaluate("dock.project.sort().col") != col:
            th.click()
        else:                                        # Name is sorted already: once more is Z to A
            th.click()
            th.click()
        th = pg.locator(f"#projList th[data-col={col}]")
        assert th.get_attribute("aria-sort") == "ascending", col
        assert "on" in th.get_attribute("class").split() and "asc" in th.get_attribute("class").split(), col
        assert _clip_order(pg) == _expect(col, 1), col
        # only the sorted heading wears the arrow
        assert pg.locator("#projList th.on").count() == 1
        th.click()
        th = pg.locator(f"#projList th[data-col={col}]")
        assert th.get_attribute("aria-sort") == "descending", col
        assert "desc" in th.get_attribute("class").split(), col
        assert _clip_order(pg) == _expect(col, -1), col
    # the sort is kept per browser
    pg.reload()
    pg.wait_for_selector("#projList tr.row", timeout=15000)
    pg.wait_for_function("[...document.querySelectorAll('#projList tr.row td.note')].every(td => td.textContent.length > 0)",
                         timeout=10000)
    assert pg.evaluate("dock.project.sort()") == {"col": cols[-1], "dir": -1}
    assert _clip_order(pg) == _expect(cols[-1], -1)
    # the values, as Premiere writes them
    row = pg.locator("#projList tr.row[data-clip='CLIP_A.MP4']")
    cells = dict(zip(cols, row.locator("td").all_inner_texts()))
    assert cells["fps"] == "30 fps" and cells["size"] == "1920×1080", cells
    # Media Start is the camera's timecode; Media End its last frame (start + duration − 1)
    assert cells["start"] == "01:00:00:00" and cells["dur"] == "00:00:06:00", cells
    assert cells["end"] == "01:00:05:29", cells                   # the last frame, at 30 fps
    assert cells["audio"] == "48000 Hz · Stereo", cells
    assert cells["state"] == "indexed" and cells["note"] == SUMMARY["CLIP_A.MP4"], cells
    # a clip without a timecode starts at its own 00:00:00:00
    row = pg.locator("#projList tr.row[data-clip='CLIP_B.MP4']")
    cells = dict(zip(cols, row.locator("td").all_inner_texts()))
    assert (cells["start"], cells["end"], cells["audio"]) == ("00:00:00:00", "00:00:03:23", "44100 Hz · Mono"), cells
    assert pg.evaluate(OVERFLOW_JS) == []
    assert pg.errors == []
    pg.context.close()


# a camera at 29.97 drop-frame, its clip starting two frames before minute 1 (which has
# no ;00 or ;01)
DF_FACTS = {"CLIP_C.MP4": {"fps": 29.97, "duration": 5.0, "tc_start": "00:00:59;28",
                           "audio": {"channels": 2, "rate": 48000}}}


def test_media_start_and_end_follow_the_cameras_timecode(live_server, project, browser):
    """BINLOOK's ask (wave 12 SMALL3): Media Start is the clip's source timecode, Media End
    its last frame (start + duration − 1 frame, as Premiere writes it), drop-frame counted
    as drop-frame; a subclip's are its In and Out on that timecode; Audio Info shows only
    once the list is wide enough for every other column."""
    def facts(route):
        resp = route.fetch()
        body = resp.json()
        for c in body.get("clips", []):
            c.update(DF_FACTS.get(c["clip"], {}))
        route.fulfill(response=resp, body=json.dumps(body), content_type="application/json")

    pg = _open(browser, live_server, project, size=(3200, 1300), proj=0.42, picks=True, wait=False)
    pg.route("**/api/clips", facts)
    pg.reload()
    pg.wait_for_selector("#projList tr.row[data-clip='CLIP_C.MP4'] [data-tw]", timeout=15000)
    pg.locator("#projList tr.row[data-clip='CLIP_C.MP4'] [data-tw]").click()
    pg.wait_for_selector("#projList tr.sub[data-of='CLIP_C.MP4']", timeout=5000)
    heads = pg.locator("#projList th").all_inner_texts()
    row = dict(zip(heads, pg.locator("#projList tr.row[data-clip='CLIP_C.MP4'] td").all_inner_texts()))
    # 150 frames from 00:00:59;28: ;28 ;29, then 00:01:00;02 (no ;00 ;01), … 00:01:04;29
    assert (row["Media Start"], row["Media End"]) == ("00:00:59;28", "00:01:04;29"), row
    assert row["Audio Info"] == "48000 Hz · Stereo", row
    assert "Media Start 00:00:59;28 · Media End 00:01:04;29" in \
        pg.locator("#projList tr.row[data-clip='CLIP_C.MP4']").get_attribute("title")
    # the pick at 1.0-3.5 s: frames 30 to 104 past the clip's start
    sub = dict(zip(heads, pg.locator("#projList tr.sub[data-of='CLIP_C.MP4'] td").all_inner_texts()))
    assert (sub["Media Start"], sub["Media End"]) == ("00:01:01;00", "00:01:03;14"), sub
    # a clip with no timecode keeps its own zero
    a = dict(zip(heads, pg.locator("#projList tr.row[data-clip='CLIP_A.MP4'] td").all_inner_texts()))
    assert a["Media Start"] == "00:00:00:00", a
    assert pg.evaluate(OVERFLOW_JS) == []
    assert pg.errors == []
    pg.context.close()
    # a narrower list: every other column still, and no Audio Info
    pg = _open(browser, live_server, project, size=(2560, 1300), proj=0.42)
    heads = pg.locator("#projList th").all_inner_texts()
    assert "Captured" in heads and "Audio Info" not in heads, heads
    pg.context.close()


@pytest.mark.parametrize("size", [(1440, 900), (1920, 1080)], ids=lambda s: f"{s[0]}x{s[1]}")
def test_the_list_is_dense_aligned_and_walked_with_the_arrows(live_server, project, browser, size):
    pg = _open(browser, live_server, project, size=size, picks=True, proposal=True)
    pg.wait_for_selector("#projList [data-cut=proposed]", timeout=10000)
    # the default columns: Name, the duration, Status (test_workspace's narrow rule)
    head = pg.locator("#projList th").all_inner_texts()
    assert head[0] == "Name" and head[-1] == "Status" and head[1] in ("Duration", "Media Duration"), head
    # Premiere's dense rows; numbers right-aligned
    h = pg.evaluate("[...document.querySelectorAll('#projList tr[data-key]')].map(r => r.getBoundingClientRect().height)")
    assert h and all(19 <= x <= 23 for x in h), h
    assert pg.evaluate("getComputedStyle(document.querySelector('#projList tr.row td.num')).textAlign") == "right"
    # every subclip open: still no cell is cut, at either size
    for tw in pg.locator("#projList [data-tw]").all():
        tw.click()
    pg.wait_for_selector("#projList tr.sub", timeout=5000)
    assert pg.evaluate(OVERFLOW_JS) == []
    # a click selects (and opens the clip in Source); ↓ walks to the next row; Enter opens it
    pg.locator("#projList tr.row[data-clip='CLIP_A.MP4']").click()
    assert "sel" in pg.locator("#projList tr.row[data-clip='CLIP_A.MP4']").get_attribute("class")
    assert pg.evaluate("rcSource.clip") == "CLIP_A.MP4"
    assert pg.evaluate("dock.focused()") == "project"
    pg.keyboard.press("ArrowDown")                 # CLIP_A's subclip, open above
    sel = pg.locator("#projList tr.sel")
    assert sel.count() == 1 and sel.get_attribute("data-testid") == "project-subclip"
    pg.keyboard.press("ArrowDown")                 # CLIP_B
    assert pg.locator("#projList tr.sel").get_attribute("data-clip") == "CLIP_B.MP4"
    assert pg.evaluate("rcSource.clip") == "CLIP_A.MP4"      # selecting is not opening
    pg.keyboard.press("Enter")
    pg.wait_for_function("rcSource.clip === 'CLIP_B.MP4'", timeout=3000)
    pg.keyboard.press("ArrowUp")
    pg.keyboard.press("ArrowUp")
    assert pg.locator("#projList tr.sel").get_attribute("data-clip") == "CLIP_A.MP4"
    # ← closes a clip's subclips, → opens them
    pg.keyboard.press("ArrowLeft")
    assert pg.locator("#projList tr.sub[data-of='CLIP_A.MP4']").count() == 0
    pg.keyboard.press("ArrowRight")
    assert pg.locator("#projList tr.sub[data-of='CLIP_A.MP4']").count() == 1
    # the Timeline's own keys are untouched while it has the focus: ↓ is its next edit
    pg.locator("#tl .blk").first.click()
    pg.evaluate("tl.seek(0)")
    pg.keyboard.press("ArrowDown")
    assert pg.evaluate("tl.state.playhead") == pytest.approx(2.0, abs=0.05)
    assert pg.locator("#projList tr.sel").get_attribute("data-clip") == "CLIP_A.MP4"
    assert pg.errors == []
    pg.context.close()


# ---------------------------------------------------------------- Icon view

FRAME_JS = """(sel) => { const img = document.querySelector(sel);
  if (!img || !img.complete || !img.naturalWidth) return null;
  const c = document.createElement('canvas'); c.width = 32; c.height = 18;
  const g = c.getContext('2d'); g.drawImage(img, 0, 0, 32, 18);
  return Array.from(g.getImageData(0, 0, 32, 18).data).join(','); }"""


def test_icon_view_scrubs_a_clip_under_the_pointer_and_sizes_with_the_slider(live_server, project, browser):
    pg = _open(browser, live_server, project, size=(1920, 1080))
    assert not pg.locator("#projZoom").is_visible()            # the slider is the posters'
    pg.locator("[data-testid=project-view-icon]").click()
    assert pg.evaluate("dock.project.view()") == "icon"
    assert pg.locator("#projList .picon").count() == 3
    assert pg.locator("[data-testid=project-view-icon]").get_attribute("class") == "on"
    # each poster says its name and its duration under it
    cap = pg.locator("#projList .picon[data-clip='CLIP_A.MP4'] .cap").inner_text()
    assert "CLIP_A.MP4" in cap and "6:00" in cap, cap
    img = "#projList .picon[data-clip='CLIP_A.MP4'] .thumb img"
    pg.wait_for_function(f"document.querySelector(\"{img}\").complete && document.querySelector(\"{img}\").naturalWidth > 0",
                         timeout=10000)
    base = pg.get_attribute(img, "src")
    base_px = pg.evaluate(FRAME_JS, img)
    box = pg.locator("#projList .picon[data-clip='CLIP_A.MP4'] .thumb").bounding_box()
    y = box["y"] + box["height"] / 2
    srcs, pix = [], []
    for f in (0.12, 0.88):
        pg.mouse.move(box["x"] + box["width"] * f, y, steps=3)
        pg.wait_for_function(f"""(() => {{ const i = document.querySelector("{img}");
            return i.getAttribute('src') !== {json.dumps(base)} && !{json.dumps(srcs)}.includes(i.getAttribute('src'))
              && i.complete && i.naturalWidth > 0; }})()""", timeout=15000)
        srcs.append(pg.get_attribute(img, "src"))
        pix.append(pg.evaluate(FRAME_JS, img))
        # the scrub's place in the clip, on the poster's foot, and its time under it
        assert pg.locator("#projList .picon[data-clip='CLIP_A.MP4'] .bar").is_visible()
    t = [float(s.split("t=")[1]) for s in srcs]
    assert t[0] < 1.5 and t[1] > 4.5, srcs                         # the pointer's place in the clip
    assert len({base_px, *pix}) == 3, "the poster did not change frame as the pointer moved"
    assert ".v2.jpg?t=" in srcs[0]                                # the versioned poster route
    # leaving puts the clip's own poster back
    pg.mouse.move(5, 5)
    pg.wait_for_function(f"document.querySelector(\"{img}\").getAttribute('src') === {json.dumps(base)}", timeout=3000)
    assert not pg.locator("#projList .picon[data-clip='CLIP_A.MP4'] .bar").is_visible()
    assert "6:00" in pg.locator("#projList .picon[data-clip='CLIP_A.MP4'] .cap").inner_text()
    # the bottom bar's slider sizes the posters, and is kept
    zoom = pg.locator("[data-testid=project-zoom]")
    assert zoom.is_visible()
    w0 = pg.locator("#projList .picon").first.bounding_box()["width"]
    zoom.evaluate("(z) => { z.value = '220'; z.dispatchEvent(new Event('input', {bubbles: true}));"
                  " z.dispatchEvent(new Event('change', {bubbles: true})); }")
    w1 = pg.locator("#projList .picon").first.bounding_box()["width"]
    assert w1 > w0 * 1.4, (w0, w1)
    pg.reload()
    pg.wait_for_selector("#projList .picon", timeout=15000)
    assert pg.locator("#projList .picon").first.bounding_box()["width"] == pytest.approx(w1, abs=2)
    # a click selects a poster and opens it in Source; List view comes back from the bar
    pg.locator("#projList .picon[data-clip='CLIP_C.MP4']").click()
    assert "sel" in pg.locator("#projList .picon[data-clip='CLIP_C.MP4']").get_attribute("class")
    assert pg.evaluate("rcSource.clip") == "CLIP_C.MP4"
    pg.locator("[data-testid=project-view-list]").click()
    assert pg.evaluate("dock.project.view()") == "list"
    assert pg.locator("#projList tr.row").count() == 3 and not pg.locator("#projZoom").is_visible()
    assert pg.errors == []
    pg.context.close()


# ---------------------------------------------------------------- the Status column (verdict 9, punch 3)

def _every_status(route):
    resp = route.fetch()
    body = resp.json()
    for c in body.get("clips", []):
        c["media"] = {"CLIP_A.MP4": "building", "CLIP_B.MP4": "queued"}.get(c["clip"], c.get("media"))
        if c["clip"] == "CLIP_C.MP4":
            c["analysed"] = c["looked"] = False
    route.fulfill(response=resp, body=json.dumps(body), content_type="application/json")


@pytest.mark.parametrize("size", [(1440, 900), (1920, 1080)], ids=lambda s: f"{s[0]}x{s[1]}")
def test_every_status_reads_whole_and_its_hover_says_the_rest(live_server, project, browser, size):
    """"preview q…" at 1440 (verdict 9): every Status is one short word, Premiere-style, and
    the cell's hover says the whole of it."""
    pg = _open(browser, live_server, project, size=size, picks=True, proposal=True, wait=False)
    pg.route("**/api/clips", _every_status)
    pg.reload()
    pg.wait_for_selector("#projList tr.row td.st[data-state=building]", timeout=15000)
    pg.wait_for_selector("#projList [data-cut=proposed]", timeout=10000)
    for tw in pg.locator("#projList [data-tw]").all():
        tw.click()
    pg.wait_for_selector("#projList tr.sub", timeout=5000)
    cells = pg.evaluate("""() => [...document.querySelectorAll('#projList tr[data-key] td.st')].map((td) => ({
        key: td.parentElement.dataset.key, text: td.textContent.trim(), title: td.title,
        sw: td.scrollWidth, cw: td.clientWidth }))""")
    by = {c["key"]: c for c in cells}
    assert by["c|CLIP_A.MP4"]["text"] == "building" and by["c|CLIP_A.MP4"]["title"].startswith("preview building"), by
    assert by["c|CLIP_B.MP4"]["text"] == "queued" and by["c|CLIP_B.MP4"]["title"].startswith("preview queued"), by
    assert by["c|CLIP_C.MP4"]["text"] == "not indexed" and by["c|CLIP_C.MP4"]["title"].startswith("not indexed"), by
    assert by["p|"]["text"] == "proposed" and by["p|"]["title"].startswith("proposed"), by
    assert [c["text"] for c in cells if c["key"].startswith("q|")] == ["open"], cells
    subs = {c["text"] for c in cells if c["key"].startswith("s|")}
    assert subs and subs <= {"pick", "kept", "hero", "in cut"}, subs
    # every one whole, in its column
    assert all(c["sw"] <= c["cw"] + 0.5 for c in cells), [c for c in cells if c["sw"] > c["cw"] + 0.5]
    assert all(c["title"] for c in cells if c["text"]), cells
    # the row's hover says it whole too
    assert "preview building" in pg.locator("#projList tr.row[data-clip='CLIP_A.MP4']").get_attribute("title")
    assert pg.evaluate(OVERFLOW_JS) == []
    assert pg.errors == []
    pg.context.close()


# ---------------------------------------------------------------- sequences

def test_the_bins_cuts_are_sequences_and_a_double_click_opens_one(live_server, project, browser):
    import httpx

    Path(project["edl"]).write_text(json.dumps(SEED, indent=1), encoding="utf-8")
    original = str(project["edl"])
    r = httpx.post(f"{live_server}/api/cuts/copy", json={"name": "Alt cut", "open": False}, timeout=30)
    assert r.status_code == 200, r.text
    alt = r.json()["copy"]["path"]
    try:
        pg = _open(browser, live_server, project)
        seqs = pg.locator("#projList tr.seq[data-testid=project-sequence]")
        pg.wait_for_function("document.querySelectorAll('#projList tr.seq').length === 2", timeout=10000)
        # the open one is marked; both read as sequences, before the clips
        cur = pg.locator("#projList tr.seq.cur")
        assert cur.get_attribute("data-cut") == original
        assert "open" in cur.inner_text()
        keys = pg.evaluate("[...document.querySelectorAll('#projList tr[data-key]')].map(r => r.dataset.key[0])")
        assert keys[:2] == ["q", "q"] and set(keys[2:]) == {"c"}, keys
        # the item count is Premiere's: every clip and sequence in the bin
        assert pg.locator("#projCount").inner_text() == "5 items"
        assert seqs.count() == 2
        # a double-click opens the other one, through the switcher (the board reloads on
        # it) — on a page whose switcher panel never opened (it used to throw on #picker)
        assert pg.locator("#picker").count() == 0
        with pg.expect_navigation(timeout=15000):
            pg.locator(f"#projList tr.seq[data-cut='{alt}']").dblclick()
        pg.wait_for_selector("#projList tr.seq.cur", timeout=15000)
        assert pg.locator("#projList tr.seq.cur").get_attribute("data-cut") == alt
        assert pg.evaluate("switcher.state.cuts && switcher.state.cuts.current.name") == "Alt cut"
        # a double-click on the open one is the Timeline's focus, not a reload
        pg.locator("#projList tr.seq.cur").dblclick()
        assert pg.evaluate("dock.focused()") == "timeline"
        assert pg.errors == []
        pg.context.close()
    finally:
        httpx.post(f"{live_server}/api/cuts/open", json={"path": original}, timeout=30)
        httpx.post(f"{live_server}/api/cuts/delete", json={"path": alt}, timeout=30)


def test_a_waiting_proposal_is_a_sequence_in_the_bin(live_server, project, browser):
    pg = _open(browser, live_server, project, proposal=True)
    row = pg.locator("#projList tr.seq[data-cut=proposed]")
    row.wait_for(timeout=10000)
    # first in the bin, named, with its badge and its length
    assert pg.evaluate("document.querySelector('#projList tr[data-key]').dataset.cut") == "proposed"
    assert row.locator(".nm").inner_text() == "First pass"
    assert row.locator(".pbadge").inner_text() == "proposed"
    assert row.get_attribute("data-testid") == "project-sequence"
    assert pg.evaluate("tl.tc(4)") in row.inner_text()             # 2.5 s + 1.5 s
    assert "First pass (proposed)" in row.get_attribute("title")
    # the review: its ghost lane on the Timeline and its panel in the Assistant
    pg.evaluate("dock.open('inspect')")
    row.dblclick()
    assert pg.evaluate("dock.current()") == "ask"
    assert pg.locator("#proposal").is_visible()
    assert pg.locator("#tl .tl-xlane[data-lane=ghost]").is_visible()
    assert pg.evaluate("dock.focused()") == "timeline"
    # in Icon view it is a poster with the badge on it
    pg.evaluate("dock.project.view('icon')")
    assert pg.locator("#projList .pseq[data-cut=proposed] .pbadge").inner_text() == "proposed"
    pg.evaluate("dock.project.view('list')")
    # answered, it leaves the bin
    pg.locator("#rejectProposal").click()
    pg.wait_for_function("!document.querySelector('#projList [data-cut=proposed]')", timeout=5000)
    assert pg.errors == []
    pg.context.close()


# ---------------------------------------------------------------- subclips

def test_the_passs_picks_are_subclips_with_their_why(live_server, project, browser):
    pg = _open(browser, live_server, project, size=(1920, 1080), proj=0.42, picks=True)
    tw = pg.locator("#projList tr.row[data-clip='CLIP_C.MP4'] [data-tw]")
    tw.wait_for(timeout=10000)
    assert tw.get_attribute("aria-expanded") == "false"
    assert pg.locator("#projList tr.sub").count() == 0            # closed until asked
    # the row's text still starts with its name (the triangle is drawn, not written)
    assert pg.locator("#projList tr.row[data-clip='CLIP_C.MP4']").inner_text().split()[0] == "CLIP_C.MP4"
    tw.click()
    subs = pg.locator("#projList tr.sub[data-of='CLIP_C.MP4']")
    assert subs.count() == 1                                      # the rejected pick is not one
    sub = subs.first
    assert sub.get_attribute("data-testid") == "project-subclip"
    cells = sub.locator("td").all_inner_texts()
    heads = pg.locator("#projList th").all_inner_texts()
    got = dict(zip(heads, cells))
    # its name is what the pass saw; its why is the Description, whole in the hover
    assert got["Name"] == "a jump off the lip, landed clean", got
    assert got["Media Start"] == pg.evaluate("tl.tc(1.0, 24)"), got
    assert got["Media Duration"] == pg.evaluate("tl.tc(2.5, 24)"), got
    assert got["Status"] == "pick" and got["Description"].startswith("a jump off the lip"), got
    title = sub.get_attribute("title")
    assert "In 00:00:01:00" in title and "Out 00:00:03:11" in title and "a jump off the lip" in title, title
    # it stays open across a reload
    pg.reload()
    pg.wait_for_selector("#projList tr.sub[data-of='CLIP_C.MP4']", timeout=15000)
    # a click opens it in the Source monitor with its In and Out
    pg.locator("#projList tr.sub[data-of='CLIP_C.MP4']").click()
    pg.wait_for_function("rcSource.clip === 'CLIP_C.MP4'", timeout=5000)
    assert pg.evaluate("[rcSource.inPoint, rcSource.outPoint]") == [1.0, 3.5]
    # a search finds a subclip by its why, its clip shown open on it
    pg.locator("#projQ").fill("hello")
    pg.wait_for_function("document.querySelectorAll('#projList tr.row').length === 1", timeout=3000)
    assert pg.locator("#projList tr.row").get_attribute("data-clip") == "CLIP_A.MP4"
    assert pg.locator("#projList tr.sub[data-of='CLIP_A.MP4'] .nm").inner_text() == "“hello there”"
    pg.locator("#projQ").fill("")
    assert pg.errors == []
    pg.context.close()


def test_the_panel_reads_the_board_through_a_read_only_accessor(live_server, project, browser):
    """BINLOOK's ask (wave 12 SMALL3): app.js answers `window.rcBoard = {picks, keeps, segs,
    plan}` with frozen copies, and dock.js reads the board through it, not through app.js's
    globals by name. The subclips and the waiting proposal still show."""
    pg = _open(browser, live_server, project, picks=True, proposal=True)
    pg.wait_for_selector("#projList [data-cut=proposed]", timeout=10000)
    pg.wait_for_selector("#projList tr.row[data-clip='CLIP_C.MP4'] [data-tw]", timeout=10000)
    got = pg.evaluate("""() => {
      const b = window.rcBoard, p = b.picks(), s = b.segs(), plan = b.plan(), k = b.keeps();
      const was = segs[0].in;
      try { s[0].in = 99; } catch (e) { /* frozen: a strict caller is told */ }
      try { p.push({}); } catch (e) { /* frozen */ }
      return {
        keys: Object.keys(b).sort(), frozen: Object.isFrozen(b),
        same: p === passPicks || s === segs || plan === pendingPlan || (!!bin && k === bin.selects),
        equal: JSON.stringify(p) === JSON.stringify(passPicks) && JSON.stringify(s) === JSON.stringify(segs)
          && JSON.stringify(plan) === JSON.stringify(pendingPlan),
        deep: Object.isFrozen(p) && Object.isFrozen(p[0]) && Object.isFrozen(s[0]) && Object.isFrozen(plan.segments),
        untouched: segs[0].in === was && passPicks.length === p.length && Array.isArray(k),
      };
    }""")
    assert got == {"keys": ["keeps", "picks", "plan", "segs"], "frozen": True, "same": False,
                   "equal": True, "deep": True, "untouched": True}, got
    # dock.js names none of app.js's globals any more
    src = pg.evaluate("fetch('/dock.js').then(r => r.text())")
    for name in ("passPicks", "pendingPlan", "typeof segs", "typeof bin"):
        assert name not in src, name
    assert pg.errors == []
    pg.context.close()


def test_a_subclip_dragged_onto_the_timeline_goes_in_as_its_range(live_server, project, browser):
    pg = _open(browser, live_server, project, picks=True)
    pg.locator("#projList tr.row[data-clip='CLIP_A.MP4'] [data-tw]").click()
    sub = pg.locator("#projList tr.sub[data-of='CLIP_A.MP4']")
    sub.wait_for(timeout=5000)
    n = pg.evaluate("segs.length")
    pg.evaluate("tl.seek(tl.total())")
    dt = pg.evaluate_handle("() => new DataTransfer()")
    sub.dispatch_event("dragstart", {"dataTransfer": dt})
    assert json.loads(pg.evaluate("(dt) => dt.getData('application/x-roughcut-shot')", dt)) == {
        "clip": "CLIP_A.MP4", "start": 0.5, "end": 2.0, "why": PICKS["picks"][0]["why"]}
    box = pg.locator("#tl .tl-view").bounding_box()
    at = {"dataTransfer": dt, "clientX": box["x"] + box["width"] - 20, "clientY": box["y"] + box["height"] / 2}
    pg.locator("#tl .tl-view").dispatch_event("dragover", at)
    pg.locator("#tl .tl-view").dispatch_event("drop", at)
    pg.wait_for_function(f"segs.length === {n + 1}", timeout=3000)
    added = pg.evaluate("segs.map(s => [s.clip, s.in, s.out])")
    assert ["CLIP_A.MP4", 0.5, 2.0] in added, added
    # and it is one undo
    pg.keyboard.press("Control+z")
    pg.wait_for_function(f"segs.length === {n}", timeout=3000)
    assert pg.errors == []
    pg.context.close()


# ---------------------------------------------------------------- fresh, and empty

def test_clips_changed_rereads_the_bin_without_a_reload(live_server, project, browser):
    hide = {"on": True}

    def clips(route):
        resp = route.fetch()
        body = resp.json()
        if hide["on"]:
            body["clips"] = [c for c in body["clips"] if c["clip"] != "CLIP_C.MP4"]
        route.fulfill(response=resp, body=json.dumps(body), content_type="application/json")

    Path(project["edl"]).write_text(json.dumps(SEED, indent=1), encoding="utf-8")
    ctx = _context(browser, (1440, 900))
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.route("**/api/clips", clips)
    pg.goto(live_server)
    pg.wait_for_function("document.querySelectorAll('#projList tr.row').length === 2", timeout=15000)
    hide["on"] = False
    pg.evaluate("window.dispatchEvent(new CustomEvent('rc:clips-changed', {detail: {added: ['CLIP_C.MP4']}}))")
    pg.wait_for_function("document.querySelectorAll('#projList tr.row').length === 3", timeout=5000)
    # what came in is selected, as Premiere's Import leaves it
    pg.wait_for_function("document.querySelector('#projList tr.row.sel') && "
                         "document.querySelector('#projList tr.row.sel').dataset.clip === 'CLIP_C.MP4'", timeout=3000)
    # dock.project.reload() still re-reads
    hide["on"] = True
    pg.evaluate("dock.project.reload()")
    pg.wait_for_function("document.querySelectorAll('#projList tr.row').length === 2", timeout=5000)
    assert errors == []
    ctx.close()


def test_an_empty_bin_says_import_media_to_start(live_server, project, browser):
    Path(project["edl"]).write_text(json.dumps({**SEED, "segments": []}, indent=1), encoding="utf-8")
    ctx = _context(browser, (1440, 900))
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.route("**/api/clips", lambda r: _json(r, {"clips": [], "probing": 0, "sessions": 0, "total_s": 0}))
    pg.goto(live_server)
    empty = pg.locator("#projList [data-testid=project-empty]")
    empty.wait_for(timeout=15000)
    text = empty.inner_text()
    assert "Import media to start" in text and "or drop files here" in text, text
    assert "Open a footage folder" not in pg.locator("#projList").inner_text()
    pg.evaluate("() => { window.__imports = []; rcImport.open = () => window.__imports.push('open'); }")
    pg.locator("[data-testid=project-empty-import]").click()
    assert pg.evaluate("window.__imports") == ["open"]
    # the bottom bar's Import is the same
    pg.locator("[data-testid=project-import]").click()
    assert pg.evaluate("window.__imports") == ["open", "open"]
    assert pg.locator("#projCount").inner_text() == ""
    assert errors == []
    ctx.close()
