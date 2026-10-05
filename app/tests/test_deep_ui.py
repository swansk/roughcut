"""How the agent sees, and Look deeper (INTAKE M15), driven in a real browser.

The coverage strip is shared by three screens through `/deep.js`: the board's inspector
for the selected shot (the shot's range marked), the pass under its tape (the playhead
live) and the open screen's cards (a mini strip). The synthetic bin carries a coarse
sidecar, a close look and one deep look on CLIP_A written here, so the strip has every
lane to draw without a model. A run is driven with a scripted backend in-process (the
server runs in a thread of this process): the button is priced first, the click starts a
`deep` job, and the result renders seen beats solid and inferred ones hatched.

Skipped when playwright is absent, like the other browser tests.
"""

from __future__ import annotations

import json
import re
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api",
                                     reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import config, deep, inference  # noqa: E402

SEED = {
    "variant": "T", "title": "test cut", "orient": "none", "story": "",
    "target_s": [5, 20],
    "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
                 {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}],
}

# One deep look already on disk, on seconds the first shot does not cover.
STORED = {"start": 3.5, "end": 5.5, "frames": [
    {"t": 3.5, "why": "floor"}, {"t": 4.2, "why": "peak"}, {"t": 4.8, "why": "asked"},
    {"t": 5.45, "why": "floor"}],
    "asked": [4.8], "followup": True, "asked_but_refused": [],
    "beats": [{"start": 3.5, "end": 3.6, "basis": "seen", "frame": 3.5,
               "what": "the test card, still"},
              {"start": 3.6, "end": 4.1, "basis": "inferred", "between": [3.5, 4.2],
               "what": "the pattern scrolls", "why": "the two frames differ by a shift"},
              {"start": 4.2, "end": 4.3, "basis": "seen", "frame": 4.2,
               "what": "mid-scroll"}],
    "events": [{"kind": "action", "start": 3.6, "end": 4.8, "confidence": "medium",
                "notable": False, "what": "the pattern moves", "frames": [4.2]}],
    "camera": {"mount": "static", "roll": "level", "evidence": "3.5"},
    "unsure": ["whether it loops"], "summary": "a test pattern scrolling",
    "model": config.DEEP_MODEL, "role": "judge", "calls": 2, "projected_usd": 0.9}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _sidecars(visual: Path) -> None:
    visual.mkdir(parents=True, exist_ok=True)
    (visual / "CLIP_A.visual.json").write_text(json.dumps({
        "clip": "CLIP_A.MP4", "moments": [
            {"start": 2.0, "end": 3.0, "kind": "jump", "notable": True,
             "confidence": "high", "what": "rider mid-air", "frames": [2.0]}],
        "unusable": [], "summary": "", "sheets_read": 1, "sheets_total": 1,
        "params": {"interval_s": 2.0, "cols": 6, "rows": 5, "role": "analysis"}}),
        encoding="utf-8")
    (visual / "CLIP_A.fine.json").write_text(json.dumps({
        "clip": "CLIP_A.mp4", "mode": "fine", "windows_read": [[3.0, 6.0]],
        "frames_sampled": [3.0, 4.0, 5.0, 6.0], "moments": [], "unusable": [],
        "params": {"interval_s": 1.0, "cols": 3, "rows": 5, "width": 480,
                   "role": "judge", "model": config.DEEP_MODEL}}), encoding="utf-8")


@pytest.fixture(scope="module")
def live_server(project, tmp_path_factory):
    import uvicorn
    import server

    original = project["edl"].read_text(encoding="utf-8")
    work = tmp_path_factory.mktemp("deepwork")
    server.configure(project["edl"], project["footage"], project["sidecars"], work,
                     proxies=False, visual=work / "visual", assets=project["assets"])
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
    project["edl"].write_text(original, encoding="utf-8")


@pytest.fixture
def bin_state(live_server, project):
    """Every test starts from the same files: the seed cut, the sidecars, one deep look."""
    import server

    Path(project["edl"]).write_text(json.dumps(SEED, indent=1), encoding="utf-8")
    visual = server.STATE["visual"]
    _sidecars(visual)
    (visual / "CLIP_A.deep.json").unlink(missing_ok=True)
    deep.extract_frames(server.STATE["proxy_dir"] / "CLIP_A.mp4",
                        [f["t"] for f in STORED["frames"]], server.deep_frames_dir("CLIP_A"))
    deep.store(visual, "CLIP_A", "CLIP_A.MP4",
               {**STORED, "frames": [{**f, "file": deep.frame_name(f["t"])}
                                     for f in STORED["frames"]]})
    server.rebuild_events()
    server.DEEPS.clear()
    return live_server


@pytest.fixture
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


def _board(browser, url):
    pg = browser.new_page(viewport={"width": 1400, "height": 1000})
    pg.goto(url)
    pg.wait_for_selector("#tl .blk")
    return pg


def test_the_inspector_shows_how_the_shot_s_clip_was_seen(bin_state, browser):
    """INTAKE M16 I16.4 / decision 9: on the shot strip, Look deeper is a visible priced
    button for the shot's own range, and how the machine saw the clip is one closed line
    that opens the full strip in place — the strip is not drawn until asked for."""
    pg = _board(browser, bin_state)
    pg.locator("#tl .blk").nth(0).click()
    look = "#inspector .srow.top .lookhost .dv-look"
    pg.wait_for_function(f"""() => /^Look deeper · ~\\$\\d/.test(
        (document.querySelector('{look}') || {{}}).textContent || '')""")
    assert "1.0–3.0 s" in pg.get_attribute(look, "title")      # the shot, not ± 2 s
    box = "#inspector .deepline"
    assert pg.locator(f"{box} .dv-bar").count() == 0, "the six-lane strip is not on the main view"
    pg.locator(f"{box} > :first-child").click()
    pg.wait_for_selector(f"{box} .dv-bar")
    lanes = pg.evaluate(f"""() => [...document.querySelectorAll('{box} .dv-lane')]
        .map((l) => [l.dataset.lane, l.querySelectorAll('i').length])""")
    got = dict(lanes)
    assert got["heard"] == 3                       # three utterances
    assert got["coarse"] == 3                      # 0, 2, 4 at every 2 s
    assert got["close"] >= 2 and got["deep"] == 1 + 4   # one span, four keyframes
    assert got["moments"] >= 2                     # the jump claim and the deep event
    # the granularity, in words, from the sidecars' own records
    legend = pg.inner_text(f"{box} .dv-legend")
    assert "a frame every 2 s" in legend and "model not recorded" in legend
    assert "every 1 s in 1 window of 3 s, 480 px by " in legend
    assert legend.count("model not recorded") == 2        # the coarse sheet and the ASR
    assert "keyframes at motion changes" in legend
    # the shot's range is marked on the strip; its priced Look deeper is the top row's
    # alone (decision 9) — the strip offers one only for seconds dragged across it
    assert pg.is_visible(f"{box} .dv-markband")
    assert pg.locator(f"{box} .dv-act .dv-look").count() == 0
    assert pg.locator("#inspector .dv-look:visible").count() == 1
    assert "drag across the strip" in pg.inner_text(f"{box} .dv-act")
    bar = pg.locator(f"{box} .dv-bar").bounding_box()
    y = bar["y"] + bar["height"] / 2
    pg.mouse.move(bar["x"] + bar["width"] * 0.1, y)
    pg.mouse.down()
    pg.mouse.move(bar["x"] + bar["width"] * 0.25, y, steps=4)
    pg.mouse.move(bar["x"] + bar["width"] * 0.4, y, steps=4)
    pg.mouse.up()
    pg.wait_for_function(f"""() => /Look deeper · ~\\$\\d/.test(
        (document.querySelector('{box} .dv-act .dv-look') || {{}}).textContent || '')""")
    assert pg.inner_text(f"{box} .dv-act").startswith("chosen · ")
    assert "if it asks for more" in pg.inner_text(f"{box} .dv-act")

    # the stored look renders in place: a filmstrip, seen solid, inferred hatched
    pg.click(f"{box} .dv-lane[data-lane=deep] i.w")
    pg.wait_for_selector(f"{box} .dv-res .dv-film figure img")
    assert pg.locator(f"{box} .dv-film figure").count() == 4
    assert pg.locator(f"{box} .dv-film figure.asked").count() == 1
    assert pg.locator(f"{box} .dv-beatbar i.seen").count() == 2
    assert pg.locator(f"{box} .dv-beatbar i.inferred").count() == 1
    assert pg.locator(f"{box} .dv-beatbar i.ask").count() == 1
    assert "inferred 3.50→4.20" in pg.inner_text(f"{box} .dv-beats")
    assert "read again with 1 frame it asked for" in pg.inner_text(f"{box} .dv-res-head")
    # hover a beat lights its bracketing frames
    pg.hover(f"{box} .dv-beats li.inferred")
    assert pg.locator(f"{box} .dv-film figure.lit").count() == 2


def test_a_trim_keeps_the_shot_s_machine_line_open_with_the_new_range(bin_state, browser):
    """INTAKE M16 decision 9: the line "opens per item and closes on the next". A trim is
    the same item, but the strip rebuilt the line once the trim settled, and every build
    renders closed — the opened strip snapped shut under Karl's hands. Now the open
    strip's mark follows the new range and the top row's Look deeper is re-priced for it."""
    pg = _board(browser, bin_state)
    pg.locator("#tl .blk").nth(0).click()
    sid = pg.evaluate("tl.idAt(0)")
    box = "#inspector .deepline"
    look = "#inspector .srow.top .lookhost .dv-look"
    pg.wait_for_function(f"(document.querySelector('{look}') || {{}}).title?.includes('1.0–3.0 s')")
    pg.wait_for_function(f"!document.querySelector('{box} .dv-line').textContent.startsWith('…')")
    pg.locator(f"{box} .dv-line").click()
    pg.wait_for_selector(f"{box} .dv-markband")
    pg.evaluate(f"tl.setRange('{sid}', 1.5, null)")
    pg.wait_for_function(f"(document.querySelector('{look}') || {{}}).title?.includes('1.5–3.0 s')")
    pg.wait_for_timeout(300)
    assert pg.evaluate("tl.state.anchor") == sid, "the same shot is selected"
    assert pg.is_visible(f"{box} .dv-more"), "the trim closed the line"
    assert pg.evaluate(f"document.querySelector('{box} .dv-more')._deep.markRange") == [1.5, 3.0]
    assert pg.inner_text(f"{box} .dv-line").endswith("▾")
    # the next shot is the next item: closed
    pg.locator("#tl .blk").nth(1).click()
    pg.wait_for_function(f"document.querySelector('{box} .dv-line') && document.querySelector('{box} .dv-more').hidden")


class Scripted:
    def __init__(self):
        self.seen = []

    def complete(self, request):
        self.seen.append(request)
        times = [float(t) for t in re.findall(r"t=([0-9.]+)s  file", request.prompt)]
        payload = {
            "beats": [{"start": times[0], "end": times[0], "basis": "seen",
                       "frame": times[0], "what": "the card at rest"},
                      {"start": times[0], "end": times[1], "basis": "inferred",
                       "between": [times[0], times[1]], "what": "it shifts",
                       "why": "the frames differ"}],
            "events": [], "camera": {"mount": "static", "roll": "level"},
            "unsure": [], "need_frames": [], "summary": "scripted"}
        text = json.dumps(payload)
        return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                backend="scripted", model=config.model_for(request.role),
                                projected_usd=0.01, latency_ms=1, raw=text)


def test_look_deeper_from_a_seen_row_is_priced_then_runs_and_shows_the_beats(
        bin_state, browser):
    backend = Scripted()
    inference.set_backend(backend)
    inference.reset_spend()
    try:
        pg = _board(browser, bin_state)
        pg.evaluate("dock.open('bin')")
        pg.evaluate("document.querySelector('#more').hidden && document.querySelector('#moreFound').click()")      # under more found (M16)
        pg.click("#libTabs .tab[data-tab=seen]")
        row = "#library .cand:has(.dv-look)"
        pg.wait_for_function("""() => [...document.querySelectorAll('#library .cand .dv-look')]
            .some((b) => /Look deeper · ~\\$\\d/.test(b.textContent))""")
        assert not backend.seen                      # priced, nothing spent
        btn = pg.locator(f"{row} .dv-look", has_text="Look deeper · ~$").first
        btn.click()
        pg.wait_for_selector("#library .dv-res.inrow .dv-beats li.inferred", timeout=30000)
        assert len(backend.seen) == 1
        assert pg.locator("#library .dv-res.inrow .dv-beatbar i.seen").count() == 1
        assert pg.locator("#library .dv-res.inrow .dv-beatbar i.inferred").count() == 1
        assert pg.locator("#library .dv-res.inrow .dv-film figure").count() >= 4
        # the cut on the board did not change: the row's own click was not triggered
        assert pg.evaluate("segs.length") == 2
    finally:
        inference.set_backend(None)


def test_the_pass_carries_the_machine_line_that_opens_the_strip(bin_state, browser):
    """I16.3: on the pass, how the machine saw the clip is one line beside the kept range
    (INTAKE M16 decision 9); a click opens the strip, the band marked, the playhead live."""
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    pg.goto(f"{bin_state}/floor")
    pg.wait_for_function("window.floor && floor.state.queue.length > 0", timeout=15000)
    pg.wait_for_function("(document.querySelector('#deepFloor .dv-line') || {}).textContent?.includes('▸')")
    assert pg.locator("#deepFloor .dv-bar").count() == 0, "closed until asked"
    clip = pg.evaluate("floor.state.queue[floor.state.i].clip")
    assert pg.get_attribute("#deepFloor", "data-clip") == clip
    assert pg.inner_text("#deepFloor .dv-line").startswith("every word heard · a frame every")
    pg.click("#deepFloor .dv-line")
    pg.wait_for_selector("#deepFloor .dv-bar")
    # the playhead rides the strip, the band is marked
    pg.wait_for_function("!document.querySelector('#deepFloor .dv-headline').hidden")
    assert pg.is_visible("#deepFloor .dv-markband")
    # the pass is keys, not buttons: the strip shows, the spending is the board's
    assert pg.locator("#deepFloor button").count() == 0
    assert "Look deeper: on the board" == pg.inner_text("#deepFloor .dv-act")
    if clip == "CLIP_A.MP4":
        pg.click("#deepFloor .dv-lane[data-lane=deep] i.w")
        pg.wait_for_selector("#deepFloor .dv-res .dv-beats li.inferred")
        assert pg.locator("#deepFloor button").count() == 0
        pg.click("#deepFloor .dv-lane[data-lane=deep] i.w")         # again: closed
        pg.wait_for_function("!document.querySelector('#deepFloor .dv-res').innerHTML")


def test_the_machine_line_is_one_line_that_opens_the_strip_and_closes_on_the_next_call(
        bin_state, browser):
    """INTAKE M16 decision 9 / C4: `deep.line` says how the machine saw a clip in one
    line, in words from the sidecars; a click opens the whole strip under it (the range
    marked, the playhead carried), a second click closes it, and every call renders
    closed — the next item never inherits an open strip."""
    pg = _board(browser, bin_state)
    pg.evaluate("""() => {
        const host = document.createElement('div');
        host.id = 'lineHost';
        document.body.prepend(host);
        window.__ln = deep.line(host, 'CLIP_A.MP4', { range: [1, 3], compact: true });
    }""")
    pg.wait_for_function("document.querySelector('#lineHost .dv-line').textContent.includes('every')")
    assert pg.inner_text("#lineHost .dv-line") == \
        "every word heard · a frame every 2 s · 1 close look · 1 deep look ▸"
    assert pg.locator("#lineHost .dv-bar").count() == 0, "closed: one line, no strip"
    pg.click("#lineHost .dv-line")
    pg.wait_for_selector("#lineHost .dv-more .dv-bar")
    assert pg.inner_text("#lineHost .dv-line").endswith("▾")
    assert pg.is_visible("#lineHost .dv-markband")
    assert pg.locator("#lineHost button").count() == 0, "compact: nothing spends here"
    pg.evaluate("__ln.head(2.5); deep.head(document.querySelector('#lineHost'), 2.5)")
    pg.wait_for_function("!document.querySelector('#lineHost .dv-headline').hidden")
    pg.click("#lineHost .dv-line")
    assert pg.locator("#lineHost .dv-more").is_hidden()
    # opened again, then a new call for the next item: closed, and its own words
    pg.click("#lineHost .dv-line")
    pg.wait_for_selector("#lineHost .dv-more .dv-bar")
    pg.evaluate("deep.line(document.querySelector('#lineHost'), 'CLIP_B.MP4', {})")
    pg.wait_for_function("document.querySelector('#lineHost .dv-line').textContent.includes('▸')")
    assert pg.locator("#lineHost .dv-more").is_hidden()
    assert pg.locator("#lineHost .dv-bar").count() == 0
    assert pg.inner_text("#lineHost .dv-line") == "every word heard · not looked at yet ▸"


def test_the_open_screen_cards_carry_no_coverage_strip(bin_state, browser):
    """INTAKE M16 (C4, I16.2): how the machine saw a clip is one line on the pass and
    the board; a card on the open screen is its picture, length and name, and how the
    bin was indexed is the one line above the cards."""
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    pg.goto(f"{bin_state}/open")
    pg.wait_for_selector('.card[data-clip="CLIP_A.MP4"]')
    assert pg.locator(".dv-mini").count() == 0
    assert pg.evaluate("typeof window.deep") == "undefined", "/deep.js is not loaded there"
