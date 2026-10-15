"""The first pass's picks as editor objects (verdict 4, punch 1; REDESIGN-GOAL dimension
11). Judge 4: "The pass found 81 moments, but the Selects bin says 'nothing kept yet' and
the timeline has no markers" — Selects and the markers read only /floor's keeps.

Now, once a first pass has made its picks:

  * Selects lists them (clip, Media In / Out in timecode, duration, kind, reason, hero ★)
    beside the editor's keeps, which say "yours"; a pick a keep already covers is that
    keep's row. A row is a clip: `,` inserts exactly its range, a drag onto V1 inserts
    it, a double-click opens it in Source with its In and Out set.
  * The timeline draws an amber marker wherever a pick overlaps a shot in the cut, the
    why on hover.
  * /floor stays, one link away from Selects, as the optional detail view.

The "first pass" is mocked: its record (what `_firstpass_walk` saves) and its proposal,
put up through the first pass's own hook (`rcShowProposal`) and accepted. No model is
called. The fixture's three 12 s clips carry five R8 speech candidates far enough apart
to be five picks. `ROUGHCUT_PICKS_SHOTS=<dir>` saves a screenshot of each state.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

SHOTS = os.environ.get("ROUGHCUT_PICKS_SHOTS")
CLIP_S = 12.0
STEMS = ["CLIP_A", "CLIP_B", "CLIP_C"]
# Each candidate is padded 0.5 s either side (picks.PAD_*); two on one clip more than
# MERGE_GAP_S apart stay two picks. 2 + 2 + 1 = 5.
LINES = {
    "CLIP_A": [(1.0, 2.0, "the lift line joke"), (8.0, 9.0, "look at that drop")],
    "CLIP_B": [(0.5, 2.0, "are we ready"), (7.0, 8.5, "here goes nothing")],
    "CLIP_C": [(4.0, 5.5, "what a run")],
}
CUT = [{"clip": "CLIP_A.MP4", "in": 0.0, "out": 3.0, "why": "open on the joke"},
       {"clip": "CLIP_B.MP4", "in": 6.0, "out": 9.5, "why": "the drop in"},
       {"clip": "CLIP_C.MP4", "in": 0.0, "out": 2.0, "why": "a breath"}]
LAYOUT = "localStorage.setItem('roughcut.ws.layout', JSON.stringify({proj: 0.42}))"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _sidecar(stem: str) -> dict:
    lines = LINES[stem]
    return {
        "clip": f"{stem}.MP4", "duration_s": CLIP_S, "sample_rate": 16000, "frame_hz": 10,
        "tracks": {"speech_band_db": [-30.0] * 120, "flatness": [0.05] * 120,
                   "onset": [0.2] * 120, "vad": [0] * 120},
        "transcript": [{"start": a, "end": b, "text": t, "no_speech_prob": 0.1,
                        "words": [{"w": w, "t": a, "p": 0.9} for w in t.split()]}
                       for a, b, t in lines],
        "events": [],
        "summary": {"speech_fraction": 0.3, "wind_dominant_fraction": 0.0,
                    "integrated_lufs": -18.0, "peak_dbfs": -3.0, "audio_usable": True,
                    "n_words": 9, "analysis_s": 0.1},
        "candidates": [{"t": a, "end": b, "why": f"speech: {t}", "score": 0.6 + 0.05 * i}
                       for i, (a, b, t) in enumerate(lines)],
    }


@pytest.fixture(scope="module")
def bin5(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("picks5")
    footage, sidecars, work = root / "footage", root / "sidecars", root / "work"
    for d in (footage, sidecars, work):
        d.mkdir()
    for stem in STEMS:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-nostdin",
             "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=24:duration={CLIP_S}",
             "-f", "lavfi", "-i", f"sine=frequency=440:duration={CLIP_S}",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", str(footage / f"{stem}.MP4")],
            check=True, capture_output=True)
        (sidecars / f"{stem}.audio.json").write_text(json.dumps(_sidecar(stem), indent=1),
                                                     encoding="utf-8")
    edl = root / "edl.json"
    assets = root / "assets"
    (assets / "music").mkdir(parents=True)
    return {"root": root, "footage": footage, "sidecars": sidecars, "work": work,
            "edl": edl, "assets": assets}


@pytest.fixture(scope="module")
def live(bin5):
    import uvicorn
    import server

    _seed(bin5, [])
    server.configure(bin5["edl"], bin5["footage"], bin5["sidecars"], bin5["work"],
                     proxies=False, visual=bin5["work"] / "visual", assets=bin5["assets"])
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


def _seed(bin5, segments: list[dict], selects: list[dict] | None = None) -> None:
    edl = {"variant": "T", "title": "picks cut", "orient": "none", "story": "",
           "target_s": [5, 30], "segments": segments}
    if selects is not None:
        edl["selects"] = selects
    Path(bin5["edl"]).write_text(json.dumps(edl, indent=1), encoding="utf-8")


def _record(made: bool) -> None:
    """What a finished first pass leaves on disk (`_firstpass_walk`'s record), or none."""
    import server
    p = server.firstpass_path()
    if not made:
        p.unlink(missing_ok=True)
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    p.write_text(json.dumps({"job": "fp-mock", "mode": "originate", "keep": [],
                             "state": "done", "started": now - 60, "finished": now,
                             "clips": 3, "manual": [],
                             "produced": {"picks": 5, "cut": {"shots": 3}}}),
                 encoding="utf-8")


def _picks(live) -> list[dict]:
    d = httpx.get(live + "/api/picks?order=clip", timeout=30).json()
    return d["picks"], d["firstpass_picked"]


def _overlapping(picks: list[dict], segs: list[list]) -> int:
    """(shot, pick) pairs where the pick's range overlaps the shot's footage — one marker each."""
    return sum(1 for clip, a, b in segs for p in picks
               if p["clip"] == clip and p["end"] > a and p["start"] < b)


def _open(browser, live, size=(1920, 1080)):
    pg = browser.new_page(viewport={"width": size[0], "height": size[1]})
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.errors = errors
    pg.add_init_script(LAYOUT)            # the Project panel wide: every Selects column
    pg.goto(live)
    pg.wait_for_function("window.tlLanes && typeof P !== 'undefined' && P && P.clips"
                         " && window.tl && tl.state", timeout=20000)
    pg.wait_for_timeout(300)
    return pg


def _shot(pg, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


def _ranges(pg) -> list[list]:
    return pg.evaluate("segs.map(s => [s.clip, s.in, s.out])")


def _first_pass(pg) -> None:
    """The mocked first pass's proposal, put up and accepted the way the pass's is."""
    pg.evaluate("(p) => rcShowProposal(p)", {
        "segments": CUT, "notes": "a first cut in three shots",
        "firstpass": {"diff": {"shots": [{"pos": i + 1, "status": "new", "locked": False}
                                         for i in range(len(CUT))]}}})
    pg.wait_for_selector("#proposal", state="visible", timeout=5000)
    pg.locator("#acceptProposal").click()
    pg.wait_for_function("segs.length === 3", timeout=8000)
    pg.wait_for_function(
        "document.querySelector('#saveState').textContent.startsWith('saved')", timeout=8000)


def _all_tab(pg, n: int) -> None:
    pg.evaluate("dock.open('bin')")
    pg.locator("#binTabs .tab[data-tab=all]").click()
    pg.wait_for_function(f"document.querySelectorAll('#library tr.keep').length === {n}",
                         timeout=10000)


def test_before_a_first_pass_selects_is_the_keeps_alone(browser, live, bin5):
    """No first pass, no picks in Selects or on the cut: the bin says nothing kept yet
    and offers the footage, as it did (test_ui_flow's empty bin)."""
    _record(False)
    _seed(bin5, CUT)
    picks, made = _picks(live)
    assert len(picks) == 5 and made is False
    pg = _open(browser, live)
    try:
        pg.evaluate("dock.open('bin')")
        pg.wait_for_function(
            "document.querySelector('#library').textContent.includes('nothing kept yet')",
            timeout=8000)
        assert pg.locator("#library tr.keep.pick").count() == 0
        assert pg.locator("#tl .tl-pick").count() == 0
        assert pg.errors == []
    finally:
        pg.close()


def test_after_the_first_pass_selects_lists_its_5_picks_and_comma_inserts_row_2(
        browser, live, bin5):
    _record(True)
    _seed(bin5, [])
    picks, made = _picks(live)
    assert made is True and len(picks) == 5
    pg = _open(browser, live)
    try:
        _first_pass(pg)
        assert _ranges(pg) == [[s["clip"], s["in"], s["out"]] for s in CUT]
        _all_tab(pg, 5)
        rows = pg.locator("#library tr.keep")
        assert pg.locator("#library tr.keep.pick").count() == 5
        heads = pg.locator("#library table.sel-list th").all_inner_texts()
        assert heads == ["Name", "Clip", "Media In", "Media Out", "Duration", "Reason", "Usage"], heads
        # every row: its clip, Media In / Out and Duration in the clip's timecode, the
        # kind, the reason — and the bin's order is by clip and start
        want = sorted(picks, key=lambda p: (p["clip"], p["start"]))
        for i, p in enumerate(want):
            cells = rows.nth(i).locator("td").all_inner_texts()
            assert cells[1] == p["clip"].split(".")[0], cells
            assert cells[2] == pg.evaluate(f"tl.tc({p['start']})"), cells
            assert cells[3] == pg.evaluate(f"tl.tc({p['end']})"), cells
            assert cells[4] == pg.evaluate(f"tl.tc({p['end'] - p['start']})"), cells
            assert cells[5] == p["why"], cells
            assert rows.nth(i).locator(".who.pass").inner_text() == p["kind"] == "speech"
            assert "the first pass's pick · speech" in rows.nth(i).get_attribute("title")
        # /floor is one link away, as the optional detail view
        hint = pg.locator("#libHint")
        assert "5 picks from the first pass · none kept yet" in hint.inner_text()
        assert hint.locator("a[href='/floor']").count() == 1
        # the markers: one wherever a pick overlaps a shot of the cut, the why on hover
        assert pg.locator("#tl .tl-pick").count() == _overlapping(picks, _ranges(pg)) == 2
        titles = [m["title"] for m in pg.evaluate("tlLanes.picks()")]
        assert any("the first pass · speech" in t and "why: " in t
                   and "the lift line joke" in t for t in titles), titles
        _shot(pg, "01-selects-picks-after-first-pass")

        # `,` on row 2 inserts exactly its range at the playhead
        r2 = want[1]
        rows.nth(1).click()
        assert "sel" in rows.nth(1).get_attribute("class")
        pg.evaluate("document.activeElement && document.activeElement.blur()")
        pg.evaluate("tl.seek(0)")
        pg.keyboard.press(",")
        pg.wait_for_function("segs.length === 4", timeout=5000)
        assert _ranges(pg)[0] == [r2["clip"], r2["start"], r2["end"]]
        assert _ranges(pg)[1:] == [[s["clip"], s["in"], s["out"]] for s in CUT]
        pg.wait_for_function(f"document.querySelectorAll('#tl .tl-pick').length === "
                             f"{_overlapping(picks, _ranges(pg))}", timeout=5000)
        assert pg.locator("#tl .tl-pick").count() == 3
        _shot(pg, "02-comma-inserted-row-2")
        assert pg.errors == []
    finally:
        pg.close()


def test_a_pick_row_drags_onto_v1_and_double_click_opens_it_in_source(browser, live, bin5):
    _record(True)
    _seed(bin5, CUT)
    picks, _ = _picks(live)
    want = sorted(picks, key=lambda p: (p["clip"], p["start"]))
    pg = _open(browser, live)
    try:
        pg.wait_for_selector("#tl .blk")
        _all_tab(pg, 5)
        row = pg.locator("#library tr.keep").nth(4)                 # CLIP_C 3.5–6.0
        p = want[4]
        pg.wait_for_function(
            "document.querySelectorAll('#library tr.keep')[4].draggable === true", timeout=5000)
        box = row.bounding_box()
        sx, sy = box["x"] + 30, box["y"] + box["height"] / 2
        lane = pg.locator("#tl .tl-lane[data-lane=V1]").bounding_box()
        tx = pg.evaluate("(() => { const v = document.querySelector('#tl .tl-view');"
                         " return v.getBoundingClientRect().left + tl.timeToX(0.05) - v.scrollLeft; })()")
        pg.mouse.move(sx, sy)
        pg.mouse.down()
        pg.mouse.move(sx + 12, sy + 6, steps=4)
        pg.mouse.move(tx, lane["y"] + lane["height"] / 2, steps=12)
        pg.mouse.up()
        pg.wait_for_function("segs.length === 4", timeout=8000)
        assert _ranges(pg)[0] == [p["clip"], p["start"], p["end"]]
        assert pg.evaluate("segs[0].why") == p["why"]

        # double-click: the pick in Source, its In and Out set
        q = want[3]                                                  # CLIP_B 6.5–9.0
        pg.locator("#library tr.keep").nth(3).dblclick()
        pg.wait_for_function(f"window.rcSource && rcSource.clip === '{q['clip']}'", timeout=5000)
        src = pg.evaluate("({...window.rcSource})")
        assert src["inPoint"] == pytest.approx(q["start"]) and src["outPoint"] == pytest.approx(q["end"])
        _shot(pg, "03-pick-in-source")
        assert pg.errors == []
    finally:
        pg.close()


def test_keeps_and_picks_both_show_and_are_told_apart(browser, live, bin5):
    """Two keeps from /floor: one where no pick is (C 8–10, a hero), one over a pick
    (B 0–2.5). Selects shows 4 picks and 2 keeps — the covered pick is the keep's row —
    and says which is which: the pass's kind, or "yours"."""
    _record(True)
    keeps = [{"clip": "CLIP_C.MP4", "start": 8.0, "end": 10.0, "hero": True, "why": "the last run"},
             {"clip": "CLIP_B.MP4", "start": 0.0, "end": 2.5, "why": "ready, kept"}]
    _seed(bin5, CUT)
    out = httpx.put(live + "/api/selects", json={"selects": keeps}, timeout=30).json()
    assert out.get("ok"), out
    picks, _ = _picks(live)
    pg = _open(browser, live)
    try:
        pg.wait_for_selector("#tl .blk")
        _all_tab(pg, 6)
        assert pg.locator("#library tr.keep.pick").count() == 4
        yours = pg.locator("#library tr.keep.yours")
        assert yours.count() == 2
        assert yours.locator(".who.yours").all_inner_texts() == ["yours", "yours"]
        assert pg.locator("#library tr.keep.pick .who.yours").count() == 0
        assert pg.locator("#library tr.keep.pick .who.pass").count() == 4
        # heroes first: the kept hero heads the list with its ★
        first = pg.locator("#library tr.keep").first
        assert "yours" in first.get_attribute("class") and "★" in first.locator(".w").inner_text()
        assert "kept by you on the pass" in first.get_attribute("title")
        assert "4 picks from the first pass · 2 kept by you" in pg.locator("#libHint").inner_text()
        # the markers: the uncovered picks and the keeps over the cut, told apart on hover
        visible = [p for p in picks if not (p["clip"] == "CLIP_B.MP4" and p["start"] == 0.0)]
        n = _overlapping(visible, _ranges(pg)) + _overlapping(
            [{"clip": k["clip"], "start": k["start"], "end": k["end"]} for k in keeps], _ranges(pg))
        assert pg.locator("#tl .tl-pick").count() == n
        assert pg.locator("#tl .tl-pick.pass").count() == _overlapping(visible, _ranges(pg))
        _shot(pg, "04-keeps-and-picks")
        assert pg.errors == []
    finally:
        pg.close()
        _seed(bin5, [], selects=[])
