"""The first pass's panel never runs off the window (wave 21 FPPANEL, verdict 16 punch 1, 6, 7, 10).

Verdict 16: at 1440x900, after manual edits and 3 new clips, "Re-run first pass" sat at
y 925-953 in a 900 px window inside an unscrollable panel; an ordinary Playwright click
timed out ("outside of the viewport"). The panel now has a `max-height` of the room under
its own top, a scrolling body and a pinned foot (Run / Stop, the price), and the shots a
re-run keeps are a collapsed "show 10 ▸" list.

Each test drives one state the verdict names (never-run, running, done, stale-after-edits
with new clips, proposal-waiting) at 1440x900 and 1920x1080 with the K5 account (both
certainty rows, the 3080 and Claude lines) and presses the foot's button with an ordinary
`click()` (no force, no scrolling first): Run in every state that has one, Stop while it
runs. The words of K5 are checked in test_k5_ui / test_k5_words; here: the 3080 and Claude
lines, the running Index step's models and counts, "No cut yet" gone under a waiting
proposal, and the contradicted chip's hover."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import flow  # noqa: E402
from test_firstpass_ui import RUNNING, BTN, PANEL, RUN, live, model  # noqa: E402,F401
from test_k5_ui import pg, _json, _serve, _panel, _certainty, _account, _shot  # noqa: E402,F401
from k5_fixture import KILLINGTON, RUNS  # noqa: E402

STOP = '[data-testid="firstpass-stop"]'
NAMES = ["CLIP_%02d.MP4" % i for i in range(1, 13)]
LOCAL = {"available": True, "reason": "", "clips": 12, "clips_done": 9, "pending": 3,
         "checked": 120, "fps": 10.0, "embedded": 5400, "embed_fps": 2.0,
         "model": "Qwen3.5-4B nf4", "embed_model": "timm/PE-Core-L-14-336"}
VERIFY = {"checked": 40, "confirmed": 23, "rejected": 12, "unsure": 5, "fps": 8}
KIL = {**KILLINGTON, "mode": "hybrid", "local": LOCAL, "verify": VERIFY,
       "looked": {**KILLINGTON["looked"], "expected_frames": 645}}


def _keep(n=10):
    return [{"id": f"s{i}", "pos": i + 1, "clip": NAMES[i % 12], "name": NAMES[i % 12][:-4],
             "in": 3.0 + i, "out": 9.0 + i, "how": ["trim"], "words": "trimmed"} for i in range(n)]


def _state(kind: str) -> dict:
    base = {**RUNNING, "stage": None, "steps": None, "counts": None, "stale": [], "new_clips": [],
            "new_count": 0, "when": time.time() - 90, "label": "Re-run first pass",
            "proposal": {"job": "ask00001", "pending": False, "answer": "accept", "shots": 19},
            "produced": {"index_ran": False, "picks": 40, "cut": {"shots": 19, "length_s": 60}}}
    w = base["would"]
    if kind == "never-run":
        return {**base, "state": "never-run", "label": "Run first pass", "when": None,
                "proposal": {}, "produced": {}}
    if kind == "running":
        return {**RUNNING, "stage": "index"}
    if kind == "done":
        return {**base, "state": "done", "would": {**w, "keep": _keep(10), "replace": _keep(9),
                                                     "mode": "rerun"}}
    if kind == "stale":
        return {**base, "state": "stale-after-edits",
                "stale": ["10 shots cut by hand since", "3 new clips since the last run"],
                "new_clips": ["CLIP_13.MP4", "CLIP_14.MP4", "CLIP_15.MP4"], "new_count": 3,
                "new_cost": {"words": "free on your plan", "hover": "~$0.10"},
                "would": {**w, "keep": _keep(10), "replace": _keep(9), "mode": "rerun"}}
    if kind == "waiting":
        return {**base, "state": "done",
                "proposal": {"job": "ask00002", "pending": True, "answer": None, "shots": 19,
                             "sentence": "A proposed cut of 19 shots is waiting."},
                "would": {**w, "keep": [], "replace": [], "mode": "run"}}
    raise AssertionError(kind)


def _acct(kind: str) -> dict:
    run = {"never-run": "never-run", "running": "running-index", "done": "done-existing",
           "stale": "done-existing", "waiting": "done-fresh"}[kind]
    acc = flow.pass_account(KIL, RUNS[run])
    acc["certainty"] = _certainty()
    return acc


def _fits(pg, sel):
    r = pg.evaluate("(s) => { const b = document.querySelector(s).getBoundingClientRect();"
                    " return [b.top, b.bottom, innerHeight]; }", sel)
    assert r[0] >= 0 and r[1] <= r[2] + 0.5, (sel, r)


@pytest.mark.parametrize("kind", ["never-run", "running", "done", "stale", "waiting"])
def test_the_foot_button_is_clickable_in_every_state(pg, kind):
    state = _state(kind)
    _serve(pg, state, _acct(kind))
    _panel(pg)
    clicked = []
    pg.route("**/api/firstpass/cancel", lambda r: (clicked.append("stop"), r.fulfill(**_json({"ok": True})))[1])
    pg.route("**/api/firstpass", lambda r: (clicked.append("run"), r.fulfill(**_json({"job": "fp2", "mode": "rerun",
             "keep": [], "would": state["would"]})))[1] if r.request.method == "POST"
             else r.fulfill(**_json(state)))
    # the panel's bottom (so its foot) is inside the window, and so is the button
    _fits(pg, PANEL)
    btn = STOP if kind == "running" else RUN
    _fits(pg, btn)
    pg.locator(btn).click(timeout=3000)               # an ordinary click: no scrolling, no force
    assert clicked == ["stop" if kind == "running" else "run"], clicked
    _shot(pg, f"w21-{kind}")


def test_the_body_scrolls_and_the_foot_stays_when_the_content_is_taller(pg):
    state = _state("stale")
    _serve(pg, state, _acct("stale"))
    _panel(pg)
    pg.locator('[data-testid="firstpass-kept-toggle"]').click()      # 10 kept shots listed
    m = pg.evaluate("""() => { const p = document.querySelector('[data-testid=firstpass-panel]');
      const b = p.querySelector('.fp-body'), f = p.querySelector('.fp-foot');
      const pr = p.getBoundingClientRect();
      return {pb: pr.bottom, vh: innerHeight, sh: b.scrollHeight, ch: b.clientHeight,
              oy: getComputedStyle(b).overflowY, fb: f.getBoundingClientRect().bottom}; }""")
    assert m["pb"] <= m["vh"] and m["fb"] <= m["vh"], m
    assert m["oy"] in ("auto", "scroll"), m
    pg.locator(RUN).click(timeout=3000)


def test_the_kept_shots_are_collapsed_until_asked_for(pg):
    _serve(pg, _state("done"), _acct("done"))
    _panel(pg)
    line = pg.locator('[data-testid="firstpass-keep"]').inner_text()
    assert "Keeps 10 shots you cut by hand" in line and "may replace 9" in line, line
    assert not pg.locator('[data-testid="firstpass-kept"]').is_visible()
    assert pg.locator('[data-testid="firstpass-kept-toggle"]').inner_text().startswith("show 10")
    pg.locator('[data-testid="firstpass-kept-toggle"]').click()
    assert pg.locator('[data-testid="firstpass-kept"] li').count() == 10
    assert pg.locator('[data-testid="firstpass-kept"]').is_visible()


def test_a_waiting_proposal_does_not_say_no_cut_yet(pg):
    _serve(pg, _state("waiting"), _acct("waiting"))
    _panel(pg)
    text = pg.locator(PANEL).inner_text()
    assert "A proposed cut of 19 shots is waiting" in text, text
    assert "No cut yet" not in text, text
    # but a bin that never ran still says it
    _serve(pg, _state("never-run"), _acct("never-run"))
    assert "No cut yet" in pg.locator(PANEL).inner_text()


def test_the_3080_and_claude_lines_say_what_each_checked(pg):
    _serve(pg, _state("done"), _acct("done"))
    _panel(pg)
    local = pg.locator('[data-testid="firstpass-line-local"]').inner_text()
    claude = pg.locator('[data-testid="firstpass-line-claude"]').inner_text()
    assert local == ("3080 (Qwen3.5-4B, PE-Core): checked 120 windows at 10 fps, levelled; "
                     "embedded 5,400 frames at 2 fps for Find · still running on 3 clips"), local
    assert claude == ("Claude checked 40 moments on levelled 8 fps strips: "
                      "23 confirmed · 12 rejected · 5 unsure"), claude


def test_running_names_the_models_and_the_counts_as_they_are_known(pg):
    run = {**RUNNING, "stage": "index"}
    acct = flow.pass_account(
        {**KIL, "looked": {**KIL["looked"], "frames": 312}},
        {"state": "running", "stage": "index",
         "counts": {"clips": 12, "listened": 7, "looked": 4, "look_need": 12,
                    "words": "heard 7/12 · looked at 4/12 · previews 2/12"}})
    acct["certainty"] = _certainty()
    _serve(pg, run, acct)
    _panel(pg)
    ix = pg.locator('[data-step="index"]').inner_text()
    assert "Whisper large-v3 · 7 of 12 heard; Sonnet · 1 frame every 4 s, 320 px, 312 of 645 frames" in ix, ix


def test_the_contradicted_chip_says_what_was_claimed_and_what_the_closer_look_found():
    m = {"what": "Telling the red-helmet friend 'I fell into the stream'", "source": "sheet",
         "status": "contradicted", "kind": "speech", "start": 1.0, "end": 3.0,
         "found": "the closer look found nothing usable (a lens cover, a blur or a black frame)"}
    c = flow.certainty(m)
    assert c["level"] == "contradicted"
    assert "Claimed: “Telling the red-helmet friend 'I fell into the stream'”" in c["title"], c
    assert "The closer look found nothing usable" in c["title"], c
    # a Claude rejection says what Claude saw
    r = flow.certainty({**m, "found": "Claude's check: he is standing, not falling",
                        "verdict": {"verdict": "reject", "by": "claude"}})
    assert "rejected by Claude" in r["title"] and "Claude's check: he is standing" in r["title"], r
    # with nothing recorded it still says both halves
    bare = flow.certainty({"what": "skier jumps", "source": "sheet", "status": "contradicted"})
    assert "Claimed: “skier jumps”" in bare["title"] and "found something else" in bare["title"]
