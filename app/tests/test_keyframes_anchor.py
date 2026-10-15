"""Redesign wave 7, lane KEYANCHOR — keyframes stay anchored to the MEDIA, as in Premiere.

A shot's keys (`segments[i].controls.keys = {prop: [[t, v], ...]}`, t film seconds from the
shot's start; judge/LOOP.md's wave-6 schema) count from its start, so every edit that moves
the start, cuts the shot or retimes it has to move them, or the animation slides off the
picture it was made for:

- a razor / split / Ctrl+K at film offset d: the first half keeps the keys before d and a
  key AT d holding the value there; the second half has the rest d earlier and a key at 0
  holding the same value — neither half jumps at the cut;
- an in-trim by Δ film seconds shifts every key by −Δ (keys before the new start go, a key
  at 0 holds their value); an out-trim drops keys past the end the same way; a slip shifts
  them by its delta; a speed change scales their times by old / new rate;
- undo brings the keys back exactly; the server's edit ops (roughcut/edits.py) do the same.

Fixture: opacity keys 100 % at 0 s and 0 % at 2 s on a 4 s shot (CLIP_A 1–5 s).
"""

from __future__ import annotations

import base64
import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import edits  # noqa: E402

KEYS = {"opacity": [[0, 100], [2, 0]]}


def _opacity_at(keys: list, t: float) -> float:
    return edits._key_at([(float(a), float(b)) for a, b in keys], t)


# ------------------------------------------------------------------ the server's ops (unit)

def _cut() -> list[dict]:
    return [{"id": "a", "clip": "CLIP_A.MP4", "in": 1.0, "out": 5.0,
             "controls": {"keys": json.loads(json.dumps(KEYS)), "contrast": 10}},
            {"id": "b", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0}]


def test_server_split_cuts_the_keys_at_the_razor():
    cut = _cut()
    r = edits.apply_ops(cut, None, [{"op": "split", "shot": "a", "at": 2.0}])
    a, b = r["segments"][0], r["segments"][1]
    assert a["controls"]["keys"] == {"opacity": [[0.0, 100.0], [1.0, 50.0]]}
    assert b["controls"]["keys"] == {"opacity": [[0.0, 50.0], [1.0, 0.0]]}
    assert a["controls"]["contrast"] == b["controls"]["contrast"] == 10
    # two shots' own controls, not one shared object
    assert a["controls"] is not b["controls"]
    assert a["controls"]["keys"] is not b["controls"]["keys"]
    # the input is untouched
    assert cut[0]["controls"]["keys"] == KEYS
    # no jump: the end of A and the start of B show the value the shot had at 1 s
    assert _opacity_at(a["controls"]["keys"]["opacity"], edits.dur(a)) == pytest.approx(50)
    assert _opacity_at(b["controls"]["keys"]["opacity"], 0) == pytest.approx(50)


def test_server_split_at_a_speed_cuts_in_film_time():
    cut = _cut()
    cut[0]["speed"] = 2.0                        # 4 s of clip = 2 s of film; keys at 0, 2
    r = edits.apply_ops(cut, None, [{"op": "split", "shot": "a", "at": 2.0}])   # 0.5 s of film in
    a, b = r["segments"][0], r["segments"][1]
    assert a["controls"]["keys"] == {"opacity": [[0.0, 100.0], [0.5, 75.0]]}
    assert b["controls"]["keys"] == {"opacity": [[0.0, 75.0], [1.5, 0.0]]}


def test_server_freeze_and_ranged_speed_split_the_keys_too():
    r = edits.apply_ops(_cut(), None, [{"op": "freeze", "shot": "a", "at": 3.0, "seconds": 1.0}])
    first, still, second = r["segments"][:3]
    assert first["controls"]["keys"] == {"opacity": [[0.0, 100.0], [2.0, 0.0]]}
    assert "controls" not in still
    assert second["controls"]["keys"] == {"opacity": [[0.0, 0.0]]}
    r = edits.apply_ops(_cut(), None, [{"op": "speed", "shot": "a", "rate": 2, "from": 2.0, "to": 4.0}])
    p1, p2, p3 = r["segments"][:3]
    assert p1["controls"]["keys"] == {"opacity": [[0.0, 100.0], [1.0, 50.0]]}
    # 1 s of film at 1x (keys 50 → 0 at 1 s) becomes 1 s of film at 2x... its own film: 2 s → 1 s
    assert p2.get("speed") == 2.0
    assert p2["controls"]["keys"] == {"opacity": [[0.0, 50.0], [0.5, 0.0]]}
    assert p3["controls"]["keys"] == {"opacity": [[0.0, 0.0]]}


def test_server_extend_and_set_range_keep_the_keys_on_the_media():
    r = edits.apply_ops(_cut(), None, [{"op": "extend", "shot": "a", "in": 0.5, "out": 0}])
    a = r["segments"][0]
    assert a["controls"]["keys"] == {"opacity": [[0.0, 75.0], [1.5, 0.0]]}
    # media time 2.0 s: 1.0 s into the old shot, 0.5 s into the new one — the same value
    assert _opacity_at(a["controls"]["keys"]["opacity"], 0.5) == pytest.approx(_opacity_at(KEYS["opacity"], 1.0))
    # an earlier in: every key later; an out before the last key: a key at the new end
    r = edits.apply_ops(_cut(), None, [{"op": "extend", "shot": "a", "in": -0.5, "out": -2.5}])
    assert r["segments"][0]["controls"]["keys"] == {"opacity": [[0.5, 100.0], [2.0, 25.0]]}
    # set_range: a slip of +0.5 s (in and out together)
    r = edits.apply_ops(_cut(), None, [{"op": "set_range", "shot": "a", "in": 1.5, "out": 5.5}])
    assert r["segments"][0]["controls"]["keys"] == {"opacity": [[0.0, 75.0], [1.5, 0.0]]}


def test_server_speed_scales_the_key_times():
    r = edits.apply_ops(_cut(), None, [{"op": "speed", "shot": "a", "rate": 2}])
    assert r["segments"][0]["controls"]["keys"] == {"opacity": [[0.0, 100.0], [1.0, 0.0]]}
    r = edits.apply_ops(_cut(), None, [{"op": "speed", "shot": "a", "rate": 0.5}])
    assert r["segments"][0]["controls"]["keys"] == {"opacity": [[0.0, 100.0], [4.0, 0.0]]}


def test_anchor_keys_edges_and_both_axes():
    keys = {"position_x": [[0, 0.5], [1, 1.0]], "position_y": [[0, 0.5], [1, 0.25]],
            "volume_db": [[0.5, -30]]}
    out = edits.anchor_keys(keys, 0.25, 0.75)
    assert out["position_x"] == [[0.0, 0.625], [0.5, 0.875]]
    assert out["position_y"] == [[0.0, 0.4375], [0.5, 0.3125]]
    # a single key inside the window stays put, shifted; no edge keys are invented
    assert out["volume_db"] == [[0.25, -30.0]]
    # a key exactly on an edge is kept, not doubled
    assert edits.anchor_keys({"scale": [[0, 100], [1, 50], [2, 0]]}, 1.0, 2.0) == {"scale": [[0.0, 50.0], [1.0, 0.0]]}
    assert edits.anchor_keys(None, 0, 1) is None


def test_server_saves_the_cut_keys():
    """The halves' keys are what the server's own validator keeps (t ≥ 0, sorted)."""
    import server
    r = edits.apply_ops(_cut(), None, [{"op": "split", "shot": "a", "at": 2.4},
                                       {"op": "extend", "shot": "new:1", "in": 0.3, "out": -0.7}])
    for s in r["segments"]:
        if "controls" in s:
            assert server.clean_keys(s["controls"]["keys"]) == s["controls"]["keys"]


# ------------------------------------------------------------------ the board (Playwright)

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
    server.ensure_proxies([f"{s}.MP4" for s in project["stems"]])
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
    project["edl"].write_text(original, encoding="utf-8")


def _seed(project) -> None:
    body = {
        "variant": "T", "title": "test cut", "orient": "none", "story": "",
        "target_s": [5, 20],
        "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 5.0, "why": "first", "id": "shot-a",
                      "controls": {"keys": KEYS}},
                     {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second", "id": "shot-b"}],
    }
    Path(project["edl"]).write_text(json.dumps(body, indent=1), encoding="utf-8")


@pytest.fixture
def page(live_server, project):
    pw_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
    _seed(project)
    with pw_api.sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(live_server)
        pg.wait_for_selector("#tl .blk")
        pg.wait_for_function("typeof C !== 'undefined' && C && C.shots && window.effects", timeout=8000)
        yield pg
        assert errors == [], errors
        browser.close()


def seek(page, t: float) -> None:
    page.evaluate("(t) => tl.seek(t)", t)
    page.wait_for_function(
        "liveVideo() && liveVideo().readyState >= 2 && !liveVideo().seeking", timeout=10000)
    page.wait_for_timeout(60)


def seg_keys(page, i: int) -> dict | None:
    return page.evaluate("(i) => { const c = tl.state.segs[i].controls; return c && c.keys ? c.keys : null; }", i)


def css_opacity(page) -> float:
    return float(page.evaluate("getComputedStyle(liveVideo()).opacity")) * 100


LAYERS = "['#pv0', '#pv1', '#gradeCanvas', '#fxCanvas']"


def _mean_luma(page, clip: dict) -> float:
    png = base64.b64encode(page.screenshot(clip=clip)).decode()
    return page.evaluate("""async (b64) => {
      const img = new Image(); img.src = 'data:image/png;base64,' + b64; await img.decode();
      const c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
      const x = c.getContext('2d'); x.drawImage(img, 0, 0);
      const d = x.getImageData(0, 0, c.width, c.height).data;
      let s = 0; for (let i = 0; i < d.length; i += 4) s += d[i] + d[i + 1] + d[i + 2];
      return s / (d.length / 4) / 3;
    }""", png)


def composited_alpha(page) -> float:
    """The Program's picture as composited on screen, as a fraction of itself over the
    background: (keyed − none) / (full − none), the same frame three ways."""
    box = page.evaluate("""(() => { const r = liveVideo().getBoundingClientRect();
        return {x: r.x + 4, y: r.y + 4, width: r.width - 8, height: r.height - 8}; })()""")
    keyed = _mean_luma(page, box)
    # the layers' own styles are put back as they were (effects.js writes them only on a change)
    page.evaluate(f"window.__kept = {LAYERS}.map(s => {{ const e = document.querySelector(s); return e ? e.style.opacity : null; }})")
    page.evaluate(f"{LAYERS}.forEach(s => {{ const e = document.querySelector(s); if (e) e.style.opacity = '0'; }})")
    bg = _mean_luma(page, box)
    page.evaluate(f"{LAYERS}.forEach(s => {{ const e = document.querySelector(s); if (e) e.style.opacity = '1'; }})")
    full = _mean_luma(page, box)
    page.evaluate(f"{LAYERS}.forEach((s, i) => {{ const e = document.querySelector(s); if (e) e.style.opacity = window.__kept[i]; }})")
    assert full - bg > 20, (full, bg)
    return (keyed - bg) / (full - bg) * 100


def test_razor_cuts_the_keys_and_the_program_does_not_jump(page):
    seek(page, 1.0)
    page.locator("#tl .blk").nth(0).click()
    seek(page, 1.0)
    page.locator("#tl").hover()
    page.keyboard.press("Control+k")                 # Premiere's Add Edit at the playhead
    page.wait_for_function("tl.state.segs.length === 3")
    d = page.evaluate("tl.dur(tl.state.segs[0])")
    assert d == pytest.approx(1.0, abs=0.01), d       # on the grid, 1 s in
    want = 100 - 50 * d
    a, b = seg_keys(page, 0), seg_keys(page, 1)
    assert a["opacity"][0] == [0, 100]
    assert a["opacity"][-1][0] == pytest.approx(d, abs=1e-6)
    assert a["opacity"][-1][1] == pytest.approx(want, abs=0.01)          # half A ends at 50 %
    assert b["opacity"][0] == [0, pytest.approx(want, abs=0.01)]          # half B starts at 50 %
    assert b["opacity"][1][0] == pytest.approx(2 - d, abs=1e-6)          # ...and reaches 0 at its 1 s
    assert b["opacity"][1][1] == 0
    assert page.evaluate("tl.state.segs[0].controls !== tl.state.segs[1].controls")
    assert page.locator("#undo").get_attribute("title").startswith("undo: split")

    # the Program across the cut: one frame before, on it, one frame after — the line
    fr = 1 / page.evaluate("tl.gridRate()")
    seen = []
    for t in (d - fr, d, d + fr):
        seek(page, t)
        expect = 100 - 50 * t
        css = css_opacity(page)
        px = composited_alpha(page)
        assert abs(css - expect) <= 1.0, (t, css, expect)
        assert abs(px - expect) <= 0.1 * expect, (t, px, expect)
        seen.append(px)
    # no jump: each step is about one frame's worth (2 %), never a cut's worth
    assert abs(seen[1] - seen[0]) < 6 and abs(seen[2] - seen[1]) < 6, seen

    # undo: one entry, the keys exactly as they were
    page.evaluate("tl.undo()")
    assert page.evaluate("tl.state.segs.length") == 2
    assert seg_keys(page, 0) == KEYS


def test_in_trim_keeps_the_picture_at_a_media_time(page):
    # media time 2.0 s of CLIP_A is 1.0 s into the shot (in = 1.0)
    seek(page, 1.0)
    before_css = css_opacity(page)
    before_px = composited_alpha(page)
    assert before_css == pytest.approx(50, abs=1.0)
    page.evaluate("tl.setRange('shot-a', 1.5, null)")          # the in 0.5 s later
    seg = page.evaluate("tl.state.segs[0]")
    dt = seg["in"] - 1.0
    assert dt == pytest.approx(0.5, abs=0.02)
    k = seg["controls"]["keys"]["opacity"]
    assert k[0] == [0, pytest.approx(100 - 50 * dt, abs=0.01)]         # a key at 0 holding 75 %
    assert k[1][0] == pytest.approx(2 - dt, abs=1e-6) and k[1][1] == 0  # the 0 % key 0.5 s earlier
    assert page.locator("#undo").get_attribute("title").startswith("undo: trim")
    # the same media time is now 0.5 s into the shot: the same picture, the same opacity
    seek(page, 2.0 - seg["in"])
    assert css_opacity(page) == pytest.approx(before_css, abs=1.0)
    assert composited_alpha(page) == pytest.approx(before_px, abs=0.1 * before_px)
    page.evaluate("tl.undo()")
    assert seg_keys(page, 0) == KEYS
    assert page.evaluate("tl.state.segs[0].in") == 1.0


def test_a_trim_drag_past_a_key_and_back_keeps_it(page):
    """A drag calls setRange once a frame inside one gesture: the keys follow from the
    drag's start, so going past a key and back within the drag loses nothing."""
    page.evaluate("tl.setRange('shot-a', 1.5, null)")
    direct = seg_keys(page, 0)
    page.evaluate("tl.undo()")
    page.evaluate("""() => { tl.begin('trim'); tl.setRange('shot-a', 3.5, null);
                              tl.setRange('shot-a', 2.0, null); tl.setRange('shot-a', 1.5, null);
                              tl.commit(); }""")
    assert seg_keys(page, 0) == direct
    # one undo entry, and it restores exactly
    page.evaluate("tl.undo()")
    assert seg_keys(page, 0) == KEYS
    # an out-trim before the last key: a key at the new end holding its value
    page.evaluate("tl.setRange('shot-a', null, 2.5)")
    k = seg_keys(page, 0)["opacity"]
    d = page.evaluate("tl.dur(tl.state.segs[0])")
    assert k[0] == [0, 100] and k[-1][0] == pytest.approx(d, abs=1e-6)
    assert k[-1][1] == pytest.approx(100 - 50 * d, abs=0.01)
    page.evaluate("tl.undo()")
    # a slip: in and out together, the keys by the slip's delta
    page.evaluate("tl.setRange('shot-a', 1.5, 5.5)")
    k = seg_keys(page, 0)["opacity"]
    assert k[0] == [0, pytest.approx(75, abs=0.6)] and k[1][0] == pytest.approx(1.5, abs=0.02)
    page.evaluate("tl.undo()")
    assert seg_keys(page, 0) == KEYS


def test_speed_scales_the_keys_and_undo_restores(page):
    page.evaluate("tl.setSpeed('shot-a', 2)")
    k = seg_keys(page, 0)["opacity"]
    assert k == [[0, 100], [pytest.approx(1.0, abs=1e-6), 0]], k
    # the Program: half way to the 0 % key is half way in film time at 2x
    seek(page, 0.5)
    assert css_opacity(page) == pytest.approx(50, abs=1.5)
    page.evaluate("tl.undo()")
    assert seg_keys(page, 0) == KEYS
    page.evaluate("tl.redo()")
    assert seg_keys(page, 0)["opacity"][1][0] == pytest.approx(1.0, abs=1e-6)


def test_cut_keys_survive_the_save(page):
    page.evaluate("tl.split('shot-a', 1.0)")
    page.wait_for_function("document.querySelector('#saveState').dataset.state === 'saved'", timeout=8000)
    page.reload()
    page.wait_for_selector("#tl .blk")
    page.wait_for_function("tl.state.segs.length === 3", timeout=8000)
    a, b = seg_keys(page, 0), seg_keys(page, 1)
    assert a["opacity"][-1][1] == pytest.approx(b["opacity"][0][1])
    assert b["opacity"][0][0] == 0
