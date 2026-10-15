"""tools/journey.py's contract with tools/gate.py, without a browser.

The journey itself is driven by the gate on the frozen snapshot (its `journey` line);
running all nineteen steps again inside the suite would only spend a minute or two on
what the gate already measures. What the gate relies on is checked here: a usage error is rc 2,
anything that stops the journey is rc 1 with a `journey ✗ step K <name>: <why>` line and
a journey.json that says so, and gate.journey() turns that into its summary line. And
the GoPro-shaped fixture is what the upright steps need it to be: a picture read the
right way up passes, the same picture as stored (upside down) fails.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JOURNEY = ROOT / "tools" / "journey.py"


def test_no_arguments_is_a_usage_error():
    r = subprocess.run([sys.executable, str(JOURNEY)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 2
    assert "journey.py <snapshot dir> <out dir>" in r.stdout


def test_a_snapshot_it_cannot_set_up_is_red_at_step_1_with_a_reason(tmp_path):
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, str(JOURNEY), str(tmp_path / "no-such-snapshot"), str(out)],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 1, r.stdout + r.stderr
    last = r.stdout.strip().splitlines()[-1]
    assert last.startswith("journey ✗ step 1 setup: "), last
    rec = json.loads((out / "journey.json").read_text())
    assert rec["ok"] is False and rec["line"] == last and rec["steps"] == []


def test_the_gate_reads_the_journeys_line(tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import gate

    empty = tmp_path / "empty-snapshot"         # a snapshot with no board in it
    empty.mkdir()
    green, line = gate.journey(empty, tmp_path / "j",
                               tmp_path / "journey.log", env=None, timeout=300)
    assert not green
    assert line.startswith("step 1 setup: "), line
    # the gate adds what it measured on the server (wave 8 SAVESAFE): nothing, with no board
    said, measured = line.split(" · slowest request", 1)
    assert measured == ": not measured (the server has no /api/debug/slow)", measured
    assert (tmp_path / "journey.log").read_text().strip().splitlines()[-1] == "journey ✗ " + said


def _journey_module():
    spec = importlib.util.spec_from_file_location("journey_tool_under_test", JOURNEY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_rotated_fixture_reads_upright_only_when_its_matrix_is_applied(tmp_path):
    np = pytest.importorskip("numpy")
    j = _journey_module()
    clip = tmp_path / j.ROT
    facts = j.make_rotated_clip(clip, tmp_path / "raw")
    assert facts["codec"] == "hevc" and facts["r_frame_rate"] == "60000/1001"
    assert abs(abs(float(facts["rotation"])) - 180) < 0.5, facts
    # displayed (ffmpeg applies the matrix, as a correct monitor must): upright
    seen = j.quadrants(j.file_frame_rgb(clip, 1.0), "the displayed frame")
    assert {k: v["looks"] for k, v in seen.items()} == {k: k for k in j.QUADS}
    # stored (the matrix ignored, the pre-ORIENT bug): every quadrant reads wrong
    raw = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-noautorotate", "-ss", "1",
                          "-i", str(clip), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    stored = np.frombuffer(raw, np.uint8).reshape(180, 320, 3)
    with pytest.raises(j.StepFailed, match="is not upright: tl looks br"):
        j.quadrants(stored, "the stored frame")
