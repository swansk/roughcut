"""tools/eval_understanding.py on a tiny synthetic bin (wave 20 EVAL): no Killington, no model.

Covers the four ways an event can reach the planner (asserted by telemetry, unsupported and
demoted, felt-only, verdict-confirmed / rejected), the recall / negative / Find scoring, and the
gate's tolerance logic.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import eval_understanding as ev  # noqa: E402


def _air(start, end, **kw):
    return {"kind": "air", "who": "wearer", "start": start, "end": end, "t": (start + end) / 2,
            "source": "telemetry", "status": "felt", "check": True, "text": f"air at {start}", **kw}


def _bin(tmp_path: Path, verdicts: dict | None = None) -> Path:
    work, b = tmp_path / "work", "tiny"
    for d in ("visual", "audio", "telemetry", "index"):
        (work / d / b).mkdir(parents=True, exist_ok=True) if d != "index" else (work / d).mkdir(parents=True, exist_ok=True)
    # CLIP_A: two telemetry airs (10 s: confirmed by Claude, 30 s: rejected), a handling impact
    (work / "telemetry" / b / "CLIP_A.felt.json").write_text(json.dumps({"schema": 2, "clip": "CLIP_A.MP4", "events": [
        _air(10.0, 10.4), _air(30.0, 30.4),
        {"kind": "impact", "who": "wearer", "start": 50.0, "end": 50.1, "t": 50.05, "peak_g": 6.0, "handling": True,
         "source": "telemetry", "status": "felt", "check": False, "text": "impact 6 g (often handling)"}]}))
    # CLIP_B: only an index claim of a jump nobody backs (a labelled negative) and a felt-free clip
    (work / "visual" / b / "CLIP_B.visual.json").write_text(json.dumps({"clip": "CLIP_B.MP4", "moments": [
        {"start": 20.0, "end": 22.0, "kind": "jump", "notable": True, "what": "a big jump over the rocks"},
        {"start": 40.0, "end": 41.0, "kind": "scenery", "notable": False, "what": "a view"}]}))
    for c in ("CLIP_A", "CLIP_B"):
        (work / "audio" / b / f"{c}.audio.json").write_text(json.dumps({
            "clip": f"{c}.MP4", "duration_s": 60.0, "tracks": {"onset": []},
            "transcript": [{"start": 5.0, "end": 7.0, "text": "look at that waterfall"}]}))
    if verdicts is not None:
        (work / "index" / f"{b}.verify.json").write_text(json.dumps({"version": 1, "mode": "hybrid", "verdicts": verdicts}))
    return work


def _set(tmp_path: Path) -> Path:
    d = tmp_path / "eval"
    d.mkdir()
    (d / "killington-moments.checked.json").write_text(json.dumps({"moments": [
        {"id": "air-a-10", "clip": "CLIP_A", "start": 10.0, "end": 10.4, "kind": "air", "confidence": "high",
         "polarity": "positive", "who": "wearer", "karl": "ok"},
        {"id": "air-a-30", "clip": "CLIP_A", "start": 30.0, "end": 30.4, "kind": "air", "confidence": "medium",
         "polarity": "positive", "who": "wearer"},
        {"id": "neg-b-20", "clip": "CLIP_B", "start": 19.0, "end": 24.0, "kind": "other", "confidence": "high",
         "polarity": "negative", "refutes": ["air"]},
        {"id": "toss-a-50", "clip": "CLIP_A", "start": 49.0, "end": 52.0, "kind": "camera-toss", "confidence": "high",
         "polarity": "positive", "karl": "ok"}]}))
    (d / "killington-find.checked.json").write_text(json.dumps({"queries": [
        {"id": "f1", "query": "waterfall", "confidence": "high", "answers": [{"clip": "CLIP_A", "start": 5.0, "end": 7.0}]},
        {"id": "f2", "query": "waterfall again", "confidence": "high", "answers": [{"clip": "CLIP_A", "start": 40.0, "end": 45.0}]},
        {"id": "f3", "query": "air", "confidence": "low", "answers": [{"clip": "CLIP_A", "start": 10.0, "end": 10.4}]}]}))
    return d


def test_unsupported_claim_is_held_and_telemetry_air_is_asserted(tmp_path):
    res = ev.evaluate(_bin(tmp_path), "tiny", _set(tmp_path))
    L = res["layers"]
    # the raw index asserts the CLIP_B jump (a labelled negative); demotion removes it
    assert L["index_raw"]["negatives_asserted"] == [1, 2]            # neg-b-20 and the toss (fall) -> only air one hit
    assert L["asserted"]["negatives_asserted"][0] == 0
    assert res["certainty"]["index_claims_held"] == {"unsupported": 1}
    # both airs are felt, so both are asserted until a checker says otherwise; the handling impact is not
    assert L["asserted"]["recall"]["conf:high"] == [1.0, 1]
    assert L["felt"]["events"] == 2


def test_verdicts_confirm_reject_and_certainty(tmp_path):
    work = _bin(tmp_path, {
        "CLIP_A:air:10.00": {"verdict": "confirm", "kind": "air", "who": "wearer", "confidence": "high"},
        "CLIP_A:air:30.00": {"verdict": "reject", "kind": "none", "confidence": "high"}})
    res = ev.evaluate(work, "tiny", _set(tmp_path))
    assert res["certainty"]["telemetry_events"] == {"claude": 1, "rejected": 1}
    L = res["layers"]
    assert L["confirmed"]["recall"]["conf:high"] == [1.0, 1]
    assert L["confirmed"]["events"] >= 1
    # the rejected air is no longer asserted: the medium row (30 s) is not found, nothing at 30 s
    assert not any(abs(p["start"] - 30.0) < 1 for p in ev.collect(work, "tiny")["asserted"])


def test_retimed_to_camera_toss_counts_for_that_kind(tmp_path):
    work = _bin(tmp_path, {
        "CLIP_A:air:30.00": {"verdict": "retime", "kind": "camera-toss", "start": 49.5, "end": 50.5, "confidence": "high"}})
    res = ev.evaluate(work, "tiny", _set(tmp_path))
    assert res["layers"]["confirmed"]["recall"]["extra:camera-toss"] == [1.0, 1]
    # a camera-toss prediction is not a fall, so the toss label (a negative for falls) is not asserted
    assert res["layers"]["confirmed"]["negatives_asserted"][0] == 0


def test_scoring_rules():
    moments = [
        {"id": "a", "clip": "C", "start": 10, "end": 11, "kind": "air", "confidence": "high", "polarity": "positive", "who": "other", "karl": "ok"},
        {"id": "f", "clip": "C", "start": 20, "end": 21, "kind": "fall", "confidence": "high", "polarity": "positive"},
        {"id": "n", "clip": "C", "start": 30, "end": 36, "kind": "other", "confidence": "high", "polarity": "negative", "refutes": ["air", "fall"]}]
    preds = [{"clip": "C", "start": 11.4, "end": 11.6, "kind": "air"},     # within +-0.5 s of the label: found
             {"clip": "C", "start": 21.6, "end": 22.0, "kind": "fall"},    # 0.6 s away: missed
             {"clip": "C", "start": 32, "end": 33, "kind": "crash"},       # a crash on a fall-refuting negative
             {"clip": "C", "start": 50, "end": 51, "kind": "air"}]         # nowhere
    s = ev.score_events(moments, preds)
    assert s["recall"]["kind:air"] == [1.0, 1] and s["recall"]["kind:fall"] == [0.0, 1]
    assert s["recall"]["air_who:other"] == [1.0, 1]
    assert s["negatives_asserted"] == [1, 1] and s["false_events"] == 1 and s["unmatched_events"] == 2   # the 0.6 s fall and the 50 s air
    assert s["neg_minutes"] == 0.1 and s["fa_per_neg_min"] == 10.0


def test_find_scoring_overlap_vs_iou_and_low_queries():
    qs = [{"id": "q1", "query": "x", "confidence": "high", "answers": [{"clip": "C", "start": 10, "end": 20}]},
          {"id": "q2", "query": "y", "confidence": "high", "answers": [{"clip": "C", "start": 10, "end": 20}]},
          {"id": "q3", "query": "z", "confidence": "low", "answers": [{"clip": "C", "start": 10, "end": 20}]}]
    rows = {"x": [{"clip": "C.MP4", "start": 11, "end": 19}],
            "y": [{"clip": "D", "start": 0, "end": 5}, {"clip": "D", "start": 0, "end": 5},
                  {"clip": "D", "start": 0, "end": 5}, {"clip": "C", "start": 10, "end": 20}],   # 4th: outside the top 3
            "z": [{"clip": "C", "start": 19, "end": 40}]}                                       # overlaps, IoU 1/31
    s = ev.score_find(qs, lambda q: rows[q])
    assert s["hit3_overlap"] == [0.5, 2] and s["hit3_iou03"] == [0.5, 2]
    assert s["low_hit3_overlap"] == [1.0, 1] and s["low_hit3_iou03"] == [0.0, 1]
    assert s["misses"] == ["q2"]


def test_tolerance_logic():
    base = {"recall_high": 0.89, "recall_karl": 0.6, "recall_air": 0.8, "recall_fall": 1.0,
            "negatives_asserted": 4, "fa_per_neg_min": 2.35, "find_overlap": 0.63, "find_iou": 0.42}
    bad, good = ev.compare(base, dict(base))
    assert not bad and not good
    bad, good = ev.compare(base, dict(base, recall_high=0.86, negatives_asserted=5, find_overlap=0.60))
    assert not bad and not good                                  # inside tolerance: -0.03, +1, -0.03
    bad, good = ev.compare(base, dict(base, recall_high=0.78, negatives_asserted=6, find_overlap=0.5))
    assert [b.split()[0] for b in bad] == ["recall_high", "negatives_asserted", "find_overlap"]
    bad, good = ev.compare(base, dict(base, recall_karl=0.8, negatives_asserted=2))
    assert not bad and len(good) == 2


def test_gate_line_regression_and_improvement():
    h = {"recall_high": 0.89, "recall_karl": 0.6, "recall_air": 0.8, "recall_fall": 1.0, "negatives_asserted": 4,
         "negatives_total": 19, "fa_per_neg_min": 2.35, "find_overlap": 0.63, "find_iou": 0.42}
    res = {"headline": h}
    ok, line = ev.gate_line(res, {"headline": h})
    assert ok and "matches baseline" in line and "4 of 19 negatives" in line
    ok, line = ev.gate_line({"headline": dict(h, recall_high=0.5)}, {"headline": h})
    assert not ok and "REGRESSION" in line
    ok, line = ev.gate_line({"headline": dict(h, negatives_asserted=1)}, {"headline": h})
    assert ok and "improved" in line and "--write-baseline" in line


def test_reads_only_and_skips_without_work_dir(tmp_path, capsys):
    work = _bin(tmp_path)
    before = sorted(p.relative_to(work).as_posix() for p in work.rglob("*"))
    ev.evaluate(work, "tiny", _set(tmp_path))
    assert before == sorted(p.relative_to(work).as_posix() for p in work.rglob("*"))
    assert ev.main(["--work", str(tmp_path / "nowhere"), "--check"]) == 3
