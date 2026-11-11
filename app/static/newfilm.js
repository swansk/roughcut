/* newfilm.js: start a film like a person (wave 24 START, area 5 of the Copper editors).
 *
 * The first-day editor looked in File (no New), in Help (nothing) and in the tour (nothing),
 * and found the only way in by clicking the title: a text field under "BINS" that wanted a
 * typed path (COLD-01, -02, -03, -04). So:
 *
 *   File › New film from a folder…   (also on the empty-timeline card, in Help, and the
 *                                      title menu)
 *   opens a dialog with a folder browser the board serves (GET /api/folders: folders only,
 *   each with its count of video files, under the places the board already reads) and a path
 *   field that still takes a typed or pasted path. "Make the film" re-points the board at
 *   it (the switcher's openBin: the same POST /api/projects/open) and lands on the next
 *   step, the Run first pass panel, where the length is asked.
 *
 * and `rcLength.open(anchor)`: the length choice behind "aims for 2–3 min" wherever it is
 * printed (the Timeline's empty card, the Assistant), POST /api/target.
 *
 * What a newcomer reads says "film" and "footage folder"; "bin" stays only in a tooltip and
 * in the Open menu where an editor expects it.
 */
(function () {
  'use strict';
  const $ = (sel, root) => (root || document).querySelector(sel);
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
  const NF = { dlg: null, dir: null, busy: false, msg: '', bad: false, free: false, seq: 0 };

  async function getJSON(url) {
    const r = await fetch(url);
    let body = null;
    try { body = await r.json(); } catch (e) { body = null; }
    if (!r.ok) {
      const err = new Error((body && typeof body.detail === 'string') ? body.detail : `HTTP ${r.status}`);
      err.status = r.status;
      throw err;
    }
    return body;
  }

  function mount() {
    if (NF.dlg) return NF.dlg;
    const d = document.createElement('dialog');
    d.id = 'newFilm';
    d.className = 'nf';
    d.setAttribute('data-testid', 'newfilm-dialog');
    d.setAttribute('aria-label', 'New film from a folder');
    d.innerHTML = `
      <form method="dialog" class="nf-form" autocomplete="off">
        <h2>New film from a folder</h2>
        <p class="nf-lead">Pick the folder on this computer that holds your video clips. Roughcut makes a
          new film from it. Your clips stay where they are; nothing is copied or changed.</p>
        <div class="nf-pathrow">
          <input type="text" class="nf-path" data-testid="newfilm-path" spellcheck="false"
                 placeholder="type or paste a folder's path, or pick one below" aria-label="footage folder">
          <button type="button" class="nf-go-path" data-testid="newfilm-look" title="Look in the folder you typed">Look</button>
        </div>
        <div class="nf-here" data-testid="newfilm-here"></div>
        <div class="nf-list" data-testid="newfilm-list" role="listbox" aria-label="folders" tabindex="0"></div>
        <div class="nf-msg" data-testid="newfilm-msg" role="status"></div>
        <div class="nf-foot">
          <button type="button" class="nf-make" data-testid="newfilm-make" disabled>Make the film</button>
          <button type="button" class="nf-cancel" data-testid="newfilm-cancel">Cancel</button>
          <span class="nf-hint">Next: Roughcut asks how long the film should be, then makes a first cut.</span>
        </div>
      </form>`;
    document.body.appendChild(d);
    NF.dlg = d;
    $('.nf-cancel', d).addEventListener('click', () => d.close());
    $('.nf-make', d).addEventListener('click', make);
    $('.nf-go-path', d).addEventListener('click', () => look($('.nf-path', d).value));
    const path = $('.nf-path', d);
    path.addEventListener('input', () => { NF.free = true; setMake(); });
    $('.nf-list', d).addEventListener('click', (e) => {
      const row = e.target.closest('[data-path]');
      if (row) look(row.dataset.path);
    });
    d.addEventListener('cancel', () => { NF.seq++; });
    return d;
  }

  function say(text, bad) {
    NF.msg = text || '';
    NF.bad = !!bad;
    const m = $('.nf-msg', NF.dlg);
    m.textContent = NF.msg;
    m.classList.toggle('bad', NF.bad);
  }

  function setMake() {
    const btn = $('.nf-make', NF.dlg);
    const typed = $('.nf-path', NF.dlg).value.trim();
    const dir = NF.dir;
    // a listed folder with video, or a typed path the browser could not list (it may still open)
    const ok = !NF.busy && !!typed && ((dir && dir.path === typed && dir.videos > 0) || NF.free);
    btn.disabled = !ok;
    if (dir && dir.path === typed) {
      btn.textContent = dir.videos > 0 ? `Make a film from ${dir.name} (${plural(dir.videos, 'video')})` : 'Make the film';
    } else {
      btn.textContent = 'Make the film';
    }
  }

  function paintDir(d) {
    NF.dir = d;
    NF.free = false;
    const here = $('.nf-here', NF.dlg);
    const crumbs = [];
    const roots = d.roots || [];
    here.innerHTML = '<span class="nf-places">Places:</span> '
      + roots.map((r) => `<button type="button" class="nf-place" data-path="${esc(r.path)}" title="${esc(r.path)}">${esc(r.name)}</button>`).join(' ')
      + `<div class="nf-where"><span title="${esc(d.path)}">${esc(d.path)}</span> · `
      + (d.videos > 0 ? `<b>${plural(d.videos, 'video file')} here</b>` : 'no video files directly in this folder')
      + '</div>';
    here.querySelectorAll('.nf-place').forEach((b) => b.addEventListener('click', () => look(b.dataset.path)));
    const rows = [];
    if (d.parent) rows.push(`<div class="nf-row nf-up" role="option" tabindex="0" data-path="${esc(d.parent)}"><span class="nf-ico">↑</span><b>Up one level</b></div>`);
    d.folders.forEach((f) => rows.push(
      `<div class="nf-row${f.videos ? ' has' : ''}${f.path === d.current ? ' now' : ''}" role="option" tabindex="0" data-path="${esc(f.path)}" title="${esc(f.path)}">`
      + `<span class="nf-ico">▸</span><b>${esc(f.name)}</b>`
      + `<span class="nf-n">${f.videos ? plural(f.videos, 'video') : 'no videos'}</span></div>`));
    if (!d.folders.length) rows.push('<div class="nf-empty">No folders inside this one.</div>');
    if (d.truncated) rows.push('<div class="nf-empty">Only the first folders are listed: type the path of the one you want.</div>');
    $('.nf-list', NF.dlg).innerHTML = rows.join('');
    $('.nf-path', NF.dlg).value = d.path;
    say(d.videos > 0 ? '' : 'Pick the folder that holds the clips: it shows how many videos each one has.');
    setMake();
    void crumbs;
  }

  async function look(path) {
    const seq = ++NF.seq;
    const p = String(path || '').trim();
    say('looking…');
    try {
      const d = await getJSON(`/api/folders${p ? `?path=${encodeURIComponent(p)}` : ''}`);
      if (seq !== NF.seq) return;
      paintDir(d);
    } catch (e) {
      if (seq !== NF.seq) return;
      NF.dir = null;
      if (e.status === 403) {
        // not a place the browser lists: typing its path still opens it (the server decides)
        NF.free = true;
        say(`${e.message}`, false);
      } else {
        NF.free = false;
        say(e.message, true);
      }
      setMake();
    }
  }

  async function make() {
    const path = $('.nf-path', NF.dlg).value.trim();
    if (!path || NF.busy) return;
    NF.busy = true;
    setMake();
    say('making the film…');
    try {
      const sw = window.switcher;
      if (!sw || typeof sw.openBin !== 'function') throw new Error('the film switcher is not loaded');
      // the board reloads itself on the new film: the next step is remembered across it
      try { sessionStorage.setItem(NEXT_KEY, '1'); } catch (e) { /* private window: no next step */ }
      const res = await sw.openBin(path, { land: false });
      if (!res || !res.ok) {
        try { sessionStorage.removeItem(NEXT_KEY); } catch (e) { /* fine */ }
        say((res && res.error) || 'could not make the film', true);
        return;
      }
      NF.dlg.close();
      // the board reloaded onto the new film (the note is read at load); a page that re-points in place goes on here
      if (typeof window.roughcutReopen === 'function') next();
    } finally {
      NF.busy = false;
      if (NF.dlg.open) setMake();
    }
  }

  /* The next step after a new film is the first pass: its panel opens, asking how long. The
   * page reloads onto the new film (the switcher), so this also runs at load, once, when the
   * move left its note in sessionStorage. */
  const NEXT_KEY = 'rc.newfilm.next';
  async function next() {
    try { sessionStorage.removeItem(NEXT_KEY); } catch (e) { /* fine */ }
    const t0 = Date.now();
    while (!(window.rcFirstPass && window.rcFirstPass.state()) && Date.now() - t0 < 20000) {
      await new Promise((r) => setTimeout(r, 200));
    }
    if (window.rcFirstPass) window.rcFirstPass.open();
  }
  try { if (sessionStorage.getItem(NEXT_KEY)) setTimeout(next, 0); } catch (e) { /* private window */ }

  function open() {
    const d = mount();
    NF.dir = null;
    NF.free = false;
    $('.nf-list', d).innerHTML = '';
    $('.nf-here', d).innerHTML = '';
    $('.nf-path', d).value = '';
    say('');
    setMake();
    if (typeof d.showModal === 'function' && !d.open) d.showModal();
    look('');
    $('.nf-path', d).focus();
  }

  /* Keys while the dialog is open. On window, capture, registered at load: before the dock's and
   * the timeline's, which take Space, the arrows, I / O and Escape (a timeline Escape once kept
   * the Export dialog open). Typing still types (only the board's listeners are stopped);
   * Escape keeps its default, which closes the dialog. */
  window.addEventListener('keydown', (e) => {
    const d = NF.dlg;
    if (!d || !d.open) return;               // a modal: every key is its own, whatever has the focus
    e.stopImmediatePropagation();
    if (e.key === 'Escape' && !d.contains(e.target)) { e.preventDefault(); d.close(); return; }
    if (e.key === 'Enter' && e.target.classList && e.target.classList.contains('nf-path')) {
      e.preventDefault();
      look(e.target.value);
    } else if ((e.key === 'Enter' || e.key === ' ') && e.target.closest && e.target.closest('.nf-row[data-path]')) {
      e.preventDefault();
      look(e.target.closest('.nf-row').dataset.path);
    } else if (e.key === 'Enter' && e.target.tagName !== 'BUTTON') {
      e.preventDefault();
    }
  }, true);
  window.addEventListener('keyup', (e) => {
    if (NF.dlg && NF.dlg.open) e.stopImmediatePropagation();
  }, true);

  window.rcNewFilm = { open, look, state: () => NF };

  /* ------------------------------------------------------------ menus
   * dock.js runs the menu bar; these three commands are this module's. A click on an item
   * with a command dock.js does not know is a no-op there, and lands here. */
  document.addEventListener('click', (e) => {
    const b = e.target.closest && e.target.closest('#menubar [data-cmd]');
    if (!b) return;
    if (b.dataset.cmd === 'newfilm') { e.preventDefault(); open(); }
    else if (b.dataset.cmd === 'fpexplain') {
      e.preventDefault();
      if (window.rcFirstPass) { window.rcFirstPass.open(); }
    }
  });

  /* ------------------------------------------------------------ the length
   * "aims for 2–3 min" is the project's target every first cut and Ask is written to. Said
   * beside the buttons that send it, it is also the way to change it (COLD-09, CLUMSY-02). */
  const LEN = { el: null, anchor: null };
  const LENGTHS = [[30, '30 s'], [60, '1 min'], [120, '2 min'], [180, '3 min']];

  function closeLen() {
    if (LEN.el) LEN.el.remove();
    LEN.el = null;
    document.removeEventListener('pointerdown', outside, true);
  }
  function outside(e) { if (LEN.el && !LEN.el.contains(e.target) && e.target !== LEN.anchor) closeLen(); }

  async function setLen(seconds, errEl) {
    try {
      const r = await fetch('/api/target', { method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ seconds }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
      if (typeof window.rcAimsChanged === 'function') window.rcAimsChanged(d);
      if (window.rcFirstPass) window.rcFirstPass.refresh();
      closeLen();
      if (typeof window.toast === 'function') window.toast(`the film now aims for ${d.aims}`, 3000);
    } catch (e) {
      if (errEl) errEl.textContent = String(e.message || e);
    }
  }

  function openLen(anchor) {
    closeLen();
    const el = document.createElement('div');
    el.className = 'nf-len';
    el.setAttribute('data-testid', 'length-menu');
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-label', 'how long should the film be');
    el.innerHTML = '<div class="nf-len-q">How long should the film be?</div><div class="nf-len-chips">'
      + LENGTHS.map(([s, t]) => `<button type="button" data-sec="${s}" data-testid="length-${s}">${t}</button>`).join('')
      + '</div><div class="nf-len-type"><input type="text" placeholder="or type: 60, 1:30, 2 min" aria-label="film length, typed" '
      + 'data-testid="length-typed" size="12" spellcheck="false"><button type="button" class="nf-len-set">Set</button></div>'
      + '<div class="nf-len-err bad" role="status"></div>'
      + '<div class="nf-len-note">The first cut and every Ask after it are written to this length.</div>';
    document.body.appendChild(el);
    LEN.el = el;
    LEN.anchor = anchor;
    const r = anchor.getBoundingClientRect();
    el.style.left = `${Math.max(8, Math.min(window.innerWidth - 270, Math.round(r.left)))}px`;
    el.style.top = `${Math.round(Math.min(window.innerHeight - 150, r.bottom + 4))}px`;
    const err = $('.nf-len-err', el);
    el.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-sec]');
      if (b) setLen(Number(b.dataset.sec), err);
    });
    const typed = $('input', el);
    const go = () => {
      const sec = window.rcFirstPass && window.rcFirstPass.parseLen ? window.rcFirstPass.parseLen(typed.value) : null;
      if (sec == null) { err.textContent = 'Type a length from 10 seconds to 30 minutes, like 60, 90 s, 1:30 or 2 min.'; return; }
      setLen(sec, err);
    };
    $('.nf-len-set', el).addEventListener('click', go);
    typed.addEventListener('keydown', (e) => {
      e.stopPropagation();
      if (e.key === 'Enter') { e.preventDefault(); go(); }
      if (e.key === 'Escape') closeLen();
    });
    document.addEventListener('pointerdown', outside, true);
    typed.focus();
  }

  window.rcLength = { open: openLen, close: closeLen };
  document.addEventListener('click', (e) => {
    const a = e.target.closest && e.target.closest('[data-aims]');
    if (!a) return;
    e.preventDefault();
    openLen(a);
  });
})();
