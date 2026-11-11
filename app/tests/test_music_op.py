"""The prompt box's one music operation (wave 25, lane MUSIC): validated against the cut and
the tracks the project has, described in a line, and what the AI is told about the bed. Pure."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import actions, assist  # noqa: E402

TRACKS = [{"asset": "project/Night Drive.wav", "name": "Night Drive.wav", "duration_s": 190.0, "project": True},
          {"asset": "music/bed.wav", "name": "bed.wav", "duration_s": 2.0}]


def state(**over):
    st = {"segments": [{"id": "s1", "clip": "A.MP4", "in": 0.0, "out": 5.0}], "clips": {"A.MP4": {"duration": 6.0}},
          "fps": 24.0, "music": None, "music_tracks": TRACKS}
    st.update(over)
    return st


def test_a_track_with_a_level_and_a_dip_is_one_clean_op():
    ok, bad = actions.validate([{"op": "music", "track": "Night Drive.wav", "level": -10, "duck": True}], state())
    assert bad == [] and ok == [{"op": "music", "track": "project/Night Drive.wav", "level": -10.0, "duck": True}]
    line = actions.describe(ok[0], state())
    assert line.startswith("Music bed: Night Drive.wav") and "-10 dB against the film" in line and "dips under talking" in line


def test_with_no_track_in_the_project_it_says_so_and_points_at_add_music():
    ok, bad = actions.validate([{"op": "music", "level": -8}], state(music_tracks=[]))
    assert ok == [] and "no music track yet" in bad[0]["why"] and "Add music…" in bad[0]["why"]
    ok, bad = actions.validate([{"op": "music", "track": "x.mp3"}], state(music_tracks=[]))
    assert ok == [] and "no music track 'x.mp3'" in bad[0]["why"]


def test_changing_the_bed_that_is_set_needs_no_track_and_remove_needs_a_bed():
    bed = {"asset": "music/bed.wav", "bed_lufs": -24.0, "duck": True, "duck_db": 12.0}
    ok, bad = actions.validate([{"op": "music", "level": -12, "fade_out": 6}], state(music=bed))
    assert bad == [] and ok[0] == {"op": "music", "level": -12.0, "fade_out": 6.0}
    ok, bad = actions.validate([{"op": "music", "remove": True}], state(music=bed))
    assert ok == [{"op": "music", "remove": True}]
    ok, bad = actions.validate([{"op": "music", "remove": True}], state())
    assert "no music bed to remove" in bad[0]["why"]
    ok, bad = actions.validate([{"op": "music", "level": -8}], state())
    assert "no music bed is set" in bad[0]["why"]


def test_values_are_bounded_in_words():
    for raw, word in (({"level": 40}, "dB against the film"), ({"level": "loud"}, "dB against the film"),
                      ({"duck_db": 99}, "0..30"), ({"fade_in": 60}, "0..20")):
        ok, bad = actions.validate([{"op": "music", "track": "bed.wav", **raw}], state())
        assert ok == [] and word in bad[0]["why"], (raw, bad)
    bed = {"asset": "music/bed.wav", "bed_lufs": -24.0}
    ok, bad = actions.validate([{"op": "music"}], state(music=bed))
    assert ok == [] and "needs a `track`" in bad[0]["why"]
    ok, _ = actions.validate([{"op": "music", "track": "bed.wav", "duck": "off"}], state())
    assert ok[0]["duck"] is False


def test_an_earlier_music_op_is_what_a_later_one_changes():
    ok, bad = actions.validate([{"op": "music", "track": "bed.wav"}, {"op": "music", "level": -14}], state())
    assert bad == [] and len(ok) == 2


def test_the_prompt_says_whether_a_bed_is_set_and_which_tracks_exist():
    none = assist.sequence_block(state())
    assert "Music bed (A2): none set" in none and "Night Drive.wav (190 s) — added to this project" in none
    empty = assist.sequence_block(state(music_tracks=[]))
    assert "Music tracks: none: the project has no music" in empty and "Add music…" in empty
    bed = {"asset": "project/Night Drive.wav", "bed_lufs": -28.0, "duck": True, "duck_db": 12.0, "fade_in": 1.5, "fade_out": 4.0}
    set_ = assist.sequence_block(state(music=bed))
    assert "Music bed (A2): project/Night Drive.wav at -12 dB against the film, dips 12 dB under talking" in set_
    assert '"op":"music"' in actions.schema_text()


def test_the_time_left_does_not_jump_around():
    """FINISH-15's readings (seconds, raw minutes left): the pill read 11, 14, 16, 12, 6, 5, 11
    minutes; eased, it falls with the clock and takes a rise in slowly."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import server
    sm = server.EtaSmoother()
    raw = [(95, 11), (219, 14), (300, 16), (359, 12), (480, 6), (554, 5), (644, 11)]
    shown = {}
    for (t0, m0), (t1, m1) in zip(raw, raw[1:]):         # the pill is polled every 2 s
        for t in range(t0, t1, 2):
            shown[t] = sm.feed((m0 + (m1 - m0) * (t - t0) / (t1 - t0)) * 60.0, float(t))
    shown[644] = sm.feed(11 * 60.0, 644.0)
    ts = sorted(shown)
    for a_, b_ in zip(ts, ts[1:]):
        assert shown[b_] >= shown[a_] - 1.5 * (b_ - a_) - 1e-6    # never much faster down than the clock
    # in any 60 s the figure moves by less than 3 minutes (the raw readings moved by up to 6)
    worst = max(abs(shown[t + 60] - shown[t]) for t in ts if t + 60 in shown)
    assert worst < 180, worst
    assert shown[644] > 5 * 60                                  # and it did not fall to the raw 5
    # with nothing new to say it counts down by the clock, never below zero
    assert sm.feed(None, 644.0 + 30.0) == pytest.approx(shown[644] - 30.0)
