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
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

# the repo root, for `roughcut` (the server tests' import of it, run alone)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

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
        # an empty sequence: the ghost is drawn in V1's own row (no empty V1 above it),
        # and the Program shows the proposal's first frame, not a black screen
        v1 = pg.evaluate("(() => { const r = document.querySelector('#tl .tl-lane[data-lane=V1]')"
                         ".getBoundingClientRect(); return [r.top, r.height]; })()")
        g1 = pg.evaluate(f"(() => {{ const r = document.querySelector('{GHOST}')"
                         ".getBoundingClientRect(); return [r.top, r.height]; })()")
        assert abs(v1[0] - g1[0]) <= 1 and abs(v1[1] - g1[1]) <= 1, (v1, g1)
        assert not pg.locator("#tl .tl-thead[data-track=V1]").is_visible()
        pg.wait_for_function("(() => { const v = document.querySelector('#player video.live');"
                             " return !!v && v.readyState >= 2; })()", timeout=15000)
        assert pg.evaluate("document.querySelector('#player video.live').dataset.src").find("CLIP_A") >= 0
        assert not pg.locator("#progEmpty").is_visible()
        # and the ghosts' frames load (the poster endpoint answers every tile)
        pg.wait_for_function(
            f"[...document.querySelectorAll('{GHOST} .gstrip img')].every(i => i.complete && i.naturalWidth > 0)",
            timeout=20000)
        # every row on screen without scrolling the panel (verdict 5, punch 3: the wave-4
        # concession of 4 rows is gone): the proposal owns the Assistant, the form folds
        seen = _rows_in_view(pg)
        assert seen["first"] and seen["n"] >= 5, seen
        # a first cut: no form to fold, so no fold line; the counts on the title's line
        assert not pg.locator("#askFold").is_visible()
        counts = pg.locator("#proposalDiff .phead").bounding_box()
        title = pg.locator("#proposal h2").bounding_box()
        assert abs(counts["y"] - title["y"]) <= 1 and title["x"] + title["width"] < counts["x"], (title, counts)

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


# ---------------------------------------------------------------- verdict 5, punch 3
# A pending proposal owns the Assistant: on a re-run (the K1 workflow) it used to open
# below the "What is this film about?" / "Ask for a change" form, with 0 rows in view.

CUT6 = [{"clip": c, "in": a, "out": b, "why": w} for c, a, b, w in [
    ("CLIP_A.MP4", 0.5, 2.0, "hello"), ("CLIP_B.MP4", 0.0, 1.5, "reply"),
    ("CLIP_C.MP4", 1.0, 2.5, "ride"), ("CLIP_A.MP4", 3.0, 4.0, "turn"),
    ("CLIP_B.MP4", 2.0, 3.0, "laugh"), ("CLIP_C.MP4", 4.0, 5.5, "view")]]


def _rerun_plan(ids: list[str]) -> dict:
    """A re-run's proposal as the server writes it: the trimmed shot kept, two added,
    one left out because the editor rejected it, checks from the model and the notes
    check, the notes long enough to fold."""
    segs = [{"id": ids[0], "clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0, "why": "hello"},
            {"id": ids[1], "clip": "CLIP_B.MP4", "in": 0.0, "out": 1.5, "why": "reply"},
            {"clip": "CLIP_C.MP4", "in": 0.2, "out": 1.0, "why": "the lift arrives before the ride"},
            {"id": ids[2], "clip": "CLIP_C.MP4", "in": 1.0, "out": 2.5, "why": "ride"},
            {"id": ids[3], "clip": "CLIP_A.MP4", "in": 3.2, "out": 4.0, "why": "the turn, tighter"},
            {"id": ids[4], "clip": "CLIP_B.MP4", "in": 2.0, "out": 3.0, "why": "laugh"},
            {"clip": "CLIP_A.MP4", "in": 5.0, "out": 5.8, "why": "a breath before the view"},
            {"id": ids[5], "clip": "CLIP_C.MP4", "in": 4.0, "out": 5.5, "why": "view"}]
    left = [{"clip": "CLIP_B.MP4", "in": 4.0, "out": 5.0, "action": "dropped",
             "rejected": {"in": 4.0, "out": 5.0}}]
    return {
        "segments": segs,
        "notes": ("I kept your trim on the hello and every shot you locked. Check the picture "
                  "under the CLIP_C 0.2-1.0 lift first: it is unaudited. Also check that the "
                  "CLIP_A 5.0-5.8 breath really holds still before the view.\n\nLeft out: "
                  "CLIP_B 0:04.0–0:05.0 — you rejected this shot before, so it stays out."),
        "notes_model": ("I kept your trim on the hello and every shot you locked. Check the picture "
                        "under the CLIP_C 0.2-1.0 lift first: it is unaudited. Also check that the "
                        "CLIP_A 5.0-5.8 breath really holds still before the view."),
        "checks": [
            {"text": "CLIP_B 0:04.0–0:05.0 — you rejected this shot before: left out",
             "clip": "CLIP_B.MP4", "in": 4.0, "out": 5.0, "kind": "rejected", "shot": None},
            {"text": "Check the picture under the lift first: it is unaudited.",
             "clip": "CLIP_C.MP4", "in": 0.2, "out": 1.0, "kind": "model"},
            {"text": "Check the breath really holds still before the view.",
             "clip": "CLIP_A.MP4", "in": 5.0, "out": 5.8, "kind": "model"}],
        "notes_check": {"ok": True, "flags": [], "checked": 1, "rejected": left},
        "firstpass": {"rejected": left, "mode": "rerun", "keep": ids[:2],
                      "diff": {"sentence": "This keeps 5 of your shots, changes 1 and adds 2.",
                               "shots": []}},
    }


def _seed_rerun(project) -> None:
    Path(project["edl"]).write_text(json.dumps({
        "variant": "T", "title": "test cut", "orient": "none",
        "story": "A greeting, a ride and a goodbye on the hill.",
        "target_s": [5, 20], "segments": [dict(s, id=f"s{i}") for i, s in enumerate(CUT6)]},
        indent=1), encoding="utf-8")


def _rows_in_view(pg) -> dict:
    return pg.evaluate("""() => {
      const body = document.querySelector('#proposal').closest('.ws-body').getBoundingClientRect();
      const rows = [...document.querySelectorAll('#proposalDiff tr.pshot')].map(r => r.getBoundingClientRect());
      const inView = rows.filter(r => r.top >= body.top - 1 && r.bottom <= body.bottom + 1);
      return {n: inView.length, first: rows.length ? rows[0].top >= body.top - 1 && rows[0].bottom <= body.bottom + 1 : false,
              body: [body.top, body.bottom], rows: rows.map(r => [Math.round(r.top), Math.round(r.bottom)])};
    }""")


def test_a_pending_rerun_proposal_owns_the_assistant(browser, live_server, project):
    """Verdict 5, punch 3: at 1440x900, with a pending re-run proposal, the proposal sits
    above the Ask form (folded to one line), the table's first row is in view and at
    least 5 rows show without scrolling."""
    _seed_rerun(project)
    ids = [f"s{i}" for i in range(6)]
    path = _waiting("rv-rerun", _rerun_plan(ids))
    pg = _open(browser, live_server)
    try:
        pg.wait_for_selector("#proposal", state="visible", timeout=10000)
        pg.wait_for_function(f"document.querySelectorAll('{GHOST} .ghost').length === 8", timeout=8000)
        pg.wait_for_timeout(300)
        # the proposal is above the form, and the form is one line while it is up
        top = pg.evaluate("""() => {
          const p = document.querySelector('#proposal').getBoundingClientRect();
          const f = [...document.querySelectorAll('#askStory, #askPanel')]
            .filter(e => e.offsetParent !== null).map(e => e.getBoundingClientRect());
          return {p: p.top, forms: f.map(r => [r.top, r.height])};
        }""")
        assert all(t > top["p"] for t, _ in top["forms"]), top
        assert pg.locator("#story").is_visible() is False, "the story box folds while a proposal is up"
        assert pg.locator("#ask").is_visible() is False, "the Ask button folds while a proposal is up"
        fold = pg.locator("#askFold")
        assert fold.is_visible()
        fb = fold.bounding_box()
        assert fb["height"] <= 30, fb
        _shot(pg, "review-rerun-1440")
        seen = _rows_in_view(pg)
        assert seen["first"], seen
        assert seen["n"] >= 5, seen
        # the checks are the plan's list, not a regex on the notes
        items = pg.locator("#proposalChecks li").all_inner_texts()
        assert len(items) == 3, items
        assert "CLIP_C 0:00.2–0:01.0" in items[1] and pg.locator("#proposalChecks li").nth(1).get_attribute("data-k") == "2"
        # a long list shows 3 and folds the rest, so the shots stay in view
        many = _rerun_plan(ids)
        many["checks"] = many["checks"] + [{"text": f"Check {n}.", "kind": "model"} for n in range(3)]
        pg.evaluate("(p) => rcShowProposal(p)", many)
        pg.wait_for_function("document.querySelectorAll('#proposalChecks li.pmore').length === 3")
        assert pg.locator("#proposalChecks li:visible").count() == 3
        assert pg.locator("#proposalChecks .pshowall").inner_text() == "+ 3 more"
        assert _rows_in_view(pg)["n"] >= 5
        pg.locator("#proposalChecks .pshowall").click()
        assert pg.locator("#proposalChecks li:visible").count() == 6
        # the fold opens the form again (the proposal stays up, above it)
        fold.click()
        assert pg.locator("#ask").is_visible()
        assert pg.locator("#proposal").is_visible()
        assert pg.errors == []
    finally:
        path.unlink(missing_ok=True)
        _seed(project, TWO)
        pg.close()


def test_checks_are_the_plans_list_not_a_regex_on_the_notes(browser, live_server, project):
    """Verdict 5: the model's "Check … first" / "Also check …" warnings sat in the folded
    notes because "Things to check" parsed only the literal phrase. A plan carries its
    checks as data — `checks: [{text, clip, in, out}]` — and the list renders from it; a
    check on a proposed shot's range points at its row."""
    _seed(project, TWO)
    pg = _open(browser, live_server)
    try:
        pg.wait_for_selector("#tl .blk")
        notes = ("Opens on the ride now. Check the picture under the CLIP_C 0.5-4.0 ending first: "
                 "it is unaudited. Also check that the CLIP_A 1.0-3.0 hello really reads on screen.")
        plan = {"segments": [{"clip": "CLIP_C.MP4", "in": 0.5, "out": 4.0, "why": "the ride"},
                             {"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "the hello"}],
                "notes": notes}
        # without a list, the notes alone give nothing to check (the old regex's blind spot)
        pg.evaluate("(p) => rcShowProposal(p)", plan)
        pg.wait_for_selector("#proposal", state="visible", timeout=5000)
        assert pg.locator("#proposalChecks li").count() == 0
        pg.locator("#rejectProposal").click()
        # with it, both are Things to check, each pointing at its shot
        plan["checks"] = [
            {"text": "Check the picture under the ending first: it is unaudited.",
             "clip": "CLIP_C.MP4", "in": 0.5, "out": 4.0},
            {"text": "Check that the hello really reads on screen.",
             "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0}]
        pg.evaluate("(p) => rcShowProposal(p)", plan)
        pg.wait_for_selector("#proposal", state="visible", timeout=5000)
        assert pg.locator("#proposalChecks").is_visible()
        li = pg.locator("#proposalChecks li")
        assert li.count() == 2, li.all_inner_texts()
        assert "unaudited" in li.nth(0).inner_text() and "CLIP_C 0:00.5–0:04.0" in li.nth(0).inner_text()
        assert "reads on screen" in li.nth(1).inner_text()
        assert [li.nth(i).get_attribute("data-k") for i in range(2)] == ["0", "1"]
        li.nth(1).click()
        assert "open" in pg.locator("#proposalDiff tr.pshot").nth(1).get_attribute("class")
        pg.locator("#rejectProposal").click()
        assert pg.errors == []
    finally:
        _seed(project, TWO)
        pg.close()


def test_pick_markers_never_cover_a_ghosts_name(browser, live_server, project):
    """Verdict 5: "ghost names truncated to 'C…' under amber pick markers". The markers
    sit under the name band, so no `.tl-pick` box intersects a `.ghost .gtxt` box."""
    _seed(project, TWO)
    pg = _open(browser, live_server)
    try:
        pg.wait_for_selector("#tl .blk")
        out = pg.evaluate("""(selects) => fetch('/api/selects', {
            method: 'PUT', headers: {'content-type': 'application/json'},
            body: JSON.stringify({selects})}).then(r => r.json())""", [
            {"clip": "CLIP_A.MP4", "start": 0.0, "end": 2.5, "hero": True, "why": "the hello"},
            {"clip": "CLIP_C.MP4", "start": 1.5, "end": 2.0, "why": "the ride"},
            {"clip": "CLIP_B.MP4", "start": 0.0, "end": 1.0, "why": "the reply"}])
        assert out.get("ok"), out
        pg.evaluate("refreshBin()")
        pg.evaluate("(p) => rcShowProposal(p)", {"segments": [
            {"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "the hello"},
            {"clip": "CLIP_C.MP4", "in": 1.5, "out": 4.0, "why": "the ride"},
            {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "the reply"}], "notes": "three"})
        pg.wait_for_function(f"document.querySelectorAll('{GHOST} .tl-pick').length >= 3", timeout=8000)
        hits = pg.evaluate("""() => {
          const box = (e) => e.getBoundingClientRect();
          const cross = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
          const picks = [...document.querySelectorAll('#tl .tl-pick')].map(box);
          const names = [...document.querySelectorAll('#tl .ghost .gtxt')].map(box);
          const out = [];
          picks.forEach((p, i) => names.forEach((n, j) => { if (cross(p, n)) out.push([i, j]); }));
          return {hits: out, picks: picks.length, names: names.length};
        }""")
        assert hits["picks"] >= 3 and hits["names"] == 3, hits
        assert hits["hits"] == [], hits
        _shot(pg, "review-picks-under-names-1440")
        pg.locator("#rejectProposal").click()
        assert pg.errors == []
    finally:
        _seed(project, TWO)
        pg.close()


# ---------------------------------------------------------------- checks, server side
CLIPS3 = {c: {"name": c, "duration": 6.0} for c in ("CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4")}


def test_the_plan_carries_the_models_checks_validated():
    """`checks` is part of the plan the model writes: kept with its range when the range
    is real, kept without it when not, dropped when it says nothing — never a re-ask."""
    from roughcut import revise
    assert "checks" in revise.PLAN_SCHEMA
    plan = revise.validate_plan({
        "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "hello"}],
        "notes": "Opens on the hello.",
        "checks": [{"text": "Check the hello reads on screen.", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0},
                   {"text": "Check  the  ending.", "clip": "CLIP_Z.MP4", "in": 1, "out": 2},
                   {"text": "Past the end.", "clip": "CLIP_B.MP4", "in": 5.0, "out": 9.0},
                   {"text": "  "}, "Whole film: is it too long?"]}, CLIPS3)
    assert plan["checks"] == [
        {"text": "Check the hello reads on screen.", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "kind": "model"},
        {"text": "Check the ending.", "clip": None, "in": None, "out": None, "kind": "model"},
        {"text": "Past the end.", "clip": "CLIP_B.MP4", "in": None, "out": None, "kind": "model"},
        {"text": "Whole film: is it too long?", "clip": None, "in": None, "out": None, "kind": "model"}]
    # an old answer without the key still validates
    assert revise.validate_plan({"segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0}]},
                                CLIPS3)["checks"] == []
    # every prompt asks for them
    prompt = revise.build_first_prompt({}, "", "", (5, 20))
    assert "`checks`" in prompt


def test_check_notes_puts_flags_rejections_and_the_models_checks_in_one_list():
    from roughcut import revise
    current = [{"id": "a", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0},
               {"id": "b", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0}]
    plan = {"segments": [{"id": "a", "clip": "CLIP_A.MP4", "in": 1.5, "out": 3.0},
                         {"id": "b", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0},
                         {"clip": "CLIP_C.MP4", "in": 0.5, "out": 4.0}],
            "notes": "Added the ride; the rest is unchanged.",
            "checks": [{"text": "Check the ride's picture.", "clip": "CLIP_C.MP4",
                        "in": 1.0, "out": 2.0, "kind": "model"}],
            "firstpass": {"rejected": [{"clip": "CLIP_B.MP4", "in": 3.0, "out": 4.0,
                                        "action": "dropped"}]}}
    out = revise.check_notes(plan, current)
    kinds = [c["kind"] for c in out["checks"]]
    assert kinds == ["flag", "rejected", "model"], out["checks"]
    flag, rej, model = out["checks"]
    assert flag["shot"] == 0 and "shot 1 (CLIP_A) changes 0:01.0–0:03.0 → 0:01.5–0:03.0" in flag["text"]
    assert rej["shot"] is None and rej["text"] == "CLIP_B 0:03.0–0:04.0 — you rejected this shot before: left out"
    assert model["shot"] == 2 and model["text"] == "Check the ride's picture."
    # idempotent: a second pass does not double the list
    assert revise.check_notes(out, current)["checks"] == out["checks"]
    # a first cut (nothing to compare) still carries the model's checks
    first = revise.check_notes({"segments": plan["segments"], "notes": "x",
                                "checks": plan["checks"]}, [])
    assert [c["kind"] for c in first["checks"]] == ["model"]


# ---------------------------------------------------------------- verdict 6, punch 4
# On real output (a 19-shot first pass, six checks of 150-200 characters) only 3 rows
# were in view at 1440x900: "Things to check" took ~170 px of a 379 px panel. The
# fixture's short checks hid it. Each check is one line now (its whole text on hover and
# a click), and while a proposal waits under the Assistant tab the Effect Controls column
# runs the window's full height. Check text was cut mid-word at 200 characters, and L / K
# toasted "nothing to play yet" over the proposal's frame on an empty sequence.

REAL_CHECKS = [
    "Check that the skier turning toward the camera at the top of the run reads as a face: it is "
    "the only person in the film so far, and the opening has no one in it without them.",
    "The lift arrives before the ride here, and I could not see whether the chair swings into "
    "frame or the camera is still on the bar; if it is the bar, the cut lands on a blur.",
    "Check the breath before the view really holds still: the frames sampled at 5.0 and 5.8 look "
    "level, but anything between them could still be the pole plant that shakes the whole picture.",
    "The laugh is heard, not seen, so check that the picture under it is not the inside of a glove "
    "or a sleeve; the transcript is clear about the laugh and says nothing about the picture.",
    "Whole film: at two minutes fifty it is past the target, and the middle three ride shots look "
    "alike from the samples, so one of them could go if the pace drags when you watch it through.",
    "The last shot ends on the view, but the audio under it is wind noise at full level; check "
    "whether it wants a fade or the music bed to carry it, since a hard stop on wind reads as a mistake.",
]
_RANGES = [("CLIP_A.MP4", 0.2, 1.4), ("CLIP_C.MP4", 0.2, 1.0), ("CLIP_A.MP4", 5.0, 5.8),
           ("CLIP_B.MP4", 2.0, 3.0), (None, None, None), ("CLIP_C.MP4", 4.0, 5.5)]
WHYS = ["opens on the skier turning toward the camera at the top of the run, the only face we get",
        "the lift arrives before the ride, so the climb reads before the descent starts",
        "the first turn, wide, with the whole valley behind it and the light coming low from the left",
        "a laugh off camera that sounds like the friend from the car, kept for the voice",
        "the second turn, closer, the snow spraying past the lens"]


def _real_segments(ids: list[str] | None = None) -> list[dict]:
    """19 shots of a real first pass's shape: short ranges over the three clips, each
    with a sentence of why — ids on the first `len(ids)` when a cut is being revised."""
    out = []
    for i in range(19):
        clip = ("CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4")[i % 3]
        a = round((i * 0.29) % 4.8, 2)
        seg = {"clip": clip, "in": a, "out": round(a + 0.6 + (i % 4) * 0.2, 2),
               "why": f"{WHYS[i % len(WHYS)]} ({i + 1})"}
        if ids and i < len(ids):
            seg["id"] = ids[i]
        out.append(seg)
    return out


def _real_checks(kind: str = "model") -> list[dict]:
    return [{"text": t, "clip": c, "in": a, "out": b, "kind": kind}
            for t, (c, a, b) in zip(REAL_CHECKS, _RANGES)]


REAL_NOTES = ("My guess at what this film is about: a morning on the hill with a friend, from the "
              "lift to the last run. It opens on the only face and closes on the view. The middle "
              "is the ride, cut to the turns; the laugh is kept for the voice.")


def _real_first() -> dict:
    return {"segments": _real_segments(), "notes": REAL_NOTES, "notes_model": REAL_NOTES,
            "checks": _real_checks(),
            "firstpass": {"diff": {"sentence": "This adds 19 new shots.",
                                   "shots": [{"pos": i + 1, "status": "new", "locked": False}
                                             for i in range(19)]}}}


def _real_rerun(ids: list[str]) -> dict:
    left = [{"clip": "CLIP_B.MP4", "in": 4.0, "out": 5.0, "action": "dropped",
             "rejected": {"in": 4.0, "out": 5.0}}]
    checks = [{"text": "CLIP_B 0:04.0–0:05.0 — you rejected this shot before: left out",
               "clip": "CLIP_B.MP4", "in": 4.0, "out": 5.0, "kind": "rejected", "shot": None}]
    return {"segments": _real_segments(ids), "notes": REAL_NOTES, "notes_model": REAL_NOTES,
            "checks": checks + _real_checks()[:5],
            "notes_check": {"ok": True, "flags": [], "checked": 6, "rejected": left},
            "firstpass": {"rejected": left, "mode": "rerun", "keep": ids[:2],
                          "diff": {"sentence": "This keeps 6 of your shots and adds 13.", "shots": []}}}


def _panels_tile(pg) -> dict:
    """The five panels inside the window, none overlapping another, and the board not
    scrolling (test_workspace's checks, in the proposal's arrangement)."""
    return pg.evaluate("""() => {
      const ids = ['pProject', 'pSource', 'pProgram', 'pTimeline', 'pInspector'];
      const r = Object.fromEntries(ids.map(i => [i, document.getElementById(i).getBoundingClientRect()]));
      const hits = [];
      ids.forEach((a, i) => ids.slice(i + 1).forEach(b => {
        const A = r[a], B = r[b];
        if (A.left < B.right - 0.5 && B.left < A.right - 0.5 && A.top < B.bottom - 0.5 && B.top < A.bottom - 0.5)
          hits.push([a, b]);
      }));
      const out = ids.filter(i => r[i].left < -0.5 || r[i].top < -0.5
        || r[i].right > innerWidth + 0.5 || r[i].bottom > innerHeight + 0.5);
      const se = document.scrollingElement;
      return {hits, out, scroll: se.scrollHeight > se.clientHeight || se.scrollWidth > se.clientWidth,
              rects: Object.fromEntries(ids.map(i => [i, [Math.round(r[i].left), Math.round(r[i].top),
                                                          Math.round(r[i].width), Math.round(r[i].height)]]))};
    }""")


def _checks_one_line(pg) -> list:
    """Each visible check's height over its line height (1 = one line)."""
    return pg.eval_on_selector_all("#proposalChecks li", """els => els.filter(e => e.offsetParent !== null)
      .map(e => Math.round(e.getBoundingClientRect().height / parseFloat(getComputedStyle(e).lineHeight) * 100) / 100)""")


def test_a_real_length_first_cut_shows_five_rows_and_plays_on_l(browser, live_server, project):
    _seed(project, [])
    plan = _real_first()
    path = _waiting("rv-real-first", plan)
    pg = _open(browser, live_server)
    try:
        pg.wait_for_selector("#proposal", state="visible", timeout=10000)
        pg.wait_for_function(f"document.querySelectorAll('{GHOST} .ghost').length === 19", timeout=8000)
        pg.wait_for_timeout(300)
        # the Effect Controls column runs the full height while it waits
        assert pg.evaluate("document.getElementById('ws').classList.contains('ptall')")
        tile = _panels_tile(pg)
        assert tile["hits"] == [] and tile["out"] == [] and not tile["scroll"], tile
        insp, prog = tile["rects"]["pInspector"], tile["rects"]["pProgram"]
        assert insp[1] <= prog[1] + 1 and insp[3] >= 800, tile
        # each check one line, 3 shown and "+ 3 more"; the whole of each on its hover
        lines = _checks_one_line(pg)
        assert len(lines) == 3 and all(h <= 1.05 for h in lines), lines
        assert pg.locator("#proposalChecks .pshowall").inner_text() == "+ 3 more"
        for i, li in enumerate(pg.locator("#proposalChecks li").all()):
            assert REAL_CHECKS[i] in li.get_attribute("title"), i
            assert li.text_content().endswith(REAL_CHECKS[i]), "the words are whole; the line clips them"
        # >= 5 rows fully in view, the first among them
        seen = _rows_in_view(pg)
        assert seen["first"] and seen["n"] >= 5, seen
        _shot(pg, "review-real-first-1440")
        # a click opens a check in place (and it stays one line until then)
        first = pg.locator("#proposalChecks li").first
        first.click()
        lh = pg.evaluate("parseFloat(getComputedStyle(document.querySelector('#proposalChecks li')).lineHeight)")
        assert first.bounding_box()["height"] > 1.5 * lh
        first.click()
        assert first.bounding_box()["height"] <= 1.05 * lh

        # L / K / Space play and pause the proposal on the empty sequence, as ▶ Play it does
        toasts: list[str] = []
        pg.expose_function("rvToast", lambda t: toasts.append(t))
        pg.evaluate("""() => { const t = window.toast; window.toast = (m, ...r) => { rvToast(String(m)); return t(m, ...r); }; }""")
        pg.evaluate("document.activeElement && document.activeElement.blur()")
        assert pg.evaluate("rcProposalPlayer.playing()") is False
        pg.keyboard.press("l")
        pg.wait_for_function("rcProposalPlayer.playing()", timeout=8000)
        assert pg.evaluate("player.playing") is False, "the proposal plays, not the (empty) cut"
        assert pg.evaluate("document.querySelector('#playingWhat').textContent").startswith("proposal ·")
        pg.wait_for_timeout(400)
        pg.keyboard.press("k")
        pg.wait_for_function("!rcProposalPlayer.playing()", timeout=3000)
        at = pg.evaluate("tl.state.playhead")
        assert at > 0.1, at
        pg.keyboard.press("Space")
        pg.wait_for_function("rcProposalPlayer.playing()", timeout=8000)
        pg.wait_for_timeout(200)
        assert pg.evaluate("tl.state.playhead") >= at - 0.05, "it carries on from where it stopped"
        pg.keyboard.press("Space")
        pg.wait_for_function("!rcProposalPlayer.playing()", timeout=3000)
        assert not any("nothing to play" in t for t in toasts), toasts

        # answered, the arrangement is the editor's again: the Program never changed size
        pg.locator("#rejectProposal").click()
        pg.wait_for_function("!document.getElementById('ws').classList.contains('ptall')", timeout=3000)
        after = _panels_tile(pg)
        assert after["hits"] == [] and not after["scroll"], after
        assert abs(after["rects"]["pProgram"][2] - prog[2]) <= 1, (after["rects"], prog)
        assert after["rects"]["pInspector"][1] > prog[1] + 100, after["rects"]
        assert pg.errors == []
    finally:
        path.unlink(missing_ok=True)
        _seed(project, TWO)
        pg.close()


def test_a_real_length_rerun_shows_five_rows(browser, live_server, project):
    _seed_rerun(project)
    ids = [f"s{i}" for i in range(6)]
    path = _waiting("rv-real-rerun", _real_rerun(ids))
    pg = _open(browser, live_server)
    try:
        pg.wait_for_selector("#proposal", state="visible", timeout=10000)
        pg.wait_for_function(f"document.querySelectorAll('{GHOST} .ghost').length === 19", timeout=8000)
        pg.wait_for_timeout(300)
        assert pg.locator("#askFold").is_visible()
        assert pg.evaluate("document.getElementById('ws').classList.contains('ptall')")
        tile = _panels_tile(pg)
        assert tile["hits"] == [] and tile["out"] == [] and not tile["scroll"], tile
        lines = _checks_one_line(pg)
        assert len(lines) == 3 and all(h <= 1.05 for h in lines), lines
        seen = _rows_in_view(pg)
        assert seen["first"] and seen["n"] >= 5, seen
        _shot(pg, "review-real-rerun-1440")
        # another tab in the group gives the column back; the Assistant takes it again
        pg.evaluate("dock.open('inspect')")
        pg.wait_for_function("!document.getElementById('ws').classList.contains('ptall')", timeout=3000)
        pg.evaluate("dock.open('ask')")
        pg.wait_for_function("document.getElementById('ws').classList.contains('ptall')", timeout=3000)
        # a sequence with shots: the keys drive the cut, as before (the proposal is not theirs)
        assert pg.evaluate("rcProposalPlayer.ready()") is False
        # a check saved before the word-aware trim, cut 200 characters into a word, is
        # shown to its last whole word
        legacy = ("Check the opening shot: the skier turns toward the camera and it is the only face "
                  "we get, and if it does not read on the smaller screen there is nobody in the whole "
                  "film, no someone the viewer can identify")[:200]
        assert len(legacy) == 200 and legacy.endswith("ide"), legacy
        plan = _real_rerun(ids)
        plan["checks"][1]["text"] = legacy
        pg.evaluate("(p) => rcShowProposal(p)", plan)
        shown = pg.locator("#proposalChecks li").nth(1).text_content()
        assert shown.endswith("viewer can…"), shown
        pg.locator("#rejectProposal").click()
        assert pg.errors == []
    finally:
        path.unlink(missing_ok=True)
        _seed(project, TWO)
        pg.close()


def test_check_text_is_never_cut_inside_a_word():
    """Verdict 6: "…someone the viewer can ide" — `CHECK_TEXT_MAX` cut at 200 characters.
    A check past the cap ends at a sentence, else at a whole word and "…"; one within it
    is kept whole."""
    from roughcut import revise
    clips = CLIPS3
    long_one = ("Check the moment at the top of the run where the skier turns toward the camera: it "
                "is the only face in the film so far, and if it does not read the opening has no one "
                "in it. Without it there is no someone the viewer can identify with, and the whole "
                "middle of the film is scenery with nobody in it at all, which is not the film you "
                "described when you said it was about a morning with a friend.")
    no_stop = " ".join(["the viewer can identify with someone"] * 20)
    raws = REAL_CHECKS[:4] + [long_one, no_stop]          # CHECKS_MAX is 6
    out = revise.validate_checks([{"text": t} for t in raws], clips)
    texts = [c["text"] for c in out]
    assert texts[:4] == REAL_CHECKS[:4], "a check within the cap is kept whole"
    assert len(texts) == 6, texts
    for raw, got in zip(raws, texts):
        norm = " ".join(raw.split())
        assert len(got) <= revise.CHECK_TEXT_MAX
        if got == norm:
            continue
        kept = got[:-1] if got.endswith("…") else got
        assert norm.startswith(kept), (kept, norm)
        assert norm[len(kept)] == " " or got.endswith("…") and norm[len(kept.rstrip(' ,;:—–-'))] in " ,;:", (got, norm)
        assert got.endswith(("…", ".", "!", "?")), got
    assert texts[4].endswith("in it."), texts[4]
    assert texts[5].endswith("…") and texts[5][:-1].split()[-1] in no_stop.split(), texts[5]
    # the judge's own: 200 characters into a word, never "…ide"
    judge = ("Check the opening shot: the skier turns toward the camera and it is the only face we get. "
             "If it does not read on the small screen there is nobody in this film and no someone the "
             "viewer can identify with at all")
    got = revise.trim_check(judge, 200)
    assert not got.endswith("ide") and got.endswith((".", "…")), got
