"""The open screen's other two server-side pieces (INTAKE I5.3's slider, I5.4's picker):
the look interval through pricing and the index, and pointing the board at another bin
without a relaunch."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import progress  # noqa: E402

from conftest import _make_clip  # noqa: E402
from test_index import _stub_tools, _wait_index  # noqa: E402
from test_server import _fresh  # noqa: E402


# ------------------------------------------------------------- the interval

def test_the_price_reprices_per_interval_and_the_default_is_the_tools(tmp_path, project,
                                                                      monkeypatch):
    """One slider, re-pricing live: /api/status carries every interval's price for the
    same pending clips, so the page needs no round trip. At 100 s a clip, a sheet of 30
    frames covers 120 s at 4 s (one sheet) and 30 s at 1 s (four)."""
    import server

    with _fresh(tmp_path, project) as c:
        monkeypatch.setattr(server, "clip_duration", lambda clip: 100.0)
        v = c.get("/api/status").json()["visual"]
        assert v["interval_s"] == 4.0 and v["intervals"] == [4.0, 3.0, 2.0, 1.0]
        fine = 3 * server.FINE_WINDOWS_PER_CLIP * server.FINE_USD_PER_WINDOW
        assert v["by_interval"]["4"] == round(3 * 1 * server.VISUAL_USD_PER_SHEET + fine, 2)
        assert v["by_interval"]["2"] == round(3 * 2 * server.VISUAL_USD_PER_SHEET + fine, 2)
        assert v["by_interval"]["1"] == round(3 * 4 * server.VISUAL_USD_PER_SHEET + fine, 2)
        assert v["projected_usd"] == v["by_interval"]["4"]
        assert "--interval" in server.visual_cmd([], False)
        assert server.visual_cmd([], False)[-1] == "4"


def test_the_index_takes_the_interval_and_keeps_it_in_the_project(tmp_path, project,
                                                                   monkeypatch):
    """POST /api/index {interval_s} is the slider's word: written to the EDL so a
    resumed index looks at the rest of the bin the same way, read by the command and
    by the look stage's cost. Anything off the slider is refused."""
    import server

    with _fresh(tmp_path, project, sidecars=project["sidecars"], visual=None) as c:
        assert c.post("/api/index", json={"interval_s": 2.5}).status_code == 400
        assert c.post("/api/index", json={"interval_s": "fine"}).status_code == 400
        assert server.look_interval() == 4.0
        real_cmd = server.visual_cmd
        _stub_tools(server, monkeypatch, tmp_path)
        r = c.post("/api/index", json={"interval_s": 2})
        assert r.status_code == 200, r.text
        assert server.look_interval() == 2.0
        on_disk = json.loads(server.STATE["edl"].read_text(encoding="utf-8"))
        assert on_disk["look"] == {"interval_s": 2.0}
        s = _wait_index(c, r.json()["job"])
        assert s["state"] == "done", s
        assert c.get("/api/index").json()["interval_s"] == 2.0
        assert c.get("/api/status").json()["visual"]["interval_s"] == 2.0
        # the real command reads it back — the stub was only for the run
        cmd = real_cmd(["CLIP_A"], False)
        assert cmd[cmd.index("--interval") + 1] == "2"


# ------------------------------------------------------------ the picker

def test_the_picker_lists_the_bins_it_knows_and_the_folders_next_door(tmp_path, project):
    other = project["footage"].parent / "other-trip"
    other.mkdir(exist_ok=True)
    if not (other / "GX01.MP4").exists():
        _make_clip(other / "GX01.MP4")
    empty = project["footage"].parent / "notes"
    empty.mkdir(exist_ok=True)
    with _fresh(tmp_path, project) as c:
        p = c.get("/api/projects").json()
        assert p["current"]["name"] == project["footage"].name and p["current"]["clips"] == 3
        by = {r["name"]: r for r in p["projects"]}
        assert p["projects"][0]["current"] is True and p["projects"][0]["known"] is True
        assert by["other-trip"] == {
            "name": "other-trip", "footage": str(other), "known": False, "opened": None,
            "exists": True, "clips": 1, "cut": False, "segments": 0, "journal": False,
            "cut_name": None, "cuts": 0, "current": False}
        assert p["current"]["cut"] == "main"
        assert by[project["footage"].name]["cut_name"] == "main"
        assert by[project["footage"].name]["cuts"] == 1
        assert "notes" not in by, "a folder without video is not a bin"
        # the registry under --work remembers the bin this board opened
        known = json.loads((tmp_path / "projects.json").read_text(encoding="utf-8"))
        assert known[project["footage"].name]["footage"] == str(project["footage"])


def test_opening_another_bin_repoints_everything_without_a_relaunch(tmp_path, project):
    """I5.4: the same path main() takes, at runtime. The other bin gets its own scaffolded
    EDL, sidecars, proxies and journal paths; the per-clip caches do not leak across."""
    import server

    other = project["footage"].parent / "second-bin"
    other.mkdir(exist_ok=True)
    if not (other / "CLIP_A.MP4").exists():        # the same stem as the first bin's
        _make_clip(other / "CLIP_A.MP4")
    with _fresh(tmp_path, project) as c:
        assert c.get("/api/status").json()["clips"] == 3
        server.clip_duration("CLIP_A.MP4")          # warm a per-clip cache
        assert "durations" in server.STATE

        r = c.post("/api/projects/open", json={"footage": str(other)})
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "second-bin" and r.json()["clips"] == 1
        assert r.json()["edl_created"] is True
        assert "durations" not in server.STATE, "the first bin's durations must not leak"
        s = c.get("/api/status").json()
        assert s["footage"] == str(other) and s["clips"] == 1 and s["analysed"] == 0
        assert Path(s["edl"]) == tmp_path / "projects" / "second-bin.edl.json"
        assert Path(s["sidecars"]) == tmp_path / "audio" / "second-bin"
        assert server.journal_path() == tmp_path / "index" / "second-bin.json"
        assert c.get("/api/index").json()["exists"] is False
        # both bins are known now; the one just opened is first
        names = [p["name"] for p in c.get("/api/projects").json()["projects"]]
        assert names[0] == "second-bin" and project["footage"].name in names

        # and back — the first bin's own scaffolded EDL, not a second scaffold
        r = c.post("/api/projects/open", json={"footage": str(project["footage"])})
        assert r.status_code == 200 and r.json()["clips"] == 3
        assert r.json()["edl_created"] is False
        assert c.get("/api/project").json()["title"] == project["footage"].name


def test_opening_refuses_bad_folders_and_a_running_job(tmp_path, project):
    import server

    with _fresh(tmp_path, project) as c:
        assert c.post("/api/projects/open", json={}).status_code == 400
        assert c.post("/api/projects/open", json={"footage": str(tmp_path / "nope")}).status_code == 400
        empty = project["footage"].parent / "empty-folder"
        empty.mkdir(exist_ok=True)
        assert c.post("/api/projects/open", json={"footage": str(empty)}).status_code == 400
        server.INDEXES["stuck"] = progress.Job("index", "Indexing", id="stuck", state="running")
        try:
            r = c.post("/api/projects/open", json={"footage": str(project["footage"])})
            assert r.status_code == 409 and "running" in r.json()["detail"]
        finally:
            server.INDEXES.clear()
        # nothing changed
        assert c.get("/api/status").json()["footage"] == str(project["footage"])


# --------------------------------------------------------------- the cuts
#
# One bin, several cuts (Karl, 2026-09-08: "saving copies so we can try different
# things"). A cut is a whole project file; a copy is a second one of the same shape
# under `projects/<bin>/`, and the registry remembers which one each bin is on.

def _cut_names(c) -> list[str]:
    return [r["name"] for r in c.get("/api/cuts").json()["cuts"]]


def _other_bin(project, name: str) -> Path:
    folder = project["footage"].parent / name
    folder.mkdir(exist_ok=True)
    if not (folder / "GX01.MP4").exists():
        _make_clip(folder / "GX01.MP4")
    return folder


def test_a_bin_starts_with_one_cut_called_main_and_everything_says_so(tmp_path, project):
    with _fresh(tmp_path, project) as c:
        cuts = c.get("/api/cuts").json()
        assert cuts["bin"] == project["footage"].name
        assert [r["name"] for r in cuts["cuts"]] == ["main"]
        row = cuts["cuts"][0]
        assert row["current"] and row["default"] and row["segments"] == 0 and row["from"] is None
        assert Path(row["path"]) == tmp_path / "projects" / f"{project['footage'].name}.edl.json"
        assert cuts["current"] == row
        assert c.get("/api/status").json()["cut"] == "main"
        assert c.get("/api/project").json()["cut"] == "main"
        assert c.get("/api/projects").json()["current"]["cut"] == "main"


def test_a_copy_is_the_whole_file_and_the_board_moves_to_it(tmp_path, project):
    """Save a copy under a name and work on it: the copy carries everything the file
    had, the original is untouched by what happens next, and the registry remembers
    which one the bin is on."""
    import server

    with _fresh(tmp_path, project, sidecars=project["sidecars"]) as c:
        shot = {"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"}
        assert c.put("/api/project", json={"segments": [shot], "story": "the milk"}).status_code == 200
        original = server.STATE["edl"]
        r = c.post("/api/cuts/copy", json={"name": "  Rocks   runner "})
        assert r.status_code == 200, r.text
        copy = r.json()["copy"]
        assert copy["name"] == "Rocks runner" and copy["from"] == "main" and copy["current"]
        assert copy["segments"] == 1 and copy["duration_s"] == 2.0 and copy["default"] is False
        assert Path(copy["path"]) == (tmp_path / "projects" / project["footage"].name
                                      / "rocks-runner.edl.json")
        assert server.STATE["edl"] == Path(copy["path"]) and server.STATE["edl_created"] is False
        on_disk = json.loads(Path(copy["path"]).read_text(encoding="utf-8"))
        assert on_disk["segments"] == [shot] and on_disk["story"] == "the milk"
        assert on_disk["cut"]["name"] == "Rocks runner" and on_disk["cut"]["from"] == "main"
        assert on_disk["orient"] == "auto" and on_disk["title"] == project["footage"].name
        # editing the copy leaves the original alone, and the name survives a save
        assert c.put("/api/project", json={"segments": [], "story": "the milk"}).status_code == 200
        assert json.loads(original.read_text(encoding="utf-8"))["segments"] == [shot]
        assert json.loads(Path(copy["path"]).read_text(encoding="utf-8"))["cut"]["name"] == "Rocks runner"
        assert c.get("/api/status").json()["cut"] == "Rocks runner"
        assert c.get("/api/project").json()["cut"] == "Rocks runner"
        assert _cut_names(c) == ["Rocks runner", "main"]           # the one on the board first
        known = json.loads((tmp_path / "projects.json").read_text(encoding="utf-8"))
        assert known[project["footage"].name]["edl"] == copy["path"]
        # a name in use (however it is cased), an empty one, one with nothing usable
        assert c.post("/api/cuts/copy", json={"name": "rocks RUNNER"}).status_code == 409
        assert c.post("/api/cuts/copy", json={"name": "   "}).status_code == 400
        assert c.post("/api/cuts/copy", json={"name": "///"}).status_code == 400
        assert c.post("/api/cuts/copy", json={}).status_code == 400


def test_a_checkpoint_copy_leaves_you_where_you_are(tmp_path, project):
    import server

    with _fresh(tmp_path, project) as c:
        r = c.post("/api/cuts/copy", json={"name": "before lunch", "open": False})
        assert r.status_code == 200, r.text
        assert r.json()["current"]["name"] == "main" and r.json()["copy"]["current"] is False
        assert server.STATE["edl"] == tmp_path / "projects" / f"{project['footage'].name}.edl.json"
        assert _cut_names(c) == ["main", "before lunch"]
        assert c.get("/api/status").json()["cut"] == "main"


def test_opening_a_cut_switches_and_refuses_strangers_and_a_running_job(tmp_path, project):
    import server

    with _fresh(tmp_path, project) as c:
        copy = c.post("/api/cuts/copy", json={"name": "b", "open": False}).json()["copy"]
        main = c.get("/api/cuts").json()["current"]
        stranger = tmp_path / "elsewhere.edl.json"
        stranger.write_text("{}", encoding="utf-8")
        assert c.post("/api/cuts/open", json={"path": str(stranger)}).status_code == 404
        assert c.post("/api/cuts/open", json={}).status_code == 400
        server.RENDERS["stuck"] = progress.Job("render", "Rendering", id="stuck", state="running")
        try:
            r = c.post("/api/cuts/open", json={"path": copy["path"]})
            assert r.status_code == 409 and "running" in r.json()["detail"]
            # a checkpoint is fine mid-job; a copy that would move the board is not
            assert c.post("/api/cuts/copy", json={"name": "c", "open": False}).status_code == 200
            assert c.post("/api/cuts/copy", json={"name": "d"}).status_code == 409
            # the one you are on is a no-op, even now
            assert c.post("/api/cuts/open", json={"path": main["path"]}).status_code == 200
        finally:
            server.RENDERS.clear()
        assert c.get("/api/status").json()["cut"] == "main"
        r = c.post("/api/cuts/open", json={"path": copy["path"]})
        assert r.status_code == 200 and r.json()["current"]["name"] == "b"
        assert server.STATE["edl"] == Path(copy["path"])
        assert c.get("/api/project").json()["cut"] == "b"
        r = c.post("/api/cuts/open", json={"path": main["path"]})
        assert r.status_code == 200 and r.json()["current"]["name"] == "main"
        assert server.STATE["edl"] == Path(main["path"])


def test_rename_and_delete_keep_the_files_where_they_are(tmp_path, project):
    """The name lives inside the file, so a rename moves nothing; a delete moves the
    file to the bin's trash rather than away, and never the one on the board."""
    with _fresh(tmp_path, project) as c:
        copy = c.post("/api/cuts/copy", json={"name": "b", "open": False}).json()["copy"]
        r = c.post("/api/cuts/rename", json={"path": copy["path"], "name": "the long one"})
        assert r.status_code == 200, r.text
        assert _cut_names(c) == ["main", "the long one"]
        assert Path(copy["path"]).exists()
        assert json.loads(Path(copy["path"]).read_text(encoding="utf-8"))["cut"]["name"] == "the long one"
        assert c.post("/api/cuts/rename", json={"path": copy["path"], "name": "MAIN"}).status_code == 409
        assert c.post("/api/cuts/rename", json={"path": copy["path"], "name": ""}).status_code == 400
        main = c.get("/api/cuts").json()["current"]
        assert c.post("/api/cuts/rename", json={"path": main["path"], "name": "first cut"}).status_code == 200
        assert c.get("/api/status").json()["cut"] == "first cut"
        assert c.get("/api/cuts").json()["current"]["default"] is True
        assert c.post("/api/cuts/delete", json={"path": main["path"]}).status_code == 409
        r = c.post("/api/cuts/delete", json={"path": copy["path"]})
        assert r.status_code == 200, r.text
        assert not Path(copy["path"]).exists()
        trashed = Path(r.json()["trashed"])
        assert trashed.parent == tmp_path / "projects" / project["footage"].name / "trash"
        assert trashed.exists() and trashed.name.startswith("b.")
        assert _cut_names(c) == ["first cut"]
        assert c.post("/api/cuts/delete", json={"path": copy["path"]}).status_code == 404
        # the name is free again
        assert c.post("/api/cuts/copy", json={"name": "b", "open": False}).status_code == 200


def test_a_bin_reopens_on_the_cut_it_was_left_on(tmp_path, project):
    """The registry's `edl` used to be written and never read: a cut named with --edl
    was forgotten the moment you switched bins. A bin comes back on the cut you left
    it on now — and can be asked for a particular one, if it is one of its own."""
    import server

    other = _other_bin(project, "cuts-bin")
    with _fresh(tmp_path, project) as c:
        copy = c.post("/api/cuts/copy", json={"name": "b"}).json()["copy"]
        assert server.STATE["edl"] == Path(copy["path"])
        assert c.post("/api/projects/open", json={"footage": str(other)}).status_code == 200
        assert c.get("/api/cuts").json()["current"]["name"] == "main"
        row = next(p for p in c.get("/api/projects").json()["projects"]
                   if p["name"] == project["footage"].name)
        assert row["cut_name"] == "b" and row["cuts"] == 2 and row["cut"] is True
        r = c.post("/api/projects/open", json={"footage": str(project["footage"])})
        assert r.status_code == 200 and r.json()["cut"] == "b"
        assert server.STATE["edl"] == Path(copy["path"])
        main = next(r for r in c.get("/api/cuts").json()["cuts"] if r["name"] == "main")
        assert c.post("/api/projects/open", json={"footage": str(other)}).status_code == 200
        r = c.post("/api/projects/open",
                   json={"footage": str(project["footage"]), "edl": main["path"]})
        assert r.status_code == 200 and r.json()["cut"] == "main"
        assert server.STATE["edl"] == Path(main["path"])
        assert c.post("/api/projects/open",
                      json={"footage": str(other), "edl": main["path"]}).status_code == 400


def test_a_cut_named_on_the_command_line_stays_listed(tmp_path, project):
    """`--edl research/edl/B1-variantB.json`: a cut outside --work. It is the cut,
    named after its file; a copy of it lands under --work; and after the board moves
    to the copy the original is still in the list, because the registry kept it."""
    import server

    with _fresh(tmp_path, project, edl=project["edl"], sidecars=project["sidecars"]) as c:
        cuts = c.get("/api/cuts").json()
        assert [r["name"] for r in cuts["cuts"]] == ["edl"]
        assert cuts["current"]["default"] is False and cuts["current"]["segments"] == 2
        copy = c.post("/api/cuts/copy", json={"name": "tighter"}).json()["copy"]
        assert Path(copy["path"]).parent == tmp_path / "projects" / project["footage"].name
        assert copy["from"] == "edl"
        assert _cut_names(c) == ["tighter", "edl"]
        known = json.loads((tmp_path / "projects.json").read_text(encoding="utf-8"))
        assert known[project["footage"].name]["cuts"] == [str(project["edl"])]
        assert c.post("/api/cuts/open", json={"path": str(project["edl"])}).status_code == 200
        assert c.get("/api/status").json()["cut"] == "edl"
    # and a render's download name says which cut it came from — except the bin's own
    bin_name = project["footage"].name
    assert server.download_name({"cut": "tighter", "segments": 2}, Path("x.mp4"), None) \
        == f"{bin_name}-tighter-2shots-preview.mp4"
    assert server.download_name({"cut": "main", "segments": 2}, Path("x.mp4"), None) \
        == f"{bin_name}-2shots-preview.mp4"
