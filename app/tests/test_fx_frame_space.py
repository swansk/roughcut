"""Wave 23 — a title is in the FRAME's space (cold editor COLD-08 / -09: a title on a shot
turned by hand in Effect Controls came out sideways, twice, "redrawn level" on the card).

The shot's own Motion (rotation, scale, position) moves the picture. A designed effect that
has text is drawn AFTER it: level, at its own size, where its spec says, whatever the shot
under it does; an effect without text (a marker on a rock) is still on the picture and
moves with it. Pinned in the export (assemble.py, a real render of a turned and scaled shot
with a title on it) and in the monitor (fx.js draws titles on `#fxFrame`, which Motion never
touches). The proof and the frame check draw the unmoved shot, which is the frame.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import fx  # noqa: E402
from test_transitions import EDL, FPS, H, SEGS, W, proj, render  # noqa: E402,F401

TITLE = {"duration": 1.5, "size": 0.8, "anim": {}, "shapes": [
    {"type": "text", "text": "TITLE WORDS", "at": [0, 0], "h": 0.12, "bold": True, "color": "#ffffff"}]}
MARKER = {"duration": 1.5, "size": 0.2, "anim": {}, "shapes": [
    {"type": "circle", "at": [0, 0], "r": 0.8, "fill": True, "color": "#ffffff"}]}


def _effect(overlay, x, y, eid):
    e = {"id": eid, "shot": "s1", "clip": "CLIP_A.MP4", "name": eid, "note": "", "why": "",
         "events": fx.validate_events([{"t": 1.2, "x": x, "y": y}], None),
         "overlay": fx.validate_overlay(overlay), "sound": None, "status": "accepted"}
    return e


def _white_box(path: Path, t: float):
    n = int(t * FPS + 1e-6)
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-vf", f"select=eq(n\\,{n})",
                        "-frames:v", "1", "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       capture_output=True, check=True)
    a = np.frombuffer(r.stdout, np.uint8).reshape(H, W, 3).astype(int)
    m = (a[:, :, 0] > 200) & (a[:, :, 1] > 200) & (a[:, :, 2] > 200)
    ys, xs = np.where(m)
    if not len(xs):
        return None
    return [xs.min() / W, ys.min() / H, (xs.max() + 1) / W, (ys.max() + 1) / H]


def _export(proj, effects, controls, name):
    segs = [{**SEGS[0], "controls": controls}] if controls else [dict(SEGS[0])]
    edl = {**EDL, "segments": segs, "effects": effects}
    out, _ = render(proj, edl, name)
    return out


def test_a_title_on_a_turned_and_scaled_shot_is_level_and_where_its_spec_says_in_the_file(proj):
    e = _effect(TITLE, 0.5, 0.5, "fx_title")
    want = fx.measure(e, W, H)["views"][0]["all"]
    still = _white_box(_export(proj, [e], None, "fs_plain"), 0.5)
    turned = _white_box(_export(proj, [e], {"rotation": 90, "scale": 150}, "fs_turned"), 0.5)
    assert still is not None and turned is not None
    # the box the spec draws, on the unmoved shot and on the turned one: the same, level
    assert still == pytest.approx(want, abs=0.03)
    assert turned == pytest.approx(still, abs=0.02)
    assert (turned[2] - turned[0]) > 2 * (turned[3] - turned[1])      # wide and low, not turned up on its end


def test_a_marker_without_text_still_moves_with_the_shot(proj):
    e = _effect(MARKER, 0.25, 0.5, "fx_marker")
    still = _white_box(_export(proj, [e], None, "fm_plain"), 0.5)
    turned = _white_box(_export(proj, [e], {"rotation": 180, "scale": 100}, "fm_turned"), 0.5)
    assert still is not None and turned is not None
    assert still[0] < 0.4 and turned[0] > 0.6                           # turned half way round, it is on the other side


def test_frame_space_is_an_effect_with_text():
    assert fx.frame_space(_effect(TITLE, 0.5, 0.5, "a")) is True
    assert fx.frame_space(_effect(MARKER, 0.5, 0.5, "b")) is False


def test_part_graph_inputs_and_labels_do_not_collide_when_called_twice(tmp_path):
    a = (_effect(MARKER, 0.5, 0.5, "m"), [{"t_part": 0.5, "x": 0.5, "y": 0.5}])
    b = (_effect(TITLE, 0.5, 0.5, "t"), [{"t_part": 0.5, "x": 0.5, "y": 0.5}])
    e1, g1, v1, _ = fx.part_graph([a], W, H, 24, tmp_path)
    e2, g2, v2, _ = fx.part_graph([b], W, H, 24, tmp_path, vin=v1, first_input=1 + e1.count("-i"), tag="f")
    assert v1 == "vout" and v2 == "fvout"
    assert "[2:v]" in g2 and "[1:v]" not in g2 and "fxv2" in g2


def test_the_monitor_draws_titles_on_a_canvas_the_shots_motion_does_not_move():
    src = (Path(__file__).resolve().parents[1] / "static" / "fx.js").read_text(encoding="utf-8")
    assert "fxFrame" in src and "frameSpace" in src and "drawGuides(ctx, W, H)" in src
    css = (Path(__file__).resolve().parents[1] / "static" / "workspace.css").read_text(encoding="utf-8")
    assert "#fxFrame" in css
    eff = (Path(__file__).resolve().parents[1] / "static" / "effects.js").read_text(encoding="utf-8")
    assert "OVER = ['#fxCanvas']" in eff                              # Motion moves the picture's effects only
