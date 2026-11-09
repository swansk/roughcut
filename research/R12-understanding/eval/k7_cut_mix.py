"""K7 audit (judge/K7-AUDIT.md): where a first cut's seconds come from, and which labelled
moments it reaches. Read-only, no model call, no GPU.

    uv run python research/R12-understanding/eval/k7_cut_mix.py \
        --work ~/roughcut-loop/judge16/work --bin killington-neutral EDL [EDL ...]

Each EDL's first accepted proposal (`provenance.bases[0]`, the fresh first pass) is split
into 0.1 s steps: within 1 s of a telemetry event a checker would look at, else under a
transcript line, else other. Labelled moments (killington-moments.checked.json, high and
medium positives) are split into events (air, fall, camera-into-snow, camera-toss, water)
and the rest (lift ride, chairlift talk, cheers, the trail-map stop, the selfie, scenery);
a moment is reached when a shot overlaps it (label widened 0.5 s each side).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from roughcut import telemetry_events as te  # noqa: E402

LABELS = Path(__file__).with_name("killington-moments.checked.json")
EVENTISH = {"air", "fall", "camera-into-snow", "camera-toss", "water"}
STEP = 0.1


def load_bin(work: Path, name: str) -> tuple[dict, dict, dict]:
    tel, aud = work / "telemetry" / name, work / "audio" / name
    ev, speech, dur = {}, {}, {}
    for p in sorted(aud.glob("*.audio.json")):
        a = json.loads(p.read_text())
        c = Path(a["clip"]).stem
        dur[c] = float(a["duration_s"])
        speech[c] = [(u["start"], u["end"]) for u in a.get("transcript", [])]
        ev[c] = [(e["start"] - 1.0, e["end"] + 1.0)
                 for e in te.telemetry_events(c, tel) if e.get("check")]
    return ev, speech, dur


def mix(shots: list[dict], ev: dict, speech: dict) -> dict:
    tot = {"telemetry": 0.0, "speech": 0.0, "other": 0.0}
    for s in shots:
        c, t = Path(s["clip"]).stem, float(s["in"])
        while t < float(s["out"]):
            k = ("telemetry" if any(a <= t < b for a, b in ev[c]) else
                 "speech" if any(a <= t < b for a, b in speech[c]) else "other")
            tot[k] += STEP
            t += STEP
    return tot


def reached(shots: list[dict], labels: list[dict]) -> dict:
    out: dict[str, list[tuple[str, bool]]] = {"event": [], "broll": []}
    for m in labels:
        if m["polarity"] != "positive" or m["confidence"] == "low":
            continue
        got = any(Path(s["clip"]).stem == m["clip"] and float(s["in"]) < m["end"] + .5
                  and float(s["out"]) > m["start"] - .5 for s in shots)
        out["event" if m["kind"] in EVENTISH else "broll"].append((m["id"], got))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--bin", default="killington-neutral")
    ap.add_argument("edls", nargs="+", type=Path)
    a = ap.parse_args(argv)
    ev, speech, dur = load_bin(a.work, a.bin)
    labels = json.loads(LABELS.read_text())["moments"]
    whole = mix([{"clip": c, "in": 0.0, "out": d} for c, d in dur.items()], ev, speech)
    n = sum(whole.values())
    print("whole bin: " + ", ".join(f"{k} {v / n:.0%}" for k, v in whole.items()))
    for p in a.edls:
        bases = (json.loads(p.read_text()).get("provenance") or {}).get("bases") or []
        if not bases:
            continue
        shots = [s for s in (bases[0]["shots"][i] for i in bases[0]["order"])
                 if Path(s["clip"]).stem in ev]           # generated shots (titles) aside
        m = mix(shots, ev, speech)
        n = sum(m.values())
        r = reached(shots, labels)
        print(f"{p}: {len(shots)} shots {n:.0f}s, "
              + ", ".join(f"{k} {v / n:.0%}" for k, v in m.items())
              + f", clips {len({Path(s['clip']).stem for s in shots})}/{len(dur)}"
              + "".join(f" | {k} {sum(g for _, g in v)}/{len(v)}" for k, v in r.items())
              + f" | pocket pizza {'yes' if dict(r['broll']).get('talk-03-pizza') else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
