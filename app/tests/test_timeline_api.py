"""INTAKE M9 I9.0 — what the promoted timeline needs from the server: stable segment ids,
snap points for the magnet, and the module's static routes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_segments_get_ids_once_and_keep_them_through_saves(client, project):
    p = client.get("/api/project").json()
    ids = [s["id"] for s in p["segments"]]
    assert len(ids) == 2 and all(i.startswith("g") and len(i) == 11 for i in ids)
    # minted once: the EDL on disk now carries them, and a second read repeats them
    on_disk = json.loads(project["edl"].read_text(encoding="utf-8"))
    assert [s["id"] for s in on_disk["segments"]] == ids
    assert [s["id"] for s in client.get("/api/project").json()["segments"]] == ids

    # a reorder keeps each shot's id with the shot; a trim too
    segs = p["segments"]
    segs.reverse()
    segs[0]["in"] = round(segs[0]["in"] + 0.5, 2)
    r = client.put("/api/project", json={"segments": segs, "story": p.get("story", "")})
    assert r.status_code == 200, r.text
    after = client.get("/api/project").json()["segments"]
    assert [s["id"] for s in after] == list(reversed(ids))
    assert after[0]["in"] == segs[0]["in"]

    # a copy of a shot (same id twice) and a shot without an id get fresh ids
    dup = [dict(after[0]), dict(after[0]), {"clip": "CLIP_C.MP4", "in": 0.0, "out": 1.0}]
    client.put("/api/project", json={"segments": dup})
    final = [s["id"] for s in client.get("/api/project").json()["segments"]]
    assert final[0] == after[0]["id"] and len(set(final)) == 3
    # the bin's `used_in` names shots by those ids
    sel = client.get("/api/selects").json()
    assert all(all(u.startswith("g") for u in s["used_in"]) for s in sel["selects"])


def test_snap_points_come_from_the_sidecar(client):
    r = client.get("/api/snaps/CLIP_A.MP4")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["clip"] == "CLIP_A.MP4" and d["duration"] == 6.0
    assert d["pads"] == {"head": 0.25, "tail": 0.45}
    first = d["sentences"][0]
    assert first["start"] == 0.5 and first["cut_in"] == 0.25
    assert first["cut_out"] == round(first["end"] + 0.45, 2)
    assert first["text"] and 0.5 in d["words"]
    assert d["words"] == sorted(set(d["words"]))
    assert isinstance(d["onsets"], list)          # a flat synthetic track has no peaks
    assert client.get("/api/snaps/NOPE.MP4").status_code == 404


def test_onset_peaks_are_local_maxima_above_the_noise_and_spaced_out():
    import server

    track = [0.1] * 100
    for i in (20, 21, 60, 62):                  # 21 is a shoulder of 20; 62 within 0.5 s of 60
        track[i] = 0.9 if i in (20, 60) else 0.8
    peaks = server.onset_peaks({"tracks": {"onset": track}, "frame_hz": 10})
    assert peaks == [2.0, 6.0]


def test_the_timeline_files_are_served_by_name_only(client):
    assert client.get("/timeline/nope.js").status_code == 404
    r = client.get("/timeline/timeline.js")
    assert r.status_code in (200, 404)            # 404 = not built yet, by name
    if r.status_code == 200:
        assert r.headers["content-type"].startswith("application/javascript")
