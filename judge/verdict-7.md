VERDICT: FAIL

# Roughcut redesign: verdict 7

- **Rev judged:** `273b1f1` (branch `redesign/2026-10-14`: waves 8/9 SAVESAFE, REVIEW3, SMALL, RANGEPROXY, PLAY3, RENDER3, YIELD, V2EC, TRANSITIONS, KEYANCHOR, LUTCACHE + the lead's `da990ab`, `64687de`, `273b1f1`). The gate archived it to `~/roughcut-loop/snap-273b1f1`; `diff -rq` against a fresh `git archive 273b1f1` printed nothing. I served that snapshot on 127.0.0.1:8781 with `--work ~/roughcut-loop/judge7/work` and `--footage ~/footage/killington-neutral` (12 GoPro clips: CLIP_01-03 HEVC 59.94, CLIP_04-12 H.264 29.97 with a −180° matrix). Fresh work dir, cold land.
- **Karl's live board:** `35cb147`, an ancestor of `273b1f1`, **214 commits behind** (48 since verdict 6's `ee3f15e`). Port 8765 untouched.
- **Box load (6 cores; NOITA/STICKFIGHT `tourney.mjs` alone held it at ~8-14, at times 460 % CPU):** 8.3 at the cold land, 12.0-16.2 (mean 14.3) during the first pass, 12.7-16.6 for every editing, playback, transition and export run below. Every timing is under that load.
- **Model spend:** backend `claude_cli` (`charged: false`). One real 12-clip first pass (Start, Stop at 30 s, Re-run) and one re-run after hand edits. Nothing billed.
- **Cleanup:** my server (uv 2297229 / python 2297253) killed; port 8781 free; `pgrep -af roughcut-loop/judge7` empty (an export review-copy ffmpeg outlived the server by a few seconds, then finished on its own: punch 10). The only repo write is this file, uncommitted.

## Gate: my own run, `~/roughcut-loop/gate-273b1f1-20261015T1806/summary.md` — **AMBER**, 2449 s

| check | result |
|---|---|
| browser | ✓ Playwright 1.64.0 / Chromium 156 |
| suite | ◐ **AMBER**: 1259 passed, 0 failed, 2 skipped, 0 flaky, 0 broken. `test_latency_under_load` **skipped in the full run at load 14.43 and again on the retry at 13.58** ("not a measurement of the app"). The box's other loops held it above 2× cores the whole time; my own first pass and export added to it during the retry window. |
| screens | ✓ 7 PNGs, 0 page errors; words·controls / 93·51, /floor 52·7, /open 55·8, / shot 119·66, / film 101·50, / jobs 102·52, / fx 124·54 (each at its budget) |
| journey | ✓ 19 steps, 51 s, "slowest request 0.34 s GET /api/junk", review copy in 4.1 s |
| hygiene | ✓ clean |

**My standalone perf runs (to see what the AMBER hides):** `test_latency_under_load` with `ROUGHCUT_PERF_BURNERS=0` on the frozen snapshot, three runs at load **14.2 / 15.7 / 16.4 (2.4-2.7× cores): 3 of 3 PASSED** — longest frame gap 50.1 ms every time, cut gaps 16.6-33.4 ms, both cuts rolled and parked on their own buffers, seeks 66-155 ms (median ≈ 92), click 22-37 ms, keys 18-54 ms, `effects.set` 20-36 ms (`perfrun0.log`, `perf0-*.json`). The app is now well inside the limits even at 2.5× cores. The gate is still not green, and the cause is structural (punch 3).

## Rubric changes log (rulings on entries since verdict 6)
1. **Lead: Playback Resolution logged after verdict 6's ruling. Honest** (procedural fix). The "2026-10-16" date stamps on this and the next entry are wrong (the box clock read 10-15); LOOP.md admits it.
2. **Wave 8 SMALL: a skipped perf test makes the gate AMBER, retried once after the suite; the Playback Resolution menu; board controls budgets +1. Honest.** The +1s are exactly the menu (measured above). The AMBER rule did its job today. But on this shared box the retry cannot ever measure (other loops sit at 2.3× cores), so the gate can only go green when the neighbours sleep — see punch 3.
3. **Not logged, and should be:** `JOURNEY_SLOWEST_S = 1.0` (a new journey check, SAVESAFE) and the perf test's new 4K 59.94 fixture with 0.5 s proxy keyframes and "parked / own buffer" asserts (PLAY3). Both are stricter, not lenient, so I rule them **honest in substance, procedurally unlogged**. The journey's "slowest request" only counts `/api` paths on a 3-clip fixture, so it cannot see the 27-35 s `/api/junk` stall on real footage (blocker 3).

## Wave 8/9 and lead claims checked (not trusting LOOP.md)
| claim | what I found |
|---|---|
| SAVESAFE: a save never vanishes on reload | **True.** Accept sent one PUT (0.13 s), disk had 18 segments 3 s later, `accepted == proposal minus rejected`. Drop CLIP_05 (landed 77 ms) + `effects.set('temperature', 40)` + reload: `gd2c0be0d80` on disk, `balance.gain [1.06, 1, 0.94]`, EC reads 40.0, no `tmp-`. Delete a shot then reload **60 ms later**: the browser raised `beforeunload` and the delete was on disk after the reload (24 → 23 → 23; `misc.log`). Verdict 6's blocker 2 is fixed. |
| REVIEW3: ≥ 5 rows at 1440, checks whole-word, L/K play it | **Rows true:** the Assistant takes the whole right column while a proposal is pending; **19 of 20** rows fully in view on the first pass and **19 of 19** on the re-run (`31-proposal-1440.png`). Checks are one line each with "+ 3 more", whole words in the title. **L on the empty sequence: not verified by me** (my probe read `player.playing` and the toast said "building proxies…"; the proposal has its own `rcProposalPlayer`, which the gate's `test_a_real_length_first_cut_shows_five_rows_and_plays_on_l` asserts). Cost: the Source monitor shrinks to 343 px and its transport crowds (timecode touches `[+`, Insert touches the duration; `31c-source-transport-crop.png`). |
| SMALL: Playback Resolution menu; pick double-click → Source In/Out | **Menu true** (Auto / Full / 1/2 / 1/4 on the Program). It only governs the graded canvas: on an ungraded shot the canvas is off and the video plays directly. **Double-click true now:** `rcSource.inPoint/outPoint` = 216.1993 / 236.2026 for pick 216.2-236.2 on CLIP_02's 59.94 grid. |
| RANGEPROXY: the cut's shots sharp in minutes | **True.** Pass done 18:30:08; range proxies for every cut clip landed 18:33-18:44 (CLIP_02 3 ranges in 134 s … CLIP_06 by 18:44), i.e. ~14 min at load ~15. Whole proxies keep landing for the next hour ("54 % · ~66 min left"). |
| PLAY3: third buffer, holes at real cuts | **True on real footage** (table below): no hole over 100 ms across any cut, the 0.4 s shot included. |
| YIELD: background encodes pause while the Program plays | **True.** Two niced (NI 19) proxy ffmpegs went `S` → **`T` (stopped) while playing** → `R` after pause (`misc.log`). |
| RENDER3: GPU exports, 3.3× faster; V2+ overlays take their own controls | **Speed true:** full sequence 4:23.8 Quick 1080p in **108 s** at load 13-16 (0.41× real time; verdict 6: 17 min for 3:58). `30000/1001` CFR, 7906 frames, 263.797 s against `tl.total()` 263.8134 (−16 ms, half a frame: the off-grid whole-clip drop, punch 9), one AAC, 223 MB against an estimate of 275 MB (−19 %). **V2 controls true:** a V2 clip at Opacity 50 / Scale 50 composites at half size and half opacity in the Program (`81-v2-op50-sc50.png`) and in the export (`96-export-v2-overlay.png`). **But see blocker 2: one Opacity stopwatch click makes every export fail.** |
| V2EC: Effect Controls for V2+ clips | **True.** A click on the V2 block selects it (`osel`); Effect Controls reads "V2 · CLIP_10 · 1.2 s" with Motion and Opacity and says why Lumetri/Volume/Speed are V1-only (`80-v2-effect-controls.png`); each `effects.set` is one undo. |
| TRANSITIONS: Ctrl+D / Shift+D | **True, driven on a real Killington cut** (`trans.log`): Ctrl+D on the CLIP_02 → CLIP_03 edit made a 1.001 s centred Cross Dissolve in **67 ms**, drawn as Premiere's box on the cut, "undo: Cross Dissolve". Program at the edit: mean abs diff to (A+B)/2 **5.05** against 37.7 (A) and 34.7 (B) (`71-prog-dissolve-mid.png`); 0.25 s earlier it is closer to ¾A+¼B. Shift+D added Constant Power on A1; dragging the box's end 25 px made it 2.54 s, one undo. Both survived a reload. In/Out export of the edit: 2.002 s, 60 frames, frame at the edit 5.55 from (A+B)/2 against 36.5/35.3. **Against:** playing through the dissolve showed one **132 ms** hole (load 12.7). |
| KEYANCHOR: keys follow razor and trims | **True.** Opacity keys [0,100] [2.002,0] on an 11.8 s shot; razor at +1.0 s gave halves [[0,100],[1.001,50]] and [[0,50],[1.001,0]]; the Program read 74.96 / 50 / 50 / 25.0 at +0.5 / +0.999 / +1.0 / +1.5, the same as before the cut; one undo restored it (`misc2.py`). |
| Lead fixes: keys sheet fits; journey test | **True.** The `?` sheet fits at 1440×900 and 1366×768 (`98-keys-*.png`), and lists Ctrl+D / Shift+D. |

## Driving it like an editor
**Cold land (`cold.py`, 1440 and 1920):** rows in 0.20-0.25 s, a picture in 0.21-0.29 s, one blue control ("Run first pass"), exactly two first-pass controls (header + Timeline), the Program says "the sequence is empty · drag a clip to the Timeline or press , (Insert)", 0 page errors, no header overflow (`01-cold-5s-1440.png`, `01-cold-5s-1920.png`).

**First pass (`pass.log`):** panel in 75 ms; Stop at 30 s took **72 ms**, 0 children 1.5 s later; "Re-run first pass" survives a reload; the panel says what a re-run does. The timed run: heard 12/12 at 163 s, looked 12/12 at ~460 s, a **20-shot, 3:01.8 proposal at 524 s (8.7 min, load 12-16)** with 77 picks and 18 markers, none over a ghost's name. It cold-opens on the stream burial, gag-cuts "won't be go-pro-ing" to "Recording started", runs the crew's roll call and the "take two/three" filming thread, and pays the fall off at the end — a usable assistant's assembly with the right checks ("is the buried skier actually in frame"). Re-run after a hand trim + a dropped clip: 57 s, "keeps 2 of your shots exactly", kept both bit-exact, re-added neither rejected range, one PUT on Accept. Two clicks from anywhere.

**Editing (`edit-1440.log`):** +/−/\, →, ↑/↓ (selection follows), J/K/L, I/O (sequence in/out toasted), M, C = razor tool only, V, Ctrl+K split, undo/redo all one entry each. Ripple trim 98.8988 → 94.9949 (total −3.90 s); roll held the total to 0.1 ms; move reordered and survived a reload; Add Video Track / undo; V3 overlay laid from Source I/O + `,` and dragged +28 s; `.` 10 s past the end left a 10.01 s gap; Delete Track "Ctrl+Z brings it back". `,` from Selects inserted picks exactly on the grid. Latency: click → Effect Controls 34-78 ms (4 of 8 not caught by my probe), keys 59-82 ms including the Playwright round trip, `effects.set` 6-22 ms, Ctrl+D 67 ms, Ctrl+M → dialog 118-133 ms.

**Playback across real cuts (`framelog-*.json`, recomputed inside the play window by `gaps.py`):**

| size | load | seek → frame (6) | 3 cuts: gap at the switch / longest | 0.4 s shot (3 runs): longest |
|---|---|---|---|---|
| 1440 | 13.6-14.5 | 163, (miss), 88, 87, 122, 119 | 16.7-33.5 / 33-100 ms | 83.3, 83.4, 66.7 ms |
| 1920 | 14.0-15.3 | 208, 193, 238, 96, 80, 187 | 16.6-33.3 / 50-83 ms | 100, 66.8, 66.7 ms |

No black, no wait text, ~29-30 fps; meters −36 to −8 dBFS. Verdict 6 had 200-600 ms holes here. One seek at 1440 never fired a frame callback within 5 s (unexplained; could be a same-frame seek).

## The 12 dimensions

| # | dimension | score | evidence | vs Premiere Pro |
|---|---|---|---|---|
| 1 | Orientation | **8** | Rows 0.20-0.25 s, picture 0.21-0.29 s, one blue "Run first pass", the Program's empty-sequence hint, 0 errors at both sizes (`01-cold-5s-*.png`). Against: a reload in the hour after a pass can land on an **empty Timeline and Program for ~30 s** (blocker 3, `90-boot-after-proxy-5s.png`). | Premiere's Editing workspace. |
| 2 | First pass visible + re-runnable (K1) | **9** | Header control in every state; Stop 72 ms with 0 children; 8.7 min real pass at load ~14; re-run 57 s says what it keeps and does it exactly; 2 clicks. | n/a; a clear, re-runnable AI step. |
| 3 | Footage in, no ceremony | **9** | Drag to V1 77 ms; `,` from Selects exact; double-click pick now marks In/Out on the clip's grid; Source I/O on the grid; `.` past the end leaves a gap. Against: a whole-clip drop ends half a frame off the grid (CLIP_05 0-35.1851). | Premiere's bin + Source three-point edit. |
| 4 | Workspace | **9** | Program 674×379 (1440) / 841×473 (1920), vertical meters, panels 286/773/365 and 382/1034/488; the Assistant grows to the full right column for a proposal (19/20 rows). Against: Source transport crowds at 343 px in that state. | Premiere's Editing workspace. |
| 5 | Timeline craft | **9** | All of the editing list above, plus transitions on the edit (Ctrl+D/Shift+D, drag an end, one undo) and keys that follow a razor. | Premiere's core trim model, tracks, keys, default transitions. |
| 6 | Monitors and playback | **8** | Real cuts at load 14-15: switches 17-34 ms, worst hole 100 ms, 0.4 s shot ≤ 100 ms; seeks 80-238 ms; perf test 3/3 pass at 2.4-2.7× cores (50 ms gaps). Against: a 132 ms hole through a dissolve; the gate itself could not measure (AMBER); one seek miss. | Close to Premiere's ½-res playback on a busy machine; a hitch at dissolves. |
| 7 | Inspector, effects, colour | **8** | V2+ clips in Effect Controls (Program + export), keyframes anchored through razor, dissolves graded and mixed in the Program, Lumetri Basic. Against: Lumetri stops at Basic (no curves, wheels, HSL, scopes); turning a stopwatch off deletes keys with no warning; a single Opacity key breaks the export (blocker 2). | Premiere's Effect Controls; a fraction of Lumetri. |
| 8 | Export | **5** | When it works it is good: 4:24 in 108 s, CFR, right length, dissolve and V2 overlay rendered, size −19 %. **But one click on Opacity's stopwatch (a single key, `[[0.5005, 100]]`) makes every export fail**, reproduced on two shots at Quick (`swrepro.py`, `93-one-stopwatch-export.png`, two full-sequence failures in `export-quick-all*.log`); the dialog says only "the render failed — see the log" and the log is not reachable from it. | Premiere never refuses to export a keyframed clip. |
| 9 | Looks like a shipped pro tool | **8** | The 1440/1920 screens read as Premiere (`40-1440-06-trims.png`, `40-1920-04-playing-cut.png`, gate `screens/*.png`), the dissolve box is drawn like Premiere's, the keys sheet fits. Against: Source transport crowding in the proposal state, ghost names "C…" at fit zoom, the "building proxies in the background" toast parks over the Timeline repeatedly for an hour, the blank board on reload. | Close. |
| 10 | Responsive, reliable | **5** | Fast and safe on edits (sub-100 ms everywhere, one PUT per Accept, beforeunload + flush hold a delete reloaded at 60 ms, background encodes stop while playing). **Against: the board boots blank for 27-35 s** whenever a whole proxy has just landed (blocker 3; hit twice in my session, ~12 times per pass by design) and **an ordinary keyframe makes Export fail with an error that does not say what to do** (blocker 2). | Premiere never shows an empty project on open. |
| 11 | Proposals are reviewable edits | **9** | Ghosts in V1 with filmstrips, ✓/✗ per shot (Accept 18 of 20 applied exactly), 19/20 rows in view, checks with clickable ranges, rejections remembered, kept shots bit-exact, "free on your plan". Against: L-play on an empty sequence not verified by me (gate only). | Premiere-like review. |
| 12 | The journey | **9** | 19 steps green in the gate (51 s) including drop-grade-reload, upright 59.94, a 0.4 s shot, export + ffprobe. Against: it cannot see either new blocker (no keyed-but-constant opacity step; a 3-clip fixture never stalls `/api/junk`). | n/a |

## Section K
- **K1 "not clear how to re run the first pass": RESOLVED on real footage** (as verdicts 3-6): one header control in every state, 2 clicks, Stop in 72 ms, re-runs that say and do what they keep. Karl to confirm.
- **K2 "not premier pro quality … complete redesign": OPEN.** A harsh Premiere editor would no longer call this a toy: the workspace, keys, three-point editing, trims, tracks, keyframes, default transitions and a 0.4× real-time export are all there, and playback across real 4K GoPro cuts is now smooth at 2.5× cores. Would they choose to cut in it today? Not yet: one ordinary keyframe makes Export fail with no usable message, a reload during the hour after a pass can show an empty project for half a minute, and colour stops at Lumetri Basic. Karl decides K2.

## Section S
- **Inference only through `roughcut.inference`, no model-ID literal outside `config.py`: intact.** `claude-*` literals appear only in `roughcut/config.py` and test fixtures/docstrings; `import anthropic` only at `roughcut/inference.py:592`; gate hygiene clean.
- **No price reads as money spent on `claude_cli`: intact.** "free on your plan" everywhere; `charged: false`; `cli.js:163` says "spent" only when the backend bills.
- **Karl's footage and work dirs never written: intact.** All writes under `~/roughcut-loop/judge7/`; 8765 untouched.
- **INTAKE decisions:** no violation seen.
- **No Co-Authored-By; a CHANGELOG entry on every commit: intact.** `ee3f15e..273b1f1`: 0 Co-Authored-By lines; every non-merge commit touches CHANGELOG.md.

## Blockers
1. **K2 is open.**
2. **One Opacity keyframe breaks every export.** Clicking Opacity's stopwatch once (Premiere's normal "start animating") stores one key; `research/tools/assemble.py:745-758` `opacity_commands` writes no command lines when the value never changes, `controls_graph` (`:790-791`, and the V2+ alpha path `:827`) still adds `sendcmd=f=<empty file>`, ffmpeg rejects the graph ("Error parsing global options: Invalid argument"), and the dialog says "the render failed — see the log" (`app/server.py:5470`). Scale with one key exports fine. Reproduced 3 times on 2 shots (two full-sequence exports, one In/Out range).
3. **A blank board for ~30 s on load after a proxy lands.** `app/static/app.js:4952` `await fetchJunk()` runs before `tl.mount`; `GET /api/junk` (`app/server.py:2299`, `junk_rows(measure=True)` → `junk_assess` → `measure_colour` at `:2239-2241`) synchronously measures a newly landed 1280 proxy. Measured 27.4 s by curl and **31.6 s for a real board load** with an empty Timeline/Program and no wait text (`90-boot-after-proxy-5s.png`, `bootwatch2.log`; `edit.py`'s first attempt timed out at 30 s on the same stall, `/api/debug/slow` shows 35.8 s and 34.7 s).
4. **The gate is AMBER, not green** (the perf test never measured in the gate; my 0-burner runs pass 3/3, so the fix is in the gate, punch 3).

## THE THREE BIGGEST GAPS TO PREMIERE
1. **Export breaks on a normal keyframe.** If you click the Opacity stopwatch on a clip and leave it, every export fails with "the render failed — see the log", and nothing tells you which clip or how to fix it.
2. **Opening the project can show an empty timeline for half a minute.** For about an hour after a first pass, each time a background proxy finishes, the next reload shows no clips and no picture for ~30 s with no message.
3. **The finishing tools are still thin.** Colour is Lumetri's Basic panel only (no curves, colour wheels, HSL or scopes); there is one video and one audio transition and no Effects bin to drag them from; turning a stopwatch off silently deletes your keyframes; playback hitches for ~130 ms through a dissolve.

## PUNCH LIST (most important first; [files] = lane ownership)

1. **A keyed property that never changes value still renders.**
   - **Files:** [`research/tools/assemble.py`] `opacity_commands` / `controls_graph` (V1 path ~:790 and the V2+ alpha path ~:827): when the command file would be empty, emit the static `dim_vf(op)` / alpha multiply (or `null`) instead of `sendcmd`; audit the other keyed graphs (`keyed_motion_graph`, volume keys) for the same one-key / constant case. [`app/server.py` ~:5470, `app/static/export.js`]: a failed export names the failing shot (clip + timecode, parsed from assemble's "cut failed CLIP_02.MP4 5.8058-6.006") with a "Go to shot" button and a "Show log" disclosure.
   - **Done =** `test_render_keyframes.py` gains one key, two equal keys, and keys that hold a constant over the part, for opacity on V1 and on V2+, and each exports with the expected picture; `test_export_dialog.py`: a forced render failure shows the shot's name and a working "Go to shot"; `tools/journey.py` adds a step "click Opacity's stopwatch once, export" (red on today's rev).
2. **The board never waits on a measurement to show the cut.**
   - **Files:** [`app/static/app.js`] boot (~:4952): mount the timeline first, then `fetchJunk()` without awaiting it (repaint the bin when it lands). [`app/server.py`] `api_junk` never measures inside a GET; measure the colour file in the proxy job right after a proxy lands (or a background queue), and serve what is known.
   - **Done =** a test with `measure_colour` patched to sleep 10 s: a fresh page has `tl.state.segs.length > 0` within 2 s; a new gate/journey line "board shows its cut N s after a proxy lands" ≤ 2 s; on Killington, a reload right after a whole proxy lands shows the cut in ≤ 2 s.
3. **The perf gate can turn green on a shared box.**
   - **Files:** [`tools/gate.py`, `app/tests/test_perf_latency.py`]: when the box is already above 2× cores, run the test with 0 burners instead of skipping (a busier box is a harder test); a pass there is green and says the load; a fail at > 2× cores is AMBER-and-named (not red); run the perf test first, before the suite heats the box, as well as at the end. Log it in the rubric log.
   - **Done =** a gate run while `tourney.mjs` holds the box at ~14 reports the perf line as measured (passed at load ~14 with 0 burners) and the run is GREEN; `ROUGHCUT_PERF_FORCE_SKIP=1` still gives AMBER.
4. **No hitch through a dissolve.**
   - **Files:** [`app/static/app.js` (the `prog.tx` in/out buffers), `app/static/grade.js` (mix)]: preroll and park `pvTxIn` at the incoming handle's frame before the transition starts, as PLAY3 does for cuts; grow `RANGE_PAD_S` with the transition's half (the lead's queued item).
   - **Done =** `test_perf_latency` (or `test_transitions`) plays across a 1 s dissolve on the 4K fixture with the longest gap ≤ 67 ms; on Killington my 132 ms hole is ≤ 67 ms.
5. **Lumetri beyond Basic.**
   - **Files:** [`app/static/effects.js`, `app/static/grade.js` (shader), `roughcut/colour.py`, `research/tools/assemble.py` (`lut_vf`)]: Lumetri › Curves (RGB master + R/G/B) and › Color Wheels (shadows/midtones/highlights), plus a Lumetri Scopes panel tab (waveform + vectorscope of the Program frame).
   - **Done =** tests: a curve point and a wheel push move `grade.probe()` within 150 ms and the exported frame by the same amount (±3 %); the scope's waveform of a solid-grey fixture is a flat line at the grey's IRE.
6. **An Effects bin for transitions.**
   - **Files:** [`app/static/dock.js` (Effects tab), `app/static/timeline.js` (drop on an edit), `roughcut/tracks.py` + `research/tools/assemble.py` (kinds)]: Video Transitions › Dissolve (Cross Dissolve, Dip to Black, Dip to White, Film Dissolve) and Audio Transitions › Crossfade (Constant Power, Constant Gain, Exponential Fade), draggable onto an edit or a clip end; a transition at a clip's start/end (fade from/to black).
   - **Done =** tests: dragging Dip to Black onto an edit makes a transition whose Program and export midpoint is black (luma ≤ 5 %); a fade at the sequence's first clip starts at black.
7. **Keyframe safety like Premiere's.**
   - **Files:** [`app/static/effects.js`]: turning a stopwatch off with keys asks "This will delete existing keyframes" (or says it in a toast with Undo) and is one undo entry.
   - **Done =** test: stopwatch off on a keyed Opacity shows the warning; Cancel keeps the keys; OK removes them and one Ctrl+Z brings them back.
8. **The proposal state's small chrome.**
   - **Files:** [`app/static/workspace.css` / the Source transport, `app/static/timeline.js` ghost labels, `app/static/app.js` toast]: the Source transport keeps ≥ 4 px between controls down to 340 px (drop the duration first); ghost names never read "C…" (clip number only, e.g. "01", when narrow); the "building proxies" toast shows once per pass, then lives in the job pill.
   - **Done =** `test_workspace.py` overflow/spacing check at 1440×900 with a pending proposal: no two Source transport controls closer than 4 px; no ghost label ends in "…" with < 3 characters before it; the toast appears at most once per pass.
9. **A whole-clip drop ends on the clip's grid.**
   - **Files:** [`app/static/timeline.js` drop path, `app/server.py` clip durations]: the out of a whole-clip drop is floor(duration × fps) / fps.
   - **Done =** test: drop a 29.97 clip of duration 35.1851 s; `out × fps` is an integer; `tl.total()` equals the exported frame count / fps.
10. **Nothing outlives the server.**
    - **Files:** [`app/server.py`]: the export review copy (and any `-vf scale=min(1280,iw)` copy job) is spawned like the YIELD encodes (process group / PDEATHSIG, killed on shutdown).
    - **Done =** `test_yield.py::test_the_shutdown_hook_kills_every_encode` extended: start an export, SIGTERM the server during its review copy, `pgrep` finds no ffmpeg writing into `--work` 1 s later.

## What I could not verify
- **L / K / Space playing a pending proposal on an empty sequence.** My probe read the wrong player; I took it from the gate test.
- **The gate's own perf line.** It skipped twice at load 13.6-14.4; I substituted three `ROUGHCUT_PERF_BURNERS=0` runs (all passed). Box load never fell below ~11 in my session.
- **Audio by ear**, including the Constant Power crossfade (meters −36 to −8 dBFS stand in; the gate's `test_transitions` checks the cos/sin levels).
- **The one seek at 1440 that fired no frame callback** within 5 s: not reproduced, cause unknown.
- **Highlights/shadows/whites/blacks in an export** (gate `test_render_keyframes`).
- **CLS:** no layout jump seen in any screenshot.
