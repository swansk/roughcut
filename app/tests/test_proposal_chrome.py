"""The proposal state's small chrome (verdict 7, punch 8; wave 10 lane CRAFT).

Judge 7, at 1440×900 with a proposal waiting: *"the Source monitor shrinks to 343 px and
its transport crowds (timecode touches `[+`, Insert touches the duration)"*, the ghost
shots' names read "C…" at fit zoom, and *"the 'building proxies in the background' toast
parks over the Timeline repeatedly for an hour"*. Now, in that state:

  * no two of the Source transport's controls are closer than 4 px (the duration goes
    first when the panel is narrow), and none is cut or spills out of the panel;
  * no ghost's label is truncated: it is the clip's name, or its number ("01") when the
    name does not fit, or nothing (the tooltip has it) — never one letter and an ellipsis;
  * the "building proxies" toast shows once per pass: a reload while the same pass runs
    stays quiet; the next pass (after the previews were seen ready) says it again.

The server, seed and waiting-proposal helpers are test_proposal_review's.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")

from test_proposal_review import (  # noqa: E402,F401  (fixtures: live_server, browser)
    GHOST, _open, _seed, _shot, _waiting, browser, live_server)

TOAST = "building proxies in the background"

# A first cut with short shots among long ones: at fit zoom the 0.3 s shots are a few
# pixels wide per letter — the case that read "C…".
LENGTHS = [2.0, 0.3, 1.5, 0.25, 0.6, 2.0, 0.3, 1.0, 0.4, 2.5, 0.25, 0.8, 1.6, 0.3, 2.0]
CLIPS = ["CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"]
PLAN = {
    "segments": [{"clip": CLIPS[i % 3], "in": 0.5, "out": round(0.5 + d, 3), "why": f"shot {i + 1}"}
                 for i, d in enumerate(LENGTHS)],
    "notes": "A first cut with short shots.",
}


@pytest.fixture(autouse=True)
def _no_proposal_left_behind(live_server):
    """The waiting proposal is an Ask record in the session project's work dir: left
    there, every later module's board would open on it (as test_proposal_review's
    tests, each test takes its record away)."""
    yield
    import server
    for rec in server.STATE["asks"].glob("ch-*.json"):
        rec.unlink(missing_ok=True)


def _pending(browser, live_server, project, job="ch-first"):
    _seed(project, [])
    _waiting(job, PLAN)
    pg = _open(browser, live_server)
    pg.wait_for_selector("#proposal", state="visible", timeout=10000)
    pg.wait_for_function(f"document.querySelectorAll('{GHOST} .ghost').length === {len(LENGTHS)}",
                         timeout=8000)
    return pg


def _source_clip(pg, clip="CLIP_B.MP4"):
    """A clip in the Source monitor, its In and Out marked: every readout filled."""
    pg.wait_for_function("window.rcSource !== undefined && document.querySelector('#projList [data-clip]')",
                         timeout=10000)
    pg.evaluate("(c) => document.querySelector(`#projList [data-clip=\"${c}\"]`).click()", clip)
    pg.wait_for_function("(c) => window.rcSource && rcSource.clip === c", arg=clip, timeout=5000)
    pg.locator("#source .transport [data-src=in]").click()
    pg.wait_for_function("document.querySelector('#srcDur').textContent.length > 0", timeout=5000)


GAPS_JS = """() => {
  const tp = document.querySelector('#source .transport');
  const box = tp.getBoundingClientRect();
  const els = [...tp.querySelectorAll('#srcPos, .btns > button, #srcDur')]
    .filter((e) => e.offsetParent !== null && e.getBoundingClientRect().width > 0);
  const r = els.map((e) => { const b = e.getBoundingClientRect();
    return { id: e.dataset.src || e.id, l: b.left, r: b.right, cut: e.scrollWidth > e.clientWidth + 1 }; })
    .sort((a, b) => a.l - b.l);
  const gaps = [];
  for (let i = 1; i < r.length; i++) gaps.push([r[i - 1].id, r[i].id, +(r[i].l - r[i - 1].r).toFixed(2)]);
  return { width: box.width, left: box.left, right: box.right, items: r, gaps,
           panel: document.querySelector('#pSource').getBoundingClientRect().width };
}"""


def test_source_transport_keeps_4px_between_controls(browser, live_server, project):
    pg = _pending(browser, live_server, project)
    try:
        _source_clip(pg)
        m = pg.evaluate(GAPS_JS)
        # the waiting proposal leaves the Source its width (verdict 11, punch 8: the Assistant's
        # full-height column squeezed it to ~345 px at 1440); a narrow Source is forced below
        assert m["panel"] >= 560, f"the proposal state squeezes the Source: {m['panel']} px"
        names = [i["id"] for i in m["items"]]
        for want in ("srcPos", "in", "out", "back", "play", "fwd", "insert"):
            assert want in names, (want, names)
        for a, b, gap in m["gaps"]:
            assert gap >= 4, f"{a} and {b} are {gap} px apart at a {m['panel']:.0f} px Source: {m}"
        for i in m["items"]:
            assert not i["cut"], f"{i['id']} is cut: {m}"
            assert i["l"] >= m["left"] - 0.5 and i["r"] <= m["right"] + 0.5, f"{i['id']} spills: {m}"
        _shot(pg, "chrome-source-transport-1440")

        # and narrow, down to 340 px (a dragged splitter, a small window), the same holds (the
        # duration goes first)
        for w in (420, 380, 340):
            pg.evaluate("(w) => { const p = document.querySelector('#pSource');"
                        " p.style.flex = `0 0 ${w}px`; p.style.width = `${w}px`;"
                        " p.style.maxWidth = `${w}px`; }", w)
            pg.wait_for_timeout(100)
            m = pg.evaluate(GAPS_JS)
            for a, b, gap in m["gaps"]:
                assert gap >= 4, f"{a} and {b} are {gap} px apart at {m['panel']:.0f} px: {m}"
            for i in m["items"]:
                assert not i["cut"] and i["r"] <= m["right"] + 0.5, f"{i['id']} at {m['panel']:.0f} px: {m}"
        assert pg.errors == []
    finally:
        pg.close()


LABELS_JS = """() => [...document.querySelectorAll('%s .ghost')].map((d) => {
  // the label's text against the band's content edge (the ghost's border and 5 px padding):
  // CSS's ellipsis is not in the DOM, so a label is cut when its text runs past that edge
  const n = d.querySelector('.gtxt .name'), b = d.querySelector('.gtxt');
  const edge = d.getBoundingClientRect().right - 1 - 5;
  const rect = (e) => { const r = document.createRange(); r.selectNodeContents(e); return r.getBoundingClientRect(); };
  const cut = !!b.textContent && rect(b).right > edge + 0.5;
  return { k: +d.dataset.k, w: d.getBoundingClientRect().width, name: n ? n.textContent : null,
           text: b.textContent, over: cut, nameOver: false, title: d.title };
})""" % GHOST


def test_ghost_labels_are_never_one_letter_and_an_ellipsis(browser, live_server, project):
    pg = _pending(browser, live_server, project)
    try:
        labels = pg.evaluate(LABELS_JS)
        assert len(labels) == len(LENGTHS)
        full = [g for g in labels if g["name"] in ("CLIP_A", "CLIP_B", "CLIP_C")]
        assert full, f"the long shots are named in full: {labels}"
        for g in labels:
            assert not g["over"] and not g["nameOver"], f"label truncated: {g}"
            nm = g["name"] or ""
            assert nm in ("", "CLIP_A", "CLIP_B", "CLIP_C") or (nm.isdigit() and len(nm) >= 2) \
                or nm.startswith("CLIP_"), g
            assert not nm.endswith("…"), g
            # the tooltip names the clip whatever the label says
            assert "CLIP_" in g["title"], g
        assert any(g["name"] != "CLIP_A" and g["name"] not in ("CLIP_B", "CLIP_C") for g in labels), \
            f"some ghost is too narrow for its name at fit zoom (the case under test): {labels}"
        # a numbered clip falls back to its number, never "C…"; then to nothing
        lab = "(w) => tlLanes.ghostLabel('CLIP_05', w).name"
        assert pg.evaluate(lab, 200) == "CLIP_05"
        assert pg.evaluate(lab, 32) == "05"
        assert pg.evaluate(lab, 14) == ""
        assert pg.evaluate("tlLanes.ghostLabel('CLIP_05', 200, '2×')") == {"name": "CLIP_05", "badge": "2×"}
        assert pg.evaluate("tlLanes.ghostLabel('CLIP_05', 40, '2×', true).name") in ("", "05")
        # zoomed in, every shot has room: every label is the full name again
        pg.evaluate("tl.zoomTo(tl.state.zoom * 6, { at: 0, viewX: 0 })")
        pg.wait_for_timeout(150)
        wide = [g for g in pg.evaluate(LABELS_JS) if g["w"] >= 70]
        assert wide and all(g["name"].startswith("CLIP_") and not g["over"] for g in wide), wide
        _shot(pg, "chrome-ghost-labels-1440")
        assert pg.errors == []
    finally:
        pg.close()


COUNT_TOAST = """() => new Promise((res) => {
  const seen = [];
  const el = document.querySelector('#toast');
  const look = () => { if (el.textContent.includes('%s') && el.classList.contains('show')) seen.push(1); };
  look();
  const mo = new MutationObserver(look);
  mo.observe(el, { childList: true, characterData: true, subtree: true, attributes: true });
  setTimeout(() => { mo.disconnect(); res(seen.length); }, 1500);
})""" % TOAST


def _boot_toasts(pg) -> int:
    """Whether the board said "building proxies" as it booted (1) or not (0)."""
    pg.wait_for_function("typeof P !== 'undefined' && P && P.clips", timeout=20000)
    return 1 if pg.evaluate(COUNT_TOAST) else 0


def test_building_proxies_toast_once_per_pass(browser, live_server, project):
    import server
    was = server.STATE.get("proxies_ready")
    server.STATE["proxies_ready"] = False           # a pass is running
    try:
        pg = _pending(browser, live_server, project, job="ch-toast")
        try:
            seen = [_boot_toasts(pg)]
            for _ in range(3):                          # three reloads, same pass
                pg.reload()
                seen.append(_boot_toasts(pg))
            assert seen[0] == 1, f"the first page of a pass says it: {seen}"
            assert sum(seen) == 1, f"at most once per pass: {seen}"
            # the pass ends (the page sees the previews ready) ...
            server.STATE["proxies_ready"] = True
            pg.wait_for_function("P.proxies_ready === true", timeout=10000)
            pg.reload()
            assert _boot_toasts(pg) == 0
            # (the board ends the pass with a POST late in its boot, app.js `proxyPassOver`, sent
            # after its first poll of the jobs, not waited on. On a loaded box it could land after
            # the flip below, where the server took it for one more ask of the same pass, and the
            # next pass was never said: [0, 0], 1 run in 20 at load 12-16 (lane TESTS2, wave 17).
            # So the server has ended the pass before the next one starts.)
            end = time.monotonic() + 10
            while server.PROXY_TOAST["key"] is not None and time.monotonic() < end:
                time.sleep(0.05)
            assert server.PROXY_TOAST["key"] is None, "the board never ended the pass"
            # ... and the next pass is said again, once
            server.STATE["proxies_ready"] = False
            pg.reload()
            again = [_boot_toasts(pg)]
            pg.reload()
            again.append(_boot_toasts(pg))
            assert again == [1, 0], again
            assert pg.errors == []
        finally:
            pg.close()
    finally:
        server.STATE["proxies_ready"] = was
