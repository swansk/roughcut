"""Next lets go of a proposal the moment it is answered (wave 18 POLISH12, verdict 14
dimension 9).

The judge's journey step 8 showed "Next: Read the proposal and accept or discard it" beside
the toast "applied 4 of 5 shots — undo with Ctrl+Z": the toast was current, the chip stale.
The server stops naming a proposal once its answer is on disk, and the board asked it again
only after the save, the answer and a 1.2 s debounce (app.js `flowSoon`). Now Accept and
Discard fire `rc:proposal-answered` and the chip drops that proposal at once, asking again
until the server agrees (flow.js `heldBack`).

The answer's POST is held 2.5 s here, so the server keeps naming the proposal that long: the
chip must not. Same fixtures as test_flow_ui (the real server on a real port, the synthetic
bin); no model call (the proposal is an Ask record written to disk).
"""

from __future__ import annotations

import json
import time

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")

from test_flow_ui import browser, live  # noqa: E402,F401

READ = "Read the proposal and accept or discard it"
PLAN = {
    "segments": [
        {"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
        {"clip": "CLIP_B.MP4", "in": 0.0, "out": 1.5, "why": "the reply"},
        {"clip": "CLIP_C.MP4", "in": 1.0, "out": 3.0, "why": "the ride starts"},
    ],
    "notes": "Opens on the hello, then the reply and the ride.",
}
CHIP = "(() => { const a = document.querySelector('#flowNext'); return a ? a.innerText.replace(/\\s+/g, ' ').trim() : ''; })()"


def _waiting(job: str) -> None:
    """An unanswered Ask record on disk, newer than any save: only its answer clears it."""
    import server
    asks = server.STATE["asks"]
    asks.mkdir(parents=True, exist_ok=True)
    for old in asks.glob("*.json"):
        old.unlink()
    (asks / f"{job}.json").write_text(json.dumps({
        "job": job, "created": time.time() + 3600, "note": "", "story": "", "plan": PLAN}),
        encoding="utf-8")


@pytest.mark.parametrize("button", ["#acceptProposal", "#rejectProposal"], ids=["accept", "discard"])
def test_next_lets_go_of_a_proposal_the_moment_it_is_answered(browser, live, button):
    import httpx
    _waiting(f"held-{button[1:7]}")
    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    errors: list = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    held: list = []                      # the answer's request, let go 2.5 s on

    pg.route("**/api/asks/answer", lambda route: held.append(route))
    pg.goto(live)
    pg.wait_for_selector("#proposal", state="visible", timeout=20000)
    pg.wait_for_function(f"{CHIP}.includes('{READ}')", timeout=15000)
    pg.wait_for_timeout(300)

    pg.locator(button).click()
    t0 = time.time()
    pg.wait_for_timeout(100)
    assert READ not in pg.evaluate(CHIP), "the chip still names the answered proposal"
    # while the answer is held the server still names it; the chip does not, at any poll
    seen = []
    while time.time() - t0 < 2.5:
        seen.append(pg.evaluate(CHIP))
        pg.wait_for_timeout(100)
    assert held, "the answer was sent"
    assert not any(READ in s for s in seen), seen
    assert ((httpx.get(live + "/api/flow", timeout=10).json().get("next") or {}).get("target") or {}).get("ask"), \
        "the server still names the proposal while its answer is held"
    held[0].continue_()
    # the answer lands; the server agrees, and the chip shows its Next again
    deadline = time.time() + 10
    while time.time() < deadline:
        f = httpx.get(live + "/api/flow", timeout=10).json()
        if not ((f.get("next") or {}).get("target") or {}).get("ask"):
            break
        time.sleep(0.2)
    assert not ((f.get("next") or {}).get("target") or {}).get("ask"), f.get("next")
    want = (f.get("next") or {}).get("sentence") or ""
    if want and not pg.evaluate("window.flowBar.yielded()"):
        pg.wait_for_function(f"{CHIP}.includes({json.dumps(want)})", timeout=8000)
    assert READ not in pg.evaluate(CHIP)
    assert errors == []
    pg.unroute_all(behavior="ignoreErrors")
    pg.close()
