"""Video effects play in real time (redesign wave 16, lane VFXPLAY; verdict 12 punch 2,
REDESIGN-GOAL dimension 6).

Verdict 12: "a shot with the new Sharpen effect plays at about 5-7 fps (holds of 150-217 ms)
at both 1440 and 1920 on a quiet box, and is normal with it off". The Program drew Sharpen
as an SVG feConvolveMatrix (and Gaussian Blur as a CSS blur()) on the grade canvas and the
videos, which the software compositor ran on every frame. Now grade.js's renderer draws
both as stages after the grade (the vignette's way: no other frame's program carries them;
built while the Program is parked, for the kinds the cut uses), at Playback Resolution while
playing, with THE formulas —
roughcut/vfx.py's 3 × 3 unsharp mask, the export's Gaussian of σ = Blurriness·W/1920 with
black outside the frame — and the CSS filter stays only on the layers no grade canvas draws.

On test_perf_latency's kind of footage (1280 × 720 59.94 H.264, a 1 s GOP and temporal
grain, proxied by the server's own `ensure_proxies`, a hand balance on every shot so the
grade canvas is the picture), measured inside the page:

  play     from 1.0 s before the cut into a shot with Sharpen 60 (and one with Gaussian
           Blur 12) to 2.0 s into it, on a 1440 and a 1920 board: the graded canvas's draws
           (`grade.draws` on every animation frame) by the frame clock (rAF's timestamp),
           the longest hold <= 67 ms (two frame periods at 29.97), ASSERTED; the worker's
           clock (`grade.drawLog`) and the page clock in the record, and the same plays with
           the effects bypassed (the control); no CSS filter on the canvas, the effect drawn
           by the shader (`grade.adjust.vfx`)
  formula  parked, the Program's picture at the frame's own size (`grade.sample`) with the
           effect is THE formula applied to the same picture without it: the 3 × 3 kernel
           (mean |Δ| under 1 code value), the Gaussian with black outside (under 1.5)
  parked   the parked monitor is THE formula on the monitor's own grid — the grid the CSS
           path it replaces drew on (the SVG kernel, the CSS blur of σ scaled to the
           monitor), measured beside it with the grade off — mean |Δ| under 1.5 / 2.0
  export   the saved shot's export does what the Program does: the detail Sharpen adds
           within 10 % of the Program's; what each does to the picture's patches (the edge
           the blur darkens) within ±5 % of the patch (test_vignette_ui's tolerance; the
           grade's own Program-vs-export difference is the same with and without it); its
           luma against THE formula on its own plain frame under 2.0 (its chroma is
           filtered on the 4:2:0 grid)

A box already past twice its cores skips the play, visibly (test_tx_playback's rule); its
numbers are written to $ROUGHCUT_VFXPLAY_LOG when that is set. The measurement helpers
(burners, load, ports, the limit, the gaps) are test_perf_latency's.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest

playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

from test_perf_latency import (  # noqa: E402
    FPS, GAP_LIMIT_MS, GRAIN, Burners, _burners, _free_port, _load, canvas_gaps, worker_gaps,
)

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "research" / "tools"
sys.path.insert(0, str(ROOT))
from roughcut import vfx  # noqa: E402

SRC_W, SRC_H = 1280, 720       # the proxy's own size (Killington's GoPro proxies)
CLIP_S = 8.0
STEMS = [("VP_A", 0), ("VP_B", 120), ("VP_C", 240)]
SHARP, BLUR = 60.0, 12.0       # verdict 12's: the AI's Sharpen 60; the judge's Gaussian Blur 12
SEGS = [
    {"clip": "VP_A.MP4", "in": 1.0, "out": 4.0, "why": "plain", "id": "s1"},
    {"clip": "VP_B.MP4", "in": 1.0, "out": 4.0, "why": "sharpened", "id": "s2",
     "controls": {"vfx": [{"id": "sharpen", "kind": "sharpen", "params": {"amount": SHARP}}]}},
    {"clip": "VP_C.MP4", "in": 1.0, "out": 4.0, "why": "blurred", "id": "s3",
     "controls": {"vfx": [{"id": "gaussian_blur", "kind": "gaussian_blur", "params": {"blurriness": BLUR}}]}},
    {"clip": "VP_A.MP4", "in": 5.0, "out": 7.0, "why": "plain again", "id": "s4"},
    # the still pattern the formula and the export are measured on (no motion, no temporal
    # grain: the parked frame and the exported one are the same picture)
    {"clip": "VP_S.MP4", "in": 1.0, "out": 3.0, "why": "still", "id": "s5"},
]
BALANCE = {"gain": [1.06, 1.0, 0.94], "exposure": 1.05, "knee": 0.8, "lift": 0.0}
PRE_S, POST_S = 1.0, 2.0       # played from 1.0 s before the cut to 2.0 s into the effected shot


def _too_busy() -> str | None:
    """Already past twice the cores before the test adds anything: a frame gap then says how
    busy the box is, not how the board plays (test_tx_playback's rule, visibly skipped)."""
    cores, load = os.cpu_count() or 4, _load()
    if load > 2 * cores and not os.environ.get("ROUGHCUT_PERF_BURNERS"):
        return f"box already at load {load:.1f} on {cores} cores before any burner: not a measurement of the app"
    return None


def _make_source(path: Path, hue: int) -> None:
    """A 1280x720 59.94 H.264 source with a keyframe every second and temporal grain (the
    proxy then runs at a real proxy's bitrate, test_perf_latency's `_make_source` at the
    proxy's size)."""
    subprocess.run(
        ["nice", "-n", "19", "ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i",
         f"testsrc2=size={SRC_W}x{SRC_H}:rate={FPS}:duration={CLIP_S},hue=h={hue},"
         f"noise=alls={GRAIN}:allf=t",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={CLIP_S}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True)


def _make_still(path: Path) -> None:
    """The still: testsrc2's first frame held, with a fixed (not temporal) grain for detail."""
    subprocess.run(
        ["nice", "-n", "19", "ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i",
         f"testsrc2=size={SRC_W}x{SRC_H}:rate={FPS}:duration={CLIP_S},"
         f"trim=end_frame=1,loop=loop=-1:size=1,noise=alls={GRAIN},trim=duration={CLIP_S}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={CLIP_S}",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True)


def _edl() -> dict:
    return {"variant": "T", "title": "vfx play", "orient": "none", "story": "", "target_s": [5, 30],
            "segments": json.loads(json.dumps(SEGS)),
            "colour": {"mode": "auto", "shots": {s["id"]: {"balance": dict(BALANCE)} for s in SEGS}}}


@pytest.fixture(scope="module")
def vp_project(tmp_path_factory):
    from conftest import _sidecar

    root = tmp_path_factory.mktemp("vfxplay")
    footage, sidecars, work = root / "footage", root / "sidecars", root / "work"
    for d in (footage, sidecars, work):
        d.mkdir()
    jobs = [(footage / f"{s}.MP4", h) for s, h in STEMS]
    with ThreadPoolExecutor(len(jobs) + 1) as ex:
        fs = [ex.submit(_make_source, p, h) for p, h in jobs] + [ex.submit(_make_still, footage / "VP_S.MP4")]
        for f in fs:
            f.result()
    for stem in [s for s, _ in STEMS] + ["VP_S"]:
        side = _sidecar(stem)
        side["duration_s"] = CLIP_S
        (sidecars / f"{stem}.audio.json").write_text(json.dumps(side), encoding="utf-8")
    edl_path = root / "edl.json"
    edl_path.write_text(json.dumps(_edl(), indent=1), encoding="utf-8")
    return {"root": root, "footage": footage, "sidecars": sidecars, "work": work, "edl": edl_path}


@pytest.fixture(scope="module")
def vp_server(vp_project):
    import uvicorn
    import server

    p = vp_project
    server.configure(p["edl"], p["footage"], p["sidecars"], p["work"], proxies=False,
                     visual=p["work"] / "no-visual")
    server.ensure_proxies([f"{s}.MP4" for s, _ in STEMS] + ["VP_S.MP4"])
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


def _open(browser, url: str, w: int, h: int):
    pg = browser.new_page(viewport={"width": w, "height": h})
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "shader" in m.text else None)
    pg.add_init_script("try { localStorage.clear(); localStorage.setItem('roughcut.tour', 'seen'); } catch (e) {}")
    pg.goto(url)
    pg.wait_for_selector("#tl .tl-lane[data-lane=V1] .blk")
    pg.wait_for_function(
        "typeof C !== 'undefined' && C && C.shots && segs.length === 5 && window.effects && window.grade && grade.ready",
        timeout=15000)
    return pg, errors


# ------------------------------------------------------------------ the play

RECORD = """() => {
  window.__fl = []; window.__cv = []; window.__on = true;
  const t0 = performance.now();
  window.__t0 = t0;
  player.vids.forEach((v) => {
    const cb = (now, md) => {
      if (!window.__on) return;
      window.__fl.push({ t: now - t0, v: v.id, live: v.classList.contains('live'), mt: md.mediaTime });
      v.requestVideoFrameCallback(cb);
    };
    v.requestVideoFrameCallback(cb);
  });
  const look = (ft) => {
    if (!window.__on) return;
    window.__cv.push({ t: performance.now() - t0, ft: ft - t0, playing: player.playing, draws: grade.draws,
                       on: !!document.querySelector('#gradeCanvas.on'), shot: grade.shot });
    requestAnimationFrame(look);
  };
  requestAnimationFrame(look);
}"""


def play_into(pg, k: int) -> dict:
    """Park 1.0 s before shot k (the effected one), play to 2.0 s into it, and measure the
    graded canvas by the frame clock (asserted), the worker's and the page's (a record).

    Parked the way an editor looks before playing: on the effected shot, then back to the
    play's start (another shot, so the grade re-arms on the live buffer). A first seek slow
    enough to put the poster up (NOFRAME_MS) left the grade's frame callback dead until the
    next cut, as `showLive` skips setShot for the shot already shown — no graded frame for
    the play's first second whatever the shot: lane PERF7's (wave 16 `8c0571b`, grade.js
    `rides`), not this test's subject."""
    parked = ("liveVideo() && liveVideo().readyState >= 3 && !liveVideo().seeking"
              " && (() => { const b = upcomingVideo(1); return b && b.readyState >= 2 && !b.seeking; })()")
    pg.evaluate(f"cueAt({k}, segs[{k}].in + 0.5)")
    pg.wait_for_function("liveVideo() && liveVideo().readyState >= 3 && !liveVideo().seeking", timeout=20000)
    pg.wait_for_timeout(400)
    pg.evaluate(f"cueAt({k - 1}, segs[{k - 1}].out - {PRE_S})")
    pg.wait_for_function(parked, timeout=20000)
    pg.wait_for_timeout(600)
    pg.evaluate(RECORD)
    load = _load()
    pg.keyboard.press("Space")
    pg.wait_for_timeout(int((PRE_S + POST_S) * 1000))
    mid = pg.evaluate("""() => ({ w: document.querySelector('#gradeCanvas').width, shot: grade.shot,
      vfx: grade.adjust && grade.adjust.vfx ? grade.adjust.vfx : null, warm: grade.warmLog,
      filter: getComputedStyle(document.querySelector('#gradeCanvas')).filter })""")
    pg.keyboard.press("Space")
    pg.evaluate("window.__on = false")
    samples = pg.evaluate("window.__cv")
    wk = worker_gaps(pg.evaluate(
        "(() => { const t0 = window.__t0, p = window.__cv.filter((s) => s.playing);"
        " if (!p.length) return null; const a = t0 + p[0].t, b = t0 + p[p.length - 1].t;"
        " const r = (x) => Math.round((x - t0) * 10) / 10;"
        " return { end: b - t0, log: grade.drawLog.filter((d) => d.t >= a && d.t <= b).map((d) =>"
        " ({ t: r(d.t), made: r(d.made), got: r(d.got), start: r(d.start), ms: d.ms, skipped: d.skipped })) }; })()"))
    fc = canvas_gaps([dict(s, t=s["ft"]) for s in samples])
    page_clock = canvas_gaps(samples)
    sid = pg.evaluate(f"segs[{k}].id")
    inside = [s for s in samples if s["shot"] == sid]
    fl = sorted((f for f in pg.evaluate("window.__fl") if f["live"]), key=lambda f: f["t"])
    live_gaps = [b["t"] - a["t"] for a, b in zip(fl, fl[1:])]
    return {"load": round(load, 2), "frame": fc, "page_ms": page_clock.get("longest_ms"),
            "worker": {k2: wk[k2] for k2 in ("longest_ms", "holes_over", "draws", "hole")} if wk else None,
            "in_shot_frames": len(inside), "mid": mid,
            "live_longest_ms": round(max(live_gaps), 1) if live_gaps else None}


def toggle_fx(pg) -> None:
    """Shots 2 and 3's effect bypassed, or back on (Effect Controls' fx toggle, one undo each)."""
    pg.evaluate("[[1, 'sharpen'], [2, 'gaussian_blur']].forEach(([i, id]) => effects.vfxAct('off', id, tl.byId(segs[i].id)))")
    pg.evaluate("flushSave()")
    pg.wait_for_function("document.querySelector('#saveState').dataset.state === 'saved'", timeout=8000)
    pg.wait_for_timeout(300)


def _ps() -> str:
    return subprocess.run("ps -eo ni,pcpu,comm --sort=-pcpu | head -6", shell=True,
                          capture_output=True, text=True).stdout


def test_a_sharpened_and_a_blurred_shot_play_with_no_hold(vp_server):
    """Verdict 12 punch 2's done: a graded shot with Sharpen 60, and one with Gaussian Blur 12,
    play with no hold over 67 ms on the frame clock at 1440 and 1920; the canvas wears no CSS
    filter (the shader draws the effect, at Playback Resolution)."""
    busy = _too_busy()
    if busy:
        pytest.skip(busy)
    facts: dict = {"load_pre": round(_load(), 2), "ps": _ps()}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        try:
            with Burners(_burners() if os.environ.get("ROUGHCUT_PERF_BURNERS") else 0) as b:
                facts["burners"] = b.n
                for (w, h) in ((1440, 900), (1920, 1080)):
                    pg, errors = _open(browser, vp_server, w, h)
                    # the effects, then the same plays with them bypassed (fx off: the record's
                    # control, verdict 12's "normal with it off"), then on again
                    for off in (False, True):
                        if off:
                            toggle_fx(pg)
                        for name, k in (("sharpen", 1), ("blur", 2)):
                            r = play_into(pg, k)
                            key = f"{name}{'_off' if off else ''}_{w}"
                            facts[key] = r
                            print(f"\nVFXPLAY {key}: frame {r['frame'].get('longest_ms')} ms "
                                  f"({r['frame'].get('holes_over')} over) · worker {(r['worker'] or {}).get('longest_ms')}"
                                  f" · page {r['page_ms']} · live {r['live_longest_ms']} · load {r['load']}"
                                  f" · canvas {r['mid']['w']} px · filter {r['mid']['filter']}", flush=True)
                            pg.evaluate("pauseCut()")
                    toggle_fx(pg)
                    assert errors == [], errors
                    pg.close()
            facts["load_end"] = round(_load(), 2)
            facts["ps_end"] = _ps()
            print("\nVFXPLAY", json.dumps(facts), flush=True)
            out = os.environ.get("ROUGHCUT_VFXPLAY_LOG")
            if out:
                Path(out).write_text(json.dumps(facts, indent=1), encoding="utf-8")
        finally:
            browser.close()
    for key in ("sharpen_1440", "blur_1440", "sharpen_1920", "blur_1920"):
        r = facts[key]
        kind = "sharpen" if key.startswith("sharpen") else "gaussian_blur"
        assert r["frame"]["on"], f"the grade canvas went off while the graded shots played ({key}): {r}"
        assert r["in_shot_frames"] > 60, f"the play did not reach the effected shot ({key}): {r}"
        # the shader draws the effect: no CSS / SVG filter on the canvas, the stage in its adjust
        assert r["mid"]["filter"] == "none", r["mid"]
        assert r["mid"]["vfx"] and r["mid"]["vfx"][0]["k"] == ("sharp" if kind == "sharpen" else "blur"), r["mid"]
        assert r["mid"]["w"] <= 640, f"not at Playback Resolution while playing ({key}): {r['mid']}"
        fc = r["frame"]
        assert fc.get("longest_ms") is not None and fc["longest_ms"] <= GAP_LIMIT_MS, \
            f"the {kind} shot held the graded canvas by the frame clock ({key}): {r}"


# ------------------------------------------------------------------ the picture

def _sample(pg, w: int) -> np.ndarray:
    """The Program's picture at `w` wide (grade.sample: the renderer's own draw), top row first."""
    s = pg.evaluate(f"(() => {{ const s = grade.sample({w}); return s ? {{ w: s.w, h: s.h, d: Array.from(s.data) }} : null; }})()")
    assert s, "grade.sample drew nothing"
    a = np.asarray(s["d"], np.float64).reshape(s["h"], s["w"], 4)[::-1, :, :3]
    return a


def _park(pg, i: int, at: float = 0.6) -> None:
    pg.evaluate(f"cueAt({i}, segs[{i}].in + {at})")
    pg.wait_for_function(
        f"liveVideo() && liveVideo().readyState >= 2 && !liveVideo().seeking && grade.shot === segs[{i}].id"
        " && grade.lut && grade.probe()", timeout=15000)
    pg.wait_for_timeout(400)


def _set_effect(pg, i: int, entry: dict | None) -> None:
    """Shot i's stack as `entry` (one effect) or none, as the AI's `effect` op lands it."""
    pg.evaluate("""([i, e]) => {
      const s = () => tl.byId(segs[i].id);
      for (const x of effects.vfx(s())) if (!e || x.kind !== e.kind) effects.vfxOp({ kind: x.kind, remove: true }, s());
      if (e) effects.vfxOp({ kind: e.kind, params: e.params }, s());
    }""", [i, entry])
    pg.wait_for_timeout(300)


def luma(a: np.ndarray) -> np.ndarray:
    return 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]


def detail(a: np.ndarray) -> float:
    """The picture's fine detail: the mean |difference| of neighbouring pixels' luma."""
    y = luma(a)
    return float(np.abs(np.diff(y, axis=1)).mean() + np.abs(np.diff(y, axis=0)).mean())


def convolve3(a: np.ndarray, k: list[float]) -> np.ndarray:
    """vfx.py's 3 × 3 kernel, the edge pixels repeated, per channel, 8-bit."""
    q = np.pad(a, ((1, 1), (1, 1), (0, 0)), mode="edge")
    h, w = a.shape[:2]
    out = sum(k[3 * j + i] * q[j:j + h, i:i + w] for j in range(3) for i in range(3))
    return np.clip(np.floor(out + 0.5), 0, 255)


def gaussian(a: np.ndarray, sigma: float) -> np.ndarray:
    """THE blur: a Gaussian of `sigma` px, black outside the frame (assemble.py's pad)."""
    r = int(np.ceil(3.0 * sigma)) + 1
    x = np.arange(-r, r + 1, dtype=np.float64)
    g = np.exp(-0.5 * (x / sigma) ** 2)
    g /= g.sum()
    p = np.pad(a, ((r, r), (r, r), (0, 0)))
    h, w = a.shape[:2]
    t = sum(g[i] * p[:, i:i + w] for i in range(2 * r + 1))
    return sum(g[j] * t[j:j + h] for j in range(2 * r + 1))


PATCHES = {"tl": (0, 0, .12, .15), "tr": (.88, 0, 1, .15), "centre": (.4, .4, .6, .6),
           "bars": (.1, .55, .9, .7), "left": (0, .4, .08, .6)}


def patches(a: np.ndarray) -> dict:
    y = luma(a)
    hh, ww = y.shape
    return {k: float(y[int(y0 * hh):int(np.ceil(y1 * hh)), int(x0 * ww):int(np.ceil(x1 * ww))].mean())
            for k, (x0, y0, x1, y1) in PATCHES.items()}


def render(vp_project, live: str, seg: dict, name: str) -> Path:
    """One shot of the cut exactly as the board saved it, through assemble.py as the board renders."""
    import httpx
    edl = httpx.get(f"{live}/api/project").json()
    edl["segments"] = [seg]
    edl.pop("transitions", None)
    path = vp_project["root"] / f"{name}.json"
    path.write_text(json.dumps(edl), encoding="utf-8")
    out = vp_project["root"] / f"{name}.mp4"
    r = subprocess.run(["nice", "-n", "19", "uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(path),
                        "--footage", str(vp_project["footage"]), "--profile", "match", "-o", str(out)],
                       capture_output=True, text=True, env={**os.environ, "ROUGHCUT_GPU": "0"})
    assert r.returncode == 0, r.stdout + r.stderr
    return out


def export_frame(path: Path, n: int) -> np.ndarray:
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
                        "-vf", f"select=eq(n\\,{n}),scale=in_range=tv:out_range=pc:in_color_matrix=bt709",
                        "-frames:v", "1", "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).astype(np.float64).reshape(SRC_H, SRC_W, 3)


def screen(pg) -> np.ndarray:
    """The Program as shown: a screenshot of the monitor (the picture's own 16:9 box)."""
    png = pg.locator("#player .screen").screenshot()
    box = pg.locator("#player .screen").bounding_box()
    w, h = int(round(box["width"])), int(round(box["height"]))
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-f", "png_pipe", "-i", "-",
                        "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       input=png, capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).astype(np.float64).reshape(h, w, 3)


def mid(a: np.ndarray) -> np.ndarray:
    """The picture's middle: clear of its edges and of the monitor's "ungraded" tag."""
    h, w = a.shape[:2]
    return a[int(0.15 * h):int(0.85 * h), int(0.1 * w):int(0.9 * w)]


def formula(name: str, a: np.ndarray) -> np.ndarray:
    """THE formula of case `name` on picture `a`, on its own pixel grid (σ scales with its width)."""
    if name == "sharpen":
        return convolve3(a, vfx.sharpen_kernel(SHARP))
    return gaussian(a, BLUR * a.shape[1] / 1920)


CASES = [("sharpen", {"id": "sharpen", "kind": "sharpen", "params": {"amount": SHARP}}),
         ("blur", {"id": "gaussian_blur", "kind": "gaussian_blur", "params": {"blurriness": BLUR}})]


def test_the_programs_effects_are_the_formula_the_export_and_the_parked_picture(vp_server, vp_project):
    """Parked on the still: the shader's Sharpen 60 / Gaussian Blur 12 at the frame's own size
    are THE formulas on the same picture without them; the parked monitor is THE formula on the
    monitor's own grid (the grid the CSS path it replaces drew on, measured beside it); the
    export of the saved shot does to the picture what the Program does."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            pg, errors = _open(browser, vp_server, 1440, 900)
            i = 4
            _set_effect(pg, i, None)
            _park(pg, i)
            plain = _sample(pg, SRC_W)
            plain_screen = screen(pg)
            pg.evaluate("grade.enabled = false")
            pg.wait_for_timeout(400)
            plain_css = screen(pg)
            pg.evaluate("grade.enabled = true")
            facts: dict = {}
            for name, entry in CASES:
                _set_effect(pg, i, entry)
                _park(pg, i)
                got = _sample(pg, SRC_W)
                err = np.abs(got - formula(name, plain))
                # the parked monitor: the shader's picture (grade on), and beside it the CSS
                # path it replaces (grade off: the raw video under the CSS filter)
                shown = screen(pg)
                assert pg.evaluate("getComputedStyle(document.querySelector('#gradeCanvas')).filter") == "none"
                pg.evaluate("grade.enabled = false")
                pg.wait_for_timeout(400)
                css = screen(pg)
                vf = pg.evaluate("getComputedStyle(liveVideo()).filter")
                pg.evaluate("grade.enabled = true")
                pg.wait_for_timeout(300)
                f = {"formula_mean": round(float(err.mean()), 3), "formula_worst": round(float(err.max()), 1),
                     "program_detail_x": round(detail(got) / detail(plain), 3),
                     "parked_mean": round(float(np.abs(mid(shown) - mid(formula(name, plain_screen))).mean()), 3),
                     "css_mean": round(float(np.abs(mid(css) - mid(formula(name, plain_css))).mean()), 3),
                     "monitor_w": shown.shape[1], "video_filter": vf}
                # the saved shot, exported
                pg.evaluate("flushSave()")
                pg.wait_for_function("document.querySelector('#saveState').dataset.state === 'saved'", timeout=8000)
                seg = pg.evaluate(f"JSON.parse(JSON.stringify(segs[{i}]))")
                assert [e["kind"] for e in (seg.get("controls") or {}).get("vfx") or []] == [entry["kind"]], seg
                film = render(vp_project, vp_server, seg, f"vp-{name}")
                base = render(vp_project, vp_server, {**seg, "controls": None}, f"vp-{name}-plain")
                ex, ex0 = export_frame(film, 36), export_frame(base, 36)
                f["export_detail_x"] = round(detail(ex) / detail(ex0), 3)
                # (in luma: the export works on Y'CbCr planes, its chroma on the 4:2:0 grid)
                f["export_formula_mean"] = round(float(np.abs(luma(mid(ex)) - luma(mid(formula(name, ex0)))).mean()), 3)
                # what the effect does to each patch, in the Program and in the export (the
                # grade's own Program-vs-export difference is the same with and without it)
                pp, pe, pp0, pe0 = patches(got), patches(ex), patches(plain), patches(ex0)
                f["patches"] = {k: [round(pp[k] - pp0[k], 1), round(pe[k] - pe0[k], 1)] for k in pp}
                facts[name] = f
                print(f"\nVFXPLAY parity {name}: {json.dumps(f)}", flush=True)
                tol = 1.0 if name == "sharpen" else 1.5
                assert f["formula_mean"] < tol, (name, f)
                assert f["parked_mean"] < tol + 0.5, (name, f)
                assert f["export_formula_mean"] < 2.0, (name, f)
                if name == "sharpen":
                    assert abs(f["export_detail_x"] - f["program_detail_x"]) <= 0.1 * f["program_detail_x"], (name, f)
                for k in pp:
                    assert abs((pe[k] - pe0[k]) - (pp[k] - pp0[k])) <= 0.05 * max(pp[k], 20.0), (name, k, f)
                assert "url(" in vf or "blur(" in vf, vf          # the fallback is still the CSS one
            assert errors == [], errors
        finally:
            browser.close()
