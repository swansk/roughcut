# Changelog

All notable changes to Roughcut are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [SemVer](https://semver.org/)
once code exists; spec-phase entries go under [Unreleased].

Every commit that changes behavior or documentation adds an entry under [Unreleased], in the
same commit. Releases move entries into a dated version section.

## [Unreleased]

### Added
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
