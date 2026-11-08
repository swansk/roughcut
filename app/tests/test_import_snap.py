"""Import-time requests and honest causes (wave 17, verdict 13 punch 10).

Verdict 13, on Killington: a mid-session import of two 4K clips left two requests over the
300 ms budget — GET /api/firstpass 482 ms (`snap_respond` waited on its snapshot's rebuild,
on a worker thread) and GET /api/clips 343 ms (it waited on the new clips' facts probe) —
and both read cause "faults", on 3 and 8 major faults, because the cause had no class for a
handler that waits. Now:

  * while an import settles, a status snapshot whose rebuild is slow answers with the last
    one (`X-Roughcut-Snapshot: last`) and the rebuild carries on on its own thread; the
    next read has it. Never in place of the client's own write, nor outside an import;
  * the clip list does the same, with a row of null facts for a clip it did not have yet
    and `probing` counting it, so the board asks again 0.3 s on;
  * a request whose time was a worker thread or a subprocess reads "waiting", with what it
    waited on (`waited_on`); a wait the client asked for (the media status's `?at=` hint)
    is the client's, like a body's upload: `asked_ms`, not in `ms`;
  * (wave 19, verdict 15 punch 10) a change in what the CLI says about itself while an
    import settles — signed out, a new version — is never answered from a snapshot of the
    old word: not late, and not by joining a build begun before the change.

These run a real uvicorn loop (the suite's `live` board from test_loop_budget): the
TestClient bypasses the loop watcher. Every model call is scripted; the imported clip is a
copy of a fixture clip under tmp_path.
"""

from __future__ import annotations

import gc
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
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


@contextmanager
def _frozen():
    """The heap as the server runs it (main() freezes what is alive at start): the suite's
    own objects are not walked by a collection in the middle of a timed read."""
    gc.collect()
    gc.freeze()
    try:
        yield
    finally:
        gc.unfreeze()


def _import_one(base: str, tmp: Path, project, name: str = "KX_77.MP4") -> list[str]:
    card = tmp / "card"
    card.mkdir(exist_ok=True)
    shutil.copy(project["footage"] / "CLIP_B.MP4", card / name)
    r = httpx.post(base + "/api/import", json={"paths": [str(card / name)]}, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()["added"]


def _waited(base: str, path: str) -> list:
    """`path`'s reads over the budget that waited on a build (a collection of the suite's
    own heap — it is not frozen here, as the server's is — is not one)."""
    slow = httpx.get(base + "/api/debug/slow").json()
    return [r for r in slow["over_budget"] if r["path"] == path and r.get("wait_ms")]


def test_an_imports_slow_snapshot_rebuild_never_holds_a_request(live, project, monkeypatch):
    """The judge's /api/firstpass row, made deterministic: the snapshot's rebuild takes 1.5
    s once an import lands. The read answers within the budget with the last snapshot and
    says so; the rebuild lands on its own thread and the next read has it. A client's own
    write is read back fresh — waited for, its row "waiting" on the rebuild — and outside
    an import a slow rebuild is waited for as before."""
    base, server = live["base"], live["server"]
    real = server.firstpass_state
    slow = threading.Event()
    built: list = []

    def firstpass_state():
        if slow.is_set():
            time.sleep(1.5)
        out = real()
        built.append(time.time())
        return out

    monkeypatch.setattr(server, "firstpass_state", firstpass_state)
    first = httpx.get(base + "/api/firstpass", timeout=30)
    assert first.status_code == 200 and "x-roughcut-snapshot" not in first.headers
    slow.set()
    added = _import_one(base, live["tmp"], project)
    assert added == ["KX_77.MP4"] and server.import_settling()
    with _frozen():
        t = time.perf_counter()
        r = httpx.get(base + "/api/firstpass", timeout=30)
        took = time.perf_counter() - t
    assert r.status_code == 200 and r.headers.get("x-roughcut-snapshot") == "last", r.headers
    # the last snapshot, as it was, said to be from before the import (wave 18 POLISH12)
    assert r.json() == {"importing": True, **first.json()}, "the last snapshot, as it was"
    assert took < 0.6, f"{took * 1000:.0f} ms"
    assert httpx.get(base + "/api/debug/slow").json()["late_answers"]["firstpass"] >= 1
    # the rebuild lands on its own: a later read is the new answer
    deadline = time.time() + 20
    while time.time() < deadline:
        r2 = httpx.get(base + "/api/firstpass", timeout=30)
        if "x-roughcut-snapshot" not in r2.headers:
            break
        time.sleep(0.1)
    assert "x-roughcut-snapshot" not in r2.headers and r2.json()["state"]
    assert not _waited(base, "/api/firstpass"), "no read waited on the import's rebuild"

    # the client's own write is read back fresh: waited for, and the row says on what
    segs = httpx.get(base + "/api/project").json()["segments"]
    assert httpx.put(base + "/api/project", json={"segments": segs[:-1], "story": ""},
                     timeout=30).status_code == 200
    r3 = httpx.get(base + "/api/firstpass", timeout=30)
    assert "x-roughcut-snapshot" not in r3.headers
    rows = _waited(base, "/api/firstpass")
    assert rows and rows[-1]["cause"] == "waiting", rows
    assert rows[-1]["waited_on"].get("the firstpass snapshot's rebuild", 0) >= 1000, rows[-1]

    # outside an import, a slow rebuild is waited for (never an old answer)
    server.SNAP["import"] = None
    assert not server.import_settling()
    server.media_changed()
    r4 = httpx.get(base + "/api/firstpass", timeout=30)
    assert "x-roughcut-snapshot" not in r4.headers
    slow.clear()


def test_the_clip_list_answers_from_what_is_known_during_an_import(live, project, monkeypatch):
    """/api/clips while an import settles and its build is slow: the last list, the new
    clip as a row of null facts, `probing` counting it, within the budget; the build's list
    follows on the next read. Outside an import the build is waited for."""
    base, server = live["base"], live["server"]
    real = server.clips_payload
    slow = threading.Event()

    def clips_payload():
        if slow.is_set():
            time.sleep(1.5)
        return real()

    monkeypatch.setattr(server, "clips_payload", clips_payload)
    deadline = time.time() + 30
    while time.time() < deadline:
        before = httpx.get(base + "/api/clips", timeout=30).json()
        if not before["probing"]:
            break
        time.sleep(0.2)
    names = [c["clip"] for c in before["clips"]]
    slow.set()
    added = _import_one(base, live["tmp"], project, "KX_78.MP4")
    with _frozen():
        t = time.perf_counter()
        r = httpx.get(base + "/api/clips", timeout=30)
        took = time.perf_counter() - t
    assert r.headers.get("x-roughcut-snapshot") == "last", r.headers
    assert took < 0.6, f"{took * 1000:.0f} ms"
    d = r.json()
    rows = {c["clip"]: c for c in d["clips"]}
    assert set(rows) == set(names) | set(added)
    new = rows[added[0]]
    assert new["duration"] is None and new["fps"] is None and new["poster"].startswith("/media/poster/")
    assert new["source"] == "import" and new["path"].endswith("KX_78.MP4"), new
    assert d["probing"] >= 1, "the board asks again in 0.3 s"
    assert httpx.get(base + "/api/debug/slow").json()["late_answers"]["clips"] >= 1
    assert all(rows[n] == next(c for c in before["clips"] if c["clip"] == n) for n in names)
    slow.clear()
    deadline = time.time() + 30
    while time.time() < deadline:
        r2 = httpx.get(base + "/api/clips", timeout=30)
        d2 = r2.json()
        if "x-roughcut-snapshot" not in r2.headers and not d2["probing"]:
            break
        time.sleep(0.2)
    got = {c["clip"]: c for c in d2["clips"]}
    assert got[added[0]]["duration"] and got[added[0]]["fps"], got[added[0]]
    assert not _waited(base, "/api/clips"), "no read waited on the import's build"

    # outside an import the list is built for the request, however long it takes
    server.SNAP["import"] = None
    slow.set()
    r3 = httpx.get(base + "/api/clips", timeout=30)
    slow.clear()
    assert "x-roughcut-snapshot" not in r3.headers
    rows = _waited(base, "/api/clips")
    assert rows and rows[-1]["cause"] == "waiting", rows
    assert rows[-1]["waited_on"].get("the clip list's build", 0) >= 1000, rows[-1]


# ---------------------------------------------------------------- the CLI's word (wave 19 POLISH13)
#
# Verdict 15, punch 10: the stale-read rule tested where it is widest. POLISH12 let a late
# answer stand across a re-ask of the CLI's facts that came back the same (only what the CLI
# said counts, not its 30 s clock bucket or its ask count): the late path is widest exactly
# there. Here the CLI really changes its word while an import settles — a `claude` first on
# PATH answers the server's two free questions about itself (`--version`, `auth status`)
# from files the test rewrites and refuses anything else, so the real `_cli_facts_ask` and
# its background re-ask (`cli_facts_refresh`, kicked by the banner's /api/backend poll) run
# — and no read that begins once the server knows the new word is answered from a snapshot
# of the old one: not late, and not by joining a build begun before the change.

SIGNED_IN = {"version": "2.1.289", "logged_in": True, "plan": "max"}
SIGNED_OUT = {"version": "2.1.289", "logged_in": False, "plan": None}
UPDATED = {"version": "2.1.300", "logged_in": True, "plan": "max"}     # a new CLI: new models


def _stand_in_cli(tmp: Path, monkeypatch):
    """`say(facts)` sets what the stand-in `claude` answers; it logs every ask."""
    import json
    import os

    home = tmp / "cli-bin"
    home.mkdir()
    exe = home / "claude"
    exe.write_text("#!/bin/sh\n"
                   'd=$(dirname "$0")\n'
                   'printf "%s\\n" "$*" >> "$d/asked.log"\n'
                   'case "$*" in\n'
                   '  --version) cat "$d/version" ;;\n'
                   '  "auth status") cat "$d/auth.json" ;;\n'
                   '  *) echo "the stand-in CLI makes no model call" >&2; exit 3 ;;\n'
                   "esac\n", encoding="utf-8")
    exe.chmod(0o755)

    def say(facts: dict) -> None:
        auth = {"loggedIn": facts["logged_in"],
                "authMethod": "claude.ai" if facts["logged_in"] else "none"}
        if facts["plan"]:
            auth["subscriptionType"] = facts["plan"]
        for name, text in (("version", f"{facts['version']} (Claude Code)\n"),
                           ("auth.json", json.dumps(auth))):
            new = home / f".{name}.new"
            new.write_text(text, encoding="utf-8")
            os.replace(new, home / name)               # never a half-written answer

    say(SIGNED_IN)
    monkeypatch.setenv("PATH", f"{home}{os.pathsep}{os.environ.get('PATH', '')}")
    # no long-lived token handed to the stand-in (inference.cli_env)
    monkeypatch.setenv("ROUGHCUT_CLAUDE_TOKEN_FILE", str(tmp / "no-token"))
    return say, home / "asked.log"


def _cli_word(path: str, body: dict) -> dict:
    """What an answer says the CLI said: the status's `backend.cli` and its fix (the
    banner), or the flow's blockers (the Next action's "Fix the CLI")."""
    if path == "/api/status":
        b = body["backend"]
        cli = b["cli"]
        return {"version": cli.get("version"), "logged_in": cli.get("logged_in"),
                "plan": cli.get("plan"), "fix": (b.get("fix") or {}).get("kind")}
    return {"blocked": [x.get("kind") for x in body.get("blockers") or []]}


def _expect(path: str, facts: dict) -> dict:
    if path == "/api/status":
        return {**facts, "fix": None if facts["logged_in"] else "login"}
    return {"blocked": [] if facts["logged_in"] else ["cli"]}


CLI_READS = ("/api/status", "/api/flow")


def _until_late(base: str, path: str, timeout: float = 10.0):
    """Read `path` until an answer comes late (a rebuild began and is held): the CLI's
    facts are re-asked every CLI_FACTS_TTL_S here, which moves the snapshot's key."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = httpx.get(base + path, timeout=5)
        if r.headers.get("x-roughcut-snapshot") == "last":
            return r
        time.sleep(0.05)
    raise AssertionError(f"{path} never answered late")


def _until_said(base: str, facts: dict, timeout: float = 10.0) -> None:
    """The banner's poll (/api/backend asks the CLI again once its facts are stale) until
    the server knows the new word."""
    want = {k: facts[k] for k in ("version", "logged_in", "plan")}
    deadline = time.time() + timeout
    while time.time() < deadline:
        cli = httpx.get(base + "/api/backend", timeout=5).json()["cli"]
        if {k: cli.get(k) for k in want} == want:
            return
        time.sleep(0.05)
    raise AssertionError(f"the server never heard the CLI say {want}")


def _until_fresh(base: str, path: str, facts: dict, timeout: float = 10.0) -> None:
    """Read `path` until an answer is the current one (not late) and says `facts`."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = httpx.get(base + path, timeout=5)
        if r.headers.get("x-roughcut-snapshot") != "last" and _cli_word(path, r.json()) == _expect(path, facts):
            return
        time.sleep(0.05)
    raise AssertionError(f"{path} never answered {facts} fresh")


def test_a_change_in_what_the_cli_says_during_an_imports_settle_is_never_answered_old(
        live, project, monkeypatch):
    """While an import settles, the CLI (a stand-in, asked for real) signs out, then comes
    back updated to a new version. Before each change a status and a flow build is held
    mid-way, having read the old word, and the reads that began it were answered late —
    the widened path (a re-ask that came back the same) is live. Once /api/backend says the
    new word, a status and a flow read answer with it: never late from the old snapshot,
    never by joining the held build. Afterwards late answers resume, from the new word. The
    stand-in was only ever asked its version and sign-in."""
    import concurrent.futures

    base, server = live["base"], live["server"]
    say, asked = _stand_in_cli(live["tmp"], monkeypatch)
    monkeypatch.setitem(server.STATE, "cli_facts", True)        # as a launch without --no-probe
    monkeypatch.setattr(server, "CLI_FACTS_TTL_S", 0.5)
    for k, v in (("at", 0.0), ("facts", {}), ("asking", False)):
        monkeypatch.setitem(server._CLI_FACTS, k, v)
    held: dict = {"gate": None}

    def holding(real):
        """A snapshot build that waits, after its reads, while the test holds the gate."""
        def build():
            out = real()
            gate = held["gate"]
            if gate is not None and threading.current_thread().name.startswith("snapshot"):
                gate.wait(15)
            return out
        return build

    monkeypatch.setattr(server, "_status_disk", holding(server._status_disk))
    monkeypatch.setattr(server, "_flow_disk_facts", holding(server._flow_disk_facts))
    names = {"/api/status": "status", "/api/flow": "flow"}
    try:
        _until_said(base, SIGNED_IN)                               # the launch's first ask
        for p in CLI_READS:                                        # the answers before
            _until_fresh(base, p, SIGNED_IN)
        assert _import_one(base, live["tmp"], project, "KX_91.MP4") == ["KX_91.MP4"]
        for now, then in ((SIGNED_IN, SIGNED_OUT), (SIGNED_OUT, UPDATED)):
            assert server.import_settling()
            gate = held["gate"] = threading.Event()
            try:
                for p in CLI_READS:
                    r = _until_late(base, p)
                    assert _cli_word(p, r.json()) == _expect(p, now), (p, r.json())
                    assert r.json()["importing"] is True
                begun = {p: server.SNAP["builds"][names[p]] for p in CLI_READS}
                say(then)
                _until_said(base, then)
                assert not any(f.done() for f in begun.values()), \
                    "a build begun on the old word still runs as the reads come"
                with concurrent.futures.ThreadPoolExecutor(len(CLI_READS)) as ex:
                    reads = {p: ex.submit(httpx.get, base + p, timeout=30) for p in CLI_READS}
                    time.sleep(0.3)                                 # a late answer is out by now
                    held["gate"] = None
                    gate.set()
                    got = {p: f.result() for p, f in reads.items()}
            finally:
                held["gate"] = None
                gate.set()
            for p, r in got.items():
                assert r.status_code == 200
                assert _cli_word(p, r.json()) == _expect(p, then), \
                    (p, "answered from the snapshot of what the CLI said before", r.headers, r.json())
                assert r.headers.get("x-roughcut-snapshot") != "last" and "importing" not in r.json()
            # late answers resume, from the new word
            for p in CLI_READS:
                _until_fresh(base, p, then)
            gate = held["gate"] = threading.Event()
            try:
                for p in CLI_READS:
                    r = _until_late(base, p)
                    assert _cli_word(p, r.json()) == _expect(p, then), (p, r.json())
            finally:
                held["gate"] = None
                gate.set()
        lines = asked.read_text(encoding="utf-8").split("\n")
        assert {x for x in lines if x} == {"--version", "auth status"}, set(lines)
    finally:
        held["gate"] = None
        monkeypatch.setattr(server, "CLI_FACTS_TTL_S", 30.0)
        deadline = time.time() + 10
        while server._CLI_FACTS["asking"] and time.time() < deadline:   # a re-ask in flight lands
            time.sleep(0.05)
        server.SNAP["import"] = None
