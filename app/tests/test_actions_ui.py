"""The AI does what the pro tools do, as reviewable edits (redesign wave 12, lane AIOPS;
REDESIGN-GOAL dimension 15, Karl's K3 d).

In real Chromium on the fixture bin, with a scripted model behind `POST /api/assist`
(never a real call): for each family of the pro tools — Timeline, Effect Controls,
Lumetri, Transitions, Designed Effects, Export — the AI's op list goes through the review
panel (`rcActions.review`) and its Accept, and the EDL that lands on disk is the one the
same edit made by hand (the `tl.*` / `effects.*` / colour-pane call the UI makes) lands.
Accept is exactly one Ctrl+Z back; Reject leaves the EDL byte for byte as it was; an
export runs only once accepted; and Designed Effects' edits to the cut undo with one
Ctrl+Z (they used to clear the undo history)."""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from roughcut import config, fx, inference  # noqa: E402

SEGS = [{"id": "shot-a", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
        {"id": "shot-b", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"},
        {"id": "shot-c", "clip": "CLIP_C.MP4", "in": 2.0, "out": 5.0, "why": "third"}]


class Model:
    """The assist call answers with `answer`; anything else falls back ("{}")."""
    name = "actions-ui-scripted"

    def __init__(self):
        self.answer = {"kind": "answer", "text": "ok"}

    def complete(self, request):
        text = json.dumps(self.answer) if "kind" in (request.schema or {}) else "{}"
        model = config.model_for(request.role)
        return inference.Result(content=text, input_tokens=4000, output_tokens=300, backend=self.name,
                                model=model, projected_usd=config.projected_usd(model, 4000, 300),
                                latency_ms=1, raw=text)


MODEL = Model()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed(project) -> None:
    body = {"variant": "T", "title": "test cut", "orient": "none", "story": "", "target_s": [5, 20],
            "segments": [dict(s) for s in SEGS]}
    Path(project["edl"]).write_text(json.dumps(body, indent=1), encoding="utf-8")


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
    srv.should_exit = True
    thread.join(timeout=10)
    inference.set_backend(None)
    project["edl"].write_text(original, encoding="utf-8")


@pytest.fixture(scope="module")
def browser(live_server):
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        yield b
        b.close()


class Board:
    """One fresh page on a freshly seeded EDL."""

    def __init__(self, browser, url, project):
        _seed(project)
        self.project = project
        self.ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        self.page = self.ctx.new_page()
        self.errors: list[str] = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.goto(url)
        self.page.wait_for_selector("#tl .blk")
        self.page.wait_for_function(
            "window.rcActions && window.effects && window.tlLanes && typeof C !== 'undefined' && C && C.shots",
            timeout=10000)

    def close(self):
        errs = list(self.errors)
        self.ctx.close()
        assert errs == [], errs

    # ---- the disk
    def raw(self) -> bytes:
        return Path(self.project["edl"]).read_bytes()

    def settle(self) -> dict:
        """Every save the board has waiting written, then the EDL as it is on disk."""
        self.page.evaluate("""async () => {
          for (let k = 0; k < 3; k++) {
            await roughcutFlush();
            if (tl.saveSeq) await tl.saveSeq();
            await new Promise((r) => setTimeout(r, 60));
          }
        }""")
        return json.loads(self.raw())

    # ---- the AI's path: ask → review → accept
    def ask(self, ops, summary="the edit", text="do it") -> dict:
        MODEL.answer = {"kind": "ops", "summary": summary, "ops": ops}
        res = self.page.evaluate("""async (text) => {
          await roughcutFlush();
          const r = await (await fetch('/api/assist', {method: 'POST', headers: {'content-type': 'application/json'},
            body: JSON.stringify({text, scope: {kind: 'sequence', playhead: tl.state.playhead}})})).json();
          for (;;) {
            const d = await (await fetch('/api/assist/' + r.job)).json();
            if (d.status !== 'running') return d;
            await new Promise((z) => setTimeout(z, 50));
          }
        }""", text)
        assert res["status"] == "done", res
        return res

    def review(self, res) -> None:
        self.page.evaluate("(r) => rcActions.review(r)", res)
        self.page.wait_for_selector("[data-testid=ai-review] [data-testid=ai-op-row]")
        self.page.wait_for_function("document.querySelector('[data-testid=ai-accept-all]') !== null")

    def accept(self) -> None:
        self.page.locator("[data-testid=ai-accept-all]").click()
        self.page.wait_for_function("!rcActions.open")

    def undo(self) -> None:
        self.page.evaluate("document.activeElement && document.activeElement.blur()")
        self.page.keyboard.press("Control+z")


def norm(edl: dict) -> dict:
    """The EDL with every id that is minted (a new shot's, a V2 clip's) replaced by its
    place, so two runs that made the same edit compare equal."""
    ids = {s["id"]: f"S{i}" for i, s in enumerate(edl.get("segments") or [])}
    out = json.loads(json.dumps(edl))
    for s in out.get("segments") or []:
        s["id"] = ids.get(s["id"], "?")
    for t in out.get("transitions") or []:
        for k in ("a", "b"):
            if t.get(k) is not None:
                t[k] = ids.get(t[k], "?")
    col = out.get("colour") or {}
    if col.get("shots"):
        col["shots"] = {ids.get(k, k): v for k, v in col["shots"].items()}
    if col.get("reference"):
        col["reference"] = ids.get(col["reference"], col["reference"])
    for o in out.get("overlays") or []:
        o.pop("id", None)
    for k in ("selects", "floor", "accepted"):
        out.pop(k, None)
    return out


# Verdict 9, punch 3: an AI accept's undo entry is a short op summary ("undo: AI: trim +
# move"), at most 40 characters with the key, made of whole words — never the model's
# sentence cut mid-word ("undo: AI: Shot 1 is only 1.92 s now, so 'about 3 s' means").
UNDO_WORDS = set("""AI: + more edit edits insert inserts overwrite overwrites move moves razor cut cuts
ripple delete deletes trim trims roll rolls slip slips speed changes change marker markers In/Out
new track tracks overlay overlays keyframes keyframe sets colour transition transitions fade fades
effect effects export exports opacity volume motion""".split())


def check_undo_title(title: str) -> str:
    """The header's undo title for an AI accept: ≤ 40 characters, "undo: AI: … (Ctrl+Z)",
    every word of its label one of the op vocabulary's (so none is cut). Returns the label."""
    assert len(title) <= 40, (len(title), title)
    assert title.startswith("undo: AI: ") and title.endswith(" (Ctrl+Z)"), title
    label = title[len("undo: "):-len(" (Ctrl+Z)")]
    for w in label.split(" "):
        assert w.isdigit() or w in UNDO_WORDS, (w, title)
    return label


def ai_then_hand(browser, url, project, ops, hand_js, *, extra=None):
    """The AI's ops reviewed and accepted, against the same edit made by hand, each on a
    fresh board on the same seed. Returns (ai, hand, seeded) normalised EDLs and checks
    that one Ctrl+Z takes the AI's accept back whole."""
    b = Board(browser, url, project)
    try:
        seeded = norm(b.settle())
        res = b.ask(ops)
        assert res["refused"] == [], res["refused"]
        b.review(res)
        assert b.page.locator("[data-testid=ai-op-row]").count() == len(ops)
        b.accept()
        if extra:
            extra(b)
        ai = norm(b.settle())
        assert ai != seeded, "the AI's accept changed nothing"
        check_undo_title(b.page.locator("#undo").get_attribute("title") or "")
        b.undo()
        back = norm(b.settle())
        assert back == seeded, "one Ctrl+Z did not take the whole accept back"
    finally:
        b.close()
    h = Board(browser, url, project)
    try:
        h.page.evaluate(hand_js)
        hand = norm(h.settle())
    finally:
        h.close()
    return ai, hand, seeded


# ---------------------------------------------------------------- one test per family

def test_timeline_razor_trim_speed_and_a_transition_on_the_new_half(browser, live_server, project):
    ops = [{"op": "razor", "id": "shot-c", "at": 5.5},
           {"op": "transition", "a": "shot-c", "b": "new:1", "kind": "cross_dissolve", "dur": 0.5},
           {"op": "trim", "id": "shot-a", "in": 1.5},
           {"op": "speed", "id": "shot-b", "rate": 0.5}]
    hand = """() => {
      const second = tl.split('shot-c', 5.5);                          // the Razor tool's click
      tl.addTransition(tl.indexOf('shot-c'), 'cross_dissolve', 0.5);    // the Effects bin's drop
      tl.setRange('shot-a', 1.5, null);                                  // a ripple trim
      tl.setSpeed('shot-b', 0.5);                                        // Speed in Effect Controls
      return second;
    }"""
    ai, h, seeded = ai_then_hand(browser, live_server, project, ops, hand)
    assert ai == h
    assert len(ai["segments"]) == 4 and ai["transitions"][0]["kind"] == "cross_dissolve"
    assert ai["segments"][1]["speed"] == 0.5


def test_timeline_insert_overwrite_and_append(browser, live_server, project):
    ops = [{"op": "insert", "clip": "CLIP_C.MP4", "in": 0.0, "out": 1.0, "at": 1.0},
           {"op": "overwrite", "clip": "CLIP_B.MP4", "in": 3.0, "out": 4.0, "at": 5.0},
           {"op": "append", "clip": "CLIP_A.MP4", "in": 4.0, "out": 5.0}]
    hand = """() => {
      tl.insertAt({clip: 'CLIP_C.MP4', in: 0, out: 1}, 1.0);            // Insert (,)
      tl.overwrite({clip: 'CLIP_B.MP4', in: 3, out: 4}, 5.0);           // Overwrite (.)
      tl.insert({clip: 'CLIP_A.MP4', in: 4, out: 5}, null);             // a drop past the end
    }"""
    ai, h, _ = ai_then_hand(browser, live_server, project, ops, hand)
    assert ai == h
    assert [s["clip"] for s in ai["segments"]][-1] == "CLIP_A.MP4"


def test_timeline_move_ripple_delete_roll_and_slip(browser, live_server, project):
    ops = [{"op": "roll", "a": "shot-a", "b": "shot-b", "by": 0.5},
           {"op": "slip", "id": "shot-c", "by": -1.0},
           {"op": "move", "ids": ["shot-c"], "before": "shot-a"},
           {"op": "ripple_delete", "ids": ["shot-b"]}]
    hand = """() => {
      // the trim lane's roll (timeline-trim.js step): the two edges, one gesture
      tl.begin('roll');
      tl.setRange('shot-a', null, 3.0 + 0.5); tl.setRange('shot-b', 0.0 + 0.5, null);
      tl.commit();
      tl.begin('slip');                                                  // the slip tool
      tl.setRange('shot-c', 2.0 - 1.0, 5.0 - 1.0);
      tl.commit();
      tl.move(['shot-c'], 'shot-a');                                     // a drag on V1
      tl.remove(['shot-b']);                                             // ripple delete
    }"""
    ai, h, _ = ai_then_hand(browser, live_server, project, ops, hand)
    assert ai == h
    assert [(s["clip"], s["in"], s["out"]) for s in ai["segments"]] == [
        ("CLIP_C.MP4", 1.0, 4.0), ("CLIP_A.MP4", 1.0, 3.5)]


def test_timeline_markers_in_out_track_and_overlay(browser, live_server, project):
    ops = [{"op": "marker", "at": 2.5},
           {"op": "in_out", "in": 1.0, "out": 5.0},
           {"op": "track_add", "kind": "video"},
           {"op": "overlay", "clip": "CLIP_C.MP4", "in": 0.0, "out": 1.5, "at": 3.0, "track": "V3"}]
    hand = """() => {
      tl.addMarker(2.5);                                                 // M
      tl.setSeqIn(1.0); tl.setSeqOut(5.0);                               // I, O
      tl.addTrack('video');                                              // Sequence › Add Video Track
      tl.addOverlay({clip: 'CLIP_C.MP4', in: 0, out: 1.5}, 3.0, 'V3');   // a drop on V3
    }"""
    ai, h, seeded = ai_then_hand(browser, live_server, project, ops, hand)
    assert ai == h
    assert ai["markers"] == [2.5] and ai["in"] == 1.0 and ai["out"] == 5.0
    assert ai["overlays"][0]["track"] == "V3"


def test_a_marker_only_answer_is_one_ctrl_z_too(browser, live_server, project):
    """A list that only moves markers is one undo entry like any other (markers and In /
    Out are on the timeline's undo stack since lane AIGLUE): one accept, one Ctrl+Z, and
    Ctrl+Shift+Z brings it back."""
    b = Board(browser, live_server, project)
    try:
        seeded = b.settle()
        b.review(b.ask([{"op": "marker", "at": 1.0}, {"op": "marker", "at": 4.0}]))
        b.accept()
        assert b.settle()["markers"] == [1.0, 4.0]
        b.undo()
        b.page.wait_for_function("tl.markers().length === 0")
        assert b.settle().get("markers", []) == seeded.get("markers", [])
        b.page.keyboard.press("Control+Shift+z")
        b.page.wait_for_function("tl.markers().length === 2")
    finally:
        b.close()


def test_effect_controls_values_and_keyframes(browser, live_server, project):
    ops = [{"op": "set", "id": "shot-a", "prop": "scale", "value": 125},
           {"op": "set", "id": "shot-a", "prop": "posx", "value": 1200},
           {"op": "set", "id": "shot-b", "prop": "volume_db", "value": -6},
           {"op": "keys", "id": "shot-c", "prop": "opacity", "keys": [[0.0, 100], [2.0, 0]]}]
    hand = """() => {
      const seg = (id) => tl.byId(id);
      effects.set('scale', 125, seg('shot-a'));                          // a typed value
      effects.set('posx', 1200, seg('shot-a'));
      effects.set('volume_db', -6, seg('shot-b'));
      // the stopwatch: select the shot, playhead on the frame, stopwatch, type the value
      tl.select(['shot-c'], {source: 'click'});
      const s = tl.filmStart('shot-c');
      tl.seek(s + 0.0); effects.animate('opacity'); effects.set('opacity', 100, seg('shot-c'));
      tl.seek(s + 2.0); effects.set('opacity', 0, seg('shot-c'));
    }"""
    ai, h, _ = ai_then_hand(browser, live_server, project, ops, hand)
    assert ai == h
    c = ai["segments"][2]["controls"]
    assert c["keys"]["opacity"] == [[0, 100], [2, 0]]
    assert ai["segments"][0]["controls"]["scale"] == 125


def test_lumetri_basic_curves_wheels_hsl_look_and_match(browser, live_server, project):
    ops = [{"op": "set", "id": "shot-a", "prop": "temperature", "value": 30},
           {"op": "set", "id": "shot-a", "prop": "exposure", "value": 0.25},
           {"op": "set", "id": "shot-a", "prop": "contrast", "value": 20},
           {"op": "curve", "id": "shot-b", "channel": "master", "points": [[0, 0], [0.5, 0.6], [1, 1]]},
           {"op": "wheel", "id": "shot-b", "range": "shadows", "x": 0.1, "y": -0.05, "level": 10},
           {"op": "set", "id": "shot-b", "prop": "hsl_shift", "value": 30},
           {"op": "look", "ids": "all", "name": "alpine", "strength": 0.6},
           {"op": "look", "ids": ["shot-c"], "name": "crisp"},
           {"op": "match", "ids": ["shot-c"], "to": "reference", "reference": "shot-a"}]
    hand = """() => {
      const seg = (id) => tl.byId(id);
      effects.set('temperature', 30, seg('shot-a'));                     // Lumetri › Basic
      effects.set('exposure', 0.25, seg('shot-a'));
      effects.set('contrast', 20, seg('shot-a'));
      effects.curve('master', [[0, 0], [0.5, 0.6], [1, 1]], seg('shot-b'));   // Curves
      effects.wheel('shadows', [0.1, -0.05, 10], seg('shot-b'));         // Color Wheels
      effects.set('hsl_shift', 30, seg('shot-b'));                       // HSL Secondary
      // the film's look and strength with nothing selected; the shot's own look
      colourEdit('film look', () => { colour.look = 'alpine'; });
      colourEdit('look strength', () => { colour.strength = 0.6; });
      colourEdit('look', () => { shotOverride('shot-c', true).look = 'crisp'; });
      // set as reference, then match to it (the colour pane's two buttons)
      colourEdit('reference', () => { colour.reference = 'shot-a'; });
      colourEdit('match', () => { shotOverride('shot-c', true).match = 'reference'; });
    }"""
    ai, h, _ = ai_then_hand(browser, live_server, project, ops, hand)
    assert ai == h
    assert ai["colour"]["look"] == "alpine" and ai["colour"]["shots"]["S2"]["look"] == "crisp"
    assert ai["segments"][1]["controls"]["curves"]["master"] == [[0, 0], [0.5, 0.6], [1, 1]]


def test_transitions_and_fades(browser, live_server, project):
    ops = [{"op": "transition", "a": "shot-a", "b": "shot-b", "kind": "dip_to_black", "dur": 0.5},
           {"op": "fade", "id": "shot-c", "edge": "end", "kind": "dip_to_black", "dur": 1.0},
           {"op": "fade", "id": "shot-a", "edge": "start", "kind": "audio_crossfade", "dur": 0.5}]
    hand = """() => {
      tl.addTransition(0, 'dip_to_black', 0.5);                          // dropped on the edit
      tl.addFade(2, 'end', 'dip_to_black', 1.0);                         // on the clip's end
      tl.addFade(0, 'start', 'audio_crossfade', 0.5);
    }"""
    ai, h, _ = ai_then_hand(browser, live_server, project, ops, hand)
    assert ai == h
    assert sorted(t["kind"] for t in ai["transitions"]) == ["audio_crossfade", "dip_to_black", "dip_to_black"]


def _design_route(board, sent):
    def design(route):
        sent.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"job": "fxjob0001"}))
    board.page.route("**/api/fx/design", design)


def _wait(page, cond, timeout=20.0):
    """Poll `cond` while the page's events run (a route handler only runs inside a
    Playwright call: time.sleep here would starve it)."""
    end = time.time() + timeout
    while not cond() and time.time() < end:
        page.wait_for_timeout(100)
    return cond()


def test_designed_effects_go_to_the_designer_with_the_note(browser, live_server, project):
    """Accepting a design_effect op opens Designed Effects on that shot with the note and
    presses Design (the designer's own proposal and review follow) — the request the same
    note typed there and Design pressed by hand sends. It changes nothing in the cut by
    itself: no undo entry, the EDL untouched."""
    b = Board(browser, live_server, project)
    try:
        before = b.raw()
        ai: list = []
        _design_route(b, ai)
        b.review(b.ask([{"op": "design_effect", "id": "shot-b", "note": "hit markers on the rocks"}]))
        assert b.page.locator("[data-testid=ai-op-row]").get_attribute("data-op") == "design_effect"
        b.accept()
        b.page.wait_for_function("dock.current() === 'fx'", timeout=8000)
        assert _wait(b.page, lambda: ai), "Design was never pressed"
        assert b.page.evaluate("fx.state.shot") == "shot-b"
        assert b.page.locator("#undo").is_disabled()
        assert b.raw() == before
    finally:
        b.close()
    h = Board(browser, live_server, project)
    try:
        hand: list = []
        _design_route(h, hand)
        p = h.page
        p.locator("#tl .blk").nth(1).click()                          # the shot
        p.evaluate("dock.open('fx')")                                 # Designed Effects
        p.wait_for_function("fx.state.shot === 'shot-b'")
        p.locator("#fxNote").fill("hit markers on the rocks")         # the note, typed
        p.wait_for_function("!document.querySelector('#fxDesign').disabled", timeout=15000)
        p.locator("#fxDesign").click()                                # Design
        assert _wait(p, lambda: hand)
    finally:
        h.close()
    assert ai[0] == hand[0], (ai[0], hand[0])
    assert ai[0]["shot"] == "shot-b" and ai[0]["note"] == "hit markers on the rocks"


def _export_routes(board, renders):
    board.page.route("**/api/export/sample", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps({"state": "failed"})))

    def render(route):
        if route.request.method == "POST":
            renders.append(json.loads(route.request.post_data or "{}"))
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"job": "rjob0001", "planned_s": 7.0, "path": "/x/review copy.mp4"}))
        else:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"state": "cancelled"}))
    board.page.route("**/api/render", render)
    board.page.route("**/api/render/rjob0001", render)


def test_an_export_runs_only_when_accepted(browser, live_server, project):
    """The review says what an export will write and nothing runs; Reject runs nothing;
    Accept starts the Export dialog's own export with the preset and the name — the same
    request the dialog sends when a person picks them and presses Export."""
    import server
    b = Board(browser, live_server, project)
    try:
        ai: list = []
        _export_routes(b, ai)
        files_before = sorted(p.name for p in Path(server.STATE["renders"]).parent.rglob("*.mp4"))
        res = b.ask([{"op": "export", "preset": "youtube", "range": "all", "name": "review copy"}])
        b.review(res)
        b.page.wait_for_function("(document.querySelector('[data-testid=ai-export-what]') || {}).textContent"
                                 " && document.querySelector('[data-testid=ai-export-what]').textContent.includes('review copy.mp4')")
        b.page.wait_for_timeout(500)
        assert ai == []
        assert not b.page.evaluate("document.getElementById('exportDialog').open")
        b.page.locator("[data-testid=ai-reject-all]").click()          # rejected: still nothing
        b.page.wait_for_timeout(300)
        assert ai == []
        b.review(res)                                                  # accepted: it runs
        b.accept()
        assert _wait(b.page, lambda: ai), "the export never started"
        assert ai[0]["preset"] == "youtube" and ai[0]["name"] == "review copy"
        assert len(ai[0]["segments"]) == 3
        assert sorted(p.name for p in Path(server.STATE["renders"]).parent.rglob("*.mp4")) == files_before
    finally:
        b.close()
    h = Board(browser, live_server, project)
    try:
        hand: list = []
        _export_routes(h, hand)
        p = h.page
        p.evaluate("exportDialog.open()")                              # Ctrl+M
        p.wait_for_selector("#exportDialog #exPreset")
        p.select_option("#exportDialog #exPreset", "youtube")
        p.locator("#exportDialog #exName").fill("review copy")
        p.wait_for_function("!document.querySelector('#exportDialog #exGo').disabled", timeout=20000)
        p.locator("#exportDialog #exGo").click()
        assert _wait(p, lambda: hand)
    finally:
        h.close()
    assert ai[0] == hand[0], (ai[0], hand[0])


# ---------------------------------------------------------------- the review itself

def test_reject_leaves_the_edl_byte_for_byte(browser, live_server, project):
    """With the ghost up (a razor), the Program showing After (a colour change), and a
    verdict flipped, Reject all puts everything back and writes nothing."""
    b = Board(browser, live_server, project)
    try:
        b.settle()
        time.sleep(0.3)
        before = b.raw()
        mtime = Path(project["edl"]).stat().st_mtime_ns
        res = b.ask([{"op": "razor", "id": "shot-a", "at": 1.0},
                     {"op": "set", "id": "shot-b", "prop": "exposure", "value": 0.4},
                     {"op": "set", "id": "shot-b", "prop": "opacity", "value": 40},
                     {"op": "marker", "at": 3.0}])
        b.review(res)
        b.page.wait_for_function("rcActions.previewing")
        b.page.wait_for_function("tlLanes.ghost() && tlLanes.ghost().ghosts.length === 4")
        assert b.page.evaluate("tl.byId('shot-b').controls.opacity") == 40      # After, in memory
        b.page.locator("[data-testid=ai-op-row][data-op-index='3'] [data-testid=ai-op-reject]").click()
        b.page.locator("[data-testid=ai-before-after]").click()                 # Before
        assert b.page.evaluate("tl.byId('shot-b').controls") is None
        b.page.locator("[data-testid=ai-before-after]").click()                 # After again
        b.page.wait_for_timeout(900)                                            # past any autosave
        b.page.locator("[data-testid=ai-reject-all]").click()
        b.page.wait_for_function("!rcActions.open")
        assert b.page.evaluate("tl.byId('shot-b').controls") is None
        b.page.wait_for_function("tlLanes.ghost() === null")             # the lane redraws next frame
        b.page.wait_for_timeout(1200)
        assert b.raw() == before
        assert Path(project["edl"]).stat().st_mtime_ns == mtime
        assert b.page.locator("#undo").is_disabled()
        b.page.wait_for_function("async (j) => !!(await (await fetch('/api/assist/' + j)).json()).answer",
                                 arg=res["job"])
        rec = b.page.evaluate("async (j) => (await fetch('/api/assist/' + j)).json()", res["job"])
        assert rec["answer"]["rejected"] == [0, 1, 2, 3] and rec["answer"]["accepted"] == []
    finally:
        b.close()


def test_partial_accept_takes_what_was_kept_and_its_dependants_follow(browser, live_server, project):
    b = Board(browser, live_server, project)
    try:
        seeded = norm(b.settle())
        res = b.ask([{"op": "razor", "id": "shot-a", "at": 1.0},
                     {"op": "speed", "id": "new:1", "rate": 0.5},
                     {"op": "set", "id": "shot-b", "prop": "opacity", "value": 40}])
        b.review(res)
        # rejecting the razor takes the op that names its half with it
        b.page.locator("[data-testid=ai-op-row][data-op-index='0'] [data-testid=ai-op-reject]").click()
        assert b.page.evaluate("rcActions.state().verdict") == [False, False, True]
        assert b.page.locator("[data-testid=ai-accept-all]").inner_text() == "Accept 1 of 3"
        b.accept()
        after = norm(b.settle())
        assert len(after["segments"]) == 3 and after["segments"][1]["controls"]["opacity"] == 40
        b.page.wait_for_function("async (j) => !!(await (await fetch('/api/assist/' + j)).json()).answer",
                                 arg=res["job"])
        rec = b.page.evaluate("async (j) => (await fetch('/api/assist/' + j)).json()", res["job"])
        assert rec["answer"]["accepted"] == [2] and rec["answer"]["rejected"] == [0, 1]
        b.undo()
        assert norm(b.settle()) == seeded
    finally:
        b.close()


def test_the_panel_reads_as_a_panel_beside_the_timeline(browser, live_server, project):
    """In the Assistant tab of the Effect Controls group, never over the Timeline; one row
    per op with its family, the asked words and the price in section S's words."""
    b = Board(browser, live_server, project)
    try:
        b.review(b.ask([{"op": "razor", "id": "shot-a", "at": 1.0},
                        {"op": "transition", "a": "shot-a", "b": "new:1", "kind": "cross_dissolve"},
                        {"op": "set", "id": "shot-b", "prop": "temperature", "value": 25},
                        {"op": "speed", "id": "ghost", "rate": 2}], text="cut the opener and warm shot 2"))
        p = b.page
        assert p.evaluate("dock.current()") == "ask"
        rev = p.locator("[data-testid=ai-review]").bounding_box()
        tlb = p.locator("#pTimeline").bounding_box()
        assert rev["x"] >= tlb["x"] + tlb["width"] - 1, (rev, tlb)            # beside, not over
        rows = p.locator("[data-testid=ai-op-row]")
        assert rows.count() == 3
        assert [rows.nth(i).get_attribute("data-op") for i in range(3)] == ["razor", "transition", "set"]
        assert rows.nth(0).inner_text().startswith("Razor shot 1 (CLIP_A) at 00:00:01:00")
        assert "Cross Dissolve between shot 1 (CLIP_A) and shot 1's second half" in rows.nth(1).inner_text()
        assert "speed not applied: shot ghost is not in the cut" in p.locator("[data-testid=ai-op-refused]").inner_text()
        assert "cut the opener and warm shot 2" in p.locator("[data-testid=ai-review]").inner_text()
        assert p.locator("[data-testid=ai-cost]").inner_text() == "free on your plan"
        assert p.locator("[data-testid=ai-accept-all]").inner_text() == "Accept all"
        assert p.evaluate("document.getElementById('ws').classList.contains('aitall')")
        # Esc rejects
        p.locator("[data-testid=ai-review] [data-testid=ai-op-accept]").first.focus()
        p.keyboard.press("Escape")
        p.wait_for_function("!rcActions.open")
        assert not p.evaluate("document.getElementById('ws').classList.contains('aitall')")
    finally:
        b.close()


def test_an_edit_elsewhere_takes_the_after_preview_down_first(browser, live_server, project):
    """The After picture is in memory only: a trim made while it is up starts from Before,
    and what the trim saves carries no preview."""
    b = Board(browser, live_server, project)
    try:
        b.review(b.ask([{"op": "set", "id": "shot-b", "prop": "opacity", "value": 30}]))
        b.page.wait_for_function("rcActions.previewing")
        # a click on the timeline (an edit begins there) ends the preview before it lands
        b.page.locator("#tl .blk").nth(0).click()
        assert not b.page.evaluate("rcActions.previewing")
        assert b.page.evaluate("tl.byId('shot-b').controls") is None
        b.page.evaluate("tl.setRange('shot-a', 1.25, null)")
        disk = b.settle()
        assert "controls" not in disk["segments"][1]
        assert disk["segments"][0]["in"] == 1.25
        # the review is still there to accept
        assert b.page.evaluate("rcActions.open")
        b.accept()
        assert b.settle()["segments"][1]["controls"]["opacity"] == 30
    finally:
        b.close()


def test_a_whole_cut_rewrite_opens_the_proposal(browser, live_server, project):
    b = Board(browser, live_server, project)
    try:
        def ask_job(route):
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "state": "done", "plan": {"segments": [dict(SEGS[1]), dict(SEGS[0])],
                                          "notes": "the second shot first"}}))
        b.page.route("**/api/ask/askjob01", ask_job)
        b.page.evaluate("(r) => rcActions.review(r)", {
            "job": "abcdef012345", "status": "done", "kind": "plan", "plan_ask": "askjob01",
            "summary": "re-cut it", "text_asked": "make it a teaser"})
        b.page.wait_for_selector("[data-testid=ai-plan]")
        b.page.wait_for_function("document.getElementById('proposal').style.display === 'block'", timeout=8000)
        assert not b.page.evaluate("rcActions.open")
    finally:
        b.close()


# ---------------------------------------------------------------- Designed Effects' edits undo

def test_designed_effects_edit_undoes_with_one_ctrl_z(browser, live_server, project):
    """An effect's edit to the cut (here a slow motion) is applied on the server and read
    back; that read-back is ONE undo entry now: Ctrl+Z puts the cut back as it was before
    Accept (the trim made earlier stays), Ctrl+Shift+Z brings the edit back."""
    import server
    b = Board(browser, live_server, project)
    try:
        p = b.page
        p.evaluate("tl.setRange('shot-a', 1.5, null)")
        before = b.settle()["segments"]
        fx.save(server.fx_home(), {
            "id": "fx_aiops01", "shot": "shot-b", "clip": "CLIP_B.MP4", "name": "slow motion",
            "note": "slow motion on the hit", "why": "", "events": [{"t": 1.1, "x": 0.5, "y": 0.5}],
            "status": "proposed", "edits": [{"op": "speed", "shot": "shot-b", "rate": 0.5,
                                             "from": 1.0, "to": 1.4}],
            "created": "2026-10-18T12:00:00"})
        p.evaluate("tl.select(['shot-b'], {source: 'click'})")
        p.evaluate("dock.open('fx')")
        p.evaluate("fx.refresh()")
        p.wait_for_selector("#fx .fxcard[data-id='fx_aiops01'] button[data-act=accept]")
        p.locator("#fx .fxcard[data-id='fx_aiops01'] button[data-act=accept]").click()
        p.wait_for_function("segs.some(s => s.speed === 0.5)")
        after = b.settle()["segments"]
        assert any(s.get("speed") == 0.5 for s in after) and len(after) == 5
        assert (p.locator("#undo").get_attribute("title") or "").startswith("undo: effect: slow motion")
        b.undo()
        p.wait_for_function("!segs.some(s => s.speed === 0.5)")
        back = b.settle()["segments"]
        assert [(s["clip"], s["in"], s["out"], s.get("speed")) for s in back] == \
            [(s["clip"], s["in"], s["out"], s.get("speed")) for s in before]
        assert back[0]["in"] == 1.5                                            # the trim stays
        p.keyboard.press("Control+Shift+z")
        p.wait_for_function("segs.some(s => s.speed === 0.5)")
        assert any(s.get("speed") == 0.5 for s in b.settle()["segments"])
    finally:
        b.close()


def test_the_program_shows_after_then_before(browser, live_server, project):
    """A colour op under review is on the Program monitor before it lands (After), ◐ shows
    the shot as it is (Before), and neither is saved."""
    b = Board(browser, live_server, project)
    try:
        p = b.page
        before_disk = b.raw()
        p.locator("#tl .blk").nth(1).click()
        p.evaluate("cueAt(1, segs[1].in + 0.5)")
        p.wait_for_function("liveVideo() && liveVideo().readyState >= 2 && grade.shot === 'shot-b' && grade.probe()",
                            timeout=10000)
        p.wait_for_timeout(300)
        base = p.evaluate("grade.probe()")
        b.review(b.ask([{"op": "set", "id": "shot-b", "prop": "exposure", "value": 0.5},
                        {"op": "set", "id": "shot-b", "prop": "temperature", "value": 60}]))
        p.wait_for_function("rcActions.previewing")
        moved = ("(base) => { const q = grade.probe(); return !!q && "
                 "Math.abs(q[0]-base[0]) + Math.abs(q[1]-base[1]) + Math.abs(q[2]-base[2]) > 6; }")
        p.wait_for_function(moved, arg=base, timeout=5000)          # After
        after = p.evaluate("grade.probe()")
        assert sum(after) > sum(base)                                # brighter
        p.locator("[data-testid=ai-before-after]").click()
        p.wait_for_function("(base) => { const q = grade.probe(); return !!q && "
                            "Math.abs(q[0]-base[0]) + Math.abs(q[1]-base[1]) + Math.abs(q[2]-base[2]) < 2; }",
                            arg=base, timeout=5000)                  # Before
        p.locator("[data-testid=ai-before-after]").click()
        p.wait_for_function(moved, arg=base, timeout=5000)           # After again
        p.wait_for_timeout(1000)
        assert b.raw() == before_disk                                # nothing saved
        b.accept()
        disk = b.settle()
        assert disk["colour"]["shots"]["shot-b"]["balance"]["exposure"] > 1.0
    finally:
        b.close()


def test_an_answer_and_a_failure_read_as_words(browser, live_server, project):
    b = Board(browser, live_server, project)
    try:
        p = b.page
        MODEL.answer = {"kind": "answer", "summary": "what B says", "text": "CLIP_B says “hello there”."}
        res = p.evaluate("""async () => {
          const r = await (await fetch('/api/assist', {method: 'POST', headers: {'content-type': 'application/json'},
            body: JSON.stringify({text: 'what does B say?'})})).json();
          for (;;) { const d = await (await fetch('/api/assist/' + r.job)).json();
                     if (d.status !== 'running') return d; await new Promise((z) => setTimeout(z, 50)); }
        }""")
        p.evaluate("(r) => rcActions.review(r)", res)
        p.wait_for_selector("[data-testid=ai-review] .air-answer")
        assert "CLIP_B says “hello there”." in p.locator("[data-testid=ai-review]").inner_text()
        assert p.locator("[data-testid=ai-op-row]").count() == 0
        p.locator("[data-testid=ai-review-close]").click()
        p.wait_for_function("!rcActions.open")
        p.evaluate("(r) => rcActions.review(r)", {"job": "abcdef012345", "status": "failed",
                                                   "error": "claude CLI timed out after 600s"})
        assert "The AI could not answer: claude CLI timed out" in p.locator("[data-testid=ai-review]").inner_text()
        p.locator("[data-testid=ai-review-close]").click()
        assert p.locator("#undo").is_disabled()
    finally:
        b.close()


# ---------------------------------------------------------------- a transition shows before it lands
# Verdict 9, punch 6: "a transition shows only as a line. Nothing is drawn on the timeline
# before Accept." A transition or fade op under review is its dashed box on the edit it
# will sit on (or against its clip's edge), in the transition's colour, labelled with its
# kind and length — and none is left once the review is answered.

TX_GHOSTS = """() => [...document.querySelectorAll('#tl .tx-ghost')].map((n) => {
  const r = n.getBoundingClientRect(), cs = getComputedStyle(n);
  return {kind: n.dataset.kind, lane: n.dataset.lane, align: n.dataset.align, a: n.dataset.a || null,
          b: n.dataset.b || null, id: n.dataset.id || null, edge: n.dataset.edge || null,
          dur: n.dataset.dur, label: n.querySelector('.tx-lbl').textContent,
          left: r.left, right: r.right, mid: (r.left + r.right) / 2, top: r.top, bottom: r.bottom,
          width: r.width, style: cs.borderTopStyle, colour: cs.borderTopColor};
})"""


def _box(page, sel):
    return page.evaluate("(s) => { const r = document.querySelector(s).getBoundingClientRect();"
                         " return {left: r.left, right: r.right, top: r.top, bottom: r.bottom}; }", sel)


def test_a_transition_and_fades_show_on_their_edit_before_accept(browser, live_server, project):
    b = Board(browser, live_server, project)
    try:
        p = b.page
        b.settle()
        time.sleep(0.3)
        before = b.raw()
        res = b.ask([{"op": "transition", "a": "shot-b", "b": "shot-c", "kind": "cross_dissolve", "dur": 0.5},
                     {"op": "fade", "id": "shot-a", "edge": "start", "kind": "dip_to_black", "dur": 1.0},
                     {"op": "fade", "id": "shot-c", "edge": "end", "kind": "audio_crossfade", "dur": 0.5}])
        assert res["refused"] == [], res["refused"]
        b.review(res)
        p.wait_for_function("document.querySelectorAll('#tl .tx-ghost').length === 3")
        g = {x["kind"]: x for x in p.evaluate(TX_GHOSTS)}
        zoom = p.evaluate("tl.state.zoom")
        v1 = _box(p, "#tl .tl-lane[data-lane=V1]")
        a1 = p.evaluate("(() => { const r = tl.el.tracks.A1.getBoundingClientRect();"
                        " return {left: r.left, right: r.right, top: r.top, bottom: r.bottom}; })()")
        # the dissolve: centred on the edit between shots 2 and 3, on V1, its length wide
        x = g["cross_dissolve"]
        assert (x["lane"], x["align"], x["a"], x["b"]) == ("V1", "center", "shot-b", "shot-c"), x
        assert x["label"] == "Cross Dissolve · 0.50 s"
        assert x["style"] == "dashed"
        edit = _box(p, '#tl .blk[data-id="shot-c"]')["left"]
        assert abs(x["mid"] - edit) <= 1.5, (x, edit)
        assert abs(x["width"] - 0.5 * zoom) <= 2, (x["width"], zoom)
        assert v1["top"] <= x["top"] and x["bottom"] <= v1["bottom"], (x, v1)
        # the fade from black: against shot 1's start, inside it
        f = g["dip_to_black"]
        assert (f["lane"], f["align"], f["id"], f["edge"]) == ("V1", "start", "shot-a", "start"), f
        assert f["label"] == "Dip to Black · 1.00 s"
        assert abs(f["left"] - _box(p, '#tl .blk[data-id="shot-a"]')["left"]) <= 1.5
        assert abs(f["width"] - 1.0 * zoom) <= 2
        # the sound's fade: on A1, against shot 3's end, in the audio colour (not the video's)
        s = g["audio_crossfade"]
        assert (s["lane"], s["align"], s["id"], s["edge"]) == ("A1", "end", "shot-c", "end"), s
        assert s["label"] == "Constant Power · 0.50 s"
        assert abs(s["right"] - _box(p, '#tl .blk[data-id="shot-c"]')["right"]) <= 1.5
        assert a1["top"] <= s["top"] and s["bottom"] <= a1["bottom"], (s, a1)
        assert s["colour"] != x["colour"]
        # the review says where to look; nothing has landed
        assert "3 transitions on the timeline" in p.locator("[data-testid=ai-tx-preview]").inner_text()
        assert p.locator("#tl .tl-trans").count() == 0 and p.evaluate("tl.transitions().length") == 0
        # ✗ on the fade's row takes its box away, the others stay
        p.locator("[data-testid=ai-op-row][data-op-index='1'] [data-testid=ai-op-reject]").click()
        p.wait_for_function("document.querySelectorAll('#tl .tx-ghost').length === 2")
        assert sorted(x["kind"] for x in p.evaluate(TX_GHOSTS)) == ["audio_crossfade", "cross_dissolve"]
        # a zoom redraws them on the same edit
        p.evaluate("tl.zoomTo(tl.state.zoom * 1.5)")
        p.wait_for_function("(z) => Math.abs(tl.state.zoom - z) < 1e-6", arg=zoom * 1.5)
        p.wait_for_timeout(120)
        x2 = {x["kind"]: x for x in p.evaluate(TX_GHOSTS)}["cross_dissolve"]
        assert abs(x2["mid"] - _box(p, '#tl .blk[data-id="shot-c"]')["left"]) <= 1.5
        # Reject all: none left, nothing written
        p.locator("[data-testid=ai-reject-all]").click()
        p.wait_for_function("!rcActions.open")
        p.wait_for_function("document.querySelectorAll('#tl .tx-ghost').length === 0")
        p.wait_for_timeout(900)
        assert p.locator("#tl .tl-trans").count() == 0
        assert b.raw() == before
    finally:
        b.close()


def test_an_accepted_transition_lands_where_its_ghost_was(browser, live_server, project):
    b = Board(browser, live_server, project)
    try:
        p = b.page
        b.review(b.ask([{"op": "transition", "a": "shot-a", "b": "shot-b", "kind": "dip_to_black", "dur": 0.5}]))
        p.wait_for_function("document.querySelectorAll('#tl .tx-ghost').length === 1")
        ghost = p.evaluate(TX_GHOSTS)[0]
        assert ghost["label"] == "Dip to Black · 0.50 s" and ghost["lane"] == "V1"
        assert "Dip to Black on the timeline" in p.locator("[data-testid=ai-tx-preview]").inner_text()
        row = p.locator("[data-testid=ai-op-row][data-op-index='0'] .air-line")
        assert "dashed on the timeline" in row.get_attribute("title")
        b.accept()
        p.wait_for_function("document.querySelectorAll('#tl .tx-ghost').length === 0")
        p.wait_for_selector("#tl .tl-trans[data-kind=dip_to_black]")
        real = _box(p, "#tl .tl-trans[data-kind=dip_to_black]")
        assert abs((real["left"] + real["right"]) / 2 - ghost["mid"]) <= 1.5, (real, ghost)
        assert abs((real["right"] - real["left"]) - ghost["width"]) <= 2, (real, ghost)
    finally:
        b.close()


def test_a_transition_on_a_shot_the_review_makes_is_drawn_on_the_new_cut(browser, live_server, project):
    """Razor shot 3, then a dissolve on the new edit: the edit exists only in the cut Accept
    makes, so the box is drawn on the ghost lane (the new cut under V1), on that edit."""
    b = Board(browser, live_server, project)
    try:
        p = b.page
        b.review(b.ask([{"op": "razor", "id": "shot-c", "at": 5.5},
                        {"op": "transition", "a": "shot-c", "b": "new:1", "kind": "cross_dissolve", "dur": 0.5}]))
        p.wait_for_function("tlLanes.ghost() && tlLanes.ghost().ghosts.length === 4")
        p.wait_for_function("[...document.querySelectorAll('#tl .tx-ghost')].some((n) => n.dataset.lane === 'ghost')")
        (x,) = p.evaluate(TX_GHOSTS)
        assert (x["a"], x["b"], x["align"]) == ("shot-c", "new:1", "center"), x
        half = _box(p, "#tl .tl-xlane[data-lane=ghost] .ghost[data-k='3']")
        lane = _box(p, "#tl .tl-xlane[data-lane=ghost]")
        assert abs(x["mid"] - half["left"]) <= 1.5, (x, half)
        assert lane["top"] <= x["top"] and x["bottom"] <= lane["bottom"]
        # ✗ on the razor takes the dissolve with it (it names the razor's shot)
        p.locator("[data-testid=ai-op-row][data-op-index='0'] [data-testid=ai-op-reject]").click()
        p.wait_for_function("document.querySelectorAll('#tl .tx-ghost').length === 0")
        p.locator("[data-testid=ai-reject-all]").click()
        p.wait_for_function("!rcActions.open")
    finally:
        b.close()


# ---------------------------------------------------------------- the undo entry's name

def test_an_ai_accept_s_undo_reads_as_its_ops_never_the_model_s_sentence(browser, live_server, project):
    """Verdict 9, punch 3: the header's ↶ said "undo: AI: Shot 1 is only 1.92 s now, so
    'about 3 s' means". Each accept's entry is named for its ops, whole words within 40
    characters, however long the model's summary; one Ctrl+Z still takes it all back."""
    sentence = "Shot 1 is only 1.92 s now, so 'about 3 s' means lengthening it rather than trimming"
    cases = [
        ([{"op": "trim", "id": "shot-a", "in": 1.5},
          {"op": "move", "ids": ["shot-c"], "before": "shot-a"}], "AI: trim + move"),
        ([{"op": "set", "id": "shot-b", "prop": "temperature", "value": 20},
          {"op": "set", "id": "shot-b", "prop": "shadows", "value": 25},
          {"op": "curve", "id": "shot-b", "channel": "master", "points": [[0, 0], [0.5, 0.6], [1, 1]]}],
         "AI: 3 colour changes"),
        ([{"op": "razor", "id": "shot-c", "at": 5.5},
          {"op": "trim", "id": "shot-a", "in": 1.25},
          {"op": "speed", "id": "shot-b", "rate": 0.5},
          {"op": "set", "id": "shot-b", "prop": "temperature", "value": 10},
          {"op": "transition", "a": "shot-a", "b": "shot-b", "kind": "cross_dissolve", "dur": 0.5},
          {"op": "marker", "at": 1.0}], "AI: razor cut + 5 more"),
        ([{"op": "fade", "id": "shot-c", "edge": "end", "kind": "dip_to_black", "dur": 1.0}], "AI: fade"),
    ]
    b = Board(browser, live_server, project)
    try:
        p = b.page
        seeded = norm(b.settle())
        for ops, want in cases:
            res = b.ask(ops, summary=sentence)
            assert res["refused"] == [], res["refused"]
            b.review(res)
            assert sentence in p.locator("[data-testid=ai-review]").inner_text()     # still said, in the review
            b.accept()
            title = p.locator("#undo").get_attribute("title") or ""
            assert check_undo_title(title) == want, title
            assert "Shot 1 is only" not in title
            assert p.evaluate("(ops) => rcActions.label(ops)", ops) == want
            b.undo()
            p.wait_for_function("document.querySelector('#undo').disabled")
            assert norm(b.settle()) == seeded, want
    finally:
        b.close()
