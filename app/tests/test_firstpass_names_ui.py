"""The first-pass panel speaks the editor's words, in a real browser (verdict 9, punch 3).

  * A generated black clip — what an overwrite past the end of the sequence leaves in the
    gap (Premiere's own behaviour, `tl.placeAt`) — reads "Black Video" in the panel's
    "added by hand" list, never its file name `gen_black_<hash>`.

Every model call is scripted (test_firstpass.Scripted); the index runs with
test_index's stubbed tools over a copy of the fixture bin under tmp_path.
"""

from __future__ import annotations

import shutil
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import inference  # noqa: E402
from test_firstpass import Scripted, _end_jobs  # noqa: E402
from test_index import _stub_tools  # noqa: E402

BTN = '[data-testid="firstpass-button"]'
PANEL = '[data-testid="firstpass-panel"]'


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live(tmp_path, project, monkeypatch):
    import uvicorn
    import server

    model = Scripted()
    inference.set_backend(model)
    inference.reset_spend()
    monkeypatch.setenv("ROUGHCUT_BACKEND", "claude_cli")
    footage, sidecars = tmp_path / "bin" / "day1", tmp_path / "sidecars"
    footage.mkdir(parents=True)
    sidecars.mkdir()
    for stem in project["stems"]:
        shutil.copy(project["footage"] / f"{stem}.MP4", footage / f"{stem}.MP4")
        shutil.copy(project["sidecars"] / f"{stem}.audio.json", sidecars / f"{stem}.audio.json")
    server.configure(None, footage, sidecars, tmp_path / "work", proxies=False, visual=None)
    _stub_tools(server, monkeypatch, tmp_path)
    monkeypatch.setattr(server, "FIRSTPASS_POLL_S", 0.05)
    monkeypatch.setattr(server, "build_proxy",
                        lambda src, dest, orient: shutil.copy(src, dest) and "copied")
    server.FIRSTPASS.clear()
    server.INDEXES.clear()
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        _end_jobs(server)
        srv.should_exit = True
        th.join(timeout=10)
        server.FIRSTPASS.clear()
        inference.set_backend(None)


@pytest.fixture
def page(live):
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        try:
            pg = b.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(live + "/")
            pg.wait_for_function("window.tl && tl.el && tl.el.view && window.rcFirstPass"
                                 " && rcFirstPass.state()", timeout=20000)
            yield pg
            assert errors == [], errors
        finally:
            b.close()


def _segments(live) -> list[dict]:
    return httpx.get(live + "/api/project", timeout=20).json()["segments"]


def _until(pred, what: str, timeout: float = 30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        got = pred()
        if got:
            return got
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


def _wait_state(live, until, timeout=90.0) -> dict:
    return _until(lambda: (lambda s: s if s["state"] in until else None)(
        httpx.get(live + "/api/firstpass", timeout=20).json()), f"the pass to reach {until}",
        timeout)


def _open_panel(pg) -> str:
    pg.evaluate("rcFirstPass.refresh()")
    pg.locator(BTN).click()
    pg.wait_for_selector(PANEL, state="visible", timeout=5000)
    pg.wait_for_function("document.querySelector('[data-testid=\"firstpass-panel\"] "
                         ".fp-steps li') !== null", timeout=10000)
    return pg.locator(PANEL).inner_text()


def test_a_generated_black_clip_reads_black_video_in_the_panel(page, live):
    """Overwrite past the end: the gap is a generated black clip, and the re-run's list
    of what it keeps names it the way Premiere names it."""
    pg = page
    pg.evaluate("tl.insert({clip: 'CLIP_A.MP4', in: 0.5, out: 2.0}, null)")
    pg.evaluate("tl.placeAt({clip: 'CLIP_B.MP4', in: 0.5, out: 1.5}, tl.total() + 2, 'overwrite')")
    segs = _until(lambda: (lambda s: s if len(s) == 3 else None)(_segments(live)),
                  "the cut with its gap to be saved")
    gap = segs[1]
    assert gap["clip"].startswith("gen_black_") and gap.get("gap") is True, segs
    s = httpx.get(live + "/api/firstpass", timeout=20).json()
    keep = {r["clip"]: r for r in s["would"]["keep"]}
    assert keep[gap["clip"]]["name"] == "Black Video"
    assert all(r.get("name") for r in s["manual_edits"])
    text = _open_panel(pg)
    assert "shot 2 · Black Video 0:00.0–0:02.0 · added by hand" in text, text
    assert "gen_" not in text, text
    html = pg.locator(PANEL).inner_html()
    assert "gen_" not in html, "nor in a hover"


def test_a_generated_clip_reads_as_premiere_names_it():
    """The rule the panel and the server share (`firstpass.clip_name`)."""
    from roughcut import firstpass

    assert firstpass.clip_name("gen_black_78a418d8.mp4") == "Black Video"
    assert firstpass.clip_name("gen_colour_1a2b3c4d.mp4") == "Color Matte"
    assert firstpass.clip_name("gen_still_1a2b3c4d.mp4") == "Still"
    assert firstpass.clip_name("gen_still_1a2b3c4d.mp4",
                               {"kind": "still", "from_clip": "KX_05.MP4"}) == "KX_05 Still"
    assert firstpass.clip_name("gen_.mp4") == "Generated clip"
    assert firstpass.clip_name("CLIP_A.MP4") == "CLIP_A"
    row = firstpass._row({"id": "g1", "clip": "gen_black_78a418d8.mp4", "in": 0, "out": 2},
                         1, ["add"])
    assert row["name"] == "Black Video" and row["words"] == "added by hand"
    assert firstpass.rejected_lines([{"clip": "gen_black_78a418d8.mp4", "in": 0, "out": 2,
                                      "action": "dropped"}])[0].startswith(
        "Left out: Black Video 0:00.0–0:02.0")
