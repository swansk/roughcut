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
               workdir=None, segments=None, clips=None, window=None):
        e = json.loads(json.dumps(HIT))
        e["shot"] = seg["id"]
        e["clip"] = seg["clip"]
        e["note"] = note
        if window is not None:      # what the real design does: only hits inside
            e["events"] = [ev for ev in e["events"] if window[0] <= ev["t"] <= window[1]] or e["events"][:1]
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
    # removed, not gone: listed as such for Restore; Discard is what deletes for good
    lst = client.get("/api/fx").json()["effects"]
    assert [(x["id"], x["status"]) for x in lst] == [(fx_id, "removed")]
    assert client.post("/api/fx/remove", json={"id": fx_id}).status_code == 404
    assert client.post("/api/fx/discard", json={"id": fx_id}).status_code == 200
    assert client.get("/api/fx").json()["effects"] == []


def test_remove_keeps_the_effect_for_restore_and_accept_keeps_the_previous_for_revert(stubbed, client):
    """Karl: effects applied long term must be reversible, and more can go on top."""
    project = stubbed
    shot = _seed(project, client)
    job = client.post("/api/fx/design", json={"shot": shot, "note": "hit markers"}).json()["job"]
    a = _wait(client, job)["result"]["id"]
    job = client.post("/api/fx/design", json={"shot": shot, "note": "a flash too"}).json()["job"]
    b = _wait(client, job)["result"]["id"]
    assert client.post("/api/fx/accept", json={"id": a}).status_code == 200
    assert client.post("/api/fx/accept", json={"id": b}).status_code == 200
    edl = json.loads(project["edl"].read_text(encoding="utf-8"))
    assert [e["id"] for e in edl["effects"]] == [a, b]          # two on one shot, in order
    # remove keeps it: listed as removed, out of the EDL, files intact
    r = client.post("/api/fx/remove", json={"id": a})
    assert r.status_code == 200 and r.json()["effect"]["status"] == "removed"
    edl = json.loads(project["edl"].read_text(encoding="utf-8"))
    assert [e["id"] for e in edl["effects"]] == [b]
    lst = {e["id"]: e for e in client.get("/api/fx").json()["effects"]}
    assert lst[a]["status"] == "removed" and lst[a]["sound_url"]
    # restore puts it back as it was
    assert client.post("/api/fx/restore", json={"id": a}).status_code == 200
    edl = json.loads(project["edl"].read_text(encoding="utf-8"))
    assert sorted(e["id"] for e in edl["effects"]) == sorted([a, b])
    assert client.post("/api/fx/restore", json={"id": a}).status_code == 404
    # a revision accepted over an accepted one keeps the old version; revert restores it
    job = client.post("/api/fx/revise", json={"id": a, "note": "make them red"}).json()["job"]
    _wait(client, job)
    assert client.post("/api/fx/accept", json={"id": a}).status_code == 200
    e = next(x for x in client.get("/api/fx").json()["effects"] if x["id"] == a)
    assert {s["color"] for s in e["overlay"]["shapes"]} == {"#ff0000"}
    assert len(e["previous"]) == 1 and e["previous"][0]["overlay"]["shapes"][0]["color"] == "#ffffff"
    assert client.post("/api/fx/revert", json={"id": a}).status_code == 200
    e = next(x for x in client.get("/api/fx").json()["effects"] if x["id"] == a)
    assert {s["color"] for s in e["overlay"]["shapes"]} == {"#ffffff"} and e["previous"] == []
    assert client.post("/api/fx/revert", json={"id": a}).status_code == 400
    # the peaks the design starts from are readable
    r = client.get(f"/api/fx/peaks?shot={shot}")
    assert r.status_code == 200 and isinstance(r.json()["peaks"], list)


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


def test_the_window_is_the_humans_and_confines_the_hits(stubbed, client):
    """Karl, after the first live effect: the markers went across the whole shot; the
    rocks were only at the end. The window is his to give, and it travels with the
    effect."""
    shot = _seed(stubbed, client)
    job = client.post("/api/fx/design", json={"shot": shot, "note": "hit markers", "window": [2.0, 3.0]}).json()["job"]
    fx_id = _wait(client, job)["result"]["id"]
    e = next(x for x in client.get("/api/fx").json()["effects"] if x["id"] == fx_id)
    assert e["window"] == [2.0, 3.0]
    assert [ev["t"] for ev in e["events"]] == [2.4]
    # a window outside the shot, or backwards, is refused with a sentence
    r = client.post("/api/fx/design", json={"shot": shot, "note": "hit", "window": [0.5, 2.0]})
    assert r.status_code == 400 and "not inside the shot" in r.json()["detail"]
    r = client.post("/api/fx/design", json={"shot": shot, "note": "hit", "window": [2.5, 2.0]})
    assert r.status_code == 400
    r = client.post("/api/fx/design", json={"shot": shot, "note": "hit", "window": "later"})
    assert r.status_code == 400
    # the window survives an accept and a nudge
    assert client.post("/api/fx/accept", json={"id": fx_id}).status_code == 200
    r = client.put(f"/api/fx/{fx_id}", json={"events": [{"t": 2.5, "x": 0.4, "y": 0.6}]})
    assert r.status_code == 200 and r.json()["window"] == [2.0, 3.0]


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


# ---------------------------------------------------------------- the free check, by itself
# INTAKE M16 I16.5: the Verify button goes; the check runs after every design, revise
# and nudge, queued behind any render, and never a model call.

def _wait_verify(client, fx_id: str, pred=lambda e: bool(e.get("verify")), timeout: float = 20.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        e = next((x for x in client.get("/api/fx").json()["effects"] if x["id"] == fx_id), None)
        if e is not None and pred(e):
            return e
        time.sleep(0.05)
    raise AssertionError(f"{fx_id} was never checked")


def _verify_jobs(fx_id: str) -> list:
    import server
    return [j for j in server.FX.values() if j.get("fx_kind") == "verify" and j.get("fx_id") == fx_id]


def test_a_design_and_a_revise_are_checked_without_a_click(stubbed, client):
    shot = _seed(stubbed, client)
    fx_id = _wait(client, client.post("/api/fx/design", json={"shot": shot, "note": "hit markers"}).json()["job"])["result"]["id"]
    e = _wait_verify(client, fx_id)
    assert e["verify"]["ok"] is True and e["proof_url"].endswith("proof.mp4")
    assert _verify_jobs(fx_id)[0]["label"] == "Checking hit markers"
    _wait(client, client.post("/api/fx/revise", json={"id": fx_id, "note": "make them red"}).json()["job"])
    e = _wait_verify(client, fx_id, lambda e: bool(e.get("verify"))
                     and {s["color"] for s in e["overlay"]["shapes"]} == {"#ff0000"})
    assert e["verify"]["ok"] is True


def test_a_nudge_clears_the_checklist_and_is_checked_again(stubbed, client):
    shot = _seed(stubbed, client)
    fx_id = _wait(client, client.post("/api/fx/design", json={"shot": shot, "note": "hit markers"}).json()["job"])["result"]["id"]
    _wait_verify(client, fx_id)
    r = client.put(f"/api/fx/{fx_id}", json={"events": [{"t": 1.5 + fx.NUDGE_S, "x": 0.5, "y": 0.7}]})
    assert r.status_code == 200 and "verify" not in r.json()
    e = _wait_verify(client, fx_id)
    assert e["events"][0]["t"] == pytest.approx(1.5 + fx.NUDGE_S, abs=1e-3) and e["verify"]["ok"] is True


def test_the_check_waits_behind_a_render_and_one_waits_per_effect(stubbed, client, monkeypatch):
    """A 4K encode owns the cores: the check waits for it, saying so, and a second
    change while it waits does not queue a second proof — the waiting one reads the
    effect when it starts. The Verify endpoint (tools, tests) joins the same queue."""
    import server
    from roughcut import progress
    monkeypatch.setattr(server, "FX_VERIFY_POLL_S", 0.05)
    shot = _seed(stubbed, client)
    server.RENDERS["rtest001"] = progress.Job("render", "Rendering — preview", id="rtest001")
    try:
        fx_id = _wait(client, client.post("/api/fx/design", json={"shot": shot, "note": "hit markers"}).json()["job"])["result"]["id"]
        time.sleep(0.3)
        jobs = _verify_jobs(fx_id)
        assert len(jobs) == 1 and jobs[0]["state"] == "running"
        assert jobs[0]["detail"] == "waiting for the render to finish"
        assert "verify" not in next(x for x in client.get("/api/fx").json()["effects"] if x["id"] == fx_id)
        assert client.put(f"/api/fx/{fx_id}", json={"events": [{"t": 2.0, "x": 0.4, "y": 0.6}]}).status_code == 200
        assert client.post("/api/fx/verify", json={"id": fx_id}).json()["job"] == jobs[0]["id"]
        assert len(_verify_jobs(fx_id)) == 1
    finally:
        server.RENDERS["rtest001"].finish("done")
    e = _wait_verify(client, fx_id)
    assert [ev["t"] for ev in e["events"]] == [2.0]            # the change it waited through
    assert e["verify"]["ok"] is True
    server.RENDERS.pop("rtest001", None)


def test_a_check_that_outlives_its_effect_does_not_write_it_back(stubbed, client, monkeypatch):
    """A nudge while the proof renders: the old checklist is dropped (writing it back
    would also put the old events back), and the nudge's own check is what lands."""
    import threading
    import server
    gate, seen = threading.Event(), []
    real = fx.verify

    def slow(effect, seg, **kw):
        seen.append([ev["t"] for ev in effect["events"]])
        if len(seen) == 1:
            gate.wait(10)
        return real(effect, seg, **kw)
    monkeypatch.setattr(fx, "verify", slow)
    shot = _seed(stubbed, client)
    fx_id = _wait(client, client.post("/api/fx/design", json={"shot": shot, "note": "hit markers"}).json()["job"])["result"]["id"]
    t0 = time.time()
    while not seen and time.time() - t0 < 10:
        time.sleep(0.02)
    assert seen == [[1.5, 2.4]]
    assert client.put(f"/api/fx/{fx_id}", json={"events": [{"t": 2.2, "x": 0.4, "y": 0.6}]}).status_code == 200
    gate.set()
    e = _wait_verify(client, fx_id)
    assert [ev["t"] for ev in e["events"]] == [2.2] and seen[-1] == [2.2]
    first = next(j for j in _verify_jobs(fx_id) if j["state"] == "done" and j["result"]["ok"] is None)
    assert "changed meanwhile" in first["detail"]
    assert server.FX_VERIFY_WAITING.get(fx_id) is None


# ---------------------------------------------------------------- the sentence, in film time
# INTAKE M16 I16.5 (1): a proposal says in one sentence what changes in the film, in
# film time — not "CLIP_08.MP4 at 0.4× from 170.85 to 171.50s" (clip seconds that read
# as film time on a 3:09 film).

def _propose(fx_id: str, shot: str, ops: list, **extra) -> None:
    import server
    e = {"id": fx_id, "shot": shot, "clip": extra.pop("clip", "CLIP_B.MP4"), "name": "an edit",
         "why": "", "events": [{"t": 1.2, "x": 0.5, "y": 0.5}], "status": "proposed",
         "edits": ops, "created": "2026-09-20T22:42:23", **extra}
    fx.save(server.fx_home(), e)


def test_an_edit_proposal_says_what_changes_in_film_time(stubbed, client):
    _seed(stubbed, client)
    a, b = [s["id"] for s in client.get("/api/project").json()["segments"]]   # 1.0–3.0, 0.0–2.0
    _propose("fx_says0001", b, [{"op": "speed", "shot": b, "rate": 0.5, "from": 1.0, "to": 1.5}])
    _propose("fx_says0002", "new:1", [{"op": "generate", "kind": "black", "seconds": 3, "before": a}],
             clip="")
    _propose("fx_says0003", b, [{"op": "speed", "shot": "gone", "rate": 0.5}])
    lst = {e["id"]: e for e in client.get("/api/fx").json()["effects"]}
    # shot 2 starts at 0:02 of the film; 0.5 s of it at 0.5× is 0.5 s more, from 0:03
    assert lst["fx_says0001"]["says"] == "Slows 0.50 s to 0.5×. Shot 2 gets 0.5 s longer, at 0:03."
    assert lst["fx_says0001"]["film"] == {"n": 2, "start": 2.0, "in": 0.0, "speed": 1.0}
    assert lst["fx_says0002"]["says"] == "Adds a 3 s black slide before shot 1. The film gets 3.0 s longer, at 0:00."
    # the shot the edits will create has a place in the film already
    assert lst["fx_says0002"]["film"]["n"] == 1 and lst["fx_says0002"]["film"]["start"] == 0.0
    assert lst["fx_says0003"]["says"] == "This change no longer fits the cut."
    # an overlay effect has no sentence of its own (the card says its why)
    fx_id = _wait(client, client.post("/api/fx/design", json={"shot": a, "note": "hit markers"}).json()["job"])["result"]["id"]
    e = next(x for x in client.get("/api/fx").json()["effects"] if x["id"] == fx_id)
    assert "says" not in e and e["film"] == {"n": 1, "start": 0.0, "in": 1.0, "speed": 1.0}


def test_accepting_an_edit_only_proposal_changes_the_cut_once(stubbed, client, project):
    """The slow motion has nothing to draw or hear. Accept answered 500 *after* the
    edits had changed the cut — validate_effect refused an effect with no overlay, no
    sound and no edits left — and the proposal stayed waiting, to be applied twice. Now
    the cut changes once, the proposal leaves every list (the record stays on disk with
    the sentence and `applied.before` for an undo), and nothing is put in `effects`."""
    import server
    _seed(stubbed, client)
    a, b = [s["id"] for s in client.get("/api/project").json()["segments"]]
    _propose("fx_slow0001", b, [{"op": "speed", "shot": b, "rate": 0.5, "from": 1.0, "to": 1.5}])
    r = client.post("/api/fx/accept", json={"id": "fx_slow0001"})
    assert r.status_code == 200, r.text
    got = r.json()["effect"]
    assert got["status"] == "applied" and got["says"].startswith("Slows 0.50 s to 0.5×")
    assert [s["id"] for s in got["applied"]["before"]] == [a, b]
    edl = json.loads(project["edl"].read_text(encoding="utf-8"))
    assert [s.get("speed") for s in edl["segments"]] == [None, None, 0.5, None]
    assert not edl.get("effects")
    assert client.get("/api/fx").json()["effects"] == []
    assert json.loads((server.fx_home() / "fx_slow0001.json").read_text())["status"] == "applied"
