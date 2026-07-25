"""Inference layer and the revise loop, against a scripted backend.

No live calls: CLAUDE.md puts model calls behind mocked backends by default, and the
things worth testing here are the contract and the guardrails, not whether a model
can edit video. What must hold:

  * a plan that names a clip we do not have, or runs past the end of one, fails loudly
  * bad JSON gets one bounded re-ask, not an infinite loop and not a crash
  * every call is accounted for, on the subscription backend too
  * the budget cap is enforced *before* spending
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import config, inference, revise  # noqa: E402


class ScriptedBackend:
    """Returns canned replies in order and records what it was asked."""

    name = "scripted"

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.requests: list[inference.Request] = []

    def complete(self, request: inference.Request) -> inference.Result:
        self.requests.append(request)
        text = self.replies.pop(0) if self.replies else "{}"
        model = config.model_for(request.role)
        return inference.Result(
            content=text, input_tokens=1000, output_tokens=200,
            backend=self.name, model=model,
            projected_usd=config.projected_usd(model, 1000, 200),
            latency_ms=5, raw=text)


CLIPS = {
    "A.MP4": {"clip": "A.MP4", "duration": 10.0, "summary": {},
              "transcript": [{"start": 1.0, "end": 2.0, "text": "hello"}]},
    "B.MP4": {"clip": "B.MP4", "duration": 8.0, "summary": {},
              "transcript": []},
}
SEGMENTS = [{"clip": "A.MP4", "in": 1.0, "out": 3.0, "why": "x"}]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUGHCUT_LEDGER", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setenv("ROUGHCUT_BUDGET_USD", "15.0")
    inference.reset_spend()
    yield
    inference.set_backend(None)


def use(replies: list[str]) -> ScriptedBackend:
    b = ScriptedBackend(replies)
    inference.set_backend(b)
    return b


# ------------------------------------------------------------------ config

def test_roles_resolve_and_are_overridable(monkeypatch):
    assert config.model_for(config.ROLE_SKELETON)
    monkeypatch.setenv("ROUGHCUT_MODEL_SKELETON", "some-future-model")
    assert config.model_for(config.ROLE_SKELETON) == "some-future-model"


def test_unknown_role_is_an_error():
    with pytest.raises(ValueError):
        config.model_for("nonesuch")


def test_unknown_model_family_is_never_priced_free():
    assert config.projected_usd("some-future-model", 1_000_000, 0) > 0


# --------------------------------------------------------------- accounting

def test_every_call_is_logged_with_a_projection(tmp_path):
    use(['{"segments": [{"clip": "A.MP4", "in": 0, "out": 2}], "notes": "n"}'])
    revise.propose(SEGMENTS, CLIPS, "story", "shorter")
    lines = config.ledger_path().read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["projected_usd"] > 0          # true on the subscription backend too
    assert entry["input_tokens"] == 1000
    assert entry["role"] == config.ROLE_SKELETON
    assert inference.spent_usd() == pytest.approx(entry["projected_usd"])


def test_budget_cap_is_enforced_before_spending(monkeypatch):
    monkeypatch.setenv("ROUGHCUT_BUDGET_USD", "0.0001")
    b = use(['{"segments": [{"clip": "A.MP4", "in": 0, "out": 2}]}'])
    with pytest.raises(inference.BudgetExceeded):
        revise.propose(SEGMENTS, CLIPS, "", "shorter")
    assert b.requests == [], "must refuse before calling the model, not after"


# ------------------------------------------------------------ schema/retry

def test_json_is_extracted_from_a_fenced_reply():
    use(['Sure!\n```json\n{"segments":[{"clip":"A.MP4","in":0,"out":2}]}\n```\nHope that helps'])
    plan = revise.propose(SEGMENTS, CLIPS, "", "shorter")
    assert plan["segments"][0]["clip"] == "A.MP4"


def test_bad_json_gets_one_bounded_reask_then_succeeds():
    b = use(["not json at all",
             '{"segments":[{"clip":"A.MP4","in":0,"out":2}],"notes":"ok"}'])
    plan = revise.propose(SEGMENTS, CLIPS, "", "shorter")
    assert plan["notes"] == "ok"
    assert len(b.requests) == 2
    assert "could not be parsed" in b.requests[1].prompt


def test_retry_is_bounded_not_infinite():
    b = use(["nope", "still nope", "and again"])
    with pytest.raises(inference.InferenceError):
        revise.propose(SEGMENTS, CLIPS, "", "shorter")
    assert len(b.requests) == 2, "one re-ask, then give up"


# -------------------------------------------------------------- validation

@pytest.mark.parametrize("payload,reason", [
    ('{"segments":[{"clip":"GHOST.MP4","in":0,"out":2}]}', "unknown clip"),
    ('{"segments":[{"clip":"A.MP4","in":0,"out":99}]}', "past clip end"),
    ('{"segments":[{"clip":"A.MP4","in":5,"out":2}]}', "inverted"),
    ('{"segments":[]}', "empty"),
    ('{"notes":"I did nothing"}', "no segments key"),
])
def test_invalid_plans_are_rejected(payload, reason):
    use([payload, payload])           # same bad reply twice: exhaust the retry
    with pytest.raises(inference.InferenceError):
        revise.propose(SEGMENTS, CLIPS, "", "shorter")


def test_valid_plan_is_normalised():
    use(['{"segments":[{"clip":"B.MP4","in":"1.234","out":"5.678","why":"  trimmed  "}],'
         ' "notes":"tightened"}'])
    plan = revise.propose(SEGMENTS, CLIPS, "", "tighten")
    seg = plan["segments"][0]
    assert (seg["in"], seg["out"]) == (1.23, 5.68)
    assert seg["why"] == "trimmed"
    assert plan["usage"]["projected_usd"] > 0


# --------------------------------------------------- claude_cli backend shape

# Captured verbatim from a real `claude -p ... --output-format json` invocation, so
# these tests pin the parsing against the actual contract rather than my memory of it.
LIVE_SHAPE = {
    "is_error": False, "num_turns": 1, "session_id": "abc", "total_cost_usd": 0,
    "usage": {"input_tokens": 12, "cache_creation_input_tokens": 8000,
              "cache_read_input_tokens": 24000, "output_tokens": 900,
              "service_tier": "standard"},
    "result": '{"segments":[{"clip":"A.MP4","in":0,"out":2}]}',
    "type": "result", "duration_ms": 1726,
}
LIVE_ERROR = {**LIVE_SHAPE, "is_error": True,
              "result": "Not logged in · Please run /login",
              "usage": {"input_tokens": 0, "output_tokens": 0}}


def _cli_with(monkeypatch, payload=None, exc=None):
    import subprocess as sp

    def fake_run(cmd, **kwargs):
        if exc is not None:
            raise exc
        return sp.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(inference.subprocess, "run", fake_run)
    return inference.ClaudeCliBackend()


def test_cli_counts_cached_input_tokens(monkeypatch):
    """Reading only `input_tokens` would report 12 tokens for a call that processed
    32,012 — and a projection of essentially zero."""
    backend = _cli_with(monkeypatch, LIVE_SHAPE)
    result = backend.complete(inference.Request(prompt="hi",
                                                role=config.ROLE_SKELETON))
    assert result.input_tokens == 12 + 8000 + 24000
    assert result.output_tokens == 900
    assert result.projected_usd > 0.05, "a 32k-token opus call is not nearly free"


def test_cli_surfaces_not_logged_in(monkeypatch):
    backend = _cli_with(monkeypatch, LIVE_ERROR)
    with pytest.raises(inference.InferenceError, match="Not logged in"):
        backend.complete(inference.Request(prompt="hi"))


def test_cli_missing_binary_explains_the_path_problem(monkeypatch):
    backend = _cli_with(monkeypatch, exc=FileNotFoundError("claude"))
    with pytest.raises(inference.InferenceError, match=r"not found on PATH"):
        backend.complete(inference.Request(prompt="hi"))


def test_cli_timeout_is_reported_cleanly(monkeypatch):
    import subprocess as sp
    backend = _cli_with(monkeypatch, exc=sp.TimeoutExpired("claude", 300))
    with pytest.raises(inference.InferenceError, match="timed out"):
        backend.complete(inference.Request(prompt="hi"))


def test_cli_passes_images_by_path_never_base64(monkeypatch, tmp_path):
    """SPEC §6.1 rule 1. Base64 is an API-backend implementation detail."""
    captured = {}

    def fake_run(cmd, **kwargs):
        import subprocess as sp
        captured["cmd"] = cmd
        return sp.CompletedProcess(cmd, 0, stdout=json.dumps(LIVE_SHAPE), stderr="")

    monkeypatch.setattr(inference.subprocess, "run", fake_run)
    img = tmp_path / "sheet.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    inference.ClaudeCliBackend().complete(
        inference.Request(prompt="look", images=(img,)))
    joined = " ".join(captured["cmd"])
    assert str(img.resolve()) in joined
    assert "base64" not in joined.lower()


# ------------------------------------------------------------------ prompt

def test_prompt_carries_the_transcript_and_the_note():
    """R8/R9: the words are the strongest signal in this footage. A revision prompt
    without them is asking the model to edit blind."""
    b = use(['{"segments":[{"clip":"A.MP4","in":0,"out":2}]}'])
    revise.propose(SEGMENTS, CLIPS, "milk is the joke", "more skiing")
    prompt = b.requests[0].prompt
    assert "hello" in prompt                  # the transcript line
    assert "more skiing" in prompt            # the note
    assert "milk is the joke" in prompt       # the story
    assert "A.MP4" in prompt and "B.MP4" in prompt
    assert b.requests[0].role == config.ROLE_SKELETON


def test_empty_note_is_rejected_without_calling_the_model():
    b = use(['{"segments":[]}'])
    with pytest.raises(ValueError):
        revise.propose(SEGMENTS, CLIPS, "", "   ")
    assert b.requests == []


# ------------------------------------------------------------------ chronology

# 20:43, 20:53, 21:40, 22:17 on one day, then one clip a day and a half later —
# the shape of B1's travel section, which is where the ordering fell over.
TIMED = {
    "A.MP4": {"clip": "A.MP4", "duration": 10.0, "summary": {}, "captured": 1770237800,
              "transcript": [{"start": 1.0, "end": 2.0, "text": "hello"}]},
    "B.MP4": {"clip": "B.MP4", "duration": 8.0, "summary": {}, "captured": 1770238400,
              "transcript": []},
    "C.MP4": {"clip": "C.MP4", "duration": 8.0, "summary": {}, "captured": 1770241200,
              "transcript": []},
    "D.MP4": {"clip": "D.MP4", "duration": 8.0, "summary": {}, "captured": 1770369000,
              "transcript": []},
}


def test_shot_timeline_orders_clips_and_splits_sessions():
    """Karl on the first originated cut: the airport was cut out of order in a way
    that made no sense. It was — and the model had never been told when anything was
    shot, so it could not have known."""
    tl = revise.shot_timeline(TIMED)
    assert tl["A.MP4"].startswith("recorded #1 of 4, session 1, first thing")
    assert "recorded #2 of 4, session 1, 10 min after the previous" == tl["B.MP4"]
    assert "recorded #3 of 4, session 1" in tl["C.MP4"]
    assert "session 2" in tl["D.MP4"] and "different session" in tl["D.MP4"]


def test_shot_timeline_is_relative_never_wall_clock():
    """GoPro writes UTC and the trip was not in UTC; a time of day seven hours out
    would be worse than none. Nothing absolute may appear."""
    joined = " ".join(revise.shot_timeline(TIMED).values())
    assert "20:4" not in joined and "2026" not in joined
    assert ":" not in joined.replace("session", "")


def test_clips_without_capture_times_still_work():
    assert revise.shot_timeline(CLIPS) == {}
    b = use(['{"segments":[{"clip":"A.MP4","in":0,"out":2}]}'])
    revise.originate(CLIPS, story="x", note="y")       # must not raise
    assert "recorded #" not in b.requests[0].prompt


def test_both_prompts_carry_the_shot_order():
    """The revision path had the same blind spot — session 2 flagged that its
    reorder 'may invent a chronology the footage contradicts'."""
    b = use(['{"segments":[{"clip":"A.MP4","in":0,"out":2}]}',
             '{"segments":[{"clip":"A.MP4","in":0,"out":2}]}'])
    revise.originate(TIMED, story="x", note="")
    revise.propose(SEGMENTS, TIMED, "x", "tighten it")
    for req in b.requests:
        assert "recorded #1 of 4" in req.prompt
        assert "backwards" in req.prompt, "and what to do with it"


def test_shot_order_is_information_not_a_rule():
    """Karl, after the fix landed: "note that we don't ALWAYS need to go
    chronological." The first version of this guidance read as a constraint, and the
    cut that came back was almost strictly in order — a strictly chronological cut is
    usually the dullest one available."""
    b = use(['{"segments":[{"clip":"A.MP4","in":0,"out":2}]}',
             '{"segments":[{"clip":"A.MP4","in":0,"out":2}]}'])
    revise.originate(TIMED, story="x", note="")
    revise.propose(SEGMENTS, TIMED, "x", "tighten it")
    for req in b.requests:
        assert "information rather than as a rule" in req.prompt \
            or "information, not as a rule" in req.prompt
    first = b.requests[0].prompt
    assert "Order is a choice, not a record" in first
    assert "dullest one available" in first
    assert "accidental" in first, "only the accidental kind is the mistake"


# ------------------------------------------------------------------ originate

def test_originate_builds_a_cut_with_nothing_to_revise():
    """The prerequisite that made the app expert-only was a hand-authored EDL. This
    is the same call addressed to an empty timeline."""
    b = use(['{"segments":[{"clip":"B.MP4","in":0,"out":4,"why":"opens on the group"}],'
             '"notes":"built around the arrival"}'])
    plan = revise.originate(CLIPS, story="a trip film", note="keep it loose")
    assert plan["segments"] == [{"clip": "B.MP4", "in": 0.0, "out": 4.0,
                                 "why": "opens on the group"}]
    assert plan["usage"]["projected_usd"] > 0
    prompt = b.requests[0].prompt
    assert "hello" in prompt and "a trip film" in prompt and "keep it loose" in prompt
    assert b.requests[0].role == config.ROLE_SKELETON


def test_originate_states_the_traps_that_are_not_inferable_from_one_clip():
    """R8: transcript density points away from the action (16.8 candidates/min on the
    travel footage vs 7.4 on the mountain), and the connective tissue is a running
    joke. A model reading clips one at a time cannot rediscover either."""
    b = use(['{"segments":[{"clip":"A.MP4","in":0,"out":2}]}'])
    revise.originate(CLIPS, story="", note="")
    prompt = b.requests[0].prompt
    assert "Quiet does not mean boring" in prompt
    assert "through-line" in prompt
    assert "cannot see the frame" in prompt.lower()
    # no brief: the model is told to infer one and to say so, not to refuse
    assert "infer what this film is about" in prompt


def test_originate_does_not_pretend_there_is_an_edit():
    b = use(['{"segments":[{"clip":"A.MP4","in":0,"out":2}]}'])
    revise.originate(CLIPS, story="x", note="")
    prompt = b.requests[0].prompt
    assert "The current edit" not in prompt
    assert "There is no" in prompt and "first" in prompt.lower()
    assert "first rough cut" in (b.requests[0].system or "").lower()


def test_originate_needs_something_to_cut_from():
    b = use(['{"segments":[]}'])
    with pytest.raises(ValueError):
        revise.originate({}, story="x", note="y")
    assert b.requests == []


def test_originated_plans_are_validated_as_strictly_as_revisions():
    """A first cut has no prior edit to sanity-check it against, so the validator is
    the only thing between an invented timestamp and missing footage in a render."""
    b = use(['{"segments":[{"clip":"NOPE.MP4","in":0,"out":2}]}',
             '{"segments":[{"clip":"A.MP4","in":0,"out":99}]}'])
    with pytest.raises(inference.InferenceError):
        revise.originate(CLIPS, story="x", note="y")
    assert len(b.requests) == 2, "one bounded re-ask, not an unbounded loop"
