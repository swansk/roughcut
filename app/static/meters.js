/* meters.js: stereo peak meters beside the Program monitor (REDESIGN-GOAL dimension 6;
 * verdict 3 punch 6: "there are no audio meters").
 *
 * What is metered is what the Program plays: every <video>/<audio> inside #player (pv0
 * and pv1 today; lane PROGRAM's V2 layer and an unlinked-sound element when they land)
 * and the music bed (#bed, A2). An element joins the mix the first time it fires `play`
 * (a capturing listener on the document: `play` does not bubble), so nothing has to
 * register itself.
 *
 * WebAudio's createMediaElementSource can be called ONCE per element, and from then on
 * the element is heard only through the graph. This file owns that call:
 *     element → its own GainNode (window.rcAudio.gainFor(el)) → the mix bus → speakers
 *                                                               ↘ splitter → L / R analysers
 * The bus is two channels, so a mono clip shows on both meters. The element's own
 * `volume` (what timeline.js's applyMonitorAudio sets from A1/A2 mute, solo and gain_db,
 * and Effect Controls' volume) is applied before the graph, so the meters show what is
 * heard; a track gain above 0 dB, which `volume` cannot carry, is the element's gain node.
 * The graph is built only once the AudioContext runs (after a user gesture): an element
 * routed into a suspended context would go silent.
 *
 * Drawing: one <canvas> (no words, no controls): two bars, -60..0 dBFS, green / yellow
 * from -18 / red from -6, a peak-hold tick (1.5 s, then it falls 20 dB/s), the level
 * itself falls 24 dB/s (Premiere's ballistics are similar). Vertical beside the picture
 * when the panel has room for it; under the picture when the picture is as wide as the
 * panel (at 1440×900 the Program picture is width-bound, and 30 px beside it would take
 * the picture under its 380 px floor).
 *
 *   window.rcMeters.read()   {now: [l, r], shown: [l, r], hold: [l, r]} in dBFS (-Infinity: silence)
 *   window.rcMeters.mode()   'v' | 'h'
 *   window.rcMeters.sources() how many elements are in the mix
 *   window.rcAudio.gainFor(el)  the element's GainNode (attaches it), or null before audio can run
 */
(function () {
  'use strict';

  const FLOOR_DB = -60;
  const HOLD_MS = 1500, HOLD_FALL = 20, LEVEL_FALL = 24;   // dB per second
  const V_W = 30, H_H = 22;                                 // px: the meters' own size
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
    applyBoost();
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

  /* A1's gain above 0 dB: `volume` stops at 1, so the rest is the element's gain node.
   * Below 0 dB (and mute / solo) the element's own volume already carries it. */
  function applyBoost() {
    const tl = window.tl;
    if (!tl || !tl.tracks || !ctx) return;
    let tr;
    try { tr = tl.tracks(); } catch (err) { return; }
    const boost = (name) => {
      const t = tr[name] || {};
      const audible = tl.audible ? tl.audible(name) : !t.mute;
      return audible ? Math.max(1, Math.pow(10, (Number(t.gain_db) || 0) / 20)) : 1;
    };
    const a1 = boost('A1'), a2 = boost('A2');
    for (const el of document.querySelectorAll('#player video, #player audio, #bed')) {
      const n = nodes.get(el);
      if (!n) continue;
      const want = el.id === 'bed' ? a2 : a1;
      if (Math.abs(n.gain.gain.value - want) > 1e-4) n.gain.gain.value = want;
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

  function draw() {
    if (!cv || !g2) return;
    const W = cv.width, H = cv.height, dpr = window.devicePixelRatio || 1;
    g2.setTransform(1, 0, 0, 1, 0, 0);
    g2.fillStyle = COL.bg;
    g2.fillRect(0, 0, W, H);
    g2.scale(dpr, dpr);
    const w = W / dpr, h = H / dpr;
    g2.font = '8px system-ui, sans-serif';
    g2.fillStyle = COL.txt;
    const bars = [];
    if (mode === 'v') {
      const top = 6, bot = h - 4, len = bot - top;
      for (let ch = 0; ch < 2; ch++) bars.push({ x: 3 + ch * 7, y: top, w: 5, h: len, v: true });
      g2.textAlign = 'right'; g2.textBaseline = 'middle';
      for (const d of SCALE) {
        const y = bot - frac(d) * len;
        g2.fillStyle = COL.tick; g2.fillRect(17, y, 2, 1);
        if (len > 80 || d % 12 === 0) { g2.fillStyle = COL.txt; g2.fillText(String(-d), w - 1, y); }
      }
    } else {
      const left = 4, right = w - 4, len = right - left;
      for (let ch = 0; ch < 2; ch++) bars.push({ x: left, y: 2 + ch * 6, w: len, h: 4, v: false });
      g2.textAlign = 'center'; g2.textBaseline = 'alphabetic';
      for (const d of SCALE) {
        const x = left + frac(d) * len;
        g2.fillStyle = COL.tick; g2.fillRect(x, 13, 1, 2);
        if (len > 160 || d % 12 === 0) { g2.fillStyle = COL.txt; g2.fillText(String(d), Math.min(right - 4, Math.max(left + 4, x)), h - 1); }
      }
    }
    bars.forEach((b, ch) => {
      g2.fillStyle = COL.off;
      g2.fillRect(b.x, b.y, b.w, b.h);
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
    // vertical only when the picture is bound by the height even with the meters beside it
    const next = (W - V_W) >= H * 16 / 9 ? 'v' : 'h';
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
    const hook = () => {
      if (window.tl && window.tl.on) { window.tl.on('tracks', applyBoost); return true; }
      return false;
    };
    if (!hook()) {
      let tries = 0;
      const iv = setInterval(() => { if (hook() || ++tries > 40) clearInterval(iv); }, 250);
    }
  }

  document.addEventListener('play', onPlay, true);
  document.addEventListener('playing', onPlay, true);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();

  window.rcMeters = {
    read: () => ({ now: [...lvl.now], shown: [...lvl.shown], hold: [...lvl.hold] }),
    mode: () => mode,
    sources: () => count,
    state: () => (ctx ? ctx.state : 'none'),
    layout,
  };
  window.rcAudio = {
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
