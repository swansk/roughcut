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
    # no head (wave 14) unless a test asks for one: these tests are about a shot with no
    # picture at all, which a head made in a second would give one
    real_want_head = server.want_head
    monkeypatch.setattr(server, "want_head", lambda clip, t: None)

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
               "asked": asked,
               "heads": lambda: monkeypatch.setattr(server, "want_head", real_want_head)}
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


def _slow_original_posters(server, monkeypatch, delay_s: float) -> None:
    """A poster cut from an original takes `delay_s` longer (Killington's 4K GoPro frame:
    0.7-1.3 s on a loaded box, BLACK's follow-up), so a seek's own frame is slow to come."""
    real = server.build_poster

    def slow(src, dest, t, original=False):
        if original:
            time.sleep(delay_s)
        return real(src, dest, t, original=original)

    monkeypatch.setattr(server, "build_poster", slow)


def test_a_poster_cut_from_the_original_answers_the_nearest_still_at_once(building, monkeypatch):
    """Wave 13 CANVAS2: `near=1` answers a clip's still already on disk at once when the
    asked frame is not cut yet and would come from the original (no preview, no proxy);
    not cached, and it says which time it shows. The exact frame is still the exact frame,
    and a clip with a proxy always answers its exact frame."""
    url, server = building["url"], building["server"]
    with urllib.request.urlopen(f"{url}/media/poster/CLIP_B.jpg?t=0.40", timeout=30) as r:
        assert r.status == 200
    _slow_original_posters(server, monkeypatch, 1.5)
    t0 = time.monotonic()
    with urllib.request.urlopen(f"{url}/media/poster/CLIP_B.v2.jpg?near=1&t=2.37", timeout=30) as r:
        took = time.monotonic() - t0
        assert r.status == 200 and r.read(3) == b"\xff\xd8\xff"
        assert (r.headers["x-roughcut-poster"] or "").startswith("near "), dict(r.headers)
        assert "no-store" in r.headers["cache-control"]
    print(f"near still for CLIP_B at 2.37 in {took * 1000:.0f} ms")
    assert took < 0.3, f"the near still took {took:.2f}s"
    # the exact frame: cut (slowly) from the original, immutable
    t0 = time.monotonic()
    with urllib.request.urlopen(f"{url}/media/poster/CLIP_B.v2.jpg?t=2.37", timeout=30) as r:
        assert r.status == 200 and "x-roughcut-poster" not in r.headers
        assert "immutable" in r.headers["cache-control"]
    assert time.monotonic() - t0 >= 1.4
    # now on disk: near=1 answers it, exact
    with urllib.request.urlopen(f"{url}/media/poster/CLIP_B.v2.jpg?near=1&t=2.37", timeout=30) as r:
        assert "x-roughcut-poster" not in r.headers
    # a clip with a proxy: its exact frame (cut from the proxy), never a near one
    with urllib.request.urlopen(f"{url}/media/poster/CLIP_A.v2.jpg?t=0.00", timeout=30) as r:
        assert r.status == 200
    with urllib.request.urlopen(f"{url}/media/poster/CLIP_A.v2.jpg?near=1&t=1.73", timeout=30) as r:
        assert "x-roughcut-poster" not in r.headers
    assert server.poster_path("CLIP_A", 1.73).exists()


def test_a_seek_onto_a_building_shot_shows_a_picture_within_300_ms(building, monkeypatch):
    """BLACK's follow-up (wave 13): on a fresh Killington open the poster landed 0.7-1.3 s
    after a seek onto a building shot (every seek a new frame, cut from the 4K original), so
    "preparing preview…" stood on black until then. The Program shows the clip's nearest
    still at once and the exact frame when it lands."""
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
    server = building["server"]
    with urllib.request.urlopen(f"{building['url']}/media/poster/CLIP_B.jpg?t=0.40", timeout=30) as r:
        assert r.status == 200
    _slow_original_posters(server, monkeypatch, 1.2)
    with sync_api.sync_playwright() as pw:
        browser, page, errors = _board(pw, building["url"])
        try:
            page.evaluate("cueAt(0, segs[0].in + 0.2)")
            page.wait_for_function("liveVideo().readyState >= 2 && !liveVideo().seeking", timeout=10000)
            res = []
            for k in range(3):
                got = page.evaluate("""async (k) => {
                    const t0 = performance.now();
                    cueAt(1, segs[1].in + 0.37 + k * 0.41);
                    return await new Promise((ok) => {
                      const look = () => {
                        const img = document.querySelector('#monPoster');
                        const src = img ? img.getAttribute('src') || '' : '';
                        if (img && !img.hidden && src.includes('CLIP_B') && img.complete && img.naturalWidth > 0) {
                          ok({ ms: performance.now() - t0, src }); return;
                        }
                        if (performance.now() - t0 > 5000) { ok({ ms: null, src, hidden: img && img.hidden }); return; }
                        requestAnimationFrame(look);
                      };
                      look();
                    });
                }""", k)
                exact = page.evaluate("""() => new Promise((ok) => { const t0 = performance.now();
                    const look = () => { const s = document.querySelector('#monPoster').getAttribute('src') || '';
                      if (!s.includes('near=1') && document.querySelector('#monPoster').complete) { ok({ ms: performance.now() - t0, s }); return; }
                      if (performance.now() - t0 > 8000) { ok({ ms: null, s }); return; }
                      requestAnimationFrame(look); };
                    look(); })""")
                res.append((got, exact))
                page.evaluate("cueAt(2, segs[2].in + 0.2)")
                page.wait_for_function(
                    "liveVideo().readyState >= 2 && liveVideo().classList.contains('live')", timeout=10000)
            print("seek onto the building shot → a picture:", [None if g["ms"] is None else round(g["ms"]) for g, _ in res],
                  "then the exact frame:", [e["ms"] and round(e["ms"]) for _, e in res])
            for got, exact in res:
                assert got["ms"] is not None and got["ms"] <= SEEK_LIMIT_MS, got
                assert exact["ms"] is not None and "CLIP_B" in exact["s"], exact
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


# ------------------------------------------------------------------ the head (wave 14, punch 6)
#
# Verdict 10 punch 6: a clip imported mid-session, put in with `,` and played, first played
# ~21 s later (judge10 freshplay.log: its whole 360p preview had to be made first). The
# clip time the monitor waits at gets a HEAD first (server.py `want_head`): HEAD_PRE_S
# before it to HEAD_S after, on the clip's own timeline; the board plays it, and past its
# end the shot plays on the film clock on its poster until the preview lands. Here CLIP_B's
# whole preview never ends (the fixture), its head is made for real (build_preview).

HEAD_PLAY_LIMIT_S = 10.0     # Done = a just-imported clip presents frames within 10 s


def _status(url: str, stem: str, at: float | None = None) -> dict:
    q = "" if at is None else f"?at={at:.2f}"
    with urllib.request.urlopen(f"{url}/media/status/{stem}.mp4{q}", timeout=10) as r:
        return json.loads(r.read())


def _frame_rgb(path: Path, n: int) -> "list[float]":
    """Frame n (decode order = display order here) of a file, 80x45 RGB, as floats."""
    import subprocess
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-vf",
                        f"select=eq(n\\,{n}),scale=80:45", "-frames:v", "1", "-f", "rawvideo",
                        "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
    return list(r.stdout)


def test_a_waited_on_clip_gets_a_head_at_the_monitor_s_time_on_its_own_timeline(building, tmp_path):
    import subprocess
    building["heads"]()
    url, server = building["url"], building["server"]
    t0 = time.monotonic()
    st = _status(url, "CLIP_B", at=3.0)
    assert st["serves"] == "building" and not st["ready"], st
    while not st.get("head") and time.monotonic() - t0 < 20:
        time.sleep(0.1)
        st = _status(url, "CLIP_B")
    took = time.monotonic() - t0
    print(f"CLIP_B's head for clip 3.0 s in {took:.2f}s: {st['head']}")
    h = st["head"]
    assert h, f"no head within 20 s: {st}"
    assert h["a"] == pytest.approx(3.0 - server.HEAD_PRE_S) and h["b"] == pytest.approx(6.0), h
    # still not what /media/proxy serves: only the board knows whether its time is in it
    assert st["serves"] == "building" and not st["ready"]
    try:
        urllib.request.urlopen(f"{url}/media/proxy/CLIP_B.mp4", timeout=10)
        raise AssertionError("/media/proxy served the head")
    except urllib.error.HTTPError as exc:
        assert exc.code == 503
    # the file: its frames at the clip's own times
    got = tmp_path / "head.mp4"
    with urllib.request.urlopen(f"{url}{h['url']}", timeout=10) as r:
        assert r.headers["content-type"].startswith("video/mp4")
        got.write_bytes(r.read())
    pr = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "frame=pts_time", "-of", "json", str(got)], capture_output=True, text=True, check=True).stdout)
    pts = [float(f["pts_time"]) for f in pr["frames"]]
    # from 0 (Chrome clamps a seek to a file's length from its first frame): the span's
    # first frame is held from 0, the rest are at their own times
    assert pts[0] == 0.0 and pts[1] == pytest.approx(1.0 + 1 / 24, abs=1e-3), pts[:3]
    assert pts[-1] == pytest.approx(6.0 - 1 / 24, abs=1 / 12), pts[-3:]
    snd = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=start_time,duration", "-of", "json", str(got)], capture_output=True, text=True,
        check=True).stdout)["streams"]
    assert snd and float(snd[0]["start_time"]) == 0.0 and float(snd[0]["duration"]) >= 5.9, snd
    # the head's frame at clip time 3.0 is the source's frame at 3.0, not its neighbour's
    k = min(range(len(pts)), key=lambda i: abs(pts[i] - 3.0))
    src = Path(server.STATE["footage"]) / "CLIP_B.MP4"
    mine = _frame_rgb(got, k)
    diff = lambda a, b: sum(abs(x - y) for x, y in zip(a, b)) / len(a)   # noqa: E731
    same, near = diff(mine, _frame_rgb(src, 72)), diff(mine, _frame_rgb(src, 66))
    print(f"head frame at {pts[k]:.3f}: mean |diff| vs source 3.0 s {same:.1f}, vs 2.75 s {near:.1f}")
    assert same < near / 2, (same, near)
    # a time the newest head holds makes no other; one it does not, a new one
    assert server.head_covers(server.head_record("CLIP_B"), 4.0)
    assert not server.head_covers(server.head_record("CLIP_B"), 0.2)


def _head_board(pw, url):
    browser, page, errors = _board(pw, url)
    page.evaluate("cueAt(0, segs[0].in + 0.2)")
    page.wait_for_function("liveVideo().readyState >= 2 && !liveVideo().seeking", timeout=10000)
    return browser, page, errors


# frames the live buffer presents, by its own media clock: [page ms, mediaTime, src]
FRAMES = """() => { window.__hf = []; window.__hfOn = true;
  for (const v of player.vids) { if (v.__hfRec) continue; v.__hfRec = true;
    const cb = (now, md) => { if (window.__hfOn && v.classList.contains('live'))
        window.__hf.push([now, md.mediaTime, decodeURIComponent(v.currentSrc || '')]);
      v.requestVideoFrameCallback(cb); };
    v.requestVideoFrameCallback(cb); } }"""


def test_a_shot_waiting_on_its_preview_plays_from_its_head_within_10_s(building):
    """The judge's freshplay on the fixture: a seek onto the building shot (the wait line),
    then Play at once; the shot's own frames play, from its head, inside 10 s, at its clip's
    times (a frame's mediaTime is the clip time it shows)."""
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
    building["heads"]()
    # a 5 s shot of CLIP_B (the fixture's 2 s one ends while its head is still being made)
    edl = json.loads(building["server"].STATE["edl"].read_text(encoding="utf-8"))
    edl["segments"][1].update({"in": 0.5, "out": 5.5})
    building["server"].STATE["edl"].write_text(json.dumps(edl), encoding="utf-8")
    with sync_api.sync_playwright() as pw:
        browser, page, errors = _head_board(pw, building["url"])
        try:
            page.evaluate(FRAMES)
            t0 = time.monotonic()
            page.evaluate("cueAt(1, segs[1].in + 0.3)")       # clip 0.8 s of CLIP_B
            page.wait_for_function("!document.querySelector('#monWait').hidden", timeout=5000)
            page.evaluate("playFrom(1)")
            page.wait_for_function(
                "window.__hf.some(([t, mt, src]) => src.includes('/media/head/CLIP_B'))", timeout=20000)
            took = time.monotonic() - t0
            page.wait_for_timeout(700)
            fr = [f for f in page.evaluate("window.__hf") if "/media/head/CLIP_B" in f[2]]
            st = page.evaluate("({ph: tl.state.playhead, idx: player.idx, off: !!player.off,"
                               " wait: !document.querySelector('#monWait').hidden})")
            print(f"seek + Play on the building shot → its own frames in {took:.2f}s; "
                  f"{len(fr)} frames, clip {fr[0][1]:.3f} → {fr[-1][1]:.3f}; {st}")
            assert took <= HEAD_PLAY_LIMIT_S, f"{took:.1f}s to the shot's first played frame"
            assert len(fr) >= 10, fr
            mts = [mt for _t, mt, _s in fr]
            assert all(b > a for a, b in zip(mts, mts[1:])), "the head's frames did not move on"
            # on the clip's timeline: from about where the clock was, never from 0
            assert 0.8 <= mts[0] <= 5.0 and mts[-1] - mts[0] >= 0.3, mts
            assert st["idx"] == 1 and not st["off"] and not st["wait"], st
            assert not [e for e in errors if "Error" in e], errors
        finally:
            browser.close()


def test_past_its_head_the_shot_plays_on_the_film_clock_and_the_next_one_plays(building, monkeypatch):
    """A head shorter than the shot (HEAD_S 1 s here): its frames, then the poster and the
    wait line on the film clock to the shot's out, then the next shot; never a black buffer,
    never the next shot early."""
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
    server = building["server"]
    building["heads"]()
    monkeypatch.setattr(server, "HEAD_S", 1.0)
    with sync_api.sync_playwright() as pw:
        browser, page, errors = _head_board(pw, building["url"])
        try:
            page.evaluate("cueAt(1, segs[1].in + 0.2)")       # head [0, 2.2] of a 1.0-3.0 shot
            page.wait_for_function("liveVideo()._head && liveVideo().readyState >= 2"
                                   " && liveVideo().classList.contains('live')", timeout=20000)
            head = page.evaluate("liveVideo()._head")
            assert head["b"] == pytest.approx(2.2), head
            page.evaluate(FRAMES)
            out = page.evaluate("""() => new Promise((ok) => {
                const s = []; const t0 = performance.now();
                playFrom(1);
                const look = () => {
                    const v = liveVideo(), w = document.querySelector('#monWait'), img = document.querySelector('#monPoster');
                    s.push({ t: performance.now() - t0, idx: player.idx, off: !!player.off, ph: tl.state.playhead,
                             line: w && !w.hidden ? w.textContent.trim() : '', poster: !!(img && !img.hidden),
                             liveBlack: !!(v && v.classList.contains('live') && v.readyState < 2), playing: player.playing });
                    const l = s[s.length - 1];
                    if ((l.idx === 2 && l.ph > segs[0].out - segs[0].in + 2.0 + 0.6) || l.t > 9000 || (!l.playing && l.t > 500)) { ok(s); return; }
                    requestAnimationFrame(look);
                };
                look();
            })""")
            fr = [f for f in page.evaluate("window.__hf") if "CLIP_B" in f[2]]
            on_b = [x for x in out if x["idx"] == 1]
            past = [x for x in on_b if x["off"]]
            c = [x for x in out if x["idx"] == 2]
            print(f"head frames {len(fr)} up to clip {fr[-1][1] if fr else None}; past it on the clock "
                  f"{len(past)} samples, ph {past[0]['ph'] if past else None} → {past[-1]['ph'] if past else None}; "
                  f"next shot reached: {bool(c)}")
            assert fr and max(mt for _t, mt, _s in fr) >= 2.0, fr[-3:]
            assert past, "the head's end was not played past on the clock"
            assert all(x["poster"] and "preparing preview" in x["line"] for x in past[3:]), past[:5]
            # the clock ran to the shot's out (film 1.5 + 2.0), not cut short at the head's end
            assert past[-1]["ph"] >= 1.5 + 2.0 - 0.15, past[-1]
            assert c and c[-1]["playing"], out[-3:]
            assert not any(x["liveBlack"] for x in on_b), "a black live buffer was on screen"
            assert not [e for e in errors if "Error" in e], errors
        finally:
            browser.close()


def test_a_missing_generated_clip_says_what_is_wrong(building):
    """Wave 13's open note: a shot on a generated clip whose file is gone (a 404) said
    "preparing preview…" for ever. It says the media is offline and why."""
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
    edl = json.loads(building["server"].STATE["edl"].read_text(encoding="utf-8"))
    edl["segments"].insert(1, {"id": "g1", "clip": "gen_black_0123abcd.mp4", "in": 0.0, "out": 1.0,
                               "why": "a black that is not there"})
    building["server"].STATE["edl"].write_text(json.dumps(edl), encoding="utf-8")
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        try:
            page.goto(building["url"])
            page.wait_for_function("segs.length === 4 && player.vids.length >= 2", timeout=20000)
            page.evaluate("cueAt(1, 0.3)")
            page.wait_for_function("!document.querySelector('#monWait').hidden"
                                   " && /offline/i.test(document.querySelector('#monWait').textContent)", timeout=5000)
            line = page.locator("#monWait").inner_text()
            page.wait_for_timeout(3500)                     # and it stays so: no "preparing" loop
            again = page.locator("#monWait").inner_text()
            print("missing generated clip:", line)
            assert "Media offline" in line and "Black Video" in line and "missing" in line, line
            assert "preparing" not in again and again == line, again
            assert not __import__("re").search(r"gen_[a-z]+_[0-9a-f]{6}", line), line
            assert not [e for e in errors if "Error" in e], errors
        finally:
            browser.close()
