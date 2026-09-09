"""The pass, driven in a real browser (docs/INTAKE.md M2).

Everything that matters on the floor lives in the browser's media stack and in the
JavaScript: that the preview plays from the moment, that the kept range is the pick's
preview snapped to the sentence and moves only by hand (a drag, a key), that one key
writes a verdict to the EDL, that undo puts it back. So, like test_ui_flow.py, this
drives Chromium against the live server and asserts on the EDL on disk.

The synthetic bin (conftest.py) yields one pick per clip: the two candidates at 0.5 and
5.0 merge (2.0 s apart, under MERGE_GAP_S), so each pick is 0-6 s and previews whole.
The transcript is "hello there" 0.5-2.0, "how are you" 2.4-4.0, "goodbye" 5.0-5.6, so
a preview cut to end at 1.2 s (inside the first line) snaps to 2.45 (end + PAD_TAIL) —
the tests that need the snap to have something to do cut it that way.

Skipped, not failed, when playwright is absent:

    uv run --with pytest --with fastapi --with uvicorn --with httpx \
        --with playwright pytest app/tests/test_floor_ui.py -q
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
                     visual=project["work"] / "no-visual", assets=project["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in project["stems"]])

    port = _free_port()
    config = uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error")
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


SEED = {
    "variant": "T", "title": "test cut", "orient": "none", "story": "",
    "target_s": [5, 20],
    "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
                 {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}],
}


@pytest.fixture
def page(live_server, project):
    # Every verdict reaches the EDL, so each test starts from the seed — no selects,
    # no floor position — rather than from whatever the previous test decided.
    Path(project["edl"]).write_text(json.dumps(SEED, indent=1), encoding="utf-8")
    with sync_playwright() as pw:
        # The picture plays with sound from a key; a fake microphone lets V record.
        browser = pw.chromium.launch(args=[
            "--autoplay-policy=no-user-gesture-required",
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
        ])
        pg = browser.new_page(viewport={"width": 1280, "height": 900})
        pg.goto(f"{live_server}/floor")
        pg.wait_for_function("window.floor && floor.state.queue.length > 0", timeout=15000)
        yield pg
        browser.close()


def edl(project) -> dict:
    return json.loads(Path(project["edl"]).read_text(encoding="utf-8"))


def wait_edl(project, pred, timeout=8.0) -> dict:
    """The verdict is a POST the page awaits; the file is written before it answers."""
    deadline = time.time() + timeout
    while True:
        d = edl(project)
        if pred(d):
            return d
        if time.time() > deadline:
            raise AssertionError(f"EDL never satisfied the predicate: {json.dumps(d)[:800]}")
        time.sleep(0.05)


def playing_at(page, t: float, timeout=10000):
    page.wait_for_function(
        f"document.querySelector('#pic').currentTime >= {t}", timeout=timeout)


def paused_at(page) -> float:
    page.keyboard.press("k")
    page.wait_for_function("document.querySelector('#pic').paused", timeout=3000)
    return page.evaluate("document.querySelector('#pic').currentTime")


def stamped(page, word: str):
    """The stamp lands when the verdict's POST answers, a beat after the EDL is written."""
    page.wait_for_function(
        f"document.querySelector('#stamp').textContent === {json.dumps(word)}", timeout=5000)


# ------------------------------------------------------------------ the page

def test_the_floor_lists_the_picks_and_starts_on_the_first(page):
    assert page.evaluate("floor.state.queue.length") == 3
    assert "pick 1 of 3" in page.locator("#hudPos").inner_text()
    assert "queue frozen" in page.locator("#hudPos").inner_text()
    assert page.locator("#ctxClip").inner_text() == "CLIP_A"
    # the picture is the pick's proxy, fetched from the preview start, metadata only
    src = page.evaluate("document.querySelector('#pic').getAttribute('src')")
    assert src.startswith("/media/proxy/CLIP_A.mp4#t=0.00"), src
    assert page.evaluate("document.querySelector('#pic').preload") == "metadata"
    # witnesses as seals with the word, the reason on the caption line
    seals = page.locator("#seals .seal")
    assert seals.count() >= 1
    assert "HEARD" in seals.first.inner_text()
    assert "hello there" in page.locator("#why").inner_text()
    # the zoomed strip carries words (they fit at 80 px/s) and snap ticks at line ends,
    # line starts and word starts — where the keys and a dragged handle land
    assert page.locator("#zoomInner .w").count() == 6
    assert page.locator("#zoomInner .snap.end").count() == 3
    assert page.locator("#zoomInner .snap.start").count() == 3
    assert page.locator("#zoomInner .snap.word").count() == 6
    # the tape marks this clip's picks, the current one as a bracket, with a ruler (every
    # second on a 6 s clip), a lens, and a legend in the margin that names the marks
    assert page.locator("#tapeMarks span.now").count() == 1
    assert page.locator("#tapeRuler span").count() == 7
    assert page.locator("#tapeRuler span").first.inner_text() == "0:00"
    assert page.locator("#tapeLens").is_visible()
    legend = page.locator("#legend").inner_text()
    for word in ("this", "picked", "later", "undecided", "lens"):
        assert word in legend
    assert "WHOLE CLIP" in page.locator("#tapeLbl").inner_text()
    assert "CLOSER" in page.locator("#zoom").inner_text()
    assert page.locator("#tapeTrace path").count() == 0, "no felt witness, no trace"
    # and it is playing, from the top of the preview
    playing_at(page, 0.3)


def test_no_buttons_in_the_flow(page):
    assert page.locator("#app button").count() == 0


def test_the_key_line_shows_six_things_and_the_map_has_the_rest(page):
    """I2.7 move 4: P X U · space · [ ] { } · V · ? on the line; everything else behind ?."""
    keys = page.locator("#keys")
    assert keys.locator(".key").count() == 10
    line = keys.inner_text()
    for word in ("shuttle", "hero", "undo", "frame", "evidence", "more"):
        assert word not in line, word
    page.keyboard.press("?")
    page.wait_for_selector("#overlay[data-kind=keymap]")
    full = page.locator("#overlayBox").inner_text()
    for word in ("shuttle", "hero", "undo", "frame step", "evidence", "drag"):
        assert word in full, word


# --------------------------------------------------------------- verdicts

def preview_to(page, end: float):
    """Cut the current pick's preview to `end` and reload it: it plays from 0 and stops
    there on its own."""
    page.evaluate(f"floor.current().preview[1] = {end}; floor.show(0)")
    page.wait_for_function(
        f"document.querySelector('#pic').paused && document.querySelector('#pic').currentTime >= {end - 0.1}",
        timeout=10000)


def test_P_keeps_the_preview_snapped_to_the_sentence_and_advances(page, project):
    """I2.7 move 2: the band is the pick's preview when it loads, snapped outward to the
    sentence, and P writes exactly that. The preview is cut to 1.2 s — inside "hello
    there" — so the snap has something to do."""
    preview_to(page, 1.2)
    assert page.evaluate("floor.keepRange().snapped") == [0.0, 2.45]
    # the margin says what will be kept before the key is pressed, and where it came from
    assert page.locator("#ctxKeep").inner_text().startswith("0:00.0–0:02.5")
    assert "the preview 0:00.0–0:01.2, snapped out" in page.locator("#ctxSnap").inner_text()
    assert "watched" not in page.locator("#left").inner_text()
    page.keyboard.press("p")
    d = wait_edl(project, lambda d: len(d.get("selects", [])) == 1)
    s = d["selects"][0]
    assert s["clip"] == "CLIP_A.MP4"
    assert s["start"] == 0.0, "the preview started at 0, before any line"
    assert s["end"] == 2.45, "end of 'hello there' + PAD_TAIL"
    assert s["why"] and s["witnesses"][0]["kind"] == "heard"
    assert s["source"] == "floor" and s["hero"] is False
    # the timeline is untouched: a keep is not a shot
    assert d["segments"] == SEED["segments"]
    # a stamp landed, then the next pick began
    page.wait_for_function("document.querySelector('#ctxClip').textContent === 'CLIP_B'",
                           timeout=5000)
    assert "pick 2 of 3" in page.locator("#hudPos").inner_text()
    assert "1 moment" in page.locator("#hudBin").inner_text()
    assert "0:02.5" in page.locator("#hudBin").inner_text()     # if strung out


def test_X_rejects_the_picks_own_range_and_1_marks_a_hero(page, project):
    page.keyboard.press("x")
    d = wait_edl(project, lambda d: d.get("floor", {}).get("verdicts"))
    v = d["floor"]["verdicts"][0]
    assert v["verdict"] == "reject" and v["clip"] == "CLIP_A.MP4"
    assert (v["start"], v["end"]) == (0.0, 6.0)
    stamped(page, "REJECTED")
    # the stamp lands, then the pass moves on by itself (I2.8 1c) — no ↵
    page.wait_for_function("document.querySelector('#ctxClip').textContent === 'CLIP_B'")
    playing_at(page, 0.8)
    paused_at(page)
    page.keyboard.press("1")
    d = wait_edl(project, lambda d: len(d.get("selects", [])) == 1)
    assert d["selects"][0]["hero"] is True and d["selects"][0]["clip"] == "CLIP_B.MP4"
    stamped(page, "HERO")
    assert "1 hero" in page.locator("#hudBin").inner_text()


def test_the_verdict_carries_the_typed_note(page, project):
    page.keyboard.press("n")
    page.wait_for_selector("#noteEdit:visible")
    page.keyboard.type("hold on his face after")
    page.keyboard.press("Enter")
    assert "hold on his face after" in page.locator("#noteText").inner_text()
    # a note on an undecided pick is not a verdict: nothing reached the EDL yet
    assert "floor" not in edl(project) or not edl(project)["floor"].get("verdicts")
    playing_at(page, 0.6)
    paused_at(page)
    page.keyboard.press("p")
    d = wait_edl(project, lambda d: len(d.get("selects", [])) == 1)
    assert d["selects"][0]["note"] == "hold on his face after"


# ------------------------------------------------------------------ trimming

def test_trim_keys_snap_to_sentences_and_arrows_step_frames_at_the_edge(page, project):
    preview_to(page, 1.2)                            # the band starts as 0-2.45
    assert page.evaluate("floor.keepRange().snapped[1]") == 2.45
    # } extends the out-point to the next line's end: 2.45 -> 4.45 ("how are you")
    page.keyboard.press("}")
    assert page.evaluate("floor.keepRange().snapped[1]") == 4.45
    assert "extend to the next line" in page.locator("#ctxExtend").inner_text()
    assert "goodbye" in page.locator("#ctxExtend").inner_text()
    page.keyboard.press("}")                                    # 6.05 clamps to the clip
    assert page.evaluate("floor.keepRange().snapped[1]") == 6.0
    page.keyboard.press("{")                                    # back to 4.45
    assert page.evaluate("floor.keepRange().snapped[1]") == 4.45
    # the picture parks on the edge frame
    assert page.evaluate("document.querySelector('#pic').paused")
    assert page.evaluate("document.querySelector('#pic').currentTime") == pytest.approx(4.45, abs=0.1)
    # arrows step a frame at the active edge (out, the last one touched)
    page.keyboard.press("ArrowLeft")
    assert page.evaluate("floor.keepRange().snapped[1]") == pytest.approx(4.45 - 1 / 30, abs=0.01)
    # ] moves the in-point to the next sentence start - PAD_HEAD, and makes it the edge
    page.keyboard.press("]")
    assert page.evaluate("floor.keepRange().snapped[0]") == 0.25
    assert page.evaluate("floor.state.keep.edge") == "in"
    page.keyboard.press("ArrowRight")
    assert page.evaluate("floor.keepRange().snapped[0]") == pytest.approx(0.25 + 1 / 30, abs=0.01)
    page.keyboard.press("[")                                    # back to the line's start
    assert page.evaluate("floor.keepRange().snapped[0]") == 0.25
    page.keyboard.press("[")                                    # no earlier line: the top
    assert page.evaluate("floor.keepRange().snapped[0]") == 0.0
    page.keyboard.press("p")
    d = wait_edl(project, lambda d: len(d.get("selects", [])) == 1)
    assert (d["selects"][0]["start"], d["selects"][0]["end"]) == (0.0, 4.42)


def test_space_plays_and_pauses_and_at_the_bands_end_watches_on_without_moving_it(page, project):
    """Karl, 2026-09-08: "space needs to be play / pause". So: space pauses a playing
    picture and plays a paused one. When playback has stopped on its own at the band's end,
    space plays on past it — the whole clip opens — and the band stays the preview: watching
    is still not keeping (I2.7 move 2). Extending is `}` or a drag of the out handle."""
    playing_at(page, 0.2)
    page.keyboard.press("Space")
    page.wait_for_function("document.querySelector('#pic').paused", timeout=3000)
    t0 = page.evaluate("document.querySelector('#pic').currentTime")
    page.keyboard.press("Space")
    page.wait_for_function("!document.querySelector('#pic').paused", timeout=3000)
    playing_at(page, t0 + 0.2)
    assert page.evaluate("floor.state.whole") is False

    preview_to(page, 1.2)                          # stops on its own at the band's end
    assert page.evaluate("document.querySelector('#pic').currentTime") < 1.6
    assert page.evaluate("floor.keepRange().snapped") == [0.0, 2.45]
    page.keyboard.press("Space")                   # not a rewind: it watches on
    playing_at(page, 2.6)                          # inside "how are you"
    assert page.evaluate("floor.state.whole") is True
    page.keyboard.press("Space")
    page.wait_for_function("document.querySelector('#pic').paused", timeout=3000)
    t = page.evaluate("document.querySelector('#pic').currentTime")
    assert 2.6 <= t < 4.0
    assert page.evaluate("floor.keepRange().snapped") == [0.0, 2.45], "watching is not keeping"
    assert "watched" not in page.locator("#left").inner_text() + page.locator("#zoomInfo").inner_text()
    page.keyboard.press("p")
    d = wait_edl(project, lambda d: len(d.get("selects", [])) == 1)
    assert d["selects"][0]["end"] == 2.45, "the preview, snapped — not the second line"


# ------------------------------------------------------- direct manipulation (I2.7)

def center(page, sel):
    page.wait_for_timeout(50)            # a frame, so the box is where the last paint put it
    b = page.locator(sel).bounding_box()
    return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2


def test_dragging_a_handle_trims_with_a_magnet_and_dragging_the_band_slides_it(page, project):
    """I2.7 move 1. The closer strip is 80 px/s at this width. The out handle dragged to
    4.5 s takes the sentence-end tick at 4.45; the in handle dragged to 1.15 s takes the
    word "there" at 1.2; the band's middle then slides the range, length kept, clamped."""
    playing_at(page, 0.3)
    paused_at(page)
    assert page.evaluate("floor.keepRange().snapped") == [0.0, 6.0]
    x, y = center(page, "#handleOut")
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x - 60, y, steps=4)
    page.mouse.move(x - 120, y, steps=4)                    # 6.0 - 1.5 = 4.5 s
    # while the pointer is down: the tick it took lights, the time reads out under the
    # handle, the picture is parked on the edge frame
    lit = page.locator("#zoomInner .snap.lit")
    assert lit.count() == 1 and lit.get_attribute("data-t") == "4.45"
    assert "sentence end" in page.locator("#zoomHint").inner_text()
    assert page.evaluate("document.querySelector('#pic').paused")
    assert page.evaluate("document.querySelector('#pic').currentTime") == pytest.approx(4.45, abs=0.1)
    page.mouse.up()
    assert page.evaluate("floor.keepRange().snapped") == [0.0, 4.45]
    assert page.locator("#zoomInner .snap.lit").count() == 0
    assert page.evaluate("floor.state.keep.edge") == "out"
    # the in handle, dragged to 1.15 s, takes the word at 1.2 and becomes the edge
    x, y = center(page, "#handleIn")
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 92, y, steps=6)
    assert page.locator("#zoomInner .snap.lit").get_attribute("data-t") == "1.2"
    page.mouse.up()
    assert page.evaluate("floor.keepRange().snapped") == [1.2, 4.45]
    assert page.evaluate("floor.state.keep.edge") == "in"
    # the band's middle slides the range: +0.5 s, then more than the clip has
    x, y = center(page, "#zoomKeep")
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 40, y, steps=4)
    assert page.evaluate("floor.keepRange().snapped") == [1.7, 4.95]
    page.mouse.move(x + 400, y, steps=4)
    page.mouse.up()
    assert page.evaluate("floor.keepRange().snapped") == [2.75, 6.0]
    page.keyboard.press("p")
    d = wait_edl(project, lambda d: len(d.get("selects", [])) == 1)
    assert (d["selects"][0]["start"], d["selects"][0]["end"]) == (2.75, 6.0)


def test_clicking_a_mark_on_the_tape_jumps_to_that_pick(page, project):
    """The synthetic bin has one pick per clip, so a second pick in CLIP_A is put in the
    page's queue by hand: the tape then shows two marks, and the one that is not this
    pick takes a click."""
    page.evaluate("""() => {
        const q = JSON.parse(JSON.stringify(floor.state.queue[0]));
        Object.assign(q, { id: 'made-up', start: 4, end: 6, preview: [4, 6], rank: 9,
                           why: 'a second pick, made up for the test' });
        floor.state.picks.push(q);
        floor.state.queue.push(q);
        floor.show(0, { autoplay: false });
    }""")
    assert page.locator("#tapeMarks span").count() == 2
    mark = page.locator("#tapeMarks span.jump")
    assert mark.count() == 1
    assert "made up for the test" in mark.get_attribute("title")
    assert "click to jump" in mark.get_attribute("title")
    mark.click()
    page.wait_for_function("floor.state.i === 3", timeout=5000)
    assert "pick 4 of 4" in page.locator("#hudPos").inner_text()
    assert page.locator("#ctxPick").inner_text().startswith("0:04.0 → 0:06.0")
    assert page.locator("#tapeMarks span.now").count() == 1
    playing_at(page, 4.2)                            # it plays from its preview, as ↵ would
    wait_edl(project, lambda d: d.get("floor", {}).get("position", {}).get("index") == 3)
    # and back: now the first pick's mark is the one that jumps
    page.locator("#tapeMarks span.jump").click()
    page.wait_for_function("floor.state.i === 0", timeout=5000)
    assert page.locator("#ctxPick").inner_text().startswith("0:00.0 → 0:06.0")


# ------------------------------------------------------------ compare takes (I2.4)

def inject_takes(page):
    """A take cluster in CLIP_A, put into the page by hand: the synthetic bin yields one
    pick per clip (every candidate inside 6 s merges, and speech takes need a shared
    theme), so the sidecar route cannot make one. The real pick is cut to 0-3 s; a second
    take 4-6 s joins this round's queue; a third, 3.2-3.8 s, was rejected in an earlier
    round and is in the picks but not the queue."""
    page.evaluate("""() => {
        const a = floor.state.queue[0];
        const b = JSON.parse(JSON.stringify(a));
        const c = JSON.parse(JSON.stringify(a));
        Object.assign(a, { end: 3, preview: [0, 3] });
        Object.assign(b, { id: 'made-up', start: 4, end: 6, preview: [4, 6], rank: 9,
                           why: 'the second attempt', witnesses: [{ kind: 'heard', start: 5, end: 5.6, at: 5,
                           text: 'goodbye', score: 0.8 }, { kind: 'felt', start: 4.5, end: 5.5, at: 5, text: '6.7 g', score: 0 }] });
        Object.assign(c, { id: 'old-take', start: 3.2, end: 3.8, preview: [3.2, 3.8], rank: 12,
                           why: 'an earlier attempt', verdict: 'reject' });
        a.take = { id: 'CLIP_A.MP4:speech:1', n: 1, of: 3, others: ['old-take', 'made-up'] };
        c.take = { id: 'CLIP_A.MP4:speech:1', n: 2, of: 3, others: [a.id, 'made-up'] };
        b.take = { id: 'CLIP_A.MP4:speech:1', n: 3, of: 3, others: [a.id, 'old-take'] };
        floor.state.picks.push(c, b);
        floor.state.queue.push(b);
        floor.show(0, { autoplay: false });
    }""")
    page.wait_for_function("document.querySelector('#pic').paused")


def survey_cards(page):
    return page.locator("#overlay[data-kind=survey] .take")


def test_T_compares_the_takes_and_P_keeps_one_and_rejects_the_rest_in_one_undo(page, project):
    """I2.4 on the floor. The panel says the pick is a take; T lays the cluster out in
    time order with a still, the range, the kind, the witness line, the felt numbers and
    any verdict; ← → and ↵ move the pass; P on a take keeps it and rejects the cluster's
    other undecided takes, one POST each, then moves on — and ⌘Z takes all of it back."""
    inject_takes(page)
    assert "take 1 of 3" in page.locator("#ctxOthers").inner_text()
    assert "T to compare" in page.locator("#ctxOthers").inner_text()
    page.keyboard.press("t")
    page.wait_for_selector("#overlay[data-kind=survey]")
    cards = survey_cards(page)
    assert cards.count() == 3
    assert "3 takes of one speech" in page.locator("#overlayBox h2").inner_text()
    first, old, second = cards.nth(0), cards.nth(1), cards.nth(2)
    assert "★" in first.inner_text() and "cursor" in first.get_attribute("class")
    assert "0:00.0–0:03.0" in first.inner_text() and "speech" in first.inner_text()
    assert "“goodbye”" in first.inner_text(), "the strongest witness's line (0.8 over 0.55)"
    assert "0:04.0–0:06.0" in second.inner_text() and "felt 6.7 g" in second.inner_text()
    assert "REJECTED · decided in an earlier round" in old.inner_text()
    assert "gone" in old.get_attribute("class")
    for k in range(3):
        src = cards.nth(k).locator("img").get_attribute("src")
        assert src.startswith("/media/poster/CLIP_A.jpg?t="), src
    # → twice lands on the earlier round's take: ↵ says why it will not go there
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    assert "cursor" in second.get_attribute("class")
    page.keyboard.press("ArrowLeft")
    assert "cursor" in old.get_attribute("class")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('#toast').textContent.includes('earlier round')")
    assert page.locator("#overlay").is_visible()
    # → then ↵ goes to the second take, as a mark click would; T there stars it
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Enter")
    page.wait_for_function("floor.state.i === 3", timeout=5000)
    assert page.locator("#overlay").is_hidden()
    assert page.locator("#ctxPick").inner_text().startswith("0:04.0 → 0:06.0")
    assert "take 3 of 3" in page.locator("#ctxOthers").inner_text()
    page.keyboard.press("t")
    page.wait_for_selector("#overlay[data-kind=survey]")
    assert "★" in survey_cards(page).nth(2).inner_text()
    assert "cursor" in survey_cards(page).nth(2).get_attribute("class")
    # a click on the first take goes back to it
    survey_cards(page).nth(0).click()
    page.wait_for_function("floor.state.i === 0", timeout=5000)
    # P on the second take from the first: the second is kept (its preview, snapped), the
    # first — undecided, in the queue — is rejected as the other take, the earlier round's
    # is left alone; then the pass moves on past both to CLIP_B
    posted: list[dict] = []
    page.on("request", lambda r: posted.append(r.post_data_json)
            if r.method == "POST" and r.url.endswith("/api/floor/verdict") else None)
    page.keyboard.press("t")
    page.wait_for_selector("#overlay[data-kind=survey]")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("p")
    d = wait_edl(project, lambda d: len(d.get("selects", [])) == 1
                 and len(d.get("floor", {}).get("verdicts", [])) == 1)
    s = d["selects"][0]
    assert s["clip"] == "CLIP_A.MP4" and (s["start"], s["end"]) == (4.0, 6.0)
    assert s["why"] == "the second attempt"
    v = d["floor"]["verdicts"][0]
    assert v["verdict"] == "reject" and (v["start"], v["end"]) == (0.0, 3.0)
    # one POST per verdict, in time order, the reject carrying its reason (selects.py
    # keeps a reject's note, not its why, so the reason is proved on the wire). The
    # request events are pumped by playwright calls, so give them a moment to land.
    for _ in range(100):
        if len(posted) >= 2:
            break
        page.wait_for_timeout(50)
    assert [(b["verdict"], b["start"], b["end"]) for b in posted] == [("pick", 4.0, 6.0), ("reject", 0.0, 3.0)]
    assert posted[1]["why"] == "other take of CLIP_A.MP4:speech:1"
    assert page.locator("#overlay").is_hidden()
    page.wait_for_function("document.querySelector('#ctxClip').textContent === 'CLIP_B'", timeout=5000)
    assert "kept take 3 of 3" in page.locator("#toast").inner_text()
    assert "rejected 1 other take" in page.locator("#toast").inner_text()
    # ⌘Z: both verdicts come back off the EDL, and the kept take is in front of you
    page.keyboard.press("Control+z")
    wait_edl(project, lambda d: d.get("selects") == [] and not d["floor"]["verdicts"])
    page.wait_for_function("floor.state.i === 3", timeout=5000)
    assert page.evaluate("floor.state.queue[0].verdict") is None
    assert page.evaluate("floor.current().verdict") is None
    assert page.locator("#ctxPick").inner_text().startswith("0:04.0 → 0:06.0")
    # X in the survey rejects the chosen take only, and the pass stays put
    page.keyboard.press("t")
    page.wait_for_selector("#overlay[data-kind=survey]")
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("ArrowLeft")
    assert "cursor" in survey_cards(page).nth(0).get_attribute("class")
    page.keyboard.press("x")
    d = wait_edl(project, lambda d: d.get("floor", {}).get("verdicts"))
    assert (d["floor"]["verdicts"][0]["start"], d["floor"]["verdicts"][0]["end"]) == (0.0, 3.0)
    assert d["selects"] == []
    page.wait_for_function("document.querySelector('#overlay[data-kind=survey] .take .t4.reject') !== null")
    assert page.evaluate("floor.state.i") == 3
    page.keyboard.press("Escape")
    assert page.locator("#overlay").is_hidden()
    # the map has the T row; a pick that is not a take says so
    page.keyboard.press("?")
    page.wait_for_selector("#overlay[data-kind=keymap]")
    assert "compare takes" in page.locator("#overlayBox").inner_text()
    page.keyboard.press("Escape")
    page.evaluate("floor.show(1, { autoplay: false })")
    assert "T to compare" not in page.locator("#ctxOthers").inner_text()
    page.keyboard.press("t")
    page.wait_for_function("document.querySelector('#toast').textContent.includes('not a take')")
    assert page.locator("#overlay").is_hidden()


def lens(page) -> tuple[float, float]:
    """The lens's left and width on the tape, in percent of the clip."""
    return tuple(page.evaluate(
        "[parseFloat(document.querySelector('#tapeLens').style.left),"
        " parseFloat(document.querySelector('#tapeLens').style.width)]"))


def lens_at(page, left: float, width: float, tol=0.3):
    """The lens is painted on the frame after a seek, so wait for it rather than for
    currentTime — under a loaded machine one frame is enough to read it early."""
    page.wait_for_function(
        f"Math.abs(parseFloat(document.querySelector('#tapeLens').style.left) - {left}) < {tol}"
        f" && Math.abs(parseFloat(document.querySelector('#tapeLens').style.width) - {width}) < {tol}",
        timeout=5000)
    assert lens(page) == pytest.approx((left, width), abs=tol)


def test_clicking_a_strip_seeks_the_playhead_and_the_lens_tracks_it(page):
    """Paused stays parked, playing stays playing; the lens on the tape is the ±8 s the
    closer strip shows. On a 6 s clip that is always the whole tape, so the clip is told
    it is 40 s long — the picture still only has 6, and every seek here stays inside them.
    The closer strip is 80 px/s with the playhead at its centre."""
    page.evaluate("floor.current().duration = 40; floor.show(0, { autoplay: false })")
    page.wait_for_function("document.querySelector('#pic').paused")
    lens_at(page, 0.0, 20.0)                                                # 0-8 of 40
    tb = page.locator("#tape").bounding_box()
    page.mouse.click(tb["x"] + tb["width"] * 3 / 40, tb["y"] + 8)          # 3.0 s
    page.wait_for_function("Math.abs(document.querySelector('#pic').currentTime - 3) < 0.1")
    assert page.evaluate("document.querySelector('#pic').paused")
    lens_at(page, 0.0, 27.5)                                                # 0-11 of 40
    assert float(page.evaluate("document.querySelector('#tapeHead').style.left")[:-1]) == pytest.approx(7.5, abs=0.3)
    zb = page.locator("#zoom").bounding_box()
    page.mouse.click(zb["x"] + zb["width"] / 2 + 80, zb["y"] + 10)          # a second right
    page.wait_for_function("Math.abs(document.querySelector('#pic').currentTime - 4) < 0.1")
    assert page.evaluate("document.querySelector('#pic').paused")
    lens_at(page, 0.0, 30.0)                                                # 0-12 of 40
    # playing: the playhead moves and the picture goes on, the lens with it
    page.keyboard.press("l")
    playing_at(page, 4.2)
    page.mouse.click(tb["x"] + tb["width"] / 40, tb["y"] + 8)               # back to 1.0 s
    page.wait_for_function("document.querySelector('#pic').currentTime < 2")
    assert not page.evaluate("document.querySelector('#pic').paused")
    playing_at(page, 1.3)
    assert 22.0 < lens(page)[1] < 26.0                                      # 0-(9..10) of 40


def tape_keep(page) -> tuple[float, float]:
    """The kept range's band on the tape: left and width in percent of the clip."""
    return tuple(page.evaluate(
        "[parseFloat(document.querySelector('#tapeKeep').style.left),"
        " parseFloat(document.querySelector('#tapeKeep').style.width)]"))


def test_the_kept_range_is_a_green_band_on_the_tape_and_the_panel_says_where_it_sits(page):
    """I2.8 1b. Karl: "it is unclear where the subclip is within the whole clip timeline".
    The tape carries the kept range as a solid band inside this pick's bracket, following
    the handles while the pointer is down; the bridge between the strips fans the lens
    out to the closer strip's full width and carries the playhead across; and the panel
    says it in words. The clip is told it is 40 s long so the numbers are not all 0 and
    100 — the picture still has 6."""
    page.evaluate("floor.current().duration = 40; floor.show(0, { autoplay: false })")
    page.wait_for_function("document.querySelector('#pic').paused")
    assert page.evaluate("floor.keepRange().snapped") == [0.0, 6.0]
    assert tape_keep(page) == pytest.approx((0.0, 15.0), abs=0.05)              # 0-6 of 40
    assert page.locator("#tapeMarks span.now").count() == 1, "the bracket stays"
    assert page.locator("#ctxWhere").inner_text() == "0:00 → 0:06 of 0:40 · 0 % in"
    assert "the clip you keep" in page.locator("#legend").inner_text()
    # the bridge: the lens's edges (0-8 of 40) fan out to the strip's full width, and the
    # playhead's line runs from its place on the tape to the strip's centre
    lens_at(page, 0.0, 20.0)
    assert page.get_attribute("#bridgeLens", "points") == "0.0,0 200.0,0 1000,16 0,16"
    assert page.get_attribute("#bridgeHead", "x1") == "0.0"
    assert page.get_attribute("#bridgeHead", "x2") == "500.0"
    page.evaluate("floor.seek(3)")
    page.wait_for_function("document.querySelector('#bridgeHead').getAttribute('x1') === '75.0'")
    assert page.get_attribute("#bridgeLens", "points") == "0.0,0 275.0,0 1000,16 0,16"
    # the out handle dragged to 4.5 s takes the tick at 4.45: the tape's band follows while
    # the pointer is still down
    x, y = center(page, "#handleOut")
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x - 60, y, steps=4)
    page.mouse.move(x - 120, y, steps=4)
    assert page.evaluate("floor.keepRange().snapped") == [0.0, 4.45]
    assert tape_keep(page) == pytest.approx((0.0, 11.12), abs=0.05)
    page.mouse.up()
    assert tape_keep(page) == pytest.approx((0.0, 11.12), abs=0.05)
    # the in handle to "there" at 1.2: the band's left edge moves and the words follow
    x, y = center(page, "#handleIn")
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 92, y, steps=6)
    page.mouse.up()
    assert page.evaluate("floor.keepRange().snapped") == [1.2, 4.45]
    assert tape_keep(page) == pytest.approx((3.0, 8.12), abs=0.05)
    assert page.locator("#ctxWhere").inner_text() == "0:01 → 0:04 of 0:40 · 3 % in"
    # the same green on both strips
    tape_bg = page.evaluate("getComputedStyle(document.querySelector('#tapeKeep')).backgroundColor")
    zoom_bg = page.evaluate("getComputedStyle(document.querySelector('#zoomKeep')).backgroundColor")
    assert tape_bg.startswith("rgba(100, 208, 138") and zoom_bg.startswith("rgba(100, 208, 138")


# --------------------------------------------------------------------- undo

def test_ctrl_z_restores_the_verdict_with_its_trim_and_note(page, project):
    preview_to(page, 1.2)
    page.keyboard.press("}")
    keep_before = page.locator("#ctxKeep").inner_text()
    page.keyboard.press("n")
    page.keyboard.type("keep the reply")
    page.keyboard.press("Enter")
    page.keyboard.press("p")
    wait_edl(project, lambda d: len(d.get("selects", [])) == 1)
    page.wait_for_function("document.querySelector('#ctxClip').textContent === 'CLIP_B'")

    page.keyboard.press("Control+z")
    wait_edl(project, lambda d: d.get("selects") == [])
    page.wait_for_function("document.querySelector('#ctxClip').textContent === 'CLIP_A'")
    assert "pick 1 of 3" in page.locator("#hudPos").inner_text()
    assert page.locator("#ctxKeep").inner_text() == keep_before
    assert page.evaluate("floor.keepRange().snapped[1]") == 4.45
    assert "keep the reply" in page.locator("#noteText").inner_text()
    assert page.evaluate("floor.current().verdict") is None
    assert "0 moments" in page.locator("#hudBin").inner_text()


def test_undo_reinstates_the_verdict_that_was_there_before(page, project):
    """A pick that was `later` and then picked goes back to `later`, not to nothing. The
    `later` moves the pass on (I2.8 1c); ⌫ brings the decided pick back with its stamp,
    and re-deciding it moves on again."""
    playing_at(page, 0.6)
    paused_at(page)
    page.keyboard.press("u")
    wait_edl(project, lambda d: d.get("floor", {}).get("verdicts"))
    page.wait_for_function("document.querySelector('#ctxClip').textContent === 'CLIP_B'")
    page.keyboard.press("Backspace")
    page.wait_for_function("document.querySelector('#ctxClip').textContent === 'CLIP_A'")
    stamped(page, "LATER")                   # revisited: the stamp is shown again
    page.keyboard.press("p")
    wait_edl(project, lambda d: len(d.get("selects", [])) == 1
             and not d["floor"]["verdicts"])
    page.wait_for_function("document.querySelector('#ctxClip').textContent === 'CLIP_B'")
    page.keyboard.press("Control+z")
    d = wait_edl(project, lambda d: d.get("selects") == [] and d["floor"]["verdicts"])
    assert d["floor"]["verdicts"][0]["verdict"] == "later"
    page.wait_for_function("document.querySelector('#ctxClip').textContent === 'CLIP_A'")
    stamped(page, "LATER")


# ------------------------------------------------------- rounds and the card

def test_three_verdicts_in_a_row_with_no_enter_land_on_the_closing_card(page, project):
    """I2.8 1c, Karl: "when I pick, reject, or later a clip, it should move on to the next
    one. Right now, I'm not sure how you move on". X, U, P — nothing else pressed — and
    the round is done."""
    for key, word in (("x", "REJECTED"), ("u", "LATER"), ("p", "PICKED")):
        page.wait_for_function("floor.state.mode === 'pass' && !floor.current().verdict", timeout=5000)
        page.keyboard.press(key)
        stamped(page, word)
    page.wait_for_selector("#overlay[data-kind=card]", timeout=5000)
    card = page.locator("#overlayBox").inner_text()
    assert "3 picks · 3 decided · 3 clips" in card
    assert "1 moment" in card and "1 later · 1 rejected" in card
    d = edl(project)
    assert len(d["selects"]) == 1 and d["selects"][0]["clip"] == "CLIP_C.MP4"
    assert {v["verdict"] for v in d["floor"]["verdicts"]} == {"reject", "later"}
    assert page.locator("#hud").inner_text().count("CAPS") == 0, "no switch to find"


def test_the_closing_card_appears_after_the_last_pick_and_plays_the_bin(page, project):
    for clip in ("CLIP_A", "CLIP_B", "CLIP_C"):
        page.wait_for_function(
            f"document.querySelector('#ctxClip').textContent === '{clip}'", timeout=5000)
        playing_at(page, 0.6)
        paused_at(page)
        page.keyboard.press("p")
        time.sleep(0.1)
    wait_edl(project, lambda d: len(d.get("selects", [])) == 3)
    page.wait_for_selector("#overlay[data-kind=card]", timeout=5000)
    card = page.locator("#overlayBox").inner_text()
    assert "Round 1 done" in card
    assert "3 moments" in card and "if strung out" in card
    assert "3 picks · 3 decided · 3 clips" in card
    assert "0 new picks" in card
    assert "Assemble · $0.60" in card
    assert "enough" not in card.lower(), "the card reports, it never judges"
    assert page.locator("#cardNext").is_disabled(), "nothing left for a next round"
    # ↵ plays the bin: every select in order, in the same picture
    page.keyboard.press("Enter")
    page.wait_for_function("floor.state.mode === 'bin'", timeout=5000)
    assert page.locator("#ctxClip").inner_text() == "CLIP_A"
    assert "playing the bin · 1 of 3" in page.locator("#ctxOthers").inner_text()
    playing_at(page, 0.3)
    page.wait_for_function("floor.state.bin && floor.state.bin.k === 1", timeout=10000)
    assert page.locator("#ctxClip").inner_text() == "CLIP_B"
    page.keyboard.press("Escape")
    page.wait_for_selector("#overlay[data-kind=card]", timeout=5000)


def test_the_position_resumes_after_a_reload(page, project, live_server):
    playing_at(page, 0.6)
    paused_at(page)
    page.keyboard.press("p")
    page.wait_for_function("document.querySelector('#ctxClip').textContent === 'CLIP_B'")
    page.keyboard.press("Enter")                     # skip B without a verdict
    page.wait_for_function("document.querySelector('#ctxClip').textContent === 'CLIP_C'")
    wait_edl(project, lambda d: d.get("floor", {}).get("position", {}).get("index") == 1)

    page.reload()
    page.wait_for_function("window.floor && floor.state.queue.length > 0", timeout=15000)
    assert page.evaluate("floor.state.queue.length") == 2, "A is decided and gone"
    assert page.locator("#ctxClip").inner_text() == "CLIP_C"
    assert "pick 2 of 2" in page.locator("#hudPos").inner_text()
    assert "1 moment" in page.locator("#hudBin").inner_text()


def test_by_clip_switches_the_order_and_starts_a_new_round(page, project):
    page.evaluate("floor.show(2)")
    page.keyboard.press("Enter")                     # past the last pick: the card
    page.wait_for_selector("#overlay[data-kind=card]", timeout=5000)
    page.keyboard.press("c")
    page.wait_for_function("floor.state.round === 2", timeout=5000)
    assert page.evaluate("floor.state.order") == "clip"
    d = wait_edl(project, lambda d: d.get("floor", {}).get("position", {}).get("round") == 2)
    assert d["floor"]["position"]["order"] == "clip"
    assert "round 2" in page.locator("#hudPos").inner_text()


# ---------------------------------------------------------- evidence and more

def test_the_evidence_drawer_opens_and_any_verdict_closes_it(page, project):
    page.keyboard.press("e")
    page.wait_for_selector("#overlay[data-kind=evidence]")
    text = page.locator("#overlayBox").inner_text()
    assert "Evidence" in text and "HEARD" in text and "goodbye" in text
    page.keyboard.press("e")
    assert page.locator("#overlay").is_hidden()
    page.keyboard.press("?")
    page.wait_for_selector("#overlay[data-kind=keymap]")
    keymap = page.locator("#overlayBox").inner_text()
    assert "skip for now" in keymap and "auto-advance" not in keymap and "Caps" not in keymap
    page.keyboard.press("Escape")
    assert page.locator("#overlay").is_hidden()
    page.keyboard.press("e")
    page.wait_for_selector("#overlay[data-kind=evidence]")
    page.keyboard.press("x")
    wait_edl(project, lambda d: d.get("floor", {}).get("verdicts"))
    assert page.locator("#overlay").is_hidden(), "a verdict closes the drawer"


def test_more_opens_the_whole_clip_and_says_what_is_not_built(page):
    page.keyboard.press(".")
    page.wait_for_selector("#overlay[data-kind=more]")
    page.keyboard.press("l")
    assert page.locator("#toast").inner_text() == "not on the floor yet"
    assert page.locator("#overlay").is_hidden()
    page.evaluate("floor.current().preview[1] = 1.0; floor.show(0)")
    page.wait_for_function("document.querySelector('#pic').paused", timeout=10000)
    page.keyboard.press(".")
    page.keyboard.press("o")
    assert page.evaluate("floor.state.whole") is True
    playing_at(page, 1.5)                            # past the preview end, still going


# ------------------------------------------------ what was looked at (I7.2)

LOOKED = {"interval_s": 2, "frames": [0, 2, 4], "sheets": 1, "prompt_version": 2}


def inject_looked(page, looked=LOOKED, frames=(2, 4),
                  why="a skier leaves the lip at 0:02.0 and lands at 0:04.0"):
    """The look pass's word on the synthetic bin, put into the page by hand: the bin has
    no visual sidecar, so `/api/picks` cannot say it. CLIP_A was read every 2 s (three
    frames), CLIP_B never; CLIP_A's pick gains a `seen` witness citing two of the frames
    and a reason that names two times."""
    page.evaluate("""([looked, frames, why]) => {
        floor.state.looked = { 'CLIP_A.MP4': looked, 'CLIP_B.MP4': null };
        const p = floor.state.queue[0];
        p.why = why;
        p.witnesses = p.witnesses.filter((w) => w.kind !== 'seen');
        p.witnesses.push({ kind: 'seen', start: 1.0, end: 5.0, at: 2.0, text: 'a skier leaves the lip',
                           state: 'claimed', event_kind: 'jump', score: 0.7, frames, confidence: 'high' });
        floor.show(0, { autoplay: false });
    }""", [looked, list(frames), why])
    page.wait_for_function("document.querySelector('#pic').paused")


def looked_ticks(page) -> list[tuple[float, str]]:
    return [tuple(x) for x in page.evaluate(
        "[...document.querySelectorAll('#tapeLooked span')]"
        ".map((s) => [parseFloat(s.style.left), s.className])")]


def lit_ticks(page) -> list[str]:
    return page.evaluate("[...document.querySelectorAll('#tapeLooked span.lit')].map((s) => s.dataset.t)")


def test_the_tape_shows_what_was_looked_at_and_every_time_is_a_link(page):
    """I7.2, Karl's third report: "indicate how much of the clip was indexed by keyframe …
    the indexed keyframes should be referenced in the why and timestamps should be
    jumpable". The tape carries a tick per sampled frame, the two a witness cites brighter;
    the label counts them; WHY and the witness line turn every time into a chip that parks
    the picture on that frame and lights its tick; the drawer says how many frames sit
    under this pick's window. A clip never looked at says so; a wire that does not say
    leaves the tape as it was."""
    # before anything is injected: the old contract — no ticks, no label, no legend line
    assert page.locator("#tapeLooked span").count() == 0
    assert "LOOKED" not in page.locator("#tapeLbl").inner_text()
    assert page.locator("#legendLooked").is_hidden()
    assert page.locator("#why .chip").count() == 0
    inject_looked(page)
    # three ticks at 0, 2 and 4 of 6 s; the ones a witness cites are marked
    assert looked_ticks(page) == [(0.0, ""), (33.33, "cited"), (66.67, "cited")]
    assert "LOOKED · 3 frames · every 2 s · 1 sheet" in page.locator("#tapeLbl").inner_text()
    assert page.locator("#legendLooked").is_visible()
    assert "look pass" in page.locator("#legend").inner_text()
    # WHY: the two times are chips
    why_chips = page.locator("#why .chip")
    assert why_chips.count() == 2
    assert [why_chips.nth(k).inner_text() for k in range(2)] == ["0:02.0", "0:04.0"]
    # the witness line: its frames after the text, and how sure the pass was
    seal = page.locator("#seals .seal", has_text="SEEN")
    assert seal.count() == 1
    assert "frames" in seal.inner_text() and "high confidence" in seal.inner_text()
    frame_chips = seal.locator(".chip")
    assert [frame_chips.nth(k).get_attribute("data-t") for k in range(frame_chips.count())] == ["2", "2", "4"]
    assert frame_chips.nth(1).inner_text() == "0:02" and frame_chips.nth(2).inner_text() == "0:04"
    # a chip clicked: parked on that frame, paused, its tick lit
    why_chips.nth(1).click()
    page.wait_for_function("Math.abs(document.querySelector('#pic').currentTime - 4) < 0.1")
    assert page.evaluate("document.querySelector('#pic').paused")
    assert lit_ticks(page) == ["4"]
    assert page.evaluate("floor.state.whole") is False, "4 s is inside the preview"
    # hovering a chip lights its tick too, and unlights it when the pointer leaves
    why_chips.nth(0).hover()
    assert sorted(lit_ticks(page)) == ["2", "4"]
    page.mouse.move(5, 5)
    assert lit_ticks(page) == ["4"]
    # the evidence drawer: the same chips, and how much of this window was looked at —
    # the frames at 0, 2 and 4 of the three the 2 s interval puts in 0–6
    page.keyboard.press("e")
    page.wait_for_selector("#overlay[data-kind=evidence]")
    cover = page.locator("#evCover")
    assert cover.inner_text().startswith("this window: 3 of 3 frames looked at · every 2 s")
    assert cover.locator(".chip").count() == 3
    assert page.locator("#overlayBox .chip").count() >= 3 + 2 + 3   # cover + WHY + the witness's
    # a chip in the drawer closes it and parks the picture, so the frame can be seen
    cover.locator(".chip").nth(1).click()
    assert page.locator("#overlay").is_hidden()
    page.wait_for_function("Math.abs(document.querySelector('#pic').currentTime - 2) < 0.1")
    assert lit_ticks(page) == ["2"]
    # a window the grid reaches but no sampled frame does, and one the grid skips: the
    # drawer says nothing is under the claim either way
    assert page.evaluate("floor.coverageLine({ clip: 'CLIP_A.MP4', start: 5.5, end: 6.5 })") \
        == "this window: 0 of 1 frames looked at — nothing under the claim"
    assert page.evaluate("floor.coverageLine({ clip: 'CLIP_A.MP4', start: 2.5, end: 3.5 })").startswith(
        "this window: no frame falls in it")
    # CLIP_B was never looked at: the tape and the drawer say so
    page.evaluate("floor.show(1, { autoplay: false })")
    assert page.locator("#tapeLooked span").count() == 0
    assert "not looked at yet" in page.locator("#tapeLbl").inner_text()
    assert page.locator("#legendLooked").is_hidden()
    page.keyboard.press("e")
    page.wait_for_selector("#overlay[data-kind=evidence]")
    assert "this window: not looked at yet" in page.locator("#evCover").inner_text()
    page.keyboard.press("Escape")
    # the map says a time is a link
    page.keyboard.press("?")
    page.wait_for_selector("#overlay[data-kind=keymap]")
    assert "click it to park the picture" in page.locator("#overlayBox").inner_text()


# ----------------------------------------------------------------- dictation

def test_holding_V_records_posts_and_falls_back_to_N_on_501(page, monkeypatch):
    """I2.5 when dictation is unavailable: the recording is made and sent; the 501 says
    only that the recogniser is not installed, and — the recogniser being genuinely absent
    (I2.8: the one case that may open the editor) — the typed note takes over. Dictation
    is real on this tree (M4), so its absence is simulated — the server runs in this
    process. A reload then says so in the hint before any key is held."""
    from roughcut import dictate
    monkeypatch.setattr(dictate, "available", lambda: False)
    hits: list[int] = []
    page.on("response", lambda r: hits.append(r.status) if "/api/dictate" in r.url else None)
    playing_at(page, 0.3)
    page.keyboard.down("v")
    page.wait_for_function("document.querySelector('#pic').volume < 0.2", timeout=3000)
    assert "listening" in page.locator("#dictState").inner_text()
    page.wait_for_timeout(1000)
    page.keyboard.up("v")
    page.wait_for_function("document.querySelector('#pic').volume > 0.9", timeout=3000)
    page.wait_for_function(
        "document.querySelector('#toast').textContent.includes('dictation is not installed on the server')",
        timeout=10000)
    assert page.locator("#toast").inner_text() == "dictation is not installed on the server"
    assert hits == [501], hits
    page.wait_for_selector("#noteEdit:visible")
    hint = page.locator("#dictHint")
    assert "not installed" in hint.inner_text() and "N" in hint.inner_text()
    assert hint.get_attribute("data-mic") == "absent"
    page.keyboard.type("typed instead")
    page.keyboard.press("Enter")
    assert "typed instead" in page.locator("#noteText").inner_text()
    # from a fresh load /api/picks says `dictation: false`: the hint says so at boot, and
    # V goes straight to the editor without recording anything
    page.reload()
    page.wait_for_function("window.floor && floor.state.queue.length > 0", timeout=15000)
    assert page.evaluate("floor.state.dictation") is False
    assert "dictation is not installed on the server" in page.locator("#dictHint").inner_text()
    page.keyboard.down("v")
    page.wait_for_selector("#noteEdit:visible")
    page.keyboard.up("v")
    assert hits == [501], "no second recording was sent"


MIC_STUB = """
  // The microphone, scripted: the permission the browser reports, a getUserMedia whose
  // answer the test controls, and a MediaRecorder that hands back a blob on stop.
  window.__mic = { calls: 0, grant: null, refuse: null, recs: 0 };
  navigator.permissions.query = async () => ({ state: %(perm)s, onchange: null });
  navigator.mediaDevices.getUserMedia = () => new Promise((res, rej) => {
    window.__mic.calls += 1;
    const stream = { getTracks: () => [{ readyState: 'live', stop() { this.readyState = 'ended'; } }] };
    window.__mic.grant = () => res(stream);
    window.__mic.refuse = () => rej(new DOMException('Permission denied', 'NotAllowedError'));
    if (%(answer)s === 'refuse') window.__mic.refuse();
    if (%(answer)s === 'grant') window.__mic.grant();
  });
  class FakeRecorder {
    constructor() { this.state = 'inactive'; window.__mic.recs += 1; }
    static isTypeSupported() { return true; }
    start() { this.state = 'recording'; }
    stop() {
      this.state = 'inactive';
      if (this.ondataavailable) this.ondataavailable({ data: new Blob([new Uint8Array(64)], { type: 'audio/webm' }) });
      if (this.onstop) this.onstop();
    }
  }
  window.MediaRecorder = FakeRecorder;
"""


def mic_page(page, perm: str, answer: str):
    """Reload the floor with the microphone scripted: `perm` is what the permission query
    says at boot; `answer` is what getUserMedia does — 'refuse' at once, 'grant' at once,
    or 'wait' for the test to call window.__mic.grant() (the permission prompt, up)."""
    page.add_init_script(MIC_STUB % {"perm": json.dumps(perm), "answer": json.dumps(answer)})
    page.reload()
    page.wait_for_function("window.floor && floor.state.queue.length > 0", timeout=15000)


def test_a_blocked_microphone_is_named_and_never_opens_the_editor(page):
    """I2.8 1a. The hint says what V will do before it is held; a refused microphone is
    said by name, the note is left alone, and the next hold goes straight to the message
    without asking the browser again."""
    mic_page(page, "prompt", "refuse")
    hint = page.locator("#dictHint")
    assert "V will ask for the microphone the first time" in hint.inner_text()
    assert hint.get_attribute("data-mic") == "prompt"
    playing_at(page, 0.3)
    page.keyboard.down("v")
    page.wait_for_function(
        "document.querySelector('#toast').textContent.includes('microphone blocked for this site')",
        timeout=5000)
    page.keyboard.up("v")
    assert "allow it in the address bar" in page.locator("#toast").inner_text()
    assert page.locator("#noteEdit").is_hidden(), "an error never opens the typed editor"
    assert page.locator("#dictState").inner_text() == ""
    assert "microphone blocked for this site" in hint.inner_text()
    assert hint.get_attribute("data-mic") == "denied"
    assert page.evaluate("document.querySelector('#pic').volume") > 0.9
    assert page.evaluate("window.__mic.calls") == 1
    page.keyboard.down("v")
    page.keyboard.up("v")
    page.wait_for_function(
        "document.querySelector('#toast').textContent.includes('microphone blocked for this site')")
    assert page.evaluate("window.__mic.calls") == 1, "denied is remembered; nobody is asked twice"
    assert page.locator("#noteEdit").is_hidden()


def test_a_microphone_granted_after_V_was_released_is_kept_and_the_next_hold_records(page, project):
    """I2.8 1a, the Chrome path: the first hold raises the permission prompt, V comes up
    while it is up, and the stream lands afterwards. It is kept and announced, the
    editor stays closed, and the next hold records at once on the same stream — one
    getUserMedia for the page — and its text lands as the note. A hold under 0.4 s is
    dropped and said."""
    mic_page(page, "prompt", "wait")
    playing_at(page, 0.3)
    page.keyboard.down("v")
    page.wait_for_function("document.querySelector('#pic').volume < 0.2", timeout=3000)
    assert "listening" in page.locator("#dictState").inner_text()
    page.keyboard.up("v")                              # the prompt is still up
    page.wait_for_function("document.querySelector('#pic').volume > 0.9", timeout=3000)
    assert page.locator("#dictState").inner_text() == ""
    page.evaluate("window.__mic.grant()")
    page.wait_for_function(
        "document.querySelector('#toast').textContent === 'microphone ready — hold V and speak'",
        timeout=5000)
    assert page.locator("#noteEdit").is_hidden()
    assert page.evaluate("window.__mic.recs") == 0, "nothing was recorded from a released key"
    assert page.locator("#dictHint").get_attribute("data-mic") == "granted"
    assert "ducks while you speak" in page.locator("#dictHint").inner_text()
    # too brief: dropped, said, nothing posted
    hits: list[int] = []
    page.on("request", lambda r: hits.append(r.method) if "/api/dictate" in r.url else None)
    page.keyboard.down("v")
    page.keyboard.up("v")
    page.wait_for_function(
        "document.querySelector('#toast').textContent.includes('held too briefly')", timeout=5000)
    assert page.evaluate("window.__mic.recs") == 1
    assert hits == []
    # the second hold records on the stream already open and its text lands
    page.evaluate("""() => {
        const real = window.fetch.bind(window);
        window.fetch = (url, opts) => url === '/api/dictate'
            ? Promise.resolve(new Response(JSON.stringify({text: 'landed on his back', latency_ms: 600}),
                                           {status: 200, headers: {'content-type': 'application/json'}}))
            : real(url, opts);
    }""")
    page.keyboard.down("v")
    page.wait_for_function("document.querySelector('#dictState').textContent.includes('listening')")
    page.wait_for_timeout(600)
    page.keyboard.up("v")
    page.wait_for_function(
        "document.querySelector('#noteText').textContent.includes('landed on his back')", timeout=5000)
    assert "landed" in page.locator("#dictState").inner_text()
    assert page.evaluate("window.__mic.calls") == 1, "one stream for the page — never re-asked"
    assert page.evaluate("window.__mic.recs") == 2
    assert page.evaluate("floor.mic.stream !== null"), "the stream stays open for the next note"


def test_a_dictated_note_lands_in_the_slot_and_on_a_decided_pick(page, project):
    """The success path, with the recogniser's answer scripted in the page — the real
    one is M4's — and a decided pick, so the note goes straight to the EDL. The `later`
    moves on; ⌫ comes back to the decided pick."""
    playing_at(page, 0.6)
    paused_at(page)
    page.keyboard.press("u")
    wait_edl(project, lambda d: d.get("floor", {}).get("verdicts"))
    page.wait_for_function("document.querySelector('#ctxClip').textContent === 'CLIP_B'")
    page.keyboard.press("Backspace")
    page.wait_for_function("document.querySelector('#ctxClip').textContent === 'CLIP_A'")
    stamped(page, "LATER")                   # the page knows the pick is decided
    page.evaluate("""() => {
        const real = window.fetch.bind(window);
        window.fetch = (url, opts) => url === '/api/dictate'
            ? Promise.resolve(new Response(JSON.stringify({text: 'come back to this one', latency_ms: 800, model: 'stub'}),
                                           {status: 200, headers: {'content-type': 'application/json'}}))
            : real(url, opts);
        return floor.dictSend(new Blob([new Uint8Array(16)], {type: 'audio/webm'}));
    }""")
    page.wait_for_function(
        "document.querySelector('#noteText').textContent.includes('come back to this one')")
    assert "0.8 s" in page.locator("#noteMeta").inner_text()
    d = wait_edl(project, lambda d: d["floor"]["verdicts"][0].get("note") == "come back to this one")
    assert d["floor"]["verdicts"][0]["verdict"] == "later"
