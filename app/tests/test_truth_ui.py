"""Wave 23, lane TRUTH: the monitor tells the truth.

The Copper editors (judge/COPPER-EDITORS.md, area 2) could not trust the Program:

  * it went black on graded shots, over a video that was fine, and the grade's worker went on
    saying every draw was good (the graphics context had been lost; grade.js had no handler);
  * while a proposal waited, "Play it" ran the Program with its timecode stuck at 00:00:00:00 and
    the Program did not follow the playhead (ruler click, arrow keys);
  * after a ripple delete, an Accept or an undo, the Program showed the old frame under the new
    shot's label, and Program and timeline disagreed about the playhead;
  * the audio meters stayed at their last peak when playback stopped.

Each is pinned here in real Chromium (Playwright's default, software GL: the GPU launch leaks).
The context is lost on purpose (`grade.loseContext`, WEBGL_lose_context), in the worker and on
the page's own canvas. No model is called: a proposal is an Ask record on disk.
"""

from __future__ import annotations

import base64
import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

BALANCE = {"gain": [1.08, 1.0, 0.92], "exposure": 1.05, "knee": 0.8, "lift": 0.0}
CANVAS = "#gradeCanvas"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _segments():
    return [{"clip": "CLIP_A.MP4", "in": 0.5, "out": 3.5, "why": "one", "id": "t1"},
            {"clip": "CLIP_B.MP4", "in": 0.5, "out": 3.5, "why": "two", "id": "t2"},
            {"clip": "CLIP_C.MP4", "in": 0.5, "out": 3.5, "why": "three", "id": "t3"},
            {"clip": "CLIP_A.MP4", "in": 3.5, "out": 5.5, "why": "four", "id": "t4"}]


def _seed(project, segments=None, graded=True):
    segs = _segments() if segments is None else segments
    edl = {"variant": "T", "title": "truth", "orient": "none", "story": "",
           "target_s": [5, 20], "segments": segs}
    if graded and segs:
        edl["colour"] = {"mode": "auto", "shots": {s["id"]: {"balance": dict(BALANCE)}
                                                    for s in segs if s.get("id")}}
    Path(project["edl"]).write_text(json.dumps(edl, indent=1), encoding="utf-8")


@pytest.fixture(scope="module")
def live_server(project):
    import uvicorn
    import server

    original = project["edl"].read_text(encoding="utf-8")
    server.configure(project["edl"], project["footage"], project["sidecars"],
                     project["work"], proxies=False, assets=project["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in project["stems"]])
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started, "server did not start"
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    th.join(timeout=10)
    project["edl"].write_text(original, encoding="utf-8")


@pytest.fixture
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        yield b
        b.close()


def _waiting(job: str, plan: dict) -> Path:
    import server
    asks = server.STATE["asks"]
    asks.mkdir(parents=True, exist_ok=True)
    for old in asks.glob("*.json"):
        old.unlink()
    path = asks / f"{job}.json"
    path.write_text(json.dumps({"job": job, "created": time.time() + 3600, "note": "",
                                "story": "", "plan": plan}), encoding="utf-8")
    return path


def _open(browser, live_server, query="", blocks=True):
    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.goto(live_server + query)
    pg.wait_for_function("window.tlLanes && window.grade && typeof P !== 'undefined' && P && P.clips"
                         " && window.tl && tl.state && window.effects && typeof C !== 'undefined' && C && C.shots",
                         timeout=25000)
    if blocks:
        pg.wait_for_selector("#tl .tl-lane[data-lane=V1] .blk", timeout=15000)
    return pg


LUMA_JS = """async (b64) => {
  const blob = await (await fetch('data:image/png;base64,' + b64)).blob();
  const bm = await createImageBitmap(blob);
  const c = document.createElement('canvas'); c.width = bm.width; c.height = bm.height;
  const g = c.getContext('2d'); g.drawImage(bm, 0, 0);
  const d = g.getImageData(0, 0, c.width, c.height).data;
  let s = 0; for (let i = 0; i < d.length; i += 4) s += 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2];
  return s / (d.length / 4);
}"""


def _picture_luma(pg) -> float:
    """Mean luma of the Program's picture box, as a person sees it (a screenshot)."""
    box = pg.evaluate("""() => { const c = document.querySelector('#gradeCanvas');
      const r = (c.parentElement || c).getBoundingClientRect();
      return { x: r.x, y: r.y, width: r.width, height: r.height }; }""")
    png = pg.screenshot(clip=box)
    return pg.evaluate(LUMA_JS, base64.b64encode(png).decode())


def _park_graded(pg, i=1):
    pg.evaluate("document.activeElement && document.activeElement.blur()")
    pg.evaluate(f"cueAt({i}, segs[{i}].in + 0.8)")
    pg.wait_for_function(f"liveVideo() && liveVideo().readyState >= 2 && !liveVideo().seeking"
                         f" && grade.lut && document.querySelector('{CANVAS}.on')", timeout=20000)
    pg.wait_for_timeout(300)


# ------------------------------------------------------------------ (a) the lost context

@pytest.mark.parametrize("query,mode", [("", "worker"), ("?grade=main", "main")])
def test_a_lost_context_that_comes_back_is_drawn_again_never_black(browser, live_server, project, query, mode):
    _seed(project)
    pg = _open(browser, live_server, query)
    try:
        assert pg.evaluate("grade.mode") == mode
        _park_graded(pg)
        assert _picture_luma(pg) > 12, "the graded picture is up before the loss"
        draws = pg.evaluate("grade.draws")
        pg.evaluate("grade.loseContext(700)")
        pg.wait_for_function("grade.lost", timeout=3000)
        # while it is lost: the canvas is off, the raw picture shows, the tag says so
        assert pg.evaluate(f"!document.querySelector('{CANVAS}').classList.contains('on')")
        assert "restoring" in pg.locator("#ungraded").inner_text()
        assert pg.locator("#ungraded").is_visible()
        assert _picture_luma(pg) > 12, "never black while the context is gone"
        # ... and no draw is counted on a lost context
        pg.evaluate("grade.draw()")
        pg.wait_for_timeout(200)
        assert pg.evaluate("grade.draws") == draws
        # it comes back: graded again within a second of the restore, and the tag is gone
        pg.wait_for_function(f"!grade.lost && document.querySelector('{CANVAS}.on') && grade.draws > {draws}",
                             timeout=4000)
        assert not pg.locator("#ungraded").is_visible()
        assert _picture_luma(pg) > 12
        what = [e["what"] for e in pg.evaluate("grade.lostLog")]
        assert "lost" in what and "restored" in what and "gave up" not in what, what
        # and the next shot is graded too (the textures were made again)
        _park_graded(pg, 2)
        assert _picture_luma(pg) > 12
        assert pg.errors == []
    finally:
        pg.close()


@pytest.mark.parametrize("query,mode", [("", "worker"), ("?grade=main", "main")])
def test_a_context_that_does_not_come_back_shows_the_picture_and_says_why(browser, live_server, project, query, mode):
    _seed(project)
    pg = _open(browser, live_server, query)
    try:
        assert pg.evaluate("grade.mode") == mode
        _park_graded(pg)
        pg.evaluate("grade.loseContext(-1)")
        pg.wait_for_function("grade.lost", timeout=3000)
        assert _picture_luma(pg) > 12
        pg.wait_for_function("grade.lostLog.some(e => e.what === 'gave up')", timeout=6000)
        assert pg.locator("#ungraded").is_visible()
        assert "reload" in pg.locator("#ungraded").inner_text()
        assert "did not come back" in pg.locator("#ungraded").get_attribute("title")
        # one toast says it; the picture is still there; parking elsewhere does not turn it black
        assert "could not come back" in pg.locator("#toast, .toast").first.inner_text()
        pg.evaluate("cueAt(2, segs[2].in + 0.8)")
        pg.wait_for_timeout(1200)
        assert _picture_luma(pg) > 12
        assert pg.evaluate(f"!document.querySelector('{CANVAS}').classList.contains('on')")
        assert pg.errors == []
    finally:
        pg.close()


# ------------------------------------------------------------------ (d) the meters

def test_the_meters_fall_to_rest_when_playback_stops_even_if_frames_stop(browser, live_server, project):
    _seed(project)
    pg = _open(browser, live_server)
    try:
        pg.evaluate("document.activeElement && document.activeElement.blur()")
        pg.evaluate("cueAt(0, segs[0].in)")
        pg.wait_for_function("liveVideo() && liveVideo().readyState >= 2", timeout=15000)
        pg.keyboard.press("Space")
        pg.wait_for_function("rcMeters.sources() >= 1 && Math.max(...rcMeters.read().shown) > -30", timeout=10000)
        # a throttled tab: the animation frames stop for good (the editors' pinned bars)
        pg.evaluate("window.requestAnimationFrame = () => 0")
        pg.wait_for_timeout(150)
        pg.keyboard.press("Space")
        pg.wait_for_function("!player.playing", timeout=5000)
        pg.wait_for_function("rcMeters.read().shown.every(x => x === -Infinity)"
                             " && rcMeters.read().hold.every(x => x === -Infinity)", timeout=4000)
    finally:
        pg.close()


def test_the_meters_are_silent_after_the_stop_with_animation_frames_running(browser, live_server, project):
    _seed(project)
    pg = _open(browser, live_server)
    try:
        pg.evaluate("document.activeElement && document.activeElement.blur()")
        pg.evaluate("cueAt(0, segs[0].in)")
        pg.wait_for_function("liveVideo() && liveVideo().readyState >= 2", timeout=15000)
        pg.keyboard.press("Space")
        pg.wait_for_function("rcMeters.sources() >= 1 && Math.max(...rcMeters.read().shown) > -30", timeout=10000)
        pg.keyboard.press("Space")
        t0 = time.time()
        pg.wait_for_function("rcMeters.read().shown.every(x => x === -Infinity)", timeout=4000)
        assert time.time() - t0 < 3.5
    finally:
        pg.close()


# ------------------------------------------------------------------ (b) a waiting proposal

PLAN = {
    "segments": [
        {"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.5, "why": "open"},
        {"clip": "CLIP_B.MP4", "in": 1.0, "out": 3.0, "why": "reply"},
        {"clip": "CLIP_C.MP4", "in": 2.0, "out": 4.0, "why": "ride"},
    ],
    "notes": "A proposal.",
}

STATE_JS = """() => ({ pos: document.querySelector('#pos').textContent,
  total: document.querySelector('#total').textContent,
  ph: tl.state.playhead, btn: document.querySelector('#playCut').textContent,
  what: document.querySelector('#playingWhat').textContent,
  src: liveVideo() ? liveVideo().dataset.src : null,
  ct: liveVideo() ? liveVideo().currentTime : null,
  paused: liveVideo() ? liveVideo().paused : null })"""


def _ghost_at(pg, t):
    return pg.evaluate("""(t) => { const g = tlLanes.ghostAt(t); return g ? { clip: g.seg.clip, start: g.start,
      in: g.seg.in, out: g.seg.out } : null; }""", t)


def test_while_a_first_cut_waits_the_program_runs_and_follows_the_playhead(browser, live_server, project):
    _seed(project, [])
    path = _waiting("truth-first", PLAN)
    pg = _open(browser, live_server, blocks=False)
    try:
        pg.wait_for_selector("#proposal", state="visible", timeout=10000)
        pg.wait_for_function("tlLanes.ghost() && tlLanes.ghost().ghosts.length === 3", timeout=8000)
        total = pg.evaluate("tlLanes.ghost().total")
        assert total == pytest.approx(6.0, abs=0.05)
        # the Program's duration is the proposal's, not 00:00:00:00
        assert pg.evaluate("document.querySelector('#total').textContent") == pg.evaluate("tl.tc(tlLanes.ghost().total)")
        # Play it: the timecode runs with the picture, and the button says Pause
        pg.click("#playProposal")
        pg.wait_for_function("tl.state.playhead > 0.8 && !liveVideo().paused", timeout=10000)
        s = pg.evaluate(STATE_JS)
        assert s["pos"] != "00:00:00:00", s
        assert s["pos"] == pg.evaluate("tl.tc(tl.state.playhead)") or s["pos"] >= "00:00:00:20", s
        assert s["btn"] == "❚❚ Pause", s
        # the button stops it where it is
        pg.click("#playCut")
        pg.wait_for_function("liveVideo().paused", timeout=3000)
        assert pg.evaluate("document.querySelector('#playCut').textContent") == "▶ Play"
        pos = pg.evaluate("document.querySelector('#pos').textContent")
        assert pos == pg.evaluate("tl.tc(tl.state.playhead)")
        # a ruler click: the Program parks on the proposal's shot there, with that frame and timecode
        box = pg.locator("#tl .tl-ruler").bounding_box()
        for frac in (0.12, 0.85):
            x = box["x"] + box["width"] * frac
            pg.mouse.click(x, box["y"] + box["height"] / 2)
            pg.wait_for_function("liveVideo() && liveVideo().readyState >= 2 && !liveVideo().seeking", timeout=8000)
            pg.wait_for_timeout(400)
            ph = pg.evaluate("tl.state.playhead")
            g = _ghost_at(pg, ph)
            s = pg.evaluate(STATE_JS)
            assert s["src"].endswith(g["clip"].lower().replace(".mp4", ".mp4")) or g["clip"].split(".")[0] in s["src"], (s, g)
            want = g["in"] + (ph - g["start"])
            assert abs(s["ct"] - want) < 0.3, (s, g, ph)
            assert s["pos"] == pg.evaluate("tl.tc(tl.state.playhead)"), s
            assert s["what"].startswith("proposal"), s
        # arrow keys step a frame and the Program follows (timecode, frame)
        before = pg.evaluate("tl.state.playhead")
        pg.keyboard.press("ArrowLeft")
        pg.wait_for_timeout(500)
        after = pg.evaluate("tl.state.playhead")
        assert after < before
        assert pg.evaluate("document.querySelector('#pos').textContent") == pg.evaluate("tl.tc(tl.state.playhead)")
        # Up / Down walk the proposal's shot starts
        pg.keyboard.press("Home")
        pg.wait_for_timeout(300)
        assert pg.evaluate("tl.state.playhead") == pytest.approx(0, abs=0.01)
        assert pg.errors == []
    finally:
        path.unlink(missing_ok=True)
        pg.close()


def test_a_click_while_a_proposal_plays_stops_it_and_parks_there(browser, live_server, project):
    _seed(project, [])
    path = _waiting("truth-click", PLAN)
    pg = _open(browser, live_server, blocks=False)
    try:
        pg.wait_for_selector("#proposal", state="visible", timeout=10000)
        pg.wait_for_function("tlLanes.ghost() && tlLanes.ghost().ghosts.length === 3", timeout=8000)
        pg.click("#playProposal")
        pg.wait_for_function("tl.state.playhead > 0.4 && !liveVideo().paused", timeout=10000)
        box = pg.locator("#tl .tl-ruler").bounding_box()
        pg.mouse.click(box["x"] + box["width"] * 0.7, box["y"] + box["height"] / 2)
        pg.wait_for_function("liveVideo().paused", timeout=4000)
        pg.wait_for_timeout(600)
        ph = pg.evaluate("tl.state.playhead")
        assert ph > 3.0, ph
        g = _ghost_at(pg, ph)
        s = pg.evaluate(STATE_JS)
        assert g["clip"].split(".")[0] in s["src"], (s, g)
        assert s["btn"] == "▶ Play"
        assert s["pos"] == pg.evaluate("tl.tc(tl.state.playhead)")
    finally:
        path.unlink(missing_ok=True)
        pg.close()


def test_over_a_cut_the_played_proposal_is_followed_by_the_ruler(browser, live_server, project):
    _seed(project)
    path = _waiting("truth-over", PLAN)
    pg = _open(browser, live_server)
    try:
        pg.wait_for_selector("#proposal", state="visible", timeout=10000)
        pg.wait_for_function("tlLanes.ghost() && tlLanes.ghost().ghosts.length === 3", timeout=8000)
        pg.click("#playProposal")
        pg.wait_for_function("tl.state.playhead > 0.5 && !liveVideo().paused", timeout=10000)
        assert pg.evaluate("document.querySelector('#pos').textContent") != "00:00:00:00"
        pg.click("#playCut")                       # pause the proposal
        pg.wait_for_function("liveVideo().paused", timeout=3000)
        box = pg.locator("#tl .tl-ruler").bounding_box()
        pg.mouse.click(box["x"] + box["width"] * 0.45, box["y"] + box["height"] / 2)
        pg.wait_for_function("liveVideo().readyState >= 2 && !liveVideo().seeking", timeout=8000)
        pg.wait_for_timeout(500)
        ph = pg.evaluate("tl.state.playhead")
        g = _ghost_at(pg, ph)
        s = pg.evaluate(STATE_JS)
        assert g["clip"].split(".")[0] in s["src"], (s, g, ph)
        assert s["what"].startswith("proposal"), s
        assert s["pos"] == pg.evaluate("tl.tc(tl.state.playhead)")
    finally:
        path.unlink(missing_ok=True)
        pg.close()


# ------------------------------------------------------------------ (c) after an edit

HONEST_JS = """() => {
  const v = liveVideo(), t = tl.state.playhead, at = tl.shotAt(t);
  const seg = at ? segs[at.index] : null;
  return { idx: player.idx, at: at ? at.index : null, id: at ? at.id : null,
           src: v ? v.dataset.src : null, want: seg ? P.clips[seg.clip].proxy : null,
           ct: v ? v.currentTime : null, clipT: at ? at.clipT,
           pos: document.querySelector('#pos').textContent, tc: tl.tc(t),
           what: document.querySelector('#playingWhat').textContent,
           n: segs.length };
}""".replace("clipT: at ? at.clipT,", "clipT: at ? at.clipT : null,")


def _settled(pg):
    pg.wait_for_function("liveVideo() && !liveVideo().seeking && liveVideo().readyState >= 2", timeout=10000)
    pg.wait_for_timeout(350)


def _honest(pg, tag=""):
    s = pg.evaluate(HONEST_JS)
    assert s["at"] is not None, (tag, s)
    assert s["idx"] == s["at"], (tag, "the Program's shot is not the playhead's", s)
    assert s["src"] == s["want"], (tag, "the Program holds another clip than the shot under the playhead", s)
    assert abs(s["ct"] - s["clipT"]) < 0.4, (tag, "the Program's frame is not the playhead's", s)
    assert s["pos"] == s["tc"], (tag, "the Program's timecode is not the timeline's", s)
    assert s["what"].startswith(f"{s['at'] + 1}/{s['n']} ·"), (tag, "the label is another shot's", s)


def test_after_a_ripple_delete_the_program_shows_the_shot_now_under_the_playhead(browser, live_server, project):
    _seed(project)
    pg = _open(browser, live_server)
    try:
        # park inside shot 3 (CLIP_C), then delete shot 1 and shot 2: shot 3 slides to the front
        pg.evaluate("cueAt(2, segs[2].in + 1.0)")
        _settled(pg)
        _honest(pg, "parked")
        pg.evaluate("tl.select(['t1']); tl.remove(['t1'], { ripple: true })")
        _settled(pg)
        _honest(pg, "after the first delete")
        # delete the shot the monitor is parked on: the next one slides under the playhead
        pg.evaluate("tl.seek(1.0)")
        _settled(pg)
        _honest(pg, "parked in shot 1")
        pg.evaluate("tl.select(['t2']); tl.remove(['t2'], { ripple: true })")
        _settled(pg)
        _honest(pg, "after the second delete")
        # undo twice: the shots come back and the Program is of the cut as it is again
        pg.keyboard.press("Control+z")
        _settled(pg)
        _honest(pg, "after undo")
        pg.keyboard.press("Control+z")
        _settled(pg)
        _honest(pg, "after the second undo")
        assert pg.errors == []
    finally:
        pg.close()


def test_after_accept_the_program_shows_the_new_cut_at_the_playhead(browser, live_server, project):
    _seed(project)
    ids = [s["id"] for s in _segments()]
    plan = {"segments": [
        {"clip": "CLIP_C.MP4", "in": 3.0, "out": 5.0, "why": "new open"},
        {"clip": "CLIP_B.MP4", "in": 3.0, "out": 5.0, "why": "new reply"},
        {"id": ids[0], "clip": "CLIP_A.MP4", "in": 0.5, "out": 3.5, "why": "kept"},
    ], "notes": "Re-cut."}
    path = _waiting("truth-accept", plan)
    pg = _open(browser, live_server)
    try:
        pg.wait_for_selector("#proposal", state="visible", timeout=10000)
        pg.evaluate("cueAt(3, segs[3].in + 0.5)")           # far into the old cut, shot 4 (CLIP_A)
        _settled(pg)
        pg.evaluate("tl.setPlayhead(5.0)")
        pg.evaluate("tl.seek(5.0)")
        _settled(pg)
        pg.click("#acceptProposal")
        pg.wait_for_function("segs.length === 3", timeout=8000)
        _settled(pg)
        _honest(pg, "after accept")
        pg.keyboard.press("Control+z")
        pg.wait_for_function("segs.length === 4", timeout=8000)
        _settled(pg)
        _honest(pg, "after undo of the accept")
        assert pg.errors == []
    finally:
        path.unlink(missing_ok=True)
        pg.close()
