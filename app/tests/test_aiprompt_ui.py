"""The AI prompt, driven in a real browser (REDESIGN-GOAL dimension 14, Karl's K3 c).

Karl, 2026-10-16: *"there is no clear way … to run the AI tooling on this … easy to learn how
to use, especially how to use those AI features"*. One always-visible prompt
(`/modules/aiprompt.js`) in the Timeline panel's header row takes plain words about
anything in the editor:

  * it is in view and usable at 1440×900 and 1920×1080 in every board state (an empty
    sequence, a cut, a proposal waiting, an export running), and the app bar is still
    one row with nothing over anything else;
  * Ctrl+J focuses it from anywhere and the keys sheet lists it; Esc leaves; ↑ recalls;
  * its scope chip follows the selection, then the sequence In/Out, then the Source clip
    while the Source monitor is in use, else the whole sequence; a click changes it;
  * ↵ shows a working state in under 100 ms (measured in the page, the POST held open),
    with the honest cost ("free on your plan", no dollar sign) and a Stop;
  * each kind of answer does its thing: an op list goes to `rcActions.review` (stubbed:
    lane AIOPS builds it), a whole-cut rewrite opens the existing proposal, an answer in
    words opens a popover; a failure says what to do and Retry asks again;
  * every item of the AI menu does something visible; the ones that need a selection
    are greyed with the reason when there is none;
  * a right-click on a timeline block, a Project panel clip and either monitor offers
    "Ask AI about this…" (and Look deeper on a block), which scopes the prompt to it;
    the Effects bin's own menu still opens.

`/api/assist` is faked with `page.route` (lane AIOPS serves it); the server's model
backend refuses every call, so no test here can ever reach a real model.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import inference  # noqa: E402

VIEWPORTS = [(1440, 900), (1920, 1080)]
SEED = {"variant": "T", "title": "test cut", "orient": "none", "story": "",
        "target_s": [5, 20],
        "segments": [{"id": "aip0000001", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
                     {"id": "aip0000002", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}]}
INPUT = '[data-testid="ai-prompt-input"]'
SCOPE = '[data-testid="ai-scope"]'
STATUS = '[data-testid="ai-status"]'
RESULT = '[data-testid="ai-result"]'
RENDER_JOB = {"id": "aip-export", "kind": "render", "state": "running", "label": "Exporting — Quick 1080p",
              "pct": 41, "eta_s": 200, "eta_source": "measured", "milestone": "encoding",
              "detail": "frame 1210 of 2950", "elapsed_s": 140}
PLAN = {"segments": [{"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "open on the wave"},
                     {"clip": "CLIP_A.MP4", "in": 1.5, "out": 3.0, "why": "then the hello"}],
        "notes": "a tighter open"}


class NoModel:
    """The server's backend while this module runs: a model call is a test bug."""
    name = "no-model"

    def complete(self, request):
        raise inference.InferenceError("test_aiprompt_ui: no model calls")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(project):
    import uvicorn
    import server

    original = project["edl"].read_text(encoding="utf-8")
    inference.set_backend(NoModel())
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
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=10)
    inference.set_backend(None)
    project["edl"].write_text(original, encoding="utf-8")


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        yield b
        b.close()


class Assist:
    """`/api/assist` in place of lane AIOPS's server: POST answers {job} (or `post`),
    GET answers `answer` (running until a test sets it). `hold` keeps the POST open."""

    def __init__(self, pg):
        self.posts: list[dict] = []
        self.gets = 0
        self.cancels = 0
        self.answer: dict = {"status": "running"}
        self.post: tuple[int, dict] = (200, {"job": "aj1"})
        self.hold = False
        self.held = []
        pg.route("**/api/assist", self._post)
        pg.route("**/api/assist/*", self._get)
        pg.route("**/api/assist/*/cancel", self._get)
        # the price before an ask is the real server's (it never calls the model)
        pg.route("**/api/assist/price?*", lambda route: route.continue_())

    def _post(self, route):
        if route.request.method != "POST":
            return route.continue_()
        self.posts.append(json.loads(route.request.post_data or "{}"))
        if self.hold:
            self.held.append(route)
            return None
        status, body = self.post
        return route.fulfill(status=status, content_type="application/json", body=json.dumps(body))

    def release(self):
        status, body = self.post
        for r in self.held:
            r.fulfill(status=status, content_type="application/json", body=json.dumps(body))
        self.held = []

    def _get(self, route):
        if route.request.method == "POST" and route.request.url.endswith("/cancel"):
            self.cancels += 1
            return route.fulfill(status=200, content_type="application/json", body="{}")
        self.gets += 1
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(self.answer))


def _open(browser, base, project, size=(1440, 900), seed=SEED, jobs=None, before=None):
    Path(project["edl"]).write_text(json.dumps(seed, indent=1), encoding="utf-8")
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]})
    # the first-run walk-through (lane TEACH) never covers what these tests click
    ctx.add_init_script("try { localStorage.setItem('roughcut.tour', 'seen'); } catch (e) {}")
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.assist = Assist(pg)
    if jobs is not None:
        pg.route("**/api/jobs", lambda r: r.fulfill(status=200, content_type="application/json",
                                                     body=json.dumps({"jobs": jobs})))
    if before:
        before(pg)
    pg.goto(base)
    pg.wait_for_selector("#projList [data-clip]", timeout=15000)
    if seed["segments"]:
        pg.wait_for_selector("#tl .blk", timeout=15000)
    pg.wait_for_selector(INPUT, timeout=10000)
    # lane AIOPS's review, stubbed: what it was handed
    pg.evaluate("window.__reviewed = []; window.rcActions = window.rcActions || {};"
                " rcActions.review = (r) => { __reviewed.push(r); }; 0")   # (a trailing function would be called)
    return pg


def _close(pg):
    pg.unroute_all(behavior="ignoreErrors")
    pg.context.close()


def _scope_text(pg) -> str:
    return pg.locator(SCOPE).inner_text().strip()


USABLE_JS = """() => {
  const inp = document.querySelector('[data-testid=ai-prompt-input]');
  const field = inp.closest('.aip-field').getBoundingClientRect(), r = inp.getBoundingClientRect();
  const panel = document.querySelector('#pTimeline').getBoundingClientRect();
  const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  const kids = (sel) => [...document.querySelectorAll(sel)].filter(e => e.getClientRects().length)
    .map(e => { const b = e.getBoundingClientRect(); return [e.id || e.className, b.left, b.right, b.top, b.bottom]; });
  return {input_w: r.width, l: field.left, r: field.right, t: field.top, b: field.bottom,
          panel_l: panel.left, panel_r: panel.right, panel_t: panel.top,
          hit: !!hit && (hit === inp || inp.contains(hit)), placeholder: inp.placeholder,
          head: kids('#pTimeline > .ws-head > *'), prompt: kids('#aiPrompt > *'),
          header: kids('header > *'), vw: innerWidth, vh: innerHeight};
}"""


def _assert_usable(pg, size, where):
    u = pg.evaluate(USABLE_JS)
    assert u["hit"], (where, u)                                  # nothing lies over it
    assert u["input_w"] >= 150, (where, u)                       # room to type in
    assert u["l"] >= u["panel_l"] - 0.5 and u["r"] <= u["panel_r"] + 0.5, (where, u)
    assert 0 <= u["t"] and u["b"] <= u["vh"] and u["r"] <= u["vw"], (where, u)
    assert u["placeholder"].startswith("Ask the AI"), u["placeholder"]
    for (a, b) in zip(u["head"], u["head"][1:]):                 # the panel's header row, side by side
        assert a[2] <= b[1] + 0.5, (where, a, b)
    for (a, b) in zip(u["prompt"], u["prompt"][1:]):             # its status line, then the field
        assert a[2] <= b[1] + 0.5 and b[2] <= u["panel_r"] + 0.5, (where, a, b)
    for (a, b) in zip(u["header"], u["header"][1:]):             # the app bar: one row, none over another
        assert a[2] <= b[1] + 0.5, (where, a, b)
    assert all(k[4] <= 50 for k in u["header"]), (where, u["header"])
    # the header's middle is still the panel's: a click there focuses the Timeline, not the field
    pg.locator("#pTimeline .ws-head").click()
    assert pg.evaluate("document.activeElement.dataset.testid") != "ai-prompt-input", where
    # usable: a click puts the caret in it and the words land
    pg.locator(INPUT).click()
    pg.keyboard.type("trim")
    assert pg.locator(INPUT).input_value() == "trim", where
    pg.keyboard.press("Escape")
    pg.locator(INPUT).fill("")


# ---------------------------------------------------------------- in view, in every state

STATES = ["empty", "cut", "proposal", "exporting"]


@pytest.mark.parametrize("size", VIEWPORTS, ids=lambda s: f"{s[0]}x{s[1]}")
@pytest.mark.parametrize("state", STATES)
def test_the_prompt_is_in_view_and_usable_in_every_board_state(live_server, project, browser,
                                                                 size, state):
    seed = {**SEED, "segments": []} if state == "empty" else SEED
    pg = _open(browser, live_server, project, size, seed=seed,
               jobs=[RENDER_JOB] if state == "exporting" else None)
    if state == "proposal":
        pg.evaluate(f"rcShowProposal({json.dumps(PLAN)})")
        pg.wait_for_function("getComputedStyle(document.querySelector('#proposal')).display === 'block'",
                             timeout=5000)
    if state == "exporting":
        pg.wait_for_selector("#progress .job[data-job=aip-export]", timeout=10000)
    pg.wait_for_timeout(400)
    _assert_usable(pg, size, state)
    assert _scope_text(pg) == "Sequence"
    assert pg.errors == []
    _close(pg)


# ---------------------------------------------------------------- the key

def test_ctrl_j_focuses_it_from_anywhere_esc_leaves_and_the_keys_sheet_lists_it(live_server, project, browser):
    pg = _open(browser, live_server, project)
    pg.locator("#tl .blk").first.click()
    pg.keyboard.press("Control+j")
    assert pg.evaluate("document.activeElement.dataset.testid") == "ai-prompt-input"
    # Esc gives the keys back to the board: J/K/L, space, I/O act again
    pg.keyboard.press("Escape")
    assert pg.evaluate("document.activeElement.dataset.testid") != "ai-prompt-input"
    # from inside another field (the Project panel's search) too
    pg.locator("#projQ").click()
    pg.keyboard.press("Control+j")
    assert pg.evaluate("document.activeElement.dataset.testid") == "ai-prompt-input"
    # typing in it never reaches the timeline's keys (j k l c x are words here)
    segs = pg.evaluate("tl.state.segs.length")
    tool = pg.evaluate("tl.tool")
    pg.keyboard.type("cut x quickly")
    assert pg.evaluate("tl.state.segs.length") == segs and pg.evaluate("tl.tool") == tool
    pg.keyboard.press("Escape")
    # …and Esc gave the focus back to where it was: the search box
    assert pg.evaluate("document.activeElement.id") == "projQ"
    # the keys sheet: an AI group with the key
    pg.locator("#tlTitle").click()
    pg.keyboard.press("?")
    pg.wait_for_selector("#keysOverlay:not([hidden])", timeout=3000)
    sheet = pg.locator("#keyTable").inner_text()
    assert "AI" in sheet and "Ctrl+J" in sheet and "Ask AI about this" in sheet, sheet
    # it fits the window at the smallest size the sheet is checked at
    pg.set_viewport_size({"width": 1366, "height": 768})
    box = pg.evaluate("(() => { const r = document.querySelector('#keysOverlay .panel').getBoundingClientRect();"
                      " return [r.top, r.bottom, innerHeight]; })()")
    assert box[0] >= 0 and box[1] <= box[2], box
    assert pg.errors == []
    _close(pg)


def test_up_recalls_the_last_asks(live_server, project, browser):
    pg = _open(browser, live_server, project)
    pg.assist.answer = {"status": "done", "kind": "answer", "text": "ok", "summary": "ok"}
    for text in ("first ask", "second ask"):
        pg.keyboard.press("Control+j")
        pg.keyboard.type(text)
        pg.keyboard.press("Enter")
        pg.wait_for_selector(RESULT, timeout=5000)
        pg.keyboard.press("Escape")
    pg.keyboard.press("Control+j")
    pg.keyboard.type("draft")
    pg.keyboard.press("ArrowUp")
    assert pg.locator(INPUT).input_value() == "second ask"
    pg.keyboard.press("ArrowUp")
    assert pg.locator(INPUT).input_value() == "first ask"
    pg.keyboard.press("ArrowDown")
    pg.keyboard.press("ArrowDown")
    assert pg.locator(INPUT).input_value() == "draft"
    _close(pg)


# ---------------------------------------------------------------- the scope

def test_the_scope_chip_follows_the_selection_then_in_out_then_the_source(live_server, project, browser):
    pg = _open(browser, live_server, project)
    assert _scope_text(pg) == "Sequence"
    assert pg.evaluate("rcAI.scope().kind") == "sequence"
    # one block → its clip; ctrl-click a second → "2 clips"
    blocks = pg.locator("#tl .blk")
    blocks.nth(0).click()
    pg.wait_for_function(f"document.querySelector('{SCOPE}').innerText.trim() === 'Shot 1 · CLIP_A'", timeout=3000)
    s = pg.evaluate("rcAI.scope()")
    assert s["kind"] == "selection" and s["ids"] == ["aip0000001"] and "playhead" in s, s
    blocks.nth(1).click(modifiers=["Control"])
    pg.wait_for_function(f"document.querySelector('{SCOPE}').innerText.trim() === 'Shots 1–2'", timeout=3000)
    assert pg.evaluate("rcAI.scope().ids") == ["aip0000001", "aip0000002"]
    # nothing selected, an In/Out on the sequence → the range
    pg.evaluate("tl.select([])")
    pg.evaluate("tl.setSeqIn(0.5); tl.setSeqOut(2.5)")
    pg.wait_for_function(f"document.querySelector('{SCOPE}').innerText.trim().startsWith('In/Out')", timeout=3000)
    s = pg.evaluate("rcAI.scope()")
    assert s["kind"] == "range" and abs(s["in"] - 0.5) < 0.05 and abs(s["out"] - 2.5) < 0.05, s
    # the In/Out reads as its timecodes, the ruler's (verdict 9, punch 7), whole
    want = pg.evaluate("(s) => `In/Out ${tl.tc(s.in)}–${tl.tc(s.out)}`", s)
    assert want.startswith("In/Out 00:00:00:") and _scope_text(pg) == want, (want, _scope_text(pg))
    assert pg.evaluate("(s) => { const l = document.querySelector(s + ' .aip-scope-l');"
                       " return l.scrollWidth <= l.clientWidth; }", SCOPE), "the In/Out chip is cut"
    # no marks, the Source monitor in use → its clip
    pg.evaluate("tl.setSeqIn(null); tl.setSeqOut(null)")
    pg.locator("#projList tr.row", has_text="CLIP_C").click()
    pg.wait_for_function("window.rcSource && rcSource.clip === 'CLIP_C.MP4'", timeout=5000)
    pg.locator("#srcScreen").click()
    pg.wait_for_function(f"document.querySelector('{SCOPE}').innerText.trim() === 'Source: CLIP_C'", timeout=3000)
    # …which clicking into the prompt itself does not lose
    pg.locator(INPUT).click()
    assert pg.evaluate("rcAI.scope()")["clip"] == "CLIP_C.MP4"
    # the Timeline in use again, nothing selected → the sequence
    pg.locator("#tlTitle").click()
    pg.evaluate("tl.select([])")
    pg.wait_for_function(f"document.querySelector('{SCOPE}').innerText.trim() === 'Sequence'", timeout=3000)
    assert pg.errors == []
    _close(pg)


def test_a_click_on_the_chip_changes_the_scope_until_the_selection_moves(live_server, project, browser):
    pg = _open(browser, live_server, project)
    pg.locator("#tl .blk").nth(0).click()
    pg.wait_for_function(f"document.querySelector('{SCOPE}').innerText.trim() === 'Shot 1 · CLIP_A'", timeout=3000)
    pg.locator(SCOPE).click()
    menu = pg.locator('[data-testid="ai-scope-menu"]')
    assert menu.is_visible()
    assert menu.locator('button[data-scope="range"]').is_disabled()       # no In/Out yet, said why
    assert "mark I and O" in menu.locator('button[data-scope="range"]').inner_text()
    menu.locator('button[data-scope="sequence"]').click()
    assert not menu.is_visible()
    assert _scope_text(pg) == "Sequence" and pg.evaluate("rcAI.scope().kind") == "sequence"
    assert pg.evaluate("document.activeElement.dataset.testid") == "ai-prompt-input"
    # the selection moves: the chip follows it again
    pg.locator("#tl .blk").nth(1).click()
    pg.wait_for_function(f"document.querySelector('{SCOPE}').innerText.trim() === 'Shot 2 · CLIP_B'", timeout=3000)
    _close(pg)


# ---------------------------------------------------------------- running

def test_enter_shows_working_within_100ms_with_the_honest_cost_and_a_stop(live_server, project, browser):
    pg = _open(browser, live_server, project)
    pg.wait_for_function("document.querySelector('#tl .blk')", timeout=5000)
    pg.locator("#tl .blk").nth(1).click()
    pg.assist.hold = True                                       # the server takes its time
    pg.evaluate("""() => {
      window.__t = {};
      window.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !__t.key) __t.key = performance.now(); }, true);
      const st = document.querySelector('[data-testid=ai-status]');
      new MutationObserver(() => { if (st.dataset.state === 'working' && !__t.working) __t.working = performance.now(); })
        .observe(st, {attributes: true, childList: true, subtree: true});
    }""")
    pg.keyboard.press("Control+j")
    pg.keyboard.type("dissolve between 1 and 2")
    pg.keyboard.press("Enter")
    pg.wait_for_function("window.__t.working", timeout=3000)
    t = pg.evaluate("window.__t")
    assert t["working"] - t["key"] < 100, t
    st = pg.locator(STATUS)
    assert st.get_attribute("data-state") == "working"
    words = st.inner_text()
    assert "Working" in words and "free on your plan" in words and "$" not in words, words
    assert "API" in st.locator(".aip-cost").get_attribute("title")   # the estimate is in the hover
    # the field's own hover says what it does, what it costs and what it changes
    hover = pg.locator(INPUT).get_attribute("title")
    assert "free on your plan" in hover and "review" in hover and "Ctrl+J" in hover, hover
    assert pg.locator('[data-testid="ai-stop"]').is_visible()
    # what went out: the words and the scope, the selected block
    pg.assist.release()
    pg.wait_for_function("rcAI.state().job === 'aj1'", timeout=3000)
    body = pg.assist.posts[0]
    assert body["text"] == "dissolve between 1 and 2"
    assert body["scope"]["kind"] == "selection" and body["scope"]["ids"] == ["aip0000002"]
    assert isinstance(body["scope"]["playhead"], (int, float))
    # Stop: nothing changes, the service is told, a late answer is ignored
    pg.locator('[data-testid="ai-stop"]').click()
    assert st.get_attribute("data-state") == "stopped"
    assert "nothing changed" in st.inner_text()
    pg.assist.answer = {"status": "done", "kind": "ops", "ops": [{"op": "razor", "id": "aip0000001", "at": 1.0}]}
    pg.wait_for_timeout(1500)
    assert pg.evaluate("__reviewed.length") == 0
    assert pg.assist.cancels == 1
    assert pg.errors == []
    _close(pg)


def test_an_op_list_goes_to_the_review_and_the_line_says_how_many(live_server, project, browser):
    pg = _open(browser, live_server, project)
    ops = [{"op": "transition", "a": "aip0000001", "b": "aip0000002", "kind": "Cross Dissolve", "dur": 1.0},
           {"op": "set", "id": "aip0000002", "prop": "temperature", "value": 12}]
    pg.assist.answer = {"status": "done", "kind": "ops", "ops": ops, "summary": "a dissolve and a warmer shot 2",
                        "cost": {"usd": 0.03, "charged": False}}
    pg.keyboard.press("Control+j")
    pg.keyboard.type("dissolve 1→2 and warm shot 2")
    pg.keyboard.press("Enter")
    pg.wait_for_selector(RESULT, timeout=5000)
    assert pg.locator(STATUS).get_attribute("data-state") == "done"
    assert pg.locator(RESULT).inner_text().startswith("✓2 changes ready") or \
        pg.locator(RESULT).inner_text().replace("\n", " ").startswith("✓ 2 changes ready")
    got = pg.evaluate("__reviewed")
    assert len(got) == 1 and got[0]["ops"] == ops and got[0]["job"] == "aj1", got
    # Review reopens it
    pg.locator('[data-testid="ai-result-open"]').click()
    assert pg.evaluate("__reviewed.length") == 2
    # the prompt is empty and ready for the next ask; the scope is unchanged
    assert pg.locator(INPUT).input_value() == ""
    assert pg.errors == []
    _close(pg)


def test_a_whole_cut_rewrite_opens_the_existing_proposal(live_server, project, browser):
    def before(pg):
        pg.route("**/api/ask/pj1", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"state": "done", "plan": PLAN})))
    pg = _open(browser, live_server, project, before=before)
    pg.assist.answer = {"status": "done", "kind": "plan", "plan_ask": "pj1", "summary": "a tighter open"}
    pg.keyboard.press("Control+j")
    pg.keyboard.type("tighten the intro to 3 s")
    pg.keyboard.press("Enter")
    pg.wait_for_function("getComputedStyle(document.querySelector('#proposal')).display === 'block'",
                         timeout=5000)
    assert "A new cut is ready" in pg.locator(RESULT).inner_text()
    assert pg.evaluate("dock.current()") == "ask"
    assert "2 shots" in pg.locator("#proposalDiff").inner_text()
    assert pg.evaluate("__reviewed.length") == 0
    assert pg.errors == []
    _close(pg)


def test_an_answer_in_words_opens_a_popover(live_server, project, browser):
    pg = _open(browser, live_server, project)
    text = "The intro is the first two shots, 4.0 s together."
    pg.assist.answer = {"status": "done", "kind": "answer", "text": text, "summary": "4.0 s"}
    pg.keyboard.press("Control+j")
    pg.keyboard.type("how long is the intro?")
    pg.keyboard.press("Enter")
    pop = pg.locator('[data-testid="ai-answer"]')
    pop.wait_for(state="visible", timeout=5000)
    assert text in pop.inner_text() and "how long is the intro?" in pop.inner_text()
    assert "Answered" in pg.locator(RESULT).inner_text()
    pg.keyboard.press("Escape")                                 # leaves the prompt
    pg.keyboard.press("Escape")                                 # and closes the popover
    assert not pop.is_visible()
    pg.locator('[data-testid="ai-result-open"]').click()             # "Show" brings it back
    assert pop.is_visible()
    assert pg.evaluate("__reviewed.length") == 0
    _close(pg)


def test_a_failure_says_what_to_do_and_retry_asks_again(live_server, project, browser):
    pg = _open(browser, live_server, project)
    pg.assist.post = (500, {"detail": "the model is overloaded"})
    pg.keyboard.press("Control+j")
    pg.keyboard.type("warm up the snow shots")
    pg.keyboard.press("Enter")
    st = pg.locator(STATUS)
    pg.wait_for_function(f"document.querySelector('{STATUS}').dataset.state === 'failed'", timeout=5000)
    assert "Failed" in st.inner_text()
    pg.locator('[data-testid="ai-details"]').click()
    pop = pg.locator('[data-testid="ai-answer"]')
    assert pop.is_visible()
    words = pop.inner_text()
    assert "the model is overloaded" in words and "try again" in words.lower(), words
    # Retry: the same words again, and this time it works
    pg.assist.post = (200, {"job": "aj1"})
    pg.assist.answer = {"status": "done", "kind": "answer", "text": "done", "summary": "done"}
    pop.locator('[data-pop="retry"]').click()
    pg.wait_for_selector(RESULT, timeout=5000)
    assert [p["text"] for p in pg.assist.posts] == ["warm up the snow shots"] * 2
    # a job that fails on the server says so too
    pg.locator('[data-ai="dismiss"]').click()
    pg.assist.answer = {"status": "failed", "error": "no shot matched 'snow'"}
    pg.keyboard.press("Control+j")
    pg.keyboard.type("warm up the snow shots")
    pg.keyboard.press("Enter")
    pg.wait_for_function(f"document.querySelector('{STATUS}').dataset.state === 'failed'", timeout=5000)
    assert "no shot matched" in pg.evaluate("rcAI.state().error")
    # a board without the service (404) names the way that still works
    pg.assist.post = (404, {"detail": "Not Found"})
    pg.locator('[data-ai="retry"]').click()
    pg.wait_for_function("rcAI.state().error.includes('no AI prompt service')", timeout=5000)
    pg.locator('[data-testid="ai-details"]').click()
    # (not the Assistant's own box: there is none since wave 13, one ask box)
    assert "AI menu" in pop.inner_text() and "Run first pass" in pop.inner_text()
    assert "Sequence › Ask for a change" not in pop.inner_text()
    assert pg.errors == []
    _close(pg)


# ---------------------------------------------------------------- the AI menu

def _menu(pg, name):
    pg.locator('[data-testid="menu-ai"]').click()
    item = pg.locator(f'#menubar button[data-cmd="ai:{name}"]')
    assert item.is_visible()
    return item


def test_every_ai_menu_item_does_something_visible(live_server, project, browser):
    def before(pg):
        # the pass never runs for real: its POST is answered here, and its state reads
        # "never run" whatever another module left in the session's work dir
        pg.fp_posts = []

        def fp(route):
            if route.request.method == "POST":
                pg.fp_posts.append(1)
                return route.fulfill(status=200, content_type="application/json",
                                     body=json.dumps({"job": "fp-test", "keep": []}))
            resp = route.fetch()
            body = resp.json()
            body.update({"state": "never-run"})
            return route.fulfill(response=resp, body=json.dumps(body), content_type="application/json")
        pg.route("**/api/firstpass", fp)
    pg = _open(browser, live_server, project, before=before)
    pg.locator("#tl .blk").nth(0).click()
    pg.wait_for_function("tl.state.sel.size === 1", timeout=3000)
    active = "document.activeElement && (document.activeElement.dataset.testid || document.activeElement.id)"

    _menu(pg, "prompt").click()
    assert pg.evaluate(active) == "ai-prompt-input"
    pg.keyboard.press("Escape")

    # Ask for a change to the cut: the prompt, on the whole sequence (a block is selected)
    _menu(pg, "ask").click()
    assert pg.evaluate(active) == "ai-prompt-input" and _scope_text(pg) == "Sequence"
    pg.keyboard.press("Escape")

    # the selected clip: the prompt, scoped to it
    pg.locator("#tl .blk").nth(1).click()
    _menu(pg, "shot").click()
    assert pg.evaluate(active) == "ai-prompt-input" and _scope_text(pg) == "Shot 2 · CLIP_B"
    pg.keyboard.press("Escape")

    # a colour change: the prompt, pre-filled
    _menu(pg, "colour").click()
    assert pg.evaluate(active) == "ai-prompt-input"
    assert pg.locator(INPUT).input_value() == "Colour: "
    pg.keyboard.press("Escape")
    pg.locator(INPUT).fill("")

    # Design an effect: Designed Effects, its note focused
    _menu(pg, "fx").click()
    pg.wait_for_function("dock.current() === 'fx' && document.activeElement && document.activeElement.id === 'fxNote'",
                         timeout=5000)

    # Look deeper: the clip's priced Look deeper, in view and focused
    _menu(pg, "deep").click()
    pg.wait_for_function("dock.current() === 'inspect' && document.querySelector('#inspector details.assist').open"
                         " && document.activeElement && document.activeElement.classList.contains('dv-look')",
                         timeout=8000)
    assert pg.locator("#inspector .lookhost .dv-look").is_visible()

    # Find a moment: Selects, its find box focused
    _menu(pg, "find").click()
    pg.wait_for_function("dock.current() === 'bin' && document.activeElement.id === 'findQ'", timeout=3000)

    # the claims audit: greyed with the reason when there is nothing to audit (this bin),
    # else Selects with its priced button focused
    item = _menu(pg, "audit")
    if item.is_disabled():
        assert item.get_attribute("title") and "nothing to check" in item.inner_text()
        pg.keyboard.press("Escape")
    else:
        item.click()
        pg.wait_for_function("dock.current() === 'bin' && document.activeElement.id === 'auditClaims'",
                             timeout=3000)

    # the first pass, never run here: "Run first pass" with its price runs it, its panel open
    item = _menu(pg, "firstpass")
    words = item.inner_text()
    assert words.startswith("Run first pass") and "free on your plan" in words, words
    item.click()
    pg.wait_for_selector('[data-testid="firstpass-panel"]:not([hidden])', timeout=5000)
    pg.wait_for_timeout(300)
    assert pg.fp_posts == [1]
    # Learn the AI features is lane TEACH's (dock.js routes it); the item is there
    pg.locator('[data-testid="menu-ai"]').click()
    assert pg.locator('#menubar button[data-cmd="tour:ai"]').is_visible()
    pg.keyboard.press("Escape")
    assert pg.errors == []
    _close(pg)


def test_the_first_pass_item_reads_re_run_after_a_pass(live_server, project, browser):
    def before(pg):
        def patched(route):
            if route.request.method != "GET":
                return route.continue_()
            resp = route.fetch()
            body = resp.json()
            body.update({"state": "done", "when": time.time() - 600})
            route.fulfill(response=resp, body=json.dumps(body), content_type="application/json")
        pg.route("**/api/firstpass", patched)
    pg = _open(browser, live_server, project, before=before)
    pg.wait_for_function("window.rcFirstPass && rcFirstPass.state() && rcFirstPass.state().state === 'done'",
                         timeout=10000)
    item = _menu(pg, "firstpass")
    assert item.inner_text().strip() == "Re-run first pass…"
    item.click()
    # a re-run says what it keeps before it runs: the panel, not a run
    pg.wait_for_selector('[data-testid="firstpass-panel"]:not([hidden])', timeout=5000)
    assert pg.locator('[data-testid="firstpass-run"]').is_visible()
    _close(pg)


def test_items_that_need_a_selection_are_greyed_with_the_reason(live_server, project, browser):
    pg = _open(browser, live_server, project)
    pg.evaluate("tl.select([])")
    for name in ("shot", "deep"):
        item = _menu(pg, name)
        assert item.is_disabled(), name
        assert "select a clip" in item.inner_text() and "Timeline" in item.get_attribute("title")
        pg.keyboard.press("Escape")
    # the Sequence menu's own Ask and Design items still work (the pro controls stay)
    pg.locator("#menubar .menu[data-menu=sequence] .mtop").click()
    pg.locator('#menubar button[data-cmd="ask"]').click()
    assert pg.evaluate("dock.current()") == "ask"
    pg.locator("#menubar .menu[data-menu=sequence] .mtop").click()
    pg.locator('#menubar button[data-cmd="fx"]').click()
    assert pg.evaluate("dock.current()") == "fx"
    _close(pg)


# ---------------------------------------------------------------- "Ask AI about this…"

def test_right_click_a_block_asks_about_it_or_looks_deeper(live_server, project, browser):
    pg = _open(browser, live_server, project)
    pg.locator("#tl .blk").nth(1).click(button="right")
    menu = pg.locator('[data-testid="ai-context-menu"]')
    assert menu.is_visible()
    assert pg.locator('[data-testid="ai-ctx-deep"]').is_visible()
    pg.locator('[data-testid="ai-ctx-ask"]').click()
    assert not menu.is_visible()
    assert pg.evaluate("document.activeElement.dataset.testid") == "ai-prompt-input"
    assert _scope_text(pg) == "Shot 2 · CLIP_B"
    assert pg.evaluate("[...tl.state.sel]") == ["aip0000002"]          # Premiere's right-click selects it
    # what goes out names that block, not a description retyped
    pg.assist.answer = {"status": "running"}
    pg.keyboard.type("slow this down")
    pg.keyboard.press("Enter")
    pg.wait_for_function("rcAI.state().job === 'aj1'", timeout=3000)
    assert pg.assist.posts[-1]["scope"]["ids"] == ["aip0000002"]
    pg.locator('[data-testid="ai-stop"]').click()
    # Look deeper from the same menu: the block's priced button, focused
    pg.locator("#tl .blk").nth(0).click(button="right")
    pg.locator('[data-testid="ai-ctx-deep"]').click()
    pg.wait_for_function("document.activeElement && document.activeElement.classList.contains('dv-look')",
                         timeout=8000)
    assert pg.evaluate("[...tl.state.sel]") == ["aip0000001"]
    # the block's linked sound on A1 is the same shot
    pg.locator("#tl .ablk").nth(1).click(button="right")
    pg.locator('[data-testid="ai-ctx-ask"]').click()
    assert pg.evaluate("rcAI.scope().ids") == ["aip0000002"]
    pg.keyboard.press("Escape")
    # Esc closes it without choosing
    pg.locator("#tl .blk").nth(0).click(button="right")
    assert menu.is_visible()
    pg.keyboard.press("Escape")
    assert not menu.is_visible()
    assert pg.errors == []
    _close(pg)


NARROW = {**SEED, "segments": [
    {"id": "aip0000001", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
    {"id": "aip0000002", "clip": "CLIP_B.MP4", "in": 0.0, "out": 0.35, "why": "a flash"},
    {"id": "aip0000003", "clip": "CLIP_C.MP4", "in": 0.5, "out": 2.5, "why": "third"}]}

HIT_JS = """([x, y]) => { const n = document.elementFromPoint(x, y);
  return n ? {edge: !!n.closest('#tl .tl-edge'), blk: (n.closest('#tl .blk') || {dataset: {}}).dataset.id || null} : null; }"""


def test_right_click_a_narrow_block_or_a_trim_edge_opens_the_clips_menu(live_server, project, browser):
    """Verdict 10, punch 9: right-click on a block narrower than ~15 px at fit zoom opened no
    menu (a 14 px block gave nothing, a 26 px one the menu). The trim edges' columns
    (timeline-trim.js `.tl-edge`, 16 px over each cut, above the blocks) cover the whole of
    such a block and the ends of every other; the menu is the clip's under the pointer."""
    pg = _open(browser, live_server, project, seed=NARROW)
    pg.evaluate("tl.zoomTo(40)")                                      # 0.35 s = 14 px
    pg.wait_for_function("document.querySelectorAll('#tl .tl-lane[data-lane=V1] > .tl-edge').length === 2"
                         " && Math.abs(document.querySelector('#tl .blk[data-id=aip0000002]')"
                         ".getBoundingClientRect().width - 14) < 1.5", timeout=5000)
    box = lambda i: pg.locator(f'#tl .blk[data-id="aip000000{i}"]').bounding_box()   # noqa: E731
    narrow, first, third = box(2), box(1), box(3)
    menu = pg.locator('[data-testid="ai-context-menu"]')
    y = narrow["y"] + narrow["height"] / 2
    spots = [  # (x, the block whose menu it is, what is under the pointer)
        (narrow["x"] + narrow["width"] / 2, "aip0000002", "the narrow block's middle"),
        (narrow["x"] + 2, "aip0000002", "the narrow block, just past its in point"),
        (first["x"] + first["width"] - 3, "aip0000001", "shot 1's out edge"),
        (third["x"] + 3, "aip0000003", "shot 3's in edge"),
    ]
    for x, want, where in spots:
        hit = pg.evaluate(HIT_JS, [x, y])
        assert hit["edge"], f"{where}: not on a trim edge's column, the test proves nothing: {hit}"
        pg.mouse.click(x, y, button="right")
        assert menu.is_visible(), f"right-click on {where} opened no menu"
        pg.locator('[data-testid="ai-ctx-ask"]').click()
        assert pg.evaluate("rcAI.scope().ids") == [want], where
        assert pg.evaluate("[...tl.state.sel]") == [want], where
        pg.keyboard.press("Escape")
    assert _scope_text(pg) == "Shot 3 · CLIP_C"
    assert pg.errors == []
    _close(pg)


def test_right_click_a_project_clip_and_the_monitors(live_server, project, browser):
    pg = _open(browser, live_server, project)
    menu = pg.locator('[data-testid="ai-context-menu"]')
    # a Project panel clip: the prompt scoped to that clip
    pg.locator("#projList [data-clip]", has_text="CLIP_C").first.click(button="right")
    assert menu.is_visible()
    assert pg.locator('[data-testid="ai-ctx-deep"]').count() == 0 or not pg.locator('[data-testid="ai-ctx-deep"]').is_visible()
    pg.locator('[data-testid="ai-ctx-ask"]').click()
    s = pg.evaluate("rcAI.scope()")
    assert s["kind"] == "clip" and s["clip"] == "CLIP_C.MP4", s
    assert _scope_text(pg) in ("CLIP_C", "Source: CLIP_C")
    pg.keyboard.press("Escape")
    # the Program monitor: the shot under the playhead
    pg.evaluate("tl.seek(2.5)")                                 # inside shot 2
    pg.wait_for_timeout(200)
    pg.locator("#player .screen").click(button="right")
    assert menu.is_visible()
    pg.locator('[data-testid="ai-ctx-ask"]').click()
    assert pg.evaluate("rcAI.scope().ids") == ["aip0000002"]
    pg.keyboard.press("Escape")
    # the Source monitor: its clip
    pg.locator("#projList tr.row", has_text="CLIP_A").click()
    pg.wait_for_function("window.rcSource && rcSource.clip === 'CLIP_A.MP4'", timeout=5000)
    pg.locator("#srcScreen").click(button="right")
    assert menu.is_visible()
    pg.locator('[data-testid="ai-ctx-ask"]').click()
    assert pg.evaluate("rcAI.scope()")["clip"] == "CLIP_A.MP4"
    assert _scope_text(pg) == "Source: CLIP_A"
    assert pg.errors == []
    _close(pg)


def test_the_effects_bins_own_menu_still_opens(live_server, project, browser):
    pg = _open(browser, live_server, project)
    pg.locator('#pProject .ws-tab[data-tool="effects"]').click()
    item = pg.locator("#fxbin .fxb-item").first
    if not item.is_visible():
        pg.locator("#fxbin .fxb-folder").first.click()
    pg.locator("#fxbin .fxb-item").first.click(button="right")
    assert pg.locator("#fxbinMenu").is_visible()
    assert not pg.locator('[data-testid="ai-context-menu"]').is_visible()
    assert pg.errors == []
    _close(pg)


def test_ask_about_from_code_pre_scopes_the_prompt(live_server, project, browser):
    pg = _open(browser, live_server, project)
    pg.evaluate("rcAI.askAbout({kind: 'range', in: 0.5, out: 1.5})")
    assert pg.evaluate("document.activeElement.dataset.testid") == "ai-prompt-input"
    s = pg.evaluate("rcAI.scope()")
    assert s["kind"] == "range" and s["in"] == 0.5 and s["out"] == 1.5
    pg.evaluate("rcAI.focus('end on the fall')")
    assert pg.locator(INPUT).input_value() == "end on the fall"
    pg.assist.answer = {"status": "running"}
    assert pg.evaluate("rcAI.ask('end on the fall', {kind: 'sequence'})") is True
    assert pg.assist.posts[-1]["scope"]["kind"] == "sequence"
    _close(pg)


def test_the_sequence_menus_ask_goes_to_the_prompt(live_server, project, browser):
    """Sequence › Ask for a change… is the same one ask box as AI › Ask for a change to the
    cut (wave 13: the Assistant tab's box is a button now): it puts the cursor in the AI
    prompt, scoped to the sequence, and asks nothing by itself."""
    pg = _open(browser, live_server, project)
    posts = []
    pg.on("request", lambda r: posts.append(r.url) if r.method == "POST" and "/api/assist" in r.url else None)
    pg.locator('#menubar .menu[data-menu="sequence"] .mtop').click()
    pg.locator('#menubar button[data-cmd="ask"]').click()
    pg.wait_for_function("document.activeElement && document.activeElement.dataset.testid === 'ai-prompt-input'",
                         timeout=3000)
    assert "Sequence" in pg.locator('[data-testid="ai-scope"]').inner_text()
    assert pg.evaluate("dock.current()") == "ask"      # the Assistant, where the answer is reviewed
    assert posts == []
    pg.context.close()
