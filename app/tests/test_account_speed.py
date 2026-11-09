"""The first pass's account answers in <= 300 ms, however busy the box is (wave 21 FPPANEL,
verdict 16 punch 10: api_firstpass_account measured 320-508 ms, 6 over budget, load 8-15).

Its heavy half (every clip's coverage, the certainty of every moment, the 3080's and
Claude's counts) is cached on the sidecars' mtimes. A request that finds a changed bin
serves the last build and refreshes on a worker thread, waiting for it at most
`server.ACCT_WAIT_S`; the request never builds on the event loop (the endpoint is a plain
`def`, run on the threadpool)."""

from __future__ import annotations

import inspect
import time

import pytest


def _get(client):
    t = time.perf_counter()
    r = client.get("/api/firstpass/account")
    assert r.status_code == 200
    return time.perf_counter() - t, r.json()


def test_the_endpoint_is_not_a_coroutine():
    import server
    assert not inspect.iscoroutinefunction(server.api_firstpass_account)


def test_a_second_call_on_an_unchanged_bin_reuses_the_build(client, monkeypatch):
    import server
    _get(client)
    built = []
    real = server._account_build
    monkeypatch.setattr(server, "_account_build", lambda sig: built.append(1) or real(sig))
    for _ in range(3):
        dt, d = _get(client)
        assert dt < 0.15, dt
    assert built == [], "an unchanged bin was rebuilt"
    assert d["inputs"]["clips"] == 3


def test_a_changed_bin_answers_at_once_with_the_last_build_and_refreshes(client, monkeypatch):
    import server
    _ = _get(client)
    gate = {"slow": True}
    real = server._account_build

    def slow(sig):
        if gate["slow"]:
            time.sleep(1.0)                    # a build that is far over budget
        return real(sig)
    monkeypatch.setattr(server, "_account_build", slow)
    # a sidecar changes: the next answer is the old build, inside the budget
    side = next(server.STATE["sidecars"].glob("*.audio.json"))
    side.touch()
    dt, d = _get(client)
    assert dt <= 0.3, f"waited {dt:.2f}s for a rebuild"
    assert d["inputs"]["clips"] == 3
    # the refresh finishes on its worker, and the next call sees the new signature
    th = server._ACCT["thread"]
    if th is not None:
        th.join(5)
    gate["slow"] = False
    dt, _d = _get(client)
    assert dt < 0.3
    assert server._ACCT["sig"] == server.account_sig()


def test_the_cache_never_crosses_bins(client):
    import server
    _get(client)
    sig = server._ACCT["sig"]
    assert str(server.STATE["work"]) in sig
