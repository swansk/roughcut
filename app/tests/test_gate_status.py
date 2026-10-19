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
  * a journey step that passed only on its retry after a Chromium crash is AMBER (verdict 10)
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


def test_the_perf_line_names_the_graded_canvas():
    """Verdict 8 punch 2: the line says what the graded canvas did, beside the live gap."""
    r = rec("passed", 8.0)
    r["facts"]["canvas"] = {"1440": {"longest_ms": 50.2, "fps": 29.6}, "1920": {"longest_ms": 60.1, "fps": 29.9}}
    assert "graded canvas worst gap 50.2 ms at 29.6 fps (1440), 60.1 ms at 29.9 fps (1920)" \
        in gate.perf_said(r, CORES), gate.perf_said(r, CORES)
    assert "graded canvas not measured" in gate.perf_said(rec("passed", 8.0), CORES)


# ---------------------------------------------------------------- the journey's retry
#
# Verdict 10's ruling (REDESIGN-GOAL, 2026-10-18): a journey step retried after a Chromium
# crash ended GREEN, with only a note in its line. A page can crash its own renderer, so
# an app bug that crashes half the time would pass three times in four. Now such a
# journey is AMBER, named; a GREEN journey means no step was retried.

RETRIED = {"n": 2, "name": "firstpass", "ok": True,
           "words": "the page's renderer crashed (the browser still up · no FATAL line)"}
SLOW = {"slowest_api": {"ms": 140, "method": "GET", "path": "/api/state"}}


def _journey_json(tmp_path: Path, line: str, retried: list | None, **kw) -> Path:
    import json
    out = tmp_path / "journey"
    out.mkdir()
    rec = {"ok": line.startswith("journey ✓"), "line": line, "slow": SLOW, **kw}
    if retried is not None:
        rec["retried"] = retried
    (out / "journey.json").write_text(json.dumps(rec))
    return out


def test_a_journey_with_no_retry_is_green(tmp_path):
    out = _journey_json(tmp_path, "journey ✓ 24 steps · 53 s", [])
    green, line, amber = gate.journey_read(out, tmp_path / "journey.log", 0)
    assert green and not amber, line
    assert line == "24 steps · 53 s · slowest request 0.14 s GET /api/state"
    assert gate.run_status([("journey", green, line)], {"journey"} if amber else set()) == ("GREEN", 0)


def test_a_retried_step_makes_the_journey_amber_and_names_it(tmp_path):
    said = (f"journey ✓ 24 steps · 61 s · step 2 firstpass retried once after {RETRIED['words']}"
            " · AMBER: 1 step passed only on a retry after Chromium crashed: step 2 firstpass")
    out = _journey_json(tmp_path, said, [RETRIED])
    green, line, amber = gate.journey_read(out, tmp_path / "journey.log", 0)
    assert green and amber, line
    # the step, and why it is not a pass
    assert line.endswith("→ the journey is AMBER: step 2 firstpass passed only on a retry after"
                         " Chromium crashed (verdict 10: a page can crash its own renderer),"
                         " not proven"), line
    assert f"step 2 firstpass retried once after {RETRIED['words']}" in line
    # the run: AMBER, never GREEN
    assert gate.run_status([("suite", True, ""), ("journey", green, line)], {"journey"}) == ("AMBER", 2)


def test_two_retried_steps_are_both_named(tmp_path):
    two = [RETRIED, {**RETRIED, "n": 15, "name": "rerun-panel"}]
    out = _journey_json(tmp_path, "journey ✓ 24 steps · 70 s", two)
    green, line, amber = gate.journey_read(out, tmp_path / "journey.log", 0)
    assert amber and "AMBER: step 2 firstpass, step 15 rerun-panel passed only on a retry" in line, line


def test_a_retry_that_failed_is_red_not_amber(tmp_path):
    said = f"journey ✗ step 2 firstpass: {RETRIED['words']}; retried: ✗ TimeoutError: x"
    out = _journey_json(tmp_path, said, [{**RETRIED, "ok": False}])
    green, line, amber = gate.journey_read(out, tmp_path / "journey.log", 1)
    assert not green and not amber, line
    assert gate.run_status([("journey", green, line)], set()) == ("RED", 1)


def test_a_record_without_the_retried_list_reads_the_line(tmp_path):
    """A journey.json from before the list (or a log line alone): its words still count."""
    said = f"journey ✓ 24 steps · 61 s · step 7 razor retried once after {RETRIED['words']}"
    out = _journey_json(tmp_path, said, None)
    green, line, amber = gate.journey_read(out, tmp_path / "journey.log", 0)
    assert green and amber and "AMBER: step 7 razor passed only on a retry" in line, line
    # no journey.json at all: the log's last line
    log = tmp_path / "journey.log"
    log.write_text("  07 razor ✓ …\n" + said + "\n")
    green, line, amber = gate.journey_read(tmp_path / "no-such-dir", log, 0)
    assert green and amber, line
