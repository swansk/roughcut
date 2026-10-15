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

import numpy as np
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
    size = page.inner_text("#exSize").splitlines()[0]
    assert size.startswith("≈ ") and size.endswith("MB"), size
    # the preset's bitrate (measured off this bin's earlier files once there are any, or
    # off the 2 s sample the first open encodes) plus its sound, over the sequence's
    # length; read once the sample has landed, so both sides use the same figure
    page.wait_for_function(
        "document.querySelector('#exSize').dataset.source !== 'measuring'", timeout=120000)
    quick = next(p for p in page.evaluate("fetch('/api/export').then(r => r.json())")["presets"]
                 if p["key"] == "quick")
    mb = size_estimate_mb(page)
    secs = page.evaluate("tl.total()")
    assert abs(mb - secs * (quick["video_mbps"] + quick["audio_kbps"] / 1000) / 8) < 0.05, (mb, secs)
    # the frame says the rate: the sequence's
    assert page.inner_text("#exFrame") == "1920×1080 · 24 fps"
    # another preset re-estimates
    page.select_option("[data-testid=export-preset]", "youtube")
    assert page.inner_text("#exSize").splitlines()[0] != size
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


# ---------------------------------------------------------------- verdict 4, punch 6

def test_an_in_out_export_is_written_at_the_rate_the_dialog_says(page, project, monkeypatch):
    """Verdict 4: with an In/Out range the dialog said "29.970 fps" and the file came out
    at 60000/1001 — `sequence_fps` counted only the clips inside the range. Here the
    range holds only shot 3, whose clip (CLIP_C) is made to read 30 fps while the
    sequence's other four shots are 24: the whole sequence is 24 fps, and so is the
    file."""
    import server
    real = server.media_facts

    def facts(src):
        f = real(src)
        return {**f, "fps": 30.0} if f and Path(src).name == "CLIP_C.MP4" else f

    monkeypatch.setattr(server, "media_facts", facts)
    # shot 3 is 8-12 s of the film: In/Out inside it
    page.evaluate("tl.state.seqIn = 8.5; tl.state.seqOut = 11.5;")
    page.keyboard.press("Control+m")
    wait_settings(page)
    assert page.input_value("#exRange") == "inout"
    page.select_option("[data-testid=export-preset]", "quick")
    frame = page.inner_text("#exFrame")
    assert frame.endswith("· 24 fps"), frame
    path = export(page, "in out rate", "lane-tests", preset="quick")
    rates = ffprobe_rates(path)
    assert rates["r_frame_rate"] == rates["avg_frame_rate"] == "24/1", rates
    assert abs(ffprobe_duration(path) - 3.0) <= 1.0 / 24 + 1e-6
    page.evaluate("tl.state.seqIn = null; tl.state.seqOut = null;")


def test_the_first_ever_export_of_a_preset_is_within_a_quarter_of_its_estimate(page, project):
    """Verdict 4: "Estimate 310 MB, file 148 MB (2.1×)" until the bin has one file of the
    preset. Now the dialog's first open encodes a ~2 s sample of the sequence with the
    preset and the estimate is laid from it: the first file is within ±25 %."""
    import server
    key = "youtube"            # no test in this module exports with it before this one
    info = page.evaluate("fetch('/api/export').then(r => r.json())")
    pre = next(p for p in info["presets"] if p["key"] == key)
    assert pre["measured"] == 0, pre
    server.sample_file(key).unlink(missing_ok=True)
    page.keyboard.press("Control+m")
    wait_settings(page)
    page.select_option("[data-testid=export-preset]", key)
    page.wait_for_function(
        "document.querySelector('#exSize') && document.querySelector('#exSize').dataset.source === 'sample'",
        timeout=120000)
    assert "sample" in page.locator("#exSize").locator("xpath=..").get_attribute("title")
    est = size_estimate_mb(page)
    sample = json.loads(server.sample_file(key).read_text(encoding="utf-8"))
    assert sample["seconds"] <= 2.0 + 1e-6 and sample["slices"] >= 2, sample
    path = export(page, "first youtube", "lane-tests", preset=key)
    real = Path(path).stat().st_size / 1e6           # the dialog's MB are 10^6 bytes
    print(f"first {key} export: estimate {est:.3f} MB, file {real:.3f} MB "
          f"({100 * (est - real) / real:+.1f} %)")
    assert abs(est - real) <= 0.25 * real, (est, real)


def test_parts_carry_pcm_and_the_film_one_aac_stream(project, tmp_path):
    """The sound is AAC-encoded once (verdict 4, punch 6): a part carries PCM, the joined
    film one AAC stream — with and without a music bed under it."""
    tool = Path(__file__).resolve().parents[2] / "research" / "tools" / "assemble.py"

    def streams(path: Path) -> list[tuple[str, str]]:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=codec_type,codec_name", "-of", "json", str(path)],
                           capture_output=True, text=True, check=True)
        return [(s["codec_type"], s["codec_name"]) for s in json.loads(r.stdout)["streams"]]

    for bed in (None, str(Path(project["assets"]) / "music" / "bed.wav")):
        edl = {"variant": "T", "title": "pcm", "orient": "none", "segments": SEGMENTS[:2]}
        if bed:
            edl["effects_music"] = {"asset": bed, "gain_db": -18}
        src = tmp_path / f"edl{bool(bed)}.json"
        src.write_text(json.dumps(edl), encoding="utf-8")
        parts, out = tmp_path / f"parts{bool(bed)}", tmp_path / f"film{bool(bed)}.mp4"
        subprocess.run(["uv", "run", "--quiet", str(tool), str(src), "--footage",
                        str(project["footage"]), "--parts-dir", str(parts), "--keep-parts",
                        "-o", str(out)], check=True, capture_output=True, text=True)
        for part in sorted(parts.glob("part_*.mp4")):
            assert ("audio", "pcm_s16le") in streams(part), (part, streams(part))
        got = streams(out)
        assert [s for s in got if s[0] == "audio"] == [("audio", "aac")], got


# ---------------------------------------------------------------- verdict 5, punch 6

def test_export_is_not_clickable_while_the_size_is_measuring(page, project, monkeypatch):
    """Verdict 5: the dialog let Export go while the size read "measuring…" (≈ 773 MB
    shown, 306 MB written). Now Export waits for the sample: held here until the test
    lets the sample run, a click does nothing; once it lands the estimate is the
    sample's, Export is enabled, and the file is within ±25 % of what it said."""
    import server
    key = "master"             # no test in this module exports with it
    server.sample_file(key).unlink(missing_ok=True)
    gate = threading.Event()
    real = server._sample_job

    def held(preset, k, scope):
        gate.wait(60)
        real(preset, k, scope)

    monkeypatch.setattr(server, "_sample_job", held)
    page.keyboard.press("Control+m")
    wait_settings(page)
    page.select_option("[data-testid=export-preset]", key)
    page.wait_for_function(
        "document.querySelector('#exSize').dataset.source === 'measuring'", timeout=10000)
    assert "measuring" in page.inner_text("#exSize")
    go = page.locator("[data-testid=export-go]")
    assert go.is_disabled()
    renders = len(server.RENDERS)
    go.click(force=True)                                  # a click lands on a disabled button
    page.keyboard.press("Enter")                          # and Enter in the name field
    page.wait_for_timeout(600)
    assert len(server.RENDERS) == renders, "an export started while the size was a guess"
    assert page.evaluate("exportDialog.phase") == "settings"
    gate.set()
    page.wait_for_function(
        "document.querySelector('#exSize').dataset.source === 'sample'", timeout=120000)
    assert go.is_enabled()
    est = size_estimate_mb(page)
    path = export(page, "master after measuring", "lane-tests", preset=key)
    real_mb = Path(path).stat().st_size / 1e6
    print(f"first {key} export: estimate {est:.3f} MB, file {real_mb:.3f} MB "
          f"({100 * (est - real_mb) / real_mb:+.1f} %)")
    assert abs(est - real_mb) <= 0.25 * real_mb, (est, real_mb)


def _assemble(edl: dict, project, root: Path, name: str) -> tuple[Path, float, set[str], str]:
    """Render `edl` the board's way (the preview profile, 1920×1080) and watch the output's
    folder while it runs: (the film, wall seconds, every other file that appeared, log)."""
    tool = Path(__file__).resolve().parents[2] / "research" / "tools" / "assemble.py"
    out_dir = root / name
    out_dir.mkdir()
    src = root / f"{name}.json"
    src.write_text(json.dumps(edl), encoding="utf-8")
    out = out_dir / "film.mp4"
    seen: set[str] = set()
    t0 = time.perf_counter()
    proc = subprocess.Popen(["uv", "run", "--quiet", str(tool), str(src), "--footage",
                             str(project["footage"]), "--parts-dir", str(root / f"{name}-parts"),
                             "-o", str(out)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)
    while proc.poll() is None:
        seen.update(p.name for p in out_dir.iterdir())
        time.sleep(0.01)
    wall = time.perf_counter() - t0
    log, err = proc.communicate()
    assert proc.returncode == 0, log + err
    return out, wall, seen - {"film.mp4"}, log


def test_a_v3_overlay_composites_inside_the_parts_with_no_second_film_pass(project, tmp_path):
    """Verdict 5: a 1.2 s V3 clip cost a whole-film re-encode (`.tracks.mp4`), 24 min for
    4:37. Now the clip is composited inside the part it covers: no `.tracks.mp4` (no
    second file at all) appears beside the film, and the render takes <= 1.3x the same
    sequence without it."""
    base = {"variant": "T", "title": "v3", "orient": "none", "segments": SEGMENTS}
    with_v3 = {**base, "tracks": {"V3": {}},
               "overlays": [{"id": "o1", "clip": "CLIP_C.MP4", "in": 1.0, "out": 2.0, "at": 5.5,
                             "track": "V3"}]}
    times: dict[str, list[float]] = {"base": [], "v3": []}
    for run in range(2):                                  # alternate, keep the best of two
        for name, edl in (("base", base), ("v3", with_v3)):
            film, wall, extra, log = _assemble(edl, project, tmp_path, f"{name}{run}")
            times[name].append(wall)
            if name == "v3":
                assert not extra, f"a second pass wrote {extra}"
                assert "V3 00 CLIP_C.MP4" in log and "inside part 1" in log, log
                v3_film = film
            else:
                base_film = film
    ratio = min(times["v3"]) / min(times["base"])
    print(f"render wall: base {times['base']}, with V3 {times['v3']}, ratio {ratio:.2f}")
    assert ratio <= 1.3, times

    def gray(path: Path, t: float) -> np.ndarray:
        r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-ss", f"{t:.3f}",
                            "-frames:v", "1", "-vf", "scale=64:36", "-f", "rawvideo",
                            "-pix_fmt", "gray", "-"], capture_output=True, check=True)
        return np.frombuffer(r.stdout, np.uint8).astype(float)

    # CLIP_C's 1.0-2.0 over 5.5-6.5 s of the film (inside shot 2, CLIP_B 1.5-2.5 there)
    clip_c = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-ss", "1.0", "-i",
                    str(Path(project["footage"]) / "CLIP_C.MP4"), "-t", "1.0", "-an", str(clip_c)],
                   check=True, capture_output=True)
    inside = gray(v3_film, 6.0)
    assert np.abs(inside - gray(clip_c, 0.5)).mean() < 8
    # CLIP_B under it is the same testsrc2 half a second away, so V1 differs from C by
    # ~10.7 here; the Quick render's own error against C is ~5.2 (x264) / ~5.4 (NVENC +
    # GPU scale, wave 8 RENDER3), which sat right on a 2x margin: 1.5x still tells them apart
    assert np.abs(inside - gray(base_film, 6.0)).mean() > 1.5 * np.abs(inside - gray(clip_c, 0.5)).mean()
    for t in (5.2, 6.8, 12.0):                             # V1 untouched outside its range
        assert np.abs(gray(v3_film, t) - gray(base_film, t)).mean() < 3, t
    # the film is still CFR at the sequence's rate and the sequence's length
    rates = ffprobe_rates(str(v3_film))
    assert rates["r_frame_rate"] == rates["avg_frame_rate"] == "24000/1001", rates
    assert abs(ffprobe_duration(str(v3_film)) - ffprobe_duration(str(base_film))) < 1e-3


def test_v1_hidden_is_black_in_every_part_under_v2(project, tmp_path):
    """V1's eye off is drawn inside the parts too: black under V2, V2's picture over it,
    no second pass."""
    edl = {"variant": "T", "title": "eye", "orient": "none", "segments": SEGMENTS[:2],
           "tracks": {"V1": {"eye": False}},
           "overlays": [{"id": "o1", "clip": "CLIP_C.MP4", "in": 1.0, "out": 3.0, "at": 3.0}]}
    film, _, extra, log = _assemble(edl, project, tmp_path, "eye")
    assert not extra, extra
    assert "V1 hidden" in log and "inside parts 0, 1" in log, log

    def luma(t: float) -> float:
        r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(film), "-ss", f"{t:.3f}",
                            "-frames:v", "1", "-vf", "scale=64:36", "-f", "rawvideo",
                            "-pix_fmt", "gray", "-"], capture_output=True, check=True)
        return float(np.frombuffer(r.stdout, np.uint8).mean())

    assert luma(1.0) < 20 and luma(7.0) < 20               # V1 hidden: black
    assert luma(3.5) > 60 and luma(4.5) > 60               # V2 over both parts it spans
