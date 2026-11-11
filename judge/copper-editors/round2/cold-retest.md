# cold — round 2 retest (one line per old finding)

COLD-01 | fixed | File menu now starts with "New film from a folder…"; the empty timeline also has a "New film from a folder…" button.
COLD-02 | fixed | Tour step 1 is now "Start with your footage" and names File › New film from a folder… (bubble hops around the screen when I click elsewhere, polish).
COLD-03 | better | Help now has "Start a new film" and "What the first pass does" besides the tour and shortcuts; still no search.
COLD-04 | fixed | New film from a folder… opens a real folder browser that shows how many videos each folder has; I got to copper-02-2026 in four clicks without typing a path.
COLD-07 | fixed | First pass panel now says 'Takes about 6 minutes' and 'free on your plan', no dollar figure.
COLD-05 | fixed | Cost wording is now only 'free on your plan'; no dollars anywhere I have seen so far.
COLD-09 | fixed | First pass now asks 'How long should the film be?' (typed 90 s) and the bullet updated to 'proposes a rough cut about 1:30 long'; result checked below.
COLD-08 | better | Before the pass GX010474 was still sideways in Source with a wide poster; a minute into the pass the three sideways clips switched to upright ('flag ignored'), GX010492 confirmed upright in Source. Before-pass state still wrong (new COLD-01, COLD-02).
COLD-06 | better | Index now opens with a plain sentence ("What the AI knows about each clip… you only need this table to look again at one clip"), but the columns still read Heard / Looked / Close / Telemetry / Preview / Local (3080) / Cost / Ran and the model menu still says 'Hybrid (3080 + Claude)'.
COLD-18 | same | Opening Index still widens the left panel to ~600 px and squeezes the Timeline to under half.
COLD-10 | better | The tags are now explained in one line under the list ('unsupported = a closer look found nothing there · unchecked = not looked at closely yet'); still a red 'unsupported' on the very first shot it kept, which reads alarming.
COLD-11 | fixed | Program timecode now runs with playback (00:00:03:21 of 00:01:35:11) and the button reads '❚❚ Pause' while playing, '▶ Play' when stopped.
COLD-12 | fixed | With the proposal waiting, a click on the ruler moved the playhead and the Program monitor showed that frame (shot 1 at 0:03, shot 5 at 0:32).
COLD-13 | same | The opening shot of the new proposal is again GX010474 (airport bar) and it plays sideways in the Program monitor, tall and narrow with faces on their side, although its own description says "recorded sideways" and its chip says "shot upright".
COLD-15 | fixed | Played the accepted cut from 0:00 to 1:35: all 13 shots showed picture, the whole ski half included, none black (watched once, 16 looks).
COLD-14 | fixed | The Pause/Play button stayed in the same place while the shot label changed from '1/13 · GX010474' to '13/13 · GX010495'.
COLD-16 | better | Meters move with the sound while playing and show nothing after it stops at the end; once, right after I pressed Stop, they still showed a high green bar for a moment.
COLD-19 | fixed | Export dialog now says 'peaks are limited at −1 dBTP (true peak), so the file does not clip'; the file measures true peak −1.7 dBFS, −15.8 LUFS.
COLD-20 | fixed | When the export finishes the dialog shows a big blue '↓ Download' next to Play / Show file, fully visible at 1536x864.
COLD-17 | fixed | This time the AI's notes and 'things to check' only referred to what was there (milkman, helmeted man, dark van shot); nothing I never said was quoted back to me. (One run: could be luck.)

Note: new findings in out/findings.jsonl restart at COLD-01 (kit numbering); they are this round's, not the old ids above.
Totals: fixed 13 (01,02,04,05,07,09,11,12,14,15,17,19,20) · better 5 (03,06,08,10,16) · same 2 (13,18) · worse 0 · could not retest 0
