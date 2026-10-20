"""Honest causes (wave 17, verdict 13 punch 10).

Verdict 13, on Killington: a mid-session import of two 4K clips left two requests over the
300 ms budget — GET /api/firstpass 482 ms (`snap_respond` waited on its snapshot's rebuild,
on a worker thread) and GET /api/clips 343 ms (it waited on the new clips' facts probe) —
and both read cause "faults", on 3 and 8 major faults, because the cause had no class for a
handler that waits. Now a request whose time was a worker thread or a subprocess reads
"waiting", with what it waited on (`waited_on`); a wait the client asked for (the media
status's `?at=` hint) is the client's, like a body's upload: `asked_ms`, not in `ms`.

These run a real uvicorn loop (the suite's `live` board from test_loop_budget): the
TestClient bypasses the loop watcher.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from test_loop_budget import live  # noqa: E402,F401 — the fixture


def test_a_request_that_waited_on_a_thread_or_a_process_reads_waiting():
    import server
    c = server._cause
    # the judge's two rows: mostly waiting, a few faults, little CPU
    assert c(482, {"wait_ms": 420, "majflt": 8, "proc_cpu_ms": 62, "rq_ms": 10}) == "waiting"
    assert c(343, {"wait_ms": 300, "majflt": 3, "proc_cpu_ms": 50}) == "waiting"
    # the waited-on thread's CPU is the wait's: still "waiting", not "server"
    assert c(400, {"wait_ms": 380, "proc_cpu_ms": 390}) == "waiting"
    # a collection still explains what it held; a short wait explains nothing
    assert c(400, {"wait_ms": 300, "gc_ms": 250}) == "gc"
    assert c(400, {"wait_ms": 100, "majflt": 5, "proc_cpu_ms": 20}) == "faults"
    assert c(400, {"wait_ms": 100, "rq_ms": 300}) == "starved"
    # Killington at load 25-32: a 390 ms read that waited 207 ms while the loop itself sat
    # 390 ms in the run queue was the box's
    assert c(390, {"wait_ms": 207, "rq_ms": 390, "proc_cpu_ms": 10}) == "starved"
    assert c(390, {"wait_ms": 300, "rq_ms": 250, "proc_cpu_ms": 10}) == "waiting"
    # a loop hold never has one
    assert c(400, {"loop_cpu_ms": 2, "rq_ms": 30, "majflt": 41}) == "faults"


def test_waits_are_named_and_an_asked_wait_is_the_clients(live):
    """A sync handler waiting on a worker thread, one waiting on a subprocess, and one
    whose wait the client asked for, on the real loop."""
    from fastapi.routing import APIRoute
    base, server = live["base"], live["server"]

    def snap_waits_on_a_thread():
        done = threading.Event()
        threading.Thread(target=lambda: (time.sleep(0.45), done.set()), daemon=True).start()
        with server.req_wait("a test thread"):
            done.wait(5)
        return {"ok": True}

    def snap_waits_on_a_process():
        with server.req_wait("a test process"):
            subprocess.run(["sleep", "0.45"], check=True)
        return {"ok": True}

    def snap_waits_as_asked():
        with server.req_wait("a test hint", asked=True):
            time.sleep(0.45)
        return {"ok": True}

    added = [APIRoute("/api/test/snap-thread", snap_waits_on_a_thread, methods=["GET"]),
             APIRoute("/api/test/snap-process", snap_waits_on_a_process, methods=["GET"]),
             APIRoute("/api/test/snap-asked", snap_waits_as_asked, methods=["GET"])]
    server.app.router.routes.extend(added)
    try:
        time.sleep(0.5)                                  # the sampler has a baseline
        for p in ("thread", "process", "asked"):
            assert httpx.get(f"{base}/api/test/snap-{p}", timeout=30).status_code == 200
        slow = httpx.get(base + "/api/debug/slow").json()
        rows = {r["path"]: r for r in slow["over_budget"]}
        for p, what in (("thread", "a test thread"), ("process", "a test process")):
            r = rows[f"/api/test/snap-{p}"]
            assert r["cause"] == "waiting", r
            assert r["wait_ms"] >= 400 and r["waited_on"][what] >= 400, r
            assert "majflt" in r and "rq_ms" in r, r
        assert "/api/test/snap-asked" not in rows, "the client's own wait is not over budget"
        top = slow["slowest_api"]
        assert top["path"] != "/api/test/snap-asked" or top["ms"] < 300, top
        # a fast request carries no wait at all
        assert all("wait_ms" not in r for r in slow["over_budget"]
                   if not r["path"].startswith("/api/test/snap-"))
    finally:
        for r in added:
            server.app.router.routes.remove(r)
