"""Footage in, no ceremony (REDESIGN-GOAL dimensions 1 and 3; verdict 2 punch items 7, 8).

Verdict 2: "before a clip's speech is heard, Insert, `,`, `.` and drag all refuse"; "for
5.7 s the Project panel says 'No footage in this bin yet' while 12 clips sit in the
folder"; "/api/clips sends no fps/width/height"; the empty Timeline offers "Nothing
indexed yet · The footage →". Here, on the suite's synthetic clips with the index
running and its speech-to-text **never finishing** (every tool replaced by a script that
waits for the test to let it go):

  * `,`, `.`, the Source monitor's Insert and a drag from the Project panel each put a
    clip the AI has not heard on V1 within 1 s, with an empty transcript; the shot
    survives a save and a reload;
  * the Status column says heard / not indexed truthfully, and at >= 470 px the
    Project list has Frame Rate and Resolution columns filled from /api/clips;
  * with a fresh --work: no "No footage" text while /api/clips is pending (held with
    page.route), rows within 2 s, a picture in the Source monitor within 5 s, and the
    empty Timeline offers "Drag a clip here or press , · Run first pass".

    uv run --with pytest --with fastapi --with uvicorn --with httpx --with numpy \
        --with playwright pytest app/tests/test_ingest.py -q
"""

from __future__ import annotations

import json
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from conftest import _sidecar

playwright_api = pytest.importorskip("playwright.sync_api",
                                     reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

SEED = {"variant": "T", "title": "ingest", "orient": "none", "story": "",
        "target_s": [5, 20],
        "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"}]}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def ingest_bin(project, tmp_path_factory):
    """The suite's three clips in a folder of their own; only CLIP_A has been heard."""
    root = tmp_path_factory.mktemp("ingest")
    footage, heard, none = root / "footage", root / "heard", root / "none"
    for d in (footage, heard, none):
        d.mkdir()
    for stem in project["stems"]:
        shutil.copy(project["footage"] / f"{stem}.MP4", footage / f"{stem}.MP4")
    (heard / "CLIP_A.audio.json").write_text(json.dumps(_sidecar("CLIP_A")), encoding="utf-8")
    return {"root": root, "footage": footage, "heard": heard, "none": none,
            "assets": project["assets"]}


@pytest.fixture(scope="module")
def live_server(ingest_bin):
    import uvicorn
    import server

    server.configure(None, ingest_bin["footage"], ingest_bin["heard"],
                     ingest_bin["root"] / "work0", proxies=False, assets=ingest_bin["assets"])
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


@pytest.fixture
def deaf_index(monkeypatch, tmp_path):
    """Every tool the index runs waits until the test ends: the index is running and the
    speech-to-text never finishes (verdict 2: "heard 0/12" for minutes)."""
    import server

    flag = tmp_path / "let-go"
    script = tmp_path / "hang.py"
    script.write_text("import os, sys, time\n"
                      f"while not os.path.exists({str(flag)!r}):\n"
                      "    time.sleep(0.1)\n"
                      "sys.exit(1)\n", encoding="utf-8")
    hang = [sys.executable, str(script)]
    for name in ("analyze_cmd", "visual_cmd", "scan_cmd", "fine_cmd", "telemetry_cmd"):
        monkeypatch.setattr(server, name, lambda *a, **k: list(hang))
    yield flag
    flag.write_text("done", encoding="utf-8")
    deadline = time.time() + 20
    while time.time() < deadline and any(
            x["state"] not in server.progress.TERMINAL for x in server.INDEXES.values()):
        time.sleep(0.1)


def _configure(ingest_bin, work: Path, sidecars: Path, seed: dict | None):
    import server

    edl = None
    if seed is not None:
        edl = work / "edl.json"
        work.mkdir(parents=True, exist_ok=True)
        edl.write_text(json.dumps(seed, indent=1), encoding="utf-8")
    server.configure(edl, ingest_bin["footage"], sidecars, work, proxies=False,
                     assets=ingest_bin["assets"])


@pytest.fixture
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        yield b
        b.close()


def _page(browser, url, size=(1440, 900), layout=None, init=None):
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]})
    if layout:
        ctx.add_init_script(
            f"try {{ localStorage.setItem('roughcut.ws.layout', {json.dumps(json.dumps(layout))}) }} catch (e) {{}}")
    if init:
        ctx.add_init_script(init)
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    if url:
        pg.goto(url)
    return pg


def _rows(pg):
    return pg.locator("#projList tr.row")


def _clips(pg):
    return pg.evaluate("segs.map(s => s.clip)")


# ---------------------------------------------------------------- punch 7: edit at once

@pytest.mark.parametrize("how", ["comma", "period", "insert", "drag"])
def test_a_clip_the_ai_has_not_heard_goes_on_v1_at_once(how, live_server, ingest_bin,
                                                         deaf_index, browser, tmp_path):
    import server

    side = tmp_path / "side"                       # this test's own: it may hear CLIP_B
    shutil.copytree(ingest_bin["heard"], side)
    _configure(ingest_bin, tmp_path / "work", side, SEED)
    job = server.index_entry()
    threading.Thread(target=server._index_job, args=(job, ""), daemon=True).start()
    pg = _page(browser, live_server)
    pg.wait_for_selector("#tl .blk")
    pg.wait_for_function("document.querySelectorAll('#projList tr.row').length === 3")
    assert server.INDEXES[job]["state"] == "running"
    assert not (side / "CLIP_B.audio.json").exists()
    # truthful status: A is heard (not looked at), B and C are not indexed
    status = {r.split()[0]: r for r in _rows(pg).all_inner_texts()}
    assert "heard" in status["CLIP_A.MP4"] and "not indexed" in status["CLIP_B.MP4"], status
    assert pg.evaluate("!('CLIP_B.MP4' in P.clips)")
    pg.evaluate("tl.seek(tl.total())")             # the playhead at the end of the cut

    if how == "drag":
        dt = pg.evaluate_handle("() => new DataTransfer()")
        row = _rows(pg).filter(has_text="CLIP_B")
        t0 = time.monotonic()
        row.dispatch_event("dragstart", {"dataTransfer": dt})
        box = pg.locator("#tl .tl-view").bounding_box()
        at = {"dataTransfer": dt, "clientX": box["x"] + box["width"] - 20,
              "clientY": box["y"] + box["height"] / 2}
        pg.locator("#tl .tl-view").dispatch_event("dragover", at)
        pg.locator("#tl .tl-view").dispatch_event("drop", at)
    else:
        _rows(pg).filter(has_text="CLIP_B").click()
        assert pg.evaluate("rcSource.clip") == "CLIP_B.MP4"
        t0 = time.monotonic()
        if how == "insert":
            pg.locator("#source button[data-src=insert]").click()
        else:
            pg.evaluate("document.activeElement && document.activeElement.blur()")
            pg.keyboard.press("," if how == "comma" else ".")
    pg.wait_for_function("segs.some(s => s.clip === 'CLIP_B.MP4')", timeout=1000)
    took = time.monotonic() - t0
    assert took <= 1.0, f"{how}: {took:.2f} s"
    # a V1 block, and its linked A1 partner (lane TRACKS)
    pg.wait_for_function("document.querySelectorAll('#tl .tl-lane[data-lane=V1] .blk').length === 2"
                         " && document.querySelectorAll('#tl .tl-track[data-track=A1] .ablk').length === 2",
                         timeout=2000)
    b = pg.evaluate("segs.find(s => s.clip === 'CLIP_B.MP4')")
    assert b["out"] - b["in"] == pytest.approx(6.0, abs=0.1), b      # the whole clip
    if how in ("insert", "drag"):
        assert b.get("why", "") == "", b
    # listed as footage the AI has not heard: the monitor can play it, no transcript yet
    clip = pg.evaluate("P.clips['CLIP_B.MP4']")
    assert clip["heard"] is False and clip["transcript"] == [] and clip["proxy"], clip
    # it is saved, and a reload keeps it — the server lists it the same way
    pg.evaluate("save()")
    pg.wait_for_function("!saveTimer", timeout=5000)
    saved = json.loads((tmp_path / "work" / "edl.json").read_text(encoding="utf-8"))
    assert "CLIP_B.MP4" in [s["clip"] for s in saved["segments"]]
    pg.reload()
    pg.wait_for_function("segs.some(s => s.clip === 'CLIP_B.MP4')", timeout=10000)
    clip = pg.evaluate("P.clips['CLIP_B.MP4']")
    assert clip["heard"] is False and clip["duration"] == pytest.approx(6.0, abs=0.1)
    assert server.INDEXES[job]["state"] == "running", "the index never had to finish"
    if how == "insert":
        # the clip is heard while the index still runs: its transcript (what the trims
        # snap to) reaches the board on the Project panel's next look, no reload
        (side / "CLIP_B.audio.json").write_text(json.dumps(_sidecar("CLIP_B")), encoding="utf-8")
        pg.evaluate("dock.project.reload()")
        pg.wait_for_function("(P.clips['CLIP_B.MP4'].transcript || []).length === 3", timeout=5000)
        assert pg.evaluate("P.clips['CLIP_B.MP4'].heard") is None
        assert "heard" in _rows(pg).filter(has_text="CLIP_B").inner_text()
    assert pg.errors == []
    pg.context.close()


def test_the_project_list_shows_frame_rate_and_resolution_when_wide(live_server, ingest_bin,
                                                                   browser, tmp_path):
    _configure(ingest_bin, tmp_path / "work", ingest_bin["heard"], SEED)
    d = json.loads(_page(browser, None).context.request.get(f"{live_server}/api/clips").text())
    a = d["clips"][0]
    assert d["probing"] == 0
    assert a["fps"] == pytest.approx(24.0) and (a["width"], a["height"]) == (320, 180)
    assert a["codec"] == "h264"
    # 470 px and up: Frame Rate and Video Info (the size), filled in
    pg = _page(browser, live_server, size=(1440, 900), layout={"proj": 0.36})
    pg.wait_for_function("document.querySelectorAll('#projList tr.row').length === 3")
    assert pg.evaluate("document.querySelector('#projList').clientWidth") >= 470
    head = pg.locator("#projList th").all_inner_texts()
    assert "Frame Rate" in head and "Video Info" in head, head          # Premiere's names (BINLOOK)
    first = _rows(pg).first.inner_text()
    assert "24 fps" in first and "320×180" in first, first
    # the default (narrower) panel keeps to Name / Duration / Status
    pg2 = _page(browser, live_server, size=(1440, 900))
    pg2.wait_for_function("document.querySelectorAll('#projList tr.row').length === 3")
    if pg2.evaluate("document.querySelector('#projList').clientWidth") < 400:
        assert "Frame Rate" not in pg2.locator("#projList th").all_inner_texts()
    assert pg.errors == [] and pg2.errors == []
    pg.context.close()
    pg2.context.close()


# ---------------------------------------------------------------- punch 8: cold land

SAW_EMPTY = """
window.__sawNoFootage = false;
new MutationObserver(() => {
  const el = document.querySelector('#projList');
  if (el && /No footage/.test(el.textContent)) window.__sawNoFootage = true;
}).observe(document, { subtree: true, childList: true, characterData: true });
"""


def test_a_cold_land_reads_the_folder_and_shows_a_picture(live_server, ingest_bin, browser,
                                                          tmp_path):
    # a fresh --work and nothing heard: the first open of a new bin
    _configure(ingest_bin, tmp_path / "fresh", ingest_bin["none"], None)
    held, release = [], []

    def hold(route):
        if release:
            route.continue_()
        else:
            held.append(route)

    pg = _page(browser, None, init=SAW_EMPTY)
    pg.route("**/api/clips", hold)
    pg.goto(live_server)
    pg.wait_for_function("document.querySelector('#projList') && document.querySelector('#projList').textContent.length > 0")
    deadline = time.time() + 5
    while not held and time.time() < deadline:
        pg.wait_for_timeout(50)
    assert held, "/api/clips was never asked"
    pg.wait_for_timeout(800)                   # the request is pending all this while
    text = pg.locator("#projList").inner_text()
    assert "Reading the footage folder" in text and "No footage" not in text, text
    release.append(True)
    for r in held:
        r.continue_()
    pg.wait_for_function("document.querySelectorAll('#projList tr.row').length === 3")
    assert pg.evaluate("window.__sawNoFootage") is False
    # the empty Timeline: the editor's first action, and the first pass beside it
    pg.wait_for_selector("#tlEmpty .empty [data-testid=firstpass-empty-timeline]")
    empty = pg.locator("#tlEmpty .empty").inner_text()
    assert "Drag a clip here or press ," in empty and "Run first pass" in empty, empty
    assert "Nothing indexed" not in empty and "The footage" not in empty
    # one Run first pass offer each (lane FPUI's button), never a second link of ours
    assert pg.locator("#tlEmpty [data-testid=firstpass-empty-timeline]").count() == 1
    assert pg.locator("#tlEmpty a").count() == 0
    assert "press ," in pg.locator("#progEmpty").inner_text()
    # the Program offers no copy since wave 6 (verdict 5 punch 9): header + Timeline only
    assert pg.locator("#progEmpty [data-testid=firstpass-empty-program]").count() == 0
    assert pg.url.rstrip("/") == live_server.rstrip("/")
    assert pg.errors == []
    pg.context.close()

    # not held: rows within 2 s of open, a picture in a monitor within 5 s
    _configure(ingest_bin, tmp_path / "fresh2", ingest_bin["none"], None)
    pg = _page(browser, None, init=SAW_EMPTY)
    t0 = time.monotonic()
    pg.goto(live_server)
    pg.wait_for_function("document.querySelectorAll('#projList tr.row').length === 3",
                         timeout=2000)
    rows_s = time.monotonic() - t0
    # the poster at once, or the clip's own frame once the video has one
    pg.wait_for_function("""(() => { const im = document.querySelector('#srcPoster');
        const v = document.querySelector('#srcVideo');
        const poster = im && !im.hidden && im.complete && im.naturalWidth > 0
          && im.getBoundingClientRect().width > 50;
        const frame = v && v.classList.contains('live') && v.readyState >= 2
          && v.getBoundingClientRect().width > 50;
        return poster || frame; })()""", timeout=5000)
    pic_s = time.monotonic() - t0
    assert rows_s <= 2.0 and pic_s <= 5.0, (rows_s, pic_s)
    assert pg.locator("#srcTitle").inner_text() == "Source: CLIP_A.MP4"
    assert pg.evaluate("window.__sawNoFootage") is False
    # `,` puts the clip the Source opened on into the empty sequence, unheard as it is
    pg.wait_for_selector("#tlEmpty .empty")          # the board has mounted its timeline
    pg.evaluate("document.activeElement && document.activeElement.blur()")
    pg.keyboard.press(",")
    pg.wait_for_function("segs.length === 1 && segs[0].clip === 'CLIP_A.MP4'"
                         " && document.querySelectorAll('#tl .tl-lane[data-lane=V1] .blk').length === 1",
                         timeout=1000)
    assert pg.errors == []
    pg.context.close()


def test_make_a_first_cut_waits_while_the_first_pass_runs(live_server, ingest_bin, browser,
                                                          tmp_path):
    """Two ways to a first cut must not overlap: while the first pass runs (lane FPUI's
    `rcFirstPass.state()`), the empty Timeline's "Make a first cut" is disabled."""
    _configure(ingest_bin, tmp_path / "work", ingest_bin["heard"], {**SEED, "segments": []})
    pg = _page(browser, live_server)
    pg.wait_for_selector("#tlEmpty #firstCut", state="attached")
    pg.wait_for_function("window.rcFirstPass && rcFirstPass.state()", timeout=10000)
    assert "Drag a clip here or press ," in pg.locator("#tlEmpty .empty").inner_text()
    # ONE AI action at every first-pass state (verdict 4, punch 10): the pass's before and
    # while it runs — "Make a first cut" is not beside it — and this button once it has run
    one = """() => [...document.querySelectorAll('#tlEmpty button')].filter(b => b.getClientRects().length)
        .map(b => b.id || b.className)"""
    for state in ("never-run", "running", "stopped", "failed"):
        pg.evaluate(f"rcFirstPass.state = () => ({{ state: '{state}' }})")
        pg.wait_for_function("document.querySelector('#tlEmpty #firstCut').offsetParent === null", timeout=2500)
        assert pg.evaluate(one) == ["fp-empty-btn"], (state, pg.evaluate(one))
    pg.evaluate("rcFirstPass.state = () => ({ state: 'running' })")
    pg.wait_for_function("document.querySelector('#tlEmpty #firstCut').disabled", timeout=2500)
    pg.evaluate("rcFirstPass.state = () => ({ state: 'done' })")
    pg.wait_for_function("!document.querySelector('#tlEmpty #firstCut').disabled", timeout=2500)
    assert pg.locator("#tlEmpty #firstCut").is_visible()
    assert pg.evaluate(one) == ["firstCut"], pg.evaluate(one)
    assert pg.errors == []
    pg.context.close()
