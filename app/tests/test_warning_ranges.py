"""No zero-length warning ranges (redesign wave 13, lane SMALL4; verdict 9, punch 3).

The judge's Effect Controls read "⚠ 0:13.2–0:13.2 the lens is covered"
(`40-1440-13-effect-controls.png`): Killington's visual pass flags single frames as
`{"start": 200.2, "end": 200.2, "why": "Glove fills about half the frame…"}`, and the
shot strip printed the moment as a range from it to itself. app.js `shotWarnings` now
writes a stretch shorter than one frame as one time, drops such a moment when it does not
say what is wrong, and keeps real stretches as ranges.

Real Chromium, the suite's bin, shot 1 = CLIP_A 1.0-3.0 s with a visual sidecar for CLIP_A.

    uv run --with pytest --with fastapi --with uvicorn --with httpx --with numpy \
        --with playwright pytest app/tests/test_warning_ranges.py -q
"""

from __future__ import annotations

import json
import re
import socket
import threading
import time
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

SEED = {"variant": "T", "title": "warnings", "orient": "none", "story": "", "target_s": [5, 20],
        "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
                     {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}]}
UNUSABLE = [
    # a single frame, as Killington's pass writes one (CLIP_01 at 200.2 s)
    {"start": 2.0, "end": 2.0, "why": "Glove fills about half the frame, though the snow and pole stay partly visible."},
    # a moment that says nothing about what is wrong: not shown
    {"start": 1.5, "end": 1.5, "why": ""},
    # shorter than a frame (1/24 s) but not zero
    {"start": 2.4, "end": 2.42, "why": "lens covered by snow, uniform blue frame"},
    # a real stretch stays a range
    {"start": 2.5, "end": 2.8, "why": "black frames"},
]


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


def test_a_moment_reads_as_one_time_and_a_silent_one_is_not_shown(live_server, project):
    import server

    Path(project["edl"]).write_text(json.dumps(SEED, indent=1), encoding="utf-8")
    vdir = Path(server.STATE["visual"])
    vdir.mkdir(parents=True, exist_ok=True)
    sidecar = vdir / "CLIP_A.visual.json"
    sidecar.write_text(json.dumps({"clip": "CLIP_A.MP4", "moments": [], "unusable": UNUSABLE,
                                   "summary": "a run"}), encoding="utf-8")
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
            pg = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(live_server)
            pg.wait_for_selector("#tl .blk")
            pg.locator("#tl .blk").nth(0).click()
            insp = pg.locator("#inspector")
            pg.wait_for_function("(() => { const w = document.querySelector('#inspector .warns');"
                                 " return w && !w.hidden && w.querySelectorAll('.w').length > 0; })()",
                                 timeout=8000)
            # the visual pass's warnings (the transcript's "starts mid-sentence" ones aside)
            bad = insp.locator(".warns .w:has(button[data-act=trimbad])")
            said = [" ".join(t.split()) for t in bad.all_inner_texts()]
            assert said == ["⚠ 0:01.0 the lens is covered · Trim it out",
                            "⚠ 0:01.4 the lens is covered · Trim it out",
                            "⚠ 0:01.5–0:01.8 goes dark · Trim it out"], said
            # no range from a time to itself, anywhere in the strip
            assert not re.search(r"(\d+:\d+(?:\.\d)?)–\1\b", insp.locator(".warns").inner_text())
            # each keeps the pass's own sentence as its hover, and its own fix: the second
            # one's Trim it out is the stretch at 2.4 s (the silent moment dropped before it)
            titles = bad.evaluate_all("els => els.map(e => e.title)")
            assert titles == [UNUSABLE[0]["why"], UNUSABLE[2]["why"], UNUSABLE[3]["why"]], titles
            bad.nth(1).locator("button[data-act=trimbad]").click()
            # keepTail (0.58 s) < keepHead (1.4 s): the out comes in before 2.4 s, on a frame
            pg.wait_for_function("segs[0].out < 2.4", timeout=3000)
            assert pg.evaluate("segs[0].in") == pytest.approx(1.0, abs=1e-6)
            assert pg.evaluate("segs[0].out") == pytest.approx(2.375, abs=1e-3)
            assert errors == [], errors
            browser.close()
    finally:
        sidecar.unlink(missing_ok=True)
