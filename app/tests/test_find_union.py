"""Find, wave 20 (FIND3): lexical + the 3080's frame search fused, and the Claude rerank.

Pure functions plus the endpoint with scripted backends - no GPU, no real model call."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from roughcut import find, localvlm


def _clips() -> dict:
    return {
        "CLIP_A.MP4": {"clip": "CLIP_A.MP4", "duration": 120.0, "visual": {}, "summary": {},
                       "transcript": [{"start": 10.0, "end": 14.0, "text": "that was a huge jump"},
                                      {"start": 70.0, "end": 72.0, "text": "nice one"}]},
        "CLIP_B.MP4": {"clip": "CLIP_B.MP4", "duration": 60.0, "visual": {}, "summary": {}, "transcript": []},
    }


def _vis(clip="CLIP_A", t=11.0, score=0.3):
    return {"clip": clip, "start": t - 1, "end": t + 1, "score": score}


def _fake_search(rows):
    def search(text, k=3, **_):
        return {"ok": True, "why": "", "results": {text: rows[:k]}}
    return search


# ------------------------------------------------------------------ fusion

def test_a_moment_both_sources_find_beats_one_only_one_does():
    got = find.search("huge jump", _clips(), local_search=_fake_search([_vis("CLIP_A", 11.0), _vis("CLIP_B", 30.0)]))
    top = got["matches"][0]
    assert top["clip"] == "CLIP_A.MP4"
    assert {e["source"] for e in top["evidence"]} == {"heard", "local"}
    assert top["source"] == "heard+local"
    assert top["label"] == "heard · seen by the 3080"
    # the local-only row is still there, after it, with its own label and a clip NAME (not a stem)
    other = next(m for m in got["matches"] if m["clip"] == "CLIP_B.MP4")
    assert other["label"] == "seen by the 3080" and other["score"] < top["score"]
    assert got["local"] == {"used": True, "why": ""}


def test_windows_merge_and_each_keeps_its_evidence():
    lex = find.lexical("huge jump", _clips())          # 9.5-14.5
    rows = find.union(lex, [dict(_vis("CLIP_A", 15.0), clip="CLIP_A.MP4", source="local", what="x")])
    assert len(rows) == 1                                # 14.0-16.0 touches 9.5-14.5 within the gap
    assert rows[0]["start"] == pytest.approx(9.5) and rows[0]["end"] == pytest.approx(16.0)
    assert len(rows[0]["evidence"]) == 2
    far = find.union(lex, [dict(_vis("CLIP_A", 60.0), clip="CLIP_A.MP4", source="local", what="x")])
    assert len(far) == 2


def test_a_local_hit_inside_a_long_window_does_not_widen_it():
    lex = [{"clip": "CLIP_A.MP4", "start": 10.0, "end": 40.0, "what": "talk", "why": "", "source": "heard", "score": 1.0}]
    rows = find.union(lex, [{"clip": "CLIP_A.MP4", "start": 20.0, "end": 22.0, "source": "local", "what": "x", "score": 0.3}])
    assert (rows[0]["start"], rows[0]["end"]) == (10.0, 40.0) and rows[0]["score"] > 1.0


def test_felt_and_seen_keep_their_words_in_the_label():
    lex = [{"clip": "CLIP_A.MP4", "start": 5.0, "end": 6.0, "what": "air", "why": "", "source": "felt+heard", "score": 1.0}]
    row = find.union(lex, [])[0]
    assert row["label"] == "felt · heard"
    assert find._label("seen") == "seen by Claude" and find._label("model") == "Claude"


# ------------------------------------------------------------------ fallbacks

def test_no_local_stack_is_exactly_the_lexical_list():
    lex = find.lexical("huge jump", _clips())
    got = find.search("huge jump", _clips(),
                      local_search=lambda t, k=3, **_: {"ok": False, "why": "No GPU", "results": {t: []}})
    assert [(m["clip"], m["start"], m["end"]) for m in got["matches"]] == [(r["clip"], r["start"], r["end"]) for r in lex]
    assert got["local"] == {"used": False, "why": "No GPU"}


def test_a_crashing_local_search_degrades_to_lexical():
    def boom(*a, **k):
        raise RuntimeError("cuda out of memory")
    got = find.search("huge jump", _clips(), local_search=boom)
    assert got["matches"] and not got["local"]["used"] and "out of memory" in got["local"]["why"]


def test_local_rows_for_a_clip_the_bin_does_not_list_are_dropped():
    rows, _ = find.local_rows("x", _clips(), search=_fake_search([_vis("CLIP_Z", 3.0), _vis("CLIP_B", 3.0)]))
    assert [r["clip"] for r in rows] == ["CLIP_B.MP4"]


def test_local_off_means_lexical_only_without_calling_the_search():
    def never(*a, **k):
        raise AssertionError("must not be called")
    got = find.search("huge jump", _clips(), local=False, local_search=never)
    assert got["matches"][0]["source"] == "heard" and got["local"]["used"] is False


def test_rank_windows_is_the_subprocess_search():
    t = np.arange(0, 20, 0.5)
    e = np.zeros((len(t), 4), dtype=np.float16)
    e[:, 0] = 1.0
    e[24] = [0, 1, 0, 0]                                     # t = 12.0 matches the query
    top = localvlm.rank_windows([0, 1, 0, 0], [("CLIP_A", t, e)], k=2)
    assert top[0]["t"] == pytest.approx(12.0, abs=1.5) and top[0]["end"] - top[0]["start"] == 2.0
    assert abs(top[1]["t"] - top[0]["t"]) >= 3.0               # non-maximum suppression


# ------------------------------------------------------------------ rerank + mode gating

def test_the_rerank_runs_by_default_only_in_claude_and_hybrid(monkeypatch):
    assert find.RERANK_DEFAULT_ON is False                 # off while it loses to the union (eval --rerank)
    assert find.rerank_policy("hybrid") == {"allowed": True, "default": False, "mode": "hybrid"}
    monkeypatch.setattr(find, "RERANK_DEFAULT_ON", True)
    assert find.rerank_policy("claude") == {"allowed": True, "default": True, "mode": "claude"}
    assert find.rerank_policy("hybrid")["default"] is True
    assert find.rerank_policy("local") == {"allowed": False, "default": False, "mode": "local"}


class _Scripted:
    name = "scripted"

    def __init__(self, ranking):
        self.ranking, self.seen = ranking, []

    def complete(self, request):
        from roughcut import config, inference
        self.seen.append(request)
        text = json.dumps({"ranking": self.ranking, "notes": "looked"})
        return inference.Result(content=text, input_tokens=10, output_tokens=5, backend="scripted",
                                model=config.model_for(request.role), projected_usd=1e-3, latency_ms=1, raw=text)


def _rows():
    return [{"clip": "CLIP_A.MP4", "start": 1.0 + 10 * i, "end": 3.0 + 10 * i, "what": f"w{i}", "why": "",
             "source": "heard", "label": "heard", "score": 1.0 - i / 10,
             "evidence": [{"source": "heard", "label": "heard", "what": f"w{i}"}]} for i in range(4)]


def test_rerank_puts_yes_first_demotes_no_and_keeps_everything(tmp_path):
    from roughcut import inference
    be = _Scripted([{"n": 1, "fit": "no", "why": "x"}, {"n": 3, "fit": "yes", "why": "y"},
                    {"n": 2, "fit": "maybe", "why": "z"}])
    inference.set_backend(be)
    try:
        thumbs = []
        for i in range(3):
            p = tmp_path / f"t{i}.jpg"
            p.write_bytes(b"x")
            thumbs.append(p)
        got = find.rerank("the jump", _rows(), lambda r: thumbs[int(r["start"] // 10)] if r["start"] < 30 else None)
    finally:
        inference.set_backend(None)
    assert len(be.seen) == 1 and be.seen[0].isolated is True and len(be.seen[0].images) == 3
    assert "The first 3 candidates come with one frame" in be.seen[0].prompt
    starts = [m["start"] for m in got["matches"]]
    assert starts == [21.0, 11.0, 31.0, 1.0]            # yes, maybe, unjudged, no
    assert got["matches"][0]["rerank"]["fit"] == "yes" and "model" in got["matches"][0]["source"]
    assert len(got["matches"]) == 4 and got["usage"]["thumbs"] == 3


def test_rerank_drops_the_frames_it_cannot_make_rather_than_mislabel_them():
    from roughcut import inference
    be = _Scripted([{"n": 1, "fit": "yes", "why": ""}])
    inference.set_backend(be)
    try:
        find.rerank("q", _rows(), lambda r: None)
    finally:
        inference.set_backend(None)
    assert be.seen[0].images == () and "No frames are attached" in be.seen[0].prompt


def test_validate_rerank_rejects_a_candidate_that_does_not_exist():
    with pytest.raises(ValueError, match="candidate 9"):
        find.validate_rerank({"ranking": [{"n": 9, "fit": "yes"}]}, 4)
    with pytest.raises(ValueError, match="fit"):
        find.validate_rerank({"ranking": [{"n": 1, "fit": "great"}]}, 4)


# ------------------------------------------------------------------ the endpoint

def test_find_endpoint_reports_the_local_state_and_the_rerank_policy(client):
    d = client.post("/api/find", json={"query": "goodbye"}).json()
    assert d["local"]["used"] is False                    # the suite runs with the local stack off
    assert d["rerank"]["allowed"] and d["rerank"]["usd"] > 0
    assert d["matches"][0]["label"] == "heard"
    price = client.get("/api/find/price").json()
    assert price["rerank_usd"] == d["rerank"]["usd"]


def test_the_rerank_endpoint_is_refused_in_local_only_mode(client):
    assert client.put("/api/index/mode", json={"mode": "local"}).status_code == 200
    r = client.post("/api/find", json={"query": "goodbye", "rerank": True})
    assert r.status_code == 409 and "Local only" in r.json()["detail"]
    assert client.post("/api/find", json={"query": "goodbye"}).json()["rerank"]["allowed"] is False
    client.put("/api/index/mode", json={"mode": "hybrid"})


def test_the_rerank_endpoint_runs_one_call_and_returns_the_reordered_list(client):
    from roughcut import inference
    be = _Scripted([{"n": 1, "fit": "yes", "why": "said"}])
    inference.set_backend(be)
    inference.reset_spend()
    assert client.put("/api/index/mode", json={"mode": "hybrid"}).status_code == 200
    try:
        r = client.post("/api/find", json={"query": "goodbye", "rerank": True})
        assert r.status_code == 200 and r.json()["job"]
        import time
        for _ in range(100):
            s = client.get(f"/api/job/{r.json()['job']}").json()
            if s["state"] in ("done", "failed"):
                break
            time.sleep(0.05)
        assert s["state"] == "done", s
        assert s["found"]["matches"][0]["rerank"]["fit"] == "yes"
        assert len(be.seen) == 1
    finally:
        inference.set_backend(None)


def test_rerank_and_deep_together_are_refused(client):
    assert client.post("/api/find", json={"query": "goodbye", "rerank": True, "deep": True}).status_code == 400
