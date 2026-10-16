"""tools/gate.py's perf and status rules, without running anything (wave 10 PERFGATE).

Verdict 7 punch 3: on a box shared with other loops (load ~14 on 6 cores) the perf test
skipped, the gate stayed AMBER and could never go green. Now the test runs there with 0
burners and the gate decides from its records:

  * the perf test runs alone before the suite and after it; the better-conditioned run
    that measured (the lower load before the test added anything, read by the test after
    its fixture's encodes drained) counts, ties to the earlier one, and the line says which
  * a pass is green wherever it ran; above 2x the cores the line says "shared box"
  * a fail above 2x the cores is AMBER and named; at or below 2x it is RED
  * nothing measured (ROUGHCUT_PERF_FORCE_SKIP=1, or no record) is AMBER
  * the run is GREEN only when every check is green and none amber
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import gate  # noqa: E402

CORES = 6


def rec(outcome: str, load_pre: float | None, launch: float | None = None, burners: int = 0,
        gap: float = 50.1, **kw) -> dict:
    r = {"outcome": outcome, "cores": CORES, "load_setup": load_pre, "burners": burners,
         "load_start": load_pre, "load_end": load_pre,
         "facts": {"play": {"longest_gap_ms": gap}, "seek": [66, 92, 155]}}
    if load_pre is not None:
        r["load_pre"] = load_pre
        r["shared"] = load_pre > 2 * CORES
    if launch is not None:
        r["load_launch"] = launch
    r.update(kw)
    return r


def skipped(forced: bool = True) -> dict:
    return {"outcome": "skipped", "forced": forced, "cores": CORES, "load_setup": 14.0,
            "reason": "forced: ROUGHCUT_PERF_FORCE_SKIP=1 (box at load 14.0 on 6 cores)"}


def verdict(before, after):
    return gate.perf_verdict([("before the suite", before), ("after the suite", after)], CORES)


def test_a_pass_on_a_shared_box_is_green_and_says_the_load():
    ok, amber, line = verdict(rec("passed", 14.2, launch=14.6), rec("passed", 15.1, launch=15.3))
    assert ok and not amber, line
    assert "passed at load 14.2 with 0 burners (shared box" in line, line
    assert "counted: before the suite (the better-conditioned run)" in line, line
    assert "worst gap 50.1 ms" in line and "seek median 92 ms" in line, line


def test_a_fail_on_a_shared_box_is_amber_and_named_not_red():
    ok, amber, line = verdict(rec("failed", 14.2, launch=14.0, gap=180.0),
                              rec("failed", 15.0, launch=15.2, gap=210.0))
    assert ok and amber, line
    assert "FAILED at load 14.2" in line and "shared box" in line, line
    assert "AMBER: it failed on a shared box" in line, line


def test_a_fail_at_or_below_twice_the_cores_is_red():
    for load in (12.0, 9.5, 3.0):
        ok, amber, line = verdict(rec("failed", load, launch=load, gap=120.0), None)
        assert not ok and not amber, (load, line)
        assert "shared box" not in line, line


def test_the_better_conditioned_run_counts_even_when_it_failed():
    # a fail at the quieter moment is not rescued by a pass at a busier one
    ok, amber, line = verdict(rec("passed", 14.5, launch=14.5), rec("failed", 9.0, launch=9.2))
    assert not ok and not amber, line
    assert "counted: after the suite" in line, line
    assert "before the suite: passed at load 14.5" in line, line


def test_a_pass_at_the_quieter_moment_beats_a_shared_fail():
    ok, amber, line = verdict(rec("failed", 15.0, launch=15.0, gap=150.0), rec("passed", 11.0, launch=11.4))
    assert ok and not amber, line
    assert "counted: after the suite" in line and "passed at load 11.0" in line, line


def test_a_tie_counts_the_earlier_run():
    ok, amber, line = verdict(rec("passed", 13.0, launch=13.0), rec("failed", 13.0, launch=13.0))
    assert ok and not amber, line
    assert "counted: before the suite" in line, line


def test_the_box_reads_from_the_test_s_own_settled_reading_else_the_gates():
    # the test's reading just before it measured (after letting its fixture drain) decides
    r = rec("failed", 13.0, launch=10.0, gap=120.0, settled_s=75.0)
    assert gate.perf_conditions(r) == 13.0 and gate.perf_shared(r, CORES)
    ok, amber, line = verdict(r, None)
    assert ok and amber and "after 75 s letting its fixture's encodes drain" in line, line
    # a record from an older rev (no load_pre): the gate's reading at launch
    old = rec("failed", None, launch=10.0, gap=120.0)
    assert gate.perf_conditions(old) == 10.0 and not gate.perf_shared(old, CORES)
    ok, amber, line = verdict(old, None)
    assert not ok and not amber, line


def test_a_skip_or_no_record_is_amber_never_green():
    for before, after in [(skipped(), skipped()), (None, None), (skipped(), None)]:
        ok, amber, line = verdict(before, after)
        assert ok and amber, line
        assert "AMBER: a skipped perf test is not a pass" in line, line
    ok, amber, line = verdict(skipped(), rec("passed", 8.0, launch=8.0))
    assert ok and not amber and "the only run that measured" in line, line


def test_an_older_revs_skip_record_still_reads():
    # a rev from before wave 10 skipped on its own above 2x the cores, with no load_pre
    old = {"outcome": "skipped", "forced": False, "cores": CORES, "load_setup": 14.43,
           "reason": "box already at load 14.4 on 6 cores before any burner: not a measurement of the app"}
    ok, amber, line = verdict(old, old)
    assert ok and amber and "SKIPPED at load 14.43" in line, line


def test_the_forced_skip_env_is_read_from_the_gate_or_its_env(monkeypatch):
    monkeypatch.delenv("ROUGHCUT_PERF_FORCE_SKIP", raising=False)
    assert not gate.perf_forced({})
    assert gate.perf_forced({"ROUGHCUT_PERF_FORCE_SKIP": "1"})
    assert not gate.perf_forced({"ROUGHCUT_PERF_FORCE_SKIP": "0"})
    monkeypatch.setenv("ROUGHCUT_PERF_FORCE_SKIP", "1")
    assert gate.perf_forced({})


@pytest.mark.parametrize("checks,amber,want", [
    ([("suite", True, ""), ("journey", True, "")], set(), ("GREEN", 0)),
    ([("suite", True, ""), ("journey", True, "")], {"suite"}, ("AMBER", 2)),
    ([("suite", False, ""), ("journey", True, "")], {"suite"}, ("RED", 1)),
    ([("suite", True, ""), ("hygiene", False, "")], set(), ("RED", 1)),
])
def test_the_run_status(checks, amber, want):
    assert gate.run_status(checks, amber) == want
