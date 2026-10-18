"""Ask the AI for an edit (redesign wave 12, lane AIOPS; REDESIGN-GOAL dimensions 14-15).

`POST /api/assist {text, scope}` answers `{job}` at once; the model runs on the job's
thread (roughcut/assist.py, through `roughcut.inference`) and `GET /api/assist/{job}`
reads back one of three kinds — `ops` (the pro tools' own operations, checked against
the cut), `plan` (a whole-cut rewrite through the existing Ask job) or `answer` (words).
`POST /api/assist/{job}/answer` records the review. Never a real model call: a scripted
backend answers through `inference.set_backend` (tools/journey.py's pattern)."""

from __future__ import annotations

import json
import re
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import actions, assist, config, inference  # noqa: E402

from test_server import _fresh  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


class Scripted:
    """Answers the assist call with `answer` (a dict, or a callable of the prompt); the
    Ask's cut call with a two-shot plan; everything else (the ETA estimate) with "{}" so it
    falls back. Every prompt is kept."""
    name = "assist-scripted"

    def __init__(self, answer=None, gate: threading.Event | None = None, fail: str | None = None):
        self.answer = answer
        self.gate = gate
        self.fail = fail
        self.prompts: list[str] = []
        self.schemas: list[dict] = []

    def complete(self, request):
        self.prompts.append(request.prompt)
        self.schemas.append(request.schema or {})
        schema = request.schema or {}
        if self.gate is not None and "kind" in schema:
            assert self.gate.wait(10), "the test never let the model answer"
        if self.fail and "kind" in schema:
            raise inference.InferenceError(self.fail)
        if "kind" in schema:
            ans = self.answer(request.prompt) if callable(self.answer) else self.answer
            text = json.dumps(ans)
        elif "segments" in schema:
            text = json.dumps({"segments": [
                {"clip": "CLIP_B.MP4", "in": 0.5, "out": 2.0, "why": "the teaser opens on hello"},
                {"clip": "CLIP_A.MP4", "in": 2.4, "out": 4.0, "why": "how are you"}],
                "notes": "a teaser"})
        else:
            text = "{}"
        model = config.model_for(request.role)
        return inference.Result(content=text, input_tokens=1200, output_tokens=300,
                                backend=self.name, model=model,
                                projected_usd=config.projected_usd(model, 1200, 300),
                                latency_ms=1, raw=text)


@pytest.fixture
def board(tmp_path, project, monkeypatch):
    """A server on a copy of the fixture's EDL (ids minted at open), with no model."""
    monkeypatch.delenv("ROUGHCUT_BACKEND", raising=False)
    for role in (config.ROLE_SKELETON, config.ROLE_ANALYSIS):
        monkeypatch.delenv(f"ROUGHCUT_MODEL_{role.upper()}", raising=False)
    edl = tmp_path / "edl.json"
    shutil.copy(project["edl"], edl)
    with _fresh(tmp_path / "work", project, edl=edl, sidecars=project["sidecars"],
                assets=project["assets"]) as c:
        ids = [s["id"] for s in c.get("/api/project").json()["segments"]]
        yield c, ids
    inference.set_backend(None)


def wait(c, job: str, timeout: float = 10.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        d = c.get(f"/api/assist/{job}").json()
        if d["status"] != "running":
            return d
        time.sleep(0.05)
    raise AssertionError(f"the assist job never finished: {d}")


def ask(c, text: str, scope: dict | None = None) -> dict:
    r = c.post("/api/assist", json={"text": text, "scope": scope or {"kind": "sequence"}})
    assert r.status_code == 200, r.text
    return wait(c, r.json()["job"])


# ---------------------------------------------------------------- the three kinds

def test_ops_come_back_checked_described_and_priced(board):
    c, (a, b) = board
    model = Scripted({"kind": "ops", "summary": "a dissolve, and a brighter second shot",
                      "ops": [{"op": "transition", "a": a, "b": b, "kind": "cross_dissolve"},
                              {"op": "set", "id": b, "prop": "exposure", "value": 0.3},
                              {"op": "set", "id": b, "prop": "exposure", "value": 7}]})
    inference.set_backend(model)
    d = ask(c, "dissolve between 1 and 2 and brighten 2")
    assert d["status"] == "done" and d["kind"] == "ops"
    assert d["ops"] == [{"op": "transition", "a": a, "b": b, "kind": "cross_dissolve", "dur": 1.0},
                        {"op": "set", "id": b, "prop": "exposure", "value": 0.3}]
    assert d["lines"] == ["Cross Dissolve between shot 1 (CLIP_A) and shot 2 (CLIP_B), 1.00 s",
                          "Shot 2 (CLIP_B): Exposure +0.30"]
    assert d["index"] == [0, 1]
    assert d["refused"] == [{"i": 2, "op": "set", "why": "Exposure is -0.415 to 0.585; 7 is outside it"}]
    assert d["summary"] == "a dissolve, and a brighter second shot"
    assert set(d["families"]) == {"transitions", "effect_controls"}
    assert "preview" not in d                       # nothing on V1 changes: no ghost
    # the subscription backend: no price reads as spending money (section S)
    cost = d["cost"]
    assert cost["charged"] is False and cost["backend"] == "claude_cli"
    assert cost["words"] == "free on your plan" and "$" not in cost["words"]
    assert "if it went through the API" in cost["hover"]
    assert cost["measured"] is True and cost["usd"] > 0
    # the deep role's model, from config — never a literal (section S)
    assert cost["model"] == config.model_for(assist.ROLE)


def test_a_cut_change_carries_its_ghost(board):
    c, (a, b) = board
    inference.set_backend(Scripted({"kind": "ops", "summary": "razor shot 1",
                                    "ops": [{"op": "razor", "id": a, "at": 1.0}]}))
    d = ask(c, "cut shot 1 in half")
    assert d["ops"] == [{"op": "razor", "id": a, "at": 1.0, "makes": "new:1"}]
    assert [s["id"] for s in d["preview"]["segments"]] == [a, "new:1", b]


def test_a_whole_cut_rewrite_goes_through_the_ask(board):
    c, ids = board
    model = Scripted({"kind": "plan", "summary": "a 10 s teaser",
                      "note": "cut it down to a 10 second teaser that opens on hello"})
    inference.set_backend(model)
    d = ask(c, "make it a teaser")
    assert d["kind"] == "plan" and d["status"] == "done"
    assert d["note"] == "cut it down to a 10 second teaser that opens on hello"
    job = d["plan_ask"]
    assert re.fullmatch(r"[0-9a-f]{8}", job)
    end = time.time() + 15
    while time.time() < end:
        s = c.get(f"/api/ask/{job}").json()
        if s["state"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert s["state"] == "done", s
    assert [x["clip"] for x in s["plan"]["segments"]] == ["CLIP_B.MP4", "CLIP_A.MP4"]
    # the Ask was asked with the assistant's brief
    assert any("10 second teaser that opens on hello" in p for p in model.prompts[1:])
    assert c.get(f"/api/assist/{d['job']}").json()["plan_state"] == "done"


def test_a_question_is_answered_in_words(board):
    c, _ = board
    inference.set_backend(Scripted({"kind": "answer", "summary": "what CLIP_B says",
                                    "text": "CLIP_B opens on “hello there”."}))
    d = ask(c, "what does clip B say?")
    assert d["kind"] == "answer" and d["text"] == "CLIP_B opens on “hello there”."
    assert "ops" not in d


def test_every_op_refused_is_an_answer_with_the_reasons(board):
    c, _ = board
    inference.set_backend(Scripted({"kind": "ops", "summary": "x",
                                    "ops": [{"op": "speed", "id": "nope", "rate": 0.5}]}))
    d = ask(c, "slow down shot 9")
    assert d["kind"] == "answer"
    assert d["text"] == "I could not make that edit: shot nope is not in the cut"
    assert d["ops"] == [] and d["refused"][0]["why"] == "shot nope is not in the cut"


def test_a_failed_call_says_why(board):
    c, _ = board
    inference.set_backend(Scripted(fail="claude CLI timed out after 600s"))
    d = ask(c, "anything")
    assert d["status"] == "failed" and "timed out" in d["error"]


def test_an_empty_request_is_refused_before_a_job(board):
    c, _ = board
    inference.set_backend(Scripted({"kind": "answer", "text": "x"}))
    r = c.post("/api/assist", json={"text": "   "})
    assert r.status_code == 400 and "say what you want" in r.json()["detail"]


# ---------------------------------------------------------------- the scope reaches the model

def test_the_scope_reaches_the_prompt(board):
    c, (a, b) = board
    model = Scripted({"kind": "answer", "text": "ok"})
    inference.set_backend(model)
    ask(c, "warm these up", {"kind": "selection", "ids": [b], "playhead": 2.5})
    p = model.prompts[-1]
    scope = p.split("## Scope", 1)[1].split("## The sequence", 1)[0]
    assert f"The selected shots: shot 2 ({b})" in scope
    assert "The playhead is at 2.50 s (00:00:02:12) (shot 2, 0.50 s in)." in scope
    assert "“warm these up”" in p
    ask(c, "tighten this", {"kind": "range", "in": 0.5, "out": 1.5})
    scope = model.prompts[-1].split("## Scope", 1)[1].split("## The sequence", 1)[0]
    assert f"The range 0.50–1.50 s (00:00:00:12–00:00:01:12) of the sequence: shot 1 ({a})" in scope
    ask(c, "use the good bit", {"kind": "clip", "clip": "CLIP_C.MP4", "in": 2.4, "out": 4.0})
    assert ("The clip CLIP_C.MP4 (in the Source monitor or the Project panel; not a shot in the sequence),"
            " its In 2.40 s to Out 4.00 s") in model.prompts[-1]
    ask(c, "anything", {"kind": "bogus"})
    assert "The whole sequence" in model.prompts[-1].split("## The sequence", 1)[0]


def test_the_prompt_carries_the_sequence_the_clips_and_the_vocabulary(board):
    c, (a, b) = board
    model = Scripted({"kind": "answer", "text": "ok"})
    inference.set_backend(model)
    ask(c, "anything")
    p = model.prompts[-1]
    assert f"{a}  CLIP_A.MP4  clip 1.00–3.00 s  film 0.00–2.00 s" in p
    assert f"{b}  CLIP_B.MP4  clip 0.00–2.00 s  film 2.00–4.00 s" in p
    assert "CLIP_C.MP4  6.00 s — says: “hello there how are you goodbye”" in p
    assert "Looks: crisp, alpine, filmic" in p
    assert "quick (Quick 1080p)" in p
    assert actions.schema_text() in p
    assert len(p) < 40_000                          # bounded


def test_the_inventory_is_bounded(board):
    clips = {f"CLIP_{i:03d}.MP4": {"duration": 10.0, "transcript": [{"text": "word " * 80}]}
             for i in range(400)}
    block = assist.clips_block(clips)
    assert len(block) <= assist.CLIPS_BLOCK_MAX + 2000
    assert "more clips:" in block
    assert all(len(line) <= assist.CLIP_LINE_MAX for line in block.splitlines()[:-1])


# ---------------------------------------------------------------- never waits on the model

def test_the_request_never_waits_on_the_model(board):
    c, (a, b) = board
    gate = threading.Event()
    inference.set_backend(Scripted({"kind": "ops", "summary": "s",
                                    "ops": [{"op": "marker", "at": 1.0}]}, gate=gate))
    t0 = time.time()
    r = c.post("/api/assist", json={"text": "a marker at one second"})
    took = time.time() - t0
    assert r.status_code == 200 and took < 1.0, took
    job = r.json()["job"]
    assert r.json()["charged"] is False
    # running, with its price already on it (priced before the call, from its prompt)
    end = time.time() + 5
    while time.time() < end:
        d = c.get(f"/api/assist/{job}").json()
        if d.get("cost"):
            break
        time.sleep(0.05)
    assert d["status"] == "running"
    assert d["cost"]["measured"] is False and d["cost"]["words"] == "free on your plan"
    assert d["cost"]["usd"] > 0 and "tokens in" in d["cost"]["basis"]
    gate.set()
    d = wait(c, job)
    assert d["status"] == "done" and d["lines"] == ["Marker at 00:00:01:00"]


@pytest.mark.parametrize("backend, words", [("claude_cli", "free on your plan"),
                                            ("anthropic_api", "~$")])
def test_cost_wording_follows_the_backend(board, monkeypatch, backend, words):
    c, _ = board
    monkeypatch.setenv("ROUGHCUT_BACKEND", backend)
    inference.set_backend(Scripted({"kind": "answer", "text": "ok"}))
    d = ask(c, "anything")
    assert d["cost"]["backend"] == backend
    assert d["cost"]["charged"] is (backend == "anthropic_api")
    assert d["cost"]["words"].startswith(words)


# ---------------------------------------------------------------- the review, on the record

def test_the_review_is_recorded(board):
    c, (a, b) = board
    inference.set_backend(Scripted({"kind": "ops", "summary": "s",
                                    "ops": [{"op": "marker", "at": 1.0},
                                            {"op": "speed", "id": b, "rate": 0.5}]}))
    d = ask(c, "two things")
    job = d["job"]
    r = c.post(f"/api/assist/{job}/answer", json={"accepted": [1], "rejected": [0]})
    assert r.status_code == 200 and r.json()["answer"]["accepted"] == [1]
    assert c.get(f"/api/assist/{job}").json()["answer"]["rejected"] == [0]
    for body, why in [({"accepted": [5]}, "no op 5"), ({"accepted": [0], "rejected": [0]}, "both"),
                      ({"accepted": "all"}, "list of op indices")]:
        r = c.post(f"/api/assist/{job}/answer", json=body)
        assert r.status_code == 400 and why in r.json()["detail"], r.text
    assert c.post("/api/assist/0123456789ab/answer", json={}).status_code == 404


def test_the_answer_waits_for_the_job(board):
    c, _ = board
    gate = threading.Event()
    inference.set_backend(Scripted({"kind": "answer", "text": "ok"}, gate=gate))
    job = c.post("/api/assist", json={"text": "x"}).json()["job"]
    assert c.post(f"/api/assist/{job}/answer", json={"accepted": []}).status_code == 409
    gate.set()
    wait(c, job)


def test_the_record_outlives_the_process(board):
    """On disk under --work/assist/<bin>/ before anyone reads it, like the Ask's."""
    import server
    c, (a, b) = board
    inference.set_backend(Scripted({"kind": "ops", "summary": "s",
                                    "ops": [{"op": "marker", "at": 1.0}]}))
    d = ask(c, "a marker")
    path = server.assist_home() / f"{d['job']}.json"
    rec = json.loads(path.read_text(encoding="utf-8"))
    assert rec["text"] == "a marker" and rec["result"]["ops"] == [{"op": "marker", "at": 1.0}]
    assert path.is_relative_to(server.STATE["work"])
    server.ASSISTS.clear()                           # a restart
    again = c.get(f"/api/assist/{d['job']}").json()
    assert again["status"] == "done" and again["ops"] == d["ops"]
    assert c.get("/api/assist/ffffffffffff").status_code == 404
    assert c.get("/api/assist/..%2Fetc").status_code == 404


# ---------------------------------------------------------------- the board's cut, now

def test_preview_checks_against_the_cut_the_board_holds(board):
    c, (a, b) = board
    ops = [{"op": "razor", "id": a, "at": 1.0}, {"op": "speed", "id": "tmp-3", "rate": 2}]
    d = c.post("/api/assist/preview", json={"ops": ops}).json()
    assert d["index"] == [0] and "tmp-3 is not in the cut" in d["refused"][0]["why"]
    segs = c.get("/api/project").json()["segments"] + [
        {"id": "tmp-3", "clip": "CLIP_C.MP4", "in": 0, "out": 2}]
    d = c.post("/api/assist/preview", json={"ops": ops, "segments": segs}).json()
    assert d["index"] == [0, 1] and d["refused"] == []
    assert d["lines"][1] == "Shot 3 (CLIP_C): Speed 200 %"     # as the board shows it now


# ---------------------------------------------------------------- hygiene (section S)

def test_no_model_literal_and_every_call_through_inference():
    lit = re.compile(r"claude-(opus|sonnet|haiku|fable)-\d")
    for rel in ("roughcut/actions.py", "roughcut/assist.py", "app/static/actions.js"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert not lit.search(text), rel
        assert not re.search(r"^\s*(import anthropic|from anthropic)", text, re.M), rel
        assert "subprocess" not in text, rel
    src = (ROOT / "roughcut/assist.py").read_text(encoding="utf-8")
    assert "from .inference import complete" in src
    assert "ROLE = config.ROLE_" in src


# ---------------------------------------------------------------- stop, and the price first

def test_a_cancelled_ask_drops_its_answer(board):
    """The prompt's Stop (AIPROMPT): the job reads `cancelled` at once and stays so; the
    call already sent finishes on its thread and what it returns never reaches a review."""
    c, (a, b) = board
    gate = threading.Event()
    inference.set_backend(Scripted({"kind": "ops", "summary": "s",
                                    "ops": [{"op": "marker", "at": 1.0}]}, gate=gate))
    job = c.post("/api/assist", json={"text": "a marker"}).json()["job"]
    r = c.post(f"/api/assist/{job}/cancel")
    assert r.status_code == 200 and r.json() == {"ok": True, "status": "cancelled"}
    d = c.get(f"/api/assist/{job}").json()
    assert d["status"] == "cancelled" and d["error"] == "cancelled by the editor"
    gate.set()
    time.sleep(0.5)                                  # the call returns
    d = c.get(f"/api/assist/{job}").json()
    assert d["status"] == "cancelled" and "ops" not in d
    assert c.post(f"/api/assist/{job}/cancel").json() == {"ok": False, "status": "cancelled"}
    assert c.post("/api/assist/0123456789ab/cancel").status_code == 404
    # a finished job is left as it is
    inference.set_backend(Scripted({"kind": "answer", "text": "ok"}))
    done = ask(c, "anything")
    assert c.post(f"/api/assist/{done['job']}/cancel").json() == {"ok": False, "status": "done"}
    assert c.get(f"/api/assist/{done['job']}").json()["status"] == "done"


def test_the_price_is_on_the_ask_before_it_is_sent(board):
    """GET /api/assist/price prices the prompt the ask would send, at the role's model,
    in section S's words — and never calls the model."""
    c, (a, b) = board

    class Never:
        name = "never"

        def complete(self, request):
            raise AssertionError("a price must not call the model")
    inference.set_backend(Never())
    import json as _json
    d = c.get("/api/assist/price", params={"text": "warm up shot 2",
                                           "scope": _json.dumps({"kind": "selection", "ids": [b]})}).json()
    assert d["charged"] is False and d["words"] == "free on your plan"
    assert d["projected_usd"] > 0 and d["usd"] == round(d["projected_usd"], 2)
    assert d["model"] == config.model_for(assist.ROLE) and d["measured"] is False
    bare = c.get("/api/assist/price").json()          # no text yet: still a price
    assert bare["projected_usd"] > 0
