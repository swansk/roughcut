"""Who said what — alignment of diarization turns to Whisper words, the wearer pick, and
where the labels show (the planner's transcript lines, Find's lexical rows).

Synthetic data throughout: the diarizer itself is a GPU tool in its own environment
(`research/tools/diarize.py`) and is exercised on the Killington footage, not here.
"""

from __future__ import annotations

import json
import stat

from roughcut import find, revise, speakers


def _words(start: float, text: str, step: float = 0.3) -> list[dict]:
    out = []
    for i, w in enumerate(text.split()):
        out.append({"w": w, "t": round(start + i * step, 2), "e": round(start + i * step + 0.25, 2),
                    "p": 0.9})
    return out


def _utt(start: float, text: str) -> dict:
    w = _words(start, text)
    return {"start": w[0]["t"], "end": w[-1]["e"], "text": text, "no_speech_prob": 0.1, "words": w}


def _sidecar(level_a: float = -20.0, level_b: float = -38.0) -> dict:
    # 10 Hz speech-band track: the first 10 s loud (wearer), 10-20 s quiet (the other voice)
    sp = [level_a] * 100 + [level_b] * 100
    return {"clip": "X.MP4", "frame_hz": 10, "duration_s": 20.0,
            "tracks": {"speech_band_db": sp, "wind_dominant": [0] * 200},
            "transcript": [_utt(1.0, "is there ski patrol over here"),
                           _utt(12.0, "yo be careful there is ski patrol"),
                           _utt(15.0, "thanks man")]}


def _diar() -> dict:
    return {"model": "m", "device": "cuda", "gpu_s": 1.0, "peak_vram_mb": 900,
            "turns": [], "exclusive": [
                {"start": 0.8, "end": 3.2, "speaker": "SPEAKER_01"},
                {"start": 11.9, "end": 14.0, "speaker": "SPEAKER_00"},
                {"start": 14.9, "end": 16.0, "speaker": "SPEAKER_01"}]}


def test_labels_in_order_of_first_speech_and_utterances_get_a_speaker():
    sc = speakers.apply(_sidecar(), _diar())
    t = sc["transcript"]
    # SPEAKER_01 spoke first -> S1, whatever pyannote called it
    assert [u["speaker"] for u in t] == ["S1", "S2", "S1"]
    assert all(w["s"] == "S1" for w in t[0]["words"])


def test_wearer_is_the_loudest_speaker_not_the_first():
    # the first voice here is the quiet one: first-to-speak must not decide
    sc = _sidecar(level_a=-40.0, level_b=-18.0)
    sc = speakers.apply(sc, _diar())
    by = {s["id"]: s for s in sc["speakers"]}
    assert by["S2"]["wearer"] is True and by["S1"]["wearer"] is False
    assert [u["wearer"] for u in sc["transcript"]] == [False, True, False]
    assert by["S2"]["wearer_margin_db"] > 10


def test_summary_has_talk_time_and_loudness():
    sc = speakers.apply(_sidecar(), _diar())
    s1, s2 = sc["speakers"]
    assert s1["id"] == "S1" and s1["loudness_db"] == -20.0 and s2["loudness_db"] == -38.0
    assert s1["wearer"] and not s2["wearer"]
    assert s1["talk_s"] > s2["talk_s"] > 0


def test_wind_frames_are_masked_out_of_the_loudness():
    sc = _sidecar()
    # a gust over the quiet voice's words would otherwise make it the loudest
    sc["tracks"]["speech_band_db"] = [-20.0] * 100 + [-38.0] * 100
    sc["tracks"]["speech_band_db"][120:131] = [-5.0] * 11
    sc["tracks"]["wind_dominant"][120:131] = [1] * 11
    out = speakers.apply(sc, _diar())
    assert [s["wearer"] for s in out["speakers"]] == [True, False]


def test_lone_speaker_is_the_wearer_and_short_talkers_cannot_win():
    sc = _sidecar(level_b=-30.0)
    one = {"exclusive": [{"start": 0.0, "end": 20.0, "speaker": "A"}]}
    out = speakers.apply(sc, one)
    assert [s["wearer"] for s in out["speakers"]] == [True]
    # a stranger who shouts one word (< MIN_TALK_S) loses to the person actually talking
    sc = _sidecar()
    sc["tracks"]["speech_band_db"] = [-20.0] * 100 + [-10.0] * 100
    sc["transcript"][2] = _utt(18.0, "hey")
    d = {"exclusive": [{"start": 0.8, "end": 3.2, "speaker": "A"},
                       {"start": 11.9, "end": 14.0, "speaker": "A"},
                       {"start": 17.9, "end": 18.3, "speaker": "B"}]}
    out = speakers.apply(sc, d)
    assert {s["id"]: s["wearer"] for s in out["speakers"]} == {"S1": True, "S2": False}


def test_a_word_between_turns_takes_the_nearest_or_its_neighbours_speaker():
    sc = _sidecar()
    d = {"exclusive": [{"start": 1.0, "end": 1.9, "speaker": "A"}]}   # covers 3 of 6 words
    out = speakers.apply(sc, d)
    assert {w["s"] for w in out["transcript"][0]["words"]} == {"S1"}


def test_a_line_split_between_voices_is_marked_mixed():
    sc = {"frame_hz": 10, "tracks": {}, "transcript": [_utt(0.0, "one two three four")]}
    d = {"exclusive": [{"start": 0.0, "end": 0.7, "speaker": "A"},
                       {"start": 0.7, "end": 2.0, "speaker": "B"}]}
    out = speakers.apply(sc, d)
    assert out["transcript"][0].get("mixed") is True


def test_apply_is_idempotent():
    a = speakers.apply(_sidecar(), _diar())
    b = speakers.apply(json.loads(json.dumps(a)), _diar())
    assert a == b


def test_planner_transcript_shows_who_spoke():
    sc = speakers.apply(_sidecar(), _diar())
    block = revise._clip_block({"clip": "X.MP4", "duration": 20.0, "transcript": sc["transcript"],
                                "summary": {}, "visual": {}})
    assert "wearer: is there ski patrol over here" in block
    assert "S2: yo be careful there is ski patrol" in block
    plain = revise._clip_block({"clip": "X.MP4", "duration": 20.0, "summary": {}, "visual": {},
                                "transcript": [_utt(1.0, "hello there")]})
    assert "wearer:" not in plain and "S1:" not in plain and "hello there" in plain


def test_find_says_who_said_it():
    sc = speakers.apply(_sidecar(), _diar())
    clips = {"X.MP4": {"clip": "X.MP4", "duration": 20.0, "transcript": sc["transcript"],
                       "summary": {}, "visual": {}}}
    rows = find.lexical("ski patrol", clips)
    by_start = {round(r["start"]): r for r in rows}
    assert any(r["why"] == "said here by the wearer" for r in rows)
    assert any(r["why"] == "said here by someone else" for r in rows)
    assert by_start and all(r["speaker"] in ("S1", "S2") for r in rows)
    # an undiarized clip reads as before
    plain = find.lexical("goodbye", {"Y.MP4": {"clip": "Y.MP4", "duration": 6.0, "summary": {},
                                               "visual": {}, "transcript": [_utt(5.0, "goodbye")]}})
    assert plain[0]["why"] == "said here"


def test_a_bystander_burst_louder_than_the_wearers_monologue_does_not_win():
    sc = {"frame_hz": 10, "tracks": {"speech_band_db": [-26.0] * 200 + [-20.0] * 100,
                                     "wind_dominant": [0] * 300},
          "transcript": [_utt(1.0, " ".join(["word"] * 40)), _utt(25.0, "hey look out there")]}
    d = {"exclusive": [{"start": 0.9, "end": 13.5, "speaker": "A"},
                       {"start": 24.9, "end": 26.5, "speaker": "B"}]}
    out = speakers.apply(sc, d)
    by = {s["id"]: s for s in out["speakers"]}
    assert by["S2"]["loudness_db"] > by["S1"]["loudness_db"]
    assert by["S1"]["wearer"] and not by["S2"]["wearer"]


def test_a_lone_voice_far_below_the_wearers_level_is_not_the_wearer():
    sc = _sidecar(level_a=-42.0, level_b=-42.0)
    out = speakers.apply(sc, {"exclusive": [{"start": 0.0, "end": 20.0, "speaker": "A"}]})
    assert [s["wearer"] for s in out["speakers"]] == [False]
    assert all(u["wearer"] is False for u in out["transcript"])
