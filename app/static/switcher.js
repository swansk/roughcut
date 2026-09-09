/* The bin-and-cut switcher — one control in every screen's header.
 *
 * Karl, 2026-09-08: "work on multiple projects at the same time … switching easily
 * between projects, and saving copies so we can try different things." Two lists
 * behind one name: the bins the server knows (opened before, or a folder of video
 * next door — INTAKE I5.4's picker, which used to live only on the open screen) and
 * the cuts of the bin you are on. A cut is a copy of the whole project file; saving one
 * under a name is how you try something without losing what you had.
 *
 * Shared by /open, /floor and / as a classic script loaded before each page's own, so
 * it owns nothing of theirs. It mounts on `#hdBin`, appends its panel to the body,
 * and talks to the page through two optional hooks: `window.roughcutFlush()` — write
 * anything unsaved before the server re-points itself (the board autosaves on a
 * timer) — and `window.roughcutReopen()` — reload the page's state in place; a page
 * without one is simply reloaded, with the word carried across in sessionStorage.
 */
(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
  const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
  const isTyping = (el) => !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA'
    || el.isContentEditable);

  const CSS = `
  #hdBin { background: transparent; border: 1px solid transparent; color: var(--dim, #9a9aa8);
           font: inherit; font-weight: 400; letter-spacing: 0; font-size: 12px; margin-left: 6px;
           padding: 1px 8px; border-radius: 6px; cursor: pointer; white-space: nowrap; }
  #hdBin b { color: var(--text, #e8e8ee); font-weight: 600; }
  #hdBin .cutname { color: var(--accent, #6ea8fe); margin-left: 6px; }
  #hdBin .cutname:empty { display: none; }
  #hdBin::after { content: " ▾"; color: var(--faint, #6c6c80); font-size: 10px; }
  #hdBin:hover, #hdBin[aria-expanded="true"] { border-color: #3a3a48; background: var(--card-hi, #1e1e25); }
  #picker { position: fixed; z-index: 60; width: 440px; max-width: calc(100vw - 24px);
            background: var(--panel, #0f0f13); color: var(--text, #e8e8ee); border: 1px solid #3a3a48;
            border-radius: 10px; padding: 10px 12px; box-shadow: 0 12px 32px rgba(0,0,0,.55);
            display: flex; flex-direction: column; gap: 8px; font: 12px/1.45 var(--display, system-ui, sans-serif); }
  #picker.busy { opacity: .6; pointer-events: none; }
  #picker .lbl { font: 600 9.5px var(--mono, ui-monospace, monospace); letter-spacing: .1em;
                 color: #8f8fa3; text-transform: uppercase; }
  #picker .lbl .sub { text-transform: none; letter-spacing: 0; font-weight: 400; color: var(--dim, #9a9aa8); margin-left: 4px; }
  #picker .hint { color: var(--dim, #9a9aa8); }
  #picker .small { font-size: 10.5px; }
  #picker .bad { color: var(--bad-soft, #ffb8b8); }
  #picker .key { display: inline-block; font: 600 9.5px var(--mono, ui-monospace, monospace); color: var(--text, #e8e8ee);
                 background: #1e1e25; border: 1px solid #3a3a48; border-radius: 4px; padding: 0 5px; }
  #cutList, #pickerList { display: flex; flex-direction: column; gap: 2px; max-height: 32vh; overflow-y: auto; }
  #picker .prow { display: flex; gap: 8px; align-items: baseline; width: 100%; text-align: left; background: transparent;
                  border: 1px solid transparent; border-radius: 6px; padding: 5px 8px; font: inherit; font-size: 11.5px;
                  min-width: 0; color: inherit; cursor: pointer; box-sizing: border-box; }
  #picker .prow:hover, #picker .prow:focus { background: var(--card-hi, #1e1e25); border-color: #3a3a48; outline: none; }
  #picker .prow.current { border-color: #2f4a70; cursor: default; }
  #picker .prow b { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  #picker .prow .n { color: var(--dim, #9a9aa8); font-size: 10.5px; white-space: nowrap; }
  #picker .prow .cur { margin-left: auto; font: 600 9px var(--mono, ui-monospace, monospace); letter-spacing: .06em;
                       color: var(--accent, #6ea8fe); text-transform: uppercase; white-space: nowrap; }
  #picker .flags { display: inline-flex; gap: 4px; flex-wrap: wrap; }
  #picker .flag { font: 500 9px var(--mono, ui-monospace, monospace); color: var(--faint, #6c6c80); font-style: normal; white-space: nowrap; }
  #picker .flag.on { color: #cfd6e4; }
  #picker .flag.good { color: var(--good-soft, #b8f0cc); }
  #picker .flag.bad { color: var(--bad-soft, #ffb8b8); }
  #picker .acts { margin-left: auto; display: inline-flex; gap: 8px; white-space: nowrap; }
  #picker .prow.current .acts { margin-left: 8px; }
  #picker .act { font-size: 10px; color: var(--dim, #9a9aa8); cursor: pointer; text-decoration: none; }
  #picker .act:hover { color: var(--text, #e8e8ee); text-decoration: underline; }
  #picker .act.bad:hover { color: var(--bad-soft, #ffb8b8); }
  #copyForm { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
  #copyName, #pickerPath { font: inherit; font-size: 11px; color: var(--text, #e8e8ee); background: #101014;
                           border: 1px solid #2e2e38; border-radius: 6px; padding: 6px 9px; box-sizing: border-box; }
  #copyName { flex: 1; min-width: 140px; }
  #pickerPath { width: 100%; }
  #copyName:focus, #pickerPath:focus { outline: none; border-color: var(--accent, #6ea8fe); }
  #copyForm label { font-size: 10.5px; color: var(--dim, #9a9aa8); display: inline-flex; gap: 4px; align-items: center;
                    white-space: nowrap; cursor: pointer; }
  #copyGo { font: inherit; font-size: 11px; background: var(--accent, #6ea8fe); color: #0b0b0f; border: 0;
            border-radius: 6px; padding: 5px 10px; font-weight: 600; cursor: pointer; }
  #copyGo:disabled { opacity: .45; cursor: default; }
  #picker hr { border: 0; border-top: 1px solid #22222b; margin: 2px 0; }
  #switcherToast { position: fixed; left: 50%; bottom: 40px; transform: translateX(-50%); background: #1e1e25;
                   color: #e8e8ee; border: 1px solid #3a3a48; border-radius: 8px; padding: 8px 14px; font-size: 12px;
                   opacity: 0; pointer-events: none; transition: opacity .2s; z-index: 70; max-width: 70vw; }
  #switcherToast.show { opacity: 1; }
  `;

  const S = { open: false, busy: false, bins: null, cuts: null, error: null };

  function say(msg, ms = 2600) {
    if (typeof window.toast === 'function') return window.toast(msg, ms);
    let el = $('#switcherToast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'switcherToast';
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove('show'), ms);
  }

  async function getJSON(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
    return r.json();
  }

  async function send(url, body) {
    const r = await fetch(url, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
    return data;
  }

  /* ------------------------------------------------------------ the header */

  function paintName() {
    const btn = $('#hdBin');
    if (!btn) return;
    const bin = (S.cuts && S.cuts.bin) || (S.bins && S.bins.current && S.bins.current.name) || '';
    const cut = (S.cuts && S.cuts.current && S.cuts.current.name) || '';
    btn.innerHTML = `<b>${esc(bin)}</b><span class="cutname">${esc(cut)}</span>`;
    btn.title = cut ? `${bin} · the cut "${cut}" — switch bins or cuts, save a copy · O`
                    : 'switch bins or cuts, save a copy · O';
  }

  /* --------------------------------------------------------------- the rows */

  function binFlags(p) {
    const out = [];
    if (!p.exists) out.push('<i class="flag bad">missing</i>');
    if (p.segments > 0) out.push(`<i class="flag on">cut · ${plural(p.segments, 'shot')}</i>`);
    if (p.cuts > 1) out.push(`<i class="flag on">${plural(p.cuts, 'cut')}</i>`);
    if (p.journal) out.push('<i class="flag good">journal</i>');
    if (p.exists && !(p.segments > 0) && !p.journal) out.push('<i class="flag">new</i>');
    return out.join('');
  }

  function binRow(p) {
    return `<button type="button" class="prow${p.current ? ' current' : ''}" data-footage="${esc(p.footage)}" `
      + `title="${esc(p.footage)}"${p.current ? ' aria-current="true"' : ''}>`
      + `<b>${esc(p.name)}</b><span class="n tnum">${plural(p.clips, 'clip')}</span>`
      + `<span class="flags">${binFlags(p)}</span>${p.current ? '<span class="cur">open now</span>' : ''}</button>`;
  }

  function when(t) {
    if (!t) return '';
    const d = new Date(t * 1000);
    return `${d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} `
      + `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`;
  }

  function cutRow(c) {
    const facts = [c.segments > 0 ? `${plural(c.segments, 'shot')} · ${clock(c.duration_s || 0)}` : 'empty'];
    if (c.music) facts.push('♪');
    const flags = [];
    if (c.from) flags.push(`<i class="flag">from ${esc(c.from)}</i>`);
    if (c.created) flags.push(`<i class="flag">${esc(when(c.created))}</i>`);
    const acts = [`<a class="act" data-act="rename" title="rename this cut">rename</a>`];
    if (!c.current) acts.push(`<a class="act bad" data-act="delete" title="move this cut to the bin's trash">delete</a>`);
    return `<div class="prow crow${c.current ? ' current' : ''}" role="button" tabindex="0" data-path="${esc(c.path)}" `
      + `title="${esc(c.story || c.path)}"${c.current ? ' aria-current="true"' : ''}>`
      + `<b>${esc(c.name)}</b><span class="n tnum">${facts.join(' · ')}</span>`
      + `<span class="flags">${flags.join('')}</span>`
      + `${c.current ? '<span class="cur">open now</span>' : ''}<span class="acts">${acts.join('')}</span></div>`;
  }

  function render() {
    if (!S.open) return;
    const cl = $('#cutList'), bl = $('#pickerList');
    if (S.error) {
      cl.innerHTML = `<span class="bad small">${esc(S.error)}</span>`;
      bl.innerHTML = '';
      return;
    }
    if (S.cuts) {
      cl.innerHTML = S.cuts.cuts.map(cutRow).join('')
        || '<span class="hint small">no cut yet</span>';
      $('#cutSub').textContent = `of ${S.cuts.bin} · ${plural(S.cuts.cuts.length, 'cut')}`;
    }
    if (S.bins) {
      bl.innerHTML = S.bins.projects.map(binRow).join('')
        || '<span class="hint small">no bins known yet — type a folder below</span>';
      $('#pickerSub').textContent = `${plural(S.bins.projects.length, 'bin')} · opened before, or a folder of video next door`;
    }
  }

  /* -------------------------------------------------------------- the panel */

  function mountPanel() {
    if ($('#picker')) return;
    const style = document.createElement('style');
    style.textContent = CSS;
    document.head.appendChild(style);
    const el = document.createElement('div');
    el.id = 'picker';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-label', 'switch bins or cuts');
    el.hidden = true;
    el.innerHTML = `
      <div class="lbl">Cuts <span id="cutSub" class="sub"></span></div>
      <div id="cutList"></div>
      <form id="copyForm" autocomplete="off">
        <input id="copyName" type="text" placeholder="save a copy as…" spellcheck="false" maxlength="60">
        <label><input type="checkbox" id="copyOpen" checked> work on the copy</label>
        <button type="submit" id="copyGo">Save copy</button>
      </form>
      <div class="hint small">a copy is the whole project file — timeline, story, music and the pass's picks — so switching never loses anything · the bin's footage, proxies and renders are shared by every cut</div>
      <hr>
      <div class="lbl">Bins <span id="pickerSub" class="sub"></span></div>
      <div id="pickerList"></div>
      <input id="pickerPath" type="text" placeholder="open a folder… (its path, typed or pasted)" spellcheck="false" autocomplete="off">
      <div class="hint small">a folder of video, by its path — there is no browsing dialog in a page · <span class="key">↵</span> opens · <span class="key">esc</span> closes · the run and the cut you have stay with the bin they belong to</div>`;
    document.body.appendChild(el);
    wirePanel(el);
  }

  function place() {
    const btn = $('#hdBin'), el = $('#picker');
    if (!btn || !el) return;
    const r = btn.getBoundingClientRect();
    el.style.top = `${Math.round(r.bottom + 4)}px`;
    el.style.left = `${Math.max(8, Math.round(r.left))}px`;
  }

  async function refresh() {
    S.error = null;
    try {
      [S.cuts, S.bins] = await Promise.all([getJSON('/api/cuts'), getJSON('/api/projects')]);
    } catch (e) {
      S.error = String(e.message || e);
    }
    paintName();
    render();
  }

  async function open() {
    if (S.open) return;
    mountPanel();
    S.open = true;
    const el = $('#picker');
    el.hidden = false;
    place();
    $('#hdBin').setAttribute('aria-expanded', 'true');
    $('#cutList').innerHTML = '<span class="hint small">looking…</span>';
    $('#pickerList').innerHTML = '';
    await refresh();
    if (S.open) $('#copyName').focus();
  }

  function close() {
    if (!S.open) return;
    S.open = false;
    const el = $('#picker');
    el.hidden = true;
    $('#hdBin').setAttribute('aria-expanded', 'false');
    if (el.contains(document.activeElement)) $('#hdBin').focus();
  }

  /* ------------------------------------------------------------ the moves */

  // The page's word: write what is unsaved, then re-point. Every move goes through
  // here so no page can forget the flush.
  async function move(fn, after) {
    if (S.busy) return;
    S.busy = true;
    $('#picker').classList.add('busy');
    try {
      if (typeof window.roughcutFlush === 'function') await window.roughcutFlush();
      const r = await fn();
      close();
      if (typeof window.roughcutReopen === 'function') {
        say(after(r));
        await window.roughcutReopen();
        await refresh();
      } else {
        try { sessionStorage.setItem('rc.toast', after(r)); } catch (e) { /* private mode */ }
        location.reload();
      }
    } catch (e) {
      say(String(e.message || e), 5000);        // 400 / 404 / 409: the server's word; the panel stays
    } finally {
      S.busy = false;
      const el = $('#picker');
      if (el) el.classList.remove('busy');
    }
  }

  function openBin(footage) {
    const path = String(footage || '').trim();
    if (!path) return;
    return move(() => send('/api/projects/open', { footage: path }),
                (r) => `opened ${r.name}${r.edl_created ? ' · new project' : ''}`
                  + (r.cut && !r.edl_created ? ` · ${r.cut}` : ''));
  }

  function openCut(path) {
    if (S.cuts && S.cuts.current && S.cuts.current.path === path) return close();
    return move(() => send('/api/cuts/open', { path }),
                (r) => `on ${r.current ? r.current.name : 'the cut'}`);
  }

  async function saveCopy() {
    const name = $('#copyName').value.trim();
    if (!name) { $('#copyName').focus(); return; }
    const openIt = $('#copyOpen').checked;
    if (openIt) {
      return move(() => send('/api/cuts/copy', { name, open: true }),
                  (r) => `saved a copy as ${r.copy.name} — you are on it now`);
    }
    // A checkpoint: nothing re-points, the list just gains a row.
    if (S.busy) return;
    S.busy = true;
    $('#picker').classList.add('busy');
    try {
      if (typeof window.roughcutFlush === 'function') await window.roughcutFlush();
      const r = await send('/api/cuts/copy', { name, open: false });
      $('#copyName').value = '';
      say(`saved a copy as ${r.copy.name} — still on ${r.current ? r.current.name : 'this cut'}`);
      S.cuts = r;
      render();
    } catch (e) {
      say(String(e.message || e), 5000);
    } finally {
      S.busy = false;
      $('#picker').classList.remove('busy');
    }
  }

  async function renameCut(path) {
    const row = S.cuts && S.cuts.cuts.find((c) => c.path === path);
    const name = window.prompt('rename this cut', row ? row.name : '');
    if (name == null || !name.trim()) return;
    try {
      S.cuts = await send('/api/cuts/rename', { path, name: name.trim() });
      paintName();
      render();
      say(`renamed to ${name.trim()}`);
    } catch (e) {
      say(String(e.message || e), 5000);
    }
  }

  async function deleteCut(path) {
    const row = S.cuts && S.cuts.cuts.find((c) => c.path === path);
    if (!window.confirm(`move the cut "${row ? row.name : path}" to the bin's trash?`)) return;
    try {
      S.cuts = await send('/api/cuts/delete', { path });
      render();
      say(`moved to trash — ${S.cuts.trashed}`, 4000);
    } catch (e) {
      say(String(e.message || e), 5000);
    }
  }

  /* --------------------------------------------------------------- wiring */

  function wirePanel(el) {
    $('#pickerList', el).addEventListener('click', (e) => {
      const row = e.target.closest('.prow');
      if (row && !row.classList.contains('current')) openBin(row.dataset.footage);
    });
    $('#cutList', el).addEventListener('click', (e) => {
      const act = e.target.closest('.act');
      const row = e.target.closest('.crow');
      if (!row) return;
      e.preventDefault();
      if (act && act.dataset.act === 'rename') return renameCut(row.dataset.path);
      if (act && act.dataset.act === 'delete') return deleteCut(row.dataset.path);
      if (!row.classList.contains('current')) openCut(row.dataset.path);
    });
    $('#cutList', el).addEventListener('keydown', (e) => {
      const row = e.target.closest('.crow');
      if (row && e.key === 'Enter') { e.preventDefault(); openCut(row.dataset.path); }
    });
    $('#copyForm', el).addEventListener('submit', (e) => { e.preventDefault(); saveCopy(); });
    $('#pickerPath', el).addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      openBin(e.target.value);
    });
    // keys typed into the panel's fields are the panel's, not the page's (the pass
    // binds most of the keyboard; the board binds j/k/x/u and the brackets)
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); close(); return; }
      e.stopPropagation();
    });
    el.addEventListener('keyup', (e) => e.stopPropagation());
    el.addEventListener('keypress', (e) => e.stopPropagation());
  }

  function onKey(e) {
    if (e.key === 'Escape') {
      if (S.open) { e.preventDefault(); close(); }
      return;
    }
    if ((e.key === 'o' || e.key === 'O') && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey
        && !isTyping(document.activeElement)) {
      e.preventDefault();
      if (S.open) close(); else open();
    }
  }

  function boot() {
    const btn = $('#hdBin');
    if (!btn) return;
    btn.setAttribute('aria-haspopup', 'dialog');
    btn.setAttribute('aria-expanded', 'false');
    btn.addEventListener('click', () => { if (S.open) close(); else open(); });
    // capture: the pass and the board stop propagation on some keys of their own
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('pointerdown', (e) => {
      if (S.open && !e.target.closest('#picker') && !e.target.closest('#hdBin')) close();
    });
    window.addEventListener('resize', () => { if (S.open) place(); });
    let carried = null;
    try { carried = sessionStorage.getItem('rc.toast'); sessionStorage.removeItem('rc.toast'); } catch (e) { /* */ }
    if (carried) setTimeout(() => say(carried, 3200), 0);
    getJSON('/api/cuts').then((c) => { S.cuts = c; paintName(); }).catch(() => {});
  }

  /* What the pages and the tests reach for. */
  window.switcher = { state: S, open, close, refresh, openBin, openCut, saveCopy, renameCut, deleteCut,
                      paintName };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
