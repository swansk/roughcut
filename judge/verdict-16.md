VERDICT: FAIL

# Roughcut redesign: verdict 16

- **Rev judged:** `b2cbc31` (branch HEAD as briefed: wave 19 EFFECTS2 / LOOPHOLD / PLAY10 / WORKER-cap / POLISH13, wave 20 TELEM / LOCALGPU / VERIFY / K5UI / EVAL / FIND3 / DIARIZE / CERTFIX / GATEFIX; 84 commits, 67 non-merge, since verdict 15's commit `2600864`).
  - My gate archived it to `~/roughcut-loop/snap-b2cbc31`. I served only that snapshot, on 127.0.0.1:8781:
    - **Killington:** `--footage ~/footage/killington-neutral --work judge16/work`, `ROUGHCUT_LEDGER=judge16/ledger.jsonl`.
    - **The K3 walk:** `--footage judge16/empty --work judge16/work2`, `ROUGHCUT_LEDGER=judge16/ledger2.jsonl`, a fresh browser with no tour flag.
- **Karl's live board (8765):** `35cb147`, an ancestor of `b2cbc31`, **666 commits behind**. pid 1613, up since Nov 8 14:11. No request sent.
- **Karl's preview (8766):** pid 2170051, started **Nov 9 03:37:56**, cwd the live worktree, so it serves `b2cbc31`'s Python and pages. No request sent. I read its work copy `/mnt/roughcut/work/app-redesign` read-only (the gate's `understanding` check and my own run of the same tool).
  - `judge/FOR-KARL.md` still names `5138865` and says nothing about K5 or K6.
- **Box load (6 cores, RTX 3080):** 3.1-4.7 before I started; **5-19.5 (mean 11.8)** during my first pass (my pass + the 3080 stage + my gate's perf/suite); 7.8-8.6 for editing; 8-11 for the AI asks; 7-15 for import and re-run; 8-12.4 for the playback runs; 7-17.5 during the K3 walk. My gate ran beside all of it. Every timing below carries its load.
- **Model spend:** backend `claude_cli` throughout, nothing billed. Killington: 157 calls, $28.61 API-equivalent (one first pass, one re-run, 6 asks; **76 of them `verify`-role checker calls: 40 in the first pass, 36 in the re-run**, $4.91). K3 walk: 21 calls, $5.77. I started no checker batch myself.
- **Cleanup:** both my servers stopped (8781 free). `footage-before.txt` = `footage-after.txt`. Imports were copies under `judge16/import2`, `import3`, `dropsrc`, `dropsrc2`. **Karl's `~/work/roughcut-ledger.jsonl`: 2154 lines, mtime Nov 8 14:11, before and after.** The only repo write is this file, uncommitted.

## Gate: my own run, `~/roughcut-loop/gate-b2cbc31-20261109T0338/`: **RED, and it never wrote a summary**

| check | result |
|---|---|
| browser | ✓ Playwright 1.64.1 / Chromium 156 |
| perf before the suite | **✗ FAILED at load 7.89** (≤ 2× cores, so RED by the gate's rules). The graded canvas held **83.4 ms on the frame clock at 1920** (limit 67); worker 54.3 ms. My first pass was running at the time. |
| suite | **✗ 2 failed**, 2038 passed, 7 skipped, in **3722.9 s**: `test_vfx_playback::…sharpened_and_a_blurred…` (sharpen 1440: 83.4 ms on the frame clock **at load 2.67**; sharpen 1920: 83-85 ms on both clocks at load 3.3), and `test_cut_frames::test_a_half_speed_shot_changes_picture_every_four_display_frames`. |
| the gate itself | **crashed:** `subprocess.TimeoutExpired` — the suite ran past `gate.py`'s own 3600 s timeout. No perf-after, screens, journey, boot, understanding or hygiene lines, and no `summary.md`. |
| quick gate, same rev, after (`gate-b2cbc31-20261109T0442`, load 1.5-4.4) | ✓ GREEN in 105 s: screens 5 PNGs, budgets exact (`/` 110·58 … `/ fx` 144·61), 0 errors; **journey 24 steps · 58 s · slowest request 0.05 s · loop hold 25 ms**; boot 0.69 s; understanding "matches baseline" (on the preview copy; see the ruling below); hygiene clean. |

- **Alone afterwards** (`alone.log`, 2 rounds at load 3.6 and 6.8), the two failed tests and `test_latency_under_load` passed 3 of 3 both times.
  - So these are the known load- and order-sensitive frame holds, not a new break. But the full gate is RED for the second rev running: the lead's `fb0a251` gate was RED on the same perf test.
- **The lead's GREEN** on `9d44587` ran at normal priority on a quiet box (LOOP.md).
- **My run** shared the box with my own first pass and the 3080 stage for its first 25 minutes. That is the condition Karl will be in when he runs a pass and plays at the same time.

## Rubric changes log: rulings

There is exactly one new entry since verdict 15, and four changes that should have had one.

1. **2026-10-21 POLISH13 (the stale-read join, the List view's whole-column scroll, the keeps lane's name): honest.** Stricter as written; its tests are in the suite my gate ran. I did not drive the List view's sideways scroll or the keeps lane myself this time.
2. **UNLOGGED: the gate's new `understanding` check (wave 20 EVAL). Lenient by construction, and not logged.** A new gate check is a gate change and the log's own rule says it is logged before the next judge. What it measures is not the rev:
   - It scores **Karl's preview work copy's sidecars** (`/mnt/roughcut/work/app-redesign`), produced by whatever revs 8766 ran over the past days, not by the frozen rev. Only the scoring and demotion code are the rev's.
   - Its baseline was written with **0 checker verdicts** on that copy (`"verdicts": 0`; the "Checker-confirmed" layer is empty), so the Claude half of Hybrid is outside what it can see.
   - **When I ran the same tool on my fresh first pass of this rev (`eval-mine/understanding.md`), it reports `REGRESSION vs baseline: recall_high 0.889 -> 0.778; recall_air 0.8 -> 0.6`.** The gate line on the preview copy reads "matches baseline". The check is green while the rev's own pass regresses.
   - It is "skipped (green)" on any machine without that directory.
3. **UNLOGGED: WORKER's Playback Resolution change (Full draws every frame; Auto lifts the 30.5 fps cap once `behind` clears) and `test_playback_resolution`'s new bar. Lenient in two places, not logged.** Verdict 15's punch 5 asked for exactly this and said "the rubric log says which". The test skips its ≥ 55 fps bar above load 6, and its Auto assertion runs only when `behind` was true both before and after. The product change itself is the right one.
4. **UNLOGGED but honest: GATEFIX's `claims_as_found` fixture** (four UI tests run with `verify.DEMOTE = False` because their fixtures fabricate claims with no telemetry). The assertions are unchanged and the demotion has its own tests. But no UI test covers what demotion does to footage that has no telemetry, and that is where I found the worst K6 bug (blocker 4).
5. **LOOPHOLD's proposed bar (judge loop holds by faults per hold rather than raw `slowest_loop`): the lead was right not to adopt it.** A hold is a hold to the editor whatever caused it.

## Driving it like an editor

**Cold land, fresh work dir (`cold.log`, `01-cold-5s-*.png`, load 5.7-6.9).**
- Rows in 0.19-0.25 s and a picture in 0.25-0.30 s at both sizes. 0 page errors.
- At 1440 there was one console error: a 503 on `/media/proxy/CLIP_01.mp4`, because the proxy was not built yet on the fresh work dir.
- The first action is "Run first pass" in the header. The six-step tour shows.
- The AI menu lists every AI action, including "Look again / re-tune the index…", with hover cards.
- **The Index panel (590 px) now has a "Local (3080)" column and a mode select in its foot.** The select reads **"Hybrid (3080 +"**, cut off at 1440 and at 1920. The sentence that explains the mode is ellipsized and only readable in the hover (`06-index-detail-*.png`). GATEFIX moved it there and said "a judge may want it visible": yes.

**First pass, Hybrid (`pass.log`, `11-14*.png`, `13k-*.png`, `account.json`).**
- **2 clicks** (the header, then Run). It took **638 s at load 5-19.5** (mean 11.8). 0 page errors, 0 bad responses.
- **Before it runs (K5),** the panel says: "Will hear every clip with speech to text (every word timed), look at 1 frame every 4 s on contact sheets, then take close looks at the busiest seconds."
  - It names no model, no frame count or size, no telemetry, **no 3080 stage and no Claude check**, although Hybrid runs both.
- **While it runs,** the same sentence repeats with "heard 7/12 · looked at 4/12", and the certainty counts show ("All 134 moments: 74 felt by telemetry · 60 unchecked").
- **After it runs, the account is what K5 asked for:**
  > Ran — heard 12 clips (1,642 words, every word timed, Whisper large-v3) · looked at 1 frame every 4 s (649 frames, 6×5 contact sheets of 320 px frames, Sonnet) · close looks on 1 of 43 min (6 windows, 1 frame/s, 480 px, Opus) · motion tracked 10 times a second.
  >
  > The proposed cut: 19 shots, how sure: 4 checked by Claude · 10 unchecked · 5 contradicted. All 220 moments: 34 felt by telemetry · 3 checked by the 3080 · 23 checked by Claude · 12 seen once · 132 unchecked · 16 contradicted.
  - The account still has no line for the 3080's work: the stage ran 21 min wall for 12 clips, 146 GPU-s of checks plus the PE-Core embeddings, and finished ~11 min **after** the proposal.
  - Nor is there a line for the checker's 40 Claude checks on levelled strips. They appear only as chip counts.
  - "No cut yet — it proposes a first one." stays printed under a waiting proposal (`14k-fp-panel-end.png`).
- **5 of the 19 proposed shots rest on "contradicted" moments**, yet none of the "Things to check" names them. Shot 5's chip reads "contradicted" on a speech line ("Telling the red-helmet friend 'I fell into the stream'"), which an editor cannot interpret (`31-proposal-1440.png`).

**Review (`review.log`).**
- The proposal was visible 0.64 s after load: 19 ghosts with filmstrips, ✓ / ✗, why on hover, a certainty chip per row.
- Rejecting 2 gave "Accept 17 of 19", which landed exactly the proposal minus those 2.
- A drop-then-grade landed in 80 ms and survived a reload.

**Editing (`edit-1440.log`, load 7.5-8.6, 3 of 12 proxies built at the time).**
- Seeks to the first frame: 47-168 ms. Clicks to Effect Controls: 26-47 ms; keys 31-71 ms; `effects.set` 18-36 ms.
- The key map all works: +/−, \, arrows, J/K/L, I/O, M, C/V, Ctrl+K, undo / redo.
- Also working: ripple trim, roll, move, Selects `,` insert, Add / Delete Track, a V3 overlay, and an overwrite past the end making a gap.
- A reload was exact.
- **Plays across cuts:**
  - Cut 3 (CLIP_01 → CLIP_02, load 7.9): **4 gaps over 67 ms, longest 216.7 ms, inside CLIP_01 and not at the switch**, while that clip's proxy was still being built.
  - Cuts 6 and 7: clean.
  - One play across a 0.4 s shot logged a 2.8 s gap. Verdict 15 traced that to the script's own Space press; it is not counted.

**AI asks on Killington, in new words, real model (`ask.log`, load 8-11):**

| family | my words | answer | review | land |
|---|---|---|---|---|
| timeline | "Put the third shot first, and cut half a second off the start of what is then shot 2" | 7.5 s: move + trim (in 1.50 → 2.00) | ✓ / ✗ rows; disk unchanged during review | one Ctrl+Z exact; redo exact |
| keyframes | "Fade shot 1 up from black over its first second" | 13.0 s: said shot 1 is only 0.2 s, so Opacity keys 0→18 % over shot 1 and 20→100 % over shot 2 | 2 rows | one Ctrl+Z exact |
| colour (right-click) | "Give this shot more punch: a bit more contrast and saturation" | 9.9 s: Contrast +20, Saturation 120 | ◐ before/after | one Ctrl+Z exact |
| transition | "Dip to black between shots 6 and 7, about half a second" | 9.5 s: Dip to Black 0.50 s centred | a ghost on the edit (`63-transition-review.png`) | one Ctrl+Z exact |
| speed (right-click) | "Play this one at 50 percent" | 5.9 s: Speed 50 % | row | one Ctrl+Z exact |
| export | "Render me a 720p preview file of the whole thing" | 6.4 s: 720p, Entire Sequence → `preview_720p.mp4` | row | ran only on Accept, 136 s. `ffprobe`: 1280×720, 60000/1001, **13931 frames = 232.4155 s** (sequence 232.42) |

"Working" was on screen by 400 ms each time. My observer missed the exact millisecond; the K3 walk measured **80 ms**.

**Footage in, mid-session (`import.log`, load 15.2).**
- Ctrl+I opened in **88 ms**. Two clips gave rows at **0.29 s** with fps, size and timecode. KX_05 was cut at once with `,` (then undone).
- An OS-style drop of a 480 MB file read "Imported 1 clip" at **1.19 s**.
- The offer read "3 new clips: add them to the cut? Looking at them: free on your plan".

**Re-run (`rerun.log`, `20-rerun-panel-*.png`), load 8-14.**
- **At 1440×900, after manual edits and 3 new clips, the panel's "Re-run first pass" button sits at y 925-953 in a 900 px window.** The panel is 955 px tall, `overflow: visible`, with no max-height and no scroll; a wheel over it moves nothing. Playwright's ordinary click timed out ("element is outside of the viewport").
  - The header control, the AI menu's "Re-run first pass…" and the offer's "Re-run first pass…" all open this same panel.
  - **A Premiere editor on a 1440×900 screen cannot start the re-run in the state Karl's K1 is about** (after manual edits). At 1920×1080 the button is visible.
  - `test_k5_ui`'s `_inside()` check exists, but only for fixture states with short content.
- I started it by a scripted `dispatchEvent` to measure the rest. It took **188 s**. It **kept all 10 hand-edited shots exactly**. It changed one proposal shot's out point (CLIP_10, 35.67 → 35.30) and said so in Things to check. It named why each new clip was left out ("a near-duplicate of CLIP_05 …").

**Playback, after the re-run (`out/pb11-slowA.json`, `out/pb11-pbA.json`), load 8.1-12.4:**
- **Inside the AI's 0.5× shot:** 1 of 8 plays held (83 ms on the frame clock, load 11.2). Verdict 15 read 3 of 8 at load 5-9.
- **Cut into the 0.5× shot, the 0.5 s Dip to Black out of it, and plain cuts 6 and 7:** **0 of 8 plays held over 67 ms**, at 1440 and 1920. Verdict 15 read a 116-133 ms freeze on 4 of 4 plays out of a 0.5× shot. PLAY10 is real progress. The sample is small (1 play per cut per size).
- **Again on a quieter box** (`out/pb11-slowB.json`, `pb11-pbB*.json`, load 3.0-5.7, after my gate's suite):
  - Inside the 0.5× shot: **0 of 8 plays held on the frame clock** (1 of 8 on the live buffer's clock, 83 ms).
  - Cuts into and out of it, including the Dip, and plain cuts 6 and 7, 2 plays each per size: **15 of 16 clean**. The miss was **plain cut 6 at 1920 (CLIP_04 → CLIP_07): 83.3 ms at load 3.8**.
- **My gate's perf test before the suite FAILED at load 7.89** (≤ 2× cores, so RED by the gate's own rules): the graded canvas held **83.4 ms on the frame clock at 1920** (limit 67). My first pass was running at the time.

**The K3 walk: an empty project, a fresh browser, no doc (`fresh.log`, load 7-17.5).**
- Every empty panel names Import; the tour's Show me opened Import. Rows came at 0.27 s, and a dropped file landed at 2.4 s ("Imported" at 4.1 s).
- **First pass: 134 s.** Why on pick markers and Selects rows. I rejected 1 of 5 ghosts and accepted the rest.
- **Asks.**
  - "Cut this down to about 20 seconds and start with the wipeout": a plan in 12.3 s that opens on the burial. Accepted: 6 shots, 22.42 s.
  - "this looks flat and grey, make the snow brighter and a little warmer": Exposure +0.20, Temperature +15, Contrast +12 in 5.9 s.
  - "Working" showed at **80 ms** both times.
- **Export.** 720p / 29.97: **672 frames = 22.42 s** (sequence 22.4225).
- **Guesses: none.**
- **The no-telemetry fall (my test of K6 on ordinary footage).** KX_DROP2 is CLIP_01 55-100 s re-muxed without its GPMF track, the way a phone or DSLR clip arrives.
  - The close look described the tumble correctly: "The wearer falls into deep snow. At 14 the trees are steeply tilted…". That is the moment Karl confirmed as `fall-01-68`.
  - **But both fall claims went to `events_unsupported` and off the board's events.**
  - The server offers "Check 5 claims" (`GET /api/verify`, `claims_to_check: 5`), but **no page calls `/api/verify/claims` or `/api/verify/events`**.
  - **The AI menu reads "Check the pass's claims… nothing to check [off] «every hot claim has had a close look»"** (`90-fresh-ai-menu-claims.png`): that item still drives the old R10 `/api/visual/audit`.
  - On any footage without telemetry, every jump / fall / crash the index sees is held back, and the app tells the editor there is nothing to check.

## Footage understanding (K6), measured on Karl's test set

The bake-off's design is built and runs in the app on the default Hybrid mode:
- telemetry events: 74 felt events on the bin;
- the 3080 stage: Qwen3.5-4B checks on levelled frames and PE-Core embeddings;
- Claude checks on levelled 8 fps strips: 40 in a first pass, the WAVE20 bar's maximum;
- Find, as the lexical + PE-Core union.

The per-bin mode select works and says what each mode costs.

| | recall (high, 9) | Karl-confirmed (5) | air (5) | fall (4) | negatives asserted | false / neg-min | other riders' air (Karl, 11-08) |
|---|---|---|---|---|---|---|---|
| baseline / Karl's preview copy (gate line) | 0.89 | 0.60 | 0.80 | 1.00 | 3 of 19 | 1.17 | **0 of 1** |
| **my fresh first pass of this rev** (`eval-mine`) | **0.78** | 0.60 | **0.60** | 1.00 | 1 of 19 | 1.17 | **0 of 1** |

- **Fewer false claims, fewer real airs.** Negatives asserted fell from 3 to 1.
- **But Claude's checker rejected real, telemetry-backed airs.** `CLIP_05:air:17.97` (labelled high, free fall then landing) was rejected: "The terrain is flat and continuous …, no crest". It rejected the identical copy `KX_05` the same way. With that, Hybrid scores **below telemetry alone** on airs (felt 0.80, asserted 0.60).
- **Karl's 2026-11-08 answer, "other riders' airs count", is not delivered.**
  - CLIP_10's skier over the camera is missed by every layer, and CLIP_10 got 0 local checks.
  - WAVE20 says the frames should find others' airs, but no stage looks for an air that telemetry did not flag.
- Find + 3080: 0.79 on the preview copy (above the 0.75 bar). On my work dir that line skipped, because the 3 imported clips were not embedded yet.

## The 16 dimensions

| # | dimension | score | evidence | vs Premiere Pro |
|---|---|---|---|---|
| 1 | Orientation | **9** | Rows 0.19-0.25 s, picture 0.25-0.30 s at both sizes; 0 page errors; one first action; every empty panel names Import (fresh walk). Against: a 503 console error for an unbuilt proxy on a fresh bin. | Premiere's one workspace, matched. |
| 2 | First pass visible + re-runnable (K1) | **5** | 2 clicks to run; the K5 account after a run is clear and numeric. **Against: at 1440×900 after manual edits + new clips, Re-run first pass is at y 925-953 in a 900 px window inside an unscrollable panel (`20-rerun-panel-1440.png`): not reachable by an editor**; the account names neither the 3080 stage nor the Claude checks; while running it names no model or frame counts. | n/a; Karl's own K1 state is broken on a laptop-size screen. |
| 3 | Footage in, no ceremony | **9** | Drag + grade 80 ms; `,` insert from Selects; an imported clip cut at once. | Premiere's bin and three-point editing. |
| 4 | Workspace | **9** | Program 674×379 at 1440; Index 590 px with 10 rows and a detail open at 1440, 12 at 1920; panels unchanged. | Premiere's Editing workspace. |
| 5 | Timeline craft | **9** | Full key map, trim, roll, move, razor, tracks, overwrite past the end, V3; each one undo; a reload exact (`edit-1440.log`). | Matched for the basics. |
| 6 | Monitors and playback | **7** | Better: verdict 15's slow-motion freezes are gone. Cut into / Dip out of a 0.5× shot held 0 of 12 plays at load 3-10; inside 0.5× held 0 of 8 at load 3-4.7 and 1 of 8 at load 9-12; plain cuts 15 of 16 clean on a quiet box. Against: **the full gate is RED on frame holds**: perf before the suite at load 7.9 (83.4 ms frame clock, 1920); `test_vfx_playback` sharpen 83 ms **at load 2.7**; the half-speed test; a plain cut held 83 ms at load 3.8; a 216 ms hold inside CLIP_01 while its proxy built; every bin re-proxies for ~20 min on first open. | Premiere plays through a busy machine without a hitch. |
| 7 | Inspector, effects, colour | **8** | AI and hand Lumetri values land in Effect Controls (Contrast, Saturation, Exposure, Temperature); EFFECTS2 adds Crop, Transform, the flips, Mirror, Black & White with keyframes and an export frame compare in the suite (`test_crop_transform`, `test_more_video_effects`: green in my gate's suite). I did not drive the new effects by hand. | Lumetri basics plus Premiere's most-used Transform effects. |
| 8 | Export | **9** | 13931 frames = 232.4155 s vs 232.42 (720p59.94); 672 frames = 22.42 s (720p29.97); the AI export ran only on Accept. | Premiere's Export Settings basics. |
| 9 | Looks like a shipped pro tool | **7** | Against: the first-pass panel runs off the bottom of a 1440×900 window; the Index foot's mode select reads "Hybrid (3080 +" at both sizes and its explanation is hover-only; the Local column shows a bare "10:28" (a running clock) on every row; "No cut yet — it proposes a first one." under a waiting proposal; "contradicted" chips on speech lines with no explanation. | Premiere never clips its own dialogs. |
| 10 | Responsive, reliable | **8** | Clicks 26-47 ms, keys 31-71 ms, Ctrl+I 88 ms; every edit and AI accept one undo; reload exact. Against: the new `GET /api/firstpass/account` was over the 300 ms budget 6 times (320-508 ms, "server") and a 232 ms loop hold opening a telemetry sidecar (`slow-final.json`, load 13). | Premiere's UI never waits on its own bookkeeping. |
| 11 | Proposals are reviewable edits | **8** | 19 ghosts with ✓ / ✗ and a certainty chip each; 17 of 19 exact; why on hover; the re-run's diff and why each new clip was left out. Against: 5 of 19 proposed shots rest on "contradicted" moments and Things to check names none of them. | Premiere-like review. |
| 12 | The journey | **9** | Quick gate: 24 steps green, 58 s, slowest request 0.05 s, loop hold 25 ms, no retry. The full gate never reached the journey (it crashed on its suite timeout). | n/a |
| 13 | Footage in, from anywhere | **9** | Ctrl+I 88 ms; rows 0.27-0.29 s; Media Browser; drop of 480 MB "Imported" at 1.19 s; free stages started on their own; "3 new clips" offer; the re-run says why each was left out. | Premiere's Import and Media Browser. |
| 14 | One way to ask the AI | **8** | Always-visible prompt, Ctrl+J, scope chip follows right-click; "working" by 80 ms; answers 5.9-13 s. Against: **the AI menu's "Check the pass's claims…" says "nothing to check" while the server holds 5 unchecked claims, including a real fall**; the checker's own calls have no menu item. | n/a |
| 15 | The AI does everything, as reviewable edits | **9** | Six families on the real model in new words (move+trim, Opacity keys, Contrast+Saturation, Dip to Black, speed, export), each previewed, ✓ / ✗ per op, disk unchanged until Accept, one Ctrl+Z exact 5 of 5, redo exact; export ffprobe exact. | n/a |
| 16 | Easy to learn | **8** | 6-step tour on the real workspace; my K3 walk had no guess. Against: K5's "what is it doing" while the pass runs omits the models, the frame counts, the 3080 and the checker; the Index mode's explanation is hover-only and its name is cut off. | Premiere's Project and Metadata panels, with AI on top. |

## Section K
- **K1 "not clear how to re run the first pass": REGRESSED on this rev.** At 1440×900, in the after-manual-edits state, the re-run button is off-screen in an unscrollable panel (blocker 2). At 1920×1080 it works: 3 clicks, 10 of 10 edited shots kept exactly.
- **K2 "not premier pro quality": OPEN (Karl's call).**
- **K3 "AI-first, easy to learn, add footage obviously": OPEN (Karl's call).** It is delivered as far as I can test: my K3 walk had no guess.
- **K4 (the Index panel; the old pages folded in): OPEN (Karl's call).** Still delivered. The Index panel gains the Local (3080) column and the mode select, but the select is cut off.
- **K5 "what it is doing, at what granularity": OPEN.** It is partly delivered:
  - **After a run:** the account has words and numbers for hearing (Whisper large-v3, 1,642 words), looking (649 frames, 6×5 sheets of 320 px frames, Sonnet), close looks (1 of 43 min, 6 windows, 1 frame/s, 480 px, Opus), telemetry (10 Hz), and certainty for the cut and the bin. "This run heard and looked at the footage first…" / re-rank-only wording exists (`test_k5_ui`).
  - **While it runs:** the same plan sentence, with no model, frame count or size.
  - **Nowhere:** what the 3080 did (windows checked, frames embedded, which model), or what Claude's checks did (how many, on what, confirmed / rejected).
- **K6 "both the 3080 and the Claude approach in the app, the best approach on the test set": OPEN.** It is built and wired: Hybrid by default, per-bin mode, local stage, 40 checker calls, Find union 0.79. Four things are not delivered:
  - (a) **The rev's own fresh pass scores below the gate's baseline on the test set** (recall 0.89 → 0.78, airs 0.80 → 0.60), because the checker rejects a real telemetry air. Karl said "the approach that wins is the one that scores best on it", and on airs Hybrid now scores below telemetry alone.
  - (b) **Other riders' airs (Karl's 11-08 answer): 0 of 1.**
  - (c) **On footage without telemetry, real falls are demoted and cannot be checked from the UI**, while the AI menu says there is nothing to check.
  - (d) **Deploy:** 8766 runs `b2cbc31` from the live worktree, so the K6 deploy answer is met. But FOR-KARL is stale (it names `5138865`), and Karl has not been told what to try.

## Section S
- **Inference only through `roughcut.inference`; no model-ID literal outside `config.py`: intact.** The checker and Find rerank use the `verify` role (`config.ROLE_VERIFY`); `roughcut/localvlm.py` runs local models in a subprocess, not through inference, which is correct for a free local stage. Quick gate hygiene clean; my grep of the snapshot (excluding tests and `config.py`) finds no `claude-*` ID; `import anthropic` only at `roughcut/inference.py:610`. **To rule on:** the local models' IDs (`Qwen/Qwen3.5-4B`, `timm/PE-Core-L-14-336`, `qwen35-4b-bnb4`) are literals in `localgpu/rcgpu/config.py:7-9` and `roughcut/localvlm.py`. They are not inference and S was written for Claude IDs, so this is not ruled broken. The lead should either move them to `roughcut/config.py` or write the exception into S.
- **No price reads as money spent on `claude_cli`: intact.** Every hover reads "free on your plan" with the API estimate after it ("~$11.18 if it went through the API — no charge on your plan"); all 178 ledger rows are `claude_cli`.
- **Karl's footage and work dirs never written: intact.** Footage listings identical; `~/work/roughcut-ledger.jsonl` 2154 lines, mtime unchanged. The gate's understanding check and my eval only read `/mnt/roughcut/work/app-redesign` (the tool writes only to `--out`).
- **INTAKE decisions: intact.** Imported clips were heard and proxied free; nothing priced ran unasked (on `claude_cli` the checker's calls are free, and its price is in `first_pass_price`).
- **The pro controls stay: intact.** Waves 19-20 only added (EFFECTS2's effects, the 3080 column, the mode select).
- **No Co-Authored-By; CHANGELOG on every commit: intact.** In `2600864..b2cbc31`: 0 Co-Authored-By lines; 67 of 67 non-merge commits touch `CHANGELOG.md`.

## Verdict 15's punch list, item by item
1. **Put the judged rev in front of Karl: PARTLY.** 8766 serves this branch, restarted 03:37 today from the live worktree, not a frozen snapshot. FOR-KARL still names `5138865`.
2. **A cut out of a slowed shot with no freeze: DONE on my small sample.** 0 of 2 plays (one per size) over 67 ms through the cut / Dip out of the 0.5× shot at load 8-10. The lead's 18-play A/B is in LOOP.md.
3. **A dissolve into or out of a slowed shot: MOSTLY.** Per LOOP: frame clock 0/18 and 1/18, worker clock 8/18. My Dip-to-Black out of 0.5×: 0 of 2.
4. **The late page→worker message, three clean gates: NOT DONE.** My gate's perf test before the suite was RED at load 7.9, and the lead's gate on `fb0a251` was RED on the same test.
5. **59.94 at 59.94: DONE as a product change, logged nowhere.** The test skips its bar above load 6.
6. **The half-speed bar back to "no hold": NOT DONE** (the bar was left as is; LOOP says the tip meets 59 of 60).
7. **The loop never waits on page faults: PARTLY.** LOOPHOLD's lock is in; under my session the slowest hold was 232 ms (a sidecar open, load 13, majflt 0).
8. **Crop and Transform: DONE**, as built-in effects with keyframes, AI ops and an export frame compare (in the suite).
9. **Polish (List-view scroll, keeps lane name): DONE** per POLISH13's tests. Not driven by me.
10. **The stale-read rule tested at the facts change: DONE** (POLISH13, with mutants).

## Blockers
1. **K2, K3, K4, K5 and K6 are open.** Only Karl closes them. K5 and K6 also have concrete gaps above.
2. **K1 has regressed:** at 1440×900 after manual edits, "Re-run first pass" is off-screen in a panel that cannot scroll (dimension 2 at 5, and a stuck state in the driven session).
3. **The full gate is RED.**
   - Perf failed before the suite at load 7.9.
   - 2 frame-hold tests failed in the suite, one at load 2.7.
   - The gate crashed on its own 3600 s suite timeout before screens, journey, understanding or hygiene ran.
   - The quick gate is green, and the failing tests pass alone. That is a load- and order-sensitive player, not a pass.
4. **K6 on ordinary footage:** without telemetry, every visual fall / jump / crash claim is demoted off the board. The UI cannot check them, and the AI menu says "nothing to check" (dimensions 11, 14).
5. **Dimensions below 8: 2 (5), 6 (7), 9 (7).**

## THE THREE BIGGEST GAPS TO PREMIERE
1. **On a laptop screen you can't press Re-run after you've edited.** The first-pass box grew taller than the window once it lists your edited shots and the new clips, and it doesn't scroll. So the button you need, in exactly the situation Karl asked about first, is below the bottom of the screen.
2. **The new footage understanding is wired in, but it isn't yet the winner on Karl's own test set.**
   - A fresh first pass finds fewer real airs than the old baseline, because Claude's double-check throws out a jump the sensors clearly felt.
   - It never finds other riders' airs, which Karl said count.
   - On a phone or camera clip without GoPro sensors, a real fall gets hidden, and the menu says there's nothing to check.
   - The gate can't see any of this, because it grades Karl's preview copy instead of the build.
3. **It still doesn't fully say what it's doing.** After a pass, the summary is good. But while it runs, and for the new parts (the 3080's checks and Claude's checks), it says nothing about what it looked at or how. Playback still trips the speed check when the machine is busy.

## PUNCH LIST (most important first; [files] = lane ownership)

1. **Re-run reachable at every window size and state.**
   - **Files:** [`app/static/firstpass.css` / `firstpass.js` `paintPanel`: the panel gets `max-height: calc(100vh - top - 8px)` and its body scrolls; the foot (Run / Stop, cost, note) is pinned and always visible; the kept list collapses to "Keeps 10 edited shots ▸" by default]; [`app/tests/test_k5_ui.py` or `test_firstpass_ui.py`: a fixture with 10 kept shots, 3 new clips, the K5 account and both certainty rows].
   - **Done =** at 1440×900 and 1920×1080 an ordinary Playwright `click()` on `[data-testid=firstpass-run]` works in the never-run, running, done, stale-after-edits-with-new-clips and proposal-waiting states, and the panel's bottom is ≤ the viewport.
   - **Proof:** that test, plus `judge16/fpsize.py` reading `run` inside `vh`.
2. **Make the understanding gate measure the rev, and log it.**
   - **Files:** [`tools/eval_understanding.py`, `tools/gate.py` `understanding_check`]; [`research/R12-understanding/eval/baseline.json`]; [`REDESIGN-GOAL.md` log].
   - **Done =**
     - The gate runs the rev's own index pipeline on a frozen, read-only copy of Killington's inputs (proxies, telemetry, transcripts, the 3080 sidecars or a cached local result) with a **recorded checker** (the verdicts of one real Hybrid pass, replayed by the scripted backend), writes into the gate's run dir, and scores that.
     - The baseline includes checker verdicts.
     - "Skipped" is AMBER, not green.
     - Logged in the rubric log.
   - **Proof:** on `b2cbc31` it reads what `judge16/eval-mine` read (0.78, air 0.60) and turns RED, not "matches baseline".
3. **The checker must not throw out a sensor-proven air.**
   - **Files:** [`roughcut/verify.py` `settle` / `annotate_event` / `demote` / `route`: a telemetry air with free fall ≥ 0.25 s and a landing > 2.5 g that Claude rejects becomes `unsure` (shown, still asserted as felt), never demoted; the checker prompt gets the free-fall numbers first]; [`app/tests/test_verify.py`].
   - **Done =** a fresh Hybrid first pass on Killington scores recall_air ≥ 0.80 and recall_high ≥ 0.89 with negatives asserted ≤ 3 (`tools/eval_understanding.py --work <fresh work>`); a synthetic FALSE telemetry air is still rejected.
4. **Other riders' airs (Karl's K6 answer).**
   - **Files:** [`roughcut/localvlm.py` + `localgpu/rcgpu` (a frame-scan question "is anyone in frame airborne", levelled, over high-motion windows without a telemetry air), `roughcut/verify.py` routing (every local "other air" goes to Claude), `events.py` `who: other`].
   - **Done =** `air_who:other` 1 of 1 on a fresh pass (CLIP_10 ~25 s) with no new negatives asserted. The eval prints the who=other row in the gate line.
5. **Footage without telemetry: claims checkable from the UI, and an honest menu.**
   - **Files:** [`app/static/aiprompt.js` (`audit` item) + `app/static/app.js` (the seen-tab audit button): drive `GET /api/verify` `claims_check` and `POST /api/verify/claims`, price first]; [`app/server.py`: retire `/api/visual/audit` or make it the same thing]; [`app/tests/test_verify_ui.py` new].
   - **Done =**
     - On a clip with no telemetry and a visual fall claim, the AI menu reads "Check 1 claim · free on your plan", not "nothing to check".
     - After a scripted confirm, the fall is back in `/api/project` `events` and on its marker.
     - The menu never says "nothing to check" while `claims_to_check > 0`.
   - **Proof:** that test plus `judge16/claims.py` on a re-muxed clip.
6. **K5 complete: every Hybrid stage in the account, while running too.**
   - **Files:** [`roughcut/flow.py` `pass_account`, `app/server.py` `api_firstpass_account`, `app/static/firstpass.js` steps].
   - **Done =**
     - While running, the Index step names its models and counts as they are known ("Whisper large-v3 · 7 of 12 heard; Sonnet · 1 frame every 4 s, 320 px, 312 of 649 frames").
     - Two new lines: "3080 (Qwen3.5-4B, PE-Core): checked N windows at 10 fps, levelled; embedded N frames at 2 fps for Find · still running on M clips" and "Claude checked N moments on levelled 8 fps strips: X confirmed · Y rejected · Z unsure".
     - The stale "No cut yet" line is gone while a proposal waits.
   - **Proof:** `test_k5_words.py` / `test_k5_ui.py` cases for each.
7. **Contradicted moments in the proposed cut.**
   - **Files:** [`roughcut/verify.py` `apply_to_plan`, the planner prompt in `roughcut/revise.py` / `firstpass.py`; `app/static/firstpass.js` chip hover].
   - **Done =**
     - A shot whose moment is contradicted is either not proposed or listed in Things to check with what contradicted it.
     - The chip's hover says what was claimed and what the closer look found.
   - **Proof:** a fresh Killington pass shows 0 unexplained "contradicted" shots, plus a unit test on `apply_to_plan`.
8. **A full gate that finishes, then three green ones.**
   - **Files:** [`tools/gate.py`: the suite's 3600 s timeout must end in a RED summary with the lines that can still run (screens, journey, understanding, hygiene), never a traceback with no summary; raise or split the suite budget (it took 3723 s beside a first pass)]; [`app/static/grade.js` the sharpen stage and the page→worker hand-off (verdict 15 punch 4, still open: sharpen held 83 ms at load 2.7 in my suite)].
   - **Done =** a timed-out suite writes `summary.md` RED, and `test_gate_status.py` pins it. Then three consecutive full gates GREEN with both perf runs passing at gate load.
9. **Log what changed, and tell Karl what to try.**
   - **Files:** [`REDESIGN-GOAL.md` rubric log: the understanding check, WORKER's Full / Auto cap and `test_playback_resolution`'s load > 6 skip and conditional Auto assert]; [`judge/FOR-KARL.md`: the judged rev, then "Try K5: Run first pass on Killington and read the panel; Try K6: the Index mode select, the Local (3080) column, Find"].
   - **Done =** both written before the next judge.
10. **Index panel and account polish, and the account's speed.**
    - **Files:** [`app/static/indexpanel.js` / `.css`: the mode select at its full name ("Hybrid (3080 + Claude)") with its one-line sentence visible under the foot at 1440 and 1920; the Local column says "running 10:28" or a %, not a bare clock]; [`app/server.py` `api_firstpass_account` / `bin_certified`: ≤ 300 ms (measured 320-508 ms, 6 over budget, load 8-15)].
    - **Done =** `test_workspace`'s overflow check covers `.ix-foot` select and `.ix-modeline`; `/api/debug/slow` shows no `api_firstpass_account` over budget during a first pass.

## What I could not verify
- **A GPU browser:** every playback number is headless Chromium with software compositing.
- **Playback on a quiet box.** My playback runs were at load 8-12.4 with my gate beside them, and one play per cut per size.
- **The new effects (Crop, Transform, flips, Mirror, Black & White) by hand** (only through the suite).
- **The List view's whole-column scroll and the keeps lane label** (POLISH13), by hand.
- **The `anthropic_api` backend:** the checker's and the first pass's price confirm.
- **Speaker labels (DIARIZE):** the speakers stage was queued and running on my bin; I did not read its output.
- **The Find union on my own work dir** (it skipped until the 3 imported clips are embedded); 0.79 is the preview copy's.
- **Karl's 8765 and 8766** beyond their processes and start times.
- **Audio by ear.** The meters moved between −24.5 and −9 dBFS while playing.
