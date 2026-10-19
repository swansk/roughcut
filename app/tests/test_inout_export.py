"""An In/Out export is the range, on its own clock (wave 16, lane INOUT; REDESIGN-GOAL
dimensions 5 and 8).

Premiere's Export with Source Range: Sequence In/Out is the film between the two marks,
every track of it. The Export dialog sends only the range's V1 shots (export.js
`clipRange`), and the render took everything else from the EDL on the sequence's clock:

  * V2+ (`overlays`) and A3+ (`audio`) keep their `at` on the WHOLE sequence's clock, so
    an In/Out range that does not start at 0 put a title or a sound late by the In (or
    past the film's end, gone). `server.export_layers` moves them onto the range's own
    clock (`range_start`, MARKERS'), cuts those that straddle an edge (the head's source
    and its keys move with the cut) and leaves out those outside it;
  * a shot the In cuts into carries its keys moved earlier (export.js `shiftKeys`), so a
    key before the In was a negative time and the export a 400 ("t must be 0.."): the
    keys before 0 are one key at 0 now, with the value the shot had there;
  * the In and Out were kept to 0.01 s (timeline.js `onFrame`): at 59.94 an In at a
    shot's first frame (1.63497 s) was 1.63, the shot before's last frame, and an In
    inside a shot whose 0.01 s rounds up started the export a frame late. They are on the
    edit grid every cut uses now (`tl.onGrid`), and so is the EDL's `in` / `out`.

The renders are real (assemble.py through `/api/render`, the Match Sequence preset at the
clips' own 320×180) and are read back with ffprobe / a frame sample. ROUGHCUT_GPU=0: the
CPU path, never the GPU a playback lane is measuring. No model is called.
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from conftest import _sidecar  # noqa: E402

CLIP_S = 10.0
NTSC60 = "60000/1001"
F60 = 60000 / 1001


def _clip(path: Path, video: str, audio: str, rate: str = "24") -> None:
    """`video`: a colour, then any filters after the source (`white,drawbox=…`)."""
    colour, _, post = video.partition(",")
    src = f"color=c={colour}:size=320x180:rate={rate}:duration={CLIP_S}" + (f",{post}" if post else "")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i", src,
         "-f", "lavfi", "-i", audio,
         "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-t", f"{CLIP_S}", str(path)],
        check=True, capture_output=True)


SILENT = f"anullsrc=r=48000:cl=stereo,atrim=0:{CLIP_S}"
# 880 Hz from 0.5 s on: silence before it, so a clip cut at its head is heard from where
# the cut puts it, never from its own first second
LATE_TONE = f"sine=frequency=880:duration={CLIP_S},volume=enable='lt(t,0.5)':volume=0"


@pytest.fixture(scope="module", autouse=True)
def cpu_only():
    mp = pytest.MonkeyPatch()
    mp.setenv("ROUGHCUT_GPU", "0")
    yield
    mp.undo()


@pytest.fixture(scope="module")
def proj(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("inout")
    footage, sidecars, work, assets = (root / d for d in ("footage", "sidecars", "work", "assets"))
    for d in (footage, sidecars, work, assets / "music"):
        d.mkdir(parents=True)
    clips = {
        # V1 at 24: red, green, blue, all silent
        "CLIP_R": ("red", SILENT, "24"),
        "CLIP_G": ("0x00c000", SILENT, "24"),
        "CLIP_B": ("blue", SILENT, "24"),
        # V2's: white for its first second, black after it
        "CLIP_W": ("white,drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='gte(t,1)'",
                   SILENT, "24"),
        # A3's: grey, its sound 880 Hz from 0.5 s
        "CLIP_T": ("gray", LATE_TONE, "24"),
        # a 59.94 sequence: red, then a clip whose every frame is its own grey
        # (16 + 7·(N mod 32)), so a sampled frame says which frame it is
        "CLIP_R60": ("red", SILENT, NTSC60),
        "CLIP_N60": ("black,format=yuv420p,geq=lum='16+mod(N\\,32)*7':cb=128:cr=128",
                     SILENT, NTSC60),
    }
    for stem, (video, audio, rate) in clips.items():
        _clip(footage / f"{stem}.MP4", video, audio, rate)
        side = _sidecar(stem)
        side["duration_s"] = CLIP_S
        (sidecars / f"{stem}.audio.json").write_text(json.dumps(side), encoding="utf-8")
    edl = root / "edl.json"
    edl.write_text(json.dumps(EDL24), encoding="utf-8")
    return {"root": root, "footage": footage, "sidecars": sidecars, "work": work,
            "edl": edl, "assets": assets, "stems": list(clips)}


A3_TRACK = {"A3": {"mute": False, "solo": False, "lock": False, "target": False, "gain_db": 0.0}}

# 0-3 red, 3-6 green, 6-9 blue; In 2.0 / Out 7.0 is a 5 s film: red 0-1, green 1-4, blue 4-5
EDL24 = {
    "variant": "T", "title": "inout", "orient": "none", "story": "", "target_s": [1, 30],
    "fps": 24.0,
    "segments": [{"id": "s1", "clip": "CLIP_R.MP4", "in": 0.0, "out": 3.0, "why": "red"},
                 {"id": "s2", "clip": "CLIP_G.MP4", "in": 0.0, "out": 3.0, "why": "green"},
                 {"id": "s3", "clip": "CLIP_B.MP4", "in": 0.0, "out": 3.0, "why": "blue"}],
    "tracks": A3_TRACK,
    "overlays": [
        # the title: 4-5 on the sequence, inside the range → 2-3 of the film
        {"id": "o1", "clip": "CLIP_W.MP4", "in": 0.0, "out": 1.0, "at": 4.0},
        # straddles the In: 1-3 on the sequence; its white second is before the In, so
        # the film's 0-1 is its black second
        {"id": "o2", "clip": "CLIP_W.MP4", "in": 0.0, "out": 2.0, "at": 1.0},
        # straddles the Out: 6.5-7.5 → 4.5-5.0 of the film, cut at its end
        {"id": "o3", "clip": "CLIP_W.MP4", "in": 0.0, "out": 1.0, "at": 6.5},
        # wholly before the In and wholly after the Out: not in the film
        {"id": "o4", "clip": "CLIP_W.MP4", "in": 0.0, "out": 0.5, "at": 0.25},
        {"id": "o5", "clip": "CLIP_W.MP4", "in": 0.0, "out": 1.0, "at": 7.5},
    ],
    "audio": [
        # a sound starting inside the range: 3.5-4.5 on the sequence → 1.5-2.5 of the film
        {"id": "x1", "clip": "CLIP_T.MP4", "in": 1.0, "out": 2.0, "at": 3.5, "track": "A3"},
        # straddles the In: 1.5-2.5; its silent half-second is before the In, so the film
        # opens on its tone
        {"id": "x2", "clip": "CLIP_T.MP4", "in": 0.0, "out": 1.0, "at": 1.5, "track": "A3"},
    ],
}
# what export.js `clipRange(sequence, 2.0, 7.0)` sends
SENT24 = [{**EDL24["segments"][0], "in": 2.0}, dict(EDL24["segments"][1]),
          {**EDL24["segments"][2], "out": 1.0}]


# ---------------------------------------------------------------- reading the film

def _rgb(path: Path, t: float) -> tuple[float, float, float]:
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{t:.3f}", "-i", str(path),
                        "-frames:v", "1", "-vf", "scale=32:18", "-f", "rawvideo",
                        "-pix_fmt", "rgb24", "-"], capture_output=True)
    assert r.returncode == 0 and len(r.stdout) == 32 * 18 * 3, r.stderr[-300:]
    px = r.stdout
    n = len(px) // 3
    return tuple(sum(px[c::3]) / n for c in range(3))


def _colour(path: Path, t: float) -> str:
    r, g, b = _rgb(path, t)
    if min(r, g, b) > 200:
        return "white"
    if max(r, g, b) < 40:
        return "black"
    for name, hi, lo1, lo2 in (("red", r, g, b), ("green", g, r, b), ("blue", b, r, g)):
        if hi > 120 and lo1 < 70 and lo2 < 70:
            return name
    return f"other{(round(r), round(g), round(b))}"


def _peak_db(path: Path, start: float, length: float) -> float:
    r = subprocess.run(["ffmpeg", "-v", "info", "-nostdin", "-ss", f"{start:.3f}", "-i", str(path),
                        "-t", f"{length:.3f}", "-map", "0:a:0", "-af", "volumedetect",
                        "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"max_volume: (-?[\d.]+|-inf) dB", r.stderr)
    assert m, r.stderr[-400:]
    return -999.0 if m.group(1) == "-inf" else float(m.group(1))


def _video(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                        "-show_entries", "stream=r_frame_rate,nb_read_frames", "-of", "json",
                        str(path)], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)["streams"][0]


def _grey(path: Path, t: float | None = None, frame: int | None = None) -> float:
    """The mean grey of one frame: at `t`, or the `frame`-th decoded."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if t is not None:
        cmd += ["-ss", f"{t:.4f}"]
    cmd += ["-i", str(path)]
    if frame is not None:
        cmd += ["-vf", f"select=eq(n\\,{frame}),scale=32:18", "-fps_mode", "passthrough"]
    else:
        cmd += ["-vf", "scale=32:18"]
    cmd += ["-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    r = subprocess.run(cmd, capture_output=True)
    assert r.returncode == 0 and len(r.stdout) == 32 * 18, r.stderr[-300:]
    return sum(r.stdout) / len(r.stdout)


def _which_frame(film: Path, src: Path, near: int) -> int:
    """Which frame of `src` (CLIP_N60, every frame its own grey) the film's first frame is."""
    g = _grey(film, frame=0)
    cands = {k: _grey(src, frame=k) for k in range(max(0, near - 3), near + 4)}
    return min(cands, key=lambda k: abs(cands[k] - g))


# ---------------------------------------------------------------- the clock (no render)

def test_v2_and_a3_move_onto_the_ranges_own_clock():
    import server
    whole = [dict(s) for s in EDL24["segments"]]
    sent = [dict(s) for s in SENT24]
    assert server.range_start(whole, sent) == 2.0
    got = {o["id"]: o for o in server.export_layers(EDL24["overlays"], whole, sent)}
    assert sorted(got) == ["o1", "o2", "o3"]                       # o4, o5: outside it
    assert {k: got["o1"][k] for k in ("in", "out", "at")} == {"in": 0.0, "out": 1.0, "at": 2.0}
    # the In cuts o2's first second off: its source starts a second in, at the film's 0
    assert {k: got["o2"][k] for k in ("in", "out", "at")} == {"in": 1.0, "out": 2.0, "at": 0.0}
    # the Out cuts o3's last half-second off
    assert {k: got["o3"][k] for k in ("in", "out", "at")} == {"in": 0.0, "out": 0.5, "at": 4.5}
    aud = {c["id"]: c for c in server.export_layers(EDL24["audio"], whole, sent)}
    assert {k: aud["x1"][k] for k in ("in", "out", "at", "track")} == {
        "in": 1.0, "out": 2.0, "at": 1.5, "track": "A3"}
    assert {k: aud["x2"][k] for k in ("in", "out", "at")} == {"in": 0.5, "out": 1.0, "at": 0.0}
    # the whole sequence is its own clock: nothing moves
    assert server.export_layers(EDL24["overlays"], whole, [dict(s) for s in whole]) == EDL24["overlays"]
    # a range that cannot be placed leaves them where they were (a whole-sequence render
    # sent before an edit reached the disk), never at a guessed time
    assert server.export_layers(EDL24["audio"], whole, [{"id": "zz", "clip": "X", "in": 0, "out": 1}]) == EDL24["audio"]


def test_a_retimed_overlay_cut_at_the_in_moves_its_source_by_its_speed_and_its_keys_with_it():
    import server
    whole = [dict(s) for s in EDL24["segments"]]
    # at 2×: 1.0-3.0 of the sequence is 0-4 of its clip; keys from its first film frame
    o = {"id": "k1", "clip": "CLIP_W.MP4", "in": 0.0, "out": 4.0, "at": 1.0, "speed": 2.0,
         "controls": {"opacity": 100, "keys": {"opacity": [[0.0, 0.0], [2.0, 100.0]],
                                               "scale": [[0.25, 50.0], [0.5, 80.0]]}}}
    (got,) = server.export_layers([o], whole, [dict(s) for s in SENT24])
    assert {k: got[k] for k in ("in", "out", "at", "speed")} == {"in": 2.0, "out": 4.0, "at": 0.0, "speed": 2.0}
    # a second cut off its head: the opacity ramp is halfway there at the film's 0; every
    # scale key was before the In, so it holds its last value
    assert got["controls"]["keys"] == {"opacity": [[0.0, 50.0], [1.0, 100.0]], "scale": [[0.0, 80.0]]}
    assert o["controls"]["keys"]["opacity"] == [[0.0, 0.0], [2.0, 100.0]]   # the EDL's own untouched


# ---------------------------------------------------------------- the export

@pytest.fixture
def api(proj):
    from fastapi.testclient import TestClient
    import server
    server.configure(proj["edl"], proj["footage"], proj["sidecars"], proj["work"],
                     proxies=False, visual=proj["work"] / "no-visual", assets=proj["assets"])
    with TestClient(server.app) as c:
        yield c


def _export(api, sent: list[dict], name: str, timeout: float = 240.0) -> Path:
    r = api.post("/api/render", json={"segments": sent, "preset": "match", "name": name,
                                      "folder": "inout-tests"})
    assert r.status_code == 200, r.text
    job = r.json()["job"]
    end = time.time() + timeout
    while time.time() < end:
        st = api.get(f"/api/render/{job}").json()
        if st["state"] != "running":
            break
        time.sleep(0.3)
    assert st["state"] == "done", st
    return Path(r.json()["path"])


def test_an_in_out_export_has_its_v2_title_and_a3_sound_where_the_range_has_them(api, proj):
    """Fixture sequence 0-9 s; In 2.0 / Out 7.0. The film is 5 s: red 0-1, green 1-4,
    blue 4-5; V2's title (sequence 4-5) at 2-3, V2 cut at the In (its black second) at
    0-1 and cut at the Out (white) at 4.5-5; A3's sound (sequence 3.5-4.5) at 1.5-2.5 and
    the one cut at the In (its tone, its silent head before the In) at 0-0.5."""
    proj["edl"].write_text(json.dumps(EDL24), encoding="utf-8")
    film = _export(api, [dict(s) for s in SENT24], "in out tracks")
    v = _video(film)
    assert v["r_frame_rate"] == "24/1" and int(v["nb_read_frames"]) == 120, v      # 5.00 s
    seen = {t: _colour(film, t) for t in (0.5, 1.5, 2.5, 3.5, 4.2, 4.75)}
    assert seen == {0.5: "black",     # V2 o2's second second (its first is before the In)
                    1.5: "green",     # V1 (the sequence clock had o2 here, white)
                    2.5: "white",     # the title (the sequence clock had it at 4-5)
                    3.5: "green",
                    4.2: "blue",      # V1 (the sequence clock had the title here)
                    4.75: "white"}, seen   # o3, cut at the Out (its 6.5 was past the film)
    peaks = {w: _peak_db(film, *w) for w in ((0.05, 0.35), (0.65, 0.7), (1.6, 0.3), (2.0, 0.4),
                                            (2.65, 0.7), (3.6, 0.8))}
    assert peaks[(0.05, 0.35)] > -30, peaks      # x2 cut at the In: its tone, from 0
    assert peaks[(0.65, 0.7)] < -60, peaks
    assert peaks[(1.6, 0.3)] > -30, peaks        # x1, at 1.5 (the sequence clock: 3.5)
    assert peaks[(2.0, 0.4)] > -30, peaks        # (the tone is -21 dBFS)
    assert peaks[(2.65, 0.7)] < -60, peaks
    assert peaks[(3.6, 0.8)] < -60, peaks        # nothing here (the sequence clock had x1)


def test_an_in_inside_a_keyed_shot_exports_with_the_value_it_had_there(api, proj):
    """The In cuts 2 s into shot 1, keyed opacity 0 → 100 over its first 4 s: export.js
    sends its keys 2 s earlier, [[-2, 0], [2, 100]]. That was a 400 ("t must be 0.."); it
    is a key at 0 now with the value at the In — 50 % — and the film opens half red."""
    import server
    edl = json.loads(json.dumps(EDL24))
    edl["segments"][0]["out"] = 6.0
    edl["segments"][0]["controls"] = {"keys": {"opacity": [[0.0, 0.0], [4.0, 100.0]]}}
    edl.pop("overlays")
    edl.pop("audio")
    proj["edl"].write_text(json.dumps(edl), encoding="utf-8")
    seen: dict = {}
    real = server._render_job

    def capture(job, edl_path, out_path, meta):
        seen["edl"] = json.loads(Path(edl_path).read_text(encoding="utf-8"))
        real(job, edl_path, out_path, meta)

    server._render_job = capture
    try:
        sent = [{**edl["segments"][0], "in": 2.0,
                 "controls": {"keys": {"opacity": [[-2.0, 0.0], [2.0, 100.0]]}}},
                {**edl["segments"][1], "out": 1.0}]
        film = _export(api, sent, "in out keys")
    finally:
        server._render_job = real
    assert seen["edl"]["segments"][0]["controls"]["keys"] == {"opacity": [[0.0, 50.0], [2.0, 100.0]]}
    r, g, b = _rgb(film, 0.0)
    assert 90 < r < 170 and g < 30 and b < 30, (r, g, b)           # half red over black
    assert _colour(film, 2.5) == "red"                              # the ramp's end: all of it


# ---------------------------------------------------------------- the board at 59.94

pw_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")

# 59.94: red for 98 frames (1.635 s, frame 98 starts at 1.63497 s), then CLIP_N60 from its
# frame 0 for 240 frames
EDL60 = {
    "variant": "T", "title": "inout 59.94", "orient": "none", "story": "", "target_s": [1, 30],
    "fps": 59.94,
    "segments": [{"id": "r1", "clip": "CLIP_R60.MP4", "in": 0.0, "out": 1.635, "why": "red"},
                 {"id": "n1", "clip": "CLIP_N60.MP4", "in": 0.0, "out": 4.004, "why": "frames"}],
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(proj):
    import uvicorn
    import server
    server.configure(proj["edl"], proj["footage"], proj["sidecars"], proj["work"],
                     proxies=False, visual=proj["work"] / "no-visual", assets=proj["assets"])
    server.ensure_proxies(["CLIP_R60.MP4", "CLIP_N60.MP4"])
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started, "server did not start"
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="module")
def browser(live_server):
    with pw_api.sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser, live_server, proj):
    import server
    server.configure(proj["edl"], proj["footage"], proj["sidecars"], proj["work"],
                     proxies=False, visual=proj["work"] / "no-visual", assets=proj["assets"])
    proj["edl"].write_text(json.dumps(EDL60, indent=1), encoding="utf-8")
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(live_server)
    pg.wait_for_selector("#tl .blk")
    pg.wait_for_selector("#tlKeys", state="attached")
    pg.evaluate("localStorage.clear(); tl.setSeqIn(null); tl.setSeqOut(null); tl.clearHistory()")
    pg.click("#tl .tl-ruler", position={"x": 5, "y": 5})     # the timeline is the panel in focus
    yield pg
    ctx.close()
    assert errors == [], errors


def _disk(proj) -> dict:
    return json.loads(proj["edl"].read_text(encoding="utf-8"))


def _mark(page, edge: str, frame: int) -> float:
    """The playhead on sequence frame `frame` (its own start, as a step of → puts it), then
    the I or O key."""
    page.evaluate(f"tl.seek({frame} * 1001 / 60000)")
    page.keyboard.press(edge)
    return page.evaluate("tl.state.seqIn" if edge == "i" else "tl.state.seqOut")


def test_i_and_o_at_59_94_land_on_the_edit_grid_and_the_edl_keeps_them_there(page, proj):
    """Frame 98 (the second shot's first) starts at 1.63497 s: to 0.01 s the In was 1.63,
    inside frame 97: the red shot's last, the shot the In was in."""
    assert page.evaluate("tl.fps()") == pytest.approx(59.94)
    assert page.evaluate("tl.total()") == pytest.approx(5.639)
    at_cut = _mark(page, "i", 98)
    assert at_cut == 1.635 == page.evaluate("tl.onGrid(1.63497)"), at_cut
    out = _mark(page, "o", 158)
    assert out == page.evaluate("tl.onGrid(158 * 1001 / 60000)") == 2.636, out
    assert page.evaluate("[tl.isOnGrid(tl.state.seqIn), tl.isOnGrid(tl.state.seqOut)]") == [True, True]
    # the In is the second shot's, not the red one's before it
    assert page.evaluate("tl.shotAt(tl.state.seqIn).id") == "n1"
    # mid-shot: frame 100 is 1.66833 s — 1.67 to 0.01 s, a frame's fifth late
    mid = _mark(page, "i", 100)
    assert mid == 1.6683 and page.evaluate(f"tl.isOnGrid({mid})"), mid
    # tl.setSeqIn / setSeqOut themselves are on the grid, whoever calls them
    assert page.evaluate("tl.setSeqOut(2.6359667)") == 2.636
    assert page.evaluate("tl.setSeqIn(1.6683333)") == 1.6683
    page.evaluate("async () => { await roughcutFlush(); await tl.saveSeq(); }")
    end = time.time() + 8
    while time.time() < end and (_disk(proj).get("in"), _disk(proj).get("out")) != (1.6683, 2.636):
        time.sleep(0.1)
    assert (_disk(proj).get("in"), _disk(proj).get("out")) == (1.6683, 2.636), _disk(proj)


def _export_dialog(page, name: str) -> str:
    page.keyboard.press("Control+m")
    page.wait_for_selector("[data-testid=export-dialog] [data-testid=export-go]", timeout=5000)
    assert page.input_value("#exRange") == "inout"
    page.select_option("[data-testid=export-preset]", "match")
    page.fill("[data-testid=export-name]", name)
    page.fill("#exFolder", "inout-tests")
    page.click("[data-testid=export-go]")
    page.wait_for_selector("[data-testid=export-done]", timeout=240000)
    path = page.get_attribute("[data-testid=export-done]", "data-path")
    page.keyboard.press("Escape")
    return path


def test_an_in_out_export_at_59_94_starts_on_the_ins_own_frame(page, proj):
    """I at the second shot's first frame and O 60 frames on: the film is that shot's frames
    0-59. I two frames into it (1.66833 s, 1.67 to 0.01 s): the film starts on its frame 2,
    never 3 — export.js cut the shot at 0.035 s, after frame 2's 0.03337 s."""
    src = proj["footage"] / "CLIP_N60.MP4"
    _mark(page, "i", 98)
    _mark(page, "o", 158)
    film = Path(_export_dialog(page, "in out 59.94 cut"))
    v = _video(film)
    assert v["r_frame_rate"] == NTSC60 and int(v["nb_read_frames"]) == 60, v
    assert _colour(film, 0.0).startswith("other") or _colour(film, 0.0) == "black"   # not red
    assert _which_frame(film, src, 0) == 0
    _mark(page, "i", 100)
    film = Path(_export_dialog(page, "in out 59.94 mid"))
    v = _video(film)
    assert v["r_frame_rate"] == NTSC60 and int(v["nb_read_frames"]) == 58, v
    assert _which_frame(film, src, 2) == 2
