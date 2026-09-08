/* Roughcut — the open screen (docs/design/cutting-room-floor.html §2–§3, INTAKE M5).
 *
 * Know the bin before you pay for it: the folder as a contact sheet — one grid per
 * session in capture order, a card per clip with its first frame, its length and the
 * free flags — the price of looking before the button that spends it, and the journal's
 * word on every clip while the index runs. Framework-free like app.js and floor.js,
 * served from disk; it borrows their patterns by copy.
 *
 * One action spends money: **Index the footage** → POST /api/index (decision 3: unattended,
 * resumable, priority-ordered, releasing clips whole). Everything else here is ffprobe
 * and file checks. While a run is going the page polls GET /api/index every 2 s; the run
 * lives in the server, so closing the tab changes nothing.
 */

'use strict';

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const STAGES = ['probe', 'telemetry', 'asr', 'proxy', 'look', 'close', 'picks'];
const STAGE_LABEL = { telemetry: 'tele' };
const SETTLED = new Set(['done', 'skipped']);
const POLL_MS = 2000;
const ORDER_WORD = { priority: 'most promising first', capture: 'capture order' };

const clock = (s) => {
  s = Math.max(0, Math.round(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return (h ? `${h}:${String(m).padStart(2, '0')}` : String(m)) + `:${String(r).padStart(2, '0')}`;
};
const dayOf = (epoch) => epoch
  ? new Date(epoch * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  : 'undated';
const timeOf = (epoch) => new Date(epoch * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
const usd = (x) => `$${Number(x || 0).toFixed(2)}`;

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function toast(msg, ms = 2600) {
  const el = $('#toast');
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

async function send(method, url, body) {
  const r = await fetch(url, {
    method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
  return data;
}

/* ------------------------------------------------------------------ state */

const O = {
  clips: null,             // /api/clips — the folder, with the journal's word when there is one
  status: null,            // /api/status — the backend's budget and the look pass's price
  index: null,             // /api/index — the journal's progress, whether a run is going
  order: null,             // the toggle: 'priority' | 'capture'; null until the journal or the human says
  job: null,               // the id of the run this page started or found running
  detail: '',              // the running job's own one-liner ("CLIP_07 · look")
  busy: false,             // a POST in flight — the button is disabled meanwhile
  timer: null,
  polls: 0,                // how many times the page has asked while a run was going
};

/* ------------------------------------------------------------ the sheet */

// The journal's word on a clip, as one badge on its picture. Derived from the stage
// states the same way journal.progress() derives a row's `state`, so the card and the
// table never disagree; `retrying` is a failed stage the journal will run again (Fig. 2's
// "retrying · encode failed twice").
function journalWord(j) {
  if (!j) return null;
  if (j.missing) return 'missing';
  if (j.parked) return 'parked';
  const states = STAGES.map((s) => j.stages[s]);
  if (states.every((s) => SETTLED.has(s))) return 'released';
  if (states.includes('running')) return 'indexing';
  if (states.includes('failed')) return 'retrying';
  return 'queued';
}

function flagsOf(c) {
  const out = [];
  out.push(c.analysed ? '<i class="flag on">listened</i>' : '<i class="flag off">not yet</i>');
  if (c.telemetry === true) out.push('<i class="flag tele">telemetry</i>');
  else if (c.telemetry === false) out.push('<i class="flag">no telemetry</i>');
  else out.push('<i class="flag" title="the file could not be probed">telemetry ?</i>');
  if (c.looked) out.push('<i class="flag on">looked</i>');
  if (c.released) out.push('<i class="flag good">released</i>');
  return out.join('');
}

function cardHtml(c) {
  const word = journalWord(c.journal);
  const reason = c.journal && c.journal.parked && c.journal.parked.error;
  const picture = c.proxy
    ? `<img src="${escapeHtml(c.poster)}" loading="lazy" alt="" onerror="this.replaceWith(Object.assign(document.createElement('div'), {className: 'ph', textContent: 'no preview'}))">`
    : '<div class="ph">no preview yet</div>';
  return `<div class="card${word ? ` ${word}` : ''}" data-clip="${escapeHtml(c.clip)}"${word ? ` data-word="${word}"` : ''}>
    <div class="frame">${picture}<span class="tc tnum">${clock(c.duration)}</span>${
      word ? `<span class="badge ${word}"${reason ? ` title="${escapeHtml(reason)}"` : ''}>${word}</span>` : ''}</div>
    <div class="cap"><b title="${escapeHtml(c.clip)}">${escapeHtml(c.stem)}</b><span class="flags">${flagsOf(c)}</span></div>
  </div>`;
}

function renderSheet() {
  const d = O.clips;
  const clips = d.clips.slice().sort((a, b) => (a.captured || 0) - (b.captured || 0));
  const name = String(d.footage).split(/[\\/]/).filter(Boolean).pop() || d.footage;
  $('#binName').textContent = name;
  $('#hdBin').textContent = name;
  const withTele = clips.filter((c) => c.telemetry === true).length;
  $('#binMeta').textContent = `${plural(clips.length, 'clip')} · ${clock(d.total_s)} · ${plural(d.sessions, 'session')}`;
  $('#binTele').textContent = clips.length ? `telemetry ${withTele}/${clips.length}` : '';
  $('#empty').hidden = clips.length > 0;

  const bySession = new Map();
  for (const c of clips) {
    const k = c.session == null ? 0 : c.session;
    if (!bySession.has(k)) bySession.set(k, []);
    bySession.get(k).push(c);
  }
  $('#sessions').innerHTML = [...bySession.entries()].map(([k, cs]) => {
    const secs = cs.reduce((t, c) => t + (c.duration || 0), 0);
    const day = dayOf(cs[0].captured);
    return `<div class="session" data-session="${k}">
      <div class="lbl"><span>session ${k} · ${escapeHtml(day)} · ${plural(cs.length, 'clip')} · ${clock(secs)}</span></div>
      <div class="grid">${cs.map(cardHtml).join('')}</div>
    </div>`;
  }).join('');

  // the step strip: listened when every clip has been heard; the pass opens on what is released
  const heard = clips.length > 0 && clips.every((c) => c.analysed);
  $('[data-step=listen]').classList.toggle('done', heard);
  const released = clips.filter((c) => c.released).length;
  const pass = $('#stepPass');
  pass.setAttribute('aria-disabled', released ? 'false' : 'true');
  pass.textContent = released ? `5 the pass · ${plural(released, 'clip')} →` : '5 the pass';
  const link = $('#openPass');
  link.setAttribute('aria-disabled', released ? 'false' : 'true');
  link.textContent = released ? `Open the pass on ${plural(released, 'clip')} →` : 'Open the pass →';
  $('#passHint').textContent = released
    ? (released < clips.length ? 'the rest keep indexing; new clips join as a round when they are released'
                               : 'every clip is released')
    : 'nothing released yet — the pass opens on the first clip that is';
}

/* ------------------------------------------------- the price and the budget */

function renderControls() {
  const v = O.status.visual, b = O.status.backend;
  const pending = (v.pending || []).length;
  $('#priceLine').innerHTML = pending
    ? `<b>~${usd(v.projected_usd)}</b> for the ${plural(pending, 'clip')} not yet looked at`
    : 'every clip has been looked at — nothing left to buy';
  $('#priceDetail').textContent = pending
    ? `${plural(v.coarse_calls, 'sheet')} at a frame every 4 s, then a close look at up to ${plural(v.fine_calls, 'window')} across ${plural(v.fine_pending, 'clip')}`
    : '';
  $('#budgetLine').textContent = `${usd(b.spent_usd)} of ${usd(b.budget_usd)}`;
  const problems = (b.problems || []).map((p) => `<span class="bad">${escapeHtml(p)}</span>`).join(' ');
  $('#backendLine').innerHTML = `${escapeHtml(b.backend || '')} · ${escapeHtml(b.model || '')}${problems ? ' · ' + problems : ''}`;
  renderButton();
}

function renderButton() {
  const btn = $('#indexBtn');
  const running = !!(O.index && O.index.running);
  const v = O.status && O.status.visual;
  const price = v && v.pending.length ? ` · ~${usd(v.projected_usd)}` : '';
  btn.disabled = running || O.busy || !O.status || !O.index;
  if (running) btn.textContent = 'Indexing… runs on its own';
  else if (O.index && O.index.exists) btn.textContent = `Index what isn't done${price}`;
  else btn.textContent = `Index the footage${price} · runs on its own`;
  for (const el of $$('#order button')) {
    el.classList.toggle('on', el.dataset.order === order());
    el.disabled = running || O.busy;
  }
}

function order() {
  if (O.order) return O.order;
  if (O.index && O.index.exists && ORDER_WORD[O.index.order]) return O.index.order;
  return 'priority';
}

/* -------------------------------------------------------------- the index */

function chip(stage, state, attempts) {
  const cls = state === 'done' ? 'ok' : state === 'running' ? 'run'
    : (state === 'failed' || state === 'parked') ? 'bad' : state === 'skipped' ? 'skip' : '';
  let label = STAGE_LABEL[stage] || stage;
  if (state === 'skipped' && stage === 'telemetry') label = 'no tele';
  else if (state === 'skipped') label = `${label} · skipped`;
  if (attempts && attempts > 1 && state !== 'done') label += ` ✕ ${attempts}`;
  return `<span class="stg ${cls}" title="${stage}: ${state}">${label}</span>`;
}

function rowState(r) {
  if (r.state === 'indexing') {
    const running = STAGES.find((s) => r.stages[s] === 'running');
    return { cls: 'indexing', text: running ? `${STAGE_LABEL[running] || running}…` : 'indexing' };
  }
  if (r.state === 'parked') return { cls: 'parked', text: `parked · ${r.error || 'failed three times'}` };
  if (r.state === 'missing') return { cls: 'missing', text: 'missing — the file is gone' };
  if (r.state === 'queued' && STAGES.some((s) => r.stages[s] === 'failed')) {
    return { cls: 'queued', text: `retrying · ${r.error || 'a stage failed'}` };
  }
  return { cls: r.state, text: r.state };
}

function renderIndex() {
  const ix = O.index;
  const running = !!ix.running;
  $('#progress').hidden = !ix.exists;
  $('#index').hidden = !ix.exists;
  $('#paused').hidden = !(ix.exists && ix.paused_priced);
  $('[data-step=index]').classList.toggle('now', running);
  renderButton();
  if (!ix.exists) return;

  const p = ix.progress;
  const orderWord = ORDER_WORD[ix.order] || ix.order;
  const counts = { released: 0, indexing: 0, queued: 0, parked: 0, missing: 0 };
  for (const r of p.rows) counts[r.state] = (counts[r.state] || 0) + 1;

  // the table (Fig. 2): clip · stages as chips · priority · state, in the journal's order
  $('#indexTitle').textContent = `${running ? 'Indexing' : p.released === p.clips - p.missing ? 'Indexed' : 'Index paused'} · ${orderWord}`;
  $('#indexCounts').textContent = Object.entries(counts).filter(([, n]) => n)
    .map(([k, n]) => `${n} ${k}`).join(' · ');
  $('#indexDetail').textContent = running ? O.detail : '';
  $('#rows').innerHTML = '<span class="lbl">clip</span><span class="lbl">stages</span><span class="lbl">priority</span><span class="lbl">state</span>'
    + p.rows.map((r) => {
      const st = rowState(r);
      return `<span class="clip" title="${escapeHtml(r.clip)}">${escapeHtml(String(r.clip).replace(/\.[^.]+$/, ''))}</span>`
        + `<span>${STAGES.map((s) => chip(s, r.stages[s], r.attempts && r.attempts[s])).join('')}</span>`
        + `<span class="tnum">${r.priority == null ? '—' : Number(r.priority).toFixed(2)}</span>`
        + `<span class="st ${st.cls}" title="${escapeHtml(st.text)}">${escapeHtml(st.text)}</span>`;
    }).join('');

  // the overall block: bar, released n of N, cost, ETA — all the journal's own numbers
  const prog = $('#progress');
  prog.classList.toggle('running', running);
  prog.classList.toggle('done', !running);
  $('#progCount').textContent = `${p.released} of ${p.clips - p.missing} released`;
  $('#progBar').style.width = `${Math.max(0, Math.min(100, p.pct || 0))}%`;
  const eta = p.eta_s != null ? `about ${clock(p.eta_s)} left`
    : running ? 'ETA once a stage has been timed' : 'not running';
  const parked = p.parked ? ` · <span class="bad">${plural(p.parked, 'clip')} parked</span>` : '';
  const paused = p.paused_priced ? ' · <span class="warn">priced stages paused</span>' : '';
  $('#progMeta').innerHTML = `${p.pct || 0}% of stages · <span class="warn">${usd(p.cost_usd)}</span> spent by the index · ${eta}${parked}${paused}`;
  $('#pausedWhy').textContent = p.paused_reason || 'paused';
  $('#journalPath').textContent = ix.path || '';
  $('#journalLog').innerHTML = (p.log || []).slice(-5).map((e) =>
    `<div><b>${escapeHtml(timeOf(e.at))}</b>${escapeHtml(e.what)}</div>`).join('');
}

/* --------------------------------------------------------------- actions */

async function startIndex(extra = {}) {
  if (O.busy) return;
  O.busy = true;
  renderButton();
  try {
    const r = await send('POST', '/api/index', { order: order(), ...extra });
    O.job = r.job;
    O.detail = '';
    toast(extra.resume_priced ? 'priced stages resumed — the cap is checked again before each one'
                              : 'indexing — it runs on its own; close the tab and it keeps going');
    await refreshIndex();
    poll();
  } catch (e) {
    toast(String(e.message || e), 5000);
  } finally {
    O.busy = false;
    renderButton();
  }
}

/* ----------------------------------------------------------------- polling */

async function refreshClips() {
  O.clips = await getJSON('/api/clips');
  renderSheet();
}

async function refreshStatus() {
  O.status = await getJSON('/api/status');
  renderControls();
}

async function refreshIndex() {
  O.index = await getJSON('/api/index');
  if (O.index.job) O.job = O.index.job;
  if (O.job) {
    // the run's own one-liner ("CLIP_07 · look", "budget cap reached — …") lives on the job
    try { O.detail = (await getJSON(`/api/job/${O.job}`)).detail || ''; } catch (e) { /* the job may be gone */ }
  }
  renderIndex();
}

function poll() {
  clearTimeout(O.timer);
  O.timer = setTimeout(tick, POLL_MS);
}

async function tick() {
  O.polls++;
  try {
    await Promise.all([refreshIndex(), refreshClips(), refreshStatus()]);
  } catch (e) {
    toast(String(e.message || e), 4000);
  }
  if (O.index && O.index.running) poll();
  else if (O.detail) $('#indexDetail').textContent = O.detail;
}

async function boot() {
  $('#indexBtn').addEventListener('click', () => startIndex());
  $('#resume').addEventListener('click', () => startIndex({ resume_priced: true }));
  for (const el of $$('#order button')) {
    el.addEventListener('click', () => { O.order = el.dataset.order; renderButton(); });
  }
  try {
    await Promise.all([refreshClips(), refreshStatus(), refreshIndex()]);
    if (O.index.running) poll();
  } catch (e) {
    $('#binName').textContent = 'could not open the folder';
    toast(String(e.message || e), 6000);
  }
}

window.sheet = { state: O, refresh: tick, journalWord, order };
boot();
