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
let segs = [];                // working segment list
let sel = 0;
const undoStack = [];

const $ = (s) => document.querySelector(s);
const fmt = (t) => {
  const m = Math.floor(t / 60), s = t - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, '0')}`;
};

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

function render() {
  const tl = $('#timeline');
  tl.innerHTML = '';
  segs.forEach((s, i) => tl.appendChild(segCard(s, i)));

  const t = total();
  const [lo, hi] = P.target;
  const cls = t < lo ? 'under' : t > hi ? 'over' : 'ok';
  $('#total').innerHTML = `<span class="${cls}">${fmt(t)}</span>`;
  $('#band').textContent = `${segs.length} shots · target ${fmt(lo)}–${fmt(hi)}`;
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

async function ask() {
  const note = $('#note').value.trim();
  if (!note) return toast('type what you want changed first');
  $('#ask').disabled = true;
  $('#askState').textContent = 'thinking…';
  try {
    const r = await fetch('/api/ask', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ note, segments: segs, story: $('#story').value }),
    });
    if (!r.ok) {
      const detail = await r.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${r.status}`);
    }
    const plan = await r.json();
    showProposal(plan);
    const u = plan.usage || {};
    $('#askState').textContent = u.model
      ? `${u.model} · ${u.input_tokens}→${u.output_tokens} tok · $${(u.projected_usd || 0).toFixed(4)} projected`
      : '';
  } catch (e) {
    $('#askState').textContent = '';
    toast(`ask failed: ${e.message}`, 6000);
  } finally {
    $('#ask').disabled = false;
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

async function doRender() {
  const r = await fetch('/api/render', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ segments: segs }),
  });
  const { job } = await r.json();
  $('#renderState').textContent = 'rendering…';
  const poll = setInterval(async () => {
    const s = await (await fetch(`/api/render/${job}`)).json();
    if (s.state === 'running') return;
    clearInterval(poll);
    $('#renderState').textContent = s.state === 'done' ? 'done' : 'failed';
    if (s.url) {
      const v = $('#preview');
      v.src = s.url;
      v.style.display = 'block';
      toast('render ready');
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
  segs = P.segments.map((s) => ({ ...s }));
  $('#title').textContent = `${P.variant} · ${P.title}`;
  $('#story').value = P.story || '';
  document.title = `Cut board — ${P.title}`;
  render();
  if (!P.proxies_ready) {
    toast('building proxies in the background — previews appear as they finish', 6000);
    waitForProxies();
  }
  $('#save').onclick = save;
  $('#snap').onclick = snap;
  $('#undo').onclick = undo;
  $('#render').onclick = doRender;
  $('#ask').onclick = ask;
  $('#acceptProposal').onclick = acceptProposal;
  $('#rejectProposal').onclick = rejectProposal;
}
boot();
