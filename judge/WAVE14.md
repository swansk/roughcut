# Wave 14: verdict 10's punch list — lanes and the files each owns

Verdict 10 (FAIL on 9470064 = 69fb0f0's code; judge's gate GREEN): 15 of 16 dimensions at 8-9, 10 (responsive and
reliable) at 7; K2, K3 open (Karl's). Punch items are verbatim in `judge/verdict-10.md`; each brief names its items.
WAVE12.md's contracts and test hooks still hold.

| Lane | Punch | Owns |
|---|---|---|
| SERVERPERF | 1 | server.py `_fx_verify_job` / `_fx_proof` / `_fx_autoverify` + the fx job's progress, the upload PUT handler, the event-loop hold sources it finds (pricing a prompt, poster builds — coordinate in your report); roughcut/fx.py `part_graph` and the overlay frame renderer; tests |
| AIOPS3 | 2, 7 | actions.js (accept path, `livePreview`, `lookOn`/`lookOff`/`guard`), app.js `save` / `touch` and the header's save state, grade.js's in-memory look LUT (not its mix), tests |
| TIMEBASE | 3 | server.py `sequence_fps` / `sequence_payload` + the EDL's stored `fps`, roughcut/tracks.py, the range snapping in roughcut/firstpass.py / revise.py / actions.py, timeline.js's sequence-rate code, export.js "Match Sequence", Sequence › Sequence Settings… (index.html's Sequence menu item + its dock.js command), tests |
| PLAY4 | 5, 6 | app.js player (`txFollow` / `txHand` / park / `mediaWaitFor` / programSync), grade.js's mix, server.py's preview queue (a 360p range around the playhead of a waited-on clip first), tests incl. test_tx_playback's short-handle dissolve |
| VFX | 8 | dock.js's Effects bin tree, effects.js, research/tools/assemble.py, roughcut/actions.py's new video-effect ops, tests |
| POLISH10 | 9, 10, the journey retry → AMBER | app.js `#playingWhat`, roughcut/firstpass.py `new_clip_rows`, tour.js, timeline.js's right-click on a narrow block / trim edge, the header's job readouts (app.js / firstpass.js), test_status_cheap's flake, tools/gate.py + tools/journey.py (a retried step = AMBER), tests |

Shared-file notes: server.py has four owners in different regions (SERVERPERF fx/upload, TIMEBASE sequence fps, PLAY4
preview queue, none else); app.js three (AIOPS3 save, PLAY4 player, POLISH10 #playingWhat + jobs); grade.js two
(AIOPS3 look LUT, PLAY4 mix); timeline.js two (TIMEBASE rate, POLISH10 context menu); actions.py two (TIMEBASE snapping,
VFX new ops). Touch only your functions; say in your report which ones you touched.

Box rule: every lane tests under `nice -n 19`, one Playwright process at a time; PLAY4 measures playback at nice 0
and records `ps -eo ni,pcpu,comm --sort=-pcpu | head` with every number. Ports: SERVERPERF 8900s, AIOPS3 8905-8909,
TIMEBASE 8910s, PLAY4 8915-8919, VFX 8920s, POLISH10 8925-8929.
