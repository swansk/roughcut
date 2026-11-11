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


@pytest.fixture(scope="module", autouse=True)
def stubbed_fx():
    """The designer answers an off-frame title for any note and never fixes it; the proof's
    checklist is the suite's stub, with a failed sound check the effect has no sound for."""
    def design(note, seg, clip, sidecar, *, reference=None, place=False, proxy=None,
               workdir=None, segments=None, clips=None, window=None):
        calls["design"] += 1
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
    page.locator("#fx .fxhand input[data-hand=size]").evaluate(
        "el => { el.value = 0.5; el.dispatchEvent(new Event('change', {bubbles: true})); }")
    got = wait_effect(page, e["id"], "e => e.overlay.size === 0.5")
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
