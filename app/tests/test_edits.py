"""`roughcut.edits` (INTAKE M13): every op, every refusal, the words.

Pure: nothing here touches a file or a server. The cut is two shots with ids, the
clips are two 6 s files, and each test says what one operation does to them or why
it is refused — with the sentence, since the sentence is what the model and the
human read.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import edits  # noqa: E402


def _segs() -> list[dict]:
    return [{"id": "a", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
            {"id": "b", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}]


CLIPS = {"CLIP_A.MP4": {"duration": 6.0}, "CLIP_B.MP4": {"duration": 6.0}}


def _apply(*ops):
    segs = _segs()
    clean = edits.validate_ops(list(ops), segs, CLIPS)
    return edits.apply_ops(segs, CLIPS, clean)


def _refused(*ops, clips=CLIPS) -> str:
    with pytest.raises(ValueError) as exc:
        edits.validate_ops(list(ops), _segs(), clips)
    return str(exc.value)


# ------------------------------------------------------------------ the arithmetic

def test_dur_is_the_range_at_the_speed():
    assert edits.dur({"in": 1.0, "out": 3.0}) == 2.0
    assert edits.dur({"in": 1.0, "out": 3.0, "speed": 0.5}) == 4.0
    assert edits.dur({"in": 1.0, "out": 3.0, "speed": 2}) == 1.0
    assert edits.speed_of({"speed": "x"}) == 1.0          # a bad value plays as 1
    assert edits.speed_of({"speed": 9}) == 1.0             # out of range plays as 1
    assert edits.dur({"in": "1", "out": "3", "speed": None}) == 2.0


def test_validate_speed_keeps_only_what_is_not_one():
    assert edits.validate_speed(None) is None
    assert edits.validate_speed("") is None
    assert edits.validate_speed(1) is None
    assert edits.validate_speed("1.0") is None
    assert edits.validate_speed(0.5) == 0.5
    assert edits.validate_speed("0.3333") == 0.333
    with pytest.raises(ValueError, match="not a number"):
        edits.validate_speed("fast")
    with pytest.raises(ValueError, match="out of range"):
        edits.validate_speed(0.05)
    with pytest.raises(ValueError, match="out of range"):
        edits.validate_speed(4.5)


def test_generated_names_are_deterministic_and_recognisable():
    a = edits.generated_name("black", 2.0)
    assert a == edits.generated_name("black", 2.0)
    assert a.startswith("gen_black_") and a.endswith(".mp4")
    assert a != edits.generated_name("black", 2.5)
    assert edits.generated_name("colour", 2.0, color="#ff0000") != edits.generated_name("colour", 2.0, color="#00ff00")
    assert (edits.generated_name("still", 1.5, from_clip="CLIP_A.MP4", at=2.0)
            != edits.generated_name("still", 1.5, from_clip="CLIP_A.MP4", at=2.5))
    assert edits.is_generated(a) and not edits.is_generated("CLIP_A.MP4")


# ------------------------------------------------------------------ the ops

def test_extend_moves_the_points_by_a_delta():
    r = _apply({"op": "extend", "shot": "a", "in": -0.5, "out": 1.0})
    assert (r["segments"][0]["in"], r["segments"][0]["out"]) == (0.5, 4.0)
    assert r["changed"] == ["a"] and r["id_map"] == {} and r["generated"] == []
    assert r["words"] == ["extend shot CLIP_A.MP4: in -0.50s, out +1.00s"]


def test_extend_refusals_say_why():
    assert "before the clip starts" in _refused({"op": "extend", "shot": "a", "in": -1.5})
    assert "pass the clip's end" in _refused({"op": "extend", "shot": "a", "out": 3.5})
    assert "shorter than a shot can be" in _refused({"op": "extend", "shot": "a", "in": 1.9})
    assert "moves nothing" in _refused({"op": "extend", "shot": "a"})
    assert "not in the cut" in _refused({"op": "extend", "shot": "zz", "out": 1})


def test_set_range_is_the_range_outright():
    r = _apply({"op": "set_range", "shot": "b", "in": 2.0, "out": 5.5})
    assert (r["segments"][1]["in"], r["segments"][1]["out"]) == (2.0, 5.5)
    assert r["words"] == ["set shot CLIP_B.MP4 to 2.00–5.50s"]
    assert "shorter than" in _refused({"op": "set_range", "shot": "b", "in": 2.0, "out": 2.1})
    assert "passes the clip's end" in _refused({"op": "set_range", "shot": "b", "in": 2.0, "out": 7.0})
    assert "not a number" in _refused({"op": "set_range", "shot": "b", "in": "x", "out": 7.0})


def test_split_makes_a_second_shot_with_a_placeholder_id():
    r = _apply({"op": "split", "shot": "a", "at": 2.0})
    s = r["segments"]
    assert [x["id"] for x in s] == ["a", "new:1", "b"]
    assert (s[0]["in"], s[0]["out"]) == (1.0, 2.0)
    assert (s[1]["in"], s[1]["out"], s[1]["clip"]) == (2.0, 3.0, "CLIP_A.MP4")
    assert "why" not in s[1]                       # the reason belongs to the first piece
    assert r["id_map"] == {"new:1": None}
    assert r["changed"] == ["a", "new:1"]
    assert r["words"] == ["split shot CLIP_A.MP4 at 2.00s"]


def test_split_needs_room_on_both_sides():
    assert "not inside the shot" in _refused({"op": "split", "shot": "a", "at": 1.1})
    assert "not inside the shot" in _refused({"op": "split", "shot": "a", "at": 3.0})
    assert "not inside the shot" in _refused({"op": "split", "shot": "a", "at": 5.0})


def test_a_later_op_can_name_the_new_shot():
    r = _apply({"op": "split", "shot": "a", "at": 2.0},
               {"op": "speed", "shot": "new:1", "rate": 0.5})
    assert r["segments"][1]["speed"] == 0.5
    assert "speed" not in r["segments"][0]
    assert r["words"][1] == "CLIP_A.MP4 at 0.5× for the whole shot"


def test_speed_on_the_whole_shot():
    r = _apply({"op": "speed", "shot": "a", "rate": 2})
    assert r["segments"][0]["speed"] == 2.0 and r["changed"] == ["a"]
    assert edits.dur(r["segments"][0]) == 1.0
    r = _apply({"op": "speed", "shot": "a", "rate": 1})
    assert "speed" not in r["segments"][0]         # 1× is no speed at all


def test_speed_on_a_range_splits_around_it():
    r = _apply({"op": "speed", "shot": "a", "rate": 0.5, "from": 1.5, "to": 2.5})
    s = r["segments"]
    assert [x["id"] for x in s] == ["a", "new:1", "new:2", "b"]
    assert [(x["in"], x["out"]) for x in s[:3]] == [(1.0, 1.5), (1.5, 2.5), (2.5, 3.0)]
    assert s[1]["speed"] == 0.5 and "speed" not in s[0] and "speed" not in s[2]
    assert r["changed"] == ["new:1"]
    assert r["words"] == ["CLIP_A.MP4 at 0.5× from 1.50 to 2.50s"]


def test_speed_on_a_range_touching_an_edge_splits_once():
    r = _apply({"op": "speed", "shot": "a", "rate": 0.25, "from": 1.0, "to": 2.0})
    s = r["segments"]
    assert [x["id"] for x in s] == ["a", "new:1", "b"]
    assert s[0]["speed"] == 0.25 and "speed" not in s[1]
    r = _apply({"op": "speed", "shot": "a", "rate": 4, "from": 2.0, "to": 3.0})
    s = r["segments"]
    assert [x["id"] for x in s] == ["a", "new:1", "b"]
    assert s[1]["speed"] == 4.0 and "speed" not in s[0]


def test_speed_refusals():
    assert "out of range" in _refused({"op": "speed", "shot": "a", "rate": 8})
    assert "out of range" in _refused({"op": "speed", "shot": "a", "rate": 0})
    assert "not inside the shot" in _refused({"op": "speed", "shot": "a", "rate": 0.5, "from": 0.5, "to": 2.0})
    assert "shorter than" in _refused({"op": "speed", "shot": "a", "rate": 0.5, "from": 1.5, "to": 1.6})


def test_generate_black_at_the_end_by_default():
    r = _apply({"op": "generate", "kind": "black", "seconds": 2})
    s = r["segments"]
    name = edits.generated_name("black", 2.0)
    assert [x["id"] for x in s] == ["a", "b", "new:1"]
    assert s[2] == {"clip": name, "in": 0.0, "out": 2.0, "id": "new:1", "why": "black slide, 2s"}
    assert r["generated"] == [{"kind": "black", "seconds": 2.0, "color": None,
                               "from_clip": None, "at": None, "clip": name}]
    assert r["words"] == ["a black clip of 2s"]


def test_generate_places_before_or_after_a_shot():
    r = _apply({"op": "generate", "kind": "colour", "color": "#FF8800", "seconds": 1, "before": "a"})
    assert [x["id"] for x in r["segments"]] == ["new:1", "a", "b"]
    assert r["generated"][0]["color"] == "#ff8800"
    assert r["words"] == ["a colour #ff8800 clip of 1s"]
    r = _apply({"op": "generate", "kind": "black", "after": "a", "why": "a breath"})
    assert [x["id"] for x in r["segments"]] == ["a", "new:1", "b"]
    assert r["segments"][1]["why"] == "a breath"
    r = _apply({"op": "generate", "kind": "black", "before": None})
    assert [x["id"] for x in r["segments"]] == ["a", "b", "new:1"]


def test_the_same_slide_twice_is_one_file():
    r = _apply({"op": "generate", "kind": "black", "seconds": 2, "before": "a"},
               {"op": "generate", "kind": "black", "seconds": 2})
    assert len(r["generated"]) == 1
    assert r["segments"][0]["clip"] == r["segments"][-1]["clip"]


def test_generate_a_still_from_a_shot():
    r = _apply({"op": "generate", "kind": "still", "from_shot": "a", "at": 2.5, "seconds": 1.5, "after": "a"})
    g = r["generated"][0]
    assert (g["kind"], g["from_clip"], g["at"], g["seconds"]) == ("still", "CLIP_A.MP4", 2.5, 1.5)
    assert r["segments"][1]["clip"] == edits.generated_name("still", 1.5, from_clip="CLIP_A.MP4", at=2.5)
    assert r["words"] == ["a a still clip of 1.5s"]


def test_generate_refusals():
    assert "unknown generate kind" in _refused({"op": "generate", "kind": "white"})
    assert "not #rrggbb" in _refused({"op": "generate", "kind": "colour", "color": "red"})
    assert "needs from_shot and at" in _refused({"op": "generate", "kind": "still", "at": 2})
    assert "not inside from_shot" in _refused({"op": "generate", "kind": "still", "from_shot": "a", "at": 5})
    assert "out of range" in _refused({"op": "generate", "kind": "black", "seconds": 60})
    assert "not in the cut" in _refused({"op": "generate", "kind": "black", "before": "zz"})
    assert "not in the cut" in _refused({"op": "generate", "kind": "black", "after": "zz"})


def test_freeze_puts_a_still_between_the_halves():
    r = _apply({"op": "freeze", "shot": "a", "at": 2.0, "seconds": 1.5})
    s = r["segments"]
    still = edits.generated_name("still", 1.5, from_clip="CLIP_A.MP4", at=2.0)
    assert [x["id"] for x in s] == ["a", "new:2", "new:1", "b"]
    assert [(x["clip"], x["in"], x["out"]) for x in s[:3]] == [
        ("CLIP_A.MP4", 1.0, 2.0), (still, 0.0, 1.5), ("CLIP_A.MP4", 2.0, 3.0)]
    assert s[1]["why"] == "freeze frame of CLIP_A.MP4 at 2.00s"
    assert r["generated"][0]["kind"] == "still" and r["generated"][0]["at"] == 2.0
    assert r["changed"] == ["new:2"] and set(r["id_map"]) == {"new:1", "new:2"}
    assert r["words"] == ["freeze CLIP_A.MP4 at 2.00s for 1.5s"]


def test_freeze_refusals():
    assert "not inside the shot" in _refused({"op": "freeze", "shot": "a", "at": 1.0})
    assert "out of range" in _refused({"op": "freeze", "shot": "a", "at": 2.0, "seconds": 0.1})


def test_insert_footage_into_the_cut():
    r = _apply({"op": "insert", "clip": "CLIP_B.MP4", "in": 3.0, "out": 5.0, "after": "a", "why": "the jump"})
    s = r["segments"]
    assert [x["id"] for x in s] == ["a", "new:1", "b"]
    assert s[1] == {"clip": "CLIP_B.MP4", "in": 3.0, "out": 5.0, "id": "new:1", "why": "the jump"}
    assert r["words"] == ["insert CLIP_B.MP4 3.00–5.00s"]


def test_insert_refusals():
    assert "does not have" in _refused({"op": "insert", "clip": "NOPE.MP4", "in": 0, "out": 1})
    assert "needs a clip" in _refused({"op": "insert", "in": 0, "out": 1})
    assert "passes the clip's end" in _refused({"op": "insert", "clip": "CLIP_B.MP4", "in": 3, "out": 7})
    assert "shorter than" in _refused({"op": "insert", "clip": "CLIP_B.MP4", "in": 3, "out": 3.1})
    # without a clips map the length is unknown and the insert is taken on trust
    ops = edits.validate_ops([{"op": "insert", "clip": "X.MP4", "in": 0, "out": 99}], _segs(), None)
    assert ops[0]["clip"] == "X.MP4"


def test_remove_and_the_empty_cut():
    r = _apply({"op": "remove", "shot": "a"})
    assert [x["id"] for x in r["segments"]] == ["b"]
    assert r["changed"] == [] and r["words"] == ["remove shot CLIP_A.MP4"]
    assert "empty cut" in _refused({"op": "remove", "shot": "a"}, {"op": "remove", "shot": "b"})
    assert "not in the cut" in _refused({"op": "remove", "shot": "a"}, {"op": "extend", "shot": "a", "out": 1})


def test_move_before_after_and_to_the_end():
    r = _apply({"op": "move", "shot": "b", "before": "a"})
    assert [x["id"] for x in r["segments"]] == ["b", "a"]
    r = _apply({"op": "move", "shot": "a", "after": "b"})
    assert [x["id"] for x in r["segments"]] == ["b", "a"]
    r = _apply({"op": "move", "shot": "a", "before": None})
    assert [x["id"] for x in r["segments"]] == ["b", "a"]
    assert r["changed"] == ["a"] and r["words"] == ["move shot CLIP_A.MP4"]
    assert "needs before or after" in _refused({"op": "move", "shot": "a"})
    assert "not in the cut" in _refused({"op": "move", "shot": "a", "before": "zz"})


# ------------------------------------------------------------------ the list

def test_the_list_itself_is_checked():
    assert "1–24 operations" in _refused()
    assert "1–24 operations" in _refused(*([{"op": "remove", "shot": "a"}] * 25))
    with pytest.raises(ValueError, match="1–24 operations"):
        edits.validate_ops({"op": "remove"}, _segs(), CLIPS)
    assert "edit 1 is not an object" in _refused("remove")
    assert "unknown op" in _refused({"op": "delete", "shot": "a"})
    assert "needs a shot" in _refused({"op": "remove"})
    assert "edit 2 (split) needs a shot" in _refused({"op": "remove", "shot": "a"}, {"op": "split", "at": 1})


def test_ops_come_back_clean_with_the_why_trimmed():
    ops = edits.validate_ops([{"op": "extend", "shot": "a", "out": "0.5", "junk": 1, "why": "  more  "}],
                             _segs(), CLIPS)
    assert ops == [{"op": "extend", "shot": "a", "in": 0.0, "out": 0.5, "why": "more"}]
    ops = edits.validate_ops([{"op": "remove", "shot": "a", "why": "x" * 300}], _segs(), CLIPS)
    assert len(ops[0]["why"]) == 200


def test_apply_never_touches_the_input():
    segs = _segs()
    edits.apply_ops(segs, CLIPS, [{"op": "extend", "shot": "a", "in": 0.0, "out": 1.0},
                                  {"op": "split", "shot": "b", "at": 1.0},
                                  {"op": "remove", "shot": "a"}])
    assert segs == _segs()


def test_one_line_per_op_in_words():
    r = _apply({"op": "split", "shot": "a", "at": 2.0},
               {"op": "generate", "kind": "black", "seconds": 1, "before": "a"},
               {"op": "move", "shot": "b", "before": "a"},
               {"op": "remove", "shot": "new:1"})
    assert len(r["words"]) == 4
    assert [x["id"] for x in r["segments"]] == ["new:2", "b", "a"]
