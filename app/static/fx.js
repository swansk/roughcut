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
  function shotIndex(id) { return SEGS().findIndex((s) => String(s.id) === String(id)); }

  function badge() {
    if (window.dock && typeof dock.badge === 'function') dock.badge('fx', forShot(S.shot).length);
  }

  async function fetchPrice(id) {
    if (!id) { S.price = null; return; }
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

  function preview(e) {
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
        say(`${e.name}: accepted — in the cut`);
        await refresh();
      } else if (name === 'discard') {
        await api('POST', '/api/fx/discard', { id: e.id });
        say(`${e.name}: discarded`);
        await refresh();
      } else if (name === 'remove') {
        await api('POST', '/api/fx/remove', { id: e.id });
        say(`${e.name}: removed from the cut`);
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
    const body = { shot, note, place: !!S.place };
    if (S.reference) body.reference = S.reference;
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
      `<li class="fxev${i === sel ? ' sel' : ''}" data-i="${i}" title="click: park the monitor on this hit, then click the monitor to move it">`
      + `<button class="nudge" data-d="-1" title="one frame earlier">◀</button>`
      + `<span class="t">${fmtT(ev.t)}</span>`
      + `<span class="xy">x ${Number(ev.x).toFixed(2)} y ${Number(ev.y).toFixed(2)}</span>`
      + (ev.label ? `<span class="hint">${esc(ev.label)}</span>` : '')
      + `<button class="nudge" data-d="1" title="one frame later">▶</button></li>`).join('');
    const v = e.verify;
    const checks = v && Array.isArray(v.checks) && v.checks.length
      ? `<div class="fxverify ${v.ok ? 'ok' : 'bad'}"><div class="fxvhead">${v.ok ? '✓ verified' : '✗ not yet'}`
        + (v.at ? ` <span class="hint">${esc(String(v.at).replace('T', ' ').slice(0, 16))}</span>` : '')
        + `</div><ul class="fxchecks">${v.checks.map(checkRow).join('')}</ul></div>`
      : '';
    const proposed = e.status === 'proposed';
    const btns = `<div class="fxbtns">`
      + `<button data-act="preview" title="play this shot in the monitor with the effect drawn and heard">Preview</button>`
      + `<button data-act="verify" title="render a proof of the shot and run the checklist">Verify</button>`
      + `<button data-act="iterate" title="tell the model what to change">Iterate</button>`
      + (proposed
        ? `<button data-act="accept" class="primary" title="into the cut — the render draws it">Accept</button>`
          + `<button data-act="discard" title="drop the proposal and its files">Discard</button>`
        : `<button data-act="remove" title="out of the cut">Remove</button>`)
      + `</div>`;
    const iter = S.iterOpen.has(e.id)
      ? `<div class="fxiter"><input type="text" placeholder="make them red and bigger · one hit only, the big one" value="${esc(S.iter[e.id] || '')}">`
        + `<button data-act="revise" class="primary">Go</button></div>`
      : '';
    const extras = [];
    if (e.reference) extras.push(`reference · ${(e.reference.marks || []).length} marks${e.reference.goal ? ` · ${esc(e.reference.goal)}` : ''}`);
    if (e.proof_url) extras.push(`<a href="${esc(e.proof_url)}" target="_blank" rel="noopener">proof</a>`);
    if (e.strip_url) extras.push(`<a href="${esc(e.strip_url)}" target="_blank" rel="noopener">strip</a>`);
    return `<div class="fxcard ${esc(e.status)}" data-id="${esc(e.id)}">`
      + `<div class="fxhead"><b class="fxname">${esc(e.name || 'effect')}</b>`
      + `<span class="fxn">${n} hit${n === 1 ? '' : 's'}</span>${chip(e)}</div>`
      + (e.note ? `<div class="fxnote">${esc(e.note)}</div>` : '')
      + (e.why ? `<div class="fxwhy hint">${esc(e.why)}</div>` : '')
      + `<ul class="fxevents">${events}</ul>`
      + (sel >= 0 ? `<div class="fxpick hint">hit ${sel + 1} selected · click the monitor (paused) to move it there</div>` : '')
      + checks
      + (extras.length ? `<div class="fxextras hint">${extras.join(' · ')}</div>` : '')
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
      : '';
    const sk = S.sketch ? sketchHtml() : '';
    return `<div class="fxdesign">`
      + `<div class="fxdhead hint">design an effect for this shot</div>`
      + `<textarea id="fxNote" placeholder="hit markers where my skis hit the rocks, with the sound" rows="3">${esc(S.note)}</textarea>`
      + `<label class="fxplace" title="a priced model call looks at a frame around each impact and puts the marker on the thing you named"><input type="checkbox" id="fxPlace"${S.place ? ' checked' : ''}> place on the frames${price}</label>`
      + ref + sk
      + `<div class="fxbtns"><button id="fxSketch"${S.sketch ? ' disabled' : ''} title="pause the monitor and draw on the frame: where the markers go">Draw a reference</button>`
      + `<div class="grow"></div><button id="fxDesign" class="primary"${designing ? ' disabled' : ''}>${designing ? 'Designing…' : 'Design'}</button></div>`
      + (busy ? `<div class="fxstate hint">${esc(busy.label)}${busy.detail ? ` — ${esc(busy.detail)}` : ''}</div>` : '')
      + `</div>`;
  }

  function sketchHtml() {
    const n = S.sketch.strokes.length;
    return `<div class="fxsketch">`
      + `<div class="hint">draw on the monitor · <span class="fxstrokes">${n} stroke${n === 1 ? '' : 's'}</span> · <kbd>⌫</kbd> undoes the last · <kbd>esc</kbd> cancels</div>`
      + `<input type="text" id="fxGoal" placeholder="the skis — put the markers here" value="${esc(S.sketch.goal || '')}">`
      + `<div class="fxbtns"><button id="fxUse" class="primary"${n ? '' : ' disabled'}>Use it</button><button id="fxCancel">Cancel</button></div>`
      + `</div>`;
  }

  function signature() {
    return JSON.stringify([
      S.shot, S.effects, S.sel, S.price,
      S.reference && [S.reference.t, S.reference.marks.length, S.reference.goal],
      S.sketch && [S.sketch.strokes.length, S.sketch.goal], [...S.iterOpen], S.place,
      S.busy && [S.busy.id, S.busy.state, S.busy.detail, S.busy.milestone],
    ]);
  }

  /* Rebuild the tool's DOM — only when what it is built from changed (force: always),
   * so a poll never interrupts typing in the design box. */
  function paint(force) {
    const el = $('#fx');
    if (!el) return;
    const sig = signature();
    if (!force && sig === S.sig) return;
    S.sig = sig;
    const sg = S.shot != null ? seg(S.shot) : null;
    if (!sg) {
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
  }

  function onToolClick(e) {
    const btn = e.target.closest('button');
    if (btn) {
      if (btn.id === 'fxDesign') { design(); return; }
      if (btn.id === 'fxSketch') { startSketch(); return; }
      if (btn.id === 'fxUse') { useSketch(); return; }
      if (btn.id === 'fxCancel') { cancelSketch(); return; }
      if (btn.dataset.act === 'clearref') { S.reference = null; paint(); return; }
      const card = btn.closest('.fxcard');
      const eff = card ? byId(card.dataset.id) : null;
      if (!eff) return;
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
  // (part 2 — the overlay and the sound)
  function syncAudio() { /* filled with the overlay */ }
  function draw() { /* filled with the overlay */ }

  /* ------------------------------------------------------------ the sketch */
  // (part 3)
  function startSketch() { say('the sketch is not built yet'); }
  function useSketch() {}
  function cancelSketch() {}

  /* ------------------------------------------------------------ mount */
  function mount() {
    const el = $('#fx');
    if (!el) return;
    el.addEventListener('click', onToolClick);
    el.addEventListener('input', onToolInput);
    el.addEventListener('change', onToolInput);
    el.addEventListener('keydown', onToolKey);
    const t = TL();
    if (t && typeof t.on === 'function') {
      t.on('select', onSelect);
      t.on('change', () => { onSelect(); paint(); badge(); });
    }
    setInterval(onSelect, 500);          // playback moves the anchor without a select event
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
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
})();
