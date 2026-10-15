"""The Program monitor plays A3 and up (verdict 5 punch 5; wave 6 lane PLAYBACK).

The render mixes the added audio tracks' clips (EDL `audio`, roughcut/tracks.py
`audio_layers`) at their track's gain; the Program has to sound the same. Judged in a real
browser on the element that plays it: one clip of CLIP_B's sound laid on A3 at film 2.0 s
(clip 1.0-3.0) under a 6 s CLIP_A shot.

  * a seek inside it parks its <audio> on `in + (t - at)`; outside it nothing of A3 plays
  * playing through it, it plays in step with the picture, then stops after its end
  * A3's gain / mute / solo set its level through rcAudio (as A1's do)
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

from test_program_tracks import _clip, _sidecar  # noqa: E402

EDL = {
    "variant": "T", "title": "program a3", "orient": "none", "story": "",
    "target_s": [5, 20],
    "segments": [{"id": "s1", "clip": "CLIP_A.MP4", "in": 0.0, "out": 6.0, "why": "red"}],
    "tracks": {"A3": {"mute": False, "solo": False, "lock": False, "target": False, "gain_db": 0.0}},
    # A3: CLIP_B 1.0–3.0 laid at film 2.0 → sounds over film 2.0–4.0
    "audio": [{"id": "x1", "clip": "CLIP_B.MP4", "in": 1.0, "out": 3.0, "at": 2.0, "track": "A3"}],
}

AX = """(() => {
  const el = document.querySelector('#player audio[data-ax="x1"]');
  if (!el) return null;
  return { src: el.dataset.src, t: el.currentTime, paused: el.paused,
           level: window.rcAudio ? rcAudio.levelOf(el) : el.volume,
           film: tl.state.playhead };
})()"""


@pytest.fixture(scope="module")
def proj(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("proga3")
    footage, sidecars, work, assets = (root / d for d in ("footage", "sidecars", "work", "assets"))
    for d in (footage, sidecars, work, assets / "music"):
        d.mkdir(parents=True)
    for stem, colour, hz in (("CLIP_A", "red", 440), ("CLIP_B", "blue", 880)):
        _clip(footage / f"{stem}.MP4", colour, hz)
        (sidecars / f"{stem}.audio.json").write_text(json.dumps(_sidecar(stem)), encoding="utf-8")
    edl = root / "edl.json"
    edl.write_text(json.dumps(EDL, indent=1), encoding="utf-8")
    return {"root": root, "footage": footage, "sidecars": sidecars, "work": work,
            "assets": assets, "edl": edl}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(proj):
    import uvicorn
    import server

    server.configure(proj["edl"], proj["footage"], proj["sidecars"], proj["work"],
                     proxies=False, visual=proj["work"] / "no-visual", assets=proj["assets"])
    server.ensure_proxies(["CLIP_A.MP4", "CLIP_B.MP4"])
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
def page(live_server, proj):
    proj["edl"].write_text(json.dumps(EDL, indent=1), encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(live_server)
        pg.wait_for_selector("#tl .blk")
        pg.wait_for_function("tl.audioClips().length === 1 && segs.length === 1")
        yield pg
        assert not errors, errors
        browser.close()


def test_a3_is_parked_on_a_seek_inside_it_and_silent_outside(page):
    page.evaluate("tl.seek(3.0)")                 # 1.0 s into the A3 clip → clip 2.0
    page.wait_for_function(f"(() => {{ const a = {AX}; return a && Math.abs(a.t - 2.0) < 0.02; }})()",
                           timeout=5000)
    a = page.evaluate(AX)
    assert "CLIP_B" in a["src"], a
    assert a["paused"], "a seek parks it; it plays only with the monitor"
    assert a["level"] > 0.99, a
    page.evaluate("tl.seek(5.0)")                 # past its end: nothing of A3 sounds
    page.wait_for_timeout(200)
    a = page.evaluate(AX)
    assert a is None or a["paused"], a


def test_a3_plays_in_step_with_the_picture_and_stops_after_its_end(page):
    page.evaluate("tl.seek(1.6)")
    page.wait_for_timeout(300)
    page.evaluate("playFrom(0)")      # resumes from the playhead (playFrom's resume rule)
    page.wait_for_function("tl.state.playhead > 2.8", timeout=8000)
    a = page.evaluate(AX)
    assert a and not a["paused"], a
    # film 2.0 is clip 1.0: the clip runs `film - 1.0`, a tick either way
    assert abs(a["t"] - (a["film"] - 1.0)) < 0.15, a
    page.wait_for_function("tl.state.playhead > 4.3", timeout=8000)
    a = page.evaluate(AX)
    assert a is None or a["paused"], f"A3 still sounding after its clip ended: {a}"
    page.evaluate("pauseCut()")


def test_a3_follows_its_tracks_gain_mute_and_solo(page):
    page.evaluate("tl.seek(3.0)")
    page.wait_for_function(f"!!{AX}", timeout=5000)
    page.evaluate("tl.setTrack('A3', 'gain_db', -6)")
    page.evaluate("tl.seek(3.1)")
    page.wait_for_timeout(100)
    assert abs(page.evaluate(AX)["level"] - 10 ** (-6 / 20)) < 0.01
    page.evaluate("tl.setTrack('A3', 'mute', true)")
    page.evaluate("tl.seek(3.2)")
    page.wait_for_timeout(100)
    assert page.evaluate(AX)["level"] == 0
    page.evaluate("tl.setTrack('A3', 'mute', false)")
    page.evaluate("tl.setTrack('A1', 'solo', true)")   # another track soloed: A3 is out
    page.evaluate("tl.seek(3.3)")
    page.wait_for_timeout(100)
    assert page.evaluate(AX)["level"] == 0
    page.evaluate("tl.setTrack('A1', 'solo', false)")
    page.evaluate("tl.setTrack('A3', 'gain_db', 0)")
