"""The screen budget (INTAKE M16, I16.1 — "take things away").

Karl, 2026-10-04: *"roughcut feels hard to use / with a bunch of buttons and text"* — the
fourth report of the same complaint, and every earlier answer added a surface (the UI
grew from 10 to 101 `<button`s). This file is the ratchet. At 1440×900, on the suite's
own fixture bin, read-only (it only loads pages):

  * every screen has ONE header row, at most 50 px: the bin · cut switcher (#hdBin) and
    the Next chip (#flow) in it, side by side;
  * exactly one blue button (`.is-next`, placed by /flow.js on Next's target or on the
    chip) — none while a round of the pass is open, where the chip reads "N left";
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

SCREENS = ("/", "/floor", "/open")
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
WORDS_IN_VIEW = {"/": 80, "/floor": 58, "/open": 59, "/ shot": 130, "/ film": 74, "/ fx": 130,
                 "/ jobs": 108}
CONTROLS_IN_VIEW = {"/": 20, "/floor": 8, "/open": 9, "/ shot": 33, "/ film": 19, "/ fx": 35,
                    "/ jobs": 20}

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
  const inView = el => { const r = el.getBoundingClientRect();
    return r.top < innerHeight && r.bottom > 0 && r.left < innerWidth && r.right > 0; };
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
        pg.locator("#makeFilm").click()                     # the header's Make the film
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
def measured(project):
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
    server.configure(project["edl"], project["footage"], project["sidecars"],
                     project["work"], proxies=False, assets=project["assets"])
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
                pg.goto(base + path, wait_until="domcontentloaded")
                pg.wait_for_function("window.flowBar && flowBar.state()", timeout=15000)
                pg.wait_for_selector("#flowNext", timeout=15000)
                _set_up(pg, state)
                pg.wait_for_timeout(3000)
                m = pg.evaluate(INVENTORY_JS)
                m["next"] = pg.evaluate("flowBar.state().next")
                out[state] = m
                print(f"\nbudget {state:7s} words {m['words_in_view']:4d} · controls "
                      f"{m['controls_in_view']:3d} · header {m['header'] and round(m['header']['height'])} px"
                      f" ({m['header_tag']}) · page {m['scroll_h']} / {m['inner_h']} · blue {m['blue']}"
                      f" · chip {m['chip_text']!r}")
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
    if state == "/floor" and re.fullmatch(r"\d+ left", m["chip_text"] or ""):
        # a round of the pass is open: P / X / U are the choice, nothing is blue
        assert m["blue"] == [], m["blue"]
        return
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


def test_the_selected_shot_is_in_view(measured):
    """INTAKE M16 I16.4: the selected shot's strip sits under the timeline, in view —
    it used to start at y≈953, below the fold."""
    m = measured["/ shot"]
    assert m["strip"]["height"] > 0 and m["strip"]["bottom"] <= m["inner_h"], m["strip"]


@pytest.mark.parametrize("state", STATES)
def test_words_and_controls_in_view_stay_under_the_screens_numbers(measured, state):
    m = measured[state]
    assert m["words_in_view"] <= WORDS_IN_VIEW[state], (state, m["words_in_view"])
    assert m["controls_in_view"] <= CONTROLS_IN_VIEW[state], (state, m["controls_in_view"],
                                                              m["controls"])


def test_no_step_bar_on_any_screen(measured):
    """M16 decision 2: the seven-step bar came off every screen; Next is the one
    "what now". The header's own budget would hide a bar that wrapped off-screen, so
    this says it outright."""
    for path, m in measured.items():
        assert m["steps"] == 0, (path, m["steps"])
        assert m["flow_links"] == 1, (path, "#flow holds the Next chip and nothing else")
