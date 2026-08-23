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
let renderList = [];          // the versions list, kept so it can be repainted on edit
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

/* Every mutation goes through here, which makes it the honest place to hang autosave.
 *
 * The board used to keep the working edit in memory and write it only when you
 * pressed Save EDL — so a refresh silently threw away everything you had accepted and
 * trimmed. Karl lost a 16-shot cut that way. The EDL on disk is supposed to be the
 * source of truth; it is now actually kept that way. */
function pushUndo() {
  undoStack.push(JSON.stringify(segs));
  if (undoStack.length > 100) undoStack.shift();
  touch();
}

let saveTimer = null;

function touch() {
  $('#saveState').textContent = 'unsaved…';
  clearTimeout(saveTimer);
  saveTimer = setTimeout(save, 700);
}

function undo() {
  if (!undoStack.length) return toast('nothing to undo');
  segs = JSON.parse(undoStack.pop());
  render();
  touch();                      // undoing is an edit too, and must reach the disk
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
  // What the visual pass saw inside this shot — the only account of anything nobody said.
  const seen = seenFor(seg).map(
    (m) => `<div><b>${m.start.toFixed(1)}</b> ${kindTag(m.kind)}${escapeHtml(m.what)}</div>`).join('');
  const blind = unusableFor(seg).map(
    (u) => `unusable ${u.start.toFixed(1)}–${u.end.toFixed(1)}: ${u.why}`).join(' · ');

  el.innerHTML = `
    <video preload="metadata" muted playsinline
           src="${clip.proxy || ''}#t=${seg.in.toFixed(2)}"></video>
    <div>
      <div class="meta">
        <span class="handle" title="drag to reorder">⋮⋮</span>
        <span class="clip">${seg.clip.replace('.MP4', '')}</span>
        <span class="times">${seg.in.toFixed(2)} → ${seg.out.toFixed(2)}</span>
        ${warn ? `<span style="color:var(--warn)">⚠ ${warn}</span>` : ''}
        ${blind ? `<span style="color:var(--bad)" class="blind">⚠ ${escapeHtml(blind)}</span>` : ''}
        <span class="dur">${(seg.out - seg.in).toFixed(2)}s</span>
      </div>
      <div class="why" contenteditable data-i="${i}">${escapeHtml(seg.why || '')}</div>
      <div class="lines">${lines}</div>
      ${seen ? `<div class="lines seen">${seen}</div>` : ''}
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
    // The poster is the shot; clicking it plays the cut from here, in the monitor.
    if (e.target.tagName === 'VIDEO') { revealMonitor(); return playFrom(i); }
    const b = e.target.closest('button');
    if (!b) { paint(); return; }
    const act = b.dataset.act;
    if (act === 'play') { revealMonitor(); return playFrom(i, { single: true }); }
    if (act === 'del') { pushUndo(); segs.splice(i, 1); return render(); }
    pushUndo();
    const d = parseFloat(b.dataset.d) * (e.shiftKey ? 4 : 1);
    nudge(i, act, d);
    render();
  });

  el.querySelector('.why').addEventListener('blur', (ev) => {
    if (segs[i].why === ev.target.textContent.trim()) return;
    segs[i].why = ev.target.textContent.trim();
    touch();
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

/* The monitor. One place where the cut plays, fed from the proxies, so judging an edit
 * means pressing play rather than waiting two minutes on a render. Two <video> elements
 * take turns: while one plays the current shot the other already holds the next one,
 * parked on its in-point, so a cut costs a swap of which element is visible rather than
 * the time it takes to open a file. Not gapless — a rough cut does not need to be — but
 * close enough that the rhythm of the edit reads. */
/* `gen` counts commands to the monitor. Opening a shot is asynchronous — a proxy that
 * is not in the browser's cache takes seconds to give up its metadata — and until this
 * counter existed a command issued in that window did not cancel the one before it:
 * press play, press it again because nothing had happened yet, and the second press
 * paused a monitor that was not yet playing while the first press's callback fired
 * afterwards and started the video anyway. The board then believed it was stopped —
 * no clock, no playhead, no out-point, no next shot — while sound came out of it.
 * Every command bumps `gen`; a deferred callback that finds it moved on does nothing. */
const player = { vids: [], cur: 0, idx: -1, playing: false, single: false, raf: 0, gen: 0 };

const stem = (clip) => String(clip).replace(/\.[^.]+$/, '');

function filmStart(i) {
  let t = 0;
  for (let k = 0; k < i && k < segs.length; k++) t += segs[k].out - segs[k].in;
  return t;
}

function liveVideo() { return player.vids[player.cur]; }

/* Point a buffer at a shot's in-point without playing it. */
function arm(v, seg) {
  const src = (P.clips[seg.clip] || {}).proxy || '';
  if (v.dataset.src !== src) {
    v.dataset.src = src;
    v.src = src;
    v.load();
  }
  // Deferred, so by the time metadata arrives this buffer may have been pointed at a
  // different clip; parking the old shot would then seek the new one.
  const park = () => { if (v.dataset.src === src) v.currentTime = seg.in; };
  if (v.readyState >= 1) park();
  else v.addEventListener('loadedmetadata', park, { once: true });
}

function showLive() {
  player.vids.forEach((v, k) => v.classList.toggle('live', k === player.cur));
}

/* The monitor sits at the top of the column and the shot list runs a long way below it.
 * Clicking shot 12's poster on the Killington cut started playback 3,163 px above the
 * viewport — measured — where nothing about it could be seen or heard to be about that
 * shot. A play started from down the list brings the monitor back first. */
function revealMonitor() {
  const el = $('#player');
  if (!el || el.style.display === 'none') return;
  const r = el.getBoundingClientRect();
  if (r.top >= 56 && r.bottom <= window.innerHeight) return;
  window.scrollTo({ top: Math.max(0, window.scrollY + r.top - 64), behavior: 'smooth' });
}

/* Say on the screen what the monitor is doing when it is not showing a picture. The
 * monitor had exactly one way of reporting anything — a black rectangle — and three
 * things it could be doing behind it: opening a proxy, waiting on more of one, or
 * having been refused permission to play at all, since `play()`'s rejection was thrown
 * away by an empty `.catch`. All three looked identical, and identical to broken. */
function screenMsg(text, kind) {
  const el = $('#screenMsg');
  if (!el) return;
  el.hidden = !text;
  el.className = kind || '';
  el.textContent = text || '';
}

/* What a MediaError means, in the terms of this app rather than the spec's. */
const MEDIA_ERR = {
  1: 'the load was cancelled',
  2: 'the connection to the board dropped — is the server still running?',
  3: 'the browser could not decode it — the proxy may be half-written',
  4: 'that proxy would not open — it may still be building',
};

function mediaErrorText(v) {
  const e = v.error;
  const name = stem(String(v.dataset.src || v.currentSrc || '').split('/').pop() || 'shot');
  if (!e) return `${name} would not play`;
  return `${name}: ${MEDIA_ERR[e.code] || 'unknown media error'} (code ${e.code})`
    + (e.message ? ` — ${e.message}` : '');
}

/* A refused play is a fact about the browser, not about the cut, and it has to reach
 * the person: Chrome will not start an unmuted video without a gesture it recognises,
 * and the board's own click on a shot card is not always one it counts. */
function playRefused(err) {
  const gesture = err && err.name === 'NotAllowedError';
  const why = gesture
    ? 'the browser refused to play — click the monitor, then press play again'
    : `the browser refused to play — ${(err && err.name) || 'error'}`
      + `${err && err.message ? `: ${err.message}` : ''}`;
  pauseCut();
  screenMsg(why, 'bad');
  toast(why, 8000);
}

function schedule() {
  cancelAnimationFrame(player.raf);
  player.raf = requestAnimationFrame(tick);
}

/* Play from shot i. `single` stops at its out-point instead of carrying on. Playing the
 * shot that is already paused in the monitor resumes it rather than restarting it. */
function playFrom(i, { single = false } = {}) {
  if (!segs.length || !player.vids.length) return;
  i = Math.max(0, Math.min(segs.length - 1, i));
  const seg = segs[i];
  const v = liveVideo();
  const resume = player.idx === i && !player.playing && !!v.dataset.src
    && v.currentTime > seg.in && v.currentTime < seg.out - 0.1;
  const g = ++player.gen;
  player.idx = i;
  player.single = single;
  sel = i;
  paint();
  if (!resume) arm(v, seg);
  if (!single && segs[i + 1]) arm(player.vids[1 - player.cur], segs[i + 1]);
  showLive();
  v.muted = false;
  const go = () => {
    if (g !== player.gen || !player.playing) return;   // pause, or a later command, won
    if (!resume) v.currentTime = seg.in;
    v.play().catch((err) => { if (g === player.gen) playRefused(err); });
  };
  player.playing = true;            // before go(), which refuses to start a paused monitor
  screenMsg(v.readyState >= 2 ? '' : `opening ${stem(seg.clip)}…`);
  if (v.readyState >= 1) go(); else v.addEventListener('loadedmetadata', go, { once: true });
  cueBed(filmStart(i) + (resume ? Math.max(0, v.currentTime - seg.in) : 0));
  schedule();
  paintStrip();
  paintTransport();
}

function pauseCut() {
  player.gen++;                     // any shot still opening must not start behind this
  player.vids.forEach((v) => v.pause());
  if (bed.el) bed.el.pause();
  player.playing = false;
  cancelAnimationFrame(player.raf);
  screenMsg('');       // callers that have something to say set it after this
  paintTransport();
}

function toggleCut() {
  if (player.playing) return pauseCut();
  playFrom(sel);
}

/* True when the shot under the playhead ended and the monitor moved on or stopped. */
function boundary() {
  const v = liveVideo();
  const seg = segs[player.idx];
  if (!seg) { pauseCut(); return true; }
  const clipT = v.currentTime;
  const filmT = filmStart(player.idx) + Math.max(0, clipT - seg.in);
  paintPos(filmT, clipT, seg);
  bedTick(filmT, clipT, seg);
  if (clipT >= seg.out - 0.04 || v.ended) { advance(); return true; }
  return false;
}

function tick() {
  if (!player.playing) return;
  if (!boundary()) player.raf = requestAnimationFrame(tick);
}

/* The shot ended: stop, or hand over to the buffer that is holding the next one. */
function advance() {
  const v = liveVideo();
  v.pause();
  const g = ++player.gen;           // the shot we are leaving must not restart itself
  const next = player.idx + 1;
  if (player.single || next >= segs.length) {
    player.playing = false;
    if (bed.el) bed.el.pause();
    cancelAnimationFrame(player.raf);
    screenMsg('');
    if (!player.single) { sel = 0; player.idx = -1; paint(); }   // the end: space restarts
    paintTransport();
    return;
  }
  player.cur = 1 - player.cur;
  player.idx = next;
  sel = next;
  paint();
  const nv = liveVideo();
  arm(nv, segs[next]);          // normally armed already; re-arming survives edits made mid-play
  nv.muted = false;
  showLive();
  const go = () => {
    if (g !== player.gen || !player.playing) return;
    nv.currentTime = segs[next].in;
    nv.play().catch((err) => { if (g === player.gen) playRefused(err); });
  };
  screenMsg(nv.readyState >= 2 ? '' : `opening ${stem(segs[next].clip)}…`);
  if (nv.readyState >= 1) go(); else nv.addEventListener('loadedmetadata', go, { once: true });
  if (segs[next + 1]) arm(v, segs[next + 1]);
  paintStrip();
  paintTransport();
  schedule();
}

/* Keep the monitor honest against the timeline it is playing: hide it when there is
 * nothing to play, and re-cue if the shot under the playhead was edited out from under it. */
function syncPlayer() {
  $('#player').style.display = segs.length ? '' : 'none';
  if (player.idx >= segs.length) { pauseCut(); player.idx = -1; }
  if (player.playing && player.idx >= 0) {
    const seg = segs[player.idx];
    const v = liveVideo();
    const src = (P.clips[seg.clip] || {}).proxy || '';
    if (v.dataset.src !== src || v.currentTime < seg.in - 0.5 || v.currentTime > seg.out + 0.5) {
      playFrom(player.idx, { single: player.single });
    }
  }
  paintStrip();
  paintTransport();
}

function paintTransport() {
  $('#playCut').textContent = player.playing ? '❚❚ Pause' : '▶ Play cut';
  const seg = segs[player.idx];
  $('#playingWhat').textContent = seg
    ? `${player.idx + 1}/${segs.length} · ${stem(seg.clip)}${player.single ? ' · this shot only' : ''}`
    : '';
}

function paintPos(filmT, clipT, seg) {
  $('#pos').textContent = fmt(filmT);
  const blk = document.querySelectorAll('#strip .blk')[player.idx];
  if (blk && seg) {
    const frac = Math.max(0, Math.min(1, (clipT - seg.in) / (seg.out - seg.in)));
    blk.querySelector('.head').style.left = `${(frac * 100).toFixed(2)}%`;
  }
}

function hueOf(clip) {
  let h = 0;
  for (const c of String(clip)) h = (h * 31 + c.charCodeAt(0)) % 360;
  return h;
}

/* The strip: every shot as a block, width proportional to its length, coloured by clip
 * so a run of cuts from one clip reads as one colour. Click to play from there. */
function paintStrip() {
  const strip = $('#strip');
  strip.innerHTML = '';
  segs.forEach((s, i) => {
    const b = document.createElement('div');
    b.className = 'blk' + (i === sel ? ' sel' : '') + (i === player.idx ? ' live' : '');
    b.style.flex = `${Math.max(0.2, s.out - s.in)} 0 0`;
    b.style.background = `hsl(${hueOf(s.clip)} 45% 58%)`;
    b.title = `${i + 1}. ${stem(s.clip)} ${fmt(s.in)}–${fmt(s.out)} (${(s.out - s.in).toFixed(1)}s)`
      + (s.why ? `\n${s.why}` : '');
    b.innerHTML = `<span>${escapeHtml(stem(s.clip))}</span><i class="head"></i>`;
    b.onclick = () => playFrom(i);
    strip.appendChild(b);
  });
  $('#posTotal').textContent = fmt(total());
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function paint() {
  document.querySelectorAll('.seg').forEach((el, i) =>
    el.classList.toggle('sel', i === sel));
  document.querySelectorAll('#strip .blk').forEach((el, i) =>
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
  const vz = S && S.visual;
  const look = vz && vz.pending.length ? `<div class="hint" style="margin-top:12px">Optional, and
    worth it on footage where things happen: <b>Look at the footage</b> first (Project panel —
    ${vz.pending.length} clip${vz.pending.length > 1 ? 's' : ''}, ~$${vz.projected_usd.toFixed(2)}),
    so the first cut knows what happened on screen and not only what was said.</div>` : '';
  el.innerHTML = `<h3>No cut yet</h3>
    <div>${analysed} clip${analysed > 1 ? 's' : ''} analysed and ready.
    Say what this film is about — a sentence is enough — and ask for a first cut.
    You will get a proposal to accept, discard or take apart by hand.</div>
    <textarea id="firstNote" style="margin-top:12px;min-height:60px"
      placeholder="a 2–3 minute edit of the trip for the friends who were there · loose and fun · the people are the point"></textarea>
    <button id="firstCut" class="primary" style="margin-top:10px">Ask for a first cut</button>
    <div class="hint" id="firstState" style="margin-top:8px"></div>${look}`;
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
  syncPlayer();

  // With an empty timeline the empty state already has its own "what is this film
  // about" box, so the sidebar panel is a second input for the same thing.
  $('#askPanel').style.display = segs.length ? 'block' : 'none';

  // Nothing in the header acts on an empty timeline, so nothing in the header shows.
  ['#snap', '#undo', '#render', '#saveState'].forEach((sel) => {
    $(sel).style.display = segs.length ? '' : 'none';
  });

  // "Fix cut points" only means something when there are cut points to fix, and the
  // count is the reason to press it.
  const warnings = segs.filter((s) => boundaryWarning(s)).length;
  const snap = $('#snap');
  snap.disabled = !warnings;
  snap.textContent = warnings ? `Fix ${warnings} cut point${warnings > 1 ? 's' : ''}`
    : 'Cut points OK';

  const t = total();
  const [lo, hi] = P.target;
  const cls = t < lo ? 'under' : t > hi ? 'over' : 'ok';
  $('#total').innerHTML = `<span class="${cls}">${fmt(t)}</span>`;
  $('#band').textContent = `${segs.length} shots · target ${fmt(lo)}–${fmt(hi)}`;
  paintSteps();
  paintVersions();          // so "this cut" follows the timeline rather than the last fetch
  renderLibrary();
}

/* What the visual pass saw inside this shot, and any stretch it said not to use. */
const HOT = new Set(['fall', 'crash', 'jump', 'reaction']);

function seenFor(seg) {
  const v = (P.clips[seg.clip] || {}).visual || {};
  return (v.moments || []).filter((m) => m.end > seg.in && m.start < seg.out);
}

function unusableFor(seg) {
  const v = (P.clips[seg.clip] || {}).visual || {};
  return (v.unusable || []).filter((u) => u.end > seg.in && u.start < seg.out);
}

function kindTag(kind) {
  return `<i class="kind${HOT.has(kind) ? ' hot' : ''}">${escapeHtml(kind || 'seen')}</i>`;
}

/* Two sources of moments to add: what was *heard* (the audio candidates, ranked) and
 * what was *seen* (the visual pass's notable moments — events first, since a fall
 * nobody narrated is exactly what the transcripts cannot offer). */
let libTab = 'heard';

function renderLibrary() {
  const used = new Set(segs.map((s) => `${s.clip}@${Math.round(s.in)}`));
  const rows = [];
  let anySeen = false;
  for (const clip of Object.values(P.clips)) {
    const moments = (clip.visual || {}).moments || [];
    if (moments.length) anySeen = true;
    if (libTab === 'seen') {
      for (const m of moments) {
        if (!m.notable || m.kind === 'junk') continue;
        if (used.has(`${clip.clip}@${Math.round(m.start)}`)) continue;
        rows.push({ clip: clip.clip, t: m.start, end: m.end, why: m.what, kind: m.kind,
                    score: HOT.has(m.kind) ? 2 : m.kind === 'scenery' ? 0 : 1 });
      }
    } else {
      for (const c of clip.candidates) {
        if (used.has(`${clip.clip}@${Math.round(c.t)}`)) continue;
        rows.push({ clip: clip.clip, ...c });
      }
    }
  }
  rows.sort((a, b) => b.score - a.score);
  $('#libTabs').style.display = anySeen ? 'flex' : 'none';
  if (!anySeen) libTab = 'heard';
  $('#libHint').textContent = libTab === 'seen'
    ? 'What the visual pass saw, not yet in the cut — events first.'
    : 'Audio candidates not yet in the cut.';
  const lib = $('#library');
  lib.innerHTML = rows.length ? '' : `<div class="hint">${libTab === 'seen'
    ? 'nothing left that was seen — or look at more of the footage (Project panel)'
    : 'nothing left to add'}</div>`;
  rows.slice(0, 40).forEach((r) => {
    // The line people read is what is said or seen, not the ranking score that put it here.
    const d = document.createElement('div');
    d.className = 'cand';
    d.innerHTML = `<span class="w">${r.kind ? kindTag(r.kind) : ''}${escapeHtml(r.why)}</span>
      <span class="t">${stem(r.clip)} · ${fmt(r.t)}${r.end ? `–${fmt(r.end)}` : ''}</span>`;
    d.onclick = () => {
      pushUndo();
      const at = sel + 1;
      segs.splice(at, 0, { clip: r.clip, in: r.t, out: r.end ?? r.t + 3, why: r.why });
      sel = at;
      render();
      toast(`added ${stem(r.clip)} @ ${r.t.toFixed(1)}s`);
    };
    lib.appendChild(d);
  });
}

/* The visual pass, in-app. Karl: "the analysis missed some critical moments that would
 * have required video analysis — like me falling into a river." The pass that finds
 * those existed in a terminal; this runs it with the same honest progress as the audio
 * pass. It costs model calls, so the button carries the count and the price, and
 * nothing here starts on its own. */
let looking = false;

async function lookAtFootage() {
  const r = await fetch('/api/visual', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}',
  });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    return toast(`could not start: ${d.detail || r.status}`, 5000);
  }
  const { job, total } = await r.json();
  looking = true;
  $('#visual').disabled = true;
  $('#visual').textContent = 'Looking…';
  $('#visualBar').style.display = 'block';
  const poll = setInterval(async () => {
    let s;
    try { s = await (await fetch(`/api/visual/${job}`)).json(); } catch (e) { return; }
    $('#visualBar').firstElementChild.style.width =
      `${Math.round(100 * s.done / Math.max(1, total))}%`;
    $('#visualState').textContent = `${s.done}/${total} clips seen · ${clock(s.elapsed_s)}`;
    if (s.state === 'running') return;
    clearInterval(poll);
    looking = false;
    $('#visualBar').style.display = 'none';
    $('#visualState').textContent = '';
    // Whatever was written is worth showing, even if a later sheet failed.
    P = await (await fetch('/api/project')).json();
    await refreshStatus();
    if (s.state === 'failed') {
      render();
      return toast(`visual pass failed — ${s.log.split('\n').slice(-1)[0] || 'see log'}`, 8000);
    }
    libTab = 'seen';
    document.querySelectorAll('.tab').forEach((x) =>
      x.classList.toggle('sel', x.dataset.tab === 'seen'));
    render();
    toast(`${s.done} clip${s.done === 1 ? '' : 's'} seen — what happened on screen is on the cards now, and under "seen"`, 6000);
  }, 2000);
}

/* Music: a bed under the cut. The same `effects_music` the render reads, saved the
 * moment it changes, and heard in the monitor before anything is rendered. */
let music = null;             // the EDL's effects_music, or null
let tracks = [];              // assets/music, from /api/assets
const bed = { el: null, gain: 0, last: 0 };

async function loadAssets() {
  try {
    tracks = (await (await fetch('/api/assets')).json()).music || [];
  } catch (e) {
    tracks = [];
  }
  $('#musicTrack').innerHTML = '<option value="">no music</option>' + tracks.map((t) =>
    `<option value="${escapeHtml(t.asset)}">${escapeHtml(t.name)} · ${fmt(t.duration_s || 0)}</option>`).join('');
  paintMusic();
}

function trackInfo() {
  return music ? tracks.find((t) => t.asset === music.asset) : undefined;
}

function paintMusic() {
  const sel = $('#musicTrack');
  sel.value = music ? music.asset : '';
  if (music && sel.value !== music.asset) {   // the EDL names a track the library lacks
    sel.insertAdjacentHTML('beforeend',
      `<option value="${escapeHtml(music.asset)}">${escapeHtml(music.asset)} (not in assets/)</option>`);
    sel.value = music.asset;
  }
  $('#musicOpts').style.display = music ? 'block' : 'none';
  if (music) {
    $('#duck').value = music.duck === false ? 0 : (music.duck_db ?? 12);
    $('#duckVal').textContent = $('#duck').value;
    $('#fadeIn').value = music.fade_in ?? 1.5;
    $('#fadeOut').value = music.fade_out ?? 4;
  }
  $('#musicHint').textContent = music
    ? 'Heard under the cut in the monitor; the render mixes it under the film with the picture untouched.'
    : tracks.length
      ? 'A bed sits under the cut and ducks where people talk. You hear it in the monitor before you render.'
      : 'No tracks yet — drop an mp3 or wav into assets/music/ and reload.';
}

function musicChanged() {
  const asset = $('#musicTrack').value;
  if (!asset) {
    music = null;
  } else {
    const duck = parseFloat($('#duck').value);
    music = { asset, duck: duck > 0, duck_db: duck > 0 ? duck : 12,
              fade_in: parseFloat($('#fadeIn').value) || 0,
              fade_out: parseFloat($('#fadeOut').value) || 0 };
  }
  paintMusic();
  cueBed();
  save();                       // straight to disk: a render reads the EDL, not the screen
}

/* Where people talk in a clip, padded and merged the way effects.speech_regions does it
 * for the render — so the duck you hear in the monitor is the duck the render applies. */
function speechRegions(clip) {
  const c = P.clips[clip];
  if (!c) return [];
  if (c._speech) return c._speech;
  const raw = (c.transcript || []).map((u) => [u.start - 0.35, u.end + 0.35])
    .sort((a, b) => a[0] - b[0]);
  const out = [];
  for (const [lo, hi] of raw) {
    const last = out[out.length - 1];
    if (last && lo - last[1] <= 1.2) last[1] = Math.max(last[1], hi);
    else out.push([Math.max(0, lo), hi]);
  }
  c._speech = out;
  return out;
}

/* The bed's level at a moment, as a linear gain for the <audio>: the render's balance
 * (bed at −24 LUFS under a film at −16) transposed onto the proxy's own loudness, pulled
 * down by the asked depth while anyone is talking, faded at the ends of the film. */
function bedGainAt(filmT, clipT, seg) {
  const t = trackInfo();
  if (!music || !t || !seg) return 0;
  const clipLufs = ((P.clips[seg.clip] || {}).summary || {}).integrated_lufs ?? -16;
  const trackLufs = t.lufs ?? -14;
  let db = (-24 - trackLufs) - (-16 - clipLufs);
  if (music.duck && speechRegions(seg.clip).some(([lo, hi]) => clipT >= lo && clipT <= hi)) {
    db -= music.duck_db;
  }
  let g = Math.pow(10, db / 20);
  const tot = total();
  if (music.fade_in > 0) g *= Math.min(1, filmT / music.fade_in);
  if (music.fade_out > 0) g *= Math.min(1, Math.max(0, tot - filmT) / music.fade_out);
  return Math.max(0, Math.min(1, g));
}

/* Called every frame while the monitor plays. Fast down, slow up — 80ms / 900ms, the
 * render's envelope — so the bed reads as mixed rather than pumping. */
function bedTick(filmT, clipT, seg) {
  if (!bed.el || !music) return;
  const target = bedGainAt(filmT, clipT, seg);
  const now = performance.now();
  const dt = Math.min(0.2, bed.last ? (now - bed.last) / 1000 : 0.016);
  bed.last = now;
  const tau = target < bed.gain ? 0.08 / 3 : 0.9 / 3;
  bed.gain += (target - bed.gain) * (1 - Math.exp(-dt / tau));
  bed.el.volume = Math.max(0, Math.min(1, bed.gain));
}

/* Start (or re-point) the bed at a film time. With no argument, at wherever the monitor
 * is — used when the track or its settings change mid-play. */
function cueBed(filmT) {
  const el = bed.el;
  if (!el) return;
  const t = trackInfo();
  if (!music || !t) {
    el.pause();
    if (el.dataset.src) { delete el.dataset.src; el.removeAttribute('src'); el.load(); }
    return;
  }
  if (el.dataset.src !== t.url) { el.dataset.src = t.url; el.src = t.url; el.load(); }
  if (filmT === undefined) {
    if (!player.playing || player.idx < 0) return;
    const seg = segs[player.idx];
    filmT = filmStart(player.idx) + Math.max(0, liveVideo().currentTime - seg.in);
  }
  const seek = () => { el.currentTime = t.duration_s ? filmT % t.duration_s : 0; };
  if (el.readyState >= 1) seek(); else el.addEventListener('loadedmetadata', seek, { once: true });
  bed.gain = 0;
  bed.last = 0;
  el.volume = 0;
  el.play().catch(() => {});
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
  const vz = S.visual || { pending: [], done: 0, total: 0, calls: 0, projected_usd: 0 };
  $('#project').innerHTML = `
    <div class="kv"><span>clips in folder</span><b>${S.clips}</b></div>
    <div class="kv"><span>analysed</span><b>${S.analysed}</b></div>
    <div class="kv"><span>shots in the cut</span><b>${S.segments}</b></div>
    ${vz.total ? `<div class="kv"><span>looked at</span><b>${vz.done}/${vz.total}</b></div>` : ''}
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
  // A permanently greyed-out button is furniture. It appears when there is something
  // to analyse and otherwise stays out of the way.
  const btn = $('#analyze');
  btn.style.display = S.pending.length || analysing ? 'inline-block' : 'none';
  btn.textContent = S.pending.length
    ? `Analyse ${S.pending.length} clip${S.pending.length > 1 ? 's' : ''}`
    : 'Analysing…';
  btn.disabled = analysing;
  btn.classList.toggle('primary', S.pending.length > 0 && !segs.length);
  // The visual pass: offered with its price while there is footage nobody has looked at.
  const vbtn = $('#visual');
  $('#visualRow').style.display = vz.pending.length || looking ? 'flex' : 'none';
  if (!looking) {
    const n = vz.pending.length;
    vbtn.disabled = false;
    vbtn.textContent = `Look at ${n} clip${n === 1 ? '' : 's'} · ~$${vz.projected_usd.toFixed(2)}`;
    vbtn.title = `Samples frames from each clip and asks a model what happens in them — the `
      + `only way the agent learns about a fall nobody narrated. ${vz.calls} model call`
      + `${vz.calls === 1 ? '' : 's'}, about $${vz.projected_usd.toFixed(2)} projected.`;
  }
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
  clearTimeout(saveTimer);
  const r = await fetch('/api/project', {
    method: 'PUT', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ segments: segs, story: $('#story').value, music }),
  });
  if (!r.ok) {
    $('#saveState').textContent = 'save failed';
    return toast('save failed — the edit is still on screen, do not reload', 8000);
  }
  const t = new Date();
  $('#saveState').textContent =
    `saved ${t.getHours()}:${String(t.getMinutes()).padStart(2, '0')}`;
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
  // Each shot with the reason it was chosen: the `why` is what you check the
  // reasoning against, and it is the only account of what the agent thinks it saw.
  // A shot whose boundaries were polished says so and says where it came from —
  // a cut that silently differs from what the model asked for is one you cannot audit.
  const detail = plan.segments.map((s, i) => `<div style="padding:4px 0">
    <span class="hint">${String(i + 1).padStart(2, '0')} ${escapeHtml(
      s.clip.replace('.MP4', ''))} ${fmt(s.in)}–${fmt(s.out)}
    (${(s.out - s.in).toFixed(1)}s)</span><br>${escapeHtml(s.why || '')}${
    s.polished_from ? `<br><span class="hint">↳ polished from ${
      s.polished_from[0].toFixed(2)}–${s.polished_from[1].toFixed(2)}: ${
      escapeHtml(s.polish_why || '')}</span>` : ''}</div>`).join('');
  $('#proposalDiff').innerHTML =
    `<div class="hint" style="margin-bottom:6px">${segs.length} shots ${fmt(oldTotal)}
     → ${plan.segments.length} shots ${fmt(newTotal)}</div>`
    + rows.join('') + '<hr style="border:0;border-top:1px solid var(--line);margin:10px 0">'
    + detail;
  $('#proposal').style.display = 'block';
  $('#proposal').scrollIntoView({ block: 'start', behavior: 'smooth' });
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
function ago(seconds) {
  const m = Math.round(seconds / 60);
  if (m < 1) return 'just now';
  if (m < 120) return `${m} min ago`;
  if (m < 48 * 60) return `${Math.round(m / 60)} h ago`;
  return `${Math.round(m / 1440)} days ago`;     // "40105 min ago" is not a time
}

async function offerLastProposal() {
  const { record } = await (await fetch('/api/asks/latest')).json();
  if (!record) return;
  const el = $('#lastAsk');
  el.style.display = 'block';
  el.innerHTML = `last proposal — ${record.plan.segments.length} shots,
    ${ago(Date.now() / 1000 - record.created)} · <a href="#" id="showLast">show it</a>`;
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
  $('#lastAsk').style.display = 'none';
  render();
  save();                       // straight to disk; a 16-shot cut is not "in progress"
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

/* Is this file a render of what is on the timeline right now?
 *
 * Karl watched a rendered *proposal* and reported that the board "doesn't seem to
 * reflect the render". It did not and could not — that proposal was never accepted —
 * but nothing on screen said which of the renders the board *did* reflect, and with
 * three files whose names are hashes there was no way to work it out. Renders record
 * their shot list now; the ones made before that fall back to matching on shot count
 * and total length, which is weaker but is all they can support. */
function isThisCut(v) {
  if (!segs.length) return false;
  if (v.shots) {
    return v.shots.length === segs.length && v.shots.every((s, i) =>
      s.clip === segs[i].clip && Math.abs(s.in - segs[i].in) < 0.005
      && Math.abs(s.out - segs[i].out) < 0.005);
  }
  return v.segments === segs.length && v.planned_s != null
    && Math.abs(v.planned_s - total()) < 0.05;
}

/* Renders made before the metadata sidecar existed have no duration or shot count;
 * "0:00.0 · ? shots" reads as a broken file rather than an old one. */
function versionLabel(v) {
  const base = v.duration_s ? `${fmt(v.duration_s)} · ${v.segments} shots`
    : `${v.name.replace(/^cut_|\.mp4$/g, '')} · older render`;
  // Renders made before the profile existed carry neither field and must read as
  // the preview they always were — no suffix, not "unknown".
  const quality = v.profile === 'delivery'
    ? ` · ${v.width >= 3840 ? '4K' : (v.width ? `${v.width}x${v.height}` : 'delivery')}`
    : '';
  // A render can carry a label — "proposal, not accepted" is the one that matters, since
  // a version that was never the cut must not read as if it had been.
  return (v.music ? `${base} ♪` : base) + quality + (v.note ? ` · ${v.note}` : '')
    + (isThisCut(v) ? ' · this cut' : '');
}

function loadVersion(v, slot) {
  const el = $(`#preview${slot}`);
  el.src = v.url;
  el.load();
  $(`#label${slot}`).textContent = versionLabel(v);
}

/* Rebuilds only the list, never the A/B slots — repainted on every edit so that
 * "this cut" tracks the timeline instead of going stale the moment anything is trimmed. */
function paintVersions() {
  const box = $('#versions');
  if (!box) return;
  box.innerHTML = renderList.length ? '' : '<div class="hint">no renders yet</div>';
  renderList.forEach((v) => {
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
  });
}

async function refreshVersions() {
  const { renders } = await (await fetch('/api/renders')).json();
  nRenders = renders.length;
  renderList = renders;
  paintVersions();
  if (renders[0]) loadVersion(renders[0], 'A');   // newest is what you just made
  if (renders[1]) loadVersion(renders[1], 'B');   // and the one before it, to compare
  // An empty black player labelled "B —" is not a feature; the B slot appears when
  // there is a second version to compare against.
  $('#slotB').style.display = renders.length > 1 ? 'block' : 'none';
  paintSteps();
}

async function doRender() {
  const r = await fetch('/api/render', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ segments: segs, profile: $('#renderProfile').value }),
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
  else if (k === ' ') { e.preventDefault(); revealMonitor(); toggleCut(); }
  else if (k === 'Enter') {
    e.preventDefault(); revealMonitor(); playFrom(sel, { single: true });
  }
  else return;
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
  player.vids = [$('#pv0'), $('#pv1')];
  player.vids.forEach((v) => {
    // rAF stops in a background tab; timeupdate (4Hz) keeps the cut points honest there
    v.addEventListener('timeupdate', () => {
      if (player.playing && v === liveVideo()) boundary();
    });
    // A media error used to be reported only if it hit the live buffer while playing,
    // and then only as a guess about proxies still building. It says what actually
    // failed now, and it says it on the screen and not only in a toast that fades.
    v.addEventListener('error', () => {
      const msg = mediaErrorText(v);
      if (v === liveVideo()) { pauseCut(); screenMsg(msg, 'bad'); }
      toast(msg, 8000);
    });
    v.addEventListener('playing', () => { if (v === liveVideo()) screenMsg(''); });
    v.addEventListener('waiting', () => {
      if (v === liveVideo() && player.playing) screenMsg('buffering…');
    });
  });
  $('#playCut').onclick = toggleCut;
  // Makes the refusal message actionable: "click the monitor, then press play again"
  // has to be something a person can do, and a monitor you can click to play is what
  // everyone expects anyway.
  $('.screen').onclick = toggleCut;
  bed.el = $('#bed');
  P = await (await fetch('/api/project')).json();
  music = P.music || null;
  await refreshStatus();
  await loadAssets();
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
  $('#story').oninput = touch;
  $('#snap').onclick = snap;
  $('#undo').onclick = undo;
  $('#render').onclick = doRender;
  $('#ask').onclick = () => ask();   // not `ask` — a MouseEvent has a `.button` too
  $('#acceptProposal').onclick = acceptProposal;
  $('#rejectProposal').onclick = rejectProposal;
  $('#visual').onclick = lookAtFootage;
  $('#libTabs').onclick = (e) => {
    const t = e.target.closest('.tab');
    if (!t) return;
    libTab = t.dataset.tab;
    document.querySelectorAll('.tab').forEach((x) => x.classList.toggle('sel', x === t));
    renderLibrary();
  };
  $('#musicTrack').onchange = musicChanged;
  $('#duck').oninput = () => { $('#duckVal').textContent = $('#duck').value; };
  $('#duck').onchange = musicChanged;
  $('#fadeIn').onchange = musicChanged;
  $('#fadeOut').onchange = musicChanged;
}
boot();
