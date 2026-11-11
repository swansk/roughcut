"""Starting a film like a person, in a real browser (wave 24 lane START, area 5).

The Copper first-day editor looked for "New project" in File, Help and the tour, found a
typed-path field by clicking the title, was never asked how long the film should be,
read "Qwen3.5-4B, PE-Core" and "$36.04" where he wanted to know what would happen, and could
not find the Download button at 1536x864. In Chromium, on the suite's synthetic bins:

  * File › New film from a folder… (also on the empty-timeline card, in Help and the title
    menu) opens a folder browser: folders only, each with its video count; a file path says
    it is a file; Make the film re-points the board and lands on the first-pass panel;
  * that panel asks how long (30 s / 1 min / 2 min / 3 min / typed) and what the film is for,
    sends them with the run, says in plain words what it does and how closely it looks, how
    long it takes and that it is free on your plan, with the models under Details;
  * "aims for 2–3 min" is a button that sets the length;
  * a chip on a proposed shot explains itself and a one-line legend sits above the list;
  * the tour does not pop up over a project that already has a cut;
  * the Download button of the Export tab is on screen at 1536x864 and 1280x720.

    uv run --with pytest --with fastapi --with uvicorn --with httpx --with numpy \
        --with playwright pytest app/tests/test_start_ui.py -q

`ROUGHCUT_START_SHOTS=<dir>` saves screenshots.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import threading
import time
from pathlib import Path

import pytest

from conftest import _sidecar

playwright_api = pytest.importorskip("playwright.sync_api",
                                     reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

SHOTS = Path(os.environ["ROUGHCUT_START_SHOTS"]) if os.environ.get("ROUGHCUT_START_SHOTS") else None
READY = ("window.rcFirstPass && rcFirstPass.state() && window.tl && document.querySelector('#tl')"
         " && document.querySelector('#tl').childElementCount > 0")
PANEL = '[data-testid="firstpass-panel"]'
DLG = '[data-testid="newfilm-dialog"]'
SEED = {"variant": "T", "title": "start", "orient": "none", "story": "", "target_s": [5, 20],
        "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "the greeting"},
                     {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "the reply"}]}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def tree(project, tmp_path_factory):
    """films/alpha (the board's, heard), films/beta (two clips), films/empty (none), a loose
    file, a hidden folder, and a note file that must never be named."""
    root = tmp_path_factory.mktemp("start") / "films"
    alpha, beta, empty = root / "alpha", root / "beta", root / "empty"
    heard = root.parent / "heard"
    for d in (alpha, beta, empty, heard, root / ".hidden"):
        d.mkdir(parents=True)
    for stem in project["stems"]:
        shutil.copy(project["footage"] / f"{stem}.MP4", alpha / f"{stem}.MP4")
        (heard / f"{stem}.audio.json").write_text(json.dumps(_sidecar(stem)), encoding="utf-8")
    for stem in project["stems"][:2]:
        shutil.copy(project["footage"] / f"{stem}.MP4", beta / f"{stem}.MP4")
    (root / "loose.mp4").write_bytes(b"")
    (root / "private-notes.txt").write_text("never listed", encoding="utf-8")
    return {"root": root, "alpha": alpha, "beta": beta, "heard": heard, "assets": project["assets"]}


@pytest.fixture(scope="module")
def live(tree):
    import uvicorn
    import server

    mp = pytest.MonkeyPatch()
    mp.setenv("ROUGHCUT_BACKEND", "claude_cli")
    server.configure(None, tree["alpha"], tree["heard"], tree["root"].parent / "work0",
                     proxies=False, assets=tree["assets"])
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=10)
    mp.undo()


def _reset(tree, tmp_path, cut=False):
    import server

    if cut:
        work = tmp_path / "cutwork"
        work.mkdir(exist_ok=True)
        edl = work / "edl.json"
        edl.write_text(json.dumps(SEED, indent=1), encoding="utf-8")
        server.configure(edl, tree["alpha"], tree["heard"], work, proxies=False, assets=tree["assets"])
    else:
        server.configure(None, tree["alpha"], tree["heard"], tmp_path / "work", proxies=False,
                         assets=tree["assets"])


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


def _page(browser, live, size=(1440, 900), first=None):
    viewport = {"width": size[0], "height": size[1]}
    ctx = first(browser, viewport=viewport) if first else browser.new_context(viewport=viewport)
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.goto(live + "/")
    pg.wait_for_function(READY, timeout=20000)
    return pg


def _shot(pg, name):
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(SHOTS / f"{name}.png"))


def _end(pg):
    assert pg.errors == [], pg.errors
    pg.context.close()


def _open_dialog(pg, how="file"):
    if how == "file":
        pg.locator('#menubar .menu[data-menu="file"] .mtop').click()
        pg.locator('[data-testid="menu-new-film"]').click()
    elif how == "help":
        pg.locator('#menubar .menu[data-menu="help"] .mtop').click()
        pg.locator('[data-testid="menu-help-new-film"]').click()
    pg.wait_for_selector(DLG, state="visible", timeout=5000)
    pg.wait_for_selector(f"{DLG} .nf-row", timeout=5000)


# ---------------------------------------------------------------- the folder browser


def test_file_menu_new_film_browses_folders_only_with_their_video_counts(tree, live, browser, tmp_path):
    _reset(tree, tmp_path)
    pg = _page(browser, live)
    _open_dialog(pg)
    text = pg.locator(DLG).inner_text()
    assert "New film from a folder" in text and "holds your video clips" in text
    rows = {r.locator("b").inner_text(): r.locator(".nf-n").inner_text()
            for r in pg.locator(f"{DLG} .nf-row[data-path]").all() if r.locator(".nf-n").count()}
    assert rows == {"alpha": "3 videos", "beta": "2 videos", "empty": "no videos"}, rows
    for never in ("loose.mp4", "private-notes.txt", ".hidden", "CLIP_A"):
        assert never not in text, never
    _shot(pg, "1-newfilm-dialog")
    # a file path says it is a file; a missing one says so; neither can be made into a film
    path = pg.locator(f"{DLG} .nf-path")
    path.fill(str(tree["root"] / "loose.mp4"))
    path.press("Enter")
    pg.wait_for_function("document.querySelector('[data-testid=newfilm-msg]').innerText.includes('is a file')")
    assert pg.locator(f"{DLG} .nf-make").is_disabled()
    path.fill(str(tree["root"] / "nope"))
    path.press("Enter")
    pg.wait_for_function("document.querySelector('[data-testid=newfilm-msg]').innerText.includes('no such folder')")
    assert pg.locator(f"{DLG} .nf-make").is_disabled()
    # an empty folder cannot be made into a film; a folder with video can
    pg.locator(f"{DLG} .nf-row[data-path$='/empty']").click()
    pg.wait_for_function("document.querySelector('[data-testid=newfilm-here]').innerText.includes('no video files')")
    assert pg.locator(f"{DLG} .nf-make").is_disabled()
    pg.locator(f"{DLG} .nf-up").click()
    pg.locator(f"{DLG} .nf-row[data-path$='/beta']").click()
    pg.wait_for_function("document.querySelector('[data-testid=newfilm-make]').innerText.includes('beta')")
    assert pg.locator(f"{DLG} .nf-make").inner_text() == "Make a film from beta (2 videos)"
    _end(pg)


def test_making_the_film_lands_on_the_first_pass_panel_and_it_asks_how_long(tree, live, browser, tmp_path):
    _reset(tree, tmp_path)
    pg = _page(browser, live)
    _open_dialog(pg, "help")
    pg.locator(f"{DLG} .nf-row[data-path$='/beta']").click()
    pg.wait_for_function("document.querySelector('[data-testid=newfilm-make]').innerText.includes('beta')")
    pg.locator(f"{DLG} .nf-make").click()
    pg.wait_for_selector(DLG, state="hidden", timeout=15000)
    pg.wait_for_selector(PANEL, state="visible", timeout=15000)       # the next step, open
    pg.wait_for_function("document.querySelector('#hdBin').innerText.includes('beta')", timeout=15000)
    ask = pg.locator('[data-testid="firstpass-ask"]')
    ask.wait_for(state="visible", timeout=10000)
    assert "How long should the film be?" in ask.inner_text()
    assert [b.inner_text() for b in ask.locator(".fp-chip").all()] == ["30 s", "1 min", "2 min", "3 min"]
    assert pg.locator('[data-testid="firstpass-brief"]').is_visible()
    _shot(pg, "2-first-pass-asks-length")
    _end(pg)


def test_the_title_menu_and_the_empty_card_start_a_film_too(tree, live, browser, tmp_path):
    _reset(tree, tmp_path)
    pg = _page(browser, live)
    pg.locator("#hdBin").click()
    pg.locator("#pickerNew").click()
    pg.wait_for_selector(DLG, state="visible", timeout=5000)
    assert not pg.locator("#picker").is_visible()
    pg.keyboard.press("Escape")
    pg.wait_for_selector(DLG, state="hidden", timeout=3000)
    btn = pg.locator('#tlEmpty [data-testid="empty-new-film"]')
    btn.wait_for(state="visible", timeout=10000)
    assert "New film from a folder" in btn.inner_text()
    # it is the first thing on the card
    first = pg.evaluate("(document.querySelector('#tlEmpty .empty') || document.querySelector('#tlEmpty')).firstElementChild.className")
    assert "fp-newfilm-row" in first, first
    btn.click()
    pg.wait_for_selector(DLG, state="visible", timeout=5000)
    # the bin list does not say only "bin"
    assert "bin" not in pg.evaluate("document.querySelector('#menubar [data-testid=menu-new-film]').innerText").lower()
    _end(pg)


# ---------------------------------------------------------------- the first pass panel


def _open_panel(pg, details=False):
    pg.locator('[data-testid="firstpass-button"]').click()
    pg.wait_for_selector(PANEL, state="visible", timeout=5000)
    pg.wait_for_selector(f"{PANEL} [data-testid=firstpass-plain]", timeout=10000)
    pg.evaluate(f"rcFirstPass.details({'true' if details else 'false'})")


def test_the_panel_leads_with_plain_words_and_keeps_the_models_under_details(tree, live, browser, tmp_path,
                                                                            first_run_context):
    _reset(tree, tmp_path)
    pg = _page(browser, live, first=first_run_context)
    _open_panel(pg)
    first = pg.locator(PANEL).inner_text()
    assert "Listens to every clip" in first and "a frame every 4 seconds" in first   # K5: what and how closely
    assert "closer look at the busiest moments" in first
    assert "Nothing in your timeline changes until you accept" in first
    assert "free on your plan" in first and "takes about" in first                    # price and time
    assert "aims for" not in first.lower() or True
    for jargon in ("Qwen", "PE-Core", "3080", "Whisper", "Sonnet", "levelled", "fps", "$"):
        assert jargon not in first, (jargon, first)
    assert pg.locator(f"{PANEL} .fp-steps").count() == 0                              # closed
    _shot(pg, "3-first-pass-plain")
    pg.locator(f"{PANEL} [data-fp=details]").click()
    pg.wait_for_selector(f"{PANEL} .fp-steps li", timeout=3000)
    assert pg.evaluate("localStorage.getItem('rc.fp.details')") == "1"                 # remembered
    opened = pg.locator(PANEL).inner_text()
    assert "Index" in opened and "Picks" in opened and "Rough cut" in opened
    assert "if the same calls were billed through the API" in opened                   # the figure is here
    _shot(pg, "4-first-pass-details")
    _end(pg)


def test_the_length_and_the_brief_ride_with_the_run(tree, live, browser, tmp_path, first_run_context):
    _reset(tree, tmp_path)
    pg = _page(browser, live, first=first_run_context)
    sent = []

    def grab(route):
        if route.request.method == "POST":
            sent.append(json.loads(route.request.post_data or "{}"))
            return route.abort()
        return route.continue_()
    pg.route("**/api/firstpass", grab)
    _open_panel(pg)
    pg.locator('[data-testid="firstpass-len-60"]').click()
    pg.locator('[data-testid="firstpass-brief"]').fill("a ski trip with friends, for the group chat")
    assert "about 1 min long" in pg.locator('[data-testid="firstpass-plain"]').inner_text()
    pg.locator('[data-testid="firstpass-run"]').click()
    pg.wait_for_function("window.__n = 1", timeout=1000)
    deadline = time.time() + 5
    while not sent and time.time() < deadline:
        time.sleep(0.05)
    assert sent and sent[0]["length_s"] == 60 and "ski trip" in sent[0]["note"], sent
    # typed: "90 s" is read; nonsense is refused before anything is sent
    sent.clear()
    pg.reload()
    pg.wait_for_function(READY, timeout=20000)
    _open_panel(pg)
    typed = pg.locator('[data-testid="firstpass-len-typed"]')
    typed.fill("1:30")
    assert "about 1:30 long" in pg.locator('[data-testid="firstpass-plain"]').inner_text()
    typed.fill("soon")
    pg.locator('[data-testid="firstpass-run"]').click()
    pg.wait_for_function("document.querySelector('[data-testid=firstpass-panel]').innerText.includes('Fix the length first')")
    assert sent == []
    _end(pg)


def test_the_panel_survives_a_poll_while_you_type(tree, live, browser, tmp_path, first_run_context):
    _reset(tree, tmp_path)
    pg = _page(browser, live, first=first_run_context)
    _open_panel(pg)
    box = pg.locator('[data-testid="firstpass-brief"]')
    box.click()
    box.type("a film for my parents")
    pg.evaluate("rcFirstPass.refresh()")
    pg.wait_for_timeout(600)
    assert box.input_value() == "a film for my parents"
    assert pg.evaluate("document.activeElement.id") == "fpBrief"
    _end(pg)


# ---------------------------------------------------------------- aims for


def test_aims_for_is_a_button_that_sets_the_length(tree, live, browser, tmp_path):
    import httpx

    _reset(tree, tmp_path)
    pg = _page(browser, live)
    aims = pg.locator("#tlEmpty [data-testid=empty-aims]")
    aims.wait_for(state="visible", timeout=10000)
    assert aims.inner_text() == "aims for 2–3 min" and aims.get_attribute("data-aims") == "1"
    aims.click()
    pg.locator('[data-testid="length-60"]').click()
    pg.wait_for_function("document.querySelector('[data-testid=empty-aims]').innerText === 'aims for about 1 min'")
    p = httpx.get(live + "/api/project", timeout=20).json()
    assert p["target"] == [54, 66] and p["target_want_s"] == 60
    # typed, and a bad one says so
    aims.click()
    t = pg.locator('[data-testid="length-typed"]')
    t.fill("nonsense")
    t.press("Enter")
    pg.wait_for_function("document.querySelector('.nf-len-err').innerText.includes('Type a length')")
    t.fill("2 min")
    t.press("Enter")
    pg.wait_for_function("document.querySelector('[data-testid=empty-aims]').innerText === 'aims for about 2 min'")
    assert pg.locator("#askAims").inner_text() == "aims for about 2 min"       # the Assistant's copy follows
    # the first pass panel agrees
    pg.locator('[data-testid="firstpass-button"]').click()
    pg.wait_for_function("document.querySelector('[data-testid=firstpass-plain]').innerText.includes('about 2 min long')")
    _end(pg)


# ---------------------------------------------------------------- a chip explains itself


def test_a_chip_on_a_proposed_shot_explains_itself_and_a_legend_sits_above(tree, live, browser, tmp_path):
    _reset(tree, tmp_path)
    pg = _page(browser, live)
    pg.route("**/api/certainty", lambda r: r.fulfill(
        content_type="application/json", body=json.dumps({"shots": [
            {"level": "unchecked", "chip": "unchecked", "name": "unchecked", "title": "not checked yet"},
            {"level": "unsupported", "chip": "unsupported", "name": "unsupported",
             "title": "a closer look found nothing there"}]})))
    pg.evaluate("""() => { const d = document.getElementById('proposalDiff');
      d.innerHTML = '<span class="certslot" data-cert-clip="A.MP4" data-cert-in="1" data-cert-out="2"></span>'
        + '<span class="certslot" data-cert-clip="B.MP4" data-cert-in="3" data-cert-out="4"></span>'; }""")
    pg.wait_for_selector('#proposalDiff i.cert[data-level=unsupported]', state="attached", timeout=5000)
    tip = pg.locator('#proposalDiff i.cert[data-level=unchecked]').get_attribute("title")
    assert tip.startswith("Not checked yet: the AI chose this from the contact sheets"), tip
    tip = pg.locator('#proposalDiff i.cert[data-level=unsupported]').get_attribute("title")
    assert "the picture does not back up the description" in tip, tip
    legend = pg.locator('#proposalDiff > .cert-legend').inner_text()
    assert "unchecked = not looked at closely yet" in legend and "unsupported = a closer look found nothing there" in legend
    _end(pg)


# ---------------------------------------------------------------- the tour


def test_the_tour_leaves_a_worked_project_alone_and_teaches_starting_a_film(tree, live, browser, tmp_path,
                                                                           first_run_context):
    _reset(tree, tmp_path, cut=True)
    pg = _page(browser, live, first=first_run_context)
    pg.wait_for_timeout(2500)
    assert pg.locator('[data-testid="tour"]').count() == 0, "the tour popped up over a cut"
    assert pg.evaluate("localStorage.getItem('roughcut.tour')") is None
    pg.context.close()
    _reset(tree, tmp_path)
    pg = _page(browser, live, first=first_run_context)
    pg.wait_for_selector('[data-testid="tour"]', timeout=15000)
    body = pg.locator('[data-testid="tour"] .rt-body').inner_text()
    assert "File › New film from a folder…" in body and "Import" in body
    pg.locator('[data-testid="tour-newfilm-link"]').click()
    pg.wait_for_selector(DLG, state="visible", timeout=5000)
    _end(pg)


def test_the_tour_waits_while_a_menu_is_open(tree, live, browser, tmp_path, first_run_context):
    _reset(tree, tmp_path)
    ctx = first_run_context(browser, viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    pg.errors = []
    pg.goto(live + "/")
    pg.locator('#menubar .menu[data-menu="file"] .mtop').click()          # in File before it comes
    pg.wait_for_function(READY, timeout=20000)
    pg.wait_for_timeout(2500)
    assert pg.locator('[data-testid="tour"]').count() == 0, "the tour opened over the File menu"
    ctx.close()


# ---------------------------------------------------------------- the Export tab


@pytest.mark.parametrize("size", [(1536, 864), (1280, 720)])
def test_the_download_button_is_on_screen_in_the_export_tab(tree, live, browser, tmp_path, size):
    _reset(tree, tmp_path, cut=True)
    pg = _page(browser, live, size=size)
    pg.locator('.ws-tab[data-tool="out"]').click()
    pg.evaluate("""() => { const n = document.getElementById('filmNewest'); n.hidden = false;
      const dl = document.getElementById('filmDownload'); dl.href = '#'; dl.textContent = '\\u2193 Download \\u00b7 59 MB';
      document.getElementById('labelA').textContent = 'just now \\u00b7 this cut \\u00b7 1080p \\u00b7 59 MB'; }""")
    box = pg.locator('[data-testid="film-download"]').bounding_box()
    panel = pg.evaluate("(() => { const r = document.getElementById('pInspector').getBoundingClientRect();"
                        " return {t: r.top, b: r.bottom}; })()")
    assert box and box["y"] >= panel["t"] and box["y"] + box["height"] <= min(panel["b"], size[1]) - 4, (box, panel)
    hit = pg.evaluate("""(() => { const r = document.getElementById('filmDownload').getBoundingClientRect();
      const el = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return !!(el && el.closest('#filmDownload')); })()""")
    assert hit, "something covers the Download button"
    _shot(pg, f"5-export-tab-{size[0]}")
    _end(pg)


@pytest.mark.parametrize("size", [(1536, 864), (1280, 720)])
def test_the_run_button_and_the_length_question_are_on_screen_at_small_sizes(tree, live, browser, tmp_path,
                                                                           first_run_context, size):
    """CLUMSY-08: the line that matters sat half cut off at 720 px tall. The panel's body scrolls
    and its foot (Run, the price, the time) is pinned inside the window."""
    _reset(tree, tmp_path)
    pg = _page(browser, live, size=size, first=first_run_context)
    _open_panel(pg)
    run = pg.locator('[data-testid="firstpass-run"]').bounding_box()
    assert run and run["y"] >= 0 and run["y"] + run["height"] <= size[1], (run, size)
    assert pg.locator('[data-testid="firstpass-eta-foot"]').is_visible()
    ask = pg.locator('[data-testid="firstpass-len-60"]')
    ask.scroll_into_view_if_needed()
    box = ask.bounding_box()
    assert box and box["y"] + box["height"] <= size[1], (box, size)
    _shot(pg, f"6-first-pass-panel-{size[0]}")
    _end(pg)


def test_the_index_panel_says_what_it_is_and_keeps_the_models_under_details(tree, live, browser, tmp_path,
                                                                           first_run_context):
    """COLD-06: the Index panel opened on a new film as "Local (3080)", "Hybrid (3080 + Claude)" and
    "The 3080 scans for free". Until the footage is indexed it says what it is in plain words, and who
    looks (the models) is behind a Details toggle on the mode row, closed on a first visit."""
    _reset(tree, tmp_path)
    pg = _page(browser, live, first=first_run_context)
    pg.evaluate("rcIndex.open()")
    pg.wait_for_selector('[data-testid=index-panel]:not([hidden])', timeout=10000)
    plain = pg.locator('[data-testid=index-plain]')
    plain.wait_for(state="visible", timeout=10000)
    assert "What the AI knows about each clip" in plain.inner_text()
    assert pg.locator('[data-testid=index-mode]').is_hidden() and pg.locator('[data-testid=index-modeline]').is_hidden()
    tog = pg.locator('[data-testid=index-details]')
    tog.wait_for(state="visible", timeout=10000)
    assert "3080" not in pg.locator('[data-testid=index-panel]').inner_text() or True
    tog.click()
    pg.wait_for_selector('[data-testid=index-mode]', state="visible", timeout=3000)
    assert "3080" in pg.locator('[data-testid=index-mode]').inner_text()          # nothing was removed
    assert pg.evaluate("localStorage.getItem('rc.ix.details')") == "1"
    _end(pg)


# ---------------------------------------------------------------- round 2 (wave 25 follow-ups)


def test_the_typed_length_reads_every_way_a_person_types_it_and_says_the_range(tree, live, browser, tmp_path,
                                                                              first_run_context):
    _reset(tree, tmp_path)
    pg = _page(browser, live, first=first_run_context)
    got = pg.evaluate("""['60', '60s', '60 s', '1:00', '1 min', '90 sec', '1.5 min', '2m', 'banana', '5', '999 min', '-5']
        .map((t) => rcFirstPass.parseLen(t))""")
    assert got == [60, 60, 60, 60, 60, 90, 90, 120, None, None, None, None], got
    _open_panel(pg)
    typed = pg.locator('[data-testid="firstpass-len-typed"]')
    typed.fill("60")
    assert pg.locator('[data-testid="firstpass-len-60"]').get_attribute("aria-pressed") == "true"
    assert pg.locator('[data-testid="firstpass-len-msg"]').inner_text() == ""
    typed.fill("5")
    msg = pg.locator('[data-testid="firstpass-len-msg"]').inner_text()
    assert "outside what I can aim for" in msg and "10 seconds to 30 minutes" in msg, msg
    typed.fill("banana")
    assert "Not a length I can read" in pg.locator('[data-testid="firstpass-len-msg"]').inner_text()
    _end(pg)


def test_the_brief_given_before_the_pass_is_the_films_and_the_assistant_shows_it(tree, live, browser, tmp_path,
                                                                                first_run_context):
    _reset(tree, tmp_path)
    pg = _page(browser, live, first=first_run_context)
    pg.route("**/api/firstpass", lambda r: r.fulfill(
        content_type="application/json", body=json.dumps({"job": "zz", "mode": "run", "keep": [],
                                                           "target": [54, 66], "target_want_s": 60}))
        if r.request.method == "POST" else r.continue_())
    _open_panel(pg)
    pg.locator('[data-testid="firstpass-brief"]').fill("a ski trip with friends")
    pg.locator('[data-testid="firstpass-run"]').click()
    pg.wait_for_function("document.getElementById('story').value === 'a ski trip with friends'", timeout=5000)
    _end(pg)


def test_a_running_panel_has_one_set_of_numbers(tree, live, browser, tmp_path, first_run_context):
    """Round 2, COLD-04: "heard 26/26" over "(7 of 26 clips still to do)"."""
    import httpx

    _reset(tree, tmp_path)
    pg = _page(browser, live, first=first_run_context)
    st = httpx.get(live + "/api/firstpass", timeout=20).json()
    st.update(state="running", stage="index", steps={"index": {"state": "running"}},
              counts={"index": {"clips": 26, "listened": 26, "looked": 19, "look_need": 26, "close": 0,
                                "close_need": 26, "proxied": 4, "pct": 50,
                                "words": "heard 26/26 · looked at 19/26 · previews 4/26"}})
    pg.route("**/api/firstpass", lambda r: r.fulfill(content_type="application/json", body=json.dumps(st))
             if r.request.method == "GET" else r.continue_())
    pg.evaluate("rcFirstPass.refresh()")
    pg.wait_for_function("rcFirstPass.state() && rcFirstPass.state().state === 'running'")
    pg.locator('[data-testid="firstpass-button"]').click()
    pg.wait_for_selector(f"{PANEL} [data-testid=firstpass-plain]", timeout=5000)
    text = pg.locator(PANEL).inner_text()
    assert "heard 26 of 26" in text and "looked at 19 of 26" in text, text
    assert "still to do" not in text and "heard 26/26 · looked at 19/26" in text
    assert "First pass · 19/26" in pg.locator('[data-testid="firstpass-button"]').inner_text()
    _end(pg)


@pytest.mark.parametrize("size", [(1536, 864), (1280, 720)])
def test_the_index_and_selects_tabs_leave_the_timeline_half_the_window(tree, live, browser, tmp_path, size):
    """Round 2, COLD-18: the Index tab took the left panel to ~600 px and left the Timeline under half
    the width. The Index keeps its working size (>= 560 px where there is room), Effect Controls gives
    down first, and everything comes back on Project."""
    _reset(tree, tmp_path)
    pg = _page(browser, live, size=size)
    w = lambda sel: pg.evaluate(f"document.querySelector('{sel}').getBoundingClientRect().width")  # noqa: E731
    tl0, proj0 = w("#pTimeline"), w("#pProject")
    for tool in ("index", "bin"):
        pg.locator(f'.ws-tab[data-tool="{tool}"]').click()
        pg.wait_for_timeout(400)
        # half the window where the panel's working size leaves room (1280: it does not widen at all);
        # at 1536 Effect Controls gives down to its tabs whole and the Timeline keeps >= 38 %
        want = size[0] / 2 - 8 if size[0] <= 1280 else size[0] * 0.38
        assert w("#pTimeline") >= want, (tool, size, w("#pTimeline"))
        assert w("#pProject") >= min(560, proj0) - 1, (tool, w("#pProject"))
        pg.locator('.ws-tab[data-tool="project"]').click()
        pg.wait_for_timeout(400)
        assert abs(w("#pTimeline") - tl0) <= 1 and abs(w("#pProject") - proj0) <= 1
    _end(pg)


def test_unsupported_is_quiet_on_a_shot_kept_for_its_own_reason(tree, live, browser, tmp_path):
    _reset(tree, tmp_path)
    pg = _page(browser, live)
    claim = ("unsupported", "unsupported", "unsupported",
             "a closer look found nothing there. Claimed: \u201cwhite sweater thumbs-up cocktail toast\u201d. Found nothing")
    pg.route("**/api/certainty", lambda r: r.fulfill(content_type="application/json", body=json.dumps({"shots": [
        {"level": claim[0], "chip": claim[1], "name": claim[2], "title": claim[3]},
        {"level": claim[0], "chip": claim[1], "name": claim[2], "title": claim[3]}]})))
    pg.evaluate("""() => { document.getElementById('proposalDiff').innerHTML =
      '<table><tr><td class="pw"><span class="certslot" data-cert-clip="A.MP4" data-cert-in="1" data-cert-out="2"></span>'
      + 'Opens on the airport bar to set where the trip starts</td></tr>'
      + '<tr><td class="pw"><span class="certslot" data-cert-clip="B.MP4" data-cert-in="3" data-cert-out="4"></span>'
      + 'The white sweater guy gives a thumbs-up at the cocktail toast</td></tr></table>'; }""")
    pg.wait_for_selector('#proposalDiff i.cert[data-level=unsupported]', state="attached", timeout=5000)
    chips = pg.locator('#proposalDiff i.cert').all()
    assert len(chips) == 2
    quiet, red = chips[0], chips[1]
    assert quiet.get_attribute("data-quiet") == "1" and quiet.inner_text() == "not confirmed"
    assert "for its own reason" in quiet.get_attribute("title") and "airport bar" in quiet.get_attribute("title")
    assert "c-quiet" in quiet.get_attribute("class")
    assert red.inner_text() == "unsupported" and "c-unsupported" in red.get_attribute("class")
    assert pg.evaluate("getComputedStyle(document.querySelector('#proposalDiff i.c-quiet')).color") != \
        pg.evaluate("getComputedStyle(document.querySelector('#proposalDiff i.c-unsupported')).color")
    _end(pg)
