# Karl: what was built while you were away, and what only you can answer (about 30 minutes)

On 2026-11-10 you said "continue working on this closedloop". On 2026-11-11 the loop built the first Copper
trial's ranked areas, sent the same six simulated editors back in on a Copper project made from scratch, and
built what they found. Of their 131 old pain points: 71 fixed, 39 better, 19 the same, none worse. The whole story
is `judge/COPPER-EDITORS-2.md`; what is below is only what needs you.

## Open it

- **The preview has all of it:** `Roughcut redesign.cmd` in `Karl OS/tools/Launch` → `localhost:18766` (on foxtrot:
  `localhost:8766`). Its own copy of Killington; nothing you do there touches your real project. It opens on a
  first-pass proposal that is waiting for an answer (Accept or Discard, either can be undone). Every gate is green
  on this build (`e0c0501`, 2026-11-11 17:30).
- **Your real board does not, yet:** `Roughcut.cmd` → `localhost:18765` is still the build of 2026-11-10 08:25. When
  you want the new one there (your Killington cut is kept; it is backed up at
  `/mnt/roughcut/work/app.backup-pre-redesign-20261110`), on foxtrot:
  `git -C ~/Documents/Projects/roughcut merge --ff-only redesign/2026-10-14` then
  `systemctl --user restart roughcut-board`. Pushing main is yours too.

## Seven things, in order

1. **Watch three short films (5 minutes).** In `Karl OS/Builds/Roughcut/` (it syncs to your laptop):
   `killington-new-first-cut-2026-11-10.mp4` (2:57: the first pass on Killington since your B-roll note, K7: "pocket
   pizza" at 0:27), `copper-round2-first-day-editor-89s.mp4` (a newcomer asked for 90 seconds of Copper and fixed
   three things) and `copper-round2-premiere-editor-82s.mp4` (the same footage recut by hand, with titles and a grade).
   **Tell me:** is the mix of action and people what you meant? Is a Copper film like this worth sending to the
   friends who were there?
2. **Start a film from a folder yourself (K3, K5).** On the preview: File › New film from a folder…, pick a folder
   (Copper is `/mnt/roughcut/footage/copper-02-2026`; the Aug 2020 folders are under `/mnt/roughcut/space/backup-1-2-2021/`),
   say how long, run the first pass (about 5 minutes for Copper; free on your plan).
   **Tell me:** did it say what it was doing and at what detail, in words you believe? Did you get the length you asked for?
3. **Direct it in words for five minutes (K3).** Ctrl+J. Try a plain edit, then "what did you change?", "put it
   back", something only the picture can answer ("lose the bit where the camera points at the ground"), and "make a
   20 second vertical version for Instagram".
   **Tell me:** was anything a guess? Is this how you want to run the AI? Is the wait when it looks at frames
   (25-45 seconds) worth it?
4. **Find three moments you remember (K4, K6).** Selects › find a moment; then Window › Index and click a clip: what
   it found, what was said, Run to look again.
   **Tell me:** did Find land on the moment? Does the Index tell you what ran and what it found? Is Hybrid the right default?
5. **Cut by hand for five minutes (K2).** The keys you know. New since you last looked: copy / cut / paste, a typed
   timecode (click it or Ctrl+G), a selection that never follows the playhead (Delete acts on what you clicked and
   says what went), `[` and `]` trim the in-point one frame (Shift five, Alt the out-point), transitions you can select and trim, double-click a shot to open it in the Source monitor.
   **Tell me:** would you choose to cut in this? What looked like a toy first?
6. **Finish one (K2).** Ask for a title, move it by hand on its card; Audio › Add music… and set its level; Sequence ›
   Sequence Settings… › Frame › Vertical 9:16 on a copy of the cut; Export, put the dialog away, keep working.
   **Tell me:** is the exported file what the Program monitor showed?
7. **Six trades the loop did not make alone** (a line each is enough):
   a. The Index panel at laptop width leaves the timeline 38 % of the window. Let Effect Controls collapse while the
      Index is open, let the Index be narrower, or leave it?
   b. An insert past the end of the film now goes to the end instead of leaving a gap. Right?
   c. The selection no longer follows the playhead, so Effect Controls shows the film, not the shot under the
      playhead, until you click a shot. Right?
   d. A CPU export (when the graphics card is busy) is about 1 level off in red and blue against the previews.
      Closing it rebuilds every preview once. Do it?
   e. The Index shows 8 clip rows instead of 10 at laptop height, to make room for "what it found". Keep?
   f. Find's judged list takes about 12 seconds longer than its first hits. Keep the model step on by default?

## Known, so you do not have to report it

- Not built yet: J and L cuts, lift and gaps, a slide tool, typed speed / reverse / freeze, paste attributes for a
  grade, fonts and colours for titles, the camera's .WAV as the sound, a loudness target on export.
- The Program monitor going black in the first trial was the test browser, not the board; the board now also falls
  back to the ungraded picture if a browser ever loses its graphics, and says so.
- A third round of the six editors and the next build wave (the "not built yet" list above) were not started: the
  weekly plan limit was at 68 % with four days to its reset and Screek's loop draws on the same limit. Say the word
  and either runs.
- Everything was built and checked on two bins, Killington and Copper, and the second round ran on the bin the
  fixes were made on. A third folder that nobody has tuned on is the honest test: yours in step 2 would be it.

## How to answer

In your own words, in any order, here or by voice. Your words go into `REDESIGN-GOAL.md` section K verbatim and the
next wave starts from them. Judge 17 stays held until you have looked.
