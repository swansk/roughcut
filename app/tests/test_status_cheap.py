"""The status endpoints stay cheap under load (redesign wave 11, lane CHEAP; verdict 8,
punch 10).

Verdict 8: while an export and the proxy builds ran (load ~19), `GET /api/flow`,
`/api/status` and `/api/firstpass` took 0.5-1.5 s and `/api/peaks` up to 10.4 s. The
board polls all four, so the whole UI felt stuck. Now:

  (1) flow, status and first pass answer from a snapshot of what is on disk, rebuilt
      only when something it reads changes (an inotify watch, drained without touching
      the disk; an in-process write is a change the same way). What changes every
      second, the running jobs' progress and the CLI's fix, is merged in from memory;
  (2) a clip's waveform is decoded by a background worker, once, into the peaks file
      (when its sound is first heard, or first asked for); the GET serves it from
      memory, and until it exists answers `202 {"pending": true}` at once.

The tests:

  * a snapshot hit opens no file and lists no folder (an audit hook counts them), on
    the suite's own project — before, one poll of the three opened dozens;
  * a real server process on a bin of the suite's kind (testsrc2 + a tone, with
    sidecars), longer and larger so the work lasts, builds its proxies (real ffmpeg) and
    runs a whole-cut Quick 1080p export (real ffmpeg) while each endpoint is polled 20
    times: p95 <= 150 ms for flow / status / firstpass, <= 300 ms for peaks (60 s clips:
    a decode inside the GET took 0.3-0.6 s here); both jobs still running at the end;
  * a cut written behind the server's back is the next poll's answer, and a clip's
    sidecar landing (its sound heard) makes its peaks without anybody asking.

    uv run --with pytest --with fastapi --with uvicorn --with httpx --with numpy \\
        --with playwright pytest app/tests/test_status_cheap.py -q
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from conftest import TRANSCRIPT

REPO = Path(__file__).resolve().parents[2]
CLIP_S = 60.0
STEMS = [f"LOAD_{c}" for c in "ABCDEF"]
POLLS = 20
P95_MS = {"/api/flow": 150.0, "/api/status": 150.0, "/api/firstpass": 150.0, "peaks": 300.0}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_clip(path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30:duration={CLIP_S}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={CLIP_S}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True)


def _sidecar(stem: str) -> dict:
    n = int(CLIP_S * 10)
    return {"clip": f"{stem}.MP4", "duration_s": CLIP_S, "sample_rate": 16000, "frame_hz": 10,
            "tracks": {"speech_band_db": [-30.0] * n, "flatness": [0.05] * n,
                       "onset": [0.2] * n, "vad": [0] * n},
            "transcript": TRANSCRIPT, "events": [],
            "summary": {"speech_fraction": 0.5, "wind_dominant_fraction": 0.0,
                        "integrated_lufs": -18.0, "peak_dbfs": -3.0, "audio_usable": True,
                        "n_words": 6, "analysis_s": 0.1},
            "candidates": [{"t": 0.5, "end": 2.0, "why": "speech: hello there", "score": 0.55}]}


def _p95(xs: list[float]) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]


@pytest.fixture(scope="module")
def loaded(tmp_path_factory):
    root = tmp_path_factory.mktemp("cheap")
    footage, sidecars, work = root / "footage", root / "sidecars", root / "work"
    for d in (footage, sidecars, work):
        d.mkdir()
    for stem in STEMS:
        _make_clip(footage / f"{stem}.MP4")
        (sidecars / f"{stem}.audio.json").write_text(json.dumps(_sidecar(stem)), encoding="utf-8")
    # the whole bin, twice over: a 6-minute cut, so the export outlasts the polls
    segs = [{"clip": f"{s}.MP4", "in": 0.0, "out": CLIP_S, "why": "load"} for s in STEMS * 2]
    edl = root / "edl.json"
    edl.write_text(json.dumps({"variant": "T", "title": "load", "orient": "none", "story": "",
                               "target_s": [60, 600], "segments": segs}), encoding="utf-8")
    port = _free_port()
    log = open(root / "server.log", "w")
    proc = subprocess.Popen(
        [sys.executable, str(REPO / "app" / "server.py"), "--footage", str(footage),
         "--edl", str(edl), "--sidecars", str(sidecars), "--work", str(work),
         "--visual", str(work / "no-visual"), "--port", str(port), "--no-probe"],
        stdout=log, stderr=subprocess.STDOUT, env={**os.environ, "PYTHONUNBUFFERED": "1"},
        start_new_session=True)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                if httpx.get(base + "/api/project", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        else:
            raise AssertionError((root / "server.log").read_text())
        yield {"base": base, "root": root, "work": work, "segs": segs, "proc": proc}
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
        log.close()


def test_status_endpoints_answer_fast_during_an_export_and_proxy_builds(loaded):
    base = loaded["base"]
    c = httpx.Client(base_url=base, timeout=30)
    r = c.post("/api/render", json={"preset": "quick", "name": "load-test",
                                    "segments": loaded["segs"]})
    assert r.status_code == 200, r.text
    job = r.json()["job"]
    st = c.get("/api/status").json()
    assert st["proxies"]["done"] < st["proxies"]["total"], "the proxies finished before the polls"

    times: dict[str, list[float]] = {k: [] for k in P95_MS}
    pending = 0
    for i in range(POLLS):
        for path in ("/api/flow", "/api/status", "/api/firstpass"):
            t0 = time.perf_counter()
            r = c.get(path)
            times[path].append((time.perf_counter() - t0) * 1000.0)
            assert r.status_code == 200, (path, r.text[:300])
        stem = STEMS[i % len(STEMS)]
        t0 = time.perf_counter()
        r = c.get(f"/api/peaks/{stem}?pps=50")
        times["peaks"].append((time.perf_counter() - t0) * 1000.0)
        assert r.status_code in (200, 202), r.text[:300]
        if r.status_code == 202:
            assert r.json()["pending"] is True
            assert "no-store" in r.headers.get("cache-control", "")
            pending += 1
        else:
            assert len(r.json()["max"]) == pytest.approx(CLIP_S * 50, abs=3)
        time.sleep(0.05)

    # the work was still going when the polls ended: the numbers are under load
    jobs = c.get("/api/jobs").json()["jobs"]
    export = next((j for j in jobs if j.get("id") == job), None)
    assert export is not None and export["state"] not in ("done", "failed", "cancelled"), \
        "the export finished before the polls ended"
    report = {k: {"p95": round(_p95(v), 1), "max": round(max(v), 1),
                  "median": round(sorted(v)[len(v) // 2], 1)} for k, v in times.items()}
    print("\nload", os.getloadavg()[0], json.dumps(report), "peaks pending", pending)
    for k, limit in P95_MS.items():
        assert _p95(times[k]) <= limit, (k, report)

    # every peaks request was answered, and the waveform landed without a GET making it
    deadline = time.time() + 120
    while time.time() < deadline:
        r = c.get(f"/api/peaks/{STEMS[0]}?pps=50")
        if r.status_code == 200:
            break
        time.sleep(0.25)
    assert r.status_code == 200 and len(r.json()["max"]) == pytest.approx(CLIP_S * 50, abs=3)
    assert list((loaded["work"] / "audio").rglob(f"{STEMS[0]}.*.peaks"))
    c.post(f"/api/render/{job}/cancel")
    c.close()


def test_a_change_on_disk_shows_on_the_next_poll(loaded):
    """The snapshot is never stale: a cut written behind the server's back is the next
    answer's cut (no wait between the write and the GET)."""
    c = httpx.Client(base_url=loaded["base"], timeout=30)
    edl = loaded["root"] / "edl.json"
    d = json.loads(edl.read_text(encoding="utf-8"))
    for n in (3, 5, 2):
        d["segments"] = loaded["segs"][:n]
        edl.write_text(json.dumps(d), encoding="utf-8")
        assert c.get("/api/status").json()["segments"] == n
        cut = next(s for s in c.get("/api/flow").json()["stages"] if s["key"] == "cut")
        assert cut["counts"]["shots"] == n
        would = c.get("/api/firstpass").json()["would"]
        assert len(would["keep"]) + len(would["replace"]) == n
    c.close()


def test_a_clip_heard_gets_its_peaks_without_a_request(loaded):
    """Its sidecar landing is the server's cue (seen by the snapshot's watch on the next
    poll): the peaks file is written in the background, and the first GET is the pairs."""
    sidecars, footage = loaded["root"] / "sidecars", loaded["root"] / "footage"
    (footage / "LOAD_NEW.MP4").write_bytes((footage / f"{STEMS[1]}.MP4").read_bytes())
    tmp = sidecars / "LOAD_NEW.audio.json.tmp"
    tmp.write_text(json.dumps(_sidecar("LOAD_NEW")), encoding="utf-8")
    tmp.replace(sidecars / "LOAD_NEW.audio.json")
    c = httpx.Client(base_url=loaded["base"], timeout=30)
    assert c.get("/api/status").json()["analysed"] == len(STEMS) + 1
    made = None
    deadline = time.time() + 120
    while time.time() < deadline and not made:
        made = list((loaded["work"] / "audio").rglob("LOAD_NEW.*.peaks"))
        time.sleep(0.25)
    assert made, "the peaks were never written"
    r = c.get("/api/peaks/LOAD_NEW?pps=50")
    assert r.status_code == 200 and len(r.json()["max"]) == pytest.approx(CLIP_S * 50, abs=3)
    c.close()


# ------------------------------------------------------------------ a hit reads nothing

_COUNT = {"on": False, "events": []}


def _audit(event, args):
    if _COUNT["on"] and event in ("open", "os.listdir", "os.scandir", "glob.glob"):
        _COUNT["events"].append((event, str(args[0])[-80:] if args else "",
                                 threading.current_thread().name))


sys.addaudithook(_audit)


def _background_idle(server, timeout: float = 60.0) -> None:
    """Wait until the work `configure()` started behind the requests is over.

    The audit hook counts every thread in the process, and `configure()` warms the clip
    facts in the background (`warm_clip_facts`: two ffprobes a clip for its facts and one
    for its colour tags, on a 4-thread pool); each ffprobe's pipes are an "open" of an
    fd. This test flaked in the gates of verdicts 9 and 10 and the lead's on 69fb0f0, on those:
    `[('open', '12'), ('open', '15'), ('open', '22'), ('open', '24')]`, the clipfacts
    threads' `_warm_colour_probe → clip_probe → subprocess.run` (lane POLISH10 caught it
    with the thread and stack on each event, in the gate's file order). Alone, the test
    builds CLIP_A's peaks first (a second or two) and the warm-up is over by then; in
    the full suite the session's work dir already holds the proxies and the peaks, so
    the window opened ~50 ms after `configure()`, with the warm-up still probing on a
    loaded box. Not the snapshot: every counted open was in a clipfacts thread.

    The clip-facts pool is idle when nothing is queued and every worker waits for work
    (its innermost frame the pool's `_worker` loop, blocked in the queue's get), twice
    50 ms apart: a task taken between the two looks either ran before the window or
    is caught by the second. The peaks, colour and sprite workers are idle when their
    queues are empty and their threads gone."""
    def pool_idle(pool) -> bool:
        if not pool._work_queue.empty():
            return False
        frames = sys._current_frames()
        return all(frames.get(t.ident) is None or frames[t.ident].f_code.co_name == "_worker"
                   for t in list(pool._threads))

    deadline = time.time() + timeout
    pool = server.CLIP_FACTS["pool"]
    while pool is not None:
        if pool_idle(pool):
            time.sleep(0.05)
            if pool_idle(pool):
                break
        assert time.time() < deadline, f"the clip-facts warm-up still busy after {timeout} s"
        time.sleep(0.05)
    while True:
        busy = [name for name, q in (("peaks", server.PEAKS_Q), ("colour", server.COLOUR_Q),
                                     ("sprites", server.SPRITES))
                if q["pending"] or (q.get("thread") is not None and q["thread"].is_alive())]
        if not busy:
            return
        assert time.time() < deadline, f"background workers still busy after {timeout} s: {busy}"
        time.sleep(0.05)


def test_a_snapshot_hit_opens_no_file(client, project):
    """After one poll, the next polls of flow / status / firstpass (and a peaks answer
    already made) open nothing and list nothing — they are the snapshot. Before wave
    11 each poll opened the EDL, the sidecars, the asks, the journal… every time.
    Counted in every thread, once the background work `configure()` started is over
    (`_background_idle`: wave 14, the flake's cause)."""
    import server

    paths = ("/api/flow", "/api/status", "/api/firstpass")
    for p in paths:
        assert client.get(p).status_code == 200
    deadline = time.time() + 60
    while client.get("/api/peaks/CLIP_A?pps=50").status_code == 202 and time.time() < deadline:
        time.sleep(0.1)
    assert client.get("/api/peaks/CLIP_A?pps=50").status_code == 200
    _background_idle(server)
    _COUNT["events"].clear()
    _COUNT["on"] = True
    try:
        for _ in range(5):
            for p in (*paths, "/api/peaks/CLIP_A?pps=50"):
                assert client.get(p).status_code == 200
    finally:
        _COUNT["on"] = False
    assert _COUNT["events"] == [], _COUNT["events"][:20]
    # and the next change is read at once: one shot fewer on disk
    d = json.loads(project["edl"].read_text(encoding="utf-8"))
    d["segments"] = d["segments"][:1]
    project["edl"].write_text(json.dumps(d), encoding="utf-8")
    assert client.get("/api/status").json()["segments"] == 1
