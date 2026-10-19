"""Wave 14 SERVERPERF (verdict 10, punch 1): a Designed Effects check never stalls the
server.

Verdict 10's "soft vignette" (8 stacked rings, a box of 1.6 frame widths, no animation)
was checked by drawing 240 identical 720p frames with PIL on a server thread — a
10 672-px canvas per shape per frame, ~5 s a frame, the GIL held ~250 ms at a time — for
12+ minutes at "0%", while a second identical check queued behind it. Here:

  * the renderer draws a frame once per run of identical frames, a static overlay is
    one PNG the part's graph loops (`-loop 1`), and only the frame's share of a sprite
    is drawn;
  * the check runs in a child process (`roughcut.fxcheck`) under the server's procscope,
    reports a real % with an ETA, and one check covers one change of an effect.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import fx  # noqa: E402

# verdict 10's vignette, as the designer wrote it (work/fx/…/fx_252c6f18.json)
VIGNETTE = {"duration": 2.0, "size": 1.6, "anim": {},
            "shapes": [{"type": "ring", "color": "#000000", "opacity": 0.032, "width": 0.08,
                        "at": [0.0, 0.0], "r": 1.5, "r2": r2}
                       for r2 in (0.3, 0.36, 0.42, 0.48, 0.54, 0.6, 0.66, 0.71)]}


def _alpha(img) -> np.ndarray:
    return np.asarray(img.convert("RGBA"))[:, :, 3]


# ------------------------------------------------------------ the renderer

def test_a_static_overlay_is_one_run_and_an_animated_one_is_not():
    vig = fx.validate_overlay(VIGNETTE)
    assert fx.overlay_static(vig, 24) and fx.overlay_runs(vig, 24) == [(0, 48)]
    pulse = fx.validate_overlay({**VIGNETTE, "anim": {"opacity": [[0, 0], [1.0, 1], [2.0, 1]]}})
    runs = fx.overlay_runs(pulse, 24)
    assert not fx.overlay_static(pulse, 24)
    # the fade-in is a frame each; the second it holds at 1 is one picture
    assert len(runs) == 25 and runs[-1] == (24, 24)
    # a title that types and then holds: a frame per character, then one
    title = fx.validate_overlay({"duration": 2.0, "size": 0.4, "shapes": [
        {"type": "text", "text": "HELLO", "reveal": "typewriter", "cps": 10}]})
    assert len(fx.overlay_runs(title, 24)) == 6
    # a flash is its own run
    hit = fx.validate_overlay({"duration": 0.5, "size": 0.1, "flash": {"duration": 0.1},
                               "shapes": [{"type": "circle", "r": 1}]})
    assert fx.overlay_runs(hit, 24) == [(0, 3), (3, 9)]


def test_a_static_overlay_reaches_the_part_as_one_looped_frame(tmp_path):
    """The proof's and the export's graph: one PNG, `-loop 1` for the overlay's
    duration, drawn once — not 48 PNGs packed into a .mov."""
    e = {"id": "fx_vig", "overlay": fx.validate_overlay(VIGNETTE), "sound": None}
    evs = [{"t_part": 0.5, "x": 0.5, "y": 0.5}]
    before = fx.RENDERED["frames"]
    extra, graph, vout, aout = fx.part_graph([(e, evs)], 640, 360, 24, tmp_path)
    assert fx.RENDERED["frames"] - before == 1
    assert extra[:6] == ["-loop", "1", "-framerate", "24", "-t", "2.0000"]
    assert extra[6] == "-i" and extra[7].endswith("fx_vig_ev00.png") and len(extra) == 8
    assert not list(tmp_path.glob("*.mov")) and not list(tmp_path.glob("*_frames_*"))
    assert "setpts=PTS-STARTPTS+0.5000/TB" in graph and "between(t,0.5000,2.5000)" in graph
    assert (vout, aout) == ("vout", "abase")
    # the export's rate as a fraction passes through as one
    extra2, _g, _v, _a = fx.part_graph([(e, evs)], 640, 360, "30000/1001", tmp_path / "x")
    assert extra2[3] == "30000/1001"
    assert fx._fps_text(29.97002997) == "30000/1001" and fx._fps_text(25) == "25"


def test_frames_that_repeat_are_drawn_once_and_linked(tmp_path):
    pulse = fx.validate_overlay({"duration": 0.5, "size": 0.2, "shapes": [{"type": "circle", "r": 1}],
                                 "anim": {"scale": [[0, 2.0], [0.125, 1.0]]}})
    before = fx.RENDERED["frames"]
    paths = fx.render_overlay_frames(pulse, 320, 180, 24, tmp_path / "f")
    assert len(paths) == 12 and [p.name for p in paths][:2] == ["f_0000.png", "f_0001.png"]
    runs = fx.overlay_runs(pulse, 24)
    assert fx.RENDERED["frames"] - before == len(runs) == 4          # 3 scaling, then held
    assert all(p.stat().st_ino == paths[3].stat().st_ino for p in paths[3:])
    from PIL import Image
    assert np.array_equal(_alpha(fx.render_overlay_frame(pulse, 320, 180, 11 / 24)),
                          _alpha(Image.open(paths[11])))


def test_only_the_frames_share_of_a_big_sprite_is_drawn():
    """The vignette's box is 1.6 frame widths: its sprite square was ~5 300 px at 720p
    (×2 supersampled, per shape). Drawn now is the frame plus a few pixels — and the
    picture is the vignette: the centre clear, the corners darkened."""
    vig = fx.validate_overlay(VIGNETTE)
    t0 = time.perf_counter()
    frame = fx.render_overlay_frame(vig, 1280, 720, 0.0)
    took = time.perf_counter() - t0
    a = _alpha(frame)
    assert a.shape == (720, 1280)
    assert a[360, 640] == 0 and a[:40, :40].mean() > 30 and a[-40:, -40:].mean() > 30
    assert took < 3.0, f"{took:.2f} s for one 720p vignette frame"      # was ~5 s, measured 0.25 s


def test_a_sprite_cut_by_the_frame_edge_draws_as_it_did(tmp_path):
    """Windowed drawing is the same picture: a marker half off the frame's corner, turned
    and fading, against the whole sprite drawn and cropped (the old way)."""
    from PIL import Image
    ov = fx.validate_overlay({"duration": 0.1, "size": 0.3, "shapes": [
        {"type": "rect", "at": [0.3, 0], "w": 1.2, "h": 0.4, "fill": True},
        {"type": "ring", "at": [0, 0], "r": 1, "r2": 0.6, "color": "#ff0000"}],
        "anim": {"rotate": [[0, 30]], "opacity": [[0, 0.7]]}})
    w, h, anchor = 320, 180, (0.97, 0.05)
    got = fx.render_overlay_frame(ov, w, h, 0.0, anchor=anchor)
    pose = fx.pose_at(ov, 0.0)
    sprite, ext, (left, top) = fx._sprite_image(ov, ov["size"] * w, pose, 0.0)
    assert (left, top) == (0, 0) and sprite.width == 2 * ext
    ref = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    x0, y0 = int(round(anchor[0] * w)) - ext, int(round(anchor[1] * h)) - ext
    l, t = max(0, -x0), max(0, -y0)
    part = sprite.crop((l, t, min(sprite.width, w - x0), min(sprite.height, h - y0)))
    ref.alpha_composite(part, dest=(x0 + l, y0 + t))
    diff = np.abs(np.asarray(got).astype(int) - np.asarray(ref).astype(int))
    assert diff.max() <= 2, diff.max()


def test_an_accepted_vignette_exports_through_the_looped_frame(project, tmp_path):
    """The export's part graph is the proof's: `assemble.py` renders the accepted static
    vignette from its one looped PNG — corners darker than the film without it, the
    centre the same, the part's length unchanged."""
    import subprocess
    tools = Path(__file__).resolve().parents[2] / "research" / "tools"
    segs = [{"id": "s1", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"}]
    vig = {"id": "fx_vig0001", "shot": "s1", "name": "soft vignette", "status": "accepted",
           "events": [{"t": 1.0, "x": 0.5, "y": 0.5}], "overlay": {**VIGNETTE, "duration": 2.0},
           "sound": None}
    films, logs = {}, {}
    for name, effects in (("with", [fx.validate_effect(vig, segs)]), ("without", [])):
        edl = {"variant": "F", "title": "vignette", "orient": "none", "target_s": [1, 20],
               "segments": segs, **({"effects": effects} if effects else {})}
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(edl), encoding="utf-8")
        out, parts = tmp_path / f"{name}.mp4", tmp_path / f"{name}_parts"
        r = subprocess.run(["uv", "run", "--quiet", str(tools / "assemble.py"), str(path),
                            "--footage", str(project["footage"]), "--sidecars", str(project["sidecars"]),
                            "--parts-dir", str(parts), "--keep-parts", "-o", str(out)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        films[name], logs[name] = parts / "part_000.mp4", r.stdout
    assert "fx: soft vignette" in logs["with"], logs["with"]

    def luma(p: Path, t: float) -> np.ndarray:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(p), "-frames:v", "1",
                              "-vf", "scale=160:90,format=gray", "-f", "rawvideo", "-"],
                             capture_output=True, check=True).stdout
        return np.frombuffer(raw, dtype=np.uint8).reshape(90, 160).astype(float)

    a, b = luma(films["with"], 1.0), luma(films["without"], 1.0)
    assert abs(a[35:55, 60:100].mean() - b[35:55, 60:100].mean()) < 3          # the centre clear
    assert b[:12, :16].mean() - a[:12, :16].mean() > 4                          # a corner darker
    probe = [subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                             str(films[k])], capture_output=True, text=True).stdout for k in ("with", "without")]
    assert abs(float(probe[0]) - float(probe[1])) < 0.05
