"""Redesign wave 6, lane KEYUI — keyframes in Effect Controls (verdict 5 punch 4,
REDESIGN-GOAL dimension 7: "no keyframes, so nothing can change over time").

Under test, in a real browser on the synthetic three-clip bin: Premiere's stopwatch per
property (Position, Scale, Rotation, Opacity, Volume), the mini timeline beside it (the
shot's span, the playhead, a diamond per key), ◀ ◆ ▶, dragging a diamond in time, and the
Program monitor following the playhead through the keys (parked and playing). The data is
the wave-6 schema (judge/LOOP.md): `segments[i].controls.keys = {prop: [[t, v], ...]}`, t
in film seconds from the shot's start, linear between keys, held outside them; server.py's
`clean_keys` validates it on every save.
"""

from __future__ import annotations

import base64
import json
import socket
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

LIMIT_MS = 150


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
        "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first", "id": "shot-a"},
                     {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second", "id": "shot-b"}],
    }
    Path(project["edl"]).write_text(json.dumps(body, indent=1), encoding="utf-8")


def _open(pw, live_server):
    browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(live_server)
    pg.wait_for_selector("#tl .blk")
    pg.wait_for_function("typeof C !== 'undefined' && C && C.shots && window.effects", timeout=8000)
    pg._errors = errors
    return browser, pg


@pytest.fixture
def page(live_server, project):
    _seed(project)
    with sync_playwright() as pw:
        browser, pg = _open(pw, live_server)
        yield pg
        assert pg._errors == [], pg._errors
        browser.close()


def select_first(page) -> None:
    page.locator("#tl .blk").nth(0).click()
    page.wait_for_selector("#inspector .ec [data-sec=motion]")
    seek(page, 0.0)


def seek(page, t: float) -> None:
    """Park the playhead (and the monitor) at film time t, its frame drawn."""
    page.evaluate("(t) => tl.seek(t)", t)
    page.wait_for_function(
        "liveVideo() && liveVideo().readyState >= 2 && !liveVideo().seeking", timeout=10000)


def type_value(page, key: str, value: float) -> None:
    page.locator(f"#inspector .ec-v[data-k={key}]").click()
    inp = page.locator(f"#inspector .ec-v.editing[data-k={key}]")
    inp.fill(str(value))
    inp.press("Enter")


def undo_title(page) -> str:
    return page.locator("#undo").get_attribute("title") or ""


def keys(page, prop: str):
    return page.evaluate("(p) => effects.keys(p)", prop)


def opacity(page) -> float:
    return float(page.evaluate("getComputedStyle(liveVideo()).opacity"))


def wait_saved(page) -> None:
    page.wait_for_function(
        "document.querySelector('#saveState').dataset.state === 'saved'", timeout=8000)


def two_opacity_keys(page, t1: float = 1.5, v1: float = 20) -> list:
    """The stopwatch at the shot's first frame (100 %), then a value at t1: two keys."""
    select_first(page)
    page.locator("#inspector .ec-sw[data-sw=opacity]").click()
    assert undo_title(page).startswith("undo: animate opacity")
    assert keys(page, "opacity") == [[0, 100]]
    seek(page, t1)
    type_value(page, "opacity", v1)
    k = keys(page, "opacity")
    assert len(k) == 2 and k[0] == [0, 100] and k[1][1] == v1, k
    assert k[1][0] == pytest.approx(t1, abs=0.05), k
    return k


def mean_luma(page, clip: dict) -> float:
    """The mean of the Program monitor's pixels as composited on screen (a screenshot,
    decoded in the page)."""
    png = base64.b64encode(page.screenshot(clip=clip)).decode()
    return page.evaluate("""async (b64) => {
      const img = new Image(); img.src = 'data:image/png;base64,' + b64; await img.decode();
      const c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
      const x = c.getContext('2d'); x.drawImage(img, 0, 0);
      const d = x.getImageData(0, 0, c.width, c.height).data;
      let s = 0; for (let i = 0; i < d.length; i += 4) s += d[i] + d[i + 1] + d[i + 2];
      return s / (d.length / 4) / 3;
    }""", png)


# ------------------------------------------------------------------ the panel

def test_every_animatable_property_has_a_stopwatch_and_a_lane(page):
    select_first(page)
    groups = page.locator("#inspector .ec-sw").evaluate_all("els => els.map(e => e.dataset.sw)")
    assert groups == ["pos", "scale", "rotation", "opacity", "volume_db"], groups
    assert page.locator("#inspector .ec-lane").count() == 5
    # not animated: no ◀ ◆ ▶, no diamonds; the lane shows the playhead at the shot's start
    assert page.locator("#inspector .ec-anim.animated").count() == 0
    assert page.locator("#inspector .ec-kf").count() == 0
    assert page.locator("#inspector .ec-row[data-row=opacity] .ec-nav").evaluate(
        "e => getComputedStyle(e).visibility") == "hidden"
    left = page.locator("#inspector .ec-lane[data-lane=opacity] .ec-ph").evaluate("e => e.style.left")
    assert left == "0%", left
    # the lanes line up in one column, on the right of the panel
    xs = page.locator("#inspector .ec-lane").evaluate_all(
        "els => els.map(e => Math.round(e.getBoundingClientRect().right))")
    assert len(set(xs)) == 1, xs


def test_two_opacity_keys_interpolate_on_the_program(page):
    k = two_opacity_keys(page)
    # the panel: two diamonds, ◀ ◆ ▶ shown, the stopwatch on
    assert page.locator("#inspector .ec-lane[data-lane=opacity] .ec-kf").count() == 2
    assert page.locator("#inspector .ec-sw[data-sw=opacity]").get_attribute("aria-pressed") == "true"
    # the midpoint: the Program shows the interpolated value
    mid = k[1][0] / 2
    seek(page, mid)
    want = 100 + (k[1][1] - 100) * (mid / k[1][0])          # 60 %
    got = opacity(page) * 100
    assert abs(got - want) <= 0.1 * want, (got, want)
    assert page.evaluate("effects.value('opacity')") == pytest.approx(want, abs=1.0)
    assert page.locator("#inspector .ec-v[data-k=opacity]").input_value() == f"{want:.1f} %"
    # and the picture as composited on screen is that much of itself over the background:
    # the same frame at full opacity, at none, and at the keyed value
    box = page.evaluate("""(() => { const r = liveVideo().getBoundingClientRect();
        return {x: r.x + 4, y: r.y + 4, width: r.width - 8, height: r.height - 8}; })()""")
    layers = "['#pv0', '#pv1', '#gradeCanvas', '#fxCanvas']"
    keyed = mean_luma(page, box)
    page.evaluate(f"{layers}.forEach(s => {{ const e = document.querySelector(s); if (e) e.style.opacity = '0'; }})")
    bg = mean_luma(page, box)
    page.evaluate(f"{layers}.forEach(s => {{ const e = document.querySelector(s); if (e) e.style.opacity = '1'; }})")
    full = mean_luma(page, box)
    assert full - bg > 20, (full, bg)                          # there is a picture to dim
    a = (keyed - bg) / (full - bg)
    assert abs(a * 100 - want) <= 0.1 * want, (a, want, keyed, bg, full)
    effect_back = "effects.apply()"
    page.evaluate(f"{layers}.forEach(s => {{ const e = document.querySelector(s); if (e) e.style.opacity = ''; }}); {effect_back}")


def test_a_click_on_the_lane_moves_the_playhead_and_the_program_follows_within_150_ms(page):
    k = two_opacity_keys(page)
    lane = page.locator("#inspector .ec-lane[data-lane=opacity]")
    b = lane.bounding_box()
    dur = page.evaluate("tl.dur(segs[0])")
    frac = (k[1][0] / 2) / dur
    page.evaluate("""() => {
      const P = window.__probe = { t0: null, t1: null };
      const loop = () => {
        const o = parseFloat(getComputedStyle(liveVideo()).opacity);
        if (o > 0.54 && o < 0.66) { P.t1 = performance.now(); return; }
        if (performance.now() - P.t0 < 3000) requestAnimationFrame(loop);
      };
      document.addEventListener('pointerdown', () => { P.t0 = performance.now(); requestAnimationFrame(loop); },
        { capture: true, once: true });
    }""")
    page.mouse.click(b["x"] + b["width"] * frac, b["y"] + b["height"] / 2)
    page.wait_for_function("window.__probe.t1 != null", timeout=4000)
    ms = page.evaluate("window.__probe.t1 - window.__probe.t0")
    assert ms < LIMIT_MS, f"lane click → Program {ms:.0f} ms"
    assert page.evaluate("tl.state.playhead") == pytest.approx(k[1][0] / 2, abs=0.05)


def test_the_program_plays_the_animation(page):
    select_first(page)
    page.evaluate("effects.animate('opacity')")                 # 100 % at 0
    seek(page, 1.9)
    type_value(page, "opacity", 0)                               # 0 % at 1.9 s
    seek(page, 0.0)
    page.evaluate("""() => {
      const S = window.__samples = [];
      const t0 = performance.now();
      const loop = () => {
        S.push([tl.state.playhead, parseFloat(getComputedStyle(liveVideo()).opacity)]);
        if (performance.now() - t0 < 1500) requestAnimationFrame(loop);
      };
      requestAnimationFrame(loop);
      toggleCut();
    }""")
    page.wait_for_timeout(1700)
    page.evaluate("pauseCut()")
    samples = page.evaluate("window.__samples")
    inside = [(t, o) for t, o in samples if 0.05 < t < 1.85]
    distinct = sorted({round(o, 2) for _, o in inside})
    assert len(distinct) >= 5, distinct                           # it moved while playing
    for t, o in inside:                                           # and it is the line, ±10 %
        want = 1 - t / 1.9
        assert abs(o - want) <= 0.1 * max(want, 0.1) + 0.04, (t, o, want)


def test_scale_and_position_keys_move_the_picture(page):
    select_first(page)
    page.evaluate("effects.animate('scale')")
    seek(page, 1.0)
    type_value(page, "scale", 200)
    seek(page, 0.5)
    tf = page.evaluate("getComputedStyle(liveVideo()).transform")
    assert tf.startswith("matrix(1.5"), tf                        # halfway from 100 to 200
    # Position: one stopwatch, x and y keyed together
    seek(page, 0.0)
    page.locator("#inspector .ec-sw[data-sw=pos]").click()
    seek(page, 1.0)
    type_value(page, "posx", 1920)
    kx, ky = keys(page, "position_x"), keys(page, "position_y")
    assert [k[0] for k in kx] == [k[0] for k in ky] and len(kx) == 2, (kx, ky)
    assert kx[1][1] == pytest.approx(1.0) and ky[1][1] == pytest.approx(0.5)
    seek(page, 0.5)
    assert page.evaluate("effects.value('posx')") == pytest.approx(1440, abs=2)


# ------------------------------------------------------------------ undo

def test_one_undo_per_key_add_move_and_remove(page):
    select_first(page)
    page.locator("#inspector .ec-sw[data-sw=opacity]").click()
    seek(page, 1.0)
    add = page.locator("#inspector .ec-row[data-row=opacity] [data-kact=key]")
    add.click()
    assert undo_title(page).startswith("undo: add keyframe")
    assert [k[0] for k in keys(page, "opacity")] == [0, pytest.approx(1.0, abs=0.05)]
    t_key = keys(page, "opacity")[1][0]
    page.keyboard.press("Control+z")
    assert len(keys(page, "opacity")) == 1
    page.keyboard.press("Control+Shift+z")
    assert len(keys(page, "opacity")) == 2

    # ◀ ▶ go from key to key
    page.locator("#inspector .ec-row[data-row=opacity] [data-kact=prev]").click()
    assert page.evaluate("tl.state.playhead") == pytest.approx(0, abs=1e-3)
    page.locator("#inspector .ec-row[data-row=opacity] [data-kact=next]").click()
    assert page.evaluate("tl.state.playhead") == pytest.approx(t_key, abs=1e-3)

    # drag the second diamond later: one entry, on a frame
    page.wait_for_function("document.querySelectorAll('#inspector .ec-lane[data-lane=opacity] .ec-kf').length === 2")
    kf = page.locator("#inspector .ec-lane[data-lane=opacity] .ec-kf").nth(1)
    lane = page.locator("#inspector .ec-lane[data-lane=opacity]").bounding_box()
    b = kf.bounding_box()
    x, y = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    for i in range(1, 6):
        page.mouse.move(x + lane["width"] * 0.05 * i, y)
    page.mouse.up()
    assert undo_title(page).startswith("undo: move keyframe")
    moved = keys(page, "opacity")[1][0]
    assert moved > t_key + 0.2, (t_key, moved)
    fps = page.evaluate("tl.gridRate()")
    assert abs(moved * fps - round(moved * fps)) < 1e-3, (moved, fps)   # on the frame grid
    page.keyboard.press("Control+z")
    assert keys(page, "opacity")[1][0] == pytest.approx(t_key, abs=1e-6)
    assert undo_title(page).startswith("undo: add keyframe")             # one entry, not one per move

    # ◆ on a key removes it: one entry
    seek(page, t_key)
    add.click()
    assert undo_title(page).startswith("undo: remove keyframe")
    assert len(keys(page, "opacity")) == 1
    page.keyboard.press("Control+z")
    assert len(keys(page, "opacity")) == 2

    # the stopwatch off: one entry, the value at the playhead stays
    seek(page, 0.0)
    page.locator("#inspector .ec-sw[data-sw=opacity]").click()
    assert undo_title(page).startswith("undo: stop animating opacity")
    assert keys(page, "opacity") == []
    assert page.evaluate("segs[0].controls") is None                     # 100 % is the default
    page.keyboard.press("Control+z")
    assert len(keys(page, "opacity")) == 2


# ------------------------------------------------------------------ persistence

def test_keys_survive_a_reload(live_server, project):
    _seed(project)
    with sync_playwright() as pw:
        browser, pg = _open(pw, live_server)
        k = two_opacity_keys(pg)
        wait_saved(pg)
        saved = pg.evaluate("async () => (await fetch('/api/project')).json()")
        assert saved["segments"][0]["controls"] == {"keys": {"opacity": k}}, saved["segments"][0]
        pg.reload()
        pg.wait_for_selector("#tl .blk")
        pg.wait_for_function("typeof C !== 'undefined' && C && C.shots && window.effects", timeout=8000)
        select_first(pg)
        assert keys(pg, "opacity") == k
        assert pg.locator("#inspector .ec-lane[data-lane=opacity] .ec-kf").count() == 2
        assert pg.locator("#inspector .ec-sw[data-sw=opacity]").get_attribute("aria-pressed") == "true"
        seek(pg, k[1][0] / 2)
        assert opacity(pg) == pytest.approx(0.6, abs=0.06)
        assert pg._errors == []
        browser.close()


def test_the_server_validates_keys(live_server, project):
    import httpx
    _seed(project)
    seg = {"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "id": "shot-a"}
    bad = [
        {"glow": [[0, 1]]},                                  # not an animatable prop
        {"opacity": [[1.0, 50], [0.5, 60]]},                 # unsorted
        {"opacity": [[0.5, 50], [0.5, 60]]},                 # two keys at one time
        {"opacity": [[0, 150]]},                             # out of range
        {"position_x": [[0, 11]]},                           # out of -10..10 frames
        {"volume_db": [[0, 12]]},                            # above +6 dB
        {"opacity": [[-1, 50]]},                             # before the shot
        {"opacity": [[0, True]]},                            # not a number
        {"opacity": [[0, "50"]]},
        {"opacity": [[0]]},                                  # not a pair
        {"opacity": {"0": 50}},                              # not a list
        {"opacity": [[i / 10, 50] for i in range(65)]},      # more than 64
        [["opacity", 0, 50]],                                # not an object
    ]
    for keys_ in bad:
        r = httpx.put(f"{live_server}/api/project",
                      json={"segments": [{**seg, "controls": {"keys": keys_}}]})
        assert r.status_code == 400, (keys_, r.status_code, r.text)
    for lumetri in ({"highlights": 101}, {"shadows": -101}, {"whites": "x"}, {"blacks": 200}):
        r = httpx.put(f"{live_server}/api/project", json={"segments": [{**seg, "controls": lumetri}]})
        assert r.status_code == 400, (lumetri, r.text)
    good = {"keys": {"opacity": [[0, 100], [1.5, 20]],
                     "position_x": [[0, 0.5], [1, 0.25]], "scale": [[i / 10, 100 + i] for i in range(64)]},
            "highlights": -40, "shadows": 30, "whites": 0, "blacks": 10}
    r = httpx.put(f"{live_server}/api/project", json={"segments": [{**seg, "controls": good}]})
    assert r.status_code == 200, r.text
    c = httpx.get(f"{live_server}/api/project").json()["segments"][0]["controls"]
    assert c["keys"]["opacity"] == [[0, 100], [1.5, 20]]
    assert len(c["keys"]["scale"]) == 64
    assert {k: c.get(k) for k in ("highlights", "shadows", "whites", "blacks")} == \
        {"highlights": -40, "shadows": 30, "whites": None, "blacks": 10}     # 0 is the default
    # an empty list is no animation
    r = httpx.put(f"{live_server}/api/project",
                  json={"segments": [{**seg, "controls": {"keys": {"opacity": []}}}]})
    assert r.status_code == 200
    assert "controls" not in httpx.get(f"{live_server}/api/project").json()["segments"][0]
