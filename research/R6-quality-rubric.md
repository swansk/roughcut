# R6 — Rough-Cut Quality Rubric

**Question:** What does "a good rough cut" mean, concretely enough that T13's human eval is a
measurement rather than a vibe — and what threshold constitutes Phase 1 success?

**Replaces the assumption:** "Karl watches it and decides if it's good."

## Method
1. Draft a rubric of 6–10 criteria, each scored 1–5 with written anchors for 1/3/5. Candidate
   criteria: coverage (nothing important missing), selection (best takes chosen), pacing,
   story arc / ordering, cut cleanliness (no mid-word or mid-action chops), audio comfort
   (levels, no clicks), goal fit (matches the brief), length discipline (near target duration).
2. **Calibrate against real edits:** score 2–3 existing human-made videos (e.g. a past ski-trip
   edit) and one deliberately bad auto-cut (random shots concatenated) with the draft rubric.
   If the bad cut doesn't score clearly worse, the anchors are broken — revise.
3. Define the Phase 1 pass bar from calibration (e.g. "mean ≥ 3.5 with no criterion below 2"),
   set relative to where the human edits landed.
4. Produce a scoring sheet template (`benchmarks/rubric-sheet.md`) usable per run.

## Definition of Done
- [ ] Rubric with anchored 1/3/5 descriptions committed as `R6-rubric.md`
- [ ] Calibration scores for the reference videos recorded in `R6-report.md`; bad-cut
      separation demonstrated
- [ ] Phase 1 pass bar defined and written into T13's DoD and SPEC §1 (RQ-6 resolved)
