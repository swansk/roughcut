/* events.js: Window › Events, the index's journal in the editor (wave 17 FOLDIN, K4 b).
 *
 * The footage screen (/open) showed, behind its "how it was indexed" line, the index's
 * journal: where it is on disk and its last lines ("CLIP_07 · look done", "paused at the
 * budget cap"). That screen is gone and the Index panel (lane INDEXPANEL's) shows each
 * clip's stages, not the journal's running account, so the account is Premiere's Events
 * panel: Window › Events, a dialog listing the journal's lines, newest first, with its
 * path, read from GET /api/index (re-read every 2 s while it is open and a run goes).
 * Read-only: nothing here starts, stops or spends anything. Test hooks: `events` (the
 * dialog), `events-row`, `events-path`, `events-close`.
 */
(function () {
  'use strict';
  const $ = (s, r) => (r || document).querySelector(s);
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const MAX = 60;                     // lines shown: the journal keeps its own, longer
  const POLL_MS = 2000;
  const E = { dlg: null, timer: 0, data: null };

  const CSS = `
#eventsDlg { padding: 0; width: 560px; max-width: calc(100vw - 32px);
  background: var(--panel, #1d1d1f); color: var(--text, #dcdcdf);
  border: 1px solid var(--line-hi, #46464c); border-radius: 6px;
  box-shadow: 0 18px 48px rgba(0, 0, 0, .55); font-size: 12px; }
#eventsDlg::backdrop { background: rgba(4, 4, 6, .4); }
#eventsDlg .ev-head { display: flex; align-items: baseline; gap: 12px; padding: 10px 16px;
  background: var(--head, #252527); border-bottom: 1px solid var(--line, #333337); border-radius: 6px 6px 0 0; }
#eventsDlg .ev-title { font-weight: 600; font-size: 13px; }
#eventsDlg .ev-sub { color: var(--dim, #94949c); font-size: 11.5px; min-width: 0; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
#eventsDlg .ev-body { padding: 8px 16px; max-height: 50vh; overflow: auto; }
#eventsDlg .ev-path { color: var(--faint, #77777f); font: 10.5px ui-monospace, Consolas, monospace;
  word-break: break-all; margin-bottom: 6px; }
#eventsDlg table { width: 100%; border-collapse: collapse; }
#eventsDlg td { padding: 2px 6px 2px 0; vertical-align: top; border-bottom: 1px solid var(--line, #2a2a2e); }
#eventsDlg td.t { color: var(--dim, #94949c); white-space: nowrap; font-variant-numeric: tabular-nums; width: 1%; }
#eventsDlg .ev-none { color: var(--dim, #94949c); padding: 8px 0; }
#eventsDlg .ev-foot { display: flex; justify-content: flex-end; padding: 8px 16px 12px;
  border-top: 1px solid var(--line, #333337); }
#eventsDlg .ev-btn { height: 26px; padding: 0 14px; border-radius: 3px; font: inherit; cursor: pointer;
  background: var(--card-hi, #313135); color: var(--text, #dcdcdf); border: 1px solid var(--line-hi, #46464c); }
`;

  function build() {
    if (E.dlg) return E.dlg;
    if (!$('#events-css')) {
      const st = document.createElement('style');
      st.id = 'events-css';
      st.textContent = CSS;
      document.head.appendChild(st);
    }
    const d = document.createElement('dialog');
    d.id = 'eventsDlg';
    d.dataset.testid = 'events';
    d.setAttribute('aria-label', 'Events');
    d.innerHTML = '<div class="ev-head"><span class="ev-title">Events</span><span class="ev-sub" id="evSub"></span></div>'
      + '<div class="ev-body"><div class="ev-path" id="evPath" data-testid="events-path"></div><div id="evList"></div></div>'
      + '<div class="ev-foot"><button type="button" class="ev-btn" id="evClose" data-testid="events-close">Close</button></div>';
    document.body.appendChild(d);
    d.addEventListener('cancel', (e) => { e.preventDefault(); close(); });
    d.addEventListener('keydown', (e) => { if (e.key !== 'Escape') e.stopPropagation(); });
    $('#evClose', d).addEventListener('click', close);
    E.dlg = d;
    return d;
  }

  const when = (at) => {
    const t = new Date(Number(at) * 1000);
    if (Number.isNaN(t.getTime())) return '';
    const today = new Date().toDateString() === t.toDateString();
    return (today ? '' : `${t.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} `)
      + t.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  function paint() {
    const d = E.dlg, ix = E.data;
    if (!d) return;
    if (!ix) { $('#evList', d).innerHTML = '<div class="ev-none">reading the journal…</div>'; return; }
    if (!ix.exists) {
      $('#evSub', d).textContent = 'the index';
      $('#evPath', d).textContent = '';
      $('#evList', d).innerHTML = '<div class="ev-none">No index has run on this bin yet: the Index panel and the first pass start one.</div>';
      return;
    }
    const log = ((ix.progress || {}).log || []).slice(-MAX).reverse();
    $('#evSub', d).textContent = `the index · ${ix.running ? 'running' : 'at rest'}${log.length ? ` · ${log.length} line${log.length === 1 ? '' : 's'}` : ''}`;
    $('#evPath', d).textContent = ix.path ? `journal: ${ix.path}` : '';
    $('#evList', d).innerHTML = log.length
      ? `<table>${log.map((e) => `<tr data-testid="events-row"><td class="t">${esc(when(e.at))}</td><td>${esc(e.what)}</td></tr>`).join('')}</table>`
      : '<div class="ev-none">The journal has no lines yet.</div>';
  }

  async function read() {
    clearTimeout(E.timer);
    try {
      const r = await fetch('/api/index');
      if (r.ok) E.data = await r.json();
    } catch (e) { /* the last answer stays */ }
    paint();
    if (E.dlg && E.dlg.open && E.data && E.data.running) E.timer = setTimeout(read, POLL_MS);
  }

  function open() {
    const d = build();
    E.data = null;
    paint();
    if (!d.open) d.showModal();
    $('#evClose', d).focus();
    return read();
  }

  function close() {
    clearTimeout(E.timer);
    if (E.dlg && E.dlg.open) E.dlg.close();
  }

  window.rcEvents = { open, close, read, state: E };
})();
