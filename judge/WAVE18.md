# Wave 18: verdict 14's punch list

Verdict 14 (FAIL on `799d8b8`; 15/16 at 8-9, dimension 6 at 7; the judge's gate GREEN with 3 flaky under its load).
Blockers: K2-K4 (Karl's), dimension 6 (0.5x judder on every play; 59.94 clips show a new picture on half the frame
steps), section S (the call ledger ignored `--work`: fixed by the lead in `1fc6907`, punch 9). Punch 1 (Karl's look):
the lead updates FOR-KARL.md; the 8766 restart from a frozen snapshot waits for Karl's yes. All lanes branch from
`1fc6907`.

| Lane | Punch | Owns |
|---|---|---|
| FRAME59 | 2 | server.py's proxy build for 59.94 sources (rate, `PROXY_V`) or a parked still from the original; app.js's parked-frame path (`parkAt`, `parkedOn`, `paintRestStill`, `posterSrc`, the cue's park); new `test_parked_frame_5994.py` |
| PLAY9 | 3, 4, + 10's Blur at 1920 | app.js's rate path for a slowed shot and the roll at a plain cut (`rollStart`, `cutNow`, `advance`, `tick`, `preroll*`); grade.js `armFrames` / `drawWhenShown` / the Blur stage; `test_tx_playback.py` (the canvas on the rAF timestamp + worker clock, the bar in frames), `test_cut_frames.py`, `test_vfx_playback.py` |
| UNDO5 | 5 | passreview.js `verdict` / `undo`; the board's one undo history (timeline-keys.js / `tl.mark`); `test_passreview_ui.py` |
| INDEX2 | 6, 8 | tour.js (an Index mention), firstpass.js (its Index step links the panel), dock.js (the AI menu item; the Index width), indexpanel.js / .css (Run's hover card, the foot, the summary), server.py `/api/index/clips` (a batch hear stage per clip from its sidecars) |
| REDO2 | 7 | fx.js `withdraw` / `redoAsks`; server.py's fx job cancel; `test_ai_honest_ui.py` (the in-flight case and the set-aside race) |
| POLISH12 | 10 (rest) | timeline.js's ruler last label; server.py's status read right after an import POST (`/api/firstpass` 526 ms on the snapshot rebuild) |

Shared files: app.js (FRAME59 the park, PLAY9 the play/roll/cut), grade.js (PLAY9 only), server.py (FRAME59 proxies,
INDEX2 index endpoint, REDO2 fx cancel, POLISH12 status), dock.js (INDEX2 only), timeline.js (POLISH12 ruler; UNDO5 only
if the undo stack lives there). Ports: FRAME59 9050-9054, PLAY9 9055-9059, UNDO5 9060-9064, INDEX2 9065-9069, REDO2
9070-9074, POLISH12 9075-9079.

Box rule: tests under `nice -n 19`, one Playwright process at a time per lane; nice 0 only for PLAY9's own timing
probes. Every server started with `--work` under the lane's scratch dir (the ledger now follows it; set
`ROUGHCUT_LEDGER` too). Bars that change (PLAY9's clock and frame bar; INDEX2's screens budget if a tour step or menu
item adds words or controls) are logged by the lead in REDESIGN-GOAL.md before the next judge: each lane writes the
proposed entry in its report.
