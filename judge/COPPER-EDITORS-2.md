# Copper, round two: the same six editors on the rebuilt board (2026-11-11)

Karl, 2026-11-10 evening, after the first trial (`judge/COPPER-EDITORS.md`): *"I will be gone for the rest of the day
so continue working on this closedloop"*. This is what the loop did with that day, and what the editors said when
they came back.

## The loop

1. **Built** the first trial's ranked areas as lanes, each in its own worktree (plans: `judge/WAVE23.md`,
   `WAVE24.md`, `WAVE25.md`): SAFE, TRUTH, DIRECT, EYES, TITLE, FILM, DESCRIBE, START, HANDS, DELIVER, then GATEFIX for
   what the merges broke.
2. **Sent the editors back** (`research/editor-trial/ROUND2.md`): the same six people, on a Copper project made from
   scratch by the new build, each retrying what hurt them last time before making their film again. Software browser
   this time (the GPU test browser was round one's black monitor).
3. **Sent their new findings back to the lanes** the same day, and added lanes for what nobody had built: VERTICAL,
   DIRECT2, MATCH, MUSIC, FIND2, and second and third rounds for FILM, TITLE, SAFE, START, HANDS, DESCRIBE.

Main, 8765 and pushing main were not touched. Model calls ran on the plan throughout (about 1,450 by the boards and
the lanes' replays, about $157 API-equivalent, nothing billed).

## The retest: 131 old pain points

| Editor | Old findings | Fixed | Better | Same | Worse | Not retested |
|---|---|---|---|---|---|---|
| `cold` (first day, from nothing) | 20 | 13 | 5 | 2 | 0 | 0 |
| `words` (directs by words only) | 23 | 15 | 5 | 2 | 0 | 1 |
| `clumsy` (every ordinary mistake) | 21 | 12 | 7 | 2 | 0 | 0 |
| `finish` (delivers the file) | 26 | 12 | 8 | 5 | 0 | 1 |
| `finder` (audits the index) | 22 | 8 | 12 | 2 | 0 | 0 |
| `hands` (Premiere, by keyboard) | 19 | 11 | 2 | 6 | 0 | 0 |
| **All** | **131** | **71** | **39** | **19** | **0** | **2** |

"Fixed" was defined for them as "a person would no longer be hurt", not "something changed". The 19 "same" are
mostly things nobody had built yet when they looked (J and L cuts, lift and gaps, slide, typed speed, paste
attributes, a vertical sequence, a music level, the camera's .WAV) and three that needed a second go (the sideways
opening shot, Find missing the jump, the Index squeezing the timeline). Each editor's line-by-line table is in
`judge/copper-editors/round2/<editor>-retest.md`.

## What they measured

- **A film of the length asked for.** Asked 90 s: proposed 1:36, exported 89.4 s (round one: 2:56 for a 60-120 s brief).
- **The AI's eyes.** The director checked 19 things the prompt-box AI said it saw in frames it had looked at: 18 true,
  none false, one generous. It held ten turns on one idea ("what did you change", "no, shorter", "put it back", "the
  second one", "why?", "do both") without losing the thread.
- **Descriptions.** The finder's second audit on an index made from scratch: verdict words ("nothing notable", "not
  ski footage") led 22 of 26 descriptions before and 0 now; true 9 → 13, false 4 → 0; 16 better, 7 the same, none
  worse. After lane FIND2's last step (the description written last, from the moments and the words): 20 true of 23.
- **Monitor against file.** Ten timecodes: titles, the dissolve, the graded shot, the end card and the once-sideways
  clip in the same place in both; length to the frame (95.554 s); true peak -1.4 dBFS (round one: 0.0).
- **Undo.** Twenty undos and redos across two reloads matched the film shot for shot.
- **No black monitor** in any of the six second-round sessions.

## What they found, and what happened to it the same day

102 new findings (4 at severity 4, 41 at 3, 50 at 2, 7 at 1; round one: 10, 58, 55, 8), in
`judge/copper-editors/round2/<editor>-findings.jsonl`. The ones that mattered:

| Found | By | Cause | Now |
|---|---|---|---|
| The first clip labelled "shot upright, flag ignored" still played sideways for 20 minutes; the editor "fixed" it by hand and the export came out sideways and zoomed | `cold` | a proxy build that started before the rotation decision finished after it; the open tab kept the old file | FILM: builds checked against the decision when they end, wrong pictures repaired at open, the sensor read before the first picture, the new file reaches open tabs without a reload, right-click › Rotate clip, Fill the frame. Checked by the lead on a copy of the broken project. |
| A title on a hand-turned shot ran up the side of the frame | `cold` | titles were drawn before the shot's own motion, on purpose | TITLE: a title is in the frame's space, in the monitor, the proof and the export |
| An end card's two lines on top of each other (round one) | `finish` | every re-validation of an effect reset its shapes to the centre | TITLE: fixed and pinned |
| Accepting a proposal made before later edits put back shots cut since, silently | `clumsy` | a proposal is a whole cut; nothing recorded what it was made from | SAFE: each proposal records the cut it came from; a stale one says "made before your last N changes" and asks once |
| "Reject all" on an AI edit could not be undone; the Ctrl+Z after it undid an older edit | `clumsy` | itemised edits were outside the proposal net | SAFE: set aside undoably, listed in Recent proposals |
| The Stop beside the prompt did nothing, four times of four | `clumsy` | the status line repainted and replaced the button before the click landed | SAFE: fixed; checked live that the model process ends |
| Undo history gone after the first switch to another film or cut | `words`, `clumsy`, `hands` | the stored history was compared too strictly with a cut an accepted proposal had been through | SAFE: fixed, reproduced with real clicks |
| "Only two of your requests led to changes" (twelve had) | `words` | the conversation is bounded; the AI stated its edge as a fact | DIRECT2: a complete record of changes beside the conversation; it says where its memory of the talk starts |
| An edit placed on "the lift scene" without looking | `words` | it looked only when the words said "look" | DIRECT2: looks when the words name something to be seen; checked on three replays |
| "Put it back" answered "press Undo three times" | `words` | no operation for it | DIRECT2: `take_back`, reviewed like any edit, one undo |
| Find said "nothing convincing" for a mountain view the index lists, and 9 matches for a snowball fight nobody had | `finder` | picture scores cannot separate present from absent across bins | FIND2: the model reads the candidates and gives each a strength; "nothing" only when it agrees; a bounded frame scan found the jump at 19.5-20.5 s |
| A clip's description contradicted its own found moments (the chairlift ride, the milk jug) | `finder` | the description was written from the coarse sheet, before the close looks and without the words | FIND2: the description is written last, from the whole index |
| The file 2-5 levels darker than the monitor, both rounds | `finish` | mostly the way the frames were grabbed; really: graded shots and coloured titles went RGB → YUV in BT.601 in a file tagged BT.709 | MATCH: fixed on the export side; within 0.4 of a level on Copper's graded shots |
| No level for the music bed; the AI "can't find a music bed" | `finish`, `words` | the level was a constant nobody could set | MUSIC: a level, ducking and fades in the Audio tab; one AI operation; measured in real exports |
| "Make a 20 second vertical version" offered to overwrite the main film | `words`, `finish` | no frame shape; no way to make a second cut by words | VERTICAL: 9:16 and 1:1 end to end; the AI's vertical version is a new cut |
| Delete removed a shot the editor never clicked (the selection follows the playhead) | `hands`, `clumsy` | the board selects on the editor's behalf | HANDS round 3: the selection is only what the editor selects, Delete and `x` act on it and say what went (pinned with the playhead over another shot) |

Three things the lanes found themselves while fixing the above, worth knowing: the export's parts were 16-bit and
could clip before the limiter saw them (FILM: float now); Find's ranking had been tuned to Copper's absent queries
and lost Killington's hits until the gate's offline check caught it (DESCRIBE: ranking restored, the floor became a
sentence); and twelve older tests failing after the merges hid five real regressions (GATEFIX: a split's selection
collapsing, the "Refine" chip hidden, Ctrl+D making a fade instead of a dissolve, the Accept button turned blue, the
Source monitor's AI menu covered).

After this was written the full gate on the merged build was red (23 tests and the journey). Wave 26 found three
real bugs under it (a silent film's export ended in an error; a title's "Fit to frame" button was cut off at the
inspector's real width; a rotation undone in the page's first 300 ms was not turned back) and nineteen older tests
that still pinned the behaviour the day had changed on purpose. The gate is green on `e0c0501` and the preview on
8766 runs that build.

## Their verdicts, round one against round two

- `cold`: "Yes, cautiously… I couldn't trust what I was watching for half the film." → "Yes, for a quick trip film:
  getting to the first cut is now genuinely easy." What would change it more: "one rotation truth" (since fixed).
- `words`: "For a first cut, yes. To finish a film by talking to it, not yet." → "Yes. For words-first directing it is
  now genuinely good." Next: the AI's own record of what it changed, and a copy it can make (both since built).
- `clumsy`: "I wouldn't trust it with an idea I hadn't saved some other way." → "Yes. This time it rarely lost my
  work." Next: every AI suggestion behaves the same way; a stale proposal says so (both since built).
- `finish`: "Not yet… the tool's own '✓ checked' let them through." → "A 16:9 web film to friends, yes, after checking
  every shot's Motion and every title's placement by eye. To a client, not yet." Next: real audio control (a bed
  level since built; a loudness target and the .WAVs not).
- `finder`: "For logging footage, not yet." → "Yes, for logging and finding. The index is now a real index." Next:
  Find as good as the prompt-box AI (since built).
- `hands`: "Not for paid work yet. For a quick rough cut on top of the AI pass, yes." → "For a quick 90-second
  friends' film I would now finish here instead of going to Premiere, which I would not have said last round. For
  paid work, not yet: Delete has no single rule I can trust, and trims step in quarter seconds."

## Still open

**Not built.** J and L cuts (an audio in and out apart from the picture's: the cut file, the monitor and the
render); lift and gaps; a slide tool; typed speed, reverse and freeze; paste attributes for a grade; fonts and
colours for titles, and titles as items on a track; the camera's .WAV as the sound; a loudness target (LUFS) on
export; codecs beyond H.264; a transition's alignment by hand; the look prompt hearing the words (it still calls a
jug "a white object" until the last step rewrites it); how the local 3080 stage sees a wrongly flagged clip.

**For Karl to decide** (each is a trade the loop should not make alone):
1. The Index panel at laptop width leaves the timeline 38 % of the window, not half. Half means either Effect
   Controls collapses while the Index is open, or the Index goes narrower than its 560 px working size.
2. An insert past the end of the film now goes to the end; it used to leave a gap (an earlier verdict asked for the
   gap, the finder was bitten by it on an emptied cut). A real "lift" would give gaps back on purpose.
3. A CPU export against GPU-built proxies is about 1.2 levels off in red and blue. Closing it rebuilds every proxy.
4. The Index's "what it found" block costs two rows at 900 px high (8 clip rows with a detail open, was 10).
5. The prompt-box AI now looks at frames whenever the words name something to be seen: 25-45 s more on those asks.
6. Find's model step adds about 12 s to the judged list (the first hits come as fast as before).

**What this cannot tell you.** The editors are models playing people; they cannot hear; the second round ran on one
bin, the same one the fixes were made on, so "fixed" here is not a held-out result. Killington's offline numbers
were held at baseline through every merge (recall 0.89, Find 0.63 / 0.42, B-roll 6 of 12), which is the only
cross-bin check this round had. A third footage set, and Karl's own K2-K7 look, are still the real test.
