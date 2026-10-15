"""One save in flight, ids stable (wave 5, lane SAVE; LOOP.md "the double save").

Judge 4's trim read "added by hand": the board saved a second time before `tl.afterSave`
had re-keyed its `tmp-N` ids, so the server minted a second set of ids for the same new
shots. And an Accept saved at once while its timeline commit queued the 700 ms autosave
of the same cut; a PUT landing between the two was overwritten (REVIEW, test_firstpass_ui).

Now the board has at most one PUT /api/project in flight; a save asked for meanwhile is
folded into one follow-up, built after the reply re-keyed (the reply's `ids` names each
`tmp-N` the server minted for), and the Accept's save and its commit's autosave are one.

Every PUT the page sends is recorded by a fetch wrapper installed before the board's
scripts (`__puts`: start, end, body, reply); `__holdPut` holds a reply in the page for a
while after the server wrote it, the window the old board raced in. No model is called:
the waiting proposal is an Ask record written to disk.

    uv run --with pytest --with fastapi --with uvicorn --with httpx --with numpy \
        --with playwright pytest app/tests/test_save_once.py -q
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

RECORD_PUTS = """
(() => {
  const real = window.fetch.bind(window);
  window.__puts = []; window.__holdPut = 0; window.__maxLive = 0;
  let live = 0;
  window.fetch = async (url, opts) => {
    const isPut = opts && opts.method === 'PUT' && String(url).endsWith('/api/project');
    if (!isPut) return real(url, opts);
    const rec = { t0: performance.now(), t1: null, body: JSON.parse(opts.body), reply: null };
    window.__puts.push(rec);
    live += 1;
    window.__maxLive = Math.max(window.__maxLive, live);
    try {
      const r = await real(url, opts);
      rec.reply = await r.clone().json().catch(() => null);
      const hold = window.__holdPut;
      if (hold) await new Promise((res) => setTimeout(res, hold));
      return r;
    } finally {
      live -= 1;
      rec.t1 = performance.now();
    }
  };
})();
"""

SEED = [
    {"id": "keep000001", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
    {"id": "keep000002", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"},
]

PLAN = {
    "segments": [
        {"id": "keep000001", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
        {"clip": "CLIP_B.MP4", "in": 0.0, "out": 1.5, "why": "the reply"},
        {"clip": "CLIP_C.MP4", "in": 1.0, "out": 3.0, "why": "the ride starts"},
    ],
    "notes": "Opens on the hello, then the reply and the ride.",
}


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


@pytest.fixture
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        yield b
        b.close()


def _seed(project, segments: list[dict], colour: dict | None = None) -> None:
    edl = {"variant": "T", "title": "test cut", "orient": "none", "story": "",
           "target_s": [5, 20], "segments": segments}
    if colour:
        edl["colour"] = colour
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
    path = server.STATE["asks"] / f"{job}.json"
    path.write_text(json.dumps({"job": job, "created": time.time() + 3600, "note": "",
                                "story": "", "plan": plan}), encoding="utf-8")


def _open(browser, live_server):
    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.errors = errors
    pg.add_init_script(RECORD_PUTS)
    pg.goto(live_server)
    pg.wait_for_function("window.tl && tl.state && typeof P !== 'undefined' && P && P.clips",
                         timeout=20000)
    pg.wait_for_selector("#tl .tl-lane[data-lane=V1] .blk", timeout=20000)   # mounted
    return pg


def _disk(project) -> dict:
    return json.loads(Path(project["edl"]).read_text(encoding="utf-8"))


def _settled(pg, quiet_ms: int = 1200, timeout: int = 15000) -> None:
    """No save waiting, none in flight, and none started for `quiet_ms`."""
    pg.wait_for_function(
        """(q) => (typeof saveRun === 'undefined' || saveRun === null) && saveTimer === null
             && (!__puts.length || (__puts[__puts.length - 1].t1 !== null
                 && performance.now() - __puts[__puts.length - 1].t1 > q))""",
        arg=quiet_ms, timeout=timeout)


def _puts(pg) -> list[dict]:
    return pg.evaluate("__puts")


def _assert_one_at_a_time(puts: list[dict]) -> None:
    for a, b in zip(puts, puts[1:]):
        assert a["t1"] is not None and b["t0"] >= a["t1"], (
            f"two PUT /api/project in flight: one {a['t0']:.0f}-{a['t1']} ms, "
            f"the next started {b['t0']:.0f} ms")


def _ids(body: dict) -> list[str | None]:
    return [s.get("id") for s in body.get("segments") or []]


# ------------------------------------------------------------------------- (a)

def test_inserts_and_an_accept_make_one_save_at_a_time_and_ids_are_minted_once(
        browser, live_server, project):
    _seed(project, SEED)
    _waiting("save-once", PLAN)
    pg = _open(browser, live_server)
    try:
        pg.wait_for_selector("#proposal", state="visible", timeout=10000)
        _settled(pg)
        pg.evaluate("__puts.length = 0; __maxLive = 0")

        # The Accept's save is held in the page 1.5 s after the server wrote it: the two
        # inserts (each a commit that touches the 700 ms autosave) ask to save meanwhile.
        pg.evaluate("__holdPut = 1500")
        pg.locator("#acceptProposal").click()
        pg.wait_for_function("__puts.length === 1")
        pg.evaluate("""() => {
          tl.insertAt({ clip: 'CLIP_C.MP4', in: 0.5, out: 1.5, why: 'one' }, tl.total());
          tl.insertAt({ clip: 'CLIP_A.MP4', in: 4.0, out: 5.0, why: 'two' }, tl.total());
        }""")
        pg.wait_for_timeout(1000)               # past the 700 ms autosave of the inserts
        assert pg.evaluate("__maxLive") == 1
        pg.evaluate("__holdPut = 0")
        _settled(pg)

        puts = _puts(pg)
        _assert_one_at_a_time(puts)
        cut = [p for p in puts if "segments" in p["body"]]
        # the Accept's save, and the two inserts folded into ONE follow-up
        assert len(cut) == 2, [_ids(p["body"]) for p in cut]
        first, follow = cut
        sent = _ids(first["body"])
        assert sent[0] == "keep000001" and all(i and i.startswith("tmp-") for i in sent[1:]), sent
        minted = first["reply"]["ids"]
        assert sorted(minted) == sorted(sent[1:]), minted
        named = ["keep000001"] + [minted[i] for i in sent[1:]]
        # the follow-up was built after the reply re-keyed: the accepted shots go under
        # exactly the ids the server minted; only the two new inserts are `tmp-`
        again = _ids(follow["body"])
        assert again[:3] == named, (again, named)
        assert all(i.startswith("tmp-") for i in again[3:]) and len(again) == 5, again
        assert not set(again[3:]) & set(sent), "a tmp id was sent twice"
        named += [follow["reply"]["ids"][i] for i in again[3:]]

        disk = _disk(project)["segments"]
        assert [s["id"] for s in disk] == named
        assert len(set(named)) == 5 and not any(i.startswith("tmp-") for i in named)
        assert pg.evaluate("segs.map((s) => s.id)") == named
        assert pg.evaluate("tl.needsRekey()") is False

        # a later save sends exactly the ids on disk, no `tmp-`
        pg.evaluate("__puts.length = 0")
        pg.evaluate("tl.setRange(segs[1].id, null, segs[1].out - 0.25)")
        _settled(pg)
        later = [p for p in _puts(pg) if "segments" in p["body"]]
        assert len(later) == 1 and _ids(later[0]["body"]) == named
        assert later[0]["reply"]["ids"] == {}

        # undo takes the last insert back; redo saves it under the id the server gave
        # it, never a second one (its undo snapshot was taken under `tmp-N`)
        pg.evaluate("tl.undo(); tl.undo()")
        _settled(pg)
        assert [s["id"] for s in _disk(project)["segments"]] == named[:4]
        pg.evaluate("tl.redo()")
        _settled(pg)
        assert [s["id"] for s in _disk(project)["segments"]] == named[:5]
        _assert_one_at_a_time(_puts(pg))
        assert pg.errors == []
    finally:
        pg.close()


def test_the_accept_saves_once(browser, live_server, project):
    """The Accept's explicit save and its commit's autosave are one PUT."""
    _seed(project, SEED)
    _waiting("save-once-b", PLAN)
    pg = _open(browser, live_server)
    try:
        pg.wait_for_selector("#proposal", state="visible", timeout=10000)
        _settled(pg)
        pg.evaluate("__puts.length = 0")
        pg.locator("#acceptProposal").click()
        pg.wait_for_function("__puts.length >= 1")
        _settled(pg, quiet_ms=1500)
        cut = [p for p in _puts(pg) if "segments" in p["body"]]
        assert len(cut) == 1, f"the Accept saved {len(cut)} times"
        assert len(_disk(project)["segments"]) == 3
    finally:
        pg.close()


# ------------------------------------------------------------------------- (b)

def test_a_hand_edit_made_between_the_accept_and_its_autosave_survives(
        browser, live_server, project):
    import httpx

    _seed(project, SEED)
    _waiting("save-once-c", PLAN)
    pg = _open(browser, live_server)
    try:
        pg.wait_for_selector("#proposal", state="visible", timeout=10000)
        _settled(pg)

        # REVIEW's race: the Accept's save lands, a hand trim lands (a PUT from outside
        # the page, as test_firstpass_ui's), and the board must not save the accepted cut
        # over it 700 ms later
        pg.evaluate("__puts.length = 0")
        pg.locator("#acceptProposal").click()
        pg.wait_for_function("__puts.length === 1 && __puts[0].t1 !== null")
        cut = httpx.get(f"{live_server}/api/project", timeout=10).json()["segments"]
        assert len(cut) == 3
        trimmed = [dict(s) for s in cut]
        trimmed[1]["out"] = round(trimmed[1]["out"] - 0.5, 4)
        r = httpx.put(f"{live_server}/api/project", json={"segments": trimmed}, timeout=10)
        assert r.status_code == 200
        _settled(pg, quiet_ms=1500)
        disk = _disk(project)["segments"]
        assert disk[1]["out"] == trimmed[1]["out"], "the board saved over the hand trim"
        assert len([p for p in _puts(pg) if "segments" in p["body"]]) == 1

        # and a trim made on the board while the Accept's save is in flight reaches disk,
        # in the one follow-up
        _seed(project, SEED)
        _waiting("save-once-d", PLAN)
        pg.reload()
        pg.wait_for_selector("#proposal", state="visible", timeout=10000)
        _settled(pg)
        pg.evaluate("__puts.length = 0; __holdPut = 1200")
        pg.locator("#acceptProposal").click()
        pg.wait_for_function("__puts.length === 1")
        pg.evaluate("tl.setRange(segs[2].id, segs[2].in + 0.5, null)")
        want = pg.evaluate("segs[2].in")
        pg.evaluate("__holdPut = 0")
        _settled(pg)
        disk = _disk(project)["segments"]
        assert abs(disk[2]["in"] - want) < 1e-6, (disk[2], want)
        assert pg.evaluate("segs.map((s) => s.id)") == [s["id"] for s in disk]
        _assert_one_at_a_time(_puts(pg))
        assert pg.errors == []
    finally:
        pg.close()


# ------------------------------------------------------------------------- (c)

def test_a_per_shot_colour_set_right_after_an_insert_follows_the_rekey(
        browser, live_server, project):
    _seed(project, SEED)
    _no_proposal()
    pg = _open(browser, live_server)
    try:
        pg.wait_for_function("window.effects && typeof effects.set === 'function'", timeout=10000)
        _settled(pg)

        # in the same task as the insert: the first save carries `colour.shots[tmp-N]`
        tmp = pg.evaluate("""() => {
          const id = tl.insertAt({ clip: 'CLIP_C.MP4', in: 0.5, out: 2.0, why: 'new' }, tl.total());
          effects.set('temperature', 20, tl.byId(id));
          return id;
        }""")
        assert tmp.startswith("tmp-")
        assert pg.evaluate("(id) => !!(colour.shots && colour.shots[id])", tmp)
        _settled(pg)
        edl = _disk(project)
        new_id = edl["segments"][2]["id"]
        assert not new_id.startswith("tmp-")
        shots = (edl.get("colour") or {}).get("shots") or {}
        assert list(shots) == [new_id], shots
        assert shots[new_id].get("balance"), shots
        assert pg.evaluate("Object.keys(colour.shots)") == [new_id]

        # while the insert's save is in flight: the grade is set on the `tmp-N` shot, and
        # the follow-up writes it under the minted id
        pg.evaluate("__holdPut = 1200")
        tmp2 = pg.evaluate("""async () => {
          const id = tl.insertAt({ clip: 'CLIP_B.MP4', in: 2.0, out: 3.0, why: 'newer' }, tl.total());
          save();
          await new Promise((r) => setTimeout(r, 150));
          effects.set('temperature', -20, tl.byId(id));
          return id;
        }""")
        assert tmp2.startswith("tmp-")
        pg.evaluate("__holdPut = 0")
        _settled(pg)
        edl = _disk(project)
        ids = [s["id"] for s in edl["segments"]]
        assert len(ids) == 4 and len(set(ids)) == 4 and not any(i.startswith("tmp-") for i in ids)
        shots = edl["colour"]["shots"]
        assert sorted(shots) == sorted(ids[2:]), (shots, ids)
        assert all(not k.startswith("tmp-") for k in shots)
        assert shots[ids[2]]["balance"] != shots[ids[3]]["balance"]
        assert pg.evaluate("Object.keys(colour.shots).sort()") == sorted(ids[2:])

        # the switcher's flush waits for the save in flight and the one it asked for
        pg.evaluate("__holdPut = 600")
        pg.evaluate("tl.setRange(segs[0].id, null, segs[0].out - 0.25)")
        pg.evaluate("roughcutFlush()")        # the timer: saves now
        pg.evaluate("tl.setRange(segs[1].id, null, segs[1].out - 0.25)")
        outs = pg.evaluate("roughcutFlush().then(() => segs.map((s) => s.out))")
        assert [s["out"] for s in _disk(project)["segments"]] == outs
        pg.evaluate("__holdPut = 0")
        _settled(pg)
        _assert_one_at_a_time(_puts(pg))
        assert pg.errors == []
    finally:
        pg.close()


def test_the_server_names_the_tmp_ids_it_mints(client, project):
    """PUT /api/project mints one id per `tmp-N`, renames colour keys to it, drops a
    `tmp-` colour key no shot carries, and says which in `ids`."""
    _seed(project, SEED)
    body = {"segments": [dict(SEED[0]),
                         {"id": "tmp-7", "clip": "CLIP_C.MP4", "in": 0.5, "out": 1.5}],
            "colour": {"mode": "auto", "reference": "tmp-7",
                       "shots": {"tmp-7": {"auto": False}, "tmp-99": {"auto": False},
                                 "keep000001": {"auto": False}}}}
    r = client.put("/api/project", json=body)
    assert r.status_code == 200, r.text
    ids = r.json()["ids"]
    assert list(ids) == ["tmp-7"] and not ids["tmp-7"].startswith("tmp-")
    edl = _disk(project)
    assert [s["id"] for s in edl["segments"]] == ["keep000001", ids["tmp-7"]]
    assert sorted(edl["colour"]["shots"]) == sorted(["keep000001", ids["tmp-7"]])
    assert edl["colour"]["reference"] == ids["tmp-7"]
    # a body with no new shots names none
    r = client.put("/api/project", json={"segments": edl["segments"]})
    assert r.json()["ids"] == {}
    assert [s["id"] for s in _disk(project)["segments"]] == ["keep000001", ids["tmp-7"]]
