/* Roughcut — the open screen (docs/design/cutting-room-floor.html §2–§3, INTAKE M5, M16).
 *
 * Know the bin before you pay for it: the folder as a contact sheet — one grid per day
 * in capture order, a card per clip with a picture from the middle of it, its length and
 * its name — and the price of looking on the button that spends it. Framework-free like
 * app.js and floor.js, served from disk; it borrows their patterns by copy.
 *
 * Two states (INTAKE M16 I16.2, "take things away"). **Setup** — no index yet, new clips,
 * or a stage still to run: the sentence, "Looks at a frame every 4 s · change" (the
 * slider opens on change and re-prices the button), and **Index the footage · ~$X**,
 * which becomes one progress line once clicked. **Done**: a headline, the pictures, a
 * badge on a card only when something is wrong with it (not heard yet · look paused ·
 * parked · missing · junk? answered on the card), and one line on how it was indexed that
 * opens to the per-clip table and the journal. While a run is going the page polls
 * GET /api/index every 2 s; the run lives in the server, so closing the tab changes
 * nothing.
 *
 * One action spends money: the Index button → POST /api/index (decision 3: unattended,
 * resumable, priority-ordered, releasing clips whole), or Resume when the cap or the
 * editor paused the looks. The themes step is gone (M16 decision 8): the sentence is the
 * brief, and its words tag moments for free (roughcut/picks.py). Everything else here is
 * ffprobe and file checks.
 *
 * The bin is something you point at, not a launch argument (INTAKE I5.4): the name in
 * the header opens the switcher (/switcher.js), and this page reloads all of its data
 * for the new bin through `window.roughcutReopen`.
 *
 * The order, the workers and the budget cap live in settings, behind the header's
 * settings button (or `,`): GET/PUT /api/settings for the cap and the workers, the order
 * riding with the next POST /api/index. No cap is the default (INTAKE M16 decision 7);
 * a cap, when chosen, is on this project's spend — the one money number on this page —
 * and is the line the index pauses its priced stages at. A cap from the environment
 * (ROUGHCUT_BUDGET_USD) wins over the one saved here, so the choice is disabled when the
 * server says so. Workers are one small stepper per stage, 1–8, applied to the next run.
 */

'use strict';

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const STAGES = ['probe', 'telemetry', 'asr', 'proxy', 'look', 'close', 'picks'];
const STAGE_LABEL = { telemetry: 'tele' };
const SETTLED = new Set(['done', 'skipped']);
const POLL_MS = 2000;
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
// The stages a worker count applies to, in the order the index runs them, and why the
// default is what it is — in words, one line each (design §3: "workers apply to previews
// first, sheets second; the CLI backend's request-rate ceiling is the sheet limit").
const WORKER_STAGES = ['probe', 'asr', 'proxy', 'sheet', 'picks'];
const WORKER_WORD = {
  probe: 'two — ffprobe is quick; more just contend for the disk',
  asr: 'one — the model loads once per run and walks the folder',
  proxy: 'one — two 4K encodes share the same cores',
  sheet: 'two sheets in flight at once — more spends faster, not better',
  picks: 'one — a call per released clip; the sheet limit is the ceiling that matters',
};
const WORKERS_MIN = 1, WORKERS_MAX = 8;

const clock = (s) => {
  s = Math.max(0, Math.round(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return (h ? `${h}:${String(m).padStart(2, '0')}` : String(m)) + `:${String(r).padStart(2, '0')}`;
};
const dayOf = (epoch) => epoch
  ? new Date(epoch * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  : 'undated';
const dayKey = (epoch) => (epoch ? new Date(epoch * 1000).toDateString() : '');
const timeOf = (epoch) => new Date(epoch * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
const usd = (x) => `$${Number(x || 0).toFixed(2)}`;
// The one money number (INTAKE M16 I16.0g): this project's spend, and the cap only when
// there is one.
const spentLine = (b) => (b && b.budget_usd != null
  ? `${usd(b.spent_usd)} of ${usd(b.budget_usd)} cap` : usd(b && b.spent_usd));
const capText = (c) => (c == null ? 'none' : usd(c));

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
  order: null,             // settings' toggle: 'priority' | 'capture'; null until the journal or the human says
  interval: null,          // the slider: seconds between frames once the human moves it; null = the project's
  job: null,               // the id of the run this page started or found running
  detail: '',              // the running job's own one-liner ("CLIP_07 · look")
  busy: false,             // a POST in flight — the button is disabled meanwhile
  timer: null,
  polls: 0,                // how many times the page has asked while a run was going
  brief: null,             // /api/themes — the EDL's sentence about the film, whether dictation is installed
  story: null,             // the sentence as last saved to the EDL; null until /api/themes has answered
  dictation: null,         // null until tried; false once the recogniser said 501 (the mic hides)
  lookOpen: false,         // the slider, behind "change"
  howOpen: false,          // the table and the journal, behind the how-it-was-indexed line
  days: [],                // the days the bin was shot on, for the headline
  keysOpen: false,         // the keys, behind ?
  settings: null,          // /api/settings — the cap, the spend, the workers, the defaults, the cap's source
  sgone: false,            // the server answered the settings GET with an error (no endpoint yet)
  sopen: false,            // the drawer is open
  sbusy: false,            // a settings PUT in flight
};

/* ------------------------------------------------------------ the sheet */

// The journal's word on a clip, derived from the stage states the same way
// journal.progress() derives a row's `state`, so the card and the table never disagree;
// `retrying` is a failed stage the journal will run again (Fig. 2's "retrying · encode
// failed twice"). `waiting` (INTAKE M14): the free stages are done and only the paused
// priced ones are left — the pass already shows the clip from its words.
const PRICED = new Set(['look', 'close']);

function journalWord(j, paused) {
  if (!j) return null;
  if (j.missing) return 'missing';
  if (j.parked) return 'parked';
  const states = STAGES.map((s) => j.stages[s]);
  if (states.every((s) => SETTLED.has(s))) return 'released';
  if (states.includes('running')) return 'indexing';
  if (states.includes('failed')) return 'retrying';
  if (paused && STAGES.every((s) => PRICED.has(s) || s === 'picks' || SETTLED.has(j.stages[s]))) {
    return 'waiting';
  }
  return 'queued';
}

// Where the page is (I16.2): one progress line while a run goes; setup while there is
// something to index — no journal yet, a clip the journal has not met, a stage queued or
// failed; done otherwise. A pause is not setup: the paused box asks for its own word.
function phase() {
  const ix = O.index, d = O.clips;
  if (!ix || !d) return 'loading';
  if (ix.running) return 'running';
  if (!ix.exists) return 'setup';
  return d.clips.some((c) => ['queued', 'retrying', null].includes(journalWord(c.journal, d.paused_priced)))
    ? 'setup' : 'done';
}

// A badge only when something is wrong with the clip (M16 decision 1) — one, the worst.
// A new bin is not wrong, only not indexed yet: no badges until the index has run.
function badgeOf(c) {
  const d = O.clips, ix = O.index;
  const word = journalWord(c.journal, d && d.paused_priced);
  if (word === 'missing') return { cls: 'missing', text: 'missing', title: 'the file is gone' };
  if (word === 'parked') {
    return { cls: 'parked', text: 'parked', title: (c.journal.parked && c.journal.parked.error) || 'failed three times' };
  }
  if (c.junk === 'proposed') return { cls: 'junk', text: 'junk?', title: 'black, one flat field or a blip with no words' };
  if (c.junk === 'confirmed') return { cls: 'junk', text: 'junk', title: 'out of the Ask and Find; its look is skipped' };
  if (word === 'waiting') return { cls: 'waiting', text: 'look paused', title: 'on the pass from its words; its look waits' };
  if (ix && ix.exists && !ix.running && !c.analysed) return { cls: 'unheard', text: 'not heard yet', title: '' };
  return null;
}

// The picture from the middle of the clip: a first frame is often the lens cap or the
// pocket (Killington's CLIP_12 is black at 0 s). The poster URL carries its own time.
function midPoster(c) {
  const t = ((c.duration || 0) / 2).toFixed(2);
  const url = String(c.poster || '');
  return /[?&]t=/.test(url) ? url.replace(/([?&]t=)[^&]*/, `$1${t}`) : `${url}${url.includes('?') ? '&' : '?'}t=${t}`;
}

function cardHtml(c) {
  const word = journalWord(c.journal, O.clips && O.clips.paused_priced);
  const badge = badgeOf(c);
  const picture = c.proxy
    ? `<img src="${escapeHtml(midPoster(c))}" loading="lazy" alt="" onerror="this.replaceWith(Object.assign(document.createElement('div'), {className: 'ph'}))">`
    : '<div class="ph"></div>';
  const answer = c.junk === 'proposed'
    ? '<button type="button" data-junk="junk" title="junk: out of the Ask, Find and the bin; the index skips its look">Junk</button>'
      + '<button type="button" data-junk="keep" title="not junk: keep it everywhere">Keep</button>'
    : c.junk === 'confirmed' ? '<button type="button" data-junk="keep" title="not junk after all">Keep</button>' : '';
  return `<div class="card${word ? ` ${word}` : ''}" data-clip="${escapeHtml(c.clip)}"${word ? ` data-word="${word}"` : ''}>
    <div class="frame">${picture}<span class="tc tnum">${clock(c.duration)}</span>${
      badge ? `<span class="badge ${badge.cls}"${badge.title ? ` title="${escapeHtml(badge.title)}"` : ''}>${badge.text}</span>` : ''}</div>
    <div class="cap"><b title="${escapeHtml(c.clip)}">${escapeHtml(c.stem)}</b>${answer}</div>
  </div>`;
}

// The days the bin was shot on: "Jan 18 + Jan 27", or the first and last of many.
function daysText(days) {
  const named = days.filter(Boolean);
  if (!named.length) return '';
  return named.length <= 3 ? named.join(' + ') : `${named[0]} – ${named[named.length - 1]} · ${named.length} days`;
}

function renderSheet() {
  const d = O.clips;
  const clips = d.clips.slice().sort((a, b) => (a.captured || 0) - (b.captured || 0));
  $('#empty').hidden = clips.length > 0;
  const byDay = new Map();
  for (const c of clips) {
    const k = dayKey(c.captured);
    if (!byDay.has(k)) byDay.set(k, []);
    byDay.get(k).push(c);
  }
  const days = [...byDay.values()].map((cs) => (cs[0].captured ? dayOf(cs[0].captured) : ''));
  O.days = days;
  renderHeadline();
  $('#sessions').innerHTML = [...byDay.values()].map((cs, k) => `<div class="session" data-day="${k}">
      <div class="lbl"><span>${escapeHtml(days[k] || 'undated')} · ${plural(cs.length, 'clip')}</span></div>
      <div class="grid">${cs.map(cardHtml).join('')}</div>
    </div>`).join('');
  renderButton();
  renderHow();
}

// "12 clips · 43:08 · Jan 18 + Jan 27" — or "· not indexed yet" on a new bin.
function renderHeadline() {
  const d = O.clips, ix = O.index;
  if (!d) return;
  const days = daysText(O.days || []);
  $('#headline').textContent = `${plural(d.clips.length, 'clip')} · ${clock(d.total_s)}`
    + (ix && !ix.exists ? ' · not indexed yet' : days ? ` · ${days}` : '');
}

// How it was indexed, in one line honest to the files (sidecars on disk, the project's
// interval, this project's spend): "every word heard · a frame every 4 s · close looks
// on all 12 · spent $4.38 ▸". It opens to the per-clip table and the journal.
function howWords() {
  const d = O.clips;
  const live = d.clips.filter((c) => !(c.journal && c.journal.missing));
  const clean = live.filter((c) => c.junk !== 'confirmed');
  const n = live.length, m = clean.length;
  const heard = live.filter((c) => c.analysed).length;
  const looked = clean.filter((c) => c.looked).length;
  const closed = clean.filter((c) => c.closed).length;
  const closeWait = clean.filter((c) => !c.closed && c.journal && !SETTLED.has(c.journal.stages.close)).length;
  const out = [heard >= n ? 'every word heard' : `${heard} of ${n} heard`];
  const every = `a frame every ${projectInterval()} s`;
  if (looked) out.push(looked >= m ? every : `${looked} of ${m} looked at · ${every}`);
  if (closed) {
    out.push(closed >= m ? `close looks on all ${m}`
      : closeWait ? `close looks on ${closed} of ${closed + closeWait}` : `close looks on ${closed}`);
  }
  return out.join(' · ');
}

function renderHow() {
  const ix = O.index;
  const show = !!(ix && ix.exists && !ix.running && O.clips && O.status);
  $('#howLine').hidden = !show;
  $('#how').hidden = !(show && O.howOpen);
  $('#howLine').setAttribute('aria-expanded', String(show && O.howOpen));
  $('#howArrow').textContent = O.howOpen ? '▾' : '▸';
  if (!show) return;
  $('#howText').textContent = `${howWords()} · `;
}

function toggleHow() {
  O.howOpen = !O.howOpen;
  renderHow();
}

async function junkVerdict(clip, verdict) {
  try {
    await send('POST', '/api/junk', { clip, verdict });
  } catch (e) {
    return toast(`junk: ${e.message || e}`, 5000);
  }
  const stem = String(clip).replace(/\.[^.]+$/, '');
  toast(verdict === 'junk' ? `${stem} is junk — its look is skipped` : `${stem} kept`);
  try { await refreshClips(); } catch (e) { /* the next poll will */ }
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
  $('#lookWord').textContent = `Looks at a frame every ${i} s`;
  // one money number, on the how line; the backend and its model are not this page's
  // to say — the CLI banner (/cli.js) speaks when it needs Karl
  $('#budgetLine').textContent = spentLine(b);
  renderButton();
  renderHow();
}

function renderButton() {
  const btn = $('#indexBtn');
  const running = !!(O.index && O.index.running);
  const v = O.status && O.status.visual;
  const pending = !!(v && v.pending && v.pending.length);
  const price = pending ? ` · ~${usd(priceAt(interval()))}` : '';
  const ph = phase();
  $('#setup').hidden = ph !== 'setup';
  // the slider's line only while there is something left to look at; "change" opens it
  $('#lookLine').hidden = !pending;
  $('#look').hidden = !(pending && O.lookOpen);
  $('#lookChange').setAttribute('aria-expanded', String(pending && O.lookOpen));
  btn.disabled = running || O.busy || !O.status || !O.index;
  btn.textContent = O.index && O.index.exists ? `Index what isn't done${price}` : `Index the footage${price}`;
  for (const el of $$('#order button')) {
    el.classList.toggle('on', el.dataset.order === order());
    el.disabled = running || O.busy;
  }
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
  if (r.state === 'waiting') return { cls: 'waiting', text: 'on the pass · look paused' };
  if (r.state === 'queued' && STAGES.some((s) => r.stages[s] === 'failed')) {
    return { cls: 'queued', text: `retrying · ${r.error || 'a stage failed'}` };
  }
  return { cls: r.state, text: r.state };
}

function renderIndex() {
  const ix = O.index;
  const running = !!ix.running;
  const w = ix.waiting || { clips: 0 };
  $('#paused').hidden = !(ix.exists && ix.paused_priced && !running && w.clips);
  renderHeadline();
  renderButton();
  renderHow();
  renderProgress();
  if (!ix.exists) return;

  const p = ix.progress;
  const orderWord = ORDER_WORD[ix.order] || ix.order;
  const counts = { released: 0, waiting: 0, indexing: 0, queued: 0, parked: 0, missing: 0 };
  for (const r of p.rows) counts[r.state] = (counts[r.state] || 0) + 1;
  const COUNT_WORD = { waiting: 'look paused' };

  // the table (Fig. 2), behind the how line: clip · stages as chips · priority · state,
  // in the journal's order. The title says what the run is doing in words.
  const all = p.released === p.clips - p.missing;
  $('#indexTitle').textContent = `${running ? 'Indexing' : all ? 'Indexed'
    : p.paused_priced ? 'Looks paused' : 'Index stopped'} · ${orderWord}`;
  $('#indexCounts').textContent = Object.entries(counts).filter(([, n]) => n)
    .map(([k, n]) => `${n} ${COUNT_WORD[k] || k}`).join(' · ');
  $('#indexDetail').textContent = running ? O.detail : '';
  $('#rows').innerHTML = '<span class="lbl">clip</span><span class="lbl">stages</span><span class="lbl">priority</span><span class="lbl">state</span>'
    + p.rows.map((r) => {
      const st = rowState(r);
      return `<span class="clip" title="${escapeHtml(r.clip)}">${escapeHtml(String(r.clip).replace(/\.[^.]+$/, ''))}</span>`
        + `<span>${STAGES.map((s) => chip(s, r.stages[s], r.attempts && r.attempts[s])).join('')}</span>`
        + `<span class="tnum">${r.priority == null ? '—' : Number(r.priority).toFixed(2)}</span>`
        + `<span class="st ${st.cls}" title="${escapeHtml(st.text)}">${escapeHtml(st.text)}</span>`;
    }).join('');
  renderPaused(ix);
  $('#journalPath').textContent = ix.path || '';
  $('#journalLog').innerHTML = (p.log || []).slice(-5).map((e) =>
    `<div><b>${escapeHtml(timeOf(e.at))}</b>${escapeHtml(e.what)}</div>`).join('');
  renderSettingsRunning();
}

// After the click, one progress line (I16.2): "Indexing · 3 of 12 ready · about 25:00
// left", and a thin bar — the journal's own numbers.
function renderProgress() {
  const ix = O.index;
  const running = !!(ix && ix.running);
  $('#progress').hidden = !running;
  if (!running) return;
  const p = ix.exists ? ix.progress : null;
  if (!p) {
    $('#progLine').textContent = 'Indexing · opening the journal';
    $('#progBar').style.width = '0%';
    return;
  }
  const eta = p.eta_s >= 1 ? ` · about ${clock(p.eta_s)} left` : '';
  $('#progLine').textContent = `Indexing · ${p.released} of ${p.clips - p.missing} ready${eta}`;
  $('#progBar').style.width = `${Math.max(0, Math.min(100, p.pct || 0))}%`;
}

// The paused box (INTAKE M14): what waits, what it costs, and why — said to the editor.
// The journal's reason is a log line ("paused by the lead after the live kill test" was
// on screen); only the two reasons the app itself writes are repeated, anything else is
// the plain fact that they were paused before this run.
function pausedWords(reason) {
  const r = String(reason || '');
  if (r.startsWith('budget cap')) {
    // the cap as it is now, not as the reason recorded it: raised or removed since,
    // Resume is all it takes
    const b = (O.status && O.status.backend) || {};
    if (b.budget_usd == null) return 'They stopped at the budget cap. There is no cap now.';
    return `The budget cap is reached — spent ${spentLine(b)}. Raise or remove the cap in settings.`;
  }
  if (r === 'paused by the editor') return 'You paused them.';
  return 'They were paused before this run.';
}

function renderPaused(ix) {
  const w = ix.waiting || { clips: 0, looks: 0, close: 0, usd: 0 };
  const what = w.looks ? 'Looks' : 'Close looks';
  $('#pausedTitle').textContent = w.clips ? `${what} paused on ${plural(w.clips, 'clip')}` : 'Looks paused';
  $('#pausedWhy').textContent = pausedWords(ix.progress && ix.progress.paused_reason);
  $('#resume').textContent = w.clips ? `Resume · ~${usd(w.usd)}` : 'Resume';
}

/* --------------------------------------------------------------- settings */

// The drawer behind the gear (Fig. 1's "settings ▾"): the budget cap with the spend
// beside it and where the cap comes from, and one stepper per stage. Nothing here is
// written until Save — one PUT of the whole form — and the budget line in the index
// controls re-reads from /api/status afterwards, so the two never disagree. A server
// without the endpoint (an older tree) is said in the drawer, never a broken form.

const clampWorkers = (v) => {
  const n = Math.round(Number(v));
  return Number.isFinite(n) ? Math.max(WORKERS_MIN, Math.min(WORKERS_MAX, n)) : WORKERS_MIN;
};

function buildWorkers() {
  $('#workers').innerHTML = WORKER_STAGES.map((st) => `<div class="wrow" data-stage="${st}">
    <div class="top"><span class="wname">${st}</span>
      <span class="stepper"><button type="button" data-d="-1" aria-label="fewer ${st} workers" title="fewer">−</button>
      <input type="number" data-stage="${st}" min="${WORKERS_MIN}" max="${WORKERS_MAX}" step="1" inputmode="numeric" aria-label="${st} workers">
      <button type="button" data-d="1" aria-label="more ${st} workers" title="more">+</button></span></div>
    <div class="whint hint small">${escapeHtml(WORKER_WORD[st] || '')}</div>
  </div>`).join('');
}

function workerField(st) {
  return $(`#workers input[data-stage=${st}]`);
}

// The form from a settings-shaped object — the server's answer, or its `defaults`.
function fillSettingsForm(v) {
  const s = O.settings;
  if (!s) return;
  if (s.source.budget_usd !== 'env') setCapForm(v.budget_usd);
  for (const st of WORKER_STAGES) {
    const f = workerField(st);
    if (f && v.workers && v.workers[st] != null) f.value = String(clampWorkers(v.workers[st]));
  }
}

// No cap, or a cap and its number: the radios and the field say the same thing.
function setCapForm(cap) {
  $('#capNone').checked = cap == null;
  $('#capOn').checked = cap != null;
  $('#capField').value = cap == null ? '' : String(cap);
}

function settingsErr(text) {
  const el = $('#settingsErr');
  el.textContent = text || '';
  el.hidden = !text;
}

function renderSettingsRunning() {
  $('#settingsRunning').hidden = !(O.sopen && O.index && O.index.running);
}

function renderSettingsButtons() {
  const s = O.settings;
  $('#settingsSave').disabled = O.sbusy || !s;
  $('#settingsReset').disabled = O.sbusy || !s || !s.defaults;
}

function renderSettings() {
  const s = O.settings;
  $('#settingsBtn').setAttribute('aria-expanded', String(O.sopen));
  $('#settings').hidden = !O.sopen;
  renderSettingsRunning();
  if (!O.sopen) return;
  $('#settingsGone').hidden = !O.sgone;
  $('#settingsForm').hidden = !s;
  renderSettingsButtons();
  if (!s) return;
  const src = (s.source || {}).budget_usd;
  const env = src === 'env';
  for (const el of [$('#capField'), $('#capNone'), $('#capOn')]) el.disabled = env;
  setCapForm(s.budget_usd);                        // the environment's number too, so the form says what holds
  $('#capHint').textContent = env
    ? 'from the environment — ROUGHCUT_BUDGET_USD wins over this'
    : s.budget_usd == null
      ? 'no cap — every button that spends still shows its price first'
      : 'on this project\'s spend — the index pauses its priced stages at this line, and a model call past it is refused';
  fillSettingsForm(s);
  $('#settingsState').textContent = '';
}

async function refreshSettings() {
  try {
    O.settings = await getJSON('/api/settings');
    O.sgone = false;
  } catch (e) {
    O.settings = null;                             // an older server: the drawer says so
    O.sgone = true;
  }
  renderSettings();
}

async function openSettings() {
  O.sopen = true;
  closeKeys();
  settingsErr('');
  renderSettings();                                // the last answer first, then a fresh one
  await refreshSettings();
  const first = $('#capField').disabled ? workerField(WORKER_STAGES[0])
    : $('#capOn').checked ? $('#capField') : $('#capNone');
  if (first && O.settings) first.focus();
}

function closeSettings() {
  if (!O.sopen) return;
  O.sopen = false;
  renderSettings();
  if (isTyping(document.activeElement) && $('#settings').contains(document.activeElement)) {
    document.activeElement.blur();
  }
}

function toggleSettings() {
  if (O.sopen) closeSettings(); else openSettings();
}

// What Save changed, in words: "cap $15.00 → $5.00 · sheet workers 2 → 4".
function settingsChanges(before, after) {
  const out = [];
  if (before && capText(before.budget_usd) !== capText(after.budget_usd)) {
    out.push(`cap ${capText(before.budget_usd)} → ${capText(after.budget_usd)}`);
  }
  for (const st of WORKER_STAGES) {
    const a = before && before.workers ? before.workers[st] : undefined;
    const b = after.workers ? after.workers[st] : undefined;
    if (a !== b) out.push(`${st} workers ${a == null ? '?' : a} → ${b}`);
  }
  return out;
}

// One PUT of the whole form; `body` given explicitly is sent as it is (the tests send a
// bad one on purpose to see the server's 400 said).
async function saveSettings(body) {
  if (O.sbusy) return null;
  const before = O.settings;
  if (!body) {
    if (!before) return null;
    body = { workers: {} };
    for (const st of WORKER_STAGES) {
      const f = workerField(st);
      const n = clampWorkers(f.value);
      f.value = String(n);
      body.workers[st] = n;
    }
    if (before.source.budget_usd !== 'env') {
      if ($('#capNone').checked) {
        body.budget_usd = null;                    // no cap
      } else {
        const cap = Number($('#capField').value);
        if (!(cap > 0)) {
          settingsErr('the cap must be more than $0 — or choose No cap');
          return null;
        }
        body.budget_usd = cap;
      }
    }
  }
  O.sbusy = true;
  renderSettingsButtons();
  try {
    const r = await send('PUT', '/api/settings', body);
    O.settings = r;
    O.sgone = false;
    settingsErr('');
    renderSettings();
    const changes = settingsChanges(before, r);
    toast(changes.length ? `saved · ${changes.join(' · ')}` : 'saved — nothing changed', 4000);
    // the budget line in the index controls re-reads the cap
    try { await refreshStatus(); } catch (e) { /* the next poll will */ }
    return r;
  } catch (e) {
    settingsErr(`not saved: ${e.message || e}`);
    toast(`settings not saved: ${e.message || e}`, 5000);
    return null;
  } finally {
    O.sbusy = false;
    renderSettingsButtons();
  }
}

// Reset fills the form from the server's `defaults`; Save is still the word that writes.
function resetSettings() {
  const s = O.settings;
  if (!s || !s.defaults) return;
  fillSettingsForm(s.defaults);
  settingsErr('');
  $('#settingsState').textContent = 'the defaults are filled in — Save applies them';
}

function onSettingsKey(e) {
  if (e.key === 'Escape') {
    if (O.sopen) { e.preventDefault(); closeSettings(); }
    if (O.keysOpen) { e.preventDefault(); closeKeys(); }
    return;
  }
  if (e.key === '?' && !e.ctrlKey && !e.metaKey && !e.altKey && !isTyping(document.activeElement)) {
    e.preventDefault();
    toggleKeys();
    return;
  }
  if (e.key === ',' && !e.ctrlKey && !e.metaKey && !e.altKey && !isTyping(document.activeElement)) {
    e.preventDefault();
    toggleSettings();
  }
}

function wireSettings() {
  buildWorkers();
  $('#settingsBtn').addEventListener('click', toggleSettings);
  $('#settingsSave').addEventListener('click', () => saveSettings());
  $('#settingsReset').addEventListener('click', resetSettings);
  $('#workers').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-d]');
    if (!b) return;
    const f = b.closest('.wrow').querySelector('input');
    f.value = String(clampWorkers(Number(f.value) + Number(b.dataset.d)));
    settingsErr('');
  });
  // a number typed out of range is clamped when the field settles (1–8, whole)
  $('#workers').addEventListener('change', (e) => {
    if (e.target.matches('input[data-stage]')) e.target.value = String(clampWorkers(e.target.value));
  });
  // typing a number is choosing a cap; choosing No cap empties the number
  $('#capField').addEventListener('input', () => { $('#capOn').checked = true; settingsErr(''); });
  $('#capNone').addEventListener('change', () => { if ($('#capNone').checked) $('#capField').value = ''; });
  $('#capOn').addEventListener('change', () => { if ($('#capOn').checked) $('#capField').focus(); });
  $('#settings').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.target.matches('input')) { e.preventDefault(); saveSettings(); }
  });
  document.addEventListener('keydown', onSettingsKey);
}

/* ------------------------------------------------------------------ keys */

// ? in the header: the page's few keys, in a small popover — Esc or ? again closes it.
function renderKeys() {
  $('#keys').hidden = !O.keysOpen;
  $('#keysBtn').setAttribute('aria-expanded', String(O.keysOpen));
}

function closeKeys() {
  if (!O.keysOpen) return;
  O.keysOpen = false;
  renderKeys();
}

function toggleKeys() {
  O.keysOpen = !O.keysOpen;
  if (O.keysOpen) closeSettings();
  renderKeys();
}

/* ---------------------------------------------------------- the sentence */

// One field, "What is this film about?" (INTAKE M16 C7): the EDL's `story`, the brief
// the Ask reads and the words that tag moments on the pass (roughcut/picks.py).
function renderBrief() {
  const t = O.brief;
  if (!t) return;
  if (O.story === null) {                        // first answer: the EDL's sentence fills the field
    O.story = t.story || '';
    $('#story').value = O.story;
  }
}

// The sentence is saved when the field settles, so one typed and never used survives a
// reload.
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

/* ---------------------------------------------------------------- picker */

// One bin per launch was the rule; now the header's name is a control (INTAKE I5.4).
// The control, its panel and the cuts list it grew are /switcher.js's, shared with
// the pass and the board; this page only says how to reload itself for another bin
// or another cut — the hook the switcher calls after the server has re-pointed
// itself. Forget everything this page held about the last one — a run's id, the
// sentence, the slider's move — and load the new one the way boot() does.
async function reopen() {
  clearTimeout(O.timer);
  dictStop();
  Object.assign(O, {
    clips: null, status: null, index: null, order: null, interval: null, job: null, detail: '',
    busy: false, timer: null, brief: null, story: null, sopen: false, lookOpen: false, howOpen: false,
  });
  $('#headline').textContent = 'opening the folder…';
  $('#story').value = '';
  renderSettings();
  await load();
}
window.roughcutReopen = reopen;

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
    O.lookOpen = false;
    toast(extra.resume_priced ? 'looks resumed' : 'indexing');
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

async function refreshBrief() {
  O.brief = await getJSON('/api/themes');
  // The server says whether the recogniser is installed: hide the mic before the first
  // hold rather than on a 501.
  if (O.brief.dictation === false && O.dictation !== false) {
    O.dictation = false;
    $('#mic').hidden = true;
  }
  renderBrief();
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
    renderSheet();                                 // the badges read the journal's answer too
  } catch (e) {
    toast(String(e.message || e), 4000);
  }
  if (O.index && O.index.running) poll();
  else if (O.detail) $('#indexDetail').textContent = O.detail;
}

function wireBrief() {
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
  const look = () => { O.lookOpen = !O.lookOpen; renderButton(); };
  $('#lookChange').addEventListener('click', look);
  $('#lookChange').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); look(); }
  });
  $('#howLine').addEventListener('click', toggleHow);
  $('#keysBtn').addEventListener('click', toggleKeys);
  // a click anywhere else puts the two header popovers away
  document.addEventListener('click', (e) => {
    if (O.keysOpen && !e.target.closest('#keys, #keysBtn')) closeKeys();
    if (O.sopen && !e.target.closest('#settings, #settingsBtn')) closeSettings();
  });
  $('#sessions').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-junk]');
    const card = b && b.closest('.card');
    if (card) junkVerdict(card.dataset.clip, b.dataset.junk);
  });
  wireBrief();
  wireSettings();
  await load();
}

// Everything the page knows about the bin, in one go — at boot and again when the
// picker points the board at another folder. The settings never fail the load — an
// older server without the endpoint is said in the drawer instead.
async function load() {
  try {
    await Promise.all([refreshClips(), refreshStatus(), refreshIndex(), refreshBrief(), refreshSettings()]);
    renderSheet();                                 // the badges read the journal's answer too
    if (O.index.running) poll();
  } catch (e) {
    $('#headline').textContent = 'could not open the folder';
    toast(String(e.message || e), 6000);
  }
}

window.sheet = { state: O, refresh: tick, journalWord, phase, badgeOf, midPoster, order, interval,
                 renderControls, renderSheet, dictSend, dictStart, dictStop, reopen,
                 openSettings, closeSettings, saveSettings, resetSettings, refreshSettings };
boot();
