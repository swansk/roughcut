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


def test_find_union_is_scored_from_embeddings_and_cached_vectors_or_says_skipped(tmp_path):
    """FIND3: the union line needs the clips' .embed.npy and the cached text vectors; with
    either missing it is 'skipped' and never a regression (its headline value is None)."""
    import numpy as np
    work, evald = _bin(tmp_path), _set(tmp_path)
    res = ev.evaluate(work, "tiny", evald)
    assert "skipped" in res["find_union"] and "no frame embeddings" in res["find_union"]["skipped"]
    assert res["headline"]["find_union_overlap"] is None
    assert ev.compare({"find_union_overlap": 0.79}, res["headline"]) == ([], [])

    local = tmp_path / "local"
    local.mkdir()
    t = np.arange(0, 60, 0.5)
    e = np.zeros((len(t), 4), dtype=np.float16)
    e[:, 0] = 1.0
    e[12] = [0, 1, 0, 0]                                  # 6 s on CLIP_A
    np.save(local / "CLIP_A.embed.npy", e)
    (local / "CLIP_A.embed.json").write_text(json.dumps({"clip": "CLIP_A", "times": list(t)}))
    res = ev.evaluate(work, "tiny", evald, local)
    assert "only" not in res["find_union"].get("skipped", "") and "embedded 1 of 2" in res["find_union"]["skipped"]
    np.save(local / "CLIP_B.embed.npy", e)
    (local / "CLIP_B.embed.json").write_text(json.dumps({"clip": "CLIP_B", "times": list(t)}))
    res = ev.evaluate(work, "tiny", evald, local)
    assert "no cached text vectors" in res["find_union"]["skipped"]
    qs = ev.load_set(evald)[1]
    (evald / ev.VECTORS).write_text(json.dumps({"vectors": {q["query"]: [0, 1, 0, 0] for q in qs}}))
    res = ev.evaluate(work, "tiny", evald, local)
    assert res["find_union"]["hit3_overlap"][1] >= 1 and res["headline"]["find_union_overlap"] is not None


# ------------------------------------------------------------ the recorded checker (wave 21 EVALREV)

def _record(**verdicts):
    """The tiny bin's 6 g impact is a hero shot, so the router sends it too: record it (confirmed)."""
    verdicts.setdefault("CLIP_A:impact:50.00", {"verdict": "reject", "kind": "none", "who": "wearer", "start": None,
                                                 "end": None, "confidence": "high", "why": "handling", "model": "m"})
    return {"version": 1, "bin": "tiny", "rev": "t", "mode": "hybrid", "verdicts": verdicts}


def _v(verdict, confidence="high", kind="air"):
    return {"verdict": verdict, "kind": kind, "who": "wearer", "start": None, "end": None,
            "confidence": confidence, "why": "recorded", "model": "m"}


def test_the_replay_runs_the_revs_router_and_settle_on_recorded_answers(tmp_path):
    work = _bin(tmp_path, {"CLIP_A:air:10.00": {"verdict": "confirm", "confidence": "high"}})   # NOT read in replay
    rec = _record(**{"CLIP_A:air:10.00": _v("reject"), "CLIP_A:air:30.00": _v("confirm")})
    res = ev.evaluate(work, "tiny", _set(tmp_path), record=rec, replay_dir=tmp_path / "rp")
    rp = res["replay"]
    assert rp["recorded"] == 3 and rp["answered"] == 3 and rp["unrecorded"] == [] and rp["not_routed"] == []
    # the sensor found the 10 s air, the recorded checker threw it out: lost, and the asserted layer misses it
    assert res["checker_lost"] == ["air-a-10"]
    assert res["headline"]["checker_lost"] == 1 and res["layers"]["asserted"]["recall"]["conf:high"] == [0.0, 1]
    assert res["certainty"]["telemetry_events"] == {"claude": 1, "rejected": 1}
    assert res["certainty"]["verdicts"] == 3        # from the replay, not from index/<bin>.verify.json


def test_a_low_confidence_answer_is_settled_by_the_revs_code_not_the_recording(tmp_path):
    rec = _record(**{"CLIP_A:air:10.00": _v("reject", "low"), "CLIP_A:air:30.00": _v("confirm")})
    res = ev.evaluate(_bin(tmp_path), "tiny", _set(tmp_path), record=rec, replay_dir=tmp_path / "rp")
    assert res["checker_lost"] == [] and res["certainty"]["telemetry_events"] == {"claude": 1, "unsure": 1}


def test_an_event_the_rev_routes_but_the_recording_never_saw_is_named(tmp_path):
    rec = _record(**{"CLIP_A:air:10.00": _v("confirm")})            # the 30 s air is an air: the router sends it
    res = ev.evaluate(_bin(tmp_path), "tiny", _set(tmp_path), record=rec, replay_dir=tmp_path / "rp")
    assert res["replay"]["unrecorded"] == ["CLIP_A_air_30.00"] and res["replay"]["answered"] == 2
    ok, line = ev.gate_line(res, None)
    assert "STALE RECORDING" in line and "2 of 3 routed" in line


def test_the_replay_writes_nothing_under_the_inputs_and_calls_no_model(tmp_path):
    from roughcut import inference
    work = _bin(tmp_path)
    before = sorted(p.relative_to(work).as_posix() for p in work.rglob("*"))
    rec = _record(**{"CLIP_A:air:10.00": _v("confirm"), "CLIP_A:air:30.00": _v("confirm")})
    ev.evaluate(work, "tiny", _set(tmp_path), record=rec, replay_dir=tmp_path / "rp")
    assert before == sorted(p.relative_to(work).as_posix() for p in work.rglob("*"))
    assert inference._OVERRIDE is None or inference._OVERRIDE.name != "recorded"      # put back
    assert (tmp_path / "rp" / "replay-ledger.jsonl").read_text().count("\n") == 3       # the run dir, not ~/work


def test_cli_frozen_modes(tmp_path):
    work, evald = _bin(tmp_path), _set(tmp_path)
    args = ["--frozen", str(work), "--bin", "tiny", "--eval", str(evald), "--baseline", str(tmp_path / "nobase"), "--check"]
    assert ev.main(args + ["--record", str(tmp_path / "missing.json")]) == 3                # no recording: skipped (AMBER)
    rec = tmp_path / "rec.json"
    rec.write_text(json.dumps(_record(**{"CLIP_A:air:10.00": _v("confirm")})))
    assert ev.main(args + ["--record", str(rec)]) == 4                                      # stale (AMBER)
    rec.write_text(json.dumps(_record(**{"CLIP_A:air:10.00": _v("confirm"), "CLIP_A:air:30.00": _v("confirm")})))
    assert ev.main(args + ["--record", str(rec)]) == 0
    base = tmp_path / "base.json"
    base.write_text(json.dumps({"headline": {"checker_lost": 0}}))
    rec.write_text(json.dumps(_record(**{"CLIP_A:air:10.00": _v("reject"), "CLIP_A:air:30.00": _v("confirm")})))
    assert ev.main(["--frozen", str(work), "--bin", "tiny", "--eval", str(evald), "--baseline", str(base),
                    "--record", str(rec), "--check"]) == 1                                  # RED


def test_capture_record_keeps_the_answer_before_settle(tmp_path):
    work = _bin(tmp_path, {"CLIP_A:air:10.00": {"verdict": "unsure", "raw_verdict": "reject", "kind": "none",
                                                "who": "wearer", "confidence": "low", "why": "w", "model": "m", "at": 1}})
    out = tmp_path / "rec.json"
    assert ev.main(["--capture-record", str(work), "--bin", "tiny", "--record", str(out), "--rev", "abc"]) == 0
    got = json.loads(out.read_text())
    assert got["rev"] == "abc" and got["verdicts"]["CLIP_A:air:10.00"]["verdict"] == "reject"
    assert "at" not in got["verdicts"]["CLIP_A:air:10.00"]


def test_asserted_layer_is_the_boards_demote_ranked_with_a_claude_reject_on_a_telemetry_row(tmp_path):
    """EVALFIX: the eval's `asserted` rows are exactly `verify.demote_ranked(ranked, state)` (what the
    board's /api/project `events` is), and a telemetry row Claude rejected is held even with a
    `stopped` period beside it."""
    from roughcut import verify
    work = _bin(tmp_path, {})
    felt = work / "telemetry" / "tiny" / "CLIP_A.felt.json"
    d = json.loads(felt.read_text())
    d["events"] += [
        {"kind": "speed_drop", "who": "wearer", "start": 229.0, "end": 232.0, "t": 232.0, "from_ms": 16.0,
         "to_ms": 3.0, "peak_g": 4.0, "stopped": False, "source": "telemetry", "status": "felt", "check": True,
         "text": "speed drop"},
        {"kind": "stopped", "who": "wearer", "start": 231.0, "end": 260.0, "t": 245.0, "source": "telemetry",
         "status": "felt", "check": False, "text": "stopped"}]
    felt.write_text(json.dumps(d))
    def asserted_falls(verdicts):
        (work / "index" / "tiny.verify.json").write_text(json.dumps({"version": 1, "mode": "hybrid", "verdicts": verdicts}))
        data = ev.collect(work, "tiny")
        return data, [r for r in data["asserted_rows"] if r.get("kind") == "fall" and r["start"] == 229.0]
    _, before = asserted_falls({})
    assert len(before) == 1
    data, after = asserted_falls({"CLIP_A:speed_drop:229.00": {"verdict": "reject", "raw_verdict": "reject",
                                                               "kind": "none", "confidence": "high"}})
    assert after == []
    assert any(r["start"] == 229.0 and r["certainty"] == "rejected" for r in data["held"])


# ------------------------------------------------------------------ the B-roll line (wave 22, K7)

def test_broll_line_counts_the_non_event_labels_the_list_and_the_cut_reach(tmp_path):
    moments = [
        {"id": "lift-1", "clip": "C3", "start": 0.0, "end": 40.0, "kind": "lift", "confidence": "high", "polarity": "positive"},
        {"id": "talk-1", "clip": "C3", "start": 18.3, "end": 21.0, "kind": "talk-key-line", "confidence": "medium",
         "polarity": "positive"},
        {"id": "talk-low", "clip": "C3", "start": 30.0, "end": 31.0, "kind": "talk-key-line", "confidence": "low",
         "polarity": "positive"},
        {"id": "air-1", "clip": "C1", "start": 5.0, "end": 6.0, "kind": "air", "confidence": "high", "polarity": "positive"},
        {"id": "neg", "clip": "C2", "start": 5.0, "end": 6.0, "kind": "other", "confidence": "high", "polarity": "negative"}]
    assert [m["id"] for m in ev.broll_labels(moments)] == ["lift-1", "talk-1"]      # no events, no lows, no negatives
    rows = [{"clip": "C3.MP4", "start": 17.0, "end": 22.0}, {"clip": "C1.MP4", "start": 5.0, "end": 6.0}]
    edl = tmp_path / "cut.edl.json"
    edl.write_text(json.dumps({"segments": [{"clip": "C3.MP4", "in": 100.0, "out": 104.0}]}))
    r = ev.score_broll(moments, rows, ev.cut_shots(edl))
    assert r["hit"] == ["lift-1", "talk-1"] and r["recall"] == [1.0, 2]
    assert r["cut_hit"] == [] and r["cut_recall"] == [0.0, 2]
    low = ev.score_broll(moments, [{"clip": "C1.MP4", "start": 5.0, "end": 6.0}])
    assert low["recall"] == [0.0, 2] and ev.broll_low({"broll": low}) and not ev.broll_low({"broll": r})
    assert ev.broll_low({}) is False
