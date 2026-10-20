# Wave 17: Karl's K4 — the Index panel, and the old pages folded in

Karl, 2026-10-19, trying `b12b979`: "1. it is still not clear to me what indexing was run over the existing footage, or
how to re run / tune that 2. The previous parts where you click on the top and can get to the "pass" and stuff is still
here... odd". He chose: an **Index panel** in the editor, and **fold in, then remove** `/open` and `/floor`. Bars in
REDESIGN-GOAL.md's rubric log (2026-10-19, K4). Judge 13's punch list (running) joins this wave when it lands.

| Lane | Owns |
|---|---|
| INDEXPANEL | new `app/static/indexpanel.js` + `.css` on `/modules/`, its mount + Window › Index item in index.html, its dock tab (dock.js: the panel registration only), server.py's index endpoints (per-clip / per-selection re-run and re-tune; the per-clip stage report from the journal), roughcut/journal.py if needed, tests |
| FOLDIN | the audit of every `/open` and `/floor` feature and where it lives in the editor; moving what is missing (except the index controls: INDEXPANEL's); the File menu's "Footage and index…" / "Review the pass…" items, dock.js COMMANDS `footage` / `pass`, flow.js's Next targets, tour.js steps and cards that point at those pages, server.py's `/open` / `/floor` routes (answer with the editor), deleting open.html/js and floor.html/js once nothing needs them; the tests that drive those pages (`test_open_ui`, `test_floor*`, test_budget's `/floor` `/open` screens, the journey if it touches them) |

Contract between them: INDEXPANEL exposes `window.rcIndex = { open(clips?), rerun(clips, opts), state() }` and test hooks
`index-panel`, `index-row` (`data-clip`), `index-stage` (`data-stage`, `data-state`), `index-rerun`, `index-interval`,
`index-price`; FOLDIN points every former "Footage and index…" entry (the menu, the Next chip's index target, the tour)
at `rcIndex.open()`.

Box rule: tests under `nice -n 19`, one Playwright process at a time, no nice-0 load. Ports: INDEXPANEL 9005-9009,
FOLDIN 9010-9014. Karl may be using his 8766 preview: never restart `roughcut-redesign`; the lead holds merges into the
redesign worktree (which 8766 serves) until Karl says he is done looking.

## Added after verdict 13 (FAIL on b12b979; the judge's gate GREEN; 15/16 at 8-9, 16 at 7 from K4)

| Lane | Punch | Owns |
|---|---|---|
| VFXBLUR | 3 | grade.js's Blur / Sharpen stages and the worker's scheduling around them |
| PLAY8 | 9 + FRAMEACC's monitor items | app.js's player (cue, arm, boundary, preroll, play start), timeline.js `shotAt` clip time, server.py `build_poster` time rounding |
| TESTS2 | 4, 7 | the flaky tests (test_poster_time, test_proposal_review:169, …), test_lumetri_ui `arm()` |
| SELECTS | 5, 8 | dock.js `layout` / `tlHeadNeed` / Selects width, tour.js `NEVER_COVER` / placement |
| REDO | 6 | fx.js's design undo / redo, server.py fx discard / restore |
| SNAP | 10 | server.py `snap_respond` / snapshots / `_cause` + the import-burst handlers, roughcut/firstpass.py's re-run prompt |

Shared with the first two lanes: dock.js (FOLDIN COMMANDS, INDEXPANEL panel registration, SELECTS layout), tour.js (FOLDIN
steps/cards, SELECTS placement), server.py (INDEXPANEL index endpoints, FOLDIN routes, PLAY8 posters, REDO fx, SNAP
snapshots). Ports 9015-9044 by lane in the order above.
