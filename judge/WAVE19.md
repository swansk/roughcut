# Wave 19: verdict 15's punch list

Verdict 15 (FAIL on `5138865`; 1-5 and 8-16 at 9, 7 at 8, 6 at 7; the judge's gate GREEN, 1 flaky; the lead's RED on
perf after the suite). Blockers: K2-K4 (Karl's) and dimension 6 (a cut out of a 0.5x shot freezes 116-133 ms 4/4; dissolves
with a slowed side hold 16/16; the graded dissolve test 2/4 alone at load 7-8; the 30.5 fps draw cap). Punch 1 (Karl's
look, 8766 restart from a frozen snapshot with its v5 proxies prebuilt) waits for Karl's yes: it writes his preview's work
copy under /mnt/roughcut. All lanes branch from the commit that adds this file.

| Lane | Punch | Owns |
|---|---|---|
| PLAY10 | 2, 3 | app.js's cut roll out of a slowed shot and the transition buffers (`pvTxIn` / `pvTxOut`) at rate < 1; grade.js `paceDue` / the slowed shot's exit and the mix when one side is slowed (`G.mix`, `mixDrawn`); new cases in `test_cut_frames.py` and `test_tx_playback.py` |
| WORKER | 4, 5, 6 | grade.js's page -> worker frame hand-off (`toWorker`, `post`, the worker's `onmessage` frame branch), `MAX_DRAW_FPS` / `tooSoon` tied to the Program's Playback Resolution (app.js: the menu only), `test_playback_resolution.py`, the half-speed bar in `test_cut_frames.py` (its assertion lines only) |
| LOOPHOLD | 7 | server.py's loop memory lock and what the loop touches under page-cache pressure (`utils.py _get_param_aliases` path), a new loop-hold test or the gate's boot check |
| EFFECTS2 | 8 | effects.js, grade.js's vfx stages (`stagesOf`, the Crop / Transform stages; not the hand-off or pacer), roughcut/vfx.py (export), roughcut/actions.py (the AI ops), their tests |
| POLISH13 | 9, 10 | the Project List view's sideways scroll (app.js bin list / workspace.css), timeline-lanes.js's keeps lane label, `test_import_snap.py` (the facts-change case + mutant) |

Shared: grade.js (PLAY10 pacer / mix, WORKER hand-off / cap, EFFECTS2 vfx stages: different functions, say which you
touched), app.js (PLAY10 cut / transition buffers, WORKER the Playback Resolution menu, POLISH13 the bin list),
test_cut_frames.py (PLAY10 new cases, WORKER the half-speed assertions). Ports: PLAY10 9080-9084, WORKER 9085-9089,
LOOPHOLD 9090-9094, EFFECTS2 9095-9099, POLISH13 9100-9104.

Box rule: tests under `nice -n 19`, one Playwright process at a time per lane; nice 0 only for PLAY10's and WORKER's own
timing probes (they share the box: interleave A/Bs, report load). LOOPHOLD's 4 GB page-cache stress runs only in short
windows, announced in its scratch `STRESS.now` file (PLAY10 / WORKER: don't measure while it exists). Every server with
`--work` and `ROUGHCUT_LEDGER` under the lane's scratch. Bars that change are logged by the lead: write the proposed entry
in your report. Never call a change a fix on fewer than ~20 plays a side, pooled.
