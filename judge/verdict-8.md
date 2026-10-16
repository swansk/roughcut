VERDICT: FAIL

# Roughcut redesign: verdict 8

- **Rev judged:** `1e99342` (branch `redesign/2026-10-14`, wave 10: BOOT, EXPORTOK, PERFGATE, TXHITCH, CRAFT + lead `321ec31`, `1e99342`). The gate re-archived it to `~/roughcut-loop/snap-1e99342`; `diff -rq` against a fresh `git archive 1e99342` printed nothing. I served that snapshot on 127.0.0.1:8781 with `--work ~/roughcut-loop/judge8/work` and `--footage ~/footage/killington-neutral` (12 GoPro clips: CLIP_01-03 HEVC 59.94, CLIP_04-12 H.264 29.97 with a −180° matrix). Fresh work dir, cold land.
- **Karl's live board:** `35cb147`, an ancestor of `1e99342`, **236 commits behind** (22 since verdict 7's `273b1f1`). Port 8765 untouched.
- **Box load (6 cores; NOITA's judge-9 tourney and my own gate ran beside me):** 12.2-12.4 at the cold land, 12.5-16.4 (mean 14.2) during the first pass, 9.8-20 for everything after. Every timing below is under that load.
- **Model spend:** backend `claude_cli` (`charged: false`). One real 12-clip first pass (Start, Stop at 30 s, Re-run) and one re-run after hand edits. Nothing billed.
- **Cleanup:** my server (uv 3176543 / python 3176654) was stopped with SIGTERM *during an export's review copy* (the punch-10 test); every judge8 process was gone 5 s later and port 8781 is free. Nothing of mine is running. The only repo write is this file, uncommitted.

## Gate: my own run, `~/roughcut-loop/gate-1e99342-20261015T2135/summary.md` — **GREEN**, 2914 s

| check | result |
|---|---|
| browser | ✓ Playwright 1.64.0 / Chromium 156 |
| suite | ✓ 1315 passed, 0 failed, 1 skipped, **0 flaky**, 0 broken. Perf `test_latency_under_load` **measured both times**: before the suite at load 12.0 (worst gap 50.1 ms, seek median 164 ms), after it at load 11.7 (worst gap 66.6 ms, seek median 94.8 ms); both at ≤ 2× cores, 0 burners, so neither leaned on the new shared-box rule. |
| screens | ✓ 7 PNGs, 0 page errors; / 93·51, /floor 52·7, /open 55·8, / shot 119·66, / film 101·50, / jobs 102·52, / fx 124·54 |
| journey | ✓ 20 steps, 49 s (now with the one-Opacity-key step before Export), slowest request 0.27 s |
| boot | ✓ the cut 0.95 s after a proxy lands (colour measurement patched to 10 s; `/api/junk` 0.083 s) |
| hygiene | ✓ clean |

The lead's own gate on this rev had two in-suite failures that passed alone (`test_floor_ui::test_a_clicked_verdict_drops_a_pending_batch_question`, `test_perf_latency::test_the_grade_draws_in_a_worker…`); mine had none. They are still flaky tests (punch 9).

## Rubric changes log (rulings on the two wave-10 entries)
1. **PERFGATE (a)+(b): `JOURNEY_SLOWEST_S = 1.0` and the 4K 59.94 perf fixture, logged late. Honest.** Both make the gate stricter, and the entry says plainly what the slowest-request line cannot see.
2. **PERFGATE: the perf gate measures on a shared box (0 burners above 2× cores, run before and after the suite, the better-conditioned run counts, a pass anywhere is green, a fail above 2× is AMBER). Honest on balance.** The lenient part (a pass at > 2× cores turns the gate green) is fair, because a busier box is a harder test of the same limits, and the line names the load. A fail above 2× reads AMBER, which still blocks a PASS. It did not matter today: both runs measured at ≤ 2× cores. **But the test still measures only the live `<video>` buffer, never the graded canvas**, and the canvas is what you see on every coloured shot. On Killington I measured the canvas at 24-27 fps with 70-114 ms holes (below). That is a coverage gap rather than a leniency: punch 2.

## Wave 10 claims checked (not trusting LOOP.md)
| claim | what I found |
|---|---|
| BOOT: the cut shows before the junk marks; `/api/junk` never measures | **True.** Two reloads fired within ~1 s of a whole proxy landing (CLIP_11, CLIP_02): the cut was there in **365 ms and 302 ms**, with a Program picture (`bootwatch8.log`, `90-boot-after-proxy-1.png`). Verdict 7 measured 27-35 s. Gate boot line: 0.95 s with a 10 s measurement. |
| EXPORTOK: a held key renders; a failed export names its shot; the review copy runs in a procscope | **Held keys true:** one Opacity key (stopwatch, then 40 % on the same frame, `[[0.5005, 40]]`) → In/Out export OK in 6 s, 60 frames CFR, picture at **0.38× the source luma** (expected 0.40). One Rotation key + one Volume key on another shot → OK. One Opacity key at 50 % on a **V2** clip → OK (`kf8.log`, `kf8b.log`). Full sequence with all three keyed shots, a V2 overlay and a Cross Dissolve: OK (below). **Failure UI: not reproduced by me** (I found no user action that fails a part). I take it from `test_export_dialog::test_a_failed_export_names_the_shot_and_goes_to_it` (green in my gate) and `export.js:paintFailed`. **Procscope true:** SIGTERM during the review copy of a 4:41 export: the review ffmpeg was gone 1 s later, the server and a proxy ffmpeg within 5 s (`killtest.log`). |
| PERFGATE | True, as ruled above. |
| TXHITCH: dissolve followers rate-corrected; range proxies cover handles | **Better, not done.** Seven plays through a real Cross Dissolve (CLIP_07 → CLIP_08) at 1440 and 1920, load 10-12: the live buffer's longest hole was 66.7 ms in 4 plays and **83.3-100 ms in 3**. The followers inside the mix window held **up to 116.6 ms** (`pvTxOut`, `fl8-1440-plain7.json`). The lead's own figure (83 ms) stands: the ≤ 67 ms criterion is not met. |
| CRAFT: stopwatch-off confirm | **True.** The stopwatch on a keyed Opacity opens "Delete keyframes · This action will delete existing keyframes. Do you want to continue? · Opacity: 1 keyframe." Cancel and Esc keep the key; OK removes it and keeps 40 %; one Ctrl+Z brings it back (`92-stopwatch-off-confirm.png`). |
| CRAFT: proposal chrome | **Mostly true.** The Source transport at 343 px now has room (the duration is dropped). Ghost labels read "02", "03", "CLIP_05". **But one ghost still read `0…`** at fit zoom on the real first proposal at 1440 (`crop-ghost-labels.png`): the canvas measure says "05" fits, the browser's ellipsis says it does not. The "building proxies in the background" toast still comes back on **every page load** for the hour the proxies take, parked over the Timeline (`45-1920-shot-selected.png`, 22:12, 25 min after the pass). |
| CRAFT: whole-clip drop on the clip's grid | **True.** CLIP_05 dropped whole: 0-35.1685 = exactly 1054 frames at 29.97. The full export's 8429 frames at 30000/1001 = 281.2476 s against `tl.total()` 281.2477. |

## Driving it like an editor
**Cold land (`cold.py`, 1440 and 1920):** rows and a picture in 0.18-0.37 s, one blue control ("Run first pass"), exactly two first-pass controls, the Program says what to do on an empty sequence, 0 errors, no header overflow (`01-cold-5s-*.png`).

**First pass (`pass.log`, `review.log`):** the panel opens in 67 ms. Stop at 30 s took **71 ms**, with 0 children 1.5 s later. "Re-run first pass" survives a reload. The timed run gave a **17-shot, 2:56 proposal at 574 s** (load 12.5-16.4), all ranges on the frame grid, with checks that say what to look at. I rejected 2 (one on the ghost's ✗, one in the table): "Accept 15 of 17" applied exactly, with one PUT. I then trimmed shot 1 and dropped CLIP_05 whole. The re-run took **37 s**, said "keeps 2 of your shots exactly", kept all 15 surviving ids bit-exact, did not bring back either rejected range, and its Accept sent one PUT. It is two clicks from any state.

**Editing (`edit-1440.log`, `lat8.py`):** +/−, →, ↑/↓, J/K/L, I/O, M, C, V, Ctrl+K, undo/redo, ripple trim, roll, move, reload, Selects `,`, double-click pick → Source In/Out, Add/Delete Video Track, a V3 overlay, `.` past the end: all as verdict 7 found them, each one undo. Click on a block → Effect Controls in **25-79 ms**. Ctrl+M → dialog 138-193 ms (with the Playwright round trip). Ctrl+D on an edit with no handles refuses with "insufficient media … trim them to make room" (Premiere applies it with repeated frames and a warning).

**Right after the pass: shots with no preview yet play black, with no message.** The proposal was accepted at 21:47:55. At 21:49-21:54, seeks onto some shots took **4.4 s and 1.6 s** or never presented a frame. Plays across cuts into CLIP_04/07/09 showed **0 frames for 3 s**. On a CLIP_09 shot (its preview still building), the Program was **plain black for 11.5 s and more**: `#monWait` "preparing preview…" stayed hidden, no wait text anywhere, and the Project panel said "indexed" (`51-clip09-building-full.png`, `50-a-media-CLIP_07.png`). Meanwhile the server holds `GET /media/proxy/CLIP_09.mp4` for **20.0 s** before it answers 503 (curl). Previews for all 12 clips landed by 21:56 (~9 min after the pass). Range proxies for the cut landed by ~22:08. My own playback runs paused those encodes (YIELD), so a real user who presses Play to watch the cut makes the wait longer too.

**Playback after the media landed (`pb8-*.log`, recomputed inside the play window by `gaps8.py`):**

| size | load | seek → frame (6) | plain cuts: live longest | graded across a cut (canvas) | dissolve: live / followers longest |
|---|---|---|---|---|---|
| 1440 | 9.8-11.2 | 336, 208, 119, 89, 121, 74 | 66.6-66.7 ms (canvas on one: 88.4) | 26 fps, **91.8 / 98.4 ms**, 4-6 holes > 67 ms per 3 s | 66.7-83.4 / up to **116.6** ms |
| 1920 | 11-12.1 | 235, 158, 97, 77, 163, 154 | 83.3-83.4 ms (canvas 79.9) | 24-26 fps, **100.7 / 114.5 ms**, 5-9 holes > 67 ms | 66.6-100 / up to 100.1 ms |

On a graded shot the live video itself drops to 25-26 fps (holes of 83-100 ms) while the worker draws the canvas. The perf test runs at load 12 on its 4K fixture and reports 50-67 ms. It never measures the canvas (punch 2).

**Export (`export-quick-all.log`, `trans.log`):** the whole 4:41.25 sequence (21 shots; three keyed shots, a V2 overlay, a dissolve) at Quick 1080p took **108 s at load 14-20**: 30000/1001 CFR, 8429 frames, 281.2476 s against `tl.total()` 281.2477, one AAC, 224 MB against an estimate of 263 MB (−15 %). The frame at the dissolve's centre is **3.79** from (A+B)/2, against 21.9 (A) and 20.4 (B). A second, YouTube-preset run: 158 s. **Escape does not close the finished dialog** (true in 3 of 3 tries; `export.js:577` says it should).

## The 12 dimensions

| # | dimension | score | evidence | vs Premiere Pro |
|---|---|---|---|---|
| 1 | Orientation | **8** | Cold land 0.18-0.37 s, one blue first action, the Program's empty-sequence hint, 0 errors at both sizes (`01-cold-5s-*.png`). Boot after a proxy lands: 0.30-0.37 s (was 30 s). Against: the "Effects" tab is the AI effect designer, not Premiere's Effects bin, so a Premiere user goes there for a dissolve and finds a sketch tool; the Program goes black with no word right after a pass. | Premiere's Editing workspace. |
| 2 | First pass visible + re-runnable (K1) | **9** | Header control in every state; Stop 71 ms with 0 children; real pass 574 s; re-run 37 s keeps both hand edits exactly and leaves the 2 rejections out; 2 clicks. | n/a: a clear, re-runnable AI step. |
| 3 | Footage in, no ceremony | **9** | Drag to V1 81 ms; a whole-clip drop now ends on its grid (1054 frames); `,` from Selects exact; double-click pick → Source In/Out. | Premiere's bin + Source three-point edit. |
| 4 | Workspace | **9** | Program 674×379 / 841×473, vertical meters, panels 286/773/365 and 382/1034/488 (`edit-1440.log`, `45-1920-shot-selected.png`); the Assistant takes the right column for a proposal, 17/17 and 16/16 rows in view. | Premiere's Editing workspace. |
| 5 | Timeline craft | **9** | The whole key map, ripple/roll/move/razor, tracks, keys, Ctrl+D/Shift+D, each one undo. Against: a transition at an edit with no handles is refused rather than applied with repeated frames. | Premiere's core trim model. |
| 6 | Monitors and playback | **7** | Plain cuts are fine once media exist (≤ 83 ms) and seeks are 74-336 ms. Against: **graded playback runs at 24-27 fps with 70-114 ms canvas holes** at load 10-12; **dissolves hold 83-117 ms**; and **for ~10-20 min after a pass, shots on clips without a preview play black with no message**, with seeks of 1.6-4.4 s. | Premiere plays these GoPro files at ½ res on this GPU with no black frames, and says "media pending" when it has to. |
| 7 | Inspector, effects, colour | **8** | Effect Controls for V1 and V2+, keys with Premiere's delete warning (`92-stopwatch-off-confirm.png`), keys through razor, live 10° rotation in the Program (`45-1920-shot-selected.png`). Against: Lumetri is still Basic Correction only (no curves, wheels, HSL or scopes; `46-1440-lumetri.png`); there is no Effects bin. | Premiere's Effect Controls; a fraction of Lumetri and none of the Effects panel. |
| 8 | Export | **9** | One key on Opacity, Rotation, Volume and a V2 clip all export; the whole 4:41 sequence in 108 s, exact length, CFR, dissolve + overlay + keys rendered, size −15 %; the failure UI is covered by the gate's test. Against: Escape leaves the finished dialog open; I could not drive a failure myself. | Premiere's Export Settings, minus codec detail. |
| 9 | Looks like a shipped pro tool | **8** | The 1440/1920 screens read as Premiere (`45-1920-shot-selected.png`, gate `screens/*.png`, the delete-keyframes modal). Against: a `0…` ghost label; V1 blocks read "CLI…"/"CL…" at fit zoom; the proxies toast parks over the Timeline on every load for an hour; a large ▶ sits over the Program picture. | Close. |
| 10 | Responsive, reliable | **7** | Edits are fast (clicks 25-79 ms, one PUT per Accept), a reload shows the cut in 0.3 s, a hard stop leaves nothing running. Against: **a black, wordless Program is a dead end** right after the pass (`51-clip09-building-full.png`); `GET /media/proxy` holds 20 s before a 503; `/api/flow`, `/api/status` and `/api/firstpass` take 0.5-1.5 s and `/api/peaks` up to 10.4 s while an export and proxies run (`server.log`); Ctrl+M 138-193 ms. | Premiere never shows a black frame with no word. |
| 11 | Proposals are reviewable edits | **9** | Ghosts in V1, ✓/✗ per shot, 15/17 applied exactly, why on hover, checks with clickable ranges, rejections remembered through the re-run, "free on your plan". Against: one `0…` label. | Premiere-like review. |
| 12 | The journey | **9** | 20 steps green (49 s), now with the one-key-then-export step and the keyed shot's frame checked in the file. Against: its 3-clip fixture can never see the black-before-preview state or the canvas stutter. | n/a |

## Section K
- **K1 "not clear how to re run the first pass": RESOLVED on real footage** (as in verdicts 3-7): one header control in every state, 2 clicks, Stop in 71 ms, a re-run that says what it keeps and keeps it. Karl to confirm.
- **K2 "not premier pro quality … complete redesign": OPEN.** Both verdict-7 export and boot blockers are gone, so a Premiere editor could now cut, key, dissolve and export a real film here. They would still hit three things. Right after the AI pass, half the cut plays black with no explanation. Coloured shots and dissolves stutter at playback. Colour and effects stop at Lumetri Basic, with no Effects bin. Karl decides K2.

## Section S
- **Inference only through `roughcut.inference`, no model-ID literal outside `config.py`: intact.** A grep over the snapshot's `.py/.js/.html` outside `tests/` and `config.py` finds no `claude-*` model ID; `import anthropic` appears only at `roughcut/inference.py:592`; gate hygiene is clean.
- **No price reads as money spent on `claude_cli`: intact.** "free on your plan" everywhere; `charged: false`; `cli.js:163-169` says "spent" only when the backend bills.
- **Karl's footage and work dirs never written: intact.** All writes are under `~/roughcut-loop/judge8/`; port 8765 untouched.
- **INTAKE decisions:** I saw no violation.
- **No Co-Authored-By; a CHANGELOG entry on every commit: intact.** `273b1f1..1e99342`: 0 Co-Authored-By lines; every non-merge commit touches CHANGELOG.md.

## Blockers
1. **K2 is open.**
2. **A silent black Program after the first pass (a stuck state in the driven session).** On a shot whose clip's preview is still building, the Program shows plain black for ≥ 11.5 s with `#monWait` hidden, and Play gives 0 frames. `GET /media/proxy/<clip>.mp4` waits 20 s before its 503, and only that 503 starts `mediaWaitFor` (`app/static/app.js:1445-1470`). On Killington this hit CLIP_04/07/09 shots of the accepted cut for ~9 min after the pass (`51-clip09-building-full.png`, `edit-1440.log` cuts 3-4, `probe8` CLIP_07/09: 0 frames).
3. **Dimensions 6 and 10 are below 8.**

## THE THREE BIGGEST GAPS TO PREMIERE
1. **Right after the AI pass, parts of the cut play black and nothing says why.** Accept the proposed cut, press play, and some shots show a black screen for many seconds while their previews are still being made in the background. Premiere would show the frame or "media pending".
2. **Coloured shots and dissolves stutter.** Any shot with a grade (and Killington's auto-balance grades most of them) plays at about 25 fps with visible hitches of 70-115 ms. Dissolves still hitch for up to about a tenth of a second.
3. **The finishing tools are thin.** Colour is Lumetri's Basic panel only (no curves, colour wheels, HSL or scopes). The Effects tab is an AI effect designer, not a bin of transitions and effects to drag on. A dissolve is refused on an edit with no spare frames, where Premiere would apply it anyway and warn.

## PUNCH LIST (most important first; [files] = lane ownership)

1. **The Program is never black without a word, and the accepted cut plays as soon as it is accepted.**
   - **Files:** [`app/server.py`] `media_fallback` / `/media/proxy`: answer at once (≤ 1 s) with 503 + `Retry-After` (or 307 to the original) instead of holding 20 s. After Accept, put the accepted cut's clips first in the preview queue, in film order. [`app/static/app.js`] `arm` / `mediaWaitFor` / `monWait`: when the live buffer has no frame 300 ms after a seek or a cut, show the poster at the playhead and "preparing preview… CLIP_09 · ~N s". Play skips such a shot's time on the poster with that line, never sits on black. [`app/static/project.js` or the bin]: the Status column says "preview building" for such a clip.
   - **Done =** a new test with a clip's media patched to "building": a seek onto it shows the poster and the wait line within 300 ms; Play over it advances the playhead with the line shown and reaches the next shot; `/media/proxy` answers in ≤ 1 s. On Killington: from Accept, every shot of the cut shows a picture or a wait line within 300 ms of a seek, and the whole cut plays (preview quality) within 3 min of Accept at load ~12.
2. **Measure and fix what the viewer sees on a graded shot.**
   - **Files:** [`app/tests/test_perf_latency.py`, `tools/gate.py`]: record `grade.draws` per animation frame while the canvas is `.on`, on a graded shot across both cuts. The longest canvas gap goes into the perf record and the gate line with the same 67 ms limit. [`app/static/grade.js`] (worker post/commit, Auto playback width) and [`app/static/app.js`] (no extra decode on the main thread while the canvas plays).
   - **Done =** the perf test fails today's rev on the canvas (my numbers: 24-27 fps, holes of 70-114 ms at load 10-12) and passes after the fix. On Killington with auto-balance on, a 3 s play across a graded cut at 1440 and 1920 has no canvas gap > 67 ms and ≥ 29 canvas fps.
3. **No hitch through a dissolve (carried; TXHITCH got it to 83-117 ms).**
   - **Files:** [`app/static/app.js` transition followers, `app/static/grade.js` mix]: defer the next shot's park until the dissolve ends, and hand the preroll buffer to the incoming follower (the lead's own queued items).
   - **Done =** `test_tx_playback` asserts the longest gap of *each* follower inside the mix window is ≤ 67 ms. On Killington, 5 plays through a 1 s Cross Dissolve at load ~12 show no follower or live gap > 67 ms.
4. **Lumetri beyond Basic (carried from verdict 7, not attempted).**
   - **Files:** [`app/static/effects.js`, `app/static/grade.js` shader, `roughcut/colour.py`, `research/tools/assemble.py` `lut_vf`]: Lumetri › Curves (RGB master + R/G/B), › Color Wheels (shadows/midtones/highlights), › HSL Secondary (one key), and a Lumetri Scopes tab (waveform + vectorscope of the Program frame).
   - **Done =** a curve point and a wheel push move `grade.probe()` within 150 ms and the exported frame by the same amount (±3 %); the waveform of a solid-grey fixture is a flat line at its IRE; one undo per drag.
5. **An Effects bin, as Premiere has (carried from verdict 7, not attempted).**
   - **Files:** [`app/static/dock.js` + `index.html` tabs, `app/static/timeline.js` drop on an edit/clip end, `roughcut/tracks.py` + `research/tools/assemble.py` kinds]: rename today's tab "Designed Effects" (or move it under Effects). Add an Effects tab with Video Transitions › Dissolve (Cross Dissolve, Dip to Black, Dip to White, Film Dissolve) and Audio Transitions › Crossfade (Constant Power, Constant Gain, Exponential Fade), dragged onto an edit or a clip end. An edit with no handles takes the transition with repeated frames and a warning, as Premiere does, instead of refusing it.
   - **Done =** dragging Dip to Black onto an edit makes a transition whose Program and export midpoint has luma ≤ 5 %; a fade at the first clip starts at black; Ctrl+D on a handle-less edit makes a transition and toasts "insufficient media — repeated frames".
6. **Labels never ellipsize on real footage.**
   - **Files:** [`app/static/timeline-lanes.js` `ghostLabel` / `textW`, `app/static/timeline.js` V1 block labels]: measure with the font the label actually renders in (or measure the DOM once per zoom); give V1 blocks the same clip-number fallback ("02", never "CLI…").
   - **Done =** a test using the board's real font stack at fit zoom on a 17-shot / 2:56 proposal (Killington's lengths) finds no `.gtxt` or V1 label whose text ends in "…" or overflows. On Killington at 1440 no ghost reads `0…`.
7. **The proxies toast shows once per pass, not once per page load.**
   - **Files:** [`app/static/app.js` toast, per-project state]: remember per project and pass that it was shown (server-side or keyed by pass id); never place a toast over the Timeline tracks (anchor it bottom-left of the Program or in the job pill).
   - **Done =** a test reloads 3 times during one pass's proxy phase and sees the toast once; its rect never intersects `#tl .tl-lane`.
8. **The Export dialog's small things.**
   - **Files:** [`app/static/export.js`]: Escape closes the finished dialog (today it stays open after "Exported"; `:577` intends otherwise); Ctrl+M shows the dialog within 100 ms (138-193 ms today: build it once, then fill it).
   - **Done =** `test_export_dialog`: Escape after export-done closes it; Ctrl+M → visible ≤ 100 ms median of 5 at the suite's load.
9. **No flaky tests in the gate.**
   - **Files:** [`app/tests/test_floor_ui.py`, `app/tests/test_perf_latency.py::test_the_grade_draws_in_a_worker_shows_what_probe_reads_and_prefetches`]: find the race (the lead's gate saw both fail in the suite and pass alone).
   - **Done =** each passes 20 of 20 runs inside the full suite order on a loaded box (`pytest --count` or a loop); the gate reports 0 flaky three gates running.
10. **Status endpoints stay cheap under load.**
    - **Files:** [`app/server.py`] `/api/flow`, `/api/status`, `/api/firstpass` (0.5-1.5 s while an export and proxies run at load ~19) and `/api/peaks` (up to 10.4 s): serve from a cached snapshot updated by the jobs, and peaks from a file written when the audio is heard.
    - **Done =** a test that runs an export and a proxy build and polls each endpoint 20× gets p95 ≤ 150 ms (peaks ≤ 300 ms); the journey's slowest-request line stays under 0.5 s.

## What I could not verify
- **The failed-export UI** (shot name, Go to shot, Show log) on a real failure: I found no user action that fails a part, so I take it from the gate's `test_a_failed_export_names_the_shot_and_goes_to_it` and the code.
- **What the Program shows after the server's 20 s hold ends** (whether the poster + "preparing preview…" then appears): the preview landed during my second, longer probe.
- **Audio by ear** (meters −36 to −5 dBFS while playing stand in).
- **Seek misses:** my rVFC seek probe fires no callback when the parked buffer already shows the frame. Six of the 12 seeks in `edit-1440.log`/`probe8` read "None" for that reason, not because nothing showed; the `pb8` seeks (12 of 12 measured) are the numbers I used.
- **Undo of a dissolve:** in `pb8`, Ctrl+Z after Ctrl+D left the transition in place. The dissolve had already been made by my aborted first `pb8` run, so that undo removed a grade instead. I did not isolate it.
