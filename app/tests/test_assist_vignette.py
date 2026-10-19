"""The AI's vignette is Lumetri's (redesign wave 15, lane VIGNETTE; verdict 11 punch 7,
REDESIGN-GOAL dimension 15).

Verdict 11: asked to "put a soft vignette on this shot", the AI answered `design_effect`
("there is no Vignette control in the tool set") and the designer faked one from five
stacked rings that darkened the centre 12 %. Now the prompt's vocabulary has Lumetri ›
Vignette (the `vignette` op) and says a vignette is that, never a designed effect; this
file checks, with a scripted backend (never a real model call), that the prompt the model
gets says so, and that a vignette answer comes back as one Lumetri op the review shows in
one line. The real model's answer on Killington is the lane's report's (claude_cli).
"""

from __future__ import annotations

from test_assist import Scripted, ask, board  # noqa: F401

from roughcut import actions, inference


def test_the_prompt_says_a_vignette_is_lumetris_vignette(board):
    c, (a, b) = board
    model = Scripted({"kind": "answer", "text": "ok"})
    inference.set_backend(model)
    ask(c, "put a soft vignette on this shot", {"kind": "selection", "ids": [a], "playhead": 0.5})
    p = model.prompts[-1]
    # the vocabulary: the op, its four numbers and what a soft one is
    assert '{"op":"vignette","id","amount","midpoint"?,"roundness"?,"feather"?}' in p
    assert "Lumetri › Vignette, Premiere's" in p
    # how editors say it: a vignette is this op, never a designed effect
    rule = next(line for line in p.splitlines() if line.startswith("- a vignette"))
    assert "→ vignette" in rule
    i = p.index(rule)
    assert "never a design_effect" in p[i:i + 400]
    assert "only what no tool above draws" in p                     # design_effect's own line
    # and Sharpen is in the bin's kinds, with the words that ask for it
    assert "sharpen (Blur & Sharpen › Sharpen: amount 0..500)" in p
    assert "sharper / crisper → effect sharpen" in p
    assert f"The selected shots: shot 1 (CLIP_A) [id {a}]" in p


def test_a_vignette_answer_is_one_lumetri_op_reviewed_in_one_line(board):
    c, (a, b) = board
    inference.set_backend(Scripted({"kind": "ops", "summary": "a soft vignette on shot 1",
                                    "ops": [{"op": "vignette", "id": a, "amount": -1.5,
                                             "midpoint": 45, "feather": 75}]}))
    d = ask(c, "put a soft vignette on this shot", {"kind": "selection", "ids": [a]})
    assert d["status"] == "done" and d["kind"] == "ops"
    assert d["ops"] == [{"op": "vignette", "id": a, "amount": -1.5, "midpoint": 45.0, "feather": 75.0}]
    assert d["lines"] == ["Shot 1 (CLIP_A): Lumetri Vignette — Amount −1.5, Midpoint 45, Feather 75"]
    assert d["families"] == ["lumetri"] and d["refused"] == []
    assert "preview" not in d                                       # nothing on V1's cut moves


def test_the_sequence_tells_the_model_a_shot_has_a_vignette_and_video_effects(board):
    c, (a, b) = board
    proj = c.get("/api/project").json()
    proj["segments"][0]["controls"] = {"vignette_amount": -2, "vignette_feather": 70,
                                       "vfx": [{"id": "sharpen", "kind": "sharpen", "params": {"amount": 60}}]}
    r = c.put("/api/project", json=proj)
    assert r.status_code == 200, r.text
    model = Scripted({"kind": "answer", "text": "ok"})
    inference.set_backend(model)
    ask(c, "what is on shot 1?")
    row = next(line for line in model.prompts[-1].splitlines() if f"  {a}  CLIP_A.MP4" in line)
    assert "vignette_amount -2" in row and "vignette_feather 70" in row and "video effects sharpen" in row, row
    assert actions.describe({"op": "vignette", "id": a, "feather": 50},
                            actions.state_from(c.get("/api/project").json())).startswith("Shot 1 (CLIP_A): Lumetri Vignette")
