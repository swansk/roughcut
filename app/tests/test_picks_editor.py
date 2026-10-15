"""The pass's picks and proposals as editor objects (verdict 3, punch 7; REDESIGN-GOAL
dimension 11: "proposals are reviewable edits, not a separate app").

  * A proposal on the ghost lane is reviewed shot by shot: ✓ / ✗ on every proposed shot,
    Enter accepts the focused one and ⌫ rejects it, a ↺ on a shot the proposal removes
    keeps it. Accept applies only the accepted shots — a rejected new shot is not added,
    a rejected change leaves the shot as it is, a refused removal stays — in one undo;
    Accept all and Discard stay. The shot's "why" and the notes check's flags are on its
    hover, and a flagged shot wears a mark.
  * The Selects tab is a list like Project's (Name, Duration, Usage always; Media In /
    Out, Reason and Clip as the panel widens; the rest in the row's hover), sortable by
    its column heads, and its rows are clips: `,` / `.` insert / overwrite the selected
    row's range, a drag onto V1 inserts it, a double-click opens it in the Source monitor
    with its In and Out set (and `,` then inserts that).
  * The picks are markers on the cut and on the proposed cut, the reason on hover.

Every proposal here is put up through the first pass's own hook (`rcShowProposal`), so
no model is called. `ROUGHCUT_PICKS_SHOTS=<dir>` saves a screenshot of each state.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

GHOST = "#tl .tl-xlane[data-lane=ghost]"
SHOTS = os.environ.get("ROUGHCUT_PICKS_SHOTS")


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
                     project["work"], proxies=False,
                     visual=project["work"] / "picks-visual", assets=project["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in project["stems"]])
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


def _seed(project, segments: list[dict]) -> None:
    Path(project["edl"]).write_text(json.dumps({
        "variant": "T", "title": "test cut", "orient": "none", "story": "",
        "target_s": [5, 20], "segments": segments}, indent=1), encoding="utf-8")


TWO = [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
       {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}]


@pytest.fixture
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        yield b
        b.close()


def _open(browser, live_server, size=(1440, 900)):
    pg = browser.new_page(viewport={"width": size[0], "height": size[1]})
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.errors = errors
    pg.goto(live_server)
    pg.wait_for_function("window.tlLanes && typeof P !== 'undefined' && P && P.clips && window.tl && tl.state",
                         timeout=20000)
    pg.wait_for_timeout(300)
    return pg


def _shot(pg, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


def _ranges(pg) -> list[list]:
    return pg.evaluate("segs.map(s => [s.clip, s.in, s.out])")


def _on_disk(project) -> list[list]:
    return [[s["clip"], s["in"], s["out"]] for s in
            json.loads(Path(project["edl"]).read_text(encoding="utf-8"))["segments"]]


def _wait_saved(pg) -> None:
    pg.wait_for_function(
        "document.querySelector('#saveState').textContent.startsWith('saved')", timeout=8000)


def _propose(pg, plan: dict) -> None:
    pg.evaluate("(p) => rcShowProposal(p)", plan)
    pg.wait_for_selector("#proposal", state="visible", timeout=5000)
    n = len(plan["segments"])
    pg.wait_for_function(f"document.querySelectorAll('{GHOST} .ghost').length === {n}",
                         timeout=8000)


def _put_selects(pg, selects: list[dict]) -> None:
    out = pg.evaluate("""(selects) => fetch('/api/selects', {
        method: 'PUT', headers: {'content-type': 'application/json'},
        body: JSON.stringify({selects})}).then(r => r.json())""", selects)
    assert out.get("ok"), out


def _toast(pg, part: str) -> None:
    pg.wait_for_function("(t) => document.querySelector('#toast').textContent.includes(t)",
                         arg=part, timeout=5000)


FIVE = {
    "segments": [
        {"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0, "why": "open on the hello"},
        {"clip": "CLIP_B.MP4", "in": 0.0, "out": 1.5, "why": "the reply"},
        {"clip": "CLIP_C.MP4", "in": 1.0, "out": 3.0, "why": "the ride starts"},
        {"clip": "CLIP_A.MP4", "in": 4.0, "out": 5.5, "why": "the goodbye"},
        {"clip": "CLIP_B.MP4", "in": 3.0, "out": 4.5, "why": "close on the view"},
    ],
    "notes": "a first cut in five shots",
    "firstpass": {"diff": {"shots": [{"pos": i + 1, "status": "new", "locked": False}
                                     for i in range(5)]}},
}


# ------------------------------------------------------------------ per-shot review

def test_accepting_2_of_5_proposed_shots_puts_exactly_those_2_on_v1_in_one_undo(
        browser, live_server, project):
    _seed(project, [])
    pg = _open(browser, live_server)
    try:
        assert pg.evaluate("segs.length") == 0
        _propose(pg, FIVE)
        ghosts = pg.locator(f"{GHOST} .ghost")
        # every shot starts accepted, each has its ✓ / ✗, and its why is on the hover
        assert pg.locator(f"{GHOST} .ghost button.rv.ok[aria-pressed=true]").count() == 5
        assert pg.locator(f"{GHOST} .ghost button.rv.no").count() == 5
        title = ghosts.nth(2).get_attribute("title")
        assert "why: the ride starts" in title and "new" in title, title
        assert pg.locator("#acceptProposal").inner_text() == "Accept"
        assert not pg.locator("#acceptAll").is_visible()

        # ✗ on shot 2 by its button
        ghosts.nth(1).locator("button.rv.no").click()
        assert "rejected" in ghosts.nth(1).get_attribute("class")
        assert pg.locator("#acceptProposal").inner_text() == "Accept 4 of 5"
        assert pg.locator("#acceptAll").is_visible()
        # the panel's row says so too
        assert "rejected" in pg.locator("#proposalDiff .pshot").nth(1).get_attribute("class")
        # Enter accepts the focused shot and moves on; ⌫ rejects it and moves on
        pg.evaluate(f"document.querySelectorAll('{GHOST} .ghost')[2].focus()")
        pg.keyboard.press("Enter")
        assert pg.evaluate("document.activeElement.dataset.k") == "3"
        pg.keyboard.press("Backspace")
        assert pg.evaluate("document.activeElement.dataset.k") == "4"
        pg.keyboard.press("Backspace")
        # the keys were the review's: the cut and the playhead are untouched
        assert pg.evaluate("segs.length") == 0
        assert [("rejected" in (c or "")) for c in pg.eval_on_selector_all(
            f"{GHOST} .ghost", "els => els.map(e => e.className)")] == [False, True, False, True, True]
        assert pg.locator("#acceptProposal").inner_text() == "Accept 2 of 5"
        _shot(pg, "01-review-2-of-5")

        pg.locator("#acceptProposal").click()
        assert _ranges(pg) == [["CLIP_A.MP4", 0.5, 2.0], ["CLIP_C.MP4", 1.0, 3.0]]
        assert pg.locator("#tl .blk").count() == 2
        assert pg.evaluate(f"document.querySelector('{GHOST}').hidden") is True
        _toast(pg, "applied 2 of 5 shots")
        _wait_saved(pg)
        assert _on_disk(project) == [["CLIP_A.MP4", 0.5, 2.0], ["CLIP_C.MP4", 1.0, 3.0]]
        _shot(pg, "02-accepted-2")
        # one undo takes the whole accept back
        pg.evaluate("document.activeElement && document.activeElement.blur()")
        pg.keyboard.press("Control+z")
        pg.wait_for_function("segs.length === 0", timeout=5000)
        assert pg.locator("#tl .blk").count() == 0
        assert pg.errors == []
    finally:
        pg.close()


def test_a_rejected_change_stays_as_it_is_a_kept_removal_stays_and_flags_show(
        browser, live_server, project):
    """The cut A 1–3, B 0–2, C 4.5–5.5; the proposal trims A (and its notes call the rest
    unchanged — the notes check flags A), keeps B, drops C 4.5 and adds C 0.5–4. A ✗ on
    A leaves A at 1–3; the ↺ on the struck C keeps it after B; Accept is one undo. Then
    Accept all takes every shot whatever was rejected."""
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
            "notes": "Tightened the open; the rest is unchanged.",
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
        _propose(pg, plan)
        ghosts = pg.locator(f"{GHOST} .ghost")
        a = ghosts.nth(0)
        assert "flagged" in a.get_attribute("class")
        assert a.locator(".flag").count() == 1
        assert "the notes say “the rest is unchanged”" in a.get_attribute("title")
        assert "replaces 0:01.0–0:03.0" in a.get_attribute("title")
        assert "kept — your edit, locked" in ghosts.nth(1).get_attribute("title")
        assert "⚠" in pg.locator("#proposalDiff .pshot").nth(0).inner_text()
        strike = pg.locator("#tl .tl-over .strike")
        assert strike.count() == 1
        a.locator("button.rv.no").click()
        strike.locator("button.rv").click()
        assert "kept" in strike.get_attribute("class")
        assert pg.locator("#acceptProposal").inner_text() == "Accept 2 of 3"
        _shot(pg, "03-review-flags")
        pg.locator("#acceptProposal").click()
        assert _ranges(pg) == [["CLIP_A.MP4", 1.0, 3.0], ["CLIP_B.MP4", 0.0, 2.0],
                               ["CLIP_C.MP4", 4.5, 5.5], ["CLIP_C.MP4", 0.5, 4.0]]
        assert pg.evaluate("segs.slice(0, 3).map(s => s.id)") == ids
        pg.evaluate("document.activeElement && document.activeElement.blur()")
        pg.keyboard.press("Control+z")
        pg.wait_for_function("segs.length === 3", timeout=5000)
        assert _ranges(pg) == [["CLIP_A.MP4", 1.0, 3.0], ["CLIP_B.MP4", 0.0, 2.0],
                               ["CLIP_C.MP4", 4.5, 5.5]]

        # Accept all: every proposed shot, the rejected one too
        _propose(pg, plan)
        pg.locator(f"{GHOST} .ghost").nth(0).locator("button.rv.no").click()
        pg.locator("#acceptAll").click()
        assert _ranges(pg) == [["CLIP_A.MP4", 1.5, 3.0], ["CLIP_B.MP4", 0.0, 2.0],
                               ["CLIP_C.MP4", 0.5, 4.0]]
        # and Discard still discards
        pg.evaluate("document.activeElement && document.activeElement.blur()")
        pg.keyboard.press("Control+z")
        pg.wait_for_function("segs.length === 3 && segs[2].in === 4.5", timeout=5000)
        before = _ranges(pg)
        _propose(pg, plan)
        pg.locator("#rejectProposal").click()
        assert _ranges(pg) == before
        assert pg.errors == []
    finally:
        pg.close()


# ------------------------------------------------------------------ the Selects list

KEEPS = [
    {"clip": "CLIP_A.MP4", "start": 4.0, "end": 5.5, "hero": True, "why": "the goodbye"},
    {"clip": "CLIP_C.MP4", "start": 0.5, "end": 2.0, "why": "the ride starts"},
    {"clip": "CLIP_B.MP4", "start": 2.5, "end": 5.0, "why": "a long look at the view"},
]


def _selects(browser, live_server, project, size=(1440, 900)):
    _seed(project, TWO)
    pg = _open(browser, live_server, size)
    pg.wait_for_selector("#tl .blk")
    _put_selects(pg, KEEPS)
    pg.reload()
    pg.wait_for_function("window.tlLanes && typeof P !== 'undefined' && P && P.clips && window.tl && tl.state", timeout=20000)
    pg.wait_for_selector("#tl .blk")
    pg.evaluate("dock.open('bin')")
    pg.wait_for_selector("#library table.sel-list tr.keep")
    return pg


def _row(pg, clip: str):
    return pg.locator(f'#library tr.keep[title^="{clip}"]')


def test_selects_is_a_sortable_list_whose_rows_insert_by_comma_and_overwrite_by_period(
        browser, live_server, project):
    pg = _selects(browser, live_server, project)
    try:
        heads = pg.locator("#library table.sel-list th").all_inner_texts()
        assert heads[0] == "Name" and "Duration" in heads and heads[-1] == "Usage", heads
        rows = pg.locator("#library tr.keep")
        assert rows.count() == 3
        hero = _row(pg, "CLIP_A")
        assert hero.locator(".w").inner_text() == "★ “goodbye”"
        # what a narrow panel leaves out is in the row's hover: clip, range, reason
        assert hero.get_attribute("title").startswith("CLIP_A 0:04.0–0:05.5\nthe goodbye")
        assert hero.locator("td.t").inner_text() == pg.evaluate("tl.tc(1.5)")
        # sort by Duration, then the other way, then back to the bin's order (hero first)
        order = lambda: pg.eval_on_selector_all(   # noqa: E731
            "#library tr.keep", "els => els.map(e => e._keep.clip)")
        assert order() == ["CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"]
        pg.locator("#library th[data-sort=dur]").click()
        assert order() == ["CLIP_A.MP4", "CLIP_C.MP4", "CLIP_B.MP4"]       # 1.5, 1.5, 2.5
        pg.locator("#library th[data-sort=dur]").click()
        assert order()[0] == "CLIP_B.MP4"
        assert "desc" in pg.locator("#library th[data-sort=dur]").get_attribute("class")
        pg.locator("#library th[data-sort=dur]").click()
        assert order() == ["CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"]
        _shot(pg, "04-selects-list")

        # `,` inserts the selected row's range at the playhead
        _row(pg, "CLIP_C").click()
        assert "sel" in _row(pg, "CLIP_C").get_attribute("class")
        pg.evaluate("document.activeElement && document.activeElement.blur()")
        pg.evaluate("tl.seek(2.0)")
        pg.keyboard.press(",")
        assert _ranges(pg) == [["CLIP_A.MP4", 1.0, 3.0], ["CLIP_C.MP4", 0.5, 2.0],
                               ["CLIP_B.MP4", 0.0, 2.0]]
        _toast(pg, "from the bin")
        # `.` overwrites from the playhead with the selected row's range
        _row(pg, "CLIP_B").click()
        pg.evaluate("document.activeElement && document.activeElement.blur()")
        pg.evaluate("tl.seek(0)")
        pg.keyboard.press(".")
        assert _ranges(pg)[0] == ["CLIP_B.MP4", 2.5, 5.0]
        assert pg.evaluate("tl.total()") == pytest.approx(5.5)
        assert pg.errors == []
    finally:
        pg.close()


def test_a_wide_selects_panel_shows_every_column_in_timecode(browser, live_server, project):
    """The Project panel dragged wide (as an editor does for a list): Media In / Out in
    the clip's timecode, Reason and Clip join Name, Duration and Usage."""
    pg = _selects(browser, live_server, project, size=(1920, 1080))
    try:
        pg.evaluate("localStorage.setItem('roughcut.ws.layout', JSON.stringify({proj: 0.42}))")
        pg.reload()
        pg.wait_for_function("window.tlLanes && typeof P !== 'undefined' && P && P.clips", timeout=20000)
        pg.evaluate("dock.open('bin')")
        pg.wait_for_selector("#library table.sel-list tr.keep")
        heads = pg.locator("#library table.sel-list th").all_inner_texts()
        assert heads == ["Name", "Clip", "Media In", "Media Out", "Duration", "Reason", "Usage"], heads
        cells = _row(pg, "CLIP_B").locator("td").all_inner_texts()
        assert cells[1:6] == ["CLIP_B", pg.evaluate("tl.tc(2.5)"), pg.evaluate("tl.tc(5.0)"),
                              pg.evaluate("tl.tc(2.5)"), "a long look at the view"], cells
        pg.locator("#library th[data-sort=in]").click()
        assert pg.eval_on_selector_all("#library tr.keep", "els => els.map(e => e._keep.start)") \
            == [0.5, 2.5, 4.0]
        _shot(pg, "07-selects-wide")
        assert pg.errors == []
    finally:
        pg.evaluate("localStorage.removeItem('roughcut.ws.layout')")
        pg.close()


def test_a_selects_row_drags_onto_v1_and_double_click_opens_it_in_source_with_in_and_out(
        browser, live_server, project):
    pg = _selects(browser, live_server, project)
    try:
        row = _row(pg, "CLIP_C")
        pg.wait_for_function(
            "document.querySelector('#library tr.keep[title^=\"CLIP_C\"]').draggable === true",
            timeout=5000)
        # a native drag onto V1 at the very start: before shot 1
        box = row.bounding_box()
        sx, sy = box["x"] + 30, box["y"] + box["height"] / 2
        lane = pg.locator("#tl .tl-lane[data-lane=V1]").bounding_box()
        tx = pg.evaluate("(() => { const v = document.querySelector('#tl .tl-view');"
                         " return v.getBoundingClientRect().left + tl.timeToX(0.05) - v.scrollLeft; })()")
        pg.mouse.move(sx, sy)
        pg.mouse.down()
        pg.mouse.move(sx + 12, sy + 6, steps=4)
        pg.mouse.move(tx, lane["y"] + lane["height"] / 2, steps=12)
        pg.mouse.up()
        pg.wait_for_function("segs.length === 3", timeout=8000)
        assert _ranges(pg)[0] == ["CLIP_C.MP4", 0.5, 2.0]
        assert pg.evaluate("segs[0].why") == "the ride starts"

        # double-click: the pick in the Source monitor, its In and Out set
        _row(pg, "CLIP_B").dblclick()
        pg.wait_for_function("window.rcSource && rcSource.clip === 'CLIP_B.MP4'", timeout=5000)
        src = pg.evaluate("({...window.rcSource})")
        assert src["inPoint"] == pytest.approx(2.5) and src["outPoint"] == pytest.approx(5.0)
        assert not src.get("auto")
        assert pg.locator("#srcTitle").inner_text().strip() == "Source: CLIP_B.MP4"
        pg.wait_for_function("Math.abs(document.querySelector('#srcVideo').currentTime - 2.5) < 0.05",
                             timeout=10000)
        assert not pg.locator("#source .srcbar .range").is_hidden()
        _shot(pg, "05-pick-in-source")
        # `,` now takes the Source monitor's In–Out
        pg.evaluate("document.activeElement && document.activeElement.blur()")
        pg.evaluate("tl.seek(tl.total())")
        pg.keyboard.press(",")
        assert _ranges(pg)[-1] == ["CLIP_B.MP4", 2.5, 5.0]
        _toast(pg, "from the Source monitor")
        assert pg.errors == []
    finally:
        pg.close()


# ------------------------------------------------------------------ picks as markers

def test_picks_are_markers_on_the_cut_and_on_the_proposed_cut_with_the_reason_on_hover(
        browser, live_server, project):
    _seed(project, TWO)
    pg = _open(browser, live_server)
    try:
        pg.wait_for_selector("#tl .blk")
        _put_selects(pg, [{"clip": "CLIP_A.MP4", "start": 1.5, "end": 2.5, "hero": True,
                           "why": "the hello lands"},
                          {"clip": "CLIP_C.MP4", "start": 1.0, "end": 2.0, "why": "the ride"}])
        pg.evaluate("refreshBin()")
        # on V1: the pick inside shot 1 (A 1–3) starts 0.5 s into the film
        pg.wait_for_function("tlLanes.picks().filter(p => !p.ghost).length === 1", timeout=8000)
        v1 = [p for p in pg.evaluate("tlLanes.picks()") if not p["ghost"]]
        assert v1[0]["t"] == pytest.approx(0.5, abs=0.01)
        assert "pick ★ hero" in v1[0]["title"] and "why: the hello lands" in v1[0]["title"]
        mk = pg.locator("#tl .tl-picks .tl-pick")
        assert mk.count() == 1
        assert mk.get_attribute("title") == v1[0]["title"]
        x = pg.evaluate("parseFloat(document.querySelector('#tl .tl-picks .tl-pick').style.left)")
        assert x == pytest.approx(pg.evaluate("tl.timeToX(0.5)"), abs=0.5)
        # on the proposed cut: both picks, at their film time in the proposal
        _propose(pg, {"segments": [
            {"clip": "CLIP_C.MP4", "in": 0.5, "out": 3.0, "why": "open on the ride"},
            {"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "then the hello"}],
            "notes": "open on the ride"})
        pg.wait_for_function("tlLanes.picks().filter(p => p.ghost).length === 2", timeout=8000)
        on_ghost = sorted(p["t"] for p in pg.evaluate("tlLanes.picks()") if p["ghost"])
        assert on_ghost == [pytest.approx(0.5, abs=0.01), pytest.approx(3.0, abs=0.01)]
        assert pg.locator(f"{GHOST} .tl-pick[title*='why: the ride']").count() == 1
        _shot(pg, "06-picks-as-markers")
        pg.locator("#rejectProposal").click()
        assert pg.errors == []
    finally:
        pg.close()
