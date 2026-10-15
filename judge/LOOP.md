# LOOP.md: the Roughcut redesign loop (runbook + state; resume here)

Started 2026-10-14 on foxtrot at Karl's request ("I would like the critical 30 minute reviewer set up
for roughcut ... so you can work on it closed loop, like noita"), after he tried `f4b6f41` and said it
does not meet "Premiere Pro quality" and may need a complete redesign. Modelled on the NOITA and
STICKFIGHT loops (`Karl OS/sites/kswans-noita`, `kswans-stickfight/judge/`).

- **Bar:** `REDESIGN-GOAL.md` (12 dimensions, section S standing decisions, section K Karl's notes).
- **Critic:** `judge/JUDGE.md`, a fresh agent each time, never told the builder's results.
- **Closes:** two consecutive PASS verdicts on the same branch. Then the result goes to Karl; merging to
  main and restarting his board are his call.

## Where things live

| What | Where |
|---|---|
| The branch everyone builds on | `redesign/2026-10-14`, worktree `~/Documents/Projects/roughcut-wt/redesign` |
| Karl's live board (do not touch) | `roughcut-board.service`, port 8765, serves `~/Documents/Projects/roughcut` main |
| Gate | `python3 tools/gate.py <rev> [--quick]` → `~/roughcut-loop/gate-<rev>-<stamp>/summary.md` |
| Judge scratch + verdict copies | `~/roughcut-loop/judge<n>/` (server on 8781, `--work` inside it) |
| Builder's own server, if needed | port 8780, `--work ~/roughcut-loop/lead-work` |
| Verdicts | `judge/verdict-<n>.md` (the lead commits them) |

## Rules

- Never merge to main, push main, or restart `roughcut-board`: those are Karl's. Pushing the
  `redesign/2026-10-14` branch to GitHub as a backup is fine (`git push git@github.com:swansk/roughcut.git
  redesign/2026-10-14`, SSH works from foxtrot).
- Every commit: conventional prefix, a CHANGELOG `[Unreleased]` entry (the hook enforces it; never
  `--no-verify`), **no Co-Authored-By line** (Karl's rule for this repo).
- Never write `~/footage`, `~/work`, or Karl's projects. Tests use the suite's synthetic fixture.
- Lanes own disjoint files. Never `git checkout/restore/stash/reset --hard` in a shared tree; stage
  explicit paths. Lanes work in their own worktrees and the lead merges.
- Gate a frozen rev before launching a judge. Do not launch a judge on an unchanged rev.
- Section K is Karl's. The builder may mark a K line resolved only with evidence, and only the judge's
  verdict makes it count.
- Budget: the box is shared with NOITA and STICKFIGHT loops; one judge at a time; heavy runs detached.

## Cadence (the 30-minute tick)

A cron tick every ~30 minutes runs this. A tick is an evaluation window, NOT a mandate:
1. A judge or a lane still running → do nothing; say so in one line.
2. HEAD == the last judged rev → do nothing (unless a wave just merged; then go to 3).
3. Otherwise: `python3 tools/gate.py HEAD --quick`; if green and no lane is in flight, launch the next
   judge (an Agent with `judge/JUDGE.md` as its brief and its verdict number), in the background.
4. When a verdict lands: commit it, read its punch list, plan the next wave (lanes by file ownership,
   top items first), launch the lanes, merge as they return, full gate, then the next tick judges it.
5. Stop when two consecutive verdicts PASS, or when Karl says stop. Update the State section every wave.

## State (newest first)

- 2026-10-15 00:00: **VERDICT 1 = FAIL** (baseline, rev `a65528b`). Gate green (37 budget cases flaky only because
  Chromium rev 1248 was missing); no journey.py. Scores: 1:2 2:2 3:3 4:2 5:4 6:2 7:4 8:4 9:2 10:4 11:4 12:1. Blockers:
  journey missing, "spent $0.27" on claude_cli (`open.js:494`), K1/K2 open, Source monitor / audio tracks / markers /
  insert-overwrite / meters / timecode / export dialog / Inspector / re-run control not implemented, no picture until
  proxies (~8 min/clip under load 15). Wave 1 lanes (disjoint files): MONEY+GATE (punch 1 + gate installs its browser),
  WORKSPACE (punch 3 + the monitor's error path from 5), MEDIA (punch 5 server side), KEYS (punch 7 + markers +
  HH:MM:SS:FF ruler from 6), FIRSTPASS (punch 4 server side + flow.py honest progress + punch 10's notes check).
  Wave 2: multi-track A/V (6), first-pass panel UI (4), Inspector (8), export dialog (9), journey.py (2).
- 2026-10-14 22:30: loop created. Branch `redesign/2026-10-14` cut from `closed-loop/2026-10-13`
  (`f4b6f41`, whose own self-score was 9/8/8/7/8/8/8/7 against the old 8-dimension goal). Karl's K1
  (re-run the first pass) and K2 (Premiere quality, maybe a full redesign) recorded. Verdict 1 next:
  the baseline, expected FAIL with low B and C scores.
