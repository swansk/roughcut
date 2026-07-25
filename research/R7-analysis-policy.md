# R7 — Analysis Policy Bake-off (adaptive agentic search vs uniform per-shot)

**Runs BEFORE R1.** R1's grid assumes the per-shot K-keyframes representation; this study
tests that assumption against competing analysis *policies*. R1 then runs its model/parameter
grid inside whichever policy wins.

**Question:** What analysis policy finds the interesting parts of a 20h bin best, per dollar
and per hour of wall-clock:

- **Policy A — uniform per-shot** (current SPEC §3 S2 design): K keyframes per surviving shot,
  one structured call each, Batch API.
- **Policy B — coarse-to-fine adaptive search** (Karl's proposal): sample sparsely across the
  whole timeline first (e.g. 1 frame / N seconds tiled into **contact sheets**), score segments
  coarsely, then recursively deep-dive only into segments that look interesting — a best-first
  search over the footage, terminating when refinement stops changing scores.
- **Policy C — hybrid**: tier-0/1 signal gate (audio/motion/transcript) chooses candidate
  regions as in the SPEC, but tier-2 uses contact sheets + one refinement level instead of
  per-shot calls.

**Secondary question (model landscape):** does the *dense/coarse* pass need a frontier VLM at
all? Perception ("what is happening in these frames") and judgment ("is this worth including,
given the brief") are separable. Candidates for the dense pass if Claude-tier cost dominates:
a video-native API (Gemini consumes video directly, priced per second), or a local open-weight
VLM (e.g. Qwen2.5-VL — zero marginal cost, matters at 20h scale), with Claude reserved for
judgment/re-scoring of candidates. Only investigate this arm if the winning policy's projected
cost still exceeds the SPEC §7 envelope.

**Replaces the assumptions:** "per-shot uniform sampling is the right unit of analysis" and
"one model does both perception and judgment".

## Why contact sheets matter (the cost mechanism)
A 5×6 grid of ~200px thumbnails with timestamp burn-ins is one image (~1,100–1,600 tokens)
covering 30 sample points — roughly an order of magnitude cheaper per frame than individual
images, at the price of resolution. The bet behind Policy B/C is that coarse resolution is
enough to *locate* interest, with full-resolution frames only for the deep dive.

## Known risk to measure, not assume away
Adaptive search's failure mode is **recall on brief highlights**: a 2-second gem inside a
boring 10-minute segment is invisible if the coarse sample misses it and the segment scores
low (so it's never refined). Audio-driven moments (a shout, laughter, "watch this!") are
invisible to any pure-visual policy — which is why Policy C keeps the tier-0/1 signals in the
loop rather than replacing them. The label set from R4 (inclusive highlight marks) is exactly
the instrument to measure this.

## Method
1. **Harness: Claude Code CLI, no pipeline code.** Each policy is an agentic session (Sonnet 5
   or Opus 5 as the session model) given: B1 proxies for a ≥2h slice, ffmpeg/ffprobe, a small
   contact-sheet helper script (`research/tools/contact_sheet.py` — the only code this study
   builds), the brief, and a policy prompt (versioned under `research/prompts/r7/`). The agent
   explores and emits a ranked list of (start_s, end_s, score, rationale) candidate segments.
   This deliberately prototypes the whole analysis concept before T7 hardens it into pipeline
   code.
2. **Policies run on the same slice** with the same output contract. Policy A is simulated with
   the same harness (scripted per-shot calls) so cost accounting is comparable.
3. **Metrics per policy:**
   - Highlight recall@budget: fraction of Karl's labeled highlights overlapped by any
     candidate segment, at **matched token budgets** per hour of footage (evaluate at two
     levels, roughly 0.3M and 1M input tokens per footage-hour)
   - Brief-highlight recall (labels < 5s long) reported separately — the adaptive failure mode
   - Ranking quality: AUROC of candidate scores vs labels
   - **Tokens** (input/output) and wall-clock per hour of footage, plus `projected_usd` at API
     rates → 5h-bin projection. Development runs on the Max subscription where marginal cost is
     zero, so **tokens are the currency of this study**; dollars are a projection (SPEC §7).
   - Call count per hour of footage — the scarce resource on the subscription backend, and the
     axis on which policy A is worst (~500–1,000 sequential calls vs ~20–50 for B/C)
   - Determinism/reproducibility notes (adaptive runs are path-dependent; quantify run-to-run
     variance over 2 repeats)
4. **Decision rule:** prefer the cheapest policy **by tokens** whose overall recall ≥ 95% AND
   brief-highlight recall ≥ 90% at the higher budget level; tie-break on wall-clock. If B or C
   wins, R1's grid variables change from (K, resolution, model) to (sheet density, refinement
   depth, model) — amend R1 before running it.

   > **Control for dev-backend bias.** Policy A needs ~20× the calls of B/C, which makes it
   > genuinely unpleasant on a subscription backend — slow, and a large bite out of a rolling
   > window. That is a production consideration only if it *also* shows up in tokens or quality;
   > the API backend's Batch support makes raw call count a non-issue in production. Rank on
   > tokens and recall, report call count separately, and do not let it pick the winner. If A
   > wins on quality per token, A wins.
5. **Model-landscape arm (conditional):** if the winner's 5h projection exceeds the SPEC §7 cap
   ($15), rerun its coarse pass with (a) a video-native model and (b) a local open-weight VLM
   (the RTX 5080's 16GB fits a 7B-class model), keeping Claude for refinement/judgment; compare
   recall and tokens. Adopting a non-Claude dense pass is a DECISION for Karl (a second provider
   dependency), not an automatic switch.

## Definition of Done
- [ ] Contact-sheet helper exists with tests (grid layout, timestamp burn-in readable)
- [ ] Policies A/B/C each run on the same ≥2h labeled slice; sessions archived under
      `research/runs/r7/` (transcripts, candidate lists, token usage)
- [ ] Metric table incl. brief-highlight recall and run-to-run variance in `R7-report.md`
- [ ] Decision recorded in SPEC (RQ-7): winning policy, and consequences for S2's design
- [ ] R1 protocol amended to run inside the winning policy's representation
- [ ] Model-landscape arm run + reported IF triggered by the cost condition; otherwise a note
      that it wasn't triggered and stays available
- [ ] Study run on the subscription backend (no marginal spend); token totals and
      `projected_usd` per policy logged to the cost ledger
