/* passreview.js: the pass's review, in Selects (wave 17 FOLDIN, Karl's K4 b).
 *
 * Karl, 2026-10-19: "The previous parts where you click on the top and can get to the
 * "pass" and stuff is still here... odd". He chose fold in, then remove: the pass was a
 * separate screen (/floor) that played each pick large and took one key for a verdict.
 * Its verdicts live here now, on the rows Selects already lists (app.js renderKept: the
 * editor's keeps and the first pass's picks, one list), in a bin's idiom: a row's ✓ and
 * ✗ (shown on the row under the pointer or selected), and its right-click menu —
 *
 *   a pick       Keep · Keep as hero · Reject · Later (Undecided, Restore) · Note… ·
 *                Open in Source · Show its takes · Keep this take, reject the others ·
 *                Reject (or Later for) this and the rest of the clip ·
 *                Reject the N picks shown… (a filter on, asks once) · Undo
 *   a keep       Make hero / Not a hero · Note… · Open in Source · Remove from Selects · Undo
 *
 * What the pass's four rules said, kept: a Keep is the pick's preview snapped outward to
 * sentence ends (boundaries.py's pads, as the pass had them) — or, when the pick is open
 * in the Source monitor and its In / Out were moved there, exactly those (Premiere's way
 * to trim before you keep); verdicts are ranges on clip time (POST /api/floor/verdict,
 * the same endpoint, the same EDL fields); a note goes with POST /api/floor/note; and
 * every verdict is undone from the menu (Undo, its range and note put back). A rejected
 * pick leaves the list and is listed under the "rejected" chip, where Restore brings it
 * back. Nothing here spends a model call.
 *
 * app.js draws the rows (keepRow) and hands over its lists (window.rcSelects); this owns
 * the verdicts, the menu and the note box. Test hooks: `sel-menu` (the menu, items by
 * `data-act`), `sel-note` (the note box), `.pv` on a row (✓ / ✗).
 */
(function () {
  'use strict';
  // boundaries.py's numbers, as the pass kept them, so a keep from here ends where it
  // ended there
  const PAD_HEAD = 0.25;
  const PAD_TAIL = 0.45;
  const NEAR_WORD = 0.30;

  const $ = (s, r) => (r || document).querySelector(s);
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const r2 = (x) => Math.round(Number(x) * 100) / 100;
  const stemOf = (c) => String(c || '').replace(/\.[^.]+$/, '');
  const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
  const at = (t) => {
    const v = Math.max(0, Number(t) || 0), m = Math.floor(v / 60);
    return `${m}:${(v - m * 60).toFixed(1).padStart(4, '0')}`;
  };
  const say = (msg, ms) => { if (typeof window.toast === 'function') window.toast(msg, ms || 3200); };
  const UNDO_LABEL = { pick: 'keep', reject: 'reject', later: 'later', clear: 'restore', hero: 'hero', note: 'note' };

  const R = { undo: [], busy: false, writes: 0, menu: null, note: null, ask: null };

  const sel = () => window.rcSelects || null;
  const picks = () => { const s = sel(); return s ? s.picks() : []; };
  function clipOf(clip) {
    const s = sel();
    return (s && s.clip(clip)) || {};
  }

  /* ------------------------------------------------------------ the keep's range */
  function utterances(clip) {
    return (clipOf(clip).transcript || [])
      .filter((u) => Number.isFinite(u.start) && Number.isFinite(u.end))
      .slice().sort((a, b) => a.start - b.start);
  }
  // Outward only: a start inside a line goes to the line's start (less a breath), an end
  // inside a line, or so soon after it that the cut would sound like it, to its end plus
  // the tail; a boundary already in silence stays.
  function snapStart(clip, t) {
    const u = utterances(clip).find((x) => x.start < t && t < x.end);
    return u ? Math.max(0, u.start - PAD_HEAD) : Math.max(0, t);
  }
  function snapEnd(clip, t, dur) {
    const u = utterances(clip).find((x) => x.start < t && t < x.end + NEAR_WORD);
    return Math.min(dur || Infinity, u ? u.end + PAD_TAIL : t);
  }
  /* What Keep keeps: the Source monitor's In / Out when the pick is open there and they
   * were moved off the pick's own range; else the pick's preview, snapped to sentences. */
  function keepRange(p) {
    const dur = Number(p.duration) || Number(clipOf(p.clip).duration) || 0;
    const src = window.rcSource;
    if (src && src.clip === p.clip && Number.isFinite(src.inPoint) && Number.isFinite(src.outPoint)) {
      const a = src.inPoint, b = src.outPoint;
      const moved = Math.abs(a - p.start) > 0.02 || Math.abs(b - p.end) > 0.02;
      if (moved && b - a >= 0.1 && a < p.end && b > p.start) return [r2(a), r2(b)];
    }
    const base = Array.isArray(p.preview) && p.preview.length === 2 ? p.preview : [p.start, p.end];
    let a = snapStart(p.clip, Number(base[0]));
    let b = snapEnd(p.clip, Number(base[1]), dur);
    a = Math.max(0, dur ? Math.min(a, dur) : a);
    b = Math.max(a + 0.1, dur ? Math.min(b, dur) : b);
    return [r2(a), r2(b)];
  }

  /* -------------------------------------------------------------- the writes */
  async function post(url, body) {
    try {
      const r = await fetch(url, {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
      return data;
    } finally {
      R.writes++;
    }
  }
  function vbody(s, kind, range, hero, note) {
    return { clip: s.clip, start: range[0], end: range[1], verdict: kind, hero: !!hero,
             why: s.why || '', witnesses: s.witnesses || [], tags: s.tags || [],
             note: note || '', source: 'selects' };
  }
  // what a row was before a verdict, so Undo can put it back
  function snapshot(s) {
    return { verdict: s.pick ? (s.verdict || null) : 'pick', hero: !!s.hero,
             note: String(s.note || ''), range: [r2(s.start), r2(s.end)] };
  }
  function push(label, items) {
    if (!items.length) return;
    R.undo.push({ label, items });
    if (R.undo.length > 100) R.undo.shift();
  }
  async function refresh() {
    try {
      if (typeof window.rcPicksStale === 'function') await window.rcPicksStale();
      if (typeof window.refreshBin === 'function') await window.refreshBin();
    } catch (e) { /* the next read will */ }
  }
  const what = (s) => `${stemOf(s.clip)} ${at(s.start)}–${at(s.end)}`;

  /* One verdict on one row. `kind`: pick | reject | later | clear; `hero` with pick. */
  async function verdict(s, kind, { hero = false, quiet = false } = {}) {
    if (!s || R.busy) return false;
    R.busy = true;
    try {
      const range = kind === 'pick' && s.pick ? keepRange(s) : [r2(s.start), r2(s.end)];
      const heroNow = kind === 'pick' && !!hero;
      await post('/api/floor/verdict', vbody(s, kind, range, heroNow, s.note));
      push(kind === 'pick' && hero ? 'hero' : kind, [{ s, prev: snapshot(s), sent: range }]);
      await refresh();
      if (!quiet) {
        say(kind === 'pick' ? `${heroNow ? 'hero' : 'kept'} · ${stemOf(s.clip)} ${at(range[0])}–${at(range[1])}`
          : kind === 'reject' ? `rejected · ${what(s)} — right-click › Undo, or more found › rejected restores it`
            : kind === 'later' ? `marked later · ${what(s)}`
              : `back on the list · ${what(s)}`);
      }
      return true;
    } catch (e) {
      say(`the verdict did not land: ${e.message || e}`, 6000);
      return false;
    } finally {
      R.busy = false;
    }
  }

  /* A keep's hero mark: the same keep, its range and note, hero or not. */
  async function hero(s, on) {
    if (!s || s.pick || R.busy) return false;
    R.busy = true;
    try {
      const range = [r2(s.start), r2(s.end)];
      // a keep merges into the keep it overlaps, hero if either was (selects.apply_verdict):
      // taking the mark off is the keep cleared, then kept again without it
      if (!on) await post('/api/floor/verdict', { clip: s.clip, start: range[0], end: range[1], verdict: 'clear' });
      await post('/api/floor/verdict', { ...vbody(s, 'pick', range, on, s.note), source: s.source || 'selects' });
      push('hero', [{ s, prev: snapshot(s), sent: range }]);
      await refresh();
      say(on ? `hero · ${what(s)}` : `not a hero · ${what(s)}`);
      return true;
    } catch (e) {
      say(`the hero mark did not land: ${e.message || e}`, 6000);
      return false;
    } finally {
      R.busy = false;
    }
  }

  /* Several picks, one verdict each, one Undo for the lot (the pass's ⇧X). */
  async function many(list, kind, why, done) {
    const items = [];
    if (R.busy) return 0;
    R.busy = true;
    try {
      for (const q of list) {
        const range = [r2(q.start), r2(q.end)];
        try {
          await post('/api/floor/verdict', { ...vbody(q, kind, range, false, q.note), why: why || q.why || '' });
        } catch (e) {
          say(`${kind} did not land: ${e.message || e}`, 6000);
          break;
        }
        items.push({ s: q, prev: snapshot(q), sent: range });
      }
      push(kind, items);
      await refresh();
      if (items.length && done) say(done(items.length), 4000);
      return items.length;
    } finally {
      R.busy = false;
    }
  }

  /* Undo the last verdict: each range cleared, then what was there before put back. */
  async function undo() {
    const entry = R.undo.pop();
    if (!entry) { say('no verdict to undo'); return false; }
    if (R.busy) { R.undo.push(entry); return false; }
    R.busy = true;
    try {
      for (const it of entry.items.slice().reverse()) {
        const s = it.s;
        if (entry.label === 'note') {
          // a note on an undecided pick made it a later (selects.note): undone, it is undecided
          if (it.prev.verdict) await post('/api/floor/note', { clip: s.clip, start: it.sent[0], end: it.sent[1], text: it.prev.note });
          else await post('/api/floor/verdict', { clip: s.clip, start: it.sent[0], end: it.sent[1], verdict: 'clear' });
          continue;
        }
        await post('/api/floor/verdict', { clip: s.clip, start: it.sent[0], end: it.sent[1], verdict: 'clear' });
        if (it.prev.verdict) {
          await post('/api/floor/verdict', vbody(s, it.prev.verdict, it.prev.range, it.prev.hero, it.prev.note));
        }
      }
      await refresh();
      say(`undone · ${UNDO_LABEL[entry.label] || entry.label}${entry.items.length > 1 ? ` ×${entry.items.length}` : ''}`);
      return true;
    } catch (e) {
      say(`undo did not land: ${e.message || e}`, 6000);
      return false;
    } finally {
      R.busy = false;
    }
  }

  /* --------------------------------------------------------- takes and clips */
  function takesOf(p) {
    if (!p || !p.take) return [];
    const ids = new Set([p.id].concat(p.take.others || []));
    return picks().filter((q) => ids.has(q.id)).sort((a, b) => a.start - b.start);
  }
  const undecided = (q) => !q.verdict;
  function restOf(p) {
    return picks().filter((q) => q.clip === p.clip && q.start >= p.start - 0.01
      && (q === p || q.id === p.id || undecided(q)) && q.verdict !== 'reject' && q.verdict !== 'pick')
      .sort((a, b) => a.start - b.start);
  }
  async function keepTake(p) {
    const others = takesOf(p).filter((q) => q.id !== p.id && undecided(q));
    const ok = await verdict(p, 'pick', { quiet: true });
    if (!ok) return;
    const kept = R.undo.pop();               // one Undo for the take and its rejected siblings
    const n = await many(others, 'reject', `other take of ${p.take.id}`);
    const last = n ? R.undo.pop() : null;
    push('pick', [...(kept ? kept.items : []), ...(last ? last.items : [])]);
    say(`kept take ${p.take.n} of ${p.take.of}${n ? ` · rejected ${plural(n, 'other take')}` : ''}`, 4000);
  }

  /* The picks the list shows now that a batch may take: undecided, under a filter. */
  function shownUndecided() {
    const s = sel();
    return (s ? s.shown() : []).filter((q) => q && q.pick && undecided(q));
  }

  /* ---------------------------------------------------------------- the menu */
  function menuEl() {
    if (R.menu) return R.menu;
    const m = document.createElement('div');
    m.id = 'selMenu';
    m.className = 'tl-ctx';
    m.setAttribute('role', 'menu');
    m.dataset.testid = 'sel-menu';
    m.hidden = true;
    document.body.appendChild(m);
    m.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-act]');
      if (!b || b.disabled) return;
      e.stopPropagation();
      run(b.dataset.act, b);
    });
    m.addEventListener('contextmenu', (e) => e.preventDefault());
    document.addEventListener('pointerdown', (e) => { if (!m.hidden && !m.contains(e.target)) closeMenu(); }, true);
    window.addEventListener('blur', closeMenu);
    R.menu = m;
    return m;
  }
  function closeMenu() {
    if (R.menu) R.menu.hidden = true;
    R.ask = null;
  }

  function items(s) {
    const out = [];
    const add = (act, label, opts = {}) => out.push({ act, label, ...opts });
    const sep = () => out.push(null);
    if (s.pick) {
      if (s.verdict === 'reject') {
        add('clear', 'Restore', { title: 'back on the list, undecided' });
      } else {
        add('pick', 'Keep', { k: '✓', title: 'keep it: its preview, snapped to the sentences (or the Source monitor\'s In and Out, when you moved them)' });
        add('hero', 'Keep as hero', { k: '★', title: 'keep it, and every cut made from Selects must use it' });
        add('reject', 'Reject', { k: '✗', title: 'out of Selects and off the markers; the rejected chip restores it' });
        if (s.verdict === 'later') add('clear', 'Undecided', { title: 'take the later mark off' });
        else add('later', 'Later', { title: 'not now: it stays on the list, marked later' });
      }
      sep();
      add('note', 'Note…');
      add('source', 'Open in Source', { title: 'its In and Out set; Mark In / Out (I / O) there, then Keep takes them' });
      const takes = takesOf(s);
      if (s.take && takes.length > 1) {
        sep();
        add('takes', `Show its takes (${takes.length})`, { title: 'the takes of the same moment, side by side in the list' });
        const others = takes.filter((q) => q.id !== s.id && undecided(q)).length;
        if (s.verdict !== 'reject') {
          add('keeptake', 'Keep this take, reject the others', { disabled: !others && s.verdict === 'pick',
            title: others ? `keep this one; ${plural(others, 'other undecided take')} rejected` : 'keep this one' });
        }
      }
      const rest = restOf(s);
      if (rest.length > 1 && s.verdict !== 'reject') {
        sep();
        add('restreject', `Reject this and the rest of ${stemOf(s.clip)} (${rest.length})`);
        add('restlater', `Later for this and the rest of ${stemOf(s.clip)} (${rest.length})`);
      }
      const shown = shownUndecided();
      const filtered = sel() && sel().filtered();
      if (filtered && shown.length > 1) {
        sep();
        const n = shown.length;
        add('batch', R.ask === 'batch' ? `Click again: reject ${plural(n, 'pick')}` : `Reject the ${n} picks shown…`,
          { title: `every undecided pick the filter shows: ${filtered}`, warn: R.ask === 'batch' });
      }
    } else {
      add(s.hero ? 'unhero' : 'mkhero', s.hero ? 'Not a hero' : 'Make hero', { k: '★' });
      add('note', 'Note…');
      add('source', 'Open in Source');
      add('remove', 'Remove from Selects', { title: 'the keep goes; a pick it covered comes back undecided' });
    }
    if (R.undo.length) {
      sep();
      const last = R.undo[R.undo.length - 1];
      add('undo', `Undo ${UNDO_LABEL[last.label] || last.label}${last.items.length > 1 ? ` ×${last.items.length}` : ''}`);
    }
    return out;
  }

  function paintMenu() {
    const m = menuEl();
    const s = m._s;
    if (!s) return;
    m.innerHTML = items(s).map((it) => (it === null ? '<hr>'
      : `<button type="button" role="menuitem" data-act="${it.act}"${it.disabled ? ' disabled' : ''}`
        + `${it.title ? ` title="${esc(it.title)}"` : ''}${it.warn ? ' class="warn"' : ''}>`
        + `${esc(it.label)}${it.k ? `<span class="k">${esc(it.k)}</span>` : ''}</button>`)).join('');
  }

  function menu(e, s, row) {
    if (!s || s.whole) return false;
    e.preventDefault();
    const m = menuEl();
    R.ask = null;
    m._s = s;
    m._row = row || null;
    if (row && typeof window.rcSelects === 'object' && window.rcSelects.select) window.rcSelects.select(s, row);
    paintMenu();
    m.hidden = false;
    const w = m.offsetWidth || 240, h = m.offsetHeight || 200;
    m.style.left = `${Math.max(4, Math.min(e.clientX, innerWidth - w - 4))}px`;
    m.style.top = `${Math.max(4, Math.min(e.clientY, innerHeight - h - 4))}px`;
    return true;
  }

  async function run(act, btn) {
    const m = menuEl();
    const s = m._s, row = m._row;
    if (!s) return;
    if (act === 'batch' && R.ask !== 'batch') { R.ask = 'batch'; paintMenu(); return; }
    closeMenu();
    if (act === 'pick') return verdict(s, 'pick');
    if (act === 'hero') return verdict(s, 'pick', { hero: true });
    if (act === 'reject' || act === 'later' || act === 'clear') return verdict(s, act);
    if (act === 'mkhero') return hero(s, true);
    if (act === 'unhero') return hero(s, false);
    if (act === 'remove') return verdict(s, 'clear', { quiet: true }).then((ok) => { if (ok) say(`removed from Selects · ${what(s)}`); });
    if (act === 'note') return openNote(s, row);
    if (act === 'source') { if (typeof window.openPickInSource === 'function') window.openPickInSource(s); return undefined; }
    if (act === 'takes') { if (sel()) sel().show({ chip: `take:${s.take.id}` }); return undefined; }
    if (act === 'keeptake') return keepTake(s);
    if (act === 'restreject' || act === 'restlater') {
      const kind = act === 'restreject' ? 'reject' : 'later';
      return many(restOf(s), kind, '', (n) => `${kind === 'reject' ? 'rejected' : 'marked later'} ${plural(n, 'pick')} in ${stemOf(s.clip)}`);
    }
    if (act === 'batch') {
      const words = sel() ? sel().filtered() : '';
      return many(shownUndecided(), 'reject', `filtered out: ${words}`,
        (n) => `rejected ${plural(n, 'pick')} · ${words} — right-click › Undo brings them back`);
    }
    if (act === 'undo') return undo();
    void btn;
    return undefined;
  }

  /* ------------------------------------------------------------- the note box */
  function openNote(s, row) {
    closeNote();
    const box = document.createElement('div');
    box.id = 'selNote';
    box.dataset.testid = 'sel-note';
    box.className = 'sel-note';
    box.innerHTML = `<div class="sn-what">${esc(what(s))}</div>`
      + '<input type="text" maxlength="600" spellcheck="false" placeholder="what you see — Enter keeps it, Esc cancels">';
    document.body.appendChild(box);
    const input = box.querySelector('input');
    input.value = String(s.note || '');
    const r = (row && row.isConnected ? row : document.querySelector('#library')).getBoundingClientRect();
    box.style.left = `${Math.max(4, Math.min(r.left + 24, innerWidth - 324))}px`;
    box.style.top = `${Math.max(4, Math.min(r.bottom + 2, innerHeight - 70))}px`;
    R.note = { box, s };
    input.addEventListener('keydown', (e) => {
      e.stopPropagation();                  // the timeline's keys are on window: typing is typing
      if (e.key === 'Enter') { e.preventDefault(); saveNote(input.value); }
      else if (e.key === 'Escape') { e.preventDefault(); closeNote(); }
    });
    input.addEventListener('keyup', (e) => e.stopPropagation());
    input.addEventListener('blur', () => { if (R.note && R.note.box === box) closeNote(); });
    input.focus();
    input.select();
  }
  function closeNote() {
    if (!R.note) return;
    const { box } = R.note;
    R.note = null;
    box.remove();
  }
  async function saveNote(text) {
    const n = R.note;
    if (!n) return;
    closeNote();
    const s = n.s;
    const range = [r2(s.start), r2(s.end)];
    try {
      await post('/api/floor/note', { clip: s.clip, start: range[0], end: range[1], text: String(text || '').trim() });
      push('note', [{ s, prev: snapshot(s), sent: range }]);
      await refresh();
      say(String(text || '').trim() ? `note kept · ${what(s)}${s.pick && !s.verdict ? ' (marked later)' : ''}` : `note cleared · ${what(s)}`);
    } catch (e) {
      say(`the note did not land: ${e.message || e}`, 6000);
    }
  }

  /* ---------------------------------------------------------------- wiring */
  const CSS = `
#library table.sel-list td.name .pv { flex: none; display: none; gap: 2px; margin-left: auto; }
#library table.sel-list tr.keep:hover .pv, #library table.sel-list tr.keep.sel .pv,
#library table.sel-list tr.keep:focus-within .pv { display: inline-flex; }
#library table.sel-list .pv button { padding: 0 5px; line-height: 15px; font-size: 11px; min-width: 20px; }
#library table.sel-list .pv button:hover { border-color: var(--accent); }
#library table.sel-list .who.later { color: var(--dim); border-color: var(--line-hi, #46464c); }
#library table.sel-list tr.keep.rejected td { opacity: .55; }
#library table.sel-list tr.keep.rejected td.use { opacity: 1; }
#library table.sel-list button.use.restore { padding: 0 6px; line-height: 15px; }
#selMenu button { display: flex; justify-content: space-between; gap: 18px; }
#selMenu button .k { color: var(--dim, #888); }
#selMenu button.warn { color: var(--warn, #e0b050); }
#selMenu hr { border: 0; border-top: 1px solid var(--line, #333337); margin: 4px 0; }
.sel-note { position: fixed; z-index: 95; width: 320px; padding: 6px 8px; background: var(--panel, #232327);
  border: 1px solid var(--line-hi, #46464c); border-radius: 4px; box-shadow: 0 8px 24px rgba(0,0,0,.5); }
.sel-note .sn-what { color: var(--dim, #94949c); font-size: 11px; margin-bottom: 4px; }
.sel-note input { width: 100%; box-sizing: border-box; font: inherit; font-size: 12px; padding: 4px 6px;
  background: var(--bg, #111112); color: var(--text, #dcdcdf); border: 1px solid var(--accent, #4a9df8); border-radius: 3px; }
`;

  function init() {
    if (!document.getElementById('passreview-css')) {
      const st = document.createElement('style');
      st.id = 'passreview-css';
      st.textContent = CSS;
      document.head.appendChild(st);
    }
    const lib = document.getElementById('library');
    if (!lib) return;
    lib.addEventListener('contextmenu', (e) => {
      const row = e.target.closest('tr.keep');
      if (row && row._keep) menu(e, row._keep, row);
    });
    // a row's ✓ / ✗, and a rejected pick's Restore
    lib.addEventListener('click', (e) => {
      const b = e.target.closest('.pv button[data-pv], button.use.restore');
      if (!b) return;
      const row = b.closest('tr.keep');
      if (!row || !row._keep) return;
      e.stopPropagation();
      b.blur();
      if (b.classList.contains('restore')) verdict(row._keep, 'clear');
      else verdict(row._keep, b.dataset.pv);
    });
  }

  // Esc closes the menu and is the menu's alone: on window, capture, registered as the
  // script runs — before the timeline's keys (timeline-keys.js mounts with the cut, after
  // the board's first reads), whose Esc would also clear the timeline's selection.
  window.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || !R.menu || R.menu.hidden) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    closeMenu();
  }, true);

  window.rcPassReview = { state: R, verdict, hero, undo, menu, closeMenu, keepRange, takesOf, restOf,
                          keepTake, many, openNote, saveNote };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
