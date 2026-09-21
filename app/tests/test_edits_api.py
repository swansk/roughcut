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
