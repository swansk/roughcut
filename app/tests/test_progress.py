"""The shared progress model, and the estimate that starts it.

Everything here is about one property: the bar must never say something the work does
not support. It must not dip, it must not finish early, it must not sit at 100% while
a job is still going, and an estimate nobody could parse must not be able to stop the
work it was only ever meant to describe.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import config, estimate, inference, progress, revise  # noqa: E402


# ------------------------------------------------------------------ the model

def _job(**kw) -> progress.Job:
    return progress.Job("test", "A job", id="j1", **kw)


def test_a_job_carries_the_shape_every_reader_needs():
    """One shape for four operations: the whole point of the module."""
    job = _job(total=3, done=0)
    snap = job.snapshot()
    for key in ("id", "kind", "label", "state", "started", "elapsed_s", "pct",
                "detail", "eta_s", "milestones"):
        assert key in snap, key
    assert snap["pct"] == 0.0 and snap["eta_s"] is None
    # and it is still a dict, so the server's existing mutations keep working
    job["done"] = 2
    assert job.snapshot()["done"] == 2


def test_pct_comes_from_milestones_when_there_are_any():
    job = _job()
    job.set_estimate(100, [progress.milestone("a", "first", 1),
                           progress.milestone("b", "second", 3)])
    assert job.percent() < 1.0
    job.complete("a")
    assert job.snapshot()["pct"] == pytest.approx(25.0, abs=0.5)
    assert job.snapshot()["milestone"] == "second"
    job.advance("b", 0.5)
    assert job.snapshot()["pct"] == pytest.approx(62.5, abs=0.5)


def test_pct_comes_from_counted_work_when_there_are_none():
    """Parts on disk, sidecars on disk — the filesystem is the honest bar."""
    job = _job(done=0, total=4)
    job.pct_fn = progress.counted()
    assert job.percent() == 0.0
    job["done"] = 3
    assert job.percent() == pytest.approx(75.0)


def test_the_bar_never_dips_and_never_finishes_early():
    """A count read off a filesystem can go down — a glob racing a rename — and a bar
    that dips reads as a failure. 100% is reserved for actually being finished."""
    job = _job(done=3, total=4)
    job.pct_fn = progress.counted()
    assert job.percent() == pytest.approx(75.0)
    job["done"] = 1
    assert job.percent() == pytest.approx(75.0), "the bar went backwards"
    job["done"] = 4
    assert job.percent() <= progress.RUNNING_CEILING
    job.finish("done")
    assert job.percent() == 100.0


def test_a_render_whose_shots_are_all_cut_is_three_quarters_done():
    """Exactly the render's own shape. The old bar hit 100% the moment the parts were
    on disk and then sat there for the whole join — minutes of it on a 4K delivery."""
    job = progress.Job("render", "Rendering", id="r", total=2, done=0)
    job.set_estimate(60, [progress.milestone("cutting", "cutting the shots", 0.75),
                          progress.milestone("joining", "joining and mixing", 0.25)])
    job.complete("cutting")
    assert job.percent() == pytest.approx(75.0, abs=0.5)
    job.finish("done")
    assert job.percent() == 100.0


def test_reaching_a_milestone_closes_the_ones_before_it():
    """A stream can skip a stage. Leaving the skipped one open would park the bar."""
    job = _job()
    job.set_estimate(60, [progress.milestone("a", "a"), progress.milestone("b", "b"),
                          progress.milestone("c", "c")])
    job.complete("c")
    assert [m["done_at"] is not None for m in job["milestones"]] == [True, True, True]


def test_the_eta_is_recalibrated_from_what_the_work_actually_cost():
    """An estimate that says "2 minutes" after eight minutes of running is worse than
    no estimate. Each milestone replaces part of the guess with measurement."""
    job = _job()
    job.set_estimate(100, [progress.milestone("a", "a", 1),
                           progress.milestone("b", "b", 1)])
    job["started"] = time.time() - 60           # half the work took 60s, not 50
    job.complete("a")
    # observed total 120s, blended with the 100s guess at 75% trust -> ~115s total
    assert job["total_est_s"] == pytest.approx(115.0, abs=2.0)
    assert job.snapshot()["eta_s"] == pytest.approx(55.0, abs=2.0)
    assert job.snapshot()["eta_source"] == "measured"


def test_a_long_milestone_still_moves_the_bar_but_never_overruns_it():
    """Creep on the clock between checkpoints — stopping short, because arriving is
    the milestone's job."""
    job = _job()
    job.set_estimate(100, [progress.milestone("a", "a", 1),
                           progress.milestone("b", "b", 1)])
    job["started"] = time.time() - 40           # 40% of the estimate spent, nothing done
    pct = job.percent()
    assert 0 < pct < 50.0, pct


def test_live_shows_what_is_running_and_forgets_what_finished():
    a, b = _job(), progress.Job("test", "Another", id="j2")
    b.finish("done")
    b["finished"] = time.time() - 300
    assert [j["id"] for j in progress.live([a, b])] == ["j1"]
    b["finished"] = time.time()
    assert {j["id"] for j in progress.live([a, b])} == {"j1", "j2"}


# --------------------------------------------------------------- the estimate

GOOD = {"eta_s": 180, "milestones": [{"key": "read", "label": "reading", "weight": 2},
                                     {"key": "shots", "label": "shots", "weight": 5}]}


def test_a_good_estimate_validates_and_keeps_the_offered_order():
    out = estimate.validate_estimate(
        {"eta_s": 90, "milestones": [{"key": "shots", "label": "b", "weight": 1},
                                     {"key": "read", "label": "a", "weight": 1}]},
        ["read", "shots"])
    assert [m["key"] for m in out["milestones"]] == ["read", "shots"]
    assert out["eta_s"] == 90.0


@pytest.mark.parametrize("payload, why", [
    ("not an object", "not a dict"),
    ({"milestones": GOOD["milestones"]}, "no eta"),
    ({"eta_s": "soon", "milestones": GOOD["milestones"]}, "eta not a number"),
    ({"eta_s": 2, "milestones": GOOD["milestones"]}, "eta below the floor"),
    ({"eta_s": 99999, "milestones": GOOD["milestones"]}, "eta past the ceiling"),
    ({"eta_s": 100, "milestones": []}, "no milestones"),
    ({"eta_s": 100, "milestones": [{"key": "invented", "label": "x", "weight": 1},
                                   {"key": "read", "label": "y", "weight": 1}]},
     "invented key"),
    ({"eta_s": 100, "milestones": [{"key": "read", "label": "x", "weight": 0},
                                   {"key": "shots", "label": "y", "weight": 1}]},
     "zero weight"),
    ({"eta_s": 100, "milestones": [{"key": "read", "label": "x", "weight": 1}]},
     "only one usable milestone"),
    ({"eta_s": 100, "milestones": [{"key": "read", "label": "x", "weight": 1},
                                   {"key": "read", "label": "y", "weight": 1}]},
     "repeated key"),
])
def test_a_bad_estimate_is_rejected(payload, why):
    with pytest.raises(ValueError):
        estimate.validate_estimate(payload, ["read", "shots"])


def test_too_many_milestones_is_rejected():
    keys = [f"k{i}" for i in range(30)]
    payload = {"eta_s": 100,
               "milestones": [{"key": k, "label": k, "weight": 1} for k in keys]}
    with pytest.raises(ValueError):
        estimate.validate_estimate(payload, keys)


class _Backend:
    """A scripted backend that answers with whatever it is handed."""
    name = "scripted"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen = []

    def complete(self, request):
        self.seen.append(request)
        text = self.replies[min(len(self.seen), len(self.replies)) - 1]
        model = config.model_for(request.role)
        return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                backend=self.name, model=model, projected_usd=1e-4,
                                latency_ms=1, raw=text)


CHECKS = [("read", "it starts"), ("shots", "it writes")]


def _fallback():
    return estimate.Estimate(eta_s=210.0,
                             milestones=[progress.milestone("read", "reading", 1),
                                         progress.milestone("shots", "shots", 4)],
                             source="fallback")


def test_an_estimate_call_that_works_is_used():
    inference.set_backend(_Backend(json.dumps(GOOD)))
    inference.reset_spend()
    try:
        got = estimate.estimate("a job", "some facts", CHECKS, fallback=_fallback())
    finally:
        inference.set_backend(None)
    assert got.source == "model" and got.eta_s == 180.0
    assert [m["key"] for m in got.milestones] == ["read", "shots"]
    assert got.usage["projected_usd"] > 0


def test_garbage_falls_back_after_one_bounded_re_ask():
    """An estimate must never block the real work — this is the whole safety rule."""
    backend = _Backend("I reckon a couple of minutes?")
    inference.set_backend(backend)
    inference.reset_spend()
    try:
        got = estimate.estimate("a job", "some facts", CHECKS, fallback=_fallback())
    finally:
        inference.set_backend(None)
    assert got.source == "fallback" and got.eta_s == 210.0
    assert len(backend.seen) == 2, "one re-ask, then give up — not a retry loop"
    assert got.detail, "it should say why it fell back"


def test_a_backend_that_explodes_falls_back_rather_than_raising():
    class Broken:
        name = "broken"

        def complete(self, request):
            raise inference.InferenceError("Not logged in")

    inference.set_backend(Broken())
    inference.reset_spend()
    try:
        got = estimate.estimate("a job", "facts", CHECKS, fallback=_fallback())
    finally:
        inference.set_backend(None)
    assert got.source == "fallback" and "Not logged in" in got.detail


def test_a_second_re_ask_is_used_when_the_first_reply_was_junk():
    backend = _Backend("nonsense", json.dumps(GOOD))
    inference.set_backend(backend)
    inference.reset_spend()
    try:
        got = estimate.estimate("a job", "facts", CHECKS, fallback=_fallback())
    finally:
        inference.set_backend(None)
    assert got.source == "model" and len(backend.seen) == 2


# ------------------------------------------------------- counting a half-answer

def test_shots_are_counted_out_of_a_half_written_plan():
    """The Ask's bar is driven by this: the plan is a JSON list of segments, so the
    ones already written are real progress against a real denominator."""
    partial = ('{"segments": [{"clip": "A.MP4", "in": 1.0, "out": 4.0, "why": "x"},'
               ' {"clip": "B.MP4", "in": 2.0, "out": 5.0, "why": "y"},'
               ' {"clip": "C.MP4", "in')
    assert revise.count_shots(partial) == 3
    assert revise.count_shots("") == 0


def test_the_expected_shot_count_prefers_the_cut_that_exists():
    assert revise.expected_shots([{}] * 16, (120, 180)) == 16
    assert revise.expected_shots([], (120, 180)) == pytest.approx(19, abs=2)
