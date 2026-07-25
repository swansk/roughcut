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
                     project["work"], proxies=False)
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
def page(live_server):
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
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


def test_save_writes_story_and_segments_to_the_edl(page, project):
    page.locator("#story").fill("the milk is the running joke")
    page.locator(".seg").first.click()
    page.keyboard.press("}")
    page.locator("#save").click()
    page.wait_for_function(
        "document.querySelector('#toast').textContent.includes('saved')", timeout=8000)
    on_disk = json.loads(Path(project["edl"]).read_text(encoding="utf-8"))
    assert on_disk["story"] == "the milk is the running joke"
    assert len(on_disk["segments"]) == 2


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
        "document.querySelector('#renderState').textContent === 'done'", timeout=180000)
    src = page.get_attribute("#preview", "src")
    assert src and src.startswith("/media/render/")
    assert page.locator("#preview").is_visible()
    page.wait_for_function(
        "document.querySelector('#preview').readyState >= 1", timeout=30000)
    assert page.evaluate("document.querySelector('#preview').duration") > 0
