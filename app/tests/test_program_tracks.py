"""The Program monitor shows the sequence (verdict 3 punch 4, REDESIGN-GOAL dimension 6).

The export composites V2 over V1 and lays a moved A1 sound at its offset
(test_tracks.py proves the render); the monitor has to show the same thing. Judged in a
real browser on what it presents — the Program's pixels, read from a screenshot — over
two clips that cannot be confused: CLIP_A is solid red, CLIP_B solid blue.

  * V2: CLIP_B laid on V2 over CLIP_A; a seek inside it shows blue, at the overlay's
    own clip time; V2's eye off shows red again; outside the overlay it is red; V1's eye
    off is black under V2's blue. Seek → presented frame ≤ 250 ms.
  * V2 plays with the monitor: inside the overlay while playing it runs at the film's
    pace, and it is gone after the overlay ends.
  * A1: an unlinked shot with `a_off` 1.0 s mutes its picture's sound and plays its own
    from #pvA1 exactly 1.0 s of clip time behind the picture; the gap before the moved
    sound is silent; A1 mute / gain set its level.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

CLIP_S = 8.0
SHOTS = Path.home() / "roughcut-loop" / "lane-program" / "shots"

# film: shot 1 = CLIP_A 0–5 (0–5 s), shot 2 = CLIP_B 0–3 (5–8 s), its sound 1.0 s later
EDL = {
    "variant": "T", "title": "program tracks", "orient": "none", "story": "",
    "target_s": [5, 20],
    "segments": [
        {"id": "s1", "clip": "CLIP_A.MP4", "in": 0.0, "out": 5.0, "why": "red"},
        {"id": "s2", "clip": "CLIP_B.MP4", "in": 0.0, "out": 3.0, "why": "blue",
         "link": False, "a_off": 1.0},
    ],
    # V2: CLIP_B 1.0–3.0 laid at film 1.0 → over film 1.0–3.0
    "overlays": [{"id": "o1", "clip": "CLIP_B.MP4", "in": 1.0, "out": 3.0, "at": 1.0}],
}


def _clip(path: Path, colour: str, hz: int) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i", f"color=c={colour}:size=320x180:rate=24:duration={CLIP_S}",
         "-f", "lavfi", "-i", f"sine=frequency={hz}:duration={CLIP_S}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True)


def _sidecar(stem: str) -> dict:
    return {
        "clip": f"{stem}.MP4", "duration_s": CLIP_S, "sample_rate": 16000, "frame_hz": 10,
        "tracks": {"speech_band_db": [-30.0] * 80, "flatness": [0.05] * 80,
                   "onset": [0.2] * 80, "vad": [0] * 80},
        "transcript": [{"start": 0.5, "end": 2.0, "text": "hello there", "no_speech_prob": 0.1,
                        "words": [{"w": "hello", "t": 0.5, "p": 0.9},
                                  {"w": "there", "t": 1.2, "p": 0.9}]}],
        "events": [],
        "summary": {"speech_fraction": 0.5, "wind_dominant_fraction": 0.0,
                    "integrated_lufs": -18.0, "peak_dbfs": -3.0, "audio_usable": True,
                    "n_words": 2, "analysis_s": 0.1},
        "candidates": [],
    }


@pytest.fixture(scope="module")
def proj(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("progtracks")
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


@pytest.fixture
def page(live_server, proj):
    proj["edl"].write_text(json.dumps(EDL, indent=1), encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(live_server)
        pg.wait_for_selector("#tl .blk")
        pg.wait_for_function("tl.overlays().length === 1 && segs.length === 2")
        yield pg
        assert not errors, errors
        browser.close()


# ------------------------------------------------------------------ helpers

def program_rgb(pg, name: str | None = None) -> tuple[float, float, float]:
    """The mean colour of the middle of the Program picture, from a real screenshot."""
    png = pg.locator("#player .screen").screenshot()
    if name:
        SHOTS.mkdir(parents=True, exist_ok=True)
        (SHOTS / f"{name}.png").write_bytes(png)
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-f", "png_pipe", "-i", "-",
                        "-vf", "crop=iw/2:ih/2,scale=16:9", "-f", "rawvideo",
                        "-pix_fmt", "rgb24", "-"], input=png, capture_output=True)
    assert r.returncode == 0 and len(r.stdout) == 16 * 9 * 3, r.stderr[-300:]
    px = r.stdout
    n = len(px) // 3
    return tuple(sum(px[k::3]) / n for k in range(3))   # type: ignore[return-value]


def is_red(c) -> bool:
    return c[0] > 150 and c[1] < 70 and c[2] < 70


def is_blue(c) -> bool:
    return c[2] > 150 and c[0] < 70 and c[1] < 70


def is_black(c) -> bool:
    return max(c) < 30


SEEK_TO_FRAME = """async (t) => {
  // Seek → the frame each visible layer presents: the first video frame callback whose
  // media time is the sought one (within a frame), asked for at once, since Chrome may
  // present the frame before 'seeked'. Headless Chrome sometimes skips the callback for
  // a layer that was hidden, so 'seeked' (or a ready parked element) plus two animation
  // frames — the frame is painted by then — also ends the wait. A miss counts as 12 s.
  const frame = (v, want) => new Promise((res) => {
    let over = false;
    const end = (x) => { if (!over) { over = true; res(x); } };
    const ask = () => { if (!over) v.requestVideoFrameCallback((now, m) => {
      if (Math.abs(m.mediaTime - want) < 0.05) end(performance.now()); else ask();
    }); };
    ask();
    const painted = () => requestAnimationFrame(() => requestAnimationFrame(() => {
      if (!v.seeking && v.readyState >= 2 && Math.abs(v.currentTime - want) < 0.05) end(performance.now());
    }));
    v.addEventListener('seeked', painted);
    painted();
    setTimeout(() => { v.removeEventListener('seeked', painted); end(performance.now() + 10000); }, 2000);
  });
  const t0 = performance.now();
  tl.seek(t);
  const lv = liveVideo(), v2 = document.querySelector('#pvV2');
  const waits = [frame(lv, tl.shotAt(t).clipT)];
  if (v2.style.visibility !== 'hidden') waits.push(frame(v2, v2._goal.t));
  const ends = await Promise.all(waits);
  return Math.max(...ends) - t0;
}"""


def seek(pg, t: float) -> float:
    ms = pg.evaluate(SEEK_TO_FRAME, t)
    pg.wait_for_timeout(120)          # one more paint for the screenshot
    return ms


def v2_state(pg) -> dict:
    return pg.evaluate("""(() => { const v = document.querySelector('#pvV2');
      return {shown: v.style.visibility !== 'hidden', t: v.currentTime, paused: v.paused,
              src: v.dataset.src}; })()""")


# ------------------------------------------------------------------ V2

def test_v2_draws_over_v1_inside_the_overlay_and_only_there(page):
    seek(page, 0.5)
    assert is_red(program_rgb(page, "v1-outside")), "before the overlay the Program is CLIP_A"
    assert not v2_state(page)["shown"]

    seek(page, 2.0)
    st = v2_state(page)
    assert st["shown"] and "CLIP_B" in st["src"]
    assert abs(st["t"] - 2.0) < 0.05, st                  # in + (t - at) * speed = 1 + 1
    c = program_rgb(page, "v2-over-v1")
    assert is_blue(c), c
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / "board-v2-1440.png"))

    seek(page, 4.0)
    assert not v2_state(page)["shown"]
    assert is_red(program_rgb(page, "v1-after")), "after the overlay V1 shows again"


def test_v2_eye_off_shows_v1_and_v1_eye_off_is_black_under_v2(page):
    seek(page, 2.0)
    assert is_blue(program_rgb(page))
    page.evaluate("tl.setTrack('V2', 'eye', false)")
    page.wait_for_timeout(150)
    assert not v2_state(page)["shown"]
    assert is_red(program_rgb(page, "v2-eye-off")), "V2's eye off: CLIP_A again"
    page.evaluate("tl.setTrack('V2', 'eye', true)")
    page.wait_for_timeout(150)
    assert is_blue(program_rgb(page))

    page.evaluate("tl.setTrack('V1', 'eye', false)")
    page.wait_for_timeout(150)
    assert is_blue(program_rgb(page)), "V2 still draws with V1 hidden"
    seek(page, 4.0)
    c = program_rgb(page, "v1-eye-off")
    assert is_black(c), c
    page.evaluate("tl.setTrack('V1', 'eye', true)")
    page.wait_for_timeout(150)
    assert is_red(program_rgb(page))


def test_seek_to_frame_stays_within_250_ms(page):
    seek(page, 0.3)                    # opens CLIP_A and parks V2 on its overlay's in-point
    seek(page, 1.2)
    times = [seek(page, t) for t in (1.6, 2.4, 0.8, 2.9, 4.5, 1.1, 6.0, 2.2)]
    print("seek → frame ms:", [round(x) for x in times])
    assert max(times) <= 250, times


def test_v2_plays_with_the_monitor(page):
    seek(page, 0.6)
    page.evaluate("playFrom(0)")
    page.wait_for_function("tl.state.playhead > 1.6", timeout=8000)
    st = page.evaluate("""(() => { const v = document.querySelector('#pvV2');
      return {shown: v.style.visibility !== 'hidden', paused: v.paused, t: v.currentTime,
              head: tl.state.playhead}; })()""")
    assert st["shown"] and not st["paused"], st
    assert abs(st["t"] - (1.0 + (st["head"] - 1.0))) < 0.3, st
    assert is_blue(program_rgb(page, "v2-playing"))
    page.wait_for_function("tl.state.playhead > 3.3", timeout=8000)
    assert not v2_state(page)["shown"]
    page.evaluate("pauseCut()")
    assert v2_state(page)["paused"]


# ------------------------------------------------------------------ A1 offset

A1 = """(() => { const a = document.querySelector('#pvA1'), v = liveVideo();
  return {t: a.currentTime, paused: a.paused, src: a.dataset.src, vol: a.volume,
          vt: v.currentTime, vmuted: v.muted, vsrc: v.dataset.src}; })()"""


def test_an_unlinked_shot_sounds_at_its_offset_on_a_seek(page):
    seek(page, 6.5)                    # 1.5 s into shot 2 → its sound is 0.5 s in
    page.wait_for_function("Math.abs(document.querySelector('#pvA1').currentTime - 0.5) < 0.02",
                           timeout=4000)
    a = page.evaluate(A1)
    assert "CLIP_B" in a["src"] and "CLIP_B" in a["vsrc"]
    assert abs((a["vt"] - a["t"]) - 1.0) < 0.02, a        # offset by exactly a_off
    assert a["vmuted"], "the picture's own sound is not its A1 any more"
    assert a["vol"] > 0.99
    # the gap before the moved sound is silence: no A1 element playing, picture muted
    seek(page, 5.5)
    a = page.evaluate(A1)
    assert a["paused"] and a["vmuted"], a
    # shot 1 is linked and in sync: its picture carries its sound
    seek(page, 2.0)
    assert not page.evaluate(A1)["vmuted"]


def test_the_offset_sound_plays_in_step_and_follows_a1s_header(page):
    seek(page, 5.2)
    page.evaluate("playFrom(1)")
    page.wait_for_function("tl.state.playhead > 6.6", timeout=8000)
    a = page.evaluate(A1)
    assert not a["paused"], a
    assert abs((a["vt"] - a["t"]) - 1.0) < 0.15, a
    page.evaluate("tl.setTrack('A1', 'gain_db', -6)")
    page.wait_for_timeout(100)
    assert abs(page.evaluate(A1)["vol"] - 10 ** (-6 / 20)) < 0.01
    page.evaluate("tl.setTrack('A1', 'mute', true)")
    page.wait_for_timeout(100)
    assert page.evaluate(A1)["vol"] == 0
    page.evaluate("pauseCut()")
    assert page.evaluate(A1)["paused"]
