"""INTAKE M12 — effects on the board, driven in a real browser.

Under test: `app/static/fx.js` — the FX tool in the dock (the selected shot's effects
as cards, the design box, the jobs), the monitor overlay (`#fxCanvas`, drawn from the
same JSON the render draws from, and heard), the sketch (a reference drawn on a paused
frame) and live nudging (a hit selected on its card, a click on the monitor moves it).
Every assertion about an effect is made against what the server holds (`GET /api/fx`,
`GET /api/project`), never against the tool's own display alone.

Same fixture pattern as test_dock_ui.py: the real uvicorn server on a real port in a
thread of this process, the synthetic three-clip bin, the EDL re-seeded per test, a
fresh browser per test. The model and the render lane are stubbed the way
test_fx_api.py's `stubbed` fixture does it — `fx.design` / `fx.revise` /
`fx.synth_sound` / `fx.part_graph` / `fx.verify` are monkeypatched for the module,
which reaches the server because it runs in this process — and a FakeBackend answers
`roughcut.inference` with the same fixed hit-marker effect, so nothing here can reach a
real model whichever path the server takes. Skipped when playwright is absent.
"""

from __future__ import annotations

import json
import shutil
import socket
import sys
import threading
import time
import wave
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api",
                                     reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import fx  # noqa: E402


# ---------------------------------------------------------------- the fixed effect

HIT = {
    "name": "hit markers",
    "why": "the two impacts the onset track found",
    "events": [{"t": 1.5, "x": 0.5, "y": 0.7}, {"t": 2.4, "x": 0.55, "y": 0.72}],
    "overlay": {"duration": 0.35, "size": 0.12,
                "shapes": [{"type": "line", "from": [-1, -1], "to": [-0.3, -0.3], "width": 0.1},
                           {"type": "line", "from": [1, -1], "to": [0.3, -0.3], "width": 0.1},
                           {"type": "line", "from": [-1, 1], "to": [-0.3, 0.3], "width": 0.1},
                           {"type": "line", "from": [1, 1], "to": [0.3, 0.3], "width": 0.1}],
                "anim": {"scale": [[0, 1.4], [0.06, 1.0]], "opacity": [[0, 1], [0.23, 1], [0.35, 0]]},
                "flash": {"color": "#ff0000", "opacity": 0.15, "duration": 0.08}},
    "sound": {"duration": 0.18, "gain_db": -6,
              "layers": [{"type": "tone", "freq": 1800, "wave": "square", "decay": 0.06},
                         {"type": "noise", "color": "white", "hp": 1500, "decay": 0.09}]},
}


def _silent_wav(path: Path, seconds: float = 0.2, sr: int = 48000) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * 2 * int(sr * seconds))
    return path


@pytest.fixture(scope="module", autouse=True)
def stubbed_fx():
    """test_fx_api.py's stubs, held for the module: the render lane's functions raise
    NotImplementedError on this branch, and the server calls them from a job thread
    of this process."""
    def design(note, seg, clip, sidecar, *, reference=None, place=False, proxy=None,
               workdir=None, segments=None, clips=None, window=None):
        e = json.loads(json.dumps(HIT))
        e["shot"] = seg["id"]
        e["clip"] = seg["clip"]
        e["note"] = note
        if reference and reference.get("marks"):
            e["events"] = [{"t": float(reference.get("t") or 1.5), "x": m[0], "y": m[1]}
                           for m in reference["marks"]]
        if workdir:
            Path(workdir).mkdir(parents=True, exist_ok=True)
            (Path(workdir) / "strip.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        e["created"] = "2026-09-20T12:00:00"
        e["history"] = [{"note": note, "at": e["created"]}]
        return fx.validate_effect(e, segments or [seg], clips)

    def revise(effect, note, segments, clips=None):
        e = json.loads(json.dumps(effect))
        if "red" in note:
            for s in e["overlay"]["shapes"]:
                s["color"] = "#ff0000"
        e.setdefault("history", []).append({"note": note, "at": "2026-09-20T12:01:00"})
        return fx.validate_effect(e, segments, clips)

    def synth(sound, out, *, sr=48000):
        return _silent_wav(Path(out), sound["duration"], sr)

    def part_graph(effects, w, h, fps, workdir, *, vin="vbase", ain="abase"):
        return [], "", vin, ain

    def verify(effect, seg, *, onset=None, hz=10, part=None, base=None, impact=True):
        checks = [{"key": "in_shot", "label": "every hit inside the shot", "ok": True, "detail": ""},
                  {"key": "on_onset", "label": "each hit on an onset peak", "ok": None,
                   "detail": "skipped: no impact named"},
                  {"key": "audio_landed", "label": "the sound is in the proof",
                   "ok": part is not None and Path(part).exists(), "detail": "rise 9.1 dB"}]
        return {"ok": all(c["ok"] is not False for c in checks), "at": "2026-09-20T12:02:00",
                "checks": checks}

    mp = pytest.MonkeyPatch()
    mp.setattr(fx, "design", design)
    mp.setattr(fx, "revise", revise)
    mp.setattr(fx, "synth_sound", synth)
    mp.setattr(fx, "part_graph", part_graph)
    mp.setattr(fx, "verify", verify)
    yield
    mp.undo()


@pytest.fixture(scope="module", autouse=True)
def fake_backend():
    """Whatever the server asks the model, the answer is the fixed hit-marker effect."""
    from roughcut import config, inference

    class FakeBackend:
        name = "fake-fx"

        def complete(self, request):
            text = json.dumps(HIT)
            model = config.model_for(request.role)
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend="fake-fx", model=model,
                                    projected_usd=0.0001, latency_ms=1, raw=text)

    inference.set_backend(FakeBackend())
    inference.reset_spend()
    yield
    inference.set_backend(None)


# ---------------------------------------------------------------- the browser

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
    config = uvicorn.Config(server.app, host="127.0.0.1", port=port,
                            log_level="error")
    srv = uvicorn.Server(config)
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
    # finished fx jobs stay on /api/jobs for 12 s and would put the progress strip in
    # the header of the next module's pages (test_dock_ui measures the header)
    server.FX.clear()
    shutil.rmtree(server.fx_home(), ignore_errors=True)


@pytest.fixture
def page(live_server, project):
    import server

    seed = json.dumps({
        "variant": "T", "title": "test cut", "orient": "none", "story": "",
        "target_s": [5, 20],
        # ids in the seed: a read mints ids in memory and never writes them (a fresh
        # pair per GET /api/project), and the fx endpoints look a shot up by id in
        # the file — the same reason test_fx_api.py's _seed reconfigures the server
        "segments": [{"id": "gfxseed0001", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
                     {"id": "gfxseed0002", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}],
    }, indent=1)
    Path(project["edl"]).write_text(seed, encoding="utf-8")
    # proposals from an earlier test would still be on disk
    shutil.rmtree(server.fx_home(), ignore_errors=True)
    # and its finished fx jobs would still be on /api/jobs for 12 s: ten of them make
    # the header's progress strip 585 px tall (measured), and the monitor no longer
    # fits under it in the 900 px viewport the pointer tests need it in
    server.FX.clear()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        pg = browser.new_page(viewport={"width": 1280, "height": 900})
        pg.goto(live_server)
        pg.wait_for_selector("#tl .blk")
        pg.wait_for_function("window.fx && fx.ready")
        yield pg
        browser.close()


# ---------------------------------------------------------------- helpers

def api(page, path: str) -> dict:
    return page.evaluate(f"fetch('{path}').then(r => r.json())")


def effects(page) -> list[dict]:
    return api(page, "/api/fx")["effects"]


def shot_ids(page) -> list[str]:
    return page.evaluate("segs.map(s => s.id)")


def open_fx(page, shot: int = 0) -> str:
    """Open the FX tool on shot `shot` (0-based); returns its id."""
    page.evaluate("dock.open('fx')")
    sid = shot_ids(page)[shot]
    page.evaluate(f"tl.select('{sid}')")
    page.wait_for_selector("#fx .fxtitle")
    return sid


def design(page, note: str = "hit markers where my skis hit the rocks, with the sound",
           shot: int = 0) -> dict:
    """Design an effect on `shot` through the tool; returns it as the server lists it."""
    open_fx(page, shot)
    open_design(page)
    page.locator("#fxNote").fill(note)
    page.locator("#fxDesign").click()
    page.wait_for_selector("#fx .fxcard", timeout=20000)
    lst = effects(page)
    assert len(lst) >= 1, lst
    return lst[-1]


def open_design(page) -> None:
    """The design box: open by itself on a shot with no effects, else behind "+ design
    another effect" (INTAKE M16 I16.5)."""
    if page.locator("#fxAdd").count():
        page.locator("#fxAdd").click()
    page.wait_for_selector("#fxNote")


def wait_effect(page, fx_id: str, pred_js: str, timeout: float = 10.0) -> dict:
    """Wait until the server's copy of the effect satisfies `pred_js` (an `e =>` arrow).
    Polled from here with `evaluate` (which awaits the fetch) — `wait_for_function`
    does not await a returned Promise and would pass on the first tick."""
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        last = page.evaluate(
            f"fetch('/api/fx').then(r => r.json()).then(d => {{"
            f" const e = d.effects.find(x => x.id === '{fx_id}');"
            f" return {{e, ok: !!e && ({pred_js})(e)}}; }})")
        if last["ok"]:
            return last["e"]
        time.sleep(0.05)
    raise AssertionError(f"the effect never satisfied {pred_js}: {last}")


# ---------------------------------------------------------------- the FX tool

def test_the_range_bar_drag_survives_the_parked_playhead(page):
    """Karl, 2026-09-20: the handle "doesn't move much after I click on it to drag".
    A drag from the right handle to the middle of the bar moves the window's end to
    the middle, the handle is the same element throughout (no rebuild), the monitor
    is parked (paused) on the frame, and the band on the timeline follows."""
    open_fx(page)
    page.wait_for_selector("#fxBar .h1")
    bar = page.locator("#fxBar").bounding_box()
    h1 = page.locator("#fxBar .h1").bounding_box()
    page.evaluate("document.querySelector('#fxBar .h1').dataset.mark = 'held'")
    y = h1["y"] + h1["height"] / 2
    page.mouse.move(h1["x"] + h1["width"] / 2, y)
    page.mouse.down()
    for k in range(1, 9):
        page.mouse.move(h1["x"] + h1["width"] / 2 - k * (bar["width"] * 0.5 / 8), y)
        page.wait_for_timeout(40)
    assert page.evaluate("document.querySelector('#fxBar .h1').dataset.mark") == "held"   # never rebuilt
    page.mouse.up()
    page.wait_for_timeout(300)
    seg = page.evaluate("segs[0]")
    w = page.evaluate("fx.state.window")
    assert w is not None
    mid = seg["in"] + (seg["out"] - seg["in"]) * 0.5
    assert abs(w["t1"] - mid) < 0.15 * (seg["out"] - seg["in"]), (w, seg)
    assert page.evaluate("!player.playing") is True
    assert page.locator("#tl .fx-tlband").count() == 1
    # the band names the window in film time (shot 1 starts the film at its clip 1.0)
    want_end = page.evaluate("t => fx.fmtT(t)", w["t1"] - seg["in"])
    assert page.locator("#tl .fx-tlband span").inner_text() == f"0:00.0–{want_end}"


def test_the_range_bar_is_the_video(page):
    """Karl: "it's not clear how it connects to the current video / playhead." The bar is
    a filmstrip of the shot; a labelled playhead on it follows the monitor; a drag on
    the strip scrubs the monitor; the line under it says where the monitor is."""
    open_fx(page)
    page.wait_for_selector("#fxBar .strip img")
    assert page.locator("#fxBar .strip img").count() == 8
    srcs = page.evaluate("[...document.querySelectorAll('#fxBar .strip img')].map(i => i.getAttribute('src'))")
    assert all("/media/poster/" in s and "?t=" in s for s in srcs)
    # park the monitor at 1.5 s of the clip through the timeline: the bar's playhead follows
    page.evaluate("tl.seek(tl.filmStart(segs[0].id) + 0.5)")
    # the playhead is labelled in film time (clip 1.5 of shot 1 is 0:00.5 of the film)
    page.wait_for_function("!document.querySelector('#fxBar .ph').hidden && document.querySelector('#fxBar .ph b').textContent === '0:00.5'")
    assert page.locator("#fxBarLine").count() == 0          # no hint line under the bar
    # a drag on the strip scrubs: the monitor ends near the pointer's time, paused
    bar = page.locator("#fxBar").bounding_box()
    y = bar["y"] + bar["height"] / 2
    page.mouse.move(bar["x"] + bar["width"] * 0.2, y)
    page.mouse.down()
    page.mouse.move(bar["x"] + bar["width"] * 0.75, y, steps=6)
    page.wait_for_timeout(250)
    page.mouse.up()
    page.wait_for_timeout(300)
    seg = page.evaluate("segs[0]")
    want = seg["in"] + (seg["out"] - seg["in"]) * 0.75
    page.wait_for_function(f"Math.abs(liveVideo().currentTime - {want}) < 0.3", timeout=5000)
    assert page.evaluate("!player.playing") is True
    assert page.evaluate("fx.state.window") is None          # a scrub is not a window
    assert page.locator("#fxBar .h1 span").inner_text() == "0:02.0"   # the handles carry film times
    # one control sets the nearer end at the parked playhead: here, near the end
    head = page.locator("#fxAtHead")
    page.wait_for_function("document.querySelector('#fxAtHead').textContent === 'set end at playhead'")
    head.click()
    w = page.evaluate("fx.state.window")
    assert w["t0"] == seg["in"] and abs(w["t1"] - want) < 0.3, w


def test_the_tool_follows_the_selected_shot_and_prices_the_button(page):
    """The header names the shot; the design box is under it; the placing checkbox
    carries the price from GET /api/fx/price before anything is pressed."""
    sid = open_fx(page, 0)
    # (text_content, not inner_text: the title is uppercased by CSS)
    assert page.locator("#fx .fxtitle").text_content() == "Shot 1 · 2.0 s"   # no file name (M16)
    # no effects yet: the design box is open by itself, and nothing says so in words
    assert page.locator("#fxNote").is_visible() and page.locator("#fxAdd").count() == 0
    assert "no effects" not in page.locator("#fx").inner_text()
    page.wait_for_selector("#fx .fxprice")
    price = api(page, f"/api/fx/price?place=1&shot={sid}")
    assert price["frames"] >= 1
    assert page.locator("#fxDesign").inner_text() == f"Design · ~${price['usd']:.2f}"   # no "· 8 frames"
    # the other shot
    open_fx(page, 1)
    assert page.locator("#fx .fxtitle").text_content() == "Shot 2 · 2.0 s"
    # nothing selected: the tool says so and the design box goes
    page.evaluate("tl.select([])")
    page.wait_for_function("document.querySelector('#fx .fxtitle').textContent === 'FX'")
    assert "select a shot" in page.locator("#fx").inner_text()
    assert page.locator("#fxDesign").count() == 0


def test_the_design_box_is_behind_its_link_and_the_window_has_no_number_inputs(page):
    """INTAKE M16 I16.5 (4). On a shot with an effect the design box is one line, "+ design
    another effect"; it opens on a click and closes when another shot is chosen. The
    window is the range bar's handles and one "set start/end at playhead" control: the
    number inputs, the two "◀ playhead" buttons, "the whole shot ✕", "· N frames" and
    "click the strip to park the monitor on this shot" are gone."""
    design(page)
    assert page.locator("#fxNote").count() == 0
    assert page.locator("#fxAdd").inner_text() == "+ design another effect"
    page.locator("#fxAdd").click()
    page.wait_for_selector("#fxNote")
    assert page.evaluate("document.activeElement.id") == "fxNote"
    text = page.locator("#fx .fxdesign").inner_text()
    for gone in ("where", "◀ playhead", "the whole shot", "frame", "click the strip", "design an effect for this shot"):
        assert gone not in text, gone
    assert page.locator("#fx .fxdesign input").count() == 0
    assert page.locator("#fxAtHead").count() == 1
    open_fx(page, 1)                                         # another shot: no effects, the box open
    assert page.locator("#fxNote").is_visible()
    open_fx(page, 0)                                         # back: closed again
    assert page.locator("#fxNote").count() == 0 and page.locator("#fxAdd").count() == 1


def test_a_finished_check_does_not_take_the_caret_from_the_next_note(page):
    """The server's check finishes seconds after every design and repaints the tool;
    Karl typing the next note in the design box keeps his caret."""
    e = design(page)
    open_design(page)
    note = page.locator("#fxNote")
    assert note.input_value() == ""                          # the last design's note is used
    note.click()
    page.keyboard.type("a SEND IT ti")
    page.evaluate("fx.state.sig = ''; fx.refresh()")         # a repaint, as a finished job makes
    page.wait_for_function("document.activeElement && document.activeElement.id === 'fxNote'")
    page.keyboard.type("tle")
    assert note.input_value() == "a SEND IT title"
    assert e["id"]


def test_design_and_go_wait_for_their_price_and_never_spend_without_one(page):
    """INTAKE I16.0f, review: Design and Iterate's Go were clickable while their price
    was still on its way, and stayed unpriced for good when the fetch failed. They are
    disabled until priced; with no price to be had they say so and stay disabled."""
    e = design(page)
    open_design(page)
    page.wait_for_function("!document.querySelector('#fxDesign').disabled")
    page.locator(f"#fx .fxcard[data-id='{e['id']}'] button[data-act=iterate]").click()
    go = page.locator(f"#fx .fxcard[data-id='{e['id']}'] button[data-act=revise]")
    page.wait_for_function(
        "(() => { const b = document.querySelector('#fx .fxiter button[data-act=revise]');"
        " return !!b && !b.disabled && b.textContent.includes('~$'); })()", timeout=5000)

    posts = []
    page.on("request", lambda r: posts.append(r.url)
            if r.method == "POST" and ("/api/fx/design" in r.url or "/api/fx/revise" in r.url)
            else None)
    page.route("**/api/fx/price*", lambda route: route.abort())
    page.reload()
    page.wait_for_selector("#tl .blk")
    page.wait_for_function("window.fx && fx.ready")
    open_fx(page, 0)
    open_design(page)
    page.wait_for_function(
        "document.querySelector('#fxDesign').textContent.includes('price unavailable')",
        timeout=5000)
    assert page.locator("#fxDesign").is_disabled()
    page.locator(f"#fx .fxcard[data-id='{e['id']}'] button[data-act=iterate]").click()
    page.wait_for_function(
        "(() => { const b = document.querySelector('#fx .fxiter button[data-act=revise]');"
        " return !!b && b.textContent.includes('price unavailable'); })()", timeout=5000)
    assert go.is_disabled()
    # Enter in the box and ⌘Enter in the note go through the same refusal
    page.locator("#fx .fxiter input").fill("bigger")
    page.locator("#fx .fxiter input").press("Enter")
    page.locator("#fxNote").fill("a title")
    page.locator("#fxNote").press("Control+Enter")
    page.wait_for_timeout(300)
    assert posts == [], posts
    page.unroute("**/api/fx/price*")


def test_design_makes_a_proposal_card_and_never_touches_the_edl(page):
    """Design → a job (kind fx) → the card: name, the amber chip, one sentence, the
    moments with their times in FILM time (shot 1 is CLIP_A 1.0–3.0 at film 0, so the
    hits at clip 1.5 / 2.4 are 0:00.5 / 0:01.4), the anchors only on hover; Karl's note
    behind why?; the rail badge counts it; the EDL has no `effects` until Accept."""
    e = design(page)
    card = page.locator("#fx .fxcard")
    assert card.count() == 1
    text = card.inner_text()
    assert "hit markers" in text and "moments" not in text
    assert card.locator(".fxchip").text_content() == "proposed"   # uppercased by CSS
    assert card.locator(".fxsays").inner_text() == "the two impacts the onset track found"
    assert "hit markers where my skis hit the rocks" not in text   # the note is behind why?
    card.locator("button[data-act=why]").click()
    page.wait_for_selector("#fx .fxwhybox")
    assert "hit markers where my skis hit the rocks" in card.locator(".fxwhybox").inner_text()
    rows = card.locator(".fxev")
    assert rows.count() == 2
    assert rows.nth(0).locator(".t").inner_text() == "0:00.5"
    assert rows.nth(0).get_attribute("title").startswith("x 0.50 y 0.70")
    assert rows.nth(1).locator(".t").inner_text() == "0:01.4"
    assert card.locator("button[data-act=accept]").is_visible()
    assert card.locator("button[data-act=discard]").is_visible()
    assert card.locator("button[data-act=remove]").count() == 0
    assert page.locator("#rail .tool[data-tool=fx] .badge").inner_text() == "1"
    # the server: a proposal, on the shot, with its sound; the EDL untouched
    assert e["status"] == "proposed" and e["shot"] == shot_ids(page)[0]
    assert e["sound_url"].endswith("/sound.wav")
    assert api(page, "/api/project").get("effects") in (None, [])
    # the other shot has no cards — and the badge still counts the proposal waiting on
    # shot 1: it is what waits across the cut, not the selected shot's (INTAKE M16)
    open_fx(page, 1)
    assert page.locator("#fx .fxcard").count() == 0
    assert page.locator("#rail .tool[data-tool=fx] .badge").inner_text() == "1"


def test_the_nudges_move_a_hit_one_frame_through_put(page):
    e = design(page)
    card = page.locator("#fx .fxcard")
    card.locator(".fxev").nth(0).locator("button.nudge[data-d='1']").click()
    got = wait_effect(page, e["id"], f"e => Math.abs(e.events[0].t - {1.5 + fx.NUDGE_S}) < 1e-3")
    assert got["events"][0]["t"] == pytest.approx(1.5 + fx.NUDGE_S, abs=1e-3)
    card.locator(".fxev").nth(0).locator("button.nudge[data-d='-1']").click()
    wait_effect(page, e["id"], "e => Math.abs(e.events[0].t - 1.5) < 1e-3")
    card.locator(".fxev").nth(0).locator("button.nudge[data-d='-1']").click()
    got = wait_effect(page, e["id"], f"e => Math.abs(e.events[0].t - {1.5 - fx.NUDGE_S}) < 1e-3")
    # the card follows the server's copy (1.4583 reads as 0:01.5 at a tenth)
    page.wait_for_function(
        "document.querySelector('#fx .fxev .t').textContent === '0:00.5'")
    assert got["events"][1]["t"] == 2.4                      # the other hit stayed
    # a nudge clears the checklist, and the server checks the effect again by itself
    wait_effect(page, e["id"], "e => !!e.verify && Math.abs(e.events[0].t - %s) < 1e-3" % (1.5 - fx.NUDGE_S))


def test_the_check_runs_by_itself_then_change_accept_and_remove(page):
    """INTAKE M16 I16.5. No Verify button: the server checks a design by itself and the
    card says "✓ checked"; why? shows the checklist in plain words (the measurements on
    hover, not on the card). Change · ~$x opens one line and Go (priced) revises it — the
    change is checked again by itself. Accept folds the card (Remove · Change, the moments
    behind "adjust the moments ▸", no check line once it passes); Remove keeps it for
    Restore; Delete is for good."""
    e = design(page)
    card = page.locator("#fx .fxcard")
    assert card.locator("button[data-act=verify]").count() == 0
    page.wait_for_function("(document.querySelector('#fx .fxcheck') || {}).textContent === '✓ checked'",
                           timeout=30000)
    got = next(x for x in effects(page) if x["id"] == e["id"])
    assert got["verify"]["ok"] is True
    card.locator("button[data-act=why]").click()
    checks = card.locator(".fxchecks li")
    assert checks.count() == 3
    assert [checks.nth(k).locator(".mark").inner_text() for k in range(3)] == ["✓", "–", "✓"]
    assert checks.nth(0).locator(".lbl").inner_text() == "every moment is in the shot"
    assert checks.nth(1).locator(".lbl").inner_text() == "each hit lands on a sharp sound — not checked"
    assert checks.nth(2).locator(".lbl").inner_text() == "the sound is heard"
    assert "9.1 dB" not in card.inner_text() and checks.nth(2).get_attribute("title") == "rise 9.1 dB"
    # change: a model call, its price on the button and on Go
    price = api(page, '/api/fx/price')['usd']
    assert card.locator("button[data-act=iterate]").inner_text() == f"Change · ~${price:.2f}"
    card.locator("button[data-act=iterate]").click()
    go = card.locator(".fxiter button[data-act=revise]")
    assert go.inner_text() == f"Go · ~${price:.2f}"
    box = card.locator(".fxiter input")
    box.fill("make them red")
    box.press("Enter")
    got = wait_effect(page, e["id"], "e => e.overlay.shapes.every(s => s.color === '#ff0000') && !!e.verify",
                      timeout=30000)
    assert [h["note"] for h in got["history"]][-1] == "make them red"
    assert page.locator("#fx .fxiter").count() == 0
    # accept: the human's click, and only then the EDL
    card.locator("button[data-act=accept]").click()
    page.wait_for_function("document.querySelector('#fx .fxchip').textContent === 'accepted'")
    assert [x["id"] for x in api(page, "/api/project")["effects"]] == [e["id"]]
    assert card.locator("button[data-act=remove]").is_visible()
    assert card.locator("button[data-act=accept]").count() == 0
    assert card.locator("button[data-act=preview]").count() == 0
    page.wait_for_function("!document.querySelector('#fx .fxcard .fxcheck')")   # passed and answered: quiet
    assert card.locator(".fxev").count() == 0                 # folded
    card.locator("button[data-act=moments]").click()
    page.wait_for_function("document.querySelectorAll('#fx .fxev').length === 2")
    # the badge counts what waits on you: an accepted effect is answered (INTAKE M16)
    assert page.locator("#rail .tool[data-tool=fx] .badge").inner_text() == ""
    # remove keeps it (Karl: reversible), out of the cut, with Restore; Delete is for good
    card.locator("button[data-act=remove]").click()
    page.wait_for_function("document.querySelector('#fx .fxcard .fxchip.removed') !== null")
    assert api(page, "/api/project")["effects"] == []
    assert page.locator("#rail .tool[data-tool=fx] .badge").inner_text() == ""
    assert card.locator("button[data-act=restore]").is_visible()
    card.locator("button[data-act=restore]").click()
    page.wait_for_function("document.querySelector('#fx .fxcard .fxchip.accepted') !== null")
    assert len(api(page, "/api/project")["effects"]) == 1
    assert page.locator("#rail .tool[data-tool=fx] .badge").inner_text() == ""
    card.locator("button[data-act=remove]").click()
    page.wait_for_function("document.querySelector('#fx .fxcard .fxchip.removed') !== null")
    card.locator("button[data-act=discard]").click()
    page.wait_for_function("document.querySelectorAll('#fx .fxcard').length === 0")
    assert effects(page) == []


def test_the_waiting_proposal_is_first_in_film_time_and_its_preview_is_nexts(page):
    """INTAKE M16 I16.5 on the slow motion: an edit-only proposal on shot 2, beside an
    accepted effect, is the first card; it says what changes in the film in film time
    (shot 2 starts at 0:02), is checked by itself, shows no moment rows (nothing to draw),
    and its ▶ Preview carries data-next-for="polish" and its id — the button flow.js
    makes the one blue one. Nothing in the tool keeps the old primary style."""
    import server
    sid1, sid2 = shot_ids(page)
    acc = design(page, shot=1)
    page.locator(f"#fx .fxcard[data-id='{acc['id']}'] button[data-act=accept]").click()
    wait_effect(page, acc["id"], "e => e.status === 'accepted'")
    fx.save(server.fx_home(), {
        "id": "fx_slowui01", "shot": sid2, "clip": "CLIP_B.MP4", "name": "slow motion on the hit",
        "note": "slow motion at 0.4x over the biggest hit", "why": "the hit at 1.10 s at 0.4x",
        "events": [{"t": 1.1, "x": 0.5, "y": 0.5}], "status": "proposed",
        "edits": [{"op": "speed", "shot": sid2, "rate": 0.4, "from": 1.0, "to": 1.4}],
        "edit_words": ["CLIP_B.MP4 at 0.4× from 1.00 to 1.40s"],
        "limits": "the onset at 0.20 s is a voice", "created": "2026-09-20T22:42:23"})
    page.evaluate("fx.refresh()")
    page.wait_for_function("document.querySelectorAll('#fx .fxcard').length === 2")
    first = page.locator("#fx .fxcard").first
    assert first.get_attribute("data-id") == "fx_slowui01"
    assert first.locator(".fxsays").inner_text() == "Slows 0.40 s to 0.4×. Shot 2 gets 0.6 s longer, at 0:03."
    text = first.inner_text()
    assert "CLIP_B" not in text and "1.00" not in text and "could not" not in text
    assert first.locator(".fxev").count() == 0
    page.wait_for_function(
        "(document.querySelector(\"#fx .fxcard[data-id='fx_slowui01'] .fxcheck\") || {}).textContent === '✓ checked'",
        timeout=30000)
    pv = first.locator("button[data-act=preview]")
    assert pv.get_attribute("data-next-for") == "polish" and pv.get_attribute("data-fx") == "fx_slowui01"
    assert pv.inner_text() == "▶ Preview"
    assert [b.inner_text() for b in first.locator(".fxbtns button").all()][:3] == ["▶ Preview", "Accept", "Discard"]
    assert page.locator("#fx .primary").count() == 0
    assert page.locator("#fx button[data-act=verify]").count() == 0
    # flow.js's one blue button (C2) survives the tool's own rebuilds
    pv.evaluate("b => b.classList.add('is-next')")
    page.evaluate("fx.state.sig = ''; fx.refresh()")
    page.wait_for_function("document.querySelector(\"#fx button[data-fx='fx_slowui01']\").classList.contains('is-next')")
    assert page.locator("#fx .is-next").count() == 1
    first.locator("button[data-act=why]").click()
    why = first.locator(".fxwhybox").inner_text()
    assert "slow motion at 0.4x over the biggest hit" in why and "the hit at 1.10 s" in why
    assert "the onset at 0.20 s is a voice" in why
    # the accepted one folds below it: its sentence in two lines at most (the rest on
    # hover), Remove and Change, no moments
    second = page.locator("#fx .fxcard").nth(1)
    assert second.locator(".fxchip").text_content() == "accepted"
    assert second.locator(".fxev").count() == 0 and second.locator("button[data-act=moments]").count() == 1
    says = second.locator(".fxsays")
    assert says.evaluate("el => getComputedStyle(el).webkitLineClamp") == "2"
    assert says.get_attribute("title") == says.inner_text()
    assert first.locator(".fxsays").evaluate("el => getComputedStyle(el).webkitLineClamp") == "none"


def test_accepting_an_edit_changes_the_cut_in_place(page):
    """INTAKE M16 I16.5 (5), contract C5. Accepting the slow motion reloaded the whole
    page. Accept now awaits the board's window.roughcutRefresh() — the shell repaints
    the timeline, the shot strip and the bin in place — and the card leaves the tool;
    only where the board has no roughcutRefresh is the page reloaded."""
    import server

    def propose(fx_id, sid, op):
        fx.save(server.fx_home(), {
            "id": fx_id, "shot": sid, "clip": "CLIP_B.MP4", "name": "slow motion",
            "note": "slow motion on the hit", "why": "", "events": [{"t": 1.1, "x": 0.5, "y": 0.5}],
            "status": "proposed", "edits": [{"op": "speed", "shot": sid, "rate": 0.5, **op}],
            "created": "2026-09-20T22:42:23"})

    sid1, sid2 = shot_ids(page)
    propose("fx_inplace1", sid2, {"from": 1.0, "to": 1.4})
    open_fx(page, 1)
    page.evaluate("fx.refresh()")
    page.wait_for_selector("#fx .fxcard[data-id='fx_inplace1']")
    # (a function body: an expression that ends in a function is *called* by evaluate,
    # which counted one refresh before the click)
    page.evaluate("""() => { window.__stay = 1; window.__refreshed = 0;
        window.roughcutRefresh = async () => { window.__refreshed += 1; }; }""")
    page.locator("#fx .fxcard[data-id='fx_inplace1'] button[data-act=accept]").click()
    page.wait_for_function("window.__refreshed === 1")
    page.wait_for_function("document.querySelectorAll('#fx .fxcard').length === 0")
    assert page.evaluate("window.__stay") == 1                # no reload
    cut = api(page, "/api/project")["segments"]
    assert [s.get("speed") for s in cut] == [None, None, 0.5, None]
    # the fallback: no roughcutRefresh on the board, the page reloads onto the new cut
    page.reload()
    page.wait_for_selector("#tl .blk")
    page.wait_for_function("window.fx && fx.ready")
    sid = shot_ids(page)[3]                                  # CLIP_B 1.4–2.0, the last piece
    propose("fx_inplace2", sid, {})
    open_fx(page, 3)
    page.evaluate("fx.refresh()")
    page.wait_for_selector("#fx .fxcard[data-id='fx_inplace2']")
    page.evaluate("window.__stay = 1; window.roughcutRefresh = undefined")
    page.locator("#fx .fxcard[data-id='fx_inplace2'] button[data-act=accept]").click()
    page.wait_for_function("window.__stay === undefined", timeout=10000)
    page.wait_for_selector("#tl .blk", timeout=15000)
    page.wait_for_function("typeof segs !== 'undefined' && segs.length === 4 && segs[3].speed === 0.5",
                           timeout=15000)


def test_discard_drops_the_proposal(page):
    e = design(page)
    page.locator("#fx .fxcard button[data-act=discard]").click()
    page.wait_for_function("document.querySelectorAll('#fx .fxcard').length === 0")
    assert effects(page) == []
    assert api(page, f"/api/fx/{e['id']}/sound.wav") == {"detail": "sound.wav is not there yet"}


def test_a_passed_check_leaves_no_row_in_the_progress_strip(page):
    """INTAKE M16 decision 1, where I16.5 meets I16.1: the free check now runs after
    every design, Change and nudge, and its finished job lingered 12 s as a "Checking …
    done" row in the progress strip — the board's one header row became two after
    every nudge. A check that passed says so on its card ("✓ checked") and leaves no
    row; one that is running, or found something wrong, keeps its row."""
    job = {"id": "fx_jobv1", "kind": "fx", "fx_kind": "verify", "fx_id": "fx_x",
           "state": "done", "label": "Checking slow motion", "detail": "every check passed",
           "pct": 100, "elapsed_s": 2.0, "eta_s": None, "started": 1.0, "milestones": []}
    jobs = {"jobs": [job]}
    page.route("**/api/jobs", lambda r: r.fulfill(status=200, content_type="application/json",
                                                  body=json.dumps(jobs)))
    try:
        page.evaluate("pollJobs()")
        assert page.locator("#progress .job[data-job=fx_jobv1]").count() == 0
        assert page.locator("#progress").is_hidden()
        for state, detail in (("done", "failed: the sound is in the proof"),
                              ("running", "rendering a proof of the shot")):
            job.update(state=state, detail=detail)
            page.evaluate("pollJobs()")
            assert page.locator("#progress .job[data-job=fx_jobv1]").count() == 1, state
            assert page.locator("#progress").is_visible(), state
        job.update(state="done", detail="every check passed")
        page.evaluate("pollJobs()")
        assert page.locator("#progress .job[data-job=fx_jobv1]").count() == 0
        assert page.locator("#progress").is_hidden()
    finally:
        page.unroute("**/api/jobs")


def test_a_reload_lands_on_a_proposal_made_before_it(page):
    """The effects are the server's: a new page shows what an earlier one designed."""
    e = design(page)
    page.reload()
    page.wait_for_selector("#tl .blk")
    page.wait_for_function("window.fx && fx.ready")
    open_fx(page, 0)
    page.wait_for_selector("#fx .fxcard")
    assert page.locator("#fx .fxcard").get_attribute("data-id") == e["id"]



def test_next_lands_on_the_waiting_effect_not_the_anchored_shot(page, live_server):
    """INTAKE M16 I16.0a. On Killington, Next opened the FX tool on the shot the board
    had anchored — shot 1's accepted title — while the slow motion waited on shot 17,
    and the rail badge counted the selected shot's effects. Here: an accepted effect
    and a proposal on shot 2, the board on shot 1. Next selects shot 2, parks the
    monitor at its start (paused), opens FX with the proposal's card in view — in
    place on the board, and from another screen through the hash."""
    # (540, not 640: the board's header became one row, INTAKE M16 I16.1, and the dock
    # grew by what it gave up — the card has to start below the fold all the same)
    page.set_viewport_size({"width": 1280, "height": 540})
    sid2 = open_fx(page, 1)
    # two accepted effects ahead of the proposal: its card starts below the dock's fold,
    # as the slow motion's did on Killington, so the landing has to scroll to it
    for n, note in enumerate(("a title as we drop in", "a red vignette as I land"), 1):
        open_design(page)
        page.locator("#fxNote").fill(note)
        page.locator("#fxDesign").click()
        page.wait_for_function(f"document.querySelectorAll('#fx .fxcard').length === {n}",
                               timeout=20000)
        made = next(e for e in effects(page) if e["status"] == "proposed")
        page.locator(f"#fx .fxcard[data-id='{made['id']}'] button[data-act=accept]").click()
        wait_effect(page, made["id"], "e => e.status === 'accepted'")
        page.wait_for_function(
            f"[...document.querySelectorAll('#fx .fxchip')].filter(c => c.textContent === 'accepted').length === {n}")
    open_design(page)
    page.locator("#fxNote").fill("hit markers where my skis hit the rocks")
    page.locator("#fxDesign").click()
    page.wait_for_function("document.querySelectorAll('#fx .fxcard').length === 3", timeout=20000)
    waiting = next(e for e in effects(page) if e["status"] == "proposed")
    assert waiting["shot"] == sid2
    # the board on shot 1, which has no effects: the badge still says one waits
    sid1 = open_fx(page, 0)
    assert page.evaluate("fx.state.shot") == sid1
    assert page.locator("#rail .tool[data-tool=fx] .badge").inner_text() == "1"
    page.evaluate("dock.open('bin')")
    page.evaluate("flowBar.poll()")
    page.wait_for_function(
        f"(() => {{ const n = flowBar.state() && flowBar.state().next;"
        f" return !!n && !!n.target && n.target.fx === '{waiting['id']}'; }})()", timeout=10000)
    nxt = page.evaluate("flowBar.state().next")
    assert nxt["stage"] == "polish"
    assert nxt["href"] == f"/#tool=fx&shot={sid2}&fx={waiting['id']}"

    def landed():
        page.wait_for_function(
            f"window.tl && tl.state && tl.state.anchor === '{sid2}'"
            f" && window.fx && fx.state.shot === '{sid2}' && dock.current() === 'fx'"
            f" && !!document.querySelector(\"#fx .fxcard[data-id='{waiting['id']}']\")",
            timeout=15000)
        assert page.evaluate("!player.playing") is True
        assert page.evaluate("player.idx") == 1
        assert page.evaluate(f"Math.abs(tl.state.playhead - tl.filmStart('{sid2}'))") < 0.05
        page.wait_for_function("Math.abs(liveVideo().currentTime - segs[1].in) < 0.1", timeout=5000)
        page.wait_for_timeout(200)
        box = page.evaluate(f"""(() => {{
            const c = document.querySelector("#fx .fxcard[data-id='{waiting['id']}']").getBoundingClientRect();
            const t = document.querySelector('#tools').getBoundingClientRect();
            return {{ct: c.top, tt: t.top, tb: t.bottom}}; }})()""")
        assert box["tt"] - 1 <= box["ct"] < box["tb"] - 40, box   # the card's head is in view
        assert page.evaluate("window.scrollY") == 0          # only the dock scrolled
        assert page.locator("#rail .tool[data-tool=fx] .badge").inner_text() == "1"

    # the waiting card is the first one now (I16.5); fx.focus still brings it into view
    # from a dock scrolled past it — the accepted cards' why? open makes the tool tall
    page.evaluate(f"tl.select(['{sid2}'])")
    page.evaluate("dock.open('fx')")
    page.wait_for_selector(f"#fx .fxcard[data-id='{waiting['id']}']")
    assert page.locator("#fx .fxcard").first.get_attribute("data-id") == waiting["id"]
    for k in (1, 2):
        page.locator("#fx .fxcard").nth(k).locator("button[data-act=why]").click()
    page.wait_for_function("document.querySelectorAll('#fx .fxwhybox').length === 2")
    page.evaluate("window.scrollTo(0, 0)")                   # the clicks scrolled the page to reach them
    page.evaluate("(() => { const t = document.querySelector('#tools'); t.scrollTop = t.scrollHeight; })()")
    start = page.evaluate(f"""(() => {{
        const c = document.querySelector("#fx .fxcard[data-id='{waiting['id']}']").getBoundingClientRect();
        const t = document.querySelector('#tools').getBoundingClientRect();
        return {{ct: c.top, tt: t.top}}; }})()""")
    assert start["ct"] < start["tt"] - 1, f"the card must start out of view: {start}"
    assert page.evaluate(f"fx.focus('{waiting['id']}')") is True
    box = page.evaluate(f"""(() => {{
        const c = document.querySelector("#fx .fxcard[data-id='{waiting['id']}']").getBoundingClientRect();
        const t = document.querySelector('#tools').getBoundingClientRect();
        return {{ct: c.top, tt: t.top, tb: t.bottom}}; }})()""")
    assert box["tt"] - 1 <= box["ct"] < box["tb"] - 40, box
    page.evaluate(f"tl.select(['{sid1}'])")
    page.evaluate("dock.open('bin')")
    # on the board: in place, no reload
    page.evaluate("window.__stay = 1")
    page.locator("#flowNext").click()
    landed()
    assert page.evaluate("window.__stay") == 1
    assert page.evaluate("location.hash") == "#tool=fx"
    # the landing is the board's pick, not a shot Karl chose: ⌫ (the pass's "previous
    # moment") does not take it out of the cut (I16.0n)
    cut = page.evaluate("segs.map(s => s.id)")
    page.keyboard.press("Backspace")
    assert page.evaluate("segs.map(s => s.id)") == cut
    assert page.evaluate("tl.state.anchor") == sid2
    # from another screen: the href carries the target, and the hash lets go of it
    page.goto(live_server + "/open")
    page.wait_for_function(
        f"(() => {{ const n = window.flowBar && flowBar.state() && flowBar.state().next;"
        f" return !!n && !!n.target && n.target.fx === '{waiting['id']}'; }})()", timeout=15000)
    page.locator("#flowNext").click()
    page.wait_for_selector("#tl .blk", timeout=15000)
    landed()
    assert page.evaluate("location.hash") == "#tool=fx"


def test_focus_on_its_own_selects_the_shot_as_the_boards_pick(page):
    """The seam fix (I16.0a × n): fx.focus selects the effect's shot itself when nobody
    did, and that is the board's pick like Next's landing — ⌫ must not take it. The
    landing test cannot see this half: land() has selected the shot before focus runs."""
    e = design(page, shot=1)
    sid1, sid2 = shot_ids(page)
    page.locator(f'#tl .blk[data-id="{sid1}"]').click()   # Karl chose shot 1
    page.evaluate("player.playing && pauseCut()")
    assert page.evaluate("tl.state.anchor") == sid1
    assert page.evaluate(f"fx.focus('{e['id']}')") is True
    assert page.evaluate("tl.state.anchor") == sid2
    page.keyboard.press("Backspace")
    assert shot_ids(page) == [sid1, sid2], "⌫ took the shot focus selected"


def test_a_proposal_whose_shot_left_the_cut_neither_counts_nor_holds_next(page):
    """Review of I16.0a: the FX tool has a card only for a shot of the cut, so a proposal
    whose shot was taken out cannot be answered — yet the rail badge counted it and it
    held Next ("1 effect proposed — accept or discard") for good. It stays on disk and
    comes back with its shot."""
    e = design(page, shot=1)
    sid2 = shot_ids(page)[1]
    assert e["status"] == "proposed" and e["shot"] == sid2
    assert page.locator("#rail .tool[data-tool=fx] .badge").inner_text() == "1"
    page.locator(f'#tl .blk[data-id="{sid2}"]').click()
    page.keyboard.press("x")
    page.wait_for_function(f"!segs.some(s => s.id === '{sid2}')")
    page.wait_for_function(
        "document.querySelector('#saveState').textContent.startsWith('saved')", timeout=8000)
    page.wait_for_function(
        "document.querySelector('#rail .tool[data-tool=fx] .badge').textContent === ''",
        timeout=5000)
    page.evaluate("flowBar.poll()")
    page.wait_for_function(
        "(() => { const f = window.flowBar && flowBar.state();"
        " const p = f && f.stages.find((x) => x.key === 'polish');"
        " return !!p && p.counts.fx_proposed === 0; })()", timeout=10000)
    nxt = page.evaluate("flowBar.state().next")
    assert nxt["stage"] != "polish" and e["id"] not in json.dumps(nxt), nxt
    assert any(x["id"] == e["id"] for x in effects(page)), "kept on disk"
    # the shot back (⌘Z): the proposal counts again
    page.keyboard.press("Control+z")
    page.wait_for_function(f"segs.some(s => s.id === '{sid2}')")
    page.wait_for_function(
        "document.querySelector('#rail .tool[data-tool=fx] .badge').textContent === '1'",
        timeout=5000)


# ---------------------------------------------------------------- the monitor overlay

def test_poseAt_follows_the_python_rules(page):
    """`fx.poseAt` in the browser is `fx.pose_at` in Python: linear between keys, the
    first value before the first key, the last after the last, defaults for a missing
    track. Sampled at times on, between and beyond the keys."""
    overlay = json.loads(json.dumps(HIT["overlay"]))
    overlay["anim"]["rotate"] = [[0.05, -20], [0.2, 40]]
    overlay["anim"]["dx"] = [[0, 0.1]]
    overlay = fx.validate_overlay(overlay)
    for t in (0, 0.03, 0.06, 0.1, 0.2, 0.23, 0.3, 0.35, 0.5):
        want = fx.pose_at(overlay, t)
        got = page.evaluate("([o, t]) => fx.poseAt(o, t)", [overlay, t])
        for k in ("scale", "opacity", "rotate", "dx", "dy"):
            assert got[k] == pytest.approx(want[k], abs=1e-6), (t, k, got, want)


PIXELS = """([x, y, half]) => {
    const c = document.querySelector('#fxCanvas');
    if (!c.width || !c.height) return -1;
    const ctx = c.getContext('2d');
    const x0 = Math.max(0, Math.round(x * c.width) - half), y0 = Math.max(0, Math.round(y * c.height) - half);
    const d = ctx.getImageData(x0, y0, half * 2, half * 2).data;
    let n = 0;
    for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
    return n;
}"""


def test_the_monitor_draws_the_effect_at_its_hit_and_only_then(page):
    """Park the monitor on the first hit (shot 1 = CLIP_A 1.0–3.0, the hit at clip
    1.5 = film 0.5): the canvas is live, sized to the proxy, and has paint around the
    anchor (x 0.5, y 0.7) and the flash's tint in a corner. Parked a frame before the
    hit there is nothing on it; on the other shot the canvas is not live at all."""
    design(page)
    page.evaluate("tl.seek(0.5)")
    page.wait_for_function("document.querySelector('#fxCanvas').classList.contains('live')")
    page.wait_for_function(
        "Math.abs(document.querySelector('.screen video.live').currentTime - 1.5) < 0.02", timeout=10000)
    page.wait_for_function(f"({PIXELS})([0.5, 0.7, 30]) > 0", timeout=10000)
    assert page.evaluate("[document.querySelector('#fxCanvas').width, document.querySelector('#fxCanvas').height]") == [320, 180]
    # the sprite: four lines in an X around the anchor, none of it at the far corner
    # (the flash tints the whole frame at 15 %, so the corner has alpha but the sprite
    # region has much more of it)
    around = page.evaluate(PIXELS, [0.5, 0.7, 30])
    corner = page.evaluate(PIXELS, [0.05, 0.1, 30])
    assert around == 60 * 60 and corner == 60 * 60          # the flash covers everything
    alpha_corner = page.evaluate("""() => {
        const c = document.querySelector('#fxCanvas');
        return c.getContext('2d').getImageData(10, 10, 1, 1).data[3]; }""")
    assert 30 <= alpha_corner <= 45                          # 15 % red, and nothing else there
    alpha_sprite = page.evaluate("""() => {
        const c = document.querySelector('#fxCanvas');
        // the -1,-1 → -0.3,-0.3 line at scale 1.4 in a 38 px box: ~ -17 px from the anchor
        const x = Math.round(0.5 * c.width) - 12, y = Math.round(0.7 * c.height) - 12;
        let best = 0;
        for (let dx = -4; dx <= 4; dx++) for (let dy = -4; dy <= 4; dy++) {
            best = Math.max(best, c.getContext('2d').getImageData(x + dx, y + dy, 1, 1).data[3]);
        }
        return best; }""")
    assert alpha_sprite > 200, alpha_sprite
    # a frame before the hit: nothing drawn
    page.evaluate("tl.seek(0.4)")
    page.wait_for_function(
        "Math.abs(document.querySelector('.screen video.live').currentTime - 1.4) < 0.02", timeout=10000)
    page.wait_for_function(f"({PIXELS})([0.5, 0.7, 30]) === 0", timeout=10000)
    # the other shot has no effects: the canvas is not live
    page.evaluate("tl.seek(2.5)")
    page.wait_for_function("!document.querySelector('#fxCanvas').classList.contains('live')")


def test_preview_plays_the_shot_and_sounds_each_hit_once_per_pass(page):
    """Preview plays this shot only; the effect's sound (one Audio from its sound.wav)
    starts as the clip time crosses each hit — twice for two hits, not more, however
    many frames land inside one — and a seek resets the guard for the next pass."""
    e = design(page)
    src = page.evaluate(f"fx.audio('{e['id']}').src")
    assert f"/api/fx/{e['id']}/sound.wav" in src
    page.locator("#fx .fxcard button[data-act=preview]").click()
    page.wait_for_function("player.playing === true && player.single === true")
    page.wait_for_function("fx.state.sounded === 2", timeout=10000)
    page.wait_for_function("player.playing === false", timeout=10000)   # the shot ended
    assert page.evaluate("fx.state.sounded") == 2
    # another pass from before the second hit: one more
    page.evaluate("tl.seek(1.0)")                            # clip 2.0 of shot 1
    page.locator("#fx .fxcard button[data-act=preview]").click()
    page.wait_for_function("fx.state.sounded === 3", timeout=10000)
    page.wait_for_function("player.playing === false", timeout=10000)
    assert page.evaluate("fx.state.sounded") == 3


# ---------------------------------------------------------------- the sketch

def screen_rect(page) -> dict:
    """The picture's box on screen, after putting the whole screen in the viewport
    under the sticky header: with the progress strip in the header (the earlier
    tests' finished jobs stay on it 12 s) the header is tall and the monitor's lower
    half sits below 900 px — a pointer event on either lands on the strip or on
    nothing. Asserted, so a miss is loud rather than a 30 s wait."""
    r = page.evaluate("""async () => {
        const hd = document.querySelector('header').getBoundingClientRect().bottom;
        const s = document.querySelector('.screen').getBoundingClientRect();
        window.scrollBy(0, s.top - hd - 8);
        // two frames: the rect is right at once, but a pointer event dispatched in the
        // same tick is hit-tested against the pre-scroll page
        await new Promise((res) => requestAnimationFrame(() => requestAnimationFrame(res)));
        const b = document.querySelector('#fxCanvas').getBoundingClientRect();
        return {x: b.x, y: b.y, w: b.width, h: b.height, vh: window.innerHeight,
                hd: document.querySelector('header').getBoundingClientRect().bottom}; }""")
    assert r["hd"] <= r["y"] and r["y"] + r["h"] <= r["vh"] + 1, r
    return r


def drag(page, r: dict, x0: float, y0: float, x1: float, y1: float, steps: int = 6) -> None:
    """One stroke on the monitor, from and to fractions of the picture."""
    page.mouse.move(r["x"] + x0 * r["w"], r["y"] + y0 * r["h"])
    page.mouse.down()
    page.mouse.move(r["x"] + x1 * r["w"], r["y"] + y1 * r["h"], steps=steps)
    page.mouse.up()


def test_draw_a_reference_makes_marks_from_strokes_and_the_design_uses_them(page):
    """Draw a reference pauses the monitor and hands the canvas the pointer; strokes
    are fractions of the frame, each one's centroid a mark; ⌫ undoes the last; Use it
    builds the reference (t = the live clip time, the goal, the strokes, the marks, a
    PNG of the frame with the strokes on it) and the design box says so until the
    next Design carries it — and then the server keeps the marks as the anchors."""
    open_fx(page, 0)
    page.evaluate("tl.seek(0.5)")                            # shot 1 parked at clip 1.5
    page.wait_for_function(
        "Math.abs(document.querySelector('.screen video.live').currentTime - 1.5) < 0.02", timeout=10000)
    page.locator("#fxSketch").click()
    page.wait_for_function("document.querySelector('#fxCanvas').classList.contains('sketch')")
    assert page.evaluate("player.playing") is False
    assert page.evaluate("getComputedStyle(document.querySelector('#fxCanvas')).pointerEvents") == "auto"
    assert "0 strokes" in page.locator("#fx .fxsketch").inner_text()
    assert page.locator("#fxUse").is_disabled()
    assert page.locator("#fxSketch").is_disabled()
    r = screen_rect(page)
    drag(page, r, 0.30, 0.60, 0.40, 0.70)                    # centroid ≈ (0.35, 0.65)
    page.wait_for_function("fx.state.sketch && fx.state.sketch.strokes.length === 1")
    drag(page, r, 0.60, 0.60, 0.70, 0.80)                    # centroid ≈ (0.65, 0.70)
    page.wait_for_function("fx.state.sketch.strokes.length === 2")
    assert "2 strokes" in page.locator("#fx .fxsketch").inner_text()
    assert page.locator("#fxUse").is_enabled()
    # the clicks on the monitor did not start playback (the screen's own click would)
    assert page.evaluate("player.playing") is False
    # ⌫ undoes the last stroke — and does not ripple-delete the selected shot
    page.keyboard.press("Backspace")
    page.wait_for_function("fx.state.sketch.strokes.length === 1")
    assert "1 stroke" in page.locator("#fx .fxsketch").inner_text()
    assert page.evaluate("segs.length") == 2
    drag(page, r, 0.60, 0.60, 0.70, 0.80)
    page.wait_for_function("fx.state.sketch.strokes.length === 2")
    # the strokes are drawn on the canvas in the accent colour, 3 px on screen
    assert page.evaluate(PIXELS, [0.35, 0.65, 6]) > 0
    page.locator("#fxGoal").fill("the skis — put the markers here")
    page.locator("#fxUse").click()
    page.wait_for_function("!document.querySelector('#fxCanvas').classList.contains('sketch')")
    assert page.evaluate("fx.state.sketch") is None
    ref = page.evaluate("fx.state.reference")
    assert ref["t"] == pytest.approx(1.5, abs=0.02)
    assert ref["goal"] == "the skis — put the markers here"
    assert len(ref["strokes"]) == 2 and len(ref["marks"]) == 2
    assert ref["marks"][0][0] == pytest.approx(0.35, abs=0.03)
    assert ref["marks"][0][1] == pytest.approx(0.65, abs=0.03)
    assert ref["marks"][1][0] == pytest.approx(0.65, abs=0.03)
    assert ref["marks"][1][1] == pytest.approx(0.70, abs=0.03)
    for stroke in ref["strokes"]:
        assert len(stroke) >= 2
        for p in stroke:
            assert 0 <= p["x"] <= 1 and 0 <= p["y"] <= 1        # fractions, never pixels
    assert ref["png"].startswith("data:image/png;base64,")
    line = page.locator("#fx .fxref").inner_text()
    assert "reference · 2 marks at 0:00.5" in line and "the skis" in line   # film time
    # the next Design carries it; the server keeps the marks as the anchors
    page.locator("#fxNote").fill("hit markers on the skis")
    page.locator("#fxDesign").click()
    page.wait_for_selector("#fx .fxcard", timeout=20000)
    e = effects(page)[-1]
    assert [(ev["x"], ev["y"]) for ev in e["events"]] == [tuple(m) for m in ref["marks"]]
    assert e["events"][0]["t"] == pytest.approx(1.5, abs=0.02)
    assert e["reference"]["goal"] == "the skis — put the markers here"
    assert e["reference"]["marks"] == ref["marks"]
    assert e["ref_url"] == f"/api/fx/{e['id']}/ref.png"
    assert page.evaluate(f"fetch('{e['ref_url']}').then(r => r.status)") == 200
    # used: the line is gone
    assert page.locator("#fx .fxref").count() == 0
    assert page.evaluate("fx.state.reference") is None


def test_esc_cancels_the_sketch_and_the_clear_button_forgets_a_reference(page):
    open_fx(page, 0)
    page.evaluate("tl.seek(0.5)")
    page.wait_for_function(
        "Math.abs(document.querySelector('.screen video.live').currentTime - 1.5) < 0.02", timeout=10000)
    page.locator("#fxSketch").click()
    page.wait_for_function("document.querySelector('#fxCanvas').classList.contains('sketch')")
    r = screen_rect(page)
    drag(page, r, 0.2, 0.2, 0.3, 0.3)
    page.wait_for_function("fx.state.sketch.strokes.length === 1")
    page.keyboard.press("Escape")
    page.wait_for_function("!document.querySelector('#fxCanvas').classList.contains('sketch')")
    assert page.evaluate("fx.state.sketch") is None
    assert page.evaluate("fx.state.reference") is None
    assert page.locator("#fx .fxref").count() == 0
    assert page.evaluate("segs.length") == 2 and page.evaluate("[...tl.state.sel].length") == 1
    # a used reference can be forgotten before Design
    page.locator("#fxSketch").click()
    page.wait_for_function("document.querySelector('#fxCanvas').classList.contains('sketch')")
    drag(page, r, 0.2, 0.2, 0.3, 0.3)
    page.wait_for_function("fx.state.sketch.strokes.length === 1")
    page.locator("#fxUse").click()
    page.wait_for_selector("#fx .fxref")
    page.locator("#fx .fxref button[data-act=clearref]").click()
    page.wait_for_function("document.querySelectorAll('#fx .fxref').length === 0")
    assert page.evaluate("fx.state.reference") is None


# ---------------------------------------------------------------- live nudging

SPRITE_ALPHA = """([x, y]) => {
    const c = document.querySelector('#fxCanvas');
    if (!c.width) return -1;
    // the -1,-1 → -0.3,-0.3 line of the X at scale 1.4 in a 38 px box: ~12 px up-left
    const px = Math.round(x * c.width) - 12, py = Math.round(y * c.height) - 12;
    let best = 0;
    for (let dx = -4; dx <= 4; dx++) for (let dy = -4; dy <= 4; dy++) {
        best = Math.max(best, c.getContext('2d').getImageData(px + dx, py + dy, 1, 1).data[3]);
    }
    return best;
}"""


def test_a_click_on_the_paused_monitor_moves_the_selected_hit(page):
    """EFFECTS.md: "Two clicks beats any amount of inference." Click the hit on its
    card — the monitor parks on its frame — then click the monitor: the anchor moves
    there (PUT, fractions of the frame), the other hit and the time stay, the sprite
    draws at the new place, and playback did not start. With nothing selected, or
    while playing, the click is the screen's own."""
    e = design(page)
    card = page.locator("#fx .fxcard")
    card.locator(".fxev").nth(1).click()                     # hit 2, at clip 2.4
    page.wait_for_function(
        "Math.abs(document.querySelector('.screen video.live').currentTime - 2.4) < 0.02", timeout=10000)
    assert "sel" in card.locator(".fxev").nth(1).get_attribute("class")
    assert card.locator(".fxpick").inner_text() == "click the picture to move moment 2"
    assert page.evaluate("player.playing") is False
    r = screen_rect(page)
    page.mouse.click(r["x"] + 0.25 * r["w"], r["y"] + 0.60 * r["h"])
    got = wait_effect(page, e["id"], "e => Math.abs(e.events[1].x - 0.25) < 0.03")
    assert got["events"][1]["y"] == pytest.approx(0.60, abs=0.03)
    assert got["events"][1]["t"] == 2.4
    assert (got["events"][0]["x"], got["events"][0]["y"], got["events"][0]["t"]) == (0.5, 0.7, 1.5)
    assert page.evaluate("player.playing") is False           # the click was ours, not the screen's
    page.wait_for_function(
        "document.querySelectorAll('#fx .fxev')[1].title.startsWith('x 0.2')")
    # the monitor draws it where it went
    page.wait_for_function(f"({SPRITE_ALPHA})([{got['events'][1]['x']}, {got['events'][1]['y']}]) > 200",
                           timeout=10000)
    # the same hit again deselects; a click on the monitor is then the screen's own
    card.locator(".fxev").nth(1).click()
    page.wait_for_function("fx.state.sel === null")
    page.mouse.click(r["x"] + 0.75 * r["w"], r["y"] + 0.30 * r["h"])
    page.wait_for_function("player.playing === true", timeout=10000)
    page.evaluate("pauseCut()")
    assert next(x for x in effects(page) if x["id"] == e["id"])["events"][1]["x"] == got["events"][1]["x"]
    # selected but playing: the click pauses, it does not move
    page.evaluate(f"fx.select('{e['id']}', 1)")
    page.wait_for_function("fx.state.sel && fx.state.sel.i === 1")
    page.evaluate("playFrom(0)")
    page.wait_for_function("player.playing === true", timeout=10000)
    page.mouse.click(r["x"] + 0.75 * r["w"], r["y"] + 0.30 * r["h"])
    page.wait_for_function("player.playing === false", timeout=10000)
    assert next(x for x in effects(page) if x["id"] == e["id"])["events"][1]["x"] == got["events"][1]["x"]


