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
    page.locator("#fxNote").fill(note)
    page.locator("#fxDesign").click()
    page.wait_for_selector("#fx .fxcard", timeout=20000)
    lst = effects(page)
    assert len(lst) >= 1, lst
    return lst[-1]


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

def test_the_tool_follows_the_selected_shot_and_prices_the_button(page):
    """The header names the shot; the design box is under it; the placing checkbox
    carries the price from GET /api/fx/price before anything is pressed."""
    sid = open_fx(page, 0)
    # (text_content, not inner_text: the title is uppercased by CSS)
    assert page.locator("#fx .fxtitle").text_content() == "FX · shot 1 · CLIP_A"
    assert "no effects on this shot yet" in page.locator("#fx").inner_text()
    page.wait_for_selector("#fx .fxprice")
    price = api(page, f"/api/fx/price?place=1&shot={sid}")
    assert price["frames"] >= 1
    assert page.locator("#fx .fxprice").inner_text().startswith(f"≈ ${price['usd']:.2f}")
    # the other shot
    open_fx(page, 1)
    assert page.locator("#fx .fxtitle").text_content() == "FX · shot 2 · CLIP_B"
    # nothing selected: the tool says so and the design box goes
    page.evaluate("tl.select([])")
    page.wait_for_function("document.querySelector('#fx .fxtitle').textContent === 'FX'")
    assert "select a shot" in page.locator("#fx").inner_text()
    assert page.locator("#fxDesign").count() == 0


def test_design_makes_a_proposal_card_and_never_touches_the_edl(page):
    """Design → a job (kind fx) → the card: name, hits, the amber chip, the note, the
    events with their times and anchors; the rail badge counts it; the EDL has no
    `effects` until Accept."""
    e = design(page)
    card = page.locator("#fx .fxcard")
    assert card.count() == 1
    text = card.inner_text()
    assert "hit markers" in text and "2 moments" in text
    assert card.locator(".fxchip").text_content() == "proposed"   # uppercased by CSS
    assert "hit markers where my skis hit the rocks" in text
    rows = card.locator(".fxev")
    assert rows.count() == 2
    assert rows.nth(0).locator(".t").inner_text() == "0:01.5"
    assert rows.nth(0).locator(".xy").inner_text() == "x 0.50 y 0.70"
    assert rows.nth(1).locator(".t").inner_text() == "0:02.4"
    assert card.locator("button[data-act=accept]").is_visible()
    assert card.locator("button[data-act=discard]").is_visible()
    assert card.locator("button[data-act=remove]").count() == 0
    assert page.locator("#rail .tool[data-tool=fx] .badge").inner_text() == "1"
    # the server: a proposal, on the shot, with its sound; the EDL untouched
    assert e["status"] == "proposed" and e["shot"] == shot_ids(page)[0]
    assert e["sound_url"].endswith("/sound.wav")
    assert api(page, "/api/project").get("effects") in (None, [])
    # the other shot has no cards and no badge
    open_fx(page, 1)
    assert page.locator("#fx .fxcard").count() == 0
    assert page.locator("#rail .tool[data-tool=fx] .badge").inner_text() == ""


def test_the_nudges_move_a_hit_one_frame_through_put(page):
    e = design(page)
    card = page.locator("#fx .fxcard")
    card.locator(".fxev").nth(0).locator("button.nudge[data-d='1']").click()
    got = wait_effect(page, e["id"], f"e => Math.abs(e.events[0].t - {1.5 + fx.NUDGE_S}) < 1e-3")
    assert got["events"][0]["t"] == pytest.approx(1.5 + fx.NUDGE_S, abs=1e-3)
    assert "verify" not in got
    card.locator(".fxev").nth(0).locator("button.nudge[data-d='-1']").click()
    wait_effect(page, e["id"], "e => Math.abs(e.events[0].t - 1.5) < 1e-3")
    card.locator(".fxev").nth(0).locator("button.nudge[data-d='-1']").click()
    got = wait_effect(page, e["id"], f"e => Math.abs(e.events[0].t - {1.5 - fx.NUDGE_S}) < 1e-3")
    # the card follows the server's copy (1.4583 reads as 0:01.5 at a tenth)
    page.wait_for_function(
        "document.querySelector('#fx .fxev .t').textContent === '0:01.5'")
    assert got["events"][1]["t"] == 2.4                      # the other hit stayed


def test_verify_iterate_accept_and_remove(page):
    """Verify runs the checklist and the card shows it (✓ / ✗ / – per check with its
    detail); Iterate is one line and a revise job that clears the checklist; Accept
    moves it into the EDL (the chip goes green, Remove replaces Accept / Discard);
    Remove takes it out of the cut."""
    e = design(page)
    card = page.locator("#fx .fxcard")
    card.locator("button[data-act=verify]").click()
    page.wait_for_selector("#fx .fxverify", timeout=30000)
    got = next(x for x in effects(page) if x["id"] == e["id"])
    assert got["verify"]["ok"] is True
    checks = card.locator(".fxchecks li")
    assert checks.count() == 3
    assert checks.nth(0).locator(".mark").inner_text() == "✓"
    assert checks.nth(1).locator(".mark").inner_text() == "–"
    assert "skipped" in checks.nth(1).inner_text()
    assert checks.nth(2).locator(".mark").inner_text() == "✓"
    assert "rise 9.1 dB" in checks.nth(2).inner_text()
    assert "verified" in card.locator(".fxvhead").inner_text()
    # iterate
    card.locator("button[data-act=iterate]").click()
    box = card.locator(".fxiter input")
    box.fill("make them red")
    box.press("Enter")
    got = wait_effect(page, e["id"], "e => e.overlay.shapes.every(s => s.color === '#ff0000')",
                      timeout=30000)
    assert [h["note"] for h in got["history"]][-1] == "make them red"
    assert "verify" not in got                               # an iteration is unverified
    page.wait_for_function("document.querySelectorAll('#fx .fxverify').length === 0")
    assert page.locator("#fx .fxiter").count() == 0
    # accept: the human's click, and only then the EDL
    card.locator("button[data-act=accept]").click()
    page.wait_for_function("document.querySelector('#fx .fxchip').textContent === 'accepted'")
    assert [x["id"] for x in api(page, "/api/project")["effects"]] == [e["id"]]
    assert card.locator("button[data-act=remove]").is_visible()
    assert card.locator("button[data-act=accept]").count() == 0
    assert page.locator("#rail .tool[data-tool=fx] .badge").inner_text() == "1"
    # remove keeps it (Karl: reversible), out of the cut, with Restore; Delete is for good
    card.locator("button[data-act=remove]").click()
    page.wait_for_function("document.querySelector('#fx .fxcard .fxchip.removed') !== null")
    assert api(page, "/api/project")["effects"] == []
    assert page.locator("#rail .tool[data-tool=fx] .badge").inner_text() == ""
    assert card.locator("button[data-act=restore]").is_visible()
    card.locator("button[data-act=restore]").click()
    page.wait_for_function("document.querySelector('#fx .fxcard .fxchip.accepted') !== null")
    assert len(api(page, "/api/project")["effects"]) == 1
    assert page.locator("#rail .tool[data-tool=fx] .badge").inner_text() == "1"
    card.locator("button[data-act=remove]").click()
    page.wait_for_function("document.querySelector('#fx .fxcard .fxchip.removed') !== null")
    card.locator("button[data-act=discard]").click()
    page.wait_for_function("document.querySelectorAll('#fx .fxcard').length === 0")
    assert effects(page) == []


def test_discard_drops_the_proposal(page):
    e = design(page)
    page.locator("#fx .fxcard button[data-act=discard]").click()
    page.wait_for_function("document.querySelectorAll('#fx .fxcard').length === 0")
    assert effects(page) == []
    assert api(page, f"/api/fx/{e['id']}/sound.wav") == {"detail": "sound.wav is not there yet"}


def test_a_reload_lands_on_a_proposal_made_before_it(page):
    """The effects are the server's: a new page shows what an earlier one designed."""
    e = design(page)
    page.reload()
    page.wait_for_selector("#tl .blk")
    page.wait_for_function("window.fx && fx.ready")
    open_fx(page, 0)
    page.wait_for_selector("#fx .fxcard")
    assert page.locator("#fx .fxcard").get_attribute("data-id") == e["id"]



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
    assert "reference · 2 marks at 0:01.5" in line and "the skis" in line
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
    assert "moment 2 selected" in card.locator(".fxpick").inner_text()
    assert page.evaluate("player.playing") is False
    r = screen_rect(page)
    page.mouse.click(r["x"] + 0.25 * r["w"], r["y"] + 0.60 * r["h"])
    got = wait_effect(page, e["id"], "e => Math.abs(e.events[1].x - 0.25) < 0.03")
    assert got["events"][1]["y"] == pytest.approx(0.60, abs=0.03)
    assert got["events"][1]["t"] == 2.4
    assert (got["events"][0]["x"], got["events"][0]["y"], got["events"][0]["t"]) == (0.5, 0.7, 1.5)
    assert "verify" not in got
    assert page.evaluate("player.playing") is False           # the click was ours, not the screen's
    page.wait_for_function(
        "document.querySelectorAll('#fx .fxev .xy')[1].textContent.startsWith('x 0.2')")
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


