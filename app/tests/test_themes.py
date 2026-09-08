"""Themes proposed from the transcripts (INTAKE I5.2) — the module and the endpoints."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import themes  # noqa: E402


def _clips():
    return {"CLIP_A.MP4": {"clip": "CLIP_A.MP4", "duration": 6.0, "transcript": [
                {"start": 0.5, "end": 2.0, "text": "how's your milk, Spenny"},
                {"start": 2.4, "end": 4.0, "text": "look at the cinematography"}]},
            "CLIP_B.MP4": {"clip": "CLIP_B.MP4", "duration": 6.0, "transcript": []}}


def test_the_prompt_carries_every_transcript_and_asks_for_recognisable_phrases():
    p = themes.build_prompt(_clips(), story="a loose film for the friends")
    assert "how's your milk, Spenny" in p and "(no speech)" in p
    assert "a loose film for the friends" in p
    assert "not a category" in p and "names" in p


def test_validate_rejects_an_invented_clip_and_dedupes():
    with pytest.raises(ValueError, match="unknown clip"):
        themes.validate_proposal({"themes": [
            {"theme": "the milk joke", "clips": ["NOPE.MP4"]}]}, _clips())
    with pytest.raises(ValueError, match="no usable theme"):
        themes.validate_proposal({"themes": [{"theme": "   "}]}, _clips())
    ok = themes.validate_proposal({"themes": [
        {"theme": "the milk joke", "clips": ["CLIP_A.MP4", "CLIP_A.MP4"],
         "lines": ["how's your milk, Spenny"], "why": "recurs"},
        {"theme": "The Milk Joke", "clips": []}],
        "names": ["Spenny", "spenny", "Eric,"], "notes": "a milk film"}, _clips())
    assert len(ok["themes"]) == 1 and ok["themes"][0]["clips"] == ["CLIP_A.MP4"]
    assert ok["names"] == ["Spenny", "Eric"] and ok["notes"] == "a milk film"


def test_the_price_is_calibrated_on_the_live_run():
    """Killington, 2026-09-08: 12 clips, 323 capped lines -> $0.20 billed. The estimate
    must land near that, not at the $0.09 it first said."""
    clips = {f"C{i}.MP4": {"clip": f"C{i}.MP4", "duration": 200.0,
                           "transcript": [{"start": j, "end": j + 1, "text": "a line of talk"}
                                          for j in range(27)]} for i in range(12)}
    assert 0.16 <= themes.projected_usd(clips) <= 0.26
    assert 0 < themes.projected_usd(_clips()) < 0.2


# ------------------------------------------------------------------ endpoints

def _wait(client, job, timeout=20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/job/{job}").json()
        if s["state"] != "running":
            return s
        time.sleep(0.05)
    raise AssertionError(f"themes never finished: {s}")


def test_themes_are_proposed_by_one_call_and_kept_by_the_editor(client, project):
    from roughcut import config, inference

    class Scripted:
        name = "scripted"
        seen: list = []

        def complete(self, request):
            Scripted.seen.append(request)
            text = json.dumps({
                "themes": [{"theme": "the greeting", "why": "every clip opens on it",
                            "clips": ["CLIP_A.MP4", "CLIP_B.MP4"], "lines": ["hello there"]},
                           {"theme": "saying goodbye", "why": "a payoff", "clips": ["CLIP_C.MP4"],
                            "lines": ["goodbye"]}],
                "names": ["Spenny"], "notes": "people meeting and parting"})
            model = config.model_for(request.role)
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend="scripted", model=model,
                                    projected_usd=1e-4, latency_ms=1, raw=text)

    before = client.get("/api/themes").json()
    assert before["themes"] == [] and before["projected_usd"] > 0 and before["job"] is None

    inference.set_backend(Scripted())
    inference.reset_spend()
    try:
        r = client.post("/api/themes/propose", json={"story": "two people talking"})
        assert r.status_code == 200, r.text
        s = _wait(client, r.json()["job"])
        assert s["state"] == "done", s
        p = s["proposal"]
        assert [t["theme"] for t in p["themes"]] == ["the greeting", "saying goodbye"]
        assert p["names"] == ["Spenny"] and p["usage"]["projected_usd"] > 0
        assert len(Scripted.seen) == 1 and Scripted.seen[0].role == config.ROLE_JUDGE
        assert "hello there" in Scripted.seen[0].prompt and "two people talking" in Scripted.seen[0].prompt
    finally:
        inference.set_backend(None)

    # the proposal is not the EDL's word until the editor keeps it — but it survives a
    # reload: the last finished proposal rides along with the endpoint
    after = client.get("/api/themes").json()
    assert after["themes"] == [] and after["job"] is None
    assert after["last"]["id"] == r.json()["job"] and after["last"]["proposal"]["names"] == ["Spenny"]
    assert isinstance(after["dictation"], bool)
    # and on disk, so a restart does not lose what was paid for; Discard removes it
    import server
    assert json.loads(server.proposal_path().read_text(encoding="utf-8"))["id"] == r.json()["job"]
    server.THEMES.clear()                          # a restart, in effect
    assert client.get("/api/themes").json()["last"]["proposal"]["names"] == ["Spenny"]
    assert client.post("/api/themes/discard").status_code == 200
    assert client.get("/api/themes").json()["last"] is None
    assert not server.proposal_path().exists()
    r = client.put("/api/themes", json={"themes": ["the greeting", " The Greeting ", "goodbye"],
                                        "names": ["Spenny", "Eric"]})
    assert r.status_code == 200 and r.json()["themes"] == ["the greeting", "goodbye"]
    on_disk = json.loads(project["edl"].read_text(encoding="utf-8"))
    assert on_disk["themes"] == ["the greeting", "goodbye"] and on_disk["names"] == ["Spenny", "Eric"]
    # and the picks read them: the "goodbye" candidate is tagged and lifted
    tagged = [p for p in client.get("/api/picks").json()["picks"] if p["tags"]]
    assert tagged and "goodbye" in tagged[0]["tags"]
    assert client.put("/api/themes", json={"themes": "not a list"}).status_code == 400


def test_a_proposal_naming_an_unknown_clip_fails_the_job(client):
    from roughcut import config, inference

    class Inventing:
        name = "scripted"

        def complete(self, request):
            text = json.dumps({"themes": [{"theme": "x", "clips": ["IMAGINARY.MP4"]}]})
            model = config.model_for(request.role)
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend="scripted", model=model,
                                    projected_usd=1e-4, latency_ms=1, raw=text)

    inference.set_backend(Inventing())
    inference.reset_spend()
    try:
        s = _wait(client, client.post("/api/themes/propose", json={}).json()["job"])
        assert s["state"] == "failed" and s["code"] == 502 and "unknown clip" in s["detail"]
    finally:
        inference.set_backend(None)
