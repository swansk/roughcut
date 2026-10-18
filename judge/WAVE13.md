# Wave 13: verdict 9's punch list — lanes and the files each owns

Verdict 9 (FAIL on 7436562 = a426586's code): 13 of 16 dimensions at 8-9; 6, 9, 13 at 7; gate RED (perf, the
ungraded dissolve, the journey's step-15 browser close); K2, K3 open (Karl's). Punch items are verbatim in
`judge/verdict-9.md`; each lane's brief names its items. WAVE12.md's contracts and test hooks still hold.

| Lane | Punch | Owns |
|---|---|---|
| CANVAS2 (running since a426586) | 1 (perf + dissolves), 4 (poster ≤ 300 ms part) | app.js player / followers / `mediaWait*`, grade.js, server.py `/media/poster` + the preview queue |
| PRICED | 2, and 3's `gen_black_<hash>` name | server.py `start_free_index` / `index_ask_intake` / `_intake_after` and the index-join code, roughcut/journal.py, firstpass.js, the re-run's notes (roughcut/revise.py or firstpass.py), tests |
| EXPORT2 | 5, and 3's FRAME/BITRATE overlap | export.js / export.css, research/tools/assemble.py, roughcut/actions.py's `export` op, server.py `/api/render` + the size estimate, tests |
| BINLOOK2 | 9, and 3's Status column | dock.js Project panel, workspace.css Project rules, a new server.py section for a per-clip filmstrip sprite, tests |
| AIUX | 6's transition/fade ghost, 7, and 3's AI undo labels | actions.js, timeline-lanes.js (the ghost of a transition op), aiprompt.js, app.js's Assistant ask form (+ its index.html section), tests |
| SMALL4 | 1's journey diagnosis, 10's playhead clamp, 3's zero-length warning, the tour callout notes in dimensions 1 and 16 | tools/journey.py, timeline.js (playhead after undo), app.js's warning-range rendering, tour.js, tests |

Deferred to wave 14 (they share files with the lanes above): punch 4's "fresh clips play within 10 s" (CANVAS2's
player + server media path), punch 6's Look / Match preview (grade.js), punch 8 video effects (assemble.py, actions.py,
effects.js, dock.js bin tree), punch 10's event-loop holds (server-wide).

Box rule this wave: every lane runs tests under `nice -n 19`, one Playwright process at a time, so CANVAS2's
perf A/B (nice 0) keeps the CPU. Ports: CANVAS2 8860s, PRICED 8865-8869, EXPORT2 8870s, BINLOOK2 8875-8879,
AIUX 8880s, SMALL4 8885-8889.
