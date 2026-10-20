/* projsettings.js: File › Project Settings… (wave 17 FOLDIN, Karl's K4 b).
 *
 * The footage screen (/open) kept the project's money and work settings in a drawer
 * behind its header's "settings": the budget cap and the index's workers. That screen is
 * gone (fold in, then remove), and Premiere keeps a project's settings in a dialog under
 * File, so here they are, in the Sequence Settings dialog's dense modal:
 *
 *   Budget cap   No cap (the default, INTAKE M16 decision 7) · or cap this project at $ —
 *                the line the index pauses its priced stages at, and a model call past it
 *                is refused. A cap from the environment (ROUGHCUT_BUDGET_USD) wins over the
 *                saved one, so the choice is disabled and says so. This project's spend
 *                beside it, in the backend's words (rcCost: on the plan an API-equivalent
 *                estimate, never "spent").
 *   Index        the order it works through the bin in (most promising first: the
 *                sentence's words, what was said and the sensors first; or capture order,
 *                a chronological cull: the footage screen's toggle, which the Index panel
 *                does not carry), and one worker stepper per stage, 1–8, each with why its
 *                default is what it is; both applied at the next index run (said, when one
 *                is running).
 *
 * Nothing is written until OK: one PUT /api/settings of the whole form; the server's 400
 * is said in the dialog, which stays open. Reset to defaults fills the form from the
 * server's `defaults` (OK still writes). Cancel and Esc change nothing. Test hooks:
 * `project-settings` (the dialog), `ps-cap-none`, `ps-cap-on`, `ps-cap`, `ps-order-priority`,
 * `ps-order-capture`, `ps-workers` rows by `data-stage`, `ps-ok`, `ps-cancel`, `ps-reset`,
 * `ps-error`.
 */
(function () {
  'use strict';
  const $ = (s, r) => (r || document).querySelector(s);
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const usd = (x) => `$${Number(x || 0).toFixed(2)}`;
  const capText = (c) => (c == null ? 'none' : usd(c));
  const ORDER_WORD = { priority: 'most promising first', capture: 'capture order' };
  const say = (msg, ms) => { if (typeof window.toast === 'function') window.toast(msg, ms || 3500); };

  // The stages a worker count applies to, in the order the index runs them, and why each
  // default is what it is (the footage screen's words, design §3).
  const STAGES = ['probe', 'asr', 'proxy', 'sheet', 'picks'];
  const NAMES = { probe: 'Probe', asr: 'Hearing', proxy: 'Previews', sheet: 'Looks', picks: 'Picks' };
  const WHY = {
    probe: 'two — ffprobe is quick; more just contend for the disk',
    asr: 'one — the model loads once per run and walks the folder',
    proxy: 'one — two 4K encodes share the same cores',
    sheet: 'two sheets in flight at once — more spends faster, not better',
    picks: 'one — a call per released clip; the sheet limit is the ceiling that matters',
  };
  const MIN = 1, MAX = 8;
  const clamp = (v) => {
    const n = Math.round(Number(v));
    return Number.isFinite(n) ? Math.max(MIN, Math.min(MAX, n)) : MIN;
  };

  const S = { dlg: null, settings: null, backend: null, running: false, busy: false };

  const CSS = `
#projSettings { padding: 0; width: 480px; max-width: calc(100vw - 32px);
  background: var(--panel, #1d1d1f); color: var(--text, #dcdcdf);
  border: 1px solid var(--line-hi, #46464c); border-radius: 6px;
  box-shadow: 0 18px 48px rgba(0, 0, 0, .55); font-size: 12.5px; }
#projSettings::backdrop { background: rgba(4, 4, 6, .62); }
#projSettings .ps { margin: 0; display: flex; flex-direction: column; }
#projSettings .ps-head { display: flex; align-items: baseline; gap: 12px; padding: 10px 16px;
  background: var(--head, #252527); border-bottom: 1px solid var(--line, #333337); border-radius: 6px 6px 0 0; }
#projSettings .ps-title { font-weight: 600; font-size: 13px; }
#projSettings .ps-sub { color: var(--dim, #94949c); font-size: 11.5px; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; min-width: 0; }
#projSettings .ps-body { padding: 12px 16px 8px; display: flex; flex-direction: column; gap: 6px; }
#projSettings .ps-sec { font-weight: 600; font-size: 11.5px; color: var(--dim, #94949c); margin-top: 6px; }
#projSettings .ps-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
#projSettings label { display: inline-flex; align-items: center; gap: 5px; cursor: pointer; }
#projSettings input[type=radio] { margin: 0; accent-color: var(--accent, #4a9df8); }
#projSettings input[type=number] { width: 72px; height: 24px; box-sizing: border-box; padding: 2px 6px; font: inherit;
  background: var(--bg, #111112); color: var(--text, #dcdcdf); border: 1px solid var(--line-hi, #46464c); border-radius: 3px; }
#projSettings input[type=number]:focus { outline: none; border-color: var(--accent, #4a9df8); }
#projSettings input:disabled { opacity: .5; cursor: default; }
#projSettings .ps-about { color: var(--faint, #77777f); font-size: 11px; line-height: 1.35; }
#projSettings .ps-warn { color: var(--warn, #e0b050); font-size: 11px; }
#projSettings .ps-err { color: var(--bad, #e06a6a); font-size: 11.5px; }
#projSettings .ps-w { display: grid; grid-template-columns: 76px auto 1fr; gap: 4px 10px; align-items: center; }
#projSettings .ps-w .nm { color: var(--text, #dcdcdf); }
#projSettings .ps-step { display: inline-flex; align-items: center; border: 1px solid var(--line-hi, #46464c); border-radius: 3px; overflow: hidden; }
#projSettings .ps-step button { border: 0; border-radius: 0; padding: 0 8px; height: 22px; font: inherit; cursor: pointer;
  background: var(--card-hi, #313135); color: var(--text, #dcdcdf); }
#projSettings .ps-step input[type=number] { width: 38px; height: 22px; text-align: center; border: 0; border-radius: 0;
  border-left: 1px solid var(--line-hi, #46464c); border-right: 1px solid var(--line-hi, #46464c); -moz-appearance: textfield; appearance: textfield; }
#projSettings .ps-step input::-webkit-outer-spin-button, #projSettings .ps-step input::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }
#projSettings .ps-foot { display: flex; align-items: center; gap: 8px; padding: 10px 16px 12px;
  border-top: 1px solid var(--line, #333337); }
#projSettings .ps-grow { flex: 1; }
#projSettings .ps-btn { height: 26px; padding: 0 14px; border-radius: 3px; font: inherit; cursor: pointer;
  background: var(--card-hi, #313135); color: var(--text, #dcdcdf); border: 1px solid var(--line-hi, #46464c); }
#projSettings .ps-btn:hover { border-color: var(--accent, #4a9df8); }
#projSettings .ps-btn:disabled { opacity: .5; cursor: default; }
#projSettings .ps-btn.primary { background: var(--accent, #4a9df8); border-color: var(--accent, #4a9df8);
  color: #08111c; font-weight: 600; min-width: 72px; }
`;

  async function getJSON(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
    return r.json();
  }

  function build() {
    if (S.dlg) return S.dlg;
    if (!$('#projsettings-css')) {
      const st = document.createElement('style');
      st.id = 'projsettings-css';
      st.textContent = CSS;
      document.head.appendChild(st);
    }
    const d = document.createElement('dialog');
    d.id = 'projSettings';
    d.dataset.testid = 'project-settings';
    d.setAttribute('aria-label', 'Project Settings');
    d.innerHTML = `
      <form method="dialog" class="ps" autocomplete="off">
        <div class="ps-head"><span class="ps-title">Project Settings</span><span class="ps-sub" id="psSub"></span></div>
        <div class="ps-body">
          <div class="ps-sec">Budget cap</div>
          <div class="ps-row" role="radiogroup" aria-label="budget cap">
            <label><input type="radio" name="psCap" id="psCapNone" value="none" data-testid="ps-cap-none"> No cap</label>
            <label><input type="radio" name="psCap" id="psCapOn" value="cap" data-testid="ps-cap-on"> Cap this project at $</label>
            <input type="number" id="psCap" min="0.01" step="0.5" inputmode="decimal" placeholder="—"
                   aria-label="budget cap in dollars" data-testid="ps-cap">
          </div>
          <div class="ps-about" id="psCapAbout"></div>
          <div class="ps-about" id="psSpent" data-testid="ps-spent"></div>
          <div class="ps-sec">Index</div>
          <div class="ps-warn" id="psRunning" hidden>The index is running: changes apply to its next run.</div>
          <div class="ps-row" role="radiogroup" aria-label="index order">
            <label title="the sentence's words, what was said and the sensors first"><input type="radio" name="psOrder" value="priority" data-testid="ps-order-priority"> Most promising first</label>
            <label title="a chronological cull"><input type="radio" name="psOrder" value="capture" data-testid="ps-order-capture"> Capture order</label>
          </div>
          <div class="ps-w" id="psWorkers" data-testid="ps-workers"></div>
          <div class="ps-err" id="psErr" role="alert" data-testid="ps-error" hidden></div>
        </div>
        <div class="ps-foot">
          <input type="button" class="ps-btn" id="psReset" data-testid="ps-reset" value="Reset to defaults">
          <span class="ps-grow"></span>
          <input type="button" class="ps-btn" id="psCancel" data-testid="ps-cancel" value="Cancel">
          <input type="button" class="ps-btn primary" id="psOk" data-testid="ps-ok" value="OK">
        </div>
      </form>`;
    $('#psWorkers', d).innerHTML = STAGES.map((st) => `
      <span class="nm" title="${esc(st)}">${esc(NAMES[st])}</span>
      <span class="ps-step" data-stage="${st}">
        <button type="button" data-d="-1" aria-label="fewer ${st} workers" title="fewer">−</button>
        <input type="number" data-stage="${st}" min="${MIN}" max="${MAX}" step="1" inputmode="numeric" aria-label="${st} workers">
        <button type="button" data-d="1" aria-label="more ${st} workers" title="more">+</button></span>
      <span class="ps-about">${esc(WHY[st])}</span>`).join('');
    document.body.appendChild(d);
    d.querySelector('form').addEventListener('submit', (e) => e.preventDefault());
    d.addEventListener('cancel', (e) => { e.preventDefault(); close(); });
    // keys typed in it stay in it (the timeline's keys are on window)
    d.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && e.target && e.target.matches('input[type=number]')) { e.preventDefault(); ok(); }
      if (e.key !== 'Escape') e.stopPropagation();
    });
    d.addEventListener('click', (e) => {
      const step = e.target.closest('.ps-step button[data-d]');
      if (step) {
        const f = step.parentElement.querySelector('input');
        f.value = String(clamp(Number(f.value) + Number(step.dataset.d)));
        err('');
        return;
      }
      const b = e.target.closest('input[type=button]');
      if (!b || b.disabled) return;
      if (b.id === 'psOk') ok();
      else if (b.id === 'psReset') reset();
      else close();
    });
    d.addEventListener('change', (e) => {
      if (e.target.matches('#psWorkers input')) e.target.value = String(clamp(e.target.value));
      if (e.target.id === 'psCapNone' && e.target.checked) $('#psCap', d).value = '';
      if (e.target.id === 'psCapOn' && e.target.checked) $('#psCap', d).focus();
      about();
    });
    // typing a number is choosing a cap
    $('#psCap', d).addEventListener('input', () => { $('#psCapOn', d).checked = true; err(''); about(); });
    S.dlg = d;
    return d;
  }

  function err(text) {
    const el = $('#psErr', S.dlg);
    el.textContent = text || '';
    el.hidden = !text;
  }

  const env = () => !!(S.settings && (S.settings.source || {}).budget_usd === 'env');
  const free = () => !!(window.rcCost && S.backend && rcCost.free(S.backend));

  function about() {
    const d = S.dlg, s = S.settings;
    if (!d || !s) return;
    const capOn = $('#psCapOn', d).checked;
    $('#psCapAbout', d).textContent = env()
      ? 'From the environment: ROUGHCUT_BUDGET_USD wins over a cap saved here.'
      : !capOn
        ? (free() ? 'No cap: free on your plan; every AI button still shows its API-equivalent estimate first.'
          : 'No cap: every button that spends still shows its price first.')
        : free()
          ? 'On this project\'s spend as the API would bill it (nothing is billed on your plan): the index pauses its priced stages at this line, and a model call past it is refused.'
          : 'On this project\'s spend: the index pauses its priced stages at this line, and a model call past it is refused.';
    const amount = `${usd(s.spent_usd)}${s.budget_usd != null ? ` of ${usd(s.budget_usd)} cap` : ''}`;
    const sp = $('#psSpent', d);
    sp.textContent = `This project: ${window.rcCost ? rcCost.total(amount, S.backend || {}) : `spent ${amount}`}`;
    sp.title = window.rcCost ? rcCost.totalNote(s.spent_usd, S.backend || {}) : '';
  }

  function fill(v) {
    const d = S.dlg;
    if (!env()) {
      $('#psCapNone', d).checked = v.budget_usd == null;
      $('#psCapOn', d).checked = v.budget_usd != null;
      $('#psCap', d).value = v.budget_usd == null ? '' : String(v.budget_usd);
    }
    for (const st of STAGES) {
      const f = d.querySelector(`#psWorkers input[data-stage=${st}]`);
      if (f && v.workers && v.workers[st] != null) f.value = String(clamp(v.workers[st]));
    }
    if (ORDER_WORD[v.order]) {
      d.querySelectorAll('input[name=psOrder]').forEach((r) => { r.checked = r.value === v.order; });
    }
  }

  function paint() {
    const d = S.dlg, s = S.settings;
    $('#psRunning', d).hidden = !S.running;
    const locked = env();
    for (const id of ['#psCapNone', '#psCapOn', '#psCap']) $(id, d).disabled = locked || !s;
    if (locked && s) {
      $('#psCapNone', d).checked = s.budget_usd == null;
      $('#psCapOn', d).checked = s.budget_usd != null;
      $('#psCap', d).value = s.budget_usd == null ? '' : String(s.budget_usd);
    }
    $('#psOk', d).disabled = S.busy || !s;
    $('#psReset', d).disabled = S.busy || !s || !s.defaults;
    let bin = '';
    try { bin = ($('#hdBin b') || {}).textContent || ''; } catch (e) { /* none */ }
    $('#psSub', d).textContent = bin ? `Project · ${bin}` : '';
    if (s) about();
  }

  async function open() {
    const d = build();
    err('');
    S.settings = null;
    paint();
    if (!d.open) d.showModal();
    try {
      const [s, st, ix] = await Promise.all([
        getJSON('/api/settings'),
        getJSON('/api/status').catch(() => null),
        getJSON('/api/index').catch(() => null)]);
      S.settings = s;
      S.backend = st && st.backend ? st.backend : null;
      S.running = !!(ix && ix.running);
    } catch (e) {
      err(`the settings could not be read: ${e.message || e}`);
    }
    paint();
    if (S.settings) { fill(S.settings); about(); }
    const first = env() ? d.querySelector('#psWorkers input') : $('#psCapOn', d).checked ? $('#psCap', d) : $('#psCapNone', d);
    if (first && S.settings) first.focus();
    return d;
  }

  function close() {
    if (S.dlg && S.dlg.open) S.dlg.close();
  }

  function reset() {
    const s = S.settings;
    if (!s || !s.defaults) return;
    fill(s.defaults);
    err('');
    about();
    say('the defaults are filled in — OK applies them');
  }

  // what OK changed, in words: "cap none → $5.00 · sheet workers 2 → 4"
  function changes(before, after) {
    const out = [];
    if (capText(before.budget_usd) !== capText(after.budget_usd)) {
      out.push(`cap ${capText(before.budget_usd)} → ${capText(after.budget_usd)}`);
    }
    if (before.order && after.order && before.order !== after.order) {
      out.push(`order ${ORDER_WORD[before.order] || before.order} → ${ORDER_WORD[after.order] || after.order}`);
    }
    for (const st of STAGES) {
      const a = (before.workers || {})[st], b = (after.workers || {})[st];
      if (a !== b) out.push(`${st} workers ${a == null ? '?' : a} → ${b}`);
    }
    return out;
  }

  /* One PUT of the whole form; `body` given is sent as it is (a test sends a bad one on
   * purpose, to see the server's 400 said). */
  async function ok(body) {
    const d = S.dlg, before = S.settings;
    if (S.busy || !before) return null;
    if (!body) {
      body = { workers: {} };
      for (const st of STAGES) {
        const f = d.querySelector(`#psWorkers input[data-stage=${st}]`);
        const n = clamp(f.value);
        f.value = String(n);
        body.workers[st] = n;
      }
      const ord = d.querySelector('input[name=psOrder]:checked');
      if (ord) body.order = ord.value;
      if (!env()) {
        if ($('#psCapNone', d).checked) body.budget_usd = null;
        else {
          const cap = Number($('#psCap', d).value);
          if (!(cap > 0)) { err('The cap must be more than $0, or choose No cap.'); $('#psCap', d).focus(); return null; }
          body.budget_usd = cap;
        }
      }
    }
    S.busy = true;
    paint();
    try {
      const r = await fetch('/api/settings', {
        method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
      const said = changes(before, data);
      S.settings = data;
      close();
      say(said.length ? `Project Settings saved · ${said.join(' · ')}` : 'Project Settings: nothing changed', 4000);
      return data;
    } catch (e) {
      err(`Not saved: ${e.message || e}`);
      return null;
    } finally {
      S.busy = false;
      if (d.open) paint();
    }
  }

  window.rcProjectSettings = { open, close, ok, reset, state: S };
})();
