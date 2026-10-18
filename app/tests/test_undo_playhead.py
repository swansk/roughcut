"""The playhead after an undo past the end (redesign wave 13, lane SMALL4; verdict 9 punch 10).

The judge inserted a clip at the end of the sequence with `,`, pressed Ctrl+Z, and the
playhead stayed past the sequence's new end while the Program kept showing the removed
shot's last frame (`58c-after-undo-past-end.png`). Premiere's playhead never sits on
nothing after an undo. timeline.js now brings a playhead that was on the film back to the
film's new end after an undo or a redo, and cues the monitor on the cut as it now is; a
playhead the editor put past the end on purpose stays there.

Real Chromium, the suite's three-clip bin, the EDL re-seeded per test (two shots, 2 s each:
CLIP_A 1-3, CLIP_B 0-2), the insert made the way the judge made it: CLIP_C opened in the
Source monitor from the Project panel, the playhead at the end, `,`.

    uv run --with pytest --with fastapi --with uvicorn --with httpx --with numpy \
        --with playwright pytest app/tests/test_undo_playhead.py -q
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

SEED = {"variant": "T", "title": "undo playhead", "orient": "none", "story": "", "target_s": [5, 20],
        "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
                     {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}]}

# what the Program shows: the live buffer's clip and time, the rest poster when it is up
PROGRAM_JS = """() => {
  const v = liveVideo();
  const p = document.querySelector('#monPoster');
  const shown = (el) => !!el && el.style.visibility !== 'hidden' && !el.hidden
    && getComputedStyle(el).display !== 'none' && getComputedStyle(el).opacity !== '0';
  return {playhead: tl.state.playhead, total: tl.total(), n: segs.length,
          live: v ? decodeURIComponent(v.currentSrc || v.src || '') : '', t: v ? v.currentTime : null,
          idx: player.idx, poster: shown(p) ? decodeURIComponent(p.currentSrc || p.src || '') : ''};
}"""


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
def page(live_server, project):
    Path(project["edl"]).write_text(json.dumps(SEED, indent=1), encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.goto(live_server)
        pg.wait_for_function("segs.length === 2 && document.querySelectorAll('#tl .blk').length >= 2",
                             timeout=20000)
        yield pg
        assert pg.errors == [], pg.errors
        browser.close()


def _insert_c_at_the_end(pg) -> dict:
    """CLIP_C into the Source monitor from the Project panel, the playhead at the end, `,`."""
    pg.locator(".ws-tab[data-tool=project]").first.click()
    pg.locator("#projList tr.row", has_text="CLIP_C").first.click()
    pg.wait_for_function("window.rcSource && rcSource.clip === 'CLIP_C.MP4'", timeout=10000)
    pg.evaluate("tl.seek(tl.total())")
    pg.evaluate("document.activeElement && document.activeElement.blur()")
    pg.keyboard.press(",")
    pg.wait_for_function("segs.length === 3 && segs[2].clip === 'CLIP_C.MP4'", timeout=5000)
    # the monitor parks on the inserted shot's last frame, as the judge saw it
    pg.wait_for_function("liveVideo() && decodeURIComponent(liveVideo().currentSrc).includes('CLIP_C')"
                         " && !liveVideo().seeking", timeout=15000)
    return pg.evaluate(PROGRAM_JS)


def test_an_undone_insert_at_the_end_brings_the_playhead_back_to_the_cut(page):
    pg = page
    after = _insert_c_at_the_end(pg)
    assert after["total"] == pytest.approx(10.0, abs=0.05), after
    assert after["playhead"] == pytest.approx(after["total"], abs=0.05), after

    pg.locator("#pProgram .ws-head").click()
    pg.keyboard.press("Control+z")
    pg.wait_for_function("segs.length === 2", timeout=5000)
    # the playhead is on the film again, at its end
    pg.wait_for_function("tl.state.playhead <= tl.total() + 1e-6", timeout=3000)
    st = pg.evaluate(PROGRAM_JS)
    assert st["total"] == pytest.approx(4.0, abs=0.01), st
    assert st["playhead"] == pytest.approx(st["total"], abs=1e-6), st
    # and the Program shows the cut's last frame, never the shot that was taken out
    pg.wait_for_function("liveVideo() && decodeURIComponent(liveVideo().currentSrc).includes('CLIP_B')"
                         " && !liveVideo().seeking", timeout=15000)
    pg.wait_for_timeout(600)                       # the rest poster settles (restSoon, 450 ms)
    st = pg.evaluate(PROGRAM_JS)
    assert "CLIP_C" not in st["live"], f"the Program still shows the removed shot: {st}"
    assert "CLIP_B" in st["live"] and st["idx"] == 1, st
    assert st["t"] == pytest.approx(2.0, abs=0.1), f"not CLIP_B's last frame: {st}"
    assert "CLIP_C" not in st["poster"], f"the Program's poster is the removed shot: {st}"
    assert pg.locator("#tl .tl-head").bounding_box()["x"] <= \
        pg.locator("#tl .tl-end").bounding_box()["x"] + 1, "the playhead line is drawn past the end"

    # redo puts the shot back; the playhead (on the film) does not move
    pg.keyboard.press("Control+Shift+z")
    pg.wait_for_function("segs.length === 3", timeout=5000)
    st = pg.evaluate(PROGRAM_JS)
    assert st["playhead"] == pytest.approx(4.0, abs=1e-6) and st["total"] == pytest.approx(10.0, abs=0.05), st


def test_a_playhead_put_past_the_end_on_purpose_stays_through_an_undo(page):
    """Premiere lets the playhead sit past the end (verdict 5, punch 8); an undo of an edit
    that did not shorten the film past it leaves it there."""
    pg = page
    pg.evaluate("tl.seek(tl.total() + 3)")
    assert pg.evaluate("tl.state.playhead") == pytest.approx(7.0, abs=0.01)
    a = pg.evaluate("segs[0].id")
    pg.evaluate(f"tl.setRange('{a}', 1.5, null)")      # a trim: the film 0.5 s shorter, one undo
    pg.wait_for_function("Math.abs(tl.total() - 3.5) < 0.01", timeout=3000)
    pg.locator("#pProgram .ws-head").click()
    pg.keyboard.press("Control+z")
    pg.wait_for_function("Math.abs(tl.total() - 4.0) < 0.01", timeout=3000)
    assert pg.evaluate("tl.state.playhead") == pytest.approx(7.0, abs=0.01)
