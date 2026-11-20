"""A proposal that brings its own music (2026-11-20).

A first cut now comes with a bed from the music library and its cuts on the bed's beats
(`server.plan_music`). On the page: the proposal says which track and offers a box to
leave it out; ▶ Play it plays the proposal WITH that bed (it used to be silent of music,
so a cut made to the beat could not be heard before it was accepted); Accept puts the bed
on the cut in the same undo entry and saves it; with the box unticked the cut is accepted
without it. The bed is kept on the film's clock while it plays (`bedSync`).

No model is called: the waiting proposal is an Ask record on disk; the "library" is the
fixture's own 2 s track, named as the plan would name it.
"""

from __future__ import annotations

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

CUT = [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 2.0, "why": "hello"}]
BED = {"asset": "music/bed.wav", "duck": True, "duck_db": 12, "bed_lufs": -24, "fade_in": 0, "fade_out": 0}
PLAN = {
    "segments": [
        {"clip": "CLIP_A.MP4", "in": 0.5, "out": 3.0, "why": "the whole hello"},
        {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.5, "why": "the reply"},
        {"clip": "CLIP_C.MP4", "in": 1.0, "out": 3.0, "why": "the ride"},
    ],
    "notes": "A first cut.\n\nCut to the music (120 bpm): 2 of 2 cuts now land on a beat (was 0); none was moved into a line.",
    "music": BED,
    "music_lines": ["Click Parade by Kevin MacLeod, from the music library (bouncy): under the film, "
                    "dipping when people talk. Change or remove it in the Audio tab."],
    "beats": {"bpm": 120.0, "cuts": 2, "on": 2, "was": 0, "moved": 2},
}


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


@pytest.fixture
def waiting(project):
    import server
    Path(project["edl"]).write_text(json.dumps({
        "variant": "T", "title": "test cut", "orient": "none", "story": "",
        "target_s": [3, 10], "segments": CUT}, indent=1), encoding="utf-8")
    undo = server.undo_path()
    undo.unlink(missing_ok=True)
    asks = server.STATE["asks"]
    asks.mkdir(parents=True, exist_ok=True)
    for old in asks.glob("*.json"):
        old.unlink()
    path = asks / "beatprop.json"
    path.write_text(json.dumps({"job": "beatprop", "created": time.time() - 60, "note": "a first cut",
                                "story": "", "plan": PLAN, "waits": True}), encoding="utf-8")
    yield path
    path.unlink(missing_ok=True)
    undo.unlink(missing_ok=True)


def _open(browser, live_server):
    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.errors = errors
    pg.goto(live_server)
    pg.wait_for_function("window.tlLanes && typeof P !== 'undefined' && P && P.clips && window.tl && tl.state",
                         timeout=20000)
    pg.wait_for_selector("#proposal", state="visible", timeout=10000)
    return pg


def _saved_music(pg):
    return pg.evaluate("fetch('/api/project').then((r) => r.json()).then((p) => p.music || null)")


def test_the_proposal_names_its_music_and_accept_puts_it_on_the_cut(browser, live_server, waiting):
    pg = _open(browser, live_server)
    line = pg.inner_text("#proposalDiff .proposalMusic")
    assert "Click Parade by Kevin MacLeod" in line and "add this music when I accept" in line
    assert pg.is_checked("#proposalMusicKeep")
    assert pg.evaluate("music") is None                      # nothing is on the cut until Accept
    pg.click("#acceptProposal")
    pg.wait_for_function("music && music.asset === 'music/bed.wav'", timeout=5000)
    assert pg.eval_on_selector("#musicTrack", "e => e.value") == "music/bed.wav"
    pg.wait_for_function("fetch('/api/project').then((r) => r.json()).then((p) => !!(p.music && p.music.asset))",
                         timeout=8000)
    assert _saved_music(pg)["asset"] == "music/bed.wav" and _saved_music(pg)["duck"] is True
    # one undo entry: the cut and the bed go back together
    pg.keyboard.press("Control+z")
    pg.wait_for_function("music === null && segs.length === 1", timeout=5000)
    assert pg.errors == []


def test_unticked_the_cut_is_accepted_without_the_music(browser, live_server, waiting):
    pg = _open(browser, live_server)
    pg.uncheck("#proposalMusicKeep")
    pg.click("#acceptProposal")
    pg.wait_for_function("segs.length === 3", timeout=5000)
    pg.wait_for_function("fetch('/api/project').then((r) => r.json()).then((p) => p.segments.length === 3)",
                         timeout=8000)
    assert pg.evaluate("music") is None and _saved_music(pg) is None
    assert pg.errors == []


def test_play_it_plays_the_proposal_with_its_bed_on_the_films_clock(browser, live_server, waiting):
    pg = _open(browser, live_server)
    assert pg.evaluate("bed.el.paused")
    pg.evaluate("playProposal()")
    pg.wait_for_function("!bed.el.paused && bed.el.currentTime > 0.3 && (bed.el.dataset.src || '').includes('bed.wav')",
                         timeout=10000)
    # into the second shot (the film is 2.5 s in): the 2 s track has looped and is where the film is
    pg.wait_for_function("tl.state.playhead > 3.2 && !bed.el.paused", timeout=10000)
    off = pg.evaluate("""() => { const d = bed.el.duration || 2, w = tl.state.playhead % d;
        let x = Math.abs(bed.el.currentTime - w); return Math.min(x, d - x); }""")
    assert off < 0.15, off
    # played out: the bed stops with the proposal, and the cut still has no music
    pg.wait_for_function("bed.el.paused", timeout=15000)
    assert pg.evaluate("music") is None
    # unticked: the proposal plays without it
    pg.uncheck("#proposalMusicKeep")
    pg.evaluate("playProposal()")
    pg.wait_for_function("tl.state.playhead > 0.8", timeout=10000)
    assert pg.evaluate("bed.el.paused")
    assert pg.errors == []
