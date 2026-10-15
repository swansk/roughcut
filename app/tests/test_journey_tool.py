"""tools/journey.py's contract with tools/gate.py, without a browser.

The journey itself is driven by the gate on the frozen snapshot (its `journey` line);
running all thirteen steps again inside the suite would only spend a minute on what the
gate already measures. What the gate relies on is checked here: a usage error is rc 2,
anything that stops the journey is rc 1 with a `journey ✗ step K <name>: <why>` line and
a journey.json that says so, and gate.journey() turns that into its summary line.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

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
    assert (tmp_path / "journey.log").read_text().strip().splitlines()[-1] == "journey ✗ " + line
