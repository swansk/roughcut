/* Roughcut — the open screen (docs/design/cutting-room-floor.html §2–§3, INTAKE M5).
 *
 * Know the bin before you pay for it: the folder as a contact sheet — one grid per
 * session in capture order, a card per clip with its first frame, its length and the
 * free flags — the price of looking before the button that spends it, and the journal's
 * word on every clip while the index runs. Framework-free like app.js and floor.js,
 * served from disk; it borrows their patterns by copy.
 *
 * Two actions spend money. **Index the footage** → POST /api/index (decision 3: unattended,
 * resumable, priority-ordered, releasing clips whole). **Propose themes** → POST
 * /api/themes/propose (INTAKE I5.2): one judge-role call over the transcripts, priced
 * before the button, whose answer is chips the editor keeps or discards — nothing from a
 * proposal is the EDL's word until Keep. Everything else here is ffprobe and file checks.
 * While a run is going the page polls GET /api/index every 2 s; the run lives in the
 * server, so closing the tab changes nothing.
 */

'use strict';

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const STAGES = ['probe', 'telemetry', 'asr', 'proxy', 'look', 'close', 'picks'];
const STAGE_LABEL = { telemetry: 'tele' };
const SETTLED = new Set(['done', 'skipped']);
const POLL_MS = 2000;
const THEMES_POLL_MS = 500;      // the proposal is one short call; its job is polled closer
const HOLD_MS = 250;             // V held longer than this in the story field speaks; a tap types
const ORDER_WORD = { priority: 'most promising first', capture: 'capture order' };
// The slider's four stops in words (design §2: "sample interval, 4 s to 1 s"). A sheet
// is 30 frames, so at 4 s one sheet covers two minutes and a jump is a frame or two.
const INTERVAL_WORD = {
  4: 'a frame every 4 s · sees the run, misses the moment',
  3: 'a frame every 3 s · sees the approach',
  2: 'a frame every 2 s · sees the air',
  1: 'every 1 s · sees the landing',
};

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
  interval: null,          // the slider: seconds between frames once the human moves it; null = the project's
  job: null,               // the id of the run this page started or found running
  detail: '',              // the running job's own one-liner ("CLIP_07 · look")
  busy: false,             // a POST in flight — the button is disabled meanwhile
  timer: null,
  polls: 0,                // how many times the page has asked while a run was going
  themes: null,            // /api/themes — the EDL's kept themes + names, the story, the price of proposing
  proposal: null,          // a proposal being edited: {themes:[{theme, why, clips, lines, kept}], names:[{name, kept}]}
  tjob: null,              // the id of the proposal job this page started or found running
  tbusy: false,            // a themes POST/PUT in flight
  ttimer: null,
  story: null,             // the story as last saved to the EDL; null until /api/themes has answered
  dictation: null,         // null until tried; false once the recogniser said 501 (the mic hides)
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

// The slider (design §2, Fig. 1): one control, four stops coarse to fine, re-pricing
// live. `/api/status`'s `visual.by_interval` carries every stop's price for the same
// pending clips, so a move costs no round trip; the chosen stop rides with POST
// /api/index as `interval_s` and the server keeps it in the EDL (`look.interval_s`),
// so after a run the slider shows the project's word, not the page's.

function intervals() {
  const v = O.status && O.status.visual;
  return v && v.intervals && v.intervals.length ? v.intervals.map(Number) : [4, 3, 2, 1];
}

// The project's interval: `/api/index` and `/api/status` both say it (the same EDL field).
function projectInterval() {
  if (O.index && O.index.interval_s != null) return Number(O.index.interval_s);
  const v = O.status && O.status.visual;
  return v && v.interval_s != null ? Number(v.interval_s) : intervals()[0];
}

function interval() {
  return O.interval == null ? projectInterval() : O.interval;
}

function priceAt(i) {
  const v = O.status && O.status.visual;
  if (!v) return null;
  const by = v.by_interval || {};
  if (by[String(i)] != null) return by[String(i)];
  return i === Number(v.interval_s) ? v.projected_usd : null;
}

function renderControls() {
  const v = O.status.visual, b = O.status.backend;
  const pending = (v.pending || []).length;
  const stops = intervals(), i = interval();
  const at = Math.max(0, stops.indexOf(i));
  const el = $('#interval');
  el.max = String(stops.length - 1);
  el.value = String(at);
  $('#stops').innerHTML = stops.map((s, k) => `<span${k === at ? ' class="on"' : ''}>${s} s</span>`).join('');
  $('#intervalWord').textContent = pending ? (INTERVAL_WORD[i] || `a frame every ${i} s`) : '';
  $('#priceLine').innerHTML = pending
    ? `<b>~${usd(priceAt(i))}</b> for the ${plural(pending, 'clip')} not yet looked at`
    : 'every clip has been looked at — nothing left to buy';
  // the sheet count is on the wire only for the project's own interval; the other
  // stops carry their price (by_interval), and the close look is the same at every stop
  const atProject = i === Number(v.interval_s);
  $('#priceDetail').textContent = pending
    ? `${atProject ? plural(v.coarse_calls, 'sheet') : 'sheets'} at a frame every ${i} s, then a close look at up to ${plural(v.fine_calls, 'window')} across ${plural(v.fine_pending, 'clip')}`
    : '';
  const looked = v.done || 0;
  $('#sliderHint').textContent = !pending
    ? `the slider is off — the bin was looked at a frame every ${projectInterval()} s and there is nothing left to re-price`
    : looked
      ? `applies to the ${plural(pending, 'clip')} not yet looked at — the ${plural(looked, 'clip')} already looked at stay as they are`
      : 're-prices live as the thumb moves · the close look after the sheets is the same at every stop';
  $('#budgetLine').textContent = `${usd(b.spent_usd)} of ${usd(b.budget_usd)}`;
  const problems = (b.problems || []).map((p) => `<span class="bad">${escapeHtml(p)}</span>`).join(' ');
  $('#backendLine').innerHTML = `${escapeHtml(b.backend || '')} · ${escapeHtml(b.model || '')}${problems ? ' · ' + problems : ''}`;
  renderButton();
}

function renderButton() {
  const btn = $('#indexBtn');
  const running = !!(O.index && O.index.running);
  const v = O.status && O.status.visual;
  const pending = !!(v && v.pending && v.pending.length);
  const price = pending ? ` · ~${usd(priceAt(interval()))}` : '';
  btn.disabled = running || O.busy || !O.status || !O.index;
  if (running) btn.textContent = 'Indexing… runs on its own';
  else if (O.index && O.index.exists) btn.textContent = `Index what isn't done${price}`;
  else btn.textContent = `Index the footage${price} · runs on its own`;
  for (const el of $$('#order button')) {
    el.classList.toggle('on', el.dataset.order === order());
    el.disabled = running || O.busy;
  }
  // the slider is off while a run is going (it is looking at the project's interval) and
  // once every clip has been looked at (nothing left to re-price)
  $('#interval').disabled = running || O.busy || !pending;
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

/* ---------------------------------------------------------------- themes */

// Listen first, then propose (design §2, Fig. 1). The story is the brief the proposal
// reads; the chips are its answer — one per theme with its clip count, the quoted line
// and the why on hover — and each is a toggle the editor keeps or not. Themes are a brief
// and a filter (INTAKE Decisions): the pass lifts and tags picks that match them and the
// journal's priority counts theme hits; they never score. Nothing from a proposal
// reaches the EDL until Keep, which is one PUT of exactly the ticked ones.

function themePrice() {
  const p = O.themes && O.themes.projected_usd;
  return p == null ? '' : `~${usd(p)}`;
}

function whyOf(t) {
  if (t.clips === null) return t.own ? 'your own — the pass tags what matches it' : 'kept — change to edit';
  const line = (t.lines || [])[0];
  return `${line ? `“${line}” — ` : ''}${t.why || `in ${plural(t.clips.length, 'clip')}`}`;
}

function chipHtml(t, i, cls, attr) {
  const n = t.clips ? ` <span class="n">· ${plural(t.clips.length, 'clip')}</span>` : '';
  return `<button type="button" class="chip${t.kept ? ' on' : ''}${cls ? ` ${cls}` : ''}" ${attr}="${i}" role="checkbox" `
    + `aria-checked="${!!t.kept}" title="${escapeHtml(whyOf(t))}">${t.kept ? '✓' : '+'} ${escapeHtml(t.theme)}${n}</button>`;
}

function nameChips(names, keptOnly) {
  const list = keptOnly ? names.filter((n) => n.kept) : names;
  if (!list.length) return '';
  return '<span class="who">people:</span>' + list.map((n, i) =>
    `<button type="button" class="chip${n.kept ? ' on' : ''}${keptOnly ? ' static' : ''}" data-n="${names.indexOf(n)}" `
    + `role="checkbox" aria-checked="${!!n.kept}">${n.kept ? '✓' : '+'} ${escapeHtml(n.name)}</button>`).join('');
}

function renderChips() {
  const p = O.proposal;
  if (!p) return;
  $('#chips').innerHTML = p.themes.map((t, i) => chipHtml(t, i, '', 'data-i')).join('')
    || '<span class="hint small">nothing proposed — add your own, or discard</span>';
  $('#nameChips').innerHTML = nameChips(p.names, false);
  $('#themesNotes').textContent = p.notes ? `the transcripts' one sentence: ${p.notes}` : '';
  $('#themesNotes').hidden = !p.notes;
  $('#themeWhy').textContent = '';
}

function renderThemes() {
  const t = O.themes;
  if (!t) return;
  if (O.story === null) {                        // first answer: the EDL's story fills the field
    O.story = t.story || '';
    $('#story').value = O.story;
  }
  const analysed = (t.analysed || 0) > 0;
  const kept = t.themes.length > 0 || t.names.length > 0;
  const running = !!O.tjob;
  const editing = !!O.proposal;
  $('#themesRunning').hidden = !running;
  $('#themesEdit').hidden = running || !editing;
  $('#themesKept').hidden = running || editing || !kept;
  $('#themesAsk').hidden = running || editing || kept;
  $('#themesSub').textContent = editing ? (O.proposal.proposed ? 'the transcripts suggest —' : 'editing')
    : kept ? 'kept' : '';
  $('#themesPrice').innerHTML = analysed
    ? `<b>${themePrice()}</b> · one call over the transcripts of ${plural(t.analysed, 'clip')}`
    : 'the audio pass has not listened yet — themes are proposed from the transcripts once it has';
  $('#proposeBtn').disabled = !analysed || O.tbusy;
  $('#againBtn').textContent = `propose again ${themePrice()}`;
  $('#againBtn').disabled = !analysed || O.tbusy;
  $('#keepBtn').disabled = O.tbusy;
  $('#keptChips').innerHTML = t.themes.map((theme, i) =>
    chipHtml({ theme, kept: true, clips: null }, i, 'static', 'data-k')).join('')
    || '<span class="hint small">no themes kept — picks rank on their own</span>';
  $('#keptNames').innerHTML = nameChips(t.names.map((name) => ({ name, kept: true })), true);
  $('[data-step=themes]').classList.toggle('done', kept);
}

function openProposal(p) {
  O.proposal = {
    themes: (p.themes || []).map((x) => ({ theme: x.theme, why: x.why || '', clips: x.clips || [],
                                           lines: x.lines || [], kept: true, own: false })),
    names: (p.names || []).map((name) => ({ name, kept: true })),
    notes: p.notes || '', usage: p.usage || null, proposed: true,
  };
  renderChips();
  renderThemes();
}

// "change": the kept ones come back as chips, ticked, with no counts — the counts were
// the proposal's and the EDL keeps only the words.
function openKept() {
  const t = O.themes;
  O.proposal = {
    themes: t.themes.map((theme) => ({ theme, why: '', clips: null, lines: [], kept: true, own: false })),
    names: t.names.map((name) => ({ name, kept: true })),
    notes: '', usage: null, proposed: false,
  };
  renderChips();
  renderThemes();
}

async function proposeThemes() {
  if (O.tjob || O.tbusy) return;
  O.tbusy = true;
  renderThemes();
  try {
    await saveStory();
    const r = await send('POST', '/api/themes/propose', { story: $('#story').value });
    O.tjob = r.job;
    O.proposal = null;
    $('#themesRunning').textContent = 'listening… reading the transcripts';
  } catch (e) {
    toast(String(e.message || e), 5000);
  } finally {
    O.tbusy = false;
    renderThemes();
  }
  if (O.tjob) pollThemes();
}

async function pollThemes() {
  clearTimeout(O.ttimer);
  if (!O.tjob) return;
  let s;
  try {
    s = await getJSON(`/api/job/${O.tjob}`);
  } catch (e) {
    O.tjob = null;
    renderThemes();
    return toast(`the proposal was lost: ${e.message}`, 5000);
  }
  if (s.state === 'running') {
    $('#themesRunning').textContent = `listening… ${s.detail || ''}`;
    O.ttimer = setTimeout(pollThemes, THEMES_POLL_MS);
    return;
  }
  O.tjob = null;
  if (s.state === 'done' && s.proposal) {
    openProposal(s.proposal);
    const u = s.proposal.usage || {};
    toast(`${plural(O.proposal.themes.length, 'theme')} proposed${u.projected_usd != null ? ` · ${usd(u.projected_usd)}` : ''} — keep what fits`);
  } else {
    toast(`no themes: ${s.detail || s.state}`, 6000);
    renderThemes();
  }
}

async function keepThemes() {
  const p = O.proposal;
  if (!p || O.tbusy) return;
  O.tbusy = true;
  renderThemes();
  const body = {
    themes: p.themes.filter((t) => t.kept).map((t) => t.theme),
    names: p.names.filter((n) => n.kept).map((n) => n.name),
    story: $('#story').value,
  };
  try {
    const r = await send('PUT', '/api/themes', body);
    O.themes.themes = r.themes || [];
    O.themes.names = r.names || [];
    O.story = body.story;
    O.proposal = null;
    toast(r.themes.length ? `kept ${plural(r.themes.length, 'theme')} — the pass lifts and tags what matches`
                          : 'no themes kept — picks rank on their own');
  } catch (e) {
    toast(`could not keep the themes: ${e.message}`, 5000);
  } finally {
    O.tbusy = false;
    renderThemes();
  }
}

function discardThemes() {
  if (!O.proposal) return;
  O.proposal = null;
  renderThemes();
  toast('discarded — nothing was written');
}

function addTheme(text) {
  const s = String(text || '').trim().slice(0, 60);
  if (!s || !O.proposal) return;
  const p = O.proposal;
  const had = p.themes.find((t) => t.theme.toLowerCase() === s.toLowerCase());
  if (had) had.kept = true;
  else p.themes.push({ theme: s, why: '', clips: null, lines: [], kept: true, own: true });
  renderChips();
}

// The story is the EDL's brief (the ask reads it too); it is saved on its own when the
// field settles, so a brief typed and never proposed on survives a reload.
async function saveStory() {
  const s = $('#story').value;
  if (O.story === null || s === O.story) return;
  try {
    await send('PUT', '/api/themes', { story: s });
    O.story = s;
  } catch (e) {
    toast(`could not save the story: ${e.message}`, 4000);
  }
}

function landStory(text) {
  const el = $('#story');
  const cur = el.value.trim();
  el.value = cur ? `${cur} ${text}` : text;
  saveStory();
}

/* --------------------------------------------------------------- dictation */

// The floor's pattern (I2.5), by copy: hold → MediaRecorder → POST /api/dictate with the
// Blob as the body → the text lands in the field on release. A 501 hides the mic and
// leaves typing; a 413 is said in a toast.

const dict = { rec: null, stream: null, chunks: [], held: false, pending: null };

function dictSay(text, live) {
  $('#dictState').textContent = text;
  $('#dictState').className = live ? 'hint small live' : 'hint small';
  $('#mic').classList.toggle('live', !!live);
  $('#story').classList.toggle('live', !!live);
}

function dictGone(why) {
  O.dictation = false;
  $('#mic').hidden = true;
  dictSay('');
  toast(`${why} — type instead`, 4000);
}

async function dictStart() {
  if (dict.held || O.dictation === false) return;
  dict.held = true;
  dictSay('listening…', true);
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    dictStop();
    return dictGone('no microphone in this browser');
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    dictStop();
    return dictGone(`no microphone — ${e.name || e}`);
  }
  if (!dict.held) {                                // released before the mic answered
    stream.getTracks().forEach((t) => t.stop());
    return;
  }
  const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : '';
  dict.stream = stream;
  dict.chunks = [];
  dict.rec = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
  dict.rec.ondataavailable = (e) => { if (e.data && e.data.size) dict.chunks.push(e.data); };
  dict.rec.onstop = () => {
    const blob = new Blob(dict.chunks, { type: mime || 'audio/webm' });
    stream.getTracks().forEach((t) => t.stop());
    dict.rec = null;
    dict.stream = null;
    dictSend(blob);
  };
  dict.rec.start();
}

function dictStop() {
  if (!dict.held) return;
  dict.held = false;
  if (dict.rec && dict.rec.state !== 'inactive') dict.rec.stop();
  else dictSay('');
}

async function dictSend(blob) {
  dictSay('transcribing…');
  let r;
  try {
    r = await fetch('/api/dictate', {
      method: 'POST', headers: { 'content-type': blob.type || 'audio/webm' }, body: blob,
    });
  } catch (e) {
    return dictGone('dictation unreachable');
  }
  if (r.status === 501) return dictGone('dictation not built yet');
  const d = await r.json().catch(() => ({}));
  if (r.status === 413) {
    dictSay('');
    return toast(`too long — ${d.detail || 'a brief is at most 30 s'}`, 5000);
  }
  if (!r.ok) {
    dictSay('');
    return toast(`dictation failed: ${d.detail || r.status}`, 5000);
  }
  if (!d.text) {
    dictSay('');
    return toast('heard nothing — type instead', 3000);
  }
  landStory(d.text);
  dictSay(`heard in ${((d.latency_ms || 0) / 1000).toFixed(1)} s`);
  clearTimeout(dict.said);
  dict.said = setTimeout(() => { if (!dict.held) dictSay(''); }, 3000);
}

// V: held anywhere outside an input it speaks, like the floor. In the story field a tap
// types the letter and a hold speaks — "very" must still be typeable.
function typeInStory(ch) {
  const el = $('#story');
  el.setRangeText(ch, el.selectionStart, el.selectionEnd, 'end');
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

function isTyping(el) {
  return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA');
}

function onKeyDown(e) {
  if (e.key !== 'v' || e.ctrlKey || e.metaKey || e.altKey) return;
  const el = document.activeElement;
  const inStory = el === $('#story');
  if (isTyping(el) && !inStory) return;
  if (O.dictation === false) return;               // the mic is gone: v is a letter again
  e.preventDefault();
  if (e.repeat) return;
  if (inStory) {
    clearTimeout(dict.pending);
    dict.pending = setTimeout(() => { dict.pending = null; dictStart(); }, HOLD_MS);
  } else {
    dictStart();
  }
}

function onKeyUp(e) {
  if (e.key !== 'v' && e.key !== 'V') return;
  if (dict.pending) {
    clearTimeout(dict.pending);
    dict.pending = null;
    if (document.activeElement === $('#story')) typeInStory('v');
    return;
  }
  dictStop();
}

/* --------------------------------------------------------------- actions */

async function startIndex(extra = {}) {
  if (O.busy) return;
  O.busy = true;
  renderButton();
  try {
    // the slider's word rides along; the server keeps it in the EDL, so from here the
    // slider follows the project's interval rather than the page's
    const r = await send('POST', '/api/index', { order: order(), interval_s: interval(), ...extra });
    O.job = r.job;
    O.interval = null;
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

async function refreshThemes() {
  O.themes = await getJSON('/api/themes');
  if (O.themes.job && !O.tjob) {                   // a proposal started elsewhere: pick it up
    O.tjob = O.themes.job;
    pollThemes();
  }
  renderThemes();
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
    // themes too: the price of proposing grows as the asr stage hears more clips
    await Promise.all([refreshIndex(), refreshClips(), refreshStatus(), refreshThemes()]);
  } catch (e) {
    toast(String(e.message || e), 4000);
  }
  if (O.index && O.index.running) poll();
  else if (O.detail) $('#indexDetail').textContent = O.detail;
}

function wireThemes() {
  $('#proposeBtn').addEventListener('click', proposeThemes);
  $('#againBtn').addEventListener('click', proposeThemes);
  $('#keepBtn').addEventListener('click', keepThemes);
  $('#discardBtn').addEventListener('click', discardThemes);
  $('#changeBtn').addEventListener('click', openKept);
  $('#chips').addEventListener('click', (e) => {
    const c = e.target.closest('.chip');
    if (!c || !O.proposal) return;
    const t = O.proposal.themes[Number(c.dataset.i)];
    if (!t) return;
    t.kept = !t.kept;
    renderChips();
  });
  $('#chips').addEventListener('mouseover', (e) => {
    const c = e.target.closest('.chip');
    if (!c || !O.proposal) return;
    const t = O.proposal.themes[Number(c.dataset.i)];
    if (t) $('#themeWhy').textContent = whyOf(t);
  });
  $('#chips').addEventListener('focusin', (e) => {
    const c = e.target.closest('.chip');
    const t = c && O.proposal && O.proposal.themes[Number(c.dataset.i)];
    if (t) $('#themeWhy').textContent = whyOf(t);
  });
  $('#nameChips').addEventListener('click', (e) => {
    const c = e.target.closest('.chip');
    if (!c || !O.proposal) return;
    const n = O.proposal.names[Number(c.dataset.n)];
    if (!n) return;
    n.kept = !n.kept;
    renderChips();
  });
  $('#addTheme').addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    addTheme(e.target.value);
    e.target.value = '';
  });
  $('#story').addEventListener('change', saveStory);
  const mic = $('#mic');
  mic.addEventListener('pointerdown', (e) => { e.preventDefault(); dictStart(); });
  for (const ev of ['pointerup', 'pointercancel', 'pointerleave']) mic.addEventListener(ev, dictStop);
  document.addEventListener('keydown', onKeyDown);
  document.addEventListener('keyup', onKeyUp);
  window.addEventListener('blur', () => { clearTimeout(dict.pending); dict.pending = null; dictStop(); });
}

async function boot() {
  $('#indexBtn').addEventListener('click', () => startIndex());
  $('#resume').addEventListener('click', () => startIndex({ resume_priced: true }));
  for (const el of $$('#order button')) {
    el.addEventListener('click', () => { O.order = el.dataset.order; renderButton(); });
  }
  $('#interval').addEventListener('input', (e) => {
    O.interval = intervals()[Number(e.target.value)] ?? null;
    if (O.status) renderControls();
  });
  wireThemes();
  try {
    await Promise.all([refreshClips(), refreshStatus(), refreshIndex(), refreshThemes()]);
    if (O.index.running) poll();
  } catch (e) {
    $('#binName').textContent = 'could not open the folder';
    toast(String(e.message || e), 6000);
  }
}

window.sheet = { state: O, refresh: tick, journalWord, order, interval, renderControls,
                 renderThemes, proposeThemes, keepThemes, dictSend, dictStart, dictStop };
boot();
