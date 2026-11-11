"""The exported file's colour is what the Program monitor showed (wave 25, lane MATCH).

The finishing editor measured the export 2-5 levels darker in red than the monitor on every
frame, twice (FINISH-19). This pins the whole path with instruments that are exact:

  * the sources are flat patches at known YUV code values, full-range (`yuvj420p`, what a
    GoPro writes) and limited-range, a grey ramp and eight saturated colours, tagged BT.709;
  * the monitor is the board's own canvas read back (`grade.sample`, the pixels the Program
    draws, not a screenshot: a screenshot of a WebGL canvas is itself a level darker);
  * the file is a real export (assemble.py, the CPU path) decoded by ffmpeg with the exact
    conversion (`full_chroma_int`): a default `ffmpeg -i f.mp4 x.png` is up to 2.3 levels
    darker than the file's pixels, which is most of what FINISH-19 measured.

What it found, both on the export's side and both fixed there:
  * a graded shot's chain converted RGB back to YUV with swscale's default matrix (BT.601)
    while the file is tagged BT.709: saturated colours came out up to 8 levels off, red and
    blue the other way on a blue sky (`colour.lut_vf`, now `out_color_matrix=bt709`);
  * a title or effect sprite (RGBA) met the film the same way inside `overlay` (a #33cc33 title
    came out [39 181 46], 23 levels short in green; `fx.part_graph`, now stated).
The monitor was exact to the formula in both cases.
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

playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "research" / "tools"
W, H, COLS, ROWS = 640, 360, 6, 4
FRAME_T = 7 * 1001 / 24000            # source frame 7 at 23.976: the frame the Program parks on

# the shot's balance, as Copper's hand-graded shot has it (gain, exposure, shoulder, lift)
HAND = {"mode": "auto", "look": None, "strength": 0.5, "reference": None,
        "shots": {"s1": {"balance": {"gain": [0.9626, 0.996, 1.0414], "exposure": 1.0028,
                                     "knee": 0.8, "lift": -0.04, "source": "hand"}}}}
OFF = {"mode": "off"}


def _patches(full: bool) -> list[tuple[int, int, int]]:
    """Sixteen greys across the range and eight saturated colours, as (Y, U, V) codes."""
    lo, hi = (0, 255) if full else (16, 235)
    cr = (0, 255) if full else (16, 240)
    out = [(round(lo + (hi - lo) * i / 15), 128, 128) for i in range(16)]
    for y, u, v in [(0.45, 0.40, 0.62), (0.55, 0.60, 0.40), (0.35, 0.62, 0.42), (0.70, 0.42, 0.55),
                    (0.30, 0.55, 0.60), (0.80, 0.48, 0.52), (0.15, 0.52, 0.55), (0.60, 0.58, 0.48)]:
        out.append((round(lo + (hi - lo) * y), round(cr[0] + (cr[1] - cr[0]) * u),
                    round(cr[0] + (cr[1] - cr[0]) * v)))
    return out


def _make(path: Path, full: bool) -> None:
    pw, ph = W // COLS, H // ROWS
    Y = np.zeros((H, W), np.uint8)
    U = np.zeros((H // 2, W // 2), np.uint8)
    V = U.copy()
    for k, (y, u, v) in enumerate(_patches(full)):
        r, c = divmod(k, COLS)
        Y[r * ph:(r + 1) * ph, c * pw:(c + 1) * pw] = y
        U[r * ph // 2:(r + 1) * ph // 2, c * pw // 2:(c + 1) * pw // 2] = u
        V[r * ph // 2:(r + 1) * ph // 2, c * pw // 2:(c + 1) * pw // 2] = v
    pf = "yuvj420p" if full else "yuv420p"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "rawvideo", "-pix_fmt", pf, "-s", f"{W}x{H}",
         "-r", "24000/1001", "-i", "-", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-t", "1", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", pf, "-g", "12",
         "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
         "-color_range", "pc" if full else "tv", "-c:a", "aac", str(path)],
        input=(Y.tobytes() + U.tobytes() + V.tobytes()) * 24, check=True, capture_output=True)


def _sidecar(stem: str) -> dict:
    return {"clip": f"{stem}.MP4", "duration_s": 1.0, "sample_rate": 16000, "frame_hz": 10,
            "tracks": {"speech_band_db": [-30.0] * 10, "flatness": [0.05] * 10,
                       "onset": [0.2] * 10, "vad": [0] * 10},
            "transcript": [], "events": [],
            "summary": {"speech_fraction": 0.0, "wind_dominant_fraction": 0.0,
                        "integrated_lufs": -30.0, "peak_dbfs": -20.0, "audio_usable": False,
                        "n_words": 0, "analysis_s": 0.1},
            "candidates": []}


def _patch_means(img: np.ndarray) -> np.ndarray:
    """The mean RGB of the middle of each patch of a picture (any size) of the grid."""
    h, w = img.shape[:2]
    out = []
    for k in range(len(_patches(True))):
        r, c = divmod(k, COLS)
        cy, cx = (r + 0.5) / ROWS, (c + 0.5) / COLS
        y0, y1 = int((cy - 0.04) * h), int((cy + 0.04) * h)
        x0, x1 = int((cx - 0.04) * w), int((cx + 0.04) * w)
        out.append(img[y0:y1, x0:x1].reshape(-1, 3).mean(0))
    return np.array(out)


def _file_frame(path: Path, t: float) -> np.ndarray:
    """The file's frame as a viewer's exact conversion gives it: limited BT.709 to full RGB."""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{t:.4f}", "-i", str(path), "-vf",
         f"scale={W}:{H}:flags=area+accurate_rnd+full_chroma_int:in_range=tv:in_color_matrix=bt709"
         ":out_range=full,format=rgb24", "-frames:v", "1", "-f", "rawvideo", "-"],
        capture_output=True)
    assert r.returncode == 0 and len(r.stdout) == W * H * 3, r.stderr[-300:]
    return np.frombuffer(r.stdout, np.uint8).reshape(H, W, 3)


def _export(edl: Path, out: Path, root: Path) -> None:
    parts = out.parent / f"parts_{out.stem}"
    parts.mkdir(exist_ok=True)
    r = subprocess.run(
        ["uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(edl), "--footage", str(root / "footage"),
         "--sidecars", str(root / "sidecars"), "--parts-dir", str(parts), "--keep-parts",
         "--profile", "preview", "-o", str(out)],
        capture_output=True, text=True, env={**os.environ, "ROUGHCUT_GPU": "0"})
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]


# the Program parked on film time t, as the parked-frame test does it
PARK = """async (t) => {
  tl.seek(t);
  const want = tl.shotAt(tl.state.playhead);
  const t0 = performance.now();
  for (;;) {
    const v = liveVideo();
    if (v && v.readyState >= 2 && !v.seeking && player.idx === want.index
        && Math.abs(v.currentTime - want.clipT) < 0.004) break;
    if (performance.now() - t0 > 10000) throw new Error('no frame at ' + t);
    await new Promise((r) => setTimeout(r, 15));
  }
  await new Promise((r) => { const v = liveVideo();
    if (v.requestVideoFrameCallback) { v.requestVideoFrameCallback(() => r()); setTimeout(r, 300); } else setTimeout(r, 120); });
  return want;
}"""

# the canvas the Program shows, read back by the board's own hook: RGBA, bottom row first
SAMPLE = """() => { const s = grade.sample(640); return {w: s.w, h: s.h, data: Array.from(s.data)}; }"""


@pytest.fixture(scope="module")
def proj(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("match")
    for d in ("footage", "sidecars", "work", "assets", "out"):
        (root / d).mkdir()
    for full, stem in ((True, "FULL"), (False, "LIM")):
        _make(root / "footage" / f"{stem}.MP4", full)
        (root / "sidecars" / f"{stem}.audio.json").write_text(json.dumps(_sidecar(stem)), encoding="utf-8")
    edl = root / "edl.json"
    edl.write_text(json.dumps({"variant": "T", "title": "m", "orient": "none", "target_s": [1, 20],
                               "segments": [{"id": "s1", "clip": "FULL.MP4", "in": 0.0, "out": 0.8}]}),
                   encoding="utf-8")
    return {"root": root, "edl": edl}


@pytest.fixture(scope="module")
def live(proj):
    import uvicorn
    import server

    def point():
        server.configure(proj["edl"], proj["root"] / "footage", proj["root"] / "sidecars",
                         proj["root"] / "work", proxies=False, visual=proj["root"] / "work" / "no-visual",
                         assets=proj["root"] / "assets")
    point()
    server.ensure_proxies(["FULL.MP4", "LIM.MP4"])
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started, "server did not start"
    yield {"url": f"http://127.0.0.1:{port}", "point": point}
    srv.should_exit = True
    th.join(timeout=10)


def _doc(clip: str, colour: dict | None, effects: list | None = None) -> dict:
    doc = {"variant": "T", "title": "m", "orient": "none", "target_s": [1, 20],
           "segments": [{"id": "s1", "clip": clip, "in": 0.0, "out": 0.8}]}
    if colour:
        doc["colour"] = colour
    if effects:
        doc["effects"] = effects
    return doc


def _park(url: str, t: float):
    pw = sync_playwright().start()
    b = pw.chromium.launch()
    pg = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(url)
    pg.wait_for_selector("#tl .blk")
    pg.wait_for_function("segs.length === 1")
    pg.evaluate(PARK, t)
    pg.wait_for_timeout(1200)
    return pw, b, pg, errors


@pytest.mark.parametrize("clip", ["FULL.MP4", "LIM.MP4"])
@pytest.mark.parametrize("name,colour", [("ungraded", OFF), ("graded", HAND)])
def test_the_exported_frame_is_the_monitors(live, proj, clip, name, colour):
    """Per channel, over the 24 patches: the mean absolute difference between the Program's
    canvas and the exported file at most 1 level, and no patch more than 3 off."""
    proj["edl"].write_text(json.dumps(_doc(clip, colour)), encoding="utf-8")
    live["point"]()
    pw, b, pg, errors = _park(live["url"], FRAME_T)
    try:
        s = pg.evaluate(SAMPLE)
        assert not errors, errors
    finally:
        b.close()
        pw.stop()
    mon = np.array(s["data"], np.uint8).reshape(s["h"], s["w"], 4)[::-1, :, :3]
    out = proj["root"] / "out" / f"{clip[:-4]}_{name}.mp4"
    _export(proj["edl"], out, proj["root"])
    a, f = _patch_means(mon), _patch_means(_file_frame(out, FRAME_T))
    d = np.abs(a - f)
    print(f"\n{clip} {name}: mean abs diff per channel {d.mean(0).round(2)}, worst patch {d.max(0).round(1)}, "
          f"signed mean {(a - f).mean(0).round(2)}")
    assert (d.mean(0) <= 1.0).all(), d.mean(0)
    assert (d.max(0) <= 3.0).all(), (d.max(0), np.argwhere(d > 3.0)[:6].tolist())


@pytest.mark.parametrize("colour", ["#ff6600", "#1e90ff", "#33cc33"])
def test_a_coloured_title_is_the_colour_it_was_drawn_in(live, proj, colour):
    """A full-frame sprite over the shot: the file shows the colour the sprite was drawn in,
    which is what the Program's overlay canvas draws (RGBA in, RGB out)."""
    fx = {"id": "fx_1", "shot": "s1", "clip": "FULL.MP4", "name": "plate", "note": "x", "why": "x",
          "events": [{"t": 0.3, "x": 0.5, "y": 0.5, "label": "x"}],
          "overlay": {"duration": 0.5, "size": 1.0,
                      "shapes": [{"type": "rect", "color": colour, "opacity": 1.0, "width": 0.08,
                                  "fill": True, "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0, "rotate": 0.0}],
                      "anim": {"opacity": [[0.0, 1.0], [0.5, 1.0]]}},
          "sound": None, "status": "accepted"}
    proj["edl"].write_text(json.dumps(_doc("FULL.MP4", OFF, [fx])), encoding="utf-8")
    live["point"]()
    out = proj["root"] / "out" / f"title_{colour[1:]}.mp4"
    _export(proj["edl"], out, proj["root"])
    t = 11 * 1001 / 24000                          # inside the sprite's 0.3-0.8 s
    px = _file_frame(out, t)[H // 2 - 20:H // 2 + 20, W // 2 - 20:W // 2 + 20].reshape(-1, 3).mean(0)
    want = np.array([int(colour[i:i + 2], 16) for i in (1, 3, 5)], float)
    print(f"\n{colour}: file centre {px.round(1)}")
    assert np.abs(px - want).max() <= 2.0, (px, want)
