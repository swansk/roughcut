"""Picture at once (REDESIGN-GOAL dimensions 1, 3, 6; verdict 1 punch 5, server half).

Verdict 1: "You can't see your footage when you open it." Posters 404'd and the monitor
showed a raw MEDIA_ELEMENT_ERROR until each clip's 720p proxy finished, ~8 min a clip on
Killington. Here every test runs on a bin with **no proxies at all** and asserts:

  * a poster for every clip within 5 s of open, straight from the original;
  * `/media/proxy/<stem>.mp4` answers with playable video before any proxy exists — the
    original when the browser can decode it, else a 360p preview built on demand — and
    range requests (seeks) work on whatever it redirects to;
  * a <video> in real Chromium reaches readyState >= 2 within 5 s (skipped without
    playwright).

    uv run --with pytest --with fastapi --with uvicorn --with httpx --with numpy \
        --with playwright pytest app/tests/test_media_first.py -q
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from conftest import _make_clip, _sidecar


def _make_mpeg4_clip(path: Path, seconds: float = 6.0) -> None:
    """A clip no browser plays as it is (MPEG-4 part 2 video, like an old camera's):
    the server must build a preview for it."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
         "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True)


def _probe(path: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height:format=duration", "-of", "json",
         str(path)], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def _keyframe_times(path: Path) -> list[float]:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
         "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True)
    return [float(x.strip(",")) for x in r.stdout.split() if x.strip(",")]


@pytest.fixture(scope="module")
def media_bin(tmp_path_factory):
    """Two browser-playable clips (h264/aac, like the suite's fixture) and two that are
    not (MPEG-4 part 2), with sidecars, and a work dir that has never seen them."""
    root = tmp_path_factory.mktemp("mediabin")
    footage, sidecars = root / "footage", root / "sidecars"
    footage.mkdir()
    sidecars.mkdir()
    for stem in ("CLIP_A", "CLIP_B"):
        _make_clip(footage / f"{stem}.MP4")
    for stem in ("CLIP_M", "CLIP_N"):
        _make_mpeg4_clip(footage / f"{stem}.MP4")
    for stem in ("CLIP_A", "CLIP_B", "CLIP_M", "CLIP_N"):
        (sidecars / f"{stem}.audio.json").write_text(json.dumps(_sidecar(stem)),
                                                      encoding="utf-8")
    return {"root": root, "footage": footage, "sidecars": sidecars,
            "stems": ["CLIP_A", "CLIP_B", "CLIP_M", "CLIP_N"]}


@pytest.fixture
def fresh(media_bin, tmp_path):
    """The bin opened on a work dir with no proxies, no posters, no previews — the way
    `main()` opens it, minus the background proxy build (proxies=False)."""
    from fastapi.testclient import TestClient
    import server

    work = tmp_path / "work"
    server.MEDIA["failed"].clear()
    server.configure(None, media_bin["footage"], media_bin["sidecars"], work,
                     proxies=False, orient="none", visual=work / "no-visual")
    with TestClient(server.app) as c:
        yield {"client": c, "work": work, "server": server}
    server.MEDIA["wanted"].clear()


def _no_proxies(server) -> None:
    assert list(Path(server.STATE["proxy_dir"]).glob("*.mp4")) == [], "a proxy exists"


# ------------------------------------------------------------------ posters


def _poster(c, name: str, wait_s: float = 20.0, **params):
    """A poster as the board gets it: a frame not cut yet is a stand-in at once (wave 15:
    cut in the background, never inside the request; `x-roughcut-poster` says so), so ask
    again until it is the frame."""
    deadline = time.monotonic() + wait_s
    while True:
        r = c.get(f"/media/poster/{name}", params=params or None)
        if "x-roughcut-poster" not in r.headers or time.monotonic() > deadline:
            return r
        time.sleep(0.05)


def test_a_poster_for_every_clip_within_five_seconds_of_open(fresh, media_bin):
    """The bin's cards ask for `/media/poster/<stem>.jpg` the moment they render.
    Before, that 404'd until the proxy existed and every card was a broken <img>."""
    c, server = fresh["client"], fresh["server"]
    t0 = time.monotonic()
    for stem in media_bin["stems"]:
        r = _poster(c, f"{stem}.jpg")
        assert r.status_code == 200, (stem, r.status_code, r.text[:200])
        assert r.headers["content-type"] == "image/jpeg"
        assert r.content[:2] == b"\xff\xd8", f"{stem}: not a JPEG"
    took = time.monotonic() - t0
    assert took < 5.0, f"{len(media_bin['stems'])} posters took {took:.2f}s"
    _no_proxies(server)


def test_a_poster_at_a_time_comes_from_the_original_too(fresh):
    c = fresh["client"]
    a = _poster(c, "CLIP_A.jpg", t=0.5)
    b = _poster(c, "CLIP_A.jpg", t=4.5)
    assert a.status_code == b.status_code == 200
    assert a.content != b.content
    assert "immutable" in a.headers["cache-control"]
    _no_proxies(fresh["server"])


def test_a_poster_for_no_such_clip_is_still_a_404(fresh):
    assert fresh["client"].get("/media/poster/NOPE.jpg").status_code == 404
    assert fresh["client"].get("/media/poster/..%2F..%2Fetc%2Fpasswd").status_code == 404


# ------------------------------------------------------------------ playable media


def test_a_browser_playable_original_plays_before_any_proxy(fresh, tmp_path):
    """h264/aac at 180p: the original itself, through a redirect, with ranges."""
    c, server = fresh["client"], fresh["server"]
    t0 = time.monotonic()
    r = c.get("/media/proxy/CLIP_A.mp4", follow_redirects=False)
    assert r.status_code == 307, r.status_code
    assert r.headers["location"] == "/media/original/CLIP_A.mp4"
    assert r.headers["x-roughcut-media"] == "original"
    assert "no-store" in r.headers["cache-control"]
    full = c.get("/media/proxy/CLIP_A.mp4")             # TestClient follows the redirect
    assert time.monotonic() - t0 < 5.0
    assert full.status_code == 200
    assert full.headers["content-type"] == "video/mp4"
    assert full.headers["accept-ranges"] == "bytes"
    out = tmp_path / "a.mp4"
    out.write_bytes(full.content)
    info = _probe(out)
    assert {s["codec_type"] for s in info["streams"]} >= {"video", "audio"}
    assert float(info["format"]["duration"]) == pytest.approx(6.0, abs=0.2)
    # a seek is a range request against the redirect target
    part = c.get("/media/proxy/CLIP_A.mp4", headers={"Range": "bytes=1000-1999"})
    assert part.status_code == 206
    assert part.headers["content-range"] == f"bytes 1000-1999/{len(full.content)}"
    assert part.content == full.content[1000:2000]
    _no_proxies(server)
    assert not server.preview_path("CLIP_A").exists(), "built a preview it did not need"


def test_a_clip_the_browser_cannot_play_gets_a_preview_first(fresh, tmp_path):
    """MPEG-4 part 2 is not something a <video> decodes. The first ask builds a 360p-or-
    smaller h264 preview with a keyframe every half second, and redirects to it."""
    c, server = fresh["client"], fresh["server"]
    t0 = time.monotonic()
    # Verdict 8: the ask never holds more than a second; while the preview is still being
    # made it is a 503 with retry-after, and the board asks again (here: at once)
    while True:
        a0 = time.monotonic()
        r = c.get("/media/proxy/CLIP_M.mp4", follow_redirects=False)
        assert time.monotonic() - a0 <= 1.0, "held the request"
        if r.status_code != 503 or time.monotonic() - t0 > 5.0:
            break
        assert 1 <= int(r.headers["retry-after"]) <= 10
        time.sleep(0.1)
    took = time.monotonic() - t0
    assert r.status_code == 307, (r.status_code, r.text[:200])
    assert r.headers["location"] == server.preview_url("CLIP_M")
    assert r.headers["x-roughcut-media"] == "preview"
    assert took < 5.0, f"first playable media took {took:.2f}s"
    full = c.get("/media/proxy/CLIP_M.mp4")
    assert full.status_code == 200 and full.headers["content-type"] == "video/mp4"
    out = tmp_path / "m.mp4"
    out.write_bytes(full.content)
    info = _probe(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert v["codec_name"] == "h264"
    assert v["height"] <= server.PREVIEW_H and v["height"] == 180, "upscaled or wrong size"
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    assert float(info["format"]["duration"]) == pytest.approx(6.0, abs=0.2)
    keys = _keyframe_times(out)
    gaps = [b - a for a, b in zip(keys, keys[1:])]
    assert keys and max(gaps) <= server.PREVIEW_GOP_S + 0.05, f"keyframes {keys}"
    part = c.get("/media/proxy/CLIP_M.mp4", headers={"Range": "bytes=0-1023"})
    assert part.status_code == 206 and len(part.content) == 1024
    _no_proxies(server)


def test_the_software_path_makes_a_preview_without_a_gpu(fresh, monkeypatch, tmp_path):
    """No NVENC (a laptop, CI): libx264 ultrafast, same shape."""
    server = fresh["server"]
    monkeypatch.setenv("ROUGHCUT_PREVIEW_GPU", "0")
    dest = tmp_path / "soft.mp4"
    how = server.build_preview(Path(server.STATE["footage"]) / "CLIP_N.MP4", dest, "none")
    assert how == "cpu"
    v = next(s for s in _probe(dest)["streams"] if s["codec_type"] == "video")
    assert v["codec_name"] == "h264" and v["height"] == 180
    keys = _keyframe_times(dest)
    assert max(b - a for a, b in zip(keys, keys[1:])) <= server.PREVIEW_GOP_S + 0.05


def test_status_says_what_plays_and_wanting_a_clip_starts_its_preview(fresh):
    c, server = fresh["client"], fresh["server"]
    a = c.get("/media/status/CLIP_A.mp4").json()
    assert a["serves"] == "original" and a["ready"] and a["url"] == "/media/original/CLIP_A.mp4"
    n = c.get("/media/status/CLIP_N.mp4").json()
    assert n["serves"] in ("building", "preview"), n
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not n["ready"]:
        time.sleep(0.1)
        n = c.get("/media/status/CLIP_N.mp4").json()
    assert n["ready"] and n["serves"] == "preview" and n["url"] == server.preview_url("CLIP_N")
    assert server.MEDIA["wanted"][0] == "CLIP_N"
    assert c.get("/media/status/NOPE.mp4").status_code == 404


def test_unknown_or_hostile_names_still_404(fresh):
    c = fresh["client"]
    assert c.get("/media/proxy/NOPE.mp4").status_code == 404
    assert c.get("/media/proxy/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert c.get("/media/original/CLIP_M.mp4").status_code == 404, \
        "an original the browser cannot play is never served raw"
    assert c.get("/media/original/..%2FCLIP_A.mp4").status_code == 404
    assert c.get("/media/preview/NOPE.mp4").status_code == 404


def test_once_the_proxy_exists_it_is_served_directly(fresh):
    c, server = fresh["client"], fresh["server"]
    server.ensure_proxies(["CLIP_B.MP4"])
    r = c.get("/media/proxy/CLIP_B.mp4", follow_redirects=False)
    assert r.status_code == 200 and "x-roughcut-media" not in r.headers
    st = c.get("/media/status/CLIP_B.mp4").json()
    assert st["serves"] == "proxy" and st["url"] == "/media/proxy/CLIP_B.mp4"
    # and the proxy seeks cheaply: a keyframe at least every second
    keys = _keyframe_times(Path(server.STATE["proxy_dir"]) / "CLIP_B.mp4")
    assert max(b - a for a, b in zip(keys, keys[1:])) <= server.PROXY_GOP_S + 0.05, keys


def test_the_clip_the_human_opened_gets_its_proxy_next(fresh, monkeypatch):
    """Punch 5: order the proxy queue by what was opened. Asking the monitor for a clip
    moves it to the front of ensure_proxies' walk."""
    server = fresh["server"]
    built: list[str] = []
    monkeypatch.setattr(server, "build_proxy", lambda src, dest, orient: built.append(src.name))
    monkeypatch.setattr(server, "measure_colour", lambda clip, force=False: None)
    fresh["client"].get("/media/status/CLIP_N.mp4")
    fresh["client"].get("/media/status/CLIP_B.mp4")      # the most recent ask wins
    server.ensure_proxies(["CLIP_A.MP4", "CLIP_B.MP4", "CLIP_M.MP4", "CLIP_N.MP4"])
    assert built == ["CLIP_B.MP4", "CLIP_N.MP4", "CLIP_A.MP4", "CLIP_M.MP4"]


def test_the_background_walk_makes_posters_and_previews_when_proxies_are_on(
        media_bin, tmp_path, monkeypatch):
    """With proxies enabled (how `main()` runs) the bin is walked at open: a poster at
    the card frame (`POSTER_AT_S`, 1 s in: many clips open on black) for every clip, then a preview for every clip that needs one. Proxies are
    stubbed out here so only the walk is measured."""
    import server
    monkeypatch.setattr(server, "ensure_proxies", lambda clips, progress=None: None)
    if not server.preview_gpu():
        pytest.skip("the background preview walk runs on a GPU only")
    work = tmp_path / "work"
    server.configure(None, media_bin["footage"], media_bin["sidecars"], work,
                     proxies=True, orient="none", visual=work / "no-visual")
    try:
        deadline = time.monotonic() + 30
        want = {server.poster_path(s, server.POSTER_AT_S).name for s in media_bin["stems"]}
        while time.monotonic() < deadline:
            have = {p.name for p in Path(server.STATE["posters"]).glob("*.jpg")}
            if want <= have and all(server.preview_path(s).exists()
                                    for s in ("CLIP_M", "CLIP_N")):
                break
            time.sleep(0.2)
        assert want <= {p.name for p in Path(server.STATE["posters"]).glob("*.jpg")}
        assert server.preview_path("CLIP_M").exists() and server.preview_path("CLIP_N").exists()
        assert not server.preview_path("CLIP_A").exists(), "a playable original needs none"
    finally:
        server.STATE["proxies_enabled"] = False
        _quiet_media(server)


def _fake_encodes(server, monkeypatch, hold: threading.Event) -> list:
    """build_preview, recorded and held until `hold` is set: [(seconds since this was
    called, the file it writes, its span or None)]."""
    calls: list = []
    t0 = time.monotonic()

    def fake(src, dest, orient, span=None, **kw):
        calls.append((time.monotonic() - t0, Path(dest).name, span))
        hold.wait(10)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dest)
        return "fake"
    monkeypatch.setattr(server, "build_preview", fake)
    return calls


def _until(pred, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = pred()
        if got:
            return got
        time.sleep(0.02)
    return pred()


def test_a_short_clips_asked_preview_starts_at_once_and_a_head_asked_meanwhile_joins_it(
        fresh, monkeypatch):
    """Verdict 11, punch 10 (the cause of this file's flake): an asked whole preview waited
    HEAD_FIRST_S for a head ask before it started (wave 14), and a page that never asks for
    one (the Source monitor, the browser test below) lost those 2 s every time. A clip no
    longer than a head starts at once; the head the Program then asks for is that same
    preview, never a second encode of the same frames beside it."""
    c, server = fresh["client"], fresh["server"]
    assert server.head_is_clip("CLIP_M.MP4"), "the fixture's 6 s clip is shorter than a head"
    hold = threading.Event()
    calls = _fake_encodes(server, monkeypatch, hold)
    try:
        r = c.get("/media/proxy/CLIP_M.mp4", follow_redirects=False)
        assert r.status_code == 503, r.status_code
        assert _until(lambda: calls), "the asked preview did not start"
        assert calls[0][2] is None and calls[0][0] < server.HEAD_FIRST_S / 2, calls
        # the Program's head ask while it encodes: answered when the preview lands
        got: dict = {}
        ask = threading.Thread(target=lambda: got.update(
            c.get("/media/status/CLIP_M.mp4", params={"at": 2.0}).json()))
        ask.start()
        time.sleep(0.4)
        hold.set()
        ask.join(15)
        assert got.get("ready") and got.get("serves") == "preview", got
        assert [x[2] for x in calls] == [None], f"the same frames encoded twice: {calls}"
    finally:
        hold.set()


def test_a_longer_clips_asked_preview_still_lets_its_head_go_first(fresh, monkeypatch):
    """Past a head's length the wave-14 order stands: the asked whole preview waits for the
    head the board asks for a moment later, and that head is encoded first."""
    c, server = fresh["client"], fresh["server"]
    monkeypatch.setattr(server, "HEAD_S", 3.0)            # 6 s is longer than a head now
    assert not server.head_is_clip("CLIP_N.MP4")
    hold = threading.Event()
    calls = _fake_encodes(server, monkeypatch, hold)
    try:
        r = c.get("/media/proxy/CLIP_N.mp4", follow_redirects=False)
        assert r.status_code == 503, r.status_code
        time.sleep(0.3)
        assert calls == [], f"it did not wait for the head: {calls}"
        threading.Thread(target=lambda: c.get("/media/status/CLIP_N.mp4", params={"at": 3.0}),
                         daemon=True).start()
        assert _until(lambda: calls), "no encode after the head ask"
        assert calls[0][2] is not None, f"the head was not first: {calls}"
        hold.set()
        assert _until(lambda: len(calls) >= 2), calls
        assert calls[1][2] is None, calls
        assert _until(lambda: server.preview_path("CLIP_N").exists())
    finally:
        hold.set()


def _quiet_media(server, timeout: float = 15.0) -> None:
    """End the walk and what it left running: a preview that landed queues its clip's
    sprite, and the sprites (seconds of decode each) went on into the next test — the
    browser test's 5 s frame bar (verdict 11, punch 10: CLIP_M and CLIP_N's sprites, 3.2
    and 3.5 s, were built inside the gate's failing run)."""
    server.stop_media()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        walk, sprites = server.MEDIA.get("thread"), server.SPRITES.get("thread")
        if not (walk and walk.is_alive()) and not (sprites and sprites.is_alive()):
            return
        time.sleep(0.05)
    raise AssertionError("the media walk or the sprites did not stop")


# ------------------------------------------------------------------ in a browser


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_video_element_has_a_frame_within_five_seconds_with_no_proxies(media_bin, tmp_path,
                                                                        monkeypatch):
    """Real Chromium: `<video src=/media/proxy/...>` on a bin with no proxies reaches
    readyState >= 2 (a decoded frame) within 5 s, for an original and for a preview,
    and a seek lands.

    Verdict 11, punch 10: this failed in the judge's gate at load ~8-9 ("timeout rs=0" on
    the preview). Its 5 s held the preview's encode plus 2 s the app waited for a head the
    page never asks for (`_head_first`, wave 14) — fixed for a clip no longer than a head —
    and the previous test's sprites still being built beside it (`_quiet_media`). So the
    bar is also read apart: the app's own share (the frame less the encode the box gives
    it, timed on the server) is at most APP_SHARE_MS, which the 2 s wait failed on any box."""
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
    import uvicorn
    import server

    work = tmp_path / "work"
    server.MEDIA["failed"].clear()
    server.configure(None, media_bin["footage"], media_bin["sidecars"], work,
                     proxies=False, orient="none", visual=work / "no-visual")
    encodes: dict[str, float] = {}
    real_build = server.build_preview

    def timed(src, dest, orient, *a, **kw):
        t0 = time.monotonic()
        try:
            return real_build(src, dest, orient, *a, **kw)
        finally:
            encodes[Path(src).stem] = time.monotonic() - t0
    monkeypatch.setattr(server, "build_preview", timed)
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started
    try:
        with sync_api.sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/media/status/CLIP_A.mp4")
            for stem in ("CLIP_A", "CLIP_M"):
                ms = page.evaluate("""async (stem) => {
                    const v = document.createElement('video');
                    v.muted = true; v.preload = 'auto';
                    document.body.appendChild(v);
                    const t0 = performance.now();
                    v.src = `/media/proxy/${stem}.mp4`;
                    // a 503 while the preview is made (verdict 8: answered at once, not
                    // held): the board asks /media/status and loads the URL again
                    let tries = 0;
                    v.addEventListener('error', async () => {
                        if (++tries > 60) return;
                        await new Promise(r => setTimeout(r, 150));
                        v.src = `/media/proxy/${stem}.mp4`;
                    });
                    // waited for past the bar, so a miss says by how much
                    const ok = await new Promise((done) => {
                        const tick = () => v.readyState >= 2 ? done(true) :
                            (v.error && tries > 60) || performance.now() - t0 > 30000 ? done(false) :
                            setTimeout(tick, 20);
                        tick();
                    });
                    const ready = performance.now() - t0;
                    if (!ok) return {ready, tries, rs: v.readyState, err: v.error && v.error.code};
                    const s0 = performance.now();
                    v.currentTime = 4.0;
                    await new Promise(ok => v.addEventListener('seeked', ok, {once: true}));
                    return {ready, tries, seek: performance.now() - s0, w: v.videoWidth};
                }""", stem)
                enc = encodes.get(stem)
                facts = (stem, ms, f"encode {enc:.2f} s" if enc is not None else "no encode",
                         f"load {os.getloadavg()[0]:.1f}")
                assert ms.get("w", 0) > 0, facts
                assert ms["ready"] < 5000, facts
                assert ms["seek"] < 1000, facts
                if stem == "CLIP_M":
                    assert enc is not None, facts
                    assert ms["ready"] - enc * 1000 <= APP_SHARE_MS, facts
            browser.close()
    finally:
        srv.should_exit = True
        thread.join(timeout=10)
    assert list(Path(server.STATE["proxy_dir"]).glob("*.mp4")) == []


# The frame less the preview's encode: the 503s and the retry, the redirect, Chromium's
# first decode (0.0-0.2 s measured, on a quiet box and at load 12 with a 12.5 s encode).
# The head wait alone was 2.0 s (3.27 s to a frame for a 1.22 s encode).
APP_SHARE_MS = 1500
