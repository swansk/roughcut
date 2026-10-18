"""An import never runs a priced stage unasked (verdict 9, punch 2; REDESIGN-GOAL dimension 13).

Verdict 9: *"each import joined the background index that was still running after the
pass, and the priced look ran unasked on every imported clip"* — `KX_05.visual.json`
15 s after the import, the journal at `look: done, cost_usd 0.27`. Free on `claude_cli`,
billed on `anthropic_api`. Now footage imported into a running index joins it the way a
fresh free run takes it: probed, heard, previewed, and its look and close look HELD
(`journal.hold_priced`) until the editor asks — the re-run, or the Index button. The
"N new clips" offer and the re-run panel carry the price of those held stages, and the
re-run's proposal says what it did with every new clip.

Every model call is scripted; the index runs with test_index's stubbed tools, its proxy
encodes held open so the post-pass background index is still running at the import
(the verdict's case). The bin is a copy of the suite's fixture under tmp_path, so the
stub analyser's sidecars for the imported clips never land in the shared fixture.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import config, inference, journal  # noqa: E402

from test_firstpass import _end_jobs, _plan, _wait  # noqa: E402
from test_index import _stub_tools, _wait_index  # noqa: E402

NEW = ["KX_05.MP4", "KX_10.MP4"]
NEW_MARK = "## New footage since the last pass"


def _make(path: Path, src: str, seconds: float = 3.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i", f"{src}:size=320x180:rate=24:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True)


@pytest.fixture(scope="module")
def card(tmp_path_factory) -> Path:
    """A second card: two clips the bin does not have."""
    root = tmp_path_factory.mktemp("held-card")
    _make(root / "KX_05.MP4", "color=c=red")
    _make(root / "KX_10.MP4", "color=c=blue")
    return root


class Model:
    """The pass's calls: a first cut, then a re-run that (when its prompt lists new
    footage) uses KX_10 and, if `say` is set, accounts for both new clips."""

    name = "scripted"

    def __init__(self, say: bool = True):
        self.say = say
        self.seen: list = []

    def complete(self, request):
        self.seen.append(request)
        prompt = request.prompt
        if "segments" not in (request.schema or {}):
            text = "{}"                                   # the estimate: falls back
        elif NEW_MARK in prompt:
            body = {"segments": [
                {"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0, "why": "the greeting"},
                {"clip": "KX_10.MP4", "in": 0.5, "out": 2.5, "why": "the blue run"},
                {"clip": "CLIP_B.MP4", "in": 2.4, "out": 4.0, "why": "the reply"}],
                "notes": "a conversation, with the new run in the middle"}
            if self.say:
                body["new_clips"] = [
                    {"clip": "KX_10.MP4", "used": True, "note": "the blue run, as shot 2"},
                    {"clip": "kx_05", "used": False, "note": "a near-duplicate of CLIP_B"}]
            text = json.dumps(body)
        else:
            text = json.dumps({"segments": [
                {"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0, "why": "the greeting"},
                {"clip": "CLIP_B.MP4", "in": 2.4, "out": 4.0, "why": "the reply"}],
                "notes": "a conversation"})
        return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                backend="scripted", model=config.model_for(request.role),
                                projected_usd=1e-4, latency_ms=1, raw=text)


@pytest.fixture
def board(tmp_path, project, monkeypatch, request):
    """A copy of the fixture bin, the index's tools stubbed, every proxy encode held
    open by `gate` (so the index keeps running after a pass), the import's free index
    on. Yields (client, server, model, gate, looked) — `looked` is every clip the look
    stage was asked for."""
    from fastapi.testclient import TestClient
    import server

    say = getattr(request, "param", True)
    model = Model(say=say)
    inference.set_backend(model)
    inference.reset_spend()
    monkeypatch.setenv("ROUGHCUT_BACKEND", "claude_cli")
    footage, sidecars = tmp_path / "bin" / "day1", tmp_path / "sidecars"
    footage.mkdir(parents=True)
    sidecars.mkdir()
    for stem in project["stems"]:
        shutil.copy(project["footage"] / f"{stem}.MP4", footage / f"{stem}.MP4")
        shutil.copy(project["sidecars"] / f"{stem}.audio.json", sidecars / f"{stem}.audio.json")
    server.configure(None, footage, sidecars, tmp_path / "work", proxies=False, visual=None)
    _stub_tools(server, monkeypatch, tmp_path)
    monkeypatch.setattr(server, "FIRSTPASS_POLL_S", 0.05)
    server.FIRSTPASS.clear()
    server.INDEXES.clear()
    gate = threading.Event()

    def held_open(src, dest, orient):
        gate.wait(60)
        shutil.copy(src, dest)
        return "copied"

    monkeypatch.setattr(server, "build_proxy", held_open)
    looked: list[str] = []
    real = server.visual_cmd
    monkeypatch.setattr(server, "visual_cmd",
                        lambda only, force: (looked.extend(only), real(only, force))[1])
    server.STATE["index_on_import"] = True
    try:
        with TestClient(server.app) as c:
            yield c, server, model, gate, looked
    finally:
        gate.set()
        server.STATE.pop("index_on_import", None)
        _end_jobs(server)
        server.FIRSTPASS.clear()
        inference.set_backend(None)


def _live_index(server) -> dict | None:
    return next((x for x in server.INDEXES.values()
                 if x["state"] not in server.progress.TERMINAL), None)


def _journal(server) -> journal.Journal:
    return journal.Journal.load(server.journal_path())


def _until(pred, what: str, timeout: float = 30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        got = pred()
        if got:
            return got
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def _clip_cost(j: journal.Journal, clip: str) -> float:
    return round(sum(float(st.get("cost_usd") or 0.0)
                     for st in j.clips[clip]["stages"].values()), 4)


def _pass_then_import(c, server, card, looked, *, accept: bool) -> dict:
    """A first pass (its proposal accepted, or left waiting), the index still running
    on the held-open previews, then two clips imported into it. Returns the live index."""
    assert c.post("/api/firstpass", json={"mode": "run"}).status_code == 200
    s = _wait(c)
    assert s["state"] == "done", s
    if accept:
        plan = _plan(c, s["proposal"]["job"])
        assert c.put("/api/project", json={"segments": plan["segments"],
                                           "story": ""}).status_code == 200
        c.post("/api/asks/answer", json={"answer": "accept"})
    live = _live_index(server)
    assert live is not None and not live.get("free"), "the post-pass background index runs"
    assert sorted(looked) == ["CLIP_A", "CLIP_B", "CLIP_C"]
    got = c.post("/api/import", json={"paths": [str(card)]}).json()
    assert got["added"] == NEW, got
    assert got["index"] == live["id"], "the import joins the running index"
    # heard and probed in this run, as the import promised...
    _until(lambda: all(c in _journal(server).clips
                       and _journal(server).state(c, "asr") == "done" for c in NEW),
           "the imported clips to be heard")
    # ...and given every chance to be looked at: the sheet pool is idle (the close looks
    # wait on the held-open previews), so an unheld look would start at once
    time.sleep(6 * server.INDEX_WAIT_S + 0.5)
    return live


def _assert_held(c, server, looked) -> None:
    j = _journal(server)
    for clip in NEW:
        assert j.state(clip, "look") == "queued", clip
        assert j.state(clip, "close") == "queued", clip
        assert j.held(clip), clip
        assert _clip_cost(j, clip) == 0.0, clip
        assert not (server.STATE["visual"] / f"{Path(clip).stem}.visual.json").exists(), clip
    assert not set(looked) & {"KX_05", "KX_10"}, looked
    assert not j.paused_priced, "a per-clip hold, never the bin's pause"
    st = c.get("/api/index").json()
    assert st["progress"]["held"] == NEW
    assert st["waiting"]["looks"] >= 2 and st["waiting"]["usd"] > 0
    rows = {r["clip"]: r for r in st["progress"]["rows"]}
    assert all(rows[c]["held"] and rows[c]["stages"]["look"] == "queued" for c in NEW)
    # the offer and the panel say what taking them in costs, the plan's way
    s = c.get("/api/firstpass").json()
    assert s["new_clips"] == NEW
    cost = s["new_cost"]
    assert cost["looks"] == 2 and cost["close"] == 2 and cost["usd"] > 0
    assert cost["words"] == "free on your plan" and cost["charged"] is False
    assert f"~${cost['usd']:.2f} if it went through the API" in cost["hover"]
    assert "Index the rest" in s["would"]["steps"][0]["will"]
    assert s["would"]["steps"][0]["cost"]["usd"] >= cost["usd"]


def test_an_import_into_the_running_index_holds_its_look_until_the_rerun(board, card):
    """The verdict's case: the pass's index still running in the background, two clips
    imported into it. They are heard, never looked at, and cost nothing, until Re-run —
    which takes the running index's looks on, waits for them, and names both clips in
    its notes."""
    c, server, model, gate, looked = board
    live = _pass_then_import(c, server, card, looked, accept=False)
    _assert_held(c, server, looked)

    # ---- Re-run: the editor asks; the running index takes the looks on
    assert _live_index(server)["id"] == live["id"]
    r = c.post("/api/firstpass", json={"mode": "rerun"})
    assert r.status_code == 200, r.text
    s = _wait(c)
    assert s["state"] in ("done", "stale-after-edits"), s
    assert server.FIRSTPASS[r.json()["job"]]["index_job"] == live["id"], "it joined"
    j = _journal(server)
    for clip in NEW:
        assert j.state(clip, "look") == "done", clip
        assert not j.held(clip), clip
        assert _clip_cost(j, clip) > 0, clip
        assert (server.STATE["visual"] / f"{Path(clip).stem}.visual.json").exists()
    assert {"KX_05", "KX_10"} <= set(looked)
    # the prompt named the new footage and asked for a word on each
    rerun = [q for q in model.seen if NEW_MARK in q.prompt]
    assert len(rerun) == 1
    assert "KX_05.MP4" in rerun[0].prompt and "KX_10.MP4" in rerun[0].prompt
    assert "new_clips" in rerun[0].schema
    # ...and the proposal says what it did with every one of them
    plan = _plan(c, s["proposal"]["job"])
    rows = {r["clip"]: r for r in plan["firstpass"]["new_clips"]}
    assert set(rows) == set(NEW)
    assert rows["KX_10.MP4"]["used"] and rows["KX_10.MP4"]["shots"] == [2]
    assert rows["KX_10.MP4"]["line"] == "Used new clip KX_10 in shot 2: the blue run, as shot 2."
    assert not rows["KX_05.MP4"]["used"] and rows["KX_05.MP4"]["source"] == "model"
    assert rows["KX_05.MP4"]["line"] == "Left out new clip KX_05: a near-duplicate of CLIP_B."
    for r in rows.values():
        assert r["line"] in plan["notes"]
    assert s["proposal"]["new_clips"] == plan["firstpass"]["new_clips"]
    # nothing is new to the next run
    assert not c.get("/api/firstpass").json().get("new_clips")


@pytest.mark.parametrize("board", [False], indirect=True)
def test_the_hold_outlives_the_index_and_a_silent_rerun_still_names_each_clip(board, card):
    """The index ends with the imported clips still held (the journal keeps the hold);
    the re-run starts a new index, which is the editor's ask, so it looks at them. The
    model says nothing about them: the notes still name each, from the plan."""
    c, server, model, gate, looked = board
    _pass_then_import(c, server, card, looked, accept=True)
    _assert_held(c, server, looked)
    gate.set()                                        # the previews land; the index ends
    job = _live_index(server)["id"]
    s = _wait_index(c, job, timeout=60)
    assert s["state"] == "done", s
    assert "2 imported clips not looked at yet: the look is priced" in s["detail"], s["detail"]
    j = _journal(server)
    assert j.held_clips() == NEW and all(j.state(c, "look") == "queued" for c in NEW)
    assert all(_clip_cost(j, c) == 0.0 for c in NEW)

    r = c.post("/api/firstpass", json={"mode": "rerun"})
    assert r.status_code == 200, r.text
    s = _wait(c)
    assert s["state"] in ("done", "stale-after-edits"), s
    j = _journal(server)
    assert all(j.state(c, "look") == "done" and not j.held(c) for c in NEW)
    plan = _plan(c, s["proposal"]["job"])
    rows = {r["clip"]: r for r in plan["firstpass"]["new_clips"]}
    assert rows["KX_10.MP4"]["source"] == "shot"
    assert rows["KX_10.MP4"]["line"] == "Used new clip KX_10 in shot 2: the blue run."
    assert rows["KX_05.MP4"]["line"] == "Left out new clip KX_05 (no reason given)."
    assert all(r["line"] in plan["notes"] for r in rows.values())


def test_the_index_button_is_an_ask_for_the_held_looks(board, card):
    """Index's own priced button (POST /api/index, /open) while the background index
    holds imported clips: no 409 — the running index takes their looks on."""
    c, server, model, gate, looked = board
    live = _pass_then_import(c, server, card, looked, accept=False)
    _assert_held(c, server, looked)
    r = c.post("/api/index", json={})
    assert r.status_code == 200 and r.json() == {"job": live["id"], "joined": True}, r.text
    _until(lambda: all(_journal(server).state(c, "look") == "done" for c in NEW),
           "the held looks to run")
    assert not _journal(server).held_clips()
    # nothing held any more: a second press is the old "one at a time"
    assert c.post("/api/index", json={}).status_code == 409


# ------------------------------------------------------------------ the pure halves


def test_the_answer_s_word_on_new_clips_is_validated_forgivingly():
    from roughcut import revise

    new = ["KX_05.MP4", "KX_10.MP4"]
    raw = [{"clip": "kx_10", "used": True, "note": "  the blue   run "},
           {"clip": "KX_10.MP4", "used": False, "note": "a second word on it"},   # dup
           {"clip": "CLIP_A.MP4", "used": True, "note": "not new"},              # unknown
           {"clip": "KX_05.MP4", "used": False, "note": ""},                     # no words
           "KX_05", None]
    assert revise.validate_new_clips(raw, new) == [
        {"clip": "KX_10.MP4", "used": True, "note": "the blue run"}]
    assert revise.validate_new_clips("nope", new) == []
    assert revise.validate_new_clips(raw, []) == []
    long = revise.validate_new_clips([{"clip": "KX_05.MP4", "note": "word " * 80}], new)
    assert len(long[0]["note"]) <= revise.NEW_NOTE_MAX and long[0]["note"].endswith("…")
    # the prompt names them only when there are any, and the answer's shape asks for them
    segs = [{"id": "s1", "clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0, "why": "hi"}]
    clips = {"CLIP_A.MP4": {"clip": "CLIP_A.MP4", "duration": 6.0, "transcript": [],
                            "summary": {}}}
    with_new = revise.build_prompt(segs, clips, "", "re-run", (5, 20), new_clips=new)
    assert NEW_MARK in with_new and "  - KX_05.MP4\n  - KX_10.MP4" in with_new
    assert "`new_clips`" in with_new
    assert NEW_MARK not in revise.build_prompt(segs, clips, "", "re-run", (5, 20))
    assert NEW_MARK in revise.build_first_prompt(clips, "", "", (5, 20), new_clips=new)
    plan = revise.validate_plan({"segments": [{"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0}],
                                 "new_clips": raw}, clips, new_clips=new)
    assert plan["new_clips"] == [{"clip": "KX_10.MP4", "used": True, "note": "the blue run"}]
    assert "new_clips" not in revise.validate_plan(
        {"segments": [{"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0}], "new_clips": raw}, clips)


def test_each_new_clip_gets_one_line_and_the_plan_decides_the_verb():
    from roughcut import firstpass

    segs = [{"clip": "CLIP_A.MP4", "in": 0, "out": 1, "why": "hi"},
            {"clip": "KX_10.MP4", "in": 0, "out": 1, "why": "the jump"},
            {"clip": "KX_10.MP4", "in": 2, "out": 3, "why": ""}]
    said = [{"clip": "KX_10.MP4", "used": True, "note": "the cliff jump"},
            # the model says it used KX_05, the plan does not: the plan wins
            {"clip": "KX_05.MP4", "used": True, "note": "the opening"}]
    rows = firstpass.new_clip_rows(segs, ["KX_10.MP4", "KX_05.MP4", "KX_03.MP4", "KX_07.MP4"],
                                   said, {"KX_03.MP4": "it is not heard yet, so this run "
                                                       "could not use it"})
    by = {r["clip"]: r for r in rows}
    assert by["KX_10.MP4"]["shots"] == [2, 3]
    assert by["KX_10.MP4"]["line"] == "Used new clip KX_10 in shots 2, 3: the cliff jump."
    assert by["KX_05.MP4"]["source"] == "none"
    assert by["KX_05.MP4"]["line"] == "Left out new clip KX_05 (no reason given)."
    assert by["KX_03.MP4"]["line"] == ("Left out new clip KX_03: it is not heard yet, so this "
                                       "run could not use it.")
    assert by["KX_07.MP4"]["line"] == "Left out new clip KX_07 (no reason given)."
    assert [r["clip"] for r in rows] == ["KX_10.MP4", "KX_05.MP4", "KX_03.MP4", "KX_07.MP4"]
