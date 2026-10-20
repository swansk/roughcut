"""The Program shows the right frame at the right time while it plays (wave 17 PLAY8;
verdict 13 punch 9; REDESIGN-GOAL dimension 6).

Judge 13 on Killington: every cut showed the incoming shot 1-3 frames into it (PERF7: "the
incoming buffer arrives 1-3 frames past its in-point and boundary() cuts 0.04 s early; a
12-frame shot shows 9-10 frames"), the first animation frame past a cut had the new buffer
under the old shot's playhead (edit.py's `wrong_clip_frames`, 1-2 a cut), and a 0.5x shot of
a 29.97 proxy held 83 ms 1-4 times in a 3 s play where its cadence is an even 66.7 ms.

Causes, measured on Killington (lane PLAY8):
  * the incoming buffer rolled from its park roll.lat (play() to its first NEW frame, 25-55 ms,
    mostly the wait for that frame) ahead of a cut placed 0.04 s before the out, so its clock
    was 50-100 ms past its in at the cut (the presented frames say so);
  * boundary() drew the playhead from the outgoing clock in the cut's frame and moved it on
    only in the next one;
  * the grade canvas drew each frame in its frame callback, and 12-30 % of those run a display
    frame before the frame is shown (its expectedDisplayTime): drawn then, a frame went up a
    display frame early, its neighbours one short and one long (50 then 83 ms at 0.5x).

The fixture: three 320x180 clips at 29.97 with sound, every shot graded (so the canvas is the
picture, as on an auto-balanced bin). One play through five shots: four cuts, ins at different
places inside their frames, and a 0.5x shot last. The page records every frame callback of
the Program's buffers (the frame's media time and when it is shown) and every animation frame
(which buffer is up, the playhead, the canvas's draws), and the test rebuilds what each display
frame showed: the buffer that was up and its latest frame shown by then (a frame presented
while hidden is still its picture when it goes up).

Asserted: on every animation frame the playhead is in the shot of the buffer that is up; at
three cuts of four or more the outgoing shot's last frame is the last up and the incoming's
first is its in's frame, and never more than one frame off at any (a display frame either way
at one cut is a loaded box's: a start, or a frame callback, a display frame late). The 0.5x
shot: the canvas adds no hold over 70 ms of its own to the buffer's frames (from 0.2 s into
it). Before (base d9924e2, this fixture, 3 runs at load 15): the incoming opened 2 frames into
the shot at three cuts of four, the playhead lagged the buffer at every cut, and the canvas
held 83 ms 5-6 times in the 0.5x shot.
"""

from __future__ import annotations

import json
import math
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

FPS = 30000 / 1001
CLIP_S = 8.0
BALANCE = {"gain": [1.06, 1.0, 0.94], "exposure": 1.05, "knee": 0.8, "lift": 0.0}
STEMS = ["F30_A", "F30_B", "F30_C"]
R = 30000 / 1001
# the ins sit at different places inside their frames (the frame a time names: the last one
# that starts at or before it + 1 ms): on a frame's start, 4 / 13 / 24 ms into
# that, 42 frames long each (the 0.5x shot 36 frames of clip, 72 of film)
SEGS = [{"id": f"s{k}", "clip": c, "in": round(f / R + d, 4), "out": round((f + n) / R + d, 4), **x}
        for k, (c, f, d, n, x) in enumerate([("F30_A.MP4", 15, 0, 42, {}), ("F30_B.MP4", 40, 0.004, 42, {}),
                                             ("F30_C.MP4", 70, 0.013, 42, {}), ("F30_A.MP4", 120, 0.024, 42, {}),
                                             ("F30_B.MP4", 150, 0, 36, {"speed": 0.5})])]


def _make(path: Path, hue: int, hz: int) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=30000/1001:duration={CLIP_S},hue=h={hue}",
         "-f", "lavfi", "-i", f"sine=frequency={hz}:duration={CLIP_S}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "30",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True)


@pytest.fixture(scope="module")
def proj(tmp_path_factory):
    from conftest import _sidecar
    root = tmp_path_factory.mktemp("cutframes")
    footage, sidecars, work = root / "footage", root / "sidecars", root / "work"
    for d in (footage, sidecars, work):
        d.mkdir()
    for k, stem in enumerate(STEMS):
        _make(footage / f"{stem}.MP4", 120 * k, 330 + 110 * k)
        side = _sidecar(stem)
        side["duration_s"] = CLIP_S
        (sidecars / f"{stem}.audio.json").write_text(json.dumps(side), encoding="utf-8")
    edl = {"variant": "T", "title": "cut frames", "orient": "none", "story": "",
           "target_s": [5, 20], "fps": R, "segments": SEGS,
           "colour": {"mode": "auto", "shots": {s["id"]: {"balance": dict(BALANCE)} for s in SEGS}}}
    (root / "edl.json").write_text(json.dumps(edl, indent=1), encoding="utf-8")
    return {"root": root, "footage": footage, "sidecars": sidecars, "work": work,
            "edl": root / "edl.json"}


@pytest.fixture(scope="module")
def live(proj):
    import uvicorn
    import server
    server.configure(proj["edl"], proj["footage"], proj["sidecars"], proj["work"], proxies=False,
                     visual=proj["work"] / "no-visual")
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
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    th.join(timeout=10)


# registered before the play, so in every animation frame it runs before the player's tick:
# what it records is the page as the previous frame's tick left it, i.e. what this frame shows
ARM = r"""() => { window.__fl = []; window.__rf = []; window.__dr = []; window.__on = true;
  // every graded draw while playing asks app.js whether its frame is past its shot (grade.js
  // draw): which buffer, which frame, and whether it was drawn
  if (!window.__past) { window.__past = window.rcPastShot;
    window.rcPastShot = (v) => { const r = window.__past(v);
      if (window.__on && v && v._pf && player.playing) window.__dr.push({v: v.id, mt: v._pf.mt, idx: player.idx, lv: liveVideo().id, drawn: !r});
      return r; }; }
  const vids = [...document.querySelectorAll('#player video.pvbuf')];
  vids.forEach(v => { const cb = (now, md) => { if (!window.__on) return;
     window.__fl.push({ed: md.expectedDisplayTime || now, t: now, v: v.id, mt: md.mediaTime}); v.requestVideoFrameCallback(cb); };
     v.requestVideoFrameCallback(cb); });
  const look = (ft) => { if (!window.__on) return;
     window.__rf.push({ft, playing: player.playing, ph: tl.state.playhead, idx: player.idx,
       lv: liveVideo() ? liveVideo().id : null, vis: (vids.find(v => v.classList.contains('live')) || {}).id || null,
       draws: window.grade ? grade.draws : null, on: !!document.querySelector('#gradeCanvas.on')});
     requestAnimationFrame(look); };
  requestAnimationFrame(look); }"""


def _fr(t: float) -> int:
    """The proxy frame on screen at clip time t: the last that starts at or before t + 1 ms."""
    return int((t + 0.001) * FPS)


def _play(live: str) -> dict:
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        pg = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(live)
        pg.wait_for_function("segs.length === 5 && player.vids.length === 3 && window.grade && grade.ready",
                             timeout=30000)
        for _ in range(3):                        # a page still wiring its monitor can drop a cue
            pg.evaluate("tl.seek(0.2)")
            try:
                pg.wait_for_function("player.idx === 0 && liveVideo().readyState >= 2 && !liveVideo().seeking",
                                     timeout=8000)
                break
            except Exception:
                continue
        pg.wait_for_timeout(1500)                 # the next shot parked, the canvas drawn
        starts = pg.evaluate("[0,1,2,3,4,5].map(i => i < 5 ? tl.filmStart(tl.idAt(i)) : tl.total())")
        pg.evaluate(ARM)
        pg.evaluate("playFrom(0)")
        pg.wait_for_function("!player.playing || player.idx === 4 && tl.state.playhead > tl.total() - 0.3",
                             timeout=30000, polling=100)
        pg.evaluate("pauseCut(); window.__on = false")
        rec = {"fl": pg.evaluate("window.__fl"), "rf": pg.evaluate("window.__rf"), "starts": starts,
               "cut": pg.evaluate("player.cutLog || []"), "clock": pg.evaluate("roll.clock"),
               "dr": pg.evaluate("window.__dr")}
        b.close()
        assert not errors, errors
    return rec


def _shown(rec: dict) -> list[tuple]:
    """(frame time, the buffer up, the media time it shows, the shot index) per display frame."""
    rf = [x for x in rec["rf"] if x["playing"]]
    byv: dict[str, list] = {}
    for f in sorted(rec["fl"], key=lambda f: f["ed"]):
        byv.setdefault(f["v"], []).append(f)
    out = []
    for x in rf[1:]:
        cur = None
        for f in byv.get(x["vis"], []):
            if f["ed"] <= x["ft"] + 1:
                cur = f
            else:
                break
        if cur is not None:
            out.append((x["ft"], x["vis"], cur["mt"], x["idx"]))
    return out


def test_every_cut_shows_the_shots_own_frames_and_the_playhead_goes_with_it(live):
    rec = _play(live)
    shown = _shown(rec)
    heads, report = [], []
    for k in range(1, 5):
        a, b = SEGS[k - 1], SEGS[k]
        out_frames = [s for s in shown if s[3] == k - 1]
        in_frames = [s for s in shown if s[3] == k]
        assert out_frames and in_frames, (k, len(out_frames), len(in_frames))
        # the buffer the incoming shot plays in, and its first picture on screen
        el_b = in_frames[-1][1]
        first = next(s for s in shown if s[1] == el_b and s[0] >= in_frames[0][0])
        last_out = [s for s in shown if s[0] < first[0] and s[1] != el_b][-1]
        L = _fr(a["out"] - a.get("speed", 1) / R)           # the frame of the shot's last film frame
        tail, head = L - _fr(last_out[2]), _fr(first[2]) - _fr(b["in"])
        report.append((k, tail, head))
        heads.append(head)
    print("\ncut (shot, outgoing frames never shown, incoming frames skipped):", report,
          "cutLog:", [(c["shot"], c["rolled"], c["held_ms"], c.get("ahead_ms")) for c in rec["cut"]],
          "start latency by rate:", rec["clock"])
    # A display frame either way at one cut in four is a loaded box's (a start or a frame
    # callback a display frame late); the base was two frames into the shot at three cuts of
    # four, every run.
    assert all(t in (-1, 0, 1) and h in (-1, 0, 1) for _, t, h in report), report
    assert sum(1 for _, t, _ in report if t == 0) >= 2, report     # the shot's last frame, and no more
    assert sum(1 for h in heads if h == 0) >= 3, report            # the in's own frame first
    # the graded canvas is the picture: it never draws the frame after a shot's last (a cut held
    # for the incoming shot's first frame keeps the last up; app.js rcPastShot)
    past = []
    for d in rec["dr"]:
        k = d["idx"] if d["v"] == d["lv"] else d["idx"] - 1       # held at a cut: the outgoing's
        seg = SEGS[k]
        if d["drawn"] and k < 4 and _fr(d["mt"]) > _fr(seg["out"] - seg.get("speed", 1) / R):
            past.append((k, _fr(d["mt"])))
    print("graded draws:", len(rec["dr"]), "kept off a frame past its shot:", sum(1 for d in rec["dr"] if not d["drawn"]))
    assert len(rec["dr"]) > 100 and past == [], past
    # every animation frame: the playhead is in the shot whose buffer is up (edit.py's
    # wrong_clip_frames), cuts and all
    starts = rec["starts"]
    wrong = []
    for x in [x for x in rec["rf"] if x["playing"]]:
        shot = next((i for i in range(5) if x["ph"] < starts[i + 1]), 4)
        if shot != x["idx"]:
            wrong.append((round(x["ft"]), x["idx"], shot, round(x["ph"], 4)))
    assert wrong == [], wrong


def test_a_half_speed_shot_changes_picture_every_four_display_frames(live):
    rec = _play(live)
    rf = [x for x in rec["rf"] if x["playing"] and x["idx"] == 4 and x["on"]]
    assert len(rf) > 60, len(rf)
    t0 = rf[0]["ft"]
    changes = [rf[i]["ft"] for i in range(1, len(rf)) if rf[i]["draws"] != rf[i - 1]["draws"] and rf[i]["ft"] - t0 > 200]
    gaps = [round(changes[i] - changes[i - 1], 1) for i in range(1, len(changes))]
    # the buffer's own frames, when the compositor put them up (expectedDisplayTime): the sound's
    # clock at 0.5x can put one up a display frame off, which no canvas can take back
    lv = rf[-1]["lv"]
    eds = sorted(f["ed"] for f in rec["fl"] if f["v"] == lv and f["ed"] - t0 > 200 and f["ed"] <= rf[-1]["ft"])
    own = [round(eds[i] - eds[i - 1], 1) for i in range(1, len(eds))]
    print("\n0.5x shot: canvas picture changes every (ms):", sorted(set(gaps)), "n", len(gaps),
          "over 70 ms at", [round(changes[i] - t0) for i in range(1, len(changes)) if changes[i] - changes[i - 1] > 70],
          "| the buffer's own frames over 70 ms:", sum(1 for g in own if g > 70))
    assert len(gaps) >= 20, gaps
    # the canvas adds no hold of its own (the base: 5-10 a play, every frame callback that came a
    # display frame before its frame went up)
    assert sum(1 for g in gaps if g > 70) <= sum(1 for g in own if g > 70) + 1, (gaps, own)


# ------------------------------------------------------------------ half speed, evenly
# (wave 18 PLAY9, verdict 14 punch 3: "the 0.5x shot judders on every play (8/8, 2-12 holds of
# 83 ms per 5 s, even at load 1.7-2.8)"; REDESIGN-GOAL dimension 6)
#
# On test_perf_latency's kind of footage at 29.97 (3840x2160 H.264, a 1 s GOP, temporal grain made
# at 1280 and scaled up, proxied by the server's own `ensure_proxies` to the 1280 proxy), a shot at
# 0.5x: its proxy has a new frame every 66.7 ms of film, four display frames at 60 Hz. Played for
# 4.5 s from 1 s into it, twice on one page (the first play is the page's first: its sound starts
# then), what the screen shows is measured on the frame clock (rAF's timestamp) and the worker's
# (each draw up from the display frame after the worker finished it): the canvas's draws while it
# is the picture, graded at 1440 and 1920 and ungraded at 1440 (grade.js draws a slowed shot
# through the canvas either way: slowPlaying). ASSERTED: no picture held past 67.5 ms on the
# worker's clock, nor on the frame clock (one a play allowed past one runnable task a core: the
# page's animation frames then run late enough to see a draw a display frame early or late; the
# worker's clock of such a play was even), every source frame of the play drawn (none skipped),
# the canvas up from its first draw. The buffer's own frames (rVFC expectedDisplayTime) are in the record: they hold
# 83 ms 0-12 times a play on the base and the head alike (the renderer's frame choice before its
# cadence locks, ~2 s), which the canvas no longer shows. Base 1fc6907, this fixture, load 3.6-4.9:
# the graded canvas held 83 ms 3-12 times in each of its 4 plays, and the ungraded shot was the raw
# buffer (no canvas), holding 3 times a play. A box already past twice its cores skips the hold
# count, visibly.

SLOW_STEMS = [("H30_A", 0), ("H30_B", 150)]
SLOW_SEGS = [{"id": "h1", "clip": "H30_A.MP4", "in": 1.0, "out": 2.5, "why": "one"},
             {"id": "h2", "clip": "H30_B.MP4", "in": 1.0, "out": 5.0, "speed": 0.5, "why": "slow"}]
SLOW_FROM_S, SLOW_PLAY_S = 1.0, 4.5      # film seconds into the 0.5x shot, and how long each play is
HOLD_BAR_MS = 67.5                       # a 0.5x frame of a 29.97 proxy is 66.7 ms (4 display frames)


def _make_4k(path: Path, hue: int) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i",
         f"testsrc2=size=1280x720:rate=30000/1001:duration={CLIP_S},hue=h={hue},"
         "noise=alls=12:allf=t,scale=3840:2160",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={CLIP_S}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True)


def _slow_edl(graded: bool) -> dict:
    colour = ({"mode": "auto", "shots": {s["id"]: {"balance": dict(BALANCE)} for s in SLOW_SEGS}}
              if graded else {"mode": "off"})
    return {"variant": "T", "title": "half speed", "orient": "none", "story": "",
            "target_s": [5, 20], "fps": R, "segments": SLOW_SEGS, "colour": colour}


@pytest.fixture(scope="module")
def slow_proj(tmp_path_factory):
    from concurrent.futures import ThreadPoolExecutor
    from conftest import _sidecar
    root = tmp_path_factory.mktemp("halfspeed")
    footage, sidecars, work = root / "footage", root / "sidecars", root / "work"
    for d in (footage, sidecars, work):
        d.mkdir()
    with ThreadPoolExecutor(len(SLOW_STEMS)) as ex:
        list(ex.map(lambda sh: _make_4k(footage / f"{sh[0]}.MP4", sh[1]), SLOW_STEMS))
    for stem, _h in SLOW_STEMS:
        side = _sidecar(stem)
        side["duration_s"] = CLIP_S
        (sidecars / f"{stem}.audio.json").write_text(json.dumps(side), encoding="utf-8")
    (root / "edl.json").write_text(json.dumps(_slow_edl(True), indent=1), encoding="utf-8")
    return {"root": root, "footage": footage, "sidecars": sidecars, "work": work, "edl": root / "edl.json"}


@pytest.fixture(scope="module")
def slow_live(slow_proj):
    """(Configured after the 320x180 module's tests: server.configure is the app's one project.)"""
    import uvicorn
    import server
    p = slow_proj
    server.configure(p["edl"], p["footage"], p["sidecars"], p["work"], proxies=False,
                     visual=p["work"] / "no-visual")
    server.ensure_proxies([f"{s}.MP4" for s, _h in SLOW_STEMS])
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


SLOW_ARM = r"""() => { window.__fl = []; window.__rf = []; window.__on = true; window.__t0 = performance.now();
  [...document.querySelectorAll('#player video.pvbuf')].forEach(v => { const cb = (now, md) => { if (!window.__on) return;
     window.__fl.push({t: now, ed: md.expectedDisplayTime || now, v: v.id, live: v.classList.contains('live'), mt: md.mediaTime});
     v.requestVideoFrameCallback(cb); }; v.requestVideoFrameCallback(cb); });
  const look = (ft) => { if (!window.__on) return;
     window.__rf.push({ft, t: performance.now(), playing: player.playing, idx: player.idx, draws: grade.draws,
                       on: !!document.querySelector('#gradeCanvas.on')});
     requestAnimationFrame(look); };
  requestAnimationFrame(look); }"""


def _load1() -> float:
    try:
        return float(open("/proc/loadavg").read().split()[0])
    except OSError:
        return 0.0


def _slow_plays(url: str, size: tuple[int, int], plays: int = 2) -> list[dict]:
    out = []
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        pg = b.new_context(viewport={"width": size[0], "height": size[1]}).new_page()
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(url)
        pg.wait_for_function("segs.length === 2 && player.vids.length === 3 && window.grade && grade.ready",
                             timeout=30000)
        for _ in range(plays):
            for _try in range(3):                 # a page still wiring its monitor can drop a cue
                # (the slowed shot's film start, read once the timeline has it: 1.5 s here)
                pg.wait_for_function("window.tl && tl.state && tl.state.segs.length === 2"
                                     " && Number.isFinite(tl.filmStart(tl.idAt(1)))", timeout=15000)
                start = pg.evaluate("tl.filmStart(tl.idAt(1))") + SLOW_FROM_S
                pg.evaluate(f"tl.seek({start})")
                try:
                    pg.wait_for_function("player.idx === 1 && liveVideo().readyState >= 2 && !liveVideo().seeking",
                                         timeout=8000)
                    break
                except Exception:  # noqa: BLE001 — tried again; the play below says if it never parked
                    continue
            parked = pg.evaluate("({idx: player.idx, ph: tl.state.playhead, rs: liveVideo().readyState,"
                                 " sk: liveVideo().seeking, ct: liveVideo().currentTime, src: liveVideo().dataset.src})")
            assert parked["idx"] == 1, f"never parked in the slowed shot: {parked}"
            pg.wait_for_timeout(2000)
            pg.evaluate(SLOW_ARM)
            load = _load1()
            pg.keyboard.press("Space")
            pg.wait_for_timeout(int(SLOW_PLAY_S * 1000))
            pg.keyboard.press("Space")
            pg.wait_for_timeout(150)
            pg.evaluate("window.__on = false")
            rec = {"load": load, "fl": pg.evaluate("window.__fl"), "rf": pg.evaluate("window.__rf"),
                   "pace": pg.evaluate("(grade.paceLog || []).slice()"), "t0": pg.evaluate("window.__t0"),
                   "dl": pg.evaluate("grade.drawLog.filter((d) => d.t >= window.__t0).map((d) => ({t: d.t, ts: d.ts, made: d.made}))"),
                   "rate": pg.evaluate("liveVideo().playbackRate")}
            out.append(rec)
        b.close()
        assert not errors, errors
    return out


def _slow_facts(rec: dict) -> dict:
    rf = [x for x in rec["rf"] if x["playing"]]
    on_at = [x["ft"] for x in rf if x["on"]]
    ch = [rf[i]["ft"] for i in range(1, len(rf)) if rf[i]["draws"] != rf[i - 1]["draws"]]
    edges = ch + [rf[-1]["ft"]] if ch else []
    gaps = [edges[i] - edges[i - 1] for i in range(1, len(edges))]
    live = sorted(f["ed"] for f in rec["fl"] if f["live"] and rf and rf[0]["ft"] <= f["t"] <= rf[-1]["ft"])
    own = [live[i] - live[i - 1] for i in range(1, len(live))]
    a, z = (rf[0]["ft"], rf[-1]["ft"]) if rf else (0, 0)
    # the frames drawn in the play (grade.drawLog `ts`: a paced frame's own media time, in us), each
    # one proxy frame after the one before: none skipped
    ks = sorted({round(d["ts"] / 1e6 * FPS) for d in rec["dl"] if a <= d["t"] <= z})
    steps = [ks[i] - ks[i - 1] for i in range(1, len(ks))]
    mts = [p for p in rec["pace"] if a - 50 <= p["due"] <= z + 50]
    # the worker's clock: each draw on screen from the first display frame after the worker finished
    # it (the rAF timestamps' grid), so a page late to see a draw does not move it
    V = 1000 / 60
    drawn = sorted((d for d in rec["dl"] if a <= d["t"] <= z), key=lambda d: d["t"])
    slot_of = lambda d: math.ceil((d["t"] - a) / V - 1e-6)                      # noqa: E731
    slots = sorted({slot_of(d) for d in drawn})
    wgaps = [(slots[i] - slots[i - 1]) * V for i in range(1, len(slots))]
    # each hold on the worker's clock, with whose it is: the buffer's when the frame that ended it
    # was presented by the buffer (its frame callback) after the time the pacer's clock had for it
    # (the frame before it's grade.paceLog `due` and one frame period at the rate on), so no canvas
    # could have had it up on time (the buffer's own clock fell behind: under load its sound's
    # clock stalls, and its frames come later and later); else the canvas's
    pace = {round(p["mt"], 4): p for p in rec["pace"]}
    came = {}
    for f in sorted(rec["fl"], key=lambda f: f["t"]):
        if f["live"]:
            came.setdefault(round(f["mt"], 4), f["t"])
    canvas_holds = buffer_late = 0
    for i in range(1, len(slots)):
        if (slots[i] - slots[i - 1]) * V <= HOLD_BAR_MS:
            continue
        before = [d for d in drawn if slot_of(d) == slots[i - 1]][-1]
        d = next(d for d in drawn if slot_of(d) == slots[i])
        m0, m1 = round(before["ts"] / 1e6, 4), round(d["ts"] / 1e6, 4)
        late = False
        if m0 in pace and m1 in came:
            want = pace[m0]["due"] + (m1 - m0) / max(0.05, rec["rate"]) * 1000
            late = came[m1] > want + 2
        buffer_late += 1 if late else 0
        canvas_holds += 0 if late else 1
    return {"load": rec["load"], "rate": rec["rate"], "draws": len(ch),
            "holds": sum(1 for g in gaps if g > HOLD_BAR_MS), "longest_ms": round(max(gaps), 1) if gaps else None,
            "worker_holds": sum(1 for g in wgaps if g > HOLD_BAR_MS), "canvas_holds": canvas_holds,
            "buffer_late_holds": buffer_late,
            "pattern": "".join(str(min(9, round(g / (1000 / 60)))) for g in gaps),
            "paced_frames": len(mts), "drawn_frames": len(ks), "skipped": sum(s - 1 for s in steps if s > 1),
            # up from its first draw on (an ungraded shot's canvas goes up with its first frame drawn;
            # the buffer under it shows that parked frame till then), and that within 100 ms
            "canvas_up": bool(on_at) and on_at[0] - a <= 100 and all(x["on"] for x in rf if x["ft"] >= on_at[0]),
            "own_holds": sum(1 for g in own if g > HOLD_BAR_MS), "own_longest_ms": round(max(own), 1) if own else None}


@pytest.mark.parametrize("graded,size", [(True, (1440, 900)), (True, (1920, 1080)), (False, (1440, 900))],
                         ids=["graded-1440", "graded-1920", "ungraded-1440"])
def test_a_half_speed_shot_of_a_4k_proxy_plays_evenly(slow_proj, slow_live, graded, size):
    slow_proj["edl"].write_text(json.dumps(_slow_edl(graded), indent=1), encoding="utf-8")
    cores, load0 = os.cpu_count() or 4, _load1()
    recs = _slow_plays(slow_live, size)
    dump = os.environ.get("ROUGHCUT_HALFSPEED_DUMP")          # a dir: each play's raw record
    if dump:
        for k, r in enumerate(recs):
            Path(dump, f"halfspeed-{'g' if graded else 'u'}{size[0]}-{int(time.time())}-{k}.json").write_text(json.dumps(r))
    facts = [_slow_facts(r) for r in recs]
    print("\nHALFSPEED", json.dumps({"graded": graded, "size": size, "plays": facts}), flush=True)
    for f in facts:
        assert f["rate"] == 0.5, f
        assert f["canvas_up"], f"the canvas is the picture of a slowed shot, graded or not: {f}"
        assert f["draws"] >= 60 and f["paced_frames"] >= 60 and f["drawn_frames"] >= 60, f
        assert f["skipped"] == 0, f"a source frame of the play was never drawn: {f}"
    if load0 > 2 * cores:
        pytest.skip(f"box at load {load0:.1f} on {cores} cores before the test: holds measured, not judged: {facts}")
    # Past one runnable task a core, the page's own animation frames run late enough (6-15 ms) to
    # see a draw the worker finished in the display frame before, or the next: one hold a play on
    # the frame clock that the worker's clock does not have (lane PLAY9, slow.py's `attr.py`)
    allowed = 0 if load0 <= cores else 1
    assert all(f["worker_holds"] == 0 for f in facts), f"the canvas held a frame on the worker's clock: {facts}"
    assert all(f["holds"] <= allowed for f in facts), (allowed, load0, facts)
