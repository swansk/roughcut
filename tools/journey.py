#!/usr/bin/env python3
"""The journey (REDESIGN-GOAL dimension 12): one scripted editing session, end to end.

    python3 tools/journey.py <snapshot dir> <out dir>

Real Chromium against the board of `<snapshot dir>` (a `git archive` of the rev under
judgement, as tools/gate.py makes it), on the suite's synthetic fixture (three 6-second
clips with fabricated sidecars, built fresh under `<out dir>/fixture` by the snapshot's
own app/tests/conftest.py) and a fresh `--work` under `<out dir>/work`. The model is
scripted (no real model call, ever: the ledger is checked afterwards) and the index's
tools are the suite's stubs (app/tests/test_index._stub_tools).

The steps, each one a screenshot at 1440×900 in `<out dir>/NN-step.png`:

  01 open           the board lands; the bin holds the fixture; "Run first pass" in the header
  02 firstpass      one click on [data-testid=firstpass-button] opens its panel (steps, cost)
  03 run            [data-testid=firstpass-run] → the index (stubbed) → picks → the proposal opens
  04 accept         Accept puts the proposed cut on V1 and on disk
  05 selects        Selects lists the pass's picks (> 0 rows, one per pick); an amber marker
                    on the cut wherever a pick overlaps a shot (verdict 4, punch 1)
  06 trim-out       a ripple trim of shot 1's out, dragged on the cut's edge column
  07 trim-in        a ripple trim of shot 3's in, the same way
  08 razor          the playhead into shot 2 by a click on the ruler, then C: two pieces
  09 move           shot 4 dragged by its body to the head of the film
  10 warmer         Effect Controls › Lumetri Color › Temperature on a trimmed shot
  11 rerun-panel    the header reads "Re-run first pass"; its panel names both trimmed shots as kept
  12 rerun-accept   Run, accept: every kept shot (the trimmed two among them) has the same in/out
  13 export         Ctrl+M → export-dialog, export-name, export-go, export-progress → 100, export-done
  14 ffprobe        the file's duration is within one frame of tl.total()

The first failing step stops the journey. Zero page errors are allowed: an error thrown
in the page fails the step it happened in. `<out dir>/journey.json` has each step's name,
ok, ms, PNG, why it failed and the page errors; the last line printed is the gate's:
`journey ✓ 14 steps · 74 s` or `journey ✗ step 7 razor: <why>`. Exit 0 only when green.

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


# ------------------------------------------------------------------ the scripted model

FIRST_CUT = [
    {"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0, "why": "the greeting"},
    {"clip": "CLIP_B.MP4", "in": 2.4, "out": 4.0, "why": "the reply"},
    {"clip": "CLIP_C.MP4", "in": 0.5, "out": 4.0, "why": "the walk"},
    {"clip": "CLIP_B.MP4", "in": 4.4, "out": 5.6, "why": "goodbye"},
]
# The re-run's answer tampers with the editor's shots on purpose (the server must put
# each locked one back exactly) and offers one new shot.
RE_CUT = [
    {"clip": "CLIP_A.MP4", "in": 0.4, "out": 2.2, "why": "the greeting, longer"},
    {"clip": "CLIP_C.MP4", "in": 0.2, "out": 3.0, "why": "the walk, earlier"},
    {"clip": "CLIP_A.MP4", "in": 4.6, "out": 5.8, "why": "a new ending"},
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

    def fp_state(self) -> dict:
        import httpx
        return httpx.get(self.base + "/api/firstpass", timeout=30).json()

    def wait_saved(self, timeout=15000) -> None:
        self.until("document.querySelector('#saveState') && "
                   "document.querySelector('#saveState').dataset.state === 'saved'",
                   "the board never said the edit was saved", timeout)

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
        pg.set_default_timeout(30000)
        F = self.facts

        def open_board():
            t0 = time.time()
            pg.goto(self.base + "/")
            self.until("window.rcFirstPass && rcFirstPass.state()",
                       "the board never loaded the first pass's state (rcFirstPass)", 30000)
            self.until("document.querySelectorAll('#projList [data-clip]').length === 3",
                       "the Project panel never listed the fixture's three clips", 20000)
            F["open_ms"] = round((time.time() - t0) * 1000)
            clips = self.js("[...document.querySelectorAll('#projList tr[data-clip], #projList .picon[data-clip]')]"
                            ".map(r => r.dataset.clip)")
            need(sorted(set(clips)) == ["CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"],
                 f"the bin shows {clips}, not the fixture's three clips")
            need(self.js("segs.length") == 0, "a fresh bin should open with an empty sequence")
            self.fp_label("Run first pass")
            need(pg.locator('header [data-testid="firstpass-button"]').count() == 1,
                 "the first-pass control is not in the header")

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
            need([s["clip"] for s in got] == [s["clip"] for s in FIRST_CUT],
                 f"the accepted cut is not the model's: {[s['clip'] for s in got]}")
            need(all(s["id"] for s in got), "an accepted shot has no id")
            F["accepted"] = got
            self.wait_saved()
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
            self.until(f"document.querySelectorAll('#library tr.keep.pick').length === {len(picks)}",
                       f"Selects does not list the pass's {len(picks)} picks", 15000)
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
            self.wait_disk(lambda d: len(d.get("segments", [])) == len(after),
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

        def export():
            pg.locator("#tl .tl-ruler").click(position={"x": 2, "y": 2})   # focus off any input
            pg.keyboard.press("Control+m")
            self.sel("[data-testid=export-dialog] [data-testid=export-go]", "Ctrl+M opened no export dialog", 8000)
            need(self.js("document.querySelector('[data-testid=export-dialog]').open !== false"),
                 "the export dialog is not open")
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

        plan = [("open", open_board), ("firstpass", firstpass_panel), ("run", run_firstpass),
                ("accept", accept), ("selects", selects), ("trim-out", trim(0, "out", -0.35)),
                ("trim-in", trim(2, "in", 0.4)), ("razor", razor), ("move", move),
                ("warmer", warmer), ("rerun-panel", rerun_panel), ("rerun-accept", rerun_accept),
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
    if srv is not None:
        srv.should_exit = True
        th.join(timeout=10)
    if mp is not None:
        mp.undo()
    j.facts["stopped_children"] = len(_stop_children())     # e.g. the export's review copy

    planned = getattr(j, "planned", 14)
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
