/* How the machine saw a clip, and Look deeper (INTAKE M15). One file, three screens.
 *
 * Karl, 2026-10-03 (#4): "make it clearer how the videos are indexed by the agent (e.g.
 * showing granularity), and make it easier to run deeper keyframe-based analysis (w/ AI
 * interpolating as needed between frames to really understand what is going on)."
 *
 * The **coverage strip** is one clip's duration as a bar with a lane per layer of the
 * index — what was heard, the coarse sheets' frames, the close look's windows, the deep
 * looks' keyframes, the free motion track — and the moments each layer claimed, marked
 * by whether anything agreed. The legend states the granularity in words, from the
 * sidecars' own records (a sidecar that did not record its model says so). Hover says
 * what was seen at that second; drag picks seconds to look deeper at; embedded beside a
 * player it carries the playhead and the shot's range.
 *
 * **Look deeper** is the priced button: a dry run prices the span (the price goes on the
 * button; nothing spends without the click), the run is a `deep` job in the top bar, and
 * the result shows in place — the keyframes as a filmstrip with their timestamps, the
 * beats under it with `seen` solid on its frame and `inferred` hatched between the two
 * frames that bracket it, the events, the camera, and the frames it asked for.
 *
 *   deep.strip(host, clip, {mark, onSeek, compact})  → controller {head(t), mark(a, b)}
 *   deep.line(host, clip, {range, onSeek, compact})  → {head(t), mark(a, b)}   ONE line
 *       ("every word heard · a frame every 4 s · 3 close looks ▸"); a click opens the
 *       strip under it, in host; every call renders closed (INTAKE M16 decision 9)
 *   deep.inspector(host, seg)        the board's inspector, for the selected shot
 *   deep.head(host, t)               the playhead, on a strip or an open line
 *   deep.minis(root)                 the open screen's cards, one request for the bin
 *   deep.rowButton(row, clip, start, end)   a priced button on a seen-tab row
 *
 * Logic stays here; each screen's hook is a line or two (the flow lane is rebuilding the
 * stage bars on all three screens in parallel, so nothing here touches their layout).
 */
(function () {
  'use strict';

  const CSS = `
.dv { font-size: 11px; color: var(--text, #e8e8ee); margin-top: 8px; }
.dv-top { display: flex; gap: 8px; align-items: baseline; margin-bottom: 3px; }
.dv-title { font: 600 9.5px var(--mono, ui-monospace, monospace); letter-spacing: .08em; color: var(--dim, #9a9aa8); }
.dv-wrap { position: relative; display: grid; grid-template-columns: 50px 1fr; }
.dv-labels { display: flex; flex-direction: column; font: 600 8.5px var(--mono, ui-monospace, monospace); color: var(--dim, #9a9aa8); letter-spacing: .04em; }
.dv-labels span { height: 10px; line-height: 10px; margin-bottom: 2px; }
.dv-labels span.m { height: 18px; line-height: 18px; }
.dv-bar { position: relative; background: #0c0c11; border: 1px solid var(--line, #2e2e38); border-radius: 3px; cursor: crosshair; user-select: none; touch-action: none; }
.dv-lane { position: relative; height: 10px; margin-bottom: 2px; overflow: hidden; }
.dv-lane.m { height: 18px; }
.dv-lane i { position: absolute; top: 0; bottom: 0; display: block; }
.dv-lane[data-lane=heard] i { background: #4a86b8; opacity: .75; top: 2px; bottom: 2px; border-radius: 2px; }
.dv-lane[data-lane=coarse] i { width: 1px; background: #8a8aa0; top: 2px; bottom: 2px; }
.dv-lane[data-lane=close] i.w { background: rgba(110,168,254,.18); border-left: 1px solid rgba(110,168,254,.5); border-right: 1px solid rgba(110,168,254,.5); }
.dv-lane[data-lane=close] i.f { width: 1px; background: #6ea8fe; top: 1px; bottom: 1px; }
.dv-lane[data-lane=deep] i.w { background: rgba(240,200,96,.18); border-left: 1px solid rgba(240,200,96,.6); border-right: 1px solid rgba(240,200,96,.6); cursor: pointer; }
.dv-lane[data-lane=deep] i.f { width: 2px; background: #f0c860; }
.dv-lane[data-lane=deep] i.f.asked { background: #ff8f6a; top: -2px; }
.dv-lane[data-lane=moments] i { top: 1px; bottom: 1px; min-width: 3px; border-radius: 2px; opacity: .9; }
.dv-lane svg { position: absolute; inset: 0; width: 100%; height: 100%; }
.dv-lane.empty::after { content: attr(data-empty); position: absolute; left: 4px; top: 0; font-size: 8.5px; line-height: 10px; color: #5a5a6a; }
.st-confirmed { background: var(--good, #64d08a); }
.st-deep { background: #f0c860; }
.st-unseen { background: transparent; box-shadow: inset 0 0 0 1px var(--warn, #e0b050); }
.st-fine-only { background: #8a7040; }
.st-unsupported { background: #4a4a58; }
.st-contradicted { background: var(--bad, #e06a6a); opacity: .55 !important; }
.st-unusable { background: repeating-linear-gradient(45deg, #333 0 3px, transparent 3px 6px); }
.dv-markband { position: absolute; top: 0; bottom: 0; background: rgba(100,208,138,.10); border-left: 1px solid var(--good, #64d08a); border-right: 1px solid var(--good, #64d08a); pointer-events: none; }
.dv-sel { position: absolute; top: 0; bottom: 0; background: rgba(240,200,96,.16); border: 1px dashed #f0c860; pointer-events: none; }
.dv-headline { position: absolute; top: -2px; bottom: -2px; width: 1px; background: #fff; pointer-events: none; }
.dv-tip { position: absolute; z-index: 30; bottom: calc(100% + 4px); transform: translateX(-50%); background: #1d1d25; border: 1px solid var(--line, #2e2e38); border-radius: 4px; padding: 5px 7px; width: max-content; max-width: 320px; white-space: normal; pointer-events: none; line-height: 1.35; }
.dv-tip b { color: var(--text, #e8e8ee); }
.dv-legend { color: var(--dim, #9a9aa8); line-height: 1.45; margin: 4px 0 0 50px; }
.dv-legend span { display: block; }
.dv-legend .k { font: 600 8.5px var(--mono, ui-monospace, monospace); letter-spacing: .05em; color: var(--text, #e8e8ee); margin-right: 4px; }
.dv-keys { margin: 3px 0 0 50px; color: var(--dim, #9a9aa8); display: flex; gap: 10px; flex-wrap: wrap; }
.dv-keys i { display: inline-block; width: 10px; height: 7px; border-radius: 2px; vertical-align: middle; margin-right: 3px; }
.dv-act { margin: 6px 0 0 50px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.dv-look { background: #2a2414; color: #f0c860; border: 1px solid #6a5a2a; border-radius: 4px; padding: 3px 9px; font: inherit; font-weight: 600; cursor: pointer; }
.dv-look:disabled { opacity: .5; cursor: default; }
.dv-look.small { padding: 1px 6px; font-size: 10px; font-weight: 500; }
.dv-info { color: var(--dim, #9a9aa8); }
.dv-res { margin: 8px 0 0 50px; border-top: 1px solid var(--line, #2e2e38); padding-top: 6px; }
.dv-res.inrow { margin: 4px 0 8px 0; }
.dv-x { float: right; background: none; border: 1px solid var(--line, #2e2e38); color: var(--dim, #9a9aa8); border-radius: 3px; font: 10px var(--mono, ui-monospace, monospace); cursor: pointer; padding: 0 5px; }
.dv-res-head { font: 600 9.5px var(--mono, ui-monospace, monospace); letter-spacing: .05em; color: #f0c860; margin-bottom: 4px; }
.dv-film { display: flex; gap: 3px; overflow-x: auto; padding-bottom: 4px; }
.dv-film figure { margin: 0; flex: none; width: 112px; cursor: zoom-in; border: 1px solid transparent; border-radius: 3px; }
.dv-film figure.big { width: 400px; cursor: zoom-out; }
.dv-film figure.asked { border-color: #ff8f6a; }
.dv-film figure.lit { border-color: #f0c860; }
.dv-film img { display: block; width: 100%; border-radius: 2px; background: #000; }
.dv-film figcaption { font: 9px var(--mono, ui-monospace, monospace); color: var(--dim, #9a9aa8); padding: 1px 2px; }
.dv-film figure.asked figcaption { color: #ff8f6a; }
.dv-beatbar { position: relative; height: 34px; background: #0c0c11; border: 1px solid var(--line, #2e2e38); border-radius: 3px; margin: 4px 0; }
.dv-beatbar i { position: absolute; display: block; border-radius: 2px; }
.dv-beatbar i.seen { top: 3px; height: 12px; background: #f0c860; min-width: 4px; }
.dv-beatbar i.inferred { top: 18px; height: 12px; background: repeating-linear-gradient(45deg, rgba(240,200,96,.55) 0 3px, transparent 3px 6px); border: 1px dashed rgba(240,200,96,.8); }
.dv-beatbar i.fr { top: 0; bottom: 0; width: 1px; background: #555566; }
.dv-beatbar i.ask { top: 0; width: 0; height: 0; border: 4px solid transparent; border-top-color: #ff8f6a; transform: translateX(-4px); background: none; }
.dv-beatbar i.lit { outline: 1px solid #fff; }
.dv-beats { margin: 0; padding-left: 0; list-style: none; }
.dv-beats li { padding: 1px 0; }
.dv-beats li .b { font: 600 9px var(--mono, ui-monospace, monospace); padding: 0 4px; border-radius: 2px; margin-right: 4px; }
.dv-beats li.seen .b { background: #f0c860; color: #1a1408; }
.dv-beats li.inferred .b { border: 1px dashed #f0c860; color: #f0c860; }
.dv-beats li .why { color: var(--dim, #9a9aa8); }
.dv-ev { margin-top: 4px; }
.dv-ev span { display: inline-block; margin: 0 6px 2px 0; }
.dv-cam, .dv-unsure { color: var(--dim, #9a9aa8); margin-top: 3px; }
.dv-mini { margin-top: 3px; position: relative; height: 12px; background: #0c0c11; border-radius: 2px; overflow: hidden; }
.dv-mini i { position: absolute; display: block; }
.dv-mini i.c { top: 0; height: 3px; width: 1px; background: #8a8aa0; }
.dv-mini i.w { top: 3px; height: 3px; background: #6ea8fe; }
.dv-mini i.d { top: 6px; height: 3px; background: #f0c860; }
.dv-mini i.m { top: 9px; height: 3px; min-width: 2px; }
.dv-line { color: var(--dim, #9a9aa8); font-size: 11px; cursor: pointer; user-select: none; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dv-line:hover { color: var(--text, #e8e8ee); }
`;

  function injectCss() {
    if (document.getElementById('deep-css')) return;
    const s = document.createElement('style');
    s.id = 'deep-css';
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const stemOf = (clip) => String(clip).replace(/\.[^.]+$/, '');
  const pct = (t, dur) => `${(100 * Math.max(0, Math.min(1, t / (dur || 1)))).toFixed(3)}%`;
  const fmtT = (t) => {
    if (t == null || !Number.isFinite(t)) return '';
    const m = Math.floor(t / 60);
    const s = t - 60 * m;
    return m ? `${m}:${s.toFixed(1).padStart(4, '0')}` : `${s.toFixed(1)} s`;
  };
  const usd = (x) => `$${Number(x || 0).toFixed(2)}`;

  /* "claude-<family>-<major>-<minor>" reads as "<Family> <major>.<minor>"; anything else as itself. */
  function modelName(m) {
    if (!m) return '';
    return String(m).split(',').map((x) => {
      const r = /claude-([a-z]+)-(\d+)-(\d+)/i.exec(x.trim());
      return r ? `${r[1][0].toUpperCase()}${r[1].slice(1)} ${r[2]}.${r[3]}` : x.trim();
    }).join(', ');
  }
  /* What read a layer: the model the sidecar recorded, or the role and the plain fact
   * that the model was not recorded — never today's config passed off as history. */
  function reader(layer) {
    if (layer.model) return modelName(layer.model);
    return layer.role ? `role ${layer.role}, model not recorded` : 'model not recorded';
  }

  const STATUS_WORD = {
    confirmed: 'confirmed — two looks agree', deep: 'deep look', unseen: 'unaudited',
    'fine-only': 'close look only', unsupported: 'looked again, nothing there',
    contradicted: 'contradicted',
  };

  // ---------------------------------------------------------------- data

  const covCache = new Map();          // clip -> Promise<coverage>
  function coverage(clip, fresh) {
    if (fresh || !covCache.has(clip)) {
      covCache.set(clip, fetch(`/api/coverage/${encodeURIComponent(clip)}`)
        .then((r) => (r.ok ? r.json() : null)).catch(() => null));
    }
    return covCache.get(clip);
  }
  const deepCache = new Map();         // clip -> Promise<{spans, frames_url}>
  function deepOf(clip, fresh) {
    if (fresh || !deepCache.has(clip)) {
      deepCache.set(clip, fetch(`/api/deep/${encodeURIComponent(clip)}`)
        .then((r) => (r.ok ? r.json() : null)).catch(() => null));
    }
    return deepCache.get(clip);
  }
  let binCov = null;
  let binAt = 0;

  const live = new Set();             // controllers on screen, to refresh after a run
  function invalidate(clip) {
    covCache.delete(clip);
    deepCache.delete(clip);
    binCov = null;
    live.forEach((c) => { if (c.clip === clip && c.host.isConnected) c.reload(); });
    live.forEach((c) => { if (!c.host.isConnected) live.delete(c); });
  }

  async function dryRun(clip, start, end) {
    const r = await fetch('/api/deep', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ clip, start, end, dry_run: true }) });
    return r.ok ? r.json() : null;
  }

  /* The seen tab prices every row; one request for all of them, gathered per tick. */
  let quoteQueue = [];
  function quoteLater(span) {
    return new Promise((resolve) => {
      quoteQueue.push({ span, resolve });
      if (quoteQueue.length === 1) {
        setTimeout(async () => {
          const batch = quoteQueue;
          quoteQueue = [];
          let quotes = [];
          try {
            const r = await fetch('/api/deep/quote', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ spans: batch.map((b) => b.span) }) });
            quotes = r.ok ? (await r.json()).quotes : [];
          } catch (e) { quotes = []; }
          batch.forEach((b, i) => b.resolve(quotes[i] || null));
        }, 0);
      }
    });
  }

  // ---------------------------------------------------------------- the button

  function priceText(q) {
    if (!q || q.error) return 'Look deeper';
    if (q.cached) return 'Show the deep look';
    return `Look deeper · ~${usd(q.projected_usd)}`;
  }
  function priceTitle(q) {
    if (!q || q.error) return (q && q.error) || 'price unavailable';
    if (q.cached) return `${fmtT(q.read.start)}–${fmtT(q.read.end)} was already read — free to show`;
    return `${q.frames} keyframes, ${q.start.toFixed(1)}–${q.end.toFixed(1)} s`
      + `${q.capped ? ` (capped at ${q.max_span_s} s)` : ''}, read in order by ${modelName(q.model)}`
      + ` · up to ${usd(q.max_usd)} if it asks for more frames (once)`
      + ` · motion ${q.motion}`;
  }

  /* Run one look: start the job and follow it to the end. Resolves with the final job
   * (or null when it could not start); `say(text)` narrates. */
  async function run(clip, start, end, say) {
    const r = await fetch('/api/deep', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ clip, start, end }) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { say(d.detail || 'the deep look could not start'); return null; }
    if (!d.job) return { state: 'done', cached: true };
    say(`looking — ${d.frames} frames, ~${usd(d.projected_usd)}`);
    for (;;) {
      await new Promise((ok) => setTimeout(ok, 1000));
      let j;
      try { j = await (await fetch(`/api/job/${d.job}`)).json(); } catch (e) { continue; }
      if (j.state === 'done' || j.state === 'failed' || j.state === 'cancelled') {
        invalidate(clip);
        say(j.detail || j.state);
        return j;
      }
      say(j.detail || 'looking');
    }
  }

  /* A priced Look deeper button for [start, end] of `clip`, with its info line. */
  function lookButton(host, clip, start, end, { small = false, onResult } = {}) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = `dv-look${small ? ' small' : ''}`;
    b.textContent = 'Look deeper · …';
    b.disabled = true;
    const info = document.createElement('span');
    info.className = 'dv-info';
    host.append(b, info);
    let q = null;
    const priced = small ? quoteLater({ clip, start, end }) : dryRun(clip, start, end);
    priced.then((got) => {
      q = got;
      b.textContent = priceText(q);
      b.title = priceTitle(q);
      b.disabled = !q || !!q.error;
      b.dataset.usd = q && !q.cached ? String(q.projected_usd) : '0';
      if (!small && q && !q.error) {
        info.textContent = q.cached ? 'already read'
          : `${q.frames} frames${q.capped ? ` · the first ${q.max_span_s} s` : ''} · ≤ ${usd(q.max_usd)} if it asks for more`;
      }
    });
    b.addEventListener('click', async (e) => {
      e.stopPropagation();
      e.preventDefault();
      if (!q) return;
      if (q.cached) { if (onResult) onResult(q.read.start, q.read.end); return; }
      b.disabled = true;
      const j = await run(clip, q.start, q.end, (t) => { info.textContent = t; });
      if (j && j.state === 'done') {
        b.textContent = 'Show the deep look';
        q = { ...q, cached: true, read: { start: q.start, end: q.end } };
        b.disabled = false;
        if (onResult) onResult(q.start, q.end);
      } else {
        b.disabled = false;
      }
    });
    return b;
  }

  // ---------------------------------------------------------------- the result

  /* One deep look, in place: the keyframes as a filmstrip, the beats under them (seen
   * solid on their frame, inferred hatched between the two frames that bracket it),
   * the beats in words, the events, the camera, what it was unsure of. */
  async function renderResult(host, clip, start, end, { inrow = false, bare = false } = {}) {
    const d = await deepOf(clip);
    const spans = (d && d.spans) || [];
    const r = spans.find((s) => s.start <= start + 0.25 && end <= s.end + 0.25)
      || spans.find((s) => s.end > start && s.start < end);
    host.innerHTML = '';
    if (!r) return;
    host.className = `dv-res${inrow ? ' inrow' : ''}`;
    host.dataset.start = r.start;
    host.dataset.end = r.end;
    const asked = new Set((r.asked || []).map((t) => Number(t).toFixed(2)));
    const span = Math.max(1e-6, r.end - r.start);
    const at = (t) => `${(100 * (t - r.start) / span).toFixed(2)}%`;
    const inferred = (r.beats || []).filter((b) => b.basis === 'inferred').length;
    const head = `DEEP LOOK · ${stemOf(clip)} ${r.start.toFixed(1)}–${r.end.toFixed(1)} s · `
      + `${(r.frames || []).length} keyframes · ${modelName(r.model) || 'model not recorded'} · ${usd(r.projected_usd)}`
      + ` · ${(r.beats || []).length} beats, ${inferred} inferred`
      + (r.followup ? ` · read again with ${r.asked.length} frame${r.asked.length === 1 ? '' : 's'} it asked for` : '')
      + ((r.asked_but_refused || []).length ? ` · it asked for ${r.asked_but_refused.length} more (refused by the budget)` : '');
    const film = (r.frames || []).map((f) => {
      const k = Number(f.t).toFixed(2);
      const tag = asked.has(k) ? 'asked for' : f.why === 'peak' ? 'motion peak'
        : f.why === 'turn' ? 'motion turn' : f.why === 'fill' ? 'most change' : 'even';
      return `<figure data-t="${k}" class="${asked.has(k) ? 'asked' : ''}" title="${esc(`${k} s · ${tag}`)}">
        <img loading="lazy" src="${esc(d.frames_url + f.file)}" alt="${esc(`${clip} at ${k} s`)}">
        <figcaption>${k}${asked.has(k) ? ' · asked' : ''}</figcaption></figure>`;
    }).join('');
    const bar = (r.frames || []).map((f) => `<i class="fr" style="left:${at(f.t)}"></i>`).join('')
      + (r.asked || []).map((t) => `<i class="ask" style="left:${at(t)}" title="it asked for ${Number(t).toFixed(2)} s"></i>`).join('')
      + (r.beats || []).map((b, i) => {
        const a = b.basis === 'inferred' ? b.between[0] : b.start;
        const z = b.basis === 'inferred' ? b.between[1] : b.end;
        return `<i class="${b.basis}" data-beat="${i}" style="left:${at(a)};width:${(100 * Math.max(0, z - a) / span).toFixed(2)}%" title="${esc(b.what)}"></i>`;
      }).join('');
    const list = (r.beats || []).map((b, i) => {
      const where = b.basis === 'seen' ? `seen @${Number(b.frame).toFixed(2)}`
        : `inferred ${Number(b.between[0]).toFixed(2)}→${Number(b.between[1]).toFixed(2)}`;
      return `<li class="${b.basis}" data-beat="${i}"><span class="b">${esc(where)}</span>${esc(b.what)}`
        + (b.why ? ` <span class="why">— ${esc(b.why)}</span>` : '') + '</li>';
    }).join('');
    const evs = (r.events || []).map((e) => `<span><i class="kind${['fall', 'crash', 'jump'].includes(e.kind) ? ' hot' : ''}">${esc(e.kind)}</i> ${e.start.toFixed(1)}–${e.end.toFixed(1)} s · ${esc(e.confidence)} · ${esc(e.what)}</span>`).join('');
    const cam = r.camera || {};
    // `bare`: the pass is keys, not buttons (test_floor_ui) — there a second click on the
    // deep span closes it instead.
    host.innerHTML = `<div class="dv-res-head">${esc(head)}${bare ? '' : ' <button type="button" class="dv-x" title="close the deep look">close</button>'}</div>
      <div class="dv-film">${film}</div>
      <div class="dv-beatbar" title="seen beats (solid) sit on their frame; inferred beats (hatched) span the two frames that bracket them">${bar}</div>
      <ol class="dv-beats">${list}</ol>
      ${evs ? `<div class="dv-ev">${evs}</div>` : ''}
      <div class="dv-cam">camera: ${esc(cam.mount || 'unknown')}${cam.roll ? ` · ${esc(cam.roll)}` : ''}${cam.evidence ? ` · ${esc(cam.evidence)}` : ''}</div>
      ${(r.unsure || []).length ? `<div class="dv-unsure">unsure: ${r.unsure.map(esc).join(' · ')}</div>` : ''}
      ${r.summary ? `<div class="dv-cam">${esc(r.summary)}</div>` : ''}`;
    const x = host.querySelector('.dv-x');
    if (x) x.addEventListener('click', (e) => { e.stopPropagation(); host.innerHTML = ''; });
    host.querySelectorAll('.dv-film figure').forEach((fig) => {
      fig.addEventListener('click', (e) => { e.stopPropagation(); fig.classList.toggle('big'); });
    });
    const light = (i, on) => {
      const b = (r.beats || [])[i];
      if (!b) return;
      const ts = b.basis === 'seen' ? [b.frame] : b.between;
      ts.forEach((t) => {
        const fig = host.querySelector(`.dv-film figure[data-t="${Number(t).toFixed(2)}"]`);
        if (fig) fig.classList.toggle('lit', on);
      });
      host.querySelectorAll(`[data-beat="${i}"]`).forEach((x) => x.classList.toggle('lit', on));
    };
    host.querySelectorAll('[data-beat]').forEach((x) => {
      x.addEventListener('mouseenter', () => light(Number(x.dataset.beat), true));
      x.addEventListener('mouseleave', () => light(Number(x.dataset.beat), false));
    });
  }

  // ---------------------------------------------------------------- the strip

  function legendLines(c) {
    const L = c.layers;
    const out = [];
    const h = L.heard;
    out.push(['heard', h.read
      ? `${h.utterances.length} utterance${h.utterances.length === 1 ? '' : 's'}, ${h.words} words each timed — ASR ${h.model ? esc(h.model) : 'model not recorded'}`
      : 'not listened to yet']);
    const co = L.coarse;
    if (co.read) {
      const cells = co.cols && co.rows ? co.cols * co.rows : null;
      out.push(['coarse', `a frame every ${co.interval_s} s`
        + (cells ? `, read ${cells} to a sheet (${co.cols}×${co.rows}${co.width ? `, ${co.width} px` : ', width not recorded'})` : '')
        + ` by ${esc(reader(co))}`
        + (co.frames_recorded ? '' : ' · frame times derived from the interval')]);
    } else out.push(['coarse', 'not looked at yet']);
    const cl = L.close;
    if (cl.read && cl.windows.length) {
      const lens = cl.windows.map((w) => w[1] - w[0]);
      const lo = Math.min(...lens), hi = Math.max(...lens);
      out.push(['close', `every ${cl.interval_s} s in ${cl.windows.length} window${cl.windows.length === 1 ? '' : 's'} of `
        + `${lo === hi ? lo.toFixed(0) : `${lo.toFixed(0)}–${hi.toFixed(0)}`} s`
        + (cl.width ? `, ${cl.width} px` : '') + ` by ${esc(reader(cl))}`]);
    } else out.push(['close', 'no close look yet']);
    const dp = L.deep;
    if (dp.read) {
      const models = [...new Set(dp.spans.map((s) => modelName(s.model)).filter(Boolean))].join(', ');
      out.push(['deep', `${dp.spans.length} span${dp.spans.length === 1 ? '' : 's'}: keyframes at motion changes (≤ ${dp.cap}, ${dp.width} px each, in order) — ${esc(models || 'model not recorded')} infers between them`]);
    } else {
      out.push(['deep', `none yet — keyframes at motion changes (≤ ${dp.cap}, ${dp.width} px), the deep model infers between · drag across the strip to choose up to ${dp.max_span_s} s`]);
    }
    const mo = L.motion;
    out.push(['motion', mo.cached ? `picture change at ${mo.hz} Hz — free, no model` : 'not measured yet (free; measured on the first deep look)']);
    return out;
  }

  function build(ctl) {
    const { host, cov, opts } = ctl;
    const dur = cov.duration || opts.duration || 1;
    ctl.dur = dur;
    const L = cov.layers;
    const lane = (name, inner, empty) => `<div class="dv-lane${name === 'motion' ? ' m' : ''}${inner ? '' : ' empty'}" data-lane="${name}" data-empty="${esc(empty || '')}">${inner}</div>`;
    const heard = L.heard.utterances.map(([a, b]) => `<i style="left:${pct(a, dur)};width:${pct(b - a, dur)}"></i>`).join('');
    const coarse = L.coarse.frames.map((t) => `<i style="left:${pct(t, dur)}"></i>`).join('');
    const close = L.close.windows.map(([a, b]) => `<i class="w" style="left:${pct(a, dur)};width:${pct(b - a, dur)}"></i>`).join('')
      + L.close.frames.map((t) => `<i class="f" style="left:${pct(t, dur)}"></i>`).join('');
    const deep = L.deep.spans.map((s, k) => `<i class="w" data-span="${k}" style="left:${pct(s.start, dur)};width:${pct(s.end - s.start, dur)}" title="deep look ${s.start.toFixed(1)}–${s.end.toFixed(1)} s — click to show it"></i>`
      + s.frames.map((t) => `<i class="f${(s.asked || []).some((a) => Math.abs(a - t) < 0.01) ? ' asked' : ''}" style="left:${pct(t, dur)}"></i>`).join('')).join('');
    let motion = '';
    const mv = L.motion.values || [];
    if (mv.length > 1) {
      const max = Math.max(...mv, 1e-6);
      const step = L.motion.step_s || 0.1;
      const pts = mv.map((v, i) => `${(1000 * Math.min(1, (i * step) / dur)).toFixed(1)},${(18 - 17 * v / max).toFixed(1)}`).join(' ');
      motion = `<svg viewBox="0 0 1000 18" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="#7a7a90" stroke-width="1" vector-effect="non-scaling-stroke"/></svg>`;
    }
    const marks = cov.unusable.map((u) => `<i class="st-unusable" style="left:${pct(u.start, dur)};width:${pct(u.end - u.start, dur)}" title="${esc(`unusable ${u.start.toFixed(1)}–${u.end.toFixed(1)}: ${u.why}`)}"></i>`).join('')
      + cov.moments.map((m) => `<i class="st-${esc(m.status || 'unseen')}" style="left:${pct(m.start, dur)};width:${pct(Math.max(0, m.end - m.start), dur)}" title="${esc(`${m.kind} ${m.start.toFixed(1)}–${m.end.toFixed(1)} · ${STATUS_WORD[m.status] || m.status} · ${m.source}\n${m.what}`)}"></i>`).join('');
    const keys = ['confirmed', 'deep', 'unseen', 'fine-only', 'contradicted'].filter((s) => cov.moments.some((m) => m.status === s));
    host.innerHTML = `<div class="dv-top"><span class="dv-title">HOW THE MACHINE SAW ${esc(stemOf(cov.clip).toUpperCase())} · ${esc(fmtT(dur))}</span></div>
      <div class="dv-wrap">
        <div class="dv-labels"><span>heard</span><span>coarse</span><span>close</span><span>deep</span><span class="m">motion</span><span>claims</span></div>
        <div class="dv-bar">
          ${lane('heard', heard, 'no speech heard')}
          ${lane('coarse', coarse, 'not looked at')}
          ${lane('close', close, 'no close look')}
          ${lane('deep', deep, opts.compact ? 'none yet' : 'none yet — drag to choose seconds')}
          ${lane('motion', motion, 'not measured')}
          ${lane('moments', marks, 'nothing claimed')}
          <div class="dv-markband" hidden></div>
          <div class="dv-sel" hidden></div>
          <div class="dv-headline" hidden></div>
          <div class="dv-tip" hidden></div>
        </div>
      </div>
      ${opts.compact ? '' : `<div class="dv-legend">${legendLines(cov).map(([k, v]) => `<span><span class="k">${k}</span>${v}</span>`).join('')}</div>`}
      ${keys.length ? `<div class="dv-keys">${keys.map((s) => `<span><i class="st-${s}"></i>${STATUS_WORD[s]}</span>`).join('')}</div>` : ''}
      <div class="dv-act"></div>
      <div class="dv-res"></div>`;
    ctl.bar = host.querySelector('.dv-bar');
    wire(ctl);
    paintMark(ctl);
    if (ctl.headT != null) ctl.head(ctl.headT);
    setSelection(ctl, ctl.sel || ctl.markRange, !ctl.sel);
    // A deep look already read inside the range on show shows itself — except in the
    // compact strip under the pass's tape, where it would take the picture's height.
    const r = ctl.sel || ctl.markRange;
    const hit = !opts.compact && r && L.deep.spans.find((s) => s.end > r[0] && s.start < r[1]);
    if (hit) renderResult(host.querySelector('.dv-res'), cov.clip, hit.start, hit.end);
  }

  function paintMark(ctl) {
    const band = ctl.host.querySelector('.dv-markband');
    if (!band) return;
    const m = ctl.markRange;
    band.hidden = !m;
    if (m) {
      band.style.left = pct(m[0], ctl.dur);
      band.style.width = pct(m[1] - m[0], ctl.dur);
    }
  }

  /* The seconds the button would look at: a drag on the strip, else the shot's range. */
  function setSelection(ctl, range, fromMark) {
    const sel = ctl.host.querySelector('.dv-sel');
    const act = ctl.host.querySelector('.dv-act');
    if (!sel || !act) return;
    ctl.sel = fromMark ? null : range;
    sel.hidden = !range || fromMark;
    if (range && !fromMark) {
      sel.style.left = pct(range[0], ctl.dur);
      sel.style.width = pct(range[1] - range[0], ctl.dur);
    }
    const key = range ? `${range[0].toFixed(2)}-${range[1].toFixed(2)}` : '';
    if (act.dataset.key === key) return;
    act.dataset.key = key;
    act.innerHTML = '';
    // The pass does not spend: there the strip shows, and Look deeper is the board's
    // (a shot's priced button) — four words say where, nothing to click.
    if (ctl.opts.compact) {
      act.innerHTML = '<span class="dv-info">Look deeper: on the board</span>';
      return;
    }
    if (!range || range[1] - range[0] < 0.2) {
      if (!ctl.opts.compact) {
        act.innerHTML = '<span class="dv-info">drag across the strip to choose seconds to look deeper at</span>';
      }
      return;
    }
    const lbl = document.createElement('span');
    lbl.className = 'dv-info';
    lbl.textContent = `${fromMark ? (ctl.opts.markLabel || 'this range') : 'chosen'} · ${range[0].toFixed(1)}–${range[1].toFixed(1)} s`;
    act.appendChild(lbl);
    lookButton(act, ctl.clip, range[0], range[1], {
      onResult: (a, b) => renderResult(ctl.host.querySelector('.dv-res'), ctl.clip, a, b),
    });
  }

  function wire(ctl) {
    const bar = ctl.bar;
    const tip = bar.querySelector('.dv-tip');
    const tAt = (x) => {
      const r = bar.getBoundingClientRect();
      return Math.max(0, Math.min(ctl.dur, ctl.dur * (x - r.left) / Math.max(1, r.width)));
    };
    let drag = null;
    bar.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      const w = e.target.closest('.dv-lane[data-lane=deep] i.w');
      if (w) {
        const s = ctl.cov.layers.deep.spans[Number(w.dataset.span)];
        const res = ctl.host.querySelector('.dv-res');
        if (s && ctl.opts.compact && res.innerHTML && Number(res.dataset.start) === s.start) {
          res.innerHTML = '';                       // a second click closes it
        } else if (s) {
          renderResult(res, ctl.clip, s.start, s.end, { bare: !!ctl.opts.compact });
        }
        return;
      }
      drag = { x: e.clientX, t0: tAt(e.clientX), moved: false };
      bar.setPointerCapture(e.pointerId);
      e.stopPropagation();
    });
    bar.addEventListener('pointermove', (e) => {
      const t = tAt(e.clientX);
      showTip(ctl, tip, t, e.clientX);
      if (!drag) return;
      if (!drag.moved && Math.abs(e.clientX - drag.x) < 4) return;
      if (ctl.opts.compact) return;          // the pass seeks; it does not spend
      drag.moved = true;
      let a = Math.min(drag.t0, t), b = Math.max(drag.t0, t);
      const max = ctl.cov.layers.deep.max_span_s || 20;
      if (b - a > max) { if (t > drag.t0) b = a + max; else a = b - max; }
      setSelection(ctl, [a, b], false);
    });
    const up = (e) => {
      if (!drag) return;
      const was = drag;
      drag = null;
      if (!was.moved && ctl.opts.onSeek) ctl.opts.onSeek(was.t0);
      e.stopPropagation();
    };
    bar.addEventListener('pointerup', up);
    bar.addEventListener('pointercancel', () => { drag = null; });
    bar.addEventListener('pointerleave', () => { tip.hidden = true; });
  }

  function showTip(ctl, tip, t, clientX) {
    const c = ctl.cov;
    const L = c.layers;
    const lines = [`<b>${fmtT(t)}</b>`];
    if (L.heard.utterances.some(([a, b]) => a <= t && t <= b)) lines.push('heard: speech');
    const cf = L.coarse.frames;
    if (cf.length) {
      const near = cf.reduce((p, x) => (Math.abs(x - t) < Math.abs(p - t) ? x : p), cf[0]);
      lines.push(`coarse: nearest frame ${fmtT(near)} (${Math.abs(near - t).toFixed(1)} s away)`);
    }
    const w = L.close.windows.find(([a, b]) => a <= t && t <= b);
    if (w) lines.push(`close: inside the window ${w[0].toFixed(1)}–${w[1].toFixed(1)} s, a frame every ${L.close.interval_s} s`);
    const d = L.deep.spans.find((s) => s.start <= t && t <= s.end);
    if (d) lines.push(`deep: ${d.frames.length} keyframes over ${d.start.toFixed(1)}–${d.end.toFixed(1)} s — ${esc(d.summary || '')}`);
    c.moments.filter((m) => m.start <= t + 0.05 && t - 0.05 <= m.end).slice(0, 4).forEach((m) => {
      lines.push(`<span>${esc(m.kind)} · ${esc(STATUS_WORD[m.status] || m.status)} · ${esc(m.source)}: ${esc(m.what)}</span>`);
    });
    c.unusable.filter((u) => u.start <= t && t <= u.end).forEach((u) => lines.push(`unusable: ${esc(u.why)}`));
    tip.innerHTML = lines.join('<br>');
    const r = ctl.bar.getBoundingClientRect();
    tip.style.left = `${Math.max(80, Math.min(r.width - 80, clientX - r.left))}px`;
    tip.hidden = false;
  }

  function strip(host, clip, opts = {}) {
    injectCss();
    let ctl = host._deep;
    if (ctl && ctl.clip === clip) {
      if (opts.mark) ctl.mark(opts.mark[0], opts.mark[1]);
      ctl.opts = { ...ctl.opts, ...opts };
      return ctl;
    }
    host.classList.add('dv');
    host.dataset.clip = clip;
    ctl = {
      host, clip, opts, cov: null, dur: opts.duration || 1, markRange: opts.mark || null,
      sel: null, headT: null, bar: null,
      head(t) {
        this.headT = t;
        const h = host.querySelector('.dv-headline');
        if (!h) return;
        h.hidden = t == null;
        if (t != null) h.style.left = pct(t, this.dur);
      },
      mark(a, b) {
        const same = this.markRange && Math.abs(this.markRange[0] - a) < 1e-3
          && Math.abs(this.markRange[1] - b) < 1e-3;
        this.markRange = [a, b];
        if (same || !this.cov) return;
        paintMark(this);
        if (!this.sel) setSelection(this, this.markRange, true);
      },
      async reload() {
        const cov = await coverage(clip);
        if (host._deep !== this || !cov) return;
        this.cov = cov;
        build(this);
      },
    };
    host._deep = ctl;
    live.add(ctl);
    host.innerHTML = `<div class="dv-title">HOW THE MACHINE SAW ${esc(stemOf(clip).toUpperCase())} · …</div>`;
    ctl.reload();
    return ctl;
  }

  // ---------------------------------------------------------------- the hooks

  /* The board's inspector: the selected shot's clip, with the shot's in/out marked. */
  function inspector(host, seg) {
    if (!host || !seg || !seg.clip || String(seg.clip).startsWith('gen_')) {
      if (host) { host.hidden = true; host._deep = null; host.innerHTML = ''; }
      return;
    }
    host.hidden = false;
    strip(host, seg.clip, { mark: [seg.in, seg.out], markLabel: 'this shot' });
  }

  /* How the machine saw a clip, as one line, in words honest to the sidecars: what was
   * heard, how often a frame was read, how many closer and deeper looks there are. */
  function lineWords(cov) {
    const L = cov.layers;
    const n = (k, one) => `${k} ${one}${k === 1 ? '' : 's'}`;
    const out = [];
    out.push(!L.heard.read ? 'not listened to yet'
      : L.heard.utterances.length ? 'every word heard' : 'no speech heard');
    out.push(L.coarse.read && L.coarse.interval_s ? `a frame every ${L.coarse.interval_s} s`
      : 'not looked at yet');
    if (L.close.read && L.close.windows.length) out.push(n(L.close.windows.length, 'close look'));
    if (L.deep.spans.length) out.push(n(L.deep.spans.length, 'deep look'));
    return out.join(' · ');
  }

  /* INTAKE M16 decision 9: "how the machine saw" is one line that opens per item and
   * closes on the next. The line goes into `host`; a click opens the whole strip under
   * it, in `host` (the range marked, the playhead live); a second click closes it. Every
   * call renders closed — a new item never inherits the last one's open strip. */
  function line(host, clip, opts = {}) {
    if (!host || !clip) return null;
    injectCss();
    const tok = {};
    host._line = tok;
    host._deep = null;
    host.dataset.clip = clip;
    host.innerHTML = '<div class="dv-line" role="button" tabindex="-1" title="how the machine saw this clip — click to open">…</div><div class="dv-more" hidden></div>';
    const ln = host.querySelector('.dv-line');
    const more = host.querySelector('.dv-more');
    tok.range = opts.range || null;
    let words = '';
    const say = () => { ln.textContent = `${words} ${more.hidden ? '▸' : '▾'}`; };
    coverage(clip).then((cov) => {
      if (host._line !== tok) return;
      words = cov ? lineWords(cov) : 'how the machine saw it: not known';
      say();
    });
    ln.addEventListener('click', (e) => {
      e.stopPropagation();
      if (more.hidden) {
        more.hidden = false;
        strip(more, clip, { mark: tok.range, markLabel: opts.markLabel || 'this range',
          onSeek: opts.onSeek, compact: !!opts.compact });
        if (tok.t != null) more._deep.head(tok.t);
      } else {
        more.hidden = true;
      }
      if (words) say();
    });
    tok.more = more;
    tok.head = (t) => { tok.t = t; if (!more.hidden && more._deep) more._deep.head(t); };
    tok.mark = (a, b) => { tok.range = [a, b]; if (!more.hidden && more._deep) more._deep.mark(a, b); };
    return tok;
  }

  /* The pass's old hook: the strip under its tape. */
  function floor(host, clip, range, onSeek) {
    if (!host || !clip) return;
    strip(host, clip, { mark: range, markLabel: 'the band', onSeek, compact: true });
  }

  function head(host, t) {
    if (!host) return;
    if (host._deep) host._deep.head(t);
    else if (host._line) host._line.head(t);
  }

  /* The open screen: a mini strip per card, from one request for the whole bin. */
  async function minis(root) {
    injectCss();
    const cards = [...(root || document).querySelectorAll('.card[data-clip]')];
    if (!cards.length) return;
    if (!binCov || Date.now() - binAt > 15000) {
      binAt = Date.now();
      binCov = fetch('/api/coverage').then((r) => (r.ok ? r.json() : null)).catch(() => null);
    }
    const d = await binCov;
    if (!d) return;
    cards.forEach((card) => {
      const c = d.clips[card.dataset.clip];
      if (!c) return;
      let el = card.querySelector('.dv-mini');
      if (!el) {
        el = document.createElement('div');
        el.className = 'dv-mini';
        card.appendChild(el);
      }
      const dur = c.duration || 1;
      const iv = c.coarse.interval_s;
      el.title = [
        iv ? `coarse: a frame every ${iv} s (${c.coarse.frames.length})` : 'coarse: not looked at',
        c.close.length ? `close: ${c.close.length} window${c.close.length === 1 ? '' : 's'} at 1 s` : 'close: none',
        c.deep.length ? `deep: ${c.deep.length} span${c.deep.length === 1 ? '' : 's'}` : 'deep: none',
        `${c.moments.length} claim${c.moments.length === 1 ? '' : 's'}`,
      ].join(' · ');
      el.innerHTML = c.coarse.frames.map((t) => `<i class="c" style="left:${pct(t, dur)}"></i>`).join('')
        + c.close.map(([a, b]) => `<i class="w" style="left:${pct(a, dur)};width:${pct(b - a, dur)}"></i>`).join('')
        + c.deep.map(([a, b]) => `<i class="d" style="left:${pct(a, dur)};width:${pct(b - a, dur)}"></i>`).join('')
        + c.moments.map((m) => `<i class="m st-${esc(m.status || 'unseen')}" style="left:${pct(m.start, dur)};width:${pct(Math.max(0, m.end - m.start), dur)}"></i>`).join('');
    });
  }

  /* A seen-tab row: a small priced button; the result opens under the row. The tab is
   * rebuilt after every save and when the run's job lands (the board reloads the
   * project), so an opened result is remembered by its span and reopens on the new row. */
  const openRows = new Map();          // `${clip}|${a}|${b}` -> [start, end] shown
  function rowButton(row, clip, start, end) {
    injectCss();
    const pad = 2.0;
    const a = Math.max(0, start - pad);
    const b = Math.max(a + 1, (end == null ? start + 3 : end) + pad);
    const key = `${clip}|${a.toFixed(2)}|${b.toFixed(2)}`;
    const holder = document.createElement('span');
    holder.className = 'dv-rowact';
    row.appendChild(holder);
    const show = (s, e) => {
      openRows.set(key, [s, e]);
      if (!row.parentNode) return;
      let res = row.nextElementSibling;
      if (!res || !res.classList.contains('dv-res')) {
        res = document.createElement('div');
        res.className = 'dv-res inrow';
        row.after(res);
      }
      renderResult(res, clip, s, e, { inrow: true });
    };
    lookButton(holder, clip, a, b, { small: true, onResult: show });
    // the row is appended by the caller after this returns
    if (openRows.has(key)) setTimeout(() => show(...openRows.get(key)), 0);
  }

  window.deep = { strip, line, inspector, floor, head, minis, rowButton, invalidate, modelName };
})();
