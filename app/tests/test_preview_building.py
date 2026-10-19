"""Verdict 8, blocker 2 — the Program is never black without a word while a preview builds.

Right after the first pass, a shot whose clip's 360p preview was still being made left the
Program plain black for 11.5 s and more, with no message, and Play gave 0 frames:
`GET /media/proxy/<clip>.mp4` held the request 20 s before its 503, and only that 503
started the board's wait. Now:

  * `/media/proxy` answers within a second — a 503 with `retry-after` (and the estimate in
    `x-roughcut-eta`) while the preview is being made;
  * a seek onto such a shot shows the shot's poster and "preparing preview… CLIP_B · ~N s"
    within 300 ms;
  * Play plays past it on the film clock (the playhead runs, the poster and the line stay)
    and the next shot plays, as Premiere plays past offline media; when the preview lands
    the shot plays;
  * the Project bin's Status reads "building" for that clip (its hover: "preview building…");
  * the media walk builds the cut's previews first, in film order, before any range proxy
    (the queue-order number below).

The fixture: the session's synthetic bin (CLIP_A/B/C, 6 s each), a cut A · B · C with
proxies for A and C; CLIP_B's media is patched to "building" (not playable as it is, and its
preview build never ends until the test lets it).

    uv run --with pytest --with fastapi --with uvicorn --with httpx --with numpy \
        --with playwright pytest app/tests/test_preview_building.py -q
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

SEEK_LIMIT_MS = 300          # the poster and the wait line this soon after a seek
PROXY_ANSWER_LIMIT_S = 1.0   # /media/proxy answers within this

CUT = {
    "variant": "T", "title": "building", "orient": "none", "story": "", "target_s": [5, 20],
    "segments": [
        {"id": "s1", "clip": "CLIP_A.MP4", "in": 1.0, "out": 2.5, "why": "first"},
        {"id": "s2", "clip": "CLIP_B.MP4", "in": 1.0, "out": 3.0, "why": "building"},
        {"id": "s3", "clip": "CLIP_C.MP4", "in": 0.5, "out": 3.5, "why": "after it"},
    ],
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def building(project, tmp_path, monkeypatch):
    """The live server on the synthetic bin, CLIP_B's preview "being made" until
    `release()` makes its original playable (the preview landing, for the board)."""
    import uvicorn
    import server

    work = tmp_path / "work"
    work.mkdir()
    edl = tmp_path / "cut.json"
    edl.write_text(json.dumps(CUT, indent=1), encoding="utf-8")
    server.MEDIA["failed"].clear()
    server.MEDIA["wanted"].clear()
    server.configure(edl, project["footage"], project["sidecars"], work,
                     proxies=False, assets=project["assets"])
    server.ensure_proxies(["CLIP_A.MP4", "CLIP_C.MP4"])
    landed = threading.Event()
    real_playable = server.browser_playable
    key = str(server.preview_path("CLIP_B"))
    asked: list[float] = []

    def playable(src: Path) -> bool:
        if src.stem == "CLIP_B" and not landed.is_set():
            return False
        return real_playable(src)

    def start_preview(clip: str):
        if Path(clip).stem != "CLIP_B":
            return None
        asked.append(time.time())
        ev, _owner = server._claim_preview(key)        # it is being built: MEDIA["building"]
        return ev

    monkeypatch.setattr(server, "browser_playable", playable)
    monkeypatch.setattr(server, "start_preview", start_preview)

    def release() -> None:
        landed.set()
        with server.MEDIA["lock"]:
            ev = server.MEDIA["building"].pop(key, None)
        if ev is not None:
            ev.set()

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
    try:
        yield {"url": f"http://127.0.0.1:{port}", "server": server, "release": release,
               "asked": asked}
    finally:
        release()
        srv.should_exit = True
        thread.join(timeout=10)
        server.MEDIA.setdefault("holds", {}).clear()
        server.MEDIA["wanted"].clear()


def test_media_proxy_answers_within_a_second_while_the_preview_builds(building):
    url = building["url"]
    t0 = time.monotonic()
    try:
        urllib.request.urlopen(f"{url}/media/proxy/CLIP_B.mp4", timeout=30)
        raise AssertionError("served something while the preview is still being made")
    except urllib.error.HTTPError as exc:
        took = time.monotonic() - t0
        assert exc.code == 503, exc.code
        retry = int(exc.headers["retry-after"])
        eta = exc.headers.get("x-roughcut-eta")
    print(f"/media/proxy building: 503 in {took * 1000:.0f} ms, retry-after {retry}, eta {eta}")
    assert took <= PROXY_ANSWER_LIMIT_S, f"/media/proxy held the request {took:.2f}s"
    assert 1 <= retry <= 10
    assert eta and float(eta) > 0
    with urllib.request.urlopen(f"{url}/media/status/CLIP_B.mp4", timeout=10) as r:
        st = json.loads(r.read())
    assert st["serves"] == "building" and not st["ready"] and st["eta_s"] > 0, st
    with urllib.request.urlopen(f"{url}/api/clips", timeout=30) as r:
        rows = {c["clip"]: c for c in json.loads(r.read())["clips"]}
    assert rows["CLIP_B.MP4"]["media"] == "building", rows["CLIP_B.MP4"]
    assert rows["CLIP_A.MP4"]["media"] == "proxy"
    # the build the monitor now waits on is held: the yield thread never pauses it
    assert str(building["server"].preview_path("CLIP_B")) in building["server"]._waited_on()
    # a playable answer still comes at once
    t0 = time.monotonic()
    with urllib.request.urlopen(f"{url}/media/proxy/CLIP_A.mp4", timeout=10) as r:
        assert r.status == 200
    assert time.monotonic() - t0 < PROXY_ANSWER_LIMIT_S


# In the page: how long from the seek (or the cut) until the poster and the wait line show.
WATCH = """([target, t0]) => new Promise((ok) => {
  const look = () => {
    const img = document.querySelector('#monPoster');
    const w = document.querySelector('#monWait');
    const v = liveVideo();
    const black = !!(v && v.classList.contains('live') && !v._shown);
    const line = w && !w.hidden ? w.textContent.trim() : '';
    if (img && !img.hidden && (img.getAttribute('src') || '').includes(target)
        && line.includes('preparing preview') && line.includes(target)) {
      ok({ ms: performance.now() - t0, line, src: img.getAttribute('src'), black });
      return;
    }
    if (performance.now() - t0 > 5000) {
      ok({ ms: null, line, src: img && img.getAttribute('src'), hidden: img && img.hidden });
      return;
    }
    requestAnimationFrame(look);
  };
  look();
})"""


def _board(pw, url):
    browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_selector("#tl .blk")
    page.wait_for_function("segs.length === 3 && player.vids.length >= 2", timeout=20000)
    return browser, page, errors


def test_a_seek_onto_a_building_shot_shows_its_poster_and_the_wait_line(building):
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
    with sync_api.sync_playwright() as pw:
        browser, page, errors = _board(pw, building["url"])
        try:
            # parked on shot 1 (a proxy) first, so the seek is a seek, not the first load
            page.evaluate("cueAt(0, segs[0].in + 0.2)")
            page.wait_for_function("liveVideo().readyState >= 2 && !liveVideo().seeking", timeout=10000)
            res = []
            for k in range(3):
                got = page.evaluate("""async ([k, watch]) => {
                    const t0 = performance.now();
                    cueAt(1, segs[1].in + 0.3 + k * 0.4);
                    return await (eval(watch))(['CLIP_B', t0]);
                }""", [k, WATCH])
                res.append(got)
                # away again, onto a shot that plays, before the next seek
                page.evaluate("cueAt(2, segs[2].in + 0.2)")
                page.wait_for_function(
                    "liveVideo().readyState >= 2 && liveVideo().classList.contains('live')", timeout=10000)
            print("seek onto the building shot → poster + line:",
                  [None if r["ms"] is None else round(r["ms"]) for r in res], res[0]["line"])
            for r in res:
                assert r["ms"] is not None, f"no poster + wait line within 5 s: {r}"
                assert r["ms"] <= SEEK_LIMIT_MS, f"poster + line took {r['ms']:.0f} ms: {r}"
            # the line says the clip and, once the server answered, how long
            page.evaluate("cueAt(1, segs[1].in + 0.5)")
            page.wait_for_function(
                "/preparing preview… CLIP_B · ~\\d+ s/.test(document.querySelector('#monWait').textContent)",
                timeout=5000)
            # the Project bin's Status for that clip
            page.wait_for_function(
                "(() => { const r = document.querySelector('#projList [data-clip=\"CLIP_B.MP4\"]');"
                " return r && !!r.querySelector('td.st[data-state=building][title^=\"preview building\"]'); })()", timeout=10000)
            row_a = page.locator('#projList [data-clip="CLIP_A.MP4"]').inner_text()
            assert "building" not in row_a, row_a
            assert not [e for e in errors if "Error" in e], errors
        finally:
            browser.close()


def test_play_plays_past_a_building_shot_and_reaches_the_next(building):
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
    with sync_api.sync_playwright() as pw:
        browser, page, errors = _board(pw, building["url"])
        try:
            page.evaluate("cueAt(0, segs[0].in)")
            page.wait_for_function("liveVideo().readyState >= 2", timeout=10000)
            # sample the board every frame from Play until shot 3 has played a while
            out = page.evaluate("""() => new Promise((ok) => {
                const s = [];
                const t0 = performance.now();
                playFrom(0);
                const look = () => {
                    const v = liveVideo();
                    const w = document.querySelector('#monWait');
                    const img = document.querySelector('#monPoster');
                    s.push({ t: performance.now() - t0, idx: player.idx, playing: player.playing,
                             off: !!player.off, ph: tl.state.playhead,
                             line: w && !w.hidden ? w.textContent.trim() : '',
                             poster: !!(img && !img.hidden),
                             liveBlack: !!(v && v.classList.contains('live') && !v._shown && v.readyState < 2),
                             ct: v ? v.currentTime : null });
                    const last = s[s.length - 1];
                    if ((last.idx === 2 && last.ct > segs[2].in + 0.6) || last.t > 12000 || !last.playing && last.t > 500) {
                        ok(s); return;
                    }
                    requestAnimationFrame(look);
                };
                look();
            })""")
            on_b = [x for x in out if x["idx"] == 1]
            assert on_b, f"never reached shot 2: {out[-3:]}"
            enter_b = on_b[0]["t"]
            leave_b = on_b[-1]["t"]
            c = [x for x in out if x["idx"] == 2]
            dur_b = CUT["segments"][1]["out"] - CUT["segments"][1]["in"]
            print(f"shot 2 (building) on screen from {enter_b:.0f} to {leave_b:.0f} ms "
                  f"({leave_b - enter_b:.0f} ms for a {dur_b:.1f} s shot); "
                  f"shot 3 reached: {bool(c)}; playhead {on_b[0]['ph']:.2f} → {on_b[-1]['ph']:.2f}")
            assert c, f"Play never reached the shot after the building one: {out[-3:]}"
            assert c[-1]["playing"] and c[-1]["ct"] > CUT["segments"][2]["in"] + 0.5, c[-1]
            # it went past on the clock: the playhead moved through the shot, the line was up
            assert on_b[-1]["ph"] - on_b[0]["ph"] > dur_b * 0.7, (on_b[0], on_b[-1])
            assert any(x["off"] for x in on_b), "no film clock ran over the building shot"
            shown = [x for x in on_b if x["t"] - enter_b > SEEK_LIMIT_MS]
            assert shown and all(x["poster"] and "preparing preview" in x["line"]
                                 and "CLIP_B" in x["line"] for x in shown), \
                [x for x in shown if not (x["poster"] and "preparing preview" in x["line"])][:3]
            assert not any(x["liveBlack"] for x in shown), "a black live buffer was on screen"
            # about the shot's own length on the clock (not frozen, not skipped)
            assert dur_b * 0.8 <= (leave_b - enter_b) / 1000 <= dur_b + 1.5, (enter_b, leave_b)
            assert not [e for e in errors if "Error" in e], errors
        finally:
            browser.close()


def test_the_preview_landing_puts_the_shot_on_screen(building):
    """Parked on the building shot with the poster up; the preview lands (here: its
    original becomes playable) and the shot's own frame replaces the poster."""
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
    with sync_api.sync_playwright() as pw:
        browser, page, errors = _board(pw, building["url"])
        try:
            page.evaluate("cueAt(1, segs[1].in + 0.5)")
            page.wait_for_function("!document.querySelector('#monWait').hidden", timeout=5000)
            building["release"]()
            page.wait_for_function(
                "(() => { const v = liveVideo(); return v.classList.contains('live') && v.readyState >= 2"
                " && !v.error && document.querySelector('#monWait').hidden"
                " && v.dataset.src.includes('CLIP_B'); })()", timeout=15000)
            page.wait_for_function(
                "(() => { const r = document.querySelector('#projList [data-clip=\"CLIP_B.MP4\"]');"
                " return r && !r.querySelector('td.st[data-state=building]'); })()", timeout=15000)
            assert not [e for e in errors if "Error" in e], errors
        finally:
            browser.close()


# ------------------------------------------------------------------ the queue's order


def test_after_accept_the_cuts_previews_go_first_in_film_order(tmp_path, project, monkeypatch):
    """The media walk's order (`_next_media_job`), driven to the end on a fixture: a bin
    of 6 clips, an accepted cut that plays D · B · F · B (film order D, B, F), the human's
    Source monitor on A (not in the cut). Every cut clip's preview comes before any range
    proxy and before any other clip's; then the ranges, in film order; then the rest. A clip
    a monitor waits on (`hold_media`) goes first of all.

    The queue-order number: the job at which the last cut clip's preview is built — here
    the 3rd of 9. The walk before verdict 8 (`02fc382`, the same simulation) built it at
    the 6th: A's preview (the Source monitor's clip) first, then D, D's range, B, B's
    range, F. On Killington's 9-clip cut that is the 9th job instead of the ~18th."""
    from test_server import _fresh
    import server

    stems = ["CLIP_A", "CLIP_B", "CLIP_C", "CLIP_D", "CLIP_E", "CLIP_F"]
    footage = tmp_path / "footage"
    footage.mkdir()
    for s in stems:
        (footage / f"{s}.MP4").write_bytes(b"x")
    cut = {**CUT, "segments": [
        {"id": "a", "clip": "CLIP_D.MP4", "in": 0.0, "out": 1.0},
        {"id": "b", "clip": "CLIP_B.MP4", "in": 0.0, "out": 1.0},
        {"id": "c", "clip": "CLIP_F.MP4", "in": 0.0, "out": 1.0},
        {"id": "d", "clip": "CLIP_B.MP4", "in": 2.0, "out": 3.0},
    ]}
    edl = tmp_path / "cut.json"
    edl.write_text(json.dumps(cut), encoding="utf-8")
    old = time.time() - 60
    import os
    os.utime(edl, (old, old))
    with _fresh(tmp_path, project, edl=edl):
        server.STATE["footage"] = footage
        server.MEDIA["wanted"].clear()
        server.MEDIA.setdefault("holds", {}).clear()
        previews: set[str] = set()
        ranges: set[str] = set()
        monkeypatch.setattr(server, "ranges_enabled", lambda: True)
        monkeypatch.setattr(server, "footage_clips", lambda: [f"{s}.MP4" for s in stems])
        monkeypatch.setattr(server, "needs_preview", lambda c: c not in previews)
        monkeypatch.setattr(server, "needs_range",
                            lambda c, cut=None: c in previews and c not in ranges)
        server.want_media("CLIP_A")                   # the Source monitor opened CLIP_A
        jobs = []
        for _ in range(20):
            job = server._next_media_job()
            if job is None:
                break
            jobs.append((job[0], Path(job[1]).stem))
            (previews if job[0] == "preview" else ranges).add(job[1])
        print("walk after Accept:", jobs)
        assert jobs[:3] == [("preview", "CLIP_D"), ("preview", "CLIP_B"), ("preview", "CLIP_F")], jobs
        assert jobs[3:6] == [("range", "CLIP_D"), ("range", "CLIP_B"), ("range", "CLIP_F")], jobs
        assert jobs[6] == ("preview", "CLIP_A"), "the clip the human opened leads the rest"
        assert sorted(s for k, s in jobs[7:]) == ["CLIP_C", "CLIP_E"] and len(jobs) == 9, jobs
        last_cut = max(i for i, j in enumerate(jobs) if j[0] == "preview" and j[1] in
                       ("CLIP_D", "CLIP_B", "CLIP_F")) + 1
        print(f"queue-order number: the last cut clip's preview is job {last_cut} of {len(jobs)}")
        assert last_cut == 3

        # a monitor waiting on a clip (the Program played onto it) puts it first of all
        previews.clear()
        ranges.clear()
        server.hold_media("CLIP_F.MP4")
        assert server._next_media_job() == ("preview", "CLIP_F.MP4")
        # and a preview another thread is building is skipped, not waited on
        key = str(server.preview_path("CLIP_F"))
        with server.MEDIA["lock"]:
            server.MEDIA["building"][key] = threading.Event()
        try:
            assert server._next_media_job() == ("preview", "CLIP_D.MP4")
        finally:
            with server.MEDIA["lock"]:
                server.MEDIA["building"].pop(key, None)
            server.MEDIA["holds"].clear()
            server.MEDIA["wanted"].clear()
