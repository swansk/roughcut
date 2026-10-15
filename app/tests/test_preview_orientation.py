"""Every picture Roughcut shows is upright, exactly as the export is (verdict 3 punch 1).

Verdict 3: 9 of the 12 Killington GoPro clips (display matrix −180°) played upside down
in the Source monitor, the Program monitor and the filmstrips until their proxy existed,
while the export was upright. `build_preview`'s all-GPU path (NVDEC + scale_cuda + NVENC)
never applies the matrix — ffmpeg's autorotate does nothing to CUDA frames — and the
posters were cut from those previews.

Here a fixture clip carries an asymmetric picture (four differently coloured quadrants)
and a rotation flag written with `-display_rotation`. The reference is `ffmpeg -i src`
autorotated (what the proxy and the export do under `orient: auto`), or the stored
pixels under `orient: none`. Every output is decoded the way a browser shows it (the
rotation tag, if any, applied) and compared by the mean colour of each quadrant: the
preview on every path, `/media/proxy` before and after the 720p proxy exists, the
poster, the export's own part encode, and a rotated original the browser plays itself.

    uv run --with pytest --with fastapi --with uvicorn --with httpx --with numpy \
        --with playwright pytest app/tests/test_preview_orientation.py -q
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from conftest import _sidecar

ROOT = Path(__file__).resolve().parents[2]
SECONDS = 3.0
# yellow | red / green | dark: no flip or quarter turn of it looks like another
PATTERN = ("color=c=black:s=320x192:r=30:d={d},format=rgb24,"
           "geq=r='if(lt(Y,H/2),230,20)':g='if(lt(X,W/2),230,20)':b=20")
TOL = 40           # per channel, per quadrant: the colours are ~210 apart


def _x265() -> bool:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True)
    return "libx265" in r.stdout


def _make_pattern(dest: Path, rotation: int, playable: bool = False) -> None:
    """The pattern with a display matrix of `rotation` (ffmpeg's -display_rotation, so
    counter-clockwise; 180 is what ffprobe reports as -180, Karl's GoPro flag).
    `playable` is h264 a browser takes as it is; otherwise HEVC like a GoPro master
    (MPEG-4 part 2 where there is no libx265), which always goes through a preview."""
    base = dest.with_suffix(".base.mp4")
    if playable:
        vc = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    elif _x265():
        vc = ["-c:v", "libx265", "-x265-params", "log-level=error", "-pix_fmt", "yuv420p"]
    else:
        vc = ["-c:v", "mpeg4", "-q:v", "3"]
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i", PATTERN.format(d=SECONDS),
         "-f", "lavfi", "-i", f"sine=frequency=330:duration={SECONDS}",
         *vc, "-c:a", "aac", "-shortest", str(base)], check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin",
                    "-display_rotation", str(rotation), "-i", str(base), "-c", "copy",
                    str(dest)], check=True, capture_output=True)
    base.unlink()


def _rotation(path: Path) -> int:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream_side_data=rotation", "-of", "csv=p=0",
                        str(path)], capture_output=True, text=True)
    vals = [v for v in r.stdout.replace(",", " ").split() if v.strip()]
    return int(float(vals[0])) if vals else 0


def _quads(path: Path, pre: tuple[str, ...] = (), t: float = 0.5) -> np.ndarray:
    """Mean RGB of the four quadrants (TL, TR, BL, BR) of a frame, decoded the way a
    browser shows the file: ffmpeg's autorotate applies any rotation tag, as <video>
    and <img> do. `pre` = ("-display_rotation", "0") reads the stored pixels instead."""
    is_still = path.suffix.lower() in (".jpg", ".png")
    seek = [] if is_still else ["-ss", f"{t:.2f}"]
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", *pre, *seek, "-i", str(path),
         "-frames:v", "1", "-vf", "scale=64:64", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-"], capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(64, 64, 3).astype(float)
    return np.array([a[:32, :32].mean((0, 1)), a[:32, 32:].mean((0, 1)),
                     a[32:, :32].mean((0, 1)), a[32:, 32:].mean((0, 1))])


def _same(got: np.ndarray, want: np.ndarray, what: str) -> None:
    diff = np.abs(got - want).max()
    assert diff <= TOL, (f"{what}: the picture does not match the reference "
                         f"(max quadrant diff {diff:.0f})\n got {got.round()}\nwant {want.round()}")


def _upright(src: Path) -> np.ndarray:
    return _quads(src)                                       # autorotated: orient auto


def _stored(src: Path) -> np.ndarray:
    return _quads(src, pre=("-display_rotation", "0"))       # orient none


@pytest.fixture(scope="module")
def rot_bin(tmp_path_factory):
    root = tmp_path_factory.mktemp("rotbin")
    footage, sidecars = root / "footage", root / "sidecars"
    footage.mkdir()
    sidecars.mkdir()
    clips = {"ROT180": (180, False), "ROT90": (90, False), "ROT270": (-90, False),
             "FLAT": (0, False), "PLAY180": (180, True)}
    for stem, (rot, playable) in clips.items():
        _make_pattern(footage / f"{stem}.MP4", rot, playable)
        (sidecars / f"{stem}.audio.json").write_text(json.dumps(_sidecar(stem)),
                                                      encoding="utf-8")
    for stem, (rot, _) in clips.items():
        src = footage / f"{stem}.MP4"
        assert _rotation(src) % 360 == rot % 360, f"{stem}: fixture flag not written"
        if rot % 360:
            assert np.abs(_upright(src) - _stored(src)).max() > 100, \
                f"{stem}: the fixture's turn is not visible — the test would be vacuous"
    return {"root": root, "footage": footage, "sidecars": sidecars}


def _open(server, rot_bin, work: Path, orient: str) -> None:
    server.MEDIA["failed"].clear()
    server.MEDIA["wanted"].clear()
    server.configure(None, rot_bin["footage"], rot_bin["sidecars"], work,
                     proxies=False, orient=orient, visual=work / "no-visual")


@pytest.fixture
def board(rot_bin, tmp_path):
    from fastapi.testclient import TestClient
    import server

    work = tmp_path / "work"
    _open(server, rot_bin, work, "auto")
    with TestClient(server.app) as c:
        yield {"client": c, "server": server, "work": work}
    server.MEDIA["wanted"].clear()


def _fetch(client, url: str, dest: Path, **kw) -> Path:
    r = client.get(url, **kw)
    assert r.status_code == 200, (url, r.status_code, r.text[:200])
    dest.write_bytes(r.content)
    return dest


# ------------------------------------------------------------------ build_preview, per path


def _gpu() -> bool:
    import server
    return server.preview_gpu()


@pytest.mark.parametrize("stem", ["ROT180", "ROT90", "ROT270"])
@pytest.mark.parametrize("mode", ["gpu-turn", "gpu-decode", "cpu"])
def test_every_preview_path_turns_a_flagged_clip_like_the_export(
        rot_bin, tmp_path, monkeypatch, stem, mode):
    """`orient: auto`: the preview's picture is the autorotated source, and no rotation
    tag is left on it for the browser to apply a second time."""
    import server
    if mode == "cpu":
        monkeypatch.setenv("ROUGHCUT_PREVIEW_GPU", "0")
    elif not _gpu():
        pytest.skip("no NVENC here")
    if mode == "gpu-decode":
        monkeypatch.setattr(server, "TURN_VF", {})        # the turn path unavailable
    src = rot_bin["footage"] / f"{stem}.MP4"
    dest = tmp_path / f"{stem}.mp4"
    how = server.build_preview(src, dest, "auto")
    assert how == mode
    assert _rotation(dest) == 0, f"{mode} left a rotation tag on the preview"
    _same(_quads(dest), _upright(src), f"{stem} preview ({how})")


@pytest.mark.parametrize("stem", ["ROT180", "ROT90"])
def test_orient_none_previews_the_stored_pixels(rot_bin, tmp_path, stem):
    """`orient: none` says the flag is wrong: the preview is the stored picture (as the
    proxy's and the export's `-display_rotation 0`), with no tag either."""
    import server
    src = rot_bin["footage"] / f"{stem}.MP4"
    dest = tmp_path / f"{stem}.mp4"
    how = server.build_preview(src, dest, "none")
    if _gpu():
        assert how == "gpu", "a clip that needs no turn keeps the all-GPU path"
    assert _rotation(dest) == 0
    _same(_quads(dest), _stored(src), f"{stem} preview, orient none ({how})")


def test_a_clip_with_no_flag_keeps_the_all_gpu_path(rot_bin, tmp_path):
    import server
    if not _gpu():
        pytest.skip("no NVENC here")
    src = rot_bin["footage"] / "FLAT.MP4"
    dest = tmp_path / "flat.mp4"
    assert server.build_preview(src, dest, "auto") == "gpu"
    _same(_quads(dest), _upright(src), "FLAT preview")


# ------------------------------------------------------------------ what the board serves


def _assemble():
    spec = importlib.util.spec_from_file_location(
        "assemble_for_orient_test", ROOT / "research" / "tools" / "assemble.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("stem", ["ROT180", "ROT90"])
def test_monitor_poster_proxy_and_export_all_agree(board, rot_bin, tmp_path, monkeypatch,
                                                   stem):
    """`/media/proxy` before the proxy (the preview), the poster (from the original
    before any preview, then from the preview), `/media/proxy` once the 720p proxy
    exists, a poster from that proxy, and the export's own part encode: one picture."""
    c, server = board["client"], board["server"]
    src = rot_bin["footage"] / f"{stem}.MP4"
    want = _upright(src)

    # a poster before anything else exists: cut from the original
    assert not server.preview_path(stem).exists()
    p0 = _fetch(c, f"/media/poster/{stem}.jpg", tmp_path / "p0.jpg", params={"t": 1.0})
    _same(_quads(p0), want, f"{stem} poster from the original")

    r = c.get(f"/media/proxy/{stem}.mp4", follow_redirects=False)
    assert r.status_code == 307 and r.headers["x-roughcut-media"] == "preview", r.headers
    assert r.headers["location"] == server.preview_url(stem)
    assert f".{server.PICTURE_V}." in r.headers["location"]
    prev = _fetch(c, f"/media/proxy/{stem}.mp4", tmp_path / "prev.mp4")
    assert _rotation(prev) == 0
    _same(_quads(prev), want, f"{stem} /media/proxy before the proxy (preview)")

    p1 = _fetch(c, f"/media/poster/{stem}.jpg", tmp_path / "p1.jpg", params={"t": 1.5})
    _same(_quads(p1), want, f"{stem} poster from the preview")

    monkeypatch.setattr(server, "measure_colour", lambda clip, force=False: None)
    server.ensure_proxies([f"{stem}.MP4"])
    r = c.get(f"/media/proxy/{stem}.mp4", follow_redirects=False)
    assert r.status_code == 200 and "x-roughcut-media" not in r.headers
    prox = tmp_path / "proxy.mp4"
    prox.write_bytes(r.content)
    assert _rotation(prox) == 0
    _same(_quads(prox), want, f"{stem} /media/proxy after the proxy")

    p2 = _fetch(c, f"/media/poster/{stem}.jpg", tmp_path / "p2.jpg", params={"t": 2.0})
    _same(_quads(p2), want, f"{stem} poster from the proxy")

    assemble = _assemble()
    w, h = (192, 320) if stem == "ROT90" else (320, 192)
    part = tmp_path / "part.mp4"
    assemble.cut(src, 0.5, 2.0, 0.0, part, "auto",
                 {"w": w, "h": h, "fps": "30", "preset": "ultrafast", "crf": "28",
                  "audio_bitrate": "96k", "timescale": 30000})
    assert _rotation(part) == 0
    _same(_quads(part), want, f"{stem} export part")


def test_a_stale_v1_preview_and_poster_are_rebuilt_not_served(board, rot_bin, tmp_path):
    """A --work from before the fix holds an upside-down `<stem>.mp4` preview and
    posters cut from it. Neither is served: the versioned names miss, the picture is
    rebuilt upright, and the old preview URL is a 404 (a page holding it asks again)."""
    c, server = board["client"], board["server"]
    stem = "ROT180"
    src = rot_bin["footage"] / f"{stem}.MP4"
    want = _upright(src)
    # what the v1 all-GPU path made: the stored pixels, no tag
    old = server.preview_dir() / f"{stem}.mp4"
    old.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-display_rotation", "0",
                    "-i", str(src), "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", str(old)], check=True, capture_output=True)
    _same(_quads(old), _stored(src), "the stale preview really is the wrong way up")
    t = server.poster_time(f"{stem}.MP4")
    old_poster = server.STATE["posters"] / f"{stem}@{t:09.2f}.jpg"
    old_poster.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-i", str(old),
                    "-frames:v", "1", str(old_poster)], check=True, capture_output=True)

    assert server.needs_preview(f"{stem}.MP4"), "the v1 preview still counts as built"
    assert c.get(f"/media/preview/{stem}.mp4").status_code == 404
    prev = _fetch(c, f"/media/proxy/{stem}.mp4", tmp_path / "prev.mp4")
    _same(_quads(prev), want, "rebuilt preview")
    poster = _fetch(c, f"/media/poster/{stem}.jpg", tmp_path / "poster.jpg",
                    params={"t": t})
    _same(_quads(poster), want, "rebuilt poster")
    assert c.get(f"/media/preview/{stem}.mp4").status_code == 404


def test_a_rotated_original_is_served_only_where_the_browser_turns_it_right(
        rot_bin, tmp_path):
    """A browser-playable h264 original with a −180° flag: under `orient: auto` the
    browser turns it exactly as the export's autorotate does, so the original plays;
    under `orient: none` the browser would turn it anyway, so it goes through a preview
    of the stored pixels instead."""
    from fastapi.testclient import TestClient
    import server

    src = rot_bin["footage"] / "PLAY180.MP4"
    work = tmp_path / "work-auto"
    _open(server, rot_bin, work, "auto")
    with TestClient(server.app) as c:
        r = c.get("/media/proxy/PLAY180.mp4", follow_redirects=False)
        assert r.status_code == 307 and r.headers["x-roughcut-media"] == "original"
        got = _fetch(c, "/media/proxy/PLAY180.mp4", tmp_path / "orig.mp4")
        _same(_quads(got), _upright(src), "the original as a browser shows it")
        poster = _fetch(c, "/media/poster/PLAY180.jpg", tmp_path / "op.jpg")
        _same(_quads(poster), _upright(src), "the original's poster")

    work = tmp_path / "work-none"
    _open(server, rot_bin, work, "none")
    with TestClient(server.app) as c:
        r = c.get("/media/proxy/PLAY180.mp4", follow_redirects=False)
        assert r.status_code == 307 and r.headers["x-roughcut-media"] == "preview", \
            "orient none must not hand the browser a tag it would honour"
        assert c.get("/media/original/PLAY180.mp4").status_code == 404
        got = _fetch(c, "/media/proxy/PLAY180.mp4", tmp_path / "none.mp4")
        assert _rotation(got) == 0
        _same(_quads(got), _stored(src), "orient none preview")
        poster = _fetch(c, "/media/poster/PLAY180.jpg", tmp_path / "np.jpg",
                        params={"t": 2.0})
        _same(_quads(poster), _stored(src), "orient none poster")


# ------------------------------------------------------------------ in a browser


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_chromium_shows_every_served_picture_upright(rot_bin, tmp_path):
    """Real Chromium, `orient: auto`, no proxies: `<video src=/media/proxy/...>` for a
    flagged original (the browser turns it), a flagged HEVC clip (its preview) and the
    poster, screenshotted and compared with the autorotated source."""
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
    import uvicorn
    import server

    work = tmp_path / "work"
    _open(server, rot_bin, work, "auto")
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
            try:
                browser = pw.chromium.launch()
            except Exception as exc:                    # no browser binary installed
                pytest.skip(f"chromium unavailable: {exc}")
            page = browser.new_page(viewport={"width": 800, "height": 600})
            page.goto(f"http://127.0.0.1:{port}/media/status/FLAT.mp4")
            page.set_content("<body style='margin:0;background:#000'></body>")
            for stem in ("PLAY180", "ROT180", "ROT90"):
                page.evaluate("""async ([port, stem]) => {
                    document.body.innerHTML = '';
                    const v = document.createElement('video');
                    v.muted = true; v.preload = 'auto';
                    v.style.cssText = 'display:block;width:320px;height:320px;object-fit:fill';
                    v.id = 'v';
                    document.body.appendChild(v);
                    v.src = `http://127.0.0.1:${port}/media/proxy/${stem}.mp4`;
                    await new Promise((ok, bad) => {
                        const t0 = performance.now();
                        const tick = () => v.readyState >= 2 ? ok() :
                            v.error ? bad(new Error('media error ' + v.error.code)) :
                            performance.now() - t0 > 20000 ? bad(new Error('timeout')) :
                            setTimeout(tick, 20);
                        tick();
                    });
                    v.currentTime = 1.0;
                    await new Promise(ok => v.addEventListener('seeked', ok, {once: true}));
                }""", [port, stem])
                shot = tmp_path / f"{stem}-video.png"
                page.locator("#v").screenshot(path=str(shot))
                _same(_quads(shot), _upright(rot_bin["footage"] / f"{stem}.MP4"),
                      f"{stem} in a <video>")
                page.evaluate("""async ([port, stem]) => {
                    document.body.innerHTML = '';
                    const img = new Image();
                    img.id = 'p';
                    img.style.cssText = 'display:block;width:320px;height:320px;object-fit:fill';
                    img.src = `http://127.0.0.1:${port}/media/poster/${stem}.jpg?t=1.00`;
                    document.body.appendChild(img);
                    await img.decode();
                }""", [port, stem])
                shot = tmp_path / f"{stem}-poster.png"
                page.locator("#p").screenshot(path=str(shot))
                _same(_quads(shot), _upright(rot_bin["footage"] / f"{stem}.MP4"),
                      f"{stem} poster in an <img>")
            browser.close()
    finally:
        srv.should_exit = True
        thread.join(timeout=10)
    shutil.rmtree(work, ignore_errors=True)
