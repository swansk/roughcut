"""The "building proxies" toast: once per pass, never over the Timeline (verdict 8, punch 7;
wave 11 lane SMALL2).

Judge 8 on Killington: *"the 'building proxies in the background' toast still comes back
on every page load for the hour the proxies take, parked over the Timeline"*. CRAFT had
kept the fact in localStorage, which every fresh browser (each of the judge's scripts, a
new profile, a private window) starts without. The server remembers it now. Here, during
one pass's proxy phase:

  * the first board says it; three reloads of that page say nothing; and nor do two
    brand-new browsers (fresh storage — the judge's case);
  * when it shows, its rectangle is inside the Program and meets no `#tl .tl-lane`;
  * the pass ending (the previews ready) and a new one starting says it again, once.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")

from test_proposal_review import (  # noqa: E402,F401  (fixtures: live_server, browser)
    _open, _seed, _shot, browser, live_server)

TOAST = "building proxies in the background"

# Whether this load's board was told to say it; and, if so, where the toast sits.
SAID_JS = """() => new Promise((res) => {
  const t0 = performance.now();
  const look = () => {
    if (window.rcProxyToastSaid === undefined) {
      if (performance.now() - t0 > 15000) return res({ said: null });
      return setTimeout(look, 30);
    }
    const el = document.querySelector('#toast');
    if (!window.rcProxyToastSaid) return res({ said: false });
    // after its fade in
    setTimeout(() => {
      const r = el.getBoundingClientRect();
      const lanes = [...document.querySelectorAll('#tl .tl-lane, #tl .tl-track, #pTimeline')]
        .map((l) => l.getBoundingClientRect()).filter((b) => b.width > 0 && b.height > 0);
      const meets = (b) => r.left < b.right && r.right > b.left && r.top < b.bottom && r.bottom > b.top;
      const prog = document.querySelector('#pProgram').getBoundingClientRect();
      res({ said: true, text: el.textContent, shown: el.classList.contains('show'),
            opacity: +getComputedStyle(el).opacity,
            rect: [r.left, r.top, r.right, r.bottom].map((v) => +v.toFixed(1)),
            lanes: lanes.length, hits: lanes.filter(meets).length,
            inProgram: r.left >= prog.left - 0.5 && r.right <= prog.right + 0.5
              && r.top >= prog.top - 0.5 && r.bottom <= prog.bottom + 0.5 });
    }, 400);
  };
  look();
})"""


def _said(pg) -> dict:
    out = pg.evaluate(SAID_JS)
    assert out["said"] is not None, "the board never asked the server"
    if out["said"]:
        assert TOAST in out["text"] and out["shown"] and out["opacity"] > 0.9, out
        assert out["lanes"] >= 3, out             # V1, the audio tracks, the panel
        assert out["hits"] == 0, f"the toast sits over the Timeline's tracks: {out}"
        assert out["inProgram"], f"the toast is not on the Program: {out}"
    return out


def test_proxies_toast_once_per_pass_across_reloads_and_fresh_browsers(browser, live_server, project):
    import server
    was = server.STATE.get("proxies_ready")
    server.PROXY_TOAST.update(key=None, missing=frozenset())
    _seed(project, [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "a"},
                    {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "b"}])
    server.STATE["proxies_ready"] = False           # a pass is running
    pages = []
    try:
        pg = _open(browser, live_server)
        pages.append(pg)
        first = _said(pg)
        assert first["said"] is True, f"the first board of a pass says it: {first}"
        _shot(pg, "proxy-toast-1440")
        seen = []
        for _ in range(3):                          # three reloads of the same page
            pg.reload()
            pg.wait_for_function("typeof P !== 'undefined' && P && P.clips", timeout=20000)
            seen.append(_said(pg)["said"])
        for _ in range(2):                          # two brand-new browsers: no storage at all
            other = _open(browser, live_server, size=(1920, 1080))
            pages.append(other)
            seen.append(_said(other)["said"])
        assert seen == [False] * 5, f"once per pass, not once per load: {seen}"

        # the pass ends (a board sees the previews ready) ...
        server.STATE["proxies_ready"] = True
        pg.wait_for_function("P.proxies_ready === true", timeout=10000)
        pg.reload()
        pg.wait_for_function("typeof P !== 'undefined' && P && P.clips", timeout=20000)
        pg.wait_for_timeout(600)
        assert pg.evaluate("window.rcProxyToastSaid === undefined"), "a ready board asks nothing"
        assert not pg.evaluate("document.querySelector('#toast').textContent.includes('%s')" % TOAST)
        # ... and the next pass is said again, once
        server.STATE["proxies_ready"] = False
        again = []
        for _ in range(2):
            pg.reload()
            pg.wait_for_function("typeof P !== 'undefined' && P && P.clips", timeout=20000)
            again.append(_said(pg)["said"])
        assert again == [True, False], again
        for p in pages:
            assert p.errors == [], p.errors
    finally:
        server.STATE["proxies_ready"] = was
        for p in pages:
            p.close()


def test_the_server_says_it_once_per_pass(live_server, project):
    """The endpoint alone: one yes per pass; new footage (a clip newly missing its proxy)
    is a new pass even if no board saw the last one end."""
    import httpx
    import server
    was = server.STATE.get("proxies_ready")
    server.PROXY_TOAST.update(key=None, missing=frozenset())
    extra = project["footage"] / "CLIP_Z.MP4"
    try:
        server.STATE["proxies_ready"] = False
        says = [httpx.post(f"{live_server}/api/proxies/toast").json()["say"] for _ in range(3)]
        assert says == [True, False, False], says
        extra.write_bytes((project["footage"] / "CLIP_A.MP4").read_bytes())
        says = [httpx.post(f"{live_server}/api/proxies/toast").json()["say"] for _ in range(2)]
        assert says == [True, False], says
        server.STATE["proxies_ready"] = True
        assert httpx.post(f"{live_server}/api/proxies/toast").json() == {"say": False, "ready": True}
        server.STATE["proxies_ready"] = False
        assert httpx.post(f"{live_server}/api/proxies/toast").json()["say"] is True
    finally:
        extra.unlink(missing_ok=True)
        server.STATE["proxies_ready"] = was
