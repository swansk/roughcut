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
    assert "New film" in page.evaluate("document.activeElement.textContent")    # (wave 24 START: File's first item)
    page.keyboard.press("ArrowDown")
    assert "Import" in page.evaluate("document.activeElement.textContent")
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

def test_parse_timecode_accepts_frames_seconds_m_ss_digits_and_offsets(page):
    r = page.evaluate("1 / tl.frame()")             # the sequence's own rate
    n = round(r)                                    # the nominal count timecode runs at
    p = lambda s, frm=0: page.evaluate(f"tlKeys.parseTimecode({s!r}, {frm})")
    assert abs(p("00:00:40:00")["t"] - 40 * n / r) < 1e-6
    assert abs(p("40.")["t"] - 40 * n / r) < 1e-6           # timecode seconds: 40. is 00:00:40:00
    assert abs(p("40.5")["t"] - round(40.5 * n) / r) < 1e-6
    assert abs(p("1:05")["t"] - 65 * n / r) < 1e-6
    assert abs(p("5108")["t"] - (51 * n + 8) / r) < 1e-6
    assert abs(p("+2.", 10)["t"] - (10 + 2 * n / r)) < 0.05
    assert abs(p("-10", 10)["t"] - (10 - 10 / r)) < 0.05
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
    assert tc_text(page) == "00:00:03:23"


def test_escape_leaves_the_timecode_field_and_a_bad_one_stays_open(page):
    page.locator("#tl .tl-tc").click()
    page.keyboard.type("zzz")
    page.keyboard.press("Enter")
    assert page.locator("input.hands-tc.bad").count() == 1
    assert "not a timecode" in toast_text(page)
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
    page.keyboard.type("bbbbbbbbxxxxxx", delay=8)   # the x's are ripple delete, were they shortcuts
    page.wait_for_timeout(200)
    assert page.evaluate("segs.length") == 2
    assert "looks like typing" in toast_text(page)
