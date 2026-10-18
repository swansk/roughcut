# Wave 12: Karl's K3 (AI-first on top of the pro tools) — lanes and contracts

Written by the lead on 2026-10-18 when the loop resumed. The bar is REDESIGN-GOAL.md section E
(dimensions 13-16). The scaffold commit that goes with this file added the menu items, the mount
points, the four module stubs and the dock.js command routes, so no lane needs to touch another
lane's files to be reachable. Read this whole file before writing code.

## Lanes and the files each one owns

| Lane | Dimension | Owns (only these; anything else is a request to the lead) |
|---|---|---|
| IMPORT | 13 | `roughcut/media.py` (new), `app/static/import.js` + `import.css`, server.py's footage model (`footage_clips`, `configure`'s clip scan) + new `/api/import*`, `/api/media/*`, `/api/upload` endpoints, firstpass.js's "N new clips" offer, `app/tests/test_import*.py` |
| AIOPS | 15 | `roughcut/actions.py` (new), `roughcut/assist.py` (new), server.py's new `/api/assist*` endpoints, `app/static/actions.js` + `actions.css`, the FX accept path (fx.js accept + app.js `roughcutRefresh`'s `clearHistory`), `roughcut/edits.py` if reused, `app/tests/test_actions*.py`, `test_assist*.py` |
| AIPROMPT | 14 | `app/static/aiprompt.js` + `aiprompt.css`, the AI menu block in index.html, the `#aiPrompt` mount, context menus (by event delegation from aiprompt.js, no edits in timeline*.js / dock.js), `app/tests/test_aiprompt*.py` |
| TEACH | 16 (learn) | `app/static/tour.js` + `tour.css`, the Help menu's tour item, app.js `emptyState()` (Timeline) and the Program's empty line, index.html's Source empty line, hovers on AI controls (attached from tour.js by selector, no edits in other lanes' files), `app/tests/test_tour*.py` |
| BINLOOK | 16 (look) | `app/static/dock.js` Project panel (`paintProject`, list / icon views, the empty state), `workspace.css`'s Project rules (`table.plist`, `.picons`), `app/tests/test_bin_views*.py` |

Shared files: `app/tests/test_budget.py` (each lane sets its own measured numbers and logs the delta
in its report; the lead re-measures after the merge), `CHANGELOG.md` (each lane adds its entries; the
lead unions them), `REDESIGN-GOAL.md` rubric log (the lead writes entries from your reports).

## Contracts (fixed; a lane that needs one changed asks the lead)

### Globals (stubs exist in the four modules)
- `window.rcImport = { open(), browse(), drop(files) }` (IMPORT). The menu's `import` and
  `mediabrowser` commands call `open()` / `browse()`. Ctrl+I opens Import (bind it in import.js;
  check `timeline-keys.js:823` keeps ⌥I).
- `window.rcActions = { apply(ops, {label}), review(result), describe(op) }` (AIOPS).
  `apply` runs every op through the same `window.tl` / `window.effects` call a human edit makes,
  inside ONE `tl.begin(label)` / `tl.commit()` (one undo entry), and returns
  `{applied, refused:[{i, why}]}`.
- `window.rcAI = { focus(text), ask(text, scope), command(name), askAbout(target), scope() }`
  (AIPROMPT). `command(name)` covers the AI menu: `prompt firstpass ask shot colour fx deep find
  audit`. `askAbout({kind:'segment'|'clip'|'range', id, clip, in, out})` pre-scopes the prompt.
- `window.rcTour = { start(which), active }` (TEACH). `start()` = the whole loop, `start('ai')`
  = the AI features.
- Key rows: a module pushes `[key, what, group]` onto `window.rcExtraKeys` (groups: play, trim,
  move, bin, source, ai); dock.js's keys sheet shows them. Re-check the sheet fits at 1366×768.

### Test hooks (data-testid; the journey and TEACH's steps rely on them)
- IMPORT: `menu-import`, `menu-media-browser` (scaffold), `import-dialog`, `media-browser`,
  `media-browser-path`, `media-browser-row`, `import-go`, `import-cancel`, `project-drop` (the
  drop overlay on the Project panel), `import-progress`.
- AIPROMPT: `ai-prompt` (scaffold mount), `ai-prompt-input`, `ai-scope` (the scope chip),
  `ai-status` (working / done / failed), `ai-result` (the one-line result with its Review link),
  `menu-ai` (scaffold).
- AIOPS: `ai-review` (the review panel), `ai-op-row` (one per op, `data-op-index`, `data-op`),
  `ai-op-accept`, `ai-op-reject`, `ai-accept-all`, `ai-reject-all`.
- TEACH: `tour`, `tour-step` (`data-step`), `tour-next`, `tour-back`, `tour-skip`, `menu-tour`
  (scaffold).
- BINLOOK: `project-view-list`, `project-view-icon`, `project-row` (`data-clip`), `project-sort`
  (`data-col`), `project-sequence` (`data-cut`), `project-subclip`.

### Server (AIOPS owns; AIPROMPT codes against it and fakes it with Playwright `page.route` until AIOPS merges)
- `POST /api/assist {text, scope}` → `{job}` at once (never waits on the model).
  `scope = {kind:'selection'|'range'|'sequence'|'clip', ids?:[...], in?, out?, clip?, playhead}`.
- `GET /api/assist/{job}` → `{status:'running'|'done'|'failed', kind:'ops'|'plan'|'answer',
  ops?:[...], plan_ask?:<ask job id for the existing proposal path>, text?, summary, cost, error?}`.
  `kind:'plan'` = a whole-cut rewrite: the client shows it through the existing
  `rcShowProposal` path. `kind:'answer'` = a question answered in words, no edit.
- `POST /api/assist/{job}/answer {accepted:[i...], rejected:[i...]}` records the review.
- Every model call goes through `roughcut.inference` (section S); the cost line follows section S's
  wording rules (`rcCost`, "free on your plan" on `claude_cli`).
- Tests use a deterministic backend via `inference.set_backend(...)` (see `tools/journey.py`
  `scripted_model` for the pattern). Never a real model call in a test.

### The op vocabulary (draft; AIOPS owns the final schema in `roughcut/actions.py` and documents it there)
Times: `at` / `t` are film (sequence) seconds; `in` / `out` are clip seconds; `id` is a segment id the
server sent. Each op maps to the human path named after it.
- Timeline: `insert {clip,in,out,at}` → `tl.insertAt`; `overwrite {clip,in,out,at}` → `tl.overwrite`;
  `append {clip,in,out,after}` → `tl.insert`; `move {ids,before}` → `tl.move`; `razor {id,at}` →
  `tl.split`; `ripple_delete {ids}` → `tl.remove`; `trim {id,in?,out?}` → `tl.setRange`;
  `roll {a,b,by}`; `slip {id,by}`; `speed {id,rate}` → `tl.setSpeed`; `marker {at,name?}` →
  `tl.addMarker`; `in_out {in,out}` → `tl.setSeqIn/Out`; `track_add {kind}` → `tl.addTrack`;
  `overlay {clip,in,out,at,track?}` → `tl.addOverlay`.
- Effect Controls: `set {id,prop,value}` → `effects.set` (props: posx posy scale rotation opacity
  volume_db temperature tint exposure contrast highlights shadows whites blacks saturation hsl_*);
  `keys {id,prop,keys:[[t,v],…]}` → `effects.animate` / `effects.key`.
- Lumetri: `curve {id,channel,points}` → `effects.curve`; `wheel {id,range,x,y,level?}` →
  `effects.wheel`; `look {ids|'all',name,strength}` and `match {ids,reference}` → the colour patch.
- Transitions: `transition {a,b,kind,dur}` → `tl.addTransition`; `fade {id,edge,kind,dur}` →
  `tl.addFade` (kinds = the Effects bin's seven).
- Designed effects: `design_effect {note,id?}` → the FX designer (its own review follows).
- Export: `export {preset,range:'all'|'in_out',name?}` → the Export dialog's start, only on accept.

## Box rules this wave
- While `~/roughcut-loop/QUIET` exists, a quiet-box perf A/B is running: run single test files only,
  under `nice -n 19`, one Playwright process at a time, never the full suite or the gate. The lead
  deletes the file when the A/B is done; check it before each test run.
- Ports: lanes use 8820-8849 (pick one from your lane's slot: IMPORT 8820s, AIOPS 8825s, AIPROMPT
  8830s, TEACH 8835s, BINLOOK 8840s). Never 8765/8766 (Karl), 8780/8781 (lead/judge), 8788-8793
  (STICKFIGHT), 8797-8802 (NOITA), 8810-8819 (this wave's A/B lane).
- Scratch: `~/roughcut-loop/lane-<name>/` only (lane hygiene: never another lane's dir).
