/* Roughcut — the timeline's lanes and drag and drop (INTAKE M9, I9.4).
 *
 * Built ON the foundation (timeline.js, `window.tl`): nothing here edits the foundation
 * or app.js. The lane hangs its own elements inside `tl.el.canvas` beside V1 and moves
 * V1 down (`--tl-above`) or grows the view (`--tl-below`) through CSS variables the
 * rules at the end of timeline.css read. Every edit goes through the foundation's API
 * (`tl.begin` / `tl.move` / `tl.insert` / `tl.commit`), so it is one undo entry and it
 * reaches the autosave the same way a trim does.
 *
 *   heroes    (INTAKE M16 I16.4: the markers lane above V1 and its legend are gone) a
 *             block holding a hero keep wears `.hero`, and the block's name a ★.
 *   V1        the foundation's. This file owns the BODY drag: a pointerdown on a block
 *             that is not on a handle, not within 8 px of an edge and has no alt key is a
 *             move (the trim lane owns edges and alt-drag). A drop line shows where the
 *             shot(s) land, snapping to cut points; under 4 px it is the foundation's click.
 *             Not with the razor or the slip tool (`tl.state.tool`): those cut and slip.
 *   ghost     while a proposal is pending (the diff panel is up): the proposed timeline
 *             under V1 aligned by ITS film time — unchanged shots dim, added ones green,
 *             moved ones with an arrow from where they are now, removed ones struck out on
 *             V1. Click a ghost → the monitor plays that range. The whole proposal plays
 *             from the panel's ▶ Play it (`playPlan`) and the cut from the monitor's own
 *             ▶ / space — the lane's "play proposal / play cut" buttons said both again
 *             (INTAKE M16 review). Ghost blocks are named like V1's (`tl.blockName`:
 *             the first words, a slide by its kind), not by the camera's file and a
 *             length. Accept and discard stay the panel's buttons.
 *             The same drawing on request (INTAKE M13): `tlLanes.showGhost(segments)`
 *             draws an explicit list as the ghost — the FX card's preview of an edit
 *             before Accept — and `tlLanes.clearGhost()` takes it down. A segment whose
 *             id is `new:n` (a shot the edit would make) or whose clip is not in the cut
 *             is drawn as added; `playPlan` plays it, at each shot's `speed`. While an
 *             explicit list is up it wins over a pending Ask's plan.
 *
 * Film time everywhere here is `tl.dur(seg)` — `(out − in) / speed` — never `out − in`,
 * which is a clip length (INTAKE M13). A clip offset inside a shot (a keep's start, a
 * speech region's edge, an event) maps to film as `(t − in) / speed`.
 *   A1        the music bed: the track's name, a bar the length of the film, the fades as
 *             ramps, a dip under every speech region in the cut (the same `speechRegions`
 *             the monitor ducks by) and the monitor's own `bedGainAt` curve on top. It
 *             draws, never edits (INTAKE decision 5): a click scrolls the music panel in.
 *   bin       the pass's keeps NOT in the cut as faint outlines after the last shot of
 *             their clip (else after the end), width to length — drag one onto V1. No
 *             label and no clip name on them (M16): an outline is a drag path, the
 *             tooltip says what it is.
 *
 * Drop from outside: a kept row, a Find result or an `available` outline dragged onto
 * the timeline carries `application/x-roughcut-shot` = `{clip, start, end, why}` and
 * lands at the drop line (`tl.insert`, then `tl.move` before the shot at the line — one
 * `insert` entry). Past the end of the film it appends.
 *
 * Reaching the board: app.js's top-level `let`s and functions (`bin`, `music`,
 * `pendingPlan`, `P`, `speechRegions`, `bedGainAt`, `shotOf`, `keepsUsable`, `binOrder`,
 * `pauseCut`, `arm`, `liveVideo`…) are global bindings, read by name at call time and
 * guarded, so the module is inert on a page without the board. The foundation has no
 * "mounted" hook and its `render` is only observable when called through `tl.render`,
 * so both are wrapped here (never edited) and a light poll catches what neither sees —
 * a bin re-read, a music change, the proposal panel closing.
 */
(function () {
  'use strict';

  const tl = window.tl;
  if (!tl) return;

  const MIME = 'application/x-roughcut-shot';
  const EDGE = 8;                      // px at a block's edge that belong to the trim lane
  const CLICK = 4;                     // px under which a pointer drag is the foundation's click
  const POLL_MS = 500;
  const H = { ghost: 40, bin: 22, gap: 2 };   // gap: the foundation's ROW_GAP between rows
  const GHOST_MIN = 80;                // the ghost lane is V1's height, never under this (verdict 4)

  const el = { ghost: null, ghostHead: null, a1: null, bin: null, over: null, arrows: null, drop: null };
  let adrag = null;                    // a drag on A1 (the pair, or an unlinked sound alone)
  let odrag = null;                    // a drag on V2 (a clip laid over the cut)
  let mounted = false;
  let drag = null;                     // the body drag on V1, from pointerdown to pointerup
  let holdRedraw = false;              // an outline is pressed or dragged: rebuilding would end the drag
  let dragActive = false;              // a native drag from one of the lanes is in flight
  let raf = 0;                         // one redraw per frame, however many triggers
  let rangeGen = 0;                    // proposal playback token
  let mode = 'cut';                    // the ghost lane's toggle
  let seen = {};                       // what the poll last saw
  let ghost = null;                    // the matched proposal, while one is pending
  let shown = null;                    // an explicit plan from showGhost(), while one is up
  let shownGen = 0;                    // bumps per showGhost / clearGhost, for the poll
  // the per-shot review of the pending proposal (verdict 3, punch 7): which proposed
  // shots are rejected (by index) and which removals are refused (by the cut's shot id)
  let review = { plan: null, rej: new Set(), keep: new Set() };

  const q = (s) => document.querySelector(s);
  const round2 = (x) => Math.round(x * 100) / 100;
  const stemOf = (clip) => String(clip).replace(/\.[^.]+$/, '');
  const isGen = (clip) => String(clip).startsWith('gen_');
  const nameOf = (clip) => (isGen(clip) ? ((/^gen_([a-z]+)_/i.exec(String(clip)) || [])[1] || 'generated') : stemOf(clip));
  const spd = (seg) => tl.speedOf(seg);
  /* a clip time inside a shot → film time */
  const toFilm = (seg, t) => tl.filmStart(seg.id) + Math.max(0, Math.min(tl.dur(seg), (t - seg.in) / spd(seg)));
  const overlap = (a, b) => {
    const shorter = Math.max(1e-6, Math.min(a[1] - a[0], b[1] - b[0]));
    return Math.max(0, Math.min(a[1], b[1]) - Math.max(a[0], b[0])) / shorter;
  };

  /* app.js's state, by name, at call time — null on a page without the board. */
  function app() {
    try {
      return { P, segs, bin, music, pendingPlan, findSel, player };   // eslint-disable-line no-undef
    } catch (err) { return null; }
  }
  const fn = (name) => {
    try { const f = (0, eval)(name); return typeof f === 'function' ? f : null; } catch (err) { return null; }
  };

  function div(cls) { const d = document.createElement('div'); d.className = cls; return d; }
  function px(n) { return `${n}px`; }

  /* ------------------------------------------------------------ layout */
  /* The rows are the foundation's (`tl.layoutTracks`): the ghost goes under V1, the bin
   * outlines under A2, every row sized from the panel's height. The strike-outs and the
   * arrows sit over V1. */
  function layout() {
    // A first cut on an empty sequence (verdict 5, punch 3): the ghost is drawn in V1's
    // own row, where Accept will put it, not under an empty V1 — the foundation lays the
    // rows out without a ghost row and the lane takes V1's place.
    const inV1 = ghostInV1();
    if (inV1) el.ghost.hidden = true;
    if (tl.layoutTracks) tl.layoutTracks();
    if (inV1) el.ghost.hidden = false;
    placeGhostInV1(inV1);
    el.over.style.top = 'var(--tl-v1top)';
    el.arrows.style.top = el.over.style.top;
    if (el.picks) el.picks.style.top = el.over.style.top;
  }

  function ghostInV1() {
    return !!(el.ghost && ghost && !el.ghost.hidden && !tl.state.segs.length);
  }

  function placeGhostInV1(on) {
    const v1 = tl.el.lanes.V1;
    const head = tl.el.heads && tl.el.heads.V1;
    if (head) head.style.visibility = on ? 'hidden' : '';
    el.ghost.classList.toggle('in-v1', on);
    if (!on || !v1) return;
    el.ghost.style.top = v1.style.top;
    el.ghost.style.height = v1.style.height;
    if (el.ghostHead) {
      el.ghostHead.style.top = v1.style.top;
      el.ghostHead.style.height = v1.style.height;
    }
  }

  /* The review controls' and the pick markers' look, shipped with the lane that draws
   * them (the timeline's stylesheet is the foundation's). */
  const CSS = `
#tl.tl-empty.tl-proposing { display: block; }
.tl-proposing > #tlEmpty { display: none; }
#tl .tl-xlane[data-lane=ghost] .ghost:focus { outline: 2px solid var(--accent); outline-offset: -2px; opacity: 1; }
#tl .tl-xlane[data-lane=ghost] .ghost .rvs { position: absolute; right: 2px; bottom: 2px; display: flex; gap: 2px; z-index: 2; }
#tl .tl-xlane[data-lane=ghost] .ghost.narrow .rvs { flex-direction: column; }
#tl .tl-xlane[data-lane=ghost] .ghost.tiny .rvs { display: none; }
#tl .tl-xlane[data-lane=ghost] .ghost.tiny:focus .rvs, #tl .tl-xlane[data-lane=ghost] .ghost.tiny:hover .rvs { display: flex; z-index: 3; }
#tl .tl-xlane[data-lane=ghost] .ghost { padding: 0; background: hsl(var(--hue) 12% 9%); opacity: .62; }
#tl .tl-xlane[data-lane=ghost] .ghost.added, #tl .tl-xlane[data-lane=ghost] .ghost.moved { opacity: .92; }
#tl .tl-xlane[data-lane=ghost] .ghost .gstrip { position: absolute; inset: 0; display: flex; overflow: hidden; pointer-events: none; }
#tl .tl-xlane[data-lane=ghost] .ghost .gstrip img { flex: none; height: 100%; object-fit: cover; display: block; background: #000; }
#tl .tl-xlane[data-lane=ghost] .ghost .gtxt { position: absolute; left: 0; right: 0; top: 0; z-index: 1; padding: 1px 5px 3px;
  line-height: 14px; font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  background: linear-gradient(rgba(10, 10, 14, .85), rgba(10, 10, 14, .55) 70%, transparent); }
#tl .tl-ghead { position: absolute; left: 0; right: 0; display: flex; flex-direction: column; justify-content: center;
  padding: 0 7px; box-sizing: border-box; overflow: hidden; pointer-events: auto; background: #1a1d1b;
  border-left: 2px solid var(--good); border-top: 1px solid #22222a; font-size: 11px; line-height: 15px; }
#tl .tl-ghead b { color: var(--text); font-weight: 600; }
#tl .tl-ghead span { color: var(--dim); font-size: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#tl .tl-xlane button.rv { font-size: 12px; font-weight: 700; line-height: 16px; width: 20px; height: 18px; padding: 0; margin: 0;
  border-radius: 3px; border: 1px solid var(--line); background: rgba(13, 13, 17, .9); color: var(--dim); cursor: pointer; }
#tl .tl-xlane button.rv.ok[aria-pressed=true] { color: var(--good); border-color: var(--good); }
#tl .tl-xlane button.rv.no[aria-pressed=true] { color: var(--bad); border-color: var(--bad); }
#tl .tl-xlane[data-lane=ghost] .ghost.rejected { opacity: .28; border-style: dashed; border-color: var(--bad); }
#tl .tl-xlane[data-lane=ghost] .ghost.rejected .name { text-decoration: line-through; }
#tl .tl-xlane[data-lane=ghost] .ghost.rejected:focus, #tl .tl-xlane[data-lane=ghost] .ghost.rejected:hover { opacity: .6; }
#tl .tl-xlane[data-lane=ghost] .ghost .flag { float: right; font-style: normal; font-weight: 700; color: #111;
  background: var(--warn); border-radius: 2px; padding: 0 3px; margin-left: 3px; line-height: 12px; }
#tl .tl-xlane[data-lane=ghost] .ghost.flagged { box-shadow: inset 0 -2px 0 var(--warn); }
#tl .tl-over .strike button.rv { pointer-events: auto; position: absolute; right: 2px; top: 2px; font-size: 10px;
  line-height: 13px; width: 16px; height: 15px; padding: 0; border-radius: 3px; border: 1px solid var(--bad);
  background: rgba(13, 13, 17, .85); color: var(--bad); cursor: pointer; }
#tl .tl-over .strike.kept { border-style: dashed; background: none; }
#tl .tl-over .strike.kept button.rv { color: var(--good); border-color: var(--good); }
#tl .tl-over .strike.flagged { box-shadow: inset 0 -2px 0 var(--warn); }
#tl .tl-picks { position: absolute; left: 0; right: 0; height: 0; pointer-events: none; z-index: 4; }
#tl .tl-xlane[data-lane=ghost].in-v1 { z-index: 3; }
/* under the name band (the label's 1 + 14 + 3 px inside the ghost's 1 px border, and a
   pixel clear), never over a shot's name — verdict 5 read "C…" where markers covered the
   ghosts' names */
#tl .tl-pick { position: absolute; top: 20px; width: 9px; height: 10px; margin-left: -4.5px; pointer-events: auto;
  background: #e0a63a; cursor: help; clip-path: polygon(0 0, 100% 0, 100% 60%, 50% 100%, 0 60%); }
#tl .tl-pick.hero { background: #f2c94c; }
#tl .tl-pick.on-ghost { z-index: 2; }
`;
  function injectCss() {
    if (document.getElementById('tl-review-css')) return;
    const st = document.createElement('style');
    st.id = 'tl-review-css';
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  function schedule() {
    if (raf) return;
    raf = requestAnimationFrame(() => { raf = 0; redraw(); });
  }

  function redraw() {
    if (!mounted || holdRedraw) return;
    const a = app();
    if (!a) return;
    drawHeroes(a);
    drawA1(a);
    drawGhost(a);
    drawPicks(a);
    layout();
    seen = snapshot(a);
  }

  const snapshot = (a) => ({
    bin: a.bin, music: a.music, plan: a.pendingPlan, zoom: tl.state.zoom, total: tl.total(),
    clips: a.P ? a.P.clips : null,
    up: q('#proposal') ? q('#proposal').style.display : '',
    n: tl.state.segs.length, shown: shownGen,
  });

  function poll() {
    const a = app();
    if (!a) return;
    const now = snapshot(a);
    for (const k of Object.keys(now)) if (now[k] !== seen[k]) { schedule(); return; }
  }

  /* ------------------------------------------------------------ heroes + bin */
  function drawHeroes(a) {
    const list = tl.state.segs;
    const tot = tl.total();
    const z = tl.state.zoom;
    const keepsUsable = fn('keepsUsable'), shotOf = fn('shotOf'), binOrder = fn('binOrder');
    const keeps = keepsUsable && binOrder ? binOrder(keepsUsable()) : [];
    const heroIds = new Set(), avail = [];
    for (const k of keeps) {
      const i = shotOf ? shotOf(k) : -1;
      if (i < 0) { avail.push(k); continue; }
      if (k.hero && list[i]) heroIds.add(list[i].id);
    }
    tl.el.lanes.V1.querySelectorAll('.blk').forEach((b) => {
      b.classList.toggle('hero', heroIds.has(b.dataset.id));
    });

    // the pass's keeps not in the cut: after the last shot of their clip, else after the end
    el.bin.hidden = !list.length || !avail.length;
    el.bin.replaceChildren();
    const lastEnd = new Map();
    let start = 0;
    for (const seg of list) { start += tl.dur(seg); lastEnd.set(seg.clip, start); }
    const cursor = new Map();
    for (const k of avail) {
      const anchor = lastEnd.has(k.clip) ? lastEnd.get(k.clip) : tot;
      const at = cursor.has(anchor) ? cursor.get(anchor) : anchor;
      const len = Math.max(0, k.end - k.start);
      const d = div('avail');
      d.draggable = true;
      d.style.left = px(tl.timeToX(at));
      d.style.width = px(Math.max(6, len * z));
      d.style.setProperty('--hue', tl.hueOf(k.clip));
      d.textContent = k.hero ? '★' : '';
      d.title = `kept, not in the cut · ${len.toFixed(1)} s — drag onto the timeline to add it`
        + (k.note || k.why ? `\n${k.note || k.why}` : '');
      d._keep = k;
      el.bin.appendChild(d);
      cursor.set(anchor, at + len);
    }
  }

  /* ------------------------------------------------------------ A2: the music bed */
  /* The bed lives on A2 (the tracks: A1 is each shot's own sound now). The foundation
   * owns the row and its header; this draws inside it. */
  function drawA1(a) {
    const m = a.music;
    const list = tl.state.segs;
    const tot = tl.total();
    const z = tl.state.zoom;
    const lane = el.a1;
    lane.replaceChildren();
    if (!m || !list.length || tot <= 0) { lane.classList.remove('has-bed'); return; }
    lane.classList.add('has-bed');
    lane.h = lane.offsetHeight || 30;
    const trackInfo = fn('trackInfo');
    const t = trackInfo ? trackInfo() : null;

    const bed = div('bed');
    bed.style.left = px(tl.timeToX(0));
    bed.style.width = px(tot * z);
    const name = document.createElement('span');
    name.className = 'bedname';
    name.textContent = `♪ ${(t && t.name) || m.asset}`
      + (m.duck ? ` · ducks −${m.duck_db} dB under speech` : ' · no duck');
    bed.appendChild(name);
    bed.title = `A2 · the music bed: ${(t && t.name) || m.asset} — click for its settings`;
    lane.appendChild(bed);

    // the dips: every speech region of every shot, mapped to film time — the same
    // regions bedGainAt() ducks by in the monitor, the depth the asked dB as a gain ratio
    const speechRegions = fn('speechRegions');
    if (m.duck && speechRegions) {
      const depth = 1 - Math.pow(10, -(m.duck_db || 0) / 20);
      let start = 0;
      for (const seg of list) {
        const s = spd(seg);
        for (const [lo, hi] of speechRegions(seg.clip)) {
          const a0 = Math.max(lo, seg.in), b0 = Math.min(hi, seg.out);
          if (b0 <= a0) continue;
          const d = div('duck');
          d.style.left = px(tl.timeToX(start + (a0 - seg.in) / s));
          d.style.width = px((b0 - a0) / s * z);
          d.style.height = `${(depth * 100).toFixed(1)}%`;
          d.title = `duck −${m.duck_db} dB · ${stemOf(seg.clip)} ${tl.fmt(a0)}–${tl.fmt(b0)}`;
          lane.appendChild(d);
        }
        start += tl.dur(seg);
      }
    }

    // the fades as ramps
    if (m.fade_in > 0) {
      const f = div('fade in');
      f.style.left = px(tl.timeToX(0));
      f.style.width = px(Math.min(m.fade_in, tot) * z);
      f.title = `fade in ${m.fade_in}s`;
      lane.appendChild(f);
    }
    if (m.fade_out > 0) {
      const f = div('fade out');
      const len = Math.min(m.fade_out, tot);
      f.style.left = px(tl.timeToX(tot - len));
      f.style.width = px(len * z);
      f.title = `fade out ${m.fade_out}s`;
      lane.appendChild(f);
    }

    // the monitor's own level curve, sampled along the film
    const bedGainAt = fn('bedGainAt');
    if (bedGainAt && t) {
      const w = tot * z;
      const n = Math.max(24, Math.min(900, Math.round(w / 3)));
      const gains = [];
      for (let k = 0; k < n; k++) {
        const filmT = tot * k / (n - 1);
        const at = tl.shotAt(filmT);
        const seg = at ? tl.byId(at.id) : null;
        gains.push(seg ? bedGainAt(filmT, at.clipT, seg) : 0);
      }
      const gmax = Math.max(1e-6, ...gains);
      const hgt = lane.h - 6;
      const pts = gains.map((g, k) => `${(tl.timeToX(tot * k / (n - 1))).toFixed(1)},${(3 + hgt - hgt * g / gmax).toFixed(1)}`);
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('class', 'curve');
      svg.setAttribute('width', String(Math.ceil(tl.timeToX(tot)) + 2));
      svg.setAttribute('height', String(lane.h));
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
      line.setAttribute('points', pts.join(' '));
      svg.appendChild(line);
      lane.appendChild(svg);
    }
  }

  /* ------------------------------------------------------------ the proposal ghost lane */
  function pendingPlanOf(a) {
    if (shown) return shown;             // an explicit list (showGhost) wins over the Ask's
    const p = a.pendingPlan;
    const box = q('#proposal');
    if (!p || !Array.isArray(p.segments) || !box || box.style.display === 'none') return null;
    return p;
  }

  /* Draw a proposed cut on the ghost lane on request — the FX card's preview of an edit
   * before Accept. `segments` is a whole cut: `{id?, clip, in, out, why?, speed?}` per
   * shot, ids `new:n` for shots that do not exist yet. One plan object per call, so the
   * lane's mode resets when a new list comes and holds while the same one is redrawn. */
  function showGhost(segments) {
    if (!Array.isArray(segments)) return false;
    shown = { segments: segments.map((s) => ({ ...s })), explicit: true };
    shownGen++;
    schedule();
    return true;
  }

  function clearGhost() {
    if (!shown) return false;
    shown = null;
    shownGen++;
    schedule();
    return true;
  }

  /* Longest increasing subsequence: the positions (in `seq`) that keep their order. */
  function lis(seq) {
    const n = seq.length;
    const out = new Set();
    if (!n) return out;
    const len = new Array(n).fill(1), prev = new Array(n).fill(-1);
    let best = 0;
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < i; j++) {
        if (seq[j] < seq[i] && len[j] + 1 > len[i]) { len[i] = len[j] + 1; prev[i] = j; }
      }
      if (len[i] > len[best]) best = i;
    }
    for (let i = best; i >= 0; i = prev[i]) out.add(i);
    return out;
  }

  /* Match the plan's shots to the cut's: by id when the plan carries one, else by clip
   * and overlap ≥ 0.5 (the kept tab's rule), each shot of the cut claimed once. */
  function matchPlan(plan) {
    const cur = tl.state.segs;
    const used = new Set();
    const pairs = plan.segments.map((s) => {
      // a `new:n` id is a shot the edit would make: never one of the cut's, whatever
      // footage it takes (a still of a shot overlaps that shot's range by construction)
      const fresh = typeof s.id === 'string' && s.id.startsWith('new:');
      let i = s.id && !fresh ? tl.indexOf(s.id) : -1;
      if (i >= 0 && used.has(i)) i = -1;
      if (i < 0 && !fresh) {
        let bestR = 0.5;
        cur.forEach((c, k) => {
          if (used.has(k) || c.clip !== s.clip) return;
          const r = overlap([s.in, s.out], [c.in, c.out]);
          if (r >= bestR) { bestR = r; i = k; }
        });
      }
      if (i >= 0) used.add(i);
      return i;
    });
    const matchedPos = pairs.map((i, k) => (i >= 0 ? k : -1)).filter((k) => k >= 0);
    const stay = lis(matchedPos.map((k) => pairs[k]));
    const ghosts = [];
    let start = 0;
    plan.segments.forEach((s, k) => {
      const i = pairs[k];
      const pos = matchedPos.indexOf(k);
      const c = i >= 0 ? cur[i] : null;
      ghosts.push({
        seg: s, k, start, cur: i,
        cls: i < 0 ? 'added' : stay.has(pos) ? 'same' : 'moved',
        trimmed: !!c && (Math.abs(c.in - s.in) > 0.011 || Math.abs(c.out - s.out) > 0.011
                         || spd(c) !== spd(s)),
      });
      start += tl.dur(s);
    });
    const removed = cur.map((c, k) => k).filter((k) => !used.has(k));
    return { plan, ghosts, removed, total: start };
  }

  /* ------------------------------------------------------------ per-shot review */
  /* Verdict 3, punch 7: a proposal is reviewed shot by shot on the ghost lane — ✓ / ✗ on
   * every proposed shot (Enter / ⌫ on the focused one, ← → between them), a ↺ on every
   * shot the proposal would remove — and Accept applies only what was accepted, in one
   * undo (app.js acceptProposal reads `reviewed()`). Every shot starts accepted, so an
   * untouched review is the whole proposal. An explicit list (showGhost, the FX card's
   * preview) is not reviewed here: its Accept is the card's. */
  function reviewOf(plan) {
    if (review.plan !== plan) review = { plan, rej: new Set(), keep: new Set() };
    return review;
  }
  const reviewable = (plan) => !!plan && !plan.explicit && !plan.unchanged;
  const curKey = (c, i) => (c.id != null ? String(c.id) : `#${i}`);

  function announce() {
    try { window.dispatchEvent(new CustomEvent('tlreview')); } catch (err) { /* old engine */ }
  }

  /* Accept (true) or reject (false) proposed shot k of the pending plan. */
  function setVerdict(k, ok) {
    const a = app();
    const plan = a ? pendingPlanOf(a) : null;
    if (!reviewable(plan) || !(k >= 0 && k < plan.segments.length)) return false;
    const rv = reviewOf(plan);
    if (ok) rv.rej.delete(k); else rv.rej.add(k);
    paintVerdicts();
    announce();
    return true;
  }
  function verdict(k) {
    const a = app();
    const plan = a ? pendingPlanOf(a) : null;
    return !plan || !reviewOf(plan).rej.has(k);
  }
  /* Refuse (keep = true) or allow a removal of the cut's shot `id`. */
  function keepRemoved(id, keep) {
    const a = app();
    const plan = a ? pendingPlanOf(a) : null;
    if (!reviewable(plan)) return false;
    const rv = reviewOf(plan);
    if (keep) rv.keep.add(String(id)); else rv.keep.delete(String(id));
    paintVerdicts();
    announce();
    return true;
  }

  /* The cut an Accept of `plan` makes from the verdicts: accepted shots as proposed, in
   * the proposal's order; a rejected new shot left out; a rejected shot the proposal
   * changed in place stays as it is now; a rejected move and a refused removal stay
   * where they are in the cut now (after the nearest shot before them that is kept). */
  function reviewed(plan) {
    if (!plan || !Array.isArray(plan.segments)) return null;
    const m = matchPlan(plan);
    const rv = reviewOf(plan);
    const cur = tl.state.segs;
    const out = [];
    const back = [];
    for (const g of m.ghosts) {
      if (!rv.rej.has(g.k)) { out.push({ seg: { ...g.seg }, cur: g.cur }); continue; }
      if (g.cur < 0) continue;
      if (g.cls === 'moved') back.push(g.cur);
      else out.push({ seg: { ...cur[g.cur] }, cur: g.cur });
    }
    const kept = m.removed.filter((i) => rv.keep.has(curKey(cur[i], i)));
    back.push(...kept);
    back.sort((x, y) => x - y);
    for (const i of back) {
      let at = 0;
      for (let j = i - 1; j >= 0; j--) {
        const p = out.findIndex((o) => o.cur === j);
        if (p >= 0) { at = p + 1; break; }
      }
      out.splice(at, 0, { seg: { ...cur[i] }, cur: i });
    }
    const rejectedShots = [...rv.rej].filter((k) => k < m.ghosts.length).sort((x, y) => x - y);
    // Verdict 4: the ranges the editor turned down — a rejected shot whose range the cut
    // does not keep (a new one, or a change) — so a re-run remembers not to bring them
    // back. A rejected move or a kept-as-is shot is still in the cut: not a rejection.
    const segsOut = out.map((o) => o.seg);
    const inCut = (s) => segsOut.some((o) => o.clip === s.clip
      && Math.abs(o.in - s.in) < 0.005 && Math.abs(o.out - s.out) < 0.005);
    const rejectedRanges = rejectedShots.map((k) => plan.segments[k])
      .filter((s) => s && !inCut(s)).map((s) => ({ clip: s.clip, in: s.in, out: s.out }));
    reviewed.lastRanges = rejectedRanges;      // answerProposal sends them with the Accept
    return {
      segments: segsOut,
      total: m.ghosts.length, accepted: m.ghosts.length - rejectedShots.length,
      rejectedShots, keptRemoved: kept.map((i) => curKey(cur[i], i)),
      rejected: rejectedShots.length + kept.length, rejectedRanges,
    };
  }

  /* What proposed shot k is to the cut, in words, and the notes' claims it breaks:
   * the first pass's diff (kept / replaced / new, locked) else the plan's own `diff`,
   * and `notes_check.flags` matched by id, else by clip and range. */
  function shotFacts(plan, k) {
    const seg = plan && plan.segments ? plan.segments[k] : null;
    if (!seg) return null;
    const fp = plan.firstpass && plan.firstpass.diff && plan.firstpass.diff.shots;
    const d = (Array.isArray(fp) && fp[k]) || (plan.diff && Array.isArray(plan.diff.shots) && plan.diff.shots[k]) || null;
    let words = '';
    if (d) {
      const was = d.was ? `${tl.fmt(d.was.in)}–${tl.fmt(d.was.out)}` : '';
      words = d.status === 'kept' ? (d.locked ? 'kept — your edit, locked' : 'kept as it is')
        : d.status === 'new' ? 'new'
          : `replaces ${was || 'a shot of the cut'}`;
      if (d.hand && d.status !== 'kept') words += ` (you ${String(d.hand).replace(/,/g, ', ')} it)`;
    }
    const same = (s) => s && s.now && ((s.id && seg.id && s.id === seg.id)
      || (s.clip === seg.clip && Math.abs(s.now.in - seg.in) < 0.02 && Math.abs(s.now.out - seg.out) < 0.02));
    const flags = ((plan.notes_check && plan.notes_check.flags) || [])
      .filter((f) => (f.shots || []).some(same))
      .map((f) => `the notes say “${f.claim}” — ${f.why || 'but the range differs'}`);
    return { status: d ? d.status : null, words, flags, why: seg.why || '' };
  }
  /* The flags on a shot the plan removes (the cut's shot `id`). */
  function removedFlags(plan, c) {
    return ((plan.notes_check && plan.notes_check.flags) || [])
      .filter((f) => (f.shots || []).some((s) => s.now == null
        && ((s.id && c.id && s.id === c.id) || (s.clip === c.clip && Math.abs(s.was.in - c.in) < 0.02))))
      .map((f) => `the notes say “${f.claim}” — but this shot is removed`);
  }

  /* The verdicts on the drawn lane, in place (no rebuild: focus stays where it is). */
  function paintVerdicts() {
    if (!ghost) return;
    paintGhostHead(ghost.plan);
    const rv = reviewOf(ghost.plan);
    el.ghost.querySelectorAll('.ghost[data-k]').forEach((d) => {
      const no = rv.rej.has(Number(d.dataset.k));
      d.classList.toggle('rejected', no);
      const ok = d.querySelector('button.rv.ok'), x = d.querySelector('button.rv.no');
      if (ok) ok.setAttribute('aria-pressed', no ? 'false' : 'true');
      if (x) x.setAttribute('aria-pressed', no ? 'true' : 'false');
    });
    el.over.querySelectorAll('.strike[data-key]').forEach((s) => {
      const kept = rv.keep.has(s.dataset.key);
      s.classList.toggle('kept', kept);
      const b = s.querySelector('button.rv');
      if (b) {
        b.setAttribute('aria-pressed', kept ? 'true' : 'false');
        b.title = kept ? 'kept — click to let the proposal remove it' : 'keep this shot (refuse its removal)';
      }
    });
  }

  function focusGhost(k) {
    if (!ghost) return;
    const n = ghost.ghosts.length;
    const d = el.ghost.querySelector(`.ghost[data-k="${Math.max(0, Math.min(n - 1, k))}"]`);
    if (d) d.focus({ preventScroll: true });
  }

  /* Enter accepts the focused ghost, ⌫ / Delete rejects it (both then move on), ← → move
   * between them. On window, capture, registered before timeline-keys (whose Enter and
   * ⌫ are the cut's), and only while a ghost has the focus. */
  window.addEventListener('keydown', (e) => {
    const t = document.activeElement;
    const g = t && t.closest ? t.closest('#tl .tl-xlane[data-lane=ghost] .ghost[data-k]') : null;
    if (!g || !ghost || !reviewable(ghost.plan) || e.metaKey || e.ctrlKey || e.altKey) return;
    const k = Number(g.dataset.k);
    let handled = true;
    if (e.key === 'Enter') { setVerdict(k, true); focusGhost(k + 1); }
    else if (e.key === 'Backspace' || e.key === 'Delete') { setVerdict(k, false); focusGhost(k + 1); }
    else if (e.key === 'ArrowRight') focusGhost(k + 1);
    else if (e.key === 'ArrowLeft') focusGhost(k - 1);
    else handled = false;
    if (handled) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);

  /* The ghost lane is as tall as V1 (verdict 4, punch 4: "a thin 40-pixel strip of ghost
   * shots"): the foundation shares the view's height after the ghost's own among V2 0.8 ·
   * V1 1.5 · A1 1.15 · A2 0.75 (tl.layoutTracks), so a ghost of V1's height is
   * base × 1.5 / 5.7 — never under GHOST_MIN; a short panel scrolls instead. */
  function ghostHeight() {
    const view = tl.el.view ? tl.el.view.clientHeight : 0;
    const bin = el.bin && !el.bin.hidden ? (el.bin.h || 0) + H.gap : 0;
    const base = view - 26 - 4 * H.gap - 6 - bin;      // the ruler, the row gaps, the A/V gap
    return Math.max(GHOST_MIN, Math.floor(base * 1.5 / 5.7));
  }

  /* The lane's name in the track-header column, where V1's is — not over its first
   * shot — with what the review stands at. */
  function paintGhostHead(plan) {
    const h = el.ghostHead;
    if (!h) return;
    h.style.display = plan && ghost ? '' : 'none';
    if (!plan || !ghost) return;
    const n = ghost.ghosts.length;
    const rej = reviewable(plan) ? [...reviewOf(plan).rej].filter((k) => k < n).length : 0;
    const info = reviewable(plan) ? `${n - rej} of ${n} ✓ · ${tl.fmt(ghost.total)}` : `${n} shots · ${tl.fmt(ghost.total)}`;
    h.title = 'the proposed cut, under V1 — ✓ / ✗ each shot, then Accept in the Assistant';
    if (h.lastChild.textContent !== info) h.lastChild.textContent = info;
  }

  /* A ghost's filmstrip, as V1's (timeline.js paintStrip): the head frame, then one every
   * tile's width, edge to edge, from the clip's poster endpoint. */
  function ghostStrip(seg, w, hgt, a) {
    const strip = div('gstrip');
    const clip = a && a.P && a.P.clips ? a.P.clips[seg.clip] : null;
    const poster = clip && clip.poster;
    if (!poster) return strip;
    const tileW = Math.max(24, Math.round(hgt * 16 / 9));
    const n = Math.max(1, Math.min(48, Math.ceil(w / tileW)));
    const tw = n === 48 ? Math.ceil(w / n) : tileW;
    const tileS = (tw / tl.state.zoom) * spd(seg);
    const q = tileS >= 1 ? 1 : 0.25;
    const sep = poster.includes('?') ? '&' : '?';
    for (let k = 0; k < n; k++) {
      const t = k === 0 ? seg.in
        : Math.max(seg.in, Math.round(Math.min(seg.out - 0.05, seg.in + k * tileS) / q) * q);
      const img = document.createElement('img');
      img.draggable = false; img.loading = 'lazy'; img.decoding = 'async'; img.alt = '';
      img.style.width = px(tw);
      img.src = `${poster}${sep}t=${Math.max(0, t).toFixed(2)}`;
      strip.appendChild(img);
    }
    return strip;
  }

  function drawGhost(a) {
    const plan = pendingPlanOf(a);
    const lane = el.ghost;
    const af = document.activeElement;
    const hadK = af && lane.contains(af) && af.closest('.ghost[data-k]') ? Number(af.closest('.ghost[data-k]').dataset.k) : null;
    lane.replaceChildren();              // its name is in the header column (paintGhostHead)
    el.over.replaceChildren();
    el.arrows.replaceChildren();
    // A first cut's proposal on an empty sequence: the timeline shows (with only the
    // ghost lane) in place of the empty state, so it is reviewed where it will land.
    tl.el.root.classList.toggle('tl-proposing', !!plan);
    if (tl.el.root.parentElement) tl.el.root.parentElement.classList.toggle('tl-proposing', !!plan);
    if (!plan) {
      if (ghost) stopRange();
      ghost = null;
      lane.hidden = true;
      paintGhostHead(null);
      return;
    }
    const fresh = !ghost || ghost.plan !== plan;
    if (fresh) mode = 'cut';
    ghost = matchPlan(plan);
    // a first cut on an empty sequence: zoomed so the whole proposal is in view (the
    // redraw the zoom asks for finds the same plan and does not zoom again)
    if (fresh && !tl.state.segs.length && ghost.total > 0 && tl.zoomTo && tl.el.view) {
      const w = tl.el.view.clientWidth || 0;
      if (w > 200) tl.zoomTo(Math.max(0.5, Math.min(400, (w - 12 - 90) / ghost.total)), { at: 0, viewX: 0 });
    }
    // ... and its first frame in the Program monitor, not a black screen (verdict 5)
    if (fresh && !tl.state.segs.length && ghost.ghosts.length) cueRange(ghost.ghosts[0].seg, ghost.ghosts[0].start);
    lane.hidden = false;
    lane.h = ghostHeight();
    if (lane.style.height !== px(lane.h)) lane.style.height = px(lane.h);
    const z = tl.state.zoom;
    const cur = tl.state.segs;
    const laneH = tl.el.lanes.V1.offsetHeight || 72;
    const canReview = reviewable(plan);
    lane.classList.toggle('reviewing', canReview);

    for (const g of ghost.ghosts) {
      const d = div(`ghost ${g.cls}${g.trimmed ? ' trimmed' : ''}${isGen(g.seg.clip) ? ' gen' : ''}`);
      d.dataset.k = g.k;
      if (typeof g.seg.id === 'string') d.dataset.id = g.seg.id;
      const dur = tl.dur(g.seg);
      const s = spd(g.seg);
      d.style.left = px(tl.timeToX(g.start));
      d.style.width = px(Math.max(3, dur * z));
      d.style.setProperty('--hue', tl.hueOf(g.seg.clip));
      const a = app();
      d.appendChild(ghostStrip(g.seg, Math.max(3, dur * z), lane.h, a));
      const txt = div('gtxt');
      txt.innerHTML = '<span class="name"></span><span class="dur"></span>';
      d.appendChild(txt);
      d.querySelector('.name').textContent = tl.blockName(g.seg, a && a.P && a.P.clips ? a.P.clips[g.seg.clip] || null : null);
      // like V1: a badge only when the shot is retimed; the length is the tooltip's
      d.querySelector('.dur').textContent = s === 1 ? '' : `${round2(s)}×`;
      const what = g.cls === 'added' ? 'added' : g.cls === 'moved' ? 'moved' : g.trimmed ? 'trimmed' : 'unchanged';
      const f = canReview ? shotFacts(plan, g.k) : null;
      d.title = `proposal ${g.k + 1}. ${nameOf(g.seg.clip)} ${tl.fmt(g.seg.in)}–${tl.fmt(g.seg.out)} (${dur.toFixed(1)}s`
        + `${s === 1 ? '' : ` at ${round2(s)}×`}) · ${what}${f && f.words ? ` · ${f.words}` : ''}`
        + (g.seg.why ? `\nwhy: ${g.seg.why}` : '')
        + (g.seg.polished_from ? `\npolished from ${tl.fmt(g.seg.polished_from[0])}–${tl.fmt(g.seg.polished_from[1])}${g.seg.polish_why ? `: ${g.seg.polish_why}` : ''}` : '')
        + (f && f.flags.length ? `\n⚠ ${f.flags.join('\n⚠ ')}` : '')
        + '\nclick to play this shot in the monitor'
        + (canReview ? ' · ✓ / Enter accepts it, ✗ / ⌫ rejects it' : '');
      if (canReview) {
        d.tabIndex = 0;
        if (f && f.flags.length) {
          d.classList.add('flagged');
          txt.insertAdjacentHTML('afterbegin', '<i class="flag" aria-label="the notes disagree with this shot">!</i>');
        }
        const rv = document.createElement('span');
        rv.className = 'rvs';
        rv.innerHTML = '<button type="button" class="rv ok" aria-pressed="true" title="accept this shot · Enter">✓</button>'
          + '<button type="button" class="rv no" aria-pressed="false" title="reject this shot · ⌫">✗</button>';
        d.appendChild(rv);
        if (dur * z < 46) d.classList.add('narrow');       // ✓ over ✗, still readable
        if (dur * z < 24) d.classList.add('tiny');         // on hover / focus only
      }
      lane.appendChild(d);
      if (g.cls === 'moved' && g.cur >= 0) {
        const c = cur[g.cur];
        const x0 = tl.timeToX(tl.filmStart(c.id) + tl.dur(c) / 2);
        const x1 = tl.timeToX(g.start + dur / 2);
        const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.setAttribute('class', 'arrow');
        line.setAttribute('x1', x0.toFixed(1)); line.setAttribute('y1', String(laneH - 3));
        line.setAttribute('x2', x1.toFixed(1)); line.setAttribute('y2', String(laneH + H.gap + 3));
        line.setAttribute('marker-end', 'url(#tl-arrowhead)');
        el.arrows.appendChild(line);
      }
    }
    for (const k of ghost.removed) {
      const c = cur[k];
      const s = div('strike');
      s.style.left = px(tl.timeToX(tl.filmStart(c.id)));
      s.style.width = px(Math.max(3, tl.dur(c) * z));
      const flags = canReview ? removedFlags(plan, c) : [];
      s.title = `${stemOf(c.clip)} — removed by the proposal${flags.length ? `\n⚠ ${flags.join('\n⚠ ')}` : ''}`;
      if (canReview) {
        s.dataset.key = curKey(c, k);
        if (flags.length) s.classList.add('flagged');
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'rv keep';
        b.textContent = '↺';
        b.setAttribute('aria-pressed', 'false');
        b.title = 'keep this shot (refuse its removal)';
        b.addEventListener('click', (e) => {
          e.stopPropagation();
          keepRemoved(s.dataset.key, !reviewOf(plan).keep.has(s.dataset.key));
        });
        b.addEventListener('pointerdown', (e) => e.stopPropagation());
        s.appendChild(b);
      }
      el.over.appendChild(s);
    }
    el.arrows.setAttribute('width', String(Math.ceil(tl.el.canvas.offsetWidth || 0)));
    el.arrows.setAttribute('height', String(laneH + H.gap + lane.h));
    if (!el.arrows.querySelector('defs')) el.arrows.appendChild(arrowDefs());
    drawGhostPicks(a, plan);
    if (canReview) {
      paintVerdicts();
      if (hadK != null) focusGhost(hadK);
    }
    paintGhostHead(plan);
    paintMode(a);
  }

  /* ------------------------------------------------------------ picks as markers */
  /* The pass's picks as markers on the cut (V1) and on the proposed cut (the ghost
   * lane): an amber flag where a pick starts inside a shot that uses its footage, the
   * pick's label and reason on hover (verdict 3, punch 7). The picks are the editor's
   * keeps and the first pass's own picks (`picksUsable`, verdict 4, punch 1) — the pass
   * found 81 and the cut showed 0 when only the keeps were read. Drawn, never saved:
   * the sequence markers (M) stay the editor's. */
  function picksIn(list, startOf) {
    const keepsUsable = fn('keepsUsable'), picksUsable = fn('picksUsable'), keepLabel = fn('keepLabel');
    const keeps = (keepsUsable ? keepsUsable() : []).concat(picksUsable ? picksUsable() : []);
    const out = [];
    list.forEach((seg, i) => {
      for (const k of keeps) {
        if (k.clip !== seg.clip || !(k.end > seg.in && k.start < seg.out)) continue;
        const at = startOf(i) + Math.max(0, (Math.max(k.start, seg.in) - seg.in) / spd(seg));
        const label = keepLabel ? keepLabel(k) : '';
        const who = k.pick ? ` · the first pass · ${k.kind || 'moment'}` : ' · yours';
        out.push({ t: at, keep: k,
                   title: `pick${k.hero ? ' ★ hero' : ''}${who}: ${label}`
                     + `\n${nameOf(k.clip)} ${tl.fmt(k.start)}–${tl.fmt(k.end)}`
                     + (k.why ? `\nwhy: ${k.why}` : '') });
      }
    });
    return out;
  }

  function pickEl(p) {
    const m = document.createElement('i');
    m.className = `tl-pick${p.keep.hero ? ' hero' : ''}${p.keep.pick ? ' pass' : ' yours'}`;
    m.style.left = px(tl.timeToX(p.t));
    m.dataset.t = String(round2(p.t));
    m.title = p.title;
    return m;
  }

  function drawPicks() {
    if (!el.picks) return;
    el.picks.replaceChildren();
    const list = tl.state.segs;
    let start = 0;
    const starts = list.map((s) => { const at = start; start += tl.dur(s); return at; });
    for (const p of picksIn(list, (i) => starts[i])) el.picks.appendChild(pickEl(p));
  }

  function drawGhostPicks(a, plan) {
    if (!ghost) return;
    const starts = ghost.ghosts.map((g) => g.start);
    for (const p of picksIn(plan.segments, (i) => starts[i])) {
      const m = pickEl(p);
      m.classList.add('on-ghost');
      el.ghost.appendChild(m);
    }
  }

  function arrowDefs() {
    const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
    defs.innerHTML = '<marker id="tl-arrowhead" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">'
      + '<path d="M0,0 L6,3 L0,6 z"></path></marker>';
    return defs;
  }

  /* What the monitor plays — the proposal or the cut (`tlLanes.mode()`): the cut again
   * the moment the monitor's own play takes over. */
  function paintMode(a) {
    if (a && a.player && a.player.playing) mode = 'cut';
  }

  /* Play one range — a proposal's shot, not necessarily in the cut — in the monitor,
   * paused at its end. The monitor's own play (space, ▶) takes over the moment it
   * starts: `player.playing` flips and this loop stops. */
  function playRange(seg, filmFrom, onEnd) {
    const a = app();
    const pauseCut = fn('pauseCut'), liveVideo = fn('liveVideo'), arm = fn('arm');
    const showLive = fn('showLive'), reveal = fn('revealMonitor');
    if (!a || !pauseCut || !liveVideo || !arm || !seg) return false;
    pauseCut();
    if (reveal) reveal();
    const v = liveVideo();
    if (!v) return false;
    arm(v, seg);
    if (showLive) showLive();
    v.muted = false;
    const token = ++rangeGen;
    const src = v.dataset.src;
    const what = q('#playingWhat');
    const s = spd(seg);
    if (what) what.textContent = `proposal · ${nameOf(seg.clip)} ${tl.fmt(seg.in)}–${tl.fmt(seg.out)}${s === 1 ? '' : ` · ${round2(s)}×`}`;
    const step = () => {
      if (token !== rangeGen || a.player.playing || v.dataset.src !== src) return;
      tl.setPlayhead(filmFrom + Math.max(0, v.currentTime - seg.in) / s, { reveal: false });
      if (v.currentTime >= seg.out - 0.04 || v.ended) {
        v.pause();
        if (onEnd) onEnd();
        return;
      }
      requestAnimationFrame(step);
    };
    const go = () => {
      if (token !== rangeGen) return;
      v.currentTime = seg.in;
      v.play().catch(() => {});
      step();
    };
    if (v.readyState >= 1) go(); else v.addEventListener('loadedmetadata', go, { once: true });
    return true;
  }

  /* Park the monitor on a proposal's shot without playing it: its first frame, with
   * a picture (readyState ≥ 2) — preload="metadata" alone can stop short of one, so the
   * parked time is seeked to again once the metadata is in. */
  function cueRange(seg, filmFrom) {
    const a = app();
    const pauseCut = fn('pauseCut'), liveVideo = fn('liveVideo'), arm = fn('arm'), showLive = fn('showLive');
    if (!a || !a.player || a.player.playing || !pauseCut || !liveVideo || !arm || !seg) return false;
    pauseCut();
    const v = liveVideo();
    if (!v) return false;
    rangeGen++;
    arm(v, seg);
    if (showLive) showLive();
    const src = v.dataset.src;
    const what = q('#playingWhat');
    if (what) what.textContent = `proposal · ${nameOf(seg.clip)} ${tl.fmt(seg.in)}–${tl.fmt(seg.out)}`;
    tl.setPlayhead(filmFrom || 0, { reveal: false });
    const kick = () => { if (v.dataset.src === src && v.readyState < 2) v.currentTime = seg.in; };
    if (v.readyState >= 1) kick(); else v.addEventListener('loadedmetadata', kick, { once: true });
    return true;
  }

  function stopRange() {
    rangeGen++;
    const liveVideo = fn('liveVideo');
    const a = app();
    const v = liveVideo ? liveVideo() : null;
    if (v && a && !a.player.playing) v.pause();
  }

  /* The whole proposal, shot after shot, from its k-th. */
  function playPlan(k = 0) {
    if (!ghost || k >= ghost.ghosts.length) { mode = 'cut'; paintMode(app()); return false; }
    mode = 'proposal';
    paintMode(null);
    const g = ghost.ghosts[k];
    return playRange(g.seg, g.start, () => playPlan(k + 1));
  }

  /* ------------------------------------------------------------ drop slots */
  /* The cut point nearest a film time, skipping the shots being moved: `{t, beforeId}`
   * — the shot the drop lands before, or null for the end of the film. */
  function slotAt(t, excluded) {
    const list = tl.state.segs;
    let best = null, start = 0;
    const consider = (tt, beforeId, index) => {
      const d = Math.abs(tt - t);
      if (!best || d < best.d) best = { d, t: tt, beforeId, index };
    };
    list.forEach((s, i) => {
      if (!excluded || !excluded.has(s.id)) consider(start, s.id, i);
      start += tl.dur(s);
    });
    consider(start, null, list.length);
    return best;
  }

  function showDrop(t) {
    el.drop.style.left = px(tl.timeToX(t));
    el.drop.style.display = 'block';
  }
  function hideDrop() { el.drop.style.display = 'none'; }

  /* One undo entry: append, then move before the shot at the line. */
  function insertAt(shot, beforeId) {
    if (!shot || !shot.clip || !(shot.end > shot.start)) return null;
    tl.begin('insert');
    const id = tl.insert({ clip: shot.clip, in: round2(shot.start), out: round2(shot.end), why: shot.why || '' }, null);
    if (id != null && beforeId != null) tl.move([id], beforeId);
    tl.commit();
    const toast = fn('toast');
    if (toast) toast(`added ${stemOf(shot.clip)} @ ${Number(shot.start).toFixed(1)}s`);
    return id;
  }

  /* ------------------------------------------------------------ the body drag on V1 */
  function onDown(e) {
    if (e.button !== 0 || e.altKey || drag) return;
    // the razor cuts on a click and the slip tool slips: neither moves the shot
    if (tl.state.tool === 'razor' || tl.state.tool === 'slip') return;
    const b = e.target.closest('.blk');
    if (!b || e.target.closest('[class*="handle"]')) return;
    const r = b.getBoundingClientRect();
    if (e.clientX - r.left < EDGE || r.right - e.clientX < EDGE) return;   // the trim lane's zone
    drag = { id: b.dataset.id, x: e.clientX, y: e.clientY, pid: e.pointerId, live: false, ids: null, slot: null };
  }

  function onMove(e) {
    if (!drag || e.pointerId !== drag.pid) return;
    if (!drag.live) {
      if (Math.abs(e.clientX - drag.x) <= CLICK && Math.abs(e.clientY - drag.y) <= CLICK) return;
      drag.live = true;
      const sel = tl.state.sel;
      drag.ids = sel.has(drag.id) && sel.size > 1
        ? tl.state.segs.filter((s) => sel.has(s.id)).map((s) => s.id)
        : [drag.id];
      if (!sel.has(drag.id)) tl.select([drag.id], { source: 'api' });
      try { tl.el.lanes.V1.setPointerCapture(drag.pid); } catch (err) { /* not pointer-capable */ }
      for (const id of drag.ids) {
        const b = tl.el.lanes.V1.querySelector(`.blk[data-id="${id}"]`);
        if (b) b.classList.add('dragging');
      }
    }
    drag.slot = slotAt(tl.eventTime(e), new Set(drag.ids));
    showDrop(drag.slot.t);
  }

  function endDrag(e, apply) {
    const d = drag;
    if (!d || (e && e.pointerId !== d.pid)) return;
    drag = null;
    hideDrop();
    if (!d.live) return;                                   // under 4 px: the foundation's click
    tl.el.lanes.V1.querySelectorAll('.blk.dragging').forEach((b) => b.classList.remove('dragging'));
    try { tl.el.lanes.V1.releasePointerCapture(d.pid); } catch (err) { /* already released */ }
    if (!apply || !d.slot) return;
    tl.begin('move');
    tl.move(d.ids, d.slot.beforeId);
    tl.commit();
  }

  /* ------------------------------------------------------------ A1 and V2 drags */
  /* A1. A press on a sound block: ⌥ and no travel links or unlinks the pair; a linked
   * sound dragged moves the pair (the drop line, as V1's body drag); an unlinked sound
   * dragged slides alone — its `a_off`, snapping back into sync within CLICK × 2 px and
   * to the frame grid — one `move audio` entry. Under CLICK px it is the foundation's
   * click (select, park). Not with the razor (it cuts) or the slip tool. */
  function onADown(e) {
    if (e.button !== 0 || adrag) return;
    if (tl.state.tool === 'razor' || tl.state.tool === 'slip') return;
    const b = e.target.closest('.ablk');
    if (!b) return;
    const seg = tl.byId(b.dataset.id);
    if (!seg) return;
    adrag = { id: seg.id, x: e.clientX, y: e.clientY, pid: e.pointerId, live: false, alt: e.altKey,
              linked: tl.isLinked(seg), off0: tl.aOff(seg), zoom: tl.state.zoom, slot: null, block: b };
    if (e.altKey) { e.stopPropagation(); e.preventDefault(); }
  }

  function onAMove(e) {
    const d = adrag;
    if (!d || e.pointerId !== d.pid) return;
    if (!d.live) {
      if (Math.abs(e.clientX - d.x) <= CLICK && Math.abs(e.clientY - d.y) <= CLICK) return;
      if (d.alt) return;                     // ⌥ is a click on A1, never a drag
      d.live = true;
      try { tl.el.tracks.A1.setPointerCapture(d.pid); } catch (err) { /* not pointer-capable */ }
      if (!tl.state.sel.has(d.id)) tl.select([d.id], { source: 'click' });
      if (!d.linked) tl.begin('move audio');
      d.block.classList.add('dragging');
    }
    e.stopPropagation();
    if (d.linked) {
      d.slot = slotAt(tl.eventTime(e) - d.off0, new Set([d.id]));
      showDrop(d.slot.t);
    } else {
      let off = d.off0 + (e.clientX - d.x) / d.zoom;
      if (Math.abs(off) * d.zoom < CLICK * 2) off = 0;      // back in sync
      tl.setAudioOffset(d.id, off);
    }
  }

  function endADrag(e, apply) {
    const d = adrag;
    if (!d || (e && e.pointerId !== d.pid)) return;
    adrag = null;
    hideDrop();
    if (d.alt && !d.live) {
      if (e) { e.stopPropagation(); e.preventDefault(); }
      if (apply) tl.toggleLink(d.id);
      return;
    }
    if (!d.live) return;                      // the foundation's click
    if (e) { e.stopPropagation(); e.preventDefault(); }
    d.block.classList.remove('dragging');
    try { tl.el.tracks.A1.releasePointerCapture(d.pid); } catch (err) { /* already released */ }
    if (d.linked) {
      if (apply && d.slot) { tl.begin('move'); tl.move([d.id], d.slot.beforeId); tl.commit(); }
    } else if (apply) tl.commit(); else tl.cancel();
  }

  /* V2: a clip over the cut drags along it, snapping its start to a cut point or the
   * playhead within CLICK × 2 px; one `move V2` entry. */
  function onODown(e) {
    if (e.button !== 0 || odrag) return;
    const b = e.target.closest('.oblk');
    if (!b) return;
    const o = tl.overlayById(b.dataset.id);
    if (!o) return;
    odrag = { id: o.id, x: e.clientX, y: e.clientY, pid: e.pointerId, live: false, at0: o.at,
              zoom: tl.state.zoom, at: o.at, block: b };
  }

  function onOMove(e) {
    const d = odrag;
    if (!d || e.pointerId !== d.pid) return;
    if (!d.live) {
      if (Math.abs(e.clientX - d.x) <= CLICK && Math.abs(e.clientY - d.y) <= CLICK) return;
      d.live = true;
      try { tl.el.tracks.V2.setPointerCapture(d.pid); } catch (err) { /* not pointer-capable */ }
      tl.selectOverlay(d.id);
    }
    e.stopPropagation();
    let at = Math.max(0, d.at0 + (e.clientX - d.x) / d.zoom);
    const near = [tl.state.playhead];
    let t = 0;
    for (const s of tl.state.segs) { near.push(t); t += tl.dur(s); }
    near.push(t);
    for (const c of near) if (Math.abs(c - at) * d.zoom < CLICK * 2) { at = c; break; }
    d.at = at;
    d.block.style.left = px(tl.timeToX(at));
    showDrop(at);
  }

  function endODrag(e, apply) {
    const d = odrag;
    if (!d || (e && e.pointerId !== d.pid)) return;
    odrag = null;
    hideDrop();
    if (!d.live) return;
    if (e) { e.stopPropagation(); e.preventDefault(); }
    try { tl.el.tracks.V2.releasePointerCapture(d.pid); } catch (err) { /* already released */ }
    if (apply) tl.moveOverlay(d.id, d.at);
    tl.render();
  }

  /* ------------------------------------------------------------ drop from outside */
  const carries = (e) => {
    const types = e.dataTransfer && e.dataTransfer.types;
    return !!types && Array.prototype.includes.call(types, MIME);
  };

  /* What a draggable row stands for. Kept rows and Find rows carry their data only in
   * app.js closures: a kept row is the n-th of binOrder(bin.selects) (renderKept's own
   * order); a Find row is loaded the way its click loads it, then read from `findSel`. */
  function shotFor(row) {
    const a = app();
    if (!a) return null;
    if (row.classList.contains('avail') && row._keep) {
      const k = row._keep;
      return { clip: k.clip, start: k.start, end: k.end, why: k.why || k.note || '' };
    }
    if (row.classList.contains('keep') && row._keep) {
      // The dock's bin (INTAKE M11) filters its rows, so a row's position says nothing
      // about which keep it is; the row carries the keep itself.
      const k = row._keep;
      if (k.missing || !a.P || !a.P.clips[k.clip]) return null;
      return { clip: k.clip, start: k.start, end: k.end, why: k.why || k.note || '' };
    }
    if (row.classList.contains('keep')) {
      const rows = [...document.querySelectorAll('#library .keep')];
      const binOrder = fn('binOrder');
      const list = binOrder ? binOrder((a.bin && a.bin.selects) || []) : [];
      const k = list[rows.indexOf(row)];
      if (!k || k.missing || !a.P || !a.P.clips[k.clip]) return null;
      return { clip: k.clip, start: k.start, end: k.end, why: k.why || k.note || '' };
    }
    if (row.classList.contains('cand') && row.closest('#findResults')) {
      if (typeof row.onclick === 'function') row.onclick();
      const m = app().findSel;
      if (!m || !m.clip) return null;
      const qEl = q('#findQ');
      return { clip: m.clip, start: m.start, end: m.end,
               why: m.what || `found: ${qEl ? qEl.value.trim() : ''}` };
    }
    return null;
  }

  function onDragStart(e) {
    // A row's mousedown also selects its shot, and the board smooth-scrolls to the card;
    // a page that scrolls under a drag in flight lands the drop somewhere else. Freeze it.
    window.scrollTo({ top: window.scrollY, left: window.scrollX, behavior: 'instant' });
    const t = e.target instanceof Element ? e.target : null;
    const row = t && t.closest('#library .keep, #findResults .cand, #tl .avail');
    if (!row) return;
    const shot = shotFor(row);
    if (!shot) return;
    e.dataTransfer.setData(MIME, JSON.stringify(shot));
    e.dataTransfer.effectAllowed = 'copyMove';
    row.classList.add('tl-dragsrc');
    // A redraw replaces the bin lane's outlines, and Chromium ends a drag whose source
    // leaves the DOM — so the lanes hold still until the drag is over.
    holdRedraw = true;
    dragActive = true;
    row.addEventListener('dragend', () => {
      row.classList.remove('tl-dragsrc');
      dragActive = false;
      holdRedraw = false;
      schedule();
    }, { once: true });
  }

  /* The drag source is decided at the press: an outline rebuilt between pointerdown
   * and the first move is a detached node and the drag never starts. Hold from the
   * press; a release without a drag lets the lanes go again. */
  function holdWhilePressed(e) {
    if (e.button !== 0 || !e.target.closest('.avail')) return;
    holdRedraw = true;
    document.addEventListener('pointerup', () => {
      if (dragActive) return;                       // dragend releases instead
      holdRedraw = false;
      schedule();
    }, { once: true });
  }

  function markDraggable() {
    document.querySelectorAll('#library .keep').forEach((row) => {
      row.draggable = !!row.querySelector('button.add, a.use');
    });
    document.querySelectorAll('#findResults .cand').forEach((row) => { row.draggable = true; });
  }

  function onDragOver(e) {
    if (!carries(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    showDrop(slotAt(tl.eventTime(e)).t);
  }

  function onDragLeave(e) {
    if (e.relatedTarget && tl.el.root.contains(e.relatedTarget)) return;
    hideDrop();
  }

  function onDrop(e) {
    if (!carries(e)) return;
    e.preventDefault();
    hideDrop();
    let shot = null;
    try { shot = JSON.parse(e.dataTransfer.getData(MIME)); } catch (err) { shot = null; }
    if (!shot) return;
    insertAt(shot, slotAt(tl.eventTime(e)).beforeId);
  }

  /* ------------------------------------------------------------ mount */
  function lane(name, h, legend) {
    const d = div('tl-xlane');
    d.dataset.lane = name;
    d.h = h;
    d.style.height = px(h);
    d.hidden = true;
    const lbl = document.createElement('span');
    lbl.className = 'lbl';
    if (legend) lbl.innerHTML = legend;
    d.lbl = lbl;
    d.appendChild(lbl);
    return d;
  }

  function setup() {
    if (mounted || !tl.el || !tl.el.canvas) return;
    mounted = true;
    const canvas = tl.el.canvas;
    el.ghost = lane('ghost', H.ghost, '');
    // its name in the header column, at its row (the foundation lays out `heads.ghost`);
    // not a .tl-thead — those are the four tracks' toggles
    const heads = tl.el.side && tl.el.side.querySelector('.tl-heads');
    if (heads && tl.el.heads) {
      el.ghostHead = div('tl-ghead');
      el.ghostHead.dataset.lane = 'ghost';
      el.ghostHead.innerHTML = '<b>Proposal</b><span></span>';
      el.ghostHead.style.display = 'none';
      heads.appendChild(el.ghostHead);
      tl.el.heads.ghost = el.ghostHead;
    }
    el.a1 = tl.el.tracks && tl.el.tracks.A2;      // the bed's row: the foundation's A2
    el.bin = lane('bin', H.bin, '');
    el.over = div('tl-over');
    el.arrows = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    el.arrows.setAttribute('class', 'tl-arrows');
    el.drop = div('tl-dropline');
    el.drop.style.display = 'none';
    el.picks = div('tl-picks');
    injectCss();
    for (const x of [el.ghost, el.bin, el.over, el.picks, el.arrows, el.drop]) canvas.appendChild(x);
    // the foundation lays these rows out with its tracks
    tl.el.lanes.ghost = el.ghost;
    tl.el.lanes.bin = el.bin;

    // V1: the body drag
    const v1 = tl.el.lanes.V1;
    v1.addEventListener('pointerdown', onDown);
    v1.addEventListener('pointermove', onMove);
    v1.addEventListener('pointerup', (e) => endDrag(e, true));
    v1.addEventListener('pointercancel', (e) => endDrag(e, false));
    // A1: a linked sound drags its pair (as V1's body does); an unlinked one moves alone
    const a1 = tl.el.tracks && tl.el.tracks.A1;
    if (a1) {
      a1.addEventListener('pointerdown', onADown, true);
      a1.addEventListener('pointermove', onAMove, true);
      a1.addEventListener('pointerup', (e) => endADrag(e, true), true);
      a1.addEventListener('pointercancel', (e) => endADrag(e, false), true);
    }
    // V2: a clip laid over the cut moves along it
    const v2 = tl.el.tracks && tl.el.tracks.V2;
    if (v2) {
      v2.addEventListener('pointerdown', onODown, true);
      v2.addEventListener('pointermove', onOMove, true);
      v2.addEventListener('pointerup', (e) => endODrag(e, true), true);
      v2.addEventListener('pointercancel', (e) => endODrag(e, false), true);
    }

    // the lanes' own clicks
    el.a1.addEventListener('click', (e) => {
      if (!e.target.closest('.bed')) return;
      const p = q('#musicPanel');
      if (window.dock) window.dock.reveal(p);      // the panel is a tool in the dock (M11)
      if (p) p.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    });
    el.ghost.addEventListener('click', (e) => {
      const g = e.target.closest('.ghost');
      if (!g || !ghost) return;
      const b = e.target.closest('button.rv');
      if (b) {                                       // ✓ / ✗: a verdict, not a play
        e.stopPropagation();
        setVerdict(Number(g.dataset.k), b.classList.contains('ok'));
        return;
      }
      const item = ghost.ghosts[Number(g.dataset.k)];
      if (item) playRange(item.seg, item.start, null);
    });

    // drop from outside — bound on the timeline's root, not the scrolling view, so an
    // absolutely placed child (the trim lane's magnet button sits top-right of #tl) is
    // drop-transparent: dragover on it bubbles here and is accepted, and the film time
    // comes from clientX whatever the target was.
    const root = tl.el.root;
    root.addEventListener('dragover', onDragOver);
    root.addEventListener('dragleave', onDragLeave);
    root.addEventListener('drop', onDrop);
    for (const sel of ['#library', '#findResults', '#tl']) {
      const box = q(sel);
      if (box) box.addEventListener('dragstart', onDragStart);
    }
    el.bin.addEventListener('pointerdown', holdWhilePressed);
    markDraggable();
    if (typeof MutationObserver !== 'undefined') {
      const mo = new MutationObserver(() => { markDraggable(); schedule(); });
      for (const sel of ['#library', '#findResults']) {
        const box = q(sel);
        if (box) mo.observe(box, { childList: true });
      }
      const prop = q('#proposal');
      if (prop) new MutationObserver(schedule).observe(prop, { attributes: true, attributeFilter: ['style'] });
    }
    for (const sel of ['#musicTrack', '#duck', '#fadeIn', '#fadeOut']) {
      const c = q(sel);
      if (c) c.addEventListener('change', () => setTimeout(schedule, 0));
    }
    if (typeof ResizeObserver !== 'undefined') new ResizeObserver(schedule).observe(tl.el.view);

    tl.on('change', schedule);
    tl.on('zoom', schedule);
    const render = tl.render;
    tl.render = function () {
      const r = render.apply(tl, arguments);
      if (ghostInV1()) layout();           // the render laid a ghost row out under V1 again
      schedule();
      return r;
    };
    setInterval(poll, POLL_MS);
    redraw();
  }

  // The foundation has no mounted hook: wrap tl.mount (never edit it), and cover the
  // case where the board mounted before this file ran.
  const mount = tl.mount;
  tl.mount = function () { const r = mount.apply(tl, arguments); setup(); return r; };
  if (tl.el && tl.el.canvas) setup();

  window.tlLanes = {
    redraw, slotAt, playRange, playPlan, insertAt, MIME,
    showGhost, clearGhost,
    setVerdict, verdict, keepRemoved, reviewed, shotFacts,
    picks: () => [...document.querySelectorAll('#tl .tl-pick')].map((m) => ({
      t: Number(m.dataset.t), title: m.title, ghost: m.classList.contains('on-ghost') })),
    ghost: () => ghost,
    shown: () => shown,
    mode: () => mode,
  };
})();
