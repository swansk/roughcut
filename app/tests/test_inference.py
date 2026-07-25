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
