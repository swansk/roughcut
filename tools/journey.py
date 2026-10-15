#!/usr/bin/env python3
"""The journey (REDESIGN-GOAL dimension 12): one scripted editing session, end to end.

    python3 tools/journey.py <snapshot dir> <out dir>

Real Chromium against the board of `<snapshot dir>` (a `git archive` of the rev under
judgement, as tools/gate.py makes it), on the suite's synthetic fixture (three 6-second
24 fps clips with fabricated sidecars, built fresh under `<out dir>/fixture` by the
snapshot's own app/tests/conftest.py) plus one clip in the shape of Killington's GoPro
footage, built here: CLIP_R, HEVC at 59.94 fps, stored upside down with a −180° display matrix
(`ffmpeg -display_rotation 180`), its picture four coloured quadrants so a turn, a flip
or a mirror each read wrong. A fresh `--work` under `<out dir>/work`. The model is
scripted (no real model call, ever: the ledger is checked afterwards) and the index's
tools are the suite's stubs (app/tests/test_index._stub_tools).

The steps, each one a screenshot at 1440×900 in `<out dir>/NN-step.png`:

  01 open           the board lands; the bin holds the fixture; "Run first pass" in the header
  02 preview        CLIP_R opened in the Source monitor before any index ran: the server's
                    360p preview plays (an HEVC original is never handed to the browser),
                    each quadrant its colour, the right way up (verdict 3 punch 1's picture)
  03 firstpass      one click on [data-testid=firstpass-button] opens its panel (steps, cost)
  04 run            [data-testid=firstpass-run] → the index (stubbed) → picks → the proposal opens
  05 reject         ✗ on the proposal's 5th shot (its ghost's `.rv.no`): Accept reads "Accept 4 of 5"
  06 accept         Accept puts the 4 accepted shots on V1 and on disk; the EDL's
                    provenance.rejected records the 5th shot's range (verdict 4, punch 2)
  07 selects        Selects lists the pass's picks (> 0 rows, one per pick); an amber marker
                    on the cut wherever a pick overlaps a shot (verdict 4, punch 1)
  08 trim-out       a ripple trim of shot 1's out, dragged on the cut's edge column
  09 trim-in        a ripple trim of shot 3's in, the same way
  10 razor          the playhead into shot 2 by a click on the ruler, then C: two pieces
  11 move           shot 4 dragged by its body to the head of the film
  12 warmer         Effect Controls › Lumetri Color › Temperature on a trimmed shot
  13 rerun-panel    the header reads "Re-run first pass"; its panel names both trimmed shots as kept
  14 rerun-accept   Run (the model re-proposes the rejected range): the proposal leaves it out,
                    says so in plan.firstpass.rejected and the notes; accept: every kept shot
                    (the trimmed two among them) has the same in/out
  15 drop-grade     CLIP_R dragged from the Project panel to the film's end, and within 100 ms
                    of it landing `effects.set('temperature', 40)` on the shot Effect Controls
                    shows; idle, reload: the shot is on disk under a server id with the warm
                    balance, colour.shots has no `tmp-` key, Effect Controls shows 40
                    (verdict 5, punch 1: the grade judge 5 lost)
  16 upright        CLIP_R in the Program monitor (its graded frame, as shown) and in the
                    Source monitor: each quadrant is its colour, the right way up
  17 short-shot     a 0.4 s range of CLIP_R marked I / O in the Source monitor and inserted
                    with `,` at the first cut; ▶ from 0.6 s before it plays through it into
                    the next shot
  18 export         Ctrl+M → Quick 1080p, the whole sequence; the size estimate settles
                    (no "measuring…"); export-go, export-progress → 100, export-done
  19 ffprobe        the file's duration is within one frame of tl.total(); its size within
                    ±25 % of the dialog's estimate; CLIP_R's frame in it is upright

After the browser closes, the review copy the board started for the export is waited
for (not killed half-made, which printed "!! review copy failed"); a copy that failed
fails the journey.

The first failing step stops the journey. Zero page errors are allowed: an error thrown
in the page fails the step it happened in. `<out dir>/journey.json` has each step's name,
ok, ms, PNG, why it failed and the page errors; the last line printed is the gate's:
`journey ✓ 19 steps · 74 s` or `journey ✗ step 7 razor: <why>`. Exit 0 only when green.

The script needs fastapi, uvicorn, httpx, numpy, pytest and playwright; when the Python
running it lacks them it re-runs itself under `uv run --with …` (as the gate's suite does).
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

DEPS = ("pytest", "fastapi", "uvicorn", "httpx", "numpy", "playwright")
W, H = 1440, 900


def _reexec_under_uv() -> None:
    missing = [m for m in DEPS if importlib.util.find_spec(m) is None]
    if not missing:
        return
    if os.environ.get("ROUGHCUT_JOURNEY_UV"):
        sys.exit(f"journey ✗ step 0 setup: missing {', '.join(missing)} even under uv")
    cmd = ["uv", "run", "--with", "pytest", "--with", "fastapi", "--with", "uvicorn",
           "--with", "httpx", "--with", "numpy", "--with", "playwright", "python",
           str(Path(__file__).resolve()), *sys.argv[1:]]
    os.environ["ROUGHCUT_JOURNEY_UV"] = "1"
    os.execvp(cmd[0], cmd)


class StepFailed(Exception):
    pass


def need(cond, why: str) -> None:
    if not cond:
        raise StepFailed(why)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _descendants(root: int) -> list[int]:
    """Every live process below `root` (by /proc parent links): the ffmpegs the server
    starts for renders, review copies and posters. Only ours, never anyone else's."""
    kids: dict[int, list[int]] = {}
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            stat = (d / "stat").read_text()
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        kids.setdefault(ppid, []).append(int(d.name))
    out, todo = [], [root]
    while todo:
        for c in kids.get(todo.pop(), []):
            out.append(c)
            todo.append(c)
    return out


def _stop_children() -> list[int]:
    pids = _descendants(os.getpid())
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for p in pids:
            try:
                os.kill(p, sig)
            except ProcessLookupError:
                pass
        if sig == signal.SIGTERM and pids:
            time.sleep(0.5)
    return pids


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ the GoPro-shaped clip

ROT = "CLIP_R.MP4"
CLIPS = ["CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4", ROT]
ROT_S = 6.0
# The picture as it must be SEEN, by quadrant. No two alike, so an upside-down picture
# (the bug ORIENT fixed), a mirror and a vertical flip each read wrong.
QUADS = {"tl": (255, 0, 0), "tr": (0, 255, 0), "bl": (0, 0, 255), "br": (255, 255, 0)}


def make_rotated_clip(path: Path, scratch: Path) -> dict:
    """CLIP_R as a GoPro mounted upside down writes it: HEVC at 59.94 fps, the pixels
    stored turned 180°, and a display matrix of −180° that says to turn them back. HEVC,
    as Killington's 59.94 clips are, so the board cannot hand the browser the original
    (it turns a tagged h264 file itself) and the picture goes through the server's
    preview, where the turn was lost before ORIENT (verdict 3, punch 1). Returns what
    ffprobe reads off it (the fixture is checked, so a quiet ffmpeg change cannot make
    the upright step vacuous)."""
    scratch.mkdir(parents=True, exist_ok=True)
    stored = scratch / "CLIP_R.stored.mp4"
    w, h = 320, 180
    boxes = ",".join(
        f"drawbox=x={x}:y={y}:w={w // 2}:h={h // 2}:c={c}:t=fill"
        for x, y, c in ((0, 0, "red"), (w // 2, 0, "lime"), (0, h // 2, "blue"),
                        (w // 2, h // 2, "yellow")))
    src = f"color=c=black:s={w}x{h}:r=60000/1001:d={ROT_S},{boxes},hflip,vflip"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi", "-i", src,
                    "-f", "lavfi", "-i", f"sine=frequency=660:duration={ROT_S}",
                    "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "log-level=error",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(stored)],
                   check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-display_rotation", "180",
                    "-i", str(stored), "-c", "copy", str(path)], check=True, capture_output=True)
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=codec_name,r_frame_rate:stream_side_data=rotation", "-of", "json", str(path)],
                       capture_output=True, text=True, check=True)
    v = json.loads(r.stdout)["streams"][0]
    rot = next((sd.get("rotation") for sd in v.get("side_data_list") or [] if "rotation" in sd), None)
    if (v.get("codec_name") != "hevc" or v.get("r_frame_rate") != "60000/1001"
            or rot is None or abs(abs(float(rot)) - 180) > 0.5):
        raise RuntimeError(f"the rotated fixture came out as {v}")
    return {"codec": v["codec_name"], "r_frame_rate": v["r_frame_rate"], "rotation": rot}


def quadrants(rgb, why: str) -> dict:
    """Which colour each quadrant of a picture shows (an H×W×3 array; black bars around
    the picture are cropped off first). Fails the step unless every quadrant is the
    colour QUADS says, by a clear margin."""
    import numpy as np
    a = np.asarray(rgb, dtype=float)
    lit = a.max(axis=2) > 60
    rows = np.where(lit.mean(axis=1) > 0.5)[0]
    cols = np.where(lit.mean(axis=0) > 0.5)[0]
    need(len(rows) > 16 and len(cols) > 16, f"{why}: no picture to read (all dark)")
    y0, y1, x0, x1 = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
    ph, pw = y1 - y0, x1 - x0
    seen, wrong = {}, []
    for key, (qy, qx) in {"tl": (0, 0), "tr": (0, 1), "bl": (1, 0), "br": (1, 1)}.items():
        # the middle half of the quadrant: clear of its edges and of any overlay text
        ys, ye = y0 + int(ph * (0.5 * qy + 0.125)), y0 + int(ph * (0.5 * qy + 0.375))
        xs, xe = x0 + int(pw * (0.5 * qx + 0.125)), x0 + int(pw * (0.5 * qx + 0.375))
        m = a[ys:ye, xs:xe].reshape(-1, 3).mean(axis=0)
        dist = {k: float(np.linalg.norm(m - np.array(c))) for k, c in QUADS.items()}
        best = min(dist, key=dist.get)
        seen[key] = {"rgb": [round(float(x)) for x in m], "looks": best}
        if best != key or dist[best] > 150:
            wrong.append(f"{key} looks {best} {seen[key]['rgb']}")
    need(not wrong, f"{why} is not upright: " + "; ".join(wrong))
    return seen


def png_rgb(png: bytes):
    """A PNG (a Playwright screenshot) as an H×W×3 uint8 array, decoded by ffmpeg."""
    import struct
    import numpy as np
    w, h = struct.unpack(">II", png[16:24])
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-f", "png_pipe", "-i", "-",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       input=png, capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8)[: w * h * 3].reshape(h, w, 3)


def file_frame_rgb(path: Path, t: float):
    """The frame of a file at `t` seconds, displayed (ffmpeg applies the matrix)."""
    import numpy as np
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height", "-of", "json", str(path)],
                       capture_output=True, text=True, check=True)
    s = json.loads(r.stdout)["streams"][0]
    w, h = int(s["width"]), int(s["height"])
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{t:.3f}", "-i", str(path),
                        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8)[: w * h * 3].reshape(h, w, 3)


# A Project row dragged to the film's end on V1 (HTML5 drag events, as Chromium sends
# them), then — the moment the shot is in the sequence — Effect Controls' own setter on
# the shot it shows: the judge's "drop a clip, warm it" in one breath (verdict 5, dim 7).
DROP_THEN_GRADE = """async ([clip, value]) => {
  const row = [...document.querySelectorAll('#projList tr[data-clip], #projList .picon[data-clip]')]
    .find((r) => r.dataset.clip === clip);
  if (!row) return { error: `no Project row for ${clip}` };
  const before = new Set(segs.map((s) => String(s.id)));
  const n0 = segs.length;
  const dt = new DataTransfer();
  row.dispatchEvent(new DragEvent('dragstart', { bubbles: true, cancelable: true, dataTransfer: dt }));
  const view = tl.el.view, lane = tl.el.lanes.V1;
  const r = view.getBoundingClientRect(), lr = lane.getBoundingClientRect();
  const x = r.left + tl.timeToX(tl.total()) - view.scrollLeft + 2;
  const y = lr.top + lr.height / 2;
  const t0 = performance.now();
  for (const type of ['dragenter', 'dragover', 'drop']) {
    lane.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, clientX: x,
                                             clientY: y, dataTransfer: dt }));
  }
  row.dispatchEvent(new DragEvent('dragend', { bubbles: true, dataTransfer: dt }));
  while (segs.length === n0 && performance.now() - t0 < 3000) {
    await new Promise((res) => setTimeout(res, 2));
  }
  const landed = performance.now() - t0;
  const fresh = segs.filter((s) => !before.has(String(s.id)));
  const shown = typeof inspected === 'function' && inspected() ? String(inspected().id) : null;
  const ok = effects.set('temperature', value);
  const set = performance.now() - t0;
  return { n0, n: segs.length, landed: Math.round(landed), set: Math.round(set), ok, shown,
           fresh: fresh.map((s) => ({ id: String(s.id), clip: s.clip, in: s.in, out: s.out })) };
}"""


# ------------------------------------------------------------------ the scripted model

FIRST_CUT = [
    {"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0, "why": "the greeting"},
    {"clip": "CLIP_B.MP4", "in": 2.4, "out": 4.0, "why": "the reply"},
    {"clip": "CLIP_C.MP4", "in": 0.5, "out": 4.0, "why": "the walk"},
    {"clip": "CLIP_B.MP4", "in": 4.4, "out": 5.6, "why": "goodbye"},
    # the shot the editor rejects in the review (step 04); last, so the cut the later
    # steps trim, razor and move is the same four shots
    {"clip": "CLIP_C.MP4", "in": 4.4, "out": 5.6, "why": "the wave"},
]
REJECTED = FIRST_CUT[-1]
# The re-run's answer tampers with the editor's shots on purpose (the server must put
# each locked one back exactly), offers one new shot, and proposes the rejected range
# again — the server must leave it out (verdict 4, punch 2), or the check is vacuous.
RE_CUT = [
    {"clip": "CLIP_A.MP4", "in": 0.4, "out": 2.2, "why": "the greeting, longer"},
    {"clip": "CLIP_C.MP4", "in": 0.2, "out": 3.0, "why": "the walk, earlier"},
    {"clip": "CLIP_A.MP4", "in": 4.6, "out": 5.8, "why": "a new ending"},
    {"clip": "CLIP_C.MP4", "in": 4.4, "out": 5.6, "why": "the wave, again"},
]


def scripted_model(inference, config):
    class JourneyModel:
        """Answers the cut call of a run and of a re-run (a re-run's prompt says LOCKED
        when shots are locked); every other call gets "{}" and falls back."""
        name = "journey-scripted"

        def __init__(self):
            self.calls: list[str] = []

        def complete(self, request):
            prompt = request.prompt or ""
            if "segments" not in (request.schema or {}):
                text, kind = "{}", "other"
            elif "LOCKED" in prompt:
                text, kind = json.dumps({"segments": RE_CUT, "notes": "a re-cut around your shots"}), "rerun"
            else:
                text, kind = json.dumps({"segments": FIRST_CUT, "notes": "a short conversation"}), "run"
            self.calls.append(kind)
            model = config.model_for(request.role)
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend=self.name, model=model, projected_usd=1e-4,
                                    latency_ms=1, raw=text)

    return JourneyModel()


# ------------------------------------------------------------------ the session

class Journey:
    def __init__(self, snap: Path, out: Path):
        self.snap, self.out = snap.resolve(), out.resolve()
        self.steps: list[dict] = []
        self.errors: list[str] = []          # page errors, all of them
        self.console: list[str] = []         # console errors (recorded, not judged)
        self.facts: dict = {}
        self.media: list[tuple[str, str | None]] = []
        self.pg = None
        self.base = ""

    # ---- a step: time it, screenshot it, fail it on an exception or a page error
    def step(self, name: str, fn) -> bool:
        n = len(self.steps) + 1
        png = self.out / f"{n:02d}-{name}.png"
        seen = len(self.errors)
        t0 = time.time()
        rec = {"n": n, "name": name, "ok": False, "ms": 0, "png": png.name, "why": "",
               "errors": []}
        try:
            fn()
            rec["ok"] = True
        except StepFailed as e:
            rec["why"] = str(e)
        except Exception as e:                      # a timeout, a missing element …
            rec["why"] = f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"
            rec["trace"] = traceback.format_exc(limit=4)
        rec["ms"] = round((time.time() - t0) * 1000)
        if self.pg is not None:
            try:
                self.pg.wait_for_timeout(150)       # let the last paint land
            except Exception:
                pass
            new = self.errors[seen:]
            rec["errors"] = new
            if new and rec["ok"]:
                rec["ok"], rec["why"] = False, f"page error: {new[0][:200]}"
            try:
                self.pg.screenshot(path=str(png))
            except Exception as e:
                rec["png"] = None
                rec["why"] = rec["why"] or f"no screenshot: {e}"
                rec["ok"] = False
        self.steps.append(rec)
        mark = "✓" if rec["ok"] else "✗"
        print(f"  {n:02d} {name:<13} {mark} {rec['ms']:>6} ms  {rec['why']}", flush=True)
        return rec["ok"]

    # ---- the page's own state
    def js(self, expr: str, arg=None):
        return self.pg.evaluate(expr, arg) if arg is not None else self.pg.evaluate(expr)

    def until(self, expr: str, why: str, timeout=10000) -> None:
        """Wait for a page condition; on a timeout fail the step with `why`."""
        try:
            self.pg.wait_for_function(expr, timeout=timeout)
        except Exception as e:
            if "Timeout" not in type(e).__name__ + str(e):
                raise
            raise StepFailed(f"{why} (waited {timeout / 1000:.0f} s)")

    def sel(self, selector: str, why: str, timeout=10000, state="visible") -> None:
        try:
            self.pg.wait_for_selector(selector, state=state, timeout=timeout)
        except Exception as e:
            if "Timeout" not in type(e).__name__ + str(e):
                raise
            raise StepFailed(f"{why} (waited {timeout / 1000:.0f} s for {selector})")

    def segs(self) -> list[dict]:
        return self.js("segs.map(s => ({id: s.id, clip: s.clip, in: s.in, out: s.out}))")

    def disk(self) -> dict:
        import httpx
        return httpx.get(self.base + "/api/project", timeout=30).json()

    def wait_edl(self, cond, why: str, timeout=15.0) -> dict:
        """Wait for the EDL file itself (what the server writes, provenance and all)."""
        import server
        deadline = time.time() + timeout
        d = {}
        while time.time() < deadline:
            try:
                d = json.loads(Path(server.STATE["edl"]).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                d = {}
            if cond(d):
                return d
            time.sleep(0.2)
        raise StepFailed(why)

    def fp_state(self) -> dict:
        import httpx
        return httpx.get(self.base + "/api/firstpass", timeout=30).json()

    def wait_saved(self, timeout=15000) -> None:
        self.until("document.querySelector('#saveState') && "
                   "document.querySelector('#saveState').dataset.state === 'saved'",
                   "the board never said the edit was saved", timeout)

    def idle(self, quiet_ms=800, timeout=20000) -> None:
        """The board says saved, no save is waiting or in flight, and it stays so."""
        self.wait_saved(timeout)
        expr = ("(typeof saveTimer === 'undefined' || saveTimer === null)"
                " && (typeof saveRun === 'undefined' || saveRun === null)"
                " && document.querySelector('#saveState').dataset.state === 'saved'")
        self.until(expr, "the board never went idle (a save kept waiting)", timeout)
        self.pg.wait_for_timeout(quiet_ms)
        self.until(expr, "the board saved again after it said saved", timeout)

    def loaded(self) -> None:
        """The board after a (re)load: its state, the bin, the cut on V1."""
        self.until("window.rcFirstPass && rcFirstPass.state() && window.effects && window.tl && tl.state",
                   "the board never loaded after a reload", 30000)
        self.until(f"document.querySelectorAll('#projList [data-clip]').length === {len(CLIPS)}",
                   "the Project panel never listed the clips after a reload", 20000)
        self.until("segs.length > 0 && document.querySelectorAll('#tl .tl-lane[data-lane=V1] .blk').length === segs.length",
                   "V1 never drew the cut after a reload", 20000)

    def source_picture(self, why: str) -> dict:
        """Open CLIP_R in the Source monitor from the Project panel and read its picture."""
        pg = self.pg
        pg.locator(".ws-tab[data-tool=project]").click()      # Selects may be in front
        pg.locator("#projList tr.row", has_text=ROT.split(".")[0]).first.click()
        self.until(f"window.rcSource && rcSource.clip === {json.dumps(ROT)}",
                   f"a click on {ROT}'s row did not open it in Source", 5000)
        self.until("(() => { const v = document.querySelector('#srcVideo');"
                   " return v.readyState >= 2 && !v.seeking"
                   f" && v.currentSrc.includes({json.dumps(ROT.split('.')[0])}); }})()",
                   f"the Source monitor never showed {ROT}'s frame", 120000)
        pg.wait_for_timeout(300)
        return quadrants(png_rgb(pg.locator("#srcScreen").screenshot()), why)

    def media_kinds(self) -> list[str]:
        """What the server said it served for CLIP_R so far (x-roughcut-media)."""
        return sorted({k for u, k in self.media if ROT.split(".")[0] in u and k})

    def wait_disk(self, cond, why: str, timeout=30.0) -> dict:
        deadline = time.time() + timeout
        d = {}
        while time.time() < deadline:
            d = self.disk()
            if cond(d):
                return d
            time.sleep(0.2)
        raise StepFailed(f"{why} (on disk: {[(s.get('clip'), s.get('in'), s.get('out')) for s in d.get('segments', [])]})")

    def fp_label(self, text: str, timeout=60000) -> None:
        try:
            self.pg.wait_for_function(
                "(t) => { const b = document.querySelector('[data-testid=\"firstpass-button\"]');"
                " return b && b.innerText.trim() === t; }", arg=text, timeout=timeout)
        except Exception:
            got = self.js("(() => { const b = document.querySelector('[data-testid=\"firstpass-button\"]');"
                          " return b ? b.innerText.trim() : null; })()")
            raise StepFailed(f'the header control reads {got!r}, not "{text}"')

    def open_fp_panel(self) -> str:
        btn = self.pg.locator('[data-testid="firstpass-button"]')
        need(btn.is_visible(), "no visible first-pass control in the header")
        btn.click()
        self.sel('[data-testid="firstpass-panel"]', "one click on the first-pass control opened no panel", 5000)
        self.until("document.querySelector('[data-testid=\"firstpass-panel\"] .fp-steps li') !== null",
                   "the first-pass panel lists no steps", 15000)
        return self.pg.locator('[data-testid="firstpass-panel"]').inner_text()

    def accept_proposal(self, timeout=120000) -> list[dict]:
        try:
            self.pg.wait_for_selector("#proposal", state="visible", timeout=timeout)
        except Exception:
            s = self.fp_state()
            raise StepFailed(f"no proposal opened (first pass {s.get('state')}"
                             f" · stage {s.get('stage')} · {str(s.get('error') or '')[:120]})")
        plan = self.js("pendingPlan ? pendingPlan.segments.map(s => ({id: s.id, clip: s.clip, in: s.in, out: s.out})) : null")
        need(plan, "the proposal is showing but holds no plan")
        # the shots the review rejected (✗ on their ghosts) are not applied
        out = set(self.js("[...document.querySelectorAll('#tl .tl-xlane[data-lane=ghost] .ghost')]"
                          ".map((g, k) => g.classList.contains('rejected') ? k : -1).filter(k => k >= 0)"))
        plan = [p for k, p in enumerate(plan) if k not in out]
        self.pg.locator("#acceptProposal").click()
        want = [(p["clip"], round(p["in"], 3), round(p["out"], 3)) for p in plan]
        self.wait_disk(lambda d: [(s["clip"], round(s["in"], 3), round(s["out"], 3))
                                  for s in d.get("segments", [])] == want,
                       "the accepted proposal never reached the cut on disk")
        return plan

    # ---- pointer helpers (the suite's: real drags in viewport coordinates)
    def zoom(self) -> float:
        return self.js("tl.state.zoom")

    def drag(self, loc, dx: float) -> None:
        loc.scroll_into_view_if_needed()
        box = loc.bounding_box()
        need(box, "the drag handle is not on screen")
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        self.pg.mouse.move(x, y)
        self.pg.mouse.down()
        self.pg.mouse.move(x + dx, y, steps=8)
        self.pg.mouse.up()

    def x_of(self, t: float) -> float:
        return self.js("(t) => { const v = document.querySelector('#tl .tl-view');"
                       " return v.getBoundingClientRect().left + tl.timeToX(t) - v.scrollLeft; }", t)

    # ------------------------------------------------------------------ the steps
    def run(self, pw) -> None:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        self.browser = b
        pg = b.new_page(viewport={"width": W, "height": H})
        self.pg = pg
        pg.on("pageerror", lambda e: self.errors.append(str(e)))
        pg.on("console", lambda m: self.console.append(m.text) if m.type == "error" else None)
        # what the server hands the monitors: original, preview or proxy (its header)
        pg.on("response", lambda r: self.media.append((r.url, r.headers.get("x-roughcut-media")))
              if "/media/" in r.url else None)
        pg.set_default_timeout(30000)
        F = self.facts

        def open_board():
            t0 = time.time()
            pg.goto(self.base + "/")
            self.until("window.rcFirstPass && rcFirstPass.state()",
                       "the board never loaded the first pass's state (rcFirstPass)", 30000)
            self.until(f"document.querySelectorAll('#projList [data-clip]').length === {len(CLIPS)}",
                       f"the Project panel never listed the fixture's {len(CLIPS)} clips", 20000)
            F["open_ms"] = round((time.time() - t0) * 1000)
            clips = self.js("[...document.querySelectorAll('#projList tr[data-clip], #projList .picon[data-clip]')]"
                            ".map(r => r.dataset.clip)")
            need(sorted(set(clips)) == sorted(CLIPS),
                 f"the bin shows {clips}, not the fixture's {CLIPS}")
            need(self.js("segs.length") == 0, "a fresh bin should open with an empty sequence")
            self.fp_label("Run first pass")
            need(pg.locator('header [data-testid="firstpass-button"]').count() == 1,
                 "the first-pass control is not in the header")

        def preview():
            # CLIP_R before the index has made its proxy: the Source monitor plays the
            # server's 360p preview (an HEVC original is not handed to the browser), the
            # picture Killington shows for its first hour and more. It was upside down
            # before ORIENT; it must be the right way up.
            import server
            proxy = Path(server.STATE["proxy_dir"]) / "CLIP_R.mp4"
            need(not proxy.exists(), f"{ROT} has a proxy before any index ran: {proxy}")
            got = self.source_picture(f"{ROT}'s preview in the Source monitor")
            kinds = self.media_kinds()
            need("preview" in kinds, f"the Source monitor did not play {ROT}'s preview "
                                     f"(the server answered {kinds or 'nothing'} for it)")
            F["preview"] = {"quadrants": got, "media": kinds}

        def firstpass_panel():
            text = self.open_fp_panel()
            for word in ("Index", "Picks", "Rough cut", "free on your plan"):
                need(word in text, f'the panel does not say "{word}": {text[:160]!r}')
            run = pg.locator('[data-testid="firstpass-run"]')
            need(run.is_visible() and run.is_enabled(), "the panel's Run button is not usable")
            need(run.inner_text().strip() == "Run first pass",
                 f"the Run button reads {run.inner_text().strip()!r}")

        def run_firstpass():
            t0 = time.time()
            pg.locator('[data-testid="firstpass-run"]').click()
            try:
                pg.wait_for_selector("#proposal", state="visible", timeout=180000)
            except Exception:
                s = self.fp_state()
                raise StepFailed(f"no proposal opened (first pass {s.get('state')} · stage "
                                 f"{s.get('stage')} · {str(s.get('error') or '')[:120]})")
            F["firstpass_s"] = round(time.time() - t0, 1)
            n = self.js("pendingPlan ? pendingPlan.segments.length : 0")
            need(n == len(FIRST_CUT), f"the proposal holds {n} shots, the model proposed {len(FIRST_CUT)}")

        def reject():
            # ✗ on the 5th proposed shot, by its ghost's own review button
            ghosts = pg.locator("#tl .tl-xlane[data-lane=ghost] .ghost")
            need(ghosts.count() == len(FIRST_CUT),
                 f"the ghost lane shows {ghosts.count()} shots, not {len(FIRST_CUT)}")
            k = len(FIRST_CUT) - 1
            ghosts.nth(k).locator("button.rv.no").click()
            self.until(f"document.querySelectorAll('#tl .tl-xlane[data-lane=ghost] .ghost')[{k}]"
                       ".classList.contains('rejected')", "the ✗ did not mark the shot rejected", 5000)
            label = pg.locator("#acceptProposal").inner_text().strip()
            need(label == f"Accept {k} of {k + 1}", f"after one ✗ the button reads {label!r}")
            s = self.js(f"(s => ({{clip: s.clip, in: s.in, out: s.out}}))(pendingPlan.segments[{k}])")
            need(s["clip"] == REJECTED["clip"], f"the 5th proposed shot is {s}, not the model's {REJECTED}")
            F["rejected"] = s

        def accept():
            plan = self.accept_proposal()
            self.until(f"document.querySelectorAll('#tl .tl-lane[data-lane=V1] .blk').length === {len(plan)}",
                       f"V1 does not show the accepted proposal's {len(plan)} shots")
            got = self.segs()
            # the server shapes the model's ranges (padding, sentence ends): the cut on
            # V1 is the proposal as shown, which is the model's clips in the model's order
            need([(s["clip"], s["in"], s["out"]) for s in got]
                 == [(s["clip"], s["in"], s["out"]) for s in plan],
                 f"the accepted cut is not the proposal shown: {got} vs {plan}")
            need([s["clip"] for s in got] == [s["clip"] for s in FIRST_CUT[:-1]],
                 f"the accepted cut is not the model's: {[s['clip'] for s in got]}")
            need(all(s["id"] for s in got), "an accepted shot has no id")
            need(not any(s["clip"] == F["rejected"]["clip"] and s["in"] == F["rejected"]["in"]
                         for s in got), f"the rejected shot {F['rejected']} was applied")
            F["accepted"] = got
            self.wait_saved()
            # the rejection is remembered on the cut (provenance.rejected), for the re-run
            r = F["rejected"]
            edl = self.wait_edl(lambda e: any(
                x.get("clip") == r["clip"] and abs(x.get("in", -1) - r["in"]) < 0.01
                and abs(x.get("out", -1) - r["out"]) < 0.01
                for x in ((e.get("provenance") or {}).get("rejected") or [])),
                f"the EDL's provenance.rejected never recorded {r['clip']} {r['in']}–{r['out']}")
            F["provenance_rejected"] = (edl.get("provenance") or {}).get("rejected")
            # the edge columns over the cuts are what the trims drag
            self.sel("#tl .tl-edge .q.t.l", "no edge columns over the cuts to trim with", state="attached")
            pg.evaluate("Promise.all(segs.map(s => tl.snapsFor(s.clip)))")

        def selects():
            # the first pass's picks are editor objects: rows in Selects, markers on the cut
            import httpx
            d = httpx.get(self.base + "/api/picks?order=clip", timeout=30).json()
            need(d.get("firstpass_picked"), "/api/picks does not know the first pass made its picks")
            picks = [p for p in d["picks"] if p.get("verdict") not in ("reject", "pick")]
            need(len(picks) > 0, "the first pass made no picks to list")
            pg.evaluate("dock.open('bin')")
            pg.locator("#binTabs .tab[data-tab=all]").click()
            try:
                self.until(f"document.querySelectorAll('#library tr.keep.pick').length === {len(picks)}",
                           f"Selects does not list the pass's {len(picks)} picks", 15000)
            except StepFailed as e:
                # say which: the board's own copy of /api/picks is what Selects draws
                held = self.js("Array.isArray(passPicks) ? passPicks.length : passPicks")
                age = self.js("Math.round(Date.now() - picksRead)")
                raise StepFailed(f"{e}; the board holds {held} picks, read {age} ms ago, while "
                                 f"/api/picks has {len(picks)} with firstpass_picked true (a read "
                                 "made before the pass made its picks is kept: nothing re-reads "
                                 "them when the pass lands)")
            n = self.js("document.querySelectorAll('#library tr.keep.pick').length")
            need(n > 0, "Selects shows none of the pass's picks")
            F["selects_picks"] = n
            want = sum(1 for s in self.segs() for p in picks
                       if p["clip"] == s["clip"] and p["end"] > s["in"] and p["start"] < s["out"])
            self.until(f"document.querySelectorAll('#tl .tl-pick').length === {want}",
                       f"the cut does not carry one marker per pick over a shot ({want})")
            F["pick_markers"] = want
            # the step's screenshot is Selects with the picks; the steps after are the
            # timeline's and the other panels', and do not care which tab Project shows

        def trim(i: int, side: str, dt: float):
            def go():
                before = self.segs()
                z = self.zoom()
                # shot i's out: the top-left quadrant over the cut after it; shot i's in:
                # the top-right quadrant over the cut before it (timeline-trim.js, I9.7)
                q = (pg.locator("#tl .tl-edge").nth(i).locator(".q.t.l") if side == "out"
                     else pg.locator("#tl .tl-edge").nth(i - 1).locator(".q.t.r"))
                self.drag(q, dt * z)
                self.until(f"segs[{i}] && Math.abs(segs[{i}].{side} - {before[i][side]}) > 0.05",
                           f"dragging shot {i + 1}'s {side} on the cut's edge did not trim it "
                           f"({side} still {before[i][side]})", 8000)
                after = self.segs()
                need([s["id"] for s in after] == [s["id"] for s in before], "a trim changed the shots")
                need(after[i]["clip"] == before[i]["clip"], "the trimmed shot changed clip")
                other = "in" if side == "out" else "out"
                need(abs(after[i][other] - before[i][other]) < 1e-6,
                     f"a {side} trim moved the shot's {other} too")
                need((after[i][side] - before[i][side]) * dt > 0,
                     f"the {side} went {before[i][side]} → {after[i][side]}, the wrong way")
                for k, (a, bb) in enumerate(zip(after, before)):
                    if k != i:
                        need((a["in"], a["out"]) == (bb["in"], bb["out"]), f"shot {k + 1} changed too")
                self.wait_saved()
                d = self.wait_disk(lambda d: any(s.get("id") == after[i]["id"]
                                                 and abs(s[side] - after[i][side]) < 1e-6
                                                 for s in d.get("segments", [])),
                                   "the trim never reached the disk")
                del d
                F.setdefault("trimmed", []).append(after[i]["id"])
                F.setdefault("trims", []).append({"id": after[i]["id"], "side": side,
                                                  "from": before[i][side], "to": after[i][side]})
            return go

        def razor():
            before = self.segs()
            i = 1                                          # shot 2
            t0 = sum(s["out"] - s["in"] for s in before[:i])
            mid = t0 + (before[i]["out"] - before[i]["in"]) / 2
            ruler = pg.locator("#tl .tl-ruler")
            box = ruler.bounding_box()
            pg.mouse.click(self.x_of(mid), box["y"] + box["height"] / 2)
            self.until(f"Math.abs(tl.state.playhead - {mid}) < 0.2",
                       f"a click on the ruler did not put the playhead at {mid:.2f}", 5000)
            ph = self.js("tl.state.playhead")
            need(t0 + 0.15 < ph < t0 + before[i]["out"] - before[i]["in"] - 0.15,
                 f"the ruler click put the playhead at {ph:.2f}, not inside shot 2")
            self.js("pauseCut && pauseCut()")
            pg.keyboard.press("c")
            self.until(f"segs.length === {len(before) + 1}",
                       f"C with the playhead in shot 2 did not cut it ({len(before)} shots still)", 5000)
            after = self.segs()
            a, b2 = after[i], after[i + 1]
            need(a["clip"] == b2["clip"] == before[i]["clip"], "the razor's pieces are not of shot 2's clip")
            need(abs(a["in"] - before[i]["in"]) < 1e-6 and abs(b2["out"] - before[i]["out"]) < 1e-6
                 and abs(a["out"] - b2["in"]) < 1e-6, f"the pieces {a}, {b2} do not tile shot 2 {before[i]}")
            self.wait_saved()
            d = self.wait_disk(lambda d: len(d.get("segments", [])) == len(after),
                           "the razor never reached the disk")
            F["razored"] = [a["id"], b2["id"]]

        def move():
            before = self.segs()
            last = len(before) - 1
            # C left the razor tool in hand, as in Premiere: V for the selection tool
            pg.keyboard.press("v")
            need(self.js("tl.state.tool") == "select", f"V did not pick the selection tool ({self.js('tl.state.tool')})")
            blk = pg.locator("#tl .tl-lane[data-lane=V1] .blk").nth(last)
            blk.scroll_into_view_if_needed()
            bx = blk.bounding_box()
            first = pg.locator("#tl .tl-lane[data-lane=V1] .blk").nth(0).bounding_box()
            cx, cy = bx["x"] + bx["width"] / 2, bx["y"] + bx["height"] / 2
            pg.mouse.move(cx, cy)
            pg.mouse.down()
            pg.mouse.move(cx - 40, cy, steps=4)
            pg.mouse.move(first["x"] + 4, cy, steps=10)
            pg.mouse.up()
            self.until(f"segs[0].id === {json.dumps(before[last]['id'])}",
                       f"dragging shot {last + 1} by its body to the head did not move it there", 5000)
            after = self.segs()
            need([s["id"] for s in after] == [before[last]["id"]] + [s["id"] for s in before[:last]],
                 "the move did not put the last shot first and leave the rest in order")
            need(all((a["in"], a["out"]) == (b_["in"], b_["out"])
                     for a in after for b_ in before if a["id"] == b_["id"]), "a move changed a range")
            self.wait_saved()
            self.wait_disk(lambda d: [s.get("id") for s in d.get("segments", [])] == [s["id"] for s in after],
                           "the move never reached the disk")
            F["moved"] = before[last]["id"]

        def warmer():
            sid = F["trimmed"][0]
            idx = self.js(f"segs.findIndex(s => s.id === {json.dumps(sid)})")
            pg.locator("#tl .tl-lane[data-lane=V1] .blk").nth(idx).click()
            self.js("pauseCut && pauseCut()")
            # the proposal left the Assistant in front: the Effect Controls tab, as a person would
            tab = pg.locator(".ws-tab[data-tool=inspect]")
            need(tab.count() == 1, "no Effect Controls tab")
            tab.click()
            self.sel("#inspector .ec [data-sec=lumetri]", "Effect Controls shows no Lumetri Color for the shot")
            need(self.js("tl.state.sel.has(" + json.dumps(sid) + ")"), "the click did not select the shot")
            webgl = self.js("!!(window.grade && grade.ready)")
            if webgl:
                self.js(f"cueAt({idx}, segs[{idx}].in + 0.3)")
                self.until(f"liveVideo() && liveVideo().readyState >= 2 && grade.shot === {json.dumps(sid)}",
                           "the Program monitor never showed the selected shot's frame", 15000)
                pg.wait_for_timeout(200)
                p0 = self.js("grade.probe()")
            v = pg.locator("#inspector .ec-v[data-k=temperature]")
            need(v.is_visible(), "Effect Controls has no Temperature value")
            v.click()
            inp = pg.locator("#inspector .ec-v.editing[data-k=temperature]")
            inp.fill("40")
            inp.press("Enter")
            self.until("Math.abs(effects.value('temperature') - 40) < 0.6",
                       "Temperature 40 typed into Effect Controls did not take", 5000)
            self.wait_saved()
            d = self.wait_disk(lambda d: (((d.get("colour") or {}).get("shots") or {}).get(sid) or {}).get("balance"),
                               "the warmer balance never reached the disk")
            bal = d["colour"]["shots"][sid]["balance"]
            need(bal["gain"][0] > bal["gain"][2], f"the saved balance is not warmer: {bal}")
            F["warmed"] = {"id": sid, "balance": bal, "webgl": webgl}
            if webgl:
                pg.wait_for_timeout(250)
                p1 = self.js("grade.probe()")
                need(p0 and p1 and (p1[0] - p0[0]) - (p1[2] - p0[2]) > 1.0,
                     f"the Program picture did not get warmer: {p0} → {p1}")
                F["warmed"]["probe"] = [p0, p1]
            pg.keyboard.press("Escape")

        def rerun_panel():
            s = self.fp_state()
            need(s.get("state") == "stale-after-edits",
                 f"after hand edits the first pass is {s.get('state')!r}, not stale-after-edits")
            self.js("rcFirstPass.refresh()")
            self.fp_label("Re-run first pass")
            text = self.open_fp_panel()
            kept = pg.locator('[data-testid="firstpass-kept"] li')
            rows = [{"id": kept.nth(k).get_attribute("data-shot"), "text": kept.nth(k).inner_text()}
                    for k in range(kept.count())]
            F["kept_listed"] = rows
            ids = {r["id"] for r in rows}
            for sid in F["trimmed"]:
                need(sid in ids, f"the re-run panel does not name trimmed shot {sid} as kept "
                                 f"(it lists {[r['text'] for r in rows]}; {text[:160]!r})")
                line = next(r["text"] for r in rows if r["id"] == sid)
                need("trimmed" in line, f"the kept line for {sid} does not say trimmed: {line!r}")
            need(pg.locator('[data-testid="firstpass-run"]').inner_text().strip() == "Re-run first pass",
                 "the panel's button does not read Re-run first pass")
            F["kept_before"] = {s["id"]: s for s in self.segs() if s["id"] in ids}

        def rerun_accept():
            pg.locator('[data-testid="firstpass-run"]').click()
            self.sel("#proposal", "the re-run opened no proposal", 180000)
            self.until("pendingPlan && pendingPlan.firstpass", "the re-run's proposal has no first-pass facts", 30000)
            # the model proposed the rejected range again (RE_CUT); the proposal leaves it
            # out and says so — in plan.firstpass.rejected and in the notes
            r = F["rejected"]
            p = self.js("({segs: pendingPlan.segments.map(s => ({clip: s.clip, in: s.in, out: s.out})),"
                        " left: (pendingPlan.firstpass || {}).rejected || [], notes: pendingPlan.notes || ''})")
            back = [s for s in p["segs"] if s["clip"] == r["clip"]
                    and min(s["out"], r["out"]) - max(s["in"], r["in"]) > 0.5 * (r["out"] - r["in"])]
            need(not back, f"the re-run proposes the rejected range again: {back}")
            need(any(x.get("clip") == r["clip"] and x.get("action") in ("dropped", "kept as it is")
                     for x in p["left"]),
                 f"plan.firstpass.rejected does not name the left-out range: {p['left']}")
            need("you rejected" in p["notes"] and r["clip"].split(".")[0] in p["notes"],
                 f"the notes do not say the rejected shot was left out: {p['notes'][-200:]!r}")
            F["rerun_left_out"] = p["left"]
            self.accept_proposal(timeout=180000)
            after = self.segs()
            by = {s["id"]: s for s in after}
            for sid in F["trimmed"]:
                need(sid in by, f"trimmed shot {sid} is gone after the re-run")
            for sid, was in F["kept_before"].items():
                need(sid in by, f"kept shot {sid} ({was['clip']}) is gone after the re-run")
                now = by[sid]
                need((now["clip"], now["in"], now["out"]) == (was["clip"], was["in"], was["out"]),
                     f"kept shot {sid} was {was['clip']} {was['in']}–{was['out']}, "
                     f"now {now['clip']} {now['in']}–{now['out']}")
            # the warmth rides on the shot's id: still there
            w = F["warmed"]
            d = self.disk()
            bal = (((d.get("colour") or {}).get("shots") or {}).get(w["id"]) or {}).get("balance")
            need(bal == w["balance"], f"the warmed shot lost its balance in the re-run: {bal}")
            F["after_rerun"] = after
            F["model_calls"] = list(self.model.calls)
            need("rerun" in self.model.calls, "the re-run never asked the model with locked shots")
            self.wait_saved()

        def drop_grade():
            n0 = len(self.segs())
            got = self.js(DROP_THEN_GRADE, [ROT, 40])
            need("error" not in got, got.get("error", ""))
            need(got["n"] == n0 + 1 and len(got["fresh"]) == 1 and got["fresh"][0]["clip"] == ROT,
                 f"dropping {ROT} on V1 did not add one {ROT} shot: {got}")
            tmp = got["fresh"][0]["id"]
            need(got["shown"] == tmp,
                 f"after the drop Effect Controls shows {got['shown']}, not the dropped shot {tmp}")
            need(got["ok"] is True, f"effects.set('temperature', 40) refused the dropped shot: {got}")
            need(got["set"] - got["landed"] < 100,
                 f"the grade was set {got['set'] - got['landed']} ms after the drop, not within 100")
            F["drop"] = got
            self.idle()
            pg.reload()
            self.loaded()
            d = self.disk()
            on = d.get("segments", [])
            need(on and on[-1].get("clip") == ROT, f"after a reload the cut does not end on {ROT}: "
                 f"{[(s.get('clip'), s.get('id')) for s in on]}")
            sid = on[-1]["id"]
            need(sid and not str(sid).startswith("tmp-"), f"the dropped shot is on disk as {sid!r}")
            edl = self.wait_edl(lambda e: True, "the EDL cannot be read")
            shots = ((edl.get("colour") or {}).get("shots") or {})
            stray = [k for k in shots if str(k).startswith("tmp-")]
            need(not stray, f"colour.shots keeps temporary keys {stray} after a reload: {shots}")
            bal = (shots.get(sid) or {}).get("balance")
            need(bal and bal["gain"][0] > bal["gain"][2],
                 f"the grade set right after the drop is lost: shot {sid} has {shots.get(sid)} "
                 f"(colour.shots keys {list(shots)})")
            need([s["id"] for s in self.segs()] == [s["id"] for s in on],
                 "the board's shots are not the ones on disk after a reload")
            idx = len(on) - 1
            pg.locator("#tl .tl-lane[data-lane=V1] .blk").nth(idx).click()
            self.js("pauseCut && pauseCut()")
            tab = pg.locator(".ws-tab[data-tool=inspect]")
            if tab.count():
                tab.click()
            self.sel("#inspector .ec-v[data-k=temperature]", "Effect Controls shows no Temperature")
            need(self.js("String(inspected().id)") == sid, "the click did not put the dropped shot in Effect Controls")
            shown = pg.locator("#inspector .ec-v[data-k=temperature]").input_value()
            try:
                val = float("".join(ch for ch in shown if ch in "0123456789.-"))
            except ValueError:
                val = float("nan")
            need(abs(val - 40) < 0.6, f"after a reload Effect Controls shows Temperature {shown!r}, not 40")
            F["dropped"] = {"id": sid, "tmp": tmp, "balance": bal, "shown": shown}

        def upright():
            sid = F["dropped"]["id"]
            idx = self.js(f"segs.findIndex(s => s.id === {json.dumps(sid)})")
            need(idx >= 0, f"the dropped shot {sid} is not in the cut")
            self.js(f"cueAt({idx}, segs[{idx}].in + 1.0)")
            self.until(f"player.idx === {idx} && liveVideo() && liveVideo().readyState >= 2"
                       f" && (!(window.grade && grade.ready) || grade.shot === {json.dumps(sid)})",
                       f"the Program monitor never showed {ROT}'s frame", 90000)
            pg.wait_for_timeout(500)
            prog = quadrants(png_rgb(pg.locator("#player .screen").screenshot()),
                             f"{ROT} in the Program monitor")
            # the Source monitor, as opened from the Project panel: its proxy now
            src = self.source_picture(f"{ROT} in the Source monitor (its proxy)")
            F["upright"] = {"program": prog, "source": src,
                            "program_src": self.js("liveVideo().currentSrc"),
                            "media": self.media_kinds()}

        def short_shot():
            before = self.segs()
            # Source still holds CLIP_R (the upright step opened it): mark 2.0 → 2.4
            self.until(f"window.rcSource && rcSource.clip === {json.dumps(ROT)}",
                       f"{ROT} is not in the Source monitor", 5000)
            pg.locator("#pSource .ws-head").click()
            for key, t in (("i", 2.0), ("o", 2.4)):
                self.js(f"document.querySelector('#srcVideo').currentTime = {t}")
                self.until(f"Math.abs(document.querySelector('#srcVideo').currentTime - {t}) < 0.01",
                           f"the Source monitor did not go to {t}", 5000)
                pg.keyboard.press(key)
            src = self.js("({...rcSource})")
            need(src.get("inPoint") is not None and src.get("outPoint") is not None
                 and abs(src["outPoint"] - src["inPoint"] - 0.4) < 0.03,
                 f"I / O in the Source monitor marked {src}, not 0.4 s")
            # at the first cut: the short shot plays between two others
            cut = before[0]["out"] - before[0]["in"]
            self.js(f"tl.seek({cut})")
            self.js("document.activeElement && document.activeElement.blur()")
            pg.keyboard.press(",")
            self.until(f"segs.length === {len(before) + 1}",
                       f", did not insert the Source range ({len(before)} shots still)", 5000)
            after = self.segs()
            s = after[1]
            need(s["clip"] == ROT and abs((s["out"] - s["in"]) - 0.4) < 0.03,
                 f"the shot after the first cut is {s}, not 0.4 s of {ROT}")
            need([x["id"] for x in after[:1] + after[2:]] == [x["id"] for x in before],
                 "the insert moved or changed the other shots")
            self.idle()
            d = self.wait_disk(lambda d: len(d.get("segments", [])) == len(after)
                           and not str(d["segments"][1].get("id", "tmp-")).startswith("tmp-")
                           and d["segments"][1].get("clip") == ROT, "the 0.4 s shot never reached the disk")
            # ▶ across it: shot 1's last 0.6 s, the 0.4 s shot, into shot 3
            self.js("""() => { window.__pb = [];
              const tick = () => { if (!window.__pb) return;
                const m = document.querySelector('#screenMsg');
                window.__pb.push({t: performance.now(), idx: player.idx, playing: !!player.playing,
                                  msg: m && !m.hidden ? m.textContent : ''});
                requestAnimationFrame(tick); };
              requestAnimationFrame(tick); }""")
            self.js("cueAt(0, segs[0].out - 0.6)")
            self.until("liveVideo() && liveVideo().readyState >= 2", "the Program monitor is not ready at shot 1", 30000)
            pg.locator("#pProgram .ws-head").click()
            t0 = time.time()
            pg.keyboard.press(" ")
            self.until("player.playing", "space did not start playback", 5000)
            self.until("player.idx >= 2 && liveVideo() && liveVideo().currentTime > segs[2].in + 0.2",
                       "playback never got through the 0.4 s shot into the next", 15000)
            wall = time.time() - t0
            self.js("pauseCut && pauseCut()")
            pb = self.js("(() => { const p = window.__pb; window.__pb = null; return p; })()")
            order = []
            for r in pb:
                if r["playing"] and (not order or order[-1] != r["idx"]):
                    order.append(r["idx"])
            need(order[:3] == [0, 1, 2], f"playback did not go shot 1 → the 0.4 s shot → shot 3: {order}")
            in_short = [r["t"] for r in pb if r["idx"] == 1 and r["playing"]]
            F["short"] = {"id": d["segments"][1]["id"], "range": [s["in"], s["out"]], "wall_s": round(wall, 2),
                          "order": order[:6],
                          "short_on_screen_ms": round(in_short[-1] - in_short[0]) if in_short else 0,
                          "messages": sorted({r["msg"] for r in pb if r["msg"]})}
            need(wall < 0.6 + 0.4 + 0.2 + 4.0, f"playing 1.2 s across the short shot took {wall:.1f} s")

        def export():
            pg.locator("#tl .tl-ruler").click(position={"x": 2, "y": 2})   # focus off any input
            pg.keyboard.press("Control+m")
            self.sel("[data-testid=export-dialog] [data-testid=export-go]", "Ctrl+M opened no export dialog", 8000)
            need(self.js("document.querySelector('[data-testid=export-dialog]').open !== false"),
                 "the export dialog is not open")
            # Quick 1080p, the whole sequence; the estimate is read once it is measured
            pg.select_option("[data-testid=export-preset]", "quick")
            self.until("document.querySelector('#exSize') &&"
                       " document.querySelector('#exSize').dataset.source !== 'measuring'",
                       "the size estimate never stopped measuring", 120000)
            F["export_estimate"] = {"mb": float(pg.get_attribute("#exSize", "data-mb")),
                                    "source": pg.get_attribute("#exSize", "data-source"),
                                    "duration": pg.inner_text("#exDur"),
                                    "tc_total": self.js("tl.tc(tl.total())")}
            need(F["export_estimate"]["duration"] == F["export_estimate"]["tc_total"],
                 f"the dialog exports {F['export_estimate']['duration']}, not the whole sequence "
                 f"({F['export_estimate']['tc_total']})")
            starts = self.js("(() => { let t = 0; return segs.map(s => { const a = t;"
                             " t += (s.out - s.in) / (Number(s.speed) > 0 ? Number(s.speed) : 1);"
                             " return {id: s.id, clip: s.clip, at: a, len: t - a}; }); })()")
            F["export_shots"] = starts
            self.js("""() => { window.__bar = [];
              const d = document.querySelector('[data-testid=export-dialog]');
              const read = () => { const b = d.querySelector('[data-testid=export-progress]');
                if (b) window.__bar.push(Number(b.getAttribute('aria-valuenow'))); };
              new MutationObserver(read).observe(d, {subtree: true, childList: true, attributes: true,
                                                     attributeFilter: ['aria-valuenow']}); }""")
            pg.fill("[data-testid=export-name]", "journey")
            pg.click("[data-testid=export-go]")
            self.sel("[data-testid=export-progress]", "Export showed no progress bar")
            self.until("(() => { const b = document.querySelector('[data-testid=export-progress]');"
                       " return b && Number(b.getAttribute('aria-valuenow')) >= 100; })()",
                       "the export never reached 100 %", 300000)
            self.sel("[data-testid=export-done][data-path]", "the export reached 100 % but never said where the file is", 30000)
            bar = self.js("window.__bar")
            need(bar == sorted(bar), f"the export bar went backwards: {bar}")
            F["export_bar"] = sorted(set(bar))
            F["export_path"] = pg.get_attribute("[data-testid=export-done]", "data-path")
            F["tl_total"] = self.js("tl.total()")

        def ffprobe():
            path = Path(F["export_path"] or "")
            need(path.is_file() and path.stat().st_size > 0, f"no file at {path}")
            need(str(path).startswith(str(self.out / "work")), f"the export left --work: {path}")
            r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                "format=duration:stream=codec_type,duration,r_frame_rate,"
                                "avg_frame_rate,nb_frames",
                                "-of", "json", str(path)], capture_output=True, text=True)
            need(r.returncode == 0, f"ffprobe could not read the file: {r.stderr.strip()[:160]}")
            info = json.loads(r.stdout)
            kinds = [s.get("codec_type") for s in info.get("streams", [])]
            need("video" in kinds, f"the file has no video stream: {kinds}")
            need("audio" in kinds, f"the file has no audio stream: {kinds}")
            v = next(s for s in info["streams"] if s["codec_type"] == "video")
            # one frame is the SEQUENCE's frame (/api/project fps, the clips' own rate).
            # Not the file's r_frame_rate: on a cut whose edit points are not on frame
            # boundaries the file's timestamps are irregular and r_frame_rate reads
            # 120/1 on a 24 fps sequence, which would make "one frame" 8 ms.
            seq_fps = float(self.disk().get("fps") or 0) or 24.0
            frame = 1.0 / seq_fps
            got = float(info["format"]["duration"])
            want = float(F["tl_total"])
            F["ffprobe"] = {"duration": got, "video_duration": float(v.get("duration") or 0),
                            "tl_total": want, "sequence_fps": seq_fps,
                            "r_frame_rate": v.get("r_frame_rate"),
                            "avg_frame_rate": v.get("avg_frame_rate"),
                            "nb_frames": v.get("nb_frames"), "streams": kinds,
                            "bytes": path.stat().st_size}
            need(abs(got - want) <= frame + 1e-6,
                 f"the file is {got:.3f} s, the sequence {want:.3f} s (one frame = {frame:.4f} s)")
            vd = F["ffprobe"]["video_duration"]
            need(abs(vd - want) <= frame + 1e-6,
                 f"the video stream is {vd:.3f} s, the sequence {want:.3f} s (one frame = {frame:.4f} s)")
            # the dialog's size estimate against the file (its MB are 10^6 bytes)
            est, real = F["export_estimate"]["mb"], path.stat().st_size / 1e6
            F["ffprobe"]["size_vs_estimate"] = round((est - real) / real, 3)
            need(abs(est - real) <= 0.25 * real,
                 f"the dialog estimated {est:.3f} MB ({F['export_estimate']['source']}), "
                 f"the file is {real:.3f} MB ({100 * (est - real) / real:+.0f} %)")
            # CLIP_R in the film is upright too (the frame in the middle of the dropped shot)
            shot = next(s for s in F["export_shots"] if s["id"] == F["dropped"]["id"])
            F["ffprobe"]["rotated_frame"] = quadrants(
                file_frame_rgb(path, shot["at"] + shot["len"] / 2), f"{ROT} in the exported file")

        plan = [("open", open_board), ("preview", preview), ("firstpass", firstpass_panel), ("run", run_firstpass),
                ("reject", reject), ("accept", accept), ("selects", selects), ("trim-out", trim(0, "out", -0.35)),
                ("trim-in", trim(2, "in", 0.4)), ("razor", razor), ("move", move),
                ("warmer", warmer), ("rerun-panel", rerun_panel), ("rerun-accept", rerun_accept),
                ("drop-grade", drop_grade), ("upright", upright), ("short-shot", short_shot),
                ("export", export), ("ffprobe", ffprobe)]
        self.planned = len(plan)
        for name, fn in plan:
            if not self.step(name, fn):
                break
        b.close()


# ------------------------------------------------------------------ the harness

def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        print(__doc__.split("\n\n")[1])
        return 2
    _reexec_under_uv()
    snap, out = Path(args[0]).resolve(), Path(args[1]).resolve()
    t_all = time.time()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    # never Karl's ledger, footage or work: everything under <out>
    os.environ["ROUGHCUT_LEDGER"] = str(out / "ledger.jsonl")
    os.environ["ROUGHCUT_BACKEND"] = "claude_cli"           # the wording of the plan backend
    deps = Path.home() / ".local/chromium-deps/root/usr/lib/x86_64-linux-gnu"
    if deps.exists() and str(deps) not in os.environ.get("LD_LIBRARY_PATH", ""):
        os.environ["LD_LIBRARY_PATH"] = f"{deps}:{os.environ.get('LD_LIBRARY_PATH', '')}"
    for p in (snap / "app" / "tests", snap / "app", snap):
        sys.path.insert(0, str(p))

    j = Journey(snap, out)
    result = {"snapshot": str(snap), "out": str(out), "ok": False, "steps": j.steps,
              "page_errors": j.errors, "console_errors": j.console, "facts": j.facts}
    srv = th = server = mp = None
    setup_err = None
    try:
        import pytest
        import uvicorn
        conftest = _load("journey_conftest", snap / "app" / "tests" / "conftest.py")
        fx = out / "fixture"
        footage, sidecars = fx / "footage", fx / "sidecars"
        footage.mkdir(parents=True)
        sidecars.mkdir()
        for stem in ("CLIP_A", "CLIP_B", "CLIP_C"):
            conftest._make_clip(footage / f"{stem}.MP4")
            (sidecars / f"{stem}.audio.json").write_text(
                json.dumps(conftest._sidecar(stem), indent=1), encoding="utf-8")
        # the GoPro-shaped clip: HEVC 59.94, upside down under a −180° matrix, no words
        j.facts["rotated_fixture"] = make_rotated_clip(footage / ROT, fx / "raw")
        side = conftest._sidecar("CLIP_R")
        side.update(duration_s=ROT_S, transcript=[], candidates=[])
        side["summary"] = {**side["summary"], "n_words": 0, "speech_fraction": 0.0}
        (sidecars / "CLIP_R.audio.json").write_text(json.dumps(side, indent=1), encoding="utf-8")

        import server
        from roughcut import config, inference
        from test_index import _stub_tools
        mp = pytest.MonkeyPatch()
        server.configure(None, footage, sidecars, out / "work", proxies=False, visual=None)
        stubs = out / "stubs"
        stubs.mkdir()
        _stub_tools(server, mp, stubs)
        mp.setattr(server, "FIRSTPASS_POLL_S", 0.05)
        if hasattr(server, "RENDER_TICK_S"):
            mp.setattr(server, "RENDER_TICK_S", 0.1)
        server.FIRSTPASS.clear()
        j.model = scripted_model(inference, config)
        inference.set_backend(j.model)
        inference.reset_spend()

        port = _free_port()
        srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                            log_level="error"))
        th = threading.Thread(target=srv.run, daemon=True)
        th.start()
        for _ in range(200):
            if srv.started:
                break
            time.sleep(0.1)
        if not srv.started:
            raise RuntimeError("the server did not start")
        j.base = f"http://127.0.0.1:{port}"
    except Exception as e:
        setup_err = f"{type(e).__name__}: {e}"
        traceback.print_exc()

    if setup_err is None:
        from playwright.sync_api import sync_playwright
        print(f"journey: {snap} → {out} (server {j.base})", flush=True)
        try:
            with sync_playwright() as pw:
                j.run(pw)
        except Exception as e:                      # the browser itself
            setup_err = f"browser: {type(e).__name__}: {str(e).splitlines()[0]}"
            traceback.print_exc()

    # ---- the backend was the scripted one, every call
    ledger_bad = []
    lp = out / "ledger.jsonl"
    if lp.exists():
        for ln in lp.read_text().splitlines():
            try:
                row = json.loads(ln)
            except ValueError:
                continue
            if row.get("backend") != "journey-scripted":
                ledger_bad.append(row.get("backend"))
    j.facts["ledger_rows"] = len(lp.read_text().splitlines()) if lp.exists() else 0

    # ---- stop everything we started: renders, the pass, the server
    if server is not None:
        for proc in list(getattr(server, "RENDER_PROCS", {}).values()):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                pass
        deadline = time.time() + 30
        while any(x.get("state") == "running" for x in server.FIRSTPASS.values()) \
                and time.time() < deadline:
            time.sleep(0.1)
        # The board's renders list started a review copy of the export (ensure_review):
        # wait for it rather than kill its ffmpeg half-way, which is what printed
        # "!! review copy failed for journey.mp4" in every gate. A copy that fails on
        # its own is a real failure and fails the journey below.
        states = getattr(server, "REVIEW_STATE", {})
        lock = getattr(server, "REVIEW_LOCK", threading.Lock())
        deadline = time.time() + 180
        while time.time() < deadline:
            with lock:
                review = dict(states)
            if "building" not in review.values():
                break
            time.sleep(0.2)
        j.facts["review_copies"] = review
    if srv is not None:
        srv.should_exit = True
        th.join(timeout=10)
    if mp is not None:
        mp.undo()
    j.facts["stopped_children"] = len(_stop_children())     # e.g. the export's review copy

    planned = getattr(j, "planned", 19)
    fail = next((s for s in j.steps if not s["ok"]), None)
    total = time.time() - t_all
    if fail:
        line = f"journey ✗ step {fail['n']} {fail['name']}: {fail['why'][:300]}"
    elif setup_err:
        where = "setup" if not j.steps else "browser"
        line = f"journey ✗ step {len(j.steps) + 1} {where}: {setup_err[:200]}"
    elif len(j.steps) != planned:
        line = f"journey ✗ step {len(j.steps) + 1} ?: the journey stopped after {len(j.steps)} of {planned} steps"
    elif ledger_bad:
        line = f"journey ✗ step {planned} {j.steps[-1]['name']}: a call reached a real backend ({ledger_bad[0]})"
    elif any(v != "ready" for v in (j.facts.get("review_copies") or {}).values()):
        name, st = next((k, v) for k, v in j.facts["review_copies"].items() if v != "ready")
        line = f"journey ✗ step {planned} {j.steps[-1]['name']}: the review copy of {name} is {st}, not ready"
    else:
        line = f"journey ✓ {len(j.steps)} steps · {total:.0f} s"
    result.update(ok=line.startswith("journey ✓"), line=line, total_s=round(total, 1),
                  setup_error=setup_err, ledger_bad=ledger_bad)
    (out / "journey.json").write_text(json.dumps(result, indent=1, default=str))
    print(line, flush=True)
    sys.stdout.flush()
    # daemon threads (uvicorn, the pass's workers) must not hold the exit
    os._exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    sys.exit(main())
