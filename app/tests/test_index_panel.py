"""The Index panel's server half (wave 17, Karl's K4): the per-clip report, the price of a
re-run, and the re-run itself — scoped to the clips and stages asked, the look at the
interval asked, the files replaced — with every tool stubbed (no model call, no GPU).

Karl, 2026-10-19: *"it is still not clear to me what indexing was run over the existing
footage, or how to re run / tune that"*. GET /api/index/clips says, per clip, which stages
ran, when, at what look interval and cost; POST /api/index/price and /api/index/rerun
re-run or re-tune one clip, a selection or the bin, the price first.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import journal  # noqa: E402

from test_index import _stub_tools, _wait_index  # noqa: E402
from test_server import _fresh  # noqa: E402


def stub_looker(server, monkeypatch, tmp_path, calls: list, *, sleep: float = 0.05):
    """visual_cmd → a script that writes the coarse sidecar with the interval the command
    asked for (params.interval_s, as visual_pass.py records it) and says it was forced."""
    script = tmp_path / "look_iv.py"
    script.write_text(
        "import json, sys, time\n"
        "from pathlib import Path\n"
        "out, interval, force = Path(sys.argv[1]), float(sys.argv[2]), sys.argv[3] == '1'\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        f"time.sleep({sleep})\n"
        "for stem in sys.argv[4:]:\n"
        "    (out / f'{stem}.visual.json').write_text(json.dumps({\n"
        "        'clip': f'{stem}.MP4', 'moments': [], 'unusable': [], 'summary': '',\n"
        "        'projected_usd': 0.09, 'sheets_read': 1, 'sheets_total': 1,\n"
        "        'frames_sampled': [i * interval for i in range(6)], 'forced': force,\n"
        "        'params': {'interval_s': interval}}))\n", encoding="utf-8")

    def cmd(only, force):
        iv = server.look_interval()
        if len(only) == 1:
            per = {Path(c).stem: v for c, v in server.clip_look_intervals().items()}
            iv = per.get(only[0]) or iv
        calls.append({"only": list(only), "force": bool(force), "interval": iv})
        return [sys.executable, str(script), str(server.STATE["visual"]), f"{iv:g}",
                "1" if force else "0", *only]
    monkeypatch.setattr(server, "visual_cmd", cmd)


def stub_hearer(server, monkeypatch, tmp_path, calls: list):
    """analyze_cmd → a script that rewrites the audio sidecar of every clip it is given
    (the stems not skipped), noting whether it was forced."""
    script = tmp_path / "hear.py"
    script.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "out, force = Path(sys.argv[1]), sys.argv[2] == '1'\n"
        "for stem in sys.argv[3:]:\n"
        "    (out / f'{stem}.audio.json').write_text(json.dumps({\n"
        "        'clip': f'{stem}.MP4', 'duration_s': 6.0, 'transcript': [], 'candidates': [],\n"
        "        'heard_again': force,\n"
        "        'summary': {'speech_fraction': 0.0, 'audio_usable': True}}))\n",
        encoding="utf-8")

    def cmd(skip, force):
        stems = [Path(c).stem for c in server.footage_clips() if Path(c).stem not in skip]
        calls.append({"stems": stems, "force": bool(force)})
        return [sys.executable, str(script), str(server.STATE["sidecars"]),
                "1" if force else "0", *stems]
    monkeypatch.setattr(server, "analyze_cmd", cmd)


@pytest.fixture
def indexed(tmp_path, project, monkeypatch):
    """A bin indexed end to end with stubbed tools: every clip heard, looked at (4 s),
    looked at closely and previewed. Its own sidecars dir, so a re-hear writes there."""
    import shutil
    import server

    side = tmp_path / "sidecars"
    shutil.copytree(project["sidecars"], side)
    monkeypatch.setenv("ROUGHCUT_BACKEND", "claude_cli")
    with _fresh(tmp_path, project, sidecars=side, visual=None) as c:
        _stub_tools(server, monkeypatch, tmp_path)
        looks: list = []
        heard: list = []
        stub_looker(server, monkeypatch, tmp_path, looks)
        stub_hearer(server, monkeypatch, tmp_path, heard)
        # the whole bin, proxies encoded for real: on a loaded box (nice 19) it takes a while
        s = _wait_index(c, c.post("/api/index", json={}).json()["job"], timeout=180.0)
        assert s["state"] == "done", s
        looks.clear()
        heard.clear()
        yield {"c": c, "server": server, "looks": looks, "heard": heard}


def _row(report: dict, clip: str) -> dict:
    return next(r for r in report["rows"] if r["clip"] == clip)


def test_the_report_says_what_ran_on_each_clip(indexed):
    c, server = indexed["c"], indexed["server"]
    rep = c.get("/api/index/clips").json()
    assert [r["clip"] for r in rep["rows"]] == ["CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"]
    j = journal.Journal.load(server.journal_path())
    for r in rep["rows"]:
        st = r["stages"]
        assert {k: v["state"] for k, v in st.items()} == {
            "asr": "done", "look": "done", "close": "done", "telemetry": "skipped",
            "proxy": "done"}, (r["clip"], st)
        # when: the journal's own finish time, to the second
        for s in ("asr", "look", "close", "proxy"):
            assert st[s]["at"] == pytest.approx(j.clips[r["clip"]]["stages"][s]["finished"]), s
        assert st["look"]["interval_s"] == 4.0 and st["look"]["sheets"] == 1
        # what it cost: the sidecar's measured number
        assert st["look"]["cost_usd"] == pytest.approx(0.09)
        assert st["close"]["cost_usd"] == pytest.approx(0.073)
        assert r["interval_s"] == 4.0 and r["tuned"] is False
        assert set(r["sheets_at"]) == {"4", "3", "2", "1"}
    s = rep["summary"]
    assert (s["clips"], s["heard"], s["looked"], s["close"], s["previews"]) == (3, 3, 3, 3, 3)
    assert s["looked_at"] == {"4": 3} and s["no_telemetry"] == 3
    assert rep["charged"] is False and rep["backend"] == "claude_cli"
    assert rep["intervals"] == [4.0, 3.0, 2.0, 1.0] and rep["running"] is False


def test_a_look_sidecar_whose_frames_are_a_count_does_not_take_the_picks_down(indexed):
    """A visual sidecar with `frames_sampled` as a count, not the list visual_pass.py
    writes, is re-derived from its interval like an old one, and /api/picks still answers
    (one bad sidecar took the whole bin's picks down: wave 17's gate)."""
    c, server = indexed["c"], indexed["server"]
    p = server.STATE["visual"] / "CLIP_B.visual.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["frames_sampled"] = 6
    p.write_text(json.dumps(d), encoding="utf-8")
    got = server.looked_at("CLIP_B.MP4")
    assert got["frames"] and got["frames"][:2] == [0.0, got["interval_s"]], got
    assert c.get("/api/picks").status_code == 200


def test_a_clip_is_retuned_and_looked_at_again_alone(indexed):
    """Re-tune one clip's look to 2 s and re-run it: its price first, then only its look
    runs (forced, at 2 s), the journal and its row say so, the others are untouched."""
    c, server, looks = indexed["c"], indexed["server"], indexed["looks"]
    before = journal.Journal.load(server.journal_path())
    a_look = dict(before.clips["CLIP_A.MP4"]["stages"]["look"])
    body = {"clips": ["CLIP_B.MP4"], "stages": ["look"], "interval_s": 2}
    price = c.post("/api/index/price", json=body).json()
    assert price["plan"] == {"CLIP_B.MP4": ["look", "picks"]}
    assert price["looks"] == 1 and price["close"] == 0 and price["replaces"]["look"] == 1
    assert price["usd"] == pytest.approx(server.sheets_for("CLIP_B.MP4", 2.0) * server.VISUAL_USD_PER_SHEET)
    assert set(price["by_interval"]) == {"4", "3", "2", "1"} and price["charged"] is False
    assert looks == [], "a price starts nothing"
    assert "clips" not in (server.read_edl().get("look") or {}), "and writes nothing"

    r = c.post("/api/index/rerun", json=body).json()
    assert r["joined"] is False and r["usd"] == price["usd"]
    s = _wait_index(c, r["job"])
    assert s["state"] == "done", s
    assert "CLIP_B looked at again" in s["detail"]
    assert looks == [{"only": ["CLIP_B"], "force": True, "interval": 2.0}]
    assert server.read_edl()["look"]["clips"] == {"CLIP_B.MP4": 2.0}
    side = json.loads((server.STATE["visual"] / "CLIP_B.visual.json").read_text())
    assert side["params"]["interval_s"] == 2.0 and side["forced"] is True
    j = journal.Journal.load(server.journal_path())
    b = j.clips["CLIP_B.MP4"]["stages"]["look"]
    assert b["state"] == "done" and b["force"] is False
    assert b["finished"] > before.clips["CLIP_B.MP4"]["stages"]["look"]["finished"]
    assert j.clips["CLIP_A.MP4"]["stages"]["look"] == a_look, "nothing else re-ran"
    assert j.state("CLIP_B.MP4", "picks") == "done"
    rep = c.get("/api/index/clips").json()
    rb, ra = _row(rep, "CLIP_B.MP4"), _row(rep, "CLIP_A.MP4")
    assert rb["stages"]["look"]["state"] == "done" and rb["stages"]["look"]["interval_s"] == 2.0
    assert rb["interval_s"] == 2.0 and rb["tuned"] is True
    assert ra["stages"]["look"]["interval_s"] == 4.0 and ra["interval_s"] == 4.0
    assert rep["summary"]["looked_at"] == {"4": 2, "2": 1}


def test_a_selection_is_heard_again_and_nothing_priced_runs(indexed):
    c, server = indexed["c"], indexed["server"]
    looks, heard = indexed["looks"], indexed["heard"]
    before = journal.Journal.load(server.journal_path())
    r = c.post("/api/index/rerun", json={"clips": ["CLIP_A.MP4", "CLIP_C.MP4"],
                                         "stages": ["asr"]}).json()
    assert r["usd"] == 0 and r["looks"] == 0
    s = _wait_index(c, r["job"])
    assert s["state"] == "done", s
    assert heard == [{"stems": ["CLIP_A", "CLIP_C"], "force": True}], "one batch, the two only"
    assert looks == [], "a free re-run buys no look"
    for stem in ("CLIP_A", "CLIP_C"):
        assert json.loads((server.STATE["sidecars"] / f"{stem}.audio.json").read_text())["heard_again"]
    assert "heard_again" not in json.loads((server.STATE["sidecars"] / "CLIP_B.audio.json").read_text())
    j = journal.Journal.load(server.journal_path())
    for clip in ("CLIP_A.MP4", "CLIP_C.MP4"):
        assert j.clips[clip]["stages"]["asr"]["finished"] > before.clips[clip]["stages"]["asr"]["finished"]
    assert j.clips["CLIP_B.MP4"]["stages"]["asr"] == before.clips["CLIP_B.MP4"]["stages"]["asr"]


def test_a_rerun_on_a_bin_never_looked_at_buys_only_what_it_asked(tmp_path, project, monkeypatch):
    """A bin only heard (a free run): re-hearing one clip must not look at the others,
    and the report says their looks were never run, not that they are queued."""
    import server

    with _fresh(tmp_path, project, sidecars=project["sidecars"], visual=None) as c:
        _stub_tools(server, monkeypatch, tmp_path)
        looks: list = []
        stub_looker(server, monkeypatch, tmp_path, looks)
        rep = c.get("/api/index/clips").json()
        assert rep["journal"] is False
        assert {r["stages"]["asr"]["state"] for r in rep["rows"]} == {"done"}, "heard on disk"
        assert {r["stages"]["look"]["state"] for r in rep["rows"]} == {"not run"}
        r = c.post("/api/index/rerun", json={"clips": ["CLIP_A.MP4"], "stages": "look"}).json()
        s = _wait_index(c, r["job"])
        assert s["state"] == "done", s
        assert [x["only"] for x in looks] == [["CLIP_A"]]
        assert not (server.STATE["visual"] / "CLIP_B.visual.json").exists()
        rep = c.get("/api/index/clips").json()
        assert _row(rep, "CLIP_A.MP4")["stages"]["look"]["state"] == "done"
        assert _row(rep, "CLIP_B.MP4")["stages"]["look"]["state"] == "not run"
        # a close look on a clip never looked at brings its look, priced with it
        p = c.post("/api/index/price", json={"clips": ["CLIP_B.MP4"], "stages": ["close"]}).json()
        assert p["plan"]["CLIP_B.MP4"][:2] == ["look", "close"] and p["looks"] == 1
        assert p["usd"] == pytest.approx(server.VISUAL_USD_PER_SHEET
                                         + server.FINE_WINDOWS_PER_CLIP * server.FINE_USD_PER_WINDOW,
                                         abs=0.01)
        assert p["notes"] and "look comes first" in p["notes"][0]


def test_a_rerun_is_refused_what_it_cannot_do(indexed):
    c = indexed["c"]
    assert c.post("/api/index/rerun", json={"clips": ["NOPE.MP4"], "stages": ["asr"]}).status_code == 404
    assert c.post("/api/index/rerun", json={"stages": ["dance"]}).status_code == 400
    r = c.post("/api/index/price", json={"stages": ["look"], "interval_s": 5})
    assert r.status_code == 400 and "interval_s" in r.text
    r = c.post("/api/index/price", json={"stages": ["asr"], "interval_s": 2})
    assert r.status_code == 400 and "look" in r.text


def test_the_whole_bin_retuned_sets_the_projects_interval(indexed):
    c, server, looks = indexed["c"], indexed["server"], indexed["looks"]
    r = c.post("/api/index/rerun", json={"clips": ["CLIP_C.MP4"], "stages": ["look"],
                                         "interval_s": 1}).json()
    assert _wait_index(c, r["job"])["state"] == "done"
    assert server.read_edl()["look"]["clips"] == {"CLIP_C.MP4": 1.0}
    looks.clear()
    p = c.post("/api/index/price", json={"clips": None, "stages": ["look"], "interval_s": 3}).json()
    assert p["whole_bin"] is True and p["looks"] == 3
    r = c.post("/api/index/rerun", json={"clips": None, "stages": ["look"], "interval_s": 3}).json()
    s = _wait_index(c, r["job"])
    assert s["state"] == "done", s
    look = server.read_edl()["look"]
    assert look["interval_s"] == 3.0 and "clips" not in look, "the bin's one interval"
    assert sorted(x["only"][0] for x in looks) == ["CLIP_A", "CLIP_B", "CLIP_C"]
    assert {x["interval"] for x in looks} == {3.0}
    assert c.get("/api/index/clips").json()["summary"]["looked_at"] == {"3": 3}


def test_a_stopped_rerun_keeps_the_old_look_and_says_so(indexed, monkeypatch):
    c, server = indexed["c"], indexed["server"]
    slow: list = []
    stub_looker(server, monkeypatch, Path(server.STATE["work"]), slow, sleep=30)
    old = json.loads((server.STATE["visual"] / "CLIP_A.visual.json").read_text())
    r = c.post("/api/index/rerun", json={"clips": ["CLIP_A.MP4"], "stages": ["look"],
                                         "interval_s": 1}).json()
    for _ in range(200):
        rep = c.get("/api/index/clips").json()
        if _row(rep, "CLIP_A.MP4")["stages"]["look"]["state"] == "running":
            break
        time.sleep(0.05)
    assert _row(rep, "CLIP_A.MP4")["stages"]["look"]["state"] == "running"
    assert rep["running"] and rep["job"] == r["job"] and rep["scoped"] is True
    t0 = time.time()
    stop = c.post("/api/index/cancel").json()
    assert r["job"] in stop["stopped"] and time.time() - t0 < 6
    assert c.get(f"/api/job/{r['job']}").json()["state"] == "cancelled"
    assert json.loads((server.STATE["visual"] / "CLIP_A.visual.json").read_text()) == old
    j = journal.Journal.load(server.journal_path())
    st = j.clips["CLIP_A.MP4"]["stages"]["look"]
    assert st["state"] == "queued" and st["force"] is True and st["attempts"] == 0
    a = _row(c.get("/api/index/clips").json(), "CLIP_A.MP4")["stages"]["look"]
    assert a["state"] == "done" and a["again"] == "waiting" and a["interval_s"] == 4.0


def test_a_rerun_asked_while_an_index_runs_joins_it(indexed, monkeypatch):
    """One index at a time: a re-run asked while one runs is taken in by that run, on
    its own journal, and runs."""
    c, server = indexed["c"], indexed["server"]
    looks: list = []
    stub_looker(server, monkeypatch, Path(server.STATE["work"]), looks, sleep=1.0)
    r1 = c.post("/api/index/rerun", json={"clips": ["CLIP_A.MP4"], "stages": ["look"]}).json()
    r2 = c.post("/api/index/rerun", json={"clips": ["CLIP_B.MP4"], "stages": ["look"]}).json()
    assert r2["joined"] is True and r2["job"] == r1["job"]
    s = _wait_index(c, r1["job"])
    assert s["state"] == "done", s
    assert sorted(x["only"][0] for x in looks) == ["CLIP_A", "CLIP_B"]
    j = journal.Journal.load(server.journal_path())
    assert all(not j.clips[k]["stages"]["look"]["force"] for k in j.clips)



def test_what_isnt_done_runs_only_that_and_replaces_nothing(indexed):
    """The open screen's "Index what isn't done", in the panel: of the stages asked, only
    those not done on each clip (a look whose file is gone), and nothing forced."""
    c, server, looks = indexed["c"], indexed["server"], indexed["looks"]
    body = {"clips": None, "stages": "everything", "only_missing": True}
    p = c.post("/api/index/price", json=body).json()
    assert p["plan"] == {} and p["usd"] == 0, "a bin indexed whole has nothing left"
    assert c.post("/api/index/rerun", json=body).status_code == 400
    (server.STATE["visual"] / "CLIP_B.visual.json").unlink()
    p = c.post("/api/index/price", json={**body, "interval_s": 2}).json()
    assert p["plan"] == {"CLIP_B.MP4": ["look", "picks"]} and p["looks"] == 1
    assert p["replaces"]["look"] == 0 and p["words"] == "CLIP_B: what isn't done (looks at 2 s)"
    before = journal.Journal.load(server.journal_path())
    r = c.post("/api/index/rerun", json={**body, "interval_s": 2}).json()
    s = _wait_index(c, r["job"])
    assert s["state"] == "done", s
    assert looks == [{"only": ["CLIP_B"], "force": False, "interval": 2.0}]
    j = journal.Journal.load(server.journal_path())
    assert j.state("CLIP_B.MP4", "look") == "done"
    assert j.clips["CLIP_A.MP4"]["stages"]["look"] == before.clips["CLIP_A.MP4"]["stages"]["look"]
    # the whole bin asked at 2 s: the bin's interval (the old slider's one setting)
    assert server.read_edl()["look"]["interval_s"] == 2.0

def test_footage_imported_during_a_rerun_is_heard_and_its_look_waits(indexed, project, monkeypatch):
    """A re-run's own index takes footage imported meanwhile in as a free run would: heard
    and previewed in that run, its priced look held for the editor."""
    import shutil
    c, server = indexed["c"], indexed["server"]
    slow: list = []
    stub_looker(server, monkeypatch, Path(server.STATE["work"]), slow, sleep=3)
    r = c.post("/api/index/rerun", json={"clips": ["CLIP_A.MP4"], "stages": ["look"]}).json()
    for _ in range(200):
        if slow:
            break
        time.sleep(0.05)
    new = project["footage"] / "CLIP_D.MP4"
    shutil.copy(project["footage"] / "CLIP_A.MP4", new)
    server.STATE["index_on_import"] = True
    try:
        assert server.media_added(["CLIP_D.MP4"])["index"] == r["job"], "joined the re-run"
        s = _wait_index(c, r["job"], timeout=120.0)
        assert s["state"] == "done", s
        j = journal.Journal.load(server.journal_path())
        assert j.state("CLIP_D.MP4", "asr") == "done" and j.state("CLIP_D.MP4", "proxy") == "done"
        assert j.state("CLIP_D.MP4", "look") == "queued" and j.held("CLIP_D.MP4")
        assert [x["only"] for x in slow] == [["CLIP_A"]], "no look bought for the import"
        d = _row(c.get("/api/index/clips").json(), "CLIP_D.MP4")["stages"]
        assert d["asr"]["state"] == "done" and d["look"]["state"] == "held"
    finally:
        server.STATE.pop("index_on_import", None)
        new.unlink(missing_ok=True)
        for f in (server.STATE["sidecars"] / "CLIP_D.audio.json",
                  server.STATE["proxy_dir"] / "CLIP_D.mp4"):
            f.unlink(missing_ok=True)

# ------------------------------------------------------------------ the journal's half

def _done_journal(tmp_path) -> journal.Journal:
    j = journal.Journal(tmp_path / "j.json", bin="b")
    j.add_clips(["A.MP4", "B.MP4"], now=1.0)
    for c in ("A.MP4", "B.MP4"):
        for s in journal.STAGES:
            j.start(c, s, now=2.0)
            j.finish(c, s, 0.1 if s in journal.PRICED else 0.0, now=3.0)
    return j


def test_a_stage_asked_again_is_not_taken_as_done_from_its_file(tmp_path):
    j = _done_journal(tmp_path)
    j.reopen("A.MP4", "look", force=True, now=4.0)
    st = j.clips["A.MP4"]["stages"]["look"]
    assert (st["state"], st["force"], st["kept"]) == ("queued", True, True)
    # its file is on disk: reconcile leaves it queued (it is to be replaced, not trusted)
    j.reconcile({"A.MP4": {"look": True, "asr": True}, "B.MP4": {"look": True}}, now=5.0)
    assert j.state("A.MP4", "look") == "queued"
    # ...and the clip stays released: its old look stands until the new one lands
    assert j.released("A.MP4") and "A.MP4" in j.released_clips()
    # a failed re-run keeps the old one standing too
    j.start("A.MP4", "look", now=6.0)
    j.fail("A.MP4", "look", "rate limited", now=7.0)
    assert j.state("A.MP4", "look") == "failed" and j.released("A.MP4")
    j.start("A.MP4", "look", now=8.0)
    j.finish("A.MP4", "look", 0.2, now=9.0)
    st = j.clips["A.MP4"]["stages"]["look"]
    assert (st["state"], st["force"], st["kept"]) == ("done", False, False)
    assert st["cost_usd"] == pytest.approx(0.3), "every look paid for stays recorded"
    # reloaded from disk, the new fields are there for an old journal too
    assert journal.Journal.load(tmp_path / "j.json").clips["B.MP4"]["stages"]["look"]["force"] is False


def test_the_walk_takes_only_what_it_may_and_what_was_asked_passes_the_hold(tmp_path):
    j = _done_journal(tmp_path)
    for c in ("A.MP4", "B.MP4"):
        j.reopen(c, "look", force=True)
    j.pause_priced("budget cap $1.00 reached")
    assert j.next() is None, "the pause holds every priced stage"
    assert j.next(asked={("B.MP4", "look")}) == ("B.MP4", "look")
    only = lambda c, s: c == "A.MP4"
    assert j.next(only=only) is None
    assert j.next(only=only, asked={("A.MP4", "look"), ("B.MP4", "look")}) == ("A.MP4", "look")
    j.resume_priced()
    j.hold_priced(["A.MP4"], "imported")
    assert j.next() == ("B.MP4", "look")
    assert j.next(only=only) is None and j.next(only=only, asked={("A.MP4", "look")}) == ("A.MP4", "look")
