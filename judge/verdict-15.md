VERDICT: FAIL

# Roughcut redesign: verdict 15

- **Rev judged:** `5138865`, the branch HEAD as briefed (wave 18 merged, plus PLAY9b and the lead's half-speed bar; 31 commits, 25 non-merge, since verdict 14's `799d8b8`).
  - While I judged, the branch moved to `a4f0e14`, a docs-only commit (`judge/LOOP.md` and `CHANGELOG.md`, +9 lines). Everything here is `5138865`.
  - My gate archived it to `~/roughcut-loop/snap-5138865`. I served that snapshot on 127.0.0.1:8781 only:
    - **Killington:** `--footage ~/footage/killington-neutral --work judge15/work`, with `ROUGHCUT_LEDGER=judge15/ledger.jsonl` as JUDGE.md now says.
    - **The K3 walk:** `--footage judge15/empty --work judge15/work2`, a fresh browser with no tour flag, and **no** `ROUGHCUT_LEDGER`, to test that the ledger follows `--work`.
- **Karl's live board (8765):** `35cb147`, an ancestor of `5138865`, **580 commits behind**. Its process (pid 1902724) dates from Oct 15. I sent it no request.
- **Karl's preview (8766):** pid 1552214 has run since **Oct 19 21:30 on `b12b979`'s Python**. It serves the live worktree's static files, which are now `5138865`'s.
  - So today it would show wave-18 pages on a wave-16 server.
  - `judge/FOR-KARL.md` still names `799d8b8`. I did not touch either.
- **Box load (6 cores):**
  - 3.1-3.4 at the cold land.
  - 2.4-13.3 (mean 8.8) during the first pass, with my gate's suite beside it, plus my own ~10 GB of file copies at 21:33-21:36.
  - 3-10 for editing, asks and import; 10-15.6 during the re-run.
  - **1.0-3.8 for the decisive playback runs** (`slow-c`, `pb-p2`), after my gate finished.
  - 3.4-7.6 for the standalone tests.
- **Model spend:** backend `claude_cli`, nothing billed.
  - Killington: 103 calls (one first pass, 6 asks, 1 export ask, 2 design asks plus 1 design redo, one re-run). That is $30.62 API-equivalent.
  - The K3 walk: 21 calls ($5.72 API-equivalent).
- **Cleanup:**
  - My servers are stopped (8781 free).
  - `footage-before*.txt` = `footage-after*.txt`, byte-identical.
  - Imports were copies under `judge15/import2`, `import3`, `import4`, `dropsrc` and `dropsrc2`.
  - **Karl's `~/work/roughcut-ledger.jsonl`: 2144 lines and mtime Oct 20 08:46, before and after both of my servers.**
  - The only repo write is this file, uncommitted.

## Gate: my own run, `~/roughcut-loop/gate-5138865-20261020T2126/summary.md`: **GREEN**, 3452 s

| check | result |
|---|---|
| browser | ✓ Playwright 1.64.0 / Chromium 156 |
| suite | ✓ 1866 passed, **1 flaky** (failed in the full run, passed alone), 3 skipped, 0 broken. **Perf: both runs passed.** Before the suite, at load 6.7 with 0 burners: canvas frame/worker 50.1/50.9 ms (1440) and 66.7/57.4 ms (1920). After the suite, at load 7.2: 66.7/60.3 and 50.1/65.5. |
| screens | ✓ 5 PNGs, 0 page errors. `/` 110·58, `/ shot` 139·73, `/ film` 118·57, `/ jobs` 119·59, `/ fx` 144·61: exactly INDEX2's "no budget change". |
| journey | ✓ 24 steps · 52 s · slowest request 0.07 s · slowest loop hold 22 ms |
| boot | ✓ the cut on screen 0.72 s after a proxy lands |
| hygiene | ✓ clean |

- **The flaky test** was `test_vfx_playback::test_a_sharpened_and_a_blurred_shot_play_with_no_hold`.
  - It failed on sharpen at 1440 (83.3 ms on the frame clock, load 4.3).
  - In the same run, blur at 1920 also held: 83.3 ms on the frame clock and 82.4 ms on the worker clock.
- **The 3 skips:**
  - Two of `test_cut_frames`' three new half-speed cases (`graded-1920`, `ungraded-1440`) skipped at load 12-13: "holds measured, not judged". Both read 0 holds.
  - The live dictation test.
  - My session (first pass, re-run, copies) caused that load, so the gate judged one of the three half-speed cases.
- **The same rev's other gate was RED.** The lead's gate an hour earlier failed only on perf after the suite: 76.3 ms on the 1920 worker clock, at load 7.7.
  - The perf result on this rev depends on the moment it runs. That is punch 4 below.

**Standalone, after the gate (`perf/perf.log`), 4 rounds back to back at load 3.4-7.6:**
- **`test_tx_playback::…graded_dissolve…`: passed 2 of 4.** Both failures were in the test's plain-cut half, at load 7.1-8.0:
  - **run 3:** the outgoing shot's last two frames were never drawn (`skipped: [298, 299]`).
  - **run 4:** a hold of 4 display frames where the limit is 3.
- **`test_cut_frames::…half_speed…4k_proxy…` (3 cases): passed 4 of 4.** 1 play in 24 held one frame on either clock.
- **`test_vfx_playback`: passed 4 of 4** (it flaked once in the gate).

## Ruling on the lead's deviation from LOOP.md cadence (3): acceptable once, as disclosed; it buys nothing toward a PASS
- **The rule.** Cadence (3) says a judge runs only on a green gate. The lead launched me on a red one, and said so in LOOP.md and in my brief.
- **Why it was acceptable here.** The rule exists so that no judge is spent on a rev that cannot pass. This rev could not pass anyway, because K2-K4 are open. Six lanes of changes needed a verdict.
- **My own gate on the same rev is GREEN.** Both perf runs passed, at load 6.7 and 7.2. So the lead's red was the known load-sensitive fault, not a new regression.
- **Not a precedent.** A red rev is never a PASS candidate. Two gates on one rev that disagree mean the perf bar is still a coin flip at gate load (punch 4).

## Rubric changes log: rulings on the entries since verdict 14

1. **INDEX2 "heard is the pass's count": honest.**
   - During my Killington pass I read the Index summary and `/api/firstpass` side by side every ~4 s (`pass-poll.json`).
   - **113 of 114 polls agreed** on heard and looked. The one miss (heard 10 against 11, at 119 s) agreed again at the next poll: "within one poll", as logged.
2. **INDEX2 "the Index tab at Selects' width, no budget change": honest.**
   - The Index panel is **590 px at 1440 and at 1920**.
   - With CLIP_04's detail open, **10 rows show at 1440** (12 at 1920).
   - The foot is one row (28 px), the summary one line, and nothing is ellipsized (`06-index-detail-*.png`).
   - My gate measured the screens budget exact.
3. **UNDO5 "no new control": honest** (`selundo.log`).
   - A Selects ✗ read "rejected · CLIP_01 0:00.0–0:12.0 — Ctrl+Z undoes it". Ctrl+Z restored it; Ctrl+Shift+Z rejected it again.
   - After a razor and then a reject, the first Ctrl+Z took the reject and the second took the razor, back to the exact cut.
   - Later and Note are each one Ctrl+Z.
4. **POLISH12, SNAP's late answers widened: honest as far as I could test; the ruler: honest (stricter).**
   - **The import window** (`impsnap.py`). I polled `/api/clips`, `/api/firstpass` and `/api/index/clips` every 100 ms for 6 s from the Import click.
     - The import POST answered in 45 ms.
     - **48 polls after it: 0 stale.** Every read listed the new clip from the first poll on, and one body carried `importing: true`.
   - **Not tested:** the CLI-facts re-ask path.
   - **The ruler.** Its last label is whole at 1440 and at 1920 (`cold.log`).
5. **REDO2 "Ctrl+Z stops a design in flight; its redo asks again": honest** (`fxredo.log`, `kids.log`).
   - **In flight.** I accepted a "thin white border" design and pressed Ctrl+Z 0.4 s later. The toast read **"its design call was stopped, nothing is kept"**.
     - The server's `claude` children went from 9 to 0 within 0.6 s, and no fx file was written.
     - Ctrl+Shift+Z 2.5 min later read **"asks the designer again · free on your plan"**, and a design landed **16 s** later.
   - **Landed.** A landed design still comes back as the **same id in 94 ms with 0 model calls**.
   - **Not run:** the API's price confirm.
6. **PLAY9, `test_tx_playback` in frames: honest and stricter for the plain cut; lenient for the dissolve, honestly logged.**
   - The dissolve half lets one dropped frame pass, where dimension 6 says "no visible gap".
   - Under that bar, the plain-cut half still fails 2 of 4 alone at load 7-8.
   - On Killington at load 1.5-3.8, dissolves between 1x shots held 0 of 8 plays. **Dissolves out of a 0.5× shot held on 16 of 16 plays across both runs** (1-5 holds of 83-100 ms each). The test's fixture has no slowed shot at a transition.
7. **PLAY9, the new half-speed test and the 30.5 fps draw cap.**
   - **The test: honest, stricter than none.**
   - **The cap: honest as logged, and a product limit, not a regression.**
     - While playing, the graded canvas draws at most 30.5 frames a second (`grade.js:1941`, hardcoded).
     - The Program's Playback Resolution "Full" does not lift it, so a 59.94 sequence previews at 29.97 while it plays. Premiere plays 59.94 at 59.94.
     - The 29.97 proxy did the same before FRAME59.
8. **FRAME59, 59.94 proxies (PROXY_V v5): honest; stricter in effect.**
   - `fstep.py`: 10 of 10 one-frame steps on CLIP_02 (59.94) show a new picture, and CLIP_04 (29.97) in the 59.94 sequence holds strictly 2:2.
   - `test_parked_frame_5994` passes in my gate.
   - **The one-time cost on Killington:**
     - The 12 proxies took **~20 min** from server start (21:27 → 21:47, at load 6-12). The first landed at 21:32:58 and the last at 21:47:10.
     - Meanwhile the Index panel showed "queued" in its Preview column, and L on the proposal answered "building proxies in the background".
     - Karl's preview copy will pay this once.
   - `test_range_proxy`'s bound 12 → 16 is a small loosening with its measurement logged (12.8 measured; a turned picture reads ~84).
9. **The lead's half-speed bar (worker clock only, one held frame a case): honestly logged as LOOSER; lenient as a bar.**
   - It drops the frame clock entirely and allows a hold.
   - **At load ≤ 3** my `slow.py` read 0 holds on both clocks in 8 of 8 plays, which is what punch 3 asked.
   - **At load 5-9** it read 3 of 8 on the frame clock and 2 of 8 on the worker clock. At 1440 the second pair of plays (one "case") held twice, so it fails even the looser bar.
   - In my gate, 2 of its 3 cases skipped for load.

## Driving it like an editor

**Cold land (`cold.log`, `01-cold-5s-*.png`).**
- Rows in 0.23 s, a picture in 0.30-0.31 s, at both sizes, with **0 page errors and 0 bad responses**.
- The first action, "Run first pass", is in the header.
- The tour's step 2 now says "The Project panel's Index tab shows what was heard and looked at on each clip; re-tune a clip there" (`08-tour-k-2.png`).
- The AI menu lists **"Look again / re-tune the index…"**. Its hover card reads: what it opens / Cost: none, no model call / Changes: nothing by itself.
- The Index panel's Run has the AI card: "The model reads CLIP_04's contact sheets again, one frame every 4 s (3 sheets) · free on your Claude plan — about $0.81 … · Adds CLIP_04's looked-at moments; its picks are re-ranked" (`10-index-run-card-k.png`).

**First pass (`pass.log`, `11-14-*.png`).**
- **2 clicks.** The panel opened in 117 ms, and its Index step links "each clip in the Index panel", which opens the panel.
- The pass took **461 s** at load 3.3-13.3 (mean 8.8): 75 picks and 17 shots (3:04.6), with honest "Things to check".

**Review (`review.log`).**
- 17 ghosts with ✓ / ✗. I rejected 2; "Accept 15 of 17" landed exactly the proposal minus those 2.
- A drop-then-grade landed in 77 ms and survived a reload.
- Selects showed Name, Media In, Duration, Reason and Usage.

**Editing (`edit-1440.log`, `edit-lat.log`, load 1.5-3.4).**
- Seeks to the first frame took 39-80 ms. Two plays across 1x cuts had **0 gaps over 67 ms and 0 wrong-clip frames**.
- The key map works:
  - +/−, \, arrows, J/K/L, I/O, M and C/V, plus Ctrl+K with its 0.2 s guard.
  - Undo and redo, a roll edit, a move, Selects `,` ×3, and Add/Delete Track.
  - A V3 overlay, and an overwrite 10 s past the end, which made a gap.
- A reload kept the cut exactly. Clicks on a shot reached Effect Controls in 21-58 ms; keys took 41-66 ms with Playwright's round trip; `effects.set` took 8-33 ms.
- Typing 80 into Saturation was one undo (`ec.py`).
- (A 2.8 s "gap" in one short-shot log is after my script pressed Space to stop; a script artefact.)

**AI asks on Killington, in my own words, real model (`ask.log`, `ask-export.log`):**

| family | my words | answer | review | land |
|---|---|---|---|---|
| timeline | "Swap the first two shots, and take about a second off the end of the last shot" | 8.9 s: move + trim | ghosts; I rejected the trim → "1 of 2 applied" | disk unchanged until Accept; one Ctrl+Z exact; redo exact |
| keyframes | "Slow push in on shot 2: keyframe Scale from 100 at its start to 115 at its end" | 6.5 s: Scale 100 → 115, 2 keys. It said honestly: "the shot is only 0.44 s long, so the push-in will look quick" | ◐ After | one Ctrl+Z exact |
| colour (right-click, chip "Shot 3 · CLIP_02") | "This one is too blue and a bit dark. Warm it up and lift the midtones a touch" | 6.9 s: Temperature +25 and a Lumetri RGB master curve | ◐; I rejected the curve → "1 of 2" | one Ctrl+Z exact |
| transition | "Add a cross dissolve of about 20 frames between shots 4 and 5" | 7.0 s: 0.33 s Cross Dissolve | a dissolve ghost on the edit | one Ctrl+Z exact |
| speed (right-click) | "Slow this one down to half speed" | 7.5 s: "runs about 22 s instead of 11 s" | ghosts | one Ctrl+Z exact |
| export | "Export a 1080p review copy of the whole sequence" | 5.9 s: Quick 1080p, Entire Sequence → `review_copy_1080p.mp4` | a row with path, size, rate and duration | ran only on Accept, 114 s. `ffprobe`: 1920×1080, 60000/1001, **14988 frames = 250.0498 s** (sequence 250.0498), AAC 48 kHz stereo |

**Footage in, mid-session (`import.log`, `impsnap.log`).**
- Ctrl+I opened the dialog in **49 ms**. Two clips gave rows at **0.24 s**, with fps, size and timecode. KX_05 was cut at once with `,` (then undone).
- An OS-style drop of a 480 MB file read "Imported 1 clip" at **1.46 s**.
- The offer read "3 new clips: add them to the cut? · Looking at them: free on your plan". At the re-run, the 4 new clips had been heard (free) and not looked at (priced).
- **`/api/debug/slow`: the import, the drop and the second import added 0 over-budget rows.**
  - The 12 rows in the session all came at 21:34-21:39, at load 9.8-10.9: during the pass, my gate's suite and my own 10 GB of file copies.
  - They include a **2.5 s event-loop hold** (cause: page faults) and `/api/index/clips` at 4.2 s, waiting on its snapshot rebuild.

**Re-run (`rerun.log`).**
- The panel read "Keeps 11 edited shots (10 cut by hand, 1 by the AI) · keeps the grade on every shot it keeps · may replace 11" and listed each one.
- It took **3 clicks and 102 s, and kept all 11 edited shots exactly.**
- It named why each new clip was left out, for example: "Left out new clip KX_DROP: it duplicates CLIP_03, and its pocket-pizza moment … is the CLIP_03 18-33 range you rejected".

**Playback, the canvas by its frame clock and the worker's clock (`slow-*.log`, `pb-p*.log`, `wclock.py`):**
- **Inside the 0.5× shot** (CLIP_06 slowed by the AI, 5 s plays):
  - **At load 1.7-2.7: 0 of 8 plays hold, on both clocks** (`slow-c`). Verdict 14 read 8 of 8 with 2-12 holds.
  - At load 5.4-9.5: 3 of 8 held on the frame clock and 2 of 8 on the worker clock, one 83 ms hold each (`slow-a`).
- **59.94 frame steps:** 10 of 10 (above).
- **Plain 1x cuts** (cuts 7 and 8, graded): **0 of 8** plays held on the frame clock at load 1-8; 1 of 8 held on the worker clock (83 ms, load 7.9).
- **Cut out of the 0.5× shot into CLIP_07:** a **116-133 ms freeze on 4 of 4 plays**, at both sizes, at load 1.2-7.9.
- **Cut into the 0.5× shot:** 2 of 4 plays held 83 ms.
- **Dissolves:**
  - 0.4 s and 1.0 s **out of the 0.5× shot: 16 of 16 plays held**, 1-5 times each, 83-100 ms, **including at load 1.5-3.3**.
  - Between 1x shots: 1 of 16 plays held (100 ms, at load 3.8).

**The K3 walk: an empty project, a fresh browser, no doc (`fresh.log`, `80-97-*.png`).**
- **Landing.** Every panel names Import, and the header reads "Next: Import footage (Ctrl+I)".
- **Import.** The tour's Show me opened Import, and Import Folder gave rows at 0.24 s. A 442 MB drop gave a row at 7.0 s and "Imported" at 8.7 s (load 5-8).
- **First pass: 66 s.** Afterwards the Index tab read "3 clips · heard 3 · looked 3 at 4 s · close looks 1 · 2 running".
- **Why.** On the pick markers and on Selects rows.
- **Review.** I rejected one ghost, which gave "Accept 3 of 4".
- **Asks.**
  - "Make this a tight 20-second edit that opens on the best moment": a plan in 12.9 s, accepted at 20.57 s.
  - "this shot is a bit cold and dark, warm it up and brighten it slightly": 2 ops in 5.9 s.
  - "Working" showed by 80 ms both times.
- **Export.** 720p / 29.97 gave **616 frames = 20.554 s**, from a 20.5705 s 59.94 sequence (1233 frames). The half frame lost is the rate conversion.
- **Guesses: none.** The tour card stayed clear of the AI's line.
- **The ledger.** The server, started without `ROUGHCUT_LEDGER`, printed "model calls logged to …/judge15/work2/roughcut-ledger.jsonl" and wrote its 21 rows there.

## The 16 dimensions

| # | dimension | score | evidence | vs Premiere Pro |
|---|---|---|---|---|
| 1 | Orientation | **9** | Rows 0.23 s and a picture 0.30 s at both sizes; 0 page errors; one first action; every empty panel names Import (`80-fresh-empty-land.png`); the ruler's edge label is now whole or dropped. Against: after this update, a bin needs ~20 min to rebuild its proxies (Killington). | Premiere's one workspace, matched. |
| 2 | First pass visible + re-runnable (K1) | **9** | 2 clicks to run; the Index summary agrees with the pass within one poll (113 of 114); the panel links the Index panel; the re-run took 3 clicks and 102 s, kept all 11 edited shots exactly and named why each new clip was left out. | n/a; a clear, re-runnable AI step with its index in view. |
| 3 | Footage in, no ceremony | **9** | Drag + grade 77 ms; Selects `,` ×3; an imported clip cut at once. | Premiere's bin and three-point editing. |
| 4 | Workspace | **9** | Program 674×379 at 1440; Index and Selects at 590 px with 10 rows and a detail open; the Timeline keeps 467 px meanwhile. | Premiere's Editing workspace. |
| 5 | Timeline craft | **9** | The key map, roll, move, razor, tracks, overwrite past the end and V3, each one undo; a reload exact (`edit-1440.log`); Selects verdicts now on the one Ctrl+Z history. | Matched for the basics. |
| 6 | Monitors and playback | **7** | Fixed: **59.94 steps 10 of 10** and 29.97 in 59.94 strictly 2:2; the **0.5× shot 0 of 8 at load ≤ 3**; plain cuts 0 of 8 on the frame clock; seeks 39-80 ms. Against: **cutting out of a 0.5× shot freezes 116-133 ms on 4 of 4 plays**; **dissolves out of a 0.5× shot hold on 16 of 16 plays, even at load 1.5-3.3**; the graded-dissolve test's plain cut drops or holds a frame in 2 of 4 standalone runs at load 7-8; a 59.94 sequence plays at ≤ 30.5 fps (hardcoded); `test_vfx_playback` flaked in my gate. | Premiere plays slow motion into a cut or a dissolve without a hitch, and 60p at 60. |
| 7 | Inspector, effects, colour | **8** | Typing in Effect Controls (Saturation 80, one undo); AI Lumetri ops (Temperature, an RGB curve) land as Effect Controls values; Sharpen / Blur. Against: five built-in video effects (unchanged); Sharpen and Blur still hold a frame now and then (gate flake: 83 ms at 1440 and at 1920). | Most of Lumetri; a sliver of the Effects panel. |
| 8 | Export | **9** | 14988 frames = 250.0498 s exact (1080p59.94); 720p29.97 within the rate conversion's half frame; the AI export ran only on Accept. Against: no codec or audio settings. | Premiere's Export Settings basics. |
| 9 | Looks like a shipped pro tool | **9** | Fixed: the ruler label, the cramped Index panel, and the journey's stale "Read the proposal" (step 8 now reads "Make the film — quick look"). Left: the List view scrolled sideways shows half-cut columns ("ıfo", "łz · Mono" in journey 15 and 17); the "available keeps" lane under A2 is unlabeled dashed boxes at 1920 (`40-crop-bottom.png`). | Close in layout and density. |
| 10 | Responsive, reliable | **9** | Clicks 21-58 ms; Ctrl+I 49 ms; every edit and AI accept one undo; **both verdict-14 undo holes closed** (Selects verdicts on Ctrl+Z; an in-flight design is stopped, and its redo re-asks); 0 over-budget rows from the imports. Against: a **2.5 s event-loop hold** (page faults) and 11 more over-budget rows at load ~10 while 10 GB of copies ran; 1 flaky test in my gate; the same rev's other gate was RED. | Premiere's undo covers bin actions; now matched. |
| 11 | Proposals are reviewable edits | **9** | 17 ghosts with ✓ / ✗; 15 of 17 exact; why on hover; honest Things to check; the re-run's diff. | Premiere-like review. |
| 12 | The journey | **9** | 24 steps green in my gate, 52 s, slowest request 0.07 s, loop hold 22 ms, no retry. | n/a |
| 13 | Footage in, from anywhere | **9** | Ctrl+I 49 ms; rows 0.24 s; Media Browser; drop 1.46 s (480 MB, load 3.6); 0 stale reads after the import POST; free stages ran on their own, the priced look waited; "N new clips" offer; the re-run says why each was left out. Against: a folder dropped from the OS is unverified. | Premiere's Import and Media Browser. |
| 14 | One way to ask the AI | **9** | Always-visible prompt; Ctrl+J; the scope chip follows the right-click and the In/Out; "working" by 80 ms; answers in 5.9-12.9 s; **the AI menu now lists "Look again / re-tune the index…"**, with a hover card. | n/a |
| 15 | The AI does everything, as reviewable edits | **9** | Six families on the real model (move / trim, Scale keyframes, Temperature + RGB curve, dissolve, speed, export), plus a designed effect. Each previews, ✓ / ✗ per op, disk unchanged until Accept, one Ctrl+Z exact 5 of 5, redo exact. The design redo now never loses a request. | n/a |
| 16 | Easy to learn | **9** | 6-step tour on the real workspace with Show me, re-openable from Help › Learn Roughcut…; **the tour, the first-pass panel and the AI menu now point at the Index panel**, and its Run has the what / cost / changes card; my K3 walk had no guess. | Premiere's Project and Metadata panels, with AI on top. |

## Section K
- **K1 "not clear how to re run the first pass": RESOLVED**, as in verdicts 3-14. It is reachable from the header control, the AI menu and the "N new clips" offer, and the panel says what it keeps (3 clicks; 11 of 11 kept).
- **K2 "not premier pro quality": OPEN (Karl's call).** He has not looked since `b12b979`.
- **K3 "AI-first, easy to learn, add footage obviously": OPEN (Karl's call).** It is delivered as far as I can test. My K3 walk had no guess.
- **K4: OPEN (Karl's call); both parts are delivered as far as I can test, and wave 18 closed verdict 14's K4 leftovers.**
  - **(a) The Index panel** now has:
    - a working size, 590 px;
    - one count with the pass;
    - a tour step, the first-pass link and an AI menu item;
    - Run's price-and-changes card.
  - **(b)** `/open` and `/floor` still answer 307 → `/`, and their `.js` / `.html` answer 404.
  - **Karl has not seen either.** His 8766 is a `b12b979` server under wave-18 pages, and FOR-KARL names `799d8b8`.

## Section S
- **Inference only through `roughcut.inference`; no model-ID literal outside `config.py`: intact.**
  - A grep of the snapshot's `.py/.js/.html` (excluding tests and `config.py`) finds no `claude-*` ID.
  - `import anthropic` appears only at `roughcut/inference.py:592`. Gate hygiene is clean.
- **No price reads as money spent on `claude_cli`: intact.**
  - Every AI control and hover reads "free on your plan" or "free on your Claude plan — about $X estimated cost if using the API".
  - Every ledger row is `claude_cli`.
- **Karl's footage and work dirs never written: intact, and now enforced by the app.**
  - `server.keep_ledger_in` (`app/server.py:15836-15848`) moves the ledger into any `--work` other than `~/work/app`.
  - My second server, run without `ROUGHCUT_LEDGER`, logged to `work2/roughcut-ledger.jsonl` (21 rows).
  - **`~/work/roughcut-ledger.jsonl`: 2144 lines before and after, mtime unchanged (08:46).** The footage listings are identical.
  - Imports were in place from my own copies, so nothing was written to `~/footage`.
- **INTAKE decisions: intact.** The priced look never ran unasked on the 4 imported clips (heard 16 of 16, looked 12 of 16 until my re-run).
- **The pro controls stay: intact.** Wave 18 only added.
- **No Co-Authored-By; CHANGELOG on every commit: intact.** In `799d8b8..5138865`: 0 Co-Authored-By lines; 25 of 25 non-merge commits touch `CHANGELOG.md`.

## Verdict 14's punch list, item by item
1. **Put the judged rev in front of Karl: NOT DONE** (it waits for his yes).
   - 8766 is pid 1552214, unchanged since Oct 19 21:30, on `b12b979`'s Python and the live worktree's pages.
   - FOR-KARL names `799d8b8`.
2. **59.94 clips show every frame: DONE.**
   - `fstep.py`: 10 of 10 steps, and 29.97 in 59.94 strictly 2:2.
   - `test_parked_frame_5994` (parked frame = export frame 48 of 48) is green in my gate.
3. **Half-speed plays evenly: DONE on my measure; PARTLY as a test.**
   - `slow.py` at load ≤ 3: 0 of 8 plays hold on the frame clock (and on the worker clock).
   - At load 5-9: 3 of 8.
   - The test now judges the worker clock only, with one hold allowed (logged looser), where the ask was the frame clock.
4. **The honest clock, then fix the one dropped frame: PARTLY.**
   - The clock is fixed: frame clock and worker clock, in frames.
   - The defect is not fixed. The graded-dissolve test passed **2 of 4 alone at load 7-8** (the ask was 20 of 20 at load 4-8). Both failures were in its plain-cut half: two frames never drawn, and a 4-frame hold.
   - On Killington the plain cuts were clean (0 of 8 on the frame clock).
5. **Selects verdicts on Ctrl+Z: DONE.**
6. **Teach and tie in the Index panel: DONE.** It is in the tour, the first-pass link, the AI menu and Run's card, and the counts agree within one poll.
7. **The design redo never loses a request: DONE.** The in-flight case is stopped and re-asked, and the landed case restored with 0 calls. The REDO test was green in my gate (not run 10 of 10 alone).
8. **The Index panel at a working size: DONE** (590 px, 10 rows with a detail open, the foot one row).
9. **The call ledger stays inside `--work`: DONE.**
10. **Three clean gates and the leftovers: PARTLY.**
    - The ruler label: done.
    - The imports added 0 over-budget rows.
    - Blur at 1920: **not done** (83.3 ms in my gate).
    - Three clean gates: **not done** (the lead's gate RED, mine 1 flaky).

## Blockers
1. **K2, K3 and K4 are open.** Only Karl can close them, and he has not seen this rev: 8766 serves a wave-16 server under wave-18 pages.
2. **Dimension 6 is below 8 (7).**
   - Slow motion hitches at its edges: a 116-133 ms freeze cutting out of a 0.5× shot (4 of 4), and holds on every dissolve out of one (16 of 16), on a quiet box.
   - The graded-dissolve test's plain cut drops or holds a frame in 2 of 4 standalone runs at load 7-8.

My gate is GREEN (1 flaky). The rev's other gate (the lead's) was RED on the same perf bar. Every other dimension is 8 or 9.

## THE THREE BIGGEST GAPS TO PREMIERE
1. **Slow motion still hitches where it meets the next shot.**
   - A half-speed shot now plays smoothly on its own on a quiet machine.
   - But cutting out of it freezes the picture for about an eighth of a second every time.
   - A dissolve out of it stutters every time.
   - And 60 fps footage plays back at 30 while you watch.
2. **Only Karl can say whether it is Premiere quality, and he can't see it yet.**
   - What he asked for in K4 is built and works: the Index panel is now taught, sized and in the AI menu.
   - The preview he would open today is an old server under new pages.
   - The first open after this update will spend about 20 minutes rebuilding the 60 fps previews.
3. **Playback still depends on how busy the machine is.**
   - The same build failed one full check and passed the next.
   - Sharpen and blur still drop a frame now and then.
   - The plain-cut-in-a-dissolve test fails half the time on a busy box.
   - Premiere's player doesn't care what else is running.

## PUNCH LIST (most important first; [files] = lane ownership)

1. **Put the judged rev in front of Karl, previews prebuilt; only he can close K2-K4.**
   - **Files:** [the `roughcut-redesign` service: restart 8766 from a frozen `git archive` of the judged rev, not the live worktree]; [`judge/FOR-KARL.md`].
   - **Done =**
     - `readlink /proc/<pid>/cwd` = `snap-<rev>`.
     - Its work copy's `proxies/killington-neutral/v5` holds all 12 proxies **before** Karl is told, because the PROXY_V v5 rebuild took ~20 min on Killington.
     - FOR-KARL names the rev and lists, for K4: the Index tab (tour step 2, AI › Look again / re-tune the index…, the first-pass panel's link); Selects ✓ / ✗ on Ctrl+Z.
2. **A cut out of a slowed shot with no freeze.**
   - **Files:** [`app/static/app.js` the cut roll when the outgoing shot plays through the canvas at `playbackRate < 1` (slowPlaying) and the incoming is 1x: the incoming buffer parked on its in-frame and handed to the canvas on the pacer's clock]; [`app/static/grade.js` `paceDue` / the slowed shot's exit]; [`app/tests/test_cut_frames.py` new case: a 0.5× 4K 29.97 grain shot cut to a 1x shot, 1440 and 1920].
   - **Done =**
     - The new test asserts no gap over 67.5 ms at the cut on the worker clock and the frame clock (at load ≤ cores).
     - `judge15/pb.py` (`CUTS=4,5`, the cut after CLIP_06 at 0.5×) holds 0 of 8 plays at load ≤ 4. Today: 4 of 4 at 116-133 ms.
3. **A dissolve into or out of a slowed shot plays evenly.**
   - **Files:** [`app/static/grade.js` the mix (`G.mix`, `mixDrawn`) when one side is slowed: the slowed side's frames from the pacer, the mix drawn at the faster side's cadence]; [`app/static/app.js` the transition buffers (`pvTxIn` / `pvTxOut`) at `playbackRate` 0.5]; [`app/tests/test_tx_playback.py` a graded dissolve whose outgoing shot is 0.5×, the bar in frames as PLAY9's].
   - **Done =**
     - `judge15/pb.py` `DISSOLVE_AT=k1` on the edit after a 0.5× shot, 0.4 s and 1.0 s, 16 plays at load ≤ 4: 0 holds over 67.5 ms on either clock. Today: 16 of 16 hold, 83-100 ms.
     - The new test passes 10 of 10 alone.
4. **Find and fix the late page→worker frame message; then three clean gates.**
   - **Files:** [`app/static/grade.js` the worker hand-off (PLAY9: ~1% of frame messages arrive 30-70 ms late with the worker idle; instrument `sent` → `got` per message under the gate's load, and try a dedicated `MessageChannel`, or moving frame capture into the worker through `MediaStreamTrackProcessor`)].
   - **Done =**
     - `test_tx_playback::…graded_dissolve…` passes 20 of 20 alone at load 4-8 (today 2 of 4).
     - `test_vfx_playback` passes 10 of 10 alone (Blur at 1920 included).
     - **Three consecutive full gates GREEN with 0 flaky**, both perf runs passing.
5. **59.94 plays at 59.94 when the machine can.**
   - **Files:** [`app/static/grade.js` `MAX_DRAW_FPS` (line 1941): tied to the Program's Playback Resolution menu, so Full draws every frame and Auto keeps today's cap only while the canvas is falling behind]; [`app/static/app.js` the menu]; [`app/tests/test_playback_resolution.py`].
   - **Done =**
     - At Full, a graded 59.94 shot draws ≥ 55 frames a second at load ≤ cores.
     - At Auto, today's numbers hold under the gate's load.
     - The rubric log says which.
6. **The half-speed test's bar back to "no hold".**
   - **Files:** [`app/tests/test_cut_frames.py` the half-speed case: 0 held frames on the worker's clock (and on the frame clock at load ≤ cores)]; [`REDESIGN-GOAL.md` log].
   - **Done =** it passes 10 of 10 alone at load 4-8 with that bar. Today it passes 4 of 4 alone with 1 hold in 24 plays, so this looks within reach once item 4 lands.
7. **The event loop never waits on page faults.**
   - **Files:** [`app/server.py` the loop's memory lock (`locked 131 MB the event loop runs on`): the 2.5 s hold sampled in `utils.py _get_param_aliases` with 36 major faults while 10 GB of files were copied on the box]; [`app/tests/test_loop_hold.py` or the gate's boot check: a hold measured while `dd` streams 4 GB through the page cache].
   - **Done =** `/api/debug/slow` `slowest_loop` < 200 ms during a first pass with a 4 GB copy running beside it.
8. **More of Premiere's Effects panel, built in.**
   - **Files:** [`app/static/effects.js` and `grade.js` (the vfx path), `roughcut/vfx.py` (export), `roughcut/actions.py` (the AI op)].
   - **Done =**
     - Crop and Transform (Premiere's two most-used video effects) are in the Effects bin and in Effect Controls with keyframes.
     - They render identically in the export (a frame compare) and are available as AI ops (the AI-op == hand-op EDL test).
9. **Small polish.**
   - **Files:** [`app/static/app.js` / `workspace.css`, the Project List view: a sideways scroll snaps to whole columns, so a header never reads "ıfo" and a cell never reads "łz · Mono" (journey 15 and 17)]; [`app/static/timeline-lanes.js`: the "available keeps" lane under A2 gets its label and a hover at fit zoom (`40-crop-bottom.png`)].
   - **Done =** `test_workspace`'s overflow check covers the List view after a sideways scroll, and the lane shows a label at 1440 and 1920.
10. **The stale-read rule, tested where it is widest.**
    - **Files:** [`app/tests/test_import_snap.py`: a real CLI-facts change (backend signed out, or the model list changed) during an import's settle is never answered from the old snapshot].
    - **Done =** the test, plus a mutant that keeps the old snapshot across a facts change failing it.

## What I could not verify
- **A GPU browser:** every playback number is headless Chromium with software compositing.
- **A folder dropped from the OS** (headless Chromium cannot fake `webkitGetAsEntry`), and Upload Folder… through the chooser.
- **The `anthropic_api` backend:** the design redo's price confirm, and the Index Run's price.
- **The Index re-tune end to end on this rev** (a 2 s re-look). Verdict 14 drove it, and wave 18 changed only its words, width and counts.
- **Over-budget rows during my re-run.** I stopped the server before reading `/api/debug/slow` again.
- **POLISH12's CLI-facts path** of late answers.
- **Karl's 8765 and 8766** beyond their processes and start times (I sent them no request).
- **Audio by ear.** The meters moved between −35 and −8 dBFS while playing.
