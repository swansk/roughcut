# Research Studies

Directed studies that replace assumptions with measurements. Each `RQ:` marker in
[../docs/SPEC.md](../docs/SPEC.md) maps to one study here. Rules:

- A study is **done** when its own DoD passes and its results are written to a
  `R#-report.md` next to the protocol, **and** the SPEC's RQ registry row is updated with the
  decision + link. Provisional values in TASKS.md DoDs are then replaced with measured ones.
- Studies must be **reproducible**: the protocol file states the exact dataset, commands, and
  metric definitions; report includes raw numbers, not just conclusions.
- Studies run on whichever backend is configured (SPEC §6). On the Claude Max / CLI backend
  there is no marginal spend, so **studies are budgeted and reported in tokens**, with
  `projected_usd` recorded alongside so production affordability stays answerable.

| ID | Study | Decides | Needs | Est. cost |
|---|---|---|---|---|
| [R7](R7-analysis-policy.md) | Analysis policy bake-off (**runs before R1**) | per-shot uniform vs adaptive coarse-to-fine vs hybrid; whether the dense pass needs a frontier VLM | B1 highlight labels + proxies | subscription (~$10–25 projected) |
| [R1](R1-vlm-sampling.md) | VLM sampling & model selection (within R7's winning policy) | keyframes/shot or sheet density, resolution, model tier, batch prompt | R7 + T3 shots + labeled subset of B1 | subscription (~$5–20 projected) |
| [R2](R2-shot-detection.md) | Shot-detection tuning | detector, threshold, T3 F1 target | B1/B2 + human cut lists | $0 |
| [R3](R3-asr-sizing.md) | ASR model sizing | whisper model, T4 WER target | B1 audio + 10-min reference transcript | $0 |
| ~~R4~~ | **Deferred** — [gate recall curve](R4-gate-recall.md). Gating is off by default at the 3–5h design scale (SPEC §7): analyzing everything costs < $15, so there's no reason to accept dropped-highlight risk. Revive only if 20h bins appear. | — | — | — |
| [R5](R5-frame-accuracy.md) | Frame-accuracy harness | how T10 renders are verified | ffmpeg only | $0 |
| [R6](R6-quality-rubric.md) | Rough-cut quality rubric | how T13 output is judged | Karl's judgment | $0 |

## Closed on real bins

Run against footage rather than planned against the SPEC. Their reports are the decision record,
and they are the studies most likely to be re-litigated.

| ID | Study | Decided | Cost |
|---|---|---|---|
| [R8](R8-audio-signals.md) | Audio signals on B1 — what the Tier A DSP is worth | the transcript is the signal; the DSP speech detector is weak and ASR makes it redundant | $0 |
| [R9](R9-events-and-wind.md) | Audio event tagging, and the wind detector it exposed as dead | no non-verbal reaction signal exists on this footage at any threshold; the wind rule is recalibrated to three terms | $0 |
| [R10](R10-events-priority.md) | Sorting what was seen, and what a closer look is worth | rank on **confirmation**, not on kind — 11 adjudicated event claims, none survived as described, because a 45° helmet-cam horizon reads as "rider inverted mid-air" at 4s and at 1s alike. Density is not the expensive axis; coverage is | $1.68 |
