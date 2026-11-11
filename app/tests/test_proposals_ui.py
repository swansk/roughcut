"""The AI's work cannot be lost, in the board (wave 23, lane SAFE): real Chromium on the suite's
fixture bin and a scripted model (never a live call). Each test is a Copper editor's own steps.

  (a) Discard is undoable: the toast's Undo and Ctrl+Z bring the proposal back as it was, and
      Discard is not Accept's twin (CLUMSY-01).
  (b) The Assistant lists the last proposals and any of them opens again (CLUMSY-09, FINDER-20).
  (c) A whole-film re-cut can be stopped, and the card says what Stop did (CLUMSY-13).
  (d) After Accept nothing still says "a new cut is ready · Review" (CLUMSY-11, WORDS-03).
  (e) Undo and redo survive a reload and a switch to another cut and back; the buttons stay on
      an emptied sequence (CLUMSY-04, -06, -17, HANDS-07, WORDS-14, FINISH-10).
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from roughcut import config, inference  # noqa: E402

SEGS = [{"id": "shot-a", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
        {"id": "shot-b", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"},
        {"id": "shot-c", "clip": "CLIP_C.MP4", "in": 2.0, "out": 5.0, "why": "third"}]
PLAN_1 = [{"clip": "CLIP_C.MP4", "in": 0.5, "out": 3.0, "why": "open on the walk"},
          {"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0, "why": "the greeting"},
          {"clip": "CLIP_B.MP4", "in": 2.4, "out": 4.0, "why": "the reply"}]
PLAN_2 = [{"clip": "CLIP_B.MP4", "in": 2.4, "out": 4.0, "why": "the reply"},
          {"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0, "why": "the greeting"}]


class Model:
    """`plans` answers the Ask call in turn; the assist call answers a plan-kind ask; `gate`,
    when set, holds the Ask call (a slow whole-film re-cut)."""
    name = "proposals-ui-scripted"

    def __init__(self):
        self.plans: list[list[dict]] = []
        self.gate: threading.Event | None = None
        self.assist_gate: threading.Event | None = None   # holds the prompt box's own call
        self.assist: list[dict] = []                      # its answers, in turn (default: a plan)
        self.assist_calls = 0

    def complete(self, request):
        schema = request.schema or {}
        if "kind" in schema and "ops" in schema:
            self.assist_calls += 1
            if self.assist_gate is not None:
                self.assist_gate.wait(timeout=60)
            if self.assist:
                text = json.dumps(self.assist.pop(0))
                model = config.model_for(request.role)
                return inference.Result(content=text, input_tokens=4000, output_tokens=300, backend=self.name,
                                        model=model, projected_usd=1e-4, latency_ms=1, raw=text)
            text = json.dumps({"kind": "plan", "summary": "a tighter cut", "note": "make it tighter"})
        elif "segments" in schema:
            if self.gate is not None:
                self.gate.wait(timeout=60)
            text = json.dumps({"segments": self.plans.pop(0) if self.plans else PLAN_1,
                               "notes": "a tighter cut"})
        else:
            text = "{}"
        model = config.model_for(request.role)
        return inference.Result(content=text, input_tokens=4000, output_tokens=300, backend=self.name,
                                model=model, projected_usd=config.projected_usd(model, 4000, 300),
                                latency_ms=1, raw=text)


MODEL = Model()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed(project) -> None:
    body = {"variant": "T", "title": "test cut", "orient": "none", "story": "", "target_s": [5, 20],
            "segments": [dict(s) for s in SEGS]}
    Path(project["edl"]).write_text(json.dumps(body, indent=1), encoding="utf-8")


@pytest.fixture(scope="module")
def live_server(project):
    import uvicorn
    import server

    original = project["edl"].read_text(encoding="utf-8")
    _seed(project)
    server.configure(project["edl"], project["footage"], project["sidecars"], project["work"],
                     proxies=False, assets=project["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in project["stems"]])
    inference.set_backend(MODEL)
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
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
def browser(live_server):
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        yield b
        b.close()


@pytest.fixture
def board(browser, live_server, project):
    """A fresh board on the seeded cut, no proposals, no undo history, a quiet model."""
    import server
    _seed(project)
    for d in (server.STATE["asks"],):
        for p in d.glob("*.json"):
            p.unlink()
    server.undo_path().unlink(missing_ok=True)
    MODEL.plans, MODEL.gate, MODEL.assist, MODEL.assist_gate = [], None, [], None
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(live_server)
    page.wait_for_selector("#tl .blk")
    page.wait_for_function("window.rcActions && window.rcAI && window.rcReopenProposal")
    page.wait_for_timeout(500)
    yield page, errors
    for g in (MODEL.gate, MODEL.assist_gate):
        if g is not None:
            g.set()
    ctx.close()
    assert not errors, errors


def ask_plan(page, plan, words="make it tighter and open on the walk", *, wait_proposal=True):
    MODEL.plans.append(plan)
    page.keyboard.press("Escape")
    page.keyboard.press("Control+j")
    page.wait_for_function(
        "document.activeElement && document.activeElement.dataset.testid === 'ai-prompt-input'")
    page.keyboard.type(words)
    page.keyboard.press("Enter")
    if wait_proposal:
        page.wait_for_function("!!pendingPlan && !!pendingPlan.job", timeout=30000)


def the_job(page) -> str:
    return page.evaluate("pendingPlan.job")


# ---------------------------------------------------------------- (a) Discard is undoable

def test_discard_is_undoable_by_the_toast_and_by_ctrl_z(board):
    page, _ = board
    ask_plan(page, PLAN_1)
    job = the_job(page)
    shots = page.evaluate("pendingPlan.segments.length")
    page.locator("#rejectProposal").click()
    page.wait_for_function("pendingPlan === null")
    toast = page.locator("#toast")
    assert "set aside" in toast.inner_text() and "Ctrl+Z" in toast.inner_text()
    assert page.locator("#toast .toastAct").inner_text() == "Undo"
    assert page.evaluate("tl.nextUndo().tag") == f"proposal:{job}"

    page.keyboard.press("Control+z")                  # Ctrl+Z brings it back
    page.wait_for_function("!!pendingPlan")
    assert page.evaluate("pendingPlan.job") == job
    assert page.evaluate("pendingPlan.segments.length") == shots
    assert page.locator("#proposal").is_visible() and page.locator("#acceptProposal").is_visible()

    page.locator("#rejectProposal").click()           # and the toast's own button does too
    page.wait_for_function("pendingPlan === null")
    page.locator("#toast .toastAct").click()
    page.wait_for_function("!!pendingPlan")
    assert page.evaluate("pendingPlan.job") == job

    page.locator("#rejectProposal").click()           # redo puts it away again
    page.wait_for_function("pendingPlan === null")
    page.keyboard.press("Control+z")
    page.wait_for_function("!!pendingPlan")
    page.keyboard.press("Control+Shift+z")
    page.wait_for_function("pendingPlan === null")
    assert page.evaluate("segs.length") == 3, "the cut itself was never touched by a Discard"


def test_discard_does_not_look_like_accepts_twin(board):
    page, _ = board
    ask_plan(page, PLAN_1)
    acc, rej = page.locator("#acceptProposal"), page.locator("#rejectProposal")
    a = acc.evaluate("e => { const c = getComputedStyle(e); return [c.borderColor, c.backgroundColor]; }")
    r = rej.evaluate("e => { const c = getComputedStyle(e); return [c.borderColor, c.backgroundColor]; }")
    assert a != r, "Accept and Discard are drawn differently"
    assert "ghost" in (rej.get_attribute("class") or "")
    assert "Ctrl+Z" in (rej.get_attribute("title") or "")


# ---------------------------------------------------------------- (b) the list

def test_the_assistant_lists_proposals_and_a_replaced_one_can_be_accepted(board):
    page, _ = board
    ask_plan(page, PLAN_1, "first idea for the cut")
    first = the_job(page)
    ask_plan(page, PLAN_2, "second idea for the cut", wait_proposal=False)
    page.wait_for_function(f"!!pendingPlan && pendingPlan.job !== '{first}'", timeout=30000)
    second = the_job(page)
    assert "kept under Recent proposals" in page.locator("#toast").inner_text()
    page.wait_for_selector("#askRecent .rr")
    rows = page.evaluate("[...document.querySelectorAll('#askRecent .rr')].map(r => [r.dataset.job, r.dataset.status])")
    assert rows[0] == [second, "screen"] and [first, "replaced"] in rows

    page.locator(f"#askRecent .rr[data-job='{first}'] .rr-open").click()
    page.wait_for_function(f"pendingPlan && pendingPlan.job === '{first}'")
    page.wait_for_function("document.querySelector('#askRecent .rr[data-status=replaced]')")
    page.locator("#acceptProposal").click()
    page.wait_for_function("segs.length === 3 && segs[0].clip === 'CLIP_C.MP4'")
    page.wait_for_function(
        f"[...document.querySelectorAll('#askRecent .rr')].some(r => r.dataset.job === '{first}' && r.dataset.status === 'accepted')")


def test_a_waiting_proposal_survives_a_reload_and_a_saved_copy(board, live_server, project):
    page, _ = board
    ask_plan(page, PLAN_1)
    job = the_job(page)
    page.reload()
    page.wait_for_selector("#tl .blk")
    page.wait_for_function(f"pendingPlan && pendingPlan.job === '{job}'", timeout=15000)
    # FINDER-20's steps: cut switcher > save a copy, work on it, come back to main
    import server
    main = str(server.STATE["edl"])
    r = page.evaluate("fetch('/api/cuts/copy', {method:'POST', headers:{'content-type':'application/json'},"
                      " body: JSON.stringify({name: 'finder selects'})}).then(r => r.status)")
    assert r == 200
    page.reload()
    page.wait_for_selector("#tl .blk")
    page.wait_for_timeout(800)
    assert page.evaluate("pendingPlan") is None, "the copy is another cut: the proposal waits for main"
    page.evaluate("(p) => fetch('/api/cuts/open', {method:'POST', headers:{'content-type':'application/json'},"
                  " body: JSON.stringify({path: p})})", main)
    for p in (project["work"] / "projects").rglob("finder-selects.edl.json"):
        p.unlink()
    page.reload()
    page.wait_for_selector("#tl .blk")
    page.wait_for_function(f"pendingPlan && pendingPlan.job === '{job}'", timeout=15000)


# ---------------------------------------------------------------- (c) Stop

def test_the_whole_film_recut_has_a_stop_that_stops_it(board):
    page, _ = board
    MODEL.gate = threading.Event()
    ask_plan(page, PLAN_1, "re-cut the whole film", wait_proposal=False)
    page.wait_for_selector("[data-testid=ai-plan]", timeout=30000)
    stop = page.locator("[data-testid=ai-plan-stop]")
    stop.wait_for(state="visible", timeout=15000)
    assert page.locator("[data-testid=ai-review-close]").inner_text() == "Hide", "Close only hides it, and says so"
    import server
    ask = next(j for j in server.ASKS.values() if j["state"] not in ("done", "failed", "cancelled"))
    stop.click()
    page.wait_for_function("document.querySelector('.air-plan-what').textContent.startsWith('Stopped')", timeout=15000)
    line = page.locator(".air-plan-what").inner_text()
    assert "no proposal was kept" in line and "cut stays as it was" in line
    assert page.locator("[data-testid=ai-plan-stop]").count() == 0
    page.wait_for_function("[...document.querySelectorAll('.aip-dim')].some(e => e.textContent.includes('Stopped'))")
    assert ask["state"] == "cancelled"
    MODEL.gate.set()
    time.sleep(0.8)
    assert not (server.STATE["asks"] / f"{ask['id']}.json").exists()
    assert page.evaluate("pendingPlan") is None


def test_every_running_ai_job_has_an_x_on_its_pill(board):
    """The header's pill for an Ask carries ×; pressing it stops the job and says so."""
    page, _ = board
    MODEL.gate = threading.Event()
    ask_plan(page, PLAN_1, "re-cut the whole film", wait_proposal=False)
    page.wait_for_selector("#progress .job[data-kind=ask] .jx", timeout=30000)
    page.locator("#progress .job[data-kind=ask] .jx").click()
    page.wait_for_function("document.querySelector('#toast').textContent.includes('no proposal was kept')")
    import server
    assert list(server.ASKS.values())[-1]["state"] == "cancelled"
    MODEL.gate.set()


# ---------------------------------------------------------------- (d) the banner after Accept

def test_after_accept_nothing_still_says_a_new_cut_is_ready(board):
    page, _ = board
    ask_plan(page, PLAN_1)
    page.wait_for_selector("[data-testid=ai-result]", timeout=15000)
    assert "new cut is ready" in page.locator("[data-testid=ai-result]").inner_text()
    page.locator("#acceptProposal").click()
    page.wait_for_function("segs.length === 3 && segs[0].clip === 'CLIP_C.MP4'")
    page.wait_for_function("document.querySelector('[data-testid=ai-result]') === null")
    assert "new cut is ready" not in (page.locator("[data-testid=ai-status]").inner_text() or "")
    assert page.locator("[data-testid=ai-plan]").count() == 0
    assert page.evaluate("pendingPlan") is None
    # Next does not offer a first cut over the cut
    page.wait_for_timeout(2500)
    nxt = page.evaluate("(document.querySelector('#flowNext') || {innerText: ''}).innerText")
    assert "first cut" not in nxt


def test_discard_clears_the_banner_too(board):
    page, _ = board
    ask_plan(page, PLAN_1)
    page.wait_for_selector("[data-testid=ai-result]", timeout=15000)
    page.locator("#rejectProposal").click()
    page.wait_for_function("document.querySelector('[data-testid=ai-result]') === null")


# ---------------------------------------------------------------- (e) undo that stays

def _undo_title(page) -> str:
    return page.locator("#undo").get_attribute("title")


def test_undo_survives_a_reload(board):
    page, _ = board
    page.locator("#tl .blk").nth(1).click()
    page.keyboard.press("Delete")
    page.wait_for_function("segs.length === 2")
    assert _undo_title(page).startswith("undo: remove")
    page.wait_for_function("document.querySelector('#saveState').dataset.state === 'saved'")
    page.wait_for_timeout(700)                      # the history is written a moment after the edit
    page.reload()
    page.wait_for_selector("#tl .blk")
    page.wait_for_function("window.rcReopenProposal")
    page.wait_for_function("!document.querySelector('#undo').disabled", timeout=10000)
    assert _undo_title(page).startswith("undo: remove")
    page.keyboard.press("Control+z")
    page.wait_for_function("segs.length === 3")
    page.keyboard.press("Control+Shift+z")          # and the redo came with it
    page.wait_for_function("segs.length === 2")


def test_undo_survives_a_switch_to_another_cut_and_back(board, project):
    page, _ = board
    import server
    main = str(server.STATE["edl"])
    page.locator("#tl .blk").nth(0).click()
    page.keyboard.press("Delete")
    page.wait_for_function("segs.length === 2")
    page.wait_for_function("document.querySelector('#saveState').dataset.state === 'saved'")
    page.wait_for_timeout(700)
    # the switcher's own order: flush, switch on the server, reload
    page.evaluate("fetch('/api/cuts/copy', {method:'POST', headers:{'content-type':'application/json'},"
                  " body: JSON.stringify({name: 'peek'})})")
    page.reload()
    page.wait_for_selector("#tl .blk")
    page.wait_for_timeout(600)
    assert page.locator("#undo").get_attribute("title") == "nothing to undo", "the copy has its own history"
    page.evaluate("(p) => fetch('/api/cuts/open', {method:'POST', headers:{'content-type':'application/json'},"
                  " body: JSON.stringify({path: p})})", main)
    for p in (project["work"] / "projects").rglob("peek.edl.json"):
        p.unlink()
    page.reload()
    page.wait_for_selector("#tl .blk")
    page.wait_for_function("!document.querySelector('#undo').disabled", timeout=10000)
    page.keyboard.press("Control+z")
    page.wait_for_function("segs.length === 3")


def test_a_cut_changed_elsewhere_does_not_get_old_undo_put_back_over_it(board, project):
    page, _ = board
    page.locator("#tl .blk").nth(0).click()
    page.keyboard.press("Delete")
    page.wait_for_function("segs.length === 2")
    page.wait_for_function("document.querySelector('#saveState').dataset.state === 'saved'")
    page.wait_for_timeout(700)
    edl = json.loads(Path(project["edl"]).read_text(encoding="utf-8"))
    edl["segments"] = edl["segments"][:1]           # changed by something else (a design accepted on the server)
    Path(project["edl"]).write_text(json.dumps(edl), encoding="utf-8")
    page.reload()
    page.wait_for_selector("#tl .blk")
    page.wait_for_timeout(800)
    assert page.locator("#undo").get_attribute("title") == "nothing to undo"
    assert page.evaluate("segs.length") == 1


def test_undo_and_redo_stay_on_an_emptied_sequence(board):
    page, _ = board
    page.locator("#tl .blk").nth(0).click()
    page.keyboard.press("Control+a")
    page.keyboard.press("Delete")
    page.wait_for_function("segs.length === 0")
    assert page.locator("#undo").is_visible() and page.locator("#redo").is_visible()
    assert not page.locator("#undo").is_disabled()
    page.locator("#undo").click()                    # with the mouse, as the editor did
    page.wait_for_function("segs.length === 3")
    page.wait_for_timeout(2200)
    nxt = page.evaluate("(document.querySelector('#flowNext') || {innerText: ''}).innerText")
    assert "first cut" not in nxt


# ---------------------------------------------------------------- the round-2 holes (wave 23 SAFE)

EDIT_OPS = [{"op": "set", "id": "shot-a", "prop": "contrast", "value": 20},
            {"op": "set", "id": "shot-b", "prop": "saturation", "value": 115}]


def ask_edits(page, words="give the first two shots more punch"):
    MODEL.assist = [{"kind": "ops", "summary": "more punch", "ops": EDIT_OPS}]
    page.keyboard.press("Escape")
    page.keyboard.press("Control+j")
    page.wait_for_function(
        "document.activeElement && document.activeElement.dataset.testid === 'ai-prompt-input'")
    page.keyboard.type(words)
    page.keyboard.press("Enter")
    page.wait_for_selector("[data-testid=ai-result][data-kind=ops]", timeout=30000)
    op = page.locator("[data-testid=ai-result-open]")
    if op.count() and op.is_visible() and op.inner_text().strip() == "Review":
        op.click()
    page.wait_for_selector("[data-testid=ai-review] [data-testid=ai-op-row]")


def test_a_proposal_that_predates_later_edits_says_so_and_asks_before_it_replaces_them(board):
    """CLUMSY-02 / -04: two proposals waiting; accept one; the other (reopened) predates that
    change: its card says how many changes, names them, and Accept asks once; one undo."""
    page, _ = board
    ask_plan(page, PLAN_1, "first idea for the cut")
    first = the_job(page)
    ask_plan(page, PLAN_2, "second idea for the cut", wait_proposal=False)
    page.wait_for_function(f"pendingPlan && pendingPlan.job !== '{first}'", timeout=30000)
    second = the_job(page)
    page.wait_for_timeout(800)
    assert page.locator("#proposalStale").is_hidden(), "a fresh proposal is of the cut as it is"
    page.locator("#acceptProposal").click()                       # the second is accepted
    page.wait_for_function("segs.length === 2")
    page.wait_for_function("document.querySelector('#saveState').dataset.state === 'saved'")
    page.locator(f"#askRecent .rr[data-job='{first}'] .rr-open").click()      # the first, made before it
    page.wait_for_function(f"pendingPlan && pendingPlan.job === '{first}'")
    page.wait_for_selector("#proposalStale", state="visible", timeout=10000)
    said = page.locator("#proposalStale").inner_text()
    assert "Made before your last change" in said and "proposal" in said and "Its notes describe the older cut" in said
    page.locator("#acceptProposal").click()                       # asks once: nothing changes yet
    page.wait_for_timeout(500)
    assert page.evaluate("segs.length") == 2 and page.evaluate("!!pendingPlan")
    assert "Accept anyway" in page.locator("#acceptProposal").inner_text()
    page.locator("#acceptProposal").click()
    page.wait_for_function("segs.length === 3")
    assert page.evaluate("pendingPlan") is None
    page.keyboard.press("Control+z")                              # the accept is one undo
    page.wait_for_function("segs.length === 2")


def test_set_aside_ai_edits_are_undoable_and_listed(board):
    """CLUMSY-17: Reject all on the AI edit card leaves an entry: Ctrl+Z brings the card back and
    never reaches past it to the edit before; Recent proposals lists the edits."""
    page, _ = board
    page.locator("#tl .blk").nth(2).click()
    page.keyboard.press("Delete")                                  # an older edit
    page.wait_for_function("segs.length === 2")
    ask_edits(page)
    page.locator("[data-testid=ai-reject-all]").click()
    page.wait_for_selector("[data-testid=ai-review]", state="hidden")
    assert "set aside" in page.locator("#toast").inner_text()
    assert page.locator("#toast .toastAct").inner_text() == "Undo"
    assert page.evaluate("tl.nextUndo().tag").startswith("proposal:assist:")
    page.wait_for_selector("#askRecent .rr[data-kind=edits]", timeout=10000)
    assert "set aside" in page.locator("#askRecent .rr[data-kind=edits] .rr-st").inner_text()
    page.keyboard.press("Control+z")
    page.wait_for_selector("[data-testid=ai-review] [data-testid=ai-op-row]", timeout=10000)
    assert page.evaluate("segs.length") == 2, "the older edit was not undone"
    page.locator("[data-testid=ai-reject-all]").click()            # and the toast's button does too
    page.wait_for_selector("[data-testid=ai-review]", state="hidden")
    page.locator("#toast .toastAct").click()
    page.wait_for_selector("[data-testid=ai-review] [data-testid=ai-op-row]", timeout=10000)
    page.locator("[data-testid=ai-accept-all]").click()            # reopened edits can be accepted
    page.wait_for_function("document.querySelector('[data-testid=ai-review]').hidden")
    page.wait_for_function("tl.byId('shot-a').controls && tl.byId('shot-a').controls.contrast === 20")


def test_an_accepted_proposal_that_was_undone_says_so(board):
    page, _ = board
    ask_plan(page, PLAN_1)
    page.locator("#acceptProposal").click()
    page.wait_for_function("segs.length === 3 && segs[0].clip === 'CLIP_C.MP4'")
    page.wait_for_selector("#askRecent .rr[data-status=accepted]", timeout=10000)   # its answer is on the record
    page.keyboard.press("Control+z")
    page.wait_for_function("segs[0].clip === 'CLIP_A.MP4'")
    page.wait_for_selector("#askRecent .rr[data-status=undone]", timeout=10000)
    assert page.locator("#askRecent .rr[data-status=undone] .rr-st").inner_text() == "accepted, then undone"
    assert page.locator("#askRecent .rr[data-status=undone] .rr-open").inner_text() == "Accept again"
    page.keyboard.press("Control+Shift+z")                         # redone: accepted again
    page.wait_for_selector("#askRecent .rr[data-status=accepted]", timeout=10000)


def test_recent_proposals_show_the_editors_words_not_the_plan_markup(board):
    page, _ = board
    page.evaluate("fetch('/api/ask', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({"
                  "note: 'put the walk first\\n\\n\\u27e6the plan\\u27e7\\nOpen on CLIP_C, then the greeting.', segments: segs})})")
    page.wait_for_function("document.querySelector('#progress .job[data-kind=ask]') === null", timeout=30000)
    page.reload()
    page.wait_for_selector("#askRecent .rr", timeout=15000)
    row = page.locator("#askRecent .rr").first
    assert "put the walk first" in row.inner_text() and "the plan" not in row.inner_text()
    assert "Open on CLIP_C" in (row.get_attribute("title") or "")


# ---------------------------------------------------------------- Stop beside the prompt

def test_the_stop_beside_the_prompt_stops_a_short_ask(board):
    """CLUMSY-11 / -05 / -12: Stop 4 s into a short ask ends it; the counter stops; nothing arrives."""
    page, _ = board
    MODEL.assist_gate = threading.Event()
    MODEL.assist = [{"kind": "answer", "text": "the second shot is a greeting"}]
    page.keyboard.press("Control+j")
    page.keyboard.type("what is in the second shot")
    page.keyboard.press("Enter")
    stop = page.locator("[data-testid=ai-stop]")
    stop.wait_for(state="visible", timeout=10000)
    page.wait_for_timeout(4200)                                    # the Stop stays beyond the first seconds
    assert stop.is_visible()
    t0 = page.locator(".aip-time").inner_text()
    stop.click()
    page.wait_for_function("document.querySelector('[data-testid=ai-status]').innerText.includes('Stopped')")
    MODEL.assist_gate.set()                                        # the call returns after the Stop ...
    page.wait_for_timeout(2500)
    assert "Stopped" in page.locator("[data-testid=ai-status]").inner_text(), "... and nothing arrives"
    assert page.locator("[data-testid=ai-result]").count() == 0 and page.locator("#aiReview:visible").count() == 0
    assert t0 and page.locator(".aip-time").count() == 0, "the counter stopped with it"


def test_stop_beside_the_prompt_on_itemised_edits_keeps_the_cut(board):
    page, _ = board
    MODEL.assist_gate = threading.Event()
    MODEL.assist = [{"kind": "ops", "summary": "more punch", "ops": EDIT_OPS}]
    page.keyboard.press("Control+j")
    page.keyboard.type("more punch on the first two")
    page.keyboard.press("Enter")
    page.locator("[data-testid=ai-stop]").click(timeout=10000)
    MODEL.assist_gate.set()
    page.wait_for_timeout(2000)
    assert page.locator("[data-testid=ai-review]:visible").count() == 0
    assert page.evaluate("tl.byId('shot-a').controls") in (None, {})


# ---------------------------------------------------------------- undo across the switcher's own clicks

def _switch_bin(page, path):
    page.click("#hdBin")
    page.wait_for_selector("#pickerPath")
    page.fill("#pickerPath", str(path))
    with page.expect_navigation():
        page.press("#pickerPath", "Enter")
    page.wait_for_selector("#tl, #tlEmpty", timeout=20000)
    page.wait_for_timeout(1500)


def test_undo_survives_the_switchers_own_clicks_to_another_film_and_back(board, project, tmp_path):
    """WORDS-19 and the first round trip: edits, an accepted proposal, then the title-bar switcher
    to another film and back."""
    import shutil
    page, _ = board
    other = tmp_path / "otherfilm"
    other.mkdir()
    shutil.copy(project["footage"] / "CLIP_A.MP4", other / "CLIP_A.MP4")
    page.locator("#tl .blk").nth(2).click()
    page.keyboard.press("Delete")
    page.wait_for_function("segs.length === 2")
    ask_plan(page, PLAN_1)
    page.locator("#acceptProposal").click()
    page.wait_for_function("segs.length === 3 && segs[0].clip === 'CLIP_C.MP4'")
    page.wait_for_function("document.querySelector('#saveState').dataset.state === 'saved'")
    depth = page.evaluate("tl.history().undo.length")
    assert depth >= 2
    _switch_bin(page, other)                       # at once: no waiting for any debounce
    assert page.locator("#undo").get_attribute("title") == "nothing to undo"
    _switch_bin(page, project["footage"])
    page.wait_for_selector("#tl .blk")
    page.wait_for_function("!document.querySelector('#undo').disabled", timeout=10000)
    assert page.evaluate("tl.history().undo.length") == depth
    page.keyboard.press("Control+z")               # the accepted proposal, one undo
    page.wait_for_function("segs.length === 2")


def test_undo_survives_a_copy_and_the_return_to_the_original_through_the_switcher(board, project):
    page, _ = board
    page.locator("#tl .blk").nth(0).click()
    page.keyboard.press("Delete")
    page.wait_for_function("segs.length === 2")
    page.wait_for_function("document.querySelector('#saveState').dataset.state === 'saved'")
    page.click("#hdBin")
    page.fill("#copyName", "peek copy")
    with page.expect_navigation():
        page.click("#copyGo")
    page.wait_for_selector("#tl .blk")
    page.wait_for_timeout(1000)
    assert page.locator("#undo").get_attribute("title") == "nothing to undo"
    page.click("#hdBin")
    page.wait_for_selector("#cutList .crow")
    with page.expect_navigation():
        page.locator("#cutList .crow:not(.current)").first.click()
    page.wait_for_selector("#tl .blk")
    page.wait_for_function("!document.querySelector('#undo').disabled", timeout=10000)
    for p in (project["work"] / "projects").rglob("peek-copy.edl.json"):
        p.unlink()
    page.keyboard.press("Control+z")
    page.wait_for_function("segs.length === 3")


def test_a_reopened_accepted_proposal_stays_waiting_through_a_copy_a_return_and_a_reload(board, project):
    """Finder 8: nothing is inferred from the cut. The proposal is the cut already there, says so up
    front, and keeps its state and its Proposal lane through a switch of cuts and back and a reload."""
    import server
    page, _ = board
    ask_plan(page, PLAN_1)
    job = the_job(page)
    page.locator("#acceptProposal").click()
    page.wait_for_function("segs.length === 3 && segs[0].clip === 'CLIP_C.MP4'")
    page.wait_for_selector(f"#askRecent .rr[data-job='{job}'][data-status=accepted]", timeout=10000)
    page.locator(f"#askRecent .rr[data-job='{job}'] .rr-open").click()
    page.wait_for_function(f"pendingPlan && pendingPlan.job === '{job}'")
    page.wait_for_selector("#proposalStale", state="visible", timeout=10000)
    assert "the cut you already have" in page.locator("#proposalStale").inner_text()
    assert page.locator("#acceptProposal").is_disabled()
    main = str(server.STATE["edl"])
    page.evaluate("fetch('/api/cuts/copy', {method:'POST', headers:{'content-type':'application/json'},"
                  " body: JSON.stringify({name: 'same peek'})})")
    page.reload()
    page.wait_for_selector("#tl .blk")
    page.evaluate("(p) => fetch('/api/cuts/open', {method:'POST', headers:{'content-type':'application/json'},"
                  " body: JSON.stringify({path: p})})", main)
    for p in (project["work"] / "projects").rglob("same-peek.edl.json"):
        p.unlink()
    page.reload()
    page.wait_for_selector("#tl .blk")
    page.wait_for_function(f"pendingPlan && pendingPlan.job === '{job}'", timeout=15000)
    assert page.locator("#proposal").is_visible()
    rows = page.evaluate("fetch('/api/asks/recent').then(r => r.json()).then(d => d.proposals.map(r => [r.job, r.status]))")
    assert [job, "waiting"] in rows
