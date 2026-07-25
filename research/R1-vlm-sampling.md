# R1 — VLM Sampling & Model Selection

**Question:** For the tier-2 per-shot analysis, what combination of (K keyframes per shot,
keyframe resolution, model tier) gives the best highlight-agreement and description quality per
dollar — and what does a full 20h project actually cost at that config?

**Replaces the assumption:** "a few frames per shot on a mid-tier model is good enough" and the
SPEC §7 envelope math ($5–40/project), both currently guesses.

## Method
1. **Dataset:** 40-shot stratified subset of B1 (10 human-marked highlights, 10 near-misses,
   20 random), labeled by Karl with: 1-line ground-truth description, highlight y/n,
   usable y/n. Labeling protocol in [../benchmarks/README.md](../benchmarks/README.md).
2. **Grid:** K ∈ {1, 3, 5} × resolution ∈ {512px, 768px, 1092px long edge} × model ∈
   {`claude-haiku-4-5`, `claude-sonnet-5`, `claude-opus-5`}. Prune the grid greedily (start at
   K=3/768/Sonnet; explore neighbors) — full grid is 27 cells × 40 shots and unnecessary.
3. **Prompt held constant** (versioned in this repo under `research/prompts/r1/`); structured
   output per SPEC §5.4. All calls via Batch API; log to cost ledger.
4. **Metrics per cell:**
   - Highlight agreement: AUROC of model interestingness vs human highlight label
   - Description quality: LLM-judge (Opus 5) pairwise vs ground-truth, 1–5 scale, blind to cell
   - Usability-flag accuracy vs labels
   - Measured $ per shot → extrapolated $ per 20h project (using T3's real shot counts)
5. **Decision rule:** cheapest cell whose AUROC is within 0.03 of the best cell and whose
   description score ≥ 4.0. Tie-break on cost.

## Definition of Done
- [ ] All evaluated cells have complete metrics + raw outputs archived under `research/runs/r1/`
- [ ] Report `R1-report.md` with the metric table, chosen config, and measured $/20h projection
- [ ] SPEC §6 + RQ-1 row updated; T7 DoD updated with the measured overlap target
- [ ] Total study spend recorded (target < $20)
