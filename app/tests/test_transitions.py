"""Premiere's default transitions at an edit (verdict 6, punch 6).

Ctrl+D lays a 1 s Cross Dissolve centred on the selected edit, Shift+D a Constant Power
audio crossfade, Ctrl+Shift+D both, each one undo. The EDL keeps `transitions` (`{a, b,
kind, dur, align}`, validated by the server); the timeline draws the box on the cut and
its edges trim its duration. With solid-colour clips (CLIP_A red with a 440 Hz tone,
CLIP_B blue with 880 Hz, CLIP_C green and silent), the picture on the edit's frame is
the mean of the two shots (±10 %) in the export and in the Program monitor; the export
is as long as the cut without the transition; the crossfade's levels follow cos / sin
(volumedetect windows); a shot without enough media repeats its first or last frame.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
TOOLS = Path(__file__).resolve().parents[2] / "research" / "tools"
sys.path.insert(0, str(TOOLS))

from roughcut import edits, tracks as tracksmod  # noqa: E402

W, H, FPS, CLIP_S = 320, 180, 24, 6.0
CUT = 2.0                                     # film second of the edit

# film: CLIP_A 1-3 (0-2 s), CLIP_B 2-4 (2-4 s): 3 s of A's media past its out, 2 s of
# B's before its in — handles for a 1 s dissolve (0.5 s each side)
SEGS = [{"id": "s1", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "red"},
        {"id": "s2", "clip": "CLIP_B.MP4", "in": 2.0, "out": 4.0, "why": "blue"}]
EDL = {"variant": "T", "title": "transitions", "orient": "none", "story": "",
       "target_s": [2, 20], "segments": SEGS}
DISSOLVE = {"a": "s1", "b": "s2", "kind": "cross_dissolve", "dur": 1.0, "align": "center"}
XFADE = {"a": "s1", "b": "s2", "kind": "audio_crossfade", "dur": 1.0, "align": "center"}


def _clip(path: Path, colour: str, hz: int | None) -> None:
    audio = (["-f", "lavfi", "-i", f"sine=frequency={hz}:duration={CLIP_S}"] if hz else
             ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={CLIP_S}"])
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i", f"color=c={colour}:size={W}x{H}:rate={FPS}:duration={CLIP_S}",
         *audio, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-g", "12", "-c:a", "aac", "-t", str(CLIP_S), str(path)],
        check=True, capture_output=True)


def _sidecar(stem: str) -> dict:
    return {
        "clip": f"{stem}.MP4", "duration_s": CLIP_S, "sample_rate": 16000, "frame_hz": 10,
        "tracks": {"speech_band_db": [-30.0] * 60, "flatness": [0.05] * 60,
                   "onset": [0.2] * 60, "vad": [0] * 60},
        "transcript": [], "events": [],
        "summary": {"speech_fraction": 0.0, "wind_dominant_fraction": 0.0,
                    "integrated_lufs": -18.0, "peak_dbfs": -3.0, "audio_usable": True,
                    "n_words": 0, "analysis_s": 0.1},
        "candidates": [],
    }


@pytest.fixture(scope="module")
def proj(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("trans")
    footage, sidecars, work, assets = (root / d for d in ("footage", "sidecars", "work", "assets"))
    for d in (footage, sidecars, work, assets / "music"):
        d.mkdir(parents=True)
    for stem, colour, hz in (("CLIP_A", "red", 440), ("CLIP_B", "blue", 880),
                             ("CLIP_C", "green", None)):
        _clip(footage / f"{stem}.MP4", colour, hz)
        (sidecars / f"{stem}.audio.json").write_text(json.dumps(_sidecar(stem)), encoding="utf-8")
    edl = root / "edl.json"
    edl.write_text(json.dumps(EDL, indent=1), encoding="utf-8")
    return {"root": root, "footage": footage, "sidecars": sidecars, "work": work,
            "assets": assets, "edl": edl}


# ------------------------------------------------------------------ the export

def render(proj: dict, edl: dict, name: str, profile: str = "match"):
    path = proj["root"] / f"{name}.json"
    path.write_text(json.dumps(edl), encoding="utf-8")
    out = proj["root"] / f"{name}.mp4"
    r = subprocess.run(["uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(path),
                        "--footage", str(proj["footage"]), "--profile", profile,
                        "--fps", str(FPS), "-o", str(out)],
                       capture_output=True, text=True, env={**os.environ, "ROUGHCUT_GPU": "0"})
    assert r.returncode == 0, r.stdout + r.stderr
    return out, r.stdout


def rgb(path: Path, t: float) -> np.ndarray:
    """The mean colour of the middle of the film's frame under second t (by number: a
    seek lands on the frame after)."""
    n = int(t * FPS + 1e-6)
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
                        "-vf", f"select=eq(n\\,{n})", "-frames:v", "1", "-vsync", "0",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       capture_output=True, check=True)
    a = np.frombuffer(r.stdout, np.uint8).astype(np.float64).reshape(H, W, 3)
    return a[H // 2 - 8:H // 2 + 8, W // 2 - 8:W // 2 + 8].reshape(-1, 3).mean(axis=0)


def within(got, want, pct: float = 10.0) -> bool:
    return bool(np.all(np.abs(np.asarray(got) - np.asarray(want)) <= 255 * pct / 100))


def frames_of(path: Path) -> int:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                        "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True)
    return int(r.stdout.strip())


def duration(path: Path, stream: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", stream,
                        "-show_entries", "stream=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def mean_db(path: Path, a: float, b: float) -> float:
    r = subprocess.run(["ffmpeg", "-v", "info", "-nostdin", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}",
                        "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"mean_volume: (-?[\d.]+|-inf) dB", r.stderr)
    assert m, r.stderr[-400:]
    return -120.0 if m.group(1) == "-inf" else float(m.group(1))


def mid(t: float) -> float:
    """The middle of the frame that starts at film second t (24 fps)."""
    return t + 0.5 / FPS


@pytest.fixture(scope="module")
def films(proj) -> dict:
    plain, _ = render(proj, EDL, "plain")
    dis, log = render(proj, {**EDL, "transitions": [DISSOLVE]}, "dissolve")
    return {"plain": plain, "dissolve": dis, "log": log}


def test_the_exported_dissolve_is_the_mean_of_both_shots_on_the_edit(films):
    red = rgb(films["plain"], mid(1.0))
    blue = rgb(films["plain"], mid(3.0))
    assert red[0] > 150 and blue[2] > 150, (red, blue)
    got = rgb(films["dissolve"], mid(CUT))
    assert within(got, 0.5 * (red + blue)), (got, red, blue)
    # a quarter in and three quarters in: 25 % / 75 % blue; outside the window, as cut
    assert within(rgb(films["dissolve"], mid(1.75)), 0.75 * red + 0.25 * blue), rgb(films["dissolve"], mid(1.75))
    assert within(rgb(films["dissolve"], mid(2.25)), 0.25 * red + 0.75 * blue)
    assert within(rgb(films["dissolve"], mid(1.3)), red, 3)
    assert within(rgb(films["dissolve"], mid(2.7)), blue, 3)
    assert "Cross Dissolve 1.00s at 2.00s" in films["log"], films["log"]


def test_the_export_is_as_long_as_the_cut(films):
    assert frames_of(films["dissolve"]) == frames_of(films["plain"]) == 4 * FPS
    assert abs(duration(films["dissolve"], "a:0") - duration(films["plain"], "a:0")) < 0.03


def test_the_audio_crossfade_is_constant_power(proj):
    # A (440 Hz) into C (silent): a hard cut is silent from 2.0 s; the crossfade lets A
    # out at cos(πx/2) — -3 dB on the edit, about -8 dB 0.25 s after it, gone by 2.5 s
    segs = [SEGS[0], {"id": "s3", "clip": "CLIP_C.MP4", "in": 2.0, "out": 4.0}]
    cut, _ = render(proj, {**EDL, "segments": segs}, "hardcut")
    xf, log = render(proj, {**EDL, "segments": segs,
                            "transitions": [{**XFADE, "b": "s3"}]}, "xfade")
    full = mean_db(cut, 0.6, 1.4)
    assert mean_db(cut, 2.1, 2.4) < -60
    assert abs(mean_db(xf, 0.6, 1.4) - full) < 0.5
    assert abs(mean_db(xf, 1.98, 2.02) - (full - 3.0)) < 1.0, (mean_db(xf, 1.98, 2.02), full)
    assert abs(mean_db(xf, 2.23, 2.27) - (full + 20 * np.log10(np.cos(np.pi / 2 * 0.75)))) < 1.0
    assert mean_db(xf, 2.6, 3.0) < -60
    # the picture is a cut: the dissolve is the video transition's, not this one's
    assert within(rgb(xf, mid(2.1)), rgb(cut, mid(2.1)), 3)
    assert "Constant Power 1.00s at 2.00s" in log, log
    assert abs(duration(xf, "a:0") - duration(cut, "a:0")) < 0.03


def test_insufficient_media_repeats_frames(proj):
    # B from 0.2: only 4 frames of media before its in — the 1 s dissolve keeps its length
    # and B's first frame is repeated for the other 8 (Premiere's "insufficient media")
    segs = [SEGS[0], {**SEGS[1], "in": 0.2, "out": 2.2}]
    out, log = render(proj, {**EDL, "segments": segs, "transitions": [DISSOLVE]}, "short")
    assert "Cross Dissolve 1.00s" in log and "insufficient media: 8 repeated frames" in log, log
    assert frames_of(out) == 4 * FPS
    red, blue = rgb(out, mid(1.0)), rgb(out, mid(3.0))
    assert within(rgb(out, mid(CUT)), 0.5 * (red + blue))
    x = (38 - 36) / 24                                   # frame 38 of the window 36-59
    assert within(rgb(out, mid(38 / FPS)), (1 - x) * red + x * blue), rgb(out, mid(38 / FPS))


def test_the_halves_model():
    clip_len = {"CLIP_A.MP4": 6.0, "CLIP_B.MP4": 6.0}.get
    h = tracksmod.transition_halves(SEGS, [DISSOLVE, XFADE], edits.dur, clip_len)
    assert h[0]["video"] == 0.5 and h[0]["audio"] == 0.5 and not h[0]["short"]
    h = tracksmod.transition_halves([SEGS[0], {**SEGS[1], "in": 0.0}], [DISSOLVE],
                                    edits.dur, clip_len)
    # no media before B's in: the half stays, its frames are B's first frame repeated
    assert h[0]["video"] == 0.5 and h[0]["short"] == [] and h[0]["repeat"] == {
        "cross_dissolve": {"a": 0.0, "b": 0.5}}
    # validation: a pair no longer adjacent goes; tmp ids are renamed; bad kinds are errors
    segs3 = [*SEGS, {"id": "s9", "clip": "CLIP_A.MP4", "in": 0, "out": 1}]
    got = tracksmod.validate_transitions(
        [DISSOLVE, {"a": "s1", "b": "s9", "kind": "cross_dissolve", "dur": 1},
         {"a": "s2", "b": "tmp-4", "kind": "audio_crossfade", "dur": 0.5}],
        segs3, {"tmp-4": "s9"}, edits.dur)
    assert [(t["a"], t["b"], t["kind"]) for t in got] == [
        ("s1", "s2", "cross_dissolve"), ("s2", "s9", "audio_crossfade")]
    for bad in ({**DISSOLVE, "kind": "wipe"}, {**DISSOLVE, "dur": 0},
                {**DISSOLVE, "align": "start"}, {**DISSOLVE, "dur": "x"},
                {**DISSOLVE, "a": None, "b": None}, {**DISSOLVE, "a": None, "align": "end"}):
        with pytest.raises(ValueError):
            tracksmod.validate_transitions([bad], SEGS)


# ------------------------------------------------------------------ the server

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(proj):
    import uvicorn
    import server

    server.configure(proj["edl"], proj["footage"], proj["sidecars"], proj["work"],
                     proxies=False, visual=proj["work"] / "no-visual", assets=proj["assets"])
    server.ensure_proxies(["CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"])
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


def on_disk(proj) -> dict:
    return json.loads(proj["edl"].read_text(encoding="utf-8"))


def test_the_server_validates_and_keeps_transitions(live_server, proj):
    import httpx
    proj["edl"].write_text(json.dumps(EDL, indent=1), encoding="utf-8")
    body = {"segments": SEGS, "transitions": [DISSOLVE]}
    r = httpx.put(f"{live_server}/api/project", json=body)
    assert r.status_code == 200, r.text
    assert on_disk(proj)["transitions"] == [DISSOLVE]
    assert httpx.get(f"{live_server}/api/project").json()["transitions"] == [DISSOLVE]
    # a shot laid in a moment ago (tmp-N): the transition is renamed with the shot
    segs = [SEGS[0], {**SEGS[1], "id": "tmp-7"}]
    r = httpx.put(f"{live_server}/api/project",
                  json={"segments": segs, "transitions": [{**XFADE, "b": "tmp-7"}]})
    minted = r.json()["ids"]["tmp-7"]
    assert on_disk(proj)["transitions"] == [{**XFADE, "b": minted}]
    # a save that reorders the two drops it; bad ones are a 400 and write nothing
    r = httpx.put(f"{live_server}/api/project",
                  json={"segments": [{**SEGS[1], "id": minted}, SEGS[0]]})
    assert r.status_code == 200 and "transitions" not in on_disk(proj)
    for bad in ({**DISSOLVE, "kind": "wipe"}, {**DISSOLVE, "dur": 99}, {**DISSOLVE, "align": "end"}):
        r = httpx.put(f"{live_server}/api/project", json={"segments": SEGS, "transitions": [bad]})
        assert r.status_code == 400, (bad, r.text)
    proj["edl"].write_text(json.dumps(EDL, indent=1), encoding="utf-8")


# ------------------------------------------------------------------ the board

@pytest.fixture()
def board(live_server, proj):
    playwright_api = pytest.importorskip("playwright.sync_api")
    proj["edl"].write_text(json.dumps(EDL, indent=1), encoding="utf-8")
    with playwright_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(live_server)
        pg.wait_for_selector("#tl .blk")
        pg.wait_for_function("segs.length === 2 && !!window.tl.applyTransition")
        yield pg
        assert not errors, errors
        browser.close()


def saved(pg, proj, want, timeout=8.0) -> dict:
    """The EDL on disk once the autosave has landed with `want(edl)` true."""
    pg.evaluate("flushSave()")
    t0 = time.time()
    while time.time() - t0 < timeout:
        edl = on_disk(proj)
        if want(edl):
            return edl
        time.sleep(0.2)
        pg.evaluate("flushSave()")
    raise AssertionError(on_disk(proj).get("transitions"))


def test_ctrl_d_on_a_cut_makes_a_1s_dissolve_and_undo_removes_it(board, proj):
    pg = board
    # the edit point selected as Premiere's selection tool does: a click on its column
    col = pg.locator("#tl .tl-lane .tl-edge").first
    box = col.bounding_box()
    pg.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] * 0.25)
    assert pg.evaluate("tl.editForTransition()") == 0
    pg.keyboard.press("Control+d")
    tr = pg.evaluate("tl.transitions()")
    assert tr == [{"a": "s1", "b": "s2", "kind": "cross_dissolve", "dur": 1, "align": "center"}], tr
    edl = saved(pg, proj, lambda e: e.get("transitions"))
    assert edl["transitions"] == [DISSOLVE]
    # drawn on the cut, as wide as 1 s, labelled
    b = pg.locator("#tl .tl-trans").bounding_box()
    zoom = pg.evaluate("tl.state.zoom")
    assert abs(b["width"] - zoom) < 2, (b, zoom)
    cut_x = pg.evaluate("tl.timeToX(2) - tl.el.view.scrollLeft + tl.el.view.getBoundingClientRect().left")
    assert abs(b["x"] + b["width"] / 2 - cut_x) < 2
    assert pg.locator("#tl .tl-trans .lbl").inner_text() == "Cross Dissolve"
    assert pg.evaluate("document.querySelector('#undo').title").startswith("undo: Cross Dissolve")
    pg.keyboard.press("Control+z")
    assert pg.evaluate("tl.transitions()") == []
    assert pg.locator("#tl .tl-trans").count() == 0
    saved(pg, proj, lambda e: "transitions" not in e)
    # the key map lists them
    keys = pg.evaluate("tlKeys.KEYS.map(k => k[0]).join(' | ')")
    assert "Ctrl+D Shift+D Ctrl+Shift+D" in keys, keys


def test_shift_d_crossfades_ctrl_shift_d_both_one_undo(board, proj):
    pg = board
    pg.evaluate("tl.seek(1.9)")              # nothing selected: the edit nearest the playhead
    pg.keyboard.press("Shift+d")
    assert [t["kind"] for t in pg.evaluate("tl.transitions()")] == ["audio_crossfade"]
    assert pg.locator("#tl .tl-trans.audio").count() == 1
    pg.keyboard.press("Control+z")
    pg.keyboard.press("Control+Shift+d")
    assert sorted(t["kind"] for t in pg.evaluate("tl.transitions()")) == ["audio_crossfade", "cross_dissolve"]
    edl = saved(pg, proj, lambda e: len(e.get("transitions") or []) == 2)
    assert {t["kind"] for t in edl["transitions"]} == {"audio_crossfade", "cross_dissolve"}
    pg.keyboard.press("Control+z")
    assert pg.evaluate("tl.transitions()") == []
    # Delete removes a selected transition (and not the shots)
    pg.keyboard.press("Control+d")
    tb = pg.locator("#tl .tl-trans").bounding_box()       # the body, off the edit's column
    pg.mouse.click(tb["x"] + tb["width"] * 0.25, tb["y"] + tb["height"] / 2)
    assert pg.evaluate("tl.tsel") == "s1|s2|cross_dissolve"
    pg.keyboard.press("Delete")
    assert pg.evaluate("tl.transitions()") == [] and pg.evaluate("segs.length") == 2


def test_dragging_an_end_changes_its_duration_and_double_click_types_it(board, proj):
    pg = board
    pg.evaluate("tl.selectEdit(0)")
    pg.keyboard.press("Control+d")
    b = pg.locator("#tl .tl-trans").bounding_box()
    zoom = pg.evaluate("tl.state.zoom")
    y = b["y"] + b["height"] / 2
    pg.mouse.move(b["x"] + b["width"] - 2, y)
    pg.mouse.down()
    pg.mouse.move(b["x"] + b["width"] - 2 + 0.25 * zoom, y, steps=5)
    pg.mouse.up()
    d = pg.evaluate("tl.transitions()[0].dur")
    assert abs(d - 1.5) < 2 / FPS + 1e-6, d                 # both ends move: centred
    assert pg.evaluate("document.querySelector('#undo').title").startswith("undo: transition duration")
    edl = saved(pg, proj, lambda e: (e.get("transitions") or [{}])[0].get("dur", 0) > 1.2)
    assert abs(edl["transitions"][0]["dur"] - d) < 1e-6
    pg.keyboard.press("Control+z")
    assert pg.evaluate("tl.transitions()[0].dur") == 1
    # double-click: type it (frames with f)
    tb = pg.locator("#tl .tl-trans").bounding_box()
    pg.mouse.dblclick(tb["x"] + tb["width"] * 0.25, tb["y"] + tb["height"] / 2)
    inp = pg.locator("#tl .tl-trans input")
    inp.fill("12f")
    inp.press("Enter")
    assert abs(pg.evaluate("tl.transitions()[0].dur") - 0.5) < 1e-6


def test_insufficient_media_repeats_frames_and_says_so(board, proj):
    pg = board
    # B from 0.2 s: 4 frames of media before its in at 24 fps — the 1 s dissolve is laid
    # whole, B's first frame repeated for the rest (Premiere), and the toast says so
    pg.evaluate("tl.setRange('s2', 0.2, 2.2)")
    pg.evaluate("tl.selectEdit(0)")
    pg.keyboard.press("Control+d")
    assert pg.evaluate("tl.transitions()[0].dur") == 1
    assert "insufficient media — repeated frames" in pg.locator("#toast, .toast").first.inner_text()
    assert pg.locator("#tl .tl-trans.rep").count() == 1
    pg.keyboard.press("Control+z")
    # B from 0: no media at all — still laid, hatched, its half whole
    pg.evaluate("tl.setRange('s2', 0, 2)")
    pg.keyboard.press("Control+d")
    assert [t["dur"] for t in pg.evaluate("tl.transitions()")] == [1]
    assert pg.locator("#tl .tl-trans.rep").count() == 1
    assert pg.evaluate("tl.transitionHalves()[0].rep") == ["cross_dissolve"]
    pg.keyboard.press("Control+z")
    # a trim that eats a handle later: the box is hatched, the length kept
    pg.evaluate("tl.setRange('s2', 2, 4)")
    pg.keyboard.press("Control+d")
    assert pg.locator("#tl .tl-trans.rep").count() == 0
    pg.evaluate("tl.setRange('s2', 0.25, 2.25)")
    assert pg.locator("#tl .tl-trans.rep").count() == 1
    assert abs(pg.evaluate("tl.transitionHalves()[0].video") - 0.5) < 1e-6


def program_rgb(pg) -> np.ndarray:
    png = pg.locator("#player .screen").screenshot()
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-f", "png_pipe", "-i", "-",
                        "-vf", f"scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       input=png, capture_output=True)
    assert r.returncode == 0, r.stderr[-300:]
    a = np.frombuffer(r.stdout, np.uint8).astype(np.float64).reshape(H, W, 3)
    return a[H // 2 - 6:H // 2 + 6, W // 2 - 6:W // 2 + 6].reshape(-1, 3).mean(axis=0)


SETTLED = """(() => { const tx = prog.tx; if (!tx) return false;
    const v = [tx.in, tx.out].find(e => Number(e.dataset.w) > 0);
    const lv = liveVideo();
    return !!v && v.readyState >= 2 && !v.seeking && lv.readyState >= 2 && !lv.seeking; })()"""


def test_the_program_shows_the_mean_of_both_shots_on_the_edit(board, proj):
    """Parked on the edit's frame the Program is half A, half B (±10 %); a quarter into the
    window it is 25 % B. Playing through the window the follower plays (not frozen)."""
    pg = board
    pg.evaluate("tl.seek(1.0)")
    pg.wait_for_timeout(700)
    red = program_rgb(pg)
    pg.evaluate("tl.seek(3.0)")
    pg.wait_for_timeout(700)
    blue = program_rgb(pg)
    assert red[0] > 150 and blue[2] > 150, (red, blue)
    pg.evaluate("tl.selectEdit(0)")
    pg.keyboard.press("Control+d")
    for t, wb in ((CUT, 0.5), (CUT - 0.25, 0.25), (CUT + 0.25, 0.75)):
        pg.evaluate(f"tl.seek({t})")
        pg.wait_for_function(SETTLED)
        pg.wait_for_timeout(500)
        got = program_rgb(pg)
        want = (1 - wb) * red + wb * blue
        assert within(got, want), (t, got, want)
    mode = pg.evaluate("({w: [prog.tx.in.dataset.w, prog.tx.out.dataset.w], mix: grade.mix})")
    assert mode["w"] == ["0", "0.25"] and mode["mix"]["id"] == "s1", mode    # A going out
    # outside the window: the follower is gone, the shot alone
    pg.evaluate("tl.seek(2.8)")
    pg.wait_for_timeout(600)
    assert within(program_rgb(pg), blue, 6)
    assert pg.evaluate("prog.tx.out.style.visibility") == "hidden"
    # played through: the incoming follower runs inside the window
    pg.evaluate("tl.seek(1.2)")
    pg.wait_for_timeout(400)
    pg.evaluate("playFrom(0)")
    pg.wait_for_function("tl.state.playhead > 1.7 && tl.state.playhead < 1.95", timeout=8000)
    run = pg.evaluate("({p: prog.tx.in.paused, t: prog.tx.in.currentTime, w: Number(prog.tx.in.dataset.w)})")
    assert not run["p"] and 1.5 < run["t"] < 2.0 and 0 < run["w"] < 0.5, run
    pg.evaluate("pauseCut()")


GREY = [SEGS[0], {**SEGS[1], "controls": {"saturation": 0}}]     # B graded: Lumetri sat 0


def test_a_graded_shot_dissolves_graded_in_the_export(proj):
    plain, _ = render(proj, {**EDL, "segments": GREY}, "grey-plain")
    dis, _ = render(proj, {**EDL, "segments": GREY, "transitions": [DISSOLVE]}, "grey-dissolve")
    red, grey = rgb(plain, mid(1.0)), rgb(plain, mid(3.0))
    assert abs(grey[0] - grey[2]) < 12 and grey[2] < 150, grey      # blue, desaturated
    assert within(rgb(dis, mid(CUT)), 0.5 * (red + grey)), (rgb(dis, mid(CUT)), red, grey)
    # the incoming handle takes its grade too: a quarter in, 25 % of the GREY
    assert within(rgb(dis, mid(1.75)), 0.75 * red + 0.25 * grey)


def test_the_program_dissolves_the_graded_picture(live_server, proj):
    """B graded (saturation 0): the grade canvas is on, so the dissolve is the grade
    worker's — the other shot's frame through its own grade, blended at x — and it
    matches the mean of the two GRADED pictures, on both sides of the edit."""
    playwright_api = pytest.importorskip("playwright.sync_api")
    proj["edl"].write_text(json.dumps({**EDL, "segments": GREY}, indent=1), encoding="utf-8")
    with playwright_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(live_server)
        pg.wait_for_selector("#tl .blk")
        pg.wait_for_function("segs.length === 2 && !!window.tl.applyTransition")
        pg.evaluate("tl.seek(1.0)")
        pg.wait_for_timeout(700)
        red = program_rgb(pg)
        pg.evaluate("tl.seek(3.0)")
        pg.wait_for_function("document.querySelector('#gradeCanvas').classList.contains('on')")
        pg.wait_for_timeout(700)
        grey = program_rgb(pg)
        assert abs(grey[0] - grey[2]) < 12 and grey[2] < 150, grey
        pg.evaluate("tl.selectEdit(0)")
        pg.keyboard.press("Control+d")
        for t, wb in ((CUT, 0.5), (CUT - 0.25, 0.25), (CUT + 0.25, 0.75)):
            pg.evaluate(f"tl.seek({t})")
            pg.wait_for_function(SETTLED)
            pg.wait_for_timeout(600)
            got = program_rgb(pg)
            st = pg.evaluate("""({mix: grade.mix, on: document.querySelector('#gradeCanvas').classList.contains('on'),
                vis: [prog.tx.in.style.visibility, prog.tx.out.style.visibility],
                op: [prog.tx.in.style.opacity, prog.tx.out.style.opacity], probe: grade.probe(),
                shot: grade.shot, lut: grade.lut && grade.lut.identity})""")
            assert within(got, (1 - wb) * red + wb * grey), (t, got, red, grey, st)
            # drawn by the grade (the follower's own layer hidden), never raw over it
            assert st["on"] and st["mix"]["graded"] == (wb < 0.5) and st["vis"] == ["hidden", "hidden"], (t, st)
        assert not errors, errors
        browser.close()
    proj["edl"].write_text(json.dumps(EDL, indent=1), encoding="utf-8")
