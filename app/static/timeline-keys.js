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
 *   ↑ ↓          previous / next cut: the playhead to the boundary, that shot selected.
 *   Home End     the film's start / end.
 *   ← →          one frame (1/30 s); ⇧ one second. The playhead only — the trim lane's
 *                `,` `.` are the edge nudges, so the two never collide.
 *   I O          mark in / out on the clip in Find while it plays; ↵ inserts the range as
 *                a shot after the selected one, with the clip's transcript line as why;
 *                ⌥I ⌥O clear. Playing the cut: a toast says to open a clip.
 *   Esc          clear the marks.
 *   ⌘Z ⌘⇧Z       the foundation's — not bound twice here.
 *   ?            the map.
 *
 * What app.js used to do with `j` / `k` (move the card selection) is `↑` / `↓` now. The
 * board's static Keys hint said "j/k move"; this file rewrites that one phrase in place so
 * the panel does not lie, since index.html is not this lane's to edit.
 *
 * Whole-clip playback on the board is the Find panel's `#findVideo` (a kept row has no
 * play of its own), so that is the clip the I / O marks belong to; the marks show as two
 * ticks on a small bar next to the transport's `#pos` line.
 */
(function () {
  'use strict';

  const FRAME = 1 / 30;
  const MIN_LEN = 0.2;             // the foundation's shortest shot
  const MAX_RATE = 8;

  /* The one table. [keys, what, owner] — keys are space-separated tokens, each a <kbd>;
   * owner names the lane when it is not this one (so the map lists the whole timeline
   * and cannot drift from the file that binds the keys). */
  const KEYS = [
    ['J K L', 'shuttle — L forward, again for 2× 4× 8×; J reverse, again stacks; K pause. Hold K and tap L or J to play at 1× while held'],
    ['↑ ↓', 'previous / next cut — the playhead to the boundary, that shot selected'],
    ['Home End', 'film start / end'],
    ['← →', 'one frame (⇧ one second) — the playhead only'],
    ['I O', 'mark in / out on the clip in Find while it plays · ⌥I ⌥O clear'],
    ['↵', 'with both marks: insert that range as a shot after the selected one'],
    ['⌘Z ⌘⇧Z', 'undo / redo', 'foundation'],
    ['Esc', 'clear the marks'],
    [', .', 'nudge the active edge a frame (⇧ a second)', 'trim'],
    ['S', 'the magnet on / off', 'trim'],
    ['+ − \\', 'zoom in / out · fit', 'foundation'],
    ['?', 'this map'],
  ];

  const tl = window.tl;
  if (!tl) return;

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
      // hand-over at a cut would drop back to 1×.
      v.defaultPlaybackRate = r;
      v.playbackRate = r;
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
    const t = v.currentTime + sh.rate * dt;
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
    paintAt(tl.filmStart(seg.id) + (t - seg.in), t, seg);
  }

  function shuttleKey(k, e) {
    if (e.repeat) return true;                         // holding a key is not pressing it again
    const p = mon();
    if (!p || !p.vids.length || !segs().length) { say('nothing to play yet'); return true; }
    if (k === 'k') { sh.kHeld = true; pause(); return true; }
    if (k === 'l') {
      if (sh.kHeld) { forward(1); sh.kl = true; return true; }
      const v = live();
      const cur = p.playing ? (sh.rate > 0 ? sh.rate : (v && v.playbackRate) || 1) : 0;
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
    marksEl.title = 'I / O marks on the clip in Find — ↵ inserts the range after the selected shot · ⌥I ⌥O clear';
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

  /* ------------------------------------------------------------ marks, cleared */
  function clearAll() {
    clearMarks();
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
    const mod = e.metaKey || e.ctrlKey;
    const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    let handled = false;
    if (mod && !e.altKey) {
      // ⌘Z / ⌘⇧Z are the foundation's; they are not bound twice
    } else if (e.altKey && !mod) {
      if (e.code === 'KeyI') handled = clearMark('in');       // ⌥I is a dead key on a Mac: by code
      else if (e.code === 'KeyO') handled = clearMark('out');
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
        case 'ArrowLeft': handled = step(-(e.shiftKey ? 1 : FRAME)); break;
        case 'ArrowRight': handled = step(e.shiftKey ? 1 : FRAME); break;
        case 'i': handled = mark('in'); break;
        // `o` is the project switcher's (open the picker) until a clip is open in Find;
        // then it is the out mark, and the switcher does not see it
        case 'o': handled = clipPlayer() ? mark('out') : false; break;
        case 'Enter': handled = insertMarked(); break;     // false: app.js plays this shot only
        case 'Escape': handled = pickerOpen() ? false : clearAll(); break;   // the switcher closes its picker first
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

  window.tlKeys = { KEYS, marks, shuttle: sh, renderMap, clearMarks };
})();
