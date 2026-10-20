"""Footage in, from anywhere (REDESIGN-GOAL dimension 13, Karl's K3 b), through the API.

Karl, 2026-10-16: *"there is no clear way to add new footage."* A bin was one folder;
now it is its folder plus what was imported (files or folders elsewhere on the board's
machine, referenced in place) plus what was uploaded from the viewer's computer (stored
under --work). These drive the routes the Import dialog and the Media Browser use:

  * GET /api/media/browse lists a folder (folders, videos with sizes) without ffprobe;
  * POST /api/import adds a second folder to a running bin, recursively; the clip list is
    the union; a second CLIP_A.MP4 gets a stable unique name; a file already in the bin
    is skipped, not doubled;
  * POST /api/upload + PUT /api/upload/{batch}/{i} streams a file to disk (the part file
    grows while the body is still arriving) and it becomes a clip, under --work, never
    under the footage folder; a short body leaves nothing behind;
  * everything survives a restart (configure again on the same --work);
  * a cut using an imported clip resolves its poster from that clip's own file and
    renders it (assemble.py, the real tool, through the bin's view folder).

The fixture's clips are copied into tmp dirs: nothing here writes ~/footage.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import numpy as np
import pytest


def _make(path: Path, src: str, seconds: float = 3.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i", f"{src}:size=320x180:rate=24:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True)


@pytest.fixture(scope="module")
def media(tmp_path_factory, project) -> dict:
    """A bin folder (copies of two fixture clips) and a second card elsewhere: a red
    CLIP_A.MP4 (the same name as the bin's), a blue clip one folder down, a text file
    and a hidden file the browser and the import must leave out."""
    root = tmp_path_factory.mktemp("import-media")
    footage = root / "trip" / "day1"
    footage.mkdir(parents=True)
    for stem in ("CLIP_A", "CLIP_B"):
        shutil.copy(project["footage"] / f"{stem}.MP4", footage / f"{stem}.MP4")
    card = root / "trip" / "card2"
    _make(card / "CLIP_A.MP4", "color=c=red")
    _make(card / "sub" / "Blue Run (1).mov", "color=c=blue")
    (card / "notes.txt").write_text("not footage", encoding="utf-8")
    (card / ".DS_Store").write_bytes(b"\0")
    up = root / "laptop"
    _make(up / "CLIP_B.MP4", "color=c=green")      # its name is taken in the bin
    _make(up / "phone.mp4", "color=c=yellow", 2.0)
    return {"root": root, "footage": footage, "card": card, "laptop": up}


def _board(work: Path, media: dict):
    from fastapi.testclient import TestClient
    import server

    server.configure(None, media["footage"], None, work, proxies=False,
                     visual=work / "no-visual")
    return TestClient(server.app)


def _quiet(server) -> None:
    """Every task configure() put on the clip-facts pool has ended (each clip's facts, then
    its colour tags: ffprobes on the pool's threads, `warm_clip_facts`). One task per worker
    meets the caller at a barrier: the queue is first in, first out, so when every worker is
    in one, nothing queued before them is still running or waiting.

    Lane TESTS2, wave 17: test_browse patches `subprocess.run` (the module's, process-wide)
    to see whether listing a folder probes, and in 1 neighbourhood run of 20 the bin's own
    configure-time probes, still on the pool, ran inside the patch: "the browser never
    probes" read two ffprobes that were not the browser's."""
    pool = server.CLIP_FACTS["pool"]
    if pool is None:
        return
    n = server.CLIP_FACTS_WORKERS
    meet = threading.Barrier(n + 1)
    for _ in range(n):
        pool.submit(meet.wait, 30)
    meet.wait(30)


def _clips(c) -> dict:
    """/api/clips, waited on until every clip's facts are probed."""
    deadline = time.time() + 30
    while True:
        j = c.get("/api/clips").json()
        if not j["probing"] or time.time() > deadline:
            return {r["clip"]: r for r in j["clips"]}
        time.sleep(0.2)


def _poster(c, url: str, wait_s: float = 20.0):
    """GET a poster as the board does (wave 15 BUDGET's contract): a frame not cut yet is
    answered at once with a stand-in marked `x-roughcut-poster` (cut in the background; a
    request waits for it at most POSTER_WAIT_S), so ask again until it is the frame.

    Lane TESTS2, wave 17: the import test read the card's poster once and got the stand-in
    (mean (21, 20, 22), the neutral #151517), 3 runs of 3 alone: in a cold process the
    import's own poster build (`_import_posters`: the clip's ffprobe, then a walk slot) only
    started its ffmpeg ~0.9 s after the import, after the test had asked. The server's
    "poster failed for CLIP_A.MP4 at 1.00s:" with no error was that build's ffmpeg killed
    (-9) as the failed test's board shut down. Warm (the full suite), it was on disk first."""
    deadline = time.monotonic() + wait_s
    while True:
        r = c.get(url)
        if "x-roughcut-poster" not in r.headers:
            return r
        assert r.status_code == 200 and "standin" in r.headers["server-timing"], dict(r.headers)
        assert time.monotonic() < deadline, f"{url}: still a stand-in after {wait_s:.0f} s"
        time.sleep(0.1)


def _mean_rgb(jpeg: bytes) -> np.ndarray:
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "rawvideo",
                        "-pix_fmt", "rgb24", "pipe:1"], input=jpeg, capture_output=True,
                       check=True)
    return np.frombuffer(r.stdout, np.uint8).reshape(-1, 3).mean(axis=0)


# ------------------------------------------------------------------ browse


def test_browse_lists_folders_and_videos_with_sizes_and_never_probes(tmp_path, media,
                                                                     monkeypatch):
    import server

    with _board(tmp_path, media) as c:
        _quiet(server)                     # the bin's own probes are not the browser's
        calls = []
        real = subprocess.run
        monkeypatch.setattr(server.subprocess, "run", lambda *a, **k: (
            calls.append(a[0] if a else k.get("args")), real(*a, **k))[1])
        d = c.get("/api/media/browse", params={"path": str(media["card"])}).json()
        assert not [x for x in calls if "ffprobe" in str(x)], "the browser never probes"
        names = [(e["kind"], e["name"]) for e in d["entries"]]
        assert names == [("dir", "sub"), ("video", "CLIP_A.MP4")]     # folders first
        vid = d["entries"][1]
        assert vid["size"] == (media["card"] / "CLIP_A.MP4").stat().st_size
        assert vid["mtime"] > 0 and vid["in_project"] is False
        assert d["others"] == 1                          # notes.txt: counted, not listed
        assert d["parent"] == str(media["card"].parent)
        assert d["host"] == socket.gethostname()
        kinds = {r["kind"]: r["path"] for r in d["roots"]}
        assert kinds["bin"] == str(media["footage"])
        assert kinds["folder"] == str(media["footage"].parent)
        assert kinds["home"] == str(Path.home())

        # no path: the folder that holds the bin (where a trip's other cards sit)
        d0 = c.get("/api/media/browse").json()
        assert d0["path"] == str(media["footage"].parent)
        assert {e["name"] for e in d0["entries"]} == {"day1", "card2"}
        # the bin's own clips read as in the project
        own = c.get("/api/media/browse", params={"path": str(media["footage"])}).json()
        assert all(e["in_project"] for e in own["entries"])

        assert c.get("/api/media/browse",
                     params={"path": str(media["card"] / "nope")}).status_code == 404
        assert c.get("/api/media/browse",
                     params={"path": str(media["card"] / "notes.txt")}).status_code == 400


# ------------------------------------------------------------------ import


def test_importing_a_second_folder_unions_the_clips_and_names_collisions(tmp_path, media):
    import server

    with _board(tmp_path, media) as c:
        assert set(_clips(c)) == {"CLIP_A.MP4", "CLIP_B.MP4"}
        r = c.post("/api/import", json={"paths": [str(media["card"])]})
        assert r.status_code == 200, r.text
        got = r.json()
        # the card's CLIP_A is a second CLIP_A: its stem plus its folder's name; the file
        # one folder down is found, its name made URL-safe
        assert got["added"] == ["CLIP_A-card2.MP4", "Blue_Run_1.mov"]
        assert got["clips"] == 4
        assert got["index"] is None                  # proxies off: nothing behind our back
        clips = _clips(c)
        assert set(clips) == {"CLIP_A.MP4", "CLIP_B.MP4", "CLIP_A-card2.MP4", "Blue_Run_1.mov"}
        red = clips["CLIP_A-card2.MP4"]
        assert red["source"] == "import" and red["path"] == str(media["card"] / "CLIP_A.MP4")
        assert red["came_as"] == "CLIP_A.MP4"
        assert clips["CLIP_A.MP4"]["source"] == "folder"
        # the free facts, from the imported file itself
        assert red["duration"] == pytest.approx(3.0, abs=0.1)
        assert red["fps"] == pytest.approx(24.0) and (red["width"], red["height"]) == (320, 180)
        # referenced in place: nothing copied into the bin's folder or anywhere else
        assert sorted(p.name for p in media["footage"].iterdir()) == ["CLIP_A.MP4", "CLIP_B.MP4"]
        assert not list((tmp_path / "media").rglob("*.MP4")) or all(
            p.is_symlink() for p in (tmp_path / "media").rglob("*.MP4"))
        # the poster is the card's red frame, not the bin's CLIP_A (the resolver)
        p = _poster(c, red["poster"])
        assert p.status_code == 200 and p.headers["content-type"] == "image/jpeg"
        rgb = _mean_rgb(p.content)
        assert rgb[0] > 180 and rgb[1] < 60 and rgb[2] < 60, rgb
        own = _mean_rgb(_poster(c, clips["CLIP_A.MP4"]["poster"]).content)
        assert not (own[0] > 180 and own[1] < 60), "the bin's CLIP_A is not red"

        # again: the same files are skipped, not doubled; nor the bin's own file; nor text
        again = c.post("/api/import", json={"paths": [
            str(media["card"]), str(media["footage"] / "CLIP_B.MP4"),
            str(media["card"] / "notes.txt"), str(media["card"] / "missing.mp4")]}).json()
        assert again["added"] == []
        why = {Path(s["path"]).name: s["why"] for s in again["skipped"]}
        assert why["CLIP_A.MP4"] == "already in the project"
        assert why["Blue Run (1).mov"] == "already in the project"
        assert why["CLIP_B.MP4"].startswith("already in the project (the bin's folder)")
        assert why["notes.txt"] == "not a video file"
        assert why["missing.mp4"] == "no such file or folder"
        assert len(_clips(c)) == 4

        assert c.post("/api/import", json={"paths": []}).status_code == 400
        assert c.post("/api/import", json={}).status_code == 400

        # the clip plays like one from the folder: an original the browser can decode
        st = c.get("/media/status/CLIP_A-card2.mp4").json()
        assert st["ready"] is True, st
        assert server.clip_path("CLIP_A-card2.MP4") == media["card"] / "CLIP_A.MP4"
        assert server.clip_path("CLIP_A.MP4") == media["footage"] / "CLIP_A.MP4"


def test_collision_names_are_unique_case_blind_and_stable():
    from roughcut import media as mediamod

    taken = {"clip_a", "c0001"}
    assert mediamod.unique_name("CLIP_A.MP4", taken, "card2") == "CLIP_A-card2.MP4"
    assert mediamod.unique_name("c0001.mp4", taken, "dayB") == "c0001-dayB.mp4"
    taken |= {"clip_a-card2"}
    assert mediamod.unique_name("CLIP_A.MP4", taken, "card2") == "CLIP_A-card2-2.MP4"
    assert mediamod.unique_name("CLIP_A.MP4", taken, "") == "CLIP_A-2.MP4"
    assert mediamod.unique_name("new.MP4", taken, "x") == "new.MP4"
    assert mediamod.safe_name("My Clip (1).MOV") == "My_Clip_1.MOV"
    assert mediamod.safe_name("../../etc/passwd.mp4") == "passwd.mp4"
    assert mediamod.safe_name("###.mp4") == "clip.mp4"
    assert mediamod.safe_name("gen_black_1.mp4") == "clip_gen_black_1.mp4"   # not a slide


def test_the_imported_names_survive_a_restart(tmp_path, media):
    import server

    with _board(tmp_path, media) as c:
        added = c.post("/api/import", json={"paths": [str(media["card"])]}).json()["added"]
        assert added == ["CLIP_A-card2.MP4", "Blue_Run_1.mov"]
        saved = json.loads((tmp_path / "media" / "day1" / "sources.json").read_text())
        assert [e["clip"] for e in saved["entries"]] == added
    # a restart is configure() again on the same --work (main() takes the same path)
    server.STATE.pop("media_sources", None)
    with _board(tmp_path, media) as c:
        clips = _clips(c)
        assert set(clips) == {"CLIP_A.MP4", "CLIP_B.MP4", "CLIP_A-card2.MP4", "Blue_Run_1.mov"}
        assert clips["CLIP_A-card2.MP4"]["path"] == str(media["card"] / "CLIP_A.MP4")
        # an unplugged drive: its clips leave the list, and come back with it
        moved = media["card"].with_name("card2-away")
        media["card"].rename(moved)
        try:
            assert set(_clips(c)) == {"CLIP_A.MP4", "CLIP_B.MP4"}
            rows = c.get("/api/media/sources").json()["entries"]
            assert [r["present"] for r in rows] == [False, False]
        finally:
            moved.rename(media["card"])
        assert len(_clips(c)) == 4


# ------------------------------------------------------------------ upload


def test_an_upload_streams_to_disk_under_work_and_becomes_a_clip(tmp_path, media):
    with _board(tmp_path, media) as c:
        files = [media["laptop"] / "CLIP_B.MP4", media["laptop"] / "phone.mp4"]
        plan = c.post("/api/upload", json={"files": [
            {"name": f.name, "size": f.stat().st_size} for f in files]
            + [{"name": "readme.txt", "size": 4}]}).json()
        rows = {r["name"]: r for r in plan["files"]}
        assert rows["CLIP_B.MP4"]["clip"] == "CLIP_B-upload.MP4"     # the bin has a CLIP_B
        assert rows["phone.mp4"]["clip"] == "phone.mp4"
        assert rows["readme.txt"]["skip"] == "not a video file"
        batch = plan["batch"]
        jobs = {j["id"]: j for j in c.get("/api/jobs").json()["jobs"]}
        assert jobs[batch]["kind"] == "import" and jobs[batch]["label"] == "Importing 2 files"
        for f, r in zip(files, plan["files"]):
            res = c.put(f"/api/upload/{batch}/{r['i']}", content=f.read_bytes(),
                        headers={"content-type": "application/octet-stream"})
            assert res.status_code == 200, res.text
            assert res.json()["clip"] == r["clip"]
        job = c.get(f"/api/job/{batch}").json()
        assert job["state"] == "done" and job["detail"] == "2 clips imported"
        clips = _clips(c)
        green = clips["CLIP_B-upload.MP4"]
        assert green["source"] == "upload"
        where = Path(green["path"])
        assert where == tmp_path / "media" / "day1" / "uploads" / "CLIP_B-upload.MP4"
        assert where.read_bytes() == files[0].read_bytes()
        assert sorted(p.name for p in media["footage"].iterdir()) == ["CLIP_A.MP4", "CLIP_B.MP4"]
        assert green["duration"] == pytest.approx(3.0, abs=0.1)
        rgb = _mean_rgb(_poster(c, green["poster"]).content)
        assert rgb[1] > 100 and rgb[0] < 60, rgb

        # the same file again is "already imported"; nothing is sent
        again = c.post("/api/upload", json={"files": [
            {"name": "phone.mp4", "size": files[1].stat().st_size}]}).json()
        assert again["batch"] is None and again["files"][0]["skip"] == "already imported"

        # a body shorter than announced: refused, and no part file left behind
        f = media["laptop"] / "phone.mp4"
        p2 = c.post("/api/upload", json={"files": [{"name": "short.mp4",
                                                     "size": f.stat().st_size}]}).json()
        bad = c.put(f"/api/upload/{p2['batch']}/0", content=f.read_bytes()[:1000])
        assert bad.status_code == 400 and "of" in bad.json()["detail"]
        assert not list((tmp_path / "media" / "day1" / "uploads").rglob("*.part"))
        assert "short.mp4" not in _clips(c)
        assert c.get(f"/api/job/{p2['batch']}").json()["state"] == "failed"
        # a file that is not in the plan, or a plan of nothing
        assert c.put(f"/api/upload/{p2['batch']}/9", content=b"x").status_code == 404
        assert c.post("/api/upload", json={"files": []}).status_code == 400


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_an_upload_lands_on_disk_while_its_body_is_still_arriving(tmp_path, media,
                                                                  monkeypatch):
    """Streamed, not buffered: halfway through the body the part file already holds the
    first half and the header's job says how far it is."""
    import uvicorn
    import server

    monkeypatch.setattr(server, "UPLOAD_WRITE", 64 * 1024)
    server.configure(None, media["footage"], None, tmp_path, proxies=False,
                     visual=tmp_path / "no-visual")
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"
    data = (media["laptop"] / "phone.mp4").read_bytes()
    data = data * (1 + (600_000 // len(data)))           # ≥ 600 kB, an exact multiple
    gate = threading.Event()
    half = len(data) // 2
    seen: dict = {}

    def body():
        for at in range(0, half, 32 * 1024):
            yield data[at:min(at + 32 * 1024, half)]
        gate.wait(10)
        for at in range(half, len(data), 32 * 1024):
            yield data[at:at + 32 * 1024]

    try:
        plan = httpx.post(base + "/api/upload", json={"files": [
            {"name": "big.mp4", "size": len(data)}]}).json()
        batch = plan["batch"]
        result: dict = {}
        t = threading.Thread(target=lambda: result.update(r=httpx.put(
            f"{base}/api/upload/{batch}/0", content=body(), timeout=30)))
        t.start()
        incoming = tmp_path / "media" / "day1" / "uploads" / ".incoming"
        deadline = time.time() + 10
        while time.time() < deadline:
            parts = list(incoming.glob("*.part")) if incoming.is_dir() else []
            if parts and parts[0].stat().st_size >= half - 64 * 1024:
                seen["part"] = parts[0].stat().st_size
                jobs = {j["id"]: j for j in httpx.get(base + "/api/jobs").json()["jobs"]}
                seen["job"] = jobs[batch]
                break
            time.sleep(0.05)
        gate.set()
        t.join(30)
        assert seen.get("part"), "the first half never reached the disk before the rest"
        assert seen["part"] < len(data)
        assert 0 < seen["job"]["got"] < len(data) and seen["job"]["state"] == "running"
        assert "big.mp4" in seen["job"]["detail"]
        assert result["r"].status_code == 200, result["r"].text
        dest = tmp_path / "media" / "day1" / "uploads" / "big.mp4"
        assert dest.stat().st_size == len(data)
        assert not list(incoming.glob("*.part"))
    finally:
        gate.set()
        srv.should_exit = True
        th.join(timeout=10)


# ------------------------------------------------------------------ in the cut


def test_a_cut_using_an_imported_clip_plays_and_renders_it(tmp_path, media):
    import server

    with _board(tmp_path, media) as c:
        c.post("/api/import", json={"paths": [str(media["card"] / "CLIP_A.MP4")]})
        segs = [{"clip": "CLIP_A.MP4", "in": 0.5, "out": 1.5, "why": "the bin's"},
                {"clip": "CLIP_A-card2.MP4", "in": 0.5, "out": 1.5, "why": "the card's"}]
        assert c.put("/api/project", json={"segments": segs}).status_code == 200
        p = c.get("/api/project").json()
        red = p["clips"]["CLIP_A-card2.MP4"]          # listed before it is heard
        assert red["heard"] is False and red["duration"] == pytest.approx(3.0, abs=0.1)
        assert c.get(red["poster"] + "?t=1.00").status_code == 200

        job = c.post("/api/render", json={"segments": segs}).json()["job"]
        deadline = time.time() + 180
        while time.time() < deadline:
            st = c.get(f"/api/render/{job}").json()
            if st["state"] != "running":
                break
            time.sleep(0.5)
        assert st["state"] == "done", st.get("log", "")[-800:]
        out = Path(st["output"])
        # the second shot is the card's red, the first the bin's test pattern
        frame = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1.5", "-i", str(out),
                                "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24",
                                "pipe:1"], capture_output=True, check=True).stdout
        rgb = np.frombuffer(frame, np.uint8).reshape(-1, 3).mean(axis=0)
        assert rgb[0] > 170 and rgb[1] < 70 and rgb[2] < 70, rgb
        # the tools were handed the bin's view: one symlink per clip, named as the clip
        view = tmp_path / "media" / "day1" / "view"
        assert (view / "CLIP_A-card2.MP4").resolve() == (media["card"] / "CLIP_A.MP4").resolve()
        assert (view / "CLIP_A.MP4").resolve() == (media["footage"] / "CLIP_A.MP4").resolve()
        # and taking it out of the bin is refused while the cut uses it
        assert c.post("/api/media/remove", json={"clips": ["CLIP_A-card2.MP4"]}).status_code == 409
        c.put("/api/project", json={"segments": segs[:1]})
        gone = c.post("/api/media/remove", json={"clips": ["CLIP_A-card2.MP4", "CLIP_B.MP4"]}).json()
        assert gone["removed"] == ["CLIP_A-card2.MP4"] and gone["not_imported"] == ["CLIP_B.MP4"]
        assert (media["card"] / "CLIP_A.MP4").exists()      # a reference: the file stays
        assert "CLIP_A-card2.MP4" not in _clips(c)
        _ = server


# ------------------------------------------------------------------ the free index


def test_an_import_starts_the_free_index_and_never_the_priced_look(tmp_path, media,
                                                                   monkeypatch):
    """`index_on_import`: the bin's free stages (probe, telemetry, the sound, proxies) run
    for the new clips; the look and the close look are held for this run only — the
    journal is not paused, so the next index (or first pass) the editor starts prices
    and runs them as ever."""
    import server
    from roughcut import journal
    from test_index import _stub_tools, _wait_index

    with _board(tmp_path, media) as c:
        _stub_tools(server, monkeypatch, tmp_path)
        monkeypatch.setattr(server, "build_proxy",
                            lambda src, dest, orient: shutil.copy(src, dest) and "copied")
        server.STATE["index_on_import"] = True
        try:
            got = c.post("/api/import", json={"paths": [str(media["card"])]}).json()
            job = got["index"]
            assert job, got
            s = _wait_index(c, job)
            assert s["state"] == "done", s
            assert s["label"] == "Indexing the new footage (free)"
            assert "not looked at yet: the look is priced" in s["detail"]
            j = journal.Journal.load(server.journal_path())
            for clip in ("CLIP_A-card2.MP4", "Blue_Run_1.mov", "CLIP_A.MP4"):
                assert j.state(clip, "asr") == "done", clip
                assert j.state(clip, "proxy") == "done", clip
                assert j.state(clip, "look") == "queued", clip
            assert not j.paused_priced
            assert j.cost_usd() == 0.0
            # heard: the sidecar names the imported clip
            assert "CLIP_A-card2" in server.analysed_stems()
        finally:
            server.STATE.pop("index_on_import", None)
            from test_firstpass import _end_jobs
            _end_jobs(server)
