"""The server's colour wiring (INTAKE M10): the per-clip colour files measured from the
proxies, `/api/colour`, `/api/lut/{id}`, and the `colour` block on save."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _ids(client):
    return [s["id"] for s in client.get("/api/project").json()["segments"]]


def test_colour_files_are_measured_from_the_proxies(client, project):
    import server
    for stem in project["stems"]:
        f = server.colour_file(f"{stem}.MP4")
        assert f.exists(), f
        d = json.loads(f.read_text(encoding="utf-8"))
        assert d["version"] == 1 and d["samples"] and "summary" in d
        assert d["probe"]["family"] in ("gopro", "iphone", "other")
        assert d["probe"]["hdr"] is None


def test_api_colour_resolves_every_shot_in_film_order(client):
    r = client.get("/api/colour")
    assert r.status_code == 200
    d = r.json()
    assert [l["name"] for l in d["looks"]][:3] == ["crisp", "alpine", "filmic"]
    ids = _ids(client)
    assert [s["id"] for s in d["shots"]] == ids
    for s in d["shots"]:
        assert set(s) >= {"balance", "match", "look", "strength", "family", "identity", "witness"}
        assert s["witness"]["n"] >= 1
    assert all(v["measured"] for v in d["clips"].values())


def test_lut_endpoint_returns_a_table_in_cube_order(client):
    sid = _ids(client)[0]
    r = client.get(f"/api/lut/{sid}?n=5")
    assert r.status_code == 200
    d = r.json()
    assert d["size"] == 5 and len(d["table"]) == 5 ** 3 * 3
    # the second entry is the grid's second red step, whatever the grade did to it
    assert d["table"][0:3] != d["table"][3:6] or d["identity"]
    assert client.get("/api/lut/no-such-shot").status_code == 404


def test_save_validates_and_persists_the_colour_block(client, project):
    p = client.get("/api/project").json()
    body = {"segments": p["segments"], "story": p.get("story", "")}
    bad = client.put("/api/project", json={**body, "colour": {"look": "teal-orange-9000"}})
    assert bad.status_code == 400 and "colour" in bad.json()["detail"]
    bad = client.put("/api/project", json={**body, "colour": {"mode": "vivid"}})
    assert bad.status_code == 400
    sid = p["segments"][1]["id"]
    ok = client.put("/api/project", json={**body, "colour": {
        "mode": "auto", "look": "alpine", "strength": 0.6, "reference": p["segments"][0]["id"],
        "shots": {sid: {"look": "filmic", "balance": {"gain": [1.5, 1, 0.5], "exposure": 1.2}}}}})
    assert ok.status_code == 200
    saved = json.loads(project["edl"].read_text(encoding="utf-8"))["colour"]
    assert saved["look"] == "alpine" and saved["strength"] == 0.6
    assert saved["shots"][sid]["look"] == "filmic"
    assert saved["shots"][sid]["balance"]["gain"] == [1.15, 1.0, 0.85]   # clamped
    c = client.get("/api/colour").json()
    by_id = {s["id"]: s for s in c["shots"]}
    assert by_id[sid]["look"] == "filmic" and by_id[sid]["balance"]["source"] == "hand"
    other = p["segments"][0]["id"]
    assert by_id[other]["look"] == "alpine" and by_id[other]["strength"] == 0.6
    lut = client.get(f"/api/lut/{sid}?n=3").json()
    assert lut["look"] == "filmic" and lut["identity"] is False
    # an explicit null removes the block
    gone = client.put("/api/project", json={**body, "colour": None})
    assert gone.status_code == 200
    assert "colour" not in json.loads(project["edl"].read_text(encoding="utf-8"))
    # a save that does not mention colour leaves it alone
    client.put("/api/project", json={**body, "colour": {"mode": "off"}})
    client.put("/api/project", json=body)
    assert json.loads(project["edl"].read_text(encoding="utf-8"))["colour"]["mode"] == "off"


def test_off_mode_makes_every_shot_the_identity(client):
    p = client.get("/api/project").json()
    body = {"segments": p["segments"], "story": p.get("story", ""), "colour": {"mode": "off"}}
    assert client.put("/api/project", json=body).status_code == 200
    c = client.get("/api/colour").json()
    assert c["film"]["mode"] == "off"
    assert all(s["identity"] for s in c["shots"])
    lut = client.get(f"/api/lut/{p['segments'][0]['id']}?n=3").json()
    assert lut["identity"] is True
    assert lut["table"][3:6] == pytest.approx([0.5, 0, 0], abs=1e-4)
