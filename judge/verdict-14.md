VERDICT: FAIL

# Roughcut redesign: verdict 14

- **Rev judged:** `799d8b8`, as briefed (wave 17 merged plus the lead's fix commit; 47 commits, 39 non-merge, since verdict 13's `b12b979`).
  - While I started, the branch moved to `0eba502`, a docs-only commit (`CHANGELOG.md` and `judge/LOOP.md`, +12 lines). My first gate launch picked it up by accident. I stopped that gate after 30 s, deleted its dirs and relaunched on `799d8b8`.
  - The gate archived `799d8b8` to `~/roughcut-loop/snap-799d8b8`. I served that snapshot on 127.0.0.1:8781 only:
    - **Killington:** `--footage ~/footage/killington-neutral --work judge14/work` (12 GoPro clips; 3 at 59.94, 9 at 29.97).
    - **The K3 walk:** `--footage judge14/empty --work judge14/work2`, a fresh browser with no tour flag.
- **Karl's live board (8765):** `35cb147`, an ancestor of `799d8b8`, **549 commits behind**. Its process (pid 1902724) dates from Oct 15. I sent it no request.
- **Karl's preview (8766):** pid 1552214 has run since **Oct 19 21:30 on `b12b979`'s Python**, and it serves the live worktree's static files, which are now at wave 17 (`0eba502`).
  - So if Karl opens it today, he gets a wave-17 page against a wave-16 server: the Index panel's `/api/index/clips` and `/api/index/rerun` do not exist on that server.
  - `judge/FOR-KARL.md` still names `992ca33`. I did not touch either.
- **Box load (6 cores):**
  - 2.1-5.6 at the cold land.
  - 5-9.3 during the first pass, with my gate's suite running beside it.
  - 4-7 for editing; 1.7-4.1 for the asks.
  - 1.7-4.7 for playback, except one play at 7.4.
  - 1.8-8.7 for the standalone test runs.
- **Model spend:** backend `claude_cli`, nothing billed.
  - Killington: one first pass (Start, Stop, server restart, Re-run), one Index re-look of CLIP_04, 8 asks, 1 export ask, 2 design asks, and one re-run.
  - The empty project: one 3-clip pass and 2 asks.
  - My own ledger has 109 lines (`judge14/ledger.jsonl`), plus the lines below that went to Karl's ledger.
- **Cleanup:**
  - My servers are stopped (8781 is free; `pgrep judge14` is empty).
  - `footage-before.txt` = `footage-after.txt` (byte-identical listing, sizes and mtimes).
  - Imports were copies under `judge14/import2`, `import3` and `dropsrc`.
  - The only repo write is this file, uncommitted.
  - **But see Section S:** my first server instance appended about 21 model-call lines to `~/work/roughcut-ledger.jsonl` before I noticed.

## Gate: my own run, `~/roughcut-loop/gate-799d8b8-20261020T0835/summary.md`: **GREEN**, 3377 s

| check | result |
|---|---|
| browser | ✓ Playwright 1.64.0 / Chromium 156 |
| suite | ✓ 1828 passed, 1 skipped, **3 flaky** (failed in the full run, passed alone; see below). **Perf: both runs passed.** Before the suite, at load 10.5 with 0 burners: live 50.1 ms, canvas frame/worker 50.1/50.0 (1440) and 50.1/45.2 (1920). After the suite, at load 9.5: 50.0/45.2 and 50.0/52.6. |
| screens | ✓ 5 PNGs, 0 page errors. `/` 110·58, `/ shot` 139·73, `/ film` 118·57, `/ jobs` 119·59, `/ fx` 144·61: exactly the INDEXPANEL entry's numbers. `/floor` and `/open` are gone. |
| journey | ✓ 24 steps · 52 s · slowest request 0.07 s · slowest loop hold 15 ms |
| boot | ✓ the cut on screen 0.75 s after a proxy lands |
| hygiene | ✓ clean |

The three flaky tests:
- `test_ai_honest_ui::test_a_redo_brings_back_the_same_design_with_no_model_call` (**REDO's own test**): the set-aside design's dir still existed after a new edit, `assert not (fx_home()/fx_id).exists()`.
- `test_tx_playback::test_playing_through_a_graded_dissolve_has_no_hole`.
- `test_vfx_playback::test_a_sharpened_and_a_blurred_shot_play_with_no_hold`.

My session drove the board during most of the suite, so the load is partly mine. The lead's own gate on this rev read 0 flaky. Its gate on `612440a` an hour earlier was RED.

**Standalone, after the gate (`perf/perf.log`), at load 1.8-8.7:**
- `test_vfx_playback` **passed 4 of 5.** Run 3 failed on Gaussian Blur at 1920: 116.6 ms on the frame clock, 2 holds, at load 5.6. Verdict 13 measured it failing 4 of 6.
- `test_tx_playback`'s graded-dissolve test **passed 5 of 5.** The plain graded cut read 34-66 ms on its page clock (66.1 ms once, at load 7.6).

## Rubric changes log: rulings on the six entries since verdict 13

1. **"Logged what the Lumetri timing tests skip": honest.** The skip above 2× the cores is now written down. TESTS2's frame bar is asserted at any load, so the skip no longer hides a late preview.
2. **REDO: honest for a landed design; the in-flight path does not do what the entry says** (`fxredo.log`).
   - **(A) Verified.** A "DROP IN" caption landed in 23 s as `fx_85c67b90`. Ctrl+Z read "the design is discarded" and the design left the list. Ctrl+Shift+Z brought **the same id back in 145 ms** ("the design is back"), with **0 model calls** in my ledger.
   - **(B) Failed.** I accepted a "white border" design request and pressed Ctrl+Z about 1 s later, while its first design call was running.
     - The ledger shows one design call (09:17:47) and never the second. No design landed and none was set aside: no fx file.
     - 2.5 min later, Ctrl+Shift+Z read **"redone: AI: effect"**, and then nothing. There was no design, no "asks the designer again" and no call in the next 20 s.
     - The entry says such a design "is set aside too and comes back with no call", or else the redo re-asks. Here the request was lost while the toast said "redone". In `fx.js` `redoAsks`, an item cancelled while pressed is neither `withdrawn`, `skipped` nor `discarded`, so it is dropped.
     - This was one trial. The REDO test itself also flaked in my gate.
3. **INDEXPANEL screens budget +1/+1: honest.** The gate measures exactly the logged counts. It is the one tab Karl asked for.
4. **SNAP, two measurement choices.**
   - **(1) The asked wait subtracted: honest.** It is one declared long-poll, which was always excused by name.
   - **(2) Late snapshot answers during an import's settle: honest as logged, with a coverage caveat.**
     - This is an app behaviour, not a clock: reads during an import answer from the last snapshot and fill in later.
     - The suite's TestClient never takes that path (`server.py:4035-4038`), so only the 4 tests in `test_import_snap` cover what a user gets mid-import.
     - On Killington, the one over-budget request in the restarted server's whole session was **GET `/api/firstpass` at 525.9 ms**, `cause: waiting` on "the firstpass snapshot's rebuild". It came 0.7 s after my own import POST, which by design is served fresh.
5. **FOLDIN: `/floor` and `/open` leave the screens budget: honest.**
   - `/open` and `/floor` answer **307 → `/`**, and `/open.js`, `/floor.js`, `/open.html` and `/floor.html` answer 404.
   - My crawl clicked **45 enabled menu items** (a clip selected) plus the Next chip: **0 left `/`**, 0 popups. There is no `a[href]` on the page.
6. **TESTS2's Lumetri frame bar: honest, stricter.**
   - `arm()` now starts at `P.first`, the first value-changing input (`test_lumetri_ui.py:218-229`).
   - `shown_by_frame` asserts the change shows by the 2nd animation frame at any load.

**Ruling asked of me: PLAY8b's finding on `test_tx_playback`'s 67 ms bar. The bar's number is honest; its clock is not.**
- **How it measures.** The test reads the graded canvas by sampling `grade.draws` inside `requestAnimationFrame` with `performance.now()`: the page-callback clock (`test_tx_playback.py:178,282-286`).
- **Why that clock is wrong for this bar.** In wave 15 the loop moved `test_perf_latency` off that clock, onto the rAF timestamp and the worker's clock, because the page clock overstates holds by up to ~12 ms.
  - The bar, 67 ms, is exactly two frame periods at 29.97, so a single dropped frame is exactly 66.7 ms.
  - On a clock with ±1 vsync of jitter, a single drop reads 53-78 ms. The test passes it about half the time and fails it the other half: it neither forbids one dropped frame nor allows it.
  - That is why the gate goes red about 20% of the time, in base and head alike.
- **Ask.**
  - Measure the canvas on the rAF timestamp and the worker clock, as `test_perf_latency` does.
  - State the bar in frames: every source frame of the cut shown, none held past its cadence. Dimension 6 says "no visible gap".
  - Then fix the real defect PLAY8b found: the plain graded cut drops one frame in about 10% of plays on a loaded box (punch 4).
- **On Killington.** Plain graded cuts that do not touch the slow shot (cuts 6 and 7) had **0 holds over 67.5 ms in 12 plays** at both sizes, on the frame clock (`pb-plain*.log`).

**The 59.94 design question (PLAY8 left it open): it is a defect, measured** (`fstep.py`).
- **The test.** In the 59.94 Killington sequence, parked on CLIP_01 (59.94 native), I pressed → ten times.
- **What it showed.** The Program's picture **changed on only 5 of 10 one-frame steps, strictly alternating** (mean |diff| 0 / 2.7 / 0 / 2.4 / …). The live video's `currentTime` moved 16.7 ms each step, but the 29.97 proxy has no frame there.
- **Why it matters.** The export renders the original, so every other frame you park or trim on is not the frame you export. Premiere's proxies keep the clip's own rate, so frame-stepping shows every frame.
- **Also seen.** A 29.97 clip in the same 59.94 sequence (CLIP_07, which starts on a half frame) held its frames in an irregular 1/2/3 pattern rather than 2:2. I did not trace that one.

## Driving it like an editor

**Cold land (`cold.log`, `01-cold-5s-1440/1920.png`).**
- Rows in 0.16-0.21 s, a picture in 0.27-0.32 s. One blue app action ("Run first pass"), plus the tour card's Next.
- 0 page errors. One console 503 for CLIP_01's preview while it builds.
- The ruler's last label is still clipped at the Timeline's right edge at both sizes ("00:00:20:0").

**K4 (a): the Index panel (`k4cold.log`, `k4mid.py`, `k4post.log`, `22-25`, `40-46-*.png`).**
- **Where it is.** Window › Index, File › Index…, and the Project group's 4th tab.
- **Cold.** Rows came 114 ms after the menu click. It read "12 clips · heard 0 · looked 0 · close looks 0 · free on your plan", with five stage columns, Cost and Ran.
- **Mid-pass.** Each cell showed queued, running % or a running clock, with hovers.
- **After the pass.**
  - CLIP_04's hovers: "Heard: done Oct 20 08:42 · 225 words", and "Looked at: done … one frame every 4 s · 3 sheets · free on your Claude plan — about $0.69 if using the API".
  - Its detail: Telemetry "11 peaks", Close looks "3 windows".
- **Re-tune (the K4 bar).**
  - I selected CLIP_04 and chose Look, every 2 s. The server quote arrived in 149 ms: "free on your plan" (hover: "1 look · 6 sheets … about $1.62"), shown before Run.
  - Run read "Added to the index running now — CLIP_04: looked at (looks at 2 s)".
  - The cell went 1% → 99% → **"✓ 2 s" in 134 s**. The summary became **"looked 12 (11 at 4 s, 1 at 2 s)"**, the row is tagged "2 s" (tuned), and Events logged "CLIP_04 picks asked again". A reload keeps all of it.
- **Bin and selection scopes are priced too:**
  - 3 clips, Hear: "free (runs on this machine)".
  - Close looks: "3 close looks … about $3.29".
  - Everything: "3 looks · 7 sheets + 3 close looks … about $5.18".
- **Against:**
  - **(1) Two readouts disagreed mid-pass.** At the same moment, `/api/firstpass` said "heard 11/12" and the Index panel said "heard 1". All 11 Heard cells read "running 1:20" for about 1-2 min, because the hear stage is one batch, the panel follows the journal, and the pass counts sidecars.
  - **(2) The re-look leaves CLIP_04's close looks** from the old 4 s look.
  - **(3) Nothing teaches it.**
    - No tour step mentions it.
    - The first-pass panel's "Index" step has no link to it.
    - The AI menu has no Index / "look again" item, though a look is a model call.
    - Its Run button has a plain `title` tooltip, not the AI hover card (what / cost / changes).
  - **(4) It is cramped.**
    - 438 px wide at both 1440 and 1920. Only 7 rows show when a clip's detail is open.
    - The foot wraps "Stop" onto a second row, and the summary wraps to 2 lines (`42-*.png`).

**K4 (b): the old pages folded in (`k4cold.log`, `crawl.py`, `foldin.log`, `50-56-*.png`).**
- **Routes and menus.**
  - The routes 307 to the editor.
  - The File menu is now Import… / Media Browser / Open bin or cut / **Index…** / **Project Settings…** / Export….
  - Window has **Index** and **Events**.
- **Project Settings** opened in 99 ms: budget cap, "This project: ~$15.64 API-equivalent · free on your plan", index order, workers per stage, and Reset.
- **Events** opened in 62 ms: 20 journal rows and the journal's path.
- **Selects carries /floor's verdicts.**
  - Each row has ✓ ✗.
  - The right-click menu has Keep / Keep as hero / Reject / Later / Note… / Open in Source / "Reject this and the rest of CLIP_01 (2)" / "Later for …" / Undo.
  - Later marks the row. A note shows "your note: …". The menu's Undo took the note back.
- **Against: a verdict is not on Ctrl+Z.** I rejected a pick with its ✗; the toast read "rejected … right-click › Undo". Ctrl+Z right after answered **"nothing to undo"**, and the pick stayed rejected.
  - On `/floor`, Ctrl+Z undid a verdict. In Premiere, Ctrl+Z undoes whatever you just did.
  - With any timeline edit in the history, Ctrl+Z would undo that edit instead of the verdict.

**First pass (`pass.log`, `passresume.log`).**
- The panel opens in 66 ms. **Stop took 78 ms**, and its children were gone at 1.5 s. The label read "Re-run first pass" after Stop and after a reload.
- I stopped the server mid-pass (the ledger finding, Section S) and restarted it. The panel said exactly what was left: "Stopped 1 min ago · heard 12/12 · looked 8/12. Re-run first pass looks at 4 more, then picks, then the cut."
- The resumed pass took **182 s**. The whole pass was about 6 min of run time. It proposed 18 shots (3:06) and 73 picks.

**Review (`review.log`).**
- 18 ghosts. I rejected 2 (ghost ✗ and table ✗).
- "Accept 16 of 18" landed exactly the proposal minus those 2, with 2 PUTs.
- A drop-then-grade landed in 77 ms and survived a reload.
- Selects at 1440 is now **574 px with Name, Media In, Duration, Reason, Usage** (verdict 13 punch 5 done).

**Editing (`edit-1440.log`, load 4.5-6.9).**
- Seeks 63-110 ms. Three plays across cuts: 0 gaps over 67 ms and **`wrong_clip_frames` 0** (verdict 13 punch 9b done). The 0.2 s shot played clean twice; the third rep is my script's artefact.
- The keys (+/−, \, arrows, J/K/L, I/O, M, C/V, Ctrl+K, undo/redo), ripple, roll, move, Selects `,` ×3, Add/Delete Track, a V3 overlay and overwrite past the end: each worked and is one undo. A reload kept the cut exactly.
- Timings: clicks 27-40 ms; keys 63-82 ms including Playwright's round trip; `effects.set` 11-20 ms.

**AI asks on Killington, in my own words, real model (`ask.log`, `ask-export.log`):**

| family | my words | answer | review | land |
|---|---|---|---|---|
| timeline | "Cut the third shot down to about a second and a half, then move the second shot to the very end" | 7.4 s. It said so honestly: "Shot 3 is only 0.2 s long, so I can't cut it down. Instead this extends its out point…" | trim + move rows, ghosts and a move arrow; I rejected the move → "1 of 2 applied" | disk unchanged until Accept; one Ctrl+Z exact; redo exact |
| keyframes | "Fade the last shot out to black over its final second with opacity keyframes" | 12.4 s: Opacity 100 → 0 over 0.98 s, 2 keys | ◐ After | one undo exact |
| colour (right-click, chip "Shot 5 · CLIP_02") | "The snow looks grey and flat here, make it whiter and add a bit of contrast" | 8.3 s: Exposure, Whites, Contrast, Blacks | ◐; I rejected 1 → "3 of 4" | one undo exact |
| transition | "Put a short cross dissolve, about half a second, between shots 2 and 3" | 6.9 s | row | one undo exact |
| speed (right-click) | "Play this shot at 50 percent speed" | 5.9 s: "runs about 27.2 s instead of 13.6 s" | row | one undo exact |
| In/Out + marker | "Set In at the start of shot 2 and Out at the end of shot 4, and drop a blue marker called jump…" | 6.4 s | 2 rows | one undo exact |
| title | "Put a white KILLINGTON 2026 title in the middle of the first shot for two seconds" | 6.4 s: `design_effect` | row; "1 effect being designed" | one undo |
| export | "Export a 720p version of the whole sequence for my phone" | 7.4 s: "writes …/phone_720p.mp4 · 1280×720 · 59.94 fps · 00:04:26:11" | row | ran only on Accept, 90 s. `ffprobe`: 1280×720, 60000/1001, **15971 frames = 266.449 s** (sequence 266.449), **chapters "Start" and "jump"** |

**Footage in, mid-session (`import.log`).**
- Ctrl+I opened the dialog in **35 ms**. Two clips (KX_05, KX_10) imported; rows at 0.26 s, with fps, size and timecode.
- KX_05 was cut at once with `,` (then undone).
- An OS-style drop of a 480 MB file read "Imported 1 clip" at **1.54 s**.
- The Index panel shows the new clips heard ✓, telemetry ✓ and preview ✓ on their own, with the priced look not run.
- The offer read "3 new clips: add them to the cut? · Looking at them: free on your plan · Re-run first pass… · Not now".

**Re-run (`rerun.log`, `80-84-*.png`).**
- The panel read "Keeps 11 edited shots (9 cut by hand, 2 by the AI) · keeps the grade on every shot it keeps · may replace 11". It listed each kept shot, including "speed changed by the AI".
- It took **3 clicks and 77 s, and kept 11 of 11 edited shots exactly.**
- It named each new clip with why it was left out ("KX_05: it is a duplicate of CLIP_05…").
- Its Things to check no longer question the 0.5× locked shot (verdict 13 punch 10b done).

**Playback (`pb-plain*.log`, `slow.log`, `slowq.log`; the canvas by its frame clock, the live buffer by rVFC):**
- **Plain graded cuts (6 and 7):** **0/12 plays with a hold.**
- **Cuts into and out of the 0.5× shot:** 4/12 plays with an 83-100 ms hold.
- **Inside the 0.5× shot** (CLIP_04, 5 s plays):
  - 8 of 8 plays held frames, at load 3.4-7.4: 1, 2, 1, 12 holds at 1440; 1, 5, 23, 20 at 1920.
  - **Again on a quiet box (load 1.7-2.8): 8 of 8, with 2-12 holds of 83 ms each per play.**
  - The live buffer's own `expectedDisplayTime` holds too (83-111 ms). So PLAY8's fix (draw a frame in the animation frame it is shown in) does not cover this.
  - PLAY8's own claim was "0.5x canvas holds 108 → 2 in 28 plays" (CLIP_09). On my AI-slowed CLIP_04 the slow shot judders on every play.

**The K3 walk: an empty project, a fresh browser, no doc (`fresh.log`, `80-97-*.png`).**
- **Landing:** every panel names Import; the header reads "Next: Import footage (Ctrl+I)".
- **Import:** tour Show me opened Import, and Import Folder gave rows at 0.24 s. A 480 MB drop landed at 2.6 s and was done at 4.3 s.
- **First pass:** 70 s. Afterwards the Index tab read "3 clips · heard 3 · looked 3 at 4 s · close looks 1 · 2 running · free on your plan".
- **Why:** on the pick markers and the Selects row hover.
- **Review:** I rejected one ghost → "Accept 2 of 3".
- **Asks:** "Cut this down to roughly 25 seconds and start on the most exciting bit" (11.0 s, a plan, accepted, 26.6 s), and "this shot looks a little dark, lift it and cool it down slightly" (4.9 s, 2 ops). "Working" showed by 80 ms both times.
- **Adjust by hand:** typing 80 in Saturation's value is one undo (`ec.py`). My first script grabbed the range slider instead; that was my error.
- **Export:** 720p / 29.97 gave **797 frames = 26.593 s** exactly.
- **No guess.** The step-5 card sat at (819, 256), clear of the AI's result line (verdict 13 punch 8 done).

## The 16 dimensions

| # | dimension | score | evidence | vs Premiere Pro |
|---|---|---|---|---|
| 1 | Orientation | **9** | Rows 0.16-0.21 s, a picture 0.27-0.32 s, one blue app action, 0 page errors; every empty panel names Import (`80-fresh-empty-land.png`). **Nothing leaves the editor** (45 menu items, Next, routes 307). Against: the ruler's last label clipped at both sizes. | Premiere's one workspace, now matched. |
| 2 | First pass visible + re-runnable (K1) | **9** | Stop 78 ms; a resumed pass says exactly what is left; re-run 3 clicks, 77 s, 11/11 exact, names each new clip; **the index is visible per clip** (stages, when, interval, cost) and re-tunable with the price first (CLIP_04 at 2 s in 134 s). Against: the Index panel said "heard 1" while the pass said "heard 11/12"; the first-pass panel's Index step does not open the Index panel. | n/a; a clear re-runnable AI step with its index in view. |
| 3 | Footage in, no ceremony | **9** | Drag + grade 77 ms; Selects `,` ×3; an imported clip cut at once. | Premiere's bin and three-point editing. |
| 4 | Workspace | **9** | Program 674×379 at 1440; Selects 574 px with Media In / Reason on Killington (fixed); the Index panel docked as a Project tab. Against: the Index panel is 438 px at both sizes, its foot wraps and 7 rows show with a detail open. | Premiere's Editing workspace; its Metadata panel is resizable and scrolls. |
| 5 | Timeline craft | **9** | The key map; ripple / roll / move / razor / tracks / overwrite past the end, each one undo; markers; reload exact (`edit-1440.log`). | Matched for the basics. |
| 6 | Monitors and playback | **7** | Seeks 63-110 ms; plain cuts 0/12 holds; 0 wrong-clip frames at cuts; dissolve test 5/5 alone; `test_vfx_playback` 4/5 alone (was 2/6). Against: **the 0.5× shot judders on every play (8/8, 2-12 holds of 83 ms per 5 s, even at load 1.7-2.8)**; **59.94 clips show a new picture on only half the frame steps** (`fstep.py`: 5/10, alternating), so parked and trim frames differ from the export; one dropped frame on ~10% of plain graded cuts in the synthetic test (PLAY8b), whose bar's clock is noisy. | Premiere steps every frame of a 59.94 clip and plays slow motion evenly. |
| 7 | Inspector, effects, colour | **8** | Effect Controls typing (Saturation 100 → 80, one undo); Lumetri ops from the AI land as Effect Controls values; Sharpen / Blur in the grade. Against: five video effects in all (unchanged). | Most of Lumetri; a sliver of Premiere's Effects panel. |
| 8 | Export | **9** | Two exports exact (15971 frames = 266.449 s; 797 = 26.593 s); the AI export ran only on Accept; chapters from named markers. Against: no codec / audio settings. | Premiere's Export Settings basics. |
| 9 | Looks like a shipped pro tool | **8** | No exits, no ids, the tour card off the AI line, Selects whole at 1440. Left: the ruler's last label clipped (1440 and 1920); the Index panel cramped (wrapped foot, 2-line summary, `42-*.png`); journey step 8 shows "Next: Read the proposal…" beside "applied 4 of 5 shots". | Close in layout and density; Premiere shows none of these. |
| 10 | Responsive, reliable | **8** | Clicks 27-40 ms; Ctrl+I 35 ms; Project Settings 99 ms; Events 62 ms; **1 over-budget request in the restarted Killington server's whole session** (08:46-09:35: pass, edits, 11 asks, export, import, re-run; 526 ms, `waiting`); every timeline and AI edit one undo; the design redo restores the same id in 145 ms. Against: **a Selects verdict is not on Ctrl+Z** ("nothing to undo" right after a reject); **a design undone in flight comes back as nothing** while the toast says "redone"; 3 flaky tests in my gate. | Premiere's undo covers bin actions too. |
| 11 | Proposals are reviewable edits | **9** | 18 ghosts with ✓ / ✗; 16/18 exact; why on hover; the re-run's diff; free on your plan. | Premiere-like review. |
| 12 | The journey | **9** | 24 steps green in my gate, 52 s, slowest request 0.07 s, loop hold 15 ms, no retry. | n/a |
| 13 | Footage in, from anywhere | **9** | Ctrl+I 35 ms; Media Browser; Project-panel Import; drop 1.54 s (480 MB); facts at once; the free index runs on its own, the look waits; the "3 new clips" offer; the re-run names each new clip. Against: a folder dropped from the OS is unverified. | Premiere's Import and Media Browser. |
| 14 | One way to ask the AI | **9** | Always-visible prompt; Ctrl+J; the scope chip follows the right-click; working by 80 ms; answers 4.9-12.4 s; AI menu with hover cards. Against: **the Index panel's look / close look (model calls) are not in the AI menu.** | n/a |
| 15 | The AI does everything, as reviewable edits | **9** | 8 families on the real model (trim / move, opacity keys, Lumetri, dissolve, speed, In/Out + named marker, designed title, export). Each previews; ✓ / ✗ per op; disk unchanged during every review; one Ctrl+Z exact 7/7; redo exact; the landed design redo makes 0 calls. Against: the in-flight design redo loses the request. | n/a |
| 16 | Easy to learn | **8** | 6-step tour on the real workspace with Show me; my K3 walk had no guess; hovers say what / cost / changes; empty states; **K4 (a)'s Index panel exists and works.** Against: **nothing teaches the Index panel** (no tour step, no link from the first-pass panel, no AI menu item, no hover card on its Run), and Karl's K4 was exactly "not clear what indexing was run"; Selects' verdict undo is in a right-click menu only. | Premiere's Project and Metadata panels, with AI on top. |

## Section K
- **K1 "not clear how to re run the first pass": RESOLVED**, as in verdicts 3-13: the header control, the AI menu, the "3 new clips" offer, and a panel that says what it keeps and may replace. 3 clicks, 11/11 kept.
- **K2 "not premier pro quality": OPEN (Karl's call).** His last look, at `b12b979`, produced K4, not a resolution.
- **K3 "AI-first, easy to learn, add footage obviously": OPEN (Karl's call).** It is delivered as far as I can test. My K3 walk had no guess.
- **K4: OPEN (Karl's call); both parts are delivered on `799d8b8` as far as I can test.**
  - **(a)** An Index panel in the workspace shows each clip's heard / looked (at what interval) / close looks / telemetry / preview, with when, cost on the backend and a detail. It re-runs or re-tunes a clip, a selection or the bin, priced before Run. Verified by re-tuning CLIP_04 to 2 s on Killington.
  - **(b)** `/open` and `/floor` are gone (307). No menu item, chip or link leaves `/`. Their features are in the editor: Project Settings, Events, Selects verdicts and notes, and Index.
  - Karl has not seen either. His preview (8766) is a b12b979 server under wave-17 pages, and FOR-KARL names `992ca33`.

## Section S
- **Inference only through `roughcut.inference`; no model-ID literal outside `config.py`: intact.**
  - A grep of the snapshot's `.py/.js/.html` (excluding tests and `config.py`) finds no `claude-*` ID.
  - `import anthropic` appears only at `roughcut/inference.py:592`. Gate hygiene is clean.
- **No price reads as money spent on `claude_cli`: intact.**
  - Every control reads "free on your plan" or "no charge on your plan", including the Index panel's cells ("free", with the API estimate in the hover).
  - Project Settings shows "~$15.64 API-equivalent · free on your plan" as visible text beside the budget cap. That is an estimate next to the cap it governs, not spending.
- **Karl's footage and work dirs never written: BROKEN by the judge's server (my session), through an app default.**
  - `roughcut/config.py:138-140` puts the model-call ledger at `~/work/roughcut-ledger.jsonl` (`~/work` → `/mnt/roughcut/work`, Karl's). `conftest.py:37-56` calls this "the file the project's spend is judged from". It ignores `--work`.
  - The suite and the journey redirect it (`conftest.py:57`, `tools/journey.py:2068`). JUDGE.md's server command does not.
  - So my first server, run exactly as briefed, appended **about 21 lines (08:41-08:46)** of claude_cli calls to Karl's ledger. Two more lines at 08:37 are unattributed.
  - I restarted with `ROUGHCUT_LEDGER=judge14/ledger.jsonl` at 08:46. Karl's ledger has stayed at 2144 lines since.
  - Every earlier judge likely did the same.
  - It is a log, not footage or a project. But it is his real spend record, and the standing decision is absolute.
- **INTAKE decisions: intact.** The priced look never ran unasked on imported clips; the free stages ran on their own.
- **The pro controls stay: intact.** Wave 17 only added (Index, Project Settings, Events, Selects verdicts).
- **No Co-Authored-By; CHANGELOG on every commit: intact.** In `b12b979..799d8b8`: 0 Co-Authored-By lines; 39 of 39 non-merge commits touch `CHANGELOG.md`.

## Verdict 13's punch list, item by item
1. **K4 (a), the Index panel: DONE.** Per-clip stages, when, interval and cost; re-run / re-tune for a clip, a selection or the bin, priced first; a driven re-tune of CLIP_04 on Killington. (Leftovers are under punch 6 below.)
2. **K4 (b), fold in and remove `/open` and `/floor`: DONE.** Routes 307, 45 menu items crawled, no `a[href]`, every audited feature found in the editor. The one regression: Selects verdicts are off Ctrl+Z.
3. **Effects play with no dropped frame: PARTLY.** `test_vfx_playback` passed 4/5 alone (was 2/6) and flaked once in my gate; one blur hold of 116.6 ms at 1920.
4. **Zero flaky, three gates in a row: NOT DONE.**
   - The lead's gate on `612440a` was RED, its gate on `799d8b8` had 0 flaky, and mine had 3 flaky under my session's load.
   - REDO's new test is one of the three.
5. **Selects at 1440 on a real project: DONE.** 574 px with Media In / Duration / Reason / Usage.
6. **A redo restores, does not re-ask: DONE for a landed design** (same id, 145 ms, 0 calls). **NOT DONE for a design undone in flight:** it comes back as nothing, under a "redone" toast.
7. **Lumetri timing from the first change, and the log: DONE.**
8. **The tour card off the AI's line: DONE** (not overlapping in my walk).
9. **Playback leftovers: PARTLY.**
   - Done: wrong-clip frames at cuts 0 (was 1-2 per cut); play-start holds 0 on plain cuts.
   - Not done: the 0.5× cadence (8/8 plays judder on CLIP_04, even on a quiet box).
10. **Import-time requests and honest causes: DONE nearly.**
    - Done: the `waiting` cause names the snapshot rebuild; the re-run no longer questions a speed-changed shot.
    - Left: 1 over-budget row (`/api/firstpass` 526 ms right after the import POST).

## Blockers
1. **K2, K3 and K4 are open.** Only Karl can close them, and he has not seen this rev: 8766 is mixed-version, and FOR-KARL is stale.
2. **Dimension 6 is below 8 (7).**
   - The 0.5× shot judders on every play.
   - 59.94 clips park on the wrong frame every other step.
3. **Section S: Karl's `~/work` was written by the judge's server.** About 21 call-ledger lines, because the app writes its ledger to `~/work` whatever `--work` says.

The gate is GREEN (3 flaky under my load). Every other dimension is at 8 or 9.

## THE THREE BIGGEST GAPS TO PREMIERE
1. **Frame accuracy and slow motion.**
   - On the 60 fps GoPro clips, stepping one frame at a time shows a new picture only every other press. Half the frames you park or trim on are not the frame you export.
   - Half-speed shots stutter a little on every play, even on a quiet machine.
2. **The new Index panel works but hides.**
   - It now shows what was heard and looked at per clip, when, how often and at what cost, and you can redo or retune a clip with the price shown first. That is what Karl asked for.
   - But nothing points you to it: not the walk-through, not the first-pass panel, not the AI menu. It is a narrow, cramped tab. Mid-pass it can disagree with the pass's own progress.
3. **Undo has two holes.**
   - Rejecting or marking a pick in Selects can only be undone from its right-click menu; Ctrl+Z says "nothing to undo".
   - Undoing an AI effect request in its first second, then redoing it, brings back nothing, while the app says "redone".

## PUNCH LIST (most important first; [files] = lane ownership)

1. **Put the judged rev in front of Karl; only he can close K2-K4.**
   - **Files:** [the `roughcut-redesign` service: restart 8766 from a frozen `git archive` of the judged rev, not the live worktree]; [`judge/FOR-KARL.md`].
   - **Done =**
     - 8766's server starts after the snapshot and serves that snapshot's static files (`readlink /proc/<pid>/cwd` = `snap-<rev>`).
     - FOR-KARL names the rev and says, for K4: the Project group's Index tab (or Window › Index); pick a clip, Look, every 2 s, Run; File has no "Footage and index…" or "Review the pass…" any more; Selects ✓ / ✗ and right-click replace the pass page.
2. **59.94 clips show every frame when parked and trimmed.**
   - **Files:** [`app/server.py` proxy build: proxies at the source's own rate for 59.94 sources (`PROXY_V` bump), or a parked-frame still from the original when the proxy has no frame at t]; [`app/static/app.js` / `grade.js` parked-frame path]; [new `app/tests/test_parked_frame_5994.py`].
   - **Done =**
     - `judge14/fstep.py`: 10 one-frame steps on a 59.94 clip in a 59.94 sequence change the picture 10/10.
     - The parked frame equals the export's frame 48/48 at 59.94 (PLAY8's own measure).
     - A 29.97 clip in a 59.94 sequence holds exactly 2:2.
3. **Half-speed plays evenly.**
   - **Files:** [`app/static/app.js` the player's rate path for a 0.5× shot (the live buffer's `expectedDisplayTime` itself holds 83-111 ms; e.g. step the proxy by `currentTime` on a 66.7 ms clock rather than `playbackRate = 0.5`)]; [`grade.js` `armFrames`]; [`app/tests/test_cut_frames.py` with a 0.5× shot of a 4K 29.97 grain proxy].
   - **Done =** `judge14/slow.py` (`SLOW=<the 0.5× shot>`, 4 reps × 5 s at 1440 and 1920, load ≤ 3) shows 0 holds over 67.5 ms on the frame clock, and the test asserts it.
4. **The dissolve / plain-cut speed bar on an honest clock, then fix the one dropped frame.**
   - **Files:** [`app/tests/test_tx_playback.py`: the canvas on the rAF timestamp and the worker clock, as `test_perf_latency`, and the bar in frames, every source frame of the cut shown with none held past its cadence]; [`app/static/app.js` the roll at a plain cut (PLAY8b: the incoming rolled buffer's first frames come late under load)]; [`REDESIGN-GOAL.md` log].
   - **Done =** the graded-dissolve test passes 20/20 alone at load 4-8 on the new clock.
5. **Selects verdicts are on Ctrl+Z.**
   - **Files:** [`app/static/passreview.js` `verdict` / `undo`]; [`app/static/timeline-keys.js` / the board's undo stack: one history with timeline edits]; [`app/tests/test_passreview_ui.py`].
   - **Done =** reject a pick, then Ctrl+Z restores it and Ctrl+Shift+Z re-rejects. After a timeline trim then a reject, Ctrl+Z undoes the reject first, then the trim. The same for Later, Note and hero.
6. **Teach and tie in the Index panel.**
   - **Files:** [`app/static/tour.js`: the first-pass step points at the Index tab ("see what was heard and looked at, re-tune a clip")]; [`app/static/firstpass.js`: the Index step links "each clip in the Index panel"]; [`app/static/dock.js` AI menu: "Look again / re-tune the index…" → `rcIndex.open()`]; [`app/static/indexpanel.js`: Run gets the AI hover card, saying what it replaces ("CLIP_04's looked moments; its picks are re-ranked; its close looks are kept from the old look" or re-run them) and its cost]; [`app/server.py` `/api/index/clips`: a batch hear stage reports per clip from its sidecars, as `/api/firstpass` counts].
   - **Done =** during a pass, the Index summary and `/api/firstpass` agree within one poll (heard N/12). The AI menu lists it. Hovering Run shows what / cost / changes. A fresh-browser tour mentions it.
7. **The design redo never loses a request.**
   - **Files:** [`app/static/fx.js` `withdraw` / `redoAsks`: an item pressed and cancelled before landing counts as never made, so it re-asks, free on the plan or priced first on the API]; [`app/server.py` the fx job's cancel]; [`app/tests/test_ai_honest_ui.py`: this case, plus the race in `test_a_redo_brings_back_the_same_design_with_no_model_call` (the set-aside dir still on disk right after a new edit)].
   - **Done =** accept a design, Ctrl+Z within 1 s, wait 60 s, Ctrl+Shift+Z: a design lands (restored, or re-asked with "asks the designer again · free on your plan"), never a bare "redone". The REDO test passes 10/10 alone and in 3 full gates.
8. **The Index panel at a working size.**
   - **Files:** [`app/static/dock.js`: open Index as wide as Selects (≥ 560 px) at 1440 and 1920]; [`app/static/indexpanel.css`: the foot on one row, the summary on one line].
   - **Done =** at 1440, the Index panel is ≥ 560 px with ≥ 10 rows visible while a clip's detail is open, the foot is one row, and nothing wraps (a `test_workspace` check).
9. **The call ledger stays inside `--work`.**
   - **Files:** [`roughcut/config.py` `ledger_path` / `app/server.py` startup: with `--work`, the ledger defaults to `<work>/roughcut-ledger.jsonl` unless `ROUGHCUT_LEDGER` is set]; [`judge/JUDGE.md`: the server line sets `ROUGHCUT_LEDGER=$J/ledger.jsonl` until then].
   - **Done =** a judge server with `--work` under `~/roughcut-loop` runs a first pass and `wc -l ~/work/roughcut-ledger.jsonl` is unchanged (a test that starts the server with a tmp `--work` and a monkeypatched HOME).
10. **Three clean gates, and the small leftovers.**
    - **Files:** [`app/static/timeline.js` ruler: the last label kept whole or dropped at the panel's right edge, at 1440 and 1920]; [`app/server.py` the status read right after the client's own import POST (526 ms on the firstpass snapshot's rebuild): answer the prior snapshot plus "importing" rather than wait]; [`app/static/grade.js` Gaussian Blur at 1920 (116.6 ms, 1 of 5 alone)]; [whatever flakes].
    - **Done =** three consecutive full gates with "0 flaky", and `/api/debug/slow` `over_budget` empty after a mid-session import of two 4K clips.

## What I could not verify
- **A GPU browser:** every playback number is headless Chromium with software compositing.
- **A folder dropped from the OS** (headless Chromium cannot fake `webkitGetAsEntry`), and Upload Folder… through the chooser.
- **The `anthropic_api` backend:** the Index panel's and the redo's price-first confirms there (not run).
- **Karl's 8765 and 8766** beyond their processes and start times (I sent them no request).
- **Why a 29.97 clip in the 59.94 sequence held 1/2/3 frames** instead of 2:2 when frame-stepped (CLIP_07, which starts on a half frame).
- **PLAY8's CLIP_09 0.5× numbers:** I measured an AI-slowed CLIP_04 instead.
- **Audio by ear.** The meters moved between −24.5 and −9.5 dBFS while playing.
