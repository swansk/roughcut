"""Wave 23, lane DIRECT: an AI you can direct (WORDS-01, -05, -06, -08, -10, -17, -18; COLD-17;
CLUMSY-12). The prompt box remembers this cut's earlier turns, the planner's brief is
labelled and never cut mid-sentence, a scope chosen on the chip survives playback, and the
AI names only panels that exist. The model is the suite's scripted backend."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import assist, inference, revise  # noqa: E402

from test_assist import Scripted, ask, board, wait  # noqa: E402,F401

ROOT = Path(__file__).resolve().parents[2]


def last_prompt(model) -> str:
    return [p for p, s in zip(model.prompts, model.schemas) if "kind" in s][-1]


# ---------------------------------------------------------------- (a) memory

def test_the_next_ask_carries_the_last_turns(board):
    c, (a, b) = board
    model = Scripted({"kind": "answer", "text": "I can trim the end or dip to black. Say which."})
    inference.set_backend(model)
    ask(c, "end on the funniest thing anyone says")
    ask(c, "ok do both")
    p = last_prompt(model)
    assert "Earlier in this conversation" in p
    assert "end on the funniest thing anyone says" in p
    assert "I can trim the end or dip to black. Say which." in p
    assert p.index("end on the funniest") < p.index("ok do both")


def test_edits_accepted_rejected_and_undone_are_in_the_memory(board):
    c, (a, b) = board
    model = Scripted({"kind": "ops", "summary": "razor shot 1", "ops": [{"op": "razor", "id": a, "at": 1.0}]})
    inference.set_backend(model)
    d = ask(c, "cut shot 1 in half")
    assert c.post(f"/api/assist/{d['job']}/answer", json={"accepted": [0], "rejected": []}).status_code == 200
    assert c.post("/api/assist/event", json={"kind": "undo", "label": "AI edit"}).status_code == 200
    assert c.post("/api/assist/event", json={"kind": "sideways"}).status_code == 400
    inference.set_backend(Scripted({"kind": "answer", "text": "x"}))
    model2 = inference.get_backend() if hasattr(inference, "get_backend") else None
    m = Scripted({"kind": "answer", "text": "x"})
    inference.set_backend(m)
    ask(c, "what did you just change?")
    p = last_prompt(m)
    assert "razor" in p.lower() and "The editor accepted edits [1]" in p
    assert "The editor chose undo: AI edit" in p


def test_memory_is_bounded_newest_first(board):
    c, _ = board
    long = "word " * 150
    m = Scripted({"kind": "answer", "text": long.strip()})
    inference.set_backend(m)
    for i in range(14):
        ask(c, f"question number {i:02d}")
    ask(c, "now")
    p = last_prompt(m)
    block = p[p.index("Earlier in this conversation"):p.index("The editor asked now")]
    assert len(block) < assist.HISTORY_CHARS + 600
    assert "question number 13" in block and "question number 00" not in block


def test_another_cut_or_bin_never_leaks_turns(board, monkeypatch):
    import server
    c, _ = board
    m = Scripted({"kind": "answer", "text": "secret from cut one"})
    inference.set_backend(m)
    ask(c, "private question in cut one")
    monkeypatch.setattr(server, "assist_cut", lambda: "another-cut")
    ask(c, "hello from the other cut")
    assert "private question in cut one" not in last_prompt(m)
    assert server.assist_history("another-cut") == [server.assist_history("another-cut")[0]]
    assert server.assist_history("") == []


# ---------------------------------------------------------------- (b) the plan is not the editor's

def test_a_long_brief_is_whole_and_not_cut_at_2000(board):
    brief = ". ".join(f"Shot {i}: keep the middle of it, about {i} s" for i in range(1, 160)) + "."
    assert len(brief) > 2000
    out = assist.validate_answer({"kind": "plan", "note": brief})
    assert len(out["note"]) > 2000 and out["note"].endswith(".")
    huge = brief * 4
    out = assist.validate_answer({"kind": "plan", "note": huge})
    assert len(out["note"]) <= assist.PLAN_MAX and out["note"].endswith(".")


def test_fit_never_stops_mid_sentence():
    t = "First thing. Second thing is long and goes on. Third thing"
    assert assist.fit(t, 40) == "First thing."
    assert assist.fit("short", 40) == "short"


def test_the_cut_writer_gets_the_plan_labelled_as_the_plan(board):
    c, ids = board
    m = Scripted({"kind": "plan", "summary": "re-cut", "note": "Open on the best skiing. Keep the milk joke."})
    inference.set_backend(m)
    d = ask(c, "Cut this down to about 90 seconds total. Keep the milk joke and the best skiing.")
    assert d["kind"] == "plan" and d["plan_ask"]
    end = __import__("time").time() + 10
    while __import__("time").time() < end:
        a = c.get(f"/api/ask/{d['plan_ask']}").json()
        if a.get("state") in ("done", "failed"):
            break
        __import__("time").sleep(0.05)
    cutp = [p for p, s in zip(m.prompts, m.schemas) if "segments" in s][-1]
    assert "## The editor's note\nCut this down to about 90 seconds total." in cutp
    assert "## The plan written from those words (not the editor's text)\nOpen on the best skiing." in cutp
    assert "never say the plan or the note is cut off" in cutp


def test_note_pair_roundtrips_and_a_plain_note_is_unchanged():
    n = revise.compose_note("make it shorter", "Drop shot 3.")
    assert revise.split_note(n) == ("make it shorter", "Drop shot 3.")
    assert revise.split_note("just this") == ("just this", "")
    assert revise.note_section("just this") == "## The editor's note\njust this"


# ---------------------------------------------------------------- (c) honest about its eyes

def test_an_edit_resting_on_the_picture_says_it_has_not_looked(board):
    c, (a, b) = board
    inference.set_backend(Scripted({"kind": "ops", "summary": "Trims shot 1 so it picks up just after the wobble.",
                                    "ops": [{"op": "razor", "id": a, "at": 1.0}]}))
    d = ask(c, "at about 0:40 there is a wobble, lose it")
    assert d["summary"].startswith("From the written descriptions and the transcript; I have not looked")
    assert d["from_picture"] is True


def test_the_model_flag_alone_also_adds_the_line_and_plain_edits_stay_plain(board):
    c, (a, b) = board
    inference.set_backend(Scripted({"kind": "ops", "summary": "razor", "from_picture": True,
                                    "ops": [{"op": "razor", "id": a, "at": 1.0}]}))
    assert ask(c, "split it somewhere")["summary"].startswith("From the written descriptions")
    inference.set_backend(Scripted({"kind": "ops", "summary": "Cut shot 1 in half.",
                                    "ops": [{"op": "razor", "id": a, "at": 1.0}]}))
    assert ask(c, "cut shot 1 in half")["summary"] == "Cut shot 1 in half."


def test_the_prompt_tells_it_what_it_cannot_see_and_to_push_back(board):
    c, _ = board
    m = Scripted({"kind": "answer", "text": "x"})
    inference.set_backend(m)
    ask(c, "anything")
    p = last_prompt(m)
    assert "You have not looked at" in p and "do not cut on it" in p
    assert "contradicts itself" in p


# ---------------------------------------------------------------- (e) real panels only

def test_the_guide_lists_exactly_the_window_menu():
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    menu = html[html.index('data-menu="window"'):html.index('data-menu="help"')]
    labels = [re.sub(r"<[^>]+>", "", m).strip() for m in re.findall(r"<button[^>]*data-cmd=\"(?:w:\w+|events)\"[^>]*>(.*?)</button>", menu)]
    assert labels == list(assist.WINDOW_PANELS)
    assert "There is no History panel" in assist.APP_GUIDE


def test_the_prompt_carries_the_app_guide(board):
    c, _ = board
    m = Scripted({"kind": "answer", "text": "x"})
    inference.set_backend(m)
    ask(c, "undo everything")
    assert "Edit › Undo (Ctrl+Z)" in last_prompt(m)
