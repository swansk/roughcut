# R2 — Shot Detection Tuning

**Question:** Which PySceneDetect detector (ContentDetector / AdaptiveDetector / HashDetector)
and threshold gives the best cut F1 on real consumer footage, including VFR phone clips and
continuous handheld action (ski runs), and what F1 target should T3's DoD assert?

**Replaces the assumption:** default ContentDetector threshold 27 works, and the provisional
F1 ≥ 0.85 target.

## Method
1. **Ground truth:** human cut lists for 30 minutes of B1 + 15 minutes of B2 (frame-number of
   each true cut), labeled per benchmarks/README protocol. Include at least 5 VFR phone clips
   and 2 long continuous action clips (no cuts — false-positive stressors).
2. **Grid:** each detector × threshold sweep (detector-appropriate ranges), plus min-scene-len
   ∈ {0.5s, 1s, 2s}. Run on proxies (as production will).
3. **Matching:** predicted cut matches truth if within ±12 frames.
4. **Metrics:** precision, recall, F1 overall and per-clip-class (phone VFR vs camera CFR vs
   continuous action).
5. **Decision rule:** highest F1 config with recall ≥ precision preference (missed cuts hurt
   downstream more than extra cuts — extra cuts merge naturally at skeleton time). Set the T3
   DoD target at (best F1 − 0.03) to allow implementation noise.

## Definition of Done
- [ ] Metric table per config and clip class in `R2-report.md`; raw predicted cut lists archived
- [ ] Chosen detector/threshold/min-scene-len recorded in SPEC (RQ-2 resolved)
- [ ] T3 DoD provisional target replaced with measured target
- [ ] A note on failure modes observed (e.g., exposure flicker false cuts) for T3 implementation
