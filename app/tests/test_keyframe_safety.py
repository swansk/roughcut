"""Keyframe safety like Premiere's (verdict 7, punch 7; wave 10 lane CRAFT).

Judge 7: one stopwatch click on a keyed property threw its keys away without a word.
Premiere asks first — "This action will delete existing keyframes. Do you want to
continue?" — and so does the board now, in a modal of its own (never window.confirm):

  * the stopwatch off on a keyed Opacity shows the warning, and nothing changes yet;
  * Cancel (and Esc) keeps every key, with no undo entry;
  * OK removes them as ONE undo entry, the value at the playhead becoming the static one;
  * one Ctrl+Z brings every key back;
  * a property with no keys turns on without a question, and a locked track refuses at
    once rather than asking a question it will then refuse.

The server and the page come from test_keyframes_ui (same seed, same helpers).
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")

from test_keyframes_ui import (  # noqa: E402,F401  (fixtures: live_server, page)
    keys, live_server, page, seek, select_first, type_value, undo_title)

ASK = "#ecConfirm"
SW = "#inspector .ec-sw[data-sw=opacity]"


def _keyed(page) -> list:
    """Opacity 100 at the shot's first frame, 20 at 1.5 s: two keys."""
    select_first(page)
    page.locator(SW).click()
    assert page.locator(ASK).count() == 0 or not page.locator(ASK).is_visible(), \
        "turning a stopwatch ON never asks"
    seek(page, 1.5)
    type_value(page, "opacity", 20)
    k = keys(page, "opacity")
    assert len(k) == 2, k
    return k


def test_stopwatch_off_asks_and_cancel_keeps_the_keys(page):
    k = _keyed(page)
    before = undo_title(page)
    seek(page, 0.75)
    page.locator(SW).click()
    dlg = page.locator(ASK)
    dlg.wait_for(state="visible", timeout=3000)
    assert page.evaluate("document.querySelector('#ecConfirm').open") is True
    assert page.evaluate("document.querySelector('#ecConfirm').matches(':modal')") is True
    text = dlg.inner_text()
    assert "This action will delete existing keyframes. Do you want to continue?" in text, text
    assert "Opacity: 2 keyframes" in text, text
    # the board's own buttons, OK the default
    assert dlg.locator("[data-ans=ok]").inner_text() == "OK"
    assert dlg.locator("[data-ans=cancel]").inner_text() == "Cancel"
    assert page.evaluate("document.activeElement && document.activeElement.dataset.ans") == "ok"
    # nothing changed while it asks
    assert keys(page, "opacity") == k
    assert undo_title(page) == before

    dlg.locator("[data-ans=cancel]").click()
    assert not dlg.is_visible()
    assert keys(page, "opacity") == k, "Cancel keeps the keys"
    assert undo_title(page) == before, "Cancel is no undo entry"
    assert page.locator(SW).get_attribute("aria-pressed") == "true"

    # Esc is Cancel too
    page.locator(SW).click()
    dlg.wait_for(state="visible", timeout=3000)
    page.keyboard.press("Escape")
    assert not dlg.is_visible()
    assert keys(page, "opacity") == k
    assert undo_title(page) == before


def test_ok_removes_the_keys_as_one_undo_entry(page):
    k = _keyed(page)
    seek(page, 0.75)                              # halfway: 100 → 20 reads 60 here
    want = page.evaluate("effects.value('opacity')")
    assert want == pytest.approx(60, abs=1.5), want
    page.locator(SW).click()
    page.locator(ASK).wait_for(state="visible", timeout=3000)
    page.locator(f"{ASK} [data-ans=ok]").click()
    assert not page.locator(ASK).is_visible()
    assert keys(page, "opacity") == [], "OK removes them"
    assert undo_title(page).startswith("undo: stop animating opacity"), undo_title(page)
    assert page.locator(SW).get_attribute("aria-pressed") == "false"
    # the value at the playhead is now the static value, everywhere in the shot
    assert page.evaluate("effects.value('opacity')") == pytest.approx(want, abs=0.01)
    assert page.evaluate("segs[0].controls") == {"opacity": pytest.approx(want, abs=0.01)}
    seek(page, 1.8)
    assert page.evaluate("effects.value('opacity')") == pytest.approx(want, abs=0.01)

    # ONE Ctrl+Z brings every key back
    page.keyboard.press("Control+z")
    assert keys(page, "opacity") == k, "one undo restores the keys"
    assert undo_title(page).startswith("undo: opacity"), undo_title(page)   # the typed key, below it
    page.keyboard.press("Control+Shift+z")
    assert keys(page, "opacity") == []


def test_the_api_asks_too_and_a_locked_track_refuses_without_asking(page):
    _keyed(page)
    page.evaluate("effects.animate('opacity')")
    page.locator(ASK).wait_for(state="visible", timeout=3000)
    page.locator(f"{ASK} [data-ans=cancel]").click()
    assert len(keys(page, "opacity")) == 2

    page.evaluate("tl.setTrack('V1', 'lock', true)")
    page.locator(SW).click()
    page.wait_for_function("document.querySelector('#toast').textContent.includes('V1 is locked')",
                           timeout=3000)
    assert not page.locator(ASK).is_visible(), "a locked track never asks"
    assert len(keys(page, "opacity")) == 2
    page.evaluate("tl.setTrack('V1', 'lock', false)")
