# Changelog

All notable changes to Roughcut are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [SemVer](https://semver.org/)
once code exists; spec-phase entries go under [Unreleased].

Every commit that changes behavior or documentation adds an entry under [Unreleased], in the
same commit. Releases move entries into a dated version section.

## [Unreleased]

### Added
- Task board (docs/TASKS.md): T0–T13 with per-task Definitions of Done and verification
  commands, dependency graph, and DECISION items for Karl.
- Phase 1 technical specification (docs/SPEC.md): architecture, pipeline stages S0–S6, SQLite
  index schema, OTIO conventions, model/cost policy with hard budget cap, RQ registry.
- Repository scaffold: README with architecture overview, .gitignore (media files excluded), this changelog.
