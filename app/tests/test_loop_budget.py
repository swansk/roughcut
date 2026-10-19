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
