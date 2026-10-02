"""HANDOFF roadmap item 5 — junk in the dock's bin, driven in a real browser.

The synthetic junk bin (conftest `junk_project`): CLIP_DARK is black and silent and
CLIP_BLIP is a one-second press, both proposed; CLIP_NIGHT is dim and real and is not.
The bin opens on the kept tab when there is a proposal; a proposed clip has a card of
its own and its keeps wear a `junk?` badge with Confirm / Keep; Confirm takes the clip
out of the grid's default view and the `junk` chip brings it back; Keep clears the
badge. Same fixture pattern as test_dock_ui.py. Skipped when playwright is absent.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request

import pytest

playwright_api = pytest.importorskip("playwright.sync_api",
                                     reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(junk_project):
    import uvicorn
    import server

    jp = junk_project
    jp["edl"].write_text(jp["seed"], encoding="utf-8")
    server.configure(jp["edl"], jp["footage"], jp["sidecars"], jp["work"],
                     proxies=False, assets=jp["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in jp["stems"]])
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
    jp["edl"].write_text(jp["seed"], encoding="utf-8")


def _put(url: str, body: dict) -> None:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="PUT",
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req) as r:
        assert r.status == 200


@pytest.fixture
def page(live_server, junk_project):
    junk_project["edl"].write_text(junk_project["seed"], encoding="utf-8")
    # one keep on the black clip, one on the good one
    _put(f"{live_server}/api/selects", {"selects": [
        {"clip": "CLIP_DARK.MP4", "start": 2.0, "end": 6.0, "why": "pocket"},
        {"clip": "CLIP_OK.MP4", "start": 0.5, "end": 2.0, "why": "hello there"}]})
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        pg = browser.new_page(viewport={"width": 1280, "height": 900})
        pg.goto(live_server)
        pg.wait_for_selector("#tl .blk")
        yield pg
        browser.close()


def _cards(pg) -> dict:
    return pg.evaluate("""() => [...document.querySelectorAll('#library .keep')].map((d) =>
        ({clip: d._keep ? d._keep.clip : d.dataset.clip, junkcard: d.classList.contains('junkcard'),
          badge: !!d.querySelector('.junkq .jb')}))""")


def test_badge_shows_and_confirm_hides_the_clip(page):
    pg = page
    pg.wait_for_selector("#library .junkcard")
    cards = _cards(pg)
    # the proposals have cards; the dark clip's keep wears the badge; the good one not
    assert {c["clip"] for c in cards if c["junkcard"]} == {"CLIP_DARK.MP4", "CLIP_BLIP.MP4"}
    keep_dark = next(c for c in cards if not c["junkcard"] and c["clip"] == "CLIP_DARK.MP4")
    keep_ok = next(c for c in cards if not c["junkcard"] and c["clip"] == "CLIP_OK.MP4")
    assert keep_dark["badge"] and not keep_ok["badge"]
    assert "junk 2" in pg.inner_text("#binFilter")

    pg.click('#library .junkcard[data-clip="CLIP_DARK.MP4"] button[data-verdict="junk"]')
    pg.wait_for_function("""() => ![...document.querySelectorAll('#library .keep')]
        .some((d) => (d._keep ? d._keep.clip : d.dataset.clip) === 'CLIP_DARK.MP4')""")
    assert {c["clip"] for c in _cards(pg)} == {"CLIP_BLIP.MP4", "CLIP_OK.MP4"}

    # the junk chip brings the confirmed clip back, answerable
    pg.click('#binFilter .chip[data-chip="junk"]')
    pg.wait_for_selector('#library .junkcard.confirmed[data-clip="CLIP_DARK.MP4"]')
    assert "CLIP_OK.MP4" not in {c["clip"] for c in _cards(pg)}


def test_keep_clears_the_badge(page):
    pg = page
    pg.wait_for_selector('#library .junkcard[data-clip="CLIP_BLIP.MP4"]')
    pg.click('#library .junkcard[data-clip="CLIP_BLIP.MP4"] button[data-verdict="keep"]')
    pg.wait_for_function("""() => !document.querySelector(
        '#library .junkcard[data-clip="CLIP_BLIP.MP4"]')""")
    rows = {r["clip"]: r["state"] for r in pg.evaluate(
        "async () => (await (await fetch('/api/junk')).json()).clips")}
    assert rows["CLIP_BLIP.MP4"] == "kept" and rows["CLIP_DARK.MP4"] == "proposed"
