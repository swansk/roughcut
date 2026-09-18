/* Roughcut — trims by drag (INTAKE M9, I9.2; lane agent/tl-trim). The magnet follows.
 *
 * Built ON the foundation (timeline.js, `window.tl`) and never edits it: everything here
 * hangs on the DOM the foundation exposes (`tl.el`) and goes through its edit API — one
 * `tl.begin` on pointerdown, `tl.setRange` on every move (so the board, the cards, the
 * posters and the autosave behave exactly as they do for a card's trim button), one
 * `tl.commit` on pointerup, `tl.cancel` on Esc. A drag is one undo entry, however many
 * moves it took.
 *
 *   Ripple trim   drag a block's in or out handle (8 px, `ew-resize`): the shot's `in` or
 *                 `out` moves; the film closes up behind it, because that is what a change
 *                 to a segment's range does on a film-time-derived timeline.
 *   Roll          drag the 10 px zone straddling the cut between two adjacent blocks
 *                 (`col-resize`): the left shot's `out` and the right shot's `in` move
 *                 together — the film's length does not change.
 *   Slip          ⌥/alt + drag a block's body: `in` and `out` move together, the length
 *                 kept, clamped to the clip.
 *   Nudge         `,` / `.` move the *active edge* — the last one dragged, or the selected
 *                 shot's out — by one frame (1/30 s); `⇧,` / `⇧.` by 1 s. These two keys
 *                 are this lane's; the keys lane owns the rest of the keyboard.
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

  const CLICK_PX = 3;              // less travel than this is a click, not a trim
  const ROLL_PX = 10;              // the zone straddling a cut that rolls it
  const MIN_LEN = 0.2;             // s — the foundation's shortest shot
  const FRAME = 1 / 30;            // s — one nudge
  const TIP_FLASH_MS = 900;

  let tl = null, lane = null, canvas = null;
  let drag = null;                 // the gesture in flight (see onDown)
  let active = null;               // {id, edge} — the edge `,`/`.` move
  const ui = { tip: null };
  const snaps = new Map();         // clip -> resolved /api/snaps payload (for the durations)
  let stepRaf = 0, layoutRaf = 0, tipTimer = 0;
  let mo = null;

  const round2 = (x) => Math.round(x * 100) / 100;
  const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
  const secs = (t) => `${t.toFixed(2)}s`;

  /* The clip's length bounds a slip and a roll. app.js keeps the clips on a script-local
   * `P` the foundation reads through its hooks and does not expose, so the length comes
   * from the snaps payload (the server's own clip_duration), warmed for every clip in the
   * cut at mount and on every change; before it lands, setRange's own clamp still holds. */
  function clipDur(name) {
    const s = snaps.get(name);
    if (s && Number.isFinite(s.duration) && s.duration > 0) return s.duration;
    return 1e9;
  }

  /* ---------------------------------------------------------------- clip lengths */
  function warm() {
    for (const s of tl.state.segs) load(s.clip);
  }

  function load(clip) {
    if (!clip || snaps.has(clip)) return;
    tl.snapsFor(clip).then((p) => { if (p) snaps.set(clip, p); }).catch(() => {});
  }

  /* ---------------------------------------------------------------- the picture */
  function el(tag, cls, parent) {
    const e = document.createElement(tag);
    e.className = cls;
    if (parent) parent.appendChild(e);
    return e;
  }

  function buildUi() {
    ui.tip = el('div', 'tl-tip', canvas);
    ui.tip.hidden = true;
  }

  /* Handles on every block and a roll zone over every interior cut. Blocks are the
   * foundation's, created and moved by its render; a MutationObserver on the lane sees
   * both and this lays the lane's own elements over them, one frame later. */
  function decorate(b) {
    if (b.querySelector(':scope > .tl-h.in')) return;
    const hin = el('i', 'tl-h in', b);
    hin.title = 'drag: trim the in point (ripple) · ⌥ drag the body: slip';
    const hout = el('i', 'tl-h out', b);
    hout.title = 'drag: trim the out point (ripple) · ⌥ drag the body: slip';
  }

  function layout() {
    layoutRaf = 0;
    const blocks = [...lane.querySelectorAll(':scope > .blk')];
    blocks.forEach(decorate);
    const rolls = [...lane.querySelectorAll(':scope > .tl-roll')];
    const want = Math.max(0, blocks.length - 1);
    while (rolls.length > want) rolls.pop().remove();
    while (rolls.length < want) {
      const r = el('i', 'tl-roll', lane);
      r.title = 'drag: roll the cut (one shot grows as the other shrinks)';
      rolls.push(r);
    }
    for (let i = 0; i < want; i++) {
      const b = blocks[i], r = rolls[i];
      const right = parseFloat(b.style.left) + parseFloat(b.style.width);
      r.style.left = `${right - ROLL_PX / 2}px`;
      r.style.width = `${ROLL_PX}px`;
      r.dataset.left = b.dataset.id;
      r.dataset.right = blocks[i + 1].dataset.id;
    }
    if (drag && drag.moved) paintTip();
    if (mo) mo.takeRecords();      // our own edits are not a reason to lay out again
  }

  function scheduleLayout() {
    if (!layoutRaf) layoutRaf = requestAnimationFrame(layout);
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

  function paintTip() {
    if (!drag || !drag.moved) { ui.tip.hidden = true; return; }
    if (drag.kind === 'trim') {
      const seg = tl.byId(drag.id);
      if (!seg) return;
      const t = drag.edge === 'in' ? seg.in : seg.out;
      showTip(edgeFilm(drag.id, drag.edge), `${drag.edge} ${secs(t)} · ${secs(seg.out - seg.in)} long`);
    } else if (drag.kind === 'roll') {
      const L = tl.byId(drag.left), R = tl.byId(drag.right);
      if (!L || !R) return;
      showTip(tl.filmStart(drag.right),
        `cut ${secs(L.out)} | ${secs(R.in)} · ${secs(L.out - L.in)} + ${secs(R.out - R.in)}`);
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

  /* ---------------------------------------------------------------- the gesture */
  function onDown(e) {
    if (e.button !== 0 || drag || !tl.state.segs.length) return;
    const roll = e.target.closest('.tl-roll');
    const h = e.target.closest('.tl-h');
    const blk = e.target.closest('.blk');
    let d = null;
    if (roll && roll.dataset.left && roll.dataset.right) {
      const L = tl.byId(roll.dataset.left), R = tl.byId(roll.dataset.right);
      if (!L || !R) return;
      const durL = clipDur(L.clip);
      d = {
        kind: 'roll', left: L.id, right: R.id, ids: [L.id, R.id],
        clipL: L.clip, clipR: R.clip, outL0: L.out, inR0: R.in,
        b0: tl.filmStart(R.id),
        lo: Math.max(L.in + MIN_LEN - L.out, -R.in),
        hi: Math.min(durL - L.out, R.out - MIN_LEN - R.in),
      };
    } else if (h && blk) {
      const seg = tl.byId(blk.dataset.id);
      if (!seg) return;
      d = {
        kind: 'trim', id: seg.id, ids: [seg.id], edge: h.classList.contains('in') ? 'in' : 'out',
        clip: seg.clip, in0: seg.in, out0: seg.out, fs0: tl.filmStart(seg.id), dur: clipDur(seg.clip),
      };
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
                       moved: false, dt: 0, dirty: false });
    drag = d;
    try { lane.setPointerCapture(e.pointerId); } catch (err) { /* not pointer-capable */ }
    lane.classList.add('tl-dragging', `tl-drag-${d.kind}`);
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

  function onMove(e) {
    if (!drag || e.pointerId !== drag.pointerId) return;
    e.stopPropagation();
    const dx = e.clientX - drag.x0;
    if (!drag.moved && Math.abs(dx) < CLICK_PX) return;
    drag.moved = true;
    drag.dt = dx / drag.zoom;
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
    if (d.kind === 'trim') {
      const v = d.edge === 'in' ? clamp(d.in0 + d.dt, 0, d.out0 - MIN_LEN)
                                : clamp(d.out0 + d.dt, d.in0 + MIN_LEN, d.dur);
      if (d.edge === 'in') tl.setRange(d.id, v, null); else tl.setRange(d.id, null, v);
      park(d.id, d.edge);
    } else if (d.kind === 'roll') {
      const v = round2(clamp(d.dt, d.lo, d.hi));
      tl.setRange(d.left, null, round2(d.outL0 + v));
      tl.setRange(d.right, round2(d.inR0 + v), null);
      park(d.right, 'in');
    } else {
      const v = round2(clamp(d.dt, -d.in0, d.dur - d.out0));
      tl.setRange(d.id, round2(d.in0 + v), round2(d.out0 + v));
      park(d.id, 'in');
    }
    paintTip();
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
    lane.classList.remove('tl-dragging', 'tl-drag-trim', 'tl-drag-roll', 'tl-drag-slip');
    hideTip();
    if (cancelled) tl.cancel(); else tl.commit();
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
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName) || e.target.isContentEditable) return;
    const k = e.key;
    if (k === 'Escape') {
      if (drag) { e.preventDefault(); finish(true); }
      return;
    }
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (!tl.state.segs.length || drag) return;
    if (k === ',' || k === '<') { e.preventDefault(); nudge(-1, e.shiftKey || k === '<'); }
    else if (k === '.' || k === '>') { e.preventDefault(); nudge(1, e.shiftKey || k === '>'); }
  }

  /* ---------------------------------------------------------------- mount */
  function init() {
    tl = window.tl;
    lane = tl.el.lanes.V1;
    canvas = tl.el.canvas;
    buildUi();
    lane.addEventListener('pointerdown', onDown, true);
    lane.addEventListener('pointermove', onMove, true);
    lane.addEventListener('pointerup', onUp, true);
    lane.addEventListener('pointercancel', onCancel, true);
    document.addEventListener('keydown', onKey);
    mo = new MutationObserver(scheduleLayout);
    mo.observe(lane, { childList: true, subtree: true, attributes: true, attributeFilter: ['style'] });
    tl.on('change', warm);
    tl.on('zoom', scheduleLayout);
    warm();
    layout();
    tl.trim = {
      FRAME,
      get active() { return active; },
      get dragging() { return drag ? drag.kind : null; },
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
