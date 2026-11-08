#!/usr/bin/env python3
"""Score the local stage's fall checks against Karl's checked Killington moments.

Rules are the bake-off's `score.py` (research/R12-understanding/eval/README.md):
  * a telemetry item is a TRUE fall when it overlaps (+-0.5 s) a positive, non-low-confidence
    fall / crash / camera-into-snow moment; a camera-toss is a negative; an item that overlaps
    only a low-confidence row is left out;
  * AUROC of P(yes) over the telemetry windows asked 'fall' (positives vs negatives), and over
    the labelled windows (positives at labelled falls, negatives at labelled refutations).

  score_local_checks.py tel SIDE_DIR            AUROC from a bin's <stem>.local.json sidecars
  score_local_checks.py labelled run  OUT_DIR --footage DIR --work DIR   check the labelled windows
  score_local_checks.py labelled score OUT_DIR  AUROC from those sidecars

Offline: reads sidecars, makes no model call (`labelled run` uses the local GPU).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
EVAL = REPO / "research" / "R12-understanding" / "eval"
PAD = 0.5
FALL_K = {"fall", "crash", "camera-into-snow"}


def moments() -> list[dict]:
    mom = json.loads((EVAL / "killington-moments.checked.json").read_text(encoding="utf-8"))["moments"]
    for x in mom:
        if x["kind"] == "camera-toss":
            x["polarity"] = "negative"
            x["refutes"] = ["fall"]           # Karl: tossed camera, not a fall
    return mom


def ov(a0, a1, b0, b1, pad=0.0) -> bool:
    return a0 < b1 + pad and b0 < a1 + pad


def auroc(pos: list[float], neg: list[float]):
    if not pos or not neg:
        return None
    s = 0.0
    for p in pos:
        for n in neg:
            s += 1.0 if p > n else (0.5 if p == n else 0.0)
    return s / (len(pos) * len(neg))


def tel_auroc(side: Path, mom: list[dict]) -> dict:
    pos, neg, n_items = [], [], 0
    peak = 0.0
    gpu_s = 0.0
    for f in sorted(side.glob("*.local.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        clip = d["clip"]
        gpu_s += float((d.get("gpu") or {}).get("gpu_s") or 0)
        peak = max(peak, float((d.get("gpu") or {}).get("peak_vram_gb") or 0))
        for it in d.get("checks") or []:
            if it["q"] != "fall" or it["p_yes"] is None or it["cand"].get("kind") == "label":
                continue
            c = it["cand"]
            n_items += 1
            truth = any(x["clip"] == clip and x["polarity"] == "positive" and x["confidence"] != "low"
                        and x["kind"] in FALL_K and ov(c["start"], c["end"], x["start"], x["end"], PAD)
                        for x in mom)
            lowrow = any(x["clip"] == clip and x["confidence"] == "low"
                         and ov(c["start"], c["end"], x["start"], x["end"], PAD) for x in mom)
            if lowrow and not truth:
                continue
            (pos if truth else neg).append(it["p_yes"])
    return {"auroc_tel_fall": auroc(pos, neg), "positives": len(pos), "negatives": len(neg),
            "items": n_items, "gpu_s_total": round(gpu_s, 1), "peak_vram_gb": peak,
            "mean_p_pos": round(sum(pos) / len(pos), 3) if pos else None,
            "mean_p_neg": round(sum(neg) / len(neg), 3) if neg else None}


def labelled_items(mom: list[dict]) -> dict[str, list[dict]]:
    """The labelled windows (bake-off run_matrix.check_items, labelled half): a positive fall
    is checked at its middle (three windows when long), a refuted fall at 1-3 points."""
    by: dict[str, list[dict]] = {}
    for x in mom:
        refut = list(x.get("refutes") or [])
        neg = x["polarity"] == "negative" or bool(refut)
        if x["polarity"] == "positive" and x["kind"] in FALL_K:
            if x["end"] - x["start"] <= 4.5:
                ts = [round((x["start"] + x["end"]) / 2, 2)]
            else:
                ts = [round(x["start"] + f * (x["end"] - x["start"]), 2) for f in (.15, .5, .85)]
            for t in ts:
                by.setdefault(x["clip"], []).append(
                    {"kind": "label", "t": t, "start": t, "end": t, "questions": ["fall"], "mid": x["id"], "neg": False})
        elif neg and ("fall" in refut or "crash" in refut):
            fr = [.2, .5, .8] if x["end"] - x["start"] > 4 else [.5]
            for f in fr:
                t = round(x["start"] + f * (x["end"] - x["start"]), 2)
                by.setdefault(x["clip"], []).append(
                    {"kind": "label", "t": t, "start": t, "end": t, "questions": ["fall"], "mid": x["id"], "neg": True})
    return by


def labelled_run(out: Path, footage: Path, work: Path) -> None:
    sys.path.insert(0, str(REPO))
    from roughcut import localvlm
    ctx = localvlm.bind(footage, work)
    items = labelled_items(moments())
    out.mkdir(parents=True, exist_ok=True)
    # reuse the bin's cached sensors for levelling, but write the labelled sidecars apart
    clips = sorted(items)
    job = {"out": str(out), "force": False, "embed": False, "check": True, "clips": []}
    for c in clips:
        pre = localvlm.prepare(ctx, c + ".MP4")
        job["clips"].append({"stem": c, "video": str(footage / f"{c}.MP4"), "candidates": items[c],
                             "sensors": pre["sensors"]})
    jf = out / "job.json"
    jf.write_text(json.dumps(job), encoding="utf-8")
    import subprocess
    r = subprocess.run(localvlm._cmd("index", str(jf)), env=localvlm._env())
    sys.exit(r.returncode)


def labelled_score(out: Path) -> dict:
    pos, neg = [], []
    for f in sorted(out.glob("*.local.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        for it in d.get("checks") or []:
            if it["p_yes"] is None:
                continue
            (neg if it["cand"].get("neg") else pos).append(it["p_yes"])
    return {"auroc_labelled_fall": auroc(pos, neg), "positives": len(pos), "negatives": len(neg),
            "mean_p_pos": round(sum(pos) / len(pos), 3) if pos else None,
            "mean_p_neg": round(sum(neg) / len(neg), 3) if neg else None}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("tel"); p.add_argument("dir")
    p = sub.add_parser("labelled"); p.add_argument("what", choices=["run", "score"]); p.add_argument("dir")
    p.add_argument("--footage"); p.add_argument("--work")
    a = ap.parse_args()
    if a.cmd == "tel":
        print(json.dumps(tel_auroc(Path(a.dir), moments()), indent=1))
    elif a.what == "run":
        labelled_run(Path(a.dir), Path(a.footage), Path(a.work))
    else:
        print(json.dumps(labelled_score(Path(a.dir)), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
