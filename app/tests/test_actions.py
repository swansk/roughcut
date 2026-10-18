"""The action vocabulary (redesign wave 12, lane AIOPS; REDESIGN-GOAL dimension 15).

`roughcut/actions.py` is the server's half of "the AI can do everything the pro tools do":
the ops the model may answer with, each checked against the cut it will meet (the ops
before it applied), described in one line for the review panel, and simulated for the
ghost lane. Pure: no server, no browser, no model."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import actions  # noqa: E402

FPS = 24


def state(**over) -> dict:
    st = {
        "segments": [
            {"id": "s1", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0},
            {"id": "s2", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0},
            {"id": "s3", "clip": "CLIP_C.MP4", "in": 2.0, "out": 5.0, "speed": 0.5},
        ],
        "clips": {"CLIP_A.MP4": {"duration": 6.0}, "CLIP_B.MP4": {"duration": 6.0},
                  "CLIP_C.MP4": {"duration": 6.0}},
        "fps": FPS, "markers": [], "in": None, "out": None, "tracks": {}, "overlays": [],
        "transitions": [], "colour": {}, "looks": ["crisp", "alpine", "filmic"],
        "presets": {"quick": "Quick 1080p", "youtube": "YouTube 1080p"},
    }
    st.update(over)
    return st


def one(op: dict, st: dict | None = None) -> tuple[list, list]:
    return actions.validate([op], st or state())


def accepted(op: dict, st: dict | None = None) -> dict:
    ok, bad = one(op, st)
    assert not bad, bad
    assert len(ok) == 1
    return ok[0]


def refused(op: dict, st: dict | None = None) -> str:
    ok, bad = one(op, st)
    assert not ok, ok
    assert len(bad) == 1 and bad[0]["i"] == 0
    return bad[0]["why"]


# ---------------------------------------------------------------- the vocabulary itself

def test_every_family_of_the_pro_tools_has_its_ops():
    """Dimension 15's list: insert / overwrite / move / razor / ripple delete / trim /
    roll / slip, markers and In/Out, track add, speed, Motion / Opacity / Volume with
    keyframes, Lumetri (Basic, Curves, Wheels, HSL), transitions, Designed Effects, and
    an export with a preset."""
    need = {"insert", "overwrite", "move", "razor", "ripple_delete", "trim", "roll", "slip",
            "marker", "in_out", "track_add", "speed", "set", "keys", "curve", "wheel",
            "look", "match", "transition", "fade", "design_effect", "export", "append",
            "overlay"}
    assert need <= set(actions.OPS)
    assert set(actions.FAMILIES) == {"timeline", "effect_controls", "lumetri", "transitions",
                                     "designed_effects", "export"}
    for p in ("posx", "posy", "scale", "rotation", "opacity", "volume_db", "temperature",
              "tint", "exposure", "contrast", "highlights", "shadows", "whites", "blacks",
              "saturation", "hsl_hue", "hsl_shift", "hsl_saturation"):
        assert p in actions.SET_PROPS
    text = actions.schema_text()
    for op in actions.OPS:
        assert f'"op":"{op}"' in text, op


def test_the_module_documents_its_schema():
    doc = actions.__doc__
    for op in actions.OPS:
        assert op in doc, op
    assert "FILM seconds" in doc and "CLIP seconds" in doc and "new:1" in doc


# ---------------------------------------------------------------- timeline

def test_insert_and_overwrite():
    op = accepted({"op": "insert", "clip": "CLIP_C.MP4", "in": 0, "out": 1, "at": 1.0})
    assert op == {"op": "insert", "clip": "CLIP_C.MP4", "in": 0.0, "out": 1.0, "at": 1.0,
                  "makes": "new:1"}
    assert actions.describe(op, state()) == "Insert CLIP_C (0.00–1.00 s) at 00:00:01:00"
    sim = actions.simulate([op], state())["segments"]
    assert [s["id"] for s in sim] == ["s1", "new:1", "new:part1", "s2", "s3"]
    assert sim[0]["out"] == 2.0 and sim[2]["in"] == 2.0
    ow = accepted({"op": "overwrite", "clip": "CLIP_C.MP4", "in": 0, "out": 2, "at": 1.0})
    assert actions.describe(ow, state()).startswith("Overwrite with CLIP_C (0.00–2.00 s) at 00:00:01:00")
    sim = actions.simulate([ow], state())["segments"]
    # s1 keeps 1.0 s of its head, the new shot covers 1-3 s, s2's first second goes: its
    # tail is a new piece, as tl.overwrite's cutAt makes it (the razor's second half)
    assert [(s["id"], s["in"], s["out"]) for s in sim[:3]] == [
        ("s1", 1.0, 2.0), ("new:1", 0.0, 2.0), ("new:part2", 1.0, 2.0)]


@pytest.mark.parametrize("op, why", [
    ({"op": "insert", "clip": "NOPE.MP4", "in": 0, "out": 1, "at": 0}, "no clip 'NOPE.MP4'"),
    ({"op": "insert", "clip": "CLIP_C.MP4", "in": 0, "out": 9, "at": 0}, "past the end of CLIP_C"),
    ({"op": "insert", "clip": "CLIP_C.MP4", "in": 1, "out": 1.1, "at": 0}, "shorter than 0.2"),
    ({"op": "insert", "clip": "CLIP_C.MP4", "in": -1, "out": 1, "at": 0}, "before the clip starts"),
    ({"op": "insert", "clip": "CLIP_C.MP4", "in": 0, "out": 1, "at": 99}, "outside the sequence"),
    ({"op": "insert", "clip": "CLIP_C.MP4", "in": 0, "out": 1}, "needs `at`"),
    ({"op": "overwrite", "clip": "CLIP_C.MP4", "in": "x", "out": 1, "at": 0}, "must be numbers"),
])
def test_insert_refusals(op, why):
    assert why in refused(op)


def test_append_move_and_ripple_delete():
    ap = accepted({"op": "append", "clip": "CLIP_A.MP4", "in": 4, "out": 5, "after": "s1"})
    assert actions.describe(ap, state()) == "Add CLIP_A (4.00–5.00 s) after shot 1 (CLIP_A)"
    assert actions.describe({"op": "append", "clip": "CLIP_A.MP4", "in": 4, "out": 5},
                            state()) == "Add CLIP_A (4.00–5.00 s) at the end"
    mv = accepted({"op": "move", "ids": ["s3"], "before": "s1"})
    assert actions.describe(mv, state()) == "Move shot 3 (CLIP_C) before shot 1 (CLIP_A)"
    assert [s["id"] for s in actions.simulate([mv], state())["segments"]] == ["s3", "s1", "s2"]
    assert actions.describe({"op": "move", "ids": ["s1"]}, state()) == "Move shot 1 (CLIP_A) to the end"
    rd = accepted({"op": "ripple_delete", "ids": ["s2"]})
    assert actions.describe(rd, state()) == "Ripple delete shot 2 (CLIP_B)"
    assert [s["id"] for s in actions.simulate([rd], state())["segments"]] == ["s1", "s3"]
    assert "already there" in refused({"op": "move", "ids": ["s3"]})
    assert "before itself" in refused({"op": "move", "ids": ["s1"], "before": "s1"})
    assert "not in the cut" in refused({"op": "ripple_delete", "ids": ["zz"]})
    assert "list of shot ids" in refused({"op": "ripple_delete"})
    assert "not in the cut" in refused({"op": "append", "clip": "CLIP_A.MP4", "in": 4, "out": 5,
                                        "after": "zz"})


def test_razor_names_its_new_half_for_the_ops_after_it():
    ok, bad = actions.validate([
        {"op": "razor", "at": 1.0},                                    # inside s1
        {"op": "transition", "a": "s1", "b": "new:1", "kind": "cross_dissolve", "dur": 0.5},
    ], state())
    assert not bad
    assert ok[0] == {"op": "razor", "id": "s1", "at": 1.0, "makes": "new:1"}
    assert actions.describe(ok[0], state()) == "Razor shot 1 (CLIP_A) at 00:00:01:00"
    # a razor by id, at a film time inside a half-speed shot
    r = accepted({"op": "razor", "id": "s3", "at": 6.0})
    assert r["at"] == 6.0
    sim = actions.simulate([r], state())["segments"]
    assert (sim[2]["in"], sim[2]["out"], sim[3]["in"]) == (2.0, 3.0, 3.0)   # 2 film s × 0.5


@pytest.mark.parametrize("op, why", [
    ({"op": "razor", "id": "s1", "at": 0.1}, "within 0.2 s of an edge"),
    ({"op": "razor", "id": "s1", "at": 3.0}, "is not inside shot 1"),
    ({"op": "razor", "id": "zz", "at": 1.0}, "not in the cut"),
    ({"op": "razor", "at": 50}, "outside the sequence"),
])
def test_razor_refusals(op, why):
    assert why in refused(op)


def test_a_refused_razor_takes_the_ops_naming_its_half_with_it():
    ok, bad = actions.validate([
        {"op": "razor", "id": "s1", "at": 0.05},
        {"op": "speed", "id": "new:1", "rate": 0.5},
        {"op": "speed", "id": "s2", "rate": 0.5},
    ], state())
    assert [b["i"] for b in bad] == [0, 1]
    assert "would be made by an op that was refused" in bad[1]["why"]
    assert ok == [{"op": "speed", "id": "s2", "rate": 0.5}]


def test_trim_roll_slip_speed():
    t = accepted({"op": "trim", "id": "s1", "in": 1.5})
    assert t == {"op": "trim", "id": "s1", "in": 1.5}
    assert actions.describe(t, state()) == "Trim shot 1 (CLIP_A): in 1.00 → 1.50 s"
    assert actions.describe({"op": "trim", "id": "s2", "out": 1.0}, state()) == \
        "Trim shot 2 (CLIP_B): out 2.00 → 1.00 s"
    r = accepted({"op": "roll", "a": "s1", "b": "s2", "by": 0.5})
    assert r == {"op": "roll", "a": "s1", "b": "s2", "by": 0.5}
    assert actions.describe(r, state()) == "Roll the edit between shot 1 (CLIP_A) and shot 2 (CLIP_B) by +0.50 s"
    sim = actions.simulate([r], state())["segments"]
    assert (sim[0]["out"], sim[1]["in"]) == (3.5, 0.5)
    s = accepted({"op": "slip", "id": "s1", "by": -0.5})
    assert actions.describe(s, state()) == "Slip shot 1 (CLIP_A) by −0.50 s"
    sp = accepted({"op": "speed", "id": "s2", "rate": 0.5})
    assert actions.describe(sp, state()) == "Shot 2 (CLIP_B): Speed 50 %"


@pytest.mark.parametrize("op, why", [
    ({"op": "trim", "id": "s1"}, "needs `in` or `out`"),
    ({"op": "trim", "id": "s1", "out": 1.1}, "shorter than 0.2"),
    ({"op": "trim", "id": "s1", "out": 7}, "past the end of CLIP_A"),
    ({"op": "trim", "id": "s1", "in": 1.0}, "already 1.00–3.00"),
    ({"op": "roll", "a": "s1", "b": "s3", "by": 0.5}, "does not come right after"),
    ({"op": "roll", "a": "s1", "b": "s2", "by": -1.0}, "at most 0.00 s earlier"),
    ({"op": "roll", "a": "s1", "b": "s2", "by": 0}, "non-zero"),
    ({"op": "slip", "id": "s1", "by": -2}, "can slip at most 1.00 s earlier"),
    ({"op": "slip", "id": "s1", "by": 4}, "can slip at most 3.00 s later"),
    ({"op": "speed", "id": "s2", "rate": 9}, "speed is 0.1 to 4"),
    ({"op": "speed", "id": "s3", "rate": 0.5}, "already plays at 0.5"),
])
def test_trim_roll_slip_speed_refusals(op, why):
    assert why in refused(op)


def test_markers_in_out_tracks_and_overlays():
    m = accepted({"op": "marker", "at": 2.5})
    assert actions.describe(m, state()) == "Marker at 00:00:02:12"
    assert "a marker at 00:00:02:12 already" in refused({"op": "marker", "at": 2.5},
                                                        state(markers=[2.5]))
    io = accepted({"op": "in_out", "in": 1.0, "out": 3.0})
    assert actions.describe(io, state()) == "Sequence In 00:00:01:00, Out 00:00:03:00"
    assert "Out must come after its In" in refused({"op": "in_out", "in": 3, "out": 1})
    assert "needs `in` and/or `out`" in refused({"op": "in_out"})
    tr = accepted({"op": "track_add", "kind": "video"})
    assert actions.describe(tr, state()) == "Add a video track (V3)"
    assert "video or audio" in refused({"op": "track_add", "kind": "subtitle"})
    full = {f"V{n}": {} for n in range(3, 10)}
    assert "no more video tracks" in refused({"op": "track_add", "kind": "video"},
                                             state(tracks=full))
    ov = accepted({"op": "overlay", "clip": "CLIP_C.MP4", "in": 0, "out": 2, "at": 1})
    assert actions.describe(ov, state()) == "CLIP_C (0.00–2.00 s) on V2 at 00:00:01:00"
    assert "no track V3" in refused({"op": "overlay", "clip": "CLIP_C.MP4", "in": 0, "out": 2,
                                     "at": 1, "track": "V3"})
    # a track the list adds first can take it
    ok, bad = actions.validate([{"op": "track_add", "kind": "video"},
                                {"op": "overlay", "clip": "CLIP_C.MP4", "in": 0, "out": 2,
                                 "at": 1, "track": "V3"}], state())
    assert not bad and ok[1]["track"] == "V3"


# ---------------------------------------------------------------- Effect Controls

def test_set_every_kind_of_property():
    for prop, v, line in [("exposure", 0.4, "Shot 2 (CLIP_B): Exposure +0.40"),
                          ("scale", 120, "Shot 2 (CLIP_B): Scale 120"),
                          ("opacity", 50, "Shot 2 (CLIP_B): Opacity 50 %"),
                          ("volume_db", -6, "Shot 2 (CLIP_B): Volume −6.0 dB"),
                          ("temperature", 20, "Shot 2 (CLIP_B): Temperature +20"),
                          ("posx", 1200, "Shot 2 (CLIP_B): Position X 1200 px"),
                          ("hsl_shift", -30, "Shot 2 (CLIP_B): HSL hue shift −30°")]:
        op = accepted({"op": "set", "id": "s2", "prop": prop, "value": v})
        assert op == {"op": "set", "id": "s2", "prop": prop, "value": float(v)}
        assert actions.describe(op, state()) == line


@pytest.mark.parametrize("op, why", [
    ({"op": "set", "id": "s2", "prop": "glow", "value": 1}, "unknown property 'glow'"),
    ({"op": "set", "id": "s2", "prop": "exposure", "value": 2}, "Exposure is -0.415 to 0.585"),
    ({"op": "set", "id": "s2", "prop": "opacity", "value": "half"}, "not a number"),
    ({"op": "set", "id": "zz", "prop": "opacity", "value": 50}, "not in the cut"),
    ({"op": "set", "id": "s2", "prop": "opacity"}, "needs `value`"),
])
def test_set_refusals(op, why):
    assert why in refused(op)


def test_a_clip_on_v2_takes_motion_and_opacity_only():
    st = state(overlays=[{"id": "ov1", "clip": "CLIP_C.MP4", "in": 0, "out": 2, "at": 1}])
    op = accepted({"op": "set", "id": "ov1", "prop": "opacity", "value": 50}, st)
    assert actions.describe(op, st) == "V2 clip CLIP_C: Opacity 50 %"
    assert "Motion and Opacity only" in refused(
        {"op": "set", "id": "ov1", "prop": "exposure", "value": 0.2}, st)


def test_keys():
    op = accepted({"op": "keys", "id": "s2", "prop": "opacity", "keys": [[1.5, 0], [0, 100]]})
    assert op["keys"] == [[0.0, 100.0], [1.5, 0.0]]                   # sorted, on frames
    assert actions.describe(op, state()) == \
        "Shot 2 (CLIP_B): Opacity keyframes 100 % → 0 % over 1.50 s (2 keys)"


@pytest.mark.parametrize("op, why", [
    ({"op": "keys", "id": "s2", "prop": "exposure", "keys": [[0, 0.1]]}, "has no stopwatch"),
    ({"op": "keys", "id": "s2", "prop": "opacity", "keys": []}, "needs `keys`"),
    ({"op": "keys", "id": "s2", "prop": "opacity", "keys": [[5, 50]]}, "outside the shot"),
    ({"op": "keys", "id": "s2", "prop": "opacity", "keys": [[0, 500]]}, "Opacity is 0 to 100"),
    ({"op": "keys", "id": "s2", "prop": "opacity", "keys": [[0.5, 1], [0.51, 2]]}, "two keys on the frame"),
    ({"op": "keys", "id": "s2", "prop": "opacity", "keys": [[0.5]]}, "each key is [t, value]"),
    ({"op": "keys", "id": "s2", "prop": "opacity", "keys": [[i / 24, 50] for i in range(65)]},
     "at most 64"),
])
def test_keys_refusals(op, why):
    assert why in refused(op)


# ---------------------------------------------------------------- Lumetri

def test_curves_wheels_looks_and_match():
    c = accepted({"op": "curve", "id": "s1", "channel": "master",
                  "points": [[0, 0], [0.5, 0.6], [1, 1]]})
    assert actions.describe(c, state()) == "Shot 1 (CLIP_A): Lumetri RGB master curve, 3 points"
    w = accepted({"op": "wheel", "id": "s1", "range": "midtones", "x": 0.1, "y": -0.05, "level": 10})
    assert w == {"op": "wheel", "id": "s1", "range": "midtones", "x": 0.1, "y": -0.05, "level": 10.0}
    assert actions.describe(w, state()) == \
        "Shot 1 (CLIP_A): Lumetri Midtones wheel puck (+0.10, -0.05), lightness +10"
    lk = accepted({"op": "look", "ids": "all", "name": "alpine", "strength": 0.6})
    assert actions.describe(lk, state()) == "The film: look alpine at 0.60"
    lk2 = accepted({"op": "look", "ids": ["s2"], "name": "none"})
    assert actions.describe(lk2, state()) == "Shot 2 (CLIP_B): no look"
    m = accepted({"op": "match", "ids": ["s3"], "to": "reference", "reference": "s1"})
    assert actions.describe(m, state()) == "Shot 3 (CLIP_C): Color Match to the reference, shot 1 (CLIP_A)"
    assert actions.describe({"op": "match", "ids": ["s2"], "to": "previous"}, state()) == \
        "Shot 2 (CLIP_B): Color Match to the shot before"


@pytest.mark.parametrize("op, why", [
    ({"op": "curve", "id": "s1", "channel": "luma", "points": [[0, 0], [1, 1]]}, "`channel` must be"),
    ({"op": "curve", "id": "s1", "points": [[0, 0]]}, "2 to 16 points"),
    ({"op": "curve", "id": "s1", "points": [[0, 0], [0, 0.5], [1, 1]]}, "x strictly increasing"),
    ({"op": "curve", "id": "s1", "points": [[0, 0], [1, 1.5]]}, "on 0..1"),
    ({"op": "wheel", "id": "s1", "range": "mids", "x": 0, "y": 0}, "`range` must be"),
    ({"op": "wheel", "id": "s1", "range": "shadows", "x": 0.9, "y": 0.9}, "inside the wheel"),
    ({"op": "wheel", "id": "s1", "range": "shadows", "x": 0, "y": 0, "level": 200}, "lightness is -100 to 100"),
    ({"op": "look", "ids": "all", "name": "teal"}, "no look 'teal'"),
    ({"op": "look", "ids": "all", "name": "film"}, "the film's own look"),
    ({"op": "look", "ids": ["s1"]}, "needs `name`"),
    ({"op": "look", "ids": ["s1"], "name": "crisp", "strength": 3}, "strength is 0 to 1"),
    ({"op": "match", "ids": ["s1"], "to": "sky"}, "previous or reference"),
    ({"op": "match", "ids": ["s1"], "reference": "s1"}, "matched to itself"),
])
def test_lumetri_refusals(op, why):
    assert why in refused(op)


# ---------------------------------------------------------------- transitions

def test_transitions_and_fades():
    t = accepted({"op": "transition", "a": "s1", "b": "s2", "kind": "cross_dissolve"})
    assert t["dur"] == 1.0
    assert actions.describe(t, state()) == "Cross Dissolve between shot 1 (CLIP_A) and shot 2 (CLIP_B), 1.00 s"
    f = accepted({"op": "fade", "id": "s3", "edge": "end", "kind": "dip_to_black", "dur": 2})
    assert actions.describe(f, state()) == "Dip to Black at the end of shot 3 (CLIP_C), 2.00 s"
    assert set(actions.TRANSITION_KINDS) == {"cross_dissolve", "dip_to_black", "dip_to_white",
                                             "film_dissolve", "audio_crossfade", "constant_gain",
                                             "exponential_fade"}


@pytest.mark.parametrize("op, why", [
    ({"op": "transition", "a": "s1", "b": "s3", "kind": "cross_dissolve"}, "does not come right after"),
    ({"op": "transition", "a": "s1", "b": "s2", "kind": "wipe"}, "unknown transition 'wipe'"),
    ({"op": "transition", "a": "s1", "b": "s2", "kind": "cross_dissolve", "dur": 30}, "0.04 to 10"),
    ({"op": "fade", "id": "s1", "edge": "middle", "kind": "dip_to_black"}, "start or end"),
])
def test_transition_refusals(op, why):
    assert why in refused(op)


# ---------------------------------------------------------------- designed effects, export

def test_design_effect_and_export():
    d = accepted({"op": "design_effect", "id": "s2", "note": "hit markers where the skis hit"})
    assert actions.describe(d, state()) == \
        "Designed effect on shot 2 (CLIP_B): “hit markers where the skis hit” (its own review follows)"
    assert "needs `note`" in refused({"op": "design_effect", "id": "s2", "note": "  "})
    e = accepted({"op": "export", "preset": "quick", "range": "all", "name": "review copy"})
    assert actions.describe(e, state()) == \
        "Export Quick 1080p, Entire Sequence → review copy.mp4, on accept"
    assert "unknown preset 'mov'" in refused({"op": "export", "preset": "mov", "range": "all"})
    assert "no In / Out" in refused({"op": "export", "preset": "quick", "range": "in_out"})
    assert "all or in_out" in refused({"op": "export", "preset": "quick", "range": "half"})
    assert "empty" in refused({"op": "export", "preset": "quick", "range": "all"},
                              state(segments=[]))
    ok, bad = actions.validate([{"op": "in_out", "in": 1, "out": 3},
                                {"op": "export", "preset": "youtube", "range": "in_out"}], state())
    assert not bad and ok[1]["range"] == "in_out"


# ---------------------------------------------------------------- the list as a whole

def test_unknown_ops_bad_shapes_and_the_cap():
    ok, bad = actions.validate([{"op": "teleport"}, "razor", {"no": "op"}], state())
    assert not ok
    assert [b["why"] for b in bad] == ["unknown op 'teleport'", "not an op object", "unknown op None"]
    ok, bad = actions.validate({"op": "razor"}, state())
    assert not ok and bad[0]["why"] == "ops must be a list"
    many = [{"op": "marker", "at": i * 0.1} for i in range(actions.MAX_OPS + 3)]
    ok, bad = actions.validate(many, state())
    assert len(ok) == actions.MAX_OPS
    assert len(bad) == 3 and all("more than 24 ops" in b["why"] for b in bad)


def test_check_carries_lines_indices_refusals_and_the_ghost():
    out = actions.check([
        {"op": "set", "id": "s1", "prop": "exposure", "value": 9},
        {"op": "razor", "id": "s2", "at": 3.0},
        {"op": "marker", "at": 1},
    ], state())
    assert out["index"] == [1, 2]
    assert out["lines"] == ["Razor shot 2 (CLIP_B) at 00:00:03:00", "Marker at 00:00:01:00"]
    assert out["refused"][0]["i"] == 0
    assert out["families"] == ["timeline"]
    assert [s["id"] for s in out["preview"]["segments"]] == ["s1", "s2", "new:1", "s3"]
    # nothing changes V1: no ghost
    assert "preview" not in actions.check([{"op": "marker", "at": 1}], state())


def test_lines_name_shots_as_the_timeline_shows_them_when_the_review_opens():
    """A shot is its number on the timeline the editor is looking at, and its clip; a shot
    an op makes is named by what made it."""
    out = actions.check([{"op": "move", "ids": ["s3"], "before": "s1"},
                         {"op": "speed", "id": "s1", "rate": 2},
                         {"op": "razor", "id": "s2", "at": 8.0},
                         {"op": "transition", "a": "s2", "b": "new:1", "kind": "dip_to_black"},
                         {"op": "append", "clip": "CLIP_A.MP4", "in": 0, "out": 1},
                         {"op": "speed", "id": "new:2", "rate": 0.5}], state())
    assert out["refused"] == []
    assert out["lines"] == ["Move shot 3 (CLIP_C) before shot 1 (CLIP_A)",
                            "Shot 1 (CLIP_A): Speed 200 %",
                            "Razor shot 2 (CLIP_B) at 00:00:08:00",
                            "Dip to Black between shot 2 (CLIP_B) and shot 2's second half, 1.00 s",
                            "Add CLIP_A (0.00–1.00 s) at the end",
                            "The new CLIP_A shot: Speed 50 %"]


def test_timecode_matches_the_timeline():
    assert actions.timecode(12 + 4 / 24, 24) == "00:00:12:04"
    assert actions.timecode(3600 + 61.48, 25) == "01:01:01:12"
    assert actions.timecode(0.5 / 24 * 3, 24) == "00:00:00:02"      # 1.5 frames: half up, as JS
    assert actions.grid_rate(29.97) == pytest.approx(30000 / 1001)
    assert actions.grid_rate(24) == 24


def test_state_from_the_project_payload():
    payload = {"segments": [{"id": "a", "clip": "X.MP4", "in": 0, "out": 1}],
               "clips": {"X.MP4": {"duration": 4.0, "transcript": []}}, "fps": 29.97,
               "markers": [1.0], "in": None, "out": 2.0, "tracks": {"V3": {}}, "overlays": [],
               "transitions": [], "colour": {"look": "crisp"}}
    st = actions.state_from(payload, looks=["crisp"], presets={"quick": "Quick 1080p"})
    assert st["clips"] == {"X.MP4": {"duration": 4.0}}
    assert st["fps"] == 29.97 and st["out"] == 2.0 and st["presets"] == {"quick": "Quick 1080p"}
    assert actions.check([{"op": "overlay", "clip": "X.MP4", "in": 0, "out": 1, "at": 0,
                           "track": "V3"}], st)["refused"] == []
