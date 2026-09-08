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
import threading
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
        # seen[-1]: the plan call is the last one, behind the cheap estimate call
        # that now opens every ask.
        assert "rider goes down in deep snow" in Scripted.seen[-1].prompt
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


def test_media_suffix_range_returns_the_end_of_the_file(client):
    """`bytes=-500` is the last 500 bytes, not the first 501. Read the wrong way it is
    a silently wrong answer, and the request a player makes when it has to hunt for a
    moov atom at the end of a file that was not written with +faststart."""
    full = client.get("/media/proxy/CLIP_A.mp4").content
    r = client.get("/media/proxy/CLIP_A.mp4", headers={"Range": "bytes=-500"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes {len(full) - 500}-{len(full) - 1}/{len(full)}"
    assert r.content == full[-500:]


def test_a_large_open_ended_range_is_never_read_whole(client, tmp_path):
    """`bytes=0-` is the first thing every <video> sends, and answering it with
    `fh.read(size)` put the whole file in memory before a byte left the server: one load
    of the 16-shot Killington board took its RSS from 187 MB to 674 MB, for files it only
    ever had to copy. Twenty megabytes here, produced a megabyte at a time."""
    import tracemalloc
    import server

    size = 20 << 20
    big = tmp_path / "big.bin"
    with big.open("wb") as fh:
        for i in range(size >> 20):
            fh.write(bytes([i % 256]) * (1 << 20))

    # the generator itself: producing the first chunk must not cost the whole file
    gen = server.iter_range(big, 0, size - 1)
    tracemalloc.start()
    first = next(gen)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    gen.close()
    assert len(first) == server.RANGE_CHUNK
    assert first == bytes([0]) * server.RANGE_CHUNK
    assert peak < 4 * server.RANGE_CHUNK, \
        f"{peak / 1e6:.1f} MB to produce the first megabyte of a {size / 1e6:.0f} MB file"

    # and what the route hands back is that stream rather than a body. Asserted on the
    # response object, not through TestClient, which reassembles the whole body itself
    # and would measure the harness instead of the server.
    import asyncio

    from fastapi import Request
    from fastapi.responses import StreamingResponse

    resp = server.ranged_file(big, Request(
        {"type": "http", "method": "GET", "path": "/", "query_string": b"",
         "headers": [(b"range", b"bytes=0-")]}))
    assert isinstance(resp, StreamingResponse)
    assert resp.status_code == 206
    assert resp.headers["content-range"] == f"bytes 0-{size - 1}/{size}"
    assert resp.headers["content-length"] == str(size)

    async def take_two():
        out = []
        async for c in resp.body_iterator:
            out.append(len(c))
            if len(out) == 2:
                break
        await resp.body_iterator.aclose()
        return out

    tracemalloc.start()
    got = asyncio.run(take_two())
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert got == [server.RANGE_CHUNK, server.RANGE_CHUNK]
    assert peak < 6 * server.RANGE_CHUNK, \
        f"{peak / 1e6:.1f} MB to take 2 MB off the front of a {size / 1e6:.0f} MB file"

    # end to end, the bytes still have to be right
    shutil.copy(big, Path(server.STATE["proxy_dir"]) / "BIG.bin")
    try:
        r = client.get("/media/proxy/BIG.bin", headers={"Range": "bytes=0-"})
        assert r.status_code == 206 and len(r.content) == size
        assert r.content == big.read_bytes()
        tail = client.get("/media/proxy/BIG.bin",
                          headers={"Range": f"bytes={size - 3}-"})
        assert tail.content == bytes([(size >> 20) - 1]) * 3
    finally:
        (Path(server.STATE["proxy_dir"]) / "BIG.bin").unlink(missing_ok=True)


def test_media_unknown_proxy_404s(client):
    assert client.get("/media/proxy/NOPE.mp4").status_code == 404


def test_media_path_traversal_is_contained(client):
    """Only the basename is used, so a traversal attempt resolves inside proxy_dir."""
    r = client.get("/media/proxy/..%2F..%2Fetc%2Fpasswd")
    assert r.status_code == 404


# ------------------------------------------------------------------ posters

def test_poster_is_a_small_jpeg_of_the_frame_at_that_time(client, project):
    """A shot card gets a picture, not a stream. Sixteen cards each holding open a
    720p proxy took every connection Chrome allows and the monitor's own request
    queued behind them: on the Killington board it played sound over a black screen
    for 6.8-9.5 s. A poster is a few KB."""
    import server
    r = client.get("/media/poster/CLIP_A.jpg", params={"t": 2.5})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content[:2] == b"\xff\xd8", "not a JPEG"
    assert len(r.content) < 60_000, f"a card's picture should be small: {len(r.content)}"
    assert (server.STATE["posters"] / "CLIP_A@000002.50.jpg").exists()


def test_poster_at_a_different_time_is_a_different_picture(client):
    """The card follows the in-point, so the URL has to carry the time."""
    a = client.get("/media/poster/CLIP_A.jpg", params={"t": 0.5}).content
    b = client.get("/media/poster/CLIP_A.jpg", params={"t": 4.5}).content
    assert a and b and a != b


def test_poster_is_cached_on_disk_and_declared_immutable(client, project):
    """One clip at one timestamp is one frame forever, so the browser may keep it and
    so may the disk — re-rendering the board must not re-run ffmpeg once per card."""
    import server
    dest = server.STATE["posters"] / "CLIP_B@000003.00.jpg"
    dest.unlink(missing_ok=True)
    r = client.get("/media/poster/CLIP_B.jpg", params={"t": 3.0})
    assert r.status_code == 200
    assert dest.exists()
    stamp = dest.stat().st_mtime_ns
    cache = r.headers.get("cache-control", "")
    assert "immutable" in cache and "max-age" in cache, cache
    again = client.get("/media/poster/CLIP_B.jpg", params={"t": 3.0})
    assert again.content == r.content
    assert dest.stat().st_mtime_ns == stamp, "rebuilt a poster it already had"


def test_poster_past_the_end_of_a_clip_falls_back_to_its_first_frame(client):
    """A trim can park an in-point past the end of a clip; a card showing frame one
    beats a card showing a broken image."""
    head = client.get("/media/poster/CLIP_A.jpg", params={"t": 0.0})
    late = client.get("/media/poster/CLIP_A.jpg", params={"t": 9999.0})
    assert late.status_code == 200
    assert late.content == head.content


def test_poster_for_an_unbuilt_proxy_404s_rather_than_inventing_one(client):
    assert client.get("/media/poster/NOPE.jpg").status_code == 404


def test_poster_path_traversal_is_contained(client):
    """Same containment as the other media routes: the name names a stem in proxy_dir."""
    for name in ("..%2F..%2Fetc%2Fpasswd", "%2e%2e%2f%2e%2e%2fCLIP_A.jpg", "....jpg"):
        assert client.get(f"/media/poster/{name}").status_code == 404


def test_poster_time_is_validated(client):
    """A negative time is clamped rather than handed to ffmpeg; a non-number is a 422."""
    assert (client.get("/media/poster/CLIP_A.jpg", params={"t": -5}).content
            == client.get("/media/poster/CLIP_A.jpg", params={"t": 0}).content)
    assert client.get("/media/poster/CLIP_A.jpg", params={"t": "soon"}).status_code == 422


def test_project_offers_a_poster_url_per_clip(client):
    clips = client.get("/api/project").json()["clips"]
    assert clips["CLIP_A.MP4"]["poster"] == "/media/poster/CLIP_A.jpg"


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


def _rendered(client, segs) -> dict:
    """Render and wait, returning the finished row from /api/renders."""
    job = client.post("/api/render", json={"segments": segs}).json()["job"]
    deadline = time.time() + 120
    while client.get(f"/api/render/{job}").json()["state"] == "running":
        assert time.time() < deadline, "render timed out"
        time.sleep(0.3)
    assert client.get(f"/api/render/{job}").json()["state"] == "done"
    name = f"cut_{job}.mp4"
    return next(r for r in client.get("/api/renders").json()["renders"]
                if r["name"] == name)


def _review_ready(client, name: str, timeout: float = 120) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = next(r for r in client.get("/api/renders").json()["renders"]
                   if r["name"] == name)
        if row["review_state"] != "building":
            return row
        time.sleep(0.3)
    raise AssertionError(f"review copy for {name} never finished")


def test_a_finished_render_gets_a_review_copy_and_the_players_play_that(client, project):
    """Karl, watching the 4K delivery render in the board: *"they seem to get stuck in
    this loading forever place and also only have played for like 3s before video
    buffers / pauses."* The file was fine — 5427 frames at a clean 1/29.97 — it was
    987 MB of 4K at 43.6 Mbps, which no browser streams off this box: measured, one
    player pulled 157 MB in 15 s of watching against 5 MB for the review copy. So the
    players get a 720p copy and the master stays for the download."""
    import server
    row = _rendered(client, [{"clip": "CLIP_A.MP4", "in": 0.5, "out": 3.0, "why": "a"}])
    assert row["review_state"] in ("building", "ready")
    row = _review_ready(client, row["name"])
    assert row["review_state"] == "ready"
    copy = server.review_path(row["name"])
    assert copy.exists()
    assert client.get(row["review_url"],
                      headers={"range": "bytes=0-99"}).status_code == 206
    # the same cut, capped at the proxy width: a render comes out at 1920x1080 and
    # the copy the player streams is 1280x720
    assert _duration(copy) == pytest.approx(2.5, abs=0.35)
    w, h = server.probe_resolution(copy)
    assert (w, h) == (1280, 720) and w <= server.PROXY_W
    assert server.probe_resolution(server.STATE["renders"] / row["name"]) == (1920, 1080)
    # the master is untouched: it is what the download serves
    assert (server.STATE["renders"] / row["name"]).exists()


def test_a_review_copy_that_cannot_be_made_falls_back_to_the_master(client, project,
                                                                    monkeypatch):
    """Nothing in the versions list may become unplayable because a derived file is
    missing. A failure is reported once and the row keeps its master URL."""
    import server
    row = _rendered(client, [{"clip": "CLIP_B.MP4", "in": 0.0, "out": 1.5, "why": "b"}])
    _review_ready(client, row["name"])
    server.review_path(row["name"]).unlink(missing_ok=True)
    with server.REVIEW_LOCK:
        server.REVIEW_STATE.pop(row["name"], None)

    def explode(src, dest):
        raise RuntimeError("no ffmpeg for you")

    monkeypatch.setattr(server, "build_review", explode)
    again = _review_ready(client, row["name"])
    assert again["review_state"] == "failed"
    assert again["url"] == f"/media/render/{row['name']}"
    assert client.get(again["url"], headers={"range": "bytes=0-99"}).status_code == 206
    # and it is not retried on every poll while it keeps failing
    assert _review_ready(client, row["name"])["review_state"] == "failed"


def test_a_render_can_be_downloaded_under_a_name_worth_having(client, project):
    """Karl: *"Make it clear how to download the renders."* The board's only render
    URL is served inline, so clicking it played the file in a tab rather than saving
    it, and `cut_110ecb13.mp4` says nothing on a desktop full of downloads."""
    row = _rendered(client, [{"clip": "CLIP_A.MP4", "in": 0.0, "out": 2.0, "why": "a"},
                             {"clip": "CLIP_B.MP4", "in": 0.0, "out": 1.0, "why": "b"}])
    r = client.get(row["download_url"], headers={"range": "bytes=0-99"})
    assert r.status_code == 206
    disp = r.headers["content-disposition"]
    assert disp.startswith("attachment;"), disp
    assert row["download_name"] in disp, (disp, row["download_name"])
    # bin, shots, duration, quality — enough to know what it is a year later
    assert row["download_name"].startswith(project["footage"].name)
    assert "2shots" in row["download_name"]
    assert "0m03" in row["download_name"]
    assert row["download_name"].endswith(".mp4")
    assert r.headers["content-type"] == "video/mp4"


def test_download_of_an_unknown_render_404s_and_cannot_escape_the_folder(client):
    assert client.get("/media/download/render/nope.mp4").status_code == 404
    assert client.get(
        "/media/download/render/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert client.get("/media/review/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_every_version_says_how_big_it_is_and_at_what_size(client, project):
    """A row that offers a download has to say what you are about to download; 987 MB
    is worth knowing before the click. Renders made before the profile field existed
    carry no dimensions, so they are probed rather than left blank."""
    import server
    row = _rendered(client, [{"clip": "CLIP_C.MP4", "in": 0.0, "out": 1.0, "why": "c"}])
    assert row["size"] > 0
    assert (row["width"], row["height"]) == (1920, 1080)
    # an old render with no metadata sidecar at all still reports its size
    meta = (server.STATE["renders"] / row["name"]).with_suffix(".json")
    meta.unlink()
    bare = next(r for r in client.get("/api/renders").json()["renders"]
                if r["name"] == row["name"])
    assert (bare["width"], bare["height"]) == (1920, 1080)
    assert bare["size"] == row["size"]
    assert bare["download_name"].endswith("1080p.mp4")


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
    rather than a spinner.

    Driven, not sampled. This used to poll a real three-shot render every 50 ms and
    then assert it had *caught* the counting; the shots are 1.5-2 s of 6-second clips,
    so under load the whole render could land between two polls and the test would see
    neither "cutting" nor a non-zero count — green five runs out of six. The stand-in
    for `assemble.py` below cuts its next shot only once the board has been seen
    reporting the last one, so every state asserted here is waited for instead of
    raced, and the assertions get stronger rather than weaker: all three counts and
    all three "cutting shot N of 3" lines, not "at least one of something". The real
    encoder is what every other render in this file runs.
    """
    import server
    monkeypatch.setattr(server, "PROGRESS_TICK_S", 0.05)
    # The lines the board must show, in order, each one a checkpoint the render waits
    # on. `advance` names the shot it is starting; the last part on disk means joining.
    wanted = ["cutting shot 2 of 3", "cutting shot 3 of 3", "joining 3 shots"]
    acked = [threading.Event() for _ in wanted]
    src = server.STATE["proxy_dir"] / "CLIP_A.mp4"
    real_run = subprocess.run

    def paced_assemble(cmd, *args, **kw):
        if "assemble.py" not in " ".join(str(c) for c in cmd):
            return real_run(cmd, *args, **kw)      # probes and the review copy, for real
        parts = Path(cmd[cmd.index("--parts-dir") + 1])
        out = Path(cmd[cmd.index("-o") + 1])
        parts.mkdir(parents=True, exist_ok=True)
        for i, ack in enumerate(acked):
            shutil.copy(src, parts / f"part_{i:03d}.mp4")
            if not ack.wait(60):
                return subprocess.CompletedProcess(
                    cmd, 1, "", f"the board never said {wanted[i]!r}")
        shutil.copy(src, out)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(server.subprocess, "run", paced_assemble)
    segs = [{"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.5, "why": "a"},
            {"clip": "CLIP_B.MP4", "in": 1.0, "out": 2.0, "why": "b"},
            {"clip": "CLIP_C.MP4", "in": 0.0, "out": 1.5, "why": "c"}]
    job = client.post("/api/render", json={"segments": segs}).json()["job"]

    stages, counts, said = set(), set(), set()
    deadline = time.time() + 120
    while time.time() < deadline:
        s = client.get(f"/api/render/{job}").json()
        stages.add(s["stage"])
        counts.add(s["done"])
        said.add(s["detail"])
        assert s["total"] == 3
        assert s["elapsed_s"] >= 0
        # Release the next shot only once this one has been read off the API, which
        # is what makes the states below observations rather than lucky timing.
        for want, ack in zip(wanted, acked):
            if s["detail"] == want:
                ack.set()
        if s["state"] != "running":
            break
        time.sleep(0.02)
    assert s["state"] == "done", s["log"][-400:]
    assert "cutting" in stages
    assert set(wanted) <= said, f"never said: {sorted(set(wanted) - said)}"
    assert {1, 2, 3} <= counts, f"never showed progress: {sorted(counts)}"
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


def _running_renders():
    import server
    from roughcut import progress
    return [j for j in server.RENDERS.values()
            if j["state"] not in progress.TERMINAL]


def _wait_render(client, job, timeout: float = 120):
    deadline = time.time() + timeout
    while client.get(f"/api/render/{job}").json()["state"] == "running":
        assert time.time() < deadline, "render timed out"
        time.sleep(0.2)
    return client.get(f"/api/render/{job}").json()


def test_only_one_render_at_a_time(client, project):
    """Karl: "can hitting the button multiple times break the system state of the
    render?" It cannot corrupt anything — every job has its own id, parts directory
    and output file, so two renders write two files and neither touches the other.
    What it does is make both crawl: his delivery render took ~17 minutes with the
    machine otherwise idle, and two 4K encodes share the same cores. Analyse and the
    visual pass have refused a second job all along; this is the same rule."""
    body = {"segments": [{"clip": "CLIP_A.MP4", "in": 0.0, "out": 2.0, "why": "a"}]}
    plans_before = len(list(project["work"].glob("render_*.json")))
    job = client.post("/api/render", json=body).json()["job"]
    second = client.post("/api/render", json=body)
    assert second.status_code == 409
    # named, so the refusal points at what is running rather than just scolding
    assert job in second.json()["detail"], second.json()
    # and refused before anything was started or written for it
    assert len(_running_renders()) == 1
    assert len(list(project["work"].glob("render_*.json"))) == plans_before + 1

    done = _wait_render(client, job)                   # the first one lands untouched
    assert done["state"] == "done", done["log"][-400:]
    assert Path(done["output"]).name == f"cut_{job}.mp4"
    # once it is over the button works again
    again = client.post("/api/render", json=body)
    assert again.status_code == 200
    _wait_render(client, again.json()["job"])


def test_renders_from_before_the_profile_existed_still_list_cleanly(client, project):
    """Old metadata on disk has neither `profile` nor `width`/`height` — it must
    read as the preview render it always was, never crash the versions list, and
    never be mistaken for a delivery render.

    The dimensions used to come back as null, which was honest but useless once the
    row started saying what you are about to download. They are probed off the file
    now — measured, not invented, so an old preview still cannot read as 4K."""
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
    assert (entry["width"], entry["height"]) == (1920, 1080)
    assert "1080p" in entry["download_name"] and "4K" not in entry["download_name"]


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
    """Wait for an ask to stop. It opens in `estimating` — the cheap call that sizes
    the job runs before the expensive one that does it — so "not running" stopped
    meaning "finished" the day the shared progress model landed."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/ask/{job}").json()
        if s["state"] not in ("estimating", "running"):
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
        # The plan comes back polished: 4.0 is the end of "you", and an out-point
        # sitting on the last word is heard as cutting through it, so it gains a
        # tail pad and says where it came from.
        assert plan["segments"] == [
            {"clip": "CLIP_C.MP4", "in": 0.5, "out": 4.45, "why": "per the note",
             "polished_from": [0.5, 4.0],
             "polish_why": "out +0.45s to finish 'you'"}]
        assert plan["notes"].startswith("swapped in the unused clip")
        assert plan["usage"]["projected_usd"] > 0
        # the model was given the transcripts and the note (the last call is the
        # plan; the one before it is the estimate that draws the progress bar)
        prompt = Scripted.seen[-1].prompt
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
        # An originated cut is polished on the same terms as a revision — the
        # first cut has the same clipped words as every later one — and the
        # adjustment is declared in the notes rather than applied silently.
        assert plan["notes"].startswith("read it as a conversation")
        assert "Boundary polish adjusted 2 of 2 shots" in plan["notes"]
        assert [s["out"] for s in plan["segments"]] == [2.45, 4.45]
        prompt = Scripted.seen[-1].prompt
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
        assert client.get(f"/api/ask/{job}").json()["state"] in (
            "estimating", "running")
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


# ------------------------------------------------------------------ shot-scoped ask

def test_shot_ask_replaces_only_the_focused_shot(client, project):
    """A note about one shot: the model answers for that shot alone, the server
    splices, and the proposal the human reads is the whole timeline — every other
    shot verbatim."""
    from roughcut import config, inference

    class Scripted:
        name = "scripted"
        seen: list = []

        def complete(self, request):
            Scripted.seen.append(request)
            text = json.dumps({
                "segments": [{"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0,
                              "why": "starts on the line now"},
                             {"clip": "CLIP_A.MP4", "in": 5.0, "out": 5.6,
                              "why": "and the goodbye split out"}],
                "notes": "split it as asked"})
            model = config.model_for(request.role)
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend="scripted", model=model,
                                    projected_usd=1e-4, latency_ms=1, raw=text)

    inference.set_backend(Scripted())
    inference.reset_spend()
    try:
        plan = _ask(client, {
            "note": "split this into the line and the goodbye",
            "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
                         {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}],
            "story": "a test film", "focus": 0})
        # the replacement, then the untouched second shot, in order
        assert [s["clip"] for s in plan["segments"]] == [
            "CLIP_A.MP4", "CLIP_A.MP4", "CLIP_B.MP4"]
        assert plan["segments"][2] == {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0,
                                       "why": "second"}
        assert plan["focus"] == {"index": 0, "clip": "CLIP_A.MP4", "in": 1.0,
                                 "out": 3.0, "with": 2}
        # the replacement is polished like any proposal; the untouched shot is not
        assert plan["segments"][1]["out"] == pytest.approx(5.6 + 0.45, abs=0.06)
        # one call, no estimate ahead of it — the scoped prompt carries the film,
        # the note, the focused clip's transcript and the marker
        assert len(Scripted.seen) == 1
        prompt = Scripted.seen[-1].prompt
        assert "split this into the line and the goodbye" in prompt
        assert "the shot this note is about" in prompt
        assert "hello there" in prompt
        assert "Every segment must come from CLIP_A.MP4" in prompt
    finally:
        inference.set_backend(None)


def test_shot_ask_is_confined_to_the_shots_clip(client):
    """The scoped call may only cut from the clip the note is about — a model that
    wanders to another clip fails loudly rather than proposing blind."""
    from roughcut import config, inference

    class Wandering:
        name = "scripted"

        def complete(self, request):
            text = json.dumps({"segments": [
                {"clip": "CLIP_C.MP4", "in": 0.0, "out": 2.0, "why": "a swap"}]})
            model = config.model_for(request.role)
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend="scripted", model=model,
                                    projected_usd=1e-4, latency_ms=1, raw=text)

    inference.set_backend(Wandering())
    inference.reset_spend()
    try:
        job = client.post("/api/ask", json={
            "note": "swap this for something better",
            "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0}],
            "focus": 0}).json()["job"]
        s = _ask_status(client, job)
        assert s["state"] == "failed" and s["code"] == 502
        assert "unknown clip" in s["detail"]
    finally:
        inference.set_backend(None)


def test_shot_ask_rejects_a_bad_focus(client):
    segs = [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0}]
    r = client.post("/api/ask", json={"note": "x", "segments": segs, "focus": 5})
    assert r.status_code == 400 and "no shot 6" in r.json()["detail"]
    r = client.post("/api/ask", json={"note": "x", "segments": [], "focus": 0})
    assert r.status_code == 400
    r = client.post("/api/ask", json={"note": "x", "segments": segs,
                                      "focus": "not-a-number"})
    assert r.status_code == 400


# ------------------------------------------------------- the progress model

PLAN_TEXT = json.dumps({
    "segments": [{"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0, "why": "one"},
                 {"clip": "CLIP_B.MP4", "in": 0.5, "out": 2.0, "why": "two"},
                 {"clip": "CLIP_C.MP4", "in": 0.5, "out": 2.0, "why": "three"}],
    "notes": "three shots"})

ESTIMATE_TEXT = json.dumps({
    "eta_s": 120,
    "milestones": [{"key": "read", "label": "reading the footage", "weight": 1},
                   {"key": "think", "label": "working out the shape", "weight": 2},
                   {"key": "shots", "label": "choosing the shots", "weight": 4},
                   {"key": "notes", "label": "writing its reasoning", "weight": 1},
                   {"key": "polish", "label": "snapping to speech", "weight": 1}]})


class _Streaming:
    """A scripted backend that answers the estimate call, then *streams* the plan.

    The point of the stream is that a `claude -p` call otherwise reports nothing until
    it ends; this fakes the deltas so the job's milestones can be watched advancing
    without a live call.
    """
    name = "scripted-stream"

    def __init__(self, estimate_text=ESTIMATE_TEXT, plan_text=PLAN_TEXT):
        self.estimate_text, self.plan_text = estimate_text, plan_text
        self.seen = []

    def complete(self, request):
        from roughcut import config as cfg, inference
        self.seen.append(request)
        first = request.role == cfg.ROLE_ANALYSIS
        text = self.estimate_text if first else self.plan_text
        # A real estimate call is seconds, not microseconds, and the state it puts the
        # job in is one the test is here to observe.
        time.sleep(0.2 if first else 0.0)
        if request.on_partial is not None:
            request.on_partial("thinking", "considering the material")
            time.sleep(0.15)
            for cut in (60, 130, 200, len(text)):
                request.on_partial("text", text[:cut])
                time.sleep(0.15)
        model = cfg.model_for(request.role)
        return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                backend=self.name, model=model, projected_usd=1e-4,
                                latency_ms=1, raw=text)


def _watch(client, job, path="/api/ask"):
    """Poll a job to the end, keeping every snapshot."""
    seen, deadline = [], time.time() + 30
    while time.time() < deadline:
        s = client.get(f"{path}/{job}").json()
        seen.append(s)
        if s["state"] not in ("estimating", "running"):
            return seen
        time.sleep(0.03)
    raise AssertionError(f"job never finished: {seen[-1]}")


def test_an_ask_estimates_itself_then_reports_milestones(client):
    """Karl: "the first step the AI must complete is an estimate of how long it will
    take to apply the changes. This will (when estimate is complete) start a progress
    bar... The initial assessment must include milestones, which the agent will
    complete, and then follow back up with the app on."

    All of it, end to end: the cheap call goes first, the bar appears with its
    checkpoints, and the checkpoints are then completed from what the model has
    actually written rather than from a clock."""
    from roughcut import inference

    backend = _Streaming()
    inference.set_backend(backend)
    inference.reset_spend()
    try:
        job = client.post("/api/ask", json={"note": "tighten it"}).json()["job"]
        seen = _watch(client, job)
    finally:
        inference.set_backend(None)

    states = [s["state"] for s in seen]
    assert states[0] == "estimating", states[:3]
    assert "running" in states and states[-1] == "done", states
    # the estimate arrived before the work, and it is the model's
    final = seen[-1]
    assert final["estimate"]["source"] == "model"
    assert final["estimated_s"] == 120.0
    assert [m["key"] for m in final["milestones"]] == [
        "read", "think", "shots", "notes", "polish"]
    assert all(m["done_at"] for m in final["milestones"]), final["milestones"]

    # the bar only ever went forwards, and only reached 100 at the end
    pcts = [s["pct"] for s in seen]
    assert pcts == sorted(pcts), pcts
    assert max(p for s, p in zip(states, pcts) if s != "done") < 100.0
    assert pcts[-1] == 100.0
    # and it moved while the model was still writing, which is the whole exercise
    assert any(0 < p < 100 for p in pcts), pcts

    # the human line said which shot it was on, counted out of a real denominator
    details = " | ".join(s["detail"] for s in seen)
    assert "shots decided" in details, details
    # the ETA is recalibrated off this run rather than repeating the first guess
    assert final["eta_source"] == "measured"
    assert final["eta_s"] == 0.0


def test_an_unusable_estimate_does_not_stop_the_ask(client):
    """The safety rule: an estimate is a nicety, the work is not."""
    from roughcut import inference

    backend = _Streaming(estimate_text="about a minute I should think")
    inference.set_backend(backend)
    inference.reset_spend()
    try:
        job = client.post("/api/ask", json={"note": "tighten it"}).json()["job"]
        seen = _watch(client, job)
    finally:
        inference.set_backend(None)

    final = seen[-1]
    assert final["state"] == "done", final
    assert len(final["plan"]["segments"]) == 3
    assert final["estimate"]["source"] == "fallback"
    assert final["estimate"]["why"], "it should record why it fell back"
    # a fallback estimate is still an estimate: the bar and the checkpoints exist
    assert final["estimated_s"] > 0 and len(final["milestones"]) == 5


def test_every_kind_of_job_shows_up_in_one_list(client, project):
    """The top bar polls /api/jobs and nothing else, so everything long has to be
    there — and two at once, because a render and an Ask overlap routinely."""
    import server
    from roughcut import inference

    for registry in (server.ASKS, server.RENDERS, server.ANALYSES, server.VISUALS):
        registry.clear()          # earlier tests' jobs linger in the list for 25s
    inference.set_backend(_Streaming())
    inference.reset_spend()
    try:
        ask = client.post("/api/ask", json={"note": "tighten it"}).json()["job"]
        segs = [{"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.5, "why": "a"}]
        render = client.post("/api/render", json={"segments": segs}).json()["job"]

        kinds, ids = set(), set()
        deadline = time.time() + 90
        while time.time() < deadline:
            jobs = client.get("/api/jobs").json()["jobs"]
            for j in jobs:
                kinds.add(j["kind"])
                ids.add(j["id"])
                # every row has what a bar needs, whatever kind it is
                for key in ("label", "state", "pct", "detail", "elapsed_s"):
                    assert key in j, (j["kind"], key)
                assert 0 <= j["pct"] <= 100
                assert "plan" not in j, "the heartbeat must not carry payloads"
            # Both jobs settled, not just the render: the streaming ask is paced and
            # routinely outlives a 6 s synthetic render, and the plan asserted below
            # only exists once it is done. Waiting on the render alone was a race
            # that lost whenever the box was busy.
            if ({ask, render} <= ids
                    and client.get(f"/api/render/{render}").json()["state"] != "running"
                    and client.get(f"/api/ask/{ask}").json()["state"]
                    not in ("estimating", "running")):
                break
            time.sleep(0.1)
        assert kinds == {"ask", "render"}, kinds
        assert {ask, render} <= ids

        # the full record, payload and all, is one fetch away
        full = client.get(f"/api/job/{ask}").json()
        assert full["plan"]["segments"]
        assert client.get("/api/job/deadbeef").status_code == 404
    finally:
        inference.set_backend(None)


def test_a_render_is_not_finished_when_the_last_shot_is_cut(client, project,
                                                            monkeypatch):
    """The old bar hit 100% the moment the parts were on disk and then sat there for
    the whole join — minutes of it on a delivery render. Joining is its own milestone
    now, so the bar stops where cutting was actually worth stopping."""
    import server
    monkeypatch.setattr(server, "PROGRESS_TICK_S", 0.05)
    segs = [{"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.5, "why": "a"},
            {"clip": "CLIP_B.MP4", "in": 1.0, "out": 2.0, "why": "b"}]
    job = client.post("/api/render", json={"segments": segs}).json()["job"]
    seen = _watch(client, job, path="/api/render")
    assert seen[-1]["state"] == "done", seen[-1]["log"][-400:]
    # The invariant, whether or not the poll happened to land inside the join on a
    # two-shot render of 6-second clips: nothing running ever claims to be finished.
    # What the join is *worth* is asserted exactly in app/tests/test_progress.py.
    assert all(s["pct"] < 100 for s in seen if s["state"] == "running")
    assert [m["key"] for m in seen[-1]["milestones"]] == \
        ["cutting", "joining", "review"]
    assert seen[-1]["pct"] == 100.0
    # the old fields the browser still reads are all still there
    assert seen[-1]["done"] == seen[-1]["total"] == 2
    assert seen[-1]["stage"] == "done" and seen[-1]["elapsed_s"] > 0


def test_a_render_is_not_finished_until_the_players_have_something_to_play(
        client, project, monkeypatch):
    """The two branches met here. The A/B players stream the 720p review copy, and the
    copy was derived *after* the render reported done — so the top bar said finished
    while the only thing anyone was waiting for was still being made (~40 s off a
    1080p master, ~95 s off a 4K one, measured). It is the render's third phase now:
    the job stays running through it and only reaches 100% once the copy is there."""
    import server
    monkeypatch.setattr(server, "PROGRESS_TICK_S", 0.05)
    body = {"segments": [{"clip": "CLIP_C.MP4", "in": 0.0, "out": 1.5, "why": "c"}]}
    job = client.post("/api/render", json=body).json()["job"]
    seen = _watch(client, job, path="/api/render")
    last = seen[-1]
    assert last["state"] == "done", last["log"][-400:]
    # The copy exists by the time the job says so — not "started", finished.
    assert last["review"] == "ready"
    assert server.review_path(Path(last["output"]).name).exists()
    # and the versions list agrees the moment the job does, with no second encode
    row = next(r for r in client.get("/api/renders").json()["renders"]
               if r["name"] == Path(last["output"]).name)
    assert row["review_state"] == "ready"
    # Nothing that was still building ever claimed to be finished, and the phase is
    # visible rather than dead air: the job says what it is doing while it runs.
    assert all(s["pct"] < 100 for s in seen if s["state"] == "running")
    assert last["milestones"][-1]["key"] == "review"
    assert all(m["done_at"] for m in last["milestones"])


def test_a_render_whose_review_copy_fails_still_finishes_and_says_so(
        client, project, monkeypatch):
    """"Only at 100% when the copy is ready" must not mean "never at 100%". A copy
    that definitively failed ends the phase too — the players fall back to the master,
    which is what preview's fallback is for, and the job says that is what happened."""
    import server
    monkeypatch.setattr(server, "PROGRESS_TICK_S", 0.05)

    def boom(src, dest):
        raise RuntimeError(f"review copy failed for {src.name}: no")

    monkeypatch.setattr(server, "build_review", boom)
    body = {"segments": [{"clip": "CLIP_C.MP4", "in": 0.0, "out": 1.0, "why": "c"}]}
    job = client.post("/api/render", json=body).json()["job"]
    last = _watch(client, job, path="/api/render")[-1]
    assert last["state"] == "done", last["log"][-400:]
    assert last["review"] == "failed"
    assert "master" in last["detail"]
    assert last["pct"] == 100.0
    # the master is still there and still offered — nothing about the render is lost
    assert Path(last["output"]).exists()
    row = next(r for r in client.get("/api/renders").json()["renders"]
               if r["name"] == Path(last["output"]).name)
    assert row["review_state"] == "failed" and row["download_url"]


def test_the_visual_pass_says_which_sheet_it_is_on(client):
    """`visual_pass.py` has always said what it was reading; it said it into a log box
    nobody opens. That chatter is the top bar's human line now."""
    import server

    assert server._visual_detail("CLIP_04.MP4: 3 sheet(s)") == \
        "reading CLIP_04 — 3 sheets"
    assert server._visual_detail("  sheet_00.jpg: 4 moments ($0.0712)") == \
        "reading CLIP_04 — sheet 2 of 3"
    assert server._visual_detail("total projected $1.23") == ""
    # a clip's own summary line is not a sheet line and must not be read as one —
    # `visual_pass.py` prints both, and they differ only by their indentation
    assert server._visual_detail("CLIP_04.MP4: 5 moments, 2 notable, 3/3 sheets") == ""


def test_the_count_and_the_sheet_line_do_not_overwrite_each_other(client):
    """Found on the first live pass: the two-second ticker and the tool's own chatter
    were writing the same field, so "reading CLIP_05 — 1 sheet" flashed up and was
    replaced by "0 of 1 clips seen" a second later, over and over. The count answers
    "how far" and the line answers "on what"; a bar needs both, so they compose."""
    import server
    from roughcut import progress

    job = progress.Job("visual", "Looking", id="v", total=3, done=0, now="")
    assert server._visual_note(job) == "0 of 3 clips seen"
    server._visual_note(job, "CLIP_04.MP4: 3 sheet(s)")
    job["done"] = 1
    assert server._visual_note(job) == \
        "1 of 3 clips seen — reading CLIP_04 — 3 sheets"
    assert job["detail"] == "1 of 3 clips seen — reading CLIP_04 — 3 sheets"
    # one clip reads as one clip
    solo = progress.Job("visual", "Looking", id="v2", total=1, done=0, now="")
    assert server._visual_note(solo) == "0 of 1 clip seen"


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
    bin's frames for the other's clip. Posters are frames out of those proxies, so
    they carry the same hazard and the same per-bin split."""
    import server

    with _fresh(tmp_path, project):
        first, first_posters = server.STATE["proxy_dir"], server.STATE["posters"]
    other = tmp_path / "other-bin"
    other.mkdir()
    with _fresh(tmp_path, {"footage": other}):
        assert server.STATE["proxy_dir"] != first
        assert server.STATE["posters"] != first_posters


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


def test_the_board_never_serves_its_own_code_from_a_cache(client):
    """The page and the script have to agree with each other, and they shipped with no
    Cache-Control, no ETag and no Last-Modified — nothing telling a browser either to
    keep them or to check. An index.html carrying a monitor that the cached app.js has
    never heard of paints a board that will not play."""
    for path in ("/", "/app.js"):
        cc = client.get(path).headers.get("cache-control", "")
        assert "no-store" in cc, f"{path}: {cc!r}"


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
    and nothing starts on the app's own initiative. Both stages are priced: a button
    that silently grew dearer because a default changed is not offering a price."""
    import server

    with _fresh(tmp_path, project, sidecars=project["sidecars"], visual=None) as c:
        v = c.get("/api/status").json()["visual"]
        assert v["done"] == 0 and v["total"] == 3
        assert v["pending"] == ["CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"]
        assert v["coarse_calls"] == 3                  # one sheet each, for 6s clips
        assert v["fine_calls"] == 3 * server.FINE_WINDOWS_PER_CLIP
        assert v["calls"] == v["coarse_calls"] + v["fine_calls"]
        assert v["projected_usd"] == pytest.approx(
            3 * server.VISUAL_USD_PER_SHEET
            + v["fine_calls"] * server.FINE_USD_PER_WINDOW, abs=0.005)
        assert v["running"] is False and v["events"] == 0
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
        start = c.post("/api/visual", json={"fine": False}).json()
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
        assert v["done"] == 3 and v["pending"] == [] and v["coarse_calls"] == 0
        # the rank is rebuilt at the end of the pass, for free, without being asked
        assert v["events"] == 3
        assert c.get("/api/project").json()["events"][0]["kind"] == "fall"
        # a second run has nothing to do unless forced
        assert c.post("/api/visual", json={}).status_code == 400


def _stub_fine(script: Path, out: Path, stems: list[str]) -> list[str]:
    """A stand-in for `visual_pass.py --windows`: writes one fine sidecar per clip
    saying the close look found a camera artefact where the coarse pass claimed a fall.
    That disagreement is the case the ranking exists to handle."""
    script.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)\n"
        "for stem in sys.argv[2:]:\n"
        "    (out / f'{stem}.fine.json').write_text(json.dumps({\n"
        "        'clip': f'{stem}.mp4', 'mode': 'fine',\n"
        "        'windows_read': [[0.5, 4.0]],\n"
        "        'moments': [{'start': 1.5, 'end': 2.5, 'kind': 'junk',\n"
        "                     'what': 'a glove over the lens', 'notable': True}],\n"
        "        'unusable': [], 'summary': 'artefact', 'projected_usd': 0.073}))\n"
        "    print(f'{stem}.mp4: 1 moments', flush=True)\n", encoding="utf-8")
    return [sys.executable, str(script), str(out), *stems]


def test_the_close_look_is_a_second_stage_that_can_overrule_the_first(
        tmp_path, project, monkeypatch):
    """Coarse pass, free scan, close look, rank — and the rank believes the close look.

    Karl, on the revision the coarse pass produced: *"You missed some cool jumps —
    ... the lack of a workflow / algorithm that applies sort / priority following a
    granular keyframe analysis on the first pass."* This is that workflow end to end,
    with the stages stubbed: what must hold is that the second stage runs after the
    first, that its disagreement demotes the first stage's claim rather than being
    averaged with it, and that a failure there keeps the sheets already paid for.
    """
    import server

    monkeypatch.setattr(server, "PROGRESS_TICK_S", 0.05)
    stems = ["CLIP_A", "CLIP_B", "CLIP_C"]
    seen: dict = {}
    monkeypatch.setattr(server, "visual_cmd", lambda only, force: _stub_looker(
        tmp_path / "look.py", server.STATE["visual"], stems))

    def scan(only, windows_out, limit):
        seen["limit"], seen["only"] = limit, only
        Path(windows_out).write_text(
            json.dumps({s: [[0.5, 4.0]] for s in stems}), encoding="utf-8")
        return [sys.executable, "-c", "print('scanned')"]

    monkeypatch.setattr(server, "scan_cmd", scan)
    monkeypatch.setattr(server, "fine_cmd", lambda windows: _stub_fine(
        tmp_path / "fine.py", server.STATE["visual"], stems))

    with _fresh(tmp_path, project, sidecars=project["sidecars"], visual=None) as c:
        job = c.post("/api/visual", json={}).json()["job"]
        s = _wait_visual(c, job)
        assert s["state"] == "done" and s["done"] == 3 and s["fine_done"] == 3, s
        assert seen["limit"] == server.FINE_WINDOWS_PER_CLIP
        assert "scanned" in s["log"]

        # The coarse "fall 1.0-3.0" sat inside the audited window and the close look
        # called it a glove, so it must not be near the top of the rank any more.
        ranked = c.get("/api/project").json()["events"]
        falls = [e for e in ranked if e["kind"] == "fall"]
        assert falls, "the coarse claim should survive, demoted, not vanish"
        assert all(e["why_ranked"]["confirmation"] == "contradicted" for e in falls)
        assert all(e["score"] < 0.6 for e in falls)
        # junk is a floor, not a low score: it is never offered as a moment to add
        assert not [e for e in ranked if e["kind"] == "junk"]

        # and the clip inventory the Ask reads carries the close look, not the claim
        clip = c.get("/api/project").json()["clips"]["CLIP_A.MP4"]
        kinds = [m["kind"] for m in clip["visual"]["moments"]]
        assert kinds == ["junk"], kinds


def test_a_failed_close_look_keeps_the_sheets_that_were_paid_for(tmp_path, project,
                                                                 monkeypatch):
    import server

    monkeypatch.setattr(server, "PROGRESS_TICK_S", 0.05)
    stems = ["CLIP_A", "CLIP_B", "CLIP_C"]
    monkeypatch.setattr(server, "visual_cmd", lambda only, force: _stub_looker(
        tmp_path / "look.py", server.STATE["visual"], stems))
    monkeypatch.setattr(server, "scan_cmd", lambda only, out, limit: [
        sys.executable, "-c", "import sys; sys.exit(4)"])
    with _fresh(tmp_path, project, sidecars=project["sidecars"], visual=None) as c:
        s = _wait_visual(c, c.post("/api/visual", json={}).json()["job"])
        assert s["state"] == "done" and s["done"] == 3
        assert "close look did not finish" in s["detail"]
        # the coarse pass still reached the board, and still got ranked
        assert c.get("/api/project").json()["events"][0]["kind"] == "fall"


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


# ------------------------------------------------------------ the bin in the ask
#
# docs/INTAKE.md I1.1. The first cut is asked *from the bin*: heroes fixed, keeps as
# bounds the model may trim inside, the inventory for connective tissue. The server
# does not yet pass `selects` through to `revise.originate`, so these drive `revise`
# directly with a scripted backend; the one end-to-end test wires the call the way
# `_ask_job` will (`selects=read_edl().get("selects")`).

def _scripted(plan: dict):
    """A backend that answers every plan call with `plan` and records what it saw."""
    from roughcut import config, inference

    class Scripted:
        name = "scripted"
        seen: list = []

        def complete(self, request):
            Scripted.seen.append(request)
            text = json.dumps(plan)
            model = config.model_for(request.role)
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend="scripted", model=model,
                                    projected_usd=1e-4, latency_ms=1, raw=text)
    return Scripted


def _bin() -> list[dict]:
    from roughcut import selects
    return [selects.new_select("CLIP_A.MP4", 0.5, 2.0, why="the greeting",
                               note="open on this, hold the smile", hero=True,
                               created=1.0),
            selects.new_select("CLIP_B.MP4", 2.4, 4.0, why="the reply", created=2.0)]


_EVENT = {"rank": 1, "clip": "CLIP_C.MP4", "start": 1.0, "end": 3.0, "kind": "fall",
          "score": 0.9, "what": "rider goes down", "notable": True,
          "why_ranked": {"confirmation": "confirmed"}}


def test_originate_reads_the_bin_ahead_of_the_events_and_the_inventory(client):
    import server
    from roughcut import inference, revise

    clips, _ = server._ask_clips()
    Scripted = _scripted({
        "segments": [{"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0, "why": "opens"},
                     {"clip": "CLIP_B.MP4", "in": 2.4, "out": 4.0, "why": "answers"}],
        "notes": "built from the two keeps"})
    inference.set_backend(Scripted())
    inference.reset_spend()
    try:
        plan = revise.originate(clips, "two people talking", events=[_EVENT],
                                selects=_bin())
        assert len(plan["segments"]) == 2
        prompt = Scripted.seen[-1].prompt
        assert "## The editor's selects" in prompt
        assert ("CLIP_A.MP4 0.5-2.0 (1.5s) — the greeting — editor's note: "
                "\"open on this, hold the smile\" — HERO") in prompt
        assert "CLIP_B.MP4 2.4-4.0 (1.6s) — the reply\n" in prompt
        assert prompt.count("HERO") >= 2, "the marker on the line and in the guidance"
        # the bin before the ranked events, both before the inventory
        assert (prompt.index("## The editor's selects") < prompt.index("## Events, ranked")
                < prompt.index("## Every clip available"))
        assert "connective tissue" in prompt and "trim inside" in prompt
        # and the same call without a bin says nothing about one
        Scripted.seen.clear()
        revise.originate(clips, "two people talking")
        assert "The editor's selects" not in Scripted.seen[-1].prompt
    finally:
        inference.set_backend(None)


def test_a_first_cut_that_drops_a_hero_must_say_so_in_notes(client):
    import server
    from roughcut import inference, revise

    clips, _ = server._ask_clips()
    silent = {"segments": [{"clip": "CLIP_B.MP4", "in": 2.4, "out": 4.0, "why": "x"}],
              "notes": "went with the reply alone"}
    Scripted = _scripted(silent)
    inference.set_backend(Scripted())
    inference.reset_spend()
    try:
        with pytest.raises(inference.InferenceError, match="CLIP_A.MP4"):
            revise.originate(clips, "a test film", selects=_bin())
        # one bounded re-ask, told what was wrong, before it gives up
        assert len(Scripted.seen) == 2
        assert "hero CLIP_A.MP4 0.5-2.0" in Scripted.seen[-1].prompt
    finally:
        inference.set_backend(None)

    # the same plan, explained, is accepted — the editor sees why in the notes
    explained = dict(silent, notes="CLIP_A is too dark to open on, so I left it out")
    inference.set_backend(_scripted(explained)())
    try:
        plan = revise.originate(clips, "a test film", selects=_bin())
        assert plan["notes"].startswith("CLIP_A is too dark")
    finally:
        inference.set_backend(None)

    # trimming inside the hero is fine; keeping a sliver of it is dropping it
    heroes = [{"clip": "CLIP_A.MP4", "start": 0.5, "end": 2.0}]
    ok = revise.validate_plan({"segments": [{"clip": "CLIP_A.MP4", "in": 0.9, "out": 2.0}]},
                              clips, heroes)
    assert ok["segments"][0]["in"] == 0.9
    with pytest.raises(ValueError, match="hero CLIP_A.MP4"):
        revise.validate_plan({"segments": [{"clip": "CLIP_A.MP4", "in": 0.5, "out": 1.0}]},
                             clips, heroes)
    # and a hero on a clip the inventory does not have is not a constraint
    stale = [dict(_bin()[0], clip="GONE.MP4")]
    assert revise.heroes_of(stale, clips) == []


def test_the_server_reports_a_dropped_hero_as_a_502_naming_the_clip(client, project,
                                                                     monkeypatch):
    """End to end — the server passes the bin to the ask itself — a plan that drops a
    hero silently fails the job with the clip in the detail, not a silent proposal."""
    from roughcut import inference

    r = client.put("/api/selects", json={"selects": [
        {"clip": "CLIP_A.MP4", "start": 0.5, "end": 2.0, "why": "the greeting",
         "hero": True}]})
    assert r.status_code == 200

    inference.set_backend(_scripted({
        "segments": [{"clip": "CLIP_B.MP4", "in": 2.4, "out": 4.0, "why": "x"}],
        "notes": "the reply alone"})())
    inference.reset_spend()
    try:
        job = client.post("/api/ask", json={"note": "", "segments": [],
                                            "story": "a test film"}).json()["job"]
        s = _ask_status(client, job)
        assert s["state"] == "failed" and s["code"] == 502
        assert "hero CLIP_A.MP4" in s["detail"]
    finally:
        inference.set_backend(None)


def test_a_revision_reads_the_bin_as_context_not_constraint(client):
    import server
    from roughcut import inference, revise

    clips, _ = server._ask_clips()
    Scripted = _scripted({
        "segments": [{"clip": "CLIP_C.MP4", "in": 0.5, "out": 4.0, "why": "per the note"}],
        "notes": "swapped in the unused clip"})
    inference.set_backend(Scripted())
    inference.reset_spend()
    try:
        plan = revise.propose([{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0}], clips,
                              "a test film", "use the clip that isn't in the cut",
                              selects=_bin())
        # the hero is gone and unmentioned, and that is allowed in a revision
        assert plan["segments"][0]["clip"] == "CLIP_C.MP4"
        prompt = Scripted.seen[-1].prompt
        assert "## The editor's selects" in prompt
        assert "context, not constraints" in prompt
        assert "— HERO" in prompt
        assert prompt.index("## The editor's selects") < prompt.index("## Every clip available")
    finally:
        inference.set_backend(None)
