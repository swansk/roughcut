# Roughcut

An AI video-editing agent. Phase 1 goal: **the rough-cut generator** — point it at a source bin
(3h typical, 5h max) and a stated goal, and get back a watchable, frame-accurate rough cut with
normalized audio.

Linux-first, container-ready. Developed in WSL2/Linux, targeted at a Linux box; see
[docs/SPEC.md](docs/SPEC.md) §9.

Architecturally, Roughcut is a **compiler for videos**, not a video editor:

```
source bin ──► ingest ──► analysis ──► index ──► skeleton (human-in-loop) ──► assembly ──► audio ──► export
              (proxies,   (transcript,  (SQLite)   (OTIO timeline)             (FFmpeg)     (loudness)
               shots)      signals,
                           VLM pass)
```

- The **OpenTimelineIO (OTIO) timeline is the spine**. Every pipeline stage is a transformation
  over data structures; rendering is a swappable backend (FFmpeg now, NLE export later).
- The **analysis index is the second pillar**: a persistent, queryable SQLite artifact holding
  shots, transcripts, signal scores, and VLM descriptions. The skeleton conversation and all
  future revision loops query it.
- **Cost is a hard requirement, not an optimization**: inference for a 20h project must stay
  under budget via a hierarchical analysis pass (free signals → cheap scoring → VLM only on
  survivors). See [docs/SPEC.md](docs/SPEC.md) §7.

## Repository guide

| File | Purpose |
|---|---|
| [docs/SPEC.md](docs/SPEC.md) | Phase 1 technical specification — architecture, schemas, pipeline stages |
| [docs/AUDIO.md](docs/AUDIO.md) | Audio analysis design — the cheap fast pass that maps *where* things happen |
| [docs/HANDOFF.md](docs/HANDOFF.md) | **Current state and next actions — read this first in a new session** |
| [docs/TASKS.md](docs/TASKS.md) | Task board. Every task has a Definition of Done with verification commands |
| [research/](research/README.md) | Directed research studies (R1–R6) that replace assumptions with measurements |
| [docs/FUTURE_PHASES.md](docs/FUTURE_PHASES.md) | Phase 2/3 scope, kept current as deferred work is discovered |
| [benchmarks/README.md](benchmarks/README.md) | How to register benchmark footage bins and ground-truth labels |
| [CLAUDE.md](CLAUDE.md) | Agent operating manual: closed-loop iteration protocol for long sessions |
| [CHANGELOG.md](CHANGELOG.md) | Keep-a-Changelog format; every behavior/docs change gets an entry |

## Setup (Linux / WSL2)

```bash
# System tools
sudo apt update && sudo apt install -y ffmpeg
ffmpeg -version   # record the major version; it is pinned in the container image

# Python toolchain
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync

# Secrets (never committed)
export ANTHROPIC_API_KEY=...    # or put it in .env, which is gitignored
```

On WSL2, copy the working footage bin into the ext4 filesystem rather than reading it across
`/mnt/c` — the 9p bridge is slow enough to distort transcode timings. Register the bin's path
in `benchmarks/bins/B1.json`; no media path is ever hardcoded in code or tests.

## Status

Spec phase. No pipeline code yet — see docs/TASKS.md for the build order.

## Working on this repo

Development is agent-driven (Claude Code) with human review. The contract is in CLAUDE.md:
one feature per commit, changelog entry per change, and **no task is marked done until every
item in its Definition of Done passes with fresh command output**.
