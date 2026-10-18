/* import.js: footage in, from anywhere (REDESIGN-GOAL dimension 13, Karl's K3 b).
 *
 * Karl, 2026-10-16: "there is no clear way to add new footage." Premiere's ways in, on a
 * board that runs on one machine (foxtrot) and is opened from another (a laptop):
 *
 *   File › Import… (Ctrl+I / ⌘I)   the Import dialog: the board machine's folders (a dense
 *   File › Import from Media        file list — Name, Size, Date Modified — with a path bar,
 *        Browser…                   back / forward / up and the places to start from), and
 *                                   "Upload from this computer" for files on the viewer's
 *                                   own disk (the browser's file picker: files or a folder)
 *   the Project panel's Import, its  the same dialog: dock.js (BINLOOK), the empty states
 *        empty states, the walk-     and the walk-through (tour.js, TEACH: `[data-rc-import]`)
 *        through                    call `rcImport.open()`
 *   files or a folder dragged from  "Drop to import 3 files" over the Project panel; the
 *        the OS                     drop uploads them, with progress at the panel's foot
 *                                   (and in the header's job row, a server job); a drop on
 *                                   the Timeline also puts them in the sequence there
 *
 * Files on foxtrot are referenced where they are (POST /api/import, never copied);
 * uploads stream to the bin's --work media dir one file at a time (PUT
 * /api/upload/{batch}/{i}: XHR, so the browser streams the File and reports progress).
 * Each landed clip fires `rc:clips-changed` ({added: [names]}) — the Project panel
 * reloads on it — and the server starts the free work (poster, facts, previews, the
 * free index); the priced look is only ever offered (Run first pass).
 *
 * Keys in the browser: ↑ ↓ Home End PageUp PageDown move, Shift extends, Ctrl+A selects
 * every clip, Space toggles, Enter opens a folder or imports, Backspace / Alt+↑ goes up,
 * Alt+← / Alt+→ back and forward, a letter jumps to a name, Esc closes.
 */
(function () {
  'use strict';
  const IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform || '');
  const KEY = IS_MAC ? '⌘I' : 'Ctrl+I';
  const VIDEO = /\.(mp4|mov|mkv|avi|m4v|mts|webm)$/i;
  const LAST = 'rc.import.path';
  const S = {
    dlg: null, mode: 'import', data: null, path: '', back: [], fwd: [], sel: new Set(),
    anchor: -1, cursor: -1, busy: false, err: '', host: '', type: '', typeAt: 0, seq: 0,
    queue: [], up: null, strip: null, hideT: 0,
  };

  const $ = (sel, root) => (root || document).querySelector(sel);
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
  function bytes(n) {
    let v = Number(n) || 0;
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return i < 2 ? `${Math.round(v)} ${u[i]}` : `${v.toFixed(v < 10 ? 2 : 1)} ${u[i]}`;
  }
  function when(t) {
    if (!t) return '';
    const d = new Date(Number(t) * 1000);
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }
  function left(s) {
    if (s == null || !isFinite(s)) return '';
    if (s < 45) return `~${Math.max(5, Math.round(s / 5) * 5)} s left`;
    if (s < 90) return '~1 min left';
    return `~${Math.round(s / 60)} min left`;
  }
  function say(msg, ms) { if (typeof window.toast === 'function') window.toast(msg, ms || 4000); }
  function remember(p) { try { localStorage.setItem(LAST, p); } catch (_) { /* private window */ } }
  function remembered() { try { return localStorage.getItem(LAST) || ''; } catch (_) { return ''; } }

  const ICON = {
    dir: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M1.5 4.2c0-.7.5-1.2 1.2-1.2h3.4l1.4 1.5h5.8c.7 0 1.2.5 1.2 1.2v6.6c0 .7-.5 1.2-1.2 1.2H2.7c-.7 0-1.2-.5-1.2-1.2z" fill="currentColor" opacity=".9"/></svg>',
    video: '<svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.3"><rect x="1.5" y="3" width="13" height="10" rx="1.2"/><path d="M1.5 6h13M5 3v3M8 3v3M11 3v3"/></svg>',
    bin: '<svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.3"><path d="M2 5h12v8H2zM1.5 3h13v2h-13z"/></svg>',
    home: '<svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.3"><path d="M2 7.5L8 2.5l6 5M3.5 6.5V13.5h9V6.5"/></svg>',
    drive: '<svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.3"><rect x="1.5" y="5" width="13" height="6" rx="1.2"/><path d="M11.5 8h1"/></svg>',
    folder: '<svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.3"><path d="M1.5 4.2c0-.7.5-1.2 1.2-1.2h3.4l1.4 1.5h5.8c.7 0 1.2.5 1.2 1.2v6.6c0 .7-.5 1.2-1.2 1.2H2.7c-.7 0-1.2-.5-1.2-1.2z"/></svg>',
    up: '<svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M8 13V3M4 7l4-4 4 4"/></svg>',
    upload: '<svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M8 10.5V2.5M4.8 5.5L8 2.3l3.2 3.2M2.5 10v3h11v-3"/></svg>',
    import: '<svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2.5v8M4.8 7.3L8 10.5l3.2-3.2M2.5 10v3h11v-3"/></svg>',
  };

  /* ================================================================ the dialog */

  function build() {
    if (S.dlg) return S.dlg;
    const d = document.createElement('dialog');
    d.id = 'importDialog';
    d.dataset.testid = 'import-dialog';
    d.setAttribute('aria-labelledby', 'imTitle');
    d.innerHTML = `<div class="im">
  <div class="im-head"><span class="im-title" id="imTitle">Import</span>
    <span class="im-host" title="the machine Roughcut runs on: imports are referenced there, in place"></span>
    <span class="im-grow"></span><button type="button" class="im-x" data-im="cancel" aria-label="close" title="Close (Esc)">×</button></div>
  <div class="im-bar">
    <button type="button" class="im-nav" data-im="back" title="Back (Alt+←)" aria-label="back">‹</button>
    <button type="button" class="im-nav" data-im="fwd" title="Forward (Alt+→)" aria-label="forward">›</button>
    <button type="button" class="im-nav" data-im="up" title="Up one folder (Backspace)" aria-label="up one folder">${ICON.up}</button>
    <input type="text" class="im-path" data-testid="media-browser-path" spellcheck="false" autocomplete="off" aria-label="folder on the board's machine">
    <button type="button" class="im-nav" data-im="refresh" title="Refresh" aria-label="refresh">⟳</button>
  </div>
  <div class="im-main">
    <nav class="im-places" aria-label="places">
      <div class="im-group im-group-host">On this board's machine</div>
      <div class="im-roots"></div>
      <div class="im-group">From this computer</div>
      <button type="button" class="im-place im-upload" data-im="upload-files" data-testid="import-upload-files">${ICON.upload}<span>Upload Files…</span></button>
      <button type="button" class="im-place im-upload" data-im="upload-folder" data-testid="import-upload-folder">${ICON.upload}<span>Upload Folder…</span></button>
      <p class="im-tip">or drag files or a folder onto the Project panel</p>
    </nav>
    <div class="im-list" data-testid="media-browser" role="listbox" aria-multiselectable="true" tabindex="0" aria-label="folders and clips">
      <table class="im-table"><colgroup><col><col style="width:86px"><col style="width:132px"></colgroup>
        <thead><tr><th>Name</th><th class="r">Size</th><th>Date Modified</th></tr></thead><tbody></tbody></table>
      <div class="im-empty" hidden></div>
    </div>
  </div>
  <div class="im-foot">
    <span class="im-status" aria-live="polite"></span>
    <span class="im-grow"></span>
    <button type="button" class="im-btn" data-im="folder" data-testid="import-folder" title="Import every clip in this folder and the folders inside it">Import Folder</button>
    <button type="button" class="im-btn" data-im="cancel" data-testid="import-cancel">Cancel</button>
    <button type="button" class="im-btn primary" data-im="go" data-testid="import-go">Import</button>
  </div>
  <input type="file" class="im-files" multiple accept="video/*,.mts,.mkv,.m4v" hidden>
  <input type="file" class="im-dir" multiple webkitdirectory hidden>
</div>`;
    document.body.appendChild(d);
    S.dlg = d;
    d.addEventListener('click', onClick);
    d.addEventListener('dblclick', onDouble);
    d.addEventListener('cancel', (e) => { e.preventDefault(); close(); });
    d.addEventListener('mousedown', (e) => { if (e.target === d) close(); });   // the backdrop
    $('.im-files', d).addEventListener('change', (e) => picked(e.target));
    $('.im-dir', d).addEventListener('change', (e) => picked(e.target));
    // a drop on the dialog uploads what was dropped, as on the Project panel
    d.addEventListener('dragover', (e) => { if (hasFiles(e)) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; } });
    d.addEventListener('drop', (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      collect(e.dataTransfer).then((files) => { close(); drop(files); });
    });
    return d;
  }

  function open(mode) {
    const d = build();
    S.mode = mode === 'browse' ? 'browse' : 'import';
    $('.im-title', d).textContent = S.mode === 'browse' ? 'Media Browser' : 'Import';
    S.err = '';
    if (!d.open) d.showModal();
    const start = S.path || remembered();
    return go(start, { keep: true }).then(() => $('.im-list', d).focus());
  }
  function close() {
    if (S.dlg && S.dlg.open) S.dlg.close();
  }

  async function go(path, opts) {
    opts = opts || {};
    const d = build();
    const seq = ++S.seq;                 // the last folder asked for wins, never a slow earlier one
    S.busy = true;
    paintStatus();
    let j = null;
    try {
      const r = await fetch(`/api/media/browse${path ? `?path=${encodeURIComponent(path)}` : ''}`);
      j = await r.json().catch(() => ({}));
      if (seq !== S.seq) return;
      if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
    } catch (e) {
      if (seq !== S.seq) return;
      S.busy = false;
      if (opts.keep && path) return go('', { keep: true });     // a remembered folder that went away
      S.err = String(e.message || e);
      $('.im-path', d).value = S.path || path || '';
      paintStatus();
      return;
    }
    if (S.data && S.data.path !== j.path && !opts.history) {
      S.back.push(S.data.path);
      S.fwd = [];
    }
    const from = S.data && S.data.path;
    S.data = j;
    S.path = j.path;
    S.host = j.host || '';
    S.busy = false;
    S.err = '';
    S.sel = new Set();
    // up a folder: the cursor lands on the folder we came out of, as a file manager's does
    const came = from ? j.entries.findIndex((e) => e.path === from) : -1;
    S.cursor = came >= 0 ? came : (j.entries.length ? 0 : -1);
    S.anchor = S.cursor;
    remember(j.path);
    paint();
  }

  function paint() {
    const d = S.dlg;
    const j = S.data;
    if (!d || !j) return;
    $('.im-host', d).textContent = S.host ? `on ${S.host}` : '';
    $('.im-group-host', d).textContent = S.host ? `On ${S.host}` : "On this board's machine";
    $('.im-path', d).value = j.path;
    $('[data-im=back]', d).disabled = !S.back.length;
    $('[data-im=fwd]', d).disabled = !S.fwd.length;
    $('[data-im=up]', d).disabled = !j.parent;
    const roots = j.roots || [];
    $('.im-roots', d).innerHTML = roots.map((r) => {
      const here = j.path === r.path;
      const label = r.kind === 'bin' ? `${r.name} — this bin` : r.name;
      return `<button type="button" class="im-place${here ? ' on' : ''}" data-place="${esc(r.path)}" title="${esc(r.path)}">`
        + `${ICON[r.kind] || ICON.folder}<span>${esc(label)}</span></button>`;
    }).join('');
    const rows = j.entries.map((e, i) => {
      const cls = ['im-row', e.kind, S.sel.has(i) ? 'sel' : '', i === S.cursor ? 'cur' : '',
        e.in_project ? 'have' : ''].filter(Boolean).join(' ');
      const tag = e.in_project ? '<span class="im-have" title="already in this project">in project</span>' : '';
      return `<tr class="${cls}" data-i="${i}" data-kind="${e.kind}" data-path="${esc(e.path)}" data-testid="media-browser-row" role="option" aria-selected="${S.sel.has(i)}">`
        + `<td class="n"><span class="nn">${ICON[e.kind === 'dir' ? 'dir' : 'video']}<span class="nm">${esc(e.name)}</span>${tag}</span></td>`
        + `<td class="r">${e.kind === 'video' ? esc(bytes(e.size)) : ''}</td>`
        + `<td class="dt">${esc(when(e.mtime))}</td></tr>`;
    }).join('');
    $('.im-table tbody', d).innerHTML = rows;
    const empty = $('.im-empty', d);
    empty.hidden = j.entries.length > 0;
    empty.textContent = j.entries.length ? '' : (j.others
      ? `No clips here — ${plural(j.others, 'other file')} (not video) not shown.` : 'This folder is empty.');
    paintStatus();
    const cur = $('.im-row.cur', d);
    if (cur) cur.scrollIntoView({ block: 'nearest' });
  }

  function chosen() {
    const j = S.data;
    if (!j) return [];
    return [...S.sel].sort((a, b) => a - b).map((i) => j.entries[i])
      .filter((e) => e && !e.in_project);
  }

  function paintStatus() {
    const d = S.dlg;
    if (!d) return;
    const st = $('.im-status', d);
    const go1 = $('[data-im=go]', d);
    const j = S.data;
    st.classList.toggle('bad', !!S.err);
    if (S.err) { st.textContent = S.err; go1.disabled = !chosen().length; return; }
    if (S.busy || !j) { st.textContent = 'reading the folder…'; go1.disabled = true; return; }
    const pick = chosen();
    const vids = pick.filter((e) => e.kind === 'video');
    const dirs = pick.filter((e) => e.kind === 'dir');
    const total = vids.reduce((a, e) => a + (e.size || 0), 0);
    const all = `${plural(j.videos, 'clip')}${j.folders ? `, ${plural(j.folders, 'folder')}` : ''}`;
    const sel = pick.length
      ? ` · ${[vids.length ? `${plural(vids.length, 'clip')} selected (${bytes(total)})` : '',
        dirs.length ? `${plural(dirs.length, 'folder')} selected` : ''].filter(Boolean).join(', ')}`
      : '';
    st.textContent = `${all}${sel} · imported in place, nothing is copied`;
    go1.disabled = !pick.length;
    $('[data-im=folder]', d).disabled = !j.videos && !j.folders;
  }

  /* ---------------------------------------------------------------- selection */

  function setCursor(i, extend, toggle) {
    const n = S.data ? S.data.entries.length : 0;
    if (!n) return;
    i = Math.max(0, Math.min(n - 1, i));
    if (extend && S.anchor >= 0) {
      S.sel = new Set();
      const [a, b] = [Math.min(S.anchor, i), Math.max(S.anchor, i)];
      for (let k = a; k <= b; k++) S.sel.add(k);
    } else if (toggle) {
      if (S.sel.has(i)) S.sel.delete(i); else S.sel.add(i);
      S.anchor = i;
    } else {
      S.sel = new Set([i]);
      S.anchor = i;
    }
    S.cursor = i;
    S.err = '';
    paintRows();
  }

  function paintRows() {
    const d = S.dlg;
    d.querySelectorAll('.im-row').forEach((tr) => {
      const i = Number(tr.dataset.i);
      tr.classList.toggle('sel', S.sel.has(i));
      tr.classList.toggle('cur', i === S.cursor);
      tr.setAttribute('aria-selected', String(S.sel.has(i)));
    });
    const cur = $('.im-row.cur', d);
    if (cur) cur.scrollIntoView({ block: 'nearest' });
    paintStatus();
  }

  function activate(i) {
    const e = S.data && S.data.entries[i];
    if (!e) return;
    if (e.kind === 'dir') { go(e.path); return; }
    if (!e.in_project) importPaths([e.path]);
  }

  function onClick(e) {
    const t = e.target.closest('[data-im], [data-place], .im-row');
    if (!t) return;
    if (t.classList.contains('im-row')) {
      setCursor(Number(t.dataset.i), e.shiftKey, e.ctrlKey || e.metaKey);
      $('.im-list', S.dlg).focus();
      return;
    }
    if (t.dataset.place) { go(t.dataset.place); return; }
    const what = t.dataset.im;
    if (what === 'cancel') close();
    else if (what === 'go') importPaths(chosen().map((x) => x.path));
    else if (what === 'folder') importPaths([S.path]);
    else if (what === 'up') up();
    else if (what === 'back') history(-1);
    else if (what === 'fwd') history(1);
    else if (what === 'refresh') go(S.path, { history: true });
    else if (what === 'upload-files') $('.im-files', S.dlg).click();
    else if (what === 'upload-folder') $('.im-dir', S.dlg).click();
  }
  function onDouble(e) {
    const tr = e.target.closest('.im-row');
    if (tr) activate(Number(tr.dataset.i));
  }
  function up() { if (S.data && S.data.parent) go(S.data.parent); }
  function history(dir) {
    const from = dir < 0 ? S.back : S.fwd;
    const to = dir < 0 ? S.fwd : S.back;
    if (!from.length || !S.data) return;
    to.push(S.data.path);
    go(from.pop(), { history: true });
  }

  /* Keys while the dialog is open. On window, capture, registered at load — before the
   * dock's and the timeline's, which would take Space, the arrows, I / O and Escape
   * (verdict 8 punch 8: a timeline Escape once kept the Export dialog open). */
  function onDialogKey(e) {
    const d = S.dlg;
    const t = e.target;
    const inPath = t && t.classList && t.classList.contains('im-path');
    // the path bar: Enter goes there (its own listener would never hear it: the board's
    // keys are stopped here, at window, before the event reaches the input)
    if (inPath && e.key === 'Enter') { e.preventDefault(); go(t.value.trim()); return; }
    if (inPath || e.key === 'Tab' || e.key === 'Escape') return;
    if (t && t.tagName === 'BUTTON' && (e.key === 'Enter' || e.key === ' ')) return;
    const n = S.data ? S.data.entries.length : 0;
    const page = Math.max(1, Math.floor(($('.im-list', d).clientHeight || 300) / 22) - 1);
    let handled = true;
    if (e.altKey && e.key === 'ArrowLeft') history(-1);
    else if (e.altKey && e.key === 'ArrowRight') history(1);
    else if ((e.altKey && e.key === 'ArrowUp') || e.key === 'Backspace') up();
    else if (e.key === 'ArrowDown') setCursor(S.cursor + 1, e.shiftKey);
    else if (e.key === 'ArrowUp') setCursor(S.cursor - 1, e.shiftKey);
    else if (e.key === 'PageDown') setCursor(S.cursor + page, e.shiftKey);
    else if (e.key === 'PageUp') setCursor(S.cursor - page, e.shiftKey);
    else if (e.key === 'Home') setCursor(0, e.shiftKey);
    else if (e.key === 'End') setCursor(n - 1, e.shiftKey);
    else if (e.key === ' ') setCursor(S.cursor, false, true);
    else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') {
      S.sel = new Set(S.data.entries.map((x, i) => (x.kind === 'video' && !x.in_project ? i : -1)).filter((i) => i >= 0));
      paintRows();
    } else if (e.key === 'Enter') {
      const pick = chosen();
      if (pick.length === 1 && pick[0].kind === 'dir') go(pick[0].path);
      else if (pick.length) importPaths(pick.map((x) => x.path));
      else if (S.cursor >= 0) activate(S.cursor);
    } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey && n) {
      // type-ahead, as a file list's: the letters typed within a second find a name
      const now = Date.now();
      S.type = now - S.typeAt < 1000 ? S.type + e.key.toLowerCase() : e.key.toLowerCase();
      S.typeAt = now;
      const hit = S.data.entries.findIndex((x) => x.name.toLowerCase().startsWith(S.type));
      if (hit >= 0) setCursor(hit);
    } else handled = false;
    if (handled) e.preventDefault();
  }

  /* ---------------------------------------------------------------- import (references) */

  async function importPaths(paths) {
    if (!paths.length || S.busy) return [];
    S.busy = true;
    paintStatus();
    let j = null;
    try {
      const r = await fetch('/api/import', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ paths }),
      });
      j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
    } catch (e) {
      S.busy = false;
      S.err = `could not import: ${e.message || e}`;
      paintStatus();
      return [];
    }
    S.busy = false;
    const added = j.added || [];
    const skipped = j.skipped || [];
    if (!added.length) {
      S.err = skipped.length ? `nothing imported — ${skipped[0].why}${skipped.length > 1 ? ` (and ${skipped.length - 1} more)` : ''}` : 'nothing to import here';
      paintStatus();
      return [];
    }
    close();
    landed(added, { index: j.index, skipped: skipped.length });
    return added;
  }

  /* New clips are in the bin: the panel shows them, the board can cut them. */
  function landed(added, info) {
    info = info || {};
    try {
      window.dispatchEvent(new CustomEvent('rc:clips-changed', { detail: { added: added.slice() } }));
    } catch (_) { /* old browser */ }
    if (window.dock && window.dock.project && typeof window.dock.project.reload === 'function') {
      window.dock.project.reload();
    }
    if (window.rcFirstPass && typeof window.rcFirstPass.refresh === 'function') {
      setTimeout(() => window.rcFirstPass.refresh(), 300);    // "3 new clips: add them…?"
    }
    if (info.quiet) return;
    const free = info.index ? ' — hearing them now (free)' : '';
    const skip = info.skipped ? ` · ${info.skipped} skipped` : '';
    say(`Imported ${plural(added.length, 'clip')}${free}${skip}`, 5000);
  }

  /* ================================================================ uploads */

  function picked(input) {
    const files = [...(input.files || [])].map((f) => ({ file: f, rel: f.webkitRelativePath || '' }));
    input.value = '';
    if (!files.length) return;
    close();
    drop(files);
  }

  function hasFiles(e) {
    const t = e.dataTransfer && e.dataTransfer.types;
    return !!t && [...t].includes('Files');
  }

  /* Everything a DataTransfer holds, folders walked: [{file, rel}]. */
  async function collect(dt) {
    const items = [...(dt.items || [])].filter((it) => it.kind === 'file');
    const entries = items.map((it) => (it.webkitGetAsEntry ? it.webkitGetAsEntry() : null));
    const files = items.map((it) => it.getAsFile());
    if (!entries.some((x) => x && x.isDirectory)) {
      const flat = files.filter(Boolean);
      return (flat.length ? flat : [...(dt.files || [])]).map((f) => ({ file: f, rel: '' }));
    }
    const out = [];
    async function walk(entry, prefix) {
      if (!entry) return;
      if (entry.isFile) {
        const f = await new Promise((res) => entry.file(res, () => res(null)));
        if (f) out.push({ file: f, rel: prefix ? `${prefix}/${f.name}` : '' });
      } else if (entry.isDirectory) {
        const reader = entry.createReader();
        const kids = [];
        for (;;) {
          const batch = await new Promise((res) => reader.readEntries(res, () => res([])));
          if (!batch.length) break;
          kids.push(...batch);
        }
        kids.sort((a, b) => a.name.localeCompare(b.name));
        const here = prefix ? `${prefix}/${entry.name}` : entry.name;
        for (const k of kids) if (!k.name.startsWith('.')) await walk(k, here);
      }
    }
    for (let i = 0; i < entries.length; i++) {
      if (entries[i] && entries[i].isFile && files[i]) out.push({ file: files[i], rel: '' });
      else await walk(entries[i], '');
    }
    return out;
  }

  /* Upload files from this computer into the bin: [{file, rel}] or File objects. One
   * batch at a time; a drop while one runs waits its turn. `opts.at` (film seconds): put
   * each clip in the sequence there once it lands (a drop on the Timeline). */
  function drop(files, opts) {
    const list = [...(files || [])].map((f) => (f && f.file ? f : { file: f, rel: '' }))
      .filter((f) => f.file);
    const vids = list.filter((f) => VIDEO.test(f.file.name));
    const other = list.length - vids.length;
    if (!vids.length) {
      say(list.length ? `nothing to import: ${plural(other, 'file')} that ${other === 1 ? 'is' : 'are'} not video` : 'nothing to import');
      return Promise.resolve([]);
    }
    return new Promise((resolve) => {
      S.queue.push({ files: vids, other, opts: opts || {}, resolve });
      if (!S.up) next();
    });
  }

  async function next() {
    const job = S.queue.shift();
    if (!job) { S.up = null; return; }
    S.up = { job, batch: null, xhr: null, i: 0, n: job.files.length, cur: 0, done: 0, total: 0,
             t0: Date.now(), added: [], failed: [], skipped: [], stopped: false, name: '' };
    paintStrip();
    let plan = null;
    try {
      const r = await fetch('/api/upload', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ files: job.files.map((f) => ({ name: f.file.name, size: f.file.size, rel: f.rel || '' })) }),
      });
      plan = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(plan.detail || `HTTP ${r.status}`);
    } catch (e) {
      finishUpload(`Could not upload: ${e.message || e}`, true);
      job.resolve([]);
      return;
    }
    const up = S.up;
    up.batch = plan.batch;
    const rows = plan.files || [];
    up.skipped = rows.filter((r) => r.skip);
    const todo = rows.filter((r) => !r.skip);
    up.n = todo.length;
    up.total = todo.reduce((a, r) => a + (r.size || 0), 0);
    up.t0 = Date.now();
    for (let k = 0; k < todo.length && !up.stopped; k++) {
      const row = todo[k];
      up.i = k;
      up.name = row.clip;
      paintStrip();
      const ok = await put(up, row, job.files[row.i].file);
      if (ok) {
        up.done += row.size;
        up.added.push(row.clip);
        landed([row.clip], { quiet: true });
        if (job.opts.at != null) await place(row.clip, job.opts);
      } else if (!up.stopped) {
        up.failed.push(row.clip);
      }
    }
    const n = up.added.length;
    const already = up.skipped.filter((r) => r.skip === 'already imported').length;
    let msg;
    if (up.stopped) msg = `Upload stopped — ${plural(n, 'clip')} imported`;
    else if (n) msg = `Imported ${plural(n, 'clip')}${up.failed.length ? ` — ${up.failed.length} failed` : ''}`;
    else if (already) msg = `Already in the project: ${plural(already, 'file')}`;
    else msg = up.failed.length ? 'The upload failed' : 'Nothing was uploaded';
    if (job.other) msg += ` · ${plural(job.other, 'file')} skipped (not video)`;
    finishUpload(msg, !n && !already);
    job.resolve(up.added);
  }

  function put(up, row, file) {
    return new Promise((resolve) => {
      const x = new XMLHttpRequest();
      up.xhr = x;
      up.cur = 0;
      x.open('PUT', `/api/upload/${encodeURIComponent(up.batch)}/${row.i}`);
      x.setRequestHeader('content-type', 'application/octet-stream');
      x.upload.onprogress = (e) => { up.cur = e.loaded; paintStrip(); };
      x.onload = () => { up.xhr = null; up.cur = 0; resolve(x.status >= 200 && x.status < 300); };
      x.onerror = x.onabort = () => { up.xhr = null; up.cur = 0; resolve(false); };
      x.send(file);
    });
  }

  function stopUpload() {
    const up = S.up;
    if (!up) return;
    up.stopped = true;
    if (up.xhr) up.xhr.abort();
    if (up.batch) fetch(`/api/upload/${encodeURIComponent(up.batch)}/cancel`, { method: 'POST' }).catch(() => {});
    S.queue.forEach((j) => j.resolve([]));
    S.queue = [];
  }

  function finishUpload(msg, bad) {
    const strip = mountStrip();
    S.up = null;
    if (strip) {
      strip.hidden = false;
      strip.classList.toggle('bad', !!bad);
      strip.classList.add('done');
      $('.ip-line', strip).textContent = msg;
      $('.ip-sub', strip).textContent = '';
      $('.ip-fill', strip).style.width = bad ? '0%' : '100%';
      $('.ip-stop', strip).hidden = true;
      clearTimeout(S.hideT);
      S.hideT = setTimeout(() => { if (!S.up) strip.hidden = true; }, bad ? 9000 : 5000);
    }
    say(msg, 5000);
    if (S.queue.length) next();
  }

  /* The panel's foot while an upload runs (Premiere's progress at the panel's bottom). */
  function mountStrip() {
    if (S.strip && document.body.contains(S.strip)) return S.strip;
    const host = document.getElementById('pProject');
    if (!host) return null;
    const el = document.createElement('div');
    el.className = 'import-progress';
    el.dataset.testid = 'import-progress';
    el.hidden = true;
    el.setAttribute('role', 'status');
    el.innerHTML = `<div class="ip-row">${ICON.upload}<span class="ip-line"></span>`
      + '<button type="button" class="ip-stop" title="Stop the upload — what landed is kept">Stop</button></div>'
      + '<div class="ip-bar"><i class="ip-fill"></i></div><div class="ip-sub"></div>';
    $('.ip-stop', el).addEventListener('click', stopUpload);
    host.appendChild(el);
    S.strip = el;
    return el;
  }

  function paintStrip() {
    const up = S.up;
    const el = mountStrip();
    if (!el || !up) return;
    clearTimeout(S.hideT);
    el.hidden = false;
    el.classList.remove('done', 'bad');
    $('.ip-stop', el).hidden = false;
    const sent = up.done + (up.cur || 0);
    const pct = up.total ? Math.min(100, (100 * sent) / up.total) : 0;
    const secs = (Date.now() - up.t0) / 1000;
    const rate = secs > 1 && sent ? sent / secs : 0;
    $('.ip-line', el).textContent = up.batch
      ? `Uploading ${Math.min(up.i + 1, up.n)} of ${up.n} · ${up.name}`
      : `Preparing ${plural(up.job.files.length, 'file')}…`;
    $('.ip-sub', el).textContent = up.batch
      ? [`${bytes(sent)} of ${bytes(up.total)}`, `${Math.round(pct)}%`,
        rate ? left((up.total - sent) / rate) : ''].filter(Boolean).join(' · ')
      : '';
    $('.ip-fill', el).style.width = `${pct.toFixed(1)}%`;
  }

  /* A drop on the Timeline: the clip goes in at the drop time once it lands (Premiere's
   * drag from the desktop onto a sequence), the next one after it. */
  async function place(clip, opts) {
    let c = null;
    for (let k = 0; k < 40 && !(c && c.duration); k++) {
      if (window.dock && window.dock.project) {
        await window.dock.project.reload();
        c = window.dock.project.clips().find((x) => x.clip === clip) || null;
      }
      if (!(c && c.duration)) await new Promise((r) => setTimeout(r, 250));
    }
    if (!c || !c.duration || !window.tl || typeof window.tl.insertAt !== 'function') return;
    if (typeof window.rcEnsureClip === 'function') window.rcEnsureClip(clip);
    const at = opts.at;
    const id = window.tl.insertAt({ clip, in: 0, out: Number(c.duration), why: '' }, at);
    if (id != null) opts.at = at + Number(c.duration);
  }

  /* ================================================================ drag and drop */

  function overlay(host) {
    let o = host.querySelector(':scope > .project-drop');
    if (!o) {
      o = document.createElement('div');
      o.className = 'project-drop';
      o.dataset.testid = host.id === 'pProject' ? 'project-drop' : 'timeline-drop';
      o.hidden = true;
      o.innerHTML = `<div class="pd-box">${ICON.import}<b class="pd-line"></b><span class="pd-sub"></span></div>`;
      host.appendChild(o);
    }
    return o;
  }

  function dropWords(e, where) {
    const items = [...((e.dataTransfer && e.dataTransfer.items) || [])].filter((it) => it.kind === 'file');
    const n = items.length;
    const typed = items.every((it) => it.type);       // a folder (or an .mts) has no type
    const what = n ? plural(n, typed ? 'file' : 'item') : 'files';
    return where === 'timeline'
      ? [`Drop to import ${what} into the sequence`, 'uploaded from this computer, then inserted here']
      : [`Drop to import ${what}`, `uploaded from this computer${S.host ? ` to ${S.host}` : ''} · folders too`];
  }

  function attach(host, where) {
    if (!host || host.dataset.rcImportDrop) return;
    host.dataset.rcImportDrop = '1';
    let depth = 0;
    const hide = () => { depth = 0; overlay(host).hidden = true; };
    host.addEventListener('dragenter', (e) => {
      if (!hasFiles(e)) return;
      depth += 1;
      const o = overlay(host);
      const [line, sub] = dropWords(e, where);
      $('.pd-line', o).textContent = line;
      $('.pd-sub', o).textContent = sub;
      o.hidden = false;
    });
    host.addEventListener('dragover', (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
      if (where === 'timeline' && window.tl && typeof window.tl.eventTime === 'function') {
        const t = window.tl.eventTime(e);
        const tc = typeof window.tl.tc === 'function' ? window.tl.tc(t) : `${t.toFixed(2)} s`;
        $('.pd-sub', overlay(host)).textContent = `uploaded from this computer, then inserted at ${tc}`;
      }
    });
    host.addEventListener('dragleave', (e) => {
      if (!hasFiles(e)) return;
      depth = Math.max(0, depth - 1);
      if (!depth || !host.contains(e.relatedTarget)) hide();
    });
    host.addEventListener('drop', (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      e.stopPropagation();
      hide();
      const at = where === 'timeline' && window.tl && typeof window.tl.eventTime === 'function'
        ? window.tl.eventTime(e) : null;
      collect(e.dataTransfer).then((files) => drop(files, at == null ? {} : { at }));
    });
  }

  /* A file dropped anywhere else must not navigate the board away (the browser opens a
   * dropped video in the tab, and the cut's undo history goes with it). */
  window.addEventListener('dragover', (e) => {
    if (hasFiles(e) && !e.defaultPrevented) { e.preventDefault(); e.dataTransfer.dropEffect = 'none'; }
  });
  window.addEventListener('drop', (e) => { if (hasFiles(e)) e.preventDefault(); });
  window.addEventListener('beforeunload', (e) => {
    if (S.up && S.up.batch) { e.preventDefault(); e.returnValue = ''; }
  });

  /* ================================================================ the ways in */

  /* The Project panel's own Import controls (its bottom bar's Import, its empty state's,
   * a double-click on its empty space) are BINLOOK's, in dock.js, and call
   * `rcImport.open()`; this module adds only the OS drop onto the panel (on #pProject,
   * never inside #projList, which every repaint rebuilds). */
  function mountProject() {
    attach(document.getElementById('pProject'), 'project');
  }

  window.addEventListener('keydown', (e) => {
    if (S.dlg && S.dlg.open) {
      onDialogKey(e);
      e.stopImmediatePropagation();     // the board's keys wait while the dialog is up
      return;
    }
    const mod = IS_MAC ? e.metaKey : e.ctrlKey;
    if (!mod || e.shiftKey || e.altKey || e.code !== 'KeyI') return;
    if (e.target && e.target.isContentEditable) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    open();
  }, true);

  (window.rcExtraKeys = window.rcExtraKeys || []).push(
    [KEY, 'import footage (File › Import): this machine’s folders, or upload', 'bin']);

  function init() {
    mountProject();
    attach(document.getElementById('pTimeline'), 'timeline');
    // the menu shows this platform's key
    const k = document.querySelector('#menubar button[data-cmd="import"] .k');
    if (k) k.textContent = KEY;
  }

  window.rcImport = {
    open: () => open('import'),
    browse: () => open('browse'),
    drop: (files, opts) => drop(files, opts),
    importPaths,
    state: () => ({ open: !!(S.dlg && S.dlg.open), mode: S.mode, path: S.path,
                    uploading: !!S.up, queued: S.queue.length }),
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
