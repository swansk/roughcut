"""The status read right after an import answers at once (wave 18 POLISH12, verdict 14 punch 10).

Verdict 14, on Killington: the one over-budget request in the judge's whole session was
GET /api/firstpass at 525.9 ms, `cause: waiting` on "the firstpass snapshot's rebuild", 0.7 s
after its own import POST. Wave 17 (SNAP) had made such a read answer with the last snapshot
while an import settles, but two things refused it that answer though neither changes what
it says:

  * the judge's server asks the CLI about itself every CLI_FACTS_TTL_S (SNAP's probe ran
    --no-probe, which never does), and the snapshot key's clock bucket and ask count moved
    with it. A late answer now compares what the CLI said, not when it was asked
    (`_snap_lag_view`); a real change in what it says still reads fresh;
  * a write that changed nothing the answer reads (a test route here; the Index panel's
    price on the judge's board) left the stored answer's write count behind for good, since
    a snapshot hit served it without a rebuild. A hit is the answer as of now, so it takes
    the write count it was checked at; a write that changed what it reads still reads back
    fresh (test_import_snap). The Index panel's price itself writes nothing: not a write.

A late answer says so in its body as well as its header: `"importing": true` (the board asks
again until the answer knows the new clips: import.js `landed`, test_import_status_ui).

These run a real uvicorn loop (test_loop_budget's `live` board): the TestClient never takes
the late path. Every model call is scripted; the CLI's two free calls are a stand-in.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from test_import_snap import _frozen, _import_one, _waited  # noqa: E402
from test_loop_budget import live  # noqa: E402,F401 — the fixture

FACTS = {"version": "2.1.289", "logged_in": True, "auth": "oauth_token", "plan": "max"}


def _slow_firstpass(server, monkeypatch) -> threading.Event:
    """The firstpass snapshot's rebuild takes 1.5 s once the event is set."""
    real = server.firstpass_state
    slow = threading.Event()

    def firstpass_state():
        if slow.is_set():
            time.sleep(1.5)
        return real()

    monkeypatch.setattr(server, "firstpass_state", firstpass_state)
    return slow


def _cli_asked_again(server, monkeypatch) -> dict:
    """The CLI's facts on, as a server started without --no-probe has them, asked again
    behind every read once stale (here every 0.4 s until the test puts back the 30 s): the
    same answer each time, unless the test changes `said`."""
    said = dict(FACTS)

    def ask():
        time.sleep(0.05)
        server._CLI_FACTS.update(at=time.time(), facts=dict(said), gen=server._CLI_FACTS["gen"] + 1)
        return dict(said)

    monkeypatch.setitem(server.STATE, "cli_facts", True)
    monkeypatch.setattr(server, "CLI_FACTS_TTL_S", 0.4)
    monkeypatch.setattr(server, "_cli_facts_ask", ask)
    monkeypatch.setitem(server._CLI_FACTS, "facts", dict(said))
    monkeypatch.setitem(server._CLI_FACTS, "at", time.time())
    monkeypatch.setitem(server._CLI_FACTS, "asking", False)
    return said


def _late(r) -> bool:
    return r.headers.get("x-roughcut-snapshot") == "last"


def test_a_late_answer_says_importing_in_its_body():
    import server
    assert server._importing(b'{"state":"done","new_count":0}') == \
        b'{"importing":true,"state":"done","new_count":0}'
    assert server._importing(b"{}") == b'{"importing":true}'
    assert server._importing(b"[1,2]") == b"[1,2]"                 # not an object: as it was


def test_the_status_read_after_an_import_answers_at_once_though_the_cli_was_asked_again(
        live, project, monkeypatch):
    """The judge's row: the CLI's facts were asked again (the key's clock bucket rolled and
    its ask count moved, the facts the same) between the last firstpass snapshot and the
    read right after the import POST. That read answers within the budget with the last
    snapshot, `importing` in its body; the rebuild lands and the next read has it. A real
    change in what the CLI says is not answered late."""
    base, server = live["base"], live["server"]
    said = _cli_asked_again(server, monkeypatch)
    slow = _slow_firstpass(server, monkeypatch)
    first = httpx.get(base + "/api/firstpass", timeout=30)
    assert first.status_code == 200 and not _late(first)
    # the CLI is asked again behind the reads: its clock bucket rolls, its count moves
    gen0 = server._CLI_FACTS["gen"]
    deadline = time.time() + 10
    while server._CLI_FACTS["gen"] == gen0 and time.time() < deadline:
        server.cli_facts()
        time.sleep(0.1)
    assert server._CLI_FACTS["gen"] > gen0 and server._CLI_FACTS["facts"] == FACTS
    # back to its 30 s (the bucket moves once more): a rebuild that lands is a hit again
    monkeypatch.setattr(server, "CLI_FACTS_TTL_S", 30.0)
    slow.set()
    added = _import_one(base, live["tmp"], project, "KX_81.MP4")
    assert added == ["KX_81.MP4"] and server.import_settling()
    with _frozen():
        t = time.perf_counter()
        r = httpx.get(base + "/api/firstpass", timeout=30)
        took = time.perf_counter() - t
    assert r.status_code == 200 and _late(r), (r.headers, f"{took * 1000:.0f} ms")
    assert took < 0.6, f"{took * 1000:.0f} ms"
    assert r.json() == {"importing": True, **first.json()}
    # the rebuild lands on its own: a later read knows the new clip, without `importing`
    deadline = time.time() + 20
    while time.time() < deadline:
        r2 = httpx.get(base + "/api/firstpass", timeout=30)
        if not _late(r2):
            break
        time.sleep(0.1)
    assert not _late(r2) and "importing" not in r2.json()
    assert not _waited(base, "/api/firstpass"), "no read waited on the import's rebuild"

    # the CLI now says something else (signed out): no late answer stands in for that
    said["logged_in"] = False
    server._CLI_FACTS.update(facts=dict(said), gen=server._CLI_FACTS["gen"] + 1)
    server.media_changed()
    r3 = httpx.get(base + "/api/firstpass", timeout=30)
    assert not _late(r3) and "importing" not in r3.json()
    rows = _waited(base, "/api/firstpass")
    assert rows and rows[-1]["waited_on"].get("the firstpass snapshot's rebuild", 0) >= 1000, rows
    slow.clear()


def test_a_write_that_changed_nothing_the_answer_reads_leaves_it_answerable_late(live, project, monkeypatch):
    """A client POST that writes nothing the firstpass snapshot reads, then a read (a hit:
    the answer as of now), then an import: the read right after it answers late, at once.
    Before, the hit kept the stored answer's old write count and the read waited 1.5 s."""
    from fastapi.routing import APIRoute
    base, server = live["base"], live["server"]
    slow = _slow_firstpass(server, monkeypatch)
    route = APIRoute("/api/test/writes-nothing", lambda: {"ok": True}, methods=["POST"])
    server.app.router.routes.append(route)
    try:
        first = httpx.get(base + "/api/firstpass", timeout=30)
        assert not _late(first)
        w0 = server.SNAP["writes"]
        assert httpx.post(base + "/api/test/writes-nothing", timeout=30).status_code == 200
        assert server.SNAP["writes"] > w0, "a POST counts as a write"
        again = httpx.get(base + "/api/firstpass", timeout=30)
        assert again.content == first.content and not _late(again)
        assert server.SNAP["bytes"]["firstpass"][2] == server.SNAP["writes"], \
            "the hit counts the writes it was checked at"
        slow.set()
        added = _import_one(base, live["tmp"], project, "KX_82.MP4")
        assert added == ["KX_82.MP4"]
        with _frozen():
            t = time.perf_counter()
            r = httpx.get(base + "/api/firstpass", timeout=30)
            took = time.perf_counter() - t
        assert _late(r) and took < 0.6, (r.headers, f"{took * 1000:.0f} ms")
        assert r.json()["importing"] is True
    finally:
        server.app.router.routes.remove(route)
        slow.clear()


def test_the_index_panels_price_is_not_a_write(live):
    """POST /api/index/price starts nothing and writes nothing (its docstring): pricing a
    re-look before an import never costs the import its late answers."""
    base, server = live["base"], live["server"]
    w0 = server.SNAP["writes"]
    r = httpx.post(base + "/api/index/price", json={"clips": ["CLIP_A.MP4"], "stages": ["hear"]},
                   timeout=30)
    assert r.status_code in (200, 400), r.text
    assert server.SNAP["writes"] == w0
