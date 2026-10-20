"""The Timeline ruler's labels are whole or not drawn (wave 18 POLISH12, verdict 14 punch 10).

Verdict 14, dimensions 1 and 9: on the cold land the ruler's last label was clipped at the
Timeline's right edge at both 1440 and 1920 ("00:00:20:0"). Premiere never draws half a
timecode. Now a label the panel's edge would cut is not drawn: at the canvas's end it is
never made, and the one the view's visible edge crosses (zoomed in and scrolled) is hidden
until it is whole again.

At 1440×900 and 1920×1080, on an empty sequence (the cold land), on a cut at the fit zoom,
with Selects open (the narrowest Timeline the default layout makes), and zoomed from 2 to
3000 px/s at the start, the middle and the end of the scroll: every ruler label on screen
lies wholly inside the view's visible box and inside the Timeline panel, and the labels the
edge cut hid come back once they are whole. Same fixtures as test_workspace (the real
server on a real port, the synthetic bin); no model call.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")

from test_workspace import SEED, VIEWPORTS, _open, browser, live_server  # noqa: E402,F401

# Every ruler label the page shows: on screen (any part inside the view's visible box) and not
# hidden. `cut` holds those not wholly inside the view's visible box and the Timeline panel.
EDGE_JS = """() => {
  const v = document.querySelector('#tl .tl-view'), vb = v.getBoundingClientRect();
  const p = document.querySelector('#pTimeline').getBoundingClientRect();
  const left = vb.left + v.clientLeft, right = left + v.clientWidth;
  const shown = [...document.querySelectorAll('#tl .tl-ruler .tick.major label')]
    .filter(l => l.getClientRects().length && getComputedStyle(l).visibility !== 'hidden')
    .map(l => { const r = l.getBoundingClientRect(); return [l.textContent, r.left, r.right]; })
    .filter(([, a, b]) => b > left && a < right)
    .sort((a, b) => a[1] - b[1]);
  const cut = shown.filter(([, a, b]) => a < left - 0.5 || b > right + 0.5
                                       || a < p.left - 0.5 || b > p.right + 0.5);
  const hidden = document.querySelectorAll('#tl .tl-ruler .tick.major label.edge-cut').length;
  return {shown, cut, hidden, left, right, panel: [p.left, p.right], sl: v.scrollLeft,
          sw: v.scrollWidth, cw: v.clientWidth, zoom: tl.state.zoom};
}"""


def _whole(pg, where, least: int = 2) -> dict:
    d = pg.evaluate(EDGE_JS)
    assert d["cut"] == [], (where, "a ruler label cut by the edge", d)
    assert len(d["shown"]) >= least, (where, "too few ruler labels to read", d)
    return d


def _scroll(pg, x) -> None:
    pg.evaluate(f"(() => {{ const v = document.querySelector('#tl .tl-view'); v.scrollLeft = {x}; }})()")
    pg.wait_for_timeout(80)                  # the scroll event, then a frame


@pytest.mark.parametrize("size", VIEWPORTS, ids=lambda s: f"{s[0]}x{s[1]}")
def test_the_cold_lands_ruler_shows_no_label_cut_by_the_edge(live_server, project, browser, size):
    """The judge's screen: an empty sequence, the ruler drawn over the view at its default
    zoom; its last label used to run off the right edge."""
    pg = _open(browser, live_server, project, size, seed={**SEED, "segments": []})
    pg.wait_for_selector("#tl .tl-ruler .tick.major label", timeout=15000)
    pg.wait_for_timeout(400)
    d = _whole(pg, (size, "cold"))
    # only the label that would be cut is dropped: one more step would not have fitted
    step = d["shown"][1][1] - d["shown"][0][1]
    gap = d["right"] - d["shown"][-1][2]
    assert gap < step, (size, "a label that fits was dropped", gap, step, d)
    assert pg.errors == []
    pg.context.close()


@pytest.mark.parametrize("size", VIEWPORTS, ids=lambda s: f"{s[0]}x{s[1]}")
def test_a_cuts_ruler_is_whole_at_fit_with_selects_and_at_every_zoom(live_server, project, browser, size):
    """A cut at the fit zoom, with Selects open, then zoomed in and out and scrolled: no label
    cut by the view's edge, and a label hidden at the edge comes back once it is whole."""
    pg = _open(browser, live_server, project, size)
    pg.wait_for_timeout(400)
    _whole(pg, (size, "fit"))
    pg.locator(".ws-tab[data-tool=bin]").click()
    pg.wait_for_selector('#pProject section[data-tool="bin"]:not([hidden])', timeout=5000)
    pg.wait_for_timeout(500)
    _whole(pg, (size, "selects"))
    pg.locator(".ws-tab[data-tool=project]").click()
    pg.wait_for_timeout(400)
    hid = 0
    for z in (2, 5, 9, 17, 33, 70, 150, 400, 1000, 3000):
        pg.evaluate(f"tl.zoomTo({z})")
        pg.wait_for_timeout(80)
        _scroll(pg, 0)
        _whole(pg, (size, z, "start"))
        sw, cw = pg.evaluate("[document.querySelector('#tl .tl-view').scrollWidth,"
                             " document.querySelector('#tl .tl-view').clientWidth]")
        if sw > cw + 1:
            for x in (round((sw - cw) * 0.37) + 13, sw - cw):
                _scroll(pg, x)
                hid += _whole(pg, (size, z, x), least=1)["hidden"]
    assert hid >= 1, "zoomed and scrolled, some label met the view's edge and was hidden"
    # one scrolled back from the edge is drawn again: the label shown at the start is back
    pg.evaluate("tl.zoomTo(150)")
    pg.wait_for_timeout(80)
    _scroll(pg, 0)
    at0 = _whole(pg, (size, "back"))
    _scroll(pg, 41)
    _scroll(pg, 0)
    assert _whole(pg, (size, "back again"))["shown"] == at0["shown"]
    assert pg.errors == []
    pg.context.close()


@pytest.mark.parametrize("size", VIEWPORTS, ids=lambda s: f"{s[0]}x{s[1]}")
def test_a_long_films_ruler_is_whole_at_fit(live_server, project, browser, size):
    """A film as long as Karl's (Killington: 266 s), at the fit zoom: the step grows to
    whole seconds of timecode and the end's room past the film never holds half a label."""
    segs = [{"clip": c, "in": 0.0, "out": 4.0, "why": "x"}
            for c in ("CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4") * 22]
    pg = _open(browser, live_server, project, size, seed={**SEED, "segments": segs})
    pg.wait_for_timeout(400)
    tot = pg.evaluate("tl.total()")
    assert tot > 200, tot
    _whole(pg, (size, "long fit"))
    assert pg.errors == []
    pg.context.close()
    Path(project["edl"]).write_text(json.dumps(SEED, indent=1), encoding="utf-8")
