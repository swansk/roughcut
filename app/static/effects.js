/* Roughcut — Effect Controls (redesign wave 2, lane EFFECTS; REDESIGN-GOAL dimension 7).
 *
 * Premiere's Effect Controls for the selected shot, in its order and its words:
 *
 *   Motion          Position (x, y in a 1920×1080 frame), Scale, Rotation
 *   Opacity         Opacity %
 *   Time Remapping  Speed (the shot's own rate, the timeline's `speed`)
 *   Volume          Level dB
 *   Lumetri Color   › Basic Correction, Premiere's order, each a slider and a number:
 *                   White Balance (Temperature, Tint), Tone (Exposure, Contrast,
 *                   Highlights, Shadows, Whites, Blacks), Saturation — Temperature / Tint /
 *                   Exposure are the colour block's hand balance; the auto, the look and the
 *                   match behind "Creative & match"
 *   fx stack        the designed effects on the shot: bypass (fx) and order
 *
 * Keyframes (wave 6, verdict 5 punch 4): Position, Scale, Rotation, Opacity and Level each
 * have Premiere's stopwatch; on, the row shows ◀ ◆ ▶ and its lane of the panel's mini
 * timeline (the shot's span, the playhead, a diamond per key — drag one in time, click the
 * lane to move the playhead). The Program monitor shows the keyed values at the playhead,
 * parked or playing. Each stopwatch, key add / remove / move is one undo entry.
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
 *        "highlights" / "shadows" / "whites" / "blacks": -100..100 (grade.js has the
 *        formula), "saturation": 0..200, "fx_off": [effect ids], "fx_order": [effect ids],
 *        "keys": {prop: [[t, v], ...]} (see "keyframes" below)}
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
 *   keys(prop, seg?)          a property's keyframes, [[t, v], ...] (see "keyframes" below)
 *   now(seg?)                 the shot's controls with its keys evaluated at the playhead
 *   animate(group)            the stopwatch: pos | scale | rotation | opacity | volume_db
 *   key(act, group)           ◀ ◆ ▶: 'prev' | 'key' (add or remove at the playhead) | 'next'
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
  const open = Object.assign({ motion: true, opacity: true, time: true, volume: true, lumetri: true, basic: true, fxs: true },
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
    programSync: () => (typeof programSync === 'function' ? programSync : undefined),
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

  /* ------------------------------------------------------------ V2+ clips (wave 9, V2EC)
   * A clip on V2, V3… is a target too: selected (tl.state.osel, no V1 shot selected) it
   * gets Motion and Opacity with their keyframes, written to `overlays[i].controls` by
   * the same rules (server.py `clean_controls`), one undo entry each; the Program's
   * #pvV2 follows (app.js `v2Look`) and the export composites it by alpha (assemble.py
   * `overlay_controls_graph`). Its keys count from the clip's own first frame (`at`).
   * Lumetri, Volume and Time Remapping stay V1's: a V2+ clip has no grade path (the
   * render conforms it as shot) and no sound. */
  function isOv(seg) {
    const t = TL();
    return !!(seg && t && typeof t.overlayById === 'function' && t.indexOf(seg.id) < 0
      && t.overlayById(seg.id));
  }
  /* the picture clip selected on V2+, when no V1 shot is */
  function ovSel() {
    const t = TL();
    if (!t || t.state.osel == null || typeof t.overlayById !== 'function') return null;
    if (t.state.sel && t.state.sel.size) return null;
    const o = t.overlayById(t.state.osel);
    return o && !(o.track && o.track[0] === 'A') ? o : null;
  }

  /* ------------------------------------------------------------ the properties */
  const ctl = (seg) => (seg && seg.controls) || {};
  function setCtl(seg, key, v, dflt) {
    const c = { ...ctl(seg) };
    if (v == null || (Array.isArray(dflt) ? JSON.stringify(v) === JSON.stringify(dflt) : v === dflt)) delete c[key];
    else c[key] = v;
    if (Object.keys(c).length) seg.controls = c; else delete seg.controls;
  }

  /* ------------------------------------------------------------ keyframes (verdict 5 punch 4)
   * The schema (judge/LOOP.md, wave 6; the render is research/tools/assemble.py's):
   *   controls.keys = {prop: [[t, v], ...]}
   *     prop  position_x, position_y (fractions of the frame, 0.5 = centre, as `position`),
   *           scale (%), rotation (degrees), opacity (%), volume_db (dB)
   *     t     seconds from the shot's start in FILM time (after its speed), sorted, ≤ 64
   *     v     linear between keys, held before the first and after the last
   *   A prop with keys is animated: its keys win over its static value. Premiere's
   *   stopwatch turns that on (one key at the playhead, the value it had) and off (the
   *   value at the playhead becomes the static one). With the stopwatch on, a changed
   *   value is a key at the playhead: the one there, or a new one. Position is one
   *   property with one stopwatch, as in Premiere: its x and y keys share their times. */
  const KDEF = { position_x: 0.5, position_y: 0.5, scale: 100, rotation: 0, opacity: 100, volume_db: 0 };
  const GROUPS = { pos: ['position_x', 'position_y'], scale: ['scale'], rotation: ['rotation'],
                   opacity: ['opacity'], volume_db: ['volume_db'] };
  const GROUP_NAME = { pos: 'position', scale: 'scale', rotation: 'rotation', opacity: 'opacity', volume_db: 'volume' };
  const MAX_KEYS = 64;
  const keysOf = (seg, kp) => {
    const k = ctl(seg).keys;
    return k && Array.isArray(k[kp]) && k[kp].length ? k[kp] : null;
  };
  const animated = (seg, group) => GROUPS[group].some((kp) => keysOf(seg, kp));
  function staticOf(seg, kp) {
    const c = ctl(seg);
    if (kp === 'position_x' || kp === 'position_y') return (c.position || [0.5, 0.5])[kp === 'position_x' ? 0 : 1];
    return c[kp] ?? KDEF[kp];
  }
  function interp(keys, t) {
    const n = keys.length;
    if (t <= keys[0][0]) return keys[0][1];
    if (t >= keys[n - 1][0]) return keys[n - 1][1];
    for (let i = 1; i < n; i++) {
      if (t <= keys[i][0]) {
        const [t0, v0] = keys[i - 1], [t1, v1] = keys[i];
        return t1 > t0 ? v0 + (v1 - v0) * (t - t0) / (t1 - t0) : v1;
      }
    }
    return keys[n - 1][1];
  }
  const valAt = (seg, kp, t) => { const k = keysOf(seg, kp); return k ? interp(k, t) : staticOf(seg, kp); };
  const kround = (kp, v) => (kp.startsWith('position') ? Math.round(v * 1e6) / 1e6 : Math.round(v * 1000) / 1000);
  function setKeys(seg, kp, list) {
    const c = { ...ctl(seg) };
    const keys = { ...(c.keys || {}) };
    if (list && list.length) keys[kp] = list; else delete keys[kp];
    if (Object.keys(keys).length) c.keys = keys; else delete c.keys;
    if (Object.keys(c).length) seg.controls = c; else delete seg.controls;
  }
  function setStatic(seg, kp, v) {
    if (kp === 'position_x' || kp === 'position_y') {
      const p = [...(ctl(seg).position || [0.5, 0.5])];
      p[kp === 'position_x' ? 0 : 1] = kround(kp, v);
      setCtl(seg, 'position', p, [0.5, 0.5]);
    } else setCtl(seg, kp, kround(kp, v), KDEF[kp]);
  }
  /* the film's frame grid: keys sit on frames, as every edit point does */
  function rate() {
    const t = TL();
    const r = t && typeof t.gridRate === 'function' ? Number(t.gridRate())
      : t && typeof t.fps === 'function' ? Number(t.fps()) : 0;
    return r > 0 ? r : 24;
  }
  const onFrame = (t) => Math.round(Math.round(t * rate()) / rate() * 1e6) / 1e6;
  const sameT = (a, b) => Math.abs(a - b) < 0.5 / rate();
  function span(seg) {
    const t = TL();
    if (!t || !seg) return null;
    if (isOv(seg)) return { start: Number(seg.at) || 0, dur: t.dur(seg) };
    const s = t.filmStart(seg.id);
    return s < 0 ? null : { start: s, dur: t.dur(seg) };
  }
  /* The playhead inside the shot (film seconds from its start), or null outside it. */
  function localT(seg) {
    const sp = span(seg);
    if (!sp) return null;
    const t = (TL().state.playhead || 0) - sp.start;
    const eps = 0.5 / rate();
    return t < -eps || t > sp.dur - eps ? null : clamp(t, 0, sp.dur);
  }
  const lastT = (sp) => Math.max(0, onFrame(sp.dur - 1 / rate()));
  /* Where a key made now goes: the playhead's frame, or the shot's first frame when the
   * playhead is elsewhere (where an edit parks the monitor, `preview`). */
  function keyT(seg) {
    const sp = span(seg), t = localT(seg);
    return sp && t != null ? clamp(onFrame(t), 0, lastT(sp)) : 0;
  }
  const shownT = (seg) => { const t = localT(seg); return t == null ? 0 : t; };
  function putKey(seg, kp, t, v) {
    const list = (keysOf(seg, kp) || []).map((k) => [k[0], k[1]]);
    const i = list.findIndex((k) => sameT(k[0], t));
    if (i >= 0) list[i][1] = kround(kp, v);
    else {
      if (list.length >= MAX_KEYS) return false;
      list.push([t, kround(kp, v)]);
      list.sort((a, b) => a[0] - b[0]);
    }
    setKeys(seg, kp, list);
    return true;
  }
  /* A value set on an animated property: a key at the playhead. Position's other axis
   * gets one at the same time, holding its value there, so x and y keep one set of times. */
  function keyValue(seg, kp, v) {
    const t = keyT(seg);
    const grp = kp.startsWith('position') ? GROUPS.pos : [kp];
    const other = grp.filter((k) => k !== kp && keysOf(seg, k)).map((k) => [k, valAt(seg, k, t)]);
    putKey(seg, kp, t, v);
    other.forEach(([k, ov]) => { if (!keysOf(seg, k).some((x) => sameT(x[0], t))) putKey(seg, k, t, ov); });
  }
  /* the controls as the monitor shows them at the playhead: keys evaluated */
  function ctlNow(seg) {
    const c = ctl(seg);
    if (!c.keys) return c;
    const sp = span(seg);
    const t = sp ? clamp((TL().state.playhead || 0) - sp.start, 0, sp.dur) : 0;
    const out = { ...c };
    if (keysOf(seg, 'position_x') || keysOf(seg, 'position_y')) {
      out.position = [valAt(seg, 'position_x', t), valAt(seg, 'position_y', t)];
    }
    ['scale', 'rotation', 'opacity', 'volume_db'].forEach((kp) => {
      const k = keysOf(seg, kp);
      if (k) out[kp] = interp(k, t);
    });
    return out;
  }
  /* A property the stopwatch animates: get shows the value at the playhead, set keys it. */
  function keyed(p, kp, group) {
    const get0 = p.get, set0 = p.set;
    const toUi = kp === 'position_x' ? FRAME_W : kp === 'position_y' ? FRAME_H : 1;
    p.kp = kp;
    p.group = group;
    p.get = (seg) => (keysOf(seg, kp) ? valAt(seg, kp, shownT(seg)) * toUi : get0(seg));
    p.set = (seg, v) => {
      if (!keysOf(seg, kp)) { set0(seg, v); return; }
      keyValue(seg, kp, clamp(v, p.lo, p.hi) / toUi);
    };
    return p;
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
      key: `pos${axis}`, sec: 'motion', label: 'Position', lo: -10 * size, hi: 10 * size, step: 1, digits: 1,
      unit: '', dflt: size / 2, hover: `${axis.toUpperCase()} in a ${FRAME_W}×${FRAME_H} frame · ${RENDERED}`,
      get: (seg) => ((ctl(seg).position || [0.5, 0.5])[k]) * size,
      set: (seg, v) => {
        const p = [...(ctl(seg).position || [0.5, 0.5])];
        p[k] = Math.round(clamp(v, -10 * size, 10 * size) / size * 1e6) / 1e6;
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
    keyed(posProp('x'), 'position_x', 'pos'), keyed(posProp('y'), 'position_y', 'pos'),
    keyed(plain('scale', 100, 0, 1000, 0.1, 1, '', 'Scale', 'motion'), 'scale', 'scale'),
    keyed(plain('rotation', 0, -3600, 3600, 0.1, 1, '°', 'Rotation', 'motion'), 'rotation', 'rotation'),
    keyed(plain('opacity', 100, 0, 100, 0.1, 1, ' %', 'Opacity', 'opacity'), 'opacity', 'opacity'),
    keyed(plain('volume_db', 0, -60, 6, 0.1, 2, ' dB', 'Level', 'volume'), 'volume_db', 'volume_db'),
    balProp('temperature', 'Temperature', -100, 100, 0.5, 1, '',
      'the shot\'s white balance, warm ↔ cool — saved as its hand balance; the export renders it'),
    balProp('tint', 'Tint', -100, 100, 0.5, 1, '',
      'green ↔ magenta — saved as its hand balance; the export renders it'),
    balProp('exposure', 'Exposure', -0.415, 0.585, 0.01, 2, '',
      'stops, within what the auto may do (×0.75 to ×1.5) — the export renders it'),
    plain('contrast', 0, -100, 100, 0.5, 1, '', 'Contrast', 'lumetri'),
    plain('highlights', 0, -100, 100, 0.5, 1, '', 'Highlights', 'lumetri'),
    plain('shadows', 0, -100, 100, 0.5, 1, '', 'Shadows', 'lumetri'),
    plain('whites', 0, -100, 100, 0.5, 1, '', 'Whites', 'lumetri'),
    plain('blacks', 0, -100, 100, 0.5, 1, '', 'Blacks', 'lumetri'),
    plain('saturation', 100, 0, 200, 0.5, 1, '', 'Saturation', 'lumetri'),
  ];
  // Lumetri Color › Basic Correction, in Premiere's order
  const BASIC = [['White Balance', ['temperature', 'tint']],
                 ['Tone', ['exposure', 'contrast', 'highlights', 'shadows', 'whites', 'blacks']],
                 [null, ['saturation']]];
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
    // an overlay's object is replaced by each V2 edit (and an undo): find it by id
    if (seg && t) return t.byId(seg.id) || (typeof t.overlayById === 'function' && t.overlayById(seg.id)) || seg;
    const insp = g('inspected');
    return (typeof insp === 'function' ? insp() : null) || ovSel();
  }
  /* After a commit: a V2+ clip's controls ride the sequence's save (tl.saveSeq — the
   * cut's autosave carries only V1), so they reach the disk with the edit. */
  function saved(seg) {
    const t = TL();
    if (t && isOv(seg || cur()) && typeof t.saveSeq === 'function') t.saveSeq();
  }
  /* Every edit here opens through this: a locked track refuses it, as the timeline's
   * own edits do (a V2+ clip's track; a V1 shot's V1, or A1 for its volume). */
  function refused(audio, target) {
    const t = TL();
    if (!t) return true;
    const seg = target || cur();
    const name = seg && isOv(seg) ? seg.track || 'V2' : audio ? 'A1' : 'V1';   // V2 is implied
    return !!(name && typeof t.refuseIfLocked === 'function' && t.refuseIfLocked(name));
  }
  function opens(label, audio, target) {
    const t = TL();
    if (!t || refused(audio, target)) return false;
    return !!t.begin(label);
  }
  function begin(p, seg) {
    return opens(labelOf(p), p.key === 'volume_db', seg);
  }
  function finish(p) {
    const t = TL();
    if (p.colour) { const td = g('tidyColour'); if (typeof td === 'function') td(); }
    if (t) t.commit();                      // touch → autosave; 'change' → the inspector repaints
    saved();
    const ri = g('renderInspector');
    if (typeof ri === 'function') ri();
  }
  function change(p, seg, v) {
    seg = cur(seg);
    if (!seg || !Number.isFinite(v)) return false;
    if (!begin(p, seg)) return false;
    p.set(seg, v);
    preview(seg);
    finish(p);
    return true;
  }

  /* ------------------------------------------------------------ the live preview */
  // the Program's buffers (app.js player.vids, `video.pvbuf`) and the canvases over them
  const LAYERS = ['#player video.pvbuf', '#gradeCanvas', '#fxCanvas'];
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
    const c = ctlNow(seg);                  // keyframes evaluated at the playhead
    const pos = c.position || [0.5, 0.5];
    const moved = pos[0] !== 0.5 || pos[1] !== 0.5 || (c.scale ?? 100) !== 100 || (c.rotation ?? 0) !== 0;
    const transform = moved
      ? `translate(${r4((pos[0] - 0.5) * 100)}%, ${r4((pos[1] - 0.5) * 100)}%) rotate(${c.rotation ?? 0}deg) scale(${r4((c.scale ?? 100) / 100)})`
      : '';
    const opacity = (c.opacity ?? 100) !== 100 ? String(r4(c.opacity / 100)) : '';
    const key = `${transform}|${opacity}`;
    if (key !== lastStyle) {
      lastStyle = key;
      LAYERS.forEach((s) => document.querySelectorAll(s).forEach((el) => {
        el.style.transform = transform;
        el.style.opacity = opacity;
      }));
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
    if (seg && t && isOv(seg)) {
      // a V2+ clip: park inside it when the playhead is elsewhere (its first frame, as
      // a V1 shot's edit parks there), then the Program's #pvV2 takes the new look
      if (localT(seg) == null && !(g('player') || {}).playing) t.seek(span(seg).start);
      const ps = g('programSync');
      if (typeof ps === 'function') ps();
      return;
    }
    if (seg && t && monitorShot() !== String(seg.id)) {
      const i = t.indexOf(seg.id);
      const cue = g('cueAt');
      if (i >= 0 && typeof cue === 'function' && !(g('player') || {}).playing) cue(i, seg.in);
    }
    apply();
    drawSoon();
  }
  /* One grade draw on the next frame, however many changes and repaints ask for it
   * before then (verdict 4 punch 5). A drawn frame is a frame upload and a shader pass:
   * one `effects.set` used to draw three times in its own task — preview, the commit's
   * repaint, finish's repaint — and a click on a shot twice, ~80 ms each on a loaded box. */
  let drawQ = 0;
  function drawSoon() {
    if (drawQ) return;
    drawQ = requestAnimationFrame(() => {
      drawQ = 0;
      const gr = typeof g('gradeApi') === 'function' ? g('gradeApi')() : null;
      if (gr) gr.draw();
    });
  }
  function adjust(id) {
    const t = TL();
    const seg = t ? t.byId(id) : null;
    const c = ctl(seg);
    const out = { pre: [1, 1, 1], con: 1 + (c.contrast ?? 0) / 200, sat: (c.saturation ?? 100) / 100,
                  // Lumetri's tone (grade.js's shader has the formula): blacks, shadows, highlights, whites
                  tone: ['blacks', 'shadows', 'highlights', 'whites'].map((k) => (c[k] ?? 0) / 100) };
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
  const WATCH_SVG = '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="9.2" r="5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M6.3 1.9h3.4M8 2v2.2M8 9.2V6.6M12.2 4.4l1-1" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';
  const valInput = (k, title) => `<input class="ec-v" data-k="${k}" readonly spellcheck="false" autocomplete="off" role="spinbutton"${title ? ` title="${title}"` : ''}>`;
  const resetBtn = (keys, label) => `<button type="button" class="ec-reset" data-reset="${keys}" title="reset ${esc(label)}">${RESET_SVG}</button>`;
  function row(p) {
    return `<div class="ec-row" data-row="${p.key}" title="${esc(p.hover)}">
      <span class="ec-name">${esc(p.label)}</span>
      <span class="ec-vals">${valInput(p.key)}</span>
      ${resetBtn(p.key, p.label.toLowerCase())}
    </div>`;
  }
  /* An animatable row, Premiere's: the stopwatch, the name, the value(s), ◀ ◆ ▶ (shown
   * once it is animated), the reset, and its lane of the mini timeline — the shot's span,
   * the playhead, a diamond per key. */
  function animRow(group, label, vals, resetKeysList, hover) {
    const name = GROUP_NAME[group];
    return `<div class="ec-row ec-anim" data-row="${group === 'pos' ? 'pos' : group}" data-g="${group}" title="${esc(hover)}">
      <button type="button" class="ec-sw" data-sw="${group}" aria-pressed="false" title="toggle animation: keyframes for ${name}">${WATCH_SVG}</button>
      <span class="ec-name">${esc(label)}</span>
      <span class="ec-vals">${vals}</span>
      <span class="ec-nav"><button type="button" data-kact="prev" data-g="${group}" title="go to the previous keyframe">◂</button><button type="button" class="ec-kadd" data-kact="key" data-g="${group}" title="add or remove a keyframe at the playhead">◆</button><button type="button" data-kact="next" data-g="${group}" title="go to the next keyframe">▸</button></span>
      ${resetBtn(resetKeysList, name)}
      <div class="ec-lane" data-lane="${group}" title="${esc(name)} keyframes over this shot · click to move the playhead, drag a diamond to move its key"><i class="ec-ph"></i></div>
    </div>`;
  }
  /* Lumetri's rows: the name, a slider, the number. */
  function slRow(p) {
    return `<div class="ec-row ec-lum" data-row="${p.key}" title="${esc(p.hover)}">
      <span class="ec-name">${esc(p.label)}</span>
      <input type="range" class="ec-sl" data-sl="${p.key}" min="${p.lo}" max="${p.hi}" step="${p.key === 'exposure' ? 0.005 : 0.5}" aria-label="${esc(p.label)}">
      <span class="ec-vals">${valInput(p.key)}</span>
      ${resetBtn(p.key, p.label.toLowerCase())}
    </div>`;
  }
  function sectionHtml(s, gen) {
    if (s.colour && gen) return '';
    let body = '';
    const BK = (k) => BY_KEY.get(k);
    if (s.key === 'motion') {
      body = animRow('pos', 'Position', valInput('posx', 'X') + valInput('posy', 'Y'), 'posx,posy', BK('posx').hover)
        + animRow('scale', 'Scale', valInput('scale'), 'scale', BK('scale').hover)
        + animRow('rotation', 'Rotation', valInput('rotation'), 'rotation', BK('rotation').hover);
    } else if (s.key === 'opacity') body = animRow('opacity', 'Opacity', valInput('opacity'), 'opacity', BK('opacity').hover);
    else if (s.key === 'volume') body = animRow('volume_db', 'Level', valInput('volume_db'), 'volume_db', BK('volume_db').hover);
    else if (s.key === 'time') {
      body = `<div class="ec-row" data-row="speed" title="the shot's rate — the export renders it">
          <span class="ec-name">Speed</span><span class="ec-vals ec-slot" data-slot="speed"></span>
          <button type="button" class="ec-reset" data-reset="speed" title="reset speed to 100%">${RESET_SVG}</button></div>`;
    } else if (s.key === 'lumetri') {
      // Lumetri Color › Basic Correction, as Premiere lays it out
      const basic = BASIC.map(([head, keys]) => (head ? `<div class="ec-grp">${esc(head)}</div>` : '')
        + keys.map((k) => slRow(BK(k))).join('')).join('');
      body = `<div class="ec-sub${open.basic ? ' open' : ''}" data-sub="basic">
          <div class="ec-ssh"><button type="button" class="ec-twirl ec-subtw" data-twirl="basic" aria-expanded="${!!open.basic}"><i class="ec-tri"></i><span>Basic Correction</span></button></div>
          <div class="ec-ssb">${basic}<div class="ec-slot ec-quick" data-slot="nudge"></div></div>
        </div>`
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
    // a V2+ clip (`opts.overlay`): Motion and Opacity, and a line on what stays V1's
    wrap.innerHTML = opts.overlay
      ? SECTIONS.filter((s) => s.key === 'motion' || s.key === 'opacity').map((s) => sectionHtml(s, true)).join('')
        + '<div class="ec-empty ec-ovnote" data-testid="ec-ovnote" title="the export conforms a V2+ clip as shot and composites it over the tracks below by its Opacity">'
        + 'Lumetri Color, Volume and Time Remapping apply to V1 shots: a clip on V2+ renders as shot, picture only.</div>'
      : SECTIONS.map((s) => sectionHtml(s, gen)).join('');
    if (opts.overlay) wrap.dataset.ov = String(seg && seg.id);
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
    follow();
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
        const sec = tw.closest('.ec-sub, .ec-sec');
        sec.classList.toggle('open', open[k]);
        tw.setAttribute('aria-expanded', String(open[k]));
        return;
      }
      const sw = e.target.closest('[data-sw]');
      if (sw) { e.stopPropagation(); toggleAnim(sw.dataset.sw); return; }
      const ka = e.target.closest('[data-kact]');
      if (ka) { e.stopPropagation(); keyAct(ka.dataset.kact, ka.dataset.g); return; }
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
    wrap.querySelectorAll('.ec-sl').forEach((el) => bindSlider(el));
    wrap.querySelectorAll('.ec-lane').forEach((el) => {
      el.addEventListener('pointerdown', (e) => onLaneDown(e, el));
    });
  }

  /* ------------------------------------------------------------ Lumetri's sliders
   * A drag is one undo entry (begin on its first input, commit on its change); each
   * arrow-key step one. The number beside it is the same value, typed or scrubbed. */
  let slDrag = null;                          // the slider being dragged
  function bindSlider(el) {
    const p = BY_KEY.get(el.dataset.sl);
    if (!p) return;
    el.addEventListener('input', () => {
      const seg = cur();
      const v = parseFloat(el.value);
      if (!seg || !Number.isFinite(v)) return;
      if (slDrag !== el) {
        if (!begin(p)) return;
        slDrag = el;
      }
      p.set(seg, clamp(v, p.lo, p.hi));
      const num = el.parentNode.querySelector('.ec-v');
      if (num) num.value = fmtVal(p, p.get(seg));
      preview(seg);
    });
    el.addEventListener('change', () => {
      if (slDrag !== el) return;
      slDrag = null;
      finish(p);
    });
    el.addEventListener('keydown', (e) => {
      e.stopPropagation();                    // its arrows are its own, not the timeline's
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        const t = TL();
        if (t) { if (e.shiftKey) t.redo(); else t.undo(); }
      }
    });
  }

  /* ------------------------------------------------------------ keyframes: the controls */
  function finishK() {
    const t = TL();
    if (t) t.commit();
    saved();
    const ri = g('renderInspector');
    if (typeof ri === 'function') ri();
  }
  /* Premiere's warning before a stopwatch turns off over keys (verdict 7, punch 7): "This
   * action will delete existing keyframes. Do you want to continue?" A modal of the
   * board's own (the Export dialog's look), never window.confirm: Cancel (or Esc) keeps
   * the keys, OK takes them off as one undo entry. Resolves true on OK. */
  let askDlg = null;
  function askDeleteKeys(what) {
    if (!askDlg) {
      askDlg = document.createElement('dialog');
      askDlg.id = 'ecConfirm';
      askDlg.className = 'ec-confirm';
      askDlg.dataset.testid = 'keys-confirm';
      askDlg.setAttribute('aria-labelledby', 'ecConfirmTitle');
      askDlg.innerHTML = `
        <form method="dialog" class="ecq">
          <div class="ecq-head" id="ecConfirmTitle">Delete keyframes</div>
          <p class="ecq-body"><span class="ecq-msg">This action will delete existing keyframes. Do you want to continue?</span>
            <span class="ecq-what"></span></p>
          <div class="ecq-foot">
            <button type="button" class="ecq-btn" data-ans="cancel">Cancel</button>
            <button type="button" class="ecq-btn primary" data-ans="ok">OK</button>
          </div>
        </form>`;
      // keys typed in it stay in it (app.js plays and steps the cut from document)
      askDlg.addEventListener('keydown', (e) => { if (e.key !== 'Escape') e.stopPropagation(); });
      document.body.appendChild(askDlg);
    }
    const d = askDlg;
    d.querySelector('.ecq-what').textContent = what || '';
    return new Promise((resolve) => {
      const done = (ok) => {
        d.removeEventListener('click', onClick);
        d.removeEventListener('cancel', onCancel);
        if (d.open) d.close();
        resolve(ok);
      };
      const onClick = (e) => {
        const b = e.target.closest('[data-ans]');
        if (b) { e.preventDefault(); done(b.dataset.ans === 'ok'); }
      };
      const onCancel = (e) => { e.preventDefault(); done(false); };
      d.addEventListener('click', onClick);
      d.addEventListener('cancel', onCancel);
      d.showModal();
      d.querySelector('[data-ans=ok]').focus();          // Premiere's default button
    });
  }
  function toggleAnim(group, sure) {
    const seg = cur();
    const t = TL();
    if (!seg || !t || !GROUPS[group]) return;
    const kps = GROUPS[group];
    const on = animated(seg, group);
    if (on && !sure) {
      // a locked track refuses at once: it never asks a question it will then refuse
      if (refused(group === 'volume_db')) return;
      if (askDlg && askDlg.open) return;
      const n = keyTimes(seg, group).length;
      const nm = GROUP_NAME[group];
      askDeleteKeys(`${nm[0].toUpperCase()}${nm.slice(1)}: ${n} keyframe${n === 1 ? '' : 's'}.`).then((ok) => {
        const now = ok ? cur() : null;            // still the same shot, still keyed
        if (now && now.id === seg.id && animated(now, group)) toggleAnim(group, true);
      });
      return;
    }
    if (!opens(on ? `stop animating ${GROUP_NAME[group]}` : `animate ${GROUP_NAME[group]}`, group === 'volume_db')) return;
    if (on) {                                 // Premiere: the keys go, the value at the playhead stays
      const tv = shownT(seg);
      const vals = kps.map((kp) => valAt(seg, kp, tv));
      kps.forEach((kp, i) => { setKeys(seg, kp, null); setStatic(seg, kp, vals[i]); });
    } else {
      const at = keyT(seg);
      kps.forEach((kp) => setKeys(seg, kp, [[at, kround(kp, staticOf(seg, kp))]]));
    }
    preview(seg);
    finishK();
  }
  function keyTimes(seg, group) {
    const ts = [];
    GROUPS[group].forEach((kp) => (keysOf(seg, kp) || []).forEach((k) => {
      if (!ts.some((x) => sameT(x, k[0]))) ts.push(k[0]);
    }));
    return ts.sort((a, b) => a - b);
  }
  /* Park the playhead (and the monitor) at a time inside the shot. */
  function seekIn(seg, t) {
    const sp = span(seg), tl = TL();
    if (!sp || !tl) return;
    tl.seek(sp.start + clamp(t, 0, lastT(sp)));
  }
  function keyAct(act, group) {
    const seg = cur();
    const t = TL();
    if (!seg || !t || !GROUPS[group] || !animated(seg, group)) return;
    const ts = keyTimes(seg, group);
    const now = localT(seg);
    const eps = 0.5 / rate();
    if (act === 'prev' || act === 'next') {
      const ref = now == null ? (act === 'prev' ? Infinity : -Infinity) : now;
      const to = act === 'prev' ? ts.filter((x) => x < ref - eps).pop() : ts.find((x) => x > ref + eps);
      if (to != null) seekIn(seg, to);
      return;
    }
    const at = keyT(seg);
    const has = ts.some((x) => sameT(x, at));
    if (!opens(has ? 'remove keyframe' : 'add keyframe', group === 'volume_db')) return;
    GROUPS[group].forEach((kp) => {
      const k = keysOf(seg, kp);
      if (!k) return;
      if (has) {
        const gone = k.find((x) => sameT(x[0], at));
        const left = k.filter((x) => !sameT(x[0], at)).map((x) => [x[0], x[1]]);
        // the last key gone: not animated any more, holding the value it had
        if (!left.length && gone) { setKeys(seg, kp, null); setStatic(seg, kp, gone[1]); }
        else setKeys(seg, kp, left);
      } else {
        putKey(seg, kp, at, valAt(seg, kp, at));
      }
    });
    preview(seg);
    finishK();
  }

  /* The lane: a press moves the playhead there (a drag scrubs); a press on a diamond
   * drags its key along the shot, on frames, one undo entry; a click on one without a
   * drag parks the playhead on it. */
  let kfDrag = null;
  function onLaneDown(e, lane) {
    if (e.button !== 0) return;
    const seg = cur();
    const sp = span(seg);
    if (!seg || !sp || !sp.dur) return;
    e.preventDefault();
    e.stopPropagation();
    const group = lane.dataset.lane;
    const r = lane.getBoundingClientRect();     // one layout read per press
    const tOf = (x) => clamp(onFrame((x - r.left) / (r.width || 1) * sp.dur), 0, lastT(sp));
    const kf = e.target.closest('.ec-kf');
    if (lane.setPointerCapture) { try { lane.setPointerCapture(e.pointerId); } catch (err) { /* fine */ } }
    let move, up;
    if (kf) {
      const t0 = Number(kf.dataset.t);
      kfDrag = { group, t0, at: t0, began: false, x0: e.clientX };
      move = (ev) => {
        if (!kfDrag) return;
        if (!kfDrag.began) {
          if (Math.abs(ev.clientX - kfDrag.x0) < 3) return;
          if (!opens('move keyframe', group === 'volume_db')) { kfDrag = null; return; }
          kfDrag.began = true;
          kf.classList.add('drag');
        }
        const s2 = cur(seg);
        const to = tOf(ev.clientX);
        if (sameT(to, kfDrag.at)) return;
        if (keyTimes(s2, group).some((x) => sameT(x, to))) return;   // never onto another key
        GROUPS[group].forEach((kp) => {
          const k = keysOf(s2, kp);
          if (!k) return;
          setKeys(s2, kp, k.map((x) => (sameT(x[0], kfDrag.at) ? [to, x[1]] : [x[0], x[1]]))
            .sort((a, b) => a[0] - b[0]));
        });
        kfDrag.at = to;
        kf.dataset.t = String(to);
        kf.style.left = `${(to / sp.dur) * 100}%`;
        apply();
      };
      up = () => {
        const d = kfDrag;
        kfDrag = null;
        kf.classList.remove('drag');
        if (d && d.began) { preview(cur(seg)); finishK(); } else if (d) seekIn(seg, d.t0);
      };
    } else {
      seekIn(seg, tOf(e.clientX));
      let last = e.clientX;
      move = (ev) => { if (Math.abs(ev.clientX - last) >= 2) { last = ev.clientX; seekIn(seg, tOf(ev.clientX)); } };
      up = () => {};
    }
    const done = () => {
      lane.removeEventListener('pointermove', move);
      lane.removeEventListener('pointerup', done);
      lane.removeEventListener('pointercancel', done);
      up();
    };
    lane.addEventListener('pointermove', move);
    lane.addEventListener('pointerup', done);
    lane.addEventListener('pointercancel', done);
  }

  /* Paint the stopwatches, ◀ ◆ ▶, the diamonds and the lanes' playhead. The diamonds are
   * rebuilt only when the keys change, and never under a drag. */
  function paintKeys(box, seg) {
    const sp = span(seg);
    const now = localT(seg);
    box.querySelectorAll('.ec-anim').forEach((rowEl) => {
      const group = rowEl.dataset.g;
      const on = animated(seg, group);
      rowEl.classList.toggle('animated', on);
      const sw = rowEl.querySelector('.ec-sw');
      if (sw && sw.getAttribute('aria-pressed') !== String(on)) sw.setAttribute('aria-pressed', String(on));
      const ts = on ? keyTimes(seg, group) : [];
      const add = rowEl.querySelector('.ec-kadd');
      if (add) add.classList.toggle('at', on && now != null && ts.some((x) => sameT(x, now)));
      const lane = rowEl.querySelector('.ec-lane');
      if (!lane || !sp) return;
      if (!(kfDrag && kfDrag.group === group)) {
        const key = JSON.stringify([ts, sp.dur]);
        if (lane.dataset.key !== key) {
          lane.dataset.key = key;
          lane.querySelectorAll('.ec-kf').forEach((n) => n.remove());
          ts.forEach((t) => {
            const d = document.createElement('i');
            d.className = 'ec-kf';
            d.dataset.t = String(t);
            d.title = `keyframe at ${t.toFixed(2)} s`;
            d.style.left = `${sp.dur ? (t / sp.dur) * 100 : 0}%`;
            lane.appendChild(d);
          });
        }
      }
    });
    paintHead(box, seg, sp, now);
  }
  /* the lanes' playhead: one style write per lane, only when it moved */
  function paintHead(box, seg, sp, now) {
    const left = sp && now != null && sp.dur ? `${(now / sp.dur) * 100}%` : '';
    box.querySelectorAll('.ec-ph').forEach((ph) => {
      if (ph.style.left !== left) ph.style.left = left;
      ph.hidden = !left;
    });
  }
  /* The playhead moved (a tick of playback, a scrub, a cue): the monitor shows the keyed
   * values at it, and the panel's animated numbers, ◆ and lanes follow. */
  let following = false;
  function follow() {
    const t = TL();
    if (following || !t || typeof t.on !== 'function') return;
    following = true;
    t.on('playhead', onPlayhead);
  }
  function onPlayhead() {
    const t = TL();
    if (!t) return;
    const live = liveId ? t.byId(liveId) : null;
    if (live && ctl(live).keys) apply();
    const box = document.querySelector('#inspector .shot');
    const seg = box ? cur() : null;
    if (!seg || !box.querySelector('.ec')) return;
    if (ctl(seg).keys) {
      paintKeys(box, seg);
      box.querySelectorAll('.ec-anim.animated .ec-v').forEach((el) => {
        const p = BY_KEY.get(el.dataset.k);
        if (!p || !el.readOnly || (gesture && gesture.p === p)) return;
        const txt = fmtVal(p, p.get(seg));
        if (el.value !== txt) el.value = txt;
      });
    } else {
      const sp = span(seg);
      paintHead(box, seg, sp, localT(seg));
    }
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
    if (!ps.length || !t || !opens(key === 'lumetri' ? 'colour reset' : key, key === 'volume')) return;
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
    if (!opens(act === 'bypass' ? 'fx on/off' : 'fx order')) return;
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
      if (r) r.classList.toggle('changed', (p.group && animated(seg, p.group)) || (p.key.startsWith('pos')
        ? BY_KEY.get('posx').get(seg) !== FRAME_W / 2 || BY_KEY.get('posy').get(seg) !== FRAME_H / 2
        : Math.abs(v - p.dflt) > 1e-6));
    });
    box.querySelectorAll('.ec-sl').forEach((el) => {
      const p = BY_KEY.get(el.dataset.sl);
      if (!p || slDrag === el) return;
      const v = String(p.get(seg));
      if (el.value !== v) el.value = v;
    });
    paintKeys(box, seg);
    const spd = box.querySelector('[data-row=speed]');
    if (spd) spd.classList.toggle('changed', Number(seg.speed || 1) !== 1);
    paintStack(box, seg);
    apply();
    drawSoon();
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
    // keyframes (tests and the judge): a prop's keys, and the controls the monitor shows now
    keys: (kp, seg) => { const s = cur(seg); const k = s ? keysOf(s, kp) : null; return k ? k.map((x) => [x[0], x[1]]) : []; },
    now: (seg) => { const s = cur(seg); return s ? ctlNow(s) : null; },
    animate: (group) => toggleAnim(group),
    key: (act, group) => keyAct(act, group),
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
