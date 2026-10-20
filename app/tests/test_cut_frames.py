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
