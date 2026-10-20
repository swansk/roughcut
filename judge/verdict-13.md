VERDICT: FAIL

# Roughcut redesign: verdict 13

- **Rev judged:** `b12b979`, the branch HEAD when I started (wave 16 merged; 35 commits, 27 non-merge, since verdict 12's `992ca33`). The gate archived it to `~/roughcut-loop/snap-b12b979`. I served that snapshot on 127.0.0.1:8781 only:
  - **Killington:** `--footage ~/footage/killington-neutral --work judge13/work` (12 GoPro clips), restarted once at the end for the K4 check.
  - **The K3 walk:** an empty project in a fresh browser (`empty`/`work2`).
  - **Fresh-import playback:** 3 fresh 12-clip Killington work dirs (`work3-1..3`) with 4 busy-loop burners (load 4.9-7.0).
- **While I judged, the branch moved:** `b12b979..e43ea43` is three docs commits recording **Karl's K4** (he tried `b12b979` on 8766) and the wave 17 plan. No code changed. I judged `b12b979` and treat K4 as an open K line against it (verified below).
- **Karl's live board:** `35cb147`, an ancestor of `b12b979`, **502 commits behind**. Port 8765 untouched (pid 1902724, Oct 15).
  - **8766 was restarted by the lead at 21:30:27** (journal) on Karl's request, serving the live worktree, not a frozen snapshot. I sent it no request.
- **Box load (6 cores):**
  - 2.7-5.3 at the cold land.
  - 4.7-10.2 (mean 7.2) during the first pass, with my gate's suite running beside it.
  - 4-7.8 for editing; 4.6-6.7 for the asks; 2.9-5.8 for playback; load_pre 8.5-11.9 for the standalone perf runs (their own 4K fixtures).
  - Another Claude session's Chrome was running from about 21:24.
- **Model spend:** backend `claude_cli`, nothing billed.
  - Killington: one first pass (Start, Stop, Re-run) and one re-run; 14 asks; 4 designs (2 from the KILLINGTON ask, 1 SEND IT and its redo).
  - The empty project: one 3-clip pass and 4 asks.
- **Cleanup:**
  - All my servers are stopped (`pgrep judge13` is empty, 8781 is free).
  - The `~/footage` listing is identical before and after (`footage-before.txt` = `footage-after.txt`, sizes and mtimes). Import folders were copies under `judge13/import2`, `import3` and `dropsrc`.
  - The only repo write is this file, uncommitted.

## Gate: my own run, `~/roughcut-loop/gate-b12b979-20261019T2016/summary.md`: **GREEN**, 3080 s

| check | result |
|---|---|
| browser | ✓ Playwright 1.64.0 / Chromium 156 |
| suite | ✓ 1856 passed, 1 skipped, **2 flaky** (failed in the full run, passed alone): `test_poster_time::test_a_clip_still_being_probed_gets_the_poster_it_will_keep` (**new in wave 16**: CLIP_B's duration was already known, `6.0 is None`) and `test_proposal_review::test_a_waiting_first_cut_opens_on_reload_as_an_edit` (the ghost's ✗ had no bounding box). `test_vfx_playback` passed in this run. **Perf, every run counts:** before the suite **passed** at load 4.9 (live 50.1 ms, 33.5 by expectedDisplayTime; canvas frame/worker 50.1/50.3 at 1440, 50.0/50.3 at 1920). After the suite **passed** at load 4.5 (canvas 50.1/52.7 at 1920). |
| screens | ✓ 7 PNGs, 0 page errors. Counts unchanged from verdict 12: / 109·57, /floor 52·7, /open 55·8, / shot 138·72, / film 117·56, / jobs 118·58, / fx 143·60 |
| journey | ✓ 24 steps · 52 s · slowest request 0.06 s · slowest loop hold 15 ms; `journey.json` `retried: []`, `amber: null` |
| boot | ✓ the cut on screen 0.74 s after a proxy lands (limit 2 s) |
| hygiene | ✓ clean |

**The GREEN is fragile.** I ran `test_vfx_playback::test_a_sharpened_and_a_blurred_shot_play_with_no_hold` alone 6 times back to back at load 3.2-6.5. It **failed 4 of 6** (`judge13/perf/vfx-*.log`), each on one hold of 83-117 ms: 5 of the 6 holds were on Gaussian Blur. The lead counted ~3 in 10. The lead's own gate on `3b37b53` was RED on it.

## Rubric changes log: rulings on the entries since verdict 12

1. **"Said plainly: the speed test no longer asserts the graded canvas on the page-callback clock": honest.** It says exactly what verdict 12 asked for. The asserts at `test_perf_latency.py:646-661` are unchanged: frame and worker clocks for the canvas, `now` and expectedDisplayTime for the live buffer.
2. **"Ctrl+Shift+Z after undoing an AI design request asks the designer again": honest as a disclosure, lenient as behaviour.** I measured it (`fxredo.log`):
   - Accept: "an effect being designed — review it in Designed Effects". The design landed in 19 s.
   - Ctrl+Z: "undone: AI: effect — the design is discarded". Its file `fx_cc16d1f1.json` is deleted.
   - Ctrl+Shift+Z: "redone: … 1 effect being designed". **A different design** (`fx_03876b5d`, "SEND IT lower-third, shot 6" where the first was "SEND IT lower-third title, shot 6") arrived 17.6 s later, from a new model call.
   - A redo that brings back a different edit is not Premiere's redo. On `anthropic_api` it would spend on a keypress with no price shown first, which is against the lead's own rule (dimension 13: "a priced step is offered with its cost line, never run unasked").
   - **Ask:** keep a discarded design restorable, so a redo brings back the same one with no model call. Re-ask only for a request taken back before its design landed (punch 6).
3. **"Two Lumetri UI timing tests now measure what the editor sees".**
   - **(a) The clock: honest in substance, with a flaw and an unlogged leniency.**
     - On Killington at load ~1.2 I timed 6 curve drags from the **first** input that changed the curve (`curve13.py`). They read **1.2-14.7 ms**, and in 6 of 6 the frame that showed the change came before the second input. So the old 26-226 ms was the test's own overhead, as the lead says.
     - The flaw: `arm()` starts the clock at the **latest** value-changing input before that frame (`P.t0 = P.last`). On a board slower than the stream of inputs, that understates the delay.
     - The leniency: `within_limit` now **skips** the curve and wheel bars above 2× the cores. The commit message `cc44d12` says so; the rubric log entry does not.
   - **(b) The waveform at ≤ 9 frames always, ≤ 150 ms only under the core count: lenient, as the lead logged it.** A frame count stretches with the very load it excuses. It is acceptable for a scope (Premiere's scopes lag behind the picture too) because it was logged.
4. **(Logged at 21:37, after `b12b979`, while I judged) Karl's K4 as bars: honest and stricter.** They follow his two answers word for word. They are not implemented on this rev, as expected (wave 17).

## Driving it like an editor

**Cold land (`cold.log`, `01-cold-5s-1440/1920.png`).**
- Rows in 0.13-0.23 s and a picture in 0.14-0.35 s. One blue app action ("Run first pass") plus the tour's Next. 0 page errors; one console 503 for CLIP_01's preview while it builds.
- **Fixed:** the tour card no longer covers "preparing preview…".
- Left: the ruler's last label is clipped at the panel's right edge at both sizes ("00:00:20:0").

**First pass (`pass.log`).**
- The panel opens in 49 ms. **Stop took 69 ms**; its child processes were gone 1.5 s later. The label read "Re-run first pass" after Stop and after a reload.
- **Fixed:** no stale hover card over the running panel (`12-fp-running-30s.png`).
- The timed run took **423 s** at load 4.7-10.2. It proposed 15 shots (2:54) and 79 picks.

**Review (`review.log`).**
- 15 ghosts on the Proposal track. I rejected 2 (ghost ✗ and table ✗).
- "Accept 13 of 15" landed exactly the proposal minus those 2, with 2 PUTs.
- A drop-then-grade landed in 71 ms and survived a reload.

**Editing (`edit-1440.log`, load 4-7.8).**
- Seeks took 46-142 ms. Plays across 3 cuts had 0 gaps over 67 ms (16.7-33.3 ms at the switch). The 0.4 s shot played clean 3 of 3.
  - The 2.8 s "gap" in rep 2 is my script's artefact: a frame from the next rep's seek, after the pause.
- Every key worked: +/−, \, arrows, J/K/L, I/O, M, C/V, Ctrl+K, undo/redo.
- Ripple, roll, move, Selects `,` ×2, Add/Delete Track, a V3 overlay, and overwrite past the end (Black Video) each worked and each is one undo. A reload kept the cut.
- Timings: clicks 22-31 ms; keys 51-135 ms including Playwright's round trip (two at 132/135 ms at load 7.5); `effects.set` 12-31 ms.

**Markers by hand (`w16.log`, `90-*`/`91-*.png`), verdict 12's punch 4.**
- M drops a marker; M on it opens Premiere's **Marker dialog**: Name, In, Duration, Comments, 8 Marker Colors, Delete/Cancel/OK. Keys typed in it stay in it.
- OK is one undo ("edit marker"). The ruler shows "best trick" in red, and the hover reads "best trick · 00:00:13:14 · Duration 00:00:01:00 · Red" plus the comment.
- A reload keeps it, a double-click reopens it, and Esc cancels.

**AI asks on Killington, in my own words, on the real model (`ask.log`):**

| family | my words | answer | review | land |
|---|---|---|---|---|
| timeline | "Shorten shot 3 to about a second and a half, and move the last shot so it opens the film" | 5.9 s: trim + move. The hover reads "shot 19 (CLIP_11)" (**no ids**) | ghosts and a move arrow; I rejected the move → "Accept 1 of 2" | disk unchanged until Accept; one Ctrl+Z exact (segs, tx, colour, markers, In/Out); redo exact |
| keyframes | "Fade shot 2 up from black over its first second using opacity keyframes" | 6.4 s | row | one undo exact |
| colour (right-click, chip "Shot 5 · CLIP_02") | "The snow in this one looks dull and a little green…" | 9.0 s: 5 Lumetri ops | ◐ After; rejected 1 → "Accept 4 of 5" | one undo exact |
| transition | "Put a half second cross dissolve between the fourth and fifth shots" | 6.5 s | row | one undo exact |
| vignette | right-click: "Darken the corners of this shot a little…" | 5.9 s: Lumetri Vignette −1.2 / Feather 70 | ◐ | one undo exact |
| sharpen | right-click: "This shot is a bit soft, sharpen it slightly" | 6.9 s: Sharpen 60 | ◐ | one undo exact |
| speed | right-click: "Slow this shot down to half speed" | 5.9 s: Speed 50 % | row | one undo exact |
| look | "Make the whole film feel colder…" | 13.5 s: look cold 0.70 | ◐ | one undo exact |
| In/Out + marker | "…drop a marker at the start of shot 3 called best trick" | 5.9 s: In/Out + **Marker "best trick"** | rows | one undo exact; **the name lands** |
| title | "Put a big white KILLINGTON title over the first two seconds of the first shot" | 12.5 s: 2 `design_effect` | rows | **"2 effects being designed — review them in Designed Effects · Show · Ctrl+Z undoes"**. Ctrl+Z took only those back; **the In/Out + marker stayed** |
| export | "Render me a 1080p review copy of the whole sequence" | 5.0 s: Quick 1080p, Entire Sequence | "writes …/review copy.mp4 · 1920×1080 · 59.94 fps" | ran only on Accept, 225 s. `ffprobe`: 1920×1080, 60000/1001, **17577 frames = 293.243 s exactly**; **chapters "Start" and "best trick"** |

- **No internal ids anywhere:** 22 page scans for `\bg[0-9a-f]{10}\b` (text and `title`; after every answer and every redo, and once by hand) found nothing.
- The AI menu lists every AI action, and each hover says what it does, what it costs and what it changes (`cold.log`).

**Selects open at 1440 (`w16.log`, `selw.py`, `92-*.png`), verdict 12's punch 8.**
- **The Timeline header is whole now:** chip "In/Out 0:01–0:17", input 162 px, 0 overlapping ruler labels.
- But **on Killington Selects no longer widens at all.** The list is 268 px with Name / Duration / Usage only, and the names are cut to about a dozen characters ("That's my …"). This holds whether the chip says In/Out or Sequence.
  - The cause is `dock.js:217`: Selects widens only if `room − 592 ≥ tlHeadNeed()`, and "Timeline: killington-neutral" plus the prompt needs more than the 475 px that are left.
  - In my 3-clip project named "empty", Selects opened at 592 px (Timeline 467 px). `test_selects_opens_wide_enough_for_its_media_in_out_and_reason` passes only because the suite's project name is short.
  - At 1920 it is fine (592 px with Media In and Reason).

**Footage in, mid-session (`import.log`, `kxins.py`).**
- Ctrl+I opens the dialog in **71 ms**. Two 450 MB clips imported with their facts (fps, size, timecode). KX_05 was cut at once with `,` and undone.
- An OS-style drop of a 190 MB file read "Imported 1 clip" at **0.52 s**.
- The offer read "3 new clips: add them to the cut? · Looking at them: free on your plan · Re-run first pass… · Not now".

**Re-run (`rerun.log`, `72`/`73-*.png`), verdict 12's punch 10c.**
- The panel read **"Keeps 11 edited shots (9 cut by hand, 2 by the AI) · keeps the grade on every shot it keeps · may replace 8"**.
- It took 3 clicks (offer → Run → Accept) and **72 s**, and kept 11/11 exactly.
- It named each new clip with why it was left out ("KX_10: its transcript matches CLIP_10 word for word").
- Against: Things to check says the AI-slowed locked shot "is listed as 29.4 s, but its in and out only span 14.7 s". The model was not told the shot's speed.

**Playback (`out/pb11-*.json`, the canvas by its frame clock, the live buffer by rVFC):**
- **Sharpen 60 and Gaussian Blur 12 on Killington, verdict 12's punch 2:** across 12 plays at 1440 and 1920 (load 2.9-3.9), **10 had no hold over 67 ms** and 2 had a single 83.3 ms hold inside the effected shot. Verdict 12 saw 150-217 ms holds, 10-14 per play.
  - The dedicated test failed 4 of 6 alone (above).
- **Graded Cross Dissolves (0.4 s and 1.0 s, 24 plays):** 0 holds in or after the mix; 2 single 83 ms holds in the first 133 ms of a play.
- **Plain graded cuts:** 1 single 83 ms hold in 6 plays.
- **The 0.5× shot (CLIP_09):** holds 83 ms 4 times in a 3 s play at 1440 and once at 1920. A 29.97 proxy at half speed gives 15 new frames a second, so the cadence should be an even 66.7 ms.
- **The speed test standalone, verdict 12's punch 3:** **10 of 10 passed**, two sets of 5 back to back at load_pre 8.5-11.9 (`perf/perf-[ab]-*.json`).
  - One run's 1920 live buffer read 83.4/100 ms. That is not asserted, since at 1920 the canvas is the picture, and the canvas read 66.6.
- **Fresh import at load 4.9-7.0 (`freshplay-1..3.log`):** a just-inserted KX_03 played real frames at **+8 s, 3 of 3**. The wait line read "~5 s" at about +1 s and "~1 s" at +4-5 s.

**Export (`exp.log`).**
- Ctrl+M opens the dialog in 30-52 ms; Esc closes it.
- An In/Out export of 3.987 s gave **239 frames = 3.987 s** exactly.

**The server on Killington (`slow-killington-final.json`), verdict 12's punch 7.**
- **2 over-budget rows in the whole session** (verdict 12 had 24): GET `/api/clips` 343 ms and GET `/api/firstpass` 482 ms. Both came at the mid-session import, with `cause` "faults" (3 and 8 major faults, 50-62 ms of CPU).
- 0 rows over 500 ms. The slowest loop hold was 178 ms ("faults", 2 faults). RSS 149-153 MB, 127 MB locked, 3.4 MB swapped.
- The `cause` field (`server.py:2146`) has no class for a handler that is **waiting**. Here `snap_respond` (`:3884`) waits on a worker thread's rebuild after the import. Any major fault then makes the row read "faults", so the label is a guess for these two rows.

**The K3 walk: an empty project, a fresh browser, no doc (`fresh.log`, `80-…97-*.png`).**
- **Landing:** every panel names Import; the header reads "Next: Import footage (Ctrl+I)".
- **Import:** Show me opens Import. Import Folder gave rows at 0.48 s; the drop showed its row at 1.24 s and was done at 3.0 s.
- **First pass:** 66 s.
- **Why:** on the markers, on hover in Selects, and as a subclip under KX_03 (`binsub12.py`).
- **Review:** I rejected one ghost → "Accept 5 of 6".
- **Two asks:** "Make this about 20 seconds long and open on the funniest moment" (10.0 s, a plan, accepted, 21.7 s), and "brighten this shot a touch and make the snow look whiter" (7.5 s, 2 ops). "Working" showed by 80 ms both times.
- **Adjust by hand:** Saturation typed, one undo (`ec12.py`).
- **Export:** 720p/29.97 gave **650 frames = 21.688 s** for a 21.672 s sequence.
- **No guess.** One snag: after the second ask, the step-5 tour card sat over the AI's result line under the Timeline header (`95b-result.png`), so its "Review" could not be clicked. The Assistant panel showed the same review with Accept beside it.
  - A second try in a fresh browser placed the card over the Program instead (`99c-*.png`). In the plan step it no longer covers Accept (`95a-plan-proposal.png`).

**Karl's K4, checked on `b12b979` (`k4.py`, `100-102-*.png`).**
- File › "Footage and index…" goes to **`/open`**, and File › "Review the pass…" to **`/floor`**. Both are pages without the editor's chrome that look like another app.
  - **`/open` is the only place the index's settings show** ("every word heard · a frame every 4 s · close looks on all 15 · ~$30.98 API-equivalent").
- The same pages are linked from Selects' "review on the pass" (`app.js:4183`), the project switcher's rows (`switcher.js:246-248`), the Next chip in some states (`flow.js:179`) and the `dock.js:324-325` commands.
- Inside the editor, a clip's index is one line in its row's hover ("indexed: its sound heard and its pictures read"). There is no per-clip stage list, no look interval and no per-clip re-run or re-tune.

## The 16 dimensions

| # | dimension | score | evidence | vs Premiere Pro |
|---|---|---|---|---|
| 1 | Orientation | **8** | Rows 0.13-0.23 s, a picture 0.14-0.35 s, one blue app action, 0 page errors; every empty panel names Import (`80-fresh-empty-land.png`). Against: **two File menu items, Selects' "review on the pass", the switcher and the Next chip leave the editor** for `/open` / `/floor` (K4b, `101-footage-index-item.png`). | Premiere never leaves its workspace for another page. |
| 2 | First pass visible + re-runnable (K1) | **8** | Stop 69 ms; pass 423 s; re-run 72 s, 3 clicks, keeps 11/11 exactly, "9 cut by hand, 2 by the AI", names each new clip. Against: **the index half of the pass is opaque in the editor** (which stages ran, at what interval and cost, re-tune per clip: only on `/open`; K4a). | n/a: a clear, re-runnable AI step, but its index is hidden. |
| 3 | Footage in, no ceremony | **9** | Drag + grade 71 ms; Selects `,` ×2; an imported clip cut at once with `,`. | Premiere's bin and three-point editing. |
| 4 | Workspace | **8** | Program 674×379 at 1440, Source unchanged; the Timeline header is whole with Selects open. Against: **on Killington at 1440 Selects stays a 268 px list** (3 columns, names cut, no Media In / Reason; `92-selects-open-1440.png`); old pages sit outside the workspace. | Premiere's Editing workspace; its Project panel shows its columns at any width (it scrolls). |
| 5 | Timeline craft | **9** | The key map; ripple/roll/move/razor/tracks/overwrite past the end, each one undo; **markers with Name, Comments, Color and Duration in Premiere's Marker dialog**, on the ruler, in the hover, kept on reload, as export chapters. | Premiere's markers, matched. |
| 6 | Monitors and playback | **8** | Seeks 46-142 ms; cuts and the 0.4 s shot clean; **Sharpen/Blur shots 10/12 plays clean** (verdict 12: 5-7 fps); graded dissolves 0/24 in the mix; speed test **10/10** standalone; fresh import plays at +8 s 3/3. Against: single 83 ms hitches (2 of 12 effect plays, 2 of 24 dissolve plays at a play's start); **`test_vfx_playback` fails 4 of 6 alone**; the 0.5× shot judders (83 ms ×1-4 per play). | Premiere plays these with no dropped frame. |
| 7 | Inspector, effects, colour | **8** | Effect Controls typing (Saturation, one undo); Lumetri Basic/Curves/Wheels/HSL/Vignette; Sharpen and Gaussian Blur now drawn in the grade's shader. Against: five video effects in all; Curves' clock has the flaw above. | Most of Lumetri; a sliver of Premiere's Effects panel. |
| 8 | Export | **9** | Ctrl+M 30-52 ms; three exports with exact frames (17577, 239, 650); the AI export ran only on Accept; **chapters from named markers**. Against: no codec or audio settings. | Premiere's Export Settings basics. |
| 9 | Looks like a shipped pro tool | **8** | Verdict 12's leftovers fixed: no ids, no stale hover card, the tour card off "preparing preview…" and off Accept, the Timeline header whole. Left: the Selects list's cut names at 1440; `/open` and `/floor` look like another app; the tour card over the AI's line once (`95b`); the ruler's last label clipped; the toast repeats the AI's line (`65-title-accepted.png`); Designed Effects showed "SHOT 2" over shot 1's note while queued. | Close in layout and density; Premiere shows none of these. |
| 10 | Responsive, reliable | **9** | Clicks 22-31 ms; Ctrl+I 71 ms; Ctrl+M 30-52 ms; **every edit and every AI accept is one undo, including design requests** (verdict 12's bug fixed); **2 over-budget rows in a whole Killington session** (verdict 12: 24); journey loop hold 15 ms. Against: **a redo of an AI design makes a new design**; 2 flaky tests in the gate; two key timings at 132/135 ms (with the round trip, load 7.5). | Premiere's redo puts back what you undid. |
| 11 | Proposals are reviewable edits | **9** | 15 ghosts with ✓/✗; 13/15 exact; why on hover; the re-run's diff; free on your plan. | Premiere-like review. |
| 12 | The journey | **9** | 24 steps green in the gate, 52 s, slowest request 0.06 s, slowest loop hold 15 ms, no retry. | n/a |
| 13 | Footage in, from anywhere | **9** | Ctrl+I 71 ms; Media Browser and Import Folder; Project-panel Import; drop 0.52-3.0 s; facts at once; the "3 new clips" offer; the re-run names each new clip. Against: an OS folder drop is unverified. | Premiere's Import and Media Browser. |
| 14 | One way to ask the AI | **9** | Always-visible prompt; Ctrl+J; chip follows the right-click ("Shot 5 · CLIP_02"), the selection and In/Out; working by 80 ms; answers 5.0-13.5 s; AI menu complete with hover cards. | n/a |
| 15 | The AI does everything, as reviewable edits | **9** | 11 families on the real model: trim, move, opacity keys, Lumetri, dissolve, vignette, Sharpen, speed, look, **In/Out + a named marker**, designed title, export. Each previews; ✓/✗ per op; disk unchanged during every review; one Ctrl+Z exact 10/10; **the design accept is honest and one undo**; no ids. Against: the redo re-asks instead of restoring. | n/a |
| 16 | Easy to learn | **7** | 6-step tour on the real workspace with Show me; my K3 walk had no guess; hovers say what/cost/changes; empty states; subclips with their why. Against: **Karl, trying this rev (K4a): "it is still not clear to me what indexing was run over the existing footage, or how to re run / tune that"**. The Index panel his answer asks for does not exist yet. The step-5 card covered the AI's line once. | Premiere's Project panel with AI on top. |

## Section K
- **K1 "not clear how to re run the first pass": RESOLVED** on real footage, as in verdicts 3-12 (header control, AI menu, the "3 new clips" offer, a panel that says what it keeps and may replace). Karl did not reopen it in K4.
- **K2 "not premier pro quality": OPEN (Karl's call).** He looked at `b12b979` and wrote K4, not a resolution.
- **K3 "AI-first, easy to learn, add footage obviously": OPEN (Karl's call).** It is delivered as far as I can test, but K4 (a) is about learning the index, which is K3 (e) territory.
- **K4 (new, 2026-10-19, on this rev): OPEN, and verified on `b12b979`.**
  - (a) The index shows only as a one-line hover in the editor. Its settings, cost and re-run/re-tune live on `/open`.
  - (b) Five entry points leave the editor for `/open` or `/floor` (above).

## Section S
- **Inference only through `roughcut.inference`; no model-ID literal outside `config.py`: intact.** A grep of the snapshot (`.py/.js/.html`, excluding tests and `config.py`) finds no `claude-*` ID. `import anthropic` is only at `roughcut/inference.py:592`. Gate hygiene is clean.
- **No price reads as money spent on `claude_cli`: intact.** Every control reads "free on your plan" or "no charge on your plan". The API estimate is in the hover ("~$0.21 if it went through the API"). The redo's spend on `anthropic_api` is untested and is ruled above.
- **Karl's footage and work dirs never written: intact.** The footage listing is identical; imports referenced copies under `judge13/`. 8765 was untouched; 8766 was restarted by the lead, not by me.
- **INTAKE decisions: intact.**
- **The pro controls stay: intact.** Wave 16 only added (Marker dialog, shader effects).
- **No Co-Authored-By; CHANGELOG on every commit: intact.** In `992ca33..b12b979`: 0 Co-Authored-By lines; 27 of 27 non-merge commits touch `CHANGELOG.md`.

## Verdict 12's punch list, item by item
1. **Put the judged rev in front of Karl: DONE.** The lead restarted 8766 at 21:30 on his request; he tried `b12b979` and his words are K4.
   - It served the live worktree (then at `b12b979`), not a frozen snapshot.
   - `FOR-KARL.md` still named `992ca33`.
2. **Video effects play in real time: DONE on Killington, NOT stable in its test.** 10/12 Killington plays had no hold and 2 had a single 83 ms hold. `test_vfx_playback` passed 2 of 6 alone.
3. **The speed test 5 of 5, twice, and the log sentence: DONE.** 10/10 at load_pre 8.5-11.9, and the log entry is in.
4. **Markers as Premiere has them: DONE.** By hand and by the AI, with the same EDL (`test_actions_ui`); ruler, hover, reload, chapters.
5. **An AI design request is honest about what it did: DONE.** "Being designed", one Ctrl+Z, older edits untouched. The redo re-asks (ruled lenient above).
6. **Internal ids never reach the user: DONE.** 0 ids in 22 scans, and the AI's words say "shot 19 (CLIP_11)".
7. **The server's budget during a pass on real footage: DONE nearly.** 2 rows in the whole session (343 and 482 ms at an import), down from 24.
8. **A narrow Timeline and the tour never hide controls: PARTLY DONE.**
   - Done: the Timeline header whole with Selects open; the tour card off "preparing preview…" and off the plan's Accept; the first pass's hover card closes.
   - Not done: on Killington at 1440 Selects stays 268 px with cut names; the step-5 card covered the AI's line under the header once.
9. **Zero flaky tests, three gates in a row: NOT DONE.** 2 flaky in my gate, one of them a new wave-16 test, plus `test_vfx_playback` at 4/6 failing alone.
10. **Playback and wording leftovers: DONE mostly.**
    - Done: the re-run words "9 cut by hand, 2 by the AI"; 0/24 holds in or after a graded dissolve.
    - Not proven: the ETA ("~5 s" at about +1 s, playing by +8 s) is within ~1.5× at my poll resolution only.

## Blockers
1. **K2, K3 and the new K4 are open.** K4 is verified on this rev.
2. **K4's bars (an Index panel; no menu, chip or link leaves the editor) are not implemented on `b12b979`.** They were logged after it, and wave 17 owns them.
3. **Dimension 16 is below 8 (7)**, because Karl could not tell what indexing ran or how to tune it.

The gate is GREEN (fragile: `test_vfx_playback` fails 4 of 6 alone), and every other dimension is at 8 or 9.

## THE THREE BIGGEST GAPS TO PREMIERE
1. **You can't see or steer the indexing from the editor.**
   - Which clips were listened to and looked at, how often, what it cost, and how to redo or retune it for one clip: none of that is in the editor. It is only on the old "Footage and index" page, which looks like a different app.
   - The File menu, the Selects panel and the project switcher still jump out to that page and the old "pass" page. Karl hit both (K4).
2. **Effects and slow motion still hiccup now and then.**
   - A blurred or sharpened shot is smooth most of the time now (it was 5-7 frames a second). It still drops a frame in about 1 play in 6, and the app's own test for it fails 4 times in 6.
   - Half-speed shots judder slightly.
3. **Small rough edges at 1440 and in undo.**
   - On a real project, opening Selects gives a 270 px list where pick names are cut to a dozen characters, with no In or Reason column.
   - The tour card can sit over the AI's "Review" line.
   - Redoing an AI title asks for a brand-new design instead of bringing back the one you undid.

## PUNCH LIST (most important first; [files] = lane ownership)

1. **K4 (a): an Index panel in the workspace.**
   - **Files:** [new `app/static/indexpanel.js` + `.css`, its mount and Window › Index in `index.html`, its dock registration in `dock.js`]; [`app/server.py` per-clip and per-selection index re-run and re-tune endpoints, a per-clip stage report from the journal]; [`roughcut/journal.py`]; [tests].
   - **Done =** each clip's stages (heard, looked, close looks, telemetry, previews) with when, look interval and cost on the current backend; re-run or re-tune for a clip, a selection or the bin, the price shown before anything runs.
   - **Test:** a driven test re-tunes one Killington-like clip's look interval and re-runs it. The judge can say what ran on CLIP_04 without leaving `/`.
2. **K4 (b): fold in `/open` and `/floor`, then remove them.**
   - **Files:** [`app/static/dock.js:324-325` COMMANDS `footage`/`pass`]; [`app/static/flow.js:179` the Next chip's `/floor` target]; [`app/static/switcher.js:246-248` place rows]; [`app/static/app.js:3632,4183,4228` "review on the pass" / "look at more" links]; [`app/server.py` `/open` and `/floor` routes]; [`open.*`, `floor.*`]; [tests that drive those pages, `test_budget`'s `/open` and `/floor` screens].
   - **Done =** no menu item, chip, link or tour step changes `location.pathname` away from `/`; `/open` and `/floor` answer with the editor; an audit table maps each of their features to its place in the editor.
   - **Test:** one crawls every menu item and link on `/` and asserts no navigation.
3. **Effects play with no dropped frame, every time.**
   - **Files:** [`app/static/grade.js` the Gaussian Blur stage (one 83-117 ms hold on Blur 12 at 640 px, both sizes) and the worker's scheduling around it]; [`app/tests/test_vfx_playback.py`].
   - **Done =** `test_a_sharpened_and_a_blurred_shot_play_with_no_hold` passes 10/10 alone at load 3-7 (`judge13/vfx5.sh`), and `pb12.py` with `CUTS=` on Killington's effected shots shows 0 holds over 67 ms in 12 plays.
4. **Zero flaky tests, three gates in a row.**
   - **Files:** [`app/tests/test_poster_time.py`: CLIP_B's duration is already cached when the full suite reaches it, so reset `CLIP_FACTS` and the probe cache it reads]; [`app/tests/test_proposal_review.py:169`: wait for the ✗ button's box]; and whatever flakes next.
   - **Done =** three consecutive full gates with "0 flaky".
5. **Selects at 1440 on a real project keeps Media In and Reason.**
   - **Files:** [`app/static/dock.js` `layout()` / `tlHeadNeed()` (line 217): take the width from Effect Controls, or let the AI prompt drop to its `#aiUnder` row first, before refusing Selects its 592 px]; [`app/tests/test_workspace.py`].
   - **Done =** with a project named "killington-neutral", an In/Out chip set and Selects open at 1440: the list ≥ 560 px with Media In, Duration and Reason, and the Timeline header whole (`test_with_selects_open_the_timeline_header_and_ruler_are_whole`).
6. **A redo restores; it does not re-ask.**
   - **Files:** [`app/static/fx.js` `withdraw` / `discardOne` / `reinstate`]; [`app/server.py` `/api/fx/discard` → a restorable "withdrawn" state]; [`app/tests/test_ai_honest_ui.py`]; [`REDESIGN-GOAL.md` log].
   - **Done =** after a landed AI design is undone, Ctrl+Shift+Z brings back the **same** fx id with no model call. A re-ask happens only for a request withdrawn before it landed, and on `anthropic_api` shows its price first.
7. **The Lumetri timing tests time from the first change, and the log says what they skip.**
   - **Files:** [`app/tests/test_lumetri_ui.py` `arm()`: `t0` = the first value-changing input since the base probe, not `P.last`]; [`REDESIGN-GOAL.md`: log `within_limit`'s skip above 2× the cores, or drop it].
   - **Done =** a mutant that delays the curve's preview by 3 frames fails the curve test.
8. **The tour card moves off the AI's line.**
   - **Files:** [`app/static/tour.js` NEVER_COVER, re-checked when `#aiUnder` or the AI status appears or moves].
   - **Done =** a tour test with Selects open, step 5, and a scripted answer: the card covers neither `#aiUnder` nor `[data-testid=ai-result-open]`.
9. **Playback leftovers.**
   - **Files:** [`app/static/app.js` player / `grade.js`].
   - **Done =**
     - A 0.5× shot of a 29.97 proxy plays at an even 66.7 ms cadence (no 83 ms frames).
     - The first animation frame after the playhead crosses a cut shows the new clip (edit.py's `wrong_clip_frames` 0, now 1-2 per cut, 15-20 ms).
     - 0 holds at a play's start (2 of 24 dissolve plays now).
   - **Proven by** `pb12.py` (`CUTS=` the 0.5× shot) and `edit.py`.
10. **Import-time requests and honest causes.**
    - **Files:** [`app/server.py` `snap_respond` (serve the last snapshot while an import's rebuild runs on its thread), `_cause` (a "waiting" class for a request whose time was a worker thread or a subprocess, not "faults" on 3-8 faults)]; [`roughcut/firstpass.py`: the re-run prompt gives a locked shot's speed, so it never reports "29.4 s vs 14.7 s"].
    - **Done =** a mid-session import of two 4K clips leaves `/api/debug/slow` `over_budget` empty, and the re-run's checks never question a speed-changed locked shot.

## What I could not verify
- **A GPU browser:** every playback number is headless Chromium with software compositing.
- **A folder dropped from the OS** (headless Chromium cannot fake `webkitGetAsEntry`), and Upload Folder… through the chooser.
- **The `anthropic_api` backend:** its cost words and whether the design redo spends unasked (not run).
- **Karl's 8766** beyond the journal and its process (restarted by the lead at 21:30; I sent it no request).
- **The Program's parked frame against the export's frame** (FRAMEACC's own queued item): not measured.
- **The ETA's accuracy** below my 3-4 s poll resolution.
- **Audio by ear.** The meters moved between −37 and −15 dBFS.
