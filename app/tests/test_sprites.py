"""Each clip's filmstrip sprite (redesign wave 13, lane BINLOOK2; verdict 9 punch 9: "Icon
hover-scrub is instant, from a per-clip filmstrip sprite made at import (one image, many
frames)").

The Project panel's Icon view scrubbed a clip through /media/poster stills, a request each
(1-3 s apiece on a loaded box, and at 90 % it still showed the 0 s frame). Now, on the
suite's synthetic bin (copied, so a replaced file can be faked):

  * GET /api/sprites answers at once with what is built and queues the rest: a background
    worker (never the GET) makes one JPEG per clip, SPRITE_N frames in a SPRITE_COLS-wide
    grid, its ffmpeg in a background procscope (nice 19, paused while the Program plays);
  * each cell is the clip's frame at its listed time, the same frame /media/poster gives
    there, and the times run from 0 to the clip's last frame;
  * the sprite is served immutable by its versioned name; any other name is a 404; a
    replaced clip file gets a new sprite and the old one goes;
  * a 4K original whose preview the media walk will make waits for it, and its sprite is
    made from the preview the moment that lands (`sprites_landed`, the preview build's
    hook); with no preview coming it is read from the original;
  * a Stop (`stop_media`) kills a sprite's ffmpeg like the posters' and previews', and is
    no failure: the next look asks again;
  * none are made while the board makes no background pictures (proxies off: the suite,
    --no-proxies) unless asked (`SPRITES["force"]`, as these tests do).

    uv run --with pytest --with fastapi --with uvicorn --with httpx --with numpy \\
        pytest app/tests/test_sprites.py -q
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest

STEMS = ["CLIP_A", "CLIP_B", "CLIP_C"]


@pytest.fixture
def bin_copy(project, tmp_path):
    """The session's three clips and sidecars, copied (this module touches a file's mtime),
    with sprites made although the suite runs with proxies off; after the test the worker
    is idle and the switch back, so no sprite ffmpeg runs into another module."""
    import server

    footage, sidecars = tmp_path / "footage", tmp_path / "sidecars"
    shutil.copytree(project["footage"], footage)
    shutil.copytree(project["sidecars"], sidecars)
    edl = tmp_path / "edl.json"
    edl.write_text(project["edl"].read_text(encoding="utf-8"), encoding="utf-8")
    server.SPRITES["force"] = True
    yield {"footage": footage, "sidecars": sidecars, "edl": edl, "work": tmp_path / "work",
           "assets": project["assets"]}
    server.SPRITES["force"] = None
    _idle(server)


def _client(server, b, proxies=True):
    from fastapi.testclient import TestClient

    server.configure(b["edl"], b["footage"], b["sidecars"], b["work"], proxies=False,
                     visual=b["work"] / "no-visual", assets=b["assets"])
    if proxies:
        server.ensure_proxies([f"{s}.MP4" for s in STEMS])
    with server.SPRITES["lock"]:
        server.SPRITES["mem"].clear()
        server.SPRITES["waiting"].clear()
        server.SPRITES["failed"].clear()
    return TestClient(server.app)


def _idle(server, timeout=60.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        with server.SPRITES["lock"]:
            busy = server.SPRITES["pending"] or (server.SPRITES["thread"] is not None
                                                 and server.SPRITES["thread"].is_alive())
        if not busy:
            return
        time.sleep(0.05)
    raise AssertionError("the sprite worker did not finish")


def _all_built(c, server, n=3, timeout=90.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        body = c.get("/api/sprites").json()
        if len(body["sprites"]) == n:
            return body
        time.sleep(0.2)
    raise AssertionError(f"sprites not built: {body}")


def _rgb(data: bytes, w: int, h: int, scale: tuple[int, int] | None = None) -> np.ndarray:
    """A JPEG's pixels as RGB (optionally scaled), through ffmpeg."""
    vf = f"scale={scale[0]}:{scale[1]}," if scale else ""
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-f", "image2pipe", "-i", "-",
                        "-vf", f"{vf}format=rgb24", "-f", "rawvideo", "-"],
                       input=data, capture_output=True, check=True)
    ow, oh = scale or (w, h)
    return np.frombuffer(r.stdout, np.uint8)[: ow * oh * 3].reshape(oh, ow, 3).astype(np.int16)


def test_the_get_answers_at_once_and_a_niced_worker_makes_one_sprite_per_clip(bin_copy, monkeypatch):
    import server
    from roughcut import procscope

    c = _client(server, bin_copy)
    real_frame, seen, built = server._sprite_frame, [], []

    def frame(src, t, original):
        sc = procscope.current()
        seen.append((sc is not None and sc.background, procscope._wrap(["ffmpeg"], sc)[:3] if sc else None))
        return real_frame(src, t, original)

    real_build = server.build_sprite

    def slow_build(clip):
        time.sleep(0.4)                      # a busy box: the GET must not wait on this
        built.append(clip)
        return real_build(clip)

    monkeypatch.setattr(server, "_sprite_frame", frame)
    monkeypatch.setattr(server, "build_sprite", slow_build)
    t0 = time.monotonic()
    first = c.get("/api/sprites")
    took = time.monotonic() - t0
    assert first.status_code == 200 and "no-store" in first.headers["cache-control"]
    assert first.json()["sprites"] == {} and first.json()["pending"] == 3, first.json()
    assert took < 0.3, f"GET /api/sprites took {took:.2f} s"
    body = _all_built(c, server)
    _idle(server)
    assert sorted(built) == [f"{s}.MP4" for s in STEMS]
    # every ffmpeg the worker ran was in a background scope: nice 19 (and paused while
    # the Program plays, procscope's background rule)
    assert seen and all(bg for bg, _ in seen), seen
    assert all(w is None or (w[0].endswith("nice") and w[1:] == ["-n", "19"]) for _, w in seen), seen
    for stem in STEMS:
        sp = body["sprites"][stem]
        # 240 wide, the clip's shape (320×180 → 240×135, even: 136)
        assert (sp["n"], sp["cols"], sp["rows"], sp["w"], sp["h"]) == (20, 5, 4, 240, 136), sp
        assert sp["from"] == "proxy", sp
        # the scrub's own mapping: 0 to the last frame, evenly
        assert sp["times"][0] == 0.0 and sp["times"][-1] == pytest.approx(6.0 - 1 / 24, abs=0.002), sp["times"]
        assert all(b > a for a, b in zip(sp["times"], sp["times"][1:]))
        r = c.get(sp["url"])
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        assert "immutable" in r.headers["cache-control"]
        assert r.content[:3] == b"\xff\xd8\xff"
        assert _rgb(r.content, 1200, 544).shape == (544, 1200, 3)
    # asked again: listed from memory, nothing made twice
    again = c.get("/api/sprites").json()
    assert again["pending"] == 0 and set(again["sprites"]) == set(STEMS)
    _idle(server)
    assert len(built) == 3


def test_each_cell_is_the_clips_frame_at_its_time(bin_copy):
    import server

    c = _client(server, bin_copy)
    sp = _all_built(c, server)["sprites"]["CLIP_A"]
    w, h = sp["w"], sp["h"]
    grid = _rgb(c.get(sp["url"]).content, w * 5, h * 4)
    cell = lambda k: grid[(k // 5) * h:(k // 5 + 1) * h, (k % 5) * w:(k % 5 + 1) * w]  # noqa: E731
    still = lambda t: _rgb(c.get(f"/media/poster/CLIP_A.v2.jpg?t={t:.4f}").content, 320, 180,  # noqa: E731
                           scale=(w, h))
    # a still at the cell's own time (the poster route keeps 4 places since wave 17 PLAY8, and
    # both take the frame on screen at t: the one the Program and the export show)
    diffs = []
    for k in (0, 5, 9, 14, 19):
        t = sp["times"][k]
        same = float(np.abs(cell(k) - still(t)).mean())
        other = float(np.abs(cell(k) - still(sp["times"][(k + 10) % 20])).mean())
        diffs.append((k, round(same, 1), round(other, 1)))
        assert same < 5.0 and other > 4.0 * same, diffs   # a frame off reads 7-10
    # and no two of them are the same picture (the scrub moves through the clip)
    print("cell vs its still / vs a still 3 s away:", diffs)


def test_a_sprite_is_served_only_by_its_own_name_and_follows_a_replaced_file(bin_copy):
    import server

    c = _client(server, bin_copy)
    sp = _all_built(c, server)["sprites"]["CLIP_B"]
    name = sp["url"].rsplit("/", 1)[1]
    for bad in ("CLIP_B.jpg", name.replace(".v2.", ".v1."), "CLIP_B.1-2.v2.jpg", "..%2Fedl.json",
                name.replace(".jpg", ".json")):
        assert c.get(f"/media/sprite/{bad}").status_code == 404, bad
    # the clip's file replaced (a new mtime): a new sprite, and the old one goes
    clip = bin_copy["footage"] / "CLIP_B.MP4"
    st = clip.stat()
    os.utime(clip, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert "CLIP_B" not in c.get("/api/sprites").json()["sprites"]
    new = _all_built(c, server)["sprites"]["CLIP_B"]
    assert new["url"] != sp["url"]
    _idle(server)
    left = sorted(p.name for p in server.sprite_dir().glob("CLIP_B.*"))
    assert left == sorted([new["url"].rsplit("/", 1)[1], new["url"].rsplit("/", 1)[1].replace(".jpg", ".json")]), left
    assert c.get(sp["url"]).status_code == 404


def test_an_original_waits_for_the_preview_the_walk_will_make(bin_copy, monkeypatch):
    import server

    c = _client(server, bin_copy, proxies=False)          # no proxies: the originals only
    coming = {"on": True}
    monkeypatch.setattr(server, "browser_playable", lambda src: False)     # a 4K HEVC original
    real_state = server.media_state
    monkeypatch.setattr(server, "media_state",
                        lambda clip: "queued" if coming["on"] and not server.preview_path(Path(clip).stem).exists()
                        else real_state(clip))
    assert c.get("/api/sprites").json()["pending"] == 3
    _idle(server)
    body = c.get("/api/sprites").json()
    assert body["sprites"] == {} and body["waiting"] == 3 and body["pending"] == 0, body
    # CLIP_B's preview lands (the preview build's hook): its sprite, from the preview
    prev = server.preview_path("CLIP_B")
    prev.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(bin_copy["footage"] / "CLIP_B.MP4", prev)
    server.sprites_landed(prev)
    _idle(server)
    body = c.get("/api/sprites").json()
    assert list(body["sprites"]) == ["CLIP_B"] and body["sprites"]["CLIP_B"]["from"] == "preview", body
    assert body["waiting"] == 2
    # no preview coming any more (the walk stopped, the build failed): the original itself
    coming["on"] = False
    assert c.get("/api/sprites").json()["pending"] == 2
    body = _all_built(c, server)
    assert {body["sprites"][s]["from"] for s in ("CLIP_A", "CLIP_C")} == {"original"}
    _idle(server)


def test_the_preview_build_queues_the_sprite(bin_copy, monkeypatch):
    """The one call in the preview queue (`_build_claimed`): a preview built is a sprite
    asked for, so the Icon view has it before anybody hovers."""
    import server

    _client(server, bin_copy, proxies=False)
    asked = []
    monkeypatch.setattr(server, "queue_sprites", lambda stems, front=False: asked.extend(stems))
    monkeypatch.setattr(server, "build_preview", lambda src, dest, orient: (shutil.copy(src, dest), "cpu")[1])
    dest = server.preview_path("CLIP_C")
    dest.parent.mkdir(parents=True, exist_ok=True)
    ev, owner = server._claim_preview(str(dest))
    assert owner
    server._build_claimed(server.clip_path("CLIP_C.MP4"), dest, "none", ev)
    assert dest.exists() and asked == ["CLIP_C"], asked


def test_a_stop_kills_a_sprites_ffmpeg_and_the_next_look_asks_again(bin_copy, monkeypatch):
    import server

    c = _client(server, bin_copy)
    started = []

    def slow_frame(src, t, original):          # a frame that takes a minute: the Stop's
        started.append(t)
        r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-re", "-f", "lavfi", "-i",
                            "testsrc2=size=160x90:rate=10", "-t", "60", "-f", "null", "-"],
                           capture_output=True)
        return b"" if r.returncode else b"x"

    monkeypatch.setattr(server, "_sprite_frame", slow_frame)
    assert c.get("/api/sprites").json()["pending"] == 3
    t0 = time.monotonic()
    while not _ffmpeg_kids() and time.monotonic() - t0 < 10:
        time.sleep(0.05)
    assert _ffmpeg_kids(), "the sprite's ffmpeg never started"
    assert server.stop_media() >= 1
    _idle(server, timeout=10)
    t0 = time.monotonic()
    while _ffmpeg_kids() and time.monotonic() - t0 < 3:
        time.sleep(0.05)
    assert not _ffmpeg_kids()
    assert not server.SPRITES["failed"] and not server.SPRITES["pending"]      # stopped, not broken
    monkeypatch.undo()
    assert c.get("/api/sprites").json()["pending"] == 3                        # asked again
    _all_built(c, server)


def _ffmpeg_kids() -> list[str]:
    """This process's ffmpeg children (named by their command line: `nice` and `setpriv`
    exec into the ffmpeg procscope puts behind them)."""
    r = subprocess.run(["pgrep", "-a", "-P", str(os.getpid())], capture_output=True, text=True)
    return [ln for ln in r.stdout.splitlines() if "ffmpeg" in ln]


def test_none_are_made_while_the_board_makes_no_background_pictures(bin_copy):
    import server

    c = _client(server, bin_copy)
    server.SPRITES["force"] = None                     # the suite's own: proxies off
    body = c.get("/api/sprites").json()
    assert body == {"sprites": {}, "pending": 0, "waiting": 0, "n": 20, "off": True}, body
    server.sprites_landed(server.preview_path("CLIP_A"))
    with server.SPRITES["lock"]:
        assert not server.SPRITES["pending"] and server.SPRITES["thread"] is None
    # proxies on (the board's default): made
    server.STATE["proxies_enabled"] = True
    try:
        assert c.get("/api/sprites").json()["pending"] == 3
        _all_built(c, server)
    finally:
        server.STATE["proxies_enabled"] = False
        _idle(server)
