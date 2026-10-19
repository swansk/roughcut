"""The first pass in the workspace, in a real browser (REDESIGN-GOAL dimension 2, K1).

Karl, 2026-10-14: *"it is not clear how to re run the first pass."* Verdict 2: the
server had `GET/POST /api/firstpass` and no file under app/static called it. Now a
header control (`/modules/firstpass.js`) reads "Run first pass" / "First pass · heard
3/12 · looked 0/12" / "Re-run first pass", one click opens its panel (the steps and
their price, what a re-run keeps, Run), and the result opens in the Assistant as an
ordinary proposal.

These drive it from each state the rubric names — a fresh bin, mid-index (mocked), after
a pass, after a manual trim — and check the control is on `/` with its panel one click
away each time, that the panel names the trimmed shot as kept, and that the re-run's
accepted cut still holds that shot's in/out. Every model call is scripted
(test_firstpass.Scripted); the index runs with test_index's stubbed tools.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

playwright_api = pytest.importorskip("playwright.sync_api",
                                     reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import inference  # noqa: E402
from test_firstpass import Scripted, _end_jobs  # noqa: E402
from test_index import _stub_tools  # noqa: E402

BTN = '[data-testid="firstpass-button"]'
PANEL = '[data-testid="firstpass-panel"]'
RUN = '[data-testid="firstpass-run"]'


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def model():
    m = Scripted()
    inference.set_backend(m)
    inference.reset_spend()
    yield m
    if m.hold is not None:
        m.hold.set()
    inference.set_backend(None)


@pytest.fixture
def live(tmp_path, project, monkeypatch, model):
    """A fresh bin (no cut yet) over the suite's footage and sidecars, served for real;
    the index's tools stubbed, every write under tmp_path."""
    import uvicorn
    import server

    monkeypatch.setenv("ROUGHCUT_BACKEND", "claude_cli")
    server.configure(None, project["footage"], project["sidecars"], tmp_path,
                     proxies=False, visual=None)
    _stub_tools(server, monkeypatch, tmp_path)
    monkeypatch.setattr(server, "FIRSTPASS_POLL_S", 0.05)
    server.FIRSTPASS.clear()
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started
    yield f"http://127.0.0.1:{port}"
    _end_jobs(server, model)              # and any index it left in the background
    srv.should_exit = True
    th.join(timeout=10)
    if model.hold is not None:
        model.hold.set()
    deadline = time.time() + 30                    # no first pass outlives its test
    while any(x["state"] == "running" for x in server.FIRSTPASS.values()) \
            and time.time() < deadline:
        time.sleep(0.1)
    server.FIRSTPASS.clear()


@pytest.fixture
def page(live):
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(live + "/")
        pg.wait_for_function("window.rcFirstPass && rcFirstPass.state()", timeout=20000)
        yield pg
        assert errors == [], errors
        b.close()


def _state(live) -> dict:
    return httpx.get(live + "/api/firstpass", timeout=20).json()


def _wait_state(live, until, timeout=120.0) -> dict:
    deadline = time.time() + timeout
    s = {}
    while time.time() < deadline:
        s = _state(live)
        if s["state"] in until:
            return s
        time.sleep(0.2)
    raise AssertionError(f"first pass did not reach {until}: {s.get('state')}")


def _label(pg, text: str, timeout=30000) -> None:
    pg.wait_for_function(
        "(t) => { const b = document.querySelector('[data-testid=\"firstpass-button\"]');"
        " return b && b.innerText.trim() === t; }", arg=text, timeout=timeout)


def _one_click_to_panel(pg) -> str:
    """The control is visible in the header and its panel is one click away."""
    btn = pg.locator(BTN)
    assert btn.is_visible()
    assert pg.locator(f"header {BTN}").count() == 1
    assert not pg.locator(PANEL).is_visible()
    btn.click()
    pg.wait_for_selector(PANEL, state="visible", timeout=5000)
    pg.wait_for_function("document.querySelector('[data-testid=\"firstpass-panel\"] "
                         ".fp-steps li') !== null", timeout=10000)
    return pg.locator(PANEL).inner_text()


def _panel_says(pg, words: str) -> str:
    """The panel (open, or opened with one click) once it says `words`."""
    if not pg.locator(PANEL).is_visible():
        _one_click_to_panel(pg)
    pg.wait_for_function(
        "(w) => (document.querySelector('[data-testid=\"firstpass-panel\"]') || {})"
        ".innerText.includes(w)", arg=words, timeout=10000)
    return pg.locator(PANEL).inner_text()


def _segs(live) -> list[dict]:
    return httpx.get(live + "/api/project", timeout=20).json()["segments"]


def _accept(pg, live, n: int) -> list[dict]:
    pg.wait_for_selector("#proposal", state="visible", timeout=90000)
    pg.locator("#acceptProposal").click()
    deadline = time.time() + 40
    while time.time() < deadline:
        segs = _segs(live)
        # the Accept is the save AND the answer (POST /api/asks/answer, which records
        # the base when it lands second): an edit made before the answer lands is
        # taken for the accepted cut, so wait for both — the board fires them together
        rec = httpx.get(live + "/api/asks/latest", timeout=20).json().get("record") or {}
        if len(segs) == n and rec.get("answered"):
            _board_settled(pg)
            return _segs(live)
        time.sleep(0.2)
    raise AssertionError("the accepted proposal never reached the cut (saved and answered)")


def _board_settled(pg, quiet: float = 1.2, timeout: float = 15.0) -> None:
    """Until the board has not PUT the cut for `quiet` s. An Accept saves at once and
    its timeline commit queues the debounced autosave (700 ms) of the same cut — a save
    the test's own PUT (a hand trim) must not race, or the trim is overwritten (the
    double save LOOP.md queues for wave 5)."""
    last = [time.time()]

    def seen(r):
        if r.method == "PUT" and r.url.endswith("/api/project"):
            last[0] = time.time()

    pg.on("request", seen)
    try:
        deadline = time.time() + timeout
        while time.time() - last[0] < quiet:
            assert time.time() < deadline, "the board kept saving"
            pg.wait_for_timeout(100)
    finally:
        pg.remove_listener("request", seen)


def test_fresh_bin_offers_run_first_pass_in_the_header_and_both_empty_states(page, live):
    pg = page
    _label(pg, "Run first pass")
    text = _one_click_to_panel(pg)
    for word in ("Index", "Picks", "Rough cut", "free on your plan", "No cut yet"):
        assert word in text, (word, text)
    assert "$" not in text, "no dollar figure in view on the plan (the hover has it)"
    hover = pg.locator(f"{PANEL} .fp-scost").first.get_attribute("title") or ""
    assert "$" in hover and "no charge on your plan" in hover
    assert pg.locator(RUN).inner_text().strip() == "Run first pass"
    # Esc closes it; the Timeline's empty state opens it too (the Program monitor's copy
    # is not drawn: two "Run first pass" on a cold screen, not three — verdict 5, punch 9)
    pg.keyboard.press("Escape")
    assert not pg.locator(PANEL).is_visible()
    tl = pg.locator('#tlEmpty [data-testid="firstpass-empty-timeline"]')
    tl.wait_for(state="visible", timeout=10000)
    assert tl.inner_text().strip() == "Run first pass"
    tl.click()
    pg.wait_for_selector(PANEL, state="visible", timeout=5000)
    pg.keyboard.press("Escape")
    assert not pg.locator('#progEmpty [data-testid="firstpass-empty-program"]').is_visible()
    # a bin without a cut: the first pass is THE first action, so its header button is
    # the screen's one blue button and Next's chip yields to it
    pg.keyboard.press("Escape")
    pg.evaluate("flowBar.poll()")
    pg.wait_for_function("window.flowBar && flowBar.yielded()", timeout=10000)
    pg.wait_for_function(
        "document.querySelector('[data-testid=\"firstpass-button\"]').classList.contains('is-next')",
        timeout=10000)
    assert pg.locator(".is-next").count() == 1


RUNNING = {
    "state": "running", "stage": "index", "job": "fp000001", "mode": "run",
    "label": "Run first pass", "backend": "claude_cli", "charged": False,
    "manual_edits": [], "last": None, "started": 0,
    "steps": {"index": {"state": "running"}, "picks": {"state": "pending"},
              "cut": {"state": "pending"}},
    "counts": {"index": {"clips": 12, "listened": 3, "looked": 0, "look_need": 12,
                         "close": 0, "close_need": 12, "proxied": 2, "pct": 10,
                         "words": "heard 3/12 · looked at 0/12 · previews 2/12"}},
    "would": {"mode": "run", "keep": [], "replace": [], "locked": [],
              "cost": {"words": "free on your plan", "hover": "~$0.53 if it went through "
                       "the API — no charge on your plan", "usd": 0.53},
              "steps": [{"key": "index", "name": "Index", "needed": True,
                         "will": "Index the rest: hear 9 of 12 clips.",
                         "cost": {"words": "free on your plan", "hover": "~$0.40"}},
                        {"key": "picks", "name": "Picks", "needed": True,
                         "will": "Rank what the index found into picks.",
                         "cost": {"words": "free on your plan", "hover": "~$0.00"}},
                        {"key": "cut", "name": "Rough cut", "needed": True,
                         "will": "Propose a first rough cut.",
                         "cost": {"words": "free on your plan", "hover": "~$0.13"}}]},
}


def test_mid_index_the_control_reads_the_counts_and_offers_a_stop(page, live):
    """Mid-index (mocked): the counts the API gives, no % it does not, a × to stop,
    and the panel one click away with the steps under way."""
    pg = page
    stops = []
    pg.route("**/api/firstpass", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps(RUNNING)))
    pg.route("**/api/firstpass/cancel", lambda r: (stops.append(1), r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"job": "fp000001", "stage": "index", "cancelling": True}))))
    pg.evaluate("rcFirstPass.refresh()")
    # short enough for the header at 1440 beside Next and a job pill (verdict 3): the
    # count of clips heard and looked at; the full counts in the hover and the panel
    _label(pg, "First pass · 0/12")
    assert "%" not in pg.locator(BTN).inner_text()
    assert "heard 3/12" in (pg.locator(BTN).get_attribute("title") or "")
    stop = pg.locator('[data-testid="firstpass-cancel"]')
    assert stop.is_visible()
    text = _one_click_to_panel(pg)
    assert "heard 3/12" in text and "Stop" in text
    assert pg.locator(f"{PANEL} li[data-step=index].is-running").count() == 1
    stop.click()
    deadline = time.time() + 5
    while not stops and time.time() < deadline:
        pg.wait_for_timeout(100)
    assert stops, "the × asks the server to stop the pass"


def _job(id_, kind, label, pct, eta_s, **kw) -> dict:
    return {"id": id_, "kind": kind, "state": "running", "label": label, "pct": pct,
            "eta_s": eta_s, "elapsed_s": 30.0, "started": 1.0, "milestone": "", "detail": "",
            "milestones": [], **kw}


# Verdict 10's header mid-pass: the pass's index (54 %, its ×), the call writing its cut
# (20 %, ~2 min) and, from another family, an export
PASS_INDEX = _job("ix000001", "index", "Indexing the footage", 54.0, 300.0)
PASS_ASK = _job("as000001", "ask", "First pass — proposing the cut", 20.0, 120.0,
                firstpass="fp000001", ask_kind="first pass")
EXPORT = _job("rn000001", "render", "Exporting — Quick 1080p", 40.0, 60.0)

READOUTS_JS = """() => {
  const vis = (el) => !!el && el.getClientRects().length > 0;
  const fp = document.querySelector('#firstpass');
  const left = fp.querySelector('[data-testid=firstpass-left]');
  const head = document.querySelector('header');
  const texts = [];
  const walk = document.createTreeWalker(head, NodeFilter.SHOW_TEXT);
  while (walk.nextNode()) {
    const n = walk.currentNode;
    if (n.textContent.trim() && vis(n.parentElement)) texts.push(n.textContent.trim());
  }
  return {pills: [...document.querySelectorAll('#progress .job')].filter(vis).map((el) => el.dataset.job),
          left: vis(left) ? left.textContent : '', label: fp.querySelector('.fp-label').textContent,
          title: fp.querySelector('.fp-btn').title, texts};
}"""


def test_while_a_pass_runs_the_header_has_one_readout_per_job_family(page, live):
    """Verdict 10, punch 9: mid-pass the header read "First pass · the cut ×", "54% ×" and
    "20% · ~2 min left" at once, three readouts for one pass. The pass's own jobs (its
    index, the call writing its cut) are read on its control (time left beside it, the %
    in its hover); another family's job (an export) keeps its pill; a pass job that
    failed keeps its pill; once the pass is over the index's pill is back."""
    pg = page
    now = {"fp": {**RUNNING, "stage": "cut",
                  "steps": {"index": {"state": "done"}, "picks": {"state": "done"},
                            "cut": {"state": "running"}},
                  "counts": {**RUNNING["counts"], "cut": {"detail": "choosing the shots",
                                                          "state": "running"}}},
           "jobs": [PASS_INDEX, PASS_ASK, EXPORT]}

    def fp(route):
        if route.request.method != "GET":
            return route.continue_()
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(now["fp"]))
    pg.route("**/api/firstpass", fp)
    pg.route("**/api/jobs", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps({"jobs": now["jobs"]})))
    pg.evaluate("rcFirstPass.refresh()")
    _label(pg, "First pass · the cut")
    left = "(t) => { const n = document.querySelector('[data-testid=firstpass-left]');" \
           " return n && !n.hidden && n.textContent === t; }"
    pg.wait_for_function(left, arg="~2 min left", timeout=10000)
    r = pg.evaluate(READOUTS_JS)
    assert r["pills"] == ["rn000001"], r            # the export's own; none for the pass's jobs
    assert "First pass — proposing the cut: 20% · ~2 min left" in r["title"], r["title"]
    pct = [t for t in r["texts"] if "%" in t]
    assert pct == ["40%"] or pct == ["40% · ~1 min left"], r["texts"]   # the export's, alone
    assert sum("left" in t for t in r["texts"]) == 2, r["texts"]        # the pass's, the export's

    # the index stage: the index's time left on the control
    now["fp"] = RUNNING
    pg.evaluate("rcFirstPass.refresh()")
    _label(pg, "First pass · 0/12")
    pg.wait_for_function(left, arg="~5 min left", timeout=10000)
    assert pg.evaluate(READOUTS_JS)["pills"] == ["rn000001"]

    # a pass job that failed says so on its own pill
    now["jobs"] = [{**PASS_INDEX, "state": "failed", "detail": "failed: ffmpeg exited 1"}, EXPORT]
    pg.wait_for_function("!!document.querySelector('#progress .job[data-job=ix000001].failed')",
                         timeout=10000)

    # the pass over (the server's own state: never run): the index going on has its pill
    now["jobs"] = [PASS_INDEX, EXPORT]
    pg.unroute("**/api/firstpass")
    pg.evaluate("rcFirstPass.refresh()")
    _label(pg, "Run first pass")
    pg.wait_for_function("(() => { const r = document.querySelector('#progress .job[data-job=ix000001]');"
                         " return r && r.getClientRects().length && !r.classList.contains('failed'); })()",
                         timeout=10000)
    r = pg.evaluate(READOUTS_JS)
    assert sorted(r["pills"]) == ["ix000001", "rn000001"] and r["left"] == "", r
    pg.unroute("**/api/jobs")


def test_stop_while_the_cut_is_written_discards_it(page, live, model):
    """A real run held at the model call: × stops it, the proposal it was writing is
    discarded (never shown), and the control remembers the run: Re-run first pass,
    the panel saying it stopped and what a re-run does."""
    pg = page
    model.hold = threading.Event()
    _one_click_to_panel(pg)
    pg.locator(RUN).click()
    pg.wait_for_function("(rcFirstPass.state() || {}).stage === 'cut'", timeout=60000)
    _label(pg, "First pass · the cut")
    with pg.expect_response("**/api/firstpass/cancel") as resp:
        pg.locator('[data-testid="firstpass-cancel"]').click()
    assert resp.value.ok
    s = _state(live)
    assert s["state"] == "stopped" and s["step"] == "cut", s
    model.hold.set()
    _label(pg, "Re-run first pass", timeout=20000)
    text = _panel_says(pg, "Stopped")
    assert "writes the cut again" in text, text
    latest = httpx.get(live + "/api/asks/latest", timeout=20).json()
    assert not latest.get("pending"), latest
    assert not pg.locator("#proposal").is_visible()
    assert _segs(live) == []


def test_run_accept_trim_rerun_keeps_the_trim(page, live):
    """The journey of K1: run from the header, accept, trim a shot by hand, re-run from
    the header — the panel names the trimmed shot as kept, and the accepted re-run
    still holds its in/out exactly."""
    pg = page
    # ---- after a fresh run: the proposal opens in the Assistant; accept it
    _one_click_to_panel(pg)
    pg.locator(RUN).click()
    segs = _accept(pg, live, 2)
    assert _wait_state(live, ("done",))["state"] == "done"
    pg.evaluate("rcFirstPass.refresh()")
    _label(pg, "Re-run first pass")
    text = _one_click_to_panel(pg)
    assert "Keeps 0 shots you edited by hand · may replace 2" in text, text
    pg.keyboard.press("Escape")

    # ---- a manual trim (the board saves the whole cut; this is that save)
    trimmed = dict(segs[0], out=round(segs[0]["out"] - 0.37, 2))
    r = httpx.put(live + "/api/project", json={"segments": [trimmed, segs[1]],
                                               "story": ""}, timeout=20)
    assert r.status_code == 200, r.text
    assert _state(live)["state"] == "stale-after-edits"
    pg.reload()
    pg.wait_for_function("window.rcFirstPass && rcFirstPass.state()", timeout=20000)
    _label(pg, "Re-run first pass")
    assert pg.locator("#firstpass .fp-dot").is_visible(), "stale: the cut changed since"
    text = _one_click_to_panel(pg)
    assert "Keeps 1 shot you edited by hand · may replace 1" in text, text
    kept = pg.locator('[data-testid="firstpass-kept"] li')
    assert kept.count() == 1
    line = kept.first.inner_text()
    stem = trimmed["clip"].rsplit(".", 1)[0]
    assert stem in line and "trimmed" in line, line
    assert kept.first.get_attribute("data-shot") == trimmed["id"]

    # ---- re-run from the panel; accept; the trimmed shot's in/out hold exactly
    pg.locator(RUN).click()
    after = _accept(pg, live, 3)
    same = [s for s in after if s.get("id") == trimmed["id"]]
    assert len(same) == 1, after
    assert (same[0]["clip"], same[0]["in"], same[0]["out"]) == (
        trimmed["clip"], trimmed["in"], trimmed["out"])
    s = _wait_state(live, ("done", "stale-after-edits"))
    assert s["state"] == "done", s
    pg.evaluate("rcFirstPass.refresh()")
    _label(pg, "Re-run first pass")
    _one_click_to_panel(pg)


def _kids(marker: str) -> list[str]:
    """Live processes whose command line carries `marker` (`pgrep -f`)."""
    import subprocess
    r = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)
    return r.stdout.split()


def test_stop_during_a_long_step_lands_within_5s_and_kills_its_children(page, live,
                                                                         monkeypatch):
    """Verdict 3: Stop took 5 min 25 s, because the index waited for the clip-stage in
    hand. The look here is a 60 s tool with a child of its own (an ffmpeg would be
    one): Stop from the header ends the run within 5 s, no child is left, and the
    control says it stopped, what finished, and what a re-run picks up."""
    import server

    marker = "59.8765"                        # unique on a shared box
    monkeypatch.setattr(server, "visual_cmd", lambda only, force: [
        "bash", "-c", f"sleep {marker} & sleep {marker}; wait"])
    pg = page
    _one_click_to_panel(pg)
    pg.locator(RUN).click()
    deadline = time.time() + 30
    while time.time() < deadline and len(_kids(f"sleep {marker}")) < 2:
        time.sleep(0.1)
    assert len(_kids(f"sleep {marker}")) >= 2, "the 60 s look is running"
    _label(pg, "First pass · 0/3")
    t0 = time.time()
    pg.locator('[data-testid="firstpass-cancel"]').click()
    while time.time() - t0 < 5 and _state(live)["state"] == "running":
        time.sleep(0.1)
    s = _state(live)
    assert s["state"] != "running", f"still running {time.time() - t0:.1f} s after Stop"
    assert time.time() - t0 < 5
    while time.time() - t0 < 5 and _kids(f"sleep {marker}"):
        time.sleep(0.1)
    assert _kids(f"sleep {marker}") == [], "a child of the stopped step outlived the Stop"
    assert s["state"] == "stopped" and s["label"] == "Re-run first pass"
    _label(pg, "Re-run first pass", timeout=10000)
    text = _panel_says(pg, "Stopped")
    assert "Stopped just now · heard 3/3 · looked 0/3" in text, text
    assert "Re-run first pass looks at 3 more, then picks, then the cut" in text, text
    assert pg.locator(RUN).inner_text().strip() == "Re-run first pass"
