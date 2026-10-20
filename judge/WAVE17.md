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
