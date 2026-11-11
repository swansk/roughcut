# Retest of my round-1 findings (old ids = round 1's WORDS-xx; new findings this round restart at WORDS-01)

old id | verdict | what I saw this time
---|---|---
old WORDS-01 | better | Default 'Sequence' chip still flips to 'Shot 1 · GX010474' when I press Play (new WORDS-02), but once I choose Whole sequence it stays.
old WORDS-05 | fixed | After choosing Whole sequence on the chip, 8 s of play left it on 'Sequence'; tooltip says it stays until I change it.
old WORDS-06 | fixed | 'what did you just change?' (12 s) quoted my request and listed the per-shot changes, honest that it landed at 78.4 s not 75.
old WORDS-02 | fixed | After Accept, Program showed the frame at the timeline playhead (00:00:35:09, both read the same), the new cut.
old WORDS-03 | fixed | After Accept the 'A new cut is ready · Review' banner was gone; Recent proposals marks it 'accepted'.
old WORDS-04 | fixed | After the AI put two new skiing shots first, Program drew them at 0:00-0:10 (8 of 8 looks), no black.
old WORDS-10 | fixed | 'ok look at it, and do both' after it offered options A and B: did A+B (ridge drop first, then the helmet-cam run) in one proposal.
old journal #27 'the second one' | fixed | Picked option B from its own list of two (12 s).
old WORDS-08 | fixed | 'at about 0:40 there is a wobble, lose it': it looked at 45 frames, said there was no wobble at 0:40 and named a real shake at 0:38.2-0.38.8 (checked: true), offering a trim instead of inventing one.
old WORDS-12 | fixed | Ground/sky ask: it looked at 90 frames of the whole cut, found the one lap/chair bit at 0:18-0:19, trimmed it, said no sky shots; my 3 s contact sheet agrees.
old WORDS-13 | better | Find a moment finished in 32 s (was >122 s stuck), but the hits are still not ground/sky (new WORDS-10).
old WORDS-17 | fixed | 'slow the biggest jump to half speed': looked at 89 frames, said nobody is in the air, offered the mound pass at 0:57-0:59; I checked 0:53-1:00, every claim true (mound, skis on snow, carve with the jug).
old WORDS-23 | better | The cut still opened with the airport shot sideways in Program (a -90° rotation on it, new WORDS-01), but 'the airport bar shot plays sideways, fix it' fixed it by words in 22 s (looked at 4 frames, set rotation 0); plays upright now.
old WORDS-15 | fixed | Title: one Accept in the Assistant, ~45 s later it was in, marked ACCEPTED in Designed Effects; while waiting it said 'the words run outside the frame — asking the designer again (1 of 2)'.
old WORDS-19 | fixed | 'swap the first two shots' (7 s): the proposal says the 'Copper 2026' title is attached to shot 1 and will no longer open the film, and offers to move it. I rejected it.
old WORDS-16 | better | Audio now has 'Add music… a file on this machine' instead of 'drop into assets/music and reload', but there is still no track to pick and the AI says it can't place or duck even an imported song (new WORDS-11).
old WORDS-18 | fixed | 'make it longer but cut half the shots' (11 s): 'These two pull in opposite directions', gave the numbers and three readings to choose from. Nothing changed.
old WORDS-09 | same | Asked a question while a proposal waited: the answer popover covers 'Reject all' (screen says covered) and leaves 'Accept' clear (new WORDS-13).
old journal #24 'fix it' | fixed | 'fix it' (9 s) asked what I meant and listed 4 real candidates, including a half-second ridge-drop repeat it then fixed on 'do 2'.
old WORDS-14 | fixed | Reloaded the tab (F5): undo still read 'undo: AI: trim (Ctrl+Z)' after the reload, and the 'Answered · Show' line came back.
old WORDS-11 | same | 'cut the last shot straight out on it's over + dip to black': did the dip, said it has no word timings (new WORDS-16).
old WORDS-22 | better | Still proposes to overwrite main with a 20.9 s horizontal cut (new WORDS-18), but when I said 'keep my main cut' it refused plainly and explained it has no copy and no 9:16; the Discard says Ctrl+Z brings it back.
old WORDS-07 | fixed | Played the whole 74 s film once in Program: 19 of 19 looks drew a picture (last look black = my dip to black at the end); the dissolve showed.
(builder claim) undo survives a switch to another project and back | worse than claimed | starter-bin and back: 'nothing to undo' (new WORDS-19). Reload is fine.
old WORDS-20 | fixed | The first title came out clean and inside the frame ('Copper 2026', checked at 5 frames); while designing it caught 'the words run outside the frame' and re-asked the designer itself.
old WORDS-21 | could not retest | The title was right first time, so there was no broken title to fix by words; no stacked titles appeared.

Totals (23 old findings + 2 old journal rows): fixed 15 (01-05→05, 02, 03, 04, 06, 07, 08, 10, 12, 14, 15, 17, 18, 19, 20) · better 5 (01, 13, 16, 22, 23) · same 2 (09, 11) · worse 0 · could not retest 1 (21). Plus journal rows 'the second one' and 'fix it': fixed. Builder claim 'undo survives a project switch': not true (WORDS-19).
