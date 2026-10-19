"""Wave 14 SERVERPERF (verdict 10, punch 1): a Designed Effects check never stalls the
server.

Verdict 10's "soft vignette" (8 stacked rings, a box of 1.6 frame widths, no animation)
was checked by drawing 240 identical 720p frames with PIL on a server thread — a
10 672-px canvas per shape per frame, ~5 s a frame, the GIL held ~250 ms at a time — for
12+ minutes at "0%", while a second identical check queued behind it. Here:

  * the renderer draws a frame once per run of identical frames, a static overlay is
    one PNG the part's graph loops (`-loop 1`), and only the frame's share of a sprite
    is drawn;
  * the check runs in a child process (`roughcut.fxcheck`) under the server's procscope,
    reports a real % with an ETA, and one check covers one change of an effect.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import fx  # noqa: E402

# verdict 10's vignette, as the designer wrote it (work/fx/…/fx_252c6f18.json)
VIGNETTE = {"duration": 2.0, "size": 1.6, "anim": {},
            "shapes": [{"type": "ring", "color": "#000000", "opacity": 0.032, "width": 0.08,
                        "at": [0.0, 0.0], "r": 1.5, "r2": r2}
                       for r2 in (0.3, 0.36, 0.42, 0.48, 0.54, 0.6, 0.66, 0.71)]}


def _alpha(img) -> np.ndarray:
    return np.asarray(img.convert("RGBA"))[:, :, 3]


# ------------------------------------------------------------ the renderer

def test_a_static_overlay_is_one_run_and_an_animated_one_is_not():
    vig = fx.validate_overlay(VIGNETTE)
    assert fx.overlay_static(vig, 24) and fx.overlay_runs(vig, 24) == [(0, 48)]
    pulse = fx.validate_overlay({**VIGNETTE, "anim": {"opacity": [[0, 0], [1.0, 1], [2.0, 1]]}})
    runs = fx.overlay_runs(pulse, 24)
    assert not fx.overlay_static(pulse, 24)
    # the fade-in is a frame each; the second it holds at 1 is one picture
    assert len(runs) == 25 and runs[-1] == (24, 24)
    # a title that types and then holds: a frame per character, then one
    title = fx.validate_overlay({"duration": 2.0, "size": 0.4, "shapes": [
        {"type": "text", "text": "HELLO", "reveal": "typewriter", "cps": 10}]})
    assert len(fx.overlay_runs(title, 24)) == 6
    # a flash is its own run
    hit = fx.validate_overlay({"duration": 0.5, "size": 0.1, "flash": {"duration": 0.1},
                               "shapes": [{"type": "circle", "r": 1}]})
    assert fx.overlay_runs(hit, 24) == [(0, 3), (3, 9)]


def test_a_static_overlay_reaches_the_part_as_one_looped_frame(tmp_path):
    """The proof's and the export's graph: one PNG, `-loop 1` for the overlay's
    duration, drawn once — not 48 PNGs packed into a .mov."""
    e = {"id": "fx_vig", "overlay": fx.validate_overlay(VIGNETTE), "sound": None}
    evs = [{"t_part": 0.5, "x": 0.5, "y": 0.5}]
    before = fx.RENDERED["frames"]
    extra, graph, vout, aout = fx.part_graph([(e, evs)], 640, 360, 24, tmp_path)
    assert fx.RENDERED["frames"] - before == 1
    assert extra[:6] == ["-loop", "1", "-framerate", "24", "-t", "2.0000"]
    assert extra[6] == "-i" and extra[7].endswith("fx_vig_ev00.png") and len(extra) == 8
    assert not list(tmp_path.glob("*.mov")) and not list(tmp_path.glob("*_frames_*"))
    assert "setpts=PTS-STARTPTS+0.5000/TB" in graph and "between(t,0.5000,2.5000)" in graph
    assert (vout, aout) == ("vout", "abase")
    # the export's rate as a fraction passes through as one
    extra2, _g, _v, _a = fx.part_graph([(e, evs)], 640, 360, "30000/1001", tmp_path / "x")
    assert extra2[3] == "30000/1001"
    assert fx._fps_text(29.97002997) == "30000/1001" and fx._fps_text(25) == "25"


def test_frames_that_repeat_are_drawn_once_and_linked(tmp_path):
    pulse = fx.validate_overlay({"duration": 0.5, "size": 0.2, "shapes": [{"type": "circle", "r": 1}],
                                 "anim": {"scale": [[0, 2.0], [0.125, 1.0]]}})
    before = fx.RENDERED["frames"]
    paths = fx.render_overlay_frames(pulse, 320, 180, 24, tmp_path / "f")
    assert len(paths) == 12 and [p.name for p in paths][:2] == ["f_0000.png", "f_0001.png"]
    runs = fx.overlay_runs(pulse, 24)
    assert fx.RENDERED["frames"] - before == len(runs) == 4          # 3 scaling, then held
    assert all(p.stat().st_ino == paths[3].stat().st_ino for p in paths[3:])
    from PIL import Image
    assert np.array_equal(_alpha(fx.render_overlay_frame(pulse, 320, 180, 11 / 24)),
                          _alpha(Image.open(paths[11])))


def test_only_the_frames_share_of_a_big_sprite_is_drawn():
    """The vignette's box is 1.6 frame widths: its sprite square was ~5 300 px at 720p
    (×2 supersampled, per shape). Drawn now is the frame plus a few pixels — and the
    picture is the vignette: the centre clear, the corners darkened."""
    vig = fx.validate_overlay(VIGNETTE)
    t0 = time.perf_counter()
    frame = fx.render_overlay_frame(vig, 1280, 720, 0.0)
    took = time.perf_counter() - t0
    a = _alpha(frame)
    assert a.shape == (720, 1280)
    assert a[360, 640] == 0 and a[:40, :40].mean() > 30 and a[-40:, -40:].mean() > 30
    assert took < 3.0, f"{took:.2f} s for one 720p vignette frame"      # was ~5 s, measured 0.25 s


def test_a_sprite_cut_by_the_frame_edge_draws_as_it_did(tmp_path):
    """Windowed drawing is the same picture: a marker half off the frame's corner, turned
    and fading, against the whole sprite drawn and cropped (the old way)."""
    from PIL import Image
    ov = fx.validate_overlay({"duration": 0.1, "size": 0.3, "shapes": [
        {"type": "rect", "at": [0.3, 0], "w": 1.2, "h": 0.4, "fill": True},
        {"type": "ring", "at": [0, 0], "r": 1, "r2": 0.6, "color": "#ff0000"}],
        "anim": {"rotate": [[0, 30]], "opacity": [[0, 0.7]]}})
    w, h, anchor = 320, 180, (0.97, 0.05)
    got = fx.render_overlay_frame(ov, w, h, 0.0, anchor=anchor)
    pose = fx.pose_at(ov, 0.0)
    sprite, ext, (left, top) = fx._sprite_image(ov, ov["size"] * w, pose, 0.0)
    assert (left, top) == (0, 0) and sprite.width == 2 * ext
    ref = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    x0, y0 = int(round(anchor[0] * w)) - ext, int(round(anchor[1] * h)) - ext
    l, t = max(0, -x0), max(0, -y0)
    part = sprite.crop((l, t, min(sprite.width, w - x0), min(sprite.height, h - y0)))
    ref.alpha_composite(part, dest=(x0 + l, y0 + t))
    diff = np.abs(np.asarray(got).astype(int) - np.asarray(ref).astype(int))
    assert diff.max() <= 2, diff.max()


def test_an_accepted_vignette_exports_through_the_looped_frame(project, tmp_path):
    """The export's part graph is the proof's: `assemble.py` renders the accepted static
    vignette from its one looped PNG — corners darker than the film without it, the
    centre the same, the part's length unchanged."""
    import subprocess
    tools = Path(__file__).resolve().parents[2] / "research" / "tools"
    segs = [{"id": "s1", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"}]
    vig = {"id": "fx_vig0001", "shot": "s1", "name": "soft vignette", "status": "accepted",
           "events": [{"t": 1.0, "x": 0.5, "y": 0.5}], "overlay": {**VIGNETTE, "duration": 2.0},
           "sound": None}
    films, logs = {}, {}
    for name, effects in (("with", [fx.validate_effect(vig, segs)]), ("without", [])):
        edl = {"variant": "F", "title": "vignette", "orient": "none", "target_s": [1, 20],
               "segments": segs, **({"effects": effects} if effects else {})}
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(edl), encoding="utf-8")
        out, parts = tmp_path / f"{name}.mp4", tmp_path / f"{name}_parts"
        r = subprocess.run(["uv", "run", "--quiet", str(tools / "assemble.py"), str(path),
                            "--footage", str(project["footage"]), "--sidecars", str(project["sidecars"]),
                            "--parts-dir", str(parts), "--keep-parts", "-o", str(out)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        films[name], logs[name] = parts / "part_000.mp4", r.stdout
    assert "fx: soft vignette" in logs["with"], logs["with"]

    def luma(p: Path, t: float) -> np.ndarray:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(p), "-frames:v", "1",
                              "-vf", "scale=160:90,format=gray", "-f", "rawvideo", "-"],
                             capture_output=True, check=True).stdout
        return np.frombuffer(raw, dtype=np.uint8).reshape(90, 160).astype(float)

    a, b = luma(films["with"], 1.0), luma(films["without"], 1.0)
    assert abs(a[35:55, 60:100].mean() - b[35:55, 60:100].mean()) < 3          # the centre clear
    assert b[:12, :16].mean() - a[:12, :16].mean() > 4                          # a corner darker
    probe = [subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                             str(films[k])], capture_output=True, text=True).stdout for k in ("with", "without")]
    assert abs(float(probe[0]) - float(probe[1])) < 0.05


# ------------------------------------------------------------ the check, in the server

def _vignette_design(note, seg, clip, sidecar, *, reference=None, place=False, proxy=None,
                     workdir=None, segments=None, clips=None, window=None):
    """The model's design, scripted: verdict 10's vignette on the whole shot."""
    e = {"name": "soft edge vignette", "why": "the edges fall off", "shot": seg["id"],
         "clip": seg["clip"], "note": note,
         "events": [{"t": float(seg["in"]), "x": 0.5, "y": 0.5, "label": "whole shot"}],
         "overlay": {**VIGNETTE, "duration": round(float(seg["out"]) - float(seg["in"]), 3)},
         "sound": None, "created": "2026-10-18T22:20:23",
         "history": [{"note": note, "at": "2026-10-18T22:20:23"}]}
    return fx.validate_effect(e, segments or [seg], clips)


@pytest.fixture
def scripted(monkeypatch, project):
    """Only the model is scripted (fx.design / fx.revise): the proof, the overlay, the
    measuring are the real ones, so the check runs where the server runs it."""
    monkeypatch.setattr(fx, "design", _vignette_design)
    monkeypatch.setattr(fx, "revise", lambda effect, note, segments, clips=None:
                        fx.validate_effect({**effect, "note": note}, segments, clips))
    return project


def _seed(project, client, segments) -> list[str]:
    import shutil
    import server
    edl = json.loads(project["edl"].read_text(encoding="utf-8"))
    edl["segments"] = segments
    edl.pop("effects", None)
    project["edl"].write_text(json.dumps(edl, indent=1), encoding="utf-8")
    server.configure(project["edl"], project["footage"], project["sidecars"],
                     project["work"], proxies=False, assets=project["assets"])
    shutil.rmtree(server.fx_home(), ignore_errors=True)
    return [s["id"] for s in client.get("/api/project").json()["segments"]]


def _job(client, job: str) -> dict:
    return client.get(f"/api/job/{job}").json()


def _wait(client, job: str, timeout: float = 30.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        snap = _job(client, job)
        if snap["state"] in ("done", "failed"):
            return snap
        time.sleep(0.05)
    raise AssertionError(f"job {job} did not finish: {_job(client, job)}")


def _checks(fx_id: str) -> list[dict]:
    import server
    return [j for j in server.FX.values() if j.get("fx_kind") == "verify" and j.get("fx_id") == fx_id]


def test_the_vignette_check_runs_in_a_child_with_a_moving_percent_and_one_job(scripted, client):
    """Verdict 10's case end to end on the suite's bin: the design lands, its check
    starts by itself, the board's card asks for a check of the proposal while it runs
    (fx.js refresh: a proposal with no `verify` yet) and gets the same job, the job's %
    moves with an ETA, the proof draws one overlay frame — in a child process, not in
    the server's — and the checklist lands on the effect."""
    import server
    shot = _seed(scripted, client, [{"clip": "CLIP_A.MP4", "in": 0.0, "out": 6.0, "why": "all"}])[0]
    drawn = fx.RENDERED["frames"]
    r = client.post("/api/fx/design", json={"shot": shot, "note": "put a soft vignette on this shot"})
    fx_id = _wait(client, r.json()["job"])["result"]["id"]
    checks = _checks(fx_id)
    assert len(checks) == 1
    job = checks[0]["id"]
    seen, asked = [], None
    t0 = time.time()
    while time.time() - t0 < 60:
        snap = _job(client, job)
        if asked is None:
            asked = client.post("/api/fx/verify", json={"id": fx_id}).json()["job"]
        if snap["state"] != "running":
            break
        seen.append((snap["pct"], snap["eta_s"], snap["detail"], snap.get("check_pid")))
        time.sleep(0.03)
    assert snap["state"] == "done", snap
    assert asked == job, "a second check was queued for the same effect"
    assert len(_checks(fx_id)) == 1
    pcts = [p for p, _e, _d, _pid in seen if 0 < p < 100]
    assert len(set(pcts)) >= 3 and pcts == sorted(pcts), seen
    assert any(eta is not None and eta > 0 for _p, eta, _d, _pid in seen), seen
    assert {"rendering the shot", "rendering the proof"} <= {d for _p, _e, d, _pid in seen}, seen
    pids = {pid for *_x, pid in seen if pid}
    assert pids and server.os.getpid() not in pids
    assert fx.RENDERED["frames"] == drawn, "the overlay was drawn in the server's process"
    e = next(x for x in client.get("/api/fx").json()["effects"] if x["id"] == fx_id)
    assert e["verify"]["ok"] is True, e["verify"]
    assert e["verify"]["proof"]["overlay_frames"] <= 2, e["verify"]["proof"]
    pic = next(c for c in e["verify"]["checks"] if c["key"] == "picture_landed")
    assert pic["ok"] is True, pic
    assert (server.fx_home() / fx_id / "proof.mp4").exists()
    assert not list((server.fx_home() / fx_id).glob("check.*"))      # spec and answer cleaned up


def test_a_vignette_at_the_shots_start_passes_its_own_check(monkeypatch, scripted, client):
    """Verdict 11, punch 2, end to end through the server's check (the child, the real
    proof and `verify`): judge 11's vignette on Killington sat on a shot from 194.5944 s,
    and the designer anchored it at 194.59, the in-point to 2 decimals; its check read
    "✗ a moment is outside the shot" and measured the picture at no event at all ("(0)").
    The same here on a shot from 1.5944 s: the proposal sits on the in-point, every check
    passes, and the picture is measured at its one event."""
    def rounded_start(note, seg, clip, sidecar, **kw):
        e = _vignette_design(note, seg, clip, sidecar, **kw)
        e["events"] = [{"t": round(float(seg["in"]), 2), "x": 0.5, "y": 0.5, "label": "whole shot"}]
        return fx.validate_effect(e, kw.get("segments") or [seg], kw.get("clips"))

    monkeypatch.setattr(fx, "design", rounded_start)
    shot = _seed(scripted, client, [{"clip": "CLIP_A.MP4", "in": 1.5944, "out": 5.0, "why": "all"}])[0]
    r = client.post("/api/fx/design", json={"shot": shot, "note": "put a soft vignette on this shot"})
    fx_id = _wait(client, r.json()["job"])["result"]["id"]
    t0 = time.time()
    while time.time() - t0 < 60:
        e = next(x for x in client.get("/api/fx").json()["effects"] if x["id"] == fx_id)
        if (e.get("verify") or {}).get("at"):
            break
        time.sleep(0.1)
    assert [ev["t"] for ev in e["events"]] == [1.5944]
    by = {c["key"]: c for c in e["verify"]["checks"]}
    assert by["in_shot"]["ok"] is True, by["in_shot"]
    assert by["picture_landed"]["ok"] is True and "(1)" in by["picture_landed"]["detail"], by["picture_landed"]
    assert e["verify"]["ok"] is True, e["verify"]


def test_one_check_per_change_while_one_runs(monkeypatch, scripted, client):
    """The rule, deterministic (the check held on its thread): an ask for the effect as
    it is joins the running check; a change queues one more — one, however many asks
    follow — and that one is what lands."""
    import threading
    import server
    gate, entered = threading.Event(), threading.Event()
    real = fx.verify

    def held(effect, seg, **kw):
        entered.set()
        gate.wait(20)
        return real(effect, seg, **kw)
    monkeypatch.setattr(fx, "verify", held)                  # replaced: on the job's thread
    shot = _seed(scripted, client, [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "a"}])[0]
    fx_id = _wait(client, client.post("/api/fx/design", json={"shot": shot, "note": "vignette"}).json()["job"])["result"]["id"]
    assert entered.wait(30)
    first = _checks(fx_id)[0]["id"]
    try:
        for _ in range(3):
            assert client.post("/api/fx/verify", json={"id": fx_id}).json()["job"] == first
        assert len(_checks(fx_id)) == 1
        assert client.put(f"/api/fx/{fx_id}", json={"events": [{"t": 2.0, "x": 0.4, "y": 0.5}]}).status_code == 200
        second = {j["id"] for j in _checks(fx_id)} - {first}
        assert len(second) == 1
        for _ in range(3):
            assert client.post("/api/fx/verify", json={"id": fx_id}).json()["job"] in second
        assert len(_checks(fx_id)) == 2
    finally:
        gate.set()
    snaps = [_wait(client, j["id"]) for j in _checks(fx_id)]
    assert sorted(s["detail"] for s in snaps) == ["every check passed",
                                                  "the effect changed meanwhile — checked again"]
    assert server.FX_VERIFY_RUNNING.get(fx_id) is None and server.FX_VERIFY_WAITING.get(fx_id) is None


def test_a_discard_while_the_child_renders_is_not_a_failed_check(scripted, client):
    """A check starts by itself after every design, so a quick Discard lands while the
    child renders into the effect's folder, which the Discard deletes: the check ends
    "the effect is gone", never a red "failed" row."""
    shot = _seed(scripted, client, [{"clip": "CLIP_A.MP4", "in": 0.0, "out": 6.0, "why": "all"}])[0]
    fx_id = _wait(client, client.post("/api/fx/design", json={"shot": shot, "note": "vignette"}).json()["job"])["result"]["id"]
    job = _checks(fx_id)[0]["id"]
    t0 = time.time()
    while _job(client, job)["detail"] in ("", "waiting for another check to finish") and time.time() - t0 < 20:
        time.sleep(0.02)
    assert client.post("/api/fx/discard", json={"id": fx_id}).status_code == 200
    snap = _wait(client, job)
    assert (snap["state"], snap["detail"]) == ("done", "the effect is gone"), snap


def test_the_eta_counts_from_the_check_not_from_its_wait(monkeypatch, scripted, client):
    """A check waits behind an export (a 4K encode owns the cores); its ETA once it runs
    is the stages' own, not an estimate already spent while it waited (Killington, a
    260 s export: the check ran at "30%" with no time left to show)."""
    import server
    from roughcut import progress
    monkeypatch.setattr(server, "FX_VERIFY_POLL_S", 0.05)
    # a quick check (~1 s planned) behind a longer wait: counted from the wait, the
    # plan would be spent before the check began
    monkeypatch.setattr(server, "FX_CHECK_COST", {"base": 0.05, "proof": 0.05, "frame": 0.05,
                                                  "measure": 0.2})
    shot = _seed(scripted, client, [{"clip": "CLIP_A.MP4", "in": 0.0, "out": 6.0, "why": "all"}])[0]
    server.RENDERS["rwait001"] = progress.Job("render", "Exporting — test", id="rwait001")
    try:
        fx_id = _wait(client, client.post("/api/fx/design", json={"shot": shot, "note": "vignette"}).json()["job"])["result"]["id"]
        job = _checks(fx_id)[0]["id"]
        time.sleep(2.5)
        snap = _job(client, job)
        assert snap["detail"] == "waiting for the render to finish" and snap["pct"] == 0.0
    finally:
        server.RENDERS["rwait001"].finish("done")
    etas = []
    t0 = time.time()
    while time.time() - t0 < 60:
        snap = _job(client, job)
        if snap["state"] != "running":
            break
        if snap.get("milestones_total"):
            etas.append(snap["eta_s"])
        time.sleep(0.03)
    server.RENDERS.pop("rwait001", None)
    assert snap["state"] == "done", snap
    assert snap["waited_s"] >= 2.0 and snap["elapsed_s"] < snap["waited_s"] + 60
    assert etas and etas[0] is not None and etas[0] > 0, etas


def test_a_vignette_that_fades_up_is_checked_once_it_is_up(project, tmp_path):
    """Killington, wave 14: the designer eased the vignette in (opacity 0 → 1 over
    0.4 s); its check looked 0.1 s in, found a quarter of it at the bottom edge only,
    and said "not drawn where expected". The proof is measured once the sprite is up."""
    import subprocess
    from roughcut import fxcheck
    # a mid-grey shot: a quarter of a soft vignette moves no pixel past the check's
    # threshold (24/255), the whole of it darkens the corners by ~50
    grey = tmp_path / "GREY.MP4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi",
                    "-i", "color=c=0x808080:size=320x180:rate=24:duration=4", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=4", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(grey)],
                   check=True, capture_output=True)
    seg = {"id": "s1", "clip": "GREY.MP4", "in": 0.0, "out": 4.0}
    e = fx.validate_effect({"id": "fx_fade0001", "shot": "s1", "clip": "GREY.MP4",
                            "name": "soft vignette", "events": [{"t": 0.0, "x": 0.5, "y": 0.5}],
                            "overlay": {**VIGNETTE, "duration": 4.0,
                                        "shapes": [dict(s, opacity=0.06) for s in VIGNETTE["shapes"]],
                                        "anim": {"opacity": [[0, 0], [0.4, 1], [3.6, 1], [4.0, 0]]}},
                            "sound": None}, [seg])
    assert fx._shown_at(e["overlay"], 4.0) == pytest.approx(0.375 + 0.05, abs=0.05)
    assert fx._shown_at(VIGNETTE, 2.0) is None                     # no fade: as before
    res = fxcheck.check({"effect": e, "seg": seg, "src": str(grey),
                         "dir": str(tmp_path / "fx"), "w": 640, "h": 360, "fps": 24,
                         "onset": None, "hz": 10, "impact": False})
    pic = next(c for c in res["verify"]["checks"] if c["key"] == "picture_landed")
    assert pic["ok"] is True, pic
    assert res["stats"]["overlay_frames"] <= 20                   # the fades, then one held frame
    assert res["stats"]["sprites_drawn"] == 1                     # a fade reuses its sprite
