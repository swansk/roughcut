"""More of Premiere's Effects panel, built in (redesign wave 19, lane EFFECTS2; verdict 15
punch 8: "five video effects in all", dimension 7: "a sliver of the Effects panel").

Beyond Crop and Transform (test_crop_transform.py), Premiere's common ones on the same four
parts — the Effects bin, Effect Controls (with stopwatches where they have numbers), the
Program's grade canvas (a shader stage after the grade, in the stack's order: grade.js
`geoStage`) and the export (research/tools/assemble.py `vfx_graph`) drawing the same picture,
and the AI's `effect` op (test_actions_ui.py's AI-op == hand-op test):

  Transform › Horizontal Flip, Vertical Flip   no parameters; pixel for pixel

roughcut/vfx.py has THE formulas. Measured here as test_crop_transform measures: the
Program's parked picture at the frame's size against the export's frame, luma mean |Δ| under
2.0 code values, no whole pixel off; the effect visibly does what it says.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))
from roughcut import vfx  # noqa: E402
from test_crop_transform import (BAR, FPS, _clip, _sidecar, best_shift, export_frame, luma,  # noqa: E402
                                 render, sample)

SHOTS = [("plain", None, None),
         ("hflip", [{"id": "horizontal_flip", "kind": "horizontal_flip"}], None),
         ("vflip", [{"id": "vertical_flip", "kind": "vertical_flip"}], None),
         ("transform then both flips", [{"id": "transform", "kind": "transform", "params": {"rotation": 10, "scale": 90}},
                                        {"id": "horizontal_flip", "kind": "horizontal_flip"},
                                        {"id": "vertical_flip", "kind": "vertical_flip"}], None)]
SEGS = [{"id": f"s{i}", "clip": "STILL.MP4", "in": 1.0, "out": 3.0, "why": name,
         **({"controls": {"vfx": stack, **({"keys": keys} if keys else {})}} if stack else {})}
        for i, (name, stack, keys) in enumerate(SHOTS)]


def _edl() -> dict:
    return {"variant": "T", "title": "more video effects", "orient": "none", "story": "", "target_s": [2, 60],
            "segments": json.loads(json.dumps(SEGS)), "colour": {"mode": "off"}}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def proj(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("more-vfx")
    footage, sidecars, work, assets = (root / d for d in ("footage", "sidecars", "work", "assets"))
    for d in (footage, sidecars, work, assets / "music"):
        d.mkdir(parents=True)
    _clip(footage / "STILL.MP4", True)
    (sidecars / "STILL.audio.json").write_text(json.dumps(_sidecar("STILL")), encoding="utf-8")
    edl = root / "edl.json"
    edl.write_text(json.dumps(_edl(), indent=1), encoding="utf-8")
    return {"root": root, "footage": footage, "sidecars": sidecars, "work": work, "assets": assets, "edl": edl}


@pytest.fixture(scope="module")
def live_server(proj):
    import uvicorn
    import server

    server.configure(proj["edl"], proj["footage"], proj["sidecars"], proj["work"], proxies=False,
                     visual=proj["work"] / "no-visual", assets=proj["assets"])
    server.ensure_proxies(["STILL.MP4"])
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
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


@pytest.fixture
def page(live_server, proj):
    proj["edl"].write_text(json.dumps(_edl(), indent=1), encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "shader" in m.text else None)
        pg.add_init_script("try { localStorage.clear(); localStorage.setItem('roughcut.tour', 'seen'); } catch (e) {}")
        pg.goto(live_server)
        pg.wait_for_selector("#tl .tl-lane[data-lane=V1] .blk")
        pg.wait_for_function(f"typeof C !== 'undefined' && C && C.shots && segs.length === {len(SEGS)}"
                             " && window.effects && window.grade && grade.ready", timeout=15000)
        yield pg
        assert errors == [], errors
        browser.close()


def park(pg, i: int, n: int) -> None:
    pg.evaluate(f"tl.seek(tl.filmStart(segs[{i}].id) + {n} / {FPS} + 0.0005)")
    pg.wait_for_function(
        f"liveVideo() && liveVideo().readyState >= 2 && !liveVideo().seeking && grade.shot === segs[{i}].id",
        timeout=15000)
    pg.wait_for_timeout(500)


def test_the_new_kinds_are_premieres_with_their_formulas():
    """The flips have no parameters, live in Premiere's Transform folder, and are named in the
    render log; the schema refuses a parameter they do not have."""
    for kind, name in (("horizontal_flip", "Horizontal Flip"), ("vertical_flip", "Vertical Flip")):
        K = vfx.KINDS[kind]
        assert (K.name, K.folder, K.params, K.choices) == (name, "Transform", (), {})
        assert vfx.clean([{"id": kind, "kind": kind}]) == [{"id": kind, "kind": kind}]
        with pytest.raises(ValueError, match="has no parameter"):
            vfx.clean([{"id": kind, "kind": kind, "params": {"amount": 1}}])
    assert vfx.describe({"vfx": SHOTS[3][1]}) == "Transform scale 90, rotation 10; Horizontal Flip; Vertical Flip"


def test_the_programs_new_effects_are_the_exports_frame(page, proj):
    """Parked, the Program's picture at the frame's size is the export's frame for every new
    effect and stack (luma mean |Δ| under 2.0, no whole pixel off), drawn by the canvas's
    shader (its stages; no CSS on the canvas); and each does what it says to the picture."""
    pg = page
    pfoot = proj["root"] / "proxy-footage"
    pfoot.mkdir(exist_ok=True)
    link = pfoot / "STILL.MP4"
    if not link.exists():
        link.symlink_to(next(proj["work"].glob("proxies/*/v*")) / "STILL.mp4")
    film = render(proj, pfoot, "more-vfx")
    shots: dict = {}
    for i, (name, stack, _) in enumerate(SHOTS):
        park(pg, i, 24)
        got = sample(pg)
        if stack:
            st = pg.evaluate("grade.adjust && grade.adjust.vfx")
            assert st and len(st) == len(stack), (name, st)
            css = pg.evaluate("(() => { const c = getComputedStyle(document.querySelector('#gradeCanvas'));"
                              " return [c.clipPath, c.transform, c.filter]; })()")
            assert css == ["none", "none", "none"], (name, css)
        ex = export_frame(film, 48 * i + 24)
        f = {"luma_mean": round(float(np.abs(luma(got) - luma(ex)).mean()), 3), "shift": best_shift(got, ex)}
        print(f"\nEFFECTS2 {name}: {json.dumps(f)}", flush=True)
        assert f["luma_mean"] < BAR and f["shift"] == [0, 0], (name, f)
        shots[name] = ex
    plain = shots["plain"]
    # the flips are the picture mirrored, pixel for pixel (in the export: hflip / vflip)
    assert np.abs(shots["hflip"] - plain[:, ::-1]).mean() < 1.0
    assert np.abs(shots["vflip"] - plain[::-1]).mean() < 1.0


def test_a_flip_from_the_bin_is_one_undo_and_flips_the_program(page):
    """Double-clicked in the bin with a clip selected: one undo ("undo: Horizontal Flip"), a
    section in Effect Controls with its fx toggle and no rows (Premiere's has none), the
    Program mirrored; its fx toggle bypasses it."""
    pg = page
    park(pg, 0, 24)
    before = sample(pg)
    pg.locator("#tl .blk[data-id=s0]").click()
    pg.locator(".ws-tab[data-tool=effects]").click()
    pg.wait_for_selector("#fxbin .fxb-item")
    if not pg.locator("#fxbin .fxb-item[data-vfx]").count():
        pg.locator("#fxbin .fxb-folder[data-folder=veffects]").click()
    pg.locator("#fxbin .fxb-item[data-vfx=horizontal_flip]").dblclick()
    assert pg.evaluate("effects.vfx(tl.byId('s0'))") == [{"id": "horizontal_flip", "kind": "horizontal_flip"}]
    assert pg.locator("#undo").get_attribute("title").startswith("undo: Horizontal Flip")
    sec = pg.locator("#inspector .ec-vsec[data-kind=horizontal_flip]")
    sec.wait_for()
    assert sec.locator(".ec-vname").inner_text() == "Horizontal Flip" and sec.locator(".ec-row").count() == 0
    park(pg, 0, 24)
    after = sample(pg)
    assert np.abs(luma(after) - luma(before[:, ::-1])).mean() < 1.0
    sec.locator("[data-vfxact=off]").click()
    pg.wait_for_function("!(grade.adjust && grade.adjust.vfx)")
