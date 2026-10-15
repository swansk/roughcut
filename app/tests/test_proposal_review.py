"""Proposal review where the cut is (verdict 4, punch 4; REDESIGN-GOAL dimension 11).

Judge 4: *"The proposal review reads like a chat, not an edit. The cut arrives as a wall
of text in a side tab, over a thin 40-pixel strip of ghost shots."* — the proposal did
not open on a reload, its first line said "No cut yet: …", a "last proposal · show it"
link pointed at the proposal already on screen, Discard wrapped onto its own row, and
the ghost lane's "proposal" label overprinted the first shot's name.

Now:
  * a waiting proposal opens on page load — the ghost lane under V1 and the review in
    the Assistant — with no "No cut yet" anywhere and no "show it" link;
  * the ghost lane is V1's height (≥ 80 px at 1440×900), each ghost a filmstrip like
    V1's, ✓ / ✗ big enough to read, its name in the track-header column, clear of the
    blocks;
  * the Assistant: every button on one row, "Things to check" on top (the notes check's
    flags, then the checks the model's notes ask for), the notes folded under a
    disclosure, and a table with one row per proposed shot — ✓ / ✗, Source In / Out
    timecode, why.

No model is called: the waiting proposal is an Ask record written to disk, a revision
is put up through `rcShowProposal`. `ROUGHCUT_REVIEW_SHOTS=<dir>` saves screenshots.
"""

from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

GHOST = "#tl .tl-xlane[data-lane=ghost]"
SHOTS = os.environ.get("ROUGHCUT_REVIEW_SHOTS")
TC = re.compile(r"^\d\d:\d\d:\d\d:\d\d$")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(project):
    import uvicorn
    import server

    original = project["edl"].read_text(encoding="utf-8")
    server.configure(project["edl"], project["footage"], project["sidecars"],
                     project["work"], proxies=False, assets=project["assets"])
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started, "server did not start"
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    th.join(timeout=10)
    project["edl"].write_text(original, encoding="utf-8")


@pytest.fixture
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        yield b
        b.close()


def _seed(project, segments: list[dict]) -> None:
    Path(project["edl"]).write_text(json.dumps({
        "variant": "T", "title": "test cut", "orient": "none", "story": "",
        "target_s": [5, 20], "segments": segments}, indent=1), encoding="utf-8")


def _waiting(job: str, plan: dict) -> Path:
    """An unanswered Ask record on disk, newer than the cut: a proposal that waits."""
    import server
    asks = server.STATE["asks"]
    asks.mkdir(parents=True, exist_ok=True)
    for old in asks.glob("*.json"):
        old.unlink()
    path = asks / f"{job}.json"
    path.write_text(json.dumps({"job": job, "created": time.time() + 3600, "note": "",
                                "story": "", "plan": plan}), encoding="utf-8")
    return path


def _open(browser, live_server, size=(1440, 900)):
    pg = browser.new_page(viewport={"width": size[0], "height": size[1]})
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.errors = errors
    pg.goto(live_server)
    pg.wait_for_function("window.tlLanes && typeof P !== 'undefined' && P && P.clips && window.tl && tl.state",
                         timeout=20000)
    return pg


def _shot(pg, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


FIRST = {
    "segments": [
        {"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0, "why": "open on the hello, before anyone knows the camera is on"},
        {"clip": "CLIP_B.MP4", "in": 0.0, "out": 1.5, "why": "the reply"},
        {"clip": "CLIP_C.MP4", "in": 1.0, "out": 3.0, "why": "the ride starts"},
        {"clip": "CLIP_A.MP4", "in": 4.0, "out": 5.5, "why": "the goodbye"},
        {"clip": "CLIP_B.MP4", "in": 3.0, "out": 4.5, "why": "close on the view"},
    ],
    "notes": ("My guess at what this film is about: a greeting, a ride and a goodbye. It opens "
              "on the hello and closes on the view. Things to check first: whether the hello "
              "reads on screen (it's unaudited), and whether the 3.0 ride frame really exists "
              "before 3.2. If it does not, the middle needs re-picking."),
    "firstpass": {"diff": {"sentence": "This adds 5 new shots.",
                           "shots": [{"pos": i + 1, "status": "new", "locked": False}
                                     for i in range(5)]}},
}


def _tops(pg, sel: str) -> list[float]:
    return pg.eval_on_selector_all(
        sel, "els => els.filter(e => e.offsetParent !== null)"
             ".map(e => Math.round(e.getBoundingClientRect().top))")


def test_a_waiting_first_cut_opens_on_reload_as_an_edit(browser, live_server, project):
    _seed(project, [])
    path = _waiting("rv-first", FIRST)
    pg = _open(browser, live_server)
    try:
        # it opens by itself: the review in the Assistant, the ghost lane under V1
        pg.wait_for_selector("#proposal", state="visible", timeout=10000)
        pg.wait_for_function(f"document.querySelectorAll('{GHOST} .ghost').length === 5", timeout=8000)
        assert pg.evaluate("segs.length") == 0, "a proposal is not an edit until accepted"
        assert pg.locator('.ws-tab.on:has-text("Assistant")').count() == 1

        # the ghost lane is V1's height, never a 40 px strip
        lane_h = pg.evaluate(f"document.querySelector('{GHOST}').getBoundingClientRect().height")
        v1_h = pg.evaluate("document.querySelector('#tl .tl-lane[data-lane=V1]').getBoundingClientRect().height")
        assert lane_h >= 80, lane_h
        assert abs(lane_h - v1_h) <= 2, (lane_h, v1_h)
        # each ghost is a filmstrip, like V1's blocks: the head frame and on
        assert pg.locator(f"{GHOST} .ghost").nth(0).locator(".gstrip img").count() >= 1
        src = pg.locator(f"{GHOST} .ghost").nth(0).locator(".gstrip img").first.get_attribute("src")
        assert "/media/poster/CLIP_A" in src and src.endswith("t=0.50"), src
        # ✓ / ✗ readable: at least 18 px, on every shot
        assert pg.locator(f"{GHOST} .ghost button.rv.ok").count() == 5
        box = pg.locator(f"{GHOST} .ghost button.rv.no").first.bounding_box()
        assert box["width"] >= 18 and box["height"] >= 16, box
        # the lane's name is in the header column, clear of the blocks
        assert pg.locator(f"{GHOST} .lbl").count() == 0
        head = pg.locator("#tl .tl-ghead")
        assert head.is_visible() and "Proposal" in head.inner_text()
        assert "5 of 5" in head.inner_text()
        hb = head.bounding_box()
        first = pg.locator(f"{GHOST} .ghost").nth(0).bounding_box()
        assert hb["x"] + hb["width"] <= first["x"], (hb, first)
        assert abs(hb["y"] - first["y"]) <= 2 and abs(hb["height"] - first["height"]) <= 2, (hb, first)

        # no "No cut yet" while segments are proposed, and no link to what is on screen
        body = pg.evaluate("document.body.innerText")
        assert "No cut yet" not in body
        assert "show it" not in body
        assert not pg.locator("#lastAsk").is_visible()

        # the Assistant: Things to check on top, the notes folded, one row per shot
        checks = pg.locator("#proposalChecks")
        assert checks.is_visible()
        items = checks.locator("li").all_inner_texts()
        assert len(items) == 2 and "hello reads on screen" in items[0] and "3.0 ride frame" in items[1], items
        notes = pg.locator("#proposalNotes")
        assert "First pass: This adds 5 new shots." in notes.inner_text()
        details = notes.locator("details.pnotes")
        assert details.count() == 1 and details.get_attribute("open") is None
        assert "If it does not" not in notes.inner_text(), "the notes are folded"
        assert pg.evaluate("document.querySelector('#proposalChecks').getBoundingClientRect().top"
                           " < document.querySelector('#proposalNotes').getBoundingClientRect().top"
                           " && document.querySelector('#proposalNotes').getBoundingClientRect().top"
                           " < document.querySelector('#proposalDiff table').getBoundingClientRect().top")
        rows = pg.locator("#proposalDiff table.ptable tbody tr.pshot")
        assert rows.count() == 5
        for i, seg in enumerate(FIRST["segments"]):
            cells = rows.nth(i).locator("td").all_inner_texts()
            assert cells[1] == f"{i + 1:02d}" and cells[2] == seg["clip"][:-4], cells
            assert TC.match(cells[3]) and TC.match(cells[4]), cells
            assert seg["why"] in cells[5], cells
            assert rows.nth(i).locator(".pv[aria-checked=true]").inner_text() == "✓"
        assert rows.nth(0).locator("td").nth(3).inner_text() == "00:00:00:12", "0.5 s at the clip's 24 fps"
        # the timecodes are whole on screen, not "00:00:00:…"
        clipped = pg.eval_on_selector_all(
            "#proposalDiff tr.pshot td.tc, #proposalDiff tr.pshot td.pc",
            "els => els.filter(e => e.scrollWidth > e.clientWidth).map(e => e.textContent)")
        assert clipped == [], clipped
        # and the ghosts' frames load (the poster endpoint answers every tile)
        pg.wait_for_function(
            f"[...document.querySelectorAll('{GHOST} .gstrip img')].every(i => i.complete && i.naturalWidth > 0)",
            timeout=20000)
        # every row on screen without scrolling the panel
        body_box = pg.evaluate("document.querySelector('#proposal').closest('.ws-body').getBoundingClientRect().bottom")
        last = rows.nth(4).bounding_box()
        assert last["y"] + last["height"] <= body_box + 1, (last, body_box)

        # a rejection, from the table: the lane and Accept follow; every button on one row
        rows.nth(1).locator(".pv").click()
        assert rows.nth(1).locator(".pv").inner_text() == "✗"
        assert "rejected" in pg.locator(f"{GHOST} .ghost").nth(1).get_attribute("class")
        assert pg.locator("#acceptProposal").inner_text() == "Accept 4 of 5"
        assert "4 of 5" in head.inner_text()
        buttons = pg.eval_on_selector_all(
            "#proposal button", "els => els.map(e => e.offsetParent !== null)")
        assert buttons == [True, True, True, True], "▶ Play it · Accept 4 of 5 · Accept all · Discard"
        tops = _tops(pg, "#proposal button")
        assert len(set(tops)) == 1, tops
        assert pg.locator("#proposal [role=checkbox]").count() == 5
        _shot(pg, "review-first-cut-1440")

        # Accept applies the 4; the line about an empty sequence stays gone
        pg.locator("#acceptProposal").click()
        assert pg.evaluate("segs.length") == 4
        assert not pg.locator("#proposal").is_visible()
        assert pg.evaluate(f"document.querySelector('{GHOST}').hidden") is True
        assert not head.is_visible()
        assert pg.errors == []
    finally:
        path.unlink(missing_ok=True)
        pg.close()


def test_discarding_a_waiting_first_cut_brings_back_the_empty_sequence(browser, live_server, project):
    """Discard answers it: the Assistant's line about an empty sequence comes back, and a
    reload does not open the proposal again."""
    _seed(project, [])
    path = _waiting("rv-discard", FIRST)
    pg = _open(browser, live_server)
    try:
        pg.wait_for_selector("#proposal", state="visible", timeout=10000)
        assert not pg.locator("#askFirst").is_visible()
        pg.locator("#rejectProposal").click()
        pg.wait_for_function(f"document.querySelector('{GHOST}').hidden", timeout=5000)
        pg.evaluate("dock.open('ask')")
        assert pg.locator("#askFirst").is_visible()
        with pg.expect_response(lambda r: "/api/asks/latest" in r.url, timeout=15000):
            pg.reload()
        pg.wait_for_function("window.tlLanes && typeof P !== 'undefined' && P && P.clips", timeout=20000)
        pg.wait_for_timeout(500)
        assert not pg.locator("#proposal").is_visible()
        assert pg.errors == []
    finally:
        path.unlink(missing_ok=True)
        pg.close()


TWO = [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
       {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}]


def test_a_revision_puts_the_notes_flags_under_things_to_check(browser, live_server, project):
    """A revision of a cut: the notes check's flag is the first thing to check (and
    clicking it opens its shot's row), the changed shot's row wears ⚠ and says what it
    is to the cut, and the shot the proposal drops is struck at the end of the table."""
    _seed(project, TWO + [{"clip": "CLIP_C.MP4", "in": 4.5, "out": 5.5, "why": "third"}])
    pg = _open(browser, live_server)
    try:
        pg.wait_for_selector("#tl .blk")
        ids = pg.evaluate("segs.map(s => s.id)")
        plan = {
            "segments": [
                {"id": ids[0], "clip": "CLIP_A.MP4", "in": 1.5, "out": 3.0, "why": "tighter start"},
                {"id": ids[1], "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"},
                {"clip": "CLIP_C.MP4", "in": 0.5, "out": 4.0, "why": "end on the ride"},
            ],
            "notes": "Tightened the open; the rest is unchanged.\n\nCheck: shot 1 (CLIP_A) changes …",
            "notes_model": "Tightened the open; the rest is unchanged.",
            "notes_check": {"ok": False, "checked": 1, "flags": [{
                "claim": "the rest is unchanged",
                "why": "says kept/unchanged, but the ranges differ",
                "shots": [{"id": ids[0], "clip": "CLIP_A.MP4", "pos": 1,
                           "was": {"in": 1.0, "out": 3.0}, "now": {"in": 1.5, "out": 3.0},
                           "delta_in": 0.5, "delta_out": 0.0}]}]},
            "firstpass": {"diff": {"shots": [
                {"pos": 1, "status": "replaced", "was": {"in": 1.0, "out": 3.0}, "locked": False},
                {"pos": 2, "status": "kept", "locked": True},
                {"pos": 3, "status": "new", "locked": False}]}},
        }
        pg.evaluate("(p) => rcShowProposal(p)", plan)
        pg.wait_for_selector("#proposal", state="visible", timeout=5000)
        items = pg.locator("#proposalChecks li").all_inner_texts()
        assert len(items) == 1 and "shot 1 (CLIP_A) changes 0:01.0–0:03.0 → 0:01.5–0:03.0" in items[0], items
        assert "the rest is unchanged" in items[0]
        # the notes are the model's words; the server's "Check:" line is the item above
        assert "Check:" not in pg.locator("#proposalNotes").inner_text()
        rows = pg.locator("#proposalDiff tr.pshot")
        assert rows.count() == 3
        # what each shot is to the cut: ± changed, nothing on a kept one, + new; the words
        # on the mark's hover and in the opened row
        assert rows.nth(0).locator(".pst").inner_text() == "±" and "⚠" in rows.nth(0).inner_text()
        assert "replaces 0:01.0–0:03.0" in rows.nth(0).locator(".pst").get_attribute("title")
        assert rows.nth(1).locator(".pst").count() == 0
        assert "kept — your edit, locked" in rows.nth(1).get_attribute("title")
        assert rows.nth(2).locator(".pst").inner_text() == "+"
        gone = pg.locator("#proposalDiff tr.pgone")
        assert gone.count() == 1 and "CLIP_C" in gone.inner_text() and "removed" in gone.inner_text()
        assert "replaces" not in rows.nth(0).inner_text()
        pg.locator("#proposalChecks li[data-k='0']").click()
        assert "open" in rows.nth(0).get_attribute("class")
        assert "replaces 0:01.0–0:03.0" in rows.nth(0).inner_text()
        # the frames in view load (lazy, as V1's: the new shot runs past the panel's edge)
        pg.wait_for_function(
            f"[...document.querySelectorAll('{GHOST} .ghost:first-child .gstrip img')]"
            ".every(i => i.complete && i.naturalWidth > 0)", timeout=20000)
        lane_h = pg.evaluate(f"document.querySelector('{GHOST}').getBoundingClientRect().height")
        assert lane_h >= 80, lane_h
        _shot(pg, "review-revision-1440")
        pg.locator("#rejectProposal").click()
        assert pg.evaluate("segs.length") == 3
        assert pg.errors == []
    finally:
        pg.close()


def test_rejected_ranges_are_said_in_the_panel_and_the_proposal(browser, live_server, project):
    """Lane RERUN remembers what the editor rejected. The first-pass panel says a run will
    not propose those ranges again; a re-run's proposal names what it left out under
    Things to check and greys it at the end of the table ("you rejected this"), and a
    range it still proposed (nothing else to propose) is flagged on its row's item."""
    Path(project["edl"]).write_text(json.dumps({
        "variant": "T", "title": "test cut", "orient": "none", "story": "",
        "target_s": [5, 20], "segments": TWO,
        "provenance": {"rejected": [{"clip": "CLIP_C.MP4", "in": 0.5, "out": 4.0,
                                     "at": time.time(), "job": "j0"}]}}, indent=1),
        encoding="utf-8")
    pg = _open(browser, live_server)
    try:
        pg.wait_for_selector("#tl .blk")
        pg.locator('[data-testid="firstpass-button"]').click()
        line = pg.locator('[data-testid="firstpass-rejected"]')
        line.wait_for(state="visible", timeout=10000)
        assert line.inner_text() == "Won't re-propose 1 range you rejected"
        assert "CLIP_C 0:00.5–0:04.0" in line.get_attribute("title")
        pg.keyboard.press("Escape")

        ids = pg.evaluate("segs.map(s => s.id)")
        left = [{"clip": "CLIP_C.MP4", "in": 0.5, "out": 4.0, "action": "dropped",
                 "rejected": {"in": 0.5, "out": 4.0}}]
        plan = {
            "segments": [{"id": ids[0], "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
                         {"id": ids[1], "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}],
            "notes": "Kept both shots.\n\nLeft out: CLIP_C 0:00.5–0:04.0 — you rejected this shot before, so it stays out.",
            "notes_check": {"ok": True, "flags": [], "checked": 0, "rejected": left},
            "firstpass": {"rejected": left, "diff": {"shots": [
                {"pos": 1, "status": "kept", "locked": True}, {"pos": 2, "status": "kept", "locked": True}]}},
        }
        pg.evaluate("(p) => rcShowProposal(p)", plan)
        pg.wait_for_selector("#proposal", state="visible", timeout=5000)
        items = pg.locator("#proposalChecks li").all_inner_texts()
        assert items == ["CLIP_C 0:00.5–0:04.0 — you rejected this shot before: left out"], items
        assert "Left out:" not in pg.locator("#proposalNotes").inner_text()
        assert pg.locator("#proposalDiff tr.pshot").count() == 2
        rej = pg.locator("#proposalDiff tr.prej")
        assert rej.count() == 1 and "you rejected this" in rej.inner_text() and "CLIP_C" in rej.inner_text()
        _shot(pg, "review-rejected-1440")
        pg.locator("#rejectProposal").click()

        # nothing else to propose: the server keeps it, flagged — the item points at its row
        flagged = [{**left[0], "action": "flagged"}]
        plan2 = {"segments": [{"clip": "CLIP_C.MP4", "in": 0.5, "out": 4.0, "why": "the ride"}],
                 "notes": "Only the ride.", "firstpass": {"rejected": flagged}}
        pg.evaluate("(p) => rcShowProposal(p)", plan2)
        pg.wait_for_selector("#proposal", state="visible", timeout=5000)
        li = pg.locator("#proposalChecks li")
        assert li.count() == 1 and "proposed again" in li.inner_text()
        assert li.get_attribute("data-k") == "0"
        assert pg.locator("#proposalDiff tr.prej").count() == 0
        pg.locator("#rejectProposal").click()
        assert pg.errors == []
    finally:
        _seed(project, TWO)
        pg.close()
