# Wave 22: Karl's K7 (B-roll), a GPU read of the playback stall, and the park's loose ends

Karl, 2026-11-10, after the redesign went onto main and 8765: asked what moves Roughcut closest to the goal, then
"do all, in order except 3, I have more footage it can use here". The five were: (1) the K7 B-roll fix, (2) playback
measured on a real GPU, (3) a second test set (SKIPPED: he has more footage for it), (4) the try-list rewritten for the
live board, (5) back up and tidy. Judge 17 stays held (his K2-K6 look, or the second bin).

| Lane | Punch | Owns |
|---|---|---|
| BROLL | K7 (`judge/K7-AUDIT.md`, "Proposed fix") | `roughcut/events.py` (scoring constants, a talk / hangout moment source), `roughcut/revise.py` (`events_section`, `EVENTS_HEADER`, `FIRST_GUIDANCE`), `research/tools/audio_analyze.py` `build_candidates` or a pure re-score in `roughcut/picks.py`, `tools/eval_understanding.py` (a B-roll line), `research/R12-understanding/eval/baseline.json` only for the new line, `app/tests/test_events.py`, `test_revise*.py`, new `app/tests/test_broll.py`. Reads `verify.py` verdicts, does not change its routing. `app/server.py` only if the `events` slice must widen. |
| GPUPLAY (lead) | verdict 16 blocker 3, dimension 6 | no product file: a measurement under `~/roughcut-loop/gpuplay/` of the grade worker's commit stall in GPU-backed Chromium on the 3080 vs the headless software GL every number so far came from |
| lead | 4, 5 | `judge/FOR-KARL.md`, `judge/LOOP.md`, this file, the rubric log |

Box rule as wave 21: tests `nice -n 19`, never the full gate under nice, one Playwright at a time, GPU through
`~/.cache/roughcut/gpu.lock`, no model calls in the lane (the lead runs the one live Hybrid pass on a work copy after the
merge). Ports: BROLL 9190-9194, GPUPLAY 9195-9199. The root disk is at 91 %: lane scratch stays small, no video copies.
