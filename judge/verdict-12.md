VERDICT: FAIL

# Roughcut redesign: verdict 12

- **Rev judged:** `992ca33`, the branch HEAD (wave 15 merged; 57 commits, 50 non-merge, since verdict 11's `18944d0`). The gate archived it to `~/roughcut-loop/snap-992ca33`. I served that snapshot on 127.0.0.1:8781 only:
  - **Killington:** `--footage ~/footage/killington-neutral --work judge12/work` (12 GoPro clips). I restarted it 3 times on the same work dir: once for an ungraded copy of my own EDL, then for the AI-menu check and the effect probes.
  - **The K3 walk:** an empty project in a fresh browser (`empty`/`work2`).
  - **Fresh-import playback:** 4 fresh 12-clip Killington work dirs (`work3-1..4`). Trials 2-4 ran with 5 busy-loop burners to put the box at load 6.5-9.7.
- **Karl's live board:** `35cb147`, an ancestor of `992ca33`, **467 commits behind**.
  - Ports 8765/8766 were untouched: same pids as at the start (1902724 started Oct 15 00:59; 3924990 started **Oct 16 00:05**).
  - The redesign preview on 8766 is still the pre-wave-12 server process. Its cwd is this live worktree.
- **Box load (6 cores):**
  - 2.8-3.6 at the cold land.
  - 5.1-11.0 (mean 7.5) during the first pass, with my gate's suite running beside it. Swap was in use (4.3 GB), with swap-ins up to 7.8 MB/s.
  - 4.5-8.7 for the asks.
  - 1-5 for the playback probes.
  - The standalone perf runs launched at load 7.0-8.1, and measured at load_pre 8.8-11.1 (their own 4K fixture encodes).
- **Model spend:** backend `claude_cli`, nothing billed.
  - Killington: one 12-clip first pass (Start, Stop, Re-run), one re-run, 11 asks, and 3 Designed Effects (2 from the AI, 1 by hand).
  - The empty project: one 3-clip pass and 2 asks.
- **Cleanup:**
  - All my servers are stopped (`pgrep judge12/` is empty, 8781 is free).
  - The `~/footage` listing is byte-identical before and after (`footage-before.txt` = `footage-after.txt`). Import folders were copies under `judge12/import2`, `import3` and `dropsrc`.
  - The only repo write is this file, uncommitted.

## Gate: my own run, `~/roughcut-loop/gate-992ca33-20261019T1346/summary.md`: **GREEN**, 2830 s

| check | result |
|---|---|
| browser | ✓ Playwright 1.64.0 / Chromium 156 |
| suite | ✓ 1782 passed, 1 failed in the full run, 1 skipped, **1 flaky**: `test_lumetri_ui::test_a_wheel_push_moves_the_program_and_the_export_alike` (196 ms against its 150 ms bar; it passed alone in 11 s). This is the flake LOOP.md already names. **Perf, every run counts:** before the suite **passed** at load 7.4 (live 50.0 ms, 66.7 by expectedDisplayTime; canvas frame/worker 50.1/52.1 at 1440, 50.1/50.2 at 1920). After the suite **passed** at load 5.5 (canvas frame/worker 66.7/65.9 at 1920; its page clock read **68.4**). |
| screens | ✓ 7 PNGs, 0 page errors. Counts: / 109·57, /floor 52·7, /open 55·8, / shot 138·72, / film 117·56, / jobs 118·58, / fx 143·60 (unchanged from verdict 11) |
| journey | ✓ 24 steps · 52 s · slowest request 0.06 s · **slowest loop hold 20 ms** (the new line); `journey.json` `retried: []`, `amber: null` |
| boot | ✓ the cut on screen 0.66 s after a proxy lands (limit 2 s) |
| hygiene | ✓ clean |

## Rubric changes log: rulings on the entries since verdict 11

There is one new entry: 2026-10-19, "the speed test's bar, stricter, and the gate watches the loop". I ruled on each part.

1. **The canvas on the frame clock AND the worker clock; the live buffer on rVFC `expectedDisplayTime` as well as `now`: honest in substance, lenient in wording.**
   - `test_perf_latency.py:646-661` asserts the live buffer on both `now` and expectedDisplayTime, and the canvas on both the frame clock and the worker clock.
   - What the entry does not say plainly: the **page-clock canvas assertion was removed** (it was `cv["longest_ms"]` at `18944d0:621`). "As the page-callback clock was" hides that.
   - It matters on this very gate. The after-suite run's 1920 canvas read 68.4 ms on the page clock, which would have failed the old bar, and 66.7/65.9 on the frame and worker clocks, which pass.
   - Verdict 11 showed the page clock overstates a 4-vsync hold by 2-12 ms, and asked for exactly this swap. So the change is right. **Ask:** add one sentence to the log saying the page clock is no longer asserted.
2. **5/5 standalone runs at the test's own load: honest (stricter), and not met.** My five back-to-back runs (`judge12/perf/perf-1..5.json`, 0 burners, load_pre 8.8 / 11.1 / 9.7 / 10.5 / 10.0) passed **3 of 5**:
   - Run 2 failed on the live buffer: 99.9 ms by `now`, 83.3 ms by expectedDisplayTime.
   - Run 5 failed on the 1920 graded canvas: 75.2 ms by the worker clock, 66.6 ms by the frame clock.
   - LOOP.md does not claim 5/5 after PERF6.
3. **Both gate runs count: honest, stricter.** `tools/gate.py:286-321`: any run that fails at or below 2× the cores is RED, whatever the other run did. A fail above 2× the cores is still AMBER, as before. Both runs passed today.
4. **The journey line prints `slowest_loop` and is RED over 200 ms: honest.** `tools/gate.py:166-200`; today it read 20 ms. This closes the hole verdict 11 found.
5. Also checked: `dc51041` changes 3 test expectations. It makes them wait out a poster stand-in, and patches `clip_duration_known` in one price test. Neither loosens a bar.

**PERF5's note on my predecessor's `pb11.py`: verified, PERF5 is right.**
- `tl.addTransition(k)` puts the transition between `segs[k]` and `segs[k+1]` (`timeline.js:2594-2611`). `judge11/pb11.py` seeked to `filmStart(idAt(k)) - 1.5` and played 3.0 s, with k chosen so `segs[k]` lasts more than 2.5 s.
- So every "dissolve play" ended at least 0.5 s before the dissolve began. **Verdict 11's "Cross Dissolves 1 of 24" measured plain graded cuts, not dissolves.**
- I used the corrected copy (`DISSOLVE_AT=k1`) as `judge12/pb12.py`.

## Driving it like an editor

**Cold land (`cold.log`, `01-cold-5s-1440/1920.png`).**
- Rows in 0.12-0.14 s and a picture in 0.13-0.26 s. One blue app action ("Run first pass") plus the tour's Next. 0 page errors; one console 503 for CLIP_01's preview while it builds.
- Premiere's Editing arrangement, with the AI prompt in the Timeline header.
- Still there from verdict 11: the tour card "1 of 6" covers the Source's "preparing preview…" label (it reads "eparing preview…").

**First pass on Killington (`pass.log`).**
- The panel opens in 66 ms. **Stop took 120 ms**, and the label became "Re-run first pass", including after a reload.
- The timed run took **473 s** at load 5-11 and proposed 18 shots (3:01.5) with "Things to check".
- `12-fp-running-30s.png`: the header button's "Run first pass" hover card stays up over the open panel and hides the line under Stop. The button now reads "First pass · 0/12".

**Review (`review.log`, `31-proposal-1440.png`).**
- 18 ghosts on the Proposal track, and 19 pick markers whose hovers carry the why.
- I rejected 2 (ghost ✗ and table ✗). "Accept 16 of 18" landed exactly the proposal minus those 2, with 2 PUTs.
- The sequence took 59.94 from its first clip.
- A drag-then-grade (drop, then Temperature 40) landed in 73 ms and survived a reload. The hand trim stayed on the frame grid.

**Editing (`edit-1440.log`, load 5.4-8.1).**
- Keys: +/−, \, →, ↑/↓, J/K/L, I/O, M, C/V, Ctrl+K, undo and redo all work.
- Edits: ripple, roll, move, Selects `,` (2 inserts), double-click pick → Source In/Out, Add/Delete Video Track, a V3 overlay, and overwrite past the end (Black Video). Each is one undo. The cut survived a reload.
- Timings: seeks 34-101 ms; click → Effect Controls 22-36 ms; keys 56-97 ms (Playwright round trip included); `effects.set` 7-22 ms.
- **Markers are bare timestamps.** `timeline.js:806-810` gives them no name, comment or colour, and the AI's `marker` op is `{at}` only (`actions.py:246`). Asked to drop "a marker … called best trick", the AI answered: *"The marker tool can't take a name, so you'll need to type 'best trick' on it yourself"*. You can't type a name by hand either.
- Narrow blocks (10-14 px at fit zoom) can't be selected with a left click: the edit-point trim columns cover them (`timeline-trim.js:207-216`). This is close to Premiere's selection-tool behaviour at the edges, so it is noted, not scored.

**AI asks on Killington, in my own words, on the real model (`ask.log`):**

| family | my words | answer | review | land |
|---|---|---|---|---|
| timeline | "Shorten shot 3 to about a second and a half, and move the last shot so it opens the film" | 6.9 s: trim + move | ghosts on Proposal; I rejected the move → "Accept 1 of 2" | disk unchanged until Accept; one Ctrl+Z exact; redo exact |
| keyframes | "Fade shot 2 up from black over its first second using opacity keyframes" | 6.9 s: opacity 0→100 % over 1.00 s | ◐ After | one undo exact |
| colour (right-click "Ask AI about this…", chip "Shot 5 · CLIP_02") | "The snow in this one looks dull and a little green…" | 10.5 s: Tint +15, Whites +20, Blacks −12, Contrast +20 | ◐; I rejected one → "Accept 3 of 4" | one undo exact |
| transition | "Put a half second cross dissolve between the fourth and fifth shots" | 5.9 s | "Cross Dissolve · 0.50 s" ghost on the edit | one undo exact |
| **vignette (punch 7)** | right-click shot 7: "Darken the corners of this shot a little, a gentle vignette" | 6.4 s: **`vignette` op: Lumetri Vignette Amount −1.2, Feather 70** | ◐ After; the corners darken on the Program (`64-vignette-after.png`) | one undo exact |
| **sharpen (new)** | right-click shot 3: "This shot is a bit soft, sharpen it slightly" | 4.4 s: `effect` Sharpen 60 | ◐ After | one undo exact (**but see Playback**) |
| speed | right-click shot 8: "Slow this shot down to half speed" | 5.5 s: Speed 50 % | ghosts | one undo exact |
| look | "Make the whole film feel colder, like early morning blue light" | 6.5 s: look cold 0.70 | ◐ After | one undo exact |
| In/Out + marker | "Set In and Out around shots 2 through 4 and drop a marker at the start of shot 3 called best trick" | 6.5 s: In 00:00:01:14, Out 00:00:22:48, marker at 00:00:13:43 | rows | one undo exact; **no name (above)** |
| title (Designed Effects) | "Put a big white KILLINGTON title over the first two seconds of the first shot" | 8.0 s: 2 `design_effect` ops (1.23 s on shot 1, 0.77 s on shot 2) | "its own review follows" | **Accept read "✓ 2 changes applied" though nothing was applied. The next Ctrl+Z silently undid the In/Out + marker before it.** The two designs checked ✓ in ~20 s; accepting one in Designed Effects is one Ctrl+Z (`fx12.log`); the title draws on the Program (`79f-ai-title-program.png`) |
| export | "Render me a 1080p review copy of the whole sequence" | 6.5 s: Quick 1080p, Entire Sequence | "writes …/review_copy_1080p.mp4 · 1920×1080 · 59.94 fps · Quick · 00:05:04:48" | ran only on Accept, 241 s; `ffprobe` 1920×1080, 60000/1001, **18288 frames = 305.105 s × 59.94 exactly** |

- **Internal ids leak into the AI's words:**
  - The timeline answer says *"The last shot (g5848fde311) then moves to the start of the film"* (`63-timeline-review.png`).
  - The KILLINGTON effect card says *"the continuation into shot g0e2637544b needs its own matching effect"*.
- The AI menu lists every AI action, and each hover card says what it does, what it costs and what it changes (`aimenu`).
- The keys sheet lists Ctrl+J and Ctrl+I.

**Designed Effects by hand (punch 2, `fx12.log`).**
- On shot 17 I asked for "A big white SEND IT title that pops in at the start of this shot…". It was designed and checked in 30.4 s, and **its check passes ("✓ checked", event at the shot's first frame).**
- Accept → undo reads "undo: effect: SEND IT pop title"; **Ctrl+Z → `proposed`; Ctrl+Shift+Z → `accepted`.**

**Footage in, mid-session (`import.log`).**
- Ctrl+I opens the dialog in **75 ms**.
- `judge12/import2` (KX_05, KX_10): rows in **0.41 s** with fps, size and timecode. KX_05 was cut at once with `,` and undone.
- An OS-style drop of a 190 MB file read "Imported 1 clip" at **0.67 s**. Posters show in Icon view (lazy-loaded).
- The offer read "3 new clips: add them to the cut? · Looking at them: free on your plan · Re-run first pass… · Not now".

**Re-run after a film-wide AI look (punch 6, `rerun.log`, `72`/`73-*.png`).**
- The panel, after the AI's cold look, per-shot AI colour, a vignette and hand edits: **"Keeps 11 shots you cut by hand · keeps the grade on every shot it keeps · may replace 11"**, with each kept shot named. Shot 21 reads "Black Video".
- 3 clicks (offer → Re-run → Accept), **67 s**. It kept 11/11 exactly.
- Because my locked shots already ran 194 s, past the 180 s ceiling, it dropped the other 11 and said why. It named each new clip once, as a duplicate ("KX_05 … matches CLIP_05").
- Things to check shows no `gen_` names.
- Against: an AI-made trim and speed change are listed as "cut by hand".

**Playback (`pb12-*.log`, raw logs in `judge12/out/`; the canvas by its frame clock, the live buffer by rVFC):**
- **Plain cuts:** 1440 and 1920, 0 holds over 67 ms. Seeks at 1920 took 47-107 ms.
- **Graded Cross Dissolves**, real ones this time (0.4 s and 1.0 s, 24 plays):
  - **0 of 24 hold over 67 ms inside the mix.** Several hold exactly 66.7 ms.
  - One play held 83.4 ms about 0.8 s after the mix ended, inside the next shot.
- **Ungraded dissolves (12 plays):** the live buffer's longest gap is ≤ 66.7 ms in all 12. PERF5's merged "screen" metric reads 83.3 ms in 4. Every one of those 4 is at the canvas→video handoff, where a video frame presented 17-33 ms earlier becomes the picture as the canvas hides. I read them as artefacts of the metric.
- **The 0.4 s shot:** 3 of 3 plays clean.
- **NEW: a shot with Sharpen plays at about 5-7 fps.** Shot 3 carries the AI's Sharpen 60, which is an SVG `feConvolveMatrix` on `#gradeCanvas` and the videos: `filter: url("#ecvs0")`, `sharpprobe.py`.
  - At **1920** on a quiet box (load 1.0-2.3), 3 plays across it held **183-217 ms with 10-14 holds** each.
  - At **1440**, 150-183 ms with 3-6 holds.
  - **Sharpen off** (`vfxAct('off')`, the same cut): 66.7-100 ms, 0-2 holds, which is what the 0.5× shot after it gives.
  - This is headless software compositing; I did not check a GPU browser.
- **Fresh import at load ~8 (punch 5, `freshplay-2..4.log`):**
  - KX_03 imported and inserted at once. It played its first real frames at **+8 s in 3 of 3** trials at load 6.5-9.7 (my polls fell at +5 s, then +8 s), and +8 s once at load 1.5-3.4.
  - The wait line read "~3 s" at +0.6 s and "~1 s" at +5 s. It is optimistic by about 2×.
- Meters move between −40 and −16 dBFS.

**Export (`exp.log`).**
- Ctrl+M opens the dialog in 35-51 ms; Esc closes it.
- An In/Out export (4.00 s) at 1080p/59.94 gave 240 frames = 4.004 s.
- The fresh project's 720p/29.97 export gave 622 frames = 20.754 s (sequence 20.771 s).

**The K3 walk: an empty project, a fresh browser, no doc (`fresh.log`, `80-…97-*.png`).**
- **Landing:** every panel names Import; the header reads "Next: Import footage (Ctrl+I)".
- **Import:** the tour's Show me opens Import. Import Folder gave rows at 0.44 s; the drop finished in 3.4 s.
- **First pass:** 78 s. **Why:** on the markers, in Selects, and as subclips under each clip (`binsub12.py`).
- **Review:** I rejected one ghost → "Accept 2 of 3".
- **Two asks:**
  - "Make this about 20 seconds long and open on the funniest moment" → a 4-shot plan (20.8 s), accepted.
  - "brighten this shot a touch and make the snow look whiter" → 3 ops with ◐, accepted.
  - "Working" showed by **80 ms** after Enter both times.
- **Adjust by hand:** typed Saturation 80 in Effect Controls → one undo "saturation" (`ec12.py`). My walk script first clicked the slider rather than the number; a person would not.
- **Export:** Ctrl+M at 720p/29.97.
- **I made no guess at any step.** What I saw on the way:
  - With Selects open (tour step 3's Show me), the Project column is 595 px wide and the Timeline about 465 px.
  - The Timeline header then shows the scope chip as **"Sequ…"** and the prompt as **"Ask the A"**, and its **ruler labels overlap** (`95a-crop-tlhead.png`).
  - The step-5 tour card covers the Assistant's Accept ("ccept", `95a-plan-proposal.png`).

## The 16 dimensions

| # | dimension | score | evidence | vs Premiere Pro |
|---|---|---|---|---|
| 1 | Orientation | **9** | Rows 0.12-0.14 s, a picture 0.13-0.26 s, one blue app action, 0 page errors (`01-cold-5s-*.png`); every empty panel names Import (`80-fresh-empty-land.png`). Against: the tour card still clips "preparing preview…". | Premiere's Editing workspace and "Import media to start". |
| 2 | First pass visible + re-runnable (K1) | **9** | Header control, AI menu, empty-state button; Stop 120 ms; pass 473 s; re-run 67 s keeps 11/11 exactly and **"may replace 11" after a film-wide AI look** (`72-rerun-panel.png`); 3 clicks. Against: a stale hover card over the running panel; an AI trim/speed reads "cut by hand". | n/a: a clear, re-runnable AI step. |
| 3 | Footage in, no ceremony | **9** | Drag + grade 73 ms; Selects `,` ×2 exact; an imported clip cut at once with `,`. | Premiere's bin and three-point editing. |
| 4 | Workspace | **9** | Program 674×379 (1440) / 841×473 (1920), vertical meters; **the Source stays 714 px with the Assistant open** (`64-vignette-after.png`); panels persist (gate). Against: Selects open squeezes the Timeline to ~465 px. | Premiere's Editing workspace. |
| 5 | Timeline craft | **8** | The key map, ripple/roll/move/razor/tracks/gap/overwrite past the end, each one undo (`edit-1440.log`); fixed timebase. Against: **markers have no name, comment or colour** (`timeline.js:806-810`, `actions.py:246`), so "a marker called best trick" can't be made by hand or by the AI. | Premiere's markers carry a name, comment, colour and duration (Marker dialog). |
| 6 | Monitors and playback | **7** | Seeks 34-107 ms; plain cuts and the 0.4 s shot clean; **graded dissolves 0/24 holds in the mix** (1 play held 83 ms just after); ungraded 0/12 real holds; fresh import plays at +8 s 3/3 at load 6.5-9.7; gate perf passed both runs. Against: **a Sharpen-ed shot plays at about 5-7 fps (holds 150-217 ms, up to 14 per 3 s) at 1440 and 1920 on a quiet box**, normal with it off; `test_latency_under_load` **3 of 5** standalone (live 99.9 ms; 1920 canvas worker 75.2 ms). | Premiere plays a sharpened GoPro shot and these cuts with no held frame. |
| 7 | Inspector, effects, colour | **8** | Effect Controls typing (Saturation 80, one undo); **Lumetri Vignette** live and from the AI as a Lumetri op; **Sharpen** in the bin and by AI; Crop/Transform/Blur; keys. Against: the Sharpen preview is an SVG filter that stalls playback; five video effects in all. | Most of Lumetri; a sliver of Premiere's Effects panel, and Premiere's play in real time. |
| 8 | Export | **9** | Ctrl+M 35-51 ms; Esc; three exports with exact frames (18288, 622, 240); the AI export op ran only on Accept. Against: no codec/audio settings; size "measuring…" at 2.5 s. | Premiere's Export Settings basics. |
| 9 | Looks like a shipped pro tool | **8** | Verdict 11's leftovers fixed: no `gen_` in Things to check, the gap reads Black Video, the In/Out chip whole ("In/Out 0:01-0:22"), the price words whole. Left: **internal ids in the AI's sentences** ("g5848fde311", "shot g0e2637544b"); the Selects-open Timeline header ("Sequ…", "Ask the A", overlapping ruler labels); a tour card over the Assistant's Accept and one over "preparing preview…"; a stale hover card over the first-pass panel; a toast over the Program picture (`31-proposal-1440.png`). | Close in layout and density; Premiere shows none of these. |
| 10 | Responsive, reliable | **8** | Clicks 22-36 ms; keys 56-97 ms; Ctrl+I 75 ms; **every timeline / Effect Controls / AI edit and every Designed Effects accept is one undo** (verdict 11's blocker fixed); disk unchanged during all 11 reviews; journey loop hold 20 ms. Against: the design_effect accept says "applied" and the next Ctrl+Z undoes an older, unrelated edit; **24 over-budget entries in the Killington session** (`slow-killington.json`: posters 0.4-0.9 s and `/api/firstpass` 0.4-0.67 s at the pass's start and end, `/api/sprites` 677 ms, 5 loop holds 227-651 ms, mostly with the loop barely on CPU while the box swapped); a flaky test in the gate. | Premiere undoes everything it says it applied, and never stalls. |
| 11 | Proposals are reviewable edits | **9** | 18 ghosts with ✓/✗; 16/18 exact; why on hover; the re-run's diff marks dropped shots on V1 (`73-rerun-proposal.png`); free on your plan. | Premiere-like review. |
| 12 | The journey | **9** | 24 steps green in the gate, 52 s, slowest request 0.06 s, slowest loop hold 20 ms, no retry. | n/a |
| 13 | Footage in, from anywhere | **9** | Ctrl+I 75 ms; Media Browser + Import Folder; Project-panel Import; drop 0.67-3.4 s; facts at once; posters; "3 new clips" offer; the re-run names each new clip. Against: an OS folder drop is unverified. | Premiere's Import and Media Browser. |
| 14 | One way to ask the AI | **9** | Always-visible prompt; Ctrl+J; chip follows the right-click ("Shot 7 · CLIP_04") and In/Out; working by 80 ms; answers 4.4-10.5 s; AI menu complete with hover cards. Against: with Selects open the chip reads "Sequ…" and the input "Ask the A". | n/a |
| 15 | The AI does everything, as reviewable edits | **8** | 11 families on the real model: trim, move, opacity keys, Lumetri, dissolve, **Lumetri Vignette**, **Sharpen**, speed, look, In/Out + marker, designed title, export. Each previews; ✓/✗ per op; disk unchanged 11/11; one Ctrl+Z exact 9/9; export only on Accept, exact frames. Against: **markers can't be named**; a design_effect accept is not an undo step and its words claim "applied"; the AI's words carry internal ids. | n/a |
| 16 | Easy to learn | **9** | 6-step tour on the real workspace, Show me each; my K3 walk had no guess; hovers with what/cost/changes; empty states; subclips with their why. Against: the step-5 card covers Accept. | Premiere's Project panel with AI on top. |

## Section K
- **K1 "not clear how to re run the first pass": RESOLVED** on real footage, as in verdicts 3-11 (header control, AI menu, the "3 new clips" offer, a panel that says what it keeps and may replace). Karl to confirm.
- **K2 "not premier pro quality": OPEN (Karl's call).**
  - Verdict 11's list is mostly gone: Designed Effects undo, the false check, the re-run lock, the vignette rings, and the fresh-import wait.
  - Left: a sharpened shot stutters, markers without names, ids in the AI's words, and a cramped Timeline when Selects is open.
  - Karl has not looked: 8766 still runs Oct 16's server code, and `judge/FOR-KARL.md` still describes `18944d0`.
- **K3 "AI-first, easy to learn, add footage obviously": OPEN (Karl's call), delivered** as far as I can test. The walk had no guess; every pro family is askable and reviewable; the AI menu is complete.

## Section S
- **Inference only through `roughcut.inference`; no model-ID literal outside `config.py`: intact.** A grep of the snapshot (`.py/.js/.html`, excluding tests and `config.py`) finds no `claude-*` ID. `import anthropic` is only at `roughcut/inference.py:592`. Gate hygiene is clean.
- **No price reads as money spent on `claude_cli`: intact.** Controls read "free on your plan" or "no charge on your plan"; the API estimate is in the hover or `title`.
- **Karl's footage and work dirs never written: intact.** The footage listing is identical; imports referenced copies under `judge12/`; 8765/8766 untouched.
- **INTAKE decisions: intact.**
- **The pro controls stay: intact.** Wave 15 only added (Lumetri Vignette, Sharpen).
- **No Co-Authored-By; CHANGELOG on every commit: intact.** In `18944d0..992ca33`: 0 Co-Authored-By lines; 50 of 50 non-merge commits touch `CHANGELOG.md`.

## Verdict 11's punch list, item by item
1. **Put the judged rev in front of Karl: NOT DONE (waits on Karl's yes).** 8766 is the Oct 16 00:05 process; `FOR-KARL.md` still names `18944d0` and "wave 15 is fixing"; no new words from Karl in section K.
2. **Designed Effects: one Ctrl+Z, and no false check at the shot's start: DONE.** The SEND IT title at a shot's first frame checks ✓; accept, Ctrl+Z (`proposed`), Ctrl+Shift+Z (`accepted`). The AI's titles also checked ✓ and undo. The Killington vignette is now Lumetri's, not a designed effect.
3. **A perf test that measures what the viewer sees, and passes every time: PARTLY DONE.**
   - Done: the clocks are asserted; both gate runs count; both passed.
   - Not done: **3 of 5 standalone** at load_pre 8.8-11.1. The log hides that the page clock was dropped.
4. **The server keeps its 300 ms budget; the gate watches the loop: PARTLY DONE.**
   - Done: the journey line shows the loop (20 ms, RED over 200 ms).
   - Not done: the Killington session still had 24 over budget. All of them are at the first pass's start and end (posters 0.4-0.9 s, `/api/firstpass`, `/api/sprites`, 5 loop holds), with the box at load ~9 and swapping. None appeared during the asks, re-run, export or playback.
5. **A just-imported clip plays within 10 s at load ~8: DONE**, +8 s in 3 of 3 at load 6.5-9.7. The ETA is about 2× optimistic ("~1 s" at +5 s).
6. **A re-run can still re-cut after the AI regrades: DONE.** "May replace 11" after a film-wide AI look.
7. **Lumetri Vignette and Sharpen: DONE**, with one new fault: Sharpen's preview stalls playback (see punch 2). I did not measure the exported vignette's centre ±2 %; `test_vignette.py` covers it.
8. **Polish leftovers: PARTLY DONE.**
   - Done: Things to check names, Black Video, the price words, the Source ≥ 560 px with the Assistant open, the In/Out chip at normal width.
   - Not done: the chip still ellipsizes ("Sequ…") when Selects narrows the Timeline.
9. **The rest of the dissolve: DONE (nearly).** On real dissolves: 0/24 graded plays hold in the mix (1 holds 83 ms just after it); 0/12 ungraded plays have a real hold.
10. **Zero flaky tests: NOT DONE.** `test_lumetri_ui`'s 150 ms bar flaked in my gate (LOOP.md named it as known).

## Blockers
1. **K2 and K3 are open** (Karl's).
2. **Dimension 6 is below 8.** A shot with the Sharpen effect plays at about 5-7 fps (holds up to 217 ms). Separately, the speed test passes 3 of 5 standalone.

The gate is GREEN, and every other dimension is at 8 or 9.

## THE THREE BIGGEST GAPS TO PREMIERE
1. **Effects make playback stutter.** Put Sharpen on a shot, by hand or by asking the AI, and that shot plays at 5-7 frames a second, on a quiet machine and at either window size. Turn it off and it is smooth again. Premiere plays a sharpened shot in real time. Separately, the app's own speed test still fails about 2 runs in 5 when the machine is moderately busy.
2. **Markers are just ticks.** You can't name a marker, add a comment or give it a colour. When I asked the AI for "a marker called best trick", it had to tell me to name it myself, and there's nowhere to do that.
3. **Small AI-and-editor rough edges.**
   - The AI's explanations sometimes print internal IDs like "g5848fde311" instead of "shot 22".
   - Accepting an AI title says "2 changes applied" when it has only sent them to Designed Effects; pressing Ctrl+Z then undoes the edit before it.
   - With Selects open, the AI prompt shrinks to "Ask the A" and the timeline ruler labels overlap.

## PUNCH LIST (most important first; [files] = lane ownership)

1. **Put the judged rev in front of Karl (K2, K3: the only way to PASS).**
   - **Files:** ops plus [`judge/FOR-KARL.md`].
   - **Steps:**
     - With Karl's yes, restart `roughcut-redesign` (8766) from a frozen snapshot of the judged rev. Its process dates from Oct 16 00:05 and serves this live worktree.
     - Update FOR-KARL to this rev and wave 15: Lumetri Vignette, Sharpen, Designed Effects undo, re-run after AI colour.
   - **Done =** Karl's words on K2 and K3 are recorded in section K.
2. **Video effects play in real time.**
   - **Files:** [`app/static/effects.js` `vfxLook` (the CSS/SVG filter it hangs on `#gradeCanvas` and the videos)]; [`app/static/grade.js`: a Sharpen stage (and Gaussian Blur's) in the worker's shader, as the vignette has, at Playback Resolution while playing]; [`app/tests/test_vfx_playback.py` new].
   - **Done =** a graded shot with Sharpen 60 (and one with Gaussian Blur 12) plays with no hold over 67 ms on the frame clock at 1440 and 1920 in the test, and in `judge12/pb12.py` on Killington (`CUTS=` the effected shot). Parked frames and the export are unchanged (`test_vignette`-style parity).
3. **The speed test passes 5 of 5 standalone, and the log says what it dropped.**
   - **Files:** [`app/static/grade.js` worker scheduling at 1920]; [`app/static/app.js` live-buffer delivery at load ~10]; [`REDESIGN-GOAL.md` rubric log: one line saying the page-clock canvas assertion was removed].
   - **Done =** `judge12/perf5.sh` (5 back-to-back runs, load_pre 6-10) passes 5/5 twice.
4. **Markers as Premiere has them.**
   - **Files:** [`app/static/timeline.js` markers: name, comment, colour (and duration); a Marker dialog on double-click or M at a marker; the name on the ruler and in the hover]; [`app/server.py` / the EDL schema for `markers`]; [`roughcut/actions.py` `_c_marker` and the vocabulary: `{at, name?, comment?, colour?}`]; [tests].
   - **Done =** "drop a marker at the start of shot 3 called best trick" lands a named marker. A hand-named marker gives the same EDL (`test_actions_ui` AI-op = hand-op). Names survive a reload and show in the export's chapter list if there is one.
5. **An AI design request is honest about what it did.**
   - **Files:** [`app/static/actions.js` / `aiprompt.js` the accept path for `design_effect` ops]; [`app/static/fx.js`].
   - **Done =** accepting `design_effect` ops says "2 effects being designed — review them in Designed Effects" (never "applied") and opens that tab. A Ctrl+Z right after either discards those designs or says "nothing to undo for the designs". It never undoes an older edit without saying so.
   - **Test:** ask for a title after an In/Out change; accept; Ctrl+Z; In/Out unchanged.
6. **Internal ids never reach the user.**
   - **Files:** [`roughcut/assist.py` / the ask prompt: refer to shots as "shot N (CLIP_xx)"]; [`app/static/app.js` `clipNamed`: map `g[0-9a-f]{10}` to "shot N"]; [`roughcut/fx.py` notes].
   - **Done =** a page scan for `/\bg[0-9a-f]{10}\b/` over the Assistant, the AI status `title`, Things to check and Designed Effects cards finds nothing, in a test with a scripted answer that contains an id.
7. **The server's budget during a first pass, on real footage.**
   - **Files:** [`app/server.py` poster handler at the pass's end (the burst of card posters), `api_firstpass`, `api_sprites`, `snap_poll` / `dirwatch` (the loop was held in `dirwatch._drain` / `_compile`)]; memory (the box swapped during the pass).
   - **Done =** on Killington, a first pass, 5 asks, an export and an import leave `/api/debug/slow` `over_budget` empty except the declared long-poll.
8. **A narrow Timeline and the tour never hide the controls.**
   - **Files:** [`app/static/dock.js` the Selects width vs the Timeline's minimum]; [`app/static/aiprompt.js` the chip and input minimum widths, the result line moving out of the header]; [`app/static/timeline.js` ruler label density]; [`app/static/tour.js` card placement: never over the target's Accept or a status label]; [the hover card closes when its button is clicked].
   - **Done =** `test_workspace`'s overflow check also runs with Selects open at 1440 (chip whole, input ≥ 160 px, ruler labels not overlapping); a tour-step test asserts the card does not cover `#acceptProposal` or `#monWait`.
9. **Zero flaky tests, three gates in a row.**
   - **Files:** [`app/tests/test_lumetri_ui.py`'s two 150 ms bars (measure from the frame the change lands, or wait out the box)], and whatever flakes next.
   - **Done =** three consecutive full gates with "0 flaky".
10. **Playback and wording leftovers.**
    - **Files:** [`app/static/grade.js` / `app.js`: the 83 ms hold just after a graded dissolve]; [`app/server.py` `/media/status` ETA: it said "~1 s" at +5 s for a clip that played at +8 s]; [`app/static/firstpass.js`: an accepted AI trim or speed change reads "edited" rather than "cut by hand"].
    - **Done =** `pb12.py` 0/24 graded and 0/12 ungraded holds over 67 ms across and after the dissolve; `freshplay12.py` ETA within 1.5× three times; a test for the re-run words.

## What I could not verify
- **A GPU browser:** the Sharpen stall and every playback number are headless Chromium with software compositing (frame, worker and rVFC clocks, no real vsync).
- **Gaussian Blur playback:** my probe did not apply it (the `effects.set` call returned false), so only Sharpen is measured.
- **A folder dropped from the OS** (headless Chromium cannot fake `webkitGetAsEntry`), and Upload Folder… through the chooser.
- **The exported vignette's centre ±2 %** (left to `test_vignette.py`).
- **Karl's 8766 preview** beyond its process start time and cwd; per the brief I sent it no request.
- **The `anthropic_api` backend's cost words** (not run).
- **Audio by ear.** The meters moved between −40 and −16 dBFS.
