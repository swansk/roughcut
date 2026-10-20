"""An AI design request says what it did, and no shot id reaches the editor (verdict 12,
punches 5 and 6; wave 16 lane AIHONEST).

Punch 5, the judge's words: *"Put a big white KILLINGTON title over the first two seconds of
the first shot" → 2 `design_effect` ops … Accept read "✓ 2 changes applied" though nothing
was applied. The next Ctrl+Z silently undid the In/Out + marker before it.* Now the accept
says "2 effects being designed — review them in Designed Effects" and opens that tab; it is
ONE undo entry of its own (`tl.mark`), so a Ctrl+Z right after it takes the requests back —
the design still running is stopped (verdict 14, punch 7: its job cancelled, its call killed,
nothing kept; one that landed before its cancel is set aside), the one not asked yet is never
asked — and the In/Out stays as it was. Ctrl+Shift+Z brings back what was set aside with no
call and asks again for what was never made; Ctrl+Z after they landed sets the proposals
aside. An accept that also changes the cut rides one entry with them.

Punch 6: the model saw shot ids and wrote them ("The last shot (g5848fde311) then moves…",
"the continuation into shot g0e2637544b needs its own matching effect"). A page scan for
`\\bg[0-9a-f]{10}\\b` over the Assistant, the AI prompt's line and its hover, the answer,
Things to check and the Designed Effects cards finds nothing — with a scripted model whose
every sentence carries ids, through the real server (roughcut/assist.py, roughcut/fx.py),
and with the server's words faked to carry them raw (app.js `clipNamed`, the board's guard).

In real Chromium on the fixture bin, the real uvicorn server, a scripted model behind
`roughcut.inference.set_backend` — never a real call. The designer is the real `fx.design`
(the placing look and the proof render stubbed, as test_fx_ui stubs them), held on a gate
so a Ctrl+Z can land while a design is still running."""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from roughcut import config, fx, inference  # noqa: E402

# the server's own id shape (server.py `_mint`: "g" + ten hex digits)
SEGS = [{"id": "g0a0000000a", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
        {"id": "g0b0000000b", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"},
        {"id": "g0c0000000c", "clip": "CLIP_C.MP4", "in": 2.0, "out": 5.0, "why": "third"}]
A, B, C = (s["id"] for s in SEGS)
GHOST = "g0e2637544b"                       # an id the cut does not have
SHOT_ID = re.compile(r"\bg[0-9a-f]{10}\b")
STATUS = '[data-testid="ai-status"]'
RESULT = '[data-testid="ai-result"]'
INPUT = '[data-testid="ai-prompt-input"]'

TITLE = {
    "name": f"KILLINGTON title on {A}",
    "why": f"a big white title over shot {A}; the continuation into shot {B} needs its own matching effect",
    "limits": f"it cannot run on into the next shot ({B})",
    "events": [{"t": 1.5, "x": 0.5, "y": 0.3}],
    "overlay": {"duration": 1.0, "size": 0.9,
                "shapes": [{"type": "text", "text": "KILLINGTON", "at": [0, 0], "h": 0.34, "bold": True,
                            "color": "#ffffff"}]},
    "sound": None,
}


class Model:
    """The assist call (its schema has `kind`) answers `answer`; the designer's call
    (its prompt starts "THE NOTE:") answers TITLE and is counted; anything else "{}".
    `calls` counts every call, of any kind (a redo that restores makes none). With
    `hold_s`, the designer's call takes that long the way the CLI backend makes it: a
    child process of the job's thread, so in the job's procscope (`running` is set when it
    starts; a cancel kills it as it kills `claude -p`, counted in `killed`, and the call
    fails as the CLI's does)."""
    name = "aihonest-scripted"

    def __init__(self):
        self.answer: dict = {"kind": "answer", "text": "ok"}
        self.notes: list[str] = []
        self.calls = 0
        self.hold_s = 0
        self.killed = 0
        self.running = threading.Event()

    def complete(self, request):
        self.calls += 1
        schema = request.schema or {}
        if "kind" in schema:
            text = json.dumps(self.answer)
        elif request.prompt.startswith("THE NOTE:"):
            if self.hold_s:
                self.running.set()
                proc = subprocess.run([sys.executable, "-c", f"import time; time.sleep({self.hold_s})"])
                if proc.returncode != 0:
                    self.killed += 1
                    raise inference.InferenceError(f"claude CLI failed: exit {proc.returncode}")
            self.notes.append(request.prompt.split("\n", 1)[0][len("THE NOTE: "):])
            text = json.dumps(TITLE)
        else:
            text = "{}"
        model = config.model_for(request.role)
        return inference.Result(content=text, input_tokens=4000, output_tokens=300, backend=self.name,
                                model=model, projected_usd=config.projected_usd(model, 4000, 300),
                                latency_ms=1, raw=text)


MODEL = Model()
GATE = threading.Event()                    # the designer answers once this is set
GATE.set()


def _silent_wav(path: Path, seconds: float = 0.2, sr: int = 48000) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * 2 * int(sr * seconds))
    return path


@pytest.fixture(scope="module", autouse=True)
def designer():
    """The real `fx.design` on the scripted model, held on GATE and asked not to place by
    looking (that is a strip of frames and a second call); the proof render stubbed, as
    test_fx_ui stubs it — the server calls all of these from a job thread of this process."""
    real = fx.design

    def design(note, seg, clip, sidecar, **kw):
        assert GATE.wait(30), "the test never let the designer answer"
        kw["place"] = False
        return real(note, seg, clip, sidecar, **kw)

    def verify(effect, seg, *, onset=None, hz=10, part=None, base=None, impact=True):
        return {"ok": True, "at": "2026-10-19T12:00:00",
                "checks": [{"key": "in_shot", "label": "every moment inside the shot", "ok": True, "detail": ""}]}

    mp = pytest.MonkeyPatch()
    mp.setattr(fx, "design", design)
    mp.setattr(fx, "synth_sound", lambda sound, out, *, sr=48000: _silent_wav(Path(out), 0.2, sr))
    mp.setattr(fx, "part_graph", lambda effects, w, h, fps, workdir, *, vin="vbase", ain="abase": ([], "", vin, ain))
    mp.setattr(fx, "verify", verify)
    yield
    GATE.set()
    mp.undo()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed(project) -> None:
    body = {"variant": "T", "title": "test cut", "orient": "none", "story": "", "target_s": [5, 20],
            "segments": [dict(s) for s in SEGS]}
    Path(project["edl"]).write_text(json.dumps(body, indent=1), encoding="utf-8")


def _clear_fx() -> None:
    """No proposal and no finished fx job from an earlier test (a finished job stays on
    /api/jobs for 12 s and its row would grow the header's strip)."""
    import server
    server.FX.clear()
    shutil.rmtree(server.fx_home(), ignore_errors=True)


@pytest.fixture(scope="module")
def live_server(project):
    import uvicorn
    import server

    original = project["edl"].read_text(encoding="utf-8")
    _seed(project)
    server.configure(project["edl"], project["footage"], project["sidecars"], project["work"],
                     proxies=False, assets=project["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in project["stems"]])
    inference.set_backend(MODEL)
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
    GATE.set()
    srv.should_exit = True
    thread.join(timeout=10)
    inference.set_backend(None)
    _clear_fx()
    project["edl"].write_text(original, encoding="utf-8")


@pytest.fixture(scope="module")
def browser(live_server):
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        yield b
        b.close()


@pytest.fixture(autouse=True)
def fresh(live_server):
    MODEL.answer = {"kind": "answer", "text": "ok"}
    MODEL.notes = []
    MODEL.calls = 0
    MODEL.hold_s = 0
    MODEL.killed = 0
    MODEL.running.clear()
    GATE.set()
    _clear_fx()
    yield
    GATE.set()


class Board:
    """One fresh page on a freshly seeded EDL; a page error or a console error (a 404
    included) fails the test at close."""

    def __init__(self, browser, url, project):
        _seed(project)
        self.project = project
        self.ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        self.page = self.ctx.new_page()
        self.errors: list[str] = []
        self.console: list[str] = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on("console", lambda m: self.console.append(m.text) if m.type == "error" else None)
        self.page.on("response", lambda r: self.console.append(f"{r.status} {r.url}")
                     if r.status >= 400 and "/api/" in r.url else None)
        self.page.goto(url)
        self.page.wait_for_selector("#tl .blk")
        self.page.wait_for_function(
            "window.rcActions && window.rcAI && window.fx && fx.ready && window.tlLanes"
            " && typeof C !== 'undefined' && C && C.shots", timeout=15000)
        self.page.wait_for_selector(INPUT)
        # every toast the page shows, in order (a later one replaces the one before)
        self.page.evaluate("""() => {
          window.__toasts = [];
          new MutationObserver(() => {
            const t = document.querySelector('#toast');
            if (t && t.textContent) window.__toasts.push(t.textContent);
          }).observe(document.querySelector('#toast'), {childList: true, characterData: true, subtree: true});
        }""")

    def close(self):
        errs, cons = list(self.errors), list(self.console)
        self.ctx.close()
        assert errs == [], errs
        assert cons == [], cons

    def settle(self) -> dict:
        self.page.evaluate("""async () => {
          for (let k = 0; k < 3; k++) {
            await roughcutFlush();
            if (tl.saveSeq) await tl.saveSeq();
            await new Promise((r) => setTimeout(r, 60));
          }
        }""")
        return json.loads(Path(self.project["edl"]).read_bytes())

    def ask(self, ops: list, summary: str = "the edit", text: str = "do it") -> None:
        """What an editor does: Ctrl+J, the words, Enter; the review opens by itself."""
        MODEL.answer = {"kind": "ops", "summary": summary, "ops": ops}
        p = self.page
        p.locator("#tl").first.click(position={"x": 5, "y": 5})
        p.keyboard.press("Control+j")
        p.keyboard.type(text)
        p.keyboard.press("Enter")
        p.wait_for_function(f"document.querySelector('{STATUS}').dataset.state === 'done'", timeout=15000)
        p.wait_for_selector("[data-testid=ai-review] [data-testid=ai-op-row]")

    def accept(self) -> None:
        self.page.locator("[data-testid=ai-accept-all]").click()
        self.page.wait_for_function("!rcActions.open")

    def undo(self) -> None:
        self.page.evaluate("document.activeElement && document.activeElement.blur()")
        self.page.keyboard.press("Control+z")

    def redo(self) -> None:
        self.page.evaluate("document.activeElement && document.activeElement.blur()")
        self.page.keyboard.press("Control+Shift+z")

    def result(self) -> str:
        return " ".join(self.page.locator(RESULT).inner_text().split())

    def undo_title(self) -> str:
        return self.page.locator("#undo").get_attribute("title") or ""

    def toasts(self) -> list[str]:
        return self.page.evaluate("window.__toasts")

    def effects(self) -> list[dict]:
        return self.page.evaluate("fetch('/api/fx').then(r => r.json())")["effects"]

    def designs_idle(self, timeout: float = 20.0) -> None:
        """Every fx job finished on the server (the designs and their checks; a design its
        Ctrl+Z stopped reads cancelled)."""
        import server
        end = time.time() + timeout
        while time.time() < end:
            if all(j.get("state") in ("done", "failed", "cancelled") for j in list(server.FX.values())):
                self.page.wait_for_timeout(1500)            # the board's poll sees it
                return
            self.page.wait_for_timeout(100)
        raise AssertionError("an fx job never finished")


def _marks(edl: dict) -> tuple:
    return (edl.get("in"), edl.get("out"), edl.get("markers"))


def _on_disk() -> list[dict]:
    """Every effect record in the bin's fx folder, listed or not (a withdrawn one is not).
    A record the server deletes between the listing and the read is not there (verdict 14's
    gate: this read raced a discard, as the check after it did)."""
    import server
    out = []
    for f in sorted(server.fx_home().glob("fx_*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except FileNotFoundError:
            continue
    return out


# ---------------------------------------------------------------- punch 5

def test_a_design_request_says_so_and_one_ctrl_z_takes_it_back_not_the_edit_before(browser, live_server, project):
    """The judge's test: an In/Out change; then a title asked for — two design_effect ops;
    Accept; Ctrl+Z; the In/Out unchanged."""
    b = Board(browser, live_server, project)
    try:
        p = b.page
        # the In/Out (and a marker) first, from the AI, as the judge made it
        b.ask([{"op": "in_out", "in": 0.5, "out": 3.5}, {"op": "marker", "at": 1.0}], text="in and out around 1 to 2")
        b.accept()
        marked = _marks(b.settle())
        assert marked[0] == 0.5 and marked[1] == 3.5 and len(marked[2]) == 1, marked
        assert b.undo_title() == "undo: AI: In/Out + marker (Ctrl+Z)"

        # the title: two designs, the first held in the designer
        GATE.clear()
        b.ask([{"op": "design_effect", "id": A, "note": "a big white KILLINGTON title"},
               {"op": "design_effect", "id": B, "note": "the KILLINGTON title carries on"}],
              summary="a big white KILLINGTON title", text="Put a big white KILLINGTON title over the first two seconds")
        b.accept()
        # never "applied": being designed, and where to review them — that tab open
        p.wait_for_function("dock.current() === 'fx'", timeout=8000)
        line = b.result()
        assert "2 effects being designed — review them in Designed Effects" in line, line
        assert "applied" not in line, line
        assert "Ctrl+Z undoes" in line, line
        assert any("2 effects being designed — review them in Designed Effects" in t for t in b.toasts()), b.toasts()
        assert "applied" not in b.toasts()[-1], b.toasts()
        # the accept is its own undo step, named for what it is
        assert b.undo_title() == "undo: AI: 2 effects (Ctrl+Z)"
        end = time.time() + 10
        while not MODEL.notes and time.time() < end:          # the first design is with the designer
            p.wait_for_timeout(100)
        assert len(MODEL.notes) == 0                           # held: the gate is shut
        assert p.evaluate("fx.state.busy && fx.state.busy.fx_kind") == "design"

        # Ctrl+Z right after: the requests taken back, the In/Out untouched; the design with
        # the designer is stopped (verdict 14, punch 7), and the toast says what that stopped
        import server
        b.undo()
        p.wait_for_function("window.__toasts.some((t) => t.includes('the 2 designs are discarded'))", timeout=5000)
        assert ("undone: AI: 2 effects — the 2 designs are discarded · the one being designed: stopped before its"
                " design call, nothing was asked") in b.toasts(), b.toasts()
        assert _marks(b.settle()) == marked, "the Ctrl+Z after the design accept undid the In/Out"
        assert b.undo_title() == "undo: AI: In/Out + marker (Ctrl+Z)"
        assert p.locator(STATUS).is_hidden() or "being designed" not in b.result()
        [job] = [j for j in server.FX.values() if j["fx_kind"] == "design"]
        assert (job["state"], job["detail"]) == (
            "cancelled", "taken back (Ctrl+Z) — stopped before its design call, nothing was asked"), dict(job)
        # the designer let go: the stopped job makes no call and keeps nothing; the second is
        # never asked for
        GATE.set()
        b.designs_idle()
        p.wait_for_timeout(1000)
        assert MODEL.notes == [], MODEL.notes
        assert b.effects() == [] and _on_disk() == []
        assert p.locator("#fx .fxcard").count() == 0
        assert not any("a proposal" in t or "failed" in t for t in b.toasts()), b.toasts()

        # Ctrl+Shift+Z: neither design was made, so both are asked again — free on this plan,
        # so no question first
        b.redo()
        p.wait_for_function("window.__toasts.some((t) => t.startsWith('redone: AI: 2 effects —'))", timeout=5000)
        said = next(t for t in b.toasts() if t.startswith("redone: AI: 2 effects —"))
        assert said == ("redone: AI: 2 effects — asks the designer again · free on your plan"
                        " — review them in Designed Effects"), said
        assert p.locator("[data-testid=redo-ask-confirm][open]").count() == 0
        assert _marks(b.settle()) == marked
        end = time.time() + 30
        while len([e for e in b.effects() if e["status"] == "proposed"]) < 2 and time.time() < end:
            p.wait_for_timeout(250)
        got = b.effects()
        assert sorted(e["shot"] for e in got) == sorted([A, B]) and all(e["status"] == "proposed" for e in got), got
        assert MODEL.notes == ["a big white KILLINGTON title", "the KILLINGTON title carries on"], MODEL.notes
        b.designs_idle()
        p.wait_for_function(
            f"document.querySelector('{RESULT}') === null || !document.querySelector('{RESULT}').textContent.includes('applied')")

        # Ctrl+Z once they are proposals: discarded now; the In/Out still as it was
        b.undo()
        end = time.time() + 10
        while b.effects() and time.time() < end:
            p.wait_for_timeout(200)
        assert b.effects() == []
        assert _marks(b.settle()) == marked
        p.wait_for_function("document.querySelectorAll('#fx .fxcard').length === 0", timeout=5000)
        # and the next Ctrl+Z is the In/Out's, saying so
        b.undo()
        p.wait_for_function("window.__toasts.some((t) => t === 'undone: AI: In/Out + marker')", timeout=5000)
        back = _marks(b.settle())
        assert back[0] is None and back[1] is None and not back[2], back
    finally:
        b.close()


def test_a_design_beside_a_cut_edit_is_one_undo_with_it(browser, live_server, project):
    """An answer that changes the cut and asks for a design: "1 change applied · 1 effect
    being designed"; one Ctrl+Z puts the cut back AND takes the request back."""
    b = Board(browser, live_server, project)
    try:
        p = b.page
        seeded = _marks(b.settle())
        b.ask([{"op": "in_out", "in": 0.5, "out": 2.5},
               {"op": "design_effect", "id": C, "note": "an arrow at the jump"}], text="mark the jump and point at it")
        b.accept()
        p.wait_for_function("dock.current() === 'fx'", timeout=8000)
        line = b.result()
        assert "1 change applied · 1 effect being designed — review it in Designed Effects" in line, line
        assert b.undo_title() == "undo: AI: In/Out + effect (Ctrl+Z)"
        end = time.time() + 15
        while not [e for e in b.effects() if e["status"] == "proposed"] and time.time() < end:
            p.wait_for_timeout(200)
        assert [e["shot"] for e in b.effects()] == [C]
        b.designs_idle()
        p.wait_for_function(f"document.querySelector('{RESULT}').textContent.includes('1 effect designed')", timeout=5000)
        b.undo()
        p.wait_for_function("window.__toasts.some((t) => t === 'undone: AI: In/Out + effect — the design is discarded')",
                            timeout=5000)
        assert _marks(b.settle()) == seeded
        end = time.time() + 10
        while b.effects() and time.time() < end:
            p.wait_for_timeout(200)
        assert b.effects() == []
    finally:
        b.close()


# ---------------------------------------------------------------- verdict 13, punch 6

def _until(page, fn, timeout: float = 15.0, what: str = "the condition"):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        page.wait_for_timeout(150)
    raise AssertionError(f"timed out waiting for {what}")


def test_a_redo_brings_back_the_same_design_with_no_model_call(browser, live_server, project):
    """The judge's measurement (verdict 13): Accept, the design lands, Ctrl+Z, Ctrl+Shift+Z —
    and "a different design (fx_03876b5d) arrived 17.6 s later, from a new model call". Now
    the redo is Premiere's: the same fx id, the same design, zero model calls (counted on
    the scripted backend), and no design job; twice over. A new edit after an undo empties
    the redo side, and the design set aside there goes for good."""
    import server
    b = Board(browser, live_server, project)
    try:
        p = b.page
        b.ask([{"op": "design_effect", "id": A, "note": "a big white KILLINGTON title"}],
              summary="a KILLINGTON title", text="put a big white KILLINGTON title on the first shot")
        b.accept()
        p.wait_for_function("dock.current() === 'fx'", timeout=8000)
        _until(p, lambda: [e for e in b.effects() if e["status"] == "proposed"], what="the design to land")
        b.designs_idle()
        [first] = b.effects()
        fx_id = first["id"]
        p.wait_for_selector(f"#fx .fxcard[data-id='{fx_id}']", timeout=8000)
        assert MODEL.notes == ["a big white KILLINGTON title"], MODEL.notes
        calls, jobs = MODEL.calls, set(server.FX)
        spec = {k: first.get(k) for k in ("name", "note", "events", "overlay", "shot")}

        for round_ in (1, 2):
            # Ctrl+Z: out of Designed Effects, set aside on disk (not deleted)
            b.undo()
            p.wait_for_function("(n) => window.__toasts.filter((t) => t === 'undone: AI: effect — the design is discarded')"
                                ".length >= n", arg=round_, timeout=5000)
            _until(p, lambda: b.effects() == [], what="the design to be set aside")
            assert [(e["id"], e["status"]) for e in _on_disk()] == [(fx_id, "withdrawn")]
            p.wait_for_function("document.querySelectorAll('#fx .fxcard').length === 0", timeout=5000)
            # Ctrl+Shift+Z: the same design back, as it was
            b.redo()
            p.wait_for_function("(n) => window.__toasts.filter((t) => t === 'redone: AI: effect — the design is back"
                                " — review it in Designed Effects').length >= n", arg=round_, timeout=5000)
            got = _until(p, lambda: b.effects() or None, what="the design to come back")
            assert [(e["id"], e["status"]) for e in got] == [(fx_id, "proposed")], got
            assert {k: got[0].get(k) for k in spec} == spec
            p.wait_for_selector(f"#fx .fxcard[data-id='{fx_id}']", timeout=8000)
            assert b.undo_title() == "undo: AI: effect (Ctrl+Z)"
            # zero model calls, no design job, no question asked
            p.wait_for_timeout(1500)
            assert MODEL.calls == calls, (MODEL.calls, calls)
            assert MODEL.notes == ["a big white KILLINGTON title"], MODEL.notes
            assert not [j for j in set(server.FX) - jobs if server.FX[j]["fx_kind"] == "design"]
            assert p.locator("[data-testid=redo-ask-confirm][open]").count() == 0

        # undone, then a new edit: the redo side is gone, and the design set aside with it
        b.undo()
        _until(p, lambda: b.effects() == [], what="the design to be set aside")
        assert p.evaluate("tl.mark('a new edit')") is True
        # gone for good: the record, then its files' folder (POST /api/fx/discard unlinks the
        # one and then removes the other — verdict 14's gate caught this test reading between
        # the two, so it waits for both)
        _until(p, lambda: _on_disk() == [] and not (server.fx_home() / fx_id).exists(),
               what="the set-aside design and its files to go for good")
        b.redo()                                      # nothing to redo: nothing comes back
        p.wait_for_timeout(800)
        assert b.effects() == [] and MODEL.calls == calls
    finally:
        b.close()


# ---------------------------------------------------------------- verdict 14, punch 7

def test_a_design_undone_while_its_call_runs_is_stopped_and_its_redo_designs_it(browser, live_server, project):
    """The judge's flow (verdict 14, `fxredo.log` B): accept a design, Ctrl+Z about a second
    later while its design call runs, wait, Ctrl+Shift+Z — and "redone: AI: effect", then
    nothing: no design, no "asks the designer again", no call. Now the Ctrl+Z stops the job:
    its call — a child process in the job's procscope, as `claude -p` is — is killed, the
    toast and the job say what was stopped, and nothing lands while the redo waits. The
    request counts as never made, so the redo asks the designer again, free on the plan, and
    a design lands."""
    import server
    b = Board(browser, live_server, project)
    try:
        p = b.page
        MODEL.hold_s = 120                            # the call is running, and runs on
        b.ask([{"op": "design_effect", "id": A, "note": "a thin white border"}],
              summary="a white border", text="put a thin white border around the first shot")
        b.accept()
        assert MODEL.running.wait(15), "the design call never started"
        b.undo()
        head = "undone: AI: effect — the design is discarded"
        p.wait_for_function("(h) => window.__toasts.some((t) => t.startsWith(h + ' ·'))", arg=head, timeout=5000)
        said = next(t for t in b.toasts() if t.startswith(head + " ·"))
        assert said == f"{head} · its design call was stopped, nothing is kept", said
        # the call is dead within the kill's grace, and the job says what it stopped
        _until(p, lambda: MODEL.killed == 1, timeout=5, what="the design call to be killed")
        [job] = [j for j in server.FX.values() if j["fx_kind"] == "design"]
        assert (job["state"], job["detail"]) == (
            "cancelled", "taken back (Ctrl+Z) — its design call was stopped, nothing is kept"), dict(job)
        # the wait: nothing lands, nothing is kept, nothing is said
        p.wait_for_timeout(5000)
        assert MODEL.notes == [] and b.effects() == [] and _on_disk() == []
        assert not (server.fx_home().exists() and any(server.fx_home().iterdir())), list(server.fx_home().iterdir())
        assert p.locator("#fx .fxcard").count() == 0
        assert not any("failed" in t or "a proposal" in t for t in b.toasts()), b.toasts()

        # Ctrl+Shift+Z: never made, so asked again — free on the plan — and a design lands
        MODEL.hold_s = 0
        calls = MODEL.calls
        b.redo()
        p.wait_for_function("window.__toasts.some((t) => t.startsWith('redone: AI: effect'))", timeout=5000)
        p.wait_for_timeout(1500)                      # where the judge read it
        redone = [t for t in b.toasts() if t.startswith("redone: AI: effect")]
        # the timeline's own "redone: AI: effect" comes first; this tool's line replaces it,
        # and is the last word: never a bare "redone"
        assert redone[-1] == ("redone: AI: effect — asks the designer again · free on your plan"
                              " — review it in Designed Effects"), redone
        assert p.locator("[data-testid=redo-ask-confirm][open]").count() == 0
        got = _until(p, lambda: [e for e in b.effects() if e["status"] == "proposed"] or None, timeout=30,
                     what="the asked-again design to land")
        assert [e["shot"] for e in got] == [A], got
        p.wait_for_selector(f"#fx .fxcard[data-id='{got[0]['id']}']", timeout=8000)
        assert MODEL.notes == ["a thin white border"] and MODEL.calls == calls + 1, (MODEL.notes, MODEL.calls, calls)
        b.designs_idle()
        assert [(e["id"], e["status"]) for e in _on_disk()] == [(got[0]["id"], "proposed")]
    finally:
        MODEL.hold_s = 0
        b.close()


def test_on_the_api_a_redo_that_must_ask_again_shows_its_price_first(browser, live_server, project, monkeypatch):
    """A request taken back before its design was asked has nothing to restore, so its redo
    asks the designer again — and on `anthropic_api`, where that bills, the price comes
    first in a question (REDESIGN-GOAL section S; dimension 13: a priced step is "never run
    unasked"). "Don't ask" keeps the redo without the call; a later redo asks again and
    "Ask" makes the one call. The design that did land (after its Ctrl+Z) comes back as it
    was, with no call, either way."""
    monkeypatch.setenv("ROUGHCUT_BACKEND", "anthropic_api")
    b = Board(browser, live_server, project)
    try:
        p = b.page
        GATE.clear()
        b.ask([{"op": "design_effect", "id": A, "note": "a big white KILLINGTON title"},
               {"op": "design_effect", "id": B, "note": "the KILLINGTON title carries on"}],
              summary="a KILLINGTON title", text="title the first two shots")
        b.accept()
        p.wait_for_function("dock.current() === 'fx'", timeout=8000)
        p.wait_for_function("fx.state.busy && fx.state.busy.fx_kind === 'design'", timeout=15000)
        # taken back while the first is with the designer and the second not asked yet — and
        # the first lands before its cancel reaches the server (held here): the cancel answers
        # "done", and that design is set aside, not lost
        import server
        held = []
        p.route("**/api/fx/cancel", lambda route: held.append(route))
        b.undo()
        _until(p, lambda: held, what="the cancel to be sent")
        GATE.set()
        _until(p, lambda: [j for j in server.FX.values() if j["fx_kind"] == "design" and j["state"] == "done"],
               what="the held design to land")
        held[0].continue_()
        p.wait_for_function("window.__toasts.some((t) => t.includes('the 2 designs are discarded'))", timeout=5000)
        p.unroute("**/api/fx/cancel")
        b.designs_idle()
        assert MODEL.notes == ["a big white KILLINGTON title"], MODEL.notes
        _until(p, lambda: [e["status"] for e in _on_disk()] == ["withdrawn"], what="the landed design set aside")
        assert b.effects() == []
        [aside] = _on_disk()
        assert aside["status"] == "withdrawn" and aside["shot"] == A
        calls = MODEL.calls
        price = p.evaluate(f"fetch('/api/fx/price?place=1&shot={B}').then((r) => r.json())")
        assert price["charged"] is True and price["usd"] > 0
        dollars = f"${price['usd']:.2f}"
        dlg = "[data-testid=redo-ask-confirm]"

        # Ctrl+Shift+Z: the landed one back; the price of the other on screen, nothing asked
        b.redo()
        p.wait_for_selector(f"{dlg}[open]", timeout=8000)
        text = " ".join(p.locator(dlg).inner_text().split())
        assert "Redo asks the designer again" in text, text
        assert f"about {dollars}, billed to the API key" in text, text
        assert "1 design came back as it was, with no call." in text, text
        assert "free on your plan" not in text, text
        assert p.locator(f"{dlg} [data-testid=redo-ask-ok]").inner_text().strip() == f"Ask · ~{dollars}"
        got = _until(p, lambda: b.effects() or None, what="the landed design to come back")
        assert [(e["id"], e["status"]) for e in got] == [(aside["id"], "proposed")], got
        p.wait_for_timeout(1500)
        assert MODEL.calls == calls, "a call ran while its price was still a question"
        # Don't ask: the redo stands, the other design is not asked for
        p.locator(f"{dlg} [data-ans=no]").click()
        p.wait_for_function("window.__toasts.some((t) => t === 'redone: AI: 2 effects — 1 design back · 1 design not"
                            " asked again — review it in Designed Effects')", timeout=5000)
        assert p.locator(f"{dlg}[open]").count() == 0
        p.wait_for_timeout(1500)
        assert MODEL.calls == calls and [e["id"] for e in b.effects()] == [aside["id"]]

        # once more, and this time Ask: one design call, the landed one still the same
        b.undo()
        _until(p, lambda: b.effects() == [], what="the design to be set aside")
        b.redo()
        p.wait_for_selector(f"{dlg}[open]", timeout=8000)
        assert MODEL.calls == calls
        p.locator(f"{dlg} [data-testid=redo-ask-ok]").click()
        p.wait_for_function("window.__toasts.some((t) => t.startsWith('redone: AI: 2 effects — 1 design back · asks the"
                            f" designer again for 1 · ~{dollars}'))", timeout=5000)
        _until(p, lambda: len([e for e in b.effects() if e["status"] == "proposed"]) == 2, timeout=30,
               what="the second design")
        b.designs_idle()
        got = {e["shot"]: e["id"] for e in b.effects()}
        assert got[A] == aside["id"] and got[B] != aside["id"], got
        assert MODEL.notes == ["a big white KILLINGTON title", "the KILLINGTON title carries on"], MODEL.notes
    finally:
        GATE.set()
        b.close()


# ---------------------------------------------------------------- punch 6

SCAN = """() => {
  const re = /\\bg[0-9a-f]{10}\\b/;
  const out = [];
  const look = (where, el) => {
    if (!el) return;
    const text = el.innerText || el.textContent || '';
    if (re.test(text)) out.push(`${where}: ${text.match(re)[0]} in “${text.slice(0, 200)}”`);
    for (const n of [el, ...el.querySelectorAll('[title], [aria-label]')]) {
      for (const a of ['title', 'aria-label']) {
        const v = n.getAttribute && n.getAttribute(a);
        if (v && re.test(v)) out.push(`${where} [${a}]: ${v.slice(0, 200)}`);
      }
    }
  };
  look('the Assistant', document.querySelector('#tools section[data-tool="ask"]'));
  look('the AI status', document.querySelector('[data-testid="ai-status"]'));
  look('the answer', document.querySelector('.aip-pop'));
  look('Things to check', document.querySelector('#proposalChecks'));
  look('the proposal', document.querySelector('#proposal'));
  look('Designed Effects', document.querySelector('#fx'));
  return out;
}"""


def _scan(page) -> list[str]:
    page.wait_for_timeout(150)
    return page.evaluate(SCAN)


def test_no_shot_id_reaches_the_page_from_the_model(browser, live_server, project):
    """A scripted answer whose every sentence carries ids, through the real server: the
    review, the line and its hover, the designed effect's card — no id anywhere."""
    b = Board(browser, live_server, project)
    try:
        p = b.page
        b.ask([{"op": "move", "ids": [C], "before": A},
               {"op": "design_effect", "id": A, "note": f"a KILLINGTON title over shot {A} until the cut to {B}"},
               {"op": "trim", "id": GHOST, "out": 1.0}],
              summary=f"The last shot ({C}) then moves to the start of the film; a title on {A}",
              text="move the last shot first and title it")
        review = p.locator("[data-testid=ai-review]").inner_text()
        assert "The last shot (shot 3) then moves to the start of the film" in review, review
        assert "shot 1 (CLIP_A) until the cut to shot 2 (CLIP_B)" in review, review
        assert _scan(p) == []
        b.accept()
        p.wait_for_function("dock.current() === 'fx'", timeout=8000)
        assert _scan(p) == []
        end = time.time() + 15
        while not b.effects() and time.time() < end:
            p.wait_for_timeout(200)
        b.designs_idle()
        # the card, its why opened: the model's words said as the editor reads them
        p.evaluate(f"tl.select(['{A}'], {{source: 'api'}})")
        p.wait_for_selector("#fx .fxcard button[data-act=why]", timeout=8000)
        p.locator("#fx .fxcard button[data-act=why]").first.click()
        card = p.locator("#fx .fxcard").first.inner_text()
        assert "the continuation into shot " in card and "matching effect" in card, card
        assert _scan(p) == []
        # a question answered in words, the answer open
        MODEL.answer = {"kind": "answer", "text": f"Shot {B} is the brightest; {GHOST} is no longer in the cut"}
        p.locator("#tl").first.click(position={"x": 5, "y": 5})
        p.keyboard.press("Control+j")
        p.keyboard.type("which is brightest?")
        p.keyboard.press("Enter")
        p.wait_for_selector(".aip-pop:not([hidden])", timeout=15000)
        # numbered on the cut as it is now: the accepted move put shot 3 first, so B is third
        assert "Shot 3 (CLIP_B) is the brightest; a shot is no longer in the cut" in p.locator(".aip-pop").inner_text()
        assert _scan(p) == []
    finally:
        b.close()


def test_the_board_names_ids_the_server_left_in(browser, live_server, project):
    """The board's own guard (app.js `clipNamed`): the server's words faked to carry raw
    ids — the ops review, the line's hover, Things to check, a Designed Effects card."""
    b = Board(browser, live_server, project)
    try:
        p = b.page
        raw = {"status": "done", "kind": "ops", "summary": f"The last shot ({C}) then moves to the start; {A} follows",
               "ops": [{"op": "move", "ids": [C], "before": A}],
               "lines": [f"Move shot {C} before {A}"], "index": [0], "refused": [{"i": 1, "op": "trim",
                                                                                 "why": f"shot {GHOST} is not in the cut"}],
               "families": ["timeline"], "cost": None}
        p.route("**/api/assist/*", lambda r: r.fulfill(status=200, content_type="application/json",
                                                        body=json.dumps({**raw, "job": "0123456789ab"}))
                if r.request.method == "GET" and "/price" not in r.request.url else r.continue_())
        p.route("**/api/assist", lambda r: r.fulfill(status=200, content_type="application/json",
                                                     body=json.dumps({"job": "0123456789ab", "charged": False}))
                if r.request.method == "POST" else r.continue_())
        p.route("**/api/assist/check", lambda r: r.fulfill(status=200, content_type="application/json",
                                                           body=json.dumps({"ops": raw["ops"], "lines": raw["lines"],
                                                                            "index": [0], "refused": []})))
        p.locator("#tl").first.click(position={"x": 5, "y": 5})
        p.keyboard.press("Control+j")
        p.keyboard.type("move it")
        p.keyboard.press("Enter")
        p.wait_for_function(f"document.querySelector('{STATUS}').dataset.state === 'done'", timeout=15000)
        p.wait_for_selector("[data-testid=ai-review] [data-testid=ai-op-row]")
        review = p.locator("[data-testid=ai-review]").inner_text()
        assert "The last shot (shot 3) then moves to the start; shot 1 (CLIP_A) follows" in review, review
        assert _scan(p) == [], _scan(p)
        title = p.locator(RESULT).get_attribute("title") or ""
        assert "(shot 3)" in title and not SHOT_ID.search(title), title
        # Things to check, the model's words carrying ids
        plan = {"segments": [{"clip": s["clip"], "in": s["in"], "out": s["out"], "why": "kept"} for s in SEGS],
                "notes": f"Kept {A} as the opener.",
                "checks": [{"text": f"Whether {B} is too long a pause before shot {C}", "clip": "CLIP_B.MP4",
                            "in": 0.0, "out": 2.0, "kind": "model", "shot": 1}]}
        p.evaluate("rcActions.close()")
        p.evaluate("(pl) => rcShowProposal(pl)", plan)
        p.wait_for_selector("#proposalChecks li", state="visible", timeout=10000)
        checks = p.locator("#proposalChecks").inner_text()
        assert "Whether shot 2 (CLIP_B) is too long a pause before shot 3 (CLIP_C)" in checks, checks
        assert _scan(p) == [], _scan(p)
        # a Designed Effects card whose words the server left raw
        card = {"id": "fx_raw00001", "shot": A, "clip": "CLIP_A.MP4", "name": f"title on {A}", "status": "proposed",
                "note": "a title", "why": f"the continuation into shot {B} needs its own matching effect",
                "limits": f"cannot reach ({C})", "events": [{"t": 1.5, "x": 0.5, "y": 0.5, "label": f"on {A}"}],
                "overlay": TITLE["overlay"], "sound": None,
                "verify": {"ok": True, "checks": [{"key": "in_shot", "label": "in", "ok": True, "detail": ""}]}}
        p.route("**/api/fx", lambda r: r.fulfill(status=200, content_type="application/json",
                                                 body=json.dumps({"effects": [card]}))
                if r.request.method == "GET" else r.continue_())
        p.evaluate("dock.open('fx')")
        p.evaluate(f"tl.select(['{A}'], {{source: 'api'}})")
        p.evaluate("fx.refresh()")
        p.wait_for_selector("#fx .fxcard[data-id='fx_raw00001']", timeout=8000)
        p.locator("#fx .fxcard[data-id='fx_raw00001'] button[data-act=why]").click()
        text = p.locator("#fx .fxcard[data-id='fx_raw00001']").inner_text()
        assert "title on shot 1 (CLIP_A)" in text and "the continuation into shot 2 (CLIP_B)" in text, text
        assert "(shot 3)" in text, text
        assert _scan(p) == [], _scan(p)
    finally:
        b.close()
