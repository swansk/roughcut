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
               workdir=None, segments=None, clips=None):
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


def wait_effect(page, fx_id: str, pred_js: str, timeout: int = 10000) -> dict:
    """Wait until the server's copy of the effect satisfies `pred_js` (an `e =>` arrow)."""
    page.wait_for_function(
        f"fetch('/api/fx').then(r => r.json()).then(d => {{"
        f" const e = d.effects.find(x => x.id === '{fx_id}'); return !!e && ({pred_js})(e); }})",
        timeout=timeout)
    return next(e for e in effects(page) if e["id"] == fx_id)


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
    assert "hit markers" in text and "2 hits" in text
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
    # remove
    card.locator("button[data-act=remove]").click()
    page.wait_for_function("document.querySelectorAll('#fx .fxcard').length === 0")
    assert api(page, "/api/project")["effects"] == []
    assert effects(page) == []
    assert page.locator("#rail .tool[data-tool=fx] .badge").inner_text() == ""


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

