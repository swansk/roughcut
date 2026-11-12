"""Never cut off a line — the measure and the fix (roughcut/sentences.py).

Karl, 2026-11-11: *"there are several clips where you cut the audio mid sentence. never
do that, extend as needed (past 2 mins is ok) so you capture complete sentences +
reaction"*. A shot with speech starts at a sentence start and ends after the sentence and
its reaction; the sentence wins over the target length; the editor's own shots and a shot
the ask marked `tight` are never changed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from roughcut import assist, boundaries, revise, sentences  # noqa: E402


def utt(start, end, text, words, speaker=None):
    return {"start": start, "end": end, "text": text, "speaker": speaker,
            "words": [{"w": w, "t": t, "e": e} for w, t, e in words]}


# 0.5-3.0  "We drove all night to get here."
# 3.6-4.2  "No way."                      (a reply 0.6 s later: the reaction)
# 6.0-9.0  "Then the van broke down on the pass."
# 12.0-14.0 "Anyway, the snow was perfect."
TRANSCRIPT = [
    utt(0.5, 3.0, "We drove all night to get here.",
        [("We", 0.5, 0.8), ("drove", 0.8, 1.2), ("all", 1.2, 1.5), ("night", 1.5, 1.9),
         ("to", 1.9, 2.0), ("get", 2.0, 2.4), ("here.", 2.4, 3.0)]),
    utt(3.6, 4.2, "No way.", [("No", 3.6, 3.8), ("way.", 3.8, 4.2)], speaker="S2"),
    utt(6.0, 9.0, "Then the van broke down on the pass.",
        [("Then", 6.0, 6.3), ("the", 6.3, 6.5), ("van", 6.5, 7.0), ("broke", 7.0, 7.4),
         ("down", 7.4, 7.8), ("on", 7.8, 8.0), ("the", 8.0, 8.2), ("pass.", 8.2, 9.0)]),
    utt(12.0, 14.0, "Anyway, the snow was perfect.",
        [("Anyway,", 12.0, 12.5), ("the", 12.5, 12.7), ("snow", 12.7, 13.2),
         ("was", 13.2, 13.5), ("perfect.", 13.5, 14.0)]),
]


def clip(name="A.MP4", duration=30.0, **over):
    c = {"clip": name, "duration": duration, "transcript": TRANSCRIPT, "visual": {}}
    c.update(over)
    return c


def clips(*names):
    return {n: clip(n) for n in names or ("A.MP4",)}


def kinds(segs, cl=None):
    return [p["kind"] for p in sentences.measure(segs, cl or clips())["problems"]]


# ------------------------------------------------------------------ sentences


def test_sentences_from_punctuation_pauses_and_dangling_utterance_ends():
    s = sentences.sentences(TRANSCRIPT)
    assert [x.text for x in s] == ["We drove all night to get here.", "No way.",
                                   "Then the van broke down on the pass.",
                                   "Anyway, the snow was perfect."]
    # an utterance the decoder broke on "a" is one sentence ("We need to go find a | ...")
    broken = [utt(0.0, 1.5, "We need to go find a", [("We", 0.0, 0.3), ("a", 0.4, 1.5)]),
              utt(1.6, 3.0, "gas station.", [("gas", 1.6, 2.0), ("station.", 2.0, 3.0)])]
    assert [x.text for x in sentences.sentences(broken)] == ["We a gas station."]
    # a pause of a second ends a sentence even with no punctuation
    paused = [utt(0.0, 1.0, "so yeah", [("so", 0.0, 0.4), ("yeah", 0.5, 1.0)]),
              utt(2.2, 3.0, "okay", [("okay", 2.2, 3.0)])]
    assert len(sentences.sentences(paused)) == 2


# ------------------------------------------------------------------ the measure


def test_measure_flags_a_mid_sentence_in_and_out():
    assert kinds([{"clip": "A.MP4", "in": 7.0, "out": 13.0}]) == ["mid_in", "mid_out"]


def test_measure_flags_an_out_with_no_beat_and_a_reply_left_out():
    # out 0.1 s after "here." and before "No way." (0.6 s later): both
    got = kinds([{"clip": "A.MP4", "in": 0.3, "out": 3.1}])
    assert got == ["no_beat", "reaction"]
    # including the reply with a beat after it: clean
    assert kinds([{"clip": "A.MP4", "in": 0.3, "out": 4.65}]) == []


def test_measure_ignores_a_shot_with_no_speech_and_a_run_on_split():
    assert kinds([{"clip": "A.MP4", "in": 9.5, "out": 11.5}]) == []
    # two shots of one clip that play one after the other are one stretch
    assert kinds([{"clip": "A.MP4", "in": 5.75, "out": 7.4},
                  {"clip": "A.MP4", "in": 7.4, "out": 9.45}]) == []


def test_speech_cuts_tool_prints_the_count(tmp_path):
    audio = tmp_path / "audio"
    audio.mkdir()
    (audio / "A.audio.json").write_text(json.dumps(
        {"duration_s": 30.0, "transcript": TRANSCRIPT, "frame_hz": 10,
         "tracks": {"vad": [0] * 300}}))
    cut = tmp_path / "cut.json"
    cut.write_text(json.dumps({"segments": [{"clip": "A.MP4", "in": 7.0, "out": 13.0}]}))
    r = subprocess.run([sys.executable, str(ROOT / "research/tools/speech_cuts.py"), str(cut),
                        "--audio", str(audio), "--json"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["clipped"] == 2


# ------------------------------------------------------------------ the fix


def test_polish_moves_a_mid_sentence_in_back_to_the_sentence_start():
    seg = boundaries.polish([{"clip": "A.MP4", "in": 7.0, "out": 9.45}], clips())[0]
    assert seg["in"] == 5.75                                   # 6.0 - PAD_HEAD
    assert seg["polished_from"] == [7.0, 9.45]
    assert "starts" in seg["polish_why"] and "Then the van" in seg["polish_why"]


def test_polish_finishes_a_line_and_waits_for_the_reply():
    seg = boundaries.polish([{"clip": "A.MP4", "in": 0.25, "out": 1.6}], clips())[0]
    # "here." ends 3.0, "No way." answers at 3.6 and ends 4.2: out after it, plus a beat
    assert seg["out"] == round(4.2 + sentences.PAD_TAIL, 2)
    assert "No way" in seg["polish_why"] and "reply" in seg["polish_why"]
    assert kinds([seg]) == []


def test_two_shots_of_one_clip_that_run_on_are_joined_not_split_mid_line():
    segs = [{"clip": "A.MP4", "in": 5.75, "out": 7.2, "id": "g1", "why": "the van"},
            {"clip": "A.MP4", "in": 7.6, "out": 9.45, "id": "g2", "why": "the pass"}]
    got = boundaries.polish(segs, clips())
    assert len(got) == 1, got
    assert (got[0]["in"], got[0]["out"]) == (5.75, 9.45) and got[0]["id"] == "g1"
    assert "joined" in got[0]["polish_why"]
    assert kinds(got) == []


def test_a_shot_with_no_speech_is_untouched():
    seg = {"clip": "A.MP4", "in": 9.5, "out": 11.5, "why": "the view"}
    assert boundaries.polish([seg], clips()) == [seg]


def test_a_hand_shot_and_a_tight_shot_are_untouched_and_nothing_grows_into_them():
    hand = {"clip": "A.MP4", "in": 7.0, "out": 8.0, "id": "gH"}
    tight = {"clip": "A.MP4", "in": 0.9, "out": 1.6, "id": "gT", "tight": True}
    got = boundaries.polish([dict(tight), dict(hand)], clips(), keep={"gH"})
    assert got[0] == tight and got[1] == hand


def test_the_line_rule_does_not_swallow_twenty_seconds():
    """An ASR run-on with no punctuation and no pause: past MAX_GROW_S the out retreats
    to the last whole line inside the shot instead of growing."""
    words = [(f"w{i}", 2.0 + i * 0.5, 2.4 + i * 0.5) for i in range(60)]   # 2.0-31.9
    runon = [utt(0.0, 1.0, "Hi there.", [("Hi", 0.0, 0.4), ("there.", 0.4, 1.0)]),
             utt(2.0, 31.9, "w " * 60, words)]
    c = {"A.MP4": {"clip": "A.MP4", "duration": 40.0, "transcript": runon}}
    seg = boundaries.polish([{"clip": "A.MP4", "in": 0.0, "out": 6.0}], c)[0]
    assert seg["out"] < 2.0, seg                # after "Hi there.", not 26 s later
    assert "earlier" in seg["polish_why"]


def test_an_ai_shot_echoed_from_the_cut_still_finishes_its_line_a_hand_one_does_not():
    cur = [{"clip": "A.MP4", "in": 7.0, "out": 9.45, "id": "g1"}]
    plan = {"segments": [dict(cur[0])], "notes": "kept"}
    ai = revise.polish_kept_alone(plan, clips(), cur)
    assert ai["segments"][0]["in"] == 5.75 and ai["lines_added_s"] > 0
    assert "no line is cut off" in ai["notes"]
    hand = revise.polish_kept_alone(plan, clips(), cur, hand={"g1"})
    assert hand["segments"][0]["in"] == 7.0 and hand["notes"] == "kept"


def test_validate_plan_carries_tight_and_polish_respects_it():
    payload = {"segments": [{"clip": "A.MP4", "in": 7.0, "out": 8.0, "why": "hard cut, asked",
                             "tight": True}], "notes": "n"}
    plan = revise.validate_plan(payload, clips())
    assert plan["segments"][0]["tight"] is True
    assert boundaries.polish(plan["segments"], clips())[0]["in"] == 7.0


def test_finishing_lines_drops_nothing_and_says_the_film_runs_long():
    """Length is an aim: nothing here shortens a shot to hit it; when finishing lines
    takes the film over, the notes say so in one sentence."""
    segs = [{"clip": "A.MP4", "in": 0.25, "out": 1.6}, {"clip": "A.MP4", "in": 7.0, "out": 8.0}]
    plan = revise.polish_kept_alone({"segments": segs, "notes": ""}, clips(), [])
    got = plan["segments"]
    assert all(b["out"] - b["in"] >= a["out"] - a["in"] for a, b in zip(segs, got))
    total = sum(s["out"] - s["in"] for s in got)
    line = sentences.over_aim_line(total, (1.0, 2.0), plan["lines_added_s"])
    assert line.startswith("Runs 0:0") and "over the 0:01–0:02 aim, so every line finishes" in line
    assert sentences.over_aim_line(1.5, (1.0, 2.0), 3.0) == ""          # inside the aim


def test_the_prompts_state_the_rule_once():
    first = revise.build_first_prompt(clips(), "", "", (108, 132))
    rev = revise.build_prompt([{"clip": "A.MP4", "in": 0.0, "out": 4.6, "why": "x"}],
                              clips(), "", "tighter", (108, 132))
    for p in (first, rev):
        assert p.count("Never cut off a line") == 1
        assert "the sentence wins" in p and "an aim, not a limit" in p


def test_assist_ops_land_on_sentence_edges_unless_tight():
    state = {"segments": [{"id": "g1", "clip": "A.MP4", "in": 0.25, "out": 4.65}]}
    ops = [{"op": "trim", "id": "g1", "out": 1.6},
           {"op": "append", "clip": "A.MP4", "in": 7.0, "out": 8.0},
           {"op": "append", "clip": "A.MP4", "in": 7.0, "out": 8.0, "tight": True}]
    got, lines = assist.finish_op_lines(ops, state, clips())
    assert got[0]["out"] == 4.65 and "in" not in got[0]
    assert (got[1]["in"], got[1]["out"]) == (5.75, 9.45)
    assert (got[2]["in"], got[2]["out"]) == (7.0, 8.0) and "tight" not in got[2]
    assert len(lines) == 2
