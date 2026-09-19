/* Roughcut — trims by drag, the magnet, and the edge columns (INTAKE M9, I9.2 + I9.7;
 * lanes agent/tl-trim, agent/tl-edges).
 *
 * Built ON the foundation (timeline.js, `window.tl`) and never edits it: everything here
 * hangs on the DOM the foundation exposes (`tl.el`) and goes through its edit API — one
 * `tl.begin` on pointerdown, `tl.setRange` on every move (so the board, the cards, the
 * posters and the autosave behave exactly as they do for a card's trim button), one
 * `tl.commit` on pointerup, `tl.cancel` on Esc. A drag is one undo entry, however many
 * moves it took.
 *
 *   The edge column at every interior cut: EDGE_PX wide, centred on the cut line, the full
 *   (I9.7)        height of the lane, split at half its height — Karl's rule, "at the top
 *                 of the clip it extends, bottom cuts in".
 *                 TOP half — extend or shorten the shot, the rest moves (a ripple trim):
 *                 left of the line it takes the left shot's `out`, right of it the right
 *                 shot's `in`. The shot changes length and everything after it slides,
 *                 because that is what a change to a segment's range does on a film-time
 *                 timeline. The tooltip says so: `out 3.30s · +0.30s · the rest moves`.
 *                 BOTTOM half — roll: the left shot's `out` and the right shot's `in` move
 *                 together, the film's length held. `roll · CLIP_A out 3.30s · CLIP_B in
 *                 0.70s`.
 *                 ⇧ flips the two, before or during the drag, so a hand that landed on the
 *                 wrong half never has to let go: the entry begun at pointerdown is cancelled
 *                 and begun again under the other label, one undo entry either way.
 *                 Before the press, the half under the pointer lights up — the shot's own hue
 *                 with an arrow into the neighbour and a ghost of the first shot that would
 *                 move (GHOST_S later) for an extend; a neutral bar across the cut with ⇄
 *                 for a roll — and a one-line hint under the timeline says which is which:
 *                 at once the first HINT_FREE times a cut is hovered (counted in
 *                 localStorage), after that on a HINT_DWELL_MS dwell.
 *   Ripple trim   the film's first in and last out have no neighbour: the block's own
 *                 full-height handle (8 px, `ew-resize`) extends or shortens the shot.
 *   Slip          ⌥/alt + drag a block's body: `in` and `out` move together, the length
 *                 kept, clamped to the clip.
 *   Nudge         `,` / `.` move the *active edge* — the last one dragged, or the selected
 *                 shot's out — by one frame (1/30 s); `⇧,` / `⇧.` by 1 s. These two keys
 *                 are this lane's; the keys lane owns the rest of the keyboard.
 *   The magnet    while an edge or a boundary is dragged, snap within SNAP_PX to, in
 *                 priority: another cut point (roll only — for a ripple every other cut
 *                 either lies on the far side of the block or moves with the film as it
 *                 closes up), the playhead, a sentence `cut_in` (in edge) / `cut_out`
 *                 (out edge), a word start, an onset — the clip's points from
 *                 `tl.snapsFor`. A snap line across the timeline says what it took;
 *                 the tooltip too. `S` toggles it (persisted), ⌘/ctrl held while dragging
 *                 suspends it, and the dragged block shows its clip's sentence and onset
 *                 points as faint ticks so the editor sees what there is to take.
 *
 * Pointer mapping: a drag is a delta in seconds — pointer travel ÷ the zoom at pointerdown
 * — applied to the edge's clip time. The zoom at pointerdown, deliberately: the
 * foundation refits the whole cut to the viewport after every change until someone
 * zooms, so a ripple that lengthens the film re-zooms under the pointer; a fixed
 * px-per-second keeps the hand and the number in step regardless.
 *
 * Mount: the foundation has no `mounted` event, so this file watches `#tl` for the
 * foundation's view to appear (app.js mounts it after the project loads) and wires
 * itself then — no patch to the foundation's object.
 */
(function () {
  'use strict';

  const SNAP_PX = 8;               // an edge this close to a snap point takes it
  const CLICK_PX = 3;              // less travel than this is a click, not a trim
  const EDGE_PX = 16;              // the column over an interior cut: 8 px each side of the line
  const GHOST_S = 0.5;             // s — how far the hover ghost shows the pushed shot moved
  const HINT_FREE = 5;             // cut hovers that show the hint at once, ever...
  const HINT_DWELL_MS = 600;       // ...after that, a dwell this long
  const MIN_LEN = 0.2;             // s — the foundation's shortest shot
  const FRAME = 1 / 30;            // s — one nudge
  const MAGNET_KEY = 'roughcut.tl.magnet';
  const HINT_KEY = 'roughcut.tl.edgeHintSeen';
  const CATS = ['cut', 'playhead', 'sentence', 'word', 'onset'];   // snap priority
  const TIP_FLASH_MS = 900;
  const HINT_HTML = '<b>top edge</b> · extend or shorten this shot, the rest moves · '
    + '<b>bottom edge</b> · roll the cut into the next · <b>⇧</b> flips';

  let tl = null, lane = null, canvas = null, root = null;
  let drag = null;                 // the gesture in flight (see onDown)
  let active = null;               // {id, edge} — the edge `,`/`.` move
  let hover = null;                // {col, row, side} — the edge column under the pointer
  let shift = false;               // ⇧ as last seen, for the hover cue
  let magnet = true;
  const ui = { line: null, lineLabel: null, tip: null, magnet: null, ghost: null, hint: null };
  const snaps = new Map();         // clip -> resolved /api/snaps payload
  let stepRaf = 0, layoutRaf = 0, tipTimer = 0, hintTimer = 0;
  let mo = null;

  const round2 = (x) => Math.round(x * 100) / 100;
  const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
  const secs = (t) => `${t.toFixed(2)}s`;
  const signed = (x) => `${x < 0 ? '−' : '+'}${Math.abs(x).toFixed(2)}s`;
  const stem = (clip) => String(clip).replace(/\.[^.]+$/, '');
  /* Karl's rule — the top half extends, the bottom rolls — and ⇧ flips it. */
  const modeFor = (row, shiftHeld) => ((row === 't') !== !!shiftHeld) ? 'trim' : 'roll';

  /* The clip's length bounds a slip and a roll. app.js keeps the clips on a script-local
   * `P` the foundation reads through its hooks and does not expose, so the length comes
   * from the snaps payload (the server's own clip_duration), warmed for every clip in the
   * cut at mount and on every change; before it lands, setRange's own clamp still holds. */
  function clipDur(name) {
    const s = snaps.get(name);
    if (s && Number.isFinite(s.duration) && s.duration > 0) return s.duration;
    return 1e9;
  }

  /* ---------------------------------------------------------------- snap points */
  function warm() {
    for (const s of tl.state.segs) load(s.clip);
  }

  function load(clip) {
    if (!clip || snaps.has(clip)) return;
    tl.snapsFor(clip).then((p) => { if (p) snaps.set(clip, p); }).catch(() => {});
  }

  function pointsOf(clip) {
    const p = snaps.get(clip);
    if (!p) return { ins: [], outs: [], words: [], onsets: [] };
    return {
      ins: (p.sentences || []).map((s) => s.cut_in),
      outs: (p.sentences || []).map((s) => s.cut_out),
      words: p.words || [],
      onsets: p.onsets || [],
    };
  }

  /* ---------------------------------------------------------------- the picture */
  function el(tag, cls, parent) {
    const e = document.createElement(tag);
    e.className = cls;
    if (parent) parent.appendChild(e);
    return e;
  }

  function buildUi() {
    ui.line = el('div', 'tl-snapline', canvas);
    ui.line.hidden = true;
    ui.lineLabel = el('label', '', ui.line);
    ui.tip = el('div', 'tl-tip', canvas);
    ui.tip.hidden = true;
    ui.ghost = el('div', 'tl-edge-ghost', canvas);
    ui.ghost.hidden = true;
    ui.hint = el('div', 'tl-hint', root);
    ui.hint.innerHTML = HINT_HTML;
    ui.magnet = el('button', 'tl-magnet', root);
    ui.magnet.type = 'button';
    ui.magnet.addEventListener('click', () => setMagnet(!magnet));
    paintMagnet();
  }

  function paintMagnet() {
    if (!ui.magnet) return;
    ui.magnet.textContent = `magnet · ${magnet ? 'on' : 'off'}`;
    ui.magnet.classList.toggle('off', !magnet);
    ui.magnet.title = magnet
      ? 'snapping to cuts, the playhead, sentences, words and onsets — S turns it off, ⌘/ctrl while dragging suspends it'
      : 'no snapping — S turns the magnet on';
  }

  function setMagnet(on) {
    magnet = !!on;
    try { localStorage.setItem(MAGNET_KEY, magnet ? '1' : '0'); } catch (err) { /* private mode */ }
    paintMagnet();
    if (drag && drag.moved) schedule();
  }

  /* Handles on every block (the CSS shows them only at the film's first in and last out —
   * `.tl-first` / `.tl-last`, set below in film order) and an edge column over every
   * interior cut. Blocks are the foundation's, created and moved by its render; a
   * MutationObserver on the lane sees both and this lays the lane's own elements over
   * them, one frame later. */
  function decorate(b) {
    if (b.querySelector(':scope > .tl-h.in')) return;
    const hin = el('i', 'tl-h in', b);
    hin.title = 'drag: trim the in point (ripple) · ⌥ drag the body: slip';
    const hout = el('i', 'tl-h out', b);
    hout.title = 'drag: trim the out point (ripple) · ⌥ drag the body: slip';
    el('div', 'tl-ticks', b);
  }

  /* An edge column: four quadrants, hit targets only — the cue is drawn on the column. */
  function makeColumn() {
    const c = el('i', 'tl-edge', lane);
    for (const row of ['t', 'b']) {
      for (const side of ['l', 'r']) {
        const q = el('i', `q ${row} ${side}`, c);
        q.dataset.row = row;
        q.dataset.side = side;
      }
    }
    return c;
  }

  function blockOf(id) {
    return lane.querySelector(`:scope > .blk[data-id="${CSS.escape(id)}"]`);
  }

  function layout() {
    layoutRaf = 0;
    const blocks = [...lane.querySelectorAll(':scope > .blk')]
      .sort((a, b) => (+a.dataset.i || 0) - (+b.dataset.i || 0));   // film order, not DOM order
    blocks.forEach((b, i) => {
      decorate(b);
      b.classList.toggle('tl-first', i === 0);
      b.classList.toggle('tl-last', i === blocks.length - 1);
    });
    const cols = [...lane.querySelectorAll(':scope > .tl-edge')];
    const want = Math.max(0, blocks.length - 1);
    while (cols.length > want) cols.pop().remove();
    while (cols.length < want) cols.push(makeColumn());
    for (let i = 0; i < want; i++) {
      const b = blocks[i], n = blocks[i + 1], c = cols[i];
      const cut = parseFloat(b.style.left) + parseFloat(b.style.width);
      c.style.left = `${cut - EDGE_PX / 2}px`;
      c.style.width = `${EDGE_PX}px`;
      c.dataset.left = b.dataset.id;
      c.dataset.right = n.dataset.id;
      c.style.setProperty('--hue-l', b.style.getPropertyValue('--hue'));
      c.style.setProperty('--hue-r', n.style.getPropertyValue('--hue'));
    }
    if (drag && drag.moved) { paintTicks(); paintTip(); paintLine(); }
    else if (hover) paintCue();    // the blocks moved under a hover: the ghost follows
    if (mo) mo.takeRecords();      // our own edits are not a reason to lay out again
  }

  function scheduleLayout() {
    if (!layoutRaf) layoutRaf = requestAnimationFrame(layout);
  }

  /* Faint ticks inside a dragged block: its clip's sentence cut points and onsets, at
   * film time — what the magnet can take. Re-laid on every move: the in point moves the
   * block's own origin. */
  function paintTicks() {
    if (!drag) return;
    for (const id of drag.ids) {
      const b = blockOf(id);
      const seg = tl.byId(id);
      if (!b || !seg) continue;
      const box = b.querySelector(':scope > .tl-ticks');
      if (!box) continue;
      const pts = pointsOf(seg.clip);
      const z = tl.state.zoom;
      const frag = document.createDocumentFragment();
      const put = (t, cls) => {
        if (t < seg.in || t > seg.out) return;
        const k = document.createElement('i');
        k.className = `tl-tick ${cls}`;
        k.style.left = `${(t - seg.in) * z}px`;
        frag.appendChild(k);
      };
      pts.ins.forEach((t) => put(t, 'sentence in'));
      pts.outs.forEach((t) => put(t, 'sentence out'));
      pts.onsets.forEach((t) => put(t, 'onset'));
      box.replaceChildren(frag);
      box.hidden = false;
    }
  }

  function clearTicks() {
    for (const box of lane.querySelectorAll('.tl-ticks')) { box.replaceChildren(); box.hidden = true; }
  }

  function paintLine() {
    const s = drag && drag.snapped;
    if (!s) { ui.line.hidden = true; return; }
    ui.line.hidden = false;
    ui.line.className = `tl-snapline ${s.cat}`;
    ui.line.style.left = `${tl.timeToX(s.film)}px`;
    ui.lineLabel.textContent = s.cat;
  }

  function showTip(filmT, text) {
    clearTimeout(tipTimer);
    ui.tip.hidden = false;
    ui.tip.textContent = text;
    ui.tip.style.left = `${tl.timeToX(filmT)}px`;
  }

  function flashTip(filmT, text) {
    showTip(filmT, text);
    tipTimer = setTimeout(() => { if (!drag) ui.tip.hidden = true; }, TIP_FLASH_MS);
  }

  /* The trim tip carries the change in the shot's LENGTH, signed — `+` extended, `−`
   * shortened — which is what an edge drag is for; on an in edge that is the opposite sign
   * of the hand's travel. And what else moves: the shots after this one, or, when there
   * are none, the film's end. */
  function paintTip() {
    if (!drag || !drag.moved) { ui.tip.hidden = true; return; }
    const took = drag.snapped ? ` · snap: ${drag.snapped.cat}` : '';
    if (drag.kind === 'trim') {
      const seg = tl.byId(drag.id);
      if (!seg) return;
      const t = drag.edge === 'in' ? seg.in : seg.out;
      const grew = drag.edge === 'in' ? drag.in0 - seg.in : seg.out - drag.out0;
      const after = tl.idAt(tl.indexOf(drag.id) + 1);
      showTip(edgeFilm(drag.id, drag.edge),
        `${drag.edge} ${secs(t)} · ${signed(grew)} · ${after ? 'the rest moves' : 'the end moves'}${took}`);
    } else if (drag.kind === 'roll') {
      const L = tl.byId(drag.left), R = tl.byId(drag.right);
      if (!L || !R) return;
      showTip(tl.filmStart(drag.right),
        `roll · ${stem(L.clip)} out ${secs(L.out)} · ${stem(R.clip)} in ${secs(R.in)}${took}`);
    } else {
      const seg = tl.byId(drag.id);
      if (!seg) return;
      showTip(tl.filmStart(drag.id), `slip ${secs(seg.in)}–${secs(seg.out)} · ${secs(seg.out - seg.in)} long`);
    }
  }

  function hideTip() { clearTimeout(tipTimer); ui.tip.hidden = true; }

  /* Film time of a shot's edge, as the timeline shows it now. */
  function edgeFilm(id, edge) {
    const seg = tl.byId(id);
    const fs = tl.filmStart(id);
    if (!seg || fs < 0) return 0;
    return edge === 'in' ? fs : fs + (seg.out - seg.in);
  }

  /* The monitor parks on the edge being moved. The foundation's seek cues by film time
   * and a film time on a cut lands on the NEXT shot's first frame, so an out edge is
   * cued a hair inside its own shot — the last frame, not the neighbour's first. */
  function park(id, edge) {
    if (!tl.seek) return;
    const seg = tl.byId(id);
    if (!seg) return;
    const fs = tl.filmStart(id);
    if (fs < 0) return;
    tl.seek(edge === 'in' ? fs : Math.max(fs, fs + (seg.out - seg.in) - 0.03));
  }

  /* ---------------------------------------------------------------- the cues (I9.7) */
  /* Hover, before the press: which column, which half, which side — and the cue for it. */
  function trackHover(e) {
    const q = e.target && e.target.closest ? e.target.closest('.tl-edge > .q') : null;
    if (!q) { clearHover(); return; }
    const col = q.parentElement;
    shift = !!e.shiftKey;
    if (!hover || hover.col !== col) hintOnEnter();
    hover = { col, row: q.dataset.row, side: q.dataset.side };
    paintCue();
  }

  function clearHover() {
    if (!hover) return;
    hover = null;
    clearTimeout(hintTimer);
    ui.hint.classList.remove('on');
    paintCue();
  }

  /* The lit half, as classes on the column — `.lit .top|.bot .left|.right .ext|.roll` —
   * that the CSS draws: the shot's hue and an arrow into the neighbour for an extend, a
   * neutral bar across the cut for a roll. During a drag the column follows the drag's
   * mode, so a ⇧ flip shows on the column as well as in the tooltip. */
  function paintCue() {
    let col = null, row = null, side = null, mode = null;
    if (drag && drag.col) { col = drag.col; row = drag.row; side = drag.side; mode = drag.kind; }
    else if (hover) { col = hover.col; row = hover.row; side = hover.side; mode = modeFor(row, shift); }
    for (const c of lane.querySelectorAll(':scope > .tl-edge')) {
      if (c !== col) { c.className = 'tl-edge'; continue; }
      c.className = `tl-edge lit ${row === 't' ? 'top' : 'bot'} ${side === 'l' ? 'left' : 'right'}`
        + ` ${mode === 'trim' ? 'ext' : 'roll'}`;
    }
    paintGhost(col, side, mode);
  }

  /* Before an extend: a faint outline of the first shot that would move, GHOST_S later —
   * the "rest moves" reading, visible before the hand commits. Nothing after the shot: a
   * strip past its out, the film's end moving. Gone during the drag, when the real blocks
   * move (the foundation re-renders on every setRange). */
  function paintGhost(col, side, mode) {
    if (!col || drag || mode !== 'trim') { ui.ghost.hidden = true; return; }
    const id = side === 'l' ? col.dataset.left : col.dataset.right;   // the shot being extended
    const i = tl.indexOf(id);
    const b = i < 0 ? null : blockOf(id);
    if (!b) { ui.ghost.hidden = true; return; }
    const next = tl.idAt(i + 1);
    const nb = next ? blockOf(next) : null;
    const z = tl.state.zoom;
    if (nb) {
      ui.ghost.style.left = `${parseFloat(nb.style.left) + GHOST_S * z}px`;
      ui.ghost.style.width = nb.style.width;
    } else {
      ui.ghost.style.left = `${parseFloat(b.style.left) + parseFloat(b.style.width)}px`;
      ui.ghost.style.width = `${GHOST_S * z}px`;
    }
    ui.ghost.style.setProperty('--hue', (nb || b).style.getPropertyValue('--hue'));
    ui.ghost.classList.toggle('end', !nb);
    ui.ghost.hidden = false;
  }

  /* The hint under the timeline: at once while the editor is new to the columns, then
   * only when the hand rests on one. The count lives in localStorage, so it is the
   * editor's, not the page load's. */
  function hintOnEnter() {
    clearTimeout(hintTimer);
    let seen = 0;
    try { seen = parseInt(localStorage.getItem(HINT_KEY), 10) || 0; } catch (err) { seen = 0; }
    try { localStorage.setItem(HINT_KEY, String(seen + 1)); } catch (err) { /* private mode */ }
    if (seen < HINT_FREE) ui.hint.classList.add('on');
    else hintTimer = setTimeout(() => { if (hover) ui.hint.classList.add('on'); }, HINT_DWELL_MS);
  }

  /* ---------------------------------------------------------------- the magnet */
  /* Candidates for the value a drag is setting — a clip time for a trim, a delta for a
   * roll — each with the category it belongs to and the film time to draw the line at.
   * The playhead is where it stood at pointerdown: parking the monitor on the dragged
   * edge goes through the foundation's seek, which moves the playhead with it, so the
   * frame the editor parked on before taking the edge is the one the magnet offers. */
  function candidates(d) {
    const out = [];
    const P = d.p0;                // where the playhead stood when the edge was taken
    if (d.kind === 'trim') {
      const toFilm = (v) => d.fs0 + (v - d.in0);
      const lo = d.edge === 'in' ? 0 : d.in0 + MIN_LEN;
      const hi = d.edge === 'in' ? d.out0 - MIN_LEN : d.dur;
      const add = (cat, v) => { if (v >= lo - 1e-9 && v <= hi + 1e-9) out.push({ cat, v, film: toFilm(v) }); };
      add('playhead', d.in0 + (P - d.fs0));
      const pts = pointsOf(d.clip);
      (d.edge === 'in' ? pts.ins : pts.outs).forEach((t) => add('sentence', t));
      pts.words.forEach((t) => add('word', t));
      pts.onsets.forEach((t) => add('onset', t));
    } else if (d.kind === 'roll') {
      const add = (cat, v) => { if (v >= d.lo - 1e-9 && v <= d.hi + 1e-9) out.push({ cat, v, film: d.b0 + v }); };
      for (const c of d.cuts) if (Math.abs(c - d.b0) > 1e-6) add('cut', c - d.b0);
      add('playhead', P - d.b0);
      const L = pointsOf(d.clipL), R = pointsOf(d.clipR);
      L.outs.forEach((t) => add('sentence', t - d.outL0));
      R.ins.forEach((t) => add('sentence', t - d.inR0));
      L.words.forEach((t) => add('word', t - d.outL0));
      R.words.forEach((t) => add('word', t - d.inR0));
      L.onsets.forEach((t) => add('onset', t - d.outL0));
      R.onsets.forEach((t) => add('onset', t - d.inR0));
    }
    return out;
  }

  function snapFor(d, raw) {
    const all = candidates(d);
    for (const cat of CATS) {
      let best = null;
      for (const c of all) {
        if (c.cat !== cat) continue;
        const px = Math.abs(c.v - raw) * d.zoom;
        if (px <= SNAP_PX && (!best || px < best.px)) best = { ...c, px };
      }
      if (best) return best;
    }
    return null;
  }

  /* ---------------------------------------------------------------- the gesture */
  function trimParams(seg, edge) {
    return { kind: 'trim', id: seg.id, ids: [seg.id], edge, clip: seg.clip, in0: seg.in, out0: seg.out,
             fs0: tl.filmStart(seg.id), dur: clipDur(seg.clip) };
  }

  function rollParams(L, R) {
    const durL = clipDur(L.clip);
    return {
      kind: 'roll', left: L.id, right: R.id, ids: [L.id, R.id],
      clipL: L.clip, clipR: R.clip, outL0: L.out, inR0: R.in,
      b0: tl.filmStart(R.id),
      lo: Math.max(L.in + MIN_LEN - L.out, -R.in),
      hi: Math.min(durL - L.out, R.out - MIN_LEN - R.in),
      cuts: cutPoints(),
    };
  }

  /* An edge column's gesture in one of its two modes. The side of the line picks the
   * shot an extend takes: the left shot's out, the right shot's in. */
  function columnParams(d, kind) {
    const L = tl.byId(d.L), R = tl.byId(d.R);
    if (!L || !R) return {};
    if (kind === 'roll') return rollParams(L, R);
    return d.side === 'l' ? trimParams(L, 'out') : trimParams(R, 'in');
  }

  function onDown(e) {
    if (e.button !== 0 || drag || !tl.state.segs.length) return;
    const q = e.target.closest('.tl-edge > .q');
    const h = e.target.closest('.tl-h');
    const blk = e.target.closest('.blk');
    let d = null;
    if (q) {
      const col = q.parentElement;
      if (!col.dataset.left || !col.dataset.right) return;
      d = { col, row: q.dataset.row, side: q.dataset.side, L: col.dataset.left, R: col.dataset.right };
      Object.assign(d, columnParams(d, modeFor(d.row, e.shiftKey)));
      if (!d.kind) return;
    } else if (h && blk) {
      const seg = tl.byId(blk.dataset.id);
      if (!seg) return;
      d = trimParams(seg, h.classList.contains('in') ? 'in' : 'out');
    } else if (blk && e.altKey) {
      const seg = tl.byId(blk.dataset.id);
      if (!seg) return;
      d = { kind: 'slip', id: seg.id, ids: [seg.id], clip: seg.clip, in0: seg.in, out0: seg.out,
            dur: clipDur(seg.clip) };
    } else {
      return;                      // a plain press on a block: the foundation's click
    }
    e.stopPropagation();           // the foundation never sees this as a click
    e.preventDefault();
    Object.assign(d, { pointerId: e.pointerId, x0: e.clientX, zoom: tl.state.zoom,
                       p0: tl.state.playhead,
                       moved: false, dt: 0, suspend: false, snapped: null, dirty: false });
    drag = d;
    try { lane.setPointerCapture(e.pointerId); } catch (err) { /* not pointer-capable */ }
    lane.classList.add('tl-dragging');
    if (d.col) lane.classList.add('tl-drag-edge');
    enter(d);
    paintCue();
  }

  /* Take the edge: the cursor class, the entry under the mode's label, the active edge,
   * the selection and the monitor's park. At pointerdown, and again on a ⇧ flip. */
  function enter(d) {
    lane.classList.remove('tl-drag-trim', 'tl-drag-roll', 'tl-drag-slip');
    lane.classList.add(`tl-drag-${d.kind}`);
    tl.begin(d.kind);
    d.ids.forEach((id) => { const s = tl.byId(id); if (s) load(s.clip); });
    if (d.kind === 'trim') {
      active = { id: d.id, edge: d.edge };
      tl.select([d.id], { source: 'click' });
      park(d.id, d.edge);
    } else if (d.kind === 'roll') {
      active = { id: d.left, edge: 'out' };
      park(d.right, 'in');
    } else {
      tl.select([d.id], { source: 'click' });
      park(d.id, 'in');
    }
  }

  /* ⇧ pressed or released during an edge column's drag: the other mode, without letting
   * go. The entry begun at pointerdown is cancelled — the cut back as it was — and begun
   * again under the new label, so the one undo entry says what the drag ended as; the
   * pointer's travel so far is re-applied in the new mode. */
  function flip(shiftHeld) {
    const d = drag;
    if (!d || !d.col) return;
    const kind = modeFor(d.row, shiftHeld);
    if (kind === d.kind) return;
    tl.cancel();
    const params = columnParams(d, kind);
    if (!params.kind) { finish(true); return; }
    const { col, row, side, L, R, pointerId, x0, zoom, p0, moved, dt, suspend, dirty } = d;
    drag = Object.assign({ col, row, side, L, R, pointerId, x0, zoom, p0, moved, dt, suspend, dirty,
                           snapped: null }, params);
    enter(drag);
    paintCue();
    if (drag.moved) schedule();
  }

  function cutPoints() {
    const pts = [0];
    let t = 0;
    for (const s of tl.state.segs) { t += s.out - s.in; pts.push(round2(t)); }
    return pts;
  }

  function onMove(e) {
    if (!drag) { trackHover(e); return; }
    if (e.pointerId !== drag.pointerId) return;
    e.stopPropagation();
    if (drag.col) flip(e.shiftKey);          // a no-op while the mode already matches
    const dx = e.clientX - drag.x0;
    if (!drag.moved && Math.abs(dx) < CLICK_PX) return;
    drag.moved = true;
    drag.dt = dx / drag.zoom;
    drag.suspend = !!(e.metaKey || e.ctrlKey);
    schedule();
  }

  function schedule() {
    drag.dirty = true;
    if (!stepRaf) stepRaf = requestAnimationFrame(step);
  }

  function step() {
    stepRaf = 0;
    if (!drag || !drag.dirty) return;
    drag.dirty = false;
    const d = drag;
    const use = magnet && !d.suspend && d.kind !== 'slip';
    if (d.kind === 'trim') {
      const raw = d.edge === 'in' ? clamp(d.in0 + d.dt, 0, d.out0 - MIN_LEN)
                                  : clamp(d.out0 + d.dt, d.in0 + MIN_LEN, d.dur);
      const s = use ? snapFor(d, raw) : null;
      const v = s ? s.v : raw;
      if (d.edge === 'in') tl.setRange(d.id, v, null); else tl.setRange(d.id, null, v);
      d.snapped = s;
      park(d.id, d.edge);
    } else if (d.kind === 'roll') {
      const s = use ? snapFor(d, d.dt) : null;
      const v = round2(clamp(s ? s.v : d.dt, d.lo, d.hi));
      tl.setRange(d.left, null, round2(d.outL0 + v));
      tl.setRange(d.right, round2(d.inR0 + v), null);
      d.snapped = s;
      park(d.right, 'in');
    } else {
      const v = round2(clamp(d.dt, -d.in0, d.dur - d.out0));
      tl.setRange(d.id, round2(d.in0 + v), round2(d.out0 + v));
      d.snapped = null;
      park(d.id, 'in');
    }
    paintTicks(); paintTip(); paintLine();
  }

  function onUp(e) {
    if (!drag || e.pointerId !== drag.pointerId) return;
    e.stopPropagation();
    finish(false);
  }

  function onCancel(e) {
    if (!drag || e.pointerId !== drag.pointerId) return;
    finish(true);
  }

  function finish(cancelled) {
    const d = drag;
    if (!d) return;
    if (!cancelled && d.dirty) step();      // the last move, even if its frame never came
    drag = null;
    cancelAnimationFrame(stepRaf); stepRaf = 0;
    try { lane.releasePointerCapture(d.pointerId); } catch (err) { /* already released */ }
    lane.classList.remove('tl-dragging', 'tl-drag-trim', 'tl-drag-roll', 'tl-drag-slip', 'tl-drag-edge');
    clearTicks(); hideTip();
    ui.line.hidden = true;
    if (cancelled) tl.cancel(); else tl.commit();
    clearHover();                  // the next move over a column lights it again
    paintCue();
  }

  /* ---------------------------------------------------------------- nudges and keys */
  function activeEdge() {
    if (active && tl.byId(active.id)) return active;
    const id = tl.state.anchor;
    if (id != null && tl.byId(id)) return { id, edge: 'out' };
    return null;
  }

  function nudge(dir, big) {
    const a = activeEdge();
    if (!a) return false;
    const seg = tl.byId(a.id);
    const step = dir * (big ? 1 : FRAME);
    tl.begin('nudge');
    const changed = a.edge === 'in'
      ? tl.setRange(a.id, seg.in + step, null)
      : tl.setRange(a.id, null, seg.out + step);
    tl.commit();
    active = a;
    park(a.id, a.edge);
    const s = tl.byId(a.id);
    if (s) flashTip(edgeFilm(a.id, a.edge),
      `${a.edge} ${secs(a.edge === 'in' ? s.in : s.out)} · ${secs(s.out - s.in)} long`);
    return changed;
  }

  function onKey(e) {
    if (e.key === 'Shift') {       // before the input guard: a drag runs wherever focus is
      shift = true;
      if (drag) flip(true); else if (hover) paintCue();
      return;
    }
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName) || e.target.isContentEditable) return;
    const k = e.key;
    if (k === 'Escape') {
      if (drag) { e.preventDefault(); finish(true); }
      return;
    }
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (k === 's' || k === 'S') { e.preventDefault(); setMagnet(!magnet); return; }
    if (!tl.state.segs.length || drag) return;
    if (k === ',' || k === '<') { e.preventDefault(); nudge(-1, e.shiftKey || k === '<'); }
    else if (k === '.' || k === '>') { e.preventDefault(); nudge(1, e.shiftKey || k === '>'); }
  }

  function onKeyUp(e) {
    if (e.key !== 'Shift') return;
    shift = false;
    if (drag) flip(false); else if (hover) paintCue();
  }

  /* ---------------------------------------------------------------- the map */
  /* The `?` map's Timeline section is rendered from the keys lane's one table
   * (`window.tlKeys.KEYS`, timeline-keys.js): the two modes and the flip go in before its
   * `?` row and the section is re-rendered. The keys script loads after this one, so the
   * table is looked for at mount — after both have loaded — and once more a tick later. */
  function mapRows(retry) {
    const K = window.tlKeys && window.tlKeys.KEYS;
    if (!Array.isArray(K)) { if (!retry) setTimeout(() => mapRows(true), 0); return; }
    if (K.some((r) => r[2] === 'edges')) return;
    const rows = [
      ['▲ edge', 'drag a cut’s top half: extend or shorten the shot, the rest moves', 'edges'],
      ['▼ edge', 'drag a cut’s bottom half: roll the cut into the next, the length held', 'edges'],
      ['⇧ drag', 'flips extend ↔ roll, before or during the drag', 'edges'],
    ];
    let at = K.findIndex((r) => r[0] === '?');
    if (at < 0) at = K.length;
    K.splice(at, 0, ...rows);
    if (typeof window.tlKeys.renderMap === 'function') window.tlKeys.renderMap();
  }

  /* ---------------------------------------------------------------- mount */
  function init() {
    tl = window.tl;
    root = tl.el.root;
    lane = tl.el.lanes.V1;
    canvas = tl.el.canvas;
    try { magnet = localStorage.getItem(MAGNET_KEY) !== '0'; } catch (err) { magnet = true; }
    buildUi();
    lane.addEventListener('pointerdown', onDown, true);
    lane.addEventListener('pointermove', onMove, true);
    lane.addEventListener('pointerup', onUp, true);
    lane.addEventListener('pointercancel', onCancel, true);
    lane.addEventListener('pointerleave', () => { if (!drag) clearHover(); });
    document.addEventListener('keydown', onKey);
    document.addEventListener('keyup', onKeyUp);
    window.addEventListener('blur', () => { shift = false; });
    mo = new MutationObserver(scheduleLayout);
    mo.observe(lane, { childList: true, subtree: true, attributes: true, attributeFilter: ['style'] });
    tl.on('change', warm);
    tl.on('zoom', scheduleLayout);
    warm();
    layout();
    mapRows();
    tl.trim = {
      SNAP_PX, FRAME, EDGE_PX, HINT_KEY,
      get magnet() { return magnet; },
      setMagnet,
      get active() { return active; },
      get dragging() { return drag ? drag.kind : null; },
      get hover() { return hover ? { row: hover.row, side: hover.side, mode: modeFor(hover.row, shift) } : null; },
      nudge,
    };
  }

  function ready() {
    return !!(window.tl && window.tl.el && window.tl.el.root && window.tl.el.lanes && window.tl.el.lanes.V1);
  }

  if (ready()) init();
  else {
    const host = document.querySelector('#tl') || document.body;
    const watch = new MutationObserver(() => { if (ready()) { watch.disconnect(); init(); } });
    watch.observe(host, { childList: true, subtree: host === document.body });
  }
})();
