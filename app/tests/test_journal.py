"""The index journal (INTAKE I3.1): stages per clip, files as truth, resume for free.

Everything here is about decision 3 — unattended, resumable, priority-ordered, released
whole — and decision 4 — clips arrive whenever. The module is pure, so every test drives
it with an injected clock and a dict describing the disk; the only real I/O is the
journal file itself, and the crash test uses that on purpose.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import journal as J  # noqa: E402
from roughcut.journal import Journal, JournalError  # noqa: E402

FREE = ("probe", "telemetry", "asr")
ALL_FILES = {"asr": True, "proxy": True, "look": True, "close": True, "picks": True}
NO_FILES = {"asr": False, "proxy": False, "look": False, "close": False, "picks": False}


def _journal(tmp_path: Path, *clips: str, **kw) -> Journal:
    j = Journal(tmp_path / "bin.json", **kw)
    j.add_clips(clips, now=0.0)
    return j


def _run(j: Journal, clip: str, stage: str, *, t: float, cost: float = 0.0,
         dur: float = 1.0) -> None:
    j.start(clip, stage, now=t)
    j.finish(clip, stage, cost_usd=cost, now=t + dur)


def _listen(j: Journal, clip: str, *, t: float = 0.0, facts: dict | None = None) -> None:
    """The free stages, done — the point at which a clip gets its priority."""
    for i, s in enumerate(FREE):
        _run(j, clip, s, t=t + i)
    j.set_facts(clip, facts or {})


def _drain(j: Journal, *, t: float = 100.0, cost: float = 0.0) -> list[tuple[str, str]]:
    """Run everything runnable, one job at a time, and return the order it ran in."""
    ran = []
    while (job := j.next(now=t)) is not None:
        _run(j, *job, t=t, cost=cost)
        ran.append(job)
        t += 1.0
    return ran


# --------------------------------------------------------------- persistence

def test_journal_saves_atomically_and_round_trips(tmp_path):
    j = _journal(tmp_path, "A.MP4", "B.MP4")
    j.start("A.MP4", "probe", now=1.0)
    path = tmp_path / "bin.json"
    assert path.exists()
    assert not (tmp_path / "bin.json.tmp").exists(), "temp file must be replaced, not left"
    assert json.loads(path.read_text())["clips"]["A.MP4"]["stages"]["probe"]["state"] == "running"

    again = Journal.load(path)
    assert again.state("A.MP4", "probe") == "running"
    assert again.state("B.MP4", "probe") == "queued"
    assert set(again.clips) == {"A.MP4", "B.MP4"}
    assert again.workers == J.DEFAULT_WORKERS


def test_load_of_a_missing_file_is_a_fresh_journal_and_a_corrupt_one_raises(tmp_path):
    fresh = Journal.load(tmp_path / "none.json")
    assert fresh.clips == {} and fresh.progress()["clips"] == 0
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(JournalError):
        Journal.load(bad)


# --------------------------------------------------------------- the crash

def test_crash_mid_look_then_reload_requeues_without_double_counting_cost(tmp_path):
    """The design's own example: crashed during a sheet, resumed, one sheet re-read,
    nothing else re-bought — and the journal's cost is what was actually paid."""
    path = tmp_path / "bin.json"
    j = _journal(tmp_path, "A.MP4")
    _listen(j, "A.MP4")
    _run(j, "A.MP4", "proxy", t=10.0)
    j.start("A.MP4", "look", now=20.0)          # ...and the process dies here
    del j

    back = Journal.load(path)
    assert back.state("A.MP4", "look") == "running", "the file says running until reconciled"
    changed = back.reconcile({"A.MP4": {**NO_FILES, "asr": True, "proxy": True}}, now=30.0)
    assert changed["requeued"] == [("A.MP4", "look")]
    assert back.state("A.MP4", "look") == "queued"
    assert back.clips["A.MP4"]["stages"]["look"]["attempts"] == 1, "the crashed try counts"
    assert back.state("A.MP4", "proxy") == "done" and back.state("A.MP4", "asr") == "done"
    assert back.cost_usd() == 0.0, "a crashed sheet was never recorded as paid"

    assert back.next(now=30.0) == ("A.MP4", "look"), "resume = the same stage, once"
    _run(back, "A.MP4", "look", t=30.0, cost=0.09)
    assert back.cost_usd() == pytest.approx(0.09)
    assert back.clips["A.MP4"]["stages"]["look"]["attempts"] == 2


def test_reconcile_marks_found_files_done_and_lost_files_queued(tmp_path):
    j = _journal(tmp_path, "A.MP4")
    _listen(j, "A.MP4")
    _run(j, "A.MP4", "proxy", t=10.0, cost=0.0)
    _run(j, "A.MP4", "look", t=20.0, cost=0.09)
    # someone deleted the proxy and, separately, a close look appeared from an older run
    changed = j.reconcile({"A.MP4": {"asr": True, "proxy": False, "look": True,
                                     "close": True, "picks": False}})
    assert changed["lost"] == [("A.MP4", "proxy")]
    assert changed["found"] == [("A.MP4", "close")]
    assert j.state("A.MP4", "proxy") == "queued"
    assert j.state("A.MP4", "close") == "done"
    assert j.state("A.MP4", "look") == "done"
    assert j.clips["A.MP4"]["stages"]["look"]["cost_usd"] == pytest.approx(0.09), \
        "reconcile never touches money"
    # a stage the server does not report on is left alone unless it was running
    assert j.state("A.MP4", "telemetry") == "done"


def test_reconcile_requeues_a_running_free_stage_it_has_no_file_for(tmp_path):
    j = _journal(tmp_path, "A.MP4")
    j.start("A.MP4", "probe", now=1.0)
    changed = j.reconcile({"A.MP4": NO_FILES})
    assert changed["requeued"] == [("A.MP4", "probe")]
    assert j.state("A.MP4", "probe") == "queued"


# ------------------------------------------------------------- added clips

def test_a_clip_added_mid_run_is_queued_and_ordered_by_its_priority(tmp_path):
    j = _journal(tmp_path, "A.MP4", "B.MP4")
    _listen(j, "A.MP4", facts={"candidates": 8, "words": 200})
    _listen(j, "B.MP4", facts={"candidates": 1, "words": 20})
    _run(j, "A.MP4", "proxy", t=10.0)
    j.start("A.MP4", "look", now=20.0)

    new = j.add_clips(["C.MP4", "A.MP4"], captured={"C.MP4": 5.0}, now=21.0)
    assert new == ["C.MP4"], "re-adding a known clip is not an addition"
    assert all(j.state("C.MP4", s) == "queued" for s in J.STAGES)
    assert j.state("A.MP4", "look") == "running", "nothing about existing clips re-runs"
    assert j.priority("C.MP4") is None
    # unscored: sorts after the scored clips, and its free stages still run at once
    assert j.ordered() == ["A.MP4", "B.MP4", "C.MP4"]
    assert j.next(now=21.0) == ("B.MP4", "proxy")      # proxy pool is free for B
    j.start("B.MP4", "proxy", now=21.0)
    assert j.next(now=21.0) == ("C.MP4", "probe")      # C's free stages need no waiting

    _listen(j, "C.MP4", t=22.0, facts={"candidates": 10, "theme_hits": 2, "words": 400})
    assert j.priority("C.MP4") > j.priority("A.MP4") > j.priority("B.MP4")
    assert j.ordered() == ["C.MP4", "A.MP4", "B.MP4"], "scored, it jumps the queue"
    assert j.next(now=30.0) is None, "C's proxy waits for the single proxy worker"
    j.finish("B.MP4", "proxy", now=31.0)
    assert j.next(now=31.0) == ("C.MP4", "proxy")


def test_remove_missing_keeps_the_record_and_adding_back_restores_it(tmp_path):
    j = _journal(tmp_path, "A.MP4", "B.MP4")
    _listen(j, "A.MP4")
    _run(j, "A.MP4", "proxy", t=10.0)
    _run(j, "A.MP4", "look", t=20.0, cost=0.09)
    assert j.remove_missing(["A.MP4"]) == ["A.MP4"]
    assert j.clips["A.MP4"]["missing"] is True
    assert j.state("A.MP4", "look") == "done" and j.cost_usd() == pytest.approx(0.09)
    assert j.next(now=30.0) == ("B.MP4", "probe"), "a missing clip is never offered"
    assert not j.released("A.MP4")
    assert j.progress()["missing"] == 1
    j.add_clips(["A.MP4"])
    assert j.clips["A.MP4"]["missing"] is False
    assert j.state("A.MP4", "look") == "done", "back, with everything it had paid for"
    with pytest.raises(JournalError):
        j.remove_missing(["nope.MP4"])


# ---------------------------------------------------------------- failure

def test_parked_after_three_failures_and_the_rest_continues(tmp_path):
    j = _journal(tmp_path, "A.MP4", "B.MP4")
    for c in ("A.MP4", "B.MP4"):
        _listen(j, c)
    for n in (1, 2):
        j.start("A.MP4", "proxy", now=10.0 * n)
        assert j.fail("A.MP4", "proxy", f"encode failed {n}", now=10.0 * n + 1) == "failed"
        assert j.state("A.MP4", "proxy") == "failed"
        assert j.next(now=10.0 * n + 1) == ("A.MP4", "proxy"), "failed is retryable"
    j.start("A.MP4", "proxy", now=30.0)
    assert j.fail("A.MP4", "proxy", "encode failed 3", now=31.0) == "parked"
    assert j.state("A.MP4", "proxy") == "parked"
    parked = j.clips["A.MP4"]["parked"]
    assert parked["stage"] == "proxy" and "encode failed 3" in parked["error"]
    assert parked["attempts"] == 3
    assert not j.released("A.MP4")
    # the rest continues: B gets the proxy worker, A is never offered again
    assert j.next(now=31.0) == ("B.MP4", "proxy")
    _drain(j, t=40.0)
    assert j.released("B.MP4") and not j.released("A.MP4")
    assert j.progress()["parked"] == 1
    with pytest.raises(JournalError):
        j.start("A.MP4", "proxy", now=50.0)

    j.unpark("A.MP4", now=60.0)
    assert j.state("A.MP4", "proxy") == "queued"
    assert j.clips["A.MP4"]["stages"]["proxy"]["attempts"] == 0
    assert j.next(now=60.0) == ("A.MP4", "proxy")


def test_a_backoff_keeps_the_stage_off_the_queue_until_it_expires(tmp_path):
    j = _journal(tmp_path, "A.MP4")
    _listen(j, "A.MP4")
    _run(j, "A.MP4", "proxy", t=10.0)
    j.start("A.MP4", "look", now=20.0)
    j.fail("A.MP4", "look", "429 rate limited", retry_after_s=30.0, cost_usd=0.01, now=21.0)
    assert j.next(now=22.0) is None
    assert j.next(now=51.0) == ("A.MP4", "look")
    assert j.cost_usd() == pytest.approx(0.01), "a failed call that billed is still spend"


# ---------------------------------------------------------------- release

def test_released_only_when_every_applicable_stage_is_done(tmp_path):
    j = _journal(tmp_path, "A.MP4")
    for s in ("probe", "asr"):
        _run(j, "A.MP4", s, t=1.0)
    j.skip("A.MP4", "telemetry", "no gpmd stream")     # a phone clip: not applicable
    _run(j, "A.MP4", "proxy", t=10.0)
    _run(j, "A.MP4", "look", t=20.0, cost=0.09)
    assert not j.released("A.MP4"), "look done, close and picks not: not released"
    _run(j, "A.MP4", "close", t=30.0, cost=0.07)
    assert not j.released("A.MP4"), "picks missing: not released"
    _run(j, "A.MP4", "picks", t=40.0)
    assert j.released("A.MP4"), "skipped telemetry counts as done"
    assert j.released_clips() == ["A.MP4"]
    assert j.progress()["released"] == 1
    assert any("released" in e["what"] for e in j.data["log"])


def test_dependencies_proxy_needs_probe_not_asr_and_picks_wait_for_the_close_look(tmp_path):
    j = _journal(tmp_path, "A.MP4")
    _run(j, "A.MP4", "probe", t=1.0)
    j.start("A.MP4", "asr", now=2.0)                  # ASR takes a while
    assert j.next(now=2.0) == ("A.MP4", "telemetry")
    j.start("A.MP4", "telemetry", now=2.0)
    assert j.next(now=2.0) == ("A.MP4", "proxy"), "proxy does not wait on listening"
    _run(j, "A.MP4", "proxy", t=3.0)
    assert j.next(now=4.0) == ("A.MP4", "look")
    _run(j, "A.MP4", "look", t=4.0)
    assert j.next(now=5.0) == ("A.MP4", "close")
    _run(j, "A.MP4", "close", t=5.0)
    assert j.next(now=6.0) is None, "picks needs the ASR, still running"
    with pytest.raises(JournalError, match="waits on asr"):
        j.start("A.MP4", "picks", now=6.0)
    j.finish("A.MP4", "asr", now=7.0)
    j.finish("A.MP4", "telemetry", now=7.0)
    assert j.next(now=7.0) == ("A.MP4", "picks")

    k = _journal(tmp_path / "k", "B.MP4")
    _listen(k, "B.MP4")
    _run(k, "B.MP4", "proxy", t=10.0)
    _run(k, "B.MP4", "look", t=11.0)
    assert k.next(now=12.0) == ("B.MP4", "close"), "picks never run ahead of the close look"
    k.skip("B.MP4", "close", "no candidate windows")
    assert k.next(now=12.0) == ("B.MP4", "picks"), "a skipped close look satisfies it"


def test_starting_a_stage_twice_or_an_unknown_clip_is_an_error(tmp_path):
    j = _journal(tmp_path, "A.MP4")
    j.start("A.MP4", "probe", now=1.0)
    with pytest.raises(JournalError, match="running"):
        j.start("A.MP4", "probe", now=2.0)
    with pytest.raises(JournalError, match="unknown clip"):
        j.start("Z.MP4", "probe", now=2.0)
    with pytest.raises(JournalError, match="unknown stage"):
        j.start("A.MP4", "render", now=2.0)


# --------------------------------------------------------------- priority

def test_priority_weighs_candidates_and_themes_most_and_telemetry_least():
    # each term alone, saturated: the most it can ever contribute
    full = {k: J.score({k: sat}) for k, (_w, sat) in J.WEIGHTS.items()}
    assert (full["candidates"] > full["theme_hits"] > full["words"]
            > full["duration_s"] > full["telemetry_peaks"])
    assert full["candidates"] + full["theme_hits"] >= 0.7, "the two that matter most"
    assert full["telemetry_peaks"] <= 0.05, "Karl's rule: never over-index telemetry"
    # and per unit: one peak moves the order less than one candidate or one theme hit
    one = {k: J.score({k: 1}) for k in ("telemetry_peaks", "candidates", "theme_hits")}
    assert one["telemetry_peaks"] < one["candidates"] < one["theme_hits"]
    # every telemetry peak in the world is worth less than a single theme hit or two
    # speech candidates
    assert J.score({"telemetry_peaks": 999}) < J.score({"theme_hits": 1})
    assert J.score({"telemetry_peaks": 999}) < J.score({"candidates": 2})
    assert J.score({}) == 0.0 and J.score(None) == 0.0
    assert J.score({"candidates": 10**6, "theme_hits": 10**6, "words": 10**6,
                    "duration_s": 10**6, "telemetry_peaks": 10**6}) == 1.0
    assert J.score({"candidates": "many"}) == 0.0, "garbage is zero, not an exception"


def test_priority_order_respects_the_weighting_with_capture_order_as_tiebreak(tmp_path):
    j = _journal(tmp_path, "tele.MP4", "talk.MP4", "theme.MP4", "late.MP4", "early.MP4")
    cap = {"tele.MP4": 1.0, "talk.MP4": 2.0, "theme.MP4": 3.0, "late.MP4": 9.0,
           "early.MP4": 8.0}
    for c, t in cap.items():
        j.set_captured(c, t)
    _listen(j, "tele.MP4", facts={"telemetry_peaks": 40, "duration_s": 600})
    _listen(j, "talk.MP4", facts={"candidates": 6, "words": 250})
    _listen(j, "theme.MP4", facts={"candidates": 4, "theme_hits": 2, "words": 120})
    _listen(j, "late.MP4", facts={"candidates": 2})
    _listen(j, "early.MP4", facts={"candidates": 2})
    assert j.ordered() == ["theme.MP4", "talk.MP4", "tele.MP4", "early.MP4", "late.MP4"]
    assert j.next(now=10.0) == ("theme.MP4", "proxy")
    # the priced stages run in that order, one clip released before the next starts
    ran = _drain(j, t=100.0)
    clips_in_order = []
    for c, _s in ran:
        if c not in clips_in_order:
            clips_in_order.append(c)
    assert clips_in_order == ["theme.MP4", "talk.MP4", "tele.MP4", "early.MP4", "late.MP4"]
    first_release = ran.index(("theme.MP4", "picks"))
    assert all(c == "theme.MP4" for c, _s in ran[:first_release + 1]), \
        "the top clip is released whole before the second clip starts its proxy"

    j.set_order("capture")
    assert j.ordered() == ["tele.MP4", "talk.MP4", "theme.MP4", "early.MP4", "late.MP4"]
    with pytest.raises(JournalError):
        j.set_order("random")


# ---------------------------------------------------------------- workers

def test_worker_limits_are_per_pool_and_respected(tmp_path):
    j = _journal(tmp_path, "A.MP4", "B.MP4", "C.MP4", workers={"proxy": 1, "sheet": 2})
    for i, c in enumerate(("A.MP4", "B.MP4", "C.MP4")):
        _listen(j, c, facts={"candidates": 9 - i})
    # one proxy at a time: A's proxy, then nothing else in that pool
    assert j.next(now=10.0) == ("A.MP4", "proxy")
    j.start("A.MP4", "proxy", now=10.0)
    assert j.next(now=10.0) is None, "B's proxy waits; nothing else is runnable yet"
    j.finish("A.MP4", "proxy", now=11.0)
    assert j.next(now=11.0) == ("A.MP4", "look")
    j.start("A.MP4", "look", now=11.0)
    # the sheet pool has a second slot, and the proxy pool is free again — B's proxy
    assert j.next(now=11.0) == ("B.MP4", "proxy")
    j.start("B.MP4", "proxy", now=11.0)
    j.finish("B.MP4", "proxy", now=12.0)
    assert j.next(now=12.0) == ("B.MP4", "look"), "second sheet slot"
    j.start("B.MP4", "look", now=12.0)
    assert j.next(now=12.0) == ("C.MP4", "proxy")
    j.start("C.MP4", "proxy", now=12.0)
    j.finish("C.MP4", "proxy", now=13.0)
    assert j.next(now=13.0) is None, "two sheets running: C's look waits"
    assert sorted(j.running()) == [("A.MP4", "look"), ("B.MP4", "look")]
    j.finish("A.MP4", "look", cost_usd=0.09, now=14.0)
    assert j.next(now=14.0) == ("A.MP4", "close"), "A's close look before C's first sheet"
    # a per-call override tightens the pool without rewriting the journal
    assert j.next(now=14.0, workers={"sheet": 1}) is None


# ------------------------------------------------------------------ budget

def test_pause_priced_skips_look_and_close_while_free_stages_continue(tmp_path):
    j = _journal(tmp_path, "A.MP4", "B.MP4")
    _listen(j, "A.MP4", facts={"candidates": 5})
    _run(j, "A.MP4", "proxy", t=10.0)
    j.pause_priced("budget cap $5.30 reached", now=11.0)
    assert j.paused_priced
    assert j.next(now=11.0) == ("B.MP4", "probe"), "free work goes on"
    _listen(j, "B.MP4", t=11.0, facts={"candidates": 1})
    assert j.next(now=20.0) == ("B.MP4", "proxy"), "proxies are free too"
    _run(j, "B.MP4", "proxy", t=20.0)
    assert j.next(now=21.0) is None, "only look and close are left, and they are held"
    p = j.progress(now=21.0)
    assert p["paused_priced"] and "budget cap" in p["paused_reason"]
    assert p["released"] == 0
    j.resume_priced(now=22.0)
    assert j.next(now=22.0) == ("A.MP4", "look")
    # persisted: a restart stays paused
    j.pause_priced(now=23.0)
    assert Journal.load(tmp_path / "bin.json").paused_priced


# ---------------------------------------------------------------- progress

def test_progress_counts_cost_and_a_rolling_mean_eta(tmp_path):
    j = _journal(tmp_path, "A.MP4", "B.MP4", "C.MP4", workers={"sheet": 1})
    p = j.progress(now=0.0)
    assert p["clips"] == 3 and p["released"] == 0 and p["pct"] == 0.0
    assert p["eta_s"] is None and p["eta_source"] == "none"
    assert p["total_stages"] == 21 and p["stages"]["probe"]["queued"] == 3

    for c in ("A.MP4", "B.MP4", "C.MP4"):
        _listen(j, c, facts={"candidates": 3})
        _run(j, c, "proxy", t=10.0, dur=20.0)         # proxies measured at 20 s
    _run(j, "A.MP4", "look", t=40.0, cost=0.09, dur=40.0)
    _run(j, "A.MP4", "close", t=80.0, cost=0.07, dur=30.0)
    _run(j, "A.MP4", "picks", t=110.0, dur=2.0)
    _run(j, "B.MP4", "look", t=120.0, cost=0.09, dur=60.0)   # a slower sheet
    assert j.mean_duration("look") == pytest.approx(50.0)
    assert j.mean_duration("proxy") == pytest.approx(20.0)
    assert j.mean_duration("picks") == pytest.approx(2.0)

    j.start("B.MP4", "close", now=200.0)
    p = j.progress(now=210.0)
    assert p["released"] == 1 and p["cost_usd"] == pytest.approx(0.25)
    assert p["stages"]["look"] == {"queued": 1, "running": 0, "done": 2, "failed": 0,
                                   "skipped": 0, "parked": 0}
    # sheet pool: B close has 20 s of its 30 s mean left, C look 50 s, C close 30 s → 100 s
    assert p["eta_by_kind"]["sheet"] == pytest.approx(100.0)
    assert p["eta_by_kind"]["picks"] == pytest.approx(4.0)
    assert p["eta_s"] == pytest.approx(100.0), "the busiest pool is the ETA"
    assert p["eta_source"] == "measured" and p["eta_unmeasured"] == []
    assert p["pct"] == pytest.approx(round(100 * 16 / 21, 1))
    rows = {r["clip"]: r for r in p["rows"]}
    assert rows["A.MP4"]["state"] == "released"
    assert rows["B.MP4"]["state"] == "indexing"
    assert rows["C.MP4"]["state"] == "queued"
    assert rows["A.MP4"]["stages"]["picks"] == "done"

    # the mean is rolling: after the window fills, the early runs fall out
    for _n in range(J.TIMING_WINDOW + 2):
        j._note_duration("look", 10.0)
    assert j.mean_duration("look") == pytest.approx(10.0)
    assert len(j.data["timing"]["look"]) == J.TIMING_WINDOW


def test_progress_leaves_unmeasured_stages_out_of_the_eta_and_says_so(tmp_path):
    j = _journal(tmp_path, "A.MP4")
    _run(j, "A.MP4", "probe", t=0.0, dur=1.0)
    p = j.progress(now=2.0)
    assert p["eta_s"] is None, "nothing left to run has been measured"
    assert set(p["eta_unmeasured"]) == {"telemetry", "asr", "proxy", "look", "close", "picks"}
    _run(j, "A.MP4", "asr", t=2.0, dur=5.0)
    _run(j, "A.MP4", "telemetry", t=7.0, dur=1.0)
    _run(j, "A.MP4", "proxy", t=8.0, dur=20.0)
    assert j.progress(now=30.0)["eta_s"] is None, "priced stages unmeasured: still none"
