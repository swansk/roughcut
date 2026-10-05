"""The screen budget (INTAKE M16, I16.1 — "take things away").

Karl, 2026-10-04: *"roughcut feels hard to use / with a bunch of buttons and text"* — the
fourth report of the same complaint, and every earlier answer added a surface (the UI
grew from 10 to 101 `<button`s). This file is the ratchet. At 1440×900, on the suite's
own fixture bin, read-only (it only loads pages):

  * every screen has ONE header row, at most 50 px: the bin · cut switcher (#hdBin) and
    the Next chip (#flow) in it, side by side;
  * exactly one blue button (`.is-next`, placed by /flow.js on Next's target or on the
    chip) — none while a round of the pass is open, where the chip reads "N left";
  * the board does not scroll;
  * the words and controls in view stay under each screen's number.

The words and controls are counted the way the M16 audit counted them on Killington
(the capture behind the proposal), so the numbers here and there mean the same thing.
A change that adds a surface has to take one away, or raise a number below — in review,
where it shows.
"""

from __future__ import annotations

import json
import re
import socket
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

SCREENS = ("/", "/floor", "/open")
VIEWPORT = {"width": 1440, "height": 900}
HEADER_MAX_PX = 50

# Words and controls in view per screen, at 1440×900 on the fixture bin.
# TODO-tighten: stage 1 lands lane by lane, so these are set generously. Measured on
# the nav lane's branch (step bar off, one blue, the menu's places; the other lanes not
# merged): / 176 words · 19 controls, /floor 324 · 5, /open 433 · 12 — before it,
# on b620ff7: / 197 · 26, /floor 350 · 12, /open 454 · 19. The integrator lowers each
# to just above the merged screen's own count, and every later stage lowers them again
# toward the end state (≲ 100 words on /open and the pass, ≲ 220 on the board).
WORDS_IN_VIEW = {"/": 350, "/floor": 450, "/open": 500}
CONTROLS_IN_VIEW = {"/": 60, "/floor": 20, "/open": 30}

# Contracts another lane delivers (M16 stage 1, C1): until that lane is merged the
# check is an expected failure on that screen. The integrator removes each entry as
# its lane lands — an empty dict is the end state; a listed check that passes fails,
# so an entry cannot outlive its lane and hide a regression later.
AWAITS = {
    ("header", "/"): "lane shell: the board's one header row",
    ("header", "/floor"): "lane pass: the pass's one header row",
    ("scroll", "/"): "lanes shell + cut: the selected shot in view, no page scroll",
}

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
    blue: [...document.querySelectorAll('.is-next')].map(el => el.id || el.dataset.nextFor || el.tagName),
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
        "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
                     {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}]}


@pytest.fixture(scope="module")
def measured(project):
    """Each screen loaded once at 1440×900 and counted after the page has settled —
    the same 3 s the audit's capture waited — with the flow answered and drawn."""
    import uvicorn
    import server

    original = project["edl"].read_text(encoding="utf-8")
    Path(project["edl"]).write_text(json.dumps(SEED, indent=1), encoding="utf-8")
    server.configure(project["edl"], project["footage"], project["sidecars"],
                     project["work"], proxies=False, assets=project["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in project["stems"]])
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
            for path in SCREENS:
                pg = b.new_page(viewport=VIEWPORT)
                # read-only: nothing this test does may write
                pg.route("**/*", lambda r: r.abort() if r.request.method not in ("GET", "HEAD")
                         else r.continue_())
                # the screen, not the host: a CLI this machine cannot find would put the
                # banner (its own test is test_flow_ui's) above every page and its words
                # into every count
                pg.route("**/api/backend", lambda r: r.fulfill(
                    status=200, content_type="application/json",
                    body=json.dumps({"state": "ok", "fix": None})))
                pg.goto(base + path, wait_until="domcontentloaded")
                pg.wait_for_function("window.flowBar && flowBar.state()", timeout=15000)
                pg.wait_for_selector("#flowNext", timeout=15000)
                pg.wait_for_timeout(3000)
                m = pg.evaluate(INVENTORY_JS)
                m["next"] = pg.evaluate("flowBar.state().next")
                out[path] = m
                print(f"\nbudget {path:7s} words {m['words_in_view']:4d} · controls "
                      f"{m['controls_in_view']:3d} · header {m['header'] and round(m['header']['height'])} px"
                      f" ({m['header_tag']}) · page {m['scroll_h']} / {m['inner_h']} · blue {m['blue']}"
                      f" · chip {m['chip_text']!r}")
                pg.close()
            b.close()
    finally:
        srv.should_exit = True
        thread.join(timeout=10)
        project["edl"].write_text(original, encoding="utf-8")
    return out


def awaits(check: str, path: str) -> None:
    why = AWAITS.get((check, path))
    if why:
        pytest.xfail(why)


def landed(check: str, path: str) -> None:
    if (check, path) in AWAITS:
        pytest.fail(f"{check} on {path} passes now: take it out of AWAITS ({AWAITS[(check, path)]})")


@pytest.mark.parametrize("path", SCREENS)
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


@pytest.mark.parametrize("path", SCREENS)
def test_exactly_one_blue_button(measured, path):
    m = measured[path]
    assert m["next"], f"{path}: the fixture bin always has a next action"
    if path == "/floor" and re.fullmatch(r"\d+ left", m["chip_text"] or ""):
        # a round of the pass is open: P / X / U are the choice, nothing is blue
        assert m["blue"] == [], m["blue"]
        return
    assert len(m["blue"]) == 1, (path, m["blue"])


def test_the_board_does_not_scroll(measured):
    m = measured["/"]
    try:
        assert m["scroll_h"] <= m["inner_h"] + 2, (m["scroll_h"], m["inner_h"])
    except AssertionError:
        awaits("scroll", "/")
        raise
    landed("scroll", "/")


@pytest.mark.parametrize("path", SCREENS)
def test_words_and_controls_in_view_stay_under_the_screens_numbers(measured, path):
    m = measured[path]
    assert m["words_in_view"] <= WORDS_IN_VIEW[path], (path, m["words_in_view"])
    assert m["controls_in_view"] <= CONTROLS_IN_VIEW[path], (path, m["controls_in_view"],
                                                             m["controls"])


def test_no_step_bar_on_any_screen(measured):
    """M16 decision 2: the seven-step bar came off every screen; Next is the one
    "what now". The header's own budget would hide a bar that wrapped off-screen, so
    this says it outright."""
    for path, m in measured.items():
        assert m["steps"] == 0, (path, m["steps"])
        assert m["flow_links"] == 1, (path, "#flow holds the Next chip and nothing else")
