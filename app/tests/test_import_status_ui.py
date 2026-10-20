"""After an import the board asks again while the answer says `importing` (wave 18 POLISH12,
verdict 14 punch 10).

While an import settles the server answers the first pass's status at once with what it knew
before the import, `"importing": true` in it, rather than wait on the rebuild (the judge's
526 ms /api/firstpass; test_import_status). The board's one read after an import (import.js
`landed`, 300 ms on) would then hold the old answer until the next 5 s poll, and the "2 new
clips: add them to the cut?" offer with it. So `landed` asks again every 600 ms while the
answer says `importing`: the offer shows as soon as the rebuild lands.

Driven in a real browser on test_import_ui's board (a real loop, the index stubbed, the model
scripted); the firstpass snapshot's rebuild is made slow (1.2 s) once the pass is done.
"""

from __future__ import annotations

import json
import threading
import time

import httpx
import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")

from test_import_ui import (DIALOG, PATH, ROW, _wait_state, cards, live, model,  # noqa: E402,F401
                            page)


def test_after_an_import_the_board_asks_again_until_the_status_knows_the_new_clips(
        page, live, cards, monkeypatch):
    import server
    pg, base = page, live["base"]
    pg.locator('[data-testid="firstpass-button"]').click()
    pg.wait_for_selector('[data-testid="firstpass-run"]:not([disabled])', timeout=10000)
    pg.locator('[data-testid="firstpass-run"]').click()
    _wait_state(base, ("done", "stale-after-edits"))
    pg.wait_for_selector("#proposal", state="visible", timeout=30000)
    pg.wait_for_timeout(1500)

    real = server.firstpass_state
    slow = threading.Event()

    def firstpass_state():
        if slow.is_set():
            time.sleep(1.2)
        return real()

    monkeypatch.setattr(server, "firstpass_state", firstpass_state)
    seen: list = []                     # (time, importing, new_count) of every status read

    def on_resp(r):
        if r.request.method == "GET" and r.url.endswith("/api/firstpass") and r.status == 200:
            try:
                j = r.json()
            except Exception:  # noqa: BLE001
                return
            seen.append((time.time(), bool(j.get("importing")), j.get("new_count")))

    pg.on("response", on_resp)
    # the answer as of now (a hit counts the writes it was checked at), then slow rebuilds
    assert not httpx.get(base + "/api/firstpass", timeout=20).json().get("importing")
    slow.set()

    pg.locator("#tl").click(position={"x": 600, "y": 10})
    pg.keyboard.press("Control+i")
    pg.wait_for_selector(f"{DIALOG}[open] {ROW}", timeout=10000)
    pg.fill(PATH, str(cards["card"]))
    pg.press(PATH, "Enter")
    pg.wait_for_function(
        "(p) => [...document.querySelectorAll('[data-testid=media-browser-row]')].some(r =>"
        " r.dataset.path === p)", arg=str(cards["card"] / "RUN_2.MP4"), timeout=10000)
    pg.locator('[data-testid="media-browser"]').focus()
    pg.keyboard.press("Control+a")
    t0 = time.time()
    pg.locator('[data-testid="import-go"]').click()
    offer = pg.locator('[data-testid="firstpass-new-clips"]')
    offer.wait_for(state="visible", timeout=10000)
    took = time.time() - t0
    slow.clear()
    after = [s for s in seen if s[0] >= t0]
    late = [s for s in after if s[1]]
    assert late, f"no status read after the import was answered late: {json.dumps(after)}"
    # asked again within a second of an `importing` answer, not left to the 5 s poll
    nxt = [s for s in after if s[0] > late[0][0]]
    assert nxt and nxt[0][0] - late[0][0] < 1.2, json.dumps(after)
    assert took < 3.5, f"the offer showed {took:.1f} s after Import: {json.dumps(after)}"
    assert offer.inner_text().startswith("2 new clips: add them to the cut?")
