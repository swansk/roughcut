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
let sel = -1;                 // the anchor's index; -1 until a shot is chosen (nothing at start)
let nRenders = 0;
let renderList = [];          // the versions list, kept so it can be repainted on edit
let bin = null;               // GET /api/selects — what the pass kept, and its summary

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
 * source of truth; it is now actually kept that way.
 *
 * The stack itself lives in the timeline module now (INTAKE M9): one stack, with redo,
 * serving the cards, the keys and the timeline's own edits. Callers here mutate `segs`
 * right after this call, synchronously, so the entry is closed on the microtask that
 * follows — one entry per gesture, and touch() rides on the commit as before. */
function pushUndo(label = 'edit') {
  tl.begin(label);
  queueMicrotask(() => tl.commit());
}

let saveTimer = null;

/* The header says nothing about a good save (INTAKE M16 I16.1): "save failed" when it
 * failed, and "unsaved…" only when the save is late — two seconds after the edit, a hung
 * request or a slow disk. The autosave's own 700 ms wait is nothing wrong, and saying
 * "unsaved…" through it flashed a word in the header after every trim. `data-state`
 * still moves at once (hidden until late), so whoever waits on "saved" waits for the
 * save; its text stays for them too. */
let lateTimer = 0;
const LATE_MS = 2000;
function saveSays(state, text) {
  const el = $('#saveState');
  el.dataset.state = state;
  el.textContent = text;
  clearTimeout(lateTimer);
  delete el.dataset.late;
  if (state === 'unsaved') lateTimer = setTimeout(() => { el.dataset.late = '1'; }, LATE_MS);
}

function touch() {
  saveSays('unsaved', 'unsaved…');
  clearTimeout(saveTimer);
  saveTimer = setTimeout(save, 700);
}

function undo() { tl.undo(); }

/* Film time is the timeline's arithmetic (INTAKE M13): a shot's length in the film is
 * `(out − in) / speed`, and `tl.dur` is the one place that lives. */
function total() { return segs.reduce((a, s) => a + tl.dur(s), 0); }
const speedOf = (seg) => tl.speedOf(seg);

/* ------------------------------------------------------------ the top bar
 *
 * One strip, in the header's row, that every long operation drives. Karl, on the render:
 * "got like no response - and just see rendering...", and then: "consider a progress
 * tracking bar up top for anything which may take time to complete - re-use across
 * app."
 *
 * It reads /api/jobs — the server's own registry — and nothing else, which buys three
 * things that per-panel bars could not: two operations at once (a render and an Ask
 * overlap routinely), a reload that re-attaches to whatever is still running instead
 * of losing it, and one place to fix when the next long operation is added.
 */
const strip = { rows: new Map(), open: new Set(), owned: new Set(), seen: new Map() };

/* "about 2 min left". Rounded hard on purpose: a second-by-second countdown on a
 * four-minute estimate is precision the estimate does not have. */
function etaText(s) {
  if (s == null) return '';
  if (s <= 0) return '';
  if (s < 45) return `about ${Math.max(5, Math.round(s / 5) * 5)}s left`;
  if (s < 90) return 'about a minute left';
  return `about ${Math.round(s / 60)} min left`;
}

function jobRow(j) {
  let row = strip.rows.get(j.id);
  if (!row) {
    row = document.createElement('div');
    row.className = 'job';
    row.dataset.job = j.id;
    row.dataset.kind = j.kind;
    row.innerHTML = `<span class="jlabel"><span class="caret">▸</span><span
      class="jname"></span></span><span class="jbar"><i></i></span>
      <span class="jnums"></span><span class="jdetail"></span>`;
    row.querySelector('.jlabel').onclick = () => {
      if (strip.open.has(j.id)) strip.open.delete(j.id); else strip.open.add(j.id);
      paintJob(row, strip.seen.get(j.id) || j);
    };
    strip.rows.set(j.id, row);
    $('#progress').appendChild(row);
  }
  return row;
}

function paintJob(row, j) {
  row.className = `job ${j.state}`;
  // Stop means stop (verdict 4, punch 8): a job that can be stopped carries an × that
  // calls its own cancel: the index (proxies, close looks, previews) and a render. A
  // stopped job says nothing more: its row goes, and the next poll does not bring it back.
  const CANCEL = { index: () => '/api/index/cancel', render: (x) => `/api/render/${x.id}/cancel` };
  if (j.state === 'cancelled') { row.style.display = 'none'; return; }
  row.style.display = '';
  let x = row.querySelector('.jx');
  if (CANCEL[j.kind] && j.state === 'running') {
    if (!x) {
      x = document.createElement('span');
      x.className = 'jx';
      x.setAttribute('role', 'button');
      x.dataset.testid = 'job-cancel';
      x.textContent = '×';
      x.title = j.kind === 'index' ? 'Stop indexing (proxies, close looks, previews): what finished is kept'
        : 'Stop this render: nothing is written';
      x.style.cssText = 'cursor:pointer;color:var(--dim);padding:0 0 0 2px;font-size:13px;line-height:1';
      x.onclick = async (e) => {
        e.stopPropagation();
        if (x.dataset.busy) return;
        x.dataset.busy = '1';
        x.textContent = '…';
        x.title = 'stopping…';
        try {
          await fetch(CANCEL[j.kind](j), { method: 'POST' });
        } catch (_) { /* the next poll says how it went */ }
        const seen = strip.seen.get(j.id);
        if (seen) strip.seen.set(j.id, { ...seen, state: 'cancelled' });
        row.style.display = 'none';
      };
      row.appendChild(x);
    }
  } else if (x) {
    x.remove();
  }
  row.querySelector('.jname').textContent = j.label;
  row.querySelector('.caret').textContent = strip.open.has(j.id) ? '▾' : '▸';
  row.querySelector('.jbar i').style.width = `${j.pct}%`;
  // the row is one compact line in the header: how far and how long ("~10s left"); the
  // time it has taken so far is the hover's
  const eta = j.state === 'done' || j.state === 'failed' ? ''
    : etaText(j.eta_s).replace(/^about /, '~').replace(/^~a minute/, '~1 min');
  row.querySelector('.jnums').textContent =
    [`${Math.round(j.pct)}%`, eta].filter(Boolean).join(' · ');
  // The milestone is what the operation is *on*; the detail is what it is doing inside
  // it. Both, because "choosing the shots" without "12 of ~18" is a spinner with words.
  // In the header's row they are the line's hover and the first line under ▸; a failed
  // job says them on the line itself.
  const now = [j.milestone, j.detail].filter(Boolean).join(' — ');
  row.querySelector('.jdetail').textContent = now;
  row.title = [j.label, now, `${clock(j.elapsed_s)} so far`].filter(Boolean).join(' — ');

  let more = row.querySelector('.jmore');
  if (!strip.open.has(j.id)) {
    if (more) more.remove();
    return;
  }
  if (!more) {
    more = document.createElement('div');
    more.className = 'jmore';
    row.appendChild(more);
  }
  const steps = (j.milestones || []).map((m) => {
    const cls = m.done_at ? 'was' : (m === (j.milestones || []).find((x) => !x.done_at)
      ? 'at' : '');
    return `<li class="${cls}">${escapeHtml(m.label)}${m.done_at ? ' ✓' : ''}</li>`;
  }).join('');
  const src = j.eta_source === 'measured' ? 'recalibrated from this run'
    : j.eta_source === 'model' ? 'estimated by the model before it started'
      : j.eta_source === 'fallback' ? 'estimate call failed — measured fallback' : '';
  const tail = (j.log || '').split('\n').filter(Boolean).slice(-6).join('\n');
  more.innerHTML = (now ? `<div class="jnow">${escapeHtml(now)}</div>` : '')
    + (steps ? `<ol>${steps}</ol>` : '')
    + (src ? `<div class="hint" style="margin-top:6px">${src}</div>` : '')
    + (tail ? `<pre>${escapeHtml(tail)}</pre>` : '');
}

/* What the page does about a job it did not start itself — the reload case. Its own
 * pollers already handle the ones it launched, and firing both would toast twice. */
async function adopt(j) {
  if (strip.owned.has(j.id)) return;
  if (j.kind === 'ask' && j.state === 'done') {
    // The plan is not in the heartbeat — it is 15k tokens of JSON — so fetch it.
    const full = await (await fetch(`/api/job/${j.id}`)).json();
    if (!full.plan) return;
    showProposal(full.plan);
    toast('the proposal you asked for is ready');
  } else if (j.kind === 'render' && j.state === 'done') {
    await refreshVersions();
  } else if (j.kind === 'fx' && j.state === 'done') {
    // An effect designed, revised or verified (INTAKE M12): the FX tool repaints.
    if (window.fx && typeof fx.refresh === 'function') fx.refresh();
  } else if (j.kind === 'find' && j.state === 'done') {
    // A model search started before a reload still lands its matches in the panel.
    const full = await (await fetch(`/api/job/${j.id}`)).json();
    if (!full.found) return;
    renderFindResults(full.found.matches, full.found.notes);
    toast('the model search finished — matches are in Find a moment');
  } else if ((j.kind === 'visual' || j.kind === 'analyse' || j.kind === 'index'
              || j.kind === 'deep') && j.state === 'done') {
    // Nothing on this board starts these any more (the open screen runs the index;
    // the two passes are API-only), but whatever finished wrote transcripts, sidecars
    // or picks, so the whole project reloads.
    P = await (await fetch('/api/project')).json();
    await refreshStatus();
    render();
  }
}

let jobTicks = 0;             // pollJobs ticks so far — the first one is the boot paint

/* A job that finished and found nothing wrong (INTAKE M16 decision 1: status only when
 * something is wrong). While it runs it is progress, in the header's row; once it is done
 * its result is on the screen — the proposal, the film, the card's "✓ checked", the
 * toast — and a "· done" line for 12 s more said it a second time. A failed job keeps
 * its line, saying why; so does a check that finished and failed ("failed: …"). */
function quietJob(j) {
  return j.state === 'done' && !/^failed/.test(j.detail || '');
}

async function pollJobs() {
  let jobs;
  try {
    jobs = (await (await fetch('/api/jobs')).json()).jobs;
  } catch (e) { return; }                   // a blip is not a reason to blank the bar
  const live = new Set(jobs.map((j) => j.id));
  strip.rows.forEach((row, id) => {
    if (live.has(id)) return;
    row.remove();
    strip.rows.delete(id);
    strip.open.delete(id);
    strip.seen.delete(id);
  });
  for (const j of jobs) {
    const before = strip.seen.get(j.id);
    strip.seen.set(j.id, j);
    if (quietJob(j)) {
      // said nothing worth a row: none, and the strip is not a second header row for it
      const row = strip.rows.get(j.id);
      if (row) { row.remove(); strip.rows.delete(j.id); }
    } else {
      paintJob(jobRow(j), j);
    }
    if (before && before.state !== j.state && (j.state === 'done' || j.state === 'failed')) {
      await adopt(j);
    } else if (!before && jobTicks && j.kind === 'index' && j.state === 'done') {
      // One that ran to done between two ticks arrives already finished, so a first
      // sighting after boot is a transition too.
      await adopt(j);
    }
  }
  jobTicks += 1;
  // Gone completely when there is nothing to say. A strip that lingers empty is one
  // more thing on a screen that already has plenty.
  $('#progress').hidden = !jobs.some((j) => !quietJob(j));
  // Driven from the server's registry rather than from this tab's own click, so it is
  // right after a reload and right when the render was started somewhere else. The
  // pending flag covers the second between the click and the job existing to be seen.
  setRenderBusy(renderPending
    || jobs.some((j) => j.kind === 'render' && j.state === 'running'));
}

/* One render at a time.
 *
 * Karl asked whether hitting Render repeatedly can break the state of a render. It
 * cannot — each job has its own id, parts directory and output file — but two encodes
 * on one box make both crawl, and his delivery render was ~17 minutes with the machine
 * otherwise idle. The server refuses the second with a 409; with the strip above now
 * showing what the render is doing, a control that says it is already rendering is the
 * honest other half. */
let renderPending = false;
function setRenderBusy(busy, profile) {
  const q = $('#render'), f = $('#renderFinal');
  if (!q || q.dataset.busy === String(busy)) return;
  [q, f].forEach((b) => {
    b.dataset.busy = String(busy);
    b.disabled = busy;
    b.title = busy ? 'a film is being made — see the bar above' : '';
  });
  if (busy) (profile === 'delivery' ? f : q).textContent = 'Making the film…';
  else if (P) paintVersions();
}

/* A generated clip (INTAKE M13: `gen_<kind>_<key>.mp4` — black, a colour, a still the
 * server made for an edit) is listed among the clips with a proxy and a poster but no
 * sidecar: `transcript: []`, no candidates, no visual. Everything that reads a clip
 * guards for that rather than assuming footage. */
const isGenClip = (clip) => String(clip).startsWith('gen_');

function linesFor(seg) {
  const clip = P.clips[seg.clip];
  if (!clip) return [];
  return (clip.transcript || []).filter((u) => u.end > seg.in && u.start < seg.out);
}

/* A cut that starts or ends inside somebody's sentence is the defect Karl flagged.
 * Rather than only fixing it on demand via Snap, flag it continuously so it's
 * visible while trimming. */
function boundaryWarning(seg) {
  const clip = P.clips[seg.clip];
  if (!clip) return '';
  const cutsInto = (t) => (clip.transcript || []).some((u) => u.start + 0.05 < t && t < u.end - 0.05);
  const bad = [];
  if (cutsInto(seg.in)) bad.push('opens mid-sentence');
  if (cutsInto(seg.out)) bad.push('cuts a line off');
  return bad.join(' · ');
}

/* ------------------------------------------------------------ the inspector
 *
 * One panel under the timeline for the selection (INTAKE M9, I9.5), holding what a
 * shot card held — for the anchor shot only: the still at the in-point, the header,
 * the transcript lines and what was seen inside the cut, the editable why, the trim
 * buttons, the scoped ask and remove. The list of one card per shot is gone: the
 * timeline is the editing surface now, and a card per shot was a second one that ran
 * a long way below it (shot 12's card on the Killington cut sat 3,163 px under the
 * monitor). `sel` stays an index here, as the foundation expects; the inspector reads
 * the timeline's anchor and re-renders on its `select` and `change` events and from
 * paint() — cheaply, one element updated in place, so a scrub or a shot advance never
 * rebuilds it and never steals the focus from a `why` somebody is typing in.
 *
 * The still is a few-kilobyte JPEG, never a stream: the cards used to be <video>
 * elements, and sixteen of them plus two render previews was eighteen streams against
 * Chrome's six connections per host, so the monitor's own request queued behind them
 * and the sound arrived seconds before the first frame. `posterAt` remembers which
 * frame the still shows, keyed by the segment object so it survives every re-render;
 * without it, holding the in-trim button would fetch a new frame every 0.25 s. */
const posterAt = new WeakMap();
let posterTimer = 0;

function posterSrc(seg) {
  if (!posterAt.has(seg)) posterAt.set(seg, seg.in);
  const base = (P.clips[seg.clip] || {}).poster;
  if (!base) return '';
  return `${base}${base.includes('?') ? '&' : '?'}t=${Math.max(0, posterAt.get(seg)).toFixed(2)}`;
}

/* Catch the still up to the in-point, once the trimming stops — the timeline's rule and
 * the same 450 ms. A frame that lags a nudge by half a second is fine; twenty requests
 * for twenty nudges is not. Updates the <img> in place, never a re-render. */
function refreshPosters() {
  clearTimeout(posterTimer);
  posterTimer = setTimeout(() => {
    const seg = inspected();
    const img = document.querySelector('#inspector img.poster');
    if (!seg || !img) return;
    posterAt.set(seg, seg.in);
    const src = posterSrc(seg);
    if (src && img.getAttribute('src') !== src) img.setAttribute('src', src);
  }, 450);
}

/* Per-shot ask state. Keyed by the segment object (like posterAt) so it survives the
 * re-render after every edit; the draft survives too, because a render can land while
 * someone is mid-sentence about a shot. */
const shotAskOpen = new WeakSet();
const shotAskDraft = new WeakMap();

/* The shot the inspector is about: the timeline's anchor, or null. */
function inspected() {
  return tl.state.anchor == null ? null : tl.byId(tl.state.anchor);
}

/* What the inspector is currently built for: a segment object, or one of the words
 * 'multi' / 'none' / 'empty'. The DOM is rebuilt only when this changes; everything
 * else is filled in place. A segment keeps its object across a trim, a re-key after a
 * save and a re-render, so typing in its why is never interrupted. */
let inspecting = null;

/* A why being typed when the inspector moves on (playback advanced, a proposal landed)
 * must reach the segment before the field is thrown away — Chrome does not blur an
 * element that leaves the DOM. */
function commitWhy(box) {
  const why = box.querySelector('.why');
  if (!why || document.activeElement !== why) return;
  if (!inspecting || typeof inspecting !== 'object') return;
  const text = why.textContent.trim();
  if (inspecting.why === text) return;
  inspecting.why = text;
  touch();
  tl.render();                  // the block's tooltip and fallback line carry the why
}

/* The shot strip (INTAKE M16 I16.4): one panel under the timeline, in view with the
 * picture, for the selected shot — the still, "Shot N · 12.9 s", ▶ play, Ask about this
 * shot and Look deeper (each priced), Remove, the why (editable), the first line spoken,
 * any warning with its own fix, the speed, the four colour nudges and reset; per-shot
 * look and matching behind "colour & look ▸", the evidence behind "why? ▸", and how the
 * machine saw the clip in one line. Both disclosures start closed on every shot. What
 * the old inspector also showed — the clip time header, "starts at … of the film",
 * timestamps on lines, five speed controls, four in/out buttons (trims are the edges
 * and [ ] { }), the white-point numbers, the film's look in every shot and the six-lane
 * coverage strip with its legend — is gone or folded. */
const SPEEDS = [0.25, 0.5, 0.75, 1, 1.5, 2];
let assistOpen = false;          // the strip's "Assistant" section, open or closed
/* Undo's key as this platform spells it: ⌘Z on macOS, Ctrl+Z everywhere else. */
const ON_MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');
const undoKey = () => (ON_MAC ? '⌘Z' : 'Ctrl+Z');
const speedText = (v) => ({ 0.25: '¼×', 0.5: '½×', 0.75: '¾×' }[v] || `${v}×`);

function buildShot(seg) {
  const gen = isGenClip(seg.clip);
  const el = document.createElement('div');
  el.className = 'shot' + (gen ? ' gen' : '');
  el.innerHTML = `
    <img class="poster" draggable="false" decoding="async" title="play the cut from here">
    <div class="sbody">
      <div class="srow top">
        <span class="sname"></span>
      </div>
      <div class="warns"></div>
      <div class="srow ctl ecx">
        <select class="speed" title="the shot's speed"></select>
        <span class="nudge"${gen ? ' hidden' : ''}>
          <button data-act="cwarm" title="warmer — ${undoKey()} undoes it">warmer</button>
          <button data-act="ccool" title="cooler — ${undoKey()} undoes it">cooler</button>
          <button data-act="cbright" title="brighter — ${undoKey()} undoes it">brighter</button>
          <button data-act="cdark" title="darker — ${undoKey()} undoes it">darker</button>
          <button data-act="creset" class="ghost" title="back to the auto balance">reset</button>
        </span>
        <button class="disc" data-disc="look"${gen ? ' hidden' : ''}>colour &amp; look ▸</button>
      <div class="colour" data-pane="look" hidden>
        <label title="the auto balance for this shot (unchecked: as shot)"><input type="checkbox" class="cauto"> auto-balance</label>
        <select class="clook" title="this shot's look — the film's unless set here"></select>
        <input type="range" class="cstrength" min="0" max="1" step="0.05" title="look strength for this shot">
        <span class="cstrengthVal"></span>
        <button data-act="cmatchprev" title="match this shot's colour to the shot before it (again: off)">match ← previous</button>
        <button data-act="cmatchref" title="match this shot's colour to the film's reference shot (again: off)">match ← reference</button>
        <button data-act="csetref" title="make this the shot others match to (again: none)">set as reference</button>
      </div>
      </div>
      <details class="assist" data-testid="ec-assistant">
      <summary>Assistant</summary>
      <div class="srow acts">
        <button data-act="play" title="play this shot only, then stop · enter">▶ play</button>
        <button data-act="ask" title="ask for a change to this one shot — opens a box; nothing is spent until its Ask">Ask about this shot${priceTag('shot')}</button>
        <span class="lookhost"></span>
        <button data-act="del" title="take this shot out of the cut — ${undoKey()} brings it back">Remove</button>
      </div>
      <div class="why" contenteditable title="why this shot — yours to edit, saved with the cut"></div>
      <div class="said"></div>
      <div class="srow"><button class="disc" data-disc="why" title="the evidence: what is said and seen in the shot, and its colour">why? ▸</button></div>
      <div class="whybox" data-pane="why" hidden>
        <div class="from"><span class="clip"></span> · <span class="times"></span></div>
        <div class="lines"></div>
        <div class="lines seen" hidden></div>
        <div class="witness"></div>
        <div class="polish" hidden></div>
        <div class="prov" hidden></div>
      </div>
      <div class="deepline"${gen ? ' hidden' : ''}></div>
      <div class="shotAsk" hidden>
        <textarea class="shotNote"
          placeholder="what should change in this shot — start later · hold through the reaction · just keep the punchline"></textarea>
        <div style="display:flex;gap:8px;align-items:center;margin-top:6px">
          <button data-act="shotgo"${unpricedAttr('shot')}>Ask${priceTag('shot')}</button>
          <span class="hint shotState"></span>
        </div>
      </div>
      </details>
    </div>`;
  // The AI's and the shot's own chrome sit in one collapsed "Assistant" section at the
  // foot of Effect Controls (verdict 4, punch 10): the panel opens on Premiere's
  // properties, not on Ask / Look deeper / Remove / a quote. Its open state carries
  // from shot to shot, as Premiere's twirl-downs do.
  const assist = el.querySelector('details.assist');
  assist.open = assistOpen;
  assist.addEventListener('toggle', () => { assistOpen = assist.open; });
  el.querySelector('.why').addEventListener('blur', (ev) => {
    const text = ev.target.textContent.trim();
    if (seg.why === text) return;
    seg.why = text;
    touch();
    tl.render();                // the block's tooltip and fallback line carry the why
  });
  el.querySelector('.shotNote').addEventListener('input', (ev) => {
    shotAskDraft.set(seg, ev.target.value);
  });
  // Effect Controls (/modules/effects.js): the speed, the colour and the new properties
  // in Premiere's sections; without the module the row above stays as it is.
  if (window.effects && typeof effects.build === 'function') effects.build(el.querySelector('.ecx'), seg, { gen });
  // One control for the speed: one undo entry labelled `speed` per change.
  el.querySelector('.speed').addEventListener('change', (ev) => {
    const v = parseFloat(ev.target.value);
    const cur = inspected() || seg;
    if (Number.isFinite(v)) tl.setSpeed(cur.id, v);
    fillSpeed(el, tl.byId(cur.id) || cur);
  });
  bindColour(el, seg);
  return el;
}

/* The speed select: the usual rates, plus the shot's own when it is something else. */
function fillSpeed(box, seg) {
  const spd = speedOf(seg);
  const sel = box.querySelector('select.speed');
  if (!sel) return;
  const rates = SPEEDS.includes(spd) ? SPEEDS : [...SPEEDS, spd].sort((a, b) => a - b);
  const key = rates.join(',');
  if (sel.dataset.key !== key) {
    sel.dataset.key = key;
    sel.innerHTML = rates.map((v) => `<option value="${v}">${speedText(v)}</option>`).join('');
  }
  setIfIdle(sel, String(spd));
}

function buildMulti() {
  const el = document.createElement('div');
  el.className = 'multi';
  el.innerHTML = `<span class="count"></span>
    <button data-act="delall" class="ghost" title="remove every selected shot (one undo entry)">remove</button>`;
  return el;
}

/* Nothing selected: the strip is the film — its length, its look and strength, the auto
 * balance, and the warnings to step through. The film's look lived in every shot. */
function buildNone() {
  const el = document.createElement('div');
  el.className = 'none film';
  el.innerHTML = `<span class="totals"></span>
    <label class="flook">look <select class="cfilmlook" title="the look over the whole film"></select></label>
    <span class="fstrength">
      <button data-act="fstr" data-v="0.25">subtle</button><button data-act="fstr" data-v="0.5">medium</button><button data-act="fstr" data-v="0.85">strong</button>
    </span>
    <button data-act="fauto" class="fauto" title="every shot balanced from its white reference, or the camera's picture"></button>
    <button data-act="nextwarn" class="fwarn" hidden title="select the next shot with a warning"></button>`;
  el.querySelector('.cfilmlook').addEventListener('change', (ev) => {
    const v = ev.target.value || null;
    colourEdit('film look', () => { colour.look = v; });
  });
  return el;
}

/* The shots the strip warns about: a cut inside a sentence, or a stretch the visual pass
 * said not to use. */
function shotWarnings(seg) {
  const out = [];
  const clip = P.clips[seg.clip];
  if (clip && !isGenClip(seg.clip)) {
    const cutsInto = (t) => (clip.transcript || []).some((u) => u.start + 0.05 < t && t < u.end - 0.05);
    if (cutsInto(seg.in)) out.push({ kind: 'in', text: 'starts mid-sentence', fix: 'Fix' });
    if (cutsInto(seg.out)) out.push({ kind: 'out', text: 'cuts a line off', fix: 'Fix' });
  }
  unusableFor(seg).forEach((u, k) => {
    const spd = speedOf(seg);
    const a = Math.max(0, (u.start - seg.in) / spd), b = Math.min(tl.dur(seg), (u.end - seg.in) / spd);
    const whole = u.start <= seg.in + 0.05 && u.end >= seg.out - 0.05;
    const at = (t) => (b - a >= 1 ? clock(t) : fmt(t));      // a short stretch keeps its tenths
    out.push({ kind: 'bad', k, why: u.why, text: `${at(a)}–${at(b)} ${badWords(u.why)}`,
               fix: whole ? '' : 'Trim it out' });
  });
  return out;
}

/* What an unusable stretch is, in plain words; the pass's own sentence is the tooltip. */
function badWords(why) {
  const w = String(why || '').toLowerCase();
  if (/black|dark|dropout/.test(w)) return 'goes dark';
  if (/blur|focus/.test(w)) return 'is blurred';
  if (/obstruct|cover|lens|finger|glove/.test(w)) return 'the lens is covered';
  if (/shak|jerk/.test(w)) return 'shakes';
  return 'is unusable';
}

/* Fill the shot strip from the segment, touching only what changed. */
function fillShot(box, seg) {
  const q = (s) => box.querySelector(s);
  const i = tl.indexOf(seg.id);
  const gen = isGenClip(seg.clip);
  const kind = gen ? (/^gen_([a-z]+)_/i.exec(String(seg.clip)) || [])[1] || 'slide' : '';
  q('.sname').textContent = `Shot ${i + 1} · ${kind ? `${kind} · ` : ''}${tl.dur(seg).toFixed(1)} s`;
  fillSpeed(box, seg);

  const why = q('.why');
  if (document.activeElement !== why && why.textContent !== (seg.why || '')) {
    why.textContent = seg.why || '';
  }
  const first = gen ? null : linesFor(seg)[0];
  const said = first ? `“${first.text}”` : '';
  if (q('.said').textContent !== said) q('.said').textContent = said;
  q('.said').hidden = !said;

  // the warnings, each with its own fix — one undo entry each, never automatic
  const warns = shotWarnings(seg);
  const wkey = JSON.stringify(warns.map((w) => [w.kind, w.text, w.fix]));
  const wEl = q('.warns');
  if (wEl.dataset.key !== wkey) {
    wEl.dataset.key = wkey;
    wEl.innerHTML = warns.map((w) => `<span class="w" title="${escapeHtml(w.why || '')}">⚠ ${escapeHtml(w.text)}${w.fix
      ? ` · <button data-act="${w.kind === 'bad' ? 'trimbad' : `fix${w.kind}`}" data-k="${w.k ?? ''}">${w.fix}</button>` : ''}</span>`).join('');
  }
  wEl.hidden = !warns.length;

  // why? — the evidence: where the shot came from, the timed transcript, what was seen
  // with times, the colour in words, and the proposal's polish when it carries one
  q('.clip').textContent = stem(seg.clip);
  q('.times').textContent = `${fmt(seg.in)}–${fmt(seg.out)}`;
  const genClip = gen ? (P.clips[seg.clip] || {}) : null;
  const lines = genClip
    ? `<div>${escapeHtml(typeof genClip.summary === 'string' ? genClip.summary
        : (genClip.summary && genClip.summary.generated) || 'generated clip')}</div>`
    : linesFor(seg).map(
      (u) => `<div><b>${clock(Math.max(0, (u.start - seg.in) / speedOf(seg)))}</b> ${escapeHtml(u.text)}</div>`).join('')
      || '<div>no words</div>';
  if (q('.lines:not(.seen)').innerHTML !== lines) q('.lines:not(.seen)').innerHTML = lines;
  const seen = seenFor(seg).map(
    (m) => `<div><b>${clock(Math.max(0, (m.start - seg.in) / speedOf(seg)))}</b> seen: ${escapeHtml(m.what)}</div>`).join('');
  const seenEl = q('.lines.seen');
  if (seenEl.innerHTML !== seen) seenEl.innerHTML = seen;
  seenEl.hidden = !seen;
  const pf = Array.isArray(seg.polished_from) && seg.polished_from.length === 2
    ? seg.polished_from : null;
  const polish = pf
    ? `polished from ${fmt(Number(pf[0]))}–${fmt(Number(pf[1]))}`
      + (seg.polish_why ? ` · ${seg.polish_why}` : '')
    : '';
  q('.polish').textContent = polish;
  q('.polish').hidden = !polish;
  const prov = seg.act ? `act · ${seg.act}` : '';
  q('.prov').textContent = prov;
  q('.prov').hidden = !prov;

  // The still: at the in-point when the shot arrives, and catching up to a trimmed
  // in-point once the trimming settles — never a frame per nudge.
  const img = q('img.poster');
  img.alt = `${stem(seg.clip)} at ${seg.in.toFixed(2)}s`;
  if (!posterAt.has(seg)) {
    const src = posterSrc(seg);
    // No src at all rather than an empty one for a clip with no sidecar: src="" makes
    // the browser fetch the page's own URL, which is a request for the whole board.
    if (src) img.setAttribute('src', src); else img.removeAttribute('src');
  } else if (posterAt.get(seg) !== seg.in) {
    refreshPosters();
  } else if (!img.getAttribute('src')) {
    const src = posterSrc(seg);
    if (src) img.setAttribute('src', src);
  }

  const askBox = q('.shotAsk');
  askBox.hidden = !shotAskOpen.has(seg);
  if (!askBox.hidden && !q('details.assist').open) q('details.assist').open = true;
  const note = q('.shotNote');
  if (document.activeElement !== note) note.value = shotAskDraft.get(seg) || '';

  if (!gen) fillColour(box, seg);
  else q('.witness').textContent = '';
  machineLine(box, seg);
  // the sections' values, and the Program monitor's live preview of them
  if (window.effects && typeof effects.fill === 'function') effects.fill(box, seg);
}

/* Look deeper (priced, on the strip's top row) and how the machine saw the clip (one
 * line, closed). Re-priced when the shot's range changes, once the trimming settles — a
 * drag must not ask for a price per frame. A trim is the same item (decision 9: the
 * line closes on the *next* one): the line keeps open or closed with the new range
 * marked, and a Look deeper result already drawn stays; a new shot builds both afresh. */
let machineTimer = 0;
function machineLine(box, seg) {
  if (isGenClip(seg.clip) || !window.deep) return;
  const key = `${seg.clip}|${seg.in}|${seg.out}`;
  const host = box.querySelector('.lookhost');
  if (!host || host.dataset.key === key) return;
  const first = !host.dataset.key;
  host.dataset.key = key;
  clearTimeout(machineTimer);
  const go = () => {
    if (inspected() !== seg || host.dataset.key !== key) return;
    host.replaceChildren();
    const line = box.querySelector('.deepline');
    const same = !first && line && line._line && line.dataset.clip === seg.clip;
    if (!same) {
      const res = host.parentNode && host.parentNode.querySelector(':scope > .dv-res');
      if (res) res.remove();
    }
    // deep.rowButton pads the span by 2 s each side for a moment; a shot is its own span
    if (typeof deep.rowButton === 'function') deep.rowButton(host, seg.clip, seg.in + 2, seg.out - 2);
    if (same) { line._line.mark(seg.in, seg.out); return; }
    // the top row's Look deeper is the shot's; the opened strip prices only a drag
    if (typeof deep.line === 'function') {
      deep.line(line, seg.clip, { range: [seg.in, seg.out], markLabel: 'this shot', markLook: false });
    }
    else if (line) {
      // until /deep.js carries its one line: a closed line that opens the full strip
      line.innerHTML = '<button type="button" class="dl-open">how the machine saw this clip ▸</button><div class="deepbox"></div>';
      line.querySelector('.dl-open').onclick = () => {
        const b = line.querySelector('.deepbox');
        if (b.childElementCount) { b.innerHTML = ''; b.hidden = true; return; }
        if (typeof deep.inspector === 'function') deep.inspector(b, seg);
      };
    }
  };
  if (first) go(); else machineTimer = setTimeout(go, 450);
}

function renderInspector() {
  const box = $('#inspector');
  if (!box) return;
  if (!segs.length) {
    if (inspecting !== 'empty') {
      inspecting = 'empty';
      const el = document.createElement('div');
      el.className = 'none nosq';
      el.textContent = 'Nothing selected — the sequence is empty.';
      box.replaceChildren(el);
    }
    paintEcHead(null, 0);
    return;
  }
  const selIds = [...tl.state.sel].filter((id) => tl.indexOf(id) >= 0);
  const seg = selIds.length > 1 ? null : inspected();
  const want = selIds.length > 1 ? 'multi' : seg || 'none';
  if (inspecting !== want) {
    commitWhy(box);
    inspecting = want;
    box.replaceChildren(want === 'multi' ? buildMulti() : want === 'none' ? buildNone()
      : buildShot(seg));
  }
  paintEcHead(want === 'multi' ? null : seg, selIds.length);
  if (want === 'multi') {
    const d = selIds.reduce((a, id) => { const s = tl.byId(id); return a + (s ? tl.dur(s) : 0); }, 0);
    box.querySelector('.count').textContent = `${selIds.length} shots selected · ${d.toFixed(1)} s`;
  } else if (want === 'none') {
    fillFilm(box);
  } else {
    fillShot(box, seg);
  }
}

/* The Effect Controls panel's head line, Premiere's "Source · clip" / "Sequence": the
 * selected shot's clip and its range, the selection's size, or the sequence. */
function paintEcHead(seg, n) {
  const lab = document.querySelector('#ecHead span'), what = $('#ecWhat');
  if (!lab || !what) return;
  if (seg) {
    // Premiere's head: the clip, and its source In / Out in timecode
    lab.textContent = `Shot ${tl.indexOf(seg.id) + 1}`;
    what.textContent = `${stem(seg.clip)} · ${tcOf(seg.in)} – ${tcOf(seg.out)}`;
    what.title = `source In ${tcOf(seg.in)} · Out ${tcOf(seg.out)} · ${tcOf(tl.dur(seg))} in the sequence`;
  } else if (n > 1) {
    lab.textContent = 'Selection';
    what.textContent = `${n} shots`;
    what.title = '';
  } else {
    lab.textContent = 'Sequence';
    what.textContent = (P && (P.title || P.cut)) || '';
    what.title = '';
  }
}

/* The film row: what nothing-selected shows. */
function fillFilm(box) {
  const q = (s) => box.querySelector(s);
  q('.totals').textContent = `${segs.length} shot${segs.length === 1 ? '' : 's'} · ${clock(total())}`;
  lookOptions(q('.cfilmlook'), 'none', false);
  setIfIdle(q('.cfilmlook'), colour.look || '');
  const fs = colour.strength ?? 0.5;
  const near = [0.25, 0.5, 0.85].reduce((a, b) => (Math.abs(b - fs) < Math.abs(a - fs) ? b : a));
  box.querySelectorAll('[data-act=fstr]').forEach((b) => b.classList.toggle('on', parseFloat(b.dataset.v) === near));
  q('.fstrength').hidden = !colour.look;
  q('.fauto').textContent = `auto-balance ${colour.mode === 'off' ? 'off' : 'on'}`;
  q('.fauto').classList.toggle('on', colour.mode !== 'off');
  const n = segs.filter((s) => shotWarnings(s).length).length;
  q('.fwarn').hidden = !n;
  q('.fwarn').textContent = `${n} warning${n === 1 ? '' : 's'} ▸`;
}

/* The next shot with a warning after the playhead (wrapping): select it, park there. */
function nextWarned() {
  const start = tl.shotAt(tl.state.playhead);
  const from = start ? start.index : -1;
  for (let k = 1; k <= segs.length; k++) {
    const i = (from + k) % segs.length;
    if (shotWarnings(segs[i]).length) {
      const id = tl.idAt(i);
      tl.select([id], { source: 'click' });
      tl.seek(tl.filmStart(id));
      scrollSel();
      return true;
    }
  }
  return false;
}

/* A cut that opens or closes inside a sentence, moved to the sentence's edge — the snap
 * tool's head and tail rule (research/tools/edl_snap.py: a breath before the first word,
 * the last word let land). One undo entry, and only on the click. */
const PAD_HEAD = 0.25, PAD_TAIL = 0.45;
/* A fix's edge on the frame grid, rounded AWAY from what it fixes: the timeline snaps a
 * drag to the nearest frame, but a fix that rounded inward would leave one frame of the
 * covered lens in, or clip the last frame of the line it was meant to keep. 'down' =
 * earlier, 'up' = later; the value is already on the grid, so the timeline keeps it. */
function gridAway(t, dir) {
  const r = window.tl && typeof tl.gridRate === 'function' ? tl.gridRate() : 0;
  if (!r) return t;
  const f = dir === 'up' ? Math.ceil(t * r - 1e-6) : Math.floor(t * r + 1e-6);
  return Math.round((f / r) * 1e4) / 1e4;
}

function fixEdge(seg, edge) {
  const clip = P.clips[seg.clip] || {};
  const t = edge === 'in' ? seg.in : seg.out;
  const u = (clip.transcript || []).find((x) => x.start + 0.05 < t && t < x.end - 0.05);
  if (!u) return;
  tl.begin('fix');
  if (edge === 'in') tl.setRange(seg.id, gridAway(Math.max(0, u.start - PAD_HEAD), 'down'), null);
  else {
    const end = clip.duration ?? 1e9;
    const want = gridAway(u.end + PAD_TAIL, 'up');
    tl.setRange(seg.id, null, want <= end ? want : gridAway(end, 'down'));
  }
  tl.commit();
}

/* An unusable stretch trimmed off whichever edge leaves more of the shot. */
function trimBad(seg, k) {
  const u = unusableFor(seg)[k];
  if (!u) return;
  const keepHead = u.start - seg.in, keepTail = seg.out - u.end;
  tl.begin('trim it out');
  // past the bad stretch, never into it (gridAway): no frame of it stays in the shot
  if (keepTail >= keepHead) tl.setRange(seg.id, Math.min(seg.out - 0.2, gridAway(u.end, 'up')), null);
  else tl.setRange(seg.id, null, Math.max(seg.in + 0.2, gridAway(u.start, 'down')));
  tl.commit();
}

/* The strip's controls, delegated once. Every edit goes through the timeline's API,
 * so it is one undo entry, repaints the board and reaches the autosave as before. */
function onInspectorClick(e) {
  const b = e.target.closest('button');
  if (e.target.closest('img.poster')) {
    // The still is the shot; clicking it plays the cut from here, in the monitor.
    const seg = inspected();
    if (seg) { revealMonitor(); playFrom(tl.indexOf(seg.id)); }
    return;
  }
  if (!b) return;
  const act = b.dataset.act;
  if (act === 'delall') { tl.remove([...tl.state.sel]); return; }
  if (act === 'fstr') { const v = parseFloat(b.dataset.v); colourEdit('look strength', () => { colour.strength = v; }); return; }
  if (act === 'fauto') { colourEdit('auto-balance', () => { colour.mode = colour.mode === 'off' ? 'auto' : 'off'; }); return; }
  if (act === 'nextwarn') { nextWarned(); return; }
  if (b.dataset.disc) {
    // a disclosure: open one pane, close it again; both start closed on every shot
    const pane = $(`#inspector [data-pane="${b.dataset.disc}"]`);
    if (!pane) return;
    pane.hidden = !pane.hidden;
    b.classList.toggle('on', !pane.hidden);
    b.textContent = b.textContent.replace(/[▸▾]$/, pane.hidden ? '▸' : '▾');
    return;
  }
  const seg = inspected();
  if (!seg) return;
  const i = tl.indexOf(seg.id);
  if (act === 'play') { revealMonitor(); playFrom(i, { single: true }); return; }
  if (act === 'del') { tl.remove([seg.id]); return; }
  if (act === 'fixin' || act === 'fixout') { fixEdge(seg, act.slice(3)); return; }
  if (act === 'trimbad') { trimBad(seg, Number(b.dataset.k)); return; }
  if (act === 'ask') {
    if (shotAskOpen.has(seg)) shotAskOpen.delete(seg); else shotAskOpen.add(seg);
    renderInspector();
    const note = $('#inspector .shotNote');
    if (note && shotAskOpen.has(seg)) note.focus();
    return;
  }
  if (act === 'shotgo') {
    const note = $('#inspector .shotNote').value.trim();
    if (!note) return toast('say what should change in this shot');
    ask({ note, focus: i, button: b, state: $('#inspector .shotState') });
    return;
  }
  if (act && act.startsWith('c')) onColourAct(act, seg);
}

/* The `[` `]` `{` `}` keys' nudge — the trims the strip no longer has buttons for. */
function nudge(i, edge, d) {
  const seg = segs[i];
  const dur = (P.clips[seg.clip] || {}).duration ?? 1e9;
  if (edge === 'in') seg.in = Math.max(0, Math.min(seg.out - 0.2, seg.in + d));
  else seg.out = Math.max(seg.in + 0.2, Math.min(dur, seg.out + d));
  seg.in = Math.round(seg.in * 100) / 100;
  seg.out = Math.round(seg.out * 100) / 100;
  if (edge === 'in') refreshPosters();   // debounced: one frame per trim, not per press
}

/* ------------------------------------------------------------ colour (INTAKE M10, I10.4)
 *
 * The film's `colour` block is the EDL's, kept here and sent with every save — the same
 * body as the segments, the story and the music; there is no second save path. What the
 * strip shows for a shot (the colour in words, the resolved look and strength) is the
 * server's word from GET /api/colour, refetched after every save, because a trim
 * re-derives the auto and a nudge changes what the monitor's LUT bakes. `colour` itself
 * is never overwritten from the server: the server normalises the block (fills look:
 * null, strength 0.5) but does not change its meaning, and a change made while a save
 * was in flight must not be lost to the reply.
 *
 * Every colour change is one entry on the board's undo stack (INTAKE M16 I16.4): the
 * block rides each entry beside the cut (tl's `extra` hook), so ⌘Z takes back a warmer
 * the way it takes back a trim. */
let colour = {};              // the EDL's colour block: {mode, look, strength, reference, shots}
let C = null;                 // GET /api/colour: {film, looks, shots, clips}

/* /grade.js's API, or null when it did not load. Checked by shape, not by truthiness:
 * an element with an id is reachable as window.<id>, so a bare `window.grade` can be
 * an element rather than the module — and the board must paint without the grade. */
function gradeApi() {
  const g = window.grade;
  return g && typeof g.setShot === 'function' ? g : null;
}

const NUDGE_GAIN = 0.02, NUDGE_EXPOSURE = 0.05;
const DEFAULT_BALANCE = { gain: [1, 1, 1], exposure: 1, knee: 0.8, lift: 0 };

function colourShot(id) {
  return C && C.shots ? C.shots.find((s) => s.id === id) : undefined;
}

function shotOverride(id, create = false) {
  if (!colour.shots) { if (!create) return undefined; colour.shots = {}; }
  if (!colour.shots[id] && create) colour.shots[id] = {};
  return colour.shots[id];
}

/* Drop empty overrides so the saved block says only what was set. */
function tidyColour() {
  if (colour.shots) {
    for (const [id, o] of Object.entries(colour.shots)) {
      if (!o || !Object.keys(o).length) delete colour.shots[id];
    }
    if (!Object.keys(colour.shots).length) delete colour.shots;
  }
}

async function refreshColour() {
  try {
    C = await (await fetch('/api/colour')).json();
  } catch (e) { return; }
  const box = $('#inspector');
  const seg = inspecting && typeof inspecting === 'object' ? inspecting : null;   // boot: nothing mounted yet
  if (seg && box.querySelector('.colour')) fillColour(box, seg);
  if (inspecting === 'none' && box.querySelector('.film')) fillFilm(box);
  const g = gradeApi();
  if (g) g.invalidate();
}

/* One change to the block, one undo entry: keep the block tidy, save through the one
 * path (the commit touches the autosave), and show the change at once — the words and
 * the monitor catch up when the save's refetch lands. */
function colourEdit(label, fn) {
  tl.begin(label);
  fn();
  tidyColour();
  tl.commit();
  renderInspector();
}

/* The colour, in words: what the auto (or a hand) did to the shot. */
function colourWords(shot) {
  if (!shot) return 'colour: as shot';
  const w = shot.witness || {};
  const bal = shot.balance;
  const parts = [bal ? (bal.source === 'hand' ? 'set by hand' : 'auto-balanced') : 'as shot'];
  if (bal) {
    const e = Math.round((Number(bal.exposure) - 1) * 100);
    if (e) parts.push(`${Math.abs(e)}% ${e > 0 ? 'brighter' : 'darker'}`);
    const warm = Math.round((bal.gain[0] - bal.gain[2]) * 50);
    if (warm) parts.push(`${Math.abs(warm)}% ${warm > 0 ? 'warmer' : 'cooler'}`);
  }
  if (w.clip > 0.005) parts.push(`${Math.round(w.clip * 100)}% of the frame is blown out`);
  if (shot.match) {
    const o = shotOverride(shot.id) || {};
    parts.push(`matched to the ${o.match === 'previous' ? 'shot before' : 'reference shot'}`);
  }
  return `colour: ${parts.join(', ')}`;
}

/* The look select's options: the first is the fallback (the film's look for a shot;
 * "none" for the film), a shot also gets "none" to switch its look off alone, then
 * every look in the library — a broken manifest entry disabled with its reason. */
function lookOptions(sel, firstLabel, withNone) {
  const looks = (C && C.looks) || [];
  const key = firstLabel + '|' + looks.map((l) => `${l.name}:${l.kind}`).join(',');
  if (sel.dataset.key === key) return;
  sel.dataset.key = key;
  sel.innerHTML = `<option value="">${escapeHtml(firstLabel)}</option>`
    + (withNone ? '<option value="none">none</option>' : '')
    + looks.map((l) => l.kind === 'broken'
      ? `<option value="${escapeHtml(l.name)}" disabled title="${escapeHtml(l.error || 'broken')}">${escapeHtml(l.name)} — broken: ${escapeHtml(l.error || '')}</option>`
      : `<option value="${escapeHtml(l.name)}" title="${escapeHtml(l.description || '')}">${escapeHtml(l.name)}</option>`).join('');
}

const setIfIdle = (el, v) => { if (document.activeElement !== el && el.value !== String(v)) el.value = v; };

/* Fill the shot's colour in place — nothing here rebuilds the strip, so a why being
 * typed is not interrupted. */
function fillColour(box, seg) {
  const q = (s) => box.querySelector(s);
  const blk = q('.colour');
  if (!blk) return;
  const id = seg.id;
  const shot = colourShot(id);
  const over = shotOverride(id) || {};
  const w = q('.witness');
  if (w) w.textContent = colourWords(shot);

  const auto = q('.cauto');
  auto.checked = over.auto !== false;
  auto.disabled = colour.mode === 'off' || !!over.balance;
  auto.parentNode.title = colour.mode === 'off' ? 'the film is off: no auto on any shot'
    : over.balance ? 'a hand balance is set — reset it to go back to the auto'
      : 'the auto balance for this shot (unchecked: as shot)';

  const filmLook = colour.look ? colour.look : 'none';
  lookOptions(q('.clook'), `the film's (${filmLook})`, true);
  setIfIdle(q('.clook'), 'look' in over ? (over.look || 'none') : '');
  const strength = 'strength' in over ? over.strength
    : shot ? shot.strength : (colour.strength ?? 0.5);
  setIfIdle(q('.cstrength'), strength);
  q('.cstrengthVal').textContent = Number(strength).toFixed(2);

  q('[data-act="cmatchprev"]').classList.toggle('on', over.match === 'previous');
  q('[data-act="cmatchref"]').classList.toggle('on', over.match === 'reference');
  const isRef = colour.reference === id;
  const refBtn = q('[data-act="csetref"]');
  refBtn.classList.toggle('on', isRef);
  refBtn.textContent = isRef ? 'the reference' : 'set as reference';
  q('[data-act="cmatchref"]').disabled = isRef;
  q('[data-act="creset"]').disabled = !over.balance;
}

/* The nudges start from the balance the shot resolves to today — the auto's numbers
 * when the auto is on, the hand's when one is set — so "warmer" is a step from what the
 * monitor shows, never from a blank. */
function nudgeBalance(id, fn) {
  const shot = colourShot(id);
  const base = (shotOverride(id) || {}).balance || (shot && shot.balance) || DEFAULT_BALANCE;
  const b = { gain: [...base.gain], exposure: base.exposure, knee: base.knee ?? 0.8,
              lift: base.lift ?? 0 };
  fn(b);
  b.gain = b.gain.map((g) => Math.round(g * 10000) / 10000);
  b.exposure = Math.round(b.exposure * 10000) / 10000;
  shotOverride(id, true).balance = b;
}

/* The colour buttons; the selects and sliders have their own listeners in bindColour. */
const COLOUR_LABEL = { cwarm: 'warmer', ccool: 'cooler', cbright: 'brighter', cdark: 'darker',
  creset: 'colour reset', cmatchprev: 'match', cmatchref: 'match', csetref: 'reference' };
function onColourAct(act, seg) {
  if (!COLOUR_LABEL[act]) return false;
  const id = seg.id;
  colourEdit(COLOUR_LABEL[act], () => {
    if (act === 'cwarm') nudgeBalance(id, (b) => { b.gain[0] += NUDGE_GAIN; b.gain[2] -= NUDGE_GAIN; });
    else if (act === 'ccool') nudgeBalance(id, (b) => { b.gain[0] -= NUDGE_GAIN; b.gain[2] += NUDGE_GAIN; });
    else if (act === 'cbright') nudgeBalance(id, (b) => { b.exposure += NUDGE_EXPOSURE; });
    else if (act === 'cdark') nudgeBalance(id, (b) => { b.exposure -= NUDGE_EXPOSURE; });
    else if (act === 'creset') { const o = shotOverride(id); if (o) delete o.balance; }
    else if (act === 'cmatchprev' || act === 'cmatchref') {
      const want = act === 'cmatchprev' ? 'previous' : 'reference';
      const o = shotOverride(id, true);
      if (o.match === want) delete o.match; else o.match = want;   // again: off
    } else if (act === 'csetref') {
      colour.reference = colour.reference === id ? null : id;
    }
  });
  return true;
}

function bindColour(el, seg) {
  const q = (s) => el.querySelector(s);
  q('.cauto').addEventListener('change', (ev) => {
    const on = ev.target.checked;
    colourEdit('auto-balance', () => {
      const o = shotOverride(seg.id, true);
      if (on) delete o.auto; else o.auto = false;
    });
  });
  q('.clook').addEventListener('change', (ev) => {
    const v = ev.target.value;
    colourEdit('look', () => {
      const o = shotOverride(seg.id, true);
      if (v === '') delete o.look; else o.look = v === 'none' ? null : v;
    });
  });
  q('.cstrength').addEventListener('input', (ev) => {
    q('.cstrengthVal').textContent = Number(ev.target.value).toFixed(2);
  });
  q('.cstrength').addEventListener('change', (ev) => {
    const v = parseFloat(ev.target.value);
    colourEdit('look strength', () => { shotOverride(seg.id, true).strength = v; });
  });
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
  for (let k = 0; k < i && k < segs.length; k++) t += tl.dur(segs[k]);
  return t;
}

/* Where a clip time inside shot i sits in the film: its offset from the in-point at
 * the shot's rate — the transport's position and the playhead both read this. */
function filmAt(i, clipT) {
  const seg = segs[i];
  return filmStart(i) + Math.max(0, clipT - seg.in) / speedOf(seg);
}

function liveVideo() { return player.vids[player.cur]; }

/* Point a buffer at a shot's in-point without playing it.
 *
 * The `#t=` is a media fragment, and it is the difference between fetching the shot
 * and fetching the top of the file. The elements were `preload="auto"` and got their
 * src before anything told them where the shot starts, so Chrome did the only thing
 * it could and downloaded from byte 0: measured on the Killington board, a shot
 * playing at 188.2 s had 0–15 s buffered, and the seek to 188.2 waited its turn
 * behind that. With `preload="metadata"` on the element and the in-point in the URL,
 * the first request after the moov lands where the shot is.
 *
 * `dataset.src` stays the bare proxy URL — it is how the rest of the monitor asks
 * "which clip is this buffer holding", and a fragment in it would make every check
 * miss. Re-arming the same clip at a different in-point is the seek below, not a
 * reload: the file is already open.
 *
 * The shot's `speed` (INTAKE M13) is the buffer's playback rate: set AFTER load(),
 * which resets `playbackRate` to the default, and kept on `dataset.speed` so the JKL
 * shuttle (timeline-keys.js, which rides on `defaultPlaybackRate`) can multiply it
 * rather than overwrite it. A generated clip (`gen_…`) is armed like any other: the
 * server lists it with a proxy. */
function arm(v, seg, at = seg.in) {
  const src = (P.clips[seg.clip] || {}).proxy || '';
  if (v.dataset.src !== src || (v.error && !v._waitStop)) {
    v.dataset.src = src;
    if (v._waitStop) { v._waitStop(); v._waitStop = null; }
    v.src = src ? `${src}#t=${Math.max(0, at).toFixed(2)}` : src;
    v.load();
  }
  if (v._roll) {                    // whatever it was prerolling, it is parked now
    v._roll = null;
    if (!v.paused) v.pause();
  }
  setShotRate(v, seg);
  // Deferred, so by the time metadata arrives this buffer may have been pointed at a
  // different clip; parking the old shot would then seek the new one.
  // A buffer already there is left alone: the same time set again is still a seek (a
  // decode from the keyframe before it) — verdict 4 punch 5.
  const park = () => {
    if (v.dataset.src === src && Math.abs(v.currentTime - at) > 0.002) v.currentTime = at;
  };
  if (v.readyState >= 1) park();
  else v.addEventListener('loadedmetadata', park, { once: true });
}

/* The buffer plays its shot at the shot's rate times the shuttle's (1 unless J/L is
 * held). Applied at arm and again right before play(): a load() in between resets it. */
function setShotRate(v, seg) {
  const spd = speedOf(seg);
  v.dataset.speed = String(spd);
  v.playbackRate = (v.defaultPlaybackRate || 1) * spd;
}

function showLive() {
  player.vids.forEach((v, k) => v.classList.toggle('live', k === player.cur));
  // The grade follows the monitor (INTAKE M10): the live video's shot picks the LUT.
  const g = gradeApi();
  if (g) g.setShot(segs[player.idx] ? segs[player.idx].id : null);
}

/* The monitor sits at the top of the column. When the shot list ran a long way below
 * it, clicking shot 12's poster on the Killington cut started playback 3,163 px above
 * the viewport — measured — where nothing about it could be seen or heard to be about
 * that shot. The inspector sits right under the timeline now, but a short window can
 * still have it on screen with the monitor scrolled off, so a play started from it (or
 * from the keys) brings the monitor back first. */
function revealMonitor() {
  const el = $('#player');
  if (!el || el.style.display === 'none') return;
  const r = el.getBoundingClientRect();
  if (r.top >= 56 && r.bottom <= window.innerHeight) return;
  // The timeline made the monitor taller; in a short window it may not fit under the
  // sticky header with room to spare, and then its bottom — the transport and the
  // timeline — wins over the top of the picture.
  const gap = Math.max(8, Math.min(64, window.innerHeight - r.height - 8));
  window.scrollTo({ top: Math.max(0, window.scrollY + r.top - gap), behavior: 'smooth' });
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
  // the browser's own message (MEDIA_ELEMENT_ERROR: …) is for a console, not a screen
  return `${name}: ${MEDIA_ERR[e.code] || 'it would not play'}`;
}

/* The calm wait on the Program monitor: the poster stays, one line under it. */
function monWait(on) {
  // the media's own reason; dock.js holds the poster's (see dock.wait)
  if (window.dock && typeof dock.wait === 'function') { dock.wait('#monWait', on, 'media'); return; }
  const el = $('#monWait');
  if (el) el.hidden = !on;
}

/* A /media/proxy URL that would not play — no 720p proxy yet; the server 307s it to the
 * original or a 360p preview, or answers 503 while one builds (the MEDIA lane's contract):
 * the poster at the playhead and "preparing preview…" stay up, GET /media/status/<stem>.mp4
 * is asked every ~3 s (dock.mediaWait), and once it says ready the same /media/proxy URL
 * is loaded again. The URL is never rewritten here. */
function mediaWaitFor(v) {
  const src = v.dataset.src;
  if (v._waitStop && v._waitFor === src) {
    if (v === liveVideo()) { v.classList.remove('live'); clearTimeout(restT); paintRestStill(); }
    return;
  }
  if (v._waitStop) v._waitStop();
  v._waitFor = src;
  if (!src || !window.dock || typeof dock.mediaWait !== 'function') return;
  if (v === liveVideo()) {
    v.classList.remove('live');            // the poster shows through, not a black box
    clearTimeout(restT);
    paintRestStill();
  }
  v._waitStop = dock.mediaWait(src, () => {
    v._waitStop = null;
    if (v.dataset.src !== src) return;
    v.dataset.src = '';
    const seg = v === liveVideo() ? segs[player.idx] : null;
    if (seg) { arm(v, seg); showLive(); return; }
    v.dataset.src = src;
    v.src = src;
    v.load();
  }, (why) => { const el = $('#monWait span'); if (el && v === liveVideo()) el.textContent = why; });
}

/* A refused play is a fact about the browser, not about the cut, and it has to reach
 * the person: Chrome will not start an unmuted video without a gesture it recognises,
 * and the board's own click on a shot card is not always one it counts. */
function playRefused(err) {
  // No playable source yet (the proxy is building): the calm wait, not a refusal.
  if (err && (err.name === 'NotSupportedError' || err.name === 'AbortError')) {
    const v = liveVideo();
    pauseCut();
    monWait(true);
    if (v) mediaWaitFor(v);
    return;
  }
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
  if (!single && segs[i + 1]) armNext(player.vids[1 - player.cur], segs[i + 1]);
  showLive();
  // The shot's media is not ready (no proxy yet, waiting on /media/status): the poster at
  // the playhead and the calm line stay; the wait loads it, and the next play plays.
  if (v.error) { mediaWaitFor(v); monWait(true); player.playing = false; paintTransport(); return; }
  v.muted = a1Moved(seg);           // a moved sound plays from #pvA1 (programSync)
  const go = () => {
    if (g !== player.gen || !player.playing) return;   // pause, or a later command, won
    if (!resume) v.currentTime = seg.in;
    setShotRate(v, seg);
    v.play().catch((err) => { if (g === player.gen) playRefused(err); });
  };
  player.playing = true;            // before go(), which refuses to start a paused monitor
  screenMsg(v.readyState >= 2 ? '' : `opening ${stem(seg.clip)}…`);
  if (v.readyState >= 1) go(); else v.addEventListener('loadedmetadata', go, { once: true });
  const filmT = resume ? filmAt(i, v.currentTime) : filmStart(i);
  cueBed(filmT);
  tl.setPlayhead(filmT);            // the playhead jumps with the click, not on the first tick
  schedule();
  tl.render();
  paintTransport();
}

/* Park the monitor on shot i at clip time clipT, paused — the timeline's scrub. The
 * next space resumes from there: playFrom's resume rule sees the same shot, stopped,
 * inside its range. */
function cueAt(i, clipT) {
  if (!segs[i] || !player.vids.length) return;
  pauseCut();
  const seg = segs[i];
  const v = liveVideo();
  player.idx = i;
  sel = i;
  paint();
  arm(v, seg);
  const src = v.dataset.src;
  const at = Math.max(seg.in, Math.min(seg.out, clipT));
  const park = () => { if (v.dataset.src === src) v.currentTime = at; };
  if (v.readyState >= 1) park(); else v.addEventListener('loadedmetadata', park, { once: true });
  if (segs[i + 1]) armNext(player.vids[1 - player.cur], segs[i + 1]);
  showLive();
  if (v.error) { mediaWaitFor(v); monWait(true); }
  paintPos(filmAt(i, at), at, seg);
  paintTransport();
  tl.render();
}

function pauseCut() {
  player.gen++;                     // any shot still opening must not start behind this
  player.vids.forEach((v) => v.pause());
  if (bed.el) bed.el.pause();
  player.playing = false;
  cancelAnimationFrame(player.raf);
  progPause();
  screenMsg('');       // callers that have something to say set it after this
  paintTransport();
}

/* The Program monitor shows the sequence, not only V1 (verdict 3 punch 4): what the
 * export composites, the monitor draws. Two followers ride beside the pv0/pv1 pair and
 * are driven from the playhead (tl's `playhead` event, which every tick, cue and scrub
 * emits), never the other way round, so V1 stays the clock:
 *
 *   #pvV2  V2's picture. Inside an overlay (V2's eye on) it is shown over V1 — above the
 *          grade canvas, under the fx canvas — at `in + (t - at) * speed`; outside one it
 *          is hidden and parked on the next overlay's in-point, so entering one is a seek
 *          inside an open file. Ungraded, and the Effect Controls of V1 do not move it.
 *   #pvA1  A1's sound where it is not under its picture. A shot with `a_off` (unlinked
 *          and moved — relinking keeps the offset) has its picture element muted, and
 *          this audio-only element plays that shot's sound at `in + (t - start - a_off)
 *          * speed` while the film time is inside the moved sound: the gap before it is
 *          silent and its tail sounds over the next shot's own, as assemble.py's amix
 *          lays A1. A1's mute / solo / gain_db set its volume.
 *
 * V1's eye off hides the pv0/pv1 pair, the grade and the rest poster: black under V2.
 * While playing, a follower is re-seeked only when it drifts past a threshold. */
const prog = { v2: null, a1: null, tracks: null };

function progTrack(name) {
  const t = (prog.tracks || {})[name];
  return t || {};
}

function progA1Level() {
  if (!window.tl || typeof tl.audible !== 'function' || !tl.audible('A1')) return 0;
  return Math.pow(10, (Number(progTrack('A1').gain_db) || 0) / 20);   // > 1 is fine: rcAudio's gain node
}

/* A shot whose sound sits away from its picture: its picture element is muted. */
function a1Moved(seg) {
  return !!(seg && window.tl && typeof tl.aOff === 'function' && tl.aOff(seg));
}

function progPause() {
  [prog.v2, prog.a1].forEach((el) => { if (el && !el.paused) el.pause(); });
}

function progHide(el) {
  if (!el) return;
  if (!el.paused) el.pause();
  if (el.tagName === 'VIDEO' && el.style.visibility !== 'hidden') el.style.visibility = 'hidden';
}

/* Keep one follower on `clip` at clip time `clipT`, at `rate`, playing with the monitor
 * or parked. A file still opening keeps the latest wish and meets it on loadedmetadata. */
function progFollow(el, clip, clipT, rate, play, slack) {
  const src = (P.clips[clip] || {}).proxy || '';
  if (!src) { progHide(el); return false; }
  if (el.dataset.src !== src) {
    el.dataset.src = src;
    el.src = `${src}#t=${Math.max(0, clipT).toFixed(2)}`;
    el.load();
  }
  const lv = liveVideo();
  const r = ((lv && lv.defaultPlaybackRate) || 1) * rate;
  el._goal = { src, t: clipT, at: performance.now(), play, rate: r };
  if (el.readyState < 1) return true;
  if (Math.abs(el.playbackRate - r) > 1e-3) el.playbackRate = r;
  const off = Math.abs(el.currentTime - clipT);
  if (play) {
    if (el.paused) { el.currentTime = clipT; el.play().catch(() => {}); }
    else if (off > slack && !el.seeking) el.currentTime = clipT;
  } else {
    if (!el.paused) el.pause();
    if (off > 0.01) el.currentTime = clipT;
  }
  return true;
}

function progMeet(el) {
  const g = el._goal;
  if (!g || el.dataset.src !== g.src) return;
  el.playbackRate = g.rate;
  el.currentTime = g.t + (g.play ? ((performance.now() - g.at) / 1000) * g.rate : 0);
  if (g.play && player.playing) el.play().catch(() => {});
}

/* The overlay on V2 at film time t (the last laid wins), and the next one after it. */
function v2At(t) {
  let at = null, next = null;
  const list = window.tl && typeof tl.overlays === 'function' ? tl.overlays() : [];
  for (const o of list) {
    const end = o.at + (o.out - o.in) / (Number(o.speed) || 1);
    if (t >= o.at - 1e-6 && t < end) at = o;
    else if (o.at > t && (!next || o.at < next.at)) next = o;
  }
  return { at, next };
}

/* The moved A1 sound under film time t: `{seg, clipT}`, the latest-starting one. */
function a1At(t) {
  let start = 0, hit = null;
  for (const s of segs) {
    const d = tl.dur(s);
    const off = tl.aOff(s);
    if (off) {
      const from = start + off;
      if (t >= from && t < from + d) hit = { seg: s, clipT: s.in + (t - from) * speedOf(s) };
    }
    start += d;
  }
  return hit;
}

function programSync(filmT) {
  if (!prog.v2 || !window.tl || !segs.length) { progPause(); return; }
  const t = filmT == null ? (tl.state.playhead || 0) : filmT;
  const proposal = window.tlLanes && typeof tlLanes.mode === 'function' && tlLanes.mode() === 'proposal'
    && !player.playing;
  // V1's eye: the pair, the grade and the rest poster go dark; V2 still draws over black.
  const v1 = proposal || progTrack('V1').eye !== false ? '' : 'hidden';
  ['#pv0', '#pv1', '#gradeCanvas', '#monPoster'].forEach((s) => {
    const el = $(s);
    if (el && el.style.visibility !== v1) el.style.visibility = v1;
  });
  const play = player.playing;
  const { at: o, next } = proposal || progTrack('V2').eye === false ? { at: null, next: null } : v2At(t);
  if (o) {
    const spd = Number(o.speed) || 1;
    if (progFollow(prog.v2, o.clip, o.in + (t - o.at) * spd, spd, play, 0.2)) prog.v2.style.visibility = '';
  } else {
    progHide(prog.v2);
    if (next) progFollow(prog.v2, next.clip, next.in, Number(next.speed) || 1, false, 0);
  }
  // A1: the live picture is silent when its own sound was moved; the moved sound plays here.
  const at = player.playing && segs[player.idx] ? player.idx : (tl.shotAt(t) || {}).index;
  const lv = liveVideo();
  if (lv && segs[at] && !proposal) {
    const mute = a1Moved(segs[at]);
    if (lv.muted !== mute) lv.muted = mute;
  }
  const hit = proposal ? null : a1At(t);
  if (hit) {
    const lvl = progA1Level();
    if (window.rcAudio) rcAudio.setLevel(prog.a1, 'track', lvl);
    else if (Math.abs(prog.a1.volume - Math.min(1, lvl)) > 1e-3) prog.a1.volume = Math.min(1, lvl);
    progFollow(prog.a1, hit.seg.clip, hit.clipT, speedOf(hit.seg), play, 0.12);
  } else progHide(prog.a1);
}

function programInit() {
  prog.v2 = $('#pvV2');
  prog.a1 = $('#pvA1');
  if (!prog.v2 || !prog.a1) return;
  prog.tracks = tl.tracks();
  // laid out always and hidden by visibility: a layer that stays composited presents
  // its sought frame the moment it is shown (display:none dropped the frame callback)
  prog.v2.style.display = 'block';
  prog.v2.style.visibility = 'hidden';
  [prog.v2, prog.a1].forEach((el) => {
    el.dataset.src = '';
    el.addEventListener('loadedmetadata', () => progMeet(el));
  });
  tl.on('playhead', (ev) => programSync(ev && ev.t));
  tl.on('tracks', (ev) => { prog.tracks = (ev && ev.tracks) || tl.tracks(); programSync(); });
  tl.on('change', () => programSync());
}

function toggleCut() {
  if (player.playing) return pauseCut();
  playFrom(sel);
}

/* True when the shot under the playhead ended and the monitor moved on or stopped. The
 * shot ends at its `out` in CLIP time whatever its speed; the film position it reports
 * is the clip offset at the rate. */
function boundary() {
  const v = liveVideo();
  const seg = segs[player.idx];
  if (!seg) { pauseCut(); return true; }
  const clipT = v.currentTime;
  const filmT = filmAt(player.idx, clipT);
  paintPos(filmT, clipT, seg);
  bedTick(filmT, clipT, seg);
  if (clipT >= seg.out - 0.04 || v.ended) { advance(); return true; }
  preroll((seg.out - clipT) / speedOf(seg));
  return false;
}

/* The cut, prepared (verdict 4 punch 5). The buffer holding the next shot is parked
 * PREROLL_S of its clip ahead of the in-point (armNext), and starts playing — muted,
 * not shown — when the shot on screen has that long left, less what a start takes. At
 * the cut it is already running and on its in-point: advance() only swaps which buffer
 * is shown. Without this, every cut paid for a pipeline start (~50 ms) and before that a
 * re-seek, a decode from the keyframe (the 317 ms hole verdict 4 measured on Killington). */
const PREROLL_S = 0.3;
const roll = { lat: 0.06 };         // seconds from play() to the first frame, learned

function prerollAt(seg) {
  return Math.max(0, seg.in - PREROLL_S * speedOf(seg));
}

function armNext(v, seg) {
  arm(v, seg, prerollAt(seg));
}

function preroll(left) {
  if (player.single) return;
  const nseg = segs[player.idx + 1];
  const nb = player.vids[1 - player.cur];
  if (!nseg || !nb || nb._roll || !nb.paused || nb.seeking || nb.readyState < 2) return;
  if (nb.dataset.src !== ((P.clips[nseg.clip] || {}).proxy || '')) return;
  const lead = (nseg.in - nb.currentTime) / speedOf(nseg);    // film seconds to its in-point
  if (lead < -0.01 || lead > PREROLL_S + 0.05) return;        // not parked for this cut
  // boundary() cuts 0.04 s before the out; the start's latency is real time, the
  // rest film time, so it scales with the J/L shuttle's rate
  const shuttle = (liveVideo() && liveVideo().defaultPlaybackRate) || 1;
  if (left - 0.04 > lead + roll.lat * shuttle) return;
  const g = player.gen;
  nb._roll = { gen: g, seg: nseg };
  nb.muted = true;
  setShotRate(nb, nseg);
  const t0 = performance.now();
  if (typeof nb.requestVideoFrameCallback === 'function') {
    nb.requestVideoFrameCallback(() => {
      roll.lat = Math.min(0.25, roll.lat * 0.7 + ((performance.now() - t0) / 1000) * 0.3);
    });
  }
  nb.play().catch(() => { if (nb._roll && nb._roll.gen === g) nb._roll = null; });
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
    progPause();
    screenMsg('');
    if (!player.single) { sel = 0; player.idx = -1; paint(); }   // the end: space restarts
    paintTransport();
    return;
  }
  player.cur = 1 - player.cur;
  player.idx = next;
  sel = next;
  tl.syncSel();                 // paint()'s first half; the inspector waits (below)
  const nv = liveVideo();
  // Prerolled for this cut (see preroll): it is playing, near its in-point; keep it so.
  const rolled = !!nv._roll && nv._roll.gen === g - 1 && nv._roll.seg === segs[next] && !nv.paused;
  if (rolled) {
    nv._roll = null;
    // a start that went badly wrong is put right; a frame or two either side is not
    if (Math.abs(nv.currentTime - segs[next].in) > 0.15) nv.currentTime = segs[next].in;
  } else {
    arm(nv, segs[next]);        // normally armed already; re-arming survives edits made mid-play
  }
  nv.muted = a1Moved(segs[next]);
  showLive();
  const go = () => {
    if (g !== player.gen || !player.playing) return;
    if (rolled) { setShotRate(nv, segs[next]); return; }
    // Parked on its in-point already (arm did it while the last shot played): play from
    // there. Setting the same time again is a seek all the same — a decode from the
    // keyframe before it, up to a second of frames — and it was most of the hole at the
    // cut (verdict 4 punch 5: 317 ms on Killington, 450 ms on a 59.94 fixture at load 20).
    if (!parkedAt(nv, segs[next].in)) nv.currentTime = segs[next].in;
    setShotRate(nv, segs[next]);
    nv.play().catch((err) => { if (g === player.gen) playRefused(err); });
  };
  screenMsg(nv.readyState >= 2 ? '' : `opening ${stem(segs[next].clip)}…`);
  if (nv.readyState >= 1) go(); else nv.addEventListener('loadedmetadata', go, { once: true });
  // What the cut does not need on its own frame waits for the new shot's first frames:
  // the buffer we left takes the shot after this one (its seek would take the decoder
  // threads the new shot is using), Effect Controls rebuilds for the new shot and the
  // timeline repaints — at load 20 those were ~100 ms of main thread in the cut's frame.
  // The inspector and the timeline catch up even if the monitor was stopped meanwhile.
  // One piece per frame, so no frame carries all three. The block highlight is not one of
  // them: it is a class flip with no layout, so it moves AT the cut (deferred with the
  // repaint it lagged the picture by up to half a second; the wave-4 gate caught it).
  for (const b of document.querySelectorAll('#tl .live[data-i]')) b.classList.remove('live');
  for (const b of document.querySelectorAll(`#tl .blk[data-i="${next}"], #tl .ablk[data-i="${next}"]`)) {
    b.classList.add('live');
  }
  afterFirstFrames(nv, () => {
    if (g === player.gen && segs[next + 1]) armNext(v, segs[next + 1]);
    afterFirstFrames(nv, () => {
      renderInspector();
      afterFirstFrames(nv, () => tl.render(), 1);
    }, 1);
  });
  paintTransport();
  schedule();
}

/* A buffer whose picture is the frame at clip time t, nothing pending. */
function parkedAt(v, t) {
  return v.readyState >= 2 && !v.seeking && Math.abs(v.currentTime - t) < 0.002;
}

/* Run fn once the buffer has presented `frames` more frames (a paused one presents
 * none), or after 400 ms, whichever comes first. */
function afterFirstFrames(v, fn, frames = 3) {
  let done = false, n = 0;
  const run = () => { if (done) return; done = true; fn(); };
  const t = setTimeout(run, 400);
  if (typeof v.requestVideoFrameCallback !== 'function') return;
  const cb = () => {
    if (done) return;
    if (++n >= frames) { clearTimeout(t); run(); } else v.requestVideoFrameCallback(cb);
  };
  v.requestVideoFrameCallback(cb);
}

/* Keep the monitor honest against the timeline it is playing: hide it when there is
 * nothing to play, and re-cue if the shot under the playhead was edited out from under it. */
function syncPlayer() {
  // A first cut's proposal plays in the monitor too, before there is a cut.
  const progEmpty = $('#progEmpty');
  if (progEmpty) progEmpty.hidden = !!(segs.length || pendingPlan);
  // the empty Program says how to fill it, in Premiere's words (verdict 2, punch 8)
  if (progEmpty && !progEmpty.hidden && !progEmpty.dataset.said) {
    progEmpty.dataset.said = '1';
    // only the words: firstpass.js mounts its button in here too, which workspace.css
    // does not draw (verdict 5, punch 9: Run first pass is the header's and the Timeline's)
    const words = [...progEmpty.childNodes].find((n) => n.nodeType === 3 && n.textContent.trim());
    if (words) words.textContent = 'the sequence is empty · drag a clip to the Timeline or press , (Insert)';
  }
  if (player.idx >= segs.length) { pauseCut(); player.idx = -1; }
  if (player.playing && player.idx >= 0) {
    const seg = segs[player.idx];
    const v = liveVideo();
    const src = (P.clips[seg.clip] || {}).proxy || '';
    if (v.dataset.src !== src || v.currentTime < seg.in - 0.5 || v.currentTime > seg.out + 0.5) {
      playFrom(player.idx, { single: player.single });
    } else if (v.dataset.speed !== String(speedOf(seg))) {
      setShotRate(v, seg);          // the shot's speed changed under the monitor mid-play
    }
  }
  tl.render();
  $('#total').textContent = tcOf(total());
  paintTransport();
}

/* The monitor at rest (INTAKE M16 I16.1): the frame under the playhead and a big ▶,
 * never a black box. The still is the poster endpoint's, shown only while no video is
 * live on the screen; once one is, its own parked frame is the picture. The still is
 * re-pointed once the edits and the playhead settle (restSoon, the inspector's 450 ms),
 * never per nudge — the same frame the inspector asks for, so one request serves both. */
let restT = 0;
function paintRest() {
  const big = $('#bigPlay');
  if (big) big.hidden = player.playing || !segs.length;
  const img = $('#monPoster');
  if (!img) return;
  const live = player.vids.some((v) => v.classList.contains('live') && v.dataset.src);
  if (live || !segs.length) { img.hidden = true; return; }
  restSoon();
}

function paintRestStill() {
  const img = $('#monPoster');
  const live = player.vids.some((v) => v.classList.contains('live') && v.dataset.src);
  const at = segs.length ? tl.shotAt(tl.state.playhead || 0) : null;
  const seg = at && segs[at.index];
  const base = seg && (P.clips[seg.clip] || {}).poster;
  if (live || !base) { img.hidden = true; return; }
  const src = `${base}${base.includes('?') ? '&' : '?'}t=${Math.max(0, at.clipT).toFixed(2)}`;
  if (img.getAttribute('src') !== src) img.setAttribute('src', src);
  img.hidden = false;
}

/* The playhead moved while nothing is live (a scrub before the first play, an undo):
 * the still follows, once the moving stops. */
function restSoon() {
  clearTimeout(restT);
  restT = setTimeout(paintRestStill, 450);
}

function paintTransport() {
  $('#playCut').textContent = player.playing ? '❚❚ Pause' : '▶ Play';
  paintRest();
  const seg = segs[player.idx];
  $('#playingWhat').textContent = seg
    ? `${player.idx + 1}/${segs.length} · ${stem(seg.clip)}${player.single ? ' · this shot only' : ''}`
    : '';
}

/* The playhead is the timeline's, in film time (the strip used to carry it as a
 * percentage inside one block). */
function paintPos(filmT, clipT, seg) {
  // While playing, the timeline is asked to keep the playhead in view four times a
  // second rather than every frame: its check reads the view's width, and after this
  // frame's writes that read was a forced layout of the whole board on every tick
  // (verdict 4 punch 5). The readout is written after the playhead, once, if it changed.
  const now = performance.now();
  const reveal = !player.playing || now - posReveal > 250;
  if (reveal) posReveal = now;
  tl.setPlayhead(filmT, { reveal });
  const s = tcOf(filmT), el = $('#pos');
  if (el.textContent !== s) el.textContent = s;
}
let posReveal = 0;

/* The monitors read Premiere's HH:MM:SS:FF, the timeline's own timecode (tl.tc). */
function tcOf(t) { return window.tl && typeof tl.tc === 'function' ? tl.tc(t) : fmt(t); }

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

/* `sel` is an index here and a set of ids in the timeline; the module maps the two at
 * this boundary (tl.syncSel adopts a moved index; a click on a block sets it). The
 * inspector follows the anchor: a cleared timeline selection — a click on its empty
 * lane, Esc — leaves it saying so. */
function paint() {
  tl.syncSel();
  renderInspector();
}

/* The board opened on a hand-authored EDL, so an empty timeline used to be an
 * impossible state. Now that a project can start from nothing but a footage folder it
 * is the *first* state, and it has to say what to do next rather than show a blank. */
function emptyState() {
  const el = document.createElement('div');
  el.className = 'empty';
  const analysed = S ? S.analysed : 0;
  // Footage goes in before the AI has heard it (verdict 2, punch 8): the first action
  // is the editor's — a drag or `,`. "Run first pass" beside it is lane FPUI's button,
  // which firstpass.js puts into this box (`.fp-empty`) after every render.
  const lead = 'Drag a clip here or press <kbd>,</kbd> (Insert)';
  if (!analysed) {
    el.innerHTML = `<h3>${lead}</h3>`;
    el.dataset.ai = 'pass';
    return el;
  }
  // One sentence about the film (INTAKE M16 I16.4): the EDL's story — the Ask tool's
  // field, /open's, this one — and ONE priced button. With keeps it is Cut from the bin
  // (heroes fixed, keeps as bounds); without, the first cut from the index.
  el.innerHTML = `<h3>No cut yet</h3>
    <div class="hint" style="margin-bottom:8px">${lead}</div>
    <label class="ask-q" for="firstNote">What is this film about?</label>
    <textarea id="firstNote" style="min-height:60px"
      placeholder="optional — a sentence is enough"></textarea>
    <div class="firstRow" style="display:flex;gap:10px;align-items:center;justify-content:center;margin-top:10px;flex-wrap:wrap">
      <button id="firstCut" data-next-for="cut"${unpricedAttr(firstMode())}>${firstLabel()}${priceTag(firstMode())}</button>
      <span class="hint" id="firstAims">${escapeHtml(aimsFor())}</span>
    </div>
    <div class="hint" id="firstState" style="margin-top:8px"></div>`;
  // The first pass makes a first cut too, so the empty Timeline offers ONE AI action
  // (verdict 4, punch 10: "Make a first cut" sat beside "First pass running — see it"):
  // the first pass's (firstpass.js puts it in this box) until a pass has finished, then
  // this button — the first cut from the keeps or the index — and the pass's box steps
  // aside (`data-ai`, workspace.css). Re-checked each second while the empty state is on
  // screen, since a run starts and ends without a render. While one runs this button
  // also waits rather than start an overlapping cut.
  const fc = el.querySelector('#firstCut');
  const gate = () => {
    const fp = window.rcFirstPass && typeof window.rcFirstPass.state === 'function'
      ? window.rcFirstPass.state() : null;
    const busy = !!fp && fp.state === 'running';
    const ai = window.rcFirstPass && !(fp && ['done', 'stale-after-edits'].includes(fp.state))
      ? 'pass' : 'cut';
    if (el.dataset.ai !== ai) el.dataset.ai = ai;
    if (fc.dataset.fpBusy !== String(busy)) {
      fc.dataset.fpBusy = String(busy);
      fc.disabled = busy;
      fc.title = busy ? 'the first pass is running — it makes a first cut too' : '';
    }
    return busy;
  };
  gate();
  let ticks = 0;
  const gateTimer = setInterval(() => {
    if (!el.isConnected && ticks++ > 0) clearInterval(gateTimer);   // rebuilt or filled
    else gate();
  }, 1000);
  const box = el.querySelector('#firstNote');
  box.value = $('#story').value;
  box.oninput = () => { $('#story').value = box.value; touch(); };
  el.querySelector('#firstCut').onclick = () => {
    if (gate()) return undefined;
    const opts = { button: el.querySelector('#firstCut'), state: el.querySelector('#firstState') };
    return keepsUsable().length ? cutFromBin(opts) : ask({ ...opts, note: '' });
  };
  return el;
}

/* The first cut is cut from the keeps when the pass kept any, from the index when not. */
function firstMode() { return keepsUsable().length ? 'bin' : 'first'; }
function firstLabel() {
  const n = keepsUsable().length;
  return n ? `Make the first cut from your ${n} keep${n === 1 ? '' : 's'}` : 'Make a first cut';
}

/* "aims for 2–3 min": the target every first cut and Ask is written to (the EDL's
 * `target_s`), said beside the buttons that send it — it was the header's
 * "target 2:00.0–3:00.0", far from what it governs. */
function aimsFor() {
  const [lo, hi] = (P && P.target) || [120, 180];
  const mins = lo >= 60 && lo % 60 === 0 && hi % 60 === 0;
  return mins ? `aims for ${lo / 60}–${hi / 60} min` : `aims for ${clock(lo)}–${clock(hi)}`;
}

function render() {
  // The empty state is rebuilt on every render — it reads the project's status, which
  // the reload after an index or a pass can change; the inspector fills in place.
  // The empty sequence's first action sits in the Timeline panel, where the cut will be
  // (the workspace, redesign wave 1); the Effect Controls panel says nothing is selected.
  const tlEmpty = $('#tlEmpty');
  if (tlEmpty) {
    if (!segs.length) tlEmpty.replaceChildren(emptyState()); else if (tlEmpty.childElementCount) tlEmpty.replaceChildren();
    tlEmpty.hidden = !!segs.length;
  }
  syncPlayer();
  renderInspector();

  // With an empty timeline there is nothing to change; the empty state asks for the cut
  // — and asks the sentence, so the Ask tool does not ask it a second time on the same
  // screen (Next lands here, on /#tool=ask, for the first cut).
  $('#askPanel').style.display = segs.length ? 'block' : 'none';
  $('#askStory').hidden = !segs.length;
  $('#askFirst').hidden = !!segs.length;
  $('#askAims').textContent = aimsFor();

  // Nothing in the header acts on an empty timeline, so nothing in the header shows.
  ['#undo', '#redo', '#makeFilm', '#saveState'].forEach((sel) => {
    $(sel).style.display = segs.length ? '' : 'none';
  });

  $('#total').textContent = tcOf(total());
  flowSoon();
  paintMusic();             // "loops once at 1:59 under a 3:09 cut" follows the cut's length
  paintVersions();          // so "this cut" follows the timeline rather than the last fetch
  renderLibrary();
  paintCutFromBin();        // the empty state is rebuilt above; its bin button follows
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
/* The bin's view (INTAKE M16 I16.4): 'out' — the keeps not in the cut, the default
 * (C10) — or 'all'; 'heard' and 'seen' are the machine's offers behind "more found ▸". */
let libTab = 'out';
const keepsTab = () => libTab === 'out' || libTab === 'all';

function renderLibrary() {
  const used = new Set(segs.map((s) => `${s.clip}@${Math.round(s.in)}`));
  const rows = [];
  let anySeen = false;
  /* The ranked events file, when the bin has one: kind x notable x corroboration x
   * whether a closer look confirmed it, computed once server-side rather than
   * re-guessed here from the kind alone. Falls back to the old kind ordering for a
   * bin looked at before the rank existed. */
  const ranked = P.events || [];
  const gone = junkConfirmed();
  for (const clip of Object.values(P.clips)) {
    if (gone.has(clip.clip)) continue;          // confirmed junk offers nothing to add
    const moments = (clip.visual || {}).moments || [];
    if (moments.length) anySeen = true;
    if (libTab === 'seen' && !ranked.length) {
      for (const m of moments) {
        if (!m.notable || m.kind === 'junk') continue;
        if (used.has(`${clip.clip}@${Math.round(m.start)}`)) continue;
        rows.push({ clip: clip.clip, t: m.start, end: m.end, why: m.what, kind: m.kind,
                    score: HOT.has(m.kind) ? 2 : m.kind === 'scenery' ? 0 : 1 });
      }
    } else if (libTab === 'heard') {
      for (const c of clip.candidates || []) {     // a generated clip has none
        if (used.has(`${clip.clip}@${Math.round(c.t)}`)) continue;
        rows.push({ clip: clip.clip, ...c });
      }
    }
  }
  if (libTab === 'seen' && ranked.length) {
    anySeen = true;
    for (const e of ranked) {
      if (!e.notable || e.kind === 'junk' || gone.has(e.clip)) continue;
      if (used.has(`${e.clip}@${Math.round(e.start)}`)) continue;
      rows.push({ clip: e.clip, t: e.start, end: e.end, why: e.what, kind: e.kind,
                  score: e.score, evidence: (e.why_ranked || {}).confirmation });
    }
  }
  rows.sort((a, b) => b.score - a.score);
  // kept is always there — an empty bin has something to say — and seen only once
  // something has been looked at.
  $('#libTabs .tab[data-tab=seen]').style.display = anySeen ? '' : 'none';
  if (!anySeen && libTab === 'seen') libTab = 'heard';
  document.querySelectorAll('#libTabs .tab, #binTabs .tab').forEach((x) =>
    x.classList.toggle('sel', x.dataset.tab === libTab));
  const lib = $('#library');
  lib.classList.remove('grid');                 // the keeps are a list now (verdict 3, punch 7)
  lib.classList.toggle('sel-lib', keepsTab());
  const bf = $('#binFilter');
  if (bf) bf.hidden = !keepsTab();
  paintAudit();
  if (keepsTab()) { renderKept(lib); return; }
  $('#libHint').textContent = '';
  lib.innerHTML = rows.length ? '' : `<div class="hint">${libTab === 'seen'
    ? `nothing left that was seen — <a href="/open" style="color:var(--accent)">look at more of
       the footage →</a>`
    : 'nothing left to add'}</div>`;
  rows.slice(0, 40).forEach((r) => {
    // The line people read is what is said or seen, not the ranking score that put it here.
    const d = document.createElement('div');
    d.className = 'cand';
    // The evidence word rides with the row: on this footage a `jump` nobody has
    // checked is often a tilted camera, and the human clicking is the last defence.
    // `fine-only` is a close look that nothing else saw — one look at the busiest
    // seconds, where the artefacts are (R10), so it says so rather than passing as a find.
    const seal = r.evidence === 'confirmed' ? '<i class="kind hot">confirmed</i>'
      : (r.evidence === 'contradicted' || r.evidence === 'unsupported')
        ? '<i class="kind">unconfirmed</i>'
        : r.evidence === 'fine-only' ? '<i class="kind">one look</i>'
          : r.evidence === 'deep' ? '<i class="kind hot">deep look</i>' : '';
    d.innerHTML = `<span class="w">${r.kind ? kindTag(r.kind) : ''}${seal}${escapeHtml(r.why)}</span>
      <span class="t">${stem(r.clip)} · ${fmt(r.t)}${r.end ? `–${fmt(r.end)}` : ''}</span>`;
    d.onclick = () => {
      insertShot({ clip: r.clip, start: r.t, end: r.end ?? r.t + 3, why: r.why });
    };
    // Look deeper at this moment, priced on the button (INTAKE M15).
    if (libTab === 'seen' && window.deep) deep.rowButton(d, r.clip, r.t, r.end);
    lib.appendChild(d);
  });
}

/* Audit the claims (HANDOFF roadmap item 1, R10's follow-up). The seen tab's top rows
 * are mostly `unaudited` jumps and falls, and on helmet-cam footage those are often a
 * tilted camera. One priced click spends a 1 s close look on exactly those claims and
 * rebuilds the rank; the price comes with /api/status like the visual pass's, and the
 * button is off when there is nothing left to audit or a pass is already running. */
let auditPending = false;
function paintAudit() {
  const row = $('#auditRow');
  if (!row) return;
  const vz = (S && S.visual) || {};
  const a = vz.audit || { claims: 0, windows: 0, projected_usd: 0 };
  // in view until it has been used once (INTAKE M16), then with the seen tab
  row.style.display = libTab === 'seen' || (a.windows && !auditUsed()) ? 'flex' : 'none';
  const b = $('#auditClaims');
  b.disabled = auditPending || !!vz.running || !a.windows;
  b.textContent = a.windows
    ? `Audit ${a.claims} claim${a.claims === 1 ? '' : 's'} · ${rcCost.tag(a.projected_usd, a)}`
    : 'Audit the claims';
  b.title = a.windows ? rcCost.note(a.projected_usd, a) : '';
  $('#auditInfo').textContent = !a.windows ? 'every hot claim has had a close look'
    : vz.running ? 'a visual pass is running'
    : `${a.windows} close look${a.windows === 1 ? '' : 's'} at 1 s`;
}

/* "Used once" is this viewer's click, remembered per bin (a convenience, not state). */
const auditKey = () => `roughcut.audit.used:${(S && S.footage) || ''}`;
function auditUsed() {
  try { return localStorage.getItem(auditKey()) === '1'; } catch (e) { return false; }
}

async function auditClaims() {
  try { localStorage.setItem(auditKey(), '1'); } catch (e) { /* private mode: stays in view */ }
  auditPending = true;
  paintAudit();
  try {
    const r = await fetch('/api/visual/audit', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const d = await r.json();
    if (!r.ok) { toast(d.detail || 'the audit could not start'); return; }
    // The job is a `visual` job: the top bar shows it and, when it is done, adopt()
    // reloads the project, so the seen tab repaints with the verdicts.
    toast(`auditing ${d.claims} claim${d.claims === 1 ? '' : 's'} — ${rcCost.tag(d.projected_usd, d)}`);
    await refreshStatus();
  } finally {
    auditPending = false;
    paintAudit();
  }
}

/* The bin on the board — Option B's Source panel, first stage, on the board as it is.
 *
 * Karl, 2026-09-08, after using the pass: "what should I expect going from the pass to
 * the cut board here? Cut board looks exactly the same as before." The pass wrote keeps
 * into the EDL's `selects` and the board showed nothing of it — the bin reached the cut
 * only through the Ask's prompt. Now it is the first tab under "Add a moment": one row
 * per keep, a still, the pass's reason and the editor's note, and either where the keep
 * is in the cut or the one click that puts it there.
 *
 * "In the cut" is decided here, against the live timeline, with the same rule the
 * server's `selects.used_in` applies on save (the shot and the keep share half of the
 * shorter one) — so a row flips the moment a keep is added, before the autosave lands,
 * and un-flips the moment its shot is removed. */
function overlapRatio(a, b) {
  const shorter = Math.max(1e-6, Math.min(a[1] - a[0], b[1] - b[0]));
  return Math.max(0, Math.min(a[1], b[1]) - Math.max(a[0], b[0])) / shorter;
}

function shotOf(s) {
  return segs.findIndex((g) => g.clip === s.clip
    && overlapRatio([s.start, s.end], [g.in, g.out]) >= 0.5);
}

/* The keeps the board can act on: footage present, and in the folder this board knows. */
function keepsUsable() {
  return ((bin && bin.selects) || []).filter((s) => !s.missing && P && P.clips[s.clip]);
}

/* The first pass's picks (verdict 4, punch 1): every moment the pass found is a row in
 * Selects and a marker on the cut — not only what a /floor review kept. GET /api/picks
 * builds them from the index on the server; a pick a keep already covers is that keep's
 * row (the keep is yours: your range, your note), and one rejected on /floor is gone.
 * Re-read with the bin (at most every PICKS_TTL_MS: picks change with the index, not
 * with an edit); the lanes redraw when they arrive. They are the first pass's: until a
 * pass has made them (`firstpass_picked`), Selects is the keeps alone, as before. */
let passPicks = null;         // /api/picks' rows, by clip; null before the first read
let picksRead = 0;
let picksBusy = null;
let picksMade = false;        // the last read's `firstpass_picked`
let picksAgain = false;       // a forced read asked for while one was in flight
const PICKS_TTL_MS = 4000;

/* A read that said "no pass has picked yet" is never kept: the pass can land its picks
 * at any moment, and Selects opened a second later said "nothing kept yet" for the
 * TTL (JOURNEY2's flaky `selects` step). A forced read asked for while one is in flight
 * (made before the picks landed) runs again after it. */
function fetchPicks(force) {
  if (picksBusy) {
    if (force) picksAgain = true;
    return picksBusy;
  }
  if (!force && passPicks && picksMade && Date.now() - picksRead < PICKS_TTL_MS) return Promise.resolve(passPicks);
  picksBusy = fetch('/api/picks?order=clip').then((r) => (r.ok ? r.json() : null)).then((d) => {
    if (d && Array.isArray(d.picks)) {
      const was = JSON.stringify((passPicks || []).map((p) => [p.id, p.verdict, p.released]));
      // the picks are the first pass's: until one has made them, Selects is the keeps
      picksMade = !!d.firstpass_picked;
      passPicks = picksMade ? d.picks.map((p) => ({ ...p, pick: true })) : [];
      picksRead = Date.now();
      if (JSON.stringify(passPicks.map((p) => [p.id, p.verdict, p.released])) !== was
          && P && window.tl && tl.state) {
        if (window.tlLanes) tlLanes.redraw();
        if (keepsTab()) renderLibrary();
        else if (window.dock) dock.badge('bin', keepsUsable().concat(picksUsable()).filter((s) => shotOf(s) < 0).length);
      }
    }
    return passPicks;
  }).catch(() => passPicks).finally(() => {
    picksBusy = null;
    if (picksAgain) { picksAgain = false; fetchPicks(true); }
  });
  return picksBusy;
}
/* The first pass has made (or re-made) its picks: re-read them now, whatever the cache
 * says (firstpass.js calls it when a run lands; showProposal may too). */
window.rcPicksStale = () => fetchPicks(true);

/* The pass's picks the board can act on: footage here, not rejected on /floor, and not
 * already one of the editor's keeps. All of them, as the pass counts them — not only the
 * clips /floor's rounds have released (their previews may still be building). */
function picksUsable() {
  if (!passPicks || !P) return [];
  const keeps = (bin && bin.selects) || [];
  return passPicks.filter((p) => P.clips[p.clip]
    && p.verdict !== 'reject' && p.verdict !== 'pick'
    && !keeps.some((k) => k.clip === p.clip && overlapRatio([k.start, k.end], [p.start, p.end]) >= 0.5));
}

function heroesWaiting() {
  return keepsUsable().filter((s) => s.hero && shotOf(s) < 0);
}

/* Bin order: heroes first, then by clip and start — the order the prompt reads them in. */
function binOrder(list) {
  return [...list].sort((a, b) => (b.hero ? 1 : 0) - (a.hero ? 1 : 0)
    || String(a.clip).localeCompare(String(b.clip)) || a.start - b.start);
}

/* The bin's selection and filter (INTAKE M11). A keep is selected by a click on its
 * card; Enter adds it at the playhead, space plays it in the bin's own player. The
 * filter is one chip — hero, a label the pass gave, in the cut, not yet — plus the
 * words typed in the search box, which filter as you type. */
let binSel = null;            // keepKey() of the selected keep
let binChip = null;           // 'hero' | 'in' | 'out' | 'tag:<label>' | null
const keepKey = (s) => (s.id != null ? String(s.id) : `${s.clip}@${s.start}`);

/* The kinds of evidence a keep rests on — heard, seen, felt — from its witnesses; the
 * pass's own labels for a bin whose picks carry no themes yet (Killington's do not). */
function keepKinds(s) {
  const kinds = [];
  for (const w of s.witnesses || []) {
    if (w && w.kind && !kinds.includes(w.kind)) kinds.push(w.kind);
  }
  return kinds;
}

function keepMatches(s, text) {
  // Confirmed junk is out of the default view; the junk chip shows only junk.
  const jstate = junkState(s.clip);
  if (binChip === 'junk') { if (jstate !== 'confirmed' && jstate !== 'proposed') return false; }
  else if (jstate === 'confirmed') return false;
  if (binChip === 'hero' && !s.hero) return false;
  // the tab: 'out' shows only what is not in the cut
  if (libTab === 'out' && !s.missing && P.clips[s.clip] && shotOf(s) >= 0) return false;
  if (binChip && binChip.startsWith('tag:') && !(s.tags || []).includes(binChip.slice(4))) return false;
  if (binChip && binChip.startsWith('kind:') && !keepKinds(s).includes(binChip.slice(5))) return false;
  if (text) {
    const hay = [stem(s.clip), s.why, s.note, keepLabel(s)].concat(s.tags || []).join(' ').toLowerCase();
    if (!hay.includes(text)) return false;
  }
  return true;
}

/* The chips above the grid: the labels the bin actually carries, with counts. */
function paintBinFilter(keeps) {
  const el = $('#binFilter');
  if (!el) return;
  const tags = new Map();
  keeps.forEach((s) => (s.tags || []).forEach((t) => tags.set(t, (tags.get(t) || 0) + 1)));
  const heroes = keeps.filter((s) => s.hero).length;
  const chips = [];
  if (heroes) chips.push(['hero', `★ hero ${heroes}`, 'hero']);
  [...tags.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12)
    .forEach(([t, n]) => chips.push([`tag:${t}`, `${t} ${n}`, '']));
  const kinds = new Map();
  keeps.forEach((s) => keepKinds(s).forEach((k) => kinds.set(k, (kinds.get(k) || 0) + 1)));
  [...kinds.entries()].sort((a, b) => b[1] - a[1])
    .forEach(([k, n]) => chips.push([`kind:${k}`, `${k} ${n}`, '']));
  const nJunk = junkRows().filter((r) => r.state === 'proposed' || r.state === 'confirmed').length;
  if (nJunk) chips.push(['junk', `junk ${nJunk}`, 'junk']);
  el.innerHTML = chips.map(([k, label, cls]) =>
    `<span class="chip ${cls}${binChip === k ? ' on' : ''}" data-chip="${escapeHtml(k)}"
           title="${binChip === k ? 'click to clear the filter' : 'show only these'}">${escapeHtml(label)}</span>`).join('');
  el.hidden = !keeps.length && !nJunk;
}

function selectKeep(s, row) {
  binSel = keepKey(s);
  document.querySelectorAll('#library .keep').forEach((r) => r.classList.toggle('sel', r._keep === s));
  if (row && document.activeElement !== row) row.focus({ preventScroll: true });
}

/* Play a keep where it lives — the bin's player, the whole clip seeked to the keep —
 * so the monitor stays on the cut while you look. */
function playKeep(s) {
  const clip = P.clips[s.clip];
  if (!clip || !clip.proxy) return toast('no proxy for this clip yet');
  showFindMatch({ clip: s.clip, proxy: clip.proxy, start: s.start, end: s.end,
                  what: s.why || s.note || '', duration: clip.duration });
  const fp = $('#findPlayer');
  if (fp) fp.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

/* A keep's label: the note, the first line spoken inside it, what was seen in it. */
function keepLabel(s) {
  if (s.note) return `your note: ${s.note}`;
  // a whole clip of the footage is that clip: three cards all saying "hello there" were
  // three cards nobody could tell apart
  if (s.whole) return `${stem(s.clip)} · ${keepLabel({ ...s, whole: false })}`;
  const clip = (P && P.clips[s.clip]) || {};
  const u = (clip.transcript || []).find((x) => x.end > s.start && x.start < s.end);
  if (u) return `“${u.text}”`;
  const m = ((clip.visual || {}).moments || []).find((x) => x.end > s.start && x.start < s.end && x.what);
  return m ? m.what : 'no words';
}

/* The Selects as a bin (verdict 3, punch 7): a list like Project's — Name, Clip, Media
 * In, Media Out, Duration, Reason, Usage — sorted by a click on a column's head (again:
 * the other way; a third time: the bin's own order), and its rows are clips. Drag one
 * onto the timeline; `,` / `.` insert / overwrite the selected row's range at the
 * playhead (timeline-keys' sourceShot reads `#library .keep.sel`); a double-click opens
 * the pick in the Source monitor with its In and Out set. The columns a narrow panel
 * cannot hold are in the row's hover, as Project's are. The name is the label (Karl's
 * note, else the line spoken in it, else what was seen) — never the detector's title. */
let binSort = { key: null, dir: 1 };
/* Every column but Name is a fixed width, as a Premiere bin's are, and Name takes what is
 * left (verdict 5, punch 7: at 1440 the quote wrapped over five lines while Reason was
 * cut to a dozen characters). Reason is never narrower than 186 px; Media Out (In plus
 * Duration, and in the row's hover) comes in from a 640 px list, Clip from 740. */
const SEL_COLS = [
  { k: 'name', h: 'Name' },
  { k: 'clip', h: 'Clip', w: 64, min: 740 },
  { k: 'in', h: 'Media In', w: 82, min: 440 },
  { k: 'out', h: 'Media Out', w: 82, min: 640 },
  { k: 'dur', h: 'Duration', w: 82 },
  { k: 'why', h: 'Reason', w: 186, min: 560 },
  { k: 'use', h: 'Usage', w: 58 },
];
const selCols = (width) => SEL_COLS.filter((c) => !c.min || width >= c.min);
const keepTc = (t, clip) => (window.tl && tl.tc
  ? tl.tc(Math.max(0, t), Number(((P && P.clips[clip]) || {}).fps) || undefined) : fmt(t));

function keepSortVal(s, k) {
  if (k === 'name') return keepLabel(s).toLowerCase();
  if (k === 'clip') return String(s.clip);
  if (k === 'in') return s.start;
  if (k === 'out') return s.end;
  if (k === 'dur') return s.end - s.start;
  if (k === 'why') return String(s.why || '').toLowerCase();
  const i = s.missing || !P.clips[s.clip] ? -1 : shotOf(s);     // usage: in the cut first
  return i < 0 ? 1e9 : i;
}

function sortKeeps(list) {
  if (!binSort.key) return list;
  const { key, dir } = binSort;
  return [...list].sort((a, b) => {
    const x = keepSortVal(a, key), y = keepSortVal(b, key);
    return (x < y ? -1 : x > y ? 1 : 0) * dir || String(a.clip).localeCompare(String(b.clip)) || a.start - b.start;
  });
}

/* The list's look: Project's `table.plist`, with the keep card's rules undone for a row
 * (they are the grid's — a card border, a 16:9 still) — here, so the rows ship with the
 * list that draws them. */
const SEL_CSS = `
#library table.sel-list { margin-top: 2px; }
#library table.sel-list th[data-sort] { cursor: pointer; user-select: none; }
#library table.sel-list th[data-sort]:hover { color: var(--text); }
#library table.sel-list th.asc::after { content: ' ▴'; } #library table.sel-list th.desc::after { content: ' ▾'; }
#library table.sel-list tr.keep { background: none; border: 0; border-radius: 0; box-shadow: none; overflow: visible; cursor: grab; }
#library table.sel-list tr.keep:hover td { background: #232327; }
#library table.sel-list tr.keep.sel td, #library table.sel-list tr.keep:focus-visible td { background: #26405e; color: #fff; }
#library table.sel-list tr.keep.missing td { opacity: .6; }
#library table.sel-list tr.junkcard td { background: rgba(200, 80, 80, .06); cursor: default; }
#library table.sel-list td { vertical-align: middle; }
#library table.sel-list td.name .n { display: flex; align-items: center; gap: 5px; min-width: 0; }
#library table.sel-list .w { margin: 0; display: block; -webkit-line-clamp: unset; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; font-size: 11px; min-width: 0; flex: 1 1 auto; }
#library table.sel-list .hero { margin: 0; }
#library table.sel-list td.why { color: var(--dim); }
#library table.sel-list td.use { overflow: visible; }
#library table.sel-list .use { margin: 0; font-size: 11px; }
#library table.sel-list a.use { color: var(--text); }
#library table.sel-list a.use:hover { color: #fff; text-decoration: none; }
#library table.sel-list button.use.add { padding: 0 6px; line-height: 15px; }
#library table.sel-list .gone { display: inline; margin: 0; font-size: 11px; }
#library table.sel-list .junkq { margin: 0; flex-wrap: nowrap; }
#library table.sel-list .who { flex: none; font-size: 10px; line-height: 14px; padding: 0 4px; border-radius: 2px;
  color: #e0b04a; border: 1px solid rgba(224, 176, 74, .45); white-space: nowrap; }
#library table.sel-list .who.yours { color: var(--text); background: #2c3a2c; border-color: #4a6a4a; }
`;

/* The list's table, appended to `lib`; returns its body. */
function selTable(lib) {
  if (!$('#sel-list-css')) {
    const st = document.createElement('style');
    st.id = 'sel-list-css';
    st.textContent = SEL_CSS;
    document.head.appendChild(st);
  }
  // a panel resized past a column's width redraws with that column in or out
  if (!lib._ro && 'ResizeObserver' in window) {
    lib._ro = new ResizeObserver(() => {
      if (!lib.querySelector('table.sel-list')) return;
      if (selCols(lib.clientWidth || 300).length !== (lib._cols || []).length) renderLibrary();
    });
    lib._ro.observe(lib);
  }
  const cols = selCols(lib.clientWidth || 300);
  const t = document.createElement('table');
  t.className = 'plist sel-list';
  t.innerHTML = `<colgroup>${cols.map((c) => `<col${c.w ? ` style="width:${c.w}px"` : ''}>`).join('')}</colgroup>
    <thead><tr>${cols.map((c) => `<th data-sort="${c.k}"${binSort.key === c.k ? ` class="${binSort.dir > 0 ? 'asc' : 'desc'}"` : ''}
      title="sort by ${c.h.toLowerCase()}">${c.h}</th>`).join('')}</tr></thead><tbody></tbody>`;
  t.tHead.onclick = (e) => {
    const th = e.target.closest('th[data-sort]');
    if (!th) return;
    const k = th.dataset.sort;
    binSort = binSort.key !== k ? { key: k, dir: 1 } : binSort.dir > 0 ? { key: k, dir: -1 } : { key: null, dir: 1 };
    renderLibrary();
  };
  lib.appendChild(t);
  lib._cols = cols;
  return t.tBodies[0];
}

/* Open a pick in the Source monitor with its In and Out set (Premiere's double-click on
 * a subclip): the dock's source (`dock.source`), its marks this pick's range, parked on
 * the In. `,` / `.` then take that range from the Source monitor. */
function openPickInSource(s) {
  const src = window.dock && dock.source;
  if (!src || !src.v) { playKeep(s); return false; }
  const c = (dock.project.clips().find((x) => x.clip === s.clip)) || P.clips[s.clip];
  if (!c) { toast('this clip is not in the footage folder'); return false; }
  // on the clip's own frames, as a mark made in the Source monitor is (verdict 5, punch 8)
  const on = (t) => (typeof src.snap === 'function' ? src.snap(t, c) : Math.round(t * 1000) / 1000);
  const a = on(s.start), b = on(s.end);
  src.marks[s.clip] = [a, b];
  try { localStorage.setItem('roughcut.ws.marks', JSON.stringify(src.marks)); } catch (e) { /* private mode */ }
  const same = src.clip && src.clip.clip === s.clip;
  src.load(c, {});
  if (same && src.v.readyState >= 1) src.v.currentTime = a;
  src.paint();
  src.publish();
  return true;
}

function keepRow(s, cols) {
  cols = cols || selCols(($('#library') || {}).clientWidth || 300);
  const d = document.createElement('tr');
  d.className = 'keep row' + (s.pick ? ' pick' : s.whole ? '' : ' yours')
    + (s.missing ? ' missing' : '') + (binSel === keepKey(s) ? ' sel' : '');
  d._keep = s;                  // the lanes read the keep off its row, whatever the filter shows
  d.tabIndex = 0;
  const known = !s.missing && !!P.clips[s.clip];
  const at = known && !s.whole ? shotOf(s) : -1;   // a whole clip can be added again
  const use = s.missing
    ? '<span class="gone" title="footage missing — cannot be added until the clip is back">footage missing</span>'
    : !known
      ? '<span class="gone" title="clip not analysed — cannot be added">not analysed</span>'
      : at >= 0
        ? `<a href="#" class="use" data-shot="${at}" title="in the cut · shot ${at + 1} — click to select it on the timeline">shot ${at + 1}</a>`
        : '<button class="use add" title="add it at the playhead · enter · , · or drag it onto the timeline">+ add</button>';
  // the row's hover is the whole of what its one line cuts short: the name, the reason
  d.title = `${stem(s.clip)} ${fmt(s.start)}–${fmt(s.end)}${s.why ? `\n${s.why}` : ''}\n${keepLabel(s)}`
    + (s.pick ? `\nthe first pass's pick · ${s.kind || 'moment'}` : s.whole ? '' : '\nkept by you on the pass')
    + '\ndouble-click: open in the Source monitor with its In and Out';
  // who it is from: the pass's kind on a pick, "yours" on a keep (a whole clip is neither)
  const who = s.pick ? `<span class="who pass" title="the first pass found this — ${escapeHtml(s.kind || 'a moment')}">${escapeHtml(s.kind || 'pick')}</span>`
    : s.whole ? '' : '<span class="who yours" title="you kept this on the pass">yours</span>';
  // The name is one line, as in a Premiere bin (verdict 5, punch 7): the kind chip and
  // the quote, ellipsized; no thumbnail (the list view's, not the icon view's).
  const cell = (k) => {
    if (k === 'name') {
      return `<td class="name"><span class="n">${who}<span class="w${s.note ? ' note' : ''}">${
        s.hero ? '<span class="hero">★</span> ' : ''}${escapeHtml(keepLabel(s))}</span></span></td>`;
    }
    if (k === 'clip') return `<td>${escapeHtml(stem(s.clip))}</td>`;
    if (k === 'in') return `<td class="tc">${keepTc(s.start, s.clip)}</td>`;
    if (k === 'out') return `<td class="tc">${keepTc(s.end, s.clip)}</td>`;
    if (k === 'dur') return `<td class="tc t">${keepTc(s.end - s.start, s.clip)}</td>`;
    if (k === 'why') return `<td class="why">${escapeHtml(s.why || '')}</td>`;
    return `<td class="use">${junkState(s.clip) === 'proposed' ? junkBadge(s.clip) : ''}${use}</td>`;
  };
  d.innerHTML = cols.map((c) => cell(c.k)).join('');
  d.addEventListener('click', (e) => {
    if (e.target.closest('a.use, button.use, .chip, .junkq')) return;
    selectKeep(s, d);
    // Premiere's rule: `,` inserts from the panel in use — the row just chosen, not a
    // clip the Source monitor was showing before (a double-click puts it there instead)
    if (window.rcSource && window.rcSource.clip) { window.rcSource.clip = null; window.rcSource.auto = false; }
  });
  d.addEventListener('dblclick', (e) => {
    if (e.target.closest('a.use, button.use, .chip, .junkq')) return;
    selectKeep(s, d);
    openPickInSource(s);
  });
  const link = d.querySelector('a.use');
  if (link) {
    link.onclick = (e) => {
      e.preventDefault();
      // By id through the timeline, not by nudging the index: a cleared selection
      // leaves the index where it was, and syncSel would see nothing to adopt.
      const id = tl.idAt(Number(link.dataset.shot));
      if (id == null) return;
      tl.select([id]);            // sets sel, emits select → paint() → the inspector
      scrollSel();
    };
  }
  const add = d.querySelector('button.add');
  if (add) add.onclick = () => addKeep(s);
  return d;
}

/* Insert a keep the way the heard/seen rows insert — after the selected shot, with the
 * keep's own range and reason — then straight to disk, so the bin learns the use and
 * the tab re-reads it. */
/* Where a new shot goes — Karl, 2026-09-20: at the playhead. The cut point nearest the
 * playhead, by the lanes' own slot rule, so the bin's button, its Enter key, a Find
 * match, a heard / seen row and a drop all land the same way and make the same one
 * undo entry. Falls back to after the selection when the lanes are not loaded. */
function insertShot(shot) {
  const L = window.tlLanes;
  if (L && window.tl && tl.state) {
    const slot = L.slotAt(tl.state.playhead);
    const id = L.insertAt(shot, slot ? slot.beforeId : null);
    if (id != null) {
      const i = tl.indexOf(id);
      if (i >= 0) { sel = i; tl.select([id]); }
    }
    render();                   // the total, the flow and the bin's "in the cut" follow
    return id;
  }
  pushUndo('insert');
  const at = sel + 1;
  segs.splice(at, 0, { clip: shot.clip, in: shot.start, out: shot.end, why: shot.why || '' });
  sel = at;
  render();
  toast(`added ${stem(shot.clip)} @ ${shot.start.toFixed(1)}s`);
  return null;
}

function addKeep(s) {
  insertShot({ clip: s.clip, start: s.start, end: s.end, why: s.why || s.note || '' });
  // the kept tab is up, so save() re-reads the bin after; on a microtask, after the
  // insert's commit touched the autosave, so this is its one save (wave 5, SAVE)
  queueMicrotask(save);
}

function renderKept(lib) {
  // the editor's keeps and the first pass's picks, one list (verdict 4, punch 1)
  const picked = picksUsable();
  const every = binOrder(((bin && bin.selects) || []).concat(picked));
  const isOut = (s) => s.missing || !P.clips[s.clip] || shotOf(s) < 0;
  const nOut = every.filter((s) => isOut(s) && junkState(s.clip) !== 'confirmed').length;
  const nAll = every.filter((s) => junkState(s.clip) !== 'confirmed').length;
  const tabOut = $('#binTabs .tab[data-tab=out]'), tabAll = $('#binTabs .tab[data-tab=all]');
  if (tabOut) tabOut.textContent = `not in the cut · ${nOut}`;
  if (tabAll) tabAll.textContent = `all ${nAll}`;
  if (window.dock) dock.badge('bin', nOut);          // the rail says what is left to use
  const all = libTab === 'out' ? every.filter(isOut) : every;
  const q = $('#findQ');
  const text = (q ? q.value : '').trim().toLowerCase();
  const keeps = all.filter((s) => keepMatches(s, text));
  paintBinFilter(every);
  // The clips the junk pass has a word on, as cards of their own ahead of the keeps:
  // a black clip rarely has a keep, and a proposal nobody can see is never answered.
  // Proposed ones always; confirmed ones only under the junk chip.
  const jcards = junkRows().filter((r) => (r.state === 'proposed'
    || (binChip === 'junk' && r.state === 'confirmed'))
    && (!text || r.stem.toLowerCase().includes(text)));
  // a count only when some match: "0 of 1 keeps match" over "no keep matches" said the
  // same nothing twice
  $('#libHint').textContent = binChip === 'junk'
    ? 'Junk — proposed by a measurement, yours to confirm. Confirmed clips are out of the Ask, Find and this grid, and the index skips their look.'
    : !all.length || !keeps.length || keeps.length === all.length ? ''
      : `${keeps.length} of ${all.length} ${picked.length ? 'match' : 'keeps match'}`;
  // where the rows come from, and the pass's own view of them one click away (optional)
  if (binChip !== 'junk' && picked.length) {
    const nKeeps = every.length - picked.length;
    const said = $('#libHint').textContent;
    $('#libHint').innerHTML = `${said ? `${escapeHtml(said)} · ` : ''}${picked.length} pick${picked.length === 1 ? '' : 's'} from the first pass`
      + ` · ${nKeeps ? `${nKeeps} kept by you` : 'none kept yet'}`
      + '<a class="floorbtn" href="/floor" title="the pass: each pick played, with keep / reject — optional">review on the pass</a>';
  }
  lib.innerHTML = '';
  // one list: the junk rows first, then the keeps in the bin's order or the column's
  const list = (rows) => {
    const body = selTable(lib);
    jcards.forEach((r) => body.appendChild(junkCard(r, lib._cols)));
    sortKeeps(rows).forEach((s) => body.appendChild(keepRow(s, lib._cols)));
  };
  if (binChip === 'junk') {
    if (!jcards.length && !keeps.length) lib.innerHTML = '<div class="hint">no junk — clear the chip</div>';
    else list(keeps);
    return;
  }
  if (!all.length) {
    if (jcards.length) { list([]); return; }
    if (every.length) {
      lib.innerHTML = '<div class="hint">every keep is in the cut</div>';
      return;
    }
    footageRows(lib, text);
    return;
  }
  if (!keeps.length) {
    // what to clear is what is set: the chip, the words, or both
    const clear = binChip && text ? 'clear the chip or the words above' : binChip ? 'clear the chip' : 'clear the words above';
    if (!jcards.length) lib.innerHTML = `<div class="hint">no keep matches — ${clear}</div>`;
    else list([]);
    return;
  }
  list(keeps);
}

/* Nothing kept yet: the bin is the footage (ROUGHCUT-GOAL.md dimension 2 — the pass and
 * the index make a cut better, never possible). One card per clip, the whole clip, the
 * same row and the same + add / drag as a keep, so a trim after is the only step left. */
function footageRows(lib, text) {
  const clips = Object.entries(P.clips || {})
    .filter(([c, v]) => !isGenClip(c) && v.proxy && junkState(c) !== 'confirmed'
      && (!text || stem(c).toLowerCase().includes(text)))
    .sort(([a], [b]) => a.localeCompare(b));
  // the tab says what the grid holds: the footage, not "not in the cut · 0" over three cards
  const tabOut = $('#binTabs .tab[data-tab=out]');
  if (tabOut) tabOut.textContent = `footage · ${clips.length}`;
  lib.innerHTML = `<div class="hint">nothing kept yet — the footage, whole clips; the first
    pass lists its picks here <a class="floorbtn" href="/floor">keep moments on the pass</a></div>`;
  if (clips.length) {
    const body = selTable(lib);
    sortKeeps(clips.map(([c, v]) => ({ clip: c, start: 0, end: v.duration || 0, why: 'whole clip', whole: true })))
      .forEach((s) => body.appendChild(keepRow(s, lib._cols)));
  }
  if (!clips.length && text) lib.insertAdjacentHTML('beforeend', '<div class="hint">no clip matches — clear the words above</div>');
}

/* Junk (HANDOFF roadmap item 5): the server measures, the editor answers. `junk` is
 * GET /api/junk's rows; a verdict is one POST and a re-read, and the grid repaints. */
let junk = null;

function junkRows() { return (junk && junk.clips) || []; }
function junkState(clip) {
  const r = junkRows().find((x) => x.clip === clip);
  return r ? r.state : 'clean';
}
function junkConfirmed() {
  return new Set(junkRows().filter((r) => r.state === 'confirmed').map((r) => r.clip));
}

async function fetchJunk() {
  try {
    junk = await (await fetch('/api/junk')).json();
  } catch (e) {
    /* a blip keeps the last answer */
  }
}

function junkBadge(clip) {
  const r = junkRows().find((x) => x.clip === clip) || {};
  const why = (r.reasons || []).join(' · ');
  return `<div class="junkq" data-clip="${escapeHtml(clip)}" title="${escapeHtml(why)}">
      <span class="jb">junk?</span>
      <button class="jv" data-verdict="junk" title="confirm: out of the Ask, Find and the grid; the index skips its look">Confirm</button>
      <button class="jv" data-verdict="keep" title="not junk: keep it everywhere">Keep</button></div>`;
}

/* A junk clip's row in the Selects list: its name, its length, the measurement as the
 * reason, and the answer where the usage goes. */
function junkCard(r, cols) {
  cols = cols || selCols(($('#library') || {}).clientWidth || 300);
  const d = document.createElement('tr');
  d.className = `keep row junkcard ${r.state}`;
  d.dataset.clip = r.clip;
  const why = (r.reasons || [])[0] || '';
  d.title = `${r.stem}${why ? `\n${(r.reasons || []).join(' · ')}` : ''}`;
  const answer = r.state === 'proposed' ? junkBadge(r.clip)
    : `<div class="junkq" data-clip="${escapeHtml(r.clip)}"><span class="jb done">junk</span>
         <button class="jv" data-verdict="keep" title="not junk after all">Keep</button></div>`;
  const dur = r.duration != null ? Number(r.duration) : null;
  const cell = (k) => {
    if (k === 'name') {
      return `<td class="name"><span class="n"><span class="w">${escapeHtml(r.stem)}</span></span></td>`;
    }
    if (k === 'clip') return `<td>${escapeHtml(r.stem)}</td>`;
    if (k === 'in') return `<td class="tc">${keepTc(0, r.clip)}</td>`;
    if (k === 'out' || k === 'dur') return `<td class="tc">${dur != null ? keepTc(dur, r.clip) : ''}</td>`;
    if (k === 'why') return `<td class="why">${escapeHtml(why)}</td>`;
    return `<td class="use">${answer}</td>`;
  };
  d.innerHTML = cols.map((c) => cell(c.k)).join('');
  return d;
}

async function junkVerdict(clip, verdict) {
  const r = await fetch('/api/junk', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ clip, verdict }),
  });
  if (!r.ok) return toast(`junk: ${(await r.text()).slice(0, 120)}`);
  const body = await r.json();
  await fetchJunk();
  renderLibrary();
  const skipped = (body.stages || []).length ? ` — the index skips its ${body.stages.join(' and ')}` : '';
  return toast(verdict === 'junk' ? `${stem(clip)} is junk${skipped}`
    : verdict === 'keep' ? `${stem(clip)} kept` : `${stem(clip)}: the proposal stands`);
}

/* The Project panel's one line about the bin, from the server's own summary. */
function paintBinLine() {
  if (window.dock) dock.badge('bin', keepsUsable().concat(picksUsable()).filter((s) => shotOf(s) < 0).length);
  const el = $('#binLine');
  if (!el) return;
  const sm = bin && bin.summary;
  if (!sm) { el.style.display = 'none'; return; }
  el.style.display = '';
  const counts = sm.moments
    ? [`${sm.moments} moment${sm.moments === 1 ? '' : 's'}`,
       `${sm.heroes} hero${sm.heroes === 1 ? '' : 'es'}`,
       `${clock(sm.strung_out_s || 0)} if strung out`]
    : ['nothing kept yet'];
  el.innerHTML = `bin · ${counts.join(' · ')} · <a href="/floor">the pass →</a>`;
}

async function fetchBin() {
  fetchPicks();                     // the pass's picks, alongside: they paint when they land
  try {
    return await (await fetch('/api/selects')).json();
  } catch (e) {
    return null;                    // a blip must not blank the tab it already painted
  }
}

/* Cut from the bin: the same Ask, with a fixed note. The prompt already carries the bin
 * (revise.py's "The editor's selects": heroes fixed, keeps as bounds); this is the
 * button that asks for exactly that, so going from the pass to a cut is one click and
 * not a sentence somebody has to know to type. The usual proposal / accept / discard
 * loop follows. */
const BIN_NOTE = "Build the cut from the editor's selects: every hero must appear, use "
  + 'the other keeps where they serve the story, and take nothing else unless it is '
  + 'needed to make a keep land.';

/* What an Ask costs, on its button before the click (INTAKE M16 I16.0f) — the first
 * cut, Cut from the bin (twice), the Ask panel's Ask and a shot's Ask. GET
 * /api/ask/price is free: the server fits it to the asks this project already paid
 * for, priced for today's model. Fetched at boot and again after every ask (the
 * records it is fitted to just grew). A button whose price has not arrived is
 * disabled — it used to keep its plain name and stay clickable, so a click in the
 * first second (or after a failed fetch, for good) spent with no price shown — and one
 * whose price could not be fetched says so. ask() refuses an unpriced spend too. */
const askPrice = { first: null, bin: null, full: null, shot: null };
const askPriceFailed = { first: false, bin: false, full: false, shot: false };
const priced = (mode) => !!(askPrice[mode] && typeof askPrice[mode].usd === 'number');
const priceTag = (mode) => (priced(mode) ? ` · ${rcCost.tag(askPrice[mode].usd, askPrice[mode])}`
  : askPriceFailed[mode] ? ' · price unavailable' : '');
/* For a button built in a template: disabled, and marked as held for its price only. */
const unpricedAttr = (mode) => (priced(mode) ? '' : ' disabled data-await-price="1"');

async function fetchAskPrices() {
  await Promise.all(Object.keys(askPrice).map(async (mode) => {
    try {
      const r = await fetch(`/api/ask/price?mode=${mode}`);
      if (r.ok) { askPrice[mode] = await r.json(); askPriceFailed[mode] = false; return; }
    } catch (e) { /* said on the button below */ }
    if (!priced(mode)) askPriceFailed[mode] = true;
  }));
  paintAskPrices();
}

/* Held for its price: disabled while unpriced, and enabled again once priced only if it
 * was this that disabled it — a button disabled for its own reason (an ask running)
 * keeps that. */
function holdForPrice(el, mode) {
  if (!priced(mode)) {
    if (!el.disabled) { el.disabled = true; el.dataset.awaitPrice = '1'; }
  } else if (el.dataset.awaitPrice) {
    delete el.dataset.awaitPrice;
    el.disabled = false;
  }
}

function paintAskPrices() {
  const label = (el, name, mode, basis) => {
    if (!el) return;
    el.textContent = name + priceTag(mode);
    if (basis && priced(mode)) el.title = `${rcCost.note(askPrice[mode].usd, askPrice[mode])} — ${askPrice[mode].basis}`;
  };
  label($('#ask'), 'Ask for a change', 'full', true);
  label($('#firstCut'), firstLabel(), firstMode(), true);
  document.querySelectorAll('#inspector button[data-act=shotgo]').forEach((b) => label(b, 'Ask', 'shot', true));
  document.querySelectorAll('#inspector button[data-act=ask]').forEach((b) => label(b, 'Ask about this shot', 'shot', false));
  for (const [sel, mode] of [['#ask', 'full'], ['#firstCut', firstMode()]]) {
    const el = $(sel);
    if (el) holdForPrice(el, mode);
  }
  document.querySelectorAll('#inspector button[data-act=shotgo]').forEach((b) => holdForPrice(b, 'shot'));
  paintCutFromBin();                    // its keeps and its price, together
}

function cutFromBin(opts = {}) {
  if (!keepsUsable().length) return toast('nothing kept yet — the pass is where you keep things');
  return ask({ note: BIN_NOTE, fixed: true, button: opts.button, state: opts.state });
}

/* The empty state's one button follows the bin: its words ("from your 3 keeps") and
 * its price (Cut from the bin, or a first cut from the index) change with the keeps. */
function paintCutFromBin() {
  const b = $('#firstCut');
  if (!b) return;
  const mode = firstMode();
  b.textContent = firstLabel() + priceTag(mode);
  holdForPrice(b, mode);
}

/* Re-read the bin — when the tab is shown, after an insert, after a save while the tab
 * is up — and repaint everything that reads it. Never a full render(): that rebuilds
 * the cards and would steal the focus from a `why` somebody is typing in. */
async function refreshBin() {
  const fresh = await fetchBin();
  if (fresh) bin = fresh;
  await fetchJunk();
  paintBinLine();
  flowSoon();
  renderLibrary();
  paintCutFromBin();
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
    // off / a little / a lot are 0 / 6 / 12 dB; a depth saved as anything else keeps
    // its own entry rather than being rounded on the next save
    const db = music.duck === false ? 0 : (music.duck_db ?? 12);
    const duck = $('#duck');
    if (![...duck.options].some((o) => Number(o.value) === db)) {
      duck.insertAdjacentHTML('beforeend', `<option value="${db}">${db} dB</option>`);
    }
    duck.value = String(db);
    $('#fadeIn').value = music.fade_in ?? 1.5;
    $('#fadeOut').value = music.fade_out ?? 4;
  }
  // One status line (INTAKE M16 I16.4): what the track does under the cut when that is
  // worth knowing — it loops where it is shorter — and nothing when it is not.
  const t = trackInfo();
  const len = total();
  let line = '';
  if (!tracks.length && !music) line = 'No tracks yet — drop an mp3 or wav into assets/music/ and reload.';
  else if (music && t && t.duration_s && t.duration_s < len) {
    const n = Math.ceil(len / t.duration_s) - 1;
    line = `loops ${n === 1 ? 'once' : `${n} times`} at ${clock(t.duration_s)} under a ${clock(len)} cut`;
  }
  $('#musicHint').textContent = line;
  $('#musicHint').hidden = !line;
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
    filmT = filmAt(player.idx, liveVideo().currentTime);
  }
  const seek = () => { el.currentTime = t.duration_s ? filmT % t.duration_s : 0; };
  if (el.readyState >= 1) seek(); else el.addEventListener('loadedmetadata', seek, { once: true });
  bed.gain = 0;
  bed.last = 0;
  el.volume = 0;
  el.play().catch(() => {});
}

/* The flow bar (/flow.js, INTAKE M14) replaced the five-step strip that used to be
 * painted here: it reads the files on the server, so after an edit settles (the
 * autosave has written it) it is asked again rather than left to its own poll. */
let flowT = null;
function flowSoon() {
  clearTimeout(flowT);
  flowT = setTimeout(() => { if (window.flowBar) window.flowBar.poll(); }, 1200);
}

/* The project's status (GET /api/status): what the empty state reads. The header's
 * Project ▾ popover and the model pill are gone (INTAKE M16 I16.1): the project's facts
 * are the switcher's menu (/switcher.js), and the CLI's state is the banner (/cli.js),
 * which shows only when the CLI needs Karl. */
async function refreshStatus() {
  S = await (await fetch('/api/status')).json();
  paintAudit();                        // its price rides on the status, like the pass's
  return S;
}

/* One save in flight (wave 5, lane SAVE). The board used to PUT whenever asked, so an
 * Accept's own save and its commit's autosave 700 ms later could overlap, a second PUT
 * went before the first reply re-keyed the new shots' `tmp-N` ids (the server minted a
 * second id for the same shot: judge 4's trim read "added by hand"), and a PUT landing
 * between them was overwritten. Now a save asked for while one is in flight is folded
 * into ONE follow-up, built after the reply re-keyed (tl.applyIds), carrying the latest
 * cut. Every caller gets the promise of the whole run: it resolves when what was on
 * screen at the call is on disk. The timeline's own PUT (saveSeq) takes the same turn. */
let saveRun = null;               // the run in flight (a promise), or null
let saveAgain = false;            // asked for again while a save was in flight
let saveOk = true;                // the run's last PUT landed

function save() {
  clearTimeout(saveTimer);
  saveTimer = null;
  saveAgain = true;
  if (!saveRun) {
    saveRun = (async () => {
      try {
        while (saveAgain) {
          saveAgain = false;
          saveOk = await tl.putTurn(saveOnce).catch((e) => { console.error('save', e); return false; });
        }
      } finally { saveRun = null; }
      if (!saveOk || saveTimer) return;          // a failure says so; a later edit waits
      // A save can change every shot's resolved colour — a trim re-derives the auto, a
      // nudge is a new LUT — so the inspector's witness and the monitor's LUT refetch.
      refreshColour();
      const t = new Date();
      saveSays('saved', `saved ${t.getHours()}:${String(t.getMinutes()).padStart(2, '0')}`);
      // A save is where the bin learns from the timeline (which keeps became shots, and
      // which shots were placed by hand). While the tab is up, it must show that.
      if (keepsTab()) refreshBin();
    })();
  }
  return saveRun;
}

/* Write what is waiting: the autosave's timer now, else the save in flight. */
async function flushSave() {
  if (saveTimer) await save();
  else if (saveRun) await saveRun;
}

/* The colour block keyed by the ids the server knows (tl.idFor): a per-shot grade set
 * on a shot laid in a moment ago follows the shot's re-key instead of being orphaned. */
function colourIds(block) {
  if (!block) return block;
  const ids = Object.keys(block.shots || {});
  const moved = ids.some((id) => tl.idFor(id) !== id)
    || (block.reference && tl.idFor(block.reference) !== block.reference);
  if (!moved) return block;
  const out = { ...block };
  if (block.shots) {
    out.shots = {};
    for (const id of ids) out.shots[tl.idFor(id)] = block.shots[id];
  }
  if (out.reference) out.reference = tl.idFor(out.reference);
  return out;
}

/* One PUT, in its turn: the body is built now (after the reply before it re-keyed), and
 * new shots go as `tmp-N`; the reply's `ids` names them. True when it landed. */
async function saveOnce() {
  let r = null;
  try {
    r = await fetch('/api/project', {
      method: 'PUT', headers: { 'content-type': 'application/json' },
      // The colour block rides the same body (INTAKE M10): an empty object removes it.
      // The timeline's markers and sequence In / Out ride along (lane KEYS).
      body: JSON.stringify({ segments: tl.forSave(), story: $('#story').value, music,
                             colour: colourIds(colour),
                             markers: tl.markers ? tl.markers() : [],
                             in: tl.state.seqIn ?? null, out: tl.state.seqOut ?? null }),
    });
  } catch (e) { r = null; }
  if (!r || !r.ok) {
    saveSays('failed', 'save failed');
    toast('save failed — the edit is still on screen, do not reload', 8000);
    return false;
  }
  const d = await r.json().catch(() => ({}));
  if (d && d.ids) {
    tl.applyIds(d.ids);
    colour = colourIds(colour);              // the board's own block follows the re-key
  } else if (tl.needsRekey()) {
    // a server without `ids`: read the cut back and re-key by position, as before
    const p = await (await fetch('/api/project')).json();
    tl.afterSave(p.segments);
  }
  return true;
}

async function snap() {
  toast('snapping to speech…');
  const r = await fetch('/api/snap', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ segments: segs }),
  });
  if (!r.ok) return toast('snap failed');
  const data = await r.json();
  pushUndo('snap');
  const before = total();
  segs = data.segments;
  render();
  toast(`snapped: ${fmt(before)} → ${fmt(total())} (undo with ${undoKey()})`, 4000);
}

/* Ask — the interject loop. A revision arrives as a *proposal*: shown as a diff,
 * accepted or discarded, and undoable once accepted. A model edit that applied
 * itself would be exactly the thing that makes an editor stop trusting the tool. */
let pendingPlan = null;

function summarise(list) {
  return list.map((s) => `${s.clip.replace('.MP4', '')} ${s.in.toFixed(1)}-${s.out.toFixed(1)}`);
}

/* The proposal reads as an edit, not a chat (verdict 4, punch 4): the buttons on one row
 * at the top, "Things to check" under them (the notes check's flags, then the checks the
 * model's own notes ask for), the model's notes folded to their first sentence, and the
 * shots as a table — ✓ / ✗, Source In / Out timecode, why — one row per proposed shot,
 * the removed ones struck at the end. The ghost lane under V1 is the same review. */
const PROPOSAL_CSS = `
#proposal .pbtns { display: flex; flex-wrap: nowrap; gap: 5px; align-items: center; position: sticky; top: -6px;
  z-index: 2; background: var(--panel); padding: 4px 0 6px; margin: -4px 0 6px; }
#proposal .pbtns button { flex: none; white-space: nowrap; padding: 3px 8px; }
#proposal .pbtns button[hidden] { display: none; }
#proposal h2 { margin-bottom: 4px; }
#proposal .pchecks { border-left: 2px solid var(--warn); padding: 2px 0 2px 8px; margin: 0 0 8px; }
#proposal .pchecks b { display: block; font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: var(--warn); }
#proposal .pchecks ul { margin: 2px 0 0; padding: 0; list-style: none; }
#proposal .pchecks li { padding: 1px 0; }
#proposal .pchecks li.flag::before { content: '⚠ '; color: var(--warn); }
#proposal .pchecks li.ask::before { content: '• '; color: var(--dim); }
#proposal .pchecks li[data-k] { cursor: pointer; }
#proposal .pchecks li[data-k]:hover { color: #fff; }
#proposal .pline { color: var(--dim); margin-bottom: 4px; }
#proposal details.pnotes { margin-bottom: 6px; }
#proposal details.pnotes > summary { cursor: pointer; color: var(--dim); white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; list-style-position: inside; }
#proposal details.pnotes > summary b { color: var(--text); font-weight: 600; margin-right: 4px; }
#proposal details.pnotes[open] > summary .pprev { display: none; }
#proposal details.pnotes .pall { white-space: pre-line; margin-top: 4px; }
#proposal .pnote { margin-bottom: 6px; white-space: pre-line; }
#proposalDiff .phead { color: var(--dim); margin-bottom: 4px; }
#proposalDiff table.ptable, #proposalDiff .ptable thead, #proposalDiff .ptable tbody { display: block; width: 100%; }
#proposalDiff table.ptable { font-size: 11.5px; }
#proposalDiff .ptable tr { display: grid; grid-template-columns: 25px 21px minmax(0, 1fr) 80px 80px;
  border-bottom: 1px solid #26262a; }
#proposalDiff .ptable thead tr { border-bottom-color: var(--line); }
#proposalDiff .ptable th { text-align: left; font-weight: 500; color: var(--dim); font-size: 10.5px; padding: 2px 3px;
  white-space: nowrap; }
#proposalDiff .ptable td { padding: 3px 3px 1px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#proposalDiff .ptable td.pw { grid-column: 2 / -1; padding: 0 3px 3px; color: #c4c4c9; }
#proposalDiff .ptable td.tc { font-variant-numeric: tabular-nums; color: var(--tc); font-size: 11px; text-overflow: clip; }
#proposalDiff .ptable td.pn { color: var(--dim); font-variant-numeric: tabular-nums; }
#proposalDiff .ptable tr.pshot { cursor: pointer; }
#proposalDiff .ptable tr.pshot:hover td { background: #232327; }
#proposalDiff .ptable tr.pshot.open td.pw { white-space: normal; }
#proposalDiff .ptable tr.pshot.rejected td { opacity: .45; }
#proposalDiff .ptable tr.pshot.rejected td.pc, #proposalDiff .ptable tr.pshot.rejected td.tc { text-decoration: line-through; }
#proposalDiff .ptable tr.pgone td { color: var(--bad); }
#proposalDiff .ptable tr.prej td { color: var(--faint); font-style: italic; }
#proposalDiff .ptable .pv { display: inline-block; width: 18px; height: 17px; line-height: 15px; text-align: center;
  border-radius: 3px; border: 1px solid var(--good); color: var(--good); font-weight: 700; cursor: pointer; user-select: none; }
#proposalDiff .ptable .pv[aria-checked=false] { border-color: var(--bad); color: var(--bad); }
#proposalDiff .ptable .pv:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
#proposalDiff .ptable .pst { font-style: normal; font-weight: 700; margin-right: 4px; }
#proposalDiff .ptable .pst.new { color: var(--good); }
#proposalDiff .ptable .pst.chg { color: var(--warn); }
#proposalDiff .ptable .pwords { display: none; font-style: normal; color: var(--dim); }
#proposalDiff .ptable tr.pshot.open .pwords { display: inline; }
#proposalDiff .ptable .pflag { color: var(--warn); margin-right: 3px; }
`;

function proposalCss() {
  if (document.getElementById('proposal-css')) return;
  const st = document.createElement('style');
  st.id = 'proposal-css';
  st.textContent = PROPOSAL_CSS;
  document.head.appendChild(st);
}

/* While a proposal is up, the Assistant's empty-sequence line ("No cut yet: …") is not
 * true any more — the cut is on the ghost lane — so it goes, whoever hides #proposal
 * (Accept, Discard) brings it back. render() owns the line's `hidden`; this owns its
 * display, so the two never fight. */
function watchProposalUp() {
  const box = $('#proposal');
  if (!box || box.dataset.watched) return;
  box.dataset.watched = '1';
  const sync = () => {
    const up = box.style.display !== 'none';
    const first = $('#askFirst');
    if (first) first.style.display = up ? 'none' : '';
    if (up) $('#lastAsk').style.display = 'none';
  };
  new MutationObserver(sync).observe(box, { attributes: true, attributeFilter: ['style'] });
  sync();
}

/* The model's own "Things to check first: a, b, and whether c." → its items. */
function notesChecks(notes) {
  const m = /things to check(?: first)?\s*:\s*([\s\S]*?)(?:\.(?=\s|$)|$)/i.exec(String(notes || ''));
  if (!m || !m[1].trim()) return [];
  return m[1].split(/,\s*(?:and\s+)?(?=whether\b|if\b)/i).map((x) => x.trim()).filter(Boolean);
}

/* What the notes check flags (`notes_check.flags`), one line per shot, with the
 * proposed shot it is about (k) when there is one. */
function flagChecks(plan) {
  const out = [];
  const seen = new Set();
  for (const f of (plan.notes_check && plan.notes_check.flags) || []) {
    for (const s of f.shots || []) {
      const key = `${s.id}|${s.clip}|${s.pos}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const was = s.was ? `${fmt(s.was.in)}–${fmt(s.was.out)}` : '';
      if (!s.now) {
        out.push({ text: `shot ${s.pos} (${stem(s.clip)} ${was}) is removed — the notes say “${f.claim}”`, k: null });
        continue;
      }
      const k = plan.segments.findIndex((p) => (s.id && p.id === s.id)
        || (p.clip === s.clip && Math.abs(p.in - s.now.in) < 0.02 && Math.abs(p.out - s.now.out) < 0.02));
      out.push({ text: `shot ${s.pos} (${stem(s.clip)}) changes ${was} → ${fmt(s.now.in)}–${fmt(s.now.out)}`
        + ` — the notes say “${f.claim}”`, k: k >= 0 ? k : null });
    }
  }
  return out;
}

/* The ranges the editor rejected before that this proposal touched (lane RERUN:
 * `firstpass.rejected`, also `notes_check.rejected`): left out, kept as it is, or — when
 * nothing else was proposed — still in, flagged. */
function rejectedOf(plan) {
  const fp = (plan.firstpass && plan.firstpass.rejected) || (plan.notes_check && plan.notes_check.rejected) || [];
  return Array.isArray(fp) ? fp.filter((r) => r && r.clip) : [];
}

function rejectedChecks(plan) {
  return rejectedOf(plan).map((r) => {
    const what = `${stem(r.clip)} ${fmt(r.in)}–${fmt(r.out)}`;
    if (r.action === 'flagged') {
      const k = plan.segments.findIndex((p) => p.clip === r.clip && Math.abs(p.in - r.in) < 0.02 && Math.abs(p.out - r.out) < 0.02);
      return { text: `${what} — you rejected this shot before, and it is proposed again`, k: k >= 0 ? k : null };
    }
    return { text: r.action === 'kept as it is'
      ? `${what} — you rejected this change before: left out, the shot stays as it is`
      : `${what} — you rejected this shot before: left out`, k: null };
  });
}

function proposalChecksHtml(plan, notes) {
  const flags = flagChecks(plan).concat(rejectedChecks(plan));
  const asks = notesChecks(notes);
  if (!flags.length && !asks.length) return '';
  const li = (cls, c) => `<li class="${cls}"${c.k != null ? ` data-k="${c.k}"` : ''}>${escapeHtml(c.text)}</li>`;
  return `<b>Things to check</b><ul>${flags.map((c) => li('flag', c)).join('')}${
    asks.map((t) => li('ask', { text: t, k: null })).join('')}</ul>`;
}

/* The model's notes, folded to their first sentence (the whole text one click away). */
function proposalNotesHtml(plan) {
  const parts = [];
  const fpLine = plan.firstpass && plan.firstpass.diff && plan.firstpass.diff.sentence;
  if (fpLine) parts.push(`<div class="fp-sentence pline">First pass: ${escapeHtml(fpLine)}</div>`);
  // A shot-scoped proposal says which shot it is about — the rest of the diff is
  // the untouched film, and without this line it reads as a whole-cut revision.
  if (plan.focus) parts.push(`<div class="pline">[shot ${plan.focus.index + 1} · ${escapeHtml(stem(plan.focus.clip))}]</div>`);
  // the model's words: the server's "Check:" lines are the Things to check above
  const words = String(plan.notes_model || plan.notes || '').split('\n')
    .filter((l) => !/^(Check|Left out): /.test(l.trim())).join('\n').trim();
  if (!words) parts.push('<div class="pnote hint">(no note returned)</div>');
  else {
    const first = words.split(/(?<=[.!?])\s+|\n\s*\n/)[0].trim();   // a sentence, or a paragraph
    parts.push(first === words && words.length <= 160
      ? `<div class="pnote">${escapeHtml(words)}</div>`
      : `<details class="pnotes"><summary><b>Notes</b><span class="pprev">${escapeHtml(first)}</span></summary>`
        + `<div class="pall">${escapeHtml(words)}</div></details>`);
  }
  return parts.join('');
}

/* One row per proposed shot: ✓ / ✗, the number, the clip, Source In / Out at the clip's
 * own rate, and the why (one line; the row's hover and a click have it whole). */
function proposalTableHtml(plan, reviewable) {
  const facts = (k) => (window.tlLanes && tlLanes.shotFacts ? tlLanes.shotFacts(plan, k) : null);
  const compare = segs.length > 0;
  const rows = plan.segments.map((s, i) => {
    const f = facts(i);
    const spd = speedOf(s);
    const status = f && f.status ? f.status : '';
    const hover = `${String(i + 1).padStart(2, '0')} ${stem(s.clip)} ${fmt(s.in)}–${fmt(s.out)}`
      + ` (${tl.dur(s).toFixed(1)}s${spd === 1 ? '' : ` at ${spd}×`})${f && f.words ? ` · ${f.words}` : ''}`
      + (s.why ? `\nwhy: ${s.why}` : '')
      + (s.polished_from ? `\n↳ polished from ${s.polished_from[0].toFixed(2)}–${s.polished_from[1].toFixed(2)}`
        + `${s.polish_why ? `: ${s.polish_why}` : ''}` : '')
      + (f && f.flags.length ? `\n⚠ ${f.flags.join('\n⚠ ')}` : '')
      + '\nclick to play it in the Program monitor';
    // what the shot is to the cut, as the old diff list said it: + new, ± changed (kept
    // shots say nothing); the words ("replaces 0:01.0–0:03.0") when the row is opened
    const mark = status === 'new' ? '+' : status && status !== 'kept' ? '±' : '';
    const tag = compare && f && f.words
      ? `${mark ? `<i class="pst ${status === 'new' ? 'new' : 'chg'}" title="${escapeHtml(f.words)}">${mark}</i>` : ''}`
        + `<i class="pwords">${escapeHtml(f.words)} · </i>` : '';
    return `<tr class="pshot${f && f.flags.length ? ' flagged' : ''}" data-k="${i}" title="${escapeHtml(hover)}">`
      + `<td>${reviewable ? `<span class="pv" role="checkbox" aria-checked="true" tabindex="0" data-k="${i}"`
        + ' title="accept / reject this shot — the ghost lane\'s ✓ ✗ (Enter / ⌫ there)">✓</span>' : ''}</td>`
      + `<td class="pn">${String(i + 1).padStart(2, '0')}</td>`
      + `<td class="pc">${escapeHtml(stem(s.clip))}${spd === 1 ? '' : ` ${spd}×`}</td>`
      + `<td class="tc">${keepTc(s.in, s.clip)}</td><td class="tc">${keepTc(s.out, s.clip)}</td>`
      + `<td class="pw">${f && f.flags.length ? '<span class="pflag">⚠</span>' : ''}${tag}${escapeHtml(s.why || '')}</td></tr>`;
  });
  // the cut's shots the proposal drops, struck, after the proposed ones: the ghost lane's
  // match (by id, else by clip and overlap) when it has drawn this plan, else by range
  const g = window.tlLanes && tlLanes.ghost ? tlLanes.ghost() : null;
  const kept = new Set(summarise(plan.segments));
  const dropped = g && g.plan === plan && Array.isArray(g.removed)
    ? g.removed.map((i) => segs[i]).filter(Boolean)
    : segs.filter((s) => !kept.has(summarise([s])[0]));
  const gone = dropped.map((s) =>
    `<tr class="pgone" title="${escapeHtml(`${stem(s.clip)} ${fmt(s.in)}–${fmt(s.out)} — removed by the proposal`)}">`
    + `<td>−</td><td class="pn"></td><td class="pc">${escapeHtml(stem(s.clip))}</td>`
    + `<td class="tc">${keepTc(s.in, s.clip)}</td><td class="tc">${keepTc(s.out, s.clip)}</td><td class="pw">removed</td></tr>`);
  // what the re-run left out because the editor rejected it before, greyed
  rejectedOf(plan).filter((r) => r.action !== 'flagged').forEach((r) => gone.push(
    `<tr class="prej" title="${escapeHtml(`${stem(r.clip)} ${fmt(r.in)}–${fmt(r.out)} — you rejected this before; the re-run left it out`)}">`
    + `<td>✗</td><td class="pn"></td><td class="pc">${escapeHtml(stem(r.clip))}</td>`
    + `<td class="tc">${keepTc(r.in, r.clip)}</td><td class="tc">${keepTc(r.out, r.clip)}</td>`
    + `<td class="pw">you rejected this${r.action === 'kept as it is' ? ' change — the shot stays as it is' : ' — left out'}</td></tr>`));
  // a row is two lines (a grid): ✓ / ✗ · # · clip · In · Out, and the why under them at
  // the panel's width — one cell of a 6-column row would leave it two words
  return '<table class="ptable">'
    + '<thead><tr><th></th><th>#</th><th>Clip</th><th>In</th><th>Out</th></tr></thead>'
    + `<tbody>${rows.join('')}${gone.join('')}</tbody></table>`;
}

/* Play proposed shot k in the Program monitor (the ghost lane's click). */
function playProposedShot(k) {
  const g = window.tlLanes && tlLanes.ghost ? tlLanes.ghost() : null;
  const item = g && g.ghosts ? g.ghosts[k] : null;
  if (item && tlLanes.playRange) tlLanes.playRange(item.seg, item.start, null);
}

function showProposal(plan) {
  pendingPlan = plan;
  proposalCss();
  watchProposalUp();
  const oldTotal = total();
  const newTotal = plan.segments.reduce((a, s) => a + tl.dur(s), 0);
  const notes = String(plan.notes_model || plan.notes || '');
  const checks = $('#proposalChecks');
  checks.innerHTML = proposalChecksHtml(plan, notes);
  checks.hidden = !checks.innerHTML;
  $('#proposalNotes').innerHTML = proposalNotesHtml(plan);
  // Each shot with the reason it was chosen: the `why` is what you check the
  // reasoning against, and it is the only account of what the agent thinks it saw.
  // A shot whose boundaries were polished says so on its hover — a cut that silently
  // differs from what the model asked for is one you cannot audit. Per shot (verdict 3,
  // punch 7): ✓ / ✗ like the ghost lane's, what the shot is to the cut (kept / replaces
  // / new) and a ⚠ where the notes call a changed shot unchanged (`notes_check`).
  const reviewable = !plan.unchanged && !!(window.tlLanes && tlLanes.setVerdict);
  // The grade the note asked for (INTAKE I10.5), in the server's words — "look: cold at
  // 0.6", "shot 3 · CLIP_07: warmer" — above the shots, because a colour-only proposal
  // is nothing but this line and must not read as "no change".
  const graded = plan.colour && Object.keys(plan.colour).length
    ? `<div class="proposalColour" style="margin-bottom:8px"><span class="hint">colour</span>${
      (plan.colour_lines || ['a change to the grade']).map((l) => `<div style="color:var(--good)">± ${escapeHtml(l)}</div>`).join('')}</div>`
    : '';
  const head = plan.unchanged
    ? `the cut unchanged — ${segs.length} shots ${fmt(oldTotal)}; only the colour changes`
    : `${segs.length} shots ${fmt(oldTotal)} → ${plan.segments.length} shots ${fmt(newTotal)}`;
  $('#proposal').style.display = 'block';
  if (window.tlLanes && tlLanes.redraw) tlLanes.redraw();     // the ghost lane, now
  $('#proposalDiff').innerHTML = `<div class="phead">${head}</div>` + graded
    + (plan.unchanged ? '' : proposalTableHtml(plan, reviewable));
  const toggle = (k) => { if (window.tlLanes) tlLanes.setVerdict(k, !tlLanes.verdict(k)); };
  $('#proposalDiff').onclick = (e) => {
    const b = e.target.closest('.pv');
    if (b) { toggle(Number(b.dataset.k)); return; }
    const row = e.target.closest('tr.pshot');
    if (!row) return;
    row.classList.toggle('open');               // the why, whole
    playProposedShot(Number(row.dataset.k));
  };
  $('#proposalDiff').onkeydown = (e) => {
    const b = e.target.closest && e.target.closest('.pv');
    if (!b || (e.key !== ' ' && e.key !== 'Enter')) return;
    e.preventDefault();
    e.stopPropagation();
    toggle(Number(b.dataset.k));
  };
  checks.onclick = (e) => {
    const li = e.target.closest('li[data-k]');
    if (!li) return;
    const k = Number(li.dataset.k);
    const row = $(`#proposalDiff tr.pshot[data-k="${k}"]`);
    if (row) { row.classList.add('open'); row.scrollIntoView({ block: 'nearest' }); }
    playProposedShot(k);
  };
  paintReview();
  if (!segs.length) syncPlayer();               // a first cut plays from its ghost lane
  if (window.dock) dock.reveal('#proposal');
  if (window.dock) dock.badge('ask', 0);
}

/* The per-shot verdicts, in the panel: each row's ✓ / ✗, and Accept says what it takes
 * ("Accept 2 of 5") with Accept all beside it once a shot is rejected. The ghost lane's
 * ✓ / ✗ and keys fire `tlreview`; this repaints from tlLanes.reviewed(). */
function paintReview() {
  const plan = pendingPlan;
  const acc = $('#acceptProposal'), all = $('#acceptAll');
  const r = plan && window.tlLanes && tlLanes.reviewed ? tlLanes.reviewed(plan) : null;
  const rej = new Set((r && r.rejectedShots) || []);
  document.querySelectorAll('#proposalDiff .pshot').forEach((el) => {
    const no = rej.has(Number(el.dataset.k));
    el.classList.toggle('rejected', no);
    const b = el.querySelector('.pv');
    if (b) { b.textContent = no ? '✗' : '✓'; b.setAttribute('aria-checked', no ? 'false' : 'true'); }
  });
  const partial = !!(r && r.rejected);
  if (acc) {
    acc.textContent = partial ? `Accept ${r.accepted} of ${r.total}` : 'Accept';
    acc.title = partial ? 'apply the shots you accepted, keep the cut as it is where you rejected one — one undo' : '';
  }
  if (all) all.hidden = !partial;
}
window.addEventListener('tlreview', paintReview);
/* The first pass's hook (/modules/firstpass.js): its result is an ordinary proposal. */
window.rcShowProposal = (plan) => { if (plan && Array.isArray(plan.segments)) showProposal(plan); };

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
      if (s.state === 'estimating') {
        stateEl.textContent = 'sizing the job…';
        return;
      }
      if (s.state === 'running') {
        // The top bar carries the bar, the percentage and the ETA; this line stays
        // next to the button that started it and says which shot it is on.
        stateEl.textContent = s.detail
          ? `${s.detail} · ${clock(s.elapsed_s)}` : `${verb}… ${clock(s.elapsed_s)}`;
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
  // Only while it still waits (I16.0 l): answered, or older than the cut on disk, it is
  // not a proposal any more — "last proposal — 21 shots, 40 days ago" was offered forever.
  // A waiting one opens as it was left (verdict 4, punch 4): the ghost lane under V1 and
  // the review in the Assistant — not a "show it" link to the proposal you came back for.
  const { record, pending } = await (await fetch('/api/asks/latest')).json();
  if (!record || !pending || !record.plan || !Array.isArray(record.plan.segments)) return;
  if (pendingPlan) return;                        // an Ask that landed first is up already
  showProposal(record.plan);
}

/* A four-minute Ask outlives a reload, and the job lives on the server, so the page
 * picks it back up rather than leaving it to finish unwatched. Karl reloaded mid-ask
 * and the only trace left was the recovery link, after the call had already been
 * spent. */
async function reattachAsk() {
  const running = [...strip.seen.values()].find(
    (j) => j.kind === 'ask' && (j.state === 'running' || j.state === 'estimating'));
  if (!running) return;
  strip.owned.add(running.id);
  $('#ask').disabled = true;
  $('#askState').textContent = 'picking up where you left off…';
  try {
    showProposal(await pollAsk(running.id, 'thinking', $('#askState')));
  } catch (e) {
    $('#askState').textContent = '';
    toast(`the ask that was running failed: ${e.message}`, 6000);
  } finally {
    $('#ask').disabled = false;
    holdForPrice($('#ask'), 'full');          // still never an unpriced spend
  }
}

async function ask(opts = {}) {
  const button = opts.button || $('#ask');
  const stateEl = opts.state || $('#askState');
  const note = opts.note !== undefined ? opts.note : $('#note').value.trim();
  const focus = opts.focus;                 // a shot index: revise that one shot only
  const first = !segs.length;
  if (!note && !first) return toast('type what you want changed first');
  // every spend shows its price first (I16.0f): no price on the button, no call
  const mode = focus !== undefined ? 'shot' : opts.fixed ? 'bin' : first ? 'first' : 'full';
  if (!priced(mode)) return toast('this ask has no price yet — not asking');
  // The brief is the human's half of the loop and the most valuable thing typed into
  // this app, so a first-cut note becomes the story rather than being thrown away.
  // A fixed note (Cut from the bin) is the app's words, not theirs, and never does.
  if (first && note && !opts.fixed && !$('#story').value.trim()) $('#story').value = note;
  button.disabled = true;
  const verb = focus !== undefined ? 'revising this shot'
    : opts.fixed ? 'cutting from the bin'
      : first ? 'building a first cut' : 'thinking';
  stateEl.textContent = `${verb}…`;
  try {
    const r = await fetch('/api/ask', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        note, segments: segs, story: $('#story').value,
        ...(focus !== undefined ? { focus } : {}),
      }),
    });
    if (!r.ok) {
      const detail = await r.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${r.status}`);
    }
    // A job, not a two-minute request. The call used to run inside the request, which
    // froze the whole server for its duration and left the plan existing only in that
    // one response — a dropped connection spent the call for nothing.
    const { job } = await r.json();
    strip.owned.add(job);        // this page is following it; the strip must not double up
    const plan = await pollAsk(job, verb, stateEl);
    showProposal(plan);
    // the price was on the button; what it measured is one short line after
    const u = plan.usage || {};
    const cost = typeof u.projected_usd === 'number' ? rcCost.spent(u.projected_usd, askPrice.full) : '';
    stateEl.textContent = cost;
    $('#askState').textContent = cost;
  } catch (e) {
    stateEl.textContent = '';
    toast(`ask failed: ${e.message}`, 6000);
  } finally {
    button.disabled = false;
    holdForPrice(button, mode);
    fetchAskPrices();
  }
}

/* A colour patch over the block the board holds NOW — not the one the Ask saw — so a
 * nudge made while the call ran survives it. The same merge as colour.merge_colour:
 * film keys replaced, a shot's override merged key by key, a null match or balance
 * removing that key. */
function mergeColour(base, patch) {
  const out = JSON.parse(JSON.stringify(base || {}));
  for (const k of ['mode', 'look', 'strength', 'reference']) {
    if (k in patch) out[k] = patch[k];
  }
  for (const [id, o] of Object.entries(patch.shots || {})) {
    const cur = { ...((out.shots || {})[id] || {}) };
    for (const [k, v] of Object.entries(o)) {
      if (v === null && (k === 'match' || k === 'balance')) delete cur[k];
      else cur[k] = v;
    }
    out.shots = out.shots || {};
    out.shots[id] = cur;
  }
  return out;
}

/* Accept: the shots the editor accepted (all of them unless one was rejected on the
 * ghost lane or in the panel — verdict 3, punch 7); `acceptProposal(true)` is Accept
 * all. A rejected shot leaves the cut as it is there: a new shot is not added, a
 * changed one stays as it was, a removed one stays (tlLanes.reviewed). */
function acceptProposal(everything) {
  if (!pendingPlan) return;
  const graded = !!(pendingPlan.colour && Object.keys(pendingPlan.colour).length);
  const cutToo = !pendingPlan.unchanged;
  const r = everything !== true && cutToo && window.tlLanes && tlLanes.reviewed
    ? tlLanes.reviewed(pendingPlan) : null;
  const partial = !!(r && r.rejected);
  // Segments and colour in the one save below (INTAKE I10.5), and in one undo entry:
  // the colour block rides every entry beside the cut (tl's `extra` hook, INTAKE M16
  // I16.4), so ⌘Z takes back what the proposal changed — the cut, the grade, or both.
  if (cutToo || graded) pushUndo('proposal');
  segs = (partial ? r.segments : pendingPlan.segments).map((s) => ({ ...s }));
  if (graded) {
    colour = mergeColour(colour, pendingPlan.colour);
    tidyColour();
  }
  pendingPlan = null;
  $('#proposal').style.display = 'none';
  $('#lastAsk').style.display = 'none';
  if (window.dock) dock.badge('ask', 0);
  render();
  // Straight to disk (a 16-shot cut is not "in progress"), as ONE save: pushUndo's commit
  // runs on the microtask queued above and touches the 700 ms autosave; this microtask
  // runs after it and saves now, clearing that timer (wave 5, SAVE: it used to save twice,
  // and a PUT landing between the two was overwritten). The answer goes once the cut is
  // on disk, so the server reads it against the accepted cut.
  const lanes = window.tlLanes;
  const ranges = lanes && lanes.reviewed && Array.isArray(lanes.reviewed.lastRanges)
    ? lanes.reviewed.lastRanges : null;           // read now: the review the Accept read
  const took = partial ? { accepted: r.accepted, of: r.total, rejected: r.rejectedShots,
                           kept: r.keptRemoved, ...(ranges ? { ranges } : {}) } : null;
  queueMicrotask(() => {
    const answer = () => answerProposal('accept', took);
    save().then(answer, answer);
  });
  toast(partial ? `applied ${r.accepted} of ${r.total} shots${graded ? ' and the colour' : ''} — undo with ${undoKey()}`
    : !graded ? `applied — undo with ${undoKey()}`
      : cutToo ? `applied, cut and colour — ${undoKey()} undoes both`
        : `colour applied — ${undoKey()} undoes it`);
}

/* ▶ Play it (INTAKE M16 I16.4): the proposed cut plays in the monitor from its ghost
 * lane (/timeline-lanes.js), before anything is accepted — watched, not only read. */
function playProposal() {
  if (!pendingPlan) return;
  const lanes = window.tlLanes;
  if (!lanes || !lanes.ghost() || !lanes.playPlan(0)) toast('the proposal is still being drawn — try again');
}

function rejectProposal() {
  pendingPlan = null;
  $('#proposal').style.display = 'none';
  $('#lastAsk').style.display = 'none';      // answered: not offered again
  if (window.dock) dock.badge('ask', 0);
  toast('discarded');
  answerProposal('discard');
}

/* The answer, on the proposal's record (INTAKE M14): an Accept saves the cut, which
 * answers it already; a Discard wrote nothing anywhere, so the flow bar kept calling a
 * thrown-away proposal "waiting for you". */
function answerProposal(answer, partial) {
  // `partial`: which shots an Accept took ({accepted, of, rejected: [k], kept: [ids]}) —
  // on the record for the server to read; it answers the record the same either way.
  // With it go the ranges the editor rejected (verdict 4: a re-run brought one back),
  // from the review the Accept just read (tlLanes.reviewed).
  const lanes = window.tlLanes;
  if (partial && !partial.ranges && lanes && lanes.reviewed && Array.isArray(lanes.reviewed.lastRanges)) {
    partial = { ...partial, ranges: lanes.reviewed.lastRanges };
  }
  fetch('/api/asks/answer', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(partial ? { answer, partial } : { answer }) }).then(flowSoon, () => {});
}

/* Find a moment. Two layers, two prices: the word-level match over the transcripts
 * and the visual sidecars is free and instant; "Ask the model" is one call over the
 * same inventory the Ask reads, offered with its price like the visual pass, for the
 * misses word-matching cannot close — a "river" said as "stream". A match plays the
 * WHOLE clip, seeked to the moment: a window is somewhere to look, not yet a cut. */
let findSel = null;              // the match loaded in the finder's player

/* *Ask the model* carries its price before the click (INTAKE I16.0f), so it stays
 * disabled until the price is on it: the free GET /api/find/price at boot. It used to
 * be priced only by the POST that a click on it had already sent. */
let findPriced = false;
let finding = false;             // a find is in flight (the Find button went: Enter finds)

let findTerms = null;            // the price's `charged`/`backend`, for the after-line
function paintFindDeepPrice(usd, terms) {
  if (typeof usd !== 'number') return;
  findPriced = true;
  findTerms = terms || findTerms;
  $('#findDeep').textContent = `Not it? Ask the model · ${rcCost.tag(usd, findTerms)}`;
  $('#findDeep').title = rcCost.note(usd, findTerms);
}

async function fetchFindPrice() {
  const b = $('#findDeep');
  let d = null;
  try {
    const r = await fetch('/api/find/price');
    if (r.ok) d = await r.json();
  } catch (e) { /* said below */ }
  if (d && typeof d.usd === 'number') {
    paintFindDeepPrice(d.usd, d);
    if (!finding) b.disabled = false;     // not mid-search
  } else if (!findPriced) {
    b.disabled = true;
    b.textContent = d ? 'Not it? Ask the model' : 'Not it? Ask the model · price unavailable';
    if (d && d.why) b.title = d.why;
  }
}

function renderFindResults(rows, note) {
  const box = $('#findResults');
  box.innerHTML = '';
  $('#findDeep').hidden = false;          // under any find: the priced search, plain
  if (note) box.insertAdjacentHTML('beforeend', `<div class="hint" style="padding:4px 0">${escapeHtml(note)}</div>`);
  if (!rows.length) {
    box.insertAdjacentHTML('beforeend',
      '<div class="hint">nothing found — try other words, or Ask the model</div>');
    return;
  }
  rows.forEach((m) => {
    const d = document.createElement('div');
    d.className = 'cand';
    // what was said or seen, the picture at the moment and where in its clip it is, and
    // how long — as the keep cards have a picture: the same line found in three clips
    // read as three identical rows. The clip's name and why it matched are the tooltip's
    // (INTAKE M16).
    d.title = `${stem(m.clip)} ${fmt(m.start)}–${fmt(m.end)}${m.why ? ` · ${m.why}` : ''}`;
    const poster = (P.clips[m.clip] || {}).poster;
    const still = poster ? `${poster}${poster.includes('?') ? '&' : '?'}t=${Math.max(0, m.start).toFixed(2)}` : '';
    d.innerHTML = `${still ? `<img class="still" loading="lazy" decoding="async" draggable="false" alt="" src="${escapeHtml(still)}">`
                            : '<div class="still"></div>'}
      <div class="body"><span class="w">${escapeHtml(m.what || '')}</span>
      <span class="t">at ${clock(m.start)} · ${Math.max(0, m.end - m.start).toFixed(1)} s</span></div>`;
    d.onclick = () => showFindMatch(m);
    box.appendChild(d);
  });
}

function showFindMatch(m) {
  findSel = m;
  $('#findPlayer').style.display = 'block';
  const v = $('#findVideo');
  // Same media-fragment trick as the monitor: the in-point rides in the URL so the
  // first request after the moov lands on the moment, not on byte 0.
  if (v.dataset.src !== m.proxy) {
    v.dataset.src = m.proxy;
    v.src = `${m.proxy}#t=${Math.max(0, m.start).toFixed(2)}`;
    v.load();
  }
  const seek = () => { if (v.dataset.src === m.proxy) v.currentTime = m.start; };
  if (v.readyState >= 1) seek();
  else v.addEventListener('loadedmetadata', seek, { once: true });
  v.play().catch(() => {});     // the controls are right there; a refusal costs a click
  $('#findWhat').textContent = [m.what, m.why].filter(Boolean).join(' — ');
  $('#findInfo').textContent = `${stem(m.clip)} · match ${fmt(m.start)}–${fmt(m.end)}`
    + (m.duration ? ` of ${fmt(m.duration)} — the whole clip is loaded, scrub anywhere` : '');
}

function addFindMatch() {
  if (!findSel) return;
  insertShot({
    clip: findSel.clip,
    start: Math.round(findSel.start * 100) / 100,
    end: Math.round(findSel.end * 100) / 100,
    why: findSel.what || `found: ${$('#findQ').value.trim()}`,
  });
}

function followFind(job) {
  const iv = setInterval(async () => {
    let s;
    try { s = await (await fetch(`/api/job/${job}`)).json(); } catch (e) { return; }
    if (s.state === 'running') {
      $('#findState').textContent = `${s.detail || 'searching'} · ${clock(s.elapsed_s)}`;
      return;
    }
    clearInterval(iv);
    finding = false;
    $('#findDeep').disabled = !findPriced;
    if (s.state !== 'done') {
      $('#findState').textContent = '';
      return toast(`model search failed: ${s.detail || 'see the bar above'}`, 6000);
    }
    const found = s.found || { matches: [], notes: '' };
    renderFindResults(found.matches, found.notes);
    const u = found.usage || {};
    $('#findState').textContent = u.model
      ? `${found.matches.length} from the model · ${rcCost.spent(u.projected_usd, findTerms)}`
      : `${found.matches.length} from the model`;
  }, 1000);
}

async function doFind(deep = false) {
  const q = $('#findQ').value.trim();
  if (!q) return toast('describe the moment you are looking for');
  if (deep && !findPriced) return toast('the model search has no price yet — not asking');
  finding = true;
  $('#findDeep').disabled = true;
  $('#findState').textContent = deep ? 'asking the model…' : 'searching…';
  try {
    const r = await fetch('/api/find', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ query: q, deep }),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `HTTP ${r.status}`);
    }
    const data = await r.json();
    paintFindDeepPrice(data.deep_projected_usd, data);
    renderFindResults(data.matches);
    $('#findState').textContent = data.matches.length
      ? `${data.matches.length} match${data.matches.length > 1 ? 'es' : ''} in the words and the frames`
      : 'no word match — the model may still find it';
    if (data.job) {
      strip.owned.add(data.job);    // this page is following it; the strip must not double up
      followFind(data.job);
      return;                       // buttons come back when the job settles
    }
  } catch (e) {
    $('#findState').textContent = '';
    toast(`find failed: ${e.message}`, 6000);
  }
  finding = false;
  $('#findDeep').disabled = !findPriced;
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
    // The server's _same_shots is the same rule: a shot's speed is part of the cut (a 2×
    // shot is a different film), and the header's Download hangs on this answer.
    return v.shots.length === segs.length && v.shots.every((s, i) =>
      s.clip === segs[i].clip && Math.abs(s.in - segs[i].in) < 0.005
      && Math.abs(s.out - segs[i].out) < 0.005 && speedOf(s) === speedOf(segs[i]));
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

function human(bytes) {
  if (!bytes) return '';
  const mb = bytes / (1024 * 1024);
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${Math.round(mb)} MB`;
}

/* What the film tool's player is doing when it is not showing a picture, said under it.
 * Karl: *"they seem to get stuck in this loading forever place"* — a black rectangle
 * with three things behind it: a review copy still encoding, a stalled stream, and a
 * media error. `versionSticky` is what it says when nothing transient is happening. */
let versionSticky = ['', ''];

function versionMsg(slot, text, kind) {
  const el = $(`#state${slot}`);
  if (!el) return;
  el.textContent = text || '';
  el.className = `hint${kind ? ` ${kind}` : ''}`;
}

function versionSettled(slot) {
  versionMsg(slot, versionSticky[0], versionSticky[1]);
}

/* What a player plays: the 720p review copy, never the master. The delivery render Karl
 * watched is 987 MB of 4K at 43.6 Mbps — no browser streams that smoothly off this box.
 * The master stays for Download. */
function reviewSrc(v) {
  return v.review_state === 'building' ? '' : (v.review_state === 'ready' ? v.review_url : v.url);
}

function setSrc(el, src) {
  if ((el.getAttribute('src') || '') === src) return;
  if (src) el.src = src; else el.removeAttribute('src');
  el.load();
}

/* The film tool's one player: this cut's newest film. */
function loadVersion(v) {
  setSrc($('#previewA'), reviewSrc(v));
  versionSticky = v.review_state === 'building'
    ? ['making a copy to play — Download is ready now', '']
    : (v.review_state === 'failed'
      ? [`no copy to play — playing the ${human(v.size)} master, expect it to stutter`, 'warn']
      : ['', '']);
  versionSettled('A');
}

/* "1080p" / "4K" — what a film is, in the words the buttons use. */
function qualityOf(v) {
  if (v.width >= 3840) return '4K';
  if (v.height) return `${v.height}p`;
  return v.profile === 'delivery' ? 'final' : '1080p';
}

/* "Sep 8, 10:05 PM" — the list spans weeks, and a clock time alone (three rows of
 * "02:13 PM") said nothing about which day a film was made. */
function renderWhen(v) {
  return new Date(v.created * 1000).toLocaleString([],
    { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

/* Renders of the same cut, the same shots and the same profile are one film made more
 * than once (Killington had three identical 17-shot previews): one row, with a count.
 * The newest stands for them. A render too old to carry its shot list is never folded —
 * nothing says it is the same. */
function foldRenders(list) {
  const rows = [], seen = new Map();
  list.forEach((v) => {
    const key = v.shots ? JSON.stringify([v.cut || '', v.profile || 'preview', v.shots,
      v.music || '', v.note || '']) : null;
    const row = key && seen.get(key);
    if (row) { row.also.push(v); return; }
    const fresh = { v, also: [] };
    if (key) seen.set(key, fresh);
    rows.push(fresh);
  });
  return rows;
}

/* This cut's newest film, or null: the one the header's Download and the tool's player
 * are about. */
function thisCutsFilm() {
  return segs.length ? renderList.find(isThisCut) || null : null;
}

/* The header's Export (verdict 2 punch 5): it opens the Export dialog (export.js), as
 * Ctrl+M and File › Export… do — Premiere's Export Media, not a download in disguise.
 * The film of this cut, when there is one, is named in its hover; its Download lives in
 * the Export panel and at the end of the dialog. The dot: the cut changed since its
 * last film. */
function paintMakeFilm() {
  const b = $('#makeFilm');
  if (!b) return;
  const v = thisCutsFilm();
  const key = /Mac|iPhone|iPad/.test(navigator.platform || '') ? '⌘M' : 'Ctrl+M';
  b.textContent = 'Export';
  delete b.dataset.download;
  if (v) {
    b.title = `Export… (${key}) · this cut was last exported as ${v.download_name} · ${human(v.size)}`;
  } else {
    b.title = renderList.length ? `Export… (${key}) · the cut changed since its last film`
      : `Export… (${key})`;
    if (renderList.length) b.insertAdjacentHTML('beforeend', '<span class="dot" aria-label="the cut changed">•</span>');
  }
}

function onMakeFilm() {
  if (window.exportDialog) { exportDialog.open(); return; }
  if (window.dock) dock.open('out');
}

/* "~1 min" / "~17 min": Killington's 3:09 cut took about a minute at 1080p and ~17 at
 * 4K on foxtrot — the same rates, scaled to this cut's length. */
function makeTime(ratio) {
  return `~${Math.max(1, Math.round(total() * ratio / 60))} min`;
}

/* The film tool, from the cut and the films on disk. Repainted on every edit so that
 * "this cut" follows the timeline instead of going stale the moment anything is trimmed. */
function paintVersions() {
  paintMakeFilm();
  const what = $('#filmCut');
  if (!what) return;
  what.textContent = `This cut · ${P && P.cut ? P.cut : 'main'} · ${segs.length} shot${segs.length === 1 ? '' : 's'} · ${clock(total())}`;
  const mine = thisCutsFilm();
  const since = $('#filmSince');
  if (mine) since.textContent = `made ${renderWhen(mine)}`;
  else if (renderList.length) since.textContent = `changed since your last film (${renderWhen(renderList[0])})`;
  else since.textContent = '';
  // "this cut hasn't been made yet" — said once, on the line under the cut, when no film is of it
  since.dataset.unmade = mine ? '' : '1';
  if (!$('#render').dataset.busy || $('#render').dataset.busy === 'false') {
    $('#render').textContent = `Quick look · 1080p · ${makeTime(0.32)}`;
  }
  if (!$('#renderFinal').dataset.busy || $('#renderFinal').dataset.busy === 'false') {
    $('#renderFinal').textContent = `Final 4K · ${makeTime(5.4)}`;
  }

  const newest = $('#filmNewest');
  newest.hidden = !mine;
  if (mine) {
    $('#labelA').textContent = [renderWhen(mine), 'this cut', qualityOf(mine), human(mine.size)]
      .filter(Boolean).join(' · ');
    const dl = $('#filmDownload');
    dl.href = mine.download_url;
    dl.title = `${mine.download_name} · ${human(mine.size)}`;
    dl.textContent = `↓ Download · ${human(mine.size)}`;
    if (newest.dataset.name !== mine.name || newest.dataset.review !== mine.review_state) {
      newest.dataset.name = mine.name;
      newest.dataset.review = mine.review_state;
      loadVersion(mine);
    }
  } else {
    newest.dataset.name = '';
  }

  // every other film, dated, the identical ones folded together
  const rows = foldRenders(renderList.filter((v) => v !== mine));
  const older = $('#olderFilms');
  older.hidden = !rows.length;
  older.querySelector('summary').textContent = `Older films (${rows.length})`;
  const box = $('#versions');
  box.innerHTML = '';
  rows.forEach(({ v, also }) => {
    const row = document.createElement('div');
    row.className = 'ver';
    // Size and resolution on the row, because the link next to them starts a download
    // and 987 MB is worth knowing about before it begins.
    const heft = [human(v.size), v.width ? `${v.width}x${v.height}` : ''].filter(Boolean).join(' · ');
    const building = v.review_state === 'building' ? ' · copy building…' : '';
    const times = also.length ? ` · ×${also.length + 1}` : '';
    row.innerHTML = `<span class="t">${escapeHtml(versionLabel(v))}
      <span class="hint">· ${escapeHtml(renderWhen(v))}${v.cut ? ` · ${escapeHtml(v.cut)}` : ''}${times}${heft ? ` · ${heft}` : ''}${building}</span></span>`;
    if (also.length) {
      row.title = `made ${also.length + 1} times, the same cut and shots — the newest plays; `
        + `also ${also.map(renderWhen).join(', ')}`;
    }
    // A plain link, so the browser's own download machinery handles it; the server
    // sends it as an attachment with a filename worth having.
    const dl = document.createElement('a');
    dl.className = 'dl';
    dl.href = v.download_url;
    dl.textContent = '↓';
    dl.title = `${v.download_name} · ${human(v.size)}`;
    dl.setAttribute('aria-label', 'download');
    row.appendChild(dl);
    box.appendChild(row);
  });
  paintCompare();
}

/* Compare two (inside Older films): any two films side by side — the newest and the one
 * before it to start with. The players load only once the fold is open. */
function paintCompare() {
  const opts = renderList.map((v, k) =>
    `<option value="${k}">${escapeHtml(`${renderWhen(v)} · ${versionLabel(v)}`)}</option>`).join('');
  [['#cmpPickA', 0], ['#cmpPickB', 1]].forEach(([sel, dflt]) => {
    const el = $(sel);
    const keep = el.value;
    el.innerHTML = opts;
    el.value = keep !== '' && Number(keep) < renderList.length ? keep : String(Math.min(dflt, renderList.length - 1));
  });
  if ($('#compare').open) loadCompare();
}

function loadCompare() {
  [['#cmpPickA', '#cmpA'], ['#cmpPickB', '#cmpB']].forEach(([pick, vid]) => {
    const v = renderList[Number($(pick).value)];
    if (v) setSrc($(vid), reviewSrc(v));
  });
}

async function refreshVersions() {
  const { renders } = await (await fetch('/api/renders')).json();
  nRenders = renders.length;
  renderList = renders;
  paintVersions();
  flowSoon();
  waitForReviews(renders);
}

/* A render made here derives its copy inside its own job, so by the time it reports
 * done the copy is ready. This is for the ones that did not: made before the copies
 * existed, or left half-done by a restart, they are built lazily off the versions
 * list — ~40 s for a 1080p master and ~95 s for a 4K one — so the list has to come
 * back and pick them up rather than leaving a slot empty until somebody reloads. */
let reviewPoll = 0;
function waitForReviews(renders) {
  const building = renders.some((v) => v.review_state === 'building');
  if (!building || reviewPoll) return;
  reviewPoll = setInterval(async () => {
    const { renders: now } = await (await fetch('/api/renders')).json();
    if (now.some((v) => v.review_state === 'building')) {
      renderList = now;
      paintVersions();
      return;
    }
    clearInterval(reviewPoll);
    reviewPoll = 0;
    refreshVersions();
  }, 4000);
}

/* Make the film: Quick look (`preview`, free — Next may press it) or Final 4K
 * (`delivery`, Karl's own click). Its progress is the header's strip; when it lands the
 * player and the header's Download follow. */
async function doRender(profile = 'preview') {
  if ($('#render').disabled) return;
  // Before the request, not after it: the click that matters is the second one, and
  // it happens long before any response comes back.
  renderPending = true;
  setRenderBusy(true, profile);
  let r;
  try {
    r = await fetch('/api/render', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ segments: segs, profile }),
    });
  } finally {
    renderPending = false;
  }
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    toast(body.detail || `render refused (${r.status})`);
    // A 409 means one really is running and the strip keeps the button busy;
    // anything else was this request's fault, so give the button back.
    setRenderBusy(r.status === 409);
    return;
  }
  const { job } = await r.json();
  strip.owned.add(job);
  const poll = setInterval(async () => {
    const st = await (await fetch(`/api/render/${job}`)).json();
    if (st.state === 'running') return;     // the header's strip says how far
    clearInterval(poll);
    setRenderBusy(false);
    if (st.url) {
      await refreshVersions();
      toast('the film is ready');
    } else {
      toast('the film failed — see server log');
    }
  }, 1500);
}

document.addEventListener('keydown', (e) => {
  if (['INPUT', 'TEXTAREA'].includes(e.target.tagName) || e.target.isContentEditable) return;
  const step = e.shiftKey ? 1.0 : 0.25;
  const k = e.key;
  if (k === 'j') { sel = Math.min(segs.length - 1, sel + 1); paint(); scrollSel(); }
  else if (k === 'k') { sel = Math.max(0, sel - 1); paint(); scrollSel(); }
  // no plain `u`: on the pass U is "later" (I16.0 n); undo is ⌘Z, the foundation's
  else if (k === 'x') { pushUndo('remove'); segs.splice(sel, 1); render(); }
  // the trims by key — the strip has no in/out buttons (INTAKE M16 decision 5) — and
  // only on a selected shot: nothing is selected at start
  else if ('[]{}'.includes(k) && (tl.state.anchor == null || !segs[sel])) return;
  else if (k === '[') { pushUndo('trim'); nudge(sel, 'in', -step); render(); }
  else if (k === ']') { pushUndo('trim'); nudge(sel, 'in', step); render(); }
  else if (k === '{') { pushUndo('trim'); nudge(sel, 'out', -step); render(); }
  else if (k === '}') { pushUndo('trim'); nudge(sel, 'out', step); render(); }
  else if (k === ' ') { e.preventDefault(); revealMonitor(); toggleCut(); }
  else if (k === 'Enter') {
    e.preventDefault(); revealMonitor(); playFrom(sel, { single: true });
  }
  else if (k === 'g' || k === 'G') {
    // The grade's before / after (INTAKE M10): the monitor with the LUT, or the camera's picture.
    // the "ungraded" tag on the picture says which (/grade.js)
    const g = gradeApi();
    if (!g) return toast('the grade did not load — /grade.js is missing');
    g.toggle();
  }
  else return;
});

/* Bring the selected shot's block into view — on the page and inside the timeline's
 * own scroll. The inspector sits right under it. */
function scrollSel() {
  const id = tl.idAt(sel);
  const blk = id == null ? null
    : document.querySelector(`#tl .blk[data-id="${CSS.escape(String(id))}"]`);
  if (blk) blk.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
}

/* A poster that 404s while its proxy is still building stays broken until the page is
 * reloaded — the <img> does not retry on its own, any more than the <video> it replaced
 * did. Poll until the server says building is done, then re-point the ones that never
 * loaded. */
function waitForProxies() {
  const iv = setInterval(async () => {
    const p = await (await fetch('/api/project')).json();
    await refreshStatus();          // keeps the count in the panel moving, not just a toast
    if (!p.proxies_ready) return;
    clearInterval(iv);
    P.proxies_ready = true;
    let fixed = 0;
    document.querySelectorAll('#inspector img.poster, #tl .blk img.poster, #tl .blk img.ftile').forEach((img) => {
      const src = img.getAttribute('src');
      if (!src || img.naturalWidth > 0) return;
      img.src = src;                       // same URL, fresh load attempt
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
    // A proxy that is missing or half-built is a wait, not an error (punch 5): the
    // poster at the playhead with "preparing preview…", and the same URL again once the
    // server says it is ready. Never MEDIA_ELEMENT_ERROR.
    v.addEventListener('error', () => {
      if (v === liveVideo()) { pauseCut(); monWait(true); }
      mediaWaitFor(v);
    });
    v.addEventListener('playing', () => { if (v === liveVideo()) { screenMsg(''); monWait(false); } });
    v.addEventListener('loadeddata', () => { if (v === liveVideo()) monWait(false); });
    v.addEventListener('waiting', () => {
      if (v === liveVideo() && player.playing) screenMsg('buffering…');
    });
  });
  $('#playCut').onclick = toggleCut;
  // Makes the refusal message actionable: "click the monitor, then press play again"
  // has to be something a person can do, and a monitor you can click to play is what
  // everyone expects anyway.
  $('#player .screen').onclick = toggleCut;
  // The version players get the same treatment the monitor got: a stall or an error
  // said on the screen. Silence there is what "stuck in this loading forever place"
  // was — a black rectangle with nothing to distinguish encoding, buffering and broken.
  {
    const el = $('#previewA');
    el.addEventListener('error', () => versionMsg('A', mediaErrorText(el), 'bad'));
    el.addEventListener('stalled', () =>
      versionMsg('A', 'the stream stalled — the board may be busy', 'warn'));
    el.addEventListener('waiting', () => versionMsg('A', 'buffering…'));
    el.addEventListener('playing', () => versionSettled('A'));
    el.addEventListener('loadeddata', () => versionSettled('A'));
  }
  bed.el = $('#bed');
  P = await (await fetch('/api/project')).json();
  music = P.music || null;
  colour = P.colour || {};
  await refreshColour();          // the looks and every shot's resolved colour, before the first paint
  await refreshStatus();
  await loadAssets();
  segs = P.segments.map((s) => ({ ...s }));
  // The bin, before the first paint: when the pass has kept things, the library opens
  // on them — that is what going from the pass to the board should look like.
  bin = await fetchBin();
  await fetchJunk();
  $('#story').value = P.story || '';
  document.title = `Roughcut — ${P.title || P.cut}`;
  if (window.dock && dock.setTitles) dock.setTitles(P.title || P.cut);
  // The timeline (INTAKE M9): it reads and mutates `segs` in place, maps its id
  // selection onto `sel`, drives the monitor through cueAt / playFrom, and owns the
  // undo / redo stack that pushUndo() now feeds.
  tl.mount('#tl', {
    segs: () => segs,
    setSegs: (list) => { segs = list; },
    clips: () => P.clips,
    sel: () => sel,
    setSel: (i) => { sel = i; },
    live: () => player.idx,
    cue: cueAt,
    play: (i) => playFrom(i),
    touch,
    render,
    toast,
    // the colour block rides each undo entry, so a nudge is one ⌘Z (INTAKE M16 I16.4)
    extra: () => colour,
    setExtra: (c) => { colour = c && typeof c === 'object' ? c : {}; },
  });
  tl.on('select', (ev) => {
    if (ev.source === 'app') return;          // paint() already ran; it told the module
    paint();                                  // the inspector follows the anchor
  });
  tl.on('change', renderInspector);           // a trim changes the header; an undo the why
  tl.on('playhead', () => { if (!player.playing) restSoon(); });
  programInit();                              // V2 and moved A1 sound on the Program
  $('#inspector').addEventListener('click', onInspectorClick);
  // The Bin's tabs and "more found ▸" work from the first paint: bound after the awaits
  // below, a click in the first second (the board drawn, the jobs and prices still
  // loading) did nothing — measured after test_timeline_lanes, which slows the load.
  const onTab = (e) => {
    const t = e.target.closest('.tab');
    if (!t) return;
    libTab = t.dataset.tab;
    renderLibrary();
    // Verdicts happen elsewhere (the pass, another tab): showing the bin re-reads it.
    if (keepsTab()) refreshBin();
  };
  $('#binTabs').onclick = onTab;
  $('#libTabs').onclick = onTab;
  // more found ▸: the machine's offers and the evidence chips, folded
  $('#moreFound').onclick = () => {
    const more = $('#more');
    more.hidden = !more.hidden;
    $('#moreFound').textContent = more.hidden ? 'more found ▸' : 'more found ▾';
    if (more.hidden && !keepsTab()) { libTab = 'out'; renderLibrary(); }
  };
  // The header's and the panels' buttons work from the first paint, like the tabs above:
  // bound after the awaits below, Make the film / undo / Ask did nothing for as long as
  // the versions, the last proposal and the jobs took to load — 5 s and more on a busy
  // box (the gate's "/ film" state timed out twice at load 22), longer over a network.
  $('#story').oninput = touch;
  $('#undo').onclick = undo;
  $('#render').onclick = () => doRender('preview');
  $('#renderFinal').onclick = () => doRender('delivery');
  $('#makeFilm').onclick = onMakeFilm;
  $('#compare').addEventListener('toggle', () => { if ($('#compare').open) loadCompare(); });
  ['#cmpPickA', '#cmpPickB'].forEach((sel) => { $(sel).onchange = loadCompare; });
  $('#ask').onclick = () => ask();   // not `ask` — a MouseEvent has a `.button` too
  $('#playProposal').onclick = playProposal;
  $('#acceptProposal').onclick = () => acceptProposal();
  $('#acceptAll').onclick = () => acceptProposal(true);
  $('#rejectProposal').onclick = rejectProposal;
  $('#findDeep').onclick = () => doFind(true);
  $('#findQ').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); doFind(); }
  });
  $('#findAdd').onclick = addFindMatch;
  // The music controls too: bound at the end of boot, a track picked in the first second
  // showed in the menu but changed nothing (no bed on A2, nothing saved for the render);
  // the gate's music-lane test caught it 2 runs in 3. `music` is read before the mount.
  $('#musicTrack').onchange = musicChanged;
  $('#duck').onchange = musicChanged;
  $('#fadeIn').onchange = musicChanged;
  $('#fadeOut').onchange = musicChanged;
  render();
  paintBinLine();
  // The FX tool read /api/fx before the cut was here, so no proposal was on a shot of it:
  // none counted on the rail, and one made before the server checked by itself was never
  // checked (INTAKE M16 I16.5). Read again now that the cut is.
  if (window.fx && typeof fx.refresh === 'function') fx.refresh();
  await refreshVersions();
  await offerLastProposal();
  // The job registry is server-side, so a reload lands on whatever is still going —
  // a render and an Ask at once, routinely. Paint them before the first tick so the
  // strip is right on the first frame rather than a second later.
  await pollJobs();
  reattachAsk();
  fetchAskPrices();
  fetchFindPrice();
  setInterval(pollJobs, 1000);
  if (!P.proxies_ready) {
    toast('building proxies in the background — previews appear as they finish', 6000);
    waitForProxies();
  }
  // The same box filters the bin as you type (the kept tab) — Find is one keypress on.
  let filterTimer = null;
  $('#findQ').addEventListener('input', () => {
    if (!keepsTab()) return;
    clearTimeout(filterTimer);
    filterTimer = setTimeout(renderLibrary, 120);
  });
  // A junk answer, on a junk card or on a keep from a proposed clip.
  $('#library').addEventListener('click', (e) => {
    const b = e.target.closest('.junkq button.jv');
    if (!b) return;
    e.stopPropagation();
    junkVerdict(b.closest('.junkq').dataset.clip, b.dataset.verdict);
  });
  // A chip, on the grid or on a card, is the filter; the same chip again clears it.
  document.addEventListener('click', (e) => {
    const c = e.target.closest('#binFilter .chip, #library .keep .chip');
    if (!c) return;
    e.stopPropagation();
    const k = c.dataset.chip;
    binChip = binChip === k ? null : k;
    renderLibrary();
  });
  // Keys inside the bin: enter adds the focused keep at the playhead (or selects its
  // shot when it is already in the cut), space plays it in the bin's player. Stopped
  // here so the board's own enter / space (play the shot, play the cut) stay out of it.
  $('#library').addEventListener('keydown', (e) => {
    const row = e.target.closest('.keep');
    if (!row || !row._keep) return;
    if (e.key === 'Enter') {
      e.preventDefault(); e.stopPropagation();
      const add = row.querySelector('button.add');
      const use = row.querySelector('a.use');
      if (add) addKeep(row._keep); else if (use) use.click();
    } else if (e.key === ' ') {
      e.preventDefault(); e.stopPropagation();
      playKeep(row._keep);
    }
  });
  $('#auditClaims').onclick = auditClaims;
}

/* The switcher's hook (/switcher.js): before the server re-points itself at another
 * cut or another bin, anything the autosave timer is still holding must reach the
 * file it belongs to — otherwise a trim made in the last 700 ms would land in the
 * copy, or in the next bin's cut. The board has no in-place reload; the switcher
 * reloads the page and boot() reads the new cut. */
window.roughcutFlush = flushSave;

/* Re-read the cut from disk and repaint it in place (INTAKE M16, C5) — the timeline, the
 * inspector, the bin, the film tool — keeping the selection and the playhead. For a
 * change made on the server (an effect's edit accepted), which used to reload the page.
 * Anything the autosave still holds is written first, so nothing typed is lost; the
 * undo history is not kept, as a reload did not keep it. */
window.roughcutRefresh = async () => {
  await flushSave();
  const keep = { sel: [...tl.state.sel], anchor: tl.state.anchor, at: tl.state.playhead,
                 index: tl.state.anchor == null ? -1 : tl.indexOf(tl.state.anchor) };
  P = await (await fetch('/api/project')).json();
  segs = P.segments.map((s) => ({ ...s }));
  music = P.music || null;
  colour = P.colour || {};
  // The undo stack holds snapshots of the cut before the server's change: ⌘Z of an
  // earlier trim would save that old cut over the accepted edit (the reload this
  // replaced emptied the stack too).
  tl.clearHistory();
  await refreshColour();
  render();
  const ids = keep.sel.filter((id) => id !== keep.anchor && tl.indexOf(id) >= 0);
  if (keep.anchor != null && tl.indexOf(keep.anchor) >= 0) ids.push(keep.anchor);
  // a shot the change replaced: the one now in its place
  else if (keep.index >= 0 && segs.length) ids.push(tl.idAt(Math.min(keep.index, segs.length - 1)));
  if (ids.length) tl.select(ids);
  tl.setPlayhead(Math.min(keep.at || 0, tl.total()), { reveal: false });
  paintRest();
  await refreshBin();
  await refreshVersions();
  if (window.fx && typeof fx.refresh === 'function') fx.refresh();
};
boot();
