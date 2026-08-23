"""Cut board API tests.

Weighted toward the things that actually broke while building it — proxy files
served mid-write, rotation metadata surviving into output, seeking that silently
degrades to streaming-from-zero — rather than toward coverage for its own sake.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
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


def test_visual_moments_reach_the_model_when_the_pass_has_run(tmp_path, project):
    """The visual sidecars are optional — they cost model calls — but when they exist
    the events nobody narrated have to reach the prompt."""
    from roughcut import config, inference

    vis = tmp_path / "visual"
    vis.mkdir()
    (vis / "CLIP_A.visual.json").write_text(json.dumps({
        "clip": "CLIP_A.MP4",
        "moments": [{"start": 1.0, "end": 3.0, "kind": "fall", "notable": True,
                     "what": "rider goes down in deep snow"}],
        "unusable": [], "summary": "a run"}), encoding="utf-8")

    class Scripted:
        name = "scripted"
        seen: list = []

        def complete(self, request):
            Scripted.seen.append(request)
            text = json.dumps({"segments": [{"clip": "CLIP_A.MP4", "in": 1.0,
                                             "out": 3.0, "why": "the fall"}]})
            model = config.model_for(request.role)
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend="scripted", model=model,
                                    projected_usd=1e-4, latency_ms=1, raw=text)

    inference.set_backend(Scripted())
    inference.reset_spend()
    try:
        with _fresh(tmp_path, project, sidecars=project["sidecars"],
                    visual=vis) as c:
            assert c.get("/api/project").json()["clips"]["CLIP_A.MP4"]["visual"][
                "moments"][0]["kind"] == "fall"
            _ask(c, {"note": "", "segments": [], "story": "a ski film"})
        assert "rider goes down in deep snow" in Scripted.seen[0].prompt
    finally:
        inference.set_backend(None)


def test_clips_carry_a_capture_time(client):
    """Without it the model cannot know what "before" means — the cause of the
    out-of-order airport section in the first originated cut."""
    clips = client.get("/api/project").json()["clips"]
    stamps = {c: v["captured"] for c, v in clips.items()}
    assert all(isinstance(t, float) and t > 0 for t in stamps.values()), stamps


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
    import server
    pdir = server.STATE["proxy_dir"]
    assert {p.name for p in pdir.glob("*.mp4")} >= {"CLIP_A.mp4", "CLIP_B.mp4",
                                                    "CLIP_C.mp4"}
    assert list(pdir.glob("*.part.mp4")) == [], "half-written proxy left at final path"


def test_proxy_carries_no_rotation_metadata(client, project):
    """A proxy that plays sideways in the UI is worse than no proxy."""
    import server
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream_side_data=rotation", "-of", "csv=p=0",
         str(server.STATE["proxy_dir"] / "CLIP_A.mp4")],
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


def test_renders_are_listed_as_versions_newest_first(client, project):
    """Judging an edit is comparative: the board used to show only the newest file,
    so comparing two versions meant hunting for mp4s on disk."""
    import server

    before = {r["name"] for r in client.get("/api/renders").json()["renders"]}
    jobs = []
    for out in (2.5, 3.5):
        body = {"segments": [{"clip": "CLIP_A.MP4", "in": 0.5, "out": out, "why": "a"}]}
        jobs.append(client.post("/api/render", json=body).json()["job"])
        deadline = time.time() + 90
        while client.get(f"/api/render/{jobs[-1]}").json()["state"] == "running":
            assert time.time() < deadline, "render timed out"
            time.sleep(0.5)

    versions = client.get("/api/renders").json()["renders"]
    fresh = [v for v in versions if v["name"] not in before]
    assert len(fresh) == 2
    assert [v["created"] for v in versions] == sorted(
        (v["created"] for v in versions), reverse=True), "newest first"
    newest = versions[0]
    assert newest["name"] == f"cut_{jobs[-1]}.mp4"
    assert newest["segments"] == 1 and newest["planned_s"] == 3.0
    assert abs(newest["duration_s"] - 3.0) < 0.25
    # playable straight from the list
    assert client.get(newest["url"], headers={"range": "bytes=0-99"}).status_code == 206


def test_a_version_survives_a_restart(client, project):
    """Metadata lives next to the file, not in memory: a versions list that empties
    when the server restarts is not a versions list."""
    import server

    body = {"segments": [{"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "b"}]}
    job = client.post("/api/render", json=body).json()["job"]
    deadline = time.time() + 90
    while client.get(f"/api/render/{job}").json()["state"] == "running":
        assert time.time() < deadline, "render timed out"
        time.sleep(0.5)

    # The metadata must already be on disk the moment the job says done: the UI
    # refreshes its versions list on that signal, and a render announcing itself
    # before its own metadata exists gets listed as an unlabelled older file.
    server.RENDERS.clear()
    listed = client.get("/api/renders").json()["renders"]
    assert any(r["name"] == f"cut_{job}.mp4" and r["duration_s"] for r in listed)


def test_render_reports_which_shot_it_is_on(client, project, monkeypatch):
    """Karl, on clicking Render: "got like no response - and just see rendering...".
    Every shot is cut to its own file before they are joined, so this is a real count
    rather than a spinner. (Ticking faster here only because these clips are 6
    seconds long; a real render takes minutes per shot.)"""
    import server
    monkeypatch.setattr(server, "PROGRESS_TICK_S", 0.05)
    segs = [{"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.5, "why": "a"},
            {"clip": "CLIP_B.MP4", "in": 1.0, "out": 2.0, "why": "b"},
            {"clip": "CLIP_C.MP4", "in": 0.0, "out": 1.5, "why": "c"}]
    job = client.post("/api/render", json={"segments": segs}).json()["job"]

    stages, counts = set(), set()
    deadline = time.time() + 120
    while time.time() < deadline:
        s = client.get(f"/api/render/{job}").json()
        stages.add(s["stage"])
        counts.add(s["done"])
        assert s["total"] == 3
        assert s["elapsed_s"] >= 0
        if s["state"] != "running":
            break
        time.sleep(0.05)
    assert s["state"] == "done", s["log"][-400:]
    assert "cutting" in stages
    assert max(counts) > 0, f"never showed progress: {sorted(counts)}"
    assert s["done"] == s["total"], "a finished render must not read as 0 of 3"
    # the parts directory is cleaned up behind it
    assert not list(project["work"].glob("parts_*"))


def test_render_status_404_for_unknown_job(client):
    assert client.get("/api/render/deadbeef").status_code == 404


def test_a_render_can_carry_a_label(client, project):
    """A proposal can be rendered without being accepted, to be watched before the
    decision — and then the versions list must say that is what it is."""
    body = {"segments": [{"clip": "CLIP_C.MP4", "in": 0.0, "out": 1.5, "why": "c"}],
            "label": "proposal abc123 — not accepted"}
    job = client.post("/api/render", json=body).json()["job"]
    deadline = time.time() + 90
    while client.get(f"/api/render/{job}").json()["state"] == "running":
        assert time.time() < deadline, "render timed out"
        time.sleep(0.5)
    listed = {r["name"]: r for r in client.get("/api/renders").json()["renders"]}
    assert listed[f"cut_{job}.mp4"]["note"] == "proposal abc123 — not accepted"


# ------------------------------------------------------------------ render profiles

def _resolution(path: Path) -> tuple[int, int]:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height", "-of", "csv=p=0",
                        str(path)], capture_output=True, text=True)
    w, h = r.stdout.strip().split(",")
    return int(w), int(h)


def test_delivery_resolution_caps_at_4k_and_never_upscales():
    """The pure sizing rule behind the delivery profile, isolated from ffmpeg: a
    bin bigger than 4K gets capped; a bin already at or below 4K — Killington's own
    3840x2160, or the test suite's 320x180 synthetic clips — is left alone rather
    than stretched up to fill a bigger canvas that carries no more real detail."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research" / "tools"))
    import assemble
    assert assemble.delivery_resolution(3840, 2160) == (3840, 2160)
    assert assemble.delivery_resolution(5312, 2988) == (3840, 2160)
    assert assemble.delivery_resolution(320, 180) == (320, 180)


def test_render_defaults_to_the_preview_profile(client, project):
    """No `profile` in the request must behave exactly as it always has — delivery
    is opt-in, never a silent upgrade for a render already in flight elsewhere."""
    body = {"segments": [{"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.5, "why": "a"}]}
    job = client.post("/api/render", json=body).json()["job"]
    deadline = time.time() + 90
    while client.get(f"/api/render/{job}").json()["state"] == "running":
        assert time.time() < deadline, "render timed out"
        time.sleep(0.5)
    listed = {r["name"]: r for r in client.get("/api/renders").json()["renders"]}
    entry = listed[f"cut_{job}.mp4"]
    assert entry["profile"] == "preview"
    assert (entry["width"], entry["height"]) == (1920, 1080)   # unchanged fast-path size


def test_delivery_profile_never_upscales_a_small_bin(client, project):
    """The synthetic clips are 320x180, far below the 4K cap. Delivery must render
    them at their own size — the same rule build_proxy already applies to proxies —
    and the versions list must be able to say so."""
    body = {"segments": [{"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.5, "why": "a"}],
            "profile": "delivery"}
    job = client.post("/api/render", json=body).json()["job"]
    deadline = time.time() + 90
    while client.get(f"/api/render/{job}").json()["state"] == "running":
        assert time.time() < deadline, "render timed out"
        time.sleep(0.5)
    status = client.get(f"/api/render/{job}").json()
    assert status["state"] == "done", status["log"][-800:]
    listed = {r["name"]: r for r in client.get("/api/renders").json()["renders"]}
    entry = listed[f"cut_{job}.mp4"]
    assert entry["profile"] == "delivery"
    assert (entry["width"], entry["height"]) == (320, 180), \
        "delivery must not upscale a bin shot smaller than 4K"
    assert _resolution(Path(status["output"])) == (320, 180)      # the file agrees


def test_unknown_render_profile_is_rejected(client, project):
    body = {"segments": [{"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.5, "why": "a"}],
            "profile": "cinema-grade"}
    assert client.post("/api/render", json=body).status_code == 400


def test_renders_from_before_the_profile_existed_still_list_cleanly(client, project):
    """Old metadata on disk has neither `profile` nor `width`/`height` — it must
    read as the preview render it always was, never crash the versions list, and
    never be mistaken for a delivery render."""
    body = {"segments": [{"clip": "CLIP_A.MP4", "in": 0.5, "out": 1.5, "why": "a"}]}
    job = client.post("/api/render", json=body).json()["job"]
    deadline = time.time() + 90
    while client.get(f"/api/render/{job}").json()["state"] == "running":
        assert time.time() < deadline, "render timed out"
        time.sleep(0.5)
    meta_path = Path(client.get(f"/api/render/{job}").json()["output"]).with_suffix(".json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    del meta["profile"], meta["width"], meta["height"]     # simulate a pre-existing render
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    listed = {r["name"]: r for r in client.get("/api/renders").json()["renders"]}
    entry = listed[f"cut_{job}.mp4"]
    assert entry["profile"] == "preview"
    assert entry["width"] is None and entry["height"] is None


# ------------------------------------------------------------------ music

def _loudness(path: Path) -> float:
    r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path),
                        "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
                       capture_output=True, text=True)
    for line in r.stderr.splitlines():
        if "I:" in line and "LUFS" in line:
            return float(line.split("I:")[1].split("LUFS")[0])
    raise AssertionError(f"no loudness reading for {path.name}")


def _frames(path: Path) -> int:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-count_frames", "-show_entries", "stream=nb_read_frames",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return int(r.stdout.strip().strip(","))


def test_music_bed_is_mixed_under_without_touching_the_picture(project, tmp_path):
    """The first film-wide effect, and the render path every other one will use
    (docs/EFFECTS.md). Music runs after the concat with the video stream copied, so a
    bed costs nothing in picture quality."""
    tools = Path(__file__).resolve().parents[2] / "research" / "tools"
    edl = {"variant": "M", "title": "music test", "orient": "none", "target_s": [1, 20],
           "segments": [{"clip": "CLIP_A.MP4", "in": 0.0, "out": 3.0, "why": "a"},
                        {"clip": "CLIP_B.MP4", "in": 0.0, "out": 3.0, "why": "b"}]}
    edl_path = tmp_path / "music.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")

    # A distinct tone — the clips carry 440Hz, so the bed is 900Hz. Mid-band on
    # purpose: LUFS is K-weighted, and a bass-heavy bed barely moves the number even
    # when it is plainly audible, which would make this measurement lie.
    track = tmp_path / "bed.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi",
                    "-i", "sine=frequency=900:duration=2", str(track)], check=True)

    def render(out_name, music=None):
        if music is not None:
            edl_path.write_text(json.dumps({**edl, "effects_music": music}),
                                encoding="utf-8")
        else:
            edl_path.write_text(json.dumps(edl), encoding="utf-8")
        out = tmp_path / out_name
        r = subprocess.run(
            ["uv", "run", "--quiet", str(tools / "assemble.py"), str(edl_path),
             "--footage", str(project["footage"]), "--sidecars",
             str(project["sidecars"]), "-o", str(out)],
            capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        return out, r.stdout

    plain, _ = render("plain.mp4")
    flat, flat_log = render("flat.mp4",
                            {"asset": str(track), "gain_db": 0, "duck": False})
    ducked, duck_log = render("ducked.mp4",
                              {"asset": str(track), "gain_db": 0, "duck": True})

    # picture untouched, in both cases — the whole reason music runs after the concat
    assert _frames(flat) == _frames(plain), "the picture was re-encoded"
    # a 2s track under a 6s film: looping is what keeps the bed playing throughout
    assert _duration(flat) == pytest.approx(_duration(plain), abs=0.15)
    assert rotation_of(flat) == "" and rotation_of(ducked) == ""

    # the render says which key it used; on a project with sidecars that is speech
    assert "flat" in flat_log
    assert "keyed on speech" in duck_log, duck_log[-300:]
    assert _loudness(flat) > _loudness(plain) + 0.3, "no bed audible in the mix"
    assert _duration(ducked) == pytest.approx(_duration(plain), abs=0.15)


def _mean_volume(path: Path, start: float, end: float) -> float:
    r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path),
                        "-af", f"atrim={start}:{end},volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    for line in r.stderr.splitlines():
        if "mean_volume:" in line:
            return float(line.split("mean_volume:")[1].split("dB")[0])
    raise AssertionError(f"no volume reading for {path.name} {start}-{end}")


def test_the_bed_lifts_in_the_gaps_and_drops_under_speech(tmp_path):
    """Ducking, measured where it can actually be seen: a film that is loud for three
    seconds and silent for three.

    The previous version of this test inferred ducking from the *mixed* file's
    loudness against clips that are a constant tone — no gaps, so nothing to lift into,
    and the number moved for the wrong reasons. Karl's real bed was inaudible while
    that test was green.
    """
    from roughcut import effects

    film = tmp_path / "halfquiet.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-nostdin",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=6",
        "-f", "lavfi", "-i", "aevalsrc=if(lt(t\\,3)\\,0.5*sin(2*PI*440*t)\\,0):d=6",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(film)], check=True)
    track = tmp_path / "bed.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi",
                    "-i", "sine=frequency=900:duration=6", str(track)], check=True)

    bed = tmp_path / "bedonly.m4a"
    effects.add_music(film, {"asset": str(track), "gain_db": -6, "duck": True,
                             "fade_in": 0, "fade_out": 0}, bed, bed_only=True)

    under_speech = _mean_volume(bed, 0.5, 2.5)
    in_the_gap = _mean_volume(bed, 3.5, 5.5)
    assert in_the_gap > under_speech + 3.0, (
        f"the bed does not lift in the gap: {under_speech:.1f} dB under the tone, "
        f"{in_the_gap:.1f} dB in the silence")


@pytest.mark.parametrize("asked", [8.0, 16.0])
def test_the_duck_depth_knob_is_calibrated(tmp_path, asked):
    """`duck_db` has to mean dB, or it is a dial with no markings.

    It did not, at first: ratio was exposed as the depth control, and 6 versus 12
    differed by 2 dB because the key sat 28 dB over the threshold and pinned the
    compressor either way. Depth comes from the key's level, solved for the requested
    reduction — so this asserts what was asked for is what comes out.
    """
    from roughcut import effects

    film = tmp_path / f"film{asked}.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-nostdin",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=6",
        "-f", "lavfi", "-i", "aevalsrc=if(lt(t\\,3)\\,0.5*sin(2*PI*440*t)\\,0):d=6",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(film)], check=True)
    track = tmp_path / f"bed{asked}.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi",
                    "-i", "sine=frequency=900:duration=6", str(track)], check=True)

    bed = tmp_path / f"bed{asked}.m4a"
    effects.add_music(film, {"asset": str(track), "gain_db": -6, "duck": True,
                             "duck_db": asked, "fade_in": 0, "fade_out": 0},
                      bed, bed_only=True, speech=[(0.0, 3.0)])
    measured = _mean_volume(bed, 3.5, 5.5) - _mean_volume(bed, 0.5, 2.5)
    assert abs(measured - asked) < 4.0, (
        f"asked for {asked} dB of duck, measured {measured:.1f}")


@pytest.mark.parametrize("duck", [True, False])
def test_the_bed_only_graph_actually_renders(project, tmp_path, duck):
    """music_check measures ducking by rendering the bed *alone* through the same
    graph. The first version built that by carving up the mix graph's string and
    shipped one missing separator — ffmpeg refused it, after the scored render had
    already succeeded. Asserting the string parses is not enough: it has to render."""
    from roughcut import effects

    film = tmp_path / f"film_{duck}.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin",
                    "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(film)], check=True)
    track = tmp_path / f"bed_{duck}.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi",
                    "-i", "sine=frequency=900:duration=2", str(track)], check=True)

    out = tmp_path / f"bedonly_{duck}.m4a"
    effects.add_music(film, {"asset": str(track), "gain_db": -6, "duck": duck},
                      out, bed_only=True)
    assert out.exists() and out.stat().st_size > 0
    assert _duration(out) == pytest.approx(3.0, abs=0.3)


def test_music_gain_is_range_checked():
    """Parameters are validated the way segment timestamps are: a bed louder than the
    film is a bug, not a choice."""
    from roughcut import effects

    with pytest.raises(ValueError):
        effects.music_spec({"asset": "x.mp3", "gain_db": 40})
    with pytest.raises(ValueError):
        effects.music_spec({"gain_db": -12})            # no asset
    assert effects.music_spec({"asset": "x.mp3"})["duck"] is True


def rotation_of(p: Path) -> str:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream_side_data=rotation", "-of", "csv=p=0",
                        str(p)], capture_output=True, text=True)
    return r.stdout.strip().strip(",")


# ------------------------------------------------------------------ ask

def _ask_status(client, job, timeout=20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/ask/{job}").json()
        if s["state"] != "running":
            return s
        time.sleep(0.05)
    raise AssertionError(f"ask never finished: {s}")


def _ask(client, body) -> dict:
    """Start an ask and wait for its plan. Asks are jobs, like renders and analyses:
    a two-minute model call inside the request froze the whole server for its
    duration and left the plan existing only in that one response."""
    r = client.post("/api/ask", json=body)
    assert r.status_code == 200, r.text
    s = _ask_status(client, r.json()["job"])
    assert s["state"] == "done", s
    return s["plan"]


def test_ask_returns_a_proposal_without_writing(client, project):
    """The interject loop: a note in, a revised timeline out, disk untouched."""
    import server
    from roughcut import config, inference

    before = project["edl"].read_text(encoding="utf-8")

    class Scripted:
        name = "scripted"
        seen: list = []

        def complete(self, request):
            Scripted.seen.append(request)
            text = json.dumps({
                "segments": [{"clip": "CLIP_C.MP4", "in": 0.5, "out": 4.0,
                              "why": "per the note"}],
                "notes": "swapped in the unused clip"})
            model = config.model_for(request.role)
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend="scripted", model=model,
                                    projected_usd=0.0001, latency_ms=1, raw=text)

    inference.set_backend(Scripted())
    inference.reset_spend()
    try:
        plan = _ask(client, {
            "note": "use the clip that isn't in the cut",
            "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0}],
            "story": "a test film"})
        assert plan["segments"] == [{"clip": "CLIP_C.MP4", "in": 0.5, "out": 4.0,
                                     "why": "per the note"}]
        assert plan["notes"] == "swapped in the unused clip"
        assert plan["usage"]["projected_usd"] > 0
        # the model was given the transcripts and the note
        prompt = Scripted.seen[0].prompt
        assert "hello there" in prompt and "use the clip that isn't in the cut" in prompt
    finally:
        inference.set_backend(None)

    assert project["edl"].read_text(encoding="utf-8") == before, "ask must not write"


def test_ask_rejects_an_empty_note(client):
    assert client.post("/api/ask", json={"note": "  "}).status_code == 400


def test_ask_originates_when_there_is_nothing_to_revise(client, project):
    """An empty timeline is the first state of every new project, so Ask has to be
    able to start a cut, not only change one."""
    from roughcut import config, inference

    class Scripted:
        name = "scripted"
        seen: list = []

        def complete(self, request):
            Scripted.seen.append(request)
            text = json.dumps({
                "segments": [{"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0,
                              "why": "opens on the greeting"},
                             {"clip": "CLIP_B.MP4", "in": 2.4, "out": 4.0,
                              "why": "the reply"}],
                "notes": "read it as a conversation"})
            model = config.model_for(request.role)
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend="scripted", model=model,
                                    projected_usd=0.0001, latency_ms=1, raw=text)

    inference.set_backend(Scripted())
    inference.reset_spend()
    try:
        plan = _ask(client, {"note": "", "segments": [],
                             "story": "two people talking"})
        assert len(plan["segments"]) == 2
        assert plan["notes"] == "read it as a conversation"
        prompt = Scripted.seen[0].prompt
        assert "There is no edit yet" in prompt
        assert "two people talking" in prompt and "hello there" in prompt
    finally:
        inference.set_backend(None)

    # still a proposal, not a write
    assert json.loads(project["edl"].read_text(encoding="utf-8"))["segments"] != []


def test_the_server_stays_responsive_during_an_ask(client):
    """The defect Karl hit: /api/ask ran the model call inside the request handler, so
    a 112-second Killington ask froze the whole server — status, media, previews, all
    of it — and the UI looked hung because it was."""
    from roughcut import config, inference

    class Slow:
        name = "slow"

        def complete(self, request):
            time.sleep(1.5)
            text = json.dumps({"segments": [{"clip": "CLIP_A.MP4", "in": 0.0,
                                             "out": 2.0, "why": "x"}]})
            model = config.model_for(request.role)
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend="slow", model=model, projected_usd=1e-4,
                                    latency_ms=1500, raw=text)

    inference.set_backend(Slow())
    inference.reset_spend()
    try:
        job = client.post("/api/ask", json={"note": "tighten it"}).json()["job"]
        t0 = time.time()
        assert client.get("/api/status").status_code == 200
        assert time.time() - t0 < 1.0, "status waited on the model call"
        assert client.get(f"/api/ask/{job}").json()["state"] == "running"
        assert _ask_status(client, job)["state"] == "done"
    finally:
        inference.set_backend(None)


def test_a_plan_survives_the_browser_that_asked_for_it(client, project):
    """A two-minute call whose only copy is an HTTP response is one dropped connection
    away from being spent for nothing — which is what happened on the first Killington
    ask. The plan is on disk before the job says done."""
    from roughcut import config, inference

    class Scripted:
        name = "scripted"

        def complete(self, request):
            text = json.dumps({
                "segments": [{"clip": "CLIP_B.MP4", "in": 1.0, "out": 3.0,
                              "why": "recoverable"}],
                "notes": "kept on disk"})
            model = config.model_for(request.role)
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend="scripted", model=model,
                                    projected_usd=1e-4, latency_ms=1, raw=text)

    inference.set_backend(Scripted())
    inference.reset_spend()
    try:
        _ask(client, {"note": "build it around the milk", "segments": [],
                      "story": "a test film"})
    finally:
        inference.set_backend(None)

    import server
    server.ASKS.clear()                      # the browser, and the memory of it, gone
    record = client.get("/api/asks/latest").json()["record"]
    assert record["plan"]["segments"][0]["why"] == "recoverable"
    assert record["note"] == "build it around the milk"
    assert record["created"] > 0


def test_first_cut_without_any_analysis_says_so(tmp_path, project):
    """Distinct from a backend failure: there is nothing to cut from yet."""
    with _fresh(tmp_path, project, sidecars=tmp_path / "none") as c:
        r = c.post("/api/ask", json={"note": "make me something", "segments": []})
        assert r.status_code == 400
        assert "audio pass" in r.json()["detail"]


def test_ask_surfaces_backend_failure_on_the_job(client):
    from roughcut import inference

    class Broken:
        name = "broken"

        def complete(self, request):
            raise inference.InferenceError("claude CLI error: Not logged in")

    inference.set_backend(Broken())
    inference.reset_spend()
    try:
        job = client.post("/api/ask", json={"note": "tighten it"}).json()["job"]
        s = _ask_status(client, job)
        assert s["state"] == "failed" and s["code"] == 502
        assert "Not logged in" in s["detail"]
    finally:
        inference.set_backend(None)


# ------------------------------------------------------------ new project

def _fresh(tmp_path, project, **kw):
    """Configure the server the way `main()` does for a bin nobody has cut yet."""
    from fastapi.testclient import TestClient
    import server

    server.configure(kw.pop("edl", None), project["footage"],
                     kw.pop("sidecars", None), tmp_path, proxies=False,
                     visual=kw.pop("visual", tmp_path / "no-visual"), **kw)
    return TestClient(server.app)


def test_a_footage_folder_is_enough_to_open_the_board(tmp_path, project):
    """The prerequisite that made this expert-only: without a hand-authored EDL there
    was no way in at all. A missing EDL is now a new project, not an error."""
    import server

    with _fresh(tmp_path, project) as c:
        p = c.get("/api/project").json()
        assert p["segments"] == []
        assert p["title"] == project["footage"].name

        s = c.get("/api/status").json()
        assert s["edl_created"] is True
        assert s["clips"] == 3 and s["analysed"] == 0 and s["segments"] == 0
        # derived, not required on the command line, and per-bin
        assert Path(s["edl"]).parent == tmp_path / "projects"
        assert Path(s["sidecars"]) == tmp_path / "audio" / project["footage"].name

    on_disk = json.loads(Path(server.STATE["edl"]).read_text(encoding="utf-8"))
    assert on_disk["segments"] == [] and on_disk["orient"] == "auto"


def test_an_existing_project_is_opened_not_overwritten(tmp_path, project):
    with _fresh(tmp_path, project, edl=project["edl"],
                sidecars=project["sidecars"]) as c:
        s = c.get("/api/status").json()
        assert s["edl_created"] is False
        assert s["clips"] == 3 and s["analysed"] == 3 and s["segments"] == 2
        assert s["pending"] == []
        assert all(s["tools"].values())


def test_status_lists_what_still_needs_analysing(tmp_path, project):
    """The number that tells a human whether they can cut yet."""
    sidecars = tmp_path / "partial"
    sidecars.mkdir()
    shutil.copy(project["sidecars"] / "CLIP_A.audio.json", sidecars)
    with _fresh(tmp_path, project, sidecars=sidecars) as c:
        s = c.get("/api/status").json()
        assert s["analysed"] == 1
        assert s["pending"] == ["CLIP_B.MP4", "CLIP_C.MP4"]


def test_renders_are_per_bin_too(tmp_path, project):
    """A fresh project that opens claiming "1 version" and plays another trip's cut in
    the A slot is worse than showing nothing — which is what Killington did on its
    first launch, holding Copper's renders."""
    import server

    with _fresh(tmp_path, project) as c:
        first = server.STATE["renders"]
        assert c.get("/api/renders").json()["renders"] == []
    other = tmp_path / "another-bin"
    other.mkdir()
    with _fresh(tmp_path, {"footage": other}):
        assert server.STATE["renders"] != first


def test_status_reports_preview_building_progress(tmp_path, project):
    """Encoding runs for tens of minutes in the background on a real bin, and silence
    there reads as "nothing is happening"."""
    import server

    with _fresh(tmp_path, project) as c:
        # nothing built yet, and nothing counted yet
        assert c.get("/api/status").json()["proxies"]["total"] == 0
        server.ensure_proxies([f"{s}.MP4" for s in project["stems"]])
        px = c.get("/api/status").json()["proxies"]
        assert px["ready"] is True
        assert px["done"] == px["total"] == 3


def test_proxy_dirs_do_not_collide_between_bins(tmp_path, project):
    """Two bins can hold the same GoPro stem; a shared proxy dir would serve one
    bin's frames for the other's clip."""
    import server

    with _fresh(tmp_path, project):
        first = server.STATE["proxy_dir"]
    other = tmp_path / "other-bin"
    other.mkdir()
    with _fresh(tmp_path, {"footage": other}):
        assert server.STATE["proxy_dir"] != first


# ------------------------------------------------------------ backend status

def test_preflight_names_the_backend_and_model_without_calling_it(client):
    from roughcut import inference

    class Exploding:
        name = "exploding"

        def complete(self, request):
            raise AssertionError("preflight must not spend a call")

    inference.set_backend(Exploding())
    try:
        b = client.get("/api/status").json()["backend"]
        assert b["backend"] == "claude_cli"
        assert "claude" in b["model"]
        assert b["state"] in ("unknown", "ok", "failed", "checking")
        assert b["budget_usd"] > 0
    finally:
        inference.set_backend(None)


def test_preflight_catches_a_missing_api_key_before_the_call(client, monkeypatch):
    """The two failures that are free to detect: no CLI on PATH, no API key. Both
    used to surface ~80 seconds into an Ask."""
    import server

    monkeypatch.setenv("ROUGHCUT_BACKEND", "anthropic_api")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert "ANTHROPIC_API_KEY" in " ".join(server.backend_preflight()["problems"])

    monkeypatch.setenv("ROUGHCUT_BACKEND", "claude_cli")
    monkeypatch.setattr(server.shutil, "which", lambda name: None)
    problems = " ".join(server.backend_preflight()["problems"])
    assert "not on PATH" in problems and "login shell" in problems


def test_probe_reports_a_working_backend(client):
    from roughcut import config, inference

    class Fine:
        name = "fine"

        def complete(self, request):
            model = config.model_for(request.role)
            assert request.role == config.ROLE_ANALYSIS, "probe uses the cheap role"
            return inference.Result(content="OK", input_tokens=2, output_tokens=1,
                                    backend="fine", model=model, projected_usd=1e-6,
                                    latency_ms=42, raw="OK")

    inference.set_backend(Fine())
    inference.reset_spend()
    try:
        client.post("/api/backend/probe")
        b = _await_probe(client)
        assert b["state"] == "ok" and b["detail"] == "OK" and b["latency_ms"] == 42
    finally:
        inference.set_backend(None)


def test_probe_surfaces_not_logged_in_at_launch(client):
    from roughcut import inference

    class LoggedOut:
        name = "logged_out"

        def complete(self, request):
            raise inference.InferenceError("claude CLI error: Invalid API key · "
                                           "Please run /login")

    inference.set_backend(LoggedOut())
    inference.reset_spend()
    try:
        client.post("/api/backend/probe")
        b = _await_probe(client)
        assert b["state"] == "failed" and "/login" in b["detail"]
    finally:
        inference.set_backend(None)


def _await_probe(client, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        b = client.get("/api/status").json()["backend"]
        if b["state"] not in ("checking",):
            return b
        time.sleep(0.05)
    raise AssertionError("probe never finished")


# ------------------------------------------------------------------ analyse

def _stub_analyzer(script: Path, out: Path, stems: list[str]) -> list[str]:
    """A stand-in for audio_analyze.py that writes sidecars one at a time.

    The real tool means a GPU, a 3GB model download and minutes per run; none of that
    exercises the thing under test, which is whether the job reports honest progress
    and picks up the result.
    """
    script.write_text(
        "import json, sys, time\n"
        "from pathlib import Path\n"
        "out = Path(sys.argv[1])\n"
        "for stem in sys.argv[2:]:\n"
        "    time.sleep(0.15)\n"
        "    (out / f'{stem}.audio.json').write_text(json.dumps({\n"
        "        'clip': f'{stem}.MP4', 'duration_s': 6.0,\n"
        "        'transcript': [], 'candidates': [],\n"
        "        'summary': {'speech_fraction': 0.0, 'audio_usable': True}}))\n"
        "    print(f'{stem}.MP4: done', flush=True)\n", encoding="utf-8")
    return [sys.executable, str(script), str(out), *stems]


def _wait(client, job, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/analyze/{job}").json()
        if s["state"] != "running":
            return s
        time.sleep(0.1)
    raise AssertionError(f"analysis did not finish: {s}")


def test_analysis_runs_in_app_and_reports_progress(tmp_path, project, monkeypatch):
    import server

    sidecars = tmp_path / "fresh"
    sidecars.mkdir()
    monkeypatch.setattr(server, "analyze_cmd", lambda skip, force: _stub_analyzer(
        tmp_path / "stub.py", sidecars, ["CLIP_A", "CLIP_B", "CLIP_C"]))

    with _fresh(tmp_path, project, sidecars=sidecars) as c:
        assert c.get("/api/status").json()["analysed"] == 0
        start = c.post("/api/analyze", json={"skip": []}).json()
        assert start["total"] == 3
        final = _wait(c, start["job"])
        assert final["state"] == "done" and final["done"] == 3
        assert "CLIP_C.MP4: done" in final["log"]

        # the clips are usable without a restart: status, project and proxies all
        # know about them now
        assert c.get("/api/status").json()["analysed"] == 3
        assert set(c.get("/api/project").json()["clips"]) == {
            "CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"}
        assert (server.STATE["proxy_dir"] / "CLIP_A.mp4").exists()


def test_progress_advances_even_when_the_tool_says_nothing(tmp_path, project,
                                                           monkeypatch):
    """The regression. Progress used to refresh once per line of the child's stdout,
    and `audio_analyze.py` prints without flushing — a pipe holds all of it until
    exit, so a real 12-clip bin sat at 0/12 for two and a half minutes and then jumped
    to done. The count was right and the trigger was wrong, which is indistinguishable
    from a hung job. This stub writes its sidecars but stays silent until it exits.
    """
    import server

    sidecars = tmp_path / "silent"
    sidecars.mkdir()
    script = tmp_path / "silent.py"
    script.write_text(
        "import json, sys, time\n"
        "from pathlib import Path\n"
        "out = Path(sys.argv[1])\n"
        "for stem in sys.argv[2:]:\n"
        "    time.sleep(0.4)\n"
        "    (out / f'{stem}.audio.json').write_text(json.dumps({\n"
        "        'clip': f'{stem}.MP4', 'duration_s': 6.0, 'transcript': [],\n"
        "        'candidates': [], 'summary': {'speech_fraction': 0.0}}))\n"
        "print('all done, at the very end, unflushed')\n", encoding="utf-8")
    monkeypatch.setattr(server, "PROGRESS_TICK_S", 0.05)
    monkeypatch.setattr(server, "analyze_cmd", lambda skip, force: [
        sys.executable, str(script), str(sidecars), "CLIP_A", "CLIP_B", "CLIP_C"])

    with _fresh(tmp_path, project, sidecars=sidecars) as c:
        job = c.post("/api/analyze", json={}).json()["job"]
        seen = set()
        deadline = time.time() + 20
        while time.time() < deadline:
            s = c.get(f"/api/analyze/{job}").json()
            seen.add(s["done"])
            if s["state"] != "running":
                break
            time.sleep(0.05)
        assert seen & {1, 2}, f"never reported partial progress: saw {sorted(seen)}"
        assert _wait(c, job)["done"] == 3


def test_the_preview_stage_reports_its_own_count(tmp_path, project, monkeypatch):
    """Encoding proxies takes an order of magnitude longer than the ASR on a real bin
    (half an hour against two and a half minutes), so it cannot be a bar parked at
    100% with the word "previews" next to it."""
    import server

    sidecars = tmp_path / "withproxies"
    sidecars.mkdir()
    monkeypatch.setattr(server, "analyze_cmd", lambda skip, force: _stub_analyzer(
        tmp_path / "stub3.py", sidecars, ["CLIP_A", "CLIP_B", "CLIP_C"]))
    with _fresh(tmp_path, project, sidecars=sidecars) as c:
        job = c.post("/api/analyze", json={}).json()["job"]
        final = _wait(c, job, timeout=90)
        assert final["proxy_total"] == 3 and final["proxy_done"] == 3


def test_analysis_skips_the_junk_it_is_told_to(tmp_path, project, monkeypatch):
    import server

    sidecars = tmp_path / "skipped"
    sidecars.mkdir()
    seen: dict = {}

    def cmd(skip, force):
        seen["skip"], seen["force"] = skip, force
        return _stub_analyzer(tmp_path / "stub2.py", sidecars, ["CLIP_A", "CLIP_C"])

    monkeypatch.setattr(server, "analyze_cmd", cmd)
    with _fresh(tmp_path, project, sidecars=sidecars) as c:
        start = c.post("/api/analyze", json={"skip": ["CLIP_B"]}).json()
        assert start["total"] == 2, "the skipped clip is not part of the target"
        final = _wait(c, start["job"])
        assert final["state"] == "done" and final["done"] == 2
    assert seen["skip"] == ["CLIP_B"] and seen["force"] is False


def test_a_failed_analysis_is_reported_not_swallowed(tmp_path, project, monkeypatch):
    import server

    monkeypatch.setattr(server, "analyze_cmd", lambda skip, force: [
        sys.executable, "-c", "import sys; print('cuda is unavailable'); sys.exit(2)"])
    with _fresh(tmp_path, project, sidecars=tmp_path / "empty") as c:
        job = c.post("/api/analyze", json={}).json()["job"]
        final = _wait(c, job)
        assert final["state"] == "failed"
        assert "cuda is unavailable" in final["log"]


def test_only_one_analysis_at_a_time(tmp_path, project, monkeypatch):
    import server

    monkeypatch.setattr(server, "analyze_cmd", lambda skip, force: [
        sys.executable, "-c", "import time; time.sleep(1.5)"])
    with _fresh(tmp_path, project, sidecars=tmp_path / "busy") as c:
        job = c.post("/api/analyze", json={}).json()["job"]
        assert c.post("/api/analyze", json={}).status_code == 409
        _wait(c, job)


def test_analyze_status_404_for_unknown_job(client):
    assert client.get("/api/analyze/nope").status_code == 404


# ------------------------------------------------------------------ static

def test_index_and_script_served(client):
    assert "Cut board" in client.get("/").text
    js = client.get("/app.js")
    assert js.status_code == 200
    assert "function render" in js.text


# ------------------------------------------------------------------ the visual pass

def _stub_looker(script: Path, out: Path, stems: list[str]) -> list[str]:
    """A stand-in for visual_pass.py that writes one visual sidecar per clip, slowly.
    The real tool spends a model call per contact sheet; none of that exercises the
    job plumbing, which is what is under test."""
    script.write_text(
        "import json, sys, time\n"
        "from pathlib import Path\n"
        "out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)\n"
        "for stem in sys.argv[2:]:\n"
        "    time.sleep(0.15)\n"
        "    (out / f'{stem}.visual.json').write_text(json.dumps({\n"
        "        'clip': f'{stem}.MP4',\n"
        "        'moments': [{'start': 1.0, 'end': 3.0, 'what': 'rider goes down hard',\n"
        "                     'kind': 'fall', 'notable': True}],\n"
        "        'unusable': [{'start': 0.0, 'end': 0.5, 'why': 'lens covered'}],\n"
        "        'summary': 'a run', 'projected_usd': 0.09}))\n"
        "    print(f'{stem}.MP4: 1 moments', flush=True)\n", encoding="utf-8")
    return [sys.executable, str(script), str(out), *stems]


def _wait_visual(client, job, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/visual/{job}").json()
        if s["state"] != "running":
            return s
        time.sleep(0.1)
    raise AssertionError(f"visual pass did not finish: {s}")


def test_the_visual_pass_is_priced_before_it_is_offered(tmp_path, project):
    """It spends model calls, so the status says what the rest of the bin would cost —
    and nothing starts on the app's own initiative."""
    import server

    with _fresh(tmp_path, project, sidecars=project["sidecars"], visual=None) as c:
        v = c.get("/api/status").json()["visual"]
        assert v["done"] == 0 and v["total"] == 3
        assert v["pending"] == ["CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"]
        assert v["calls"] == 3                         # one sheet each, for 6s clips
        assert v["projected_usd"] == pytest.approx(3 * server.VISUAL_USD_PER_SHEET)
        assert v["running"] is False
        # per-bin, like proxies and renders, so two bins' sidecars never mix
        assert Path(v["dir"]) == tmp_path / "visual" / project["footage"].name
    assert server.VISUALS == {}


def test_the_visual_pass_runs_in_app_and_what_it_saw_reaches_the_board(
        tmp_path, project, monkeypatch):
    import server

    monkeypatch.setattr(server, "PROGRESS_TICK_S", 0.05)
    seen: dict = {}

    def cmd(only, force):
        seen["only"], seen["force"] = only, force
        return _stub_looker(tmp_path / "look.py", server.STATE["visual"],
                            ["CLIP_A", "CLIP_B", "CLIP_C"])

    monkeypatch.setattr(server, "visual_cmd", cmd)
    with _fresh(tmp_path, project, sidecars=project["sidecars"], visual=None) as c:
        start = c.post("/api/visual", json={}).json()
        assert start["total"] == 3
        counts = set()
        deadline = time.time() + 20
        while time.time() < deadline:
            s = c.get(f"/api/visual/{start['job']}").json()
            counts.add(s["done"])
            assert s["elapsed_s"] >= 0
            if s["state"] != "running":
                break
            time.sleep(0.05)
        assert s["state"] == "done" and s["done"] == 3, s
        assert counts & {1, 2}, f"never reported partial progress: {sorted(counts)}"
        assert "CLIP_C.MP4: 1 moments" in s["log"]
        assert seen["only"] == ["CLIP_A", "CLIP_B", "CLIP_C"] and seen["force"] is False

        # without a restart: the board sees what was seen, and status stops offering it
        clip = c.get("/api/project").json()["clips"]["CLIP_A.MP4"]
        assert clip["visual"]["moments"][0]["kind"] == "fall"
        assert clip["visual"]["unusable"][0]["why"] == "lens covered"
        v = c.get("/api/status").json()["visual"]
        assert v["done"] == 3 and v["pending"] == [] and v["projected_usd"] == 0
        # a second run has nothing to do unless forced
        assert c.post("/api/visual", json={}).status_code == 400


def test_one_visual_pass_at_a_time_and_a_failure_is_reported(tmp_path, project,
                                                             monkeypatch):
    import server

    monkeypatch.setattr(server, "visual_cmd", lambda only, force: [
        sys.executable, "-c",
        "import sys, time; time.sleep(0.8); print('no sheet could be read'); sys.exit(3)"])
    with _fresh(tmp_path, project, sidecars=project["sidecars"], visual=None) as c:
        job = c.post("/api/visual", json={}).json()["job"]
        assert c.post("/api/visual", json={}).status_code == 409
        final = _wait_visual(c, job)
        assert final["state"] == "failed"
        assert "no sheet could be read" in final["log"]
        assert c.get("/api/visual/nope").status_code == 404


# ------------------------------------------------------------------ music in the board

def _library(tmp_path) -> Path:
    """An asset library with one 2s track in it."""
    lib = tmp_path / "assets"
    (lib / "music").mkdir(parents=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi",
                    "-i", "sine=frequency=900:duration=2",
                    str(lib / "music" / "bed.wav")], check=True)
    return lib


def test_assets_are_listed_with_what_a_bed_needs_to_know(tmp_path, project):
    """A track's loudness is what sets the bed level — the first music render was
    inaudible because a raw gain met a track nobody had measured."""
    lib = _library(tmp_path)
    with _fresh(tmp_path, project, sidecars=project["sidecars"], assets=lib) as c:
        a = c.get("/api/assets").json()
        assert [t["asset"] for t in a["music"]] == ["music/bed.wav"]
        track = a["music"][0]
        assert track["duration_s"] == pytest.approx(2.0, abs=0.1)
        assert isinstance(track["lufs"], float)
        assert a["sfx"] == [] and a["overlay"] == []
        # served over the same range-capable route as the proxies, so the board can
        # play it under the cut
        assert c.get(track["url"], headers={"Range": "bytes=0-99"}).status_code == 206
        assert c.get("/media/asset/music/nope.wav").status_code == 404
        assert c.get("/media/asset/etc/passwd").status_code == 404


def test_music_is_saved_into_the_edl_validated_and_rendered(tmp_path, project):
    """The board writes `effects_music` — the key assemble.py already reads — so a
    render picks the bed up with nothing else to do. Validated like a segment: an asset
    that is not in the library, or a gain outside range, is a 400 and the EDL is not
    touched."""
    lib = _library(tmp_path)
    original = project["edl"].read_text(encoding="utf-8")
    try:
        with _fresh(tmp_path, project, edl=project["edl"], sidecars=project["sidecars"],
                    assets=lib) as c:
            segs = [{"clip": "CLIP_A.MP4", "in": 0.0, "out": 3.0, "why": "a"}]
            r = c.put("/api/project", json={
                "segments": segs, "story": "",
                "music": {"asset": "music/bed.wav", "duck_db": 10,
                          "fade_in": 0.5, "fade_out": 1.0}})
            assert r.status_code == 200, r.text
            on_disk = json.loads(project["edl"].read_text(encoding="utf-8"))
            music = on_disk["effects_music"]
            assert music["asset"] == "music/bed.wav" and music["duck"] is True
            assert music["duck_db"] == 10 and music["fade_in"] == 0.5
            assert "gain_db" not in music, "left for the render to measure"
            assert c.get("/api/project").json()["music"]["asset"] == "music/bed.wav"

            # a save that does not mention music leaves it alone
            c.put("/api/project", json={"segments": segs, "story": "x"})
            assert "effects_music" in json.loads(project["edl"].read_text(encoding="utf-8"))
            # and a bad one changes nothing
            bad = c.put("/api/project", json={"segments": segs, "story": "",
                                              "music": {"asset": "music/missing.wav"}})
            assert bad.status_code == 400 and "missing.wav" in bad.json()["detail"]
            bad = c.put("/api/project", json={"segments": segs, "story": "",
                                              "music": {"asset": "music/bed.wav",
                                                        "gain_db": 40}})
            assert bad.status_code == 400
            still = json.loads(project["edl"].read_text(encoding="utf-8"))
            assert still["effects_music"]["asset"] == "music/bed.wav"

            # the render carries the bed, keyed on the transcript's speech
            job = c.post("/api/render", json={"segments": segs}).json()["job"]
            deadline = time.time() + 120
            while (s := c.get(f"/api/render/{job}").json())["state"] == "running":
                assert time.time() < deadline, "render timed out"
                time.sleep(0.5)
            assert s["state"] == "done", s["log"][-600:]
            assert "music: bed.wav" in s["log"] and "keyed on speech" in s["log"]
            assert _duration(Path(s["output"])) == pytest.approx(3.0, abs=0.35)
            assert c.get("/api/renders").json()["renders"][0]["music"] == "music/bed.wav"

            # null takes it out again
            c.put("/api/project", json={"segments": segs, "story": "", "music": None})
            assert "effects_music" not in json.loads(
                project["edl"].read_text(encoding="utf-8"))
    finally:
        project["edl"].write_text(original, encoding="utf-8")
