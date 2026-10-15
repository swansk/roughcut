"""Nothing is lost on a reload (redesign wave 8, lane SAVESAFE; verdict 6 punch 1).

Verdict 6: on the first Accept after a real first pass the save had not answered 3 s
later ("unsaved…"); a reload about 5 s after the Accept left an EMPTY sequence — the EDL
never written, the ask never answered — and nothing warned. The cause, measured
(test (c) below and the CHANGELOG): the Accept's PUT never left the browser. Chrome
runs six connections per host, and right after a pass they were all held by poster
requests the server builds one ffmpeg at a time; the PUT queued behind them in the
page (11.5 s in the reproduction, served in 11 ms once sent) and the reload cancelled
it unsent. Now:

  (a) with every PUT /api/project held 3 s by the server, Accept then reload at 1 s:
      `beforeunload` asks, and after the reload is allowed the accepted cut is on disk
      (the in-flight PUT, and the keepalive flush the page sent as it went), the ask is
      answered, and the reloaded board shows the accepted cut;
  (b) an edit still waiting for its autosave when the page goes is flushed by a
      keepalive PUT on pagehide and lands with minted ids; a PUT of the same page held
      on the server while that flush lands is never written over it (the save tag);
  (c) right after a mocked first pass, with the index still writing in the background
      and six slow poster requests holding the page's connections, the Accept's PUT
      answers within 1 s (saves go uncredentialed, on their own connections).

No model is called: the proposals are Ask records on disk, or the scripted model of
test_firstpass.

    uv run --with pytest --with fastapi --with uvicorn --with httpx --with numpy \\
        --with playwright pytest app/tests/test_save_reload.py -q
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import inference  # noqa: E402
from test_firstpass import Scripted, _end_jobs  # noqa: E402
from test_index import _stub_tools  # noqa: E402
from test_save_once import RECORD_PUTS, SEED, PLAN, _seed, _waiting, _disk  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(server):
    import uvicorn
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
    return srv, th, f"http://127.0.0.1:{port}"


@pytest.fixture
def live_server(project):
    import server
    original = project["edl"].read_text(encoding="utf-8")
    server.configure(project["edl"], project["footage"], project["sidecars"],
                     project["work"], proxies=False, assets=project["assets"])
    srv, th, base = _serve(server)
    yield base
    srv.should_exit = True
    th.join(timeout=10)
    project["edl"].write_text(original, encoding="utf-8")


@pytest.fixture
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        yield b
        b.close()


def _open(browser, base, dialogs: list):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    # every beforeunload is noted and allowed: the reload / navigation goes ahead
    pg.on("dialog", lambda d: (dialogs.append(d.type), d.accept()))
    pg.add_init_script(RECORD_PUTS)
    pg.goto(base)
    pg.wait_for_function("window.tl && tl.state && typeof P !== 'undefined' && P && P.clips",
                         timeout=20000)
    return ctx, pg


def _until(fn, timeout=15.0, every=0.1):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(every)
    return last


def _answered(job: str) -> dict | None:
    import server
    try:
        rec = json.loads((server.STATE["asks"] / f"{job}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec.get("answered")


def _settled(pg, quiet_ms=800, timeout=15000):
    pg.wait_for_function(
        """(q) => saveRun === null && saveTimer === null
             && (!__puts.length || (__puts[__puts.length - 1].t1 !== null
                 && performance.now() - __puts[__puts.length - 1].t1 > q))""",
        arg=quiet_ms, timeout=timeout)


# ------------------------------------------------------------------------- (a)

def test_a_reload_while_the_accepts_save_is_held_keeps_the_accepted_cut(
        browser, live_server, project, monkeypatch):
    import server
    _seed(project, SEED)
    _waiting("reload-a", PLAN)
    dialogs: list[str] = []
    ctx, pg = _open(browser, live_server, dialogs)
    try:
        pg.wait_for_selector("#proposal", state="visible", timeout=10000)
        _settled(pg)
        monkeypatch.setattr(server, "PUT_DELAY_S", 3.0)      # a stalled server
        pg.locator("#acceptProposal").click()
        pg.wait_for_timeout(1000)
        assert _disk(project)["segments"] == SEED, "the held save landed early"
        assert pg.evaluate("savePending()") is True
        pg.reload()
        assert "beforeunload" in dialogs, "the reload went without asking"

        # the accepted cut is on disk: three shots, the server's ids, the first kept
        def landed():
            segs = _disk(project).get("segments") or []
            return segs if len(segs) == 3 else None
        segs = _until(landed, timeout=12)
        assert segs, f"the accepted cut never reached the disk: {_disk(project)['segments']}"
        ids = [s["id"] for s in segs]
        assert ids[0] == "keep000001" and len(set(ids)) == 3, ids
        assert not any(str(i).startswith("tmp-") for i in ids), ids
        assert [(s["clip"], s["in"], s["out"]) for s in segs] == \
            [(s["clip"], s["in"], s["out"]) for s in PLAN["segments"]]
        ans = _until(lambda: _answered("reload-a"), timeout=8)
        assert ans and ans.get("answer") == "accept", ans

        # the reloaded board was served the accepted cut, not the one before it
        pg.wait_for_function("window.tl && tl.state && tl.state.segs.length === 3",
                             timeout=15000)
        assert pg.evaluate("tl.state.segs.map((s) => s.id)") == ids
        monkeypatch.setattr(server, "PUT_DELAY_S", 0.0)
        pg.wait_for_timeout(300)
        assert not pg.locator("#proposal").is_visible(), "the answered proposal came back"
        assert pg.errors == []
    finally:
        monkeypatch.setattr(server, "PUT_DELAY_S", 0.0)
        ctx.close()


# ------------------------------------------------------------------------- (b)

def test_b_an_edit_waiting_for_its_autosave_is_flushed_on_pagehide(
        browser, live_server, project, monkeypatch):
    import server
    _seed(project, SEED)
    dialogs: list[str] = []
    ctx, pg = _open(browser, live_server, dialogs)
    flushes: list[dict] = []                       # what the server received

    def note(request):
        if request.headers.get("x-roughcut-flush"):
            flushes.append(dict(request.headers))
        return 0.0

    monkeypatch.setattr(server, "put_delay", note)
    try:
        pg.wait_for_selector("#tl .tl-lane[data-lane=V1] .blk", timeout=20000)
        _settled(pg)
        pg.evaluate("tl.insertAt({ clip: 'CLIP_C.MP4', in: 0.5, out: 1.5, why: 'late' }, tl.total())")
        assert pg.evaluate("saveTimer !== null && segs[2].id.startsWith('tmp-')")
        pg.goto("about:blank")                    # inside the 700 ms autosave wait
        segs = _until(lambda: (s := _disk(project).get("segments") or []) and len(s) == 3 and s,
                      timeout=8)
        assert segs, "the waiting edit was lost when the page went"
        ids = [s["id"] for s in segs]
        assert ids[:2] == ["keep000001", "keep000002"] and ids[2].startswith("g"), ids
        assert segs[2]["clip"] == "CLIP_C.MP4" and segs[2]["why"] == "late"
        # beforeunload started the waiting save too; the keepalive flush went as well
        assert _until(lambda: flushes, timeout=5), "no keepalive flush reached the server"
        assert flushes[0]["x-roughcut-flush"] in ("pagehide", "hidden"), flushes
        assert "x-roughcut-save" in flushes[0]
    finally:
        ctx.close()


def test_b_a_held_older_put_never_writes_over_the_flush(
        browser, live_server, project, monkeypatch):
    """The coalescing: a save sent before the page went is still on the server when the
    page's keepalive flush lands. The older one is answered `stale` and writes nothing;
    the shot both carried under `tmp-N` gets ONE id."""
    import server
    _seed(project, SEED)
    held = {"n": 0}

    def hold(request):
        if request.headers.get("x-roughcut-flush"):
            return 0.0
        held["n"] += 1
        return 2.5

    dialogs: list[str] = []
    ctx, pg = _open(browser, live_server, dialogs)
    try:
        pg.wait_for_selector("#tl .tl-lane[data-lane=V1] .blk", timeout=20000)
        _settled(pg)
        monkeypatch.setattr(server, "put_delay", hold)
        # X goes in and its save is sent at once (held 2.5 s on the server); Y goes in
        # while it is held, so only the flush carries Y
        pg.evaluate("""() => {
          tl.insertAt({ clip: 'CLIP_C.MP4', in: 0.5, out: 1.5, why: 'X' }, tl.total());
          save();
        }""")
        pg.wait_for_function("__puts.length === 1")
        pg.wait_for_timeout(200)
        pg.evaluate("tl.insertAt({ clip: 'CLIP_A.MP4', in: 4.0, out: 5.0, why: 'Y' }, tl.total())")
        held_body = pg.evaluate("__puts[0].body")
        x_tmp = held_body["segments"][2]["id"]
        assert x_tmp.startswith("tmp-")
        pg.goto("about:blank")
        flushed = _until(lambda: (s := _disk(project).get("segments") or []) and len(s) == 4 and s,
                         timeout=6)
        assert flushed, f"the flush did not land: {_disk(project)['segments']}"
        time.sleep(3.0)                            # the held PUT is answered meanwhile
        assert held["n"] >= 1
        segs = _disk(project)["segments"]
        assert [s["why"] for s in segs] == ["first", "second", "X", "Y"], \
            "the older held PUT was written over the flush"
        ids = [s["id"] for s in segs]
        assert len(set(ids)) == 4 and not any(i.startswith("tmp-") for i in ids), ids
    finally:
        ctx.close()


def test_b_the_server_orders_a_pages_saves_by_their_tag(client, project):
    """API level: an older tag is answered `stale` and writes nothing; its `tmp-N` gets
    the id the newer one minted; an untagged PUT is applied as before."""
    base = [{"id": "keep000001", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "a"}]
    new = {"id": "tmp-7", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "b"}
    r2 = client.put("/api/project", json={"segments": base + [new, {**new, "id": "tmp-8"}]},
                    headers={"x-roughcut-save": "pgtest:2"})
    assert r2.status_code == 200 and not r2.json().get("stale")
    minted = r2.json()["ids"]
    r1 = client.put("/api/project", json={"segments": base + [new]},
                    headers={"x-roughcut-save": "pgtest:1"})
    assert r1.status_code == 200 and r1.json()["stale"] is True
    assert r1.json()["ids"] == {"tmp-7": minted["tmp-7"]}
    disk = client.get("/api/project").json()["segments"]
    assert [s["id"] for s in disk] == ["keep000001", minted["tmp-7"], minted["tmp-8"]]
    r3 = client.put("/api/project", json={"segments": base + [new]},
                    headers={"x-roughcut-save": "pgtest:3"})
    assert r3.json()["ids"] == {"tmp-7": minted["tmp-7"]}, "the page's tmp-7 is one shot"
    r4 = client.put("/api/project", json={"segments": base})
    assert r4.status_code == 200 and not r4.json().get("stale")
    assert [s["id"] for s in client.get("/api/project").json()["segments"]] == ["keep000001"]


# ------------------------------------------------------------------------- (c)

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
def pass_server(tmp_path, project, monkeypatch, model):
    """A fresh bin served for real, the index's tools stubbed; the close looks and
    proxies, which the index finishes after the pass, take 4 s each, so the index is
    still writing its journal when the pass's proposal lands."""
    import server
    monkeypatch.setenv("ROUGHCUT_BACKEND", "claude_cli")
    server.configure(None, project["footage"], project["sidecars"], tmp_path,
                     proxies=False, visual=None)
    _stub_tools(server, monkeypatch, tmp_path)
    monkeypatch.setattr(server, "FIRSTPASS_POLL_S", 0.05)
    real_stage = server._index_stage

    def slow_stage(j, clip, stage):
        if stage in ("close", "proxy"):
            time.sleep(4.0)
        return real_stage(j, clip, stage)

    monkeypatch.setattr(server, "_index_stage", slow_stage)
    server.FIRSTPASS.clear()
    srv, th, base = _serve(server)
    yield base
    _end_jobs(server, model, timeout=60)
    srv.should_exit = True
    th.join(timeout=10)
    server.FIRSTPASS.clear()


def test_c_the_accepts_put_answers_within_a_second_right_after_a_pass(
        browser, pass_server, monkeypatch):
    import server
    base = pass_server
    real_poster = server.build_poster

    def slow_poster(src, dest, t, original=False):
        time.sleep(3.0)                            # a 4K frame grab on a loaded box
        return real_poster(src, dest, t, original=original)

    monkeypatch.setattr(server, "build_poster", slow_poster)
    dialogs: list[str] = []
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("dialog", lambda d: (dialogs.append(d.type), d.accept()))
    pg.add_init_script(RECORD_PUTS)
    sent: list = []
    pg.on("requestfinished", lambda r: sent.append(r) if r.method == "PUT" else None)
    try:
        pg.goto(base + "/")
        pg.wait_for_function("window.rcFirstPass && rcFirstPass.state()", timeout=20000)
        r = httpx.post(base + "/api/firstpass", json={"mode": "run"}, timeout=30)
        assert r.status_code == 200, r.text
        _until(lambda: httpx.get(base + "/api/firstpass", timeout=20).json()["state"]
               in ("done", "failed"), timeout=90, every=0.2)
        st = httpx.get(base + "/api/firstpass", timeout=20).json()
        assert st["state"] == "done", st.get("error")
        assert any(x["state"] not in server.progress.TERMINAL for x in server.INDEXES.values()), \
            "the index finished before the pass: nothing was writing in the background"
        pg.reload()
        pg.wait_for_selector("#proposal", state="visible", timeout=30000)
        pg.wait_for_function("saveRun === null && saveTimer === null", timeout=10000)
        # the post-pass poster storm: six frame grabs nobody has built, each 3 s on the
        # server (three at a time), holding every connection the page's images get
        pg.evaluate("""(n) => { window.__imgs = [];
          for (let k = 0; k < n; k++) { const im = new Image();
            im.src = `/media/poster/CLIP_${'ABC'[k % 3]}.v2.jpg?t=${(1.1 + k * 0.37).toFixed(2)}`;
            __imgs.push(im); } }""", 8)
        pg.wait_for_timeout(300)
        pg.evaluate("__puts.length = 0")
        t0 = pg.evaluate("performance.now()")
        clicked = time.time()
        pg.locator("#acceptProposal").click()
        pg.wait_for_function("__puts.length >= 1 && __puts[0].t1 !== null", timeout=20000)
        put = pg.evaluate("__puts[0]")
        took = put["t1"] - t0
        assert took <= 1000, f"the Accept's PUT answered {took:.0f} ms after the click"
        timing = sent[0].timing if sent else {}
        assert timing.get("requestStart", 0) < 500, f"the PUT waited in the browser: {timing}"
        assert "segments" in put["body"] and put["reply"]["ok"]
        slow = httpx.get(base + "/api/debug/slow", timeout=10).json()
        assert not [x for x in slow["slow"] if x["path"] == "/api/project"
                    and x["method"] == "PUT" and x["at"] >= clicked], slow["slow"]
        assert errors == []
    finally:
        ctx.close()
