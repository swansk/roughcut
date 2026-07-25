# Changelog

All notable changes to Roughcut are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [SemVer](https://semver.org/)
once code exists; spec-phase entries go under [Unreleased].

Every commit that changes behavior or documentation adds an entry under [Unreleased], in the
same commit. Releases move entries into a dated version section.

## [Unreleased]

### Added
- WSL2 development environment provisioned without sudo: static ffmpeg 7.0.2, uv 0.11.32 and
  the claude CLI installed to `~/.local/bin`, PATH persisted, B1 copied to ext4.
- **R7 dataset integrity controls**: exclude pre-curated clips, and neutralize filenames before
  any policy session sees the footage.
- Draft quality rubric (research/R6-rubric.md): 8 criteria with anchored 1/3/5 descriptions,
  scoped to rough cuts, plus a provisional pass bar pending calibration against real edits.
- Labeling guide (benchmarks/LABELING.md): the highlights pass needed to reach CP2, aimed at
  the failure modes the studies measure (audio-only moments, sub-5s highlights, watching past
  the boring stretches rather than skipping to remembered ones).
- **Start-here sequencing in docs/TASKS.md**: run R7 *before* building the pipeline. R7 needs
  only ffmpeg, a contact-sheet script, footage and labels — so the thesis go/no-go (CP2) is
  reachable in a fraction of the work T0–T6 would cost, and the scaffold gets built on a
  validated premise.
- **Inference abstraction with two backends** (SPEC §6, task T0c): a single `Backend` protocol
  over a Claude Code CLI implementation (Claude Max subscription — the development default,
  zero marginal cost) and an Anthropic API implementation (per-token, Batch-capable — the
  production path), selected by `ROUGHCUT_BACKEND`. The interface is a deliberate lowest common
  denominator: images passed by path rather than base64, and schema conformance defined as a
  required outcome rather than a required mechanism, so the CLI backend can satisfy it by
  prompt-and-validate while the API backend uses native structured outputs. A contract
  conformance suite runs against both backends.
- Cost ledger records `backend`, tokens, `projected_usd` (API-rate equivalent) and nullable
  `actual_usd`; **budget caps are enforced on `projected_usd` on both backends**, so developing
  on a subscription doesn't lose the ability to answer whether the pipeline is affordable in
  production. The CLI backend adds a per-run call ceiling, since a subscription's scarce
  resource is requests-per-window rather than dollars.
- Four review checkpoints (CP1 after ingest, CP2 after R7 as an explicit thesis go/no-go,
  CP3 after first skeleton, CP4 after first rendered cut), with defined agent stop-and-report
  behavior in CLAUDE.md.
- Research study R7 (analysis policy bake-off): tests uniform per-shot sampling against a
  coarse-to-fine adaptive search (contact sheets + recursive deep-dive, prototyped via Claude
  Code CLI sessions) and a hybrid; includes a conditional model-landscape arm. Sequenced
  before R1.
- Agent operating manual (CLAUDE.md): closed-loop session protocol — pick task → implement →
  run DoD verification → iterate until green → commit with changelog — plus commit discipline,
  research-before-assumption rule, and inference/cost guardrails.
- Future-phase context tracker (docs/FUTURE_PHASES.md), benchmark registration guide,
  research studies R1–R6, task board T0–T13 with per-task Definitions of Done, the Phase 1
  technical specification, and the repository scaffold.

### Changed
- **Linux-first, container-ready** (SPEC §9): development in WSL2/Linux, production target a
  Linux box. Portability rules (no absolute media paths, one subprocess wrapper, pathlib only,
  env-var config, pinned ffmpeg) plus task T0b enforcing them via a Dockerfile and an
  in-container test run.
- **Design scale reduced to 3h typical / 5h max** (was 20h). Consequences: budget cap $15
  default / $40 hard fail (was $50/$100); cheap-signal **gating disabled by default** —
  analyzing everything costs less than the recall risk is worth; T5 rescoped from "signals +
  gate" to "signals"; study R4 deferred with its protocol preserved.
- **Model IDs are configuration, not architecture**: choices resolve through named roles in
  `config.py` from env vars; no model ID literal outside that file, and no model-version
  attribution recorded in commits or docs.
- Research studies R1 and R7 budget and report in **tokens**, with dollars as a projection.
  R7 gains a control for dev-backend bias: policy A needs ~20× the calls of the coarse
  policies, painful on a subscription but a non-issue on the API's Batch endpoint, so policies
  are ranked on tokens and recall with call count reported separately.
- Benchmark bins registered (D1): B1 = Killington 01-2026, B2 = Copper 02-2026, B3 = Mt. Marcy
  02-2025 held out. GoPro `.LRV` files may serve as free proxies (to check at T2).

### Fixed
- **Corrected the B2 `.WAV` inference.** ffprobe shows `pcm_s32le` 48kHz **4-channel** 32-bit
  audio with duration matching the paired MP4 — GoPro's raw mic-array capture, same camera and
  clock, not an external mic. External-audio sync returns to unscheduled; the raw array is
  refiled under P2.2 audio post as a wind-noise-reduction opportunity.
- **Measured real bin durations** (previously estimated from file size): Killington 43 min,
  Copper 11 min, Mt. Marcy 8 min — ~1 hour total, well under the 3h/5h design target. Formats:
  B1 is H.264 4K 29.97fps, B2/B3 are HEVC 5.3K 23.976fps.
- **Flagged B1 as pre-curated** — its files are hand-named after their content
  (`bombbeginning`, `tastytrees`, …), so a human already removed the boring material. Measuring
  highlight-finding on it would have produced a falsely positive CP2. Mitigated by restricting
  R7 to the nine long-form clips and neutralizing filenames; raw unculled footage would be
  strictly better if any is available.
