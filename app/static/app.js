/* Roughcut cut board.
 *
 * Deliberately no framework and no build step: the repo is Linux-first Python with
 * no node toolchain, and a served-from-disk page keeps it that way.
 *
 * Two rules the interaction design follows, both from Karl's "make fine tuning fun
 * and easy":
 *   - every edit is local and instant; nothing round-trips to the server except
 *     Save, Snap and Render, which are the three things that genuinely can't be
 *     local. Trimming never waits on a network call.
 *   - every edit is undoable. Fiddling is only fun when it's cheap to be wrong.
 */

let P = null;                 // project payload
let S = null;                 // project status: where this bin is in the workflow
let segs = [];                // working segment list
let sel = 0;
let analysing = false;
let nRenders = 0;
const undoStack = [];

const $ = (s) => document.querySelector(s);
const fmt = (t) => {
  const m = Math.floor(t / 60), s = t - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, '0')}`;
};

/* m:ss. Every long operation in this app shows one: a number that moves is the
 * difference between "working" and "hung", and they are otherwise identical. */
const clock = (s) =>
  `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

function toast(msg, ms = 2200) {
  const el = $('#toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), ms);
}

function pushUndo() {
  undoStack.push(JSON.stringify(segs));
  if (undoStack.length > 100) undoStack.shift();
}

function undo() {
  if (!undoStack.length) return toast('nothing to undo');
  segs = JSON.parse(undoStack.pop());
  render();
  toast('undone');
}

function total() { return segs.reduce((a, s) => a + (s.out - s.in), 0); }

function linesFor(seg) {
  const clip = P.clips[seg.clip];
  if (!clip) return [];
  return clip.transcript.filter((u) => u.end > seg.in && u.start < seg.out);
}

/* A cut that starts or ends inside somebody's sentence is the defect Karl flagged.
 * Rather than only fixing it on demand via Snap, flag it continuously so it's
 * visible while trimming. */
function boundaryWarning(seg) {
  const clip = P.clips[seg.clip];
  if (!clip) return '';
  const cutsInto = (t) => clip.transcript.some((u) => u.start + 0.05 < t && t < u.end - 0.05);
  const bad = [];
  if (cutsInto(seg.in)) bad.push('opens mid-sentence');
  if (cutsInto(seg.out)) bad.push('cuts a line off');
  return bad.join(' · ');
}

function segCard(seg, i) {
  const clip = P.clips[seg.clip] || {};
  const el = document.createElement('div');
  el.className = 'seg' + (i === sel ? ' sel' : '');
  el.draggable = true;
  el.dataset.i = i;

  const warn = boundaryWarning(seg);
  const lines = linesFor(seg).map(
    (u) => `<div><b>${u.start.toFixed(1)}</b> ${escapeHtml(u.text)}</div>`).join('')
    || '<div>(no speech)</div>';

  el.innerHTML = `
    <video preload="metadata" muted playsinline
           src="${clip.proxy || ''}#t=${seg.in.toFixed(2)}"></video>
    <div>
      <div class="meta">
        <span class="handle" title="drag to reorder">⋮⋮</span>
        <span class="clip">${seg.clip.replace('.MP4', '')}</span>
        <span class="times">${seg.in.toFixed(2)} → ${seg.out.toFixed(2)}</span>
        ${warn ? `<span style="color:var(--warn)">⚠ ${warn}</span>` : ''}
        <span class="dur">${(seg.out - seg.in).toFixed(2)}s</span>
      </div>
      <div class="why" contenteditable data-i="${i}">${escapeHtml(seg.why || '')}</div>
      <div class="lines">${lines}</div>
      <div class="trim">
        <span>in</span>
        <button data-act="in" data-d="-0.25">−</button>
        <button data-act="in" data-d="0.25">+</button>
        <span>out</span>
        <button data-act="out" data-d="-0.25">−</button>
        <button data-act="out" data-d="0.25">+</button>
        <button data-act="play">▶ play</button>
        <button data-act="del" class="ghost">remove</button>
      </div>
    </div>`;

  el.addEventListener('click', (e) => {
    sel = i;
    const b = e.target.closest('button');
    if (!b) { paint(); return; }
    const act = b.dataset.act;
    if (act === 'play') return playSeg(el.querySelector('video'), seg);
    if (act === 'del') { pushUndo(); segs.splice(i, 1); return render(); }
    pushUndo();
    const d = parseFloat(b.dataset.d) * (e.shiftKey ? 4 : 1);
    nudge(i, act, d);
    render();
  });

  el.querySelector('.why').addEventListener('blur', (ev) => {
    segs[i].why = ev.target.textContent.trim();
  });

  el.addEventListener('dragstart', (e) => {
    e.dataTransfer.setData('text/plain', i);
    el.classList.add('drag');
  });
  el.addEventListener('dragend', () => el.classList.remove('drag'));
  el.addEventListener('dragover', (e) => e.preventDefault());
  el.addEventListener('drop', (e) => {
    e.preventDefault();
    const from = parseInt(e.dataTransfer.getData('text/plain'), 10);
    if (Number.isNaN(from) || from === i) return;
    pushUndo();
    const [m] = segs.splice(from, 1);
    segs.splice(i, 0, m);
    sel = i;
    render();
  });
  return el;
}

function nudge(i, edge, d) {
  const seg = segs[i];
  const dur = (P.clips[seg.clip] || {}).duration ?? 1e9;
  if (edge === 'in') seg.in = Math.max(0, Math.min(seg.out - 0.2, seg.in + d));
  else seg.out = Math.max(seg.in + 0.2, Math.min(dur, seg.out + d));
  seg.in = Math.round(seg.in * 100) / 100;
  seg.out = Math.round(seg.out * 100) / 100;
}

function playSeg(video, seg) {
  if (!video) return;
  video.muted = false;
  video.currentTime = seg.in;
  video.play();
  clearInterval(video._iv);
  video._iv = setInterval(() => {
    if (video.currentTime >= seg.out) { video.pause(); clearInterval(video._iv); }
  }, 60);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function paint() {
  document.querySelectorAll('.seg').forEach((el, i) =>
    el.classList.toggle('sel', i === sel));
}

/* The board opened on a hand-authored EDL, so an empty timeline used to be an
 * impossible state. Now that a project can start from nothing but a footage folder it
 * is the *first* state, and it has to say what to do next rather than show a blank. */
function emptyState() {
  const el = document.createElement('div');
  el.className = 'empty';
  const analysed = S ? S.analysed : 0;
  const clips = S ? S.clips : 0;
  if (!analysed) {
    el.innerHTML = `<h3>Nothing analysed yet</h3><div>${clips}
      clip${clips === 1 ? '' : 's'} in the footage folder. They need an audio pass
      before anything can be cut — <b>Analyse audio</b>, on the right.</div>`;
    return el;
  }
  el.innerHTML = `<h3>No cut yet</h3>
    <div>${analysed} clip${analysed > 1 ? 's' : ''} analysed and ready.
    Say what this film is about — a sentence is enough — and ask for a first cut.
    You will get a proposal to accept, discard or take apart by hand.</div>
    <textarea id="firstNote" style="margin-top:12px;min-height:60px"
      placeholder="a 2–3 minute edit of the trip for the friends who were there · loose and fun · the people are the point"></textarea>
    <button id="firstCut" class="primary" style="margin-top:10px">Ask for a first cut</button>
    <div class="hint" id="firstState" style="margin-top:8px"></div>`;
  el.querySelector('#firstCut').onclick = () => ask({
    note: el.querySelector('#firstNote').value.trim(),
    button: el.querySelector('#firstCut'),
    state: el.querySelector('#firstState'),
  });
  return el;
}

function render() {
  const tl = $('#timeline');
  tl.innerHTML = '';
  if (!segs.length) tl.appendChild(emptyState());
  segs.forEach((s, i) => tl.appendChild(segCard(s, i)));

  $('#askTitle').textContent = segs.length ? 'Ask for a change' : 'Ask for a cut';
  const t = total();
  const [lo, hi] = P.target;
  const cls = t < lo ? 'under' : t > hi ? 'over' : 'ok';
  $('#total').innerHTML = `<span class="${cls}">${fmt(t)}</span>`;
  $('#band').textContent = `${segs.length} shots · target ${fmt(lo)}–${fmt(hi)}`;
  paintSteps();
  renderLibrary();
}

function renderLibrary() {
  const used = new Set(segs.map((s) => `${s.clip}@${Math.round(s.in)}`));
  const rows = [];
  for (const clip of Object.values(P.clips)) {
    for (const c of clip.candidates) {
      if (used.has(`${clip.clip}@${Math.round(c.t)}`)) continue;
      rows.push({ clip: clip.clip, ...c });
    }
  }
  rows.sort((a, b) => b.score - a.score);
  const lib = $('#library');
  lib.innerHTML = '';
  rows.slice(0, 40).forEach((r) => {
    const d = document.createElement('div');
    d.className = 'cand';
    d.innerHTML = `<span class="t">${r.clip.replace('.MP4', '')} ${r.t.toFixed(1)}s ·
      ${r.score.toFixed(2)}</span><span class="w">${escapeHtml(r.why)}</span>`;
    d.onclick = () => {
      pushUndo();
      const at = sel + 1;
      segs.splice(at, 0, { clip: r.clip, in: r.t, out: r.end ?? r.t + 3, why: r.why });
      sel = at;
      render();
      toast(`added ${r.clip.replace('.MP4', '')} @ ${r.t.toFixed(1)}s`);
    };
    lib.appendChild(d);
  });
}

/* The board was flat: Ask, Snap, Undo, Save and Render were peers, and nothing said
 * what to do first. This is the smallest honest fix — the five steps a project goes
 * through, with the one you are on marked. It reads state rather than tracking it, so
 * it cannot get out of step with the files on disk. */
function paintSteps() {
  if (!S) return;
  const steps = [
    ['footage', S.clips > 0, `${S.clips} clips`],
    ['analyse', S.clips > 0 && S.analysed >= S.clips,
      S.analysed ? `${S.analysed}/${S.clips} analysed` : 'audio pass'],
    ['first cut', segs.length > 0, segs.length ? `${segs.length} shots` : 'ask for one'],
    ['refine', segs.length > 0 && nRenders > 0, 'trim · snap · ask'],
    ['render', nRenders > 0, nRenders ? `${nRenders} version${nRenders > 1 ? 's' : ''}` : ''],
  ];
  const current = steps.findIndex(([, done]) => !done);
  $('#steps').innerHTML = steps.map(([name, done, detail], i) => {
    const cls = done ? 'done' : i === current ? 'now' : '';
    return `<span class="step ${cls}">${done ? '✓ ' : `${i + 1} `}${name}` +
      `${detail ? ` <span style="opacity:.7">${escapeHtml(detail)}</span>` : ''}</span>`;
  }).join('');
}

async function refreshStatus() {
  S = await (await fetch('/api/status')).json();
  const missing = Object.entries(S.tools).filter(([, ok]) => !ok).map(([t]) => t);
  $('#project').innerHTML = `
    <div class="kv"><span>clips in folder</span><b>${S.clips}</b></div>
    <div class="kv"><span>analysed</span><b>${S.analysed}</b></div>
    <div class="kv"><span>shots in the cut</span><b>${S.segments}</b></div>
    ${S.footage_exists ? '' : '<div style="color:var(--bad)">footage folder not found</div>'}
    ${missing.length ? `<div style="color:var(--bad)">missing on PATH: ${missing.join(', ')}</div>` : ''}
    <div class="path">${escapeHtml(S.footage)}</div>
    <div class="path">${escapeHtml(S.edl)}${S.edl_created ? ' (new)' : ''}</div>`;
  paintBackend(S.backend);
  // Previews build in the background for tens of minutes on a long bin. Silence there
  // reads as "nothing is happening", which is the confusion this panel exists to end.
  const px = S.proxies || { ready: true, done: 0, total: 0 };
  if (!px.ready && px.total) {
    $('#proxyState').style.display = 'block';
    $('#proxyState').innerHTML =
      `<div class="kv"><span>building previews</span><b>${px.done}/${px.total}</b></div>
       <div class="bar"><i style="width:${Math.round(100 * px.done / px.total)}%"></i></div>
       <div class="hint" style="margin-top:4px">You can ask for a cut now — this only
       affects the previews on each shot.</div>`;
  } else {
    $('#proxyState').style.display = 'none';
  }
  const btn = $('#analyze');
  btn.textContent = S.pending.length
    ? `Analyse ${S.pending.length} clip${S.pending.length > 1 ? 's' : ''}`
    : 'Analyse audio';
  btn.disabled = !S.pending.length || analysing;
  btn.classList.toggle('primary', S.pending.length > 0 && !segs.length);
  return S;
}

/* Backend status, in the header. Karl's report was that auth problems only appeared
 * ~80s into an Ask — the worst possible moment. Preflight catches the free cases (no
 * CLI on PATH, no API key) at launch; one tiny call catches "not logged in", which
 * nothing free can see. */
function paintBackend(b) {
  const el = $('#backend');
  const short = (b.model || '').replace(/^claude-/, '');
  el.classList.remove('ok', 'bad');
  if (b.problems.length) {
    el.classList.add('bad');
    el.textContent = `${b.backend} · not usable`;
    el.title = b.problems.join('\n');
    return;
  }
  if (b.state === 'checking') { el.textContent = `${short} · checking…`; return; }
  if (b.state === 'failed') {
    el.classList.add('bad');
    el.textContent = `${short} · ${(b.detail || 'unreachable').slice(0, 40)}`;
    el.title = b.detail;
    return;
  }
  if (b.state === 'ok') {
    el.classList.add('ok');
    el.textContent = `${short} · ready`;
    el.title = `${b.backend}, replied in ${(b.latency_ms / 1000).toFixed(1)}s\n` +
      `$${b.spent_usd} of $${b.budget_usd} projected this run`;
    return;
  }
  el.textContent = `${short} · unchecked`;
  el.title = 'Click to check the backend with one small call';
}

function followProbe() {
  const poll = setInterval(async () => {
    const b = (await (await fetch('/api/status')).json()).backend;
    paintBackend(b);
    if (b.state !== 'checking') { clearInterval(poll); S.backend = b; }
  }, 1500);
}

async function probeBackend() {
  paintBackend({ ...S.backend, state: 'checking' });
  await fetch('/api/backend/probe', { method: 'POST' });
  followProbe();
}

/* The audio pass, in-app. It was step 2 of the five terminal steps between a folder
 * of footage and this board, and the only one that takes long enough to need a
 * progress bar rather than a spinner. */
async function analyze() {
  const r = await fetch('/api/analyze', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ skip: [] }),
  });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    return toast(`analysis failed to start: ${d.detail || r.status}`, 5000);
  }
  const { job, total } = await r.json();
  analysing = true;
  $('#analyze').disabled = true;
  $('#analyzeBar').style.display = 'block';
  const poll = setInterval(async () => {
    const s = await (await fetch(`/api/analyze/${job}`)).json();
    // Two stages with very different lengths — encoding proxies for a long bin takes
    // an order of magnitude longer than the ASR — so each reports its own count
    // rather than leaving the bar parked at 100% for half an hour.
    const previews = s.stage === 'previews';
    const [at, of] = previews ? [s.proxy_done, s.proxy_total] : [s.done, s.total];
    $('#analyzeBar').firstElementChild.style.width =
      `${Math.round(100 * at / Math.max(1, of))}%`;
    $('#analyzeState').textContent = previews
      ? `building previews ${at}/${of}…` : `${s.done}/${total} analysed`;
    if (s.state === 'running') return;
    clearInterval(poll);
    analysing = false;
    $('#analyzeBar').style.display = 'none';
    if (s.state === 'failed') {
      $('#analyzeState').textContent = 'failed';
      return toast(`analysis failed — ${s.log.split('\n').slice(-1)[0] || 'see log'}`, 8000);
    }
    $('#analyzeState').textContent = '';
    // Transcripts and candidates only exist now, so the whole project reloads.
    P = await (await fetch('/api/project')).json();
    await refreshStatus();
    render();
    toast(`${s.done} clip${s.done > 1 ? 's' : ''} analysed — ready to cut`, 5000);
  }, 2000);
}

async function save() {
  const r = await fetch('/api/project', {
    method: 'PUT', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ segments: segs, story: $('#story').value }),
  });
  toast(r.ok ? 'saved to EDL' : 'save failed');
}

async function snap() {
  toast('snapping to speech…');
  const r = await fetch('/api/snap', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ segments: segs }),
  });
  if (!r.ok) return toast('snap failed');
  const data = await r.json();
  pushUndo();
  const before = total();
  segs = data.segments;
  render();
  toast(`snapped: ${fmt(before)} → ${fmt(total())} (undo with u)`, 4000);
}

/* Ask — the interject loop. A revision arrives as a *proposal*: shown as a diff,
 * accepted or discarded, and undoable once accepted. A model edit that applied
 * itself would be exactly the thing that makes an editor stop trusting the tool. */
let pendingPlan = null;

function summarise(list) {
  return list.map((s) => `${s.clip.replace('.MP4', '')} ${s.in.toFixed(1)}-${s.out.toFixed(1)}`);
}

function showProposal(plan) {
  pendingPlan = plan;
  const before = summarise(segs);
  const after = summarise(plan.segments);
  const beforeSet = new Set(before);
  const afterSet = new Set(after);
  const rows = [];
  after.forEach((a) => rows.push(
    `<div style="color:${beforeSet.has(a) ? 'var(--dim)' : 'var(--good)'}">${beforeSet.has(a) ? ' ' : '+'} ${escapeHtml(a)}</div>`));
  before.filter((b) => !afterSet.has(b)).forEach((b) => rows.push(
    `<div style="color:var(--bad)">− ${escapeHtml(b)}</div>`));

  const oldTotal = total();
  const newTotal = plan.segments.reduce((a, s) => a + (s.out - s.in), 0);
  $('#proposalNotes').textContent = plan.notes || '(no note returned)';
  $('#proposalDiff').innerHTML =
    `<div class="hint" style="margin-bottom:6px">${segs.length} shots ${fmt(oldTotal)}
     → ${plan.segments.length} shots ${fmt(newTotal)}</div>` + rows.join('');
  $('#proposal').style.display = 'block';
}

/* An elapsed count, not a frozen string. "building a first cut — about a minute…"
 * sitting there unchanged is indistinguishable from a hung app, which is exactly how
 * it read on the first Killington ask. */
function pollAsk(job, verb, stateEl) {
  return new Promise((resolve, reject) => {
    const iv = setInterval(async () => {
      let s;
      try {
        s = await (await fetch(`/api/ask/${job}`)).json();
      } catch (e) { return; }                 // a blip is not a failure; keep waiting
      if (s.state === 'running') {
        stateEl.textContent = `${verb}… ${clock(s.elapsed_s)}`;
        return;
      }
      clearInterval(iv);
      if (s.state === 'done') resolve(s.plan);
      else reject(new Error(s.detail || 'the model call failed'));
    }, 1000);
  });
}

/* The plan is on disk before it is announced, so a reload or a closed tab costs a
 * click rather than another two-minute call. */
async function offerLastProposal() {
  const { record } = await (await fetch('/api/asks/latest')).json();
  if (!record) return;
  const mins = Math.round((Date.now() / 1000 - record.created) / 60);
  const el = $('#lastAsk');
  el.style.display = 'block';
  el.innerHTML = `last proposal — ${record.plan.segments.length} shots,
    ${mins < 1 ? 'just now' : `${mins} min ago`} · <a href="#" id="showLast">show it</a>`;
  $('#showLast').onclick = (e) => {
    e.preventDefault();
    showProposal(record.plan);
  };
}

async function ask(opts = {}) {
  const button = opts.button || $('#ask');
  const stateEl = opts.state || $('#askState');
  const note = opts.note !== undefined ? opts.note : $('#note').value.trim();
  const first = !segs.length;
  if (!note && !first) return toast('type what you want changed first');
  // The brief is the human's half of the loop and the most valuable thing typed into
  // this app, so a first-cut note becomes the story rather than being thrown away.
  if (first && note && !$('#story').value.trim()) $('#story').value = note;
  button.disabled = true;
  const verb = first ? 'building a first cut' : 'thinking';
  stateEl.textContent = `${verb}…`;
  try {
    const r = await fetch('/api/ask', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ note, segments: segs, story: $('#story').value }),
    });
    if (!r.ok) {
      const detail = await r.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${r.status}`);
    }
    // A job, not a two-minute request. The call used to run inside the request, which
    // froze the whole server for its duration and left the plan existing only in that
    // one response — a dropped connection spent the call for nothing.
    const { job } = await r.json();
    const plan = await pollAsk(job, verb, stateEl);
    showProposal(plan);
    const u = plan.usage || {};
    const cost = u.model
      ? `${u.model} · ${u.input_tokens}→${u.output_tokens} tok · $${(u.projected_usd || 0).toFixed(4)} projected`
      : '';
    stateEl.textContent = cost;
    $('#askState').textContent = cost;
  } catch (e) {
    stateEl.textContent = '';
    toast(`ask failed: ${e.message}`, 6000);
  } finally {
    button.disabled = false;
  }
}

function acceptProposal() {
  if (!pendingPlan) return;
  pushUndo();
  segs = pendingPlan.segments.map((s) => ({ ...s }));
  pendingPlan = null;
  $('#proposal').style.display = 'none';
  render();
  toast('applied — undo with u');
}

function rejectProposal() {
  pendingPlan = null;
  $('#proposal').style.display = 'none';
  toast('discarded');
}

/* Renders as versions rather than "the newest file". Judging an edit is comparative —
 * reacting to a choice is faster and more informative than judging one artifact — so
 * two slots, and every past render stays reachable. */
/* Renders made before the metadata sidecar existed have no duration or shot count;
 * "0:00.0 · ? shots" reads as a broken file rather than an old one. */
function versionLabel(v) {
  return v.duration_s ? `${fmt(v.duration_s)} · ${v.segments} shots`
    : `${v.name.replace(/^cut_|\.mp4$/g, '')} · older render`;
}

function loadVersion(v, slot) {
  const el = $(`#preview${slot}`);
  el.src = v.url;
  el.load();
  $(`#label${slot}`).textContent = versionLabel(v);
}

async function refreshVersions() {
  const { renders } = await (await fetch('/api/renders')).json();
  nRenders = renders.length;
  const box = $('#versions');
  box.innerHTML = renders.length ? '' : '<div class="hint">no renders yet</div>';
  renders.forEach((v, i) => {
    const when = new Date(v.created * 1000).toLocaleTimeString([],
      { hour: '2-digit', minute: '2-digit' });
    const row = document.createElement('div');
    row.className = 'ver';
    row.innerHTML = `<span class="t">${escapeHtml(versionLabel(v))}
      <span class="hint">· ${when}</span></span>`;
    ['A', 'B'].forEach((slot) => {
      const b = document.createElement('button');
      b.textContent = slot;
      b.onclick = () => loadVersion(v, slot);
      row.appendChild(b);
    });
    box.appendChild(row);
    if (i === 0) loadVersion(v, 'A');       // newest is what you just made
    if (i === 1) loadVersion(v, 'B');       // and the one before it, to compare
  });
  paintSteps();
}

async function doRender() {
  const r = await fetch('/api/render', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ segments: segs }),
  });
  const { job } = await r.json();
  $('#renderState').textContent = 'starting…';
  $('#renderBar').style.display = 'block';
  const poll = setInterval(async () => {
    const s = await (await fetch(`/api/render/${job}`)).json();
    if (s.state === 'running') {
      // Every shot is cut to its own file before they are joined, so this is a real
      // count rather than a spinner. "rendering…" for two minutes says nothing.
      const el = clock(s.elapsed_s);
      $('#renderState').textContent = s.stage === 'joining'
        ? `joining ${s.total} shots… ${el}` : `cutting ${s.done}/${s.total}… ${el}`;
      $('#renderBar').firstElementChild.style.width =
        `${Math.round(100 * s.done / Math.max(1, s.total))}%`;
      return;
    }
    clearInterval(poll);
    $('#renderBar').style.display = 'none';
    $('#renderState').textContent = s.state === 'done'
      ? `done in ${clock(s.elapsed_s)}` : 'failed';
    if (s.url) {
      await refreshVersions();
      toast('render ready — A is the new one, B the one before');
    } else {
      toast('render failed — see server log');
    }
  }, 1500);
}

document.addEventListener('keydown', (e) => {
  if (['INPUT', 'TEXTAREA'].includes(e.target.tagName) || e.target.isContentEditable) return;
  const step = e.shiftKey ? 1.0 : 0.25;
  const k = e.key;
  if (k === 'j') { sel = Math.min(segs.length - 1, sel + 1); paint(); scrollSel(); }
  else if (k === 'k') { sel = Math.max(0, sel - 1); paint(); scrollSel(); }
  else if (k === 'u') undo();
  else if (k === 'x') { pushUndo(); segs.splice(sel, 1); render(); }
  else if (k === '[') { pushUndo(); nudge(sel, 'in', -step); render(); }
  else if (k === ']') { pushUndo(); nudge(sel, 'in', step); render(); }
  else if (k === '{') { pushUndo(); nudge(sel, 'out', -step); render(); }
  else if (k === '}') { pushUndo(); nudge(sel, 'out', step); render(); }
  else if (k === ' ') {
    e.preventDefault();
    const el = document.querySelectorAll('.seg')[sel];
    if (el) playSeg(el.querySelector('video'), segs[sel]);
  } else return;
});

function scrollSel() {
  const el = document.querySelectorAll('.seg')[sel];
  if (el) el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

/* A <video> that 404s while its proxy is still building stays broken until the page
 * is reloaded — the element does not retry on its own. Poll until the server says
 * building is done, then re-point the sources that never loaded. */
function waitForProxies() {
  const iv = setInterval(async () => {
    const p = await (await fetch('/api/project')).json();
    await refreshStatus();          // keeps the count in the panel moving, not just a toast
    if (!p.proxies_ready) return;
    clearInterval(iv);
    P.proxies_ready = true;
    let fixed = 0;
    document.querySelectorAll('.seg video').forEach((v) => {
      if (v.readyState >= 1) return;
      v.src = v.getAttribute('src');   // same URL, fresh load attempt
      v.load();
      fixed++;
    });
    if (fixed) toast(`${fixed} preview${fixed > 1 ? 's' : ''} now available`);
  }, 4000);
}

async function boot() {
  P = await (await fetch('/api/project')).json();
  await refreshStatus();
  segs = P.segments.map((s) => ({ ...s }));
  $('#title').textContent = [P.variant, P.title].filter(Boolean).join(' · ');
  $('#story').value = P.story || '';
  document.title = `Cut board — ${P.title}`;
  render();
  await refreshVersions();
  await offerLastProposal();
  if (!P.proxies_ready) {
    toast('building proxies in the background — previews appear as they finish', 6000);
    waitForProxies();
  }
  $('#backend').onclick = probeBackend;
  // the startup probe may still be in flight; follow it rather than showing "unchecked"
  if (S.backend.state === 'checking') followProbe();
  $('#analyze').onclick = analyze;
  $('#save').onclick = save;
  $('#snap').onclick = snap;
  $('#undo').onclick = undo;
  $('#render').onclick = doRender;
  $('#ask').onclick = () => ask();   // not `ask` — a MouseEvent has a `.button` too
  $('#acceptProposal').onclick = acceptProposal;
  $('#rejectProposal').onclick = rejectProposal;
}
boot();
