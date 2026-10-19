# Wave 15: verdict 11's punch list — lanes and the files each owns

Verdict 11 (FAIL on 18944d0; the judge's gate GREEN): 15 of 16 at 8-9, dimension 10 at 7; K2 + K3 open (Karl's: punch 1,
the lead's job with Karl's yes). Punch items verbatim in `judge/verdict-11.md`. WAVE12.md's contracts still hold.

| Lane | Punch | Owns |
|---|---|---|
| FXUNDO | 2 | app/static/fx.js (accept → one undo entry always), roughcut/fx.py `verify` / `in_shot` and the designer's event rounding, server.py's fx undo/redo only if needed, tests |
| PERF5 | 3, 9 | app/tests/test_perf_latency.py (the clocks: rubric log 2026-10-19), app/static/grade.js's worker draw scheduling + mix (not the shader's look stages), app.js `txFollow` / `txHand`, tools/gate.py's perf counting (both runs must pass), tests incl. test_tx_playback |
| BUDGET | 4, 5 | server.py `media_poster` (never a 4K decode in the request; cards served `near=1` / cached), `api_assist_price`, `api_gap`, `api_firstpass_cancel`, `want_head` / `HEAD_HINT_WAIT_S` / the ETA in `/media/status`; dock.js's card/tile poster URLs (`near=1`, then the exact frame); app.js `mediaWaitFor`'s words only; tools/gate.py's journey line (`slowest_loop`, RED > 200 ms); tests |
| RERUN2 | 6 | roughcut/firstpass.py manual-edit detection (`manual_edits`, `how`), firstpass.js's re-run panel words, tests |
| VIGNETTE | 7 | effects.js's Lumetri Vignette section (+ Blur & Sharpen › Sharpen), grade.js's shader stages for them (not draw scheduling/mix: PERF5's), research/tools/assemble.py, roughcut/actions.py (+ assist vocabulary), tests |
| POLISH11 | 8, 10 | the proposal's Things to check naming (actions.js / firstpass.js checks list), aiprompt.js (chip never ellipsizes; price words on a fresh project), timeline.js's gap block title, dock.js's Source width when the Assistant opens (layout only; BUDGET owns the card URLs), app/tests/test_media_first.py's flake, tests |

Shared files: grade.js (PERF5 scheduling/mix vs VIGNETTE shader), dock.js (BUDGET card URLs vs POLISH11 layout),
firstpass.js (RERUN2 panel words vs POLISH11 checks naming), tools/gate.py (PERF5 perf counting vs BUDGET journey line),
app.js (PERF5 txFollow/txHand vs BUDGET mediaWaitFor words). Touch only your functions and list them in your report.

Box rule: every lane tests under `nice -n 19`, one Playwright process at a time; PLAY-style perf measurement (PERF5) runs
at nice 0 with `ps -eo ni,pcpu,comm --sort=-pcpu | head` beside each number. Ports: FXUNDO 8930s, PERF5 8935-8939,
BUDGET 8940s, RERUN2 8945-8949, VIGNETTE 8950s, POLISH11 8955-8959.
