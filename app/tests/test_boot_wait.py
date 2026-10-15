"""Verdict 7 blocker 3 — the board never waits on a measurement to show the cut.

After a background proxy landed, a reload showed an empty Timeline and Program for
27-35 s with no word on the screen: boot awaited GET /api/junk, and /api/junk measured the
new 1280 proxy's colour inside the GET. Now boot mounts the timeline and the Program
first and the junk marks paint when they arrive; /api/junk never measures — it lists the
clip as `colour: "pending"`, a background queue (niced, paused while the Program plays)
measures it, and the bin says "checking colour… N clips" until it lands.

The fixture is "a proxy has just landed": the synthetic junk bin with its proxies built
and CLIP_DARK's colour file gone, and `measure_colour` patched to take 10 s. The page
must show its cut (the `#tl .blk` blocks and a Program frame) in <= BOOT_CUT_S, and the
junk marks after. `$ROUGHCUT_BOOT_JSON`, when set, gets the measured numbers; tools/gate.py
reads it ("board shows its cut N s after a proxy lands"). Skipped without playwright.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api",
                                     reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

BOOT_CUT_S = 2.0          # the cut on screen this soon after the navigation starts
MEASURE_S = 10.0          # what the patched colour measurement takes
SLOW = "CLIP_DARK.MP4"    # its proxy "just landed": no colour file yet


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _write_boot_json(rec: dict) -> None:
    out = os.environ.get("ROUGHCUT_BOOT_JSON")
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(rec, indent=1), encoding="utf-8")


@pytest.fixture
def landed(junk_project, monkeypatch):
    """The live server on the junk bin, right after CLIP_DARK's proxy landed: the proxy
    is there, its colour file is not, and measuring it takes MEASURE_S."""
    import uvicorn
    import server

    jp = junk_project
    jp["edl"].write_text(jp["seed"], encoding="utf-8")
    server.configure(jp["edl"], jp["footage"], jp["sidecars"], jp["work"],
                     proxies=False, assets=jp["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in jp["stems"]])
    real = server.measure_colour
    calls: list[tuple[str, float]] = []

    def slow_measure(clip, force=False):
        calls.append((clip, time.time()))
        time.sleep(MEASURE_S)
        return real(clip, force)

    monkeypatch.setattr(server, "measure_colour", slow_measure)
    if hasattr(server, "COLOUR_Q"):           # (an older server: the test is red, not an error)
        server.COLOUR_Q["failed"].clear()
    server.colour_file(SLOW).unlink(missing_ok=True)
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
    yield {"url": f"http://127.0.0.1:{port}", "calls": calls, "server": server}
    # let the queue finish (its colour file is the next test's), then put it back
    deadline = time.time() + MEASURE_S + 20
    while time.time() < deadline and ((getattr(server, "COLOUR_Q", {}) or {}).get("thread") is not None
                                      or not server.colour_file(SLOW).exists()):
        time.sleep(0.2)
    srv.should_exit = True
    thread.join(timeout=10)
    monkeypatch.undo()
    if not server.colour_file(SLOW).exists():
        real(SLOW)
    jp["edl"].write_text(jp["seed"], encoding="utf-8")


# Recorded in the page from its first script: when the cut's blocks appear, when the
# Program has a frame, what the junk wait line said and when the marks landed.
WATCH = """
(() => {
  const w = window.__boot = { blk: null, frame: null, junk: null, waitSaid: [] };
  const look = () => {
    const t = performance.now();
    if (w.blk === null && document.querySelector('#tl .blk')) w.blk = t;
    try {
      // eslint-disable-next-line no-undef
      // the Program's frame: its still at the playhead (at rest), or a live buffer's picture
      const v = typeof liveVideo === 'function' ? liveVideo() : null;
      const still = document.querySelector('#monPoster');
      const shown = (still && !still.hidden && still.complete && still.naturalWidth > 0)
        || (v && v.dataset.src && v.readyState >= 2);
      if (w.frame === null && shown) w.frame = t;
    } catch (e) { /* app.js not there yet */ }
    const jw = document.querySelector('#junkWait');
    if (jw && !jw.hidden && jw.textContent && !w.waitSaid.includes(jw.textContent)) w.waitSaid.push(jw.textContent);
    try {
      // eslint-disable-next-line no-undef
      if (w.junk === null && typeof junkState === 'function' && junkState('CLIP_DARK.MP4') === 'proposed') w.junk = t;
    } catch (e) { /* not yet */ }
    if (w.junk === null || w.frame === null || w.blk === null) requestAnimationFrame(look);
  };
  requestAnimationFrame(look);
  setInterval(look, 50);              // a hidden tab still records
})();
"""


def test_the_cut_shows_before_the_colour_is_measured(landed):
    server = landed["server"]
    url = landed["url"]
    rec: dict = {"test": "app/tests/test_boot_wait.py::test_the_cut_shows_before_the_colour_is_measured",
                 "outcome": "failed", "limit_s": BOOT_CUT_S, "measure_patch_s": MEASURE_S,
                 "load": round(os.getloadavg()[0], 2), "cores": os.cpu_count()}
    try:
        # the server side alone: the GET answers at once and says what it waits on
        t0 = time.time()
        import urllib.request
        with urllib.request.urlopen(f"{url}/api/junk", timeout=30) as r:
            body = json.loads(r.read())
        rec["api_junk_s"] = round(time.time() - t0, 3)
        row = next(x for x in body["clips"] if x["clip"] == SLOW)
        assert rec["api_junk_s"] < 1.0, f"GET /api/junk took {rec['api_junk_s']} s: it measured"
        assert row["colour"] == "pending" and body["pending"] >= 1, row
        assert row["state"] == "clean"          # judged on its length until measured

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            pg = browser.new_page(viewport={"width": 1440, "height": 900})
            pg.add_init_script(WATCH)
            pg.goto(url)
            pg.wait_for_function("window.__boot && window.__boot.blk !== null"
                                 " && window.__boot.frame !== null", timeout=30000)
            b = pg.evaluate("window.__boot")
            rec["cut_s"] = round(max(b["blk"], b["frame"]) / 1000.0, 3)
            rec["blocks_s"] = round(b["blk"] / 1000.0, 3)
            rec["frame_s"] = round(b["frame"] / 1000.0, 3)
            nblk = pg.evaluate("document.querySelectorAll('#tl .blk').length")
            assert nblk >= 1
            assert pg.evaluate("tl.state ? tl.state.segs.length : segs.length") >= 1
            # the bin says what it waits on, in words, where the marks will appear
            pg.evaluate("dock.open('bin')")
            pg.wait_for_function("(() => { const e = document.querySelector('#junkWait');"
                                 " return e && !e.hidden && /checking colour… 1 clip/.test(e.textContent); })()",
                                 timeout=5000)
            assert pg.is_visible("#junkWait")
            # the marks arrive after the measurement, without a reload
            pg.wait_for_function("window.__boot.junk !== null", timeout=(MEASURE_S + 20) * 1000)
            b = pg.evaluate("window.__boot")
            rec["junk_s"] = round(b["junk"] / 1000.0, 3)
            rec["wait_said"] = b["waitSaid"]
            pg.wait_for_selector(f'#library .junkcard[data-clip="{SLOW}"]', timeout=5000)
            pg.wait_for_function("document.querySelector('#junkWait').hidden", timeout=5000)
            browser.close()
        assert any(c == SLOW for c, _ in landed["calls"]), "the queue never measured it"
        assert rec["junk_s"] > rec["cut_s"], rec
        assert rec["cut_s"] <= BOOT_CUT_S, (
            f"the cut took {rec['cut_s']} s to show (blocks {rec['blocks_s']} s, "
            f"Program frame {rec['frame_s']} s) with the colour measurement at {MEASURE_S} s")
        assert server.colour_file(SLOW).exists()
        rec["outcome"] = "passed"
    finally:
        _write_boot_json(rec)
    print(f"\nboot: cut {rec.get('cut_s')} s (blocks {rec.get('blocks_s')} s, frame "
          f"{rec.get('frame_s')} s), junk marks {rec.get('junk_s')} s, GET /api/junk "
          f"{rec.get('api_junk_s')} s, load {rec['load']}")


def test_api_junk_never_measures(landed):
    """GET /api/junk with a slow measurement pending answers from the files on disk,
    and the queue (not the GET) measures — once, however often the board asks."""
    import urllib.request
    url = landed["url"]
    times = []
    for _ in range(3):
        t0 = time.time()
        with urllib.request.urlopen(f"{url}/api/junk", timeout=30) as r:
            body = json.loads(r.read())
        times.append(time.time() - t0)
    assert max(times) < 1.0, times
    assert body["pending"] == 1
    deadline = time.time() + MEASURE_S + 15
    while time.time() < deadline and body["pending"]:
        time.sleep(0.5)
        with urllib.request.urlopen(f"{url}/api/junk", timeout=30) as r:
            body = json.loads(r.read())
    assert body["pending"] == 0
    row = next(x for x in body["clips"] if x["clip"] == SLOW)
    assert row["colour"] == "measured" and row["state"] == "proposed", row
    assert [c for c, _ in landed["calls"]].count(SLOW) == 1, landed["calls"]


def test_the_colour_queue_runs_in_a_background_scope(landed):
    """The queue's measurement is background work: its scope yields to playback (niced,
    SIGSTOPped while the Program plays) and dies with the server."""
    server = landed["server"]
    seen = {}
    real = server.measure_colour

    def spy(clip, force=False):
        sc = server.procscope.current()
        seen[clip] = (sc is not None, bool(sc and sc.background))
        return real(clip, force)

    server.measure_colour = spy
    try:
        server.queue_colour([SLOW])
        deadline = time.time() + MEASURE_S + 15
        while time.time() < deadline and SLOW not in seen:
            time.sleep(0.1)
    finally:
        server.measure_colour = real          # the fixture's monkeypatch restores the rest
    assert seen.get(SLOW) == (True, True), seen


def test_the_gate_reads_the_record(tmp_path):
    """tools/gate.py's boot line: green at or under the limit from a passed test, red over
    it or from a failed one, and it says the number either way."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    import gate

    rec = tmp_path / "boot.json"
    env = {"ROUGHCUT_BOOT_JSON": str(rec)}
    base = {"outcome": "passed", "cut_s": 0.75, "blocks_s": 0.32, "frame_s": 0.75,
            "junk_s": 10.4, "measure_patch_s": 10.0, "api_junk_s": 0.07, "load": 15.6, "cores": 6}
    rec.write_text(json.dumps(base))
    green, line = gate.boot_check(tmp_path, tmp_path, env)
    assert green and line.startswith("board shows its cut 0.75 s after a proxy lands"), line
    rec.write_text(json.dumps({**base, "cut_s": 11.9, "outcome": "failed"}))
    green, line = gate.boot_check(tmp_path, tmp_path, env)
    assert not green and "11.90 s" in line and "FAILED" in line, line
    rec.write_text(json.dumps({**base, "cut_s": None, "outcome": "failed"}))
    green, line = gate.boot_check(tmp_path, tmp_path, env)
    assert not green and "not measured" in line, line
