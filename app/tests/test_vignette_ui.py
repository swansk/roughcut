"""Lumetri › Vignette on the board (redesign wave 15, lane VIGNETTE; verdict 11 punch 7,
REDESIGN-GOAL dimension 7).

Effect Controls › Lumetri Color ends as Premiere's does: Basic Correction, Curves, Color
Wheels & Match, HSL Secondary, Vignette — Amount, Midpoint, Roundness, Feather, each a
stopwatch, a slider and a blue number. In real Chromium on a flat grey clip:

  * a drag of Amount darkens the Program's corners within 150 ms of the gesture, the
    centre untouched, and is one undo entry; a typed value is one too;
  * the export of the cut the board saved has the Program's picture: its corners within
    ±5 % of the Program's, its centre within ±2 % of the source (THE formula keeps the
    centre: test_vignette.py measures the export against it pixel for pixel);
  * the stopwatch keys the Amount and the Program follows the playhead through the keys;
  * Lumetri's ↺ takes the vignette and its keys off, one entry.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "research" / "tools"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

W, H, FPS, CLIP_S = 320, 180, 24, 4.0
LIMIT_MS = 150
SEGS = [{"id": "g1", "clip": "GREY.MP4", "in": 0.0, "out": 2.0, "why": "grey"},
        {"id": "g2", "clip": "GREY.MP4", "in": 0.0, "out": 2.0, "why": "grey again"}]
# patches as fractions of the frame (x0, y0, x1, y1): the four corners, the middle of the
# left edge, the centre
PATCHES = {"tl": (0, 0, .06, .08), "tr": (.94, 0, 1, .08), "bl": (0, .92, .06, 1), "br": (.94, .92, 1, 1),
           "left": (0, .45, .04, .55), "centre": (.42, .4, .58, .6)}


def _clip(path: Path) -> None:
    subprocess.run(
        ["nice", "-n", "19", "ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i", f"color=c=0x808080:size={W}x{H}:rate={FPS}:duration={CLIP_S}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={CLIP_S}",
         "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
         "-c:v", "libx264", "-preset", "ultrafast", "-color_range", "tv", "-colorspace", "bt709",
         "-color_primaries", "bt709", "-color_trc", "bt709", "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True)


def _sidecar(stem: str) -> dict:
    return {
        "clip": f"{stem}.MP4", "duration_s": CLIP_S, "sample_rate": 16000, "frame_hz": 10,
        "tracks": {"speech_band_db": [-30.0] * 40, "flatness": [0.05] * 40,
                   "onset": [0.2] * 40, "vad": [0] * 40},
        "transcript": [], "events": [],
        "summary": {"speech_fraction": 0.0, "wind_dominant_fraction": 0.0,
                    "integrated_lufs": -18.0, "peak_dbfs": -3.0, "audio_usable": True,
                    "n_words": 0, "analysis_s": 0.1},
        "candidates": [],
    }


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def proj(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("vignette-ui")
    footage, sidecars, work, assets = (root / d for d in ("footage", "sidecars", "work", "assets"))
    for d in (footage, sidecars, work, assets / "music"):
        d.mkdir(parents=True)
    _clip(footage / "GREY.MP4")
    (sidecars / "GREY.audio.json").write_text(json.dumps(_sidecar("GREY")), encoding="utf-8")
    return {"root": root, "footage": footage, "sidecars": sidecars, "work": work,
            "assets": assets, "edl": root / "edl.json"}


def _seed(proj) -> None:
    # the auto's balance off: the board and the export both start from the clip's own colour
    body = {"variant": "T", "title": "vignette", "orient": "none", "story": "", "target_s": [2, 20],
            "segments": json.loads(json.dumps(SEGS)), "colour": {"mode": "off"}}
    proj["edl"].write_text(json.dumps(body, indent=1), encoding="utf-8")


@pytest.fixture(scope="module")
def live_server(proj):
    import uvicorn
    import server
    _seed(proj)
    server.configure(proj["edl"], proj["footage"], proj["sidecars"], proj["work"],
                     proxies=False, assets=proj["assets"])
    server.ensure_proxies(["GREY.MP4"])
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
    _seed(proj)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "shader" in m.text else None)
        pg.add_init_script("try { localStorage.clear(); localStorage.setItem('roughcut.tour', 'seen'); } catch (e) {}")
        pg.goto(live_server)
        pg.wait_for_selector("#tl .blk")
        pg.wait_for_function("typeof C !== 'undefined' && C && C.shots && window.effects && window.grade", timeout=8000)
        yield pg
        assert errors == [], errors
        browser.close()


def park(page, i: int, at: float = 0.5) -> str:
    """Select shot i and park the Program on it, its frame drawn."""
    page.locator("#tl .blk").nth(i).click()
    page.wait_for_selector("#inspector .ec [data-sec=lumetri]")
    page.evaluate(f"cueAt({i}, segs[{i}].in + {at})")
    page.wait_for_function(
        f"liveVideo() && liveVideo().readyState >= 2 && grade.shot === segs[{i}].id && grade.probe()",
        timeout=10000)
    return page.evaluate(f"segs[{i}].id")


def twirl(page) -> None:
    if not page.locator("#inspector .ec-sub[data-sub=vignette]").evaluate("e => e.classList.contains('open')"):
        page.locator("#inspector [data-twirl=vignette]").click()
    page.locator("#inspector .ec-sub[data-sub=vignette] .ec-ssb").scroll_into_view_if_needed()


def undo_title(page) -> str:
    return page.locator("#undo").get_attribute("title") or ""


def wait_saved(page) -> None:
    page.wait_for_function("document.querySelector('#saveState').dataset.state === 'saved'", timeout=8000)


PATCH_JS = """(P) => {
  const s = grade.sample(320);
  if (!s) return null;
  const out = {};
  for (const [k, [x0, y0, x1, y1]] of Object.entries(P)) {
    let sum = 0, n = 0;
    // the sample is bottom row first: a patch's rows are mirrored (the patches are symmetric
    // in name only; every row is read where it lies)
    for (let y = Math.floor(y0 * s.h); y < Math.ceil(y1 * s.h); y++) {
      const row = s.h - 1 - y;
      for (let x = Math.floor(x0 * s.w); x < Math.ceil(x1 * s.w); x++) {
        const i = (row * s.w + x) * 4;
        sum += 0.2126 * s.data[i] + 0.7152 * s.data[i + 1] + 0.0722 * s.data[i + 2];
        n++;
      }
    }
    out[k] = sum / n;
  }
  return out;
}"""


def program(page) -> dict:
    return page.evaluate(PATCH_JS, PATCHES)


def patches(a: np.ndarray) -> dict:
    y = 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]
    hh, ww = y.shape
    return {k: float(y[int(y0 * hh):int(np.ceil(y1 * hh)), int(x0 * ww):int(np.ceil(x1 * ww))].mean())
            for k, (x0, y0, x1, y1) in PATCHES.items()}


def render_saved(proj, live_server, name: str) -> Path:
    """The cut exactly as the board saved it, through assemble.py as the board renders."""
    import httpx
    edl = httpx.get(f"{live_server}/api/project").json()
    path = proj["root"] / f"{name}.json"
    assert (edl.get("colour") or {}).get("mode") == "off"
    path.write_text(json.dumps(edl), encoding="utf-8")
    out = proj["root"] / f"{name}.mp4"
    r = subprocess.run(["nice", "-n", "19", "uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(path),
                        "--footage", str(proj["footage"]), "--profile", "match", "-o", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return out


def frame_rgb(path: Path, t: float) -> np.ndarray:
    k = int(round(t * FPS))
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
                        "-ss", f"{max(0.0, (k - 0.25) / FPS):.4f}", "-frames:v", "1",
                        "-vf", "scale=in_range=tv:out_range=pc:in_color_matrix=bt709",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).astype(np.float64).reshape(H, W, 3)


def type_value(page, key: str, value: float) -> None:
    """Premiere's way: click the blue number, type, Enter."""
    page.locator(f"#inspector .ec-v[data-k='{key}']").click()
    inp = page.locator(f"#inspector .ec-v.editing[data-k='{key}']")
    inp.fill(str(value))
    inp.press("Enter")


# ------------------------------------------------------------------ the section

def test_lumetri_ends_in_premieres_vignette_section(page):
    park(page, 0)
    subs = page.eval_on_selector_all("#inspector .ec-sec[data-sec=lumetri] .ec-sub", "els => els.map(e => e.dataset.sub)")
    assert subs == ["basic", "curves", "wheels", "hsl", "vignette"], subs
    sec = page.locator("#inspector .ec-sub[data-sub=vignette]")
    assert not sec.evaluate("e => e.classList.contains('open')")             # closed at first, as Premiere's
    assert sec.locator(".ec-ssh").inner_text().strip() == "Vignette"
    twirl(page)
    rows = sec.locator(".ec-row")
    assert rows.count() == 4
    assert [rows.nth(i).locator(".ec-name").inner_text() for i in range(4)] == ["Amount", "Midpoint", "Roundness", "Feather"]
    for i in range(4):
        r = rows.nth(i)
        assert r.locator(".ec-sw").count() == 1 and r.locator(".ec-sl").count() == 1 and r.locator(".ec-lane").count() == 1
        # the name is whole: not cut to "Roundn…"
        assert r.locator(".ec-name").evaluate("e => e.scrollWidth <= e.clientWidth + 1"), i
    vals = [page.locator(f"#inspector .ec-v[data-k={k}]").input_value()
            for k in ("vignette_amount", "vignette_midpoint", "vignette_roundness", "vignette_feather")]
    assert vals == ["0.0", "50.0", "0.0", "50.0"], vals
    assert "the export renders it" in rows.nth(0).get_attribute("title")


def test_an_amount_drag_vignettes_the_program_at_once_and_is_one_undo(page):
    park(page, 0)
    twirl(page)
    before = program(page)
    assert max(before.values()) - min(before.values()) < 3, before                # flat grey
    undo0 = undo_title(page)
    sl = page.locator("#inspector .ec-sl[data-sl=vignette_amount]")
    b = sl.bounding_box()
    page.evaluate("""() => {
      const P = window.__vg = { t0: null, t1: null, base: null };
      const s0 = grade.sample(64); const i = 0; P.base = s0.data[i];
      const loop = () => {
        const s = grade.sample(64);
        if (s && Math.abs(s.data[0] - P.base) > 6) { P.t1 = performance.now(); return; }
        if (performance.now() - P.t0 < 3000) requestAnimationFrame(loop);
      };
      const start = (e) => { if (P.t0 == null && (e.buttons & 1)) { P.t0 = performance.now(); requestAnimationFrame(loop); } };
      document.addEventListener('pointermove', start, { capture: true });
    }""")
    # from the thumb (0, the middle) toward the left end: Amount → about −3.5
    x0, y = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
    page.mouse.move(x0, y)
    page.mouse.down()
    for k in range(1, 7):
        page.mouse.move(x0 - 0.35 * b["width"] * k / 6, y)
    page.mouse.up()
    page.wait_for_function("window.__vg.t1 != null", timeout=4000)
    ms = page.evaluate("window.__vg.t1 - window.__vg.t0")
    amount = page.evaluate("effects.value('vignette_amount')")
    print(f"\nslider drag → Program: {ms:.0f} ms, Amount {amount}")
    assert ms <= LIMIT_MS, ms
    assert -4.5 < amount < -2.5, amount
    after = program(page)
    assert all(after[k] < before[k] - 20 for k in ("tl", "tr", "bl", "br")), (before, after)
    assert abs(after["centre"] - before["centre"]) <= 0.02 * before["centre"], (before, after)
    # one drag, one undo entry
    assert undo_title(page).startswith("undo: vignette amount"), undo_title(page)
    page.keyboard.press("Control+z")
    assert page.evaluate("effects.value('vignette_amount')") == 0
    assert undo_title(page) == undo0
    page.wait_for_function("!grade.adjust || !grade.adjust.vig")
    page.keyboard.press("Control+Shift+z")
    assert page.evaluate("effects.value('vignette_amount')") == pytest.approx(amount)


def test_the_export_has_the_programs_vignette(page, proj, live_server):
    """Typed values, Premiere's way, then the saved cut exported: its corners within ±5 % of
    the Program's and its centre within ±2 % of the source (g2, the same grey untouched)."""
    park(page, 0)
    twirl(page)
    for k, v in (("vignette_amount", -2), ("vignette_midpoint", 40), ("vignette_feather", 70),
                 ("vignette_roundness", 25)):
        type_value(page, k, v)
    assert undo_title(page).startswith("undo: vignette roundness"), undo_title(page)
    page.wait_for_function("grade.adjust && grade.adjust.vig && grade.adjust.vig[3] === 70")
    page.wait_for_timeout(300)
    shown = program(page)
    wait_saved(page)
    saved = page.evaluate("segs[0].controls")
    assert saved == {"vignette_amount": -2, "vignette_midpoint": 40, "vignette_feather": 70,
                     "vignette_roundness": 25}, saved
    film = render_saved(proj, live_server, "vignette")
    out, src = patches(frame_rgb(film, 1.0)), patches(frame_rgb(film, 3.0))
    print("\nProgram:", {k: round(v, 1) for k, v in shown.items()},
          "\nexport: ", {k: round(v, 1) for k, v in out.items()},
          "\nsource: ", {k: round(v, 1) for k, v in src.items()})
    for k in ("tl", "tr", "bl", "br", "left"):
        assert abs(out[k] - shown[k]) <= 0.05 * shown[k], (k, shown[k], out[k])
        assert out[k] < src[k] - 15, (k, out[k], src[k])
    assert abs(out["centre"] - src["centre"]) <= 0.02 * src["centre"], (out["centre"], src["centre"])


def test_the_stopwatch_keys_the_amount_and_the_program_follows_the_playhead(page):
    sid = park(page, 0, at=0.0)
    twirl(page)
    page.evaluate("tl.seek(0)")
    page.locator("#inspector .ec-sw[data-sw=vignette_amount]").click()            # a key of 0 at 0 s
    assert undo_title(page).startswith("undo: animate vignette amount"), undo_title(page)
    page.evaluate("tl.seek(1.5)")
    page.wait_for_function("liveVideo() && !liveVideo().seeking && liveVideo().readyState >= 2")
    type_value(page, "vignette_amount", -4)                                        # a key at 1.5 s
    keys = page.evaluate(f"effects.keys('vignette_amount', tl.byId('{sid}'))")
    assert keys == [[0, 0], [1.5, -4]], keys
    assert page.locator("#inspector .ec-row[data-row=vignette_amount] .ec-kf").count() == 2
    page.wait_for_timeout(300)
    late = program(page)
    page.evaluate("tl.seek(0.05)")
    page.wait_for_function("liveVideo() && !liveVideo().seeking && Math.abs(grade.adjust ? grade.adjust.vig[0] : 0) < 0.3"
                           " || !grade.adjust")
    page.wait_for_timeout(300)
    early = program(page)
    assert late["tl"] < early["tl"] - 25, (early, late)
    assert abs(late["centre"] - early["centre"]) < 3, (early, late)
    # the number and the slider follow the playhead
    assert float(page.locator("#inspector .ec-v[data-k=vignette_amount]").input_value()) > -0.3


def test_lumetri_reset_takes_the_vignette_and_its_keys_off(page):
    sid = park(page, 0)
    assert page.evaluate("effects.set('vignette_amount', -3)")
    page.evaluate("tl.seek(0)")
    page.evaluate("effects.animate('vignette_midpoint')")
    assert page.evaluate(f"effects.keys('vignette_midpoint', tl.byId('{sid}')).length") == 1
    page.locator("#inspector [data-sreset=lumetri]").click()
    c = page.evaluate(f"tl.byId('{sid}').controls || null")
    assert c is None or not any(k.startswith("vignette") for k in c) and not (c.get("keys") or {}), c
    assert undo_title(page).startswith("undo: colour reset"), undo_title(page)
    page.keyboard.press("Control+z")
    assert page.evaluate("effects.value('vignette_amount')") == -3
