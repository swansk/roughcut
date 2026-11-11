"""The first-pass panel's detail choice, in Chromium (wave 27, GRAIN): one select before Run,
the time follows it before the click, and Run sends it."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from test_firstpass_ui import live, model, page, _wait_state, BTN, PANEL, RUN  # noqa: E402,F401

DETAIL = '[data-testid="firstpass-detail"]'
ETA = '[data-testid="firstpass-eta"]'


def test_the_detail_choice_changes_the_estimate_and_rides_with_run(page, live):
    pg = page
    pg.locator(BTN).click()
    pg.wait_for_selector(PANEL, state="visible")
    sel = pg.locator(DETAIL)
    sel.wait_for(state="visible")
    labels = sel.locator("option").all_inner_texts()
    assert labels == ["Quick: a frame every 4 s, closer where a lot happens",
                      "Every second: the whole of every clip at 1 s",
                      "Every 0.5 s", "Every 0.25 s", "Every 0.1 s"]
    assert sel.input_value() == "4"
    quick = pg.locator(ETA).inner_text()
    assert "frames" not in quick
    sel.select_option("0.1")
    pg.wait_for_function("document.querySelector('[data-testid=firstpass-eta]').innerText.includes('frames')")
    fine = pg.locator(ETA).inner_text()
    assert fine != quick and "sheets" in fine
    assert "every clip, a frame every 0.1 seconds" in pg.locator('[data-testid="firstpass-plain"]').inner_text()
    sent = []
    pg.on("request", lambda r: sent.append(r.post_data) if r.url.endswith("/api/firstpass") and r.method == "POST" else None)
    pg.locator(RUN).click()
    deadline = time.time() + 10
    while not sent and time.time() < deadline:
        time.sleep(0.1)
    assert sent and '"look_interval_s":0.1' in sent[0].replace(" ", ""), sent
    _wait_state(live, ("done", "failed", "stale-after-edits"))
    # remembered for the bin: the select comes back on it
    pg.reload()
    pg.wait_for_function("window.rcFirstPass && rcFirstPass.state()", timeout=20000)
    pg.locator(BTN).click()
    pg.wait_for_selector(DETAIL, state="visible")
    assert pg.locator(DETAIL).input_value() == "0.1"
