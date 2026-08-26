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
    """The whole latency argument for proxies rests on this working. The element under
    test is the monitor's, not a card's: the cards are stills now (see
    test_a_shot_card_carries_a_poster_not_a_video_stream), and the monitor is the only
    thing on the board that opens a proxy."""
    page.evaluate("arm(document.querySelector('#pv0'), segs[0])")
    page.wait_for_function(
        "document.querySelector('#pv0').readyState >= 1", timeout=15000)
    info = page.evaluate("""() => {
        const v = document.querySelector('#pv0');
        return {dur: v.duration, w: v.videoWidth, h: v.videoHeight};
    }""")
    assert info["dur"] == pytest.approx(6.0, abs=0.3)
    # 320x180 source is below the 1280 proxy cap, so it is passed through unscaled —
    # the proxy step must never upscale.
    assert (info["w"], info["h"]) == (320, 180)

    seeked = page.evaluate("""() => new Promise(res => {
        const v = document.querySelector('#pv0');
        v.onseeked = () => res(v.currentTime);
        v.currentTime = 4.0;
        setTimeout(() => res(-1), 5000);
    })""")
    assert seeked == pytest.approx(4.0, abs=0.5), "seeking failed — range serving broken"


def test_a_shot_card_carries_a_poster_not_a_video_stream(page):
    """Karl, on the Killington board: *"I can hear the videos when I click play, but
    the preview window still shows up blank."* Sixteen cards each holding open an
    85 MB proxy, against Chrome's six connections per host, starved the monitor's own
    request: it reached readyState 4 at ~10 s while the audio had already started. A
    card only ever showed one frame, so it is an <img> now."""
    assert page.locator(".seg video").count() == 0, "a card is streaming video again"
    cards = page.locator(".seg").count()
    posters = page.locator(".seg img.poster")
    assert posters.count() == cards
    src = posters.first.get_attribute("src")
    assert src.startswith("/media/poster/") and "?t=1.00" in src, src
    # and it is a real picture, not a broken image
    page.wait_for_function(
        "document.querySelector('.seg img.poster').naturalWidth > 0", timeout=15000)
    assert page.evaluate(
        "document.querySelector('.seg img.poster').naturalHeight") == 180


def test_trimming_the_in_point_moves_the_poster_without_one_frame_per_nudge(page):
    """A poster that lags a nudge by a moment is fine; one that fetches a frame on
    every 0.25 s press is not."""
    asked: list[str] = []
    page.on("request",
            lambda r: asked.append(r.url) if "/media/poster/" in r.url else None)
    card = page.locator(".seg").first
    for _ in range(6):
        card.locator("button", has_text="+").first.click()      # in +0.25, six times
    assert page.evaluate("segs[0].in") == pytest.approx(2.5, abs=0.01)
    page.wait_for_function(
        "document.querySelector('.seg img.poster').src.includes('t=2.50')",
        timeout=10000)
    page.wait_for_timeout(400)         # any straggler request would have started by now
    assert any("t=2.50" in u for u in asked), asked
    assert len(asked) <= 2, f"a frame per press, not per settled trim: {asked}"


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


def test_find_a_moment_lists_matches_and_plays_the_whole_clip(page):
    """The finder, free layer: type what you remember, get windows, click one and
    the full clip opens seeked to the moment; add it and it becomes a shot."""
    page.locator("#findQ").fill("goodbye")
    page.locator("#findGo").click()
    page.wait_for_selector("#findResults .cand", timeout=15000)
    rows = page.locator("#findResults .cand")
    assert rows.count() >= 1
    assert "goodbye" in rows.first.inner_text()

    rows.first.click()
    page.wait_for_selector("#findPlayer:visible")
    page.wait_for_function(
        "document.querySelector('#findVideo').readyState >= 1", timeout=15000)
    src = page.evaluate("document.querySelector('#findVideo').src")
    assert "/media/proxy/" in src and "#t=" in src
    # seeked to the match, not the top of the file — the full clip stays scrubbable.
    # It may already be playing (that is the point), so a range rather than a spot.
    t = page.evaluate("document.querySelector('#findVideo').currentTime")
    assert 4.4 <= t <= 6.05, f"expected playback at the match (~4.5s), got {t}"

    before = page.locator(".seg").count()
    page.locator("#findAdd").click()
    assert page.locator(".seg").count() == before + 1
    assert "goodbye" in page.locator(".seg").nth(1).inner_text()


def test_render_from_the_ui_produces_a_playable_file(page):
    page.locator("#render").click()
    page.wait_for_function(
        "document.querySelector('#renderState').textContent.startsWith('done')", timeout=180000)
    page.wait_for_selector("#versions .ver")
    # The player plays the 720p review copy, which is derived after the render says
    # done — so it appears a beat later, through the list's own poll.
    page.wait_for_function(
        "(document.querySelector('#previewA').getAttribute('src') || '')"
        ".startsWith('/media/review/')", timeout=60000)
    page.wait_for_function(
        "document.querySelector('#previewA').readyState >= 1", timeout=30000)
    assert page.evaluate("document.querySelector('#previewA').duration") > 0


def test_a_version_player_reaches_a_painted_frame(page):
    """Karl, on the finished delivery render: *"it looks like it already crashed.. or
    at least has an issue with the render"* and *"they seem to get stuck in this
    loading forever place"*. The player showed a black rectangle and said nothing. It
    plays a 720p review copy now, and a black rectangle is a failure the test can see:
    read the pixels back."""
    page.locator("#render").click()
    page.wait_for_function(
        "document.querySelector('#renderState').textContent.startsWith('done')",
        timeout=180000)
    page.wait_for_function(
        "(document.querySelector('#previewA').getAttribute('src') || '')"
        ".startsWith('/media/review/')", timeout=60000)
    page.evaluate("""() => {
        window.__paint = null;
        const el = document.querySelector('#previewA');
        const c = document.createElement('canvas');
        c.width = 48; c.height = 27;
        const ctx = c.getContext('2d', { willReadFrequently: true });
        const t0 = performance.now();
        el.muted = true;
        const poll = () => {
            if (el.videoWidth > 0) {
                ctx.drawImage(el, 0, 0, c.width, c.height);
                const d = ctx.getImageData(0, 0, c.width, c.height).data;
                let s = 0;
                for (let i = 0; i < d.length; i += 4) s += (d[i] + d[i+1] + d[i+2]) / 3;
                if (s / (c.width * c.height) > 5) {
                    window.__paint = performance.now() - t0;
                    return;
                }
            }
            requestAnimationFrame(poll);
        };
        el.play().catch((e) => { window.__err = String(e); });
        requestAnimationFrame(poll);
    }""")
    page.wait_for_function("window.__paint !== null", timeout=15000)
    assert page.evaluate("window.__paint") < 10000
    assert page.locator("#stateA").inner_text() == "", "a healthy player says nothing"


def test_a_version_row_offers_a_download_and_says_how_big_it_is(page):
    """Karl: *"Make it clear how to download the renders."* Getting a finished cut off
    the board was folklore — you had to know where ~/work/app/renders is."""
    page.locator("#render").click()
    page.wait_for_function(
        "document.querySelector('#renderState').textContent.startsWith('done')",
        timeout=180000)
    page.wait_for_selector("#versions .ver a.dl")
    row = page.locator("#versions .ver").first
    dl = row.locator("a.dl")
    assert dl.get_attribute("href").startswith("/media/download/render/cut_")
    assert dl.get_attribute("title").endswith("MB")
    assert ".mp4" in dl.get_attribute("title")
    assert "MB" in row.inner_text() and "x" in row.inner_text()   # size and resolution


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
    page.wait_for_function(
        "['A', 'B'].every((s) => (document.querySelector('#preview' + s)"
        ".getAttribute('src') || '').startsWith('/media/review/'))", timeout=60000)
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


def test_the_monitor_paints_a_frame_soon_after_play_is_pressed(page):
    """Karl: *"I can hear the videos when I click play, but the preview window still
    shows up blank."* Audio started while the picture was still black — on Killington
    the live element did not reach readyState 4 for 6.8–9.5 s from playFrom(0). The
    only honest test of "shows a picture" is to read the pixels back off the element:
    a black screen has a mean of 0.0, a decoded frame does not."""
    page.evaluate("""() => {
        window.__paint = null;
        const c = document.createElement('canvas');
        c.width = 48; c.height = 27;
        const ctx = c.getContext('2d', { willReadFrequently: true });
        const t0 = performance.now();
        const poll = () => {
            const v = document.querySelector('.screen video.live');
            if (v && v.videoWidth > 0) {
                ctx.drawImage(v, 0, 0, c.width, c.height);
                const d = ctx.getImageData(0, 0, c.width, c.height).data;
                let s = 0;
                for (let i = 0; i < d.length; i += 4) s += (d[i] + d[i+1] + d[i+2]) / 3;
                window.__mean = s / (c.width * c.height);
                if (window.__mean > 5) {
                    window.__paint = performance.now() - t0;
                    return;
                }
            }
            requestAnimationFrame(poll);
        };
        playFrom(0);
        requestAnimationFrame(poll);
    }""")
    page.wait_for_function("window.__paint !== null", timeout=8000)
    ms = page.evaluate("window.__paint")
    assert ms < 5000, f"the monitor was still black {ms:.0f}ms after play"
    # and it is playing the shot, not the top of the file: shot A starts at 1.0
    assert page.evaluate("document.querySelector('.screen video.live').currentTime") >= 1.0


def test_the_monitor_fetches_the_shot_not_the_top_of_the_file(page):
    """The monitor's elements were preload="auto" and got their src before anything
    told them where the shot starts, so Chrome downloaded from byte 0: a shot playing
    at 188.2 s on Killington had 0–15 s buffered and the seek queued behind it. The
    in-point goes in the URL as a media fragment now. (The suite's clips are 6 s and
    a few tens of KB, so the buffered range below cannot tell the two apart — it is a
    floor. The load-bearing assertions are the element's preload and the fragment in
    its URL; the 188.2 s measurement lives in CHANGELOG.)"""
    assert page.evaluate("document.querySelector('#pv0').preload") == "metadata"
    assert page.evaluate("document.querySelector('#pv1').preload") == "metadata"
    page.evaluate("playFrom(0)")
    page.wait_for_function(
        "document.querySelector('.screen video.live').readyState >= 3", timeout=15000)
    v = page.evaluate("""() => {
        const el = document.querySelector('.screen video.live');
        const r = [];
        for (let i = 0; i < el.buffered.length; i++)
            r.push([el.buffered.start(i), el.buffered.end(i)]);
        return {src: el.getAttribute('src'), ranges: r, t: el.currentTime};
    }""")
    assert v["src"].endswith("#t=1.00"), v["src"]
    assert any(a <= 1.0 <= b for a, b in v["ranges"]), \
        f"the in-point is not what got buffered: {v['ranges']}"


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

    page.locator(".seg").last.locator("img.poster").click()
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



# ------------------------------------------------------- the progress strip

STREAM_PLAN = json.dumps({
    "segments": [{"clip": "CLIP_C.MP4", "in": 0.5, "out": 4.0, "why": "one"},
                 {"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0, "why": "two"}],
    "notes": "two shots, streamed"})

STREAM_ESTIMATE = json.dumps({
    "eta_s": 60,
    "milestones": [{"key": "read", "label": "reading the footage", "weight": 1},
                   {"key": "think", "label": "working out the shape", "weight": 2},
                   {"key": "shots", "label": "choosing the shots", "weight": 4},
                   {"key": "notes", "label": "writing its reasoning", "weight": 1},
                   {"key": "polish", "label": "snapping to speech", "weight": 1}]})


def _streaming_backend(step=0.35):
    """A backend that answers the estimate call and then dribbles out the plan, so the
    browser can be watched tracking a call that is still being written."""
    from roughcut import config, inference

    class Streaming:
        name = "scripted-stream"

        def complete(self, request):
            first = request.role == config.ROLE_ANALYSIS
            text = STREAM_ESTIMATE if first else STREAM_PLAN
            time.sleep(step)
            if request.on_partial is not None:
                request.on_partial("thinking", "considering it")
                time.sleep(step)
                for cut in (55, 120, len(text)):
                    request.on_partial("text", text[:cut])
                    time.sleep(step)
            return inference.Result(
                content=text, input_tokens=10, output_tokens=5, backend="scripted",
                model=config.model_for(request.role), projected_usd=1e-4,
                latency_ms=1, raw=text)

    return Streaming()


def test_the_top_bar_tracks_an_ask_and_clears_when_everything_is_idle(page,
                                                                     monkeypatch):
    """Karl: "consider a progress tracking bar up top for anything which may take time
    to complete". For an Ask that means a bar the model's own half-written answer
    drives — and a strip that is not there at all when nothing is running."""
    import server
    from roughcut import inference, progress

    for registry in (server.ASKS, server.RENDERS, server.ANALYSES, server.VISUALS):
        registry.clear()          # earlier tests' finished jobs still linger briefly
    page.wait_for_function("document.querySelector('#progress').hidden === true",
                           timeout=5000)

    row = "#progress .job[data-kind=ask]"
    text_of = ("(document.querySelector('%s') || {}).textContent || ''")
    inference.set_backend(_streaming_backend(step=0.6))
    inference.reset_spend()
    try:
        page.locator("#note").fill("tighten the intro")
        page.locator("#ask").click()
        page.wait_for_selector(row, timeout=20000)
        assert "cut" in page.locator(f"{row} .jname").inner_text().lower()
        # a real bar, moving
        page.wait_for_function(
            f"parseFloat((document.querySelector('{row} .jbar i') || {{style:{{}}}})"
            ".style.width || 0) > 0", timeout=25000)
        # a milestone line, and the count of shots the model has actually written
        page.wait_for_function(
            f"/shots decided/.test({text_of % (row + ' .jdetail')})", timeout=25000)
        assert "%" in page.locator(f"{row} .jnums").inner_text()
        # opening it shows the checkpoints the estimate laid out
        page.locator(f"{row} .jlabel").click()
        assert "choosing the shots" in page.locator(f"{row} .jmore").inner_text()

        page.wait_for_selector("#proposal:visible", timeout=30000)
        # and then the strip empties itself
        monkeypatch.setattr(progress, "KEEP_FINISHED_S", 1.0)
        page.wait_for_function(
            "document.querySelector('#progress').hidden === true", timeout=20000)
    finally:
        inference.set_backend(None)


def test_the_top_bar_makes_a_render_impossible_to_miss(page, monkeypatch):
    """The original complaint: "got like no response - and just see rendering...".
    The Renders panel bar is easy to miss; this one is under the header."""
    import server
    from roughcut import progress

    monkeypatch.setattr(progress, "KEEP_FINISHED_S", 1.0)
    for registry in (server.ASKS, server.RENDERS, server.ANALYSES, server.VISUALS):
        registry.clear()
    row = "#progress .job[data-kind=render]"
    page.locator("#render").click()
    page.wait_for_selector(row, timeout=20000)
    assert "Rendering" in page.locator(f"{row} .jname").inner_text()
    page.wait_for_function(
        f"/cutting|joining/.test(document.querySelector('{row} .jdetail')"
        ".textContent)", timeout=60000)
    page.wait_for_function(
        "document.querySelector('#renderState').textContent.startsWith('done')",
        timeout=180000)
    page.wait_for_function(
        "document.querySelector('#progress').hidden === true", timeout=20000)


def test_the_render_control_will_not_fire_a_second_render(page, monkeypatch):
    """Karl: "can hitting the button multiple times break the system state of the
    render?" It cannot corrupt anything, but two encodes on one box make both crawl,
    so the server refuses the second with a 409 and the control says it is already
    rendering rather than quietly firing again. With the strip above showing the
    render's progress, a disabled control and a moving bar are the honest pair."""
    import server
    from roughcut import progress

    monkeypatch.setattr(progress, "KEEP_FINISHED_S", 1.0)
    for registry in (server.ASKS, server.RENDERS, server.ANALYSES, server.VISUALS):
        registry.clear()
    page.locator("#render").click()
    page.wait_for_function(
        "document.querySelector('#render').disabled === true", timeout=20000)
    assert "Rendering" in page.locator("#render").inner_text()

    # A second press does nothing: a disabled button dispatches no click, so no
    # second job is started even though the pointer found the same pixels.
    page.evaluate("document.querySelector('#render').click()")
    assert len(server.RENDERS) == 1, list(server.RENDERS)

    # and the request behind the button is refused on its own account, by name
    status, detail = page.evaluate("""async () => {
      const r = await fetch('/api/render', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ segments: segs }) });
      return [r.status, (await r.json()).detail];
    }""")
    assert status == 409, (status, detail)
    assert next(iter(server.RENDERS)) in detail, detail
    assert len(server.RENDERS) == 1

    # when it is over the control comes back on its own, off the job list rather
    # than off this tab's own click
    page.wait_for_function(
        "document.querySelector('#render').disabled === false", timeout=180000)
    assert page.locator("#render").inner_text().strip() == "Render"


def test_a_running_ask_is_picked_back_up_after_a_reload(page):
    """A four-minute call outlives a reload. The job registry is server-side, so the
    page re-attaches to what is still running instead of leaving it unwatched — which
    is how a call got spent for nothing on the first Killington ask."""
    import server
    from roughcut import inference

    for registry in (server.ASKS, server.RENDERS, server.ANALYSES, server.VISUALS):
        registry.clear()
    row = "#progress .job[data-kind=ask]"
    inference.set_backend(_streaming_backend(step=0.9))
    inference.reset_spend()
    try:
        page.locator("#note").fill("tighten the intro")
        page.locator("#ask").click()
        page.wait_for_selector(row, timeout=20000)

        page.reload()
        page.wait_for_selector(".seg")
        # still there, on the fresh page, without anyone pressing anything
        page.wait_for_selector(row, timeout=10000)
        assert "cut" in page.locator(f"{row} .jname").inner_text().lower()
        # and the answer arrives on the page that did not ask for it
        page.wait_for_selector("#proposal:visible", timeout=40000)
        assert "two shots, streamed" in page.locator("#proposalNotes").inner_text()
    finally:
        inference.set_backend(None)
