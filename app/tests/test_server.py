"""Cut board API tests.

Weighted toward the things that actually broke while building it — proxy files
served mid-write, rotation metadata surviving into output, seeking that silently
degrades to streaming-from-zero — rather than toward coverage for its own sake.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest


def _duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return float(r.stdout.strip().strip(","))


# ------------------------------------------------------------------ project io

def test_project_loads(client):
    p = client.get("/api/project").json()
    assert p["title"] == "test cut"
    assert len(p["segments"]) == 2
    assert p["segments"][0]["clip"] == "CLIP_A.MP4"


def test_all_clips_offered_not_just_used(client):
    """The library exists so you can add a shot you forgot; a clip absent from the
    timeline must still be reachable."""
    p = client.get("/api/project").json()
    assert set(p["clips"]) == {"CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"}
    assert p["clips"]["CLIP_C.MP4"]["used"] is False
    assert p["clips"]["CLIP_A.MP4"]["used"] is True


def test_transcript_and_candidates_exposed(client):
    clip = client.get("/api/project").json()["clips"]["CLIP_A.MP4"]
    assert [u["text"] for u in clip["transcript"]] == ["hello there", "how are you",
                                                       "goodbye"]
    assert clip["candidates"][0]["why"].startswith("speech")


def test_save_round_trips_to_disk(client, project):
    body = {"segments": [{"clip": "CLIP_C.MP4", "in": 1.5, "out": 4.25,
                          "why": "new one"}],
            "story": "the milk is the running joke"}
    assert client.put("/api/project", json=body).json()["saved"] == 1
    on_disk = json.loads(project["edl"].read_text(encoding="utf-8"))
    assert on_disk["segments"] == [{"clip": "CLIP_C.MP4", "in": 1.5, "out": 4.25,
                                    "why": "new one"}]
    assert on_disk["story"] == "the milk is the running joke"
    # and the reload shows what was written, since the file is the source of truth
    assert client.get("/api/project").json()["segments"][0]["clip"] == "CLIP_C.MP4"


def test_save_rejects_inverted_segment(client, project):
    before = project["edl"].read_text(encoding="utf-8")
    r = client.put("/api/project", json={
        "segments": [{"clip": "CLIP_A.MP4", "in": 3.0, "out": 2.0}], "story": ""})
    assert r.status_code == 400
    assert project["edl"].read_text(encoding="utf-8") == before, "must not partially write"


def test_save_preserves_unknown_edl_keys(client, project):
    """The EDL is shared with assemble.py and edl_snap.py; the app must not eat
    fields it does not understand."""
    client.put("/api/project", json={"segments": [
        {"clip": "CLIP_A.MP4", "in": 0.0, "out": 1.0}], "story": "x"})
    on_disk = json.loads(project["edl"].read_text(encoding="utf-8"))
    assert on_disk["orient"] == "none"
    assert on_disk["variant"] == "T"
    assert on_disk["target_s"] == [5, 20]


# ------------------------------------------------------------------ snap

def test_snap_extends_to_conversational_boundaries(client):
    """in=1.0 opens inside 'hello there' (0.5-2.0); out=3.0 lands inside
    'how are you' (2.4-4.0); 'goodbye' then starts within the 1.2s gap."""
    r = client.post("/api/snap", json={"segments": [
        {"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "x"}]})
    seg = r.json()["segments"][0]
    assert seg["in"] == pytest.approx(0.25, abs=0.01)     # 0.5 - PAD_HEAD
    assert seg["out"] == pytest.approx(6.0, abs=0.06)     # through 'goodbye', clip-capped
    assert seg["snapped_from"] == [1.0, 3.0]


def test_snap_is_a_proposal_not_a_write(client, project):
    before = project["edl"].read_text(encoding="utf-8")
    client.post("/api/snap", json={"segments": [
        {"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0}]})
    assert project["edl"].read_text(encoding="utf-8") == before


def test_snap_leaves_clean_boundaries_alone(client):
    """A segment already sitting in silence between utterances should not grow."""
    r = client.post("/api/snap", json={"segments": [
        {"clip": "CLIP_A.MP4", "in": 4.2, "out": 4.6}]})
    seg = r.json()["segments"][0]
    assert (seg["in"], seg["out"]) == (4.2, 4.6)
    assert "snapped_from" not in seg


# ------------------------------------------------------------------ media

def test_media_range_returns_206(client):
    r = client.get("/media/proxy/CLIP_A.mp4", headers={"Range": "bytes=0-1023"})
    assert r.status_code == 206
    assert r.headers["content-range"].startswith("bytes 0-1023/")
    assert len(r.content) == 1024


def test_media_plain_get_advertises_ranges(client):
    """Without accept-ranges the browser will not even attempt to seek."""
    r = client.get("/media/proxy/CLIP_A.mp4")
    assert r.status_code == 200
    assert r.headers["accept-ranges"] == "bytes"


def test_media_range_past_end_is_clamped(client):
    full = len(client.get("/media/proxy/CLIP_A.mp4").content)
    r = client.get("/media/proxy/CLIP_A.mp4",
                   headers={"Range": f"bytes=0-{full + 10_000}"})
    assert r.status_code == 206
    assert len(r.content) == full


def test_media_unknown_proxy_404s(client):
    assert client.get("/media/proxy/NOPE.mp4").status_code == 404


def test_media_path_traversal_is_contained(client):
    """Only the basename is used, so a traversal attempt resolves inside proxy_dir."""
    r = client.get("/media/proxy/..%2F..%2Fetc%2Fpasswd")
    assert r.status_code == 404


# ------------------------------------------------------------------ proxies

def test_proxies_built_and_no_partials_left(client, project):
    pdir = project["work"] / "proxies"
    assert {p.name for p in pdir.glob("*.mp4")} >= {"CLIP_A.mp4", "CLIP_B.mp4",
                                                    "CLIP_C.mp4"}
    assert list(pdir.glob("*.part.mp4")) == [], "half-written proxy left at final path"


def test_proxy_carries_no_rotation_metadata(client, project):
    """A proxy that plays sideways in the UI is worse than no proxy."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream_side_data=rotation", "-of", "csv=p=0",
         str(project["work"] / "proxies" / "CLIP_A.mp4")],
        capture_output=True, text=True).stdout.strip().strip(",")
    assert out == ""


# ------------------------------------------------------------------ render

def test_render_produces_a_file_of_the_planned_length(client, project):
    segs = [{"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.5, "why": "a"},
            {"clip": "CLIP_B.MP4", "in": 1.0, "out": 2.0, "why": "b"}]
    job = client.post("/api/render", json={"segments": segs}).json()["job"]
    for _ in range(120):
        status = client.get(f"/api/render/{job}").json()
        if status["state"] != "running":
            break
        time.sleep(1)
    assert status["state"] == "done", status["log"][-800:]
    out = Path(status["output"])
    assert out.exists()
    assert _duration(out) == pytest.approx(3.0, abs=0.35)
    # and it is fetchable over the same range-capable media route
    assert client.get(status["url"], headers={"Range": "bytes=0-99"}).status_code == 206


def test_render_status_404_for_unknown_job(client):
    assert client.get("/api/render/deadbeef").status_code == 404


# ------------------------------------------------------------------ static

def test_index_and_script_served(client):
    assert "Cut board" in client.get("/").text
    js = client.get("/app.js")
    assert js.status_code == 200
    assert "function render" in js.text
