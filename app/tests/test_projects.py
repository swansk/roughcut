"""The open screen's other two server-side pieces (INTAKE I5.3's slider, I5.4's picker):
the look interval through pricing and the index, and pointing the board at another bin
without a relaunch."""

from __future__ import annotations

import json
import shutil
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
    import server

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
            "current": False}
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
