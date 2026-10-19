"""The first-pass panel speaks the editor's words, in a real browser (verdict 9, punches 2 and 3).

  * A generated black clip — what an overwrite past the end of the sequence leaves in the
    gap (Premiere's own behaviour, `tl.placeAt`) — reads "Black Video" in the panel's
    "added by hand" list, never its file name `gen_black_<hash>` (punch 3).
  * After imports, the "N new clips" offer and the panel carry the price of the looks
    they wait on ("free on your plan", the API estimate in the hover), and once the
    re-run lands the panel says what it did with each new clip (punch 2).

Every model call is scripted (test_import_held.Model); the index runs with
test_index's stubbed tools over a copy of the fixture bin under tmp_path.
"""

from __future__ import annotations

import shutil
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import inference  # noqa: E402
from test_firstpass import _end_jobs  # noqa: E402
from test_import_held import Model, _make  # noqa: E402
from test_index import _stub_tools  # noqa: E402

BTN = '[data-testid="firstpass-button"]'
PANEL = '[data-testid="firstpass-panel"]'


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def card(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("names-card")
    _make(root / "KX_05.MP4", "color=c=red")
    _make(root / "KX_10.MP4", "color=c=blue")
    return root


@pytest.fixture
def live(tmp_path, project, monkeypatch):
    import uvicorn
    import server

    model = Model(say=True)
    inference.set_backend(model)
    inference.reset_spend()
    monkeypatch.setenv("ROUGHCUT_BACKEND", "claude_cli")
    footage, sidecars = tmp_path / "bin" / "day1", tmp_path / "sidecars"
    footage.mkdir(parents=True)
    sidecars.mkdir()
    for stem in project["stems"]:
        shutil.copy(project["footage"] / f"{stem}.MP4", footage / f"{stem}.MP4")
        shutil.copy(project["sidecars"] / f"{stem}.audio.json", sidecars / f"{stem}.audio.json")
    server.configure(None, footage, sidecars, tmp_path / "work", proxies=False, visual=None)
    _stub_tools(server, monkeypatch, tmp_path)
    monkeypatch.setattr(server, "FIRSTPASS_POLL_S", 0.05)
    monkeypatch.setattr(server, "build_proxy",
                        lambda src, dest, orient: shutil.copy(src, dest) and "copied")
    server.FIRSTPASS.clear()
    server.INDEXES.clear()
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        _end_jobs(server)
        srv.should_exit = True
        th.join(timeout=10)
        server.FIRSTPASS.clear()
        inference.set_backend(None)


@pytest.fixture
def page(live):
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        try:
            pg = b.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(live + "/")
            pg.wait_for_function("window.tl && tl.el && tl.el.view && window.rcFirstPass"
                                 " && rcFirstPass.state()", timeout=20000)
            yield pg
            assert errors == [], errors
        finally:
            b.close()


def _segments(live) -> list[dict]:
    return httpx.get(live + "/api/project", timeout=20).json()["segments"]


def _until(pred, what: str, timeout: float = 30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        got = pred()
        if got:
            return got
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


def _wait_state(live, until, timeout=90.0) -> dict:
    return _until(lambda: (lambda s: s if s["state"] in until else None)(
        httpx.get(live + "/api/firstpass", timeout=20).json()), f"the pass to reach {until}",
        timeout)


def _open_panel(pg) -> str:
    pg.evaluate("rcFirstPass.refresh()")
    pg.locator(BTN).click()
    pg.wait_for_selector(PANEL, state="visible", timeout=5000)
    pg.wait_for_function("document.querySelector('[data-testid=\"firstpass-panel\"] "
                         ".fp-steps li') !== null", timeout=10000)
    return pg.locator(PANEL).inner_text()


def test_a_generated_black_clip_reads_black_video_in_the_panel(page, live):
    """Overwrite past the end: the gap is a generated black clip, and the re-run's list
    of what it keeps names it the way Premiere names it."""
    pg = page
    pg.evaluate("tl.insert({clip: 'CLIP_A.MP4', in: 0.5, out: 2.0}, null)")
    pg.evaluate("tl.placeAt({clip: 'CLIP_B.MP4', in: 0.5, out: 1.5}, tl.total() + 2, 'overwrite')")
    segs = _until(lambda: (lambda s: s if len(s) == 3 else None)(_segments(live)),
                  "the cut with its gap to be saved")
    gap = segs[1]
    assert gap["clip"].startswith("gen_black_") and gap.get("gap") is True, segs
    s = httpx.get(live + "/api/firstpass", timeout=20).json()
    keep = {r["clip"]: r for r in s["would"]["keep"]}
    assert keep[gap["clip"]]["name"] == "Black Video"
    assert all(r.get("name") for r in s["manual_edits"])
    text = _open_panel(pg)
    assert "shot 2 · Black Video 0:00.0–0:02.0 · added by hand" in text, text
    assert "gen_" not in text, text
    html = pg.locator(PANEL).inner_html()
    assert "gen_" not in html, "nor in a hover"
    # nor in Effect Controls' head for the selected gap, nor on its A1 block's hover
    gid = str(gap["id"])
    pg.evaluate(f"tl.select(['{gid}'])")
    pg.wait_for_function("(document.querySelector('#ecWhat') || {}).textContent", timeout=5000)
    ec = pg.evaluate("[document.querySelector('#ecWhat').textContent, document.querySelector('#ecWhat').title]")
    assert ec[0].startswith("Black Video") and not any("gen_" in t for t in ec), ec
    a1 = pg.evaluate(f"[...document.querySelectorAll('#tl [data-id=\"{gid}\"]')].map(b => b.title).join(' | ')")
    assert "gen_" not in a1 and "A1 · Black Video" in a1, a1


def test_new_clips_carry_the_looks_price_and_the_rerun_says_what_it_did(page, live, card):
    pg = page
    pg.locator(BTN).click()
    pg.wait_for_selector('[data-testid="firstpass-run"]:not([disabled])', timeout=10000)
    pg.locator('[data-testid="firstpass-run"]').click()
    _wait_state(live, ("done", "stale-after-edits"))
    pg.wait_for_selector("#proposal", state="visible", timeout=30000)
    got = httpx.post(live + "/api/import", json={"paths": [str(card)]}, timeout=20).json()
    assert got["added"] == ["KX_05.MP4", "KX_10.MP4"], got

    # the offer: its line, then the price of the looks the clips wait on
    pg.evaluate("rcFirstPass.refresh()")
    offer = pg.locator('[data-testid="firstpass-new-clips"]')
    offer.wait_for(state="visible", timeout=10000)
    assert offer.inner_text().startswith("2 new clips: add them to the cut?")
    tag = offer.locator(".fp-offer-cost")
    assert tag.inner_text() == "Looking at them: free on your plan"
    hover = tag.get_attribute("title")
    assert "if it went through the API — no charge on your plan" in hover and "~$" in hover

    # the panel: which clips a re-run hears and looks at, and what looking costs
    pg.locator('[data-testid="firstpass-new-clips-go"]').click()
    pg.wait_for_selector(PANEL, state="visible", timeout=5000)
    cost = pg.locator('[data-testid="firstpass-new-cost"]')
    assert cost.inner_text() == "Looking at them: free on your plan."
    assert cost.get_attribute("title") == hover
    assert "A re-run hears and looks at KX_05, KX_10" in pg.locator(PANEL).inner_text()
    pg.locator('[data-testid="firstpass-run"]').click()
    s = _wait_state(live, ("done", "stale-after-edits"))
    assert [r["line"] for r in s["proposal"]["new_clips"]] == [
        "Left out new clip KX_05: a near-duplicate of CLIP_B.",
        "Used new clip KX_10 in shot 2: the blue run, as shot 2."]

    # after it lands, the panel says what it did with each of them
    pg.wait_for_timeout(500)
    text = _open_panel(pg)
    did = pg.locator('[data-testid="firstpass-new-result"] li')
    assert did.all_inner_texts() == [
        "Left out new clip KX_05: a near-duplicate of CLIP_B.",
        "Used new clip KX_10 in shot 2: the blue run, as shot 2."], text
    assert not pg.locator('[data-testid="firstpass-new-cost"]').count(), "nothing new now"


def test_a_generated_clip_reads_as_premiere_names_it():
    """The rule the panel and the server share (`firstpass.clip_name`)."""
    from roughcut import firstpass

    assert firstpass.clip_name("gen_black_78a418d8.mp4") == "Black Video"
    assert firstpass.clip_name("gen_colour_1a2b3c4d.mp4") == "Color Matte"
    assert firstpass.clip_name("gen_still_1a2b3c4d.mp4") == "Still"
    assert firstpass.clip_name("gen_still_1a2b3c4d.mp4",
                               {"kind": "still", "from_clip": "KX_05.MP4"}) == "KX_05 Still"
    assert firstpass.clip_name("gen_.mp4") == "Generated clip"
    assert firstpass.clip_name("CLIP_A.MP4") == "CLIP_A"
    row = firstpass._row({"id": "g1", "clip": "gen_black_78a418d8.mp4", "in": 0, "out": 2},
                         1, ["add"])
    assert row["name"] == "Black Video" and row["words"] == "added by hand"
    assert firstpass.rejected_lines([{"clip": "gen_black_78a418d8.mp4", "in": 0, "out": 2,
                                      "action": "dropped"}])[0].startswith(
        "Left out: Black Video 0:00.0–0:02.0")


# ---------------------------------------------------------------- the page scan
#
# Verdict 10, punch 9: the Program's readout still said "18/19 · gen_black_78a418d8" over a
# Black Video gap. Every board state a generated clip shows in is walked, and nothing on
# the page — no text the editor can see, no hover — names a generated clip by its file.

GEN_ID = r"gen_[a-z]+_[0-9a-f]{6}"

SCAN_JS = r"""(re) => {
  const pat = new RegExp(re, 'i');
  const vis = (el) => !!el && el.getClientRects().length > 0
    && getComputedStyle(el).visibility !== 'hidden';
  const where = (el) => {
    const p = [];
    for (let n = el; n && n !== document.body && p.length < 4; n = n.parentElement) {
      p.unshift(n.id ? `#${n.id}` : (n.dataset && n.dataset.testid) ? `[${n.dataset.testid}]`
        : `${n.tagName.toLowerCase()}${n.classList.length ? '.' + [...n.classList].join('.') : ''}`);
    }
    return p.join(' > ');
  };
  const text = [], hover = [];
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walk.nextNode()) {
    const n = walk.currentNode, el = n.parentElement;
    if (el && pat.test(n.textContent) && vis(el) && !el.closest('script, style, template')) {
      text.push(`${where(el)}: ${n.textContent.trim().slice(0, 120)}`);
    }
  }
  for (const el of document.querySelectorAll('input, textarea, select')) {
    if (vis(el) && pat.test(el.value || '')) text.push(`${where(el)} value: ${el.value}`);
  }
  for (const el of document.querySelectorAll('[title], [aria-label], img[alt]')) {
    if (!vis(el)) continue;
    for (const a of ['title', 'aria-label', 'alt']) {
      const v = el.getAttribute(a);
      if (v && pat.test(v)) hover.push(`${where(el)} ${a}: ${v.slice(0, 120)}`);
    }
  }
  return { text, hover };
}"""


def _scan(pg, state: str, found: dict) -> None:
    got = pg.evaluate(SCAN_JS, GEN_ID)
    if got["text"] or got["hover"]:
        found[state] = got


def _clean(found: dict) -> None:
    assert not found, "a generated clip's file name on the page, by board state: " + "; ".join(
        f"{state}: on screen {got['text']}, in a hover {got['hover']}" for state, got in found.items())


def test_no_generated_clips_file_name_anywhere_visible(page, live):
    pg = page
    found: dict = {}
    pg.evaluate("tl.insert({clip: 'CLIP_A.MP4', in: 0.5, out: 2.0}, null)")
    pg.evaluate("tl.placeAt({clip: 'CLIP_B.MP4', in: 0.5, out: 1.5}, tl.total() + 2, 'overwrite')")
    segs = _until(lambda: (lambda s: s if len(s) == 3 else None)(_segments(live)),
                  "the cut with its gap to be saved")
    gap = segs[1]
    assert gap["clip"].startswith("gen_black_"), segs
    gid = str(gap["id"])
    pg.wait_for_function(f"!!document.querySelector('#tl .blk[data-id=\"{gid}\"]')", timeout=10000)
    pg.wait_for_timeout(400)
    _scan(pg, "the cut with a Black Video gap", found)

    # the gap clicked: selected, the Program parked on it — its readout, Effect Controls,
    # the AI prompt's scope chip
    pg.locator(f'#tl .blk[data-id="{gid}"]').click()
    pg.wait_for_function("(document.querySelector('#playingWhat') || {}).textContent", timeout=10000)
    assert pg.locator("#playingWhat").inner_text() == "2/3 · Black Video"
    pg.wait_for_function("(document.querySelector('#ecWhat') || {}).textContent", timeout=5000)
    pg.wait_for_timeout(300)
    _scan(pg, "the gap selected, the Program parked on it", found)

    # the same generated clip laid on V2 and selected: its Effect Controls head
    oid = pg.evaluate("(c) => { const o = tl.addOverlay({clip: c, in: 0, out: 1}, 0.2);"
                      " return o && (o.id || o); }", gap["clip"])
    if oid:
        pg.evaluate("(id) => tl.selectOverlay(id)", oid)
        pg.wait_for_timeout(400)
        _scan(pg, "the generated clip on V2, selected", found)
        pg.evaluate("(id) => { tl.selectOverlay(null); tl.removeOverlay(id); }", oid)
        pg.wait_for_timeout(300)

    # played from the gap, then stopped there
    pg.evaluate("tl.select([])")
    pg.locator(f'#tl .blk[data-id="{gid}"]').dblclick()
    pg.wait_for_timeout(700)
    _scan(pg, "playing through the gap", found)
    pg.keyboard.press("k")
    pg.wait_for_timeout(300)

    # the first pass's panel (what a re-run keeps)
    _open_panel(pg)
    _scan(pg, "the first pass's panel", found)
    pg.keyboard.press("Escape")

    # the Project panel, both views, and Selects
    for view in ("list", "icon"):
        b = pg.locator(f'#projView button[data-view="{view}"]')
        if b.count() and b.is_visible():
            b.click()
            pg.wait_for_timeout(300)
            _scan(pg, f"the Project panel's {view} view", found)
    pg.evaluate("dock.open('bin', {remember: false})")
    pg.wait_for_timeout(300)
    _scan(pg, "Selects", found)

    # a proposal that keeps the gap (a re-run keeps the shots you added by hand)
    plan = {"segments": [{"clip": s["clip"], "in": s["in"], "out": s["out"],
                          "why": "kept" if s.get("gap") else "the shot"} for s in segs],
            "notes": "Kept your edits."}
    pg.evaluate("(p) => rcShowProposal(p)", plan)
    pg.wait_for_selector("#proposal", state="visible", timeout=10000)
    pg.wait_for_timeout(400)
    _scan(pg, "a proposal that keeps the gap", found)
    _clean(found)
