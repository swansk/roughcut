"""Labels that fit on real footage (verdict 8, punch 6; wave 11 lane SMALL2).

Judge 8, at 1440 on Killington's first proposal (17 shots, 2:56): one ghost still read
`0…` — *"the canvas measure says '05' fits, the browser's ellipsis says it does not"* —
and V1's blocks read "CLI…" / "CL…" at fit zoom. Here, on a bin named like Killington's
(CLIP_01 … CLIP_12) with the first proposal's own shot lengths, in the board's own font
stack (whatever the browser resolves it to on this machine):

  * no ghost's label band overflows its box (the browser's ellipsis never shows), and
    each label is the clip's name, its number ("05"), or nothing;
  * the same holds for V1's blocks once that cut is the sequence: the name, else the
    number ("02"), never "CLI…".
"""

from __future__ import annotations

import json
import subprocess

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")

from test_proposal_review import (  # noqa: E402,F401  (fixtures: live_server, browser)
    GHOST, _open, _seed, _shot, _waiting, browser, live_server)

CLIP_S = 20.0
STEMS = [f"CLIP_{i:02d}" for i in range(1, 13)]
# Killington's first proposal (judge 8, proposal-1.json): clip and length of each shot
KILLINGTON = [("CLIP_02", 4.6), ("CLIP_02", 15.15), ("CLIP_03", 14.21), ("CLIP_02", 16.08),
              ("CLIP_05", 7.01), ("CLIP_06", 15.45), ("CLIP_07", 10.21), ("CLIP_04", 13.91),
              ("CLIP_07", 5.54), ("CLIP_08", 7.97), ("CLIP_08", 12.71), ("CLIP_11", 8.74),
              ("CLIP_09", 14.71), ("CLIP_12", 12.61), ("CLIP_01", 7.71), ("CLIP_01", 8.11),
              ("CLIP_01", 1.57)]
SEGS = [{"clip": f"{c}.MP4", "in": 0.5, "out": round(0.5 + d, 3), "why": f"shot {i + 1}"}
        for i, (c, d) in enumerate(KILLINGTON)]
PLAN = {"segments": SEGS, "notes": "Killington's first cut, by length."}


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> dict:
    """A bin named as Killington's is: twelve 20 s clips, CLIP_01 … CLIP_12."""
    root = tmp_path_factory.mktemp("labelfit")
    footage, sidecars, work = root / "footage", root / "sidecars", root / "work"
    for d in (footage, sidecars, work):
        d.mkdir()
    n = int(CLIP_S * 10)
    for stem in STEMS:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-nostdin",
             "-f", "lavfi", "-i", f"testsrc2=size=160x90:rate=24:duration={CLIP_S}",
             "-f", "lavfi", "-i", f"sine=frequency=440:duration={CLIP_S}",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", str(footage / f"{stem}.MP4")],
            check=True, capture_output=True)
        (sidecars / f"{stem}.audio.json").write_text(json.dumps({
            "clip": f"{stem}.MP4", "duration_s": CLIP_S, "sample_rate": 16000, "frame_hz": 10,
            "tracks": {"speech_band_db": [-30.0] * n, "flatness": [0.05] * n,
                       "onset": [0.2] * n, "vad": [0] * n},
            "transcript": [], "events": [],
            "summary": {"speech_fraction": 0.0, "wind_dominant_fraction": 0.0,
                        "integrated_lufs": -18.0, "peak_dbfs": -3.0}}), encoding="utf-8")
    edl = root / "edl.json"
    edl.write_text(json.dumps({"variant": "T", "title": "t", "orient": "none", "story": "",
                               "target_s": [120, 180], "segments": []}), encoding="utf-8")
    assets = root / "assets"
    (assets / "music").mkdir(parents=True)
    return {"root": root, "footage": footage, "sidecars": sidecars, "work": work,
            "edl": edl, "stems": STEMS, "assets": assets}


@pytest.fixture(autouse=True)
def _no_proposal_left_behind(live_server):
    yield
    import server
    for rec in server.STATE["asks"].glob("*.json"):
        rec.unlink(missing_ok=True)


# The browser's own verdict: a band whose content is wider than its box is what CSS's
# ellipsis cuts (scrollWidth counts every inline box, margins included).
BANDS_JS = """(sel) => [...document.querySelectorAll(sel)].map((box) => {
  const band = box.querySelector('.gtxt, .txt');
  const name = box.querySelector('.name');
  const cs = getComputedStyle(name);
  const r = document.createRange(); r.selectNodeContents(name);
  const font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
  return { dom: +r.getBoundingClientRect().width.toFixed(2), canvas: +tl.textWidth(name.textContent, font).toFixed(2), w: +box.getBoundingClientRect().width.toFixed(2), name: name.textContent,
           over: band.scrollWidth > band.clientWidth, sw: band.scrollWidth, cw: band.clientWidth,
           shown: getComputedStyle(band).display !== 'none',
           font,
           html: band.innerHTML.slice(0, 200) };
})"""


def _check(labels: list[dict], what: str, need: str = "number") -> None:
    assert len(labels) == len(SEGS), (what, len(labels))
    for g in labels:
        nm = g["name"]
        assert not (g["shown"] and g["over"]), f"{what}: a label overflows its block: {g}"
        assert not nm.endswith("…"), (what, g)
        # the canvas measures what the DOM lays out (the measure the fallback trusts)
        assert not g["shown"] or abs(g["dom"] - g["canvas"]) < 0.75, (what, g)
        assert nm in ("", *STEMS) or (nm.isdigit() and len(nm) == 2), (what, g)
    if need == "name":
        assert any(g["name"] in STEMS for g in labels), f"{what}: the wide shots are named: {labels}"
    else:
        assert any(g["name"].isdigit() for g in labels), \
            f"{what}: some shot is too narrow for its name (the case under test): {labels}"


def test_ghost_labels_fit_at_fit_zoom_on_killingtons_lengths(browser, live_server, project):
    _seed(project, [])
    _waiting("lf-first", PLAN)
    pg = _open(browser, live_server)
    try:
        pg.wait_for_selector("#proposal", state="visible", timeout=10000)
        pg.wait_for_function(f"document.querySelectorAll('{GHOST} .ghost').length === {len(SEGS)}",
                             timeout=8000)
        pg.evaluate("document.fonts.ready")
        labels = pg.evaluate(BANDS_JS, f"{GHOST} .ghost")
        # at 1440 with the proposal waiting, fit zoom leaves Killington's shots 13-50 px:
        # numbers, and nothing on the narrowest
        _check(labels, "ghost")
        assert not [g for g in labels if g["name"] == "" and g["w"] > 40], labels
        # zoomed in, the names come back, and still none is cut
        for k in (2.5, 6):
            pg.evaluate("(k) => tl.zoomTo(tl.state.zoom * k, { at: 0, viewX: 0 })", k)
            pg.wait_for_timeout(150)
            _check(pg.evaluate(BANDS_JS, f"{GHOST} .ghost"), f"ghost x{k}", need="name" if k > 3 else "number")
        _shot(pg, "labelfit-ghosts-1440")
        assert pg.errors == []
    finally:
        pg.close()


def test_v1_labels_fit_at_fit_zoom_on_killingtons_lengths(browser, live_server, project):
    _seed(project, SEGS)
    pg = _open(browser, live_server)
    try:
        pg.wait_for_function(f"document.querySelectorAll('#tl .tl-lane[data-lane=V1] .blk').length === {len(SEGS)}",
                             timeout=10000)
        pg.evaluate("tl.fit()")
        pg.wait_for_timeout(150)
        pg.evaluate("document.fonts.ready")
        labels = pg.evaluate(BANDS_JS, "#tl .tl-lane[data-lane=V1] .blk")
        _check(labels, "V1")
        assert not [g for g in labels if g["name"] == "" and g["w"] > 40], labels
        pg.evaluate("tl.zoomTo(tl.state.zoom * 6, { at: 0, viewX: 0 })")
        pg.wait_for_timeout(150)
        _check(pg.evaluate(BANDS_JS, "#tl .tl-lane[data-lane=V1] .blk"), "V1 x6", need="name")
        _shot(pg, "labelfit-v1-1440")
        assert pg.errors == []
    finally:
        pg.close()
