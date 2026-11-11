"""Wave 24, lane EYES, in the browser: while the AI looks, the prompt line says what it is
looking at, how many frames and what it costs, with a Stop; when it answers, "What it saw"
shows the frames' times. `/api/assist` is faked as in test_aiprompt_ui."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_aiprompt_ui import (INPUT, RESULT, STATUS, _close, _open, browser, live_server)  # noqa: E402,F401

LOOK = {"stage": "cutting", "words": "shots 9-11 (0:38.0–0:43.0): 24 frames", "frames": 24, "cut": 6,
        "sheets": 3, "capped": False, "cap": 45, "scan": False,
        "cost": {"words": "free on your plan", "hover": "~$0.05 if it went through the API — no charge on your plan"}}


def test_the_working_line_says_what_it_looks_at_with_the_frames_and_a_stop(live_server, project, browser):
    pg = _open(browser, live_server, project)
    pg.assist.answer = {"status": "running", "look": LOOK}
    pg.keyboard.press("Control+j")
    pg.keyboard.type("at about 0:40 there is a wobble, lose it")
    pg.keyboard.press("Enter")
    pg.wait_for_selector('[data-testid="ai-look"]', timeout=5000)
    st = pg.locator(STATUS)
    words = st.inner_text()
    assert "Looking at shots 9-11 (0:38.0–0:43.0): 24 frames" in words and "cutting 6/24" in words, words
    assert "free on your plan" in words and "$" not in words
    pg.assist.answer = {"status": "running", "look": {**LOOK, "stage": "reading", "cut": 24}}
    pg.wait_for_function("document.querySelector('[data-testid=ai-look]').textContent.includes('reading them')", timeout=5000)
    assert pg.locator('[data-testid="ai-stop"]').is_visible()
    pg.locator('[data-testid="ai-stop"]').click()
    assert st.get_attribute("data-state") == "stopped" and pg.assist.cancels == 1
    assert pg.errors == []
    _close(pg)


def test_the_result_offers_what_it_saw_with_the_times(live_server, project, browser):
    pg = _open(browser, live_server, project)
    saw = "The frame at 0:40.6 is smeared sideways as he lands; 0:41.0 is steady."
    pg.assist.answer = {
        "status": "done", "kind": "ops", "summary": "Looked at shots 9-11: loses 0.4 s after the jolt at 0:40.6.",
        "ops": [{"op": "razor", "id": "aip0000001", "at": 1.0}],
        "looked": {"words": "shots 9-11 (0:38.0–0:43.0): 24 frames", "where": "shots 9-11", "saw": saw}}
    pg.keyboard.press("Control+j")
    pg.keyboard.type("at about 0:40 there is a wobble, lose it")
    pg.keyboard.press("Enter")
    pg.wait_for_selector(RESULT, timeout=5000)
    pg.locator('[data-testid="ai-saw"]').click()
    pop = pg.locator('[data-testid="ai-answer"]')
    pop.wait_for(state="visible", timeout=3000)
    assert saw in pop.inner_text() and "Looked at shots 9-11" in pop.inner_text()
    assert pg.errors == []
    _close(pg)
