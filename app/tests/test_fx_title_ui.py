"""Wave 23, TITLE — the card, in a real browser: what the editor sees of a title.

Under test: `app/static/fx.js` — the frame check's line on the card ("✗ the words run
outside the frame", with Fit to frame), the hand controls (words, size, place, in / out:
no model call), the title-safe guides on the monitor while one is used, and no sound
check on an effect with no sound. Every assertion about an effect is made against what the
server holds (`GET /api/fx`). Same fixture pattern as test_fx_ui.py (its server, its
browser); only the designer's answer is a title.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import fx  # noqa: E402
from test_fx_ui import (  # noqa: E402,F401  (fixtures: the server and the browser)
    _silent_wav, api, effects, fake_backend, live_server, open_design, open_fx, page, wait_effect,
)

OFF_FRAME = {
    "name": "Copper title", "why": "the title", "events": [{"t": 1.5, "x": 0.18, "y": 0.78}],
    "overlay": {"duration": 2.0, "size": 1.0, "anim": {}, "shapes": [
        {"type": "text", "text": "COPPER 2026", "at": [0, 0], "h": 0.13, "bold": True, "color": "#ffffff"}]},
    "sound": None,
}
calls = {"design": 0, "revise": 0}
import threading  # noqa: E402
GATE = {"on": None, "inside": threading.Event()}      # a design waits here when a test sets GATE["on"]


@pytest.fixture(scope="module", autouse=True)
def stubbed_fx():
    """The designer answers an off-frame title for any note and never fixes it; the proof's
    checklist is the suite's stub, with a failed sound check the effect has no sound for."""
    def design(note, seg, clip, sidecar, *, reference=None, place=False, proxy=None,
               workdir=None, segments=None, clips=None, window=None):
        calls["design"] += 1
        hook = getattr(fx._WATCH, "on", None)
        if hook:
            hook("the design call", False)
        if GATE["on"] is not None:
            GATE["inside"].set()
            GATE["on"].wait(20)
            if hook:
                hook("the design call", True)
        e = json.loads(json.dumps(OFF_FRAME))
        e.update(shot=seg["id"], clip=seg["clip"], note=note, created="2026-11-11T09:00:00",
                 history=[{"note": note, "at": "2026-11-11T09:00:00"}])
        return fx.validate_effect(e, segments or [seg], clips)

    def revise(effect, note, segments, clips=None):
        calls["revise"] += 1
        e = json.loads(json.dumps(effect))
        e["note"] = note
        return fx.validate_effect(e, segments, clips)

    def verify(effect, seg, *, onset=None, hz=10, part=None, base=None, impact=True):
        checks = [{"key": "in_shot", "label": "", "ok": True, "detail": ""},
                  {"key": "audio_landed", "label": "the sound is in the proof", "ok": False,
                   "detail": "no rise"}]
        return {"ok": False, "at": "x", "checks": checks}

    mp = pytest.MonkeyPatch()
    mp.setattr(fx, "design", design)
    mp.setattr(fx, "revise", revise)
    mp.setattr(fx, "synth_sound", lambda sound, out, *, sr=48000: _silent_wav(Path(out), sound["duration"], sr))
    mp.setattr(fx, "part_graph", lambda effects, w, h, fps, workdir, *, vin="vbase", ain="abase": ([], "", vin, ain))
    mp.setattr(fx, "verify", verify)
    yield
    mp.undo()


def _design(page) -> dict:
    open_fx(page)
    open_design(page)
    page.locator("#fxNote").fill("a COPPER 2026 title")
    page.locator("#fxDesign").click()
    page.wait_for_selector("#fx .fxcard", timeout=20000)
    return effects(page)[-1]


def test_a_title_outside_the_frame_says_so_on_the_card_and_fit_fixes_it_with_no_model(page):
    e = _design(page)
    page.wait_for_selector("#fx .fxcheck.bad", timeout=20000)
    assert "outside the frame" in page.locator("#fx .fxcheck.bad").inner_text()
    assert e["bounds"]["ok"] is False
    before = dict(calls)
    page.locator("#fx button[data-act=fit]").first.click()
    got = wait_effect(page, e["id"], "e => e.bounds && e.bounds.ok === true && e.bounds.by_hand === true")
    assert got["overlay"]["size"] < 1.0 or got["events"][0]["x"] > 0.18
    assert calls == before                                  # not one model call
    page.wait_for_function("!document.querySelector('#fx .fxcheck.bad') || "
                           "!/outside the frame/.test(document.querySelector('#fx .fxcheck.bad').textContent)")
    assert page.evaluate("!!fx.state.guides") is True      # the title-safe guides are on the monitor


def test_the_hand_controls_change_the_effect_and_recheck_at_once(page):
    e = _design(page)
    page.locator("#fx button[data-act=hand]").first.click()
    page.wait_for_selector("#fx .fxhand")
    before = dict(calls)
    words = page.locator("#fx .fxhand textarea[data-hand=words]")
    words.fill("Copper")
    words.dispatch_event("change")
    wait_effect(page, e["id"], "e => e.overlay.shapes.some(s => s.text === 'Copper')")
    page.wait_for_selector("#fx .fxhand")
    page.locator("#fx .fxhand button[data-hand-nudge='1,0']").click()
    got = wait_effect(page, e["id"], "e => e.events[0].x > 0.18")
    assert abs(got["events"][0]["x"] - 0.19) < 0.001
    page.locator("#fx .fxhand input[data-hand=text_height]").evaluate(
        "el => { el.value = 5; el.dispatchEvent(new Event('change', {bubbles: true})); }")
    got = wait_effect(page, e["id"], "e => e.bounds && Math.abs(e.bounds.share - 0.05) < 0.004")
    assert got["bounds"]["by_hand"] is True and got["bounds"]["ok"] is True     # a short word, small: inside
    page.locator("#fx .fxhand input[data-hand=length]").evaluate(
        "el => { el.value = 3.5; el.dispatchEvent(new Event('change', {bubbles: true})); }")
    wait_effect(page, e["id"], "e => e.overlay.duration === 3.5")
    assert calls == before


def test_a_text_effect_with_no_sound_never_says_the_sound_is_not_heard(page):
    e = _design(page)
    assert e["sound"] is None
    page.locator("#fx button[data-act=hand]").first.click()      # (the checklist below reads the stub's failed sound row)
    page.locator("#fx button[data-act=fit]").first.click()
    wait_effect(page, e["id"], "e => e.bounds && e.bounds.ok === true")
    page.wait_for_function("!document.querySelector('#fx .fxcheck.wait')", timeout=20000)
    text = page.locator("#fx").inner_text()
    assert "sound is not heard" not in text
    page.locator("#fx button[data-act=why]").first.click()
    assert "sound" not in page.locator("#fx .fxchecks").first.inner_text().lower()


def test_an_accepted_title_says_it_is_inside_the_frame_and_never_stays_silent(page):
    e = _design(page)
    page.locator("#fx button[data-act=fit]").first.click()
    wait_effect(page, e["id"], "e => e.bounds && e.bounds.ok === true")
    page.wait_for_function("(document.querySelector('#fx .fxcheck') || {}).textContent && "
                           "document.querySelector('#fx .fxcheck').textContent.includes('inside the frame')", timeout=20000)
    page.locator("#fx button[data-act=accept]").first.click()
    wait_effect(page, e["id"], "e => e.status === 'accepted'")
    page.wait_for_function("(document.querySelector('#fx .fxcheck') || {}).textContent === '✓ inside the frame'", timeout=20000)


def _accept_fitted(page):
    e = _design(page)
    page.locator("#fx button[data-act=fit]").first.click()
    wait_effect(page, e["id"], "e => e.bounds && e.bounds.ok === true")
    page.locator("#fx button[data-act=accept]").first.click()
    wait_effect(page, e["id"], "e => e.status === 'accepted'")
    page.locator("#fx button[data-act=hand]").first.click()
    page.wait_for_selector("#fx .fxhand")
    return e


def _fire(page, sel, value):
    page.locator(sel).evaluate("(el, v) => { el.value = v; el.dispatchEvent(new Event('change', {bubbles: true})); }", value)


def test_each_hand_change_to_a_title_is_one_undo_step_and_survives_a_reload(page):
    e = _accept_fitted(page)
    fid = e["id"]
    _fire(page, "#fx .fxhand input[data-hand=text_height]", "5")
    wait_effect(page, fid, "e => e.bounds && Math.abs(e.bounds.share - 0.05) < 0.004")
    page.wait_for_selector("#fx .fxhand")
    _fire(page, "#fx .fxhand input[data-hand=x]", "40")
    wait_effect(page, fid, "e => Math.abs(e.events[0].x - 0.4) < 0.001")
    page.wait_for_selector("#fx .fxhand")
    w = page.locator("#fx .fxhand textarea[data-hand=words]")
    w.fill("Copper")
    w.dispatch_event("change")
    wait_effect(page, fid, "e => e.overlay.shapes.some(s => s.text === 'Copper')")
    assert "title words" in (page.locator("#undo").get_attribute("title") or "")
    page.evaluate("document.activeElement && document.activeElement.blur()")
    page.keyboard.press("Control+z")                                   # the words only
    wait_effect(page, fid, "e => e.overlay.shapes.every(s => s.text === 'COPPER 2026')")
    got = wait_effect(page, fid, "e => e.status === 'accepted'")     # the title is still there
    assert abs(got["bounds"]["share"] - 0.05) < 0.004 and abs(got["events"][0]["x"] - 0.4) < 0.001
    assert "title place" in (page.locator("#undo").get_attribute("title") or "")
    page.keyboard.press("Control+z")                                   # the place only
    got = wait_effect(page, fid, "e => Math.abs(e.events[0].x - 0.4) > 0.01")
    assert abs(got["bounds"]["share"] - 0.05) < 0.004
    page.keyboard.press("Control+Shift+z")                             # redo puts the place back
    wait_effect(page, fid, "e => Math.abs(e.events[0].x - 0.4) < 0.001")
    # past a reload: the step is still on the board's stack and still goes back on its own
    page.wait_for_timeout(800)
    page.reload()
    page.wait_for_selector("#tl .blk")
    page.wait_for_function("window.fx && fx.ready")
    page.wait_for_function("(document.querySelector('#undo').getAttribute('title') || '').includes('title place')", timeout=10000)
    page.evaluate("document.activeElement && document.activeElement.blur()")
    page.keyboard.press("Control+z")
    got = wait_effect(page, fid, "e => Math.abs(e.events[0].x - 0.4) > 0.01")
    assert got["status"] == "accepted" and abs(got["bounds"]["share"] - 0.05) < 0.004


def test_a_number_box_that_cannot_read_what_was_typed_keeps_its_value_and_says_why(page):
    e = _accept_fitted(page)
    fid = e["id"]
    before = wait_effect(page, fid, "e => true")["events"][0]
    box = page.locator("#fx .fxhand input[data-hand=y]")
    good = box.input_value()
    for junk in ("", "500", "-3"):
        _fire(page, "#fx .fxhand input[data-hand=y]", junk)
        assert box.input_value() == good                                     # the last good value is kept
        assert "bad" in (box.get_attribute("class") or "")
        assert "a number from 0 to 100" in page.locator("#fx .fxhandmsg").inner_text()
    assert wait_effect(page, fid, "e => true")["events"][0]["y"] == before["y"]    # nothing was sent
    _fire(page, "#fx .fxhand input[data-hand=y]", "60")
    wait_effect(page, fid, "e => Math.abs(e.events[0].y - 0.6) < 0.001")
    page.wait_for_selector("#fx .fxhand")
    assert page.locator("#fx .fxhandmsg").inner_text() == ""
    for k, junk, rule in (("start", "-1", "seconds"), ("length", "0", "0.1 to 30")):
        _fire(page, f"#fx .fxhand input[data-hand={k}]", junk)
        assert rule in page.locator("#fx .fxhandmsg").inner_text()


def test_a_title_being_designed_has_a_stop_on_its_card_and_its_pill_and_leaves_nothing(page):
    GATE["on"], GATE["inside"] = threading.Event(), threading.Event()
    try:
        open_fx(page)
        open_design(page)
        page.locator("#fxNote").fill("a COPPER 2026 title")
        page.locator("#fxDesign").click()
        page.wait_for_selector("#fx #fxStop", timeout=10000)                   # from the first second
        assert GATE["inside"].wait(10)
        page.wait_for_selector("#progress .job[data-kind=fx] .jx", timeout=10000)    # and on the header's pill
        page.locator("#fx #fxStop").click()
        page.wait_for_function("(document.querySelector('#fx .fxstopped') || {}).textContent === 'stopped: no title was added.'",
                               timeout=10000)
        GATE["on"].set()
        page.wait_for_timeout(800)
        assert effects(page) == []
        assert page.locator("#fx #fxStop").count() == 0
    finally:
        GATE["on"].set()
        GATE["on"] = None


def test_the_hand_fields_never_move_when_the_check_line_changes(page):
    e = _design(page)                                         # off the frame: the check line says so
    page.locator("#fx button[data-act=hand]").first.click()
    page.wait_for_selector("#fx .fxhand")
    page.wait_for_selector("#fx .fxcheck.bad", timeout=20000)
    ys = []
    for _ in range(2):
        ys.append(page.locator("#fx .fxhand input[data-hand=x]").bounding_box()["y"])
        h = page.locator("#fx .fxcheck").first.bounding_box()["height"]
        page.locator("#fx button[data-act=fit]").first.click()              # ✗ becomes ✓ / checking…
        wait_effect(page, e["id"], "e => e.bounds && e.bounds.ok === true")
        page.wait_for_timeout(400)
        assert page.locator("#fx .fxcheck").first.bounding_box()["height"] == h
        break
    ys.append(page.locator("#fx .fxhand input[data-hand=x]").bounding_box()["y"])
    assert abs(ys[0] - ys[-1]) < 0.5, ys


def test_the_card_says_where_the_title_really_is_in_words_and_one_size_scale(page):
    e = _design(page)
    page.locator("#fx button[data-act=fit]").first.click()
    got = wait_effect(page, e["id"], "e => e.bounds && e.bounds.ok === true && !!e.bounds.place")
    text = page.locator("#fx .fxreads").first.inner_text()
    assert got["bounds"]["place"] in text and "% of the frame's height" in text
