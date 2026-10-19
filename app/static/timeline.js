/* Roughcut — the promoted timeline (INTAKE M9, I9.1: the foundation).
 *
 * One module, no framework, no build step, served at /timeline/timeline.js and mounted by
 * app.js once the project has loaded. It replaces the proportional strip under the monitor
 * with a real timeline: a global time scale, a ruler, zoom and scroll, a V1 lane of shot
 * blocks placed by film time, the playhead across the whole cut, selection by id, and an
 * undo / redo stack that serves the whole board. Trims by drag, the magnet, JKL and the
 * other lanes are I9.2–I9.4 and are built ON this module: the block below is their contract.
 *
 * ================================ THE API — window.tl ================================
 *
 *   Vocabulary. A *shot* is one entry of the EDL's `segments` — `{id, clip, in, out, why,
 *   speed?}`, `in`/`out` in the clip's own seconds (INTAKE decision 5: the only place a
 *   range lives). *Film time* is seconds from the top of the cut; a shot's film start is
 *   the sum of the durations before it, and a shot's duration in the film is
 *   `(out − in) / speed` (INTAKE M13: `speed` 0.1–4, absent = 1 — `tl.dur(seg)` is the one
 *   place that arithmetic lives, and every film-time sum here and in the lanes goes
 *   through it; a clip-time comparison — a clamp, a resume check, a boundary — does not).
 *   A shot whose clip is a *generated* one (`gen_<kind>_<key>.mp4` — black, a colour, a
 *   still the server made for an edit) has no transcript: its block wears the clip's
 *   `summary` line and its kind as the name. Ids come from the server (`g` + 10 hex). A shot the board makes
 *   before a save gets a temporary `tmp-N` id; the save strips it, the server mints a real
 *   one, and `tl.afterSave()` re-keys the shot — every map in here follows the re-key, and
 *   `tl.byId(oldTmpId)` keeps resolving. Blocks carry `data-id`, never an index.
 *
 *   tl.mount(selector, hooks)   Build the timeline inside `selector` (`#tl`). Hooks, all
 *                               supplied by app.js: `segs()` → the working array (the module
 *                               mutates it IN PLACE, never replaces it); `setSegs(arr)` for
 *                               undo/redo restores; `clips()` → the project's clips (poster,
 *                               duration, transcript); `sel()` / `setSel(i)` → app.js's
 *                               index selection (mapped id ↔ index at this boundary);
 *                               `live()` → the shot index the monitor is on; `cue(i, clipT)`
 *                               → park the monitor on shot i at clip time clipT, paused;
 *                               `play(i)` → play the cut from shot i; `touch()` → the
 *                               board's autosave; `render()` → repaint the whole board;
 *                               `toast(msg)`; optional `extra()` / `setExtra(v)` → more
 *                               state that rides each undo entry (the colour block).
 *   tl.state                    `{segs, sel, anchor, zoom, scrollX, playhead}` — `segs` is
 *                               the live array (a getter over hooks.segs()); `sel` is a
 *                               Set<id>, REPLACED on every change (read it, do not hold it);
 *                               `anchor` is the id ⇧-ranges extend from and the shot app.js's
 *                               index `sel` points at, or null when nothing is selected;
 *                               `zoom` is px per second of film; `scrollX` mirrors the
 *                               viewport's scrollLeft; `playhead` is a film time.
 *   tl.el                       `{root, view, canvas, ruler, lanes: {V1, V2, A1, A2}, tracks,
 *                               heads, head}` — the DOM
 *                               the lanes hang their own elements on. `canvas` is the
 *                               scrolled surface; `view` scrolls it.
 *
 *   — reading —
 *   tl.byId(id)                 The shot object, or null. Resolves a re-keyed tmp id too.
 *   tl.indexOf(id)              Its index in the cut, or -1.
 *   tl.idAt(i)                  The id at an index, or null.
 *   tl.dur(seg)                 The shot's length in the film: `(out − in) / (speed || 1)`.
 *                               Pure — works on a proposal's segment as well as the cut's.
 *   tl.speedOf(seg)             Its rate, 1 when absent or out of 0.1–4.
 *   tl.filmStart(id)            Film time the shot starts at, or -1.
 *   tl.total()                  The film's length in seconds.
 *   tl.shotAt(filmTime)         `{id, index, clipT}` under a film time (null on an empty cut);
 *                               `clipT` is `in + (filmTime − start) × speed`.
 *   tl.timeToX(t) / tl.xToTime(x)   Film time ↔ CANVAS x in px (scroll included — for a
 *                               pointer event, x = e.clientX − view.left + view.scrollLeft;
 *                               `tl.eventTime(e)` does exactly that).
 *   tl.snapsFor(clip)           Promise of GET /api/snaps/{clip} — sentences (raw and padded
 *                               cut points), word starts, onsets — fetched once per clip and
 *                               cached; a failed fetch is not cached.
 *
 *   — one undo entry, however many mutations —
 *   tl.begin(label)             Snapshot the cut. A second begin() before commit() is a
 *                               no-op, so a drag can begin once and mutate freely.
 *   tl.commit()                 Close the entry (nothing is pushed when nothing changed),
 *                               drop the redo stack, touch the autosave, emit `change`.
 *   tl.cancel()                 Put the cut back the way begin() found it.
 *   tl.batch(label, fn, {record})   Run fn() as ONE entry, re-entrantly (wave 12, AIGLUE):
 *                               every begin / commit / cancel the code it calls makes nests
 *                               inside it — a begin opens a level, its commit folds the
 *                               level into the entry, its cancel puts back what that level
 *                               changed — and the entry closes when fn returns (a commit,
 *                               or with `record: false` none: no entry, nothing touched for
 *                               the autosave or the tracks' save, no `commit` change — for a
 *                               preview that puts the cut back itself).
 *                               fn throwing puts the cut back as the batch found it and
 *                               rethrows. A batch inside a batch is a level of it. Returns
 *                               {ok, value, committed}; {ok: false} (fn not run) while a
 *                               gesture outside any batch holds an entry open. The batch's
 *                               own level is the batch's: a commit or cancel with no level
 *                               of its own open inside fn is a no-op (false).
 *   tl.undo() / tl.redo()       ⌘Z / ⌘⇧Z (ctrl on Windows), `u`, the header's #undo /
 *                               #redo — whose tooltips carry the label (`undo: trim`).
 *   tl.clearHistory()           Forget every undo and redo entry. For a cut the server
 *                               changed (an effect's edit accepted, read back in place):
 *                               an entry from before it would put the old cut back over it.
 *   Every mutation below is undoable on its own when called outside begin/commit (it wraps
 *   itself with its own name as the label), goes through hooks.touch() so autosave and
 *   roughcutFlush behave exactly as before, repaints the board and emits `change`.
 *
 *   — editing —
 *   tl.setRange(id, in, out)    New clip-second range; either may be null to keep. Clamped
 *                               the way nudge() clamps (≥ 0, ≥ 0.2 s long, ≤ the clip's
 *                               duration), rounded to 0.01. The block's poster follows an
 *                               in-point change once the edits settle (450 ms), never per
 *                               step. Returns true when something changed.
 *   tl.move(ids, beforeId|null) Reorder: the shots, in their current order, land before
 *                               `beforeId` — or at the end for null. Ids travel with shots.
 *   tl.split(id, atFilmTime)    Two shots at a film time inside the shot (≥ 0.2 s from each
 *                               edge): the first keeps the id, the second gets a `tmp-` id.
 *                               Returns the new id, or null when the cut point is too close
 *                               to an edge.
 *   tl.remove(ids)              Remove; the selection moves to the shot that takes the place.
 *   tl.insert(seg, afterId|null)   Insert `{clip, in, out, why}` after a shot (null =
 *                               append); a missing id becomes a `tmp-` one. Selects it.
 *                               Returns its id.
 *   tl.insertAt(seg, filmT)     Premiere's insert (`,`): the shot under filmT splits (or the
 *                               nearer cut is taken when a half would be < 0.2 s), `seg`
 *                               goes in there, the rest moves later. One `insert` entry;
 *                               selects it; returns its id.
 *   tl.overwrite(seg, filmT)    Premiere's overwrite (`.`): `seg` covers the film from
 *                               filmT for its own length — shots inside it removed, the two
 *                               at its edges trimmed; the length changes only past the end.
 *                               One `overwrite` entry; selects it; returns its id.
 *   tl.setSpeed(id, rate)       The shot's `speed` (0.1–4, rounded to 0.01): one undo
 *                               entry labelled `speed`; the block, the total and the
 *                               monitor's rate follow. 1 deletes the key from the
 *                               segment, so a 1× shot saves the way it always did.
 *                               Returns true when something changed.
 *   Keyframes (`controls.keys`, film seconds from the shot's start) stay on the media
 *   through all of these (wave 7, KEYANCHOR; `keysWindow`): a split cuts them with a key
 *   at the cut on both halves; setRange (trim, roll, slip, nudge) shifts them by the in's
 *   film move, dropping keys past an edge for one there that holds their value — within
 *   one begin … commit gesture from the gesture's start, so a drag back restores them;
 *   setSpeed scales their times by old / new rate. Undo restores them with the cut.
 *
 *   — selection, playhead, view —
 *   tl.select(ids, {add, range, source})   Replace the selection; `add` toggles the ids in
 *                               it (⌘-click); `range` extends from the anchor to the last id
 *                               (⇧-click). `[]` clears. Sets app.js's index to the anchor.
 *                               Emits `select` when it changed.
 *   tl.syncSel()                Adopt app.js's index selection when app.js moved it (j/k,
 *                               a card click, playback). app.js's paint() calls this.
 *   tl.seek(filmTime)           Move the playhead AND cue the monitor there, paused.
 *   tl.setPlayhead(filmTime)    Move the playhead only (the monitor drives this on every
 *                               frame through paintPos); keeps it in view.
 *   tl.zoomTo(pxPerSec, {at, viewX})   Zoom; keeps the selection (else the playhead) in
 *                               view, or the film time `at` under viewport x `viewX`.
 *   tl.fit()                    Fit the whole cut in the viewport, and keep fitting as the
 *                               cut changes until the next zoomTo.
 *   tl.render()                 Repaint from the working array (keyed by id — blocks are
 *                               updated in place, not rebuilt). app.js calls it where the
 *                               strip used to be painted; a lane rarely needs to.
 *
 *   — Premiere's frame: timecode, tools, markers, marks (REDESIGN-GOAL dimension 5) —
 *   tl.fps() / tl.setFps(r)     The sequence rate: setFps, else the project's `fps`, else
 *                               23.976. tl.frame() is one frame in seconds.
 *   tl.gridRate() / tl.onGrid(t) The edit grid (wave 3): the exact rate (29.97 →
 *   tl.isOnGrid(t)               30000/1001) and a time on its nearest frame. setRange,
 *                               split, insert, overwrite, setSpeed, the A1 offset and V2
 *                               commit on it, so every cut in the film is on a frame.
 *   A1 waveforms                /api/peaks/<stem>?pps= per clip, fetched once for a block
 *                               on screen; `.ablk[data-peaks]` is ok | none | loading.
 *   tl.tc(t)                    Film seconds → HH:MM:SS:FF, non-drop at the nominal rate,
 *                               rounded to the nearest frame. The ruler, the end mark and
 *                               the side column's playhead readout (`.tl-tc`) use it.
 *   tl.keyLabel(s)              ⌘ ⌥ ⇧ as Ctrl+ Alt+ Shift+ off a Mac (tl.isMac).
 *   tl.setTool(name) / tl.tool  'select' | 'razor' | 'ripple' | 'roll' | 'slip' | 'track'
 *                               (tl.TOOLS: key, glyph, words). Razor: a click on a block
 *                               splits it there. Track: a press selects that shot and every
 *                               one after. Ripple / roll / slip are read by the trim lane.
 *                               Emits `tool`. The side column's button shows it.
 *   tl.addMarker(t) removeMarker(t) clearMarkers() setMarkers(list) markers()
 *   tl.nextMarker(t) prevMarker(t)  Sequence markers, film times on the frame grid, drawn
 *                               on the ruler (a click goes there). setSeqIn(t) /
 *                               setSeqOut(t) (null clears) are the sequence's in / out,
 *                               shaded on the ruler. Both persist in localStorage per
 *                               project and cut, or come from `P.markers` / `P.in` /
 *                               `P.out` once the server sends them. Emit `markers`.
 *                               Each is one undo entry outside begin/commit (`add marker`,
 *                               `mark in`, `clear out` …); undo and redo put them back.
 *
 *   — the tracks: V2 V1 A1 A2 (verdict 2 punch 3; the model is roughcut/tracks.py) —
 *   The cut is still one magnetic list, and each shot is two clips: its picture on V1 and,
 *   linked, its clip's own sound on A1 (`.ablk` in `tl.el.tracks.A1`, keyed by the shot's
 *   id) — so a trim, a razor, a ripple delete or a move acts on both. A shot may carry
 *   `link: false` (unlinked) and `a_off` (film seconds the sound sits after its picture).
 *   A2 is the music bed (drawn by timeline-lanes.js). V2 holds `overlays`, clips laid over
 *   the cut at a film time `at` (picture only; the render composites them over V1).
 *   tl.tracks() / tl.setTrack(name, key, v)   The headers: V1/V2 {lock, eye, target},
 *                               A1/A2 {lock, mute, solo, target, gain_db}. Not undoable (as
 *                               in Premiere); saved with V2 and the markers. Emits `tracks`.
 *   tl.audible(name)            Not muted and, when any track is soloed, soloed.
 *   tl.isLinked(seg) tl.aOff(seg) tl.setLink(id, bool) tl.toggleLink(id)   ⌥-click on
 *                               either half toggles (the trim lane and the lanes call it).
 *   tl.setAudioOffset(id, s)    An unlinked shot's sound against its picture, on frames;
 *                               one `move audio` entry.
 *   tl.overlays() overlayById(id) addOverlay(seg, at) moveOverlay(id, at) removeOverlay(id)
 *   tl.trimOverlay(id, 'in'|'out', filmT)
 *   tl.selectOverlay(id|null)   V2, V3…, A3…. Each edit is one undo entry (they ride the
 *                               snapshot). tl.state.osel is the clip selected. A clip on
 *                               V3… / A3… drags (snapping) and its edges trim here; V2's
 *                               drag is timeline-lanes.js's.
 *   tl.addTrack(kind) tl.deleteTrack(kind|name)   Sequence › Add / Delete Track: one undo
 *                               entry each (the added tracks' names ride the snapshot);
 *                               a deleted track's clips go in the same entry.
 *   tl.layoutTracks()           Size every row from the view's height: V2, V1, the lanes'
 *                               ghost (`tl.el.lanes.ghost`), A1, A2, the bin outlines.
 *   tl.saveSeq()                PUT the headers, V2, markers and In / Out now (debounced on
 *                               every change; a body with no `segments` leaves the cut alone).
 *   A locked V1 or A1 refuses every edit of the cut (a toast says so); a locked V2 refuses
 *   V2's. The Program monitor's videos carry A1's level (0 muted) and the bed follows A2.
 *
 *   — transitions (verdict 6 punch 6; the Effects bin: verdict 8 punch 5; roughcut/tracks.py) —
 *   tl.transitions()            `[{a, b, kind, dur, align}]`: centred on an edit ('center',
 *                               its two shots), or one-sided at a shot's 'start' (b only)
 *                               or 'end' (a only). Video kinds (tl.VIDEO_KINDS: Cross
 *                               Dissolve, Dip to Black / White, Film Dissolve) draw on V1,
 *                               audio kinds (tl.AUDIO_KINDS: Constant Power, Constant Gain,
 *                               Exponential Fade) on A1; film seconds on the grid.
 *   tl.applyTransition('video'|'audio'|'both')   ⌘D / ⇧D / ⌘⇧D: the default kinds
 *                               (tl.transitionDefaults, tl.setDefaultTransition(kind); a
 *                               box's right-click "Set Default Transition") at
 *                               tl.editForTransition() — the selected transition's edit,
 *                               else the selected edit point (a click on its column,
 *                               tl.selectEdit(k)), else the edit nearest the playhead. One
 *                               undo entry; a shot short of handle repeats frames (hatched,
 *                               "insufficient media — repeated frames").
 *   tl.addTransition(k, kind, dur?) tl.addFade(i, edge, kind, dur?) removeTransition(key)
 *   setTransitionDur(key, s) tl.dropTransition(kind, tl.transTarget(t))   a drop from the
 *                               Effects bin (MIME tl.TRANS_MIME): centred near an edit, else
 *                               one-sided at the shot's nearer end; one undo entry.
 *   tl.selectTransition(key) editTransitionDur(key) tl.tsel   key = `a|b|kind`. The box's
 *                               ends drag its length (centred: both move), a double-click
 *                               types it (`1.5`, `12f`), Del removes the selected one.
 *   tl.transitionHalves()       per edit k `{video, audio, vkind, akind, short, rep, at}`:
 *                               the half-lengths the Program plays, at film second `at`.
 *   tl.transitionFades()        the one-sided ones `[{k, edge, fam, kind, dur, t0, t1}]`.
 *   tl.transitionsForSave()     for app.js's save body, under the ids the server knows.
 *
 *   — saving —
 *   tl.forSave()            The segments to PUT: known tmp ids mapped to their server
 *                               ids, unknown ones sent as `tmp-N` (the server mints and
 *                               names them in the reply's `ids`).
 *   tl.needsRekey()             True while a shot has no server id yet.
 *   tl.afterSave(segments)      The segments from GET /api/project after a save: re-key by
 *                               position where clip/in/out agree.
 *   tl.applyIds({tmp: id})      The save reply's `ids`: re-key exactly (wave 5, SAVE).
 *   tl.idFor(id)                The id the server knows a shot by (a re-keyed tmp's real).
 *   tl.putTurn(fn)              Run fn when no other PUT /api/project is in flight.
 *   tl.saveHeaders(extra)       The headers of a PUT /api/project: JSON, and the page's
 *                               `x-roughcut-save: <page>:<n>` tag, n counting up per body
 *                               built (wave 8, SAVESAFE: the server never applies an older
 *                               body of a page over a newer one).
 *   tl.seqForFlush()            The tracks' save body as an object (null before mount),
 *                               for the keepalive flush on unload; tl.seqDirty() says
 *                               whether it differs from what was last saved.
 *
 *   — events —
 *   tl.on(event, fn) → off()    `change` {label, kind, ids} · `select` {ids, anchor,
 *                               source: 'click'|'api'|'app'|'land'|'heir'} · `playhead`
 *                               {t} · `zoom` {zoom}. 'heir' is the shot handed the
 *                               selection when the selected one left the cut.
 *
 *   — pointer contract for the lanes —
 *   The module handles pointerdown/up on the V1 lane and the ruler. A pointerdown that
 *   moves more than 4 px before pointerup, or whose propagation a lane's own handler stops
 *   (a trim handle, a drag), never becomes a click here: a plain click on a block selects
 *   it and parks the monitor on its first frame, paused (INTAKE M16 decision 6; a double-
 *   click plays the cut from it, as space does); ⇧-click ranges; ⌘/ctrl-
 *   click toggles; a click on the empty lane or the ruler clears the selection and seeks;
 *   dragging on the ruler scrubs. Keys (outside inputs): `+`/`=` and `-` zoom ×2 / ÷2,
 *   `\` fits, ⌘/ctrl or alt + wheel zooms around the cursor (Premiere), a plain or ⇧
 *   wheel scrolls the timeline along, ⌘Z / ⌘⇧Z undo / redo.
 * =====================================================================================
 */
(function () {
  'use strict';

  const PAD = 12;                  // px of canvas before 0:00
  const PAD_R = 72;                // px after the end: room for the total's timecode
  const PAST_END_S = 3600;         // s the playhead may sit past the film's end (setPlayhead)
  const MIN_LEN = 0.2;             // s — the shortest shot, as nudge() clamps
  const MIN_SPEED = 0.1, MAX_SPEED = 4;    // edits.py's bounds: outside them a speed reads as 1
  const MIN_ZOOM = 0.5, MAX_ZOOM = 4000;   // px per second
  /* Labelled ruler steps in FRAMES at the nominal rate (24 for 23.976): 1, 2, 5, 10
   * frames, then 1 s … 1 h of timecode. A label is HH:MM:SS:FF, so it needs LABEL_PX. */
  const STEP_FRAMES = [1, 2, 5, 10];
  const STEP_SECONDS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600];
  const LABEL_PX = 84;
  const DEFAULT_FPS = 23.976;      // the sequence rate when the project does not say
  const UNDO_LIMIT = 100;
  const POSTER_SETTLE_MS = 450;    // the same debounce app.js's cards use
  const TOOLS = {                  // Premiere's tools: name -> [key, glyph, what]
    select: ['V', '↖', 'Selection tool'],
    razor: ['C', '✂', 'Razor tool — a click on a shot cuts it there'],
    ripple: ['B', '⇥', 'Ripple edit tool — an edge drag trims, the rest moves'],
    roll: ['N', '⇹', 'Rolling edit tool — an edge drag rolls the cut'],
    slip: ['Y', '↔', 'Slip tool — drag a shot to slip its content'],
    track: ['A', '⇉', 'Track select forward — a click takes that shot and every one after'],
  };
  /* The tracks (roughcut/tracks.py): V2 over V1, then A1 (each shot's own sound, linked)
   * and A2 (the music bed). Rows share the panel's height by weight, never under their
   * minimum; the ruler is RULER_H; a thin gap between rows, a wider one between the video
   * and the audio tracks, as Premiere draws them. */
  const TRACKS = ['V2', 'V1', 'A1', 'A2'];
  const TRACK_W = { V2: 0.8, V1: 1.5, A1: 1.15, A2: 0.75 };
  const TRACK_MIN = { V2: 24, V1: 40, A1: 28, A2: 22 };
  /* Sequence › Add Track (verdict 4, punch 9): V3…V9 stack above V2, A3…A9 under A2. An
   * added video track holds clips laid over the cut (overlays with `track`); an added
   * audio track holds clips' sound (`state.audio`). The render composites the video
   * tracks bottom to top and mixes the audio ones (roughcut/tracks.py). */
  const EXTRA_V = ['V3', 'V4', 'V5', 'V6', 'V7', 'V8', 'V9'];
  const EXTRA_A = ['A3', 'A4', 'A5', 'A6', 'A7', 'A8', 'A9'];
  const isExtra = (n) => EXTRA_V.includes(n) || EXTRA_A.includes(n);
  const extraDefault = (n) => (n[0] === 'V' ? { lock: false, eye: true, target: false }
    : { lock: false, mute: false, solo: false, target: false, gain_db: 0 });
  const trackW = (n) => TRACK_W[n] ?? (n[0] === 'V' ? 0.7 : 0.7);
  const trackMin = (n) => TRACK_MIN[n] ?? 22;
  /* Every track, top to bottom: V9…V3, V2, V1, A1, A2, A3…A9. */
  function trackNames() {
    const have = Object.keys(state.tracks);
    return [...EXTRA_V.filter((n) => have.includes(n)).reverse(), ...TRACKS,
            ...EXTRA_A.filter((n) => have.includes(n))];
  }
  const RULER_H = 24, ROW_GAP = 2, AV_GAP = 6;
  const DEFAULT_TRACKS = () => ({
    V1: { lock: false, eye: true, target: true },
    V2: { lock: false, eye: true, target: false },
    A1: { lock: false, mute: false, solo: false, target: true, gain_db: 0 },
    A2: { lock: false, mute: false, solo: false, target: false, gain_db: 0 },
  });
  const SEQ_SAVE_MS = 400;
  const IS_MAC = /Mac|iPhone|iPad/i.test(
    (typeof navigator !== 'undefined'
      && ((navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform)) || '');

  let hooks = null;
  const el = { root: null, view: null, canvas: null, ruler: null, lanes: {}, head: null,
               end: null, total: null, tracks: {}, heads: null, divider: null };
  const blocks = new Map();        // id -> block element
  const posterAt = new Map();      // id -> the in-point the block's poster shows
  const tmpMap = new Map();        // tmp id -> server id, learnt after a save
  const snapsCache = new Map();    // clip -> Promise<payload>
  const listeners = new Map();     // event -> Set<fn>
  const undoStack = [], redoStack = [];
  let pending = null;              // { label, before } between begin() and commit()
  let tmpN = 0;
  let posterTimer = 0;
  let autoFit = true;              // fit on every render until someone zooms
  let lastAppSel = -1;             // app.js's index the last time the two agreed
  let inCue = false;               // hooks.cue is running: app.js's sel moves are ours
  let scrubbing = false;
  let scrubRaf = 0;
  let down = null;                 // the lane's pointerdown, until pointerup

  const state = {
    sel: new Set(), anchor: null, zoom: 40, scrollX: 0, playhead: 0,
    tool: 'select', markers: [], seqIn: null, seqOut: null,
    tracks: DEFAULT_TRACKS(), overlays: [], osel: null,
    audio: [],                     // the added audio tracks' clips (A3…)
    transitions: [],               // {a, b, kind, dur, align} at the edits (punch 6)
    tsel: null,                    // the selected transition's key
    edit: null,                    // the selected edit point {a, b} (a click on its column)
  };
  const ablocks = new Map();       // id -> A1 block element (the shot's linked sound)
  const oblocks = new Map();       // overlay id -> V2 block element
  let seqTimer = 0;                // the tracks / V2 / markers save, debounced
  let seqSaved = null;             // the body last saved (or loaded): unchanged saves nothing
  let fpsOverride = null;          // tl.setFps()
  Object.defineProperty(state, 'segs', {
    enumerable: true, get: () => (hooks ? hooks.segs() : []),
  });

  /* ------------------------------------------------------------ small helpers */
  const segs = () => (hooks ? hooks.segs() : []);
  const clipOf = (name) => ((hooks && hooks.clips && hooks.clips()) || {})[name] || null;
  const isTmp = (id) => typeof id === 'string' && id.startsWith('tmp-');
  const round2 = (x) => Math.round(x * 100) / 100;
  const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
  const stem = (clip) => String(clip).replace(/\.[^.]+$/, '');
  const isGen = (clip) => String(clip).startsWith('gen_');
  /* A gap on V1 (verdict 4, punch 9): empty film time, carried by a generated black clip
   * the server makes (POST /api/gap) so the monitor and the render play black and
   * silence. Drawn as nothing; selectable; a ripple delete closes it (Premiere). */
  const isGap = (seg) => !!(seg && seg.gap === true);

  /* The one arithmetic (INTAKE M13, edits.py's `speed_of` / `dur`): a shot's rate, 1
   * when absent or nonsense, and its length in the film at that rate. */
  function speedOf(seg) {
    const s = Number(seg && seg.speed);
    return Number.isFinite(s) && s >= MIN_SPEED && s <= MAX_SPEED ? s : 1;
  }
  const dur = (seg) => (seg.out - seg.in) / speedOf(seg);

  function fmt(t) {
    const m = Math.floor(t / 60), s = t - m * 60;
    return `${m}:${s.toFixed(1).padStart(4, '0')}`;
  }

  /* ---- the sequence rate and timecode (REDESIGN-GOAL dimension 5) */
  /* The sequence's frame rate: tl.setFps(), else the project's `fps` (app.js's `P`,
   * read by name — the EDL does not carry one yet), else 23.976. */
  function fps() {
    if (fpsOverride) return fpsOverride;
    const h = hooks && hooks.fps ? Number(hooks.fps()) : 0;
    if (h > 0) return h;
    try {
      // eslint-disable-next-line no-undef
      const p = typeof P !== 'undefined' && P ? Number(P.fps || (P.edl && P.edl.fps)) : 0;
      if (p > 0 && p < 1000) return p;
    } catch (err) { /* P not initialised yet */ }
    return DEFAULT_FPS;
  }

  function setFps(rate) {
    const r = Number(rate);
    fpsOverride = Number.isFinite(r) && r > 0 && r < 1000 ? r : null;
    render();
    paintTc();
    return fps();
  }

  const frame = () => 1 / fps();

  /* Film seconds → HH:MM:SS:FF, non-drop-frame at the nominal rate (24 for 23.976, as
   * Premiere counts it). A time rounds to the nearest frame boundary: shots end on cut
   * points, and a 4.00 s film is 00:00:04:00, not 00:00:03:23. */
  function tc(t, rate) {
    const r = rate || fps();
    const nominal = Math.max(1, Math.round(r));
    let n = Math.round(Math.max(0, Number(t) || 0) * r);
    const ff = n % nominal; n = (n - ff) / nominal;
    const ss = n % 60; n = (n - ss) / 60;
    const mm = n % 60;
    const hh = (n - mm) / 60;
    return [hh, mm, ss, ff].map((x) => String(x).padStart(2, '0')).join(':');
  }

  /* A frame-grid time: markers and in / out sit on frames, as in Premiere — kept at
   * the board's 0.01 s (the monitor parks at 0.01 s, so a jump lands exactly on it). */
  const onFrame = (t) => round2(Math.round(Math.max(0, t) * fps()) / fps());

  /* The edit grid (wave 3, the journey's VFR export): every trim, roll, slip, razor cut,
   * insert, overwrite and move commits its points on the sequence's frames — exactly,
   * not to the 0.01 s the board used to keep (a 29.97 frame is 0.0334 s, so 0.01 s put
   * cuts between frames and the export came out variable-rate). The project says 29.97;
   * the grid is 30000/1001. A shot's in is a whole number of frames into its clip and
   * its length in the film a whole number of frames at its speed, so every cut in the
   * film — a sum of those lengths — is on a frame too. 4 places, what the server keeps. */
  function gridRate() {
    const r = fps();
    const n = Math.round(r * 1.001);
    if (Math.abs(r - Math.round(r)) > 0.004 && Math.abs(r - n / 1.001) < 0.004) return n / 1.001;
    return r;
  }
  const r4 = (x) => Math.round(x * 1e4) / 1e4;
  const onGrid = (t) => r4(Math.round(Math.max(0, Number(t) || 0) * gridRate()) / gridRate());
  /* A clip range on the grid. `keep` 'in' / 'out': that edge stays exactly where it is
   * (a trim of the other one); null: neither is kept (an insert, a slip). At 1× the edges
   * that move go to their nearest frame — a clip frame is a film frame — so a shot whose
   * edges are on the grid (every edit's, and the proposals' once they are rounded to
   * frames) keeps every cut on a frame; a kept edge from before the grid (0.01 s) is left
   * alone rather than moved behind the editor's back. Retimed, the moved edge makes the
   * shot's length in the film whole frames at its rate. Never shorter than MIN_LEN. */
  function gridRange(a, b, spd, keep, maxOut) {
    const r = gridRate(), s = spd || 1;
    const hi = Number.isFinite(maxOut) ? maxOut : 1e9;
    const top = (x) => (x > hi + 1e-9 ? r4(Math.floor(hi * r + 1e-6) / r) : x);
    if (s === 1) {
      let a2 = keep === 'in' ? a : onGrid(a);
      let b2 = keep === 'out' ? b : top(onGrid(b));
      if (b2 - a2 < MIN_LEN - 1e-9) {
        if (keep === 'out') a2 = r4(Math.floor((b2 - MIN_LEN) * r + 1e-6) / r);
        else b2 = top(r4(Math.ceil((a2 + MIN_LEN) * r - 1e-6) / r));
      }
      return [Math.max(0, a2), b2];
    }
    const nMin = Math.max(1, Math.ceil((MIN_LEN / s) * r - 1e-6));
    if (keep === 'out') {
      let n = Math.max(nMin, Math.round(((b - a) / s) * r));
      while (n > nMin && b - (n / r) * s < -1e-9) n -= 1;
      return [r4(Math.max(0, b - (n / r) * s)), b];
    }
    const a2 = keep === 'in' ? a : onGrid(a);
    let n = Math.max(nMin, Math.round(((b - a2) / s) * r));
    while (n > nMin && a2 + (n / r) * s > hi + 1e-9) n -= 1;
    return [a2, r4(a2 + (n / r) * s)];
  }
  /* Whether a time is on the grid, to what 4 places can say. */
  const isOnGrid = (t) => Math.abs(Number(t) - onGrid(t)) < 1e-4 + 1e-9;

  /* Keyframes stay on the MEDIA, as in Premiere (wave 7, lane KEYANCHOR). A shot's
   * `controls.keys` = {prop: [[t, v], ...]} count film seconds from its start (the wave-6
   * schema, effects.js), so every edit that moves a shot's in, cuts it or retimes it moves
   * them: `keysWindow(keys, a, b, scale)` is the keys of a shot that is the old shot's film
   * window [a, b] (times first scaled by `scale`, a speed change's old / new), counted from
   * a. A key inside stays at t − a; when keys fall outside a side, they go and a key on
   * that edge holds the value the old keys had there, so the picture and the sound never
   * jump (a razor's first half ends on the cut's value, its second half starts on it).
   * `b` Infinity: nothing is cut off the end. edits.py's `anchor_keys` is the twin. */
  const KEY_EPS = 1e-4;
  const kr6 = (x) => Math.round(x * 1e6) / 1e6;
  const kval = (prop, v) => (String(prop).startsWith('position')
    ? Math.round(v * 1e6) / 1e6 : Math.round(v * 1000) / 1000);
  function keyAt(list, t) {
    const n = list.length;
    if (t <= list[0][0]) return list[0][1];
    if (t >= list[n - 1][0]) return list[n - 1][1];
    for (let i = 1; i < n; i++) {
      if (t <= list[i][0]) {
        const [t0, v0] = list[i - 1], [t1, v1] = list[i];
        return t1 > t0 ? v0 + (v1 - v0) * (t - t0) / (t1 - t0) : v1;
      }
    }
    return list[n - 1][1];
  }
  function keysWindow(keys, a, b, scale = 1) {
    if (!keys || typeof keys !== 'object') return null;
    const hasEnd = Number.isFinite(b);
    const len = hasEnd ? kr6(b - a) : Infinity;
    const out = {};
    for (const [prop, raw] of Object.entries(keys)) {
      if (!Array.isArray(raw) || !raw.length) continue;
      const L = raw.map((k) => [Number(k[0]) * scale, Number(k[1])]);
      const res = [];
      if (L.some((k) => k[0] < a - KEY_EPS)) res.push([0, kval(prop, keyAt(L, a))]);
      for (const [t, v] of L) {
        if (t < a - KEY_EPS || (hasEnd && t > b + KEY_EPS)) continue;
        let nt = Math.abs(t - a) <= KEY_EPS ? 0 : kr6(t - a);
        if (hasEnd && Math.abs(t - b) <= KEY_EPS) nt = len;
        if (res.length && nt <= res[res.length - 1][0] + 1e-9) continue;   // the edge's key is there
        res.push([nt, v]);
      }
      if (hasEnd && L.some((k) => k[0] > b + KEY_EPS)
          && !(res.length && res[res.length - 1][0] >= len - 1e-9)) {
        res.push([len, kval(prop, keyAt(L, b))]);
      }
      if (res.length) out[prop] = res;
    }
    return Object.keys(out).length ? out : null;
  }
  const keysOfSeg = (seg) => {
    const k = seg && seg.controls && seg.controls.keys;
    return k && typeof k === 'object' && Object.keys(k).length ? k : null;
  };
  /* The shot's keys replaced (a new `controls` object, never one another shot shares). */
  function putKeys(seg, keys) {
    if (!seg.controls && !keys) return;
    const c = { ...(seg.controls || {}) };
    if (keys) c.keys = keys; else delete c.keys;
    if (Object.keys(c).length) seg.controls = c; else delete seg.controls;
  }
  /* A shot whose clip range goes from [in0, …] at `spd` to [a, b]: its keys follow the
   * media (an in moved Δ clip seconds later is Δ / spd film seconds, every key Δ / spd
   * earlier). `base` is {in, spd, keys} — the shot as the gesture found it, so a drag
   * that trims past a key and comes back within one gesture brings the key back. */
  function anchorRange(seg, base, a, b) {
    if (!base.keys) return;
    const s = base.spd || 1;
    putKeys(seg, keysWindow(base.keys, (a - base.in) / s, (b - base.in) / s));
  }
  /* The shot as the current gesture (begin … commit) found it: a trim / roll / slip drag
   * calls setRange once a frame, each from the drag's start, and so do the keys. */
  let keyBase = null;
  function baseOf(seg) {
    const now = () => ({ in: seg.in, spd: speedOf(seg),
                         keys: keysOfSeg(seg) ? JSON.parse(JSON.stringify(seg.controls.keys)) : null });
    if (!pending) return now();
    if (!keyBase || keyBase.p !== pending) keyBase = { p: pending, m: new Map() };
    if (!keyBase.m.has(seg.id)) keyBase.m.set(seg.id, now());
    return keyBase.m.get(seg.id);
  }
  /* The second half of a razor at clip time clipT (the first is `seg`; the caller moves
   * its out there): a new shot whose controls are its own copy, and both halves' keys cut
   * at the razor — the first keeps those before it and ends on the cut's value, the
   * second starts on it and has the rest, d earlier. */
  function razorHalf(seg, clipT) {
    const second = { ...seg, id: `tmp-${++tmpN}`, in: clipT };
    if (seg.controls) second.controls = JSON.parse(JSON.stringify(seg.controls));
    const k = keysOfSeg(seg);
    if (k) {
      const d = (clipT - seg.in) / speedOf(seg);
      putKeys(second, keysWindow(k, d, Infinity));
      putKeys(seg, keysWindow(k, 0, d));
    }
    return second;
  }
  /* A shot's edge moved by an edit that is not a gesture (an overwrite's trims). */
  function anchorNow(seg, a, b) { anchorRange(seg, baseOf(seg), a, b); }

  /* The key names the platform uses: ⌘ ⌥ ⇧ on a Mac, Ctrl+ Alt+ Shift+ elsewhere. */
  function keyLabel(s) {
    if (IS_MAC) return String(s);
    return String(s)
      .replace(/⌘(?=\s|$)/g, 'Ctrl').replace(/⌥(?=\s|$)/g, 'Alt').replace(/⇧(?=\s|$)/g, 'Shift')
      .replace(/⌘/g, 'Ctrl+').replace(/⌥/g, 'Alt+').replace(/⇧/g, 'Shift+');
  }

  function hueOf(clip) {
    let h = 0;
    for (const c of String(clip)) h = (h * 31 + c.charCodeAt(0)) % 360;
    return h;
  }

  function emit(event, detail) {
    const set = listeners.get(event);
    if (!set) return;
    for (const fn of [...set]) {
      try { fn(detail); } catch (err) { console.error(`tl.on(${event})`, err); }
    }
  }

  function on(event, fn) {
    if (!listeners.has(event)) listeners.set(event, new Set());
    listeners.get(event).add(fn);
    return () => listeners.get(event).delete(fn);
  }

  function toast(msg) { if (hooks && hooks.toast) hooks.toast(msg); }

  /* Every shot has an id the moment the module sees it: the server's, or a temporary one
   * that the next save replaces (forSave / afterSave). */
  function ensureIds() {
    for (const s of segs()) if (!s.id) s.id = `tmp-${++tmpN}`;
  }

  function resolveId(id) {
    if (id == null) return null;
    const list = segs();
    if (list.some((s) => s.id === id)) return id;
    const real = tmpMap.get(id);
    return real != null && list.some((s) => s.id === real) ? real : id;
  }

  function indexOf(id) {
    const real = resolveId(id);
    return segs().findIndex((s) => s.id === real);
  }

  function byId(id) {
    const i = indexOf(id);
    return i >= 0 ? segs()[i] : null;
  }

  function idAt(i) {
    const s = segs()[i];
    return s ? s.id : null;
  }

  function filmStart(id) {
    const i = indexOf(id);
    if (i < 0) return -1;
    let t = 0;
    const list = segs();
    for (let k = 0; k < i; k++) t += dur(list[k]);
    return t;
  }

  function total() { return segs().reduce((a, s) => a + dur(s), 0); }

  function shotAt(t) {
    const list = segs();
    if (!list.length) return null;
    let start = 0;
    for (let i = 0; i < list.length; i++) {
      const s = list[i], d = dur(s);
      if (t < start + d || i === list.length - 1) {
        return { id: s.id, index: i,
                 clipT: round2(clamp(s.in + (t - start) * speedOf(s), s.in, s.out)) };
      }
      start += d;
    }
    return null;
  }

  const timeToX = (t) => PAD + t * state.zoom;
  const xToTime = (x) => Math.max(0, (x - PAD) / state.zoom);

  function eventTime(e) {
    const r = el.view.getBoundingClientRect();
    return xToTime(e.clientX - r.left + el.view.scrollLeft);
  }

  /* ------------------------------------------------------------ the picture */
  /* A labelled step in frames: 1, 2, 5 or 10 frames, else whole seconds of timecode —
   * whichever first gives a label LABEL_PX of room. */
  function stepFrames(zoom) {
    const r = fps(), n = Math.max(1, Math.round(r));
    for (const f of STEP_FRAMES) if (f < n && (f / r) * zoom >= LABEL_PX) return f;
    for (const s of STEP_SECONDS) if ((s * n / r) * zoom >= LABEL_PX) return s * n;
    return STEP_SECONDS[STEP_SECONDS.length - 1] * n;
  }

  /* Labelled ticks in HH:MM:SS:FF at the sequence rate; minor ticks split a step into
   * its frames (under a second) or into fifths, tenths once fifths are 100 px apart. */
  /* The ruler is rebuilt only when what it shows changed (wave 13 CANVAS2): every render
   * rebuilt its ticks — hundreds of new elements, all restyled and laid out — and the
   * monitor renders the timeline on Play and at every cut, the frames a hitch shows on. */
  let rulerKey = '';
  function paintRuler(width) {
    const key = [width, state.zoom, fps(), state.seqIn, state.seqOut, state.markers.join(','),
                 keyLabel('⇧M')].join('|');
    if (key === rulerKey && el.ruler.childElementCount) return;
    rulerKey = key;
    const r = fps(), n = Math.max(1, Math.round(r));
    const sf = stepFrames(state.zoom);
    const step = sf / r;
    const ratio = sf < n ? Math.max(1, sf) : (step * state.zoom >= 500 ? 10 : 5);
    const minor = step / ratio;
    const end = (width - PAD) / state.zoom;
    const frag = document.createDocumentFragment();
    for (let k = 0; k * minor <= end + 1e-9 && k < 5000; k++) {
      const t = k * minor;
      const tick = document.createElement('i');
      const major = k % ratio === 0;
      tick.className = major ? 'tick major' : 'tick';
      tick.style.left = `${timeToX(t)}px`;
      if (major) {
        const lab = document.createElement('label');
        lab.textContent = tc(t, r);
        tick.appendChild(lab);
      }
      frag.appendChild(tick);
    }
    // the sequence's in / out: the ruler shaded between them, a bracket at each
    if (state.seqIn != null || state.seqOut != null) {
      const a = state.seqIn != null ? state.seqIn : 0;
      const b = state.seqOut != null ? state.seqOut : total();
      const io = document.createElement('div');
      io.className = 'tl-inout';
      io.style.left = `${timeToX(a)}px`;
      io.style.width = `${Math.max(0, (b - a) * state.zoom)}px`;
      io.classList.toggle('has-in', state.seqIn != null);
      io.classList.toggle('has-out', state.seqOut != null);
      io.title = `sequence in ${state.seqIn != null ? tc(a, r) : '—'} · out ${state.seqOut != null ? tc(b, r) : '—'}`;
      frag.appendChild(io);
    }
    // markers: a green flag on the ruler at each; a click goes there
    for (const m of state.markers) {
      const mk = document.createElement('i');
      mk.className = 'tl-marker';
      mk.dataset.t = String(m);
      mk.style.left = `${timeToX(m)}px`;
      mk.title = `marker ${tc(m, r)} — click to go there · ${keyLabel('⇧M')} next · ${keyLabel('⌘⇧M')} previous`;
      frag.appendChild(mk);
    }
    el.ruler.replaceChildren(frag);
  }

  function makeBlock(id) {
    const b = document.createElement('div');
    b.className = 'blk';
    b.dataset.id = id;
    b.innerHTML = '<div class="strip"><img class="poster" draggable="false" loading="lazy" decoding="async" alt=""></div>'
      + '<div class="txt"><span class="speed" hidden></span><span class="sync" hidden></span>'
      + '<span class="name"></span></div>'
      + '<i class="warn in" hidden></i><i class="warn out" hidden></i>';
    return b;
  }

  /* A generated clip's kind, from its name: `gen_black_1a2b.mp4` → `black`. */
  function genKind(clip) {
    const m = /^gen_([a-z]+)_/i.exec(String(clip));
    return m ? m[1] : 'generated';
  }

  /* What a generated clip is, in words — the server's `summary` line for it (`black ·
   * 2.0 s`, `colour #1a2b3c`, `still of CLIP_08 at 4:31`), else its kind. A footage
   * clip's `summary` is the sidecar's numbers, never a string, so the type is the test. */
  function genLine(seg, clip) {
    const s = clip && clip.summary;
    if (typeof s === 'string' && s) return s;
    if (s && typeof s.generated === 'string' && s.generated) return s.generated;   // the server's shape
    if (s && typeof s.text === 'string' && s.text) return s.text;
    return genKind(seg.clip);
  }

  /* The line a block wears: the first transcript line inside the cut, else the why —
   * or, for a generated clip, what it is. */
  function strongestLine(seg, clip) {
    if (isGen(seg.clip)) return genLine(seg, clip);
    const u = clip && (clip.transcript || []).find((x) => x.end > seg.in && x.start < seg.out);
    return (u && u.text) || seg.why || '';
  }

  /* A block's name is its clip's, as in Premiere (verdict 2, punch 3 — it was the first
   * words spoken, INTAKE M16, which read as three identical "Oh, that's tight." blocks);
   * a generated slide by what it is (`black`, `title`). What is said in the shot, else
   * what was seen in it, is the hover's first line (`blockSays`). */
  function blockName(seg) {
    if (isGen(seg.clip)) return genKind(seg.clip);
    return stem(seg.clip);
  }

  /* A label that fits its block (verdict 8, punch 6: V1 read "CLI…" at fit zoom, a ghost
   * "0…"): the name when it fits, else the clip's number ("02" for CLIP_02), else nothing
   * — the tooltip always names it. Measured on a canvas in the font the label renders in:
   * the computed style of a real label element (style, weight, size and the board's own
   * family stack, resolved by the browser exactly as the DOM text is), not a guess. */
  let textCtx = null;
  function fontOf(node, style) {          // style: 'normal' / 'italic' in place of the node's
    if (!node) return '';
    const cs = getComputedStyle(node);
    if (!cs.fontFamily) return '';
    return `${style || cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
  }
  function textWidth(s, font) {
    if (!s) return 0;
    if (!textCtx) textCtx = document.createElement('canvas').getContext('2d');
    if (!textCtx || !font) return s.length * 7.5;
    if (textCtx._rcFont !== font) { textCtx.font = font; textCtx._rcFont = font; }
    return textCtx.measureText(s).width;
  }
  function fitLabel(name, room, font) {
    const s = String(name || '');
    const fits = (t) => textWidth(t, font) <= room;
    if (s && fits(s)) return s;
    const num = (/(\d{2,})$/.exec(s) || [])[1] || '';
    return num && fits(num) ? num : '';
  }
  // V1's label band: 5 px of padding a side (timeline.css), a pixel clear of the edge
  const V1_LABEL_PAD = 11;
  let v1Font = '';

  function blockSays(seg, clip) {
    if (isGen(seg.clip)) return genLine(seg, clip);
    const line = strongestLine(seg, clip);
    if (line) return line;
    const seen = ((clip && clip.visual && clip.visual.moments) || [])
      .find((m) => m.end > seg.in && m.start < seg.out && m.what);
    return seen ? seen.what : '';
  }

  /* Mirrors app.js's boundaryWarning, per edge: a cut point inside somebody's sentence. */
  function warnEdges(seg, clip) {
    if (!clip) return { in: false, out: false };
    const cutsInto = (t) => (clip.transcript || []).some(
      (u) => u.start + 0.05 < t && t < u.end - 0.05);
    return { in: cutsInto(seg.in), out: cutsInto(seg.out) };
  }

  function posterUrl(seg, t) {
    const clip = clipOf(seg.clip);
    if (!clip || !clip.poster) return '';
    const sep = clip.poster.includes('?') ? '&' : '?';
    return `${clip.poster}${sep}t=${Math.max(0, t).toFixed(2)}`;
  }

  function setPoster(b, seg) {
    posterAt.set(seg.id, seg.in);
    const img = b.querySelector('.poster');
    const src = posterUrl(seg, seg.in);
    img.alt = `${stem(seg.clip)} at ${seg.in.toFixed(2)}s`;
    // No src at all rather than an empty one: src="" fetches the page itself.
    if (!src) img.removeAttribute('src');
    else if (img.getAttribute('src') !== src) img.setAttribute('src', src);
  }

  /* Posters catch up to the in-points once the trimming stops — one frame per settled
   * trim, never one per nudge (the cards' rule, and the same 450 ms). */
  function schedulePosters() {
    clearTimeout(posterTimer);
    posterTimer = setTimeout(() => {
      for (const [id, b] of blocks) {
        const seg = byId(id);
        if (seg && posterAt.get(id) !== seg.in) setPoster(b, seg);
        if (seg) paintStrip(b, seg, true);
      }
    }, POSTER_SETTLE_MS);
  }

  /* The V1 filmstrip (wave 3; verdict 3: "the filmstrip covers about 60 % of a block and
   * the rest is a maroon fill"): the head poster and then a frame every tile's width,
   * edge to edge, as Premiere's continuous thumbnails — the last tile is cut by the
   * block's end, so the pictures always reach it. A tile shows the clip at its left
   * edge, to the whole second after the head (so a trim reuses the frames it had, and
   * the posters' disk cache does the rest); while a trim is in flight the tiles only
   * grow or shrink in number and take their frames when it settles. */
  const STRIP_MAX = 48;              // tiles in one block, at most
  let stripH = 0;                    // V1's height, read once a render

  /* The filmstrip tiles' loader (lane YIELD; SAVESAFE's request): a pass leaves dozens of
   * tiles whose frames the server has to build, one ffmpeg each, and as plain <img> loads
   * they held all six of Chrome's connections to the host for seconds: every credentialed
   * GET (the first pass's poll, /api/project) queued behind them. Tiles now load through
   * this queue, at most POSTER_INFLIGHT at once, and only once they are within the
   * timeline's view (± 1250 px, Chrome's own lazy-load distance; an IntersectionObserver
   * on the scroller): the head frames (one a block) and every other picture load as
   * before, so a board leaves at least two connections free for its own requests. A frame
   * loaded once this session is set at once (the browser has it). V1's tiles and the
   * ghost lane's share it (`tl.posterLoad`). */
  const POSTER_INFLIGHT = 2;
  const posterQ = { busy: 0, loaded: new Set(), waiting: new Set(), ios: new Map() };
  function posterRelease(img) {
    if (!img._rcBusy) return;
    img._rcBusy = false;
    img.onload = img.onerror = null;
    posterQ.busy--;
  }
  function posterPump() {
    for (const img of [...posterQ.waiting]) {
      if (posterQ.busy >= POSTER_INFLIGHT) return;
      posterQ.waiting.delete(img);
      const src = img._rcWant;
      if (!src || !img.isConnected || !img._rcVis || img.getAttribute('src') === src) continue;
      img._rcBusy = true;
      posterQ.busy++;
      const fin = (ok) => {
        if (img._rcWant !== src) return;
        if (ok) posterQ.loaded.add(src);
        posterRelease(img);
        posterPump();
      };
      img.onload = () => fin(true);
      img.onerror = () => fin(false);
      img.setAttribute('src', src);
    }
  }
  function posterSeen(entries) {
    for (const e of entries) {
      const img = e.target;
      img._rcVis = e.isIntersecting;
      if (!img.isConnected) {
        e.target._rcObs = null;
        posterQ.ios.forEach((io) => io.unobserve(img));
        posterQ.waiting.delete(img);
        posterRelease(img);
      } else if (e.isIntersecting && img._rcWant && img.getAttribute('src') !== img._rcWant) {
        posterQ.waiting.add(img);
      } else if (!e.isIntersecting) {
        posterQ.waiting.delete(img);
      }
    }
    posterPump();
  }
  function posterObserve(img, tries = 0) {
    if (img._rcObs) return;
    if (!img.isConnected) {                 // a strip built before it is attached
      if (tries < 30) requestAnimationFrame(() => posterObserve(img, tries + 1));
      return;
    }
    const root = el.view && el.view.contains(img) ? el.view : null;
    let io = posterQ.ios.get(root);
    if (!io) {
      io = new IntersectionObserver(posterSeen, { root, rootMargin: '0px 1250px 0px 1250px' });
      posterQ.ios.set(root, io);
    }
    img._rcObs = io;
    io.observe(img);
  }
  function posterLoad(img, src) {
    if (!src) {
      img._rcWant = null;
      posterQ.waiting.delete(img);
      posterRelease(img);
      img.removeAttribute('src');
      return;
    }
    if (img._rcWant === src) return;
    img._rcWant = src;
    if (img._rcBusy) { posterRelease(img); posterPump(); }    // the browser drops the old load
    if (posterQ.loaded.has(src) || typeof IntersectionObserver !== 'function') {
      if (img.getAttribute('src') !== src) img.setAttribute('src', src);
      return;
    }
    img.loading = 'eager';                  // the queue decides when, not the browser
    if (img._rcVis) { posterQ.waiting.add(img); posterPump(); }
    posterObserve(img);
  }
  function paintStrip(b, seg, settle) {
    const strip = b.querySelector('.strip');
    if (!strip) return;
    const h = stripH || 40;
    const w = Math.max(3, dur(seg) * state.zoom);
    const tileW = Math.max(24, Math.round(h * 16 / 9));
    const n = clamp(Math.ceil(w / tileW), 1, STRIP_MAX);
    const tw = n === STRIP_MAX ? Math.ceil(w / n) : tileW;
    strip.style.setProperty('--tile', `${tw}px`);
    const have = strip.children.length;
    for (let k = have; k < n; k++) {
      const img = document.createElement('img');
      img.className = 'ftile';
      img.draggable = false; img.loading = 'lazy'; img.decoding = 'async'; img.alt = '';
      strip.appendChild(img);
    }
    while (strip.children.length > n) strip.lastElementChild.remove();
    if (pending && !settle) return;          // mid-trim: frames when it settles
    const spd = speedOf(seg);
    const tileS = (tw / state.zoom) * spd;               // clip seconds a tile spans
    const q = tileS >= 1 ? 1 : tileS >= 0.25 ? 0.25 : 1 / gridRate();
    for (let k = 1; k < n; k++) {
      const img = strip.children[k];
      const t = Math.min(seg.out - 0.05, seg.in + k * tileS);
      posterLoad(img, posterUrl(seg, Math.max(seg.in, Math.round(t / q) * q)));
    }
  }

  function updateBlock(b, seg, i, start, live) {
    const filmLen = dur(seg);                // what the block's width and its label are
    const spd = speedOf(seg);
    const gen = isGen(seg.clip);
    const w = Math.max(3, filmLen * state.zoom);
    b.style.left = `${timeToX(start)}px`;
    b.style.width = `${w}px`;
    b.style.setProperty('--hue', hueOf(seg.clip));
    b.dataset.i = i;
    b.classList.toggle('sel', state.sel.has(seg.id));
    b.classList.toggle('live', i === live);
    b.classList.toggle('narrow', w < 96);
    b.classList.toggle('tiny', w < 36);
    b.classList.toggle('gen', gen);
    b.classList.toggle('gap', isGap(seg));
    const clip = clipOf(seg.clip);
    if (isGap(seg)) {
      b.querySelector('.name').textContent = '';
      b.title = `gap · ${tc(filmLen)} of empty film — select it and ripple delete`
        + ` (X or ${keyLabel('⇧')}Del) to close it`;
      return;
    }
    // the badge: only when the shot is retimed, so a 1× cut looks the way it always did
    const badge = b.querySelector('.speed');
    badge.hidden = spd === 1;
    badge.textContent = spd === 1 ? '' : `${speedLabel(spd)}×`;
    paintSync(b.querySelector('.sync'), seg);
    // what fits beside the badges: the name, its number, or nothing (never "CLI…").
    // The badges' widths are their own text in their own fonts plus their boxes.
    const nameEl = b.querySelector('.name');
    if (!v1Font && !gen) v1Font = fontOf(nameEl, 'normal');
    const fam = v1Font.replace(/^.*?[\d.]+px\s+/, '');
    const sync = b.querySelector('.sync');
    // .speed: 600 10px, 4 px of padding a side, 4 px margin; .sync: 700 9px, 3 + 3, 4 margin
    const badgesW = (badge.hidden ? 0 : 12 + textWidth(badge.textContent, `normal 600 10px ${fam}`))
      + (sync.hidden ? 0 : 10 + textWidth(sync.textContent, `normal 700 9px ${fam}`));
    const full = blockName(seg, clip);
    const lab = fitLabel(full, w - V1_LABEL_PAD - badgesW, gen ? v1Font.replace(/^\S+/, 'italic') : v1Font);
    nameEl.textContent = lab;
    b.classList.toggle('unlinked', !isLinked(seg));
    const says = blockSays(seg, clip);
    b.title = (says ? `“${says}”\n` : '')
      + `${i + 1}. ${gen ? genLine(seg, clip) : stem(seg.clip)} ${fmt(seg.in)}–${fmt(seg.out)}`
      + (spd === 1 ? ` (${filmLen.toFixed(1)}s)`
                   : ` at ${speedLabel(spd)}× (${filmLen.toFixed(1)}s of film from ${(seg.out - seg.in).toFixed(1)}s)`)
      + (isLinked(seg) ? '' : ' · unlinked from its sound on A1')
      + (seg.why && seg.why !== says ? `\n${seg.why}` : '');
    const warn = warnEdges(seg, clip);
    const wi = b.querySelector('.warn.in'), wo = b.querySelector('.warn.out');
    wi.hidden = !warn.in; wi.title = 'opens mid-sentence';
    wo.hidden = !warn.out; wo.title = 'cuts a line off';
    if (!posterAt.has(seg.id)) setPoster(b, seg);
    else if (posterAt.get(seg.id) !== seg.in) schedulePosters();
    paintStrip(b, seg, false);
    if (pending) schedulePosters();
  }

  /* ================================================== the tracks (V2 V1 A1 A2) */
  /* A shot is a picture on V1 and, linked to it, its clip's own sound on A1. `link:
   * false` unlinks the pair (Alt-click either half); `a_off` is where the sound sits
   * against the picture, in film seconds — only an unlinked pair's sound moves alone,
   * and relinking keeps the offset (Premiere shows it in red on both halves). */
  const isLinked = (seg) => !(seg && seg.link === false);
  function aOff(seg) {
    const x = Number(seg && seg.a_off);
    return Number.isFinite(x) ? x : 0;
  }

  function paintSync(badge, seg) {
    if (!badge) return;
    const off = aOff(seg);
    badge.hidden = !off;
    // Premiere's red offset, without the zero hours and minutes: +00:12 is twelve frames
    badge.textContent = off ? `${off > 0 ? '+' : '−'}${tc(Math.abs(off)).replace(/^(00:)+(?=\d\d:\d\d$)/, '')}` : '';
    badge.title = off ? `out of sync: the sound is ${Math.abs(off).toFixed(2)} s ${off > 0 ? 'later' : 'earlier'} than the picture` : '';
  }

  function trackState(name) { return state.tracks[name] || DEFAULT_TRACKS()[name] || extraDefault(name); }
  /* Whether an audio track is heard: not muted and, when any track is soloed, soloed. */
  function audible(name) {
    const t = trackState(name);
    if (!t || t.mute) return false;
    const anySolo = trackNames().filter((n) => n[0] === 'A').some((n) => trackState(n).solo);
    return anySolo ? !!t.solo : true;
  }
  const cutLocked = () => trackState('V1').lock || trackState('A1').lock;

  function refuseLocked(name) {
    toast(`${name} is locked — unlock it in its track header to edit`);
    return null;
  }
  /* for the panels that edit a clip outside the timeline (effects.js): true, with the
   * toast, when `name` is locked */
  function refuseIfLocked(name) {
    if (!trackState(name).lock) return false;
    refuseLocked(name);
    return true;
  }

  function makeABlock(id) {
    const b = document.createElement('div');
    b.className = 'ablk';
    b.dataset.id = id;
    b.innerHTML = '<div class="wave"></div><span class="sync" hidden></span><span class="name"></span>';
    return b;
  }

  /* ---- A1's waveforms (wave 3 TLAUDIO; verdict 3: "A1 has no waveform") */
  /* The clip's real sound from /api/peaks (min/max pairs, int8, the whole clip at
   * `pps` a second), fetched once per clip and rate, only for a block that is on screen,
   * and drawn per block as one filled SVG path for the shot's range at its rate. The
   * track's gain scales it, as Premiere's clip waveform follows its level. */
  const peaks = new Map();          // `${clip}@${pps}` -> {done, data}
  let a1Raf = 0;
  const peaksRate = () => (state.zoom > 60 ? 100 : 50);
  function peaksFor(clip, pps) {
    const key = `${clip}@${pps}`;
    let p = peaks.get(key);
    if (p) return p;
    p = { done: false, data: null, tries: 0 };
    peaks.set(key, p);
    /* 202 {pending}: the server's worker is still making them (never in the request,
     * wave 11 CHEAP) — ask again, sooner at first; the block reads "loading" meanwhile */
    const ask = () => fetch(`/api/peaks/${encodeURIComponent(stem(clip))}?pps=${pps}`)
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null)
      .then((d) => {
        if (d && d.pending) {
          p.tries += 1;
          setTimeout(ask, Math.min(2000, 250 * p.tries));
          return;
        }
        p.data = d; p.done = true; scheduleA1();
      });
    ask();
    return p;
  }
  function scheduleA1() {
    if (a1Raf) return;
    a1Raf = requestAnimationFrame(() => {
      a1Raf = 0;
      renderA1(segs(), hooks && hooks.live ? hooks.live() : -1);
    });
  }
  /* The waveform path for `w` px of a shot: per column, the loudest and quietest peak
   * under it, mirrored about the middle of a 0..100 box; null when there is nothing. */
  function wavePath(data, seg, w, gain) {
    const lo = data && data.min, hi = data && data.max;
    if (!lo || !lo.length) return null;
    const pps = data.pps, spd = speedOf(seg), z = state.zoom;
    const step = Math.max(1, w / 1500);
    const top = [], bot = [];
    for (let x = 0; x < w; x += step) {
      const t0 = seg.in + (x / z) * spd, t1 = seg.in + (Math.min(w, x + step) / z) * spd;
      let i0 = Math.floor(t0 * pps), i1 = Math.max(i0 + 1, Math.ceil(t1 * pps));
      i0 = clamp(i0, 0, lo.length - 1); i1 = clamp(i1, i0 + 1, lo.length);
      let mn = 0, mx = 0;
      for (let k = i0; k < i1; k++) { if (lo[k] < mn) mn = lo[k]; if (hi[k] > mx) mx = hi[k]; }
      const yt = 50 - clamp((mx / 127) * gain, 0, 1) * 49, yb = 50 - clamp((mn / 127) * gain, -1, 0) * 49;
      top.push(`${x.toFixed(1)} ${Math.min(yt, 49.6).toFixed(1)}`);
      bot.push(`${x.toFixed(1)} ${Math.max(yb, 50.4).toFixed(1)}`);
    }
    top.push(`${w.toFixed(1)} ${top[top.length - 1].split(' ')[1]}`);
    bot.push(`${w.toFixed(1)} ${bot[bot.length - 1].split(' ')[1]}`);
    return `M${top.join('L')}L${bot.reverse().join('L')}Z`;
  }
  /* Within a viewport of the view, either side. `at` is the view's scrollLeft as read
   * once by the caller BEFORE it wrote any style (wave 5, SAVE): reading it here, after
   * each A1 block's left/width, forced a layout per block (~16 ms a render under load). */
  function onScreen(x, w, at) {
    if (!el.view) return true;
    const vw = viewWidth() || 1e9;
    return x + w >= at - vw && x <= at + 2 * vw;
  }

  /* The view's size, kept by the ResizeObserver (mount), whose callback runs where the
   * layout is already fresh: a render, a playhead move or keepInView never forces one to
   * learn it. Read directly only until the observer has reported (0 = not yet). */
  let viewW = 0, viewH = 0;
  function viewWidth() { return viewW || (el.view ? el.view.clientWidth : 0); }
  function viewHeight() { return viewH || (el.view ? el.view.clientHeight : 0); }
  let canvasW = 0;                     // the canvas width the last render wrote

  /* A1: each shot's sound, at its film start plus its offset: the clip's waveform for
   * the shot's range (above), over a faint band where the transcript says someone
   * speaks. */
  function renderA1(list, live, at) {
    const lane = el.tracks.A1;
    if (!lane) return;
    // one read, before this function writes anything (render passes the one it read)
    const v0 = at != null ? at : (el.view ? el.view.scrollLeft : 0);
    const seen = new Set();
    const pps = peaksRate();
    const g = trackState('A1');
    const gain = Math.pow(10, (Number(g && g.gain_db) || 0) / 20);
    let start = 0;
    list.forEach((seg, i) => {
      let b = ablocks.get(seg.id);
      if (!b) { b = makeABlock(seg.id); ablocks.set(seg.id, b); }
      if (lane.children[i] !== b) lane.insertBefore(b, lane.children[i] || null);
      const d = dur(seg), spd = speedOf(seg), w = Math.max(3, d * state.zoom);
      const x = timeToX(start + aOff(seg));
      b.style.left = `${x}px`;
      b.style.width = `${w}px`;
      b.style.setProperty('--hue', hueOf(seg.clip));
      b.classList.toggle('sel', state.sel.has(seg.id));
      b.classList.toggle('live', i === live);
      b.classList.toggle('unlinked', !isLinked(seg));
      b.classList.toggle('narrow', w < 60);
      b.classList.toggle('gen', isGen(seg.clip));
      b.classList.toggle('gap', isGap(seg));
      b.querySelector('.name').textContent = isGen(seg.clip) ? `${genKind(seg.clip)} · silence` : stem(seg.clip);
      paintSync(b.querySelector('.sync'), seg);
      const clip = clipOf(seg.clip);
      const visible = onScreen(x, w, v0);
      const pk = !isGen(seg.clip) && visible ? peaksFor(seg.clip, pps) : null;
      // the coarser rate stands in while the finer one loads
      const shown = pk && pk.done ? pk : (pk && pps !== 50 ? peaks.get(`${seg.clip}@50`) : null);
      const ready = shown && shown.done ? (shown.data && shown.data.min && shown.data.min.length ? 'ok' : 'none') : 'loading';
      const key = `${seg.in}|${seg.out}|${spd}|${Math.round(w)}|${gain}|${ready}|${shown && shown.data ? shown.data.pps : 0}`;
      if (isGen(seg.clip)) b.dataset.peaks = 'none';
      else if (visible || b.dataset.wave) b.dataset.peaks = ready;
      if ((visible || isGen(seg.clip)) && b.dataset.wave !== key) {
        b.dataset.wave = key;
        const wave = b.querySelector('.wave');
        wave.replaceChildren();
        for (const u of (clip && clip.transcript) || []) {
          const a0 = Math.max(u.start, seg.in), b0 = Math.min(u.end, seg.out);
          if (b0 <= a0) continue;
          const s = document.createElement('i');
          s.style.left = `${((a0 - seg.in) / spd) * state.zoom}px`;
          s.style.width = `${Math.max(1, ((b0 - a0) / spd) * state.zoom)}px`;
          wave.appendChild(s);
        }
        const path = ready === 'ok' ? wavePath(shown.data, seg, w, gain) : null;
        if (path) {
          const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
          svg.setAttribute('class', 'wf');
          svg.setAttribute('viewBox', `0 0 ${Math.max(1, w).toFixed(1)} 100`);
          svg.setAttribute('preserveAspectRatio', 'none');
          const pe = document.createElementNS('http://www.w3.org/2000/svg', 'path');
          pe.setAttribute('d', path);
          svg.appendChild(pe);
          wave.appendChild(svg);
        }
      }
      const says = blockSays(seg, clip);
      b.title = `A1 · ${window.rcClipName ? window.rcClipName(seg.clip) : stem(seg.clip)} ${fmt(seg.in)}–${fmt(seg.out)} — its own sound`
        + (isLinked(seg) ? ', linked to the picture on V1' : ', unlinked: drag to move it alone')
        + (aOff(seg) ? ` · ${aOff(seg) > 0 ? '+' : '−'}${Math.abs(aOff(seg)).toFixed(2)} s out of sync` : '')
        + (says ? `\n“${says}”` : '')
        + `\n${keyLabel('⌥')}-click: ${isLinked(seg) ? 'unlink' : 'link'}`;
      seen.add(seg.id);
      start += d;
    });
    for (const [id, b] of ablocks) {
      if (seen.has(id)) continue;
      b.remove(); ablocks.delete(id);
    }
    lane.classList.toggle('muted', !audible('A1'));
  }

  function makeOBlock(id) {
    const b = document.createElement('div');
    b.className = 'oblk';
    b.dataset.id = id;
    b.innerHTML = '<img class="poster" draggable="false" loading="lazy" decoding="async" alt=""><span class="name"></span>';
    return b;
  }

  /* V2 (and V3…): clips laid over the cut at their own film time (picture only); an
   * added audio track's clips (A3…) are drawn the same way, sound only. */
  function renderV2() {
    if (!el.tracks.V2) return;
    const seen = new Set();
    const all = [...state.overlays, ...state.audio.map((c) => ({ ...c, sound: true }))];
    for (const o of all) {
      const name = o.track || 'V2';
      const lane = el.tracks[name];
      if (!lane) continue;
      let b = oblocks.get(o.id);
      if (!b) { b = makeOBlock(o.id); oblocks.set(o.id, b); }
      if (b.parentNode !== lane) lane.appendChild(b);
      b.classList.toggle('aclip', !!o.sound);
      const d = dur(o), w = Math.max(3, d * state.zoom);
      b.style.left = `${timeToX(o.at)}px`;
      b.style.width = `${w}px`;
      b.style.setProperty('--hue', hueOf(o.clip));
      b.classList.toggle('sel', state.osel === o.id);
      b.classList.toggle('narrow', w < 80);
      b.querySelector('.name').textContent = blockName(o);
      const img = b.querySelector('.poster');
      const src = posterUrl(o, o.in);
      if (!src) img.removeAttribute('src'); else if (img.getAttribute('src') !== src) img.setAttribute('src', src);
      const says = blockSays(o, clipOf(o.clip));
      b.title = `${name} · ${stem(o.clip)} ${fmt(o.in)}–${fmt(o.out)} at ${tc(o.at)}–${tc(o.at + d)}`
        + (o.sound ? ' — its sound, mixed with A1' : name === 'V2' ? ' — composited over V1, picture only'
          : ' — composited over the tracks below it, picture only') + (says ? `\n“${says}”` : '')
        + (name === 'V2' ? '\ndrag to move · Delete removes it' : '\ndrag to move, an edge to trim · Delete removes it');
      seen.add(o.id);
    }
    for (const [id, b] of oblocks) {
      if (seen.has(id)) continue;
      b.remove(); oblocks.delete(id);
    }
    if (state.osel != null && !overlayById(state.osel)) state.osel = null;
  }

  /* The rows and headers of the added tracks, made or dropped to match state.tracks. */
  function ensureTrackRows() {
    if (!el.canvas || !el.heads) return;
    const box = el.side.querySelector('.tl-heads');
    const want = trackNames().filter(isExtra);
    for (const n of want) {
      if (el.tracks[n]) continue;
      const row = document.createElement('div');
      row.className = `tl-track tl-xtrack ${n[0] === 'V' ? 'video' : 'audio'}`;
      row.dataset.track = n;
      el.canvas.insertBefore(row, el.end);
      row.addEventListener('pointerdown', onLaneDown);
      row.addEventListener('pointermove', onLaneMove);
      row.addEventListener('pointerup', onTrackUp);
      row.addEventListener('pointercancel', () => { down = null; });
      // a clip on V3… / A3… drags like V2's, and its edges trim (wave 6, KEYS2)
      row.addEventListener('pointerdown', onXDown, true);
      row.addEventListener('pointermove', onXMove, true);
      row.addEventListener('pointerup', (e) => endXDrag(e, true), true);
      row.addEventListener('pointercancel', (e) => endXDrag(e, false), true);
      el.tracks[n] = row;
      el.lanes[n] = row;
      buildHead(box, n);
    }
    for (const n of [...EXTRA_V, ...EXTRA_A]) {
      if (want.includes(n) || !el.tracks[n]) continue;
      el.tracks[n].remove();
      delete el.tracks[n];
      delete el.lanes[n];
      if (el.heads[n]) { el.heads[n].remove(); delete el.heads[n]; }
    }
  }

  /* Sequence › Add Track: the next free V3…V9 (kind 'video') or A3…A9 ('audio'). One
   * undo step (wave 6, KEYS2): the added tracks ride the undo entry (`xtracks`). */
  function addTrack(kind) {
    const pool = kind === 'audio' ? EXTRA_A : EXTRA_V;
    const name = pool.find((n) => !state.tracks[n]);
    if (!name) { toast(`no more ${kind} tracks — ${pool[pool.length - 1]} is the last`); return null; }
    const own = begin(`add ${name}`);
    delete retired[name];
    state.tracks = { ...state.tracks, [name]: extraDefault(name) };
    tracksChanged();
    if (own) commit();
    toast(`added ${name}`);
    return name;
  }

  /* Sequence › Delete Track: the highest added track of that kind (or `name`), and the
   * clips on it with it — one undo step, as Premiere's Delete Tracks is (no refusal, no
   * confirm: ⌘Z brings the track and its clips back). V1, V2, A1 and A2 always stay. */
  function deleteTrack(kindOrName) {
    const name = isExtra(kindOrName) ? kindOrName
      : (kindOrName === 'audio' ? EXTRA_A : EXTRA_V).filter((n) => state.tracks[n]).pop();
    if (!name || !state.tracks[name]) {
      toast(`no added ${kindOrName === 'audio' ? 'audio' : 'video'} track to delete — V1, V2, A1 and A2 always stay`);
      return false;
    }
    if (trackState(name).lock) { refuseLocked(name); return false; }
    const n = state.overlays.filter((o) => o.track === name).length
      + state.audio.filter((c) => c.track === name).length;
    const own = begin(`delete ${name}`);
    state.overlays = state.overlays.filter((o) => o.track !== name);
    state.audio = state.audio.filter((c) => c.track !== name);
    if (state.osel != null && !overlayById(state.osel)) state.osel = null;
    const next = { ...state.tracks };
    retired[name] = next[name];
    delete next[name];
    state.tracks = next;
    tracksChanged();
    if (own) commit();
    toast(`deleted ${name}${n ? ` and its ${n} clip${n > 1 ? 's' : ''}` : ''} — ${keyLabel('⌘Z')} brings it back`);
    return true;
  }

  /* After the set of added tracks changed (an add, a delete, an undo of either). */
  function tracksChanged() {
    ensureTrackRows();
    render();
    applyMonitorAudio();
    scheduleSeqSave();
    emit('tracks', { tracks: tracks() });
  }

  /* An undo entry's added tracks: the names in it, and only those. A track brought back
   * keeps the header settings it had when it went (`retired`) — a toggle is not an edit. */
  const retired = {};
  function setExtraTracks(names) {
    const want = new Set(names);
    const next = { ...state.tracks };
    let changed = false;
    for (const n of [...EXTRA_V, ...EXTRA_A]) {
      if (want.has(n) && !next[n]) { next[n] = retired[n] || extraDefault(n); delete retired[n]; changed = true; }
      else if (!want.has(n) && next[n]) { retired[n] = next[n]; delete next[n]; changed = true; }
    }
    if (!changed) return false;
    state.tracks = next;
    ensureTrackRows();
    applyMonitorAudio();
    emit('tracks', { tracks: tracks() });
    return true;
  }

  /* The rows share the panel's height: V2, V1, the lanes' ghost (a proposal) under V1,
   * then A1, A2 and the lanes' bin outlines. Every row's header sits at its top. */
  function layoutTracks() {
    if (!el.root || !el.view) return;
    const extra = (name) => {
      const x = el.lanes[name];
      return x && !x.hidden ? (x.h || x.offsetHeight || 0) : 0;
    };
    const ghost = extra('ghost'), bin = extra('bin');
    const gaps = ROW_GAP * (trackNames().length - 1) + AV_GAP + (ghost ? ROW_GAP : 0) + (bin ? ROW_GAP : 0);
    const vh = viewHeight();
    const avail = Math.max(0, vh - RULER_H - 2 - gaps - ghost - bin);
    const names = trackNames();
    const wsum = names.reduce((a, n) => a + trackW(n), 0);
    const h = {};
    for (const n of names) h[n] = Math.max(trackMin(n), Math.floor(avail * trackW(n) / wsum));
    const rows = names.filter((n) => n[0] === 'V').map((n) => [n, h[n]]);
    if (ghost) rows.push(['ghost', ghost]);
    rows.push(['|', AV_GAP - ROW_GAP]);
    for (const n of names.filter((x) => x[0] === 'A')) rows.push([n, h[n]]);
    if (bin) rows.push(['bin', bin]);
    let y = RULER_H + 2;
    const tops = {};
    for (const [name, hh] of rows) {
      if (name !== '|') {
        tops[name] = y;
        const row = name === 'V1' ? el.lanes.V1 : (el.tracks[name] || el.lanes[name]);
        if (row) { row.style.top = `${y}px`; row.style.height = `${hh}px`; }
        const head = el.heads && el.heads[name];
        if (head) { head.style.top = `${y}px`; head.style.height = `${hh}px`; }
      }
      y += hh + ROW_GAP;
    }
    el.root.style.setProperty('--tl-lane', `${h.V1}px`);
    el.root.style.setProperty('--tl-v1h', `${h.V1}px`);
    el.root.style.setProperty('--tl-v1top', `${tops.V1}px`);
    el.root.style.setProperty('--tl-ruler', `${RULER_H}px`);
    el.canvas.style.height = `${Math.max(vh, y)}px`;
    if (el.divider) el.divider.style.top = `${tops.A1 - AV_GAP / 2 - 1}px`;
    state.trackTops = tops;
    if (h.V1 !== stripH) {           // a taller V1 is wider tiles: the strips follow
      stripH = h.V1;
      for (const [id, b] of blocks) { const seg = byId(id); if (seg) paintStrip(b, seg, false); }
    }
  }

  const LOCK_SVG = '<svg viewBox="0 0 12 12" aria-hidden="true"><rect x="2" y="5.5" width="8" height="5.5" rx="1"/>'
    + '<path d="M4 5.5V4a2 2 0 0 1 4 0v1.5" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>';
  const EYE_SVG = '<svg viewBox="0 0 14 10" aria-hidden="true"><path d="M1 5c1.6-2.7 3.7-4 6-4s4.4 1.3 6 4c-1.6 2.7-3.7 4-6 4S2.6 7.7 1 5z"'
    + ' fill="none" stroke="currentColor" stroke-width="1.2"/><circle cx="7" cy="5" r="1.8"/>'
    + '<path class="off" d="M2 9.5 12 .5" stroke="currentColor" stroke-width="1.3"/></svg>';
  const HEAD_WORDS = {
    target: (n) => `${n} — target: ${n[0] === 'V' ? 'a source clip goes here (, insert · . overwrite)' : 'its sound goes here'}`,
    lock: (n) => `lock ${n}: no edits on it`,
    eye: (n) => `toggle ${n}'s output: hidden from the Program monitor's render`,
    mute: (n) => `mute ${n}`,
    solo: (n) => `solo ${n}: only soloed tracks are heard`,
  };

  function buildHead(box, n) {
    {
      const h = document.createElement('div');
      h.className = `tl-thead ${n[0] === 'V' ? 'video' : 'audio'}`;
      h.dataset.track = n;
      const btn = (k, inner, cls = '') => `<button type="button" class="${k} ${cls}" data-toggle="${k}"`
        + ` title="${HEAD_WORDS[k](n)}" aria-label="${HEAD_WORDS[k](n)}">${inner}</button>`;
      h.innerHTML = btn('lock', LOCK_SVG) + btn('target', n, 'tname')
        + (n[0] === 'V' ? btn('eye', EYE_SVG)
          : btn('mute', 'M') + btn('solo', 'S'))
        + `<span class="tinfo"></span>`;
      box.appendChild(h);
      el.heads[n] = h;
    }
  }

  function buildHeads(side) {
    const box = side.querySelector('.tl-heads');
    el.heads = {};
    for (const n of TRACKS) buildHead(box, n);
    box.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-toggle]');
      if (!b) return;
      const n = b.closest('.tl-thead').dataset.track, k = b.dataset.toggle;
      const v = k === 'eye' ? !trackState(n).eye : !trackState(n)[k];
      setTrack(n, k, v);
      toast(`${n} ${k === 'eye' ? (v ? 'shown' : 'hidden') : k === 'target' ? (v ? 'targeted' : 'not targeted')
        : `${v ? '' : 'un'}${k === 'lock' ? 'locked' : k === 'mute' ? 'muted' : 'soloed'}`}`);
    });
  }

  function paintHeads() {
    if (!el.heads) return;
    for (const n of trackNames()) {
      const t = trackState(n), h = el.heads[n];
      if (!h) continue;
      for (const b of h.querySelectorAll('button[data-toggle]')) {
        const k = b.dataset.toggle;
        b.classList.toggle('on', k === 'eye' ? !t.eye : !!t[k]);
        b.setAttribute('aria-pressed', String(k === 'eye' ? !t.eye : !!t[k]));
      }
      h.classList.toggle('locked', !!t.lock);
      const row = n === 'V1' ? el.lanes.V1 : el.tracks[n];
      if (row) {
        row.classList.toggle('locked', !!t.lock);
        row.classList.toggle('hidden-out', n[0] === 'V' && !t.eye);
        row.classList.toggle('muted', n[0] === 'A' && !audible(n));
      }
      const info = h.querySelector('.tinfo');
      if (n[0] === 'A') {
        const g = Number(t.gain_db) || 0;
        info.textContent = g ? `${g > 0 ? '+' : ''}${g.toFixed(1)} dB` : '';
      }
    }
  }

  function setTrack(name, key, value) {
    const t = state.tracks[name];
    if (!t || !(key in t)) return false;
    let v = value;
    if (key === 'gain_db') {
      v = Math.round(clamp(Number(value) || 0, -60, 12) * 10) / 10;
    } else v = !!value;
    if (t[key] === v) return false;
    state.tracks = { ...state.tracks, [name]: { ...t, [key]: v } };
    paintHeads();
    renderA1(segs(), hooks && hooks.live ? hooks.live() : -1);
    applyMonitorAudio();
    scheduleSeqSave();
    emit('tracks', { tracks: tracks() });
    return true;
  }

  function tracks() { return JSON.parse(JSON.stringify(state.tracks)); }

  /* What the monitor plays follows A1 and A2: the Program's videos carry A1's level
   * (0 when muted or soloed out), the bed follows A2. Volume, not `muted`: the board
   * owns `muted` for its own reasons. The level goes through meters.js's
   * `rcAudio.setLevel(el, 'track', x)`, the one owner of an element's level (a gain node
   * once the element is in the WebAudio graph, so A1 above 0 dB is heard above 0 dB);
   * without it, the element's volume, capped at 1. */
  function applyMonitorAudio() {
    const g = audible('A1') ? Math.pow(10, (Number(trackState('A1').gain_db) || 0) / 20) : 0;
    try {
      // eslint-disable-next-line no-undef
      const vids = typeof player !== 'undefined' && player && player.vids ? player.vids : [];
      const ra = window.rcAudio && window.rcAudio.setLevel ? window.rcAudio : null;
      for (const v of vids) {
        if (!v) continue;
        if (ra) ra.setLevel(v, 'track', g);
        else if (Math.abs(v.volume - Math.min(1, g)) > 1e-3) v.volume = Math.min(1, g);
      }
    } catch (err) { /* no board */ }
    const bed = document.querySelector('#bed');
    if (bed) bed.muted = !audible('A2');
  }

  /* ---- the sequence's own save: tracks, V2, markers and In / Out */
  function tracksDiff() {
    const base = DEFAULT_TRACKS(), out = {};
    for (const n of trackNames()) {
      const b = base[n] || extraDefault(n);
      if (isExtra(n)) out[n] = {};                 // an added track's entry is its existence
      for (const [k, v] of Object.entries(state.tracks[n])) {
        if (b[k] !== v) (out[n] = out[n] || {})[k] = v;
      }
    }
    return out;
  }

  function seqBody() {
    return JSON.stringify({
      tracks: tracksDiff(),
      overlays: state.overlays.map((o) => ({ ...o })),
      audio: state.audio.map((c) => ({ ...c })),
      markers: [...state.markers], in: state.seqIn, out: state.seqOut,
    });
  }

  function scheduleSeqSave() {
    if (seqSaved == null) return;              // not mounted yet
    if (previewing()) return;                  // a `record: false` batch saves nothing
    clearTimeout(seqTimer);
    seqTimer = setTimeout(saveSeq, SEQ_SAVE_MS);
  }

  function saveSeq() {
    clearTimeout(seqTimer);
    return putTurn(saveSeqNow);       // never beside the cut's own save (wave 5, SAVE)
  }

  async function saveSeqNow() {
    const body = seqBody();
    if (body === seqSaved) return true;
    try {
      // credentials 'omit': its own connections, never queued behind the posters
      // (wave 8, SAVESAFE — see saveHeaders)
      const r = await fetch('/api/project', {
        method: 'PUT', headers: saveHeaders(), body, credentials: 'omit',
      });
      if (!r.ok) {
        const msg = await r.text().catch(() => '');
        toast(`the tracks did not save: ${msg.slice(0, 120)}`);
        return false;
      }
      seqSaved = body;
      return true;
    } catch (err) {
      toast('the tracks did not save — the board is unreachable');
      return false;
    }
  }

  function loadSeq() {
    let p = null;
    try {
      // eslint-disable-next-line no-undef
      p = typeof P !== 'undefined' && P ? P : null;
    } catch (err) { p = null; }
    const base = DEFAULT_TRACKS();
    const got = (p && p.tracks) || {};
    for (const n of TRACKS) base[n] = { ...base[n], ...(got[n] || {}) };
    for (const n of [...EXTRA_V, ...EXTRA_A]) {
      if (got[n]) base[n] = { ...extraDefault(n), ...got[n] };
    }
    state.tracks = base;
    state.overlays = ((p && p.overlays) || []).filter((o) => o && o.clip)
      .map((o) => ({ ...o, id: o.id || newId() }));
    state.audio = ((p && p.audio) || []).filter((c) => c && c.clip && c.track)
      .map((c) => ({ ...c, id: c.id || newId() }));
    state.transitions = ((p && p.transitions) || []).filter((t) => t && (t.a || t.b) && TRANS_NAMES[t.kind])
      .map((t) => ({ a: t.a || null, b: t.b || null, kind: t.kind, dur: Number(t.dur) || TRANS_DEFAULT_S,
                     align: alignOf(t) }));
  }

  /* An id in the server's own shape (`g` + 10 hex), so a V2 clip never needs re-keying. */
  function newId() {
    const a = new Uint8Array(5);
    (window.crypto || window.msCrypto).getRandomValues(a);
    return `g${[...a].map((x) => x.toString(16).padStart(2, '0')).join('')}`;
  }

  /* ---- link and offset */
  function setLink(id, linked) {
    const seg = byId(id);
    if (!seg || isLinked(seg) === !!linked) return false;
    return !!mutate(linked ? 'link' : 'unlink', () => {
      if (linked) delete seg.link; else seg.link = false;
      return true;
    });
  }

  function toggleLink(id) {
    const seg = byId(id);
    if (!seg) return false;
    const to = !isLinked(seg);
    if (!setLink(id, to)) return false;
    toast(to ? `linked ${stem(seg.clip)} — its picture and sound move together${aOff(seg) ? ' (still out of sync)' : ''}`
      : `unlinked ${stem(seg.clip)} — drag its sound on A1 to move it alone`);
    return true;
  }

  /* The shot's sound against its picture, in film seconds (on the frame grid); only an
   * unlinked pair's. 0 puts it back in sync. */
  function setAudioOffset(id, off) {
    const seg = byId(id);
    if (!seg || isLinked(seg)) return false;
    const x = clamp(Number(off) || 0, -600, 600);
    const v = Math.sign(x) * onGrid(Math.abs(x));
    if (v === aOff(seg)) return false;
    return !!mutate('move audio', () => {
      if (v) seg.a_off = v; else delete seg.a_off;
      return true;
    });
  }

  /* ---- V2 */
  function overlayById(id) {
    return state.overlays.find((o) => o.id === id) || state.audio.find((c) => c.id === id) || null;
  }

  function overlayMutate(kind, fn, track = 'V2') {
    if (trackState(track).lock) return refuseLocked(track);
    const own = begin(kind);
    const r = fn();
    renderV2();
    if (own) commit();
    scheduleSeqSave();
    emit('change', { label: kind, kind, ids: state.osel ? [state.osel] : [] });
    return r;
  }

  /* A clip laid on V2 at a film time: `{clip, in, out, speed?, why?}`. Returns its id. */
  function addOverlay(seg, atFilmTime, track = 'V2') {
    if (!seg || !seg.clip || !(seg.out - seg.in >= MIN_LEN - 1e-9)) return null;
    const sound = track[0] === 'A';
    if (track !== 'V2' && !state.tracks[track]) return null;
    if (sound && !EXTRA_A.includes(track)) return null;
    const g = gridded(seg);
    const o = { id: newId(), clip: seg.clip, in: g.in, out: g.out, at: onGrid(atFilmTime) };
    if (track !== 'V2') o.track = track;
    if (seg.speed && speedOf(seg) !== 1) o.speed = speedOf(seg);
    if (seg.why) o.why = seg.why;
    return overlayMutate(`${track} clip`, () => {
      if (sound) state.audio = [...state.audio, o];
      else state.overlays = [...state.overlays, o];
      state.osel = o.id;
      return o.id;
    }, track);
  }

  /* Any track's clip — V2, V3…, A3… — to a film time; one `move <track>` entry. */
  function moveOverlay(id, atFilmTime) {
    const o = overlayById(id);
    if (!o) return false;
    const at = Math.max(0, onGrid(atFilmTime));
    if (at === o.at) return false;
    const track = o.track || 'V2';
    return !!overlayMutate(`move ${track}`, () => {
      state.overlays = state.overlays.map((x) => (x.id === id ? { ...x, at } : x));
      state.audio = state.audio.map((x) => (x.id === id ? { ...x, at } : x));
      return true;
    }, track);
  }

  function removeOverlay(id) {
    const o = overlayById(id);
    if (!o) return false;
    return !!overlayMutate(`remove ${o.track || 'V2'}`, () => {
      state.overlays = state.overlays.filter((x) => x.id !== id);
      state.audio = state.audio.filter((x) => x.id !== id);
      if (state.osel === id) state.osel = null;
      return true;
    }, o.track || 'V2');
  }

  /* A clip on any track to new edges: `edge` 'in' or 'out' goes to film time `filmT`
   * (the other edge stays put in the film); one `trim <track>` entry. Clamped to the
   * clip's media and the shortest shot. */
  function trimOverlay(id, edge, filmT) {
    const o = overlayById(id);
    if (!o) return false;
    const spd = speedOf(o), c = clipOf(o.clip);
    const cd = c && c.duration ? c.duration : Infinity;
    let a = o.in, b = o.out, at = o.at;
    if (edge === 'in') {
      const t = Math.max(0, onGrid(filmT));
      a = r4(clamp(o.in + (t - o.at) * spd, 0, o.out - MIN_LEN * spd));
      at = onGrid(o.at + (a - o.in) / spd);
    } else {
      b = r4(clamp(o.in + (onGrid(filmT) - o.at) * spd, o.in + MIN_LEN * spd, cd));
    }
    if (a === o.in && b === o.out && at === o.at) return false;
    const track = o.track || 'V2';
    const fix = (x) => {
      if (x.id !== id) return x;
      const y = { ...x, in: a, out: b, at };
      // a clip on V2+ with keyframes keeps them on its media too (KEYANCHOR)
      if (keysOfSeg(x) || (keyBase && keyBase.p === pending && keyBase.m.has(x.id))) anchorRange(y, baseOf(x), a, b);
      return y;
    };
    return !!overlayMutate(`trim ${track}`, () => {
      state.overlays = state.overlays.map(fix);
      state.audio = state.audio.map(fix);
      return true;
    }, track);
  }

  /* V3… and A3…: a press on a clip drags it along its track, snapping its start or its
   * end to a cut, the playhead or another track's clip edge within 8 px (V2's own drag
   * is timeline-lanes.js's); a press within 6 px of its edge trims that edge. */
  const XEDGE = 6, XCLICK = 4;
  let xdrag = null;
  function xMode(b, x) {
    const r = b.getBoundingClientRect();
    if (r.width < 4 * XEDGE) return 'move';
    if (x - r.left <= XEDGE) return 'in';
    if (r.right - x <= XEDGE) return 'out';
    return 'move';
  }

  function xSnaps(skip) {
    const near = [state.playhead];
    let t = 0;
    for (const s of segs()) { near.push(t); t += dur(s); }
    near.push(t);
    for (const o of [...state.overlays, ...state.audio]) {
      if (o.id !== skip) near.push(o.at, o.at + dur(o));
    }
    return near;
  }

  function snapX(t, near) {
    for (const c of near) if (Math.abs(c - t) * state.zoom < XCLICK * 2) return c;
    return t;
  }

  function onXDown(e) {
    if (e.button !== 0 || xdrag) return;
    const b = e.target.closest('.oblk');
    if (!b) return;
    const o = overlayById(b.dataset.id);
    if (!o) return;
    xdrag = { id: o.id, row: e.currentTarget, x: e.clientX, y: e.clientY, pid: e.pointerId, live: false,
              mode: xMode(b, e.clientX), at0: o.at, end0: o.at + dur(o), t: null, block: b };
  }

  function onXMove(e) {
    const d = xdrag;
    if (!d) {                                   // the cursor says what a press would do
      const b = e.target.closest && e.target.closest('.oblk');
      if (b) b.style.cursor = xMode(b, e.clientX) === 'move' ? '' : 'ew-resize';
      return;
    }
    if (e.pointerId !== d.pid) return;
    if (!d.live) {
      if (Math.abs(e.clientX - d.x) <= XCLICK && Math.abs(e.clientY - d.y) <= XCLICK) return;
      d.live = true;
      try { d.row.setPointerCapture(d.pid); } catch (err) { /* not pointer-capable */ }
      selectOverlay(d.id);
      d.block = oblocks.get(d.id) || d.block;
      d.block.classList.add('dragging');
    }
    e.stopPropagation();
    const dt = (e.clientX - d.x) / state.zoom;
    const near = xSnaps(d.id);
    if (d.mode === 'move') {
      const len = d.end0 - d.at0;
      let at = Math.max(0, d.at0 + dt);
      const s1 = snapX(at, near);
      if (s1 !== at) at = s1;
      else { const s2 = snapX(at + len, near); if (s2 !== at + len) at = s2 - len; }
      d.t = Math.max(0, at);
      d.block.style.left = `${timeToX(d.t)}px`;
    } else if (d.mode === 'in') {
      d.t = clamp(snapX(Math.max(0, d.at0 + dt), near), 0, d.end0 - MIN_LEN);
      d.block.style.left = `${timeToX(d.t)}px`;
      d.block.style.width = `${Math.max(3, (d.end0 - d.t) * state.zoom)}px`;
    } else {
      d.t = Math.max(d.at0 + MIN_LEN, snapX(d.end0 + dt, near));
      d.block.style.width = `${Math.max(3, (d.t - d.at0) * state.zoom)}px`;
    }
  }

  function endXDrag(e, apply) {
    const d = xdrag;
    if (!d || (e && e.pointerId !== d.pid)) return;
    xdrag = null;
    if (!d.live) return;                        // a click: onTrackUp selects it
    if (e) { e.stopPropagation(); e.preventDefault(); }
    try { d.row.releasePointerCapture(d.pid); } catch (err) { /* already released */ }
    d.block.classList.remove('dragging');
    if (apply && d.t != null) {
      if (d.mode === 'move') moveOverlay(d.id, d.t);
      else trimOverlay(d.id, d.mode, d.t);
    }
    renderV2();
  }

  function selectOverlay(id) {
    state.osel = id && overlayById(id) ? id : null;
    if (state.osel) select([], { source: 'click' });
    renderV2();
    emit('select', { ids: [], anchor: null, overlay: state.osel, source: 'click' });
  }

  function fitZoom(viewW, tot) {
    return clamp((viewW - PAD - PAD_R) / Math.max(tot, 0.001), MIN_ZOOM, MAX_ZOOM);
  }

  function positionHead() {
    el.head.style.left = `${timeToX(state.playhead)}px`;
    paintTc();
  }

  /* The timeline's own playhead readout, top-left as in Premiere's Timeline panel. */
  function paintTc() {
    if (!el.tc) return;
    const s = tc(state.playhead);
    if (el.tc.textContent !== s) setText(el.tc, s);
  }
  /* A readout written on every played frame changes its text node's data in place: a new
   * text node is a child-list mutation inside #tl, and the timeline's `:has()` rules
   * re-match every <i> under it for each one (wave 13 CANVAS2, traced: ~1,700 restyled
   * ruler ticks and handles per second of play). */
  function setText(node, s) {
    const t = node.firstChild;
    if (t && t.nodeType === 3 && !t.nextSibling) t.data = s;
    else node.textContent = s;
  }

  /* ---- tools (Premiere's V C B N Y A) */
  function paintTool() {
    if (!el.root) return;
    const [key, glyph, what] = TOOLS[state.tool];
    for (const name of Object.keys(TOOLS)) el.root.classList.toggle(`tl-tool-${name}`, name === state.tool);
    el.root.dataset.tool = state.tool;
    if (el.toolBtn) {
      el.toolBtn.textContent = glyph;
      el.toolBtn.title = `${what} (${key}) — click for the tools`;
      el.toolBtn.setAttribute('aria-label', `${what.split(' — ')[0]} (${key})`);
    }
    if (el.toolMenu) {
      for (const b of el.toolMenu.querySelectorAll('button')) b.classList.toggle('on', b.dataset.tool === state.tool);
    }
  }

  function setTool(name) {
    if (!TOOLS[name]) return false;
    const changed = state.tool !== name;
    state.tool = name;
    paintTool();
    if (changed) emit('tool', { tool: name });
    return changed;
  }

  function buildSide(side) {
    el.tc = side.querySelector('.tl-tc');
    el.toolBtn = side.querySelector('.tl-toolbtn');
    el.toolMenu = side.querySelector('.tl-toolmenu');
    el.toolMenu.innerHTML = Object.entries(TOOLS).map(([name, [key, glyph, what]]) =>
      `<button type="button" data-tool="${name}" title="${what} (${key})">`
      + `<span class="g">${glyph}</span> ${what.split(' — ')[0]} <kbd>${key}</kbd></button>`).join('');
    el.toolBtn.addEventListener('click', () => { el.toolMenu.hidden = !el.toolMenu.hidden; });
    el.toolMenu.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-tool]');
      if (!b) return;
      setTool(b.dataset.tool);
      el.toolMenu.hidden = true;
    });
    document.addEventListener('pointerdown', (e) => {
      if (!el.toolMenu.hidden && !side.contains(e.target)) el.toolMenu.hidden = true;
    });
    el.tc.title = 'the playhead · HH:MM:SS:FF';
    paintTool();
    paintTc();
  }

  /* ---- markers and the sequence's in / out */
  /* They live in the client until the EDL carries them: the project's `markers` /
   * `in` / `out` (app.js's `P`) when the server sends them, else localStorage per
   * project and cut. Film times on the frame grid. */
  function markStoreKey() {
    let id = '';
    try {
      // eslint-disable-next-line no-undef
      if (typeof P !== 'undefined' && P) id = `${P.footage || P.title || ''}|${P.cut || 'main'}`;
    } catch (err) { id = ''; }
    return `roughcut.tl.marks:${id || location.pathname}`;
  }

  function loadMarks() {
    let v = null;
    try {
      // eslint-disable-next-line no-undef
      if (typeof P !== 'undefined' && P && Array.isArray(P.markers)
          && (P.markers.length || P.in != null || P.out != null)) {
        v = { markers: P.markers.map((m) => (typeof m === 'object' && m ? m.t : m)), in: P.in, out: P.out };
      }
    } catch (err) { v = null; }
    if (!v) {
      try { v = JSON.parse(localStorage.getItem(markStoreKey()) || 'null'); } catch (err) { v = null; }
    }
    v = v || {};
    state.markers = (Array.isArray(v.markers) ? v.markers : [])
      .map(Number).filter((t) => Number.isFinite(t) && t >= 0).sort((a, b) => a - b);
    state.seqIn = Number.isFinite(v.in) ? v.in : null;
    state.seqOut = Number.isFinite(v.out) ? v.out : null;
  }

  function storeMarks() {
    try {
      localStorage.setItem(markStoreKey(), JSON.stringify(
        { markers: state.markers, in: state.seqIn, out: state.seqOut }));
    } catch (err) { /* private mode: they last the session */ }
  }

  const emitMarks = () => emit('markers', { markers: [...state.markers], in: state.seqIn, out: state.seqOut });

  function saveMarks() {
    storeMarks();
    scheduleSeqSave();
    render();
    emitMarks();
  }

  /* Markers and the sequence In / Out are on the undo stack (redesign wave 12, AIGLUE): as
   * in Premiere, M, I and O made by hand are each one ⌘Z, labelled; inside a begin …
   * commit (an AI answer, a drag) they fold into that one entry. */
  function marksEdit(label, fn) {
    const own = begin(label);
    const out = fn();
    if (own) commit();
    return out;
  }

  const sameFrame = (a, b) => Math.abs(a - b) < 0.5 / fps();

  function addMarker(t) {
    return marksEdit('add marker', () => {
      const m = onFrame(t == null ? state.playhead : Number(t));
      if (!Number.isFinite(m) || state.markers.some((x) => sameFrame(x, m))) return null;
      state.markers = [...state.markers, m].sort((a, b) => a - b);
      saveMarks();
      return m;
    });
  }

  function removeMarker(t) {
    return marksEdit('clear marker', () => {
      const at = t == null ? state.playhead : Number(t);
      const keep = state.markers.filter((x) => !sameFrame(x, at));
      if (keep.length === state.markers.length) return false;
      state.markers = keep;
      saveMarks();
      return true;
    });
  }

  function clearMarkers() {
    return marksEdit('clear markers', () => {
      if (!state.markers.length) return false;
      state.markers = [];
      saveMarks();
      return true;
    });
  }

  function setMarkers(list) {
    marksEdit('markers', () => {
      state.markers = (Array.isArray(list) ? list : []).map((m) => onFrame(Number(m)))
        .filter((t) => Number.isFinite(t)).sort((a, b) => a - b);
      saveMarks();
    });
  }

  function nextMarker(t) {
    const at = t == null ? state.playhead : t;
    const m = state.markers.find((x) => x > at && !sameFrame(x, at));
    return m == null ? null : m;
  }

  function prevMarker(t) {
    const at = t == null ? state.playhead : t;
    const before = state.markers.filter((x) => x < at && !sameFrame(x, at));
    return before.length ? before[before.length - 1] : null;
  }

  function setSeqIn(t) {
    return marksEdit(t == null ? 'clear in' : 'mark in', () => {
      const v = t == null ? null : onFrame(Number(t));
      state.seqIn = v;
      if (v != null && state.seqOut != null && state.seqOut <= v) state.seqOut = null;
      saveMarks();
      return v;
    });
  }

  function setSeqOut(t) {
    return marksEdit(t == null ? 'clear out' : 'mark out', () => {
      const v = t == null ? null : onFrame(Number(t));
      state.seqOut = v;
      if (v != null && state.seqIn != null && state.seqIn >= v) state.seqIn = null;
      saveMarks();
      return v;
    });
  }

  /* No layout read here (wave 5, SAVE): it runs right after positionHead's write, so a
   * read of clientWidth / scrollLeft forced a layout on every playhead move. The width is
   * the observer's, the scroll is state.scrollX (kept by every write and the scroll
   * event), and the clamp is worked out from the canvas width the render wrote. */
  function keepInView(x, frac = 0.25) {
    const v = el.view;
    const w = viewWidth();
    if (!w) return;
    const at = state.scrollX || 0;
    if (x < at + 6 || x > at + w - 6) {
      const max = Math.max(0, (canvasW || x + w) - w);
      const next = Math.round(Math.min(max, Math.max(0, x - w * frac)));
      v.scrollLeft = next;
      state.scrollX = next;
    }
  }

  function paintSel() {
    for (const [id, b] of blocks) b.classList.toggle('sel', state.sel.has(id));
    for (const [id, b] of ablocks) b.classList.toggle('sel', state.sel.has(id));
  }

  function paintUndo() {
    const u = document.querySelector('#undo'), r = document.querySelector('#redo');
    const top = undoStack[undoStack.length - 1], next = redoStack[redoStack.length - 1];
    if (u) {
      u.disabled = !top;
      u.title = top ? `undo: ${top.label} (${keyLabel('⌘Z')})` : 'nothing to undo';
    }
    if (r) {
      r.disabled = !next;
      r.title = next ? `redo: ${next.label} (${keyLabel('⌘⇧Z')})` : 'nothing to redo';
    }
  }

  function render() {
    if (!hooks || !el.root) return;
    const list = segs();
    ensureIds();
    syncSel();
    const tot = total();
    // Every layout read of the render, here, before it writes a style (wave 5, SAVE):
    // a read after a write forces a layout, and one per A1 block was ~16 ms a render.
    const lane = el.lanes.V1;
    const vw = viewWidth();
    const at = el.view.scrollLeft;
    stripH = lane.clientHeight;
    // the labels' font, read here with the other reads (a style read, no layout)
    v1Font = fontOf(lane.querySelector('.blk:not(.gen) .name'), 'normal');
    if (autoFit && vw > 0 && list.length) state.zoom = fitZoom(vw, tot);
    const width = Math.max(vw, PAD + Math.max(tot, state.playhead || 0) * state.zoom + PAD_R);
    el.canvas.style.width = `${width}px`;
    canvasW = width;
    paintRuler(width);

    // Blocks are keyed by id and updated in place: a re-render after every shot advance
    // must not rebuild <img> elements, and a lane's own handles survive it.
    const live = hooks.live ? hooks.live() : -1;
    const seen = new Set();
    let start = 0;
    list.forEach((seg, i) => {
      let b = blocks.get(seg.id);
      if (!b) { b = makeBlock(seg.id); blocks.set(seg.id, b); }
      if (lane.children[i] !== b) lane.insertBefore(b, lane.children[i] || null);
      updateBlock(b, seg, i, start, live);
      seen.add(seg.id);
      start += dur(seg);
    });
    for (const [id, b] of blocks) {
      if (seen.has(id)) continue;
      b.remove(); blocks.delete(id); posterAt.delete(id);
    }
    pruneSel();
    ensureTrackRows();
    renderA1(list, live, at);
    renderV2();
    renderTransitions();
    layoutTracks();
    paintHeads();
    el.end.style.left = `${timeToX(tot)}px`;
    el.total.textContent = tc(tot);
    el.root.classList.toggle('tl-empty', !list.length);   // not `.empty`: the board's empty state owns that
    state.playhead = clamp(state.playhead, 0, tot + PAST_END_S);   // past the end stays (punch 8)
    positionHead();
    // the scroll as read above, clamped to the new width (no read after the writes)
    state.scrollX = Math.min(at, Math.max(0, width - vw));
    paintUndo();
  }

  /* ------------------------------------------------------------ selection */
  function sameSet(a, b) {
    if (a.size !== b.size) return false;
    for (const x of a) if (!b.has(x)) return false;
    return true;
  }

  function select(ids, opts = {}) {
    const list = segs();
    const wanted = (Array.isArray(ids) ? ids : ids == null ? [] : [ids])
      .map(resolveId).filter((id) => id != null && list.some((s) => s.id === id));
    const last = wanted[wanted.length - 1];
    let next, anchor = state.anchor;
    if (opts.range && anchor != null && indexOf(anchor) >= 0 && last != null) {
      next = new Set(opts.add ? state.sel : []);
      const a = indexOf(anchor), b = indexOf(last);
      for (let k = Math.min(a, b); k <= Math.max(a, b); k++) next.add(list[k].id);
    } else if (opts.add) {
      next = new Set(state.sel);
      for (const id of wanted) { if (next.has(id)) next.delete(id); else next.add(id); }
      if (last != null) anchor = next.has(last) ? last : anchor;
    } else {
      next = new Set(wanted);
      anchor = last != null ? last : null;
    }
    for (const id of [...next]) if (!list.some((s) => s.id === id)) next.delete(id);
    if (!next.size) anchor = null;
    else if (anchor == null || !next.has(anchor)) anchor = list.find((s) => next.has(s.id)).id;
    const changed = !sameSet(next, state.sel) || anchor !== state.anchor;
    state.sel = next;
    state.anchor = anchor;
    if (next.size && state.osel != null) { state.osel = null; renderV2(); }
    if (anchor != null) {
      const i = indexOf(anchor);
      lastAppSel = i;
      if (hooks.setSel) hooks.setSel(i);
    }
    paintSel();
    if (changed) emit('select', { ids: [...next], anchor, source: opts.source || 'api' });
    return changed;
  }

  /* app.js moved its index (j/k, a card click, playback advancing): follow it. While the
   * module itself is cueing the monitor, app.js's index moves are ours and not a selection. */
  function syncSel() {
    if (!hooks || !hooks.sel) return;
    const i = hooks.sel();
    if (i === lastAppSel) return;
    lastAppSel = i;
    if (inCue) return;
    const id = idAt(i);
    if (id != null) select([id], { source: 'app' });
  }

  /* After a render: drop ids that left the cut; an anchor that was removed hands the
   * selection to whatever app.js's index points at now (x leaves the index in place). */
  function pruneSel() {
    const list = segs();
    const present = new Set(list.map((s) => s.id));
    const kept = [...state.sel].filter((id) => present.has(id));
    const anchorGone = state.anchor != null && !present.has(state.anchor);
    if (kept.length === state.sel.size && !anchorGone) return;
    if (anchorGone && hooks.sel) {
      const id = idAt(hooks.sel());
      if (id != null && !kept.includes(id)) kept.push(id);
    }
    const next = new Set(kept);
    let anchor = anchorGone ? null : state.anchor;
    if (anchor == null && next.size) {
      const fallback = anchorGone && hooks.sel ? idAt(hooks.sel()) : null;
      anchor = fallback != null && next.has(fallback) ? fallback
        : list.find((s) => next.has(s.id)).id;
    }
    state.sel = next;
    state.anchor = anchor;
    if (anchor != null) { const i = indexOf(anchor); lastAppSel = i; if (hooks.setSel) hooks.setSel(i); }
    paintSel();
    emit('select', { ids: [...next], anchor, source: anchorGone ? 'heir' : 'api' });
  }

  /* ------------------------------------------------------------ playhead and view */
  /* The playhead sits anywhere on the ruler, past the film's end too, as Premiere's does
   * (verdict 5, punch 8: it was clamped to the end, so `,` / `.` there left no gap) —
   * up to PAST_END_S past the end, so a stray seek cannot make the canvas absurd. */
  const headAt = (t) => clamp(Number(t) || 0, 0, total() + PAST_END_S);
  /* The canvas runs to a playhead past the film's end, so it is drawn and scrolls there. */
  function reachHead(t) {
    const need = Math.ceil(timeToX(t) + PAD_R);
    if (need <= canvasW) return;
    canvasW = need;
    el.canvas.style.width = `${need}px`;
    paintRuler(need);
  }

  /* `opts.quiet` (wave 13 CANVAS2): the playhead's time and its event, without moving the
   * drawn playhead or its readout (the monitor's frames between two painted ones while it
   * plays, app.js paintPos); listeners that only paint read `ev.quiet` and skip too. */
  function setPlayhead(t, opts = {}) {
    t = headAt(t);
    state.playhead = t;
    if (!el.head) return;
    if (opts.quiet) { emit('playhead', { t, quiet: true }); return; }
    if (t > total()) reachHead(t);
    positionHead();
    if (opts.reveal !== false) keepInView(timeToX(t));
    emit('playhead', { t });
  }

  function seek(t) {
    t = headAt(t);
    setPlayhead(t);
    // past the film's end there is nothing to cue: the monitor's cue of the last frame
    // would put the playhead back at the end
    const at = t > total() + 1e-9 ? null : shotAt(t);
    if (at && hooks.cue) {
      inCue = true;
      try { hooks.cue(at.index, at.clipT); } finally { inCue = false; }
    }
    return at;
  }

  function zoomTo(z, opts = {}) {
    z = clamp(Number(z) || state.zoom, MIN_ZOOM, MAX_ZOOM);
    autoFit = false;
    const focusT = opts.at != null ? opts.at
      : state.anchor != null ? filmStart(state.anchor) : state.playhead;
    state.zoom = z;
    render();
    if (opts.at != null && opts.viewX != null) {
      el.view.scrollLeft = Math.max(0, timeToX(opts.at) - opts.viewX);
      state.scrollX = el.view.scrollLeft;
    } else {
      keepInView(timeToX(focusT), 0.3);
    }
    emit('zoom', { zoom: state.zoom });
    return state.zoom;
  }

  function fit() {
    autoFit = true;
    render();
    el.view.scrollLeft = 0;
    state.scrollX = 0;
    emit('zoom', { zoom: state.zoom });
    return state.zoom;
  }

  /* ------------------------------------------------------------ transitions
   * Premiere's transitions (verdict 6 punch 6; the Effects bin: verdict 8 punch 5; the
   * model and the maths are roughcut/tracks.py): `state.transitions` holds
   * `{a, b, kind, dur, align}` —
   *   centred on an edit ('center'): the two shots either side of it, each giving dur/2
   *     of media beyond its edge (its handle). A shot with less keeps the length and its
   *     last / first frame is REPEATED for the rest, as Premiere does: the box is hatched
   *     and the toast says "insufficient media — repeated frames". A dip needs no handle.
   *   one-sided, at a clip's start ('start', b only) or end ('end', a only): inside the
   *     shot, a fade from / to black (white for Dip to White) or a sound fade in / out.
   * Video kinds draw on V1, audio kinds on A1; one of each family per edge (a later one
   * on an edge replaces it). The duration is in film seconds on the grid (a centred one
   * an even number of frames, so each half is whole). They ride the undo snapshot and the
   * cut's own save (app.js saveBody, under the ids the server knows). A pair that stops
   * being neighbours (a move, a delete) loses its transition on the next render, inside
   * the same undo entry. Ctrl+D / Shift+D lay the DEFAULT video / audio transition
   * (Premiere's: Cross Dissolve and Constant Power until "Set Default Transition" on a
   * box's right-click or the Effects bin's says otherwise; kept per browser). */
  const VIDEO_KINDS = { cross_dissolve: 'Cross Dissolve', dip_to_black: 'Dip to Black',
                        dip_to_white: 'Dip to White', film_dissolve: 'Film Dissolve' };
  const AUDIO_KINDS = { audio_crossfade: 'Constant Power', constant_gain: 'Constant Gain',
                        exponential_fade: 'Exponential Fade' };
  const TRANS_NAMES = { ...VIDEO_KINDS, ...AUDIO_KINDS };
  const DIPS = new Set(['dip_to_black', 'dip_to_white']);
  const famOf = (kind) => (kind in VIDEO_KINDS ? 'video' : 'audio');
  const TRANS_DEFAULT_S = 1.0;      // Premiere's default duration
  const TRANS_EDGE_PX = 6;          // the box's ends: a drag there changes its length
  const TRANS_MIME = 'application/x-roughcut-transition';
  const TRANS_SNAP_PX = 14;         // a drop this close to an edit is centred on it
  const DEFAULTS_KEY = 'roughcut.tl.defaultTransition';
  const tblocks = new Map();        // key -> the box on V1 / A1
  let tdrag = null;
  const tkey = (t) => `${t.a}|${t.b}|${t.kind}`;
  const tlaneOf = (kind) => (famOf(kind) === 'video' ? el.lanes.V1 : el.tracks.A1);
  const ttrack = (kind) => (famOf(kind) === 'video' ? 'V1' : 'A1');
  const alignOf = (t) => t.align || (t.a && t.b ? 'center' : t.a ? 'end' : 'start');
  let transDefaults = { video: 'cross_dissolve', audio: 'audio_crossfade' };
  try {
    const v = JSON.parse(localStorage.getItem(DEFAULTS_KEY) || 'null');
    if (v && VIDEO_KINDS[v.video]) transDefaults.video = v.video;
    if (v && AUDIO_KINDS[v.audio]) transDefaults.audio = v.audio;
  } catch (err) { /* private mode: Premiere's defaults */ }

  /* Premiere's "Set Default Transition": the kind Ctrl+D (video) or Shift+D (audio) lays. */
  function setDefaultTransition(kind) {
    if (!TRANS_NAMES[kind]) return null;
    transDefaults = { ...transDefaults, [famOf(kind)]: kind };
    try { localStorage.setItem(DEFAULTS_KEY, JSON.stringify(transDefaults)); } catch (err) { /* fine */ }
    emit('transitiondefault', { ...transDefaults });
    toast(`default ${famOf(kind)} transition: ${TRANS_NAMES[kind]}`);
    return { ...transDefaults };
  }

  /* The cut index of a centred transition (between shots k and k+1), or -1 once it is not an edit. */
  function transCut(t) {
    if (alignOf(t) !== 'center') return -1;
    const k = indexOf(t.a);
    const list = segs();
    return k >= 0 && k + 1 < list.length && list[k + 1].id === resolveId(t.b) ? k : -1;
  }

  /* The shot index a one-sided transition sits in, or -1. */
  function transShot(t) {
    const al = alignOf(t);
    return al === 'start' ? indexOf(t.b) : al === 'end' ? indexOf(t.a) : -1;
  }

  const transValid = (t) => (alignOf(t) === 'center' ? transCut(t) >= 0 : transShot(t) >= 0);

  /* The centred transition of a family ('video' | 'audio', or a kind's) at cut k. */
  function transAt(k, fam) {
    const f = TRANS_NAMES[fam] ? famOf(fam) : fam;
    const list = segs();
    if (!list[k] || !list[k + 1]) return null;
    return state.transitions.find((t) => famOf(t.kind) === f && alignOf(t) === 'center'
      && resolveId(t.a) === list[k].id && resolveId(t.b) === list[k + 1].id) || null;
  }

  /* Whether shot `id`'s 'start' or 'end' edge holds a transition of the family. */
  function edgeHas(id, edge, fam, except) {
    const real = resolveId(id);
    return state.transitions.some((t) => t !== except && famOf(t.kind) === fam && transValid(t)
      && (edge === 'start' ? resolveId(t.b) === real && alignOf(t) !== 'end'
        : resolveId(t.a) === real && alignOf(t) !== 'start'));
  }

  /* The transitions a new one of `fam` on these edges replaces. */
  function onEdges(fam, edges) {
    return state.transitions.filter((t) => famOf(t.kind) === fam && transValid(t) && edges.some(([id, edge]) => {
      const real = resolveId(id);
      return edge === 'start' ? resolveId(t.b) === real && alignOf(t) !== 'end'
        : resolveId(t.a) === real && alignOf(t) !== 'start';
    }));
  }

  /* The longest half a centred transition of `fam` at cut k can have, in film seconds:
   * each shot's own length (half of it when its other edge holds one of the family too).
   * Media is not a limit (it is repeated): `media(k)` says how much there is. */
  function roomHalf(k, fam, except) {
    const list = segs();
    const a = list[k], b = list[k + 1];
    if (!a || !b) return 0;
    const roomA = dur(a) / (edgeHas(a.id, 'start', fam, except) ? 2 : 1);
    const roomB = dur(b) / (edgeHas(b.id, 'end', fam, except) ? 2 : 1);
    return Math.max(0, Math.min(roomA, roomB));
  }

  /* The handles at cut k in film seconds: the outgoing shot's media past its out, the
   * incoming's before its in. */
  function media(k) {
    const list = segs();
    const a = list[k], b = list[k + 1];
    if (!a || !b) return { tail: 0, head: 0 };
    const ca = clipOf(a.clip);
    const len = ca && Number(ca.duration) > 0 ? Number(ca.duration) : Infinity;
    return { tail: Math.max(0, (len - a.out) / speedOf(a)), head: Math.max(0, b.in / speedOf(b)) };
  }

  /* A centred duration on the grid: an even number of frames, so each half is whole. */
  function transFrames(d) {
    const r = gridRate();
    return Math.max(2, 2 * Math.round((Number(d) || 0) * r / 2));
  }

  /* Whether a centred transition of `kind` with half h repeats frames at cut k. */
  function repeats(k, kind, h) {
    if (DIPS.has(kind) || !(h > 0)) return false;
    const m = media(k);
    return h > m.tail + 1e-6 || h > m.head + 1e-6;
  }

  /* What the Program and the drawing use: per cut, `{video, audio}` half-durations in film
   * seconds (0 for none), `vkind` / `akind` the kinds, `short` kinds cut down to the shots,
   * `rep` kinds that repeat frames (insufficient media), `at` the edit's film second. */
  function transitionHalves() {
    const list = segs();
    const out = [];
    for (let k = 0; k + 1 < list.length; k++) {
      const c = { video: 0, audio: 0, vkind: null, akind: null, short: [], rep: [], at: 0 };
      for (const fam of ['video', 'audio']) {
        const t = transAt(k, fam);
        if (!t) continue;
        const want = Number(t.dur) / 2;
        const got = Math.min(want, roomHalf(k, fam, t));
        if (got < want - 1e-6) c.short.push(t.kind);
        const h = got >= 0.5 / gridRate() ? got : 0;
        c[fam] = h;
        c[fam === 'video' ? 'vkind' : 'akind'] = t.kind;
        if (repeats(k, t.kind, h)) c.rep.push(t.kind);
      }
      out.push(c);
    }
    let t = 0;
    out.forEach((c, k) => { t += dur(list[k]); c.at = t; });
    return out;
  }

  /* The one-sided transitions: `[{k, edge, fam, kind, dur, t0, t1}]` — shot k's first
   * (edge 'start') or last ('end') `dur` film seconds, [t0, t1) on the film. */
  function transitionFades() {
    const list = segs();
    const out = [];
    for (const t of state.transitions) {
      const k = transShot(t);
      if (k < 0) continue;
      const edge = alignOf(t);
      const fam = famOf(t.kind);
      const s = list[k];
      const room = dur(s) / (edgeHas(s.id, edge === 'start' ? 'end' : 'start', fam, t) ? 2 : 1);
      const d = Math.min(Number(t.dur) || 0, room);
      const t0 = edge === 'start' ? filmStart(s.id) : filmStart(s.id) + dur(s) - d;
      out.push({ k, edge, fam, kind: t.kind, dur: d, t0, t1: t0 + d });
    }
    return out;
  }

  /* Drop transitions whose shots are no longer neighbours (render calls this, inside
   * whatever entry changed the cut). */
  function pruneTransitions() {
    const keep = state.transitions.filter(transValid);
    if (keep.length !== state.transitions.length) state.transitions = keep;
    if (state.tsel && !state.transitions.some((t) => tkey(t) === state.tsel)) state.tsel = null;
  }

  function lockedFor(kind) {
    const trk = ttrack(kind);
    return trackState(trk).lock ? (refuseLocked(trk), true) : false;
  }

  /* `kind` centred at cut k (replacing the family's transitions on those two edges), 1 s
   * or `d`, cut down to the shots; a shot without the media repeats frames (`repeated`).
   * One undo entry unless inside a begin … commit. Returns it or null. */
  function addTransition(k, kind, d) {
    const list = segs();
    if (!TRANS_NAMES[kind] || !list[k] || !list[k + 1]) return null;
    if (lockedFor(kind)) return null;
    const fam = famOf(kind);
    const name = TRANS_NAMES[kind];
    const r = gridRate();
    let n = transFrames(d == null ? TRANS_DEFAULT_S : d);
    const gone = onEdges(fam, [[list[k].id, 'end'], [list[k + 1].id, 'start']]);
    const room = Math.floor(roomHalf(k, fam, gone[0]) * r + 1e-6);
    if (room < 1) {
      toast(`${blockName(list[k])} or ${blockName(list[k + 1])} is too short for a ${name}`);
      return null;
    }
    const asked = n;
    if (n / 2 > room) n = 2 * room;
    const t = { a: list[k].id, b: list[k + 1].id, kind, dur: r4(n / r), align: 'center' };
    const own = begin(name);
    state.transitions = state.transitions.filter((x) => !gone.includes(x));
    state.transitions.push(t);
    state.tsel = tkey(t);
    if (hooks.render) hooks.render(); else render();
    if (own) commit();
    const res = { ...t };
    if (n < asked) res.shortened = r4(asked / r);
    if (repeats(k, kind, n / 2 / r)) res.repeated = true;
    return res;
  }

  /* `kind` one-sided at shot i's 'start' or 'end' (a fade from / to black, a sound fade):
   * `d` (1 s) cut down to the shot. One undo entry. Returns it or null. */
  function addFade(i, edge, kind, d) {
    const list = segs();
    const s = list[i];
    if (!TRANS_NAMES[kind] || !s || !['start', 'end'].includes(edge)) return null;
    if (lockedFor(kind)) return null;
    const fam = famOf(kind);
    const r = gridRate();
    const gone = onEdges(fam, [[s.id, edge]]);
    const room = dur(s) / (edgeHas(s.id, edge === 'start' ? 'end' : 'start', fam) ? 2 : 1);
    const n = Math.max(1, Math.min(Math.round((d == null ? TRANS_DEFAULT_S : Number(d)) * r),
      Math.floor(room * r + 1e-6)));
    const t = edge === 'start' ? { a: null, b: s.id, kind, dur: r4(n / r), align: 'start' }
      : { a: s.id, b: null, kind, dur: r4(n / r), align: 'end' };
    const own = begin(TRANS_NAMES[kind]);
    state.transitions = state.transitions.filter((x) => !gone.includes(x));
    state.transitions.push(t);
    state.tsel = tkey(t);
    if (hooks.render) hooks.render(); else render();
    if (own) commit();
    return { ...t };
  }

  /* The toast for transitions just laid: what, how long, where — and Premiere's warning. */
  function sayLaid(made, where) {
    if (!made.length) return;
    const short = made.find((t) => t.shortened);
    const rep = made.some((t) => t.repeated);
    toast(`${made.map((t) => TRANS_NAMES[t.kind]).join(' + ')} · ${made[0].dur.toFixed(2)} s ${where}`
      + (short ? ` — cut to the shots from ${short.shortened.toFixed(2)} s` : '')
      + (rep ? ' — insufficient media — repeated frames' : ''));
  }

  function removeTransition(key) {
    const t = state.transitions.find((x) => tkey(x) === key);
    if (!t) return false;
    if (lockedFor(t.kind)) return false;
    const own = begin(`delete ${TRANS_NAMES[t.kind]}`);
    state.transitions = state.transitions.filter((x) => x !== t);
    if (state.tsel === key) state.tsel = null;
    if (hooks.render) hooks.render(); else render();
    if (own) commit();
    return true;
  }

  /* A new duration (film seconds), on the grid and within the shots. One undo entry
   * unless inside a gesture's begin … commit (the edge drag). Returns the duration. */
  function setTransitionDur(key, d) {
    const t = state.transitions.find((x) => tkey(x) === key);
    if (!t || !transValid(t)) return null;
    const r = gridRate();
    const fam = famOf(t.kind);
    let n;
    if (alignOf(t) === 'center') {
      const room = Math.max(1, Math.floor(roomHalf(transCut(t), fam, t) * r + 1e-6));
      n = Math.min(transFrames(d), 2 * room);
    } else {
      const s = segs()[transShot(t)];
      const other = alignOf(t) === 'start' ? 'end' : 'start';
      const room = dur(s) / (edgeHas(s.id, other, fam, t) ? 2 : 1);
      n = Math.max(1, Math.min(Math.round((Number(d) || 0) * r), Math.floor(room * r + 1e-6)));
    }
    const v = r4(n / r);
    if (Math.abs(v - t.dur) < 1e-6) return v;
    const own = begin(`${TRANS_NAMES[t.kind]} duration`);
    t.dur = v;
    if (hooks.render) hooks.render(); else render();
    if (own) commit();
    return v;
  }

  function selectTransition(key) {
    state.tsel = key && state.transitions.some((t) => tkey(t) === key) ? key : null;
    if (state.tsel) state.edit = null;
    paintTransitions();
    emit('select', { ids: [...state.sel], anchor: state.anchor, transition: state.tsel, source: 'click' });
  }

  /* The selected edit point (a click on an edit's column, below) or the transition's. */
  function selectedEdit() {
    if (state.tsel) {
      const t = state.transitions.find((x) => tkey(x) === state.tsel);
      const k = t ? transCut(t) : -1;
      if (k >= 0) return k;
    }
    if (state.edit) {
      const k = indexOf(state.edit.a);
      const list = segs();
      if (k >= 0 && list[k + 1] && list[k + 1].id === resolveId(state.edit.b)) return k;
    }
    return -1;
  }

  /* The edit Ctrl+D acts on: the selected one, else the one nearest the playhead. */
  function editForTransition() {
    const sel = selectedEdit();
    if (sel >= 0) return sel;
    const list = segs();
    let best = -1, bd = Infinity, t = 0;
    for (let k = 0; k + 1 < list.length; k++) {
      t += dur(list[k]);
      const d = Math.abs(t - (state.playhead || 0));
      if (d < bd) { bd = d; best = k; }
    }
    return best;
  }

  /* Ctrl+D (the default video transition), Shift+D (audio), Ctrl+Shift+D (both): one undo entry. */
  function applyTransition(which) {
    const k = editForTransition();
    if (k < 0) { toast('no edit to put a transition on — the sequence needs two shots'); return []; }
    const kinds = which === 'both' ? [transDefaults.video, transDefaults.audio]
      : [which === 'audio' ? transDefaults.audio : transDefaults.video];
    const own = begin(kinds.length > 1 ? 'transitions' : TRANS_NAMES[kinds[0]]);
    const made = kinds.map((kind) => addTransition(k, kind)).filter(Boolean);
    if (own) commit();
    if (made.length) {
      state.tsel = tkey(made[0]);
      paintTransitions();
      sayLaid(made, 'at the edit');
    }
    return made;
  }

  function transitionsForSave() {
    return state.transitions.map((t) => ({ ...t, a: t.a == null ? null : idFor(resolveId(t.a)),
                                           b: t.b == null ? null : idFor(resolveId(t.b)) }));
  }

  function makeTBlock(key) {
    const b = document.createElement('div');
    b.className = 'tl-trans';
    b.dataset.key = key;
    b.innerHTML = '<i class="te l"></i><span class="lbl"></span><i class="te r"></i>';
    b.addEventListener('pointerdown', onTransDown);
    b.addEventListener('dblclick', onTransDbl);
    return b;
  }

  /* The boxes: V1's for a video kind, A1's for an audio kind; a centred one on its cut, a
   * one-sided one inside its shot against the edge; hatched where frames repeat. */
  function renderTransitions() {
    pruneTransitions();
    const halves = transitionHalves();
    const fades = transitionFades();
    const seen = new Set();
    for (const t of state.transitions) {
      const lane = tlaneOf(t.kind);
      if (!lane || !transValid(t)) continue;
      const key = tkey(t);
      let b = tblocks.get(key);
      if (!b) { b = makeTBlock(key); tblocks.set(key, b); }
      if (b.parentNode !== lane) lane.appendChild(b);
      const name = TRANS_NAMES[t.kind];
      const al = alignOf(t);
      let left, w, d, short = false, rep = false, where;
      if (al === 'center') {
        const k = transCut(t);
        const c = halves[k];
        const h = famOf(t.kind) === 'video' ? c.video : c.audio;
        short = c.short.includes(t.kind);
        rep = c.rep.includes(t.kind);
        w = Math.max(6, 2 * (h || Number(t.dur) / 2) * state.zoom);
        left = timeToX(c.at) - w / 2;
        d = 2 * h;
        where = 'centred on the cut';
      } else {
        const f = fades.find((x) => x.k === transShot(t) && x.edge === al && x.fam === famOf(t.kind)) || { t0: 0, dur: 0 };
        w = Math.max(6, f.dur * state.zoom);
        left = timeToX(f.t0);
        d = f.dur;
        where = al === 'start' ? `at the clip's start (from ${t.kind === 'dip_to_white' ? 'white' : 'black'})`
          : `at the clip's end (to ${t.kind === 'dip_to_white' ? 'white' : 'black'})`;
        if (famOf(t.kind) === 'audio') where = al === 'start' ? 'fade in at the clip\'s start' : 'fade out at the clip\'s end';
      }
      b.style.left = `${left}px`;
      b.style.width = `${w}px`;
      b.dataset.kind = t.kind;
      b.dataset.align = al;
      b.classList.toggle('audio', famOf(t.kind) === 'audio');
      b.classList.toggle('side', al !== 'center');
      b.classList.toggle('short', short);
      b.classList.toggle('rep', rep);
      b.classList.toggle('narrow', w < 70);
      b.dataset.dur = String(r4(d));
      const lbl = b.querySelector('.lbl');
      if (lbl.textContent !== name) lbl.textContent = name;
      b.title = `${name} · ${d.toFixed(2)} s, ${where}`
        + (short ? ` — cut to the shots from ${Number(t.dur).toFixed(2)} s` : '')
        + (rep ? ' — insufficient media — repeated frames' : '')
        + ' · drag an end to change its length, double-click to type it, Delete removes it,'
        + ' right-click to make it the default';
      seen.add(key);
    }
    for (const [key, b] of tblocks) {
      if (seen.has(key)) continue;
      b.remove();
      tblocks.delete(key);
    }
    paintTransitions();
  }

  function paintTransitions() {
    for (const [key, b] of tblocks) b.classList.toggle('sel', key === state.tsel);
    const k = selectedEdit();
    let mark = el.editMark;
    if (!mark && el.lanes.V1) {
      mark = el.editMark = document.createElement('i');
      mark.className = 'tl-editsel';
    }
    if (!mark) return;
    if (k < 0 || state.tsel) { mark.hidden = true; return; }
    if (mark.parentNode !== el.lanes.V1) el.lanes.V1.appendChild(mark);
    mark.hidden = false;
    mark.style.left = `${timeToX(filmStart(segs()[k + 1].id))}px`;
  }

  /* A press on a box: an end drags its length (a centred one's both ends move: it stays
   * centred; a one-sided one's inner end moves), the body selects it. Never a click on
   * the lane under it. */
  function onTransDown(e) {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    const b = e.currentTarget;
    const key = b.dataset.key;
    const t = state.transitions.find((x) => tkey(x) === key);
    if (!t) return;
    selectTransition(key);
    const r = b.getBoundingClientRect();
    const end = e.clientX - r.left < TRANS_EDGE_PX || r.right - e.clientX < TRANS_EDGE_PX;
    if (!end) return;
    const al = alignOf(t);
    let cut;
    if (al === 'center') cut = filmStart(segs()[transCut(t) + 1].id);
    else {
      const s = segs()[transShot(t)];
      cut = al === 'start' ? filmStart(s.id) : filmStart(s.id) + dur(s);
    }
    tdrag = { key, pointerId: e.pointerId, cut, side: al !== 'center', b, began: false };
    try { b.setPointerCapture(e.pointerId); } catch (err) { /* not pointer-capable */ }
    b.addEventListener('pointermove', onTransMove);
    b.addEventListener('pointerup', onTransUp);
    b.addEventListener('pointercancel', onTransUp);
  }

  function onTransMove(e) {
    if (!tdrag || e.pointerId !== tdrag.pointerId) return;
    const half = Math.abs(eventTime(e) - tdrag.cut);
    if (!tdrag.began) { tdrag.began = begin('transition duration'); }
    setTransitionDur(tdrag.key, tdrag.side ? half : 2 * half);
  }

  function onTransUp(e) {
    if (!tdrag || e.pointerId !== tdrag.pointerId) return;
    const d = tdrag;
    tdrag = null;
    d.b.removeEventListener('pointermove', onTransMove);
    d.b.removeEventListener('pointerup', onTransUp);
    d.b.removeEventListener('pointercancel', onTransUp);
    try { d.b.releasePointerCapture(d.pointerId); } catch (err) { /* released */ }
    if (d.began) commit();
  }

  /* Double-click: type the duration (seconds, or frames as `12f`), as Premiere's Set
   * Transition Duration. Enter sets it, Esc leaves it. */
  function onTransDbl(e) {
    e.stopPropagation();
    e.preventDefault();
    editTransitionDur(e.currentTarget.dataset.key);
  }

  function editTransitionDur(key) {
    const b = tblocks.get(key);
    const t = state.transitions.find((x) => tkey(x) === key);
    if (!b || !t) return null;
    let inp = b.querySelector('input');
    if (inp) { inp.focus(); return inp; }
    inp = document.createElement('input');
    inp.className = 'tl-trans-dur';
    inp.value = Number(b.dataset.dur || t.dur).toFixed(2);
    inp.title = 'duration in seconds (or frames: 12f) — Enter sets it, Esc leaves it';
    b.appendChild(inp);
    inp.addEventListener('pointerdown', (ev) => ev.stopPropagation());
    let done = false;
    const close = () => { if (done) return; done = true; if (inp.isConnected) inp.remove(); };
    inp.addEventListener('keydown', (ev) => {
      ev.stopPropagation();
      if (ev.key === 'Escape') { close(); return; }
      if (ev.key !== 'Enter') return;
      const raw = inp.value.trim();
      const m = /^(\d+(?:\.\d+)?)\s*(f)?$/i.exec(raw);
      close();
      if (!m) { toast(`not a duration: ${raw}`); return; }
      const v = m[2] ? Number(m[1]) / gridRate() : Number(m[1]);
      setTransitionDur(key, v);
    });
    inp.addEventListener('blur', close);
    inp.focus();
    inp.select();
    return inp;
  }

  /* A click on an edit's column selects that edit point (Ctrl+D's target), as Premiere's
   * selection tool does; the trim lane's own press still trims it. Capture: before the
   * trim lane stops it. */
  function onEditPress(e) {
    const col = e.target && e.target.closest ? e.target.closest('.tl-edge') : null;
    if (!col) return;
    const x = col.getBoundingClientRect();
    const t = xToTime(x.left + x.width / 2 - el.view.getBoundingClientRect().left + el.view.scrollLeft);
    const list = segs();
    let best = -1, bd = Infinity, acc = 0;
    for (let k = 0; k + 1 < list.length; k++) {
      acc += dur(list[k]);
      const d = Math.abs(acc - t);
      if (d < bd) { bd = d; best = k; }
    }
    if (best < 0 || bd * state.zoom > 12) return;
    state.edit = { a: list[best].id, b: list[best + 1].id };
    state.tsel = null;
    setTimeout(paintTransitions, 0);
  }

  /* Where a transition dropped at film second t lands (the Effects bin, verdict 8 punch
   * 5): within TRANS_SNAP_PX of an edit, centred on it; else inside a shot, one-sided at
   * its nearer end. `{cut: k}` or `{shot: i, edge}`, or null past the film. */
  function transTarget(t) {
    const list = segs();
    if (!list.length || t < 0) return null;
    let acc = 0, best = -1, bd = Infinity;
    for (let k = 0; k + 1 < list.length; k++) {
      acc += dur(list[k]);
      const d = Math.abs(acc - t);
      if (d < bd) { bd = d; best = k; }
    }
    if (best >= 0 && bd * state.zoom <= TRANS_SNAP_PX) return { cut: best };
    const hit = shotAt(t);
    if (!hit || t > total() + 1e-6) return null;
    const s = list[hit.index];
    const t0 = filmStart(s.id);
    return { shot: hit.index, edge: t - t0 < t0 + dur(s) - t ? 'start' : 'end' };
  }

  /* Lay `kind` where `target` (transTarget) says: one undo entry, and the toast. */
  function dropTransition(kind, target) {
    if (!TRANS_NAMES[kind] || !target) return null;
    let made = null;
    if (target.cut != null) {
      made = addTransition(target.cut, kind);
      if (made) sayLaid([made], 'at the edit');
    } else {
      made = addFade(target.shot, target.edge, kind);
      if (made) sayLaid([made], `at the clip's ${target.edge}`);
    }
    return made;
  }

  const carriesTrans = (e) => !!(e.dataTransfer && e.dataTransfer.types
    && Array.prototype.includes.call(e.dataTransfer.types, TRANS_MIME));

  function transDropMark() {
    let m = el.transDrop;
    if (!m) {
      m = el.transDrop = document.createElement('i');
      m.className = 'tl-tdrop';
    }
    return m;
  }

  function hideTransDrop() { if (el.transDrop) el.transDrop.hidden = true; }

  /* The drop's preview: where the 1 s box would sit (V1; Chromium hides the payload's
   * kind until the drop, so the bin names it on window.rcDragTransition). */
  function onTransDragOver(e) {
    if (!carriesTrans(e)) return;
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = 'copy';
    const target = transTarget(eventTime(e));
    const m = transDropMark();
    const kind = window.rcDragTransition || 'cross_dissolve';
    const lane = tlaneOf(kind);
    if (!target || !lane) { m.hidden = true; return; }
    if (m.parentNode !== lane) lane.appendChild(m);
    const list = segs();
    let t0, t1;
    if (target.cut != null) {
      const c = filmStart(list[target.cut + 1].id);
      const h = Math.min(TRANS_DEFAULT_S / 2, roomHalf(target.cut, famOf(kind)));
      t0 = c - h; t1 = c + h;
    } else {
      const s = list[target.shot];
      const a = filmStart(s.id), d = Math.min(TRANS_DEFAULT_S, dur(s));
      [t0, t1] = target.edge === 'start' ? [a, a + d] : [a + dur(s) - d, a + dur(s)];
    }
    m.hidden = false;
    m.style.left = `${timeToX(t0)}px`;
    m.style.width = `${Math.max(4, (t1 - t0) * state.zoom)}px`;
  }

  function onTransDrop(e) {
    if (!carriesTrans(e)) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    hideTransDrop();
    let kind = null;
    try { kind = JSON.parse(e.dataTransfer.getData(TRANS_MIME)).kind; } catch (err) { kind = null; }
    dropTransition(kind, transTarget(eventTime(e)));
  }

  /* Right-click on a transition: Premiere's "Set Default Transition" (and Clear). */
  function closeTransMenu() { if (el.transMenu) el.transMenu.hidden = true; }

  function onTransContext(e) {
    const b = e.target && e.target.closest ? e.target.closest('.tl-trans') : null;
    if (!b) return;
    const t = state.transitions.find((x) => tkey(x) === b.dataset.key);
    if (!t) return;
    e.preventDefault();
    e.stopPropagation();
    selectTransition(tkey(t));
    let m = el.transMenu;
    if (!m) {
      m = el.transMenu = document.createElement('div');
      m.className = 'tl-ctx';
      m.setAttribute('role', 'menu');
      document.body.appendChild(m);
      m.addEventListener('click', (ev) => {
        const it = ev.target.closest('button[data-act]');
        if (!it) return;
        closeTransMenu();
        if (it.dataset.act === 'default') setDefaultTransition(it.dataset.kind);
        else if (it.dataset.act === 'clear') removeTransition(it.dataset.key);
      });
      document.addEventListener('pointerdown', (ev) => { if (!m.contains(ev.target)) closeTransMenu(); }, true);
      document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') closeTransMenu(); }, true);
    }
    const isDef = transDefaults[famOf(t.kind)] === t.kind;
    m.innerHTML = `<button type="button" role="menuitem" data-act="default" data-kind="${t.kind}"${isDef ? ' disabled' : ''}>`
      + `Set Default Transition</button>`
      + `<button type="button" role="menuitem" data-act="clear" data-key="${tkey(t).replace(/"/g, '&quot;')}">Clear</button>`;
    m.hidden = false;
    m.style.left = `${Math.min(e.clientX, window.innerWidth - 200)}px`;
    m.style.top = `${Math.min(e.clientY, window.innerHeight - 70)}px`;
  }

  /* ------------------------------------------------------------ undo / redo */
  /* An entry is the cut — and, when the board hands one over (`hooks.extra()` /
   * `hooks.setExtra(v)`), a second piece of state that rides the same stack: the film's
   * colour block, so a warmer / cooler nudge is one ⌘Z (INTAKE M16 I16.4). */
  /* V2's clips ride the same entry: laying, moving or removing one is one ⌘Z. The
   * track headers' toggles do not — as in Premiere, a mute or a lock is not an edit. */
  /* The added tracks' names ride it too (wave 6, KEYS2): Add / Delete Track is one ⌘Z. */
  /* The transitions ride it too (verdict 6, punch 6): Ctrl+D is one ⌘Z. */
  /* So do the markers and the sequence In / Out (wave 12, AIGLUE): M, I, O are each one ⌘Z,
   * and an AI answer that adds a marker or sets In / Out is one ⌘Z with its other edits. */
  const marksOf = () => ({ m: state.markers, i: state.seqIn, o: state.seqOut });
  const snapshot = () => JSON.stringify(hooks && hooks.extra
    ? { segs: segs(), extra: hooks.extra(), overlays: state.overlays, audio: state.audio,
        xtracks: trackNames().filter(isExtra), transitions: state.transitions, marks: marksOf() }
    : { segs: segs(), overlays: state.overlays, audio: state.audio, xtracks: trackNames().filter(isExtra),
        transitions: state.transitions, marks: marksOf() });

  function restore(json) {
    const v = JSON.parse(json);
    let marksMoved = false;
    if (v && !Array.isArray(v) && Array.isArray(v.segs)) {
      hooks.setSegs(v.segs);
      if (hooks.setExtra && 'extra' in v) hooks.setExtra(v.extra);
      if (Array.isArray(v.overlays)) state.overlays = v.overlays;
      if (Array.isArray(v.audio)) state.audio = v.audio;
      if (Array.isArray(v.xtracks)) setExtraTracks(v.xtracks);
      state.transitions = Array.isArray(v.transitions) ? v.transitions.map((t) => ({ ...t })) : [];
      if (v.marks && typeof v.marks === 'object') {
        const was = JSON.stringify(marksOf());
        state.markers = Array.isArray(v.marks.m) ? v.marks.m.slice() : [];
        state.seqIn = Number.isFinite(v.marks.i) ? v.marks.i : null;
        state.seqOut = Number.isFinite(v.marks.o) ? v.marks.o : null;
        marksMoved = JSON.stringify(marksOf()) !== was;
      }
    } else {
      hooks.setSegs(v);
    }
    ensureIds();
    scheduleSeqSave();
    if (marksMoved) { storeMarks(); emitMarks(); }
  }

  /* `pending` is the open entry. Opened by tl.batch it carries `levels`: the snapshots of
   * the begins nested inside it, innermost last (each one's cancel goes back to it); a
   * `record: false` batch is `quiet`: nothing it changes is touched for the autosave or
   * the tracks' save (the caller puts it back itself). */
  function previewing() { return !!(pending && pending.quiet); }
  function begin(label) {
    if (pending) {
      if (!pending.levels) return false;       // a gesture's second begin: a no-op, as always
      pending.levels.push(snapshot());         // inside a batch: a level of its entry
      return true;
    }
    pending = { label: label || 'edit', before: snapshot() };
    return true;
  }

  /* Close the open entry: pushed when the cut changed. */
  function closeEntry() {
    const p = pending;
    pending = null;
    const after = snapshot();
    if (after === p.before) return false;
    undoStack.push({ label: p.label, before: p.before, after });
    if (undoStack.length > UNDO_LIMIT) undoStack.shift();
    redoStack.length = 0;
    paintUndo();
    if (hooks.touch) hooks.touch();
    emit('change', { label: p.label, kind: 'commit' });
    return true;
  }

  function commit() {
    if (!pending) return false;
    if (pending.levels) {                      // inside a batch: the level folds into it
      if (!pending.levels.length) return false;
      pending.levels.pop();
      return true;
    }
    return closeEntry();
  }

  /* Back to `before`, painted; the autosave touched when `touch` (a level inside a batch
   * leaves that to the batch's own closing). */
  function putBack(before, touch) {
    if (snapshot() === before) return;
    restore(before);
    if (hooks.render) hooks.render();
    if (touch && hooks.touch) hooks.touch();
  }

  function cancel() {
    if (!pending) return false;
    if (pending.levels) {                      // inside a batch: that level's changes only
      if (!pending.levels.length) return false;
      putBack(pending.levels.pop(), false);
      return true;
    }
    const p = pending;
    pending = null;
    putBack(p.before, true);
    return true;
  }

  function batch(label, fn, opts = {}) {
    if (pending && !pending.levels) return { ok: false, value: undefined, committed: false };
    if (pending) {                             // a batch inside a batch: a level of it
      const mine = pending;
      mine.levels.push(snapshot());
      const depth = mine.levels.length;
      try {
        const value = fn();
        if (pending === mine && mine.levels.length >= depth) mine.levels.length = depth - 1;
        return { ok: true, value, committed: false };
      } catch (err) {
        if (pending === mine && mine.levels.length >= depth) {
          const before = mine.levels[depth - 1];
          mine.levels.length = depth - 1;
          putBack(before, false);
        }
        throw err;
      }
    }
    const mine = { label: label || 'edit', before: snapshot(), levels: [], quiet: !!(opts && opts.record === false) };
    pending = mine;
    let value;
    try {
      value = fn();
    } catch (err) {
      if (pending === mine) { pending = null; putBack(mine.before, !mine.quiet); }
      throw err;
    }
    // an undo or a clearHistory inside fn took the entry: nothing of it is left to close
    if (pending !== mine) return { ok: true, value, committed: false };
    mine.levels.length = 0;                    // a begin fn left open folds in too
    if (mine.quiet) { pending = null; return { ok: true, value, committed: false }; }
    return { ok: true, value, committed: closeEntry() };
  }

  /* After an undo or a redo the film can end before the playhead (an insert at the end,
   * undone): a playhead that was on the film comes back to its new end and the monitor is
   * cued on the cut as it is now, so the Program never keeps showing a shot that is gone
   * (verdict 9, punch 10). One the editor had put past the old end stays where it is. */
  function headBack(was) {
    const tot = total(), at = state.playhead;
    if (!(at > tot + 1e-6) || at > was + 1e-6) return;
    seek(tot);
  }

  function undo() {
    const e = undoStack.pop();
    if (!e) { toast('nothing to undo'); return false; }
    pending = null;
    const was = total();
    restore(e.before);
    redoStack.push(e);
    if (hooks.render) hooks.render();
    headBack(was);
    if (hooks.touch) hooks.touch();     // undoing is an edit too, and must reach the disk
    paintUndo();
    emit('change', { label: e.label, kind: 'undo' });
    toast(`undone: ${e.label}`);
    return true;
  }

  function redo() {
    const e = redoStack.pop();
    if (!e) { toast('nothing to redo'); return false; }
    pending = null;
    const was = total();
    restore(e.after);
    undoStack.push(e);
    if (hooks.render) hooks.render();
    headBack(was);
    if (hooks.touch) hooks.touch();
    paintUndo();
    emit('change', { label: e.label, kind: 'redo' });
    toast(`redone: ${e.label}`);
    return true;
  }

  /* The cut was replaced from outside the stack (window.roughcutRefresh after an effect's
   * edit was accepted on the server): every entry's snapshot is of a cut that no longer
   * exists, and restoring one would wipe the change — the page reload this replaced
   * emptied the stacks the same way (INTAKE M16, C5). */
  function clearHistory() {
    pending = null;
    undoStack.length = 0;
    redoStack.length = 0;
    paintUndo();
  }

  /* ------------------------------------------------------------ mutations */
  function mutate(kind, fn) {
    if (cutLocked()) return refuseLocked(trackState('V1').lock ? 'V1' : 'A1');
    const own = begin(kind);
    const result = fn();
    ensureIds();
    if (state.anchor != null) {
      const i = indexOf(state.anchor);
      if (i >= 0) { lastAppSel = i; if (hooks.setSel) hooks.setSel(i); }
    }
    if (hooks.render) hooks.render(); else render();
    if (hooks.touch && !previewing()) hooks.touch();
    if (own) commit();
    emit('change', { label: kind, kind, ids: [...state.sel] });
    return result;
  }

  function setRange(id, tin, tout) {
    const seg = byId(id);
    if (!seg) return false;
    const dur = (clipOf(seg.clip) || {}).duration ?? 1e9;
    let a = tin == null ? seg.in : Number(tin);
    let b = tout == null ? seg.out : Number(tout);
    if (!Number.isFinite(a) || !Number.isFinite(b)) return false;
    a = clamp(a, 0, Math.max(0, dur - MIN_LEN));
    b = clamp(b, a + MIN_LEN, dur);
    // on the sequence's frames (gridRange): an in-trim keeps the out exactly, an
    // out-trim the in, and the shot's length in the film is whole frames
    const keep = tout == null ? 'out' : tin == null ? 'in'
      : a === seg.in ? 'in' : b === seg.out ? 'out' : null;
    [a, b] = gridRange(a, b, speedOf(seg), keep, dur);
    if (a === seg.in && b === seg.out) return false;
    return mutate('trim', () => {
      const inMoved = a !== seg.in;
      anchorRange(seg, baseOf(seg), a, b);     // the keys stay on the media (KEYANCHOR)
      seg.in = a; seg.out = b;
      if (inMoved) schedulePosters();
      return true;
    });
  }

  function move(ids, beforeId) {
    const list = segs();
    const want = new Set((Array.isArray(ids) ? ids : [ids]).map(resolveId));
    const moving = list.filter((s) => want.has(s.id));
    if (!moving.length) return false;
    const rest = list.filter((s) => !want.has(s.id));
    const target = beforeId == null ? null : resolveId(beforeId);
    let at = target == null ? rest.length : rest.findIndex((s) => s.id === target);
    if (at < 0) at = rest.length;
    const next = [...rest.slice(0, at), ...moving, ...rest.slice(at)];
    if (next.every((s, i) => s === list[i])) return false;
    return mutate('move', () => { list.splice(0, list.length, ...next); return true; });
  }

  function split(id, atFilmTime) {
    const i = indexOf(id);
    if (i < 0) return null;
    const list = segs();
    const seg = list[i];
    // a film time inside the shot is a clip time at the shot's rate; the two halves keep it
    // on a frame of the film: whole frames into the shot, at its rate (onGrid)
    const clipT = splitPoint(seg, Number(atFilmTime) - filmStart(seg.id));
    if (!(clipT >= seg.in + MIN_LEN - 1e-9 && clipT <= seg.out - MIN_LEN + 1e-9)) return null;
    return mutate('split', () => {
      const second = razorHalf(seg, clipT);    // both halves' keys cut at the razor
      seg.out = clipT;
      list.splice(i + 1, 0, second);
      return second.id;
    });
  }

  function remove(ids) {
    const list = segs();
    const want = new Set((Array.isArray(ids) ? ids : [ids]).map(resolveId));
    const gone = list.filter((s) => want.has(s.id));
    if (!gone.length) return false;
    const first = list.indexOf(gone[0]);
    return mutate('remove', () => {
      const kept = list.filter((s) => !want.has(s.id));
      list.splice(0, list.length, ...kept);
      const heir = kept[Math.min(first, kept.length - 1)];
      // 'heir': the board hands the selection on — nobody chose it, so ⌫ won't take it
      // (I16.0 n), whoever called remove (the X key, the inspector's remove)
      select(heir ? [heir.id] : [], { source: 'heir' });
      return true;
    });
  }

  function insert(seg, afterId) {
    if (!seg || !seg.clip) return null;
    const list = segs();
    const s = gridded(seg);
    if (!s.id) s.id = `tmp-${++tmpN}`;
    const after = afterId == null ? -1 : indexOf(afterId);
    const at = afterId == null ? list.length : after < 0 ? list.length : after + 1;
    return mutate('insert', () => {
      list.splice(at, 0, s);
      select([s.id], { source: 'api' });
      return s.id;
    });
  }

  /* Premiere's insert and overwrite (REDESIGN-GOAL dimension 5, `,` and `.`). Inside a
   * mutation only: make a cut point at film time t and return the index a shot placed
   * there goes at. The shot under t is split when both halves keep MIN_LEN; when one
   * would not, `mode` says which side is expendable — 'in' (an overwrite's start: the
   * tail is about to be covered, so the shot's out comes back to t), 'out' (an
   * overwrite's end: the head is covered, the in moves up to t) — else ('insert') the
   * nearer of the shot's two cut points. */
  function cutAt(t, mode) {
    const list = segs();
    let start = 0;
    for (let i = 0; i < list.length; i++) {
      const s = list[i], d = dur(s);
      if (t <= start + 1e-6) return i;
      if (t < start + d - 1e-6) {
        const clipT = splitPoint(s, t - start);
        const head = clipT - s.in, tail = s.out - clipT;
        if (head >= MIN_LEN - 1e-9 && tail >= MIN_LEN - 1e-9) {
          list.splice(i + 1, 0, razorHalf(s, clipT));
          s.out = clipT;
          return i + 1;
        }
        if (mode === 'in' && head >= MIN_LEN - 1e-9) { anchorNow(s, s.in, clipT); s.out = clipT; return i + 1; }
        if (mode === 'out' && tail >= MIN_LEN - 1e-9) { anchorNow(s, clipT, s.out); s.in = clipT; return i; }
        return (t - start) < d / 2 ? i : i + 1;
      }
      start += d;
    }
    return list.length;
  }

  /* The clip time `filmOff` film seconds into a shot, moved to the nearest frame of the
   * film (the edit grid). */
  function splitPoint(seg, filmOff) {
    const n = Math.round(Math.max(0, filmOff) * gridRate());
    return r4(seg.in + (n / gridRate()) * speedOf(seg));
  }

  /* The end of a clip's media on its OWN frame grid (verdict 7, punch 9): its length
   * floored to whole frames of the clip's rate when the record has one, so a whole-clip
   * drop on a sequence of another rate (CLIP_05, 29.97, on a 59.94 sequence ended at
   * 35.1851, half a frame) still ends on one of the clip's frames. NTSC rates as
   * gridRate reads them (29.97 is 30000/1001). Unrounded: gridRange floors it to the
   * sequence's grid. */
  function clipEnd(c) {
    const d = c && Number(c.duration) > 0 ? Number(c.duration) : undefined;
    let r = c ? Number(c.fps) : 0;
    if (d === undefined || !(r > 0 && r < 1000)) return d;
    const n = Math.round(r * 1.001);
    if (Math.abs(r - Math.round(r)) > 0.004 && Math.abs(r - n / 1.001) < 0.004) r = n / 1.001;
    return Math.floor(d * r + 1e-4) / r;
  }

  /* A shot about to go in: its range on the grid, inside the clip's media. */
  function gridded(seg) {
    const [a, b] = gridRange(Number(seg.in), Number(seg.out), speedOf(seg), null,
                             clipEnd(clipOf(seg.clip)));
    return { ...seg, in: a, out: b };
  }

  function startOfIndex(i) {
    const list = segs();
    let t = 0;
    for (let k = 0; k < i && k < list.length; k++) t += dur(list[k]);
    return t;
  }

  /* Insert `{clip, in, out, why}` at a film time: the shot under it splits and the
   * rest of the film moves later by the new shot's length. One undo entry, `insert`.
   * Selects the new shot; returns its id. */
  function insertAt(seg, atFilmTime) {
    if (!seg || !seg.clip || !(seg.out > seg.in)) return null;
    const s = { ...gridded(seg), id: seg.id || `tmp-${++tmpN}` };
    return mutate('insert', () => {
      const at = cutAt(clamp(onGrid(atFilmTime), 0, total()), 'insert');
      segs().splice(at, 0, s);
      select([s.id], { source: 'api' });
      return s.id;
    });
  }

  /* Overwrite at a film time: the new shot covers the film from t for its own length —
   * whatever was there is replaced (shots wholly inside removed, the two at its edges
   * trimmed) and the film's length changes only where the shot runs past the end. One
   * undo entry, `overwrite`. Selects the new shot; returns its id. */
  function overwrite(seg, atFilmTime) {
    if (!seg || !seg.clip || !(seg.out > seg.in)) return null;
    const s = { ...gridded(seg), id: seg.id || `tmp-${++tmpN}` };
    const len = dur(s);
    return mutate('overwrite', () => {
      const list = segs();
      const at = cutAt(clamp(onGrid(atFilmTime), 0, total()), 'in');
      const end = cutAt(startOfIndex(at) + len, 'out');
      list.splice(at, Math.max(0, end - at), s);
      select([s.id], { source: 'api' });
      return s.id;
    });
  }

  /* The black a gap is made of: POST /api/gap, its clip record added to the board's
   * clips so the monitor plays it at once. Resolves to the clip's name, or null. */
  async function makeGap(seconds) {
    try {
      const r = await fetch('/api/gap', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ seconds }),
      });
      if (!r.ok) { toast(`could not leave a gap: ${(await r.text()).slice(0, 120)}`); return null; }
      const g = await r.json();
      const clips = hooks && hooks.clips ? hooks.clips() : null;
      if (clips && g.record && !clips[g.clip]) clips[g.clip] = g.record;
      return g.clip;
    } catch (err) {
      toast('could not leave a gap — the board is unreachable');
      return null;
    }
  }

  /* A clip placed at a film time on V1, as Premiere places it: inside the film it is
   * insertAt (or overwrite); past the film's end it lands where it was put, the empty
   * time before it a gap (verdict 4, punch 9: a drop at 10 s on an empty V1 left no gap).
   * One undo entry. Resolves to the new shot's id. */
  async function placeAt(seg, atFilmTime, kind = 'insert') {
    if (!seg || !seg.clip || !(seg.out > seg.in)) return null;
    const tot = total();
    const at = onGrid(Math.max(0, Number(atFilmTime) || 0));
    if (at <= tot + frame() / 2) return kind === 'overwrite' ? overwrite(seg, at) : insertAt(seg, at);
    if (cutLocked()) return refuseLocked(trackState('V1').lock ? 'V1' : 'A1');
    const len = onGrid(at - tot);
    if (len < MIN_LEN - 1e-9) return insertAt(seg, tot);      // a sliver: butt it
    const clip = await makeGap(len);
    if (!clip) return null;
    const s = { ...gridded(seg), id: seg.id || `tmp-${++tmpN}` };
    const gap = { id: `tmp-${++tmpN}`, clip, in: 0, out: len, gap: true };
    return mutate('insert', () => {
      segs().push(gap, s);
      select([s.id], { source: 'api' });
      return s.id;
    });
  }

  /* A clip dragged to the timeline past the film's end: placed where it was dropped
   * (placeAt), ahead of the lanes' own drop, which appends. */
  function onDropPast(e) {
    const dt = e.dataTransfer;
    if (!dt || ![...(dt.types || [])].includes('application/x-roughcut-shot')) return;
    // on V1's own row (the ruler and the other rows keep the lanes' append), and past
    // the end by more than the snap zone (8 px): close to the end it butts, as Premiere snaps
    if (!(e.target instanceof Element) || !e.target.closest('.tl-lane[data-lane=V1]')) return;
    const t = eventTime(e);
    if (t <= total() + Math.max(frame() / 2, 8 / state.zoom)) return;
    let shot = null;
    try { shot = JSON.parse(dt.getData('application/x-roughcut-shot')); } catch (err) { shot = null; }
    if (!shot || !shot.clip || !(shot.end > shot.start)) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    const line = el.root.querySelector('.tl-dropline');
    if (line) line.style.display = 'none';
    placeAt({ clip: shot.clip, in: shot.start, out: shot.end, why: shot.why || '' }, t);
  }

  /* Sequence › Add / Delete Track (index.html's menu; dock.js closes the menu). */
  function onSeqMenu(e) {
    const b = e.target.closest('#menubar .mdrop button[data-cmd^="seq:"]');
    if (!b) return;
    const cmd = b.dataset.cmd.slice(4);
    if (cmd === 'addv') addTrack('video');
    else if (cmd === 'adda') addTrack('audio');
    else if (cmd === 'delv') deleteTrack('video');
    else if (cmd === 'dela') deleteTrack('audio');
  }

  /* `0.5`, `0.25`, `2` — never `0.50`; the chips and the badge read the same. */
  function speedLabel(s) { return String(round2(s)); }

  function setSpeed(id, rate) {
    const seg = byId(id);
    if (!seg) return false;
    const r = Number(rate);
    if (!Number.isFinite(r)) return false;
    const want = round2(clamp(r, MIN_SPEED, MAX_SPEED));
    if (want === speedOf(seg)) return false;
    return mutate('speed', () => {
      const was = speedOf(seg);
      if (want === 1) delete seg.speed;      // 1× is the absence of the key, as edits.py reads it
      else seg.speed = want;
      // its length in the film stays whole frames at the new rate (the edit grid)
      const c = clipOf(seg.clip);
      [seg.in, seg.out] = gridRange(seg.in, seg.out, want, 'in', c && c.duration ? c.duration : undefined);
      // the keys are in film time: at the new rate a moment of the media comes was / want
      // as far into the shot (KEYANCHOR), and none passes its new end
      const k = keysOfSeg(seg);
      if (k) putKeys(seg, keysWindow(k, 0, (seg.out - seg.in) / want, was / want));
      return true;
    });
  }

  /* ------------------------------------------------------------ saving */
  /* A new shot goes to the server under its `tmp-N` id (wave 5, lane SAVE): the server
   * mints one id per temporary id and its reply names them (`ids`, applyIds below). A tmp
   * id already re-keyed (an undo brought back a snapshot taken before the re-key) goes
   * under the id the server gave it, so a shot is never minted twice. */
  function forSave() {
    return segs().map((s) => {
      if (!isTmp(s.id)) return s;
      const real = tmpMap.get(s.id);
      return real ? { ...s, id: real } : s;
    });
  }

  /* The id the server knows a shot by: a re-keyed tmp id's real one, else the id. */
  function idFor(id) { return (isTmp(id) && tmpMap.get(id)) || id; }

  function needsRekey() { return segs().some((s) => !s.id || isTmp(s.id)); }

  /* The save's reply (`ids`: {tmp-N: id}): re-key every shot it names, and any shot an
   * undo brought back under a tmp id the server already named. The map is kept even for
   * a shot no longer in the cut, so a redo of it saves under its real id. */
  function applyIds(map) {
    for (const [tmp, real] of Object.entries(map || {})) {
      if (isTmp(tmp) && typeof real === 'string' && real) tmpMap.set(tmp, real);
    }
    let changed = false;
    for (const s of segs()) {
      const real = isTmp(s.id) ? tmpMap.get(s.id) : null;
      if (!real || segs().some((o) => o.id === real)) continue;
      rekey(s.id, real);
      s.id = real;
      changed = true;
    }
    if (changed) render();
    return changed;
  }

  /* Every PUT /api/project the board sends takes a turn here, one at a time (the cut's
   * autosave in app.js and the tracks' saveSeq): two in flight could land in either
   * order, and the older body would win. `fn` builds its body when its turn comes and
   * holds the turn until it has read the reply, so the next body is built after the
   * re-key (applyIds). Returns fn's result. */
  let putQ = Promise.resolve();
  function putTurn(fn) {
    const p = putQ.then(fn);
    putQ = p.then(() => {}, () => {});
    return p;
  }

  /* Every PUT /api/project carries `x-roughcut-save: <page>:<n>` (wave 8, SAVESAFE): a
   * page that unloads with a save in flight flushes its latest body once more as a
   * keepalive PUT, and the server must never write the older one over it — whichever
   * lands last. And every one goes with credentials 'omit': Chrome keeps uncredentialed
   * requests on their own connections (six per host, a separate group from the page's
   * images and video), so a save is never queued behind the timeline's filmstrip
   * posters. Verdict 6's lost Accept was exactly that: its PUT waited in the browser
   * behind poster builds and the reload cancelled it before it was sent. */
  const PAGE = Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
  let saveN = 0;
  function saveHeaders(extra) {
    return { 'content-type': 'application/json', 'x-roughcut-save': `${PAGE}:${++saveN}`,
             ...(extra || {}) };
  }
  function seqForFlush() { return seqSaved == null ? null : JSON.parse(seqBody()); }
  function seqDirty() { return seqSaved != null && seqBody() !== seqSaved; }

  function rekey(oldId, newId) {
    if (oldId === newId) return;
    const b = blocks.get(oldId);
    if (b) { blocks.delete(oldId); blocks.set(newId, b); b.dataset.id = newId; }
    if (posterAt.has(oldId)) { posterAt.set(newId, posterAt.get(oldId)); posterAt.delete(oldId); }
    if (isTmp(oldId)) tmpMap.set(oldId, newId);
    if (state.sel.has(oldId)) {
      const next = new Set([...state.sel].map((x) => (x === oldId ? newId : x)));
      state.sel = next;
    }
    if (state.anchor === oldId) state.anchor = newId;
    // a transition names its edit by the two shots: it follows their re-key
    let moved = false;
    for (const t of state.transitions) {
      if (t.a === oldId) { t.a = newId; moved = true; }
      if (t.b === oldId) { t.b = newId; moved = true; }
    }
    if (state.edit && state.edit.a === oldId) state.edit.a = newId;
    if (state.edit && state.edit.b === oldId) state.edit.b = newId;
    if (moved) {                   // the boxes are keyed by the pair: drawn again
      for (const [key, b] of [...tblocks]) {
        if (!key.split('|').includes(oldId)) continue;
        b.remove();
        tblocks.delete(key);
      }
      if (state.tsel) state.tsel = state.tsel.split('|').map((x) => (x === oldId ? newId : x)).join('|');
    }
  }


  function afterSave(serverSegs) {
    const list = segs();
    if (!Array.isArray(serverSegs) || serverSegs.length !== list.length) return false;
    let changed = false;
    list.forEach((s, i) => {
      const r = serverSegs[i];
      if (!r || !r.id || s.id === r.id) return;
      if (s.clip !== r.clip || Math.abs(s.in - r.in) > 0.011 || Math.abs(s.out - r.out) > 0.011) return;
      rekey(s.id, r.id);
      s.id = r.id;
      changed = true;
    });
    if (changed) render();
    return changed;
  }

  function snapsFor(clip) {
    if (!snapsCache.has(clip)) {
      if (isGen(clip)) {
        // A generated clip has no sidecar and nothing to snap to; the server has no
        // snaps for it either, and a 404 per drag is noise. Its length is the clip's.
        const c = clipOf(clip);
        snapsCache.set(clip, Promise.resolve({
          clip, sentences: [], words: [], onsets: [], duration: (c && c.duration) || 0,
        }));
        return snapsCache.get(clip);
      }
      const p = fetch(`/api/snaps/${encodeURIComponent(clip)}`)
        .then((r) => { if (!r.ok) throw new Error(`snaps ${clip}: ${r.status}`); return r.json(); })
        .catch((err) => { snapsCache.delete(clip); throw err; });
      snapsCache.set(clip, p);
    }
    return snapsCache.get(clip);
  }

  /* ------------------------------------------------------------ pointer and keys */
  function onLaneDown(e) {
    if (e.button !== 0) return;
    down = { x: e.clientX, y: e.clientY, moved: false };
    // Track select forward (A): the pressed shot and every one after it, at the press —
    // so a drag that follows moves them all (the lanes' body drag takes the selection)
    const b = state.tool === 'track' && !e.defaultPrevented ? e.target.closest('.blk') : null;
    if (b) {
      const i = indexOf(b.dataset.id);
      if (i >= 0) select(segs().slice(i).map((s) => s.id), { source: 'click' });
    }
  }

  function onLaneMove(e) {
    if (down && (Math.abs(e.clientX - down.x) > 4 || Math.abs(e.clientY - down.y) > 4)) down.moved = true;
  }

  /* A plain click selects the block and parks the monitor on its first frame, paused
   * (INTAKE M16 decision 6 — it used to play from there, so looking at a shot's strip
   * started the sound). Space or a double-click plays. */
  function onLaneUp(e) {
    const d = down;
    down = null;
    if (!d || d.moved || e.button !== 0 || e.defaultPrevented) return;
    const b = e.target.closest('.blk');
    if (state.edit || state.tsel) { state.edit = null; state.tsel = null; paintTransitions(); }
    if (b && state.tool === 'razor') {
      // the razor: a cut where the click landed, the selection left alone
      const t = eventTime(e);
      if (split(b.dataset.id, t) == null) toast(`too close to a cut point to split (${MIN_LEN} s minimum)`);
      return;
    }
    if (b && state.tool === 'track') return;          // selected at the press
    if (b) {
      const id = b.dataset.id;
      if (e.shiftKey) select([id], { range: true, source: 'click' });
      else if (e.metaKey || e.ctrlKey) select([id], { add: true, source: 'click' });
      else {
        select([id], { source: 'click' });
        seek(filmStart(id));
      }
      return;
    }
    select([], { source: 'click' });
    seek(eventTime(e));
  }

  /* A click on A1 or V2 (under 4 px of travel, nobody's drag): an A1 block selects its
   * shot as V1's does — the razor cuts it there, at the picture's time less the sound's
   * offset; a V2 block selects that clip; the empty track clears and seeks. */
  function onTrackUp(e) {
    const d = down;
    down = null;
    if (!d || d.moved || e.button !== 0 || e.defaultPrevented || e.altKey) return;
    const ov = e.target.closest('.oblk');
    if (ov) { selectOverlay(ov.dataset.id); seek(eventTime(e)); return; }
    const b = e.target.closest('.ablk');
    if (b && state.tool === 'razor') {
      const seg = byId(b.dataset.id);
      if (seg && split(seg.id, eventTime(e) - aOff(seg)) == null) {
        toast(`too close to a cut point to split (${MIN_LEN} s minimum)`);
      }
      return;
    }
    if (b) {
      const id = b.dataset.id;
      if (e.shiftKey) select([id], { range: true, source: 'click' });
      else if (e.metaKey || e.ctrlKey) select([id], { add: true, source: 'click' });
      else { select([id], { source: 'click' }); seek(filmStart(id)); }
      return;
    }
    if (state.osel != null) selectOverlay(null);
    select([], { source: 'click' });
    seek(eventTime(e));
  }

  function onLaneDbl(e) {
    const b = e.target.closest('.blk, .ablk');
    if (!b || e.shiftKey || e.metaKey || e.ctrlKey || !hooks.play) return;
    hooks.play(indexOf(b.dataset.id));
  }

  function onRulerDown(e) {
    if (e.button !== 0) return;
    e.preventDefault();
    const mk = e.target.closest('.tl-marker');
    if (mk) { seek(Number(mk.dataset.t)); return; }      // a marker: go there, no scrub
    scrubbing = true;
    try { el.ruler.setPointerCapture(e.pointerId); } catch (err) { /* not pointer-capable */ }
    select([], { source: 'click' });
    seek(eventTime(e));
  }

  function onRulerMove(e) {
    if (!scrubbing) return;
    cancelAnimationFrame(scrubRaf);
    const t = eventTime(e);
    scrubRaf = requestAnimationFrame(() => { if (scrubbing) seek(t); });
  }

  function onRulerUp() { scrubbing = false; }

  /* Premiere's wheel: Alt or Ctrl/⌘ + wheel zooms around the cursor; a plain (or ⇧)
   * wheel scrolls the timeline along — unless there is nothing to scroll, when the page
   * keeps its own wheel. */
  function onWheel(e) {
    if (e.ctrlKey || e.metaKey || e.altKey) {
      e.preventDefault();
      const r = el.view.getBoundingClientRect();
      const viewX = e.clientX - r.left;
      const at = xToTime(viewX + el.view.scrollLeft);
      const d = e.deltaY || e.deltaX;
      zoomTo(state.zoom * (d < 0 ? 1.25 : 0.8), { at, viewX });
      return;
    }
    const v = el.view;
    if (v.scrollWidth <= v.clientWidth + 1) return;
    const d = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
    if (!d) return;
    e.preventDefault();
    v.scrollLeft += d;
    state.scrollX = v.scrollLeft;
  }

  function onKey(e) {
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName) || e.target.isContentEditable) return;
    if (e.target.closest && e.target.closest('dialog[open]')) return;   // a modal's keys are its own
    const k = e.key;
    if ((e.metaKey || e.ctrlKey) && k.toLowerCase() === 'z') {
      e.preventDefault();
      if (e.shiftKey) redo(); else undo();
      return;
    }
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (!segs().length) return;
    if (k === '+' || k === '=') { e.preventDefault(); zoomTo(state.zoom * 2); }
    else if (k === '-' || k === '_') { e.preventDefault(); zoomTo(state.zoom / 2); }
    else if (k === '\\') { e.preventDefault(); fit(); }
  }

  /* ------------------------------------------------------------ mount */
  function mount(selector, h) {
    hooks = h || {};
    const root = typeof selector === 'string' ? document.querySelector(selector) : selector;
    if (!root) throw new Error(`tl.mount: nothing matches ${selector}`);
    root.classList.add('tl');
    // The side column is Premiere's top-left corner of the Timeline panel: the
    // playhead's timecode, and the tool in hand (one button; the six are its menu).
    // Under it, the track headers (V2 V1 A1 A2: lock, target, eye or mute / solo), each
    // at its row's top; the rows fill the panel's height.
    root.innerHTML = `
      <div class="tl-side">
        <div class="tl-corner">
          <div class="tl-tc">00:00:00:00</div>
          <button type="button" class="tl-toolbtn"></button>
          <div class="tl-toolmenu" hidden></div>
        </div>
        <div class="tl-heads"></div>
      </div>
      <div class="tl-view">
        <div class="tl-canvas">
          <div class="tl-ruler" title="click or drag to scrub"></div>
          <div class="tl-track" data-track="V2"></div>
          <div class="tl-lane tl-track-v1" data-lane="V1"></div>
          <div class="tl-avdiv"></div>
          <div class="tl-track" data-track="A1"></div>
          <div class="tl-track" data-track="A2"></div>
          <div class="tl-end"><label class="tl-total"></label></div>
          <div class="tl-head"></div>
        </div>
      </div>`;
    el.root = root;
    el.view = root.querySelector('.tl-view');
    el.canvas = root.querySelector('.tl-canvas');
    el.ruler = root.querySelector('.tl-ruler');
    el.lanes = { V1: root.querySelector('.tl-lane[data-lane=V1]') };
    el.tracks = {};
    for (const n of ['V2', 'A1', 'A2']) {
      el.tracks[n] = root.querySelector(`.tl-track[data-track=${n}]`);
      el.lanes[n] = el.tracks[n];
    }
    el.divider = root.querySelector('.tl-avdiv');
    el.end = root.querySelector('.tl-end');
    el.total = root.querySelector('.tl-total');
    el.head = root.querySelector('.tl-head');
    el.side = root.querySelector('.tl-side');
    buildSide(el.side);
    buildHeads(el.side);
    loadMarks();
    loadSeq();
    seqSaved = seqBody();

    const lane = el.lanes.V1;
    lane.addEventListener('pointerdown', onLaneDown);
    lane.addEventListener('pointerdown', onEditPress, true);    // an edit's column: select it
    lane.addEventListener('pointermove', onLaneMove);
    lane.addEventListener('pointerup', onLaneUp);
    lane.addEventListener('pointercancel', () => { down = null; });
    lane.addEventListener('dblclick', onLaneDbl);
    el.ruler.addEventListener('pointerdown', onRulerDown);
    el.ruler.addEventListener('pointermove', onRulerMove);
    el.ruler.addEventListener('pointerup', onRulerUp);
    el.ruler.addEventListener('pointercancel', onRulerUp);
    el.view.addEventListener('wheel', onWheel, { passive: false });
    el.view.addEventListener('scroll', () => {
      state.scrollX = el.view.scrollLeft;
      scheduleA1();                // a block scrolled into view gets its waveform
      // the headers ride the rows when the tracks are taller than the panel
      const hb = el.side.querySelector('.tl-heads');
      if (hb) hb.style.transform = `translateY(${-el.view.scrollTop}px)`;
    });
    // A1: a click selects the shot (and parks the monitor on it), as V1's does; ⌥-click
    // links or unlinks the pair. Drags are the lanes'. V2: a click selects that clip.
    for (const n of ['A1', 'V2']) {
      const t = el.tracks[n];
      t.addEventListener('pointerdown', onLaneDown);
      t.addEventListener('pointermove', onLaneMove);
      t.addEventListener('pointerup', onTrackUp);
      t.addEventListener('pointercancel', () => { down = null; });
    }
    el.tracks.A1.addEventListener('dblclick', onLaneDbl);
    root.addEventListener('drop', onDropPast, true);
    // a transition from the Effects bin (verdict 8 punch 5): capture, ahead of the lanes'
    root.addEventListener('dragover', onTransDragOver, true);
    root.addEventListener('drop', onTransDrop, true);
    root.addEventListener('dragleave', (e) => {
      if (!e.relatedTarget || !root.contains(e.relatedTarget)) hideTransDrop();
    });
    root.addEventListener('contextmenu', onTransContext);
    document.addEventListener('dragend', hideTransDrop);
    document.addEventListener('click', onSeqMenu);
    document.addEventListener('keydown', onKey);
    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(() => {
        // read here, where the layout is fresh: everything else uses these (viewWidth)
        viewW = el.view.clientWidth;
        viewH = el.view.clientHeight;
        if (autoFit) render(); else layoutTracks();
      }).observe(el.view);
    }
    tl.on('playhead', applyMonitorAudio);
    applyMonitorAudio();
    const redoBtn = document.querySelector('#redo');
    if (redoBtn) redoBtn.addEventListener('click', () => redo());
    // Nothing is selected at start (INTAKE M16 I16.4): app.js's index is not adopted on
    // the first render — the strip under the timeline shows the film until a shot is
    // chosen, played or landed on.
    lastAppSel = hooks.sel ? hooks.sel() : -1;
    render();
    return tl;
  }

  const tl = {
    state, el, mount, render, on,
    byId, indexOf, idAt, dur, speedOf, filmStart, total, shotAt, timeToX, xToTime, eventTime, snapsFor,
    begin, commit, cancel, batch, undo, redo, clearHistory,
    setRange, move, split, remove, insert, setSpeed, insertAt, overwrite,
    select, syncSel, seek, setPlayhead, zoomTo, fit,
    forSave, needsRekey, afterSave, applyIds, idFor, putTurn, saveHeaders, seqForFlush, seqDirty,
    fmt, hueOf, blockName, blockSays, fitLabel, textWidth, fontOf,
    // the tracks (roughcut/tracks.py)
    TRACKS, tracks, setTrack, audible, isLinked, aOff, setLink, toggleLink, setAudioOffset,
    trackNames, addTrack, deleteTrack, placeAt, isGap, refuseIfLocked,
    audioClips: () => state.audio.map((c) => ({ ...c })),
    overlays: () => state.overlays.map((o) => ({ ...o })), overlayById,
    addOverlay, moveOverlay, trimOverlay, removeOverlay, selectOverlay, layoutTracks, saveSeq,
    get trackTops() { return { ...(state.trackTops || {}) }; },
    fps, setFps, frame, tc, keyLabel, isMac: IS_MAC, gridRate, onGrid, isOnGrid,
    TOOLS, setTool, get tool() { return state.tool; },
    addMarker, removeMarker, clearMarkers, setMarkers, nextMarker, prevMarker,
    markers: () => [...state.markers],
    setSeqIn, setSeqOut,
    keysWindow,                     // keyframes on the media (KEYANCHOR; app.js's [ ] nudge)
    // transitions at the edits (verdict 6, punch 6; roughcut/tracks.py)
    transitions: () => state.transitions.map((t) => ({ ...t })),
    transitionHalves, addTransition, removeTransition, setTransitionDur, applyTransition,
    selectTransition, editTransitionDur, transitionsForSave, editForTransition,
    TRANS_NAMES, get tsel() { return state.tsel; },
    // the Effects bin (verdict 8 punch 5): kinds, one-sided ones, drops, the defaults
    VIDEO_KINDS, AUDIO_KINDS, TRANS_MIME, transitionFades, addFade, dropTransition, transTarget,
    setDefaultTransition, get transitionDefaults() { return { ...transDefaults }; },
    selectEdit: (k) => {
      const list = segs();
      state.edit = list[k] && list[k + 1] ? { a: list[k].id, b: list[k + 1].id } : null;
      state.tsel = null;
      paintTransitions();
      return selectedEdit();
    },
    posterLoad,                     // the filmstrip tiles' loader (lane YIELD)
    get posterBusy() { return posterQ.busy; },
  };
  window.tl = tl;
})();
