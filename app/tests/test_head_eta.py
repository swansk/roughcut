"""Wave 16 SRVBUDGET2 (verdict 12, punch 10): the wait line's estimate tells the truth.

Verdict 12: a just-imported KX_03 played from its head at +5-8 s while the line said "~3 s"
at +0.6 s and "~1 s" at +5 s. The estimate was a prior (start-up + 0.15 s a footage second)
less the time run, floored at a second; the judge's heads took 0.33-0.38 s a footage second,
and a start-up running long read "~1 s" until it ended. Now the prior is what this box
measured, a start-up that has not put out a frame never reads as nearly done, and once
frames flow the estimate is the encode's own: ffmpeg's progress reports (`-progress`), the
footage seconds left at the speed it has made them.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from test_preview_building import _slow_heads, _status, building  # noqa: E402,F401


def _build(span, t0, p0=None, p=None):
    return {"span": span, "t0": t0, "p0": p0, "p": p, "scope": None}


def test_a_head_left_is_its_progress_once_frames_flow(monkeypatch):
    import server
    monkeypatch.setitem(server.MEDIA, "head_rate", 0.3)
    monkeypatch.setitem(server.MEDIA, "head_tail", None)          # not measured yet
    monkeypatch.setattr(server, "_head_load", lambda: 1.0)
    span = (10.0, 18.0)                                  # 8 s: prior 1.0 + 8 x 0.3 = 3.4 s
    now = 100.0
    # just started: the prior less the time run
    assert server._head_left(_build(span, now - 0.5), now) == pytest.approx(2.9)
    # its start-up running long, no frame out: the whole encode is still ahead (not "~1 s")
    assert server._head_left(_build(span, now - 6.0), now) == pytest.approx(2.4)
    # frames flowing 1 s at 2 footage s a second, 3 s made: 5 s left at that speed = 2.5 s
    got = server._head_left(_build(span, now - 3.0, p0=(now - 1.0, 1.0), p=(now, 3.0)), now)
    assert got == pytest.approx(2.5 + server.HEAD_TAIL_S)
    # ...and a slow one is said to be slow: 0.5 s a second, 2 s made of 8
    got = server._head_left(_build(span, now - 9.0, p0=(now - 2.0, 1.0), p=(now, 2.0)), now)
    assert got == pytest.approx(12.0 + server.HEAD_TAIL_S)
    # a report too young to time: the prior for what is left of it, never under the floor
    got = server._head_left(_build(span, now - 6.0, p0=(now - 0.1, 4.0), p=(now, 4.0)), now)
    assert got == pytest.approx(1.2)
    # every frame out: the tail (the muxer's rewrite, the exit) is what is left
    got = server._head_left(_build(span, now - 4.0, p0=(now - 2.0, 1.0), p=(now, 8.0)), now)
    assert got == pytest.approx(0.1 + server.HEAD_TAIL_S)
    # ...as measured here once a head has been made
    monkeypatch.setitem(server.MEDIA, "head_tail", 0.3)
    got = server._head_left(_build(span, now - 4.0, p0=(now - 2.0, 1.0), p=(now, 8.0)), now)
    assert got == pytest.approx(0.4)


def test_the_prior_is_what_this_box_measured():
    import server
    # the judge's heads: 8 s of 4K HEVC in 3.6-4.0 s at load 2.8-9.7 (with the bin's own
    # previews beside them): 0.33-0.38 s a footage second past the start-up
    assert 0.3 <= server.HEAD_RATE[True] <= 0.38
    est = server.HEAD_START_S + 8.0 * server.HEAD_RATE[True]
    assert 3.6 / 1.5 <= est <= 4.0 * 1.5


def test_a_preview_encode_reports_its_progress_as_it_goes(tmp_path):
    import server
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi", "-i",
                    "testsrc2=size=1280x720:rate=30:duration=12", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=12", "-c:v", "libx264", "-preset", "ultrafast",
                    "-g", "30", "-c:a", "aac", "-shortest", str(src)], check=True, capture_output=True)
    seen: list[tuple[float, float]] = []
    t0 = time.monotonic()
    with server.preview_progress(lambda s: seen.append((time.monotonic() - t0, s))):
        rc, err = server._preview_run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-re", "-i",
                                       str(src), "-t", "3", "-c:v", "libx264", "-preset",
                                       "ultrafast", "-f", "mp4", str(tmp_path / "out.mp4")])
    assert rc == 0, err
    outs = [s for _t, s in seen]
    assert len(outs) >= 4, seen                     # ~4 a second over a 3 s real-time encode
    assert outs == sorted(outs) and outs[-1] >= 2.5, seen
    assert seen[0][0] < 2.0, seen                    # as it goes, not at the end
    # and without a callback it is the plain run, errors said
    rc, err = server._preview_run(["ffmpeg", "-v", "error", "-nostdin", "-i",
                                   str(tmp_path / "nope.mp4"), "-f", "null", "-"])
    assert rc != 0 and "nope.mp4" in err
    # a failing encode with a callback says its error too
    with server.preview_progress(lambda s: None):
        rc, err = server._preview_run(["ffmpeg", "-v", "error", "-nostdin", "-i",
                                       str(tmp_path / "nope.mp4"), "-f", "null", "-"])
    assert rc != 0 and "nope.mp4" in err


def test_the_status_estimate_is_within_one_and_a_half_times_the_wait(building, monkeypatch):
    """The fixture's head is made to take a Killington head's time, its frames out at an even
    pace after a start-up (`_slow_heads`); every estimate the status gives while more than a
    second is left is within 1.5x of the real wait."""
    building["heads"]()
    url, server = building["url"], building["server"]
    _slow_heads(server, monkeypatch, delay_s=4.0)        # the prior: what it measured
    asked = time.monotonic()
    threading.Thread(target=_status, args=(url, "CLIP_B", 1.0), daemon=True).start()
    seen = []
    while time.monotonic() - asked < 20:
        st = _status(url, "CLIP_B")
        seen.append((time.monotonic() - asked, st["eta_s"], bool(st["head"])))
        if st["head"]:
            break
        time.sleep(0.2)
    real = time.monotonic() - asked + server.HEAD_PLAY_S       # the board's pick-up after
    said = [(round(t, 2), e, round(real - t, 2)) for t, e, h in seen if not h and t >= 0.3]
    print(f"head made at {real - server.HEAD_PLAY_S:.2f}s; (t, eta, real left): {said}")
    assert said
    for t, e, left in said:
        if left >= 1.0:
            assert left / 1.5 <= e <= left * 1.5, (t, e, left, said)
