"""The Effects bin, as Premiere has it (verdict 8 punch 5; verdict 7 punch 6).

The Project group's Effects tab holds Premiere's transitions in Premiere's folders —
Audio Transitions › Crossfade (Constant Gain, Constant Power, Exponential Fade) and Video
Transitions › Dissolve (Cross Dissolve, Dip to Black, Dip to White, Film Dissolve) — with
a search box that filters them. The AI effect designer is "Designed Effects". Each test
drags with the real mouse (Playwright's down / move / up, Chromium's own drag and drop):

  * an item dropped on an edit is centred on it (1 s), one undo; Dip to Black's frame on
    the edit is black in the Program (luma <= 5 %);
  * dropped on a clip's start (the sequence's first frame) or end it is one-sided: the
    Program starts at black;
  * Constant Gain's levels on the edit are 0.5 each side (-6 dB);
  * right-click on a transition → "Set Default Transition": Ctrl+D lays that kind;
  * Ctrl+D on an edit with no handles lays it anyway and says "insufficient media —
    repeated frames", hatched.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from test_transitions import (CUT, EDL, board, live_server, on_disk, proj,  # noqa: F401
                              program_rgb, saved)

ITEMS = ["Constant Gain", "Constant Power", "Exponential Fade",
         "Cross Dissolve", "Dip to Black", "Dip to White", "Film Dissolve"]


def luma(c) -> float:
    return float(0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2])


def open_bin(pg):
    pg.locator(".ws-tab[data-tool=effects]").click()
    pg.wait_for_selector("#fxbin .fxb-item")


def drag(pg, name: str, x: float, y: float):
    """A real mouse drag of the bin's item `name` to client (x, y)."""
    it = pg.locator("#fxbin .fxb-item", has_text=name).first.bounding_box()
    pg.mouse.move(it["x"] + it["width"] / 2, it["y"] + it["height"] / 2)
    pg.mouse.down()
    pg.mouse.move(it["x"] + it["width"] / 2 + 20, it["y"] + it["height"] / 2, steps=4)
    pg.mouse.move(x, y, steps=12)
    pg.mouse.up()


def v1_at(pg, t: float) -> tuple[float, float]:
    """Client (x, y) on V1 over film second t."""
    x = pg.evaluate(f"tl.timeToX({t}) - tl.el.view.scrollLeft + tl.el.view.getBoundingClientRect().left")
    lane = pg.locator("#tl .tl-lane[data-lane=V1]").bounding_box()
    return x, lane["y"] + lane["height"] / 2


def test_the_bin_has_premieres_folders_and_its_search_filters_them(board):
    pg = board
    tabs = pg.eval_on_selector_all(".ws-tab[data-tool]", "els => els.map(e => e.textContent.trim())")
    assert tabs[:3] == ["Project", "Selects", "Effects"] and "Designed Effects" in tabs, tabs
    open_bin(pg)
    folders = [t.strip() for t in pg.locator("#fxbin .fxb-folder .nm").all_inner_texts()]
    assert folders == ["Audio Transitions", "Crossfade", "Video Transitions", "Dissolve"], folders
    assert [t.strip() for t in pg.locator("#fxbin .fxb-item .nm").all_inner_texts()] == ITEMS
    # the defaults are outlined
    assert sorted(pg.eval_on_selector_all("#fxbin .fxb-item.def", "els => els.map(e => e.dataset.kind)")) == \
        ["audio_crossfade", "cross_dissolve"]
    q = pg.locator("#fxbinQ")
    q.fill("dip")
    assert [t.strip() for t in pg.locator("#fxbin .fxb-item .nm").all_inner_texts()] == ["Dip to Black", "Dip to White"]
    assert [t.strip() for t in pg.locator("#fxbin .fxb-folder .nm").all_inner_texts()] == ["Video Transitions", "Dissolve"]
    q.fill("FILM")                            # case does not matter
    assert [t.strip() for t in pg.locator("#fxbin .fxb-item .nm").all_inner_texts()] == ["Film Dissolve"]
    q.fill("crossfade")                       # a folder's name shows the folder's items
    assert len(pg.locator("#fxbin .fxb-item").all()) == 3
    q.fill("wipe")
    assert pg.locator("#fxbin .fxb-item").count() == 0
    assert "no effect matches" in pg.locator("#fxbin").inner_text()
    q.fill("")
    assert pg.locator("#fxbin .fxb-item").count() == 7
    # a folder folds
    pg.locator("#fxbin .fxb-folder", has_text="Audio Transitions").click()
    assert pg.locator("#fxbin .fxb-item").count() == 4
    pg.locator("#fxbin .fxb-folder", has_text="Audio Transitions").click()
    # the Window menu names both
    pg.locator("#menubar .mtop", has_text="Window").click()
    items = [t.strip() for t in pg.locator("#menubar .menu.open .mdrop button").all_inner_texts()]
    assert "Effects" in items and "Designed Effects" in items, items
    pg.keyboard.press("Escape")


def test_dip_to_black_dragged_onto_an_edit_is_black_on_it(board, proj):
    pg = board
    open_bin(pg)
    x, y = v1_at(pg, CUT)
    drag(pg, "Dip to Black", x + 3, y)
    tr = pg.evaluate("tl.transitions()")
    assert tr == [{"a": "s1", "b": "s2", "kind": "dip_to_black", "dur": 1, "align": "center"}], tr
    assert pg.locator("#tl .tl-trans[data-kind=dip_to_black] .lbl").inner_text() == "Dip to Black"
    assert pg.evaluate("document.querySelector('#undo').title").startswith("undo: Dip to Black")
    edl = saved(pg, proj, lambda e: e.get("transitions"))
    assert edl["transitions"][0]["kind"] == "dip_to_black"
    # the Program: black on the edit's frame, half the picture a quarter either side
    pg.evaluate("tl.seek(1.0)")
    pg.wait_for_timeout(700)
    red = program_rgb(pg)
    pg.evaluate(f"tl.seek({CUT})")
    pg.wait_for_timeout(500)
    got = program_rgb(pg)
    assert luma(got) <= 0.05 * 255, got
    assert pg.evaluate("Number(document.querySelector('#pvDip').style.opacity)") == 1
    pg.evaluate(f"tl.seek({CUT - 0.25})")
    pg.wait_for_timeout(600)
    q = program_rgb(pg)
    assert np.all(np.abs(q - 0.5 * red) <= 0.1 * 255), (q, red)
    # no follower is drawn for a dip
    assert pg.evaluate("!prog.tx || [prog.tx.in, prog.tx.out].every(e => e.style.visibility === 'hidden')")
    # one drop, one undo
    pg.keyboard.press("Control+z")
    assert pg.evaluate("tl.transitions()") == []
    pg.evaluate("tl.seek(1.9)")
    pg.wait_for_timeout(300)
    assert pg.evaluate("Number(document.querySelector('#pvDip').style.opacity)") == 0


def test_a_drop_on_the_first_clips_start_fades_from_black(board, proj):
    pg = board
    open_bin(pg)
    x, y = v1_at(pg, 0.15)
    drag(pg, "Cross Dissolve", x, y)
    tr = pg.evaluate("tl.transitions()")
    assert tr == [{"a": None, "b": "s1", "kind": "cross_dissolve", "dur": 1, "align": "start"}], tr
    box = pg.locator("#tl .tl-trans.side").bounding_box()
    zoom = pg.evaluate("tl.state.zoom")
    x0, _ = v1_at(pg, 0)
    assert abs(box["x"] - x0) < 2 and abs(box["width"] - zoom) < 2, (box, x0, zoom)
    edl = saved(pg, proj, lambda e: e.get("transitions"))
    assert edl["transitions"] == [{"a": None, "b": "s1", "kind": "cross_dissolve", "dur": 1.0,
                                   "align": "start"}]
    pg.evaluate("tl.seek(0)")
    pg.wait_for_timeout(500)
    assert luma(program_rgb(pg)) <= 0.05 * 255
    # and on the last clip's end, Dip to White: a fade to white
    x, y = v1_at(pg, 3.9)
    drag(pg, "Dip to White", x, y)
    assert [(t["kind"], t["align"]) for t in pg.evaluate("tl.transitions()")] == [
        ("cross_dissolve", "start"), ("dip_to_white", "end")]
    pg.evaluate("tl.seek(3.99)")
    pg.wait_for_timeout(500)
    assert np.all(program_rgb(pg) > 225)
    pg.keyboard.press("Control+z")
    assert len(pg.evaluate("tl.transitions()")) == 1


def test_constant_gain_is_half_each_side_on_the_edit(board):
    pg = board
    open_bin(pg)
    x, y = v1_at(pg, CUT)
    a1 = pg.locator("#tl .tl-track[data-track=A1], #tl [data-lane=A1]").first.bounding_box()
    drag(pg, "Constant Gain", x + 2, a1["y"] + a1["height"] / 2 if a1 else y)
    assert [t["kind"] for t in pg.evaluate("tl.transitions()")] == ["constant_gain"]
    assert pg.locator("#tl .tl-trans.audio").count() == 1
    pg.evaluate(f"tl.seek({CUT})")
    pg.wait_for_timeout(500)
    lv = pg.evaluate("({live: rcAudio.levelOf(liveVideo()), out: rcAudio.levelOf(prog.tx.out)})")
    assert abs(lv["live"] - 0.5) < 0.02 and abs(lv["out"] - 0.5) < 0.02, lv


def test_set_default_transition_then_ctrl_d_lays_it(board):
    pg = board
    pg.evaluate("tl.setDefaultTransition('cross_dissolve')")
    open_bin(pg)
    x, y = v1_at(pg, CUT)
    drag(pg, "Film Dissolve", x, y)
    box = pg.locator("#tl .tl-trans").bounding_box()
    pg.mouse.click(box["x"] + box["width"] * 0.3, box["y"] + box["height"] / 2, button="right")
    menu = pg.locator(".tl-ctx:not([hidden])")
    assert "Set Default Transition" in menu.inner_text()
    menu.locator("button", has_text="Set Default Transition").click()
    assert pg.evaluate("tl.transitionDefaults.video") == "film_dissolve"
    assert pg.locator("#fxbin .fxb-item.def[data-kind=film_dissolve]").count() == 1
    pg.keyboard.press("Delete")
    assert pg.evaluate("tl.transitions()") == []
    pg.evaluate("tl.selectEdit(0)")
    pg.keyboard.press("Control+d")
    assert [t["kind"] for t in pg.evaluate("tl.transitions()")] == ["film_dissolve"]
    # the bin's own right-click: Set Selected as Default Transition (the audio default)
    pg.locator("#fxbin .fxb-item", has_text="Exponential Fade").click(button="right")
    pg.locator("#fxbinMenu button", has_text="Set Selected as Default Transition").click()
    pg.keyboard.press("Shift+d")
    assert sorted(t["kind"] for t in pg.evaluate("tl.transitions()")) == ["exponential_fade", "film_dissolve"]
    # kept per browser
    pg.reload()
    pg.wait_for_function("segs.length === 2 && !!window.tl.applyTransition")
    assert pg.evaluate("tl.transitionDefaults") == {"video": "film_dissolve", "audio": "exponential_fade"}
    pg.evaluate("tl.setDefaultTransition('cross_dissolve'); tl.setDefaultTransition('audio_crossfade')")


def test_ctrl_d_on_a_handle_less_edit_repeats_frames_and_warns(board):
    pg = board
    pg.evaluate("tl.setRange('s2', 0, 2)")             # nothing before B's in
    pg.evaluate("tl.selectEdit(0)")
    pg.keyboard.press("Control+d")
    assert [t["dur"] for t in pg.evaluate("tl.transitions()")] == [1]
    assert "insufficient media — repeated frames" in pg.locator("#toast, .toast").first.inner_text()
    assert pg.locator("#tl .tl-trans.rep").count() == 1
    assert "repeated frames" in pg.locator("#tl .tl-trans").get_attribute("title")
    # the Program holds B's first frame in the window's first half (the follower parked)
    pg.evaluate(f"tl.seek({CUT - 0.25})")
    pg.wait_for_timeout(600)
    st = pg.evaluate("({t: prog.tx.in.currentTime, p: prog.tx.in.paused, w: Number(prog.tx.in.dataset.w)})")
    assert st["t"] < 0.05 and st["p"] and abs(st["w"] - 0.25) < 0.01, st
    assert json.dumps(st)
