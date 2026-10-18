"""Easy to learn, the AI features above all (REDESIGN-GOAL dimension 16, Karl's K3 e).

Karl, 2026-10-16: *"this whole thing needs to be easy to learn how to use, especially how
to use those AI features."* In real Chromium, on the suite's synthetic bin:

  * the first-run walk-through (app/static/tour.js) opens on a browser's first visit at
    step 1 of 6, ON the workspace (no full-screen modal: the panels still answer), and not
    on the second load; Skip and Esc end it for good; Help › Learn Roughcut… and AI ›
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
          target: ab, tag: a ? (a.id || a.className || a.tagName) : null,
          isMenu: !!(a && a.classList && a.classList.contains('mtop')),
          covered, vw: innerWidth, vh: innerHeight,
          title: t.querySelector('#tourTitle').textContent,
          body: t.querySelector('.rt-body').textContent,
          n: t.querySelectorAll('[data-testid="tour-step"]').length};
}"""


def _on_screen(b, vw, vh) -> bool:
    return b and b["l"] >= 0 and b["t"] >= 0 and b["r"] <= vw + 0.5 and b["b"] <= vh + 0.5


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


@pytest.mark.parametrize("how", ["skip", "esc"])
def test_skip_and_esc_end_it_for_good(live, bins, tmp_path, browser, first_run_context, how):
    _state(bins, tmp_path, "fresh")
    pg = _page(browser, live, first=first_run_context)
    pg.wait_for_selector(TOUR, timeout=15000)
    pg.locator('[data-testid="tour-next"]').click()
    pg.wait_for_function("document.querySelector('[data-testid=tour]').dataset.step === '2'")
    if how == "skip":
        pg.locator('[data-testid="tour-skip"]').click()
    else:
        pg.keyboard.press("Escape")
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
                # no Import module here, or its dialog: either way Import is what is shown
                assert pg.evaluate("""(() => !!document.querySelector('dialog[open]')
                    || (document.querySelector('#menubar .menu[data-menu=file]').classList.contains('open')
                        && document.querySelector('[data-testid=tour]').dataset.anchor === 'show'))()""")
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
            pg.evaluate("document.querySelectorAll('dialog[open]').forEach(d => d.close())")
        pg.locator('[data-testid="tour-next"]').click()
    pg.wait_for_selector(TOUR, state="detached", timeout=3000)
    want = {"loop": ["import", "firstpass", "why", "ask", "export"],
            "ai": ["prompt", "review", "menu", "deep"]}[which]
    assert showed == want, showed
    pg.wait_for_timeout(500)
    assert priced == [], f"Show me reached a priced endpoint: {priced}"
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
