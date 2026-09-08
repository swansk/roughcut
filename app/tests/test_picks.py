"""The picks engine — pure functions over the clip dicts and the events list."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import picks  # noqa: E402


def _clip(candidates=None, transcript=None, duration=60.0):
    return {"clip": "CLIP_A.MP4", "duration": duration,
            "transcript": transcript if transcript is not None else [
                {"start": 10.0, "end": 12.0, "text": "pocket pizza"},
                {"start": 30.0, "end": 33.0, "text": "go back feet yeah"},
            ],
            "candidates": candidates if candidates is not None else [
                {"t": 10.0, "end": 12.0, "why": "interest marker", "score": 0.7},
            ],
            "summary": {}, "visual": {}}


def _event(start, end, confirmation="unseen", kind="jump", score=0.9, what="airborne",
           clip="CLIP_A.MP4", rank=1, peak_at=None, z=0.0):
    return {"clip": clip, "start": start, "end": end, "kind": kind, "what": what,
            "notable": True, "score": score, "rank": rank,
            "why_ranked": {"confirmation": confirmation, "corroboration_z": z,
                           "peak_at": peak_at if peak_at is not None else start}}


def test_a_spoken_candidate_becomes_a_heard_pick():
    out = picks.build({"CLIP_A.MP4": _clip()}, [])
    assert len(out) == 1
    p = out[0]
    assert p["kind"] == "speech"
    assert p["start"] == pytest.approx(9.5) and p["end"] == pytest.approx(12.5)
    assert p["witnesses"][0]["kind"] == "heard"
    assert "pocket pizza" in p["witnesses"][0]["text"] and "pocket pizza" in p["why"]
    assert p["preview"] == [9.5, 12.5], "short picks play whole"
    assert p["verdict"] is None and p["rank"] == 1


def test_seen_witnesses_carry_three_states_and_a_contradiction_is_kept_visible():
    clips = {"CLIP_A.MP4": _clip(candidates=[])}
    evs = [_event(20.0, 24.0, "confirmed", rank=1),
           _event(40.0, 44.0, "contradicted", what="backflip", rank=2, score=0.5),
           _event(50.0, 52.0, "unseen", what="wide slope", kind="scenery", rank=3, score=0.2)]
    out = picks.build(clips, evs)
    by_start = {p["start"]: p for p in out}
    assert by_start[20.0]["witnesses"][0]["state"] == "audited"
    assert by_start[50.0]["witnesses"][0]["state"] == "claimed"
    contra = by_start[40.0]
    assert contra["witnesses"][0]["state"] == "contradicted"
    assert "backflip" in contra["conflict"] and "did not support" in contra["conflict"]
    assert contra["why"] == "", "a contradicted claim never writes the reason line"
    # and it is ranked below the audited one by a wide margin
    assert contra["score"] < by_start[20.0]["score"] * 0.5


def test_overlapping_witnesses_merge_into_one_pick_with_corroboration():
    clips = {"CLIP_A.MP4": _clip(candidates=[
        {"t": 30.0, "end": 33.0, "why": "reaction", "score": 0.6}])}
    evs = [_event(24.0, 29.0, "confirmed", what="big air then a hard landing")]
    out = picks.build(clips, evs)
    assert len(out) == 1, "an event and the reaction 1 s after it are one moment"
    p = out[0]
    assert {w["kind"] for w in p["witnesses"]} == {"seen", "heard"}
    assert p["start"] == 24.0 and p["end"] == pytest.approx(33.5)
    assert "big air" in p["why"] and "go back feet" in p["why"]
    # corroboration: worth more than the event alone
    alone = picks.build({"CLIP_A.MP4": _clip(candidates=[])}, evs)[0]
    assert p["score"] > alone["score"]


def test_long_picks_preview_the_moment_and_anchor_on_the_strongest_witness():
    clips = {"CLIP_A.MP4": _clip(candidates=[
        {"t": 30.0, "end": 33.0, "why": "reaction", "score": 0.6}])}
    evs = [_event(14.0, 29.0, "confirmed", peak_at=26.0, z=3.0)]
    p = picks.build(clips, evs)[0]
    assert p["end"] - p["start"] > picks.PREVIEW_WHOLE_S
    assert p["anchor"] == 26.0, "the audited event's peak, not the line"
    assert p["preview"] == [23.0, 29.0]


def test_a_telemetry_peak_alone_is_never_a_pick_but_joins_one():
    clips = {"CLIP_A.MP4": _clip(candidates=[])}
    tele = {"CLIP_A.MP4": {"peaks": [{"at": 45.0, "value": 4.2, "unit": "g"},
                                     {"at": 21.0, "value": 1.1, "unit": "s freefall"}]}}
    assert picks.build(clips, [], telemetry=tele) == [], "numbers alone cannot promote"
    evs = [_event(20.0, 24.0, "unseen")]
    p = picks.build(clips, evs, telemetry=tele)[0]
    felt = [w for w in p["witnesses"] if w["kind"] == "felt"]
    assert len(felt) == 1 and felt[0]["text"] == "1.1 s freefall" and felt[0]["state"] is None
    assert "telemetry: 1.1 s freefall" in p["why"]


def test_a_freefall_run_corroborates_but_an_impact_is_a_number_only():
    """R11's rule: a freefall run under 0.5 g for 0.25 s may carry a small corroboration
    weight; an impact peak is shown and never moves the rank."""
    clips = {"CLIP_A.MP4": _clip(candidates=[])}
    evs = [_event(20.0, 24.0, "unseen")]
    plain = picks.build(clips, evs)[0]
    fall = picks.build(clips, evs, telemetry={"CLIP_A.MP4": {"peaks": [
        {"at": 21.0, "value": 0.3, "unit": "s freefall at 0.03 g"}]}})[0]
    hit = picks.build(clips, evs, telemetry={"CLIP_A.MP4": {"peaks": [
        {"at": 21.0, "value": 6.7, "unit": "g"}]}})[0]
    assert fall["score"] == pytest.approx(plain["score"] + picks.CORROBORATION_BONUS)
    assert hit["score"] == pytest.approx(plain["score"]), "an impact does not move the rank"
    assert "6.7 g" in hit["why"]
    assert all(w["state"] is None for w in hit["witnesses"] if w["kind"] == "felt")


def test_themes_tag_and_lift_matching_picks():
    clips = {"CLIP_A.MP4": _clip()}
    plain = picks.build(clips, [])[0]
    themed = picks.build(clips, [], themes=["the pizza gag", "hitting rocks"])[0]
    assert themed["tags"] == ["the pizza gag"]
    assert themed["score"] == pytest.approx(plain["score"] + picks.THEME_BONUS)


def test_stored_verdicts_reattach_by_overlap_and_keeps_win():
    clips = {"CLIP_A.MP4": _clip()}
    keep = {"id": "k_1", "clip": "CLIP_A.MP4", "start": 9.0, "end": 12.0,
            "hero": True, "note": "the gag"}
    reject = {"clip": "CLIP_A.MP4", "start": 9.5, "end": 12.5, "verdict": "reject"}
    p = picks.build(clips, [], verdicts=[reject], selects=[keep])[0]
    assert p["verdict"] == "pick" and p["hero"] and p["note"] == "the gag"
    assert p["select_id"] == "k_1"
    # a reject far away does not touch it; a reject on it does, when there is no keep
    far = {"clip": "CLIP_A.MP4", "start": 40.0, "end": 44.0, "verdict": "reject"}
    assert picks.build(clips, [], verdicts=[far])[0]["verdict"] is None
    assert picks.build(clips, [], verdicts=[reject])[0]["verdict"] == "reject"


def test_order_and_rounds():
    clips = {"CLIP_B.MP4": {**_clip(), "clip": "CLIP_B.MP4"},
             "CLIP_A.MP4": _clip(candidates=[{"t": 50.0, "end": 52.0, "why": "x",
                                              "score": 0.9}])}
    out = picks.build(clips, [])
    assert [p["clip"] for p in picks.order(out, "rank")] == ["CLIP_A.MP4", "CLIP_B.MP4"]
    assert [p["clip"] for p in picks.order(out, "clip")] == ["CLIP_A.MP4", "CLIP_B.MP4"]
    assert picks.order(out, "clip")[0]["start"] == 49.5
    assert len(picks.rounds(out, size=1)) == 2
    out[0]["verdict"] = "pick"
    assert len(picks.rounds(out, size=40)[0]) == 1, "decided picks leave the round"
    assert picks.rounds([], size=40) == [[]]
