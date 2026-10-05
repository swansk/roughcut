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


def assert_priced(page, selector: str, name: str, mode: str) -> None:
    """INTAKE I16.0f: a button that asks the model carries its price before the click —
    "Ask · ~$0.58" — and it is the server's free estimate for that kind of ask."""
    usd = page.evaluate(f"fetch('/api/ask/price?mode={mode}').then(r => r.json()).then(d => d.usd)")
    want = f"{name} · ~${usd:.2f}"
    try:
        page.wait_for_function(
            "([s, w]) => document.querySelector(s) && document.querySelector(s).textContent.trim() === w",
            arg=[selector, want], timeout=5000)
    except playwright_api.TimeoutError:
        raise AssertionError(f"{selector} reads {page.locator(selector).first.inner_text()!r}, "
                             f"not {want!r}") from None


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
        pg.wait_for_selector("#tl .blk")
        yield pg
        browser.close()


def total_text(page) -> str:
    return page.locator("#total").inner_text()


def make_film(page) -> int:
    """Quick look, from the film tool (INTAKE M16 I16.1: the header's Render and its
    profile select are gone; *Make the film* opens this tool). Returns how many films
    there were before."""
    n = page.evaluate("renderList.length")
    page.evaluate("dock.open('out')")
    page.locator("#render").click()
    return n


def film_made(page, before: int, timeout: int = 180000) -> None:
    """The film landed: one more in the list and the buttons given back. The tool's own
    progress line is gone — the header's strip is the one progress surface."""
    page.wait_for_function(
        f"renderList.length > {before} && document.querySelector('#render').dataset.busy !== 'true'",
        timeout=timeout)


def cut_says(page, text: str, present: bool = True) -> None:
    """The flow bar's Cut stage (INTAKE M14) — where the old step strip's "1 hero
    waiting" went, as "1 hero not in it". Read from the files, so it follows the save."""
    page.evaluate("flowBar.poll()")
    page.wait_for_function(
        "([t, want]) => { const f = window.flowBar && flowBar.state();"
        " const s = f && f.stages.find((x) => x.key === 'cut');"
        " return !!s && s.summary.includes(t) === want; }",
        arg=[text, present], timeout=15000)


def select_shot(page, i: int) -> None:
    """Select shot i on the timeline by id, without playing it (a click on a block also
    plays the cut from there). The inspector follows the selection."""
    page.evaluate(f"tl.select([tl.idAt({i})])")


def test_board_renders_the_timeline(page):
    assert page.locator("#tl .blk").count() == 2
    # the cut's title is the tab's, not a header line (INTAKE M16 I16.1: one header row)
    assert "test cut" in page.title()
    assert page.locator("#total").inner_text() == "0:04.0"   # (3.0-1.0) + (2.0-0.0)


def test_preview_video_loads_and_can_seek(page):
    """The whole latency argument for proxies rests on this working. The element under
    test is the monitor's, not the inspector's: the inspector shows a still (see
    test_the_inspector_carries_a_poster_not_a_video_stream), and the monitor is the only
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


def test_the_inspector_carries_a_poster_not_a_video_stream(page):
    """Karl, on the Killington board: *"I can hear the videos when I click play, but
    the preview window still shows up blank."* Sixteen cards each holding open an
    85 MB proxy, against Chrome's six connections per host, starved the monitor's own
    request: it reached readyState 4 at ~10 s while the audio had already started. The
    inspector shows one frame of the selected shot, and it is an <img>."""
    assert page.locator("#inspector video").count() == 0, "the inspector is streaming video"
    posters = page.locator("#inspector img.poster")
    assert posters.count() == 1                       # shot 1 is selected at boot
    src = posters.first.get_attribute("src")
    assert src.startswith("/media/poster/") and "?t=1.00" in src, src
    # and it is a real picture, not a broken image
    page.wait_for_function(
        "document.querySelector('#inspector img.poster').naturalWidth > 0", timeout=15000)
    assert page.evaluate(
        "document.querySelector('#inspector img.poster').naturalHeight") == 180


def test_trimming_the_in_point_moves_the_poster_without_one_frame_per_nudge(page):
    """A poster that lags a nudge by a moment is fine; one that fetches a frame on
    every 0.25 s press is not. (The timeline's block catches up on the same settle,
    so two requests for the one frame is the ceiling.)"""
    asked: list[str] = []
    page.on("request",
            lambda r: asked.append(r.url) if "/media/poster/" in r.url else None)
    for _ in range(6):
        page.locator("#inspector button[data-act=in][data-d='0.25']").click()   # in +0.25
    assert page.evaluate("segs[0].in") == pytest.approx(2.5, abs=0.01)
    page.wait_for_function(
        "document.querySelector('#inspector img.poster').src.includes('t=2.50')",
        timeout=10000)
    page.wait_for_timeout(400)         # any straggler request would have started by now
    assert any("t=2.50" in u for u in asked), asked
    assert len(asked) <= 2, f"a frame per press, not per settled trim: {asked}"


def test_trim_buttons_change_duration_and_are_undoable(page):
    before = total_text(page)
    page.locator("#inspector button[data-act=out][data-d='0.25']").click()   # out +0.25
    assert total_text(page) != before
    page.locator("#undo").click()
    assert total_text(page) == before


def test_keyboard_trim_matches_button_trim(page):
    select_shot(page, 0)
    before = total_text(page)
    page.keyboard.press("}")                       # extend out by 0.25
    after_key = total_text(page)
    assert after_key != before
    page.locator("#undo").click()
    assert total_text(page) == before


def test_boundary_warning_appears_and_snap_clears_it(page):
    """The defect Karl flagged, surfaced live and then fixed by the tool. Shot 1 (the
    boot selection) opens mid-sentence: the inspector says so; after the snap it does
    not. The header's "Fix N cut points" is gone (INTAKE M16 I16.1) — the fix belongs
    on the warned shot; `snap()` is what fixes them."""
    assert "⚠" in page.locator("#inspector .meta").inner_text(), \
        "seeded EDL cuts mid-utterance; warning should show"
    assert page.locator("#snap").count() == 0
    page.evaluate("snap()")
    page.wait_for_function("segs.every((s) => !boundaryWarning(s))", timeout=15000)
    assert "⚠" not in page.locator("#inspector .meta").inner_text()
    assert page.locator("#tl .blk .warn:not([hidden])").count() == 0


def test_undo_restores_exact_state_after_snap(page):
    before = page.evaluate("JSON.stringify(segs)")
    page.evaluate("snap()")
    page.wait_for_function(f"JSON.stringify(segs) !== {json.dumps(before)}",
                           timeout=15000)
    page.locator("#undo").click()
    assert page.evaluate("JSON.stringify(segs)") == before


def test_remove_from_the_inspector_takes_the_shot_out_and_moves_on(page):
    """remove in the inspector is tl.remove: the shot goes, the one that takes its place
    is selected, and the inspector shows that one."""
    assert page.locator("#tl .blk").count() == 2
    first_clip = page.locator("#inspector .clip").inner_text()
    page.locator("#inspector button[data-act=del]").click()
    assert page.locator("#tl .blk").count() == 1
    assert page.locator("#inspector .clip").inner_text() != first_clip
    assert page.locator("#undo").get_attribute("title").startswith("undo: remove")


def test_library_insert_adds_a_shot(page):
    before = page.locator("#tl .blk").count()
    page.locator("#library .cand").first.click()
    assert page.locator("#tl .blk").count() == before + 1


def test_edits_reach_the_disk_without_being_asked(page, project):
    """Karl: "I start the project and create some cuts - but then it resets the cut
    board as soon as I refresh the page." The working edit lived in the browser and
    only a Save button wrote it, so a refresh threw the work away."""
    page.evaluate("dock.open('ask')")   # the dock's tool (INTAKE M11)
    page.locator("#story").fill("the milk is the running joke")
    page.evaluate("document.activeElement.blur()")   # the keys are the board's, not the story's
    select_shot(page, 0)
    page.keyboard.press("}")                       # extend the out point
    page.wait_for_function(
        "document.querySelector('#saveState').textContent.startsWith('saved')",
        timeout=8000)

    on_disk = json.loads(Path(project["edl"]).read_text(encoding="utf-8"))
    assert on_disk["story"] == "the milk is the running joke"
    assert on_disk["segments"][0]["out"] == 3.25


def test_the_cut_survives_a_reload(page):
    """The whole point: what is on screen after F5 is what you left."""
    page.locator("#inspector button[data-act=del]").click()
    page.wait_for_function(
        "document.querySelector('#saveState').textContent.startsWith('saved')",
        timeout=8000)
    shape = "JSON.stringify(segs.map(s => [s.clip, s.in, s.out]))"
    before = page.evaluate(shape)

    page.reload()
    page.wait_for_selector("#tl .blk")
    assert page.locator("#tl .blk").count() == 1
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
        page.evaluate("dock.open('ask')")   # the dock's tool (INTAKE M11)
        page.locator("#note").fill("use the clip that isn't in the cut")
        assert_priced(page, "#ask", "Ask for a change", "full")
        page.locator("#ask").click()
        page.wait_for_selector("#proposal:visible", timeout=30000)
        # one blue button per screen is Next's (INTAKE M16): the proposal's are plain,
        # and ▶ Play it is the one Next may mark
        assert page.locator("#proposal button.primary").count() == 0
        assert page.locator("#playProposal").get_attribute("data-next-for") == "cut"

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
        assert page.locator("#tl .blk").count() == 1
        assert page.locator("#inspector .clip").inner_text() == "CLIP_C"

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
        select_shot(page, 0)
        page.keyboard.press("x")
        page.keyboard.press("x")
        page.wait_for_selector("#inspector .empty")
        assert page.locator("#tl .blk").count() == 0
        assert "No cut yet" in page.locator(".empty").inner_text()
        # the flow: the cut is the stage to do, and Next says so
        page.evaluate("flowBar.poll()")
        page.wait_for_function(
            "window.flowBar && flowBar.state()"
            " && flowBar.state().stages.find((s) => s.key === 'cut').state === 'ready'",
            timeout=10000)
        page.wait_for_function(
            "document.querySelector('#flowNext').innerText.includes('first cut')", timeout=5000)
        # the Ask tool's change box hides itself here — there is nothing to change
        assert not page.locator("#askPanel").is_visible()
        # one sentence (INTAKE M16 I16.4): the empty state asks it in the same words as
        # the Ask tool, and both are the EDL's story; ONE priced button, Next's to mark
        assert "What is this film about?" in page.locator(".empty").inner_text()
        assert page.locator(".empty button").count() == 1
        assert page.locator("#firstCut").get_attribute("data-next-for") == "cut"
        assert "aims for" in page.locator("#firstAims").inner_text()

        page.evaluate("dock.open('ask')")   # the dock's tool (INTAKE M11)
        page.fill("#story", "")        # an earlier test may have left one behind
        page.locator("#firstNote").fill("a loose film about two people talking")
        assert page.input_value("#story") == "a loose film about two people talking"
        assert_priced(page, "#firstCut", "Make a first cut", "first")
        page.locator("#firstCut").click()
        page.wait_for_selector("#proposal:visible", timeout=30000)
        assert "conversation" in page.locator("#proposalNotes").inner_text()

        page.locator("#acceptProposal").click()
        assert page.locator("#tl .blk").count() == 2
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
        page.evaluate("dock.open('ask')")   # the dock's tool (INTAKE M11)
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


def test_the_inspector_asks_about_the_selected_shot(page):
    """The per-shot loop, end to end: open the form in the inspector, ask, read the
    proposal, accept — and every other shot survives verbatim."""
    from roughcut import config, inference

    class Scripted:
        name = "scripted"
        seen: list = []

        def complete(self, request):
            Scripted.seen.append(request)
            text = json.dumps({
                "segments": [{"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0,
                              "why": "starts on the line now"}],
                "notes": "opened it on the greeting"})
            model = config.model_for(request.role)
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend="scripted", model=model,
                                    projected_usd=0.0001, latency_ms=1, raw=text)

    inference.set_backend(Scripted())
    inference.reset_spend()
    try:
        insp = page.locator("#inspector")
        assert insp.locator(".shotAsk").is_hidden()
        insp.locator("button[data-act=ask]").click()
        page.wait_for_selector("#inspector .shotAsk:visible")
        insp.locator(".shotNote").fill("start this on the line instead")
        assert_priced(page, "#inspector button[data-act=shotgo]", "Ask", "shot")
        insp.locator("button[data-act=shotgo]").click()
        page.wait_for_selector("#proposal:visible", timeout=30000)

        # scoped: the panel names the shot, the untouched shot is in the diff
        notes = page.locator("#proposalNotes").inner_text()
        assert "shot 1" in notes and "opened it on the greeting" in notes
        assert "CLIP_B" in page.locator("#proposalDiff").inner_text()
        # and the prompt was the scoped one, confined to the shot's clip
        assert "Every segment must come from CLIP_A.MP4" in Scripted.seen[-1].prompt

        page.locator("#acceptProposal").click()
        assert page.locator("#tl .blk").count() == 2
        assert page.evaluate("segs[0].in") == pytest.approx(0.5, abs=0.01)
        assert page.evaluate("JSON.stringify([segs[1].clip, segs[1].in, segs[1].out])") \
            == '["CLIP_B.MP4",0,2]'
    finally:
        inference.set_backend(None)


def test_a_colour_proposal_reads_as_words_and_accept_writes_the_grade(page, live_server):
    """INTAKE I10.5 on the board: a colour-only answer is a proposal like any other —
    its colour line in the panel, the cut unchanged and said so, Discard leaving the
    EDL's colour alone, Accept writing the patch into it in the one save."""
    import urllib.request
    from roughcut import config, inference, revise

    def project():
        with urllib.request.urlopen(f"{live_server}/api/project") as r:
            return json.loads(r.read())

    ids = page.evaluate("segs.map((s) => s.id)")

    class Scripted:
        name = "scripted"

        def complete(self, request):
            text = (json.dumps({"notes": "a film look, the opening matched",
                                "colour": {"look": "filmic", "strength": 0.6,
                                           "shots": {ids[0]: {"match": "previous"}}}})
                    if request.system == revise.SYSTEM else "{}")
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend="scripted", model=config.model_for(request.role),
                                    projected_usd=0.0001, latency_ms=1, raw=text)

    inference.set_backend(Scripted())
    inference.reset_spend()
    try:
        before = page.evaluate("JSON.stringify(segs)")
        page.evaluate("dock.open('ask')")
        page.locator("#note").fill("make it filmic")
        page.locator("#ask").click()
        page.wait_for_selector("#proposal:visible", timeout=30000)
        diff = page.locator("#proposalDiff").inner_text()
        assert "look: filmic at 0.6" in diff
        assert "the cut unchanged" in diff and "only the colour changes" in diff

        page.locator("#rejectProposal").click()
        page.wait_for_timeout(300)
        assert not project().get("colour"), "Discard must leave the grade alone"

        page.locator("#ask").click()
        page.wait_for_selector("#proposal:visible", timeout=30000)
        page.locator("#acceptProposal").click()
        deadline = time.time() + 10
        while (project().get("colour") or {}).get("look") != "filmic":
            assert time.time() < deadline, "Accept never saved the grade"
            time.sleep(0.1)
        saved = project()
        assert saved["colour"]["strength"] == 0.6
        assert saved["colour"]["shots"] == {ids[0]: {"match": "previous"}}
        assert [s["id"] for s in saved["segments"]] == ids
        assert page.evaluate("JSON.stringify(segs)") == before
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

    before = page.locator("#tl .blk").count()
    page.evaluate("tl.seek(1.5)")      # adding lands at the playhead (INTAKE M11): the cut nearest 1.5 s is 2.0
    page.locator("#findAdd").click()
    assert page.locator("#tl .blk").count() == before + 1
    # inserted at the playhead's cut, selected, and in the inspector with its line
    assert page.evaluate("sel") == 1
    assert "goodbye" in page.locator("#inspector .why").inner_text()


def test_render_from_the_ui_produces_a_playable_file(page):
    film_made(page, make_film(page))
    page.wait_for_selector("#filmNewest:not([hidden])")
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
    film_made(page, make_film(page))
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
    the board was folklore — you had to know where ~/work/app/renders is. Now the film
    of this cut is Download in the film tool AND in the header's slot where *Make the
    film* was (INTAKE M16 I16.1), with its size before the click."""
    film_made(page, make_film(page))
    page.wait_for_selector("#filmNewest:not([hidden])")
    dl = page.locator("#filmDownload")
    assert dl.get_attribute("href").startswith("/media/download/render/cut_")
    assert dl.get_attribute("title").endswith("MB")
    assert ".mp4" in dl.get_attribute("title")
    assert "MB" in dl.inner_text()
    assert "this cut" in page.locator("#labelA").inner_text()
    # the header's button is that film's Download now: quality and length on it
    head = page.locator("#makeFilm")
    page.wait_for_function(
        "document.querySelector('#makeFilm').textContent.startsWith('↓ Download')", timeout=5000)
    assert head.inner_text() == "↓ Download · 1080p · 0:04"
    assert head.get_attribute("data-download") == dl.get_attribute("href")
    with page.expect_download(timeout=15000) as got:
        head.click()
    assert got.value.suggested_filename.endswith(".mp4")


def test_a_second_render_becomes_a_second_version_to_compare_against(page):
    """Judging an edit is comparative. Compare two (inside Older films, INTAKE M16 —
    A/B no longer on every row, and no players loading on open) starts on the newest
    film and the one before it, so two versions can be watched side by side."""
    film_made(page, make_film(page))
    select_shot(page, 0)
    page.keyboard.press("x")                      # change the edit, so B differs
    film_made(page, make_film(page))
    assert page.get_attribute("#cmpA", "src") is None, "nothing loads until it is opened"
    page.locator("#olderFilms > summary").click()
    page.locator("#compare > summary").click()
    page.wait_for_function(
        "['#cmpA', '#cmpB'].every((s) => (document.querySelector(s)"
        ".getAttribute('src') || '').startsWith('/media/review/'))", timeout=60000)
    a = page.get_attribute("#cmpA", "src")
    b = page.get_attribute("#cmpB", "src")
    assert a and b and a != b
    assert a == page.get_attribute("#previewA", "src"), "A starts on this cut's newest film"
    page.wait_for_function(
        "document.querySelector('#cmpB').readyState >= 1", timeout=30000)
    assert page.evaluate("document.querySelector('#cmpB').duration") > 0


def test_the_board_says_what_to_do_next_and_nothing_more(page):
    """The board was flat — Ask, Snap, Undo, Save and Render as peers, with nothing
    saying what to do first. The five-step strip that answered it became the M14 flow
    bar; M16 (decision 2) took the bar off and kept its Next chip, the one "what now"."""
    page.wait_for_selector("#flowNext")
    assert page.locator(".step").count() == 0 and page.locator("#steps").count() == 0
    assert page.locator("#flow .fs, #flow [data-stage]").count() == 0
    assert page.locator("#flow a").count() == 1
    # the flow behind it still knows: this project has clips, previews and a cut
    page.wait_for_function("window.flowBar && flowBar.state()", timeout=10000)
    state = lambda k: page.evaluate(  # noqa: E731
        f"flowBar.state().stages.find((s) => s.key === '{k}').state")
    assert state("footage") == "done" and state("cut") == "done"


# ---------------------------------------------------------------- the inspector
#
# INTAKE M9, I9.5: the list of one card per shot is gone; one inspector under the
# timeline holds what a card held, for the anchor shot.

def _on_disk(project) -> list[dict]:
    return json.loads(Path(project["edl"]).read_text(encoding="utf-8"))["segments"]


def test_selecting_a_block_fills_the_inspectors_header_and_why(page):
    assert page.locator(".seg").count() == 0, "the card list is gone"
    insp = page.locator("#inspector")
    # shot 1 is the anchor at boot
    assert "SHOT 1 of 2" in insp.locator(".meta").inner_text()
    page.locator("#tl .blk").nth(1).click()
    page.evaluate("pauseCut()")                   # a click on a block also plays from it
    head = insp.locator(".meta").inner_text()
    assert "SHOT 2 of 2" in head and "CLIP_B" in head, head
    assert "0:00.0 → 0:02.0" in head and "2.0 s" in head, head
    assert "starts at 0:02.0 of the film" in head, head
    assert "⚠" not in head, "CLIP_B 0.0–2.0 ends with 'hello there', on its edge"
    assert insp.locator(".why").inner_text() == "second"
    assert "hello there" in insp.locator(".lines").first.inner_text()
    assert insp.locator("img.poster").get_attribute("src") == "/media/poster/CLIP_B.jpg?t=0.00"
    assert insp.locator(".lines.seen").is_hidden(), "nothing was seen on this bin"
    # ↑ goes back to the previous cut and the inspector follows
    page.keyboard.press("ArrowUp")
    assert "SHOT 1 of 2" in insp.locator(".meta").inner_text()
    assert insp.locator(".clip").inner_text() == "CLIP_A"
    assert insp.locator(".why").inner_text() == "first"
    assert "⚠ opens mid-sentence · cuts a line off" in insp.locator(".meta").inner_text()


def test_editing_why_in_the_inspector_saves_to_the_edl(page, project):
    why = page.locator("#inspector .why")
    why.click()
    page.keyboard.press("Control+a")
    page.keyboard.type("opens on the greeting")
    assert page.evaluate("segs[0].why") == "first", "not written until the field is left"
    page.evaluate("dock.open('ask')")   # the dock's tool (INTAKE M11)
    page.locator("#story").click()                # leave the field
    assert page.evaluate("segs[0].why") == "opens on the greeting"
    page.wait_for_function(
        "document.querySelector('#saveState').textContent.startsWith('saved')", timeout=8000)
    assert _on_disk(project)[0]["why"] == "opens on the greeting"
    # the timeline's own tooltip carries the new why too
    assert "opens on the greeting" in page.locator("#tl .blk").first.get_attribute("title")


def test_the_inspectors_trim_buttons_are_one_undo_entry_each(page, project):
    insp = page.locator("#inspector")
    insp.locator("button[data-act=out][data-d='0.25']").click()
    assert page.evaluate("[segs[0].in, segs[0].out]") == [1.0, 3.25]
    assert "0:01.0 → 0:03.3" in insp.locator(".meta").inner_text()
    assert insp.locator(".times").get_attribute("title") == "1.00 → 3.25 s of CLIP_A.MP4"
    assert "2.3 s" in insp.locator(".meta").inner_text()
    assert page.locator("#total").inner_text() == "0:04.3"
    assert page.locator("#undo").get_attribute("title").startswith("undo: trim")
    insp.locator("button[data-act=in][data-d='-0.25']").click()
    assert page.evaluate("[segs[0].in, segs[0].out]") == [0.75, 3.25]
    # ⇧ makes it a second
    insp.locator("button[data-act=in][data-d='0.25']").click(modifiers=["Shift"])
    assert page.evaluate("[segs[0].in, segs[0].out]") == [1.75, 3.25]
    page.wait_for_function(
        "document.querySelector('#saveState').textContent.startsWith('saved')", timeout=8000)
    first = _on_disk(project)[0]
    assert (first["in"], first["out"]) == (1.75, 3.25)
    # one ⌘Z per press, in order
    page.keyboard.press("Control+z")
    assert page.evaluate("[segs[0].in, segs[0].out]") == [0.75, 3.25]
    page.keyboard.press("Control+z")
    assert page.evaluate("[segs[0].in, segs[0].out]") == [1.0, 3.25]
    page.keyboard.press("Control+z")
    assert page.evaluate("[segs[0].in, segs[0].out]") == [1.0, 3.0]
    assert "0:01.0 → 0:03.0" in insp.locator(".meta").inner_text()
    assert page.locator("#total").inner_text() == "0:04.0"


def test_the_kept_tabs_in_the_cut_link_selects_the_block_and_the_inspector_shows_it(page):
    _put_selects(page, [{"clip": "CLIP_B.MP4", "start": 0.0, "end": 2.0, "hero": True,
                         "why": "the reply"}])
    page.reload()
    page.wait_for_selector("#library .keep")
    link = page.locator("#library .keep", has_text="CLIP_B").locator("a.use")
    assert link.inner_text() == "in the cut · shot 2"
    # from a cleared selection too — the link goes by id through the timeline
    page.keyboard.press("Escape")
    assert page.evaluate("tl.state.anchor") is None
    link.click()
    assert page.evaluate("sel") == 1
    assert page.locator("#tl .blk.sel").get_attribute("data-id") == page.evaluate("segs[1].id")
    assert page.locator("#inspector .clip").inner_text() == "CLIP_B"
    assert "SHOT 2 of 2" in page.locator("#inspector .meta").inner_text()
    assert not page.evaluate("player.playing"), "a link selects; it does not play"


def test_the_inspector_says_so_when_nothing_or_several_are_selected(page):
    insp = page.locator("#inspector")
    page.keyboard.press("Escape")
    assert page.evaluate("tl.state.anchor") is None
    text = insp.inner_text()
    assert "select a shot on the timeline" in text and "↑" in text and "↓" in text, text
    assert "2 shots · 0:04.0 · target 0:05.0–0:20.0" in text, text
    assert insp.locator(".why").count() == 0
    # a multi-selection: the count and the length, and remove for all of them
    page.keyboard.press("Control+a")
    assert page.evaluate("tl.state.sel.size") == 2
    text = insp.inner_text()
    assert "2 shots selected · 4.0 s" in text, text
    assert insp.locator(".why").count() == 0
    insp.locator("button[data-act=delall]").click()
    assert page.evaluate("segs.length") == 0
    assert page.locator("#tl .blk").count() == 0
    assert "No cut yet" in insp.locator(".empty").inner_text()
    page.keyboard.press("Control+z")               # one entry for both
    assert page.evaluate("segs.length") == 2
    assert page.locator("#tl .blk").count() == 2
    assert insp.locator(".empty").count() == 0
    select_shot(page, 0)
    assert "SHOT 1 of 2" in insp.locator(".meta").inner_text()


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
    blocks = page.locator("#tl .blk")
    assert blocks.count() == 2
    blocks.nth(0).click()
    page.wait_for_function("player.playing && player.idx === 0", timeout=10000)
    # shot A starts at 1.0 into its clip, not at the top of the proxy
    page.wait_for_function(
        "(() => { const v = document.querySelector('.screen video.live');"
        " return v.currentTime >= 1.0 && v.currentTime < 2.6; })()", timeout=10000)
    blocks.nth(1).click()
    page.wait_for_function("player.playing && player.idx === 1", timeout=10000)
    assert page.evaluate("sel") == 1, "the strip and the inspector select together"
    assert page.locator("#inspector .clip").inner_text() == "CLIP_B"
    assert page.locator("#tl .blk.sel").count() == 1


def test_space_toggles_the_cut_and_enter_plays_one_shot(page):
    select_shot(page, 0)
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
    # not any message: while the proxy is still below HAVE_CURRENT_DATA the monitor says
    # "opening CLIP_A…" first, and the refusal follows once the metadata is in
    page.wait_for_function(
        "(() => { const m = document.querySelector('#screenMsg');"
        " return !m.hidden && m.textContent.includes('refused to play'); })()", timeout=10000)
    assert "click the monitor" in page.locator("#toast").inner_text()
    page.wait_for_function("!player.playing", timeout=5000)
    assert page.locator("#playCut").inner_text().startswith("▶")


def test_playing_from_the_inspector_brings_the_monitor_into_view(page):
    """The monitor is at the top of the column. When the shot list ran a long way below
    it, clicking shot 12's poster on the 16-shot Killington cut started playback 3,163px
    above the viewport — the board played, and the person saw a still page. The
    inspector sits right under the timeline, but a short window can still have it on
    screen with the monitor scrolled off; a play from its still brings the monitor back."""
    page.set_viewport_size({"width": 900, "height": 380})
    select_shot(page, 1)
    page.evaluate("document.querySelector('#inspector').scrollIntoView({block: 'start'})")
    page.wait_for_timeout(200)
    assert not page.evaluate(
        "(() => { const r = document.querySelector('#player').getBoundingClientRect();"
        " return r.bottom > 0 && r.top < innerHeight; })()"), "monitor should be off-screen"

    # the sticky header covers the top of the viewport, so the click is the element's own
    page.evaluate("document.querySelector('#inspector img.poster').click()")
    page.wait_for_function("player.playing && player.idx === 1", timeout=10000)
    page.wait_for_function(
        "(() => { const r = document.querySelector('#player').getBoundingClientRect();"
        " return r.top >= 0 && r.bottom <= innerHeight + 1; })()", timeout=5000)


def test_the_versions_list_says_which_render_is_the_cut_on_the_board(page, project):
    """Karl: *"Cut board doesn't seem to reflect the render"* — after watching a rendered
    proposal that had never been accepted. It was labelled as a proposal, but nothing said
    which of the renders the board *did* reflect, and their names are hashes. The film
    tool's player is this cut's newest film; the header's button is its Download, and
    *Make the film* with a dot once the cut changes (INTAKE M16 I16.1)."""
    film_made(page, make_film(page), timeout=120000)
    page.wait_for_selector("#filmNewest:not([hidden])", timeout=10000)
    assert "this cut" in page.locator("#labelA").inner_text()
    assert page.locator("#filmSince").inner_text().startswith("made ")
    assert page.locator("#makeFilm").inner_text().startswith("↓ Download")

    # trim the timeline and the render is no longer what is on the board
    page.locator("#inspector button[data-act=out][data-d='0.25']").click()
    assert not page.locator("#filmNewest").is_visible(), \
        "an edited timeline is not the rendered one any more"
    assert page.locator("#filmSince").inner_text().startswith("changed since your last film")
    assert page.locator("#makeFilm").inner_text().replace(" ", "") == "Makethefilm•"
    page.locator("#undo").click()
    page.wait_for_selector("#filmNewest:not([hidden])", timeout=5000)
    assert page.locator("#makeFilm").inner_text().startswith("↓ Download")


# ------------------------------------------------------------------ what was seen

def test_what_the_visual_pass_saw_shows_in_the_inspector_and_in_the_library(page, project):
    """Karl: the analysis "missed some critical moments that would have required video
    analysis — like me falling into a river." Once a clip has been looked at, the fall has
    to be on the board: as something you can add, and in the inspector of the shot that
    contains it."""
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
        page.wait_for_selector("#tl .blk")
        # the library grows a "seen" tab now that something has been looked at
        page.locator("#libTabs .tab", has_text="seen").click()
        cands = page.locator("#library .cand")
        assert cands.count() == 1, "notable moments only — the scenery is not offered"
        assert "fall" in cands.first.inner_text().lower()     # the tag renders uppercase
        assert "rider goes down" in cands.first.inner_text()
        cands.first.click()

        # the insert selects the new shot, so the inspector is on it
        insp = page.locator("#inspector")
        assert insp.locator(".clip").inner_text() == "CLIP_C"
        assert "rider goes down in deep snow" in insp.locator(".lines.seen").inner_text()
        # 1.5–3.5 is clear of the covered lens at the top of the clip…
        assert "unusable" not in insp.inner_text()
        # …until the in-point is dragged back into it
        for _ in range(4):
            insp.locator("button[data-act=in][data-d='-0.25']").click()
        assert "unusable 0.0–0.6: lens covered" in insp.inner_text()
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
        page.wait_for_selector("#tl .blk")
        page.locator("#libTabs .tab", has_text="seen").click()
        rows = page.locator("#library .cand")
        assert rows.count() == 2, "junk is never offered, whatever its kind says"
        # the tags render uppercase, like the kind tag beside them
        first = rows.nth(0).inner_text().lower()
        second = rows.nth(1).inner_text().lower()
        assert "the body goes down" in first and "confirmed" in first
        assert "backflip" in second and "unconfirmed" in second
        # ("events ranked" was the Project popover's line; its facts are the switcher's
        # menu now — INTAKE M16 I16.1)
    finally:
        sidecar.unlink(missing_ok=True)
        ranked.unlink(missing_ok=True)


def test_the_seen_tab_offers_the_audit_priced_and_marks_a_one_look_find(page, project):
    """HANDOFF roadmap item 1: the claims at the top of the seen tab are the ones nobody
    checked, so the tab carries one priced button that audits them. It is never clicked
    here — the real close look spends model calls."""
    import server

    vdir = Path(server.STATE["visual"])
    vdir.mkdir(parents=True, exist_ok=True)
    sidecar = vdir / "CLIP_C.visual.json"        # the tab shows once something was seen
    sidecar.write_text(json.dumps({
        "clip": "CLIP_C.MP4",
        "moments": [{"start": 1.5, "end": 3.5, "what": "rider goes down",
                     "kind": "fall", "notable": True}],
        "unusable": [], "summary": "a run"}), encoding="utf-8")
    ranked = vdir / "events.json"
    one_look = {"rank": 2, "clip": "CLIP_B.MP4", "start": 4.0, "end": 5.0, "kind": "jump",
                "notable": True, "score": 0.63, "source": "close look",
                "what": "skier leaves the lip", "why_ranked": {"confirmation": "fine-only"}}
    ranked.write_text(json.dumps({"built": 0, "clips": 2, "events": [
        {"rank": 1, "clip": "CLIP_C.MP4", "start": 1.5, "end": 3.5, "kind": "fall",
         "notable": True, "score": 1.2, "source": "sheet",
         "what": "rider goes down", "why_ranked": {"confirmation": "unseen"}},
        one_look]}), encoding="utf-8")
    try:
        page.reload()
        page.wait_for_selector("#tl .blk")
        assert not page.locator("#auditRow").is_visible()     # heard: not this tab's
        page.locator("#libTabs .tab", has_text="seen").click()
        button = page.locator("#auditClaims")
        assert button.is_visible() and button.is_enabled()
        assert button.inner_text() == f"Audit 1 claim · ~${server.FINE_USD_PER_WINDOW:.2f}"
        rows = page.locator("#library .cand")
        assert "one look" in rows.nth(1).inner_text().lower()

        # nothing left unaudited: the button stays on the tab, off, and says why
        ranked.write_text(json.dumps({"built": 0, "clips": 1, "events": [one_look]}),
                          encoding="utf-8")
        page.reload()
        page.wait_for_selector("#tl .blk")
        page.locator("#libTabs .tab", has_text="seen").click()
        assert page.locator("#auditClaims").is_disabled()
        assert "close look" in page.locator("#auditInfo").inner_text()
    finally:
        sidecar.unlink(missing_ok=True)
        ranked.unlink(missing_ok=True)


# ------------------------------------------------------------------ music

def test_the_music_panel_writes_the_bed_and_the_monitor_plays_it_ducked(page, project):
    """Music is the EDL key the render reads, and the monitor plays the bed under the
    cut with the same duck the render applies — so what you hear before rendering is
    what you get after."""
    page.evaluate("dock.open('sound')")   # the dock's tool (INTAKE M11)
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
        page.evaluate("dock.open('ask')")   # the dock's tool (INTAKE M11)
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
        page.evaluate("dock.open('ask')")   # the dock's tool (INTAKE M11)
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
    page.wait_for_function("document.querySelector('#progress').hidden === true",
                           timeout=5000)         # earlier tests' finished films gone
    before = make_film(page)
    page.wait_for_selector(row, timeout=20000)
    assert "Rendering" in page.locator(f"{row} .jname").inner_text()
    page.wait_for_function(
        f"/cutting|joining/.test(document.querySelector('{row} .jdetail')"
        ".textContent)", timeout=60000)
    # the strip is the one progress surface: the film tool keeps no bar of its own
    assert page.locator("#outPanel .bar").count() == 0
    assert "idle" not in page.locator("#outPanel").inner_text()
    film_made(page, before)
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
    make_film(page)
    page.wait_for_function(
        "document.querySelector('#render').disabled === true", timeout=20000)
    assert "Making the film" in page.locator("#render").inner_text()
    assert page.locator("#renderFinal").is_disabled(), "one film at a time, either kind"

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
    assert page.locator("#render").inner_text().strip() == "Quick look · 1080p · ~1 min"
    assert page.locator("#renderFinal").inner_text().strip() == "Final 4K · ~1 min"


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
        page.evaluate("dock.open('ask')")   # the dock's tool (INTAKE M11)
        page.locator("#note").fill("tighten the intro")
        page.locator("#ask").click()
        page.wait_for_selector(row, timeout=20000)

        page.reload()
        page.wait_for_selector("#tl .blk")
        # still there, on the fresh page, without anyone pressing anything
        page.wait_for_selector(row, timeout=10000)
        assert "cut" in page.locator(f"{row} .jname").inner_text().lower()
        # and the answer arrives on the page that did not ask for it
        page.wait_for_selector("#proposal:visible", timeout=40000)
        assert "two shots, streamed" in page.locator("#proposalNotes").inner_text()
    finally:
        inference.set_backend(None)


# ------------------------------------------------------------------ the bin

def _put_selects(page, selects: list[dict]) -> dict:
    """The bin, written the way the bin editor writes it (PUT /api/selects)."""
    out = page.evaluate("""(selects) => fetch('/api/selects', {
        method: 'PUT', headers: {'content-type': 'application/json'},
        body: JSON.stringify({selects})}).then(r => r.json())""", selects)
    assert out.get("ok"), out
    return out


def test_the_kept_tab_shows_the_bin_and_puts_a_keep_in_the_cut(page):
    """Karl, after using the pass: *"what should I expect going from the pass to the
    cut board here? Cut board looks exactly the same as before."* The pass wrote keeps
    into the EDL and the board showed nothing of it. Now the library opens on them."""
    _put_selects(page, [
        {"clip": "CLIP_A.MP4", "start": 4.0, "end": 5.5, "why": "the goodbye",
         "note": "end on this", "hero": False},
        {"clip": "CLIP_C.MP4", "start": 0.5, "end": 4.0, "hero": True,
         "why": "the whole take (frames 0:00 · 0:04)"},
        {"clip": "GONE.MP4", "start": 0.0, "end": 2.0, "why": "a keep whose file left",
         "missing": True},
    ])
    page.reload()
    page.wait_for_selector("#library .keep")
    # kept is the first tab and the one the board opened on
    tabs = page.locator("#libTabs .tab")
    assert tabs.first.inner_text() == "kept"
    assert "sel" in tabs.first.get_attribute("class")
    rows = page.locator("#library .keep")
    assert rows.count() == 3
    # hero first, then by clip and start; each row says what it is
    hero = rows.nth(0)
    assert "★ HERO" in hero.inner_text()
    assert "CLIP_C · 0:00.5 → 0:04.0 · 3.5 s" in hero.inner_text()
    assert "(frames 0:00 · 0:04)" in hero.inner_text()
    assert hero.locator("img.still").get_attribute("src") == "/media/poster/CLIP_C.jpg?t=0.50"
    plain = rows.nth(1)
    assert "CLIP_A" in plain.inner_text() and "“end on this”" in plain.inner_text()
    assert "HERO" not in plain.inner_text()
    assert plain.locator("button.add").count() == 1
    gone = rows.nth(2)
    assert "footage missing" in gone.inner_text()
    assert gone.locator("button.add").count() == 0, "a missing keep cannot be added"
    # the flow bar's Cut names the hero (the Project panel's bin line went with Project ▾,
    # INTAKE M16 I16.1 — the bin's facts are the switcher's menu)
    cut_says(page, "1 hero not in it")

    # + add to cut: a shot with the keep's range and reason, after the selected shot
    page.evaluate("tl.seek(1.5)")            # adding lands at the playhead (INTAKE M11): the cut nearest 1.5 s is 2.0, so shot 2
    hero.locator("button.add").click()
    assert page.locator("#tl .blk").count() == 3
    added = page.evaluate("JSON.stringify([segs[1].clip, segs[1].in, segs[1].out, segs[1].why])")
    assert added == '["CLIP_C.MP4",0.5,4,"the whole take (frames 0:00 · 0:04)"]'
    # …and the row flips at once, before the autosave lands
    hero = page.locator("#library .keep", has_text="CLIP_C")
    assert hero.locator("a.use").inner_text() == "in the cut · shot 2"
    assert hero.locator("button.add").count() == 0
    page.wait_for_function(
        "document.querySelector('#saveState').textContent.startsWith('saved')", timeout=8000)
    cut_says(page, "not in it", present=False)
    # the save told the bin; re-read from the server it still says the same, and the
    # two seeded shots have been adopted as hand keeps like any other
    page.wait_for_function(
        "[...document.querySelectorAll('#library .keep')].length >= 5", timeout=8000)
    on_server = page.evaluate("fetch('/api/selects').then(r => r.json())")
    hero_row = next(s for s in on_server["selects"] if s["clip"] == "CLIP_C.MP4")
    assert hero_row["used_in"], hero_row
    assert page.locator("#library .keep", has_text="CLIP_C").locator("a.use").inner_text() \
        == "in the cut · shot 2"
    # the link selects the shot
    select_shot(page, 0)
    page.locator("#library .keep", has_text="CLIP_C").locator("a.use").click()
    assert page.evaluate("sel") == 1
    assert page.locator("#inspector .clip").inner_text() == "CLIP_C"
    # removing the shot un-flips the row
    page.keyboard.press("x")
    assert page.locator("#library .keep", has_text="CLIP_C").locator("button.add").count() == 1
    cut_says(page, "1 hero not in it")


def test_an_empty_bin_says_where_to_keep_things(page):
    page.locator("#libTabs .tab", has_text="kept").click()
    box = page.locator("#library")
    page.wait_for_function(
        "document.querySelector('#library').textContent.includes('nothing kept yet')",
        timeout=5000)
    assert "the pass is where you keep things" in box.inner_text()
    assert box.locator("a").get_attribute("href") == "/floor"
    # and there is nothing to cut from: no Cut from the bin to press (INTAKE M16: the
    # Ask tool's second button is gone; with no keeps the first cut is from the index)
    assert page.locator("#cutFromBin").count() == 0
    # with no keeps the board opens on heard, as before
    page.reload()
    page.wait_for_selector("#tl .blk")
    assert "sel" in page.locator("#libTabs .tab", has_text="heard").get_attribute("class")
    assert page.locator("#library .cand").count() >= 1


BIN_NOTE = ("Build the cut from the editor's selects: every hero must appear, use the "
            "other keeps where they serve the story, and take nothing else unless it is "
            "needed to make a keep land.")


def test_cut_from_the_bin_is_one_ask_with_the_fixed_note(page):
    """The prompt already carries the bin (revise.py's "The editor's selects"); the
    empty board's one button asks for exactly that when there are keeps — "Make the
    first cut from your 1 keep" — and the proposal loop is the usual one. Once a cut
    exists it is not a second button beside Ask (INTAKE M16: the look-alike goes)."""
    from roughcut import config, inference

    class Scripted:
        name = "scripted"
        seen: list = []

        def complete(self, request):
            Scripted.seen.append(request)
            text = json.dumps({
                "segments": [{"clip": "CLIP_C.MP4", "in": 0.5, "out": 4.0,
                              "why": "the hero, whole"}],
                "notes": "built from the bin"})
            model = config.model_for(request.role)
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend="scripted", model=model,
                                    projected_usd=0.0001, latency_ms=1, raw=text)

    _put_selects(page, [{"clip": "CLIP_C.MP4", "start": 0.5, "end": 4.0, "hero": True,
                         "why": "the whole take", "note": "this is the film"}])
    page.reload()
    page.wait_for_selector("#library .keep")
    inference.set_backend(Scripted())
    inference.reset_spend()
    try:
        # with a cut on the board: the Ask tool has one priced button, Ask for a change
        page.evaluate("dock.open('ask')")   # the dock's tool (INTAKE M11)
        assert page.locator("#cutFromBin").count() == 0
        assert page.locator("#askPanel button").count() == 1

        # with no cut: the panel is hidden and the empty state carries the button; the
        # fixed note is the app's words and must not become the story
        page.fill("#story", "")
        page.evaluate("document.activeElement.blur()")
        select_shot(page, 0)
        page.keyboard.press("x")
        page.keyboard.press("x")
        page.wait_for_selector("#inspector .empty")
        assert not page.locator("#askPanel").is_visible()
        assert page.locator(".empty button").count() == 1
        assert_priced(page, "#firstCut", "Make the first cut from your 1 keep", "bin")
        page.locator("#firstCut").click()
        page.wait_for_selector("#proposal:visible", timeout=30000)
        prompt = Scripted.seen[-1].prompt
        assert "There is no edit yet" in prompt and BIN_NOTE in prompt
        assert "The editor's selects" in prompt
        assert "HERO" in prompt and "editor's note: \"this is the film\"" in prompt
        assert "built from the bin" in page.locator("#proposalNotes").inner_text()
        assert page.input_value("#story") == ""
        page.locator("#acceptProposal").click()
        assert page.locator("#tl .blk").count() == 1
        assert page.locator("#library .keep", has_text="CLIP_C").locator("a.use").inner_text() \
            == "in the cut · shot 1"
        cut_says(page, "not in it", present=False)
    finally:
        inference.set_backend(None)


# ------------------------------------------------------------ the index line

def test_the_old_buttons_are_gone_and_the_header_is_one_row(page):
    """INTAKE decision 3: the index is one unattended run started from the open screen;
    the board's Analyse audio / Look at the footage buttons stay gone. INTAKE M16 I16.1:
    the header is ONE row at most 50 px tall — the bin · cut name, Next, ↶ ↷, ? and
    Make the film. Gone from it: "Cut board" and the second bin name, the length and
    target, Project ▾ (its facts, the index line among them, are the switcher's menu),
    the model pill (the CLI banner speaks when the CLI needs Karl), "saved", "Fix N cut
    points", Render and its profile select."""
    for gone in ("#analyze", "#visual", "#analyzeBar", "#visualBar", "#visualRow",
                 "#projectBtn", "#projectPop", "#indexState", "#openFootage", "#backend",
                 "#snap", "#renderProfile", "#band", "header h1", "#title"):
        assert page.locator(gone).count() == 0, gone
    seen = page.locator("body").inner_text()      # what a person reads, not the source
    for words in ("Analyse audio", "Look at the footage", "Cut board", "target",
                  "Project ▾", "saved"):
        assert words not in seen, words
    # (the `open · pass · board` pills and the M14 step bar are gone, M16 decision 2)
    page.wait_for_selector("#flowNext")
    assert page.locator("#screens").count() == 0
    assert page.locator("#flow .fs").count() == 0
    page.set_viewport_size({"width": 1440, "height": 900})
    # (the progress strip is the header's second row only while something runs)
    import server
    for registry in (server.ASKS, server.RENDERS, server.ANALYSES, server.VISUALS):
        registry.clear()
    page.wait_for_function("document.querySelector('#progress').hidden === true", timeout=5000)
    hd = page.evaluate("document.querySelector('header').getBoundingClientRect().height")
    assert hd <= 50, hd
    mids = page.evaluate("""[...document.querySelectorAll('header > *')]
        .filter((e) => e.getBoundingClientRect().height > 0)
        .map((e) => { const r = e.getBoundingClientRect(); return (r.top + r.bottom) / 2; })""")
    assert max(mids) - min(mids) <= 3, mids       # one row: every item on one line
    ids = page.evaluate("""[...document.querySelectorAll('header button')]
        .filter((e) => e.getBoundingClientRect().height > 0).map((e) => e.id)""")
    assert ids == ["hdBin", "undo", "redo", "keysBtn", "makeFilm"], ids
    # Make the film opens the film tool (the rail calls it Film; its key stays `out`) —
    # while no film of this cut exists (an earlier test's would make it Download)
    page.evaluate("renderList = []; paintVersions()")
    assert page.locator("#makeFilm").inner_text() == "Make the film"
    page.locator("#makeFilm").click()
    assert page.evaluate("dock.current()") == "out"
    assert page.locator("#rail .tool[data-tool=out]").inner_text().strip() == "FILM"


def test_saved_says_nothing_and_a_failed_save_says_so(page, live_server):
    """INTAKE M16 I16.1: status only when something is wrong — "unsaved…" while the
    autosave waits, "save failed" when it failed, nothing after a good save."""
    page.keyboard.press("x")
    page.wait_for_function(
        "document.querySelector('#saveState').textContent.startsWith('saved')", timeout=8000)
    assert not page.locator("#saveState").is_visible()
    page.route("**/api/project", lambda route: route.fulfill(status=500, body="no")
               if route.request.method == "PUT" else route.continue_())
    page.evaluate("touch()")
    assert page.locator("#saveState").inner_text() == "unsaved…"
    page.wait_for_function(
        "document.querySelector('#saveState').textContent === 'save failed'", timeout=8000)
    assert page.locator("#saveState").is_visible()
    page.unroute("**/api/project")


def test_ask_the_model_in_the_bin_is_priced_before_it_can_be_clicked(page, live_server):
    """INTAKE I16.0f / decision 7: every model button shows its price before the click.
    The Bin's *Ask the model* read just that, enabled, and a click spent before any
    price was shown. It is disabled until the free price is on it, and when the price
    cannot be had it says so and stays disabled."""
    usd = page.evaluate("fetch('/api/find/price').then(r => r.json()).then(d => d.usd)")
    want = f"Ask the model · ~${usd:.2f}"
    page.wait_for_function(
        "(w) => document.querySelector('#findDeep').textContent.trim() === w", arg=want,
        timeout=5000)
    assert page.locator("#findDeep").is_enabled()
    # the page as served starts it disabled: no click before the price lands
    html = page.evaluate("fetch('/').then(r => r.text())")
    assert '<button id="findDeep" disabled' in html
    # no price to be had: no unpriced spend either
    page.route("**/api/find/price", lambda route: route.abort())
    page.reload()
    page.wait_for_selector("#tl .blk")
    page.wait_for_function(
        "document.querySelector('#findDeep').textContent.includes('price unavailable')",
        timeout=5000)
    assert page.locator("#findDeep").is_disabled()
    page.unroute("**/api/find/price")


def test_ask_buttons_wait_for_their_price_and_never_spend_without_one(page, live_server):
    """INTAKE I16.0f / decision 7, review: an Ask-family button whose price had not
    arrived kept its plain name and stayed clickable — a click in the first second
    spent with no price shown, and a failed price fetch left it so for good. It is
    disabled until priced; with no price to be had it says so, stays disabled, and an
    ask that gets through anyway (a key, a script) refuses before any POST."""
    assert_priced(page, "#ask", "Ask for a change", "full")
    assert page.locator("#ask").is_enabled()
    html = page.evaluate("fetch('/').then(r => r.text())")
    assert '<button id="ask" disabled data-await-price="1">Ask for a change</button>' in html

    posts = []
    page.on("request", lambda r: posts.append(r.url)
            if r.method == "POST" and r.url.rstrip("/").endswith("/api/ask") else None)
    page.route("**/api/ask/price*", lambda route: route.abort())
    page.reload()
    page.wait_for_selector("#tl .blk")
    page.wait_for_function(
        "document.querySelector('#ask').textContent === 'Ask for a change · price unavailable'", timeout=5000)
    assert page.locator("#ask").is_disabled()
    insp = page.locator("#inspector")
    insp.locator("button[data-act=ask]").click()
    page.wait_for_selector("#inspector .shotAsk:visible")
    go = insp.locator("button[data-act=shotgo]")
    assert go.is_disabled() and go.inner_text() == "Ask · price unavailable"
    page.evaluate("document.querySelector('#note').value = 'tighten it'")
    page.evaluate("ask()")
    page.wait_for_function(
        "document.querySelector('#toast').textContent.includes('no price yet')", timeout=3000)
    page.wait_for_timeout(300)
    assert posts == [], posts
    page.unroute("**/api/ask/price*")


# ---------------------------------------------------------------- the switcher

def test_saving_a_copy_flushes_the_autosave_first_and_the_board_moves_to_it(page, project,
                                                                            live_server):
    """The board autosaves on a 700 ms timer. A trim made just before Save copy is
    pressed is still on that timer; it must reach the cut it was made on — and so the
    copy — rather than be lost, or land only in the copy. Then the page is on the copy
    (a reload, the word carried across), and can come back."""
    import server
    original = server.STATE["edl"]
    page.keyboard.press("x")                 # remove the selected shot: 2 → 1, save pending
    assert page.locator("#tl .blk").count() == 1
    # hold the timer open so the flush is the only way the edit reaches the file
    page.evaluate("clearTimeout(saveTimer); saveTimer = setTimeout(save, 60000)")
    assert len(json.loads(Path(project["edl"]).read_text(encoding="utf-8"))["segments"]) == 2
    assert page.locator("#hdBin .cutname").inner_text() == "edl"     # named after its file
    page.locator("#hdBin").click()
    page.wait_for_selector("#picker:not([hidden])")
    page.wait_for_selector("#cutList .crow")
    page.locator("#copyName").fill("one shot")
    copy_path = None
    try:
        with page.expect_navigation(timeout=15000):
            page.locator("#copyGo").click()
        page.wait_for_selector("#tl .blk")
        page.wait_for_function(
            "document.querySelector('#hdBin .cutname').textContent === 'one shot'", timeout=10000)
        page.wait_for_function(
            "document.querySelector('#toast').textContent.includes('saved a copy as one shot')",
            timeout=5000)
        # the original took the flush, the copy has the same one shot, the board shows it
        assert len(json.loads(Path(project["edl"]).read_text(encoding="utf-8"))["segments"]) == 1
        cuts = page.evaluate("fetch('/api/cuts').then(r => r.json())")
        copy_path = cuts["current"]["path"]
        assert cuts["current"]["name"] == "one shot" and cuts["current"]["segments"] == 1
        assert cuts["current"]["from"] == "edl"
        assert Path(copy_path) != original and Path(copy_path).parent.name == project["footage"].name
        assert page.locator("#tl .blk").count() == 1
        # and back, by its row — the header says so, the timeline is the original's
        page.locator("#hdBin").click()
        page.wait_for_selector("#cutList .crow")
        assert page.locator("#cutList .crow").count() == 2
        with page.expect_navigation(timeout=15000):
            page.locator("#cutList .crow:not(.current)").click()
        page.wait_for_selector("#tl .blk")
        page.wait_for_function(
            "document.querySelector('#hdBin .cutname').textContent === 'edl'", timeout=10000)
        assert server.STATE["edl"] == original
    finally:
        # the module's server back on the seeded file, and the copy out of the way
        server.switch_cut(original)
        if copy_path and Path(copy_path).exists():
            Path(copy_path).unlink()


def test_the_switchers_name_is_styled_before_it_is_ever_opened(page):
    """I16.0 (h): the switcher's sheet was injected with its panel, so until the first
    open the header read "killington-neutralmain" — bin and cut run together, no ▾."""
    page.wait_for_function(
        "document.querySelector('#hdBin .cutname').textContent === 'edl'", timeout=10000)
    assert page.locator("#picker").count() == 0, "the panel has not been opened"
    after = page.evaluate("getComputedStyle(document.querySelector('#hdBin'), '::after').content")
    assert "▾" in after, after
    gap = page.evaluate("getComputedStyle(document.querySelector('#hdBin .cutname')).marginLeft")
    assert gap == "6px", gap


def test_render_rows_say_the_day_and_the_cut_and_fold_repeats(page):
    """I16.0 (k): Killington's ten renders span Jul 25 – Sep 8 and each row said only a
    clock time; three were the same 17-shot preview; none was the cut on the board, and
    nothing said so. Rows carry the day and the cut, identical ones fold with a count,
    and the line under "This cut" says when this cut has not been made — since when it
    changed (INTAKE M16 I16.1: the rows are "Older films", folded, each with ↓)."""
    got = page.evaluate("""() => {
      const other = [{clip: 'CLIP_C.MP4', in: 0, out: 1.5}];
      const base = {size: 2e6, width: 320, height: 180, duration_s: 1.5, segments: 1,
                    profile: 'preview', review_state: 'ready', url: '', review_url: '',
                    download_url: '/x', download_name: 'x.mp4', note: '', music: null};
      const t = (d) => new Date(d).getTime() / 1000;
      renderList = [
        {...base, name: 'cut_a.mp4', created: t('2026-09-08T22:05:00'), cut: 'main', shots: other},
        {...base, name: 'cut_b.mp4', created: t('2026-08-23T16:10:00'), cut: 'main', shots: other},
        {...base, name: 'cut_c.mp4', created: t('2026-08-23T14:13:00'), cut: 'main', shots: other},
        {...base, name: 'cut_d.mp4', created: t('2026-08-22T18:54:00'), cut: 'main', shots: other,
         note: 'proposal ed8134bb — not accepted'},
        {...base, name: 'cut_e.mp4', created: t('2026-07-25T20:37:00'), shots: null},
      ];
      paintVersions();
      const un = document.querySelector('#filmSince');
      // the day and the time in the viewer's own words ("Sep 8, 10:05 PM" in en-US,
      // "8 Sept, 22:05" in en-GB): the expected strings are made the way the row's are
      const when = (d) => new Date(d).toLocaleString([],
        {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'});
      return {rows: [...document.querySelectorAll('#versions .ver')].map(
                (r) => [r.textContent.replace(/\\s+/g, ' ').trim(), r.title]),
              links: document.querySelectorAll('#versions .ver a.dl').length,
              older: document.querySelector('#olderFilms > summary').textContent,
              unmade: un && un.textContent,
              when: [when('2026-09-08T22:05:00'), when('2026-08-23T16:10:00'),
                     when('2026-07-25T20:37:00')]};
    }""")
    rows = got["rows"]
    sep8, aug23, jul25 = got["when"]
    assert len(rows) == 3, rows                      # a, b, c are one film made three times
    assert sep8 in rows[0][0] and "· main" in rows[0][0] and "×3" in rows[0][0], (sep8, rows)
    assert aug23 in rows[0][1], "the folded ones are still named, on hover"
    assert "proposal ed8134bb — not accepted" in rows[1][0] and "×" not in rows[1][0]
    assert jul25 in rows[2][0]
    assert not any("this cut" in r[0] for r in rows)
    assert got["links"] == 3 and got["older"] == "Older films (3)"
    assert got["unmade"] == f"changed since your last film ({sep8})"
    # a render of the cut on the board: it leaves the rows for the player, and the line
    # says when it was made
    again = page.evaluate("""() => {
      renderList[0].shots = segs.map((s) => ({clip: s.clip, in: s.in, out: s.out}));
      paintVersions();
      return {rows: document.querySelectorAll('#versions .ver').length,
              newest: !document.querySelector('#filmNewest').hidden,
              label: document.querySelector('#labelA').textContent,
              since: document.querySelector('#filmSince').textContent};
    }""")
    assert again["newest"] and "this cut" in again["label"], again
    assert again["since"] == f"made {sep8}", again
    assert again["rows"] == 3, again             # b and c fold now; d; e


def test_the_last_proposal_link_shows_only_while_the_proposal_waits(page, live_server):
    """I16.0 (l): "last proposal — 21 shots, 40 days ago · show it" came back on every
    load. It is offered while the proposal waits, and gone once it is answered."""
    import server
    asks = server.STATE["asks"]
    asks.mkdir(parents=True, exist_ok=True)
    path = asks / "i16l.json"
    rec = {"job": "i16l", "created": time.time() + 3600, "note": "n", "story": "",
           "plan": {"segments": [{"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "w"}]}}
    try:
        path.write_text(json.dumps(rec), encoding="utf-8")
        page.reload()
        page.wait_for_selector("#tl .blk")
        page.wait_for_function(
            "document.querySelector('#lastAsk').textContent.includes('show it')", timeout=10000)
        assert page.evaluate("document.querySelector('#lastAsk').style.display") == "block"
        # answered (the board's Discard writes this): not offered again
        path.write_text(json.dumps({**rec, "answered": {"answer": "discard", "at": time.time()}}),
                        encoding="utf-8")
        with page.expect_response(lambda r: "/api/asks/latest" in r.url, timeout=15000):
            page.reload()
        page.wait_for_selector("#tl .blk")
        page.wait_for_timeout(500)                  # the answer read and acted on
        assert page.evaluate("document.querySelector('#lastAsk').style.display") == "none"
    finally:
        path.unlink(missing_ok=True)


def _waiting_ask(job: str, segments: list[dict]) -> Path:
    """An unanswered Ask proposal on disk, newer than any cut the test saves."""
    import server
    asks = server.STATE["asks"]
    asks.mkdir(parents=True, exist_ok=True)
    path = asks / f"{job}.json"
    path.write_text(json.dumps({"job": job, "created": time.time() + 3600, "note": "n",
                                "story": "", "plan": {"segments": segments}}),
                    encoding="utf-8")
    return path


def _next_with(page, key: str) -> dict:
    page.evaluate("flowBar.poll()")
    page.wait_for_function(
        "(k) => { const n = window.flowBar && flowBar.state() && flowBar.state().next;"
        " return !!n && !!n.target && !!n.target[k]; }", arg=key, timeout=15000)
    return page.evaluate("flowBar.state().next")


def test_next_lands_on_a_waiting_first_cut_proposal(page, live_server, project):
    """I16.0a, review: a first-cut proposal waits on an empty cut. Next carried
    `{ask: job}`, but the landing waited for a shot on the timeline — which an empty cut
    never has — and gave up after 15 s without showing the proposal."""
    Path(project["edl"]).write_text(json.dumps({
        "variant": "T", "title": "test cut", "orient": "none", "story": "",
        "target_s": [5, 20], "segments": []}, indent=1), encoding="utf-8")
    path = _waiting_ask("i16first", [{"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0, "why": "w"},
                                     {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "w"}])
    try:
        page.goto(live_server + "/open")
        nxt = _next_with(page, "ask")
        assert nxt["stage"] == "cut" and nxt["target"] == {"ask": "i16first"}, nxt
        page.locator("#flowNext").click()
        page.wait_for_selector("#proposal", state="visible", timeout=12000)
        assert page.evaluate("segs.length") == 0, "landed on the empty board"
        assert "ask=" not in page.evaluate("location.hash")
        assert "0 shots" in page.locator("#proposalDiff").inner_text()
    finally:
        path.unlink(missing_ok=True)


def test_next_lands_on_a_waiting_revision_proposal_in_place_and_from_open(page, live_server):
    """I16.0a: Next's `{ask}` target opens the waiting proposal — clicked on the board
    itself (in place, no reload) and from /open (through `/#tool=ask&ask=<job>`). Only
    the href string was tested; the landing that shows the proposal was not."""
    path = _waiting_ask("i16rev", [{"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "w"}])
    try:
        page.reload()
        page.wait_for_selector("#tl .blk")
        nxt = _next_with(page, "ask")
        assert nxt["href"] == "/#tool=ask&ask=i16rev", nxt
        assert not page.locator("#proposal").is_visible()
        page.locator("#flowNext").click()
        page.wait_for_selector("#proposal", state="visible", timeout=10000)
        assert page.evaluate("location.hash") == "#tool=ask"
        assert "1 shots" in page.locator("#proposalDiff").inner_text()

        page.goto(live_server + "/open")
        _next_with(page, "ask")
        page.locator("#flowNext").click()
        page.wait_for_selector("#tl .blk")
        page.wait_for_selector("#proposal", state="visible", timeout=12000)
        assert "ask=" not in page.evaluate("location.hash")
    finally:
        path.unlink(missing_ok=True)


# ------------------------------------------------------------ M16 · the board's frame

def test_the_monitor_at_rest_shows_the_frame_at_the_playhead_with_a_big_play(page):
    """INTAKE M16 I16.1: the picture at rest was a black box, with a help line under the
    transport. Now it is the frame under the playhead (the poster endpoint) with a big
    ▶, and the help line is gone (the keys are on ?)."""
    poster = page.locator("#monPoster")
    page.wait_for_function("!document.querySelector('#monPoster').hidden", timeout=5000)
    src = poster.get_attribute("src")
    assert src.startswith("/media/poster/CLIP_A") and src.endswith("t=1.00"), src
    page.wait_for_function("document.querySelector('#monPoster').naturalWidth > 0", timeout=10000)
    assert page.locator("#bigPlay").is_visible()
    assert "click a shot below" not in page.locator("#player").inner_text()
    # the playhead moves while nothing plays: the still follows it, into shot 2
    page.evaluate("tl.setPlayhead(3.0)")
    page.wait_for_function(
        "(document.querySelector('#monPoster').getAttribute('src') || '').includes('CLIP_B')",
        timeout=5000)
    # playing: the ▶ goes and the live picture replaces the still
    page.locator("#playCut").click()
    page.wait_for_function("player.playing", timeout=10000)
    assert not page.locator("#bigPlay").is_visible()
    assert not poster.is_visible()
    page.locator("#playCut").click()
    page.wait_for_function("!player.playing", timeout=5000)
    assert page.locator("#bigPlay").is_visible()
    assert page.locator("#playCut").inner_text() == "▶ Play"


def test_a_proposed_cut_plays_before_it_is_accepted(page):
    """INTAKE M16 I16.4: a proposal was a text diff with Accept and Discard — read, not
    watched. ▶ Play it plays the proposed cut in the monitor from its ghost lane; the cut
    on the board is untouched until Accept, and the readable diff stays."""
    from roughcut import config, inference

    class Scripted:
        name = "scripted"

        def complete(self, request):
            text = json.dumps({
                "segments": [{"clip": "CLIP_C.MP4", "in": 0.5, "out": 4.0,
                              "why": "brought in per the note"}],
                "notes": "replaced the opening"})
            model = config.model_for(request.role)
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend="scripted", model=model,
                                    projected_usd=0.0001, latency_ms=1, raw=text)

    inference.set_backend(Scripted())
    inference.reset_spend()
    try:
        before = page.evaluate("JSON.stringify(segs)")
        page.evaluate("dock.open('ask')")
        page.locator("#note").fill("open on the other clip")
        assert_priced(page, "#ask", "Ask for a change", "full")
        page.locator("#ask").click()
        page.wait_for_selector("#proposal:visible", timeout=30000)
        assert "CLIP_C" in page.locator("#proposalDiff").inner_text()   # the diff stays
        buttons = page.eval_on_selector_all("#proposal button", "els => els.map(e => e.textContent)")
        assert buttons == ["▶ Play it", "Accept", "Discard"], buttons
        page.wait_for_function("window.tlLanes && tlLanes.ghost()", timeout=8000)
        page.locator("#playProposal").click()
        page.wait_for_function(
            "liveVideo().dataset.src.includes('CLIP_C') && !liveVideo().paused", timeout=10000)
        assert "proposal" in page.locator("#playingWhat").inner_text()
        assert page.evaluate("JSON.stringify(segs)") == before   # watched, not applied
        page.locator("#rejectProposal").click()
    finally:
        inference.set_backend(None)


def test_sound_is_one_line_and_dip_under_talk(page, project):
    """INTAKE M16 I16.4: the Sound tool was a MUSIC heading, a 0–24 dB slider and its
    label, and three hint paragraphs. Now: the track, one line when it loops under a
    longer cut, "dip under talk: off / a little / a lot" (0 / 6 / 12 dB — the duck the
    render applies), and the fades behind "more"."""
    page.evaluate("dock.open('sound')")
    assert page.locator("#musicPanel h2").count() == 0
    page.select_option("#musicTrack", "music/bed.wav")      # 2 s under a 4 s cut
    page.wait_for_function(
        "document.querySelector('#saveState').textContent.startsWith('saved')", timeout=8000)
    assert page.locator("#musicHint").inner_text() == "loops once at 0:02 under a 0:04 cut"
    options = page.eval_on_selector_all("#duck option", "els => els.map(e => [e.value, e.textContent])")
    assert options == [["0", "off"], ["6", "a little"], ["12", "a lot"]], options
    assert page.input_value("#duck") == "12"
    assert not page.locator("#fadeIn").is_visible(), "the fades are behind more"

    def disk():
        return json.loads(Path(project["edl"]).read_text(encoding="utf-8"))["effects_music"]

    page.select_option("#duck", "6")
    for _ in range(50):
        if disk()["duck_db"] == 6:
            break
        time.sleep(0.1)
    assert disk()["duck"] is True and disk()["duck_db"] == 6
    page.select_option("#duck", "0")
    for _ in range(50):
        if disk()["duck"] is False:
            break
        time.sleep(0.1)
    assert disk()["duck"] is False
    page.select_option("#musicTrack", "")


def test_roughcut_refresh_repaints_the_cut_in_place(page):
    """INTAKE M16 C5: a change made on the server (an effect's edit accepted) used to
    reload the page. window.roughcutRefresh() reads the cut back and repaints the
    timeline and the inspector in place, keeping the selection and the playhead."""
    page.evaluate("window.__stay = 1")
    select_shot(page, 1)
    keep = page.evaluate("tl.idAt(1)")
    page.evaluate("tl.setPlayhead(2.5)")
    page.evaluate("""async () => {
      const s = tl.forSave().map((x) => ({...x}));      // the cut's ids, as on disk
      s[1].why = 'changed on the server';
      await fetch('/api/project', {method: 'PUT', headers: {'content-type': 'application/json'},
        body: JSON.stringify({segments: s, story: ''})});
    }""")
    assert page.evaluate("segs[1].why") == "second"
    page.evaluate("roughcutRefresh()")
    page.wait_for_function("segs[1].why === 'changed on the server'", timeout=5000)
    assert page.evaluate("window.__stay") == 1, "the page did not reload"
    assert page.evaluate("[...tl.state.sel]") == [keep]
    assert page.evaluate("tl.state.playhead") == pytest.approx(2.5, abs=0.01)
    page.wait_for_function(
        "document.querySelector('#inspector .why').textContent === 'changed on the server'",
        timeout=5000)
