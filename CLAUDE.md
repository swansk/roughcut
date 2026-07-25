# Roughcut — Agent Operating Manual

You are building Roughcut per [docs/SPEC.md](docs/SPEC.md). Work is defined in
[docs/TASKS.md](docs/TASKS.md) (build tasks) and [research/](research/README.md) (studies).
This file is the contract for how sessions run. Sessions may be long (multi-hour); the loop
below is designed to be repeated continuously without user prompting.

## The session loop

1. **Orient.** Read docs/TASKS.md. Pick the highest-value item whose status is `ready`
   (dependencies done). Research studies count as tasks; prefer a study when it blocks the
   next build task's real DoD. Set the item `in-progress` (commit this only together with the
   work — no status-only commits).
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
5. **Continue.** Return to step 1. Do not stop after one task. Stop only when: (a) every
   `ready` item is exhausted (all remaining are `blocked` or `DECISION`), (b) you are blocked
   on input only Karl can give (footage, labels, API key, a DECISION item), or (c) budget
   guardrails would be exceeded. When stopping, end with a status summary: what closed, what
   verification proved it, what's blocked and on what.

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

## Cost guardrails

- All Claude API calls go through `roughcut.costs` (ledger + cap check). No direct
  `client.messages.create` outside that wrapper in pipeline code.
- Development/testing calls: prefer mocked responses; real-API smoke tests use ≤ 10 shots.
  R1 is the only pre-approved spend > $5 (target < $20). Anything projected beyond that:
  stop and ask Karl.
- VLM passes use the Batch API; per-shot calls use `effort: low`; the S3 skeleton agent uses
  `claude-opus-5`.

## Environment notes

- Windows 11, PowerShell + Git Bash available. Python via `uv`. ffmpeg/ffprobe must be on
  PATH — if missing, that's a blocker to surface, not to work around.
- Long-running local jobs (transcription, batch renders) run in the background; keep working
  on other `ready` items while they run, then verify.
- Benchmark footage paths come from `benchmarks/bins/*.json` — never hardcode absolute media
  paths in code or tests.

## What NOT to do

- Don't build Phase 2/3 features (music, color, captions, revision loop, UI) — note ideas in
  docs/FUTURE_PHASES.md instead.
- Don't store edit state anywhere but OTIO files. Don't bypass the index for shot data.
- Don't "improve" the rubric, thresholds, or budget caps without a DECISION flag.
- Don't leave a task `in-progress` at session end without a written handoff note in TASKS.md
  (one line: what's done, what's next, any gotcha).
