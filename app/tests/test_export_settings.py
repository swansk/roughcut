"""The Export dialog draws nothing over anything (redesign wave 13, lane EXPORT2; verdict
9, punch 3; REDESIGN-GOAL dimension 9).

The judge, verdict 9: "In the dialog at 1440, the FRAME value '1920×1080 · 59.940 fps' is
drawn on top of BITRATE '≈ 10 Mbps'". In real Chromium on the suite's fixture bin, made a
59.94 sequence of 4K footage (the Killington case), at 1440×900 and 1920×1080: no element
of `#exportDialog` is wider inside than its box, every summary value stays in its own
cell, no label or field crosses another, and the dialog is wholly in the window.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

SEGMENTS = [
    {"id": "e1", "clip": "CLIP_A.MP4", "in": 0.5, "out": 4.5, "why": "one"},
    {"id": "e2", "clip": "CLIP_B.MP4", "in": 0.0, "out": 4.0, "why": "two"},
    {"id": "e3", "clip": "CLIP_C.MP4", "in": 1.0, "out": 4.5, "why": "three"},
]
DIALOG = "[data-testid=export-dialog]"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed(project) -> None:
    seed = {"variant": "T", "title": "test cut", "orient": "none", "story": "",
            "target_s": [5, 30], "segments": [dict(s) for s in SEGMENTS]}
    Path(project["edl"]).write_text(json.dumps(seed, indent=1), encoding="utf-8")


@pytest.fixture(scope="module")
def live_server(project):
    import uvicorn
    import server

    original = project["edl"].read_text(encoding="utf-8")
    _seed(project)
    server.configure(project["edl"], project["footage"], project["sidecars"],
                     project["work"], proxies=False, assets=project["assets"])
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
    project["edl"].write_text(original, encoding="utf-8")


@pytest.fixture(scope="module")
def browser(live_server):
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        yield b
        b.close()


@pytest.fixture
def board(browser, live_server, project, monkeypatch):
    """One page at 1440×900 on the freshly seeded sequence; its errors fail the test."""
    import server
    monkeypatch.setattr(server, "RENDER_TICK_S", 0.05)
    _seed(project)
    made = []

    def page(width: int = 1440, height: int = 900):
        ctx = browser.new_context(viewport={"width": width, "height": height})
        pg = ctx.new_page()
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.goto(live_server)
        pg.wait_for_selector("#tl .blk")
        pg.wait_for_function(f"tl.state && segs.length === {len(SEGMENTS)}", timeout=15000)
        made.append((ctx, pg))
        return pg

    yield page
    errors = [e for _, pg in made for e in pg.errors]
    for ctx, _ in made:
        ctx.close()
    assert errors == [], errors


def open_dialog(pg) -> None:
    pg.locator("#tl .tl-ruler").click(position={"x": 2, "y": 2})      # focus off any field
    pg.keyboard.press("Control+m")
    pg.wait_for_selector(f"{DIALOG} [data-testid=export-go]", timeout=8000)


# ---------------------------------------------------------------- nothing over anything

OVERLAP_JS = """() => {
  const d = document.getElementById('exportDialog');
  const shown = (el) => { const s = getComputedStyle(el);
    return el.getClientRects().length && s.display !== 'none' && s.visibility !== 'hidden'; };
  const out = [];
  // no element is wider inside than its box (a nowrap value spilling into the next cell)
  for (const el of d.querySelectorAll('*')) {
    if (!shown(el) || el.clientWidth === 0 || ['INPUT', 'OPTION', 'DATALIST'].includes(el.tagName)) continue;
    if (el.scrollWidth > el.clientWidth + 0.5) {
      const titled = el.closest('[title]');
      if (getComputedStyle(el).textOverflow === 'ellipsis' && titled && titled.title.trim()) continue;
      out.push(`${el.tagName}#${el.id}.${el.className}: "${el.textContent.trim().slice(0, 40)}" ${el.scrollWidth} > ${el.clientWidth}`);
    }
  }
  // every summary cell's text inside its own cell (FRAME never over BITRATE)
  for (const cell of d.querySelectorAll('.ex-summary > div')) {
    const c = cell.getBoundingClientRect();
    const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (!n.textContent.trim()) continue;
      const r = document.createRange(); r.selectNodeContents(n);
      for (const q of r.getClientRects()) {
        if (q.left < c.left - 0.5 || q.right > c.right + 0.5)
          out.push(`summary "${n.textContent.trim()}" ${q.left.toFixed(1)}-${q.right.toFixed(1)} outside ${c.left.toFixed(1)}-${c.right.toFixed(1)}`);
      }
    }
  }
  // the form's labels and fields: no two boxes cross
  const items = [...d.querySelectorAll('.ex-grid > *, .ex-summary > div, .ex-foot > *')].filter(shown)
    .map((el) => [el, el.getBoundingClientRect()]);
  for (let i = 0; i < items.length; i++) for (let j = i + 1; j < items.length; j++) {
    const [a, ra] = items[i], [b, rb] = items[j];
    const w = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
    const h = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
    if (w > 0.5 && h > 0.5) out.push(`${a.tagName}#${a.id || a.className} over ${b.tagName}#${b.id || b.className}`);
  }
  // and the dialog is wholly in the window
  const r = d.getBoundingClientRect();
  if (r.top < 0 || r.left < 0 || r.bottom > innerHeight + 0.5 || r.right > innerWidth + 0.5)
    out.push(`dialog ${JSON.stringify([r.left, r.top, r.right, r.bottom])} outside ${innerWidth}x${innerHeight}`);
  return out;
}"""


@pytest.mark.parametrize("size", [(1440, 900), (1920, 1080)], ids=lambda s: f"{s[0]}x{s[1]}")
def test_nothing_in_the_dialog_overlaps_on_a_5994_sequence(board, monkeypatch, size):
    """Verdict 9, punch 3: "the FRAME value '1920×1080 · 59.940 fps' is drawn on top of
    BITRATE '≈ 10 Mbps'". On a 59.94 sequence whose footage is 4K (the Killington case),
    with every preset — Match Sequence at 3840×2160 the widest — no element of the
    dialog is drawn over another."""
    import server
    real = server.media_facts

    def facts(src):
        f = real(src)
        return {**f, "fps": 59.94} if f else f

    monkeypatch.setattr(server, "media_facts", facts)
    monkeypatch.setattr(server, "_native_frame", lambda: (3840, 2160))
    hold = threading.Event()

    def held(preset, key, scope):                  # measuring, until the end
        hold.wait(30)
        server.SAMPLES[key].update(state="failed", error="held by the test")

    monkeypatch.setattr(server, "_sample_job", held)
    try:
        pg = board(*size)
        open_dialog(pg)
        shots = Path(os.environ["ROUGHCUT_SHOTS"]) if os.environ.get("ROUGHCUT_SHOTS") else None
        states = []
        for preset in ("quick", "match", "master", "youtube"):
            pg.select_option("[data-testid=export-preset]", preset)
            states.append((preset, pg.evaluate(OVERLAP_JS)))
        assert pg.inner_text("#exFrame").splitlines() == ["1920×1080", "59.94 fps"]   # YouTube
        pg.select_option("[data-testid=export-preset]", "match")
        assert pg.inner_text("#exFrame").splitlines() == ["3840×2160", "59.94 fps"]
        if shots:
            shots.mkdir(parents=True, exist_ok=True)
            pg.screenshot(path=str(shots / f"export-summary-{size[0]}.png"))
        bad = [(name, s) for name, s in states if s]
        assert bad == [], bad
    finally:
        hold.set()
