"""The id race, end to end (verdict 5, punch 1; dimension 10's lost grade).

Judge 5 dropped a clip on V1, warmed it in the same breath and reloaded: the shot came
back with no colour, `colour.shots` held the orphan key `tmp-1`, Effect Controls read 0.0,
and the console carried 404s for `/api/lut/<an id no shot has>`. Every Accept sent two
PUTs. One id race behind all of it: a shot laid in a moment ago lives under `tmp-N` until
the server names it, and whatever was written against `tmp-N` meanwhile was lost.

This drives that session as the judge did, in a real Chromium against a real server:

  1. drop a Project clip on V1 (an HTML5 drop), and within 100 ms of it landing
     `effects.set('temperature', 40)` — Effect Controls' own setter, on the shot it shows
  2. wait until the board is idle (no save waiting, none in flight, quiet 1.2 s), reload
  3. the dropped shot is on disk under a server id, its `colour.shots[id].balance` is the
     warm one, `colour.shots` has no `tmp-` key, and Effect Controls shows 40 for it
  4. a proposal waits; Accept sends exactly one PUT /api/project
  5. across the whole session: no response >= 400 (`/api/lut` included), no request for
     a `tmp-` id, every `/api/lut/<id>` asked for an id a shot has, no page error

No model is called: the proposal is an Ask record written to disk.

    uv run --with pytest --with fastapi --with uvicorn --with httpx --with numpy \
        --with playwright pytest app/tests/test_id_race.py -q
"""

from __future__ import annotations

import json
import re
import socket
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

# Every PUT /api/project the page sends: start, end, body, reply (installed before the
# board's own scripts, so the board's fetch is the wrapped one).
RECORD_PUTS = """
(() => {
  const real = window.fetch.bind(window);
  window.__puts = [];
  window.fetch = async (url, opts) => {
    const isPut = opts && opts.method === 'PUT' && String(url).endsWith('/api/project');
    if (!isPut) return real(url, opts);
    const rec = { t0: performance.now(), t1: null, body: JSON.parse(opts.body), reply: null };
    window.__puts.push(rec);
    try {
      const r = await real(url, opts);
      rec.reply = await r.clone().json().catch(() => null);
      return r;
    } finally {
      rec.t1 = performance.now();
    }
  };
})();
"""

SEED = [
    {"id": "keep000001", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
    {"id": "keep000002", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"},
]

DROPPED = "CLIP_C.MP4"

# A Project row dragged onto V1 at the film's end (HTML5 drag events, as Chromium sends
# them), then — the moment the shot is in the sequence — Effect Controls' setter on the
# shot it shows. Returns what happened and when, in page time.
DROP_THEN_GRADE = """async ([clip, value]) => {
  const row = [...document.querySelectorAll('#projList [data-clip]')]
    .find((r) => r.dataset.clip === clip);
  if (!row) return { error: `no Project row for ${clip}` };
  const before = new Set(segs.map((s) => String(s.id)));
  const n0 = segs.length;
  const dt = new DataTransfer();
  row.dispatchEvent(new DragEvent('dragstart', { bubbles: true, cancelable: true, dataTransfer: dt }));
  const view = tl.el.view, lane = tl.el.lanes.V1;
  const r = view.getBoundingClientRect(), lr = lane.getBoundingClientRect();
  const x = r.left + tl.timeToX(tl.total()) - view.scrollLeft + 2;   // the film's end: append
  const y = lr.top + lr.height / 2;
  const t0 = performance.now();
  for (const type of ['dragenter', 'dragover', 'drop']) {
    lane.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, clientX: x,
                                             clientY: y, dataTransfer: dt }));
  }
  row.dispatchEvent(new DragEvent('dragend', { bubbles: true, dataTransfer: dt }));
  while (segs.length === n0 && performance.now() - t0 < 3000) {
    await new Promise((res) => setTimeout(res, 2));
  }
  const landed = performance.now() - t0;
  const fresh = segs.filter((s) => !before.has(String(s.id)));
  const shown = typeof inspected === 'function' && inspected() ? String(inspected().id) : null;
  const ok = effects.set('temperature', value);
  const set = performance.now() - t0;
  return { n0, n: segs.length, landed, set, ok, shown,
           fresh: fresh.map((s) => ({ id: String(s.id), clip: s.clip, in: s.in, out: s.out })),
           puts_before_set: __puts.filter((p) => p.t0 < t0 + set).length };
}"""


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
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started, "server did not start"
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    th.join(timeout=10)
    project["edl"].write_text(original, encoding="utf-8")


def _seed(project, segments: list[dict]) -> None:
    edl = {"variant": "T", "title": "test cut", "orient": "none", "story": "",
           "target_s": [5, 20], "segments": segments}
    Path(project["edl"]).write_text(json.dumps(edl, indent=1), encoding="utf-8")


def _no_proposal() -> None:
    import server
    asks = server.STATE["asks"]
    asks.mkdir(parents=True, exist_ok=True)
    for old in asks.glob("*.json"):
        old.unlink()


def _waiting(job: str, plan: dict) -> None:
    """An unanswered Ask record on disk, newer than the cut: a proposal that waits."""
    import server
    _no_proposal()
    (server.STATE["asks"] / f"{job}.json").write_text(
        json.dumps({"job": job, "created": time.time() + 3600, "note": "", "story": "",
                    "plan": plan}), encoding="utf-8")


def _disk(project) -> dict:
    return json.loads(Path(project["edl"]).read_text(encoding="utf-8"))


def _settled(pg, quiet_ms: int = 1200, timeout: int = 20000) -> None:
    """Idle: no save waiting, none in flight, and none started for `quiet_ms`."""
    pg.wait_for_function(
        """(q) => (typeof saveRun === 'undefined' || saveRun === null) && saveTimer === null
             && (!__puts.length || (__puts[__puts.length - 1].t1 !== null
                 && performance.now() - __puts[__puts.length - 1].t1 > q))""",
        arg=quiet_ms, timeout=timeout)


def _loaded(pg) -> None:
    pg.wait_for_function("window.tl && tl.state && typeof P !== 'undefined' && P && P.clips"
                         " && window.effects && typeof effects.set === 'function'", timeout=20000)
    pg.wait_for_selector("#tl .tl-lane[data-lane=V1] .blk", timeout=20000)
    pg.wait_for_selector("#projList [data-clip]", state="attached", timeout=20000)


def _warm(balance: dict | None) -> bool:
    g = (balance or {}).get("gain") or [0, 0, 0]
    return g[0] > g[2]


def test_a_grade_set_right_after_a_drop_survives_a_reload_and_nothing_404s(live_server, project):
    _seed(project, SEED)
    _no_proposal()
    bad: list[str] = []          # every response >= 400, all session
    tmp_asked: list[str] = []    # every request naming a `tmp-` id
    luts: list[str] = []         # every /api/lut/<id> asked for
    errors: list[str] = []
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        try:
            pg = b.new_page(viewport={"width": 1440, "height": 900})
            pg.on("response", lambda r: bad.append(f"{r.status} {r.request.method} {r.url}")
                  if r.status >= 400 else None)
            pg.on("request", lambda r: tmp_asked.append(r.url) if "tmp-" in r.url else None)
            pg.on("request", lambda r: luts.append(r.url) if "/api/lut/" in r.url else None)
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.add_init_script(RECORD_PUTS)
            pg.goto(live_server)
            _loaded(pg)
            _settled(pg)

            # 1. drop, and grade within 100 ms of the shot landing
            got = pg.evaluate(DROP_THEN_GRADE, [DROPPED, 40])
            assert "error" not in got, got
            assert got["n"] == got["n0"] + 1, f"the drop did not add one shot: {got}"
            assert len(got["fresh"]) == 1 and got["fresh"][0]["clip"] == DROPPED, got
            tmp = got["fresh"][0]["id"]
            assert got["shown"] == tmp, (
                f"Effect Controls shows {got['shown']}, not the shot just dropped ({tmp})")
            assert got["ok"] is True, f"effects.set('temperature', 40) refused: {got}"
            assert got["set"] - got["landed"] < 100 and got["set"] < 1000, (
                f"the grade was not set within 100 ms of the drop: {got}")
            assert got["puts_before_set"] == 0, (
                f"a save went out before the grade was set, so this is not the race: {got}")

            # 2. idle, then reload
            _settled(pg)
            pg.reload()
            _loaded(pg)
            _settled(pg)

            # 3. the shot carries the balance under its server id
            edl = _disk(project)
            segs = edl["segments"]
            assert [s["clip"] for s in segs] == ["CLIP_A.MP4", "CLIP_B.MP4", DROPPED], segs
            sid = segs[-1]["id"]
            assert sid and not str(sid).startswith("tmp-"), f"the dropped shot is on disk as {sid!r}"
            shots = (edl.get("colour") or {}).get("shots") or {}
            stray = [k for k in shots if str(k).startswith("tmp-")]
            assert not stray, f"colour.shots keeps temporary keys {stray}: {shots}"
            bal = (shots.get(sid) or {}).get("balance")
            assert _warm(bal), f"the dropped shot {sid} lost its grade on disk: {shots}"
            assert pg.evaluate("segs.map((s) => String(s.id))") == [s["id"] for s in segs]

            idx = len(segs) - 1
            pg.locator("#tl .tl-lane[data-lane=V1] .blk").nth(idx).click()
            pg.evaluate("typeof pauseCut === 'function' && pauseCut()")
            tab = pg.locator(".ws-tab[data-tool=inspect]")
            if tab.count():
                tab.click()
            pg.wait_for_selector("#inspector .ec-v[data-k=temperature]", state="visible",
                                 timeout=10000)
            assert pg.evaluate("String(inspected().id)") == sid
            shown = pg.locator("#inspector .ec-v[data-k=temperature]").input_value()
            num = float(re.sub(r"[^\d.\-]", "", shown) or "nan")
            assert abs(num - 40) < 0.6, f"Effect Controls shows Temperature {shown!r}, not 40"
            assert abs(pg.evaluate("effects.value('temperature')") - 40) < 0.6

            # 4. a proposal waits; its Accept is one PUT
            plan = {"segments": [{"id": "keep000001", "clip": "CLIP_A.MP4", "in": 1.0,
                                  "out": 3.0, "why": "first"},
                                 {"id": sid, "clip": DROPPED, "in": segs[-1]["in"],
                                  "out": segs[-1]["out"], "why": "dropped"},
                                 {"clip": "CLIP_B.MP4", "in": 2.4, "out": 4.0, "why": "the reply"}],
                    "notes": "Opens on the hello, then the dropped shot and the reply."}
            _waiting("id-race", plan)
            pg.reload()
            _loaded(pg)
            pg.wait_for_selector("#proposal", state="visible", timeout=15000)
            _settled(pg)
            pg.evaluate("__puts.length = 0")
            pg.locator("#acceptProposal").click()
            pg.wait_for_function("__puts.length >= 1", timeout=10000)
            _settled(pg, quiet_ms=1500)
            cut = [p for p in pg.evaluate("__puts") if "segments" in p["body"]]
            assert len(cut) == 1, f"the Accept sent {len(cut)} PUTs of the cut"
            edl = _disk(project)
            ids = [s["id"] for s in edl["segments"]]
            assert len(ids) == 3 and not any(str(i).startswith("tmp-") for i in ids), ids
            assert sid in ids, f"the accepted cut lost the dropped shot {sid}: {ids}"
            assert _warm(((edl.get("colour") or {}).get("shots") or {}).get(sid, {}).get("balance")), \
                "the Accept dropped the dropped shot's grade"

            # 5. the session, whole
            pg.wait_for_timeout(500)
            on_disk = set(ids) | {s["id"] for s in segs}
            assert not bad, f"responses >= 400 this session: {bad}"
            assert not tmp_asked, f"requests for temporary ids: {tmp_asked}"
            unknown = [u for u in luts if u.split("/api/lut/")[1].split("?")[0] not in on_disk]
            assert not unknown, f"/api/lut asked for ids no shot has: {unknown}"
            assert errors == [], errors
        finally:
            b.close()
