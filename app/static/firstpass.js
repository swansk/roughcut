/* firstpass.js: the first pass in the workspace (REDESIGN-GOAL dimension 2, Karl's K1).
 *
 * Karl, 2026-10-14: "it is not clear how to re run the first pass." The server knew
 * (GET /api/firstpass: where it stands, what a run would do, what it keeps, what it
 * costs) and nothing on the board showed it. This is its one home: a control in the
 * header, at every state —
 *
 *   never-run                 "Run first pass"
 *   running                   "First pass · 3/12" (clips heard and looked at), with ×
 *                             to stop; the full counts are in the hover and the panel
 *   stopped / failed          "Re-run first pass"; the panel says when, what finished
 *                             and what a re-run picks up (verdict 3: never "not run")
 *   done / stale-after-edits  "Re-run first pass" (a dot when the cut changed since)
 *
 * The running label stays short (verdict 3, punch 8): the header at 1440×900 holds it
 * beside Next and a job pill without an ellipsis.
 *
 * — and one click opens its panel: the steps a run takes now, each with its price on
 * this backend ("free on your plan", the API estimate in the hover), the shots a
 * re-run keeps exactly (the ones edited by hand, named) and how many it may replace,
 * then Run. The result is an ordinary Ask proposal: when it lands it opens in the
 * Assistant (app.js's `rcShowProposal`), with Play it / Accept / Discard as ever.
 *
 * The Timeline's and the Program monitor's empty states offer the same thing; they
 * and anyone else open the panel through `window.rcFirstPass.open()`.
 */
(function () {
  'use strict';
  const POLL_MS = 5000, FAST_MS = 1500;
  const FP = { s: null, open: false, keepMine: true, busy: false, watching: null,
               shown: null, timer: 0, err: '' };

  const $ = (sel, root) => (root || document).querySelector(sel);
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
  const stem = (c) => String(c || '').replace(/\.[^.]+$/, '');
  function clockS(t) {
    const v = Math.max(0, Number(t) || 0);
    const m = Math.floor(v / 60);
    return `${m}:${(v - m * 60).toFixed(1).padStart(4, '0')}`;
  }
  function ago(ts) {
    if (!ts) return '';
    const m = Math.round((Date.now() / 1000 - ts) / 60);
    if (m < 1) return 'just now';
    if (m < 120) return `${m} min ago`;
    if (m < 48 * 60) return `${Math.round(m / 60)} h ago`;
    return `${Math.round(m / 1440)} days ago`;
  }
  function say(msg, ms) {
    if (typeof window.toast === 'function') window.toast(msg, ms || 4000);
  }

  const ICON = '<svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" '
    + 'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 1.8l1.5 '
    + '3.6 3.7 1.1-3.7 1.2L8 11.3 6.5 7.7 2.8 6.5l3.7-1.1z"/><path d="M12.6 10.6l.6 1.5 1.5.6'
    + '-1.5.6-.6 1.5-.6-1.5-1.5-.6 1.5-.6z"/></svg>';

  /* ------------------------------------------------------------ words */

  function running(s) { return !!s && s.state === 'running'; }

  /* Clips heard and looked at: the proposal's prerequisite, so the running count. */
  function ready(c) {
    c = c || {};
    return Math.min(c.listened || 0, c.looked || 0, c.clips || 0);
  }

  /* The control's words. Running: a count the API gives, never a % it does not, and
   * short — the detail is in the hover and the panel. */
  function label(s) {
    if (!s) return 'Run first pass';
    if (running(s)) {
      const c = (s.counts && s.counts.index) || {};
      if (s.stage === 'picks') return 'First pass · picks';
      if (s.stage === 'cut') return 'First pass · the cut';
      return `First pass · ${ready(c)}/${c.clips || 0}`;
    }
    return s.state === 'never-run' ? 'Run first pass' : 'Re-run first pass';
  }

  /* "Stopped 3 min ago · heard 12/12 · looked 1/12" */
  function endedLine(s) {
    const what = s.state === 'stopped' ? 'Stopped' : 'Failed';
    return [`${what} ${ago(s.when)}`, s.finished].filter(Boolean).join(' · ');
  }

  function title(s) {
    if (!s) return 'The first pass: index the footage, rank the picks, propose a rough cut';
    if (running(s)) {
      const cut = s.counts && s.counts.cut;
      const step = s.stage === 'picks' ? 'ranking the picks'
        : s.stage === 'cut' ? 'writing the cut' : 'hearing and looking at every clip';
      return ['The first pass is running', step,
        s.counts && s.counts.index && s.counts.index.words,
        cut && cut.detail].filter(Boolean).join(' — ');
    }
    if (s.state === 'stale-after-edits') {
      return `${label(s)} — ${(s.stale || []).join(' · ') || 'the cut changed since'}`;
    }
    if (s.state === 'stopped' || s.state === 'failed') {
      return `${endedLine(s)}${s.error ? ` — ${s.error}` : ''}`
        + `${s.resumes ? `. A re-run ${s.resumes}.` : ''}`;
    }
    if (s.state === 'done') return `${label(s)} — last run ${ago(s.when)}`;
    return `${label(s)} — index the footage, rank the picks, propose a rough cut`;
  }

  /* What a re-run keeps, in one line: "Keeps 1 shot you edited by hand · may replace 3". */
  function keepLine(w) {
    const keep = (w && w.keep) || [], rep = (w && w.replace) || [];
    if (!keep.length && !rep.length) return 'No cut yet — it proposes a first one.';
    if (!FP.keepMine) {
      return `Keeps none of your shots · may replace all ${keep.length + rep.length}`;
    }
    return `Keeps ${plural(keep.length, 'shot')} you edited by hand · may replace ${rep.length}`;
  }

  /* Verdict 4: a re-run brought back a shot the editor had rejected. What it will not
   * propose again, in one line; the ranges on its hover. */
  function rejectedLine(w) {
    const rej = (w && w.rejected) || [];
    if (!rej.length) return '';
    const list = rej.map((r) => `${stem(r.clip)} ${clockS(r.in)}–${clockS(r.out)}`).join('\n');
    return `<div class="fp-keep fp-rejected" data-testid="firstpass-rejected" title="${esc(list)}">`
      + `Won't re-propose ${esc(plural(rej.length, 'range'))} you rejected</div>`;
  }

  function shotLine(r) {
    return `shot ${r.pos} · ${stem(r.clip)} ${clockS(r.in)}–${clockS(r.out)}`
      + (r.words ? ` · ${r.words}` : '');
  }

  /* ------------------------------------------------------------ the header control */

  function mount() {
    const host = document.getElementById('firstpass');
    if (!host) return null;
    if (!host.querySelector('.fp-btn')) {
      host.innerHTML = `<span class="fp-ctl">`
        + `<button type="button" class="fp-btn" data-testid="firstpass-button" aria-haspopup="dialog"`
        + ` aria-expanded="false">${ICON}<span class="fp-label">Run first pass</span><i class="fp-dot" hidden></i></button>`
        + `<button type="button" class="fp-x" data-testid="firstpass-cancel" hidden`
        + ` title="Stop the first pass now — what is finished is kept" aria-label="stop the first pass">×</button>`
        + `</span><div class="fp-panel" data-testid="firstpass-panel" role="dialog" aria-label="First pass" hidden></div>`;
      $('.fp-btn', host).addEventListener('click', (e) => { e.stopPropagation(); toggle(); });
      $('.fp-x', host).addEventListener('click', (e) => { e.stopPropagation(); cancel(); });
      $('.fp-panel', host).addEventListener('click', onPanelClick);
      document.addEventListener('mousedown', (e) => {
        if (FP.open && !host.contains(e.target) && !e.target.closest('[data-fp-open]')) close();
      });
      // Esc closes it — on keyup, since the timeline's keys take Escape's keydown first
      window.addEventListener('keyup', (e) => {
        if (e.key === 'Escape' && FP.open) close();
      }, true);
    }
    return host;
  }

  function paintControl() {
    const host = mount();
    if (!host) return;
    const s = FP.s;
    const btn = $('.fp-btn', host);
    $('.fp-label', host).textContent = label(s);
    btn.title = title(s);
    host.dataset.state = s ? s.state : 'unknown';
    $('.fp-dot', host).hidden = !(s && (s.state === 'stale-after-edits' || newClips(s).length));
    $('.fp-x', host).hidden = !running(s);
    btn.setAttribute('aria-expanded', FP.open ? 'true' : 'false');
    paintEmpties();
    paintOffer();
  }

  /* ------------------------------------------------------------ new footage (wave 12)
   * Footage imported after a pass (REDESIGN-GOAL dimension 13): "3 new clips: add them
   * to the cut?" under the control, until it is answered. "Re-run first pass…" opens the
   * panel — which says what a re-run keeps and what it costs before anything runs; "Not
   * now" puts this set of clips away (a new import asks again). */
  const OFFER_KEY = 'rc.fp.newclips';
  function newClips(s) {
    if (!s || running(s) || !(s.state === 'done' || s.state === 'stale-after-edits')) return [];
    if (Array.isArray(s.new_clips) && s.new_clips.length) return s.new_clips;
    const n = Number(s.new_count) || 0;
    return n > 0 ? Array.from({ length: n }, (_, i) => `#${i + 1}`) : [];
  }
  function offerKey(list) { return list.slice().sort().join('|'); }
  function dismissed(list) {
    try { return localStorage.getItem(OFFER_KEY) === offerKey(list); } catch (_) { return false; }
  }
  function paintOffer() {
    const host = mount();
    if (!host) return;
    const s = FP.s;
    const list = newClips(s);
    const show = list.length && !FP.open && !FP.busy && !dismissed(list);
    let box = host.querySelector('.fp-offer');
    if (!show) { if (box) box.hidden = true; return; }
    if (!box) {
      box = document.createElement('div');
      box.className = 'fp-offer';
      box.dataset.testid = 'firstpass-new-clips';
      box.setAttribute('role', 'status');
      box.innerHTML = '<span class="fp-offer-line"></span>'
        + '<button type="button" class="fp-offer-go" data-testid="firstpass-new-clips-go"'
        + ' title="Opens the first pass: it says which of your shots a re-run keeps exactly, and what it costs, before anything runs">Re-run first pass…</button>'
        + '<button type="button" class="fp-offer-no" data-testid="firstpass-new-clips-later">Not now</button>';
      box.querySelector('.fp-offer-go').addEventListener('click', (e) => {
        e.stopPropagation();
        try { localStorage.setItem(OFFER_KEY, offerKey(newClips(FP.s))); } catch (_) { /* private window */ }
        open();
      });
      box.querySelector('.fp-offer-no').addEventListener('click', (e) => {
        e.stopPropagation();
        try { localStorage.setItem(OFFER_KEY, offerKey(newClips(FP.s))); } catch (_) { /* private window */ }
        paintOffer();
      });
      host.appendChild(box);
    }
    const named = list.filter((c) => !c.startsWith('#'));
    const line = `${plural(list.length, 'new clip')}: add ${list.length === 1 ? 'it' : 'them'} to the cut?`;
    const span = box.querySelector('.fp-offer-line');
    if (span.textContent !== line) span.textContent = line;
    span.title = named.length ? named.join('\n') : '';
    box.hidden = false;
  }

  /* ------------------------------------------------------------ the panel */

  function stepRow(st, live) {
    const cost = st.cost || {};
    const ran = live && live.steps && live.steps[st.key];
    const state = ran ? ran.state : (st.needed === false ? 'skip' : 'todo');
    const mark = { done: '✓', skipped: '✓', running: '…', failed: '!', cancelled: '–' }[state] || '';
    return `<li class="fp-step is-${esc(state)}" data-step="${esc(st.key)}">`
      + `<span class="fp-sname">${mark ? `<b>${mark}</b> ` : ''}${esc(st.name)}</span>`
      + `<span class="fp-swill">${esc(st.will)}</span>`
      + `<span class="fp-scost" title="${esc(cost.hover || '')}">${esc(cost.words || '')}</span></li>`;
  }

  function statusLine(s) {
    if (running(s)) {
      const ix = s.steps && s.steps.index;
      const bg = ix && ix.background
        ? ' <span class="fp-note">Previews and close looks finish in the background.</span>' : '';
      return `Running — ${esc((s.counts && s.counts.index && s.counts.index.words) || 'starting')}.`
        + ' The picks and the cut start once every clip is heard and looked at.' + bg;
    }
    if (s.state === 'never-run') return 'Not run on this bin yet.';
    if (s.state === 'stopped' || s.state === 'failed') {
      const line = `${esc(endedLine(s))}.`;
      const why = s.state === 'failed'
        ? ` <span class="fp-bad">${esc(s.error || 'unknown error')}</span>` : '';
      const next = s.resumes
        ? ` <span class="fp-resume" data-testid="firstpass-resumes">Re-run first pass ${esc(s.resumes)}.</span>` : '';
      return `${line}${why}${next}`;
    }
    const p = s.proposal || {};
    const what = p.pending ? 'its proposal is waiting for you'
      : p.answer === 'discard' ? 'you discarded its proposal'
        : p.answer === 'accept' ? 'you accepted its proposal'
          : p.job ? 'its proposal was answered' : '';
    let out = `Last run ${esc(ago(s.when))}${what ? ` — ${what}` : ''}.`;
    if ((s.state === 'stale-after-edits' || newClips(s).length) && (s.stale || []).length) {
      out += ` <span class="fp-warn">${esc(s.stale.join(' · '))}.</span>`;
    }
    const fresh = newClips(s).filter((c) => !c.startsWith('#'));
    if (fresh.length) {
      out += ` <span class="fp-new" data-testid="firstpass-new-list">A re-run hears and looks at `
        + `${esc(fresh.length > 4 ? `${fresh.slice(0, 3).map(stem).join(', ')} and ${fresh.length - 3} more`
          : fresh.map(stem).join(', '))} and may use ${fresh.length === 1 ? 'it' : 'them'}.</span>`;
    }
    return out;
  }

  function paintPanel() {
    const host = mount();
    if (!host) return;
    const panel = $('.fp-panel', host);
    panel.hidden = !FP.open;
    if (!FP.open) return;
    const s = FP.s;
    if (!s) {
      panel.innerHTML = '<div class="fp-head"><b>First pass</b></div>'
        + '<div class="fp-status">reading where it stands…</div>';
      return;
    }
    const w = s.would || {};
    const live = running(s);
    const keep = w.keep || [];
    const steps = (w.steps || []).map((st) => stepRow(st, live ? s : null)).join('');
    const kept = FP.keepMine && keep.length
      ? `<ul class="fp-kept" data-testid="firstpass-kept">${keep.map((r) =>
        `<li data-shot="${esc(r.id)}">${esc(shotLine(r))}</li>`).join('')}</ul>` : '';
    const toggleKeep = keep.length && !live
      ? `<label class="fp-keepmine"><input type="checkbox" data-fp="keepmine"${FP.keepMine ? ' checked' : ''}>`
        + ' keep the shots I edited by hand, exactly</label>' : '';
    const p = s.proposal || {};
    const pending = !live && p.pending && p.job
      ? `<div class="fp-result">${esc(p.sentence || 'A proposed cut is waiting.')} `
        + '<button type="button" class="fp-link" data-fp="show">Show it</button></div>' : '';
    const cost = w.cost || {};
    const foot = live
      ? '<button type="button" class="fp-stop" data-fp="stop" data-testid="firstpass-stop">Stop</button>'
        + '<span class="fp-note">stops now; what is finished is kept</span>'
      : `<button type="button" class="fp-run" data-fp="run" data-testid="firstpass-run"`
        + `${FP.busy || w.error ? ' disabled' : ''} title="${esc(cost.hover || '')}">${esc(label(s))}</button>`
        + `<span class="fp-cost" title="${esc(cost.hover || '')}">${esc(cost.words || '')}</span>`
        + '<span class="fp-note">nothing changes until you accept the proposal</span>';
    panel.innerHTML = '<div class="fp-head"><b>First pass</b>'
      + '<span class="fp-sub">index · picks · a proposed rough cut</span>'
      + '<button type="button" class="fp-close" data-fp="close" aria-label="close">×</button></div>'
      + `<div class="fp-status">${statusLine(s)}</div>${pending}`
      + `<ol class="fp-steps">${steps}</ol>`
      + (w.error ? `<div class="fp-bad">${esc(w.error)}</div>` : '')
      + `<div class="fp-keep" data-testid="firstpass-keep">${esc(keepLine(w))}</div>${kept}${toggleKeep}`
      + rejectedLine(w)
      + (FP.err ? `<div class="fp-bad">${esc(FP.err)}</div>` : '')
      + `<div class="fp-foot">${foot}</div>`;
  }

  function onPanelClick(e) {
    e.stopPropagation();
    const t = e.target.closest('[data-fp]');
    if (!t) return;
    const what = t.dataset.fp;
    if (what === 'close') close();
    else if (what === 'run') run();
    else if (what === 'stop') cancel();
    else if (what === 'show') showResult(FP.s && FP.s.proposal && FP.s.proposal.job, true);
    else if (what === 'keepmine') { FP.keepMine = !!t.checked; paintPanel(); }
  }

  function open() {
    FP.open = true;
    FP.err = '';
    paintControl();
    paintPanel();
    refresh();
  }
  function close() {
    if (!FP.open) return;
    FP.open = false;
    paintControl();
    paintPanel();
  }
  function toggle() { if (FP.open) close(); else open(); }

  /* ------------------------------------------------------------ actions */

  async function run() {
    const s = FP.s;
    if (!s || FP.busy || running(s)) return;
    const w = s.would || {};
    FP.busy = true;
    FP.err = '';
    paintPanel();
    try {
      const r = await fetch('/api/firstpass', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ mode: w.mode, keep: FP.keepMine ? 'manual' : 'none' }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
      FP.watching = d.job;
      const k = (d.keep || []).length;
      say(`first pass started — ${k ? `keeping ${plural(k, 'shot')} you edited by hand` : 'nothing changes until you accept'}`);
    } catch (e) {
      FP.err = `could not start: ${e.message || e}`;
    } finally {
      FP.busy = false;
    }
    await loop();
  }

  async function cancel() {
    try {
      const r = await fetch('/api/firstpass/cancel', { method: 'POST' });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
      FP.watching = null;
      say('first pass stopped — what finished is kept');
    } catch (e) {
      say(`could not stop it: ${e.message || e}`, 5000);
    }
    await refresh();
  }

  /* The result is an Ask record: read it and show it in the Assistant. */
  async function showResult(job, asked) {
    if (!job) return;
    FP.shown = job;
    if (typeof window.rcPicksStale === 'function') window.rcPicksStale();   // its picks landed
    try {
      const d = await (await fetch(`/api/ask/${encodeURIComponent(job)}`)).json();
      if (!d.plan) return;
      // what the re-run keeps and replaces ("First pass: …") is the panel's own first
      // line now (app.js reads `firstpass.diff.sentence`), so a proposal that opens on
      // a reload says it too
      if (typeof window.rcShowProposal === 'function') window.rcShowProposal(d.plan);
      else if (window.dock) window.dock.open('ask');
      close();
      if (!asked) say('the first pass proposed a cut — Play it, Accept or Discard in the Assistant', 5000);
    } catch (e) { /* the board offers the last proposal on its own */ }
  }

  /* ------------------------------------------------------------ empty states */

  // The Timeline's and the Program monitor's empty states (punch 1): the same panel.
  function emptyButton(where) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'fp-empty-btn';
    b.dataset.fpOpen = where;
    b.dataset.testid = `firstpass-empty-${where}`;
    b.innerHTML = `${ICON}<span></span>`;
    b.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); open(); });
    return b;
  }
  function paintEmpties() {
    const words = running(FP.s) ? 'First pass running — see it' : label(FP.s);
    const cost = FP.s && FP.s.would && FP.s.would.cost;
    const tl = document.getElementById('tlEmpty');
    if (tl && tl.childElementCount) {
      let box = tl.querySelector('.fp-empty');
      if (!box) {
        box = document.createElement('div');
        box.className = 'fp-empty';
        box.appendChild(emptyButton('timeline'));
        const hint = document.createElement('span');
        hint.className = 'hint fp-empty-hint';
        box.appendChild(hint);
        (tl.querySelector('.empty') || tl).appendChild(box);
      }
      const span = $('button span', box);
      if (span.textContent !== words) span.textContent = words;
      const hint = $('.fp-empty-hint', box);
      const h = `index · picks · a proposed cut${cost && cost.words ? ` — ${cost.words}` : ''}`;
      if (hint.textContent !== h) hint.textContent = h;
      hint.title = (cost && cost.hover) || '';
    }
    // The Program monitor offers no second copy (wave 6, verdict 5 punch 9): the header and
    // the Timeline's empty box are the two ways in.
  }

  /* ------------------------------------------------------------ polling */

  async function refresh() {
    let s = null;
    try {
      const r = await fetch('/api/firstpass');
      if (r.ok) s = await r.json();
    } catch (e) { /* server away: keep the last answer */ }
    if (s) {
      const was = FP.s;
      FP.s = s;
      // a run this page saw running (or started) has landed: open its proposal
      if (((was && running(was)) || FP.watching) && !running(s)) {
        FP.watching = null;
        const p = s.proposal || {};
        if ((s.state === 'done' || s.state === 'stale-after-edits') && p.pending && p.job
            && FP.shown !== p.job) {
          showResult(p.job, false);
        } else if (s.state === 'failed') {
          say(`the first pass failed: ${s.error || 'unknown error'}`, 6000);
        }
      }
    }
    paintControl();
    paintPanel();
    return FP.s;
  }

  async function loop() {
    clearTimeout(FP.timer);
    await refresh();
    clearTimeout(FP.timer);
    FP.timer = setTimeout(loop, running(FP.s) || FP.watching ? FAST_MS : POLL_MS);
  }

  function init() {
    if (!mount()) return;
    paintControl();
    // the empty states are rebuilt by app.js on every render: put the offer back
    let soon = 0;
    const again = () => { if (!soon) soon = setTimeout(() => { soon = 0; paintEmpties(); }, 0); };
    ['tlEmpty'].forEach((id) => {
      const el = document.getElementById(id);
      if (el) new MutationObserver(again).observe(el, { childList: true });
    });
    // an edit on the board can make the pass stale: re-read when the window comes back
    window.addEventListener('focus', () => refresh());
    loop();
  }

  window.rcFirstPass = { open, close, toggle, refresh, run, cancel,
                         state: () => FP.s, label: () => label(FP.s) };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
