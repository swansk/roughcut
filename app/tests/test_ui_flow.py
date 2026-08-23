"""End-to-end UI tests — a real browser driving the real app.

The API tests above prove the endpoints behave. They cannot prove the thing that
actually matters here, which is whether *using* the board works: that trimming
updates the total, that undo restores exactly, that the boundary warnings track
reality, that a preview can seek. Those live in the JavaScript and in the browser's
media stack, and the only honest way to check them is to drive a browser.

Skipped, not failed, when playwright is absent — the API suite stays runnable
everywhere, and this layer is opt-in:

    uv run --with pytest --with fastapi --with uvicorn --with httpx \
        --with playwright pytest app/tests -q
    # first time only:
    uv run --with playwright playwright install chromium
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
    """The real uvicorn server on a real port, against the synthetic project."""
    import uvicorn
    import server

    original = project["edl"].read_text(encoding="utf-8")
    server.configure(project["edl"], project["footage"], project["sidecars"],
                     project["work"], proxies=False, assets=project["assets"])
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
    # The board autosaves now — an edit reaches the EDL without anyone pressing a
    # button — so each test has to start from the seeded file rather than from
    # whatever the previous test left behind.
    seed = json.dumps({
        "variant": "T", "title": "test cut", "orient": "none", "story": "",
        "target_s": [5, 20],
        "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
                     {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}],
    }, indent=1)
    Path(project["edl"]).write_text(seed, encoding="utf-8")

    with sync_playwright() as pw:
        # The monitor plays with sound from a click or a key; headless Chromium's
        # autoplay policy would otherwise refuse the play() that follows a keypress.
        browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        pg = browser.new_page(viewport={"width": 1280, "height": 900})
        pg.goto(live_server)
        pg.wait_for_selector(".seg")
        yield pg
        browser.close()


def total_text(page) -> str:
    return page.locator("#total").inner_text()


def test_board_renders_the_timeline(page):
    assert page.locator(".seg").count() == 2
    assert "test cut" in page.locator("#title").inner_text()
    assert page.locator("#total").inner_text() == "0:04.0"   # (3.0-1.0) + (2.0-0.0)


def test_preview_video_loads_and_can_seek(page):
    """The whole latency argument for proxies rests on this working."""
    page.wait_for_function(
        "document.querySelector('.seg video').readyState >= 1", timeout=15000)
    info = page.evaluate("""() => {
        const v = document.querySelector('.seg video');
        return {dur: v.duration, w: v.videoWidth, h: v.videoHeight};
    }""")
    assert info["dur"] == pytest.approx(6.0, abs=0.3)
    # 320x180 source is below the 1280 proxy cap, so it is passed through unscaled —
    # the proxy step must never upscale.
    assert (info["w"], info["h"]) == (320, 180)

    seeked = page.evaluate("""() => new Promise(res => {
        const v = document.querySelector('.seg video');
        v.onseeked = () => res(v.currentTime);
        v.currentTime = 4.0;
        setTimeout(() => res(-1), 5000);
    })""")
    assert seeked == pytest.approx(4.0, abs=0.5), "seeking failed — range serving broken"


def test_trim_buttons_change_duration_and_are_undoable(page):
    before = total_text(page)
    page.locator(".seg").first.locator("button", has_text="+").nth(1).click()  # out +0.25
    assert total_text(page) != before
    page.locator("#undo").click()
    assert total_text(page) == before


def test_keyboard_trim_matches_button_trim(page):
    page.locator(".seg").first.click()
    before = total_text(page)
    page.keyboard.press("}")                       # extend out by 0.25
    after_key = total_text(page)
    assert after_key != before
    page.locator("#undo").click()
    assert total_text(page) == before


def test_boundary_warning_appears_and_snap_clears_it(page):
    """The defect Karl flagged, surfaced live and then fixed by the tool."""
    warned = page.locator(".seg", has_text="⚠").count()
    assert warned >= 1, "seeded EDL cuts mid-utterance; warning should show"
    page.locator("#snap").click()
    page.wait_for_function(
        "document.querySelectorAll('.seg').length && "
        "![...document.querySelectorAll('.seg')].some(s => s.innerHTML.includes('⚠'))",
        timeout=15000)
    assert page.locator(".seg", has_text="⚠").count() == 0


def test_undo_restores_exact_state_after_snap(page):
    before = page.evaluate("JSON.stringify(segs)")
    page.locator("#snap").click()
    page.wait_for_function(f"JSON.stringify(segs) !== {json.dumps(before)}",
                           timeout=15000)
    page.locator("#undo").click()
    assert page.evaluate("JSON.stringify(segs)") == before


def test_remove_and_reorder_change_the_timeline(page):
    assert page.locator(".seg").count() == 2
    first_clip = page.locator(".seg").first.locator(".clip").inner_text()
    page.locator(".seg").first.locator("button", has_text="remove").click()
    assert page.locator(".seg").count() == 1
    assert page.locator(".seg").first.locator(".clip").inner_text() != first_clip


def test_library_insert_adds_a_shot(page):
    before = page.locator(".seg").count()
    page.locator("#library .cand").first.click()
    assert page.locator(".seg").count() == before + 1


def test_edits_reach_the_disk_without_being_asked(page, project):
    """Karl: "I start the project and create some cuts - but then it resets the cut
    board as soon as I refresh the page." The working edit lived in the browser and
    only a Save button wrote it, so a refresh threw the work away."""
    page.locator("#story").fill("the milk is the running joke")
    page.locator(".seg").first.click()
    page.keyboard.press("}")                       # extend the out point
    page.wait_for_function(
        "document.querySelector('#saveState').textContent.startsWith('saved')",
        timeout=8000)

    on_disk = json.loads(Path(project["edl"]).read_text(encoding="utf-8"))
    assert on_disk["story"] == "the milk is the running joke"
    assert on_disk["segments"][0]["out"] == 3.25


def test_the_cut_survives_a_reload(page):
    """The whole point: what is on screen after F5 is what you left."""
    page.locator(".seg").first.locator("button", has_text="remove").click()
    page.wait_for_function(
        "document.querySelector('#saveState').textContent.startsWith('saved')",
        timeout=8000)
    shape = "JSON.stringify(segs.map(s => [s.clip, s.in, s.out]))"
    before = page.evaluate(shape)

    page.reload()
    page.wait_for_selector(".seg")
    assert page.locator(".seg").count() == 1
    assert page.evaluate(shape) == before


def test_ask_shows_a_proposal_that_can_be_accepted_or_discarded(page):
    """The interject loop, end to end in the browser, against a scripted backend.

    The server runs in this process, so installing a backend here reaches it.
    """
    from roughcut import config, inference

    class Scripted:
        name = "scripted"

        def complete(self, request):
            text = json.dumps({
                "segments": [{"clip": "CLIP_C.MP4", "in": 0.5, "out": 4.0,
                              "why": "brought in per the note"}],
                "notes": "replaced the opening with the unused clip"})
            model = config.model_for(request.role)
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend="scripted", model=model,
                                    projected_usd=0.0001, latency_ms=1, raw=text)

    inference.set_backend(Scripted())
    inference.reset_spend()
    try:
        before = page.evaluate("JSON.stringify(segs)")
        page.locator("#note").fill("use the clip that isn't in the cut")
        page.locator("#ask").click()
        page.wait_for_selector("#proposal:visible", timeout=30000)

        assert "replaced the opening" in page.locator("#proposalNotes").inner_text()
        assert "CLIP_C" in page.locator("#proposalDiff").inner_text()
        # a proposal is not an edit until accepted
        assert page.evaluate("JSON.stringify(segs)") == before

        page.locator("#rejectProposal").click()
        assert not page.locator("#proposal").is_visible()
        assert page.evaluate("JSON.stringify(segs)") == before

        page.locator("#ask").click()
        page.wait_for_selector("#proposal:visible", timeout=30000)
        page.locator("#acceptProposal").click()
        assert page.locator(".seg").count() == 1
        assert "CLIP_C" in page.locator(".seg").first.inner_text()

        page.locator("#undo").click()          # and it stays undoable
        assert page.evaluate("JSON.stringify(segs)") == before
    finally:
        inference.set_backend(None)


def test_an_empty_timeline_offers_a_first_cut_and_gets_one(page):
    """The state every new project starts in. It used to be unreachable — you could
    not open the board without an EDL — and now it is where everyone begins, so it
    has to explain itself and lead somewhere."""
    from roughcut import config, inference

    class Scripted:
        name = "scripted"
        seen: list = []

        def complete(self, request):
            Scripted.seen.append(request)
            text = json.dumps({
                "segments": [{"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0,
                              "why": "opens on the greeting"},
                             {"clip": "CLIP_B.MP4", "in": 2.4, "out": 4.0,
                              "why": "the reply"}],
                "notes": "read it as a conversation"})
            model = config.model_for(request.role)
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend="scripted", model=model,
                                    projected_usd=0.0001, latency_ms=1, raw=text)

    inference.set_backend(Scripted())
    inference.reset_spend()
    try:
        page.locator(".seg").first.click()
        page.keyboard.press("x")
        page.keyboard.press("x")
        page.wait_for_selector(".empty")
        assert page.locator(".seg").count() == 0
        assert "No cut yet" in page.locator(".empty").inner_text()
        assert page.locator(".step.now").inner_text().endswith("ask for one")
        # the sidebar Ask panel hides itself here — the empty state already has a box
        # for the same sentence, and two inputs for one thing is a UI defect
        assert not page.locator("#askPanel").is_visible()

        page.fill("#story", "")        # an earlier test may have left one behind
        page.locator("#firstNote").fill("a loose film about two people talking")
        page.locator("#firstCut").click()
        page.wait_for_selector("#proposal:visible", timeout=30000)
        assert "conversation" in page.locator("#proposalNotes").inner_text()

        page.locator("#acceptProposal").click()
        assert page.locator(".seg").count() == 2
        # the brief is the human's half of the loop, so it is kept, not thrown away
        assert page.input_value("#story") == "a loose film about two people talking"
        assert "There is no edit yet" in Scripted.seen[-1].prompt
    finally:
        inference.set_backend(None)


def test_ask_failure_is_reported_not_swallowed(page):
    """Until `claude /login` is run, this is exactly what Karl will hit — it has to
    read as an explanation, not a silent no-op."""
    from roughcut import inference

    class Broken:
        name = "broken"

        def complete(self, request):
            raise inference.InferenceError("claude CLI error: Not logged in")

    inference.set_backend(Broken())
    try:
        page.locator("#note").fill("tighten the intro")
        page.locator("#ask").click()
        page.wait_for_function(
            "document.querySelector('#toast').textContent.includes('ask failed')",
            timeout=30000)
        assert "Not logged in" in page.locator("#toast").inner_text()
        assert not page.locator("#proposal").is_visible()
        assert page.locator("#ask").is_enabled(), "button must not stay disabled"
    finally:
        inference.set_backend(None)


def test_render_from_the_ui_produces_a_playable_file(page):
    page.locator("#render").click()
    page.wait_for_function(
        "document.querySelector('#renderState').textContent.startsWith('done')", timeout=180000)
    page.wait_for_selector("#versions .ver")
    src = page.get_attribute("#previewA", "src")
    assert src and src.startswith("/media/render/")
    page.wait_for_function(
        "document.querySelector('#previewA').readyState >= 1", timeout=30000)
    assert page.evaluate("document.querySelector('#previewA').duration") > 0


def test_a_second_render_becomes_a_second_version_to_compare_against(page):
    """Judging an edit is comparative. The newest render lands in A and the previous
    one in B, so two versions can be watched against each other without leaving."""
    before = page.locator("#versions .ver").count()
    page.locator(".seg").first.click()
    page.keyboard.press("x")                      # change the edit, so B differs
    page.locator("#render").click()
    page.wait_for_function(
        "document.querySelector('#renderState').textContent.startsWith('done')", timeout=180000)
    page.wait_for_function(
        f"document.querySelectorAll('#versions .ver').length > {before}", timeout=30000)
    a = page.get_attribute("#previewA", "src")
    b = page.get_attribute("#previewB", "src")
    assert a and b and a != b
    page.wait_for_function(
        "document.querySelector('#previewB').readyState >= 1", timeout=30000)
    assert page.evaluate("document.querySelector('#previewB').duration") > 0


def test_the_steps_strip_says_where_the_project_is(page):
    """The board was flat — Ask, Snap, Undo, Save and Render as peers, with nothing
    saying what to do first."""
    names = page.locator(".step").all_inner_texts()
    assert [n.split()[1] for n in names] == ["footage", "analyse", "first", "refine",
                                             "render"]
    # this project has clips, sidecars and a cut, so the first three are behind us
    assert page.locator(".step.done").count() >= 3


# ------------------------------------------------------------------ the monitor

def _clock(text: str) -> float:
    m, s = text.split(":")
    return int(m) * 60 + float(s)


def test_the_cut_plays_through_from_the_proxies(page):
    """The monitor: the whole edit plays from the proxies, shot after shot, with no
    render. Two shots — A 1.0–3.0 then B 0.0–2.0 — so a hand-over has to happen."""
    page.locator("#playCut").click()
    page.wait_for_function("player.playing && player.idx === 0", timeout=10000)
    assert page.locator("#playCut").inner_text().endswith("Pause")
    page.wait_for_function("player.idx === 1", timeout=15000)          # handed over to B
    assert page.evaluate("document.querySelector('.screen video.live').id") == "pv1"
    assert "2/2 · CLIP_B" in page.locator("#playingWhat").inner_text()
    page.wait_for_function("!player.playing", timeout=15000)             # ran off the end
    assert page.evaluate("document.querySelector('#pv1').currentTime") >= 1.8
    assert _clock(page.locator("#pos").inner_text()) >= 3.7              # of 4.0
    assert page.locator("#posTotal").inner_text() == "0:04.0"
    # the end puts the selection back at the top, so space plays again from the start
    assert page.evaluate("sel") == 0


def test_clicking_a_block_in_the_strip_jumps_the_monitor(page):
    blocks = page.locator("#strip .blk")
    assert blocks.count() == 2
    blocks.nth(0).click()
    page.wait_for_function("player.playing && player.idx === 0", timeout=10000)
    # shot A starts at 1.0 into its clip, not at the top of the proxy
    page.wait_for_function(
        "(() => { const v = document.querySelector('.screen video.live');"
        " return v.currentTime >= 1.0 && v.currentTime < 2.6; })()", timeout=10000)
    blocks.nth(1).click()
    page.wait_for_function("player.playing && player.idx === 1", timeout=10000)
    assert page.evaluate("sel") == 1, "the strip and the list select together"
    assert page.locator(".seg.sel .clip").inner_text() == "CLIP_B"
    assert page.locator("#strip .blk.sel").count() == 1


def test_space_toggles_the_cut_and_enter_plays_one_shot(page):
    page.locator(".seg").first.click()
    page.keyboard.press("Space")
    page.wait_for_function("player.playing", timeout=10000)
    # let the proxy actually load and run before pausing, or currentTime is still 0
    page.wait_for_function(
        "document.querySelector('.screen video.live').currentTime >= 1.0", timeout=10000)
    page.keyboard.press("Space")
    page.wait_for_function("!player.playing", timeout=5000)
    t = page.evaluate("document.querySelector('.screen video.live').currentTime")
    assert 1.0 <= t < 3.0, "paused inside shot A"
    # enter plays only the selected shot: it stops at the out-point, no hand-over
    page.keyboard.press("Enter")
    page.wait_for_function("player.playing && player.single", timeout=10000)
    page.wait_for_function("!player.playing", timeout=15000)
    assert page.evaluate("player.idx") == 0
    assert page.evaluate("document.querySelector('#pv0').currentTime") >= 2.9


def test_pressing_play_twice_while_a_shot_opens_leaves_the_monitor_stopped(page):
    """Karl, on the Killington bin: *"I cannot play it seems"*.

    On this synthetic project a proxy opens instantly. On a real bin it does not —
    sixteen shot cards are holding every connection the browser will give the origin,
    and the monitor's first `play()` waits seconds on `loadedmetadata`. Anyone presses
    play again in that window, and that used to pause a monitor which had not started,
    after which the *first* press's callback fired and played the video anyway: sound
    coming out of a board whose clock read 0:00.0, whose playhead never moved and which
    never reached the shot's out-point. The deferral is forced here, because it is the
    window and not the bin that carries the bug."""
    stuck = page.evaluate("""() => {
        const v = document.querySelector('#pv0');
        // the proxy has not opened yet, so playFrom must defer its play()
        Object.defineProperty(v, 'readyState', {configurable: true, get: () => 0});
        playFrom(0);                                      // press play
        pauseCut();                                       // press it again — nothing happened
        delete v.readyState;
        v.dispatchEvent(new Event('loadedmetadata'));     // the proxy opens, late
        return {playing: player.playing, paused: v.paused,
                transport: document.querySelector('#playCut').textContent};
    }""")
    assert stuck["paused"], "the superseded press started a video the board thinks is paused"
    assert not stuck["playing"]
    assert stuck["transport"].startswith("▶"), "the transport and the video must agree"

    # and the guard has not broken playing: the next press works normally
    page.locator("#playCut").click()
    page.wait_for_function("player.playing && player.idx === 0", timeout=10000)
    page.wait_for_function(
        "document.querySelector('.screen video.live').currentTime >= 1.2", timeout=10000)


def test_a_media_error_is_shown_on_the_monitor_not_swallowed(page):
    """The monitor had one way of reporting anything — a black rectangle — and a proxy
    that will not open looked exactly like one that is merely slow. What the browser
    actually said has to reach the screen, in this app's terms and with its code."""
    page.locator("#playCut").click()
    page.wait_for_function("player.playing && player.idx === 0", timeout=10000)
    page.evaluate("""() => {
        const v = document.querySelector('#pv0');
        v.dataset.src = '/media/proxy/CLIP_A.mp4';
        v.src = '/media/proxy/NOT_A_CLIP.mp4';        // 404: MEDIA_ERR_SRC_NOT_SUPPORTED
        v.load();
    }""")
    page.wait_for_selector("#screenMsg:not([hidden])", timeout=10000)
    msg = page.locator("#screenMsg").inner_text()
    assert "CLIP_A" in msg, msg
    assert "would not open" in msg and "code 4" in msg, msg
    assert page.locator("#screenMsg").get_attribute("class") == "bad"
    assert "code 4" in page.locator("#toast").inner_text()
    page.wait_for_function("!player.playing", timeout=5000)   # a dead buffer stops it
    assert page.locator("#playCut").inner_text().startswith("▶")


def test_a_refused_play_is_named_on_the_monitor(page):
    """`play()` returns a promise that Chrome rejects when its autoplay policy will not
    have an unmuted video, and the board used to throw that rejection away with an empty
    `.catch` — the one failure mode that cannot be seen from the outside at all."""
    page.evaluate("""() => {
        const v = document.querySelector('#pv0');
        v.play = () => Promise.reject(
            new DOMException('play() failed because the user did not interact with the '
                             + 'document first.', 'NotAllowedError'));
    }""")
    page.locator("#playCut").click()
    page.wait_for_selector("#screenMsg:not([hidden])", timeout=10000)
    assert "refused to play" in page.locator("#screenMsg").inner_text()
    assert "click the monitor" in page.locator("#toast").inner_text()
    page.wait_for_function("!player.playing", timeout=5000)
    assert page.locator("#playCut").inner_text().startswith("▶")


def test_playing_from_a_shot_card_brings_the_monitor_into_view(page):
    """The monitor is at the top of the column and the shot list runs a long way below
    it. On the 16-shot Killington cut, clicking shot 12's poster started playback 3,163px
    above the viewport — the board played, and the person saw a still page."""
    page.set_viewport_size({"width": 900, "height": 380})
    page.locator(".seg").last.scroll_into_view_if_needed()
    page.wait_for_timeout(200)
    assert not page.evaluate(
        "(() => { const r = document.querySelector('#player').getBoundingClientRect();"
        " return r.bottom > 0 && r.top < innerHeight; })()"), "monitor should be off-screen"

    page.locator(".seg").last.locator("video").click()
    page.wait_for_function("player.playing && player.idx === 1", timeout=10000)
    page.wait_for_function(
        "(() => { const r = document.querySelector('#player').getBoundingClientRect();"
        " return r.top >= 0 && r.bottom <= innerHeight + 1; })()", timeout=5000)


def test_the_versions_list_says_which_render_is_the_cut_on_the_board(page, project):
    """Karl: *"Cut board doesn't seem to reflect the render"* — after watching a rendered
    proposal that had never been accepted. It was labelled as a proposal, but nothing said
    which of the renders the board *did* reflect, and their names are hashes."""
    page.locator("#render").click()
    page.wait_for_function(
        "document.querySelectorAll('#versions .ver').length >= 1", timeout=120000)
    page.wait_for_function(
        "[...document.querySelectorAll('#versions .ver')]"
        ".some((r) => r.textContent.includes('this cut'))", timeout=10000)

    # trim the timeline and the render is no longer what is on the board
    page.locator(".seg").first.locator("button", has_text="+").nth(1).click()
    assert not page.evaluate(
        "[...document.querySelectorAll('#versions .ver')]"
        ".some((r) => r.textContent.includes('this cut'))"), \
        "an edited timeline is not the rendered one any more"
    page.locator("#undo").click()
    page.wait_for_function(
        "[...document.querySelectorAll('#versions .ver')]"
        ".some((r) => r.textContent.includes('this cut'))", timeout=5000)


# ------------------------------------------------------------------ what was seen

def test_what_the_visual_pass_saw_shows_on_the_cards_and_in_the_library(page, project):
    """Karl: the analysis "missed some critical moments that would have required video
    analysis — like me falling into a river." Once a clip has been looked at, the fall has
    to be on the board: as something you can add, and on the shot that contains it."""
    import server

    vdir = Path(server.STATE["visual"])
    vdir.mkdir(parents=True, exist_ok=True)
    sidecar = vdir / "CLIP_C.visual.json"
    sidecar.write_text(json.dumps({
        "clip": "CLIP_C.MP4",
        "moments": [{"start": 1.5, "end": 3.5, "what": "rider goes down in deep snow",
                     "kind": "fall", "notable": True},
                    {"start": 4.0, "end": 6.0, "what": "trees", "kind": "scenery",
                     "notable": False}],
        "unusable": [{"start": 0.0, "end": 0.6, "why": "lens covered"}],
        "summary": "a run"}), encoding="utf-8")
    try:
        page.reload()
        page.wait_for_selector(".seg")
        # the library grows a "seen" tab now that something has been looked at
        page.locator("#libTabs .tab", has_text="seen").click()
        cands = page.locator("#library .cand")
        assert cands.count() == 1, "notable moments only — the scenery is not offered"
        assert "fall" in cands.first.inner_text().lower()     # the tag renders uppercase
        assert "rider goes down" in cands.first.inner_text()
        cands.first.click()

        card = page.locator(".seg", has_text="CLIP_C")
        assert card.count() == 1
        assert "rider goes down in deep snow" in card.locator(".lines.seen").inner_text()
        # 1.5–3.5 is clear of the covered lens at the top of the clip…
        assert "unusable" not in card.inner_text()
        # …until the in-point is dragged back into it
        for _ in range(4):
            card.locator("button[data-act=in][data-d='-0.25']").click()
        card = page.locator(".seg", has_text="CLIP_C")
        assert "unusable 0.0–0.6: lens covered" in card.inner_text()
    finally:
        sidecar.unlink(missing_ok=True)


def test_the_seen_tab_is_ordered_by_the_rank_not_by_the_kind(page, project):
    """Karl: *"the lack of a workflow / algorithm that applies sort / priority."*

    The ordering that matters is not "events before scenery" — it is which of two
    claimed events to believe. A `jump` a closer look called a glove over the lens has
    to sit below a `fall` two looks agreed on, and has to say so on the row.
    """
    import server

    vdir = Path(server.STATE["visual"])
    vdir.mkdir(parents=True, exist_ok=True)
    sidecar = vdir / "CLIP_C.visual.json"
    sidecar.write_text(json.dumps({
        "clip": "CLIP_C.MP4",
        "moments": [{"start": 1.5, "end": 3.5, "what": "rider goes down",
                     "kind": "fall", "notable": True}],
        "unusable": [], "summary": "a run"}), encoding="utf-8")
    ranked = vdir / "events.json"
    ranked.write_text(json.dumps({"built": 0, "clips": 1, "events": [
        {"rank": 1, "clip": "CLIP_B.MP4", "start": 4.0, "end": 5.0, "kind": "fall",
         "notable": True, "score": 1.5, "source": "close look",
         "what": "the body goes down in the snow",
         "why_ranked": {"confirmation": "confirmed"}},
        {"rank": 2, "clip": "CLIP_C.MP4", "start": 1.5, "end": 3.5, "kind": "jump",
         "notable": True, "score": 0.4, "source": "sheet",
         "what": "possibly a backflip against the sky",
         "why_ranked": {"confirmation": "contradicted"}},
        {"rank": 3, "clip": "CLIP_A.MP4", "start": 0.5, "end": 1.5, "kind": "junk",
         "notable": True, "score": 0.0, "source": "close look",
         "what": "a glove over the lens", "why_ranked": {"confirmation": "unseen"}},
    ]}), encoding="utf-8")
    try:
        page.reload()
        page.wait_for_selector(".seg")
        page.locator("#libTabs .tab", has_text="seen").click()
        rows = page.locator("#library .cand")
        assert rows.count() == 2, "junk is never offered, whatever its kind says"
        # the tags render uppercase, like the kind tag beside them
        first = rows.nth(0).inner_text().lower()
        second = rows.nth(1).inner_text().lower()
        assert "the body goes down" in first and "confirmed" in first
        assert "backflip" in second and "unconfirmed" in second
        assert "events ranked" in page.locator("#project").inner_text()
    finally:
        sidecar.unlink(missing_ok=True)
        ranked.unlink(missing_ok=True)


# ------------------------------------------------------------------ music

def test_the_music_panel_writes_the_bed_and_the_monitor_plays_it_ducked(page, project):
    """Music is the EDL key the render reads, and the monitor plays the bed under the
    cut with the same duck the render applies — so what you hear before rendering is
    what you get after."""
    page.select_option("#musicTrack", "music/bed.wav")
    page.wait_for_function(
        "document.querySelector('#saveState').textContent.startsWith('saved')",
        timeout=8000)
    on_disk = json.loads(Path(project["edl"]).read_text(encoding="utf-8"))
    assert on_disk["effects_music"]["asset"] == "music/bed.wav"
    assert on_disk["effects_music"]["duck_db"] == 12
    assert page.locator("#musicOpts").is_visible()

    # 12 dB lower while someone is talking: clip A is speech from 0.15s (padded) on, so
    # 0.05s is the one quiet moment. Same film time for both so the fades cancel out.
    talking = page.evaluate("bedGainAt(2.0, 1.0, segs[0])")
    quiet = page.evaluate("bedGainAt(2.0, 0.05, segs[0])")
    assert 0 < talking < quiet
    assert 3.5 < quiet / talking < 4.5, f"expected ~4x (12 dB), got {quiet / talking:.2f}"

    page.locator("#playCut").click()
    page.wait_for_function("player.playing", timeout=10000)
    page.wait_for_function("!document.querySelector('#bed').paused", timeout=8000)
    assert page.evaluate("document.querySelector('#bed').src").endswith(
        "/media/asset/music/bed.wav")
    page.wait_for_function("document.querySelector('#bed').volume > 0", timeout=8000)
    assert page.evaluate("document.querySelector('#bed').volume") < 0.5
    page.locator("#playCut").click()
    page.wait_for_function("document.querySelector('#bed').paused", timeout=5000)

    # "no music" takes the key out again
    page.select_option("#musicTrack", "")
    for _ in range(50):
        on_disk = json.loads(Path(project["edl"]).read_text(encoding="utf-8"))
        if "effects_music" not in on_disk:
            break
        time.sleep(0.1)
    assert "effects_music" not in on_disk


def test_a_save_is_never_observed_half_written(page, project):
    """The board autosaves while its own polls, the monitor and a render read the EDL.
    Reading it mid-write used to return an empty file (a plain write truncates first);
    this hammers the file through twenty saves and must never see anything unparseable."""
    bad = 0
    for i in range(20):
        page.locator("#story").fill(f"draft {i}")
        page.evaluate("save()")
        for _ in range(5):
            text = Path(project["edl"]).read_text(encoding="utf-8")
            try:
                json.loads(text)
            except json.JSONDecodeError:
                bad += 1
            time.sleep(0.005)
    page.wait_for_function(
        "document.querySelector('#saveState').textContent.startsWith('saved')", timeout=8000)
    assert bad == 0, f"saw a half-written EDL {bad} time(s)"
    assert json.loads(Path(project["edl"]).read_text(encoding="utf-8"))["story"] == "draft 19"

