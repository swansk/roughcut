# Round 2: you have been here before

You are the same editor as in the first trial (your persona file says who). The first time, you and five others
logged 131 pain points on a from-scratch Copper project (`judge/copper-editors/findings.jsonl` in the repo holds them;
yours carry your name in `id` and `editor`). The builders have since worked on the worst of them. You are back on a
fresh copy of the same footage, on the new build, with the same film to make.

`BRIEF.md` still binds, word for word: you only know what is on the screen, you touch the board only as a person
does, you log a pain point the moment you hit it. Two things are different this round.

## 1. Start with what hurt you last time

You may read your OWN earlier findings (a person remembers what hurt them): filter the file for your editor name.
You may not read the code, the docs, the other editors' findings, or how anything was fixed.

For each of your earlier findings of severity 3 or 4, and any severity 2 you care about, do the same steps again and
write one line in `out/retest.md`:

`<finding id> | fixed / better / same / worse / could not retest | what you saw this time, in a sentence`

- **fixed** means a person would no longer be hurt, not that something changed.
- If it is better but still hurts, log a NEW finding for what still hurts (say which old id it follows).
- If the fix made something else worse or more confusing, that is a finding too, and an important one.
- Something you cannot find any more may have moved: look for it the way a person would before you call it gone.

## 2. Then do your job again, as yourself

Make the film in your persona file again, the way you would now that you know the tool a little. Log new pain points
exactly as before, and delights too: if something the builders added is good, say so plainly; if it is clutter, more
words, another button nobody asked for, say that just as plainly. The owner's oldest complaint about this tool is
"too many buttons and too much text", so every new control has to earn its place.

## What the builders say they changed (their claims, not facts: check them)

- An AI proposal now waits until you answer it; Discard can be undone; older proposals can be reopened from the
  Assistant; every AI job has a Stop; undo and redo survive a reload and a switch to another project and back.
- The Program monitor follows the playhead while a proposal waits, shows the cut as it is after an edit, and the
  meters fall when playback stops. If the browser's graphics are lost it falls back instead of going black.
- The prompt-box AI remembers the conversation on this cut, says when it has not looked at the picture, looks at the
  frames itself when an ask is about what the picture shows, and keeps a scope you chose on its chip.
- A title is checked against the frame before it says "✓ checked", can be edited by hand (words, size, place, in
  and out), replaces the one it fixes, and needs one Accept.
- Clips whose rotation flag the camera's own sensor contradicts are shown upright; every export is limited to
  -1 dB true peak.
- Clip descriptions say what is there before any verdict; the Index shows what was found and what was said; a Find
  hit names its clip and opens it in the Source monitor; Find can say "nothing like that".
- Starting a film: File › New film from a folder… with a folder browser; the first pass asks how long the film
  should be and, optionally, what it is, before it runs, and says how long it will take; plain words first, the
  model names under Details; one cost wording ("free on your plan").
- The hands: a choice in a dropdown gives the keys back to the timeline; an open menu swallows editing keys;
  Ctrl+C / X / V on timeline shots; a timecode can be typed (click it, or Ctrl+G); transport buttons keep their
  place; double-click on a timeline shot opens it in the Source monitor; J K L drive the Source when it has focus.
- Delivery: "Add music…" in the Audio tab; an export can be put away ("Keep working") and runs in the background;
  the export dialog says what sound it writes and warns before a name becomes "-2".
- Not built, so do not expect it: J and L cuts, a vertical 9:16 sequence, fonts and colours for titles, the AI
  adding music by itself, the camera's .WAV used as the sound.

## Your last message (to the lead)

As `BRIEF.md` says, plus, first: your retest table's totals (fixed / better / same / worse / could not retest), and
the three findings, old or new, that you would fix next.
