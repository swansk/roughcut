/* Roughcut — effects on the board (INTAKE M12).
 *
 * Karl, 2026-09-20: *"Add call of duty hit markers where my skis are with the sound
 * effect … Human can iterate with the AI to make it better, but AI also tests /
 * verifies that the DoD is complete … options for human to draw references on a
 * keyframe."* The vocabulary is `roughcut/fx.py`'s and the loop is the server's
 * (`/api/fx`); this module is the board's side of it, in four parts:
 *
 *   1. the FX tool in the dock (`#fx`): the selected shot's effects as cards — the
 *      hits with one-frame nudges, the machine's checklist, Preview / Verify / Iterate /
 *      Accept / Discard / Remove — and the design box under them;
 *   2. the monitor overlay (`#fxCanvas`): every effect of the live shot drawn at the
 *      live time from the same JSON the render draws from, and heard;
 *   3. the sketch: a reference drawn on a paused frame, sent with the next Design;
 *   4. live nudging: a hit selected on its card, a click on the paused monitor moves it.
 *
 * Rules that are Karl's: a proposal is never applied by the agent — Accept is the
 * human's click, and nothing here writes the EDL except through the endpoints; nothing
 * sent is in pixels, only fractions of the frame; effects always show (no toggle).
 *
 * Loaded before app.js. The board's globals it reads (`segs`, `player`, `liveVideo`,
 * `playFrom`, `pauseCut`, `toast`, `tl`, `dock`) are reached by name at call time
 * and guarded, so the page is whole without them.
 *
 * API (window.fx):
 *   ready        true once mounted
 *   refresh()    GET /api/fx and repaint (a job finished, a save landed)
 *   badge()      put the shot's count on the rail
 *   state        the module's state (tests read it)
 */
(() => {
  'use strict';

  const $ = (s) => document.querySelector(s);
  const NUDGE_S = 1 / 24;                    // one frame at 24 fps — fx.NUDGE_S
  const POLL_MS = 1000;

  const fmtT = (t) => {
    t = Math.max(0, Number(t) || 0);
    const m = Math.floor(t / 60), s = t - m * 60;
    return `${m}:${s.toFixed(1).padStart(4, '0')}`;
  };
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const stem = (c) => String(c || '').replace(/\.[^.]+$/, '');
  const round4 = (v) => Math.round(v * 10000) / 10000;

  /* ------------------------------------------------------------ the board's globals */
  /* app.js's top-level `let` / `function` bindings share the global lexical scope with
   * this script, so they are reached by name at call time; `typeof` is safe when app.js
   * is not there at all. */
  /* eslint-disable no-undef */
  function TL() { return window.tl || null; }
  function SEGS() {
    try { return typeof segs !== 'undefined' && Array.isArray(segs) ? segs : []; } catch (e) { return []; }
  }
  function PLAYER() { try { return typeof player !== 'undefined' ? player : null; } catch (e) { return null; } }
  function live() {
    try { return typeof liveVideo === 'function' ? (liveVideo() || null) : null; } catch (e) { return null; }
  }
  function say(msg, ms) {
    try { if (typeof toast === 'function') return toast(msg, ms); } catch (e) { /* fall through */ }
    console.log(`fx: ${msg}`);
    return undefined;
  }
  function pause() { try { if (typeof pauseCut === 'function') pauseCut(); } catch (e) { /* fine */ } }
  function playShot(i) {
    try { if (typeof revealMonitor === 'function') revealMonitor(); } catch (e) { /* fine */ }
    try { if (typeof playFrom === 'function') { playFrom(i, { single: true }); return true; } } catch (e) { /* fine */ }
    return false;
  }
  /* eslint-enable no-undef */

  /* ------------------------------------------------------------ state */
  const S = {
    effects: [],             // GET /api/fx — proposed and accepted together
    shot: null,              // the selected shot's id (tl.state.anchor)
    sel: null,               // {id, i}: the hit selected on its card, for live nudging
    note: '',                // the design box
    place: false,
    price: null,             // {usd, frames} for `place` on this shot
    priceFor: null,
    reference: null,         // the sketch's reference, sent with the next Design
    window: null,            // {t0, t1} clip seconds — the human's window on the shot, or null
    peaks: [],               // GET /api/fx/peaks for the shot — the candidate impacts, drawn on the bar
    peaksFor: null,
    drag: null,              // {end: 't0'|'t1'} while a bar handle is dragged
    iter: {},                // id → the iterate box's text
    iterOpen: new Set(),
    jobs: new Map(),         // fx job id → state last seen
    busy: null,              // the running fx job, or null
    ticks: 0,
    sig: '',                 // what the last paint was built from
    sketch: null,            // {strokes, cur, goal} while drawing a reference
    audio: new Map(),        // effect id → {key, el}
    fired: new Set(),        // `${id}:${i}` sounded this pass
    sounded: 0,              // how many times a sound was started (tests read it)
    prevT: null,             // the live clip time at the last frame
    prevV: null,             // the video element it belonged to
  };

  /* ------------------------------------------------------------ the api */
  async function api(method, url, body) {
    const r = await fetch(url, {
      method,
      headers: body !== undefined ? { 'content-type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    let d = null;
    try { d = await r.json(); } catch (e) { d = null; }
    if (!r.ok) throw new Error((d && d.detail) || `${method} ${url}: ${r.status}`);
    return d;
  }

  async function refresh() {
    let d;
    try { d = await api('GET', '/api/fx'); } catch (e) { return; }
    S.effects = Array.isArray(d.effects) ? d.effects : [];
    if (S.sel && !byId(S.sel.id)) S.sel = null;     // the selected hit may be gone with its effect
    syncAudio();
    paint();
    badge();
    draw();
  }

  function byId(id) { return S.effects.find((e) => e.id === id) || null; }
  function shotId() {
    const t = TL();
    return t && t.state && t.state.anchor != null ? String(t.state.anchor) : null;
  }
  function seg(id) { return SEGS().find((s) => String(s.id) === String(id)) || null; }
  function forShot(id) { return id == null ? [] : S.effects.filter((e) => String(e.shot) === String(id)); }
  /* the ones that draw, sound and count: not the removed (kept for Restore) */
  function activeForShot(id) { return forShot(id).filter((e) => e.status !== 'removed'); }
  function shotIndex(id) { return SEGS().findIndex((s) => String(s.id) === String(id)); }

  function badge() {
    if (window.dock && typeof dock.badge === 'function') dock.badge('fx', activeForShot(S.shot).length);
  }

  async function fetchPeaks(id) {
    if (!id) { S.peaks = []; return; }
    S.peaksFor = id;
    try {
      const d = await api('GET', `/api/fx/peaks?shot=${encodeURIComponent(id)}`);
      if (S.peaksFor === id) { S.peaks = Array.isArray(d.peaks) ? d.peaks : []; paint(true); }
    } catch (e) {
      if (S.peaksFor === id) S.peaks = [];
    }
  }

  async function fetchPrice(id) {
    if (!id) { S.price = null; S.peaks = []; return; }
    fetchPeaks(id);
    S.priceFor = id;
    try {
      const d = await api('GET', `/api/fx/price?place=1&shot=${encodeURIComponent(id)}`);
      if (S.priceFor === id) { S.price = d; paint(); }
    } catch (e) {
      if (S.priceFor === id) { S.price = null; paint(); }
    }
  }

  /* ------------------------------------------------------------ the human's edits */
  async function put(id, body) {
    try {
      const n = await api('PUT', `/api/fx/${encodeURIComponent(id)}`, body);
      const k = S.effects.findIndex((x) => x.id === id);
      if (k >= 0) S.effects[k] = n; else S.effects.push(n);
      syncAudio();
      paint();
      draw();
      return n;
    } catch (err) {
      say(err.message);
      return null;
    }
  }

  function nudge(e, i, frames) {
    const events = (e.events || []).map((ev) => ({ ...ev }));
    if (!events[i]) return Promise.resolve(null);
    events[i].t = round4(Math.max(0, events[i].t + frames * NUDGE_S));
    return put(e.id, { events });
  }

  function moveEvent(e, i, x, y) {
    const events = (e.events || []).map((ev) => ({ ...ev }));
    if (!events[i]) return Promise.resolve(null);
    events[i].x = round4(Math.min(1, Math.max(0, x)));
    events[i].y = round4(Math.min(1, Math.max(0, y)));
    return put(e.id, { events });
  }

  /* Select a hit on its card: the monitor parks on its frame, and the next click on
   * the monitor moves it there. The same hit again deselects. */
  function selectEvent(id, i) {
    if (S.sel && S.sel.id === id && S.sel.i === i) { S.sel = null; paint(); return; }
    S.sel = { id, i };
    const e = byId(id), sg = e && seg(e.shot), ev = e && e.events[i];
    const t = TL();
    if (e && sg && ev && t && ev.t >= sg.in && ev.t < sg.out) {
      const start = t.filmStart(sg.id);
      if (start >= 0) t.seek(start + (ev.t - sg.in));
    }
    paint();
  }

  async function preview(e) {
    // an edit proposal previews as a ghost on the timeline (the lanes' showGhost, when
    // the board has it); an effect on an existing shot plays the shot in the monitor
    if (Array.isArray(e.edits) && e.edits.length) {
      try {
        const d = await api('POST', '/api/edits/preview', { ops: e.edits });
        const L = window.tlLanes;
        if (L && typeof L.showGhost === 'function') {
          L.showGhost(d.segments);
          say(`the ghost lane shows the cut with ${e.edits.length} change${e.edits.length === 1 ? '' : 's'} · Accept applies them`);
        } else {
          say(`${e.edits.length} change${e.edits.length === 1 ? '' : 's'}: ${(d.words || []).join(' · ')}`);
        }
      } catch (err) { say(err.message); }
      if (String(e.shot).startsWith('new:')) return;
    }
    const i = shotIndex(e.shot);
    if (i < 0) { say('that shot is not in the cut'); return; }
    S.fired.clear();
    playShot(i);
  }

  async function act(name, e) {
    try {
      if (name === 'preview') preview(e);
      else if (name === 'verify') { await api('POST', '/api/fx/verify', { id: e.id }); await pollJobs(); }
      else if (name === 'iterate') {
        if (S.iterOpen.has(e.id)) S.iterOpen.delete(e.id); else S.iterOpen.add(e.id);
        paint(true);
        const inp = $(`#fx .fxcard[data-id="${e.id}"] .fxiter input`);
        if (inp) inp.focus();
      } else if (name === 'revise') {
        const note = (S.iter[e.id] || '').trim();
        if (!note) { say('say what to change'); return; }
        await api('POST', '/api/fx/revise', { id: e.id, note });
        S.iter[e.id] = '';
        S.iterOpen.delete(e.id);
        paint(true);
        await pollJobs();
      } else if (name === 'accept') {
        await api('POST', '/api/fx/accept', { id: e.id });
        if (window.tlLanes && typeof tlLanes.clearGhost === 'function') tlLanes.clearGhost();
        if (Array.isArray(e.edits) && e.edits.length && typeof location !== 'undefined') {
          // the cut changed under the board: reload it so the timeline reads the new shots
          say(`${e.name}: the cut changed — reloading`);
          setTimeout(() => location.reload(), 600);
          return;
        }
        say(`${e.name}: accepted — in the cut`);
        await refresh();
      } else if (name === 'discard') {
        await api('POST', '/api/fx/discard', { id: e.id });
        if (window.tlLanes && typeof tlLanes.clearGhost === 'function') tlLanes.clearGhost();
        say(`${e.name}: discarded`);
        await refresh();
      } else if (name === 'remove') {
        await api('POST', '/api/fx/remove', { id: e.id });
        say(`${e.name}: out of the cut — Restore puts it back`);
        await refresh();
      } else if (name === 'restore') {
        await api('POST', '/api/fx/restore', { id: e.id });
        say(`${e.name}: back in the cut`);
        await refresh();
      } else if (name === 'revert') {
        await api('POST', '/api/fx/revert', { id: e.id });
        say(`${e.name}: the previous version is back`);
        await refresh();
      }
    } catch (err) {
      say(err.message);
    }
  }

  async function design() {
    const shot = S.shot;
    const note = (S.note || '').trim();
    if (!shot) { say('select a shot on the timeline first'); return; }
    if (!note) { say('say what the effect is'); return; }
    // placing is not optional: without it the anchor is the frame's centre, which is
    // never what anyone asked for (Karl, 2026-09-20). A drawn reference carries the
    // anchors itself and skips the call.
    const body = { shot, note, place: !S.reference };
    if (S.reference) body.reference = S.reference;
    const w = windowFor(shot);
    if (w) body.window = [w.t0, w.t1];
    try {
      await api('POST', '/api/fx/design', body);
    } catch (err) {
      say(err.message);
      return;
    }
    S.reference = null;            // used
    paint(true);
    await pollJobs();
  }

  /* ------------------------------------------------------------ jobs */
  /* The board's strip paints every job from /api/jobs; what it cannot know is what an
   * fx job means for this tool, so this reads the same registry and refetches the
   * effects when one finishes — the reload case included: a job seen finished for the
   * first time is a transition too. */
  async function pollJobs() {
    let jobs;
    try { jobs = (await (await fetch('/api/jobs')).json()).jobs || []; } catch (e) { return; }
    let changed = false, running = null;
    const seen = new Set();
    for (const j of jobs) {
      if (j.kind !== 'fx') continue;
      seen.add(j.id);
      const before = S.jobs.get(j.id);
      S.jobs.set(j.id, j.state);
      if (j.state === 'running' && !running) running = j;
      const finished = j.state === 'done' || j.state === 'failed';
      if (finished && before !== j.state) {
        changed = true;
        if (S.ticks) {
          say(j.state === 'done' ? `${j.label}: ${j.detail || 'done'}`
            : `${j.label} failed — ${j.detail || 'no detail'}`, 6000);
        }
      }
    }
    for (const id of [...S.jobs.keys()]) if (!seen.has(id)) S.jobs.delete(id);
    S.ticks += 1;
    const key = (j) => (j ? `${j.id}:${j.detail || ''}:${j.milestone || ''}` : '');
    const was = key(S.busy);
    S.busy = running;
    if (changed) await refresh();
    else if (key(running) !== was) paint();
  }

  /* ------------------------------------------------------------ the tool */
  function chip(e) {
    return `<span class="fxchip ${esc(e.status)}">${esc(e.status)}</span>`;
  }

  function checkRow(c) {
    const mark = c.ok === true ? '✓' : c.ok === false ? '✗' : '–';
    const cls = c.ok === true ? 'ok' : c.ok === false ? 'bad' : 'skip';
    return `<li class="${cls}"><span class="mark">${mark}</span><span class="lbl">${esc(c.label || c.key)}</span>`
      + (c.detail ? `<span class="hint">${esc(c.detail)}</span>` : '') + '</li>';
  }

  function cardHtml(e) {
    const n = (e.events || []).length;
    const sel = S.sel && S.sel.id === e.id ? S.sel.i : -1;
    const events = (e.events || []).map((ev, i) =>
      `<li class="fxev${i === sel ? ' sel' : ''}" data-i="${i}" title="click: park the monitor on this moment, then click the monitor to move it">`
      + `<button class="nudge" data-d="-1" title="one frame earlier">◀</button>`
      + `<span class="t">${fmtT(ev.t)}</span>`
      + `<span class="xy">x ${Number(ev.x).toFixed(2)} y ${Number(ev.y).toFixed(2)}</span>`
      + (ev.label ? `<span class="hint">${esc(ev.label)}</span>` : '')
      + `<button class="nudge" data-d="1" title="one frame later">▶</button>`
      + `<button class="nudge del" data-act="delevent" title="not one — take it out">✕</button></li>`).join('');
    const v = e.verify;
    const checks = v && Array.isArray(v.checks) && v.checks.length
      ? `<div class="fxverify ${v.ok ? 'ok' : 'bad'}"><div class="fxvhead">${v.ok ? '✓ verified' : '✗ not yet'}`
        + (v.at ? ` <span class="hint">${esc(String(v.at).replace('T', ' ').slice(0, 16))}</span>` : '')
        + `</div><ul class="fxchecks">${v.checks.map(checkRow).join('')}</ul></div>`
      : '';
    const proposed = e.status === 'proposed';
    const removed = e.status === 'removed';
    const prev = Array.isArray(e.previous) ? e.previous.length : 0;
    const btns = removed
      ? `<div class="fxbtns"><span class="hint">out of the cut — kept as it was</span><div class="grow"></div>`
        + `<button data-act="restore" class="primary" title="back into the cut, exactly as it was">Restore</button>`
        + `<button data-act="discard" title="delete it for good, files and all">Delete</button></div>`
      : `<div class="fxbtns">`
        + `<button data-act="preview" title="play this shot in the monitor with the effect drawn and heard">Preview</button>`
        + `<button data-act="verify" title="render a proof of the shot and run the checklist">Verify</button>`
        + `<button data-act="iterate" title="tell the model what to change">Iterate</button>`
        + (proposed
          ? `<button data-act="accept" class="primary" title="${Array.isArray(e.edits) && e.edits.length ? 'applies the changes to the cut, then the effect' : 'into the cut — the render draws it'}">Accept${Array.isArray(e.edits) && e.edits.length ? ` · ${e.edits.length} change${e.edits.length === 1 ? '' : 's'}` : ''}</button>`
            + `<button data-act="discard" title="drop the proposal and its files">Discard</button>`
          : `<button data-act="remove" title="out of the cut — kept, so Restore can put it back">Remove</button>`
            + (prev ? `<button data-act="revert" title="back to the version accepted before this one (${prev} kept)">Revert</button>` : ''))
        + `</div>`;
    const iter = S.iterOpen.has(e.id)
      ? `<div class="fxiter"><input type="text" placeholder="red and bigger · hold it a second longer · only the big one · no sound" value="${esc(S.iter[e.id] || '')}">`
        + `<button data-act="revise" class="primary">Go</button></div>`
      : '';
    const extras = [];
    if (Array.isArray(e.window) && e.window.length === 2) extras.push(`window · ${fmtT(e.window[0])}–${fmtT(e.window[1])}`);
    if (e.reference) extras.push(`reference · ${(e.reference.marks || []).length} marks${e.reference.goal ? ` · ${esc(e.reference.goal)}` : ''}`);
    if (e.proof_url) extras.push(`<a href="${esc(e.proof_url)}" target="_blank" rel="noopener">proof</a>`);
    if (e.strip_url) extras.push(`<a href="${esc(e.strip_url)}" target="_blank" rel="noopener">strip</a>`);
    return `<div class="fxcard ${esc(e.status)}" data-id="${esc(e.id)}">`
      + `<div class="fxhead"><b class="fxname">${esc(e.name || 'effect')}</b>`
      + `<span class="fxn">${n} moment${n === 1 ? '' : 's'}</span>${chip(e)}</div>`
      + (e.note ? `<div class="fxnote">${esc(e.note)}</div>` : '')
      + (e.why ? `<div class="fxwhy hint">${esc(e.why)}</div>` : '')
      + (e.limits ? `<div class="fxlimits">could not: ${esc(e.limits)}</div>` : '')
      + (Array.isArray(e.edit_words) && e.edit_words.length
        ? `<div class="fxedits"><div class="fxeh">changes the cut${e.status === 'proposed' ? ' when accepted' : ''}</div>`
          + `<ul>${e.edit_words.map((w) => `<li>${esc(w)}</li>`).join('')}</ul></div>` : '')
      + (e.applied && Array.isArray(e.applied.words) && e.applied.words.length
        ? `<div class="fxedits applied"><div class="fxeh">changed the cut</div><ul>${e.applied.words.map((w) => `<li>${esc(w)}</li>`).join('')}</ul></div>` : '')
      + `<ul class="fxevents">${events}</ul>`
      + (sel >= 0 ? `<div class="fxpick hint">moment ${sel + 1} selected · click the monitor (paused) to move it there</div>` : '')
      + checks
      + (extras.length ? `<div class="fxextras hint">${extras.join(' · ')}</div>` : '')
      + (e.ref_url ? `<img class="fxrefimg" src="${esc(e.ref_url)}" alt="the reference drawn on the frame" title="the frame you drew on — the marks are the anchors">` : '')
      + btns + iter + `</div>`;
  }

  function designHtml() {
    const busy = S.busy;
    const designing = !!(busy && busy.fx_kind === 'design');
    const price = S.price && typeof S.price.usd === 'number'
      ? ` <span class="hint fxprice">≈ $${S.price.usd.toFixed(2)}${S.price.frames ? ` · ${S.price.frames} frame${S.price.frames === 1 ? '' : 's'}` : ''}</span>`
      : '';
    const ref = S.reference
      ? `<div class="fxref">reference · ${S.reference.marks.length} mark${S.reference.marks.length === 1 ? '' : 's'} at ${fmtT(S.reference.t)}`
        + (S.reference.goal ? ` · <i>${esc(S.reference.goal)}</i>` : '')
        + ` <button class="ghost" data-act="clearref" title="forget the drawing">✕</button></div>`
        + (S.reference.png ? `<img class="fxrefimg" src="${S.reference.png}" alt="the reference" title="the frame with your marks — goes with the next Design">` : '')
      : '';
    const sk = S.sketch ? sketchHtml() : '';
    return `<div class="fxdesign">`
      + `<div class="fxdhead hint">design an effect for this shot</div>`
      + `<textarea id="fxNote" placeholder="hit markers with the tick where my skis hit the rocks · a SEND IT title as we drop in · a slow red vignette when I crash · a whoosh and a flash on the jump · a ring that follows Jason down" rows="3">${esc(S.note)}</textarea>`
      + whereHtml()
      + ref + sk
      + `<div class="fxbtns"><button id="fxSketch"${S.sketch ? ' disabled' : ''} title="pause the monitor and draw on the frame: where the effect goes — the marks become the anchors, no placing call">Draw a reference</button>`
      + `<div class="grow"></div><button id="fxDesign" class="primary"${designing ? ' disabled' : ''} title="${S.reference ? 'one design call; your marks are the anchors' : 'one design call, then a look at a frame around each moment to put the effect on the thing you named'}">${designing ? 'Designing…' : `Design${S.reference ? ' <span class="fxprice">≈ $0.05</span>' : price}`}</button></div>`
      + (busy ? `<div class="fxstate hint">${esc(busy.label)}${busy.detail ? ` — ${esc(busy.detail)}` : ''}</div>` : '')
      + `</div>`;
  }

  /* The window: where in the shot the effect belongs, in clip seconds. Karl's first
   * live effect put markers across a 20 s shot whose rocks were only at the end,
   * because nothing let him say so. Defaults to the whole shot; each end can be set
   * from the playhead while the monitor is parked on the moment. */
  function shotRange(id) {
    const s = seg(id);
    return s ? { t0: Number(s.in), t1: Number(s.out) } : null;
  }
  function windowFor(id) {
    const r = shotRange(id);
    if (!r || !S.window) return null;
    const t0 = Math.max(r.t0, Math.min(r.t1, Number(S.window.t0)));
    const t1 = Math.max(r.t0, Math.min(r.t1, Number(S.window.t1)));
    if (!(t1 > t0)) return null;
    if (Math.abs(t0 - r.t0) < 0.01 && Math.abs(t1 - r.t1) < 0.01) return null;   // the whole shot
    return { t0: round4(t0), t1: round4(t1) };
  }
  function liveClipTime() {
    const p = PLAYER(), v = live();
    if (!p || !v || p.idx == null) return null;
    const s = SEGS()[p.idx];
    if (!s || String(s.id) !== String(S.shot)) return null;
    return v.currentTime;
  }
  function whereHtml() {
    const r = shotRange(S.shot);
    if (!r) return '';
    const w = S.window || r;
    const whole = !windowFor(S.shot);
    return `<div class="fxwhere" title="only between these clip times — set each end from the playhead while the monitor is parked on the moment">`
      + `<span class="hint">where</span>`
      + `<input type="text" id="fxFrom" value="${Number(w.t0).toFixed(2)}" size="7">`
      + `<button class="ghost" id="fxFromHead" title="from the playhead">◀ playhead</button>`
      + `<span class="hint">to</span>`
      + `<input type="text" id="fxTo" value="${Number(w.t1).toFixed(2)}" size="7">`
      + `<button class="ghost" id="fxToHead" title="to the playhead">◀ playhead</button>`
      + `<span class="hint fxwhole">${whole ? 'the whole shot' : `${fmtT(w.t0)}–${fmtT(w.t1)}`}</span>`
      + (whole ? '' : `<button class="ghost" id="fxWholeShot" title="the whole shot again">✕</button>`)
      + `</div>` + barHtml(r, w);
  }

  /* The range bar: the shot from its in to its out, the onset peaks as ticks (the
   * candidate impacts the design starts from), the shot's hits as marks, the window
   * as a band with two handles, the playhead as a line. Dragging a handle parks the
   * monitor on that frame — it never plays — and a click anywhere on the bar parks it
   * too. Karl, 2026-09-20: "It is very hard to select the start / end time frame …
   * the video plays when you click on the clip." */
  const STRIP_N = 8;                        // stills across the bar: the shot as a filmstrip
  function clipPoster(clip) {
    try {
      const c = (typeof P !== 'undefined' && P && P.clips) ? P.clips[clip] : null;   // eslint-disable-line no-undef
      return c && c.poster ? c.poster : null;
    } catch (e) { return null; }
  }

  function barHtml(r, w) {
    const sg = seg(S.shot);
    const span = Math.max(0.001, r.t1 - r.t0);
    const pctN = (t) => Math.max(0, Math.min(100, ((t - r.t0) / span) * 100));
    const pct = (t) => `${pctN(t).toFixed(2)}%`;
    const poster = sg ? clipPoster(sg.clip) : null;
    const strip = poster
      ? `<div class="strip">${Array.from({ length: STRIP_N }, (_, k) => {
          const t = r.t0 + (k + 0.5) * span / STRIP_N;
          return `<img src="${poster}?t=${t.toFixed(2)}" alt="" loading="lazy" decoding="async" draggable="false">`;
        }).join('')}</div>`
      : '';
    const peaks = (S.peaks || []).map((p) =>
      `<i class="pk" style="left:${pct(p.t)};opacity:${(0.35 + 0.65 * (p.strength || 0.5)).toFixed(2)}" title="sharp moment ${fmtT(p.t)}"></i>`).join('');
    const hits = activeForShot(S.shot).flatMap((e) => (e.events || []).map((ev) =>
      `<i class="hit${e.status === 'proposed' ? ' proposed' : ''}" style="left:${pct(ev.t)}" title="${esc(e.name)} · ${fmtT(ev.t)}"></i>`)).join('');
    const t = liveClipTime();
    return `<div class="fxbar" id="fxBar" title="the shot, frame by frame · drag on it to scrub the monitor · drag a handle to set the window">`
      + strip
      + `<div class="dim d0" style="width:${pct(w.t0)}"></div><div class="dim d1" style="left:${pct(w.t1)}"></div>`
      + `<div class="win" style="left:${pct(w.t0)};width:${Math.max(0, pctN(w.t1) - pctN(w.t0)).toFixed(2)}%">`
      + `<b class="h h0" data-end="t0" title="from — drag"><span>${fmtT(w.t0)}</span></b>`
      + `<b class="h h1" data-end="t1" title="to — drag"><span>${fmtT(w.t1)}</span></b></div>`
      + peaks + hits
      + `<i class="ph"${t == null ? ' hidden' : ''} style="left:${pct(t == null ? r.t0 : t)}"><b>${t == null ? '' : fmtT(t)}</b></i>`
      + `<span class="lbl l0">${fmtT(r.t0)}</span><span class="lbl l1">${fmtT(r.t1)}</span></div>`
      + `<div class="fxbarline hint" id="fxBarLine">${barLine()}</div>`;
  }

  /* What the bar and the monitor have to do with each other, in one line. */
  function barLine() {
    const t = liveClipTime();
    if (t != null) return `▮ the monitor is at <b>${fmtT(t)}</b> of this shot · drag on the strip to scrub · <kbd>space</kbd> plays`;
    const p = PLAYER();
    const other = p && p.idx != null ? SEGS()[p.idx] : null;
    return other
      ? `the monitor is on shot ${p.idx + 1} — click the strip to bring it here`
      : `click the strip to park the monitor on this shot`;
  }

  /* The playhead on the bar and the line under it follow the monitor on every frame —
   * a DOM update, never a repaint. */
  function updatePlayheadDom() {
    const bar = $('#fxBar');
    if (!bar) return;
    const r = shotRange(S.shot);
    if (!r) return;
    const t = liveClipTime();
    const ph = bar.querySelector('.ph');
    if (ph) {
      if (t == null) ph.hidden = true;
      else {
        ph.hidden = false;
        const span = Math.max(0.001, r.t1 - r.t0);
        ph.style.left = `${Math.max(0, Math.min(100, ((t - r.t0) / span) * 100)).toFixed(2)}%`;
        const b = ph.querySelector('b');
        if (b) b.textContent = fmtT(t);
      }
    }
    const line = $('#fxBarLine');
    if (line) {
      const html = barLine();
      if (line.dataset.last !== html) { line.innerHTML = html; line.dataset.last = html; }
    }
  }

  function barT(ev) {
    const bar = $('#fxBar'), r = shotRange(S.shot);
    if (!bar || !r) return null;
    const b = bar.getBoundingClientRect();
    const f = Math.max(0, Math.min(1, (ev.clientX - b.left) / Math.max(1, b.width)));
    return round4(r.t0 + f * (r.t1 - r.t0));
  }

  let parkTimer = null;
  function park(t) {
    const tl = TL(), sg = seg(S.shot);
    if (!tl || !sg) return;
    pause();
    const start = tl.filmStart(sg.id);
    if (start >= 0) tl.seek(start + (t - sg.in));
  }
  function parkSoon(t) {
    clearTimeout(parkTimer);
    parkTimer = setTimeout(() => park(t), 90);
  }

  function onBarDown(ev) {
    if (ev.button !== 0) return;
    const h = ev.target.closest('.fxbar .h');
    const t = barT(ev);
    if (t == null) return;
    ev.preventDefault();
    if (h) {
      S.drag = { end: h.dataset.end };
      try { h.setPointerCapture(ev.pointerId); } catch (e) { /* fine */ }
      setWindowEnd(S.drag.end, t);
      parkSoon(t);
      const move = (e2) => {
        if (!S.drag) return;
        const tt = barT(e2);
        if (tt != null) { setWindowEnd(S.drag.end, tt); parkSoon(tt); }
      };
      const up = () => {
        window.removeEventListener('pointermove', move, true);
        window.removeEventListener('pointerup', up, true);
        window.removeEventListener('pointercancel', up, true);
        S.drag = null;
        paint(true);
      };
      window.addEventListener('pointermove', move, true);
      window.addEventListener('pointerup', up, true);
      window.addEventListener('pointercancel', up, true);
      return;
    }
    // the bar body: a scrub — the monitor follows the pointer while it is held
    S.drag = { end: null };
    park(t);
    const move = (e2) => { const tt = barT(e2); if (tt != null) parkSoon(tt); };
    const up = () => {
      window.removeEventListener('pointermove', move, true);
      window.removeEventListener('pointerup', up, true);
      window.removeEventListener('pointercancel', up, true);
      S.drag = null;
      paint(true);
    };
    window.addEventListener('pointermove', move, true);
    window.addEventListener('pointerup', up, true);
    window.addEventListener('pointercancel', up, true);
  }

  /* ------------------------------------------------------------ the timeline band */
  /* The window and the hits, drawn on the timeline itself while the FX tool is open:
   * a translucent band over the film time the window covers, a tick per hit. The
   * timeline's canvas is the positioned box its lanes and drop line live in. */
  function paintBand() {
    const tl = TL();
    const canvas = tl && tl.el && tl.el.canvas;
    let band = document.querySelector('#tl .fx-tlband');
    const open = !!(window.dock && typeof dock.current === 'function' && dock.current() === 'fx');
    const sg = S.shot != null ? seg(S.shot) : null;
    if (!canvas || !open || !sg || typeof tl.timeToX !== 'function' || typeof tl.filmStart !== 'function') {
      if (band) band.remove();
      return;
    }
    const start = tl.filmStart(sg.id);
    if (!(start >= 0)) { if (band) band.remove(); return; }
    const r = shotRange(S.shot);
    const w = windowFor(S.shot) || r;
    if (!band) {
      band = document.createElement('div');
      band.className = 'fx-tlband';
      canvas.appendChild(band);
    }
    const x0 = tl.timeToX(start + (w.t0 - sg.in)), x1 = tl.timeToX(start + (w.t1 - sg.in));
    band.style.left = `${x0}px`;
    band.style.width = `${Math.max(2, x1 - x0)}px`;
    band.classList.toggle('whole', !windowFor(S.shot));
    const ticks = activeForShot(S.shot).flatMap((e) => (e.events || [])
      .filter((ev) => ev.t >= sg.in && ev.t < sg.out)
      .map((ev) => tl.timeToX(start + (ev.t - sg.in)) - x0));
    band.innerHTML = ticks.map((x) => `<i style="left:${x.toFixed(1)}px"></i>`).join('')
      + `<span>${esc(windowFor(S.shot) ? `fx · ${fmtT(w.t0)}–${fmtT(w.t1)}` : 'fx · the whole shot')}</span>`;
  }
  function setWindowEnd(which, value) {
    const r = shotRange(S.shot);
    if (!r) return;
    const cur = S.window || { t0: r.t0, t1: r.t1 };
    const v = Number(value);
    if (!Number.isFinite(v)) return;
    S.window = which === 't0' ? { t0: v, t1: cur.t1 } : { t0: cur.t0, t1: v };
    if (S.drag) updateBarDom(); else paint(true);
  }

  /* The bar, the inputs, the label and the timeline band follow the window without a
   * rebuild — what a drag needs. */
  function updateBarDom() {
    const r = shotRange(S.shot);
    if (!r) return;
    const w = S.window || r;
    const span = Math.max(0.001, r.t1 - r.t0);
    const pct = (t) => Math.max(0, Math.min(100, ((t - r.t0) / span) * 100));
    const win = $('#fxBar .win');
    if (win) {
      win.style.left = `${pct(w.t0).toFixed(2)}%`;
      win.style.width = `${Math.max(0, pct(w.t1) - pct(w.t0)).toFixed(2)}%`;
      const l0 = win.querySelector('.h0 span'), l1 = win.querySelector('.h1 span');
      if (l0) l0.textContent = fmtT(w.t0);
      if (l1) l1.textContent = fmtT(w.t1);
    }
    const d0 = $('#fxBar .d0'), d1 = $('#fxBar .d1');
    if (d0) d0.style.width = `${pct(w.t0).toFixed(2)}%`;
    if (d1) d1.style.left = `${pct(w.t1).toFixed(2)}%`;
    const f = $('#fxFrom'), t = $('#fxTo');
    if (f && document.activeElement !== f) f.value = Number(w.t0).toFixed(2);
    if (t && document.activeElement !== t) t.value = Number(w.t1).toFixed(2);
    const lbl = $('#fx .fxwhole');
    if (lbl) lbl.textContent = windowFor(S.shot) ? `${fmtT(w.t0)}–${fmtT(w.t1)}` : 'the whole shot';
    paintBand();
  }

  function sketchHtml() {
    const n = S.sketch.strokes.length;
    return `<div class="fxsketch">`
      + `<div class="hint">draw on the monitor · <span class="fxstrokes">${n} stroke${n === 1 ? '' : 's'}</span> · <kbd>⌫</kbd> undoes the last · <kbd>esc</kbd> cancels</div>`
      + `<input type="text" id="fxGoal" placeholder="what the marks mean — the skis, the jump, where the title sits" value="${esc(S.sketch.goal || '')}">`
      + `<div class="fxbtns"><button id="fxUse" class="primary"${n ? '' : ' disabled'}>Use it</button><button id="fxCancel">Cancel</button></div>`
      + `</div>`;
  }

  function signature() {
    return JSON.stringify([
      S.shot, S.effects, S.sel, S.price,
      S.reference && [S.reference.t, S.reference.marks.length, S.reference.goal],
      S.window && [S.window.t0, S.window.t1],
      S.peaks.length,
      S.sketch && [S.sketch.strokes.length, S.sketch.goal], [...S.iterOpen], S.place,
      S.busy && [S.busy.id, S.busy.state, S.busy.detail, S.busy.milestone],
    ]);
  }

  /* Rebuild the tool's DOM — only when what it is built from changed (force: always),
   * so a poll never interrupts typing in the design box. */
  function paint(force) {
    const el = $('#fx');
    if (!el) return;
    // While a bar handle is held, nothing rebuilds the tool: the first live version
    // rebuilt it on every move and every parked playhead, which destroyed the handle
    // being dragged after a few pixels (Karl: "doesn't move much after I click on it
    // to drag … playhead moving / jumping clip is glitching the tool"). The bar
    // updates in place (updateBarDom) and the tool repaints on release.
    if (S.drag) return;
    const sig = signature();
    if (!force && sig === S.sig) return;
    S.sig = sig;
    const sg = S.shot != null ? seg(S.shot) : null;
    if (!sg) {
      paintBand();
      el.innerHTML = `<div class="fxtitle">FX</div>`
        + `<div class="hint">select a shot on the timeline — its effects and the design box appear here</div>`
        + (S.effects.length ? `<div class="hint" style="margin-top:6px">${S.effects.length} effect${S.effects.length === 1 ? '' : 's'} in this cut</div>` : '');
      return;
    }
    const list = forShot(S.shot);
    const i = shotIndex(S.shot);
    el.innerHTML = `<div class="fxtitle">FX · shot ${i + 1} · ${esc(stem(sg.clip))}</div>`
      + (list.length ? list.map(cardHtml).join('')
        : `<div class="hint fxempty">no effects on this shot yet</div>`)
      + designHtml();
    paintBand();
  }

  function onToolPointerDown(e) {
    if (e.target.closest('#fxBar')) onBarDown(e);
  }

  function onToolClick(e) {
    if (e.target.closest('#fxBar')) return;           // the bar is pointerdown's
    const btn = e.target.closest('button');
    if (btn) {
      if (btn.id === 'fxDesign') { design(); return; }
      if (btn.id === 'fxSketch') { startSketch(); return; }
      if (btn.id === 'fxUse') { useSketch(); return; }
      if (btn.id === 'fxCancel') { cancelSketch(); return; }
      if (btn.dataset.act === 'clearref') { S.reference = null; paint(); return; }
      if (btn.id === 'fxFromHead' || btn.id === 'fxToHead') {
        const t = liveClipTime();
        if (t == null) { say('park the monitor on this shot first'); return; }
        setWindowEnd(btn.id === 'fxFromHead' ? 't0' : 't1', t);
        return;
      }
      if (btn.id === 'fxWholeShot') { S.window = null; paint(true); return; }
      const card = btn.closest('.fxcard');
      const eff = card ? byId(card.dataset.id) : null;
      if (!eff) return;
      if (btn.dataset.act === 'delevent') {
        const li = btn.closest('.fxev');
        const i = Number(li.dataset.i);
        const events = (eff.events || []).filter((_, k) => k !== i);
        if (!events.length) { say('the last moment — remove or discard the effect instead'); return; }
        if (S.sel && S.sel.id === eff.id) S.sel = null;
        put(eff.id, { events });
        return;
      }
      if (btn.classList.contains('nudge')) {
        const li = btn.closest('.fxev');
        nudge(eff, Number(li.dataset.i), Number(btn.dataset.d));
        return;
      }
      if (btn.dataset.act) act(btn.dataset.act, eff);
      return;
    }
    const li = e.target.closest('.fxev');
    if (li) {
      const card = li.closest('.fxcard');
      selectEvent(card.dataset.id, Number(li.dataset.i));
    }
  }

  function onToolInput(e) {
    const t = e.target;
    if (t.id === 'fxNote') S.note = t.value;
    else if (t.id === 'fxPlace') S.place = !!t.checked;
    else if (t.id === 'fxFrom' || t.id === 'fxTo') {
      const v = Number(t.value);
      if (Number.isFinite(v)) {
        const r = shotRange(S.shot) || { t0: 0, t1: 0 };
        const cur = S.window || { t0: r.t0, t1: r.t1 };
        S.window = t.id === 'fxFrom' ? { t0: v, t1: cur.t1 } : { t0: cur.t0, t1: v };
      }
    }
    else if (t.id === 'fxGoal') { if (S.sketch) S.sketch.goal = t.value; }
    else if (t.closest('.fxiter')) {
      const card = t.closest('.fxcard');
      if (card) S.iter[card.dataset.id] = t.value;
    }
  }

  function onToolKey(e) {
    if (e.key !== 'Enter') return;
    const t = e.target;
    if (t.closest && t.closest('.fxiter')) {
      e.preventDefault();
      const card = t.closest('.fxcard');
      const eff = card ? byId(card.dataset.id) : null;
      if (eff) act('revise', eff);
    } else if (t.id === 'fxGoal') { e.preventDefault(); useSketch(); }
    else if (t.id === 'fxNote' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); design(); }
  }

  /* ------------------------------------------------------------ following the board */
  function onSelect() {
    const id = shotId();
    if (id === S.shot) return;
    S.shot = id;
    if (S.sel && (!byId(S.sel.id) || String(byId(S.sel.id).shot) !== String(id))) S.sel = null;
    if (id) fetchPrice(id); else S.price = null;
    paint();
    badge();
  }

  /* ------------------------------------------------------------ the monitor overlay */
  /* Every accepted or proposed effect of the live shot, drawn at the live time from
   * the same JSON the render draws from, on a 2D canvas over the monitor's live video
   * (the grade.js pattern: follow the video's frames with requestVideoFrameCallback,
   * re-armed when the live element changes; a parked video draws on `seeked`).
   *
   * The geometry is fx.py's, in the frame's own pixels (the canvas is videoWidth ×
   * videoHeight, so the same fractions land the same way on the proxy and the master):
   * the sprite box is `overlay.size × width` px square, centred on (x·width, y·height)
   * plus (dx·width, dy·width); shapes are in -1..1 across the box (a coordinate × half
   * the box); a stroke `width` and a text `h` are × the box; `poseAt` scales, fades,
   * rotates and offsets the whole sprite; `flash` tints the whole frame for its
   * duration. An event draws while `t ≤ clipT < t + duration` and only when it is
   * inside the shot's range. */
  const O = { canvas: null, ctx: null, frameGen: 0, armedFor: null };

  function sampleTrack(track, t, dflt) {
    if (!track || !track.length) return dflt;
    if (t <= track[0][0]) return track[0][1];
    for (let k = 0; k + 1 < track.length; k++) {
      const t0 = track[k][0], v0 = track[k][1], t1 = track[k + 1][0], v1 = track[k + 1][1];
      if (t0 <= t && t <= t1) return t1 === t0 ? v1 : v0 + (v1 - v0) * (t - t0) / (t1 - t0);
    }
    return track[track.length - 1][1];
  }

  /* fx.pose_at, the same rules: linear between keyframes, the first value before the
   * first key, the last after the last, a missing track its default. */
  function poseAt(overlay, t) {
    const a = (overlay && overlay.anim) || {};
    return {
      scale: sampleTrack(a.scale, t, 1.0),
      opacity: sampleTrack(a.opacity, t, 1.0),
      rotate: sampleTrack(a.rotate, t, 0.0),
      dx: sampleTrack(a.dx, t, 0.0),
      dy: sampleTrack(a.dy, t, 0.0),
    };
  }

  /* A shape's presence at `dt` seconds into the effect (fx.shape_alpha), and a
   * text's revealed part (fx.text_at) — the same rules as the master's. */
  function shapeAlpha(s, dt, dur) {
    const start = Number(s.start || 0), end = s.end == null ? dur : Number(s.end);
    if (dt < start || dt > end) return 0;
    const fade = Number(s.fade || 0);
    if (fade <= 0) return 1;
    return Math.max(0, Math.min(1, (dt - start) / fade, (end - dt) / fade));
  }
  function textAt(s, dt) {
    const text = String(s.text || '');
    const since = dt - Number(s.start || 0);
    if (s.reveal === 'typewriter') return [text.slice(0, Math.floor(Math.max(0, since) * Number(s.cps || 14))), 1];
    if (s.reveal === 'fade') return [text, Math.max(0, Math.min(1, since / 0.4))];
    return [text, 1];
  }

  function drawShape(ctx, s, B, dt) {
    const h = B / 2;
    ctx.strokeStyle = s.color || '#ffffff';
    ctx.fillStyle = s.color || '#ffffff';
    ctx.lineWidth = Math.max(0.5, (s.width == null ? 0.08 : s.width) * B);
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    const x = (s.x || 0) * h, y = (s.y || 0) * h;
    switch (s.type) {
      case 'line':
        ctx.beginPath();
        ctx.moveTo(s.from[0] * h, s.from[1] * h);
        ctx.lineTo(s.to[0] * h, s.to[1] * h);
        ctx.stroke();
        break;
      case 'circle':
        ctx.beginPath();
        ctx.arc(x, y, Math.max(0.5, (s.r == null ? 0.5 : s.r) * h), 0, Math.PI * 2);
        if (s.fill !== false) ctx.fill(); else ctx.stroke();
        break;
      case 'ring': {
        const r = (s.r == null ? 0.5 : s.r) * h, r2 = (s.r2 == null ? r * 0.7 : s.r2 * h);
        ctx.beginPath();
        ctx.arc(x, y, Math.max(0.5, r), 0, Math.PI * 2);
        ctx.arc(x, y, Math.max(0, Math.min(r2, r)), 0, Math.PI * 2, true);
        ctx.fill('evenodd');
        break;
      }
      case 'rect': {
        const w = (s.w == null ? 1 : s.w) * h, hh = (s.h == null ? 1 : s.h) * h;
        ctx.save();
        ctx.translate(x, y);
        ctx.rotate((s.rotate || 0) * Math.PI / 180);
        if (s.fill) ctx.fillRect(-w / 2, -hh / 2, w, hh); else ctx.strokeRect(-w / 2, -hh / 2, w, hh);
        ctx.restore();
        break;
      }
      case 'polygon': {
        const pts = s.points || [];
        if (pts.length < 2) break;
        ctx.beginPath();
        ctx.moveTo(pts[0][0] * h, pts[0][1] * h);
        for (let k = 1; k < pts.length; k++) ctx.lineTo(pts[k][0] * h, pts[k][1] * h);
        ctx.closePath();
        if (s.fill) ctx.fill(); else ctx.stroke();
        break;
      }
      case 'text': {
        // h is a fraction of the BOX side; the widest full line fits the box's width;
        // a reveal shows part of the text — the size never changes as it types
        const [shown, talpha] = textAt(s, dt == null ? 0 : dt);
        if (!shown.trim()) break;
        let px = Math.max(1, (s.h == null ? 0.2 : s.h) * B);
        const font = (p) => `${s.bold === false ? '' : 'bold '}${p}px system-ui, sans-serif`;
        ctx.font = font(px);
        const lines = String(s.text || '').split('\n');
        if (s.fit !== false) {
          const widest = Math.max(...lines.map((l) => ctx.measureText(l).width), 0);
          const limit = 0.95 * B;
          if (widest > limit && widest > 0) { px = Math.max(1, px * limit / widest); ctx.font = font(px); }
        }
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        const gap = px * 1.18;
        const top = y - gap * (lines.length - 1) / 2;
        const prev = ctx.globalAlpha;
        ctx.globalAlpha = prev * talpha;
        shown.split('\n').forEach((line, i) => { if (line) ctx.fillText(line, x, top + i * gap); });
        ctx.globalAlpha = prev;
        break;
      }
      default: break;
    }
  }

  function drawEffect(ctx, e, sg, t, W, H) {
    const ov = e.overlay;
    if (!ov || !Array.isArray(ov.shapes)) return;
    const B = (ov.size || 0.12) * W;
    const dur = ov.duration || 0.35;
    for (const ev of e.events || []) {
      if (!(ev.t >= sg.in && ev.t < sg.out)) continue;
      const dt = t - ev.t;
      if (dt < 0 || dt >= dur) continue;
      if (ov.flash && dt < (ov.flash.duration || 0)) {
        ctx.save();
        ctx.globalAlpha = ov.flash.opacity == null ? 0.15 : ov.flash.opacity;
        ctx.fillStyle = ov.flash.color || '#ff0000';
        ctx.fillRect(0, 0, W, H);
        ctx.restore();
      }
      const pose = poseAt(ov, dt);
      ctx.save();
      ctx.translate(ev.x * W + pose.dx * W, ev.y * H + pose.dy * W);
      ctx.rotate(pose.rotate * Math.PI / 180);
      ctx.scale(pose.scale, pose.scale);
      for (const s of ov.shapes) {
        const a = shapeAlpha(s, dt, dur);
        if (a <= 0) continue;
        ctx.globalAlpha = Math.max(0, Math.min(1, pose.opacity * (s.opacity == null ? 1 : s.opacity) * a));
        drawShape(ctx, s, B, dt);
      }
      ctx.restore();
    }
  }

  /* The shot the monitor is on and the element playing it. */
  function liveShot() {
    const p = PLAYER();
    const list = SEGS();
    return p && p.idx >= 0 && list[p.idx] ? list[p.idx] : null;
  }

  function draw() {
    const c = O.canvas;
    if (!c || !O.ctx) return;
    const v = live();
    const sg = liveShot();
    const effs = sg ? activeForShot(sg.id) : [];
    const on = !!(v && effs.length);
    c.classList.toggle('live', on);
    if (!on && !S.sketch) return;
    if (!v || !v.videoWidth || !v.videoHeight) return;   // no frame yet; the next one draws
    if (c.width !== v.videoWidth || c.height !== v.videoHeight) {
      c.width = v.videoWidth;
      c.height = v.videoHeight;
    }
    const ctx = O.ctx, W = c.width, H = c.height;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalAlpha = 1;
    ctx.clearRect(0, 0, W, H);
    if (sg) for (const e of effs) drawEffect(ctx, e, sg, v.currentTime, W, H);
    if (S.sketch) drawStrokes(ctx, W, H);
  }

  /* ------------------------------------------------------------ the sound */
  /* One Audio per effect from its sound.wav, started when the live clip time crosses
   * an event's t while playing; each event fires once per pass (reset on seek and on
   * play), so a frame callback that lands twice inside one hit does not double it. */
  function syncAudio() {
    const keep = new Set();
    for (const e of S.effects) {
      if (!e.sound_url) continue;
      keep.add(e.id);
      const key = `${e.sound_url}|${JSON.stringify(e.sound || null)}|${(e.history || []).length}`;
      const cur = S.audio.get(e.id);
      if (cur && cur.key === key) continue;
      let el = null;
      try {
        el = new Audio(`${e.sound_url}?v=${Date.now().toString(36)}`);
        el.preload = 'auto';
      } catch (err) { el = null; }
      S.audio.set(e.id, { key, el });
    }
    for (const id of [...S.audio.keys()]) if (!keep.has(id)) S.audio.delete(id);
  }

  function sound(e, i) {
    const k = `${e.id}:${i}`;
    if (S.fired.has(k)) return;
    S.fired.add(k);
    const a = S.audio.get(e.id);
    if (!a || !a.el) return;
    S.sounded += 1;
    try {
      const el = a.el.paused || a.el.ended ? a.el : a.el.cloneNode();   // two hits inside one WAV overlap
      el.currentTime = 0;
      const p = el.play();
      if (p && typeof p.catch === 'function') p.catch(() => { /* no gesture yet: the picture still draws */ });
    } catch (err) { /* fine */ }
  }

  function step() {
    const v = live();
    const sg = liveShot();
    if (v && sg) {
      const t = v.currentTime;
      let prev = S.prevV === v ? S.prevT : null;
      // a hand-over lands the new element at the shot's in-point: a hit right there counts
      if (prev == null && Math.abs(t - sg.in) < 0.25) prev = Math.min(t, sg.in);
      if (prev != null && !v.paused && t > prev) {
        for (const e of activeForShot(sg.id)) {
          (e.events || []).forEach((ev, i) => {
            if (ev.t >= sg.in && ev.t < sg.out && ev.t >= prev && ev.t < t) sound(e, i);
          });
        }
      }
      S.prevV = v;
      S.prevT = t;
    }
    draw();
    updatePlayheadDom();
  }

  /* Follow the live video: a new frame → step. Re-armed whenever the live element
   * changes; a callback from an element no longer live simply stops. */
  function armFrames() {
    const v = live();
    const gen = ++O.frameGen;
    O.armedFor = v;
    if (!v) return;
    const again = () => {
      if (gen !== O.frameGen || v !== live()) return;
      step();
      if ('requestVideoFrameCallback' in v) v.requestVideoFrameCallback(again);
      else requestAnimationFrame(again);
    };
    if ('requestVideoFrameCallback' in v) v.requestVideoFrameCallback(again);
    else requestAnimationFrame(again);
  }

  function mountOverlay() {
    const c = $('#fxCanvas');
    if (!c) return;
    O.canvas = c;
    try { O.ctx = c.getContext('2d'); } catch (e) { O.ctx = null; }
    if (!O.ctx) return;
    const vids = [$('#pv0'), $('#pv1')].filter(Boolean);
    vids.forEach((v) => {
      v.addEventListener('seeked', () => { S.fired.clear(); S.prevV = v; S.prevT = v.currentTime; step(); });
      v.addEventListener('play', () => { S.fired.clear(); S.prevV = v; S.prevT = v.currentTime; armFrames(); step(); });
      ['loadeddata', 'timeupdate', 'pause'].forEach((ev) => v.addEventListener(ev, step));
    });
    const t = TL();
    if (t && typeof t.on === 'function') t.on('playhead', () => { if (live() !== O.armedFor) armFrames(); });
    setInterval(() => { if (live() !== O.armedFor) armFrames(); }, 250);
    armFrames();
  }

  /* ------------------------------------------------------------ the sketch */
  /* Karl: *"options for human to draw references on a keyframe."* Draw a reference
   * pauses the monitor and hands `#fxCanvas` the pointer (`.sketch`); strokes are
   * fractions of the frame from the first point, and each stroke's centroid is a mark.
   * Use it builds `{t, goal, strokes, marks, png}` — the png is the frame with the
   * strokes drawn, at the video's own size — and the next Design carries it; the
   * server keeps the marks as the anchors, no placing call needed. */
  const K = { accent: null };

  function accent() {
    if (K.accent) return K.accent;
    let v = '';
    try { v = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim(); } catch (e) { v = ''; }
    K.accent = v || '#f5c542';
    return K.accent;
  }

  /* Where a pointer event lands on the picture, as fractions of the frame. `el` fills
   * the screen and its picture (W × H) sits inside it object-fit: contain — a
   * letterboxed rect of the element's box — for the canvas and the video alike. */
  function frameXY(ev, el, W, H) {
    if (!el || !W || !H) return null;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    const scale = Math.min(r.width / W, r.height / H);
    const dw = W * scale, dh = H * scale;
    const ox = r.left + (r.width - dw) / 2, oy = r.top + (r.height - dh) / 2;
    return {
      x: Math.min(1, Math.max(0, (ev.clientX - ox) / dw)),
      y: Math.min(1, Math.max(0, (ev.clientY - oy) / dh)),
      scale,
    };
  }

  function startSketch() {
    if (!S.shot) { say('select a shot on the timeline first'); return; }
    if (!O.canvas || !O.ctx) { say('no canvas to draw on'); return; }
    pause();
    const sg = liveShot();
    const t = TL();
    if ((!sg || String(sg.id) !== String(S.shot)) && t) {
      const start = t.filmStart(S.shot);
      if (start >= 0) t.seek(start);          // the monitor parks on the shot's first frame
    }
    S.sketch = { strokes: [], cur: null, goal: '' };
    const v = live();
    if (v && v.videoWidth && (O.canvas.width !== v.videoWidth || O.canvas.height !== v.videoHeight)) {
      O.canvas.width = v.videoWidth;
      O.canvas.height = v.videoHeight;
    }
    O.canvas.classList.add('sketch');
    mountHud();
    draw();
    paint();
  }

  /* The sketch's controls on the monitor itself — the goal, Use it, Cancel, the count —
   * so the drawing and its buttons are in the same place (the tool's own copy sat a
   * screen away: Karl could delete strokes but not see what he had drawn). */
  function mountHud() {
    const sc = $('.screen');
    if (!sc || $('#fxHud')) return;
    const hud = document.createElement('div');
    hud.id = 'fxHud';
    hud.innerHTML = `<span class="fxhudn">0 strokes</span>`
      + `<input type="text" id="fxHudGoal" placeholder="what the marks mean — the skis, the jump, where the title sits" title="what the marks mean">`
      + `<button id="fxHudUse" class="primary" disabled>Use it</button><button id="fxHudCancel">Cancel</button>`
      + `<span class="hint">one stroke per place · <kbd>⌫</kbd> undoes · <kbd>esc</kbd> cancels</span>`;
    hud.addEventListener('pointerdown', (ev) => ev.stopPropagation());
    hud.addEventListener('click', (ev) => {
      ev.stopPropagation();
      const b = ev.target.closest('button');
      if (!b) return;
      if (b.id === 'fxHudUse') useSketch();
      else if (b.id === 'fxHudCancel') cancelSketch();
    });
    hud.addEventListener('input', (ev) => { if (ev.target.id === 'fxHudGoal' && S.sketch) S.sketch.goal = ev.target.value; });
    hud.addEventListener('keydown', (ev) => { ev.stopPropagation(); if (ev.key === 'Enter') useSketch(); });
    sc.appendChild(hud);
    updateHud();
  }
  function updateHud() {
    const hud = $('#fxHud');
    if (!hud || !S.sketch) return;
    const n = S.sketch.strokes.length;
    hud.querySelector('.fxhudn').textContent = `${n} stroke${n === 1 ? '' : 's'}`;
    hud.querySelector('#fxHudUse').disabled = !n;
  }

  function endSketch() {
    S.sketch = null;
    if (O.canvas) O.canvas.classList.remove('sketch');
    const hud = $('#fxHud');
    if (hud) hud.remove();
    draw();
    paint(true);
  }

  function cancelSketch() {
    if (!S.sketch) return;
    endSketch();
  }

  function undoStroke() {
    if (!S.sketch) return false;
    if (S.sketch.cur) { S.sketch.cur = null; draw(); return true; }
    if (!S.sketch.strokes.length) return true;
    S.sketch.strokes.pop();
    draw();
    paint();
    return true;
  }

  function centroid(stroke) {
    let x = 0, y = 0;
    for (const p of stroke) { x += p.x; y += p.y; }
    return [round4(x / stroke.length), round4(y / stroke.length)];
  }

  function framePng(v) {
    try {
      const W = v && v.videoWidth ? v.videoWidth : (O.canvas.width || 1280);
      const H = v && v.videoHeight ? v.videoHeight : (O.canvas.height || 720);
      const c = document.createElement('canvas');
      c.width = W;
      c.height = H;
      const ctx = c.getContext('2d');
      if (v && v.videoWidth) ctx.drawImage(v, 0, 0, W, H);
      else { ctx.fillStyle = '#000'; ctx.fillRect(0, 0, W, H); }
      drawStrokes(ctx, W, H);
      return c.toDataURL('image/png');
    } catch (e) {
      return null;                                 // a tainted or empty frame: the marks still go
    }
  }

  function useSketch() {
    if (!S.sketch) return;
    const strokes = S.sketch.strokes.filter((s) => s.length);
    if (!strokes.length) { say('draw something on the monitor first'); return; }
    const v = live();
    const png = framePng(v);
    S.reference = {
      t: round4(v ? v.currentTime : 0),
      goal: (S.sketch.goal || '').trim(),
      strokes: strokes.map((s) => s.map((p) => ({ x: p.x, y: p.y }))),
      marks: strokes.map(centroid),
    };
    if (png) S.reference.png = png;
    endSketch();
    if (window.dock && typeof dock.reveal === 'function') dock.reveal('#fx');
    say(`reference: ${S.reference.marks.length} mark${S.reference.marks.length === 1 ? '' : 's'} at ${fmtT(S.reference.t)} — goes with the next Design`);
  }

  /* The strokes (and the one being drawn) as a 3-px accent line on screen, whatever
   * the bitmap's size, and a dot at each mark. */
  function drawStrokes(ctx, W, H) {
    const sk = S.sketch;
    if (!sk) return;
    const list = sk.strokes.concat(sk.cur ? [sk.cur] : []);
    let scale = 1;
    try {
      const r = O.canvas.getBoundingClientRect();
      scale = Math.min(r.width / W, r.height / H) || 1;
    } catch (e) { scale = 1; }
    // amber over a dark halo: a 3-px accent line was invisible on snow
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalAlpha = 1;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    const path = (s) => {
      ctx.beginPath();
      ctx.moveTo(s[0].x * W, s[0].y * H);
      for (let k = 1; k < s.length; k++) ctx.lineTo(s[k].x * W, s[k].y * H);
      if (s.length === 1) ctx.lineTo(s[0].x * W + 0.01, s[0].y * H);
    };
    for (const s of list) {
      if (!s.length) continue;
      path(s); ctx.strokeStyle = 'rgba(10,10,14,.75)'; ctx.lineWidth = Math.max(4, 10 / scale); ctx.stroke();
      path(s); ctx.strokeStyle = '#e0b050'; ctx.lineWidth = Math.max(2, 5 / scale); ctx.stroke();
    }
    sk.strokes.forEach((s, i) => {
      if (!s.length) return;
      const [cx, cy] = centroid(s);
      const rad = Math.max(5, 11 / scale);
      ctx.beginPath(); ctx.arc(cx * W, cy * H, rad + 2, 0, Math.PI * 2); ctx.fillStyle = 'rgba(10,10,14,.8)'; ctx.fill();
      ctx.beginPath(); ctx.arc(cx * W, cy * H, rad, 0, Math.PI * 2); ctx.fillStyle = '#e0b050'; ctx.fill();
      ctx.fillStyle = '#14100a';
      ctx.font = `bold ${Math.max(9, 14 / scale)}px system-ui, sans-serif`;
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(String(i + 1), cx * W, cy * H + 0.5);
    });
    ctx.restore();
    updateHud();
  }

  function onSketchDown(ev) {
    if (!S.sketch || ev.button !== 0) return;
    ev.preventDefault();
    ev.stopPropagation();
    const p = frameXY(ev, O.canvas, O.canvas.width, O.canvas.height);
    if (!p) return;
    try { O.canvas.setPointerCapture(ev.pointerId); } catch (e) { /* fine */ }
    S.sketch.cur = [{ x: round4(p.x), y: round4(p.y) }];
    draw();
  }

  function onSketchMove(ev) {
    if (!S.sketch || !S.sketch.cur) return;
    ev.preventDefault();
    const p = frameXY(ev, O.canvas, O.canvas.width, O.canvas.height);
    if (!p) return;
    const cur = S.sketch.cur, last = cur[cur.length - 1];
    if (Math.hypot(p.x - last.x, p.y - last.y) < 0.002) return;
    cur.push({ x: round4(p.x), y: round4(p.y) });
    draw();
  }

  function onSketchUp(ev) {
    if (!S.sketch || !S.sketch.cur) return;
    ev.stopPropagation();
    S.sketch.strokes.push(S.sketch.cur);
    S.sketch.cur = null;
    draw();
    paint();
  }

  function mountSketch() {
    const c = O.canvas;
    if (!c) return;
    c.addEventListener('pointerdown', onSketchDown);
    c.addEventListener('pointermove', onSketchMove);
    c.addEventListener('pointerup', onSketchUp);
    c.addEventListener('pointercancel', onSketchUp);
    // the screen's own click plays the cut; not while drawing on it
    c.addEventListener('click', (ev) => { if (S.sketch) { ev.preventDefault(); ev.stopPropagation(); } });
  }

  function inField(t) {
    return !!t && (['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName) || t.isContentEditable);
  }

  // On window, capture, and registered now — at load, before dock.js's Esc (registered
  // on DOMContentLoaded) and before timeline-keys.js's ⌫ (ripple delete, registered
  // when the timeline mounts): while a reference is being drawn, ⌫ is the last
  // stroke and Esc is the sketch, and nothing else hears them.
  window.addEventListener('keydown', (e) => {
    if (!S.sketch) return;
    if (e.key === 'Escape') {
      cancelSketch();
      e.preventDefault();
      e.stopImmediatePropagation();
    } else if ((e.key === 'Backspace' || e.key === 'Delete') && !inField(e.target)) {
      undoStroke();
      e.preventDefault();
      e.stopImmediatePropagation();
    }
  }, true);

  /* ------------------------------------------------------------ live nudging */
  /* EFFECTS.md, rule 4: *"Two clicks beats any amount of inference."* A hit selected
   * on its card parks the monitor on its frame; a click on the paused monitor then
   * moves its anchor there — a PUT in fractions of the frame, the checklist cleared
   * by the server because it no longer describes this effect. Capture, on the screen,
   * so the screen's own click (play / pause) does not fire for it; while playing, or
   * with nothing selected, the click stays the screen's. */
  function onScreenClick(ev) {
    if (S.sketch || !S.sel || ev.button !== 0) return;
    const p = PLAYER();
    if (p && p.playing) return;
    const e = byId(S.sel.id);
    const sg = e && seg(e.shot);
    const lv = liveShot();
    if (!e || !sg) return;
    if (!lv || String(lv.id) !== String(sg.id)) { say('park the monitor on that shot first — click the hit on its card'); return; }
    const v = live();
    if (!v || !v.videoWidth) return;
    const f = frameXY(ev, ev.currentTarget, v.videoWidth, v.videoHeight);
    if (!f) return;
    ev.preventDefault();
    ev.stopPropagation();
    const i = S.sel.i;
    moveEvent(e, i, f.x, f.y).then((n) => {
      if (n && n.events[i]) say(`hit ${i + 1} moved to x ${n.events[i].x.toFixed(2)} y ${n.events[i].y.toFixed(2)}`);
    });
  }

  function mountNudge() {
    const sc = $('.screen');
    if (sc) sc.addEventListener('click', onScreenClick, true);
  }

  /* ------------------------------------------------------------ mount */
  function mount() {
    const el = $('#fx');
    if (!el) return;
    el.addEventListener('click', onToolClick);
    el.addEventListener('pointerdown', onToolPointerDown);
    el.addEventListener('input', onToolInput);
    el.addEventListener('change', onToolInput);
    el.addEventListener('keydown', onToolKey);
    const t = TL();
    if (t && typeof t.on === 'function') {
      t.on('select', onSelect);
      t.on('change', () => { onSelect(); paint(); badge(); });
    }
    setInterval(onSelect, 500);          // playback moves the anchor without a select event
    if (t && typeof t.on === 'function') {
      t.on('zoom', paintBand);
      t.on('playhead', () => { paintBand(); updatePlayheadDom(); if (!S.drag) paint(); });
    }
    setInterval(paintBand, 700);         // the dock's tool changes without an event
    mountOverlay();
    mountSketch();
    mountNudge();
    paint(true);
    refresh().then(pollJobs);
    setInterval(pollJobs, POLL_MS);
    window.fx.ready = true;
  }

  window.fx = {
    ready: false,
    refresh,
    badge,
    state: S,
    NUDGE_S,
    select: selectEvent,
    nudge: (id, i, frames) => { const e = byId(id); return e ? nudge(e, i, frames) : Promise.resolve(null); },
    move: (id, i, x, y) => { const e = byId(id); return e ? moveEvent(e, i, x, y) : Promise.resolve(null); },
    fmtT,
    poseAt,
    draw,
    audio: (id) => { const a = S.audio.get(id); return a && a.el ? a.el : null; },
    sketch: { start: startSketch, use: useSketch, cancel: cancelSketch, undo: undoStroke },
    frameXY,
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
})();
