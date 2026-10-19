"""The first pass as one re-runnable step (Karl's K1, REDESIGN-GOAL dimension 2).

Karl, 2026-10-14: *"it is not clear how to re run the first pass."* `GET /api/firstpass`
says where the pass stands and what a run would do and cost; `POST /api/firstpass`
runs index → picks → a proposed cut as one job, and a re-run keeps the editor's hand
edits exactly. Also here: the index progress never ahead of its counts (verdict 1: 87%
at 0/12 heard) and the notes check (verdict 1: "unchanged" on a shot cut by 1.34 s).

Every model call is scripted; the index runs with test_index's stubbed tools, on a
copy of the suite's project, so nothing here writes the shared fixture.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import boundaries, config, firstpass, flow, inference, revise  # noqa: E402

from test_index import _stub_tools  # noqa: E402
from test_server import _fresh  # noqa: E402


# ------------------------------------------------------------------ the notes check

# Verdict 1's case, as a fixture: the model said it kept the CLIP_11 razor cut and the
# second half "exactly as it was", while the diff trimmed CLIP_11's out by 1.34 s.
CURRENT = [
    {"id": "g01", "clip": "CLIP_02.MP4", "in": 10.0, "out": 16.0},
    {"id": "g02", "clip": "CLIP_04.MP4", "in": 40.0, "out": 47.5},
    {"id": "g03", "clip": "CLIP_11.MP4", "in": 159.2, "out": 166.2},
    {"id": "g04", "clip": "CLIP_11.MP4", "in": 166.2, "out": 171.0},
    {"id": "g05", "clip": "CLIP_07.MP4", "in": 3.0, "out": 9.0},
]


def _proposal(notes: str) -> dict:
    segs = copy.deepcopy(CURRENT)
    segs[1]["out"] = 45.0                       # shot 2 tightened — said so in the notes
    segs[2]["out"] = 164.86                     # CLIP_11's out −1.34 s — NOT said
    return {"segments": segs, "notes": notes}


def test_notes_that_call_a_changed_shot_unchanged_are_flagged_and_corrected():
    plan = _proposal("I tightened the CLIP_04 shot. I've kept your CLIP_11 razor cut "
                     "exactly as it was, and the second half is unchanged.")
    out = revise.check_notes(plan, CURRENT)
    chk = out["notes_check"]
    assert chk["ok"] is False and chk["checked"] == 1
    flagged = {s["id"] for f in chk["flags"] for s in f["shots"]}
    assert flagged == {"g03"}, chk                # g02's change was declared elsewhere
    shot = chk["flags"][0]["shots"][0]
    assert shot["was"] == {"in": 159.2, "out": 166.2}
    assert shot["now"] == {"in": 159.2, "out": 164.86} and shot["delta_out"] == -1.34
    # the editor reads the correction in the notes; the model's words are kept apart
    assert "Check: shot 3 (CLIP_11) changes" in out["notes"]
    assert "out -1.34 s" in out["notes"] and "unchanged" in out["notes"]
    assert out["notes_model"] == plan["notes"]
    # the diff the claims were held to
    st = {r["id"]: r["status"] for r in out["diff"]["shots"]}
    assert st == {"g01": "kept", "g02": "changed", "g03": "changed", "g04": "kept",
                  "g05": "kept"}


def test_true_notes_pass_untouched():
    plan = _proposal("I tightened CLIP_04 and trimmed the end of the first CLIP_11 shot "
                     "by a second. Shot 5 is unchanged.")
    out = revise.check_notes(plan, CURRENT)
    assert out["notes_check"] == {"ok": True, "flags": [], "checked": 1}
    assert out["notes"] == plan["notes"] and "notes_model" not in out


def test_a_removed_shot_called_kept_is_flagged_by_number():
    plan = {"segments": copy.deepcopy(CURRENT[:4]),
            "notes": "Shot 5 is kept as it was; everything else too."}
    out = revise.check_notes(plan, CURRENT)
    assert out["notes_check"]["ok"] is False
    assert "shot 5 (CLIP_07 0:03.0–0:09.0) is removed" in out["notes"]


def test_a_first_cut_has_nothing_to_check():
    out = revise.check_notes({"segments": CURRENT, "notes": "all new, unchanged"}, [])
    assert out["notes_check"]["ok"] is True
    assert {r["status"] for r in out["diff"]["shots"]} == {"new"}


def test_polish_leaves_a_verbatim_shot_alone(project):
    """The cause of verdict 1's lie: boundary polish moved a shot the model had copied
    verbatim. CLIP_A 1.0-3.0 opens inside "hello there" and ends inside "how are you",
    so polish would move both ends — unless the range is the cut's own."""
    side = json.loads((project["sidecars"] / "CLIP_A.audio.json").read_text())
    clips = {"CLIP_A.MP4": {"clip": "CLIP_A.MP4", "duration": side["duration_s"],
                            "transcript": side["transcript"]}}
    seg = {"id": "gA", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "x"}
    assert boundaries.polish([seg], clips)[0]["out"] != 3.0, "the fixture must bite"
    plan = revise.polish_kept_alone({"segments": [dict(seg)], "notes": "kept"}, clips,
                                    [dict(seg)])
    assert (plan["segments"][0]["in"], plan["segments"][0]["out"]) == (1.0, 3.0)
    assert plan["notes"] == "kept"
    # a new range is still polished, and said
    moved = revise.polish_kept_alone({"segments": [dict(seg)], "notes": ""}, clips, [])
    assert moved["segments"][0]["out"] != 3.0 and "Boundary polish" in moved["notes"]


# ------------------------------------------------------------------ provenance


def _edl(segs):
    return {"segments": copy.deepcopy(segs)}


def test_manual_edits_are_the_cut_diffed_against_the_accepted_base():
    edl = firstpass.record_base(_edl(CURRENT), CURRENT, job="j1")
    assert firstpass.manual_edits(edl) == []
    segs = copy.deepcopy(CURRENT)
    segs[0]["out"] = 15.0                                         # trim
    piece = {"id": "gnew1", "clip": "CLIP_04.MP4", "in": 44.0, "out": 47.5}
    segs[1]["out"] = 44.0                                         # razor at 44.0
    segs.insert(2, piece)
    segs.append({"id": "gadd", "clip": "CLIP_09.MP4", "in": 1.0, "out": 4.0})   # add
    segs[3], segs[4] = segs[4], segs[3]                            # g04 moved before g03
    edl["segments"] = segs
    how = {r["id"]: r["how"] for r in firstpass.manual_edits(edl)}
    assert how["g01"] == ["trim"]
    assert how["g02"] == ["razor"] and how["gnew1"] == ["razor"]
    assert how["gadd"] == ["add"]
    # one swap is one move: exactly one of the pair left the base's order
    assert [how.get(k, []).count("move") for k in ("g03", "g04")] in ([1, 0], [0, 1])
    assert "g05" not in how
    # an undo that puts the trim back is the machine's shot again
    edl["segments"] = copy.deepcopy(CURRENT)
    assert firstpass.manual_edits(edl) == []


def test_a_grade_or_an_effect_is_never_a_hand_edit():
    """Verdict 11, punch 6: after the AI colour-matched every shot, the re-run counted all
    22 as "edited by hand" and could replace none. A grade (the colour block's override,
    the AI's or the editor's; a Lumetri value) and an effect (a Crop in Effect Controls)
    leave a shot the machine's; only a cut made by hand makes it the editor's. A base
    recorded before this rule, whose `hand` says "grade", no longer locks for it."""
    edl = firstpass.record_base(_edl(CURRENT), CURRENT, job="j1",
                                hand={"g02": ["grade"], "g03": ["trim", "grade"]})
    # the AI's Color Match over every other shot to the first, as its accept writes it
    edl["colour"] = {"mode": "auto", "reference": "g01",
                     "shots": {s["id"]: {"match": "reference"} for s in CURRENT[1:]}}
    edl["colour"]["shots"]["g01"] = {"balance": {"gain": [1.05, 1.0, 0.95], "exposure": 1.1}}
    edl["segments"][0]["controls"] = {"contrast": 12.0, "saturation": 80.0,
                                      "vfx": [{"id": "crop", "kind": "crop",
                                               "params": {"left": 10.0}}]}
    rows = firstpass.manual_edits(edl)
    assert [(r["id"], r["how"], r["words"]) for r in rows] == [("g03", ["trim"], "trimmed")]
    # and a cut by hand on a graded shot still is one
    edl["segments"][1]["out"] = 46.0
    assert {r["id"]: r["how"] for r in firstpass.manual_edits(edl)} == {
        "g02": ["trim"], "g03": ["trim"]}


def test_a_cut_the_ai_made_and_the_editor_accepted_is_marked_the_ais():
    """Verdict 12, punch 10: an accepted AI trim and speed change read "cut by hand". The
    assist records' accepted ops name the shots the AI cut (`ai_cuts`); a row whose every
    edit is one of them is the AI's (`by: "ai"`), still a locked edit; a rejected op, a
    grade op, and a row with an edit of the editor's own besides stay the editor's."""
    recs = [
        {"result": {"ops": [{"op": "trim", "id": "g02", "out": 46.0},
                            {"op": "speed", "id": "g04", "rate": 0.5},
                            {"op": "trim", "id": "g05", "in": 1.0},          # rejected
                            {"op": "set", "id": "g01", "prop": "contrast", "value": 20}]},
         "answer": {"accepted": [0, 1, 3], "rejected": [2]}},
        {"result": {"ops": [{"op": "roll", "a": "g03", "b": "g04", "by": 0.5},
                            {"op": "move", "ids": ["g05"], "before": "g01"}]},
         "answer": {"accepted": [0, 1]}},
        {"result": {"ops": [{"op": "trim", "id": "g01", "in": 9.0}]}},           # never answered
        {"result": {"ops": "garbled"}, "answer": {"accepted": [0, 7, "x"]}},
    ]
    assert firstpass.ai_cuts(recs) == {"g02": {"trim"}, "g04": {"speed", "trim"},
                                       "g03": {"trim"}, "g05": {"move"}}
    rows = [{"id": "g02", "how": ["trim"]}, {"id": "g04", "how": ["speed"]},
            {"id": "g03", "how": ["trim", "move"]}, {"id": "g01", "how": ["trim"]},
            {"id": "g09", "how": ["add"]}]
    assert [r["by"] for r in firstpass.mark_by(rows, firstpass.ai_cuts(recs))] == [
        "ai", "ai", "hand", "hand", "hand"]


def test_without_a_base_a_shot_is_the_machines_only_if_a_proposal_held_it():
    edl = _edl(CURRENT)
    rows = firstpass.manual_edits(edl, plans=[[{"clip": "CLIP_02.MP4", "in": 10.0,
                                                "out": 16.0}]])
    assert [r["id"] for r in rows] == ["g02", "g03", "g04", "g05"]
    assert all(r["how"] == ["add"] for r in rows)


def test_locks_hold_whatever_the_model_did():
    locked = ["g02", "g04"]
    proposed = [
        {"id": "g02", "clip": "CLIP_04.MP4", "in": 41.0, "out": 46.0},   # changed: put back
        {"clip": "CLIP_11.MP4", "in": 166.5, "out": 170.0},              # re-uses g04: dropped
        {"clip": "CLIP_08.MP4", "in": 2.0, "out": 8.0},                  # new
        {"id": "g01", "clip": "CLIP_02.MP4", "in": 10.0, "out": 14.0},   # replaced
    ]
    out = firstpass.enforce_locks(proposed, CURRENT, locked)
    by = {s.get("id"): s for s in out}
    assert by["g02"] == CURRENT[1] and by["g04"] == CURRENT[3]       # exact
    assert [s.get("id") for s in out].index("g02") < [s.get("id") for s in out].index("g04")
    assert not any(s["clip"] == "CLIP_11.MP4" and s.get("id") != "g04" for s in out)
    d = firstpass.rerun_diff(CURRENT, out, locked)
    st = {(r.get("id") or r["clip"]): (r["status"], r["locked"]) for r in d["shots"]}
    assert st["g02"] == ("kept", True) and st["g04"] == ("kept", True)
    assert st["g01"] == ("replaced", False) and st["CLIP_08.MP4"] == ("new", False)
    assert {r["id"] for r in d["removed"]} == {"g03", "g05"}
    assert d["sentence"].startswith("This keeps your 2 hand-edited shots exactly")


def test_resolve_keep():
    manual = [{"id": "g03", "how": ["trim"]}]
    assert firstpass.resolve_keep(None, CURRENT, manual, "rerun") == ["g03"]
    assert firstpass.resolve_keep(None, CURRENT, manual, "run") == []
    assert firstpass.resolve_keep(["g05", "g01"], CURRENT, manual, "run") == ["g01", "g05"]
    with pytest.raises(ValueError):
        firstpass.resolve_keep(["nope"], CURRENT, manual, "rerun")
    with pytest.raises(ValueError):
        firstpass.resolve_keep(7, CURRENT, manual, "rerun")


@pytest.mark.parametrize("backend, words", [("claude_cli", "free on your plan"),
                                            ("anthropic_api", "~$1.25")])
def test_cost_words_per_backend(monkeypatch, backend, words):
    monkeypatch.setenv("ROUGHCUT_BACKEND", backend)
    c = firstpass.cost_words(1.249)
    assert c["words"] == words and c["usd"] == 1.25
    assert c["charged"] is (backend == "anthropic_api") and c["backend"] == backend
    assert "$1.25" in c["hover"]


# ------------------------------------------------------------------ the API


class Scripted:
    """A model that answers the Ask's two calls: a first cut, or — when the prompt
    carries LOCKED shots — a re-cut that tampers with the locked one (the server must
    put it back) and misdescribes a shot it changed (the notes check must say so).
    `hold`, when set, makes the plan call wait — the running state, observed. `recut`,
    when set, is a re-run's answer (the re-run's note says "Re-run the first pass")."""

    name = "scripted"

    def __init__(self):
        self.seen: list = []
        self.hold: threading.Event | None = None
        self.recut: list[dict] | None = None

    def complete(self, request):
        self.seen.append(request)
        prompt = request.prompt
        if "segments" not in (request.schema or {}):
            text = "{}"                           # the estimate: it falls back
        elif self.recut is not None and "Re-run the first pass" in prompt:
            text = json.dumps({"segments": self.recut, "notes": "a re-cut"})
        elif firstpass_locked_mark() in prompt:
            if self.hold is not None:
                self.hold.wait(10)
            text = json.dumps({"segments": [
                {"clip": "CLIP_A.MP4", "in": 0.4, "out": 2.2, "why": "locked, nudged"},
                {"clip": "CLIP_C.MP4", "in": 0.5, "out": 4.0, "why": "new"},
                {"clip": "CLIP_B.MP4", "in": 0.5, "out": 2.0, "why": "a greeting"}],
                "notes": "I kept your CLIP_A shot exactly as it was. The CLIP_B shot is "
                         "unchanged."})
        else:
            if self.hold is not None:
                self.hold.wait(10)
            text = json.dumps({"segments": [
                {"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0, "why": "the greeting"},
                {"clip": "CLIP_B.MP4", "in": 2.4, "out": 4.0, "why": "the reply"}],
                "notes": "a conversation"})
        model = config.model_for(request.role)
        return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                backend="scripted", model=model, projected_usd=1e-4,
                                latency_ms=1, raw=text)


def firstpass_locked_mark() -> str:
    return "LOCKED"


@pytest.fixture
def model():
    m = Scripted()
    inference.set_backend(m)
    inference.reset_spend()
    yield m
    if m.hold is not None:
        m.hold.set()
    inference.set_backend(None)


@pytest.fixture
def fp(tmp_path, project, monkeypatch, model):
    """A fresh bin (no cut yet) over the suite's footage and sidecars, the index's
    tools stubbed, every write under tmp_path."""
    import server

    with _fresh(tmp_path, project, sidecars=project["sidecars"], visual=None) as c:
        _stub_tools(server, monkeypatch, tmp_path)
        monkeypatch.setattr(server, "FIRSTPASS_POLL_S", 0.05)
        server.FIRSTPASS.clear()
        # index jobs another file cancelled (test_firstpass_ui) stay in INDEXES and made a
        # re-run here wait on them: this test failed only when that file ran first (REVIEW)
        server.INDEXES.clear()
        yield c
        _end_jobs(server, model)
    server.FIRSTPASS.clear()


def _end_jobs(server, model=None, timeout=30.0) -> None:
    """No first pass, and no index it left finishing in the background, outlives its
    test: stop them the way the board's Stop does, and wait for them to end."""
    if model is not None and model.hold is not None:
        model.hold.set()
    for x in list(server.INDEXES.values()):
        if x["state"] not in server.progress.TERMINAL:
            x["cancel"] = True
            sc = server.INDEX_SCOPES.get(x["id"])
            if sc is not None:
                sc.kill()
    deadline = time.time() + timeout
    while time.time() < deadline and (
            any(x["state"] not in server.progress.TERMINAL for x in server.INDEXES.values())
            or any(x["state"] == "running" for x in server.FIRSTPASS.values())):
        time.sleep(0.05)


def _wait(c, until=("done", "stale-after-edits", "failed"), timeout=60.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = c.get("/api/firstpass").json()
        if s["state"] in until:
            return s
        time.sleep(0.1)
    raise AssertionError(f"first pass did not reach {until}: {s}")


def _wait_indexes(server, timeout=30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if all(x["state"] == "done" for x in server.INDEXES.values()):
            return
        time.sleep(0.05)
    raise AssertionError([(x["state"], x.get("detail")) for x in server.INDEXES.values()])


def _plan(c, job: str) -> dict:
    return c.get(f"/api/ask/{job}").json()["plan"]


def _segs(c) -> list[dict]:
    return c.get("/api/project").json()["segments"]


def test_get_says_never_run_and_what_a_run_would_do(fp, monkeypatch):
    monkeypatch.setenv("ROUGHCUT_BACKEND", "claude_cli")
    s = fp.get("/api/firstpass").json()
    assert s["state"] == "never-run" and s["label"] == "Run first pass"
    w = s["would"]
    assert w["mode"] == "run" and w["keep"] == [] and w["replace"] == []
    assert [st["key"] for st in w["steps"]] == ["index", "picks", "cut"]
    ix = w["steps"][0]
    assert ix["needed"] is True and ix["counts"]["listened"] == 3
    assert ix["counts"]["pct"] <= int(flow.honest_pct(ix["counts"]) * 100)
    assert w["steps"][2]["originate"] is True
    assert w["cost"]["words"] == "free on your plan" and w["cost"]["charged"] is False
    assert "$" in w["cost"]["hover"]
    assert all(st["cost"]["words"] == "free on your plan" for st in w["steps"])
    assert s["backend"] == "claude_cli" and s["charged"] is False


def test_cost_on_the_api_backend_is_a_price(fp, monkeypatch):
    monkeypatch.setenv("ROUGHCUT_BACKEND", "anthropic_api")
    w = fp.get("/api/firstpass").json()["would"]
    assert w["cost"]["charged"] is True and w["cost"]["words"].startswith("~$")
    assert w["cost"]["words"] == f"~${w['cost']['usd']:.2f}"
    assert w["steps"][1]["cost"]["words"] == "~$0.00"            # the picks are free


def test_run_then_hand_trim_then_rerun_keeps_the_trim(fp, model):
    import server

    # ---- run: index → picks → a proposed first cut; nothing written to the cut
    r = fp.post("/api/firstpass", json={"mode": "run"})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "run" and r.json()["keep"] == []
    s = _wait(fp)
    assert s["state"] == "done", s
    assert s["label"] == "Re-run first pass"
    assert s["produced"]["picks"] >= 0 and s["produced"]["cut"]["shots"] == 2
    assert s["produced"]["index"]["listened"] == 3
    assert s["proposal"]["pending"] is True
    job = s["proposal"]["job"]
    plan = _plan(fp, job)
    assert [r["status"] for r in plan["firstpass"]["diff"]["shots"]] == ["new", "new"]
    assert plan["notes_check"]["ok"] is True
    assert _segs(fp) == [], "a proposal, never a write"
    latest = fp.get("/api/asks/latest").json()
    assert latest["record"]["job"] == job and latest["pending"] is True
    # the index really ran, and its job ends (previews and close looks may still be
    # finishing in the background when the proposal lands)
    _wait_indexes(server)

    # ---- accept (the board saves the plan's shots verbatim): they are the machine's
    assert fp.put("/api/project", json={"segments": plan["segments"],
                                        "story": ""}).status_code == 200
    fp.post("/api/asks/answer", json={"answer": "accept"})
    s = fp.get("/api/firstpass").json()
    assert s["state"] == "done" and s["manual_edits"] == [], s

    # ---- a hand trim: the pass is now stale, and says which shot is the editor's
    segs = _segs(fp)
    trimmed = dict(segs[0], out=round(segs[0]["out"] - 0.37, 2))
    fp.put("/api/project", json={"segments": [trimmed, segs[1]], "story": ""})
    s = fp.get("/api/firstpass").json()
    assert s["state"] == "stale-after-edits", s
    assert [(m["id"], m["how"]) for m in s["manual_edits"]] == [(trimmed["id"], ["trim"])]
    w = s["would"]
    assert w["mode"] == "rerun" and w["locked"] == [trimmed["id"]]
    assert [k["id"] for k in w["keep"]] == [trimmed["id"]]
    assert [k["id"] for k in w["replace"]] == [segs[1]["id"]]
    assert "keeps 1 of your shots exactly" in w["steps"][2]["will"]

    # ---- re-run: the trimmed shot comes back with the same in/out, whatever the model did
    r = fp.post("/api/firstpass", json={"mode": "rerun"})
    assert r.status_code == 200, r.text
    assert r.json()["keep"] == [trimmed["id"]]
    s = _wait(fp)
    assert s["state"] == "done", s
    plan = _plan(fp, s["proposal"]["job"])
    kept = [x for x in plan["segments"] if x.get("id") == trimmed["id"]]
    assert len(kept) == 1
    assert (kept[0]["clip"], kept[0]["in"], kept[0]["out"]) == (
        trimmed["clip"], trimmed["in"], trimmed["out"])
    diff = plan["firstpass"]["diff"]
    rows = {(x.get("id") or x["clip"]): x for x in diff["shots"]}
    assert rows[trimmed["id"]]["status"] == "kept" and rows[trimmed["id"]]["locked"]
    assert rows[trimmed["id"]]["hand"] == ["trim"]
    assert rows["CLIP_C.MP4"]["status"] == "new"
    assert {x["status"] for x in diff["shots"]} <= {"kept", "replaced", "new"}
    assert diff["counts"]["locked"] == 1
    assert diff["sentence"].startswith("This keeps your 1 hand-edited shot exactly")
    # the prompt asked for the lock; the notes' false "unchanged" was caught
    assert "LOCKED" in model.seen[-1].prompt
    chk = plan["notes_check"]
    assert chk["ok"] is False
    assert {x["clip"] for f in chk["flags"] for x in f["shots"]} == {"CLIP_B.MP4"}
    assert "Check: shot 2 (CLIP_B 0:02.4" in plan["notes"] and "is removed" in plan["notes"]
    # still only a proposal
    assert [(x["in"], x["out"]) for x in _segs(fp)] == [(trimmed["in"], trimmed["out"]),
                                                       (segs[1]["in"], segs[1]["out"])]

    # ---- accepting the re-run keeps the trim the editor's, and the pass is not stale
    fp.put("/api/project", json={"segments": plan["segments"], "story": ""})
    s = fp.get("/api/firstpass").json()
    assert s["state"] == "done", s
    assert [(m["id"], m["how"]) for m in s["manual_edits"]] == [(trimmed["id"], ["trim"])]


def test_running_state_shows_the_stage_and_counts_and_refuses_a_second_run(fp, model):
    model.hold = threading.Event()
    assert fp.post("/api/firstpass", json={}).status_code == 200
    s = _wait(fp, until=("running",))
    deadline = time.time() + 30
    while s.get("stage") != "cut" and time.time() < deadline:
        time.sleep(0.05)
        s = fp.get("/api/firstpass").json()
    assert s["state"] == "running" and s["stage"] == "cut", s
    assert s["steps"]["index"]["state"] in ("done", "skipped")
    assert s["steps"]["picks"]["state"] == "done"
    ix = s["counts"]["index"]
    assert ix["pct"] <= int(flow.honest_pct(ix) * 100)
    assert f"heard {ix['listened']}/{ix['clips']}" in ix["words"]
    assert fp.post("/api/firstpass", json={}).status_code == 409
    model.hold.set()
    assert _wait(fp)["state"] == "done"


def test_bad_requests(fp):
    assert fp.post("/api/firstpass", json={"mode": "redo"}).status_code == 400
    assert fp.post("/api/firstpass", json={"keep": ["gnope"]}).status_code == 400
    assert fp.post("/api/firstpass", json={"keep": 3}).status_code == 400


def test_a_run_on_a_cut_with_hand_shots_and_no_base_keeps_them(tmp_path, project,
                                                                monkeypatch, model):
    """The suite's cut was made by hand (no proposal ever held it): a re-run locks both
    shots by default, and a run with keep "none" may replace everything."""
    import server

    edl = tmp_path / "edl.json"
    shutil.copy(project["edl"], edl)
    with _fresh(tmp_path, project, edl=edl, sidecars=project["sidecars"],
                visual=None) as c:
        _stub_tools(server, monkeypatch, tmp_path)
        server.FIRSTPASS.clear()
        s = c.get("/api/firstpass").json()
        assert s["state"] == "never-run"
        assert [m["how"] for m in s["manual_edits"]] == [["add"], ["add"]]
        assert s["would"]["mode"] == "rerun" and len(s["would"]["keep"]) == 2
        w = c.post("/api/firstpass", json={"mode": "run", "keep": "none"}).json()
        assert w["keep"] == [] and w["would"]["steps"][2]["originate"] is True
        assert "replace all 2 shots" in w["would"]["steps"][2]["will"]
        s = _wait(c)
        assert s["state"] == "done"
        d = _plan(c, s["proposal"]["job"])["firstpass"]["diff"]
        assert d["counts"]["removed"] == 2 and d["counts"]["locked"] == 0
        _end_jobs(server, model)
    server.FIRSTPASS.clear()


# ------------------------------------------------------------------ verdict 3, punch 5


def _run_and_accept(c) -> list[dict]:
    """A first pass, its proposal accepted the way the board accepts it."""
    assert c.post("/api/firstpass", json={"mode": "run"}).status_code == 200
    s = _wait(c)
    assert s["state"] == "done", s
    plan = _plan(c, s["proposal"]["job"])
    assert c.put("/api/project", json={"segments": plan["segments"],
                                       "story": ""}).status_code == 200
    c.post("/api/asks/answer", json={"answer": "accept"})
    return _segs(c)


def test_a_proposal_forms_while_the_proxies_never_finish(fp, monkeypatch):
    """Picks and the cut start once every clip is heard and looked at coarsely: a
    proxy encode that never ends (5-10 min a clip on Killington under load) is not a
    prerequisite of the proposal, and the index carries on with it in the background."""
    import server

    gate = threading.Event()
    started = []

    def never(src, dest, orient):
        started.append(Path(src).name)
        gate.wait(60)
        raise RuntimeError("released by the test")

    monkeypatch.setattr(server, "build_proxy", never)
    try:
        assert fp.post("/api/firstpass", json={"mode": "run"}).status_code == 200
        s = _wait(fp, timeout=60)
        assert s["state"] == "done", s
        assert s["proposal"]["pending"] is True and s["produced"]["cut"]["shots"] == 2
        assert started, "the proxy stage was running while the cut was made"
        assert s["produced"]["index"]["proxied"] == 0
        # the index is still going, in the background, on the previews
        live = [x for x in server.INDEXES.values()
                if x["state"] not in server.progress.TERMINAL]
        assert live and live[0].get("coarse_ready") is True
        j = server.load_journal()
        assert all(j.state(c, "look") == "done" for c in j.clips)
        assert any(j.state(c, "proxy") == "running" for c in j.clips)
        # the close look reads the proxy: it waits for it, never skips for want of it
        assert all(j.state(c, "close") == "queued" for c in j.clips)
    finally:
        gate.set()
        _end_jobs(server)


def test_a_graded_shot_is_not_locked_and_a_kept_shot_keeps_its_grade(fp, model):
    """Verdict 11, punch 6: a grade or an effect (a hand balance, the AI's Color Match, a
    Lumetri value or a Crop in Effect Controls) does not lock a shot's place in the cut —
    the pass is not stale for it and a re-run may replace the shot — and whatever shot
    the re-run keeps keeps it: the override is keyed by the shot's id, which the re-run
    carries, and the Effect Controls come over exactly."""
    segs = _run_and_accept(fp)
    assert fp.get("/api/firstpass").json()["manual_edits"] == []
    a, b = segs[0]["id"], segs[1]["id"]
    bal = {"gain": [1.05, 1.0, 0.95], "exposure": 1.1}
    colour = {"mode": "auto", "reference": a,
              "shots": {a: {"balance": bal}, b: {"match": "reference"}}}
    assert fp.put("/api/project", json={"colour": colour}).status_code == 200
    ctl = {"contrast": 12.0, "vfx": [{"id": "crop", "kind": "crop", "params": {"left": 10.0}}]}
    lum = [dict(segs[0], controls=ctl), dict(segs[1], controls={"saturation": 80.0})]
    assert fp.put("/api/project", json={"segments": lum, "story": ""}).status_code == 200
    s = fp.get("/api/firstpass").json()
    assert s["manual_edits"] == [] and s["state"] == "done", s
    assert s["would"]["locked"] == [] and s["would"]["keep"] == []
    assert [r["id"] for r in s["would"]["replace"]] == [a, b]

    # the re-run keeps shot 1 as it is and replaces shot 2
    model.recut = [{"clip": segs[0]["clip"], "in": segs[0]["in"], "out": segs[0]["out"],
                    "why": "the greeting"},
                   {"clip": "CLIP_C.MP4", "in": 0.5, "out": 3.0, "why": "a walk instead"}]
    r = fp.post("/api/firstpass", json={"mode": "rerun"})
    assert r.status_code == 200 and r.json()["keep"] == [], r.text
    s = _wait(fp)
    assert s["state"] == "done", s
    assert "Re-run the first pass" in model.seen[-1].prompt
    assert firstpass_locked_mark() not in model.seen[-1].prompt, "nothing is locked"
    plan = _plan(fp, s["proposal"]["job"])
    diff = plan["firstpass"]["diff"]
    rows = {(x.get("id") or x["clip"]): x for x in diff["shots"]}
    assert rows[a]["status"] == "kept" and not rows[a]["locked"]
    assert rows["CLIP_C.MP4"]["status"] == "new"
    assert [x["id"] for x in diff["removed"]] == [b], "the graded shot 2 is replaced"
    assert diff["counts"]["locked"] == 0 and diff["counts"]["replaced"] == 1
    kept = next(x for x in plan["segments"] if x.get("id") == a)
    assert (kept["clip"], kept["in"], kept["out"]) == (segs[0]["clip"], segs[0]["in"],
                                                       segs[0]["out"])
    assert kept["controls"] == ctl, "the kept shot's Lumetri and Crop come over exactly"

    # accepted: the kept shot keeps its grade; the pass has no hand edit to report
    assert fp.put("/api/project", json={"segments": plan["segments"],
                                        "story": ""}).status_code == 200
    fp.post("/api/asks/answer", json={"answer": "accept"})
    proj = fp.get("/api/project").json()
    got_bal = proj["colour"]["shots"][a]["balance"]
    assert {k: got_bal[k] for k in bal} == bal and proj["colour"]["reference"] == a
    got = {x["id"]: x for x in proj["segments"]}
    assert got[a]["controls"] == ctl and b not in got
    s = fp.get("/api/firstpass").json()
    assert s["manual_edits"] == [] and s["state"] == "done", s


def _timebase(fp, plan) -> float | None:
    """The rate a proposal is snapped to (verdict 10, punch 3): the sequence's timebase,
    or — a fresh bin's sequence has none until it holds a clip — the one its first clip
    will give it."""
    proj = fp.get("/api/project").json()
    if proj["fps"]:
        return proj["fps"]
    first = next(x["clip"] for x in plan["segments"] if not x["clip"].startswith("gen_"))
    return proj["clips"][first]["fps"]


def test_proposal_ranges_land_on_the_sequence_frame_grid(fp):
    """The journey: edit points between frames made the export VFR. Every range a
    proposal makes is a whole number of frames at the sequence rate."""
    assert fp.post("/api/firstpass", json={"mode": "run"}).status_code == 200
    s = _wait(fp)
    plan = _plan(fp, s["proposal"]["job"])
    fps = revise.exact_rate(_timebase(fp, plan))
    assert fps and fps > 1
    for x in plan["segments"]:
        for t in (x["in"], x["out"]):
            assert abs(t * fps - round(t * fps)) < 0.01, (t, fps, x)
        assert x["out"] - x["in"] >= 1 / fps - 1e-6
    # the pure rule: a copied range is left exactly as it is; a new one snaps
    cur = [{"id": "g1", "clip": "A.MP4", "in": 1.2345, "out": 3.21}]
    out = revise.to_sequence({"segments": [dict(cur[0]),
                                           {"clip": "B.MP4", "in": 0.51, "out": 0.512}]},
                             cur, 59.94)["segments"]
    assert (out[0]["in"], out[0]["out"]) == (1.2345, 3.21)
    r = 60000 / 1001
    assert out[1]["in"] == round(round(0.51 * r) / r, 4)
    assert round((out[1]["out"] - out[1]["in"]) * r) == 1, "never shorter than a frame"


def test_effect_controls_survive_a_rerun(fp):
    """The model never sees `controls`, so a re-run's plan carried none: every kept
    shot lost its Motion / Opacity / Volume on Accept. They come over exactly."""
    segs = _run_and_accept(fp)
    ctl = [{"opacity": 40.0, "position": [0.4, 0.55]}, {"volume_db": -6.0, "scale": 120.0}]
    with_ctl = [dict(s, controls=c) for s, c in zip(segs, ctl)]
    assert fp.put("/api/project", json={"segments": with_ctl, "story": ""}).status_code == 200
    assert fp.get("/api/firstpass").json()["manual_edits"] == [], \
        "opacity, motion and volume are not hand edits of the cut"
    assert fp.post("/api/firstpass", json={"mode": "rerun"}).status_code == 200
    s = _wait(fp)
    assert s["state"] == "done", s
    plan = _plan(fp, s["proposal"]["job"])
    by = {x.get("id"): x for x in plan["segments"]}
    for want in with_ctl:
        assert want["id"] in by, (want["id"], plan["segments"])
        assert by[want["id"]]["controls"] == want["controls"]
    # and accepting it keeps them on the cut
    fp.put("/api/project", json={"segments": plan["segments"], "story": ""})
    saved = {x["id"]: x.get("controls") for x in _segs(fp)}
    assert all(saved[w["id"]] == w["controls"] for w in with_ctl)


def test_a_stopped_run_is_a_state_with_what_finished_and_what_resumes(fp, monkeypatch):
    """Verdict 3: after Stop the panel said "Not run on this bin yet". A stopped run
    reads "stopped", names what finished and what a re-run picks up, and the label is
    Re-run first pass."""
    import server

    gate = threading.Event()
    stub_look = server.visual_cmd

    def slow_look(only, force):
        return [sys.executable, "-c", "import time; time.sleep(30)"]

    monkeypatch.setattr(server, "visual_cmd", slow_look)
    try:
        assert fp.post("/api/firstpass", json={"mode": "run"}).status_code == 200
        deadline = time.time() + 20
        while time.time() < deadline:
            j = server.load_journal() if server.journal_path().exists() else None
            if j and any(j.state(c, "look") == "running" for c in j.clips):
                break
            time.sleep(0.05)
        t0 = time.time()
        r = fp.post("/api/firstpass/cancel")
        assert r.status_code == 200 and r.json()["state"] == "stopped"
        s = fp.get("/api/firstpass").json()
        assert time.time() - t0 < 5
        assert s["state"] == "stopped" and s["label"] == "Re-run first pass", s
        assert s["step"] == "index" and s["finished"] == "heard 3/3 · looked 0/3"
        assert s["resumes"] == "looks at 3 more, then picks, then the cut"
        assert s["when"] and abs(s["when"] - time.time()) < 10
        r = fp.post("/api/firstpass", json={})         # a re-run straight away
        assert r.status_code == 200, r.text
        assert fp.post("/api/firstpass/cancel").status_code == 200
        _end_jobs(server)
        # the killed looks are queued again, not failed: three Stops never park a clip
        j = server.load_journal()
        for c in j.clips:
            st = j.clips[c]["stages"]["look"]
            assert st["state"] == "queued" and st["attempts"] == 0, (c, st)
        # still stopped after the threads unwound, and the record survives
        s = fp.get("/api/firstpass").json()
        assert s["state"] == "stopped" and s["last"]["state"] == "stopped"
        assert s["last"]["job"] == r.json()["job"], "the newest run's record, not the older"
        # a re-run after the Stop runs a fresh index (the stopped one is not joined)
        monkeypatch.setattr(server, "visual_cmd", stub_look)
        assert fp.post("/api/firstpass", json={}).status_code == 200
        s = _wait(fp)
        assert s["state"] == "done", s
        assert s["produced"]["index"]["looked"] == 3
    finally:
        gate.set()
        _end_jobs(server)


def test_resume_words():
    c = {"clips": 12, "listened": 12, "looked": 1, "look_need": 12}
    assert firstpass.finished_words(c) == "heard 12/12 · looked 1/12"
    assert firstpass.resume_words(c) == "looks at 11 more, then picks, then the cut"
    assert firstpass.resume_words({**c, "listened": 3}) == \
        "hears 9 more, looks at 11 more, then picks, then the cut"
    assert firstpass.resume_words({**c, "looked": 12}, "cut").startswith("writes the cut again")


@pytest.mark.parametrize("order", ["save-then-answer", "answer-then-save"])
def test_a_partial_accept_records_a_base_and_a_rerun_keeps_only_the_hand_edits(fp, order):
    """PICKS (verdict 3 punch 7): the editor can take some of a proposal's shots. A
    partial Accept is not the plan verbatim, so no base was recorded and a later
    re-run counted the accepted AI shots as the editor's own. Now the accepted shots
    are the machine's, the rest keep the provenance they had: 2 of 5 accepted, and
    the re-run's keep list is exactly the prior hand edits."""
    import server

    a, b = _run_and_accept(fp)
    # hand edits: A trimmed, three shots added by hand
    a_t = dict(a, out=round(a["out"] - 0.25, 2))
    adds = [{"id": f"gh{i}", "clip": "CLIP_C.MP4", "in": 0.5 + i, "out": 1.0 + i,
             "why": "by hand"} for i in range(3)]
    cut = [a_t, b] + adds
    assert fp.put("/api/project", json={"segments": cut, "story": ""}).status_code == 200
    prior = {m["id"]: m["how"] for m in fp.get("/api/firstpass").json()["manual_edits"]}
    assert prior == {a_t["id"]: ["trim"], "gh0": ["add"], "gh1": ["add"], "gh2": ["add"]}

    # an Ask proposal of 5 shots: A kept (status kept), B, gh0 and gh1 changed, one new
    segs = _segs(fp)
    plan_segs = [dict(segs[0]),
                 dict(segs[1], **{"in": 0.3, "out": 1.9}),
                 dict(segs[2], out=1.3),
                 dict(segs[3], out=2.3),
                 {"clip": "CLIP_A.MP4", "in": 3.0, "out": 4.0, "why": "new"}]
    plan = {"segments": plan_segs, "notes": "five", "diff": revise.shot_diff(segs, plan_segs)}
    rec = {"job": "partial1", "created": time.time(), "note": "", "story": "", "plan": plan}
    time.sleep(0.05)
    (server.STATE["asks"] / "partial1.json").write_text(json.dumps(rec), encoding="utf-8")
    # the editor accepts B's change and the new shot; rejects gh0's and gh1's changes
    new_shot = dict(plan_segs[4], id="gnew")
    saved = [dict(segs[0]), plan_segs[1], dict(segs[2]), dict(segs[3]), dict(segs[4]),
             new_shot]
    answer = {"answer": "accept", "partial": {"accepted": 2, "of": 5, "rejected": [2, 3],
                                              "kept": []}}
    if order == "save-then-answer":
        assert fp.put("/api/project", json={"segments": saved, "story": ""}).status_code == 200
        assert fp.post("/api/asks/answer", json=answer).status_code == 200
    else:
        assert fp.post("/api/asks/answer", json=answer).status_code == 200
        assert fp.put("/api/project", json={"segments": saved, "story": ""}).status_code == 200
    edl = json.loads(server.STATE["edl"].read_text(encoding="utf-8"))
    assert firstpass.bases(edl)[0]["job"] == "partial1", "a base for the partial accept"
    s = fp.get("/api/firstpass").json()
    assert {m["id"]: m["how"] for m in s["manual_edits"]} == prior
    assert sorted(s["would"]["locked"]) == sorted(prior), s["would"]["locked"]
    assert b["id"] not in s["would"]["locked"] and "gnew" not in s["would"]["locked"]


# ------------------------------------------------------------------ verdict 4, punch 2
#
# The judge's re-run on Killington kept the hand trim exactly, but rounded 12 "kept"
# shots to hundredths (off the frame grid), brought back a shot the editor had
# ✗-rejected a minute earlier, and labelled the trim "added by hand".

FIRST_FIVE = [                                 # every edit point between 24 fps frames
    {"clip": "CLIP_A.MP4", "in": 0.45, "out": 1.95, "why": "one"},
    {"clip": "CLIP_B.MP4", "in": 0.45, "out": 1.95, "why": "two"},
    {"clip": "CLIP_C.MP4", "in": 0.45, "out": 1.95, "why": "three — rejected"},
    {"clip": "CLIP_A.MP4", "in": 3.05, "out": 4.55, "why": "four"},
    {"clip": "CLIP_B.MP4", "in": 3.05, "out": 4.55, "why": "five — rejected"},
]
CUT_LINE = re.compile(r"^\s*\d+\. (\S+) (\d+\.\d\d)-(\d+\.\d\d) ", re.M)


class Echoing(Scripted):
    """The first pass proposes FIRST_FIVE; a re-run types every shot of the cut back the
    way the prompt prints it (hundredths) and re-adds the first rejected range, saying
    so as a choice — the Killington re-run."""

    def complete(self, request):
        self.seen.append(request)
        prompt = request.prompt
        if "segments" not in (request.schema or {}):
            text = "{}"
        elif "Re-run the first pass" in prompt:
            cut = prompt.split("## The current edit, in order", 1)[1].split("##", 1)[0]
            segs = [{"clip": c, "in": float(a), "out": float(b), "why": "kept"}
                    for c, a, b in CUT_LINE.findall(cut)]
            segs.insert(1, {"clip": "CLIP_C.MP4", "in": 0.45, "out": 1.95,
                            "why": "the vow"})
            text = json.dumps({"segments": segs,
                               "notes": "I also added the CLIP_C vow back in."})
        else:
            text = json.dumps({"segments": FIRST_FIVE, "notes": "five shots"})
        return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                backend="scripted", model=config.model_for(request.role),
                                projected_usd=1e-4, latency_ms=1, raw=text)


def _on_grid(t: float, fps: float) -> bool:
    return abs(t * fps - round(t * fps)) < 1e-3


@pytest.mark.parametrize("order", ["save-then-answer", "answer-then-save"])
def test_a_rerun_keeps_what_it_says_and_remembers_rejections(fp, order):
    import server

    m = Echoing()
    inference.set_backend(m)
    assert fp.post("/api/firstpass", json={"mode": "run"}).status_code == 200
    s = _wait(fp)
    assert s["state"] == "done", s
    plan = _plan(fp, s["proposal"]["job"])
    fps = revise.exact_rate(_timebase(fp, plan))
    assert len(plan["segments"]) == 5
    assert all(_on_grid(t, fps) for x in plan["segments"] for t in (x["in"], x["out"]))

    # ---- the editor ✗-rejects shots 3 and 5 and accepts the other three, as the board
    # does: the reviewed cut saved, the answer naming the rejected shots and ranges
    rej = [2, 4]
    saved = [dict(x) for k, x in enumerate(plan["segments"]) if k not in rej]
    ranges = [{k2: plan["segments"][k][k2] for k2 in ("clip", "in", "out")} for k in rej]
    answer = {"answer": "accept", "partial": {"accepted": 3, "of": 5, "rejected": rej,
                                              "kept": [], "ranges": ranges}}
    steps = [lambda: fp.put("/api/project", json={"segments": saved, "story": ""}),
             lambda: fp.post("/api/asks/answer", json=answer)]
    for step in (steps if order == "save-then-answer" else steps[::-1]):
        assert step().status_code == 200
    edl = json.loads(server.STATE["edl"].read_text(encoding="utf-8"))
    remembered = firstpass.rejections(edl)
    assert sorted((r["clip"], r["in"], r["out"]) for r in remembered) == sorted(
        (r["clip"], r["in"], r["out"]) for r in ranges)
    assert all(r["at"] and r["job"] == s["proposal"]["job"] for r in remembered)
    assert fp.get("/api/firstpass").json()["manual_edits"] == []

    # ---- a hand trim of shot 1, two frames off its out (on the grid, as the board trims)
    segs = _segs(fp)
    trimmed = dict(segs[0], out=round(segs[0]["out"] - 2 / fps, 4))
    cut = [trimmed] + segs[1:]
    assert fp.put("/api/project", json={"segments": cut, "story": ""}).status_code == 200
    st = fp.get("/api/firstpass").json()
    assert [(r["id"], r["how"], r["words"]) for r in st["manual_edits"]] == [
        (trimmed["id"], ["trim"], "trimmed")], "a trim reads trimmed, not added by hand"
    assert st["would"]["locked"] == [trimmed["id"]]

    # ---- the re-run: the model echoes every shot in hundredths and re-adds a rejected one
    assert fp.post("/api/firstpass", json={"mode": "rerun"}).status_code == 200
    s = _wait(fp)
    assert s["state"] == "done", s
    prompt = m.seen[-1].prompt
    assert "REJECTED" in prompt, "the brief names the ranges not to propose"
    for r in ranges:
        assert f"{r['clip']} {r['in']:.2f}-{r['out']:.2f}" in prompt
    plan = _plan(fp, s["proposal"]["job"])
    now = {x["id"]: x for x in _segs(fp)}
    # kept shots are the cut's values exactly, on the frame grid, whatever was typed
    assert [x.get("id") for x in plan["segments"]] == [x["id"] for x in cut]
    for x in plan["segments"]:
        want = now[x["id"]]
        assert (x["clip"], x["in"], x["out"]) == (want["clip"], want["in"], want["out"]), x
        assert _on_grid(x["in"], fps) and _on_grid(x["out"], fps), x
    rows = plan["firstpass"]["diff"]["shots"]
    assert [r["status"] for r in rows] == ["kept"] * len(cut)
    assert [r["locked"] for r in rows] == [True] + [False] * (len(cut) - 1)
    assert rows[0]["hand"] == ["trim"]
    # the rejected range is not back; the notes say it was left out, and why
    assert not any(x["clip"] == "CLIP_C.MP4" for x in plan["segments"])
    left = plan["firstpass"]["rejected"]
    assert [(r["clip"], r["action"]) for r in left] == [("CLIP_C.MP4", "dropped")]
    assert plan["notes_check"]["rejected"] == left
    assert "Left out: CLIP_C 0:00." in plan["notes"], plan["notes"]
    assert "you rejected this shot before, so it stays out" in plan["notes"]
    assert plan["firstpass"]["diff"]["counts"]["new"] == 0


def test_rejections_are_ranges_the_proposal_made_and_hold_a_rerun():
    """The pure rules: only a range the proposal made is a rejection (a rejected shot
    it kept as it is stays in the cut); a re-proposed rejected range is dropped, a
    rejected change to a shot of the cut leaves that shot as it is; a shot in the cut
    now is never touched; and accepting the exact range later forgets the rejection."""
    plan_segs = [dict(CURRENT[0]),                                   # kept as it is
                 dict(CURRENT[1], out=45.0),                         # a change
                 {"clip": "CLIP_09.MP4", "in": 1.0, "out": 4.0}]     # new
    plan = {"segments": plan_segs, "diff": revise.shot_diff(CURRENT, plan_segs)}
    got = firstpass.rejected_ranges(plan, {"rejected": [0, 1, 2], "ranges": [
        {"clip": "CLIP_02.MP4", "in": 10.0, "out": 16.0},             # kept: not one
        {"clip": "CLIP_99.MP4", "in": 0.0, "out": 1.0}]})            # not proposed
    assert [(r["clip"], r["in"], r["out"]) for r in got] == [
        ("CLIP_04.MP4", 40.0, 45.0), ("CLIP_09.MP4", 1.0, 4.0)]
    edl = _edl(CURRENT)
    assert firstpass.record_rejections(edl, got, job="j1", at=1.0)
    assert not firstpass.record_rejections(edl, got[:1], job="j2"), "each range once"

    proposed = [{"id": "g02", "clip": "CLIP_04.MP4", "in": 40.0, "out": 45.1},  # change again
                {"clip": "CLIP_09.MP4", "in": 1.2, "out": 4.0},                 # mostly it
                {"clip": "CLIP_09.MP4", "in": 8.0, "out": 12.0},                # elsewhere
                dict(CURRENT[4])]
    out, left = firstpass.drop_rejected(proposed, CURRENT, firstpass.rejections(edl))
    assert [(x.get("id"), x["clip"], x["in"], x["out"]) for x in out] == [
        ("g02", "CLIP_04.MP4", 40.0, 47.5), (None, "CLIP_09.MP4", 8.0, 12.0),
        ("g05", "CLIP_07.MP4", 3.0, 9.0)]
    assert [r["action"] for r in left] == ["kept as it is", "dropped"]
    lines = firstpass.rejected_lines(left)
    assert "you rejected this change before" in lines[0]
    assert "you rejected this shot before" in lines[1]
    # a rejected range the cut holds now (added back by hand) is the editor's latest word
    cut = CURRENT + [{"id": "gb", "clip": "CLIP_09.MP4", "in": 1.0, "out": 4.0}]
    out, left = firstpass.drop_rejected([dict(cut[-1])], cut, firstpass.rejections(edl))
    assert left == [] and out == [cut[-1]]
    # accepting that exact range later forgets the rejection
    firstpass.record_base(edl, cut, job="j3")
    assert [(r["clip"], r["in"]) for r in firstpass.rejections(edl)] == [("CLIP_04.MP4", 40.0)]
    # the brief
    note = firstpass.plan_note(0, "rerun", "", firstpass.rejections(edl))
    assert "REJECTED" in note and "CLIP_04.MP4 40.00-45.00" in note


def test_a_trim_after_the_ids_were_reminted_reads_trimmed():
    """Killington: after a partial Accept the board saved again before re-keying its
    temporary ids, so every shot came back under a fresh id, and the one the editor
    then trimmed read "added by hand". A base shot is found again by its footage."""
    edl = firstpass.record_base(_edl(CURRENT), CURRENT, job="j1")
    fresh = [dict(s, id=f"n{i}") for i, s in enumerate(CURRENT)]
    edl["segments"] = fresh
    assert firstpass.manual_edits(edl) == []
    fresh[2]["out"] = 165.0                                        # a trim
    fresh.append({"id": "nadd", "clip": "CLIP_09.MP4", "in": 1.0, "out": 4.0})
    rows = {r["id"]: (r["how"], r["words"]) for r in firstpass.manual_edits(edl)}
    assert rows == {"n2": (["trim"], "trimmed"), "nadd": (["add"], "added by hand")}
    # the panel's rows carry the cut's own values, not hundredths
    fresh[2]["out"] = 165.0417
    row = next(r for r in firstpass.manual_edits(edl) if r["id"] == "n2")
    assert row["out"] == 165.0417


def test_an_echo_in_hundredths_snaps_back_to_the_cut():
    cur = [{"id": "g1", "clip": "A.MP4", "in": 1.4014, "out": 10.2102}]
    echo = {"id": "g1", "clip": "A.MP4", "in": 1.4, "out": 10.21}
    out = revise.to_sequence({"segments": [echo]}, cur, 59.94)["segments"]
    assert (out[0]["in"], out[0]["out"]) == (1.4014, 10.2102)
    # a deliberate change of a frame or more is not an echo: it snaps to the grid instead
    moved = revise.to_sequence({"segments": [dict(echo, out=10.19)]}, cur, 59.94)["segments"]
    assert moved[0]["in"] == 1.4014 or _on_grid(moved[0]["in"], 60000 / 1001)
    assert moved[0]["out"] != 10.2102 and _on_grid(moved[0]["out"], 60000 / 1001)
