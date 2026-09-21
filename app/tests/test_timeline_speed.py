"""INTAKE M13 — a shot's `speed` and generated clips on the board, driven in a real browser.

What the effects tool can now propose (roughcut/edits.py) the board has to honour:

  * a shot with `speed` is `(out − in) / speed` of film — on the ruler, in `#total`,
    in every lane's sum (`tl.dur` is the one place that arithmetic lives);
  * the monitor ends it at its `out` in clip time and reports its film position at
    the rate;

Same fixture pattern as test_timeline_ui.py: the real uvicorn server on a real port, the
synthetic three-clip bin, the EDL re-seeded per test. Skipped when playwright is absent.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api",
                                     reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402


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
    # the block says the film length
    assert page.locator("#tl .blk").nth(0).locator(".dur").inner_text() == "4.0s"
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

