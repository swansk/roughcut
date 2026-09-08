"""The unattended index — POST /api/index driving the journal with stubbed tools.

The real tools mean a GPU, a model download and model calls per sheet; none of that
says anything about whether the orchestration honours the journal: priority order,
per-clip release, resume after a crash, added footage, the budget cap.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import journal  # noqa: E402

from test_server import _fresh, _stub_analyzer, _stub_fine, _stub_looker  # noqa: E402


def _stub_tools(server, monkeypatch, tmp_path, *, scan_windows=True):
    """Every tool the index runs, replaced by a script that writes the sidecar."""
    monkeypatch.setattr(server, "PROGRESS_TICK_S", 0.05)
    monkeypatch.setattr(server, "analyze_cmd", lambda skip, force: _stub_analyzer(
        tmp_path / "asr.py", server.STATE["sidecars"],
        [Path(c).stem for c in server.footage_clips() if Path(c).stem not in skip]))
    monkeypatch.setattr(server, "visual_cmd", lambda only, force: _stub_looker(
        tmp_path / "look.py", server.STATE["visual"], list(only)))

    def scan(only, windows_out, limit):
        Path(windows_out).write_text(
            json.dumps({s: [[0.5, 4.0]] if scan_windows else [] for s in only}),
            encoding="utf-8")
        return [sys.executable, "-c", "print('scanned')"]

    monkeypatch.setattr(server, "scan_cmd", scan)
    monkeypatch.setattr(server, "fine_cmd", lambda windows: _stub_fine(
        tmp_path / "fine.py", server.STATE["visual"],
        list(json.loads(Path(windows).read_text(encoding="utf-8")).keys())))


def _wait_index(client, job, timeout=40.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/job/{job}").json()
        if s["state"] not in ("running",):
            return s
        time.sleep(0.1)
    raise AssertionError(f"index did not finish: {s}")


def test_the_index_runs_unattended_and_releases_every_clip(tmp_path, project, monkeypatch):
    import server

    with _fresh(tmp_path, project, sidecars=project["sidecars"], visual=None) as c:
        _stub_tools(server, monkeypatch, tmp_path)
        assert c.get("/api/index").json() == {"exists": False, "running": False, "job": None}
        r = c.post("/api/index", json={"order": "priority"})
        assert r.status_code == 200, r.text
        assert c.post("/api/index", json={}).status_code == 409, "one at a time"
        s = _wait_index(c, r.json()["job"])
        assert s["state"] == "done", s
        assert s["detail"].startswith("3 of 3 clips released")

        st = c.get("/api/index").json()
        assert st["exists"] and not st["running"]
        assert sorted(st["released"]) == ["CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"]
        p = st["progress"]
        assert p["released"] == 3 and p["parked"] == 0
        # every stage settled: telemetry skipped (not integrated), the rest done
        j = journal.Journal.load(server.journal_path())
        for clip in ("CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"):
            assert j.state(clip, "telemetry") == "skipped"
            for stage in ("probe", "asr", "proxy", "look", "close", "picks"):
                assert j.state(clip, stage) == "done", (clip, stage)
            assert j.priority(clip) is not None
        # the priced stages were paid for, in the journal's own ledger
        assert j.cost_usd() == pytest.approx(
            3 * server.VISUAL_USD_PER_SHEET + 3 * server.FINE_USD_PER_WINDOW, abs=0.01)
        # the events file exists and the floor's release list is the journal's
        assert (server.STATE["visual"] / "events.json").exists()
        assert sorted(c.get("/api/picks").json()["released"]) == sorted(st["released"])


def test_the_index_resumes_after_a_crash_without_rebuying(tmp_path, project, monkeypatch):
    """A stage found running with no output is a crash: re-queued, attempts kept, and
    the finished sidecars are trusted as done — nothing already paid for runs again."""
    import server

    with _fresh(tmp_path, project, sidecars=project["sidecars"], visual=None) as c:
        _stub_tools(server, monkeypatch, tmp_path)
        # A journal from a run that died during CLIP_B's look, with CLIP_A fully done.
        vis = server.STATE["visual"]
        vis.mkdir(parents=True, exist_ok=True)
        (vis / "CLIP_A.visual.json").write_text(json.dumps({
            "clip": "CLIP_A.MP4", "moments": [], "unusable": [], "summary": ""}))
        (vis / "CLIP_A.fine.json").write_text(json.dumps({
            "clip": "CLIP_A.mp4", "windows_read": [], "moments": [], "unusable": []}))
        j = journal.Journal(server.journal_path(), bin=project["footage"].name)
        j.add_clips(["CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"])
        for clip in ("CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"):
            for stage in ("probe", "asr", "proxy"):
                j.start(clip, stage)
                j.finish(clip, stage)
            j.skip(clip, "telemetry", "test")
        j.start("CLIP_A.MP4", "look"); j.finish("CLIP_A.MP4", "look", 0.09)
        j.start("CLIP_A.MP4", "close"); j.finish("CLIP_A.MP4", "close", 0.073)
        j.start("CLIP_B.MP4", "look")            # ... and the process died here
        looked: list = []
        real = server.visual_cmd
        monkeypatch.setattr(server, "visual_cmd",
                            lambda only, force: (looked.extend(only), real(only, force))[1])

        s = _wait_index(c, c.post("/api/index", json={}).json()["job"])
        assert s["state"] == "done", s
        assert sorted(looked) == ["CLIP_B", "CLIP_C"], "CLIP_A's sheets were never re-bought"
        j2 = journal.Journal.load(server.journal_path())
        assert j2.state("CLIP_B.MP4", "look") == "done"
        assert j2.clips["CLIP_B.MP4"]["stages"]["look"]["attempts"] == 2, "the crash counted"
        assert any("re-queued" in e["what"] for e in j2.data["log"])
        assert j2.released_clips() == sorted(j2.released_clips()) and len(j2.released_clips()) == 3


def test_footage_added_later_is_indexed_and_released(tmp_path, project, monkeypatch):
    import server

    with _fresh(tmp_path, project, sidecars=project["sidecars"], visual=None) as c:
        _stub_tools(server, monkeypatch, tmp_path)
        s = _wait_index(c, c.post("/api/index", json={}).json()["job"])
        assert s["state"] == "done"
        # a friend's clip arrives
        shutil.copy(project["footage"] / "CLIP_A.MP4", project["footage"] / "CLIP_D.MP4")
        try:
            s = _wait_index(c, c.post("/api/index", json={}).json()["job"])
            assert s["state"] == "done", s
            st = c.get("/api/index").json()
            assert "CLIP_D.MP4" in st["released"] and len(st["released"]) == 4
            assert (server.STATE["sidecars"] / "CLIP_D.audio.json").exists()
            assert (server.STATE["proxy_dir"] / "CLIP_D.mp4").exists()
            assert "CLIP_D.MP4" in c.get("/api/picks").json()["released"]
        finally:
            (project["footage"] / "CLIP_D.MP4").unlink(missing_ok=True)
            for p in (server.STATE["sidecars"] / "CLIP_D.audio.json",
                      server.STATE["proxy_dir"] / "CLIP_D.mp4"):
                p.unlink(missing_ok=True)


def test_the_budget_cap_pauses_the_priced_stages_only(tmp_path, project, monkeypatch):
    import server
    from roughcut import config

    with _fresh(tmp_path, project, sidecars=project["sidecars"], visual=None) as c:
        _stub_tools(server, monkeypatch, tmp_path)
        monkeypatch.setattr(config, "budget_usd", lambda: 0.0)
        s = _wait_index(c, c.post("/api/index", json={}).json()["job"])
        assert s["state"] == "done"
        assert "budget cap" in s["detail"]
        st = c.get("/api/index").json()
        assert st["paused_priced"] is True and st["released"] == []
        j = journal.Journal.load(server.journal_path())
        assert j.state("CLIP_A.MP4", "proxy") == "done", "free stages ran"
        assert j.state("CLIP_A.MP4", "look") == "queued", "priced ones waited"
        assert not (server.STATE["visual"] / "CLIP_A.visual.json").exists()
        # the cap must not take the floor away: with the free stages done, the picks
        # come from the words — every clip is on the floor
        assert sorted(c.get("/api/picks").json()["released"]) == [
            "CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"]
        # the editor lifts the pause; the cap is checked again and holds
        s = _wait_index(c, c.post("/api/index", json={"resume_priced": True}).json()["job"])
        assert "budget cap" in s["detail"]
        monkeypatch.setattr(config, "budget_usd", lambda: 15.0)
        s = _wait_index(c, c.post("/api/index", json={"resume_priced": True}).json()["job"])
        assert s["state"] == "done" and s["detail"].startswith("3 of 3 clips released")
        assert c.get("/api/index").json()["paused_priced"] is False


def test_the_folder_reads_as_a_contact_sheet(client, project):
    """GET /api/clips: the free facts per clip, no journal yet on this bin."""
    d = client.get("/api/clips").json()
    assert d["journal"] is False and d["paused_priced"] is False
    assert [c["clip"] for c in d["clips"]] == ["CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"]
    a = d["clips"][0]
    assert a["duration"] == pytest.approx(6.0, abs=0.1)
    assert a["proxy"] and a["analysed"] and not a["looked"] and not a["closed"]
    assert a["telemetry"] is False, "the synthetic clips carry no gpmd stream"
    assert a["released"] is True
    assert a["journal"] is None and a["poster"].startswith("/media/poster/CLIP_A.jpg")
    # three files written seconds apart are one session
    assert d["sessions"] == 1 and {c["session"] for c in d["clips"]} == {1}
    assert d["total_s"] == pytest.approx(18.0, abs=0.3)


def test_the_contact_sheet_carries_the_journals_word_once_indexed(tmp_path, project,
                                                                  monkeypatch):
    import server

    with _fresh(tmp_path, project, sidecars=project["sidecars"], visual=None) as c:
        _stub_tools(server, monkeypatch, tmp_path)
        _wait_index(c, c.post("/api/index", json={}).json()["job"])
        d = c.get("/api/clips").json()
        assert d["journal"] is True
        a = d["clips"][0]["journal"]
        assert a["stages"]["telemetry"] == "skipped" and a["stages"]["close"] == "done"
        assert a["priority"] is not None and a["parked"] is None


def test_a_scan_with_nothing_to_look_at_skips_the_close_look(tmp_path, project, monkeypatch):
    import server

    with _fresh(tmp_path, project, sidecars=project["sidecars"], visual=None) as c:
        _stub_tools(server, monkeypatch, tmp_path, scan_windows=False)
        s = _wait_index(c, c.post("/api/index", json={}).json()["job"])
        assert s["state"] == "done" and s["detail"].startswith("3 of 3")
        j = journal.Journal.load(server.journal_path())
        assert j.state("CLIP_A.MP4", "close") == "skipped"
        assert not (server.STATE["visual"] / "CLIP_A.fine.json").exists()
