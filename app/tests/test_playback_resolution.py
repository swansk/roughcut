"""Playback Resolution as a visible setting (verdict 6 punch 10a).

The judge: Premiere plays at a reduced resolution too, but there it is a menu on the
Program monitor the editor can set to Full; here it was a hidden constant (the graded
picture drawn at most 640 px wide while playing, stretched to 840 px at 1920).

Now the Program's transport has Premiere's small menu, `#playRes`: Auto (the old rule,
the default), Full, 1/2, 1/4 — of the monitor's own size, while the picture plays; paused
is always full. grade.js reads it (`grade.playRes`, also its setter) and the choice is
kept per browser.

Under test, in real Chromium on two 1280×720 sources (proxied by the server's own
`ensure_proxies`) with a hand balance on both shots, so the grade canvas draws every
frame: Full makes the canvas the monitor's width while playing, 1/4 about a quarter of
it, Auto at most 640, paused full again, and the choice survives a reload.
"""

from __future__ import annotations

import json
import socket
import subprocess
import threading
import time

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

CLIP_S = 6.0
STEMS = ["PR_A", "PR_B"]
BALANCE = {"gain": [1.06, 1.0, 0.94], "exposure": 1.05, "knee": 0.8, "lift": 0.0}

# The picture the grade draws, as drawSize computes it: the video contained in the
# monitor's box (CSS px × the pixel ratio), never above the video's own size.
PICTURE_JS = """() => {
  const c = document.querySelector('#gradeCanvas');
  const box = c.parentElement.getBoundingClientRect();
  const v = liveVideo();
  const dpr = window.devicePixelRatio || 1;
  const s = Math.min(1, box.width * dpr / v.videoWidth, box.height * dpr / v.videoHeight);
  return { w: c.width, picture: v.videoWidth * s, box: box.width * dpr, vw: v.videoWidth,
           playing: !!player.playing, res: grade.playRes };
}"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def res_server(tmp_path_factory):
    import uvicorn
    import server
    from conftest import _sidecar

    root = tmp_path_factory.mktemp("playres")
    footage, sidecars, work = root / "footage", root / "sidecars", root / "work"
    for d in (footage, sidecars, work):
        d.mkdir()
    for k, stem in enumerate(STEMS):
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-nostdin",
             "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=24:duration={CLIP_S},hue=h={90 * k}",
             "-f", "lavfi", "-i", f"sine=frequency=440:duration={CLIP_S}",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", str(footage / f"{stem}.MP4")],
            check=True, capture_output=True)
        side = _sidecar(stem)
        side["duration_s"] = CLIP_S
        (sidecars / f"{stem}.audio.json").write_text(json.dumps(side), encoding="utf-8")
    segs = [{"clip": "PR_A.MP4", "in": 0.5, "out": 5.5, "why": "one", "id": "r1"},
            {"clip": "PR_B.MP4", "in": 0.5, "out": 5.5, "why": "two", "id": "r2"}]
    edl = {"variant": "T", "title": "playres", "orient": "none", "story": "",
           "target_s": [5, 20], "segments": segs,
           "colour": {"mode": "auto", "shots": {s["id"]: {"balance": dict(BALANCE)} for s in segs}}}
    edl_path = root / "edl.json"
    edl_path.write_text(json.dumps(edl, indent=1), encoding="utf-8")
    server.configure(edl_path, footage, sidecars, work, proxies=False,
                     visual=work / "no-visual")
    server.ensure_proxies([f"{s}.MP4" for s in STEMS])
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


def _ready(page) -> None:
    page.wait_for_selector("#tl .tl-lane[data-lane=V1] .blk")
    page.wait_for_function("window.grade && grade.ready && window.effects && typeof C !== 'undefined'"
                           " && C && C.shots && segs.length", timeout=20000)


def _drawn_while_playing(page) -> dict:
    """Play from the first shot's start; once the grade has drawn a few frames at the
    setting, its canvas as drawSize sized it; then stop."""
    page.evaluate("document.activeElement && document.activeElement.blur()")
    page.evaluate("cueAt(0, segs[0].in + 0.2)")
    page.wait_for_function("liveVideo() && liveVideo().readyState >= 3 && !liveVideo().seeking"
                           " && grade.lut && document.querySelector('#gradeCanvas.on')",
                           timeout=20000)
    page.keyboard.press("Space")
    page.wait_for_function("player.playing", timeout=5000)
    n0 = page.evaluate("grade.draws")
    page.wait_for_function(f"grade.draws >= {n0} + 6", timeout=10000)
    page.wait_for_timeout(300)
    got = page.evaluate(PICTURE_JS)
    page.keyboard.press("Space")
    page.wait_for_function("!player.playing", timeout=5000)
    return got


def _parked(page) -> dict:
    page.wait_for_function("document.querySelector('#gradeCanvas').width > 640", timeout=8000)
    return page.evaluate(PICTURE_JS)


def test_playback_resolution_is_a_menu_on_the_program_and_full_quarter_and_auto_size_the_grade(
        res_server):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        try:
            ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(res_server)
            _ready(page)
            if page.evaluate("grade.mode") == "off":
                pytest.skip("no WebGL in this browser: nothing draws the grade")
            menu = page.locator("#player .transport select#playRes")
            assert menu.is_visible()
            assert menu.locator("option").all_inner_texts() == ["Auto", "Full", "1/2", "1/4"]
            assert "Playback Resolution" in menu.get_attribute("title")
            # the default is the old rule: at most 640 px wide while playing
            assert menu.input_value() == "auto" and page.evaluate("grade.playRes") == "auto"
            auto = _drawn_while_playing(page)
            print("\nAUTO", auto, flush=True)
            assert auto["picture"] > 640, f"the monitor is too small to tell Auto from Full: {auto}"
            assert auto["w"] <= 640, auto

            # Full: the monitor's own size while playing
            menu.select_option("full")
            assert page.evaluate("grade.playRes") == "full"
            # the choice gives the keys back to the board (Space plays, not the menu)
            assert page.evaluate("document.activeElement && document.activeElement.id") != "playRes"
            full = _drawn_while_playing(page)
            print("FULL", full, flush=True)
            assert abs(full["w"] - full["picture"]) <= 2, full
            parked = _parked(page)
            assert abs(parked["w"] - parked["picture"]) <= 2, parked

            # 1/4: about a quarter of it
            menu.select_option("1/4")
            quarter = _drawn_while_playing(page)
            print("QUARTER", quarter, flush=True)
            assert abs(quarter["w"] - quarter["picture"] / 4) <= 2, quarter
            # paused is always full
            parked = _parked(page)
            assert abs(parked["w"] - parked["picture"]) <= 2, parked

            # kept per browser: a reload comes back on 1/4, and draws at it
            page.reload()
            _ready(page)
            assert page.locator("#playRes").input_value() == "1/4"
            assert page.evaluate("grade.playRes") == "1/4"
            again = _drawn_while_playing(page)
            assert abs(again["w"] - again["picture"] / 4) <= 2, again

            # the setter is the same switch as the menu
            page.evaluate("grade.playRes = '1/2'")
            assert page.locator("#playRes").input_value() == "1/2"
            half = _drawn_while_playing(page)
            assert abs(half["w"] - half["picture"] / 2) <= 2, half
            page.evaluate("grade.playRes = 'nonsense'")
            assert page.evaluate("grade.playRes") == "1/2"
            assert errors == [], errors
        finally:
            browser.close()
