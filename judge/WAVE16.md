# Wave 16: verdict 12's punch list — lanes and the files each owns

Verdict 12 (FAIL on 992ca33; the judge's gate GREEN, both perf runs): 15 of 16 at 8-9, dimension 6 at 7 (Sharpen plays at
5-7 fps); K2 + K3 open (Karl's: punch 1, the lead's job with Karl's yes). Real dissolves now 0/24 graded, 0/12 ungraded
inside the mix. Punch items verbatim in `judge/verdict-12.md`. WAVE12.md's contracts still hold.

| Lane | Punch | Owns |
|---|---|---|
| VFXPLAY | 2 | grade.js's shader stages for Sharpen + Gaussian Blur (at Playback Resolution while playing), effects.js `vfxLook` (drop the CSS/SVG filter where the grade draws), new app/tests/test_vfx_playback.py |
| PERF7 | 3, 10 (the 83 ms after a graded dissolve) | grade.js's worker scheduling (not shader stages), app.js live-buffer delivery / dissolve after-window, tests |
| MARKERS | 4 | timeline.js markers (name, comment, colour, duration; Marker dialog; ruler names + hover), server.py's EDL `markers` schema, roughcut/actions.py `_c_marker` + the vocabulary, research/tools/assemble.py chapters if any, tests |
| AIHONEST | 5, 6 | actions.js's `design_effect` accept path, aiprompt.js's result line for it, fx.js (the designs' discard on Ctrl+Z), roughcut/assist.py's shot naming, app.js `clipNamed` (ids → "shot N"), roughcut/fx.py notes, tests |
| SRVBUDGET2 | 7, 10 (the ETA) | server.py's poster burst at a pass's end, `api_firstpass`, `api_sprites`, `snap_poll` / roughcut/dirwatch.py, memory during a pass; `/media/status` ETA; tests |
| LAYOUT2 | 8, 9, 10 (the re-run words) | dock.js's Selects width vs the Timeline's minimum, aiprompt.js's chip/input minimum widths + the result line placement (AIHONEST owns its design_effect words), timeline.js's ruler label density (MARKERS owns markers), tour.js card placement + hover-card close, test_lumetri_ui's 150 ms bars, firstpass.js's "cut by hand" words for accepted AI trims/speed, tests |

Shared: grade.js (VFXPLAY shader vs PERF7 scheduling), timeline.js (MARKERS markers vs LAYOUT2 ruler labels), aiprompt.js
(AIHONEST design_effect words vs LAYOUT2 widths), app.js (PERF7 player vs AIHONEST clipNamed), server.py (MARKERS schema vs
SRVBUDGET2 posters/firstpass/sprites/status). Touch only your functions; list them in your report.

Box rule: tests under `nice -n 19`, one Playwright process at a time; perf measurement (VFXPLAY, PERF7) at nice 0 with
`ps -eo ni,pcpu,comm --sort=-pcpu | head` beside each number; NO nice-0 burners or exports from any other lane. Ports:
VFXPLAY 8965-8969, PERF7 8970s, MARKERS 8975-8979, AIHONEST 8980s, SRVBUDGET2 8985-8989, LAYOUT2 8990s.
