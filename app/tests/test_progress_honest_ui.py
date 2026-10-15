"""Progress that never runs ahead of the work (redesign verdict 2, punch 6).

Verdict 2: the header's job pill said "18 % · ~1 min left" 7 s into an index that had
heard nothing, and "90 %" from 3 min to 15 min while the counts went from looked 0/12
to 1/12 — the index job carried a fixed 60 s estimate called "measured"; /open said
"about 0:07 left" at 6 min with 0 of 12 ready; and the long "Now Indexing — …" chip
ran over the job pill, which covered the `?` button.

Here the index is real but its listening never finishes (a stub that waits on a flag),
so the run sits at heard 0/3. On `/` and `/open` at 1440×900, every % on screen and
every bar's width is at most the share the counts prove (`flow.honest_pct`, read after
the screen so the counts can only have grown), no "… left" is shown under the run's own
pace (with nothing heard there is no pace, so none at all), and on `/` no two of the
header's children overlap.
"""

from __future__ import annotations

import re
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

playwright_api = pytest.importorskip("playwright.sync_api",
                                     reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import flow, progress  # noqa: E402
from test_index import _stub_tools  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_index_job_has_no_clock_estimate_and_is_held_to_its_counts():
    """The unit: a job with the journal at heard 0/3 (probes and telemetry settled,
    which the journal's own % counts as work) reads 0 % and no time left, however long
    it has run; with stages measured its time left is never under its pace."""
    import server

    jid = server.index_entry()
    job = server.INDEXES[jid]
    try:
        assert job["eta_source"] == "none" and job.get("estimated_s") is None
        stages = {s: {"done": 0, "skipped": 0, "queued": 3, "running": 0}
                  for s in ("probe", "telemetry", "asr", "proxy", "look", "close", "picks")}
        stages["probe"]["done"] = 3
        stages["telemetry"]["skipped"] = 3
        job["journal"] = {"clips": 3, "missing": 0, "stages": stages, "pct": 28.6,
                          "eta_s": 4.0, "eta_unmeasured": ["asr", "look"]}
        job.advance("index", 0.286)
        job["started"] = time.time() - 600
        snap = job.snapshot()
        assert snap["pct"] == 0.0 and snap["eta_s"] is None, snap
        # one clip heard and previewed, every counted stage measured: 2 of 12 clip-stages
        stages["asr"]["done"] = 1
        stages["proxy"]["done"] = 1
        job["journal"] = dict(job["journal"], stages=stages, eta_unmeasured=[],
                              eta_s=7.0, pct=40.0)
        job.advance("index", 0.40)
        snap = job.snapshot()
        honest = 100.0 * flow.honest_pct(flow.journal_counts(job["journal"]))
        assert snap["pct"] <= honest + 0.05 and honest == pytest.approx(16.67, abs=0.01)
        # 600 s for 1/6 of the work: at least 3000 s more, not the journal's 7 s
        assert snap["eta_s"] >= 600 * (1 - 1 / 6) / (1 / 6) - 1, snap
    finally:
        job.finish("cancelled")
        server.INDEXES.pop(jid, None)


@pytest.fixture
def stuck(tmp_path, project, monkeypatch):
    """A fresh bin with no sidecars, served, its index running and its listening held
    until the test ends."""
    import uvicorn
    import server

    flag = tmp_path / "release"
    server.configure(None, project["footage"], None, tmp_path, proxies=False, visual=None)
    _stub_tools(server, monkeypatch, tmp_path)
    monkeypatch.setattr(server, "analyze_cmd", lambda skip, force: [
        sys.executable, "-c",
        "import os, sys, time\nwhile not os.path.exists(sys.argv[1]): time.sleep(0.1)",
        str(flag)])
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    base = f"http://127.0.0.1:{port}"
    r = httpx.post(base + "/api/index", json={}, timeout=20)
    assert r.status_code == 200, r.text
    job = r.json()["job"]
    deadline = time.time() + 20
    while time.time() < deadline:
        j = server.INDEXES[job]
        prog = j.get("journal") or {}
        if (prog.get("stages") or {}).get("asr", {}).get("running"):
            break
        time.sleep(0.1)
    yield base
    server.INDEXES[job]["cancel"] = True
    flag.write_text("go")
    deadline = time.time() + 30
    while server.INDEXES[job]["state"] not in progress.TERMINAL and time.time() < deadline:
        time.sleep(0.1)
    srv.should_exit = True
    th.join(timeout=10)


def _honest(base) -> float:
    st = {s["key"]: s for s in httpx.get(base + "/api/flow", timeout=20).json()["stages"]}
    return 100.0 * flow.honest_pct(st["index"]["counts"])


SEEN_JS = r"""() => {
  const vis = (el) => { const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const texts = [];
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walk.nextNode()) {
    const n = walk.currentNode, el = n.parentElement;
    if (el && n.textContent.trim() && vis(el)) texts.push(n.textContent.trim());
  }
  const bars = [...document.querySelectorAll('#progBar, .job .jbar i')]
    .filter((el) => el.closest('[hidden]') === null)
    .map((el) => parseFloat(el.style.width) || 0);
  return { texts, bars };
}"""


def _secs(words: str) -> float | None:
    m = re.search(r"(\d+):(\d\d)\s*left", words)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.search(r"(\d+)\s*s left", words)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+)\s*min left", words)
    if m:
        return 60.0 * int(m.group(1))
    if re.search(r"a minute left", words):
        return 60.0
    return None


def test_every_visible_percent_and_eta_is_held_to_the_counts(stuck):
    base = stuck
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for path in ("/", "/open"):
            pg = b.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(base + path)
            if path == "/":
                pg.wait_for_selector("#progress .job", timeout=20000)
                pg.wait_for_selector('[data-testid="firstpass-button"]', timeout=20000)
            else:
                pg.wait_for_selector("#progLine", state="visible", timeout=20000)
            for _ in range(4):                           # a few polls' worth
                pg.wait_for_timeout(1200)
                seen = pg.evaluate(SEEN_JS)
                started = httpx.get(base + "/api/jobs", timeout=20).json()["jobs"]
                ix = next(j for j in started if j["kind"] == "index")
                honest = _honest(base)
                pcts = [float(m) for t in seen["texts"]
                        for m in re.findall(r"(\d+(?:\.\d+)?)\s*%", t)]
                assert pcts or path == "/open", (path, seen["texts"][:40])
                for p in pcts + seen["bars"]:
                    assert p <= honest + 0.5, (path, p, honest, seen)
                frac = honest / 100.0
                for t in seen["texts"]:
                    if "left" not in t:
                        continue
                    secs = _secs(t)
                    if secs is None:
                        continue
                    assert frac > 0, f"{path}: an ETA with nothing done: {t!r}"
                    floor = ix["elapsed_s"] * (1 - frac) / frac
                    assert secs >= floor - 5, (path, t, floor)
            if path == "/":
                # the header's children never overlap at 1440×900, and stay inside it
                boxes = pg.evaluate("""() => {
                  const h = document.querySelector('header').getBoundingClientRect();
                  return { h: [h.left, h.right], kids: [...document.querySelectorAll('header > *')]
                    .filter((el) => { const r = el.getBoundingClientRect();
                      return r.width > 0 && getComputedStyle(el).display !== 'none'; })
                    .map((el) => { const r = el.getBoundingClientRect();
                      return [el.id || el.className || el.tagName, r.left, r.right]; }) };
                }""")
                kids = sorted(boxes["kids"], key=lambda k: k[1])
                names = [k[0] for k in kids]
                assert "flow" in names and "progress" in names and "firstpass" in names, names
                for (a, _al, ar), (c, cl, _cr) in zip(kids, kids[1:]):
                    assert ar <= cl + 0.5, f"{a} overlaps {c}: {kids}"
                assert kids[-1][2] <= boxes["h"][1] + 0.5, kids
                # the Now chip is drawn (and ellipsizes inside itself when the row is full)
                chip = pg.evaluate("""() => { const s = document.querySelector('#flowNext span');
                  return s ? [s.scrollWidth, s.clientWidth] : null; }""")
                assert chip is not None
            assert errors == [], (path, errors)
            pg.close()
        b.close()
