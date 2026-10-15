"""The app's own background work yields to playback (redesign wave 8, lane YIELD;
verdict 6 punch 3, SAVESAFE's and RANGEPROXY's requests).

Verdict 6: the Program's holes at a cut grew with the box's load, and much of that load
was the app's own: proxy, preview, range-proxy, close-look and poster-walk ffmpegs ran
beside the player. Now:

  (1) every background encode (the index's scope, the media walk's) runs at nice 19, and
      while a board's Program plays (POST /api/playing, a heartbeat every 2 s) the
      server SIGSTOPs those ffmpegs (state T) and SIGCONTs them on pause, or 5 s after
      the last beat (a closed tab can never leave them stopped). The export, and a
      background build somebody is waiting on, keep running;
  (2) the board says when it plays: `player.playing` (or the Source monitor) reaches the
      server within a beat, and a pause or a closed page lets the work run again;
  (3) encodes die with the server: an ffmpeg in a scope carries PR_SET_PDEATHSIG (a
      SIGKILLed parent takes it down), and the shutdown hook kills every scope's groups;
  (4) the filmstrip tiles load through a queue (two at a time, in view only), so with a
      storm of slow poster builds pending a credentialed GET /api/firstpass still
      answers in < 300 ms.

Mocked so nothing races a fast encode: every "encode" is a 60 s real-time ffmpeg.

    uv run --with pytest --with fastapi --with uvicorn --with httpx --with numpy \\
        --with playwright pytest app/tests/test_yield.py -q
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import httpx
import pytest

playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SLOW = ["ffmpeg", "-v", "error", "-nostdin", "-re", "-f", "lavfi",
        "-i", "testsrc2=size=160x90:rate=10", "-t", "60", "-f", "null", "-"]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ffmpeg_pids(parent: int | None = None) -> list[int]:
    r = subprocess.run(["pgrep", "-P", str(parent or os.getpid()), "ffmpeg"],
                       capture_output=True, text=True)
    return [int(x) for x in r.stdout.split()]


def state(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            return fh.read().rsplit(")", 1)[-1].split()[0]
    except OSError:
        return "gone"


def until(fn, timeout: float, every: float = 0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return fn()


@pytest.fixture(scope="module")
def bin_(project, tmp_path_factory):
    root = tmp_path_factory.mktemp("yieldbin")
    footage, sidecars, work = root / "footage", root / "sidecars", root / "work"
    for d in (footage, sidecars, work):
        d.mkdir()
    for stem in project["stems"]:
        shutil.copy(Path(project["footage"]) / f"{stem}.MP4", footage / f"{stem}.MP4")
        shutil.copy(Path(project["sidecars"]) / f"{stem}.audio.json",
                    sidecars / f"{stem}.audio.json")
    edl = root / "edl.json"
    edl.write_text(json.dumps({
        "variant": "T", "title": "yield", "orient": "none", "story": "",
        "segments": [{"clip": "CLIP_A.MP4", "in": 0.5, "out": 4.5, "why": "a"},
                     {"clip": "CLIP_B.MP4", "in": 0.5, "out": 4.5, "why": "b"}]}),
        encoding="utf-8")
    return {"footage": footage, "sidecars": sidecars, "work": work, "edl": edl,
            "assets": project["assets"], "stems": project["stems"]}


@pytest.fixture(scope="module")
def live_server(bin_):
    import uvicorn
    import server

    server.configure(bin_["edl"], bin_["footage"], bin_["sidecars"], bin_["work"],
                     proxies=False, visual=bin_["work"] / "visual", assets=bin_["assets"])
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


def _play(base: str, on: bool, tab: str = "test") -> dict:
    r = httpx.post(base + "/api/playing", json={"on": on, "tab": tab}, timeout=10)
    assert r.status_code == 200, r.text
    return r.json()


def _cleanup(server) -> None:
    with server.PLAYING["lock"]:
        server.PLAYING["tabs"].clear()
    server.yield_tick()
    server.stop_media()
    for x in server.INDEXES.values():
        x["cancel"] = True
    for sc in list(server.INDEX_SCOPES.values()):
        sc.kill()
    for proc in list(server.RENDER_PROCS.values()):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    until(lambda: not ffmpeg_pids(), 10)


# ------------------------------------------------------------------------------ (1)

def test_background_encodes_are_niced_and_stop_while_the_program_plays(live_server, monkeypatch):
    import server
    base = live_server
    real_stage = server._index_stage

    def slow_stage(j, clip, stage):          # a 60 s "proxy encode" (or close look)
        if stage == "probe":
            return real_stage(j, clip, stage)
        r = subprocess.run(SLOW, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"{stage} killed ({r.returncode})")
        return 0.0

    def slow_preview(src, dest, orient):
        r = subprocess.run(SLOW, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"preview killed ({r.returncode})")
        dest.write_bytes(b"x")
        return "cpu"

    monkeypatch.setattr(server, "_index_stage", slow_stage)
    monkeypatch.setattr(server, "build_preview", slow_preview)
    assert not ffmpeg_pids(), "an ffmpeg was already running"
    render = threading.Thread(target=server._run_render, args=("yield-export", SLOW),
                              daemon=True)
    try:
        r = httpx.post(base + "/api/index", json={}, timeout=10)
        assert r.status_code == 200, r.text
        render.start()                       # the export: never paused, never niced
        assert until(lambda: "yield-export" in server.RENDER_PROCS, 10)
        export_pid = server.RENDER_PROCS["yield-export"].pid
        assert until(lambda: len(ffmpeg_pids()) >= 2, 20), ffmpeg_pids()
        jobs = [p for p in ffmpeg_pids() if p != export_pid]
        assert jobs, "the index started no encode"
        # nice 19, always (set before exec, so every ffmpeg thread has it)
        for p in jobs:
            assert os.getpriority(os.PRIO_PROCESS, p) == 19, p
        assert os.getpriority(os.PRIO_PROCESS, export_pid) < 19
        assert all(state(p) in "RS" for p in jobs)

        # play: the encodes stop within a second; the export runs on
        t0 = time.time()
        assert _play(base, True)["playing"] is True
        assert until(lambda: all(state(p) == "T" for p in ffmpeg_pids() if p != export_pid), 1.0), \
            {p: state(p) for p in ffmpeg_pids()}
        stopped_in = time.time() - t0
        assert state(export_pid) in "RS", state(export_pid)
        # an encode the index starts while it plays is stopped as well
        # (the stopped ones stay stopped with heartbeats: 7 s of beats every 1.5 s)
        for _ in range(5):
            time.sleep(1.5)
            _play(base, True)
            assert all(state(p) == "T" for p in ffmpeg_pids() if p != export_pid), \
                {p: state(p) for p in ffmpeg_pids()}
        assert state(export_pid) in "RS"

        # a preview a monitor asked for ("preparing preview…") is not background at all
        assert server.start_preview("CLIP_C.MP4") is not None
        asked = until(lambda: [p for p in ffmpeg_pids() if p not in jobs and p != export_pid
                               and os.getpriority(os.PRIO_PROCESS, p) == 0], 10)
        assert asked, "the asked-for preview did not start at normal priority"
        time.sleep(0.6)
        assert state(asked[0]) in "RS", state(asked[0])
        server.MEDIA["ask_scope"].kill()
        assert until(lambda: state(asked[0]) in ("gone", "Z"), 3)
        # a background build (the walk's) somebody is waiting on runs even while it plays
        threading.Thread(target=server.ensure_preview, args=("CLIP_B.MP4",), daemon=True).start()
        key = str(server.preview_path("CLIP_B"))
        prev = until(lambda: [p for p, tag in server.procscope.background_procs()
                              if tag == key], 10)
        assert prev, "the preview build did not start"
        pv = prev[0]
        assert until(lambda: state(pv) == "T", 1.0)
        with server.media_waiting(key):
            assert until(lambda: state(pv) in "RS", 1.0), state(pv)
            assert all(state(p) == "T" for p in jobs if state(p) != "gone")
        assert until(lambda: state(pv) == "T", 1.0)

        # pause: everything runs again within a second
        t1 = time.time()
        assert _play(base, False)["playing"] is False
        assert until(lambda: all(state(p) in "RS" for p in ffmpeg_pids()), 1.0), \
            {p: state(p) for p in ffmpeg_pids()}
        resumed_in = time.time() - t1

        # a board that stops beating (a closed tab): stopped now, running ~5 s later
        _play(base, True, tab="gone")
        assert until(lambda: all(state(p) == "T" for p in ffmpeg_pids() if p != export_pid), 1.0)
        time.sleep(3.0)
        assert all(state(p) == "T" for p in ffmpeg_pids() if p != export_pid), "lapsed too soon"
        t2 = time.time()
        assert until(lambda: all(state(p) in "RS" for p in ffmpeg_pids()), 4.0), \
            {p: state(p) for p in ffmpeg_pids()}
        lapse = time.time() - t2 + 3.0
        assert server.playing_now() is False
        print(f"\nyield: stopped in {stopped_in * 1000:.0f} ms, resumed in "
              f"{resumed_in * 1000:.0f} ms, heartbeat lapse resumed at {lapse:.1f} s")

        # Stop (the index's ×) still stops a paused job at once: SIGTERM then SIGCONT
        _play(base, True)
        assert until(lambda: all(state(p) == "T" for p in ffmpeg_pids() if p != export_pid), 1.0)
        t3 = time.time()
        httpx.post(base + "/api/index/cancel", timeout=10)
        server.stop_media()
        assert until(lambda: ffmpeg_pids() == [export_pid], 5.0), ffmpeg_pids()
        assert time.time() - t3 < 1.5, "a paused encode waited for the SIGKILL grace"
    finally:
        _cleanup(server)
        render.join(timeout=5)


# ------------------------------------------------------------------------------ (2)

def test_the_board_says_when_it_plays(live_server):
    import server
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        server.PLAYING["log"].clear()

        def posts() -> list[dict]:          # what the server was told (keepalive fetches
            return [{"t": t, "tab": tab, "on": on}           # are not seen by Playwright)
                    for t, tab, on in list(server.PLAYING["log"])]
        try:
            pg.goto(live_server)
            pg.wait_for_function("typeof player === 'object' && player.vids.length >= 2")
            assert server.playing_now() is False
            pg.evaluate("player.playing = true")
            assert until(server.playing_now, 1.5), "the server never heard the Program play"
            time.sleep(4.5)                  # longer than the lapse: the heartbeat holds it
            assert server.playing_now(), "the heartbeat did not hold"
            assert sum(1 for p in posts() if p["on"]) >= 3, posts()
            pg.evaluate("player.playing = false")
            assert until(lambda: not server.playing_now(), 2.0), "the pause never reached it"
            # a J/K/L burst does not flap the encodes: a 300 ms pause sends nothing
            pg.evaluate("player.playing = true")
            assert until(server.playing_now, 1.5)
            n = len(posts())
            pg.evaluate("player.playing = false; setTimeout(() => { player.playing = true; }, 300)")
            time.sleep(0.8)
            assert not [p for p in posts()[n:] if p["on"] is False], posts()[n:]
            # a page that goes away while playing lets the work run again
            pg.close()
            assert until(lambda: not server.playing_now(), 6.0), "a closed tab kept it playing"
            assert errors == [], errors
        finally:
            browser.close()
            with server.PLAYING["lock"]:
                server.PLAYING["tabs"].clear()


# ------------------------------------------------------------------------------ (3)

def test_an_encode_dies_with_a_killed_server(tmp_path):
    """PR_SET_PDEATHSIG: a SIGKILLed parent (no shutdown hook runs) takes its ffmpeg."""
    script = tmp_path / "parent.py"
    script.write_text(textwrap.dedent(f"""
        import subprocess, sys, threading
        sys.path.insert(0, {str(REPO)!r})
        from roughcut import procscope
        procscope.install()
        sc = procscope.Scope("previews", background=True)
        def run():
            with procscope.active(sc):
                subprocess.run({SLOW!r}, capture_output=True)
        threading.Thread(target=run).start()
        import time
        while not sc.alive():
            time.sleep(0.02)
        print(sc.alive()[0].pid, flush=True)
        time.sleep(120)
    """), encoding="utf-8")
    parent = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True)
    try:
        pid = int(parent.stdout.readline())
        assert until(lambda: state(pid) in "RS" and Path(f"/proc/{pid}/comm").read_text().strip()
                     == "ffmpeg", 5), state(pid)
        parent.kill()                         # SIGKILL: no atexit, no shutdown hook
        parent.wait(5)
        assert until(lambda: state(pid) in ("gone", "Z"), 3.0), \
            f"the encode outlived its server: {state(pid)}"
    finally:
        if parent.poll() is None:
            parent.kill()


def test_the_shutdown_hook_kills_every_encode(live_server):
    import server
    started = threading.Event()

    def encode():
        with server.procscope.active(server.media_scope()):
            started.set()
            subprocess.run(SLOW, capture_output=True)

    th = threading.Thread(target=encode, daemon=True)
    th.start()
    started.wait(5)
    assert until(lambda: len(ffmpeg_pids()) >= 1, 5)
    _play(live_server, True)
    assert until(lambda: all(state(p) == "T" for p in ffmpeg_pids()), 1.0)
    t0 = time.time()
    server._shutdown_children()
    assert until(lambda: not ffmpeg_pids(), 3.0), ffmpeg_pids()
    assert time.time() - t0 < 2.0
    th.join(timeout=5)
    assert server.playing_now() is False


# ------------------------------------------------------------------------------ (4)

def test_a_poster_storm_leaves_the_board_its_connections(live_server, monkeypatch):
    import server
    base = live_server
    real_poster = server.build_poster
    live = {"now": 0, "max": 0, "n": 0}
    lock = threading.Lock()

    def slow_poster(src, dest, t, original=False):
        with lock:
            live["now"] += 1
            live["n"] += 1
            live["max"] = max(live["max"], live["now"])
        try:
            time.sleep(3.0)                    # a 4K frame grab on a loaded box
            return real_poster(src, dest, t, original=original)
        finally:
            with lock:
                live["now"] -= 1

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        try:
            out = {"n": 0, "last": time.time()}

            def req(r, d):
                if "/media/poster/" in r.url:
                    out["n"] += d
                    out["last"] = time.time()
            pg.on("request", lambda r: req(r, 1))
            pg.on("requestfinished", lambda r: req(r, -1))
            pg.on("requestfailed", lambda r: req(r, -1))
            pg.goto(base)
            pg.wait_for_function("document.querySelectorAll('#tl .blk').length === 2")
            # every picture the board shows at open is built (fast), so the storm is the
            # filmstrip's alone
            assert until(lambda: time.time() - out["last"] > 2.0, 30), out
            monkeypatch.setattr(server, "build_poster", slow_poster)
            pg.evaluate("tl.zoomTo(420)")      # ~13 tiles a block, every one a new frame
            pg.wait_for_function("""[...document.querySelectorAll('#tl .blk img.ftile')]
                .filter(i => i._rcWant && !(i.complete && i.getAttribute('src') === i._rcWant
                && i.naturalWidth > 0)).length >= 8""", timeout=5000)
            pending = pg.evaluate("""[...document.querySelectorAll('#tl .blk img.ftile')]
                .filter(i => i._rcWant && !(i.complete && i.naturalWidth > 0)).length""")
            assert pending >= 8, pending
            pg.wait_for_timeout(400)
            took = pg.evaluate("""async () => { const out = [];
              for (let k = 0; k < 5; k++) {
                const t = performance.now();
                await (await fetch('/api/firstpass', { credentials: 'include' })).json();
                out.push(performance.now() - t);
                await new Promise((r) => setTimeout(r, 250));
              } return out; }""")
            assert max(took) < 300, f"/api/firstpass took {took} ms under the poster storm"
            assert pg.evaluate("tl.posterBusy") <= 2
            assert live["max"] <= 2, f"{live['max']} poster builds at once from the filmstrip"
            # and the storm drains: every tile in view gets its frame
            try:
                # (in view: within the timeline's view ± 1250 px, as Chrome's lazy load)
                pg.wait_for_function("""[...document.querySelectorAll('#tl .blk img.ftile')]
                    .filter(i => i._rcWant && i._rcVis !== false).every(i => i.complete
                    && i.naturalWidth > 0 && i.getAttribute('src') === i._rcWant)""",
                    timeout=60000)
            except Exception:
                tiles = pg.evaluate("""[...document.querySelectorAll('#tl .blk img.ftile')]
                    .map(i => ({want: i._rcWant, src: i.getAttribute('src'), vis: i._rcVis,
                    busy: i._rcBusy, done: i.complete, w: i.naturalWidth,
                    x: Math.round(i.getBoundingClientRect().left)}))""")
                raise AssertionError(f"tiles never drained: {tiles} busy={pg.evaluate('tl.posterBusy')}")
            print(f"\nposter storm: {pending} tiles pending, {live['n']} builds, at most "
                  f"{live['max']} at once; /api/firstpass {', '.join(f'{x:.0f}' for x in took)} ms")
            assert errors == [], errors
        finally:
            browser.close()
