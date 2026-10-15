"""Range proxies: the cut's shots sharp in minutes, not the whole clips in ~37 (wave 7).

PLAYBACK measured it on Killington: the cut's 9 clips take ~37 min to get their 1280
proxies with one worker (1.53 s of encode per second of 59.94 HEVC), and the Program
shows the soft 360p preview meanwhile. Now each clip the cut plays first gets a RANGE
PROXY: a 1280 file on the clip's own timeline whose frames inside the cut's ranges
(each shot ± a pad) come from the original, and whose frames outside them are the
preview's, scaled up. `/media/proxy` 307s to it (`x-roughcut-media: range`) ahead of the
preview; the whole-clip proxy replaces it when it lands.

The fixture is a 1280×720 HEVC clip at 59.94 (MPEG-4 part 2 without libx265), so it goes
through a preview, with:
  * a 1-pixel grid in one quadrant: sharp at 1280, gone in the 640×360 preview, so the
    test can tell a range frame from a preview frame;
  * a white bar that moves 8 px a source frame: a frame one off reads wrong;
  * four different quadrants and a copy flagged −180° (Karl's GoPro flag), so a turn, a
    flip or a mirror reads wrong.
Every frame is compared against the whole-clip proxy `build_proxy` makes (the frame the
player would show at that clip time) and the original, autorotated, at the same time.

    uv run --with pytest --with fastapi --with uvicorn --with httpx --with numpy \
        --with playwright pytest app/tests/test_range_proxy.py -q
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from conftest import _sidecar

SECONDS = 8.0
RATE = "60000/1001"
OUT_RATE = 30000 / 1001
# FLAT: shots at [1.0, 1.8] and [6.012, 7.0]; ROT180: one at [3.009, 4.0] (padded starts
# between two source frames, so a range whose frames were not put back at their own times
# reads a frame off); CLIP_X is in the bin and not in the cut.
CUT = {
    "variant": "T", "title": "ranges", "orient": "auto", "story": "", "target_s": [5, 20],
    "segments": [
        {"id": "s1", "clip": "FLAT.MP4", "in": 1.0, "out": 1.8, "why": "one"},
        {"id": "s2", "clip": "ROT180.MP4", "in": 3.009, "out": 4.0, "why": "two"},
        {"id": "s3", "clip": "FLAT.MP4", "in": 6.012, "out": 7.0, "why": "three"},
    ],
}


def _x265() -> bool:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True)
    return "libx265" in r.stdout


def _make_base(dest: Path) -> None:
    """The fixture's pixels, once: every clip of the bin is a copy of them (one flagged)."""
    graph = (
        f"color=c=0x202020:s=1280x720:r={RATE}:d={SECONDS}[bg];"
        f"color=c=0x202020:s=640x360:r={RATE}:d={SECONDS},"
        f"drawgrid=w=2:h=2:t=1:c=0xE6E614[tl];"
        f"color=c=0xE61414:s=640x360:r={RATE}:d={SECONDS}[tr];"
        f"color=c=0x14E614:s=640x360:r={RATE}:d={SECONDS}[bl];"
        f"color=c=white:s=40x280:r={RATE}:d={SECONDS}[bar];"
        "[bg][tl]overlay=0:0[a];[a][tr]overlay=640:0[b];[b][bl]overlay=0:360[c];"
        "[c][bar]overlay=x='640+mod(n*8\\,560)':y=400,format=yuv420p[v]")
    vc = (["-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "log-level=error:crf=16"]
          if _x265() else ["-c:v", "mpeg4", "-q:v", "2"])
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-filter_complex", graph,
                    "-f", "lavfi", "-i", f"sine=frequency=330:duration={SECONDS}",
                    "-map", "[v]", "-map", "0:a", *vc, "-c:a", "aac", "-shortest", str(dest)],
                   check=True, capture_output=True)


def _flag(base: Path, dest: Path, rotation: int) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-display_rotation",
                    str(rotation), "-i", str(base), "-c", "copy", str(dest)],
                   check=True, capture_output=True)


def _gray(path: Path, t: float | None = None, n: int | None = None, w: int = 1280,
          h: int = 720) -> np.ndarray:
    """One frame as a gray image, the way a browser shows the file (autorotated): at
    clip time `t` (ffmpeg's accurate seek), or the n-th frame of the file."""
    if n is not None:
        pre, vf = [], f"select=eq(n\\,{n}),scale={w}:{h},format=gray"
    else:
        pre, vf = ["-ss", f"{t:.4f}"], f"scale={w}:{h},format=gray"
    raw = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", *pre, "-i", str(path),
                          "-frames:v", "1", "-vf", vf, "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w).astype(float)


def _sharpness(img: np.ndarray) -> float:
    """Mean absolute difference between neighbouring columns: the 1-px grid makes it
    large at 1280, and the preview (640×360, upscaled) all but loses it."""
    return float(np.abs(np.diff(img, axis=1)).mean())


def _small(img: np.ndarray) -> np.ndarray:
    """A 160×90 thumbnail: the bar's place, the quadrants; the grid averaged out."""
    return img.reshape(90, 8, 160, 8).mean((1, 3))


def _dist(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.abs(_small(a) - _small(b)).mean())


def _probe(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,r_frame_rate:stream_side_data=rotation"
                        ":format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def _keyframes(path: Path) -> list[float]:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-skip_frame",
                        "nokey", "-show_entries", "frame=pts_time", "-of", "csv=p=0",
                        str(path)], capture_output=True, text=True, check=True)
    return [float(x.strip(",")) for x in r.stdout.split() if x.strip(",")]


@pytest.fixture(scope="module")
def rbin(tmp_path_factory):
    import server

    root = tmp_path_factory.mktemp("rangebin")
    footage, sidecars = root / "footage", root / "sidecars"
    footage.mkdir()
    sidecars.mkdir()
    base = root / "base.mp4"
    _make_base(base)
    for stem, rot in (("FLAT", 0), ("ROT180", 180), ("CLIP_X", 0)):
        _flag(base, footage / f"{stem}.MP4", rot)
        side = {**_sidecar(stem), "duration_s": SECONDS}
        (sidecars / f"{stem}.audio.json").write_text(json.dumps(side), encoding="utf-8")
    # The whole-clip proxies the range frames must agree with, made by the real builder.
    refs = root / "refs"
    refs.mkdir()
    for stem in ("FLAT", "ROT180"):
        server.build_proxy(footage / f"{stem}.MP4", refs / f"{stem}.mp4", "auto")
    rot = footage / "ROT180.MP4"
    up = _gray(rot, t=0.5)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-display_rotation", "0",
                          "-ss", "0.5", "-i", str(rot), "-frames:v", "1", "-vf",
                          "scale=1280:720,format=gray", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    stored = np.frombuffer(raw, np.uint8).reshape(720, 1280).astype(float)
    assert _dist(up, stored) > 20, "the fixture's turn is not visible: the test would be vacuous"
    return {"root": root, "footage": footage, "sidecars": sidecars, "refs": refs}


def _open(server, rbin, work: Path, cut: dict | None = None, proxies: bool = False) -> None:
    server.MEDIA["failed"].clear()
    server.MEDIA["wanted"].clear()
    edl = work / "cut.edl.json"
    work.mkdir(parents=True, exist_ok=True)
    edl.write_text(json.dumps(cut or CUT), encoding="utf-8")
    old = time.time() - 60               # a cut saved a while ago: not being edited now
    os.utime(edl, (old, old))
    server.configure(edl, rbin["footage"], rbin["sidecars"], work, proxies=proxies,
                     orient="auto", visual=work / "no-visual")


@pytest.fixture
def board(rbin, tmp_path):
    from fastapi.testclient import TestClient
    import server

    _open(server, rbin, tmp_path / "work")
    with TestClient(server.app) as c:
        yield {"client": c, "server": server, "work": tmp_path / "work"}
    server.MEDIA["wanted"].clear()


def _gpu() -> bool:
    import server
    return server.preview_gpu()


def _build(server, stem: str) -> dict:
    rec = server.ensure_range(f"{stem}.MP4")
    assert rec is not None, f"{stem}: no range proxy was built ({server.MEDIA['failed']})"
    return rec


# ------------------------------------------------------------------ what gets built


def test_cut_ranges_follow_the_cut_and_merge_with_their_pads(board):
    server = board["server"]
    cut = server.cut_ranges()
    assert list(cut) == ["FLAT.MP4", "ROT180.MP4"], "film order, each clip once"
    assert cut["FLAT.MP4"] == [(1.0, 1.8), (6.012, 7.0)]
    assert server.merge_ranges(cut["FLAT.MP4"], SECONDS) == [(0.0, 2.8), (5.012, 8.0)]
    # shots closer than RANGE_MERGE_S are one range; the clip's ends clamp
    assert server.merge_ranges([(1.0, 2.0), (4.0, 5.0)], SECONDS) == [(0.0, 6.0)]
    assert server.merge_ranges([(7.5, 7.9)], SECONDS) == [(6.5, 8.0)]
    many = [(i * 10.0, i * 10.0 + 1) for i in range(12)]
    assert len(server.merge_ranges(many, 200.0)) == server.RANGE_MAX
    assert server.ranges_cover([(0.0, 3.0)], [(1.0, 2.0)], SECONDS)
    assert not server.ranges_cover([(0.9, 3.0)], [(1.0, 2.0)], SECONDS), "no room to park"
    assert not server.ranges_cover([(0.0, 3.0)], [(1.0, 3.5)], SECONDS)


@pytest.mark.parametrize("path", ["gpu", "cpu"])
def test_a_range_proxy_has_the_proxy_profile(board, monkeypatch, path):
    """1280 wide, the proxy's 59.94 → 29.97 rate, upright with no matrix left on it, a
    keyframe at least every second, the clip's whole length, its sound."""
    server = board["server"]
    if path == "gpu" and not _gpu():
        pytest.skip("no NVENC here")
    if path == "cpu":
        monkeypatch.setenv("ROUGHCUT_PREVIEW_GPU", "0")
        monkeypatch.setitem(server.MEDIA, "gpu", None)
    rec = _build(server, "ROT180")
    assert rec["how"] == path
    p = _probe(rec["path"])
    st = p["streams"][0]
    assert (st["width"], st["height"]) == (1280, 720)
    assert st["r_frame_rate"] == "30000/1001"
    assert not any("rotation" in sd for sd in st.get("side_data_list") or [])
    assert abs(float(p["format"]["duration"]) - SECONDS) < 0.2
    keys = _keyframes(rec["path"])
    assert keys and keys[0] < 0.05
    gaps = np.diff(keys + [SECONDS])
    assert gaps.max() <= 1.0 + 1.5 / OUT_RATE, f"keyframes {keys}"
    assert rec["spans"] == [[2.009, 5.0]]


# ------------------------------------------------------------------ frames


@pytest.mark.parametrize("path", ["gpu", "cpu"])
@pytest.mark.parametrize("stem", ["FLAT", "ROT180"])
def test_seeks_inside_a_shot_land_on_the_proxys_frame_and_are_sharp(
        board, rbin, monkeypatch, stem, path):
    """At clip times inside the shots: the range proxy's frame is the whole-clip proxy's
    frame at that time (not one either side: the bar moves 16 px a frame), upright like
    the original autorotated, and as sharp as the proxy (the grid is there). Outside the
    ranges the frame is still the right one, from the preview: soft, never wrong."""
    server = board["server"]
    if path == "gpu" and not _gpu():
        pytest.skip("no NVENC here")
    if path == "cpu":
        monkeypatch.setenv("ROUGHCUT_PREVIEW_GPU", "0")
        monkeypatch.setitem(server.MEDIA, "gpu", None)
    rec = _build(server, stem)
    ref, src = rbin["refs"] / f"{stem}.mp4", rbin["footage"] / f"{stem}.MP4"
    inside = [1.03, 1.52, 1.77, 6.1, 6.93] if stem == "FLAT" else [3.0, 3.51, 3.98]
    outside = [3.9, 4.4] if stem == "FLAT" else [0.4, 7.2]
    sharp_ref = _sharpness(_gray(ref, t=inside[0]))
    for t in inside + outside:
        got, want = _gray(rec["path"], t=t), _gray(ref, t=t)
        d0 = _dist(got, want)
        for k in (-1, 1):
            other = _dist(got, _gray(ref, t=t + k / OUT_RATE))
            assert other > d0 + 2, (f"{stem} t={t}: the frame is closer to the proxy's "
                                    f"{k:+d} frame ({other:.1f}) than to its own ({d0:.1f})")
        assert d0 < 4, f"{stem} t={t}: not the proxy's frame (diff {d0:.1f})"
        orig = _gray(src, t=t)
        assert _dist(got, orig) < 6, f"{stem} t={t}: not the original's picture"
        s = _sharpness(got)
        if t in inside:
            assert s > 0.8 * sharp_ref, f"{stem} t={t}: soft inside a shot ({s:.1f} vs {sharp_ref:.1f})"
        else:
            assert s < 0.5 * sharp_ref, f"{stem} t={t}: sharp outside the ranges?"


def test_the_whole_clip_proxy_frame_grid_matches_frame_for_frame(board, rbin):
    """Every frame of the range proxy is on the proxy's grid: frame n of one is frame n of
    the other, across a range's edges (the 59.94 → 29.97 phase included)."""
    server = board["server"]
    rec = _build(server, "FLAT")
    ref = rbin["refs"] / "FLAT.mp4"
    for n in (0, 29, 30, 31, 60, 83, 84, 85, 100, 149, 150, 151, 152, 200, 238):
        d = _dist(_gray(rec["path"], n=n), _gray(ref, n=n))
        # the bar moves 16 px a frame (~2 levels of this distance); a frame off by one
        # source frame (half a proxy frame) reads ~1.3
        assert d < 0.8, f"frame {n}: diff {d:.2f}"


# ------------------------------------------------------------------ serving


def test_the_program_is_served_the_range_proxy_and_status_says_so(board):
    c, server = board["client"], board["server"]
    r = c.get("/media/proxy/FLAT.mp4", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["x-roughcut-media"] == "preview", "before the range proxy: the floor"
    assert c.get("/media/status/FLAT.mp4").json()["cut_sharp"] is False
    rec = _build(server, "FLAT")
    r = c.get("/media/proxy/FLAT.mp4", follow_redirects=False)
    assert r.status_code == 307, r.status_code
    assert r.headers["x-roughcut-media"] == "range"
    assert r.headers["location"] == rec["url"]
    full = c.get(r.headers["location"], headers={"range": "bytes=0-99"})
    assert full.status_code == 206 and full.headers["content-type"] == "video/mp4"
    s = c.get("/media/status/FLAT.mp4").json()
    assert s["serves"] == "range" and s["ready"] and s["range"]
    assert s["cut_sharp"] is True and s["ranges"] == [[0.0, 2.8], [5.012, 8.0]]
    # a clip the cut does not play: no range proxy, and the status says nothing about one
    assert c.get("/media/status/CLIP_X.mp4").json()["cut_sharp"] is None
    assert server.ensure_range("CLIP_X.MP4") is None
    # a stale or made-up name is a 404
    assert c.get("/media/range/FLAT.000000000000.mp4").status_code == 404
    assert c.get("/media/range/..%2Fcut.edl.json").status_code == 404


def test_the_whole_clip_proxy_replaces_the_range_proxy_when_it_lands(board, rbin, monkeypatch):
    c, server = board["client"], board["server"]
    rec = _build(server, "FLAT")
    server.build_proxy(rbin["footage"] / "FLAT.MP4", server.STATE["proxy_dir"] / "FLAT.mp4",
                       "auto")
    r = c.get("/media/proxy/FLAT.mp4", follow_redirects=False)
    assert r.status_code == 200 and "x-roughcut-media" not in r.headers
    s = c.get("/media/status/FLAT.mp4").json()
    assert s["serves"] == "proxy" and s["cut_sharp"] is True
    assert not server.needs_range("FLAT.MP4"), "a clip with its proxy needs no range proxy"
    # the range file stays a while for a player that still holds it, then goes
    assert server.prune_ranges() == 0 and rec["path"].exists()
    monkeypatch.setattr(server, "RANGE_KEEP_S", -1.0)
    assert server.prune_ranges() == 1 and not rec["path"].exists()


def test_a_trim_past_the_range_falls_back_then_gets_a_new_range(board, rbin):
    """Shot 1 trimmed out to 4.2 s, past its range (0-2.8 s): the old range proxy still plays
    (the right frame, soft past 3 s), the status says the cut is not all sharp, and the
    walk's next job is a new range proxy that holds the trimmed shot sharp."""
    c, server = board["client"], board["server"]
    old = _build(server, "FLAT")
    cut = json.loads(json.dumps(CUT))
    cut["segments"][0]["out"] = 4.2
    server.STATE["edl"].write_text(json.dumps(cut), encoding="utf-8")
    s = c.get("/media/status/FLAT.mp4").json()
    assert s["serves"] == "range" and s["cut_sharp"] is False
    got = _gray(old["path"], t=3.6)
    ref = rbin["refs"] / "FLAT.mp4"
    assert _dist(got, _gray(ref, t=3.6)) < 4, "past the range: still the right frame"
    assert _sharpness(got) < 0.5 * _sharpness(_gray(ref, t=3.6))
    assert server.needs_range("FLAT.MP4")
    new = _build(server, "FLAT")
    assert new["path"] != old["path"] and new["spans"] == [[0.0, 8.0]]
    s = c.get("/media/status/FLAT.mp4").json()
    assert s["cut_sharp"] is True and c.get(
        "/media/proxy/FLAT.mp4", follow_redirects=False).headers["location"] == new["url"]
    got = _gray(new["path"], t=3.6)
    assert _sharpness(got) > 0.8 * _sharpness(_gray(ref, t=3.6))
    assert old["path"].exists(), "the one before stays for a player that holds it"


def test_turned_off_it_is_todays_preview(board, monkeypatch):
    c, server = board["client"], board["server"]
    _build(server, "FLAT")
    monkeypatch.setenv("ROUGHCUT_RANGE_PROXIES", "0")
    r = c.get("/media/proxy/FLAT.mp4", follow_redirects=False)
    assert r.headers["x-roughcut-media"] == "preview"
    assert not server.needs_range("FLAT.MP4")


# ------------------------------------------------------------------ the walk


def test_the_cuts_shots_get_range_proxies_before_whole_clip_proxies(rbin, tmp_path, monkeypatch):
    """The background walk (how `main()` runs, GPU box): for each clip the cut plays, in
    film order, its preview and then its range proxy; only then the rest of the bin's
    previews. The whole-clip proxies are not touched by it (stubbed here: they are the
    index's proxy pool, minutes behind)."""
    import server
    if not _gpu():
        pytest.skip("the background walk runs on a GPU only")
    jobs: list[tuple[str, str]] = []
    real_next = server._next_media_job

    def next_job():
        job = real_next()
        if job is not None:
            jobs.append(job)
        return job

    monkeypatch.setattr(server, "ensure_proxies", lambda clips, progress=None: None)
    monkeypatch.setattr(server, "_next_media_job", next_job)
    t0 = time.monotonic()
    _open(server, rbin, tmp_path / "work", proxies=True)
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if all(server.range_record(s) for s in ("FLAT", "ROT180")) and \
                    server.preview_path("CLIP_X").exists():
                break
            time.sleep(0.2)
        took = time.monotonic() - t0
        assert server.range_record("FLAT") and server.range_record("ROT180"), jobs
        assert jobs[:5] == [("preview", "FLAT.MP4"), ("range", "FLAT.MP4"),
                            ("preview", "ROT180.MP4"), ("range", "ROT180.MP4"),
                            ("preview", "CLIP_X.MP4")], jobs
        assert not list(Path(server.STATE["proxy_dir"]).glob("*.mp4")), "no whole proxy yet"
        print(f"walk: both cut clips sharp in {took:.1f}s")
        # the walk stays and watches the cut: a new shot of CLIP_X gets its range proxy
        cut = json.loads(json.dumps(CUT))
        cut["segments"].append({"id": "s4", "clip": "CLIP_X.MP4", "in": 2.0, "out": 3.0})
        server.STATE["edl"].write_text(json.dumps(cut), encoding="utf-8")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not server.range_record("CLIP_X"):
            time.sleep(0.2)
        assert server.range_record("CLIP_X"), jobs
    finally:
        server.STATE["proxies_enabled"] = False
        server.MEDIA["kick"].set()


# ------------------------------------------------------------------ in a browser


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_chromium_plays_the_shot_from_its_range_proxy_and_seeks_land(rbin, tmp_path):
    """Real Chromium on `/media/proxy/ROT180.mp4` (the URL the Program arms): it follows
    the 307 to the range proxy (1280 wide), and a seek inside the shot shows what the
    whole-clip proxy shows at that clip time in the same browser (upright, the same
    frame, as sharp); a seek outside the ranges shows the right frame too (the
    preview's, soft). The whole-clip proxy is served beside it under another name."""
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
    import shutil
    import uvicorn
    import server

    work = tmp_path / "work"
    _open(server, rbin, work)
    rec = server.ensure_range("ROT180.MP4")
    assert rec is not None
    shutil.copy(rbin["refs"] / "ROT180.mp4", server.STATE["proxy_dir"] / "REFPROXY.mp4")
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    grab = """([id, t]) => new Promise((res) => {
        const v = document.getElementById(id), c = document.getElementById('c');
        const done = () => requestAnimationFrame(() => requestAnimationFrame(() => {
            const g = c.getContext('2d'); g.drawImage(v, 0, 0, 1280, 720);
            const d = g.getImageData(0, 0, 1280, 720).data, W = 1280, H = 720;
            const y = new Float32Array(W * H);
            for (let i = 0; i < y.length; i++)
                y[i] = d[4*i] * 0.299 + d[4*i+1] * 0.587 + d[4*i+2] * 0.114;
            let sharp = 0;                                   // _sharpness, in the page
            for (let r = 0; r < H; r++)
                for (let x = 1; x < W; x++) sharp += Math.abs(y[r*W + x] - y[r*W + x - 1]);
            const small = new Array(160 * 90).fill(0);       // _small, in the page
            for (let r = 0; r < H; r++)
                for (let x = 0; x < W; x++) small[(r >> 3) * 160 + (x >> 3)] += y[r*W + x] / 64;
            res({small, sharp: sharp / (H * (W - 1))}); }));
        v.addEventListener('seeked', done, {once: true});
        v.currentTime = t; })"""
    try:
        for _ in range(100):
            if srv.started:
                break
            time.sleep(0.05)
        with sync_api.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            seen: list[tuple[str, int, str]] = []
            page.on("response", lambda r: seen.append(
                (r.url, r.status, r.headers.get("x-roughcut-media", ""))))
            base = f"http://127.0.0.1:{port}"
            page.goto(f"{base}/media/status/ROT180.mp4")
            page.set_content(
                f"<video id=v muted preload=auto src='{base}/media/proxy/ROT180.mp4'></video>"
                f"<video id=r muted preload=auto src='{base}/media/proxy/REFPROXY.mp4'></video>"
                "<canvas id=c width=1280 height=720></canvas>")
            page.wait_for_function("['v', 'r'].every((id) => "
                                   "document.getElementById(id).readyState >= 2)",
                                   timeout=15000)
            assert page.evaluate("document.getElementById('v').videoWidth") == 1280
            assert any(st == 307 and kind == "range" and "ROT180" in u
                       for u, st, kind in seen), seen

            def frame(vid: str, k: float) -> tuple[np.ndarray, float]:
                t = (k + 0.25) / OUT_RATE            # inside frame k's display time
                out = page.evaluate(grab, [vid, t])
                return np.array(out["small"], float).reshape(90, 160), float(out["sharp"])

            def dist(a: np.ndarray, b: np.ndarray) -> float:
                return float(np.abs(a - b).mean())

            for k, sharp in ((105, True), (95, True), (12, False), (225, False)):
                (got, s), (want, s_ref) = frame("v", k), frame("r", k)
                d0 = dist(got, want)
                for j in (-1, 1):
                    other = dist(got, frame("r", k + j)[0])
                    assert other > d0 + 2, f"frame {k}: Chromium shows frame {k + j}?"
                assert d0 < 4, f"frame {k}: diff {d0:.1f}"
                assert (s > 0.7 * s_ref) if sharp else (s < 0.5 * s_ref), (k, s, s_ref)
            # upright: the picture is the original's autorotated, as ffmpeg shows it
            assert dist(frame("v", 105)[0], _small(_gray(
                rbin["footage"] / "ROT180.MP4", t=105.25 / OUT_RATE))) < 12
            browser.close()
    finally:
        srv.should_exit = True
        th.join(timeout=5)
