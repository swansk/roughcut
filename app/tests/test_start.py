"""Starting a film like a person (wave 24 lane START): the server half.

The Copper first-day editor could start a film from a new footage folder only by typing its
path into the bottom of the title-bar menu (COLD-01, -04), was never asked how long the film
should be (COLD-09, CLUMSY-02: 60-120 s asked, 2:56 and 2:41 delivered) and read "free on
your Claude plan — about $36.04" in one tooltip (COLD-07). Here:

  * GET /api/folders lists folders (never files) under the places the board already reads,
    each with its video count, and never follows a path out of them;
  * POST /api/projects/open says what is wrong with a file / a missing folder / a folder with
    no video;
  * POST /api/target and POST /api/firstpass {length_s} set the target the cut-writer is
    TOLD: the prompt the (fake) model receives carries the chosen range;
  * the first pass says what it does in plain words, how long it will take (learned from
    this machine's earlier passes), and its price once, as "free on your plan".

    uv run --with pytest --with fastapi --with uvicorn --with httpx --with numpy \
        --with playwright pytest app/tests/test_start.py -q
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import startwords  # noqa: E402

from conftest import _make_clip  # noqa: E402
from test_firstpass import fp, model, _wait  # noqa: E402,F401  (the suite's fake backend)
from test_server import _fresh  # noqa: E402


# ------------------------------------------------------------------ the pure half


def test_a_length_becomes_the_range_the_writer_aims_inside():
    assert startwords.target_for(60) == [54, 66]
    assert startwords.target_for(30) == [27, 33]
    assert startwords.target_for(120) == [108, 132]
    assert startwords.target_for(90.0) == [81, 99]
    for bad in (5, 0, -3, 99999, float("nan")):
        with pytest.raises(ValueError):
            startwords.target_for(bad)
    assert startwords.clean_range([60, 120]) == [60, 120]
    for bad in ([120, 60], [1, 5], "x", None, [60]):
        with pytest.raises(ValueError):
            startwords.clean_range(bad)


def test_a_target_is_said_the_way_a_person_says_it():
    assert startwords.target_words([54, 66], 60) == "about 1 min"
    assert startwords.target_words([27, 33], 30) == "about 30 s"
    assert startwords.target_words([81, 99], 90) == "about 1:30"
    assert startwords.target_words([120, 180], None) == "2–3 min"
    assert startwords.target_words([75, 105], None) == "1:15–1:45"


def test_the_time_estimate_learns_from_this_machines_runs():
    first = startwords.estimate([], 26)
    assert first["learned"] is False and "first guess" in first["basis"]
    assert first["seconds"] == round(26 * startwords.FIRST_GUESS_PER_CLIP_S
                                     + startwords.FIRST_GUESS_FIXED_S)
    assert first["words"] == "about 6 minutes"            # the Copper trial took about 5.5
    # 26 short clips took 5 minutes here: 3 min 30 to hear and look, 1 min 30 for the rest
    row = startwords.history_row(clips=26, indexed=26, index_s=210, total_s=300)
    est = startwords.estimate([row], 26)
    assert est["learned"] is True and est["runs"] == 1
    assert est["seconds"] == 300 and est["words"] == "about 5 minutes"
    assert "1 earlier first pass on this machine" in est["basis"]
    # half the clips are already indexed: only the half costs
    assert startwords.estimate([row], 13)["seconds"] == round(13 * 210 / 26 + 90)
    assert startwords.estimate([row] * 3, 0)["seconds"] == 90
    assert startwords.minutes_words(20) == "under a minute"
    assert startwords.minutes_words(70) == "about a minute"


def test_the_plain_words_say_what_it_does_how_closely_and_that_nothing_changes():
    p = startwords.plain(clips=26, minutes=31.2, to_index=26, look_s=4.0, target=[54, 66],
                         want_s=60)
    text = " ".join(p["lines"]) + " " + p["safe"]
    assert "Listens to every clip" in text and "26 clips, 31 min of footage" in text
    assert "a frame every 4 seconds" in text and "closer look" in text      # K5: how closely
    assert "about 1 min long" in text and "Nothing in your timeline changes until you accept" in text
    for jargon in ("Qwen", "PE-Core", "3080", "Whisper", "Sonnet", "fps", "levelled", "contact sheet"):
        assert jargon not in text, jargon
    done = startwords.plain(clips=26, minutes=None, to_index=0, look_s=4.0, target=[120, 180],
                            want_s=None)
    assert "already heard and looked at" in " ".join(done["lines"])
    assert "2–3 min long" in " ".join(done["lines"])


# ------------------------------------------------------------------ the folder browser


@pytest.fixture
def films(tmp_path, project):
    """A board on `films/alpha`, with sibling folders: one with video, one empty, one hidden,
    a file, and a link out of every place the browser reads."""
    import server

    root = tmp_path / "films"
    alpha = root / "alpha"
    alpha.mkdir(parents=True)
    _make_clip(alpha / "A1.MP4")
    (root / "beta").mkdir()
    for i in range(3):
        (root / "beta" / f"b{i}.mp4").write_bytes(b"")
    (root / "beta" / "notes.txt").write_text("secret contents", encoding="utf-8")
    (root / "gamma").mkdir()
    (root / "gamma" / "readme.md").write_text("no video here", encoding="utf-8")
    (root / ".hidden").mkdir()
    (root / "loose.mp4").write_bytes(b"")
    (root / "out").symlink_to("/usr", target_is_directory=True)
    server.configure(None, alpha, None, tmp_path / "work", proxies=False)
    from fastapi.testclient import TestClient
    return TestClient(server.app), root


def test_the_browser_lists_folders_with_their_video_counts_and_nothing_else(films):
    c, root = films
    d = c.get("/api/folders").json()
    assert d["path"] == str(root.resolve())
    names = {f["name"]: f["videos"] for f in d["folders"]}
    assert names == {"alpha": 1, "beta": 3, "gamma": 0}
    # folders with video first, then by name
    assert [f["name"] for f in d["folders"]] == ["alpha", "beta", "gamma"]
    body = json.dumps(d)
    for never in ("loose.mp4", "notes.txt", "readme.md", "secret contents", ".hidden", "b0.mp4"):
        assert never not in body, never                     # no file is ever named
    assert "out" not in names                               # a link out of the places is not followed
    assert d["videos"] == 1                                 # loose.mp4, counted and never named
    assert d["current"].endswith("alpha")
    beta = c.get("/api/folders", params={"path": str(root / "beta")}).json()
    assert beta["videos"] == 3 and beta["folders"] == []
    assert beta["parent"] == str(root.resolve())


def test_the_browser_says_a_file_is_a_file_and_never_leaves_its_places(films):
    c, root = films
    r = c.get("/api/folders", params={"path": str(root / "loose.mp4")})
    assert r.status_code == 400 and "that is a file, not a folder" in r.json()["detail"]
    r = c.get("/api/folders", params={"path": str(root / "nope")})
    assert r.status_code == 404 and "no such folder" in r.json()["detail"]
    for out in ("/etc", "/usr", str(root / "out"), str(root / ".." / ".." / ".." / ".." / "etc"),
                "/proc", "/"):
        r = c.get("/api/folders", params={"path": out})
        assert r.status_code in (403, 404, 400), (out, r.status_code)
        assert r.status_code != 200, out


def test_the_browser_counts_one_level_and_caches_by_the_folders_time(films, monkeypatch):
    import server

    c, root = films
    calls = []
    real = server._count_videos
    monkeypatch.setattr(server, "_count_videos", lambda p: calls.append(str(p)) or real(p))
    server._FOLDER_COUNTS.clear()
    c.get("/api/folders")
    first = len(calls)
    c.get("/api/folders")
    assert len(calls) == first, "a second listing re-counted unchanged folders"
    (root / "beta" / "b9.mov").write_bytes(b"")
    d = c.get("/api/folders").json()
    assert {f["name"]: f["videos"] for f in d["folders"]}["beta"] == 4


def test_opening_a_folder_says_what_is_wrong_in_words(films):
    c, root = films
    r = c.post("/api/projects/open", json={"footage": str(root / "loose.mp4")})
    assert r.status_code == 400
    assert "that is a file, not a folder" in r.json()["detail"] and "holds your clips" in r.json()["detail"]
    r = c.post("/api/projects/open", json={"footage": str(root / "nope")})
    assert r.status_code == 400 and r.json()["detail"].startswith("no such folder:")
    r = c.post("/api/projects/open", json={"footage": str(root / "gamma")})
    assert r.status_code == 400 and "no video files in" in r.json()["detail"]
    r = c.post("/api/projects/open", json={"footage": f"  {root / 'beta'}  "})
    assert r.status_code == 200 and r.json()["name"] == "beta" and r.json()["edl_created"] is True


# ------------------------------------------------------------------ the length


def test_the_target_is_set_and_persists_in_the_cut_file(tmp_path, project):
    import server

    with _fresh(tmp_path, project) as c:
        assert c.get("/api/project").json()["target"] == [120, 180]       # the scaffold's default
        r = c.post("/api/target", json={"seconds": 60})
        assert r.status_code == 200
        assert r.json() == {"target": [54, 66], "target_want_s": 60.0, "aims": "about 1 min"}
        p = c.get("/api/project").json()
        assert p["target"] == [54, 66] and p["target_want_s"] == 60.0
        on_disk = json.loads(server.STATE["edl"].read_text(encoding="utf-8"))
        assert on_disk["target_s"] == [54, 66]
        # an autosave from the board does not undo it
        assert c.put("/api/project", json={"story": "friends at Copper"}).status_code == 200
        assert c.get("/api/project").json()["target"] == [54, 66]
        # a range, typed
        r = c.post("/api/target", json={"target": [75, 105]})
        assert r.json()["target"] == [75, 105] and r.json()["target_want_s"] is None
        assert r.json()["aims"] == "1:15–1:45"
        for bad in ({}, {"seconds": 3}, {"seconds": "abc"}, {"seconds": 99999},
                    {"target": [10, 5]}, {"target": "x"}, []):
            assert c.post("/api/target", json=bad).status_code == 400, bad
        assert c.get("/api/project").json()["target"] == [75, 105]


# ------------------------------------------------------------------ the first pass is told the length


def _cut_prompts(model) -> list[str]:
    return [r.prompt for r in model.seen if "segments" in (r.schema or {})]


def test_the_first_pass_is_written_to_the_length_the_editor_chose(fp, model):
    """Asked for 1 min, the cut-writer's prompt says 54-66 seconds (not the scaffold's 120-180),
    and the brief rides along as the editor's note. Both sit in the cut file afterwards."""
    import server

    before = fp.get("/api/firstpass").json()["would"]
    assert before["target"] == [120, 180] and "2–3 min" in " ".join(before["plain"]["lines"])
    r = fp.post("/api/firstpass", json={"mode": "run", "length_s": 60,
                                         "note": "a ski trip with friends at Copper, for the group chat"})
    assert r.status_code == 200, r.text
    assert r.json()["target"] == [54, 66] and r.json()["target_want_s"] == 60.0
    s = _wait(fp)
    assert s["state"] == "done", s
    prompts = _cut_prompts(model)
    assert prompts, "the cut-writer was never called"
    assert all("54-66 seconds" in p for p in prompts), [p[:400] for p in prompts]
    assert not any("120-180 seconds" in p for p in prompts)
    assert any("a ski trip with friends at Copper" in p for p in prompts)
    edl = json.loads(server.STATE["edl"].read_text(encoding="utf-8"))
    assert edl["target_s"] == [54, 66]
    assert edl["story"] == "a ski trip with friends at Copper, for the group chat"   # the film's own brief
    assert fp.get("/api/firstpass").json()["would"]["target_want_s"] == 60.0


def test_a_first_pass_without_a_length_keeps_the_projects_target(fp, model):
    assert fp.post("/api/target", json={"seconds": 180}).status_code == 200
    assert fp.post("/api/firstpass", json={"mode": "run"}).status_code == 200
    assert _wait(fp)["state"] == "done"
    assert all("162-198 seconds" in p for p in _cut_prompts(model))


def test_a_bad_length_on_the_first_pass_is_refused_before_anything_runs(fp):
    for bad in (3, "soon", 99999, {"a": 1}):
        r = fp.post("/api/firstpass", json={"mode": "run", "length_s": bad})
        assert r.status_code == 400, bad
    assert fp.get("/api/firstpass").json()["state"] == "never-run"


# ------------------------------------------------------------------ plain words, time, price


def test_the_first_pass_says_what_it_does_and_how_long_and_learns(fp, model, monkeypatch):
    import server

    monkeypatch.setenv("ROUGHCUT_BACKEND", "claude_cli")
    w = fp.get("/api/firstpass").json()["would"]
    text = " ".join(w["plain"]["lines"]) + " " + w["plain"]["safe"]
    assert "Listens to every clip" in text and "a frame every 4 seconds" in text
    assert "Nothing in your timeline changes until you accept" in text
    for jargon in ("Qwen", "PE-Core", "3080", "Whisper", "Sonnet"):
        assert jargon not in text
    assert w["time"]["learned"] is False and "first guess" in w["time"]["basis"]
    assert w["time"]["words"].startswith(("about", "under"))
    # one price wording: the words and the hover say it, the figure is the API's, kept apart
    assert w["cost"]["words"] == "free on your plan" and w["cost"]["hover"] == "free on your plan"
    assert "$" not in w["cost"]["hover"] and "$" in w["cost"]["api_note"]
    # the model names are still on the steps (the board's Details), nothing was removed
    assert [st["key"] for st in w["steps"]] == ["index", "picks", "cut"]

    assert fp.post("/api/firstpass", json={"mode": "run", "length_s": 60}).status_code == 200
    assert _wait(fp)["state"] == "done"
    rows = startwords.load_history(server.fp_history_path())
    assert len(rows) == 1 and rows[0]["clips"] == 3 and rows[0]["total_s"] > 0
    assert rows[0]["indexed"] == 3 and rows[0]["bin"] == server.STATE["footage"].name
    w2 = fp.get("/api/firstpass").json()["would"]
    assert w2["time"]["learned"] is True and "1 earlier first pass" in w2["time"]["basis"]
    # a re-run on a cut asks no brief and says so: the plan carries `has_cut`
    assert w2["has_cut"] is False                       # nothing accepted yet: still a first cut
