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
    `hold`, when set, makes the plan call wait — the running state, observed."""

    name = "scripted"

    def __init__(self):
        self.seen: list = []
        self.hold: threading.Event | None = None

    def complete(self, request):
        self.seen.append(request)
        prompt = request.prompt
        if "segments" not in (request.schema or {}):
            text = "{}"                           # the estimate: it falls back
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
        yield c
    server.FIRSTPASS.clear()


def _wait(c, until=("done", "stale-after-edits", "failed"), timeout=60.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = c.get("/api/firstpass").json()
        if s["state"] in until:
            return s
        time.sleep(0.1)
    raise AssertionError(f"first pass did not reach {until}: {s}")


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
    # the index really ran, and its job ended
    assert all(x["state"] == "done" for x in server.INDEXES.values())

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
    server.FIRSTPASS.clear()
