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

- 2026-10-18 ~14:10 EDT: **wave 12 (K3) launched** from the scaffold `ee8cea1` (+ contracts `909a5d4`; judge/WAVE12.md is
  the lanes' file table and contracts): IMPORT (13: multi-source bin, Media Browser, upload + OS drop, Ctrl+I), AIOPS
  (15: roughcut/actions.py vocabulary + assist.py + /api/assist + actions.js dispatcher and per-op review; Designed
  Effects edits made undoable), AIPROMPT (14: the always-visible prompt, scope chip, AI menu commands, "Ask AI about
  this…"), TEACH (16 learn: first-run walk-through, AI hover cards, empty states, the tests' tour switch), BINLOOK (16 look:
  Premiere Project panel List/Icon views, sequences + "First pass (proposed)" in the bin, picks as subclips). Worktrees
  roughcut-wt/w12-<lane>. `~/roughcut-loop/QUIET` holds lanes to single nice-19 test files until W11FINISH's A/B is done.
  Merge order plan: W11 BLACK + CANVAS (trial branch) -> AIOPS -> AIPROMPT -> IMPORT -> BINLOOK -> TEACH (tour anchors last),
  re-measuring test_budget at each merge; then a JOURNEY3 lane adds K3 steps (import, ask, review per op) to the journey.
- **2026-10-18 13:40 EDT: RESUMED on Karl's "Resume roughcut project loop".** Box quiet (load 0.5, NOITA/STICKFIGHT
  idle). K3 turned into rubric section E, dimensions 13-16 (`cc53ad4`; JUDGE.md drives them; PASS now needs all 16 >= 8).
  Running: lane W11FINISH (quiet-box A/B of BLACK and CANVAS vs base, merges base into CANVAS, a trial merge of both in
  `roughcut-wt/w11-trial` with test_tx_playback + test_preview_building + test_perf_latency) and a read-only survey of the
  import / AI-entry / edit-op surfaces for the K3 wave. Next: merge BLACK + CANVAS -> launch wave 12 (K3: IMPORT, AIPROMPT,
  AIOPS, TEACH, BINLOOK) -> full gate -> judge 9. Tick cron recreated (:13/:43, session-only, 7-day expiry).
- **2026-10-16 ~00:45 EDT: PAUSED by Karl.** The 30-min tick cron is deleted. Branch `redesign/2026-10-14` holds verdict 8 +
  wave 11's SMALL2, LUMETRI, FXBIN and CHEAP (merged at pause as `9be0fd2`: status endpoints p95 <= 10 ms under an export,
  peaks 202-pending then a nice-19 background build; test_status_cheap 4 green; journey 20 green on the merged HEAD).
  Unmerged, both stopped cleanly with everything committed: `redesign-w11-black` (punch 1, silent black Program) and
  `redesign-w11-canvas` (punch 2 + 3, graded canvas + dissolve holes). See the lane notes below for
  their state at pause. **To resume:** (1) read this State and verdict-8.md; (2) for each of the two w11 lane branches: if its head is
  green, merge it (CHANGELOG union; re-measure test_budget if it changed counts), else finish it in its worktree
  (roughcut-wt/w11-<lane>) or relaunch a lane with its remaining steps; (3) re-run `test_perf_latency -k grade_draws` x10
  (9/10 before BLACK); test_server's 6 analysis tests time out at load >= 15 on base too (nice-19 proxies): watch; (4) full gate `python3 tools/gate.py HEAD`; (5) judge 9 via judge/JUDGE.md; (6) recreate the tick
  cron from "Cadence" if running unattended. K2 stays Karl's: he can try the branch (stop roughcut-board, run
  `uv run app/server.py --footage ~/footage/killington-neutral` in this worktree, restart the service after).
  - Lane BLACK at pause: `redesign-w11-black` @ `4a3d9c3` (base merged, clean, journey 20 green). /media/proxy waits <= 0.3 s
    then 503 + retry-after/x-roughcut-eta; poster + "preparing preview… CLIP · ~N s" within 300 ms, Play plays past on the
    film clock; queue = waited-on clip, cut previews in film order, then cut ranges, then the rest (last cut clip job 3/9,
    was 6/9); bin Status "preview building". test_preview_building 5 green. OPEN before merging: test_latency_under_load
    failed 3x on HEAD (83.4 ms mid-shot, load 14.6-18.6; once 133 ms at a cut) while the base passed once at 17.7: run HEAD
    vs base ~5x each on a quieter box (suspects: noteKind's status-JSON parse, the 300 ms frameWatch timer); re-run
    test_media_first x3 (one 30 s walk miss at load 25); Killington check not done.
  - Lane CANVAS at pause: `redesign-w11-canvas` @ `24408ca` (LUMETRI + FXBIN merged in, clean; journey NOT run). Canvas
    check in the perf record + gate line (fails on base at load >= 15). Fixes: worker copies YUV planes + shader converts
    (worker JS 21 ms -> 0.4-4.6 ms/frame), warm-up draw, Auto snaps/learns 640->544->448, paintPos once per sequence frame,
    incoming dissolve buffer handed (one decoder less), next park deferred past the dissolve. Killington 1440 at load ~15:
    base 70-165 ms / 21-29 fps, fix 62-107 ms / 25-30 fps. NOT shown: a pass <= 67 ms (load never < 13; NOITA tourney +
    lanes). Next: quiet-box A/B base vs 24408ca (scripts in ~/roughcut-loop/lane-canvas: kab.sh, kpb.py, dec.py) incl. 1920
    + 5 dissolve plays; journey; if holes remain, frame delivery off the main thread (MediaStreamTrackProcessor in the
    worker). Queued: linear-light setMix for Film Dissolve; a 21-shot perf fixture (the 4-shot one passes base at load 12).
    Merge order hint: BLACK and CANVAS both touch app.js's player (posterUp/showLive vs followers/txHand): merge one,
    then rebase/merge the other and re-run test_tx_playback + test_preview_building together.
- 2026-10-16 00:35 EDT: FXBIN merged `e6fe1bc` (Effects bin, seven kinds with exact maths, drops on edits + clip ends,
  defaults, repeated frames hatched). Budget re-measured with both tabs (97·53 etc.), logged. Queued from FXBIN: Film
  Dissolve's two-shot mix previews in gamma space (needs a linear-light setMix mode, CANVAS asked); transition_handles
  widens range proxies for dips (no handle needed). Running: BLACK, CHEAP, CANVAS.
- 2026-10-15 23:35 EDT: SMALL2 merged `724e9a2`, LUMETRI merged `d6ae8e2` (Curves, Wheels, HSL, Scopes; budget +2 words +1
  control logged). Punch 9: floor flake fix 10/10 at load 20-28; grade-worker test 9/10 (one 15 s timeout waiting for a
  first frame at load 28: likely blocker 2's 20 s /media/proxy hold; re-run after BLACK). test_timeline_keys failed once in
  SMALL2's run (unnamed), passed 3 reruns: watch. Running: BLACK, CHEAP, CANVAS, FXBIN.
- 2026-10-15 22:40 EDT: VERDICT 8 FAIL on `1e99342` (1:8 2:9 3:9 4:9 5:9 6:7 7:8 8:9 9:8 10:7 11:9 12:9); judge's gate GREEN.
  Blockers: K2; the Program silently black >= 11.5 s on shots whose preview is building (/media/proxy holds 20 s before
  its 503); dims 6 and 10 at 7. Wave 11 lanes: BLACK (1: never black without a word, accepted cut previews first),
  CHEAP (10: status endpoints + peaks from cached snapshots), CANVAS (2 + 3: measure + fix graded-canvas holes, dissolve
  followers), LUMETRI (4: curves, wheels, HSL, scopes), FXBIN (5: Effects bin, dips, crossfade kinds, repeated-frame
  transitions), SMALL2 (6 labels, 7 proxies toast once per pass, 8 export Esc + Ctrl+M). Lead: punch 9 flakes (fixed in
  `8dbfb7f`, needs 20/20).
- 2026-10-15 21:05 EDT: PERFGATE merged: perf runs at any load with 0 burners above 2x cores, before + after the suite,
  the better-conditioned run counts; pass = green (load named), fail above 2x = AMBER, fail at <= 2x = RED; rubric log
  entries for JOURNEY_SLOWEST_S and the 4K fixture. Lane gate on 3648975 GREEN. Wave 10 complete -> lead full gate -> judge 8.
  Watch: test_floor_ui::test_a_clicked_verdict_drops_a_pending_batch_question flaked once in PERFGATE's forced run.
- 2026-10-15 20:10 EDT: TXHITCH merged `d3f0881`. Cause of the dissolve hitch: followers driven by V2's progFollow seeked
  every 110-150 ms (8-11 seeks/play). Now rate-corrected followers, LUT prefetch, TX_PRE 0.25 s, transition_handles widen
  range proxies. Killington worst 83 ms (was 275) at load 12-15. Queued: the graded canvas holds 89-228 ms at plain cuts on
  Killington (the perf test never measures the canvas); defer the next shot's park until a dissolve ends; hand the preroll
  buffer to the incoming follower (one decoder less); a cheaper second upload in the grade worker.
- 2026-10-15 20:45 EDT: CRAFT merged (stopwatch-off confirm dialog; Source transport 4 px gaps + container query; ghost
  labels name / number / nothing; proxies toast once per pass; whole-clip drop on the clip's own grid). Queued: proxy count
  has no job-pill home (S.proxies fetched, never shown); trims + Source In/Out on a mixed-rate sequence snap only to the
  sequence grid (gridRange). Waiting on PERFGATE, TXHITCH.
- 2026-10-15 19:40 EDT: BOOT merged `1b307a9` (cut on screen 0.8 s after a proxy lands, was 28.9 s; colour measured in the
  proxy job + a niced background queue; gate line "board shows its cut"). EXPORTOK merged `e0f05ad` (held keys fold to
  static, no empty sendcmd; failed export names the shot + Go to shot / Show log; review copy in a procscope; journey 20
  steps with an opacity-key step). Queued: a V1 clip with no audio stream fails its part (Premiere exports silence);
  boot still awaits /api/colour, which ffprobes every clip on a cold server; niced colour queue can take minutes at load 15.
- 2026-10-15 19:05 EDT: VERDICT 7 FAIL on `273b1f1` (1:8 2:9 3:9 4:9 5:9 6:8 7:8 8:5 9:8 10:5 11:9 12:9). Blockers: K2; one Opacity
  key breaks every export (empty sendcmd); the board blank ~30 s after a proxy lands (boot awaits /api/junk, which measures
  colour inside the GET); gate AMBER (perf skipped at load 14). Wave 10 lanes (punch 1-4, 7-10):
  EXPORTOK (1 + 10: assemble.py keyed graphs, export failure names the shot, review-copy spawn), BOOT (2: mount first,
  no measuring in GET), PERFGATE (3: 0 burners above 2x cores, perf before + after the suite, rubric log incl.
  JOURNEY_SLOWEST_S + 4K fixture), TXHITCH (4: dissolve preroll, RANGE_PAD_S grows), CRAFT (7 stopwatch-off confirm,
  8 proposal chrome, 9 clip-grid drop). Wave 11: LUMETRI (5: curves, wheels, scopes), FXBIN (6: Effects bin + dips).
- 2026-10-15 18:25 EDT: lead gate on `64687de` RED only on 2 broken tests (keys sheet overflowed 97 px after the transitions
  row; journey-tool test predated SAVESAFE's suffix), both fixed in `273b1f1` (pushed). Perf ran at load 8.4: worst gap
  50 ms, seek median 56 ms (first in-limit measurement at a sane load). Journey 19 green, screens + hygiene green.
  Judge 7 running on `273b1f1`.
- 2026-10-15 17:32 EDT (the box's clock; the "10-16" stamps below were mis-dated by the lead): YIELD, TRACKS, V2EC merged (`060bc3a`);
  lead `da990ab`: Effect Controls refuse edits on a locked track (V2EC's report), identity render test pins the CPU decoder.
  TRANSITIONS merged `95d77de` (Ctrl+D dissolve, Shift+D crossfade; graded mix in the grade worker; export blends).
  Queued from it: RANGE_PAD_S (1.0 s) should grow with a transition's half (> ~1.3 s dissolves outrun the range proxy);
  the neighbour's handle lacks its own Motion/Opacity/fx in preview + export; #gradeCanvas wears the live shot's Motion
  during a mix. Then: quiet box -> full gate -> judge 7.
- 2026-10-16 08:10: RENDER3 merged: 4-min Killington Quick export 1420.7 s -> 267.7 s (1.12x real time at load ~18; GPU decode
  was the big win). Queued: a cheaper `lut_vf` in roughcut/colour.py (40 % of the 4K Match size sample's 30 s); Effect
  Controls can't target a V2+ clip yet (data + render ready: overlays[i].controls); re-measure RENDER_S_PER_CUT_S["preview"].
  Still running: YIELD. Then: TRANSITIONS (verdict-6 punch 6), quiet box -> full gate -> judge 7.
- 2026-10-16 07:30: RANGEPROXY `0daab17`, PLAY3 `6139b41` merged. Playback on the 4K fixture: cut holes <= 67 ms in 12/12
  (rolled + parked every cut), fps 25-30, seeks median 64-120; residual mid-shot 83-100 ms holes at load 15-21 (two
  decoders during a 0.4 s shot). No perf run has yet measured at load <= 2x cores: the box has been at 15-50 from parallel
  lanes all day. YIELD (SIGSTOP background encodes while playing) running; RENDER3 running. Then: quiet box -> gate -> judge 7.
- 2026-10-16 05:00: SMALL `d4015ff`, REVIEW3 `76d8242`, SAVESAFE merged. SAVESAFE's root cause of the lost cut: Chrome's 6 connections
  per host all held by /media/poster builds after a pass; the Accept's PUT queued 11.5 s and the reload cancelled it (fix:
  saves go credentials:'omit', a separate connection group). Queued (wave 9): posters still starve every credentialed GET
  (polls like /api/firstpass): limit concurrent filmstrip/poster loads on the client or don't hold a connection while a poster
  builds. Lane hygiene: lanes must use per-lane scratch dirs (one lane's $SCRATCH/snap was overwritten by another's).
- 2026-10-16 02:30: **VERDICT 6 = FAIL** (rev `ee3f15e`: all 8-9 except 6:7, 10:6; K2 read "no longer a toy, wouldn't choose over
  Premiere yet"). Already fixed since: punch 5 (KEYANCHOR), LUT caching (LUTCACHE). **Wave 8** from this commit: SAVESAFE (1),
  PLAY3 (2), REVIEW3 (4), RENDER3 (7 + 8), SMALL (9 + 10). Held: YIELD (3) until RANGEPROXY merges (same server code);
  TRANSITIONS (6) next wave (it shares the player with PLAY3).
- 2026-10-16 01:30: KEYANCHOR `524a4a5` (+ lead `c7a83b7` trim keys via setRange), LUTCACHE `fa7624c` merged (the LUT hypothesis
  REFUTED: upload 0.1 ms; the real cost was the WebGL canvas draw + headless commit read-back, now in a worker; and a wrong
  LUT drawn 70-200 ms after a cut fixed by prefetch) + lead: meters draw their scale once. Perf at load 16-23 (3x cores):
  cut gaps <= 67 ms in 8/8, one mid-shot 83-100 ms gap in 5/8; at the test's design load (~cores) still to re-run. Queued:
  the preroll still misses at load ~20 (`rolled: false`, 130-243 ms held at the second cut): player/decoder contention.
- 2026-10-15 23:40: lead fixes after wave 6: `bc8d5d7` (a video idle after a seek is nudged: /floor's recurring flaky test was a
  real Chromium stall) and Playback Resolution in grade.js (<= 640 px while playing). test_perf_latency at the judge's 67 ms bar
  is still MARGINAL at load 9-10 (5/6): the residual hole sits ~45 ms after a cut. Wave-7 hypothesis: the per-shot LUT's
  texImage3D upload at the cut (the perf fixture gives every shot its own balance): cache LUT textures by record and upload
  the next shot's LUT in slices (texSubImage3D, a few slices a frame) during the preroll.
- 2026-10-15 22:00: **wave 6 merged**: KEYS2 `a07bedd`, KEYUI `47c3aac`, JOURNEY2 `7fa9ff2` (19 steps), PROPOSAL2 `0b71e70`,
  RENDER2 `0e0ecc8`, BINPOLISH `37758e2` (+ lead `1d48347`), PLAYBACK `4af18fb` (+ lead: tone test waits for its redraw).
  Wave-7 queue adds (PLAYBACK): a third video buffer so a short shot followed by a different file never holds a frame
  (grade.js:493, fx.js:1323, effects.js:233 hard-code #pv0/#pv1); range-first proxies (the cut's in/out ranges before whole
  clips: Program off the 360p preview in minutes, not ~37 min on Killington); perf gate marginal between load 10 and 12 (cap
  the grade canvas's per-frame cost or lower the skip threshold). Full gate next, then judge 6.
- 2026-10-15 20:30: JOURNEY2 `7fa9ff2`, PROPOSAL2 `0b71e70`, RENDER2 merged. Wave-7 queue adds: keys on a razor/split must
  shift on BOTH sides (client timeline.js `split()` AND server `roughcut/edits.py` ops) with a key at the cut; V2+ overlays
  honour their own `controls` (alpha-composite over V1, not blend to black); the size sample includes overlays; the 8-bit RGB
  tone step clips a channel before the saturation mix (shader clamps after).
- 2026-10-15 19:00: KEYS2 `a07bedd` and KEYUI merged. Queued for wave 7 (KEYUI's requests, timeline.js): a razor `split()`
  copies `controls.keys` unchanged to the second half; shift them by -(split offset) and add a key at the cut so neither half
  jumps; trimming a shot's in-point shifts its keys (t is from the shot's start): shift by the trim's film-time change so keys
  stay anchored to the media, as Premiere does. Env note (KEYS2): a uv cache env for assemble.py had a broken numpy (concurrent
  installs); a "render failed" export -> check `~/.cache/uv/environments-v2/assemble-*`.
- 2026-10-15 17:20: **VERDICT 5 = FAIL** (rev `867f694`: eight dims at 8, K1 at 9; 6:7 7:7 9:7 10:6). Since: SAVE `83781c6`
  (punch 1's cause), perf test load-aware `41a98c5`. **Wave 6 lanes** (from this commit): PLAYBACK (punch 2 + Program plays
  A3+), PROPOSAL2 (3), KEYUI (4, UI + EDL), RENDER2 (4's render + 6), KEYS2 (5 minus A3 playback), BINPOLISH (7 + 8 + 9
  minus the journey line), JOURNEY2 (10 + punch 1's test_id_race + the journey's "!!" line). Keyframe schema fixed for
  KEYUI and RENDER2: `segments[i].controls.keys = {prop: [[t, v], ...]}` (t = seconds from the shot's start in film time,
  sorted, linear between keys, held outside them; prop in position_x, position_y, scale, rotation, opacity, volume_db);
  Lumetri basic adds `highlights`, `shadows`, `whites`, `blacks` (-100..100, 0 = off) to `controls` beside contrast/saturation.
- 2026-10-15 16:00: the wave-4 gate (`89060ed`) was RED: 4 broken = 3 stale expectations (PCM parts, versioned poster,
  proposal rows vs LAYOUT's bottom-row Assistant) + 1 real lag (live highlight deferred with PERF's repaint; fixed). Trade-off
  for judge 5 to rule on: with Premiere-sized monitors the Assistant (proposal table) sits in the bottom row and shows ~4
  rows before it scrolls; the ghost lane shows every shot.
- 2026-10-15 15:10: **wave 4 merged**: RERUN `3349be3`, PICKS2 `bca9671`+`5bcdf33` (journey 15 steps), EXPORTSTOP `e1a3b2e`
  (+ lead `51cc176` bed bitrate), POLISH `5c322bf`, PERF `5f5a309` (+ lead `e8a1484` poster test), LAYOUT `7907718`,
  REVIEW `ad00a46` (+ lead: fp fixture clears INDEXES). Wave-5 queue adds: the double save (acceptProposal saves, then
  tl.commit's touch() saves again 700 ms later: re-mints tmp ids, races external PUTs); empty sequence + proposal: collapse
  V1 or draw the ghost in V1's row, Program shows the proposal's first frame; pick markers sit over the ghosts' name band;
  timeline.js should export its row weights so the ghost height can't drift; test_perf_latency's 100 ms gap limit is
  marginal on a loaded box with either layout (LAYOUT: 116.7/100.1 ms; base 133.2/100.1) — consider capping the grade
  canvas's drawn size. Full gate next, then judge 5.
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
