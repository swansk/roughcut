"""Cutting to the music (2026-11-20): `roughcut/beats.py` and the planner's music step.

The tracker is checked on a synthetic click track whose beats are known; the snap on small
hand-made cuts (every rule it must keep: the media's ends, the shortest shot, the editor's
own shots, the line rule, the film's length); `server.plan_music` on the suite's fixture
with a one-track fake library. No model is called.
"""
from __future__ import annotations

import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from roughcut import beats  # noqa: E402

BPM = 120.0
FIRST = 0.25                     # the first click's time


def _click_track(path: Path, bpm: float = BPM, seconds: float = 40.0, first: float = FIRST) -> list[float]:
    """A click every beat (a louder, lower one every fourth), as a 16-bit mono WAV."""
    sr = 22050
    y = np.zeros(int(seconds * sr), dtype=np.float32)
    rng = np.random.default_rng(7)
    times, t, k = [], first, 0
    while t < seconds - 0.2:
        n = int(0.03 * sr)
        env = np.exp(-np.arange(n) / (0.006 * sr))
        tone = np.sin(2 * np.pi * (80.0 if k % 4 == 0 else 1800.0) * np.arange(n) / sr)
        a = int(t * sr)
        y[a:a + n] += ((1.0 if k % 4 == 0 else 0.5) * env * (tone + 0.3 * rng.standard_normal(n))).astype(np.float32)
        times.append(t)
        t += 60.0 / bpm
        k += 1
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(y, -1, 1) * 30000).astype("<i2").tobytes())
    return times


@pytest.fixture(scope="module")
def click(tmp_path_factory) -> dict:
    p = tmp_path_factory.mktemp("beats") / "click.wav"
    return {"path": p, "times": _click_track(p)}


# ------------------------------------------------------------------ the tracker

def test_the_tempo_and_every_beat_of_a_click_track_are_found(click):
    got = beats.analyse(click["path"])
    assert abs(got["bpm"] - BPM) < 1.5
    found = np.array(got["beats"])
    off = [float(np.min(np.abs(found - t))) for t in click["times"][2:-2]]
    assert max(off) < 0.035, max(off)                    # within a frame and a half of each click
    assert len(found) == pytest.approx(len(click["times"]), abs=3)
    # the bar's first beat is the low click: every fourth, starting at the first
    downs = found[got["down"]::got["bar"]]
    assert float(np.min(np.abs(downs - click["times"][4]))) < 0.035


def test_a_hint_settles_a_half_or_double_tempo(click):
    assert abs(beats.analyse(click["path"], hint_bpm=118)["bpm"] - BPM) < 1.5


def test_the_analysis_is_kept_beside_the_track_and_reused(click, monkeypatch):
    first = beats.cached(click["path"])
    side = click["path"].parent / ".beats" / "click.wav.json"
    assert side.is_file() and json.loads(side.read_text())["beats"] == first["beats"]
    monkeypatch.setattr(beats, "analyse", lambda *a, **k: (_ for _ in ()).throw(AssertionError("analysed twice")))
    assert beats.cached(click["path"])["beats"] == first["beats"]


def test_a_file_that_is_not_audio_is_refused_in_words(tmp_path):
    p = tmp_path / "nothing.mp3"
    p.write_bytes(b"not audio")
    with pytest.raises(beats.BeatError):
        beats.analyse(p)


def test_the_grid_loops_with_the_bed():
    found = {"beats": [0.5, 1.0, 1.5], "duration_s": 2.0, "down": 1, "bar": 4}
    g = beats.grid(found, 5.0)
    assert [t for t, _ in g][:7] == [0.5, 1.0, 1.5, 2.5, 3.0, 3.5, 4.5]
    assert [d for _, d in g][:6] == [0, 2, 0, 0, 2, 0]
    four = beats.grid({"beats": [0.5 * k for k in range(1, 9)], "duration_s": 60.0, "down": 0, "bar": 4}, 4.0)
    assert [d for _, d in four][:8] == [2, 0, 1, 0, 2, 0, 1, 0]            # bar, beat, half bar, beat


# ------------------------------------------------------------------ the snap

GRID = [(round(0.5 * k, 3), k % 4 == 0) for k in range(1, 80)]      # 120 bpm from 0.5 s


def _cut(*lens: float, clip_len: float = 30.0) -> tuple[list[dict], dict]:
    segs = [{"id": f"s{i}", "clip": f"C{i}.MP4", "in": 5.0, "out": round(5.0 + n, 4)} for i, n in enumerate(lens)]
    return segs, {s["clip"]: clip_len for s in segs}


def test_cuts_move_onto_beats_by_a_roll_and_the_film_keeps_its_length():
    segs, durs = _cut(3.2, 2.9, 4.1, 3.3)
    was = json.dumps(segs)
    out, moved = beats.snap(segs, GRID, durations=durs, fps=24.0)
    assert json.dumps(segs) == was                                   # the input is not touched
    assert beats.measure(segs, GRID)["on"] == 0
    assert beats.measure(out, GRID) == {"cuts": 3, "on": 3, "share": 1.0, "median_off_s": 0.0}
    assert {m["how"] for m in moved} == {"roll"}
    assert sum(beats._dur(s) for s in out) == pytest.approx(sum(beats._dur(s) for s in segs))
    # a roll moves the incoming shot's in-point by what the outgoing one's out-point moved
    assert out[1]["in"] - segs[1]["in"] == pytest.approx(out[0]["out"] - segs[0]["out"])


def test_a_cut_already_on_a_beat_is_left_alone():
    segs, durs = _cut(3.0, 2.5, 4.0)
    out, moved = beats.snap(segs, GRID, durations=durs, fps=24.0)
    assert moved == [] and out == segs


def test_the_editors_own_shots_never_move():
    segs, durs = _cut(3.2, 2.9, 4.1)
    out, moved = beats.snap(segs, GRID, durations=durs, hand={"s0", "s1"}, fps=24.0)
    assert out[0] == segs[0] and out[1] == segs[1] and moved == []
    # an edit INTO a hand shot may ripple (only the outgoing shot changes), never roll
    out, moved = beats.snap(segs, GRID, durations=durs, hand={"s1"}, fps=24.0)
    assert out[1] == segs[1] and [m["how"] for m in moved] == ["ripple"]


def test_a_move_never_reaches_past_the_media():
    # shot 0 ends at its clip's last frame and shot 1 starts at its clip's first: the edit
    # can only move earlier by a ripple (0.2 s back to 3.0), never by extending either
    segs = [{"id": "a", "clip": "A.MP4", "in": 0.0, "out": 3.2}, {"id": "b", "clip": "B.MP4", "in": 0.0, "out": 3.0}]
    out, moved = beats.snap(segs, GRID, durations={"A.MP4": 3.2, "B.MP4": 9.0}, fps=24.0)
    assert out[0]["out"] == pytest.approx(3.0) and out[1] == segs[1]
    assert moved[0]["how"] == "ripple" and moved[0]["by"] == pytest.approx(-0.2)


def test_a_snap_never_leaves_a_shot_shorter_than_the_floor():
    segs, durs = _cut(3.0, 0.8, 3.0)              # the middle shot is 0.8 s: a roll at either
    out, _ = beats.snap(segs, [(3.0, True), (3.45, False), (7.0, True)], durations=durs, fps=24.0)
    assert all(beats._dur(s) >= beats.MIN_SHOT_S - 1e-6 for s in out)


def test_a_move_that_would_clip_a_line_is_not_made():
    segs, durs = _cut(3.2, 2.9, 4.1)
    start = {s["id"]: (s["in"], s["out"]) for s in segs}

    def problems(cut):          # shot 0's out-point and shot 1's in-point are on lines
        bad = set()
        for s in cut:
            if s["id"] == "s0" and s["out"] != start["s0"][1]:
                bad.add(("s0", "mid_out"))
            if s["id"] == "s1" and s["in"] != start["s1"][0]:
                bad.add(("s1", "mid_in"))
        return bad
    out, moved = beats.snap(segs, GRID, durations=durs, problems=problems, fps=24.0)
    assert out[0] == segs[0] and out[1]["in"] == segs[1]["in"]
    assert [m["cut"] for m in moved] == [1]                           # only the second edit moved
    # a line check that fails outright leaves the cut alone
    def broken(cut):
        raise KeyError("no transcript")
    out, moved = beats.snap(segs, GRID, durations=durs, fps=24.0, problems=broken)
    assert moved == [] and out == segs


def test_ripples_keep_the_films_length_within_the_slack():
    # every incoming shot is the editor's: each edit can only ripple, +0.2 s each
    segs, durs = _cut(3.3, 3.5, 3.5, 3.5, 3.5, 3.5, 3.5, 3.5, 3.5, 3.5)
    hand = {s["id"] for s in segs[1:]}
    out, moved = beats.snap(segs, GRID, durations=durs, hand=hand, fps=24.0, length_slack_s=0.5)
    assert abs(sum(beats._dur(s) for s in out) - sum(beats._dur(s) for s in segs)) <= 0.5 + 1e-6


def test_a_slowed_shot_moves_by_its_own_clock():
    segs = [{"id": "a", "clip": "A.MP4", "in": 0.0, "out": 1.6, "speed": 0.5},     # 3.2 s of film
            {"id": "b", "clip": "B.MP4", "in": 2.0, "out": 6.0}]
    out, moved = beats.snap(segs, GRID, durations={"A.MP4": 9.0, "B.MP4": 9.0}, fps=24.0)
    assert beats.cut_times(out)[0] == pytest.approx(3.0, abs=1e-3)
    assert out[0]["out"] == pytest.approx(1.5) and out[1]["in"] == pytest.approx(1.8)


def test_the_note_line_says_what_happened():
    assert "3 of 4 cuts now land on a beat (was 1)" in beats.words({"on": 1, "cuts": 4}, {"on": 3, "cuts": 4}, 120)
    assert beats.words({"on": 0, "cuts": 0}, {"on": 0, "cuts": 0}) == ""


# ------------------------------------------------------------------ the planner's step

@pytest.fixture
def library(tmp_path, monkeypatch, click):
    from roughcut import musiclib
    lib = tmp_path / "musiclib"
    (lib / "files").mkdir(parents=True)
    (lib / "catalog.json").write_text(json.dumps({"genres": {}, "pieces": [
        {"title": "Click Parade", "filename": "Click Parade.mp3", "length": "00:00:40", "feel": "Bouncy, Bright",
         "bpm": "120", "genre": "", "description": "a test click", "instruments": "click"}]}), encoding="utf-8")
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(click["path"]), "-c:a", "libmp3lame",
                    "-b:a", "128k", str(lib / "files" / "Click Parade.mp3")], check=True, capture_output=True)
    monkeypatch.setenv("ROUGHCUT_MUSIC_LIBRARY", str(lib))
    musiclib._CACHE.clear()
    yield lib
    musiclib._CACHE.clear()


PLAN = {"segments": [{"id": "p0", "clip": "CLIP_A.MP4", "in": 2.5, "out": 5.0, "why": "a"},
                     {"id": "p1", "clip": "CLIP_B.MP4", "in": 2.5, "out": 4.9, "why": "b"},
                     {"id": "p2", "clip": "CLIP_C.MP4", "in": 2.6, "out": 5.0, "why": "c"}],
        "notes": "A first cut."}


def _clips(server) -> dict:
    clips, _ = server._ask_clips(None, drop_junk=True)
    return clips


def test_a_first_cut_gets_a_bed_from_the_library_and_its_cuts_on_the_beat(client, library):
    import server
    clips = _clips(server)
    for c in clips.values():
        c["transcript"] = []                      # nobody talks: every cut is free to move
    plan = server.plan_music(json.loads(json.dumps(PLAN)), [], clips, "a bright trip film", "", set(), 24.0, first=True)
    assert plan["music"]["asset"] == "library/click-parade" and plan["music"]["duck"] is True
    assert "Click Parade by Kevin MacLeod" in plan["music_lines"][0]
    assert plan["beats"]["on"] == plan["beats"]["cuts"] == 2 and plan["beats"]["moved"] >= 1
    assert "Cut to the music" in plan["notes"]
    found = beats.cached(library / "files" / "Click Parade.mp3")
    assert beats.measure(plan["segments"], beats.grid(found, 8.0))["on"] == 2


def test_a_revision_keeps_its_cuts_and_gets_no_bed_unless_it_asks(client, library):
    import server
    clips = _clips(server)
    cur = json.loads(json.dumps(PLAN["segments"]))
    plan = server.plan_music(json.loads(json.dumps(PLAN)), cur, clips, "", "tighten the start", set(), 24.0, first=False)
    assert "music" not in plan and "beats" not in plan and plan["segments"] == PLAN["segments"]


def test_no_music_when_the_words_say_so_or_the_switch_is_off(client, library, monkeypatch):
    import server
    clips = _clips(server)
    plan = server.plan_music(json.loads(json.dumps(PLAN)), [], clips, "", "a quiet film, no music", set(), 24.0, first=True)
    assert "music" not in plan and plan["segments"] == PLAN["segments"]
    plan = server.plan_music(json.loads(json.dumps(PLAN)), [], clips, "", "", set(), 24.0, first=True, pick=False)
    assert "music" not in plan
    monkeypatch.setenv("ROUGHCUT_AUTO_MUSIC", "0")
    plan = server.plan_music(json.loads(json.dumps(PLAN)), [], clips, "", "", set(), 24.0, first=True)
    assert "music" not in plan


def test_the_line_rule_outranks_the_beat(client, library):
    """The fixture's clips have a line at 0.5-2.0 s ("hello there") — a cut that sits in
    silence after it is not moved back into it."""
    import server
    from roughcut import sentences
    clips = _clips(server)
    plan0 = {"segments": [{"id": "p0", "clip": "CLIP_A.MP4", "in": 0.3, "out": 2.45, "why": "a"},
                          {"id": "p1", "clip": "CLIP_B.MP4", "in": 0.3, "out": 2.6, "why": "b"},
                          {"id": "p2", "clip": "CLIP_C.MP4", "in": 2.6, "out": 5.0, "why": "c"}], "notes": ""}
    before = {(p["shot"], p["kind"]) for p in sentences.measure(plan0["segments"], clips)["problems"]}
    plan = server.plan_music(json.loads(json.dumps(plan0)), [], clips, "", "", set(), 24.0, first=True)
    after = {(p["shot"], p["kind"]) for p in sentences.measure(plan["segments"], clips)["problems"]}
    assert after <= before


# ------------------------------------------------------------------ the ask box's op

def _state(**over) -> dict:
    st = {
        "segments": [{"id": "s1", "clip": "CLIP_A.MP4", "in": 1.0, "out": 4.2},
                     {"id": "s2", "clip": "CLIP_B.MP4", "in": 1.0, "out": 3.9},
                     {"id": "s3", "clip": "CLIP_C.MP4", "in": 1.0, "out": 5.1}],
        "clips": {c: {"duration": 20.0} for c in ("CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4")},
        "fps": 24.0, "markers": [], "in": None, "out": None, "tracks": {}, "overlays": [],
        "transitions": [], "colour": {}, "looks": [], "presets": {},
        "music": {"asset": "music/bed.wav"},
        "music_tracks": [{"asset": "music/bed.wav", "name": "bed.wav"}, {"asset": "music/other.wav", "name": "other.wav"}],
        "beats": lambda asset, film: {"grid": GRID, "bpm": 120.0} if asset == "music/bed.wav" else None,
    }
    st.update(over)
    return st


QUIET = {c: {"transcript": [], "duration": 20.0} for c in ("CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4")}


def test_to_the_beat_arrives_as_rolls_the_editor_reviews():
    from roughcut import actions, assist
    st = _state()
    ops, line = assist.expand_beats([{"op": "to_the_beat"}], st, QUIET)
    assert [o["op"] for o in ops] == ["roll", "roll"] and "2 of 2 cuts land on a beat (was 0)" in line
    checked = actions.check(ops, st)
    assert checked["refused"] == [] and len(checked["ops"]) == 2
    assert beats.measure(checked["preview"]["segments"], GRID)["on"] == 2
    assert checked["lines"][0].startswith("Roll the edit between")


def test_to_the_beat_without_a_bed_or_a_beat_says_why():
    from roughcut import actions, assist
    for st, word in ((_state(music=None), "no music bed"), (_state(music={"asset": "music/other.wav"}), "no steady beat")):
        ops, line = assist.expand_beats([{"op": "to_the_beat"}], st, QUIET)
        checked = actions.check(ops, st)
        assert line is None and checked["ops"] == [] and word in checked["refused"][0]["why"]


def test_to_the_beat_uses_the_bed_a_music_op_before_it_sets():
    from roughcut import actions, assist
    st = _state(music={"asset": "music/other.wav"})
    ops, line = assist.expand_beats([{"op": "music", "track": "bed.wav"}, {"op": "to_the_beat"}], st, QUIET)
    assert [o["op"] for o in ops] == ["music", "roll", "roll"] and line
    assert actions.check(ops, st)["refused"] == []


def test_to_the_beat_on_a_cut_already_there_changes_nothing_and_says_so():
    from roughcut import actions, assist
    st = _state(segments=[{"id": "s1", "clip": "CLIP_A.MP4", "in": 1.0, "out": 4.0},
                          {"id": "s2", "clip": "CLIP_B.MP4", "in": 1.0, "out": 4.0}])
    ops, _ = assist.expand_beats([{"op": "to_the_beat"}], st, QUIET)
    why = actions.check(ops, st)["refused"][0]["why"]
    assert "1 of 1 cuts are already on a beat" in why


def test_the_prompt_tells_the_model_about_to_the_beat():
    from roughcut import actions
    assert '{"op":"to_the_beat"}' in actions.schema_text()
