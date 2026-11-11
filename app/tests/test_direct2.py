"""Wave 25, lane DIRECT2: the director's second round. The AI owns a complete history of the
changes made on this cut, can take the cut back N of them, looks when the words name something
to be seen, sets the project's length from the editor's number, cuts on a word, and the small
honesty fixes. The model is the suite's scripted backend."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import actions, assist, inference, revise  # noqa: E402

from test_assist import Scripted, ask, board, wait  # noqa: E402,F401
from test_eyes import Eyes, eyes, assist_calls  # noqa: E402,F401


def last_prompt(m) -> str:
    return [p for p, s in zip(m.prompts, m.schemas) if "kind" in s][-1]


def accepted_edit(c, ids, text="trim it", n=1):
    a = ids[0]
    inference.set_backend(Scripted({"kind": "ops", "summary": f"razor {text}", "ops": [{"op": "razor", "id": a, "at": 1.0}]}))
    d = ask(c, text)
    assert c.post(f"/api/assist/{d['job']}/answer", json={"accepted": [0], "rejected": []}).status_code == 200
    return d


# ---------------------------------------------------------------- (a) the complete history

def test_the_ledger_counts_every_accepted_change_even_when_the_conversation_is_cut(board):
    c, ids = board
    for i in range(12):
        accepted_edit(c, ids, text=f"change number {i:02d} " + "pad " * 120)
    m = Scripted({"kind": "answer", "text": "x"})
    inference.set_backend(m)
    ask(c, "what did you change?")
    p = last_prompt(m)
    block = p[p.index("## Every change accepted"):p.index("## Earlier in this conversation")]
    assert block.count("— in") == 12 and "12. [" in block
    assert "My record of what we said goes back to" in p and "the list of changes above is complete" in p


def test_no_horizon_line_when_the_conversation_is_whole(board):
    c, ids = board
    accepted_edit(c, ids)
    m = Scripted({"kind": "answer", "text": "x"})
    inference.set_backend(m)
    ask(c, "what did you change?")
    assert "goes back to" not in last_prompt(m)


def test_undo_and_redo_move_the_ledger_like_the_board_does(board):
    import server
    c, ids = board
    accepted_edit(c, ids, "first")
    accepted_edit(c, ids, "second")
    cut = server.assist_cut()
    assert [e["status"] for e in server.assist_ledger(cut)] == ["in", "in"]
    c.post("/api/assist/event", json={"kind": "undo", "label": "AI: razor"})
    c.post("/api/assist/event", json={"kind": "undo", "label": "Add marker"})        # by hand: not the AI's
    assert [e["status"] for e in server.assist_ledger(cut)] == ["in", "undone"]
    c.post("/api/assist/event", json={"kind": "redo", "label": "AI: razor"})
    assert [e["status"] for e in server.assist_ledger(cut)] == ["in", "in"]


def test_another_cut_has_its_own_ledger(board, monkeypatch):
    import server
    c, ids = board
    accepted_edit(c, ids)
    assert len(server.assist_ledger(server.assist_cut())) == 1
    assert server.assist_ledger("another-cut") == [] and server.assist_ledger("") == []


# ---------------------------------------------------------------- (b) put it back

def test_take_back_is_an_op_checked_against_the_ledger(board):
    import server
    c, ids = board
    for t in ("one", "two", "three"):
        accepted_edit(c, ids, t)
    st = server.assist_state()
    assert [x["what"] for x in st["changes"]] == ["razor three", "razor two", "razor one"]      # newest first
    ok = actions.check([{"op": "take_back", "n": 2}], st)
    assert ok["ops"] == [{"op": "take_back", "n": 2}] and "razor three; razor two" in ok["lines"][0]
    assert ok["families"] == ["history"] and "preview" not in ok
    bad = actions.check([{"op": "take_back", "n": 9}], st)
    assert not bad["ops"] and "only 3 accepted changes" in bad["refused"][0]["why"]
    none = actions.check([{"op": "take_back", "n": 1}], {"segments": []})
    assert "no accepted AI change" in none["refused"][0]["why"]


def test_take_back_goes_alone_and_is_in_the_prompt_vocabulary(board):
    import server
    c, ids = board
    accepted_edit(c, ids)
    out = actions.check([{"op": "take_back", "n": 1}, {"op": "razor", "id": ids[1], "at": 1.0}], server.assist_state())
    assert [o["op"] for o in out["ops"]] == ["take_back"] and out["refused"][0]["op"] == "razor"
    assert "take_back" in actions.schema_text()


def test_accepting_take_back_marks_the_changes_taken_back_and_undoing_it_restores_them(board):
    import server
    c, ids = board
    accepted_edit(c, ids, "one")
    accepted_edit(c, ids, "two")
    inference.set_backend(Scripted({"kind": "ops", "summary": "Back two.", "ops": [{"op": "take_back", "n": 2}]}))
    d = ask(c, "put it back the way it was")
    assert d["kind"] == "ops" and d["ops"] == [{"op": "take_back", "n": 2}]
    c.post(f"/api/assist/{d['job']}/answer", json={"accepted": [0], "rejected": []})
    cut = server.assist_cut()
    assert [e["status"] for e in server.assist_ledger(cut)][:2] == ["taken back", "taken back"]
    c.post("/api/assist/event", json={"kind": "undo", "label": "AI: change taken back"})
    assert [e["status"] for e in server.assist_ledger(cut)] == ["in", "in", "undone"]


# ---------------------------------------------------------------- (c) it looks when the words name something seen

def test_the_gate_lets_a_short_answer_to_its_own_picture_offer_look():
    offer = ["[11:20] The AI answered: Option B opens on the helmet cam; I would start after the sleeve in the frames."]
    assert assist.look_allowed("the second one", offer) is True
    assert assist.look_allowed("the second one", ["[11:20] The editor asked: “swap the shots”"]) is False
    assert assist.look_allowed("the second one", None) is False
    assert assist.look_allowed("swap the last two shots") is False
    assert assist.look_allowed("a dissolve between the lift scene and the next run") is True


def test_naming_a_scene_is_recognised_and_a_spoken_line_is_not():
    f = assist.names_something_seen
    assert f("a dissolve between the lift scene and the next run") and f("cut where he waves")
    assert not f("cut the part where he says it's over") and not f("swap the first two shots")


def test_an_edit_on_a_named_scene_looks_first_instead_of_guessing_from_the_playhead(eyes):
    c, (a, b) = eyes
    m = Eyes(request={"kind": "look", "looks": [{"shots": [1, 2], "reason": "the lift scene", "density": "medium"}]},
             then={"kind": "ops", "summary": "Dissolve.", "saw": "the lift at 0:02.1",
                   "ops": [{"op": "transition", "a": a, "b": b, "kind": "cross_dissolve"}]})
    first = {"kind": "ops", "summary": "Dissolve on the playhead's shot.",
             "ops": [{"op": "transition", "a": a, "b": b, "kind": "cross_dissolve"}]}
    calls = {"n": 0}
    orig = m._pick

    def pick(prompt):
        if "## What you are now looking at" not in prompt and "The editor named something" not in prompt:
            calls["n"] += 1
            return first                       # the model's first instinct: skip the look
        return orig(prompt)
    m.answer = pick
    inference.set_backend(m)
    d = ask(c, "a dissolve between the lift scene and the next run")
    assert d["status"] == "done" and d.get("looked"), d
    assert d["summary"].startswith("Looked at") and "have not looked" not in d["summary"]


def test_it_never_says_both_that_it_has_not_looked_and_that_it_looked():
    out = assist.honest_summary({"summary": "From the frames I looked at earlier, shot 9 is steady."},
                                "lose the wobble", True, None)
    assert not out["summary"].startswith("From the written descriptions")


# ---------------------------------------------------------------- (d) the editor's length is the project's target

def test_a_length_the_editor_names_for_the_film():
    f = assist.length_asked
    assert f("make it about 75 seconds") == 75 and f("make it 2 minutes") == 120 and f("cut to 1:30") == 90
    assert f("make a 20 second vertical version for Instagram") is None
    assert f("trim shot 3 to 5 seconds") is None and f("make it better") is None


def test_a_whole_cut_ask_with_a_length_sets_the_target(board):
    import server
    c, ids = board
    inference.set_backend(Scripted({"kind": "plan", "summary": "re-cut", "note": "Shorter."}))
    d = ask(c, "make it about 75 seconds")
    assert d["kind"] == "plan" and d["target"]["target_want_s"] == 75.0
    assert server.read_edl()["target_want_s"] == 75.0


def test_the_notes_say_the_headers_length():
    notes = "The cut is now about 74.7s, down from 95.6s. It is 13 s over your target.\n\nBoundary polish adjusted 5 shots; 74.7s → 78.4s."
    out = revise.true_length_words(notes, 78.4)
    assert "about 78.4 s, down from 95.6s" in out and "13 s over" in out and out.endswith("74.7s → 78.4s.")


# ---------------------------------------------------------------- (e) cut on a word

WORDS = [{"start": 10.0, "end": 14.0, "text": "and so it's over now",
          "words": [{"w": "and", "t": 10.0, "e": 10.2}, {"w": "so", "t": 10.2, "e": 10.5}, {"w": "it's", "t": 12.4, "e": 12.9},
                    {"w": "over", "t": 12.9, "e": 13.68}, {"w": "now", "t": 13.7, "e": 14.0}]}]


def test_the_word_timings_of_the_named_words_reach_the_prompt_bounded():
    st = {"segments": [{"id": "g1", "clip": "C.MP4", "in": 0.0, "out": 20.0}], "fps": 24.0}
    clips = {"C.MP4": {"transcript": WORDS}}
    blk = assist.word_timings_block("cut the last shot straight out on it's over", {"kind": "sequence"}, st, clips)
    assert "over 12.90-13.68" in blk and "Never say you have no word timings" in blk
    assert assist.word_timings_block("make it warmer", {"kind": "sequence"}, st, clips) == ""
    sel = assist.word_timings_block("trim it", {"kind": "selection", "ids": ["g1"]}, st, clips)
    assert "and 10.00-10.20" in sel
    many = {"C.MP4": {"transcript": [{"words": [{"w": f"w{i}", "t": i * 0.1, "e": i * 0.1 + 0.05} for i in range(900)]}]}}
    big = assist.word_timings_block("x", {"kind": "selection", "ids": ["g1"]}, st, many)
    assert len(big) <= assist.WORDS_BLOCK_MAX + 400 and big.count("|") <= assist.WORDS_PER_SHOT


def test_real_transcripts_carry_word_timings(project):
    import json
    words = 0
    for f in Path(project["sidecars"]).glob("*.audio.json"):
        for u in json.loads(f.read_text()).get("transcript") or []:
            words += len(u.get("words") or [])
    assert words > 0


# ---------------------------------------------------------------- (g) small

def test_recent_proposals_show_the_editors_words_and_the_prompt_boxs_edits(board):
    import server
    c, ids = board
    accepted_edit(c, ids, "trim the third shot")
    rows = c.get("/api/asks/recent").json()["proposals"]
    assert rows and rows[0]["note"] == "trim the third shot" and rows[0]["edits"] == 1
    assert rows[0]["status"] == "accepted" and rows[0]["reopen"] is True      # wave 23 SAFE: set-aside edits come back from the list
    assert "⟦" not in server.json.dumps(rows)


def test_a_look_that_confirmed_a_shot_updates_its_certainty_chip(board):
    import server
    c, ids = board
    server.look_confirm_store([{"clip": "CLIP_A.MP4", "in": 0.0, "out": 4.0, "what": "a skier in shorts among pines"}])
    assert server.look_confirmed_for("CLIP_A.MP4", 1.0, 2.0)["what"].startswith("a skier")
    assert server.look_confirmed_for("CLIP_A.MP4", 9.0, 10.0) is None and server.look_confirmed_for("CLIP_B.MP4", 1, 2) is None
    segs = [{"id": "g1", "clip": "CLIP_A.MP4", "in": 0.0, "out": 4.0}, {"id": "g2", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0}]
    assert assist.clean_confirmed([{"shot": 1, "what": "x"}, {"shot": 7, "what": "y"}, {"shot": 2}], segs) == [
        {"clip": "CLIP_A.MP4", "in": 0.0, "out": 4.0, "what": "x"}]
