"""Export Settings › Video: Frame Size, Frame Rate and a target Bitrate (redesign wave 13,
lane EXPORT2; verdict 9, punches 5 and 3; REDESIGN-GOAL dimensions 8, 9 and 15).

The judge, verdict 9: "There is no size, frame-rate or bitrate choice beyond 4 presets"
— "a 720p copy is impossible, as the AI itself said" ("There's no 720p preset in the
library … Quick is the closest"); "While it was 'measuring…', the estimate read 493 MB for
a 228 MB file"; and "In the dialog at 1440, the FRAME value '1920×1080 · 59.940 fps' is
drawn on top of BITRATE '≈ 10 Mbps'".

In real Chromium on the suite's fixture bin, through the real server and assemble.py:

  * a 720p 29.97 export of the sequence probes 1280×720, 30000/1001 constant, and the
    frame count assemble.py's own grid gives the sequence at that rate; the estimate is
    "measuring…" alone (no megabytes) until the sample at exactly that frame and rate
    lands, then within ±25 % of the file; the done line and the default name say 720p;
  * a target bitrate reaches the encoder (VBR) and the estimate is the target's;
  * the dialog never draws one thing over another (`#exportDialog *`), at 1440×900 and
    1920×1080, on a 59.94 sequence, with a 4K frame, a custom size / rate / bitrate and a
    bitrate that is not one.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from fractions import Fraction
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


def measured(pg, timeout: float = 180000) -> None:
    pg.wait_for_function("document.querySelector('#exSize').dataset.source !== 'measuring'",
                         timeout=timeout)


def ffprobe(path: str) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
                        "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate,"
                        "nb_frames,nb_read_packets,bit_rate:format=duration",
                        "-of", "json", path], capture_output=True, text=True, check=True)
    d = json.loads(r.stdout)
    return {**d["streams"][0], "duration": float(d["format"]["duration"])}


def grid_frames(segments: list[dict], rate: Fraction) -> int:
    """The film's frames on assemble.py's grid (`frame_spans`): the sequence's length at
    `rate`, rounded to the nearest frame."""
    t = 0.0
    end = 0
    for s in segments:
        a = round(t * rate)
        t += (s["out"] - s["in"]) / (s.get("speed") or 1)
        end = max(a + 1, round(t * rate))
    return end


def run_export(pg, name: str) -> str:
    pg.fill("[data-testid=export-name]", name)
    pg.fill("#exFolder", "settings")
    pg.click("[data-testid=export-go]")
    pg.wait_for_selector("[data-testid=export-done]", timeout=300000)
    return pg.get_attribute("[data-testid=export-done]", "data-path")


# ---------------------------------------------------------------- 720p 29.97

def test_a_720p_2997_export_is_1280x720_at_30000_1001_with_every_frame(board, project):
    """The punch's own: Frame Size 720, Frame Rate 29.97. The estimate says "measuring…"
    and no number until the sample at 1280×720, 29.97 has landed; then it is within ±25 %
    of the file, which probes 1280×720, 30000/1001 constant, with the frame count of the
    sequence at that rate."""
    import server
    for f in server.sample_dir().glob("*.1280x720*.json"):
        f.unlink()                                       # measured here, not by an earlier test
    pg = board()
    open_dialog(pg)
    pg.select_option("[data-testid=export-preset]", "quick")
    # what the dialog offers, as Premiere's Export Settings › Video does
    sizes = pg.eval_on_selector_all("[data-testid=export-size] option:not([hidden])", "os => os.map(o => o.value)")
    assert sizes == ["match", "2160", "1080", "720", "540"], sizes
    rates = pg.eval_on_selector_all("[data-testid=export-fps] option", "os => os.map(o => o.value)")
    assert rates == ["match", "23.976", "25", "29.97", "59.94"], rates
    assert pg.input_value("[data-testid=export-size]") == "1080"           # Quick's own frame
    assert pg.input_value("[data-testid=export-fps]") == "match"
    assert pg.input_value("[data-testid=export-bitrate]") == ""
    # watch the size cell from here on: it never shows megabytes while measuring
    pg.evaluate("""() => { window.__sizes = [];
      const d = document.getElementById('exportDialog');
      const read = () => { const s = d.querySelector('#exSize');
        if (s) window.__sizes.push([s.dataset.source, s.textContent.trim()]); };
      new MutationObserver(read).observe(d, {subtree: true, childList: true, characterData: true}); }""")
    pg.select_option("[data-testid=export-size]", "720")
    pg.select_option("[data-testid=export-fps]", "29.97")
    assert pg.get_attribute("#exSize", "data-source") == "measuring"
    assert pg.inner_text("#exSize").strip() == "measuring…"
    assert pg.locator("[data-testid=export-go]").is_disabled()
    measured(pg)
    seen = pg.evaluate("window.__sizes")
    early = [t for src, t in seen if src == "measuring"]
    assert early and all("MB" not in t and "GB" not in t for t in early), seen
    assert pg.get_attribute("#exSize", "data-source") == "sample", seen
    assert pg.inner_text("#exFrame").splitlines() == ["1280×720", "29.97 fps"]
    assert pg.input_value("[data-testid=export-name]") == "footage - edl - 720p 29.97 fps"
    assert pg.inner_text("#exAbout").startswith("Custom: Quick 1080p's encoder and sound, at 1280×720, 29.97 fps")
    est = float(pg.get_attribute("#exSize", "data-mb"))
    sample = json.loads(server.sample_file("quick", "1280x720.30000-1001").read_text(encoding="utf-8"))
    assert (sample["width"], sample["height"], sample["fps"]) == (1280, 720, "30000/1001"), sample
    seq = [dict(s) for s in SEGMENTS]
    path = run_export(pg, "seven twenty")
    got = ffprobe(path)
    want_frames = grid_frames(seq, Fraction(30000, 1001))
    assert (int(got["width"]), int(got["height"])) == (1280, 720), got
    assert got["r_frame_rate"] == got["avg_frame_rate"] == "30000/1001", got
    assert int(got["nb_read_packets"]) == int(got["nb_frames"]) == want_frames, (got, want_frames)
    assert abs(got["duration"] - want_frames * 1001 / 30000) < 0.02, got
    real = Path(path).stat().st_size / 1e6
    print(f"720p 29.97: estimate {est:.3f} MB, file {real:.3f} MB ({100 * (est - real) / real:+.1f} %)")
    assert abs(est - real) <= 0.25 * real, (est, real)
    done = pg.inner_text(".ex-done .ex-dim")
    assert "1280×720 · 29.97 fps" in done, done
    meta = json.loads(Path(path).with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["custom"] == {"frame": [1280, 720], "rate": "30000/1001", "bitrate": None}, meta
    # the next estimate at the same settings is measured off this file
    pg.click("[data-testid=export-cancel]")
    open_dialog(pg)
    pg.select_option("[data-testid=export-preset]", "quick")
    pg.select_option("[data-testid=export-size]", "720")
    pg.select_option("[data-testid=export-fps]", "29.97")
    measured(pg)
    assert pg.get_attribute("#exSize", "data-source") == "measured"
    again = float(pg.get_attribute("#exSize", "data-mb"))
    assert abs(again - real) <= 0.25 * real, (again, real)
    # and the preset as it is still says its own frame and rate, untouched by the 720p file
    pg.select_option("[data-testid=export-preset]", "youtube")
    pg.select_option("[data-testid=export-preset]", "quick")
    assert pg.input_value("[data-testid=export-size]") == "1080"
    assert pg.inner_text("#exFrame").splitlines() == ["1920×1080", "24 fps"]
    assert pg.input_value("[data-testid=export-name]") == "footage - edl"


def test_a_target_bitrate_reaches_the_encoder(board, project):
    """Bitrate 3 Mbps: the encoder aims at it (VBR, 1 pass), the estimate is the target's
    own — no sample to wait for — and the file is within ±25 % of it."""
    pg = board()
    open_dialog(pg)
    pg.select_option("[data-testid=export-preset]", "quick")
    pg.select_option("[data-testid=export-size]", "540")
    pg.fill("[data-testid=export-bitrate]", "3")
    assert pg.get_attribute("#exSize", "data-source") == "target"
    assert pg.inner_text("#exBitrateShown").splitlines() == ["3 Mbps", "target"]
    est = float(pg.get_attribute("#exSize", "data-mb"))
    # not a number: Export waits, and says why
    pg.fill("[data-testid=export-bitrate]", "fast")
    assert pg.locator("[data-testid=export-go]").is_disabled()
    assert pg.inner_text("#exSize").strip() == "—"
    pg.fill("[data-testid=export-bitrate]", "3")
    path = run_export(pg, "three megabits")
    got = ffprobe(path)
    assert (int(got["width"]), int(got["height"])) == (960, 540), got
    assert got["r_frame_rate"] == got["avg_frame_rate"] == "24/1", got     # the sequence's
    real = Path(path).stat().st_size / 1e6
    print(f"540p 3 Mbps: estimate {est:.3f} MB, file {real:.3f} MB ({100 * (est - real) / real:+.1f} %); "
          f"video {int(got['bit_rate']) / 1e6:.2f} Mbps")
    assert abs(est - real) <= 0.25 * real, (est, real)
    assert abs(int(got["bit_rate"]) / 1e6 - 3.0) <= 0.75, got
    assert "3 Mbps target" in pg.inner_text(".ex-done .ex-dim")


def test_the_server_refuses_a_size_or_rate_it_cannot_make(board, live_server):
    pg = board()
    for bad, word in (({"size": "480"}, "size"), ({"bitrate": 900}, "bitrate"),
                      ({"bitrate": "fast"}, "bitrate")):
        r = pg.request.post(live_server + "/api/render", data=json.dumps({
            "segments": SEGMENTS[:1], "preset": "quick", "name": "never", **bad}),
            headers={"content-type": "application/json"})
        assert r.status == 400 and word in r.json()["detail"], (bad, r.status, r.text())
    # the dialog's figures for a size / rate / bitrate of the export's own
    e = pg.request.get(live_server + "/api/export/estimate?preset=match&size=720&fps=59.94&bitrate=12").json()
    assert (e["width"], e["height"], e["fps_rational"], e["source"], e["video_mbps"]) == \
        (1280, 720, "60000/1001", "target", 12.0), e
    assert pg.request.get(live_server + "/api/export/estimate?preset=quick&size=721").status == 400


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
    in every state the summary can be in — the preset as it is, Match Sequence at
    3840×2160, a custom 720p 23.976 at a 199.5 Mbps target, measuring, and a bitrate
    that is not one — no element of the dialog is drawn over another."""
    import server
    real = server.media_facts

    def facts(src):
        f = real(src)
        return {**f, "fps": 59.94} if f else f

    monkeypatch.setattr(server, "media_facts", facts)
    monkeypatch.setattr(server, "_native_frame", lambda: (3840, 2160))
    hold = threading.Event()

    def held(preset, key, scope, choice=None):                  # measuring, until the end
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
        assert "Match Sequence (59.94)" in pg.inner_text("[data-testid=export-fps]")
        pg.select_option("[data-testid=export-size]", "720")
        pg.select_option("[data-testid=export-fps]", "23.976")
        assert pg.inner_text("#exSize").strip() == "measuring…"
        states.append(("custom, measuring", pg.evaluate(OVERLAP_JS)))
        pg.fill("[data-testid=export-bitrate]", "199.5")
        assert pg.inner_text("#exBitrateShown").splitlines()[0] == "199.5 Mbps"
        states.append(("custom at 199.5 Mbps", pg.evaluate(OVERLAP_JS)))
        if shots:
            shots.mkdir(parents=True, exist_ok=True)
            pg.screenshot(path=str(shots / f"export-settings-{size[0]}.png"))
        pg.fill("[data-testid=export-bitrate]", "a lot")
        states.append(("not a bitrate", pg.evaluate(OVERLAP_JS)))
        bad = [(name, s) for name, s in states if s]
        assert bad == [], bad
    finally:
        hold.set()
