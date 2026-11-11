# hands · round 2 retest

HANDS-02 | fixed | Home + Space draws shot 1 (GX010474, a different first shot this cut) in Program from 0:00, 8 of 8 looks; GX010491 (last round's black opener) is shot 11 of my cut now and draws in Program.
HANDS-04 | fixed | With the Source focused (blue border), L / J / K move only the Source timecode (5:08 → 7:01 → 6:01); Program stays put. New: L will not run past the Out mark (new R2 finding HANDS-01).
HANDS-01 | fixed | After K the meters drop to empty within a second (4 of 4 looks); only a small red peak tick stays, which is normal.
HANDS-03 | fixed | Double-click on a timeline shot opens it in the Source with In/Out set to the shot, and says so in a toast; it no longer plays. Playhead jumps to shot start though (new R2 finding HANDS-02).
Note: round-2 findings.jsonl numbers restart at HANDS-01; below, 'R2-nn' means a round-2 finding, plain HANDS-nn means the round-1 one.
HANDS-08 | fixed | Clicking the timeline timecode or Ctrl+G opens a field; 5108 Enter → 00:00:51:08 and Program shows that frame; nonsense gets a plain message. Grammar quirks logged as R2-03.
HANDS-05 | fixed | Shift+Delete on shot 4: Program at once shows the night-van shot that now sits at the playhead, toast 'removed shot 4 · GX010476 (7.0 s) — Ctrl+Z brings it back'.
HANDS-10 | fixed | Ctrl+C / Ctrl+X / Ctrl+V now work on shots, are in the Edit menu, toast what they did, and one Ctrl+Z takes a paste back without touching older edits. Paste inserts rather than overwrites (R2-04).
HANDS-12 | better | Clicking V2 now moves the video target off V1 (only one video target lit), and '.' laid the cutaway on V2 alone; but A1 stays lit, so to keep the dialogue I still had to remember to switch A1 off by hand (no source-patch column says 'this clip's sound goes to A1').
HANDS-14 | fixed | Chose ½× in the Speed dropdown, then Home, ↓×3, ↑, →: the playhead moved every time and the speed stayed ½× (0.5× badge, 1:47:09). Same for a typed Scale and a dragged Lumetri slider.
HANDS-18 | fixed | Home, Ctrl+Shift+D: 'Cross Dissolve + Constant Power · 1.00 s fading the film in' (Program black at 0:00); End, Ctrl+Shift+D: 'fading the film out', last frame black. Each is one undo step with a toast.
HANDS-19 | same | Cut switcher: 'The cut 13 shots · 1:47' and 'main 13 shots · 1:35' for the same cut after a ½× speed change (R2-12).
HANDS-07 | fixed | After F5 the ↶ tooltip still names my last edit; undo 4, F5, undo the rest = exactly the starting cut; redo all = exactly the finished cut. But switching to another cut and back now empties the history (R2-17).
HANDS-09 | same | Still no slide tool (U does nothing; sheet lists V C B N Y A). Slip (Y) still runs opposite to Premiere: dragging shot 3 RIGHT moved its source range later (0.00 → 1.54 s); dragging left at media start did nothing. The live 'slip 0.00s–5.17s · 5.17s long' readout is good.
HANDS-11 | same | No lift / extract (; and ' do nothing, no menu item), Ctrl+X and Delete both close the hole, a body drag 30 px right snapped back with no change. I still can't leave a gap on purpose.
HANDS-13 | same (not built, as the builders said) | A1 edges show a plain pointer, no trim cursor; hover text now says 'its own sound, linked to the picture on V1 … Alt-click: unlink', so the unlink trap is at least labelled. No J or L cut by hand.
HANDS-15 | same | Speed is still the six-step dropdown (¼ ½ ¾ 1 1.5 2); no reverse, freeze, typed % or duration; Clip menu sends 'Speed and colour…' to Effect Controls. Typed keys no longer change it (see HANDS-14).
HANDS-16 | same | No Paste Attributes in Edit, Clip or the shot's right-click (which has only 'Ask AI about this…' / 'Look deeper…'); Ctrl+C/V copy whole shots, not their grade. Checked again with a grade in the film-making below.
HANDS-17 | better | Titles are still AI-made (Effects has no Type tool), but the lower third 'Spencer — the milkman' now sits inside the frame with a whole bar, both titles say '✓ checked · inside the frame' and match the picture, and 'edit by hand' gives words / size / x-y / in / for without a model call; one Accept each. No font or colour (builders say not built). One lost words edit (R2-22).
HANDS-06 | fixed | After ~70 min of editing (two cut switches, three F5s, ~60 edits) I parked 1.2 s into each of the 14 shots by typed timecode: all 14 drew a picture in Program (the two night shots dark but there), no reload needed.

Totals: fixed 11 (01 02 03 04 05 06 07 08 10 14 18) · better 2 (12 17) · same 6 (09 11 13 15 16 19) · worse 0 · could not retest 0
