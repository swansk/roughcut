/* Roughcut — keyboard-first editing on the timeline (INTAKE M9, I9.3).
 *
 * Built ON the foundation (timeline.js — its API comment block is the contract) and
 * loaded right after it; it never patches the foundation or app.js. Everything here is a
 * keydown on `window`, listened for in the CAPTURE phase so it runs before app.js's own
 * handler (and before the switcher's, which is capture on `document`), and only the keys
 * this file owns are stopped (`stopImmediatePropagation`) — space, `u`, `[ ] { }`, the
 * foundation's `+ - \` and ⌘Z/⌘⇧Z, and the switcher's `o` while no clip is open in Find,
 * all reach their owners as before. Nothing fires while the focus is in an input, a
 * textarea or a contenteditable.
 *
 * The keys (KEYS below is the one table; the map in the Keys panel is rendered from it):
 *
 *   J K L        shuttle the monitor — L forward, again for 2× 4× 8×; J reverse (driven
 *                by rAF, as the pass does — Chromium cannot play backwards), again to
 *                stack; K pause. Hold K and tap L / J: 1× while held, paused on release.
 *                The shuttle's rate MULTIPLIES the shot's own `speed` (INTAKE M13): a
 *                0.5× shot under L×2 plays at 1; app.js's `arm()` keeps the shot's rate
 *                on the buffer (`dataset.speed`) and the shuttle rides on
 *                `defaultPlaybackRate`, so the two never overwrite each other.
 *   ↑ ↓          previous / next cut: the playhead to the boundary, that shot selected.
 *   Home End     the film's start / end.
 *   ← →          one frame at the sequence rate (`tl.frame()`); ⇧ five frames, as in
 *                Premiere. The playhead only — the trim lane's ⌥← ⌥→ / ⌘← ⌘→ move an edge.
 *   I O          Premiere's marks: the sequence's in / out at the playhead (on the ruler).
 *                While a clip is open in Find and the timeline was not the last thing
 *                clicked, they mark that clip instead (the source), and ↵ inserts the
 *                range after the selected shot with the clip's transcript line as why.
 *                ⌥I ⌥O clear one, ⌘⇧X both. (O was the switcher's picker on the board.)
 *   , .          insert / overwrite at the playhead from the source: the Source monitor's
 *                clip and In / Out (`window.rcSource = {clip, inPoint, outPoint}`), else a
 *                clip open in Find with both marks, else the bin's selected keep (the
 *                range "+ add" would put in). Insert splits the shot under the playhead
 *                and pushes the rest later; overwrite covers the film from the playhead
 *                for the clip's length. The playhead then sits after the new shot.
 *   V C B N Y A  the tools (`tl.setTool`): selection, razor, ripple edit, rolling edit,
 *                slip, track select forward. As in Premiere, C only picks the razor (a
 *                click with it cuts there); ⌘K, Add Edit, cuts at the playhead (the
 *                selected shot, else the shot under it) without changing the tool.
 *   M            a marker at the playhead; ⇧M the next, ⌘⇧M the previous; ⌘⌥M clears the
 *                one at the playhead.
 *   Q W          the selected shot's in / out to the playhead.
 *   X ⌫ Del      ripple delete the selection (multi-select included); ⌫ Del only a
 *                selection somebody chose, never one the board adopted (I16.0 n).
 *   ⌘A  ⌘D  Esc  select every shot · duplicate the selection after itself · clear the
 *                selection and the marks.
 *   ⌘Z ⌘⇧Z       the foundation's — not bound twice here.
 *   ?            the map.
 *
 * ⌘ means ⌘ on a Mac and Ctrl elsewhere — in the bindings and in the map, which is
 * labelled through `tl.keyLabel`.
 *
 * What app.js used to do with `j` / `k` (move the card selection) is `↑` / `↓` now, and
 * its `x` is taken over so a multi-selection goes in one press. The board's static Keys
 * hint said "j/k move"; this file rewrites that one phrase in place so the panel does not
 * lie, since index.html is not this lane's to edit.
 *
 * Whole-clip playback on the board is the Find panel's `#findVideo` (a kept row has no
 * play of its own), so that is the clip the I / O marks belong to; the marks show as two
 * ticks on a small bar next to the transport's `#pos` line.
 */
(function () {
  'use strict';

  const MIN_LEN = 0.2;             // the foundation's shortest shot
  const BIG = 5;                   // frames — ⇧ with an arrow, as in Premiere
  const MAX_RATE = 8;

  const tl = window.tl;
  if (!tl) return;
  const L = (s) => (tl.keyLabel ? tl.keyLabel(s) : s);

  /* The one table. [keys, what, owner, group] — keys are space-separated tokens, each a
   * <kbd>, written with ⌘ ⌥ ⇧ and labelled for the platform (Ctrl / Alt / Shift off a
   * Mac); owner names the lane when it is not this one (so the map lists the whole
   * timeline and cannot drift from the file that binds the keys); group is the task the
   * overlay files it under (play, trim, move, bin). */
  const RAW_KEYS = [
    ['J K L', 'shuttle: L forward (again 2× 4× 8×), J back, K stop; hold K + L / J: 1×', '', 'play'],
    ['← →', 'one frame (⇧ five) — the playhead only', '', 'play'],
    ['↑ ↓ Home End', 'previous / next cut · start / end', '', 'move'],
    [', .', 'insert / overwrite at the playhead from the Source (else the bin’s clip)', '', 'bin'],
    ['I O', 'sequence in / out (a clip open in Find: its marks) · ⌥I ⌥O ⌘⇧X clear', '', 'bin'],
    ['↵', 'with a clip’s in and out: insert it after the selected shot', '', 'bin'],
    ['V C B N Y A ⌘K', 'tools: select, razor (click to cut), ripple, roll, slip, track forward · ⌘K cut at the playhead', '', 'trim'],
    ['⌥← ⌥→', 'trim the selected edit a frame (⇧ five), ⌘← ⌘→ too', 'trim', 'trim'],
    ['Q W', 'the selected shot’s in / out to the playhead', '', 'trim'],
    ['X ⇧Del', 'ripple delete the selection (⌫ Del: only a shot you chose)', '', 'trim'],
    ['⌘Z ⌘⇧Z', 'undo / redo', 'foundation', 'trim'],
    ['⌘A ⌘D Esc', 'select all · duplicate · clear the selection and marks', '', 'trim'],
    ['M ⇧M ⌘⇧M', 'marker at the playhead · next · previous (⌘⌥M clears)', '', 'move'],
    ['S', 'the magnet on / off', 'trim', 'trim'],
    ['+ − \\', 'zoom in / out · fit · ⌥ / ⌘ wheel zooms', 'foundation', 'move'],
    ['?', 'this map', '', 'move'],
  ];
  const KEYS = RAW_KEYS.map(([k, what, who, g]) => [L(k), L(what), who || undefined, g]);

  let H = {};                      // the hooks app.js handed tl.mount (segs, clips, cue, play, toast)
  let mounted = false;
  const sh = { rate: 0, raf: 0, last: 0, hold: false, kHeld: false, kl: false, kj: false };   // the shuttle
  const marks = { clip: null, match: null, in: null, out: null };
  let marksEl = null;

  const $ = (s) => document.querySelector(s);
  const round2 = (x) => Math.round(x * 100) / 100;
  const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
  const stem = (clip) => String(clip).replace(/\.[^.]+$/, '');
  const segs = () => (H.segs ? H.segs() : tl.state.segs);
  const clipOf = (name) => ((H.clips && H.clips()) || {})[name] || null;
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function say(msg) {
    if (H.toast) return H.toast(msg);
    if (typeof toast === 'function') return toast(msg);
    console.log(msg);
  }

  /* app.js's monitor, reached by name: `player`, `liveVideo`, `pauseCut`, `paintPos`,
   * `revealMonitor` are globals of the classic script that loads after this one. */
  const mon = () => (typeof player !== 'undefined' ? player : null);
  const live = () => (typeof liveVideo === 'function' ? liveVideo() : null);
  const pauseMon = () => { if (typeof pauseCut === 'function') pauseCut(); };
  const reveal = () => { if (typeof revealMonitor === 'function') revealMonitor(); };
  function paintAt(filmT, clipT, seg) {
    if (typeof paintPos === 'function') paintPos(filmT, clipT, seg);
    else tl.setPlayhead(filmT);
  }

  /* ------------------------------------------------------------ the shuttle */
  function setRate(r) {
    const p = mon();
    if (!p) return;
    for (const v of p.vids) {
      // load() resets playbackRate to the default, and arm() loads the next shot's
      // buffer while this one plays — so the rate has to be the default too, or the
      // hand-over at a cut would drop back to 1×. The buffer's shot has a rate of its
      // own (`dataset.speed`, set by arm()); the shuttle multiplies it.
      v.defaultPlaybackRate = r;
      v.playbackRate = r * (parseFloat(v.dataset.speed) || 1);
    }
  }

  function stopReverse() {
    cancelAnimationFrame(sh.raf);
    sh.raf = 0;
    sh.hold = false;
    if (sh.rate < 0) sh.rate = 0;
  }

  /* Park the monitor on shot i at clipT for the reverse drive. When that means opening
   * another proxy, cueAt() loads it with `#t=in` in the URL and parks on loadedmetadata —
   * so between readyState reaching 1 and that event the buffer reports the fragment's
   * in-point, not the park. The drive holds until the park has run (its listener was
   * registered first, so ours fires after it). */
  function cueForReverse(i, clipT) {
    if (!H.cue) return;
    H.cue(i, clipT);
    const v = live();
    if (!v || v.readyState >= 1) return;               // same proxy: parked synchronously
    sh.hold = true;
    const release = () => { sh.hold = false; };
    v.addEventListener('loadedmetadata', release, { once: true });
    v.addEventListener('error', () => { release(); stopReverse(); }, { once: true });
  }

  /* Anything that starts ordinary playback (space, a click on a block or the screen)
   * plays at 1×: the shuttle is a gesture, not a setting. */
  function settle() {
    stopReverse();
    if (sh.rate !== 0) { sh.rate = 0; setRate(1); }
  }

  function pause() {
    stopReverse();
    sh.rate = 0;
    setRate(1);
    pauseMon();
  }

  function startIndex() {
    const p = mon();
    const at = tl.shotAt(tl.state.playhead);
    if (p && p.idx >= 0 && at && p.idx === at.index) return p.idx;
    return at ? at.index : 0;
  }

  function forward(rate) {
    const p = mon();
    if (!p) return;
    stopReverse();
    sh.rate = rate;
    setRate(rate);
    if (!p.playing) {
      reveal();
      if (H.play) H.play(startIndex());
    }
  }

  function reverse(rate) {
    const p = mon();
    if (!p) return;
    sh.rate = rate;
    setRate(1);
    if (p.playing) pauseMon();
    else if (p.idx < 0) {
      const at = tl.shotAt(tl.state.playhead);
      if (at) cueForReverse(at.index, at.clipT);
    }
    if (!sh.raf) { sh.last = 0; sh.raf = requestAnimationFrame(revTick); }
  }

  /* J: the browser cannot play backwards, so this drives currentTime, frame by frame,
   * across the cut — at a shot's in-point it parks the monitor on the previous shot's
   * out-point and carries on from there. */
  function revTick(now) {
    if (sh.rate >= 0) { sh.raf = 0; return; }
    const p = mon();
    if (!p || p.playing) { stopReverse(); return; }   // something else started playing
    sh.raf = requestAnimationFrame(revTick);
    const dt = sh.last ? Math.min(0.1, (now - sh.last) / 1000) : 0;
    sh.last = now;
    const list = segs();
    const i = p.idx, seg = list[i], v = live();
    if (!seg || !v) { stopReverse(); return; }
    if (sh.hold || v.readyState < 1) return;           // still opening: wait for the park
    const spd = tl.speedOf(seg);                       // a 0.5× shot moves half a clip second per film second
    const t = v.currentTime + sh.rate * dt * spd;
    if (t <= seg.in + 1e-3) {
      if (i === 0) {
        v.currentTime = seg.in;
        paintAt(0, seg.in, seg);
        stopReverse();
        return;
      }
      const prev = list[i - 1];
      cueForReverse(i - 1, prev.out);                  // the drive carries on from there
      return;
    }
    v.currentTime = t;
    paintAt(tl.filmStart(seg.id) + (t - seg.in) / spd, t, seg);
  }

  function shuttleKey(k, e) {
    if (e.repeat) return true;                         // holding a key is not pressing it again
    const p = mon();
    if (!p || !p.vids.length || !segs().length) { say('nothing to play yet'); return true; }
    if (k === 'k') { sh.kHeld = true; pause(); return true; }
    if (k === 'l') {
      if (sh.kHeld) { forward(1); sh.kl = true; return true; }
      const v = live();
      // the shuttle's own rate — `defaultPlaybackRate`, not `playbackRate`, which
      // carries the shot's speed as well and would read a 0.5× shot at 1× as half
      const cur = p.playing ? (sh.rate > 0 ? sh.rate : (v && v.defaultPlaybackRate) || 1) : 0;
      forward(cur > 0 ? Math.min(MAX_RATE, cur * 2) : 1);
      return true;
    }
    if (sh.kHeld) { reverse(-1); sh.kj = true; return true; }
    reverse(sh.rate < 0 ? Math.max(-MAX_RATE, sh.rate * 2) : -1);
    return true;
  }

  function onKeyUp(e) {
    const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    if (k === 'k') {
      sh.kHeld = false;
      if (sh.kl || sh.kj) { sh.kl = sh.kj = false; pause(); }
    } else if (k === 'l' && sh.kl) { sh.kl = false; pause(); }
    else if (k === 'j' && sh.kj) { sh.kj = false; pause(); }
  }

  /* ------------------------------------------------------------ navigation */
  /* The playhead to a film time; the monitor parks there unless it is playing. */
  function park(t) {
    const p = mon();
    t = clamp(t, 0, tl.total());
    if (p && p.playing) tl.setPlayhead(t); else tl.seek(t);
  }

  function goTo(t, id) {
    settle();
    tl.seek(clamp(t, 0, tl.total()));
    if (id != null) tl.select([id]);
    return true;
  }

  function prevCut() {
    const list = segs();
    if (!list.length) return true;
    const ph = tl.state.playhead;
    const at = tl.shotAt(ph);
    const start = tl.filmStart(at.id);
    if (ph - start > 0.02) return goTo(start, at.id);   // this shot's head first
    const prev = list[at.index - 1];
    return prev ? goTo(tl.filmStart(prev.id), prev.id) : goTo(0, list[0].id);
  }

  function nextCut() {
    const list = segs();
    if (!list.length) return true;
    const at = tl.shotAt(tl.state.playhead);
    const next = list[at.index + 1];
    return next ? goTo(tl.filmStart(next.id), next.id) : goTo(tl.total(), at.id);
  }

  /* A frame step keeps the exact film time on the timeline; the monitor parks at the
   * nearest 0.01 s (the cue rounds clip times the way the EDL does), so without this a
   * run of frame steps would drift by the rounding. */
  function step(d) {
    if (!segs().length) return true;
    settle();
    const t = clamp(tl.state.playhead + d, 0, tl.total());
    tl.seek(t);
    tl.setPlayhead(t);
    return true;
  }

  /* ------------------------------------------------------------ marks on a clip */
  /* The clip the marks go on: the Find panel's player, once a match has opened it. */
  function clipPlayer() {
    const box = $('#findPlayer'), v = $('#findVideo');
    const m = typeof findSel !== 'undefined' ? findSel : null;
    if (!box || !v || !m || box.style.display === 'none') return null;
    return { v, m };
  }

  function ensureMarksEl() {
    if (marksEl) return marksEl;
    const pos = document.querySelector('.transport .pos');
    if (!pos) return null;
    marksEl = document.createElement('span');
    marksEl.id = 'tlMarks';
    marksEl.className = 'tl-marks';
    marksEl.hidden = true;
    marksEl.title = L('I / O marks on the clip in Find — ↵ inserts the range after the selected shot · ⌥I ⌥O clear');
    marksEl.innerHTML = '<i class="bar"><b class="range" hidden></b><b class="tick in" hidden></b>'
      + '<b class="tick out" hidden></b></i><span class="txt"></span>';
    pos.insertAdjacentElement('afterend', marksEl);
    return marksEl;
  }

  function clipDuration() {
    const cp = clipPlayer();
    if (cp && Number.isFinite(cp.v.duration) && cp.v.duration > 0) return cp.v.duration;
    const c = clipOf(marks.clip);
    if (c && c.duration) return c.duration;
    return (marks.match && marks.match.duration) || 0;
  }

  function paintMarks() {
    const el = ensureMarksEl();
    if (!el) return;
    const has = marks.in != null || marks.out != null;
    el.hidden = !has;
    if (!has) return;
    const dur = clipDuration();
    const pct = (t) => `${dur ? clamp(t / dur, 0, 1) * 100 : 0}%`;
    const tin = el.querySelector('.tick.in'), tout = el.querySelector('.tick.out');
    const range = el.querySelector('.range');
    tin.hidden = marks.in == null;
    tout.hidden = marks.out == null;
    if (marks.in != null) tin.style.left = pct(marks.in);
    if (marks.out != null) tout.style.left = pct(marks.out);
    range.hidden = !(marks.in != null && marks.out != null);
    if (!range.hidden) {
      range.style.left = pct(marks.in);
      range.style.width = `${dur ? clamp((marks.out - marks.in) / dur, 0, 1) * 100 : 0}%`;
    }
    const bits = [];
    bits.push(`${stem(marks.clip)}`);
    bits.push(`I ${marks.in != null ? tl.fmt(marks.in) : '—'}`);
    bits.push(`O ${marks.out != null ? tl.fmt(marks.out) : '—'}`);
    if (marks.in != null && marks.out != null) bits.push('↵ inserts');
    el.querySelector('.txt').textContent = bits.join(' · ');
  }

  function clearMarks(edge) {
    if (edge) marks[edge] = null;
    else { marks.in = marks.out = null; }
    if (marks.in == null && marks.out == null) { marks.clip = null; marks.match = null; }
    paintMarks();
  }

  function mark(edge) {
    const cp = clipPlayer();
    if (!cp) { say('mark on a clip — open one from the bin or Find'); return true; }
    const t = round2(cp.v.currentTime || 0);
    if (marks.clip !== cp.m.clip) { marks.in = marks.out = null; }
    marks.clip = cp.m.clip;
    marks.match = cp.m;
    marks[edge] = t;
    if (edge === 'in' && marks.out != null && marks.out <= t) marks.out = null;
    if (edge === 'out' && marks.in != null && marks.in >= t) marks.in = null;
    paintMarks();
    say(`${edge} at ${tl.fmt(t)} on ${stem(marks.clip)}`
      + (marks.in != null && marks.out != null ? ' — ↵ inserts it' : ''));
    return true;
  }

  function clearMark(edge) {
    if (marks[edge] == null) return true;
    clearMarks(edge);
    say(`${edge} mark cleared`);
    return true;
  }

  /* ↵ with both marks: a shot after the selected one (appended when nothing is), with
   * the clip's transcript line inside the range as its why. Without marks the key is
   * app.js's (play this shot only). */
  function insertMarked() {
    if (marks.in == null && marks.out == null) return false;
    const cp = clipPlayer();
    if (cp && cp.m.clip !== marks.clip) {
      say(`the marks were on ${stem(marks.clip)} — cleared; mark this clip again`);
      clearMarks();
      return true;
    }
    if (marks.in == null || marks.out == null) {
      say(`mark ${marks.in == null ? 'I' : 'O'} too — then ↵ inserts the range`);
      return true;
    }
    if (marks.out - marks.in < MIN_LEN) {
      say(`the range is shorter than ${MIN_LEN} s — move O later`);
      return true;
    }
    const clip = marks.clip;
    const c = clipOf(clip);
    const line = ((c && c.transcript) || []).find((u) => u.end > marks.in && u.start < marks.out);
    const why = (line && line.text) || (marks.match && marks.match.what) || `marked in ${stem(clip)}`;
    const after = tl.state.anchor;
    const id = tl.insert({ clip, in: marks.in, out: marks.out, why }, after == null ? null : after);
    if (id == null) { say('could not insert that'); return true; }
    say(`added ${stem(clip)} ${tl.fmt(marks.in)}–${tl.fmt(marks.out)}`
      + (after != null ? ' after the selected shot' : ' at the end'));
    clearMarks();
    return true;
  }

  /* ------------------------------------------------------------ editing */
  function inside(id, ph) {
    const seg = tl.byId(id);
    if (!seg) return false;
    const start = tl.filmStart(id);
    return ph >= start - 1e-6 && ph <= start + tl.dur(seg) + 1e-6;
  }

  /* A cut at the playhead — the selected shot, else the shot under it. Returns whether
   * it cut. */
  function razorAt() {
    const list = segs();
    if (!list.length) return false;
    const ph = tl.state.playhead;
    let id = tl.state.anchor;
    if (id != null) {
      const seg = tl.byId(id), start = tl.filmStart(id);
      if (!(ph > start && ph < start + tl.dur(seg))) id = null;
    }
    if (id == null) { const at = tl.shotAt(ph); id = at ? at.id : null; }
    if (id == null) return false;
    const second = tl.split(id, ph);
    if (second == null) { say(`too close to a cut point to split (${MIN_LEN} s minimum)`); return false; }
    tl.select([second, id]);         // both halves; the anchor stays on the first
    say(`split ${stem(tl.byId(id).clip)} at ${tl.tc(ph)}`);
    return true;
  }

  function razor() { razorAt(); return true; }

  function trimTo(edge) {
    const id = tl.state.anchor;
    const key = edge === 'in' ? 'Q' : 'W';
    if (id == null) { say(`select a shot — ${key} trims its ${edge} to the playhead`); return true; }
    const seg = tl.byId(id);
    const ph = tl.state.playhead;
    if (!inside(id, ph)) {
      say(`the playhead is outside the selected shot — ${key} trims its ${edge} to the playhead`);
      return true;
    }
    const clipT = round2(seg.in + (ph - tl.filmStart(id)) * tl.speedOf(seg));   // film → clip
    if (edge === 'in' && clipT > seg.out - MIN_LEN) {
      say(`too close to the out point — a shot keeps ${MIN_LEN} s`); return true;
    }
    if (edge === 'out' && clipT < seg.in + MIN_LEN) {
      say(`too close to the in point — a shot keeps ${MIN_LEN} s`); return true;
    }
    const changed = edge === 'in' ? tl.setRange(id, clipT, null) : tl.setRange(id, null, clipT);
    if (!changed) { say(`the ${edge} point is already at the playhead`); return true; }
    const now = tl.byId(id), start = tl.filmStart(id);
    park(edge === 'in' ? start : start + tl.dur(now));
    say(`${edge} → ${tl.fmt(clipT)} on ${stem(now.clip)}`);
    return true;
  }

  function rippleDelete() {
    if (removeOverlaySel()) return true;
    const ids = [...tl.state.sel];
    if (!ids.length) { say('nothing selected'); return true; }
    tl.remove(ids);                  // the foundation selects the shot that takes the place
    chosen = false;                  // …which nobody chose
    say(`removed ${ids.length} shot${ids.length > 1 ? 's' : ''} — ${L('⌘Z')} brings ${ids.length > 1 ? 'them' : 'it'} back`);
    return true;
  }

  /* ⌫ / Del take out only a shot somebody chose (I16.0 n) — a click on a block, a key
   * that selects (↑ ↓ ⌘A ⌘D C, Esc to clear) — never the one the board picked: the shot
   * playback adopts as the playhead crosses into it, the first shot at load, the one
   * handed on after a delete. On the pass ⌫ is "the previous moment", so a hand used to
   * it took the shot under the playhead out of the cut. X is unchanged. */
  let chosen = false;

  /* A clip on V2 (V3…, A3…) that is selected goes first: X, ⌫ and Del take it off its
   * track (nothing closes up — those tracks are not magnetic). */
  function removeOverlaySel() {
    const id = tl.state.osel;
    if (id == null || !tl.removeOverlay) return false;
    const o = tl.overlayById(id);
    if (tl.removeOverlay(id)) say(`removed ${o ? stem(o.clip) : 'the clip'} from ${(o && o.track) || 'V2'} — ${L('⌘Z')} brings it back`);
    return true;
  }

  function backspace() {
    if (removeOverlaySel()) return true;
    if (chosen) return rippleDelete();
    say(tl.state.sel.size ? 'click a shot to choose it — ⌫ takes out only a shot you selected'
      : 'nothing selected');
    return true;
  }

  function selectAll() {
    const list = segs();
    if (!list.length) return true;
    tl.select(list.map((s) => s.id));
    return true;
  }

  function duplicate() {
    const chosen = segs().filter((s) => tl.state.sel.has(s.id));
    if (!chosen.length) { say('nothing selected'); return true; }
    tl.begin('duplicate');
    let after = chosen[chosen.length - 1].id;
    const made = [];
    for (const s of chosen) {
      const copy = { clip: s.clip, in: s.in, out: s.out, why: s.why };
      if (s.speed != null) copy.speed = s.speed;       // a duplicate keeps its rate
      const id = tl.insert(copy, after);
      if (id != null) { made.push(id); after = id; }
    }
    tl.commit();
    tl.select(made);
    say(`duplicated ${made.length} shot${made.length > 1 ? 's' : ''}`);
    return true;
  }

  function clearAll() {
    tl.select([]);
    clearMarks();
    return true;
  }

  /* ------------------------------------------------------------ sequence marks: I O */
  /* Where I / O go (Premiere marks the panel in focus): the clip open in Find while it
   * is the source — it is open and the timeline was not the last thing clicked — else
   * the sequence, at the playhead. */
  let lastPanel = 'source';
  function sourceFocused() { return !!clipPlayer() && lastPanel !== 'timeline'; }

  function markKey(edge) {
    if (sourceFocused()) return mark(edge);
    if (!segs().length) { say('nothing on the timeline to mark'); return true; }
    const t = edge === 'in' ? tl.setSeqIn(tl.state.playhead) : tl.setSeqOut(tl.state.playhead);
    say(`sequence ${edge} at ${tl.tc(t)}`);
    return true;
  }

  function clearMarkKey(edge) {
    if (sourceFocused() || marks[edge] != null) return clearMark(edge);
    const had = edge === 'in' ? tl.state.seqIn : tl.state.seqOut;
    if (had == null) return true;
    if (edge === 'in') tl.setSeqIn(null); else tl.setSeqOut(null);
    say(`sequence ${edge} cleared`);
    return true;
  }

  function clearInOut() {
    if (tl.state.seqIn == null && tl.state.seqOut == null) return true;
    tl.setSeqIn(null); tl.setSeqOut(null);
    say('sequence in and out cleared');
    return true;
  }

  /* ------------------------------------------------------------ markers: M */
  function addMarker() {
    if (!segs().length) { say('nothing on the timeline to mark'); return true; }
    const m = tl.addMarker(tl.state.playhead);
    say(m == null ? `a marker is already at ${tl.tc(tl.state.playhead)}` : `marker at ${tl.tc(m)}`);
    return true;
  }

  function jumpMarker(dir) {
    const m = dir > 0 ? tl.nextMarker() : tl.prevMarker();
    if (m == null) {
      say(tl.markers().length ? `no ${dir > 0 ? 'next' : 'previous'} marker` : `no markers — M adds one at the playhead`);
      return true;
    }
    settle();
    tl.seek(m);
    tl.setPlayhead(m);
    return true;
  }

  function clearMarker() {
    if (tl.removeMarker(tl.state.playhead)) say('marker cleared');
    else say('no marker at the playhead');
    return true;
  }

  function clearAllMarkers() {
    if (tl.clearMarkers()) say('every marker cleared');
    return true;
  }

  /* ------------------------------------------------------------ tools: V C B N Y A */
  const TOOL_OF = { v: 'select', c: 'razor', b: 'ripple', n: 'roll', y: 'slip', a: 'track' };

  function toolKey(k) {
    const name = TOOL_OF[k];
    const changed = tl.setTool(name);
    // Premiere's C picks the razor and nothing else (verdict 5, punch 5): the click cuts
    if (k === 'c') {
      if (changed) say(`razor tool — click a shot to cut it there · ${L('⌘K')} cuts at the playhead · V for the selection tool`);
      return true;
    }
    if (changed) say(`${tl.TOOLS[name][2].split(' — ')[0].toLowerCase()} (${k.toUpperCase()})`);
    return true;
  }

  /* ⌘K: Premiere's Add Edit — a cut at the playhead, the tool unchanged. */
  function addEdit() { razor(); return true; }

  /* ------------------------------------------------------------ insert / overwrite: , . */
  /* The source an insert or overwrite takes, as `{clip, in, out, why, from}`: the
   * Source monitor (`window.rcSource`, the workspace's — its In / Out, else the whole
   * clip), then a clip open in Find with both marks, then the bin's selected keep (the
   * range its "+ add" puts in). null when there is none, or its clip is not usable. */
  function sourceShot() {
    const clips = (H.clips && H.clips()) || {};
    // any clip in the folder is usable, heard or not: the dock lists one the index has
    // not heard yet in the board's clips (empty transcript) — verdict 2, punch 7
    const ok = (clip) => !!clip && (!!clips[clip]
      || (typeof window.rcEnsureClip === 'function' && !!window.rcEnsureClip(clip) && !!clips[clip]));
    const src = window.rcSource;
    if (src && src.clip) {
      ok(src.clip);
      const c = clips[src.clip];
      const dur = (c && c.duration) || 0;
      const a = Number.isFinite(Number(src.inPoint)) && src.inPoint != null ? Number(src.inPoint) : 0;
      const b = Number.isFinite(Number(src.outPoint)) && src.outPoint != null ? Number(src.outPoint) : dur;
      if (ok(src.clip) && b - a >= MIN_LEN) {
        const line = ((c && c.transcript) || []).find((u) => u.end > a && u.start < b);
        return { clip: src.clip, in: round2(a), out: round2(b),
                 why: (line && line.text) || `from the Source monitor · ${stem(src.clip)}`, from: 'the Source monitor' };
      }
    }
    if (marks.clip && marks.in != null && marks.out != null && marks.out - marks.in >= MIN_LEN && ok(marks.clip)) {
      const c = clips[marks.clip];
      const line = ((c && c.transcript) || []).find((u) => u.end > marks.in && u.start < marks.out);
      return { clip: marks.clip, in: marks.in, out: marks.out,
               why: (line && line.text) || `marked in ${stem(marks.clip)}`, from: 'the marked clip' };
    }
    const row = document.querySelector('#library .keep.sel');
    const k = row && row._keep;
    if (k && !k.missing && ok(k.clip) && k.end - k.start >= MIN_LEN) {
      return { clip: k.clip, in: round2(k.start), out: round2(k.end),
               why: k.why || k.note || '', from: 'the bin' };
    }
    return null;
  }

  function editFromSource(kind) {
    const s = sourceShot();
    if (!s) {
      say(`select a clip in the bin (or open one in the Source monitor) — ${kind === 'insert' ? ',' : '.'} ${kind}s it at the playhead`);
      return true;
    }
    settle();
    const at = clamp(tl.state.playhead, 0, tl.total());
    const seg = { clip: s.clip, in: s.in, out: s.out, why: s.why };
    // The track targets (the headers): an upper video track (V2, V3…) targeted and V1 not
    // lays the clip over the cut there at the playhead — those tracks are not magnetic,
    // so insert and overwrite are the same. No video track targeted and an added audio
    // track (A3…) targeted: the clip's sound goes there.
    const tr = tl.tracks ? tl.tracks() : null;
    const names = tl.trackNames ? tl.trackNames() : ['V2', 'V1', 'A1', 'A2'];
    const upper = tr && !tr.V1.target
      ? names.filter((n) => n[0] === 'V' && n !== 'V1').reverse().find((n) => tr[n] && tr[n].target) : null;
    const sound = tr && !upper && !tr.V1.target
      ? names.find((n) => /^A[3-9]$/.test(n) && tr[n] && tr[n].target) : null;
    if (upper || sound) {
      const track = upper || sound;
      const oid = tl.addOverlay(seg, at, track);
      if (oid == null) { say(`could not lay that on ${track}`); return true; }
      tl.setPlayhead(clamp(at + tl.dur(seg), 0, tl.total()));
      say(`laid ${stem(s.clip)} ${tl.fmt(s.in)}–${tl.fmt(s.out)} on ${track} at ${tl.tc(at)} · from ${s.from}`);
      return true;
    }
    const landed = (id, where) => {
      if (id == null) { say(`could not ${kind} that`); return; }
      chosen = true;                 // the editor put it there
      const end = tl.filmStart(id) + tl.dur(tl.byId(id));
      tl.seek(clamp(end, 0, tl.total()));
      tl.setPlayhead(clamp(end, 0, tl.total()));
      say(`${kind === 'insert' ? 'inserted' : 'overwrote with'} ${stem(s.clip)} ${tl.fmt(s.in)}–${tl.fmt(s.out)} at ${tl.tc(where)} · from ${s.from}`);
    };
    // The playhead past the film's end (verdict 5, punch 8): the clip lands there, the
    // time between the end and it a gap on V1, as Premiere leaves one (tl.placeAt).
    const ph = Math.max(0, tl.state.playhead || 0);
    if (tl.placeAt && ph > tl.total() + tl.frame() / 2) {
      tl.placeAt(seg, ph, kind).then((id) => landed(id, ph));
      return true;
    }
    landed(kind === 'insert' ? tl.insertAt(seg, at) : tl.overwrite(seg, at), at);
    return true;
  }

  /* ------------------------------------------------------------ the map */
  function keysPanel() {
    for (const h of document.querySelectorAll('.panel h2')) {
      if (h.textContent.trim() === 'Keys') return h.parentElement;
    }
    return null;
  }

  function kbds(keys) {
    return keys.split(' ').map((k) => `<kbd>${esc(k)}</kbd>`).join(' ');
  }

  function renderMap() {
    const panel = keysPanel();
    if (!panel) return;
    let sec = panel.querySelector('#tlKeys');
    if (!sec) {
      sec = document.createElement('div');
      sec.id = 'tlKeys';
      sec.className = 'tl-keymap';
      panel.appendChild(sec);
    }
    sec.innerHTML = '<h3>Timeline</h3>' + KEYS.map(([k, what, who]) =>
      `<div class="row"><span class="k">${kbds(k)}</span><span class="w">${esc(what)}`
      + `${who ? ` <em>· ${esc(who)}</em>` : ''}</span></div>`).join('');
    // The board's own line still said j/k move the selection; that is ↑/↓ now.
    const hint = panel.querySelector(':scope > .hint');
    if (hint && hint.innerHTML.includes('<kbd>j</kbd>/<kbd>k</kbd> move')) {
      hint.innerHTML = hint.innerHTML.replace('<kbd>j</kbd>/<kbd>k</kbd> move', '<kbd>↑</kbd>/<kbd>↓</kbd> move');
    }
  }

  function showMap() {
    const panel = keysPanel();
    if (!panel) return true;
    renderMap();
    panel.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    panel.classList.remove('tl-flash');
    void panel.offsetWidth;          // restart the animation on a second ?
    panel.classList.add('tl-flash');
    setTimeout(() => panel.classList.remove('tl-flash'), 1300);
    return true;
  }

  /* ------------------------------------------------------------ the handler */
  function pickerOpen() {
    const p = $('#picker');
    return !!p && !p.hidden;
  }

  function onKey(e) {
    if (!mounted) return;
    const t = e.target;
    if (t && (['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName) || t.isContentEditable)) return;
    // a modal (the Export dialog) owns its keys: Enter / Space on its buttons are its own
    if (t && t.closest && t.closest('dialog[open]')) return;
    const mod = e.metaKey || e.ctrlKey;
    const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    let handled = false;
    if (mod && e.altKey) {
      if (e.code === 'KeyM') handled = e.shiftKey ? clearAllMarkers() : clearMarker();
    } else if (mod) {
      if (e.code === 'KeyM' && e.shiftKey) handled = jumpMarker(-1);
      else if (e.code === 'KeyX' && e.shiftKey) handled = clearInOut();
      else if (k === 'a' && !e.shiftKey) handled = selectAll();
      else if (k === 'd' && !e.shiftKey) handled = duplicate();
      else if (k === 'k' && !e.shiftKey) handled = addEdit();
      // ⌘Z / ⌘⇧Z are the foundation's; they are not bound twice
    } else if (e.altKey) {
      if (e.code === 'KeyI') handled = clearMarkKey('in');    // ⌥I is a dead key on a Mac: by code
      else if (e.code === 'KeyO') handled = clearMarkKey('out');
    } else {
      switch (k) {
        case 'j': case 'k': case 'l': handled = shuttleKey(k, e); break;
        case 'ArrowUp': handled = prevCut(); break;
        case 'ArrowDown': handled = nextCut(); break;
        case 'Home': handled = segs().length ? goTo(0, segs()[0].id) : true; break;
        case 'End': {
          const list = segs();
          handled = list.length ? goTo(tl.total(), list[list.length - 1].id) : true;
          break;
        }
        case 'ArrowLeft': handled = step(-(e.shiftKey ? BIG : 1) * tl.frame()); break;
        case 'ArrowRight': handled = step((e.shiftKey ? BIG : 1) * tl.frame()); break;
        case 'i': handled = markKey('in'); break;
        case 'o': handled = markKey('out'); break;
        case 'Enter': handled = insertMarked(); break;     // false: app.js plays this shot only
        case ',': handled = editFromSource('insert'); break;
        case '.': handled = editFromSource('overwrite'); break;
        case 'm': handled = e.shiftKey ? jumpMarker(1) : addMarker(); break;
        case 'v': case 'c': case 'b': case 'n': case 'y': case 'a':
          handled = e.shiftKey ? false : toolKey(k);
          break;
        case 'q': handled = trimTo('in'); break;
        case 'w': handled = trimTo('out'); break;
        case 'x': handled = rippleDelete(); break;
        case 'Delete': case 'Backspace': handled = backspace(); break;
        case 'Escape':                     // the switcher's picker and a trim drag in progress come first
          handled = (pickerOpen() || (window.tl && tl.trim && tl.trim.dragging)) ? false : clearAll();
          break;
        case '?': handled = showMap(); break;
        case ' ': settle(); break;                          // space is app.js's: play at 1×
        default: break;
      }
    }
    if (handled) { e.preventDefault(); e.stopImmediatePropagation(); }
  }

  /* ------------------------------------------------------------ mount */
  function init(hooks) {
    if (mounted) return;
    mounted = true;
    H = hooks || {};
    // On window, capture phase: before app.js's handler (document, bubble), before the
    // foundation's (document, bubble) and before the switcher's `o` / Escape (document,
    // capture, registered earlier — a document listener of ours would run after it).
    window.addEventListener('keydown', onKey, true);
    window.addEventListener('keyup', onKeyUp, true);
    window.addEventListener('blur', () => { sh.kHeld = sh.kl = sh.kj = false; });
    // a click that starts ordinary playback plays at 1× and stops a reverse shuttle
    for (const s of ['#playCut', '.screen', '#tl']) {
      const n = $(s);
      if (n) n.addEventListener('pointerdown', settle, true);
    }
    // whether the selection was chosen: anything but app.js's index being adopted, Next
    // landing on its target ('land' — flow.js / fx.focus) or the shot handed on after a
    // delete ('heir' — tl.remove, whoever removed); a click on a block that is already
    // selected changes nothing, so it emits nothing
    const BOARDS = new Set(['app', 'land', 'heir']);
    tl.on('select', (ev) => { chosen = !BOARDS.has(ev.source); });
    const lane = $('#tl');
    if (lane) {
      lane.addEventListener('click', (e) => {
        if (e.target.closest('.blk') && tl.state.sel.size) chosen = true;
      }, true);
    }
    // which panel I / O mark: the timeline's (and the program monitor's) or the source's
    document.addEventListener('pointerdown', (e) => {
      const t = e.target instanceof Element ? e.target : null;
      if (!t) return;
      if (t.closest('#tl, .screen, .transport')) lastPanel = 'timeline';
      else if (t.closest('#findPlayer, #findResults, #library, [data-panel=source], #sourceMonitor')) lastPanel = 'source';
    }, true);
    ensureMarksEl();
    renderMap();
  }

  // The foundation has no mounted event: wrap tl.mount (this file loads before app.js
  // calls it), and cover the case where it already ran.
  const origMount = tl.mount;
  tl.mount = function (selector, hooks) {
    const r = origMount.call(tl, selector, hooks);
    init(hooks);
    return r;
  };
  if (tl.el && tl.el.root) init({});

  window.tlKeys = { KEYS, marks, shuttle: sh, renderMap, clearMarks, sourceShot,
                    get lastPanel() { return lastPanel; } };
})();
