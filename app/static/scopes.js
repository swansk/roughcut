/* Roughcut — Lumetri Scopes (redesign wave 11, lane LUMETRI; verdict 8 punch 4).
 *
 * Premiere's Lumetri Scopes panel, as a tab beside the Source monitor (where Premiere's
 * Color workspace puts it, so the scopes and the Program are seen together): a luma
 * Waveform and a YUV Vectorscope of the picture the Program shows — the graded frame,
 * drawn for it by grade.js (`grade.sample`: the same renderer, LUT and Lumetri uniforms
 * as the monitor's canvas; the raw frame when the grade is off or the shot ungraded).
 *
 *   Waveform (Luma)   x = the picture's column, y = its Rec.709 luma in IRE (0 at the
 *                     bottom, 100 at the top), brighter where more pixels land; a
 *                     graticule every 10 IRE.
 *   Vectorscope YUV   each pixel at (Cb, Cr) (BT.709; Cb right, Cr up), the circle at
 *                     |chroma| 0.5; the targets are the 75 % primaries and secondaries
 *                     (R MG B CY G YL) and the line is the skin-tone line.
 *
 * Live while parked: a new sample whenever the Program draws (grade.draws), moves or
 * changes shot — a Lumetri change shows on the scopes on the next frame or two.
 * Throttled while playing: at most PLAY_HZ samples a second, so the scopes never cost
 * the picture its frame rate. Nothing runs while the tab is hidden.
 *
 * API (window.scopes): open() · close() · on · stats() (the last sample's numbers:
 * the waveform's min / max / mean IRE and per-column ranges, the vectorscope's centroid
 * angle and chroma) · samples (count) · refresh().
 */
(() => {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const KEY = 'roughcut.scopes.on';
  const SAMPLE_W = 256;
  const PLAY_HZ = 5;
  const KR = 0.2126, KG = 0.7152, KB = 0.0722;
  const TARGETS = [['R', [1, 0, 0]], ['MG', [1, 0, 1]], ['B', [0, 0, 1]], ['CY', [0, 1, 1]],
                   ['G', [0, 1, 0]], ['YL', [1, 1, 0]]];
  const SKIN_DEG = 123;                     // the skin-tone ("I") line, from +Cb

  const S = { on: false, built: false, samples: 0, last: 0, sig: '', stats: null, raf: 0,
              wave: null, vec: null, waveImg: null, vecImg: null };
  const store = {
    get() { try { return localStorage.getItem(KEY) === '1'; } catch (e) { return false; } },
    set(v) { try { localStorage.setItem(KEY, v ? '1' : '0'); } catch (e) { /* fine */ } },
  };
  const chroma = (r, g, b) => {
    const y = KR * r + KG * g + KB * b;
    return [(b - y) / (2 * (1 - KB)), (r - y) / (2 * (1 - KR))];
  };

  /* ------------------------------------------------------------ the tab */
  function build() {
    const panel = $('#pSource');
    const tabs = panel && panel.querySelector('.ws-tabs');
    const source = $('#source');
    if (!panel || !tabs || !source || S.built) return !!S.built;
    S.built = true;
    const tab = document.createElement('button');
    tab.type = 'button';
    tab.className = 'ws-tab';
    tab.id = 'scopesTab';
    tab.setAttribute('role', 'tab');
    tab.title = 'Lumetri Scopes: the Program picture\'s luma waveform and vectorscope';
    tab.innerHTML = '<span class="lbl">Lumetri Scopes</span>';
    tabs.appendChild(tab);
    const body = document.createElement('div');
    body.className = 'ws-body sc-body';
    body.id = 'scopes';
    body.hidden = true;
    body.innerHTML = `
      <div class="sc-pane sc-wave"><div class="sc-name">Waveform (Luma)</div><canvas id="scWave" data-testid="scope-wave"></canvas></div>
      <div class="sc-pane sc-vec"><div class="sc-name">Vectorscope YUV</div><canvas id="scVec" data-testid="scope-vec"></canvas></div>`;
    source.after(body);
    S.wave = body.querySelector('#scWave');
    S.vec = body.querySelector('#scVec');
    tab.addEventListener('click', (e) => { e.stopPropagation(); open(); });
    const src = $('#srcTitle');
    if (src) src.addEventListener('click', () => close());
    // the Source monitor opening a clip brings its tab back
    window.addEventListener('rcsource', () => { if (S.on && window.rcSource && rcSource.clip) close(); });
    if (typeof ResizeObserver === 'function') new ResizeObserver(() => { if (S.on) { size(); paint(); } }).observe(body);
    return true;
  }
  function setTab(on) {
    const tab = $('#scopesTab'), src = $('#srcTitle'), body = $('#scopes'), source = $('#source');
    if (!tab || !body || !source) return;
    tab.classList.toggle('on', on);
    tab.setAttribute('aria-selected', String(on));
    if (src) src.classList.toggle('on', !on);
    body.hidden = !on;
    source.style.display = on ? 'none' : '';
  }
  function open() {
    if (!build()) return false;
    S.on = true;
    store.set(true);
    setTab(true);
    size();
    S.sig = '';
    loop();
    return true;
  }
  function close() {
    S.on = false;
    store.set(false);
    setTab(false);
    if (S.raf) cancelAnimationFrame(S.raf);
    S.raf = 0;
  }

  function size() {
    const dpr = window.devicePixelRatio || 1;
    [S.wave, S.vec].forEach((c) => {
      if (!c) return;
      const r = c.getBoundingClientRect();
      const w = Math.max(16, Math.round(r.width * dpr)), h = Math.max(16, Math.round(r.height * dpr));
      if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    });
  }

  /* ------------------------------------------------------------ sampling */
  function playing() { try { return typeof player === 'object' && !!player && !!player.playing; } catch (e) { return false; } }   // eslint-disable-line no-undef
  function signature() {
    const g = window.grade;
    let lv = null;
    try { lv = typeof liveVideo === 'function' ? liveVideo() : null; } catch (e) { lv = null; }   // eslint-disable-line no-undef
    const ph = window.tl && tl.state ? tl.state.playhead : 0;
    return [g ? g.draws : 0, g ? g.shot : '', g ? g.enabled : '', ph, lv ? lv.currentTime : '', lv ? lv.readyState : ''].join('|');
  }
  function loop() {
    if (S.raf) return;
    const tick = () => {
      S.raf = 0;
      if (!S.on) return;
      const now = performance.now();
      if (playing()) {
        if (now - S.last >= 1000 / PLAY_HZ) take(now);
      } else {
        const sig = signature();
        if (sig !== S.sig) { S.sig = sig; take(now); }
      }
      S.raf = requestAnimationFrame(tick);
    };
    S.raf = requestAnimationFrame(tick);
  }
  function take(now) {
    const g = window.grade;
    const px = g && typeof g.sample === 'function' ? g.sample(SAMPLE_W) : null;
    S.last = now;
    if (!px) return;
    S.samples++;
    analyse(px);
    paint();
  }

  /* ------------------------------------------------------------ the numbers */
  const WAVE_BINS = 128;
  function analyse(px) {
    const { w, h, data } = px;
    const wave = new Uint32Array(w * WAVE_BINS);
    const VEC = 128;
    const vec = new Uint32Array(VEC * VEC);
    const colMin = new Float32Array(w).fill(101), colMax = new Float32Array(w).fill(-1);
    let sumY = 0, sumCb = 0, sumCr = 0, sumC = 0, n = 0, minY = 101, maxY = -1;
    for (let row = 0; row < h; row++) {
      for (let x = 0; x < w; x++) {
        const i = (row * w + x) * 4;
        const r = data[i] / 255, g = data[i + 1] / 255, b = data[i + 2] / 255;
        const y = KR * r + KG * g + KB * b;
        const ire = y * 100;
        wave[x * WAVE_BINS + Math.min(WAVE_BINS - 1, Math.max(0, Math.round(y * (WAVE_BINS - 1))))]++;
        if (ire < colMin[x]) colMin[x] = ire;
        if (ire > colMax[x]) colMax[x] = ire;
        if (ire < minY) minY = ire;
        if (ire > maxY) maxY = ire;
        const [cb, cr] = chroma(r, g, b);
        const vx = Math.round((cb / 0.6 + 1) / 2 * (VEC - 1)), vy = Math.round((1 - (cr / 0.6 + 1) / 2) * (VEC - 1));
        if (vx >= 0 && vx < VEC && vy >= 0 && vy < VEC) vec[vy * VEC + vx]++;
        sumY += ire; sumCb += cb; sumCr += cr; sumC += Math.hypot(cb, cr); n++;
      }
    }
    const mcb = sumCb / n, mcr = sumCr / n;
    S.data = { w, wave, vec, VEC };
    S.stats = {
      w, h, n,
      wave: { min: r2(minY), max: r2(maxY), mean: r2(sumY / n),
              cols: Array.from(colMin, (v, i) => [r2(v), r2(colMax[i])]) },
      vec: { cb: r4(mcb), cr: r4(mcr), chroma: r4(Math.hypot(mcb, mcr)), meanChroma: r4(sumC / n),
             angle: r2(((Math.atan2(mcr, mcb) * 180 / Math.PI) + 360) % 360) },
      at: performance.now(),
    };
  }
  const r2 = (v) => Math.round(v * 100) / 100;
  const r4 = (v) => Math.round(v * 10000) / 10000;

  /* ------------------------------------------------------------ drawing */
  function paint() {
    if (!S.on) return;
    paintWave();
    paintVec();
  }
  function paintWave() {
    const c = S.wave;
    if (!c || !c.width) return;
    const ctx = c.getContext('2d');
    const W = c.width, H = c.height;
    const dpr = window.devicePixelRatio || 1;
    const padL = Math.round(22 * dpr), padT = Math.round(8 * dpr), padB = Math.round(6 * dpr);
    const pw = W - padL - 4, ph = H - padT - padB;
    ctx.fillStyle = '#0c0c0d';
    ctx.fillRect(0, 0, W, H);
    // graticule
    ctx.font = `${Math.round(9 * dpr)}px ui-sans-serif, system-ui, sans-serif`;
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    for (let ire = 0; ire <= 100; ire += 10) {
      const y = padT + (1 - ire / 100) * ph;
      ctx.strokeStyle = ire % 50 === 0 ? 'rgba(255,255,255,.22)' : 'rgba(255,255,255,.09)';
      ctx.beginPath(); ctx.moveTo(padL, Math.round(y) + 0.5); ctx.lineTo(padL + pw, Math.round(y) + 0.5); ctx.stroke();
      if (ire % 20 === 0) { ctx.fillStyle = 'rgba(255,255,255,.45)'; ctx.fillText(String(ire), padL - 4, y); }
    }
    const d = S.data;
    if (!d) return;
    // the trace: one image the size of the sample, scaled into the plot
    const img = new ImageData(d.w, WAVE_BINS);
    let peak = 1;
    for (let i = 0; i < d.wave.length; i++) if (d.wave[i] > peak) peak = d.wave[i];
    const norm = Math.log(1 + peak);
    for (let x = 0; x < d.w; x++) {
      for (let b = 0; b < WAVE_BINS; b++) {
        const v = d.wave[x * WAVE_BINS + b];
        if (!v) continue;
        const a = 0.35 + 0.65 * Math.log(1 + v) / norm;
        const o = ((WAVE_BINS - 1 - b) * d.w + x) * 4;
        img.data[o] = 120 * a; img.data[o + 1] = 255 * a; img.data[o + 2] = 140 * a; img.data[o + 3] = 255;
      }
    }
    blit(ctx, img, padL, padT, pw, ph);
  }
  function paintVec() {
    const c = S.vec;
    if (!c || !c.width) return;
    const ctx = c.getContext('2d');
    const W = c.width, H = c.height;
    const side = Math.min(W, H) - 8, cx = W / 2, cy = H / 2, R = side / 2 * (0.5 / 0.6);
    ctx.fillStyle = '#0c0c0d';
    ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = 'rgba(255,255,255,.18)';
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.stroke();
    ctx.strokeStyle = 'rgba(255,255,255,.08)';
    ctx.beginPath(); ctx.moveTo(cx - R, cy); ctx.lineTo(cx + R, cy); ctx.moveTo(cx, cy - R); ctx.lineTo(cx, cy + R); ctx.stroke();
    // the skin-tone line
    const sk = SKIN_DEG * Math.PI / 180;
    ctx.strokeStyle = 'rgba(230,180,140,.35)';
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + Math.cos(sk) * R, cy - Math.sin(sk) * R); ctx.stroke();
    // the 75 % targets
    const dpr = window.devicePixelRatio || 1;
    ctx.font = `${Math.round(8.5 * dpr)}px ui-sans-serif, system-ui, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    TARGETS.forEach(([name, rgb]) => {
      const [cb, cr] = chroma(...rgb.map((v) => v * 0.75));
      const x = cx + cb / 0.5 * R, y = cy - cr / 0.5 * R, b = 4 * dpr;
      ctx.strokeStyle = 'rgba(255,255,255,.4)';
      ctx.strokeRect(x - b, y - b, b * 2, b * 2);
      const lx = cx + cb / 0.5 * R * 1.28, ly = cy - cr / 0.5 * R * 1.28;
      ctx.fillStyle = 'rgba(255,255,255,.45)';
      ctx.fillText(name, lx, ly);
    });
    const d = S.data;
    if (!d) return;
    const V = d.VEC;
    const img = new ImageData(V, V);
    let peak = 1;
    for (let i = 0; i < d.vec.length; i++) if (d.vec[i] > peak) peak = d.vec[i];
    const norm = Math.log(1 + peak);
    for (let i = 0; i < d.vec.length; i++) {
      const v = d.vec[i];
      if (!v) continue;
      const a = 0.4 + 0.6 * Math.log(1 + v) / norm;
      img.data[i * 4] = 140 * a; img.data[i * 4 + 1] = 255 * a; img.data[i * 4 + 2] = 160 * a; img.data[i * 4 + 3] = 255;
    }
    // the histogram spans |chroma| 0.6 each way: the circle (0.5) is R wide
    const span = R * 0.6 / 0.5;
    blit(ctx, img, cx - span, cy - span, span * 2, span * 2, true);
  }
  let stage = null;
  function blit(ctx, img, x, y, w, h, add) {
    if (!stage) stage = document.createElement('canvas');
    if (stage.width !== img.width || stage.height !== img.height) { stage.width = img.width; stage.height = img.height; }
    stage.getContext('2d').putImageData(img, 0, 0);
    ctx.save();
    ctx.imageSmoothingEnabled = true;
    if (add) ctx.globalCompositeOperation = 'lighter';
    ctx.drawImage(stage, x, y, w, h);
    ctx.restore();
  }

  /* ------------------------------------------------------------ mount */
  function mount() {
    if (!build()) return;
    if (store.get()) open();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();

  window.scopes = {
    open, close,
    get on() { return S.on; },
    get samples() { return S.samples; },
    stats: () => (S.stats ? JSON.parse(JSON.stringify(S.stats)) : null),
    refresh: () => { if (S.on) take(performance.now()); return S.stats ? JSON.parse(JSON.stringify(S.stats)) : null; },
  };
})();
