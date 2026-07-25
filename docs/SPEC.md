# Roughcut — Phase 1 Technical Specification

Status: **draft v1** — parameters marked `RQ:` are open research questions; do not hardcode
values for them from guesswork. Each maps to a study in [../research/](../research/README.md),
and its resolution is recorded back into this document with a link to the study report.

## 0. Product intent (Karl's framing — this constrains design choices)

> Turn video editing from a very manual, time-consuming task into an AI-forward one, where the
> human is engaged where they are really needed, in a way that's fun to use.

Three design principles follow, and they are testable rather than decorative:

1. **Human time goes to taste and judgment, never to mechanical work.** Choosing which take is
   better, what the story is, whether a moment lands — that's the human. Scrubbing, trimming,
   logging, leveling, transcoding — that's the machine. *If we find ourselves asking the human
   to do something mechanical, that is a design bug, not a chore to delegate.*
2. **Fast feedback, or the human disengages.** Nobody has fun waiting on a render. Any loop the
   human is inside (skeleton, revision, review) must return in seconds-to-a-minute — which is
   what proxy-based draft renders are for. Slow, correct, final renders happen off the loop.
3. **Present options, don't demand specifications.** Reacting to three concrete proposals is
   fast, fun, and plays to human taste. Authoring a spec from a blank page is slow, tiring, and
   plays to nobody's. The agent should arrive with a proposal and a rationale.

**Applied to our own process, right now:** the labeling pass in
[../benchmarks/LABELING.md](../benchmarks/LABELING.md) asks Karl to scrub footage manually,
which is precisely the activity this product exists to eliminate. That is principle 1 being
violated by the project's own tooling. Mitigation: build the contact-sheet index first so
labeling is a fast visual pass over an image grid rather than a video-scrubbing session.

## 1. Scope

**In (Phase 1 — "rough-cut generator"):**
- S0 Ingest & normalization (probe, proxies, shot detection)
- S1 Goal capture (structured brief)
- S2 Analysis pass (transcription, cheap signals, hierarchical VLM) → persistent index
- S3 Skeleton co-creation (chat-based, produces an OTIO timeline)
- S4 Assembly (OTIO → FFmpeg render, frame-accurate)
- S5 Basic audio post (loudness normalization, crossfades at cuts)
- S6 Export (16:9 presets)
- E2E benchmark harness + human eval rubric

**Out (tracked in [FUTURE_PHASES.md](FUTURE_PHASES.md)):** music & beat-aligned cutting, full
audio post (ducking, NR), color correction, captions/titles, the NL revision loop, multicam
sync, vertical auto-reframe, skeleton web UI, NLE (Premiere/Resolve) export.

**Success criterion for Phase 1:** given benchmark bin B1 and a goal, the pipeline runs
end-to-end unattended and produces a rough cut that a human rates ≥ threshold on the R6 rubric,
at inference cost within budget (§7).

**Input formats:** the pipeline is format-agnostic by design — anything ffmpeg decodes is
acceptable, and no stage may assume a container or codec. The prototype exercises MP4
(H.264 and HEVC) because that is what the benchmark bins contain. Sidecar audio files (e.g.
GoPro's `.WAV` mic-array captures) are **out of scope**: audio comes from the MP4's embedded
track. See [FUTURE_PHASES.md](FUTURE_PHASES.md) P2.2 for the deferred mic-array opportunity.

## 2. Architecture

Headless Python core + CLI. No editor UI in Phase 1; the human-in-the-loop step (S3) is a chat
session driven by Claude Code / a thin CLI conversation. Rendering is FFmpeg. OTIO files are
the only representation of edit decisions — no stage may hold edit state anywhere else.

```
roughcut/
├── pyproject.toml            # uv-managed; Python 3.12+
├── src/roughcut/
│   ├── cli.py                # `roughcut <command>` entry points (typer)
│   ├── config.py             # project paths, budgets, model ROLES, backend selection
│   ├── shell.py              # the only subprocess wrapper (no shell=True anywhere)
│   ├── costs.py              # cost ledger — every inference call logs tokens + projected $
│   ├── inference/            # §6 — the ONLY place that talks to a model
│   │   ├── base.py           # Backend protocol, Request/Result types
│   │   ├── claude_cli.py     # Claude Code CLI backend (Max subscription)
│   │   └── anthropic_api.py  # Anthropic API backend (per-token, Batch API)
│   ├── ingest/
│   │   ├── probe.py          # ffprobe wrapper → media manifest
│   │   ├── proxy.py          # proxy transcode (VFR→CFR normalization)
│   │   └── shots.py          # PySceneDetect wrapper → shot table
│   ├── analyze/
│   │   ├── asr.py            # faster-whisper transcription
│   │   ├── signals.py        # audio RMS, motion magnitude, speech density
│   │   ├── vlm.py            # Claude vision pass (Batch API), hierarchical
│   │   └── scoring.py        # interestingness aggregation
│   ├── index/
│   │   ├── db.py             # SQLite connection, migrations
│   │   ├── schema.sql        # authoritative DDL
│   │   └── queries.py        # typed query helpers (by time, score, FTS)
│   ├── brief/brief.py        # goal-capture schema + validation
│   ├── skeleton/
│   │   ├── tools.py          # agent-facing tools over the index
│   │   └── otio_build.py     # skeleton → OTIO timeline
│   └── render/
│       ├── assemble.py       # OTIO → FFmpeg filtergraph, frame-accurate
│       ├── audio.py          # loudness normalize, crossfades
│       └── presets.py        # export preset table
├── tests/                    # pytest; synthetic fixtures generated by ffmpeg
├── research/                 # study protocols + reports (see research/README.md)
├── benchmarks/               # manifests + labels pointing at external footage
└── docs/
```

**Project workspace layout** (per user project, outside the repo):

```
<project>/
├── project.json        # brief + config
├── index.db            # the analysis index (SQLite)
├── proxies/            # 540p proxy files
├── timelines/          # skeleton_v1.otio, roughcut_v1.otio, ...
├── artifacts/          # keyframe JPEGs, waveforms
└── renders/
```

## 3. Pipeline stages

### S0 — Ingest & normalization
1. **Probe** every file with ffprobe: codec, resolution, fps, VFR flag, duration, creation
   time, camera metadata, color transfer/primaries (log profile detection), and **rotation
   side-data**. → `media` table.

   > **Rotation is recorded, never trusted — and correcting it is our job.** Two distinct
   > problems, both present in real footage:
   >
   > 1. *Misleading metadata.* Measured on B1 (2026-07-25): `GX010474.MP4` carries
   >    `rotation=-90` whose application yields a sideways portrait frame, while `GX010475.MP4`
   >    in the same bin carries none. The bin is mixed and the metadata is wrong on at least
   >    one clip.
   > 2. *Genuinely mis-shot footage* (Karl, 2026-07-25). Some clips are simply rotated the wrong
   >    way from the start — a camera mounted rotated, pointed sideways — with metadata that is
   >    absent or unhelpful. **The pipeline is expected to fix these, not merely to pass them
   >    through.** Orientation is therefore a *derived, corrected property of the content*, not
   >    a container field to be copied.
   >
   > **The two bins prove the rule the hard way — they behave in exactly opposite ways:**
   >
   > | Bin | Stored pixels | Side-data | Correct action |
   > |---|---|---|---|
   > | B1 Copper | upright | −90 / +90 / −180 on 6 of 26 clips | **ignore it** (`none`) — applying it corrupts the frame |
   > | B2 Killington | **upside down** on 7 of 9 clips | −180 | **apply it** (`auto`) — the metadata is correct |
   >
   > Same footage owner, same sport, same season. Any global policy — "always honour rotation",
   > "always ignore it" — is wrong on one of these two bins. Orientation must be resolved
   > per clip, from the pixels.
   >
   > Resolution order: read the side-data → decide from the pixels → persist an
   > `orient_override` per clip. Deciding from pixels is a vision task the analysis layer is
   > already good at ("which way up is this frame?" on a single thumbnail is cheap and
   > reliable), so S2 may assist S0 here; where confidence is low, surface it at CP1 rather than
   > guessing. A global `-noautorotate` is **not** the fix — portrait phone footage must keep
   > working.
2. **Proxy** transcode: 960×540 H.264 CRF 23, audio AAC 128k, **CFR normalized** (VFR phone
   footage is the #1 source of downstream sync bugs — normalize here, once). Preserve a
   source-frame ↔ proxy-frame mapping so timeline decisions made on proxies resolve to exact
   source frames.
3. **Shot detection** on proxies via PySceneDetect (`RQ: detector + threshold → R2`). Output:
   `shots` table; every downstream stage operates on shot IDs, never raw files.

### S1 — Goal capture
Structured interview producing `brief.json` (schema §5.3): purpose, audience, platform, target
duration, tone, must-include moments, people/subjects of interest. The brief is injected into
every downstream model prompt.

### S2 — Analysis pass (hierarchical; the cost bomb — see §7)
- **Tier 0 (free, local):** per-shot audio RMS/peak envelope, motion magnitude (frame-diff on
  proxies), duration, timestamp clustering. Also flags unusable audio (clipping, silence).
- **Tier 1 (cheap, local):** faster-whisper transcription (`RQ: model size → R3`) with word
  timestamps + simple diarization; speech density per shot.
- **Junk detection (on by default) vs. cost gating (off by default)** — two different things
  that both look like "filtering", and conflating them is a mistake:
  - **Junk detection is a quality filter and stays on.** Real bins contain long stretches with
    no usable content at all — a GoPro left recording in a pocket, a lens-down mount, a
    forgotten camera in a bag (Karl, 2026-07-25: uncommon, but expect it). These are cheap to
    identify from tier-0 signals — near-zero luminance variance, no scene structure, motion
    without parallax — and they must be excluded, because a 20-minute pocket recording will
    otherwise dominate a short bin and waste analysis on darkness. Detection is recorded as
    `audio_flags`/`usable=false` with the reason, and is always reversible.
  - **Cost gating is a budget filter and stays off** (see §7). Discarding *plausibly
    interesting* footage to save money is a bad trade at this scale.

  The ranking is still computed and stored (it orders the analysis queue and drives scoring),
  but `gate_discard` is off unless a project sets a threshold. Re-enabling it for large bins is
  an R4 decision. **Don't over-engineer for junk**: it's an expected edge case, not the common
  path, and the detector should be simple and conservative — when unsure, keep the footage.
- **Tier 2 (paid):** VLM analysis of surviving footage. The default hypothesis is K keyframes
  per shot at resolution R, one request per shot via the **Batch API** (50% discount; not
  latency-sensitive). A competing policy — coarse contact-sheet sampling with recursive
  deep-dive into interesting regions ("binary search for interest") — is under study and may
  replace or hybridize this design (`RQ: analysis policy → R7`; `RQ: K/density, R, model
  tier → R1`). Output per analyzed unit: description, entities/people, action, quality flags,
  interestingness 0–100 with rationale, transition-point suitability.
- **Aggregate:** `scoring.py` combines tiers into a final per-shot score; all raw outputs are
  persisted so scoring can be re-run without re-paying tier 2.

### S3 — Skeleton co-creation
A conversation in which the agent, using tools over the index (search transcript, list top
shots, show keyframes, query by time/entity), proposes a story outline; the user reacts; the
agreed outline is compiled to `skeleton_v1.otio`. The skeleton is a real timeline whose clips
reference shot IDs with in/out points — coarse (section-level) but formally valid.

**Ordering: chronological by default, deliberately not always** (Karl, 2026-07-25). Capture
timestamps (`media.shot_at`) give the default spine, and for trip footage chronology usually
*is* the story. But when a departure makes a better edit — a cold open on the best moment, a
setup shot moved ahead of the thing it sets up, two related moments from different days cut
together — taking it is correct. Two requirements follow:

- Reordering must be **deliberate and explained**, never incidental. Any clip whose timeline
  position departs from chronological order carries a `roughcut.reorder_rationale` in its
  metadata, so the choice is visible in review rather than looking like a sorting bug.
- Chronology must remain *recoverable*: `shot_at` and source order stay in the index, so a
  reordered timeline can always be explained against, or reverted to, the capture sequence.

This is a place where principle 3 (§0) applies directly — the agent should propose an ordering
with its reasoning, not ask the user to specify one.

### S4 — Assembly
`assemble.py` walks the OTIO timeline and renders from **source files** (not proxies) via
FFmpeg: per-clip trim → concat filtergraph. Requirements: cut points frame-accurate on CFR
sources (`RQ: verification method → R5`), A/V sync drift < 20ms end-to-end.

### S5 — Basic audio post
Loudness normalize the program mix to −14 LUFS integrated (YouTube target), true peak
≤ −1 dBTP, via ffmpeg `loudnorm` (two-pass). 10–20ms audio crossfades at every cut to kill
clicks.

### S6 — Export
Preset table (§5.5). Phase 1 ships 16:9 1080p and 4K, H.264 and HEVC. Verified by ffprobe
assertions against the preset.

## 4. OTIO conventions

- One `Timeline`, one video track `V1`, one audio track `A1` (program audio) in Phase 1.
- Every `Clip.media_reference` targets a **source file** with `available_range` from the probe.
- Clip metadata namespace `roughcut`: `{shot_id, score, section, rationale}`.
- Timeline metadata `roughcut`: `{project_id, brief_hash, created_by_stage, parent_timeline}` —
  timelines form a lineage chain (skeleton → roughcut → revisions).
- Markers denote section boundaries (`roughcut.section`) for the future revision loop.
- Rational time everywhere; no float seconds in edit decisions.

## 5. Data schemas (authoritative DDL lives in `src/roughcut/index/schema.sql`)

### 5.1 SQLite index
```sql
CREATE TABLE media (
  id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, duration_s REAL, fps_num INTEGER,
  fps_den INTEGER, is_vfr INTEGER, width INTEGER, height INTEGER, codec TEXT,
  rotation REAL DEFAULT 0,        -- container side-data, recorded not trusted (§3 S0)
  orient_override TEXT,           -- 'auto'|'none'|'cw'|'ccw' after visual verification
  color_transfer TEXT, log_profile TEXT, camera TEXT, shot_at TEXT, probe_json TEXT);

CREATE TABLE shots (
  id INTEGER PRIMARY KEY, media_id INTEGER REFERENCES media(id),
  start_frame INTEGER, end_frame INTEGER, start_s REAL, end_s REAL,
  keyframe_paths TEXT,            -- JSON array of artifact paths
  audio_rms REAL, motion REAL, speech_density REAL, audio_flags TEXT,
  vlm_json TEXT,                  -- raw tier-2 output (null if gated out)
  interestingness REAL, gated_out INTEGER DEFAULT 0);

CREATE TABLE transcript_segments (
  id INTEGER PRIMARY KEY, media_id INTEGER REFERENCES media(id),
  start_s REAL, end_s REAL, speaker TEXT, text TEXT, words_json TEXT);
CREATE VIRTUAL TABLE transcript_fts USING fts5(text, content=transcript_segments);

CREATE TABLE cost_ledger (
  id INTEGER PRIMARY KEY, ts TEXT, stage TEXT, role TEXT, model TEXT,
  backend TEXT NOT NULL,          -- 'claude_cli' | 'anthropic_api'
  input_tokens INTEGER, output_tokens INTEGER,
  actual_usd REAL,                -- null on the subscription backend
  projected_usd REAL NOT NULL,    -- API-rate equivalent; the cap is enforced on this
  batch INTEGER, latency_ms INTEGER);
```

### 5.2 Media manifest — the probe output persisted as `media` rows plus `probe_json` raw dump.

### 5.3 `brief.json`
```json
{
  "version": 1,
  "goal": "string — one-paragraph statement",
  "audience": "string", "platform": "youtube|family|social", "tone": "string",
  "target_duration_s": 300,
  "must_include": ["free text moments"],
  "subjects": ["names/entities of interest"],
  "aspect": "16:9"
}
```

### 5.4 VLM per-shot output (strict JSON schema, enforced via structured outputs)
```json
{
  "description": "string", "entities": ["string"], "action": "string",
  "quality": {"blur": 0, "exposure": "ok|under|over", "usable": true},
  "interestingness": 0, "rationale": "string",
  "good_cut_in": true, "good_cut_out": true
}
```

### 5.5 Export presets
| name | container | video | audio | resolution |
|---|---|---|---|---|
| yt-1080 | mp4 | H.264 CRF 18, high@4.2 | AAC 192k | 1920×1080 |
| yt-4k | mp4 | HEVC CRF 20, main10 | AAC 192k | 3840×2160 |

## 6. Inference layer

> **Model IDs are configuration, not architecture.** Every model choice lives in `config.py` as
> a named role (`ROLE_SKELETON`, `ROLE_ANALYSIS`, `ROLE_JUDGE`) overridable by environment
> variable. No model ID literal may appear at a call site, and no document should treat a
> specific version as load-bearing.

### 6.1 Two backends behind one interface

Development runs on a **Claude Max subscription via the Claude Code CLI** (no marginal
per-token cost); production targets the **Anthropic API** (per-token, batchable). Both sit
behind `roughcut.inference.Backend`, selected by `ROUGHCUT_BACKEND=claude_cli|anthropic_api`.
No pipeline code may import `anthropic` directly or shell out to `claude` — T0c enforces this
with a grep test.

```python
class Backend(Protocol):
    def complete(self, *, role: str, prompt: str,
                 images: Sequence[Path] = (),
                 schema: dict | None = None) -> Result: ...
    def complete_many(self, requests: Sequence[Request]) -> list[Result]: ...
```

`Result` carries `content` (a validated dict when `schema` was given, else text),
`input_tokens`, `output_tokens`, `backend`, `model`, and `projected_usd` (§7).

**The interface is deliberately a lowest common denominator**, because the backends differ in
real ways:

| Concern | `claude_cli` (Max plan) | `anthropic_api` |
|---|---|---|
| Images | passed **by path**, read from disk | base64 content blocks |
| Schema enforcement | prompt for JSON, then validate + bounded retry | native strict structured outputs |
| Batching | none — sequential calls | Message Batches API (50% discount) |
| Marginal cost | none (subscription) | per-token |
| Throughput ceiling | subscription rate limits (rolling windows) | API rate limits |

Two rules follow and must not be violated:

1. **Images are addressed by path in the interface, never base64.** Base64 is an API-backend
   implementation detail.
2. **A schema is a required *outcome*, not a required *mechanism*.** `complete(schema=…)`
   returns a validated object or raises; how each backend gets there is its own business.
   `complete_many` is the only batching surface — the API backend may fan out to the Batch API
   internally; the CLI backend just loops.

### 6.2 Consequences to design around

- **Sequential per-unit workloads are hostile to the CLI backend.** Per-shot analysis of a 5h
  bin is ~500–1,000 calls with per-session startup overhead — slow, and a large bite out of a
  rolling subscription window. Coarse contact-sheet policies (R7 policy B/C) need ~20–50 calls
  for the same bin. That is a genuine argument in their favor *and* a bias R7 must control for:
  policies are ranked on tokens and quality, never on how pleasant they are to run on the dev
  backend.
- **The CLI backend is for development and prototyping**; the API backend is the production
  path. The abstraction exists so that swap is a config change, not a rewrite.
- **Structured-output fidelity is lower on the CLI backend.** Any schema the pipeline depends on
  must be simple enough to survive prompt-and-validate; if a schema only works with native
  strict outputs, that is a design smell to fix in the schema.
- Adaptive thinking defaults; low effort for per-unit scoring calls, high for the S3 skeleton agent.

### 6.3 Reference prices (projection only — verify before quoting)

Per MTok input/output at time of writing: top-tier ≈ $5/$25, mid-tier ≈ $3/$15, small ≈ $1/$5;
Batch API = 50% of those. A high-resolution image can reach ~4,784 tokens; a 768px keyframe far
less, which is why keyframe/sheet resolution is an explicit R1 variable. These numbers feed
`projected_usd` and nothing else — they are not a runtime dependency.

## 7. Cost budget

**Design target: 3h typical, 5h maximum per project** (measured against Karl's real bins —
Killington 01-2026 is the largest at ~31GB). This is far below the 20h figure the pipeline was
originally sized for, and it changes the economics enough to simplify the design:

- Envelope at 5h: ~500–1,000 shots × ~3 keyframes at 768px ≈ 4M input tokens ⇒ roughly **$8 on
  Sonnet-tier batch, ~$14 on Opus-tier** — analyzing *every* shot, with no gating at all.
- **Therefore gating is an optimization, not a requirement** (see §3 S2). Default behavior is
  to analyze everything; the cheap signals still run because scoring and audio-quality flags
  need them, but they do not discard footage by default. This removes the gate's
  dropped-highlight risk entirely at current scale.
- **Default cap: $15 per project; hard fail at $40.** Configurable. The cap exists to catch
  runaway loops and misconfiguration, not to force architectural compromises.
- `roughcut cost report` prints the ledger per stage; the E2E benchmark task (T13) asserts the
  cap held.

**Accounting works identically on both backends.** On the Claude Max / CLI backend there is no
marginal dollar cost, but every call still records `input_tokens`, `output_tokens`, and
`projected_usd` — what the same work *would* cost through the API at §6.3 rates. The budget cap
is enforced against `projected_usd` regardless of backend. Without this, developing on a
subscription would silently destroy the ability to answer "is this affordable in production",
which is the entire reason the budget discipline exists. `actual_usd` is null on the CLI backend.

A second, backend-specific guardrail: the CLI backend also enforces a **per-run call ceiling**
(default 500), because a subscription's scarce resource is requests-per-window rather than
dollars. Exceeding it fails loudly with a pointer to `complete_many` and the coarse-policy
argument in §6.2.

If a future use case genuinely brings 20h bins, re-enable gating via R4 — the study and the
signal plumbing remain available.

## 8. Verification strategy (feeds every DoD)

- **Synthetic fixtures**: tests generate tiny videos with ffmpeg (color bars + frame-number
  burn-in + tone patterns) — no real footage in git. Frame accuracy is asserted by decoding
  rendered frames and reading the burned-in counter (harness built in R5).
- **Loudness**: assert via ffmpeg `ebur128` measurement, ±0.5 LU of target.
- **Schema validity**: jsonschema validation of manifest/brief/VLM outputs in tests.
- **OTIO validity**: files must round-trip through `opentimelineio` load/save; manual
  spot-check that Resolve opens exported skeletons (documented, not automated).
- **Benchmarks**: quality metrics (shot-detection F1, highlight recall, WER) run against
  human-labeled bins registered per [../benchmarks/README.md](../benchmarks/README.md).

## 9. Environment, portability & deployment

**Linux-first from commit 1.** The pipeline is headless Python driving ffmpeg and CUDA — there
is nothing Windows-specific about it, and the production target is a Linux box. Prototyping on
Windows and porting later would mean paying a path/shell/CUDA-setup tax twice for no benefit.

**Development environment: WSL2 on the Windows machine.** This is a genuine Linux userland
(so the code is Linux code from the first commit), while keeping the RTX 5080 available via
CUDA-on-WSL2 and the footage reachable without an immediate transfer. Copy the working bin
into the WSL2 ext4 filesystem rather than reading across `/mnt/c` — the 9p bridge is slow
enough to distort proxy/transcode timings. Moving later to the dedicated Linux SSD box, or
into a container, is then a no-op rather than a port.

**Container-ready from day 1; containerized at T0b.** Native dev is faster to iterate in, so
we don't develop inside a container — but the repo carries a Dockerfile and the test suite must
pass inside it (T0b). That catches works-on-my-machine drift while it's cheap to fix. Rules
that keep it true:

- No absolute media paths in code, tests, or fixtures — media locations come from
  `benchmarks/bins/*.json` manifests or CLI arguments only.
- All external-tool invocation goes through one subprocess wrapper (`roughcut.shell`); no
  shell-string commands, no `shell=True`.
- All paths via `pathlib`; no `os.sep` assumptions, no drive letters, no backslash literals.
- Config and secrets via environment variables (`ANTHROPIC_API_KEY`, `ROUGHCUT_*`); never
  read from a hardcoded location.
- ffmpeg is pinned to a known major version in the image, and the version is recorded in run
  records — ffmpeg behavior differences are a real source of render drift.

**GPU in container** requires `nvidia-container-toolkit` on the host and `--gpus all` at run
time; the image must therefore be CUDA-base for the ASR stage. T0b's DoD covers the CPU path
(tests) and documents the GPU invocation without requiring it in tests.

**Dependencies:** Python 3.12+, uv, ffmpeg/ffprobe (system, pinned), PySceneDetect,
faster-whisper, opentimelineio, anthropic, typer, pytest, ruff, jsonschema.

## 10. Open research questions (RQ registry)

| ID | Question | Blocks | Study |
|---|---|---|---|
| RQ-7 | Analysis policy: per-shot uniform vs adaptive coarse-to-fine vs hybrid; frontier-VLM necessity for the dense pass | T7 design, R1 framing | [R7](../research/R7-analysis-policy.md) |
| RQ-1 | VLM keyframes/shot (K) or sheet density, resolution (R), model tier — within R7's winning policy | T7 | [R1](../research/R1-vlm-sampling.md) |
| RQ-2 | Shot-detection detector + threshold on real footage incl. VFR | T3 DoD target | [R2](../research/R2-shot-detection.md) |
| RQ-3 | Whisper model size vs WER vs runtime on Karl's GPU | T4 DoD target | [R3](../research/R3-asr-sizing.md) |
| RQ-4 | ~~Cheap-gate operating point~~ — **deferred**: gating is off by default at 3–5h scale (§7). Revisit only if 20h bins appear. | — | [R4](../research/R4-gate-recall.md) |
| RQ-5 | Frame-accuracy verification method for FFmpeg renders | T10 DoD | [R5](../research/R5-frame-accuracy.md) |
| RQ-6 | Human eval rubric for rough-cut quality | T13 DoD | [R6](../research/R6-quality-rubric.md) |
