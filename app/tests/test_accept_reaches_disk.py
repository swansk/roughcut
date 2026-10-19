"""An accepted AI edit always reaches the disk (redesign wave 14, lane AIOPS3; verdict 10,
punch 2; REDESIGN-GOAL dimension 10).

Verdict 10's K3 walk lost one accepted AI colour change: the client held
`{"contrast":20,"blacks":-10,"saturation":115}` on shot 2 right after Accept, and 40 s
later neither the EDL on disk nor the export's render record had it. The judge's own three
reproductions saved. What they left out was the judge's next click: on the Timeline
panel's header, to give the timeline the keys before Ctrl+M. The AI prompt lives in that
header row, and after an accept its line read "✓ 4 changes applied · Undo ×"; with Selects
open at 1440 the header's centre fell on "Undo", which took the accepted change back —
the client, then the autosave — so the export rendered the cut without it. Reproduced here
before the fix (the click's target was `<button data-ai="undo">Undo</button>`; the cut
after it `{}`, the undo stack's top "proposal"). The line now says "Ctrl+Z undoes" in
words; a click on it changes nothing.

Here, in real Chromium on the fixture bin with a scripted model (never a real call):
  * the judge's exact sequence, ten times on ten fresh boards: a plan-kind ask's proposal
    accepted; at once a colour ask (Esc, a click on a block, Ctrl+J, the words, Enter);
    its review; Accept; a click on Effect Controls; a click on the Timeline's header;
    Ctrl+M → Export. Each time the EDL on disk carries the colour within 2 s of Accept and
    the export's render record carries it too;
  * an Export started while the autosave still holds the edit waits for it (the render
    reads the colour block from the disk);
  * the header's save state turns amber "unsaved…" whenever the board holds an edit the
    disk has not got for more than 3 s — a hung save, or an edit whose path never touched
    the autosave (that one is saved by the watch, and said) — and a quiet board, or an AI
    review's After picture, saves nothing and says nothing.
"""

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
from roughcut import config, inference  # noqa: E402

SEGS = [{"id": "shot-a", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
        {"id": "shot-b", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"},
        {"id": "shot-c", "clip": "CLIP_C.MP4", "in": 2.0, "out": 5.0, "why": "third"}]

# the plan's cut: what the Ask job proposes for "make it tighter"
PLAN_CUT = [{"clip": "CLIP_C.MP4", "in": 0.5, "out": 3.0, "why": "open on the walk"},
            {"clip": "CLIP_A.MP4", "in": 0.5, "out": 2.0, "why": "the greeting"},
            {"clip": "CLIP_B.MP4", "in": 2.4, "out": 4.0, "why": "the reply"}]

COLOUR_KEYS = {"contrast": 20.0, "blacks": -10.0, "saturation": 115.0}
AMBER = "rgb(226, 177, 79)"          # workspace.css --warn


class Model:
    """The assist calls answer from `assist` in turn; the plan's Ask call answers PLAN_CUT;
    anything else falls back ("{}")."""
    name = "accept-disk-scripted"

    def __init__(self):
        self.assist: list[dict] = []

    def complete(self, request):
        schema = request.schema or {}
        if "kind" in schema and "ops" in schema:
            ans = self.assist.pop(0) if self.assist else {"kind": "answer", "text": "ok"}
            text = json.dumps(ans)
        elif "segments" in schema:
            text = json.dumps({"segments": PLAN_CUT, "notes": "a tighter cut"})
        else:
            text = "{}"
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


def _disk(project) -> dict:
    return json.loads(Path(project["edl"]).read_text(encoding="utf-8"))


def _carries(edl: dict, shot: int) -> tuple[bool, bool]:
    """(the shot's Effect Controls, the shot's hand balance — the temperature — in the
    colour block)."""
    segs = edl.get("segments") or []
    if len(segs) <= shot:
        return False, False
    s = segs[shot]
    c = s.get("controls") or {}
    ctl = all(abs(float(c.get(k, 1e9)) - v) < 1e-6 for k, v in COLOUR_KEYS.items())
    col = ((edl.get("colour") or {}).get("shots") or {}).get(s.get("id")) or {}
    return ctl, (col.get("balance") or {}).get("source") == "hand"


class DiskWatch(threading.Thread):
    """From Accept on, the first moment the EDL on disk carries the colour (or None)."""

    def __init__(self, project, shot: int, limit: float = 2.0):
        super().__init__(daemon=True)
        self.project, self.shot, self.limit = project, shot, limit
        self.t0 = time.time()
        self.at: float | None = None

    def run(self):
        while time.time() - self.t0 < self.limit and self.at is None:
            try:
                if all(_carries(_disk(self.project), self.shot)):
                    self.at = time.time() - self.t0
                    return
            except (OSError, ValueError):
                pass              # caught mid-write
            time.sleep(0.02)


class Hold:
    """Every PUT /api/project held on its way until `release()` (a stalled disk)."""

    def __init__(self, page):
        self.page, self.held, self.open = page, [], False
        page.route("**/api/project", self._on)

    def _on(self, route):
        if route.request.method != "PUT" or self.open:
            route.continue_()
        else:
            self.held.append(route)

    def release(self):
        self.open = True
        for r in self.held:
            r.continue_()
        self.held = []


def _new_record(project, before: set[str], timeout: float = 15.0) -> Path:
    end = time.time() + timeout
    while time.time() < end:
        new = [p for p in Path(project["work"]).glob("render_*.json") if p.name not in before]
        if new:
            time.sleep(0.05)        # written in one go, but let it close
            return new[0]
        time.sleep(0.03)
    raise AssertionError("the export wrote no render record")


def _stop_render(page, rec: Path) -> None:
    """The record is what is judged: the encode is cancelled and waited out (one render
    at a time)."""
    import server
    from roughcut import progress
    job = rec.stem.split("_", 1)[1]
    page.evaluate("(j) => fetch('/api/render/' + j + '/cancel', {method: 'POST'})", job)
    end = time.time() + 60
    while time.time() < end:
        j = server.RENDERS.get(job)
        if j is None or j["state"] in progress.TERMINAL:
            return
        time.sleep(0.1)


def open_board(browser, url, project, *, selects=False):
    _seed(project)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_selector("#tl .blk")
    page.wait_for_function("window.rcActions && window.rcAI && window.effects && window.exportDialog")
    if selects:
        # the judge's board after a first pass: Selects open in the Project group, which
        # widens the group to 592 px and puts the Timeline at 601-1066 px at 1440
        page.locator(".ws-tab[data-tool=bin]").first.click()
    return ctx, page, errors


def accept_colour(page, shot_index: int) -> None:
    """A colour ask on the shot (Esc, a click on its block, Ctrl+J, the words, Enter), its
    review, then Accept: the judge's step 5b."""
    page.keyboard.press("Escape")
    blk = page.locator("#tl .tl-lane[data-lane=V1] .blk").nth(shot_index)
    bb = blk.bounding_box()
    page.mouse.click(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
    sid = page.evaluate("(i) => String(tl.idAt(i))", shot_index)
    MODEL.assist = [{"kind": "ops", "summary": "more punch and a cooler winter tone", "ops": [
        {"op": "set", "id": sid, "prop": "contrast", "value": 20},
        {"op": "set", "id": sid, "prop": "blacks", "value": -10},
        {"op": "set", "id": sid, "prop": "saturation", "value": 115},
        {"op": "set", "id": sid, "prop": "temperature", "value": -18}]}]
    page.keyboard.press("Control+j")
    page.keyboard.type("this shot looks flat and grey, give it more punch and a cooler winter tone")
    page.keyboard.press("Enter")
    page.wait_for_selector("[data-testid=ai-result][data-kind=ops]", timeout=30000)
    op = page.locator("[data-testid=ai-result-open]")
    if op.count() and op.is_visible() and op.inner_text().strip() == "Review":
        op.click()                  # the judge pressed Review too (it had opened itself)
    page.wait_for_selector("[data-testid=ai-review] [data-testid=ai-op-row]")
    page.wait_for_function(
        "(() => { const b = document.querySelector('[data-testid=ai-accept-all]'); return b && !b.disabled; })()")
    page.locator("[data-testid=ai-review] [data-testid=ai-accept-all]").click()


def run_once(browser, url, project, n: int) -> dict:
    """The judge's sequence (verdict 10, fresh.log 22:35:51 → 22:36:48) on a fresh board."""
    ctx, page, errors = open_board(browser, url, project, selects=True)
    out: dict = {"run": n}
    try:
        # 1. a plan-kind ask → its proposal, accepted
        MODEL.assist = [{"kind": "plan", "summary": "a tighter cut", "note": "make it tighter"}]
        page.keyboard.press("Control+j")
        page.wait_for_function(
            "document.activeElement && document.activeElement.dataset.testid === 'ai-prompt-input'")
        page.keyboard.type("make it tighter and open on the walk")
        page.keyboard.press("Enter")
        page.wait_for_selector("#acceptProposal", state="visible", timeout=30000)
        page.locator("#acceptProposal").click()
        page.wait_for_function("segs.length === 3 && segs[0].clip === 'CLIP_C.MP4'")
        # 2. at once a colour ask on shot 2, reviewed, 3. Accept
        accept_colour(page, 1)
        watch = DiskWatch(project, 1)
        watch.start()
        page.wait_for_function("!rcActions.open")
        out["client"] = page.evaluate("JSON.stringify(tl.byId(tl.idAt(1)).controls || {})")
        # 4. Effect Controls, the Timeline's header (the keys go to the timeline), Ctrl+M
        page.locator(".ws-tab[data-tool=inspect]").first.click()
        head = page.locator("#pTimeline .ws-head").first
        hb = head.bounding_box()
        out["head_hit"] = page.evaluate(
            "([x, y]) => { const e = document.elementFromPoint(x, y); return e ? e.outerHTML.slice(0, 120) : null; }",
            [hb["x"] + hb["width"] / 2, hb["y"] + hb["height"] / 2])
        head.click()
        page.keyboard.press("Control+m")
        page.wait_for_selector("[data-testid=export-go]", state="visible")
        page.wait_for_function(
            "(() => { const b = document.querySelector('[data-testid=export-go]'); return b && !b.disabled; })()",
            timeout=30000)
        before = {p.name for p in Path(project["work"]).glob("render_*.json")}
        page.locator("[data-testid=export-go]").click()
        rec = _new_record(project, before)
        out["render"] = _carries(json.loads(rec.read_text(encoding="utf-8")), 1)
        watch.join(timeout=5)
        out["disk_s"] = None if watch.at is None else round(watch.at, 2)
        out["client_end"] = page.evaluate("JSON.stringify(tl.byId(tl.idAt(1)).controls || {})")
        out["undo"] = page.locator("#undo").get_attribute("title")
        _stop_render(page, rec)
    finally:
        ctx.close()
    out["errors"] = errors
    return out


def test_an_accepted_ai_colour_change_reaches_the_disk_and_the_export_ten_times(browser, live_server, project):
    results = [run_once(browser, live_server, project, n) for n in range(10)]
    print(json.dumps(results, indent=1))
    bad = [r for r in results if r["disk_s"] is None or not all(r["render"]) or r["errors"]
           or r["client_end"] != r["client"] or "AI: 4 colour changes" not in (r["undo"] or "")]
    assert not bad, json.dumps(bad, indent=1)
    # the header's centre is the AI line, as it was for the judge — and it undoes nothing
    assert all("aip" in (r["head_hit"] or "") or "ai-" in (r["head_hit"] or "") for r in results), results


def test_an_export_started_before_the_autosave_lands_waits_for_it(browser, live_server, project):
    """Accept, then Export at once while the save is held on its way: the render's colour
    block comes from the disk, so the export waits for the save (roughcutFlush) and its
    record carries the accepted colour."""
    ctx, page, errors = open_board(browser, live_server, project)
    try:
        hold = Hold(page)
        accept_colour(page, 1)
        page.wait_for_function("!rcActions.open")
        before = {p.name for p in Path(project["work"]).glob("render_*.json")}
        page.keyboard.press("Control+m")
        page.wait_for_selector("[data-testid=export-go]", state="visible")
        page.wait_for_function(
            "(() => { const b = document.querySelector('[data-testid=export-go]'); return b && !b.disabled; })()")
        page.locator("[data-testid=export-go]").click()
        page.wait_for_timeout(1500)                  # the autosave (700 ms) is on its way, held
        assert hold.held, "the accept's save never left"
        assert not [p for p in Path(project["work"]).glob("render_*.json") if p.name not in before], \
            "the export started before the accepted edit was on disk"
        hold.release()
        rec = _new_record(project, before)
        assert _carries(json.loads(rec.read_text(encoding="utf-8")), 1) == (True, True)
        assert _carries(_disk(project), 1) == (True, True)
        _stop_render(page, rec)
    finally:
        ctx.close()
    assert errors == [], errors


def _save_state(page) -> dict:
    return page.evaluate("""() => { const e = document.querySelector('#saveState');
      return {state: e.dataset.state, late: !!e.dataset.late, text: e.textContent,
              visible: !!(e.offsetWidth || e.offsetHeight), color: getComputedStyle(e).color}; }""")


def test_a_held_save_turns_the_header_amber_after_3_s(browser, live_server, project):
    ctx, page, errors = open_board(browser, live_server, project)
    try:
        hold = Hold(page)
        page.evaluate("tl.setRange('shot-a', 1.25, null)")
        t0 = time.time()
        page.wait_for_timeout(1200)
        assert hold.held and not _save_state(page)["visible"], "the autosave's own wait is nothing wrong"
        page.wait_for_function("document.querySelector('#saveState').dataset.late === '1'", timeout=4000)
        assert time.time() - t0 <= 3.6
        page.wait_for_timeout(max(0.0, 3.2 - (time.time() - t0)) * 1000)
        s = _save_state(page)
        assert s["visible"] and s["text"] == "unsaved…" and s["color"] == AMBER, s
        hold.release()
        page.wait_for_function("document.querySelector('#saveState').dataset.state === 'saved'", timeout=8000)
        assert not _save_state(page)["visible"]
        assert _disk(project)["segments"][0]["in"] == 1.25
    finally:
        ctx.close()
    assert errors == [], errors


def test_an_edit_that_never_touched_the_autosave_is_saved_and_said(browser, live_server, project):
    """An edit whose path forgot the autosave (made here by writing the shot directly): the
    watch sees the board differ from what it last saved, saves it within ~1.5 s, and counts
    it (`saveHeals`); held off the disk, the header turns amber after 3 s."""
    ctx, page, errors = open_board(browser, live_server, project)
    try:
        assert page.evaluate("window.saveHeals") == 0
        page.evaluate("segs[1].controls = {saturation: 80}")
        end = time.time() + 2.5
        while time.time() < end and ((_disk(project)["segments"][1].get("controls") or {}).get("saturation") != 80):
            time.sleep(0.05)
        assert (_disk(project)["segments"][1].get("controls") or {}).get("saturation") == 80
        assert page.evaluate("window.saveHeals") == 1
        page.wait_for_function("document.querySelector('#saveState').dataset.state === 'saved'", timeout=5000)
        # held off the disk: amber "unsaved…" once it is older than 3 s, and it lands when let go
        hold = Hold(page)
        page.evaluate("segs[1].controls = {saturation: 60}")
        t0 = time.time()
        page.wait_for_function("document.querySelector('#saveState').dataset.late === '1'", timeout=5000)
        assert time.time() - t0 <= 4.2
        s = _save_state(page)
        assert s["visible"] and s["text"] == "unsaved…" and s["color"] == AMBER, s
        hold.release()
        page.wait_for_function("document.querySelector('#saveState').dataset.state === 'saved'", timeout=8000)
        assert _disk(project)["segments"][1]["controls"]["saturation"] == 60
    finally:
        ctx.close()
    assert errors == [], errors


def test_a_quiet_board_and_an_after_preview_save_nothing(browser, live_server, project):
    """Selecting, zooming, seeking and playing are not edits: no save, nothing said. An AI
    review's After picture is in memory on purpose: no amber, nothing written, however long
    it stays up."""
    ctx, page, errors = open_board(browser, live_server, project)
    puts: list[str] = []
    page.on("request", lambda r: puts.append(r.url) if r.method == "PUT" else None)
    try:
        raw = Path(project["edl"]).read_bytes()
        page.locator("#tl .blk").nth(1).click()
        page.keyboard.press("=")
        page.keyboard.press("-")
        page.evaluate("tl.seek(2.5)")
        page.keyboard.press("Space")
        page.wait_for_timeout(1000)
        page.keyboard.press("Space")
        page.wait_for_timeout(2500)
        assert page.evaluate("window.saveHeals") == 0 and puts == [], puts
        assert Path(project["edl"]).read_bytes() == raw
        # an After picture held up for 4 s
        MODEL.assist = [{"kind": "ops", "summary": "brighter", "ops": [
            {"op": "set", "id": "shot-b", "prop": "saturation", "value": 140}]}]
        page.evaluate("""async () => {
          const r = await (await fetch('/api/assist', {method: 'POST', headers: {'content-type': 'application/json'},
            body: JSON.stringify({text: 'brighter', scope: {kind: 'sequence', playhead: 0}})})).json();
          for (;;) {
            const d = await (await fetch('/api/assist/' + r.job)).json();
            if (d.status !== 'running') { d.job = r.job; return rcActions.review(d); }
            await new Promise((z) => setTimeout(z, 50));
          }
        }""")
        page.wait_for_function("rcActions.previewing")
        page.wait_for_timeout(4000)
        assert page.evaluate("rcActions.previewing")
        s = _save_state(page)
        assert not s["visible"] and not s["late"], s
        assert page.evaluate("window.saveHeals") == 0 and puts == [], puts
        assert Path(project["edl"]).read_bytes() == raw
        page.locator("[data-testid=ai-reject-all]").click()
        page.wait_for_timeout(1500)
        assert page.evaluate("window.saveHeals") == 0 and Path(project["edl"]).read_bytes() == raw
    finally:
        ctx.close()
    assert errors == [], errors
