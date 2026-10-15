/* Roughcut — Effect Controls (redesign wave 2, lane EFFECTS; REDESIGN-GOAL dimension 7).
 *
 * Premiere's Effect Controls for the selected shot, in its order and its words:
 *
 *   Motion          Position (x, y in a 1920×1080 frame), Scale, Rotation
 *   Opacity         Opacity %
 *   Time Remapping  Speed (the shot's own rate, the timeline's `speed`)
 *   Volume          Level dB
 *   Lumetri Color   Temperature, Tint, Exposure (the colour block's hand balance — these
 *                   render today), Contrast, Saturation; the auto, the look and the match
 *                   behind "Creative & match"
 *   fx stack        the designed effects on the shot: bypass (fx) and order
 *
 * Every value is a blue number you drag sideways (shift ×10, alt ×0.1), click to type,
 * or step with ↑/↓; ↺ puts it back. A drag is one undo entry, a typed value one, a step
 * one. The Program monitor answers within a frame: Motion and Opacity are a CSS
 * transform / opacity on the picture's layers, Volume the live video's volume, and the
 * colour goes through /grade.js's shader — a gain before the shot's LUT for the part of
 * the balance the LUT was not baked with, contrast and saturation after it (grade.js
 * `adjustNow`). The autosave then bakes the balance into the LUT and the difference goes
 * to nothing.
 *
 * Where the values live (the EDL):
 *   - Temperature / Tint / Exposure: `colour.shots[id].balance` — the colour block,
 *     exactly what "warmer" wrote, so the render already honours them.
 *   - everything else: the segment's `controls` — only what differs from the default:
 *       {"position": [x, y] (fractions of the frame, 0.5 = centre), "scale": %,
 *        "rotation": degrees, "opacity": %, "volume_db": dB, "contrast": -100..100,
 *        "saturation": 0..200, "fx_off": [effect ids], "fx_order": [effect ids]}
 *     validated by server.py's `clean_controls` on every save. The export draws every
 *     one of them (research/tools/assemble.py `encode_part`); each row says so on hover.
 *
 * Loaded after app.js. The board's globals (`tl`, `segs`, `colour`, `C`, `player`,
 * `shotOverride`, `tidyColour`, `renderInspector`, `cueAt`, `inspected`, `gradeApi`) are
 * reached by name at call time and guarded.
 *
 * API (window.effects):
 *   build(host, seg, {gen})   arrange the shot strip's controls into the sections
 *   fill(box, seg)            paint the values; re-apply the live preview
 *   adjust(id, lut)           grade.js asks: {pre, con, sat} for the live shot
 *   onShot(id)                grade.js says the monitor is on this shot now
 *   apply()                   re-apply Motion / Opacity / Volume to the monitor
 *   value(key, seg?)          a property's value as shown (tests read it)
 *   set(key, value, seg?)     one undoable change, as a typed value would make it
 */
(() => {
  'use strict';

  const FRAME_W = 1920, FRAME_H = 1080;     // the readout's frame, Premiere's default sequence
  const GAIN = 0.15;                        // colour.CLAMPS widest gain: validate_balance's clamp
  const EXPOSURE = [0.75, 1.5];             // and its exposure clamp
  // Every property here renders (verdict 3, punch 3: assemble.py's `encode_part`).
  const RENDERED = 'the export renders it';
  const store = {
    get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* fine */ } },
  };
  const OPEN_KEY = 'roughcut.ec.open';
  const open = Object.assign({ motion: true, opacity: true, time: true, volume: true, lumetri: true, fxs: true },
    store.get(OPEN_KEY) || {});

  /* app.js's top-level bindings share the global lexical scope with this script; each is
   * reached by name at call time, `typeof`-guarded, so the page is whole without them. */
  /* eslint-disable no-undef */
  const GLOBALS = {
    colour: () => (typeof colour !== 'undefined' ? colour : undefined),
    C: () => (typeof C !== 'undefined' ? C : undefined),
    segs: () => (typeof segs !== 'undefined' ? segs : undefined),
    player: () => (typeof player !== 'undefined' ? player : undefined),
    inspecting: () => (typeof inspecting !== 'undefined' ? inspecting : undefined),
    shotOverride: () => (typeof shotOverride === 'function' ? shotOverride : undefined),
    tidyColour: () => (typeof tidyColour === 'function' ? tidyColour : undefined),
    renderInspector: () => (typeof renderInspector === 'function' ? renderInspector : undefined),
    cueAt: () => (typeof cueAt === 'function' ? cueAt : undefined),
    inspected: () => (typeof inspected === 'function' ? inspected : undefined),
    gradeApi: () => (typeof gradeApi === 'function' ? gradeApi : undefined),
    liveVideo: () => (typeof liveVideo === 'function' ? liveVideo : undefined),
  };
  const g = (name) => { try { return GLOBALS[name](); } catch (e) { return undefined; } };
  /* eslint-enable no-undef */
  const TL = () => window.tl || null;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const r4 = (v) => Math.round(v * 10000) / 10000;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  /* ------------------------------------------------------------ colour: the balance */
  const NEUTRAL = { gain: [1, 1, 1], exposure: 1, knee: 0.8, lift: 0 };
  const autoBal = new Map();               // id → the auto's balance, last seen in /api/colour

  function colourBlock() { const c = g('colour'); return c && typeof c === 'object' ? c : {}; }
  function resolved(id) {
    const CC = g('C');
    const shot = CC && Array.isArray(CC.shots) ? CC.shots.find((s) => s.id === id) : null;
    if (shot && shot.balance && shot.balance.source !== 'hand') autoBal.set(id, shot.balance);
    return shot;
  }
  /* The balance the monitor should show for the shot: the hand's, else the auto's (none
   * when the film or the shot is off). */
  function wantBalance(id) {
    const blk = colourBlock();
    const over = (blk.shots || {})[id] || {};
    if (over.balance) return over.balance;
    if (blk.mode === 'off' || over.auto === false) return null;
    const shot = resolved(id);
    if (autoBal.has(id)) return autoBal.get(id);
    return shot && shot.balance && shot.balance.source !== 'hand' ? shot.balance : null;
  }
  function lumetriOf(b) {
    b = b || NEUTRAL;
    const [r, gg, bl] = b.gain;
    const L = (r + bl) / 2 || 1;
    return {
      temperature: ((r - bl) / (r + bl)) / GAIN * 100,
      tint: (1 - gg / L) / GAIN * 100,
      exposure: Math.log2(b.exposure || 1),
    };
  }
  function balanceWith(b, key, v) {
    b = b || NEUTRAL;
    const out = { gain: [...b.gain], exposure: b.exposure, knee: b.knee ?? 0.8, lift: b.lift ?? 0 };
    const [r, gg, bl] = out.gain;
    const L = (r + bl) / 2 || 1;
    if (key === 'temperature') {
      const t = clamp(v, -100, 100) / 100 * GAIN;
      out.gain[0] = L * (1 + t); out.gain[2] = L * (1 - t);
    } else if (key === 'tint') {
      out.gain[1] = L * (1 - clamp(v, -100, 100) / 100 * GAIN);
    } else if (key === 'exposure') {
      out.exposure = clamp(2 ** v, EXPOSURE[0], EXPOSURE[1]);
    }
    out.gain = out.gain.map((x) => r4(clamp(x, 1 - GAIN, 1 + GAIN)));
    out.exposure = r4(clamp(out.exposure, EXPOSURE[0], EXPOSURE[1]));
    void gg;
    return out;
  }

  /* ------------------------------------------------------------ the properties */
  const ctl = (seg) => (seg && seg.controls) || {};
  function setCtl(seg, key, v, dflt) {
    const c = { ...ctl(seg) };
    if (v == null || (Array.isArray(dflt) ? JSON.stringify(v) === JSON.stringify(dflt) : v === dflt)) delete c[key];
    else c[key] = v;
    if (Object.keys(c).length) seg.controls = c; else delete seg.controls;
  }
  function plain(key, dflt, lo, hi, step, digits, unit, label, sec) {
    return {
      key, sec, label, lo, hi, step, digits, unit, dflt, hover: RENDERED,
      get: (seg) => (ctl(seg)[key] ?? dflt),
      set: (seg, v) => setCtl(seg, key, Math.round(clamp(v, lo, hi) * 1000) / 1000, dflt),
    };
  }
  function posProp(axis) {
    const k = axis === 'x' ? 0 : 1, size = axis === 'x' ? FRAME_W : FRAME_H;
    return {
      key: `pos${axis}`, sec: 'motion', label: 'Position', lo: -10 * size, hi: 11 * size, step: 1, digits: 1,
      unit: '', dflt: size / 2, hover: `${axis.toUpperCase()} in a ${FRAME_W}×${FRAME_H} frame · ${RENDERED}`,
      get: (seg) => ((ctl(seg).position || [0.5, 0.5])[k]) * size,
      set: (seg, v) => {
        const p = [...(ctl(seg).position || [0.5, 0.5])];
        p[k] = Math.round(clamp(v, -10 * size, 11 * size) / size * 1e6) / 1e6;
        setCtl(seg, 'position', p, [0.5, 0.5]);
      },
    };
  }
  function balProp(key, label, lo, hi, step, digits, unit, hover) {
    return {
      key, sec: 'lumetri', label, lo, hi, step, digits, unit, dflt: 0, colour: true, hover,
      get: (seg) => lumetriOf(wantBalance(seg.id))[key],
      set: (seg, v) => {
        const so = g('shotOverride');
        if (typeof so !== 'function') return;
        so(seg.id, true).balance = balanceWith(wantBalance(seg.id), key, v);
      },
    };
  }
  const PROPS = [
    posProp('x'), posProp('y'),
    plain('scale', 100, 0, 1000, 0.1, 1, '', 'Scale', 'motion'),
    plain('rotation', 0, -3600, 3600, 0.1, 1, '°', 'Rotation', 'motion'),
    plain('opacity', 100, 0, 100, 0.1, 1, ' %', 'Opacity', 'opacity'),
    plain('volume_db', 0, -60, 6, 0.1, 2, ' dB', 'Level', 'volume'),
    balProp('temperature', 'Temperature', -100, 100, 0.5, 1, '',
      'the shot\'s white balance, warm ↔ cool — saved as its hand balance; the export renders it'),
    balProp('tint', 'Tint', -100, 100, 0.5, 1, '',
      'green ↔ magenta — saved as its hand balance; the export renders it'),
    balProp('exposure', 'Exposure', -0.415, 0.585, 0.01, 2, '',
      'stops, within what the auto may do (×0.75 to ×1.5) — the export renders it'),
    plain('contrast', 0, -100, 100, 0.5, 1, '', 'Contrast', 'lumetri'),
    plain('saturation', 100, 0, 200, 0.5, 1, '', 'Saturation', 'lumetri'),
  ];
  const BY_KEY = new Map(PROPS.map((p) => [p.key, p]));
  // volume previews up to 0 dB only: the monitor's element cannot play louder than full
  BY_KEY.get('volume_db').hover = `the shot's level · above 0.00 dB is heard only in the export (the monitor plays at most full level) · ${RENDERED}`;

  const LABELS = { posx: 'position', posy: 'position', volume_db: 'volume' };
  const labelOf = (p) => LABELS[p.key] || p.key;
  const fmtVal = (p, v) => {
    const s = Number(v).toFixed(p.digits);
    return (p.key === 'exposure' && v > 0 ? '+' : '') + (s === '-0.0' || s === '-0.00' ? s.slice(1) : s) + p.unit;
  };

  /* ------------------------------------------------------------ one change, one undo */
  let gesture = null;                       // {p, seg, began} while a value is dragged
  function cur(seg) {
    const t = TL();
    if (seg && t) return t.byId(seg.id) || seg;
    const insp = g('inspected');
    return typeof insp === 'function' ? insp() : null;
  }
  function begin(p) {
    const t = TL();
    return !!(t && t.begin(labelOf(p)));
  }
  function finish(p) {
    const t = TL();
    if (p.colour) { const td = g('tidyColour'); if (typeof td === 'function') td(); }
    if (t) t.commit();                      // touch → autosave; 'change' → the inspector repaints
    const ri = g('renderInspector');
    if (typeof ri === 'function') ri();
  }
  function change(p, seg, v) {
    seg = cur(seg);
    if (!seg || !Number.isFinite(v)) return false;
    if (!begin(p)) return false;
    p.set(seg, v);
    preview(seg);
    finish(p);
    return true;
  }

  /* ------------------------------------------------------------ the live preview */
  const LAYERS = ['#pv0', '#pv1', '#gradeCanvas', '#fxCanvas'];
  let liveId = null;
  let lastStyle = '';
  function monitorShot() {
    const gr = typeof g('gradeApi') === 'function' ? g('gradeApi')() : null;
    if (gr && gr.shot) return gr.shot;
    const pl = g('player'), ss = g('segs');
    return pl && Array.isArray(ss) && ss[pl.idx] ? String(ss[pl.idx].id) : null;
  }
  function apply() {
    const t = TL();
    liveId = monitorShot();
    const seg = t && liveId ? t.byId(liveId) : null;
    const c = ctl(seg);
    const pos = c.position || [0.5, 0.5];
    const moved = pos[0] !== 0.5 || pos[1] !== 0.5 || (c.scale ?? 100) !== 100 || (c.rotation ?? 0) !== 0;
    const transform = moved
      ? `translate(${r4((pos[0] - 0.5) * 100)}%, ${r4((pos[1] - 0.5) * 100)}%) rotate(${c.rotation ?? 0}deg) scale(${r4((c.scale ?? 100) / 100)})`
      : '';
    const opacity = (c.opacity ?? 100) !== 100 ? String(r4(c.opacity / 100)) : '';
    const key = `${transform}|${opacity}`;
    if (key !== lastStyle) {
      lastStyle = key;
      LAYERS.forEach((s) => {
        const el = document.querySelector(s);
        if (!el) return;
        el.style.transform = transform;
        el.style.opacity = opacity;
      });
    }
    const lv = typeof g('liveVideo') === 'function' ? g('liveVideo')() : null;
    if (lv) {
      // One owner for an element's level (meters.js rcAudio): the shot's Volume is the
      // 'clip' factor, A1's mute/solo/gain the 'track' one, so above 0 dB works too.
      const lin = 10 ** ((c.volume_db ?? 0) / 20);
      if (window.rcAudio) window.rcAudio.setLevel(lv, 'clip', lin);
      else if (Math.abs(lv.volume - clamp(lin, 0, 1)) > 1e-4) lv.volume = clamp(lin, 0, 1);
    }
  }
  /* The edited shot is shown: when the monitor is elsewhere, it parks on the shot's
   * first frame (Premiere users park on the clip they adjust; here one change does it). */
  function preview(seg) {
    const t = TL();
    if (seg && t && monitorShot() !== String(seg.id)) {
      const i = t.indexOf(seg.id);
      const cue = g('cueAt');
      if (i >= 0 && typeof cue === 'function' && !(g('player') || {}).playing) cue(i, seg.in);
    }
    apply();
    const gr = typeof g('gradeApi') === 'function' ? g('gradeApi')() : null;
    if (gr) gr.draw();
  }
  function adjust(id) {
    const t = TL();
    const seg = t ? t.byId(id) : null;
    const c = ctl(seg);
    const out = { pre: [1, 1, 1], con: 1 + (c.contrast ?? 0) / 200, sat: (c.saturation ?? 100) / 100 };
    const gr = typeof g('gradeApi') === 'function' ? g('gradeApi')() : null;
    const lut = gr ? gr.lut : null;
    if (lut && seg) {
      const want = wantBalance(String(id));
      const w = want || NEUTRAL;
      const b = lut.balance || NEUTRAL;
      out.pre = [0, 1, 2].map((k) => (w.gain[k] * (w.exposure || 1)) / ((b.gain[k] * (b.exposure || 1)) || 1));
      // a table baked with no balance has no shoulder or lift: the shader adds the wanted one
      if (!lut.balance && want) { out.knee = want.knee ?? 0.8; out.lift = want.lift ?? 0; }
    }
    return out;
  }
  function onShot(id) {
    liveId = id;
    apply();
  }

  /* ------------------------------------------------------------ the panel */
  const SECTIONS = [
    { key: 'motion', name: 'Motion', fx: true },
    { key: 'opacity', name: 'Opacity', fx: true },
    { key: 'time', name: 'Time Remapping', fx: true },
    { key: 'volume', name: 'Volume', fx: true },
    { key: 'lumetri', name: 'Lumetri Color', fx: true, colour: true },
    { key: 'fxs', name: 'Designed effects', fx: true },
  ];
  const RESET_SVG = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3a5 5 0 1 1-4.6 3H5.2A3.5 3.5 0 1 0 8 4.5V6.5L5 4l3-2.5z" fill="currentColor"/></svg>';
  function row(p) {
    return `<div class="ec-row" data-row="${p.key}" title="${esc(p.hover)}">
      <span class="ec-name">${esc(p.label)}</span>
      <span class="ec-vals"><input class="ec-v" data-k="${p.key}" readonly spellcheck="false" autocomplete="off" role="spinbutton"></span>
      <button type="button" class="ec-reset" data-reset="${p.key}" title="reset ${esc(p.label.toLowerCase())}">${RESET_SVG}</button>
    </div>`;
  }
  function sectionHtml(s, gen) {
    if (s.colour && gen) return '';
    let body = '';
    if (s.key === 'motion') {
      body = `<div class="ec-row" data-row="pos" title="${esc(BY_KEY.get('posx').hover)}">
          <span class="ec-name">Position</span>
          <span class="ec-vals"><input class="ec-v" data-k="posx" readonly spellcheck="false" autocomplete="off" role="spinbutton" title="X"><input class="ec-v" data-k="posy" readonly spellcheck="false" autocomplete="off" role="spinbutton" title="Y"></span>
          <button type="button" class="ec-reset" data-reset="posx,posy" title="reset position">${RESET_SVG}</button>
        </div>${row(BY_KEY.get('scale'))}${row(BY_KEY.get('rotation'))}`;
    } else if (s.key === 'opacity') body = row(BY_KEY.get('opacity'));
    else if (s.key === 'volume') body = row(BY_KEY.get('volume_db'));
    else if (s.key === 'time') {
      body = `<div class="ec-row" data-row="speed" title="the shot's rate — the export renders it">
          <span class="ec-name">Speed</span><span class="ec-vals ec-slot" data-slot="speed"></span>
          <button type="button" class="ec-reset" data-reset="speed" title="reset speed to 100%">${RESET_SVG}</button></div>`;
    } else if (s.key === 'lumetri') {
      body = ['temperature', 'tint', 'exposure', 'contrast', 'saturation'].map((k) => row(BY_KEY.get(k))).join('')
        + '<div class="ec-slot ec-quick" data-slot="nudge"></div>'
        + '<div class="ec-slot ec-creative" data-slot="look"></div>';
    } else if (s.key === 'fxs') {
      body = '<div class="ec-stack"></div>';
    }
    const reset = s.key === 'fxs' ? '' : `<button type="button" class="ec-reset ec-sreset" data-sreset="${s.key}" title="reset ${esc(s.name)}${s.key === 'lumetri' ? ' — the balance back to the auto' : ''}">${RESET_SVG}</button>`;
    return `<section class="ec-sec${open[s.key] ? ' open' : ''}" data-sec="${s.key}">
      <div class="ec-sh"><button type="button" class="ec-twirl" data-twirl="${s.key}" aria-expanded="${!!open[s.key]}"><i class="ec-tri"></i><b class="ec-fx">fx</b><span>${esc(s.name)}</span></button>${reset}</div>
      <div class="ec-sb">${body}</div></section>`;
  }

  function build(host, seg, opts = {}) {
    if (!host) return;
    const box = host.closest('.shot') || host;
    const gen = !!opts.gen;
    const speed = host.querySelector('select.speed');
    const nudge = host.querySelector('.nudge');
    const disc = host.querySelector('[data-disc=look]');
    const pane = host.querySelector('.colour[data-pane=look]');
    const wrap = document.createElement('div');
    wrap.className = 'ec';
    wrap.innerHTML = SECTIONS.map((s) => sectionHtml(s, gen)).join('');
    const slot = (n) => wrap.querySelector(`[data-slot="${n}"]`);
    if (speed && slot('speed')) slot('speed').appendChild(speed);
    if (nudge && slot('nudge')) { slot('nudge').appendChild(nudge); }
    if (slot('look')) {
      if (disc) { disc.textContent = 'Creative & match ▸'; slot('look').appendChild(disc); }
      if (pane) slot('look').appendChild(pane);
    }
    host.replaceChildren(wrap);
    if (gen) {                                // a generated clip has no grade: keep the old nodes out of sight
      [nudge, disc, pane].forEach((n) => { if (n) { n.hidden = true; host.appendChild(n); } });
    }
    bind(wrap);
    void box;
    void seg;
  }

  function bind(wrap) {
    wrap.addEventListener('click', (e) => {
      const tw = e.target.closest('[data-twirl]');
      if (tw) {
        const k = tw.dataset.twirl;
        open[k] = !open[k];
        store.set(OPEN_KEY, open);
        const sec = tw.closest('.ec-sec');
        sec.classList.toggle('open', open[k]);
        tw.setAttribute('aria-expanded', String(open[k]));
        return;
      }
      const rs = e.target.closest('[data-reset]');
      if (rs) { e.stopPropagation(); resetKeys(rs.dataset.reset.split(',')); return; }
      const sr = e.target.closest('[data-sreset]');
      if (sr) { e.stopPropagation(); resetSection(sr.dataset.sreset); return; }
      const fb = e.target.closest('[data-fxact]');
      if (fb) { e.stopPropagation(); stackAct(fb.dataset.fxact, fb.dataset.id); }
    });
    wrap.querySelectorAll('.ec-v').forEach((el) => {
      el.addEventListener('pointerdown', (e) => onDown(e, el));
      el.addEventListener('keydown', (e) => onKey(e, el));
      el.addEventListener('blur', () => { const p = BY_KEY.get(el.dataset.k); if (p && !el.readOnly) close(el, p, true); });
    });
  }

  function resetKeys(keys) {
    const seg = cur();
    if (!seg) return;
    if (keys[0] === 'speed') { const t = TL(); if (t) t.setSpeed(seg.id, 1); return; }
    const ps = keys.map((k) => BY_KEY.get(k)).filter(Boolean);
    if (!ps.length || !begin(ps[0])) return;
    ps.forEach((p) => p.set(seg, p.dflt));
    preview(seg);
    finish(ps[0]);
  }
  function resetSection(key) {
    const seg = cur();
    if (!seg) return;
    if (key === 'time') { const t = TL(); if (t) t.setSpeed(seg.id, 1); return; }
    const ps = PROPS.filter((p) => p.sec === key);
    const t = TL();
    if (!ps.length || !t || !t.begin(key === 'lumetri' ? 'colour reset' : key)) return;
    ps.filter((p) => !p.colour).forEach((p) => p.set(seg, p.dflt));
    if (key === 'lumetri') {
      const so = g('shotOverride');
      const o = typeof so === 'function' ? so(seg.id) : null;
      if (o) delete o.balance;               // back to the auto, as "reset" always meant
    }
    preview(seg);
    finish(ps.find((p) => p.colour) || ps[0]);
  }

  /* drag sideways to scrub; a click without a drag types. The value is a read-only
   * <input> until it is typed in, so the board's keys (the timeline's ↑/↓, J/K/L…) leave
   * it alone while it has the focus, and its own keys step it. */
  function onDown(e, el) {
    if (e.button !== 0 || !el.readOnly) return;     // typing: the caret is the input's
    const p = BY_KEY.get(el.dataset.k);
    const seg = cur();
    if (!p || !seg) return;
    e.preventDefault();
    const x0 = e.clientX, v0 = p.get(seg);
    gesture = { p, seg, began: false };
    if (el.setPointerCapture) { try { el.setPointerCapture(e.pointerId); } catch (err) { /* fine */ } }
    const move = (ev) => {
      const dx = ev.clientX - x0;
      if (!gesture || !gesture.began) {
        if (!gesture || Math.abs(dx) < 3) return;
        if (!begin(p)) return;
        gesture.began = true;
        el.classList.add('drag');
      }
      const mul = ev.shiftKey ? 10 : ev.altKey ? 0.1 : 1;
      const s2 = cur(seg);
      p.set(s2, clamp(v0 + dx * p.step * mul, p.lo, p.hi));
      el.value = fmtVal(p, p.get(s2));
      preview(s2);
    };
    const up = () => {
      el.removeEventListener('pointermove', move);
      el.removeEventListener('pointerup', up);
      el.removeEventListener('pointercancel', up);
      el.classList.remove('drag');
      const gs = gesture;
      gesture = null;
      if (gs && gs.began) finish(p);
      else edit(el, p);
    };
    el.addEventListener('pointermove', move);
    el.addEventListener('pointerup', up);
    el.addEventListener('pointercancel', up);
  }
  function onKey(e, el) {
    const p = BY_KEY.get(el.dataset.k);
    const seg = cur();
    if (!p || !seg) return;
    e.stopPropagation();                       // a value's keys are its own
    if (!el.readOnly) {                        // typing
      if (e.key === 'Enter') { e.preventDefault(); close(el, p, true); }
      else if (e.key === 'Escape') { e.preventDefault(); close(el, p, false); }
      return;
    }
    const mod = e.metaKey || e.ctrlKey;
    if (mod && e.key.toLowerCase() === 'z') {  // the board's undo, which ignores inputs
      e.preventDefault();
      const t = TL();
      if (t) { if (e.shiftKey) t.redo(); else t.undo(); }
      return;
    }
    if (e.key === 'Enter') { e.preventDefault(); edit(el, p); return; }
    if (e.key === 'Escape') { el.blur(); return; }
    if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
    e.preventDefault();
    const mul = e.shiftKey ? 10 : e.altKey ? 0.1 : 1;
    change(p, seg, clamp(p.get(seg) + (e.key === 'ArrowUp' ? 1 : -1) * p.step * mul * 10, p.lo, p.hi));
    el.value = fmtVal(p, p.get(cur(seg)));
  }
  function edit(el, p) {
    const seg = cur();
    if (!seg || !el.readOnly) return;
    el.readOnly = false;
    el.classList.add('editing');
    el.value = Number(p.get(seg)).toFixed(p.digits);
    el.focus();
    el.select();
  }
  function close(el, p, commit) {
    if (el.readOnly) return;
    const seg = cur();
    const v = parseFloat(el.value);
    el.readOnly = true;
    el.classList.remove('editing');
    if (commit && seg && Number.isFinite(v) && Math.abs(v - p.get(seg)) > 1e-9) change(p, seg, clamp(v, p.lo, p.hi));
    const s2 = cur();
    if (s2) el.value = fmtVal(p, p.get(s2));
    el.blur();
  }

  /* ------------------------------------------------------------ the fx stack */
  function fxOf(seg) {
    const fx = window.fx;
    const all = fx && fx.state && Array.isArray(fx.state.effects) ? fx.state.effects : [];
    const mine = all.filter((e) => String(e.shot) === String(seg.id) && e.status !== 'removed');
    return order(seg, mine);
  }
  function order(seg, list) {
    const o = ctl(seg).fx_order || [];
    const rank = (e) => { const i = o.indexOf(e.id); return i < 0 ? 1e6 + list.indexOf(e) : i; };
    return list.slice().sort((a, b) => rank(a) - rank(b));
  }
  function stackAct(act, id) {
    const seg = cur();
    const t = TL();
    if (!seg || !t) return;
    if (act === 'open') { if (window.dock && dock.open) dock.open('fx'); return; }
    const list = fxOf(seg).map((e) => e.id);
    if (!t.begin(act === 'bypass' ? 'fx on/off' : 'fx order')) return;
    if (act === 'bypass') {
      const off = new Set(ctl(seg).fx_off || []);
      if (off.has(id)) off.delete(id); else off.add(id);
      setCtl(seg, 'fx_off', [...off], []);
      if (!off.size) setCtl(seg, 'fx_off', null);
    } else {
      const i = list.indexOf(id), j = act === 'up' ? i - 1 : i + 1;
      if (i >= 0 && j >= 0 && j < list.length) { [list[i], list[j]] = [list[j], list[i]]; setCtl(seg, 'fx_order', list, []); }
    }
    t.commit();
    const ri = g('renderInspector');
    if (typeof ri === 'function') ri();
    if (window.fx && typeof fx.redraw === 'function') fx.redraw();
  }
  function paintStack(box, seg) {
    const host = box && box.querySelector('.ec-stack');
    if (!host || !seg) return;
    const list = fxOf(seg);
    const off = new Set(ctl(seg).fx_off || []);
    const key = JSON.stringify([list.map((e) => [e.id, e.name, e.status]), [...off]]);
    if (host.dataset.key === key) return;
    host.dataset.key = key;
    host.innerHTML = list.length
      ? list.map((e, i) => `<div class="ec-row ec-fxrow${off.has(e.id) ? ' off' : ''}" data-fx="${esc(e.id)}" title="${esc(e.note || e.name)} · bypass and order: ${RENDERED}">
          <button type="button" class="ec-fxtog${off.has(e.id) ? '' : ' on'}" data-fxact="bypass" data-id="${esc(e.id)}" title="${off.has(e.id) ? 'turn this effect on' : 'bypass this effect'}">fx</button>
          <span class="ec-name">${esc(e.name)}${e.status === 'proposed' ? ' <i class="ec-prop">proposed</i>' : ''}</span>
          <span class="ec-ord"><button type="button" data-fxact="up" data-id="${esc(e.id)}"${i ? '' : ' disabled'} title="move up the stack">▲</button><button type="button" data-fxact="down" data-id="${esc(e.id)}"${i < list.length - 1 ? '' : ' disabled'} title="move down the stack">▼</button></span>
        </div>`).join('')
      : '<div class="ec-empty">No designed effects on this shot. <button type="button" class="ec-link" data-fxact="open">Design one in Effects ▸</button></div>';
  }

  /* ------------------------------------------------------------ fill */
  function fill(box, seg) {
    if (!box || !seg) return;
    box.querySelectorAll('.ec-v').forEach((el) => {
      const p = BY_KEY.get(el.dataset.k);
      if (!p || !el.readOnly || (gesture && gesture.p === p)) return;
      const v = p.get(seg);
      const txt = fmtVal(p, v);
      if (el.value !== txt) el.value = txt;
      el.setAttribute('aria-valuenow', String(v));
      const r = el.closest('.ec-row');
      if (r) r.classList.toggle('changed', p.key.startsWith('pos')
        ? BY_KEY.get('posx').get(seg) !== FRAME_W / 2 || BY_KEY.get('posy').get(seg) !== FRAME_H / 2
        : Math.abs(v - p.dflt) > 1e-6);
    });
    const spd = box.querySelector('[data-row=speed]');
    if (spd) spd.classList.toggle('changed', Number(seg.speed || 1) !== 1);
    paintStack(box, seg);
    apply();
    const gr = typeof g('gradeApi') === 'function' ? g('gradeApi')() : null;
    if (gr) gr.draw();
  }

  /* repaint the stack when the FX tool's list changes (fx.js calls this) */
  function refreshStack() {
    const box = document.querySelector('#inspector .shot');
    const insp = g('inspected');
    const seg = typeof insp === 'function' ? insp() : null;
    if (box && seg) paintStack(box, seg);
  }

  window.effects = {
    build, fill, adjust, onShot, apply, refreshStack,
    value: (key, seg) => { const p = BY_KEY.get(key); const s = cur(seg); return p && s ? p.get(s) : null; },
    set: (key, v, seg) => { const p = BY_KEY.get(key); return p ? change(p, seg, v) : false; },
    get live() { return liveId; },
  };

  // The inspector may already be on a shot built without the sections: build it again.
  try {
    if (typeof g('renderInspector') === 'function' && g('inspecting') && typeof g('inspecting') === 'object') {
      // eslint-disable-next-line no-undef
      inspecting = null;
      g('renderInspector')();
    }
  } catch (e) { /* the next selection builds it */ }
})();
