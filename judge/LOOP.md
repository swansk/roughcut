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

- 2026-10-15 14:10: PERF merged (`5f5a309`; 4.3 -> 23-28 fps, cut hole 817 -> 17-50 ms under load; 59.94 proxies at
  29.97, `PROXY_V` v2, old proxies left on disk to delete by hand). Queued for wave 5: timeline.js `onScreen()` /
  `keepInView` read layout per block after style writes (~16 ms per tl.render under load): read once per render or cache the
  width with a ResizeObserver. LAYOUT asked to re-run test_perf_latency with the bigger monitors.
- 2026-10-15 13:30: wave 4 merged so far: RERUN, PICKS2 (+ journey reject step: 15 steps), EXPORTSTOP (+ lead: bed at
  the preset bitrate), POLISH. Still running: LAYOUT, REVIEW, PERF. Queued for wave 5 (from POLISH): drag clips along
  V3+/A3+; the Program monitor plays A3+ sound and shows V3 on its own layer; track add/delete on the undo stack; `,` past
  the end can't leave a gap (playhead clamps); app.js:2222 builds its own poster URL.
- 2026-10-15 11:40: RERUN merged (`3349be3`). Queued for wave 5: the board can save twice before `tl.afterSave` re-keys
  `tmp-N` ids, so the server mints a second set of ids (what judge 4 hit; the server now re-aliases trims, but per-shot
  colour keyed by id can still be orphaned): one save in flight, or `forSave` re-keys from the first reply.
- 2026-10-15 10:30: **VERDICT 4 = FAIL** (rev `c1c5d6a`; all dims 6-8; 2/3/12 at 8; 9 and 11 at 6). Blockers: K2, picks not
  editor objects, the re-run re-adds rejected shots and rounds kept shots off the grid. Rulings: HEADER's exemption removal
  and /jobs 109 honest; /shot should be 189 (measured). **Wave 4 lanes** (from this commit): PICKS2 (punch 1), RERUN (2),
  LAYOUT (3 + Selects columns at 1440), REVIEW (4), PERF (5), EXPORTSTOP (6 + 8: PCM parts / one AAC, first-open size
  sample, one sequence rate for In/Out exports, /api/index/cancel + kill preview/proxy children), POLISH (7 + 9 + 10 +
  versioned poster URLs + the test_budget-after-test_ui_flow order dependence).
- 2026-10-15 08:40: **wave 3 merged**: JOURNEY `f04798a`, ORIENT `c31089f`, PROGRAM `bc13ac4`, RENDER `dfe009c`,
  HEADER `bf1aa31`, PICKS `6e21dd6`, TLAUDIO `885d39f` (+ lead `1c7af03`: one level owner, 4-place saves), PASS `2f89ad2`
  (+ lead: fixes round away from what they fix). Added to the wave-4 queue: `/api/index/cancel` so the background index
  (proxies, close looks) can be stopped from its job pill after the pass has moved on; the PICKS-noted test-order
  dependence (test_budget fails after test_ui_flow in one process); Project panel too narrow at 1440 for Selects' Media
  In/Out and Reason columns; a full 12-clip Killington first pass has never been timed (PASS estimates 12-15 min at load
  ~25). Full gate next, then judge 4.
- 2026-10-15 06:10: wave 3 merges so far: ORIENT `c31089f`, PROGRAM `bc13ac4`, RENDER (this). Queued for wave 4 (lead's
  notes): audio is AAC-encoded twice since RENDER's join (per part, then at the join): keep part audio as PCM and encode
  once; app.js `isThisCut` should compare `controls` like the server's `_same_shots`; the size estimate is ~4x high until
  a bin has one export of the preset (encode a short sample on first open); poster URLs need a version param so a
  browser holding a pre-ORIENT upside-down poster drops it (`immutable`, 1-year max-age); V2 shows ungraded; overlapping
  moved A1 sounds: only the latest plays; journey prints "!! review copy failed" (probably the journey stopping the
  server mid-copy; confirm).
- 2026-10-15 04:20: **VERDICT 3 = FAIL** (rev `28eca35`; 1:7 2:6 3:7 4:7 5:7 6:5 7:5 8:7 9:6 10:6 11:3 12:2 — 12 was
  judged before JOURNEY merged). K1 resolved on evidence (Karl to confirm), K2 open. Rulings: FINISH letterbox floor
  honest; its header-ellipsis exemption LENIENT (#flowNext reads "Next Make t…"); +3 words, measured budgets, EFFECTS
  budget honest. **Wave 3 lanes** from the scaffold: ORIENT (punch 1), RENDER (3 + CFR export at the sequence rate +
  Match Sequence preset + size estimate + test fps fix), PROGRAM (4), PASS (5 + grade-only = hand edit + proposal
  ranges on the frame grid + carry `controls` through re-runs), TLAUDIO (6 + filmstrip tail + trims on the frame grid),
  PICKS (7), HEADER (8 + 9 + remove the ellipsis exemption).
- 2026-10-15 03:50: gate GREEN on `28eca35` (after the monitor-wait fix: two owners of #monWait, now reasons);
  judge 3 launched on it. JOURNEY merged (`tools/journey.py`, 13 steps, green, 12 mutation checks each red at the
  right step; a gate check in quick + full). Journey findings for wave 3: exports are VFR (r_frame_rate 120/1) because
  edit points are off the frame grid (round proposal ranges + trims/snaps to 1/fps; render CFR at the sequence rate;
  test_export_dialog's "one frame" should be the sequence fps); a grade-only shot is not a hand edit for a re-run;
  trimmed block's thumbnail strip has a dark tail; Status flickered indexed->heard during a re-run.
- 2026-10-15 05:30: **wave 2 merged** (`9309f9b`): FINISH `93c5c5e`, EXPORT `70ea941`, FPUI `10758af`, TRACKS `f187c33`,
  EFFECTS `d0edabe`, INGEST `9309f9b`. Board budgets now = measured counts, no headroom (rubric log). Open for wave 3:
  journey.py (punch 2, blocker); the render honours segments[i].controls (opacity, motion, volume_db, contrast,
  saturation, fx_off/fx_order; EFFECTS request 1); the Program monitor shows V2 and plays unlinked A1 offsets;
  sourceShot's why fallback '' for an unheard clip; segment-rebuilding paths (edits ops, first-pass rerun) carry
  `controls`; meters + picks as editor objects (verdict-2 punch 10); test_budget errors at setup when it runs after
  test_ui_flow in one process (pre-existing; alphabetical full-suite order hides it); MEDIA server ignores SIGTERM while
  previews build and leaves ffmpeg children writing into --work.
- 2026-10-15 02:20: **VERDICT 2 = FAIL** (rev `548e3b4`; 1:5 2:2 3:5 4:6 5:5 6:6 7:3 8:4 9:4 10:5 11:3 12:2; rulings:
  budgets raised = honest, Program >=425 px dropped = LENIENT (replace with >=380 px wide, <=30% letterbox), words 94->105 =
  honest). Scaffold `f371199` (/modules/<name>, firstpass/export stubs + mount points). **Wave 2 lanes** (from `f371199`):
  FPUI (punch 1 + 6), TRACKS (3 + markers/in/out/fps server), EFFECTS (4), EXPORT (5), INGEST (7 + 8), FINISH (9).
  Test hooks fixed for the wave-3 journey: firstpass-button/-panel/-run; export-dialog/-preset/-name/-go/-progress/-done(data-path)/-cancel.
  Wave 3: journey.py (2), meters + picks as editor objects (10), render honours opacity/motion/volume, whatever verdict 3 says.
- 2026-10-15 01:20: **wave 1 merged** (`c1067c0` + the bin-menu wording fix): FIRSTPASS `b78dc40`, MONEY `02c2b57`,
  MEDIA `b2d3014`, KEYS `1bff11b`, WORKSPACE `c1067c0`. Board is now Project / Source / Program / Timeline / Effect
  Controls with a menu bar; Premiere keys, markers, HH:MM:SS:FF; posters + playable media before proxies; /api/firstpass
  (no UI yet); honest progress; truthful notes; no "spent" on the plan; gate installs Chromium. Open requests for wave 2:
  server accepts markers/in/out + sends fps (and fps/width/height on /api/clips); /workspace.css static route; any clip
  insertable before its audio sidecar; journal.py % on /open still inflated; first-pass header control + panel; track
  headers in .tl-side + multi-track A/V; Inspector; export dialog; journey.py. Full gate next, then judge 2.
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
