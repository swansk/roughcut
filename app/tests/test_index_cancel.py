"""Stop everything (verdict 4, punch 8), driven in Chromium.

Verdict 4: "Stop leaves the preview ffmpeg running: there is still no /api/index/cancel."
After the first pass moves on, the index keeps encoding proxies and reading close looks,
and the previews keep building, with nothing on screen able to stop them. Now the index's
job pill carries an × that calls POST /api/index/cancel, which kills the index's tools and
every preview encode by their process groups.

Mocked so the test is not a race against a fast encode: every index stage after the probe
is a 60 s real-time ffmpeg, and so is a preview build. Done = within 5 s of the click no
ffmpeg is a child of the server (pgrep), the pill is gone, and every status is truthful:
no clip reads as proxied, no journal stage as running, done or failed for work that was
killed, no preview as failed.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

SLOW = ["ffmpeg", "-v", "error", "-nostdin", "-re", "-f", "lavfi",
        "-i", "testsrc2=size=160x90:rate=10", "-t", "60", "-f", "null", "-"]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ffmpeg_children() -> list[str]:
    """ffmpeg processes whose parent is this process — the server runs in it."""
    r = subprocess.run(["pgrep", "-a", "-P", str(os.getpid()), "ffmpeg"],
                       capture_output=True, text=True)
    return [line for line in r.stdout.splitlines() if line.strip()]


@pytest.fixture(scope="module")
def bin_(project, tmp_path_factory):
    """Its own bin and work dir: the index this test starts (and its journal) must not
    touch the session project's."""
    root = tmp_path_factory.mktemp("stopbin")
    footage, sidecars, work = root / "footage", root / "sidecars", root / "work"
    for d in (footage, sidecars, work):
        d.mkdir()
    for stem in project["stems"]:
        shutil.copy(Path(project["footage"]) / f"{stem}.MP4", footage / f"{stem}.MP4")
        shutil.copy(Path(project["sidecars"]) / f"{stem}.audio.json",
                    sidecars / f"{stem}.audio.json")
    edl = root / "edl.json"
    edl.write_text(json.dumps({
        "variant": "T", "title": "stop", "orient": "none", "story": "",
        "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "a"},
                     {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "b"}]}),
        encoding="utf-8")
    return {"footage": footage, "sidecars": sidecars, "work": work, "edl": edl,
            "assets": project["assets"], "stems": project["stems"]}


@pytest.fixture(scope="module")
def live_server(bin_):
    import uvicorn
    import server

    server.configure(bin_["edl"], bin_["footage"], bin_["sidecars"], bin_["work"],
                     proxies=False, visual=bin_["work"] / "visual", assets=bin_["assets"])
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


def test_the_index_pill_x_stops_the_index_and_the_previews(live_server, bin_, monkeypatch):
    import server

    real_stage = server._index_stage

    def slow_stage(j, clip, stage):
        if stage == "probe":
            return real_stage(j, clip, stage)
        r = subprocess.run(SLOW, capture_output=True, text=True)    # a 60 s "encode"
        if r.returncode != 0:
            raise RuntimeError(f"{stage} killed ({r.returncode})")
        return 0.0

    def slow_preview(src, dest, orient):
        r = subprocess.run(SLOW, capture_output=True, text=True)    # a 60 s preview build
        if r.returncode != 0:
            raise RuntimeError(f"preview killed ({r.returncode})")
        dest.write_bytes(b"x")
        return "cpu"

    monkeypatch.setattr(server, "_index_stage", slow_stage)
    monkeypatch.setattr(server, "build_preview", slow_preview)
    assert not ffmpeg_children(), "an ffmpeg was already running"

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        try:
            r = pg.request.post(live_server + "/api/index", data="{}",
                                headers={"content-type": "application/json"})
            assert r.ok, r.text()
            job = r.json()["job"]
            assert server.start_preview("CLIP_C.MP4") is not None     # an asked-for preview
            deadline = time.time() + 20
            while time.time() < deadline and len(ffmpeg_children()) < 2:
                time.sleep(0.1)
            kids = ffmpeg_children()
            assert len(kids) >= 2, f"the index and the preview should both be encoding: {kids}"
            running = {(c, s) for c, rec in server.load_journal().clips.items()
                       for s, st in rec["stages"].items() if st["state"] == "running"}
            assert running, "no index stage is running"

            pg.goto(live_server)
            pill = pg.locator(f".job[data-job='{job}']")
            pill.wait_for(state="visible", timeout=15000)
            x = pill.locator("[data-testid=job-cancel]")
            x.wait_for(state="visible", timeout=5000)
            t0 = time.time()
            x.click()
            # no ffmpeg child of the server within 5 s
            while time.time() - t0 < 5.0 and ffmpeg_children():
                time.sleep(0.05)
            left = ffmpeg_children()
            assert not left, f"still running {time.time() - t0:.1f}s after the ×: {left}"
            # the pill is gone, and stays gone through the next polls
            pg.wait_for_function(
                f"""!Array.from(document.querySelectorAll(".job[data-job='{job}']"))
                     .some((r) => r.offsetParent !== null)""", timeout=5000)
            time.sleep(2.5)
            assert not pill.is_visible()
            assert server.INDEXES[job]["state"] == "cancelled", server.INDEXES[job]
            st = pg.request.get(live_server + "/api/index").json()
            assert st["running"] is False, st
            # statuses: every stage that was killed is back in the queue — not running,
            # not done, not failed — and nothing reads as built that is not
            j = server.load_journal()
            for clip, stage in running:
                assert j.state(clip, stage) == "queued", (clip, stage, j.state(clip, stage))
            assert not any(st2["state"] == "running" for rec in j.clips.values()
                           for st2 in rec["stages"].values())
            clips = pg.request.get(live_server + "/api/clips").json()["clips"]
            for c in clips:
                assert c["proxy"] is False, c
                assert not c["looked"], c
                assert c["journal"]["stages"]["proxy"] in ("queued", "skipped"), c
            # the stopped preview is "not built", never "failed"
            assert not server.preview_path("CLIP_C").exists()
            assert str(server.preview_path("CLIP_C")) not in server.MEDIA["failed"]
            # the Project panel says no clip is indexed
            pg.wait_for_function("document.body.innerText.length > 0")
            assert errors == [], errors
        finally:
            browser.close()
            server.stop_media()
            for x2 in server.INDEXES.values():
                x2["cancel"] = True
            for sc in list(server.INDEX_SCOPES.values()):
                sc.kill()


def test_index_cancel_with_nothing_running_is_a_no_op(live_server):
    import httpx
    r = httpx.post(live_server + "/api/index/cancel", timeout=10)
    assert r.status_code == 200, r.text
    assert r.json()["stopped"] == []
