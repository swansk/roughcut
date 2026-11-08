"""The first pass in words, and each moment's certainty (wave 20 K5UI; REDESIGN-GOAL K5 and
the UI half of K6). Pure functions of `roughcut.flow`, then the two endpoints that feed them.

Karl, 2026-11-07: "when I click run first pass for killington, I am not really sure what it is
doing, or what level of indexing is being done e.g level of granularity." The words name each
step, what it does now and did, and the granularity, with this bin's numbers (Killington:
43 min, 12 clips, 1 frame every 4 s, 651 frames, ...)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import flow  # noqa: E402

from k5_fixture import EMPTY_BIN, KILLINGTON, RUNS  # noqa: E402


def _steps(a, run):
    return {s["key"]: s for s in flow.pass_account(a, RUNS[run])["steps"]}


def test_done_on_an_existing_index_says_it_only_re_ranked_and_re_cut():
    acc = flow.pass_account(KILLINGTON, RUNS["done-existing"])
    ix = acc["steps"][0]
    assert ix["state"] == "done" and ix["text"].startswith("already done")
    for want in ("heard 12 clips", "1,666 words", "every word timed", "Whisper large-v3",
                 "looked at 1 frame every 4 s", "651 frames", "6×5 contact sheets of 320 px",
                 "Sonnet", "close looks on 7 of 43 min", "1 frame/s", "480 px", "Opus"):
        assert want in ix["text"], (want, ix["text"])
    assert "Nothing was re-watched" in ix["text"]
    assert acc["rerank_only"] is True
    assert "only re-ranked" in acc["headline"] and "Index panel" in acc["headline"]
    assert acc["steps"][1]["text"] == "ranked 188 moments into 40 picks"
    assert acc["steps"][2]["text"].startswith("wrote a 19-shot cut")


def test_done_fresh_says_it_heard_and_looked():
    acc = flow.pass_account(KILLINGTON, RUNS["done-fresh"])
    assert acc["rerank_only"] is False
    assert acc["steps"][0]["text"].startswith("ran — heard 12 clips")
    assert "heard and looked at the footage" in acc["headline"]


def test_never_run_says_what_it_will_do_and_how_finely():
    s = _steps(EMPTY_BIN, "never-run")
    assert s["index"]["state"] == "todo"
    assert "every word timed" in s["index"]["text"]
    assert "1 frame every 4 s on contact sheets" in s["index"]["text"]
    assert "close looks" in s["index"]["text"]
    assert s["picks"]["text"].startswith("will rank what the index found")
    assert "nothing changes until you accept" in s["cut"]["text"]


def test_never_run_on_an_indexed_bin_says_what_is_already_there():
    s = _steps(KILLINGTON, "never-run")
    assert "Already: heard 12 clips" in s["index"]["text"]
    assert s["picks"]["text"].startswith("will rank 188 moments")


def test_running_says_what_it_is_doing_now_at_each_stage():
    a = flow.pass_account(KILLINGTON, RUNS["running-index"])
    assert a["now"].startswith("Index: hearing and looking at every clip")
    assert "heard 3/12 · looked at 1/12" in a["now"] and "1 frame every 4 s" in a["now"]
    assert [s["state"] for s in a["steps"]] == ["now", "todo", "todo"]
    a = flow.pass_account(KILLINGTON, RUNS["running-picks"])
    assert a["now"] == "Picks: ranking 188 moments into picks"
    assert [s["state"] for s in a["steps"]] == ["done", "now", "todo"]
    a = flow.pass_account(KILLINGTON, RUNS["running-cut"])
    assert a["now"] == "Rough cut: writing the cut — choosing among 40 picks"


def test_missing_numbers_drop_their_phrase_never_raise():
    for a in ({}, {"clips": 3}, {"heard": {}, "looked": None, "close": {"clips": 2}},
              {"looked": {"clips": 2, "intervals": {"2.0": 1, "4.0": 1}}}):
        for run in RUNS.values():
            out = flow.pass_account(a, run)
            assert len(out["steps"]) == 3
    mixed = flow.looked_words({"looked": {"clips": 2, "intervals": {"2.0": 1, "4.0": 1}}})
    assert "1 at 4 s" in mixed and "1 at 2 s" in mixed


def test_no_gpu_is_said_with_its_reason():
    a = {**KILLINGTON, "local": {"available": False, "reason": "no NVIDIA GPU found"}}
    assert "3080 not available (no NVIDIA GPU found)" in flow.index_account(a)
    a = {**KILLINGTON, "local": {"available": True, "checked": 14}}
    assert "3080 checked 14 windows" in flow.index_account(a)


def test_mode_words_are_the_three_modes():
    assert set(flow.MODE_WORDS) == {"hybrid", "local", "claude"}
    out = flow.pass_account({**KILLINGTON, "mode": "hybrid"}, RUNS["never-run"])
    assert out["mode"]["label"] == "Hybrid (3080 + Claude)"


# ---------------------------------------------------------------- certainty

def _m(**kw):
    return {"start": 10.0, "end": 12.0, "kind": "jump", "what": "a jump", "source": "sheet",
            "status": "unseen", "score": 1.0, **kw}


def test_certainty_levels_from_todays_statuses():
    want = {"unseen": "unchecked", "fine-only": "once", "confirmed": "claude", "deep": "claude",
            "unsupported": "unsupported", "contradicted": "contradicted", "": "unchecked"}
    for status, level in want.items():
        assert flow.certainty(_m(status=status))["level"] == level, status


def test_a_telemetry_air_is_felt_and_claude_confirms_it():
    air = _m(source="telemetry", status="felt", kind="air", numbers={"air_s": 0.39})
    c = flow.certainty(air)
    assert c["level"] == "felt" and c["chip"] == "felt"
    assert c["title"] == "air 0.39 s · telemetry · measured by the sensor, no look has checked it"
    done = flow.certainty({**air, "verdict": {"verdict": "confirm", "by": "claude"}})
    assert done["level"] == "claude"
    assert done["title"] == "air 0.39 s · telemetry · confirmed by Claude"
    loc = flow.certainty({**air, "verdict": "confirmed", "checked_by": "local"})
    assert loc["level"] == "local" and loc["title"].endswith("confirmed by the 3080")


def test_a_rejected_or_unsure_check():
    air = _m(source="telemetry", status="felt", numbers="air 0.31 s")
    rej = flow.certainty({**air, "verdict": {"verdict": "reject", "by": "claude"}})
    assert rej["level"] == "contradicted" and "rejected by Claude" in rej["title"]
    unsure = flow.certainty({**air, "verdict": {"verdict": "unsure", "by": "claude"}})
    assert unsure["level"] == "felt" and "unsure after Claude's check" in unsure["title"]


def test_counts_and_words_in_order_without_zeros():
    ms = [_m(status="confirmed")] + [_m(status="unseen")] * 3 + [_m(status="fine-only")] * 2
    c = flow.certainty_counts(ms)
    assert c == {"claude": 1, "unchecked": 3, "once": 2, "total": 6}
    assert flow.certainty_words(c) == ("6 moments: 1 checked by Claude · 2 seen once · "
                                       "3 unchecked")
    assert flow.certainty_words({}) == "" and flow.certainty_counts([])["total"] == 0


def test_a_shot_rests_on_the_best_scored_overlapping_moment():
    ms = [_m(start=0, end=2, score=1, status="unseen"),
          _m(start=3, end=5, score=9, status="confirmed"),
          _m(start=20, end=22, score=99, status="confirmed")]
    assert flow.shot_moment(ms, 1.0, 4.0)["score"] == 9
    assert flow.shot_moment(ms, 10.0, 12.0) is None
    assert flow.shot_moment([], 0, 1) is None
    assert flow.shot_moment([{"oops": 1}], 0, 1) is None


# ---------------------------------------------------------------- the endpoints

def test_account_endpoint_on_the_suites_bin(client):
    d = client.get("/api/firstpass/account").json()
    assert [s["key"] for s in d["steps"]] == ["index", "picks", "cut"]
    assert d["inputs"]["clips"] == 3
    assert d["steps"][0]["state"] in ("todo", "done")
    assert "bin" in d["certainty"] and d["certainty"]["bin"]["total"] >= 0
    assert d["granularity"].startswith("hear every clip")


def test_certainty_endpoint_nulls_a_shot_with_no_moment(client):
    r = client.post("/api/certainty", json={"shots": [
        {"clip": "CLIP_A.MP4", "in": 0.0, "out": 0.5}, {"clip": "nope.mp4", "in": 1, "out": 2},
        {"clip": "bad"}]})
    assert r.status_code == 200
    got = r.json()["shots"]
    assert len(got) == 3 and got[1] is None and got[2] is None


def test_lane_verify_and_telem_fields_read_as_the_chips():
    """The field names the other wave 20 lanes document (verify.py: `certainty`, `claim_status`,
    `verdict` {verdict, model}; telemetry_events.py: `text`, status "felt")."""
    air = _m(source="telemetry", status="felt", kind="air", who="wearer",
             text="air 0.39 s at 46.62-47.01 (landing 4.0 g)", certainty="telemetry")
    assert flow.certainty(air)["level"] == "felt"
    assert flow.certainty(air)["title"].startswith("air 0.39 s at 46.62-47.01 (landing 4.0 g) · telemetry")
    ok = flow.certainty({**air, "certainty": "claude",
                         "verdict": {"verdict": "confirm", "model": "claude-sonnet-4-5"}})
    assert ok["level"] == "claude" and ok["title"].endswith("telemetry · confirmed by Claude")
    assert flow.certainty({**air, "certainty": "local"})["level"] == "local"
    assert flow.certainty({**air, "certainty": "unchecked"})["level"] == "felt"
    assert flow.certainty({**air, "certainty": "unsure",
                           "verdict": {"verdict": "unsure", "model": "sonnet"}})["level"] == "felt"
    rej = flow.certainty({**air, "certainty": "rejected", "claim_status": "rejected"})
    assert rej["level"] == "contradicted" and "rejected by Claude" in rej["title"]
    old = flow.certainty(_m(claim_status="unsupported"))
    assert old["level"] == "unsupported"
