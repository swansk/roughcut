# Copper, from scratch: six simulated editors and what hurt (2026-11-10)

Karl, 2026-11-10: *"let's use /mnt/roughcut/footage/copper-02-2026 to start a project from scratch. Use pain points
(as a 'human' editor using the tool, simulated touches w/ unlimited budget …) to identify areas for improvement using
orchestrated opus 5.5 subagents and your judgement"*.

This is the record: what was run, what the six editors reported, what the lead checked afterwards, and the areas for
improvement in the lead's order. Nothing in the product changed. The raw material is beside this file in
`judge/copper-editors/` (131 findings, 39 delights, the finder's audit tables, the finisher's monitor-versus-export
notes); the kit that ran it is `research/editor-trial/`.

## What was run

- **The rev:** `e9a3116` (this branch, with wave 22's B-roll change), frozen as a snapshot. One board per editor, each on
  its own work dir, model calls on the plan (`claude_cli`): 181 calls, about $49 API-equivalent, nothing billed.
- **The footage:** Copper Mountain, Feb 2026. 26 clips, 11 minutes, GoPro HERO9, 5.3K HEVC at 23.976, a `.WAV` beside
  each file, six clips with a rotation flag. Never indexed in this work dir before: a true cold start.
- **The editors:** six Opus 5.5 agents, each a person with a film to make. They could see the board only through
  screenshots and touch it only with pointer and key events in a long-lived browser tab (WebGL on the RTX 3080). They
  were forbidden to read the code, the docs or the tests, and logged each pain point the moment they hit it.

| Editor | Who | Window | Got to |
|---|---|---|---|
| `cold` | first day, iMovie / CapCut background; starts on a practice bin and must open Copper himself | 1536x864 | new project in 3 min, first pass in 5.5 min, a 93 s film exported |
| `hands` | ten years in Premiere, cuts by keyboard | 1920x1080 | a 90 s film cut by hand from the AI's 18 shots |
| `words` | directs in words only, will not touch a trim tool | 1536x864 | 33 directions, a 72 s film; the vertical version not possible |
| `finder` | story editor: audits the index and Find against the picture, builds her own cut | 1920x1080 | 24 descriptions and 15 Find queries checked, a 46 s cut of her own |
| `finish` | finishing: sound, titles, colour, export, monitor versus file | 1920x1080 | titles, a look, 1080p and 4K exports, 10 monitor-versus-file frames |
| `clumsy` | impatient, makes every ordinary mistake, small window | 1280x720 | all eight groups of mistakes, a 49 s film |

Findings as logged: 10 at severity 4 ("stopped me, lost work, or put the wrong thing in the film"), 58 at 3, 55 at 2,
8 at 1. Two of the 4s were withdrawn by their own editors (FINDER-09, FINISH-16) and two are the black monitor below.

## What the lead checked afterwards

| Reported | Check | Verdict |
|---|---|---|
| Discard on the proposed cut is permanent (CLUMSY-01) | `rejectProposal()` in `app.js`: no confirm, nothing on the undo stack | real |
| A waiting proposal vanished unanswered (FINDER-20); a second job's proposal replaced the first (CLUMSY-09) | `ask_pending()`: only the newest ask counts, and only while it is newer than the cut file on disk | real: any save of the cut, or any later ask, drops it; no screen reopens an old one |
| Undo is gone after a reload or a bin switch (five editors) | the stacks are arrays in `timeline.js` | real (the edits themselves are saved) |
| First cut came out 2:56 / 2:41 against a 60-120 s brief (COLD-09, CLUMSY-02) | `target_s: [120, 180]` written by `scaffold_edl`; the board only prints it ("aims for 2–3 min") | real: no control sets it before a first pass; "make it 60 seconds" afterwards works |
| "Your note is cut off… please resend it" when the editor wrote one sentence (COLD-17) | the ask record: the note the cut was written from is the planner's own 2,000-character expansion, cut mid-sentence | real: an internal note is truncated and then attributed to the editor |
| Sideways clips in the film (COLD-13, FINISH-22, WORDS-23) | ffprobe + frames: 6 of 26 clips carry a rotation flag; GX010474's raw frame is upright, so its flag is wrong. The telemetry sidecars already say which: `mount: upright` on 474 / 484 / 492 (flags spurious), `inverted` on 497-499 (flags right) | real, and decidable from data the index already has. `/api/projects/open` hard-codes `orient="auto"`; the code's own docstring says Copper's flags are spurious |
| Titles overflow the frame, stamped "✓ checked" (HANDS-17, WORDS-20, FINISH-18) | the screenshots ("PER 2026"), the fx records (`verify.ok: true`, six checks) | real, three editors independently; the check has no "text inside the frame" test; in the export too |
| Speed dropdown takes the next keys (HANDS-14) | re-run live: after choosing ½×, focus stays on `select.speed`; Home made it ¼×, three Downs made it 1× | real |
| Delete goes through an open menu (CLUMSY-20) | re-run live: File menu open, Delete: 13 shots → 12 | real |
| No copy / paste; no typed timecode (HANDS-10, HANDS-08 and three others) | no handler in `timeline-keys.js` / `app.js`; no timecode input | real |
| The AI has no memory of the conversation (WORDS-06, -10) | the prompt-box path sends no earlier turns | real |
| The AI scope chip follows the playhead (WORDS-01, -05) | `aiprompt.js`: a chosen scope lasts "until the auto one changes" | real, by design |
| Find never says "not here" (FINDER-12) | `roughcut/find.py` has no floor on the score | real |
| Descriptions talk every clip down as "not ski footage / no events" (FINDER-04, -22) | `visual_pass.py` tells the model the clip is "from an action camera worn on the skier's helmet or chest"; 21 of 27 Copper descriptions sampled are framed by the absence of action | real: K7 again, in the look prompt |
| "Telemetry ✓ · 0 peaks" on every clip (FINDER-16) | the sidecars: gpmd read on all 26, GPS fix fraction 0.0 on 24 (10-70 s clips never get a fix), max 1.3 g: 3 events in the bin | honest, not a fault; the screen does not say why |
| Export peaks at 0.0 dBFS (COLD-19, FINISH-20, and the lead's own K7 render) | `assemble.py` levels by measured loudness and has no limiter | real |
| No music import, no 9:16, export locks the board (FINISH-02, -14, -21; WORDS-16, -22) | the strings and `showModal()` in `app.js` / `export.js`; no aspect setting anywhere | real |
| Export a touch darker and cooler than the monitor (FINISH-19) | not re-measured: 2-5 levels of 255 at ten timecodes, frames aligned | reported by one editor, small |
| V2 targeting overwrote V1 and A1 (HANDS-12); no J / L cuts (HANDS-13) | not re-run | reported by one editor |
| **Program monitor black on graded shots, no message (all six)** | reproduced three times, then on a page that is not the board: see below | **triggered by the test browser; the board's part is that it neither recovers nor says so** |

### The black Program monitor

Every editor saw the Program monitor go black on the graded shots while the Source monitor, the timeline thumbnails and
the export stayed right; only a reload brought it back, and it returned. The lead reproduced it without an editor: a
fresh GPU browser, parking on each shot and playing a few seconds per round, loses its GPU process about 113 seconds
after the page loads (`SharedContextState context lost via Skia OOM`, three runs: Copper twice, Killington on 8766
once, on a quiet box with 10 GB of the 3080 free). From then on the grade worker still reports every draw as done and
the canvas is black. The same five minutes in the gate's software browser: 176 looks, none black.

**The trigger is the test browser, not the board.** A control page that is not the board (one WebGL canvas taking a
plain `<video>`'s frame every animation frame) died the same way in the same browser after 95 seconds, the card's
memory climbing about 40 MB a second (2.5 → 4.9 GB) until the GPU process restarted. Headless Chromium on ANGLE over
the NVIDIA EGL driver leaks GPU memory when video frames go into WebGL, so any page that does that lasts about two
minutes there. Nothing here says Chrome on Karl's laptop does the same. Two consequences. The editors' black monitors
were this rig (they were told to reload and log it once; it cost each of them time and the undo history, which is how
the undo finding got five witnesses). And wave 22's GPUPLAY arm ran in this same leaking browser, so its "half-speed
is a display frame late on a real GPU" reading (28 of 45) may be the leak's churn and not the board: unproven until
the board reports its own playback from Karl's browser.

What is the board's either way: `grade.js` has no `webglcontextlost` / `webglcontextrestored` handling, so when the
graphics context goes (a driver reset, a laptop waking, a GPU process restart), the Program stays black, says nothing,
and counts draws as good. A person concludes the footage is gone. That part is worth fixing whatever the trigger.

## Areas for improvement, in the lead's order

**1. Never lose, replace or misreport the AI's work.** Five of six editors lost something they had not chosen to lose.
A proposal is the product's main output and today it lives in one unprotected slot: Discard beside Accept with no
confirm and no undo (CLUMSY-01), dropped by any save of the cut (FINDER-20), replaced by the next job (CLUMSY-09), no
Stop on a whole-film re-cut ("Close" only hides it, CLUMSY-13), "a new cut is ready · Review" left up after Accept so it
re-opens the accepted cut (CLUMSY-11, WORDS-03), undo wiped by a reload or a bin switch (five editors).
*Change:* proposals kept as a list you can reopen and re-accept; Discard on the undo stack; a Stop on every AI job;
undo history kept with the cut on the server; banners cleared by the answer. Medium. Goal dimensions: trust, AI-first (K3).

**2. The monitor has to tell the truth.** Beyond the black canvas: before Accept the Program does not follow the
playhead and its timecode sits at zero (COLD-11, -12); after a ripple delete or an Accept it shows the old frame under
the new label (HANDS-05, CLUMSY-18, WORDS-02); the meters stay pinned at the last peak when stopped (three editors);
sideways clips go into the film unflagged (three editors).
*Change:* context-loss recovery and a "no picture: reload" line when a frame does not arrive; one source of truth for
the Program's frame after every edit; meters fall on stop; rotation decided per clip from the gravity vector the
telemetry pass already computes, with a "this clip looks sideways" line where there is no sensor. Small to medium each.

**3. An AI you can direct: memory, eyes, and its own words.** The prompt box does precise edits well (8-16 s, itemised,
tickable, nothing else touched). It fails as a conversation: no memory of what it just did or offered (WORDS-06, -10);
it cuts on visual claims from one text description per clip and writes as if it had looked ("picks up just after the
wobble"; asked, "treat it as a guess": WORDS-08, -17); it refuses "cut where the camera points at the ground" and asks
the editor to scrub (WORDS-12), though a look is free on the plan; its notes quote things nobody said (COLD-17,
WORDS-18); the scope silently follows the playhead (WORDS-01, -05); it names a "History panel" that does not exist
(CLUMSY-12).
*Change:* send the last few turns and the list of its own edits with every ask; when an ask rests on the picture, look
at the frames first or say "I have not looked"; never present an internal note as the editor's; a scope the editor
chose stays until they change it. Medium. This is K3's "was anything a guess?" answered: yes, and it only says so when asked.

**4. Titles a person can ship.** Three editors asked for a title and each got one outside the frame, each stamped
"✓ checked": "PER 2026", an end card cut off both sides with its second line missing, a lower third starting on the
frame edge with a stripe through a letter that reads as a misspelling in the export. There is no font, size, position
or safe-area control, a title needs two Accepts in two panels, a fix by words stacks a second title on the first, and a
title follows its clip when the AI reorders shots without saying so (HANDS-17, WORDS-15, -19, -20, -21, FINISH-07, -17, -18).
*Change:* a text-inside-title-safe test in the effect check and an auto-fit before "✓"; editable text, position and
size on the card; replace instead of stack; one Accept. Small for the check, medium for the controls.

**5. Start a film like a person.** The only way to begin with your own footage is a text field under "BINS" in the
title dropdown that takes a typed server path; File has no New, the tour and Help never mention it (COLD-01 to -04).
Nothing asks how long or who for before the first pass (COLD-09). The first screens speak in model names and pipeline
words ("Qwen3.5-4B, PE-Core", "levelled 8 fps strips", "unchecked", "unsupported", "~$36 API-equivalent · free on your
plan": COLD-05, -06, -07, -10, CLUMSY-08, FINISH-23).
*Change:* File › New film from a folder… with a folder browser on the empty timeline card too; two questions before
the first pass (length, who it is for) that set the target; plain words first with the model names one click down;
a time estimate beside the price. Small to medium. Dimension: easy to learn (K3 "obvious footage import").

**6. Describe the footage as it is, and let the editor read it.** The index's descriptions are verdicts on action:
on a travel day they say "not ski footage, nothing notable" 21 times in 27, call a chairlift ride "staff handle
equipment" (re-running the look four times closer repeated it) and miss the one clear jump (FINDER-04, -05, -07, -13,
-15). The transcript is not readable anywhere; the Index says what ran, not what it found (FINDER-02, -08). Find landed
on 9 of 12 real moments, but a hit has no clip name and opens below the fold, it returns matches for things that are
not there, and a spoken line ranks under a black frame (FINDER-01, -11, -12, -14).
*Change:* a neutral look prompt (who, where, what happens, what is said, then events) with no camera assumption; the
transcript and the found moments on the clip in Index and Source; a correction the editor types sticks; Find names the
clip, opens it in Source at the moment, and says "nothing like that here" under a score floor. Medium. K4, K6, K7.

**7. What a delivery needs.** Exports peak at 0.0 dBFS with no limiter or loudness target; no way to bring in music
(the panel says to drop a file in a server folder); no 9:16 (asked for a vertical cut, the AI offered to overwrite the
main film with a shorter horizontal one: WORDS-22); the export dialog locks the board for the whole render (21 min for
4K); a transition cannot be selected to change or remove it; the `.WAV` files are never mentioned.
*Change:* a limiter and a loudness target on every export (small, and it fixes every film made today); music import in
the Audio tab; a sequence shape with reframe; export in the background. The limiter first.

**8. The hands.** The trim core passed a professional ("feels like Premiere": three-point edits, Q / W, razor, roll and
ripple are frame-exact). What fights the hands is focus: a dropdown that keeps the keys (HANDS-14, FINISH-13), keys
that pass through menus and lost-focus boxes (CLUMSY-15, -20), a play mode stuck by Enter (HANDS-08). Then the missing
basics: copy / paste, typed timecode (four editors), J and L cuts, lift and gap, slide, typed speed with reverse and
freeze, paste attributes, J K L on the Source monitor. And buttons that move under the pointer as a label changes
length (three editors mis-clicked Play, Mark Out and Stop).
*Change:* one focus rule (after any panel control the timeline has the keys; no editing key reaches a menu or a form
control; every unsupported key says so) fixes five findings at once; fixed-width transport rows; then copy / paste and
typed timecode. K2: "not for paid work yet; for a rough cut on top of the AI pass, yes".

## What worked: do not break it

- The cold start itself: path typed, project made at once; all 26 clips heard and looked at and an 18-shot cut proposed
  in 5 minutes; the proposal reads like an assistant editor's notes and found the trip's running joke by itself.
- The claims: of 10 shot claims the first-day editor checked against the picture, 7 true, 2 partly, 1 weak, none invented.
- With 3 sensor events in the whole bin, the first pass still built a people-led cut. Wave 22's talk and hang-out
  moments carried a bin where telemetry had nothing to say.
- Simple asks in words: answered in 8-16 s, itemised, tickable, exactly what was asked. "Why is shot 5 here" and "what
  have you not used" were good. It pushed back on a drone shot that does not exist.
- Trim tools frame-exact; undo and redo consistent over 20 steps; accept and delete-all undoable with a toast saying so.
- Export: length and frame rate exact (4,212 frames, 23.976), clean cancel, a done screen with the path.
- Re-running one index stage on one clip, with its interval, engine and live progress.
- Save a copy of a cut in one field; plain messages for a bad folder path; one job from a double-click.

## The six verdicts

- `cold`: "Yes, cautiously. Talking to the AI was faster than cutting by hand… I couldn't trust what I was watching for half the film."
- `hands` (K2): "Not for paid work yet. For a quick rough cut on top of the AI pass, yes." A toy first: the six-step
  speed dropdown, AI-only titles, no J or L cuts, near-empty Edit and right-click menus.
- `words` (K3): "For a first cut, yes. To finish a film by talking to it, not yet." Was anything a guess? "Yes, and the
  AI said so only when I asked."
- `finder` (K4, K6, K7): the Index says clearly what ran and how to re-run it, not what it found; Find landed 9 of 12;
  "not over-indexing on sensors: it over-indexes on an action / no-action verdict".
- `finish`: "Not yet. A misreading name super, a cropped end card, sideways shots and peaks at 0 dBFS would all have
  shipped, and the tool's own '✓ checked' let them through."
- `clumsy`: "Yes for the AI cutting, but I wouldn't trust it with an idea I hadn't saved some other way."

## What this run cannot tell you

- The editors are models playing people. They are thorough and literal; they do not get bored, and they are not Karl.
  A finding is a lead, and the table above says which ones were checked.
- They could not hear. Every sound judgement is from meters and file measurements.
- The machine was shared and at load 20-49 for most of the run. No slowness finding was kept on timing alone.
- Some mis-clicks were the script's (a coordinate gone stale as a button moved). Where an editor said so, the finding
  is marked `automation_suspect`; the moving buttons are real.
- One bin, one day. Copper is travel-heavy and its clips are short; a long helmet-cam day would stress other things.

## Re-running it

`research/editor-trial/`: `BRIEF.md` (the rules of the seat), `rc.py` and `rc` (look and touch through one long-lived
tab), `serve.sh` / `browser.sh` / `fork.sh` (a board, a browser and a project copy per editor), `personas/` (the six
briefs), `merge.py` (all findings, worst first). The paths inside point at `/mnt/roughcut/space/roughcut-loop/copper`.
The finder's `finder-audit.md` (24 description checks, 15 Find queries with where the thing really is) is the start of
a second labelled set, on a bin that is nothing like Killington; it needs Karl's check before anything is tuned on it.
