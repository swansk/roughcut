# R4 — Cheap-Gate Recall Curve

**Question:** Using only tier-0/1 signals (audio RMS, motion, speech density, duration,
timestamp clustering), how much footage can the gate discard before it starts dropping true
highlights — and where should the operating point sit?

**Replaces the assumption:** "keep ≥95% of highlights while discarding ≥50%" — currently a
target pulled from the air; the real curve might be much better or worse, and it directly
scales VLM cost (§7).

## Method
1. **Labels:** human highlight marks over ≥ 2h of B1 (every moment Karl would consider
   including, timestamped; expect 30–80 marks). Same label set feeds R1's subset.
2. **Score:** train nothing — evaluate simple rank aggregations of tier-0/1 signals
   (weighted sum with 3–4 hand-set weight profiles, plus per-signal baselines). This is a
   filter, not a ranker; sophistication belongs in tier 2.
3. **Curve:** for each aggregation, sweep the discard fraction 0→90% and compute highlight
   recall (a highlight is "kept" if any overlapping shot survives). Plot recall vs discard.
4. **Decision rule:** the operating point is the largest discard fraction with recall ≥ 97%
   on the best aggregation (stricter than the provisional 95% — a dropped highlight is
   unrecoverable, while extra kept footage only costs tier-2 dollars).
5. **Cost check:** recompute the §7 envelope with the measured surviving-shot count.

## Definition of Done
- [ ] Recall-vs-discard curves per aggregation in `R4-report.md` (data + chart)
- [ ] Chosen aggregation + operating point recorded in SPEC (RQ-4); T5 DoD updated
- [ ] SPEC §7 envelope math replaced with measured numbers
- [ ] Failure analysis: what kinds of highlights the gate almost dropped (informs whether a
      tier-1.5 cheap-LLM transcript scorer is needed — if so, file it in FUTURE_PHASES or a new task)
