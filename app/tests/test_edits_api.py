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

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def gen_dir(tmp_path):
    """Generated files go to a dir of this test's own. The project fixture is one work
    dir for the session and `project_payload` lists every generated file present, so
    a slide left behind here would appear in every later test's clip map."""
    import server
    server.STATE["generated_dir"] = tmp_path / "generated"
    yield server.STATE["generated_dir"]
    server.STATE.pop("generated_dir", None)
    server.STATE.pop("gen_durations", None)


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


# ------------------------------------------------------------------ generated clips

def _probe(path: Path) -> dict:
    import subprocess
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration:stream=codec_type,codec_name,width,height,pix_fmt",
                        "-of", "json", str(path)], capture_output=True, text=True)
    return json.loads(r.stdout)


def test_a_black_slide_is_a_real_file_made_once(client):
    import server
    spec = {"clip": edits.generated_name("black", 2.0), "kind": "black", "seconds": 2.0,
            "color": None, "from_clip": None, "at": None}
    p = server.materialise_generated(spec)
    assert p.exists() and p.parent == server.generated_dir()
    assert p.name == spec["clip"] and p.name.startswith("gen_black_")
    d = _probe(p)
    assert abs(float(d["format"]["duration"]) - 2.0) < 0.1
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    a = next(s for s in d["streams"] if s["codec_type"] == "audio")
    assert (v["width"], v["height"], v["codec_name"], v["pix_fmt"]) == (1280, 720, "h264", "yuv420p")
    assert a["codec_name"] == "aac"
    stamp = p.stat().st_mtime_ns
    assert server.materialise_generated(spec) == p           # idempotent: reused
    assert p.stat().st_mtime_ns == stamp
    rec = json.loads(server.generated_spec_path(p.name).read_text(encoding="utf-8"))
    assert rec["kind"] == "black" and rec["clip"] == p.name and rec["duration"] > 1.9


def test_a_colour_slide_and_a_still_are_made_from_their_specs(client):
    import server
    c = server.materialise_generated({"kind": "colour", "seconds": 1.0, "color": "#ff0000",
                                      "from_clip": None, "at": None})
    assert c.name == edits.generated_name("colour", 1.0, color="#ff0000")
    s = server.materialise_generated({"kind": "still", "seconds": 1.5, "color": None,
                                      "from_clip": "CLIP_A.MP4", "at": 2.5})
    assert s.name == edits.generated_name("still", 1.5, from_clip="CLIP_A.MP4", at=2.5)
    d = _probe(s)
    assert abs(float(d["format"]["duration"]) - 1.5) < 0.1
    v = next(x for x in d["streams"] if x["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1280, 720)
    assert not s.with_suffix(".png").exists()            # the frame was a means, not a file
    import pytest
    with pytest.raises(RuntimeError, match="no footage"):
        server.materialise_generated({"kind": "still", "seconds": 1.0, "color": None,
                                      "from_clip": "NOPE.MP4", "at": 1.0})


def test_generated_clips_are_listed_and_played_like_any_other(client):
    import server
    name = server.materialise_generated({"kind": "black", "seconds": 2.0, "color": None,
                                         "from_clip": None, "at": None}).name
    clips = client.get("/api/project").json()["clips"]
    assert name in clips and "CLIP_A.MP4" in clips
    c = clips[name]
    assert c["used"] is False and c["transcript"] == [] and c["candidates"] == []
    assert abs(c["duration"] - 2.0) < 0.1
    assert c["summary"]["generated"] == "generated: black 2.0 s"
    assert c["summary"]["audio_usable"] is False
    assert c["generated"]["kind"] == "black" and c["generated"]["clip"] == name
    assert c["proxy"] == f"/media/generated/{name}"
    assert c["poster"] == f"/media/poster/{Path(name).stem}.jpg"
    # the monitor's stream: whole file and a byte range
    r = client.get(c["proxy"])
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
    r = client.get(c["proxy"], headers={"range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100
    # the card's picture: the poster route falls back to the generated dir
    r = client.get(c["poster"], params={"t": 0.5})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert client.get("/media/generated/nope.mp4").status_code == 404
    # a segment on it marks it used; the Ask's clip map carries it with no transcript
    segs = _segments(client)
    segs.append({"clip": name, "in": 0.0, "out": 2.0, "why": "a breath"})
    assert _save(client, segs).status_code == 200
    assert client.get("/api/project").json()["clips"][name]["used"] is True
    ask_clips, _ = server._ask_clips()
    assert ask_clips[name]["transcript"] == [] and ask_clips[name]["duration"] > 1.9
    assert ask_clips[name]["summary"]["generated"].startswith("generated: black")


def test_a_segment_naming_a_generated_clip_that_is_not_there_is_listed_missing(client):
    ghost = edits.generated_name("black", 9.0)
    segs = _segments(client)
    segs.append({"clip": ghost, "in": 0.0, "out": 9.0})
    assert _save(client, segs).status_code == 200
    c = client.get("/api/project").json()["clips"][ghost]
    assert c["missing"] is True and c["proxy"] == "" and c["duration"] is None
    assert c["used"] is True


# ------------------------------------------------------------------ preview / apply / undo

def _on_disk(project) -> dict:
    return json.loads(project["edl"].read_text(encoding="utf-8"))


def _seed_effect(project, client, shot: str, times: list[float]) -> str:
    """An accepted effect on `shot` with an event at each of `times`, written the way
    Accept writes it."""
    from roughcut import fx
    edl = _on_disk(project)
    e = fx.validate_effect({
        "id": "fx_seed0001", "shot": shot, "name": "hit", "status": "accepted",
        "events": [{"t": t, "x": 0.5, "y": 0.5} for t in times],
        "overlay": {"duration": 0.3, "size": 0.1,
                    "shapes": [{"type": "ring", "r": 1.0, "width": 0.2}]},
    }, edl["segments"])
    edl["effects"] = [e]
    project["edl"].write_text(json.dumps(edl, indent=1), encoding="utf-8")
    return e["id"]


def test_preview_shows_the_cut_and_writes_nothing(client, project):
    import server
    before = project["edl"].read_text(encoding="utf-8")
    a = _segments(client)[0]["id"]
    r = client.post("/api/edits/preview", json={"ops": [
        {"op": "split", "shot": a, "at": 2.0},
        {"op": "generate", "kind": "black", "seconds": 1.0, "before": a}]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d) >= {"segments", "id_map", "generated", "changed", "words"}
    assert [s["id"][:4] for s in d["segments"]] == ["new:", a[:4], "new:", _segments(client)[1]["id"][:4]]
    assert d["id_map"] == {"new:1": None, "new:2": None}
    assert d["words"] == [f"split shot CLIP_A.MP4 at 2.00s", "a black clip of 1s"]
    assert project["edl"].read_text(encoding="utf-8") == before
    assert not (server.generated_dir() / d["generated"][0]["clip"]).exists()


def test_a_bad_op_is_a_400_with_the_sentence(client, project):
    before = project["edl"].read_text(encoding="utf-8")
    a = _segments(client)[0]["id"]
    for ops, words in (([{"op": "split", "shot": a, "at": 9.0}], "not inside the shot"),
                       ([{"op": "extend", "shot": "zz", "out": 1}], "not in the cut"),
                       ([], "1–24 operations"),
                       ([{"op": "warp", "shot": a}], "unknown op")):
        for route in ("/api/edits/preview", "/api/edits/apply"):
            r = client.post(route, json={"ops": ops})
            assert r.status_code == 400, (route, ops, r.text)
            assert words in r.json()["detail"]
    assert project["edl"].read_text(encoding="utf-8") == before


def test_apply_mints_ids_makes_the_file_and_writes_once(client, project):
    import server
    segs = _segments(client)
    a = segs[0]["id"]
    r = client.post("/api/edits/apply", json={"ops": [
        {"op": "freeze", "shot": a, "at": 2.0, "seconds": 1.0},
        {"op": "speed", "shot": "new:1", "rate": 0.5}]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d["id_map"]) == {"new:1", "new:2"}
    assert all(v and not v.startswith("new:") for v in d["id_map"].values())
    ids = [s["id"] for s in d["segments"]]
    assert ids == [a, d["id_map"]["new:2"], d["id_map"]["new:1"], segs[1]["id"]]
    assert d["changed"] == [d["id_map"]["new:2"], d["id_map"]["new:1"]]
    still = d["segments"][1]
    assert still["clip"] == edits.generated_name("still", 1.0, from_clip="CLIP_A.MP4", at=2.0)
    assert (server.generated_dir() / still["clip"]).exists()
    assert d["segments"][2]["speed"] == 0.5
    assert d["before"] == segs                               # what undo takes back
    assert d["words"] == ["freeze CLIP_A.MP4 at 2.00s for 1s", "CLIP_A.MP4 at 0.5× for the whole shot"]
    assert d["ops"][0]["op"] == "freeze"
    assert _on_disk(project)["segments"] == d["segments"]
    assert _segments(client) == d["segments"]
    clips = client.get("/api/project").json()["clips"]
    assert clips[still["clip"]]["used"] is True
    assert clips[still["clip"]]["summary"]["generated"] == "generated: still of CLIP_A.MP4 at 2.00 s, 1.0 s"


def test_apply_edits_is_the_module_function_accept_calls(client):
    import pytest
    import server
    a = _segments(client)[0]["id"]
    with pytest.raises(ValueError, match="not inside the shot"):
        server.apply_edits([{"op": "split", "shot": a, "at": 0.5}])
    out = server.apply_edits([{"op": "extend", "shot": a, "out": 1.0}])
    assert out["segments"][0]["out"] == 4.0 and out["before"][0]["out"] == 3.0
    assert _segments(client)[0]["out"] == 4.0


def test_a_split_shots_effects_follow_their_events(client, project):
    a = _segments(client)[0]["id"]                      # CLIP_A 1.0–3.0
    fx_id = _seed_effect(project, client, a, [1.5, 2.4])
    # a split at 2.0 leaves one event each side: the original keeps the effect, the
    # second piece gets a copy holding only its event
    r = client.post("/api/edits/apply", json={"ops": [{"op": "split", "shot": a, "at": 2.0}]})
    assert r.status_code == 200, r.text
    d = r.json()
    new = d["id_map"]["new:1"]
    effects = _on_disk(project)["effects"]
    assert [e["shot"] for e in effects] == [a, new]
    assert effects[0]["id"] == fx_id and [ev["t"] for ev in effects[0]["events"]] == [1.5, 2.4]
    assert effects[1]["id"] != fx_id and [ev["t"] for ev in effects[1]["events"]] == [2.4]
    assert d["before_effects"] == [effects[0]] and d["effects"] == effects
    # undo with the effects as they were: the copy is gone, the original is whole
    r = client.post("/api/edits/undo", json={"before": d["before"], "effects": d["before_effects"]})
    assert r.status_code == 200, r.text
    on_disk = _on_disk(project)
    assert [s["id"] for s in on_disk["segments"]] == [s["id"] for s in d["before"]]
    assert [e["id"] for e in on_disk["effects"]] == [fx_id]


def test_an_effect_moves_to_the_piece_that_holds_its_events(client, project):
    a = _segments(client)[0]["id"]
    fx_id = _seed_effect(project, client, a, [2.4, 2.8])
    r = client.post("/api/edits/apply", json={"ops": [{"op": "split", "shot": a, "at": 2.0}]})
    assert r.status_code == 200, r.text
    new = r.json()["id_map"]["new:1"]
    effects = _on_disk(project)["effects"]
    assert len(effects) == 1 and effects[0]["id"] == fx_id and effects[0]["shot"] == new
    # a removed shot takes its effect with it; undo without `effects` keeps what fits
    r = client.post("/api/edits/apply", json={"ops": [{"op": "remove", "shot": new}]})
    assert r.status_code == 200, r.text
    assert _on_disk(project)["effects"] == []
    r = client.post("/api/edits/undo", json={"before": r.json()["before"]})
    assert r.status_code == 200 and [s["id"] for s in _segments(client)] == [a, new, _segments(client)[2]["id"]]


def test_undo_validates_like_a_save(client, project):
    a = _segments(client)[0]["id"]
    r = client.post("/api/edits/apply", json={"ops": [{"op": "speed", "shot": a, "rate": 2}]})
    before = r.json()["before"]
    assert _segments(client)[0]["speed"] == 2.0
    bad = [dict(before[0], speed=99)]
    assert client.post("/api/edits/undo", json={"before": bad}).status_code == 400
    assert client.post("/api/edits/undo", json={"before": []}).status_code == 400
    assert client.post("/api/edits/undo", json={}).status_code == 400
    assert _segments(client)[0]["speed"] == 2.0                # nothing written by a refusal
    r = client.post("/api/edits/undo", json={"before": before})
    assert r.status_code == 200 and r.json()["segments"] == before
    assert _segments(client) == before and "speed" not in _segments(client)[0]


def test_apply_makes_the_file_before_it_writes(client, project, monkeypatch):
    import server
    before = project["edl"].read_text(encoding="utf-8")

    def broken(spec):
        raise RuntimeError("ffmpeg said no")

    monkeypatch.setattr(server, "materialise_generated", broken)
    r = client.post("/api/edits/apply", json={"ops": [{"op": "generate", "kind": "black"}]})
    assert r.status_code == 400 and "ffmpeg said no" in r.json()["detail"]
    assert project["edl"].read_text(encoding="utf-8") == before
