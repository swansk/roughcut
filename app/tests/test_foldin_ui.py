"""The old footage and pass screens, folded into the editor (wave 17 FOLDIN, Karl's K4 b).

Karl, 2026-10-19: "The previous parts where you click on the top and can get to the
"pass" and stuff is still here... odd". He chose fold in, then remove: what was useful on
/open and /floor moves into the editor, nothing in it jumps to a separate screen, and the
old pages go away. This drives the editor in Chromium on its own five-pick bin (the
fixture test_picks_ui.py builds: three 12 s clips, five speech picks, a mocked first pass)
and checks the three halves:

  * the pass's review is Selects': ✓ keeps a pick (its preview snapped outward to the
    sentences, as the pass did; or the Source monitor's In / Out when they were moved),
    ✗ rejects it (the "rejected" chip restores it), and the right-click menu has the rest:
    hero, later, a note, the rest of the clip, the picks a filter shows, its takes, Undo;
  * File › Project Settings… holds what the footage screen's settings drawer held: the
    budget cap (the environment's wins) and the index's workers, the server's 400 said;
  * nothing leaves `/`: every menu item, the Next chip at every target it can name, the
    switcher's place rows, the walk-through's steps and every link, crawled — and /open and
    /floor answer with the editor.

No model is called: nothing here spends, and the crawl aborts every write it does not
expect. Skipped, not failed, when playwright is absent.
"""

from __future__ import annotations

import json
import re
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

from test_picks_ui import CUT, LAYOUT, LINES, STEMS, _record, _seed, _sidecar  # noqa: E402

PAD_HEAD, PAD_TAIL, NEAR_WORD = 0.25, 0.45, 0.30     # boundaries.py's, as the pass kept them


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def fold(tmp_path_factory) -> dict:
    """test_picks_ui's five-pick bin, its own copy (this module writes verdicts)."""
    import subprocess
    root = tmp_path_factory.mktemp("foldin")
    footage, sidecars, work = root / "footage", root / "sidecars", root / "work"
    for d in (footage, sidecars, work):
        d.mkdir()
    for stem in STEMS:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-nostdin",
             "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=12",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", str(footage / f"{stem}.MP4")],
            check=True, capture_output=True)
        (sidecars / f"{stem}.audio.json").write_text(json.dumps(_sidecar(stem), indent=1),
                                                     encoding="utf-8")
    assets = root / "assets"
    (assets / "music").mkdir(parents=True)
    return {"root": root, "footage": footage, "sidecars": sidecars, "work": work,
            "edl": root / "edl.json", "assets": assets}


@pytest.fixture(scope="module")
def live(fold):
    import uvicorn
    import server

    _seed(fold, [])
    server.configure(fold["edl"], fold["footage"], fold["sidecars"], fold["work"],
                     proxies=False, visual=fold["work"] / "visual", assets=fold["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in STEMS])
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started, "server did not start"
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    th.join(timeout=10)


@pytest.fixture
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        yield b
        b.close()


def _open(browser, live, size=(1920, 1080), routes=None):
    pg = browser.new_page(viewport={"width": size[0], "height": size[1]})
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.errors = errors
    pg.add_init_script(LAYOUT)            # the Project panel wide: every Selects column
    for pattern, handler in (routes or []):
        pg.route(pattern, handler)
    pg.goto(live)
    pg.wait_for_function("window.tlLanes && typeof P !== 'undefined' && P && P.clips"
                         " && window.tl && tl.state && window.rcPassReview && window.rcSelects",
                         timeout=20000)
    pg.wait_for_timeout(300)
    return pg


def _picks(live) -> list[dict]:
    return sorted(httpx.get(live + "/api/picks?order=clip", timeout=30).json()["picks"],
                  key=lambda p: (p["clip"], p["start"]))


def _bin(live) -> dict:
    return httpx.get(live + "/api/selects", timeout=30).json()


def _fresh(fold, segments=None) -> None:
    """A clean bin: the cut, no keeps, no verdicts; the first pass has made its picks."""
    _record(True)
    _seed(fold, CUT if segments is None else segments, selects=[])


def _selects(pg, n: int | None = None, tab: str = "all") -> None:
    pg.evaluate("dock.open('bin')")
    pg.locator(f"#binTabs .tab[data-tab={tab}]").click()
    if n is not None:
        pg.wait_for_function(f"document.querySelectorAll('#library tr.keep').length === {n}",
                             timeout=10000)
    pg.wait_for_timeout(250)              # the tab's click re-reads the bin and rebuilds the rows


def _row(pg, clip: str, start: float):
    """The Selects row of a pick or keep, by its clip and start."""
    i = pg.evaluate("""([c, s]) => [...document.querySelectorAll('#library tr.keep')]
        .findIndex((r) => r._keep && r._keep.clip === c && Math.abs(r._keep.start - s) < 0.02)""",
                    [clip, start])
    assert i >= 0, (clip, start)
    return pg.locator("#library tr.keep").nth(i)


def _writes(pg) -> int:
    return pg.evaluate("rcPassReview.state.writes")


def _settle(pg, before: int, n: int = 1) -> None:
    """Wait for `n` more writes and the list's re-read after them."""
    pg.wait_for_function(f"rcPassReview.state.writes >= {before + n} && !rcPassReview.state.busy",
                         timeout=10000)
    pg.wait_for_timeout(400)


def _menu(pg, row, act: str, twice: bool = False) -> None:
    row.click(button="right")
    pg.wait_for_selector("[data-testid=sel-menu]:not([hidden])", timeout=3000)
    item = pg.locator(f"[data-testid=sel-menu] button[data-act={act}]")
    assert item.count() == 1, (act, pg.locator("[data-testid=sel-menu]").inner_text())
    item.click()
    if twice:
        pg.locator(f"[data-testid=sel-menu] button[data-act={act}]").click()


def _snap(clip: str, a: float, b: float, dur: float = 12.0) -> list[float]:
    """The pass's keep: start and end snapped outward to the clip's lines."""
    lines = LINES[clip.split(".")[0]]
    for s, e, _t in lines:
        if s < a < e:
            a = max(0.0, s - PAD_HEAD)
    for s, e, _t in lines:
        if s < b < e + NEAR_WORD:
            b = min(dur, e + PAD_TAIL)
            break
    return [round(a, 2), round(b, 2)]


def _with_picks(edit):
    """A route that answers /api/picks with the server's own answer, edited."""
    def handler(route):
        r = route.fetch()
        d = r.json()
        edit(d["picks"])
        route.fulfill(response=r, body=json.dumps(d), content_type="application/json")
    return handler


# ------------------------------------------------------------ the pass's verdicts

def test_check_keeps_a_pick_snapped_to_the_sentence_and_x_rejects_and_restore_brings_it_back(
        browser, live, fold):
    """/floor's P and X (test_floor_ui: "P keeps the preview snapped to the sentence",
    "X rejects the pick's own range"): ✓ on a pick's row keeps its preview snapped
    outward to the lines it cuts into; ✗ rejects the pick's own range, the row leaves the
    list and the "rejected" chip lists it, where Restore puts it back undecided."""
    _fresh(fold)
    picks = _picks(live)
    a = next(p for p in picks if p["clip"] == "CLIP_A.MP4")     # the lift line joke, 1.0–2.0
    # a preview whose edges fall inside the line, so the snap has something to do
    preview = [1.5, 1.8]

    def edit(rows):
        for p in rows:
            if p["id"] == a["id"]:
                p["preview"] = preview
    pg = _open(browser, live, routes=[("**/api/picks*", _with_picks(edit))])
    try:
        _selects(pg, 5)
        assert "✓ keep, ✗ reject, right-click for more" in pg.locator("#libHint").inner_text()
        assert pg.locator("#libHint a").count() == 0, "no link off the editor"
        row = _row(pg, a["clip"], a["start"])
        row.hover()
        assert row.locator(".pv button").count() == 2 and row.locator(".pv").is_visible()
        w = _writes(pg)
        row.locator(".pv button[data-pv=pick]").click()
        _settle(pg, w)
        want = _snap(a["clip"], *preview)
        assert want == [0.75, 2.45], want
        keeps = _bin(live)["selects"]
        assert [(k["clip"], k["start"], k["end"], k["hero"]) for k in keeps] == [(a["clip"], *want, False)]
        assert keeps[0]["why"] == a["why"] and keeps[0]["source"] == "selects"
        # the pick is the keep's row now: "yours", and the pick's own row is gone
        pg.wait_for_function("document.querySelectorAll('#library tr.keep.yours').length === 1", timeout=5000)
        assert pg.locator("#library tr.keep.pick").count() == 4

        # ✗ on another: its own range, out of the list, under the rejected chip
        b = next(p for p in picks if p["clip"] == "CLIP_B.MP4")
        row = _row(pg, b["clip"], b["start"])
        row.hover()
        w = _writes(pg)
        row.locator(".pv button[data-pv=reject]").click()
        _settle(pg, w)
        v = _bin(live)["floor"]["verdicts"]
        assert [(x["clip"], x["start"], x["end"], x["verdict"]) for x in v] == [
            (b["clip"], round(b["start"], 2), round(b["end"], 2), "reject")]
        assert pg.locator("#library tr.keep.pick").count() == 3
        assert "rejected" in pg.locator("#toast").inner_text()
        # the chips sit under "more found ▸" with the evidence chips (INTAKE M16)
        pg.locator("#moreFound").click()
        chip = pg.locator("#binFilter .chip[data-chip=rejected]")
        assert chip.inner_text().strip() == "rejected 1"
        chip.click()
        pg.wait_for_function("document.querySelectorAll('#library tr.keep.rejected').length === 1", timeout=5000)
        assert "Rejected" in pg.locator("#libHint").inner_text()
        w = _writes(pg)
        pg.locator("#library tr.keep.rejected button.use.restore").click()
        _settle(pg, w)
        assert _bin(live)["floor"]["verdicts"] == []
        # the chip lets go with its last row, and the pick is back, undecided
        pg.wait_for_function("document.querySelectorAll('#library tr.keep.pick').length === 4", timeout=5000)
        assert pg.locator("#binFilter .chip[data-chip=rejected]").count() == 0
        assert pg.errors == []
    finally:
        pg.close()


def test_the_menu_keeps_a_hero_marks_later_takes_a_note_and_undo_puts_each_back(browser, live, fold):
    """/floor's 1, U, N and ⌘Z ("X rejects ... and 1 marks a hero", "the verdict carries
    the typed note", "ctrl z restores the verdict with its trim and note"), on the row's
    right-click menu."""
    _fresh(fold)
    picks = _picks(live)
    a, c = picks[0], next(p for p in picks if p["clip"] == "CLIP_C.MP4")
    pg = _open(browser, live)
    try:
        _selects(pg, 5)
        # Keep as hero
        w = _writes(pg)
        _menu(pg, _row(pg, a["clip"], a["start"]), "hero")
        _settle(pg, w)
        keeps = _bin(live)["selects"]
        assert len(keeps) == 1 and keeps[0]["hero"] is True
        hero = _row(pg, keeps[0]["clip"], keeps[0]["start"])
        assert "★" in hero.locator(".w").inner_text()
        # ...and Undo: no keep, the pick undecided again
        w = _writes(pg)
        _menu(pg, hero, "undo")
        _settle(pg, w)
        assert _bin(live)["selects"] == []
        pg.wait_for_function("document.querySelectorAll('#library tr.keep.pick').length === 5", timeout=5000)

        # Later: the row stays, marked; the later chip lists it; Undecided takes it off
        w = _writes(pg)
        _menu(pg, _row(pg, c["clip"], c["start"]), "later")
        _settle(pg, w)
        assert [x["verdict"] for x in _bin(live)["floor"]["verdicts"]] == ["later"]
        row = _row(pg, c["clip"], c["start"])
        assert "later" in row.get_attribute("class") and row.locator(".who.later").inner_text() == "later"
        assert pg.locator("#binFilter .chip[data-chip=later]").text_content().strip() == "later 1"

        # a note on it: typed in the note box, Enter keeps it; the row says it
        _menu(pg, row, "note")
        box = pg.locator("[data-testid=sel-note] input")
        box.wait_for(timeout=3000)
        w = _writes(pg)
        box.fill("the turn at the top")
        box.press("Enter")
        _settle(pg, w)
        v = _bin(live)["floor"]["verdicts"]
        assert v[0]["verdict"] == "later" and v[0]["note"] == "the turn at the top"
        assert "your note: the turn at the top" in _row(pg, c["clip"], c["start"]).locator(".w").inner_text()
        # Undecided: the later mark goes (the note with it)
        w = _writes(pg)
        _menu(pg, _row(pg, c["clip"], c["start"]), "clear")
        _settle(pg, w)
        assert _bin(live)["floor"]["verdicts"] == []
        assert pg.locator("#library tr.keep.later").count() == 0

        # a keep of yours: Make hero, Not a hero, Remove from Selects
        w = _writes(pg)
        _menu(pg, _row(pg, a["clip"], a["start"]), "pick")
        _settle(pg, w)
        k = _bin(live)["selects"][0]
        w = _writes(pg)
        _menu(pg, _row(pg, k["clip"], k["start"]), "mkhero")
        _settle(pg, w)
        assert _bin(live)["selects"][0]["hero"] is True
        w = _writes(pg)
        _menu(pg, _row(pg, k["clip"], k["start"]), "unhero")
        _settle(pg, w, 2)
        assert _bin(live)["selects"][0]["hero"] is False
        w = _writes(pg)
        _menu(pg, _row(pg, k["clip"], k["start"]), "remove")
        _settle(pg, w)
        assert _bin(live)["selects"] == []
        assert pg.errors == []
    finally:
        pg.close()


def test_keep_takes_the_source_monitors_in_and_out_once_they_were_moved(browser, live, fold):
    """/floor's trim ("dragging a handle trims", "trim keys snap"), in Premiere's idiom: the
    pick opened in the Source monitor (double-click) has its In and Out; moved there, Keep
    keeps exactly them."""
    _fresh(fold)
    picks = _picks(live)
    p = next(q for q in picks if q["clip"] == "CLIP_B.MP4" and q["start"] > 5)   # here goes nothing
    pg = _open(browser, live)
    try:
        _selects(pg, 5)
        _row(pg, p["clip"], p["start"]).dblclick()
        pg.wait_for_function(f"window.rcSource && rcSource.clip === '{p['clip']}'", timeout=5000)
        # the In and Out moved in the Source monitor (I / O there), 0.4 s in from each side
        a, b = round(p["start"] + 0.4, 2), round(p["end"] - 0.4, 2)
        pg.evaluate("""([c, a, b]) => { const s = dock.source; s.marks[c] = [a, b]; s.paint(); s.publish(); }""",
                    [p["clip"], a, b])
        assert pg.evaluate("rcPassReview.keepRange(rcSelects.picks().find((q) => q.id === %r))" % p["id"]) == [a, b]
        w = _writes(pg)
        _menu(pg, _row(pg, p["clip"], p["start"]), "pick")
        _settle(pg, w)
        k = _bin(live)["selects"]
        assert [(x["clip"], x["start"], x["end"]) for x in k] == [(p["clip"], a, b)]
        assert pg.errors == []
    finally:
        pg.close()


def test_the_rest_of_a_clip_and_the_picks_a_filter_shows_are_rejected_in_one_undo(browser, live, fold):
    """/floor's ⇧X without a filter (the rest of the clip) and with one (every undecided
    match, after one confirm): one POST per pick, one Undo for the lot."""
    _fresh(fold)
    picks = _picks(live)
    a1, a2 = [p for p in picks if p["clip"] == "CLIP_A.MP4"]
    pg = _open(browser, live)
    try:
        _selects(pg, 5)
        w = _writes(pg)
        _menu(pg, _row(pg, a1["clip"], a1["start"]), "restreject")
        _settle(pg, w, 2)
        v = _bin(live)["floor"]["verdicts"]
        assert sorted((x["clip"], x["start"]) for x in v) == [(a1["clip"], round(a1["start"], 2)),
                                                              (a2["clip"], round(a2["start"], 2))]
        assert "rejected 2 picks in CLIP_A" in pg.locator("#toast").inner_text()
        pg.wait_for_function("document.querySelectorAll('#library tr.keep.pick').length === 3", timeout=5000)
        w = _writes(pg)
        _menu(pg, _row(pg, picks[2]["clip"], picks[2]["start"]), "undo")
        _settle(pg, w, 2)                         # a clear each, nothing before them to put back
        assert _bin(live)["floor"]["verdicts"] == []
        pg.wait_for_function("document.querySelectorAll('#library tr.keep.pick').length === 5", timeout=5000)

        # no filter: no batch item; a filter (the find box's words): it asks once, then rejects
        pg.locator("#library tr.keep").first.click(button="right")
        assert pg.locator("[data-testid=sel-menu] button[data-act=batch]").count() == 0
        pg.keyboard.press("Escape")
        pg.locator("#findQ").fill("CLIP_B")
        pg.wait_for_function("document.querySelectorAll('#library tr.keep').length === 2", timeout=5000)
        row = pg.locator("#library tr.keep").first
        row.click(button="right")
        item = pg.locator("[data-testid=sel-menu] button[data-act=batch]")
        assert item.inner_text() == "Reject the 2 picks shown…"
        w = _writes(pg)
        item.click()
        assert pg.locator("[data-testid=sel-menu]").is_visible(), "the first click only asks"
        assert pg.locator("[data-testid=sel-menu] button[data-act=batch]").inner_text() == "Click again: reject 2 picks"
        assert _writes(pg) == w
        pg.locator("[data-testid=sel-menu] button[data-act=batch]").click()
        _settle(pg, w, 2)
        v = _bin(live)["floor"]["verdicts"]
        assert sorted(x["clip"] for x in v) == ["CLIP_B.MP4", "CLIP_B.MP4"]
        assert all(x["why"] == 'filtered out: "CLIP_B"' for x in v), v
        assert pg.errors == []
    finally:
        pg.close()


def test_show_its_takes_and_keep_one_rejecting_the_others(browser, live, fold):
    """/floor's T ("compares the takes and P keeps one and rejects the rest in one undo"):
    a pick in a take cluster shows its takes in the list (the take chip), and Keep this
    take, reject the others keeps it and rejects its undecided siblings, one Undo."""
    _fresh(fold)
    picks = _picks(live)
    a1, a2 = [p for p in picks if p["clip"] == "CLIP_A.MP4"]
    tid = "CLIP_A.MP4:speech:1"

    def edit(rows):                         # the fixture's two CLIP_A lines as two takes
        for n, p in enumerate([q for q in rows if q["clip"] == "CLIP_A.MP4"], 1):
            other = a2["id"] if p["id"] == a1["id"] else a1["id"]
            p["take"] = {"id": tid, "n": n, "of": 2, "others": [other]}
    pg = _open(browser, live, routes=[("**/api/picks*", _with_picks(edit))])
    try:
        _selects(pg, 5)
        _menu(pg, _row(pg, a2["clip"], a2["start"]), "takes")
        pg.wait_for_function("document.querySelectorAll('#library tr.keep').length === 2", timeout=5000)
        assert pg.locator(f"#binFilter .chip[data-chip='take:{tid}']").count() == 1
        assert "The takes of one moment" in pg.locator("#libHint").inner_text()
        w = _writes(pg)
        _menu(pg, _row(pg, a2["clip"], a2["start"]), "keeptake")
        _settle(pg, w, 2)
        b = _bin(live)
        assert [(k["clip"], k["start"]) for k in b["selects"]] == [(a2["clip"], pytest.approx(a2["start"], abs=0.3))]
        assert [(x["clip"], x["verdict"], x["why"]) for x in b["floor"]["verdicts"]] == [
            (a1["clip"], "reject", f"other take of {tid}")]
        # one Undo for the take and its rejected sibling
        pg.locator(f"#binFilter .chip[data-chip='take:{tid}']").click()
        w = _writes(pg)
        _menu(pg, pg.locator("#library tr.keep").first, "undo")
        _settle(pg, w, 2)
        b = _bin(live)
        assert b["selects"] == [] and b["floor"]["verdicts"] == []
        assert pg.errors == []
    finally:
        pg.close()


# ------------------------------------------------------- File › Project Settings…

def _settings(pg) -> dict:
    pg.locator("#menubar .menu[data-menu=file] .mtop").click()
    pg.locator("[data-testid=menu-project-settings]").click()
    pg.wait_for_selector("[data-testid=project-settings][open]", timeout=3000)
    pg.wait_for_function("rcProjectSettings.state.settings !== null", timeout=5000)
    return pg.evaluate("rcProjectSettings.state.settings")


def _workers(pg) -> dict:
    return pg.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#psWorkers input')]
        .map((f) => [f.dataset.stage, f.value]))""")


def test_project_settings_holds_the_cap_and_the_workers_and_says_the_servers_400(browser, live, fold):
    """/open's settings drawer ("the gear opens settings and a saved cap moves the budget
    line", "a worker count is clamped in the ui and a bad one sent on purpose shows the
    400"), as File › Project Settings…: OK writes the whole form once, Cancel and Esc
    nothing; the toast says what changed."""
    _fresh(fold)
    pg = _open(browser, live, size=(1440, 900))
    try:
        s = _settings(pg)
        got = httpx.get(live + "/api/settings", timeout=30).json()
        assert s["workers"] == got["workers"] and set(got["workers"]) == {"probe", "asr", "proxy", "sheet", "picks"}
        # no cap is the default: the radio, the field empty, the words
        assert got["budget_usd"] is None
        assert pg.locator("[data-testid=ps-cap-none]").is_checked()
        assert pg.locator("[data-testid=ps-cap]").input_value() == ""
        assert pg.locator("#psCapAbout").inner_text().startswith("No cap")
        assert "spent" not in pg.locator("[data-testid=ps-spent]").inner_text() or not s.get("charged", True)
        assert _workers(pg) == {k: str(v) for k, v in got["workers"].items()}
        # the index's order, the footage screen's toggle: most promising first by default
        assert got["order"] == "priority" and pg.locator("[data-testid=ps-order-priority]").is_checked()
        hints = pg.locator("#psWorkers .ps-about").all_inner_texts()
        assert any("model loads once" in h for h in hints) and any("spends faster, not better" in h for h in hints)
        # Esc closes it, nothing written
        pg.keyboard.press("Escape")
        assert pg.locator("[data-testid=project-settings]").is_hidden()
        # a cap of 5: typing it chooses the cap; OK writes it and says what changed
        _settings(pg)
        pg.locator("[data-testid=ps-cap]").fill("5")
        assert pg.locator("[data-testid=ps-cap-on]").is_checked()
        sheet = pg.locator("#psWorkers input[data-stage=sheet]")
        step = pg.locator("#psWorkers .ps-step[data-stage=sheet]")
        sheet.fill("12")
        sheet.press("Tab")
        assert sheet.input_value() == "8"
        step.locator("button[data-d='1']").click()
        assert sheet.input_value() == "8"
        sheet.fill("0")
        sheet.press("Tab")
        assert sheet.input_value() == "1"
        step.locator("button[data-d='1']").click()
        step.locator("button[data-d='1']").click()
        assert sheet.input_value() == "3"
        pg.locator("[data-testid=ps-order-capture]").check()
        pg.locator("[data-testid=ps-ok]").click()
        pg.wait_for_function("document.querySelector('#toast').textContent.startsWith('Project Settings saved')", timeout=5000)
        toast = pg.locator("#toast").inner_text()
        assert "cap none → $5.00" in toast and f"sheet workers {s['workers']['sheet']} → 3" in toast, toast
        assert "order most promising first → capture order" in toast, toast
        assert pg.locator("[data-testid=project-settings]").is_hidden()
        got = httpx.get(live + "/api/settings", timeout=30).json()
        assert got["budget_usd"] == 5.0 and got["source"]["budget_usd"] == "settings" and got["workers"]["sheet"] == 3
        assert got["order"] == "capture"
        # a bad one the form would never send, sent on purpose: the server's 400, in the
        # dialog, which stays open; nothing written
        _settings(pg)
        assert pg.locator("[data-testid=ps-cap]").input_value() == "5"
        for bad in ({"workers": {"sheet": 12}}, {"workers": {"grade": 1}}, {"budget_usd": 0}):
            assert pg.evaluate("(b) => rcProjectSettings.ok(b)", bad) is None
            err = pg.locator("[data-testid=ps-error]")
            assert err.is_visible() and err.inner_text().startswith("Not saved:"), err.inner_text()
            assert pg.locator("[data-testid=project-settings]").is_visible()
        got = httpx.get(live + "/api/settings", timeout=30).json()
        assert got["workers"]["sheet"] == 3 and got["budget_usd"] == 5.0
        # Reset fills the defaults; OK applies them (the cap back to none)
        pg.locator("[data-testid=ps-reset]").click()
        assert pg.locator("[data-testid=ps-cap-none]").is_checked()
        assert pg.locator("[data-testid=ps-order-priority]").is_checked()
        assert _workers(pg) == {k: str(v) for k, v in got["defaults"]["workers"].items()}
        pg.locator("[data-testid=ps-ok]").click()
        pg.wait_for_function("document.querySelector('#toast').textContent.includes('cap $5.00 → none')", timeout=5000)
        got = httpx.get(live + "/api/settings", timeout=30).json()
        assert got["budget_usd"] is None and got["workers"] == got["defaults"]["workers"]
        assert got["order"] == "priority"
        assert pg.errors == []
    finally:
        pg.close()


def test_a_cap_from_the_environment_disables_the_choice(browser, live, fold, monkeypatch):
    """/open's "a cap from the environment disables the field": ROUGHCUT_BUDGET_USD in the
    server's process wins; the choice is disabled with the reason, and OK still writes
    the workers without sending a cap."""
    _fresh(fold)
    monkeypatch.setenv("ROUGHCUT_BUDGET_USD", "7.5")
    pg = _open(browser, live, size=(1440, 900))
    try:
        s = _settings(pg)
        assert s["source"]["budget_usd"] == "env" and s["budget_usd"] == 7.5
        cap = pg.locator("[data-testid=ps-cap]")
        assert cap.is_disabled() and float(cap.input_value()) == 7.5
        assert pg.locator("[data-testid=ps-cap-none]").is_disabled() and pg.locator("[data-testid=ps-cap-on]").is_checked()
        about = pg.locator("#psCapAbout").inner_text()
        assert "ROUGHCUT_BUDGET_USD" in about and "wins" in about, about
        assert "of $7.50 cap" in pg.locator("[data-testid=ps-spent]").inner_text()
        pg.locator("#psWorkers .ps-step[data-stage=probe] button[data-d='1']").click()
        pg.locator("[data-testid=ps-ok]").click()
        pg.wait_for_function("document.querySelector('#toast').textContent.includes('probe workers')", timeout=5000)
        got = httpx.get(live + "/api/settings", timeout=30).json()
        assert got["source"]["budget_usd"] == "env" and got["workers"]["probe"] == s["workers"]["probe"] + 1
        # back to the defaults for what follows
        httpx.put(live + "/api/settings", json={"workers": got["defaults"]["workers"]}, timeout=30)
        assert pg.errors == []
    finally:
        pg.close()


# ------------------------------------------------------ the voice, in the AI prompt

@pytest.fixture
def mic_browser():
    with sync_playwright() as pw:
        # a fake microphone, so holding the mic records (the old screens' arrangement)
        b = pw.chromium.launch(args=["--use-fake-ui-for-media-stream",
                                     "--use-fake-device-for-media-stream"])
        yield b
        b.close()


def _hold_mic(pg) -> None:
    mic = pg.locator("[data-testid=ai-mic]")
    box = mic.bounding_box()
    pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    pg.mouse.down()
    pg.wait_for_function("document.querySelector('[data-testid=ai-mic]').classList.contains('live')", timeout=3000)
    pg.wait_for_timeout(700)
    pg.mouse.up()


def test_holding_the_mic_types_what_was_said_into_the_ai_prompt(mic_browser, live, fold, monkeypatch):
    """/open's and /floor's hold-to-speak ("holding V ... dictates into it", "a dictated
    note lands in the slot"), in the AI prompt: the mic shows while the prompt has the
    focus (nothing added at rest), the recording goes through POST /api/dictate for real
    with the recogniser stubbed on the server, and the words are typed into the prompt,
    not sent: ↵ is still the ask."""
    from roughcut import dictate
    monkeypatch.setattr(dictate, "available", lambda: True)
    monkeypatch.setattr(dictate, "transcribe", lambda path, names=None: {
        "text": "warmer on the snow shots", "latency_ms": 800, "model": "stub", "duration_s": 0.7})
    _fresh(fold)
    asks: list[str] = []
    pg = _open(mic_browser, live, size=(1440, 900))
    pg.on("request", lambda r: asks.append(r.url) if "/api/assist" in r.url and r.method == "POST" else None)
    try:
        mic = pg.locator("[data-testid=ai-mic]")
        pg.wait_for_function("window.rcVoice && rcVoice.state.avail === true", timeout=5000)
        assert mic.count() == 1 and not mic.is_visible(), "at rest the board is as it was"
        box = pg.locator("[data-testid=ai-prompt-input]")
        box.click()
        assert mic.is_visible()
        box.fill("and")
        sent = pg.evaluate("rcVoice.state.sent")
        _hold_mic(pg)
        pg.wait_for_function(f"rcVoice.state.sent > {sent}", timeout=10000)
        pg.wait_for_function("document.querySelector('[data-testid=ai-prompt-input]').value.includes('snow')",
                             timeout=5000)
        assert box.input_value() == "and warmer on the snow shots"
        assert "0.8 s" in pg.locator("#toast").inner_text()
        assert pg.evaluate("document.activeElement && document.activeElement.dataset.testid") == "ai-prompt-input"
        assert asks == [], "speaking never asks the AI by itself"
        assert pg.errors == []
    finally:
        pg.close()


def test_the_mic_is_not_there_without_the_recogniser_and_goes_on_a_501(mic_browser, live, fold, monkeypatch):
    """/open's "an absent recogniser hides the mic before any hold" and "the mic hides on
    a 501 and typing stays"."""
    from roughcut import dictate
    _fresh(fold)
    monkeypatch.setattr(dictate, "available", lambda: False)
    pg = _open(mic_browser, live, size=(1440, 900))
    try:
        pg.wait_for_function("window.rcVoice && rcVoice.state.avail === false", timeout=5000)
        pg.locator("[data-testid=ai-prompt-input]").click()
        assert not pg.locator("[data-testid=ai-mic]").is_visible()
        # said available, then the server's 501 on the hold: the mic goes, typing stays
        pg.evaluate("rcVoice.state.avail = true; document.querySelector('[data-testid=ai-mic]').hidden = false")
        hits: list[int] = []
        pg.on("response", lambda r: hits.append(r.status) if "/api/dictate" in r.url else None)
        _hold_mic(pg)
        pg.wait_for_function("document.querySelector('#toast').textContent.includes('not installed')", timeout=10000)
        assert hits == [501], hits
        assert not pg.locator("[data-testid=ai-mic]").is_visible()
        pg.locator("[data-testid=ai-prompt-input]").click()
        pg.keyboard.type("very silly")
        assert pg.locator("[data-testid=ai-prompt-input]").input_value() == "very silly"
        assert pg.errors == []
    finally:
        pg.close()


# ------------------------------------------------- junk, in the Project panel's Status

def test_a_junk_proposal_shows_in_the_project_panels_status(browser, live, fold):
    """/open's card badge ("a problem shows as one badge and junk is answered on the
    card"): a clip the junk pass proposes reads "junk?" in the Project panel's Status,
    its hover says where it is answered (Selects' Confirm / Keep), and a confirmed one
    "junk"."""
    _fresh(fold)

    def edit(route):
        r = route.fetch()
        d = r.json()
        for c in d["clips"]:
            c["junk"] = {"CLIP_A.MP4": "proposed", "CLIP_B.MP4": "confirmed"}.get(c["clip"], "clean")
        route.fulfill(response=r, body=json.dumps(d), content_type="application/json")
    pg = _open(browser, live, routes=[("**/api/clips", edit)])
    try:
        pg.evaluate("dock.open('project')")
        pg.wait_for_selector("#projList [data-clip='CLIP_A.MP4'] td.st", timeout=10000)
        st = pg.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#projList tr[data-clip]')]
            .map((r) => [r.dataset.clip, [r.querySelector('td.st').dataset.state, r.querySelector('td.st').title]]))""")
        assert st["CLIP_A.MP4"][0] == "junk?" and "Selects" in st["CLIP_A.MP4"][1], st
        assert st["CLIP_B.MP4"][0] == "junk", st
        assert st["CLIP_C.MP4"][0] not in ("junk?", "junk"), st
        assert pg.errors == []
    finally:
        pg.close()
