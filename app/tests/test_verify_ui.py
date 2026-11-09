"""Wave 21 CLAIMSUI (verdict 16, punch 5): footage with no GoPro telemetry has visual fall
claims the server demotes as unsupported. The AI menu and the seen tab's button must
offer "Check N claims" (GET /api/verify `claims_check`, POST /api/verify/claims), price
first, and what Claude confirms must land back on the board. The menu must never say
"nothing to check" while `claims_to_check > 0`. The model is a scripted backend."""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api",
                                     reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import config, inference  # noqa: E402

SEED = {
    "variant": "T", "title": "test cut", "orient": "none", "story": "", "target_s": [5, 20],
    "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
                 {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}],
}


class Scripted:
    """Claude, confirming whatever fall it is asked about (no real call)."""
    name = "scripted"

    def __init__(self):
        self.seen = []

    def complete(self, request):
        self.seen.append(request)
        text = json.dumps({"verdict": "confirm", "kind": "fall", "who": "wearer",
                           "start": 2.0, "end": 3.0, "confidence": "high",
                           "why": "rider goes down at 2.4 s"})
        time.sleep(0.5)          # long enough for the progress line to be seen
        return inference.Result(content=text, input_tokens=11000, output_tokens=300,
                                backend="scripted", model=config.model_for(request.role),
                                projected_usd=0.04, latency_ms=1, raw=text)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(project, tmp_path_factory):
    import uvicorn
    import server

    original = project["edl"].read_text(encoding="utf-8")
    work = tmp_path_factory.mktemp("claimswork")
    server.configure(project["edl"], project["footage"], project["sidecars"], work,
                     proxies=False, visual=work / "visual", assets=project["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in project["stems"]])
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_level="error"))
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


@pytest.fixture
def bin_state(live_server, project):
    """Every test starts with one visual fall claim on CLIP_A, no telemetry, demotion ON,
    nothing checked, and the scripted backend installed."""
    import server

    Path(project["edl"]).write_text(json.dumps(SEED, indent=1), encoding="utf-8")
    visual = server.STATE["visual"]
    visual.mkdir(parents=True, exist_ok=True)
    (visual / "CLIP_A.visual.json").write_text(json.dumps({
        "clip": "CLIP_A.MP4", "summary": "", "unusable": [], "sheets_read": 1,
        "sheets_total": 1, "params": {"interval_s": 2.0, "cols": 6, "rows": 5,
                                      "role": "analysis"},
        "moments": [{"start": 2.0, "end": 3.0, "kind": "fall", "notable": True,
                     "confidence": "high", "what": "rider goes down", "frames": [2.0]}]}),
        encoding="utf-8")
    server.verifymod.save_state(server.verify_path(), {"mode": "claude", "verdicts": {}})
    server.verifymod.set_providers(telemetry=lambda clip: [])
    server.VERIFIES.clear()
    server.rebuild_events()
    backend = Scripted()
    inference.set_backend(backend)
    yield live_server, backend
    server.verifymod.set_providers(telemetry=None)
    inference.set_backend(None)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


def _fall_events(url):
    import urllib.request
    ev = json.load(urllib.request.urlopen(url + "/api/project"))["events"]
    return [e for e in ev if e["kind"] == "fall"]


@pytest.mark.parametrize("width", [1440, 1920])
def test_a_claim_on_footage_without_telemetry_is_checkable_from_the_menu(
        bin_state, browser, width):
    url, backend = bin_state
    # the server's own account: one claim to check, none asserted on the board
    import urllib.request
    v = json.load(urllib.request.urlopen(url + "/api/verify"))
    assert v["counts"]["claims_to_check"] == 1 and v["claims_check"]["n"] == 1
    assert v["claims_check"]["label"] == "Check 1 claim"
    assert _fall_events(url) == []

    pg = browser.new_page(viewport={"width": width, "height": 1000})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.add_init_script("try { localStorage.setItem('roughcut.tour', 'done') } catch (e) {}")
    pg.goto(url)
    pg.wait_for_selector("#tl .blk")
    pg.wait_for_function("rcClaims.state().claims !== null")

    # the menu: the count and the price, never "nothing to check"
    pg.click('[data-testid="menu-ai"]')
    item = pg.locator('#menubar button[data-cmd="ai:audit"]')
    words = item.inner_text()
    assert "Check 1 claim" in words and "free on your plan" in words, words
    assert "nothing to check" not in words and not item.is_disabled()

    # the click brings up the priced button; nothing has run yet
    item.click()
    btn = pg.locator("#auditClaims")
    pg.wait_for_function("document.activeElement.id === 'auditClaims'")
    assert btn.inner_text() == "Check 1 claim · free on your plan", btn.inner_text()
    assert backend.seen == []

    # run it: progress, then the result back on the board
    btn.click()
    pg.wait_for_function("rcClaims.state().claims && rcClaims.state().claims.job"
                         " && rcClaims.state().claims.job.state === 'done'", timeout=20000)
    pg.wait_for_function("!rcClaims.state().pending", timeout=10000)
    assert len(backend.seen) == 1
    falls = _fall_events(url)
    assert len(falls) == 1 and falls[0]["clip"] == "CLIP_A.MP4"
    # the board's own state has it too, and the seen tab lists it
    assert pg.evaluate("P.events.some((e) => e.kind === 'fall' && e.clip === 'CLIP_A.MP4')")
    pg.evaluate("dock.open('bin')")
    pg.click("#moreFound")
    pg.wait_for_function("getComputedStyle(document.querySelector('#libTabs .tab[data-tab=seen]')).display !== 'none'")
    pg.locator("#libTabs .tab[data-tab=seen]").click()
    assert "rider goes down" in pg.inner_text("#library")
    # nothing left: the menu now says so, honestly
    pg.click('[data-testid="menu-ai"]')
    item = pg.locator('#menubar button[data-cmd="ai:audit"]')
    assert item.is_disabled() and "nothing to check" in item.inner_text()
    assert errors == []
    pg.close()


def test_the_seen_tab_button_is_priced_and_never_the_old_audit(bin_state, browser):
    url, backend = bin_state
    pg = browser.new_page(viewport={"width": 1440, "height": 1000})
    reqs = []
    pg.on("request", lambda r: reqs.append((r.method, r.url.replace(url, ""))))
    pg.add_init_script("try { localStorage.setItem('roughcut.tour', 'done') } catch (e) {}")
    pg.goto(url)
    pg.wait_for_selector("#tl .blk")
    pg.wait_for_function("rcClaims.state().claims !== null")
    pg.evaluate("dock.open('bin')")
    pg.wait_for_function("document.querySelector('#auditClaims').textContent.startsWith('Check 1 claim')")
    pg.click("#auditClaims")
    pg.wait_for_function("rcClaims.state().claims && rcClaims.state().claims.job"
                         " && rcClaims.state().claims.job.state === 'done'", timeout=20000)
    assert ("POST", "/api/verify/claims") in reqs
    assert not any("visual/audit" in u for _, u in reqs)
    assert len(_fall_events(url)) == 1
    pg.close()
