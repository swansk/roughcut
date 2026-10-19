VERDICT: FAIL

# Roughcut redesign: verdict 10

- **Rev judged:** `9470064`, the branch HEAD. It is **code-identical to `69fb0f0`**: `git diff --stat 69fb0f0 9470064` touches only `CHANGELOG.md` and `judge/LOOP.md`. The gate archived it to `~/roughcut-loop/snap-9470064`. I served that snapshot on 127.0.0.1:8781 four times:
  - **Killington:** `--footage ~/footage/killington-neutral` (12 GoPro clips) with a fresh `--work judge10/work`.
  - **Fresh-clip playback:** an empty project (`empty3`/`work3`).
  - **The K3 walk:** an empty project in a fresh browser (`empty`/`work2`).
  - **A reproduction attempt:** another empty project (`empty4`/`work4`).
- **Karl's live board:** `35cb147`, an ancestor of `9470064`, **363 commits behind**.
  - I did not touch ports 8765 or 8766: they kept the pids they had when I started (1902724, 3924990).
  - **Note for K2/K3:** the redesign preview on 8766 (`roughcut-redesign`, the board `Roughcut redesign.cmd` opens) has been the same process since **2026-10-16 00:05**. Its working directory is this worktree, so it serves wave-13 static files on top of server code from before waves 12-13. I did not send it a request, as the brief requires. Restart it on the judged rev before Karl looks; otherwise he would be judging a mismatched pair.
- **Box load (6 cores):**
  - 2.8-3.2 at the cold land.
  - 5.4-10.8 (mean 7.4) during the first pass, with my gate's suite running beside it.
  - 6.4-8.7 for the asks.
  - 2.4-4.4 for the playback runs; the second run came after the gate had finished.
  - 1-2 for the fresh-project sessions.
- **Model spend:** backend `claude_cli` (`charged: false`). Nothing was billed. The calls were:
  - one 12-clip first pass (Start, Stop at 30 s, Re-run) and one re-run;
  - 7 asks on Killington, one of which started a Designed Effects design;
  - on the empty projects, 3 small first passes and 10 asks.
- **Cleanup:** I stopped all four servers with SIGTERM (pids in `judge10/server.pids`).
  - Port 8781 is free and `pgrep judge10/work` is empty.
  - The `~/footage` listing is byte-identical to the one I took before I started (`footage-before.txt` vs `footage-after.txt`).
  - The only repo write is this file, uncommitted.

## Gate: my own run, `~/roughcut-loop/gate-9470064-20261018T2145/summary.md`: **GREEN**, 2506 s

| check | result |
|---|---|
| browser | ✓ Playwright 1.64.0 / Chromium 156 |
| suite | ✓ 1646 passed, 1 failed in the full run, 1 skipped, **1 flaky** (`test_status_cheap::test_a_snapshot_hit_opens_no_file`, which also flaked in the lead's gate and in verdict 9's), 0 broken. **Perf passed in both runs:** before the suite at load 4.6 with 1 burner (live 50.0 ms, canvas 50.4 / 50.7 ms, seek median 51.5 ms; this is the run that counted), and after the suite at load 7.5 with 0 burners (50.1 / 50.4 / 66.7 ms). Both `test_tx_playback` dissolve tests passed in the suite. |
| screens | ✓ 7 PNGs, 0 page errors: / 109·57, /floor 52·7, /open 55·8, / shot 138·72, / film 117·56, / jobs 118·58, / fx 143·60, exactly the logged budgets |
| journey | ✓ 24 steps · 52 s · slowest request 0.14 s. `journey.json`: `retried: 0`, so the new crash retry did not fire. |
| boot | ✓ the cut is on screen 0.64 s after a proxy lands |
| hygiene | ✓ clean |

## Rubric changes log: rulings on the entries since verdict 9

1. **2026-10-18: dimension 13's spend rule re-attributed to the lead (verdict 9 ruling 5b). Honest.** It is what verdict 9 asked for, and the bar did not change.
2. **Wave 13 AIUX + BINLOOK2 budgets: +3 words on the two shot screens, +6 words on all five board screens. Honest.** My gate measured exactly 109 / 138 / 117 / 118 / 143, the logged counts.
3. **BINLOOK2's `INVENTORY_JS` `inView` (an element scrolled out of its scroller no longer counts as in view). Honest, and I proved it both ways.**
   - I archived `7436562` (verdict 9's code) and ran its own `test_budget.py` twice: once with the old `inView` and once with the new one copied in (`judge10/invcheck`). Both gave **identical counts on every screen** (103/52/55/129/111/112/134).
   - The reverse: the old `inView` on `9470064` (`judge10/invcheck2`) counts 161/190/169/170/195 words, i.e. +52 to +58 words of Project-list cells scrolled past the panel's edge. The new rule only stops counting what nobody can see.
4. **The gate's browsers get no D-Bus (`tools/gate.py:398-403`), and the journey retries a step once after a Chromium crash. Not logged in the rubric log, although both change how the gate behaves.**
   - **No D-Bus: honest.** It removes a desktop failure that is not the app's (a GNOME Shell crash restarting the user bus), and a web page needs no bus.
   - **The crash retry: lenient in part.** A step retried after the page's `crash` event (`renderer`) ends **green**, with only a note in the line (`journey.py:2220`). A page can crash its own renderer (WebGL, memory), so an app bug that crashes half the time would pass three times in four.
   - **Ask:** log it, and make a retried step AMBER (named), as the perf rule does for an unproven pass. It did not fire today (`retried: 0`), so it changed nothing in this verdict.

## Driving it like an editor

**Cold land (`cold.log`, `01-cold-5s-1440.png`, `01-cold-5s-1920.png`).**
- Speed: rows at 0.17 s and a picture at 0.29 s (1440); 0.11 s and 0.12 s (1920).
- One blue app action, "Run first pass", and 0 page errors. The one console error is a 503 for CLIP_01's preview, which is still being built.
- The first-run callout ("1 of 6 · Bring in footage") now sits above the Source transport, **off the track headers** (verdict 9's complaint).
- The Project List view now carries Premiere's ten columns: Name · Frame Rate · Media Start · Media End · Media Duration · Video Info · Audio Info · Status · Description · Captured.
  - They scroll sideways under a sticky Name, every one sorts, and Status is one word ("indexed", "building", "queued") with its sentence in the hover (`binlook.log`, `20-project-list-scrolled-right.png`).
  - Against: at 1440 the third column is cut mid-value at the panel edge ("02:45"), and no horizontal scrollbar is drawn.

**First pass on Killington (`pass.log`).**
- The panel opens in 48 ms. Stop took **625 ms**, and the label then read "Re-run first pass", including after a reload.
- The timed run took **443 s** and proposed 16 shots, 3:03.5, with "Things to check".
- Against: while the pass runs, the header carries **three progress readouts at once**: "First pass · the cut ×", "54% ×" and "20% · ~2 min left".

**Review (`review.log`, `19-fp-ended.png`).**
- 16 ghosts on a Proposal track and 19 amber pick markers whose hovers carry the why.
- I rejected 2 (ghost ✗ and table ✗). "Accept 14 of 16" landed exactly what was proposed minus the rejections, with one PUT.
- A whole-clip drop landed in 71 ms; a grade on it survived a reload; a hand trim stayed on the frame grid.
- But **5 of the 16 proposed ranges for 29.97 clips sit on half frames**: they are on the 59.94 grid (e.g. CLIP_04 63.7971–73.8571 = frames 1912.0–2213.5). See dimension 5.

**Footage in, mid-session (`import.log`).**
- Import: Ctrl+I opened the dialog in **45 ms**. I imported two Killington copies from `judge10/import2`: rows at 0.81 s, with duration, 29.97, 3840×2160 and source timecode. The clip went into the cut at once with `,`.
- Drop: a real `File` dropped on the Project panel gave "Drop to import 1 file · uploaded from this computer to foxtrot · folders too", with a row at 0.72 s. **But the 110 MB upload took ~88 s** (≈1.25 MB/s) while the post-pass index ran. On an idle server the same file took 2.1 s (`fresh.log`).
- **Verdict 9's punch 2 is fixed.** The journal holds all three imports, "imported while the index ran": look and close stay `queued` at `cost_usd 0`, and no `KX_*.visual.json` was written before the re-run. The offer read "3 new clips: add them to the cut? · Looking at them: free on your plan · Re-run first pass… · Not now".

**Re-run after hand edits and imports (`rerun.log`, `proposal-rerun.json`).**
- Beforehand, the panel said: "2 shots edited by hand since · 3 new clips since the last run · A re-run hears and looks at KX_05, KX_10, KX_DROP and may use them · Keeps 2 shots you edited by hand · may replace 13 · Won't re-propose 2 ranges you rejected".
- The run took **77 s**. Both hand-edited shots came back exactly, and Accept sent one PUT; 3 clicks in all.
- **The notes name every new clip:** "Left out new clip KX_10: Left out: it looks like a duplicate of CLIP_10 (same length, same transcript)". That is correct: they are copies. But "Left out" is doubled.

**AI asks on Killington, in my own words, on the real model (`ask-part1.log`, `ask2.log`, `ask3.log`):**

| family | words | answer | review before it lands | land |
|---|---|---|---|---|
| timeline | "Trim the second shot down to two seconds and swap the third and fourth shots" | 5.9 s: trim + move | The new cut as ghosts on a Proposal track with the move arrow (`63-timeline-review.png`). I rejected the move, and the button read "Accept 1 of 2". | Disk unchanged until Accept. One Ctrl+Z exact, and redo restores it. Undo label "AI: trim". |
| keyframes | "Slowly push in on the first shot: scale it from 100 to 120 percent over the length of the shot" | 6.4 s: `keys` scale [[0,100],[2.369,120]] | ◐ Before/After on the Program | One undo exact; the export renders it |
| colour (right-click "Ask AI about this…"; chip "Shot 6 · CLIP_08") | "This one is too blue and a bit dark, warm it up and brighten it slightly" | 5.4 s: Temperature +25, Exposure +0.15 | ◐ After. I rejected one op → "Accept 1 of 2". | One undo exact |
| transition | "Add a one second dip to black between shots 5 and 6" | 6.4 s | **Now drawn on the edit before Accept:** "Dip to Black · 1.00 s" (`63-transition-review.png`) | One undo exact; it plays without a hole (66.8 / 50.1 ms) |
| export | "Export a 720p copy at 30 frames a second that I can send to my phone" | 6.9 s: "Export Quick 1080p at 720p, 29.97 fps" | "writes …/phone_720p.mp4 · 1280×720 · 29.97 fps · Quick · 00:02:43:11" | Ran only on Accept, in 78 s. `ffprobe`: 1280×720, 30000/1001, 4901 frames = 163.530 s vs `tl.total()` 163.547 (4901.5 frames: dimension 5's half frames) |
| look | "Give the whole film a warm, teal-and-orange cinematic look" | 15.2 s: look filmic 0.60 + 2 Lumetri wheel ops + a match over 15 shots | ◐ After for the wheels; **the look and the match are still not previewed** (`actions.js:552/594`) | "AI: 4 colour changes", one undo exact |
| Designed Effects | right-click on shot 8: "Put a soft vignette on this shot so the edges fall off a little" | 6.9 s: a `design_effect` op | It goes to Designed Effects with its note; the designer made a vignette (8 stacked rings) in ~30 s | **Its free check then sat at "0%" for 12+ minutes** (see dimension 10) |

- The AI menu lists every AI action: Ask the AI… Ctrl+J; Run/Re-run first pass…; Ask for a change to the cut…; Ask about the selected clip…; Ask for a colour change…; Design an effect…; Look deeper…; Find a moment…; Check the pass's claims…; Learn the AI features.
- Hover cards say what an action does, what it costs ("free on your Claude plan — about $0.41 estimated cost if using the API") and what it changes.
- Against:
  - Right-click on a block narrower than ~15 px at fit zoom opens no menu (`ctx.py`: a 14 px block gave nothing, a 26 px block gave the menu).
  - The AI-menu hover card for "Re-run first pass…" is titled "Run first pass".

**Editing (`edit-1440.log`, `lat10.py`).**
- These keys all work: +/−, \, →, ↑/↓, J/K/L, I/O, M, C, V and Ctrl+K razor, with undo/redo.
- These edits all work: ripple trim and roll, move, reload-persistence, Selects `,` (exact pick ranges), double-click pick → Source In/Out, Add/Delete Video Track, a V3 overlay, and overwrite past the end, which leaves a "Black Video" gap. Each is one undo.
- Timings: a click → Effect Controls in **22-41 ms**; Ctrl+J → prompt focused in 11-26 ms; Ctrl+I → dialog in 20-32 ms; `effects.set` 12-20 ms; Ctrl+M → dialog in 21-56 ms.
- A hand adjustment the Premiere way: click Saturation's blue number, type 80, Enter. It saved, with one undo, "undo: saturation" (`chip.py`).

**Playback (`pb10a.log` at load 2.7-3.8; `pb10b.log` at load 2.4-3.8 after the gate; `edit-play-1440.log`):**

| | seek → frame | plain cuts, live (8 per size) | graded canvas across cuts | Cross Dissolve (Ctrl+D, 0.4 s, graded) |
|---|---|---|---|---|
| 1440 | 62-112 ms | longest 50-66.8 ms, none over 67 | 50-67.8 ms (one at 67.8) | live **83.3 / 66.7 / 83.3** and **116.7 / 83.3 / 66.7**: 4 of 6 plays hold one frame 83-117 ms; canvas ≤ 57.8 |
| 1920 | n/a | 50-66.7 ms | 50-65.3 ms, **one 87.1 ms** | n/a |

- **This is verdict 9's playback much improved.** Plain and graded cuts are now inside 67 ms on a quiet box in 14 of 16 plays (the other two: a 67.8 ms canvas at 1440 and an 87.1 ms canvas at 1920), and the gate's perf test passes in both runs.
- **Dissolves still drop a frame most of the time.**

**A just-imported clip in the Program (`freshplay.log`; fresh work dir, load 1-2, two clips):**
- Import, `,`, seek: "preparing preview… KX_10 · ~10 s" was up within 100 ms, and the nearest still within 0.6-1.0 s.
- Play at +5 s gave 0 frames; the attempt at **+21 s** played (verdict 9: ~2 min, on a 12-clip bin under load).

**Export (`exp.log`, `69-export-dialog-1440.png`).**
- Premiere's Export Settings basics are now there: Preset, **Frame Size** (Match/2160/1080/720/540), **Frame Rate** (Match/23.976/25/29.97/59.94), **Bitrate** (Mbps target), File name, Location, Range, and a summary row.
- My overlap check found **nothing overlapping or cut** at 1440 or 1920. Esc closes the finished dialog.
- An In/Out export at 720p / 29.97 / 6 Mbps gave 1280×720, 30000/1001, 180 frames = 6.006 s, 5.6 MB against an estimate of 4.6 MB (−18 %).
- **The sequence's frame rate changes under you.** The same "Match Sequence" read **29.97** at 22:05 and **59.94** at 22:13 after I had inserted CLIP_01 shots, and the bin row went from "main 29.97 fps" to "main 59.94 fps".
  - Cause: `server.py:1035 sequence_fps` = the most common rate among the cut's shots (ties to the higher).
  - An empty project's sequence reads "23.98 fps" until it holds a clip.
- My own misclick turned V1's output off once. Exports were then black, as Premiere's would be, with no warning. I turned it back on, and every export after that was correct.

**The K3 walk: an empty project, a fresh browser, no doc (`fresh.log`, `80-…97-*.png`).**
- **Landing:** every panel names the next action. Project: "Import media to start · Import… or drop files here". Timeline: "Import footage to start · Import footage… Ctrl+I". Program: "Import footage (Ctrl+I) to start". Header: "Next · Import footage (Ctrl+I)".
- **Import:** the tour's Show me opens the Import dialog. Its places are this bin, judge10, Home, /mnt and /media/karl, plus "Upload Files…" and "Upload Folder…". The callout stays visible beside the dialog. Import Folder gave rows at 0.57 s.
- **First pass:** I dropped a file (row at 0.42 s, 110 MB in 2.1 s), then ran a 3-clip first pass (62 s).
- **Why and review:** I read a pick's why on its marker and in Selects. I rejected one ghost → "Accept 4 of 5".
- **Two asks:**
  - "Cut this down so it's about 15 seconds, and start with the most exciting bit" → a plan in 13.5 s, a 6-shot proposal, accepted at **16.9 s**.
  - "This shot looks flat and grey, give it more punch and a slightly cooler winter tone" → 4 ops in 7.5 s, ◐ After, accepted.
- **Adjust by hand:** Saturation typed in Effect Controls.
- **Export:** Ctrl+M at 720p gave 1280×720, 30000/1001, **506 frames = 16.8835 s = `tl.total()` exactly**.
- **I made no forced guess at an AI step.** Two things went wrong around one:
  - (a) Esc, pressed to clear the prompt, **ended the whole tour** (`roughcut.tour = skipped`; Help › Learn Roughcut… brings it back).
  - (b) **The accepted colour change was missing 40 s later.** It was not in the export's render record (`work2/render_a14c0ca6.json`: no controls on any shot) and not on disk, although the client held contrast/blacks/saturation on shot 2 right after Accept.
    - Three targeted reproductions (`persist.py`, `persist3.py`, `persist4.py`, plus `fresh10b.py`) all saved, each with one PUT about 0.8 s after Accept. I could not find the cause.

## The 16 dimensions

| # | dimension | score | evidence | vs Premiere Pro |
|---|---|---|---|---|
| 1 | Orientation | **9** | Rows 0.11-0.17 s, a picture 0.12-0.29 s, one blue first action, 0 page errors, the callout off the headers (`01-cold-5s-*.png`); every empty panel names Import (`80-fresh-empty-land.png`). Against: three progress readouts in the header during a pass. | Premiere's Editing workspace and its "Import media to start". |
| 2 | First pass visible + re-runnable (K1) | **9** | Header control and AI-menu item in every state; Stop 625 ms; pass 443 s; re-run 77 s keeps 2/2 hand edits exactly, says beforehand what it keeps and what it won't re-propose, and names each new clip and why it was left out; 3 clicks (`rerun.log`). Against: "Left out … Left out:" doubled. | n/a: a clear, re-runnable AI step. |
| 3 | Footage in, no ceremony | **9** | A whole-clip drop landed in 71 ms; Selects `,` exact; an imported clip was cut at once with `,`. | Premiere's bin plus Source three-point editing. |
| 4 | Workspace | **9** | Program 674×379 / Source 645×363 at 1440, vertical meters; panels persist (gate `test_workspace`); Lumetri Scopes and the Effects bin are tabs (`9x-1920-*.png`). Against: the AI review and the proposal take the right column and shrink the Source to 343 px. | Premiere's Editing workspace. |
| 5 | Timeline craft | **8** | The whole key map, ripple/roll/move/razor/tracks/gap, each one undo (`edit-1440.log`). Against: **the sequence's frame rate is derived from its shots and flips 29.97 ↔ 59.94 as you edit** (`server.py:1035`; bin row and Export's "Match Sequence" both changed), so timecode and the frame grid move under the editor. The first pass put 5 of 16 edit points on half frames of their 29.97 clips (a sequence 4901.5 frames long, exported as 4901). Narrow blocks take no right-click. | Premiere fixes a sequence's timebase in Sequence Settings and never changes it. |
| 6 | Monitors and playback | **8** | Seeks 62-112 ms. Plain/graded cuts ≤ 67 ms in 14 of 16 plays at load 2.4-3.8 (one 67.8 ms canvas at 1440, one 87.1 ms at 1920). The gate's perf test passes in both runs (50 / 50.4 / 50.7 ms and 50.1 / 50.4 / 66.7 ms). Against: **4 of 6 plays through a Cross Dissolve hold one frame for 83-117 ms**; a just-imported clip first played at the +21 s attempt (the 10 s target not met); the nearest still takes 0.6-1.0 s on a fresh import. | Premiere plays these GoPro originals at once and holds no frame at a dissolve. |
| 7 | Inspector, effects, colour | **8** | Effect Controls with keys; Lumetri Basic, Curves, Wheels & Match, HSL, Scopes (`9x-1920-6-fx-dropped.png`); Effects bin with 7 transitions, Dip to Black dragged onto an edit (one undo). Against: **still no video effects** (no Crop, Transform, Blur); a designed vignette is 8 stacked rings whose check runs 12+ minutes. | Most of Lumetri; Premiere's Effects panel minus every video and audio effect. |
| 8 | Export | **9** | Ctrl+M 21-56 ms; Frame Size / Rate / Bitrate; no overlap at 1440/1920; 720p 29.97 → 1280×720 30000/1001, exact frames (fresh: 506 = `tl.total()`); estimate −18 %; Esc closes. Against: "Match Sequence" means a different rate after some edits (dimension 5); no codec or audio settings. | Premiere's Export Settings basics. |
| 9 | Looks like a shipped pro tool | **8** | Verdict 9's blemishes are mostly gone: export overlap, Status column, zero-length warnings (`warn.py`: none) and AI undo labels ("AI: trim", "AI: 4 colour changes"). Left: **the Program's readout still says "18/19 · gen_black_78a418d8"** over a Black Video gap (`58-black-video-program.png`); two "0%" pills sat in the header for 12+ min during the vignette check (`9x-1440-8-ai-menu.png`); "Left out new clip KX_05: Left out: …"; a big ▶ over the Program picture (`65-design-accepted.png`); three progress readouts during a pass. | Close in layout and density; Premiere shows none of these. |
| 10 | Responsive, reliable | **7** | Clicks 22-41 ms, keys/Ctrl+J/Ctrl+I 11-32 ms, every edit one undo, reload keeps edits. Against: **one Designed Effects check (a soft vignette) ran 12+ minutes at 100 % CPU inside the server process, read "0%" all the way, and a second identical check queued behind it**; it was drawing 240 identical 720p frames (`work/fx/…/fx_252c6f18_ev00_frames_*`, 148 frames after 12 min). The server log has **145 "event loop was held" lines (max 996 ms, mean 294 ms)** and slow requests to 2.1 s. A 110 MB drop uploaded at ~1.25 MB/s while the post-pass index ran. **One accepted AI colour change never reached the disk or the export** (not reproduced in 3 tries). | Premiere never freezes its UI or loses an applied change. |
| 11 | Proposals are reviewable edits | **9** | Ghosts on a Proposal track with ✓/✗; 14/16 applied exactly with one PUT; why on hover; checks; rejections kept through the re-run; "free on your plan" (`19-fp-ended.png`). | Premiere-like review. |
| 12 | The journey | **9** | 24 steps green in the gate (52 s, slowest request 0.14 s, no retry), covering import, drop, ask + per-op review + undo, the AI menu, the re-run and export + ffprobe. | n/a |
| 13 | Footage in, from anywhere | **9** | File › Import / Ctrl+I in 45 ms; the Import dialog over foxtrot's places plus Upload Files… / Upload Folder…; Project-panel Import and empty states; an OS drop (rows 0.42-0.72 s); facts at once; cut at once; **the priced look is held for imports until the re-run** (journal `held`, `cost_usd 0`, no sidecar); "3 new clips: add them to the cut?"; the re-run names each new clip. Against: the upload crawls while the server is busy; a folder drop is unverified. | Premiere's Import and Media Browser, and it spends nothing unasked. |
| 14 | One way to ask the AI, from anywhere | **9** | An always-visible prompt; Ctrl+J focuses it in 11-26 ms and is in the keys sheet; the scope chip follows a click ("Shot 1 · KX_10", "Shot 6 · CLIP_08", in the review's own words); "Working … Stop" at my first sample (120 ms); answers in 5.4-15.2 s; one ask box (gate test); the AI menu lists every AI action; right-click "Ask AI about this…" pre-scopes. Against: no context menu on narrow blocks; the price words were blank once before Enter on the fresh project. | n/a (Premiere has no AI prompt) |
| 15 | The AI does everything, as reviewable edits | **8** | 7 families on the real model (timeline, keyframes, colour, transition, export 720p/29.97, look/wheels/match, design_effect), all correct; transitions now ghosted on their edit; ✓/✗ per op; disk unchanged during review (7/7); one undo exact (5/5); export only on Accept; the AI-op = hand-op EDL tests green (`test_actions_ui.py`). Against: **Look/Match still not previewed**; the designed effect's own review never arrived (its check stuck at 0%); one accepted colour change lost once. | n/a |
| 16 | Easy to learn | **9** | 6-step tour on the real workspace with Show me; Help › Learn Roughcut… replays it; the AI tour has 5 steps; hover cards with what / cost / changes; empty states everywhere; Project List with 10 sortable columns and Icon view with **sprite hover-scrub drawn on the same mousemove** (cells 1→5→10→14→18 across a tile, `binicon.log`); "First pass … proposed" as a sequence; picks as labelled markers and subclips. My K3 walk had no forced guess at an AI step. Against: Esc ends the whole tour; clips without a preview yet have no scrub sprite (5 of 12 six minutes in). | Premiere's Project panel, with its hover scrub. |

## Section K
- **K1 "not clear how to re run the first pass": RESOLVED on real footage**, as in verdicts 3-9. There is one header control plus an AI-menu item in every state. The re-run says beforehand what it keeps, keeps it exactly, and now says what it did with new footage. Karl to confirm.
- **K2 "not premier pro quality … complete redesign": OPEN.** A Premiere editor could cut, grade, transition, ask and export a real film here, and plain cuts now play clean. They would still notice:
  - dissolves dropping a frame;
  - a just-imported clip waiting 10-20 s;
  - a sequence whose frame rate shifts as they edit;
  - no video effects;
  - and an AI effect check that bogs the app down.

  Karl decides K2.
- **K3 "AI-first, easy to learn, add footage obviously": OPEN (Karl's call), substantially delivered.** Verdict 9's K3 faults are fixed: the unasked priced look, the re-run silent on new clips, two ask boxes, the chip's naming and the unpreviewed transition. What remains is Karl's look. See the 8766 note at the top: restart that preview on the judged rev first.

## Section S
- **Inference only through `roughcut.inference`; no model-ID literal outside `config.py`: intact.**
  - A grep of the snapshot (`.py/.js/.html`, excluding tests and `config.py`) finds no `claude-*` model ID.
  - `import anthropic` appears only at `roughcut/inference.py:592`; `claude -p` only at `inference.py:399/445`.
  - `server.py:2665/2673` run `claude --version` / `auth status`, which are status checks, not inference.
  - Gate hygiene is clean.
- **No price reads as money spent on `claude_cli`: intact.** Every AI control reads "free on your plan", and the hover reads "~$0.20 if it went through the API — no charge on your plan". `cli.js:163-169` says "spent" only when the backend bills.
- **Karl's footage and work dirs never written: intact.** The footage listing is byte-identical before and after. Imports were referenced from `judge10/import2`; uploads went under `judge10/work*/media`. Ports 8765/8766 were not touched.
- **INTAKE decisions: intact.** The index stays unattended. Holding the look for imports matches the lead's own stricter rule (ruling 1 above).
- **The pro controls stay: intact.** Every Premiere-style control from waves 1-13 is still there.
- **No Co-Authored-By; CHANGELOG on every commit: intact.** In `7436562..9470064`: 0 Co-Authored-By lines, and 38 of 38 non-merge commits touch `CHANGELOG.md`.

## Verdict 9's punch list, item by item
1. **Green gate: perf and dissolve holes at ≈1× cores: PARTLY DONE.**
   - Done: my gate is GREEN, the perf test passes in both runs, both dissolve tests pass in the suite, and the journey is green with no retry.
   - Not done: on Killington at load ~3, 4 of 6 dissolve plays still hold one frame 83-117 ms. Only two consecutive green gates are known (the lead's and mine), short of the three asked for.
2. **An import never runs a priced stage unasked: DONE.** The journal holds look/close at `cost_usd 0` with no sidecar until the re-run, and the re-run's notes name every new clip (`rerun.log`).
3. **Visual polish: PARTLY DONE.**
   - Done: the export dialog no longer overlaps; Status reads one word; zero-length warnings are gone; AI undo labels are short; "Black Video" shows in Effect Controls, the chip and the first-pass panel.
   - Not done: **`#playingWhat` in the Program still shows `gen_black_<hash>`**.
4. **Fresh clips play in the Program without a 2-minute wait: PARTLY DONE.** The wait line shows within 100 ms and the nearest still within 1 s. It played within ~21 s on a quiet box, not within 10 s, and I did not test it at load ~10 (the lead deferred it to wave 14).
5. **Export settings like Premiere's basics: DONE.** Frame Size / Rate / Bitrate; the AI's `export` op takes them (720p 29.97 → 1280×720 30000/1001); the estimate came within −18 %; "measuring…" shows before a number.
6. **Every AI change previews before it lands: PARTLY DONE.** Transitions and fades are ghosted on their edit. Look and Color Match are still baked on save (`actions.js:552`).
7. **One ask box; the scope chip speaks the timeline's language: DONE.** The Assistant's field became a button that points the prompt; the chip reads "Shot N · CLIP"; `test_at_most_one_box_asks_the_ai_in_any_state` is green.
8. **Video effects in the Effects bin: NOT DONE.** The bin has Audio and Video Transitions only.
9. **Project panel closer to Premiere's: DONE.** Ten columns with a sideways scroll under a sticky Name, all sortable; the sprite scrub changes the cell on the same mousemove.
10. **The server never holds its event loop; playhead clamp after undo: PARTLY DONE.**
    - Done: the playhead clamp (from the gate's `test_undo_playhead`, 2 green; I did not drive it).
    - Not done: the event loop is still held, 145 times (max 996 ms), and the vignette check pegs the server.

## Blockers
1. **K2 and K3 are open** (Karl's).
2. **Dimension 10 is below 8**: the server stalls under a Designed Effects check (12+ min at 100 % CPU, "0%" throughout), the event loop is held 145 times (≤ 996 ms), and one accepted AI edit was lost once.

The gate is GREEN, and every other dimension is at 8 or 9.

## THE THREE BIGGEST GAPS TO PREMIERE
1. **The app still bogs down, and once it lost an AI change.** One AI "soft vignette" check ran for more than 12 minutes. It showed "0%" the whole time, kept the server busy, and froze the app for up to a second, over and over. A dropped file uploaded at about 1 MB/s while the app was busy. And once, a colour change I accepted from the AI was missing from the saved project and from the export a minute later.
2. **Playback is close but not Premiere-smooth.** Plain cuts and coloured shots now play cleanly on a quiet machine, but dissolves still drop a frame most of the time. A clip you have just imported takes 10-20 seconds before it will play.
3. **Pro details that a Premiere editor will trip on.**
   - The sequence's frame rate quietly changes as you edit (29.97 ↔ 59.94), so timecode and "Match Sequence" exports shift under you.
   - There are still no video effects (crop, transform, blur).
   - An AI "look" or colour match can't be previewed before you accept it.

## PUNCH LIST (most important first; [files] = lane ownership)

1. **The server never stalls: Designed Effects checks off the server process, no loop holds, uploads at full speed.**
   - **Files:** [`app/server.py` `_fx_verify_job` / `_fx_proof` / `_fx_autoverify`, upload PUT handler]; [`roughcut/fx.py` `part_graph` and the overlay frame renderer].
     - Render a static overlay as **one** frame looped (`-loop 1`), not 240 identical PNGs.
     - Run the proof in a child process under a procscope, never a server thread.
     - Keep one check per effect change (no duplicate queued job).
     - Show the check's progress as a real % with an ETA.
     - Write uploads from a worker without per-chunk event-loop work.
   - **Done =** each of these holds:
     - On Killington, "put a soft vignette on shot 8" → its check is done in < 60 s at load ≤ 6, with one job and a moving %.
     - During a first pass plus an export plus an fx check, `/api/debug/slow` reports no request > 300 ms and no loop hold > 200 ms.
     - A 110 MB drop during the post-pass index uploads in < 10 s.
     - A test asserts a static-overlay proof renders ≤ 2 overlay frames.
2. **An accepted AI edit always reaches the disk.**
   - **Files:** [`app/static/actions.js` accept path vs `lookOn`/`lookOff`/`guard`, the plan-proposal accept]; [`app/static/app.js` save / `touch`]; [`app/tests/test_actions_ui.py`].
   - **What happened:** the client held the change after Accept (`fresh.log` 22:36:07: shot 2 `{"contrast":20,"blacks":-10,"saturation":115}`), yet `work2/render_a14c0ca6.json` 40 s later and the EDL had none.
   - **Done =** a test runs this sequence and asserts the EDL on disk and the export's render record carry the colour within 2 s, ten runs out of ten:
     - accept a plan-kind ask's proposal;
     - at once ask a colour change (Esc, a click on a block, Ctrl+J), wait for its review, then Accept;
     - click Effect Controls, then Ctrl+M → Export.

     Also: the header's save state turns amber with "unsaved" whenever the client holds an edit not on disk for > 3 s.
3. **A sequence has a fixed timebase, like Premiere.**
   - **Files:** [`app/server.py` `sequence_fps` / `sequence_payload`, store `fps` in the EDL at creation]; [`roughcut/tracks.py`]; [`app/static/timeline.js`, `app/static/export.js` "Match Sequence"]; [`roughcut/firstpass.py` / `roughcut/revise.py` range snapping to the sequence grid]; [`app/static/dock.js` Sequence › Sequence Settings…].
   - **Done =** each of these holds:
     - A sequence takes its rate from its first clip, or from Sequence Settings, and keeps it.
     - Inserting 59.94 shots into a 29.97 cut until they outnumber the 29.97 shots leaves the fps unchanged (test).
     - Every proposed / AI / hand edit point is a whole sequence frame: `tl.total() × fps` is an integer, and the export's frame count equals it.
     - An empty project shows no fake "23.98 fps".
4. **Put the judged rev in front of Karl (K2, K3).**
   - **Files:** ops plus [`judge/FOR-KARL.md`].
   - **Steps:**
     - Restart `roughcut-redesign` (8766) on the frozen rev. Its process dates from 2026-10-16 00:05, before waves 12-13's server code. Whether the lead or Karl does this is decided by the LOOP rules.
     - Write a one-page "try this" covering: Import, first pass, review, ask (Ctrl+J), the AI menu, export at 720p.
   - **Done =** Karl's words on K2 and K3 are recorded in section K.
5. **No dropped frame through a dissolve.**
   - **Files:** [`app/static/app.js` `txFollow` / `txHand` / park], [`app/static/grade.js` mix].
   - **Done =** each of these holds:
     - On Killington at load ≤ 5, 6 plays each through a 0.4 s and a 1.0 s Cross Dissolve (graded and ungraded) show no live or follower hole > 67 ms. The judge's `pb9.py` is a ready probe.
     - `test_tx_playback` gains a short-handle (0.4 s) graded dissolve that passes 10/10 alone and in the suite.
6. **A just-imported clip plays within 10 s.**
   - **Files:** [`app/server.py` preview queue: a 360p range of the waited-on clip around the playhead first]; [`app/static/app.js` `mediaWaitFor`].
   - **Done =** on a fresh 12-clip Killington work dir at load ~8, a clip imported mid-session, inserted with `,` and played presents frames within 10 s (extend `test_preview_building.py`; the judge's `freshplay.py` is a ready probe).
7. **Look and Color Match preview before Accept.**
   - **Files:** [`app/static/actions.js` `livePreview`], [`app/static/grade.js` an in-memory LUT].
   - **Done =** in `test_actions_ui.py`, a `look` op and a `match` op each change `grade.probe()` before Accept and put it back on Reject.
8. **Video effects in the Effects bin.**
   - **Files:** [`app/static/dock.js` bin tree "Video Effects"], [`app/static/effects.js`], [`research/tools/assemble.py`], [`roughcut/actions.py`].
   - **Scope:** Transform › Crop (L/T/R/B, feather), Transform (position/scale), Blur & Sharpen › Gaussian Blur. Each is dropped on a clip into its Effect Controls stack, previewed live and exported, and each is also an AI op.
   - **Done =** a 10 % left Crop shows a black band in the Program and in the exported frame (±2 px); one undo per drop or drag; the AI-op = hand-op EDL test covers each.
9. **Polish leftovers.**
   - **Files:**
     - [`app/static/app.js` `#playingWhat` through `rcClipName`]
     - [`roughcut/firstpass.py` `new_clip_rows`: no doubled "Left out:"]
     - [`app/static/tour.js`: the AI menu's hover card title matches its item; Esc closes the dialog Show me opened, never the whole tour]
     - [`app/static/timeline.js`: right-click on a narrow block or its trim edge opens the clip's menu]
     - [header jobs in `app/static/app.js` / `firstpass.js`: one progress readout per job family while a pass runs]
   - **Done =** a test for each, including a page scan that finds no `gen_[a-z]+_[0-9a-f]{6}` text anywhere visible.
10. **No flaky test.**
    - **Files:** [`app/tests/test_status_cheap.py::test_a_snapshot_hit_opens_no_file`]. It flaked in the lead's gate, in verdict 9's and in mine.
    - **Done =** 20/20 in full-suite order on a loaded box, and three consecutive gates reporting 0 flaky.

## What I could not verify
- **Why one accepted AI colour change was lost** (`fresh.log` vs `work2/render_a14c0ca6.json`). Three targeted reproductions saved correctly.
- **Whether the vignette's check would ever finish**, and the designed effect's own review and accept. I stopped the server after 12 min; 148 of ~240 frames had been drawn.
- **A folder dropped from the OS** (headless Chromium can't fake `webkitGetAsEntry`), and Upload Folder… through the chooser.
- **Karl's 8766 preview server's actual state.** Per the brief I sent it no request; the finding rests on its process start time and its working directory.
- **Playback at load ~10 on Killington.** My plays ran at load 2.4-4.4.
- **The playhead clamp after an undo past the end.** I relied on the gate's `test_undo_playhead` (2 green) and did not drive it.
- **The `anthropic_api` backend's cost words** (not run).
- **Audio by ear.** The meters moved between −24 and −10 dBFS while playing.
