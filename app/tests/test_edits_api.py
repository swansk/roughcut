"""The server's part of the edits (INTAKE M13): `speed` through a save, generated
clips as real files the project lists and the media routes play, and the
preview / apply / undo endpoints over `roughcut.edits`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import edits  # noqa: E402


def _segments(client) -> list[dict]:
    return client.get("/api/project").json()["segments"]


def _save(client, segments: list[dict], **extra):
    return client.put("/api/project", json={"segments": segments, **extra})


# ------------------------------------------------------------------ speed on a save

def test_speed_survives_a_save_and_one_is_not_stored(client):
    segs = _segments(client)
    segs[0]["speed"] = 0.5
    segs[1]["speed"] = 1
    r = _save(client, segs)
    assert r.status_code == 200, r.text
    got = _segments(client)
    assert got[0]["speed"] == 0.5
    assert "speed" not in got[1]
    assert edits.dur(got[0]) == 4.0
    # and it round-trips through the next save untouched
    r = _save(client, got)
    assert r.status_code == 200
    assert _segments(client)[0]["speed"] == 0.5


def test_a_bad_speed_is_a_400_with_the_sentence_and_nothing_is_written(client, project):
    before = project["edl"].read_text(encoding="utf-8")
    segs = _segments(client)
    segs[0]["speed"] = 9
    r = _save(client, segs)
    assert r.status_code == 400
    assert "out of range" in r.json()["detail"]
    segs[0]["speed"] = "slow"
    r = _save(client, segs)
    assert r.status_code == 400
    assert "not a number" in r.json()["detail"]
    assert project["edl"].read_text(encoding="utf-8") == before


def test_a_render_plans_the_cut_at_its_speed(client, monkeypatch):
    import server
    started: dict = {}

    def fake_render(job, edl_path, out_path, meta):
        started["meta"] = meta
        server.RENDERS[job].finish("done")

    monkeypatch.setattr(server, "_render_job", fake_render)
    segs = _segments(client)
    segs[0]["speed"] = 0.5                     # 2 s of clip → 4 s of film
    r = client.post("/api/render", json={"segments": segs, "profile": "preview"})
    assert r.status_code == 200, r.text
    job = r.json()["job"]
    assert server.RENDERS[job]["planned_s"] == 6.0
    assert started["meta"]["planned_s"] == 6.0
    assert started["meta"]["shots"][0]["speed"] == 0.5
    assert "speed" not in started["meta"]["shots"][1]
