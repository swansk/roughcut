"""The AI's work cannot be lost (wave 23, lane SAFE; the Copper editors' area 1).

Server side, on the suite's fixture bin with a scripted model (no live call):

  * a proposal waits until the editor answers it or a newer one replaces it: a save of the
    cut, a saved copy, an index run touching the cut, a later AI job never drop it
    (CLUMSY-09, FINDER-20);
  * a second proposal keeps the first (`replaced`), the last few are listed with where each
    stands, and any of them opens again, waiting (reopen);
  * Stop ends any AI job: an Ask, a model search; no proposal and no record is kept, and the
    reply says what it did (CLUMSY-13);
  * the undo history is kept with the cut (GET / PUT /api/undo) for the page to take back
    after a reload or a bin switch (CLUMSY-06, -17, HANDS-07, WORDS-14, FINISH-10).
"""

from __future__ import annotations

import json
import os
import threading
import time

import pytest

from roughcut import config, inference

PLAN_A = [{"clip": "CLIP_C.MP4", "in": 0.5, "out": 3.0, "why": "open on the walk"},
          {"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0, "why": "the greeting"}]
PLAN_B = [{"clip": "CLIP_B.MP4", "in": 2.4, "out": 4.0, "why": "the reply"},
          {"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0, "why": "the greeting"},
          {"clip": "CLIP_C.MP4", "in": 3.0, "out": 5.0, "why": "the walk"}]


class Scripted:
    """Answers the Ask's schema with the next plan; can be made to wait (a slow model)."""
    name = "kept-scripted"

    def __init__(self):
        self.plans: list[list[dict]] = []
        self.gate: threading.Event | None = None
        self.calls = 0

    def complete(self, request):
        self.calls += 1
        if self.gate is not None:
            self.gate.wait(timeout=30)
        schema = request.schema or {}
        if "segments" in schema:
            text = json.dumps({"segments": self.plans.pop(0) if self.plans else PLAN_A,
                               "notes": "a plan"})
        elif "matches" in schema:
            text = json.dumps({"matches": [{"clip": "CLIP_A.MP4", "start": 0.5, "end": 2.0,
                                            "why": "hello"}], "notes": ""})
        else:
            text = "{}"
        model = config.model_for(request.role)
        return inference.Result(content=text, input_tokens=100, output_tokens=50,
                                backend=self.name, model=model,
                                projected_usd=1e-4, latency_ms=1, raw=text)


@pytest.fixture
def model():
    m = Scripted()
    inference.set_backend(m)
    inference.reset_spend()
    yield m
    if m.gate is not None:
        m.gate.set()
    inference.set_backend(None)


def _wait(client, job, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        s = client.get(f"/api/ask/{job}").json()
        if s["state"] not in ("estimating", "running"):
            return s
        time.sleep(0.05)
    raise AssertionError(f"ask never finished: {s}")


def _cut(client) -> list[dict]:
    return client.get("/api/project").json()["segments"]


def _ask(client, model, plan, note="tighten it") -> str:
    model.plans.append(plan)
    r = client.post("/api/ask", json={"note": note, "segments": _cut(client)})
    assert r.status_code == 200, r.text
    job = r.json()["job"]
    assert _wait(client, job)["state"] == "done"
    return job


def _status(client, job) -> str:
    rows = client.get("/api/asks/recent").json()["proposals"]
    return next(r["status"] for r in rows if r["job"] == job)


@pytest.fixture
def clean_asks(client):
    import server
    for p in server.STATE["asks"].glob("*.json"):
        p.unlink()
    yield server
    for p in server.STATE["asks"].glob("*.json"):
        p.unlink()


# ---------------------------------------------------------------- the proposal waits

def test_a_proposal_waits_until_it_is_answered(client, model, clean_asks):
    job = _ask(client, model, PLAN_A)
    latest = client.get("/api/asks/latest").json()
    assert latest["pending"] is True and latest["record"]["job"] == job
    assert latest["record"]["plan"]["job"] == job, "the plan names its own job"
    assert _status(client, job) == "waiting"

    # an index run, a save of the cut by something else: the cut's mtime moves ahead of it
    future = time.time() + 120
    os.utime(clean_asks.STATE["edl"], (future, future))
    clean_asks.write_edl(clean_asks.read_edl())
    assert client.get("/api/asks/latest").json()["pending"] is True
    assert _status(client, job) == "waiting"
    assert client.get("/api/flow").json()["stages"], "the flow still reads"
    stage = next(s for s in client.get("/api/flow").json()["stages"] if s["key"] == "cut")
    assert stage["state"] == "needs-you", stage

    assert client.post("/api/asks/answer", json={"answer": "discard", "job": job}).status_code == 200
    assert _status(client, job) == "discarded"
    assert client.get("/api/asks/latest").json()["pending"] is False


def test_a_saved_copy_does_not_drop_the_proposal_it_waits_for_its_cut(client, model, clean_asks, project):
    """FINDER-20: cut switcher > save a copy as..., work on the copy, come back to main."""
    job = _ask(client, model, PLAN_A)
    main = str(clean_asks.STATE["edl"])
    r = client.post("/api/cuts/copy", json={"name": "finder selects"})
    assert r.status_code == 200, r.text
    try:
        # on the copy: the proposal belongs to main, so it is not waiting here ...
        assert client.get("/api/asks/latest").json()["pending"] is False
        rows = client.get("/api/asks/recent").json()["proposals"]
        row = next(x for x in rows if x["job"] == job)
        assert row["this_cut"] is False and row["status"] == "waiting"
        # ... and cannot be opened over the copy
        assert client.post("/api/asks/reopen", json={"job": job}).status_code == 409
    finally:
        assert client.post("/api/cuts/open", json={"path": main}).status_code == 200
        for p in (project["work"] / "projects").rglob("finder-selects.edl.json"):
            p.unlink()
    again = client.get("/api/asks/latest").json()
    assert again["pending"] is True and again["record"]["job"] == job


def test_a_cut_equal_to_the_proposal_is_that_proposal_accepted(client, model, clean_asks):
    """An Accept whose answer never landed: the cut on disk is the proposal's shots."""
    job = _ask(client, model, PLAN_A)
    edl = clean_asks.read_edl()
    plan = client.get(f"/api/asks/{job}").json()["record"]["plan"]["segments"]
    edl["segments"] = [dict(s) for s in plan]          # what the board's Accept saves, verbatim
    clean_asks.write_edl(edl)
    assert _status(client, job) == "accepted"
    assert client.get("/api/asks/latest").json()["pending"] is False


# ---------------------------------------------------------------- two proposals

def test_a_second_proposal_keeps_the_first_and_any_can_be_reopened(client, model, clean_asks):
    first = _ask(client, model, PLAN_A, note="first idea")
    second = _ask(client, model, PLAN_B, note="second idea")
    rows = client.get("/api/asks/recent").json()["proposals"]
    assert [(r["job"], r["status"]) for r in rows[:2]] == [(second, "waiting"), (first, "replaced")]
    assert rows[1]["replaced_by"] == second and rows[0]["shots"] == 3 and rows[1]["note"] == "first idea"
    assert client.get("/api/asks/latest").json()["record"]["job"] == second
    # the replaced one still holds its plan, whole
    assert len(client.get(f"/api/asks/{first}").json()["record"]["plan"]["segments"]) == 2

    got = client.post("/api/asks/reopen", json={"job": first}).json()
    assert got["ok"] and got["replaced"] == [second]
    assert [r["status"] for r in client.get("/api/asks/recent").json()["proposals"][:2]] == ["waiting", "replaced"]
    assert client.get("/api/asks/latest").json()["record"]["job"] == first
    assert client.get("/api/asks/latest").json()["pending"] is True

    # an answer names the proposal it answers: discarding the second leaves the first waiting
    client.post("/api/asks/answer", json={"answer": "discard", "job": second})
    assert _status(client, second) == "discarded" and _status(client, first) == "waiting"


def test_a_discarded_or_accepted_proposal_opens_again(client, model, clean_asks):
    job = _ask(client, model, PLAN_A)
    client.post("/api/asks/answer", json={"answer": "discard", "job": job})
    assert client.get("/api/asks/latest").json()["pending"] is False
    assert client.post("/api/asks/reopen", json={"job": job}).json()["ok"] is True
    assert _status(client, job) == "waiting"
    client.post("/api/asks/answer", json={"answer": "accept", "job": job})
    assert _status(client, job) == "accepted"
    client.post("/api/asks/reopen", json={"job": job})
    assert _status(client, job) == "waiting"
    assert client.post("/api/asks/reopen", json={"job": "../etc"}).status_code == 400
    assert client.post("/api/asks/reopen", json={"job": "abcdef12"}).status_code == 404


def test_only_the_last_few_are_listed(client, model, clean_asks):
    jobs = [_ask(client, model, PLAN_A, note=f"n{i}") for i in range(10)]
    rows = client.get("/api/asks/recent").json()["proposals"]
    assert len(rows) == 8 and rows[0]["job"] == jobs[-1]
    assert [r["status"] for r in rows].count("waiting") == 1, "one waits, the rest were replaced"


# ---------------------------------------------------------------- Stop

def test_stop_ends_an_ask_and_keeps_nothing(client, model, clean_asks):
    model.gate = threading.Event()
    r = client.post("/api/ask", json={"note": "recut it", "segments": _cut(client)})
    job = r.json()["job"]
    end = time.time() + 10
    while model.calls == 0 and time.time() < end:
        time.sleep(0.02)
    assert client.get(f"/api/ask/{job}").json()["state"] in ("estimating", "running")

    out = client.post(f"/api/jobs/{job}/stop").json()
    assert out["stopped"] is True and out["state"] == "cancelled"
    assert "no proposal was kept" in out["said"] and "cut stays as it was" in out["said"]
    assert client.get(f"/api/ask/{job}").json()["state"] == "cancelled"
    jobs = client.get("/api/jobs").json()["jobs"]
    assert all(j["id"] != job or j["state"] == "cancelled" for j in jobs)

    model.gate.set()                              # the call returns after the Stop ...
    time.sleep(1.0)
    assert not (clean_asks.STATE["asks"] / f"{job}.json").exists(), "... and is dropped"
    assert client.get(f"/api/ask/{job}").json()["state"] == "cancelled"
    assert client.get("/api/asks/latest").json()["pending"] is False
    again = client.post(f"/api/jobs/{job}/stop").json()
    assert again["stopped"] is False and "already" in again["said"]
    assert client.post("/api/jobs/nosuchjob/stop").status_code == 404


def test_stop_ends_a_model_search(client, model, clean_asks):
    model.gate = threading.Event()
    r = client.post("/api/find", json={"query": "hello", "deep": True, "local": False})
    assert r.status_code == 200, r.text
    job = r.json()["job"]
    assert job
    out = client.post(f"/api/jobs/{job}/stop").json()
    assert out["state"] == "cancelled" and "no matches were kept" in out["said"]
    model.gate.set()
    time.sleep(0.8)
    import server
    assert server.FINDS[job]["state"] == "cancelled" and not server.FINDS[job].get("found")


def test_stop_on_a_finished_job_says_so(client, model, clean_asks):
    job = _ask(client, model, PLAN_A)
    out = client.post(f"/api/jobs/{job}/stop").json()
    assert out["stopped"] is False and out["state"] == "done"


# ---------------------------------------------------------------- undo kept with the cut

def test_the_undo_history_is_kept_with_the_cut(client, project):
    import server
    server.undo_path().unlink(missing_ok=True)
    assert client.get("/api/undo").json() == {"head": None, "undo": [], "redo": []}
    cut = str(server.STATE["edl"])
    body = {"cut": cut, "head": '{"segs":[1]}',
            "undo": [{"label": "trim", "before": '{"segs":[0]}', "after": '{"segs":[1]}'},
                     {"label": "discard proposal", "before": "a", "after": "a", "tag": "proposal:ab12cd34"},
                     {"nope": 1}],
            "redo": [{"label": "remove", "before": "x", "after": "y"}]}
    got = client.put("/api/undo", json=body).json()
    assert got == {"ok": True, "undo": 2, "redo": 1}, "a malformed entry is dropped"
    back = client.get("/api/undo").json()
    assert back["head"] == '{"segs":[1]}' and [e["label"] for e in back["undo"]] == ["trim", "discard proposal"]
    assert back["undo"][1]["tag"] == "proposal:ab12cd34" and back["redo"][0]["after"] == "y"
    server.undo_path().unlink(missing_ok=True)


def test_a_page_going_away_never_writes_over_the_cut_the_server_moved_to(client):
    """The switcher POSTs the switch and then reloads: the old page's last write names its
    cut, and the server, now on another one, refuses it."""
    import server
    before = client.get("/api/undo").json()
    r = client.put("/api/undo", json={"cut": "/somewhere/else.edl.json", "head": "h",
                                      "undo": [{"label": "x", "before": "a", "after": "b"}], "redo": []})
    assert r.status_code == 409
    assert client.get("/api/undo").json() == before
    assert client.put("/api/undo", content=b"[1]").status_code == 400
    assert client.put("/api/undo", content=b"x" * (server.UNDO_MAX_BYTES + 1)).status_code == 413


def test_each_cut_has_its_own_history(client, project):
    import server
    main = server.undo_path()
    client.post("/api/cuts/copy", json={"name": "other take"})
    try:
        assert server.undo_path() != main
        assert server.undo_path().parent == main.parent
    finally:
        client.post("/api/cuts/open", json={"path": str(project["edl"])})
        for p in (project["work"] / "projects").rglob("other-take.edl.json"):
            p.unlink()
