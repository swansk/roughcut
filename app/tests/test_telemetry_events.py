"""Telemetry as timed events (wave 20, lane TELEM) — `roughcut.telemetry_events`.

No ffmpeg, no GPMF file, no model: the rules are pure functions of three series (|a| in g
at 200 Hz, GPS speed and altitude at 18 Hz, GRAV at 30 Hz), so the tests build those
series by hand and put a dip, a spike or a slowing at a known second. Each rule has its
near-miss next to it, because the failure that matters is a rule that fires on a thing
that only looks like the event (a dropped camera, a hand, a turn).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import events, find, revise  # noqa: E402
from roughcut import telemetry_events as T  # noqa: E402

HZ = 200


def accel(seconds: float, dips=(), spikes=()):
    """|a| = 1 g everywhere; `dips` = (start, end, g); `spikes` = (t, g) one sample."""
    ta = [i / HZ for i in range(int(seconds * HZ))]
    mag = [1.0] * len(ta)
    for a, b, g in dips:
        for i in range(int(a * HZ), int(b * HZ)):
            mag[i] = g
    for t, g in spikes:
        mag[int(t * HZ)] = g
    return ta, mag


def gps(seconds: float, speed, alt=lambda t: 800.0):
    tp = [i / 18 for i in range(int(seconds * 18))]
    return tp, [speed(t) for t in tp], [alt(t) for t in tp]


def series(seconds=60, dips=(), spikes=(), speed=None, alt=None):
    ta, mag = accel(seconds, dips, spikes)
    tp, sp, al = gps(seconds, speed, alt or (lambda t: 800.0)) if speed else ([], [], [])
    return {"ta": ta, "mag": mag, "tg": [], "roll": [], "mount": "upright",
            "tp": tp, "sp": sp, "alt": al}


def kinds(ev, kind):
    return [e for e in ev if e["kind"] == kind]


# ----------------------------------------------------------------------------- air

def test_an_air_is_a_freefall_dip_then_a_landing_and_carries_its_numbers():
    ev = T.events_from_series(series(dips=[(10.0, 10.4, 0.1)], spikes=[(10.45, 4.0)]))
    (air,) = kinds(ev, "air")
    assert air["who"] == "wearer" and air["source"] == "telemetry" and air["status"] == "felt"
    assert air["check"] is True
    assert 0.35 <= air["air_s"] <= 0.45 and abs(air["t_takeoff"] - 10.0) < 0.02
    assert air["landing_g"] == 4.0 and air["min_g"] == 0.1
    assert air["start"] == air["t_takeoff"] and air["end"] == air["t_land"]
    assert air["text"].startswith("air 0.40 s at 10.00-10.40 (landing 4.0 g)")


def test_air_near_misses_are_not_airs():
    # too short: a dip of 0.2 s is a bump, not a flight
    assert not kinds(T.events_from_series(series(dips=[(5, 5.2, 0.1)], spikes=[(5.25, 4.0)])), "air")
    # not low enough: 0.55 g never goes under the 0.5 g floor
    assert not kinds(T.events_from_series(series(dips=[(5, 5.5, 0.55)], spikes=[(5.55, 4.0)])), "air")
    # no landing: a dropped camera dips and then nothing hits (2.0 g is under 2.5 g)
    assert not kinds(T.events_from_series(series(dips=[(5, 5.5, 0.1)], spikes=[(5.55, 2.0)])), "air")
    # the landing is too late to belong to the dip
    assert not kinds(T.events_from_series(series(dips=[(5, 5.5, 0.1)], spikes=[(6.5, 4.0)])), "air")


# ----------------------------------------------------------------------------- impact

def test_impacts_over_5_g_and_a_4_9_g_near_miss():
    ev = T.events_from_series(series(spikes=[(10, 7.8), (20, 4.9)]))
    (im,) = kinds(ev, "impact")
    assert im["peak_g"] == 7.8 and abs(im["t"] - 10.05) < 0.1
    assert im["handling"] is None, "no GPS: nobody can say it was hands"


def test_an_impact_with_no_speed_change_is_often_handling_and_one_with_a_stop_is_not():
    cruise = series(spikes=[(30, 8.0)], speed=lambda t: 6.0)
    (im,) = kinds(T.events_from_series(cruise), "impact")
    assert im["handling"] is True and im["check"] is False and "often handling" in im["text"]

    fall = series(spikes=[(30, 8.0)], speed=lambda t: 6.0 if t < 30 else 0.3)
    (im,) = kinds(T.events_from_series(fall), "impact")
    assert im["handling"] is False and im["check"] is True and im["text"].endswith("(check)")


def test_the_landing_of_an_air_is_marked_as_the_airs_not_a_second_event_to_check():
    ev = T.events_from_series(series(dips=[(10, 10.4, 0.1)], spikes=[(10.45, 7.7)],
                                     speed=lambda t: 6.0))
    (im,) = kinds(ev, "impact")
    assert im["after_air"] is True and "air landing" in im["text"]
    assert events.telemetry_moments("A.MP4", ev)[0]["kind"] == "jump"
    assert len(events.telemetry_moments("A.MP4", ev)) == 1


# ----------------------------------------------------------------------------- speed

def test_a_speed_drop_of_3_ms_in_3_s_with_a_jolt_is_a_fall_candidate():
    def speed(t):
        return 4.8 if t < 69 else max(0.1, 4.8 - (t - 69) * 2.0)
    ev = T.events_from_series(series(seconds=100, spikes=[(70.0, 4.8)], speed=speed))
    (d,) = kinds(ev, "speed_drop")
    assert d["from_ms"] == 4.8 and d["to_ms"] <= 0.5 and d["stopped"] is True
    assert d["peak_g"] == 4.8 and d["check"] is True
    assert "4.8 g" in d["text"]
    assert not kinds(ev, "stopped") or all(abs(e["start"] - d["start"]) > 1.5
                                           for e in kinds(ev, "stopped")), "folded into the drop"


def test_a_slowing_under_3_ms_is_a_turn_and_a_drop_without_a_jolt_is_not_checked():
    slow = series(seconds=40, speed=lambda t: 8.0 if t < 20 else max(5.5, 8.0 - (t - 20)))
    assert not kinds(T.events_from_series(slow), "speed_drop"), "2.5 m/s is a near-miss"
    turn = series(seconds=40, speed=lambda t: 8.0 if t < 20 else max(4.0, 8.0 - (t - 20) * 2))
    (d,) = kinds(T.events_from_series(turn), "speed_drop")
    assert d["stopped"] is False and d["peak_g"] < 3.5 and d["check"] is False
    assert T.felt_from_events([d]) == "", "a turn is not said to the planner"


# ----------------------------------------------------------------------------- stops, lifts, riding

def test_standing_still_for_10_s_is_a_stop_and_nine_is_not():
    still = series(seconds=60, speed=lambda t: 0.2 if 20 <= t <= 33 else 6.0)
    (st,) = kinds(T.events_from_series(still), "stopped")
    assert 19 <= st["start"] <= 21 and 32 <= st["end"] <= 34
    brief = series(seconds=60, speed=lambda t: 0.2 if 20 <= t <= 28 else 6.0)
    assert not [e for e in T.events_from_series(brief) if e["kind"] == "stopped" and "from_ms" not in e]


def test_a_steady_climb_is_a_lift_and_the_same_speed_on_the_flat_is_not():
    up = series(seconds=90, speed=lambda t: 2.5, alt=lambda t: 800 + t * 0.7)
    (lift,) = kinds(T.events_from_series(up), "lift")
    assert lift["climb_m"] >= 50 and lift["text"].startswith("lift?")
    flat_ = series(seconds=90, speed=lambda t: 2.5)
    assert not kinds(T.events_from_series(flat_), "lift")


def test_riding_is_a_stretch_at_3_ms_or_more_for_10_s():
    ev = T.events_from_series(series(seconds=60, speed=lambda t: 9.0 if 10 <= t <= 40 else 0.0))
    (ride,) = kinds(ev, "riding")
    assert ride["mean_ms"] == 9.0 and 9 <= ride["start"] <= 11 and "riding 9 m/s" in ride["text"]


# ----------------------------------------------------------------------------- roll, mount

def streams(grav):
    return {"GRAV": {"t": [i / 30 for i in range(len(grav))], "v": grav}}


def test_mount_is_read_from_gravity_and_roll_is_levelled_by_it():
    upright = T.series_from_streams(streams([(0.0, 1.0, 0.0)] * 60 + [(0.5, 0.866, 0.0)] * 60))
    assert upright["mount"] == "upright"
    assert abs(upright["roll"][0]) < 0.1 and abs(upright["roll"][-1] - 30) < 0.5
    inverted = T.series_from_streams(streams([(0.0, -1.0, 0.0)] * 60 + [(-0.5, -0.866, 0.0)] * 60))
    assert inverted["mount"] == "inverted"
    assert abs(inverted["roll"][0]) < 0.1 and abs(inverted["roll"][-1] - 30) < 0.5
    pointing_down = T.series_from_streams(streams([(0.1, 0.1, 0.99)] * 30))
    assert pointing_down["roll"][0] == 0.0, "gravity along the optical axis: roll is ill-defined"


# ----------------------------------------------------------------------------- the sidecar

def test_the_sidecar_round_trips_and_the_planner_line_reads_it(tmp_path):
    s = series(seconds=80, dips=[(46.62, 47.01, 0.05)], spikes=[(47.05, 4.0), (60, 7.8)],
               speed=lambda t: 9.0 if t < 60 else 0.1)
    s["tg"], s["roll"] = [i / 30 for i in range(30 * 80)], [10.0] * (30 * 80)
    side = T.write_sidecar("CLIP_12.MP4", [1, 2], s, tmp_path)
    assert (tmp_path / "CLIP_12.felt.json").exists() and side["schema"] == T.SCHEMA
    evs = T.telemetry_events("clip_12", tmp_path)
    assert {e["kind"] for e in evs} >= {"air", "impact"}
    line = T.felt_line("CLIP_12", tmp_path)
    assert line.startswith("felt: ") and "air 0.39 s at 46.62-47.01 (landing 4.0 g)" in line
    assert "impact 7.8 g at 60.0 (check)" in line
    assert T.speed_series("CLIP_12", tmp_path)[0] == [0.0, 9.0]
    assert T.roll_at("CLIP_12", [0.0, 5.5, 999], tmp_path) == [10.0, 10.0, 10.0]
    assert T.telemetry_events("CLIP_99", tmp_path) == [] and T.felt_line("CLIP_99", tmp_path) == ""


def test_the_cache_is_keyed_on_the_file_signature(tmp_path, monkeypatch):
    video = tmp_path / "CLIP_01.MP4"
    video.write_bytes(b"x" * 10)
    s = series(seconds=20)
    side = T.write_sidecar("CLIP_01.MP4", T.signature(video), s, tmp_path)
    monkeypatch.setattr(T, "_tool", lambda: (_ for _ in ()).throw(AssertionError("re-parsed")))
    assert T.build_sidecar("CLIP_01.MP4", video, tmp_path) == side, "same file: no parse"
    video.write_bytes(b"y" * 11)
    try:
        T.build_sidecar("CLIP_01.MP4", video, tmp_path)
    except AssertionError as exc:
        assert "re-parsed" in str(exc)
    else:
        raise AssertionError("a changed file must be read again")


def test_many_impacts_collapse_and_the_line_stays_short():
    s = series(seconds=120, spikes=[(5 + 4 * i, 6.0 + i) for i in range(20)])
    line = T.felt_from_events(T.events_from_series(s))
    assert line.count("impact ") == T.MAX_IMPACTS and "16 more impacts over 5 g" in line


# ----------------------------------------------------------------------------- the readers

def felt_events():
    return T.events_from_series(series(dips=[(46.62, 47.01, 0.05)], spikes=[(47.05, 4.0)],
                                       speed=lambda t: 6.0))


def test_telemetry_airs_are_moments_with_source_telemetry_status_felt():
    (m,) = events.telemetry_moments("CLIP_12.MP4", felt_events())
    assert (m["source"], m["status"], m["who"], m["kind"]) == ("telemetry", "felt", "wearer", "jump")
    ranked = events.rank_clip("CLIP_12.MP4", {}, {}, None, felt=felt_events())
    assert ranked[0]["source"] == "telemetry" and ranked[0]["why_ranked"]["confirmation"] == "felt"
    # K7 (wave 22): a sensor number is as good as one unaudited look, not better (FELT 1.0)
    assert ranked[0]["score"] >= events.rank_clip(
        "CLIP_12.MP4", {"moments": [{"start": 46.5, "end": 47.5, "kind": "jump", "what": "x",
                                      "notable": True}]}, {})[0]["score"], "felt is not worse than unaudited"


def test_a_sheet_claim_on_the_same_air_is_folded_into_the_telemetry_row():
    coarse = {"moments": [{"start": 45.0, "end": 48.0, "kind": "jump", "notable": True,
                           "what": "skier flies off the lip"}]}
    ranked = events.rank_clip("CLIP_12.MP4", coarse, {}, None, felt=felt_events())
    assert len(ranked) == 1 and ranked[0]["source"] == "telemetry"
    assert "skier flies off the lip" in ranked[0]["what"] and ranked[0]["seen_by_frames"]
    far = {"moments": [{"start": 20.0, "end": 22.0, "kind": "jump", "notable": True, "what": "y"}]}
    assert len(events.rank_clip("CLIP_12.MP4", far, {}, None, felt=felt_events())) == 2


def test_the_planner_block_and_find_read_the_felt_words():
    line = T.felt_from_events(felt_events())
    clip = {"clip": "CLIP_12.MP4", "duration": 100.0, "felt": line, "transcript": []}
    block = revise._clip_block(clip)
    assert "air 0.39 s at 46.62-47.01 (landing 4.0 g) (check)" in block and "felt: " in block
    assert "felt:" not in revise._clip_block({**clip, "felt": ""})

    clips = {"CLIP_12.MP4": {**clip, "felt_rows": [
        {"start": e["start"], "end": e["end"], "kind": e["kind"], "notable": e["kind"] == "air",
         "what": e["text"] + " air jump airborne"} for e in felt_events() if e["kind"] == "air"]}}
    hits = find.lexical("where did I jump", clips)
    assert hits and hits[0]["source"] == "felt" and 46 <= hits[0]["start"] <= 47
