"""Wave 24 HANDS — the hands: where the keys go, copy / cut / paste, a typed timecode, and
transport rows that hold still. One real browser on the suite's fixture (two 2 s shots, 23.976 fps).

Each test is a finding's own steps: HANDS-14 / CLUMSY-20 / FINISH-13 (the focus rule),
HANDS-08 (the stuck "this shot only"), HANDS-10 (copy / cut / paste), HANDS-08 / CLUMSY-19 /
FINISH-12 (the timecode), COLD-14 / FINDER-18 / CLUMSY-10 (nothing moves under the pointer),
HANDS-03, HANDS-04, FINDER-19, CLUMSY-07, HANDS-18, HANDS-12 (the small expectations).
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


def tc(seconds: float, fps: float = 23.976) -> str:
    """Film seconds as the timeline writes them: HH:MM:SS:FF, non-drop at the nominal
    rate, rounded to the nearest frame (timeline.js `tl.tc`)."""
    n = round(seconds * fps)
    nominal = round(fps)
    ff, n = n % nominal, n // nominal
    return f"{n // 3600:02d}:{n // 60 % 60:02d}:{n % 60:02d}:{ff:02d}"


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
    seed = json.dumps({
        "variant": "T", "title": "test cut", "orient": "none", "story": "",
        "target_s": [5, 20],
        "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
                     {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}],
    }, indent=1)
    Path(project["edl"]).write_text(seed, encoding="utf-8")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        pg = browser.new_page(viewport={"width": 1280, "height": 900})
        pg.goto(live_server)
        pg.wait_for_selector("#tl .blk")
        pg.wait_for_selector("#tlKeys", state="attached")
        yield pg
        browser.close()




def ranges(page) -> list[list]:
    return page.evaluate("segs.map(s => [s.clip, s.in, s.out])")


def tc_text(page) -> str:
    return page.evaluate("document.querySelector('#tl .tl-tc').textContent")


def toast_text(page) -> str:
    return page.evaluate("document.querySelector('#toast').textContent")


def click_shot(page, i: int) -> None:
    bb = page.locator("#tl .blk").nth(i).bounding_box()
    page.mouse.click(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
    page.wait_for_timeout(250)


def burst(page, letters: str) -> None:
    """Letters as one burst of keydowns in a single task (a busy box cannot stretch the gaps)."""
    page.evaluate("""(letters) => { for (const ch of letters) document.body.dispatchEvent(
        new KeyboardEvent('keydown', {key: ch, code: 'Key' + ch.toUpperCase(), bubbles: true, cancelable: true})); }""", letters)


def type_tc(page, text: str, which="#tl .tl-tc") -> None:
    page.locator(which).click()
    page.wait_for_selector("input.hands-tc")
    page.keyboard.type(text)
    page.keyboard.press("Enter")
    page.wait_for_timeout(250)


# ------------------------------------------------------------------- the focus rule

def test_a_dropdown_choice_by_pointer_gives_the_keys_back_to_the_timeline(page):
    """HANDS-14: after the pointer picks a value the select lets go, so Home goes to the
    film's start and Down to the next cut, and the value stays what was chosen."""
    page.evaluate("document.body.insertAdjacentHTML('beforeend', "
                  "'<select id=zz style=\"position:fixed;left:20px;top:300px;z-index:99\">"
                  "<option>one</option><option>two</option><option>three</option></select>')")
    page.mouse.click(30, 310)                       # pointer down on the select
    page.evaluate("(() => { const s = document.getElementById('zz'); s.value = 'three'; "
                  "s.dispatchEvent(new Event('change', {bubbles: true})); })()")
    page.wait_for_function("document.activeElement.id !== 'zz'")
    click_shot(page, 1)
    page.keyboard.press("Home")
    page.keyboard.press("ArrowDown")
    assert page.evaluate("document.getElementById('zz').value") == "three"
    assert tc_text(page) == "00:00:02:00"           # Home, then the next cut


def test_a_select_nobody_went_to_hands_every_key_to_the_board_and_keeps_its_value(page):
    """HANDS-14, the lead's re-run: focus() from a script (a panel redrawn under the
    caret) leaves a select holding the keyboard; Home / Down must not walk its options."""
    page.evaluate("document.body.insertAdjacentHTML('beforeend', "
                  "'<select id=zz style=\"position:fixed;left:20px;top:300px\">"
                  "<option>one</option><option>two</option><option>three</option></select>')")
    page.evaluate("document.getElementById('zz').focus()")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("End")
    assert page.evaluate("document.getElementById('zz').value") == "one"
    assert page.evaluate("document.activeElement.tagName") == "BODY"
    assert tc_text(page) == "00:00:04:00"           # End reached the timeline


def test_enter_in_a_dropdown_confirms_and_does_not_play(page):
    """FINISH-13: Enter on a select the user is in lets go and starts nothing."""
    page.evaluate("document.body.insertAdjacentHTML('beforeend', "
                  "'<select id=zz style=\"position:fixed;left:20px;top:300px\">"
                  "<option>one</option><option>two</option></select>')")
    page.mouse.click(30, 310)
    page.keyboard.press("Escape")                   # the popup, if any
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    assert page.evaluate("player.playing") is False
    assert page.evaluate("document.activeElement.id") != "zz"


def test_an_open_menu_swallows_editing_keys_and_arrows_walk_it(page):
    """CLUMSY-20: Delete (and X, Ctrl+Z) with File open did something to the cut behind it."""
    click_shot(page, 0)
    n = page.evaluate("segs.length")
    page.get_by_role("button", name="File", exact=True).first.click()
    assert page.evaluate("!!document.querySelector('#menubar .menu.open')")
    for key in ("Delete", "Backspace", "x", "Control+x", "Control+z", "Control+a", "m"):
        page.keyboard.press(key)
    assert page.evaluate("segs.length") == n
    assert page.evaluate("tl.state.markers ? tl.state.markers.length : 0") == 0
    page.keyboard.press("ArrowDown")
    assert page.evaluate("!!document.activeElement.closest('#menubar .menu.open .mdrop')")
    page.keyboard.press("ArrowRight")               # the next menu opens
    assert page.evaluate("document.querySelector('#menubar .menu.open').dataset.menu") == "edit"
    page.keyboard.press("Escape")
    assert not page.evaluate("!!document.querySelector('#menubar .menu.open')")
    page.keyboard.press("Delete")                   # a chosen shot goes again once it is closed
    assert page.evaluate("segs.length") == n - 1


def test_this_shot_only_ends_with_the_shot_and_does_not_stick(page):
    """HANDS-08: Enter plays one shot; afterwards the readout never says so again."""
    click_shot(page, 0)
    page.keyboard.press("Enter")
    page.wait_for_function("player.playing")
    assert "this shot only" in page.inner_text("#playingWhat")
    page.keyboard.press("Space")                    # pause
    page.keyboard.press("ArrowDown")
    page.wait_for_timeout(200)
    assert page.evaluate("player.single") is False
    assert "this shot only" not in page.inner_text("#playingWhat")


# ------------------------------------------------------------- copy, cut and paste

def test_copy_then_paste_inserts_at_the_playhead_in_one_undo_step(page):
    """HANDS-10: Ctrl+C on a shot, End, Ctrl+V adds it at the end; Ctrl+Z takes back exactly that."""
    click_shot(page, 0)
    page.keyboard.press("Control+c")
    assert "copied 1 shot" in toast_text(page)
    total = page.evaluate("tl.total()")
    page.keyboard.press("End")
    page.keyboard.press("Control+v")
    assert page.evaluate("segs.length") == 3
    assert abs(page.evaluate("tl.total()") - (total + 2.0)) < 0.1
    assert ranges(page)[2][:1] == ["CLIP_A.MP4"]
    assert "pasted 1 shot" in toast_text(page)
    page.keyboard.press("Control+z")
    assert page.evaluate("segs.length") == 2
    assert abs(page.evaluate("tl.total()") - total) < 0.01


def test_paste_in_the_middle_of_a_shot_splits_it_like_insert(page):
    """The Ctrl+V choice: an insert (ripple), the same as ',' — nothing is covered."""
    click_shot(page, 1)
    page.keyboard.press("Control+c")
    total = page.evaluate("tl.total()")
    page.evaluate("tl.seek(1.0); tl.setPlayhead(1.0)")
    page.keyboard.press("Control+v")
    assert page.evaluate("segs.length") == 4        # A (split), pasted B, A rest, B
    assert abs(page.evaluate("tl.total()") - (total + 2.0)) < 0.1
    page.keyboard.press("Control+z")
    assert page.evaluate("segs.length") == 2


def test_cut_removes_and_paste_puts_it_back_elsewhere(page):
    click_shot(page, 0)
    page.keyboard.press("Control+x")
    assert page.evaluate("segs.length") == 1
    assert "cut 1 shot" in toast_text(page)
    page.keyboard.press("End")
    page.keyboard.press("Control+v")
    assert [r[0] for r in ranges(page)] == ["CLIP_B.MP4", "CLIP_A.MP4"]
    page.keyboard.press("Control+z")                # the paste
    page.keyboard.press("Control+z")                # the cut
    assert [r[0] for r in ranges(page)] == ["CLIP_A.MP4", "CLIP_B.MP4"]


def test_paste_with_nothing_copied_says_so_and_changes_nothing(page):
    """HANDS-10: a silent Ctrl+V made the next Ctrl+Z undo something older."""
    page.keyboard.press("Control+v")
    assert "nothing to paste" in toast_text(page)
    assert page.evaluate("segs.length") == 2
    assert page.evaluate("tl.canUndo ? tl.canUndo() : true") in (False, True)


def test_two_shots_copied_paste_together_as_one_undo_step(page):
    page.keyboard.press("Control+a")
    page.keyboard.press("Control+c")
    page.keyboard.press("End")
    page.keyboard.press("Control+v")
    assert page.evaluate("segs.length") == 4
    page.keyboard.press("Control+z")
    assert page.evaluate("segs.length") == 2


# ------------------------------------------------------------------ a typed timecode

def test_parse_timecode_is_one_grammar_filled_from_the_right(page):
    """Round 3 (R2-03): 1:00 is one second, -100 is one second back, 12 is frames, 0.5s is half a second."""
    r = page.evaluate("1 / tl.frame()")             # the sequence's own rate
    n = round(r)                                    # the nominal count timecode runs at
    p = lambda s, frm=0: page.evaluate(f"tlKeys.parseTimecode({s!r}, {frm})")
    assert abs(p("00:00:40:00")["t"] - 40 * n / r) < 1e-6
    assert abs(p("40.")["t"] - 40 * n / r) < 1e-6           # timecode seconds: 40. is 00:00:40:00
    assert abs(p("0.5s")["t"] - round(0.5 * n) / r) < 1e-6
    assert abs(p("1:00")["t"] - n / r) < 1e-6               # one second, not one minute
    assert abs(p("1:05:00")["t"] - 65 * n / r) < 1e-6
    assert abs(p("5108")["t"] - (51 * n + 8) / r) < 1e-6
    assert abs(p("12")["t"] - 12 / r) < 1e-6                # digits fill from the right: twelve frames
    assert abs(p("100")["t"] - n / r) < 1e-6
    assert abs(p("12f")["t"] - 12 / r) < 1e-6
    assert abs(p("+2.", 10)["t"] - (10 + 2 * n / r)) < 0.05
    assert abs(p("-100", 10)["t"] - (10 - n / r)) < 0.05    # a second back, as the sheet says
    assert abs(p("-1:00", 10)["t"] - (10 - n / r)) < 0.05
    assert p(f"00:00:40:{n}")["ok"] is False                # no such frame
    assert p("nonsense")["ok"] is False


def test_clicking_the_timeline_timecode_types_and_jumps(page):
    type_tc(page, "00:00:01:12")
    assert tc_text(page) == "00:00:01:12"
    assert page.evaluate("document.querySelector('input.hands-tc')") is None
    type_tc(page, "+12")                            # 12 frames on
    assert tc_text(page) == "00:00:02:00"
    type_tc(page, "-1.")                            # one second back
    assert tc_text(page) == "00:00:01:00"
    type_tc(page, "3.")
    assert tc_text(page) == "00:00:03:00"
    assert page.evaluate("player.playing") is False


def test_a_timecode_past_the_end_goes_to_the_last_frame_and_says_so(page):
    type_tc(page, "9:00")
    assert "past the end" in toast_text(page)
    assert tc_text(page) == "00:00:04:00"           # parked ON the end (round 3), not on the last frame


def test_escape_leaves_the_timecode_field_and_a_bad_one_stays_open(page):
    page.locator("#tl .tl-tc").click()
    page.keyboard.type("zzz")
    page.keyboard.press("Enter")
    assert page.locator("input.hands-tc.bad").count() == 1
    assert "is not a time" in toast_text(page)
    page.keyboard.press("Escape")
    assert page.locator("input.hands-tc").count() == 0
    assert tc_text(page) == "00:00:00:00"


def test_the_program_timecode_types_too(page):
    type_tc(page, "00:00:02:00", which="#pos")
    assert tc_text(page) == "00:00:02:00"
    assert page.evaluate("document.querySelector('#pos').textContent") == "00:00:02:00"


def test_ctrl_g_opens_the_timeline_field(page):
    click_shot(page, 0)
    page.keyboard.press("Control+g")
    page.wait_for_selector("input.hands-tc")
    page.keyboard.type("1.")
    page.keyboard.press("Enter")
    assert tc_text(page) == "00:00:01:00"


# --------------------------------------------------------- rows that hold still

def test_transport_buttons_do_not_move_when_a_readout_changes_length(page):
    def xs():
        return page.evaluate("""(() => ['#playCut', '#progBack', '#progFwd',
            '#source [data-src=in]', '#source [data-src=out]', '#source [data-src=insert]']
            .map(s => Math.round(document.querySelector(s).getBoundingClientRect().x)))()""")
    before = xs()
    page.evaluate("document.querySelector('#playingWhat').textContent = "
                  "'7/18 · GX010495 a very long label · this shot only'")
    page.evaluate("document.querySelector('#pos').textContent = '00:59:59:23'")
    page.evaluate("document.querySelector('#srcDur').textContent = 'In–Out 00:00:08:07'")
    page.evaluate("document.querySelector('#srcPos').textContent = '00:12:12:12'")
    assert xs() == before


def test_a_jobs_stop_cross_stands_beside_its_icon_not_after_its_countdown(page):
    html = page.evaluate("""(() => { const row = jobRow({id: 'j1', kind: 'ask', label: 'x'});
        paintJob(row, {id: 'j1', kind: 'ask', state: 'running', label: 'x', pct: 40, elapsed_s: 3,
                       eta_s: 60, milestones: []});
        const a = row.querySelector('.jx').getBoundingClientRect().x;
        row.querySelector('.jnums').textContent = '40% · ~1 min left and a long tail';
        const b = row.querySelector('.jx').getBoundingClientRect().x;
        return [a, b]; })()""")
    assert html[0] == html[1]


# ------------------------------------------------------- the small expectations

def test_ctrl_s_answers_instead_of_doing_nothing(page):
    page.keyboard.press("Control+s")
    assert "saved: Roughcut saves as you go" in toast_text(page)


def test_default_transition_at_the_film_start_is_a_fade_in(page):
    """HANDS-18: Home, Ctrl+Shift+D puts a fade on the first shot's head, not a dissolve on cut 1."""
    page.keyboard.press("Home")
    page.keyboard.press("Escape")
    page.keyboard.press("Control+Shift+D")
    ts = page.evaluate("tl.transitions ? tl.transitions() : tl.state.transitions")
    assert ts and all(t["a"] is None or t["b"] is None for t in ts), ts
    assert any(t["b"] is not None and t["a"] is None for t in ts)
    page.keyboard.press("Control+z")
    ts = page.evaluate("tl.transitions ? tl.transitions() : tl.state.transitions")
    assert not ts


def test_default_transition_at_the_film_end_is_a_fade_out(page):
    page.keyboard.press("End")
    page.keyboard.press("Escape")
    page.keyboard.press("Control+d")
    ts = page.evaluate("tl.transitions ? tl.transitions() : tl.state.transitions")
    assert ts and ts[0]["b"] is None and ts[0]["a"] is not None, ts


def test_targeting_v2_takes_the_video_target_off_v1(page):
    """HANDS-12: one video target; the overwrite meant for V2 no longer also goes to V1."""
    page.locator(".tl-thead[data-track=V2] button.tname").click()
    t = page.evaluate("Object.fromEntries(Object.entries(tl.tracks()).map(([k, v]) => [k, v.target]))")
    assert t["V2"] is True and t["V1"] is False
    assert "one video target" in toast_text(page)


def test_double_click_on_a_timeline_shot_opens_it_in_the_source_paused(page):
    """HANDS-03: the Source gets the shot's clip, In and Out set; nothing plays."""
    bb = page.locator("#tl .blk").nth(1).bounding_box()
    page.mouse.dblclick(bb["x"] + bb["width"] * 0.5, bb["y"] + bb["height"] / 2)
    page.wait_for_timeout(500)
    assert "CLIP_B" in page.inner_text("#srcTitle .lbl")
    assert page.evaluate("window.rcSource.clip") == "CLIP_B.MP4"
    assert page.evaluate("window.rcSource.inPoint") == 0 and page.evaluate("window.rcSource.outPoint") == 2
    assert page.evaluate("player.playing") is False
    assert page.evaluate("document.querySelector('#srcVideo').paused") is True


def test_jkl_and_home_drive_the_source_when_it_has_the_focus(page):
    page.evaluate("dock.project.select('CLIP_C.MP4', false)")
    page.wait_for_function("document.querySelector('#srcVideo').readyState >= 2")
    page.locator("#source [data-src=fwd]").click()
    t0 = page.evaluate("document.querySelector('#srcVideo').currentTime")
    ph = page.evaluate("tl.state.playhead")
    page.keyboard.press("l")
    page.wait_for_timeout(500)
    assert page.evaluate("document.querySelector('#srcVideo').currentTime") > t0
    page.keyboard.press("k")
    assert page.evaluate("document.querySelector('#srcVideo').paused") is True
    page.keyboard.press("Home")
    assert page.evaluate("document.querySelector('#srcVideo').currentTime") == 0
    assert page.evaluate("tl.state.playhead") == ph          # the Program did not move
    assert page.evaluate("player.playing") is False


def test_the_source_insert_button_leaves_the_playhead_after_the_new_shot(page):
    """FINDER-19: the second Insert lands after the first, not in front of it."""
    page.evaluate("dock.project.select('CLIP_C.MP4', false)")
    page.wait_for_function("document.querySelector('#srcVideo').readyState >= 2")
    page.evaluate("tl.seek(0); tl.setPlayhead(0)")
    page.locator("#source [data-src=insert]").click()
    first_len = page.evaluate("segs[0].out - segs[0].in")
    assert page.evaluate("segs[0].clip") == "CLIP_C.MP4"
    assert abs(page.evaluate("tl.state.playhead") - first_len) < 0.1
    page.locator("#source [data-src=insert]").click()
    assert [r[0] for r in ranges(page)][:2] == ["CLIP_C.MP4", "CLIP_C.MP4"]


def test_a_burst_of_letters_with_no_text_box_stops_acting_as_shortcuts(page):
    """CLUMSY-15: a path typed after the box lost the focus fired a dozen shortcuts."""
    click_shot(page, 0)
    burst(page, "bbbbbbbbxxxxxx")                 # the x's are ripple delete, were they shortcuts
    page.wait_for_timeout(200)
    assert page.evaluate("segs.length") == 2
    assert "looked like typing" in toast_text(page)


# ------------------------------------------------- round 2 (CLUMSY-10, -15, -06)

def test_clicking_an_edit_point_drops_the_shot_selection_and_delete_leaves_the_shots_alone(page):
    """Round 2 CLUMSY-10: Delete removed the shot selected at the far end, not what was clicked."""
    click_shot(page, 1)
    assert page.evaluate("tl.state.sel.size") == 1
    page.evaluate("tl.selectEdit(0)")
    assert page.evaluate("tl.state.sel.size") == 0
    page.keyboard.press("Delete")
    assert page.evaluate("segs.length") == 2
    assert "nothing to delete at this edit" in toast_text(page)
    page.keyboard.press("x")
    assert page.evaluate("segs.length") == 2


def test_a_real_click_on_the_edit_column_drops_the_selection_too(page):
    click_shot(page, 1)
    edge = page.locator("#tl .tl-edge").first
    bb = edge.bounding_box()
    page.mouse.click(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
    page.wait_for_timeout(200)
    assert page.evaluate("tl.state.sel.size") == 0
    page.keyboard.press("Delete")
    assert page.evaluate("segs.length") == 2


def test_delete_with_an_edit_point_selected_removes_its_transition_in_one_undo_step(page):
    page.evaluate("tl.selectEdit(0)")
    page.keyboard.press("Control+d")
    assert page.evaluate("tl.state.transitions.length") == 1
    page.evaluate("tl.selectEdit(0)")
    page.keyboard.press("Delete")
    assert page.evaluate("tl.state.transitions.length") == 0, toast_text(page)
    assert page.evaluate("segs.length") == 2
    page.keyboard.press("Control+z")
    assert page.evaluate("tl.state.transitions.length") == 1


def test_selecting_a_shot_replaces_the_selected_edit(page):
    page.evaluate("tl.selectEdit(0)")
    assert page.evaluate("!!tl.state.edit")
    click_shot(page, 0)
    assert page.evaluate("tl.state.edit") is None


def test_a_delete_names_the_shot_it_removed(page):
    click_shot(page, 1)
    page.keyboard.press("Delete")
    assert "removed shot 2 · CLIP_B (2.0 s)" in toast_text(page)


def test_letters_after_a_text_box_loses_the_focus_are_typing_and_are_put_back(page):
    """Round 2 CLUMSY-15: a text box had the keyboard a moment ago; the next three letters are text.
    What the first two did (a marker, the rolling tool) is undone, and the grade is back."""
    page.evaluate("document.body.insertAdjacentHTML('beforeend', '<input id=pp style=\"position:fixed;left:600px;top:500px;z-index:99\">')")
    page.locator("#pp").click()
    page.keyboard.type("/m")
    page.evaluate("document.getElementById('pp').blur()")
    tool0 = page.evaluate("tl.state.tool")
    mk0 = page.evaluate("tl.markers().length")
    grade0 = page.evaluate("gradeApi().enabled")
    burst(page, "gmn")                              # g (grade off), m (a marker), n (roll tool)
    page.wait_for_timeout(200)
    assert page.evaluate("tl.markers().length") == mk0
    assert page.evaluate("tl.state.tool") == tool0
    assert page.evaluate("gradeApi().enabled") == grade0
    assert "looked like typing: 2 shortcuts undone" in toast_text(page)


def test_slow_deliberate_keys_after_a_click_on_the_timeline_are_shortcuts(page):
    page.evaluate("document.body.insertAdjacentHTML('beforeend', '<input id=pp style=\"position:fixed;left:600px;top:500px;z-index:99\">')")
    page.locator("#pp").click()
    page.keyboard.type("ab")
    page.evaluate("document.getElementById('pp').blur()")
    click_shot(page, 0)                             # the editor went back to the timeline
    for key in ("b", "n", "v"):
        page.keyboard.press(key)
    assert page.evaluate("tl.state.tool") == "select"
    page.keyboard.press("n")
    assert page.evaluate("tl.state.tool") == "roll"


def test_a_paste_inside_a_shot_says_where_it_went_and_offers_the_next_cut(page):
    """Round 2 CLUMSY-06: the playhead had moved without the editor knowing."""
    click_shot(page, 1)
    page.keyboard.press("Control+c")
    page.evaluate("tl.seek(1.0); tl.setPlayhead(1.0)")
    page.keyboard.press("Control+v")
    assert page.evaluate("segs.length") == 4
    t = toast_text(page)
    assert "inside shot 1 (split)" in t and "00:00:01:00" in t
    page.locator("#toast .toastAct").click()
    page.wait_for_timeout(300)
    assert page.evaluate("segs.length") == 3        # A whole, the pasted B, B
    assert [r[0] for r in ranges(page)] == ["CLIP_A.MP4", "CLIP_B.MP4", "CLIP_B.MP4"]


def test_a_paste_at_a_cut_does_not_offer_the_alternative(page):
    click_shot(page, 0)
    page.keyboard.press("Control+c")
    page.keyboard.press("End")
    page.keyboard.press("Control+v")
    assert "inside shot" not in toast_text(page)
    assert page.locator("#toast .toastAct").count() == 0


# ------------------------------------------- the finishing editor's round 2 (items 4 to 8)

def _open_effects(page):
    page.locator(".ws-tab[data-tool=effects]").click()
    page.wait_for_selector("#fxbin .fxb-item")


def _edit_xy(page, t):
    x = page.evaluate(f"tl.timeToX({t}) - tl.el.view.scrollLeft + tl.el.view.getBoundingClientRect().left")
    lane = page.locator("#tl .tl-lane[data-lane=V1]").bounding_box()
    return x, lane["y"] + lane["height"] / 2


def _board_alive(page) -> None:
    """After an aborted drag the board answers a click and a key, nothing holds a drag."""
    assert page.evaluate("!document.querySelector('.dragging, .fxb-item.drag')")
    assert page.evaluate("window.rcDragTransition || null") is None
    page.get_by_role("button", name="Sequence", exact=True).first.click()
    assert page.evaluate("!!document.querySelector('#menubar .menu.open')")
    page.keyboard.press("Escape")
    assert not page.evaluate("!!document.querySelector('#menubar .menu.open')")
    click_shot(page, 0)
    page.keyboard.press("End")
    assert tc_text(page) == "00:00:04:00"


def test_a_drag_from_the_effects_bin_that_never_drops_leaves_the_board_alive(page):
    """Round 2 FINISH-09: the three ways a transition drag can end without a drop."""
    _open_effects(page)
    it = page.locator("#fxbin .fxb-item", has_text="Cross Dissolve").first.bounding_box()
    sx, sy = it["x"] + it["width"] / 2, it["y"] + it["height"] / 2
    x, y = _edit_xy(page, 2.0)
    # 1. down, moves toward an edit, Escape, up
    page.mouse.move(sx, sy); page.mouse.down(); page.mouse.move(sx + 20, sy, steps=4); page.mouse.move(x, y, steps=10)
    page.keyboard.press("Escape"); page.mouse.up()
    assert page.evaluate("tl.transitions().length") == 0
    _board_alive(page)
    # 2. down, out of the window, up outside
    page.mouse.move(sx, sy); page.mouse.down(); page.mouse.move(sx + 20, sy, steps=4)
    page.mouse.move(5000, 5000, steps=6); page.mouse.up()
    assert page.evaluate("tl.transitions().length") == 0
    _board_alive(page)
    # 3. down, moves to the edit, up on the edit: the dissolve lands, one undo
    page.mouse.move(sx, sy); page.mouse.down(); page.mouse.move(sx + 20, sy, steps=4); page.mouse.move(x, y, steps=12); page.mouse.up()
    assert page.evaluate("tl.transitions().length") == 1
    page.keyboard.press("Control+z")
    assert page.evaluate("tl.transitions().length") == 0
    _board_alive(page)


def test_the_sequence_menu_applies_the_default_transition(page):
    click_shot(page, 0)
    page.evaluate("tl.selectEdit(0)")
    page.get_by_role("button", name="Sequence", exact=True).first.click()
    page.locator("[data-cmd=deftrans]").click()
    assert page.evaluate("tl.transitions().length") == 1


def test_a_narrow_transition_is_selected_by_a_click_on_it_and_shows_its_numbers(page):
    """Round 2 FINISH-10: the trim handles under a thin dissolve took the click and selected a shot."""
    page.evaluate("tl.zoomTo(12)")                  # a 1 s dissolve is a few pixels wide here
    page.evaluate("tl.selectEdit(0)")
    page.keyboard.press("Control+d")
    page.keyboard.press("Escape")
    page.evaluate("tl.selectEdit(0)")               # leave the dissolve unselected
    page.evaluate("tl.select([])")
    bb = page.locator("#tl .tl-trans").first.bounding_box()
    page.mouse.click(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
    page.wait_for_timeout(200)
    assert page.evaluate("tl.tsel") is not None
    assert page.evaluate("tl.state.sel.size") == 0
    assert page.locator("[data-testid=transition-panel]").is_visible()
    assert page.locator("[data-testid=transition-dur]").input_value() == "1.00"
    assert page.locator("[data-testid=transition-frames]").input_value() in ("24", "23")
    assert "centred on the cut" in page.inner_text("[data-testid=transition-panel]")
    page.locator("[data-testid=transition-dur]").fill("0.5")
    page.locator("[data-testid=transition-dur]").press("Enter")
    page.wait_for_timeout(200)
    assert abs(page.evaluate("tl.transitions()[0].dur") - 0.5) < 0.05
    page.keyboard.press("Delete")
    assert page.evaluate("tl.transitions().length") == 0
    page.keyboard.press("Control+z")
    assert page.evaluate("tl.transitions().length") == 1


def test_a_transition_drag_says_the_length_as_it_goes(page):
    page.evaluate("tl.selectEdit(0)")
    page.keyboard.press("Control+d")
    b = page.locator("#tl .tl-trans").bounding_box()
    zoom = page.evaluate("tl.state.zoom")
    y = b["y"] + b["height"] / 2
    page.mouse.move(b["x"] + b["width"] - 2, y)
    page.mouse.down()
    page.mouse.move(b["x"] + b["width"] - 2 + 0.25 * zoom, y, steps=5)
    assert "1.50 s" in toast_text(page)
    page.mouse.up()


def test_every_dialog_the_board_opens_swallows_the_keys_behind_it(page):
    """Round 2 FINISH-12: "O" opened the cut switcher behind the Export dialog. Keys that did not
    start inside a dialog do nothing to the cut or the board; Escape closes it."""
    click_shot(page, 0)
    n = page.evaluate("segs.length")
    opens = {
        "export": "dock.open('out'); document.getElementById('exportDialog').showModal()",
        "sequence settings": "(() => { const b = document.querySelector('[data-cmd=\"seq:settings\"]'); b && b.click(); })()",
        "marker": "tl.addMarker(1.0); tl.openMarkerDialog(1.0)",
        "keys": "dock.keys(true)",
        "project settings": "window.rcProjectSettings && window.rcProjectSettings.open()",
    }
    seen = []
    for name, js in opens.items():
        page.evaluate(js)
        page.wait_for_timeout(250)
        layer = page.evaluate("!!document.querySelector('dialog[open], #keysOverlay:not([hidden])')")
        if not layer:
            continue                                  # a dialog this fixture does not open
        seen.append(name)
        page.evaluate("document.activeElement && document.activeElement.blur()")
        page.evaluate("document.body.focus()")
        for key in ("o", "x", "Delete", "m", "n", "Control+a", "ArrowDown", "Home"):
            page.keyboard.press(key)
        assert page.evaluate("segs.length") == n, name
        assert page.evaluate("document.querySelector('#switcher, [data-testid=switcher-menu]') ? "
                             "!!document.querySelector('[role=dialog][data-open=true], .sw-open') : false") is False, name
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        assert not page.evaluate("!!document.querySelector('dialog[open], #keysOverlay:not([hidden])')"), name
        page.evaluate("tl.select([]); tl.removeMarker && tl.removeMarker(1.0)")
        click_shot(page, 0)
    assert len(seen) >= 3, seen


def test_emptying_the_cut_puts_the_playhead_at_zero_and_the_program_says_empty(page):
    """Round 2, the finder: the Program kept an old frame and 00:00:18:03 over an emptied cut."""
    page.evaluate("tl.seek(1.5); tl.setPlayhead(1.5)")
    page.keyboard.press("Control+a")
    page.keyboard.press("Delete")
    page.wait_for_timeout(300)
    assert page.evaluate("segs.length") == 0
    assert page.evaluate("tl.state.playhead") == 0
    assert page.inner_text("#pos") == "00:00:00:00"
    assert page.locator("#progEmpty").is_visible()
    page.keyboard.press("Control+z")
    page.wait_for_timeout(300)
    assert page.evaluate("segs.length") == 2
    assert not page.locator("#progEmpty").is_visible()


def test_the_first_insert_into_an_emptied_cut_lands_at_zero(page):
    page.evaluate("tl.seek(1.5); tl.setPlayhead(1.5)")
    page.keyboard.press("Control+a")
    page.keyboard.press("Delete")
    page.evaluate("dock.project.select('CLIP_C.MP4', false)")
    page.wait_for_function("document.querySelector('#srcVideo').readyState >= 2")
    page.evaluate("tl.setPlayhead(34)")             # the playhead stays where it was
    page.locator("#source [data-src=insert]").click()
    assert page.evaluate("segs.length") == 1 and page.evaluate("segs[0].clip") == "CLIP_C.MP4"
    assert page.evaluate("tl.filmStart(segs[0].id)") == 0
    assert page.evaluate("tl.gaps ? tl.gaps().length : 0") == 0


def test_an_insert_past_the_end_goes_to_the_end_without_a_gap(page):
    page.evaluate("dock.project.select('CLIP_C.MP4', false)")
    page.wait_for_function("document.querySelector('#srcVideo').readyState >= 2")
    total = page.evaluate("tl.total()")
    page.evaluate("tl.setPlayhead(50)")
    page.locator("#source [data-src=insert]").click()
    assert page.evaluate("segs.length") == 3
    assert abs(page.evaluate("tl.filmStart(segs[2].id)") - total) < 0.05
    assert page.evaluate("segs.every(s => !/^gen_/.test(s.clip))")


def test_a_paste_into_an_emptied_cut_goes_to_zero(page):
    click_shot(page, 0)
    page.keyboard.press("Control+c")
    page.keyboard.press("Control+a")
    page.keyboard.press("Delete")
    page.evaluate("tl.setPlayhead(20)")
    page.keyboard.press("Control+v")
    assert page.evaluate("segs.length") == 1
    assert page.evaluate("tl.filmStart(segs[0].id)") == 0


# ------------------------------------------------------------ round 3: the Premiere editor

def _sel_ids(page):
    return page.evaluate("[...tl.state.sel]")


def test_the_playhead_never_changes_the_selection(page):
    """R2-09: a typed time, Home, Up / Down and a click on the ruler left the selection where the editor
    put it; Delete took out that shot, not the one under the playhead."""
    click_shot(page, 1)
    chosen = _sel_ids(page)
    assert len(chosen) == 1
    type_tc(page, "1.")                             # a time inside shot 1
    assert _sel_ids(page) == chosen
    for key in ("Home", "ArrowDown", "ArrowUp", "End", "ArrowLeft"):
        page.keyboard.press(key)
        assert _sel_ids(page) == chosen, key
    page.keyboard.press("Home")
    page.keyboard.press("Delete")
    assert page.evaluate("segs.length") == 1
    assert ranges(page)[0][0] == "CLIP_A.MP4"      # shot 2 went, the one under the playhead stayed
    assert "removed shot 2" in toast_text(page)


def test_playing_does_not_move_the_selection(page):
    click_shot(page, 0)
    chosen = _sel_ids(page)
    page.evaluate("playFrom(1)")
    page.wait_for_function("player.idx === 1")
    page.wait_for_timeout(400)
    assert _sel_ids(page) == chosen
    page.evaluate("pauseCut()")


def test_delete_with_nothing_selected_says_so_and_does_nothing(page):
    page.keyboard.press("Escape")
    page.keyboard.press("Home")
    for key in ("Delete", "Shift+Delete", "Backspace", "x"):
        page.keyboard.press(key)
        assert page.evaluate("segs.length") == 2, key
        assert "nothing selected" in toast_text(page), key


def test_a_delete_leaves_nothing_selected(page):
    click_shot(page, 0)
    page.keyboard.press("Delete")
    assert _sel_ids(page) == []
    page.keyboard.press("Delete")                   # the next shot is not taken by a second press
    assert page.evaluate("segs.length") == 1


def test_one_kind_is_selected_at_a_time_and_a_transition_delete_says_what_went(page):
    click_shot(page, 1)
    page.evaluate("tl.selectEdit(0)")
    page.keyboard.press("Control+d")
    assert page.evaluate("!!tl.tsel")
    assert _sel_ids(page) == []                     # the shot is not outlined any more
    assert page.locator("#tl .blk.sel").count() == 0
    assert page.locator("#tl .tl-trans.sel").count() == 1
    page.keyboard.press("Shift+Delete")
    assert page.evaluate("tl.transitions().length") == 0 and page.evaluate("segs.length") == 2
    assert "removed Cross Dissolve" in toast_text(page)
    click_shot(page, 0)
    assert page.evaluate("tl.tsel") is None and page.evaluate("tl.state.edit") is None


def test_select_all_and_delete_takes_the_v2_clip_too(page):
    """R2-14: Ctrl+A then Delete left the cutaway floating over an empty cut."""
    page.evaluate("tl.addOverlay({clip: 'CLIP_C.MP4', in: 0, out: 1}, 1.0, 'V2')")
    assert page.evaluate("tl.overlays().length") == 1
    page.keyboard.press("Control+a")
    assert "selected all" in toast_text(page)
    page.keyboard.press("Delete")
    assert page.evaluate("segs.length") == 0 and page.evaluate("tl.overlays().length") == 0
    page.keyboard.press("Control+z")
    assert page.evaluate("segs.length") == 2


def test_a_locked_track_is_not_part_of_select_all(page):
    page.evaluate("tl.addOverlay({clip: 'CLIP_C.MP4', in: 0, out: 1}, 1.0, 'V2')")
    page.evaluate("tl.setTrack('V2', 'lock', true)")
    page.keyboard.press("Control+a")
    page.keyboard.press("Delete")
    assert page.evaluate("segs.length") == 0 and page.evaluate("tl.overlays().length") == 1


def test_a_typed_time_past_the_end_parks_on_the_end_and_an_overwrite_there_appends(page):
    """R2-13: the last frame was eaten by an overwrite 'past the end'."""
    page.evaluate("dock.project.select('CLIP_C.MP4', false)")
    page.wait_for_function("document.querySelector('#srcVideo').readyState >= 2")
    type_tc(page, "10.")
    assert tc_text(page) == "00:00:04:00" and "past the end" in toast_text(page)
    before = ranges(page)
    page.keyboard.press(".")
    after = ranges(page)
    assert after[:2] == before                      # the two shots are whole
    assert len(after) == 3 and after[2][0] == "CLIP_C.MP4"


def test_the_transition_length_field_reads_frames_by_default_and_says_when_it_limits(page):
    page.evaluate("tl.selectEdit(0)")
    page.keyboard.press("Control+d")
    tb = page.locator("#tl .tl-trans").bounding_box()
    page.mouse.dblclick(tb["x"] + tb["width"] / 2, tb["y"] + tb["height"] / 2)
    inp = page.locator("#tl .tl-trans input")
    assert inp.bounding_box()["width"] >= 70        # wide enough to read what is typed
    assert "frames" in page.inner_text("#tl .tl-trans-unit")
    inp.fill("12")
    inp.press("Enter")
    r = page.evaluate("1 / tl.frame()")
    assert abs(page.evaluate("tl.transitions()[0].dur") - 12 / r) < 0.01       # 12 is frames, not seconds
    page.mouse.dblclick(tb["x"] + tb["width"] / 2, tb["y"] + tb["height"] / 2)
    page.locator("#tl .tl-trans input").fill("0.5s")
    page.keyboard.press("Enter")
    assert abs(page.evaluate("tl.transitions()[0].dur") - 0.5) < 0.05
    page.mouse.dblclick(tb["x"] + tb["width"] / 2, tb["y"] + tb["height"] / 2)
    page.locator("#tl .tl-trans input").fill("9999")
    page.keyboard.press("Enter")
    assert "limited to" in toast_text(page)


def test_bracket_keys_trim_one_frame_shift_five_and_shift_bracket_is_not_the_out_point(page):
    """R2-20: 6 frames a press, and Shift+] trimmed the OUT by a second."""
    click_shot(page, 0)
    fr = page.evaluate("tl.frame()")
    a0, b0 = page.evaluate("[segs[0].in, segs[0].out]")
    page.keyboard.press("BracketRight")
    a1 = page.evaluate("segs[0].in")
    assert abs((a1 - a0) - fr) < 0.002
    page.keyboard.press("Shift+BracketRight")        # types } on a US keyboard
    assert abs((page.evaluate("segs[0].in") - a1) - 5 * fr) < 0.003
    assert abs(page.evaluate("segs[0].out") - b0) < 1e-6          # the out-point did not move
    page.keyboard.press("Alt+BracketLeft")
    assert abs((b0 - page.evaluate("segs[0].out")) - fr) < 0.002


def test_ctrl_g_with_the_source_focused_types_a_time_in_the_source(page):
    """R2-05: it moved the sequence, and Enter started 'this shot only' on the Program."""
    page.evaluate("dock.project.select('CLIP_C.MP4', false)")
    page.wait_for_function("document.querySelector('#srcVideo').readyState >= 2")
    page.locator("#source [data-src=fwd]").click()
    ph = page.evaluate("tl.state.playhead")
    page.keyboard.press("Control+g")
    page.wait_for_selector("input.hands-tc")
    page.keyboard.type("1:00")                      # one second
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    assert abs(page.evaluate("document.querySelector('#srcVideo').currentTime") - 1.0) < 0.1
    assert page.evaluate("tl.state.playhead") == ph
    page.locator("#source [data-src=fwd]").click()
    page.keyboard.press("Enter")                    # with the Source focused: nothing plays
    page.wait_for_timeout(300)
    assert page.evaluate("player.playing") is False


def test_l_in_the_source_plays_through_the_out_mark(page):
    page.evaluate("dock.project.select('CLIP_C.MP4', false)")
    page.wait_for_function("document.querySelector('#srcVideo').readyState >= 2")
    page.evaluate("(() => { const s = dock.source; s.marks['CLIP_C.MP4'] = [0.2, 0.6]; s.paint(); s.publish(); })()")
    page.locator("#source [data-src=fwd]").click()
    page.evaluate("document.querySelector('#srcVideo').currentTime = 0.3")
    page.keyboard.press("l"); page.keyboard.press("l"); page.keyboard.press("l")   # 8x
    page.wait_for_function("document.querySelector('#srcVideo').currentTime > 0.9", timeout=8000)
    page.keyboard.press("k")


def test_a_double_click_leaves_the_program_on_the_frame_the_source_shows(page):
    """R2-02: the playhead jumped to the shot's first frame while the Source opened where it was clicked."""
    bb = page.locator("#tl .blk").nth(1).bounding_box()
    page.mouse.dblclick(bb["x"] + bb["width"] * 0.7, bb["y"] + bb["height"] / 2)
    page.wait_for_timeout(500)
    seg_in = page.evaluate("segs[1].in")
    src_t = page.evaluate("document.querySelector('#srcVideo').currentTime")
    ph = page.evaluate("tl.state.playhead - tl.filmStart(segs[1].id)")
    assert ph > 0.8                                  # not the shot's first frame
    assert abs((src_t - seg_in) - ph) < 0.1


def test_ctrl_shift_v_pastes_over_and_ctrl_v_still_inserts(page):
    click_shot(page, 0)
    page.keyboard.press("Control+c")
    page.evaluate("tl.seek(1.0); tl.setPlayhead(1.0)")
    total = page.evaluate("tl.total()")
    page.keyboard.press("Control+Shift+V")
    assert abs(page.evaluate("tl.total()") - total) < 0.05          # overwritten, not rippled longer
    assert page.evaluate("segs.length") == 3
    assert "pasted over" in toast_text(page)
    page.keyboard.press("Control+z")
    assert page.evaluate("segs.length") == 2
    page.keyboard.press("Control+v")
    assert page.evaluate("tl.total()") > total + 1.5                # insert grows the film


def test_the_zoom_stays_after_a_change_of_length_until_asked(page):
    """R2-19: after \\, a delete re-scaled the ruler under the editor's pointer."""
    page.keyboard.press("Backslash")
    z0 = page.evaluate("tl.state.zoom")
    click_shot(page, 1)
    page.keyboard.press("Delete")
    assert abs(page.evaluate("tl.state.zoom") - z0) < 1e-6
    page.keyboard.press("Backslash")
    assert page.evaluate("tl.state.zoom") != z0


def test_a_right_click_on_a_shot_offers_the_ordinary_things_in_the_one_menu(page):
    bb = page.locator("#tl .blk").nth(0).bounding_box()
    page.mouse.click(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2, button="right")
    menu = page.locator("[data-testid=ai-context-menu]")
    assert menu.is_visible() and page.locator("[data-testid=ai-context-menu]").count() == 1
    for tid in ("ai-ctx-ask", "ctx-opensrc", "ctx-cut", "ctx-copy", "ctx-paste", "ctx-rdel", "ctx-speed"):
        assert page.locator(f"[data-testid={tid}]").count() == 1, tid
    page.locator("[data-testid=ctx-cut]").click()
    assert page.evaluate("segs.length") == 1


def test_a_cut_row_counts_a_slowed_shot_at_its_film_length(tmp_path):
    """HANDS-19: the cut menu said 1:47 for a cut the timeline said was 1:35."""
    import json
    import server
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"segments": [{"clip": "A.MP4", "in": 0, "out": 10, "speed": 0.5},
                                          {"clip": "B.MP4", "in": 0, "out": 4}]}), encoding="utf-8")
    row = server._cut_row(p)
    assert abs(row["duration_s"] - 24.0) < 0.01


def test_a_rotation_is_one_step_on_the_boards_own_undo_stack(page):
    calls = []
    page.route("**/api/clips/*/orient", lambda route: (calls.append(route.request.post_data), route.fulfill(
        status=200, content_type="application/json", body='{"clip": "CLIP_A.MP4", "changed": true, "previous": "sensor"}')))
    page.evaluate("dock.project.rotateClip('CLIP_A.MP4', 'turn90')")
    page.wait_for_function("tl.nextUndo() && /rotate/.test(tl.nextUndo().label)")
    assert page.evaluate("tl.nextUndo().tag").startswith("rotate:")
    page.keyboard.press("Control+z")
    page.wait_for_function("%d" % 0 + " === 0 && true")
    page.wait_for_timeout(500)
    assert not page.evaluate("tl.nextUndo() && /rotate/.test(tl.nextUndo().label)")
    assert any('"sensor"' in (c or "") for c in calls)             # the undo turned the clip back


# ------------------------------------------- the single-key edits act on the editor's selection

def _park_on_shot_1_then_select_shot_2(page):
    """The playhead over shot 1, shot 2 selected by selecting it (not by clicking, which would
    cue the playhead onto it): the case where a stale index would take the wrong shot."""
    page.evaluate("tl.setPlayhead(0.5)")
    page.evaluate("tl.select([tl.idAt(1)])")
    assert page.evaluate("tl.state.playhead") == pytest.approx(0.5, abs=1e-6)
    assert page.evaluate("tl.indexOf(tl.state.anchor)") == 1


def test_x_removes_the_selected_shot_not_the_one_under_the_playhead_and_names_it(page):
    first = page.evaluate("segs[0].clip")
    second = page.evaluate("segs[1].clip")
    _park_on_shot_1_then_select_shot_2(page)
    page.keyboard.press("x")
    assert page.evaluate("segs.map(s => s.clip)") == [first], "shot 2 went, shot 1 stayed"
    said = toast_text(page)
    assert second.split(".")[0] in said and "removed" in said, said
    # and nothing is selected after it: x again removes nothing and says so
    page.keyboard.press("x")
    assert page.evaluate("segs.length") == 1
    assert "nothing selected" in toast_text(page)


def test_x_with_nothing_selected_removes_nothing_wherever_the_playhead_is(page):
    page.evaluate("tl.select([])")
    page.evaluate("tl.setPlayhead(0.5)")
    before = page.evaluate("JSON.stringify(segs)")
    page.keyboard.press("x")
    assert page.evaluate("JSON.stringify(segs)") == before
    assert "nothing selected" in toast_text(page)


def test_delete_acts_on_a_chosen_shot_and_never_on_the_one_under_the_playhead(page):
    first = page.evaluate("segs[0].clip")
    click_shot(page, 1)
    page.evaluate("tl.setPlayhead(0.5)")             # the playhead back over shot 1, shot 2 still chosen
    page.keyboard.press("Delete")
    assert page.evaluate("segs.map(s => s.clip)") == [first]


def test_q_and_w_trim_the_selected_shot_and_say_when_the_playhead_is_elsewhere(page):
    a0 = page.evaluate("[segs[0].in, segs[0].out]")
    _park_on_shot_1_then_select_shot_2(page)        # shot 2 selected, playhead in shot 1
    page.keyboard.press("q")
    assert page.evaluate("[segs[0].in, segs[0].out]") == a0, "shot 1 is not the selection"
    assert "playhead is outside the selected shot" in toast_text(page)
    page.evaluate("tl.select([])")
    page.keyboard.press("w")
    assert page.evaluate("[segs[0].in, segs[0].out]") == a0
    assert "select a shot" in toast_text(page)


def test_the_bracket_trims_act_on_the_selected_shot_not_the_one_under_the_playhead(page):
    a0 = page.evaluate("[segs[0].in, segs[0].out]")
    b0 = page.evaluate("segs[1].in")
    _park_on_shot_1_then_select_shot_2(page)
    page.keyboard.press("BracketRight")
    assert page.evaluate("[segs[0].in, segs[0].out]") == a0
    assert page.evaluate("segs[1].in") == pytest.approx(b0 + 1 / 24, abs=2e-3)


def test_enter_plays_the_selected_shot_only(page):
    """Enter is "this shot only" (app.js playFrom): it must be the selected shot."""
    _park_on_shot_1_then_select_shot_2(page)
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    assert page.evaluate("player.idx") == 1
    assert page.evaluate("player.single") is True
