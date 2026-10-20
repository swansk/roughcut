"""Easy to learn, the AI features above all (REDESIGN-GOAL dimension 16, Karl's K3 e).

Karl, 2026-10-16: *"this whole thing needs to be easy to learn how to use, especially how
to use those AI features."* In real Chromium, on the suite's synthetic bin:

  * the first-run walk-through (app/static/tour.js) opens on a browser's first visit at
    step 1 of 6, ON the workspace (no full-screen modal: the panels still answer), and not
    on the second load; Skip ends it for good, Esc never does (it closes what Show me
    opened: verdict 10); Help › Learn Roughcut… and AI ›
    Learn the AI features open it again (the loop, then the AI half alone);
  * every step of both walk-throughs points at a control that is on screen (or at the menu
    that opens it), with the callout wholly on screen and clear of that control, at
    1440×900 and 1920×1080, on a fresh bin and on a cut;
  * "Show me" opens things and never starts a model call: no POST reaches /api/ask*,
    /api/assist* or /api/firstpass* while every Show me of both walk-throughs is pressed;
  * every AI control has a hover card after ~500 ms with what it does, what it costs in
    REDESIGN-GOAL S's words on `claude_cli` ("free on your Claude plan …", never "spent",
    a dollar figure only as the API's estimate) and what it changes;
  * the empty states name their next action: no footage → Import (Timeline, Program and
    Source, each calling `rcImport.open()`); footage and no pass → drag / `,` and Run first
    pass; a proposed cut waiting off screen → Review the proposed cut, which opens it.

Every other test's browser carries the walk-through's flag (conftest.py's init script); the
first-run tests open theirs through the `first_run_context` fixture.

    uv run --with pytest --with fastapi --with uvicorn --with httpx --with numpy \
        --with playwright pytest app/tests/test_tour_ui.py -q

`ROUGHCUT_TOUR_SHOTS=<dir>` saves a screenshot of every step at both sizes.
"""

from __future__ import annotations

import json
import os
import re
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

TOUR = '[data-testid="tour"]'
SIZES = [(1440, 900), (1920, 1080)]
PRICED = re.compile(r"/api/(ask|assist|firstpass)\b")
SEED = {"variant": "T", "title": "tour", "orient": "none", "story": "", "target_s": [5, 20],
        "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "the greeting"},
                     {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "the reply"}]}
PLAN = {"segments": [{"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0, "why": "the greeting"},
                     {"clip": "CLIP_C.MP4", "in": 0.5, "out": 4.0, "why": "the reply"}],
        "notes": "First pass: two shots."}
READY = ("window.rcFirstPass && rcFirstPass.state() && window.tl && document.querySelector('#tl')"
         " && document.querySelector('#tl').childElementCount > 0")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def bins(project, tmp_path_factory):
    """The suite's three clips, all heard; and a bin with no footage at all."""
    root = tmp_path_factory.mktemp("tour")
    full, heard, empty, deaf = root / "full", root / "heard", root / "empty", root / "deaf"
    for d in (full, heard, empty, deaf):
        d.mkdir()
    for stem in project["stems"]:
        shutil.copy(project["footage"] / f"{stem}.MP4", full / f"{stem}.MP4")
        (heard / f"{stem}.audio.json").write_text(json.dumps(_sidecar(stem)), encoding="utf-8")
    return {"root": root, "full": full, "heard": heard, "empty": empty, "deaf": deaf,
            "assets": project["assets"]}


@pytest.fixture(scope="module")
def live(bins):
    import uvicorn
    import server

    mp = pytest.MonkeyPatch()
    mp.setenv("ROUGHCUT_BACKEND", "claude_cli")       # the subscription: section S's words
    server.configure(None, bins["full"], bins["heard"], bins["root"] / "work0", proxies=False,
                     assets=bins["assets"])
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_level="error"))
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
    mp.undo()


_CUT_WORK: dict = {}


def _state(bins, tmp_path, state: str) -> None:
    """Point the server at a bin in `state`: 'fresh' (footage heard, no cut, no pass),
    'cut' (two shots on V1) or 'empty' (no footage)."""
    import server

    if state == "cut":
        work = _CUT_WORK.get("dir")
        if work is None:                       # one cut for the module: its proxies once
            work = bins["root"] / "cutwork"
            work.mkdir()
            _CUT_WORK["dir"] = work
        edl = work / "edl.json"
        edl.write_text(json.dumps(SEED, indent=1), encoding="utf-8")
        server.configure(edl, bins["full"], bins["heard"], work, proxies=False,
                         assets=bins["assets"])
        server.ensure_proxies(sorted(p.name for p in bins["full"].iterdir()))
        return
    footage = bins["full"] if state == "fresh" else tmp_path / "nofootage"
    footage.mkdir(exist_ok=True)
    server.configure(None, footage, bins["heard"] if state == "fresh" else bins["deaf"],
                     tmp_path / f"work-{state}", proxies=False, assets=bins["assets"])


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
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


def _done(pg) -> None:
    assert pg.errors == [], pg.errors
    pg.context.close()


STEP_JS = """() => {
  const t = document.querySelector('[data-testid="tour"]');
  const ring = document.querySelector('[data-testid="tour-ring"]');
  const a = window.rcTour.anchor();
  const box = (el) => { if (!el) return null; const r = el.getBoundingClientRect();
    return {l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height}; };
  const ab = box(a);
  let covered = false;
  if (ab && ab.w * ab.h < 200 * 200) {
    const x = Math.min(Math.max(ab.l + ab.w / 2, 0), innerWidth - 1);
    const y = Math.min(Math.max(ab.t + ab.h / 2, 0), innerHeight - 1);
    const hit = document.elementFromPoint(x, y);
    covered = !!(hit && hit.closest('[data-testid="tour"]'));
  }
  return {step: t.dataset.step, key: t.dataset.key, which: t.dataset.which, anchor: t.dataset.anchor,
          side: t.dataset.side, callout: box(t), ring: ring && !ring.hidden ? box(ring) : null,
          heads: box(document.querySelector('#tl .tl-side')),
          transports: [...document.querySelectorAll('#pSource .transport, #pProgram .transport')].map(box),
          target: ab, tag: a ? (a.id || a.className || a.tagName) : null,
          isMenu: !!(a && a.classList && a.classList.contains('mtop')),
          covered, vw: innerWidth, vh: innerHeight,
          title: t.querySelector('#tourTitle').textContent,
          body: t.querySelector('.rt-body').textContent,
          n: t.querySelectorAll('[data-testid="tour-step"]').length};
}"""


def _on_screen(b, vw, vh) -> bool:
    return b and b["l"] >= 0 and b["t"] >= 0 and b["r"] <= vw + 0.5 and b["b"] <= vh + 0.5


def _overlap(a, b) -> bool:
    return bool(a and b and a["l"] < b["r"] and a["r"] > b["l"] and a["t"] < b["b"] and a["b"] > b["t"])


# ---------------------------------------------------------------- the first run


def test_the_first_run_opens_step_one_on_the_workspace_and_only_once(live, bins, tmp_path, browser,
                                                                      first_run_context):
    _state(bins, tmp_path, "fresh")
    pg = _page(browser, live, first=first_run_context)
    pg.wait_for_selector(TOUR, timeout=15000)
    info = pg.evaluate(STEP_JS)
    assert (info["step"], info["key"], info["which"], info["n"]) == ("1", "import", "loop", 6), info
    assert info["title"] == "Bring in footage" and "Import" in info["body"], info
    assert pg.evaluate("rcTour.active") is True
    assert pg.evaluate("localStorage.getItem('roughcut.tour')") == "shown"
    # never a modal: small, and the workspace answers under it (a tab, the ruler)
    c = info["callout"]
    assert c["w"] * c["h"] < 0.08 * info["vw"] * info["vh"], c
    # and never over the Timeline's track headers (verdict 9, dimension 1: it covered V1 / V2)
    # nor the monitors' transport rows
    assert info["heads"] and not _overlap(c, info["heads"]), f"step 1 covers the track headers: {info}"
    assert len(info["transports"]) == 2 and not any(_overlap(c, b) for b in info["transports"]), \
        f"step 1 covers a monitor's transport: {info}"
    pg.locator('#pProject .ws-tab[data-tool="bin"]').click()
    pg.wait_for_selector('#pProject section[data-tool="bin"]:not([hidden])', timeout=3000)
    hit = pg.evaluate("""(() => { const r = document.querySelector('#tl .tl-ruler').getBoundingClientRect();
        const el = document.elementFromPoint(r.left + r.width * 0.6, r.top + r.height / 2);
        return !!(el && el.closest('#tl')); })()""")
    assert hit, "something covers the Timeline while the walk-through is up"
    assert pg.locator(TOUR).is_visible()
    # the second load: no walk-through
    pg.reload()
    pg.wait_for_function(READY, timeout=20000)
    pg.wait_for_timeout(2500)
    assert pg.locator(TOUR).count() == 0
    assert pg.evaluate("rcTour.active") is False
    _done(pg)


def test_skip_ends_it_for_good(live, bins, tmp_path, browser, first_run_context):
    _state(bins, tmp_path, "fresh")
    pg = _page(browser, live, first=first_run_context)
    pg.wait_for_selector(TOUR, timeout=15000)
    pg.locator('[data-testid="tour-next"]').click()
    pg.wait_for_function("document.querySelector('[data-testid=tour]').dataset.step === '2'")
    # Esc is not Skip (verdict 10, punch 9): the walk-through is still up
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)
    assert pg.locator(TOUR).count() == 1 and pg.evaluate("rcTour.active") is True
    pg.locator('[data-testid="tour-skip"]').click()
    pg.wait_for_selector(TOUR, state="detached", timeout=3000)
    assert pg.locator('[data-testid="tour-ring"]').count() == 0
    assert pg.evaluate("localStorage.getItem('roughcut.tour')") == "skipped"
    # where to find it again, said once
    assert "Learn Roughcut" in pg.locator("#toast").inner_text()
    pg.reload()
    pg.wait_for_function(READY, timeout=20000)
    pg.wait_for_timeout(2500)
    assert pg.locator(TOUR).count() == 0
    _done(pg)


def test_help_and_the_ai_menu_open_it_again(live, bins, tmp_path, browser):
    _state(bins, tmp_path, "fresh")
    pg = _page(browser, live)                       # the suite's flag: no first run
    pg.wait_for_timeout(1500)
    assert pg.locator(TOUR).count() == 0
    pg.locator('#menubar .menu[data-menu="help"] .mtop').click()
    pg.locator('[data-testid="menu-tour"]').click()
    pg.wait_for_selector(TOUR, timeout=3000)
    info = pg.evaluate(STEP_JS)
    assert (info["which"], info["step"], info["n"]) == ("loop", "1", 6), info
    pg.locator('[data-testid="tour-skip"]').click()
    pg.wait_for_selector(TOUR, state="detached", timeout=3000)
    pg.locator('#menubar .menu[data-menu="ai"] .mtop').click()
    pg.locator('#menubar [data-cmd="tour:ai"]').click()
    pg.wait_for_selector(TOUR, timeout=3000)
    info = pg.evaluate(STEP_JS)
    assert (info["which"], info["step"], info["n"]) == ("ai", "1", 5), info
    # the AI half: the prompt, review per op, the menu, Look deeper, what it costs
    keys = []
    for i in range(info["n"]):
        keys.append(pg.evaluate(STEP_JS)["key"])
        if i < info["n"] - 1:
            pg.locator('[data-testid="tour-next"]').click()
    assert keys == ["prompt", "review", "menu", "deep", "cost"], keys
    last = pg.evaluate(STEP_JS)
    assert "free" in last["body"] and "Hover any AI control" in last["body"], last["body"]
    assert pg.locator('[data-testid="tour-next"]').inner_text() == "Done"
    pg.locator('[data-testid="tour-next"]').click()
    pg.wait_for_selector(TOUR, state="detached", timeout=3000)
    assert pg.evaluate("localStorage.getItem('roughcut.tour')") == "done"
    _done(pg)


def test_esc_closes_what_show_me_opened_never_the_tour(live, bins, tmp_path, browser):
    """Verdict 10, punch 9 (the K3 walk): Esc, pressed to clear the AI prompt, ended the whole
    walk-through. Esc closes what Show me opened — the first pass's panel, the Export dialog,
    the AI prompt's words — and the walk-through stays on its step; only Skip tour ends it."""
    _state(bins, tmp_path, "cut")
    pg = _page(browser, live)
    pg.wait_for_selector("#tl .blk", timeout=15000)
    pg.wait_for_timeout(600)
    pg.evaluate("rcTour.start()")
    pg.wait_for_selector(TOUR, timeout=3000)
    up = "rcTour.active && !!document.querySelector('[data-testid=tour]')"
    step = "document.querySelector('[data-testid=tour]').dataset.key"

    def to(key):
        for _ in range(8):
            if pg.evaluate(step) == key:
                return
            pg.locator('[data-testid="tour-next"]').click()
        raise AssertionError(f"no step {key}")

    # the first pass's panel: Esc closes it, the walk-through stays on "firstpass"
    to("firstpass")
    pg.locator('[data-testid="tour-show"]').click()
    pg.wait_for_selector('[data-testid="firstpass-panel"]', state="visible", timeout=5000)
    pg.keyboard.press("Escape")
    pg.wait_for_selector('[data-testid="firstpass-panel"]', state="hidden", timeout=3000)
    pg.wait_for_timeout(300)
    assert pg.evaluate(up) and pg.evaluate(step) == "firstpass"
    # the AI prompt Show me put the cursor in: typed words, then Esc to clear them
    to("ask")
    pg.locator('[data-testid="tour-show"]').click()
    pg.wait_for_function("document.activeElement && document.activeElement.dataset"
                         " && document.activeElement.dataset.testid === 'ai-prompt-input'", timeout=3000)
    pg.keyboard.type("warmer")
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)
    assert pg.evaluate(up) and pg.evaluate(step) == "ask", "Esc in the AI prompt ended the walk-through"
    # nothing open at all: Esc (the timeline's deselect) still leaves it up
    pg.locator("#tl .blk").first.click()
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)
    assert pg.evaluate(up) and pg.evaluate(step) == "ask"
    # the Export dialog: Esc closes it, and the walk-through is still on "export"
    to("export")
    pg.locator('[data-testid="tour-show"]').click()
    pg.wait_for_selector("#exportDialog[open]", timeout=5000)
    pg.keyboard.press("Escape")
    pg.wait_for_function("!document.querySelector('#exportDialog[open]')", timeout=3000)
    pg.wait_for_timeout(300)
    assert pg.evaluate(up) and pg.evaluate(step) == "export"
    assert pg.evaluate("localStorage.getItem('roughcut.tour')") != "skipped"
    pg.locator('[data-testid="tour-skip"]').click()
    pg.wait_for_selector(TOUR, state="detached", timeout=3000)
    _done(pg)


def _item_words(pg, cmd) -> str:
    """The AI menu item as it reads now: its label, less the shortcut and the trailing "…"."""
    return pg.evaluate(f"""(() => {{ const b = document.querySelector('#menubar [data-cmd="{cmd}"]');
        const h = b.querySelector('.aip-ml') || b;
        return [...h.childNodes].filter(n => !(n.nodeType === 1 && n.classList.contains('k')))
          .map(n => n.textContent).join('').replace(/\\s+/g, ' ').trim().replace(/\\s*…$/, ''); }})()""")


@pytest.mark.parametrize("passed", [False, True], ids=["never-run", "after-a-pass"])
def test_every_ai_menu_items_card_is_titled_by_the_item(live, bins, tmp_path, browser, passed):
    """Verdict 10, punch 9: the AI menu's "Re-run first pass…" had a card titled "Run first
    pass". Every AI-menu (and the Sequence menu's two AI items') card reads its item's words."""
    _state(bins, tmp_path, "cut")
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    if passed:
        def done(route):
            if route.request.method != "GET":
                return route.continue_()
            resp = route.fetch()
            body = resp.json()
            body.update({"state": "done", "when": time.time() - 600})
            route.fulfill(response=resp, body=json.dumps(body), content_type="application/json")
        pg.route("**/api/firstpass", done)
    pg.goto(live + "/")
    pg.wait_for_function(READY, timeout=20000)
    if passed:
        pg.wait_for_function("rcFirstPass.state().state === 'done'", timeout=10000)
    pg.wait_for_selector("#tl .blk", timeout=15000)
    pg.locator("#tl .blk").first.click()                  # the items that need a clip, enabled
    pg.locator('#menubar .menu[data-menu="ai"] .mtop').click()
    titles = {}
    for cmd in ("prompt", "firstpass", "ask", "shot", "colour", "fx", "deep", "find", "audit"):
        pg.locator(f'#menubar [data-cmd="ai:{cmd}"]').hover()
        pg.wait_for_function(f"(() => {{ const c = document.querySelector('[data-testid=ai-hover]');"
                             f" return c && !c.hidden && c.dataset.for === 'ai:{cmd}'; }})()", timeout=3000)
        titles[f"ai:{cmd}"] = (pg.evaluate(CARD_JS)["name"], _item_words(pg, f"ai:{cmd}"))
    pg.locator('#menubar .menu[data-menu="sequence"] .mtop').hover()
    for cmd in ("ask", "fx"):
        pg.locator(f'#menubar [data-cmd="{cmd}"]').hover()
        pg.wait_for_function(f"(() => {{ const c = document.querySelector('[data-testid=ai-hover]');"
                             f" return c && !c.hidden && c.dataset.for === '{cmd}'; }})()", timeout=3000)
        titles[cmd] = (pg.evaluate(CARD_JS)["name"], _item_words(pg, cmd))
    pg.keyboard.press("Escape")
    for cmd, (card, words) in titles.items():
        assert words and card == words, f"{cmd}: the card says {card!r}, the item {words!r}"
    want = "Re-run first pass" if passed else "Run first pass"
    assert titles["ai:firstpass"][0] == want, titles["ai:firstpass"]
    assert titles["ai:shot"][0] == "Ask about the selected clip", titles
    _done(pg)


# ---------------------------------------------------------------- every step, on screen


@pytest.mark.parametrize("state", ["fresh", "cut"])
@pytest.mark.parametrize("which", ["loop", "ai"])
@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_every_step_points_at_something_on_screen(live, bins, tmp_path, browser, size, which,
                                                  state):
    _state(bins, tmp_path, state)
    pg = _page(browser, live, size)
    if state == "cut":
        pg.wait_for_selector("#tl .blk", timeout=15000)
    pg.wait_for_timeout(800)
    pg.evaluate(f"rcTour.start({'undefined' if which == 'loop' else repr(which)})")
    pg.wait_for_selector(TOUR, timeout=3000)
    shots = os.environ.get("ROUGHCUT_TOUR_SHOTS")
    n = pg.evaluate(STEP_JS)["n"]
    assert n == (6 if which == "loop" else 5)
    seen = []
    for i in range(n):
        pg.wait_for_timeout(300)                       # the callout follows its anchor
        info = pg.evaluate(STEP_JS)
        seen.append((info["key"], info["anchor"], info["side"]))
        vw, vh = info["vw"], info["vh"]
        assert info["step"] == str(i + 1), info
        assert info["target"] and info["target"]["w"] >= 2 and info["target"]["h"] >= 2, info
        t = info["target"]
        assert t["r"] > 0 and t["b"] > 0 and t["l"] < vw and t["t"] < vh, f"anchor off screen: {info}"
        if info["anchor"] == "fallback":
            assert info["isMenu"], f"a step with nothing on screen points at its menu: {info}"
        assert _on_screen(info["callout"], vw, vh), f"callout off screen: {info}"
        assert info["ring"] and _on_screen(info["ring"], vw, vh), info
        assert not info["covered"], f"the callout covers what it points at: {info}"
        assert not _overlap(info["callout"], info["heads"]), f"the callout covers the track headers: {info}"
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
            pg.screenshot(path=str(Path(shots) / f"{which}-{state}-{size[0]}-{i + 1}-{info['key']}.png"))
        pg.locator('[data-testid="tour-next"]').click()
    pg.wait_for_selector(TOUR, state="detached", timeout=3000)
    print(f"\n{which} {state} {size}: {seen}")
    _done(pg)


# ---------------------------------------------------------------- Show me spends nothing


@pytest.mark.parametrize("which", ["loop", "ai"])
def test_show_me_opens_things_and_never_asks_a_model(live, bins, tmp_path, browser, which):
    _state(bins, tmp_path, "cut")
    pg = _page(browser, live)
    pg.wait_for_selector("#tl .blk", timeout=15000)
    priced = []

    def spy(route):
        if route.request.method == "POST":
            priced.append(route.request.url)
            return route.abort()
        return route.continue_()

    pg.route(PRICED, spy)
    pg.evaluate("(() => { window.__imports = 0; const o = rcImport.open.bind(rcImport);"
                " rcImport.open = () => { window.__imports++; return o(); }; })()")
    pg.wait_for_timeout(600)
    pg.evaluate(f"rcTour.start({'undefined' if which == 'loop' else repr(which)})")
    pg.wait_for_selector(TOUR, timeout=3000)
    n = pg.evaluate(STEP_JS)["n"]
    showed = []
    for _ in range(n):
        key = pg.evaluate(STEP_JS)["key"]
        show = pg.locator('[data-testid="tour-show"]')
        if show.is_visible():
            show.click()
            pg.wait_for_timeout(700)
            showed.append(key)
            if which == "loop" and key == "import":
                assert pg.evaluate("window.__imports") == 1
                # Import's dialog, or (no Import module) File › Import… opened and pointed at
                assert pg.evaluate("""(() => { const d = document.querySelector('[data-testid=import-dialog]');
                    return !!document.querySelector('dialog[open]') || !!(d && d.getClientRects().length)
                      || (document.querySelector('#menubar .menu[data-menu=file]').classList.contains('open')
                          && document.querySelector('[data-testid=tour]').dataset.anchor === 'show'); })()""")
            if key == "firstpass":
                assert pg.locator('[data-testid="firstpass-panel"]').is_visible()
                assert pg.evaluate("document.querySelector('[data-testid=tour]').dataset.side") != "none"
            if key == "why":
                assert pg.locator('#pProject section[data-tool="bin"]').is_visible()
            if key == "export":
                assert pg.locator("#exportDialog[open]").count() == 1
            if key in ("menu", "deep") or (key in ("ask", "prompt") and not pg.evaluate(
                    "!!document.querySelector('[data-testid=ai-prompt-input]')")):
                assert pg.evaluate("document.querySelector('#menubar .menu[data-menu=ai]').classList.contains('open')")
            # put away what it opened (a modal dialog would hold the callout's buttons)
            cancel = pg.locator('[data-testid="import-cancel"]')
            if cancel.count() and cancel.first.is_visible():
                cancel.first.click()
            pg.evaluate("document.querySelectorAll('dialog[open]').forEach(d => d.close())")
        pg.locator('[data-testid="tour-next"]').click()
    pg.wait_for_selector(TOUR, state="detached", timeout=3000)
    want = {"loop": ["import", "firstpass", "why", "ask", "export"],
            "ai": ["prompt", "review", "menu", "deep"]}[which]
    assert showed == want, showed
    pg.wait_for_timeout(500)
    assert priced == [], f"Show me reached a priced endpoint: {priced}"
    _done(pg)


# ---------------------------------------------------------------- Show me over a dialog

DIALOG_JS = """() => {
  const t = document.querySelector('[data-testid="tour"]');
  const d = document.querySelector('dialog[open]');
  const r = t.getBoundingClientRect(), q = d ? d.getBoundingClientRect() : null;
  const hits = [[0.5, 0.5], [0.15, 0.2], [0.85, 0.8]].map(([fx, fy]) => {
    const el = document.elementFromPoint(r.left + r.width * fx, r.top + r.height * fy);
    return !!(el && el.closest('[data-testid="tour"]'));
  });
  return {dialog: d ? (d.id || d.dataset.testid) : null, inDialog: !!(d && d.contains(t)),
          anchor: t.dataset.anchor, side: t.dataset.side, step: t.dataset.step, hits,
          callout: {l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height},
          box: q && {l: q.left, t: q.top, r: q.right, b: q.bottom}, vw: innerWidth, vh: innerHeight};
}"""


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_show_me_keeps_the_callout_above_the_dialog_it_opened(live, bins, tmp_path, browser, size):
    """Verdict 9, dimension 16: "Show me on step 1 hides the callout under the modal". The
    Import dialog is a modal <dialog> (the top layer, above every z-index): the callout
    goes into it, docked beside it, on top and answering; Esc closes the dialog and the
    callout comes back to the workspace, the walk-through still up."""
    _state(bins, tmp_path, "fresh")
    pg = _page(browser, live, size)
    pg.wait_for_timeout(600)
    pg.evaluate("rcTour.start()")
    pg.wait_for_selector(TOUR, timeout=3000)
    pg.locator('[data-testid="tour-show"]').click()
    pg.wait_for_selector('[data-testid="import-dialog"][open]', timeout=5000)
    pg.wait_for_function("document.querySelector('[data-testid=tour]').dataset.anchor === 'dialog'",
                         timeout=3000)
    pg.wait_for_timeout(300)
    d = pg.evaluate(DIALOG_JS)
    assert d["dialog"] == "importDialog" and d["inDialog"], d
    assert all(d["hits"]), f"the dialog covers the callout: {d}"
    assert _on_screen(d["callout"], d["vw"], d["vh"]), d
    # docked to its edge, beside it: none of the dialog's list, path bar or buttons under it
    assert not _overlap(d["callout"], d["box"]), f"the callout sits on the dialog: {d}"
    assert d["callout"]["w"] >= 200, d
    shots = os.environ.get("ROUGHCUT_TOUR_SHOTS")
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(Path(shots) / f"show-me-import-{size[0]}.png"))
    # its buttons answer while the dialog is up: Next moves the walk-through on
    pg.locator('[data-testid="tour-next"]').click()
    pg.wait_for_function("document.querySelector('[data-testid=tour]').dataset.step === '2'", timeout=3000)
    assert pg.locator('[data-testid="import-dialog"][open]').count() == 1
    pg.locator('[data-testid="tour-back"]').click()
    pg.wait_for_function("document.querySelector('[data-testid=tour]').dataset.step === '1'", timeout=3000)
    # Esc closes the dialog only; the callout is back on the workspace, beside its anchor
    pg.keyboard.press("Escape")
    pg.wait_for_selector('[data-testid="import-dialog"]', state="hidden", timeout=3000)
    pg.wait_for_function("(() => { const t = document.querySelector('[data-testid=tour]');"
                         " return t && t.parentElement === document.body && t.dataset.anchor !== 'dialog'; })()",
                         timeout=3000)
    info = pg.evaluate(STEP_JS)
    assert info["step"] == "1" and _on_screen(info["callout"], info["vw"], info["vh"]), info
    assert info["ring"] and not _overlap(info["callout"], info["heads"]), info
    assert pg.evaluate("rcTour.active") is True
    _done(pg)


# ---------------------------------------------------------------- the AI hovers


CARD_JS = """() => {
  const c = document.querySelector('[data-testid="ai-hover"]');
  if (!c || c.hidden) return null;
  const r = c.getBoundingClientRect();
  return {for: c.dataset.for, name: c.querySelector('.hc-name').textContent,
          what: c.querySelector('.hc-what').textContent,
          cost: c.querySelector('[data-testid="ai-hover-cost"]').textContent,
          change: c.querySelector('[data-testid="ai-hover-change"]').textContent,
          box: {l: r.left, t: r.top, r: r.right, b: r.bottom}, vw: innerWidth, vh: innerHeight};
}"""


def _card(pg, sel, *, wait=True, prep=None):
    pg.mouse.move(2, 890)                              # off every control first
    pg.wait_for_timeout(450)                           # past the warm-up window
    if prep:
        pg.evaluate(prep)
    loc = pg.locator(sel).first
    loc.scroll_into_view_if_needed()
    loc.hover(force=True)
    if wait:
        pg.wait_for_function("(() => { const c = document.querySelector('[data-testid=ai-hover]');"
                             " return c && !c.hidden; })()", timeout=3000)
    return pg.evaluate(CARD_JS)


def _says_section_s(card) -> None:
    cost = card["cost"]
    assert not re.search(r"\bspent\b", cost, re.I), cost
    if cost != "none — no model call":
        assert cost.startswith("free on your Claude plan"), cost
    if "$" in cost:
        assert "if using the API" in cost, cost
    assert card["what"].strip() and card["change"].strip(), card
    b = card["box"]
    assert b["l"] >= 0 and b["t"] >= 0 and b["r"] <= card["vw"] and b["b"] <= card["vh"], card


def test_every_ai_control_has_a_hover_card(live, bins, tmp_path, browser):
    _state(bins, tmp_path, "cut")
    pg = _page(browser, live)
    pg.wait_for_selector("#tl .blk", timeout=15000)
    pg.wait_for_timeout(800)
    cards = {}

    # the header's first pass, with Premiere's delay: nothing at 250 ms, the card by ~500
    pg.mouse.move(2, 890)
    pg.locator('[data-testid="firstpass-button"]').hover()
    pg.wait_for_timeout(250)
    assert pg.evaluate(CARD_JS) is None, "the card came up before the tooltip delay"
    pg.wait_for_function("(() => { const c = document.querySelector('[data-testid=ai-hover]');"
                         " return c && !c.hidden; })()", timeout=2000)
    cards["firstpass"] = pg.evaluate(CARD_JS)
    assert "free on your Claude plan — about $" in cards["firstpass"]["cost"], cards["firstpass"]
    # it goes when the pointer leaves
    pg.mouse.move(2, 890)
    pg.wait_for_function("document.querySelector('[data-testid=ai-hover]').hidden", timeout=2000)

    # the AI menu: every action in it
    pg.locator('#menubar .menu[data-menu="ai"] .mtop').click()
    for cmd in ("prompt", "firstpass", "ask", "shot", "colour", "fx", "deep", "find", "audit"):
        pg.locator(f'#menubar [data-cmd="ai:{cmd}"]').hover()
        pg.wait_for_function(f"(() => {{ const c = document.querySelector('[data-testid=ai-hover]');"
                             f" return c && !c.hidden && c.dataset.for === 'ai:{cmd}'; }})()", timeout=3000)
        cards[f"menu ai:{cmd}"] = pg.evaluate(CARD_JS)
    pg.locator('#menubar .menu[data-menu="sequence"] .mtop').hover()     # one menu open: hover moves it
    for cmd in ("ask", "fx"):
        pg.locator(f'#menubar [data-cmd="{cmd}"]').hover()
        pg.wait_for_function(f"(() => {{ const c = document.querySelector('[data-testid=ai-hover]');"
                             f" return c && !c.hidden && c.dataset.for === '{cmd}'; }})()", timeout=3000)
        cards[f"menu {cmd}"] = pg.evaluate(CARD_JS)
    pg.keyboard.press("Escape")

    # the shot's own: Effect Controls › Assistant (Ask about this shot, Look deeper)
    pg.locator("#tl .blk").first.click()
    pg.wait_for_selector("#inspector details.assist", timeout=5000)
    pg.evaluate("document.querySelectorAll('#inspector details.assist').forEach(d => { d.open = true; })")
    cards["shot ask"] = _card(pg, '#inspector button[data-act="ask"]')
    pg.wait_for_selector("#inspector .lookhost .dv-look", timeout=10000)
    cards["look deeper"] = _card(pg, "#inspector .lookhost .dv-look")
    # Designed Effects' Design
    pg.evaluate("dock.open('fx')")
    pg.wait_for_selector("#fxDesign", timeout=5000)
    cards["design"] = _card(pg, "#fxDesign")
    # the Assistant's Ask for a change
    pg.evaluate("dock.open('ask')")
    pg.wait_for_selector("#ask", state="visible", timeout=5000)
    cards["ask"] = _card(pg, "#ask")
    # Selects: the model's find and the claims audit (shown as they are once offered)
    pg.evaluate("dock.open('bin'); const f = document.querySelector('#findDeep'); f.hidden = false;"
                " document.querySelector('#auditRow').style.display = 'flex';")
    cards["find"] = _card(pg, "#findDeep")
    cards["audit"] = _card(pg, "#auditClaims",       # the status repaints its row: shown again
                           prep="document.querySelector('#auditRow').style.display = 'flex'")
    # a proposal's Accept
    pg.evaluate(f"rcShowProposal({json.dumps(PLAN)})")
    pg.wait_for_selector("#acceptProposal", state="visible", timeout=5000)
    cards["accept"] = _card(pg, "#acceptProposal")
    assert cards["accept"]["cost"] == "none — no model call"
    # the controls other wave-12 modules draw, by their test hooks (the AI prompt, a review row)
    pg.evaluate("""(() => {
      const host = document.querySelector('#aiPrompt');
      const i = document.createElement('input'); i.dataset.testid = 'ai-prompt-input';
      i.style.width = '160px'; host.appendChild(i);
      const b = document.createElement('button'); b.dataset.testid = 'ai-op-accept'; b.textContent = '✓';
      Object.assign(b.style, {position: 'fixed', left: '700px', top: '600px', zIndex: 50});
      document.body.appendChild(b); })()""")
    cards["prompt"] = _card(pg, '[data-testid="ai-prompt-input"]')
    cards["op accept"] = _card(pg, '[data-testid="ai-op-accept"]')

    for name, card in cards.items():
        assert card, name
        _says_section_s(card)
    # what it changes: the AI's proposals land only on accept
    for name in ("firstpass", "menu ai:ask", "shot ask", "ask", "design", "menu ai:colour"):
        assert "until you accept" in cards[name]["change"], (name, cards[name])
    assert "does not change" in cards["look deeper"]["change"]
    _done(pg)


def test_the_ai_context_menu_items_have_hover_cards(live, bins, tmp_path, browser):
    """Lane AIPROMPT's right-click "Ask AI about this…" and "Look deeper…" carry cards too
    (registered from aiprompt.js with `rcTour.card`, wave 12 lane AIGLUE)."""
    _state(bins, tmp_path, "cut")
    pg = _page(browser, live)
    pg.wait_for_selector("#tl .blk", timeout=15000)
    pg.wait_for_timeout(800)
    pg.locator("#tl .blk").first.click(button="right")
    pg.wait_for_selector('[data-testid="ai-context-menu"] [data-testid="ai-ctx-ask"]', state="visible")
    ask = _card(pg, '[data-testid="ai-ctx-ask"]')
    assert ask["name"].strip() == "Ask AI about this" and ask["for"] == "ai-ctx-ask", ask
    _says_section_s(ask)
    assert "until you accept" in ask["change"], ask
    assert ask["cost"].startswith("free on your Claude plan — about $"), ask   # the assist's own price
    deep = _card(pg, '[data-testid="ai-ctx-deep"]')
    assert deep["name"].strip() == "Look deeper" and deep["for"] == "ai-ctx-deep", deep
    _says_section_s(deep)
    assert "does not change" in deep["change"], deep
    _done(pg)


@pytest.mark.parametrize("size", SIZES)
def test_the_review_steps_point_at_the_ai_review_when_one_is_up(live, bins, tmp_path, browser, size):
    """With lane AIOPS's review of an AI answer up, the AI walk-through's "Review each
    change" points at its first row (Show me: the ◐ Before / After), and the loop's
    "Review the proposed cut" at the review itself (Show me: Accept all), in words that
    name what is there; each callout wholly on screen and clear of what it points at."""
    _state(bins, tmp_path, "cut")
    pg = _page(browser, live, size)
    pg.wait_for_selector("#tl .blk", timeout=15000)
    ids = pg.evaluate("segs.map(s => s.id)")
    pg.evaluate("""(ids) => rcActions.review({kind: 'ops', status: 'done', summary: 'punchier, tighter',
      ops: [{op: 'set', id: ids[1], prop: 'contrast', value: 20}, {op: 'trim', id: ids[0], in: 1.5}]})""", ids)
    pg.wait_for_selector('[data-testid="ai-review"] [data-testid="ai-op-row"]', state="visible", timeout=5000)
    pg.wait_for_selector('[data-testid="ai-before-after"]', state="visible", timeout=5000)

    def at(step: int, which: str) -> dict:
        pg.evaluate(f"rcTour.start({'undefined' if which == 'loop' else repr(which)})")
        pg.wait_for_selector(TOUR, timeout=3000)
        for _ in range(step - 1):
            pg.locator('[data-testid="tour-next"]').click()
        pg.wait_for_timeout(300)
        info = pg.evaluate(STEP_JS)
        info["testid"] = pg.evaluate("(() => { const a = rcTour.anchor(); return a && a.dataset.testid; })()")
        assert _on_screen(info["callout"], info["vw"], info["vh"]), info
        assert not info["covered"], info
        return info

    info = at(2, "ai")
    assert info["key"] == "review" and info["testid"] == "ai-op-row", info
    assert "Each row is one edit" in info["body"] and "◐" in info["body"], info
    pg.locator('[data-testid="tour-show"]').click()
    pg.wait_for_timeout(300)
    assert pg.evaluate("rcTour.anchor().dataset.testid") == "ai-before-after"
    assert pg.evaluate(STEP_JS)["anchor"] == "show"
    pg.evaluate("rcTour.stop()")

    info = at(4, "loop")
    assert info["key"] == "review" and info["testid"] == "ai-review", info
    assert "The AI’s edits wait here" in info["body"] and "Accept all" in info["body"], info
    show = pg.locator('[data-testid="tour-show"]')
    assert show.is_visible()
    show.click()
    pg.wait_for_timeout(300)
    assert pg.evaluate("rcTour.anchor().dataset.testid") == "ai-accept-all"
    pg.evaluate("rcTour.stop()")
    # the review untouched by the walk-through: still waiting, nothing applied
    assert pg.evaluate("rcActions.open") and pg.evaluate("segs[0].in") == 1.0
    pg.evaluate("rcActions.reject()")
    _done(pg)


# ---------------------------------------------------------------- what a callout never covers

# Verdict 12, punch 8: with Selects open (step 3's Show me) and a proposed cut waiting in the
# Assistant, step 5's callout covered its Accept ("ccept"); step 1's covered the Source's
# "preparing preview…". Every visible Accept / Discard and monitor status line, as boxes.
KEEP_JS = """() => [...document.querySelectorAll('#acceptProposal, #rejectProposal, .monWait span,'
    + ' [data-testid="ai-accept-all"]')]
  .filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden')
  .map(e => { const r = e.getBoundingClientRect();
    return {id: e.id || e.closest('[id]').id, l: r.left, t: r.top, r: r.right, b: r.bottom}; })"""


@pytest.mark.parametrize("which", ["loop", "ai"])
@pytest.mark.parametrize("size", [(1440, 900), (1280, 800), (1920, 1080)], ids=lambda s: f"{s[0]}x{s[1]}")
def test_no_callout_covers_an_accept_or_a_status_line(live, bins, tmp_path, browser, size, which):
    """Every step of both walk-throughs, as they stand and with each Show me pressed (but the
    two that open a modal dialog), at 1280, 1440 and 1920: the callout never lies over the
    proposal's Accept or Discard or a monitor's "preparing preview…" (`#monWait`, `#srcWait`),
    while a proposed cut waits in the Assistant and Selects is open beside the Timeline."""
    _state(bins, tmp_path, "cut")
    pg = _page(browser, live, size)
    pg.wait_for_selector("#tl .blk", timeout=15000)
    pg.evaluate(f"rcShowProposal({json.dumps(PLAN)})")
    pg.wait_for_selector("#acceptProposal", state="visible", timeout=5000)
    setup = ("dock.open('bin', {remember: false, focus: false}); dock.open('ask', {remember: false, focus: false});"
             " dock.wait('#monWait', true, 'test'); dock.wait('#srcWait', true, 'test')")
    pg.evaluate(setup)
    pg.wait_for_timeout(500)
    ids = {k["id"] for k in pg.evaluate(KEEP_JS)}
    assert {"acceptProposal", "monWait", "srcWait"} <= ids, ids
    start = f"rcTour.start({'undefined' if which == 'loop' else repr(which)})"
    for show in (False, True):
        pg.evaluate(setup)
        pg.evaluate(start)
        pg.wait_for_selector(TOUR, timeout=3000)
        n = pg.evaluate(STEP_JS)["n"]
        for i in range(n):
            key = pg.evaluate(STEP_JS)["key"]
            if show and key not in ("import", "export") and pg.locator('[data-testid="tour-show"]').is_visible():
                pg.locator('[data-testid="tour-show"]').click()
            pg.wait_for_timeout(350)                          # the callout follows what opened
            info = pg.evaluate(STEP_JS)
            assert _on_screen(info["callout"], info["vw"], info["vh"]), info
            assert not info["covered"], info
            for k in pg.evaluate(KEEP_JS):
                assert not _overlap(info["callout"], k), \
                    f"{which} step {info['step']} ({key}, show me: {show}) covers {k['id']}: {info['callout']} {k}"
            pg.locator('[data-testid="tour-next"]').click()
            if i < n - 1:
                pg.wait_for_function(f"document.querySelector('{TOUR}').dataset.step === '{i + 2}'",
                                     timeout=3000)
        pg.wait_for_selector(TOUR, state="detached", timeout=3000)
    _done(pg)


def test_a_hover_card_goes_when_its_button_is_clicked_and_stays_gone(live, bins, tmp_path, browser):
    """Verdict 12: "the header button's 'Run first pass' hover card stays up over the open
    panel and hides the line under Stop". A click takes the card down; a move inside the
    button, or the button repainting under a still pointer, does not bring it back; leaving
    and coming back does. A card whose control is hidden goes with it."""
    _state(bins, tmp_path, "cut")
    pg = _page(browser, live)
    pg.wait_for_selector("#tl .blk", timeout=15000)
    pg.wait_for_timeout(600)
    shown = "(() => { const c = document.querySelector('[data-testid=ai-hover]'); return !!c && !c.hidden; })()"
    btn = pg.locator('[data-testid="firstpass-button"]')
    pg.mouse.move(2, 890)
    pg.wait_for_timeout(450)
    btn.hover()
    pg.wait_for_function(shown, timeout=3000)
    b = btn.bounding_box()
    cx, cy = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
    pg.mouse.move(cx, cy)
    pg.mouse.down()
    pg.mouse.up()
    pg.wait_for_selector('[data-testid="firstpass-panel"]', state="visible", timeout=3000)
    assert not pg.evaluate(shown)
    # a move inside the button, then its label redrawn under the still pointer
    for dx in (-12, 6, 14):
        pg.mouse.move(cx + dx, cy + 1)
        pg.wait_for_timeout(150)
    pg.evaluate("(() => { const l = document.querySelector('#firstpass .fp-label');"
                " l.replaceWith(l.cloneNode(true)); })()")
    pg.mouse.move(cx + 2, cy)
    pg.wait_for_timeout(1000)
    assert not pg.evaluate(shown), "the card came back over the panel its click opened"
    # out and back in: the card again
    pg.mouse.move(2, 890)
    pg.wait_for_timeout(450)
    btn.hover()
    pg.wait_for_function(shown, timeout=3000)
    # a card whose control goes away goes with it
    pg.mouse.move(2, 890)
    pg.keyboard.press("Escape")
    pg.evaluate(f"rcShowProposal({json.dumps(PLAN)}); dock.open('ask')")
    pg.wait_for_selector("#acceptProposal", state="visible", timeout=5000)
    pg.wait_for_timeout(450)
    pg.locator("#acceptProposal").hover()
    pg.wait_for_function(shown, timeout=3000)
    pg.evaluate("document.querySelector('#acceptProposal').style.display = 'none'")
    pg.wait_for_function(f"!{shown}", timeout=2000)
    _done(pg)


# Verdict 13, punch 8: with Selects open at 1440 and an ask answered ("2 changes ready ·
# Review" in its row under the Timeline's header), step 5's callout sat under the prompt it
# points at, over that line, and its Review could not be clicked. The AI's line, as boxes:
# its row under the header (#aiUnder), the line itself and its Review / Show.
AI_LINE_JS = """() => [...document.querySelectorAll('#aiUnder, [data-testid="ai-status"],'
    + ' [data-testid="ai-result-open"]')]
  .filter(e => e.getClientRects().length && getComputedStyle(e).display !== 'none' && !e.closest('[hidden]'))
  .map(e => { const r = e.getBoundingClientRect();
    return {id: e.id || e.dataset.testid, l: r.left, t: r.top, r: r.right, b: r.bottom}; })"""

# what a click on the line's Show / Review reaches: the link ("open"), the answer it opened
# (aiprompt.js's popover under the field: "pop"), else what lies over it
OPEN_HIT_JS = """() => { const b = document.querySelector('[data-testid="ai-result-open"]');
  if (!b) return null;
  const r = b.getBoundingClientRect(), hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  if (hit && (hit === b || b.contains(hit))) return 'open';
  if (hit && hit.closest('.aip-pop')) return 'pop';
  return hit ? (hit.id || hit.className || hit.tagName) : 'nothing'; }"""


@pytest.mark.parametrize("size", [(1440, 900), (1280, 800), (1920, 1080)], ids=lambda s: f"{s[0]}x{s[1]}")
def test_the_ask_steps_callout_never_covers_the_ais_line(live, bins, tmp_path, browser, size):
    """Step 5 ("Ask the AI for an edit") up, Selects open beside the Timeline, an ask typed in
    the prompt it points at and answered (scripted: no model call), first in words ("✓
    Answered · Show"), then as two edits ("2 changes ready · Review", the review opening in
    the Assistant): the callout lies over neither the line's row under the header
    (`#aiUnder`), nor the line, nor its Show / Review, which a click still reaches, wherever
    the line is (under the header at 1440, beside the field at 1280 and 1920)."""
    _state(bins, tmp_path, "cut")
    pg = _page(browser, live, size)
    pg.wait_for_selector("#tl .blk", timeout=15000)
    answers = []
    pg.route("**/api/assist", lambda r: r.fulfill(status=200, content_type="application/json",
                                                   body=json.dumps({"job": f"tour13{len(answers)}"}))
             if r.request.method == "POST" else r.continue_())
    pg.route("**/api/assist/tour13*", lambda r: r.fulfill(status=200, content_type="application/json",
                                                         body=json.dumps(answers[-1])))
    ids = pg.evaluate("tl.state.segs.map(s => s.id)")
    pg.evaluate("dock.open('bin', {remember: false, focus: false})")
    pg.wait_for_timeout(300)
    pg.evaluate("rcTour.start()")
    pg.wait_for_selector(TOUR, timeout=3000)
    while pg.evaluate(STEP_JS)["key"] != "ask":
        step = pg.evaluate(STEP_JS)["step"]
        pg.locator('[data-testid="tour-next"]').click()
        pg.wait_for_function(f"document.querySelector('{TOUR}').dataset.step !== '{step}'", timeout=3000)
    assert pg.evaluate(STEP_JS)["step"] == "5"
    ops = [{"op": "set", "id": ids[1], "prop": "temperature", "value": 12},
           {"op": "set", "id": ids[1], "prop": "exposure", "value": 0.1}]
    for ask, answer, words in (
            ("what is this film about", {"status": "done", "kind": "answer", "text": "A greeting.",
                                         "summary": "a greeting"}, "Show"),
            ("warmer and a touch brighter on shot 2", {"status": "done", "kind": "ops", "ops": ops,
                                                       "summary": "warmer, brighter"}, "Review")):
        answers.append(answer)
        pg.keyboard.press("Control+j")
        pg.keyboard.press("Control+a")
        pg.keyboard.type(ask)
        pg.keyboard.press("Enter")
        pg.wait_for_selector('[data-testid="ai-result-open"]', state="visible", timeout=5000)
        for state in ("answered", "left"):
            pg.wait_for_timeout(400)                          # the callout follows the line
            info = pg.evaluate(STEP_JS)
            assert info["key"] == "ask" and _on_screen(info["callout"], info["vw"], info["vh"]), info
            assert not info["covered"], info
            line = pg.evaluate(AI_LINE_JS)
            assert {"ai-status", "ai-result-open"} <= {k["id"] for k in line}, line
            if size[0] == 1440:
                assert "aiUnder" in {k["id"] for k in line}, line   # the line has its own row here
            for k in line:
                assert not _overlap(info["callout"], k), (size, words, state, k, info["callout"])
            assert pg.locator('[data-testid="ai-result-open"]').inner_text().strip() == words
            # a click reaches the link; just answered in words, the answer Show shows is
            # already open under the field (Esc closes it)
            hit = pg.evaluate(OPEN_HIT_JS)
            assert hit == "open" or (hit == "pop" and words == "Show" and state == "answered"), \
                (size, words, state, hit, info["callout"])
            pg.keyboard.press("Escape")                       # out of the prompt: the line stays
    # Review reopens the review: the click reaches it
    pg.locator('[data-testid="ai-result-open"]').click()
    pg.wait_for_selector('[data-testid="ai-review"]', state="visible", timeout=5000)
    assert pg.evaluate("rcTour.active")
    pg.evaluate("rcActions.reject()")
    pg.unroute_all(behavior="ignoreErrors")
    _done(pg)


# ---------------------------------------------------------------- the empty states


def test_no_footage_names_import_everywhere(live, bins, tmp_path, browser):
    _state(bins, tmp_path, "empty")
    pg = _page(browser, live)
    pg.evaluate("(() => { window.__imports = 0; rcImport.open = () => { window.__imports++; }; })()")
    pg.wait_for_selector('#tlEmpty .empty[data-ai="import"] [data-testid="empty-import"]', timeout=5000)
    tl = pg.locator("#tlEmpty .empty").inner_text()
    assert "Import footage" in tl and "Ctrl+I" in tl, tl
    # nothing for a first pass to read: its box steps aside here
    assert pg.locator("#tlEmpty .fp-empty-btn").is_hidden()
    assert pg.locator('[data-testid="program-import"]').is_visible()
    assert "Import footage" in pg.locator("#progEmpty").inner_text()
    assert pg.locator('[data-testid="source-import"]').is_visible()
    assert pg.locator("#srcEmpty .se-pick").is_hidden()
    for sel in ('[data-testid="empty-import"]', '[data-testid="program-import"]',
                '[data-testid="source-import"]'):
        pg.locator(sel).click()
    assert pg.evaluate("window.__imports") == 3
    # footage arrives (Import's event): the next action becomes the cut's own
    shutil.copy(bins["full"] / "CLIP_A.MP4", tmp_path / "nofootage" / "CLIP_A.MP4")
    pg.evaluate("window.dispatchEvent(new CustomEvent('rc:clips-changed', {detail: {added: ['CLIP_A.MP4']}}))")
    pg.wait_for_selector('#tlEmpty .empty[data-ai="pass"]', timeout=5000)
    assert pg.locator('[data-testid="empty-import"]').count() == 0
    assert "Drag a clip here or press ," in pg.locator("#tlEmpty .empty").inner_text()
    pg.wait_for_function("document.querySelector('#srcEmpty .se-none').hidden")
    _done(pg)


def test_footage_and_no_pass_names_the_drag_and_the_first_pass(live, bins, tmp_path, browser):
    _state(bins, tmp_path, "fresh")
    pg = _page(browser, live)
    pg.wait_for_selector('#tlEmpty [data-testid="firstpass-empty-timeline"]', timeout=5000)
    tl = pg.locator("#tlEmpty .empty").inner_text()
    assert "Drag a clip here or press ," in tl and "Run first pass" in tl, tl
    assert pg.locator('#tlEmpty [data-testid="empty-import"]').count() == 0
    assert "press ," in pg.locator("#progEmpty").inner_text()
    assert pg.locator('[data-testid="program-import"]').count() == 0
    assert pg.locator('[data-testid="source-import"]').is_hidden()
    _done(pg)


def test_a_proposal_waiting_off_screen_is_one_click_away(live, bins, tmp_path, browser):
    _state(bins, tmp_path, "fresh")
    pg = _page(browser, live)
    pg.wait_for_selector("#tlEmpty .empty", timeout=5000)
    pg.route("**/api/ask/tourjob", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps({"plan": PLAN})))
    pg.evaluate("(() => { rcFirstPass.state = () => ({ state: 'done', proposal: { pending: true, job: 'tourjob' } }); })()")
    pg.wait_for_selector('#tlEmpty [data-testid="empty-review"]', timeout=3000)
    assert "A proposed cut is waiting" in pg.locator("#tlEmpty .empty").inner_text()
    assert pg.locator("#tlEmpty .fp-empty-btn").is_hidden()
    pg.locator('[data-testid="empty-review"]').click()
    pg.wait_for_function("pendingPlan && pendingPlan.segments.length === 2", timeout=5000)
    pg.wait_for_selector('#tl .tl-xlane[data-lane="ghost"]', state="visible", timeout=5000)
    assert pg.locator("#tlEmpty").is_hidden()
    _done(pg)
