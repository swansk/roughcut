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
    # the tape marks this clip's picks, the current one outlined
    assert page.locator("#tapeMarks span.now").count() == 1
    assert page.locator("#tapeTrace path").count() == 0, "no felt witness, no trace"
    # and it is playing, from the top of the preview
    playing_at(page, 0.3)


def test_no_buttons_in_the_flow(page):
    assert page.locator("#app button").count() == 0


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
    page.evaluate("floor.setAuto(true)")
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
    # without auto-advance the stamped pick stays; ↵ moves on
    assert page.locator("#ctxClip").inner_text() == "CLIP_A"
    page.keyboard.press("Enter")
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


def test_holding_space_keeps_watching_but_never_moves_the_band(page, project):
    """I2.7 move 2, end to end: the preview stops on its own, space holds it open, and
    the band stays the preview — watching is not keeping. Karl's open question (should
    hold-space extend the band visibly?) is answered "never" here; extending is `}` or a
    drag of the out handle."""
    preview_to(page, 1.2)
    assert page.evaluate("document.querySelector('#pic').currentTime") < 1.6
    assert page.evaluate("floor.keepRange().snapped") == [0.0, 2.45]
    page.keyboard.down("Space")
    playing_at(page, 2.6)                          # inside "how are you"
    page.keyboard.up("Space")
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


def test_clicking_a_strip_seeks_the_playhead(page):
    """Paused stays parked, playing stays playing. The tape is the whole 6 s clip; the
    closer strip is 80 px/s with the playhead at its centre."""
    page.evaluate("floor.show(0, { autoplay: false })")
    page.wait_for_function("document.querySelector('#pic').paused")
    tb = page.locator("#tape").bounding_box()
    page.mouse.click(tb["x"] + tb["width"] * 0.5, tb["y"] + 8)             # 3.0 s
    page.wait_for_function("Math.abs(document.querySelector('#pic').currentTime - 3) < 0.1")
    assert page.evaluate("document.querySelector('#pic').paused")
    zb = page.locator("#zoom").bounding_box()
    page.mouse.click(zb["x"] + zb["width"] / 2 + 80, zb["y"] + 10)          # a second right
    page.wait_for_function("Math.abs(document.querySelector('#pic').currentTime - 4) < 0.1")
    assert page.evaluate("document.querySelector('#pic').paused")
    # playing: the playhead moves and the picture goes on
    page.keyboard.press("l")
    playing_at(page, 4.2)
    page.mouse.click(tb["x"] + tb["width"] / 6, tb["y"] + 8)                # back to 1.0 s
    page.wait_for_function("document.querySelector('#pic').currentTime < 2")
    assert not page.evaluate("document.querySelector('#pic').paused")
    playing_at(page, 1.3)


# --------------------------------------------------------------------- undo

def test_ctrl_z_restores_the_verdict_with_its_trim_and_note(page, project):
    page.evaluate("floor.setAuto(true)")
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
    """A pick that was `later` and then picked goes back to `later`, not to nothing."""
    playing_at(page, 0.6)
    paused_at(page)
    page.keyboard.press("u")
    wait_edl(project, lambda d: d.get("floor", {}).get("verdicts"))
    stamped(page, "LATER")
    page.keyboard.press("p")
    wait_edl(project, lambda d: len(d.get("selects", [])) == 1
             and not d["floor"]["verdicts"])
    stamped(page, "PICKED")
    page.keyboard.press("Control+z")
    d = wait_edl(project, lambda d: d.get("selects") == [] and d["floor"]["verdicts"])
    assert d["floor"]["verdicts"][0]["verdict"] == "later"
    stamped(page, "LATER")


# ------------------------------------------------------- rounds and the card

def test_the_closing_card_appears_after_the_last_pick_and_plays_the_bin(page, project):
    page.evaluate("floor.setAuto(true)")
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
    page.evaluate("floor.setAuto(true)")
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
    assert "auto-advance" in page.locator("#overlayBox").inner_text()
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


# ----------------------------------------------------------------- dictation

def test_holding_V_records_posts_and_falls_back_to_N_on_501(page, monkeypatch):
    """I2.5 when dictation is unavailable: the recording is made and sent; the 501 is
    said once and the typed note takes over. Dictation is real on this tree (M4), so its
    absence is simulated — the server runs in this process."""
    from roughcut import dictate
    monkeypatch.setattr(dictate, "available", lambda: False)
    hits: list[int] = []
    page.on("response", lambda r: hits.append(r.status) if "/api/dictate" in r.url else None)
    playing_at(page, 0.3)
    page.keyboard.down("v")
    page.wait_for_function("document.querySelector('#pic').volume < 0.2", timeout=3000)
    assert "listening" in page.locator("#dictState").inner_text()
    page.wait_for_timeout(700)
    page.keyboard.up("v")
    page.wait_for_function("document.querySelector('#pic').volume > 0.9", timeout=3000)
    page.wait_for_function(
        "document.querySelector('#toast').textContent.includes('dictation not built yet')",
        timeout=10000)
    assert "N to type" in page.locator("#toast").inner_text()
    assert hits == [501], hits
    page.wait_for_selector("#noteEdit:visible")
    page.keyboard.type("typed instead")
    page.keyboard.press("Enter")
    assert "typed instead" in page.locator("#noteText").inner_text()


def test_a_dictated_note_lands_in_the_slot_and_on_a_decided_pick(page, project):
    """The success path, with the recogniser's answer scripted in the page — the real
    one is M4's — and a decided pick, so the note goes straight to the EDL."""
    playing_at(page, 0.6)
    paused_at(page)
    page.keyboard.press("u")
    wait_edl(project, lambda d: d.get("floor", {}).get("verdicts"))
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
