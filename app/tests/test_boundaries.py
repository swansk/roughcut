"""Boundary polish — the two halves of Karl's note on clip length.

> *"Dual issue on clip length, both with similar frequency: (A) clip is too long
> and we trail off on conversation; (B) clip is too short and you clip words, like
> POCKET PI[CLIP] — should finish PIZZA."*

The four things that must hold, and one that must **not**:

  * an out-point that cuts a word finishes it (B)
  * an out-point stranded seconds past the last word loses the dead air (A)
  * a shot with no speech in it is left exactly as chosen — it was picked for its
    picture, and a speech rule has no opinion about it
  * no boundary is ever moved into a stretch the visual pass called unusable
  * and nothing here reaches for the *next* utterance. That is edl_snap's closure
    pass, Karl preferred the un-snapped cut for its boundaries, and re-growing
    shots by whole lines is the behaviour this must not re-introduce.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import boundaries  # noqa: E402

# Word ends (`e`) as the current audio pass writes them.
TRANSCRIPT = [
    {"start": 0.5, "end": 2.0, "text": "hello there", "no_speech_prob": 0.1,
     "words": [{"w": "hello", "t": 0.5, "e": 1.1, "p": 0.9},
               {"w": "there", "t": 1.2, "e": 2.0, "p": 0.9}]},
    {"start": 2.4, "end": 4.0, "text": "how are you", "no_speech_prob": 0.1,
     "words": [{"w": "how", "t": 2.4, "e": 2.9, "p": 0.9},
               {"w": "are", "t": 3.0, "e": 3.3, "p": 0.9},
               {"w": "you", "t": 3.5, "e": 4.0, "p": 0.9}]},
]


def clip(**over) -> dict:
    base = {"clip": "A.MP4", "duration": 30.0, "transcript": TRANSCRIPT,
            "visual": {}}
    base.update(over)
    return base


def one(seg: dict, c: dict | None = None, **kw) -> dict:
    return boundaries.polish_segment(seg, c or clip(), **kw)[0]


# ------------------------------------------------------------------ B: clipped

def test_an_out_point_inside_a_word_finishes_the_word():
    out = one({"clip": "A.MP4", "in": 2.4, "out": 3.2, "why": "x"})
    assert out["out"] == round(3.3 + boundaries.PAD_TAIL, 2) == 3.75
    assert out["polished_from"] == [2.4, 3.2]
    assert "finish" in out["polish_why"] and "are" in out["polish_why"]
    assert out["in"] == 2.4, "the in-point was already clean"


def test_pocket_pizza_the_line_that_prompted_this():
    """CLIP_03 18.4-20.9 in the Killington revision proposal, with the timings the
    audio pass now records: "pizza!" runs 20.12-20.78, and the proposal cut at
    20.90 — twelve hundredths past the decoded end, while the word is still
    sounding. Karl heard "POCKET PI"."""
    pizza = {"clip": "CLIP_03.MP4", "duration": 40.3, "visual": {}, "transcript": [
        {"start": 18.34, "end": 19.80, "text": "Yo bro, you got the pocket pizza?",
         "words": [{"w": "Yo", "t": 18.34, "e": 18.74},
                   {"w": "pizza?", "t": 19.60, "e": 19.80}]},
        {"start": 19.80, "end": 20.78, "text": "Pocket pizza!",
         "words": [{"w": "Pocket", "t": 19.80, "e": 20.12},
                   {"w": "pizza!", "t": 20.12, "e": 20.78}]}]}
    out = one({"clip": "CLIP_03.MP4", "in": 18.40, "out": 20.90}, pizza)
    assert out["out"] == 21.23
    assert out["in"] == 18.09, "and it opened mid-'Yo', so that moves back too"
    assert out["polished_from"] == [18.4, 20.9]


def test_an_out_point_a_hair_past_a_word_still_reads_as_cutting_it():
    """0.05s after the decoded end is inside the sound of the word. The measured
    decay on this footage is a median 0.12s and up to 0.26s past `e`."""
    out = one({"clip": "A.MP4", "in": 2.4, "out": 4.05})
    assert out["out"] == 4.45 and out["polished_from"] == [2.4, 4.05]


def test_a_clean_gap_after_a_line_is_left_alone():
    """Half a second past the last word is already a clean cut; polish is not an
    excuse to lengthen every shot."""
    out = one({"clip": "A.MP4", "in": 2.4, "out": 4.6})
    assert "polished_from" not in out and out["out"] == 4.6


def test_an_in_point_inside_a_word_moves_back_to_its_start():
    out = one({"clip": "A.MP4", "in": 1.5, "out": 2.9})
    assert out["in"] == round(1.2 - boundaries.PAD_HEAD, 2) == 0.95
    assert "start of" in out["polish_why"]


# ------------------------------------------------------------------ A: trailing

def test_a_shot_that_runs_on_after_the_talking_loses_the_dead_air():
    out = one({"clip": "A.MP4", "in": 2.4, "out": 9.0, "why": "the reply"})
    assert out["out"] == 4.45
    assert out["polished_from"] == [2.4, 9.0]
    assert "dead air" in out["polish_why"] and "5.0s" in out["polish_why"]
    assert out["why"] == "the reply", "the reason for the shot is untouched"


def test_trailing_over_something_worth_watching_is_kept():
    """Held dead air is not always dead: the CLIP_04 crash and the CLIP_01 river
    fall both sit seconds after the last word, and trimming to the words would
    cut the event out of the film."""
    held = clip(visual={"moments": [
        {"start": 4.0, "end": 9.0, "kind": "crash", "notable": True,
         "what": "rider goes down hard"}]})
    out = one({"clip": "A.MP4", "in": 2.4, "out": 9.0}, held)
    assert "polished_from" not in out and out["out"] == 9.0


def test_trailing_over_mere_scenery_is_still_trimmed():
    """`notable` is the discrimination. Visual moments cover 34-92% of every clip
    in this bin, so "overlaps any moment" would veto every trim there is."""
    scenic = clip(visual={"moments": [
        {"start": 4.0, "end": 9.0, "kind": "scenery", "notable": False,
         "what": "wide snowy slope with bare trees"}]})
    assert one({"clip": "A.MP4", "in": 2.4, "out": 9.0}, scenic)["out"] == 4.45


# ------------------------------------------------------- shots with no speech

def test_a_wordless_shot_is_left_exactly_as_chosen():
    """A held landing or a lift-line beat was chosen for its picture. There is no
    last word to trim to and nothing to finish, and guessing at its length is how
    an editor stops trusting the tool."""
    silent = clip(transcript=[])
    seg = {"clip": "A.MP4", "in": 10.0, "out": 22.0, "why": "the landing"}
    assert one(seg, silent) == seg


def test_a_shot_past_the_end_of_the_talking_is_wordless_too():
    """Same rule when the clip has a transcript but this shot is not in it."""
    seg = {"clip": "A.MP4", "in": 10.0, "out": 22.0}
    assert one(seg) == seg


# --------------------------------------------------------- unusable stretches

def test_a_boundary_never_moves_into_an_unusable_stretch():
    """The visual pass is the only account of where the picture is black, blurred
    or pointed at a glove. Trading a clipped word for four unwatchable frames is
    not a trade."""
    blocked = clip(visual={"unusable": [
        {"start": 4.2, "end": 6.0, "why": "nearly black - lens obstruction"}]})
    out = one({"clip": "A.MP4", "in": 2.4, "out": 4.05}, blocked)
    assert "polished_from" not in out and out["out"] == 4.05


def test_a_trim_never_lands_inside_an_unusable_stretch():
    blocked = clip(visual={"unusable": [{"start": 4.3, "end": 5.0, "why": "white"}]})
    out = one({"clip": "A.MP4", "in": 2.4, "out": 9.0}, blocked)
    assert "polished_from" not in out and out["out"] == 9.0


def test_a_head_move_never_crosses_an_unusable_stretch():
    blocked = clip(visual={"unusable": [{"start": 1.0, "end": 1.4, "why": "black"}]})
    out = one({"clip": "A.MP4", "in": 1.5, "out": 2.9}, blocked)
    assert out["in"] == 1.5


# ------------------------------------------------------------------- restraint

def test_polish_never_absorbs_the_next_line():
    """The behaviour Karl rejected. edl_snap's closure pass reaches for the reply,
    and the reply to the reply; an out-point that finishes "there" must stop at
    "there" and not swallow "how are you"."""
    out = one({"clip": "A.MP4", "in": 0.5, "out": 1.9})
    assert out["out"] == 2.45 < TRANSCRIPT[1]["start"] + 1.0
    assert out["out"] - 1.9 <= boundaries.MAX_EXTEND_S


def test_two_shots_from_one_clip_never_grow_into_each_other():
    """Back-to-back shots off the same clip share a boundary. Extending the first
    would play half a second of the same audio twice."""
    segs = [{"clip": "A.MP4", "in": 0.5, "out": 2.0},
            {"clip": "A.MP4", "in": 2.0, "out": 4.0}]
    got = boundaries.polish(segs, {"A.MP4": clip()})
    assert got[0]["out"] == 2.0 and "polished_from" not in got[0]
    assert got[1]["out"] == 4.45, "the last one still has room"


def test_an_extension_stops_at_the_end_of_the_clip():
    short = clip(duration=4.2)
    assert one({"clip": "A.MP4", "in": 2.4, "out": 4.0}, short)["out"] == 4.2


# ------------------------------------------------- old sidecars and whole plans

def test_word_ends_are_inferred_when_the_sidecar_has_only_starts():
    """Sidecars written before the audio pass recorded `e` — and the suite's own
    fixture — carry word starts only."""
    old = clip(transcript=[
        {"start": 0.5, "end": 2.0, "text": "hello there",
         "words": [{"w": "hello", "t": 0.5, "p": 0.9},
                   {"w": "there", "t": 1.2, "p": 0.9}]}])
    out = one({"clip": "A.MP4", "in": 0.5, "out": 1.9}, old)
    assert out["out"] == 2.45, "last word falls back to the utterance end"
    assert one({"clip": "A.MP4", "in": 0.8, "out": 1.9}, old)["in"] == 0.25


def test_polish_plan_declares_itself_in_the_notes_and_mutates_nothing():
    segs = [{"clip": "A.MP4", "in": 2.4, "out": 3.2},
            {"clip": "A.MP4", "in": 10.0, "out": 22.0}]
    plan = {"segments": segs, "notes": "built around the greeting"}
    got = boundaries.polish_plan(plan, {"A.MP4": clip()})
    assert got["segments"][0]["out"] == 3.75
    assert plan["segments"][0]["out"] == 3.2, "input plan untouched"
    assert plan["notes"] in got["notes"]
    assert "adjusted 1 of 2 shots" in got["notes"]


def test_a_plan_nothing_moved_in_says_nothing():
    plan = {"segments": [{"clip": "A.MP4", "in": 10.0, "out": 22.0}], "notes": "n"}
    assert boundaries.polish_plan(plan, {"A.MP4": clip()})["notes"] == "n"


def test_a_segment_from_a_clip_we_have_no_sidecar_for_is_passed_through():
    segs = [{"clip": "GHOST.MP4", "in": 1.0, "out": 2.0}]
    assert boundaries.polish(segs, {"A.MP4": clip()}) == segs
