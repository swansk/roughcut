"""INTAKE M10 I10.5 — the Ask reaches colour.

A note ("warmer", "less blue", "make it pop", "match the lift shot to the summit")
answers with a patch on the EDL's colour block, in the closed vocabulary M10 decided,
validated against the real looks library. Under test: `colour.validate_patch` /
`merge_colour` / `describe_patch`, `revise`'s colour section, `validate_plan`'s colour
clause and `carry_ids`, and the server's ask job end to end on a scripted backend —
never a live call.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import colour as colourmod  # noqa: E402
from roughcut import revise  # noqa: E402

LOOKS = colourmod.load_looks(None)          # crisp, alpine, filmic
CLIPS = {"CLIP_A.MP4": {"clip": "CLIP_A.MP4", "duration": 8.0, "transcript": []},
         "CLIP_B.MP4": {"clip": "CLIP_B.MP4", "duration": 8.0, "transcript": []}}
CUT = [{"id": "gaaa", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
       {"id": "gbbb", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}]
CTX = {"film": {"mode": "auto", "look": "alpine", "strength": 0.5},
       "looks": LOOKS,
       "shots": [{"id": "gaaa", "clip": "CLIP_A.MP4",
                  "balance": {"gain": [0.98, 1.0, 1.03], "exposure": 1.24, "knee": 0.8,
                              "lift": 0.01, "source": "surface"},
                  "look": "alpine", "strength": 0.5,
                  "witness": {"white_source": "surface", "n": 3, "clip": 0.004,
                              "chroma": 3.1, "white": {"L": 71.0, "a": 0.3, "b": -2.9}}},
                 {"id": "gbbb", "clip": "CLIP_B.MP4", "balance": None,
                  "look": "alpine", "strength": 0.5,
                  "witness": {"white_source": None, "n": 2, "clip": 0.0, "chroma": 12.0}}]}


def _plan(colour, segments=CUT):
    return {"segments": [{k: s[k] for k in ("clip", "in", "out", "why")} for s in segments]
            if segments is not None else None,
            "notes": "graded", "colour": colour}


# ------------------------------------------------------------------ validation

def test_an_invented_look_name_fails_validation():
    """The item's own test: a look that is not in the library is a failed plan."""
    with pytest.raises(ValueError, match="unknown look 'teal-orange-9000'"):
        revise.validate_plan(_plan({"look": "teal-orange-9000"}), CLIPS,
                             colour=CTX, current=CUT)
    # on a shot too
    with pytest.raises(ValueError, match="unknown look"):
        revise.validate_plan(_plan({"shots": {"gaaa": {"look": "bleach"}}}), CLIPS,
                             colour=CTX, current=CUT)
    # a real one passes
    plan = revise.validate_plan(_plan({"look": "filmic", "strength": 0.6}), CLIPS,
                                colour=CTX, current=CUT)
    assert plan["colour"] == {"look": "filmic", "strength": 0.6}


@pytest.mark.parametrize("patch, words", [
    ({"strength": 1.4}, "strength"),
    ({"mode": "vivid"}, "mode"),
    ({"shots": {"gaaa": {"balance": {"gain": [1.4, 1.0, 0.9], "exposure": 1.2}}}}, "gain"),
    ({"shots": {"gaaa": {"balance": {"gain": [1, 1, 1], "exposure": 3.0}}}}, "exposure"),
    ({"shots": {"gaaa": {"match": "summit"}}}, "match"),
    ({"shots": {"gzzz": {"auto": False}}}, "not a shot id"),
    ({"reference": "gzzz"}, "reference"),
    ({"saturation": 2}, "unknown colour key"),
    ({"shots": {"gaaa": {"hue": 3}}}, "unknown shot colour key"),
])
def test_anything_outside_the_vocabulary_fails(patch, words):
    """Out of range is a failure, not a clamp: a hand overshoot means "more", a model's
    1.4 gain means it misread the vocabulary, and the re-ask is what fixes that."""
    with pytest.raises(ValueError, match=words):
        revise.validate_plan(_plan(patch), CLIPS, colour=CTX, current=CUT)


def test_without_the_colour_context_a_colour_key_is_dropped():
    plan = revise.validate_plan(_plan({"look": "filmic"}), CLIPS, current=CUT)
    assert "colour" not in plan


def test_a_colour_only_plan_keeps_the_cut_verbatim():
    plan = revise.validate_plan(_plan({"look": "crisp"}, segments=None), CLIPS,
                                colour=CTX, current=CUT)
    assert plan["unchanged"] is True
    assert plan["segments"] == CUT and plan["colour"] == {"look": "crisp"}
    # no colour and no segments is still a broken plan
    with pytest.raises(ValueError):
        revise.validate_plan({"segments": None, "notes": ""}, CLIPS, colour=CTX,
                             current=CUT)


def test_the_shot_ask_cannot_touch_another_shot_or_the_film():
    """Rejected, not dropped: a note about one shot that answers with the film's look
    has misread its scope, and silently ignoring the half it got wrong would apply the
    other half as if nothing happened."""
    own = {"shots": {"gaaa": {"balance": {"gain": [1.02, 1.0, 1.0], "exposure": 1.3}}}}
    plan = revise.validate_plan(_plan(own, CUT[:1]), CLIPS, colour=CTX,
                                current=CUT[:1], focus_id="gaaa")
    assert plan["colour"]["shots"]["gaaa"]["balance"]["exposure"] == 1.3
    with pytest.raises(ValueError, match="not the shot this note is about"):
        revise.validate_plan(_plan({"shots": {"gbbb": {"auto": False}}}, CUT[:1]),
                             CLIPS, colour=CTX, current=CUT[:1], focus_id="gaaa")
    with pytest.raises(ValueError, match="film's"):
        revise.validate_plan(_plan({"look": "crisp"}, CUT[:1]), CLIPS, colour=CTX,
                             current=CUT[:1], focus_id="gaaa")


def test_a_change_for_a_shot_the_plan_drops_is_left_out_and_said():
    patch = {"look": "crisp", "shots": {"gbbb": {"auto": False}}}
    plan = revise.validate_plan(_plan(patch, CUT[:1]), CLIPS, colour=CTX, current=CUT)
    assert plan["colour"] == {"look": "crisp"}
    assert "left out the change for gbbb" in plan["notes"]


def test_plan_shots_carry_the_ids_of_the_shots_they_continue():
    new = [{"clip": "CLIP_B.MP4", "in": 0.2, "out": 2.0},       # B, retrimmed
           {"clip": "CLIP_A.MP4", "in": 1.5, "out": 3.5},       # A, slipped half a second
           {"clip": "CLIP_A.MP4", "in": 5.0, "out": 6.0}]       # new footage
    out = revise.carry_ids(new, CUT)
    assert [s.get("id") for s in out] == ["gbbb", "gaaa", None]


# ------------------------------------------------------------------ merge + words

def test_merge_keeps_the_editors_overrides_and_clears_on_null():
    base = {"mode": "auto", "look": "alpine", "strength": 0.5,
            "shots": {"gaaa": {"match": "previous", "strength": 0.3},
                      "gbbb": {"auto": False}}}
    patch = {"look": "filmic", "shots": {"gaaa": {"match": None, "look": "crisp"}}}
    out = colourmod.merge_colour(base, patch)
    assert out["look"] == "filmic" and out["strength"] == 0.5
    assert out["shots"]["gaaa"] == {"strength": 0.3, "look": "crisp"}
    assert out["shots"]["gbbb"] == {"auto": False}
    assert colourmod.validate_colour(out, LOOKS)        # what Accept writes is valid


def test_the_proposal_reads_as_words():
    patch = {"look": "filmic", "strength": 0.6,
             "shots": {"gaaa": {"balance": {"gain": [1.02, 1.0, 0.99], "exposure": 1.3,
                                            "knee": 0.8, "lift": 0.01}}}}
    lines = colourmod.describe_patch(
        patch, {"gaaa": "shot 1 · CLIP_A"},
        {"gaaa": CTX["shots"][0]["balance"]})
    assert lines[0] == "look: filmic at 0.6"
    assert lines[1].startswith("shot 1 · CLIP_A: warmer, brighter")


# ------------------------------------------------------------------ the prompt

def test_the_prompt_names_the_looks_and_the_numbers():
    p = revise.build_prompt(CUT, CLIPS, "a ski film", "less blue on the first shot",
                            (5, 20), colour=CTX)
    assert "## Colour" in p
    for name in ("crisp", "alpine", "filmic"):
        assert f"* {name} — " in p
    assert "Leave\n`colour` out of your answer unless" in p
    assert "gaaa" in p and "white L 71 a +0.3 b -2.9" in p and "clipped 0.4%" in p
    assert "you may leave out `segments`" in p
    # no context, no section — the Ask stays what it was
    plain = revise.build_prompt(CUT, CLIPS, "a ski film", "tighten it", (5, 20))
    assert "## Colour" not in plain and "colour" not in plain.lower()


def test_the_shot_prompt_lists_only_its_shot():
    p = revise.build_shot_prompt(CUT, 0, CLIPS, "", "warmer", (5, 20), colour=CTX)
    assert "may only set `shots.gaaa`" in p
    assert "gaaa  CLIP_A" in p and "gbbb  CLIP_B" not in p
    # a shot without an id has nothing to key a grade by: no clause
    bare = [{k: v for k, v in s.items() if k != "id"} for s in CUT]
    assert "## Colour" not in revise.build_shot_prompt(bare, 0, CLIPS, "", "warmer",
                                                       (5, 20), colour=CTX)


# ------------------------------------------------------------------ the server

def _is_plan(request) -> bool:
    return request.system in (revise.SYSTEM, revise.SHOT_SYSTEM)


def _scripted(*answers):
    """A backend that answers the plan calls with `answers` in turn (the last repeats)
    and the estimate call with nothing usable — the Ask falls back to its measured
    guess, which is the estimate's own contract."""
    from roughcut import config, inference

    class Scripted:
        name = "scripted"
        seen: list = []

        def complete(self, request):
            Scripted.seen.append(request)
            if _is_plan(request):
                k = sum(1 for r in Scripted.seen if _is_plan(r))
                text = json.dumps(answers[min(k, len(answers)) - 1])
            else:
                text = "{}"
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend="scripted", model=config.model_for(request.role),
                                    projected_usd=1e-4, latency_ms=1, raw=text)

    inference.set_backend(Scripted())
    inference.reset_spend()
    return Scripted


def _run(client, body):
    job = client.post("/api/ask", json=body).json()["job"]
    deadline = time.time() + 20
    while time.time() < deadline:
        s = client.get(f"/api/ask/{job}").json()
        if s["state"] not in ("estimating", "running"):
            return s
        time.sleep(0.05)
    raise AssertionError("ask never finished")


def test_a_colour_plan_round_trips_into_a_proposal_and_accept_applies_it(client, project):
    from roughcut import inference
    p = client.get("/api/project").json()
    ids = [s["id"] for s in p["segments"]]
    before = project["edl"].read_text(encoding="utf-8")
    seen = _scripted({"notes": "warmed the opening and gave the film a look",
                      "colour": {"look": "filmic", "strength": 0.6,
                                 "shots": {ids[0]: {"match": "previous"}}}})
    try:
        s = _run(client, {"note": "make it warmer and more filmic",
                          "segments": p["segments"], "story": ""})
    finally:
        inference.set_backend(None)
    assert s["state"] == "done", s
    plan = s["plan"]
    # the cut is the cut, ids and all; the colour is the patch, in words too
    assert plan["unchanged"] is True
    assert [x["id"] for x in plan["segments"]] == ids
    assert plan["colour"] == {"look": "filmic", "strength": 0.6,
                              "shots": {ids[0]: {"match": "previous"}}}
    assert plan["colour_lines"][0] == "look: filmic at 0.6"
    assert "shot 1 · CLIP_A: match ← previous" in plan["colour_lines"]
    # the prompt carried the vocabulary and the library
    prompt = [r for r in seen.seen if _is_plan(r)][-1].prompt
    assert "## Colour" in prompt and "* filmic — " in prompt and ids[0] in prompt
    # a proposal, never a write — Discard is the board dropping it, which leaves this
    assert project["edl"].read_text(encoding="utf-8") == before
    assert not client.get("/api/project").json().get("colour")

    # Accept: the board merges the patch over its block and saves segments + colour in
    # one PUT (app.js acceptProposal) — the same merge, the same body
    merged = colourmod.merge_colour(client.get("/api/project").json().get("colour"),
                                    plan["colour"])
    r = client.put("/api/project", json={"segments": plan["segments"], "story": "",
                                         "colour": merged})
    assert r.status_code == 200, r.text
    after = client.get("/api/project").json()
    assert after["colour"]["look"] == "filmic" and after["colour"]["strength"] == 0.6
    assert after["colour"]["shots"] == {ids[0]: {"match": "previous"}}
    assert [x["id"] for x in after["segments"]] == ids
    film = client.get("/api/colour").json()
    assert film["shots"][0]["look"] == "filmic"


def test_an_invented_look_gets_the_bounded_reask_then_fails(client):
    from roughcut import inference
    p = client.get("/api/project").json()
    bad = {"notes": "x", "colour": {"look": "teal-orange-9000"}}
    seen = _scripted(bad, bad)
    try:
        s = _run(client, {"note": "teal and orange please", "segments": p["segments"]})
    finally:
        inference.set_backend(None)
    assert s["state"] == "failed" and "unknown look" in s["detail"]
    assert sum(1 for r in seen.seen if _is_plan(r)) == 2, "one re-ask, then the failure"


def test_the_shot_ask_carries_a_colour_clause_for_its_shot_only(client):
    from roughcut import inference
    p = client.get("/api/project").json()
    ids = [s["id"] for s in p["segments"]]
    warm = {"gain": [1.03, 1.0, 0.97], "exposure": 1.1, "knee": 0.8, "lift": 0.0}
    _scripted({"notes": "warmer", "colour": {"shots": {ids[0]: {"balance": warm}}}})
    try:
        s = _run(client, {"note": "warmer", "segments": p["segments"], "focus": 0})
    finally:
        inference.set_backend(None)
    assert s["state"] == "done", s
    plan = s["plan"]
    assert plan["focus"]["index"] == 0
    assert [x["id"] for x in plan["segments"]] == ids          # nothing re-cut
    assert list(plan["colour"]["shots"]) == [ids[0]]

    # another shot's id: rejected, re-asked once, failed
    _scripted({"notes": "x", "colour": {"shots": {ids[1]: {"auto": False}}}})
    try:
        s = _run(client, {"note": "warmer", "segments": p["segments"], "focus": 0})
    finally:
        inference.set_backend(None)
    assert s["state"] == "failed" and "not the shot this note is about" in s["detail"]
