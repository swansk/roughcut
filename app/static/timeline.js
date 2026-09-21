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
 *                               `toast(msg)`.
 *   tl.state                    `{segs, sel, anchor, zoom, scrollX, playhead}` — `segs` is
 *                               the live array (a getter over hooks.segs()); `sel` is a
 *                               Set<id>, REPLACED on every change (read it, do not hold it);
 *                               `anchor` is the id ⇧-ranges extend from and the shot app.js's
 *                               index `sel` points at, or null when nothing is selected;
 *                               `zoom` is px per second of film; `scrollX` mirrors the
 *                               viewport's scrollLeft; `playhead` is a film time.
 *   tl.el                       `{root, view, canvas, ruler, lanes: {V1}, head}` — the DOM
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
 *   tl.undo() / tl.redo()       ⌘Z / ⌘⇧Z (ctrl on Windows), `u`, the header's #undo /
 *                               #redo — whose tooltips carry the label (`undo: trim`).
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
 *   tl.setSpeed(id, rate)       The shot's `speed` (0.1–4, rounded to 0.01): one undo
 *                               entry labelled `speed`; the block, the total and the
 *                               monitor's rate follow. 1 deletes the key from the
 *                               segment, so a 1× shot saves the way it always did.
 *                               Returns true when something changed.
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
 *   — saving —
 *   tl.forSave()                The segments to PUT: known tmp ids mapped to their server
 *                               ids, unknown ones stripped (the server mints).
 *   tl.needsRekey()             True while a shot has no server id yet.
 *   tl.afterSave(segments)      The segments from GET /api/project after a save: re-key by
 *                               position where clip/in/out agree.
 *
 *   — events —
 *   tl.on(event, fn) → off()    `change` {label, kind, ids} · `select` {ids, anchor,
 *                               source: 'click'|'api'|'app'} · `playhead` {t} · `zoom`
 *                               {zoom}.
 *
 *   — pointer contract for the lanes —
 *   The module handles pointerdown/up on the V1 lane and the ruler. A pointerdown that
 *   moves more than 4 px before pointerup, or whose propagation a lane's own handler stops
 *   (a trim handle, a drag), never becomes a click here: a plain click on a block selects
 *   it and plays the cut from it (the strip's old promise, kept); ⇧-click ranges; ⌘/ctrl-
 *   click toggles; a click on the empty lane or the ruler clears the selection and seeks;
 *   dragging on the ruler scrubs. Keys (outside inputs): `+`/`=` and `-` zoom ×2 / ÷2,
 *   `\` fits, ⌘/ctrl + wheel zooms around the cursor, ⇧ + wheel pans, ⌘Z / ⌘⇧Z undo / redo.
 * =====================================================================================
 */
(function () {
  'use strict';

  const PAD = 12;                  // px of canvas before 0:00
  const PAD_R = 48;                // px after the end: room for the total's label
  const MIN_LEN = 0.2;             // s — the shortest shot, as nudge() clamps
  const MIN_SPEED = 0.1, MAX_SPEED = 4;    // edits.py's bounds: outside them a speed reads as 1
  const MIN_ZOOM = 0.5, MAX_ZOOM = 4000;   // px per second
  const STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600];   // labelled ruler steps, s
  const UNDO_LIMIT = 100;
  const POSTER_SETTLE_MS = 450;    // the same debounce app.js's cards use

  let hooks = null;
  const el = { root: null, view: null, canvas: null, ruler: null, lanes: {}, head: null,
               end: null, total: null };
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
  };
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
  function stepFor(zoom) {
    for (const s of STEPS) if (s * zoom >= 64) return s;
    return STEPS[STEPS.length - 1];
  }

  function label(t, step) {
    const m = Math.floor(t / 60), s = t - m * 60;
    return step >= 1 ? `${m}:${String(Math.round(s)).padStart(2, '0')}`
                     : `${m}:${s.toFixed(1).padStart(4, '0')}`;
  }

  /* Labelled ticks every 1/2/5/10/15/30/60 s (or coarser) — whichever first gives a
   * label 64 px of room; minor ticks split each step in five, or ten once a step is
   * wide enough that fifths would be a hundred pixels apart. */
  function paintRuler(width) {
    const step = stepFor(state.zoom);
    const ratio = step * state.zoom >= 500 ? 10 : 5;
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
        lab.textContent = label(t, step);
        tick.appendChild(lab);
      }
      frag.appendChild(tick);
    }
    el.ruler.replaceChildren(frag);
  }

  function makeBlock(id) {
    const b = document.createElement('div');
    b.className = 'blk';
    b.dataset.id = id;
    b.innerHTML = '<img class="poster" draggable="false" loading="lazy" decoding="async" alt="">'
      + '<div class="txt"><span class="name"></span><span class="dur"></span>'
      + '<span class="speed" hidden></span><div class="line"></div></div>'
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
      }
    }, POSTER_SETTLE_MS);
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
    const clip = clipOf(seg.clip);
    b.querySelector('.name').textContent = gen ? genKind(seg.clip) : stem(seg.clip);
    b.querySelector('.dur').textContent = `${filmLen.toFixed(1)}s`;
    // the badge: only when the shot is retimed, so a 1× cut looks the way it always did
    const badge = b.querySelector('.speed');
    badge.hidden = spd === 1;
    badge.textContent = spd === 1 ? '' : `${speedLabel(spd)}×`;
    b.querySelector('.line').textContent = strongestLine(seg, clip);
    b.title = `${i + 1}. ${gen ? genLine(seg, clip) : stem(seg.clip)} ${fmt(seg.in)}–${fmt(seg.out)}`
      + (spd === 1 ? ` (${filmLen.toFixed(1)}s)`
                   : ` at ${speedLabel(spd)}× (${filmLen.toFixed(1)}s of film from ${(seg.out - seg.in).toFixed(1)}s)`)
      + (seg.why ? `\n${seg.why}` : '');
    const warn = warnEdges(seg, clip);
    const wi = b.querySelector('.warn.in'), wo = b.querySelector('.warn.out');
    wi.hidden = !warn.in; wi.title = 'opens mid-sentence';
    wo.hidden = !warn.out; wo.title = 'cuts a line off';
    if (!posterAt.has(seg.id)) setPoster(b, seg);
    else if (posterAt.get(seg.id) !== seg.in) schedulePosters();
  }

  function fitZoom(viewW, tot) {
    return clamp((viewW - PAD - PAD_R) / Math.max(tot, 0.001), MIN_ZOOM, MAX_ZOOM);
  }

  function positionHead() {
    el.head.style.left = `${timeToX(state.playhead)}px`;
  }

  function keepInView(x, frac = 0.25) {
    const v = el.view;
    const w = v.clientWidth;
    if (!w) return;
    if (x < v.scrollLeft + 6 || x > v.scrollLeft + w - 6) {
      v.scrollLeft = Math.max(0, x - w * frac);
      state.scrollX = v.scrollLeft;
    }
  }

  function paintSel() {
    for (const [id, b] of blocks) b.classList.toggle('sel', state.sel.has(id));
  }

  function paintUndo() {
    const u = document.querySelector('#undo'), r = document.querySelector('#redo');
    const top = undoStack[undoStack.length - 1], next = redoStack[redoStack.length - 1];
    if (u) {
      u.disabled = !top;
      u.title = top ? `undo: ${top.label} (⌘Z · u)` : 'nothing to undo';
    }
    if (r) {
      r.disabled = !next;
      r.title = next ? `redo: ${next.label} (⌘⇧Z)` : 'nothing to redo';
    }
  }

  function render() {
    if (!hooks || !el.root) return;
    const list = segs();
    ensureIds();
    syncSel();
    const tot = total();
    const viewW = el.view.clientWidth;
    if (autoFit && viewW > 0 && list.length) state.zoom = fitZoom(viewW, tot);
    const width = Math.max(viewW, PAD + tot * state.zoom + PAD_R);
    el.canvas.style.width = `${width}px`;
    paintRuler(width);

    // Blocks are keyed by id and updated in place: a re-render after every shot advance
    // must not rebuild <img> elements, and a lane's own handles survive it.
    const live = hooks.live ? hooks.live() : -1;
    const lane = el.lanes.V1;
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
    el.end.style.left = `${timeToX(tot)}px`;
    el.total.textContent = fmt(tot);
    el.root.classList.toggle('tl-empty', !list.length);   // not `.empty`: the board's empty state owns that
    state.playhead = clamp(state.playhead, 0, tot);
    positionHead();
    state.scrollX = el.view.scrollLeft;
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
    emit('select', { ids: [...next], anchor, source: 'api' });
  }

  /* ------------------------------------------------------------ playhead and view */
  function setPlayhead(t, opts = {}) {
    t = clamp(Number(t) || 0, 0, total());
    state.playhead = t;
    if (!el.head) return;
    positionHead();
    if (opts.reveal !== false) keepInView(timeToX(t));
    emit('playhead', { t });
  }

  function seek(t) {
    t = clamp(Number(t) || 0, 0, total());
    setPlayhead(t);
    const at = shotAt(t);
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

  /* ------------------------------------------------------------ undo / redo */
  const snapshot = () => JSON.stringify(segs());

  function restore(json) {
    hooks.setSegs(JSON.parse(json));
    ensureIds();
  }

  function begin(label) {
    if (pending) return false;
    pending = { label: label || 'edit', before: snapshot() };
    return true;
  }

  function commit() {
    if (!pending) return false;
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

  function cancel() {
    if (!pending) return false;
    const p = pending;
    pending = null;
    if (snapshot() !== p.before) {
      restore(p.before);
      if (hooks.render) hooks.render();
      if (hooks.touch) hooks.touch();
    }
    return true;
  }

  function undo() {
    const e = undoStack.pop();
    if (!e) { toast('nothing to undo'); return false; }
    pending = null;
    restore(e.before);
    redoStack.push(e);
    if (hooks.render) hooks.render();
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
    restore(e.after);
    undoStack.push(e);
    if (hooks.render) hooks.render();
    if (hooks.touch) hooks.touch();
    paintUndo();
    emit('change', { label: e.label, kind: 'redo' });
    toast(`redone: ${e.label}`);
    return true;
  }

  /* ------------------------------------------------------------ mutations */
  function mutate(kind, fn) {
    const own = begin(kind);
    const result = fn();
    ensureIds();
    if (state.anchor != null) {
      const i = indexOf(state.anchor);
      if (i >= 0) { lastAppSel = i; if (hooks.setSel) hooks.setSel(i); }
    }
    if (hooks.render) hooks.render(); else render();
    if (hooks.touch) hooks.touch();
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
    a = round2(clamp(a, 0, Math.max(0, dur - MIN_LEN)));
    b = round2(clamp(b, a + MIN_LEN, dur));
    if (a === seg.in && b === seg.out) return false;
    return mutate('trim', () => {
      const inMoved = a !== seg.in;
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
    const clipT = round2(seg.in + (Number(atFilmTime) - filmStart(seg.id)) * speedOf(seg));
    if (!(clipT >= seg.in + MIN_LEN && clipT <= seg.out - MIN_LEN)) return null;
    return mutate('split', () => {
      const second = { ...seg, id: `tmp-${++tmpN}`, in: clipT };
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
      select(heir ? [heir.id] : [], { source: 'api' });
      return true;
    });
  }

  function insert(seg, afterId) {
    if (!seg || !seg.clip) return null;
    const list = segs();
    const s = { ...seg };
    if (!s.id) s.id = `tmp-${++tmpN}`;
    const after = afterId == null ? -1 : indexOf(afterId);
    const at = afterId == null ? list.length : after < 0 ? list.length : after + 1;
    return mutate('insert', () => {
      list.splice(at, 0, s);
      select([s.id], { source: 'api' });
      return s.id;
    });
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
      if (want === 1) delete seg.speed;      // 1× is the absence of the key, as edits.py reads it
      else seg.speed = want;
      return true;
    });
  }

  /* ------------------------------------------------------------ saving */
  function forSave() {
    return segs().map((s) => {
      if (!isTmp(s.id)) return s;
      const { id, ...rest } = s;
      const real = tmpMap.get(id);
      return real ? { ...rest, id: real } : rest;
    });
  }

  function needsRekey() { return segs().some((s) => !s.id || isTmp(s.id)); }

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
  }

  function onLaneMove(e) {
    if (down && (Math.abs(e.clientX - down.x) > 4 || Math.abs(e.clientY - down.y) > 4)) down.moved = true;
  }

  function onLaneUp(e) {
    const d = down;
    down = null;
    if (!d || d.moved || e.button !== 0 || e.defaultPrevented) return;
    const b = e.target.closest('.blk');
    if (b) {
      const id = b.dataset.id;
      if (e.shiftKey) select([id], { range: true, source: 'click' });
      else if (e.metaKey || e.ctrlKey) select([id], { add: true, source: 'click' });
      else {
        select([id], { source: 'click' });
        if (hooks.play) hooks.play(indexOf(id));
      }
      return;
    }
    select([], { source: 'click' });
    seek(eventTime(e));
  }

  function onRulerDown(e) {
    if (e.button !== 0) return;
    e.preventDefault();
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

  function onWheel(e) {
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      const r = el.view.getBoundingClientRect();
      const viewX = e.clientX - r.left;
      const at = xToTime(viewX + el.view.scrollLeft);
      zoomTo(state.zoom * (e.deltaY < 0 ? 1.25 : 0.8), { at, viewX });
    } else if (e.shiftKey) {
      e.preventDefault();
      el.view.scrollLeft += e.deltaY || e.deltaX;
      state.scrollX = el.view.scrollLeft;
    }
  }

  function onKey(e) {
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName) || e.target.isContentEditable) return;
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
    root.innerHTML = `
      <div class="tl-view">
        <div class="tl-canvas">
          <div class="tl-ruler" title="click or drag to scrub"></div>
          <div class="tl-lane" data-lane="V1"></div>
          <div class="tl-end"><label class="tl-total"></label></div>
          <div class="tl-head"></div>
        </div>
      </div>`;
    el.root = root;
    el.view = root.querySelector('.tl-view');
    el.canvas = root.querySelector('.tl-canvas');
    el.ruler = root.querySelector('.tl-ruler');
    el.lanes = { V1: root.querySelector('.tl-lane[data-lane=V1]') };
    el.end = root.querySelector('.tl-end');
    el.total = root.querySelector('.tl-total');
    el.head = root.querySelector('.tl-head');

    const lane = el.lanes.V1;
    lane.addEventListener('pointerdown', onLaneDown);
    lane.addEventListener('pointermove', onLaneMove);
    lane.addEventListener('pointerup', onLaneUp);
    lane.addEventListener('pointercancel', () => { down = null; });
    el.ruler.addEventListener('pointerdown', onRulerDown);
    el.ruler.addEventListener('pointermove', onRulerMove);
    el.ruler.addEventListener('pointerup', onRulerUp);
    el.ruler.addEventListener('pointercancel', onRulerUp);
    el.view.addEventListener('wheel', onWheel, { passive: false });
    el.view.addEventListener('scroll', () => { state.scrollX = el.view.scrollLeft; });
    document.addEventListener('keydown', onKey);
    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(() => { if (autoFit) render(); }).observe(el.view);
    }
    const redoBtn = document.querySelector('#redo');
    if (redoBtn) redoBtn.addEventListener('click', () => redo());
    lastAppSel = -1;                 // adopt app.js's index on the first render
    render();
    return tl;
  }

  const tl = {
    state, el, mount, render, on,
    byId, indexOf, idAt, dur, speedOf, filmStart, total, shotAt, timeToX, xToTime, eventTime, snapsFor,
    begin, commit, cancel, undo, redo,
    setRange, move, split, remove, insert, setSpeed,
    select, syncSel, seek, setPlayhead, zoomTo, fit,
    forSave, needsRekey, afterSave,
    fmt, hueOf,
  };
  window.tl = tl;
})();
