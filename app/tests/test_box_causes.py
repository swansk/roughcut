"""Wave 16 SRVBUDGET2 (verdict 12, punch 7): a hold or a slow request says what the box did.

Verdict 12 counted 24 over-budget entries on Killington — posters 0.4-0.9 s, `/api/firstpass`
0.4-0.67 s, loop holds 227-651 ms — "mostly with the loop barely on CPU while the box
swapped", and the watcher could not say more: its sampler is a Python thread and needs the
GIL itself. The kernel can say it. Every hold and every request over the budget now
carries, over its span, the loop thread's run-queue wait (`rq_ms`: runnable, not run), the
process's major faults (`majflt`: pages read back from swap) and this process's garbage
collection time (`gc_ms`), and `cause` names the one that explains it ("gc", "loop",
"thread", "server", "starved", "faults", "?"). These run a real uvicorn loop (the
TestClient bypasses the watcher), the suite's `live` board from test_loop_budget.
"""

from __future__ import annotations

import gc
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from test_loop_budget import live  # noqa: E402,F401 — the fixture


def test_the_cause_is_the_largest_honest_share():
    import server
    c = server._cause
    assert c(400, {"gc_ms": 250, "loop_cpu_ms": 300}) == "gc"      # GC runs on some thread
    assert c(400, {"loop_cpu_ms": 300, "rq_ms": 10}) == "loop"
    assert c(400, {"loop_cpu_ms": 2, "busy_cpu_ms": 380}) == "thread"
    assert c(400, {"loop_cpu_ms": 2, "busy_cpu_ms": 3, "rq_ms": 390}) == "starved"
    assert c(400, {"loop_cpu_ms": 2, "rq_ms": 30, "majflt": 41}) == "faults"
    assert c(400, {"loop_cpu_ms": 2, "rq_ms": 30, "majflt": 0}) == "?"
    # a request (no loop_cpu_ms): the process's own CPU through it is the server's
    assert c(400, {"proc_cpu_ms": 350, "rq_ms": 5}) == "server"
    assert c(400, {"proc_cpu_ms": 20, "rq_ms": 300}) == "starved"


def test_the_box_is_read_from_proc_and_gc_is_timed():
    import server
    rq, maj, gcms = server._box_now()
    if sys.platform.startswith("linux"):
        assert isinstance(maj, int) and maj >= 0
    n0, ms0 = server.GC_WATCH["n"], server.GC_WATCH["ms"]
    gc.collect()
    assert server.GC_WATCH["n"] == n0 + 1 and server.GC_WATCH["ms"] >= ms0
    since = server._box_since((rq, maj, gcms))
    assert since["gc_ms"] >= 0
    if maj is not None:
        assert since["majflt"] >= 0


def test_a_hold_and_a_slow_request_carry_what_the_box_did(live):
    from fastapi.routing import APIRoute
    base, server = live["base"], live["server"]
    keep: list = []

    def budget_waits_in_a_thread():
        time.sleep(0.4)                          # a sync handler: on a worker thread
        return {"ok": True}

    async def budget_collects_on_the_loop():
        gc.collect()                             # a full collection, the GIL held
        return {"ok": True}

    added = [APIRoute("/api/test/waits", budget_waits_in_a_thread, methods=["GET"]),
             APIRoute("/api/test/collects", budget_collects_on_the_loop, methods=["GET"])]
    server.app.router.routes.extend(added)
    try:
        time.sleep(0.5)                          # the sampler has a baseline
        httpx.get(base + "/api/test/waits", timeout=30)
        slow = httpx.get(base + "/api/debug/slow").json()
        row = next(r for r in slow["over_budget"] if r["path"] == "/api/test/waits")
        assert row["cause"] in ("?", "starved", "faults"), row
        assert "gc_ms" in row and "majflt" in row and "rq_ms" in row, row
        assert row["rq_ms"] >= 0 and row["majflt"] >= 0, row
        assert slow["gc"]["n"] >= 0 and slow["rss_mb"] > 0, slow["gc"]

        # a heap of tracked objects nobody froze: one full collection walks all of them
        keep.extend([[i] for i in range(1_500_000)])
        t = time.perf_counter()
        gc.collect()
        per = time.perf_counter() - t
        if per < 0.05:
            pytest.skip(f"a full collection of 1.5M objects takes {per * 1000:.0f} ms here")
        for k in ("all", "api", "loop"):
            server.SLOWEST[k] = None
        httpx.get(base + "/api/test/collects", timeout=30)
        time.sleep(0.3)
        slow = httpx.get(base + "/api/debug/slow").json()
        hold = slow["slowest_loop"]
        assert hold and hold["ms"] >= 20 and hold["where"]["cause"] == "gc", hold
        assert hold["where"]["gc_ms"] >= 0.5 * hold["ms"], hold
        assert slow["gc"]["longest"]["ms"] >= 0.5 * per * 1000, slow["gc"]
    finally:
        keep.clear()
        for r in added:
            server.app.router.routes.remove(r)
