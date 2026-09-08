/* Roughcut — the cutting room floor: the pass.
 *
 * Photo culling for footage (docs/design/cutting-room-floor.html §4). The index proposes
 * picks — windows with witnesses — and this screen plays each one large, takes one key
 * for a verdict, keeps exactly what was watched, and moves on. Framework-free like
 * app.js, served from disk, and it borrows app.js's hard-won patterns by copy rather than
 * import: `arm()` with the `#t=` media fragment, `preload="metadata"`, the `gen` counter
 * that keeps a late play() from starting a monitor somebody already paused, and a screen
 * that says what it is doing when it is not showing a picture.
 *
 * Four rules from Karl's decisions (docs/INTAKE.md), none of which this file bends:
 *   1. keep = what you actually watched, snapped outward to sentence ends; `}` extends.
 *   2. P / X / U / 1, J-K-L intact with K = pause, Caps Lock = auto-advance, ⌘Z undoes
 *      the verdict with its trim and note.
 *   3. the queue is frozen for a round of 40; what arrives lands at the round boundary.
 *   4. verdicts are ranges on clip time, so nothing here depends on a pick id surviving.
 *
 * No buttons in the flow: every action is a key, and the only buttons live on the
 * closing card. Nothing here spends a model call.
 *
 * The strips take a pointer (I2.7, Karl's first report): the green band on the closer
 * strip is the clip — drag a handle to trim it, its middle to slide it; click either strip
 * to seek, drag to scrub; click a mark on the tape to jump to that pick. The keys stay as
 * accelerators.
 */

'use strict';

const $ = (s) => document.querySelector(s);

// boundaries.py's numbers, kept identical so the floor and the polish cannot disagree
// about where a line ends.
const PAD_HEAD = 0.25;
const PAD_TAIL = 0.45;
const NEAR_WORD = 0.30;          // an end this soon after a line is heard as inside it
const FRAME = 1 / 30;
const ZOOM_HALF_S = 8;           // the zoomed strip shows ±8 s around the playhead
const MIN_WORD_PX = 14;          // words closer than this become a sentence bar
const SNAP_PX = 10;              // a dragged edge this close to a tick takes the tick
const DRAG_PX = 3;               // less movement than this is a click
const MIN_KEEP_S = 0.5;          // a keep shorter than this is nothing watched
const STAMP_MS = 350;            // the stamp lands before the next pick begins
const SECONDS_PER_PICK = 7;      // the design's "about seven seconds" — for the round ETA

const fmt = (t) => {
  t = Math.max(0, t || 0);
  const m = Math.floor(t / 60), s = t - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, '0')}`;
};
const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
const stem = (clip) => String(clip).replace(/\.[^.]+$/, '');
const r2 = (x) => Math.round(x * 100) / 100;

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function toast(msg, ms = 2200) {
  const el = $('#toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), ms);
}

/* ------------------------------------------------------------------ state */

const F = {
  P: null,                 // /api/project — transcripts with word timings, per clip
  picks: [],               // every pick the last fetch returned, verdicts attached
  queue: [],               // this round's picks, frozen when the round started
  i: 0,                    // where we are in the queue
  round: 1,
  order: 'rank',
  roundSize: 40,
  summary: null,           // counts, never a verdict on sufficiency
  dictation: null,         // what /api/picks said about the recogniser
  dictWarned: false,       // "not built yet" is said once
  auto: false,             // Caps Lock: advance after a verdict
  caps: false,             // the lamp as last seen on a key event
  mode: 'pass',            // pass | card | bin
  keep: { manualStart: null, manualEnd: null, edge: 'out' },
  watch: { start: 0, end: 0 },   // the contiguous extent watched from the preview start
  note: '',                // the note for the current pick, sent with its verdict
  undo: [],
  gen: 0,                  // play commands; a deferred callback that finds it moved does nothing
  playing: false,
  whole: false,            // `.` opened the whole clip: no stop at the preview end
  spaceHeld: false,
  shuttle: 0,              // J: negative rate driven by the tick; L: positive playbackRate
  bin: null,               // {list, k} while the closing card plays the bin
  writes: 0,               // completed writes — the tests wait on this
  pending: 0,
  card: null,              // what the closing card learned
  overlay: null,           // evidence | keymap | more | card
};

/* ----------------------------------------------------------------- helpers */

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

async function send(method, url, body) {
  F.pending++;
  try {
    const r = await fetch(url, {
      method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
    return data;
  } finally {
    F.pending--;
    F.writes++;
  }
}

function cur() {
  if (F.mode === 'bin' && F.bin) return F.bin.list[F.bin.k] || null;
  return F.queue[F.i] || null;
}

function clipOf(p) {
  return ((F.P || {}).clips || {})[p.clip] || {};
}

function utterances(p) {
  return (clipOf(p).transcript || [])
    .filter((u) => Number.isFinite(u.start) && Number.isFinite(u.end))
    .slice().sort((a, b) => a.start - b.start);
}

/* Every spoken word as {t, e, w}, the way boundaries._word_spans derives them: `e`
 * when the sidecar has it, else the next word's start (bounded), the last word falling
 * back to the utterance end. */
function words(p) {
  const out = [];
  for (const u of utterances(p)) {
    const ws = u.words || [];
    if (!ws.length) { out.push({ t: u.start, e: u.end, w: u.text || '' }); continue; }
    ws.forEach((w, k) => {
      if (!Number.isFinite(w.t)) return;
      let e = w.e;
      if (e == null) {
        const nxt = ws[k + 1] && ws[k + 1].t;
        e = nxt != null ? Math.min(nxt, w.t + 0.8) : u.end;
      }
      out.push({ t: w.t, e: Math.max(e, w.t), w: w.w || '' });
    });
  }
  return out.sort((a, b) => a.t - b.t);
}

/* Snapping. Outward only: a start inside a line goes to the line's start (less a
 * breath), an end inside a line — or so soon after it that the cut would sound like it
 * — goes to the line's end plus the tail pad. A boundary already in silence is clean
 * and stays where the watching put it. */
function snapStart(p, t) {
  const u = utterances(p).find((x) => x.start < t && t < x.end);
  return u ? Math.max(0, u.start - PAD_HEAD) : Math.max(0, t);
}

function snapEnd(p, t) {
  const dur = p.duration || Infinity;
  const u = utterances(p).find((x) => x.start < t && t < x.end + NEAR_WORD);
  return Math.min(dur, u ? u.end + PAD_TAIL : t);
}

function sentenceStarts(p) { return utterances(p).map((u) => Math.max(0, u.start - PAD_HEAD)); }
function sentenceEnds(p) { return utterances(p).map((u) => Math.min(p.duration || Infinity, u.end + PAD_TAIL)); }

const before = (list, t) => list.filter((x) => x < t - 0.01).pop();
const after = (list, t) => list.find((x) => x > t + 0.01);

/* The kept range: what was watched (or the edges the keys set), and what it snaps to. */
function keepRange() {
  const p = cur();
  if (!p) return { raw: [0, 0], snapped: [0, 0] };
  const dur = p.duration || Math.max(p.end, F.watch.end);
  let start = F.keep.manualStart != null ? F.keep.manualStart : F.watch.start;
  let end = F.keep.manualEnd != null ? F.keep.manualEnd : F.watch.end;
  // Nothing watched yet: the preview the machine offered, not its whole window.
  if (F.keep.manualEnd == null && end - start < MIN_KEEP_S) end = Math.max(end, p.preview[1]);
  let a = F.keep.manualStart != null ? start : snapStart(p, start);
  let b = F.keep.manualEnd != null ? end : snapEnd(p, end);
  a = Math.max(0, Math.min(a, dur));
  b = Math.max(a + 0.1, Math.min(b, dur));
  return { raw: [r2(start), r2(end)], snapped: [r2(a), r2(b)] };
}

/* ---------------------------------------------------------------- picture */

const pic = () => $('#pic');

function screenMsg(text, kind) {
  const el = $('#screenMsg');
  el.hidden = !text;
  el.className = kind || '';
  el.textContent = text || '';
}

const MEDIA_ERR = {
  1: 'the load was cancelled',
  2: 'the connection to the board dropped — is the server still running?',
  3: 'the browser could not decode it — the proxy may be half-written',
  4: 'that proxy would not open — it may still be building',
};

/* Point the picture at a clip's proxy without playing it. The `#t=` media fragment is
 * the difference between fetching the moment and fetching the top of the file (app.js
 * arm(): a shot at 188.2 s on Killington used to wait behind 0–15 s of buffering). */
function arm(v, src, at) {
  if (v.dataset.src !== src) {
    v.dataset.src = src;
    v.src = `${src}#t=${Math.max(0, at).toFixed(2)}`;
    v.load();
  }
  const park = () => { if (v.dataset.src === src) v.currentTime = at; };
  if (v.readyState >= 1) park();
  else v.addEventListener('loadedmetadata', park, { once: true });
}

function playRefused(err) {
  const gesture = err && err.name === 'NotAllowedError';
  F.playing = false;
  screenMsg(gesture ? 'the browser wants a key first — press space or L to play'
    : `the browser refused to play — ${(err && err.name) || 'error'}`, 'warn');
}

/* Play the current item from `at`. Every command bumps `gen`; a deferred callback that
 * finds it moved on does nothing. */
function play(at) {
  const p = cur();
  const v = pic();
  if (!p) return;
  const g = ++F.gen;
  arm(v, p.proxy, at);
  F.playing = true;
  F.shuttle = 0;
  v.playbackRate = 1;
  const go = () => {
    if (g !== F.gen || !F.playing) return;
    v.currentTime = at;
    v.play().catch((err) => { if (g === F.gen) playRefused(err); });
  };
  screenMsg(v.readyState >= 2 ? '' : `opening ${stem(p.clip)}…`);
  if (v.readyState >= 1) go(); else v.addEventListener('loadedmetadata', go, { once: true });
}

function resume() {
  const v = pic();
  const p = cur();
  if (!p) return;
  const g = ++F.gen;
  F.playing = true;
  F.shuttle = 0;
  v.playbackRate = 1;
  const stop = stopAt();
  if (v.currentTime >= stop - 0.05 && !F.whole && !F.spaceHeld) v.currentTime = p.preview[0];
  v.play().catch((err) => { if (g === F.gen) playRefused(err); });
}

function pause() {
  F.gen++;
  pic().pause();
  F.playing = false;
  F.shuttle = 0;
  pic().playbackRate = 1;
}

/* Park the picture on a frame — every trim gesture does this, so trimming has a picture. */
function park(t) {
  pause();
  const v = pic();
  const p = cur();
  if (!p) return;
  arm(v, p.proxy, t);
  if (v.readyState >= 1) v.currentTime = Math.max(0, Math.min(p.duration || t, t));
}

/* Where playback stops on its own: the preview end, unless space is held or the whole
 * clip was opened; in the bin, the select's end. */
function stopAt() {
  const p = cur();
  if (!p) return 0;
  if (F.mode === 'bin') return p.end;
  if (F.whole || F.spaceHeld) return p.duration || Infinity;
  return p.preview[1];
}

let lastTick = 0;

function tick(now) {
  const v = pic();
  const p = cur();
  const dt = lastTick ? Math.min(0.1, (now - lastTick) / 1000) : 0;
  lastTick = now;
  if (p && v.readyState >= 1) {
    if (F.shuttle < 0) {                 // J: the browser cannot play backwards, so we drive it
      v.currentTime = Math.max(0, v.currentTime + F.shuttle * dt);
      if (v.currentTime <= 0) F.shuttle = 0;
    }
    const t = v.currentTime;
    if (F.playing && !v.paused) {
      if (F.mode === 'pass' && t >= F.watch.start) F.watch.end = Math.max(F.watch.end, t);
      if (t >= stopAt() - 0.04 || v.ended) {
        v.pause();
        F.playing = false;
        if (F.mode === 'bin') binNext();
      }
    }
    paintHead(t);
    paintKeep();
  }
  requestAnimationFrame(tick);
}

/* ------------------------------------------------------------------ paint */

function paintAuto() {
  $('#hudAuto').classList.toggle('on', F.auto);
  $('#hudAuto').textContent = F.auto ? 'CAPS · AUTO-ADVANCE' : 'CAPS OFF · ↵ ADVANCES';
}

function paintHud() {
  const n = F.queue.length;
  const done = F.queue.filter((p) => p.verdict).length;
  $('#hudPos').innerHTML = n
    ? `round ${F.round} · pick <b>${F.i + 1}</b> of ${n} · queue frozen for this round`
    : `round ${F.round} · nothing to cull`;
  $('#hudDone').style.width = n ? `${(100 * done / n).toFixed(1)}%` : '0';
  $('#hudNow').style.left = n ? `${(100 * done / n).toFixed(1)}%` : '0';
  $('#hudNow').style.width = n ? `${(100 / n).toFixed(1)}%` : '0';
  const s = F.summary || {};
  $('#hudBin').innerHTML = `bin <b class="good">${s.moments || 0} moment${s.moments === 1 ? '' : 's'}</b>`
    + ` · ${s.heroes || 0} hero · if strung out <b>${fmt(s.strung_out_s || 0)}</b>`
    + (s.later ? ` · ${s.later} later` : '');
  paintAuto();
}

function paintContext() {
  const p = cur();
  if (!p) return;
  const c = clipOf(p);
  const dur = p.duration || c.duration || 0;
  if (F.mode === 'bin') {
    $('#ctxClip').textContent = stem(p.clip);
    $('#ctxClipMeta').textContent = `· ${fmt(dur)}`;
    $('#ctxPick').textContent = `${fmt(p.start)} → ${fmt(p.end)}`;
    $('#ctxOthers').textContent = `playing the bin · ${F.bin.k + 1} of ${F.bin.list.length} · Esc stops`;
    $('#ctxKeep').textContent = p.hero ? 'HERO' : 'kept';
    $('#ctxSnap').textContent = p.why || '';
    $('#ctxExtend').textContent = '';
    $('#ctxHint').innerHTML = 'every select in order, from the proxies · <span class="key">Esc</span> back to the card';
    return;
  }
  const others = F.picks.filter((q) => q.clip === p.clip && q.id !== p.id).length;
  $('#ctxClip').textContent = stem(p.clip);
  $('#ctxClipMeta').textContent = `· ${fmt(dur)}`;
  $('#ctxPick').textContent = `${fmt(p.start)} → ${fmt(p.end)}`
    + (p.preview[0] > p.start || p.preview[1] < p.end
      ? ` · preview ${fmt(p.preview[0])} → ${fmt(p.preview[1])}` : '');
  $('#ctxOthers').innerHTML = `${others} other pick${others === 1 ? '' : 's'} in this clip`
    + ` · <span class="key">.</span> open the clip`;
  $('#ctxHint').innerHTML = 'hold <span class="key">space</span> to keep watching · what you watch is what you keep';
  paintKeep(true);
}

let keepKey = '';

function paintKeep(force) {
  const p = cur();
  if (!p || F.mode !== 'pass') { $('#zoomKeep').style.width = '0'; return; }
  const k = keepRange();
  const key = `${k.raw}|${k.snapped}|${F.keep.edge}|${F.keep.manualStart}|${F.keep.manualEnd}`;
  if (key === keepKey && !force) return;
  keepKey = key;
  const manual = F.keep.manualStart != null || F.keep.manualEnd != null;
  $('#ctxKeep').textContent = (manual ? 'trimmed: ' : "what you've watched: ")
    + `${fmt(k.raw[0])}–${fmt(k.raw[1])}`;
  const moved = k.snapped[0] !== k.raw[0] || k.snapped[1] !== k.raw[1];
  $('#ctxSnap').textContent = moved
    ? `will snap → ${fmt(k.snapped[0])}–${fmt(k.snapped[1])} (sentence end + ${PAD_TAIL})`
    : `keeps ${fmt(k.snapped[0])}–${fmt(k.snapped[1])} · ${(k.snapped[1] - k.snapped[0]).toFixed(1)} s`;
  const nxt = utterances(p).find((u) => u.end + PAD_TAIL > k.snapped[1] + 0.01);
  $('#ctxExtend').innerHTML = nxt
    ? `<span class="key">}</span> extend to the next line: "${escapeHtml(nxt.text || '')}" at ${fmt(nxt.start)}`
    : '';
  $('#zoomInfo').textContent = `${fmt(k.snapped[0])} → ${fmt(k.snapped[1])} · ${(F.watch.end - F.watch.start).toFixed(1)} s watched`;
  // words inside the keep light up
  const [a, b] = k.snapped;
  $('#zoomInner').querySelectorAll('.w, .sb').forEach((el) => {
    const t0 = parseFloat(el.dataset.t0), t1 = parseFloat(el.dataset.t1);
    el.classList.toggle('in', t1 > a && t0 < b);
  });
  $('#handleIn').classList.toggle('active', F.keep.edge === 'in');
  $('#handleOut').classList.toggle('active', F.keep.edge === 'out');
  paintHead(pic().currentTime || 0);
}

function sealClass(w) {
  if (w.kind === 'felt') return 'tele';
  if (w.kind === 'heard') return 'heard';
  if (w.kind === 'theme') return 'theme';
  return { audited: 'aud', contradicted: 'contra' }[w.state] || 'claim';
}

function sealLabel(w) {
  if (w.kind === 'felt') return 'FELT · NUMBERS ONLY';
  if (w.kind === 'heard') return 'HEARD';
  if (w.kind === 'theme') return `THEME · ${String(w.text || '').toUpperCase()}`;
  const claim = w.event_kind ? `CLAIMED "${String(w.event_kind).toUpperCase()}"` : 'CLAIMED';
  const state = { audited: 'AUDITED', contradicted: 'CONTRADICTED', claimed: 'CLAIMED' }[w.state] || '';
  return `SEEN · ${claim}${state && state !== 'CLAIMED' ? ` · ${state}` : ''}`;
}

function paintSeals() {
  const p = cur();
  const box = $('#seals');
  box.innerHTML = '';
  if (!p) return;
  const list = (p.witnesses || []).slice();
  (p.tags || []).forEach((t) => list.push({ kind: 'theme', text: t }));
  list.forEach((w) => {
    const d = document.createElement('div');
    d.className = `seal ${sealClass(w)}`;
    const when = w.kind === 'theme' ? '' : `${fmt(w.at != null ? w.at : w.start)} `;
    const text = w.kind === 'theme' ? 'from this pick’s words' : (w.text || '');
    d.innerHTML = `<span class="s">${escapeHtml(sealLabel(w))}</span><span>${escapeHtml(when)}${escapeHtml(text)}</span>`;
    box.appendChild(d);
  });
  paintNote();
}

function paintNote(meta) {
  const p = cur();
  const text = F.mode === 'pass' ? F.note : (p ? p.note || '' : '');
  $('#noteSlot').classList.toggle('empty', !text);
  $('#noteText').textContent = text ? `“${text}”` : 'no note yet';
  $('#noteMeta').textContent = text ? `· ${meta ? `${meta} · ` : ''}N edit` : '';
  $('#dictHint').hidden = F.dictation === false && F.dictWarned;
}

function paintCaption() {
  const p = cur();
  if (!p) return;
  $('#why').textContent = p.why || (F.mode === 'bin' ? '' : '(no reason — the witnesses did not agree)');
  $('#conflict').textContent = p.conflict ? `· ${p.conflict}` : '';
  $('#rank').innerHTML = F.mode === 'bin' ? ''
    : `rank ${p.rank} · ${p.kind || ''} · <span class="key">E</span> evidence in full`;
}

function paintTape() {
  const p = cur();
  if (!p) return;
  const dur = p.duration || 1;
  const mine = F.picks.filter((q) => q.clip === p.clip);
  const felt = mine.flatMap((q) => (q.witnesses || []).filter((w) => w.kind === 'felt'));
  $('#tapeLbl').textContent = `${stem(p.clip).toUpperCase()} · WHOLE CLIP ${fmt(dur)} · picks as markers`
    + (felt.length ? ' · telemetry' : '');
  // One mark per pick. A mark in this round's queue jumps there on a click — decided or
  // not, so a verdict can be revisited; a pick decided in an earlier round is shown but
  // not reachable, because the queue is frozen (rule 3) and only undo may put a pick back.
  const marks = $('#tapeMarks');
  marks.innerHTML = '';
  mine.forEach((q) => {
    const s = document.createElement('span');
    const here = q.id === p.id;
    const inQueue = F.queue.indexOf(q) >= 0;
    const cls = here ? 'now' : q.verdict === 'pick' ? (q.hero ? 'hero' : 'pick') : (q.verdict || '');
    s.className = `${cls}${inQueue && !here ? ' jump' : ''}`;
    s.dataset.id = String(q.id);
    s.style.left = `${(100 * q.start / dur).toFixed(2)}%`;
    s.style.width = `${(100 * (q.end - q.start) / dur).toFixed(2)}%`;
    const state = here ? 'this pick' : q.hero ? 'hero' : q.verdict === 'pick' ? 'picked' : q.verdict || 'undecided';
    s.title = `${fmt(q.start)}–${fmt(q.end)} · rank ${q.rank} · ${state}${q.why ? `\n${q.why}` : ''}`
      + (here ? '' : inQueue ? '\nclick to jump to it' : '\ndecided in an earlier round — not in this queue');
    marks.appendChild(s);
  });
  // A telemetry trace only when a `felt` witness exists — numbers, never event names.
  const svg = $('#tapeTrace');
  svg.innerHTML = '';
  if (felt.length) {
    const vals = felt.map((w) => ({ at: w.at, v: parseFloat(String(w.text)) || 1 }));
    const vmax = Math.max(...vals.map((x) => x.v), 1e-6);
    const pts = [[0, 40]];
    vals.sort((a, b) => a.at - b.at).forEach((x) => {
      const px = 1000 * x.at / dur;
      pts.push([px - 6, 40], [px, 40 - 30 * x.v / vmax], [px + 6, 40]);
    });
    pts.push([1000, 40]);
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', pts.map((q, k) => `${k ? 'L' : 'M'}${q[0].toFixed(1)} ${q[1].toFixed(1)}`).join(' '));
    path.setAttribute('stroke', '#4a86b8');
    path.setAttribute('stroke-width', '1.4');
    path.setAttribute('fill', 'none');
    svg.appendChild(path);
  }
}

/* The zoomed strip is built once per pick at a fixed scale and slid under a fixed
 * playhead, so a frame costs one transform rather than a rebuild. */
let zoom = { pps: 60, built: '' };

function buildZoom() {
  const p = cur();
  const inner = $('#zoomInner');
  inner.innerHTML = '';
  if (!p) return;
  const width = $('#zoom').clientWidth || 1000;
  zoom.pps = width / (2 * ZOOM_HALF_S);
  const dur = p.duration || 1;
  inner.style.width = `${dur * zoom.pps}px`;
  const ws = words(p);
  utterances(p).forEach((u) => {
    const mine = ws.filter((w) => w.t >= u.start - 0.01 && w.t <= u.end + 0.01);
    const gaps = mine.slice(1).map((w, k) => (w.t - mine[k].t) * zoom.pps);
    const fit = mine.length && gaps.every((g) => g >= MIN_WORD_PX)
      && (mine.length === 1 || Math.min(...gaps) >= MIN_WORD_PX);
    if (fit) {
      mine.forEach((w) => {
        const s = document.createElement('span');
        s.className = 'w';
        s.textContent = w.w;
        s.style.left = `${(w.t * zoom.pps).toFixed(1)}px`;
        s.dataset.t0 = w.t; s.dataset.t1 = w.e;
        inner.appendChild(s);
      });
    } else {
      const b = document.createElement('div');
      b.className = 'sb';
      b.style.left = `${(u.start * zoom.pps).toFixed(1)}px`;
      b.style.width = `${Math.max(3, (u.end - u.start) * zoom.pps).toFixed(1)}px`;
      b.title = u.text || '';
      b.dataset.t0 = u.start; b.dataset.t1 = u.end;
      b.innerHTML = `<i>${escapeHtml(String(u.text || '').slice(0, 40))}</i>`;
      inner.appendChild(b);
    }
    // snap ticks — what the keys and a dragged handle land on: the sentence end + 0.45,
    // the sentence start − 0.25, and every word start. Each carries its time so a drag
    // can light the one it took.
    const tick = (cls, t, title) => {
      const el = document.createElement('span');
      el.className = `snap ${cls}`;
      el.style.left = `${(t * zoom.pps).toFixed(1)}px`;
      el.dataset.t = r2(t);
      if (title) el.title = title;
      inner.appendChild(el);
    };
    tick('end', u.end + PAD_TAIL, `sentence end + ${PAD_TAIL}`);
    tick('start', Math.max(0, u.start - PAD_HEAD), `sentence start − ${PAD_HEAD}`);
    mine.forEach((w) => tick('word', w.t));
  });
  (p.witnesses || []).filter((w) => w.kind === 'felt').forEach((w) => {
    const f = document.createElement('span');
    f.className = 'felt';
    f.textContent = `▼ ${w.text}`;
    f.style.left = `${(w.at * zoom.pps).toFixed(1)}px`;
    inner.appendChild(f);
  });
  zoom.built = p.id || p.clip;
  keepKey = '';
}

function paintHead(t) {
  const p = cur();
  if (!p) return;
  const dur = p.duration || 1;
  $('#tapeHead').style.left = `${(100 * Math.min(1, t / dur)).toFixed(2)}%`;
  const width = $('#zoom').clientWidth || 1000;
  // px of clip time 0: the strip follows the playhead — except while a pointer is down,
  // when it holds still and the playhead moves instead, or every park would slide the
  // strip out from under the finger.
  const x0 = zoom.lock != null ? zoom.lock : width / 2 - t * zoom.pps;
  $('#zoomInner').style.transform = `translateX(${x0.toFixed(1)}px)`;
  $('#zoomHead').style.left = zoom.lock != null ? `${(x0 + t * zoom.pps).toFixed(1)}px` : '50%';
  if (F.mode !== 'pass') {
    $('#zoomKeep').style.width = '0';
    $('#handleIn').hidden = $('#handleOut').hidden = true;
    $('#zoomHint').textContent = '';
    return;
  }
  const k = keepRange();
  const [a, b] = k.snapped;
  $('#zoomKeep').style.left = `${(x0 + a * zoom.pps).toFixed(1)}px`;
  $('#zoomKeep').style.width = `${((b - a) * zoom.pps).toFixed(1)}px`;
  $('#handleIn').hidden = $('#handleOut').hidden = false;
  $('#handleIn').style.left = `${(x0 + a * zoom.pps).toFixed(1)}px`;
  $('#handleOut').style.left = `${(x0 + b * zoom.pps).toFixed(1)}px`;
  const edgeT = F.keep.edge === 'in' ? a : b;
  const raw = F.keep.edge === 'in' ? k.raw[0] : k.raw[1];
  const hint = $('#zoomHint');
  hint.style.left = `${(x0 + edgeT * zoom.pps).toFixed(1)}px`;
  const dragging = drag.kind === 'in' || drag.kind === 'out' || drag.kind === 'slide';
  hint.classList.toggle('drag', dragging);
  // while a handle moves, the time reads out under it, with the tick it took
  if (dragging) hint.textContent = `${fmt(edgeT)}${drag.snap ? ` · ${drag.snap}` : ''}`;
  else hint.textContent = Math.abs(edgeT - raw) > 0.01
    ? (F.keep.edge === 'in' ? 'snap ◂ sentence start − 0.25' : `sentence end + ${PAD_TAIL} ▸`) : '';
}

function stamp(kind, hero, extra) {
  const el = $('#stamp');
  const word = hero ? 'HERO' : { pick: 'PICKED', reject: 'REJECTED', later: 'LATER' }[kind];
  el.textContent = extra ? `${word} ${extra}` : word;
  el.className = hero ? 'hero' : kind;
  el.hidden = false;
  // restart the fade
  el.classList.remove('faded');
  void el.offsetWidth;
  el.classList.add('faded');
}

function clearStamp() {
  const el = $('#stamp');
  el.hidden = true;
  el.className = '';
}

function paintAll() {
  paintHud();
  paintContext();
  paintSeals();
  paintCaption();
  paintTape();
  if (zoom.built !== (cur() && (cur().id || cur().clip))) buildZoom();
  paintKeep(true);
}

/* ------------------------------------------------------------- the queue */

function undecided(list) {
  return list.filter((p) => p.released && !p.verdict);
}

/* Freeze a round: undecided picks of released clips in the stored order, `roundSize`
 * of them. Nothing re-fetches mid-round. */
function freeze(picks, { laters = false } = {}) {
  const pool = laters ? picks.filter((p) => p.released && p.verdict === 'later') : undecided(picks);
  F.queue = pool.slice(0, F.roundSize);
  F.laters = laters;
}

/* The position the EDL stores: how many still-undecided picks of this round sit before
 * the current one. The queue is rebuilt from the undecided picks on load, so this index
 * lands on the same pick after a reload whether or not anything was skipped. */
function positionIndex() {
  return F.queue.slice(0, F.i).filter((p) => !p.verdict).length;
}

async function savePosition() {
  try {
    await send('PUT', '/api/floor/position',
      { round: F.round, index: positionIndex(), order: F.order });
  } catch (e) {
    toast(`could not save the position: ${e.message}`, 4000);
  }
}

function show(i, { autoplay = true } = {}) {
  F.mode = 'pass';
  F.i = Math.max(0, Math.min(F.queue.length - 1, i));
  const p = cur();
  if (F.overlay === 'card') leaveCard(); else closeOverlay();
  clearStamp();
  if (!p) return closingCard();
  F.keep = { manualStart: null, manualEnd: null, edge: 'out' };
  F.watch = { start: p.preview[0], end: p.preview[0] };
  F.note = p.note || '';
  F.whole = false;
  F.spaceHeld = false;
  if (p.verdict) stamp(p.verdict, p.hero && p.verdict === 'pick');
  paintAll();
  if (autoplay) play(p.preview[0]); else park(p.preview[0]);
}

function advance() {
  if (F.mode !== 'pass') return;
  if (F.i + 1 >= F.queue.length) return closingCard();
  show(F.i + 1);
  savePosition();
}

function back() {
  if (F.mode !== 'pass' || F.i === 0) return;
  show(F.i - 1);
  savePosition();
}

/* ----------------------------------------------------------------- verdicts */

function verdictBody(p, kind, range, hero, note) {
  return {
    clip: p.clip, start: range[0], end: range[1], verdict: kind, hero: !!hero,
    why: p.why || '', witnesses: p.witnesses || [], tags: p.tags || [],
    note: note || '', source: 'floor',
  };
}

function snapshot(p) {
  return { verdict: p.verdict || null, hero: !!p.hero, note: p.note || '', sent: p.sent || null };
}

async function verdict(kind, { hero = false } = {}) {
  const p = cur();
  if (!p || F.mode !== 'pass') return;
  closeOverlay();
  pause();
  const isPick = kind === 'pick';
  const range = isPick ? keepRange().snapped : [r2(p.start), r2(p.end)];
  const heroNow = hero || (isPick && !!p.hero && p.verdict === 'pick');
  const entry = {
    i: F.i, items: [{ p, prev: snapshot(p), sent: range }],
    keep: { ...F.keep }, watch: { ...F.watch }, note: F.note,
  };
  let data;
  try {
    data = await send('POST', '/api/floor/verdict', verdictBody(p, kind, range, heroNow, F.note));
  } catch (e) {
    return toast(`the verdict did not land: ${e.message}`, 6000);
  }
  F.summary = data.summary;
  p.verdict = kind;
  p.hero = isPick ? heroNow : false;
  p.note = F.note;
  p.sent = range;
  F.undo.push(entry);
  if (F.undo.length > 100) F.undo.shift();
  stamp(kind, heroNow);
  paintHud();
  paintTape();
  savePosition();
  if (F.auto) setTimeout(() => { if (cur() === p && F.mode === 'pass') advance(); }, STAMP_MS);
}

/* ⇧X: the rest of this clip's undecided picks in the queue, from here on, in one undo. */
async function rejectRest() {
  const p = cur();
  if (!p || F.mode !== 'pass') return;
  closeOverlay();
  pause();
  const items = F.queue.slice(F.i).filter((q) => q.clip === p.clip && !q.verdict);
  if (!items.length) return toast('nothing left undecided in this clip');
  const entry = {
    i: F.i, items: [], keep: { ...F.keep }, watch: { ...F.watch }, note: F.note,
  };
  for (const q of items) {
    const range = [r2(q.start), r2(q.end)];
    try {
      const data = await send('POST', '/api/floor/verdict',
        verdictBody(q, 'reject', range, false, q === p ? F.note : ''));
      F.summary = data.summary;
    } catch (e) {
      toast(`reject did not land: ${e.message}`, 6000);
      break;
    }
    entry.items.push({ p: q, prev: snapshot(q), sent: range });
    q.verdict = 'reject';
    q.hero = false;
    q.sent = range;
  }
  if (!entry.items.length) return;
  F.undo.push(entry);
  stamp('reject', false, entry.items.length > 1 ? `×${entry.items.length}` : '');
  paintHud();
  paintTape();
  toast(`rejected ${entry.items.length} pick${entry.items.length === 1 ? '' : 's'} in ${stem(p.clip)}`);
  // past the ones just rejected, whatever the auto-advance state: that was the point
  const next = F.queue.findIndex((q, k) => k > F.i && !entry.items.some((it) => it.p === q));
  setTimeout(() => {
    if (F.mode !== 'pass') return;
    if (next < 0) closingCard(); else { show(next); savePosition(); }
  }, STAMP_MS);
}

/* ⌘Z: the last verdict comes back off the EDL with its trim and its note — across a
 * rejected clip, and across an advance. */
async function undo() {
  const entry = F.undo.pop();
  if (!entry) return toast('nothing to undo');
  closeOverlay();
  pause();
  for (const it of entry.items.slice().reverse()) {
    const p = it.p;
    try {
      let data = await send('POST', '/api/floor/verdict',
        { clip: p.clip, start: it.sent[0], end: it.sent[1], verdict: 'clear' });
      if (it.prev.verdict) {
        const range = it.prev.sent || [r2(p.start), r2(p.end)];
        data = await send('POST', '/api/floor/verdict',
          verdictBody(p, it.prev.verdict, range, it.prev.hero, it.prev.note));
      }
      F.summary = data.summary;
    } catch (e) {
      toast(`undo did not land: ${e.message}`, 6000);
    }
    p.verdict = it.prev.verdict;
    p.hero = it.prev.hero;
    p.note = it.prev.note;
    p.sent = it.prev.sent;
  }
  const first = entry.items[0].p;
  if (F.overlay === 'card') leaveCard();
  F.mode = 'pass';
  F.bin = null;
  let at = F.queue.indexOf(first);
  if (at < 0) {                      // undone across a round boundary: back in front of you
    at = Math.min(F.i, F.queue.length);
    F.queue.splice(at, 0, first);
  }
  F.i = at;
  F.keep = { ...entry.keep };
  F.watch = { ...entry.watch };
  F.note = entry.note;
  F.whole = false;
  clearStamp();
  if (first.verdict) stamp(first.verdict, first.hero);
  paintAll();
  park(keepRange().snapped[1]);
  savePosition();
  toast('undone — the verdict, its trim and its note');
}

/* -------------------------------------------------------------------- trim */

function setIn(t) {
  const p = cur();
  if (!p) return;
  const k = keepRange();
  F.keep.manualStart = r2(Math.max(0, Math.min(t, k.snapped[1] - 0.2)));
  F.keep.edge = 'in';
  park(F.keep.manualStart);
  paintKeep(true);
}

function setOut(t) {
  const p = cur();
  if (!p) return;
  const k = keepRange();
  const dur = p.duration || t;
  F.keep.manualEnd = r2(Math.max(k.snapped[0] + 0.2, Math.min(t, dur)));
  F.keep.edge = 'out';
  park(F.keep.manualEnd);
  paintKeep(true);
}

function stepEdge(dir, byWord) {
  const p = cur();
  if (!p) return;
  const k = keepRange();
  const edge = F.keep.edge;
  const at = edge === 'in' ? k.snapped[0] : k.snapped[1];
  let t;
  if (byWord) {
    const starts = words(p).map((w) => w.t);
    t = dir < 0 ? before(starts, at) : after(starts, at);
    if (t == null) t = dir < 0 ? 0 : (p.duration || at);
  } else {
    t = at + dir * FRAME;
  }
  if (edge === 'in') setIn(t); else setOut(t);
}

/* ------------------------------------------------------------------- pointer */

/* One pointer listener per strip. On the closer strip a handle trims its edge, the band's
 * middle slides the whole range, and anything else scrubs the playhead — a plain click
 * seeks. On the tape a mark in the queue jumps to its pick; anything else scrubs or seeks.
 * `setPointerCapture` keeps the gesture when the pointer leaves the strip. */
const drag = { kind: null, moved: false, x: 0, t0: 0, a: 0, b: 0, snap: '' };
const tapeDrag = { on: false, moved: false, x: 0 };

const clampT = (p, t) => Math.max(0, Math.min(p.duration || t, t));

/* Where an edge may land by magnet: the same places the keys go. */
function snapTargets(p, edge) {
  const lines = edge === 'in'
    ? sentenceStarts(p).map((t) => ({ t, why: `sentence start − ${PAD_HEAD}` }))
    : sentenceEnds(p).map((t) => ({ t, why: `sentence end + ${PAD_TAIL}` }));
  return lines.concat(words(p).map((w) => ({ t: w.t, why: `"${w.w}"` })));
}

function magnet(p, t, edge) {
  let best = null;
  for (const c of snapTargets(p, edge)) {
    const d = Math.abs(c.t - t) * zoom.pps;
    if (d <= SNAP_PX && (!best || d < best.d)) best = { ...c, d };
  }
  return best;
}

function lightTick(t) {
  $('#zoomInner').querySelectorAll('.snap').forEach((el) => {
    el.classList.toggle('lit', t != null && Math.abs(parseFloat(el.dataset.t) - t) < 0.005);
  });
}

function zoomTime(clientX) {
  const r = $('#zoom').getBoundingClientRect();
  const x0 = zoom.lock != null ? zoom.lock : r.width / 2 - (pic().currentTime || 0) * zoom.pps;
  return (clientX - r.left - x0) / zoom.pps;
}

function tapeTime(clientX) {
  const r = $('#tape').getBoundingClientRect();
  return (clientX - r.left) / r.width * ((cur() || {}).duration || 0);
}

/* Move the playhead. Playing stays playing, paused stays parked. Outside the preview the
 * whole clip is open, so playback does not stop at a preview end behind you and space does
 * not rewind to a preview start you left on purpose. */
function seek(t) {
  const p = cur();
  if (!p) return;
  t = clampT(p, t);
  if (F.mode === 'pass' && (t < p.preview[0] - 0.01 || t > p.preview[1] + 0.01)) F.whole = true;
  if (F.playing && !pic().paused) play(t); else park(t);
}

function dragEdge(edge, t) {
  const p = cur();
  const m = magnet(p, t, edge);
  drag.snap = m ? m.why : '';
  lightTick(m ? m.t : null);
  if (edge === 'in') setIn(m ? m.t : clampT(p, t)); else setOut(m ? m.t : clampT(p, t));
}

/* Slide: the range as it was on pointer-down, moved by the pointer's travel, its length
 * kept, clamped to the clip. */
function slideTo(dt) {
  const p = cur();
  const len = drag.b - drag.a;
  const a = Math.max(0, Math.min((p.duration || drag.b) - len, drag.a + dt));
  F.keep.manualStart = r2(a);
  F.keep.manualEnd = r2(a + len);
  drag.snap = `${len.toFixed(1)} s, sliding`;
  park(F.keep.edge === 'in' ? a : a + len);
  paintKeep(true);
}

function zoomDown(e) {
  const p = cur();
  if (!p || F.mode !== 'pass' || e.button !== 0) return;
  const id = e.target.id;
  const kind = id === 'handleIn' ? 'in' : id === 'handleOut' ? 'out' : id === 'zoomKeep' ? 'slide' : 'seek';
  const r = $('#zoom').getBoundingClientRect();
  zoom.lock = r.width / 2 - (pic().currentTime || 0) * zoom.pps;
  const k = keepRange();
  Object.assign(drag, { kind, moved: false, x: e.clientX, t0: zoomTime(e.clientX), a: k.snapped[0], b: k.snapped[1], snap: '' });
  $('#zoom').setPointerCapture(e.pointerId);
  $('#zoom').classList.add(`drag-${kind}`);
  if (kind === 'in' || kind === 'out') {         // taking a handle makes it the edge and shows its frame
    F.keep.edge = kind;
    park(kind === 'in' ? k.snapped[0] : k.snapped[1]);
  }
  paintKeep(true);
  e.preventDefault();
}

function zoomMove(e) {
  if (!drag.kind) return;
  if (!drag.moved && Math.abs(e.clientX - drag.x) < DRAG_PX) return;
  drag.moved = true;
  const t = zoomTime(e.clientX);
  if (drag.kind === 'in' || drag.kind === 'out') dragEdge(drag.kind, t);
  else if (drag.kind === 'slide') slideTo(t - drag.t0);
  else park(clampT(cur(), t));
}

function zoomUp(e, cancelled) {
  if (!drag.kind) return;
  const kind = drag.kind;
  const t = zoomTime(e.clientX);
  drag.kind = null;
  drag.snap = '';
  zoom.lock = null;
  lightTick(null);
  $('#zoom').classList.remove('drag-in', 'drag-out', 'drag-slide', 'drag-seek');
  if (!cancelled && (kind === 'seek' || (kind === 'slide' && !drag.moved))) seek(t);
  paintKeep(true);
}

function tapeDown(e) {
  const p = cur();
  if (!p || F.mode !== 'pass' || e.button !== 0) return;
  const m = e.target.closest ? e.target.closest('#tapeMarks span') : null;
  if (m && m.classList.contains('jump')) {
    const at = F.queue.findIndex((q) => String(q.id) === m.dataset.id);
    if (at >= 0 && at !== F.i) { show(at); savePosition(); return; }
  }
  Object.assign(tapeDrag, { on: true, moved: false, x: e.clientX });
  $('#tape').setPointerCapture(e.pointerId);
  e.preventDefault();
}

function tapeMove(e) {
  if (!tapeDrag.on) return;
  if (!tapeDrag.moved && Math.abs(e.clientX - tapeDrag.x) < DRAG_PX) return;
  tapeDrag.moved = true;
  park(clampT(cur(), tapeTime(e.clientX)));
}

function tapeUp(e, cancelled) {
  if (!tapeDrag.on) return;
  tapeDrag.on = false;
  if (!cancelled) seek(tapeTime(e.clientX));
}

/* ------------------------------------------------------------------- notes */

function editNote() {
  const ta = $('#noteEdit');
  const p = cur();
  ta.value = F.mode === 'pass' ? F.note : (p ? p.note || '' : '');
  ta.hidden = false;
  $('#noteText').hidden = true;
  $('#noteMeta').hidden = true;
  ta.focus();
}

function closeNote() {
  $('#noteEdit').hidden = true;
  $('#noteText').hidden = false;
  $('#noteMeta').hidden = false;
  $('#noteEdit').blur();
}

/* A note on an undecided pick waits for the verdict — /api/floor/note would make it a
 * `later` on its own, which is a verdict nobody gave. A decided pick's note lands now. */
async function setNote(text, meta) {
  const p = cur();
  if (!p) return;
  text = String(text || '').trim();
  F.note = text;
  p.note = text;
  paintNote(meta);
  if (F.mode === 'pass' && p.verdict) {
    const range = p.sent || [r2(p.start), r2(p.end)];
    try {
      const data = await send('POST', '/api/floor/note',
        { clip: p.clip, start: range[0], end: range[1], text });
      F.summary = data.summary;
      paintHud();
    } catch (e) {
      toast(`the note did not land: ${e.message}`, 5000);
    }
  }
}

/* --------------------------------------------------------------- dictation */

const dict = { rec: null, stream: null, chunks: [], held: false, vol: null };

function dictFallback(why) {
  if (!F.dictWarned) {
    toast(`${why} — N to type`, 4000);
    F.dictWarned = true;
  }
  $('#dictState').textContent = '';
  $('#dictState').className = 'hint small';
  paintNote();
  editNote();
}

async function dictStart() {
  if (dict.held || F.mode !== 'pass') return;
  dict.held = true;
  const v = pic();
  dict.vol = v.volume;
  v.volume = 0.1;                                  // the clip ducks while you speak
  $('#dictState').textContent = 'listening…';
  $('#dictState').className = 'hint small live';
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    dictStop();
    return dictFallback('no microphone in this browser');
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    dictStop();
    return dictFallback(`no microphone — ${e.name || e}`);
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
  const v = pic();
  if (dict.vol != null) v.volume = dict.vol;
  dict.vol = null;
  if (dict.rec && dict.rec.state !== 'inactive') dict.rec.stop();
  else {
    $('#dictState').textContent = '';
    $('#dictState').className = 'hint small';
  }
}

async function dictSend(blob) {
  $('#dictState').textContent = 'transcribing…';
  $('#dictState').className = 'hint small';
  let r;
  try {
    r = await fetch('/api/dictate', {
      method: 'POST', headers: { 'content-type': blob.type || 'audio/webm' }, body: blob,
    });
  } catch (e) {
    return dictFallback('dictation unreachable');
  }
  if (r.status === 501) return dictFallback('dictation not built yet');
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    $('#dictState').textContent = '';
    return toast(`dictation failed: ${d.detail || r.status}`, 5000);
  }
  const d = await r.json();
  $('#dictState').textContent = '';
  if (!d.text) return toast('heard nothing — N to type', 3000);
  await setNote(d.text, `${((d.latency_ms || 0) / 1000).toFixed(1)} s`);
}

/* ---------------------------------------------------------------- overlays */

function openOverlay(kind, html) {
  F.overlay = kind;
  $('#overlay').dataset.kind = kind;
  $('#overlayBox').innerHTML = html;
  $('#overlay').hidden = false;
}

function closeOverlay() {
  if (F.overlay === 'card') return;          // the card is a mode, not a drawer
  F.overlay = null;
  $('#overlay').hidden = true;
  $('#overlayBox').innerHTML = '';
  delete $('#overlay').dataset.kind;
}

function toggleOverlay(kind, html) {
  if (F.overlay === kind) return closeOverlay();
  openOverlay(kind, html());
}

function evidenceHtml() {
  const p = cur();
  const rows = (p.witnesses || []).map((w) => `<div class="ev">
    <span class="s seal ${sealClass(w)}" style="display:inline-block;padding:2px 8px">${escapeHtml(sealLabel(w))}</span>
    <div style="margin-top:4px">${escapeHtml(fmt(w.start))}–${escapeHtml(fmt(w.end))}${w.at != null ? ` · at ${fmt(w.at)}` : ''}
      ${w.score != null ? ` · score ${Number(w.score).toFixed(2)}` : ''}${w.notable ? ' · notable' : ''}</div>
    <div>${escapeHtml(w.text || '')}</div>
    <pre>${escapeHtml(JSON.stringify(w, null, 1))}</pre></div>`).join('');
  return `<h2>Evidence <span class="hint">${escapeHtml(stem(p.clip))} ${fmt(p.start)}–${fmt(p.end)} · rank ${p.rank} · score ${Number(p.score || 0).toFixed(2)}</span></h2>
    <div class="hint" style="margin-bottom:8px">${escapeHtml(p.why || '')}${p.conflict ? `<div class="bad">${escapeHtml(p.conflict)}</div>` : ''}</div>
    ${rows || '<div class="hint">no witnesses</div>'}
    <div class="hint small" style="margin-top:10px">any verdict key closes this · <span class="key">E</span> / <span class="key">Esc</span> close</div>`;
}

function keymapHtml() {
  const rows = [
    ['P', 'pick — to the bin, with the reason and any note'], ['X', 'reject — stays on the floor'],
    ['U', 'later — the pile the closing card offers back'], ['1', 'hero — must appear in the first cut'],
    ['⇧X', 'reject the rest of this clip’s picks'], ['⌘Z', 'undo the last verdict, with its trim and note'],
    ['Caps', 'auto-advance after a verdict'], ['J K L', 'shuttle — K pauses'],
    ['space', 'hold to keep watching past the preview'], ['[ ]', 'in-point to the previous / next sentence'],
    ['{ }', 'out-point likewise — } extends to the reaction'], ['← →', 'frame step at the active edge (⇧ for a word)'],
    ['↵', 'next pick · ⌫ previous'], ['V', 'hold to speak a note; N edits it'],
    ['E', 'evidence drawer'], ['.', 'more: open the whole clip · look closer · find like this'],
    ['?', 'this map'],
    ['drag', 'the green band’s edges trim it, its middle slides it · click a strip to seek, drag to scrub · click a mark on the tape to jump to that pick'],
  ];
  return `<h2>The keys</h2><div class="keymap">${rows.map(([k, t]) =>
    `<div><span class="key">${escapeHtml(k)}</span><span>${escapeHtml(t)}</span></div>`).join('')}</div>`;
}

function moreHtml() {
  return `<h2>More</h2><div class="menu">
    <div><span class="key">O</span> open the whole clip <span class="hint">play from 0 with the tape</span></div>
    <div><span class="key">L</span> look closer <span class="hint">a closer look at this window · not on the floor yet</span></div>
    <div><span class="key">F</span> find like this <span class="hint">the board’s Find, prefilled · not on the floor yet</span></div>
  </div><div class="hint small" style="margin-top:10px"><span class="key">Esc</span> close</div>`;
}

function openWhole() {
  closeOverlay();
  F.whole = true;
  play(0);
}

/* ------------------------------------------------------------ the closing card */

async function closingCard() {
  pause();
  F.mode = 'card';
  F.bin = null;
  let d;
  try {
    d = await getJSON(`/api/picks?order=${F.order}`);       // the one re-fetch: at the boundary
  } catch (e) {
    return toast(`could not read the bin: ${e.message}`, 6000);
  }
  F.summary = d.summary;
  F.dictation = d.dictation;
  const seen = new Set(F.queue.map((p) => p.id));
  const fresh = undecided(d.picks);
  const arrivals = fresh.filter((p) => !seen.has(p.id));
  const worst = Math.max(0, ...F.queue.map((p) => p.rank || 0));
  const outrank = arrivals.filter((p) => (p.rank || 0) < worst).length;
  const laters = d.picks.filter((p) => p.released && p.verdict === 'later').length;
  const decided = F.queue.filter((p) => p.verdict).length;
  const clips = new Set(F.queue.map((p) => p.clip)).size;
  const s = d.summary;
  F.card = { picks: d.picks, remaining: fresh.length, laters };
  paintHud();
  const nextN = Math.min(F.roundSize, fresh.length);
  openOverlay('card', `
    <div style="display:flex;align-items:baseline;gap:12px;margin-bottom:12px">
      <h2 style="margin:0">Round ${F.round} done</h2>
      <span class="hint tnum">${F.queue.length} pick${F.queue.length === 1 ? '' : 's'} · ${decided} decided · ${clips} clip${clips === 1 ? '' : 's'}</span>
      <span class="grow"></span>
      <span class="lbl">you could assemble now — your call</span>
    </div>
    <div class="stats">
      <div><div class="n good">${s.moments} moment${s.moments === 1 ? '' : 's'}</div><div class="hint small">${s.heroes} hero · ${s.later} later · ${s.rejected} rejected</div></div>
      <div><div class="n">${fmt(s.strung_out_s)}</div><div class="hint small">if strung out${F.P && F.P.target ? ` · target ${fmt(F.P.target[0])}–${fmt(F.P.target[1])}` : ''}</div></div>
      <div><div class="n">${s.notes} note${s.notes === 1 ? '' : 's'}</div><div class="hint small">attached to their moments</div></div>
      <div><div class="n">${d.released.length}</div><div class="hint small">clip${d.released.length === 1 ? '' : 's'} released · ${fresh.length} pick${fresh.length === 1 ? '' : 's'} still undecided</div></div>
    </div>
    <div class="hint small" style="margin-bottom:4px"><span style="color:var(--accent)">◆</span> since this round started:
      <b style="color:var(--text)">${arrivals.length} new pick${arrivals.length === 1 ? '' : 's'}</b>${outrank ? ` — ${outrank} of them outrank things you saw` : ''}</div>
    <div class="actions">
      <button id="cardPlay" class="primary">▶ Play the bin <span class="key" style="margin-left:6px">↵</span></button>
      <button id="cardNext" ${nextN ? '' : 'disabled'}>Next round · ${nextN} · ~${clock(nextN * SECONDS_PER_PICK)} <span class="key" style="margin-left:6px">R</span></button>
      <button id="cardOrder">${F.order === 'rank' ? 'By clip' : 'By rank'} <span class="key" style="margin-left:6px">C</span></button>
      <button id="cardLater" ${laters ? '' : 'disabled'}>Revisit ${laters} later <span class="key" style="margin-left:6px">L</span></button>
      <span class="grow"></span>
      <button id="cardAssemble" class="priced">Assemble · $0.60 <span class="key" style="margin-left:6px">A</span></button>
    </div>
    <div class="hint small" style="margin-top:10px"><span class="key">Esc</span> back to the last pick</div>`);
  $('#cardPlay').onclick = playBin;
  $('#cardNext').onclick = () => nextRound();
  $('#cardOrder').onclick = switchOrder;
  $('#cardLater').onclick = () => nextRound({ laters: true });
  $('#cardAssemble').onclick = () => { location.href = '/'; };
}

function leaveCard() {
  F.overlay = null;
  $('#overlay').hidden = true;
  $('#overlayBox').innerHTML = '';
  delete $('#overlay').dataset.kind;
}

async function nextRound({ laters = false } = {}) {
  const picks = (F.card && F.card.picks) || F.picks;
  F.picks = picks;
  freeze(picks, { laters });
  if (!F.queue.length) return toast(laters ? 'nothing marked later' : 'nothing left to cull', 3000);
  F.round += 1;
  F.i = 0;
  leaveCard();
  show(0);
  savePosition();
}

async function switchOrder() {
  F.order = F.order === 'rank' ? 'clip' : 'rank';
  let d;
  try {
    d = await getJSON(`/api/picks?order=${F.order}`);
  } catch (e) {
    return toast(`could not re-read the bin: ${e.message}`, 5000);
  }
  F.card = { ...(F.card || {}), picks: d.picks };
  await nextRound();
}

/* Play the bin: every select in order, from the proxies, in the same picture. */
async function playBin() {
  let d;
  try {
    d = await getJSON('/api/selects');
  } catch (e) {
    return toast(`could not read the bin: ${e.message}`, 5000);
  }
  const list = (d.selects || []).slice().sort((a, b) =>
    a.clip < b.clip ? -1 : a.clip > b.clip ? 1 : a.start - b.start);
  if (!list.length) return toast('the bin is empty — nothing to play', 3000);
  const clips = (F.P || {}).clips || {};
  F.bin = {
    k: 0,
    list: list.map((s) => ({
      ...s, preview: [s.start, s.end],
      proxy: (clips[s.clip] || {}).proxy || `/media/proxy/${stem(s.clip)}.mp4`,
      duration: (clips[s.clip] || {}).duration || s.end,
    })),
  };
  F.mode = 'bin';
  leaveCard();
  clearStamp();
  paintAll();
  play(F.bin.list[0].start);
}

function binNext() {
  if (F.mode !== 'bin' || !F.bin) return;
  if (F.bin.k + 1 >= F.bin.list.length) return stopBin();
  F.bin.k += 1;
  paintAll();
  play(cur().start);
}

function stopBin() {
  pause();
  F.bin = null;
  F.mode = 'pass';
  closingCard();
}

/* ------------------------------------------------------------------- keys */

function shuttle(key) {
  const v = pic();
  if (key === 'k') return pause();
  if (key === 'l') {
    if (!F.playing || v.paused || F.shuttle < 0) { F.shuttle = 1; return resume(); }
    v.playbackRate = Math.min(8, v.playbackRate * 2);
    F.shuttle = v.playbackRate;
    return;
  }
  // j: reverse. Chromium does not play backwards, so the tick drives currentTime.
  v.pause();
  F.playing = false;
  F.gen++;
  F.shuttle = F.shuttle < 0 ? Math.max(-8, F.shuttle * 2) : -1;
}

document.addEventListener('keydown', (e) => {
  // Caps Lock is the auto-advance switch. The lamp is read on every key and a change
  // in it flips the setting — so a Caps that is already on when the page opens counts
  // from the first key, and nothing else about the keyboard has to be trusted.
  const caps = !!(e.getModifierState && e.getModifierState('CapsLock'));
  if (caps !== F.caps) { F.caps = caps; F.auto = caps; paintAuto(); }
  if (['INPUT', 'TEXTAREA'].includes(e.target.tagName) || e.target.isContentEditable) return;
  const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  if ((e.ctrlKey || e.metaKey) && k === 'z') { e.preventDefault(); undo(); return; }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (k === '?') { e.preventDefault(); return toggleOverlay('keymap', keymapHtml); }

  if (F.mode === 'card') {
    if (k === 'Enter') { e.preventDefault(); return playBin(); }
    if (k === 'r') return nextRound();
    if (k === 'c') return switchOrder();
    if (k === 'l') return nextRound({ laters: true });
    if (k === 'a') { location.href = '/'; return; }
    if (k === 'Escape' && F.queue.length) { leaveCard(); return show(F.i, { autoplay: false }); }
    return;
  }
  if (F.mode === 'bin') {
    if (k === 'Escape' || k === 'k') return stopBin();
    if (k === ' ') { e.preventDefault(); if (F.playing) pause(); else resume(); }
    return;
  }

  if (F.overlay === 'more') {
    if (k === 'o' || k === 'Enter') return openWhole();
    if (k === 'l' || k === 'f') { closeOverlay(); return toast('not on the floor yet', 2500); }
    if (k === 'Escape' || k === '.') return closeOverlay();
  }
  if (F.overlay && (k === 'Escape' || (k === 'e' && F.overlay === 'evidence'))) return closeOverlay();
  if (!cur()) return undefined;

  switch (k) {
    case 'p': return verdict('pick');
    case 'x': return e.shiftKey ? rejectRest() : verdict('reject');
    case 'u': return verdict('later');
    case '1': return verdict('pick', { hero: true });
    case 'j': case 'k': case 'l': return shuttle(k);
    case ' ':
      e.preventDefault();
      if (e.repeat) return;
      F.spaceHeld = true;
      if (!F.playing || pic().paused) resume();
      return;
    case '[': return setIn(before(sentenceStarts(cur()), keepRange().snapped[0]) ?? 0);
    case ']': return setIn(after(sentenceStarts(cur()), keepRange().snapped[0]) ?? keepRange().snapped[0]);
    case '{': return setOut(before(sentenceEnds(cur()), keepRange().snapped[1]) ?? keepRange().snapped[1]);
    case '}': return setOut(after(sentenceEnds(cur()), keepRange().snapped[1]) ?? (cur().duration || keepRange().snapped[1]));
    case 'ArrowLeft': e.preventDefault(); return stepEdge(-1, e.shiftKey);
    case 'ArrowRight': e.preventDefault(); return stepEdge(1, e.shiftKey);
    case 'v': if (!e.repeat) dictStart(); return;
    case 'n': e.preventDefault(); return editNote();
    case 'e': return toggleOverlay('evidence', evidenceHtml);
    case '.': return toggleOverlay('more', moreHtml);
    case 'Enter': e.preventDefault(); return advance();
    case 'Backspace': e.preventDefault(); return back();
    default: return undefined;
  }
});

document.addEventListener('keyup', (e) => {
  const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  if (k === ' ') {
    F.spaceHeld = false;
    const p = cur();
    if (F.mode === 'pass' && p && F.playing && !F.whole && pic().currentTime > p.preview[1]) pause();
  } else if (k === 'v') {
    dictStop();
  }
});

/* --------------------------------------------------------------------- boot */

async function boot() {
  const v = pic();
  v.addEventListener('error', () => {
    const err = v.error;
    screenMsg(`${stem(String(v.dataset.src || '').split('/').pop() || 'clip')}: `
      + `${(err && MEDIA_ERR[err.code]) || 'media error'}${err ? ` (code ${err.code})` : ''}`, 'bad');
    F.playing = false;
  });
  v.addEventListener('playing', () => screenMsg(''));
  v.addEventListener('waiting', () => { if (F.playing) screenMsg('buffering…'); });
  v.addEventListener('timeupdate', () => {        // rAF stops in a background tab
    const p = cur();
    if (F.mode === 'pass' && p && F.playing && v.currentTime >= F.watch.start) {
      F.watch.end = Math.max(F.watch.end, v.currentTime);
    }
  });
  const tape = $('#tape'), zoomEl = $('#zoom');
  tape.addEventListener('pointerdown', tapeDown);
  tape.addEventListener('pointermove', tapeMove);
  tape.addEventListener('pointerup', (e) => tapeUp(e, false));
  tape.addEventListener('pointercancel', (e) => tapeUp(e, true));
  zoomEl.addEventListener('pointerdown', zoomDown);
  zoomEl.addEventListener('pointermove', zoomMove);
  zoomEl.addEventListener('pointerup', (e) => zoomUp(e, false));
  zoomEl.addEventListener('pointercancel', (e) => zoomUp(e, true));
  $('#frame').addEventListener('click', () => {
    if (F.mode === 'card') return;
    if (F.playing && !pic().paused) pause(); else resume();
  });
  const ta = $('#noteEdit');
  ta.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); const t = ta.value; closeNote(); setNote(t); }
    else if (e.key === 'Escape') { e.preventDefault(); closeNote(); }
    e.stopPropagation();
  });
  ta.addEventListener('blur', () => { if (!ta.hidden) { const t = ta.value; closeNote(); setNote(t); } });
  window.addEventListener('resize', () => { zoom.built = ''; buildZoom(); paintKeep(true); });
  requestAnimationFrame(tick);

  let picks;
  try {
    [F.P, picks] = await Promise.all([getJSON('/api/project'), getJSON('/api/picks')]);
  } catch (e) {
    $('#hudPos').textContent = `could not load the bin — ${e.message}`;
    return;
  }
  const pos = picks.position || {};
  F.order = pos.order === 'clip' ? 'clip' : 'rank';
  if (F.order === 'clip') picks = await getJSON('/api/picks?order=clip');
  F.picks = picks.picks;
  F.roundSize = picks.round_size || F.roundSize;
  F.summary = picks.summary;
  F.dictation = picks.dictation;
  F.round = Math.max(1, pos.round || 1);
  freeze(F.picks);
  document.title = `The pass — round ${F.round}`;
  if (!F.queue.length) { paintHud(); return closingCard(); }
  const at = Math.min(pos.index || 0, F.queue.length - 1);
  show(at);
  if (F.dictation === false) $('#dictHint').innerHTML =
    'dictation not built yet — <span class="key">N</span> to type a note';
}

/* What the tests reach for; nothing else should. */
window.floor = {
  state: F, current: cur, keepRange, show, advance, undo, playBin, seek,
  setAuto: (on) => { F.auto = !!on; paintAuto(); },
  dictSend, snapStart, snapEnd, words, utterances,
};

boot();
