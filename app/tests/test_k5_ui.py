"""K5 in the browser (wave 20 K5UI): the first pass panel says, per step, what it does, is
doing and did, with Killington's numbers and granularity; says how sure the cut's moments
are (counts by certainty, a chip on each shot); and the Index panel carries the per-bin
Understanding control and the 3080's column.

Every endpoint the lanes VERIFY / LOCALGPU / TELEM are still building is mocked here, in the
field names their docstrings give; a missing field must hide its part, never crash the page.
The live fixture (test_firstpass_ui) is the real server over the suite's three clips."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import flow  # noqa: E402
from test_firstpass_ui import RUNNING, BTN, PANEL, live, model, _one_click_to_panel  # noqa: E402,F401
from k5_fixture import BIN_COUNTS, CUT_COUNTS, HYBRID_COUNTS, KILLINGTON, RUNS  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

SHOTS = Path(os.environ.get("K5_SHOTS", str(Path.home() / "roughcut-loop/lane-w20-k5ui/shots")))
SIZES = [(1440, 900), (1920, 1080)]


def _json(body, status=200):
    return {"status": status, "content_type": "application/json", "body": json.dumps(body)}


def _account(run: str, **extra) -> dict:
    acc = flow.pass_account(KILLINGTON, RUNS[run])
    acc["certainty"] = extra.pop("certainty", None)
    acc.update(extra)
    return acc


def _certainty(bin_counts=BIN_COUNTS, cut=CUT_COUNTS, source="proposal") -> dict:
    return {"bin": bin_counts, "bin_words": flow.certainty_words(bin_counts, "moments"),
            "cut": cut, "cut_source": source, "cut_shots": 19,
            "cut_words": flow.certainty_words(cut, "of the 19 shots rest on a moment")}


DONE = {**RUNNING, "state": "done", "when": time.time() - 90, "stage": None, "steps": None,
        "counts": None, "stale": [], "new_clips": [], "new_count": 0,
        "proposal": {"job": "ask00001", "pending": False, "answer": "accept", "shots": 19},
        "produced": {"index_ran": False, "picks": 40, "cut": {"shots": 19, "length_s": 60}},
        "label": "Re-run first pass"}
NEVER = {**RUNNING, "state": "never-run", "stage": None, "steps": None, "counts": None,
         "label": "Run first pass"}


@pytest.fixture(params=SIZES, ids=lambda s: f"{s[0]}")
def pg(live, request):
    w, h = request.param
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        p = b.new_page(viewport={"width": w, "height": h})
        errors: list[str] = []
        p.on("pageerror", lambda e: errors.append(str(e)))
        p.size = (w, h)
        p.goto(live + "/")
        p.wait_for_function("window.rcFirstPass && rcFirstPass.state() && window.rcIndex", timeout=30000)
        yield p
        assert errors == [], errors
        b.close()


def _serve(pg, state, account):
    pg.route("**/api/firstpass", lambda r: r.fulfill(**_json(state)))
    pg.route("**/api/firstpass/account*", lambda r: r.fulfill(**_json(account)))
    pg.evaluate("rcFirstPass.refresh()")
    pg.wait_for_function("rcFirstPass.state() && rcFirstPass.state().state === arguments[0]"
                         if False else f"rcFirstPass.state().state === {json.dumps(state['state'])}")
    pg.evaluate("rcFirstPass.refreshAccount(true)")
    pg.wait_for_function("!!rcFirstPass.account()", timeout=10000)


def _panel(pg):
    if not pg.locator(PANEL).is_visible():
        pg.locator(BTN).click()
        pg.wait_for_selector(PANEL, state="visible", timeout=5000)
    pg.wait_for_selector(f"{PANEL} [data-testid=firstpass-step]", timeout=10000)
    return pg.locator(PANEL)


def _shot(pg, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    pg.locator(PANEL).screenshot(path=str(SHOTS / f"{name}-{pg.size[0]}.png"))


def _inside(pg, sel):
    """The element sits whole inside the viewport (no clipped panel at 1440 or 1920)."""
    box = pg.locator(sel).bounding_box()
    w, h = pg.size
    assert box and box["x"] >= 0 and box["x"] + box["width"] <= w, box
    assert box["y"] >= 0 and box["y"] + box["height"] <= h + 1, box


def _steps(pg) -> dict:
    return {li.get_attribute("data-step"): li.inner_text()
            for li in pg.locator(f"{PANEL} [data-testid=firstpass-step]").all()}


# ------------------------------------------------------------ never run

def test_never_run_each_step_says_what_it_will_do_and_how_finely(pg):
    _serve(pg, NEVER, _account("never-run"))
    _panel(pg)
    steps = _steps(pg)
    assert "Will hear every clip with speech to text (every word timed)" in steps["index"]
    assert "1 frame every 4 s on contact sheets" in steps["index"]
    assert "Already: heard 12 clips" in steps["index"]
    assert "Will rank 188 moments into picks" in steps["picks"]
    assert "Will write a proposed cut" in steps["cut"]
    assert pg.locator(BTN).inner_text().strip() == "Run first pass"
    assert pg.locator('[data-testid=firstpass-headline]').count() == 0
    _inside(pg, PANEL)
    _shot(pg, "1-never-run")


# ------------------------------------------------------------ running

def test_running_label_stays_short_and_the_hover_and_panel_say_what_it_is_doing(pg):
    state = {**RUNNING, "stage": "index"}
    _serve(pg, state, _account("running-index"))
    label = pg.locator(BTN).inner_text().strip()
    assert label == "First pass · 0/12" and len(label) < 24       # the header budget (verdict 3)
    hover = pg.locator(BTN).get_attribute("title")
    assert "Index: hearing and looking at every clip" in hover
    assert "heard 3/12 · looked at 1/12" in hover and "1 frame every 4 s" in hover
    _panel(pg)
    idx = pg.locator(f"{PANEL} li[data-step=index]")
    assert "is-running" in idx.get_attribute("class")
    assert "Hearing and looking at every clip" in idx.inner_text()
    # the later stage, then the cut
    _serve(pg, {**RUNNING, "stage": "picks"}, _account("running-picks"))
    assert "Ranking 188 moments into picks" in _steps(pg)["picks"]
    assert "Picks: ranking 188 moments" in pg.locator(BTN).get_attribute("title")
    _serve(pg, {**RUNNING, "stage": "cut"}, _account("running-cut"))
    assert "Writing the cut — choosing among 40 picks" in _steps(pg)["cut"]
    assert pg.locator(BTN).inner_text().strip() == "First pass · the cut"
    _shot(pg, "2-running-cut")


# ------------------------------------------------------------ done

def test_done_on_an_existing_index_says_it_only_re_ranked_and_re_cut(pg):
    _serve(pg, DONE, _account("done-existing", certainty=_certainty()))
    _panel(pg)
    head = pg.locator("[data-testid=firstpass-headline]").inner_text()
    assert "only re-ranked the existing index" in head and "nothing was re-watched" in head
    assert "Index panel" in head
    steps = _steps(pg)
    for want in ("Already done — heard 12 clips", "1,666 words", "looked at 1 frame every 4 s",
                 "651 frames", "close looks on 7 of 43 min", "Nothing was re-watched"):
        assert want in steps["index"], (want, steps["index"])
    assert "Ranked 188 moments into 40 picks" in steps["picks"]
    assert "Wrote a 19-shot cut" in steps["cut"]
    assert "nothing was re-watched" in pg.locator(BTN).get_attribute("title")
    # how sure: counts by certainty, the cut's and the bin's
    cert = pg.locator("[data-testid=firstpass-certainty]")
    assert cert.is_visible()
    cut = cert.locator(".fp-certrow[data-kind=cut]")
    assert "The proposed cut: 19 shots" in cut.inner_text()
    got = {c.get_attribute("data-level"): int(c.get_attribute("data-count"))
           for c in cut.locator(".cert").all()}
    assert got == {k: v for k, v in CUT_COUNTS.items() if k != "total"}
    binrow = cert.locator(".fp-certrow[data-kind=bin]")
    assert binrow.locator('.cert[data-level=unchecked]').inner_text().strip().startswith("89 unchecked")
    assert binrow.locator('.cert[data-level=contradicted]').count() == 1
    assert "checked by Claude" in binrow.locator('.cert[data-level=claude]').inner_text()
    assert binrow.locator('.cert[data-level=felt]').count() == 0      # none: not listed
    _inside(pg, PANEL)
    _shot(pg, "3-done-existing-index")


def test_hybrid_counts_list_every_level_in_order(pg):
    _serve(pg, DONE, _account("done-existing", certainty=_certainty(HYBRID_COUNTS)))
    _panel(pg)
    levels = [c.get_attribute("data-level") for c in
              pg.locator(".fp-certrow[data-kind=bin] .cert").all()]
    assert levels == ["felt", "local", "claude", "once", "unchecked", "unsupported", "contradicted"]
    assert pg.locator('.fp-certrow[data-kind=bin] .cert[data-level=local]').get_attribute("title") \
        .startswith("9 checked by the 3080")
    _shot(pg, "4-hybrid-counts")


def test_done_fresh_says_it_heard_and_looked_first(pg):
    state = {**DONE, "produced": {"index_ran": True, "picks": 40, "cut": {"shots": 19}}}
    _serve(pg, state, _account("done-fresh", certainty=_certainty()))
    _panel(pg)
    assert "heard and looked at the footage first" in \
        pg.locator("[data-testid=firstpass-headline]").inner_text()
    assert _steps(pg)["index"].startswith("Index\nRan — heard 12 clips")
    _shot(pg, "5-done-fresh")


def test_a_missing_account_falls_back_to_the_plans_words_and_never_crashes(pg):
    # no account at all (older server): the plan's own line, no headline, no counts
    served = {"status": 404, "body": {}}
    pg.route("**/api/firstpass", lambda r: r.fulfill(**_json({**RUNNING, "state": "never-run",
                                                              "stage": None, "counts": None})))
    pg.route("**/api/firstpass/account*", lambda r: r.fulfill(**_json(served["body"], served["status"])))
    pg.evaluate("rcFirstPass.refresh()")
    pg.evaluate("rcFirstPass.refreshAccount(true)")
    pg.wait_for_function("rcFirstPass.account() === null", timeout=10000)
    _panel(pg)
    assert "Index the rest: hear 9 of 12 clips." in _steps(pg)["index"]
    assert pg.locator("[data-testid=firstpass-headline]").count() == 0
    assert pg.locator("[data-testid=firstpass-certainty]").count() == 0
    # an account with every number missing, and one with certainty fields missing
    for acc in ({}, {"steps": [{"key": "index"}], "certainty": {"bin": {}, "cut": None}},
                {"steps": [], "certainty": {"cut": {"total": 2, "claude": 2}}}):
        served.update(status=200, body=acc)
        pg.evaluate("rcFirstPass.refreshAccount(true)")
        pg.wait_for_function("rcFirstPass.account() !== null", timeout=10000)
        assert pg.locator(PANEL).is_visible()
        assert "index" in _steps(pg)


# ------------------------------------------------------------ the chips

PLAN = {"segments": [{"clip": "CLIP_A.MP4", "in": 0.0, "out": 2.0, "why": "a jump off the lip"},
                     {"clip": "CLIP_B.MP4", "in": 1.0, "out": 3.0, "why": "he says hello"},
                     {"clip": "CLIP_A.MP4", "in": 3.0, "out": 4.0, "why": "after"}],
        "notes": "x"}


def _chips(level_title):
    shots = []
    for lv, t in level_title:
        shots.append(None if lv is None else {"level": lv, "chip": flow.CERT_CHIP[lv],
                                              "name": flow.CERT_NAMES[lv], "title": t})
    return {"shots": shots}


def test_each_shot_in_a_proposal_shows_its_certainty_chip_with_who_settled_it(pg):
    asked = []

    def certainty(route):
        asked.append(json.loads(route.request.post_data))
        route.fulfill(**_json(_chips([
            ("claude", "air 0.39 s · telemetry · confirmed by Claude"),
            (None, ""),
            ("unchecked", "a jump · the contact sheets · not checked yet")])))
    pg.route("**/api/certainty", certainty)
    pg.evaluate("(p) => rcShowProposal(p)", PLAN)
    pg.wait_for_selector("#proposalDiff [data-testid=certainty-chip]", timeout=10000)
    pg.wait_for_function("document.querySelectorAll('#proposalDiff [data-testid=certainty-chip]').length === 2")
    assert asked[0]["shots"][0] == {"clip": "CLIP_A.MP4", "in": 0.0, "out": 2.0}
    rows = pg.locator("#proposalDiff tr.pshot")
    first = rows.nth(0).locator("[data-testid=certainty-chip]")
    assert first.inner_text() == "Claude" and first.get_attribute("data-level") == "claude"
    assert "air 0.39 s · telemetry · confirmed by Claude" in first.get_attribute("title")
    assert rows.nth(1).locator("[data-testid=certainty-chip]").count() == 0     # a spoken shot
    assert rows.nth(2).locator("[data-testid=certainty-chip]").inner_text() == "unchecked"
    SHOTS.mkdir(parents=True, exist_ok=True)
    pg.locator("#proposalDiff").screenshot(path=str(SHOTS / f"6-proposal-chips-{pg.size[0]}.png"))


def test_a_failing_certainty_endpoint_leaves_the_proposal_whole(pg):
    served = {"status": 500, "body": "boom"}
    pg.route("**/api/certainty", lambda r: r.fulfill(status=served["status"],
                                                      content_type="application/json",
                                                      body=json.dumps(served["body"])))
    pg.evaluate("(p) => rcShowProposal(p)", PLAN)
    pg.wait_for_selector("#proposalDiff tr.pshot", timeout=10000)
    pg.wait_for_timeout(500)
    assert pg.locator("#proposalDiff tr.pshot").count() == 3
    assert pg.locator("[data-testid=certainty-chip]").count() == 0
    # a server that answers with an unknown shape
    served.update(status=200, body={"shots": [{"nope": 1}, 7, None]})
    pg.evaluate("(p) => rcShowProposal({...p, notes: 'y'})", PLAN)
    pg.wait_for_timeout(500)
    assert pg.locator("[data-testid=certainty-chip]").count() == 0


# ------------------------------------------------------------ the Index panel

MODE = {"mode": "hybrid"}


def _open_index(pg):
    pg.evaluate("rcIndex.open()")
    pg.wait_for_selector('[data-testid=index-panel]:not([hidden])', timeout=10000)
    pg.wait_for_selector('[data-testid=index-row]', timeout=15000)


def _mode_routes(pg, *, get=MODE, put_status=200, seen=None):
    seen = seen if seen is not None else []

    def handler(route):
        req = route.request
        if req.method == "PUT":
            body = json.loads(req.post_data)
            seen.append(body)
            if put_status != 200:
                return route.fulfill(status=put_status, content_type="application/json",
                                     body=json.dumps({"detail": "the bin is busy"}))
            return route.fulfill(**_json({"mode": body["mode"]}))
        if get is None:
            return route.fulfill(status=404, content_type="application/json", body='{"detail":"x"}')
        return route.fulfill(**_json(get))
    pg.route("**/api/index/mode", handler)
    return seen


def test_the_mode_control_round_trips_and_says_what_each_costs(pg):
    seen = _mode_routes(pg)
    _open_index(pg)
    sel = pg.locator("[data-testid=index-mode]")
    sel.wait_for(state="visible", timeout=10000)
    assert sel.input_value() == "hybrid"
    assert [o.inner_text() for o in sel.locator("option").all()] == [
        "Hybrid (3080 + Claude)", "Local only (3080, free)", "Claude only"]
    line = pg.locator("[data-testid=index-modeline]")
    assert "Claude double-checks only the moments the cut uses" in line.inner_text()
    sel.select_option("local")
    pg.wait_for_function("document.querySelector('[data-testid=index-modeline]').innerText.includes('Costs nothing')")
    assert seen == [{"mode": "local"}] and sel.input_value() == "local"
    assert rcIndex_state(pg)["mode"] == "local"
    sel.select_option("claude")
    pg.wait_for_function("document.querySelector('[data-testid=index-modeline]').innerText.includes('the 3080 is not used')")
    assert seen[-1] == {"mode": "claude"}
    assert "Hybrid (3080 + Claude)" in line.get_attribute("title")        # all three in the hover
    _inside(pg, "[data-testid=index-modebar]")
    SHOTS.mkdir(parents=True, exist_ok=True)
    pg.locator('[data-testid=index-panel]').screenshot(path=str(SHOTS / f"7-index-mode-{pg.size[0]}.png"))


def rcIndex_state(pg):
    return pg.evaluate("rcIndex.state()")


def test_a_refused_change_reverts_and_says_why(pg):
    _mode_routes(pg, put_status=409)
    _open_index(pg)
    sel = pg.locator("[data-testid=index-mode]")
    sel.wait_for(state="visible", timeout=10000)
    sel.select_option("local")
    pg.wait_for_function("document.querySelector('[data-testid=index-modeline]').innerText.includes('could not change it: the bin is busy')")
    assert sel.input_value() == "hybrid"


def test_no_mode_endpoint_hides_the_control(pg):
    _mode_routes(pg, get=None)
    _open_index(pg)
    pg.wait_for_timeout(400)
    assert pg.locator("[data-testid=index-modebar]").is_hidden()
    assert pg.locator("[data-testid=index-mode]").count() == 1
    assert rcIndex_state(pg)["mode"] is None


LOCAL = {"local": None, "stage": None}


def _clips_with_local(pg, local, stage=None):
    LOCAL.update(local=local, stage=stage)

    def handler(route):
        resp = route.fetch()
        d = resp.json()
        if LOCAL["local"] is not None:
            d["local"] = LOCAL["local"]
        if LOCAL["stage"] is not None:
            for r in d["rows"]:
                r["stages"]["local"] = LOCAL["stage"]
        route.fulfill(response=resp, body=json.dumps(d))
    return handler


def test_the_3080_column_shows_its_stage_and_hides_when_the_report_has_none(pg):
    _mode_routes(pg)
    pg.route("**/api/index/clips", _clips_with_local(pg, None))
    _open_index(pg)
    assert pg.locator('th[data-stage=local]').count() == 0            # no field: no column
    _clips_with_local(pg, {"available": True, "model": "Qwen3.5-4B"},
                      {"state": "done", "at": time.time() - 60, "checked": 14})
    pg.evaluate("rcIndex.refresh()")
    pg.wait_for_selector('th[data-stage=local]', timeout=10000)
    assert pg.locator('th[data-stage=local]').inner_text() == "Local (3080)"
    cells = pg.locator('td[data-stage=local]')
    assert cells.count() >= 3
    assert cells.first.get_attribute("data-state") == "done"
    assert "free, on this machine" in cells.first.get_attribute("title")
    assert "14 windows checked" in cells.first.get_attribute("title")
    pg.locator('[data-testid=index-panel]').screenshot(path=str(SHOTS / f"8-index-local-{pg.size[0]}.png"))


def test_no_gpu_says_not_available_with_the_reason(pg):
    _mode_routes(pg)
    pg.route("**/api/index/clips", _clips_with_local(pg, {"available": False,
                                                           "reason": "no NVIDIA GPU found"}))
    _open_index(pg)
    cell = pg.locator('td[data-stage=local]').first
    cell.wait_for(timeout=10000)
    assert cell.get_attribute("data-state") == "unavailable"
    assert "not available — no NVIDIA GPU found" in cell.get_attribute("title")
    assert pg.locator("[data-testid=index-mode] option[value=local]").is_disabled()
    assert "The 3080 is not available: no NVIDIA GPU found." in \
        pg.locator("[data-testid=index-modeline]").inner_text()
    assert "not available" in pg.locator("[data-testid=index-mode] option[value=local]").inner_text()
    pg.locator('[data-testid=index-panel]').screenshot(path=str(SHOTS / f"9-index-nogpu-{pg.size[0]}.png"))
