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


def test_render_status_404_for_unknown_job(client):
    assert client.get("/api/render/deadbeef").status_code == 404


# ------------------------------------------------------------------ ask

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
        r = client.post("/api/ask", json={
            "note": "use the clip that isn't in the cut",
            "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0}],
            "story": "a test film"})
        assert r.status_code == 200, r.text
        plan = r.json()
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
        r = client.post("/api/ask", json={"note": "", "segments": [],
                                          "story": "two people talking"})
        assert r.status_code == 200, r.text
        plan = r.json()
        assert len(plan["segments"]) == 2
        assert plan["notes"] == "read it as a conversation"
        prompt = Scripted.seen[0].prompt
        assert "There is no edit yet" in prompt
        assert "two people talking" in prompt and "hello there" in prompt
    finally:
        inference.set_backend(None)

    # still a proposal, not a write
    assert json.loads(project["edl"].read_text(encoding="utf-8"))["segments"] != []


def test_first_cut_without_any_analysis_says_so(tmp_path, project):
    """Distinct from a backend failure: there is nothing to cut from yet."""
    with _fresh(tmp_path, project, sidecars=tmp_path / "none") as c:
        r = c.post("/api/ask", json={"note": "make me something", "segments": []})
        assert r.status_code == 400
        assert "audio pass" in r.json()["detail"]


def test_ask_surfaces_backend_failure_as_502(client):
    from roughcut import inference

    class Broken:
        name = "broken"

        def complete(self, request):
            raise inference.InferenceError("claude CLI error: Not logged in")

    inference.set_backend(Broken())
    inference.reset_spend()
    try:
        r = client.post("/api/ask", json={"note": "tighten it"})
        assert r.status_code == 502
        assert "Not logged in" in r.json()["detail"]
    finally:
        inference.set_backend(None)


# ------------------------------------------------------------ new project

def _fresh(tmp_path, project, **kw):
    """Configure the server the way `main()` does for a bin nobody has cut yet."""
    from fastapi.testclient import TestClient
    import server

    server.configure(kw.pop("edl", None), project["footage"],
                     kw.pop("sidecars", None), tmp_path, proxies=False, **kw)
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
