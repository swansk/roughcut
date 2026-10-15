"""The Export dialog (REDESIGN-GOAL dimension 8, verdict 2 punch 5), driven in Chromium.

The judge: "Export is two buttons … Ctrl+M does nothing: there is no export dialog with a
name, folder or size." Ctrl+M, File › Export… and the header's Export open one modal with
a preset, a file name, a folder under --work/renders, the duration and an estimated size;
an export shows a real % (ffmpeg's own progress against the sequence's length) and ends
with a file whose length is the sequence's, at the path the dialog says.

The data-testid hooks asserted here are the ones the journey test (tools/journey.py)
drives: export-dialog, export-preset, export-name, export-go, export-progress,
export-done (data-path), export-cancel.
"""
from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

# Five shots, ~19 s: long enough that the bar is seen between 0 and 100.
SEGMENTS = [
    {"clip": "CLIP_A.MP4", "in": 0.5, "out": 4.5, "why": "one"},
    {"clip": "CLIP_B.MP4", "in": 0.0, "out": 4.0, "why": "two"},
    {"clip": "CLIP_C.MP4", "in": 1.0, "out": 5.0, "why": "three"},
    {"clip": "CLIP_A.MP4", "in": 2.0, "out": 5.0, "why": "four"},
    {"clip": "CLIP_B.MP4", "in": 1.0, "out": 5.0, "why": "five"},
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
    project["edl"].write_text(original, encoding="utf-8")


@pytest.fixture
def page(live_server, project, monkeypatch):
    import server
    # the bar is re-read from ffmpeg's -progress files on this clock
    monkeypatch.setattr(server, "RENDER_TICK_S", 0.05)
    seed = {"variant": "T", "title": "test cut", "orient": "none", "story": "",
            "target_s": [5, 30], "segments": SEGMENTS}
    Path(project["edl"]).write_text(json.dumps(seed, indent=1), encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(live_server)
        pg.wait_for_selector("#tl .blk")
        pg.wait_for_function("tl.state && segs.length === 5", timeout=15000)
        yield pg
        assert not errors, errors
        browser.close()


DIALOG = "[data-testid=export-dialog]"


def is_open(pg) -> bool:
    return pg.evaluate("document.getElementById('exportDialog').open")


def wait_settings(pg) -> None:
    pg.wait_for_selector(f"{DIALOG} [data-testid=export-go]", timeout=5000)
    assert is_open(pg)


def ffprobe_duration(path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True, check=True)
    return float(r.stdout.strip().strip(","))


def ffprobe_rates(path: str) -> dict:
    """The video stream's r_frame_rate, avg_frame_rate, size and frame count."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=r_frame_rate,avg_frame_rate,width,height,nb_frames",
                        "-of", "json", path], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)["streams"][0]


def rational(fps: float) -> str:
    """The sequence's rate as ffprobe writes it (the server's own rule)."""
    import server
    return server.fps_rational(fps) + ("" if "/" in server.fps_rational(fps) else "/1")


def size_estimate_mb(pg) -> float:
    return float(pg.get_attribute("#exSize", "data-mb"))


def watch_bar(pg) -> None:
    """Record every aria-valuenow the bar ever shows, not just what a poll catches."""
    pg.evaluate("""() => { window.__bar = [];
      const d = document.getElementById('exportDialog');
      const read = () => { const b = d.querySelector('[data-testid=export-progress]');
        if (b) window.__bar.push(Number(b.getAttribute('aria-valuenow'))); };
      new MutationObserver(read).observe(d, {subtree: true, childList: true, attributes: true,
                                             attributeFilter: ['aria-valuenow']}); }""")


def export(pg, name: str, folder: str, preset: str = "quick") -> str:
    """Fill the dialog, press Export, wait for the end; the path it names."""
    wait_settings(pg)
    pg.select_option("[data-testid=export-preset]", preset)
    pg.fill("[data-testid=export-name]", name)
    pg.fill("#exFolder", folder)
    watch_bar(pg)
    pg.click("[data-testid=export-go]")
    pg.wait_for_selector("[data-testid=export-done]", timeout=240000)
    return pg.get_attribute("[data-testid=export-done]", "data-path")


# ---------------------------------------------------------------- opening it

def test_ctrl_m_opens_the_export_dialog_with_name_folder_duration_and_size(page, project):
    assert not is_open(page)
    page.keyboard.press("Control+m")
    wait_settings(page)
    # a preset, a file name from the cut, a folder under --work/renders
    labels = page.eval_on_selector_all("[data-testid=export-preset] option", "os => os.map(o => o.text)")
    assert labels == ["Quick 1080p", "Match Sequence", "Master 4K", "YouTube 1080p"], labels
    assert page.input_value("[data-testid=export-name]") == "footage - edl"
    assert page.input_value("#exFolder") == "footage"
    renders = str(Path(project["work"]) / "renders")
    assert page.inner_text("#exPath") == f"{renders}/footage/footage - edl.mp4"
    # the duration is the sequence's, in timecode; the size an estimate from the bitrate
    assert page.inner_text("#exDur") == page.evaluate("tl.tc(tl.total())")
    size = page.inner_text("#exSize")
    assert size.startswith("≈ ") and size.endswith("MB"), size
    # the preset's bitrate (measured off this bin's earlier files once there are any)
    # plus its sound, over the sequence's length
    quick = next(p for p in page.evaluate("fetch('/api/export').then(r => r.json())")["presets"]
                 if p["key"] == "quick")
    mb = size_estimate_mb(page)
    secs = page.evaluate("tl.total()")
    assert abs(mb - secs * (quick["video_mbps"] + quick["audio_kbps"] / 1000) / 8) < 0.05, (mb, secs)
    # the frame says the rate: the sequence's
    assert page.inner_text("#exFrame") == "1920×1080 · 24 fps"
    # another preset re-estimates
    page.select_option("[data-testid=export-preset]", "youtube")
    assert page.inner_text("#exSize") != size
    # Escape cancels before the start, and nothing was made
    page.keyboard.press("Escape")
    page.wait_for_function("!document.getElementById('exportDialog').open", timeout=3000)
    assert not (Path(project["work"]) / "renders" / "footage" / "footage - edl.mp4").exists()


def test_the_header_button_and_file_export_open_it(page):
    page.locator("#makeFilm").click()
    wait_settings(page)
    page.click("[data-testid=export-cancel]")            # Cancel before the start closes it
    page.wait_for_function("!document.getElementById('exportDialog').open", timeout=3000)
    page.locator("#menubar .menu[data-menu=file] .mtop").click()
    page.locator("#menubar button[data-cmd=export]").click()
    wait_settings(page)
    assert not page.locator("#menubar .menu.open").count(), "the menu closes behind it"


# ---------------------------------------------------------------- an export

def test_an_export_reaches_100_and_is_the_sequence_at_the_path_it_says(page, project):
    page.keyboard.press("Control+m")
    path = export(page, "journey cut", "lane-tests")
    bar = page.evaluate("window.__bar")
    assert bar and bar[-1] == 100, bar
    assert any(0 < v < 100 for v in bar), f"no progress seen between 0 and 100: {bar}"
    assert bar == sorted(bar), f"the bar went backwards: {bar}"
    # the file is where data-path says, under --work/renders/<folder>/<name>.mp4
    want = Path(project["work"]) / "renders" / "lane-tests" / "journey cut.mp4"
    assert path == str(want) and want.exists() and want.stat().st_size > 0
    # and it is the sequence: constant frame rate at the sequence's rate, and its length
    # within one SEQUENCE frame of tl.total() (the journey's finding: the file's own
    # r_frame_rate was 120/1, so "one frame" of it was no measure at all)
    total = page.evaluate("tl.total()")
    seq_fps = page.evaluate("tl.fps()")
    rates = ffprobe_rates(path)
    assert rates["r_frame_rate"] == rates["avg_frame_rate"] == rational(seq_fps), (rates, seq_fps)
    got = ffprobe_duration(path)
    assert abs(got - total) <= 1.0 / seq_fps + 1e-6, (got, total)
    assert abs(int(rates["nb_frames"]) - total * seq_fps) <= 0.5 + 1e-6, (rates, total)
    # Play plays it, Show file shows the path
    page.click("#exShow")
    assert page.input_value("#exFilePath") == path
    page.click("#exPlay")
    page.wait_for_function(
        "(() => { const v = document.getElementById('exVideo'); return v && v.readyState >= 2; })()",
        timeout=15000)
    # Escape closes it once it is done
    page.keyboard.press("Escape")
    page.wait_for_function("!document.getElementById('exportDialog').open", timeout=3000)


def test_a_name_clash_gets_a_suffix_and_never_overwrites(page, project):
    folder = Path(project["work"]) / "renders" / "clash"
    folder.mkdir(parents=True, exist_ok=True)
    first = folder / "same name.mp4"
    first.write_bytes(b"not to be overwritten")
    page.keyboard.press("Control+m")
    path = export(page, "same name", "clash")
    assert path == str(folder / "same name-2.mp4"), path
    assert first.read_bytes() == b"not to be overwritten"
    page.click("[data-testid=export-cancel]")            # Close, after the end
    page.keyboard.press("Control+m")
    path = export(page, "same name", "clash")
    assert path == str(folder / "same name-3.mp4"), path


def test_a_running_export_can_be_cancelled(page, project, monkeypatch):
    """Cancel Export stops the encoder — its process group is killed — and nothing is
    left at the path. A stand-in for assemble.py that never finishes on its own makes
    the cancel the only way out, so the test is not a race against a fast encode."""
    import server

    def endless(job, cmd):
        proc = subprocess.Popen(["sleep", "120"], start_new_session=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        server.RENDER_PROCS[job] = proc
        try:
            out, err = proc.communicate(timeout=60)
        finally:
            server.RENDER_PROCS.pop(job, None)
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)

    monkeypatch.setattr(server, "_run_render", endless)
    page.keyboard.press("Control+m")
    wait_settings(page)
    page.fill("[data-testid=export-name]", "never")
    page.fill("#exFolder", "cancelled")
    page.click("[data-testid=export-go]")
    page.wait_for_selector("[data-testid=export-progress]", timeout=5000)
    # Escape does not hide or stop a running export
    page.keyboard.press("Escape")
    assert is_open(page)
    t0 = time.time()
    page.click("[data-testid=export-cancel]")
    page.wait_for_selector("[data-testid=export-go]", timeout=10000)
    assert time.time() - t0 < 8
    assert "cancelled" in page.inner_text("#exNote").lower()
    assert not (Path(project["work"]) / "renders" / "cancelled" / "never.mp4").exists()
    jobs = [j for j in server.RENDERS.values() if j.get("path", "").endswith("/never.mp4")]
    assert jobs and jobs[-1]["state"] == "cancelled"


def test_the_name_and_folder_cannot_leave_the_renders_folder(page, project):
    """A name or folder with separators or `..` is one sanitised path component each."""
    import server
    r = page.request.post(page.url.rstrip("/") + "/api/render", data=json.dumps({
        "segments": SEGMENTS[:1], "preset": "quick",
        "name": "../../escape/../me", "folder": "../../../tmp"}),
        headers={"content-type": "application/json"})
    assert r.ok, r.text()
    body = r.json()
    root = (Path(project["work"]) / "renders").resolve()
    assert Path(body["path"]).resolve().parent.parent == root, body["path"]
    assert "/" not in body["name"] and not body["name"].startswith(".")
    # let it finish so no render is left running for the next module
    deadline = time.time() + 120
    while time.time() < deadline and server.RENDERS[body["job"]]["state"] == "running":
        time.sleep(0.2)
    assert server.RENDERS[body["job"]]["state"] == "done"
    bad = page.request.post(page.url.rstrip("/") + "/api/render", data=json.dumps({
        "segments": SEGMENTS[:1], "preset": "nope", "name": "x"}),
        headers={"content-type": "application/json"})
    assert bad.status == 400


# ---------------------------------------------------------------- verdict 3, punch 10

def test_match_sequence_exports_at_the_sources_rate_and_frame(page, project):
    """Match Sequence is the footage's own frame size and frame rate (Quick forced
    1920×1080 at 23.976): its r_frame_rate equals the source clip's, constant."""
    src = ffprobe_rates(str(Path(project["footage"]) / "CLIP_A.MP4"))
    page.keyboard.press("Control+m")
    wait_settings(page)
    page.select_option("[data-testid=export-preset]", "match")
    assert page.inner_text("#exFrame") == f"{src['width']}×{src['height']} · 24 fps"
    path = export(page, "matched", "lane-tests", preset="match")
    got = ffprobe_rates(path)
    assert got["r_frame_rate"] == src["r_frame_rate"] == got["avg_frame_rate"], (got, src)
    assert (got["width"], got["height"]) == (src["width"], src["height"])
    total = page.evaluate("tl.total()")
    assert abs(ffprobe_duration(path) - total) <= 1.0 / 24 + 1e-6


def test_the_size_estimate_is_within_a_quarter_of_the_real_file(page, project):
    """Calibrated from the bin's own files of each preset (verdict 3: 128 MB estimated
    for a 78 MB file): after one export with a preset, the next estimate for the same
    sequence is within ±25 % of what that preset really wrote."""
    page.keyboard.press("Control+m")
    path = export(page, "measure me", "lane-tests", preset="quick")
    real_mb = Path(path).stat().st_size / (1024 * 1024)
    page.click("[data-testid=export-cancel]")            # Close, after the end
    page.wait_for_function("!document.getElementById('exportDialog').open", timeout=3000)
    page.keyboard.press("Control+m")
    wait_settings(page)
    page.select_option("[data-testid=export-preset]", "quick")
    est = size_estimate_mb(page)
    assert abs(est - real_mb) <= 0.25 * real_mb, (est, real_mb)
    assert "measured" in page.locator("#exSize").locator("xpath=..").get_attribute("title")
    # Match Sequence too, from the file the test above made (or this one's own)
    page.select_option("[data-testid=export-preset]", "match")
    matched = Path(project["work"]) / "renders" / "lane-tests" / "matched.mp4"
    if matched.exists():
        est = size_estimate_mb(page)
        real = matched.stat().st_size / (1024 * 1024)
        assert abs(est - real) <= 0.25 * real, (est, real)


def test_the_folder_chooser_offers_recent_folders_newest_first(page, project):
    page.keyboard.press("Control+m")
    wait_settings(page)
    opts = page.eval_on_selector_all("#exRecent option", "os => os.map(o => o.value)")
    assert opts[0] == "" and "lane-tests" in opts, opts
    info = page.evaluate("fetch('/api/export').then(r => r.json())")
    whens = [f["when"] for f in info["recent"]]
    assert whens == sorted(whens, reverse=True), info["recent"]
    page.select_option("#exRecent", "lane-tests")
    assert page.input_value("#exFolder") == "lane-tests"
    renders = str(Path(project["work"]) / "renders")
    assert page.inner_text("#exPath").startswith(f"{renders}/lane-tests/")
