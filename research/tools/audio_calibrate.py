# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2"]
# ///
"""Calibrate the Tier A speech detector against the ASR transcript (study R8).

The DSP detector's thresholds cannot be guessed — the first pass over Copper
called 90%+ of every clip "speech", which is obviously wrong. But the same
sidecars carry an independent reference: Whisper's transcript spans. Where it
found words, someone was talking. So the thresholds get *measured*.

Reference caveats, which the report must repeat: Whisper misses quiet or
wind-buried speech (so measured precision is pessimistic — a DSP frame outside
every span may still be speech), and its spans include intra-sentence pauses
(so measured recall is optimistic at the frame level). It is a good reference,
not a gold standard.

Usage:
    uv run audio_calibrate.py SIDECAR_DIR [-o report.json]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

DB_GRID = np.arange(-60.0, -19.0, 2.5)
# Geometric, and reaching far below the design guess of 0.35: measured flatness
# on this footage sits near 0.05, so a linear grid starting at 0.10 pins its
# optimum against the edge and reports a threshold that isn't one.
FLAT_GRID = np.array([0.01, 0.015, 0.02, 0.03, 0.04, 0.05, 0.07,
                      0.10, 0.15, 0.20, 0.30, 0.45, 0.60])
MOD_GRID = np.arange(0.05, 0.80, 0.05)


def load(sidecar_dir: Path) -> dict[str, np.ndarray]:
    db, flat, mod, onset, ref, clip = [], [], [], [], [], []
    for p in sorted(sidecar_dir.glob("*.audio.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        t = d["tracks"]
        n = min(len(t["speech_band_db"]), len(t["vad"]))
        db.append(np.asarray(t["speech_band_db"][:n]))
        flat.append(np.asarray(t["flatness"][:n]))
        mod.append(np.asarray(t["modulation"][:n]))
        onset.append(np.asarray(t["onset"][:n]))
        ref.append(np.asarray(t["vad"][:n], dtype=bool))
        clip.append(np.full(n, d["clip"]))
    if not db:
        raise SystemExit(f"no sidecars in {sidecar_dir}")
    return {"db": np.concatenate(db), "flat": np.concatenate(flat),
            "mod": np.concatenate(mod), "onset": np.concatenate(onset),
            "ref": np.concatenate(ref), "clip": np.concatenate(clip)}


def auc(score: np.ndarray, ref: np.ndarray) -> float:
    """Probability a random speech frame outranks a random non-speech frame.

    Rank-based, so it measures the feature's separation without committing to
    a threshold — 0.5 is a coin flip, i.e. the feature carries no information.
    """
    order = np.argsort(score, kind="mergesort")
    ranks = np.empty(len(score), dtype=np.float64)
    ranks[order] = np.arange(1, len(score) + 1)
    # average ranks within ties, so a constant feature scores exactly 0.5
    s = score[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + j + 2) / 2.0
        i = j + 1
    n_pos, n_neg = int(ref.sum()), int((~ref).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    return float((ranks[ref].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def prf(pred: np.ndarray, ref: np.ndarray) -> tuple[float, float, float]:
    tp = float((pred & ref).sum())
    p = tp / max(float(pred.sum()), 1e-9)
    r = tp / max(float(ref.sum()), 1e-9)
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def describe(name: str, v: np.ndarray, ref: np.ndarray) -> dict:
    q = [10, 50, 90]
    pos, neg = np.percentile(v[ref], q), np.percentile(v[~ref], q)
    return {"feature": name, "auc": round(auc(v, ref), 3),
            "speech_p10_50_90": [round(float(x), 3) for x in pos],
            "other_p10_50_90": [round(float(x), 3) for x in neg]}


def main() -> int:
    ap = argparse.ArgumentParser(description="Calibrate Tier A speech detection vs ASR.")
    ap.add_argument("sidecars", type=Path, help="directory of *.audio.json")
    ap.add_argument("-o", "--out", type=Path, help="write JSON report here")
    args = ap.parse_args()

    d = load(args.sidecars)
    ref = d["ref"]
    n = len(ref)
    print(f"{n} frames ({n / 10:.0f}s) over {len(set(d['clip']))} clips; "
          f"ASR-speech {ref.mean():.1%}\n")

    print("per-feature separation (AUC 0.5 = no information)")
    feats = [describe("speech_band_db", d["db"], ref),
             describe("flatness", -d["flat"], ref),   # low flatness = speech
             describe("modulation", d["mod"], ref),
             describe("onset", d["onset"], ref)]
    for f in feats:
        print(f"  {f['feature']:16s} auc={f['auc']:.3f}  "
              f"speech p10/50/90 {f['speech_p10_50_90']}  "
              f"other {f['other_p10_50_90']}")

    print("\ngrid search over the AND rule (db > a & flatness < b & modulation > c)")
    best: list[dict] = []
    for a in DB_GRID:
        m_db = d["db"] > a
        for b in FLAT_GRID:
            m_fb = m_db & (d["flat"] < b)
            for c in MOD_GRID:
                p, r, f = prf(m_fb & (d["mod"] > c), ref)
                best.append({"db": float(a), "flat": round(float(b), 2),
                             "mod": round(float(c), 2), "precision": round(p, 3),
                             "recall": round(r, 3), "f1": round(f, 3)})
    by_f1 = sorted(best, key=lambda x: -x["f1"])
    for row in by_f1[:5]:
        print(f"  db>{row['db']:6.1f} flat<{row['flat']:.2f} mod>{row['mod']:.2f}  "
              f"P={row['precision']:.3f} R={row['recall']:.3f} F1={row['f1']:.3f}")

    # Candidate generation cares more about not drowning in false positives than
    # about catching every syllable — the visual pass still gets a look.
    hi_p = [r for r in best if r["recall"] >= 0.60]
    hi_p.sort(key=lambda x: -x["precision"])
    print("\nbest precision at recall >= 0.60")
    for row in hi_p[:5]:
        print(f"  db>{row['db']:6.1f} flat<{row['flat']:.2f} mod>{row['mod']:.2f}  "
              f"P={row['precision']:.3f} R={row['recall']:.3f} F1={row['f1']:.3f}")

    baseline = prf(np.ones(n, dtype=bool), ref)
    print(f"\nbaseline (call everything speech): P={baseline[0]:.3f} R=1.000 "
          f"F1={baseline[2]:.3f}")

    report = {"n_frames": n, "asr_speech_fraction": round(float(ref.mean()), 4),
              "features": feats, "best_f1": by_f1[:10],
              "best_precision_at_recall_60": hi_p[:10],
              "baseline_all_speech": {"precision": round(baseline[0], 3),
                                      "recall": 1.0, "f1": round(baseline[2], 3)}}
    if args.out:
        args.out.write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
