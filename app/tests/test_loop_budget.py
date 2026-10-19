"""Wave 14 SERVERPERF (verdict 10, punch 1): the server never stalls.

Verdict 10 counted 145 "event loop was held" lines (up to 996 ms) and slow replies to
2.1 s while a Designed Effects check drew its proof on a server thread; a 110 MB drop
uploaded at ~1.25 MB/s. The budget: during a first pass, an export and an effect check,
`GET /api/debug/slow` reports no request over 300 ms and no loop hold over 200 ms.

These run a real uvicorn loop in-process (the TestClient bypasses the loop watcher) on
a copy of the suite's project, with the model scripted and the index's tools stubbed;
the export (assemble.py), the effect check (`roughcut.fxcheck`, a child) and the
upload's bytes are real.

A hold is the server's when the server's own threads ran through it — the loop thread
(sync work in an async handler) or another thread that kept the GIL — which the
watcher measures per hold (`where`: the loop's frame and each thread's CPU over the
hold). A hold during which nothing in the process ran is the box starving the process
(the suite runs at nice 19 beside other work); it is reported, and counted against
the budget only when the box is not overloaded.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import fx, inference  # noqa: E402

VIGNETTE = {"duration": 2.0, "size": 1.6, "anim": {},
            "shapes": [{"type": "ring", "color": "#000000", "opacity": 0.032, "width": 0.08,
                        "at": [0.0, 0.0], "r": 1.5, "r2": r2}
                       for r2 in (0.3, 0.36, 0.42, 0.48, 0.54, 0.6, 0.66, 0.71)]}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _overloaded() -> bool:
    return os.getloadavg()[0] >= 1.5 * (os.cpu_count() or 1)


def _ours(hold: dict) -> bool:
    """Did the server's own threads run through this hold (or the loop sit in our
    code)? See the module's docstring."""
    w = hold.get("where") or {}
    ran = float(w.get("loop_cpu_ms") or 0) + float(w.get("busy_cpu_ms") or 0)
    in_code = bool(w.get("loop")) and w["loop"] != "idle (select)"
    return in_code or ran >= 0.5 * float(hold["ms"])


@pytest.fixture
def live(tmp_path, project, monkeypatch):
    """The board on a real loop: a copy of the suite's cut, scripted model, stubbed
    index tools, everything written under tmp_path."""
    import uvicorn
    import server
    from test_firstpass import Scripted, _end_jobs
    from test_index import _stub_tools

    edl = tmp_path / "edl.json"
    shutil.copy(project["edl"], edl)
    work = tmp_path / "work"
    server.configure(edl, project["footage"], project["sidecars"], work, proxies=False,
                     visual=tmp_path / "no-visual", assets=project["assets"])
    _stub_tools(server, monkeypatch, tmp_path)
    monkeypatch.setattr(server, "FIRSTPASS_POLL_S", 0.05)
    monkeypatch.setattr(server, "FX_VERIFY_POLL_S", 0.1)
    server.FIRSTPASS.clear()
    server.INDEXES.clear()
    model = Scripted()
    inference.set_backend(model)
    inference.reset_spend()
    for k in ("all", "api", "loop"):
        server.SLOWEST[k] = None
    server.SLOW.clear()
    server.OVER_BUDGET.clear()
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"
    httpx.get(base + "/api/jobs", timeout=10)          # the first request starts the watcher
    try:
        yield {"base": base, "server": server, "work": work, "tmp": tmp_path}
    finally:
        _end_jobs(server, model)
        srv.should_exit = True
        th.join(timeout=15)
        inference.set_backend(None)


def _poll(base: str, stop: threading.Event, seen: list) -> None:
    """The board's own polling, at about its rate: every endpoint a screen reads."""
    paths = ["/api/jobs", "/api/flow", "/api/firstpass", "/api/status", "/api/backend",
             "/api/project", "/api/picks", "/api/fx", "/api/clips",
             "/api/assist/price?text=put%20a%20soft%20vignette%20on%20shot%201"]
    with httpx.Client(base_url=base, timeout=30) as c:
        while not stop.is_set():
            for p in paths:
                t = time.perf_counter()
                c.get(p)
                seen.append((p, (time.perf_counter() - t) * 1000))
            stop.wait(0.25)


def _upload(base: str, path: Path) -> dict:
    size = path.stat().st_size
    plan = httpx.post(base + "/api/upload", json={"files": [{"name": path.name, "size": size}]},
                      timeout=30).json()

    def chunks():
        with open(path, "rb") as fh:
            while True:
                b = fh.read(1 << 20)
                if not b:
                    return
                yield b
    t = time.perf_counter()
    r = httpx.put(f"{base}/api/upload/{plan['batch']}/0", content=chunks(), timeout=120)
    dt = time.perf_counter() - t
    return {"status": r.status_code, "s": dt, "mbps": size / dt / 1e6, "reply": r.json()}


def test_a_first_pass_an_export_and_an_effect_check_stay_inside_the_budget(live):
    base, server = live["base"], live["server"]
    # a big real video for the drop: uncompressed, so it is made at once (67 MB)
    drop = live["tmp"] / "drop" / "KX_DROP.mov"
    drop.parent.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi",
                    "-i", "testsrc2=size=640x360:rate=24:duration=6", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=6", "-c:v", "rawvideo",
                    "-pix_fmt", "uyvy422", "-c:a", "pcm_s16le", str(drop)],
                   check=True, capture_output=True)
    # the vignette on shot 1, proposed (a design is a model call; this is its answer)
    segs = httpx.get(base + "/api/project").json()["segments"]
    shot = segs[0]
    e = fx.validate_effect({"id": "fx_vig00001", "shot": shot["id"], "clip": shot["clip"],
                            "name": "soft edge vignette", "status": "proposed",
                            "events": [{"t": shot["in"], "x": 0.5, "y": 0.5}],
                            "overlay": {**VIGNETTE, "duration": shot["out"] - shot["in"]},
                            "sound": None}, segs)
    fx.save(server.fx_home(), e)

    stop, seen = threading.Event(), []
    pollers = [threading.Thread(target=_poll, args=(base, stop, seen), daemon=True)
               for _ in range(2)]
    for t in pollers:
        t.start()
    try:
        r = httpx.post(base + "/api/firstpass", json={"mode": "run"}, timeout=30)
        assert r.status_code == 200, r.text
        r = httpx.post(base + "/api/render", json={"preset": "quick", "name": "budget",
                                                    "segments": segs}, timeout=30)
        assert r.status_code == 200, r.text
        render = r.json()["job"]
        check = httpx.post(base + "/api/fx/verify", json={"id": e["id"]}, timeout=30).json()["job"]
        up = _upload(base, drop)
        assert up["status"] == 200, up
        deadline = time.time() + 240
        while time.time() < deadline:
            jobs = {j["id"]: j for j in httpx.get(base + "/api/jobs", timeout=30).json()["jobs"]}
            fp = httpx.get(base + "/api/firstpass", timeout=30).json()
            done = [jobs.get(j, {}).get("state") in ("done", "failed", None) for j in (render, check)]
            if all(done) and fp["state"] != "running":
                break
            time.sleep(0.5)
        else:
            raise AssertionError(f"jobs still running: {jobs}")
        time.sleep(1.0)
    finally:
        stop.set()
        for t in pollers:
            t.join(timeout=30)
    slow = httpx.get(base + "/api/debug/slow").json()
    assert slow["budget"] == {"request_ms": 300.0, "loop_ms": 200.0}
    holds = [r for r in slow["over_budget"] if r["method"] == "LOOP"]
    reqs = [r for r in slow["over_budget"] if r["method"] != "LOOP"]
    assert slow["slowest_loop"] is not None                         # the watcher ran
    report = (f"load {os.getloadavg()[0]:.1f}; holds {[(h['ms'], h.get('where')) for h in holds]}; "
              f"requests {[(r['ms'], r['method'], r['path']) for r in reqs]}")
    assert not [h for h in holds if _ours(h)], report
    if not _overloaded():
        assert not holds and not reqs, report
    else:
        assert not [r for r in reqs if r["ms"] > 1000.0], report
    # the drop's body is the client's time, the server's part of it is the write
    put = [r for r in [slow["slowest"]] + slow["over_budget"]
           if r and r["path"].startswith("/api/upload/")]
    assert all(r.get("body_ms") is not None or r["ms"] < 300 for r in put), put
    assert up["mbps"] >= 11.0, up                  # 110 MB in under 10 s
    assert len(seen) > 40
    assert httpx.get(f"{base}/api/job/{render}").json()["state"] == "done"
    snap = httpx.get(f"{base}/api/job/{check}").json()
    assert snap["state"] == "done" and snap["detail"] == "every check passed", snap
    assert snap.get("check_pid"), "the check did not run in a child process"


def test_a_hold_says_where_the_loop_was_and_what_ran(live):
    """The watcher's account of a hold names its cause: an async handler doing sync
    work shows its own frame; a thread keeping the GIL (one long C call) shows as the
    thread that ran; a request's body time is the client's, not the request's."""
    from fastapi.routing import APIRoute
    base, server = live["base"], live["server"]

    async def budget_sleeps_on_the_loop():
        time.sleep(0.35)                               # the bug: sync work on the loop
        return {"ok": True}

    def budget_hogs_the_gil():
        def hog():
            sum(range(60_000_000))                     # one C call: the GIL never drops
            time.sleep(1.5)                            # a job thread lives on after it
        threading.Thread(target=hog, name="gil hog", daemon=True).start()
        return {"ok": True}

    added = [APIRoute("/api/test/sleeps", budget_sleeps_on_the_loop, methods=["GET"]),
             APIRoute("/api/test/hogs", budget_hogs_the_gil, methods=["GET"])]
    server.app.router.routes.extend(added)
    try:
        httpx.get(base + "/api/test/sleeps", timeout=30)
        time.sleep(0.3)
        slow = httpx.get(base + "/api/debug/slow").json()
        hold = max((r for r in slow["over_budget"] if r["method"] == "LOOP"),
                   key=lambda r: r["ms"], default=None)
        assert hold is not None and hold["ms"] >= 250, slow["over_budget"]
        assert "budget_sleeps_on_the_loop" in hold["where"]["loop"], hold
        assert _ours(hold)
        server.OVER_BUDGET.clear()

        t0 = time.perf_counter()
        sum(range(1_000_000))
        per = time.perf_counter() - t0
        if per * 60 < 0.25:
            pytest.skip(f"this box sums 60M in {per * 60:.2f} s: too fast to hold 200 ms")
        httpx.get(base + "/api/test/hogs", timeout=30)
        time.sleep(per * 60 + 1.0)
        slow = httpx.get(base + "/api/debug/slow").json()
        holds = [r for r in slow["over_budget"] if r["method"] == "LOOP"]
        assert holds, slow
        hold = max(holds, key=lambda r: r["ms"])
        assert any(b.startswith("gil hog") for b in hold["where"]["busy"]), hold
        assert hold["where"]["busy_cpu_ms"] >= 0.5 * hold["ms"] and _ours(hold), hold
    finally:
        for r in added:
            server.app.router.routes.remove(r)


# ------------------------------------------------------------ the pieces

def test_a_sidecar_is_parsed_once_per_version_and_each_reader_gets_its_own(tmp_path):
    import server
    p = tmp_path / "x.audio.json"
    p.write_text(json.dumps({"clip": "X.MP4", "transcript": [{"t": 1, "text": "hi"}]}))
    a = server.read_json_file(p)
    a["transcript"].append("mine")                      # a reader changes its copy
    b = server.read_json_file(p)
    assert b == {"clip": "X.MP4", "transcript": [{"t": 1, "text": "hi"}]} and b is not a
    assert server._JSON_FILES[str(p)][0][1] == p.stat().st_size
    p.write_text(json.dumps({"clip": "X.MP4", "transcript": []}))   # a new version
    assert server.read_json_file(p)["transcript"] == []
    p.write_text("{not json")
    with pytest.raises(ValueError):
        server.read_json_file(p)
    with pytest.raises(OSError):
        server.read_json_file(tmp_path / "gone.json")


def test_stale_cli_facts_answer_at_once_and_are_asked_again_behind(monkeypatch):
    """`claude --version` + `claude auth status` are 0.4-0.6 s on a loaded box; the
    request that found the cache stale paid them every 30 s (slow /api/backend)."""
    import server
    asked = threading.Event()
    calls = []

    def ask():
        calls.append(time.time())
        asked.wait(5)
        server._CLI_FACTS.update(at=time.time(), facts={"version": "new", "logged_in": True},
                                 gen=server._CLI_FACTS["gen"] + 1)
        return server._CLI_FACTS["facts"]
    monkeypatch.setattr(server, "_cli_facts_ask", ask)
    monkeypatch.setitem(server._CLI_FACTS, "facts", {"version": "old", "logged_in": True})
    monkeypatch.setitem(server._CLI_FACTS, "at", time.time() - server.CLI_FACTS_TTL_S - 1)
    monkeypatch.setitem(server._CLI_FACTS, "asking", False)
    t0 = time.perf_counter()
    assert server.cli_facts()["version"] == "old"
    assert server.cli_facts()["version"] == "old"
    assert time.perf_counter() - t0 < 0.1
    asked.set()
    deadline = time.time() + 5
    while server._CLI_FACTS["facts"]["version"] != "new" and time.time() < deadline:
        time.sleep(0.02)
    assert server.cli_facts()["version"] == "new" and len(calls) == 1


def test_an_upload_the_disk_refuses_fails_whole_and_leaves_nothing(tmp_path, project, monkeypatch):
    """The writer thread's error is the request's: the file is failed, its part file
    gone, the batch ended — as a short body is."""
    from fastapi.testclient import TestClient
    import server

    class Full:
        def __init__(self, f):
            self.f = f

        def write(self, b):
            raise OSError(28, "No space left on device")

        def close(self):
            self.f.close()
    real = server._UploadWriter.__init__
    monkeypatch.setattr(server._UploadWriter, "__init__",
                        lambda self, f, up: real(self, Full(f), up))
    server.configure(None, project["footage"], None, tmp_path, proxies=False,
                     visual=tmp_path / "no-visual")
    data = b"\0" * (9 << 20)                              # more than one UPLOAD_WRITE piece
    with TestClient(server.app, raise_server_exceptions=False) as c:
        plan = c.post("/api/upload", json={"files": [{"name": "full.mp4", "size": len(data)}]}).json()
        r = c.put(f"/api/upload/{plan['batch']}/0", content=data)
        assert r.status_code == 500
        assert c.get(f"/api/job/{plan['batch']}").json()["state"] == "failed"
    up = server.UPLOADS[plan["batch"]]
    assert up["rows"][0]["state"] == "failed" and up["rows"][0]["why"] == "OSError"
    assert not list(tmp_path.rglob("*.part"))


# ------------------------------------------------------------------------------ wave 15
#
# Verdict 11 punch 4 (lane BUDGET): on Killington, 24 requests over the 300 ms budget in a
# session — posters (cut from 4K originals inside their requests; test_preview_building),
# `/api/assist/price` 768 ms, `/api/gap` 587 ms, `/api/firstpass/cancel` 562 ms.

def test_the_price_answers_from_what_it_read_and_reads_again_beside_it(tmp_path, project, monkeypatch):
    """Every pause in the typing asked /api/assist/price, and each re-read the cut and ~45
    sidecars (768 ms during a first pass). Now what it read is kept: a second price reads
    nothing; once the cut changes, the price is still answered at once from what was read,
    and read again beside it; the words still move the price."""
    import server

    edl = tmp_path / "edl.json"
    shutil.copy(project["edl"], edl)
    server.configure(edl, project["footage"], project["sidecars"], tmp_path / "work", proxies=False,
                     visual=tmp_path / "no-visual", assets=project["assets"])
    reads: list[float] = []
    real = server._ask_clips
    gate = threading.Event()
    gate.set()

    def counted(*a, **kw):
        gate.wait(10)
        reads.append(time.monotonic())
        return real(*a, **kw)

    monkeypatch.setattr(server, "_ask_clips", counted)

    def price(text: str) -> dict:
        return json.loads(server.api_assist_price(text, '{"kind": "sequence"}').body)

    short = price("warm it up")
    assert len(reads) == 1                                  # nothing kept yet: read now
    assert short["projected_usd"] > 0 and short["measured"] is False
    longer = price("warm it up " * 200)
    assert len(reads) == 1, "a second price re-read the cut and the clips"
    assert longer["projected_usd"] > short["projected_usd"]
    # the cut changes (a save): answered at once from what was read, read again beside it
    gate.clear()
    cut = json.loads(edl.read_text())
    cut["segments"] = cut["segments"] + [dict(cut["segments"][0], **{"in": 4.0, "out": 5.5})]
    edl.write_text(json.dumps(cut))
    os.utime(edl, ns=(time.time_ns(), time.time_ns() + 10_000_000))
    t0 = time.perf_counter()
    stale = price("warm it up")
    assert time.perf_counter() - t0 < 0.2 and stale == short
    gate.set()
    deadline = time.time() + 10
    while len(reads) < 2 and time.time() < deadline:
        time.sleep(0.02)
    assert len(reads) == 2, "the changed cut was not read again"
    while server._PRICE_INPUTS["busy"] and time.time() < deadline:
        time.sleep(0.02)
    fresh = price("warm it up")
    assert fresh["projected_usd"] > short["projected_usd"], "the new shot is not in the price"
    assert len(reads) == 2
    # another project's clips are never priced: a re-point reads its own
    server.configure(edl, project["footage"], project["sidecars"], tmp_path / "work2", proxies=False,
                     visual=tmp_path / "no-visual", assets=project["assets"])
    price("warm it up")
    assert len(reads) == 3


def test_a_stop_replies_once_its_signals_are_out(tmp_path, project, monkeypatch):
    """The Stop waited for its tools to die (up to procscope.GRACE_S for one that takes its
    SIGTERM slowly): 562 ms on Killington. Now it replies once every SIGTERM is sent, the
    state already `stopped`; the SIGKILL after the grace comes beside it."""
    import server
    from roughcut import procscope

    server.configure(None, project["footage"], None, tmp_path / "work", proxies=False,
                     visual=tmp_path / "no-visual")
    server.FIRSTPASS.clear()
    server.INDEXES.clear()
    scope = procscope.Scope("first pass test")
    # a tool that ignores its SIGTERM, as a busy encode or a model CLI mid-write can
    with procscope.active(scope):
        stubborn = subprocess.Popen([sys.executable, "-c",
                                     "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                                     "print('up', flush=True); time.sleep(60)"],
                                    stdout=subprocess.PIPE, text=True)
    assert stubborn.stdout.readline().strip() == "up"
    server.FIRSTPASS["t1"] = {"id": "t1", "state": "running", "stage": "index", "scope": scope,
                              "steps": {k: {"state": "pending"} for k in ("index", "picks", "cut")},
                              "started": time.time(), "index_job": None}
    try:
        t0 = time.perf_counter()
        r = json.loads(server.api_firstpass_cancel().body)
        took = time.perf_counter() - t0
        assert r["state"] == "stopped" and r["stopped"] is True
        assert took < 0.3, f"the Stop took {took * 1000:.0f} ms to reply"
        assert server.FIRSTPASS["t1"]["state"] == "cancelled" and scope.cancelled
        assert stubborn.poll() is None, "killed before the grace: not the stubborn case"
        assert stubborn.wait(procscope.GRACE_S + 3) is not None      # the SIGKILL beside it
    finally:
        if stubborn.poll() is None:
            stubborn.kill()
        server.FIRSTPASS.clear()


def test_a_gap_is_cut_from_the_bins_black_master(tmp_path, project, monkeypatch):
    """A gap's black was an encode inside /api/gap (~35 ms a second of black; 587 ms for a
    gap on a loaded Killington box). With the bin's black master on disk a gap is its head,
    a stream copy: no encode, the length on the frame grid, black and silent as before."""
    from fastapi.testclient import TestClient
    import server

    server.configure(None, project["footage"], None, tmp_path / "work", proxies=False,
                     visual=tmp_path / "no-visual")
    monkeypatch.setattr(server, "GAP_MASTER_S", 30.0)
    assert server.ensure_black_master() is not None
    runs: list[list[str]] = []
    real_run = subprocess.run

    def watched(cmd, *a, **kw):
        # this bin's generated clips' ffmpegs only (other tests' threads may run their own)
        if isinstance(cmd, list) and cmd and cmd[0] == "ffmpeg" \
                and str(server.generated_dir()) in " ".join(map(str, cmd)):
            runs.append(cmd)
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(server.subprocess, "run", watched)
    with TestClient(server.app) as c:
        t0 = time.perf_counter()
        r = c.post("/api/gap", json={"seconds": 7.33})
        took = time.perf_counter() - t0
    assert r.status_code == 200, r.text
    g = r.json()
    assert len(runs) == 1 and "copy" in runs[0] and "libx264" not in runs[0], runs
    assert not any("lavfi" in x for x in runs[0])
    print(f"a 7.33 s gap from the master in {took * 1000:.0f} ms")
    path = server.generated_dir() / g["clip"]
    probe = json.loads(real_run(["ffprobe", "-v", "error", "-show_entries",
                                 "stream=codec_type,nb_frames,duration,width,height",
                                 "-of", "json", str(path)], capture_output=True, text=True).stdout)
    v = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert int(v["nb_frames"]) == 176 and (v["width"], v["height"]) == (server.GEN_W, server.GEN_H)
    assert any(s["codec_type"] == "audio" for s in probe["streams"])
    assert g["record"]["duration"] == pytest.approx(176 / server.GEN_FPS, abs=0.01)
    assert g["record"]["summary"]["generated"] == "generated: black 7.3 s"
    # past the master's length: the encode, as before
    runs.clear()
    with TestClient(server.app) as c:
        assert c.post("/api/gap", json={"seconds": 31.0}).status_code == 200
    assert runs and "libx264" in runs[0]


def test_a_clips_free_facts_are_one_ffprobe_and_the_list_never_waits_long(tmp_path, project, monkeypatch):
    """A clip just imported had its free facts probed twice, one ffprobe after the other
    (length, capture time and telemetry; then the picture's facts), and /api/clips waited
    up to a second for them: 304-736 ms on Killington right after an import. Now one
    ffprobe gives both, and the list waits at most CLIP_FACTS_WAIT_S (0.2 s) — a clip
    still being probed comes with null facts and the board asks again 0.3 s later."""
    import server

    server.configure(None, project["footage"], None, tmp_path / "work", proxies=False,
                     visual=tmp_path / "no-visual")
    probes: list[list[str]] = []
    real_run = subprocess.run
    clip = server.footage_clips()[0]
    src = str(server.clip_path(clip))

    def watched(cmd, *a, **kw):
        if isinstance(cmd, list) and cmd and cmd[0] == "ffprobe" and src in cmd:
            probes.append(cmd)
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(server.subprocess, "run", watched)
    for d in ("durations", "capture", "telemetry", "clip_facts"):
        server.STATE.setdefault(d, {}).pop(clip, None)
    server.MEDIA["facts"].pop(next((k for k in list(server.MEDIA["facts"]) if k[0] == src), None), None)
    facts = server._probe_clip_facts(clip, server.STATE["footage"])
    assert len(probes) == 1, f"{len(probes)} ffprobes for one clip's facts: {probes}"
    assert facts["fps"] and facts["width"] == 320 and facts["codec"] == "h264", facts
    assert server.STATE["durations"][clip] and server.STATE["capture"][clip]
    assert server.media_facts(server.clip_path(clip))["vcodec"] == "h264"
    assert len(probes) == 1, "media_facts probed again"
    assert server.CLIP_FACTS_WAIT_S <= 0.2


def test_the_status_never_ffprobes_a_clip_the_warm_up_has_not_reached(tmp_path, project, monkeypatch):
    """The first /api/status after a start (or an import) priced the look by every clip's
    length, ffprobing the ones the facts warm-up had not reached: 300-970 ms on Killington.
    It reads the lengths known; one not known yet counts a sheet, and the status is read
    again once it lands (the snapshot's key counts the known lengths)."""
    import server

    server.configure(None, project["footage"], None, tmp_path / "work", proxies=False,
                     visual=tmp_path / "no-visual")
    probes: list[list[str]] = []
    real_run = subprocess.run

    def watched(cmd, *a, **kw):
        if isinstance(cmd, list) and cmd and cmd[0] == "ffprobe":
            probes.append(cmd)
        return real_run(cmd, *a, **kw)

    deadline = time.time() + 20                       # the configure's own warm-up, done
    while time.time() < deadline and len(server.STATE.get("clip_facts") or {}) < len(server.footage_clips()):
        time.sleep(0.05)
    clip = server.footage_clips()[0]
    known = server.STATE["durations"].pop(clip)
    monkeypatch.setattr(server.subprocess, "run", watched)
    monkeypatch.setattr(server, "load_sidecar", lambda c: {})       # as a clip not heard yet
    key_before = server._snap_key()
    v = server.visual_status()
    assert not probes, probes
    assert v["pending"] and v["coarse_calls"] >= len(v["pending"])
    server.STATE["durations"][clip] = known
    assert server._snap_key() != key_before, "a length landing does not refresh the status"


def test_a_request_over_the_budget_says_what_the_process_ran_and_the_load(tmp_path, project, monkeypatch):
    """An over-budget row names what the whole process ran meanwhile and the box's load, so
    a request the server worked through (hundreds of ms of CPU) reads apart from one the
    box starved (a few): the Killington import's rows at load 20-26 were the second kind."""
    from fastapi.testclient import TestClient
    import server

    server.configure(None, project["footage"], None, tmp_path / "work", proxies=False,
                     visual=tmp_path / "no-visual")
    real = server._price_inputs

    def slow():
        time.sleep(0.35)                        # waiting, not working
        return real()

    monkeypatch.setattr(server, "_price_inputs", slow)
    server.OVER_BUDGET.clear()
    with TestClient(server.app) as c:
        assert c.get("/api/assist/price", params={"text": "x"}).status_code == 200
    rows = [r for r in server.OVER_BUDGET if r["path"] == "/api/assist/price"]
    assert rows, list(server.OVER_BUDGET)
    r = rows[-1]
    assert r["ms"] >= 300 and "load" in r and r["load"] >= 0, r
    assert 0 <= r["proc_cpu_ms"] < r["ms"] / 2, r       # it slept: little of it was CPU
