# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2"]
# ///
"""Calibrate the Tier A wind detector against AudioSet wind detections (study R9).

R8 could not validate the wind branch: `wind_dominant_fraction` was <=0.08 on every
B1 clip and 0.00 on most, which was read as "B1 has no wind because the cameras are
static". The event tagger contradicts that — AST scores Wind at 0.65 on GX010494 and
0.54 on GX010493 — so the DSP thresholds (low-speech > 12 dB AND flatness > 0.40)
were simply too strict to fire on real wind.

Same method as R8: an independent detector supplies the reference, and the DSP
constants are measured against it rather than guessed. The reference is weaker here
than ASR was for speech — AST gives one score per clip-ish window, not frame labels —
so this calibrates a per-clip *ranking*, not a per-frame decision.

Usage:
    uv run wind_calibrate.py SIDECAR_DIR
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

DB_GRID = np.arange(0.0, 20.5, 1.0)
FLAT_GRID = np.array([0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50, 0.60, 0.80, 1.01])
# Absolute low-band level. Without this the rule is purely relative and inflates
# whenever nobody is speaking: an empty speech band makes any ambient hiss look
# "wind dominant", which is how GX010496 (silent, base area) scored 0.42 while the
# tagger scored it 0.
ABS_GRID = np.array([-99.0, -40.0, -35.0, -30.0, -27.5, -25.0, -22.5, -20.0])


def ast_wind(data: dict) -> float:
    """Max AudioSet wind confidence for the clip, 0.0 if the tagger found none."""
    scores = [e["conf"] for e in data.get("events", [])
              if e["label"].startswith("Wind")]
    return max(scores, default=0.0)


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    ra -= ra.mean(); rb -= rb.mean()
    denom = np.sqrt((ra ** 2).sum() * (rb ** 2).sum())
    return float((ra * rb).sum() / denom) if denom else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser(description="Calibrate DSP wind thresholds vs AudioSet.")
    ap.add_argument("sidecars", type=Path)
    args = ap.parse_args()

    clips = []
    for p in sorted(args.sidecars.glob("*.audio.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        t = d["tracks"]
        low = np.asarray(t["low_band_db"])
        sp = np.asarray(t["speech_band_db"])
        flat = np.asarray(t["flatness"])
        clips.append({
            "clip": d["clip"], "ref": ast_wind(d),
            "delta": low - sp, "flat": flat, "low": low,
            "shipped": d["summary"]["wind_dominant_fraction"],
        })
    if not clips:
        raise SystemExit(f"no sidecars in {args.sidecars}")

    ref = np.array([c["ref"] for c in clips])
    print(f"{len(clips)} clips; AudioSet wind reference on "
          f"{int((ref > 0.15).sum())} of them\n")
    print(f"{'clip':<14}{'AST wind':>10}{'shipped frac':>14}{'med(low-sp)':>13}{'med flat':>10}")
    for c in sorted(clips, key=lambda c: -c["ref"]):
        print(f"{c['clip']:<14}{c['ref']:10.3f}{c['shipped']:14.3f}"
              f"{np.median(c['delta']):13.1f}{np.median(c['flat']):10.3f}")

    print("\ngrid: fraction of frames with (low > c dBFS & low-speech > a dB & flatness > b)")
    print("ranked by rank-agreement with AudioSet, then by false positives on its zeros")
    pos = ref > 0.15
    rows = []
    for a in DB_GRID:
        for b in FLAT_GRID:
            for c_abs in ABS_GRID:
                frac = np.array([float(((cl["delta"] > a) & (cl["flat"] > b)
                                        & (cl["low"] > c_abs)).mean()) for cl in clips])
                if frac[pos].max() < 0.20:      # must actually fire on real wind
                    continue
                rows.append({
                    "db": float(a), "flat": round(float(b), 2), "abs": float(c_abs),
                    "rho": round(spearman(frac, ref), 3),
                    "hits": int((frac[pos] > 0.20).sum()),
                    "fp": int((frac[~pos] > 0.20).sum()),
                })
    rows.sort(key=lambda r: (-r["hits"], r["fp"], -r["rho"]))
    for r in rows[:12]:
        print(f"  low>{r['abs']:6.1f}dBFS  low-sp>{r['db']:4.1f}dB  flat>{r['flat']:.2f}   "
              f"rho={r['rho']:+.3f}  hits {r['hits']}/{int(pos.sum())}  "
              f"false pos {r['fp']}/{int((~pos).sum())}")

    shipped = np.array([c["shipped"] for c in clips])
    print(f"\nshipped thresholds (>12dB & flat>0.40): rho={spearman(shipped, ref):+.3f}, "
          f"max fraction on any clip = {shipped.max():.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
