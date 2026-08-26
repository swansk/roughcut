"""Find a moment — the free word-level layer, the model layer, and the endpoint.

The lexical layer is pure functions over the same clip dicts the Ask reads, so it is
tested directly; the model layer is tested through the endpoint with a scripted
backend, the way every other model-backed path in this suite is.
"""

from __future__ import annotations

import json
import time

import pytest

from roughcut import find


def _clips(visual: dict | None = None) -> dict:
    """The model-facing clips shape, minimal: one clip, the conftest transcript."""
    return {
        "CLIP_A.MP4": {
            "clip": "CLIP_A.MP4", "duration": 6.0,
            "transcript": [
                {"start": 0.5, "end": 2.0, "text": "hello there"},
                {"start": 2.4, "end": 4.0, "text": "how are you"},
                {"start": 5.0, "end": 5.6, "text": "goodbye"},
            ],
            "summary": {}, "visual": visual or {},
        },
    }


# ------------------------------------------------------------------ lexical

def test_lexical_matches_a_spoken_word():
    rows = find.lexical("goodbye", _clips())
    assert len(rows) == 1
    row = rows[0]
    assert row["clip"] == "CLIP_A.MP4"
    assert row["start"] <= 5.0 and row["end"] >= 5.6
    assert row["source"] == "heard" and "goodbye" in row["what"]


def test_lexical_stems_enough_for_plurals_and_gerunds():
    """rock/rocks and fall/falling must match; a bare stopword must match nothing."""
    clips = _clips({"moments": [
        {"start": 1.0, "end": 3.0, "kind": "fall", "notable": True,
         "what": "rider goes down hard in deep snow"}]})
    assert find.lexical("falling rider", clips), "prefix stems should match"
    assert not find.lexical("so", _clips()), "a stopword-only query matches nothing"
    assert not find.lexical("purple monkey dishwasher", _clips())


def test_lexical_prefers_a_notable_seen_moment():
    clips = _clips({"moments": [
        {"start": 1.0, "end": 3.0, "kind": "crash", "notable": True,
         "what": "hello shouted mid-crash"}]})
    rows = find.lexical("hello", clips)
    # The moment overlaps the "hello there" utterance (0.5-2.0 padded), so the two
    # merge into one window that knows it was both said and seen.
    assert len(rows) == 1
    assert "seen" in rows[0]["source"] and "heard" in rows[0]["source"]
    assert rows[0]["end"] >= 3.0


def test_lexical_merges_adjacent_hits_into_one_window():
    """"hello" (0.5-2.0) and "goodbye" (5.0-5.6) sit 2s apart once padded — under the
    merge gap, so they are one moment to look at, not two rows."""
    rows = find.lexical("hello goodbye", _clips())
    assert len(rows) == 1
    assert rows[0]["start"] == pytest.approx(0.0, abs=0.01)
    assert rows[0]["end"] == pytest.approx(6.0, abs=0.01)   # clamped to the clip
    # covering both query words is worth more than either hit alone
    assert rows[0]["score"] > 0.5


# ------------------------------------------------------------------ validation

def test_validate_matches_accepts_an_empty_list():
    got = find.validate_matches({"matches": [], "notes": "nothing"}, _clips())
    assert got["matches"] == [] and got["notes"] == "nothing"


def test_validate_matches_rejects_unknown_clip_and_bad_range():
    with pytest.raises(ValueError, match="unknown clip"):
        find.validate_matches({"matches": [
            {"clip": "NOPE.MP4", "start": 0, "end": 1}]}, _clips())
    with pytest.raises(ValueError, match="outside"):
        find.validate_matches({"matches": [
            {"clip": "CLIP_A.MP4", "start": 2.0, "end": 99.0}]}, _clips())
    with pytest.raises(ValueError, match="non-numeric"):
        find.validate_matches({"matches": [
            {"clip": "CLIP_A.MP4", "start": "x", "end": 1}]}, _clips())


def test_validate_matches_caps_the_list():
    many = {"matches": [{"clip": "CLIP_A.MP4", "start": 0.0, "end": 1.0}] * 30}
    assert len(find.validate_matches(many, _clips())["matches"]) == find.MAX_MATCHES


# ------------------------------------------------------------------ endpoint

def test_find_returns_playable_matches_without_spending_anything(client):
    r = client.post("/api/find", json={"query": "goodbye"})
    assert r.status_code == 200
    data = r.json()
    assert data["job"] is None
    assert data["deep_projected_usd"] > 0
    # every clip shares the conftest transcript, so all three match
    assert {m["clip"] for m in data["matches"]} == {"CLIP_A.MP4", "CLIP_B.MP4",
                                                    "CLIP_C.MP4"}
    row = data["matches"][0]
    assert row["proxy"].startswith("/media/proxy/") and row["proxy"].endswith(".mp4")
    assert "?t=" in row["poster"]
    assert row["duration"] == pytest.approx(6.0, abs=0.1)


def test_find_rejects_an_empty_query(client):
    assert client.post("/api/find", json={"query": "   "}).status_code == 400


def _find_job_status(client, job, timeout=20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/job/{job}").json()
        if s["state"] not in ("running",):
            return s
        time.sleep(0.05)
    raise AssertionError(f"find never finished: {s}")


def test_deep_find_runs_one_model_call_over_the_inventory(client):
    from roughcut import config, inference

    class Scripted:
        name = "scripted"
        seen: list = []

        def complete(self, request):
            Scripted.seen.append(request)
            text = json.dumps({
                "matches": [{"clip": "CLIP_B.MP4", "start": 1.0, "end": 2.5,
                             "what": "the greeting", "why": "'hello there' is said"}],
                "notes": "searched the words"})
            model = config.model_for(request.role)
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend="scripted", model=model,
                                    projected_usd=1e-4, latency_ms=1, raw=text)

    inference.set_backend(Scripted())
    inference.reset_spend()
    try:
        r = client.post("/api/find", json={"query": "the bit where they say hi",
                                           "deep": True})
        assert r.status_code == 200
        job = r.json()["job"]
        assert job
        s = _find_job_status(client, job)
        assert s["state"] == "done", s
        found = s["found"]
        assert found["matches"][0]["clip"] == "CLIP_B.MP4"
        assert found["matches"][0]["source"] == "model"
        assert found["matches"][0]["proxy"] == "/media/proxy/CLIP_B.mp4"
        assert found["notes"] == "searched the words"
        assert found["usage"]["projected_usd"] > 0
        # one call — no estimate ahead of it — and it carried the evidence
        assert len(Scripted.seen) == 1
        prompt = Scripted.seen[-1].prompt
        assert "the bit where they say hi" in prompt and "hello there" in prompt
        assert Scripted.seen[-1].role == config.ROLE_JUDGE
    finally:
        inference.set_backend(None)


def test_deep_find_fails_loudly_on_an_invented_clip(client):
    from roughcut import config, inference

    class Inventing:
        name = "scripted"

        def complete(self, request):
            text = json.dumps({"matches": [
                {"clip": "IMAGINARY.MP4", "start": 0.0, "end": 2.0,
                 "what": "x", "why": "y"}]})
            model = config.model_for(request.role)
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend="scripted", model=model,
                                    projected_usd=1e-4, latency_ms=1, raw=text)

    inference.set_backend(Inventing())
    inference.reset_spend()
    try:
        job = client.post("/api/find", json={"query": "anything",
                                             "deep": True}).json()["job"]
        s = _find_job_status(client, job)
        assert s["state"] == "failed" and s["code"] == 502
        assert "unknown clip" in s["detail"]
    finally:
        inference.set_backend(None)


def test_one_model_search_at_a_time(client):
    from roughcut import config, inference

    class Slow:
        name = "slow"

        def complete(self, request):
            time.sleep(1.0)
            text = json.dumps({"matches": [], "notes": "nothing"})
            model = config.model_for(request.role)
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend="slow", model=model,
                                    projected_usd=1e-4, latency_ms=1000, raw=text)

    inference.set_backend(Slow())
    inference.reset_spend()
    try:
        first = client.post("/api/find", json={"query": "x", "deep": True})
        assert first.status_code == 200
        second = client.post("/api/find", json={"query": "y", "deep": True})
        assert second.status_code == 409
        # the free layer stays available while the model call runs
        assert client.post("/api/find", json={"query": "goodbye"}).status_code == 200
        assert _find_job_status(client, first.json()["job"])["state"] == "done"
    finally:
        inference.set_backend(None)
