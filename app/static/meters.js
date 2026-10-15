/* meters.js: stereo peak meters beside the Program monitor (REDESIGN-GOAL dimension 6;
 * verdict 3 punch 6: "there are no audio meters").
 *
 * What is metered is what the Program plays: every <video>/<audio> inside #player (pv0
 * and pv1, lane PROGRAM's #pvA1 — an unlinked shot's moved sound — and #pvV2, which is
 * muted) and the music bed (#bed, A2). An element joins the mix the first time it fires
 * `play` (a capturing listener on the document: `play` does not bubble), so nothing has
 * to register itself.
 *
 * WebAudio's createMediaElementSource can be called ONCE per element, and from then on
 * the element is heard only through the graph. This file owns that call:
 *     element → its own GainNode → the mix bus → speakers
 *                                            ↘ splitter → L / R analysers
 * The bus is two channels, so a mono clip shows on both meters.
 *
 * The level an element plays at has one owner, `window.rcAudio.setLevel(el, key, linear)`:
 * named factors per element ('track': A1's mute / solo / gain_db, from timeline.js's
 * applyMonitorAudio; 'clip': the shot's Effect Controls Volume) multiplied together. Once
 * the element is in the graph the product is its gain node, so it can go above 0 dB, and
 * its `volume` is left at 1; before that (no user gesture yet) the product, capped at 1,
 * is its `volume`. A writer that still sets `volume` directly is multiplied in by the
 * graph (the element applies its own volume before the graph), so the meters always show
 * what is heard. The graph is built only once the AudioContext runs: an element routed
 * into a suspended context would go silent.
 *
 * Drawing: one <canvas> (no words, no controls): two bars, -60..0 dBFS, green / yellow
 * from -18 / red from -6, a peak-hold tick (1.5 s, then it falls 20 dB/s), the level
 * itself falls 24 dB/s (Premiere's ballistics are similar). Always vertical, beside the
 * picture, as Premiere's Audio Meters are (wave 4, lane LAYOUT: the monitors now share the
 * whole top row, so the Program panel is wider than its picture needs at the default
 * height and the 30 px column costs the picture nothing). The 'h' mode (a strip under the
 * picture) is only for a Program panel dragged so narrow that a 30 px column would leave
 * the picture under MIN_PIC_W.
 *
 *   window.rcMeters.read()   {now: [l, r], shown: [l, r], hold: [l, r]} in dBFS (-Infinity: silence)
 *   window.rcMeters.mode()   'v' | 'h'
 *   window.rcMeters.sources() how many elements are in the mix; .has(el) whether el is
 *   window.rcAudio.setLevel(el, key, linear)  one factor of the element's level (see above)
 *   window.rcAudio.levelOf(el)  the product of its factors (1 when none)
 *   window.rcAudio.gainFor(el)  the element's GainNode (attaches it), or null before audio can run
 */
(function () {
  'use strict';

  const FLOOR_DB = -60;
  const HOLD_MS = 1500, HOLD_FALL = 20, LEVEL_FALL = 24;   // dB per second
  const V_W = 30;                                           // px: the vertical meters' width
  const MIN_PIC_W = 200;                                    // px: below this, the meters go under the picture
  const IDLE_MS = 2500;                                     // stop drawing this long after silence

  let ctx = null, bus = null, an = null, buf = null;
  const nodes = new WeakMap();      // element -> {src, gain}
  let count = 0;
  const lvl = { now: [-Infinity, -Infinity], shown: [-Infinity, -Infinity],
                hold: [-Infinity, -Infinity], holdAt: [0, 0], clip: [false, false] };
  let raf = 0, lastT = 0, quietSince = 0;
  let box = null, cv = null, g2 = null, mode = 'h';

  const toDb = (x) => (x > 0 ? 20 * Math.log10(x) : -Infinity);

  /* ---------------------------------------------------------------- the graph */
  function ensureCtx() {
    if (ctx) return ctx;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    try { ctx = new AC({ latencyHint: 'interactive' }); } catch (err) { ctx = null; return null; }
    bus = ctx.createGain();
    bus.channelCount = 2; bus.channelCountMode = 'explicit'; bus.channelInterpretation = 'speakers';
    bus.connect(ctx.destination);
    const split = ctx.createChannelSplitter(2);
    bus.connect(split);
    an = [0, 1].map((ch) => {
      const a = ctx.createAnalyser();
      a.fftSize = 2048;
      a.smoothingTimeConstant = 0;
      split.connect(a, ch);
      return a;
    });
    buf = new Float32Array(an[0].fftSize);
    return ctx;
  }

  function running() {
    if (!ensureCtx()) return Promise.resolve(false);
    if (ctx.state === 'running') return Promise.resolve(true);
    // resume() can wait for a gesture forever: give it a moment, then try again later
    return Promise.race([
      ctx.resume().then(() => ctx.state === 'running', () => false),
      new Promise((res) => setTimeout(() => res(ctx.state === 'running'), 400)),
    ]);
  }

  function attach(el) {
    if (!el || nodes.has(el) || !ctx || ctx.state !== 'running') return nodes.get(el) || null;
    let src;
    try { src = ctx.createMediaElementSource(el); } catch (err) { return null; }
    const gain = ctx.createGain();
    src.connect(gain);
    gain.connect(bus);
    const n = { src, gain };
    nodes.set(el, n);
    count++;
    // the level moves from `volume` (where setLevel kept it, capped) to the gain node; a
    // writer that sets `volume` directly sets it again on its next write
    const f = levels.get(el);
    if (f) { el.volume = 1; gain.gain.value = product(f); }
    return n;
  }

  function metered(el) {
    if (!(el instanceof HTMLMediaElement)) return false;
    if (el.id === 'bed' || el.dataset.meter != null) return true;
    const pl = document.getElementById('player');
    return !!(pl && pl.contains(el));
  }

  function onPlay(e) {
    const el = e.target;
    if (!metered(el)) return;
    if (nodes.has(el)) { wake(); return; }
    running().then((ok) => { if (ok) { attach(el); wake(); } });
  }

  /* ---- the one owner of an element's level (lane PROGRAM's request) */
  const levels = new WeakMap();      // element -> {key: linear factor}
  function product(f) {
    let p = 1;
    for (const k in f) p *= f[k];
    return Number.isFinite(p) && p > 0 ? p : 0;
  }
  function setLevel(el, key, linear) {
    if (!el) return;
    const x = Math.max(0, Number(linear));
    const f = levels.get(el) || {};
    f[key] = Number.isFinite(x) ? x : 1;
    levels.set(el, f);
    const p = product(f);
    const n = nodes.get(el);
    if (n) {
      if (Math.abs(n.gain.gain.value - p) > 1e-4) n.gain.gain.value = p;
    } else {
      const v = Math.min(1, p);
      if (Math.abs(el.volume - v) > 1e-4) el.volume = v;
    }
  }

  /* ---------------------------------------------------------------- levels */
  function sample(now) {
    const dt = lastT ? Math.min(0.25, (now - lastT) / 1000) : 0;
    lastT = now;
    for (let ch = 0; ch < 2; ch++) {
      let peak = 0;
      if (an && ctx && ctx.state === 'running' && count) {
        an[ch].getFloatTimeDomainData(buf);
        for (let i = 0; i < buf.length; i++) { const v = Math.abs(buf[i]); if (v > peak) peak = v; }
      }
      const db = toDb(peak);
      lvl.now[ch] = db;
      const fallen = lvl.shown[ch] - LEVEL_FALL * dt;
      lvl.shown[ch] = db >= fallen ? db : fallen;
      if (lvl.shown[ch] < FLOOR_DB - 6) lvl.shown[ch] = -Infinity;
      if (db >= lvl.hold[ch]) { lvl.hold[ch] = db; lvl.holdAt[ch] = now; }
      else if (now - lvl.holdAt[ch] > HOLD_MS) {
        lvl.hold[ch] = Math.max(db, lvl.hold[ch] - HOLD_FALL * dt);
        if (lvl.hold[ch] < FLOOR_DB - 6) lvl.hold[ch] = -Infinity;
      }
      if (db >= -0.1) lvl.clip[ch] = true;
    }
  }

  function tick(now) {
    raf = 0;
    sample(now);
    draw();
    const loud = lvl.shown.some((x) => x > -Infinity) || lvl.hold.some((x) => x > -Infinity);
    const playing = [...document.querySelectorAll('#player video, #player audio, #bed')]
      .some((el) => nodes.has(el) && !el.paused);
    if (playing || loud) quietSince = 0;
    else if (!quietSince) quietSince = now;
    if (!quietSince || now - quietSince < IDLE_MS) raf = requestAnimationFrame(tick);
    else lastT = 0;
  }

  function wake() {
    quietSince = 0;
    if (!raf) raf = requestAnimationFrame(tick);
  }

  /* ---------------------------------------------------------------- drawing */
  const COL = { bg: '#0b0b0e', off: '#1b1d22', g: '#3fbf6a', y: '#d9c23a', r: '#e0483e',
                tick: '#3a3d46', txt: '#7d8190', hold: '#e8e8ea' };
  const SCALE = [0, -6, -12, -18, -24, -36, -48];

  function frac(db) {
    if (!(db > FLOOR_DB)) return 0;
    return Math.min(1, (db - FLOOR_DB) / -FLOOR_DB);
  }

  /* The scale (background, ticks, dB labels) never moves while playing: drawn once into a
   * cached canvas, rebuilt only when the size, the orientation or the pixel ratio changes.
   * Setting a canvas font and drawing text every frame made Chromium resolve the font through
   * style — a forced style recalc on every frame while playing (LUTCACHE's trace, 3-10 ms per
   * frame at load 20, the next-largest main-thread cost after the grade). */
  let scaleCv = null, scaleKey = '';
  function geometry(w, h) {
    const bars = [];
    if (mode === 'v') {
      const top = 6, bot = h - 4, len = bot - top;
      for (let ch = 0; ch < 2; ch++) bars.push({ x: 3 + ch * 7, y: top, w: 5, h: len, v: true });
      return { bars, top, bot, len };
    }
    const left = 4, right = w - 4, len = right - left;
    for (let ch = 0; ch < 2; ch++) bars.push({ x: left, y: 2 + ch * 6, w: len, h: 4, v: false });
    return { bars, left, right, len };
  }
  function drawScale(W, H, dpr) {
    const key = `${W}x${H}|${mode}|${dpr}`;
    if (scaleCv && key === scaleKey) return scaleCv;
    scaleKey = key;
    scaleCv = scaleCv || document.createElement('canvas');
    scaleCv.width = W; scaleCv.height = H;
    const s2 = scaleCv.getContext('2d');
    s2.setTransform(1, 0, 0, 1, 0, 0);
    s2.fillStyle = COL.bg;
    s2.fillRect(0, 0, W, H);
    s2.scale(dpr, dpr);
    const w = W / dpr, h = H / dpr;
    const gm = geometry(w, h);
    s2.font = '8px system-ui, sans-serif';
    if (mode === 'v') {
      s2.textAlign = 'right'; s2.textBaseline = 'middle';
      for (const d of SCALE) {
        const y = gm.bot - frac(d) * gm.len;
        s2.fillStyle = COL.tick; s2.fillRect(17, y, 2, 1);
        if (gm.len > 80 || d % 12 === 0) { s2.fillStyle = COL.txt; s2.fillText(String(-d), w - 1, y); }
      }
    } else {
      s2.textAlign = 'center'; s2.textBaseline = 'alphabetic';
      for (const d of SCALE) {
        const x = gm.left + frac(d) * gm.len;
        s2.fillStyle = COL.tick; s2.fillRect(x, 13, 1, 2);
        if (gm.len > 160 || d % 12 === 0) { s2.fillStyle = COL.txt; s2.fillText(String(d), Math.min(gm.right - 4, Math.max(gm.left + 4, x)), h - 1); }
      }
    }
    for (const b of gm.bars) { s2.fillStyle = COL.off; s2.fillRect(b.x, b.y, b.w, b.h); }
    return scaleCv;
  }

  function draw() {
    if (!cv || !g2) return;
    const W = cv.width, H = cv.height, dpr = window.devicePixelRatio || 1;
    if (!W || !H) return;
    g2.setTransform(1, 0, 0, 1, 0, 0);
    g2.drawImage(drawScale(W, H, dpr), 0, 0);
    g2.scale(dpr, dpr);
    const w = W / dpr, h = H / dpr;
    const bars = geometry(w, h).bars;
    bars.forEach((b, ch) => {
      const f = frac(lvl.shown[ch]);
      const zones = [[0, frac(-18), COL.g], [frac(-18), frac(-6), COL.y], [frac(-6), 1, COL.r]];
      for (const [a, z, c] of zones) {
        const e = Math.min(f, z);
        if (e <= a) continue;
        g2.fillStyle = c;
        if (b.v) g2.fillRect(b.x, b.y + b.h - e * b.h, b.w, (e - a) * b.h);
        else g2.fillRect(b.x + a * b.w, b.y, (e - a) * b.w, b.h);
      }
      const hf = frac(lvl.hold[ch]);
      if (hf > 0) {
        g2.fillStyle = lvl.hold[ch] > -6 ? COL.r : COL.hold;
        if (b.v) g2.fillRect(b.x, b.y + b.h - hf * b.h - 1, b.w, 1.5);
        else g2.fillRect(b.x + hf * b.w - 1, b.y, 1.5, b.h);
      }
      // the clip light: over 0 dBFS once, lit until the meters are clicked
      g2.fillStyle = lvl.clip[ch] ? COL.r : COL.off;
      if (b.v) g2.fillRect(b.x, 1, b.w, 3);
      else g2.fillRect(b.x + b.w + 1, b.y, 2, b.h);
    });
  }

  /* ---------------------------------------------------------------- the slot */
  function layout() {
    const pl = document.getElementById('player');
    if (!pl || !box) return;
    const tr = pl.querySelector('.transport');
    const W = pl.clientWidth, H = pl.clientHeight - (tr ? tr.offsetHeight : 0);
    if (!(W > 0 && H > 0)) return;
    // vertical beside the picture (Premiere's meters), unless the panel is too narrow to spare the column
    const next = (W - V_W) >= MIN_PIC_W ? 'v' : 'h';
    if (next !== mode || !pl.classList.contains(`meters-${next}`)) {
      mode = next;
      pl.classList.toggle('meters-v', mode === 'v');
      pl.classList.toggle('meters-h', mode === 'h');
      box.dataset.mode = mode;
    }
    const r = box.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const cw = Math.max(1, Math.round(r.width * dpr)), ch = Math.max(1, Math.round(r.height * dpr));
    if (cv.width !== cw || cv.height !== ch) { cv.width = cw; cv.height = ch; }
    draw();
  }

  function mount() {
    box = document.getElementById('meters');
    if (!box || box.querySelector('canvas')) return;
    cv = document.createElement('canvas');
    cv.setAttribute('aria-hidden', 'true');
    box.appendChild(cv);
    box.title = 'Audio meters — the Program\'s mix, peak dBFS (click to reset the clip lights)';
    box.addEventListener('click', () => { lvl.clip = [false, false]; draw(); });
    g2 = cv.getContext('2d');
    const pl = document.getElementById('player');
    if (typeof ResizeObserver !== 'undefined' && pl) new ResizeObserver(layout).observe(pl);
    layout();
  }

  document.addEventListener('play', onPlay, true);
  document.addEventListener('playing', onPlay, true);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();

  window.rcMeters = {
    read: () => ({ now: [...lvl.now], shown: [...lvl.shown], hold: [...lvl.hold] }),
    mode: () => mode,
    sources: () => count,
    has: (el) => nodes.has(el),
    state: () => (ctx ? ctx.state : 'none'),
    layout,
  };
  window.rcAudio = {
    setLevel,
    levelOf: (el) => product(levels.get(el) || {}),
    gainFor(el) {
      if (!el) return null;
      if (nodes.has(el)) return nodes.get(el).gain;
      if (!ensureCtx() || ctx.state !== 'running') return null;
      const n = attach(el);
      return n ? n.gain : null;
    },
    context: () => ctx,
  };
})();
