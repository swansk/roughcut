"""Footage in, from anywhere, in a real browser (REDESIGN-GOAL dimension 13, K3 b).

Karl, 2026-10-16: *"there is no clear way to add new footage."* These drive the ways in a
Premiere editor would try, on a running bin, and time what they see:

  * Ctrl+I opens the Import dialog; the editor types a second card's folder into its path
    bar, selects the clips with the keyboard and presses Import; the clips are rows in the
    Project panel with a poster within 5 s, carry their length, rate and size, and the
    dialog walks folders with the keyboard (Backspace up, Esc closes); File › Import from
    Media Browser… opens the same browser; the keys sheet lists Ctrl+I;
  * a file dragged from the OS onto the Project panel shows "Drop to import 1 file", the
    drop uploads it with its progress at the panel's foot, and it is a row;
  * after a first pass, the new clips are offered to the re-run ("2 new clips: add them to
    the cut?"), whose panel says what it keeps; Run gives a proposal (a scripted model —
    never a real call).

Every server here is the suite's own (uvicorn on a free port), its bin a tmp copy of the
fixture clips: nothing writes ~/footage.
"""

from __future__ import annotations

import base64
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import inference  # noqa: E402
from test_firstpass import Scripted, _end_jobs  # noqa: E402
from test_index import _stub_tools  # noqa: E402

DIALOG = '[data-testid="import-dialog"]'
ROW = '[data-testid="media-browser-row"]'
PATH = '[data-testid="media-browser-path"]'


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make(path: Path, src: str, seconds: float = 3.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i", f"{src}:size=320x180:rate=24:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True)


@pytest.fixture(scope="module")
def cards(tmp_path_factory) -> dict:
    """A second card (two clips, one of them named like the bin's own) and a phone clip on
    "this computer"."""
    root = tmp_path_factory.mktemp("import-ui")
    card = root / "card2"
    _make(card / "CLIP_A.MP4", "color=c=red")
    _make(card / "RUN_2.MP4", "color=c=blue")
    (card / "notes.txt").write_text("not footage", encoding="utf-8")
    _make(root / "laptop" / "phone.mp4", "color=c=green", 2.0)
    return {"root": root, "card": card, "phone": root / "laptop" / "phone.mp4"}


@pytest.fixture
def model():
    m = Scripted()
    inference.set_backend(m)
    inference.reset_spend()
    yield m
    if m.hold is not None:
        m.hold.set()
    inference.set_backend(None)


@pytest.fixture
def live(tmp_path, project, monkeypatch, model):
    """A bin of the fixture's three clips (copied, with their sidecars) served for real;
    the index's tools stubbed; every write under tmp_path."""
    import uvicorn
    import server

    footage, sidecars = tmp_path / "trip" / "day1", tmp_path / "sidecars"
    footage.mkdir(parents=True)
    sidecars.mkdir()
    for stem in project["stems"]:
        shutil.copy(project["footage"] / f"{stem}.MP4", footage / f"{stem}.MP4")
        shutil.copy(project["sidecars"] / f"{stem}.audio.json", sidecars / f"{stem}.audio.json")
    monkeypatch.setenv("ROUGHCUT_BACKEND", "claude_cli")
    work = tmp_path / "work"
    server.configure(None, footage, sidecars, work, proxies=False, visual=None)
    _stub_tools(server, monkeypatch, tmp_path)
    monkeypatch.setattr(server, "FIRSTPASS_POLL_S", 0.05)
    server.FIRSTPASS.clear()
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started
    yield {"base": f"http://127.0.0.1:{port}", "footage": footage, "work": work}
    _end_jobs(server, model)
    srv.should_exit = True
    th.join(timeout=10)
    deadline = time.time() + 30
    while any(x["state"] == "running" for x in server.FIRSTPASS.values()) \
            and time.time() < deadline:
        time.sleep(0.1)
    server.FIRSTPASS.clear()


@pytest.fixture
def page(live):
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(live["base"] + "/")
        pg.wait_for_selector("#projList [data-clip]", timeout=20000)
        pg.wait_for_function("window.rcImport && window.rcFirstPass && rcFirstPass.state()",
                             timeout=20000)
        yield pg
        assert errors == [], errors
        b.close()


def _rows(pg) -> list[str]:
    return pg.eval_on_selector_all("#projList [data-clip]", "els => els.map(e => e.dataset.clip)")


def _poster_shown(pg, clip: str) -> bool:
    return pg.evaluate("""(clip) => {
      const el = [...document.querySelectorAll('#projList [data-clip]')].find(e => e.dataset.clip === clip);
      const img = el && el.querySelector('img');
      return !!img && img.complete && img.naturalWidth > 0;
    }""", clip)


def test_ctrl_i_browses_to_a_second_card_and_its_clips_land_with_posters(page, live, cards):
    pg = page
    pg.evaluate("dock.project.view('icon')")         # the Project panel's Icon view: posters
    pg.locator("#tl").click(position={"x": 600, "y": 10})        # the board has the keys
    pg.keyboard.press("Control+i")
    pg.wait_for_selector(f"{DIALOG}[open] {ROW}", timeout=10000)
    assert pg.locator(f"{DIALOG} .im-title").inner_text() == "Import"
    # where it opens: the folder that holds the bin, with the bin's folder in it
    assert pg.locator(PATH).input_value() == str(live["footage"].parent)
    names = pg.eval_on_selector_all(ROW, "els => els.map(e => e.innerText.split('\\n')[0].trim())")
    assert "day1" in names

    pg.fill(PATH, str(cards["card"]))
    pg.press(PATH, "Enter")
    pg.wait_for_function(
        "(p) => [...document.querySelectorAll('[data-testid=media-browser-row]')].some(r =>"
        " r.dataset.path === p)", arg=str(cards["card"] / "RUN_2.MP4"), timeout=10000)
    rows = pg.eval_on_selector_all(ROW, "els => els.map(e => [e.dataset.kind, e.dataset.path])")
    assert [k for k, _ in rows] == ["video", "video"]           # notes.txt is not offered
    # sizes, as a file list shows them
    assert "KB" in pg.locator(ROW).first.inner_text()
    pg.locator('[data-testid="media-browser"]').focus()
    pg.keyboard.press("Control+a")
    assert "2 clips selected" in pg.locator(f"{DIALOG} .im-status").inner_text()
    t0 = time.time()
    pg.locator('[data-testid="import-go"]').click()
    pg.wait_for_function("!document.querySelector('[data-testid=import-dialog]').open",
                         timeout=5000)
    pg.wait_for_function(
        "() => ['CLIP_A-card2.MP4', 'RUN_2.MP4'].every(c =>"
        " document.querySelector(`#projList [data-clip=\"${c}\"]`))", timeout=5000)
    deadline = t0 + 5.0
    while time.time() < deadline and not all(_poster_shown(pg, c)
                                             for c in ("CLIP_A-card2.MP4", "RUN_2.MP4")):
        pg.wait_for_timeout(100)
    took = time.time() - t0
    assert all(_poster_shown(pg, c) for c in ("CLIP_A-card2.MP4", "RUN_2.MP4")), \
        f"no poster {took:.1f} s after Import"
    assert took <= 5.0, took
    # the clips carry their facts: the list view's Duration / Frame Rate / Resolution
    clips = {c["clip"]: c for c in httpx.get(live["base"] + "/api/clips", timeout=20).json()["clips"]}
    red = clips["CLIP_A-card2.MP4"]
    assert red["source"] == "import" and red["duration"] == pytest.approx(3.0, abs=0.1)
    assert red["fps"] == pytest.approx(24.0) and red["width"] == 320
    assert sorted(_rows(pg)) == ["CLIP_A-card2.MP4", "CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4",
                                 "RUN_2.MP4"]

    # the browser again, from the File menu: it remembers the folder; the clips just
    # imported read "in project"; Backspace goes up a folder; Esc closes it
    pg.locator("#menubar .menu[data-menu=file] .mtop").click()
    pg.locator('[data-testid="menu-media-browser"]').click()
    pg.wait_for_selector(f"{DIALOG}[open] {ROW}", timeout=10000)
    assert pg.locator(f"{DIALOG} .im-title").inner_text() == "Media Browser"
    assert pg.locator(PATH).input_value() == str(cards["card"])
    pg.wait_for_function("document.querySelectorAll('[data-testid=media-browser-row].have').length === 2",
                         timeout=5000)
    pg.locator('[data-testid="media-browser"]').focus()
    pg.keyboard.press("Backspace")
    pg.wait_for_function("(p) => document.querySelector('[data-testid=media-browser-path]').value === p",
                         arg=str(cards["root"]), timeout=5000)
    # the cursor is on the folder it came out of; Enter goes back in
    assert pg.locator(f"{ROW}.cur").get_attribute("data-path") == str(cards["card"])
    pg.keyboard.press("Enter")
    pg.wait_for_function("(p) => document.querySelector('[data-testid=media-browser-path]').value === p",
                         arg=str(cards["card"]), timeout=5000)
    pg.keyboard.press("Escape")
    pg.wait_for_function("!document.querySelector('[data-testid=import-dialog]').open", timeout=5000)

    # Ctrl+I is in the keys sheet, in The bin's group
    pg.keyboard.press("?")
    pg.wait_for_selector("#keysOverlay:not([hidden])", timeout=5000)
    assert "Ctrl+I" in pg.locator("#keysOverlay").inner_text()


def test_a_file_dropped_on_the_project_panel_is_uploaded_with_progress(page, live, cards):
    pg = page
    data = base64.b64encode(cards["phone"].read_bytes()).decode("ascii")
    pg.evaluate("""(b64) => {
      const bin = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
      window.__dt = new DataTransfer();
      window.__dt.items.add(new File([bin], 'phone.mp4', {type: 'video/mp4'}));
      window.__strip = [];
      new MutationObserver(() => {
        const s = document.querySelector('[data-testid=import-progress]');
        if (s && !s.hidden) window.__strip.push(s.innerText.replace(/\\s+/g, ' ').trim());
      }).observe(document.getElementById('pProject'), {subtree: true, childList: true,
                                                         attributes: true, characterData: true});
      const el = document.getElementById('projList');
      el.dispatchEvent(new DragEvent('dragenter', {bubbles: true, cancelable: true, dataTransfer: window.__dt}));
      el.dispatchEvent(new DragEvent('dragover', {bubbles: true, cancelable: true, dataTransfer: window.__dt}));
    }""", data)
    drop = pg.locator('[data-testid="project-drop"]')
    assert drop.is_visible()
    assert "Drop to import 1 file" in drop.inner_text()
    # the bin's reads a landing makes: every /api/clips asked in the same task as an
    # `rc:clips-changed` (import.js landed() and whatever it calls, synchronously)
    pg.evaluate("""() => {
      window.__landings = 0; window.__landingReads = 0;
      const send = window.dispatchEvent.bind(window), get = window.fetch;
      window.dispatchEvent = (ev) => {
        if (ev && ev.type === 'rc:clips-changed') {
          window.__landings += 1; window.__landing = true;
          setTimeout(() => { window.__landing = false; }, 0);
        }
        return send(ev);
      };
      window.fetch = (url, opts) => {
        if (window.__landing && String(url).startsWith('/api/clips')) window.__landingReads += 1;
        return get(url, opts);
      };
    }""")
    pg.evaluate("""() => document.getElementById('projList').dispatchEvent(
      new DragEvent('drop', {bubbles: true, cancelable: true, dataTransfer: window.__dt}))""")
    assert not drop.is_visible()
    pg.wait_for_selector('#projList [data-clip="phone.mp4"]', timeout=10000)
    # one landing, one read of the bin (the Project panel's, on the event) — not two
    pg.wait_for_function("window.__landings >= 1", timeout=10000)
    assert pg.evaluate("[window.__landings, window.__landingReads]") == [1, 1]
    pg.wait_for_function("(() => { const s = document.querySelector('[data-testid=import-progress]');"
                         " return s && !s.hidden && s.innerText.includes('Imported 1 clip'); })()",
                         timeout=10000)
    seen = pg.evaluate("window.__strip")
    assert any(s.startswith("Uploading 1 of 1 · phone.mp4") for s in seen), seen
    clip = next(c for c in httpx.get(live["base"] + "/api/clips", timeout=20).json()["clips"]
                if c["clip"] == "phone.mp4")
    assert clip["source"] == "upload"
    assert Path(clip["path"]).parent == live["work"] / "media" / "day1" / "uploads"
    assert Path(clip["path"]).read_bytes() == cards["phone"].read_bytes()
    assert not (live["footage"] / "phone.mp4").exists()
    # the batch was a job the header's row could show
    job = next(iter(__import__("server").IMPORTS.values()))
    assert job["kind"] == "import" and job["state"] == "done"


def _wait_state(base, until, timeout=90.0) -> dict:
    deadline = time.time() + timeout
    s = {}
    while time.time() < deadline:
        s = httpx.get(base + "/api/firstpass", timeout=20).json()
        if s["state"] in until:
            return s
        time.sleep(0.2)
    raise AssertionError(f"first pass did not reach {until}: {s.get('state')}")


def test_after_a_pass_imported_clips_are_offered_to_the_rerun(page, live, cards):
    pg = page
    base = live["base"]
    pg.locator('[data-testid="firstpass-button"]').click()
    pg.wait_for_selector('[data-testid="firstpass-run"]:not([disabled])', timeout=10000)
    pg.locator('[data-testid="firstpass-run"]').click()
    _wait_state(base, ("done", "stale-after-edits"))
    pg.wait_for_selector("#proposal", state="visible", timeout=30000)   # its proposal opened
    offer = pg.locator('[data-testid="firstpass-new-clips"]')
    assert not offer.is_visible()

    # import two clips (the dialog's own route), then the board learns of it
    got = httpx.post(base + "/api/import", json={"paths": [str(cards["card"])]},
                     timeout=20).json()
    assert got["added"] == ["CLIP_A-card2.MP4", "RUN_2.MP4"]
    pg.evaluate("rcFirstPass.refresh()")
    offer.wait_for(state="visible", timeout=10000)
    assert offer.inner_text().startswith("2 new clips: add them to the cut?")
    s = httpx.get(base + "/api/firstpass", timeout=20).json()
    assert s["new_clips"] == ["CLIP_A-card2.MP4", "RUN_2.MP4"]
    assert "2 new clips since the last run" in s["stale"]

    # Re-run first pass… opens the panel: what a re-run keeps, the new clips, its price
    pg.locator('[data-testid="firstpass-new-clips-go"]').click()
    panel = pg.locator('[data-testid="firstpass-panel"]')
    panel.wait_for(state="visible", timeout=5000)
    text = panel.inner_text()
    assert "2 new clips since the last run" in text
    assert "A re-run hears and looks at CLIP_A-card2, RUN_2" in text
    assert "free on your plan" in text and "Re-run first pass" in text
    assert pg.locator('[data-testid="firstpass-keep"]').is_visible()
    assert not offer.is_visible()
    pg.locator('[data-testid="firstpass-run"]').click()
    s2 = _wait_state(base, ("done", "stale-after-edits"))
    assert s2["last"]["clip_names"] == ["CLIP_A-card2.MP4", "CLIP_A.MP4", "CLIP_B.MP4",
                                        "CLIP_C.MP4", "RUN_2.MP4"]
    assert not s2.get("new_clips"), "after the re-run nothing is new"
    pg.wait_for_function("!document.querySelector('[data-testid=firstpass-new-clips]')"
                         " || document.querySelector('[data-testid=firstpass-new-clips]').hidden",
                         timeout=10000)
    # the re-run heard the imported clips (the stub index wrote their sidecars)
    import server
    assert {"CLIP_A-card2", "RUN_2"} <= server.analysed_stems()


def test_a_file_dropped_on_the_timeline_is_uploaded_and_put_in_the_sequence(page, live, cards):
    """Premiere's drag from the desktop onto a sequence: the file is uploaded, then
    inserted whole at the drop point (here the empty sequence's start), one undo step."""
    pg = page
    data = base64.b64encode(cards["phone"].read_bytes()).decode("ascii")
    pg.evaluate("""(b64) => {
      const bin = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
      const dt = new DataTransfer();
      dt.items.add(new File([bin], 'phone.mp4', {type: 'video/mp4'}));
      const el = document.getElementById('tl');
      const r = el.getBoundingClientRect();
      const at = {bubbles: true, cancelable: true, dataTransfer: dt,
                  clientX: r.left + 40, clientY: r.top + 60};
      el.dispatchEvent(new DragEvent('dragenter', at));
      el.dispatchEvent(new DragEvent('dragover', at));
      window.__tlDrop = !!document.querySelector('[data-testid=timeline-drop]:not([hidden])');
      el.dispatchEvent(new DragEvent('drop', at));
    }""", data)
    assert pg.evaluate("window.__tlDrop"), "the Timeline showed no drop overlay"
    pg.wait_for_function("tl.state.segs.some(s => s.clip === 'phone.mp4')", timeout=15000)
    seg = pg.evaluate("tl.state.segs.find(s => s.clip === 'phone.mp4')")
    assert seg["in"] == 0 and seg["out"] == pytest.approx(2.0, abs=0.1)
    assert pg.evaluate("tl.filmStart(tl.state.segs.find(s => s.clip === 'phone.mp4').id)") == 0
