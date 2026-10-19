VERDICT: FAIL

# Roughcut redesign: verdict 11

- **Rev judged:** `18944d0`, the branch HEAD (wave 14 merged, 47 commits since verdict 10's `9470064`). The gate archived it to `~/roughcut-loop/snap-18944d0`. I served that snapshot on 127.0.0.1:8781 four times:
  - **Killington:** `--footage ~/footage/killington-neutral --work judge11/work` (12 GoPro clips), restarted once on the same work dir for the playback probe.
  - **The K3 walk:** an empty project in a fresh browser (`empty`/`work2`).
  - **Fresh-import playback:** a fresh 12-clip Killington work dir (`work3`).
- **Karl's live board:** `35cb147`, an ancestor of `18944d0`, **410 commits behind**.
  - Ports 8765/8766 untouched: same pids as at the start (1902724 started Oct 15 00:59; 3924990 started **Oct 16 00:05**). The redesign preview on 8766 is still the pre-wave-12 server process serving this worktree's newer static files (verdict 10's note stands).
- **Box load (6 cores):** 4.5-5 at the cold land; 6.2-11.6 (mean 8.0) during the first pass with my gate's suite beside it; 4.8-10 for the asks; 2.0-3.6 for the Killington playback probe; 3-4 for the fresh-import play; the perf runs measured at load 5.9-10.1 (their own fixture encodes).
- **Model spend:** backend `claude_cli`, `charged: false`, nothing billed. One 12-clip first pass (Start, Stop, Re-run) and one re-run; 10 asks on Killington; one hand-designed effect; on the empty project one 3-clip pass and 2 asks.
- **Cleanup:** all my servers stopped (`pgrep judge11/work` empty, 8781 free). `~/footage` listing byte-identical before/after (`footage-before.txt` = `footage-after.txt`). The only repo write is this file, uncommitted.

## Gate: my own run, `~/roughcut-loop/gate-18944d0-20261019T0523/summary.md`: **GREEN**, 2711 s

| check | result |
|---|---|
| browser | ✓ Playwright 1.64.0 / Chromium 156 |
| suite | ✓ 1720 passed, 1 failed in the full run, 1 skipped, **1 flaky** (`test_media_first::test_a_video_element_has_a_frame_within_five_seconds_with_no_proxies`; it failed at ~05:48 while my import/re-run drove the box at load ~8-9 and passed alone), 0 broken. **Perf:** before the suite **FAILED** at load 7.5 (only the 1920 graded canvas: 68.6 ms by the page clock; 66.7 by the frame clock, 71.5 by the worker's); after the suite **passed** at load 5.8 (live 50.0, canvas 54.7 / 62.5 ms, seek median 79.6 ms). The after run is the better-conditioned one, so it counted. Both `test_tx_playback` dissolve tests passed. |
| screens | ✓ 7 PNGs, 0 page errors: / 109·57, /floor 52·7, /open 55·8, / shot 138·72, / film 117·56, / jobs 118·58, / fx 143·60 (the logged budgets) |
| journey | ✓ 24 steps · 53 s · slowest request 0.10 s; `journey.json` `retried: []`, `amber: null` |
| boot | ✓ the cut on screen 1.46 s after a proxy lands (limit 2 s; verdict 10 read 0.64 s) |
| hygiene | ✓ clean |

## Rubric changes log: rulings on the entries since verdict 10

1. **2026-10-18, the journey's crash retry now makes the gate AMBER. Honest.** `tools/gate.py:129-171` (`journey_retried`, `journey_read` returns amber when a green journey has a retried step) and `tools/journey.py:2223-2237`; `app/tests/test_gate_status.py:157-220` pins green-without-retry, amber-and-named, two retries named, a failed retry RED, and an old record read from its line. It did not fire today. The no-D-Bus note was already ruled honest.
2. **2026-10-19, SERVERPERF's request timing. Honest, with one hole to close.**
   - **Body wait excluded** (`server.py:1971-2010`, `body_ms`/`wall_ms` kept): honest for what it says. I re-checked verdict 10's "110 MB at ~1.25 MB/s": `judge10/import.py:79-88` loops up to 90 s waiting for an `<img>` in a List-view row, which has none, so the 88 s was my predecessor's own wait. Today the same 110 MB drop read "Imported 1 clip" **0.42 s** after the drop on Killington mid-index, and 2.7 s on the fresh project. Verdict 10's slow-upload finding was wrong.
   - **The hole:** `waited` counts every await on the body, and that includes time the loop itself was too busy to hand over chunks. The loop watcher still sees those holds, but the gate's journey line bounds only `slowest_api` (`gate.py:157-166`), never `slowest_loop`. So a loop hold during an upload no longer shows in any gate line. **Ask:** print `slowest_loop` on the journey line and turn it RED over the logged 200 ms budget.
   - **50 ms loop sampling** (`LOOP_TICK_S = 0.05`): stricter. Honest.
   - **`budget` / `over_budget` fields:** informational only; no gate reads them. Honest as logged.

### The perf bar, ruled (the lead asked; the bar is unchanged)
- **What the test reads.**
  - The live buffer is read by `requestVideoFrameCallback`'s `now`, which is vsync-aligned.
  - The graded canvas is read by `performance.now()` inside each rAF callback (the page clock). The frame clock (rAF's own timestamp) and the worker's draw clock are recorded but not asserted.
  - The page clock adds callback jitter. A 4-vsync hold, which is exactly two 29.97 frame periods (66.7 ms) and so allowed by the bar, read 68.6 / 72.1 / 74.7 / 77.9 ms by the page clock in my runs.
- **What happens in practice.**
  - I ran `test_latency_under_load` alone four times back to back on the quiet box (`judge11/perf/perf-1..4.json`, load_pre 5.9 / 8.8 / 8.9 / 10.1, 0 burners). It passed **1 of 4**, and every failure was the **1920 graded canvas**:

    | run | page clock | frame clock | worker clock |
    |---|---|---|---|
    | 1 | 72.1 | 66.7 | **78.4** |
    | 2 | 74.7 | 66.7 | **70.0** |
    | 4 | 84.2 | **83.3** | 53.6 |

  - So each failing run is over 67 ms by at least one non-page clock too. The page clock overstates the holds, but they are not all measurement noise. The lead's A/B (HEAD 1/3 vs `69fb0f0` 2/3) agrees: it is a coin flip at the test's own load of 6-10.
  - On Killington at load 2-3 (`pb11.log`, per rAF on both clocks plus rVFC `expectedDisplayTime`), plain cuts at 1440 and 1920 had **0 of 8** frame-clock holds over 67 ms. Cross Dissolves (0.4 s and 1.0 s, 24 plays) had **1 of 24** (83.3 ms), with rVFC `now` 2 of 24 and `expectedDisplayTime` 0 of 24.
- **Ruling.**
  - The gate's GREEN is honest under the logged rules (the counted run passed at 5.8), but it was lucky in which run counted.
  - The bar is met on a quiet box and is marginal at load 6-10 on the 1920 graded canvas.
  - The test does not measure exactly what a viewer sees. Assert the canvas on the frame clock **and** the worker clock, and require 3/3 back-to-back passes (punch 3). That is stricter than now, not more lenient. It needs a rubric-log entry first.

## Driving it like an editor

**Cold land (`cold.log`, `01-cold-5s-1440/1920.png`).**
- Rows in 0.13-0.29 s and a picture in 0.15-0.29 s. One blue app action, "Run first pass", and 0 page errors. The one console error is a 503 for CLIP_01's preview, which is still building.
- Premiere's Editing arrangement: Source | Program on top; Project | Timeline | Effect Controls below; the AI prompt in the Timeline header.
- The tour callout "1 of 6 · Bring in footage" covers the Source's "preparing preview…" label (it reads "eparing preview…").

**First pass on Killington (`pass.log`).**
- The panel opens in 73 ms. **Stop took 108 ms** (verdict 10: 625 ms) and the label became "Re-run first pass", including after a reload.
- The timed run took **483 s** at load 6-12 and proposed 18 shots (2:54.8) with "Things to check".
- One progress readout while it runs ("First pass · 4/12"; verdict 10 had three).

**Review (`review.log`, `31-proposal-1440.png`).**
- 18 ghosts on the Proposal track and 18 amber pick markers whose hovers carry the why.
- I rejected 2 (ghost ✗ and table ✗). "Accept 16 of 18" landed exactly what was proposed minus the rejections, with 2 PUTs.
- The sequence took its first clip's rate (59.94) and kept it (see TIMEBASE below).
- A whole-clip drag to the timeline landed in 68 ms, and its grade survived a reload. A hand ripple trim stayed on the frame grid.

**Timebase (`tb.log`, `46-sequence-settings.png`).**
- The cut held 10 29.97 shots and 7 59.94 shots at 59.94 fps, so the old most-common rule would have said 29.97.
- 5 more 59.94 inserts, then undoing them, left `tl.fps()` and `/api/project` at 59.94 throughout.
- Every boundary × 59.94 is whole to within 0.017 frames (4-decimal rounding).
- Sequence › Sequence Settings… opens a Timebase dialog.
- On the empty project no frame rate is shown anywhere.

**Editing (`edit-1440.log`, `vfx.log`, `ctx.log`).**
- These keys all work, each edit one undo: +/−, \, →, ↑/↓, J/K/L, I/O, M, C/V, Ctrl+K, undo/redo.
- These edits all work: ripple trim, roll, move, Selects `,`, double-click pick → Source In/Out, Add/Delete Video Track, a V3 overlay, and overwrite past the end (a Black Video gap).
- Timings at load 6.4-8.7: seeks 94-137 ms; a click → Effect Controls 26-64 ms; keys 45-63 ms (including Playwright's round trip); `effects.set` 7-32 ms; Ctrl+I 41 ms; Ctrl+M 21-26 ms (101 ms the first time).
- **Right-click on 3-5 px blocks now opens the menu** (verdict 10: none under ~15 px).
- **Video effects by hand:** Effects › Video Effects › Transform › Crop dragged onto shot 10 (one undo, "undo: Crop"). Left typed to 10 % in Effect Controls (one undo). The Program shows the band (`76-crop-by-hand.png`), and two Ctrl+Z restore the shot byte-for-byte.

**AI asks on Killington, in my own words, on the real model (`ask.log`):**

| family | words | answer | review | land |
|---|---|---|---|---|
| timeline | "Trim the second shot down to two seconds and swap the third and fourth shots" | 7.4 s: trim + move | ghosts on Proposal; I rejected the move → "Accept 1 of 2" | disk unchanged until Accept; one Ctrl+Z exact; redo exact |
| keyframes | "Slowly push in on the first shot: scale it from 100 to 120 percent…" | 5.9 s: scale keys 100→120 | ◐ After | one undo exact |
| colour (right-click "Ask AI about this…", chip "Shot 6 · CLIP_02") | "This one is too blue and a bit dark…" | 5.0 s: Temperature +25, Exposure +0.15 | ◐; rejected one → "Accept 1 of 2" | one undo exact |
| transition | "Add a one second dip to black between shots 5 and 6" | 9.9 s | "Dip to Black · 1.00 s" ghost on the edit | one undo exact |
| **crop (new)** | "Crop about ten percent off the left edge of this shot" | 5.5 s: `effect` Crop Left 10 % | ◐ After shows the band (`64-crop-after.png`) | one undo exact; **export shows it** (`69c-export-crop-blur-frame.png`: black to x≈120 of 1280) |
| **blur (new)** | "Blur this shot a little, like a soft gaussian blur" | 5.9 s: Gaussian Blur 12 px | ◐ After | one undo exact; exported |
| look | "Give the whole film a warm, teal-and-orange cinematic look" | 17.0 s: look filmic 0.60 + 3 wheels + 2 sets + a match over 14 shots | **◐ Before/After now differs on the Program** (mean pixel diff 6.2) | "AI: 7 colour changes", one undo exact |
| **match (new)** | "Make every shot match the colour of the first shot" | 7.9 s: one `match` op over 20 shots | **◐ previews** (mean diff 30.0) | one undo exact |
| Designed Effects | right-click shot 8: "Put a soft vignette on this shot…" | 8.9 s: `design_effect` (the AI says there is "no Vignette control in the tool set") | goes to Designed Effects; the designer + check finished in **11 s** (verdict 10: 12+ min stuck at 0 %) | **its own check reads "✗ a moment is outside the shot"** (see dimension 15) |
| export | "Export a 720p copy at 30 frames a second that I can send to my phone" | 7.5 s: Quick at 720p, 29.97 | "writes …/phone_720p30.mp4 · 1280×720 · 29.97 fps · Quick · 00:03:21:14" | ran only on Accept, 94 s; `ffprobe` 1280×720, 30000/1001, **6037 frames = 201.4346 s × 29.97 exactly** |

- **Every accept reached the disk:** the EDL on disk equals the client's cut after all 10 (`diskcmp.py`; the only difference is 3-decimal rounding).
- The AI menu lists every AI action.
- Every AI-menu hover card is titled by its own item ("Re-run first pass" now) and says what it does, what it costs and what it changes (`aimenu.log`).
- The keys sheet lists Ctrl+J and Ctrl+I.

**Designed Effects by hand (`fxundo.log`, `79c-fx-designed.png`).**
- Shot 9: "A big white SEND IT title that pops in at the start of this shot…" was designed and checked in **17.3 s**. Its check failed too: **"✗ a moment is outside the shot"**.
- Cause: `roughcut/fx.py:1768` tests `t0 <= ev["t"]` strictly, while the designer anchors "shot start" at the in-point rounded down to 2 decimals (194.59 < 194.5944; 55.6x likewise). Every effect placed "at the start of this shot" fails its own check, with nothing for the user to do about it.
- The accepted vignette does render: export corners darkened 167→141 and 77→42, though the centre also dropped 191→168, against the note's "keep the centre untouched".
- **An accept is not on the undo stack:** after Accept, Edit reads "nothing to undo", and Ctrl+Z leaves the effect `accepted`. Cause: `fx.js:321` pushes an undo entry only when the effect carries timeline `edits`; an overlay-only effect (a title, a vignette) gets none.

**Footage in, mid-session (`import.log`).**
- Ctrl+I opened the dialog in **41 ms**. Importing `judge11/import2` (KX_03 480 MB, KX_07 3.73 GB) gave rows in **0.37-0.48 s** with fps, size, timecode and duration. The clip went into the cut at once with `,`.
- An OS-style drop of a 110 MB file read "Imported 1 clip" at **0.42 s**.
- The offer read: "3 new clips: add them to the cut? · Looking at them: free on your plan · Re-run first pass… · Not now".

**Re-run (`rerun.log`, `73-rerun-proposal.png`).**
- 3 clicks: offer → Re-run → Accept. The run took **117 s**.
- It kept 22 of 22 shots exactly and named each new clip once ("Left out new clip KX_03: its transcript and frames match CLIP_03…"); the doubled "Left out:" is gone.
- **But:** after the AI colour match touched every shot, the panel said **"22 shots edited by hand since … may replace 0"**. Accepting AI grades counted as hand edits, so the re-run could change nothing.
- **Its "Things to check" shows `gen_black_1e0f5341 0:00.0–0:10.0`**, a generated clip's file name. POLISH10's page scan missed it.

**Playback (`pb11.log`, load 2.0-3.6; `edit-1440.log`, load 5.6-8.7):**
- Plain cuts: live and canvas ≤ 66.7 ms on the frame clock in 8 of 8 (one rVFC `now` 83.4 at 1920).
- The 0.4 s shot between two files: 3 of 3 plays with no wait text and no gap over 67 ms.
- Cross Dissolve 0.4 s and 1.0 s, graded, 24 plays: **1 of 24** holds 83.3 ms on the frame clock (verdict 10: 4 of 6 at 83-117 ms).
- Meters move between −38 and −6 dBFS.
- Parked past the end, the Program is black (`79-past-end.png`).

**A just-imported clip in the Program (`freshplay.log`; fresh 12-clip Killington work dir, load 3-4).**
- After import, `,` and a seek: "preparing preview… KX_03" within 100 ms, and the nearest still at 2 s.
- The attempt at +8 s gave no frames; **+11 s played** (verdict 10: +21 s).
- The wait line said **"~60 s"** while it played at ~11 s.
- I did not test it at load ~8.

**Export (`exp.log`).**
- Esc closes the dialog. An In/Out export over shot 8 at 1080p/59.94 gave 239 frames = 3.987 s (In/Out 41.46-45.45).
- The fresh project's 720p/29.97 export gave **435 frames = 14.5145 s × 29.97 exactly**. Its render record carries the AI's accepted colour (verdict 10's lost-colour scenario, now saved).

**The K3 walk: an empty project, a fresh browser, no doc (`fresh.log`, `80-…97-*.png`).**
- **Landing:** every panel names Import, and no frame rate is shown.
- **Import:** the tour's Show me opens Import. Import Folder gave rows at 0.48 s; a 110 MB drop finished in 2.7 s.
- **First pass:** a 3-clip pass ran in 74 s.
- **Why:** on the markers and in Selects. The picks are subclips under each clip (`binsub.log`: 9 under CLIP_02, each with its why).
- **Review:** I rejected one ghost → "Accept 2 of 3".
- **Two asks:**
  - "Cut this down so it's about 15 seconds…" → a 5-shot plan, accepted (14.5 s).
  - "this shot looks flat and grey…" → 4 ops with ◐, accepted.
- **Adjust by hand:** Saturation typed to 80 in Effect Controls. It saved with one undo.
- **Export:** Ctrl+M at 720p.
- **Esc no longer ends the tour** (flag still `shown` after Esc, the tour still visible).
- **I made no guess at any step.** One small fault: the price words were blank before Enter once ("↵ ask ·").

## The 16 dimensions

| # | dimension | score | evidence | vs Premiere Pro |
|---|---|---|---|---|
| 1 | Orientation | **9** | Rows 0.13-0.29 s, a picture 0.15-0.29 s, one blue first action, 0 page errors (`01-cold-5s-*.png`); every empty panel names Import (`80-fresh-empty-land.png`); one readout per job while a pass runs. Against: the tour card clips the Source's "preparing preview…". | Premiere's Editing workspace and "Import media to start". |
| 2 | First pass visible + re-runnable (K1) | **9** | Header control and AI-menu item in every state; Stop 108 ms; pass 483 s; re-run 117 s keeps 22/22 exactly, says what it keeps beforehand, names each new clip; 3 clicks. Against: AI-accepted grades count as "edited by hand", so after an AI match the re-run "may replace 0". | n/a: a clear, re-runnable AI step. |
| 3 | Footage in, no ceremony | **9** | Whole-clip drag lands in 68 ms; Selects `,` exact; an imported clip is cut at once with `,`. | Premiere's bin and three-point editing. |
| 4 | Workspace | **9** | Program 674×379 / Source 645×363 at 1440, vertical meters; panels persist (gate `test_workspace`); Effects bin with Video Effects, Lumetri Scopes. Against: the Assistant review takes the right column and shrinks the Source to 343 px (`64-look-*.png`). | Premiere's Editing workspace. |
| 5 | Timeline craft | **9** | The key map, ripple/roll/move/razor/tracks/gap, each one undo (`edit-1440.log`); **the timebase is kept from the first clip** against a 29.97 majority, Sequence Settings changes it, and edit points sit on the sequence grid (`tb.log`); narrow blocks take a right-click (`ctx.log`). | Premiere's fixed sequence settings. |
| 6 | Monitors and playback | **8** | Seeks 94-137 ms at load 8.7; plain cuts 0/8 frame-clock holds; 0.4 s shot clean 3/3; dissolves 1/24 holds 83 ms (was 4/6); gate perf counted pass at 5.8. Against: **`test_latency_under_load` passes 1 of 4 standalone at its own load 6-10, the 1920 graded canvas holding 70-84 ms by the frame or worker clock**, and the gate's before run failed; a fresh import first played between +8 and +11 s at load 3-4 with a "~60 s" wait line. | Premiere plays these GoPro originals with no held frame. |
| 7 | Inspector, effects, colour | **8** | Effect Controls with keys; Lumetri Basic/Curves/Wheels & Match/HSL/Scopes; **Video Effects: Crop, Transform, Gaussian Blur** in the bin, Effect Controls, Program and export (`76-crop-by-hand.png`, `69c-…png`). Against: three video effects in all; no Lumetri › Vignette, so the AI had to fake one from 5 stacked rings that also darkened the centre 12 %. | Most of Lumetri; a sliver of Premiere's Effects panel. |
| 8 | Export | **9** | Ctrl+M 21-26 ms; Frame Size/Rate/Bitrate; three exports with exact frame counts (6037, 435, 239); the AI export op; Esc closes. Against: no codec or audio settings. | Premiere's Export Settings basics. |
| 9 | Looks like a shipped pro tool | **8** | Verdict 10's blemishes fixed: `#playingWhat` "21/22 · Black Video", AI-menu titles, single job readout, no doubled "Left out", no stuck "0%". Left: **`gen_black_1e0f5341` in the proposal's Things to check** (`73-rerun-proposal.png`); a red "✗ a moment is outside the shot" on correct designed effects (`69b-designed-effects-ai-vignette.png`); the scope chip truncated to "In/Out 00:00:00:00-00:00:0…"; a gap block titled "gap" whose Effect Controls says "Black Video"; "~60 s" on a clip that plays in ~10 s. | Close in layout and density; Premiere shows none of these. |
| 10 | Responsive, reliable | **7** | Clicks 26-64 ms; keys 45-63 ms; every timeline / Effect Controls / AI edit one undo; all 10 AI accepts on disk; uploads instant; the fx check 11-17 s in a child; 1 loop hold all session (692 ms, loop idle, box-starved). Against: **accepting a Designed Effect is not undoable** (Edit "nothing to undo", Ctrl+Z leaves it accepted; `fx.js:321`), which breaks "every edit is one undo"; 24 requests over the server's own 300 ms budget in the Killington session (posters 1.1-2.1 s after the pass, up to 3.9 s for a fresh import; `/api/assist/price` 768 ms; `/api/gap` 587 ms; `/api/firstpass/cancel` 562 ms); a different flaky test in the gate. | Premiere undoes every applied effect and never stalls a request. |
| 11 | Proposals are reviewable edits | **9** | Ghosts on Proposal with ✓/✗; 16/18 applied exactly; why on hover; checks; free on your plan (`31-proposal-1440.png`). | Premiere-like review. |
| 12 | The journey | **9** | 24 steps green in the gate, 53 s, slowest request 0.10 s, no retry (`journey.json`). | n/a |
| 13 | Footage in, from anywhere | **9** | Ctrl+I 41 ms; Media Browser with Import Folder; Upload Files/Folder; Project-panel Import; OS drop 110 MB in 0.42-2.7 s; facts at once; "3 new clips: add them to the cut?"; the re-run names each new clip. Against: a folder drop from the OS is unverified. | Premiere's Import and Media Browser. |
| 14 | One way to ask the AI | **9** | Always-visible prompt; Ctrl+J; scope chip follows a right-click ("Shot 8 · CLIP_07"); "Working … Stop" by my first sample (120 ms); answers in 5.0-17.0 s; AI menu complete; right-click "Ask AI about this…" on any block. Against: the chip truncates an In/Out scope; the price words blank once. | n/a |
| 15 | The AI does everything, as reviewable edits | **8** | 10 families on the real model incl. the new video effects (Crop, Blur) and Color Match; Look/Match now preview; ✓/✗ per op; disk unchanged during review 10/10; one undo exact 8/8; export only on Accept; `test_actions_ui.py` AI-op = hand-op incl. `test_video_effects_crop_transform_and_blur`. Against: the AI's Designed Effects path ends in a check that falsely fails at a shot's start and an accept Ctrl+Z cannot take back; no Lumetri Vignette for the AI to use. | n/a |
| 16 | Easy to learn | **9** | 6-step tour on the real workspace, Show me, Help › Learn; Esc keeps the tour; hover cards with what/cost/changes; empty states; subclips with their why under each clip; my K3 walk had no guess. | Premiere's Project panel with AI on top. |

## Section K
- **K1 "not clear how to re run the first pass": RESOLVED** on real footage, as in verdicts 3-10. Karl to confirm.
- **K2 "not premier pro quality": OPEN (Karl's call).** Verdict 10's list is mostly gone:
  - Gone: the timebase drift, the missing video effects, the stalling effect check, the lost AI edit, most dissolve holds, Look/Match unpreviewed.
  - Left: a Designed Effect you can't Ctrl+Z, a check that cries wolf, playback that is marginal under load, and a just-imported clip waiting ~10 s.
  - Karl has not looked: his preview on 8766 still runs Oct 16's server code.
- **K3 "AI-first, easy to learn, add footage obviously": OPEN (Karl's call), delivered** as far as I can test: the walk had no guess, every pro family is askable and reviewable, and the AI menu is complete.

## Section S
- **Inference only through `roughcut.inference`; no model-ID literal outside `config.py`: intact.**
  - A grep of the snapshot (`.py/.js/.html`, excluding tests and `config.py`) finds no `claude-*` ID.
  - `import anthropic` appears only at `roughcut/inference.py:592`.
  - Gate hygiene is clean.
- **No price reads as money spent on `claude_cli`: intact.** Every control reads "free on your plan" or "no charge on your plan", with the API estimate in the hover.
- **Karl's footage and work dirs never written: intact.** The footage listing is identical before and after; imports referenced `judge11/import2` and `import3`; 8765/8766 were untouched.
- **INTAKE decisions: intact.** The priced look is held for imports until the re-run.
- **The pro controls stay: intact.** Wave 14 only added (Crop/Transform/Blur, Sequence Settings).
- **No Co-Authored-By; CHANGELOG on every commit: intact.** In `9470064..18944d0`: 0 Co-Authored-By lines, and 41 of 41 non-merge commits touch `CHANGELOG.md`.

## Verdict 10's punch list, item by item
1. **The server never stalls: PARTLY DONE (most of it).**
   - Done: the fx check runs in a child process (11-17 s, one job, a real %); uploads are instant; loop holds fell from 145 (≤ 996 ms) to 1 (692 ms, the loop idle and the box starving the process).
   - Not done: the bar "no request > 300 ms during a first pass + an export + a check" fails on Killington at load 6-11, with 24 over (posters up to 2.1 s, `/api/assist/price` 768 ms, `/api/gap` 587 ms, `/api/firstpass/cancel` 562 ms).
2. **An accepted AI edit always reaches the disk: DONE.** 10/10 Killington accepts matched on disk, and the fresh walk's AI colour is in the export's render record.
3. **A fixed timebase: DONE.** `tb.log`; Sequence Settings; exact frame counts in three exports; no fake fps on an empty project.
4. **Put the judged rev in front of Karl: PARTLY DONE.** `judge/FOR-KARL.md` is written. The 8766 preview still runs Oct 16's server code, and Karl's words are not in section K.
5. **No dropped frame through a dissolve: PARTLY DONE (nearly).** 1/24 frame-clock holds (83.3 ms) on Killington at load 2-3, against verdict 10's 4/6. `test_tx_playback`'s short graded dissolve passed in my suite. Ungraded dissolves were not driven (every shot was graded by then).
6. **A just-imported clip plays within 10 s: PARTLY DONE.** It played between +8 and +11 s at load 3-4 (was +21 s), not tested at load ~8, and the wait line says "~60 s".
7. **Look and Color Match preview before Accept: DONE.** The Program differs Before/After (pixel diff 6.2 for the look, 30.0 for the match); tests exist.
8. **Video effects in the Effects bin: DONE.** Crop/Transform/Gaussian Blur work by hand and by the AI, in the Program and the export, each one undo.
9. **Polish leftovers: PARTLY DONE.**
   - Done: `#playingWhat`, "Left out:", the AI-menu card titles, Esc and the tour, narrow right-click, one readout per job.
   - Not done: `gen_black_<hash>` still shows in the proposal's Things to check.
10. **No flaky test: PARTLY DONE.** `test_status_cheap` no longer flakes, but my gate had one new flake (`test_media_first`, under my concurrent load), and the lead's gate on `82d6854` had two.

## Blockers
1. **K2 and K3 are open** (Karl's).
2. **Dimension 10 is below 8**: accepting a Designed Effect is not undoable (`fx.js:321`), against the rubric's "every edit is one undo". With that fixed, and the over-budget requests trimmed, it would be an 8-9.

The gate is GREEN, and every other dimension is at 8 or 9.

## THE THREE BIGGEST GAPS TO PREMIERE
1. **You can't undo an AI-designed effect, and its own check says it's broken when it isn't.** Accept a "SEND IT" title or a vignette, press Ctrl+Z, and nothing happens. Every effect placed at the start of a shot shows a red "✗ a moment is outside the shot", even though it renders fine.
2. **Playback is good on a quiet machine but still flickers under load.** Plain cuts and dissolves are now almost always clean on Killington. But the app's own speed test passes only 1 time in 4 when the machine is moderately busy: on a big screen the coloured picture holds a frame for 70-85 ms. A clip you just imported takes about 10 seconds before it plays, while the screen says "~60 s".
3. **The AI and the editor still trip over each other in small ways.**
   - After the AI colour-matches the film, the re-run treats every shot as "edited by hand" and won't change anything.
   - There is no Lumetri vignette, so the AI fakes one with rings.
   - A generated clip's file name ("gen_black_…") still shows in the AI's notes.
   - Thumbnails can take 1-4 seconds to come back while the machine is busy.

## PUNCH LIST (most important first; [files] = lane ownership)

1. **Put the judged rev in front of Karl (K2, K3: the only way to PASS).**
   - **Files:** ops plus [`judge/FOR-KARL.md`].
   - **Steps:**
     - With Karl's yes, restart `roughcut-redesign` (8766) from a frozen snapshot of the judged rev, not this live worktree. Its process dates from Oct 16 00:05.
     - Update FOR-KARL to this rev and wave 14: Ctrl+J, the AI menu, Effects › Video Effects, Sequence Settings.
   - **Done =** Karl's words on K2 and K3 are recorded in section K.
2. **Designed Effects: an accept is one Ctrl+Z, and the check does not fail an effect at the shot's start.**
   - **Files:** [`app/static/fx.js` accept: always push one undo entry `effect: <name>`, not only when `edits`, so Ctrl+Z calls `/api/fx/undo`]; [`roughcut/fx.py` `verify` in_shot (`:1768`): tolerate half a frame at each end, or snap the designer's events into [in, out)]; [`roughcut/fx.py` / the design prompt's event rounding].
   - **Done =** each of these holds:
     - In a test, accept an overlay-only effect (a title); Ctrl+Z → status `proposed` and the EDL as before; Ctrl+Shift+Z → `accepted`.
     - An effect anchored "at the start of this shot" on a shot whose in-point has 4 decimals passes `in_shot`.
     - On Killington, "put a soft vignette on this shot" ends with a green check.
3. **A perf test that measures what the viewer sees, and passes every time.**
   - **Files:** [`app/tests/test_perf_latency.py`: assert the graded canvas on the frame clock AND the worker clock, both ≤ 67 ms; the live buffer on rVFC `expectedDisplayTime` as well as `now`]; [`app/static/grade.js` worker draw scheduling at 1920 (`PLAY_W` / `MIX_W`)]; [`tools/gate.py` if the counting changes]. Log it in REDESIGN-GOAL first.
   - **Done =** 5 of 5 back-to-back standalone runs pass at the test's own load (load_pre 6-10, as here), and the gate's before and after runs both pass.
4. **The server keeps its 300 ms budget on real footage, and the gate watches the loop.**
   - **Files:** [`app/server.py` `media_poster` (cards on `near=1` / cached; never a 4K decode in the request), `api_assist_price`, `api_gap`, `api_firstpass_cancel`]; [`tools/gate.py` journey line: print `slowest_loop` and turn RED over 200 ms].
   - **Done =** on Killington, a session of first pass + 5 asks + export + import + re-run has `/api/debug/slow` `over_budget` empty, except the declared long-poll `/media/status`.
5. **A just-imported clip plays within 10 s at load ~8, and the wait line tells the truth.**
   - **Files:** [`app/server.py` `want_head` / `HEAD_HINT_WAIT_S` and the ETA in `/media/status`]; [`app/static/app.js` `mediaWaitFor`'s words].
   - **Done =** `judge11/freshplay.py` on a fresh 12-clip work dir at load ~8 plays within 10 s, three runs out of three, and the ETA shown is within 2× of the real wait (`test_preview_building.py` extended).
6. **A re-run can still re-cut after the AI regrades the film.**
   - **Files:** [`roughcut/firstpass.py` manual-edit detection (`manual_edits`, `how`)]; [`app/static/firstpass.js` panel words].
   - **Done =** grades and effects accepted from the AI (or by hand) are kept on whatever shot survives, but do not lock a shot's place in the cut. Only a hand trim, move, razor or insert locks it. The panel says "keeps 3 shots you cut by hand · keeps the grade on every shot it keeps · may replace 19".
   - **Test:** an AI `match` over all shots, then a re-run, may replace shots.
7. **Lumetri › Vignette (and Blur & Sharpen › Sharpen) as Premiere has them.**
   - **Files:** [`app/static/effects.js` a Vignette section in Lumetri (Amount, Midpoint, Roundness, Feather)]; [`app/static/grade.js` shader]; [`research/tools/assemble.py` export]; [`roughcut/actions.py` ops + the AI vocabulary].
   - **Done =** "put a soft vignette on this shot" answers with a Lumetri vignette op, not a designed effect. The exported centre is within ±2 % of the source, and the corners match the Program within ±5 %. The AI-op = hand-op EDL test covers it.
8. **Polish leftovers.**
   - **Files:** [`app/static/actions.js` / `firstpass.js`: the proposal's Things to check names a clip through `rcClipName`]; [`app/static/aiprompt.js`: the scope chip never ellipsizes (say "In/Out 0:41-0:45")]; [`app/static/timeline.js`: the gap block's title says "Black Video"]; [`app/static/aiprompt.js`: the price words before Enter on a fresh project]; [`app/static/dock.js`: the Source keeps ≥ 560 px at 1440 when the Assistant opens].
   - **Done =** each has a test; the page scan includes the Assistant's checks list.
9. **The rest of the dissolve and an ungraded check.**
   - **Files:** [`app/static/app.js` `txFollow` / `txHand`], [`app/static/grade.js` mix].
   - **Done =** `judge11/pb11.py` on Killington at load ≤ 5: 0 of 24 graded dissolve plays and 0 of 12 ungraded ones (auto-balance off) hold > 67 ms on the frame clock.
10. **Zero flaky tests, three gates in a row.**
    - **Files:** [`app/tests/test_media_first.py` (a 5 s frame bar under load: wait on the box, or measure from the request)], and whatever flakes next.
    - **Done =** three consecutive full gates with "0 flaky" on the box as it is.

## What I could not verify
- **A folder dropped from the OS** (headless Chromium cannot fake `webkitGetAsEntry`) and Upload Folder… through the chooser.
- **Fresh-import play at load ~8** (I measured at load 3-4).
- **Ungraded dissolves on Killington:** every shot was graded (auto-balance plus the AI match) by the time I probed.
- **What the viewer's display actually showed:** headless Chromium has no real vsync. Every playback number is a proxy (page, frame, worker or rVFC clock), so I report all of them.
- **Karl's 8766 preview's state** beyond its process start time and working directory; per the brief I sent it no request.
- **The `anthropic_api` backend's cost words** (not run).
- **Audio by ear.** The meters moved between −38 and −6 dBFS.
