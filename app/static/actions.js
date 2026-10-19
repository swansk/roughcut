/* Actions (redesign wave 12, lane AIOPS; REDESIGN-GOAL dimension 15, Karl's K3 d).
 *
 * Karl, 2026-10-16: "While I CAN use the pro tools here (good), AI should also be capable
 * of all of it." One vocabulary for every edit (roughcut/actions.py documents it): an op
 * list is applied through the SAME window.tl / window.effects / colour / Export calls a
 * human edit makes, inside one tl.begin … tl.commit (one undo entry), and an AI's answer
 * (GET /api/assist/{job}) is reviewed here, op by op, before anything lands.
 *
 *   window.rcActions = {
 *     apply(ops, {label})   run the ops through the human paths as ONE undo entry;
 *                           returns {applied, refused: [{i, why}], after} — `after` is the
 *                           promise of the ops that run outside the cut once it has landed
 *                           (a Designed Effects request, an export), in that order
 *     review(result)        an /api/assist result (the GET body, plus `job`, `asked` and
 *                           `scope` from the AI prompt): `ops` opens the review panel
 *                           (Assistant tab), `plan` follows the Ask job into the proposal
 *                           panel, `answer` shows the words, a failed or stopped job says so
 *     describe(op)          one line for an op against the cut as it is now
 *     accept() / reject() / close()   what the panel's buttons do
 *     get open()            whether a review is up
 *   }
 *
 * How each op lands (the human path it shares):
 *   insert / overwrite / append → tl.insertAt / tl.overwrite / tl.insert; move → tl.move;
 *   razor → tl.split; ripple_delete → tl.remove; trim → tl.setRange; roll and slip → the
 *   trim lane's tl.setRange calls (timeline-trim.js `step`), on frames; speed →
 *   tl.setSpeed; marker → tl.addMarker with its name / comment / colour / duration (on a
 *   marker already there, tl.editMarker: the Marker dialog's OK); in_out → tl.setSeqIn / setSeqOut; track_add →
 *   tl.addTrack; overlay → tl.addOverlay; set → effects.set; keys → the stopwatch
 *   (effects.animate) and a value typed with the playhead on each key (effects.set);
 *   curve / wheel → effects.curve / effects.wheel; vignette → each Lumetri › Vignette
 *   value typed in its row (effects.set vignette_*); look / match → app.js colourEdit, as
 *   the colour pane's selects and buttons; transition / fade → tl.addTransition /
 *   tl.addFade; design_effect → the Designed Effects tool on that shot, its Design
 *   pressed (fx.js `request`; its own proposal and review follow); export → the Export
 *   dialog, filled in and its Export pressed — only after Accept.
 *
 * One undo entry: `tl.batch(label, fn)` wraps them all (timeline.js); the tl.begin /
 * commit / cancel the human paths make while they run nest inside it and fold into its
 * one entry. Markers and In / Out are on the timeline's undo stack (timeline.js's
 * snapshot), so they ride the same entry: one accept is one undo step. Design requests
 * ride it too — or, when nothing else changed the cut, an entry of their own (tl.mark):
 * its Ctrl+Z takes the requests back (Designed Effects discards them) and never reaches
 * past them to an older edit (verdict 12, punch 5). They are never called "applied":
 * the line says they are being designed, and where to review them.
 *
 * The review shows every op before it lands: the cut's ops as the ghost lane under V1
 * (tlLanes.showGhost, the server's simulation of the accepted ops on the board's cut,
 * POST /api/assist/preview), the colour and Effect Controls ops as the After picture on
 * the Program monitor (applied in memory with nothing saved; ◐ flips Before / After; any
 * edit elsewhere takes the preview down first, so it never rides another edit to disk) —
 * a look or a Color Match too, through the LUT baked from the colour block held in
 * memory (grade.preview; verdict 10, punch 7: they used to show only once saved),
 * a transition or a fade as its dashed box on the edit it will sit on (tlLanes.
 * showTransitions: on the ghost lane while that is up, else on V1 / A1; verdict 9,
 * punch 6), and an export as the file it will write. ✓ / ✗ per op; Accept applies the accepted
 * ones as one undo entry and POSTs the review to /api/assist/{job}/answer; Reject all
 * puts everything back as it was and writes nothing.
 */
(function () {
  'use strict';

  const TL = () => window.tl || null;
  const FX = () => window.effects || null;
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const MIN_LEN = 0.2;
  /* The model's words, and any line that falls back to an id, say a shot as the editor
   * does — "shot 22 (CLIP_05)", never "g5848fde311" (verdict 12, punch 6): app.js
   * `clipNamed`, reached by name. */
  const named = (text) => {
    try { if (typeof clipNamed === 'function') return clipNamed(text); } catch (e) { /* app.js not yet run */ }
    return String(text == null ? '' : text);
  };

  /* app.js's top-level bindings share the global lexical scope; reached by name, guarded */
  /* eslint-disable no-undef */
  const app = {
    segs: () => (typeof segs !== 'undefined' && Array.isArray(segs) ? segs : []),
    clips: () => (typeof P !== 'undefined' && P && P.clips ? P.clips : {}),
    colour: () => (typeof colour !== 'undefined' && colour && typeof colour === 'object' ? colour : null),
    setColour: (c) => { try { colour = c; } catch (e) { /* no board */ } },
    shotOverride: (id, create) => (typeof shotOverride === 'function' ? shotOverride(id, create) : null),
    colourEdit: (label, fn) => {
      if (typeof colourEdit === 'function') colourEdit(label, fn);
      else { const t = TL(); if (t) t.begin(label); fn(); if (t) t.commit(); }
    },
    renderInspector: () => { if (typeof renderInspector === 'function') renderInspector(); },
    toast: (m, ms) => { if (typeof toast === 'function') toast(m, ms); },
    undoKey: () => (typeof undoKey === 'function' ? undoKey() : 'Ctrl+Z'),
    flush: () => (typeof window.roughcutFlush === 'function' ? window.roughcutFlush() : Promise.resolve()),
    playing: () => (typeof player !== 'undefined' && player && player.playing),
  };
  /* eslint-enable no-undef */

  // Lumetri › Vignette's four numbers (wave 15, VIGNETTE): each its own stopwatch, as Premiere's
  const VIG_PROPS = ['vignette_amount', 'vignette_midpoint', 'vignette_roundness', 'vignette_feather'];
  const KEY_GROUP = { posx: 'pos', posy: 'pos', scale: 'scale', rotation: 'rotation', opacity: 'opacity', volume_db: 'volume_db',
                      ...Object.fromEntries(VIG_PROPS.map((k) => [k, k])) };
  const KEY_PROP = { posx: 'position_x', posy: 'position_y', scale: 'scale', rotation: 'rotation', opacity: 'opacity', volume_db: 'volume_db',
                     ...Object.fromEntries(VIG_PROPS.map((k) => [k, k])) };
  const CUT_OPS = new Set(['insert', 'overwrite', 'append', 'move', 'razor', 'ripple_delete', 'trim', 'roll', 'slip', 'speed']);
  const LOOK_OPS = new Set(['set', 'keys', 'effect', 'curve', 'wheel', 'vignette', 'look', 'match']);   // shown on the Program
  const TX_OPS = new Set(['transition', 'fade']);                                // drawn on the timeline
  const FAMILY = {
    insert: 'timeline', overwrite: 'timeline', append: 'timeline', move: 'timeline', razor: 'timeline',
    ripple_delete: 'timeline', trim: 'timeline', roll: 'timeline', slip: 'timeline', speed: 'timeline',
    marker: 'timeline', in_out: 'timeline', track_add: 'timeline', overlay: 'timeline',
    set: 'effect_controls', keys: 'effect_controls', effect: 'effect_controls',
    curve: 'lumetri', wheel: 'lumetri', vignette: 'lumetri', look: 'lumetri', match: 'lumetri',
    transition: 'transitions', fade: 'transitions', design_effect: 'designed_effects', export: 'export',
  };
  const FAMILY_NAME = { timeline: 'Timeline', effect_controls: 'Effect Controls', lumetri: 'Lumetri Color',
                        transitions: 'Transitions', designed_effects: 'Designed Effects', export: 'Export' };
  // one glyph per family (14 px, currentColor), in the panels' own icon style
  const ICON = {
    timeline: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="1.5" y="4" width="6" height="3.5" rx=".6"/><rect x="8.5" y="4" width="6" height="3.5" rx=".6"/><rect x="1.5" y="9.5" width="13" height="3" rx=".6"/></svg>',
    effect_controls: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M2 4h7M12 4h2M2 8h2M7 8h7M2 12h9"/><circle cx="10.5" cy="4" r="1.5"/><circle cx="5.5" cy="8" r="1.5"/><circle cx="12.5" cy="12" r="1.5"/></svg>',
    lumetri: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><circle cx="8" cy="8" r="6"/><path d="M8 2v12M2 8h12" opacity=".45"/><circle cx="10" cy="6" r="1.4" fill="currentColor"/></svg>',
    transitions: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="1.5" y="4" width="7" height="8" rx=".8"/><rect x="7.5" y="4" width="7" height="8" rx=".8" opacity=".55"/><path d="M7.5 4l1 8"/></svg>',
    designed_effects: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><path d="M8 1.8l1.5 3.6 3.7 1.1-3.7 1.2L8 11.3 6.5 7.7 2.8 6.5l3.7-1.1z"/><path d="M12.5 11.5l.6 1.4 1.4.6-1.4.6-.6 1.4-.6-1.4-1.4-.6 1.4-.6z"/></svg>',
    export: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M8 2v8M5 7l3 3 3-3"/><path d="M2.5 11v2.5h11V11"/></svg>',
  };

  /* ------------------------------------------------------------ the cut's arithmetic */
  function clipDur(name) {
    const c = app.clips()[name];
    const d = c && Number(c.duration);
    return d > 0 ? d : 1e9;
  }
  function gridRate() {
    const t = TL();
    const r = t && typeof t.gridRate === 'function' ? Number(t.gridRate()) : 0;
    return r > 0 ? r : 24;
  }
  /* timeline-trim.js `toFrames`: whole frames of the film, inside [lo, hi] */
  function toFrames(v, lo, hi) {
    const r = gridRate();
    let n = Math.round(v * r);
    if (n / r > hi + 1e-9) n = Math.floor(hi * r + 1e-9);
    if (n / r < lo - 1e-9) n = Math.ceil(lo * r - 1e-9);
    return n / r;
  }
  const tc = (t) => { const tt = TL(); return tt && tt.tc ? tt.tc(t) : `${Number(t).toFixed(2)} s`; };
  const stem = (clip) => {
    const c = String(clip || '');
    if (c.startsWith('gen_')) return `a ${c.split('_')[1] || 'generated'} slide`;
    return c.replace(/\.[^.]+$/, '');
  };

  /* ------------------------------------------------------------ the ops, as a human makes them */
  function refuse(why) { const e = new Error(why); e.refused = true; throw e; }

  function makeCtx() {
    const map = new Map();
    return {
      map,
      later: [],
      designs: [],         // design_effect ops: {shot, note}, asked for once the cut has landed
      asked: null,         // fx.request's handle for them (the accept's undo entry takes them back)
      id: (x) => (x == null ? x : map.get(String(x)) || String(x)),
      shot(x, what = 'id') {
        const t = TL();
        if (x == null || x === '') refuse(`needs ${what}`);
        const s = t.byId(this.id(x));
        if (!s || t.indexOf(s.id) < 0) refuse(`shot ${x} is not in the cut`);
        return s;
      },
      target(x) {          // a V1 shot, or a clip on V2+ (Motion and Opacity)
        const t = TL();
        const id = this.id(x);
        const s = t.byId(id);
        if (s && t.indexOf(s.id) >= 0) return { seg: s, ov: false };
        const o = typeof t.overlayById === 'function' ? t.overlayById(id) : null;
        if (o) return { seg: o, ov: true };
        refuse(`shot ${x} is not in the cut`);
        return null;
      },
    };
  }

  const range = (op) => ({ clip: op.clip, in: Number(op.in), out: Number(op.out) });

  const DO = {
    insert(op, c) {
      const id = TL().insertAt(range(op), Number(op.at));
      if (!id) refuse('the timeline refused the insert (is V1 locked?)');
      if (op.makes) c.map.set(op.makes, id);
    },
    overwrite(op, c) {
      const id = TL().overwrite(range(op), Number(op.at));
      if (!id) refuse('the timeline refused the overwrite (is V1 locked?)');
      if (op.makes) c.map.set(op.makes, id);
    },
    append(op, c) {
      const after = op.after == null ? null : c.shot(op.after, 'after').id;
      const id = TL().insert(range(op), after);
      if (!id) refuse('the timeline refused the clip (is V1 locked?)');
      if (op.makes) c.map.set(op.makes, id);
    },
    move(op, c) {
      const ids = (op.ids || []).map((x) => c.shot(x, 'ids').id);
      const before = op.before == null ? null : c.shot(op.before, 'before').id;
      if (!TL().move(ids, before)) refuse('the shots are already there');
    },
    razor(op, c) {
      const t = TL();
      let s = op.id != null ? c.shot(op.id) : null;
      if (!s) { const at = t.shotAt(Number(op.at)); s = at ? t.byId(at.id) : null; }
      if (!s) refuse('there is no shot under that time');
      const id = t.split(s.id, Number(op.at));
      if (!id) refuse(`${tc(op.at)} is too close to an edge of that shot`);
      if (op.makes) c.map.set(op.makes, id);
    },
    ripple_delete(op, c) {
      const ids = (op.ids || []).map((x) => c.shot(x, 'ids').id);
      if (!TL().remove(ids)) refuse('nothing to delete');
    },
    trim(op, c) {
      const s = c.shot(op.id);
      if (!TL().setRange(s.id, op.in == null ? null : Number(op.in), op.out == null ? null : Number(op.out))) {
        refuse('the shot already has that range');
      }
    },
    roll(op, c) {
      const t = TL();
      const L = c.shot(op.a, 'a'), R = c.shot(op.b, 'b');
      if (t.indexOf(R.id) !== t.indexOf(L.id) + 1) refuse('the two shots are not neighbours any more');
      // timeline-trim.js rollParams + step: film seconds, each side at its rate, on frames
      const spdL = t.speedOf(L), spdR = t.speedOf(R);
      const outL0 = L.out, inR0 = R.in;
      const lo = Math.max((L.in + MIN_LEN - L.out) / spdL, -R.in / spdR);
      const hi = Math.min((clipDur(L.clip) - L.out) / spdL, (R.out - MIN_LEN - R.in) / spdR);
      const v = toFrames(clamp(Number(op.by), lo, hi), lo, hi);
      if (!v) refuse('there is no media to roll into');
      t.setRange(L.id, null, outL0 + v * spdL);
      t.setRange(R.id, inR0 + v * spdR, null);
    },
    slip(op, c) {
      const s = c.shot(op.id);
      // timeline-trim.js step (slip): in and out together, clip seconds, on frames
      const in0 = s.in, out0 = s.out, d = clipDur(s.clip);
      const v = toFrames(clamp(Number(op.by), -in0, d - out0), -in0, d - out0);
      if (!v) refuse('there is no media to slip into');
      TL().setRange(s.id, in0 + v, out0 + v);
    },
    speed(op, c) {
      const s = c.shot(op.id);
      if (!TL().setSpeed(s.id, Number(op.rate))) refuse('the shot already plays at that speed');
    },
    marker(op) {
      // M, then the Marker dialog's fields (verdict 12, punch 4); at a marker already
      // there, its dialog's OK (tl.editMarker): the same one undo entry either way
      const f = {};
      if (op.name != null) f.name = String(op.name);
      if (op.comment != null) f.comment = String(op.comment);
      if (op.colour != null || op.color != null) f.colour = String(op.colour != null ? op.colour : op.color);
      if (op.duration != null || op.dur != null) f.dur = Number(op.duration != null ? op.duration : op.dur);
      const t = TL();
      if (t.addMarker(Number(op.at), f) != null) return;
      if (!Object.keys(f).length || !t.editMarker || !t.editMarker(Number(op.at), f)) {
        refuse('there is a marker there already');
      }
    },
    in_out(op) {
      const t = TL();
      if ('in' in op) t.setSeqIn(op.in == null ? null : Number(op.in));
      if ('out' in op) t.setSeqOut(op.out == null ? null : Number(op.out));
    },
    track_add(op) {
      if (!TL().addTrack(op.kind === 'audio' ? 'audio' : 'video')) refuse('no more tracks of that kind');
    },
    overlay(op) {
      if (!TL().addOverlay(range(op), Number(op.at), op.track || 'V2')) refuse(`${op.track || 'V2'} refused the clip (locked, or no such track)`);
    },
    set(op, c) {
      const { seg } = c.target(op.id);
      if (!FX() || !FX().set(op.prop, Number(op.value), seg)) refuse('Effect Controls refused it (is the track locked?)');
    },
    keys(op, c) {
      const t = TL(), fx = FX();
      const { seg, ov } = c.target(op.id);
      const group = KEY_GROUP[op.prop], kp = KEY_PROP[op.prop];
      if (!fx || !group) refuse(`${op.prop} has no stopwatch`);
      // the human: select the clip, park the playhead on the key's frame, stopwatch on,
      // type the value — the stopwatch keys it at the playhead
      if (ov) { t.select([]); t.selectOverlay(seg.id); } else t.select([seg.id], { source: 'api' });
      const start = ov ? Number(seg.at) || 0 : t.filmStart(seg.id);
      const keys = (op.keys || []).slice().sort((a, b) => a[0] - b[0]);
      keys.forEach(([kt, v], n) => {
        t.seek(start + Number(kt));
        if (!fx.keys(kp, c.target(op.id).seg).length) fx.animate(group);
        // a V2 edit replaces the clip's object: look it up again each time
        if (!fx.set(op.prop, Number(v), c.target(op.id).seg)) refuse(`key ${n + 1} was refused (is the track locked?)`);
      });
    },
    // a video effect (wave 14, VFX): the bin's drop, then its section's values (effects.js)
    effect(op, c) {
      if (!FX() || typeof FX().vfxOp !== 'function') refuse('video effects are not loaded');
      FX().vfxOp(op, c.shot(op.id));
    },
    curve(op, c) {
      const { seg } = c.target(op.id);
      if (!FX() || !FX().curve(op.channel || 'master', op.points, seg)) refuse('Lumetri refused the curve');
    },
    wheel(op, c) {
      const { seg } = c.target(op.id);
      const fx = FX();
      const cur = (fx && fx.wheel(op.range, undefined, seg)) || [0, 0, 0];
      const level = op.level == null ? cur[2] : Number(op.level);
      if (!fx || !fx.wheel(op.range, [Number(op.x) || 0, Number(op.y) || 0, level], seg)) refuse('Lumetri refused the wheel');
    },
    // Lumetri › Vignette (wave 15): each value typed in its row, Amount first, as the hand
    vignette(op, c) {
      const { seg, ov } = c.target(op.id);
      if (ov) refuse('Lumetri Color applies to clips on V1');
      const fx = FX();
      ['amount', 'midpoint', 'roundness', 'feather'].forEach((k) => {
        if (op[k] == null) return;
        const prop = `vignette_${k}`;
        if (fx && Math.abs(Number(fx.value(prop, c.target(op.id).seg)) - Number(op[k])) < 1e-9) return;
        if (!fx || !fx.set(prop, Number(op[k]), c.target(op.id).seg)) refuse(`Lumetri refused the vignette's ${k}`);
      });
      void seg;
    },
    look(op, c) {
      const name = String(op.name);
      const strength = op.strength == null ? null : Number(op.strength);
      if (op.ids === 'all') {
        // the film's look select and its strength buttons (app.js buildNone)
        app.colourEdit('film look', () => {
          const col = app.colour();
          col.look = name === 'none' ? null : name;
          if (strength != null) col.strength = strength;
        });
        return;
      }
      const ids = (op.ids || []).map((x) => c.shot(x, 'ids').id);
      // the shot's look select and its strength slider (app.js bindColour)
      app.colourEdit('look', () => ids.forEach((id) => {
        const o = app.shotOverride(id, true);
        if (name === 'film') delete o.look; else o.look = name === 'none' ? null : name;
        if (strength != null) o.strength = strength;
      }));
    },
    match(op, c) {
      const ids = (op.ids || []).map((x) => c.shot(x, 'ids').id);
      const ref = op.reference == null ? null : c.shot(op.reference, 'reference').id;
      // "set as reference", then "match to the shot before" / "match to reference"
      app.colourEdit('match', () => {
        if (ref) app.colour().reference = ref;
        ids.forEach((id) => { app.shotOverride(id, true).match = op.to === 'previous' ? 'previous' : 'reference'; });
      });
    },
    transition(op, c) {
      const t = TL();
      const a = c.shot(op.a, 'a'), b = c.shot(op.b, 'b');
      const k = t.indexOf(a.id);
      if (t.idAt(k + 1) !== b.id) refuse('the two shots are not neighbours any more');
      if (!t.addTransition(k, op.kind, op.dur == null ? undefined : Number(op.dur))) refuse('the shots are too short for it (or the track is locked)');
    },
    fade(op, c) {
      const t = TL();
      const s = c.shot(op.id);
      if (!t.addFade(t.indexOf(s.id), op.edge, op.kind, op.dur == null ? undefined : Number(op.dur))) refuse('the fade was refused (is the track locked?)');
    },
    design_effect(op, c) {
      const s = c.shot(op.id);
      const k = c.designs.length;
      c.designs.push({ shot: s.id, note: op.note });
      c.later.push(() => c.asked.press(k));
    },
    export(op, c) {
      if (!app.segs().length) refuse('the sequence is empty');
      c.later.push(() => runExport(op));
    },
  };

  let applying = false;          // an op list is landing: its own changes are not a recheck

  function applyOps(ops, opts = {}) {
    const t = TL();
    const list = Array.isArray(ops) ? ops : [];
    if (!t || typeof t.batch !== 'function') {
      return { applied: 0, refused: list.map((_, i) => ({ i, why: 'the timeline is not loaded' })), after: Promise.resolve([]) };
    }
    const label = withDesigns(opts.label || labelFor(list), list);
    const ctx = makeCtx();
    const refused = [];
    let applied = 0;
    let out;
    applying = true;
    try {
      out = t.batch(label, () => list.forEach((op, i) => {
        const fn = op && DO[op.op];
        if (!fn) { refused.push({ i, why: `unknown op ${op && op.op}` }); return; }
        try { fn(op, ctx); applied += 1; } catch (e) { refused.push({ i, why: e.refused ? e.message : `failed: ${e.message}` }); }
      }));
    } finally {
      applying = false;
    }
    if (!out.ok) {
      return { applied: 0, refused: list.map((_, i) => ({ i, why: 'another edit is in progress — finish it first' })), after: Promise.resolve([]) };
    }
    // Verdict 12, punch 5: the design requests are part of the accept's one undo entry —
    // the batch's when the cut changed too, else one of their own (tl.mark: it puts nothing
    // back on the board; Designed Effects follows its undo and takes the requests back,
    // fx.js `request`), so the next Ctrl+Z never reaches past them to an older edit.
    let committed = !!out.committed;
    const designs = ctx.designs.length;
    if (designs) {
      if (!committed && typeof t.mark === 'function') committed = !!t.mark(label);
      const fxm = window.fx;
      ctx.asked = fxm && typeof fxm.request === 'function' ? fxm.request(label, ctx.designs, { undoable: committed })
        : { press: async () => { throw new Error('Designed Effects is not loaded'); } };
    }
    const later = ctx.later.slice();
    const done = (async () => {
      const out = [];
      for (const fn of later) {
        try { out.push(await fn()); } catch (e) { out.push({ ok: false, why: e.message }); app.toast(e.message, 5000); }
      }
      return out;
    })();
    return { applied, refused, after: done, label, committed, designs };
  }

  /* An accept with design requests always says so in its entry's name ("AI: In/Out +
   * effect"), so the entry Designed Effects follows (by name) is never one without them —
   * a name shortened to "+ 3 more" becomes "AI: 4 edits + 1 effect". */
  function withDesigns(label, ops) {
    const d = ops.filter((op) => op && op.op === 'design_effect').length;
    if (!d) return label;
    const groups = String(label).replace(/^AI: /, '').split(' + ');
    if (groups.some((g) => /^(\d+ )?effects?$/.test(g))) return label;
    const rest = ops.filter((op) => op && op.op).length - d;
    return `AI: ${rest} edit${rest === 1 ? '' : 's'} + ${d} effect${d === 1 ? '' : 's'}`;
  }

  /* The undo entry's name for an op list (verdict 9, punch 3: "undo: AI: Shot 1 is only
   * 1.92 s now, so 'about 3 s' means" — the model's sentence cut mid-word): what the ops
   * are, in the tools' words — "AI: trim + move", "AI: 3 colour changes" — short enough
   * that the header's "undo: … (Ctrl+Z)" stays within 40 characters, and shortened only
   * by whole items ("AI: trim + move + 2 more"), never inside a word. */
  const LABEL_MAX = 25;              // 40 − "undo: " − " (Ctrl+Z)"
  const COLOUR_PROP = /^(temperature|tint|exposure|contrast|highlights|shadows|whites|blacks|saturation|hsl_|vignette_)/;
  const NOUN = {
    insert: ['insert', 'inserts'], overwrite: ['overwrite', 'overwrites'], append: ['insert', 'inserts'],
    move: ['move', 'moves'], razor: ['razor cut', 'razor cuts'], ripple_delete: ['ripple delete', 'ripple deletes'],
    trim: ['trim', 'trims'], roll: ['roll', 'rolls'], slip: ['slip', 'slips'], speed: ['speed', 'speed changes'],
    marker: ['marker', 'markers'], in_out: ['In/Out', 'In/Out'], track_add: ['new track', 'new tracks'],
    overlay: ['overlay', 'overlays'], keys: ['keyframes', 'keyframe sets'],
    curve: ['colour change', 'colour changes'], wheel: ['colour change', 'colour changes'],
    look: ['colour change', 'colour changes'], match: ['colour change', 'colour changes'],
    transition: ['transition', 'transitions'], fade: ['fade', 'fades'],
    design_effect: ['effect', 'effects'], export: ['export', 'exports'],
    effect: ['video effect', 'video effects'],
    vignette: ['vignette', 'vignettes'],
  };
  function nounOf(op) {
    if (op.op !== 'set') return NOUN[op.op] || ['edit', 'edits'];
    const p = String(op.prop || '');
    if (COLOUR_PROP.test(p)) return ['colour change', 'colour changes'];
    if (p === 'opacity') return ['opacity', 'opacity changes'];
    if (p === 'volume_db') return ['volume', 'volume changes'];
    return ['motion', 'motion changes'];
  }
  function labelFor(ops) {
    const groups = [];                 // [one, many, count], in the order the ops come
    for (const op of ops) {
      if (!op || !op.op) continue;
      const [one, many] = nounOf(op);
      const g = groups.find((x) => x[0] === one);
      if (g) g[2] += 1; else groups.push([one, many, 1]);
    }
    if (!groups.length) return 'AI edit';
    const word = ([one, many, n]) => (n === 1 ? one : `${n} ${many}`);
    const all = `AI: ${groups.map(word).join(' + ')}`;
    if (all.length <= LABEL_MAX) return all;
    for (let k = groups.length - 1; k >= 1; k -= 1) {
      const more = groups.slice(k).reduce((a, g) => a + g[2], 0);
      const s = `AI: ${groups.slice(0, k).map(word).join(' + ')} + ${more} more`;
      if (s.length <= LABEL_MAX) return s;
    }
    const n = groups.reduce((a, g) => a + g[2], 0);
    return `AI: ${n} edit${n === 1 ? '' : 's'}`;
  }

  /* ------------------------------------------------------------ the ops that run after */
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function until(fn, ms = 8000, step = 60) {
    const end = Date.now() + ms;
    for (;;) {
      let v = null;
      try { v = fn(); } catch (e) { v = null; }
      if (v) return v;
      if (Date.now() > end) return null;
      await sleep(step);
    }
  }

  /* Export, as a person makes one: the dialog, its preset, its Frame Size / Frame Rate /
   * Bitrate (verdict 9, punch 5: set after the preset, which fills them with its own, as
   * a person would), range and name, Export. */
  async function runExport(op) {
    const ex = window.exportDialog;
    if (!ex || typeof ex.open !== 'function') throw new Error('Export is not loaded');
    ex.open();
    const sel = await until(() => $('#exportDialog #exPreset'));
    if (!sel) throw new Error('the Export dialog did not open');
    if (op.preset && [...sel.options].some((o) => o.value === op.preset)) {
      sel.value = op.preset;
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    }
    const pick = (q, v) => {
      const el = $(`#exportDialog ${q}`);
      if (!el || v == null || ![...el.options].some((o) => o.value === String(v) && !o.disabled)) return;
      el.value = String(v);
      el.dispatchEvent(new Event('change', { bubbles: true }));
    };
    pick('[data-testid=export-size]', op.size);
    pick('[data-testid=export-fps]', op.fps);
    if (op.bitrate != null) {
      const br = $('#exportDialog [data-testid=export-bitrate]');
      if (br) { br.value = String(op.bitrate); br.dispatchEvent(new Event('input', { bubbles: true })); }
    }
    const rng = $('#exportDialog #exRange');
    if (rng) {
      const want = op.range === 'in_out' ? 'inout' : 'all';
      const opt = [...rng.options].find((o) => o.value === want);
      if (opt && !opt.disabled) { rng.value = want; rng.dispatchEvent(new Event('change', { bubbles: true })); }
    }
    if (op.name) {
      const nm = $('#exportDialog #exName');
      if (nm) { nm.value = op.name; nm.dispatchEvent(new Event('input', { bubbles: true })); }
    }
    // a Frame Size / Frame Rate of its own is measured first (a sample at exactly them)
    const go = await until(() => { const b = $('#exportDialog #exGo'); return b && !b.disabled ? b : null; }, 120000, 150);
    if (!go) throw new Error('Export is still measuring the size — press Export in the dialog');
    go.click();
    return { ok: true, op: 'export' };
  }

  /* ------------------------------------------------------------ describe (a line per op) */
  function describe(op) {
    const t = TL();
    if (!op || !op.op) return '';
    const shot = (id) => {
      const i = t ? t.indexOf(id) : -1;
      return i >= 0 ? `shot ${i + 1} (${stem(t.byId(id).clip)})` : `shot ${id}`;
    };
    const Shot = (id) => { const s = shot(id); return s[0].toUpperCase() + s.slice(1); };
    const r = (o) => `${stem(o.clip)} (${Number(o.in).toFixed(2)}–${Number(o.out).toFixed(2)} s)`;
    const sgn = (v) => `${Number(v) >= 0 ? '+' : '−'}${Math.abs(Number(v)).toFixed(2)} s`;
    const kind = (k) => (t && t.TRANS_NAMES && t.TRANS_NAMES[k]) || k;
    switch (op.op) {
      case 'insert': return `Insert ${r(op)} at ${tc(op.at)}`;
      case 'overwrite': return `Overwrite with ${r(op)} at ${tc(op.at)}`;
      case 'append': return `Add ${r(op)} ${op.after ? `after ${shot(op.after)}` : 'at the end'}`;
      case 'move': return `Move ${(op.ids || []).map(shot).join(', ')} ${op.before ? `before ${shot(op.before)}` : 'to the end'}`;
      case 'razor': return `Razor ${op.id ? shot(op.id) : 'the shot'} at ${tc(op.at)}`;
      case 'ripple_delete': return `Ripple delete ${(op.ids || []).map(shot).join(', ')}`;
      case 'trim': return `Trim ${shot(op.id)}:${op.in != null ? ` in → ${Number(op.in).toFixed(2)} s` : ''}${op.out != null ? ` out → ${Number(op.out).toFixed(2)} s` : ''}`;
      case 'roll': return `Roll the edit between ${shot(op.a)} and ${shot(op.b)} by ${sgn(op.by)}`;
      case 'slip': return `Slip ${shot(op.id)} by ${sgn(op.by)}`;
      case 'speed': return `${Shot(op.id)}: Speed ${Math.round(Number(op.rate) * 100)} %`;
      case 'marker': {
        const bits = [op.name ? `“${op.name}”` : '',
          op.colour && op.colour !== 'green' ? op.colour.charAt(0).toUpperCase() + op.colour.slice(1) : '',
          Number(op.duration) > 0 ? `${Number(op.duration).toFixed(2)} s long` : '',
          op.comment ? 'a comment' : ''].filter(Boolean);
        return `Marker at ${tc(op.at)}${bits.length ? `: ${bits.join(', ')}` : ''}`;
      }
      case 'in_out': return `Sequence${'in' in op ? ` In ${op.in == null ? 'cleared' : tc(op.in)}` : ''}${'out' in op ? ` Out ${op.out == null ? 'cleared' : tc(op.out)}` : ''}`;
      case 'track_add': return `Add a ${op.kind} track`;
      case 'overlay': return `${r(op)} on ${op.track || 'V2'} at ${tc(op.at)}`;
      case 'set': return `${Shot(op.id)}: ${op.prop} ${op.value}`;
      case 'keys': return `${Shot(op.id)}: ${op.prop} keyframes (${(op.keys || []).length})`;
      case 'effect': return `${Shot(op.id)}: ${op.remove ? 'clear ' : ''}${((FX() && FX().VFX) || {})[op.kind] ? FX().VFX[op.kind].name : op.kind}`;
      case 'curve': return `${Shot(op.id)}: Lumetri ${op.channel || 'master'} curve`;
      case 'wheel': return `${Shot(op.id)}: Lumetri ${op.range} wheel`;
      case 'vignette': return `${Shot(op.id)}: Lumetri Vignette`;
      case 'look': return `${op.ids === 'all' ? 'The film' : (op.ids || []).map(Shot).join(', ')}: look ${op.name}`;
      case 'match': return `${(op.ids || []).map(Shot).join(', ')}: Color Match to the ${op.to === 'previous' ? 'shot before' : 'reference'}`;
      case 'transition': return `${kind(op.kind)} between ${shot(op.a)} and ${shot(op.b)}`;
      case 'fade': return `${kind(op.kind)} at the ${op.edge} of ${shot(op.id)}`;
      case 'design_effect': return `Designed effect on ${shot(op.id)}: “${op.note}”`;
      case 'export': {
        const v = exportVideo(op, R.exportInfo, R.exportInfo && (R.exportInfo.presets || []).find((p) => p.key === op.preset));
        const own = [op.size != null && op.size !== '' ? v.frame : '', op.fps != null && op.fps !== '' ? v.fps : '', v.bitrate]
          .filter(Boolean);
        return `Export ${op.preset || 'quick'}${own.length ? ` at ${own.join(', ')}` : ''}, `
          + `${op.range === 'in_out' ? 'Sequence In/Out' : 'Entire Sequence'}${op.name ? ` → ${op.name}.mp4` : ''}`;
      }
      default: return op.op;
    }
  }

  /* ------------------------------------------------------------ the review */
  const R = {
    res: null,             // the /api/assist result under review
    ops: [],               // its ops
    lines: [],
    verdict: [],           // per op: true (accept) | false (reject)
    blocked: new Map(),    // op index → why it cannot be applied to the cut as it is now
    after: true,           // the Program shows the After picture
    saved: null,           // the in-memory preview's snapshot (Before), while After is up
    ghostGen: 0,
    planTimer: 0,
    planWhat: '',
    exportInfo: null,      // GET /api/export, when an export op is under review
    el: null,
  };

  const opIsLook = (op) => LOOK_OPS.has(op.op);
  const livePreview = (op) => opIsLook(op);
  const LUT_OPS = new Set(['look', 'match']);          // shown through a preview LUT (grade.preview)
  const depsOf = (op) => {
    const out = [];
    ['id', 'a', 'b', 'before', 'after', 'reference'].forEach((k) => { if (typeof op[k] === 'string' && op[k].startsWith('new:')) out.push(op[k]); });
    (Array.isArray(op.ids) ? op.ids : []).forEach((x) => { if (String(x).startsWith('new:')) out.push(String(x)); });
    return out;
  };
  const makerOf = (ph) => R.ops.findIndex((o) => o.makes === ph);
  const accepted = () => R.ops.map((_, i) => i).filter((i) => R.verdict[i] && !R.blocked.has(i));

  function panel() {
    if (R.el && R.el.isConnected) return R.el;
    const host = document.querySelector('#tools section[data-tool="ask"]') || document.body;
    const el = document.createElement('div');
    el.id = 'aiReview';
    el.className = 'air';
    el.setAttribute('data-testid', 'ai-review');
    el.setAttribute('role', 'region');
    el.setAttribute('aria-label', 'AI edit review');
    el.hidden = true;
    host.insertBefore(el, host.firstChild);
    el.addEventListener('click', onClick);
    R.el = el;
    return el;
  }

  /* The call's price in section S's words: "free on your plan" on the subscription
   * backend, the API-equivalent estimate in the hover; the estimate itself on the API. */
  function costLine(cost) {
    if (!cost || typeof cost.usd !== 'number' || !window.rcCost) return '';
    const p = { charged: cost.charged, backend: cost.backend };
    const hover = cost.measured ? rcCost.spent(cost.usd, p) : rcCost.note(cost.usd, p);
    return `<span class="air-cost" data-testid="ai-cost" title="${esc(hover)}">${esc(rcCost.tag(cost.usd, p))}</span>`;
  }

  function rowHtml(op, i) {
    const fam = FAMILY[op.op] || 'timeline';
    const line = named(R.lines[i] || describe(op));
    const block = R.blocked.has(i) ? named(R.blocked.get(i)) : undefined;
    const on = !!R.verdict[i] && !block;
    const shows = CUT_OPS.has(op.op) ? 'shown as the ghost lane under V1 until you accept'
      : livePreview(op) ? 'shown on the Program monitor (◐ Before / After) until you accept'
        : TX_OPS.has(op.op) ? 'drawn dashed on the timeline, where it will sit, until you accept'
          : op.op === 'export' ? 'runs only when you accept'
            : op.op === 'design_effect' ? 'opens Designed Effects on this shot with this note; its own review follows'
              : 'applied when you accept';
    return `<li class="air-op${on ? '' : ' off'}${block ? ' blocked' : ''}" data-testid="ai-op-row" data-op-index="${i}" data-op="${esc(op.op)}" data-family="${fam}" aria-checked="${on}">
        <span class="air-fam" title="${esc(FAMILY_NAME[fam])}">${ICON[fam]}</span>
        <span class="air-line" title="${esc(block ? `can't apply now: ${block}` : shows)}">${esc(line)}${block ? `<span class="air-why">can't apply now: ${esc(block)}</span>` : ''}</span>
        <span class="air-vote" role="group" aria-label="this edit">
          <button type="button" class="air-yes${on ? ' on' : ''}" data-testid="ai-op-accept" data-i="${i}" title="keep this edit"${block ? ' disabled' : ''} aria-pressed="${on}">✓</button>
          <button type="button" class="air-no${on ? '' : ' on'}" data-testid="ai-op-reject" data-i="${i}" title="leave this edit out" aria-pressed="${!on}">✗</button>
        </span></li>`;
  }

  /* What an export op will write: the Export dialog's own path rule (export.js
   * `component`, the folder it remembers, GET /api/export's defaults) and the length. */
  function component(text, fallback) {
    let t = String(text || '').trim().replace(/\.mp4$/i, '');
    t = t.replace(/[^A-Za-z0-9 ._()-]+/g, '-').replace(/\s+/g, ' ').replace(/^[ .-]+|[ .-]+$/g, '');
    t = t.slice(0, 80).replace(/^[ .-]+|[ .-]+$/g, '');
    return t || fallback;
  }
  /* Export Settings › Video on an export op (lane EXPORT2, verdict 9 punch 5: `size` a
   * Frame Size key — "720" — or "match", `fps` a Frame Rate key — "29.97" — or "match",
   * `bitrate` a target in Mbps; roughcut/actions.py EXPORT_SIZES / EXPORT_RATES): the
   * frame, rate and bitrate the file is written at, in the dialog's words — the chosen
   * Frame Size's pixels from GET /api/export's `sizes` (else its name, "720p"), the
   * preset's own frame and rate when the op leaves them, a bitrate only when one is set. */
  const SIZE_WORDS = { match: 'Match Sequence frame', 2160: '2160p', 1080: '1080p', 720: '720p', 540: '540p' };
  const fpsWords = (v) => {
    const n = Number(v);
    return Number.isFinite(n) && n > 0 ? `${String(Math.round(n * 1000) / 1000)} fps` : '';
  };
  function exportVideo(op, info, preset) {
    const out = { frame: '', fps: '', bitrate: '' };
    const sizes = (info && Array.isArray(info.sizes)) ? info.sizes : [];
    const rates = (info && Array.isArray(info.rates)) ? info.rates : [];
    if (op.size != null && op.size !== '') {
      const k = String(op.size);
      const sz = sizes.find((x) => String(x.key) === k);
      out.frame = sz && sz.width && sz.height ? `${sz.width}×${sz.height}` : (SIZE_WORDS[k] || `${k}p`);
    } else if (preset && preset.width && preset.height) {
      out.frame = `${preset.width}×${preset.height}`;
    }
    if (op.fps != null && op.fps !== '') {
      const k = String(op.fps);
      const r = rates.find((x) => String(x.key) === k);
      out.fps = k === 'match' ? (fpsWords(r ? r.fps : info && info.fps) || 'the sequence\'s frame rate')
        : (fpsWords(k) || `${k} fps`);
    } else if (preset && preset.fps) {
      out.fps = fpsWords(preset.fps);
    }
    if (op.bitrate != null && op.bitrate !== '' && Number(op.bitrate) > 0) out.bitrate = `${Number(op.bitrate)} Mbps`;
    return out;
  }
  // a preset by its name, its frame being said beside it: "Quick 1080p" → "Quick"
  const presetName = (label) => String(label || '').replace(/\s+(\d{3,4}p|\d+K)$/i, '');

  function exportWords(op) {
    const t = TL();
    const st = t ? t.state : {};
    const total = t ? t.total() : 0;
    const a = op.range === 'in_out' ? (st.seqIn ?? 0) : 0;
    const b = op.range === 'in_out' ? (st.seqOut ?? total) : total;
    const info = R.exportInfo;
    const preset = info && (info.presets || []).find((p) => p.key === op.preset);
    let folder = info ? info.folder : '';
    try { folder = localStorage.getItem('rc.export.folder') || folder; } catch (e) { /* private window */ }
    const path = info ? `${info.root}/${component(folder, info.folder)}/${component(op.name, info.name)}.mp4`
      : `${op.name ? `${op.name}.mp4` : 'a new file'} under --work/renders`;
    const v = exportVideo(op, info, preset);
    const name = preset ? presetName(preset.label) : (op.preset ? presetName(String(op.preset)) : '');
    return [path, v.frame, v.fps, v.bitrate, name, tc(Math.max(0, b - a))].filter(Boolean).join(' · ');
  }
  async function loadExportInfo() {
    if (!R.ops.some((op) => op.op === 'export')) return;
    try { R.exportInfo = await (await fetch('/api/export')).json(); } catch (e) { R.exportInfo = null; }
    if (R.res) paint();
  }

  function paint() {
    const el = panel();
    const res = R.res;
    syncTall();
    if (!res) { el.hidden = true; return; }
    el.hidden = false;
    el.dataset.kind = res.kind || '';
    const n = R.ops.length, acc = accepted().length;
    const asked = res.asked || res.text_asked || '';      // AIPROMPT sends `asked`, the GET `text_asked`
    const count = res.kind === 'ops' ? `<span class="air-n">${n} edit${n === 1 ? '' : 's'}</span>` : '';
    const head = `<div class="air-head"><span class="air-title">${ICON.designed_effects}<b>AI edit</b></span>${count}</div>
      ${asked ? `<div class="air-asked" title="what you asked">“${esc(asked)}”</div>` : ''}`;
    const cost = costLine(res.cost);
    if (!['ops', 'plan', 'answer'].includes(res.kind)) {
      // a job that failed or was stopped: say so, never an empty review
      const why = res.status === 'cancelled' ? 'Stopped — nothing was changed.'
        : `The AI could not answer: ${named(res.error || res.detail || 'no detail')}`;
      el.innerHTML = `${head}<div class="air-answer air-failed">${esc(why)}</div>
        <div class="air-foot">${cost}<span class="air-grow"></span><button type="button" class="air-btn" data-act="close" data-testid="ai-review-close">Close</button></div>`;
      return;
    }
    if (res.kind === 'answer') {
      el.innerHTML = `${head}<div class="air-answer">${esc(named(res.text || res.summary || ''))}</div>
        <div class="air-foot">${cost}<span class="air-grow"></span><button type="button" class="air-btn" data-act="close" data-testid="ai-review-close">Close</button></div>`;
      return;
    }
    if (res.kind === 'plan') {
      el.innerHTML = `${head}<div class="air-sum">${esc(named(res.summary || 'Re-cutting the sequence'))}</div>
        <div class="air-plan" data-testid="ai-plan"><span class="air-spin" aria-hidden="true"></span><span class="air-plan-what">${esc(R.planWhat || 'the proposal opens here when it is ready')}</span></div>
        <div class="air-foot">${cost}<span class="air-grow"></span><button type="button" class="air-btn" data-act="close" data-testid="ai-review-close">Close</button></div>`;
      return;
    }
    const looks = R.ops.some((op, i) => livePreview(op) && R.verdict[i] && !R.blocked.has(i));
    const cut = R.ops.some((op, i) => CUT_OPS.has(op.op) && R.verdict[i] && !R.blocked.has(i));
    const exp = R.ops.find((op, i) => op.op === 'export' && R.verdict[i] && !R.blocked.has(i));
    const txs = txList();
    const tkind = (k) => { const t = TL(); return (t && t.TRANS_NAMES && t.TRANS_NAMES[k]) || 'transition'; };
    const txWords = txs.length === 1 ? `${tkind(txs[0].kind)} on the timeline` : `${txs.length} transitions on the timeline`;
    const refused = (res.refused || []).filter((x) => x && x.why);
    const preview = [cut ? '<span class="air-pv" title="the cut with these edits, drawn under V1 — nothing has changed yet"><i class="air-ghost" aria-hidden="true"></i>New cut under V1</span>' : '',
      txs.length ? `<span class="air-pv" data-testid="ai-tx-preview" title="drawn dashed where ${txs.length === 1 ? 'it' : 'they'} will sit${cut ? ', on the new cut under V1' : ''} — nothing has changed yet"><i class="air-txg" aria-hidden="true"></i>${esc(txWords)}</span>` : '',
      looks ? `<button type="button" class="air-ab${R.after ? ' on' : ''}" data-act="ab" data-testid="ai-before-after" aria-pressed="${R.after}" title="the Program monitor shows the colour and Effect Controls changes (After) or the shot as it is (Before) — nothing is saved until you accept">◐ ${R.after ? 'After' : 'Before'}</button>` : '',
      exp ? `<span class="air-pv air-file" data-testid="ai-export-what" title="the Export dialog writes this file when you accept — never over an existing one (it becomes -2, -3 …)">writes ${esc(exportWords(exp))}</span>` : ''].filter(Boolean).join('');
    el.innerHTML = `${head}
      ${res.summary ? `<div class="air-sum">${esc(named(res.summary))}</div>` : ''}
      <ol class="air-ops">${R.ops.map(rowHtml).join('')}${refused.map((x) => `<li class="air-op refused" data-testid="ai-op-refused" data-op="${esc(x.op || '')}"><span class="air-fam" aria-hidden="true">⊘</span><span class="air-line">${esc(String(x.op || 'an op').replace('_', ' '))} not applied: ${esc(named(x.why))}</span></li>`).join('')}</ol>
      ${preview ? `<div class="air-preview">${preview}</div>` : ''}
      <div class="air-foot">
        <button type="button" class="air-btn" data-act="reject" data-testid="ai-reject-all" title="put everything back as it was — nothing is written (Esc)">Reject all</button>
        ${cost}
        <span class="air-grow"></span>
        <button type="button" class="air-btn primary" data-act="accept" data-testid="ai-accept-all"${acc ? '' : ' disabled'} title="apply the edits you kept as one undo step (${esc(app.undoKey())} takes them all back)">${acc === n ? (n === 1 ? 'Accept' : 'Accept all') : `Accept ${acc} of ${n}`}</button>
      </div>`;
    if (window.dock && dock.badge) dock.badge('ask', acc || 0);
  }

  /* While a review is up and the Assistant is the group's open tab, the Effect Controls
   * column runs the window's full height, as it does for a waiting proposal (app.js
   * `ptall`): the Program and the Timeline keep their size, the Source monitor gives the
   * room. Its own class, so the two never fight over one. */
  let tallWatch = null;
  function syncTall() {
    const ws = document.getElementById('ws');
    const sec = R.el && R.el.closest('section[data-tool]');
    if (!ws) return;
    if (sec && !tallWatch && typeof MutationObserver !== 'undefined') {
      tallWatch = new MutationObserver(() => syncTall());
      tallWatch.observe(sec, { attributes: true, attributeFilter: ['hidden'] });
    }
    ws.classList.toggle('aitall', !!R.res && !!sec && !sec.hidden);
    // while it waits the review owns the Assistant, as a waiting proposal does: the story
    // and the Ask form under it step aside (the AI prompt is where a new ask starts)
    if (sec) sec.classList.toggle('aiup', !!R.res);
  }

  /* Esc with the focus in the review rejects it. On window, capture phase, registered
   * when this file loads: ahead of the timeline's keys (timeline-keys.js, registered at
   * mount), whose Escape clears the selection and stops the event. */
  function onKeys(e) {
    if (e.key !== 'Escape' || !R.res || !R.el) return;
    const tg = e.target instanceof Element ? e.target : null;
    if (!tg || !R.el.contains(tg)) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    reject();
  }
  window.addEventListener('keydown', onKeys, true);

  function setVerdict(i, on) {
    if (R.blocked.has(i) && on) return;
    R.verdict[i] = on;
    const op = R.ops[i];
    if (!on && op && op.makes) {             // what names its shot goes with it
      R.ops.forEach((o, j) => { if (depsOf(o).includes(op.makes)) R.verdict[j] = false; });
    }
    if (on && op) depsOf(op).forEach((ph) => { const m = makerOf(ph); if (m >= 0) R.verdict[m] = true; });
    refreshPreview();
  }

  function onClick(e) {
    const b = e.target.closest('button');
    if (!b || !R.el.contains(b)) return;
    const tid = b.getAttribute('data-testid');
    if (tid === 'ai-op-accept') setVerdict(Number(b.dataset.i), true);
    else if (tid === 'ai-op-reject') setVerdict(Number(b.dataset.i), false);
    else if (b.dataset.act === 'accept') accept();
    else if (b.dataset.act === 'reject') reject();
    else if (b.dataset.act === 'close') closeQuiet();
    else if (b.dataset.act === 'ab') { R.after = !R.after; refreshLook(); paint(); }
  }

  /* --------------- the cut's preview: the ghost lane, from the server's simulation */
  function boardSeq() {
    const t = TL();
    const st = t.state;
    return { segments: t.forSave ? t.forSave() : app.segs(), markers: t.markers(), in: st.seqIn ?? null,
             out: st.seqOut ?? null, transitions: t.transitions(), overlays: t.overlays(),
             tracks: t.tracks ? t.tracks() : {}, colour: app.colour() || {} };
  }

  async function check(ops) {
    const r = await fetch('/api/assist/preview', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ ops, ...boardSeq() }),
    });
    if (!r.ok) throw new Error(`the preview failed (${r.status})`);
    return r.json();
  }

  async function refreshGhost() {
    const gen = ++R.ghostGen;
    const lanes = window.tlLanes;
    const idx = accepted();
    if (!idx.some((i) => CUT_OPS.has(R.ops[i].op))) { if (lanes && lanes.clearGhost) lanes.clearGhost(); return; }
    let d;
    try { d = await check(idx.map((i) => R.ops[i])); } catch (e) { return; }
    if (gen !== R.ghostGen || !R.res) return;
    if (d.preview && lanes && lanes.showGhost) lanes.showGhost(d.preview.segments);
  }

  /* --------------- a transition's preview: its dashed box on the edit (verdict 9, punch 6) */
  /* The accepted transition and fade ops, as the lane draws them: ids as the review has
   * them (a `new:n` is found on the ghost lane, where the shot that names it is drawn). */
  function txList() {
    if (!R.res || R.res.kind !== 'ops') return [];
    return accepted().map((i) => R.ops[i]).filter((op) => op && TX_OPS.has(op.op)).map((op) => (op.op === 'transition'
      ? { kind: op.kind, dur: op.dur, a: op.a, b: op.b }
      : { kind: op.kind, dur: op.dur, id: op.id, edge: op.edge }));
  }
  function refreshTx() {
    const lanes = window.tlLanes;
    if (!lanes || typeof lanes.showTransitions !== 'function') return;
    const list = txList();
    if (list.length) lanes.showTransitions(list); else lanes.clearTransitions();
  }
  function clearTx() {
    const lanes = window.tlLanes;
    if (lanes && typeof lanes.clearTransitions === 'function') lanes.clearTransitions();
  }

  /* --------------- the look's preview: After on the Program, nothing saved */
  function lookTargetOk(op) {
    const t = TL();
    // V1 shots (a V2+ clip's controls save with the tracks; a `new:` shot is not made yet)
    const onV1 = (x) => { const s = t && x != null && t.byId(x); return !!(s && t.indexOf(s.id) >= 0); };
    const ids = Array.isArray(op.ids) ? op.ids : [];
    if (op.op === 'look') return op.ids === 'all' || (ids.length > 0 && ids.every(onV1));
    if (op.op === 'match') return ids.length > 0 && ids.every(onV1) && (op.reference == null || onV1(op.reference));
    return onV1(op.id);
  }
  // the shot a preview parks on: its op's shot (a look on the whole film: none)
  const shotOf = (op) => (LUT_OPS.has(op.op) ? (Array.isArray(op.ids) ? op.ids[0] : null) : op.id);
  function lookOn() {
    if (R.saved || !R.after || !R.res) return;
    const t = TL();
    const ops = accepted().map((i) => R.ops[i]).filter((op) => livePreview(op) && lookTargetOk(op));
    if (!ops.length) return;
    const list = app.segs();
    R.saved = {
      controls: new Map(list.map((s) => [s.id, s.controls ? JSON.stringify(s.controls) : null])),
      colour: JSON.stringify(app.colour() || {}),
      sel: [...t.state.sel], playhead: t.state.playhead,
    };
    const ctx = makeCtx();
    // in memory only: no undo entry, no autosave (lookOff puts every control back itself)
    const out = t.batch('AI preview', () => ops.forEach((op) => {
      try { DO[op.op](op, ctx); } catch (e) { /* its row says so */ }
    }), { record: false });
    if (!out.ok) { R.saved = null; return; }    // a drag holds the timeline: no preview now
    if (R.el) R.el.classList.add('previewing');
    // a look or a Color Match is in no LUT the server baked: the monitor takes the LUTs
    // baked from the colour block as it is now, in memory (grade.js `preview`)
    R.saved.graded = ops.some((op) => LUT_OPS.has(op.op));
    if (R.saved.graded && window.grade && typeof grade.preview === 'function') grade.preview(app.colour() || {});
    const fid = ops.map(shotOf).find((x) => x != null);
    const first = fid == null ? null : t.byId(ctx.id(fid));
    if (first && !app.playing()) {
      const at = t.filmStart(first.id);
      if (!(t.state.playhead >= at && t.state.playhead < at + t.dur(first))) t.seek(at);
    }
  }
  /* `keepGrade` (Accept): the preview LUT stays on the monitor while the accepted ops are
   * applied for real; accept() then hands it the block they made. */
  function lookOff(opts) {
    if (!R.saved) return false;
    const t = TL();
    const s = R.saved;
    R.saved = null;
    app.segs().forEach((seg) => {
      if (!s.controls.has(seg.id)) return;
      const c = s.controls.get(seg.id);
      if (c == null) delete seg.controls; else seg.controls = JSON.parse(c);
    });
    app.setColour(JSON.parse(s.colour));
    if (s.graded && !(opts && opts.keepGrade) && window.grade && typeof grade.preview === 'function') grade.preview(null);
    t.select(s.sel, { source: 'api' });
    t.setPlayhead(Math.min(s.playhead, t.total()));
    app.renderInspector();
    if (FX() && FX().apply) FX().apply();
    if (window.grade && grade.draw) grade.draw();
    if (R.el) R.el.classList.remove('previewing');
    return true;
  }
  function refreshLook() { lookOff(); lookOn(); }

  function refreshPreview() {
    paint();
    refreshLook();
    refreshGhost();
    refreshTx();
  }

  /* An edit anywhere else takes the look's preview down first, so the Before state is the
   * one that edit starts from (and the one any save writes). Playing and looking around
   * the Program does not. */
  function guard(e) {
    if (!R.saved) return;
    const tg = e.target instanceof Element ? e.target : null;
    if (tg && (tg.closest('#aiReview') || tg.closest('#pProgram'))) return;
    if (e.type === 'keydown') {
      const k = e.key;
      if (!e.ctrlKey && !e.metaKey && !e.altKey && [' ', 'j', 'k', 'l', 'J', 'K', 'L', 'ArrowLeft', 'ArrowRight',
        'Home', 'End', 'Shift', 'Control', 'Alt', 'Meta', 'Escape'].includes(k)) return;
    }
    lookOff();
    R.after = false;
    paint();
  }
  ['pointerdown', 'keydown', 'dragstart', 'drop'].forEach((ev) => window.addEventListener(ev, guard, true));
  window.addEventListener('pagehide', () => lookOff(), true);
  document.addEventListener('visibilitychange', () => { if (document.hidden) lookOff(); }, true);

  /* The board's cut changed under the review (an edit, an undo): the rows and the ghost
   * are checked again against it. */
  let recheckT = 0;
  function recheckSoon() {
    if (!R.res || R.res.kind !== 'ops' || applying || R.saved) return;
    clearTimeout(recheckT);
    recheckT = setTimeout(recheck, 250);
  }
  async function recheck() {
    if (!R.res || R.res.kind !== 'ops') return;
    const res = R.res;
    let d;
    try { d = await check(R.ops); } catch (e) { return; }
    if (R.res !== res) return;
    const ok = new Set(d.index || []);
    R.blocked = new Map();
    (d.refused || []).forEach((x) => { if (x.i != null) R.blocked.set(x.i, x.why); });
    R.ops.forEach((_, i) => { if (!ok.has(i) && !R.blocked.has(i)) R.blocked.set(i, 'not valid on the cut as it is now'); });
    (d.index || []).forEach((src, k) => { if (d.lines && d.lines[k]) R.lines[src] = d.lines[k]; });
    paint();
    refreshGhost();
    refreshTx();
  }

  /* --------------- opening, answering */
  async function review(result) {
    const res = result && typeof result === 'object' ? result : null;
    if (!res) return false;
    // an answer whose edits were accepted is in the cut: a second review would apply
    // them again (its razor twice). Ctrl+Z is the way back; a rejected one may reopen.
    const took = res.kind === 'ops' && res.job
      ? (answered.has(res.job) ? answered.get(res.job) : ((res.answer && res.answer.accepted) || []).length) : 0;
    if (took) {
      app.toast(`${took === 1 ? 'that edit is' : 'those edits are'} already in the cut — ${app.undoKey()} takes ${took === 1 ? 'it' : 'them'} back`, 4000);
      return false;
    }
    if (R.res) closeQuiet();
    R.res = res;
    R.after = true;
    R.blocked = new Map();
    R.planWhat = '';
    if (res.kind === 'ops') {
      R.ops = Array.isArray(res.ops) ? res.ops.slice() : [];
      R.lines = Array.isArray(res.lines) ? res.lines.slice() : R.ops.map(describe);
      R.verdict = R.ops.map(() => true);
    } else {
      R.ops = []; R.lines = []; R.verdict = [];
    }
    paint();
    refreshTx();
    if (window.dock && dock.reveal) dock.reveal('#aiReview');
    if (res.kind === 'plan') { followPlan(res); return true; }
    loadExportInfo();
    if (res.kind !== 'ops') return true;
    // nothing waiting in the autosave rides the preview; then the review opens on the cut
    // the board holds now
    try { await app.flush(); } catch (e) { /* the save says so itself */ }
    if (R.res !== res) return true;
    await recheck();
    if (R.res !== res) return true;
    lookOn();
    paint();
    return true;
  }

  function followPlan(res) {
    clearInterval(R.planTimer);
    if (!res.plan_ask) { R.planWhat = 'the re-cut did not start'; paint(); return; }
    R.planWhat = 'reading the footage for the new cut…';
    paint();
    R.planTimer = setInterval(async () => {
      let s;
      try { s = await (await fetch(`/api/ask/${encodeURIComponent(res.plan_ask)}`)).json(); } catch (e) { return; }
      if (R.res !== res) { clearInterval(R.planTimer); return; }
      if (s.state === 'done' && s.plan) {
        clearInterval(R.planTimer);
        closeQuiet();
        if (typeof window.rcShowProposal === 'function') window.rcShowProposal(s.plan);
        return;
      }
      if (s.state === 'failed' || s.state === 'cancelled') {
        clearInterval(R.planTimer);
        R.planWhat = `the re-cut failed: ${s.detail || 'no detail'}`;
        paint();
        return;
      }
      R.planWhat = s.detail || 'reading the footage for the new cut…';
      const w = R.el && R.el.querySelector('.air-plan-what');
      if (w) w.textContent = R.planWhat;
    }, 1000);
  }

  /* The review on the record, and said to the page: the AI prompt's line (aiprompt.js)
   * turns "3 changes ready · Review" into what landed ("3 of 4 applied · Undo",
   * "Rejected — nothing changed"), so the same answer is never reviewed — and applied —
   * twice; `label` / `committed` name the undo entry the accept made, `designs` how many
   * of the accepted ops are design requests (never "applied": verdict 12, punch 5). */
  const answered = new Map();      // job → ops accepted, once its review is answered
  function answer(acc, rej, made = {}) {
    const job = R.res && R.res.job;
    if (!job) return;
    answered.set(job, acc.length);
    window.dispatchEvent(new CustomEvent('rc:assist-answered', {
      detail: { job, accepted: acc, rejected: rej, label: made.label || '', committed: !!made.committed,
                designs: made.designs || 0 } }));
    fetch(`/api/assist/${encodeURIComponent(job)}/answer`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, credentials: 'omit',
      body: JSON.stringify({ accepted: acc, rejected: rej }),
    }).catch(() => {});
  }

  function accept() {
    if (!R.res || R.res.kind !== 'ops') return null;
    const idx = accepted();
    if (!idx.length) return null;
    lookOff({ keepGrade: true });
    if (window.tlLanes && tlLanes.clearGhost) tlLanes.clearGhost();
    clearTx();
    // the entry is named for what the ops are, never the model's summary cut to fit (the
    // summary stays the review's own line)
    const out = applyOps(idx.map((i) => R.ops[i]), { label: labelFor(idx.map((i) => R.ops[i])) });
    const label = out.label || '';                 // with its design requests named (withDesigns)
    const failed = new Set(out.refused.map((x) => idx[x.i]));
    const took = idx.filter((i) => !failed.has(i));
    // the accepted look shows through the block it made until its save lands (then the
    // saved LUT, baked by the same function, takes over): no blink back to Before
    if (window.grade && typeof grade.preview === 'function') {
      if (took.some((i) => LUT_OPS.has(R.ops[i].op))) grade.preview(app.colour() || {}, { untilSave: true });
      else if (grade.previewing) grade.preview(null);
    }
    const rej = R.ops.map((_, i) => i).filter((i) => !took.includes(i));
    answer(took, rej, { label, committed: out.committed, designs: out.designs || 0 });
    const n = took.length;
    // a design request is not an applied edit (verdict 12, punch 5: "✓ 2 changes applied"
    // though nothing was): it is being designed, and its proposal waits in Designed Effects
    const d = out.designs || 0, cuts = n - d;
    const designing = d ? `${d === 1 ? 'an effect' : `${d} effects`} being designed — review ${d === 1 ? 'it' : 'them'} in Designed Effects` : '';
    const said = out.refused.length
      ? `${[cuts ? `applied ${cuts} of ${idx.length}` : '', designing].filter(Boolean).join(' · ') || 'nothing applied'} — ${named(out.refused[0].why)}`
      : `${[cuts ? `applied ${cuts === 1 ? 'the edit' : `${cuts} edits`}` : '', designing].filter(Boolean).join(' · ')}`
        + (out.committed ? ` — ${app.undoKey()} undoes ${n === 1 ? 'it' : 'them'}` : '');
    closeQuiet();
    app.toast(said, out.refused.length ? 6000 : d ? 5000 : 3000);
    return out;
  }

  function reject() {
    if (!R.res) return;
    if (R.res.kind === 'ops') answer([], R.ops.map((_, i) => i));
    const was = R.res.kind;
    closeQuiet();
    if (was === 'ops') app.toast('discarded — nothing changed');
  }

  function closeQuiet() {
    lookOff();
    clearInterval(R.planTimer);
    clearTimeout(recheckT);
    R.ghostGen += 1;
    if (R.res && R.res.kind === 'ops' && window.tlLanes && tlLanes.clearGhost) tlLanes.clearGhost();
    if (R.res && R.res.kind === 'ops') clearTx();
    R.res = null; R.ops = []; R.lines = []; R.verdict = []; R.blocked = new Map();
    if (R.el) { R.el.hidden = true; R.el.innerHTML = ''; R.el.classList.remove('previewing'); }
    syncTall();
    if (window.dock && dock.badge) dock.badge('ask', 0);
  }

  function init() {
    const t = TL();
    if (!t || typeof t.on !== 'function') { setTimeout(init, 200); return; }
    t.on('change', () => recheckSoon());
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();

  window.rcActions = {
    apply: (ops, opts) => applyOps(ops, opts || {}),
    review,
    describe,
    label: (ops) => labelFor(Array.isArray(ops) ? ops : []),
    accept,
    reject,
    close: closeQuiet,
    get open() { return !!R.res; },
    get previewing() { return !!R.saved; },
    state: () => ({ kind: R.res && R.res.kind, ops: R.ops.slice(), verdict: R.verdict.slice(),
                    blocked: [...R.blocked.entries()], after: R.after, previewing: !!R.saved }),
  };
})();
