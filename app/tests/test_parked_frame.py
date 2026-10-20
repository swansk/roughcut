"""The Program monitor parked on a frame shows the export's frame (wave 17 PLAY8; lane
FRAMEACC's queued finding; REDESIGN-GOAL dimensions 5 and 6).

FRAMEACC made the export frame-exact: a shot's `in` names the source frame on screen at
that time, the last one that starts at or before it (give or take assemble.py's
SEEK_TOL_S, 1 ms), and every film frame after it is the last source frame at or before
its time. The monitor did not follow: the timeline's `shotAt` rounded the clip time under
the playhead to 0.01 s and that is what the monitor cued, with no tolerance, so an in kept
to 4 places (the edit grid's) parked on the frame before whenever the rounding landed
under the frame's start; and the rest still (the monitor's picture before any buffer is
live, and the inspector's) was asked at 0.01 s and cut with `-ss <t>` ahead of `-i`, which
keeps the first frame from that time on: the one after, whenever the time fell inside a
frame. FRAMEACC measured the parked <video> right 14/48 at 59.94, 27/48 at 29.97 and
28/48 at 23.976.

Now the playhead's clip time is exact, the monitor parks 1 ms past it (the export's
SEEK_TOL_S), and the rest still is cut at the time to 4 places with an early seek and the
last frame at or before it + 1 ms.

The fixture is FRAMEACC's: lossless clips with a keyframe every 8 frames, every frame its
own grey (16 + 7·(N mod 32)), so a picture's luma says which source frame it is. Sixteen
shots of one clip, shot k's in at its frame k (to 4 places), 3 film frames each, rendered
by assemble.py (the export, CPU path) and parked on in the Program at every one of their
48 frames. 29.97 and 23.976 are asserted 48/48. A 59.94 clip plays a 29.97 proxy in the
monitor (build_proxy halves a rate over 30), so its odd frames do not exist there: that
rate is printed, not asserted (the parked frame is the proxy frame at or before the time,
which is the export's frame or the one before it).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from fractions import Fraction
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "research" / "tools"
RATES = {"30000/1001": "N30", "24000/1001": "N24", "60000/1001": "N60"}
ASSERTED = ("30000/1001", "24000/1001")
CLIP_S = 6.0
STEP = 7 * 255 / 219              # one source frame of grey, in the browser's full-range RGB
GEQ = "lum='16+mod(N\\,32)*7':cb=128:cr=128"


def _rate(text: str) -> Fraction:
    num, _, den = text.partition("/")
    return Fraction(int(num), int(den or 1))


def _make(path: Path, rate: str) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i", f"color=black:size=320x180:rate={rate}:duration={CLIP_S},"
                              f"format=yuv420p,geq={GEQ}",
         "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo,atrim=0:{CLIP_S}",
         "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-g", "8",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", f"{CLIP_S}", str(path)],
        check=True, capture_output=True)


def _edl(rate: str) -> dict:
    """FRAMEACC's `_shots`: 16 shots, shot k's in at its frame k to 4 places, 3 frames each."""
    R = _rate(rate)
    clip = f"{RATES[rate]}.MP4"
    segs = [{"id": f"s{k}", "clip": clip, "in": round(float(k / R), 4),
             "out": round(float(k / R + 3 / R), 4)} for k in range(16)]
    return {"variant": "F", "title": "parked", "orient": "none", "story": "",
            "target_s": [1, 30], "fps": float(R), "segments": segs}


def _sidecar(stem: str) -> dict:
    return {"clip": f"{stem}.MP4", "duration_s": CLIP_S, "sample_rate": 16000, "frame_hz": 10,
            "tracks": {"speech_band_db": [-30.0] * 60, "flatness": [0.05] * 60,
                       "onset": [0.2] * 60, "vad": [0] * 60},
            "transcript": [], "events": [],
            "summary": {"speech_fraction": 0.0, "wind_dominant_fraction": 0.0,
                        "integrated_lufs": -30.0, "peak_dbfs": -20.0, "audio_usable": False,
                        "n_words": 0, "analysis_s": 0.1},
            "candidates": []}


def _export_frames(root: Path, footage: Path, rate: str) -> list[int]:
    """The export of `_edl(rate)`: which source frame each of its 48 frames is."""
    d = root / f"render_{RATES[rate]}"
    d.mkdir(exist_ok=True)
    (d / "edl.json").write_text(json.dumps(_edl(rate)), encoding="utf-8")
    film = d / "film.mp4"
    r = subprocess.run(["uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(d / "edl.json"),
                        "--footage", str(footage), "--profile", "match", "--fps", rate,
                        "--jobs", "2", "-o", str(film)],
                       capture_output=True, text=True, env={**os.environ, "ROUGHCUT_GPU": "0"})
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    raw = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(film),
                          "-vf", "scale=32:18", "-pix_fmt", "yuv420p", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    n, size = 32 * 18, 32 * 18 * 3 // 2
    return [round((sum(raw[i:i + n]) / n - 16) / 7) for i in range(0, len(raw), size)]


@pytest.fixture(scope="module")
def proj(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("parked")
    footage, sidecars, work, assets = (root / d for d in ("footage", "sidecars", "work", "assets"))
    for d in (footage, sidecars, work, assets / "music"):
        d.mkdir(parents=True)
    for rate, stem in RATES.items():
        _make(footage / f"{stem}.MP4", rate)
        (sidecars / f"{stem}.audio.json").write_text(json.dumps(_sidecar(stem)), encoding="utf-8")
    edl = root / "edl.json"
    edl.write_text(json.dumps(_edl(ASSERTED[0])), encoding="utf-8")
    return {"root": root, "footage": footage, "sidecars": sidecars, "work": work,
            "assets": assets, "edl": edl}


@pytest.fixture(scope="module")
def live(proj):
    import uvicorn
    import server
    server.configure(proj["edl"], proj["footage"], proj["sidecars"], proj["work"], proxies=False,
                     visual=proj["work"] / "no-visual", assets=proj["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in RATES.values()])
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
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    th.join(timeout=10)


# the mean of a picture's red channel, drawn 32x18: a poster URL or the live <video>. A
# poster is read once it is the frame itself, not the server's stand-in for a frame still
# being cut (`x-roughcut-poster`: the nearest still on disk, which the board replaces)
MEAN = """async (src) => {
  const c = document.createElement('canvas'); c.width = 32; c.height = 18;
  const g = c.getContext('2d', {willReadFrequently: true});
  let el = liveVideo();
  if (src) {
    for (let i = 0; ; i++) {
      const r = await fetch(src, {cache: 'no-store'});
      if (r.ok && !r.headers.get('x-roughcut-poster')) { el = await createImageBitmap(await r.blob()); break; }
      if (i > 150) throw new Error('only a stand-in for ' + src);
      await new Promise((res) => setTimeout(res, 100));
    }
  }
  g.drawImage(el, 0, 0, 32, 18);
  const d = g.getImageData(0, 0, 32, 18).data; let s = 0;
  for (let i = 0; i < d.length; i += 4) s += d[i];
  return s / (d.length / 4);
}"""

# park the Program on film time t (the timeline's seek: the monitor's cue) and wait for
# the buffer's frame there
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
  // the decoded frame of that time is the element's picture from its next frame callback
  await new Promise((r) => { const v = liveVideo();
    if (v.requestVideoFrameCallback) { v.requestVideoFrameCallback(() => r()); setTimeout(r, 300); } else setTimeout(r, 120); });
  return want;
}"""

# the rest still the monitor asks for at film time t, before any buffer is live
REST = """(t) => {
  tl.setPlayhead(t, { reveal: false });
  paintRestStill();
  return document.querySelector('#monPoster').getAttribute('src');
}"""


def _parked(live: str, proj: dict, rate: str) -> tuple[list[tuple], list[int]]:
    import server
    edl = _edl(rate)
    proj["edl"].write_text(json.dumps(edl), encoding="utf-8")
    server.configure(proj["edl"], proj["footage"], proj["sidecars"], proj["work"], proxies=False,
                     visual=proj["work"] / "no-visual", assets=proj["assets"])
    export = _export_frames(proj["root"], proj["footage"], rate)
    assert len(export) == 48, len(export)
    R = _rate(rate)
    rows = []
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(live)
        pg.wait_for_selector("#tl .blk")
        pg.wait_for_function("segs.length === 16")
        times = [(k, j, pg.evaluate(f"tl.filmStart('s{k}')") + j / float(R))
                 for k in range(16) for j in range(3)]
        # the rest still first, while no buffer is live yet
        rest = {}
        for k, j, t in times:
            src = pg.evaluate(REST, t)
            rest[(k, j)] = (src, round(pg.evaluate(MEAN, src) / STEP))
        for k, j, t in times:
            at = pg.evaluate(PARK, t)
            rows.append((k, j, at["index"], round(pg.evaluate(MEAN, None) / STEP),
                         rest[(k, j)][1], at["clipT"], rest[(k, j)][0]))
        b.close()
        assert not errors, errors
    return rows, export


@pytest.mark.parametrize("rate", list(RATES))
def test_the_parked_program_frame_is_the_exports(live, proj, rate):
    rows, export = _parked(live, proj, rate)
    # the export's frame f = 3k + j is source frame k + j (test_frame_accurate_export)
    assert export == [k + j for k in range(16) for j in range(3)], export
    bad_v = [(k, j, v, export[3 * k + j]) for k, j, i, v, p, c, s in rows
             if v != export[3 * k + j] or i != k]
    bad_p = [(k, j, p, export[3 * k + j], s) for k, j, i, v, p, c, s in rows
             if p != export[3 * k + j]]
    print(f"\n{rate}: parked <video> {48 - len(bad_v)}/48 the export's frame, wrong "
          f"(shot, frame, shown, export): {bad_v}")
    print(f"{rate}: rest still {48 - len(bad_p)}/48 the export's frame, wrong: {bad_p}")
    if rate not in ASSERTED:
        # a 29.97 proxy: the frame at or before the time, so the export's or the one before
        assert all(export[3 * k + j] - v in (0, 1) and export[3 * k + j] - p in (0, 1)
                   for k, j, i, v, p, c, s in rows), rows
        return
    assert bad_v == [], bad_v
    assert bad_p == [], bad_p
    # the clip time the monitor parks on is the exact one, not 0.01 s rounded
    R = _rate(rate)
    for k, j, i, v, p, c, s in rows:
        assert c == pytest.approx(round(float(k / R), 4) + j / float(R), abs=1e-6), (k, j, c)
        assert "t=" in s and len(s.split("t=")[-1].split(".")[-1]) == 4, s
