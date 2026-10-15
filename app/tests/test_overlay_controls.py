"""V2+ clips honour their own Effect Controls (verdict 6, punch 8), composited OVER V1.

CLIP_A is solid red on V1, CLIP_B solid blue laid on V2. With the overlay at Opacity 50 %
and Scale 50 %, the middle of the frame must be half blue and half red — a mix, never
blue blended toward black — and outside the half-size box it must be V1's red, in the
export (assemble.py, as the board renders) and in the Program monitor (#pvV2's CSS over
the V1 layers, read off a real screenshot). Both within ±10 % of the expected mix. Keys
on an overlay animate it from its own first frame; the server keeps an overlay's
controls by V1's rule; the GPU path (decode, scale, NVENC) writes what the CPU path does.
"""

from __future__ import annotations

import json
import os
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

W, H, FPS, CLIP_S = 320, 180, 24, 6.0
MIX = {"opacity": 50, "scale": 50}

# film: CLIP_A 0–4 s on V1; CLIP_B 0.5–3.5 laid at film 0.5 on V2 (over 0.5–3.5)
EDL = {
    "variant": "T", "title": "overlay controls", "orient": "none", "story": "",
    "target_s": [2, 20],
    "segments": [{"id": "s1", "clip": "CLIP_A.MP4", "in": 0.0, "out": 4.0, "why": "red"}],
    "overlays": [{"id": "o1", "clip": "CLIP_B.MP4", "in": 0.5, "out": 3.5, "at": 0.5,
                  "controls": MIX}],
}


def _clip(path: Path, colour: str, hz: int) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i", f"color=c={colour}:size={W}x{H}:rate={FPS}:duration={CLIP_S}",
         "-f", "lavfi", "-i", f"sine=frequency={hz}:duration={CLIP_S}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True)


def _sidecar(stem: str) -> dict:
    """Enough of an audio sidecar for the board to list the clip (and give it a proxy)."""
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
    root = tmp_path_factory.mktemp("ovctl")
    footage, sidecars, work, assets = (root / d for d in ("footage", "sidecars", "work", "assets"))
    for d in (footage, sidecars, work, assets / "music"):
        d.mkdir(parents=True)
    for stem, colour, hz in (("CLIP_A", "red", 440), ("CLIP_B", "blue", 880)):
        _clip(footage / f"{stem}.MP4", colour, hz)
        (sidecars / f"{stem}.audio.json").write_text(json.dumps(_sidecar(stem)), encoding="utf-8")
    edl = root / "edl.json"
    edl.write_text(json.dumps(EDL, indent=1), encoding="utf-8")
    return {"root": root, "footage": footage, "sidecars": sidecars, "work": work,
            "assets": assets, "edl": edl}


# ------------------------------------------------------------------ the export

def render(proj: dict, edl: dict, name: str, profile: str = "match", env: dict | None = None):
    path = proj["root"] / f"{name}.json"
    path.write_text(json.dumps(edl), encoding="utf-8")
    out = proj["root"] / f"{name}.mp4"
    r = subprocess.run(["uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(path),
                        "--footage", str(proj["footage"]), "--profile", profile,
                        "-o", str(out)],
                       capture_output=True, text=True, env={**os.environ, **(env or {})})
    assert r.returncode == 0, r.stdout + r.stderr
    return out, r.stdout


def rgb(path: Path, t: float, w: int = W, h: int = H) -> np.ndarray:
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{t:.3f}", "-i", str(path),
                        "-frames:v", "1", "-vf", f"scale={w}:{h}", "-f", "rawvideo",
                        "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).astype(np.float64).reshape(h, w, 3)


def box(a: np.ndarray, cx: float, cy: float, half: int = 6) -> np.ndarray:
    """Mean RGB of a small box centred at fractions (cx, cy) of the frame."""
    h, w = a.shape[:2]
    x, y = int(cx * w), int(cy * h)
    return a[y - half:y + half, x - half:x + half].reshape(-1, 3).mean(axis=0)


def within(got, want, pct: float = 10.0) -> bool:
    return bool(np.all(np.abs(np.asarray(got) - np.asarray(want)) <= 255 * pct / 100))


@pytest.fixture(scope="module")
def films(proj) -> dict:
    plain = {**EDL, "overlays": [{k: v for k, v in EDL["overlays"][0].items() if k != "controls"}]}
    mix, log = render(proj, EDL, "mix")
    base, _ = render(proj, plain, "plain")
    return {"mix": mix, "plain": base, "log": log}


def expected_mix(films) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    red = box(rgb(films["plain"], 0.2), 0.5, 0.5)       # V1 alone, before the overlay
    blue = box(rgb(films["plain"], 2.0), 0.5, 0.5)      # V2 as it is, over all of V1
    return red, blue, 0.5 * red + 0.5 * blue


def test_v2_at_opacity_50_scale_50_mixes_over_v1_in_the_export(films):
    red, blue, want = expected_mix(films)
    a = rgb(films["mix"], 2.0)
    centre = box(a, 0.5, 0.5)
    assert within(centre, want), (centre, want)
    # never blended toward black: the mix keeps V1's red half (a black blend has none)
    assert centre[0] > 0.35 * red[0], centre
    # half size: inside the middle half it is the mix, outside it V1's red as it is
    for cx, cy in ((0.5 - 0.2, 0.5), (0.5 + 0.2, 0.5), (0.5, 0.5 - 0.2), (0.5, 0.5 + 0.2)):
        assert within(box(a, cx, cy, 3), want), (cx, cy, box(a, cx, cy, 3))
    for cx, cy in ((0.1, 0.1), (0.9, 0.9), (0.5 - 0.32, 0.5), (0.5, 0.5 + 0.32)):
        assert within(box(a, cx, cy, 3), red, 4), (cx, cy, box(a, cx, cy, 3))
    # outside the overlay's range the film is V1
    assert within(box(rgb(films["mix"], 3.8), 0.5, 0.5), red, 4)


def test_the_log_names_the_overlays_controls(films):
    assert "scale 50%" in films["log"] and "opacity 50%" in films["log"], films["log"]


def test_an_overlay_without_controls_covers_v1_as_before(films):
    red, blue, _ = expected_mix(films)
    a = rgb(films["plain"], 2.0)
    assert within(box(a, 0.1, 0.1, 3), blue, 4) and within(box(a, 0.5, 0.5), blue, 4)
    assert blue[2] > 150 and red[0] > 150


def test_overlay_keys_run_from_its_own_first_frame(proj, films):
    # opacity 0 → 100 over the overlay's first 2 s: at film 1.5 (1 s in) it is 50 %
    edl = {**EDL, "overlays": [{**EDL["overlays"][0],
                                "controls": {"keys": {"opacity": [[0.0, 0.0], [2.0, 100.0]],
                                                      "scale": [[0.0, 50.0], [2.0, 50.0]]}}}]}
    out, _ = render(proj, edl, "keyed")
    red, blue, want = expected_mix(films)
    a = rgb(out, 1.5 + 0.5 / FPS)
    assert within(box(a, 0.5, 0.5), want), (box(a, 0.5, 0.5), want)
    assert within(box(a, 0.1, 0.1, 3), red, 4)                 # scale key 50: V1 outside
    assert within(box(rgb(out, 0.5 + 0.5 / FPS), 0.5, 0.5), red, 6)   # first frame: 0 %


def test_overlay_rotation_and_position_move_it_over_v1(proj, films):
    edl = {**EDL, "overlays": [{**EDL["overlays"][0],
                                "controls": {"scale": 50, "position": [0.25, 0.25],
                                             "rotation": 90}}]}
    out, _ = render(proj, edl, "moved")
    red, blue, _ = expected_mix(films)
    a = rgb(out, 2.0)
    assert within(box(a, 0.25, 0.25, 3), blue, 6), box(a, 0.25, 0.25, 3)
    assert within(box(a, 0.75, 0.75, 3), red, 4)
    assert within(box(a, 0.5, 0.5, 3), red, 4)


def test_the_gpu_path_writes_what_the_cpu_path_does(proj, films):
    """Quick 1080p: GPU decode/scale + NVENC (when this box has them) against the CPU
    path with x264; the same picture within a few code values, the same length."""
    gpu, glog = render(proj, EDL, "quick-gpu", "preview")
    cpu, clog = render(proj, EDL, "quick-cpu", "preview", {"ROUGHCUT_GPU": "0"})
    assert "libx264" in clog and "GPU decode" not in clog, clog
    for t in (0.2, 2.0, 3.8):
        g, c = rgb(gpu, t, 192, 108), rgb(cpu, t, 192, 108)
        assert float(np.mean(np.abs(g - c))) < 3.0, t
    pg = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                         "-show_entries", "stream=nb_read_frames,codec_name", "-of", "json",
                         str(gpu)], capture_output=True, text=True, check=True)
    pc = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                         "-show_entries", "stream=nb_read_frames,codec_name", "-of", "json",
                         str(cpu)], capture_output=True, text=True, check=True)
    sg, sc = json.loads(pg.stdout)["streams"][0], json.loads(pc.stdout)["streams"][0]
    assert sg["nb_read_frames"] == sc["nb_read_frames"]
    assert sg["codec_name"] == sc["codec_name"] == "h264"


def test_the_overlay_graph_pieces():
    import assemble
    from fractions import Fraction
    r, p = Fraction(24), Path("/tmp/x.cmd")
    assert assemble.overlay_controls_graph(None, W, H, r, 10, "a", "b", p, 0.0, "o0") is None
    assert assemble.overlay_controls_graph({"opacity": 0}, W, H, r, 10, "a", "b", p, 0.0, "o0") == "empty"
    assert assemble.overlay_controls_graph({"scale": 0}, W, H, r, 10, "a", "b", p, 0.0, "o0") == "empty"
    g, x, y = assemble.overlay_controls_graph({"opacity": 50}, W, H, r, 10, "a", "b", p, 0.0, "o0")
    assert "lutyuv=a='val*0.5'" in g and (x, y) == (0, 0) and "16+" not in g
    g, x, y = assemble.overlay_controls_graph(MIX, W, H, r, 10, "a", "b", p, 0.0, "o0")
    assert "yuva420p" in g and (x, y) == (W // 4, H // 4), (g, x, y)
    g, _, _ = assemble.overlay_controls_graph({"rotation": 30}, W, H, r, 10, "a", "b", p, 0.0, "o0")
    assert "c=black@0" in g


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
    server.ensure_proxies(["CLIP_A.MP4", "CLIP_B.MP4"])
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


def test_the_server_keeps_an_overlays_controls(live_server, proj):
    import httpx
    proj["edl"].write_text(json.dumps(EDL, indent=1), encoding="utf-8")
    p = httpx.get(f"{live_server}/api/project").json()
    assert p["overlays"][0]["controls"] == MIX
    body = {"segments": EDL["segments"],
            "overlays": [{**EDL["overlays"][0], "controls": {"opacity": 40, "scale": 100}}]}
    r = httpx.put(f"{live_server}/api/project", json=body)
    assert r.status_code == 200, r.text
    assert httpx.get(f"{live_server}/api/project").json()["overlays"][0]["controls"] == {"opacity": 40.0}
    for bad in ({"opacity": 400}, {"warp": 1}, {"scale": [1]}):
        body["overlays"][0]["controls"] = bad
        r = httpx.put(f"{live_server}/api/project", json=body)
        assert r.status_code == 400, (bad, r.text)
    proj["edl"].write_text(json.dumps(EDL, indent=1), encoding="utf-8")


# ------------------------------------------------------------------ the Program

def program_rgb(pg, cx: float = 0.5, cy: float = 0.5) -> np.ndarray:
    """The mean colour of a small box of the Program picture, from a real screenshot."""
    png = pg.locator("#player .screen").screenshot()
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-f", "png_pipe", "-i", "-",
                        "-vf", f"scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       input=png, capture_output=True)
    assert r.returncode == 0, r.stderr[-300:]
    a = np.frombuffer(r.stdout, np.uint8).astype(np.float64).reshape(H, W, 3)
    return box(a, cx, cy, 4)


def test_the_program_composites_the_overlays_controls(live_server, proj):
    """The Program monitor's V2 layer takes the overlay's controls: at 50 % / 50 % the
    middle of the picture is the mix of the browser's own V1 red and V2 blue (±10 %),
    and outside the half-size box it is V1."""
    playwright_api = pytest.importorskip("playwright.sync_api")
    proj["edl"].write_text(json.dumps(EDL, indent=1), encoding="utf-8")
    shown = """(() => { const v = document.querySelector('#pvV2');
        return v.style.visibility !== 'hidden' && v.readyState >= 2 && !v.seeking; })()"""
    with playwright_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(live_server)
        pg.wait_for_selector("#tl .blk")
        pg.wait_for_function("tl.overlays().length === 1 && segs.length === 1")
        pg.evaluate("tl.seek(0.2)")
        pg.wait_for_timeout(600)
        red = program_rgb(pg)                                   # V1 alone
        assert red[0] > 150 and red[2] < 60, red
        pg.evaluate("tl.seek(2.0)")
        pg.wait_for_function(shown)
        pg.wait_for_timeout(400)
        look = pg.evaluate("""(() => { const v = document.querySelector('#pvV2');
            return {tf: v.style.transform, op: v.style.opacity}; })()""")
        assert "scale(0.5)" in look["tf"] and look["op"] == "0.5", look
        # V2 as it is (no controls), for the expected mix; then its own controls again
        pg.evaluate("v2Look({controls: {}}, 1.5)")
        pg.wait_for_timeout(300)
        blue = program_rgb(pg)
        assert blue[2] > 150 and blue[0] < 60, blue
        pg.evaluate("programSync()")
        pg.wait_for_timeout(300)
        want = 0.5 * red + 0.5 * blue
        centre = program_rgb(pg)
        assert within(centre, want), (centre, want)
        assert centre[0] > 0.35 * red[0], centre                # over V1, not over black
        for cx, cy in ((0.5 - 0.2, 0.5), (0.5 + 0.2, 0.5)):
            assert within(program_rgb(pg, cx, cy), want), (cx, cy)
        for cx, cy in ((0.1, 0.5), (0.9, 0.5), (0.5, 0.12), (0.5, 0.88)):
            assert within(program_rgb(pg, cx, cy), red, 6), (cx, cy, program_rgb(pg, cx, cy))
        # keys: opacity 0 → 100 over the overlay's first 2 s is 50 % at 1 s into it
        op = pg.evaluate("""(() => { v2Look({controls: {opacity: 20, keys: {opacity: [[0, 0], [2, 100]]}}}, 1.0);
            return document.querySelector('#pvV2').style.opacity; })()""")
        assert op == "0.5", op
        pg.evaluate("programSync()")
        # past the overlay: V1 alone
        pg.evaluate("tl.seek(3.8)")
        pg.wait_for_function("document.querySelector('#pvV2').style.visibility === 'hidden'")
        pg.wait_for_timeout(300)
        assert within(program_rgb(pg), red, 6)
        assert not errors, errors
        browser.close()
