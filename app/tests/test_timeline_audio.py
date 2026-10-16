"""Sound on the timeline and the Program, and the frame grid (redesign wave 3, lane
TLAUDIO; verdict 3 punch 6 and the timeline half of 10, the journey's VFR finding).

  * /api/peaks: a clip's real waveform, decoded once by ffmpeg and cached under --work.
  * A1 draws it: a fixture clip that is 2 s of tone then 2 s of silence (ffmpeg `sine`
    + `anullsrc`) gives a path that is tall over the tone and flat over the silence.
  * The meters beside the Program read the mix through WebAudio: above -30 dBFS during
    the tone, down at the floor during the silence, and nothing while A1 is muted.
  * Edit points land on the sequence's frames: a drag-trim, a razor cut, a roll and a
    slip at 29.97 (30000/1001) commit on frame boundaries, and the server keeps them.
  * The V1 filmstrip's tiles cover >= 95 % of every block, before and after a trim.
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

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

TONE_S, SILENCE_S = 2.0, 2.0
SHOTS = os.environ.get("ROUGHCUT_TLAUDIO_SHOTS")     # a dir: save screenshots there


def _clip(path: Path, audio: str) -> None:
    dur = TONE_S + SILENCE_S
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=24:duration={dur}",
         "-filter_complex", audio, "-map", "0:v", "-map", "[a]",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-ar", "44100", "-t", str(dur), str(path)],
        check=True, capture_output=True)


def _sidecar(stem: str) -> dict:
    return {"clip": f"{stem}.MP4", "duration_s": TONE_S + SILENCE_S, "sample_rate": 16000,
            "frame_hz": 10, "tracks": {"onset": [0.2] * 40, "vad": [0] * 40},
            "transcript": [{"start": 0.2, "end": 1.6, "text": "a tone", "no_speech_prob": 0.1,
                            "words": [{"w": "a", "t": 0.2, "p": 0.9}, {"w": "tone", "t": 0.8, "p": 0.9}]}],
            "events": [], "summary": {"speech_fraction": 0.3, "audio_usable": True, "n_words": 2},
            "candidates": []}


@pytest.fixture(scope="module")
def tproject(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("tlaudio")
    footage, sidecars, work = root / "footage", root / "sidecars", root / "work"
    for d in (footage, sidecars, work):
        d.mkdir()
    # TONE: 2 s of 1 kHz at -6 dBFS (sine is 1/8 scale; x4), then 2 s of digital silence
    _clip(footage / "TONE.MP4",
          f"sine=frequency=1000:sample_rate=44100:duration={TONE_S},volume=4[t];"
          f"anullsrc=r=44100:cl=mono,atrim=0:{SILENCE_S}[s];[t][s]concat=n=2:v=0:a=1[a]")
    _clip(footage / "HUM.MP4", "sine=frequency=440:sample_rate=44100:duration=4[a]")
    for stem in ("TONE", "HUM"):
        (sidecars / f"{stem}.audio.json").write_text(json.dumps(_sidecar(stem)), encoding="utf-8")
    edl = root / "edl.json"
    return {"root": root, "footage": footage, "sidecars": sidecars, "work": work, "edl": edl,
            "stems": ["TONE", "HUM"]}


SEED = {"variant": "T", "title": "audio", "orient": "none", "story": "", "target_s": [2, 20],
        "segments": [{"clip": "TONE.MP4", "in": 0.0, "out": 4.0, "why": "tone then silence"},
                     {"clip": "HUM.MP4", "in": 0.5, "out": 3.0, "why": "hum"}]}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server_url(tproject):
    import uvicorn
    import server

    tproject["edl"].write_text(json.dumps(SEED), encoding="utf-8")
    server.configure(tproject["edl"], tproject["footage"], tproject["sidecars"],
                     tproject["work"], proxies=False)
    server.ensure_proxies([f"{s}.MP4" for s in tproject["stems"]])
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
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
def page(server_url, tproject):
    tproject["edl"].write_text(json.dumps(SEED), encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(server_url)
        pg.add_style_tag(content="#progress { display: none !important; }")
        pg.wait_for_selector("#tl .blk .tl-h.in")
        pg.evaluate("Promise.all(segs.map(s => tl.snapsFor(s.clip)))")
        yield pg
        assert errors == [], errors
        browser.close()


def _shot(pg, name: str, selector: str | None = None) -> None:
    if not SHOTS:
        return
    Path(SHOTS).mkdir(parents=True, exist_ok=True)
    target = pg.locator(selector) if selector else pg
    target.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


# ------------------------------------------------------------------ the peaks endpoint

def test_peaks_are_the_clips_real_sound_and_are_cached(server_url, tproject):
    import httpx
    import server

    # made by the server's worker, never inside the request (wave 11 CHEAP): 202
    # {pending} at once until they exist, then the clip's pairs
    deadline = time.time() + 60
    while True:
        r = httpx.get(f"{server_url}/api/peaks/TONE?pps=50", timeout=60)
        if r.status_code != 202 or time.time() > deadline:
            break
        assert r.json()["pending"] is True and "no-store" in r.headers["cache-control"]
        time.sleep(0.1)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["pps"] == 50 and p["duration"] == pytest.approx(4.0, abs=0.06)
    lo, hi = p["min"], p["max"]
    assert len(lo) == len(hi) == pytest.approx(200, abs=3)
    tone, silence = range(5, 95), range(105, len(hi))
    # -6 dBFS is half scale: 63 of 127
    assert min(hi[i] for i in tone) >= 50 and max(lo[i] for i in tone) <= -50
    assert max(hi[i] for i in silence) <= 1 and min(lo[i] for i in silence) >= -1
    cached = list((tproject["work"] / "audio" / tproject["footage"].name).glob("TONE.*.peaks"))
    assert len(cached) == 1
    mtime = cached[0].stat().st_mtime_ns
    # another rate, and spp, come from the same cache (no second decode)
    q = httpx.get(f"{server_url}/api/peaks/TONE.MP4?spp=160", timeout=30).json()
    assert q["pps"] == 100 and len(q["max"]) == pytest.approx(400, abs=4)
    assert cached[0].stat().st_mtime_ns == mtime
    assert httpx.get(f"{server_url}/api/peaks/NOPE", timeout=10).status_code == 404
    assert httpx.get(f"{server_url}/api/peaks/..%2Fedl", timeout=10).status_code == 404
    assert server.PEAKS_BASE == 100


# ------------------------------------------------------------------ A1 draws it

def _wave_ys(pg, i: int) -> dict:
    return pg.evaluate("""(i) => {
        const b = document.querySelectorAll('#tl .tl-track[data-track=A1] .ablk')[i];
        const p = b && b.querySelector('.wave svg.wf path');
        if (!p) return null;
        const nums = p.getAttribute('d').match(/-?[\\d.]+/g).map(Number);
        const pts = [];
        for (let k = 0; k + 1 < nums.length; k += 2) pts.push([nums[k], nums[k + 1]]);
        const w = parseFloat(b.style.width);
        return {w, pts, peaks: b.dataset.peaks};
    }""", i)


def test_a1_draws_the_clips_waveform_tall_over_the_tone_flat_over_the_silence(page):
    page.wait_for_function(
        "document.querySelector('#tl .tl-track[data-track=A1] .ablk .wave svg.wf path')", timeout=30000)
    got = _wave_ys(page, 0)
    assert got and got["peaks"] == "ok"
    w, pts = got["w"], got["pts"]
    tone = [y for x, y in pts if 0.05 * w < x < 0.45 * w]
    quiet = [y for x, y in pts if 0.55 * w < x < 0.95 * w]
    # not flat: the tone reaches ~half the height either way, the silence stays on the line
    assert min(tone) < 30 and max(tone) > 70, (min(tone), max(tone))
    assert all(49 <= y <= 51 for y in quiet), (min(quiet), max(quiet))
    # a trim redraws it for the new range: the tone gone, a flat line left
    page.evaluate("tl.setRange(segs[0].id, 2.2, null)")
    page.wait_for_function("""() => { const p = document.querySelector('#tl .tl-track[data-track=A1] .ablk .wave svg.wf path');
        return p && p.getAttribute('d').match(/-?[\\d.]+/g).map(Number).filter((v, k) => k % 2).every(y => y >= 49 && y <= 51); }""",
                           timeout=10000)
    page.keyboard.press("Control+z")
    page.wait_for_function("""() => { const p = document.querySelector('#tl .tl-track[data-track=A1] .ablk .wave svg.wf path');
        return p && p.getAttribute('d').match(/-?[\\d.]+/g).map(Number).filter((v, k) => k % 2).some(y => y < 30); }""",
                           timeout=10000)
    _shot(page, "a1-waveforms", "#pTimeline")


# ------------------------------------------------------------------ the meters

def _play_and_sample(page, seconds: float) -> list[dict]:
    page.evaluate("tl.seek(0)")
    page.locator("#playCut").click()
    return page.evaluate("""(ms) => new Promise((done) => {
        const out = [], t0 = performance.now();
        const iv = setInterval(() => {
          const v = [...document.querySelectorAll('#player video')].find(x => !x.paused);
          const r = rcMeters.read();
          out.push({t: v ? v.currentTime : null, src: v ? (v.dataset.src || v.currentSrc) : '',
                    now: Math.max(...r.now), shown: Math.max(...r.shown)});
          if (performance.now() - t0 > ms) { clearInterval(iv); done(out); }
        }, 50);
      })""", seconds * 1000)


def test_the_meters_rise_over_the_tone_and_fall_in_the_silence(page):
    assert page.locator("#meters canvas").count() == 1
    rows = _play_and_sample(page, 3.6)
    page.locator("#playCut").click()                       # stop
    assert page.evaluate("rcMeters.sources()") >= 1, page.evaluate("rcMeters.state()")
    on_tone = [r["now"] for r in rows if r["t"] is not None and 0.3 < r["t"] < 1.7 and "TONE" in r["src"]]
    in_silence = [r["now"] for r in rows if r["t"] is not None and 2.4 < r["t"] < 3.4 and "TONE" in r["src"]]
    assert on_tone and in_silence, rows[-10:]
    assert max(on_tone) > -30, on_tone
    assert min(on_tone) > -30, on_tone
    assert max(in_silence) < -60, in_silence
    # the meters stay in the board's budget: one canvas, no words, no controls
    assert page.evaluate("document.querySelector('#meters').innerText.trim()") == ""
    assert page.locator("#meters button, #meters input").count() == 0


def test_the_meters_follow_a1s_mute(page):
    page.evaluate("tl.setTrack('A1', 'mute', true)")
    rows = _play_and_sample(page, 1.6)
    page.locator("#playCut").click()
    on_tone = [r["now"] for r in rows if r["t"] is not None and 0.3 < r["t"] < 1.5]
    assert on_tone and max(on_tone) < -60, on_tone
    page.evaluate("tl.setTrack('A1', 'mute', false)")
    rows = _play_and_sample(page, 1.6)
    on_tone = [r["now"] for r in rows if r["t"] is not None and 0.3 < r["t"] < 1.5]
    assert on_tone and max(on_tone) > -30, on_tone
    _shot(page, "meters-program", "#pProgram")
    page.locator("#playCut").click()


def _tone_level(page, seconds: float = 1.6) -> float:
    rows = _play_and_sample(page, seconds)
    page.locator("#playCut").click()
    on_tone = [r["now"] for r in rows if r["t"] is not None and 0.4 < r["t"] < 1.5 and "TONE" in r["src"]]
    assert on_tone, rows[-6:]
    return max(on_tone)


def test_a1_gain_is_one_owner_and_goes_above_0_db(page):
    """Lane PROGRAM's request: the element's level has one owner, rcAudio (A1's mute /
    solo / gain_db as its 'track' factor; Effect Controls' Volume as 'clip'), a gain node
    once the element is in the graph — so +6 dB on A1 is heard (and metered) 6 dB up."""
    base = _tone_level(page)
    assert base > -30
    page.evaluate("tl.setTrack('A1', 'gain_db', -12)")
    assert page.evaluate("rcAudio.levelOf(player.vids[0])") == pytest.approx(10 ** (-12 / 20), rel=1e-3)
    down = _tone_level(page)
    page.evaluate("tl.setTrack('A1', 'gain_db', 6)")
    assert page.evaluate("rcAudio.levelOf(player.vids[0])") == pytest.approx(10 ** (6 / 20), rel=1e-3)
    up = _tone_level(page)
    assert down == pytest.approx(base - 12, abs=2), (base, down)
    assert up == pytest.approx(base + 6, abs=2), (base, up)
    # in the graph the element's own volume stays at 1: the gain node carries the level
    assert page.evaluate("player.vids.filter(v => rcMeters.has(v)).every(v => v.volume === 1)")
    page.evaluate("tl.setTrack('A1', 'gain_db', 0)")


def test_an_unlinked_moved_sound_on_pva1_is_metered(page):
    """Lane PROGRAM's #pvA1 plays a moved A1 sound; it is in the mix like pv0/pv1."""
    page.evaluate("(() => { const id = segs[0].id; tl.setLink(id, false); tl.setAudioOffset(id, 0.5); })()")
    page.evaluate("tl.seek(0)")
    page.locator("#playCut").click()
    rows = page.evaluate("""() => new Promise((done) => {
        const out = [], t0 = performance.now(), a = document.querySelector('#pvA1');
        const iv = setInterval(() => {
          out.push({ph: tl.state.playhead, a1: !!a && !a.paused, has: !!a && rcMeters.has(a),
                    now: Math.max(...rcMeters.read().now)});
          if (performance.now() - t0 > 2200) { clearInterval(iv); done(out); }
        }, 50);
      })""")
    page.locator("#playCut").click()
    moved = [r for r in rows if 1.0 < r["ph"] < 2.2 and r["a1"]]
    assert moved, rows[-8:]
    assert all(r["has"] for r in moved)
    assert max(r["now"] for r in moved) > -30, moved


def test_the_meters_sit_beside_or_under_the_picture_without_shrinking_it(page):
    m = page.evaluate("""() => { const pic = document.querySelector('#pProgram .screen').getBoundingClientRect(),
        met = document.querySelector('#meters').getBoundingClientRect();
        return {mode: rcMeters.mode(), pw: pic.width, ph: pic.height, mw: met.width, mh: met.height,
                overlap: !(met.right <= pic.left || met.left >= pic.right || met.bottom <= pic.top || met.top >= pic.bottom)}; }""")
    assert not m["overlap"], m
    assert (m["mode"] == "h" and m["mh"] >= 16) or (m["mode"] == "v" and m["mw"] >= 24), m
    # a wide Program panel puts them beside the picture, vertical
    page.set_viewport_size({"width": 2400, "height": 900})
    page.wait_for_function("rcMeters.mode() === 'v'", timeout=5000)
    m = page.evaluate("""() => { const pic = document.querySelector('#pProgram .screen').getBoundingClientRect(),
        met = document.querySelector('#meters').getBoundingClientRect();
        return {mw: met.width, mh: met.height, left: met.left, picRight: pic.right}; }""")
    assert m["mw"] >= 24 and m["mh"] > 100 and m["left"] >= m["picRight"] - 1, m


# ------------------------------------------------------------------ the frame grid

def _on_frames(t: float, fps: float = 30000 / 1001) -> bool:
    return abs(t * fps - round(t * fps)) < 0.01


def test_a_drag_trim_lands_on_a_frame_boundary_and_the_server_keeps_it(page, tproject):
    page.evaluate("tl.setFps(29.97)")
    assert page.evaluate("tl.gridRate()") == pytest.approx(30000 / 1001)
    page.evaluate("tl.trim.setMagnet(false)")    # the magnet off: a raw drag, no snap
    # the seed's 4.0 s and 0.5 s are not 29.97 frames (119.88, 14.99): a trim of one edge
    # never moves the other, so the seed is put on frames first (a slip, both edges move)
    page.evaluate("tl.setRange(segs[0].id, 0.0001, 3.9999)")
    page.evaluate("tl.setRange(segs[1].id, 0.5001, 3.0001)")
    assert all(_on_frames(x) for x in page.evaluate("segs.flatMap(s => [s.in, s.out])"))
    in1 = page.evaluate("segs[1].in")
    page.evaluate("document.querySelector('#tl').scrollIntoView({block: 'end'})")
    z = page.evaluate("tl.state.zoom")
    h = page.locator("#tl .blk").nth(1).locator(".tl-h.out")
    box = h.bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x - 0.37 * z, y, steps=7)    # 0.37 s is 11.09 frames: never a frame
    page.mouse.up()
    out = page.evaluate("segs[1].out")
    assert out != 3.0 and out == pytest.approx(3.0 - 0.37, abs=0.05)
    assert page.evaluate("segs[1].in") == in1, "an out-trim never moves the in"
    assert _on_frames(out), out
    # every cut in the film is on a frame
    cuts = page.evaluate("(() => { let t = 0; return segs.map(s => (t += tl.dur(s))); })()")
    assert all(_on_frames(c) for c in cuts), cuts
    page.wait_for_function("document.querySelector('#saveState').textContent.startsWith('saved')", timeout=8000)
    disk = json.loads(tproject["edl"].read_text(encoding="utf-8"))["segments"]
    assert disk[1]["out"] == pytest.approx(out, abs=1e-4), disk
    assert all(_on_frames(s[k]) for s in disk for k in ("in", "out")), disk


def test_razor_roll_slip_and_insert_commit_on_frames(page):
    page.evaluate("tl.setFps(29.97)")
    page.evaluate("tl.setRange(segs[0].id, 0.0001, 3.9999)")     # the seed onto frames
    page.evaluate("tl.setRange(segs[1].id, 0.5001, 3.0001)")
    first = page.evaluate("segs[0].id")
    second = page.evaluate(f"tl.split('{first}', 1.2345)")
    assert second
    assert _on_frames(page.evaluate("segs[0].out")) and _on_frames(page.evaluate("segs[1].in"))
    # a nudge, a slip and a roll through the API the gestures use
    page.evaluate("tl.setRange(segs[1].id, null, segs[1].out - 0.123)")
    assert _on_frames(page.evaluate("segs[1].out"))       # its in is the razor's frame
    # an in-trim keeps the out exactly (even one from before the grid) and lands on a frame
    before_out = page.evaluate("segs[2].out")
    page.evaluate("tl.setRange(segs[2].id, segs[2].in + 0.0931, null)")
    assert page.evaluate("segs[2].out") == before_out
    assert _on_frames(page.evaluate("segs[2].in"))
    # a slip moves both onto frames
    page.evaluate("tl.setRange(segs[2].id, segs[2].in + 0.0712, segs[2].out + 0.0712)")
    assert _on_frames(page.evaluate("segs[2].in")) and _on_frames(page.evaluate("segs[2].out"))
    # an insert at an off-grid time with an off-grid range
    page.evaluate("tl.insertAt({clip: 'HUM.MP4', in: 0.111, out: 1.019, why: 'x'}, 0.777)")
    for s in page.evaluate("segs.map(s => [s.in, s.out])"):
        assert _on_frames(s[0]) and _on_frames(s[1]), s
    cuts = page.evaluate("(() => { let t = 0; return segs.map(s => (t += tl.dur(s))); })()")
    assert all(_on_frames(c) for c in cuts), cuts
    # a retimed shot: its length in the film is whole frames
    page.evaluate("tl.setSpeed(segs[0].id, 0.7)")
    assert _on_frames(page.evaluate("tl.dur(segs[0])"))


# ------------------------------------------------------------------ the filmstrip

def _coverage(page) -> list[dict]:
    return page.evaluate("""() => [...document.querySelectorAll('#tl .tl-lane[data-lane=V1] .blk')].map(b => {
        const r = b.getBoundingClientRect();
        const spans = [...b.querySelectorAll('.strip img')].filter(i => i.getAttribute('src'))
          .map(i => i.getBoundingClientRect()).filter(q => q.width > 0)
          .map(q => [Math.max(r.left, q.left), Math.min(r.right, q.right)]).filter(([a, z]) => z > a)
          .sort((p, q) => p[0] - q[0]);
        let cov = 0, end = -1e9;
        for (const [a, z] of spans) { const s = Math.max(a, end); if (z > s) { cov += z - s; end = z; } }
        return {id: b.dataset.id, w: r.width, cov: cov / r.width, tiles: spans.length};
    })""")


def test_filmstrip_tiles_cover_every_v1_block_before_and_after_a_trim(page):
    page.wait_for_timeout(600)                   # the strips take their frames once settled
    for b in _coverage(page):
        assert b["cov"] >= 0.95, b
    page.evaluate("tl.setRange(segs[0].id, null, 2.6)")     # a trim shortens it
    page.evaluate("tl.zoomTo(tl.state.zoom * 1.7)")         # and a zoom widens everything
    page.wait_for_timeout(900)
    cov = _coverage(page)
    assert len(cov) == 2
    for b in cov:
        assert b["cov"] >= 0.95 and b["tiles"] >= 2, b
    # the tiles are frames of the clip at their own times, not the head repeated
    srcs = page.evaluate("[...document.querySelectorAll('#tl .blk')[0].querySelectorAll('.strip img')].map(i => i.getAttribute('src'))")
    ts = [float(re.search(r"t=([\d.]+)", s).group(1)) for s in srcs if s]
    assert len(set(ts)) >= 2, ts
    _shot(page, "v1-filmstrip", "#pTimeline")
