"""A whole-clip drop ends on the clip's frame grid (verdict 7, punch 9; wave 10 lane CRAFT).

Judge 7 dropped all of Killington's CLIP_05 (29.97) from the Project panel onto a cut
whose grid was 59.94 and got 0–35.1851: 2109 frames of 59.94, 1054.5 of the clip's own
29.97 — and the 29.97 export came out 16 ms (half a frame) shorter than `tl.total()`.
Two causes: the board was told the clip lasts its *sound's* 35.2 s (the sidecar's
`duration_s`), not a whole number of its frames, and the drop floored that to the
sequence's grid only. Now:

  * the server serves a clip's length as whole frames of its own rate (`/api/clips` and
    the project's clip records, which also carry the rate): floor(d × fps) / fps;
  * the timeline keeps an insert inside the clip's media on the clip's own grid;

so the dropped shot's `out × fps` is an integer, and `tl.total()` is the exported frame
count / fps. Two synthetic clips of their own (the session's three are all 24 fps):
CLIP_02 at 59.94 (the sequence's rate), CLIP_05 at 29.97 whose sidecar says 35.1851 s.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from fractions import Fraction
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

from conftest import _sidecar  # noqa: E402

TOOLS = Path(__file__).resolve().parents[2] / "research" / "tools"
NTSC30 = Fraction(30000, 1001)
NTSC60 = Fraction(60000, 1001)
CLIP05_S = 35.1851          # the judge's number: the sidecar's length of CLIP_05


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _clip(path: Path, rate: str, seconds: float) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate={rate}:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True)


@pytest.fixture(scope="module")
def grid_project(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("gridproj")
    footage, sidecars, work = root / "footage", root / "sidecars", root / "work"
    for d in (footage, sidecars, work):
        d.mkdir()
    _clip(footage / "CLIP_02.MP4", "60000/1001", 6.0)
    _clip(footage / "CLIP_05.MP4", "30000/1001", 35.3)       # picture past the sound, as GoPro's
    for stem, secs in (("CLIP_02", 6.0), ("CLIP_05", CLIP05_S)):
        side = _sidecar(stem)
        side["duration_s"] = secs
        (sidecars / f"{stem}.audio.json").write_text(json.dumps(side), encoding="utf-8")
    edl = root / "edl.json"
    edl.write_text(json.dumps({
        "variant": "T", "title": "grid", "orient": "none", "story": "", "target_s": [5, 60],
        # 0–2.002 s of the 59.94 clip: 120 of its frames, 60 of 29.97's (on both grids)
        "segments": [{"clip": "CLIP_02.MP4", "in": 0.0, "out": 2.002, "why": "first", "id": "s1"}],
    }), encoding="utf-8")
    return {"root": root, "footage": footage, "sidecars": sidecars, "work": work, "edl": edl}


@pytest.fixture(scope="module")
def live_server(grid_project):
    from concurrent.futures import wait
    import uvicorn
    import server

    p = grid_project
    server.configure(p["edl"], p["footage"], p["sidecars"], p["work"], proxies=False,
                     visual=p["work"] / "no-visual")
    server.ensure_proxies(["CLIP_02.MP4", "CLIP_05.MP4"])
    wait(server.warm_clip_facts(server.footage_clips()), timeout=30)   # the rates known
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


def _whole(x: float, rate: Fraction) -> bool:
    n = float(Fraction(x).limit_denominator(10 ** 7) * rate)
    return abs(n - round(n)) < 1e-3


def test_the_server_serves_lengths_in_whole_frames_of_the_clip(live_server):
    import server
    assert server.on_clip_grid(35.1851, 29.97) == pytest.approx(1054 / float(NTSC30), abs=1e-6)
    assert server.on_clip_grid(6.0, 24) == 6.0
    assert server.on_clip_grid(6.006, 29.97) == 6.006          # already whole: unchanged
    assert server.on_clip_grid(35.2, None) == 35.2             # no rate known: as it was
    import httpx
    clips = {c["clip"]: c for c in httpx.get(f"{live_server}/api/clips", timeout=30).json()["clips"]}
    d5 = clips["CLIP_05.MP4"]["duration"]
    assert d5 < CLIP05_S and _whole(d5, NTSC30), d5
    proj = httpx.get(f"{live_server}/api/project", timeout=30).json()
    rec = proj["clips"]["CLIP_05.MP4"]
    assert rec["duration"] == d5 and rec["fps"] == pytest.approx(29.97, abs=1e-3), rec
    assert _whole(proj["clips"]["CLIP_02.MP4"]["duration"], NTSC60)


def test_a_whole_clip_drop_ends_on_its_grid_and_exports_to_tl_total(live_server, grid_project, tmp_path):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(live_server)
        pg.wait_for_selector("#tl .blk")
        pg.wait_for_function("document.querySelectorAll('#projList tr.row').length === 2", timeout=15000)
        # the sequence's grid is the 59.94 clip's, as Killington's cut was when judged
        assert pg.evaluate("tl.gridRate()") == pytest.approx(float(NTSC60), abs=1e-4)

        # drag the whole CLIP_05 from the Project panel onto the timeline (dock.js's
        # dragstart → the lanes' drop), as the judge did
        dt = pg.evaluate_handle("() => new DataTransfer()")
        row = pg.locator("#projList tr.row").filter(has_text="CLIP_05")
        row.dispatch_event("dragstart", {"dataTransfer": dt})
        box = pg.locator("#tl .tl-view").bounding_box()
        at = {"dataTransfer": dt, "clientX": box["x"] + box["width"] - 20,
              "clientY": box["y"] + box["height"] / 2}
        pg.locator("#tl .tl-view").dispatch_event("dragover", at)
        pg.locator("#tl .tl-view").dispatch_event("drop", at)
        pg.wait_for_function("segs.some(s => s.clip === 'CLIP_05.MP4')", timeout=3000)
        shot = pg.evaluate("({...segs.find(s => s.clip === 'CLIP_05.MP4')})")
        assert shot["in"] == 0, shot
        out = shot["out"]
        assert _whole(out, NTSC30), f"out × 29.97 = {out * float(NTSC30):.4f}, not whole: {shot}"
        assert out <= CLIP05_S and out > CLIP05_S - 2 / float(NTSC30), shot      # the whole clip
        total = pg.evaluate("tl.total()")
        assert total == pytest.approx(2.002 + out, abs=1e-4)
        pg.wait_for_function("document.querySelector('#saveState').dataset.state === 'saved'",
                             timeout=10000)
        assert errors == [], errors
        browser.close()

    # export the cut on disk at 29.97, the way the board does (assemble.py, CFR at --fps)
    edl = json.loads(grid_project["edl"].read_text(encoding="utf-8"))
    assert [s["clip"] for s in edl["segments"]] == ["CLIP_02.MP4", "CLIP_05.MP4"], edl["segments"]
    assert edl["segments"][1]["out"] == pytest.approx(out, abs=1e-6)
    edl_path, film = tmp_path / "cut.json", tmp_path / "cut.mp4"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    r = subprocess.run(["uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(edl_path),
                        "--footage", str(grid_project["footage"]), "--profile", "match",
                        "--fps", "30000/1001", "-o", str(film)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
                            "-show_entries", "stream=nb_read_packets,avg_frame_rate", "-of", "json",
                            str(film)], capture_output=True, text=True, check=True)
    st = json.loads(probe.stdout)["streams"][0]
    assert Fraction(st["avg_frame_rate"]) == NTSC30, st
    frames = int(st["nb_read_packets"])
    assert frames == 60 + round(out * float(NTSC30)), (frames, out)
    assert frames / float(NTSC30) == pytest.approx(total, abs=1e-3), \
        f"tl.total() {total} against {frames} frames = {frames / float(NTSC30):.4f} s"
