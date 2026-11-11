"""What a delivery needs (wave 24, lane DELIVER; FINISH-02/-03/-15/-21/-24/-25/-26, WORDS-16,
FINDER-17).

  * music is brought in from the board: a file from the viewer's computer or one by path lands
    in the project's own music folder (never the repo's assets/), is listed, survives a reload,
    is what the render reads; the endpoint writes only there and refuses what is not music;
  * an export is put away from its dialog and the board is used while it renders; the render
    is of the cut as it was when it started; the time left is measured from progress;
  * the dialog says the file it will write (and "-2" before, not after), and the sound;
  * the .WAV beside a camera file is said in the clip's Audio Info as not used.

No model is called. The renders are real (assemble.py), on the CPU path.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from roughcut import music as musicmod  # noqa: E402


def _wav(path: Path, seconds: float = 2.0, hz: int = 440) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi", "-i",
                    f"sine=frequency={hz}:duration={seconds}", str(path)],
                   check=True, capture_output=True)
    return path


@pytest.fixture(scope="module", autouse=True)
def cpu_only():
    mp = pytest.MonkeyPatch()
    mp.setenv("ROUGHCUT_GPU", "0")
    yield
    mp.undo()


# ---------------------------------------------------------------- the module (no server)

def test_names_are_one_safe_component_and_only_music():
    assert musicmod.clean_name("../../etc/My Song (v2).MP3") == "My Song (v2).mp3"
    assert musicmod.clean_name("a/b\\c.flac") == "c.flac"
    assert musicmod.clean_name("wéird¡name!.wav") == "w-ird-name.wav"
    for bad, word in (("x.ogg", ".ogg"), ("x.exe", ".exe"), ("noext", "no extension"),
                      ("x.mp4", ".mp4"), ("", "no name")):
        with pytest.raises(musicmod.MusicError) as e:
            musicmod.clean_name(bad)
        assert word in str(e.value)
    assert "mp3, wav, m4a or flac" in str(pytest.raises(
        musicmod.MusicError, musicmod.clean_name, "x.ogg").value)


def test_a_file_by_path_is_copied_into_the_projects_folder_and_never_over_another(tmp_path):
    (tmp_path / "in").mkdir()
    src = _wav(tmp_path / "in" / "Song One.wav")
    work = tmp_path / "work"
    a = musicmod.take_file(src, work, "binx")
    b = musicmod.take_file(src, work, "binx")
    assert a == work / "music" / "binx" / "Song One.wav"
    assert b.name == "Song One-2.wav" and a.exists() and b.exists()
    assert src.exists()                                            # a copy, the original stays
    with pytest.raises(musicmod.MusicError, match="folder"):
        musicmod.take_file(tmp_path / "in", work, "binx")
    with pytest.raises(musicmod.MusicError, match="no file"):
        musicmod.take_file(tmp_path / "nope.wav", work, "binx")
    fake = tmp_path / "in" / "notes.mp3"
    fake.write_text("this is text, not music")
    with pytest.raises(musicmod.MusicError, match="does not look like"):
        musicmod.take_file(fake, work, "binx")
    big = tmp_path / "in" / "big.wav"
    big.write_bytes(b"RIFF....WAVE" + b"0" * 64)
    old = musicmod.MAX_BYTES
    musicmod.MAX_BYTES = 10
    try:
        with pytest.raises(musicmod.MusicError, match="limit is 200 MB"):
            musicmod.take_file(big, work, "binx")
    finally:
        musicmod.MAX_BYTES = old
    assert sorted(p.name for p in (work / "music" / "binx").iterdir()) == ["Song One-2.wav", "Song One.wav"]


def test_a_project_track_resolves_only_inside_its_folder(tmp_path):
    work = tmp_path / "work"
    got = musicmod.take_file(_wav(tmp_path / "t.wav"), work, "b")
    assert musicmod.resolve("project/t.wav", work, "b", tmp_path) == got
    (tmp_path / "secret.wav").write_bytes(b"x")
    with pytest.raises(FileNotFoundError):
        musicmod.resolve("project/../../secret.wav", work, "b", tmp_path)
    with pytest.raises(FileNotFoundError):
        musicmod.resolve("project/missing.wav", work, "b", tmp_path)


def test_time_left_follows_progress_and_not_the_clock():
    import server
    f = server.eta_from_samples
    # no measurement yet: nothing is said (a countdown from a guess is what ran out early)
    assert f([], 100.0, 0.5, 5.0) is None
    assert f([], 100.0, 1.0, 40.0) is None
    # FINISH-24: a render that makes 2.7 % a minute is at 54 % after 20 minutes and has 17 to
    # go; the old figure (the first guess counted down by the clock) said "1 min left" there.
    rate = 2.7 / 60.0
    samples = [(t, rate * t) for t in range(0, 1201, 2)]
    left = f([(float(t), p) for t, p in samples], 1200.0, rate * 1200.0, 1200.0)
    assert 16 * 60 < left < 18 * 60, left            # ~17 minutes, not 1
    # a stalled stretch (the join writes nothing) lengthens the figure, never freezes it
    stalled = [(float(t), 50.0) for t in range(0, 121, 10)]
    assert f(stalled, 120.0, 50.0, 120.0) == pytest.approx(120.0)      # whole-run average
    assert f(stalled, 240.0, 50.0, 240.0) == pytest.approx(240.0)
    # a speeding render shortens it
    fast = [(float(t), 40.0 + (t - 100) * 1.0) for t in range(100, 141, 2)]
    assert f(fast, 140.0, 80.0, 140.0) < 30.0


# ---------------------------------------------------------------- the endpoints

def _put(client, name: str, data: bytes):
    return client.put("/api/music/upload", params={"name": name}, content=data)


def test_upload_lands_in_the_projects_folder_is_listed_and_served(client, project, tmp_path):
    import server
    wav = _wav(tmp_path / "up.wav").read_bytes()
    r = _put(client, "../../evil/My Bed.wav", wav)
    assert r.status_code == 200, r.text
    track = r.json()["track"]
    assert track["asset"] == "project/My Bed.wav" and track["project"] is True
    assert track["duration_s"] and 1.5 < track["duration_s"] < 2.5
    mine = project["work"] / "music" / server.STATE["footage"].name
    assert (mine / "My Bed.wav").is_file()
    assert not (project["assets"] / "music" / "My Bed.wav").exists()      # never the repo's assets
    # nothing was written anywhere else under the work dir's parents
    assert not (project["work"].parent / "evil").exists()
    assert not list(mine.glob(".*.part*"))
    # listed with the repo's tracks, project's first
    listed = client.get("/api/assets").json()["music"]
    assert [t["asset"] for t in listed][:2] == ["project/My Bed.wav", "music/bed.wav"]
    assert client.get("/media/asset/project/My Bed.wav").status_code == 200
    assert client.get("/media/asset/project/..%2f..%2fetc%2fpasswd").status_code == 404
    # a second one of the same name is kept, as -2
    assert _put(client, "My Bed.wav", wav).json()["track"]["asset"] == "project/My Bed-2.wav"


def test_upload_refuses_what_is_not_music_in_plain_words(client, project, tmp_path):
    import server
    wav = _wav(tmp_path / "up2.wav").read_bytes()
    r = _put(client, "song.ogg", wav)
    assert r.status_code == 400 and "mp3, wav, m4a or flac" in r.json()["detail"]
    r = _put(client, "song.mp3", b"not music at all, just text")
    assert r.status_code == 400 and "does not look like a music file" in r.json()["detail"]
    r = _put(client, "empty.wav", b"")
    assert r.status_code == 400
    old = musicmod.MAX_BYTES
    musicmod.MAX_BYTES = 1000
    try:
        r = _put(client, "toobig.wav", wav)
        assert r.status_code == 413 and "limit" in r.json()["detail"] and "200 MB" in r.json()["detail"]
    finally:
        musicmod.MAX_BYTES = old
    mine = project["work"] / "music" / server.STATE["footage"].name
    assert not any(p.name.startswith("song") or p.name.startswith("toobig") or p.name.startswith("empty")
                   for p in mine.iterdir())
    assert not list(mine.glob(".*part*"))


def test_a_file_by_path_is_added_and_a_bad_path_says_why(client, project, tmp_path):
    src = _wav(tmp_path / "ByPath.wav")
    r = client.post("/api/music/link", json={"path": str(src)})
    assert r.status_code == 200 and r.json()["track"]["asset"] == "project/ByPath.wav"
    assert client.post("/api/music/link", json={"path": str(tmp_path)}).status_code == 400
    r = client.post("/api/music/link", json={"path": str(tmp_path / "gone.mp3")})
    assert r.status_code == 400 and "no file at that path" in r.json()["detail"]
    r = client.post("/api/music/link", json={"path": ""})
    assert r.status_code == 400


def test_a_project_track_saves_into_the_cut_and_survives_a_reload(client, project, tmp_path):
    _put(client, "Kept.wav", _wav(tmp_path / "k.wav").read_bytes())
    cut = client.get("/api/project").json()
    r = client.put("/api/project", json={"segments": cut["segments"], "music": {"asset": "project/Kept.wav"}})
    assert r.status_code == 200, r.text
    assert client.get("/api/project").json()["music"]["asset"] == "project/Kept.wav"
    bad = client.put("/api/project", json={"segments": cut["segments"], "music": {"asset": "project/nope.wav"}})
    assert bad.status_code == 400 and "music not found" in bad.json()["detail"]


def test_the_clips_audio_info_says_the_camera_wav_is_not_used(client, project):
    clip = project["footage"] / "CLIP_A.MP4"
    wav = clip.with_suffix(".WAV")
    wav.write_bytes(b"RIFF")
    try:
        rows = {c["clip"]: c for c in client.get("/api/clips").json()["clips"]}
        a = rows["CLIP_A.MP4"]["audio"]
        assert a["wav"] == "CLIP_A.WAV" and a["wav_used"] is False
        assert a["wav_note"].startswith("camera sound: a .WAV with the same name (CLIP_A.WAV) is beside it, not used")
        assert not (rows["CLIP_B.MP4"]["audio"] or {}).get("wav")
    finally:
        wav.unlink()


def test_the_name_check_says_dash_two_before_not_after(client):
    r = client.get("/api/export/name", params={"folder": "t", "name": "film"}).json()
    assert r["exists"] is False and r["writes_name"] == "film.mp4"
    import server
    folder = server.export_root() / "t"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "film.mp4").write_bytes(b"x")
    try:
        r = client.get("/api/export/name", params={"folder": "t", "name": "film"}).json()
        assert r["exists"] is True and r["writes_name"] == "film-2.mp4"
        assert r["path"].endswith("/t/film.mp4") and r["writes"].endswith("/t/film-2.mp4")
        assert client.get("/api/export").json()["sound"]["limiter_dbtp"] == -1.0
    finally:
        (folder / "film.mp4").unlink()


def test_an_export_reads_the_cut_as_it_was_when_it_started(client, project, tmp_path, monkeypatch):
    """The render's EDL is written once, at the request. An edit made while it renders goes
    to the cut and not to this film; the bed is the file chosen, by path, in the render's EDL."""
    import server
    _put(client, "Snap.wav", _wav(tmp_path / "s.wav", 8.0).read_bytes())
    segs = [{"id": "e1", "clip": "CLIP_A.MP4", "in": 0.5, "out": 2.5, "why": "one"},
            {"id": "e2", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "two"}]
    assert client.put("/api/project", json={"segments": segs, "music": {"asset": "project/Snap.wav"}}).status_code == 200
    real = server._run_render
    monkeypatch.setattr(server, "_run_render", lambda job, cmd: (time.sleep(1.5), real(job, cmd))[1])
    r = client.post("/api/render", json={"segments": segs, "preset": "quick", "name": "Snap"})
    assert r.status_code == 200, r.text
    job = r.json()["job"]
    render_edl = server.STATE["work"] / f"render_{job}.json"
    before = render_edl.read_bytes()
    saved = json.loads(before)
    mine = project["work"] / "music" / server.STATE["footage"].name / "Snap.wav"
    assert saved["effects_music"]["asset"] == str(mine)             # the file, not a name that can move
    assert [s["out"] for s in saved["segments"]] == [2.5, 2.0]
    # the editor goes on: a longer first shot, and no music
    client.put("/api/project", json={"segments": [{**segs[0], "out": 5.0}], "music": None})
    if True:  # noqa
        assert render_edl.read_bytes() == before
        for _ in range(240):
            st = client.get(f"/api/render/{job}").json()
            if st["state"] != "running":
                break
            time.sleep(0.5)
        assert st["state"] == "done", st.get("detail")
        assert st["export"] is True and st["cut_name"] is not None
        out = Path(st["output"])
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                    "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout)
        assert 3.8 < dur < 4.3, dur                                  # 2 s + 2 s: not the edited 5 s
        # the bed was mixed in from the snapshot's path (the cut now has none)
        vol = subprocess.run(["ffmpeg", "-nostdin", "-i", str(out), "-af", "volumedetect", "-f", "null", "-"],
                             capture_output=True, text=True).stderr
        assert "max_volume" in vol
        listed = {x["name"]: x for x in client.get("/api/renders").json()["renders"]}
        assert listed[out.name]["export"] is True and listed[out.name]["path"] == str(out)


# ---------------------------------------------------------------- the board (real Chromium)

pw_sync = pytest.importorskip("playwright.sync_api", reason="playwright not installed")

SEGMENTS = [
    {"id": "e1", "clip": "CLIP_A.MP4", "in": 0.5, "out": 3.5, "why": "one"},
    {"id": "e2", "clip": "CLIP_B.MP4", "in": 0.0, "out": 3.0, "why": "two"},
    {"id": "e3", "clip": "CLIP_C.MP4", "in": 1.0, "out": 4.0, "why": "three"},
]
DIALOG = "[data-testid=export-dialog]"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed(project) -> None:
    seed = {"variant": "T", "title": "test cut", "orient": "none", "story": "",
            "target_s": [5, 30], "segments": [dict(s) for s in SEGMENTS]}
    Path(project["edl"]).write_text(json.dumps(seed, indent=1), encoding="utf-8")


@pytest.fixture(scope="module")
def live_server(project):
    import uvicorn
    import server

    original = project["edl"].read_text(encoding="utf-8")
    _seed(project)
    server.configure(project["edl"], project["footage"], project["sidecars"],
                     project["work"], proxies=False, assets=project["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in project["stems"]])
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=10)
    project["edl"].write_text(original, encoding="utf-8")


@pytest.fixture(scope="module")
def browser(live_server):
    with pw_sync.sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        yield b
        b.close()


@pytest.fixture
def board(browser, live_server, project, monkeypatch):
    import server
    monkeypatch.setattr(server, "RENDER_TICK_S", 0.05)
    _seed(project)
    mine = project["work"] / "music" / server.STATE["footage"].name
    if mine.exists():
        for p in mine.iterdir():
            p.unlink()
    made = []

    def page():
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.goto(live_server)
        pg.wait_for_selector("#tl .blk")
        pg.wait_for_function(f"tl.state && segs.length === {len(SEGMENTS)}", timeout=15000)
        made.append((ctx, pg))
        return pg

    yield page
    errors = [e for _, pg in made for e in pg.errors]
    for ctx, _ in made:
        ctx.close()
    assert errors == [], errors


def test_add_music_from_the_audio_tab_lists_it_selects_it_and_survives_a_reload(board, tmp_path):
    pg = board()
    pg.click('button.tool[data-tool="sound"]')
    js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "drop an mp3" not in js and "assets/music/ and reload" not in js     # the empty state names the button
    assert "No music yet. Add a track with Add music…" in js
    # a file the board does not take: said in words, nothing added
    ogg = tmp_path / "x.ogg"
    ogg.write_bytes(b"OggS")
    pg.set_input_files("[data-testid=music-file]", str(ogg))
    pg.wait_for_function("document.querySelector('#musicHint').textContent.includes('mp3, wav, m4a or flac')")
    assert pg.eval_on_selector_all("#musicTrack option", "o => o.length") == 2        # no music + repo's bed
    # a wav from the viewer's computer
    song = _wav(tmp_path / "Night Drive.wav", 3.0)
    pg.set_input_files("[data-testid=music-file]", str(song))
    pg.wait_for_function("document.querySelector('#musicTrack').value === 'project/Night Drive.wav'")
    assert "it is the music bed now" in pg.inner_text("#musicHint")
    assert pg.eval_on_selector("#musicTrack", "s => [...s.options].map(o => o.textContent)")[1].startswith("Night Drive.wav")
    pg.wait_for_function("P && P.music && P.music.asset === 'project/Night Drive.wav' || true")
    pg.evaluate("roughcutFlush && roughcutFlush()")
    # a reload: the track is still in the list and is still the bed
    pg.reload()
    pg.wait_for_selector("#tl .blk")
    pg.click('button.tool[data-tool="sound"]')
    pg.wait_for_function("document.querySelector('#musicTrack').value === 'project/Night Drive.wav'", timeout=10000)
    # a second track while one is the bed is listed, not swapped in
    pg.set_input_files("[data-testid=music-file]", str(_wav(tmp_path / "Second.wav", 2.0)))
    pg.wait_for_function("document.querySelector('#musicHint').textContent.includes('pick it from the list')")
    assert pg.input_value("#musicTrack") == "project/Night Drive.wav"
    # by path
    pg.click("[data-testid=music-path-btn]")
    pg.fill("[data-testid=music-path]", str(tmp_path / "missing.mp3"))
    pg.click("[data-testid=music-path-go]")
    pg.wait_for_function("document.querySelector('#musicHint').textContent.includes('no file at that path')")
    pg.fill("[data-testid=music-path]", str(_wav(tmp_path / "Bypath.wav", 2.0)))
    pg.click("[data-testid=music-path-go]")
    pg.wait_for_function("[...document.querySelectorAll('#musicTrack option')].some(o => o.value === 'project/Bypath.wav')")


def _open_export(pg):
    pg.locator("#tl .tl-ruler").click(position={"x": 2, "y": 2})
    pg.keyboard.press("Control+m")
    pg.wait_for_selector(f"{DIALOG} [data-testid=export-go]", timeout=8000)
    pg.wait_for_function("document.querySelector('#exSize').dataset.source !== 'measuring'", timeout=180000)


def test_the_dialog_says_the_sound_and_the_name_it_will_write(board, project):
    import server
    pg = board()
    _open_export(pg)
    sound = pg.inner_text("[data-testid=export-sound]")
    assert "AAC 192 kbps" in sound and "48 kHz stereo" in sound and "−1 dBTP" in sound
    pg.select_option("[data-testid=export-preset]", "master")
    assert "AAC 256 kbps" in pg.inner_text("[data-testid=export-sound]")
    pg.select_option("[data-testid=export-preset]", "quick")
    folder = server.export_root() / "nm"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "taken.mp4").write_bytes(b"x")
    try:
        pg.fill("#exFolder", "nm")
        pg.fill("[data-testid=export-name]", "free name")
        pg.wait_for_function("document.querySelector('#exGo').title.endsWith('/nm/free name.mp4')")
        assert pg.inner_text("[data-testid=export-name-note]") == ""
        pg.fill("[data-testid=export-name]", "taken")
        pg.wait_for_function("document.querySelector('#exNameNote').dataset.exists === 'yes'")
        note = pg.inner_text("[data-testid=export-name-note]")
        assert "already there" in note and "taken-2.mp4" in note and "nothing is overwritten" in note
        assert pg.get_attribute("#exGo", "title").endswith("/nm/taken-2.mp4")      # the file it will really write
    finally:
        (folder / "taken.mp4").unlink()


def test_an_export_goes_on_behind_the_board_and_is_of_the_cut_it_started_with(board, project, monkeypatch):
    import server
    real = server._run_render
    monkeypatch.setattr(server, "_run_render", lambda job, cmd: (time.sleep(4.0), real(job, cmd))[1])
    pg = board()
    _open_export(pg)
    pg.fill("[data-testid=export-name]", "behind the board")
    pg.fill("#exFolder", "bg")
    pg.click("[data-testid=export-go]")
    pg.wait_for_selector("[data-testid=export-hide]", timeout=15000)
    assert "as it was at" in pg.inner_text("[data-testid=export-snapshot]")
    pg.click("[data-testid=export-hide]")
    assert pg.evaluate("document.getElementById('exportDialog').open") is False
    # the board is the editor's: a click on a shot selects it, a key edits the cut
    pg.locator("#tl .blk").nth(1).click()
    pg.wait_for_function("tl.state.sel.size === 1")
    pg.keyboard.press("Delete")
    pg.wait_for_function("segs.length === 2", timeout=5000)
    # the pill: one row for the export, with the cut it is of and a way back to the dialog
    row = pg.locator("#progress .job[data-kind=render]")
    row.wait_for(timeout=5000)
    assert "as it was at" in row.get_attribute("title") and "not in this file" in row.get_attribute("title")
    row.locator(".jshow").click()
    assert pg.evaluate("document.getElementById('exportDialog').open") is True
    pg.click("[data-testid=export-hide]")
    # it ends while put away: a line says so, and Export brings the done screen back
    pg.wait_for_function("document.querySelector('#toast, .toast') && "
                         "/Exported behind the board/.test(document.body.innerText)", timeout=120000)
    pg.keyboard.press("Control+m")
    pg.wait_for_selector("[data-testid=export-done]", timeout=5000)
    path = pg.get_attribute("[data-testid=export-done]", "data-path")
    assert path.endswith("/bg/behind the board.mp4")
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                               capture_output=True, text=True).stdout)
    assert 8.5 < dur < 9.8, dur                       # the three 3 s shots it started with, not the two left
    # the header's Export names the file that was really written
    pg.click("[data-testid=export-cancel]")
    assert "behind the board.mp4" in pg.get_attribute("#makeFilm", "title")


def test_cancel_keeps_what_was_typed_and_leaves_nothing_behind(board, project, monkeypatch):
    import server
    real = server._run_render
    monkeypatch.setattr(server, "_run_render", lambda job, cmd: (time.sleep(8.0), real(job, cmd))[1])
    pg = board()
    _open_export(pg)
    pg.fill("[data-testid=export-name]", "typed name")
    pg.fill("#exFolder", "cx")
    pg.click("[data-testid=export-go]")
    pg.wait_for_selector("#exCancel[value='Cancel Export']", timeout=15000)
    pg.click("#exCancel")
    pg.wait_for_selector("[data-testid=export-go]", timeout=30000)
    assert pg.input_value("[data-testid=export-name]") == "typed name"
    assert pg.input_value("#exFolder") == "cx"
    folder = server.export_root() / "cx"
    assert not folder.exists() or not list(folder.glob("*.mp4"))


def test_the_pill_and_the_dialog_read_the_same_whole_number():
    # the pill used Math.round(pct) and the dialog Math.floor(progress_pct): 1% against 2%
    js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "Math.floor(Number(j.progress_pct))" in js
