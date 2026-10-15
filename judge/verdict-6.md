VERDICT: FAIL

# Roughcut redesign: verdict 6

- **Rev judged:** `ee3f15e` (branch `redesign/2026-10-14`, wave 6 plus the lead's fixes `bc8d5d7` and the Playback Resolution commit). I served the frozen snapshot `~/roughcut-loop/snap-ee3f15e`. `diff -rq` against `git archive ee3f15e` printed nothing. It ran on 127.0.0.1:8781 with `--work ~/roughcut-loop/judge6/work` and `--footage ~/footage/killington-neutral` (12 GoPro clips: CLIP_01-03 are HEVC 59.94, CLIP_04-12 are H.264 29.97 with a −180° matrix). The work dir was fresh, so the land was cold.
- **Karl's live board:** `35cb147`. It is an ancestor of `ee3f15e` and **166 commits behind** (37 since verdict 5's `867f694`). I did not touch port 8765.
- **Box load (6 cores, shared with the NOITA/STICKFIGHT `tourney.mjs` loops and another pytest):**
  - 8.6 at the cold land.
  - 12.5-25.3 (mean 21.2) during the first pass.
  - 19-25 for the editing, playback and keyframe runs.
  - 14-22 during the full export.
  - 11-25 across my perf-test runs.

  Every timing below was taken under that load. My own server's background proxy build used about 2 cores for roughly 50 minutes after the pass.
- **Model spend:** backend `claude_cli` (`charged: false`). I ran one real 12-clip first pass (Start, Stop after 30 s, Re-run) and five re-runs (three to rebuild a pending proposal for the save repro, two in the review). There was no Ask and nothing was billed.
- **Cleanup:**
  - I killed my server (uv 1342143, python 1342161) and the export review-copy ffmpeg under `judge6/work`.
  - `pgrep -af roughcut-loop/judge6` now shows nothing and port 8781 is free.
  - The only repo write is this file, uncommitted.

## Gate: reused the lead's run, `~/roughcut-loop/gate-ee3f15e-20261015T1325/summary.md`. GREEN, 1701 s
I did not re-run the full gate. Its `ref ee3f15e = ee3f15e` matches the snapshot I diffed.

| check | result |
|---|---|
| browser | ✓ Playwright 1.64.0 / Chromium 156 |
| suite | ✓ 1192 passed, 0 failed, 0 flaky, 0 broken. The one skip is `test_dictate` (live whisper). `test_perf_latency::test_latency_under_load` **ran and passed** (18.7 s), as did `test_id_race`, `test_keyframes_ui` (8) and `test_render_keyframes` (16). |
| screens | ✓ 7 PNGs, 0 page errors, every count equal to its budget (`/ shot` 119·65) |
| journey | ✓ 19 steps, 55 s: rotated 59.94 upright, 0.4 s shot, drop-grade-reload, reject-rerun, Selects, export + ffprobe. **The "!! review copy failed" line is gone** (it ends "review copy journey.mp4 in 4.1s"). |
| hygiene | ✓ clean |

## Rubric changes log (rulings on the entries since verdict 5)
1. **Lead, wave 5: load-aware burners (cores − load) and a visible skip above 2× cores.**
   - **Honest in the burner math.** Stacking a burner per core on top of other loops measured the box, not the app.
   - **LENIENT in how the skip lands in the gate.** A skipped perf test leaves the suite green. The gate summary only counts "1 skipped" and does not say which test. At load 23.9 my natural run printed `1 passed, 1 skipped`, with the reason in `-rs` output only.
   - The skip also hides the load where this machine actually lives during a pass. The first pass alone drives it to about 21, and the app's own proxy build keeps it there for about 50 minutes.
   - Remedy: the gate names a skipped `test_perf_latency` and marks the suite amber, not green, and the summary prints the load at which the test ran.
2. **Wave 6 PLAYBACK: 67 ms hole, a 0.4 s shot crossed both ways, seeks median ≤ 250 / max ≤ 400, a per-frame "no picture / no wait text" check. Honest.** This is exactly what verdict 5 asked for. It is also marginal, and the fixture is still easier than real footage. My runs of `test_latency_under_load`:

   | run | load | result | worst hole | where |
   |---|---|---|---|---|
   | natural | 23.9 | skipped | — | — |
   | natural | 12.3 | **fail** | 83.3 ms | ~45 ms after the second cut (the lead's disclosed failure, reproduced) |
   | natural | 11.2 | pass | — | — |
   | `BURNERS=0` (4 runs) | 12.7-16 | 2 pass, 2 fail | 83 ms | inside shot 1 (cut gaps 33 ms) |
   | `BURNERS=0` (4 runs) | 22-25 | 0 pass, 4 fail | 166-233 ms | at the cut out of the 0.4 s shot |

   Seeks passed in every run that reached them. On Killington at load 16, with the cut's 1280 proxies built, real cuts showed **200-450 ms holes** (table under dimension 6). The fixture's 8 s testsrc clips do not stand for 4K GoPro proxies with a 1 s keyframe interval.
3. **KEYUI: `/ shot` controls 61 → 65. Honest.** The four extra controls are Premiere's stopwatches (Position, Scale, Rotation, Opacity), measured with no headroom. ◀ ◆ ▶ are hidden unless a property is animated. `60-keyframes-ec.png` shows the density is earned.
4. **Playback Resolution (≤ 640 px drawn while playing, full size parked), CHANGELOG only. Honest in substance, but procedurally it belongs in the rubric log.**
   - Premiere itself plays at reduced resolution (½ by default for heavy media), so the trade is standard.
   - In Premiere, though, it is a visible Program-monitor setting the editor can set to Full. Here it is a hidden constant: at 1920 the 640-px picture is stretched to 840 px while playing.
   - `test_perf_latency` now asserts it, so it is a test-shape change and should be logged in the rubric log.

## Wave 6 and lead claims checked (not trusting LOOP.md)
| claim | what I found |
|---|---|
| One save in flight, ids minted once (lost grade fixed) | **Mostly true, with one serious exception** (blocker 2).<br>**Works:** in 3 of 4 tries, Accept sent **exactly one PUT** (replies 0.07-0.25 s, the ask answered right after). A Project row dropped on V1 (landed 78-80 ms), warmed at once with `effects.set('temperature', 40)` and reloaded 1.5 s later came back as `g46a5a1e5de` with `balance.gain [1.06, 1, 0.94]`. There was no `tmp-` key on disk, Effect Controls read 40.0, and 0 responses were ≥ 400 (`review2.log`).<br>**Failed once:** on the **first Accept of the fresh pass** (17 of 19), the save chip still read **"unsaved…" 3 s later**. A drop+grade followed, then a reload about 5 s after Accept. **The whole accepted cut was gone:** an empty sequence, the EDL file never rewritten (mtime 13:54:31, 132 bytes, `segments: []`), and the ask never answered. `/api/asks/answer` is posted only after the PUT replies, so that PUT never got a reply (`review.log` 14:09:32-14:10:04).<br>**Not reproduced** in three later tries on the same steps (`repro_save.log`, `repro_drop.log`, `review2.log`). I measured 0.8-1.0 s stalls on `GET /api/firstpass` meanwhile.<br>**There is no `beforeunload` guard and no keepalive flush** (`grep beforeunload app/static/*.js` → nothing). |
| Timeline render ~9 ms under load | **True.** `tl.render()` took 1.1-9.6 ms (median 1.3) at load 24 on a 21-shot cut (`render_t.py`). |
| Keyframes previewed and exported | **True, driven through the UI** (`kf.log`):<br>• The stopwatch click made `[[0, 100]]`. Typing 0 into the Opacity value at +2 s made `[[0,100],[2.002,0]]`.<br>• 2 diamonds drawn. ◂ and ▸ moved the playhead to 0.000 and +1.999.<br>• The Program's `effects.now().opacity` read 97.5 / 50.1 / 2.6 at +0.05 / +1.0 / +1.95 s (picture luma 196 / 142 / 8; `60-keyframes-ec.png`).<br>• Ctrl+Z removed the key and Shift+Ctrl+Z restored it. It survived a reload.<br>• Match Sequence In/Out export: 3840×2160, `60000/1001` CFR, 120 frames, 2.002 s. Luma against the source frame was **1.05 / 0.52 / 0.00** (`64-export-*.png`). The full Quick export's midpoint was luma 93 (half). |
| Lumetri Basic Correction | **Present:** Temperature, Tint, Exposure, Contrast, Highlights, Shadows, Whites, Blacks, Saturation under White Balance / Tone (`40-1440-13` EC text). I did not drive highlights/shadows into an export; `test_render_keyframes` (histogram, grey-at-a-third = the shader's formula) covers it in the gate. |
| C picks the razor only | **True.** C changed the tool and toasted "razor tool — click a shot to cut it there · Ctrl+K cuts at the playhead"; the segment count did not change. |
| Add/Delete Track undoable, V3 drag | **True.** Ctrl+Z after Add Video Track removed V3. A V3 clip dragged +60 px moved 5.005 → 26.56 s. Delete Track removed V3 and its clip with "Ctrl+Z brings it back". |
| A3+ plays in the Program | Not driven (gate `test_program_a3` only). |
| V2+ overlays inside the parts | **True.** A full Quick 1080p export with a 1.6 s CLIP_10 overlay on V2 wrote no `*tracks*` file. The frame at 20.6 s matches CLIP_10 at 12.7 s upright (mean abs diff 1.4 against 54.8 when flipped). |
| Export disabled while measuring | **True.** "measuring…" with `disabled: true`. On a 2 s 4K In/Out range at load 27, though, measuring took **~100 s** before Export could be pressed. |
| Pending proposal owns the Assistant, checks are plan data, ≥ 5 rows on a re-run at 1440 | **The top and the data are true.** No Ask form sits above the proposal. `plan.checks` carries `{text, clip, in, out, kind, shot}` and renders "THINGS TO CHECK + 3 more" with clickable ranges.<br>**The rows are not:** only **3 rows were fully in view** on both the first and the re-run proposal at 1440×900 (`31-proposal-1440.png`, `38-rerun-proposal.png`). "Things to check" takes ~170 px of a 379 px panel on real output.<br>**Check text is cut mid-word at 200 characters** ("…someone the viewer can ide"; `roughcut/revise.py:748` `CHECK_TEXT_MAX = 200`). |
| Picks below the ghost names; ghost in V1 on an empty sequence; Program frame | **True.** 0 pick markers intersect a `.gtxt`. The ghost lane sits in V1's row (top 609 = 609, height 121 = 121, class `in-v1`). `pv0` holds `CLIP_01.mp4#t=1.30` at `readyState 4`.<br>Against: **L/K on that screen toasts "nothing to play yet"**; only "▶ Play it" plays the proposal. |
| Selects reads like a bin | **True.** One-line rows with a kind chip, a quoted name, Media In, Duration and Reason, and "review on the pass" as a panel button (`40-1440-07-selects.png`). `,` from a row inserted the pick's exact range on the grid twice (CLIP_01 0-24 → 0-23.9906; 120.1-128.64 → 120.0866-128.6285).<br>Against: double-clicking a pick opens its clip in Source **without** its In/Out (`rcSource.inPoint: null`). |
| Source In/Out on the clip's own grid; a gap from `.` past the end | **True.** CLIP_02 134.9348/135.3352 (59.94) and CLIP_11 127.3272 (29.97 → 3816.0 frames). `.` with the playhead 10 s past the end left a **10.01 s gap** then CLIP_11. |
| Exactly two Run first pass controls cold | **True.** `#firstpass` (header) and `#tlEmpty` (Timeline) only; the Program reads "press , (Insert)" (`01-cold-5s-1440.png`). |
| Short shots hold the frame (no black, no "buffering…") | **True in what it says, not in how it plays.** No black and no wait text on any rAF in 9 crossings of a real 0.4 s CLIP_02 shot between two CLIP_01 shots. But the incoming buffer sat at `readyState 1` for 3-21 rAFs, giving **200-350 ms frozen holes** at the cut out of the short shot (load 16-25; `framelog-*-short*.json`). This is the queued "two buffers" item, and it also happens when the next shot is the *same* file at a distant time. |
| The cut's proxies build first | **True.** 1280 proxies landed CLIP_01 14:03, 11 14:09, 02 14:18, 04 14:25, 08 14:29, 07 14:36, 06 14:44, 12 14:48, 05 14:49, then CLIP_09 (not in the cut). That is about 50 min after the pass at load ~22; until then the Program shows the 640×360 preview. |
| Playback Resolution | **True.** The grade canvas is 640 px wide while playing and 674 (1440) / 840 (1920) parked. |
| A video idle after a seek is nudged | Not separately verified. |

## Driving the real first pass like an editor
**Run (`pass.log`).** I pressed Run first pass cold. Stop after 30 s took **102 ms**, and 0 children were left 1.5 s later; "Re-run first pass" survived a reload. Then I pressed Re-run.
- Heard 12/12 at 284 s.
- Looked 12/12 at 730 s.
- A **19-shot, 2:52.4 proposal at 800 s (13.3 min; load 12.5-25.3, mean 21.2)**, with 77 picks.

While it ran, the header showed "First pass · n/12"; afterwards it showed "Next · Read the proposal and accept or discard it". Re-runs took 42-93 s.

**As an editor.** It is a real rough cut.
- It cold-opens on a joke (the GoPro at 9 %), puts the stream fall third, hard-cuts "200 hours later" to the crew's roll call, and runs a "rowdy" refrain.
- A ski-patrol scare sits in the middle, the jumps come late, and it ends on a tired, funny sign-off.
- Each shot carries a one-line reason.
- The model's own checks are the right ones: is the buried person visible, is "coward / I hit it" mostly glove, is the "baby jump" in frame.

I would keep about 12 of the 19 and re-order two. Each re-run on an empty sequence proposes a materially different film (19, 23 and 16 shots, three different cold opens). That is fine for an AI first pass, but worth knowing.

**Review, accept part, hand trim, re-run (`review2.log`).**
- Rejected two shots (ghost ✗ and table checkbox). "Accept 14 of 16" applied exactly the 14 with one PUT.
- Dropped and graded CLIP_05, then reloaded: it held.
- Ripple-trimmed shot 1 to 31.9986-33.2999. The re-run panel said "2 shots edited by hand since".
- The re-run (42 s) kept all 13 cut shots **bit-exact** and on grid, kept my trim and my dropped clip, re-added neither rejected range, and added 1 new shot. Its Accept sent one PUT.

## Playback across cuts on real footage (`edit-*.log`, `framelog-*.json`)
| where | load | Program source | seek → frame (6) | across a cut | across the 0.4 s shot |
|---|---|---|---|---|---|
| 1440 | 20.7-20.9 | 360p previews (most) | 128-220 ms, median 192 | 17.4-29.9 fps, holes 67-150 ms | (script mis-focused) |
| 1440 | 24-25 | mixed | — | — | 14-16 fps, holes 250-350 ms |
| 1920 | 24-25 | mixed | 172-**462** ms | 10.9-13.3 fps, holes 283-**600** ms | 13-19 fps, holes 200-250 ms |
| 1440 | 15.8-17.2 | **1280 proxies** | 126-**410** ms | 18-22 fps, holes **233-450** ms | 20-21 fps, holes 200-300 ms |

No black frame and no "buffering…" text anywhere (the verdict-5 black hold is fixed). The meters move between −31 and −8 dBFS.

## The 12 dimensions

| # | dimension | score | evidence | vs Premiere Pro |
|---|---|---|---|---|
| 1 | Orientation | **8** | Cold: rows at **0.25 s**, a picture at **0.42 s**, one blue "Run first pass", exactly two first-pass controls, the Program reads "drag a clip to the Timeline or press , (Insert)", 0 page errors (`01-cold-5s-1440.png`). Against: a console 503 on `/media/proxy/CLIP_01.mp4` in the first session. The list view has no thumbnails (the same as Premiere's default list). | Premiere's empty Editing workspace. |
| 2 | First pass visible + re-runnable (K1) | **9** | Header control in every state: cold, running ("First pass · 0/12"), stopped (survives reload), pending, after hand edits (dot). Panel in 98 ms, Stop in **102 ms** with nothing left running. The real pass took 13.3 min. Re-runs take 42-93 s, name what they keep ("2 shots edited by hand since", "Won't re-propose 2 ranges you rejected") and do exactly that. Two clicks from anywhere. | n/a; as an AI step it is clear and re-runnable. |
| 3 | Footage in, no ceremony | **8** | Drag to V1 landed in **78-80 ms**. `,` from Selects inserts the pick exactly. Source I/O sits on the clip's own grid. `.` past the end leaves a gap. Selects is one line per row. Against: double-clicking a pick does not bring its In/Out into Source. In my run, a mouse drag of a Project row onto the far right of V1 did not land (the end-label area). | Premiere's bin, plus picks. |
| 4 | Workspace | **8** | Unchanged Editing arrangement: Program **674×379** (1440) and **841×473** (1920), vertical meters, panels 286/773/365 (`40-1440-00`, `40-1920-04`). Against: a pending proposal's table gets a 379 px corner panel and shows **3 rows**. | Premiere's Editing workspace. |
| 5 | Timeline craft | **9** | `edit-1440.log`: +/−/\, →, ↑/↓ with select following, J/K/L, I/O, M, **C = tool only**, V, Ctrl+K split, undo/redo. Ripple trim (−5.97 s); roll (total held to 0.1 ms); move (order changed, survived reload). Add Track and **Ctrl+Z removes it**; **V3 drag +21.6 s**; Delete Track takes its clip and is undoable. A gap from `.`; razor; ripple. All edit points on the clip grid except a whole-clip drop's media end. Against (queued): keys are not shifted on a razor or an in-trim. | Premiere's core trim model, tracks and keys. |
| 6 | Monitors and playback | **7** | The playback table above. Seeks are ≤ 250 ms at 1440 under load 21, but 410-462 ms at load 16-25 once proxies or 1920 are involved. Cuts on real footage show **200-600 ms frozen holes** at load 16-25. There is no black and no "buffering…" (fixed). The fixture test passes 50 % at 2-2.7× cores and fails at 4×. The Program shows the 640×360 preview for about 50 min after a pass. | Premiere would not freeze for a third of a second at a cut. |
| 7 | Inspector, effects, colour | **8** | **Keyframes:** stopwatch, typed value, ◂ ◆ ▶, diamonds on a mini timeline, Program at the midpoint = 50 %, export 1.05/0.52/0.00, undo, reload (`60-keyframes-ec.png`, `64-*`). **Lumetri Basic:** WB plus six tone controls plus saturation. Drop-then-grade survives reload (when the save lands). Against (queued): keys do not follow a razor or an in-trim; V2+ overlays ignore their own controls. Lumetri has no curves, wheels or HSL; there are no transitions. | Premiere's Effect Controls now; a fraction of Lumetri. |
| 8 | Export | **8** | Ctrl+M dialog in 160 ms. Match Sequence In/Out: 4K `60000/1001` CFR, 120 frames, keys rendered. Full sequence Quick 1080p: **237.98775 s against `tl.total()` 237.9875** (+0.25 ms), `60000/1001` CFR, 14265 frames, one AAC, the V2 overlay composited in-part, **260 MB against ≈ 221 MB** (−15 %). Export is disabled while measuring. Against: **1024 s (17 min) for 3:58** at load 14-22 (4.3× real time). The measure step for a 2 s 4K range took ~100 s. | A real Export Settings, but slow. |
| 9 | Looks like a shipped pro tool | **8** | The 1440/1920 screens read as Premiere: monitors, filmstrips, waveforms, timecode, stopwatches; no header overflow, no ellipsized leaf in the main view, no link-blue. The verdict-5 defects (black buffering, markers over names, Selects wrapping, Ask above proposal, three Run buttons, "press ,") are fixed. Against: ghost names read "C…" at fit zoom. Check text is cut mid-word. A "nothing to play yet" toast over a visible proposal frame (`31-proposal-1440.png`). The proposal table is cramped to 3 rows. | Close; the AI review corner is the roughest part. |
| 10 | Responsive, reliable | **6** | Click → Effect Controls 54-180 ms (median ~75, 2 of 8 not caught by my probe). effects.set 39-64 ms. Keys 88-180 ms including the Playwright round trip. `tl.render` 1-10 ms at load 24. One PUT per Accept, no `/api/lut` 404s, the drop-then-grade holds. **Against: a lost accepted cut** (claims table, blocker 2). Its save never replied within ~5 s, the reload discarded it, and no unload guard warned. There was also a console 503 on a proxy in the first session. | Premiere never drops your cut on a reload. |
| 11 | Proposals are reviewable edits | **8** | Ghosts in V1 on an empty sequence with a Program frame. Picks as Selects rows and 14-18 markers, none over a name. Checks are plan data with clickable ranges. Per-shot ✓/✗ applied exactly. Rejections remembered. Trims kept bit-exact. "free on your plan". Against: **3 rows in view** at 1440 (the claim was ≥ 5 on a re-run). Check text truncated at 200 characters. L/K cannot play the proposal on an empty sequence. Notes check `checked: 0`. | Premiere-like review with a cramped table. |
| 12 | The journey | **9** | 19 steps in the gate, including the real-footage shapes (rotated 59.94 HEVC upright in Source/Program/export, a 0.4 s shot, drop-grade-reload, reject-rerun, Selects, export + ffprobe), and no "!!" line (`journey.log`). Against: it cannot see a save that stalls on a busy server, or real-proxy cut holes. | n/a |

## Section K
- **K1 "not clear how to re run the first pass": RESOLVED on real footage, as in verdicts 3-5.** One header control in every state, Stop in 102 ms, and re-runs that say what they keep and won't re-propose, then do exactly that. Karl to confirm.
- **K2 "not premier pro quality … complete redesign": OPEN.**
  - **Would a harsh Premiere editor still call this a toy? No.** It has Premiere's Editing workspace and keys, a three-point edit from Source and from a bin of AI picks, ripple/roll/razor/gaps/tracks, keyframes with a stopwatch, a Lumetri Basic, Match Sequence export at the right frame count, and an AI first pass that is a better assistant editor's first assembly than most.
  - **Would that editor choose to cut in it today? Not yet.** Playback freezes for 200-600 ms at cuts on real 4K GoPro proxies while the box is busy (and the app's own proxy job keeps it busy for about 50 minutes after a pass). Once, the accepted cut vanished on a reload. There are no transitions (no Ctrl+D or audio crossfade). Lumetri stops at Basic. A 4-minute Quick export takes 17 minutes.
  - My read: it has crossed from "toy" to "credible rough-cut tool". It has not crossed to "Premiere quality". Karl decides K2.

## Section S
- **Inference only through `roughcut.inference`, no model-ID literal outside `config.py`: intact.** The grep finds literals only in `roughcut/config.py` and `tools/gate.py`'s own pattern. `import anthropic` appears only at `roughcut/inference.py:592`. Hygiene is clean.
- **No price reads as money spent on `claude_cli`: intact.** Every step reads "free on your plan", and `charged: false`.
- **Karl's footage and work dirs never written: intact.** All writes went under `~/roughcut-loop/judge6/`, `~/footage` was only read, and 8765 was untouched.
- **INTAKE decisions:** no violation seen.
- **No Co-Authored-By; a CHANGELOG entry on every commit: intact.** `867f694..ee3f15e` has 0 Co-Authored-By lines, and every non-merge commit touches CHANGELOG.md.

## Blockers
1. **K2 is open.**
2. **A lost edit.**
   - **What happened:** on the first Accept of a fresh pass, the save had not replied 3 s later (chip "unsaved…"). A reload about 5 s after Accept left an empty sequence, the EDL was never written and the ask was never answered (`review.log`). Rubric 10 requires "nothing lost when … reloading".
   - **What the code shows:** there is no `beforeunload` guard and no keepalive flush.
   - **What is not known:** what stalls the server. I did not reproduce it in three later tries, so the root cause is unproven.

## THE THREE BIGGEST GAPS TO PREMIERE
1. **Playback still stutters on real footage.** On the Killington clips, every cut, and especially the cut out of a very short shot, can freeze the picture for a quarter to half a second while this machine is busy. Jumping around can take up to half a second to show a frame. The busy machine is partly Roughcut itself: for about 50 minutes after a first pass it builds proxies in the background, and until those finish the Program shows a soft 360p picture.
2. **It once lost the whole accepted cut.** Right after the first pass I accepted the proposal, added a clip and reloaded a few seconds later. The timeline came back empty. The save had never finished, and nothing warned me before the page reloaded.
3. **The finishing tools stop early.**
   - There are no dissolves or audio crossfades at a cut.
   - Colour is Lumetri's Basic panel only.
   - Keyframes break if you razor or trim the start of a keyed clip.
   - A 4-minute export takes about 17 minutes.
   - The AI proposal's shot list fits 3 rows in its corner at laptop size, and its "check this" notes get cut off mid-word.

## PUNCH LIST (most important first; [files] = lane ownership)

1. **A save never vanishes on reload.**
   - **Files:** `app/static/app.js` (`save`/`saveRun`/`saveTimer`: a `beforeunload` prompt while a save is pending or in flight, plus a `pagehide` flush with `fetch(..., {keepalive: true})`); `app/server.py` (log any request over 500 ms with its handler; find what stalls `PUT /api/project` right after a first pass ends, e.g. the background index writes or `firstpass_state()` reading every index, and move it off the event loop or cache it).
   - **Done =** new `app/tests/test_save_reload.py`. With `PUT /api/project` delayed 3 s server-side, Accept then `page.reload()` at 1 s: either the reload is refused by `beforeunload` (Playwright `dialog` fired) or the accepted cut is on disk after the reload. A gate line reports the slowest request seen during the journey, which must be ≤ 1 s.
2. **Real-footage cuts with no visible hole.**
   - **Files:** `app/server.py` proxy encode (`-force_key_frames` every 0.5 s or shorter for the 1280 proxies, or a short all-intra "cut cache" around each edit point); `app/static/app.js` player (a third buffer; preroll the shot after next when the next is < 1 s; keep the incoming buffer seeked during the outgoing shot even when it is the same file); `grade.js:493`, `fx.js:1323`, `effects.js:233` (the hard-coded `#pv0/#pv1`); `grade.js` (cache LUT textures per record and upload the next shot's in slices during preroll).
   - **Done =** `test_perf_latency` gains a fixture of 3840×2160 59.94 H.264 with a 1 s GOP, proxied by the real `proxy` path, and passes 6 of 6 at load ≤ 2× cores. My Killington run of 0.4 s crossings has its longest hole ≤ 100 ms.
3. **The app's own background work yields to playback.**
   - **Files:** `app/server.py` job runner (renice proxy/preview/close-look ffmpeg to 19 and SIGSTOP them while the Program plays, SIGCONT on pause; range-first proxies for the cut's in/out ranges before whole clips).
   - **Done =** a test that starts a proxy job, plays, and asserts the job's ffmpeg is stopped or niced during play. On Killington the Program shows 1280 material for every cut shot within 10 min of a pass.
4. **The proposal review room at 1440.**
   - **Files:** `app/static/app.js` (`showProposal`, `proposalChecksHtml`: checks collapse to one line each with "+N more", and the Assistant grows into the Effect Controls column or the Timeline's height while a proposal is pending; L/K/Space play the proposal on an empty sequence as "▶ Play it" does); `roughcut/revise.py:748` (no mid-word cut: sentence-aware trim or `CHECK_TEXT_MAX` 400 with CSS clamping).
   - **Done =** `test_proposal_review.py` on a real-length plan (6 checks of 150-200 characters): ≥ 5 `tr.pshot` fully in view at 1440×900 on a first and a re-run proposal; no check text ends mid-word; L on an empty sequence with a pending proposal sets `player.playing`.
5. **Keys follow the media.**
   - **Files:** `app/static/timeline.js` (`split()`, in-trim), `roughcut/edits.py` (split ops).
   - **Done =** test: a razor through a shot with opacity keys at 0 and 2 s leaves both halves showing the same picture value at every frame (a key at the cut), and an in-trim of +0.5 s keeps each key on the same media frame (as queued in LOOP.md).
6. **Transitions: Ctrl+D cross dissolve, Shift+D audio crossfade at the selected edit.**
   - **Files:** `app/static/timeline-keys.js`, `app/static/timeline.js` (an edit-point transition object with a duration, drawn on the cut, trimmable), `research/tools/assemble.py` (xfade/acrossfade inside the parts), and the Program preview (crossfade the two buffers).
   - **Done =** test: Ctrl+D on a cut makes a 1 s dissolve. The Program pixel at the midpoint is the mean of both shots (±10 %), the exported frame likewise, and the export length is unchanged.
7. **Export speed.**
   - **Files:** `research/tools/assemble.py` (`h264_nvenc` for Quick/YouTube presets when available, since previews already use `-hwaccel cuda`; encode parts in parallel up to cores/2), `app/static/export.js` (the measure sample capped at 3 s of output, so the measure step takes ≤ 15 s).
   - **Done =** a 4-minute Killington Quick 1080p export in ≤ 2× real time at load ≤ 12, and "measuring…" ≤ 15 s on a 4K In/Out.
8. **V2+ overlays honour their own controls** (queued). [`research/tools/assemble.py` overlay compositing, the Program's V2 layer in `app.js`.] Done = test: a V2 clip at opacity 50 % and scale 50 % composites over V1 in both the Program and the export (±10 %).
9. **The perf gate cannot pass by skipping.**
   - **Files:** `tools/gate.py` (name a skipped `test_perf_latency` in the summary, mark the suite amber, retry it after the rest of the suite at a quieter moment, and print the load it ran at), `app/tests/test_perf_latency.py` (record load and outcome in a JSON the gate reads).
   - **Done =** a gate run with `ROUGHCUT_PERF_FORCE_SKIP=1` is not GREEN.
10. **Premiere's small expectations.**
    - **Files:** `app/static/app.js` (a Playback Resolution menu on the Program monitor, Full / ½ / ¼, persisted, defaulting to today's 640 px rule); `app/static/dock.js` (double-clicking a Selects pick opens it in Source with its In/Out marked).
    - **Done =** tests: Full makes the grade canvas equal the monitor width while playing; double-clicking a pick gives `rcSource.inPoint/outPoint` = the pick's start/end on the clip grid.

## What I could not verify
- **Root cause of the lost cut.** I did not capture the PUT's reply in the failing run, and the three re-runs saved normally.
- **Playback on an idle box.** Load never fell below 11 during my session (other loops), so the cut holes on an idle machine are unmeasured.
- **Audio by ear.** The meters (−31 to −8 dBFS while playing) stand in.
- **A3+ audio in the Program.** I took this from the gate's `test_program_a3`.
- **Highlights/shadows/whites/blacks reaching an export.** I took this from the gate's `test_render_keyframes`.
- **The "idle video nudged after a seek" fix**, as such.
- **CLS.** No layout jump appeared in any screenshot.
