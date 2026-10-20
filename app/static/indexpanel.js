/* indexpanel.js: the Index panel (wave 17, Karl's K4; REDESIGN-GOAL dimensions 2, 3, 13, 16).
 *
 * Karl, 2026-10-19: "it is still not clear to me what indexing was run over the existing
 * footage, or how to re run / tune that". He chose a docked panel in the workspace, the way
 * Premiere keeps a clip's facts in its Metadata panel, docked beside the bin:
 *
 *   Window › Index, the Project group's fourth tab (it opens as wide as Selects, taking the
 *   width from the Timeline the same way). One line for the bin ("12 clips ·
 *   heard 12 · looked 10 at 4 s · close looks 3 · free on your plan"), then a dense list,
 *   one row per clip, a cell per stage — Heard, Looked (and the interval it looked at),
 *   Close looks, Telemetry, Preview — each saying done / held / queued / running % / failed
 *   / not run, with when it ran and what it cost in its hover and in the clip's detail
 *   under the list. Rows select like a bin's (click, Ctrl / Shift click), and take up the
 *   Project panel's selection when the tab opens.
 *
 *   The foot re-runs or re-tunes exactly that: the selection (or, with none, the bin),
 *   what to run again (hear, look — at an interval from the server's stops — close looks,
 *   telemetry, previews, everything), and the price of exactly that BEFORE anything runs
 *   (section S: "free on your plan" on the subscription, the API estimate in the hover).
 *   Run starts it (POST /api/index/rerun): the rows show it queued, running with its %,
 *   then done, and the header's job row carries it with its × (Stop, as the foot's Stop:
 *   POST /api/index/cancel; what finished is kept).
 *
 * Data: GET /api/index/clips (the journal's word on each stage, bent to the files), the
 * price from POST /api/index/price (locally first from the report's unit prices, then the
 * server's own quote, which is what the run spends).
 *
 * Contract (judge/WAVE17.md): window.rcIndex = { open(clips?), rerun(clips, opts), state() };
 * test hooks index-panel, index-row (data-clip), index-stage (data-stage, data-state),
 * index-rerun, index-interval, index-price.
 */
(function () {
  'use strict';
  // the panel's columns, in the order the index buys them
  const STAGES = [
    ['asr', 'Heard', 'Heard — speech and sound, on this machine (free)'],
    ['look', 'Looked', 'Looked at — contact sheets read by the model, one frame every N s (priced)'],
    ['close', 'Close', 'Close looks — the busiest seconds read again, frame by frame (priced)'],
    ['telemetry', 'Telemetry', 'Telemetry — the camera\'s motion sensors (free)'],
    ['proxy', 'Preview', 'Preview — the proxy the monitors play (free)'],
  ];
  const NAME = { asr: 'Heard', look: 'Looked at', close: 'Close looks', telemetry: 'Telemetry', proxy: 'Preview' };
  // what the foot runs again (the select's own words are short: the bar is one row)
  const WHAT = [
    ['asr', 'Hear', 'Hear again'], ['look', 'Look', 'Look again'],
    ['close', 'Close looks', 'Look closely again'], ['telemetry', 'Telemetry', 'Read the telemetry again'],
    ['proxy', 'Previews', 'Build the previews again'], ['everything', 'Everything', 'Everything again'],
    ['missing', 'What isn\'t done', 'Run only what is not done yet: nothing on disk is replaced'],
  ];
  const PRICED = new Set(['look', 'close']);
  const POLL_MS = 10000, FAST_MS = 1000;
  const IX = {
    rep: null, sig: '', sel: new Set(), anchor: null, focus: null, what: 'look', interval: null,
    quote: null, quoteKey: '', busy: false, err: '', timer: 0, qtimer: 0, mounted: false, job: null,
    lastJob: null,
  };

  const $ = (s, r) => (r || document).querySelector(s);
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
  const stem = (c) => String(c || '').replace(/\.[^.]+$/, '');
  const dollars = (x) => `$${Number(x || 0).toFixed(2)}`;
  const say = (m, ms) => { if (typeof window.toast === 'function') window.toast(m, ms || 3200); };
  const free = () => !!(IX.rep && IX.rep.charged === false);
  const terms = () => (IX.rep ? { charged: IX.rep.charged, backend: IX.rep.backend } : null);

  function when(t, long) {
    if (!t) return '';
    const d = new Date(Number(t) * 1000), now = new Date();
    const hm = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false });
    if (!long && d.toDateString() === now.toDateString()) return hm;
    const day = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric',
      ...(d.getFullYear() !== now.getFullYear() ? { year: 'numeric' } : {}) });
    return `${day} ${hm}`;
  }
  /* the list's Ran column: the time today, else the day */
  function shortWhen(t) {
    const d = new Date(Number(t) * 1000);
    if (d.toDateString() === new Date().toDateString()) return when(t);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }
  function clock(s) {
    s = Math.max(0, Math.round(Number(s) || 0));
    const m = Math.floor(s / 60);
    return `${m}:${String(s % 60).padStart(2, '0')}`;
  }
  /* a stage's money on this backend: free stages are free on both; a priced one is "free
   * on your plan" with the API estimate in the hover (section S), or its dollars */
  function costWords(usd, priced) {
    if (!priced || !(usd > 0)) return { text: priced ? (free() ? 'free' : dollars(0)) : 'free', title: priced ? '' : 'runs on this machine: no model call' };
    if (free()) return { text: 'free', title: `free on your Claude plan — about ${dollars(usd)} if using the API` };
    return { text: dollars(usd), title: `${dollars(usd)} billed to the API key` };
  }

  /* ------------------------------------------------------------ the data */

  async function getJSON(url, opts) {
    const r = await fetch(url, opts);
    let body = null;
    try { body = await r.json(); } catch (e) { body = null; }
    if (!r.ok) {
      const d = body && body.detail;
      throw new Error(typeof d === 'string' ? d : `${r.status}`);
    }
    return body;
  }

  async function refresh() {
    let rep;
    try {
      rep = await getJSON('/api/index/clips');
    } catch (e) {
      IX.err = `the index could not be read: ${e.message}`;
      paint();
      return;
    }
    IX.err = '';
    const was = IX.rep;
    IX.rep = rep;
    const live = new Set(rep.rows.map((r) => r.clip));
    [...IX.sel].forEach((c) => { if (!live.has(c)) IX.sel.delete(c); });
    if (IX.focus && !live.has(IX.focus)) IX.focus = null;
    if (IX.interval == null) IX.interval = null;            // the selection's own until chosen
    // a run of ours that ended: say what it did, once
    if (IX.lastJob && was && was.running && !rep.running) {
      try {
        const j = await getJSON(`/api/job/${IX.lastJob}`);
        if (j && j.detail) say(j.state === 'cancelled' ? 'Stopped — what finished is kept' : j.detail, 4500);
      } catch (e) { /* the rows say it */ }
      IX.lastJob = null;
    }
    paint();
    schedule();
  }

  function schedule() {
    clearTimeout(IX.timer);
    const running = !!(IX.rep && IX.rep.running);
    // shown: kept fresh (fast while a run goes); hidden: only a run of ours is followed,
    // to say what it did when it ends (the header's job row says the rest)
    if (!isShown() && !(running && IX.lastJob)) return;
    IX.timer = setTimeout(refresh, !isShown() ? 2 * FAST_MS : running ? FAST_MS : POLL_MS);
  }

  function isShown() {
    const sec = $('#projBody > section[data-tool=index]');
    return !!sec && !sec.hidden;
  }

  /* ------------------------------------------------------------ the scope and the price */

  function rows() { return (IX.rep && IX.rep.rows) || []; }
  function scopeClips() {
    const all = rows().filter((r) => !r.missing).map((r) => r.clip);
    const picked = all.filter((c) => IX.sel.has(c));
    return picked.length ? picked : all;
  }
  function wholeBin() { return !rows().some((r) => IX.sel.has(r.clip)); }
  const ALL = ['asr', 'look', 'close', 'telemetry', 'proxy'];
  function stagesOf(what) { return what === 'everything' || what === 'missing' ? ALL.slice() : [what]; }
  function wantsLook(what) { return what === 'look' || what === 'everything' || what === 'missing'; }
  /* The interval the foot offers: the one chosen, else the selection's own when its clips
   * share one, else none ("each clip's own"). Only one the editor chose is sent: a run left
   * at its default looks at each clip's own interval and re-tunes nothing. */
  function intervalShown() {
    if (IX.interval != null) return IX.interval;
    const sel = rows().filter((r) => scopeClips().includes(r.clip));
    const ivs = [...new Set(sel.map((r) => Number(r.interval_s)))];
    return ivs.length === 1 ? ivs[0] : null;
  }
  function body() {
    const b = { clips: wholeBin() ? null : scopeClips(), stages: stagesOf(IX.what) };
    if (IX.what === 'missing') b.only_missing = true;
    if (wantsLook(IX.what) && IX.interval != null) b.interval_s = IX.interval;
    return b;
  }
  /* The price from the report's own unit prices, at once (the server's quote follows and
   * wins): a look is its clip's sheets at the interval, a close look three windows. */
  function localQuote() {
    const rep = IX.rep;
    if (!rep) return null;
    const st = stagesOf(IX.what), chosen = intervalShown();
    let sheets = 0, looks = 0, closes = 0;
    const missing = IX.what === 'missing';
    const todo = (r, k) => !missing || !['done', 'skipped'].includes(r.stages[k] && r.stages[k].state);
    for (const r of rows()) {
      if (!scopeClips().includes(r.clip) || r.junk) continue;
      const wantClose = st.includes('close') && todo(r, 'close');
      const needLook = wantClose && !st.includes('look') && r.stages.look.state !== 'done';
      const iv = String(chosen != null ? chosen : Number(r.interval_s));
      if ((st.includes('look') && todo(r, 'look')) || needLook) { looks += 1; sheets += Number((r.sheets_at || {})[iv] || 1); }
      if (wantClose) closes += 1;
    }
    const usd = sheets * Number(rep.usd_per_sheet || 0) + closes * Number(rep.usd_per_close || 0);
    return { usd: Math.round(usd * 100) / 100, looks, sheets, close: closes, local: true };
  }

  function quoteKey() { return JSON.stringify(body()); }

  function requote() {
    clearTimeout(IX.qtimer);
    const key = quoteKey();
    if (IX.quote && IX.quoteKey === key && !IX.quote.local) return;
    IX.quote = localQuote();
    IX.quoteKey = key;
    paintFoot();
    IX.qtimer = setTimeout(async () => {
      try {
        const q = await getJSON('/api/index/price', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: key });
        if (IX.quoteKey === key) { IX.quote = q; paintFoot(); }
      } catch (e) {
        if (IX.quoteKey === key) { IX.quote = { error: e.message }; paintFoot(); }
      }
    }, 120);
  }

  function priceWords(q) {
    if (!q) return { text: '', title: '' };
    if (q.error) return { text: 'no price', title: q.error };
    if (!q.local && Array.isArray(q.clips) && !q.clips.length) {
      return { text: 'nothing to run', title: (q.notes && q.notes.join('; ')) || 'every stage asked is done on these clips' };
    }
    const parts = [];
    if (q.looks) parts.push(`${plural(q.looks, 'look')} · ${plural(q.sheets, 'sheet')}`);
    if (q.close) parts.push(plural(q.close, 'close look'));
    const what = parts.join(' + ');
    if (!(q.usd > 0)) {
      return { text: 'free', title: 'runs on this machine: no model call'
        + (q.notes && q.notes.length ? ` — ${q.notes.join('; ')}` : '') };
    }
    const tag = window.rcCost ? rcCost.tag(q.usd, terms()) : `~${dollars(q.usd)}`;
    const note = window.rcCost ? rcCost.note(q.usd, terms()) : `about ${dollars(q.usd)}`;
    return { text: tag, title: `${what}: ${note}` + (q.notes && q.notes.length ? ` — ${q.notes.join('; ')}` : '') };
  }

  /* ------------------------------------------------------------ painting */

  function mount() {
    const sec = $('#projBody > section[data-tool=index]');
    if (!sec || IX.mounted) return sec;
    IX.mounted = true;
    sec.innerHTML = `
      <div class="ix-sum" data-testid="index-summary"></div>
      <div class="ix-list" tabindex="0"><table class="plist ixt">
        <colgroup><col class="c-name">${STAGES.map(([k]) => `<col class="c-${k}">`).join('')}<col class="c-cost"><col class="c-ran"></colgroup>
        <thead><tr><th class="name">Name</th>${STAGES.map(([k, l, t]) => `<th class="stg" data-stage="${k}" title="${esc(t)}">${l}</th>`).join('')}<th class="num" title="what the model calls on this clip cost">Cost</th><th title="when its index last ran">Ran</th></tr></thead>
        <tbody></tbody></table></div>
      <div class="ix-detail" data-testid="index-detail"></div>
      <div class="ix-foot">
        <span class="ix-scope" data-testid="index-scope"></span>
        <select class="ix-what" data-testid="index-what" aria-label="what to run again" title="what to run again">${WHAT.map(([k, l, t]) => `<option value="${k}" title="${esc(t)}">${l}</option>`).join('')}</select>
        <select class="ix-iv" data-testid="index-interval" aria-label="look at one frame every" title="the look reads one frame every N seconds: finer sees more and costs more sheets"></select>
        <span class="ix-price" data-testid="index-price"></span>
        <span class="grow"></span>
        <button type="button" class="ix-run" data-testid="index-rerun">Run</button>
        <button type="button" class="ix-stop" data-testid="index-stop" hidden title="stop the index: what finished is kept">Stop</button>
      </div>`;
    sec.setAttribute('data-testid', 'index-panel');
    const tb = $('tbody', sec);
    tb.addEventListener('click', (e) => {
      const tr = e.target.closest('tr[data-clip]');
      if (tr) pick(tr.dataset.clip, e);
    });
    tb.addEventListener('dblclick', (e) => {
      const tr = e.target.closest('tr[data-clip]');
      if (tr && window.dock && dock.project && typeof dock.project.select === 'function') dock.project.select(tr.dataset.clip);
    });
    $('.ix-list', sec).addEventListener('click', (e) => {
      if (!e.target.closest('tr[data-clip]') && !e.target.closest('thead')) { IX.sel.clear(); IX.focus = null; paint(); requote(); }
    });
    $('.ix-list', sec).addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') {
        e.preventDefault();
        rows().forEach((r) => IX.sel.add(r.clip));
        paint(); requote();
      } else if (e.key === 'Escape') {
        IX.sel.clear(); IX.focus = null; paint(); requote();
      }
    });
    const what = $('.ix-what', sec);
    what.value = IX.what;
    what.addEventListener('change', () => { IX.what = what.value; paintFoot(); requote(); });
    const iv = $('.ix-iv', sec);
    iv.addEventListener('change', () => { IX.interval = iv.value === '' ? null : Number(iv.value); paintFoot(); requote(); });
    $('.ix-run', sec).addEventListener('click', () => runNow());
    $('.ix-stop', sec).addEventListener('click', () => stop());
    return sec;
  }

  function cell(r, k) {
    const v = r.stages[k] || {};
    const st = v.state || 'not run';
    let text = '', title = `${NAME[k]}: `;
    if (st === 'done') {
      text = k === 'look' ? `✓ ${Number(v.interval_s || 4)} s` : '✓';
      title += `done ${when(v.at, true)}`;
      if (k === 'look') title += ` · one frame every ${Number(v.interval_s || 4)} s${v.sheets ? ` · ${plural(v.sheets, 'sheet')}` : ''}`;
      if (k === 'close' && v.windows) title += ` · ${plural(v.windows, 'window')}`;
      if (k === 'asr' && r.words != null) title += ` · ${plural(r.words, 'word')}`;
      if (v.batch) title += ' · the rest of its batch is still being heard';
      if (PRICED.has(k)) title += ` · ${costWords(v.cost_usd, true).title || costWords(v.cost_usd, true).text}`;
      if (v.again === 'waiting') { text += ' ↻'; title += ' — asked again, not run yet (stopped): runs with the next index'; }
      if (v.again === 'failed') { text += ' !'; title += ` — running it again failed: ${v.error || 'failed'}`; }
    } else if (st === 'running') {
      text = v.pct != null ? `${v.pct}%` : (v.elapsed_s != null ? clock(v.elapsed_s) : '…');
      title += `running${v.elapsed_s != null ? ` for ${clock(v.elapsed_s)}` : ''}${v.pct != null ? ` (about ${v.pct}% by its usual time)` : ''}`;
    } else if (st === 'queued') {
      text = 'queued';
      title += 'queued: the index running now takes it';
    } else if (st === 'held') {
      text = 'held';
      title += `held — ${v.why || 'waits for you'}${PRICED.has(k) ? ' (priced: it runs when you ask)' : ''}`;
    } else if (st === 'failed') {
      text = 'failed';
      title += `failed${v.attempts ? ` after ${plural(v.attempts, 'try')}` : ''}: ${v.error || 'unknown'}`;
    } else if (st === 'skipped') {
      text = k === 'telemetry' ? 'none' : 'n/a';
      title += v.error || 'does not apply to this clip';
    } else if (st === 'missing') {
      text = '—';
      title += 'the file is gone';
    } else {
      text = '—';
      title += `not run${PRICED.has(k) ? ' — priced: it runs when you ask' : ''}`;
    }
    const cls = st.replace(/\s+/g, '-');
    return `<td class="stg s-${cls}" data-testid="index-stage" data-stage="${k}" data-state="${esc(st)}" title="${esc(title)}">${esc(text)}</td>`;
  }

  function rowCost(r) {
    return ['look', 'close'].reduce((a, k) => a + (r.stages[k] && r.stages[k].state === 'done' ? Number(r.stages[k].cost_usd || 0) : 0), 0);
  }
  function lastRan(r) {
    return Math.max(0, ...Object.values(r.stages).map((v) => (v.state === 'done' ? Number(v.at) || 0 : 0)));
  }

  function rowHtml(r) {
    const c = costWords(rowCost(r), true);
    const ran = lastRan(r);
    const sel = IX.sel.has(r.clip) ? ' sel' : '';
    const name = esc(stem(r.clip));
    const tuned = r.tuned ? `<span class="tuned" title="its next look reads one frame every ${Number(r.interval_s)} s (the bin: ${Number(IX.rep.interval_s)} s)">${Number(r.interval_s)} s</span>` : '';
    const junk = r.junk ? '<span class="junk" title="marked junk: its looks are skipped">junk</span>' : '';
    return `<tr class="row${sel}${r.missing ? ' missing' : ''}" data-clip="${esc(r.clip)}" data-testid="index-row">`
      + `<td class="name" title="${esc(r.clip)}"><span class="n">${name}</span>${tuned}${junk}</td>`
      + STAGES.map(([k]) => cell(r, k)).join('')
      + `<td class="num cost" title="${esc(c.title || 'no model call on this clip yet')}">${rowCost(r) > 0 ? esc(c.text) : '—'}</td>`
      + `<td class="ran" title="${esc(ran ? `last ran ${when(ran, true)}` : 'nothing indexed yet')}">${esc(ran ? shortWhen(ran) : '—')}</td></tr>`;
  }

  function summaryWords() {
    const rep = IX.rep;
    if (!rep) return '';
    const s = rep.summary || {};
    const at = Object.entries(s.looked_at || {}).sort((a, b) => b[1] - a[1]);
    const looked = !s.looked ? 'looked 0'
      : at.length === 1 ? `looked ${s.looked} at ${at[0][0]} s`
        : `looked ${s.looked} (${at.map(([k, n]) => `${n} at ${k} s`).join(', ')})`;
    const parts = [plural(s.clips || 0, 'clip'), `heard ${s.heard || 0}`, looked, `close looks ${s.close || 0}`];
    if (s.running) parts.push(`${s.running} running`);
    else if (s.queued) parts.push(`${s.queued} queued`);
    if (s.held) parts.push(`${s.held} held`);
    if (s.failed) parts.push(`${s.failed} failed`);
    const spent = Number(s.cost_usd || 0);
    parts.push(free() ? 'free on your plan' : `spent ${dollars(spent)}`);
    return parts.join(' · ');
  }

  function paintDetail(sec) {
    const el = $('.ix-detail', sec);
    const r = rows().find((x) => x.clip === IX.focus) || null;
    if (!r) {
      const rep = IX.rep;
      let t = '';
      if (rep && rep.paused_priced) t = `Looks paused${rep.paused_reason ? ` (${rep.paused_reason})` : ''}: a re-run you ask for here still runs.`;
      else if (rep && !rep.journal) t = 'Not indexed yet in this project: what is on disk is shown. Run first pass, or run a stage again below.';
      el.innerHTML = t ? `<div class="hint">${esc(t)}</div>` : '';
      el.hidden = !t;
      return;
    }
    el.hidden = false;
    const dur = r.duration != null ? clock(r.duration) : '';
    const head = `<div class="ix-dh"><b>${esc(stem(r.clip))}</b>${dur ? `<span>${dur}</span>` : ''}`
      + `<span title="the interval its next look reads at">next look: every ${Number(r.interval_s)} s${r.tuned ? ' (tuned)' : ''}</span></div>`;
    const lines = STAGES.map(([k]) => {
      const v = r.stages[k] || {};
      const st = v.state || 'not run';
      const bits = [];
      if (st === 'done') {
        bits.push(`✓ ${when(v.at, true)}`);
        if (k === 'look') bits.push(`every ${Number(v.interval_s || 4)} s`, v.sheets ? plural(v.sheets, 'sheet') : '', v.frames ? plural(v.frames, 'frame') : '');
        if (k === 'close' && v.windows) bits.push(plural(v.windows, 'window'));
        if (k === 'asr' && r.words != null) bits.push(plural(r.words, 'word'));
        if (k === 'telemetry' && r.telemetry_peaks != null) bits.push(plural(r.telemetry_peaks, 'peak'));
        if (PRICED.has(k)) { const c = costWords(v.cost_usd, true); bits.push(`<span title="${esc(c.title)}">${esc(c.text)}</span>`); }
        if (v.again === 'waiting') bits.push('asked again: runs with the next index');
        if (v.again === 'failed') bits.push(`<span class="bad">again failed: ${esc(v.error || '')}</span>`);
      } else if (st === 'running') {
        bits.push(`running${v.elapsed_s != null ? ` ${clock(v.elapsed_s)}` : ''}${v.pct != null ? ` · ~${v.pct}%` : ''}`);
      } else if (st === 'failed') {
        bits.push(`<span class="bad">failed: ${esc(v.error || '')}</span>`);
      } else if (st === 'held') {
        bits.push(`held — ${esc(v.why || 'waits for you')}`);
      } else if (st === 'skipped') {
        bits.push(esc(v.error || 'does not apply'));
      } else {
        bits.push(st);
      }
      return `<div class="ix-dl" data-stage="${k}"><span class="k">${NAME[k]}</span><span class="v">${bits.filter(Boolean).join(' · ')}</span></div>`;
    }).join('');
    el.innerHTML = head + lines;
  }

  function paintFoot() {
    const sec = $('#projBody > section[data-tool=index]');
    if (!sec || !IX.mounted) return;
    const rep = IX.rep;
    const n = scopeClips().length;
    const scope = $('.ix-scope', sec);
    const picked = !wholeBin();
    scope.textContent = !rep ? '' : picked ? (n === 1 ? stem(scopeClips()[0]) : plural(n, 'clip')) : `all ${plural(n, 'clip')}`;
    scope.title = picked ? 'the clips selected above (none selected: the whole bin)' : 'no clip selected: the whole bin';
    const iv = $('.ix-iv', sec);
    const stops = (rep && rep.intervals) || [4, 3, 2, 1];
    const shown = intervalShown();
    const key = `${stops.join(',')}|${shown == null ? 'own' : ''}`;
    if (iv.dataset.stops !== key) {
      iv.innerHTML = (shown == null ? '<option value="">each clip\'s own</option>' : '')
        + stops.map((s) => `<option value="${s}">every ${s} s</option>`).join('');
      iv.dataset.stops = key;
    }
    iv.value = shown == null ? '' : String(shown);
    iv.hidden = !wantsLook(IX.what);
    const pw = priceWords(IX.quote);
    const price = $('.ix-price', sec);
    price.textContent = pw.text;
    price.title = pw.title;
    price.dataset.usd = IX.quote && IX.quote.usd != null ? String(IX.quote.usd) : '';
    price.dataset.local = IX.quote && IX.quote.local ? '1' : '';
    const run = $('.ix-run', sec);
    const none = !!(IX.quote && !IX.quote.local && Array.isArray(IX.quote.clips) && !IX.quote.clips.length);
    run.disabled = !rep || IX.busy || !n || none || !!(rep && rep.stopping) || !!(IX.quote && IX.quote.error);
    const whatWord = (WHAT.find(([k]) => k === IX.what) || ['', '', ''])[2];
    run.title = `${whatWord} for ${scope.textContent}${pw.text ? ` — ${pw.text}` : ''}${pw.title ? ` (${pw.title})` : ''}`;
    const stopB = $('.ix-stop', sec);
    stopB.hidden = !(rep && rep.running);
    stopB.disabled = !!(rep && rep.stopping);
    stopB.textContent = rep && rep.stopping ? 'Stopping…' : 'Stop';
  }

  function paint() {
    const sec = mount();
    if (!sec) return;
    const rep = IX.rep;
    $('.ix-sum', sec).textContent = IX.err || summaryWords() || 'reading the index…';
    const s = (rep && rep.summary) || {};
    $('.ix-sum', sec).title = rep ? [
      summaryWords(),
      `telemetry ${s.telemetry || 0}${s.no_telemetry ? ` (${s.no_telemetry} without a sensor stream)` : ''} · previews ${s.previews || 0}`,
      free() ? `~${dollars(s.cost_usd)} if using the API · free on your plan` : `spent ${dollars(s.cost_usd)}`,
      rep.journal ? `journal: ${rep.path}` : 'no journal yet: the files on disk',
      rep.job_label ? `now: ${rep.job_label}` : ''].filter(Boolean).join('\n') : '';
    const sig = JSON.stringify([rep && rep.rows, [...IX.sel], IX.focus]);
    if (sig !== IX.sig) {
      IX.sig = sig;
      const list = $('.ix-list', sec);
      const top = list.scrollTop, left = list.scrollLeft;
      $('tbody', sec).innerHTML = rows().map(rowHtml).join('')
        || '<tr class="empty"><td colspan="8" class="hint">No footage in this bin yet: File › Import…</td></tr>';
      list.scrollTop = top;
      list.scrollLeft = left;
      paintDetail(sec);
    }
    paintFoot();
  }

  /* ------------------------------------------------------------ choosing rows */

  function pick(clip, e) {
    const names = rows().map((r) => r.clip);
    if (e && e.shiftKey && IX.anchor && names.includes(IX.anchor)) {
      const a = names.indexOf(IX.anchor), b = names.indexOf(clip);
      if (!(e.ctrlKey || e.metaKey)) IX.sel.clear();
      names.slice(Math.min(a, b), Math.max(a, b) + 1).forEach((c) => IX.sel.add(c));
    } else if (e && (e.ctrlKey || e.metaKey)) {
      if (IX.sel.has(clip)) IX.sel.delete(clip); else IX.sel.add(clip);
      IX.anchor = clip;
    } else {
      IX.sel = new Set([clip]);
      IX.anchor = clip;
    }
    IX.focus = IX.sel.has(clip) ? clip : (IX.sel.size ? [...IX.sel][IX.sel.size - 1] : null);
    IX.interval = null;
    paint();
    requote();
  }

  function select(clips) {
    IX.sel = new Set((clips || []).filter(Boolean));
    IX.focus = clips && clips.length ? clips[clips.length - 1] : null;
    IX.anchor = IX.focus;
    IX.interval = null;
  }

  // the Project panel's selection (Premiere's Metadata panel shows the bin's selected clip)
  document.addEventListener('click', (e) => {
    const row = e.target.closest && e.target.closest('#projList [data-key^="c|"]');
    if (!row) return;
    const clip = row.dataset.key.slice(2);
    if (e.ctrlKey || e.metaKey || e.shiftKey) {
      IX.sel.add(clip);
      IX.focus = clip;
    } else {
      select([clip]);
    }
    if (IX.mounted) { paint(); requote(); }
  }, true);

  /* ------------------------------------------------------------ running */

  async function rerun(clips, opts = {}) {
    const b = {
      clips: clips && clips.length ? clips : null,
      stages: opts.stages || (opts.what ? stagesOf(opts.what) : ['look']),
    };
    if (opts.interval_s != null) b.interval_s = Number(opts.interval_s);
    if (opts.only_missing || opts.what === 'missing') b.only_missing = true;
    const r = await getJSON('/api/index/rerun', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) });
    IX.lastJob = r.job;
    IX.job = r.job;
    await refresh();
    if (typeof window.pollJobs === 'function') { try { window.pollJobs(); } catch (e) { /* its own poll */ } }
    return r;
  }

  async function runNow() {
    if (IX.busy) return;
    IX.busy = true;
    paintFoot();
    const b = body();
    try {
      const r = await rerun(b.clips, { stages: b.stages, interval_s: b.interval_s, only_missing: b.only_missing });
      say(`${r.joined ? 'Added to the index running now' : 'Running'} — ${r.words}`, 3200);
      IX.interval = null;
    } catch (e) {
      say(`Could not run it: ${e.message}`, 5000);
    } finally {
      IX.busy = false;
      paintFoot();
      requote();
    }
  }

  async function stop() {
    const b = $('#projBody > section[data-tool=index] .ix-stop');
    if (b) { b.disabled = true; b.textContent = 'Stopping…'; }
    try {
      await getJSON('/api/index/cancel', { method: 'POST' });
    } catch (e) {
      say(`Could not stop it: ${e.message}`, 5000);
    }
    await refresh();
  }

  /* ------------------------------------------------------------ Run's hover card */

  /* Run is a model call when it looks, so it gets the AI hover card every AI control has
   * (tour.js `rcTour.card`; verdict 14, punch 6: it had a plain title): what it runs, what
   * it costs on this backend ("free on your plan" on the subscription with the API's
   * estimate beside it; the price itself, before anything runs, on the API) and what it
   * changes — what it replaces on those clips, and what it keeps (a look again keeps the
   * close looks read from the old look). From the price the foot shows (the server's
   * quote, `replaces` and `windows` with it, once it is in). */
  function who() {
    const n = scopeClips().length;
    if (!wholeBin() && n === 1) { const c = stem(scopeClips()[0]); return { name: c, poss: `${c}’s`, its: 'its' }; }
    if (wholeBin()) return { name: `all ${plural(n, 'clip')}`, poss: 'every clip’s', its: 'their' };
    return { name: plural(n, 'clip'), poss: `these ${n} clips’`, its: 'their' };
  }
  function runName() {
    const w = IX.what === 'missing' ? 'Run what isn’t done'
      : (WHAT.find(([k]) => k === IX.what) || ['', '', 'Run'])[2];
    return `${w} — ${who().name}`;
  }
  function runWhat() {
    const q = IX.quote || {}, w = who(), iv = intervalShown();
    const every = iv != null ? `one frame every ${iv} s` : 'each clip at its own interval';
    const sheets = q.sheets ? ` (${plural(q.sheets, 'sheet')})` : '';
    const windows = q.windows || (q.close ? q.close * 3 : 0);
    switch (IX.what) {
      case 'asr': return `Hears ${w.poss} sound again with the speech model, on this machine.`;
      case 'look': return `The model reads ${w.poss} contact sheets again, ${every}${sheets}.`;
      case 'close': return `The model reads ${w.poss} busiest seconds again, frame by frame${windows ? ` (${plural(windows, 'window')})` : ''}.`;
      case 'telemetry': return `Reads ${w.poss} motion sensors again, on this machine.`;
      case 'proxy': return `Builds ${w.poss} preview again (the proxy the monitors play), on this machine.`;
      case 'everything': return `Runs every stage again on ${w.name}: hears, looks (${every}${sheets}), looks closely, reads the telemetry and builds the previews.`;
      default: return `Runs only what is not done yet on ${w.name}: ${every} for a look it still needs.`;
    }
  }
  function runChanges() {
    const q = IX.quote || {}, w = who(), rep = q.replaces || null;
    const had = (k) => (rep ? Number(rep[k] || 0) > 0
      : rows().some((r) => scopeClips().includes(r.clip) && r.stages[k] && r.stages[k].state === 'done'));
    const one = !wholeBin() && scopeClips().length === 1 ? rows().find((r) => r.clip === scopeClips()[0]) : null;
    const iv = intervalShown();
    const was = one && one.stages.look && one.stages.look.state === 'done' ? Number(one.stages.look.interval_s || 4) : null;
    const tune = was != null && iv != null && was !== iv ? ` (the ${was} s look → ${iv} s)` : '';
    const ranked = `${w.its} picks are re-ranked`;
    let out;
    switch (IX.what) {
      case 'asr': out = `${had('asr') ? 'Replaces' : 'Adds'} ${w.poss} transcript; ${ranked}.`; break;
      case 'look':
        out = had('look')
          ? `Replaces ${w.poss} looked-at moments${tune}; ${ranked}; ${w.its} close looks are kept from the old look (Close looks reads them again).`
          : `Adds ${w.poss} looked-at moments; ${ranked}.`;
        break;
      case 'close':
        out = `${had('close') ? 'Replaces' : 'Adds'} ${w.poss} close looks; ${ranked}.`
          + ((q.notes || []).some((n) => /look comes first/.test(n)) ? ' A clip never looked at is looked at first, priced with it.' : '');
        break;
      case 'telemetry': out = `Replaces ${w.poss} telemetry summary.`; break;
      case 'proxy': out = `Replaces ${w.poss} preview; the picks do not change.`; break;
      case 'everything': out = `Replaces everything the index has on ${w.name} (transcript, looked-at moments, close looks, telemetry, preview); ${ranked}.`; break;
      default: out = 'Nothing on disk is replaced: only what is missing is added, and the picks are re-ranked where something new was read.';
    }
    return `${out} The cut does not change; Stop keeps what finished.`;
  }
  /* In every AI card's words (tour.js `costWords`, cli.js `rcCost.note`): "free on your
   * Claude plan — about $1.62 estimated cost if using the API" on the subscription, "about
   * $1.62, billed to the API key" on the API (the foot shows that price before Run). */
  function runPrice() {
    const q = IX.quote;
    if (!q) return { none: 'priced before it runs — reading the price…' };
    if (q.error) return { none: `no price: ${q.error}` };
    if (!q.local && Array.isArray(q.clips) && !q.clips.length) return { none: 'nothing to run: every stage asked is done on these clips' };
    if (!(q.usd > 0)) return { none: 'none — no model call' };
    return { usd: Number(q.usd), terms: terms() };
  }
  const RUN_CARD = { name: runName, what: runWhat, change: runChanges, price: runPrice };
  function registerCard() {
    if (!window.rcTour || typeof rcTour.card !== 'function') return false;
    rcTour.card('[data-testid="index-rerun"]', RUN_CARD);
    return true;
  }

  /* ------------------------------------------------------------ the contract */

  function open(clips) {
    if (Array.isArray(clips)) select(clips);
    else if (typeof clips === 'string') select([clips]);
    const sec = mount();
    const shown = !!sec && !sec.hidden;
    if (window.dock && typeof dock.open === 'function') dock.open('index');
    paint();
    // revealed, the observer reads it fresh (init); already shown, read it here
    if (shown || !('MutationObserver' in window)) refresh().then(requote);
    return true;
  }

  function state() {
    const rep = IX.rep;
    return {
      open: isShown(),
      rows: rows().map((r) => ({
        clip: r.clip, interval_s: r.interval_s, tuned: !!r.tuned,
        stages: Object.fromEntries(Object.entries(r.stages).map(([k, v]) => [k, v.state])),
        looked_at: r.stages.look && r.stages.look.state === 'done' ? r.stages.look.interval_s : null,
      })),
      selection: [...IX.sel], scope: scopeClips(), whole_bin: wholeBin(),
      what: IX.what, interval_s: intervalShown(),
      price: IX.quote ? { usd: IX.quote.usd, local: !!IX.quote.local, words: priceWords(IX.quote).text } : null,
      summary: summaryWords(), running: !!(rep && rep.running), job: rep ? rep.job : null,
    };
  }

  window.rcIndex = { open, rerun, state, refresh, select: (c) => { select(c); paint(); requote(); },
    /* Run's hover card's words now (what / cost / changes): for the suite */
    runCard: () => ({ name: runName(), what: runWhat(), change: runChanges(), price: runPrice() }) };

  function init() {
    if (!registerCard()) window.addEventListener('load', registerCard, { once: true });
    const sec = mount();
    // the tab shown, whichever way (its click, Window › Index, dock.open('index'), a tab
    // remembered from the last visit): read fresh
    if (sec && 'MutationObserver' in window) {
      new MutationObserver(() => { if (!sec.hidden) refresh().then(requote); })
        .observe(sec, { attributes: true, attributeFilter: ['hidden'] });
    }
    window.addEventListener('rc:clips-changed', () => { if (isShown()) refresh(); });
    if (isShown()) refresh().then(requote);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
