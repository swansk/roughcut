# Changelog

All notable changes to Roughcut are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [SemVer](https://semver.org/)
once code exists; spec-phase entries go under [Unreleased].

Every commit that changes behavior or documentation adds an entry under [Unreleased], in the
same commit. Releases move entries into a dated version section.

## [Unreleased]

### Added
- Draft quality rubric (research/R6-rubric.md): 8 criteria with anchored 1/3/5 descriptions,
  scoped to rough cuts, plus a provisional pass bar pending calibration against real edits.
- Labeling guide (benchmarks/LABELING.md): the highlights pass needed to reach CP2, with
  guidance aimed at the failure modes the studies measure (audio-only moments, sub-5s
  highlights, contiguous rather than sampled footage).
- **Start-here sequencing in docs/TASKS.md**: run R7 *before* building the pipeline. R7 is a
  Claude Code CLI prototype needing only ffmpeg, a contact-sheet script, footage and labels —
  so the thesis go/no-go (CP2) is reachable in a fraction of the work that T0–T6 would cost,
  and the scaffold gets built on a validated premise.
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

### Changed
- Research studies R1 and R7 now budget and report in **tokens** with dollars as a projection.
  R7 gains an explicit control for dev-backend bias: policy A needs ~20× the calls of the
  coarse policies, which is painful on a subscription but a non-issue on the API's Batch
  endpoint, so policies are ranked on tokens and recall with call count reported separately.
- **Linux-first, container-ready** (SPEC §9): development moves to WSL2/Linux, production
  target is a Linux box. Portability rules (no absolute media paths, one subprocess wrapper,
  pathlib only, env-var config, pinned ffmpeg) plus new task T0b enforcing them via a
  Dockerfile and an in-container test run.
- **Design scale reduced to 3h typical / 5h max** (was 20h), measured against the real footage
  bins. Consequences: budget cap $15 default / $40 hard fail (was $50/$100); cheap-signal
  **gating disabled by default** — analyzing everything costs less than the recall risk is
  worth; T5 rescoped from "signals + gate" to "signals"; study R4 deferred with its protocol
  preserved for a possible 20h future.
- **Model IDs are configuration, not architecture**: model choices resolve through named roles
  in `config.py` (`ROLE_SKELETON`/`ROLE_ANALYSIS`/`ROLE_JUDGE`) from env vars; no model ID
  literal outside that file, no model-version attribution recorded in commits or docs.
- Benchmark bins registered (D1 resolved): B1 = Killington 01-2026, B2 = Copper 02-2026,
  B3 = Mt. Marcy 02-2025 held out. Recorded two footage findings: GoPro `.LRV` files may serve
  as free proxies, and B2's paired `.WAV` files promote external-audio sync from Phase 3
  speculation to likely Phase 2 work.

### Added
- Four review checkpoints (CP1 after ingest, CP2 after R7 as an explicit thesis go/no-go,
  CP3 after first skeleton, CP4 after first rendered cut), with defined agent stop-and-report
  behavior in CLAUDE.md.
- Research study R7 (analysis policy bake-off): tests uniform per-shot sampling against
  Karl's proposed coarse-to-fine adaptive search (contact sheets + recursive deep-dive,
  prototyped via Claude Code CLI sessions) and a hybrid; includes a conditional model-landscape
  arm (video-native / local VLM for the dense pass). Sequenced before R1; R1, SPEC (RQ-7),
  and T7 updated accordingly.
- Agent operating manual (CLAUDE.md): closed-loop session protocol — pick task → implement →
  run DoD verification → iterate until green → commit with changelog — plus commit discipline,
  research-before-assumption rule, and cost guardrails for multi-hour autonomous sessions.
- Future-phase context tracker (docs/FUTURE_PHASES.md): Phase 2/3 scope with rationale and
  the design hooks Phase 1 leaves for each item; living document updated as scope is deferred.
- Benchmark registration guide (benchmarks/README.md): bin manifests, label formats, target
  bins B1–B3.
- Research studies R1–R6 (research/): protocols with methods, datasets, decision rules and
  per-study DoDs for VLM sampling/model choice, shot detection, ASR sizing, gate recall,
  frame-accuracy harness, and the rough-cut quality rubric.
- Task board (docs/TASKS.md): T0–T13 with per-task Definitions of Done and verification
  commands, dependency graph, and DECISION items for Karl.
- Phase 1 technical specification (docs/SPEC.md): architecture, pipeline stages S0–S6, SQLite
  index schema, OTIO conventions, model/cost policy with hard budget cap, RQ registry.
- Repository scaffold: README with architecture overview, .gitignore (media files excluded), this changelog.
