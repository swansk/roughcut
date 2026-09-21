/* Roughcut — the timeline's lanes and drag and drop (INTAKE M9, I9.4).
 *
 * Built ON the foundation (timeline.js, `window.tl`): nothing here edits the foundation
 * or app.js. The lane hangs its own elements inside `tl.el.canvas` beside V1 and moves
 * V1 down (`--tl-above`) or grows the view (`--tl-below`) through CSS variables the
 * rules at the end of timeline.css read. Every edit goes through the foundation's API
 * (`tl.begin` / `tl.move` / `tl.insert` / `tl.commit`), so it is one undo entry and it
 * reaches the autosave the same way a trim does.
 *
 *   markers   a thin lane ABOVE V1: `★` at every hero keep that is in the cut, a tick per
 *             ranked event (`P.events`, top 60) that falls inside a shot, and the legend.
 *   V1        the foundation's. This file owns the BODY drag: a pointerdown on a block
 *             that is not on a handle, not within 8 px of an edge and has no alt key is a
 *             move (the trim lane owns edges and alt-drag). A drop line shows where the
 *             shot(s) land, snapping to cut points; under 4 px it is the foundation's click.
 *   ghost     while a proposal is pending (the diff panel is up): the proposed timeline
 *             under V1 aligned by ITS film time — unchanged shots dim, added ones green,
 *             moved ones with an arrow from where they are now, removed ones struck out on
 *             V1. Click a ghost → the monitor plays that range; `play proposal` / `play
 *             cut` at the lane's left. Accept and discard stay the panel's buttons.
 *
 * Film time everywhere here is `tl.dur(seg)` — `(out − in) / speed` — never `out − in`,
 * which is a clip length (INTAKE M13). A clip offset inside a shot (a keep's start, a
 * speech region's edge, an event) maps to film as `(t − in) / speed`.
 *   A1        the music bed: the track's name, a bar the length of the film, the fades as
 *             ramps, a dip under every speech region in the cut (the same `speechRegions`
 *             the monitor ducks by) and the monitor's own `bedGainAt` curve on top. It
 *             draws, never edits (INTAKE decision 5): a click scrolls the music panel in.
 *   bin       the pass's keeps NOT in the cut as faint outlines after the last shot of
 *             their clip (else after the end), width to length — drag one onto V1.
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
  const EVENTS_TOP = 60;
  const POLL_MS = 500;
  const HOT = new Set(['fall', 'crash', 'jump', 'reaction']);
  const H = { markers: 16, ghost: 40, a1: 30, bin: 22, gap: 4 };

  const el = { markers: null, ghost: null, a1: null, bin: null, over: null, arrows: null, drop: null };
  let mounted = false;
  let drag = null;                     // the body drag on V1, from pointerdown to pointerup
  let holdRedraw = false;              // an outline is pressed or dragged: rebuilding would end the drag
  let dragActive = false;              // a native drag from one of the lanes is in flight
  let raf = 0;                         // one redraw per frame, however many triggers
  let rangeGen = 0;                    // proposal playback token
  let mode = 'cut';                    // the ghost lane's toggle
  let seen = {};                       // what the poll last saw
  let ghost = null;                    // the matched proposal, while one is pending

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
  function layout() {
    const root = tl.el.root;
    const above = el.markers.hidden ? 0 : H.markers + 2;
    let below = 0;
    for (const lane of [el.ghost, el.a1, el.bin]) {
      if (lane.hidden) continue;
      lane.style.top = `calc(var(--tl-ruler) + 5px + var(--tl-lane) + ${above + H.gap + below}px)`;
      below += lane.h + H.gap;
    }
    el.markers.style.top = 'calc(var(--tl-ruler) + 3px)';
    el.over.style.top = `calc(var(--tl-ruler) + 5px + ${above}px)`;
    el.arrows.style.top = el.over.style.top;
    root.style.setProperty('--tl-above', px(above));
    root.style.setProperty('--tl-below', px(below));
  }

  function schedule() {
    if (raf) return;
    raf = requestAnimationFrame(() => { raf = 0; redraw(); });
  }

  function redraw() {
    if (!mounted || holdRedraw) return;
    const a = app();
    if (!a) return;
    drawMarkers(a);
    drawA1(a);
    drawGhost(a);
    layout();
    seen = snapshot(a);
  }

  const snapshot = (a) => ({
    bin: a.bin, music: a.music, plan: a.pendingPlan, zoom: tl.state.zoom, total: tl.total(),
    up: q('#proposal') ? q('#proposal').style.display : '',
    n: tl.state.segs.length,
  });

  function poll() {
    const a = app();
    if (!a) return;
    const now = snapshot(a);
    for (const k of Object.keys(now)) if (now[k] !== seen[k]) { schedule(); return; }
  }

  /* ------------------------------------------------------------ markers + bin */
  function drawMarkers(a) {
    const list = tl.state.segs;
    const tot = tl.total();
    const z = tl.state.zoom;
    const keepsUsable = fn('keepsUsable'), shotOf = fn('shotOf'), binOrder = fn('binOrder');
    const keeps = keepsUsable && binOrder ? binOrder(keepsUsable()) : [];
    const marks = [], avail = [];
    for (const k of keeps) {
      const i = shotOf ? shotOf(k) : -1;
      if (i < 0) { avail.push(k); continue; }
      if (!k.hero) continue;
      const seg = list[i];
      const at = toFilm(seg, k.start);
      marks.push({ cls: 'mk hero', t: at, text: '★',
        title: `★ hero · ${stemOf(k.clip)} ${tl.fmt(k.start)}–${tl.fmt(k.end)}` + (k.why ? `\n${k.why}` : '') });
    }
    const events = ((a.P && a.P.events) || []).slice(0, EVENTS_TOP);
    events.forEach((e, n) => {
      if (e.notable === false || e.kind === 'junk') return;
      let start = 0;
      for (const seg of list) {
        if (seg.clip === e.clip && e.end > seg.in && e.start < seg.out) {
          marks.push({ cls: 'mk ev', kind: e.kind || 'seen',
            t: start + (Math.max(e.start, seg.in) - seg.in) / spd(seg),
            title: `${e.what || e.kind || 'event'} · #${e.rank || n + 1}` });
        }
        start += tl.dur(seg);
      }
    });

    el.markers.hidden = !list.length || !(marks.length || avail.length || events.length);
    el.markers.replaceChildren(el.markers.lbl);
    for (const m of marks) {
      const d = div(m.cls);
      d.style.left = px(tl.timeToX(m.t));
      if (m.kind) d.dataset.kind = m.kind;
      if (m.text) d.textContent = m.text;
      d.title = m.title;
      el.markers.appendChild(d);
    }

    // the pass's keeps not in the cut: after the last shot of their clip, else after the end
    el.bin.hidden = !list.length || !avail.length;
    el.bin.replaceChildren(el.bin.lbl);
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
      d.textContent = `${stemOf(k.clip)} ${len.toFixed(1)}s${k.hero ? ' ★' : ''}`;
      d.title = `available: ${stemOf(k.clip)} ${tl.fmt(k.start)}–${tl.fmt(k.end)} — drag onto V1 to add it`
        + (k.why ? `\n${k.why}` : '');
      d._keep = k;
      el.bin.appendChild(d);
      cursor.set(anchor, at + len);
    }
  }

  /* ------------------------------------------------------------ A1: the music bed */
  function drawA1(a) {
    const m = a.music;
    const list = tl.state.segs;
    const tot = tl.total();
    const z = tl.state.zoom;
    const lane = el.a1;
    lane.replaceChildren(lane.lbl);
    if (!m || !list.length || tot <= 0) { lane.hidden = true; return; }
    lane.hidden = false;
    const trackInfo = fn('trackInfo');
    const t = trackInfo ? trackInfo() : null;
    lane.lbl.textContent = `♪ ${(t && t.name) || m.asset}`
      + (m.duck ? ` · ducks −${m.duck_db} dB under speech` : ' · no duck');

    const bed = div('bed');
    bed.style.left = px(tl.timeToX(0));
    bed.style.width = px(tot * z);
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
    const p = a.pendingPlan;
    const box = q('#proposal');
    if (!p || !Array.isArray(p.segments) || !box || box.style.display === 'none') return null;
    return p;
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
      let i = s.id ? tl.indexOf(s.id) : -1;
      if (i >= 0 && used.has(i)) i = -1;
      if (i < 0) {
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

  function drawGhost(a) {
    const plan = pendingPlanOf(a);
    const lane = el.ghost;
    lane.replaceChildren(lane.lbl);
    el.over.replaceChildren();
    el.arrows.replaceChildren();
    if (!plan) {
      if (ghost) stopRange();
      ghost = null;
      lane.hidden = true;
      return;
    }
    if (!ghost || ghost.plan !== plan) mode = 'cut';
    ghost = matchPlan(plan);
    lane.hidden = false;
    const z = tl.state.zoom;
    const cur = tl.state.segs;
    const laneH = tl.el.lanes.V1.offsetHeight || 72;

    for (const g of ghost.ghosts) {
      const d = div(`ghost ${g.cls}${g.trimmed ? ' trimmed' : ''}${isGen(g.seg.clip) ? ' gen' : ''}`);
      d.dataset.k = g.k;
      const dur = tl.dur(g.seg);
      const s = spd(g.seg);
      d.style.left = px(tl.timeToX(g.start));
      d.style.width = px(Math.max(3, dur * z));
      d.style.setProperty('--hue', tl.hueOf(g.seg.clip));
      d.innerHTML = '<span class="name"></span><span class="dur"></span>';
      d.querySelector('.name').textContent = nameOf(g.seg.clip);
      d.querySelector('.dur').textContent = `${dur.toFixed(1)}s${s === 1 ? '' : ` · ${round2(s)}×`}`;
      const what = g.cls === 'added' ? 'added' : g.cls === 'moved' ? 'moved' : g.trimmed ? 'trimmed' : 'unchanged';
      d.title = `proposal ${g.k + 1}. ${nameOf(g.seg.clip)} ${tl.fmt(g.seg.in)}–${tl.fmt(g.seg.out)} (${dur.toFixed(1)}s`
        + `${s === 1 ? '' : ` at ${round2(s)}×`}) · ${what}`
        + (g.seg.why ? `\n${g.seg.why}` : '') + '\nclick to play this shot in the monitor';
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
      s.title = `${stemOf(c.clip)} — removed by the proposal`;
      el.over.appendChild(s);
    }
    el.arrows.setAttribute('width', String(Math.ceil(tl.el.canvas.offsetWidth || 0)));
    el.arrows.setAttribute('height', String(laneH + H.gap + H.ghost));
    if (!el.arrows.querySelector('defs')) el.arrows.appendChild(arrowDefs());
    paintMode(a);
  }

  function arrowDefs() {
    const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
    defs.innerHTML = '<marker id="tl-arrowhead" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">'
      + '<path d="M0,0 L6,3 L0,6 z"></path></marker>';
    return defs;
  }

  function paintMode(a) {
    const b = el.ghost.lbl;
    if (a && a.player && a.player.playing) mode = 'cut';
    b.querySelector('.play-proposal').classList.toggle('on', mode === 'proposal');
    b.querySelector('.play-cut').classList.toggle('on', mode === 'cut');
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
    el.markers = lane('markers', H.markers,
      '<b>★</b> hero · <i class="ev"></i> event · <span class="box"></span> available keep');
    el.ghost = lane('ghost', H.ghost,
      'proposal · <button class="play-proposal" type="button" title="play the proposed cut in the monitor">play proposal</button>'
      + '<button class="play-cut on" type="button" title="play the cut as it is">play cut</button>');
    el.a1 = lane('A1', H.a1, '♪');
    el.bin = lane('bin', H.bin, 'available');
    el.over = div('tl-over');
    el.arrows = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    el.arrows.setAttribute('class', 'tl-arrows');
    el.drop = div('tl-dropline');
    el.drop.style.display = 'none';
    for (const x of [el.markers, el.ghost, el.a1, el.bin, el.over, el.arrows, el.drop]) canvas.appendChild(x);

    // V1: the body drag
    const v1 = tl.el.lanes.V1;
    v1.addEventListener('pointerdown', onDown);
    v1.addEventListener('pointermove', onMove);
    v1.addEventListener('pointerup', (e) => endDrag(e, true));
    v1.addEventListener('pointercancel', (e) => endDrag(e, false));

    // the lanes' own clicks
    el.a1.addEventListener('click', () => {
      const p = q('#musicPanel');
      if (window.dock) window.dock.reveal(p);      // the panel is a tool in the dock (M11)
      if (p) p.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    });
    el.ghost.addEventListener('click', (e) => {
      if (e.target.closest('.play-proposal')) { playPlan(0); return; }
      if (e.target.closest('.play-cut')) {
        stopRange(); mode = 'cut'; paintMode(null);
        const playFrom = fn('playFrom');
        if (playFrom) playFrom(0);
        return;
      }
      const g = e.target.closest('.ghost');
      if (!g || !ghost) return;
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
    tl.render = function () { const r = render.apply(tl, arguments); schedule(); return r; };
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
    ghost: () => ghost,
    mode: () => mode,
  };
})();
