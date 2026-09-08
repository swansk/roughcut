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
    selects.apply_verdict(edl, "CLIP_A.MP4", 1.0, 3.0, "reject", why="other take of CLIP_A.MP4:jump:1")
    assert edl["floor"]["verdicts"][0]["verdict"] == "reject"
    assert edl["floor"]["verdicts"][0]["why"] == "other take of CLIP_A.MP4:jump:1"
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


def test_dictation_returns_the_text_the_tool_heard(client, project, tmp_path, monkeypatch):
    """The endpoint hands the recording to roughcut.dictate and returns its text, with the
    EDL's names on the way in. The recogniser is stubbed through the command builder: a real
    run means a GPU and a model download, and neither says anything about the plumbing."""
    import subprocess
    from roughcut import dictate

    script = tmp_path / "dictate_stub.py"
    script.write_text(
        "import json\n"
        "print(json.dumps({'text': ' hold on his face after ', 'latency_ms': 900,\n"
        "                  'model': 'small', 'duration_s': 1.0, 'device': 'cuda/float16'}))\n",
        encoding="utf-8")
    seen: dict = {}

    def stub_cmd(path, names):
        seen["names"], seen["suffix"] = names, Path(path).suffix
        return [sys.executable, str(script), str(path)]

    monkeypatch.setattr(dictate, "dictate_cmd", stub_cmd)
    edl = json.loads(project["edl"].read_text(encoding="utf-8"))
    edl["names"] = ["Spenny", "Karl"]
    project["edl"].write_text(json.dumps(edl), encoding="utf-8")   # the client fixture restores it
    wav = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=1", "-ar", "16000", "-ac", "1", "-f", "wav", "pipe:1"],
        check=True, capture_output=True).stdout

    r = client.post("/api/dictate", content=wav, headers={"content-type": "audio/wav"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == "hold on his face after" and body["model"] == "small"
    assert isinstance(body["latency_ms"], int) and body["duration_s"] == 1.0
    assert seen == {"names": ["Spenny", "Karl"], "suffix": ".wav"}
    assert not list(project["work"].glob("dictate_*")), "the recording is removed after use"

    # bytes that are not a recording are one failed note, not a crash
    r = client.post("/api/dictate", content=b"\x00\x01", headers={"content-type": "audio/webm"})
    assert r.status_code == 501 and "could not be decoded" in r.json()["detail"]
    assert client.post("/api/dictate", content=b"",
                       headers={"content-type": "audio/wav"}).status_code == 400


def test_a_recording_over_thirty_seconds_is_refused_with_413(client, monkeypatch):
    from roughcut import dictate

    def too_long(path, *, names=None):
        raise dictate.TooLong("recording is 31.0 s — notes are at most 30 s")

    monkeypatch.setattr(dictate, "transcribe", too_long)
    r = client.post("/api/dictate", content=b"RIFF....", headers={"content-type": "audio/wav"})
    assert r.status_code == 413 and "30 s" in r.json()["detail"]


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


# ------------------------------------------------------------------- relink (I1.4)

def test_a_select_records_its_clips_duration_when_known():
    s = selects.new_select("CLIP_A.MP4", 1.0, 3.0, clip_duration=12.5004)
    assert s["clip_duration"] == 12.5
    assert "clip_duration" not in selects.new_select("CLIP_A.MP4", 1.0, 3.0)
    assert "clip_duration" not in selects.new_select("CLIP_A.MP4", 1.0, 3.0, clip_duration=0)
    edl = {"segments": []}
    selects.apply_verdict(edl, "CLIP_A.MP4", 1.0, 3.0, "pick", clip_duration=12.5)
    selects.apply_verdict(edl, "CLIP_A.MP4", 2.0, 5.0, "pick")
    assert edl["selects"][0]["clip_duration"] == 12.5, "a merge keeps the side that knew"
    ok = selects.validate_selects([{"clip": "CLIP_A.MP4", "start": 1, "end": 2}],
                                  {"CLIP_A.MP4": {"duration": 6.0}})
    assert ok[0]["clip_duration"] == 6.0


def test_relink_flags_a_select_whose_clip_is_gone_and_never_drops_it():
    edl = {"segments": []}
    selects.apply_verdict(edl, "GOPR0001.MP4", 1.0, 3.0, "pick", why="the drop",
                          clip_duration=12.5)
    before = dict(edl["selects"][0])
    selects.relink(edl, {"CLIP_A.MP4": {"duration": 6.0}, "CLIP_B.MP4": {"duration": 40.0}})
    s = edl["selects"][0]
    assert s["missing"] is True and s["clip"] == "GOPR0001.MP4" and s["id"] == before["id"]
    assert s["why"] == "the drop" and s["clip_duration"] == 12.5
    # a select that never learned its clip's length is missing too, and never guessed
    edl2 = {"segments": []}
    selects.apply_verdict(edl2, "GOPR0002.MP4", 0.0, 1.0, "pick")
    selects.relink(edl2, {"ONLY.MP4": {"duration": 12.5}})
    assert edl2["selects"][0]["missing"] is True and edl2["selects"][0]["clip"] == "GOPR0002.MP4"


def test_relink_repoints_a_select_at_the_one_clip_of_the_same_length():
    edl = {"segments": [{"id": "s0", "clip": "GOPR0001.MP4", "in": 1.0, "out": 3.0}]}
    selects.apply_verdict(edl, "GOPR0001.MP4", 1.0, 3.0, "pick", note="keep", hero=True,
                          clip_duration=12.5)
    selects.used_in(edl)
    ident = edl["selects"][0]["id"]
    selects.relink(edl, {"CLIP_A.MP4": {"duration": 6.0},
                         "renamed-drop.mp4": {"duration": 12.54}})
    s = edl["selects"][0]
    assert s["clip"] == "renamed-drop.mp4" and "missing" not in s
    assert s["id"] == ident and s["used_in"] == ["s0"] and s["hero"] and s["note"] == "keep"
    assert s["clip_duration"] == 12.5, "the fingerprint is the recorded length, not the new one"
    # found again: a second pass with the same folder changes nothing
    again = json.dumps(edl["selects"], sort_keys=True)
    selects.relink(edl, {"CLIP_A.MP4": {"duration": 6.0}, "renamed-drop.mp4": {"duration": 12.54}})
    assert json.dumps(edl["selects"], sort_keys=True) == again
    # 0.06 s off is not the same footage
    edl3 = {"segments": []}
    selects.apply_verdict(edl3, "GOPR0001.MP4", 1.0, 3.0, "pick", clip_duration=12.5)
    selects.relink(edl3, {"near.mp4": {"duration": 12.56}})
    assert edl3["selects"][0]["missing"] is True


def test_relink_leaves_an_ambiguous_match_missing_rather_than_guess():
    edl = {"segments": []}
    selects.apply_verdict(edl, "GOPR0001.MP4", 1.0, 3.0, "pick", clip_duration=12.5)
    selects.relink(edl, {"a.mp4": {"duration": 12.5}, "b.mp4": {"duration": 12.52}})
    s = edl["selects"][0]
    assert s["missing"] is True and s["clip"] == "GOPR0001.MP4"
    # once only one of them remains the select finds its footage
    selects.relink(edl, {"b.mp4": {"duration": 12.52}})
    assert s["clip"] == "b.mp4" and "missing" not in s


# ------------------------------------------------- the server's side of M1

def test_a_floor_keep_records_its_clip_length_and_the_bin_relinks_on_read(client, project):
    client.post("/api/floor/verdict", json={
        "clip": "CLIP_A.MP4", "start": 1.0, "end": 3.0, "verdict": "pick"})
    on_disk = json.loads(project["edl"].read_text(encoding="utf-8"))
    assert on_disk["selects"][0]["clip_duration"] == pytest.approx(6.0, abs=0.1)
    # a keep whose clip left the folder is flagged on the next read, and persisted
    on_disk["selects"].append({**on_disk["selects"][0], "id": "k_gone",
                               "clip": "GONE.MP4", "clip_duration": 99.0})
    project["edl"].write_text(json.dumps(on_disk), encoding="utf-8")
    got = client.get("/api/selects").json()["selects"]
    gone = next(s for s in got if s["id"] == "k_gone")
    assert gone["missing"] is True
    assert next(s for s in got if s["id"] != "k_gone").get("missing") is None
    again = json.loads(project["edl"].read_text(encoding="utf-8"))
    assert any(s.get("missing") for s in again["selects"]), "the flag is written back"
    # and a missing select survives the bin editor's save
    r = client.put("/api/selects", json={"selects": got})
    assert r.status_code == 200 and r.json()["summary"]["moments"] == 2
    assert client.put("/api/selects", json={"selects": [
        {"clip": "GONE.MP4", "start": 0, "end": 1}]}).status_code == 400, \
        "an unknown clip without the flag is still a mistake"


def test_an_ask_carries_the_bin_into_the_prompt(client, project):
    """The server passes `selects` through, so the first cut is asked *from the bin*."""
    import time as _time
    from roughcut import config, inference

    client.post("/api/floor/verdict", json={
        "clip": "CLIP_C.MP4", "start": 0.5, "end": 4.0, "verdict": "pick", "hero": True,
        "note": "the one the whole film is about"})

    class Scripted:
        name = "scripted"
        seen: list = []

        def complete(self, request):
            Scripted.seen.append(request)
            text = json.dumps({"segments": [{"clip": "CLIP_C.MP4", "in": 0.5, "out": 4.0,
                                             "why": "the hero"}],
                               "notes": "built from the bin"})
            model = config.model_for(request.role)
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend="scripted", model=model,
                                    projected_usd=1e-4, latency_ms=1, raw=text)

    inference.set_backend(Scripted())
    inference.reset_spend()
    try:
        job = client.post("/api/ask", json={"note": "", "segments": [],
                                            "story": "a film"}).json()["job"]
        deadline = _time.time() + 20
        while _time.time() < deadline:
            s = client.get(f"/api/ask/{job}").json()
            if s["state"] not in ("estimating", "running"):
                break
            _time.sleep(0.05)
        assert s["state"] == "done", s
        prompt = Scripted.seen[-1].prompt
        assert "## The editor's selects" in prompt
        assert "the one the whole film is about" in prompt and "HERO" in prompt
    finally:
        inference.set_backend(None)


def test_relink_leaves_a_present_clip_untouched_and_clears_a_stale_flag():
    edl = {"segments": []}
    selects.apply_verdict(edl, "CLIP_A.MP4", 1.0, 3.0, "pick", clip_duration=6.0)
    selects.apply_verdict(edl, "CLIP_B.MP4", 1.0, 3.0, "pick")
    before = json.dumps(edl["selects"][0], sort_keys=True)
    edl["selects"][1]["missing"] = True          # flagged on an earlier pass
    selects.relink(edl, {"CLIP_A.MP4": {"duration": 6.0}, "CLIP_B.MP4": {"duration": 6.0}})
    assert json.dumps(edl["selects"][0], sort_keys=True) == before
    b = edl["selects"][1]
    assert "missing" not in b, "the file is back under its own name"
    assert b["clip_duration"] == 6.0, "and a select that never knew its length learns it"
