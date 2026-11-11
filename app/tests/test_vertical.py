"""A cut has a frame shape: 16:9, 9:16 or 1:1 (wave 25, lane VERTICAL; WORDS-22, FINISH-14).

  * the one source of truth (`roughcut/frame.py`): stored with the cut as `frame`, 16:9 when none
    is stored; the arithmetic of the reframe window, which the browser (`app/static/frame.js`)
    and the renderer both take their crop from — held to the same numbers here;
  * a cut that stores none renders exactly as it always did: the conform chains of every
    16:9 path are pinned;
  * the cut stores it (save, copy, undo), the Export dialog names the sizes in it (1080×1920,
    1080×1080), the effects' frame check and proof use it;
  * the export fills the frame with the shot, centred, or where the shot's reframe puts it,
    and the same picture is on the Program: five timecodes, a title and a reframed shot among
    them (the monitor-versus-file rule);
  * sideways clips fill a 9:16 frame: a turned clip's upright picture, cropped, not its stored one;
  * the AI's second deliverable: `new_cut` first, a saved copy, the cut the editor is on untouched.

    uv run --with pytest --with fastapi --with uvicorn --with httpx --with numpy \
        --with playwright pytest app/tests/test_vertical.py -q
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from conftest import _sidecar

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TOOLS = ROOT / "research" / "tools"

from roughcut import actions, frame  # noqa: E402

# a smooth, static picture whose every part is different: red grows to the right, green
# downwards, blue is a coarse checker — a crop that lands a few percent off is a visible miss
GRAD = ("color=c=black:s=640x360:r=24:d={d},format=rgb24,"
        "geq=r='255*X/W':g='255*Y/H':b='if(lt(mod(floor(X/(W/8))+floor(Y/(H/6)),2),1),60,200)'")
TOL = 28                  # per channel, per cell of the compared grids
GRID = (6, 10)            # cells across, down


# ================================================================== the one source of truth

def test_a_cut_with_no_frame_is_16_9_and_every_shape_has_its_aspect():
    assert frame.shape_of({}) == "16:9" and frame.shape_of(None) == "16:9"
    assert frame.shape_of({"frame": "9:16"}) == "9:16"
    assert frame.shape_of({"frame": "1:1"}) == "1:1"
    assert frame.shape_of({"frame": "nonsense"}) == "16:9"
    assert frame.aspect("16:9") == pytest.approx(16 / 9)
    assert frame.aspect("9:16") == pytest.approx(9 / 16)
    assert frame.aspect("1:1") == 1.0
    assert frame.is_wide("16:9") and not frame.is_wide("9:16") and not frame.is_wide("1:1")
    assert frame.validate("vertical") == "9:16" and frame.validate(None) is None
    with pytest.raises(ValueError):
        frame.validate("3:2")


def test_the_sizes_a_shape_exports_at_follow_the_short_side():
    assert frame.reshape("16:9", 1920, 1080) == (1920, 1080)       # nothing about 16:9 changes
    assert frame.reshape("9:16", 1920, 1080) == (1080, 1920)       # Quick
    assert frame.reshape("1:1", 1920, 1080) == (1080, 1080)
    assert frame.reshape("9:16", 1280, 720) == (720, 1280)         # 720p
    assert frame.reshape("9:16", 3840, 2160) == (2160, 3840)       # Master 4K
    assert frame.proof_size("16:9") == (1280, 720)
    assert frame.proof_size("9:16") == (720, 1280)
    assert frame.proof_size("1:1") == (720, 720)


def test_the_reframe_window_is_the_largest_one_with_the_frames_aspect_moved_and_zoomed():
    # a 16:9 picture in a 9:16 frame: the full height, 31.6 % of the width, centred
    l, t, w, h = frame.window(1920, 1080, "9:16")
    assert (round(w, 4), h) == (0.3164, 1.0) and round(l, 4) == round((1 - w) / 2, 4) and t == 0
    # x slides it through the room that leaves, 0 at the left edge, 1 at the right
    assert frame.window(1920, 1080, "9:16", 0.0)[0] == 0.0
    l1, _, w1, _ = frame.window(1920, 1080, "9:16", 1.0)
    assert l1 + w1 == pytest.approx(1.0)
    # a zoom shrinks it, and y has room only once it has
    assert frame.window(1920, 1080, "9:16", 0.5, 0.0)[1] == 0.0
    _, t2, w2, h2 = frame.window(1920, 1080, "9:16", 0.5, 1.0, 2.0)
    assert (round(w2, 4), h2) == (0.1582, 0.5) and t2 + h2 == pytest.approx(1.0)
    # a portrait picture in a 9:16 frame is whole; in a square one it is cut at the top and bottom
    assert frame.window(1080, 1920, "9:16") == (0.0, 0.0, 1.0, 1.0)
    assert frame.window(1080, 1920, "1:1")[2:] == (1.0, 0.5625)
    # controls → x, y, zoom
    assert frame.reframe_of({"reframe_x": 20, "reframe_zoom": 150}) == (0.2, 0.5, 1.5)
    assert frame.reframe_of(None) == (0.5, 0.5, 1.0)


def test_the_conform_chain_of_a_16_9_cut_is_what_it_always_was():
    sys.path.insert(0, str(TOOLS))
    import assemble
    video = {"w": 1920, "h": 1080, "fps": "24000/1001"}
    assert assemble.conform_chain(video, None, None) == [
        "scale=1920:1080:force_original_aspect_ratio=decrease",
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2", "fps=24000/1001"]
    gpu = assemble.conform_chain(video, {"flip": False, "full": False}, None)
    assert gpu == ["scale_cuda=1920:1080:force_original_aspect_ratio=decrease:format=yuv420p:interp_algo=bicubic",
                   "hwdownload", "format=yuv420p", "pad=1920:1080:(ow-iw)/2:(oh-ih)/2", "fps=24000/1001"]
    scaled = assemble.conform_chain({**video, "scaler": "lanczos"}, None, "norm", 2.0, lead=1.0)
    assert scaled[0] == "norm" and scaled[-2].startswith("pad=") and "increase" not in " ".join(scaled)
    # a 16:9 cut hands the renderer no shape: the frame is the profile's own
    v = assemble.resolve_video_profile("preview", Path("."), [], None, None, None, "16:9")
    assert (v["w"], v["h"]) == (1920, 1080) and "fill" not in v
    # and a reframe never reaches a 16:9 chain, whatever the shot carries
    ctl = {"reframe_x": 0, "reframe_zoom": 300}
    assert assemble.video_filter(video, None, None, 1.0, ctl) == assemble.video_filter(video, None, None, 1.0, None)


def test_the_conform_chain_of_a_9_16_cut_fills_the_frame_and_crops_where_the_shot_says():
    sys.path.insert(0, str(TOOLS))
    import assemble
    v = assemble.resolve_video_profile("preview", Path("."), [], None, None, None, "9:16")
    assert (v["w"], v["h"], v["fill"]) == (1080, 1920, True)
    sq = assemble.resolve_video_profile("preview", Path("."), [], None, None, None, "1:1")
    assert (sq["w"], sq["h"]) == (1080, 1080)
    chain = assemble.conform_chain(v, None, None)
    assert chain[0] == "scale=1080:1920:force_original_aspect_ratio=increase"
    assert chain[1] == "crop=1080:1920:x='(iw-1080)*0.500000':y='(ih-1920)*0.500000'"
    assert not any(c.startswith("pad=") for c in chain) and chain[-1].startswith("fps=")
    moved = assemble.conform_chain(v, None, None, reframe=(0.2, 0.7, 1.5))
    assert moved[1].startswith("scale=w='trunc(iw*1.500000/2)*2'")
    assert moved[2] == "crop=1080:1920:x='(iw-1080)*0.200000':y='(ih-1920)*0.700000'"
    # the shot's own controls reach it through video_filter
    vf = assemble.video_filter(v, None, None, 1.0, {"reframe_x": 20, "reframe_y": 70, "reframe_zoom": 150})
    assert "x='(iw-1080)*0.200000'" in vf and "y='(ih-1920)*0.700000'" in vf
    # an export's own Frame Size is the size of that shape
    big = assemble.resolve_video_profile("preview", Path("."), [], None, (720, 1280), None, "9:16")
    assert (big["w"], big["h"], big["fill"]) == (720, 1280, True)


# ================================================================== the fixture bin

def _bin(root: Path) -> dict:
    footage, sidecars, work = root / "footage", root / "sidecars", root / "work"
    for d in (footage, sidecars, work):
        d.mkdir()
    for stem in ("GRADA", "GRADB", "GRADC"):
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi", "-i", GRAD.format(d=6),
             "-f", "lavfi", "-i", "sine=frequency=330:duration=6", "-c:v", "libx264",
             "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
             str(footage / f"{stem}.MP4")], check=True, capture_output=True)
        (sidecars / f"{stem}.audio.json").write_text(json.dumps(_sidecar(stem)), encoding="utf-8")
    return {"root": root, "footage": footage, "sidecars": sidecars, "work": work}


TITLE = {
    "id": "fx_vert01", "shot": "s1", "clip": "GRADA.MP4", "name": "Opening title", "note": "a title",
    "why": "", "events": [{"t": 0.5, "x": 0.5, "y": 0.5}], "sound": None, "status": "accepted",
    "overlay": {"duration": 2.0, "size": 1.0,
                "shapes": [{"type": "text", "color": "#ffffff", "opacity": 1.0, "text": "COPPER 2026",
                            "x": 0.0, "y": 0.0, "h": 0.1, "bold": True, "fit": True}],
                "anim": {}},
}


def _edl(frame_shape: str | None, **extra) -> dict:
    edl = {"variant": "V", "title": "vertical", "orient": "none", "story": "", "target_s": [5, 20],
           "fps": 24.0,
           "segments": [
               {"id": "s1", "clip": "GRADA.MP4", "in": 0.5, "out": 2.5, "why": "title"},
               {"id": "s2", "clip": "GRADB.MP4", "in": 0.5, "out": 2.5, "why": "reframed",
                "controls": {"reframe_x": 12, "reframe_y": 50, "reframe_zoom": 140}},
               {"id": "s3", "clip": "GRADC.MP4", "in": 0.5, "out": 2.5, "why": "plain"}],
           "effects": [dict(TITLE)]}
    if frame_shape and frame_shape != "16:9":
        edl["frame"] = frame_shape
    edl.update(extra)
    return edl


@pytest.fixture(scope="module")
def vbin(tmp_path_factory):
    return _bin(tmp_path_factory.mktemp("vertical-bin"))


def _configure(server, vbin, edl_path: Path):
    server.MEDIA["failed"].clear()
    server.MEDIA["wanted"].clear()
    server.configure(edl_path, vbin["footage"], vbin["sidecars"], vbin["work"], proxies=False,
                     visual=vbin["work"] / "no-visual")


@pytest.fixture
def board(vbin, tmp_path):
    from fastapi.testclient import TestClient
    import server
    edl_path = tmp_path / "edl.json"
    edl_path.write_text(json.dumps(_edl(None)), encoding="utf-8")
    _configure(server, vbin, edl_path)
    with TestClient(server.app) as c:
        yield {"client": c, "server": server, "edl": edl_path}
    server.MEDIA["wanted"].clear()


def _stored(board) -> dict:
    return json.loads(board["edl"].read_text(encoding="utf-8"))


# ================================================================== stored with the cut

def test_the_cut_stores_its_shape_and_16_9_is_the_absence_of_it(board):
    c = board["client"]
    assert c.get("/api/project").json()["frame"] == "16:9"
    assert "frame" not in _stored(board)
    r = c.put("/api/project", json={"frame": "9:16"})
    assert r.status_code == 200, r.text
    assert _stored(board)["frame"] == "9:16"
    assert c.get("/api/project").json()["frame"] == "9:16"
    # a save that says nothing about it leaves it; 16:9 clears it; a shape that is none is a 400
    assert c.put("/api/project", json={"markers": []}).status_code == 200
    assert _stored(board)["frame"] == "9:16"
    assert c.put("/api/project", json={"frame": "3:2"}).status_code == 400
    assert _stored(board)["frame"] == "9:16"
    assert c.put("/api/project", json={"frame": "16:9"}).status_code == 200
    assert "frame" not in _stored(board)
    assert c.put("/api/project", json={"frame": "1:1"}).status_code == 200
    assert c.get("/api/project").json()["frame"] == "1:1"


def test_a_saved_copy_can_take_another_shape_and_leaves_the_cut_it_came_from_alone(board):
    c = board["client"]
    before = _stored(board)
    r = c.post("/api/cuts/copy", json={"name": "Instagram", "open": False, "frame": "9:16"})
    assert r.status_code == 200, r.text
    copy = Path(r.json()["copy"]["path"])
    made = json.loads(copy.read_text(encoding="utf-8"))
    assert made["frame"] == "9:16" and made["segments"] == before["segments"]
    assert _stored(board) == before                         # the cut we are on: untouched
    assert c.get("/api/project").json()["frame"] == "16:9"
    # a copy of a vertical cut is vertical, unless it is told another shape
    c.put("/api/project", json={"frame": "9:16"})
    plain = c.post("/api/cuts/copy", json={"name": "Again", "open": False}).json()["copy"]["path"]
    assert json.loads(Path(plain).read_text())["frame"] == "9:16"
    wide = c.post("/api/cuts/copy", json={"name": "Wide", "open": False, "frame": "16:9"}).json()["copy"]["path"]
    assert "frame" not in json.loads(Path(wide).read_text())
    assert c.post("/api/cuts/copy", json={"name": "Bad", "open": False, "frame": "5:4"}).status_code == 400


def test_a_shots_reframe_is_a_control_with_ranges(board):
    c = board["client"]
    segs = c.get("/api/project").json()["segments"]
    segs[0]["controls"] = {"reframe_x": 10, "reframe_y": 90, "reframe_zoom": 200}
    assert c.put("/api/project", json={"segments": segs}).status_code == 200
    assert _stored(board)["segments"][0]["controls"] == {"reframe_x": 10.0, "reframe_y": 90.0, "reframe_zoom": 200.0}
    segs[0]["controls"] = {"reframe_zoom": 50}
    assert c.put("/api/project", json={"segments": segs}).status_code == 400
    segs[0]["controls"] = {"reframe_x": 50, "reframe_y": 50, "reframe_zoom": 100}     # the defaults are no controls
    assert c.put("/api/project", json={"segments": segs}).status_code == 200
    assert "controls" not in _stored(board)["segments"][0]


# ================================================================== the Export dialog

def test_the_export_dialog_names_its_sizes_in_the_cuts_shape(board):
    c = board["client"]

    def sizes():
        d = c.get("/api/export").json()
        return d, {s["key"]: (s["width"], s["height"]) for s in d["sizes"]}, \
            {p["key"]: (p["width"], p["height"]) for p in d["presets"]}

    d, sz, pr = sizes()
    assert d["frame"] == "16:9" and sz["1080"] == (1920, 1080) and sz["720"] == (1280, 720)
    assert pr["quick"] == (1920, 1080)
    wide = (sz, pr)
    c.put("/api/project", json={"frame": "9:16"})
    d, sz, pr = sizes()
    assert d["frame"] == "9:16"
    # Quick is fixed; YouTube / Match / Master are the bin's own frame, never upscaled: this
    # bin's clips are 640×360, so their vertical twin is 360×640
    assert pr["quick"] == (1080, 1920) and pr["youtube"] == (360, 640)
    assert sz["1080"] == (1080, 1920) and sz["720"] == (720, 1280) and sz["540"] == (540, 960)
    assert sz["2160"] == (2160, 3840)
    quick = next(p for p in d["presets"] if p["key"] == "quick")
    assert "1080×1920" in quick["about"] and "1920×1080" not in quick["about"]
    c.put("/api/project", json={"frame": "1:1"})
    d, sz, pr = sizes()
    assert pr["quick"] == (1080, 1080) and sz["1080"] == (1080, 1080) and sz["720"] == (720, 720)
    # and back: 16:9 is what it was
    c.put("/api/project", json={"frame": "16:9"})
    _, sz, pr = sizes()
    assert (sz, pr) == wide


def test_an_export_of_a_vertical_cut_asks_assemble_for_nothing_it_does_not_read_from_the_cut(board):
    """The renderer reads the shape from the cut itself, so every path that renders — the
    export, the quick version, the size sample — gets it; a custom Frame Size rides `--size`."""
    server = board["server"]
    board["client"].put("/api/project", json={"frame": "9:16"})
    choice = server.export_choice("quick", "720", None, None, None, 24.0)
    assert choice["frame"] == (720, 1280) and choice["custom_frame"] == (720, 1280)
    own = server.export_choice("quick", None, None, None, None, 24.0)
    assert own["custom_frame"] is None and own["frame"] == (1080, 1920)


# ================================================================== the effects' frame

def _title(text: str, h: float = 0.1, size: float = 1.0, y: float = 0.5, **shape) -> dict:
    return {"id": "fx_t", "shot": "s1", "name": "t", "events": [{"t": 0.0, "x": 0.5, "y": y}],
            "overlay": {"duration": 2.0, "size": size, "anim": {},
                        "shapes": [{"type": "text", "color": "#ffffff", "opacity": 1.0, "text": text,
                                    "x": 0.0, "y": 0.0, "h": h, "bold": True, "fit": True, **shape}]}}


def test_the_frame_the_effects_are_measured_on_is_the_cuts(board):
    server = board["server"]
    assert server._fx_frame() == (1280, 720)
    board["client"].put("/api/project", json={"frame": "9:16"})
    assert server._fx_frame() == (720, 1280)
    board["client"].put("/api/project", json={"frame": "1:1"})
    assert server._fx_frame() == (720, 720)


def test_a_title_that_fits_16_9_is_checked_again_on_a_9_16_frame():
    """The renderer draws the title `size` of the frame's WIDTH wide, so a line near the top
    that is inside 16:9's safe area (its height is a fraction of a 720-high frame) is a
    larger share of a 1280-high one the other way round: here a title placed at the top
    edge with a tall plate passes on 16:9 and is measured outside on 9:16, and the same
    words low on the frame pass on both."""
    from roughcut import fx
    plate = {"type": "rect", "color": "#000000", "opacity": 0.6, "x": 0.0, "y": 0.0, "w": 1.0, "h": 0.5}
    e = _title("THE HIGH PASS", y=0.1)
    e["overlay"]["shapes"].insert(0, plate)
    wide = fx.check_bounds(e, 1280, 720)
    tall = fx.check_bounds(e, 720, 1280)
    assert wide["ok"] is not None and tall["ok"] is not None
    assert wide["ok"] != tall["ok"] or wide["problems"] != tall["problems"], (wide, tall)
    # the same title, centred, is inside both: the check is not simply stricter on 9:16
    mid = _title("THE HIGH PASS")
    assert fx.check_bounds(mid, 1280, 720)["ok"] and fx.check_bounds(mid, 720, 1280)["ok"]
    # a long line is too wide for either, and is said so on both
    long = _title("COPPER MOUNTAIN · FEBRUARY 2026 · THE LONG WAY DOWN", h=0.08)
    assert fx.check_bounds(long, 1280, 720)["ok"] is False and fx.check_bounds(long, 720, 1280)["ok"] is False


def test_verify_scales_a_poses_vertical_offset_by_the_frames_own_aspect():
    from roughcut import fx
    e = _title("RISE")
    e["overlay"]["anim"] = {"dy": [[0.0, 0.0], [1.0, -0.4]]}
    seg = {"clip": "GRADA.MP4", "in": 0.0, "out": 3.0}
    ev = {"id": "fx_r", "shot": "s1", "name": "r", "status": "proposed", "sound": None,
          "events": [{"t": 1.0, "x": 0.5, "y": 0.3}], "overlay": e["overlay"]}

    def in_frame(aspect: float) -> dict:
        v = fx.verify(ev, seg, aspect=aspect)
        return next(c for c in v["checks"] if c["key"] == "in_frame")

    # dy is a fraction of the WIDTH: -0.4 of 1280 is -0.71 of a 720-high frame (off), and only
    # -0.225 of a 1280-high one? no: 0.4 × 9/16 = 0.225 — y 0.3 − 0.225 stays inside
    assert in_frame(16 / 9)["ok"] is False
    assert in_frame(9 / 16)["ok"] is True


def test_the_designer_is_told_the_frame_when_it_is_not_16_9():
    from roughcut import fx
    assert "9:16" in fx.frame_note("9:16") and "narrow" in fx.frame_note("9:16")
    assert "square" in fx.frame_note("1:1")


def test_the_proof_is_made_on_the_frame_the_cut_has(vbin, tmp_path):
    from roughcut import fxcheck
    seg = {"id": "s1", "clip": "GRADA.MP4", "in": 0.5, "out": 2.5, "controls": {"reframe_x": 0}}
    effect = dict(TITLE)
    out, base, stats = fxcheck.proof(effect, seg, vbin["footage"] / "GRADA.MP4", tmp_path, w=360, h=640,
                                     fps=24, shape="9:16")
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height", "-of", "csv=p=0", str(out)], capture_output=True, text=True)
    assert r.stdout.strip() == "360,640"
    # the base picture fills the frame (no black bars) with the shot's own reframe: x 0 is the
    # picture's left edge, which in this clip is where red is least
    left = _cells(base, 0.5)
    assert left[:, :, 0].mean() < 110, left[:, :, 0].mean()
    assert left.min() >= 0 and (left.reshape(-1, 3).max(axis=1) > 25).all(), "black bars in the proof"


# ================================================================== the render

def _cells(path: Path, t: float, grid=GRID) -> np.ndarray:
    """Mean RGB of a grid of cells of the frame at `t`: (rows, cols, 3)."""
    cols, rows = grid
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
         "-vf", f"scale={cols * 8}:{rows * 8}:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(rows * 8, cols * 8, 3).astype(float)
    return a.reshape(rows, 8, cols, 8, 3).mean((1, 3))


def _expected(wide: Path, t: float, shape: str, x=0.5, y=0.5, zoom=1.0) -> np.ndarray:
    """What the frame must show, worked out from the 16:9 export of the same shot (the whole
    picture, with whatever tone the export gives every picture): the window `frame.window`
    names, cut out of it and scaled to the cell grid."""
    sw, sh = 1920, 1080
    l, tp, w, h = frame.window(sw, sh, shape, x, y, zoom)
    cols, rows = GRID
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{t:.3f}", "-i", str(wide), "-frames:v", "1",
         "-vf", f"crop={w * sw:.3f}:{h * sh:.3f}:{l * sw:.3f}:{tp * sh:.3f},scale={cols * 8}:{rows * 8}:flags=area",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(rows * 8, cols * 8, 3).astype(float)
    return a.reshape(rows, 8, cols, 8, 3).mean((1, 3))


def _assemble(edl: dict, footage: Path, out: Path, *extra: str) -> Path:
    edl_path = out.with_suffix(".edl.json")
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    r = subprocess.run(
        ["uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(edl_path), "--footage", str(footage),
         "--profile", "preview", "--jobs", "1", "-o", str(out), *extra],
        capture_output=True, text=True, env={**os.environ, "ROUGHCUT_GPU": "0", "ROUGHCUT_NVENC": "0"})
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    return out


def _size(path: Path) -> tuple[int, int]:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    w, h = r.stdout.strip().split(",")
    return int(w), int(h)


def _short_edl(shape: str | None, controls: dict | None = None) -> dict:
    seg = {"id": "s1", "clip": "GRADA.MP4", "in": 0.5, "out": 1.5, "why": "x"}
    if controls:
        seg["controls"] = controls
    edl = {"variant": "V", "title": "v", "orient": "none", "story": "", "target_s": [1, 5], "fps": 24.0,
           "segments": [seg]}
    if shape and shape != "16:9":
        edl["frame"] = shape
    return edl


@pytest.fixture(scope="module")
def wide(vbin, tmp_path_factory):
    """The 16:9 export of the short cut: the picture whole, as the export tones every picture."""
    return _assemble(_short_edl(None), vbin["footage"], tmp_path_factory.mktemp("wide") / "wide.mp4")


@pytest.fixture(scope="module")
def wide_film(vbin, tmp_path_factory):
    """The 16:9 export of the three-shot cut (no frame, so no reframe is read)."""
    return _assemble(_edl(None, effects=[]), vbin["footage"], tmp_path_factory.mktemp("widefilm") / "film.mp4")


def test_a_9_16_export_is_1080x1920_and_every_shot_fills_it(vbin, tmp_path, wide):
    out = _assemble(_short_edl("9:16"), vbin["footage"], tmp_path / "v.mp4")
    assert _size(out) == (1080, 1920)
    got = _cells(out, 0.5)
    want = _expected(wide, 0.5, "9:16")
    assert np.abs(got - want).max() <= TOL, np.abs(got - want).max()
    # filled, not pillarboxed: no cell of the frame is black, and the red ramp runs across only
    # the middle third of the source's, not all of it
    assert got.min() > 20
    assert got[:, 0, 0].mean() > 70 and got[:, -1, 0].mean() < 190


def test_a_square_export_is_1080x1080_and_filled(vbin, tmp_path, wide):
    out = _assemble(_short_edl("1:1"), vbin["footage"], tmp_path / "s.mp4")
    assert _size(out) == (1080, 1080)
    got, want = _cells(out, 0.5), _expected(wide, 0.5, "1:1")
    assert np.abs(got - want).max() <= TOL


@pytest.mark.parametrize("ctl", [{"reframe_x": 0}, {"reframe_x": 100, "reframe_y": 20},
                                 {"reframe_x": 15, "reframe_zoom": 200}])
def test_a_reframed_shot_is_cut_from_the_window_its_controls_name(vbin, tmp_path, ctl, wide):
    out = _assemble(_short_edl("9:16", ctl), vbin["footage"], tmp_path / "r.mp4")
    x, y, z = frame.reframe_of(ctl)
    got = _cells(out, 0.5)
    want = _expected(wide, 0.5, "9:16", x, y, z)
    assert np.abs(got - want).max() <= TOL, np.abs(got - want).max()
    centred = _expected(wide, 0.5, "9:16")
    assert np.abs(got - centred).max() > TOL, "the reframe changed nothing: the test is vacuous"


def test_a_16_9_export_is_what_it_was(vbin, tmp_path, wide):
    out = _assemble(_short_edl(None, {"reframe_x": 0, "reframe_zoom": 300}), vbin["footage"], tmp_path / "w.mp4")
    assert _size(out) == (1920, 1080)
    got = _cells(out, 0.5)
    assert np.abs(got - _cells(wide, 0.5)).max() <= 4          # the whole picture, a reframe ignored, as before
    assert subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=r_frame_rate", "-of", "csv=p=0", str(out)],
                          capture_output=True, text=True).stdout.strip() == "24000/1001"


def test_a_custom_frame_size_keeps_the_shape(vbin, tmp_path, wide):
    out = _assemble(_short_edl("9:16"), vbin["footage"], tmp_path / "c.mp4", "--size", "540x960")
    assert _size(out) == (540, 960)
    got, want = _cells(out, 0.5), _expected(wide, 0.5, "9:16")
    assert np.abs(got - want).max() <= TOL


def test_a_vertical_export_keeps_its_rate_and_ends_in_the_limiter(vbin, tmp_path):
    sys.path.insert(0, str(TOOLS))
    import assemble
    out = _assemble(_short_edl("9:16"), vbin["footage"], tmp_path / "l.mp4")
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=r_frame_rate", "-of", "csv=p=0", str(out)], capture_output=True, text=True)
    assert r.stdout.strip() == "24000/1001"
    tp = assemble.true_peak_db(out)
    assert tp is None or tp <= -1.0 + 0.2, tp


def test_a_title_is_laid_out_on_the_vertical_frame(vbin, tmp_path):
    edl = _short_edl("9:16")
    edl["effects"] = [dict(TITLE, shot="s1")]
    on = _assemble(edl, vbin["footage"], tmp_path / "t.mp4")
    plain = _assemble(_short_edl("9:16"), vbin["footage"], tmp_path / "p.mp4")
    a = np.asarray(_cells(on, 0.5, (1, 20)))[:, 0]
    b = np.asarray(_cells(plain, 0.5, (1, 20)))[:, 0]
    rows = np.where(np.abs(a - b).max(axis=1) > 6)[0]
    assert len(rows) >= 1, "no title drawn"
    # centred and level, inside the title-safe area (5 % in from every edge) of the 1080×1920 frame
    assert 0.40 <= rows.mean() / 20 <= 0.60 and rows.min() / 20 >= 0.05 and (rows.max() + 1) / 20 <= 0.95


def test_a_sideways_clip_fills_a_9_16_frame_upright(tmp_path):
    """A phone held upright (a flag of 90 under `orient: auto`): the export shows its upright
    picture — the same as the autorotated source — filling the 9:16 frame."""
    from test_preview_orientation import _make_pattern, _quads, _upright
    footage = tmp_path / "f"
    footage.mkdir()
    _make_pattern(footage / "PHONE.MP4", 90, playable=True)
    edl = {"variant": "V", "title": "v", "orient": "auto", "story": "", "target_s": [1, 5], "fps": 30.0,
           "frame": "9:16", "segments": [{"id": "p", "clip": "PHONE.MP4", "in": 0.2, "out": 1.6}]}
    out = _assemble(edl, footage, tmp_path / "phone.mp4", "--size", "270x480")
    assert _size(out) == (270, 480)
    ref = _upright(footage / "PHONE.MP4")
    got = _quads(out, t=0.7)
    assert np.abs(got - ref).max() <= 40, (got.round(), ref.round())
    # the unturned reading would be the stored pixels: the test tells them apart
    from test_preview_orientation import _stored
    assert np.abs(_stored(footage / "PHONE.MP4") - ref).max() > 100


# ================================================================== the actions

def _state(frame_shape="16:9") -> dict:
    return {"segments": [{"id": "a", "clip": "X", "in": 0, "out": 10, "controls": {}},
                         {"id": "b", "clip": "X", "in": 10, "out": 20}],
            "clips": {"X": {"duration": 60.0}}, "fps": 24, "frame": frame_shape}


def test_frame_and_new_cut_are_ops_and_a_second_deliverable_comes_first():
    ops = [{"op": "new_cut", "name": "Instagram 20 s", "frame": "9:16"},
           {"op": "ripple_delete", "ids": ["b"]},
           {"op": "set", "id": "a", "prop": "reframe_x", "value": 30}]
    res = actions.check(ops, _state())
    assert [o["op"] for o in res["ops"]] == ["new_cut", "ripple_delete", "set"], res["refused"]
    assert res["ops"][0] == {"op": "new_cut", "name": "Instagram 20 s", "frame": "9:16"}
    first = res["lines"][0]
    assert "NEW cut" in first and "Instagram 20 s" in first and "stays as it is" in first
    # a reframe on a 16:9 cut says what to do first; after a frame op it is allowed
    one = actions.check([{"op": "set", "id": "a", "prop": "reframe_zoom", "value": 150}], _state())
    assert "9:16 or 1:1" in one["refused"][0]["why"]
    two = actions.check([{"op": "frame", "frame": "9:16"},
                         {"op": "set", "id": "a", "prop": "reframe_zoom", "value": 150}], _state())
    assert not two["refused"] and two["ops"][0] == {"op": "frame", "frame": "9:16"}
    assert actions.check([{"op": "frame", "frame": "9:16"}], _state("9:16"))["refused"]      # already
    # new_cut: first only, once, named, a real shape
    late = actions.check([{"op": "ripple_delete", "ids": ["b"]}, {"op": "new_cut", "name": "x"}], _state())
    assert "first op" in late["refused"][0]["why"]
    assert actions.check([{"op": "new_cut", "name": ""}], _state())["refused"]
    assert actions.check([{"op": "new_cut", "name": "x", "frame": "7:5"}], _state())["refused"]
    twice = actions.check([{"op": "new_cut", "name": "a"}, {"op": "new_cut", "name": "b"}], _state())
    assert len(twice["ops"]) == 1 and twice["refused"]
    # the ghost lane shows the copy's shots: the ops after it
    assert [s["id"] for s in res["preview"]["segments"]] == ["a"]


def test_the_model_is_taught_the_second_deliverable_and_told_the_frame():
    from roughcut import assist
    schema = actions.schema_text()
    assert '"op":"new_cut"' in schema and '"op":"frame"' in schema and "reframe_x" in schema
    prompt = assist.build_prompt("make a 20 second vertical version for Instagram", {"kind": "sequence"},
                                 _state("9:16"), {})
    assert "SECOND DELIVERABLE" in prompt and "The frame is 9:16" in prompt
    assert "never a plan" in prompt or "never a plan:" in prompt
    assert "The frame is" not in assist.build_prompt("x", {"kind": "sequence"}, _state(), {})


# ================================================================== the board, in a browser

pw_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def served(vbin, tmp_path):
    import uvicorn
    import server
    edl_path = tmp_path / "edl.json"
    edl_path.write_text(json.dumps(_edl(None)), encoding="utf-8")
    own = {**vbin, "work": tmp_path / "work"}            # its own cuts directory: copies are not shared
    own["work"].mkdir()
    _configure(server, own, edl_path)
    server.ensure_proxies([f"{s}.MP4" for s in ("GRADA", "GRADB", "GRADC")])
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started
    yield {"url": f"http://127.0.0.1:{port}", "server": server, "edl": edl_path, "footage": vbin["footage"]}
    srv.should_exit = True
    th.join(timeout=10)


def _open(pw, served):
    browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(served["url"])
    pg.wait_for_selector("#tl .blk")
    pg.wait_for_function("typeof C !== 'undefined' && C && C.shots && window.effects && window.rcFrame && window.rcActions && window.switcher", timeout=10000)
    pg._errors = errors
    return browser, pg


def _screen(pg) -> dict:
    """The Program's box and its panel, once the layout has settled (two reads agree)."""
    last = None
    for _ in range(20):
        now = _screen_now(pg)
        if now == last:
            return now
        last = now
        time.sleep(0.15)
    return last


def _screen_now(pg) -> dict:
    return pg.evaluate("""() => { const r = document.querySelector('.screen').getBoundingClientRect();
        const b = document.querySelector('.screen').closest('.screenbox').getBoundingClientRect();
        return {w: r.width, h: r.height, bw: b.width, bh: b.height, l: r.left, t: r.top}; }""")


def _wait_saved(pg):
    pg.wait_for_function("document.querySelector('#saveState').dataset.state === 'saved'", timeout=8000)


def _set_frame(pg, shape: str):
    pg.evaluate("tl.openSequenceSettings()")
    pg.wait_for_selector("#seqSettings #ssFrame")
    pg.select_option("#ssFrame", shape)
    pg.click("#ssOk")
    pg.wait_for_function("(s) => window.rcFrame.shape() === s", arg=shape, timeout=5000)


def test_the_program_is_the_cuts_shape_letterboxed_in_the_panel_never_stretched(served):
    with sync_playwright() as pw:
        browser, pg = _open(pw, served)
        wide = _screen(pg)
        assert wide["w"] / wide["h"] == pytest.approx(16 / 9, abs=0.01)
        assert pg.evaluate("document.querySelector('.screen').hasAttribute('data-frame')") is False
        _set_frame(pg, "9:16")
        tall = _screen(pg)
        assert tall["w"] / tall["h"] == pytest.approx(9 / 16, abs=0.01)
        assert tall["h"] <= tall["bh"] + 1 and tall["w"] < tall["bw"], tall        # inside the panel, letterboxed
        _set_frame(pg, "1:1")
        sq = _screen(pg)
        assert sq["w"] / sq["h"] == pytest.approx(1.0, abs=0.01) and sq["w"] <= sq["bw"] + 1 and sq["h"] <= sq["bh"] + 1
        _set_frame(pg, "16:9")
        back = _screen(pg)
        assert back["w"] / back["h"] == pytest.approx(16 / 9, abs=0.01)
        assert pg._errors == [], pg._errors
        browser.close()


def test_sequence_settings_changes_the_shape_in_one_undo_and_the_cut_keeps_it(served):
    with sync_playwright() as pw:
        browser, pg = _open(pw, served)
        pg.evaluate("tl.openSequenceSettings()")
        pg.wait_for_selector("#seqSettings #ssFrame")
        assert pg.input_value("#ssFrame") == "16:9"
        assert [o for o in pg.locator("#ssFrame option").all_inner_texts()] == [
            "Landscape 16:9", "Vertical 9:16", "Square 1:1"]
        pg.select_option("#ssFrame", "9:16")
        assert "1080×1920" in pg.locator("#ssFrameAbout").inner_text()
        pg.click("#ssOk")
        pg.wait_for_function("window.rcFrame.shape() === '9:16'")
        _wait_saved(pg)
        assert json.loads(served["edl"].read_text())["frame"] == "9:16"
        assert pg.locator("#undo").get_attribute("title").startswith("undo: sequence settings")
        pg.evaluate("tl.undo()")                                   # ONE undo takes it back
        pg.wait_for_function("window.rcFrame.shape() === '16:9'")
        _wait_saved(pg)
        assert "frame" not in json.loads(served["edl"].read_text())
        pg.evaluate("tl.redo()")
        pg.wait_for_function("window.rcFrame.shape() === '9:16'")
        pg.reload()
        pg.wait_for_function("window.rcFrame && window.tl && tl.frameShape() === '9:16'", timeout=10000)
        assert _screen(pg)["w"] / _screen(pg)["h"] == pytest.approx(9 / 16, abs=0.01)
        assert pg._errors == [], pg._errors
        browser.close()


def test_reframe_is_in_effect_controls_only_where_it_applies_and_each_change_is_one_undo(served):
    with sync_playwright() as pw:
        browser, pg = _open(pw, served)
        pg.locator("#tl .blk").nth(0).click()
        pg.wait_for_selector("#inspector .ec [data-sec=motion]")
        names = pg.locator("#inspector .ec-sec > .ec-sh .ec-twirl span").all_inner_texts()
        assert "Reframe" not in names                               # a 16:9 cut: nothing about it changes
        _set_frame(pg, "9:16")
        pg.locator("#tl .blk").nth(0).click()
        pg.wait_for_selector("#inspector [data-sec=reframe]")
        names = pg.locator("#inspector .ec-sec > .ec-sh .ec-twirl span").all_inner_texts()
        assert names[names.index("Opacity") + 1] == "Reframe"
        pg.click("#inspector [data-sec=reframe] .ec-twirl")
        assert pg.locator("#inspector [data-sec=reframe] .ec-name").all_inner_texts() == [
            "Left ↔ right", "Up ↔ down", "Zoom"]
        assert pg.input_value("#inspector .ec-v[data-k=reframe_x]") == "50.0 %"
        pg.locator("#inspector .ec-v[data-k=reframe_x]").click()
        inp = pg.locator("#inspector .ec-v.editing[data-k=reframe_x]")
        inp.fill("20")
        inp.press("Enter")
        _wait_saved(pg)
        assert json.loads(served["edl"].read_text())["segments"][0]["controls"] == {"reframe_x": 20.0}
        assert pg.locator("#undo").get_attribute("title").startswith("undo: reframe")
        pg.locator("#inspector .ec-v[data-k=reframe_zoom]").click()
        z = pg.locator("#inspector .ec-v.editing[data-k=reframe_zoom]")
        z.fill("150")
        z.press("Enter")
        _wait_saved(pg)
        assert json.loads(served["edl"].read_text())["segments"][0]["controls"] == {"reframe_x": 20.0, "reframe_zoom": 150.0}
        pg.evaluate("tl.undo()")                                    # one undo per change
        _wait_saved(pg)
        assert json.loads(served["edl"].read_text())["segments"][0]["controls"] == {"reframe_x": 20.0}
        pg.evaluate("tl.undo()")
        _wait_saved(pg)
        assert "controls" not in json.loads(served["edl"].read_text())["segments"][0]
        # the panel's reset puts it back to the centre
        pg.evaluate("tl.redo()")
        pg.click("#inspector [data-row=reframe_x] .ec-reset")
        _wait_saved(pg)
        assert "controls" not in json.loads(served["edl"].read_text())["segments"][0]
        # back to 16:9 and the section is gone again
        _set_frame(pg, "16:9")
        pg.locator("#tl .blk").nth(0).click()
        pg.wait_for_selector("#inspector .ec [data-sec=motion]")
        assert "Reframe" not in pg.locator("#inspector .ec-sec > .ec-sh .ec-twirl span").all_inner_texts()
        assert pg._errors == [], pg._errors
        browser.close()


def test_the_browsers_window_is_the_renderers_window(served):
    """`rcFrame.window` and `frame.window` are one arithmetic: a grid of pictures, shapes and
    reframes gives the same four numbers in Python and in the page."""
    grid = [(sw, sh, shape, x, y, z)
            for (sw, sh) in ((1920, 1080), (3840, 2160), (1080, 1920), (640, 360), (4000, 3000))
            for shape in ("9:16", "1:1", "16:9") for x, y in ((0.5, 0.5), (0, 0), (1, 1), (0.2, 0.8))
            for z in (1.0, 1.5, 4.0)]
    with sync_playwright() as pw:
        browser, pg = _open(pw, served)
        got = pg.evaluate("(g) => g.map(([sw, sh, s, x, y, z]) => window.rcFrame.window(sw, sh, s, x, y, z))", grid)
        for row, g in zip(grid, got):
            want = frame.window(*row)
            assert g == pytest.approx(list(want), abs=1e-9), (row, g, want)
        # the controls → the factors, the same way
        assert pg.evaluate("rcFrame.reframe({reframe_x: 20, reframe_zoom: 150})") == {"x": 0.2, "y": 0.5, "zoom": 1.5}
        browser.close()


def _shot(pg, path: Path) -> Path:
    box = pg.evaluate("(() => { const r = document.querySelector('.screen').getBoundingClientRect();"
                      " return {x: r.left, y: r.top, width: r.width, height: r.height}; })()")
    pg.screenshot(path=str(path), clip=box)
    return path


def _park(pg, film_t: float) -> None:
    """The Program parked on film time `film_t`, its picture drawn."""
    pg.evaluate("(t) => tl.seek(t)", film_t)
    pg.wait_for_function(
        "() => { const v = liveVideo(); return v && v.readyState >= 2 && !v.seeking; }", timeout=10000)
    time.sleep(0.7)                  # the frame is painted, the effects' canvas drawn


MONITOR_AT = [0.4, 1.2, 2.6, 3.4, 5.0]        # film seconds: the title shot (0-2), the reframed (2-4), the plain (4-6)


def test_the_programs_picture_is_the_exports_at_five_timecodes(served, tmp_path, wide_film):
    """The monitor-versus-file rule: the same cut parked at five times in the page, and
    exported; each Program screenshot is compared with the file's frame, cell by cell. Two of
    the five are in the shot with the title (its words are in both), two in the reframed one."""
    edl = _edl("9:16")
    served["edl"].write_text(json.dumps(edl), encoding="utf-8")
    out = _assemble(edl, served["footage"], tmp_path / "film.mp4")
    assert _size(out) == (1080, 1920)
    pairs = []
    with sync_playwright() as pw:
        browser, pg = _open(pw, served)
        assert _screen(pg)["w"] / _screen(pg)["h"] == pytest.approx(9 / 16, abs=0.01)
        for i, t in enumerate(MONITOR_AT):
            _park(pg, t)
            shot = _shot(pg, tmp_path / f"monitor-{i}.png")
            frame_png = tmp_path / f"file-{i}.png"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(out), "-frames:v", "1",
                            str(frame_png)], check=True)
            pairs.append((t, shot, frame_png))
        assert pg._errors == [], pg._errors
        browser.close()
    keep = os.environ.get("VERTICAL_PAIRS")
    if keep:
        Path(keep).mkdir(parents=True, exist_ok=True)
        for t, a, b in pairs:
            (Path(keep) / a.name).write_bytes(a.read_bytes())
            (Path(keep) / b.name).write_bytes(b.read_bytes())
    for t, a, b in pairs:
        ca, cb = _cells(a, 0, GRID), _cells(b, 0, GRID)
        assert np.abs(ca - cb).max() <= TOL, f"film {t:.1f} s: the monitor and the file differ by {np.abs(ca - cb).max():.0f}"
    # and the check can tell: the reframed shot's file frame is not the centred one
    centred = _expected(wide_film, 2.6, "9:16")
    assert np.abs(_cells(pairs[2][2], 0, GRID) - centred).max() > TOL


# ================================================================== the AI's second deliverable

class Scripted:
    """The assist call answers with `answer` (the suite's fake backend: no real call)."""
    name = "vertical-scripted"

    def __init__(self, answer):
        self.answer = answer

    def complete(self, request):
        from roughcut import config, inference
        text = json.dumps(self.answer) if "kind" in (request.schema or {}) else "{}"
        model = config.model_for(request.role)
        return inference.Result(content=text, input_tokens=4000, output_tokens=300, backend=self.name,
                                model=model, projected_usd=config.projected_usd(model, 4000, 300),
                                latency_ms=1, raw=text)


VERTICAL_ASK = "now make a 20 second vertical version for Instagram"
VERTICAL_ANSWER = {
    "kind": "ops",
    "summary": "A new cut, Instagram 20 s: the first two shots in 9:16; the cut you are on stays as it is.",
    "ops": [{"op": "new_cut", "name": "Instagram 20 s", "frame": "9:16"},
            {"op": "ripple_delete", "ids": ["s3"]},
            {"op": "set", "id": "s1", "prop": "reframe_x", "value": 30}]}


def _assist(client, text: str) -> dict:
    job = client.post("/api/assist", json={"text": text, "scope": {"kind": "sequence"}}).json()["job"]
    for _ in range(200):
        d = client.get(f"/api/assist/{job}").json()
        if d["status"] != "running":
            return d
        time.sleep(0.05)
    raise AssertionError("the assist job did not finish")


def test_the_prompt_box_proposes_a_second_cut_and_touches_nothing(board):
    from roughcut import inference
    before = board["edl"].read_bytes()
    inference.set_backend(Scripted(VERTICAL_ANSWER))
    try:
        d = _assist(board["client"], VERTICAL_ASK)
    finally:
        inference.set_backend(None)
    assert d["status"] == "done" and d["kind"] == "ops", d
    assert [o["op"] for o in d["ops"]] == ["new_cut", "ripple_delete", "set"], d.get("refused")
    first = d["lines"][0]
    assert "NEW cut" in first and "Instagram 20 s" in first and "9:16" in first and "stays as it is" in first
    assert d["summary"].startswith("A new cut")
    assert board["edl"].read_bytes() == before                  # the review wrote nothing
    # the same words, answered as a whole-cut re-cut, would replace the film: the prompt says no
    from roughcut import assist
    prompt = assist.build_prompt(VERTICAL_ASK, {"kind": "sequence"}, board["server"].assist_state(), {})
    assert "SECOND DELIVERABLE" in prompt and "NEVER shortened or reshaped" in prompt


def test_accepting_it_saves_a_copy_in_9_16_and_makes_the_edits_there(served):
    from roughcut import inference
    original = served["edl"].read_bytes()
    inference.set_backend(Scripted(VERTICAL_ANSWER))
    try:
        with sync_playwright() as pw:
            browser, pg = _open(pw, served)
            res = pg.evaluate("""async (text) => {
              await roughcutFlush();
              const r = await (await fetch('/api/assist', {method: 'POST', headers: {'content-type': 'application/json'},
                body: JSON.stringify({text, scope: {kind: 'sequence', playhead: tl.state.playhead}})})).json();
              for (;;) {
                const d = await (await fetch('/api/assist/' + r.job)).json();
                if (d.status !== 'running') return d;
                await new Promise((z) => setTimeout(z, 50));
              }
            }""", VERTICAL_ASK)
            assert res["status"] == "done", res
            pg.evaluate("(r) => rcActions.review(r)", res)
            pg.wait_for_selector("[data-testid=ai-review] [data-testid=ai-op-row]")
            rows = pg.locator("[data-testid=ai-review] [data-testid=ai-op-row]").all_inner_texts()
            assert "NEW cut" in rows[0] and "Instagram 20 s" in rows[0] and "stays as it is" in rows[0], rows[0]
            # reviewing is not doing: the cut on disk and on the board is as it was
            assert served["edl"].read_bytes() == original
            assert pg.evaluate("tl.frameShape()") == "16:9"
            pg.locator("[data-testid=ai-accept-all]").click()
            # the cut switcher opens the copy (the page loads it), and the waiting edits are made on it
            pg.wait_for_function("typeof P !== 'undefined' && P && P.cut === 'Instagram 20 s' && tl.frameShape() === '9:16'"
                                 " && segs.length === 2 && segs[0].controls && segs[0].controls.reframe_x === 30", timeout=40000)
            pg.evaluate("async () => { await roughcutFlush(); if (tl.saveSeq) await tl.saveSeq(); }")
            # the cut the editor was on: byte for byte as it was
            assert served["edl"].read_bytes() == original
            cuts = pg.evaluate("fetch('/api/cuts').then(r => r.json())")
            current = Path(cuts["current"]["path"])
            assert current != served["edl"] and cuts["current"]["name"] == "Instagram 20 s"
            made = json.loads(current.read_text(encoding="utf-8"))
            assert made["frame"] == "9:16" and [s["id"] for s in made["segments"]] == ["s1", "s2"]
            assert made["segments"][0]["controls"] == {"reframe_x": 30.0}
            assert made["cut"]["from"] and made["effects"], "a copy: the title travels with its shot"
            assert pg.locator("#inspector .ec-sec").count() >= 1
            # one undo takes the edits back, on the copy (the copy itself stays)
            pg.evaluate("document.activeElement && document.activeElement.blur()")
            pg.keyboard.press("Control+z")
            pg.wait_for_function("segs.length === 3", timeout=8000)
            assert pg.evaluate("tl.frameShape()") == "9:16"
            # and the first cut is still 16:9, three shots, when we go back to it
            pg.evaluate("""async (path) => { await switcher.openCut(path); }""", str(served["edl"]))
            pg.wait_for_function("typeof P !== 'undefined' && P && typeof tl !== 'undefined' && tl.frameShape() === '16:9' && segs.length === 3", timeout=30000)
            assert pg._errors == [], pg._errors
            browser.close()
    finally:
        inference.set_backend(None)
    assert served["edl"].read_bytes() == original


def test_a_copy_that_cannot_be_made_leaves_the_cut_alone(served):
    """The name is taken: the review says so, nothing is applied, the cut is as it was."""
    original = served["edl"].read_bytes()
    with sync_playwright() as pw:
        browser, pg = _open(pw, served)
        taken = pg.evaluate("fetch('/api/cuts/copy', {method: 'POST', headers: {'content-type': 'application/json'},"
                            " body: JSON.stringify({name: 'Instagram 20 s', open: false})}).then(r => r.status)")
        assert taken == 200
        res = {"job": "x1", "status": "done", "kind": "ops", "summary": "s", "ops": VERTICAL_ANSWER["ops"],
               "lines": ["a", "b", "c"], "index": [0, 1, 2], "families": ["timeline", "effect_controls"]}
        pg.evaluate("(r) => rcActions.review(r)", res)
        pg.wait_for_selector("[data-testid=ai-review] [data-testid=ai-op-row]")
        pg.locator("[data-testid=ai-accept-all]").click()
        pg.wait_for_function("document.querySelector('#toast').textContent.includes('no new cut')", timeout=15000)
        assert "there is already a cut" in pg.evaluate("document.querySelector('#toast').textContent")
        assert pg.evaluate("tl.frameShape()") == "16:9" and pg.evaluate("segs.length") == 3
        assert served["edl"].read_bytes() == original
        browser.close()
