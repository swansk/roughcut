# Roughcut — Agent Operating Manual

You are building Roughcut per [docs/SPEC.md](docs/SPEC.md). **Current state and next actions
live in [docs/HANDOFF.md](docs/HANDOFF.md) — start there.** This file is the contract for *how*
sessions run; HANDOFF says *what* to run. Sessions may be long (multi-hour); the loop below is
designed to repeat continuously without user prompting.

## ⚠️ Current mode: prototype-first — read docs/HANDOFF.md before anything else

The project pivoted at the end of session 1. **[docs/HANDOFF.md](docs/HANDOFF.md) is the source
of truth for current state and next actions**; docs/TASKS.md describes the pre-pivot pipeline
plan and is on hold.

The present goal is to build **one real video from Copper with throwaway tooling** (scripts +
ffmpeg — no OTIO, no SQLite, no T0–T13 scaffold), judge it, and only then decide whether the
architected pipeline is worth building. Karl's framing: *"Can Claude compile a compelling video
with good cuts from footage?"* — answered by making a video and watching it, not by measuring
agreement with human highlight labels.

While in prototype mode, the DoD discipline below still applies to anything verifiable
(orientation, sync, loudness, render integrity), but there is no task board to work through —
follow HANDOFF's next actions.

## The session loop

1. **Orient.** Read docs/HANDOFF.md for current state and next actions. (Once the prototype has
   earned the pipeline, this reverts to: read docs/TASKS.md, pick the highest-value item whose
   status is `ready`, set it `in-progress` — committed together with the work, never as a
   status-only commit.)
2. **Announce.** One sentence: which task, and restate its DoD checklist.
3. **Closed loop.** Implement → **run every DoD verification command** → read the output →
   fix → re-run. Repeat until all items pass. Rules:
   - A DoD item passes only on **fresh command output in this session**. Never mark an item
     on inspection, memory, or "it should work".
   - If a DoD item is untestable as written, fix the DoD (edit TASKS.md, note why in the
     commit) rather than skipping it. Weakening a threshold requires a `DECISION` flag for
     Karl, not a silent edit.
   - New scope discovered mid-task: if it's needed for this DoD, do it; otherwise add it to
     TASKS.md or docs/FUTURE_PHASES.md and move on. Never expand a task silently.
4. **Close out.** In one commit: the feature/study, its tests, TASKS.md status → `done`
   (or `done*` if a provisional [R#] threshold was used), CHANGELOG entry. Paste the key
   verification output (test summary, metric numbers) into the commit body.
5. **Continue.** Return to step 1. Do not stop after one task. Stop only when: (a) you have
   reached a **checkpoint** (see below), (b) every `ready` item is exhausted (all remaining are
   `blocked` or `DECISION`), (c) you are blocked on input only Karl can give (footage, labels,
   API key, a DECISION item), or (d) budget guardrails would be exceeded. When stopping, end
   with a status summary: what closed, what verification proved it, what's blocked and on what.

## Commit discipline

- **One feature/change per commit.** Never bundle unrelated tasks. Spec/doc edits ride with
  the code that motivated them, or stand alone if independent.
- Imperative subject with a conventional prefix (`feat:`, `fix:`, `docs:`, `test:`,
  `chore:`, `research:`); body references the task/study ID and includes verification output.
- CHANGELOG.md gets an `[Unreleased]` entry in every commit that changes behavior or docs.
- Never commit media files, `.env`, or anything in .gitignore. Never use `--no-verify`.

## Research-before-assumption

- Any parameter in the SPEC's RQ registry (§10) **must not be hardcoded from guesswork**.
  If a task needs an unresolved RQ, either run the study first or use the provisional value
  and mark the task `done*`.
- Closing a study means: report written, decision recorded in SPEC's RQ row, provisional
  values in TASKS.md replaced with measured ones.
- If reality contradicts the SPEC (a library can't do X, a cost estimate is off 5×), update
  the SPEC in the same commit as the discovery — the SPEC must never be knowingly stale.

## Inference & cost guardrails

- **All inference goes through `roughcut.inference`** (SPEC §6). No pipeline code imports
  `anthropic` or shells out to `claude` directly — T0c's grep test enforces this, and defeating
  it is never the right fix.
- **Development default is the Claude Code CLI backend** (`ROUGHCUT_BACKEND=claude_cli`), which
  bills against Karl's Max subscription rather than per token. Treat the scarce resource as
  **requests per rolling window**, not dollars: prefer `complete_many` and coarse-grained calls
  over per-item loops, and respect the per-run call ceiling rather than raising it.
- **Every call still logs tokens and `projected_usd`** — the API-rate equivalent. Budget caps
  are enforced on `projected_usd` on both backends, so subscription development never loses the
  ability to answer "is this affordable in production".
- Tests use mocked backends by default; live smoke tests are ≤ 3 calls and explicitly marked.
- Anything that would run hundreds of live calls in a single session: stop and confirm with
  Karl first, regardless of backend.
- Per-unit scoring calls use low effort; the S3 skeleton agent uses the top-tier role. Model IDs
  come from `config.py` roles — never literals.

## Checkpoints

Four review gates are defined in docs/TASKS.md § Review checkpoints (CP1 after T3, CP2 after
R7, CP3 after T9, CP4 after T11). At a checkpoint:

1. Stop. Do not start the next phase, even if the next task is `ready`.
2. Write `docs/notes/CP<n>-<YYYY-MM-DD>.md`: what to look at (specific files, timestamps,
   outputs), what you concluded, what decision you need, and what you'd do next under each
   plausible answer.
3. Report the same summary in chat and end the session.

**CP2 is a go/no-go, not a formality.** If R7 shows the analysis can't reliably surface the
moments Karl would pick, say so plainly and recommend stopping or rethinking rather than
proceeding to build T8–T13 on a failed premise.

## Environment notes

- **Linux-first** (WSL2 during prototyping, Linux box in production). Never add Windows-specific
  code paths, drive letters, or backslash literals — see SPEC §9 for the portability rules that
  T0b enforces.
- Python via `uv`. ffmpeg/ffprobe must be on PATH — if missing, that's a blocker to surface,
  not to work around.
- Long-running local jobs (transcription, batch renders) run in the background; keep working
  on other `ready` items while they run, then verify.
- Benchmark footage paths come from `benchmarks/bins/*.json` — never hardcode absolute media
  paths in code or tests.
- GPU available (RTX 5080, 16GB) for ASR and any local-model experiments.

## Model-version policy

Model IDs are configuration, never architecture. Use the role names from `config.py`
(`ROLE_SKELETON`, `ROLE_ANALYSIS`, `ROLE_JUDGE`); no model ID literal may appear outside that
file, and no document should present a specific model version as load-bearing. Model families
change faster than this project ships — anything that pins one is a maintenance bomb. The same
applies to attribution: don't record which model authored a change, in commits, docs, or notes.

## What NOT to do

- Don't build Phase 2/3 features (music, color, captions, revision loop, UI) — note ideas in
  docs/FUTURE_PHASES.md instead.
- Don't store edit state anywhere but OTIO files. Don't bypass the index for shot data.
- Don't "improve" the rubric, thresholds, or budget caps without a DECISION flag.
- Don't leave a task `in-progress` at session end without a written handoff note in TASKS.md
  (one line: what's done, what's next, any gotcha).
