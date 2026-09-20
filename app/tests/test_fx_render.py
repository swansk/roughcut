"""INTAKE M12 — an accepted effect reaches the film through `assemble.py`.

A two-shot EDL on the suite's synthetic bin, one accepted hit-marker effect on the
first shot (two events) and one still-proposed effect on the second, rendered the way
the board renders — `assemble.py` as a subprocess — twice: with the EDL's `effects`
and without. The parts are then measured with the same pipes `fx.verify` uses: the
first part's audio rises at each event and its picture changes at the anchor, the
second part is untouched (a proposal is not in the film), the parts share stream
properties so the concat copies, and the film probes at the planned duration.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import fx  # noqa: E402

TOOLS = Path(__file__).resolve().parents[2] / "research" / "tools"

X_LINES = [{"type": "line", "from": [-1, -1], "to": [-0.3, -0.3], "width": 0.1},
           {"type": "line", "from": [1, -1], "to": [0.3, -0.3], "width": 0.1},
           {"type": "line", "from": [-1, 1], "to": [-0.3, 0.3], "width": 0.1},
           {"type": "line", "from": [1, 1], "to": [0.3, 0.3], "width": 0.1}]
OVERLAY = {"duration": 0.35, "size": 0.12, "shapes": X_LINES,
           "anim": {"scale": [[0, 1.4], [0.06, 1.0]], "opacity": [[0, 1], [0.23, 1], [0.35, 0]]},
           "flash": {"color": "#ff0000", "opacity": 0.15, "duration": 0.08}}
SOUND = {"duration": 0.18, "gain_db": -6,
         "layers": [{"type": "tone", "freq": 1800, "wave": "square", "decay": 0.06},
                    {"type": "noise", "color": "white", "hp": 1500, "decay": 0.09}]}


def _edl(with_effects: bool) -> dict:
    segments = [{"id": "s1", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
                {"id": "s2", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}]
    edl = {"variant": "F", "title": "fx test", "orient": "none", "target_s": [1, 20],
           "segments": segments}
    if with_effects:
        accepted = {"id": "fx_accept01", "shot": "s1", "name": "hit markers", "status": "accepted",
                    "events": [{"t": 1.5, "x": 0.5, "y": 0.7}, {"t": 2.4, "x": 0.55, "y": 0.72},
                               {"t": 4.5, "x": 0.5, "y": 0.5}],       # outside the shot: not drawn
                    "overlay": OVERLAY, "sound": SOUND}
        proposed = {"id": "fx_propose1", "shot": "s2", "name": "not yet", "status": "proposed",
                    "events": [{"t": 1.0, "x": 0.5, "y": 0.5}], "overlay": OVERLAY, "sound": SOUND}
        edl["effects"] = [fx.validate_effect(accepted, segments), fx.validate_effect(proposed, segments)]
    return edl


def _render(edl: dict, root: Path, name: str, project: dict) -> tuple[Path, Path, str]:
    edl_path = root / f"{name}.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    out, parts = root / f"{name}.mp4", root / f"{name}_parts"
    cmd = ["uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(edl_path),
           "--footage", str(project["footage"]), "--sidecars", str(project["sidecars"]),
           "--parts-dir", str(parts), "--keep-parts", "-o", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return out, parts, r.stdout


def _probe(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration:stream=codec_type,codec_name,pix_fmt,width,height,"
                        "r_frame_rate,sample_rate,channels,color_range,color_space",
                        "-of", "json", str(path)], capture_output=True, text=True)
    return json.loads(r.stdout)


@pytest.fixture(scope="module")
def renders(project, tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("fxrender")
    with_out, with_parts, log = _render(_edl(True), root, "with", project)
    base_out, base_parts, _ = _render(_edl(False), root, "base", project)
    return {"film": with_out, "parts": with_parts, "log": log,
            "base_film": base_out, "base_parts": base_parts}


def test_the_effect_lands_in_its_part_and_nowhere_else(renders):
    part, base = renders["parts"] / "part_000.mp4", renders["base_parts"] / "part_000.mp4"
    assert part.exists() and base.exists()
    assert "fx: hit markers × 2 at 0.50s, 1.40s" in renders["log"], renders["log"]
    # the audio rises ≥ 6 dB at each event (the two inside the shot; 4.5 s is outside)
    for t_part in (0.5, 1.4):
        m = fx.audio_transient_at(part, t_part)
        b = fx.audio_transient_at(base, t_part)
        assert m["rise_db"] >= 6.0, (t_part, m)
        assert m["peak_db"] - b["peak_db"] >= 6.0, (t_part, m, b)
    assert abs(fx.audio_transient_at(part, 1.0)["rise_db"]) < 2.0
    # the frame changes where the anchor is while the marker shows, not before it
    # (before the event only x264's own noise between two encodes remains — a few
    # tenths of a percent on testsrc2's random blocks; the marker is well above it)
    before = fx.frame_change_at(part, base, 0.2)["changed"]
    assert before < 0.01, before
    for t_part, (ax, ay) in ((0.5, (0.5, 0.7)), (1.4, (0.55, 0.72))):
        m = fx.frame_change_at(part, base, t_part + 0.1)
        assert m["changed"] >= 0.0002 and m["changed"] > 2 * before and m["bbox"], (t_part, m, before)
        x0, y0, x1, y1 = m["bbox"]
        assert x0 <= ax <= x1 and y0 <= ay <= y1, (t_part, m)
    # the proposal on the second shot is not in the film
    part1, base1 = renders["parts"] / "part_001.mp4", renders["base_parts"] / "part_001.mp4"
    assert abs(fx.audio_transient_at(part1, 1.0)["rise_db"]) < 2.0
    assert fx.frame_change_at(part1, base1, 1.1)["changed"] < 0.01
    assert "fx:" not in renders["log"].split("01 CLIP_B.MP4")[1]


def test_the_parts_still_concat_by_copy_at_the_planned_duration(renders):
    p0, p1 = _probe(renders["parts"] / "part_000.mp4"), _probe(renders["parts"] / "part_001.mp4")
    keys = ("codec_name", "pix_fmt", "width", "height", "r_frame_rate", "sample_rate", "channels",
            "color_range", "color_space")
    for kind in ("video", "audio"):
        a = next(s for s in p0["streams"] if s["codec_type"] == kind)
        b = next(s for s in p1["streams"] if s["codec_type"] == kind)
        assert {k: a.get(k) for k in keys} == {k: b.get(k) for k in keys}, (a, b)
    v = next(s for s in p0["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"], v["pix_fmt"], v["r_frame_rate"]) == (1920, 1080, "yuv420p", "24000/1001")
    film = _probe(renders["film"])
    assert abs(float(film["format"]["duration"]) - 4.0) < 0.15
    base = _probe(renders["base_film"])
    assert abs(float(film["format"]["duration"]) - float(base["format"]["duration"])) < 0.05
    assert "drift" in renders["log"] and "warning" not in renders["log"]
    # the sound's own transient is in the film at film time: shot 1 starts the cut
    assert fx.audio_transient_at(renders["film"], 0.5)["rise_db"] >= 6.0
    assert fx.audio_transient_at(renders["film"], 2.0 + 1.0)["rise_db"] < 2.0
