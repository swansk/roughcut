"""A clip parked and stepped in a 59.94 sequence shows the frame its timecode names (wave 18
FRAME59, verdict 14 punch 2; REDESIGN-GOAL dimension 6).

Judge 14 frame-stepped the 59.94 Killington sequence: a 29.97 clip in it (CLIP_07, whose in
is half way into a source frame) held its frames 1, 2 and 3 steps rather than 2:2. The
playhead sits anywhere in a frame (a seek to a time, a click on the ruler, every step after it
keeps that phase), the park went that far into the frame, and 1.0 s into a 59.94 shot is 0.94
of a frame in, so the buffer was parked microseconds from the next source frame's start, where
the decoder's pick goes either way. Now the monitor parks on the start of the sequence frame
the playhead's timecode names (app.js `seqFrameAt`, in the cue and the rest still).

The fixture is test_parked_frame's (PLAY8's measure): lossless clips with a keyframe every 8
frames, every frame its own grey (16 + 7·(N mod 32)), so a picture's luma says which source
frame it is; proxied by the server's own `ensure_proxies`; the export rendered by assemble.py
(the CPU path). Asserted: a 29.97 clip whose in is half way into a frame, stepped 24 times in a
59.94 sequence from 1.0 s into the shot, holds every source frame exactly 2 steps, each the
export's.
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
R60, R30 = "60000/1001", "30000/1001"
CLIPS = {"N60": R60, "N30": R30}
CLIP_S = 6.0
STEP = 7 * 255 / 219              # one source frame of grey, in the browser's full-range RGB
GEQ = "lum='16+mod(N\\,32)*7':cb=128:cr=128"
SEQ = Fraction(60000, 1001)


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


def _sidecar(stem: str) -> dict:
    return {"clip": f"{stem}.MP4", "duration_s": CLIP_S, "sample_rate": 16000, "frame_hz": 10,
            "tracks": {"speech_band_db": [-30.0] * 60, "flatness": [0.05] * 60,
                       "onset": [0.2] * 60, "vad": [0] * 60},
            "transcript": [], "events": [],
            "summary": {"speech_fraction": 0.0, "wind_dominant_fraction": 0.0,
                        "integrated_lufs": -30.0, "peak_dbfs": -20.0, "audio_usable": False,
                        "n_words": 0, "analysis_s": 0.1},
            "candidates": []}


def _edl(segs: list[dict]) -> dict:
    return {"variant": "F", "title": "parked5994", "orient": "none", "story": "",
            "target_s": [1, 30], "fps": float(SEQ), "segments": segs}


def _shots60() -> list[dict]:
    """test_parked_frame's `_edl` at 59.94: 16 shots of the 59.94 clip, shot k's in at its
    frame k to 4 places, 3 frames each."""
    R = _rate(R60)
    return [{"id": f"s{k}", "clip": "N60.MP4", "in": round(float(k / R), 4),
             "out": round(float(k / R + 3 / R), 4)} for k in range(16)]


def _one(clip: str, first: float, n: int) -> list[dict]:
    """One shot of `clip` from source frame `first` (a half frame allowed), n sequence frames."""
    R = _rate(CLIPS[clip.split(".")[0]])
    a = round(float(Fraction(first).limit_denominator(4) / R), 4)
    return [{"id": "s0", "clip": clip, "in": a, "out": round(a + float(n / SEQ), 4)}]


def _export(root: Path, footage: Path, tag: str, segs: list[dict]) -> list[int]:
    """The export of `segs` in a 59.94 sequence: which source frame (mod 32) each film frame is."""
    d = root / f"render_{tag}"
    d.mkdir(exist_ok=True)
    (d / "edl.json").write_text(json.dumps(_edl(segs)), encoding="utf-8")
    film = d / "film.mp4"
    r = subprocess.run(["uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(d / "edl.json"),
                        "--footage", str(footage), "--profile", "match", "--fps", R60,
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
    root = tmp_path_factory.mktemp("parked5994")
    footage, sidecars, work, assets = (root / d for d in ("footage", "sidecars", "work", "assets"))
    for d in (footage, sidecars, work, assets / "music"):
        d.mkdir(parents=True)
    for stem, rate in CLIPS.items():
        _make(footage / f"{stem}.MP4", rate)
        (sidecars / f"{stem}.audio.json").write_text(json.dumps(_sidecar(stem)), encoding="utf-8")
    edl = root / "edl.json"
    edl.write_text(json.dumps(_edl(_shots60())), encoding="utf-8")
    return {"root": root, "footage": footage, "sidecars": sidecars, "work": work,
            "assets": assets, "edl": edl}


def _configure(proj: dict, segs: list[dict]) -> None:
    import server
    proj["edl"].write_text(json.dumps(_edl(segs)), encoding="utf-8")
    server.configure(proj["edl"], proj["footage"], proj["sidecars"], proj["work"], proxies=False,
                     visual=proj["work"] / "no-visual", assets=proj["assets"])


@pytest.fixture(scope="module")
def live(proj):
    import uvicorn
    import server
    _configure(proj, _shots60())
    server.ensure_proxies([f"{s}.MP4" for s in CLIPS])        # the shipped build_proxy
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


# test_parked_frame's reads: the mean of a picture's red channel, drawn 32x18 (a poster URL,
# once it is the frame itself and not the server's stand-in, or the live <video>); a park on
# film time t through the timeline's seek (the monitor's cue), waited on to the frame there
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

REST = """(t) => {
  tl.setPlayhead(t, { reveal: false });
  paintRestStill();
  return document.querySelector('#monPoster').getAttribute('src');
}"""

# after a one-frame step (the timeline's ArrowRight): the live buffer has stopped seeking on
# a frame other than the one before the key (or 1.5 s), then its next frame callback
SETTLED = """async (before) => {
  const t0 = performance.now();
  for (;;) {
    const v = liveVideo();
    if (v && v.readyState >= 2 && !v.seeking && Math.abs(v.currentTime - before) > 0.002) break;
    if (performance.now() - t0 > 1500) break;
    await new Promise((r) => setTimeout(r, 15));
  }
  await new Promise((r) => { const v = liveVideo();
    if (v.requestVideoFrameCallback) { v.requestVideoFrameCallback(() => r()); setTimeout(r, 300); } else setTimeout(r, 120); });
  const at = tl.shotAt(tl.state.playhead);
  return { playhead: tl.state.playhead, clipT: at.clipT, video: liveVideo().currentTime };
}"""


def _page(pw, live: str, nsegs: int):
    b = pw.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    ctx.add_init_script("try { localStorage.setItem('roughcut.tour', 'seen') } catch (e) {}")
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(live)
    pg.wait_for_selector("#tl .blk")
    pg.wait_for_function(f"segs.length === {nsegs}")
    return b, pg, errors


def _steps(pg, start: float, n: int) -> list[tuple[float, int]]:
    """Park on film time `start`, then n one-frame steps (the timeline's →, as an editor and
    judge 14's fstep.py press it): each step's playhead and the source frame (mod 32) shown."""
    pg.evaluate(PARK, start)
    pg.locator("#pTimeline .ws-head").first.click()        # the Timeline has the keys
    out = []
    for _ in range(n):
        before = pg.evaluate("liveVideo().currentTime")
        pg.keyboard.press("ArrowRight")
        at = pg.evaluate(SETTLED, before)
        out.append((at["playhead"], round(pg.evaluate(MEAN, None) / STEP) % 32))
    return out


def test_a_29_97_clip_in_a_59_94_sequence_holds_2_2(live, proj):
    """A 29.97 clip whose in is half way into a source frame (judge 14's CLIP_07), in a 59.94
    sequence, stepped 24 frames from 1.0 s into the shot: every source frame holds exactly two
    steps (the first may be cut short), and each is the export's. Before: holds of 1, 2, 3."""
    segs = _one("N30.MP4", Fraction(21, 2), 150)            # in at source frame 10.5
    _configure(proj, segs)
    export = _export(proj["root"], proj["footage"], "step30", segs)
    assert export[:5] == [10, 11, 11, 12, 12], export[:8]
    with sync_playwright() as pw:
        b, pg, errors = _page(pw, live, 1)
        rows = _steps(pg, 1.0, 24)
        b.close()
    assert not errors, errors
    want = [export[round(t * float(SEQ))] for t, _ in rows]
    shown = [f for _, f in rows]
    runs: list[int] = []
    for a, c in zip([None] + shown, shown):
        if a == c:
            runs[-1] += 1
        else:
            runs.append(1)
    print(f"\n29.97 in 59.94, 24 steps from 1.0 s: shown {shown}, holds {runs}, export {want}")
    assert shown == want, (shown, want)
    assert all(r == 2 for r in runs[1:-1]) and runs[0] <= 2 and runs[-1] <= 2, runs
