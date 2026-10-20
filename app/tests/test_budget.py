"""The screen budget (INTAKE M16, I16.1 — "take things away").

Karl, 2026-10-04: *"roughcut feels hard to use / with a bunch of buttons and text"* — the
fourth report of the same complaint, and every earlier answer added a surface (the UI
grew from 10 to 101 `<button`s). This file is the ratchet. At 1440×900, on the suite's
own fixture bin, read-only (it only loads pages):

  * every screen has ONE header row, at most 50 px: the bin · cut switcher (#hdBin) and
    the Next chip (#flow) in it, side by side;
  * exactly one blue button (`.is-next`, placed by /flow.js on Next's target or on the
    chip);
  * the board does not scroll — at rest, with a shot selected, with the film tool open,
    and in FX after Next (on the waiting proposal's shot), the three board states the
    M16 end state names — nor while jobs run, when the header is still one row;
  * with a shot selected, its strip is in view;
  * the words and controls in view stay under each screen's (and state's) number.

The words and controls are counted the way the M16 audit counted them on Killington
(the capture behind the proposal), so the numbers here and there mean the same thing.
A change that adds a surface has to take one away, or raise a number below — in review,
where it shows.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

# The editor is the one screen (wave 17 FOLDIN, Karl's K4: the footage screen /open and the
# pass /floor were folded into it and removed; their routes answer with the editor).
SCREENS = ("/",)
# The board in the states the M16 end state measures: a shot selected (its strip in
# view), the film tool open, and FX after Next — the waiting proposal's card, landed on.
BOARD = ("/", "/ shot", "/ film", "/ jobs", "/ fx")
STATES = SCREENS + BOARD[1:]
# The header's one row holds while work runs, not only at rest: a render and an Ask
# running and a find that just finished (the progress strip was a second row, 102 px
# for a whole render — the M16 wave B review).
HEADERS = SCREENS + ("/ jobs",)
VIEWPORT = {"width": 1440, "height": 900}
HEADER_MAX_PX = 50

# Words and controls in view, at 1440×900 on the fixture bin: what the merged M16 stage
# 1–4 screens measured (INTAKE M16 integration, 2026-10-05) plus 10 %, so growth fails.
# Measured (words · controls): / 72 · 18, /floor 52 · 7, /open 53 · 8, / shot 118 · 30,
# / film 67 · 17, / fx 118 · 31, / jobs 98 · 18 (two running jobs in the header's row,
# measured in the M16 wave B review) — before stage 1, on b620ff7: / 197 · 26, /floor
# 350 · 12, /open 454 · 19. Lower a number when a screen gets quieter; raise one only in
# review, saying what the new words are for. The end state (Killington, not this
# fixture): ≲ 100 words on /open and the pass, ≲ 220 on the board with a shot
# selected, ≲ 200 in FX after Next, ≲ 180 with the film tool open.
# Raised in review on the closed-loop branch (2026-10-13, ROUGHCUT-GOAL.md dimension 2):
# with nothing kept the Bin lists the footage, one card per clip, so a bin nobody has
# passed can still be cut — the fixture's three cards add 12 words (their first lines):
# / 72→84, / shot 118→130, / jobs 98→110; +10 % on those.
# Raised in review on the redesign (2026-10-14, REDESIGN-GOAL.md dimensions 4 and 9, wave 1
# lane WORKSPACE): the board is Premiere's five-panel workspace now. What is new in view
# on "/" at 1440×900, measured: the menu bar (File · Edit · Clip · Sequence · Window ·
# Help, 6), the Project panel (its two tabs, list / icon, the search box, a row per clip:
# 4 controls, ~12 words), the Source monitor's transport (In, Out, ◀, ▶, ▶|, Insert: 6)
# and the Program's edit-to-edit buttons (2), and the Effect Controls group's five tabs
# (5) where the rail was (5 off). Measured: / 85 · 34, / shot 140 · 46, / film 94 · 33,
# / jobs 111 · 34, / fx 117 · 37; the numbers below are those plus ~10 %, so growth still
# fails. /floor and /open are unchanged.
# After lane KEYS merged (the timeline's timecode column and HH:MM:SS:FF ruler labels),
# "/" measured 96 words: 94 → 105.
# Lane TRACKS (wave 2, verdict 2 punch 3): the Timeline's four track headers are 14
# controls (lock · name/target · eye on V1/V2; lock · name · M · S on A1/A2) and ~9
# words (V2 V1 A1 M S A2 M S, the bed's name on A2). Measured after it: / 103 · 49,
# / shot 154 · 61, / film 111 · 48, / jobs 129 · 49, / fx 131 · 52; the board's numbers
# Lead, at merge: the board's numbers below are the MEASURED counts on the merged tree with no
# headroom (TRACKS + FPUI together; see REDESIGN-GOAL's rubric log), so any growth fails.
# Wave 3, lane HEADER (verdict 3 punch 8): a running job is an icon and its % with the
# name in its hover, and Next is its icon while anything runs, so "/ jobs" measured 109
# words (was 132): lowered to that, no headroom.
# Wave 4, lane POLISH (verdict 4 punches 7 and 10), measured on the tree merged with
# EXPORTSTOP and PICKS2, no headroom: "/ shot" 181 words (was 196; the judge measured 189):
# the strip's play / Ask / Look deeper / Remove / why / quote fold into its collapsed
# "Assistant" section. "/ jobs" 108 words and 51 controls (was 109 · 50): Next keeps its
# words while a job runs (+), the fixture's running Ask no longer toasts "the ask that was
# running failed: no such job" (−8), and EXPORTSTOP's × on the render pill (+1 control).
# Wave 4, lane LAYOUT (verdict 4 punch 3), measured on the tree merged with POLISH, no
# headroom: the monitors take the whole top row and Effect Controls moved to the bottom
# row, where a long strip scrolls inside its panel (as Premiere's does), so less of it is
# in view: words / 106 → 93, / shot 181 → 119, / film 114 → 101, / fx 134 → 124,
# / jobs 108 → 102; controls / shot 85 → 61; the other controls unchanged.
# Wave 6, lane KEYUI (verdict 5 punch 4), measured, no headroom: the stopwatches on Position,
# Scale, Rotation and Opacity are in view with a shot selected (Volume's is below the fold):
# controls / shot 61 → 65. Words unchanged (119); Lumetri's sliders sit below the fold.
# Wave 8, lane SMALL (verdict 6 punch 10a), measured, no headroom: Premiere's Playback
# Resolution menu on the Program's transport (Auto / Full / 1/2 / 1/4) is one control on every
# board screen: controls / 50 → 51, / shot 65 → 66, / film 49 → 50, / fx 53 → 54, / jobs
# 51 → 52. Words unchanged (the closed menu's "Auto" is not counted).
# Wave 11, lane LUMETRI (verdict 8 punch 4), measured, no headroom: Premiere's "Lumetri Scopes"
# tab beside the Source monitor's (where the Color workspace puts it, so the scopes and the
# Program are seen together) is 2 words on every board screen: / 93 → 95, / shot 119 → 121,
# / film 101 → 103, / fx 124 → 126, / jobs 102 → 104; and 1 control on each: / 51 → 52,
# / shot 66 → 67, / film 50 → 51, / fx 54 → 55, / jobs 52 → 53. Curves, Color Wheels and
# HSL Secondary sit below the fold, closed.
# Wave 11, lane FXBIN (verdict 8 punch 5), measured, no headroom: Premiere's Effects bin is
# the Project group's third tab ("Effects": one control, one word, on every board screen; its
# tree is in view only while the tab is open) and the AI designer's tab reads "Designed
# Effects" (one more word): words / 93 → 95, / shot 119 → 121, / film 101 → 103, / fx 124 →
# 126, / jobs 102 → 104; controls / 51 → 52, / shot 66 → 67, / film 50 → 51, / fx 54 → 55,
# / jobs 52 → 53. /floor and /open are unchanged.
# Both wave 11 lanes together (the lead's merge, measured): / 97 · 53, / shot 123 · 68,
# / film 105 · 52, / fx 128 · 56, / jobs 106 · 54 (each lane's +2 words / +1 control above).
# Wave 12 scaffold (K3, REDESIGN-GOAL dimension 14), measured, no headroom: the menu bar's "AI"
# menu (every AI action in one place) is one word and one control on every board screen:
# / 97 → 98, / shot 123 → 124, / film 105 → 106, / fx 128 → 129, / jobs 106 → 107; controls
# / 53 → 54, / shot 68 → 69, / film 52 → 53, / fx 56 → 57, / jobs 54 → 55.
# Wave 12, lane AIPROMPT (K3, REDESIGN-GOAL dimension 14), measured, no headroom: the always-visible
# AI prompt in the Timeline panel's header row is two controls (its input and its scope chip) and
# two words (the chip's scope, "Sequence" or the selected clip's name, and the focus key's hint
# "Ctrl+J"; the example asks are the input's placeholder, not text) on every board screen:
# words / 98 → 100, / shot 124 → 126, / film 106 → 108, / fx 129 → 131, / jobs 107 → 109;
# controls / 54 → 56, / shot 69 → 71, / film 53 → 55, / fx 57 → 59, / jobs 55 → 57. The status
# line beside it is empty at rest.
# Wave 12, lane BINLOOK (K3 a, REDESIGN-GOAL dimension 16's look), measured, no headroom: the
# Project panel is Premiere's, so the bin's cut is a sequence in it (the fixture's one cut,
# "edl", is a row: its name, its duration and "open", 3 words on every board screen) and its
# bottom bar carries Premiere's Import (1 control; List / Icon moved down beside it from the
# panel's head, the item count with them, so no other change): words / 98 → 101, / shot 124 →
# 127, / film 106 → 109, / fx 129 → 132, / jobs 107 → 110; controls / 54 → 55, / shot 69 → 70,
# / film 53 → 54, / fx 57 → 58, / jobs 55 → 56. The icon-size slider shows in Icon view only.
# Both wave 12 lanes together (the lead's merge, measured): / 103 · 57, / shot 129 · 72,
# / film 111 · 56, / fx 134 · 60, / jobs 112 · 58 (AIPROMPT's +2 · +2 and BINLOOK's +3 · +1).
# / jobs measured 112 · 58 and, on another run, under it by one (the job pill's text timing):
# the limit is the larger, measured, count.
# Wave 13 lane AIUX (verdict 9, punch 7: the chip speaks the timeline's language): with a
# block selected the AI prompt's scope chip reads "Shot 1 · CLIP_A" where it read "CLIP_A",
# the review's own name for the block: +3 words on the two screens with a shot selected,
# measured: / shot 129 → 132, / fx 134 → 137. Controls unchanged.
# Wave 13, lane BINLOOK2 (verdict 9 punch 9), measured, no headroom: the Project panel's List
# view keeps every one of Premiere's columns and scrolls them sideways under its pinned Name,
# so at 1440 the panel shows Name, Frame Rate and the start of Media Start where it showed
# Name, Duration and Status: the headings +2 words ("Frame Rate", "Media Start" for
# "Duration", "Status") and each of the 4 rows +1 ("24 fps" and its Media Start for its
# duration and its status): / 103 → 109, / shot 129 → 135, / film 111 → 117, / fx 134 → 140,
# / jobs 112 → 118. Controls unchanged. The count no longer takes a cell scrolled out of a
# scroller as in view (INVENTORY_JS's `inView`; the columns past the panel's edge are inside
# the window but unseen): on b7848ec that rule measures exactly the numbers above it.
# Both wave 13 lanes together (the lead's merge, measured): / 109, / shot 138, / film 117,
# / fx 143, / jobs 118 (AIUX's +3 on the two shot screens and BINLOOK2's +6 on all five).
# Wave 17, lane INDEXPANEL (Karl's K4: the Index panel), measured, no headroom: the Project
# group's fourth tab "Index" is one word and one control on every board screen (its panel is
# in view only while the tab is open; Window › Index sits in a closed menu): words / 109 →
# 110, / shot 138 → 139, / film 117 → 118, / fx 143 → 144, / jobs 118 → 119; controls / 57 →
# 58, / shot 72 → 73, / film 56 → 57, / fx 60 → 61, / jobs 58 → 59. /floor and /open are
# unchanged.
# Wave 17, lane FOLDIN (Karl's K4 b, verdict 13 punch 2): /floor (58 · 8) and /open (59 · 9)
# are gone from the budget with the screens themselves, so the budget measures the editor
# alone. Nothing on the board's five states changed in view (the File menu's items and
# Selects' rows are not in view at rest); measured, no headroom.
WORDS_IN_VIEW = {"/": 110, "/ shot": 139, "/ film": 118, "/ fx": 144, "/ jobs": 119}
CONTROLS_IN_VIEW = {"/": 58, "/ shot": 73, "/ film": 57, "/ fx": 61, "/ jobs": 59}

# Contracts another lane delivers (M16 stage 1, C1): until that lane is merged the
# check is an expected failure on that screen. Every stage-1 lane is merged, so this is
# empty — the end state. A check listed here that passes fails, so an entry added for a
# lane in flight cannot outlive it and hide a regression later.
AWAITS: dict[tuple[str, str], str] = {}

# The same counting as the M16 audit's capture: a control is anything clickable or
# typeable that is laid out and visible; words are the visible text nodes whose element
# is in the viewport.
INVENTORY_JS = r"""
() => {
  const vis = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  };
  // In the window, and not scrolled out of a scroller it sits in (wave 13 BINLOOK2: the
  // Project list's columns scroll sideways under its pinned Name; a cell scrolled past the
  // panel's edge is inside the window but nobody sees it). A fixed element escapes every
  // scroller; an absolute one is clipped only by a scroller it is positioned inside.
  const inView = el => { const r = el.getBoundingClientRect();
    if (!(r.top < innerHeight && r.bottom > 0 && r.left < innerWidth && r.right > 0)) return false;
    let pos = getComputedStyle(el).position;
    for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
      if (pos === 'fixed') return true;
      const s = getComputedStyle(a);
      const scrolls = /(auto|scroll)/.test(s.overflowX + s.overflowY);
      if (scrolls && (pos !== 'absolute' || s.position !== 'static')) {
        const b = a.getBoundingClientRect();
        if (!(r.top < b.bottom && r.bottom > b.top && r.left < b.right && r.right > b.left)) return false;
      }
      if (pos === 'absolute' && s.position !== 'static') pos = s.position;
      else if (pos !== 'absolute') pos = s.position;
    }
    return true; };
  const ctrls = [...document.querySelectorAll('button, a[href], input, select, textarea, [role=button], summary, label.chip, .chip')].filter(vis);
  let words = 0;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const p = walker.currentNode.parentElement;
    if (!p || !vis(p) || !inView(p)) continue;
    words += (walker.currentNode.textContent.trim().match(/\S+/g) || []).length;
  }
  // The header row: the nearest element holding both the switcher and the Next chip.
  const bin = document.getElementById('hdBin'), flow = document.getElementById('flow');
  let hd = bin;
  while (hd && flow && !hd.contains(flow)) hd = hd.parentElement;
  const box = el => { if (!el) return null; const r = el.getBoundingClientRect();
    return {top: r.top, bottom: r.bottom, height: r.height}; };
  const chip = document.getElementById('flowNext');
  return {
    controls_in_view: ctrls.filter(inView).length,
    controls: ctrls.filter(inView).map(el => (el.innerText || el.value || el.id || el.tagName).trim().replace(/\s+/g, ' ').slice(0, 40)),
    words_in_view: words,
    header: box(hd), header_tag: hd ? (hd.id || hd.tagName.toLowerCase()) : null,
    bin: box(bin), chip: box(chip), chip_text: chip ? chip.innerText.trim() : null,
    strip: box(document.getElementById('inspector')),
    // how much of the strip its panel shows (Effect Controls scrolls, as Premiere's does)
    strip_seen: (() => { const el = document.getElementById('inspector'), body = el && el.closest('.ws-body');
      if (!el || !body) return 0; const a = el.getBoundingClientRect(), b = body.getBoundingClientRect();
      return Math.max(0, Math.min(a.bottom, b.bottom, innerHeight) - Math.max(a.top, b.top, 0)); })(),
    // the picture in its room (the screenbox): the meters stand beside the room, not in it
    monitor: (() => { const sc = document.querySelector('#player .screen'), pl = document.getElementById('player');
      if (!sc || !pl || !sc.getClientRects().length) return null;
      const a = sc.getBoundingClientRect();
      const box = sc.parentElement.getBoundingClientRect();
      return {left: a.left - box.left, right: box.right - a.right, top: a.top - box.top,
              bottom: box.bottom - a.bottom, width: a.width, height: a.height,
              room_w: box.width, room_h: box.height}; })(),
    blue: [...document.querySelectorAll('.is-next')].map(el => el.id || el.dataset.nextFor || el.tagName),
    jobs: [...document.querySelectorAll('#progress .job')].map(el => { const r = el.getBoundingClientRect();
      return {id: el.dataset.job, top: r.top, bottom: r.bottom, visible: vis(el)}; }),
    scroll_h: document.documentElement.scrollHeight, inner_h: innerHeight,
    steps: document.querySelectorAll('#flow [data-stage], #flow .fs').length,
    flow_links: [...document.querySelectorAll('#flow a')].length,
  };
}
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


SEED = {"variant": "T", "title": "test cut", "orient": "none", "story": "",
        "target_s": [5, 20],
        # ids in the file: the FX state's proposal names its shot by id, and the fx
        # endpoints look a shot up in the file (a read mints fresh ids per GET)
        "segments": [{"id": "budget0001", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
                     {"id": "budget0002", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}]}

# The waiting proposal FX after Next lands on: Killington's kind (I16.0a), a slow
# motion on shot 2 that changes the cut, already checked so nothing is asked of the
# server while the page is read.
PROPOSAL = {
    "id": "fx_budget01", "shot": "budget0002", "clip": "CLIP_B.MP4", "name": "slow motion",
    "note": "slow the turn down", "why": "the turn is the moment; half speed lets it land",
    "events": [], "status": "proposed",
    "edits": [{"op": "speed", "shot": "budget0002", "rate": 0.5, "from": 1.0, "to": 1.4}],
    "created": "2026-10-05T09:00:00",
    "verify": {"ok": True, "at": "2026-10-05T09:00:05",
               "checks": [{"key": "edits_apply", "label": "the edits fit the cut as it stands",
                           "ok": True, "detail": ""}]},
}


# The jobs the board's header shows in "/ jobs", answered in place of /api/jobs: two
# running (a render and an Ask overlap routinely) and one that just finished well.
JOBS = [
    {"id": "bud_render", "kind": "render", "state": "running", "label": "Rendering — preview",
     "milestone": "cutting the shots", "detail": "4 of 12", "pct": 32.0, "elapsed_s": 4.0,
     "eta_s": 8.0, "started": 1.0, "milestones": []},
    {"id": "bud_ask", "kind": "ask", "state": "running", "label": "Cutting from your note",
     "milestone": "choosing the shots", "detail": "3 shots decided", "pct": 40.0,
     "elapsed_s": 20.0, "eta_s": 30.0, "started": 2.0, "milestones": []},
    {"id": "bud_done", "kind": "find", "state": "done", "label": "Finding in the footage",
     "detail": "3 matches", "pct": 100.0, "elapsed_s": 6.0, "eta_s": None, "started": 0.5,
     "milestones": []},
]


def _read_only(route):
    """Nothing this test does may write. GETs pass, and the two POSTs that only price
    (Look deeper's quote and its dry run) — without them the shot's strip would read
    "price unavailable", which is not what the screen says."""
    req = route.request
    if req.method in ("GET", "HEAD") or req.url.endswith("/api/deep/quote"):
        return route.continue_()
    try:
        body = json.loads(req.post_data or "{}")
    except ValueError:
        body = {}
    if req.url.endswith("/api/deep") and isinstance(body, dict) and body.get("dry_run") is True:
        return route.continue_()
    return route.abort()


def _set_up(pg, state: str) -> None:
    """Put the page in `state` the way Karl would, then let it settle."""
    if state == "/ shot":
        pg.wait_for_selector("#tl .blk", timeout=15000)
        pg.locator("#tl .blk").nth(0).click()               # select and park (decision 6)
        pg.wait_for_function("tl.state.sel.size === 1", timeout=5000)
    elif state == "/ film":
        pg.wait_for_selector("#tl .blk", timeout=15000)
        # Window › Export: the films panel (the header's Export opens the Export dialog)
        pg.locator("#menubar .menu[data-menu=window] .mtop").click()
        pg.locator("#menubar button[data-cmd='w:out']").click()
        pg.wait_for_function("dock.current() === 'out'", timeout=5000)
    elif state == "/ fx":
        pg.wait_for_selector("#tl .blk", timeout=15000)
        pg.wait_for_function("(() => { const n = flowBar.state().next;"
                             " return !!n && !!n.target && n.target.fx === 'fx_budget01'; })()",
                             timeout=15000)
        pg.locator("#flowNext").click()                     # Next lands on the proposal
        pg.wait_for_function("dock.current() === 'fx' && window.fx && fx.state.shot === 'budget0002'"
                             " && !!document.querySelector(\"#fx .fxcard[data-id='fx_budget01']\")",
                             timeout=15000)
    elif state == "/ jobs":
        pg.wait_for_selector("#tl .blk", timeout=15000)
        pg.route("**/api/jobs", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps({"jobs": JOBS})))
        # the board re-attaches to a running Ask (/api/ask/<id>): answered as the job
        # still running, as the server would — it used to 404, and the gate's jobs.png
        # carried the toast "the ask that was running failed: no such job" (verdict 4)
        pg.route("**/api/ask/bud_*", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(next(j for j in JOBS if r.request.url.endswith("/" + j["id"])))))
        pg.evaluate("pollJobs()")
        pg.wait_for_selector("#progress .job[data-job=bud_ask]", timeout=5000)


def _quiet_jobs(server) -> None:
    """Jobs another test module finished linger on /api/jobs for 12 s, and the board's
    header shows them as its progress strip (a second row, measured 243 px and +87 words
    after test_ask_*): that module's work, not this screen. Finished jobs go; one still
    running stays (its thread still writes to its entry)."""
    from roughcut import progress
    for reg in (server.ANALYSES, server.VISUALS, server.INDEXES, server.ASKS, server.FINDS,
                server.THEMES, server.FX, server.RENDERS, server.DEEPS):
        for k in [k for k, j in list(reg.items()) if j["state"] in progress.TERMINAL]:
            reg.pop(k, None)


@pytest.fixture(scope="module")
def measured(project, tmp_path_factory):
    """Each screen and board state loaded once at 1440×900 and counted after the page
    has settled — the same 3 s the audit's capture waited — with the flow answered and
    drawn. FX after Next is last: its waiting proposal changes Next on every screen.
    `ROUGHCUT_BUDGET_SHOTS=<dir>` also saves a screenshot of each."""
    import os
    import uvicorn
    import server
    from roughcut import fx

    original = project["edl"].read_text(encoding="utf-8")
    Path(project["edl"]).write_text(json.dumps(SEED, indent=1), encoding="utf-8")
    # Its own work dir: the session's shared one holds whatever earlier modules left —
    # test_ui_flow's renders make "the film's quick look" done, so Next became "Index
    # what isn't done", which the first pass covers, and no chip was drawn (the
    # test-order dependence: this module failed after test_ui_flow in one process).
    work = tmp_path_factory.mktemp("budget-work")
    server.configure(project["edl"], project["footage"], project["sidecars"],
                     work, proxies=False, assets=project["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in project["stems"]])
    shots = os.environ.get("ROUGHCUT_BUDGET_SHOTS")
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started, "server did not start"
    base = f"http://127.0.0.1:{port}"
    out = {}
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
            for state in STATES:
                if state == "/ fx":
                    fx.save(server.fx_home(), PROPOSAL)
                path = state.split(" ")[0]
                _quiet_jobs(server)
                pg = b.new_page(viewport=VIEWPORT)
                pg.route("**/*", _read_only)
                # the screen, not the host: a CLI this machine cannot find would put the
                # banner (its own test is test_flow_ui's) above every page and its words
                # into every count
                pg.route("**/api/backend", lambda r: r.fulfill(
                    status=200, content_type="application/json",
                    body=json.dumps({"state": "ok", "fix": None})))
                errors = []
                pg.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                t0 = time.monotonic()
                pg.goto(base + path, wait_until="domcontentloaded")
                pg.wait_for_function("window.flowBar && flowBar.state()", timeout=15000)
                pg.wait_for_selector("#flowNext", timeout=15000)
                ready_ms = round((time.monotonic() - t0) * 1000)
                _set_up(pg, state)
                pg.wait_for_timeout(3000)
                m = pg.evaluate(INVENTORY_JS)
                m["next"] = pg.evaluate("flowBar.state().next")
                m["toast"] = pg.evaluate("(() => { const t = document.getElementById('toast');"
                                         " return t && t.classList.contains('show') ? t.textContent : ''; })()")
                m["ready_ms"], m["errors"] = ready_ms, errors
                out[state] = m
                print(f"\nbudget {state:7s} words {m['words_in_view']:4d} · controls "
                      f"{m['controls_in_view']:3d} · header {m['header'] and round(m['header']['height'])} px"
                      f" ({m['header_tag']}) · page {m['scroll_h']} / {m['inner_h']} · blue {m['blue']}"
                      f" · chip {m['chip_text']!r} · ready {ready_ms} ms · errors {len(errors)}")
                print(f"  controls: {m['controls']}")
                if shots:
                    Path(shots).mkdir(parents=True, exist_ok=True)
                    pg.screenshot(path=str(Path(shots) / (state.strip("/ ").replace(" ", "-") or "board")) + ".png")
                pg.close()
            b.close()
    finally:
        srv.should_exit = True
        thread.join(timeout=10)
        project["edl"].write_text(original, encoding="utf-8")
        shutil.rmtree(server.fx_home() / PROPOSAL["id"], ignore_errors=True)
        for f in Path(server.fx_home()).glob(f"{PROPOSAL['id']}*"):
            f.unlink(missing_ok=True)
        server.configure(project["edl"], project["footage"], project["sidecars"],
                         project["work"], proxies=False, assets=project["assets"])
    return out


def awaits(check: str, path: str) -> None:
    why = AWAITS.get((check, path))
    if why:
        pytest.xfail(why)


def landed(check: str, path: str) -> None:
    if (check, path) in AWAITS:
        pytest.fail(f"{check} on {path} passes now: take it out of AWAITS ({AWAITS[(check, path)]})")


@pytest.mark.parametrize("path", HEADERS)
def test_one_header_row_at_most_50_px(measured, path):
    m = measured[path]
    assert m["bin"] and m["chip"], f"{path}: the switcher and the Next chip are both in the header"
    try:
        assert m["header"]["height"] <= HEADER_MAX_PX, (path, m["header_tag"], m["header"])
        # side by side: one row, not the chip on a row of its own under the name
        mid = lambda b: (b["top"] + b["bottom"]) / 2   # noqa: E731
        assert abs(mid(m["bin"]) - mid(m["chip"])) < 12, (path, m["bin"], m["chip"])
    except AssertionError:
        awaits("header", path)
        raise
    landed("header", path)


# Where the one blue sits in each state (C2): Next's target when it is on screen, else
# the chip. On the fixture Next is the film's quick look until FX's proposal waits.
BLUE_ON = {"/ film": ["render"], "/ fx": ["polish"]}


@pytest.mark.parametrize("state", STATES)
def test_exactly_one_blue_button(measured, state):
    m = measured[state]
    assert m["next"], f"{state}: the fixture bin always has a next action"
    assert len(m["blue"]) == 1, (state, m["blue"])
    assert m["blue"] == BLUE_ON.get(state, ["flowNext"]), (state, m["blue"], m["next"])


@pytest.mark.parametrize("state", BOARD)
def test_the_board_does_not_scroll(measured, state):
    m = measured[state]
    try:
        assert m["scroll_h"] <= m["inner_h"] + 2, (state, m["scroll_h"], m["inner_h"])
    except AssertionError:
        awaits("scroll", state)
        raise
    landed("scroll", state)


def test_running_jobs_sit_in_the_header_row_and_a_finished_one_leaves(measured):
    """The progress strip was the header's second row, full width, one row per job, and
    a finished job lingered 12 s saying "done": 102 px for a whole render, and the board
    jumped 55 px when it cleared. Each running job is one compact line in the row now;
    one that finished well has no line (its result is on the screen)."""
    m = measured["/ jobs"]
    rows = m["jobs"]
    assert [r["id"] for r in rows] == ["bud_render", "bud_ask"], rows
    mid = lambda b: (b["top"] + b["bottom"]) / 2   # noqa: E731
    for r in rows:
        assert r["visible"] and abs(mid(r) - mid(m["bin"])) < 12, (r, m["bin"])
        assert r["bottom"] <= m["header"]["bottom"], (r, m["header"])


def test_the_monitor_is_centred_and_as_big_as_the_strip_leaves(measured):
    """The picture sat left in its panel with a ~270 px gutter on the right, 716 × 403 at
    1440 × 900, with ~185 px empty under the film row: it is centred now, and the room
    reserved under it is what a selected shot's strip measures, not more. In the
    workspace (redesign wave 1) the screen is the Program panel's width and the strip is
    in Effect Controls, so the same three facts hold: centred in its room (the meters
    stand beside the room, wave 4), big at 1440 × 900, and selecting a shot does not
    resize it."""
    m = measured["/"]["monitor"]
    assert m and abs(m["left"] - m["right"]) <= 2 and abs(m["top"] - m["bottom"]) <= 2, m
    # the 16:9 picture fills its panel's room one way or the other (was: >= 425 px tall,
    # the old single-monitor board's number; see REDESIGN-GOAL's rubric log)
    assert m["width"] >= m["room_w"] - 2 or m["height"] >= m["room_h"] - 2, m
    # ...and the default layout gives it room worth filling: >= 560 px wide, with at most
    # 30 % of its room in black letterbox (rubric log: 380 px in wave 2, when the monitors
    # had a quarter of the width each; 560 px in wave 4, verdict 4 punch 3, once they
    # share the whole top row as Premiere's do)
    assert m["width"] >= 560 and 1 - m["height"] / m["room_h"] <= 0.30, m
    assert measured["/ shot"]["monitor"]["height"] == m["height"], "selecting a shot does not resize it"


def test_the_selected_shot_is_in_view(measured):
    """INTAKE M16 I16.4: the selected shot's strip sits under the timeline, in view —
    it used to start at y≈953, below the fold."""
    m = measured["/ shot"]
    # Wave 4: Effect Controls is in the bottom row (Premiere's monitors take the top row),
    # so a long strip scrolls inside its panel as Premiere's does; the strip starts in
    # view and its panel shows at least 300 px of it (rubric log, 2026-10-15 wave 4)
    assert m["strip"]["height"] > 0 and m["strip"]["top"] < m["inner_h"], m["strip"]
    assert m["strip_seen"] >= 300, (m["strip"], m["strip_seen"])


@pytest.mark.parametrize("state", STATES)
def test_words_and_controls_in_view_stay_under_the_screens_numbers(measured, state):
    m = measured[state]
    assert m["words_in_view"] <= WORDS_IN_VIEW[state], (state, m["words_in_view"])
    assert m["controls_in_view"] <= CONTROLS_IN_VIEW[state], (state, m["controls_in_view"],
                                                              m["controls"])


@pytest.mark.parametrize("state", STATES)
def test_no_failure_toast(measured, state):
    """Verdict 4 (and 2, 3): the gate's jobs.png showed "the ask that was running failed:
    no such job" — the fixture's running Ask was not one the server knew."""
    assert not re.search(r"fail", measured[state]["toast"] or "", re.I), (state, measured[state]["toast"])


def test_no_step_bar_on_any_screen(measured):
    """M16 decision 2: the seven-step bar came off every screen; Next is the one
    "what now". The header's own budget would hide a bar that wrapped off-screen, so
    this says it outright."""
    for path, m in measured.items():
        assert m["steps"] == 0, (path, m["steps"])
        assert m["flow_links"] == 1, (path, "#flow holds the Next chip and nothing else")


# ROUGHCUT-GOAL.md dimension 1, "opens fast, lands somewhere": the screen's Next is drawn
# within this long of the request, on the fixture. Generous on purpose — the gate runs
# beside other jobs on a shared box — so a failure means something got slow, not noise.
READY_MS = 3000


@pytest.mark.parametrize("path", SCREENS)
def test_every_screen_shows_next_within_3_s(measured, path):
    assert measured[path]["ready_ms"] <= READY_MS, (path, measured[path]["ready_ms"])


@pytest.mark.parametrize("state", STATES)
def test_no_uncaught_page_errors(measured, state):
    assert measured[state]["errors"] == [], (state, measured[state]["errors"])
