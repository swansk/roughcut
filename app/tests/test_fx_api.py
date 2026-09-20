"""INTAKE M12 — the /api/fx loop, driven through the API with the model and the
renderer stubbed.

What is under test here is the server's part of the effects feature: a design job
that writes a *proposal* to disk and never the EDL; the list that merges proposals
and accepted effects; the human's edits validated like everything else; accept moving
an effect into the EDL's `effects` and remove taking it out; the files an effect owns;
the price on the button; and the project PUT re-validating `effects`. The model
(`fx.design` / `fx.revise`), the synth and the proof render are monkeypatched so the
loop runs in milliseconds; the real ones are lane fx-core's and tested in `test_fx.py`
and `test_fx_render.py`.
"""

from __future__ import annotations

import json
import sys
import time
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import fx  # noqa: E402


HIT = {
    "name": "hit markers",
    "why": "the two impacts the onset track found",
    "events": [{"t": 1.5, "x": 0.5, "y": 0.7}, {"t": 2.4, "x": 0.55, "y": 0.72}],
    "overlay": {"duration": 0.35, "size": 0.12,
                "shapes": [{"type": "line", "from": [-1, -1], "to": [-0.3, -0.3], "width": 0.1},
                           {"type": "line", "from": [1, -1], "to": [0.3, -0.3], "width": 0.1},
                           {"type": "line", "from": [-1, 1], "to": [-0.3, 0.3], "width": 0.1},
                           {"type": "line", "from": [1, 1], "to": [0.3, 0.3], "width": 0.1}],
                "anim": {"scale": [[0, 1.4], [0.06, 1.0]], "opacity": [[0, 1], [0.23, 1], [0.35, 0]]},
                "flash": {"color": "#ff0000", "opacity": 0.15, "duration": 0.08}},
    "sound": {"duration": 0.18, "gain_db": -6,
              "layers": [{"type": "tone", "freq": 1800, "wave": "square", "decay": 0.06},
                         {"type": "noise", "color": "white", "hp": 1500, "decay": 0.09}]},
}


def _silent_wav(path: Path, seconds: float = 0.2, sr: int = 48000) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * 2 * int(sr * seconds))
    return path


@pytest.fixture
def stubbed(monkeypatch, project):
    """The model designs HIT for any note (a revise note 'red' turns it red), the
    synth writes silence, the proof is the base copied, verify is the pure checks."""
    def design(note, seg, clip, sidecar, *, reference=None, place=False, proxy=None,
               workdir=None, segments=None, clips=None):
        e = json.loads(json.dumps(HIT))
        e["shot"] = seg["id"]
        e["clip"] = seg["clip"]
        e["note"] = note
        if reference and reference.get("marks"):
            e["events"] = [{"t": float(reference.get("t") or 1.5), "x": m[0], "y": m[1]}
                           for m in reference["marks"]]
        if workdir:
            Path(workdir).mkdir(parents=True, exist_ok=True)
            (Path(workdir) / "strip.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        e["created"] = "2026-09-20T12:00:00"
        e["history"] = [{"note": note, "at": e["created"]}]
        return fx.validate_effect(e, segments or [seg], clips)

    def revise(effect, note, segments, clips=None):
        e = json.loads(json.dumps(effect))
        if "red" in note:
            for s in e["overlay"]["shapes"]:
                s["color"] = "#ff0000"
        e.setdefault("history", []).append({"note": note, "at": "2026-09-20T12:01:00"})
        return fx.validate_effect(e, segments, clips)

    def synth(sound, out, *, sr=48000):
        return _silent_wav(Path(out), sound["duration"], sr)

    def part_graph(effects, w, h, fps, workdir, *, vin="vbase", ain="abase"):
        return [], "", vin, ain

    def verify(effect, seg, *, onset=None, hz=10, part=None, base=None, impact=True):
        checks = [{"key": "in_shot", "label": "every hit inside the shot", "ok": True, "detail": ""},
                  {"key": "audio_landed", "label": "the sound is in the proof",
                   "ok": part is not None and Path(part).exists(), "detail": str(part)}]
        return {"ok": all(c["ok"] for c in checks), "at": "2026-09-20T12:02:00", "checks": checks}

    monkeypatch.setattr(fx, "design", design)
    monkeypatch.setattr(fx, "revise", revise)
    monkeypatch.setattr(fx, "synth_sound", synth)
    monkeypatch.setattr(fx, "part_graph", part_graph)
    monkeypatch.setattr(fx, "verify", verify)
    return project


def _seed(project, client) -> str:
    edl = json.loads(project["edl"].read_text(encoding="utf-8"))
    edl["segments"] = [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
                       {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}]
    edl.pop("effects", None)
    project["edl"].write_text(json.dumps(edl, indent=1), encoding="utf-8")
    # ids are minted on open: reconfigure reads the file and stamps them
    import server
    server.configure(project["edl"], project["footage"], project["sidecars"],
                     project["work"], proxies=False, assets=project["assets"])
    # the project fixture is one work dir for the session: proposals from an earlier
    # test would still be on disk
    import shutil
    shutil.rmtree(server.fx_home(), ignore_errors=True)
    segs = client.get("/api/project").json()["segments"]
    return segs[0]["id"]


def _wait(client, job: str, timeout: float = 20.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        snap = client.get(f"/api/job/{job}").json()
        if snap["state"] in ("done", "failed"):
            return snap
        time.sleep(0.05)
    raise AssertionError(f"job {job} did not finish")


def test_design_makes_a_proposal_on_disk_and_never_in_the_edl(stubbed, client):
    project = stubbed
    shot = _seed(project, client)
    r = client.post("/api/fx/design", json={"shot": shot, "note": "hit markers where my skis hit the rocks, with the sound"})
    assert r.status_code == 200, r.text
    snap = _wait(client, r.json()["job"])
    assert snap["state"] == "done", snap
    fx_id = snap["result"]["id"]
    assert snap["kind"] == "fx"
    # on disk, as a proposal, with its sound rendered
    import server
    home = server.fx_home()
    e = json.loads((home / f"{fx_id}.json").read_text(encoding="utf-8"))
    assert e["status"] == "proposed" and e["shot"] == shot and len(e["events"]) == 2
    assert (home / fx_id / "sound.wav").exists()
    # never in the EDL
    edl = json.loads(project["edl"].read_text(encoding="utf-8"))
    assert not edl.get("effects")
    # the list merges proposals and accepted, with the files it owns as urls
    lst = client.get("/api/fx").json()["effects"]
    assert [x["id"] for x in lst] == [fx_id]
    assert lst[0]["sound_url"] == f"/api/fx/{fx_id}/sound.wav"
    assert client.get(lst[0]["sound_url"]).status_code == 200
    assert client.get(f"/api/fx/{fx_id}/proof.mp4").status_code == 404
    assert client.get(f"/api/fx/{fx_id}/anything").status_code == 404


def test_a_bad_design_request_is_a_400_with_a_sentence(stubbed, client):
    shot = _seed(stubbed, client)
    assert client.post("/api/fx/design", json={"shot": shot, "note": ""}).status_code == 400
    r = client.post("/api/fx/design", json={"shot": "nope", "note": "hit"})
    assert r.status_code == 400 and "no shot" in r.json()["detail"]


def test_the_human_edits_are_validated_and_clear_the_checklist(stubbed, client):
    shot = _seed(stubbed, client)
    job = client.post("/api/fx/design", json={"shot": shot, "note": "hit markers"}).json()["job"]
    fx_id = _wait(client, job)["result"]["id"]
    # verify, then nudge: the checklist goes
    v = _wait(client, client.post("/api/fx/verify", json={"id": fx_id}).json()["job"])
    assert v["state"] == "done" and v["result"]["ok"] is True
    e = next(x for x in client.get("/api/fx").json()["effects"] if x["id"] == fx_id)
    assert e["verify"]["ok"] is True and e["proof_url"].endswith("proof.mp4")
    assert client.get(e["proof_url"]).status_code == 200
    r = client.put(f"/api/fx/{fx_id}", json={"events": [{"t": 1.5 + fx.NUDGE_S, "x": 0.5, "y": 0.7}]})
    assert r.status_code == 200, r.text
    assert r.json()["events"][0]["t"] == pytest.approx(1.5 + fx.NUDGE_S, abs=1e-3)
    assert "verify" not in r.json()
    # a bad edit is refused with a sentence, and nothing changes
    r = client.put(f"/api/fx/{fx_id}", json={"events": [{"t": 1.5, "x": 2.0, "y": 0.7}]})
    assert r.status_code == 400 and "out of range" in r.json()["detail"]
    r = client.put(f"/api/fx/{fx_id}", json={"overlay": {"shapes": [{"type": "laser"}]}})
    assert r.status_code == 400 and "unknown shape" in r.json()["detail"]


def test_revise_iterates_the_proposal_and_keeps_its_id(stubbed, client):
    shot = _seed(stubbed, client)
    job = client.post("/api/fx/design", json={"shot": shot, "note": "hit markers"}).json()["job"]
    fx_id = _wait(client, job)["result"]["id"]
    job = client.post("/api/fx/revise", json={"id": fx_id, "note": "make them red"}).json()["job"]
    snap = _wait(client, job)
    assert snap["state"] == "done" and snap["result"]["id"] == fx_id
    e = next(x for x in client.get("/api/fx").json()["effects"] if x["id"] == fx_id)
    assert {s["color"] for s in e["overlay"]["shapes"]} == {"#ff0000"}
    assert [h["note"] for h in e["history"]] == ["hit markers", "make them red"]
    assert client.post("/api/fx/revise", json={"id": fx_id, "note": ""}).status_code == 400


def test_accept_moves_it_into_the_edl_and_remove_takes_it_out(stubbed, client):
    project = stubbed
    shot = _seed(project, client)
    job = client.post("/api/fx/design", json={"shot": shot, "note": "hit markers"}).json()["job"]
    fx_id = _wait(client, job)["result"]["id"]
    r = client.post("/api/fx/accept", json={"id": fx_id})
    assert r.status_code == 200 and r.json()["effect"]["status"] == "accepted"
    edl = json.loads(project["edl"].read_text(encoding="utf-8"))
    assert [e["id"] for e in edl["effects"]] == [fx_id]
    assert client.get("/api/project").json()["effects"][0]["id"] == fx_id
    # once accepted it is listed once, from the EDL
    assert [x["id"] for x in client.get("/api/fx").json()["effects"]] == [fx_id]
    # discard is for proposals; an accepted one is removed
    assert client.post("/api/fx/discard", json={"id": fx_id}).status_code == 400
    assert client.post("/api/fx/remove", json={"id": fx_id}).status_code == 200
    edl = json.loads(project["edl"].read_text(encoding="utf-8"))
    assert edl["effects"] == []
    assert client.get("/api/fx").json()["effects"] == []
    assert client.post("/api/fx/remove", json={"id": fx_id}).status_code == 404


def test_discard_drops_a_proposal_and_its_files(stubbed, client):
    shot = _seed(stubbed, client)
    job = client.post("/api/fx/design", json={"shot": shot, "note": "hit markers"}).json()["job"]
    fx_id = _wait(client, job)["result"]["id"]
    import server
    assert (server.fx_home() / fx_id / "sound.wav").exists()
    assert client.post("/api/fx/discard", json={"id": fx_id}).status_code == 200
    assert not (server.fx_home() / f"{fx_id}.json").exists()
    assert not (server.fx_home() / fx_id).exists()
    assert client.get("/api/fx").json()["effects"] == []


def test_a_drawn_reference_places_the_hits_and_is_kept(stubbed, client):
    shot = _seed(stubbed, client)
    png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    ref = {"t": 1.8, "goal": "the skis — markers here", "marks": [[0.42, 0.66], [0.61, 0.7]],
           "strokes": [[{"x": 0.4, "y": 0.65}, {"x": 0.44, "y": 0.67}]], "png": png}
    job = client.post("/api/fx/design", json={"shot": shot, "note": "hit markers", "reference": ref}).json()["job"]
    fx_id = _wait(client, job)["result"]["id"]
    e = next(x for x in client.get("/api/fx").json()["effects"] if x["id"] == fx_id)
    assert [(ev["x"], ev["y"]) for ev in e["events"]] == [(0.42, 0.66), (0.61, 0.7)]
    assert e["reference"]["goal"] == "the skis — markers here" and e["reference"]["marks"] == [[0.42, 0.66], [0.61, 0.7]]
    assert e["ref_url"] == f"/api/fx/{fx_id}/ref.png"
    assert client.get(e["ref_url"]).status_code == 200


def test_the_price_is_on_the_button_before_the_call(stubbed, client):
    shot = _seed(stubbed, client)
    r = client.get("/api/fx/price").json()
    assert r == {"usd": 0.05, "frames": 0}
    r = client.get(f"/api/fx/price?place=1&shot={shot}").json()
    assert r["frames"] >= 1 and r["usd"] >= 0.07


def test_the_project_put_revalidates_effects(stubbed, client):
    project = stubbed
    shot = _seed(project, client)
    segs = client.get("/api/project").json()["segments"]
    good = dict(HIT, shot=shot, clip="CLIP_A.MP4", id="fx_put00001", status="accepted")
    r = client.put("/api/project", json={"segments": segs, "effects": [good]})
    assert r.status_code == 200, r.text
    edl = json.loads(project["edl"].read_text(encoding="utf-8"))
    assert edl["effects"][0]["id"] == "fx_put00001" and edl["effects"][0]["overlay"]["size"] == 0.12
    bad = dict(good, shot="ghost")
    r = client.put("/api/project", json={"segments": segs, "effects": [bad]})
    assert r.status_code == 400 and "not in the cut" in r.json()["detail"]
    # a save without `effects` leaves them alone
    r = client.put("/api/project", json={"segments": segs, "story": "still here"})
    assert r.status_code == 200
    edl = json.loads(project["edl"].read_text(encoding="utf-8"))
    assert edl["effects"][0]["id"] == "fx_put00001"
