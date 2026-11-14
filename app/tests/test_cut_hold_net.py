"""No hold at a cut over a slow link (wave 29 CUTHOLD).

Karl, 2026-11-14: "sometimes playback freezes up between clips". He watches the board in a
Chrome on his laptop through an SSH tunnel, so every shot's bytes cross a link the suite's
localhost never has. Measured on his Copper cut (18 shots, proxies of 4-7 Mbit/s) with the
link throttled in the browser: the cut's own player held no cut over 83 ms at 20 Mbit/s and
40 ms; the proposal's player ("Play it") opened every shot's file in the one buffer on screen
at the shot's end and held every cut, 133-567 ms (median 208-308), with the sound stopped;
and no proxy answer could be revalidated (no ETag, no Last-Modified), so the second play of
the film fetched every byte again.

The fixture: three noisy 8 s clips (so a shot's bytes take time on the link), five shots of
2.4 s, two of them in turn from the same clip. The page's link is throttled to 20 Mbit/s with
40 ms of latency. Each player plays its five shots twice; on the second play (the first has
had its chance to fill the cache) no cut holds the picture more than HOLD_OK_MS, by the
page's own record (playLog, app.js) for the cut's player and by the frames the Program's
buffers present for the proposal's. The record reaches the server (GET /api/playlog).
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

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

R = 24000 / 1001
CLIP_S = 8.0
STEMS = ["NET_A", "NET_B", "NET_C"]
SEGS = [{"id": f"n{k}", "clip": c, "in": i, "out": round(i + 2.4, 4)}
        for k, (c, i) in enumerate([("NET_A.MP4", 0.5), ("NET_B.MP4", 0.4), ("NET_B.MP4", 4.6),
                                    ("NET_C.MP4", 1.0), ("NET_A.MP4", 4.0)])]
NET = {"offline": False, "latency": 40, "downloadThroughput": 20e6 / 8, "uploadThroughput": 20e6 / 8}
HOLD_OK_MS = 250
LOAD_MAX = 6.0


def _make(path: Path, hue: int, hz: int) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=24000/1001:duration={CLIP_S},hue=h={hue},noise=alls=24:allf=t",
         "-f", "lavfi", "-i", f"sine=frequency={hz}:duration={CLIP_S}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "12",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True)


@pytest.fixture(scope="module")
def net_live(tmp_path_factory):
    from conftest import _sidecar
    import uvicorn
    import server
    root = tmp_path_factory.mktemp("cuthold")
    footage, sidecars, work = root / "footage", root / "sidecars", root / "work"
    for d in (footage, sidecars, work):
        d.mkdir()
    for k, stem in enumerate(STEMS):
        _make(footage / f"{stem}.MP4", 120 * k, 330 + 110 * k)
        side = _sidecar(stem)
        side["duration_s"] = CLIP_S
        (sidecars / f"{stem}.audio.json").write_text(json.dumps(side), encoding="utf-8")
    edl = {"variant": "T", "title": "cut hold", "orient": "none", "story": "",
           "target_s": [5, 20], "fps": R, "segments": SEGS}
    (root / "edl.json").write_text(json.dumps(edl, indent=1), encoding="utf-8")
    server.configure(root / "edl.json", footage, sidecars, work, proxies=False, visual=work / "no-visual")
    server.ensure_proxies([f"{s}.MP4" for s in STEMS])
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
    yield {"url": f"http://127.0.0.1:{port}", "work": work}
    srv.should_exit = True
    th.join(timeout=10)


def _load() -> float:
    try:
        return os.getloadavg()[0]
    except OSError:
        return 0.0


# every frame the Program's buffers present, and which one is up on each animation frame
WATCH = r"""() => { window.__pic = []; window.__on = true;
  const vids = [...document.querySelectorAll('#player video.pvbuf')];
  if (!window.__hooked) { window.__hooked = true;
    vids.forEach(v => { const cb = (now, md) => { v.__pmt = md.mediaTime; v.requestVideoFrameCallback(cb); };
      v.requestVideoFrameCallback(cb); }); }
  const look = (ft) => { if (!window.__on) return;
    const up = vids.find(v => v.classList.contains('live'));
    window.__pic.push([ft, up ? up.id : null, up && up.__pmt != null ? up.__pmt : null]);
    requestAnimationFrame(look); };
  requestAnimationFrame(look); }"""


def _cut_holds(pic: list) -> list[float]:
    """The hold before each cut the picture shows: the buffer up changes, or its media time
    jumps (back, or more than 0.3 s on); the hold is the time since the picture last changed."""
    out, last, at = [], None, None
    for ft, vid, mt in pic:
        if mt is None:
            continue
        if last is None:
            last, at = (vid, mt), ft
            continue
        if (vid, mt) != last:
            if vid != last[0] or mt < last[1] - 0.01 or mt > last[1] + 0.3:
                out.append(ft - at)
            last, at = (vid, mt), ft
    return out


def _page(b, url):
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    cdp = ctx.new_cdp_session(pg)
    cdp.send("Network.enable")
    cdp.send("Network.emulateNetworkConditions", NET)
    pg.goto(url)
    pg.wait_for_function("segs.length === 5 && player.vids.length >= 3 && window.grade && grade.ready",
                         timeout=60000)
    return ctx, pg, errors


def _park(pg):
    for k in range(4):                    # a cue the page drops (as test_cut_frames): again, a hair on
        pg.evaluate(f"tl.seek({0.05 + 0.01 * k})")
        try:
            pg.wait_for_function("player.idx === 0 && liveVideo().readyState >= 2 && !liveVideo().seeking",
                                 timeout=8000)
            break
        except Exception:  # noqa: BLE001 — the assert below says if it never parked
            continue
    parked = pg.evaluate("({idx: player.idx, rs: liveVideo().readyState, src: liveVideo().dataset.src})")
    assert parked["idx"] == 0 and parked["rs"] >= 2, f"never parked on the first shot: {parked}"
    pg.wait_for_timeout(1500)


def test_the_cuts_player_holds_no_cut_over_a_slow_link_and_records_its_holds(net_live):
    load0 = _load()
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        ctx, pg, errors = _page(b, net_live["url"])
        recs = []
        for _ in range(2):
            _park(pg)
            pg.evaluate("playFrom(0)")
            pg.wait_for_function("!player.playing", timeout=60000, polling=200)
            recs.append(pg.evaluate("playLog.last[playLog.last.length - 1]"))
        pg.wait_for_timeout(500)
        b.close()
    assert not errors, errors
    import httpx
    kept = httpx.get(f"{net_live['url']}/api/playlog").json()["plays"]
    print("CUTHOLD cut", [r["longest"] for r in recs], "load", load0, flush=True)
    assert len(kept) >= 2 and kept[-1]["cuts"] == 4, kept
    assert kept[-1]["longest"]["why"] in ("none", "not loaded", "seeking", "decode"), kept[-1]
    warm = recs[1]
    assert warm["cuts"] == 4, warm
    if load0 > LOAD_MAX:
        pytest.skip(f"box at load {load0:.1f}: measured, not judged: {warm['longest']}")
    assert warm["longest"]["ms"] <= HOLD_OK_MS, warm


def test_the_proposals_player_holds_no_cut_over_a_slow_link(net_live):
    """"Play it" on a waiting proposal: the next shot is parked in a buffer off screen while one
    plays, and goes up at the shot's end (timeline-lanes.js armRange)."""
    load0 = _load()
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        ctx, pg, errors = _page(b, net_live["url"])
        pg.evaluate("tlLanes.showGhost(segs.map(s => ({id: s.id, clip: s.clip, in: s.in, out: s.out, why: 'kept'})))")
        pg.wait_for_function("tlLanes.ghost() && tlLanes.ghost().ghosts.length === 5", timeout=10000)
        holds = []
        for _ in range(2):
            _park(pg)
            pg.evaluate(WATCH)
            pg.evaluate("tlLanes.playPlan(0)")
            pg.wait_for_function("(() => { const g = tlLanes.ghost(); return tl.state.playhead > g.total - 0.3"
                                 " && liveVideo().paused; })()", timeout=60000, polling=200)
            pg.evaluate("window.__on = false")
            holds.append(_cut_holds(pg.evaluate("window.__pic")))
        b.close()
    assert not errors, errors
    print("CUTHOLD proposal", [[round(h) for h in x] for x in holds], "load", load0, flush=True)
    warm = holds[1]
    assert len(warm) >= 4, warm
    if load0 > LOAD_MAX:
        pytest.skip(f"box at load {load0:.1f}: measured, not judged: {warm}")
    assert max(warm) <= HOLD_OK_MS, warm
