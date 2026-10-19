"""The AI prompt and the AI's edits, run together through the real server (redesign wave 12,
lane AIGLUE; REDESIGN-GOAL dimensions 14-15, Karl's K3 c and d).

Lane AIPROMPT built the prompt (`/modules/aiprompt.js`) against a FAKED `/api/assist`; lane
AIOPS built the server (`/api/assist*`, roughcut/assist.py) and the review panel
(`/modules/actions.js`). This is the seam between them, with nothing faked in the page:
no `page.route` on `/api/assist`, the real uvicorn server on the fixture bin, and a scripted
model behind `roughcut.inference.set_backend` (tools/journey.py's pattern) — never a real call.

A Premiere editor presses Ctrl+J, types, presses Enter; the status line says Working at once
with the honest price ("free on your plan", the API estimate in the hover, from
`/api/assist/price`); the answer comes back as "3 changes ready · Review" and the AI edit
panel with one row per op; Accept all puts the edits in the EDL on disk; one Ctrl+Z takes
them all back. A whole-cut rewrite opens the existing proposal once its Ask is done; a
question opens the answer popover; a failed call says why; Stop cancels the job on the
server and a job cancelled elsewhere reads as stopped. The console never shows a 404.

Set `ROUGHCUT_SHOTS=<dir>` to keep screenshots of the ask → result → review → accepted
sequence at 1440×900.
"""

from __future__ import annotations

import json
import os
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
PLAN = [{"clip": "CLIP_B.MP4", "in": 0.5, "out": 2.0, "why": "the teaser opens on hello"},
        {"clip": "CLIP_A.MP4", "in": 2.4, "out": 4.0, "why": "how are you"}]
INPUT = '[data-testid="ai-prompt-input"]'
STATUS = '[data-testid="ai-status"]'
RESULT = '[data-testid="ai-result"]'
SHOTS = os.environ.get("ROUGHCUT_SHOTS")


class Model:
    """The assist call (its schema has `kind`) answers `answer`, or raises `fail`, after
    `gate` opens; the Ask's cut call (a `plan`'s re-cut) answers PLAN; anything else (the
    ETA estimate) "{}", so it falls back."""
    name = "junction-scripted"

    def __init__(self):
        self.answer: dict = {"kind": "answer", "text": "ok"}
        self.fail: str | None = None
        self.gate: threading.Event | None = None
        self.plan_gate: threading.Event | None = None
        self.asks: list[str] = []

    def complete(self, request):
        schema = request.schema or {}
        if "kind" in schema:
            self.asks.append(request.prompt)
            if self.gate is not None:
                self.gate.wait(20)
            if self.fail:
                raise inference.InferenceError(self.fail)
            text = json.dumps(self.answer)
        elif "segments" in schema:
            if self.plan_gate is not None:
                self.plan_gate.wait(20)
            text = json.dumps({"segments": PLAN, "notes": "a teaser"})
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
    for g in (MODEL.gate, MODEL.plan_gate):
        if g is not None:
            g.set()
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


@pytest.fixture(autouse=True)
def fresh_model():
    MODEL.answer = {"kind": "answer", "text": "ok"}
    MODEL.fail = None
    MODEL.gate = None
    MODEL.plan_gate = None
    MODEL.asks = []
    yield
    for g in (MODEL.gate, MODEL.plan_gate):
        if g is not None:
            g.set()


class Board:
    """One fresh page on a freshly seeded EDL; every page error and every console error
    (a 404 included) is kept and fails the test at close."""

    def __init__(self, browser, url, project):
        _seed(project)
        self.project = project
        self.url = url
        self.ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        self.page = self.ctx.new_page()
        self.errors: list[str] = []
        self.console: list[str] = []
        self.requests: list[str] = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on("console", lambda m: self.console.append(m.text) if m.type == "error" else None)
        self.page.on("request", lambda r: self.requests.append(f"{r.method} {r.url}")
                     if "/api/assist" in r.url else None)
        self.page.on("response", lambda r: self.console.append(f"{r.status} {r.url}")
                     if r.status >= 400 and "/api/" in r.url else None)
        self.page.goto(url)
        self.page.wait_for_selector("#tl .blk")
        self.page.wait_for_function(
            "window.rcActions && window.rcAI && window.effects && window.tlLanes"
            " && typeof C !== 'undefined' && C && C.shots", timeout=15000)
        self.page.wait_for_selector(INPUT)

    def close(self):
        errs, cons = list(self.errors), list(self.console)
        self.ctx.close()
        assert errs == [], errs
        assert cons == [], cons

    def shot(self, name: str) -> None:
        if SHOTS:
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(Path(SHOTS) / f"{name}.png"))

    def raw(self) -> bytes:
        return Path(self.project["edl"]).read_bytes()

    def settle(self) -> dict:
        """Every save the board has waiting written, then the EDL as it is on disk."""
        self.page.evaluate("""async () => {
          for (let k = 0; k < 3; k++) {
            await roughcutFlush();
            if (tl.saveSeq) await tl.saveSeq();
            await new Promise((r) => setTimeout(r, 60));
          }
        }""")
        return json.loads(self.raw())

    def ask(self, text: str) -> None:
        """What an editor does: Ctrl+J from the timeline, the words, Enter."""
        self.page.locator("#tl .tl-ruler, #tl").first.click(position={"x": 5, "y": 5})
        self.page.keyboard.press("Control+j")
        assert self.page.evaluate("document.activeElement.dataset.testid") == "ai-prompt-input"
        self.page.keyboard.type(text)
        self.page.keyboard.press("Enter")

    def state(self) -> str:
        return self.page.locator(STATUS).get_attribute("data-state")

    def wait_state(self, want: str, timeout: float = 15000) -> None:
        self.page.wait_for_function(
            f"document.querySelector('{STATUS}').dataset.state === '{want}'", timeout=timeout)

    def undo(self) -> None:
        self.page.evaluate("document.activeElement && document.activeElement.blur()")
        self.page.keyboard.press("Control+z")


def _cut(edl: dict) -> dict:
    return {k: edl.get(k) for k in ("segments", "transitions", "markers", "in", "out", "colour")}


# ---------------------------------------------------------------- an op list, end to end

def test_ask_review_accept_and_one_ctrl_z_through_the_real_server(browser, live_server, project):
    MODEL.answer = {"kind": "ops", "summary": "a tighter open, a punchier shot 2 and a dissolve",
                    "ops": [{"op": "trim", "id": "shot-a", "in": 1.5},
                            {"op": "set", "id": "shot-b", "prop": "contrast", "value": 20},
                            {"op": "transition", "a": "shot-b", "b": "shot-c", "kind": "cross_dissolve",
                             "dur": 0.5}]}
    b = Board(browser, live_server, project)
    try:
        seeded = _cut(b.settle())
        pg = b.page
        # the price before the ask: the assist's own, from the prompt it would send
        pg.locator(INPUT).focus()
        pg.wait_for_function(f"document.querySelector('{STATUS}').textContent.includes('free on your plan')")
        b.shot("1-prompt")
        b.ask("tighten the intro, punch up shot 2, dissolve 2 to 3")
        st = pg.locator(STATUS)
        pg.wait_for_function(f"['working', 'done'].includes(document.querySelector('{STATUS}').dataset.state)",
                             timeout=3000)
        b.wait_state("done")
        result = pg.locator(RESULT)
        assert result.inner_text().replace("\n", " ").replace("✓ ", "✓").startswith("✓3 changes ready"), \
            result.inner_text()
        assert "Review" in result.inner_text()
        # the result line carries the measured call's words (section S): no bill on the plan
        assert "$" not in st.inner_text()
        assert "API" in (result.get_attribute("title") or "")
        b.shot("2-result")
        # the review opened by itself, one row per op, the asked words at its head
        review = pg.locator("[data-testid=ai-review]")
        review.wait_for(state="visible", timeout=5000)
        rows = pg.locator("[data-testid=ai-op-row]")
        assert rows.count() == 3
        assert [rows.nth(i).get_attribute("data-op") for i in range(3)] == ["trim", "set", "transition"]
        assert "tighten the intro, punch up shot 2, dissolve 2 to 3" in review.inner_text()
        assert pg.locator("[data-testid=ai-before-after]").is_visible()     # the colour op's After
        pg.wait_for_function("tlLanes.ghost() && tlLanes.ghost().ghosts.length === 3", timeout=5000)
        assert b.settle()["segments"] == SEGS                              # nothing landed yet
        b.shot("3-review")
        pg.locator("[data-testid=ai-accept-all]").click()
        pg.wait_for_function("!rcActions.open")
        edl = b.settle()
        segs = edl["segments"]
        assert segs[0]["in"] == 1.5
        assert segs[1]["controls"]["contrast"] == 20
        assert [(t["kind"], t["a"], t["b"]) for t in edl["transitions"]] == [("cross_dissolve", "shot-b", "shot-c")]
        assert (pg.locator("#undo").get_attribute("title") or "").startswith("undo: AI: ")
        # the line says they landed and how to undo them — in words: a click on the line
        # (the Timeline's header row) never undoes (verdict 10, punch 2); there is nothing
        # left to review (a second Accept of the same answer would apply it twice)
        pg.wait_for_function(f"document.querySelector('{RESULT}').textContent.includes('3 changes applied')",
                             timeout=3000)
        assert "Review" not in pg.locator(RESULT).inner_text()
        assert pg.locator(f'{STATUS} [data-testid="ai-undo-hint"]').inner_text().strip() == "Ctrl+Z undoes"
        assert pg.locator(f'{STATUS} button[data-ai="undo"]').count() == 0
        b.shot("4-accepted")
        landed = _cut(b.settle())
        assert pg.evaluate("rcActions.review(rcAI.state().result)") is False   # a re-open never re-applies
        assert not pg.evaluate("rcActions.open")
        assert _cut(b.settle()) == landed
        # the review is on the record: all three accepted
        job = pg.evaluate("rcAI.state().job")
        rec = pg.evaluate("(j) => fetch('/api/assist/' + j).then(r => r.json())", job)
        assert rec["answer"]["accepted"] == [0, 1, 2] and rec["answer"]["rejected"] == []
        # a click on the line changes nothing; one Ctrl+Z: the whole accept back, the prompt idle
        pg.locator(f'{STATUS} [data-testid="ai-undo-hint"]').click()
        assert _cut(b.settle()) == landed
        b.undo()
        pg.wait_for_function("tl.byId('shot-a').in === 1.0")
        assert _cut(b.settle()) == seeded, "one undo did not take the whole accept back"
        assert b.state() == "idle" and pg.locator(STATUS).is_hidden()
        pg.keyboard.press("Control+Shift+z")                               # redo: all of it again
        pg.wait_for_function("tl.byId('shot-a').in === 1.5")
        assert _cut(b.settle()) == landed
        b.undo()                                                           # and Ctrl+Z, the same
        pg.wait_for_function("tl.byId('shot-a').in === 1.0")
        assert _cut(b.settle()) == seeded, "one Ctrl+Z did not take the whole accept back"
        # what went out: the words, the scope (the whole sequence: nothing selected) and the playhead
        assert any(r.startswith("GET ") and "/api/assist/price?" in r for r in b.requests), b.requests
        assert not any("/api/ask/price" in r for r in b.requests)
        assert "tighten the intro" in MODEL.asks[0] and "The whole sequence" in MODEL.asks[0]
    finally:
        b.close()


def test_a_selection_scopes_the_ask_and_one_accept_of_two(browser, live_server, project):
    """The scope chip's selection reaches the model; a rejected row stays out."""
    MODEL.answer = {"kind": "ops", "summary": "slower and darker",
                    "ops": [{"op": "speed", "id": "shot-b", "rate": 0.5},
                            {"op": "set", "id": "shot-b", "prop": "exposure", "value": -0.2}]}
    b = Board(browser, live_server, project)
    try:
        pg = b.page
        pg.locator("#tl .blk").nth(1).click()
        assert pg.locator('[data-testid="ai-scope"]').inner_text().strip() == "Shot 2 · CLIP_B"
        pg.keyboard.press("Control+j")
        pg.keyboard.type("slow it down and darken it")
        pg.keyboard.press("Enter")
        pg.locator("[data-testid=ai-review] [data-testid=ai-op-row]").first.wait_for(timeout=15000)
        assert "The selected shots: shot 2 (CLIP_B) [id shot-b]" in MODEL.asks[0]
        pg.locator("[data-testid=ai-op-row][data-op-index='1'] [data-testid=ai-op-reject]").click()
        assert pg.locator("[data-testid=ai-accept-all]").inner_text().strip() == "Accept 1 of 2"
        pg.locator("[data-testid=ai-accept-all]").click()
        pg.wait_for_function("!rcActions.open")
        seg = b.settle()["segments"][1]
        assert seg.get("speed") == 0.5 and "exposure" not in (seg.get("controls") or {})
        pg.wait_for_function(f"document.querySelector('{RESULT}').textContent.includes('1 of 2 applied')",
                             timeout=3000)
        assert pg.locator(f'{STATUS} [data-testid="ai-undo-hint"]').is_visible()
        # another edit on top: Ctrl+Z would take back that one, so the hint goes
        pg.evaluate("tl.setRange('shot-a', 1.25, null)")
        pg.wait_for_function(f"!document.querySelector('{STATUS} [data-testid=ai-undo-hint]')", timeout=3000)
        assert "1 of 2 applied" in pg.locator(RESULT).inner_text()
        job = pg.evaluate("rcAI.state().job")
        rec = pg.evaluate("(j) => fetch('/api/assist/' + j).then(r => r.json())", job)
        assert rec["answer"] == {**rec["answer"], "accepted": [0], "rejected": [1]}
    finally:
        b.close()


# ---------------------------------------------------------------- the other kinds

def test_a_whole_cut_rewrite_opens_the_existing_proposal(browser, live_server, project):
    """`plan` starts an ordinary Ask job, which takes its own time: the prompt says the cut
    is on its way (never "ready" before it is), the review panel follows the Ask, and the
    proposal panel opens once it is done."""
    MODEL.answer = {"kind": "plan", "summary": "a 4 s teaser", "note": "make it a 4 s teaser"}
    MODEL.plan_gate = threading.Event()
    b = Board(browser, live_server, project)
    try:
        pg = b.page
        b.ask("make it a 4 s teaser")
        b.wait_state("done")
        result = pg.locator(RESULT)
        assert "ready" not in result.inner_text() and "Re-cutting" in result.inner_text(), result.inner_text()
        pg.locator("[data-testid=ai-plan]").wait_for(state="visible", timeout=5000)
        pg.wait_for_timeout(800)
        assert pg.evaluate("getComputedStyle(document.querySelector('#proposal')).display") != "block"
        MODEL.plan_gate.set()                                  # the re-cut comes back
        pg.wait_for_function("getComputedStyle(document.querySelector('#proposal')).display === 'block'",
                             timeout=20000)
        assert "2 shots" in pg.locator("#proposalDiff").inner_text()
        pg.wait_for_function(f"document.querySelector('{RESULT}').textContent.includes('A new cut is ready')",
                             timeout=5000)
        assert b.settle()["segments"] == SEGS                              # proposed, not applied
        assert not pg.evaluate("rcActions.open")
    finally:
        b.close()


def test_a_question_is_answered_in_the_popover(browser, live_server, project):
    MODEL.answer = {"kind": "answer", "text": "The intro is shot 1, 2.0 s long.", "summary": "2.0 s"}
    b = Board(browser, live_server, project)
    try:
        pg = b.page
        b.ask("how long is the intro?")
        pop = pg.locator('[data-testid="ai-answer"]')
        pop.wait_for(state="visible", timeout=15000)
        assert "The intro is shot 1, 2.0 s long." in pop.inner_text()
        assert "Answered" in pg.locator(RESULT).inner_text()
        assert not pg.evaluate("rcActions.open")
    finally:
        b.close()


def test_a_failed_call_says_why_and_retry_asks_again(browser, live_server, project):
    MODEL.fail = "the model is overloaded"
    b = Board(browser, live_server, project)
    try:
        pg = b.page
        b.ask("warm up the snow shots")
        b.wait_state("failed")
        assert "the model is overloaded" in pg.evaluate("rcAI.state().error")
        pg.locator('[data-testid="ai-details"]').click()
        assert "the model is overloaded" in pg.locator('[data-testid="ai-answer"]').inner_text()
        MODEL.fail = None
        MODEL.answer = {"kind": "ops", "summary": "warmer", "ops": [
            {"op": "set", "id": "shot-a", "prop": "temperature", "value": 20}]}
        pg.locator('[data-testid="ai-answer"] [data-pop="retry"]').click()
        b.wait_state("done")
        assert len(MODEL.asks) == 2
        pg.locator("[data-testid=ai-review] [data-testid=ai-op-row]").first.wait_for(timeout=5000)
        pg.locator("[data-testid=ai-reject-all]").click()
        pg.wait_for_function(f"document.querySelector('{RESULT}').textContent.includes('nothing changed')",
                             timeout=3000)
        assert b.settle()["segments"] == SEGS
    finally:
        b.close()


def test_every_op_refused_reads_as_an_answer_with_the_reason(browser, live_server, project):
    MODEL.answer = {"kind": "ops", "summary": "", "ops": [{"op": "razor", "id": "shot-zz", "at": 1.0}]}
    b = Board(browser, live_server, project)
    try:
        b.ask("cut shot 9 in half")
        pop = b.page.locator('[data-testid="ai-answer"]')
        pop.wait_for(state="visible", timeout=15000)
        assert "I could not make that edit" in pop.inner_text()
    finally:
        b.close()


# ---------------------------------------------------------------- stop, cancel

def test_stop_cancels_the_job_on_the_server_and_its_late_answer_is_dropped(browser, live_server, project):
    MODEL.gate = threading.Event()
    MODEL.answer = {"kind": "ops", "summary": "a cut", "ops": [{"op": "razor", "id": "shot-a", "at": 1.0}]}
    b = Board(browser, live_server, project)
    try:
        pg = b.page
        b.ask("cut shot 1 at one second")
        b.wait_state("working")
        pg.wait_for_function("rcAI.state().job", timeout=5000)
        job = pg.evaluate("rcAI.state().job")
        pg.locator('[data-testid="ai-stop"]').click()
        assert b.state() == "stopped"
        pg.wait_for_function("(j) => fetch('/api/assist/' + j).then(r => r.json()).then(d => window.__s = d.status)",
                             arg=job)
        pg.wait_for_function("window.__s === 'cancelled'", timeout=5000)
        MODEL.gate.set()                                   # the late answer
        pg.wait_for_timeout(1500)
        assert not pg.evaluate("rcActions.open")
        assert b.state() == "stopped"
        assert any(r == f"POST {live_server}/api/assist/{job}/cancel" for r in b.requests), b.requests
    finally:
        b.close()


def test_a_job_cancelled_elsewhere_reads_as_stopped(browser, live_server, project):
    """The fourth status: the job was cancelled by another tab (or the server); the
    prompt stops waiting and says nothing changed."""
    MODEL.gate = threading.Event()
    b = Board(browser, live_server, project)
    try:
        pg = b.page
        b.ask("anything")
        pg.wait_for_function("rcAI.state().job", timeout=5000)
        job = pg.evaluate("rcAI.state().job")
        pg.evaluate("(j) => fetch('/api/assist/' + j + '/cancel', {method: 'POST'})", job)
        b.wait_state("stopped", timeout=5000)
        assert "nothing changed" in pg.locator(STATUS).inner_text()
        MODEL.gate.set()
    finally:
        b.close()


def test_stop_before_the_job_id_comes_back_still_cancels_it(browser, live_server, project):
    """Stop pressed while the POST is still on its way: once the server answers with the
    job, the page tells it to cancel (the POST is only held here, then passed through to
    the real server)."""
    MODEL.gate = threading.Event()
    b = Board(browser, live_server, project)
    held = []
    try:
        pg = b.page
        pg.route("**/api/assist", lambda route: held.append(route) if route.request.method == "POST"
                 else route.continue_())
        b.ask("cut shot 1 at one second")
        b.wait_state("working")
        end = time.time() + 5
        while not held and time.time() < end:
            pg.wait_for_timeout(50)
        assert held, "the POST never left"
        pg.locator('[data-testid="ai-stop"]').click()
        assert b.state() == "stopped"
        held.pop().continue_()
        end = time.time() + 5
        while not any(r.endswith("/cancel") for r in b.requests) and time.time() < end:
            pg.wait_for_timeout(50)
        cancel = [r for r in b.requests if r.endswith("/cancel")]
        assert cancel, b.requests
        job = cancel[0].rsplit("/", 2)[-2]
        pg.wait_for_function("(j) => fetch('/api/assist/' + j).then(r => r.json()).then(d => window.__s = d.status)",
                             arg=job)
        pg.wait_for_function("window.__s === 'cancelled'", timeout=5000)
        MODEL.gate.set()
        pg.wait_for_timeout(800)
        assert b.state() == "stopped" and not pg.evaluate("rcActions.open")
    finally:
        b.page.unroute_all(behavior="ignoreErrors")
        b.close()


def test_the_price_is_the_assists_own_before_and_while_it_runs(browser, live_server, project):
    """Before: GET /api/assist/price with the words and the scope, its `words` on the line
    and its `hover` in the hover. While it runs: the job's own estimate, the same way."""
    MODEL.gate = threading.Event()
    b = Board(browser, live_server, project)
    try:
        pg = b.page
        pg.locator("#tl .blk").nth(2).click()
        pg.keyboard.press("Control+j")
        pg.keyboard.type("dissolve into this one")
        cost = pg.locator(f"{STATUS} .aip-cost")
        # the price follows the words once they rest, for the selected shot
        end = time.time() + 6
        while time.time() < end and not any("/api/assist/price?" in r and "dissolve" in r for r in b.requests):
            pg.wait_for_timeout(100)
        priced = [r for r in b.requests if "/api/assist/price?" in r and "dissolve" in r]
        assert priced, b.requests
        assert "shot-c" in priced[-1] and "selection" in priced[-1]
        q = priced[-1].split("?", 1)[1]
        price = pg.evaluate("(q) => fetch('/api/assist/price?' + q).then(r => r.json())", q)
        assert price["words"] == "free on your plan" and "API" in price["hover"]
        pg.wait_for_function(f"(w) => (document.querySelector('{STATUS} .aip-cost') || {{}}).textContent === w",
                             arg=price["words"])
        assert cost.get_attribute("title") == price["hover"]
        pg.keyboard.press("Enter")
        b.wait_state("working")
        pg.wait_for_function("rcAI.state().job", timeout=5000)
        job = pg.evaluate("rcAI.state().job")
        live = pg.evaluate("(j) => fetch('/api/assist/' + j).then(r => r.json())", job)
        assert live["status"] == "running" and live["cost"]["measured"] is False
        pg.wait_for_function(f"(h) => (document.querySelector('{STATUS} .aip-cost') || {{}}).title === h",
                             arg=live["cost"]["hover"], timeout=5000)
        assert "$" not in pg.locator(STATUS).inner_text()
        MODEL.gate.set()
        b.wait_state("done")
    finally:
        b.close()
