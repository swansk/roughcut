/* Roughcut — the open screen (docs/design/cutting-room-floor.html §2–§3, INTAKE M5).
 *
 * Know the bin before you pay for it: the folder as a contact sheet — one grid per
 * session in capture order, a card per clip with its first frame, its length and the
 * free flags — and the journal's word on every clip once the bin has one. Framework-free
 * like app.js and floor.js, served from disk; it borrows their patterns by copy.
 *
 * Nothing on this screen spends a model call on its own. `GET /api/clips` is ffprobe and
 * file checks; the index is started only by the button in the right column (I5.3).
 */

'use strict';

const $ = (s) => document.querySelector(s);

const STAGES = ['probe', 'telemetry', 'asr', 'proxy', 'look', 'close', 'picks'];
const SETTLED = new Set(['done', 'skipped']);

const clock = (s) => {
  s = Math.max(0, Math.round(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return (h ? `${h}:${String(m).padStart(2, '0')}` : String(m)) + `:${String(r).padStart(2, '0')}`;
};
const dayOf = (epoch) => epoch
  ? new Date(epoch * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  : 'undated';
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

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

/* ------------------------------------------------------------------ state */

const O = {
  clips: null,             // /api/clips — the folder, with the journal's word when there is one
};

/* ------------------------------------------------------------ the sheet */

// The journal's word on a clip, as one badge on its picture. Derived from the stage
// states the same way journal.progress() derives a row's `state`, so the card and the
// table in the right column never disagree; `retrying` is a failed stage the journal
// will run again (Fig. 2's "retrying · encode failed twice").
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
}

/* -------------------------------------------------------------------- load */

async function refreshClips() {
  O.clips = await getJSON('/api/clips');
  renderSheet();
}

async function boot() {
  try {
    await refreshClips();
  } catch (e) {
    $('#binName').textContent = 'could not open the folder';
    toast(String(e.message || e), 6000);
  }
}

window.sheet = { state: O, refresh: refreshClips, journalWord };
boot();
