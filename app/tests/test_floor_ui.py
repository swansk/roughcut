"""The pass, driven in a real browser (docs/INTAKE.md M2).

Everything that matters on the floor lives in the browser's media stack and in the
JavaScript: that the preview plays from the moment, that the kept range is what was
watched and snaps to the sentence, that one key writes a verdict to the EDL, that undo
puts it back. So, like test_ui_flow.py, this drives Chromium against the live server
and asserts on the EDL on disk.

The synthetic bin (conftest.py) yields one pick per clip: the two candidates at 0.5 and
5.0 merge (2.0 s apart, under MERGE_GAP_S), so each pick is 0-6 s and previews whole.
The transcript is "hello there" 0.5-2.0, "how are you" 2.4-4.0, "goodbye" 5.0-5.6, so
an end watched inside the first line snaps to 2.45 (end + PAD_TAIL).

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
    # the zoomed strip carries words (they fit at 80 px/s) and snap ticks at line ends
    assert page.locator("#zoomInner .w").count() == 6
    assert page.locator("#zoomInner .snap:not(.word)").count() == 3
    # the tape marks this clip's picks, the current one outlined
    assert page.locator("#tapeMarks span.now").count() == 1
    assert page.locator("#tapeTrace path").count() == 0, "no felt witness, no trace"
    # and it is playing, from the top of the preview
    playing_at(page, 0.3)


def test_no_buttons_in_the_flow(page):
    assert page.locator("#app button").count() == 0


# --------------------------------------------------------------- verdicts

def test_P_keeps_what_you_watched_snapped_to_the_sentence_and_advances(page, project):
    page.evaluate("floor.setAuto(true)")
    playing_at(page, 0.8)
    t = paused_at(page)
    assert 0.5 < t < 2.0, f"expected to pause inside 'hello there', got {t}"
    # the margin says what will be kept before the key is pressed
    assert "will snap" in page.locator("#ctxSnap").inner_text()
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
    playing_at(page, 0.8)
    t = paused_at(page)
    assert t < 2.0
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


def test_holding_space_keeps_watching_past_the_preview(page, project):
    """Decision 1 end to end: the preview stops on its own, space holds it open, and
    what was watched is what is kept."""
    page.evaluate("floor.current().preview[1] = 1.2; floor.show(0)")
    page.wait_for_function(
        "document.querySelector('#pic').paused && document.querySelector('#pic').currentTime >= 1.1",
        timeout=10000)
    assert page.evaluate("document.querySelector('#pic').currentTime") < 1.6
    page.keyboard.down("Space")
    playing_at(page, 2.6)                          # inside "how are you"
    page.keyboard.up("Space")
    page.wait_for_function("document.querySelector('#pic').paused", timeout=3000)
    t = page.evaluate("document.querySelector('#pic').currentTime")
    assert 2.6 <= t < 4.0
    page.keyboard.press("p")
    d = wait_edl(project, lambda d: len(d.get("selects", [])) == 1)
    assert d["selects"][0]["end"] == 4.45, "watched into the second line: snapped to its end"


# --------------------------------------------------------------------- undo

def test_ctrl_z_restores_the_verdict_with_its_trim_and_note(page, project):
    page.evaluate("floor.setAuto(true)")
    playing_at(page, 0.8)
    paused_at(page)
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
