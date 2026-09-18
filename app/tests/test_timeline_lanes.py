"""INTAKE M9 I9.4 — the timeline's lanes and drag and drop, driven in a real browser.

The module under test is `app/static/timeline-lanes.js`, built on the foundation
(`timeline.js`, `window.tl`): the A1 music lane with the bed, its fades and a dip under
every speech region in the cut; the markers lane (`★` per hero keep in the cut, a tick
per ranked event inside a shot) and the bin lane (the pass's keeps not in the cut as
faint outlines); the proposal ghost lane while a proposal is pending; the body drag on
V1 with a drop line; and the HTML5 drop of a kept row, a Find result or an available
outline onto V1. Assertions are made against the EDL on disk, the monitor's element and
app.js's own `segs`, not against the module's word for it.

Same fixture pattern as test_timeline_ui.py: the real uvicorn server on a real port, the
synthetic three-clip bin, the EDL re-seeded per test (two shots, 2 s each). Skipped when
playwright is absent.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api",
                                     reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402


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
                     visual=project["work"] / "lanes-visual", assets=project["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in project["stems"]])

    port = _free_port()
    config = uvicorn.Config(server.app, host="127.0.0.1", port=port,
                            log_level="error")
    srv = uvicorn.Server(config)
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
def page(live_server, project):
    seed = json.dumps({
        "variant": "T", "title": "test cut", "orient": "none", "story": "",
        "target_s": [5, 20],
        "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
                     {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}],
    }, indent=1)
    Path(project["edl"]).write_text(seed, encoding="utf-8")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        pg = browser.new_page(viewport={"width": 1280, "height": 900})
        pg.goto(live_server)
        pg.wait_for_selector("#tl .blk")
        yield pg
        browser.close()


def ids(page) -> list[str]:
    return page.evaluate("segs.map(s => s.id)")


def shots(page) -> list[list]:
    return page.evaluate("segs.map(s => [s.clip, s.in, s.out])")


def wait_saved(page) -> None:
    page.wait_for_function(
        "document.querySelector('#saveState').textContent.startsWith('saved')",
        timeout=8000)


def on_disk(project) -> list[dict]:
    return json.loads(Path(project["edl"]).read_text(encoding="utf-8"))["segments"]


def left(page, selector: str, nth: int = 0) -> float:
    return page.evaluate(
        f"parseFloat(document.querySelectorAll('{selector}')[{nth}].style.left)")


def width(page, selector: str, nth: int = 0) -> float:
    return page.evaluate(
        f"parseFloat(document.querySelectorAll('{selector}')[{nth}].style.width)")


def x_of(page, film_t: float) -> float:
    return page.evaluate(f"tl.timeToX({film_t})")


def _put_selects(page, selects: list[dict]) -> dict:
    """The bin, written the way the bin editor writes it (PUT /api/selects)."""
    out = page.evaluate("""(selects) => fetch('/api/selects', {
        method: 'PUT', headers: {'content-type': 'application/json'},
        body: JSON.stringify({selects})}).then(r => r.json())""", selects)
    assert out.get("ok"), out
    return out


def view_x(page, film_t: float) -> float:
    """Viewport x of a film time: the canvas x less the view's scroll, plus its left."""
    return page.evaluate(
        f"(() => {{ const v = document.querySelector('#tl .tl-view');"
        f" return v.getBoundingClientRect().left + tl.timeToX({film_t}) - v.scrollLeft; }})()")


def drag_to(page, source: str, tx: float, ty: float) -> None:
    """A native HTML5 drag from `source` to a viewport point: press, move in steps
    (Chromium only begins a drag on real movement — `page.drag_and_drop`'s single move
    never starts one), release. The source point is near its left edge, which is the
    part of a wide outline that is on screen."""
    box = page.locator(source).first.bounding_box()
    sx, sy = box["x"] + min(20, box["width"] / 2), box["y"] + box["height"] / 2
    page.mouse.move(sx, sy)
    page.mouse.down()
    page.mouse.move(sx + 12, sy + 6, steps=4)
    page.mouse.move(tx, ty, steps=12)
    page.mouse.up()


def drag_to_v1(page, source: str, film_t: float) -> None:
    """Drop `source` on V1 at a film time. The timeline is scrolled into view first: a
    drag is in viewport coordinates, and what the earlier tests leave above the timeline
    (a "last proposal" line, job rows) can push the lower lanes under the fold."""
    page.locator("#tl").scroll_into_view_if_needed()
    lane = page.locator("#tl .tl-lane[data-lane=V1]").bounding_box()
    drag_to(page, source, view_x(page, film_t), lane["y"] + lane["height"] / 2)


# ------------------------------------------------------------------ the lanes

def test_the_music_lane_draws_the_bed_its_fades_and_a_dip_per_speech_region(page):
    """`effects_music` set → an A1 lane: the track's name, a bar the length of the film,
    the fade-in and fade-out as ramps, and one dip per speech region in the cut — the
    same padded, merged regions the monitor ducks by. The three fabricated utterances
    merge into one region per clip (gaps ≤ 1.2 s), so two shots make two dips, the
    second one from 2.15 s of film (CLIP_B's speech starts at 0.15 s) to the end."""
    a1 = "#tl .tl-xlane[data-lane=A1]"
    assert page.evaluate(f"document.querySelector('{a1}').hidden") is True
    page.select_option("#musicTrack", "music/bed.wav")
    page.wait_for_function(f"!document.querySelector('{a1}').hidden", timeout=8000)
    page.wait_for_function(f"document.querySelectorAll('{a1} .duck').length === 2", timeout=8000)
    zoom = page.evaluate("tl.state.zoom")
    assert "bed" in page.locator(f"{a1} .lbl").inner_text()
    assert "12 dB" in page.locator(f"{a1} .lbl").inner_text()
    # the bed bar spans the film
    assert left(page, f"{a1} .bed") == pytest.approx(x_of(page, 0), abs=0.5)
    assert width(page, f"{a1} .bed") == pytest.approx(4 * zoom, abs=0.5)
    # the dips: shot 1 (CLIP_A 1.0–3.0, all speech) → film 0–2; shot 2 → film 2.15–4
    assert left(page, f"{a1} .duck", 0) == pytest.approx(x_of(page, 0), abs=0.5)
    assert width(page, f"{a1} .duck", 0) == pytest.approx(2.0 * zoom, abs=0.5)
    assert left(page, f"{a1} .duck", 1) == pytest.approx(x_of(page, 2.15), abs=0.5)
    assert width(page, f"{a1} .duck", 1) == pytest.approx(1.85 * zoom, abs=0.5)
    # a 12 dB duck is a gain ratio of 0.25: the dip takes three quarters of the bar
    assert page.evaluate(f"document.querySelector('{a1} .duck').style.height") == "74.9%"
    # the fades: the defaults are 1.5 s in and 4 s out (the whole 4 s film)
    assert width(page, f"{a1} .fade.in") == pytest.approx(1.5 * zoom, abs=0.5)
    assert width(page, f"{a1} .fade.out") == pytest.approx(4.0 * zoom, abs=0.5)
    # the monitor's own curve rides on top, sampled along the film
    points = page.evaluate(
        f"document.querySelector('{a1} .curve polyline').getAttribute('points').split(' ').length")
    assert points >= 24
    # the lane draws, it does not edit: a click brings the music panel into view
    page.evaluate("window.scrollTo(0, 0)")
    page.locator(a1).click(position={"x": 400, "y": 10})
    page.wait_for_function("""() => {
        const r = document.querySelector('#musicPanel').getBoundingClientRect();
        return r.top >= -1 && r.bottom <= window.innerHeight + 1; }""", timeout=5000)
    assert page.evaluate("JSON.stringify(segs.map(s => [s.in, s.out]))") == "[[1,3],[0,2]]"
    # the duck slider changes the depth; "no music" takes the lane away
    page.evaluate("document.querySelector('#duck').value = 6; musicChanged()")
    page.wait_for_function(
        f"document.querySelector('{a1} .duck').style.height === '49.9%'", timeout=8000)
    page.select_option("#musicTrack", "")
    page.wait_for_function(f"document.querySelector('{a1}').hidden", timeout=8000)


def test_the_markers_lane_stars_a_hero_in_the_cut_ticks_an_event_and_shelves_the_rest(page):
    """Two keeps: a hero that IS in the cut (CLIP_A 1.0–3.0 is shot 1 — matched by clip
    and overlap ≥ 0.5, the kept tab's rule) gets a `★` at its film position; a keep that
    is not (CLIP_C) becomes a faint `available` outline in the bin lane after the end of
    the film, as wide as it is long. A ranked event inside shot 2 is a tick at its film
    time; one in a clip that is not in the cut has no film position and no tick."""
    import server

    vdir = Path(server.STATE["visual"])
    vdir.mkdir(parents=True, exist_ok=True)
    ranked = vdir / "events.json"
    ranked.write_text(json.dumps({"built": 0, "clips": 1, "events": [
        {"rank": 1, "clip": "CLIP_B.MP4", "start": 0.5, "end": 1.5, "kind": "fall",
         "notable": True, "score": 1.5, "source": "close look",
         "what": "the body goes down in the snow",
         "why_ranked": {"confirmation": "confirmed"}},
        {"rank": 2, "clip": "CLIP_C.MP4", "start": 1.5, "end": 3.5, "kind": "jump",
         "notable": True, "score": 0.4, "source": "sheet", "what": "a backflip",
         "why_ranked": {"confirmation": "contradicted"}},
    ]}), encoding="utf-8")
    _put_selects(page, [
        {"clip": "CLIP_A.MP4", "start": 1.0, "end": 3.0, "hero": True, "why": "the hello"},
        {"clip": "CLIP_C.MP4", "start": 0.5, "end": 4.0, "hero": False,
         "why": "the whole take"},
    ])
    try:
        page.reload()
        page.wait_for_selector("#tl .blk")
        markers = "#tl .tl-xlane[data-lane=markers]"
        page.wait_for_function(f"!document.querySelector('{markers}').hidden", timeout=8000)
        zoom = page.evaluate("tl.state.zoom")
        legend = page.locator(f"{markers} .lbl").inner_text()
        assert "★" in legend and "hero" in legend and "event" in legend and "available keep" in legend
        # the hero's star at the keep's film position — shot 1 starts at the keep's start
        assert page.locator(f"{markers} .mk.hero").count() == 1
        assert left(page, f"{markers} .mk.hero") == pytest.approx(x_of(page, 0), abs=0.5)
        assert "the hello" in page.locator(f"{markers} .mk.hero").get_attribute("title")
        # the event in CLIP_B at 0.5 s is 2.5 s of film; the CLIP_C one is nowhere
        ticks = page.locator(f"{markers} .mk.ev")
        assert ticks.count() == 1
        assert left(page, f"{markers} .mk.ev") == pytest.approx(x_of(page, 2.5), abs=0.5)
        assert ticks.first.get_attribute("data-kind") == "fall"
        assert ticks.first.get_attribute("title") == "the body goes down in the snow · #1"
        # V1 moved down to make room, and the view grew with it
        assert page.evaluate("getComputedStyle(document.querySelector('#tl')).getPropertyValue('--tl-above').trim()") == "18px"
        # the keep that is not in the cut: an outline after the end, width to length
        avail = "#tl .tl-xlane[data-lane=bin] .avail"
        assert page.locator(avail).count() == 1
        assert left(page, avail) == pytest.approx(x_of(page, 4.0), abs=0.5)
        assert width(page, avail) == pytest.approx(3.5 * zoom, abs=0.5)
        assert page.locator(avail).get_attribute("draggable") == "true"
        assert "CLIP_C" in page.locator(avail).inner_text()
        # the lane follows the zoom
        page.keyboard.press("+")
        page.wait_for_function(
            f"Math.abs(parseFloat(document.querySelector('{avail}').style.width) - {7.0 * zoom}) < 1",
            timeout=5000)
    finally:
        ranked.unlink(missing_ok=True)


def test_a_pending_proposal_is_a_ghost_lane_you_can_play_either_side_of(page):
    """A scripted proposal — CLIP_B first, then CLIP_A, then CLIP_C — while the diff panel
    is up: three ghost blocks under V1 by the proposal's film time. B matches shot 2 and
    keeps its order (dim); A matches shot 1 but has moved behind B (an arrow from where it
    is now) and comes back polished (trimmed); C is new (green). Nothing is removed, so
    V1 wears no strike. Clicking a ghost cues the monitor to THAT range — a shot that is
    not in the cut — without playing the cut; `play cut` hands the monitor back; discard
    takes the lane away and the cut is untouched throughout."""
    from roughcut import config, inference

    class Scripted:
        name = "scripted"

        def complete(self, request):
            text = json.dumps({
                "segments": [
                    {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "open on B"},
                    {"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "then A"},
                    {"clip": "CLIP_C.MP4", "in": 0.5, "out": 4.0, "why": "close on C"},
                ],
                "notes": "swapped the opening and closed on the unused clip"})
            model = config.model_for(request.role)
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend="scripted", model=model,
                                    projected_usd=0.0001, latency_ms=1, raw=text)

    inference.set_backend(Scripted())
    inference.reset_spend()
    ghost = "#tl .tl-xlane[data-lane=ghost]"
    try:
        before = page.evaluate("JSON.stringify(segs)")
        assert page.evaluate(f"document.querySelector('{ghost}').hidden") is True
        page.locator("#note").fill("swap the opening, close on the unused clip")
        page.locator("#ask").click()
        page.wait_for_selector("#proposal:visible", timeout=30000)
        page.wait_for_function(
            f"document.querySelectorAll('{ghost} .ghost').length === 3", timeout=8000)
        plan = page.evaluate("pendingPlan.segments.map(s => [s.clip, s.in, s.out])")
        zoom = page.evaluate("tl.state.zoom")
        classes = page.evaluate(
            f"[...document.querySelectorAll('{ghost} .ghost')].map(g => g.className)")
        # (B and A both come back polished — the server snaps a proposal's cut points to
        # speech — so both wear `trimmed`; the order verdict is what matters here)
        assert classes[0].startswith("ghost same"), classes
        assert classes[1].startswith("ghost moved"), classes
        assert classes[2] == "ghost added", classes
        # aligned by the proposal's own film time
        assert left(page, f"{ghost} .ghost", 0) == pytest.approx(x_of(page, 0), abs=0.5)
        assert left(page, f"{ghost} .ghost", 1) == pytest.approx(
            x_of(page, plan[0][2] - plan[0][1]), abs=0.5)
        assert width(page, f"{ghost} .ghost", 2) == pytest.approx(
            (plan[2][2] - plan[2][1]) * zoom, abs=0.5)
        # one arrow, from shot 1 on V1 to its ghost; no strike — nothing was removed
        assert page.locator("#tl .tl-arrows line.arrow").count() == 1
        assert page.locator("#tl .tl-over .strike").count() == 0
        x1 = page.evaluate("parseFloat(document.querySelector('#tl .tl-arrows line.arrow').getAttribute('x1'))")
        assert x1 == pytest.approx(x_of(page, 1.0), abs=1)
        assert "play proposal" in page.locator(f"{ghost} .lbl").inner_text()
        assert "on" in page.locator(f"{ghost} .play-cut").get_attribute("class")

        # click the added ghost: the monitor is on CLIP_C at 0.5 s, and the cut is not playing
        page.locator(f"{ghost} .ghost").nth(2).click()
        page.wait_for_function(
            "liveVideo().dataset.src.includes('CLIP_C') && liveVideo().currentTime >= 0.45",
            timeout=10000)
        assert not page.evaluate("player.playing")
        assert page.evaluate("liveVideo().currentTime") < 4.0
        assert "proposal" in page.locator("#playingWhat").inner_text()
        # a proposal is not an edit: the cut is what it was
        assert page.evaluate("JSON.stringify(segs)") == before
        assert page.locator("#tl .blk").count() == 2

        # play proposal / play cut
        page.locator(f"{ghost} .play-proposal").click()
        page.wait_for_function("liveVideo().dataset.src.includes('CLIP_B')", timeout=10000)
        assert "on" in page.locator(f"{ghost} .play-proposal").get_attribute("class")
        assert not page.evaluate("player.playing")
        page.locator(f"{ghost} .play-cut").click()
        page.wait_for_function("player.playing && player.idx === 0", timeout=10000)
        assert "on" in page.locator(f"{ghost} .play-cut").get_attribute("class")
        page.evaluate("pauseCut()")

        # discard: the panel closes, the ghost lane goes, the cut is untouched
        page.locator("#rejectProposal").click()
        page.wait_for_function(f"document.querySelector('{ghost}').hidden", timeout=5000)
        assert page.locator("#tl .tl-arrows line.arrow").count() == 0
        assert page.evaluate("JSON.stringify(segs)") == before

        # and a proposal that drops a shot strikes it out on V1
        page.evaluate("segs.push({clip: 'CLIP_C.MP4', in: 4.5, out: 5.5, why: 'gone soon'}); render()")
        page.locator("#ask").click()
        page.wait_for_selector("#proposal:visible", timeout=30000)
        page.wait_for_function("document.querySelectorAll('#tl .tl-over .strike').length === 1", timeout=8000)
        assert left(page, "#tl .tl-over .strike") == pytest.approx(x_of(page, 4.0), abs=0.5)
        page.locator("#rejectProposal").click()
    finally:
        inference.set_backend(None)


# ------------------------------------------------------------------ drag and drop

def test_dragging_a_shots_body_past_the_next_one_reorders_and_the_ids_travel(page, project):
    """Pointer-drag shot 1 by its body to the end of the film: a drop line at the cut
    point it will land on while dragging, then `tl.move` — one `move` undo entry, ids
    travelling with the shots, the EDL on disk in the new order. A 2 px move is a click."""
    a, b = ids(page)
    box = page.locator("#tl .blk").nth(0).bounding_box()
    box2 = page.locator("#tl .blk").nth(1).bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

    # under 4 px it is the foundation's click: select and play, no reorder
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx + 2, cy + 1)
    page.mouse.up()
    # (the click's other half, playing from there, is the foundation's promise and its
    # own test's business — a wait on the monitor starting is what stalls under load)
    page.wait_for_function(f"tl.state.sel.has('{a}')", timeout=5000)
    page.evaluate("pauseCut()")
    assert ids(page) == [a, b]
    assert page.evaluate("[...tl.state.sel]") == [a]

    # a click on a block plays from it, and that smooth-scrolls the page twice (app.js's
    # revealMonitor and scrollSel); on the taller page a full suite leaves behind, the
    # timeline can settle outside the viewport, where a press hits nothing. Let the
    # scrolls finish, bring the timeline back, measure, and check the press point is
    # really the block before pressing.
    def settled_boxes():
        page.wait_for_function("""() => new Promise((ok) => {
            const y = window.scrollY;
            setTimeout(() => ok(window.scrollY === y), 250); })""", timeout=5000)
        page.locator("#tl").scroll_into_view_if_needed()
        b0 = page.locator("#tl .blk").nth(0).bounding_box()
        b1 = page.locator("#tl .blk").nth(1).bounding_box()
        x, y = b0["x"] + b0["width"] / 2, b0["y"] + b0["height"] / 2
        under = page.evaluate(
            f"(() => {{ const e = document.elementFromPoint({x}, {y});"
            f" return e ? (e.closest('.blk') || e).className : null; }})()")
        assert under and under.startswith("blk"), (under, b0, page.evaluate("window.scrollY"))
        return b0, b1, x, y

    box, box2, cx, cy = settled_boxes()
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx + 40, cy, steps=4)
    page.mouse.move(box2["x"] + box2["width"] - 12, cy, steps=6)
    # the drop line is up, at the end of the film (the nearest cut point)
    assert page.evaluate("document.querySelector('#tl .tl-dropline').style.display") == "block"
    assert left(page, "#tl .tl-dropline") == pytest.approx(x_of(page, 4.0), abs=1)
    assert "dragging" in page.locator("#tl .blk").nth(0).get_attribute("class")
    page.mouse.up()
    assert page.evaluate("document.querySelector('#tl .tl-dropline').style.display") == "none"
    assert ids(page) == [b, a]
    assert shots(page) == [["CLIP_B.MP4", 0.0, 2.0], ["CLIP_A.MP4", 1.0, 3.0]]
    assert page.locator("#tl .blk.dragging").count() == 0
    assert page.locator("#undo").get_attribute("title").startswith("undo: move")
    assert page.locator(".seg").first.locator(".clip").inner_text() == "CLIP_B"
    wait_saved(page)
    assert [(s["id"], s["clip"]) for s in on_disk(project)] == [(b, "CLIP_B.MP4"), (a, "CLIP_A.MP4")]
    page.keyboard.press("Control+z")
    assert ids(page) == [a, b]

    # the whole selection moves together: ⇧-select both, drag the first to the end → no-op
    page.locator("#tl .blk").nth(0).click()
    page.evaluate("pauseCut()")
    page.locator("#tl .blk").nth(1).click(modifiers=["Shift"])
    assert page.evaluate("tl.state.sel.size") == 2
    box, box2, cx, cy = settled_boxes()
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(box2["x"] + box2["width"] - 12, cy, steps=8)
    assert page.locator("#tl .blk.dragging").count() == 2
    page.mouse.up()
    assert ids(page) == [a, b]
    # the move was undone above and a no-op drag pushes nothing: the stack is empty
    assert page.locator("#undo").get_attribute("title") == "nothing to undo"


def test_dropping_an_available_outline_on_v1_inserts_that_range_at_the_drop_line(page):
    """A keep not in the cut is an `available` outline; dragged onto V1 between the two
    shots it becomes the third shot with the keep's range and reason, and stops being
    available. HTML5 drag and drop, end to end."""
    _put_selects(page, [{"clip": "CLIP_C.MP4", "start": 0.5, "end": 4.0, "hero": False,
                         "why": "the whole take"}])
    page.reload()
    page.wait_for_selector("#tl .blk")
    avail = "#tl .tl-xlane[data-lane=bin] .avail"
    page.wait_for_selector(avail, timeout=8000)
    drag_to_v1(page, avail, 2.0)
    page.wait_for_function("segs.length === 3", timeout=8000)
    assert shots(page) == [["CLIP_A.MP4", 1.0, 3.0], ["CLIP_C.MP4", 0.5, 4.0],
                           ["CLIP_B.MP4", 0.0, 2.0]]
    assert page.evaluate("segs[1].why") == "the whole take"
    assert page.locator("#undo").get_attribute("title").startswith("undo: insert")
    assert page.evaluate("[...tl.state.sel]") == [page.evaluate("segs[1].id")]
    assert page.locator("#tl .blk").count() == 3
    page.wait_for_function(f"document.querySelectorAll('{avail}').length === 0", timeout=5000)
    assert page.evaluate("document.querySelector('#tl .tl-dropline').style.display") == "none"
    # one undo takes the whole drop back
    page.keyboard.press("Control+z")
    assert page.evaluate("segs.length") == 2


def test_a_kept_row_or_a_find_result_dropped_on_v1_inserts_there(page, project):
    """The kept tab's rows are draggable; one dropped at the top of the film becomes shot
    1, one dropped past the end appends. A Find result carries the same payload."""
    _put_selects(page, [{"clip": "CLIP_A.MP4", "start": 4.0, "end": 5.5, "hero": True,
                         "why": "the goodbye"}])
    # the kept tab is a long way down the sidebar: a viewport tall enough to hold the
    # row and the timeline at once, since a drag cannot scroll the page for itself
    page.set_viewport_size({"width": 1280, "height": 2400})
    page.reload()
    page.wait_for_selector("#library .keep")
    page.wait_for_function(
        "document.querySelector('#library .keep').getAttribute('draggable') === 'true'", timeout=5000)
    row = page.locator("#library .keep").first.bounding_box()
    assert row["y"] + row["height"] <= 2400, "the fixture's viewport must show the row"
    # dropped at the very start → before shot 1
    drag_to_v1(page, "#library .keep", 0.05)
    page.wait_for_function("segs.length === 3", timeout=8000)
    assert shots(page) == [["CLIP_A.MP4", 4.0, 5.5], ["CLIP_A.MP4", 1.0, 3.0],
                           ["CLIP_B.MP4", 0.0, 2.0]]
    assert page.evaluate("segs[0].why") == "the goodbye"
    # the row flipped to "in the cut" and is still draggable: past the end → appended
    page.wait_for_function(
        "document.querySelector('#library .keep a.use') !== null", timeout=8000)
    ruler = page.locator("#tl .tl-ruler").bounding_box()
    drag_to(page, "#library .keep", view_x(page, page.evaluate("tl.total()")) + 30,
            ruler["y"] + ruler["height"] / 2)
    page.wait_for_function("segs.length === 4", timeout=8000)
    assert shots(page)[3] == ["CLIP_A.MP4", 4.0, 5.5]
    wait_saved(page)
    assert [(s["clip"], s["in"]) for s in on_disk(project)] == [
        ("CLIP_A.MP4", 4.0), ("CLIP_A.MP4", 1.0), ("CLIP_B.MP4", 0.0), ("CLIP_A.MP4", 4.0)]

    # a Find result carries the same payload — the row is loaded (its own click) and read
    page.locator("#findQ").fill("goodbye")
    page.locator("#findGo").click()
    page.wait_for_selector("#findResults .cand", timeout=15000)
    assert page.locator("#findResults .cand").first.get_attribute("draggable") == "true"
    payload = page.evaluate("""() => {
        const row = document.querySelector('#findResults .cand');
        const dt = new DataTransfer();
        row.dispatchEvent(new DragEvent('dragstart', {bubbles: true, dataTransfer: dt}));
        return JSON.parse(dt.getData(tlLanes.MIME)); }""")
    assert payload["clip"] == "CLIP_A.MP4"
    assert payload["start"] == pytest.approx(5.0, abs=0.6)
    assert payload["end"] > payload["start"]
    # …and a synthetic drop of it lands at the drop line, like the rows above
    before = page.evaluate("segs.length")
    page.evaluate("""(p) => {
        const dt = new DataTransfer();
        dt.setData(tlLanes.MIME, JSON.stringify(p));
        const v = document.querySelector('#tl .tl-view');
        const r = v.getBoundingClientRect();
        const x = r.left + tl.timeToX(0.02);
        v.dispatchEvent(new DragEvent('dragover', {bubbles: true, dataTransfer: dt, clientX: x, clientY: r.top + 40}));
        v.dispatchEvent(new DragEvent('drop', {bubbles: true, dataTransfer: dt, clientX: x, clientY: r.top + 40}));
    }""", payload)
    assert page.evaluate("segs.length") == before + 1
    assert page.evaluate("segs[0].clip") == "CLIP_A.MP4"
    assert page.evaluate("segs[0].in") == pytest.approx(payload["start"], abs=0.01)
