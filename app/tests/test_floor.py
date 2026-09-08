"""The floor's API and the bin in the EDL — selects, verdicts, notes, position."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import selects  # noqa: E402


# ----------------------------------------------------------------- selects.py

def test_pick_merges_overlapping_keeps_and_clears_a_reject_on_those_seconds():
    edl = {"segments": []}
    selects.apply_verdict(edl, "CLIP_A.MP4", 1.0, 3.0, "reject")
    assert edl["floor"]["verdicts"][0]["verdict"] == "reject"
    selects.apply_verdict(edl, "CLIP_A.MP4", 1.0, 3.0, "pick", why="first", note="one")
    assert edl["floor"]["verdicts"] == [], "a keep on rejected seconds wins"
    selects.apply_verdict(edl, "CLIP_A.MP4", 2.0, 5.0, "pick", note="two", hero=True)
    assert len(edl["selects"]) == 1
    s = edl["selects"][0]
    assert (s["start"], s["end"]) == (1.0, 5.0)
    assert s["hero"] and s["note"] == "two / one" and s["why"] == "first"


def test_a_reject_removes_the_keep_it_overlaps_and_a_far_one_does_not():
    edl = {"segments": []}
    selects.apply_verdict(edl, "CLIP_A.MP4", 1.0, 3.0, "pick")
    selects.apply_verdict(edl, "CLIP_A.MP4", 10.0, 12.0, "reject")
    assert len(edl["selects"]) == 1
    selects.apply_verdict(edl, "CLIP_A.MP4", 1.5, 3.5, "reject")
    assert edl["selects"] == []
    assert [v["verdict"] for v in edl["floor"]["verdicts"]] == ["reject", "reject"]
    selects.apply_verdict(edl, "CLIP_A.MP4", 1.5, 3.5, "clear")
    assert len(edl["floor"]["verdicts"]) == 1


def test_a_note_lands_on_the_covering_verdict_or_becomes_a_later():
    edl = {"segments": []}
    selects.apply_verdict(edl, "CLIP_A.MP4", 1.0, 3.0, "pick")
    selects.note(edl, "CLIP_A.MP4", 1.2, 2.8, "hold on his face")
    assert edl["selects"][0]["note"] == "hold on his face"
    selects.note(edl, "CLIP_A.MP4", 20.0, 24.0, "come back to this")
    v = edl["floor"]["verdicts"][0]
    assert v["verdict"] == "later" and v["note"] == "come back to this"


def test_used_in_is_recomputed_from_the_timeline():
    edl = {"segments": [{"id": "s0", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0},
                        {"id": "s1", "clip": "CLIP_B.MP4", "in": 1.0, "out": 3.0}]}
    selects.apply_verdict(edl, "CLIP_A.MP4", 1.0, 3.0, "pick")
    selects.used_in(edl)
    assert edl["selects"][0]["used_in"] == ["s0"]
    edl["segments"] = []
    selects.used_in(edl)
    assert edl["selects"][0]["used_in"] == [], "a shot deleted by hand stops counting"


def test_summary_counts_and_never_judges():
    edl = {"segments": []}
    selects.apply_verdict(edl, "CLIP_A.MP4", 1.0, 3.0, "pick", hero=True, note="n")
    selects.apply_verdict(edl, "CLIP_A.MP4", 10.0, 14.0, "pick")
    selects.apply_verdict(edl, "CLIP_B.MP4", 1.0, 2.0, "later")
    selects.apply_verdict(edl, "CLIP_B.MP4", 3.0, 4.0, "reject")
    s = selects.summary(edl)
    assert s["moments"] == 2 and s["heroes"] == 1 and s["notes"] == 1
    assert s["later"] == 1 and s["rejected"] == 1 and s["strung_out_s"] == 6.0
    assert "enough" not in json.dumps(s)


def test_validate_selects_is_strict():
    clips = {"CLIP_A.MP4": {"duration": 6.0}}
    with pytest.raises(ValueError, match="unknown clip"):
        selects.validate_selects([{"clip": "NOPE", "start": 0, "end": 1}], clips)
    with pytest.raises(ValueError, match="outside"):
        selects.validate_selects([{"clip": "CLIP_A.MP4", "start": 1, "end": 9}], clips)
    ok = selects.validate_selects([{"clip": "CLIP_A.MP4", "start": 1, "end": 2,
                                    "id": "k_keep", "hero": 1}], clips)
    assert ok[0]["id"] == "k_keep" and ok[0]["hero"] is True


# ----------------------------------------------------------------- the API

def test_picks_endpoint_lists_playable_picks_from_the_synthetic_bin(client):
    r = client.get("/api/picks")
    assert r.status_code == 200
    d = r.json()
    assert d["picks"], "the conftest candidates ('hello there', 'goodbye') are picks"
    p = d["picks"][0]
    assert p["proxy"].startswith("/media/proxy/") and "?t=" in p["poster"]
    assert p["released"] is True and p["duration"] == pytest.approx(6.0, abs=0.1)
    assert p["witnesses"][0]["kind"] == "heard"
    assert d["round_size"] == 40 and d["rounds"] == 1
    assert set(d["released"]) == {"CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"}
    assert d["summary"]["moments"] == 0
    assert client.get("/api/picks?order=clip").json()["picks"][0]["clip"] == "CLIP_A.MP4"
    assert client.get("/api/picks?order=weird").status_code == 400


def test_a_verdict_is_written_to_the_edl_and_reattaches(client, project):
    before = json.loads(project["edl"].read_text(encoding="utf-8"))
    assert "selects" not in before
    first = client.get("/api/picks").json()["picks"][0]
    r = client.post("/api/floor/verdict", json={
        "clip": first["clip"], "start": first["start"], "end": first["end"],
        "verdict": "pick", "hero": True, "why": first["why"], "note": "love this"})
    assert r.status_code == 200 and r.json()["summary"]["moments"] == 1
    on_disk = json.loads(project["edl"].read_text(encoding="utf-8"))
    assert on_disk["selects"][0]["clip"] == first["clip"]
    assert on_disk["selects"][0]["hero"] is True
    again = client.get("/api/picks").json()["picks"]
    mine = next(p for p in again if p["id"] == first["id"])
    assert mine["verdict"] == "pick" and mine["hero"] and mine["note"] == "love this"
    assert client.get("/api/selects").json()["summary"]["heroes"] == 1
    # the timeline is untouched: a keep is not a shot
    assert on_disk["segments"] == before["segments"]


def test_verdict_endpoint_validates(client):
    assert client.post("/api/floor/verdict", json={
        "clip": "NOPE.MP4", "start": 0, "end": 1, "verdict": "pick"}).status_code == 400
    assert client.post("/api/floor/verdict", json={
        "clip": "CLIP_A.MP4", "start": 2, "end": 1, "verdict": "pick"}).status_code == 400
    assert client.post("/api/floor/verdict", json={
        "clip": "CLIP_A.MP4", "start": 0, "end": 1, "verdict": "maybe"}).status_code == 400


def test_notes_and_position_round_trip(client, project):
    client.post("/api/floor/verdict", json={
        "clip": "CLIP_B.MP4", "start": 0.0, "end": 2.5, "verdict": "later"})
    r = client.post("/api/floor/note", json={
        "clip": "CLIP_B.MP4", "start": 0.2, "end": 2.3, "text": "revisit — the reply"})
    assert r.status_code == 200
    floor = client.get("/api/selects").json()["floor"]
    assert floor["verdicts"][0]["note"] == "revisit — the reply"
    r = client.put("/api/floor/position", json={"round": 2, "index": 7, "order": "clip"})
    assert r.json()["position"] == {"round": 2, "index": 7, "order": "clip"}
    assert client.put("/api/floor/position", json={"order": "sideways"}).status_code == 400
    assert client.get("/api/picks").json()["position"]["index"] == 7


def test_the_bin_can_be_replaced_wholesale_and_is_validated(client):
    r = client.put("/api/selects", json={"selects": [
        {"clip": "CLIP_C.MP4", "start": 1.0, "end": 4.0, "why": "by hand", "hero": True}]})
    assert r.status_code == 200 and r.json()["summary"]["heroes"] == 1
    assert client.get("/api/selects").json()["selects"][0]["source"] == "floor"
    assert client.put("/api/selects", json={"selects": [
        {"clip": "CLIP_C.MP4", "start": 5.0, "end": 99.0}]}).status_code == 400


def test_dictation_says_it_is_not_built_yet(client):
    r = client.post("/api/dictate", content=b"\x00\x01", headers={"content-type": "audio/webm"})
    assert r.status_code == 501 and "INTAKE" in r.json()["detail"]


def test_the_floor_page_is_served(client):
    r = client.get("/floor")
    assert r.status_code == 200 and "floor" in r.text.lower()
    assert r.headers["cache-control"].startswith("no-store")


# ------------------------------------------------- hand-added shots become keeps (I1.3)

def test_sync_timeline_adopts_a_hand_added_shot_as_a_keep_idempotently():
    edl = {"segments": [{"id": "s0", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0,
                         "why": "first"},
                        {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "by hand"}]}
    selects.apply_verdict(edl, "CLIP_A.MP4", 1.0, 3.0, "pick", why="the floor's")
    selects.sync_timeline(edl)
    assert [s["clip"] for s in edl["selects"]] == ["CLIP_A.MP4", "CLIP_B.MP4"]
    floor_keep, hand = edl["selects"]
    assert floor_keep["source"] == "floor" and floor_keep["used_in"] == ["s0"]
    assert hand["source"] == "hand" and hand["why"] == "by hand" and hand["note"] == ""
    assert (hand["start"], hand["end"]) == (0.0, 2.0)
    assert hand["used_in"] == ["s1"], "an id-less segment is counted by position"
    # again: nothing new, nothing duplicated, the ids hold
    ids = [s["id"] for s in edl["selects"]]
    selects.sync_timeline(edl)
    assert [s["id"] for s in edl["selects"]] == ids
    # a shot that a keep already covers is a use, not a hand-add — even trimmed
    edl["segments"][0] = {"id": "s0", "clip": "CLIP_A.MP4", "in": 1.4, "out": 2.6}
    selects.sync_timeline(edl)
    assert len(edl["selects"]) == 2 and edl["selects"][0]["used_in"] == ["s0"]


def test_a_hand_added_shot_on_rejected_seconds_is_the_human_changing_their_mind():
    edl = {"segments": []}
    selects.apply_verdict(edl, "CLIP_C.MP4", 1.0, 4.0, "reject", note="nah")
    edl["segments"] = [{"clip": "CLIP_C.MP4", "in": 1.5, "out": 3.5}]
    selects.sync_timeline(edl)
    assert edl["floor"]["verdicts"] == [], "placing it in the film outranks the reject"
    assert edl["selects"][0]["source"] == "hand"
    assert selects.summary(edl)["used"] == 1


def test_a_saved_timeline_grows_the_bin_by_its_hand_added_shots(client, project):
    """The API path: once the bin exists, every save teaches it which shots were
    placed by hand — a keep with `source: "hand"` and its `used_in`."""
    client.post("/api/floor/verdict", json={
        "clip": "CLIP_A.MP4", "start": 1.0, "end": 3.0, "verdict": "pick"})
    r = client.put("/api/project", json={"segments": [
        {"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
        {"clip": "CLIP_C.MP4", "in": 1.5, "out": 4.25, "why": "new one"}], "story": ""})
    assert r.status_code == 200
    bin_ = client.get("/api/selects").json()
    hands = [s for s in bin_["selects"] if s["source"] == "hand"]
    assert len(hands) == 1 and hands[0]["clip"] == "CLIP_C.MP4"
    assert hands[0]["why"] == "new one" and hands[0]["used_in"] == ["s1"]
    assert bin_["summary"]["moments"] == 2 and bin_["summary"]["used"] == 2
    on_disk = json.loads(project["edl"].read_text(encoding="utf-8"))
    assert [s["source"] for s in on_disk["selects"]] == ["floor", "hand"]
