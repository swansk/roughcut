"""Ask → preview → Accept, as a person reads it (wave 28 PROPOSE).

Karl, 2026-11-11: *"I tried clicking the ask for a change, accept change, preview change
buttons, and none of it worked really well. Either the video wouldn't play or I couldn't
tell if the change was actually made. [...] Even after I accepted the change, it looks
completely wrong."* The server had it right (the cut on disk was the proposal, the record
said accepted); the page did not:

  * while a proposal waited, the Program's ▶ Play and Space played the OLD cut (only an
    empty sequence played the proposal), and after ▶ Play it a Space quietly switched to
    the cut — the Program's length stayed the cut's;
  * what the proposal changed was a sentence of counts ("21 shots 2:06.5 → 18 shots
    2:36.6") and three red diagonal strikes that read as "cut out" — they were shots the
    proposal JOINED into longer ones;
  * after Accept his board still showed the Proposal lane (18 of 18 ✓), the strikes over
    the new V1 and "proposal · GX0104…" on the Program: the lanes' drawing of the
    proposal, never taken down.

Now: while a proposal waits the Program is the proposal's (Play, Space, its length, a tag on
its title); the lane, the panel and each ghost say what changes ("2 shots longer · 1 shorter
· 1 joined · 0:04 → 0:05", "+1.0s"), a joined shot is drawn and named as joined; Accept and
Discard take the lane, the strikes, the tag and the proposal's line down at once — even when
the lanes' own redraw is stuck — and Accept says in one line what happened and that Ctrl+Z
puts it back. An old waiting proposal says how old it is and what it was asked for.

No model is called: the waiting proposal is an Ask record on disk.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

GHOST = "#tl .tl-xlane[data-lane=ghost]"

CUT = [
    {"clip": "CLIP_A.MP4", "in": 1.0, "out": 2.0, "why": "hello"},
    {"clip": "CLIP_B.MP4", "in": 0.0, "out": 1.0, "why": "the reply, first half"},
    {"clip": "CLIP_B.MP4", "in": 1.0, "out": 2.0, "why": "the reply, second half"},
    {"clip": "CLIP_C.MP4", "in": 1.0, "out": 2.0, "why": "the ride"},
]
# A longer (+1.0 s); B's two halves run together as one shot (+1.0 s, the second half
# joined into it); C shorter (−0.5 s). 4.0 s → 4.5 s.
PLAN = {
    "segments": [
        {"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.5, "why": "the whole hello"},
        {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "the reply in one take, not cut mid-sentence"},
        {"clip": "CLIP_C.MP4", "in": 1.0, "out": 1.5, "why": "the ride, shorter"},
    ],
    "notes": "Every talking shot now starts and ends on a whole sentence.",
}
NOTE = "never cut the audio mid sentence"
CHANGE = "2 shots longer · 1 shorter · 1 joined · 0:04 → 0:05"


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
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
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
    project["edl"].write_text(original, encoding="utf-8")


@pytest.fixture
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        yield b
        b.close()


@pytest.fixture
def waiting(project):
    """The cut seeded, its undo history cleared, and a proposal made 3 h ago still waiting."""
    import server
    Path(project["edl"]).write_text(json.dumps({
        "variant": "T", "title": "test cut", "orient": "none", "story": "",
        "target_s": [3, 10], "segments": CUT}, indent=1), encoding="utf-8")
    undo = server.undo_path()
    undo.unlink(missing_ok=True)
    asks = server.STATE["asks"]
    asks.mkdir(parents=True, exist_ok=True)
    for old in asks.glob("*.json"):
        old.unlink()
    path = asks / "w28prop.json"
    path.write_text(json.dumps({"job": "w28prop", "created": time.time() - 3 * 3600, "note": NOTE,
                                "story": "", "plan": PLAN, "waits": True}), encoding="utf-8")
    yield path
    path.unlink(missing_ok=True)
    undo.unlink(missing_ok=True)


def _open(browser, live_server):
    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.errors = errors
    pg.goto(live_server)
    pg.wait_for_function("window.tlLanes && typeof P !== 'undefined' && P && P.clips && window.tl && tl.state",
                         timeout=20000)
    pg.wait_for_selector("#proposal", state="visible", timeout=10000)
    pg.wait_for_function(f"document.querySelectorAll('{GHOST} .ghost').length === 3", timeout=5000)
    return pg


def _down(pg) -> dict:
    """What is left of the proposal on the board."""
    return pg.evaluate(f"""() => ({{
        lane: !document.querySelector('{GHOST}').hidden,
        ghosts: document.querySelectorAll('{GHOST} .ghost').length,
        strikes: document.querySelectorAll('#tl .tl-over .strike').length,
        head: getComputedStyle(document.querySelector('#tl .tl-ghead')).display !== 'none',
        tag: !!document.querySelector('#progTag') && !document.querySelector('#progTag').hidden,
        what: document.querySelector('#playingWhat').textContent,
        total: document.querySelector('#total').textContent,
        cutTotal: tcOf(total()),
        mode: tlLanes.mode(),
    }})""")


def test_a_waiting_proposal_says_what_it_changes_and_the_program_plays_it(browser, live_server, waiting):
    pg = _open(browser, live_server)
    try:
        # what it changes, counted, in the panel and on the lane's name — the same words
        assert pg.locator("[data-testid=proposal-change]").inner_text() == CHANGE
        assert CHANGE in pg.locator("#tl .tl-ghead").inner_text()
        assert "of 3 ✓" not in pg.locator("#tl .tl-ghead").inner_text(), "the ✓ count only once a shot is turned down"
        # each ghost says what happens to its shot
        badges = pg.eval_on_selector_all(f"{GHOST} .ghost .dur", "els => els.map(e => e.textContent)")
        assert badges == ["+1.0s", "+1.0s", "−0.5s"], badges
        cls = pg.eval_on_selector_all(f"{GHOST} .ghost", "els => els.map(e => e.className)")
        assert "grew" in cls[0] and "grew" in cls[1] and "shrank" in cls[2], cls
        # B's second half is not "removed": it is joined into the longer B, and says so
        strike = pg.locator("#tl .tl-over .strike")
        assert strike.count() == 1
        assert "joined" in strike.get_attribute("class")
        assert strike.locator(".slbl").inner_text() == "joined"
        assert "joined into proposed shot 2" in strike.get_attribute("title")
        # how old it is, and what it was asked for
        asked = pg.locator("[data-testid=proposal-asked]").inner_text()
        assert asked.startswith("asked 3 h ago") and NOTE in asked, asked

        # the Program is the proposal's: a tag on its title, its length
        assert pg.locator("[data-testid=program-proposal-tag]").is_visible()
        st = _down(pg)
        assert st["mode"] == "proposal"
        assert st["total"] == pg.evaluate("tcOf(tlLanes.ghost().total)") != st["cutTotal"], st
        # ▶ Play plays the proposal, not the cut
        pg.locator("#playCut").click()
        pg.wait_for_function("tlLanes.running()", timeout=10000)
        assert pg.evaluate("player.playing") is False, "the cut is not what plays"
        assert pg.locator("#playingWhat").inner_text().startswith("proposal ·")
        # Space pauses it, and Space plays the proposal again (it used to switch to the cut)
        pg.keyboard.press("Space")
        pg.wait_for_function("!rcProposalPlayer.playing()", timeout=5000)
        pg.keyboard.press("Space")
        pg.wait_for_function("tlLanes.running()", timeout=10000)
        assert pg.evaluate("player.playing") is False
        pg.keyboard.press("Space")
        assert pg.errors == []
    finally:
        pg.close()


def test_accept_takes_the_proposal_off_the_board_and_says_what_happened(browser, live_server, waiting):
    pg = _open(browser, live_server)
    try:
        pg.locator("#playCut").click()                     # watching it, as Karl did
        pg.wait_for_function("tlLanes.running()", timeout=10000)
        pg.locator("#acceptProposal").click()
        st = _down(pg)
        assert not st["lane"] and st["ghosts"] == 0 and st["strikes"] == 0 and not st["head"], st
        assert not st["tag"], "the Program is the cut's again"
        assert st["mode"] == "cut" and not st["what"].startswith("proposal"), st
        assert st["total"] == st["cutTotal"] == "00:00:04:12", st
        assert pg.evaluate("segs.length") == 3
        said = "Accepted · 2 shots longer · 1 shorter · 1 joined · now 0:05 · Ctrl+Z undoes"
        assert pg.locator("#askState").inner_text() == said
        assert said in pg.locator("#toast").inner_text()
        # the record answered, and a reload shows the same: the cut, no proposal
        pg.wait_for_function("fetch('/api/asks/latest').then(r => r.json()).then(d => !d.pending)", timeout=5000)
        pg.reload()
        pg.wait_for_function("window.tlLanes && window.tl && tl.state && segs.length === 3", timeout=20000)
        pg.wait_for_timeout(800)
        st = _down(pg)
        assert not st["lane"] and st["strikes"] == 0 and not st["tag"], st
        assert not pg.locator("#proposal").is_visible()
        assert pg.errors == []
    finally:
        pg.close()


def test_accept_takes_it_down_even_when_the_lanes_redraw_is_stuck(browser, live_server, waiting):
    """Karl's board after Accept kept the whole pre-accept drawing (lane, ✓ / ✗, three
    strikes over the new V1, "proposal · …"): the lanes' redraw no longer reached its end.
    A part of the redraw that throws, or a drag whose end never came, must not keep an
    answered proposal on the board."""
    pg = _open(browser, live_server)
    try:
        # a part of the redraw that throws on every frame from now on
        pg.evaluate("() => { window.__keeps = keepsUsable; keepsUsable = () => { throw new Error('boom'); }; }")
        # and a drag from the timeline whose source is rebuilt before it ends: no dragend
        pg.evaluate("""() => { const b = document.querySelector('#tl .blk');
            const d = document.createElement('div'); d.className = 'avail'; b.parentElement.appendChild(d);
            d.dispatchEvent(new DragEvent('dragstart', { bubbles: true, dataTransfer: new DataTransfer() }));
            d.remove(); }""")
        pg.locator("#acceptProposal").click()
        st = _down(pg)
        assert not st["lane"] and st["ghosts"] == 0 and st["strikes"] == 0 and not st["head"], st
        assert not st["tag"] and not st["what"].startswith("proposal"), st
        pg.evaluate("() => { keepsUsable = window.__keeps; }")
    finally:
        pg.close()


def test_discard_takes_it_down_the_other_way(browser, live_server, waiting):
    pg = _open(browser, live_server)
    try:
        pg.locator("#playCut").click()
        pg.wait_for_function("tlLanes.running()", timeout=10000)
        pg.locator("#rejectProposal").click()
        st = _down(pg)
        assert not st["lane"] and st["ghosts"] == 0 and st["strikes"] == 0 and not st["head"], st
        assert not st["tag"] and st["mode"] == "cut" and not st["what"].startswith("proposal"), st
        assert st["total"] == st["cutTotal"] == "00:00:04:00", st
        assert pg.evaluate("segs.length") == 4
        # ▶ Play is the cut's again
        pg.locator("#playCut").click()
        pg.wait_for_function("player.playing", timeout=10000)
        pg.locator("#playCut").click()
        assert pg.errors == []
    finally:
        pg.close()
