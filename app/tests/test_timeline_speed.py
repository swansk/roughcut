"""INTAKE M13 — a shot's `speed` and generated clips on the board, driven in a real browser.

What the effects tool can now propose (roughcut/edits.py) the board has to honour:

  * a shot with `speed` is `(out − in) / speed` of film — on the ruler, in `#total`,
    in every lane's sum (`tl.dur` is the one place that arithmetic lives);
  * the monitor plays it at that rate (`video.playbackRate`), ends it at its `out` in
    clip time, and reports its film position at the rate;
  * the inspector's speed row writes `seg.speed` as one undo entry and the block wears
    a badge; the save carries it (`tl.forSave()`; the server lane keeps it on disk);
  * a generated clip (`gen_<kind>_<key>.mp4`, listed with a proxy, a poster and a
    `summary` line but no sidecar) draws a block that says what it is and plays;
  * `tlLanes.showGhost(segments)` draws a proposed cut on the ghost lane on request —
    ids `new:n` as added — and `clearGhost()` takes it down.

Same fixture pattern as test_timeline_ui.py: the real uvicorn server on a real port, the
synthetic three-clip bin, the EDL re-seeded per test. Skipped when playwright is absent.
"""

from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api",
                                     reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

GEN = "gen_black_test.mp4"


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
                     project["work"], proxies=False,
                     visual=project["work"] / "speed-visual", assets=project["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in project["stems"]])
    # The generated clip the server lane will make on Accept: a real 2 s black file the
    # monitor can play and the poster route can frame, served from the proxies dir.
    gen = Path(server.STATE["proxy_dir"]) / GEN
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i", "color=c=black:size=320x180:rate=24:duration=2",
         "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=2",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(gen)],
        check=True, capture_output=True)

    port = _free_port()
    config = uvicorn.Config(server.app, host="127.0.0.1", port=port,
                            log_level="error")
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
    gen.unlink(missing_ok=True)


SEED = [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first", "speed": 0.5},
        {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}]


def _seed(project, segments: list[dict]) -> None:
    Path(project["edl"]).write_text(json.dumps({
        "variant": "T", "title": "test cut", "orient": "none", "story": "",
        "target_s": [5, 20], "segments": segments,
    }, indent=1), encoding="utf-8")


@pytest.fixture
def page(live_server, project):
    """Two shots: CLIP_A 1.0–3.0 at 0.5× (4 s of film), CLIP_B 0.0–2.0 (2 s)."""
    _seed(project, SEED)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        pg = browser.new_page(viewport={"width": 1280, "height": 900})
        pg.goto(live_server)
        pg.wait_for_selector("#tl .blk")
        yield pg
        browser.close()


def ids(page) -> list[str]:
    return page.evaluate("segs.map(s => s.id)")


def wait_saved(page) -> None:
    page.wait_for_function(
        "document.querySelector('#saveState').textContent.startsWith('saved')",
        timeout=8000)


def on_disk(project) -> list[dict]:
    return json.loads(Path(project["edl"]).read_text(encoding="utf-8"))["segments"]


def left(page, selector: str, nth: int = 0) -> float:
    return page.evaluate(
        f"parseFloat(document.querySelectorAll('{selector}')[{nth}].style.left)")


def width(page, selector: str, nth: int = 0) -> float:
    return page.evaluate(
        f"parseFloat(document.querySelectorAll('{selector}')[{nth}].style.width)")


def x_of(page, film_t: float) -> float:
    return page.evaluate(f"tl.timeToX({film_t})")


# ------------------------------------------------------------------ film time

def test_a_half_speed_shot_is_twice_as_long_on_the_ruler_and_in_the_total(page):
    """2 s of CLIP_A at 0.5× is 4 s of film: the total says 0:06.0, the end mark sits at
    6 s, the first block is twice the second's width, shot 2 starts at 4 s, and a film
    time inside the slow shot maps to a clip time at the rate."""
    assert page.evaluate("tl.dur(segs[0])") == 4.0
    assert page.evaluate("tl.dur(segs[1])") == 2.0
    assert page.evaluate("tl.speedOf(segs[0])") == 0.5
    assert page.evaluate("tl.total()") == 6.0
    assert page.locator("#tl .tl-total").inner_text() == "0:06.0"
    assert page.locator("#total").inner_text() == "0:06.0"
    assert page.locator("#posTotal").inner_text() == "0:06.0"
    labels = page.evaluate(
        "[...document.querySelectorAll('#tl .tl-ruler .major label')].map(l => l.textContent)")
    assert labels[:7] == ["0:00", "0:01", "0:02", "0:03", "0:04", "0:05", "0:06"], labels
    assert left(page, "#tl .tl-end") == pytest.approx(x_of(page, 6.0), abs=0.5)
    assert page.evaluate("tl.filmStart(segs[1].id)") == 4.0
    zoom = page.evaluate("tl.state.zoom")
    assert width(page, "#tl .blk", 0) == pytest.approx(4.0 * zoom, abs=0.5)
    assert width(page, "#tl .blk", 1) == pytest.approx(2.0 * zoom, abs=0.5)
    assert left(page, "#tl .blk", 1) == pytest.approx(x_of(page, 4.0), abs=0.5)
    # 3 s of film is 1.5 s into the slow shot's clip range: 1.0 + 3 × 0.5
    at = page.evaluate("tl.shotAt(3.0)")
    assert at["index"] == 0 and at["clipT"] == 2.5
    assert page.evaluate("tl.shotAt(5.0)") == {"id": ids(page)[1], "index": 1, "clipT": 1.0}
    # the block says the film length and wears the badge; the plain shot wears none
    assert page.locator("#tl .blk").nth(0).locator(".dur").inner_text() == "4.0s"
    assert page.locator("#tl .blk").nth(0).locator(".speed:not([hidden])").inner_text() == "0.5×"
    assert page.locator("#tl .blk").nth(1).locator(".speed:not([hidden])").count() == 0
    # a split at 2 s of film cuts the clip at 2.0 (1.0 + 2 × 0.5) and both halves keep the rate
    first = ids(page)[0]
    page.evaluate(f"tl.split('{first}', 2.0)")
    assert page.evaluate("segs.map(s => [s.in, s.out, s.speed || 1])") == [
        [1.0, 2.0, 0.5], [2.0, 3.0, 0.5], [0.0, 2.0, 1]]
    assert page.locator("#tl .tl-total").inner_text() == "0:06.0"


def test_the_lanes_and_the_keys_sum_film_time_at_the_rate(page):
    """The drop slot after the slow shot is at 4 s; the keys' ↓ walks to the cut at 4 s;
    Q at 3 s of film puts the in-point at 2.5 s of clip (the rate, not 1:1); the music
    lane's dip under CLIP_A's speech (0.15–4.35 s of clip, within 1.0–3.0) is the whole
    4 s of the slow shot."""
    assert page.evaluate("tlLanes.slotAt(3.9).t") == 4.0
    assert page.evaluate("tlLanes.slotAt(3.9).beforeId") == ids(page)[1]
    page.locator("#tl .tl-ruler").click(position={"x": x_of(page, 1.0), "y": 8})
    page.keyboard.press("ArrowDown")
    assert page.evaluate("tl.state.playhead") == pytest.approx(4.0, abs=0.02)
    assert page.evaluate("player.idx") == 1
    page.keyboard.press("ArrowUp")
    assert page.evaluate("tl.state.playhead") == pytest.approx(0.0, abs=0.02)
    page.evaluate("tl.seek(3.0)")
    page.keyboard.press("q")
    assert page.evaluate("segs[0].in") == 2.5
    assert page.locator("#tl .tl-total").inner_text() == "0:03.0"     # 0.5 s of clip left = 1 s of film
    page.keyboard.press("Control+z")
    assert page.evaluate("segs[0].in") == 1.0
    a1 = "#tl .tl-xlane[data-lane=A1]"
    page.evaluate("dock.open('sound')")
    page.select_option("#musicTrack", "music/bed.wav")
    page.wait_for_function(f"document.querySelectorAll('{a1} .duck').length === 2", timeout=8000)
    zoom = page.evaluate("tl.state.zoom")
    assert left(page, f"{a1} .duck", 0) == pytest.approx(x_of(page, 0), abs=0.5)
    assert width(page, f"{a1} .duck", 0) == pytest.approx(4.0 * zoom, abs=0.5)
    assert left(page, f"{a1} .duck", 1) == pytest.approx(x_of(page, 4.15), abs=0.5)
    assert width(page, f"{a1} .bed") == pytest.approx(6.0 * zoom, abs=0.5)
    page.select_option("#musicTrack", "")


def test_a_trim_drag_on_a_slow_shot_moves_the_edge_at_the_rate(page):
    """Dragging the slow shot's out handle one film second of travel moves its out-point
    half a clip second — and the block grows a full film second."""
    page.evaluate("tl.trim.setMagnet(false)")
    # the last out of the film is the handle: move CLIP_B to the front so A's out is last
    b = ids(page)[1]
    page.evaluate(f"tl.move(['{b}'], '{ids(page)[0]}')")
    assert page.evaluate("segs.map(s => s.clip)") == ["CLIP_B.MP4", "CLIP_A.MP4"]
    zoom = page.evaluate("tl.state.zoom")
    handle = page.locator("#tl .blk[data-id='" + ids(page)[1] + "'] .tl-h.out")
    hb = handle.bounding_box()
    x0, y0 = hb["x"] + hb["width"] / 2, hb["y"] + hb["height"] / 2
    page.mouse.move(x0, y0)
    page.mouse.down()
    page.mouse.move(x0 - 0.5 * zoom, y0, steps=6)          # half a film second earlier
    page.mouse.move(x0 - 1.0 * zoom, y0, steps=6)          # one film second earlier
    page.mouse.up()
    assert page.evaluate("segs[1].out") == pytest.approx(2.5, abs=0.02)   # 3.0 − 1 s × 0.5
    assert page.locator("#tl .tl-total").inner_text() == "0:05.0"
    zoom_after = page.evaluate("tl.state.zoom")            # the foundation refits after the change
    assert width(page, "#tl .blk", 1) == pytest.approx(3.0 * zoom_after, abs=1.5)
    assert page.locator("#undo").get_attribute("title").startswith("undo: trim")


# ------------------------------------------------------------------ the monitor

def test_the_monitor_plays_a_slow_shot_at_its_rate_and_hands_over_at_its_out(page):
    """Playing the cut: the live buffer's playbackRate is 0.5 through the slow shot, the
    transport's film position is the clip offset at the rate, and the hand-over to
    CLIP_B (at 1×) comes at CLIP_A's out in clip time — 3.0 — which is 4 s of film."""
    page.locator("#playCut").click()
    page.wait_for_function("player.playing && player.idx === 0", timeout=10000)
    page.wait_for_function("liveVideo().currentTime > 1.2", timeout=10000)
    assert page.evaluate("liveVideo().playbackRate") == 0.5
    assert page.evaluate("liveVideo().dataset.speed") == "0.5"
    clip_t = page.evaluate("liveVideo().currentTime")
    film_t = page.evaluate("tl.state.playhead")
    assert film_t == pytest.approx((clip_t - 1.0) / 0.5, abs=0.25)
    assert film_t > 0.3
    # the hand-over: at CLIP_A's out (clip 3.0 = film 4.0), B plays at 1×
    page.wait_for_function("player.idx === 1", timeout=15000)
    assert page.evaluate("tl.state.playhead") == pytest.approx(4.0, abs=0.35)
    page.wait_for_function("liveVideo().currentTime > 0.05", timeout=10000)
    assert page.evaluate("liveVideo().playbackRate") == 1
    assert page.evaluate("liveVideo().dataset.speed") == "1"
    page.evaluate("pauseCut()")
    # the ruler cues by film time at the rate: 2 s of film is 2.0 s into CLIP_A's clip
    page.locator("#tl .tl-ruler").click(position={"x": x_of(page, 2.0), "y": 8})
    assert page.evaluate("player.idx") == 0
    page.wait_for_function("Math.abs(liveVideo().currentTime - 2.0) < 0.1", timeout=10000)
    assert page.locator("#pos").inner_text() == "0:02.0"
    # JKL's rate multiplies the shot's: L twice is 2× the shuttle, 1× on the buffer
    page.keyboard.press("l")
    page.wait_for_function("player.playing", timeout=10000)
    page.keyboard.press("l")
    assert page.evaluate("liveVideo().defaultPlaybackRate") == 2
    assert page.evaluate("liveVideo().playbackRate") == 1
    page.keyboard.press("k")
    assert page.evaluate("liveVideo().playbackRate") == 0.5


# ------------------------------------------------------------------ the inspector

def test_the_inspectors_speed_chip_writes_speed_as_one_undo_entry_and_the_save_carries_it(page, project):
    """Select CLIP_B (1×): the row's 1× chip is lit and the box says 1. Click ½×: `speed`
    is 0.5 on the segment, the total is 0:08.0, the block wears ½×, the undo entry is
    `speed`, and `tl.forSave()` carries it. 1× deletes the key. The number box takes any
    rate in 0.1–4 and clamps outside it. After the autosave the page still carries the
    rate (afterSave never drops it), and the disk does once the server keeps it."""
    page.locator("#tl .blk").nth(1).click()
    page.evaluate("pauseCut()")
    assert page.locator("#inspector .clip").inner_text() == "CLIP_B"
    assert page.locator("#inspector .speedrow button.on").inner_text() == "1×"
    assert page.locator("#inspector .speedNum").input_value() == "1"
    assert page.locator("#inspector .dur").inner_text() == "2.0 s"

    page.locator("#inspector .speedrow button[data-speed='0.5']").click()
    assert page.evaluate("segs[1].speed") == 0.5
    assert page.locator("#inspector .speedrow button.on").inner_text() == "½×"
    assert page.locator("#inspector .dur").inner_text() == "4.0 s at 0.5×"
    assert "2.0 s of clip → 4.0 s of film" in page.locator("#inspector .sfilm").inner_text()
    assert page.locator("#total").inner_text() == "0:08.0"
    assert page.locator("#tl .tl-total").inner_text() == "0:08.0"
    assert page.locator("#tl .blk").nth(1).locator(".speed:not([hidden])").inner_text() == "0.5×"
    assert page.locator("#undo").get_attribute("title").startswith("undo: speed")
    assert page.evaluate("tl.forSave().map(s => s.speed)") == [0.5, 0.5]
    wait_saved(page)
    assert page.evaluate("segs[1].speed") == 0.5, "afterSave keeps the rate on the page"
    disk = on_disk(project)[1]
    if "speed" in disk:                    # the server lane keeps it through PUT /api/project
        assert disk["speed"] == 0.5
    remote = page.evaluate("fetch('/api/project').then(r => r.json())")["segments"][1]
    if "speed" in remote:
        assert remote["speed"] == 0.5

    # one undo takes it back; redo brings it
    page.keyboard.press("Control+z")
    assert page.evaluate("'speed' in segs[1]") is False
    assert page.locator("#total").inner_text() == "0:06.0"
    page.keyboard.press("Control+Shift+z")
    assert page.evaluate("segs[1].speed") == 0.5

    # 1× is the absence of the key
    page.locator("#inspector .speedrow button[data-speed='1']").click()
    assert page.evaluate("'speed' in segs[1]") is False
    assert page.evaluate("tl.forSave()[1].speed") is None
    assert page.locator("#tl .blk").nth(1).locator(".speed:not([hidden])").count() == 0

    # the number box: any rate, clamped to 0.1–4, one entry per committed value
    num = page.locator("#inspector .speedNum")
    num.fill("2.5")
    num.press("Enter")
    assert page.evaluate("segs[1].speed") == 2.5
    assert page.locator("#inspector .dur").inner_text() == "0.8 s at 2.5×"
    assert page.locator("#inspector .speedrow button.on").count() == 0
    num.fill("9")
    num.press("Enter")
    assert page.evaluate("segs[1].speed") == 4
    assert page.locator("#inspector .speedNum").input_value() == "4"
    assert page.locator("#tl .blk").nth(1).locator(".speed:not([hidden])").inner_text() == "4×"
    # the API on its own: a rate outside the bounds clamps, the same rate is no entry
    assert page.evaluate(f"tl.setSpeed('{ids(page)[1]}', 4)") is False
    assert page.evaluate(f"tl.setSpeed('{ids(page)[1]}', 0.01)") is True
    assert page.evaluate("segs[1].speed") == 0.1
    # a duplicate keeps its rate (the keys are ignored while the box has the focus)
    num.blur()
    page.keyboard.press("Control+d")
    assert page.evaluate("segs.map(s => s.speed || 1)") == [0.5, 0.1, 0.1]


# ------------------------------------------------------------------ generated clips

def test_a_generated_clip_draws_a_block_that_says_what_it_is_and_plays(page, project):
    """A shot on `gen_black_test.mp4`, listed in `P.clips` the way the server lane will
    list it (proxy, poster, `summary`, `transcript: []`, no candidates or visual): the
    block wears its kind and the summary line, its poster is the clip's, nothing that
    reads a clip throws (the library's tabs, the inspector, the warnings), and a click on
    it plays it in the monitor from the proxy."""
    page.evaluate(f"""() => {{
      P.clips['{GEN}'] = {{
        clip: '{GEN}', stem: 'gen_black_test', duration: 2.0,
        proxy: '/media/proxy/{GEN}', poster: '/media/poster/gen_black_test.jpg',
        transcript: [], summary: 'black · 2.0 s', generated: true,
      }};
      tl.insert({{clip: '{GEN}', in: 0, out: 2.0, why: 'a slide'}}, null);
    }}""")
    assert page.locator("#tl .blk").count() == 3
    blk = page.locator("#tl .blk.gen")
    assert blk.count() == 1
    assert blk.locator(".name").inner_text() == "black"
    assert blk.locator(".line").inner_text() == "black · 2.0 s"
    assert blk.locator(".dur").inner_text() == "2.0s"
    assert blk.locator(".warn:not([hidden])").count() == 0
    assert blk.locator("img.poster").get_attribute("src") == "/media/poster/gen_black_test.jpg?t=0.00"
    assert "black · 2.0 s" in blk.get_attribute("title")
    assert page.locator("#tl .tl-total").inner_text() == "0:08.0"
    # the poster route frames the generated file like any proxy
    status = page.evaluate("fetch('/media/poster/gen_black_test.jpg?t=0.00').then(r => r.status)")
    assert status == 200
    # nothing that reads a clip assumes a sidecar
    page.evaluate("libTab = 'heard'; renderLibrary(); libTab = 'kept'; renderLibrary(); libTab = 'heard'; renderLibrary()")
    assert page.evaluate(f"boundaryWarning(segs[2]) === '' && linesFor(segs[2]).length === 0")
    assert page.evaluate(f"tl.snapsFor('{GEN}')") == {
        "clip": GEN, "sentences": [], "words": [], "onsets": [], "duration": 2.0}
    # the inspector: the summary in place of the transcript lines
    assert "black · 2.0 s" in page.locator("#inspector .lines:not(.seen)").inner_text()
    assert page.locator("#inspector .clip").inner_text() == "gen_black_test"
    # it plays from the proxy like any other shot
    blk.click()
    page.wait_for_function("player.playing && player.idx === 2", timeout=10000)
    page.wait_for_function(f"liveVideo().dataset.src === '/media/proxy/{GEN}'", timeout=10000)
    page.wait_for_function("liveVideo().currentTime > 0.3", timeout=10000)
    assert page.evaluate("liveVideo().playbackRate") == 1
    assert page.evaluate("liveVideo().error") is None
    page.evaluate("pauseCut()")
    # the save strips nothing it should not: the shot goes to disk as a shot
    page.wait_for_function("segs.every(s => s.id && s.id.startsWith('g'))", timeout=8000)
    assert on_disk(project)[2]["clip"] == GEN


# ------------------------------------------------------------------ showGhost

def test_show_ghost_draws_a_proposed_cut_with_new_ids_as_added_and_play_plan_plays_it(page):
    """`tlLanes.showGhost([...])` with CLIP_B kept, CLIP_A gone and a `new:1` shot on
    CLIP_C at 2×: the ghost lane shows without any Ask pending — B `same`, the new one
    `added` at B's film end and half the width of its clip range; A struck out on V1.
    `playPlan(0)` plays the ghost's first shot; `clearGhost()` takes it all down."""
    ghost = "#tl .tl-xlane[data-lane=ghost]"
    assert page.evaluate(f"document.querySelector('{ghost}').hidden") is True
    a, b = ids(page)
    ok = page.evaluate(f"""tlLanes.showGhost([
        {{id: '{b}', clip: 'CLIP_B.MP4', in: 0.0, out: 2.0, why: 'kept'}},
        {{id: 'new:1', clip: 'CLIP_C.MP4', in: 0.5, out: 2.5, why: 'new at 2×', speed: 2}},
    ])""")
    assert ok is True
    page.wait_for_function(f"!document.querySelector('{ghost}').hidden", timeout=5000)
    page.wait_for_function(f"document.querySelectorAll('{ghost} .ghost').length === 2", timeout=5000)
    zoom = page.evaluate("tl.state.zoom")
    classes = page.evaluate(
        f"[...document.querySelectorAll('{ghost} .ghost')].map(g => g.className)")
    assert classes[0].startswith("ghost same"), classes
    assert classes[1] == "ghost added", classes
    assert page.evaluate(f"document.querySelectorAll('{ghost} .ghost')[1].dataset.id") == "new:1"
    assert left(page, f"{ghost} .ghost", 0) == pytest.approx(x_of(page, 0), abs=0.5)
    assert width(page, f"{ghost} .ghost", 0) == pytest.approx(2.0 * zoom, abs=0.5)
    assert left(page, f"{ghost} .ghost", 1) == pytest.approx(x_of(page, 2.0), abs=0.5)
    assert width(page, f"{ghost} .ghost", 1) == pytest.approx(1.0 * zoom, abs=0.5)   # 2 s at 2×
    assert "2×" in page.locator(f"{ghost} .ghost").nth(1).locator(".dur").inner_text()
    # A is not in the list: struck out on V1, the whole 4 s of film it takes
    assert page.locator("#tl .tl-over .strike").count() == 1
    assert left(page, "#tl .tl-over .strike") == pytest.approx(x_of(page, 0), abs=0.5)
    assert width(page, "#tl .tl-over .strike") == pytest.approx(4.0 * zoom, abs=0.5)
    assert page.evaluate("tlLanes.shown().segments.length") == 2
    assert page.evaluate("tlLanes.ghost().ghosts.length") == 2
    # the cut itself is untouched
    assert page.evaluate("segs.map(s => s.clip)") == ["CLIP_A.MP4", "CLIP_B.MP4"]
    # playPlan plays the proposal, shot by shot, without playing the cut
    assert page.evaluate("tlLanes.playPlan(0)") is True
    page.wait_for_function("liveVideo().dataset.src.includes('CLIP_B')", timeout=10000)
    assert page.evaluate("tlLanes.mode()") == "proposal"
    assert not page.evaluate("player.playing")
    # the new shot plays at its rate when its turn comes
    page.wait_for_function("liveVideo().dataset.src.includes('CLIP_C')", timeout=15000)
    page.wait_for_function("liveVideo().playbackRate === 2", timeout=5000)
    # a generated clip in the list is drawn as a slide
    page.evaluate(f"""tlLanes.showGhost([
        {{id: 'new:2', clip: '{GEN}', in: 0, out: 1.0}},
        {{id: '{a}', clip: 'CLIP_A.MP4', in: 1.0, out: 3.0, speed: 0.5}},
        {{id: '{b}', clip: 'CLIP_B.MP4', in: 0.0, out: 2.0}},
    ])""")
    page.wait_for_function(f"document.querySelectorAll('{ghost} .ghost').length === 3", timeout=5000)
    assert page.locator(f"{ghost} .ghost.gen .name").inner_text() == "black"
    assert page.locator("#tl .tl-over .strike").count() == 0
    assert left(page, f"{ghost} .ghost", 1) == pytest.approx(x_of(page, 1.0), abs=0.5)
    # clearGhost: the lane goes, the strikes go, the cut is what it was
    assert page.evaluate("tlLanes.clearGhost()") is True
    page.wait_for_function(f"document.querySelector('{ghost}').hidden", timeout=5000)
    assert page.locator("#tl .tl-over .strike").count() == 0
    assert page.evaluate("tlLanes.shown()") is None
    assert page.evaluate("tlLanes.clearGhost()") is False
    assert page.evaluate("segs.map(s => s.clip)") == ["CLIP_A.MP4", "CLIP_B.MP4"]
