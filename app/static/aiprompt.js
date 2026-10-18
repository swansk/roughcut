/* The AI prompt (redesign wave 12, lane AIPROMPT; REDESIGN-GOAL dimension 14, Karl's K3 c).
 *
 * Karl, 2026-10-16: "there is no clear way … to run the AI tooling on this". Every AI
 * feature had its own home inside a panel you first had to find (the Assistant tab, the
 * Effect Controls' twirl-down, Designed Effects, Selects). This is the one way in, from
 * anywhere:
 *
 *   - the prompt: one always-visible input with a fixed home — the right half of the
 *     Timeline panel's header row (the app bar has no room for a usable field at 1440
 *     without pushing Next to an icon), where the edits it asks for land; the left half
 *     stays the panel's title and grab area, and the status line grows into it.
 *     Ctrl+J (⌘J) focuses it from anywhere (Notion's Ask-AI key; nothing in Premiere's
 *     default map or the board's own keys); ↵ asks, Esc leaves, ↑ ↓ recall earlier asks.
 *   - its scope, shown before it runs (the chip at its left): the selected blocks, else
 *     the sequence In/Out, else the Source clip while the Source monitor is the panel in
 *     use, else the whole sequence. A click on the chip changes it; a change to the
 *     selection or the marks puts it back to following them.
 *   - running: POST /api/assist {text, scope} → {job}, then GET /api/assist/{job} until
 *     it is done, failed or cancelled (judge/WAVE12.md; lane AIOPS's server). The
 *     status line beside the field says "Working" at once with the honest cost (the
 *     server's own words: GET /api/assist/price before, the job's estimate while it
 *     runs — "free on your plan" on the subscription backend, the API-equivalent
 *     estimate in the hover) and a Stop (POST /api/assist/{job}/cancel); when done one
 *     line says what came back ("4 changes ready · Review"): an op list goes to the
 *     review (window.rcActions.review, lane AIOPS), a whole-cut rewrite to the existing
 *     proposal once its Ask job is done (rcShowProposal, as the first pass's does), an
 *     answer in words to a small popover. A failure says what to do; once the review is
 *     answered the line says what landed.
 *   - the AI menu (index.html): `rcAI.command(name)` for each item, routed to the
 *     existing features — none reachable only from a panel you must first find.
 *   - "Ask AI about this…": a right-click on a timeline block, a Project panel clip, or
 *     the Source / Program monitor, by event delegation from here (no other file's
 *     listeners are touched; a menu someone else already opened wins).
 *
 * window.rcAI = { focus(text), ask(text, scope), command(name), askAbout(target), scope() }
 */
(function () {
  'use strict';

  const IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');
  const KEY = IS_MAC ? '⌘J' : 'Ctrl+J';
  const HIST_KEY = 'roughcut.ai.history';
  const HIST_MAX = 30;
  const POLL_MS = 600;
  // the longest that fits the field whole (a cut-off example reads as broken)
  const PLACEHOLDERS = ['Ask the AI: tighten the intro · warm shot 3 · dissolve 4→5',
    'Ask the AI: tighten the intro · warm shot 3', 'Ask the AI: tighten the intro', 'Ask the AI…'];
  const PLACEHOLDER = PLACEHOLDERS[0];
  const COLOUR_PREFIX = 'Colour: ';

  const $ = (s, root) => (root || document).querySelector(s);
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const stem = (c) => String(c || '').replace(/\.[^.]+$/, '');
  const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
  const secs = (t) => `${(Math.round(Math.max(0, Number(t) || 0) * 10) / 10).toFixed(1)}s`;
  const clock = (ms) => {
    const s = Math.max(0, Math.floor(ms / 1000));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  };
  const TL = () => (window.tl && tl.state ? tl : null);
  const tc = (t) => (TL() && typeof tl.tc === 'function' ? tl.tc(t) : secs(t));
  function say(msg, ms) { if (typeof window.toast === 'function') window.toast(msg, ms || 4000); }
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode: memory only */ } },
  };

  const ICON = '<svg class="aip-ico" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor"'
    + ' stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 1.8l1.5 3.6 3.7 1.1'
    + '-3.7 1.2L8 11.3 6.5 7.7 2.8 6.5l3.7-1.1z"/><path d="M12.6 10.6l.6 1.5 1.5.6-1.5.6-.6 1.5-.6-1.5'
    + '-1.5-.6 1.5-.6z"/></svg>';
  const CHEV = '<svg class="aip-chev" viewBox="0 0 10 10" aria-hidden="true" fill="none" stroke="currentColor"'
    + ' stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 4l2.5 2.5L7.5 4"/></svg>';

  const AI = {
    state: 'idle',          // idle | working | done | failed | stopped (Stop, or cancelled elsewhere)
    job: null, text: '', scope: null, t0: 0, timer: 0, tick: 0, gen: 0, planTimer: 0,
    result: null,           // the finished GET /api/assist/{job}, plus its job id
    error: '', hint: '',    // a failure's words, and what to do about it
    override: null, overrideKey: '',   // a scope chosen on the chip, until the auto one changes
    lastPanel: 'timeline',  // the panel in use before the prompt took the focus
    hist: store.get(HIST_KEY, []), histAt: -1, draft: '',
    price: null,            // GET /api/assist/price: {usd, charged, words, hover, …} before a run
    runCost: null,          // the running job's own estimate (GET /api/assist/{job} `cost`)
    stoppedGen: 0,          // the ask Stop was pressed on (its job id may not be back yet)
    back: null,             // the element to give the focus back to on Esc
    ready: false,
  };
  if (!Array.isArray(AI.hist)) AI.hist = [];

  /* ------------------------------------------------------------ scope */

  function playhead() { return TL() ? Number(tl.state.playhead) || 0 : 0; }

  function selectedIds() {
    const t = TL();
    if (!t || !t.state.sel || !t.state.sel.size) return [];
    const order = (t.state.segs || []).map((s) => s.id);
    return [...t.state.sel].filter((id) => order.includes(id))
      .sort((a, b) => order.indexOf(a) - order.indexOf(b));
  }

  function rangeScope() {
    const t = TL();
    if (!t || (t.state.seqIn == null && t.state.seqOut == null)) return null;
    const a = t.state.seqIn != null ? Number(t.state.seqIn) : 0;
    const b = t.state.seqOut != null ? Number(t.state.seqOut) : (typeof t.total === 'function' ? t.total() : a);
    return b > a ? { kind: 'range', in: a, out: b } : null;
  }

  function sourceScope() {
    const s = window.rcSource;
    if (!s || !s.clip) return null;
    const out = { kind: 'clip', clip: s.clip };
    if (s.inPoint != null) out.in = Number(s.inPoint);
    if (s.outPoint != null) out.out = Number(s.outPoint);
    return out;
  }

  /* What the scope would be with nothing chosen on the chip: the selection, else the
   * In/Out, else the Source clip while the Source monitor is in use, else the sequence. */
  function autoScope() {
    const ids = selectedIds();
    if (ids.length) return { kind: 'selection', ids };
    const r = rangeScope();
    if (r) return r;
    if (AI.lastPanel === 'source') {
      const s = sourceScope();
      if (s) return s;
    }
    return { kind: 'sequence' };
  }

  const keyOf = (s) => JSON.stringify(s || {});

  function scope() {
    const auto = autoScope();
    if (AI.override && AI.overrideKey !== keyOf(auto)) AI.override = null;   // it moved on
    const s = AI.override || auto;
    return { ...s, playhead: playhead() };
  }

  function setOverride(s) {
    AI.override = s ? { ...s } : null;
    AI.overrideKey = keyOf(autoScope());
    paintScope();
  }

  /* The chip's words (short) and its hover (whole), in the timeline's language (verdict
   * 9, punch 7: the chip read "KX_05" where the review called the same block "Shot 2"):
   * a selected block is "Shot 5 · CLIP_02" — its place in the cut, as the review and its
   * ghost lane number it, then its clip — several are "Shots 2–4" (or "Shots 2, 5"); the
   * sequence In/Out is its timecodes, "In/Out 00:00:12:04–00:00:16:04"; a Project panel
   * clip is its name, "CLIP_02", as its row reads; the Source clip says "Source:". */
  function shotNo(t, id) {
    const i = t && typeof t.indexOf === 'function' ? t.indexOf(id) : -1;
    return i >= 0 ? i + 1 : null;
  }
  function shotList(nums) {
    const n = nums.slice().sort((a, b) => a - b);
    const run = n.every((v, k) => k === 0 || v === n[k - 1] + 1);
    if (run) return `Shots ${n[0]}–${n[n.length - 1]}`;
    return n.length <= 3 ? `Shots ${n.join(', ')}` : plural(n.length, 'shot');
  }
  function scopeWords(s) {
    const t = TL();
    if (s.kind === 'selection') {
      const ids = s.ids || [];
      const segOf = (id) => (t && typeof t.byId === 'function' ? t.byId(id) : null);
      if (ids.length === 1) {
        const seg = segOf(ids[0]);
        const no = shotNo(t, ids[0]);
        const name = seg ? stem(seg.clip) : 'clip';
        return { text: no ? `Shot ${no} · ${name}` : name,
          title: `the selected shot: ${no ? `shot ${no} (${name})` : name}`
            + (seg ? `, ${tc(seg.in)}–${tc(seg.out)} of the clip` : '') };
      }
      const nums = ids.map((id) => shotNo(t, id)).filter((v) => v != null);
      const each = ids.map((id) => { const seg = segOf(id), no = shotNo(t, id);
        return seg && no ? `shot ${no} (${stem(seg.clip)})` : null; }).filter(Boolean);
      return { text: nums.length === ids.length && nums.length ? shotList(nums) : plural(ids.length, 'shot'),
        title: `the ${ids.length} selected shots${each.length ? `: ${each.join(', ')}` : ''}` };
    }
    if (s.kind === 'range') {
      return { text: `In/Out ${tc(s.in)}–${tc(s.out)}`,
        title: `the sequence In/Out: ${tc(s.in)} to ${tc(s.out)} (${secs(s.out - s.in)})` };
    }
    if (s.kind === 'clip') {
      const span = s.in != null && s.out != null ? `, its In/Out ${secs(s.in)}–${secs(s.out)}` : '';
      // the Source monitor's clip says so; a Project panel clip reads as its row does
      const src = window.rcSource && window.rcSource.clip === s.clip;
      return src ? { text: `Source: ${stem(s.clip)}`, title: `the Source clip ${stem(s.clip)}${span}` }
        : { text: stem(s.clip), title: `the clip ${stem(s.clip)} in the Project panel${span}` };
    }
    return { text: 'Sequence', title: 'the whole sequence' };
  }

  /* ------------------------------------------------------------ the price */

  // What an ask costs, for the line before it runs: the assist's own price (lane AIOPS,
  // GET /api/assist/price?text=&scope=), the prompt it would send priced at the role's
  // model — never a model call — worded by the server (`words` / `hover`): "free on your
  // plan" on the subscription backend (REDESIGN-GOAL S), the API estimate in the hover.
  // While it runs the job's own estimate (GET /api/assist/{job} `cost`) takes over; a
  // finished ask's measured `cost` is in the result line's hover.
  let priceGen = 0, priceTimer = 0, priceKey = '';
  async function fetchPrice(text, sc) {
    const q = `text=${encodeURIComponent(String(text == null ? '' : text).trim())}`
      + `&scope=${encodeURIComponent(JSON.stringify(sc || scope()))}`;
    if (q === priceKey && AI.price) return;
    const gen = ++priceGen;
    try {
      const r = await fetch(`/api/assist/price?${q}`);
      if (!r.ok || gen !== priceGen) return;
      const d = await r.json();
      if (d && typeof d === 'object' && gen === priceGen) { AI.price = d; priceKey = q; paintStatus(); paintTitle(); }
    } catch (e) { /* no price: the line says none, it never guesses */ }
  }
  // the words being typed and the scope move the price a little: asked again once they rest
  function priceSoon() {
    clearTimeout(priceTimer);
    priceTimer = setTimeout(() => fetchPrice(input ? input.value : '', scope()), 700);
  }

  function costOf(c) {
    const C = window.rcCost;
    if (!C) return { words: '', title: '' };
    if (c == null) c = AI.state === 'working' && AI.runCost ? AI.runCost : AI.price;
    if (c && typeof c === 'object' && c.words) return { words: String(c.words), title: String(c.hover || c.title || '') };
    const terms = c && typeof c === 'object' ? { ...(AI.price || {}), ...c } : (AI.price || {});
    const usd = typeof c === 'number' ? c
      : c && typeof c === 'object' ? (c.usd != null ? c.usd : c.projected_usd) : (AI.price && AI.price.usd);
    if (usd == null && !C.free(terms)) return { words: '', title: '' };
    return { words: C.tag(usd, terms), title: C.note(usd, terms) };
  }

  /* ------------------------------------------------------------ the DOM */

  let host = null, field = null, input = null, chip = null, status = null, statusHtml = '';

  function mount() {
    host = document.getElementById('aiPrompt');
    if (!host) return false;
    // the status line sits left of the field, so the field never moves when an ask starts
    host.innerHTML = '<div class="aip-status" data-testid="ai-status" data-state="idle" aria-live="polite" hidden></div>'
      + '<div class="aip-field" role="group" aria-label="Ask the AI">'
      + ICON
      + `<button type="button" class="aip-scope" data-testid="ai-scope" aria-haspopup="menu" aria-expanded="false"><span class="aip-scope-l">Sequence</span>${CHEV}</button>`
      + `<input type="text" class="aip-input" data-testid="ai-prompt-input" aria-label="Ask the AI"`
      + ` autocomplete="off" spellcheck="false" placeholder="${esc(PLACEHOLDER)}">`
      + `<kbd class="aip-key" aria-hidden="true">${esc(KEY)}</kbd>`
      + '</div>';
    field = $('.aip-field', host);
    input = $('.aip-input', host);
    chip = $('.aip-scope', host);
    status = $('.aip-status', host);
    paintTitle();
    field.addEventListener('mousedown', (e) => {
      if (e.target === field || e.target.closest('.aip-ico, .aip-key')) { e.preventDefault(); input.focus(); }
    });
    chip.addEventListener('click', (e) => { e.stopPropagation(); toggleScopeMenu(); });
    input.addEventListener('keydown', onInputKey);
    input.addEventListener('focus', () => { host.classList.add('focus'); paintStatus(); fetchPrice(input.value, scope()); });
    input.addEventListener('blur', () => { host.classList.remove('focus'); paintStatus(); });
    input.addEventListener('input', () => { host.classList.toggle('typed', !!input.value); AI.histAt = -1; priceSoon(); });
    status.addEventListener('click', onStatusClick);
    if ('ResizeObserver' in window) new ResizeObserver(fitPlaceholder).observe(input);
    paintScope();
    paintStatus();
    return true;
  }

  /* The field's hover: what it does, what it costs, what it changes (dimension 16). */
  function paintTitle() {
    if (!input) return;
    const c = costOf(null);
    const t = 'Ask the AI for any edit, in words. It works on the scope at its left and proposes'
      + ' edits you review with the same tools before anything changes'
      + (c.words ? ` · ${c.words}${c.title ? ` (${c.title})` : ''}` : '')
      + ` · ${KEY} from anywhere · ↵ ask · Esc leave · ↑ an earlier ask`;
    if (input.title !== t) input.title = t;
  }

  let measure = null;
  function fitPlaceholder() {
    if (!input) return;
    const room = input.clientWidth - 6;
    if (room <= 0) return;
    measure = measure || document.createElement('canvas').getContext('2d');
    measure.font = getComputedStyle(input).font;
    const fit = PLACEHOLDERS.find((p) => measure.measureText(p).width <= room) || PLACEHOLDERS[PLACEHOLDERS.length - 1];
    if (input.placeholder !== fit) input.placeholder = fit;
  }

  let scopeKey = '';
  function paintScope() {
    if (!chip) return;
    const s = scope();
    // a new scope is a new prompt to price (the playhead alone is not) — asked while the
    // prompt is in use; a focus asks anyway, so a selection click elsewhere costs nothing
    const k = keyOf({ ...s, playhead: 0 });
    if (k !== scopeKey) {
      const first = !scopeKey;
      scopeKey = k;
      if (!first && AI.ready && host && host.classList.contains('focus')) priceSoon();
    }
    const w = scopeWords(s);
    const l = $('.aip-scope-l', chip);
    if (l.textContent !== w.text) l.textContent = w.text;
    chip.dataset.kind = s.kind;
    const t = `The AI works on ${w.title}. Click to change it: the selected clips, the In/Out,`
      + ' the Source clip or the whole sequence.';
    if (chip.title !== t) chip.title = t;
  }

  function paintStatus() {
    if (!status) return;
    status.dataset.state = AI.state;
    let html = '';
    if (AI.state === 'working') {
      const c = costOf(null);
      html = '<i class="aip-spin" aria-hidden="true"></i>'
        + `<span class="aip-what" title="${esc(`“${AI.text}” — on ${scopeWords(AI.scope || { kind: 'sequence' }).title}`)}">Working <b class="aip-time">${clock(Date.now() - AI.t0)}</b></span>`
        + (c.words ? `<span class="aip-cost" title="${esc(c.title)}">${esc(c.words)}</span>` : '')
        + '<button type="button" class="aip-btn" data-ai="stop" data-testid="ai-stop" title="Stop waiting for this ask — nothing changes">Stop</button>';
    } else if (AI.state === 'done' && AI.result) {
      html = resultHtml(AI.result);
    } else if (AI.state === 'failed') {
      html = `<span class="aip-bad" title="${esc(AI.error)}">✕ Failed</span>`
        + '<button type="button" class="aip-link" data-ai="details" data-testid="ai-details">What to do</button>'
        + '<button type="button" class="aip-btn" data-ai="retry" title="Ask the same thing again">Retry</button>';
    } else if (AI.state === 'stopped') {
      html = '<span class="aip-dim">Stopped · nothing changed</span>'
        + '<button type="button" class="aip-x" data-ai="dismiss" aria-label="dismiss" title="dismiss">×</button>';
    } else if (host && host.classList.contains('focus')) {
      const c = costOf(null);
      html = `<span class="aip-dim">↵ ask${c.words ? ` · <span class="aip-cost" title="${esc(c.title)}">${esc(c.words)}</span>` : ''}</span>`;
    }
    // compared with what was last written, not innerHTML's re-serialisation: a needless
    // rewrite between a press and its release would swallow a click on Stop or Review
    if (statusHtml !== html) { status.innerHTML = html; statusHtml = html; }
    status.hidden = !html;
  }

  /* One line for what came back. Once the review is answered (lane AIOPS's panel fires
   * `rc:assist-answered`) it says what landed, and offers no second review of ops that
   * are already in the cut. A whole-cut rewrite is an Ask job of its own: "ready" only
   * once that job is done. */
  function resultLine(d) {
    if (d.kind === 'ops') {
      const n = (d.ops || []).length;
      if (!n) return { line: 'No changes needed', act: d.summary || d.text ? 'Why' : '', what: 'answer' };
      if (d.answered) {
        const a = d.answered;
        if (!a.accepted) return { line: 'Rejected — nothing changed', act: '', what: '' };
        const line = a.accepted === a.total ? `${plural(a.accepted, 'change')} applied` : `${a.accepted} of ${a.total} applied`;
        // Undo while the accept is still the last thing on the undo stack
        return { line, act: a.top ? 'Undo' : '', what: a.top ? 'undo' : '' };
      }
      return { line: `${plural(n, 'change')} ready`, act: 'Review', what: 'review' };
    }
    if (d.kind === 'plan') {
      return d.planReady ? { line: 'A new cut is ready', act: 'Review', what: 'plan' }
        : { line: 'Re-cutting the sequence', act: 'Show', what: 'plan' };
    }
    return { line: 'Answered', act: 'Show', what: 'answer' };
  }

  function resultHtml(d) {
    const r = resultLine(d);
    const c = d.cost != null ? costOf(d.cost) : { words: '', title: '' };
    const title = [d.summary, c.title].filter(Boolean).join(' — ');
    return `<span class="aip-result" data-testid="ai-result" data-kind="${esc(d.kind || '')}" title="${esc(title)}">`
      + `<span class="aip-ok" aria-hidden="true">✓</span>${esc(r.line)}`
      + (r.act ? `<span class="aip-sep">·</span><button type="button" class="aip-link" data-ai="${r.what}" data-testid="ai-result-open">${esc(r.act)}</button>` : '')
      + '</span><button type="button" class="aip-x" data-ai="dismiss" aria-label="dismiss" title="dismiss">×</button>';
  }

  function onStatusClick(e) {
    const b = e.target.closest('[data-ai]');
    if (!b) return;
    e.stopPropagation();
    const what = b.dataset.ai;
    if (what === 'stop') stop();
    else if (what === 'retry') run(AI.text, AI.scope);
    else if (what === 'details') showPop('error');
    else if (what === 'dismiss') { AI.state = 'idle'; AI.result = null; closePop(); paintStatus(); }
    else if (what === 'review') review(AI.result);
    else if (what === 'plan') showPlan(AI.result);
    else if (what === 'answer') showPop('answer');
    else if (what === 'undo') undoAccept();
  }

  /* ------------------------------------------------------------ floating bits */

  // The scope menu, the answer popover and the context menu hang on <body>, placed by
  // the field (fixed), so no panel's overflow clips them.
  function floating(cls, testid) {
    let el = document.querySelector(`.${cls.split(' ')[0]}`);
    if (!el) {
      el = document.createElement('div');
      el.className = cls;
      el.dataset.testid = testid;
      el.hidden = true;
      document.body.appendChild(el);
    }
    return el;
  }

  function place(el, r) {
    const vw = window.innerWidth, vh = window.innerHeight;
    el.style.left = `${Math.max(8, Math.min(r.left, vw - el.offsetWidth - 8))}px`;
    el.style.top = `${Math.max(8, Math.min(r.bottom + 4, vh - el.offsetHeight - 8))}px`;
  }

  function closeFloating() {
    document.querySelectorAll('.aip-scopemenu, .aip-ctx').forEach((m) => { m.hidden = true; });
    if (chip) chip.setAttribute('aria-expanded', 'false');
  }

  function toggleScopeMenu() {
    const m = floating('aip-scopemenu tl-ctx', 'ai-scope-menu');
    if (!m.hidden) { closeFloating(); return; }
    closeFloating();
    const cur = scope();
    const ids = selectedIds();
    const r = rangeScope();
    const src = sourceScope();
    const item = (key, label, on, dis, why) => `<button type="button" role="menuitemradio" data-scope="${key}"`
      + ` aria-checked="${on ? 'true' : 'false'}"${dis ? ` disabled title="${esc(why)}"` : ''}>`
      + `<span class="aip-check">${on ? '✓' : ''}</span><span>${esc(label)}</span>`
      + `${dis ? `<span class="k">${esc(why)}</span>` : ''}</button>`;
    m.innerHTML = '<div class="aip-mhead">The AI works on</div>'
      + item('selection', ids.length ? `Selected clips (${ids.length})` : 'Selected clips',
        cur.kind === 'selection', !ids.length, 'select clips first')
      + item('range', r ? `In/Out ${tc(r.in)} – ${tc(r.out)}` : 'Sequence In/Out',
        cur.kind === 'range', !r, 'mark I and O first')
      + item('clip', src ? `Source clip: ${stem(src.clip)}` : 'Source clip',
        cur.kind === 'clip', !src, 'open one in Source')
      + item('sequence', 'Whole sequence', cur.kind === 'sequence', false, '');
    m.setAttribute('role', 'menu');
    m.hidden = false;
    chip.setAttribute('aria-expanded', 'true');
    place(m, chip.getBoundingClientRect());
    m.onclick = (e) => {
      const b = e.target.closest('button[data-scope]');
      if (!b || b.disabled) return;
      const k = b.dataset.scope;
      setOverride(k === 'selection' ? { kind: 'selection', ids: selectedIds() }
        : k === 'range' ? rangeScope() : k === 'clip' ? sourceScope() : { kind: 'sequence' });
      closeFloating();
      input.focus();
    };
  }

  function closePop() {
    const p = document.querySelector('.aip-pop');
    if (p) p.hidden = true;
  }

  /* The answer in words, or a failure and what to do — under the field. */
  function showPop(what) {
    const p = floating('aip-pop', 'ai-answer');
    const head = what === 'error' ? 'The AI could not do that' : 'The AI answered';
    let body = '';
    if (what === 'error') {
      body = `<p class="aip-bad">${esc(AI.error)}</p><p>${esc(AI.hint)}</p>`;
    } else {
      const d = AI.result || {};
      body = `<p>${esc(d.text || d.summary || '')}</p>`;
    }
    p.innerHTML = `<div class="aip-phead">${ICON}<b>${esc(head)}</b>`
      + `<span class="aip-pq" title="${esc(AI.text)}">“${esc(AI.text)}”</span>`
      + '<button type="button" class="aip-x" data-pop="close" aria-label="close" title="close · Esc">×</button></div>'
      + `<div class="aip-pbody">${body}</div>`
      + '<div class="aip-pfoot">'
      + (what === 'error' ? '<button type="button" class="aip-btn" data-pop="retry">Retry</button>' : '')
      + '<button type="button" class="aip-btn" data-pop="follow">Ask a follow-up</button></div>';
    p.dataset.what = what;
    p.hidden = false;
    place(p, field.getBoundingClientRect());
    p.onclick = (e) => {
      const b = e.target.closest('[data-pop]');
      if (!b) return;
      if (b.dataset.pop === 'close') closePop();
      else if (b.dataset.pop === 'retry') { closePop(); run(AI.text, AI.scope); }
      else if (b.dataset.pop === 'follow') { closePop(); input.value = ''; host.classList.remove('typed'); input.focus(); }
    };
  }

  /* ------------------------------------------------------------ running */

  async function run(text, sc) {
    text = String(text == null ? '' : text).trim();
    if (!AI.ready) return false;
    if (!text) {
      input.focus();
      field.classList.remove('nudge');
      void field.offsetWidth;
      field.classList.add('nudge');
      return false;
    }
    if (AI.state === 'working') { say('the AI is still on your last ask — Stop it first'); return false; }
    closePop();
    closeFloating();
    const gen = ++AI.gen;
    AI.state = 'working';
    AI.text = text;
    AI.scope = sc ? { ...sc, playhead: sc.playhead != null ? sc.playhead : playhead() } : scope();
    AI.t0 = Date.now();
    AI.job = null;
    AI.result = null;
    AI.error = '';
    AI.runCost = null;
    paintStatus();                      // the working state, before anything goes out
    remember(text);
    clearInterval(AI.tick);
    AI.tick = setInterval(() => {
      const t = status && status.querySelector('.aip-time');
      if (t) t.textContent = clock(Date.now() - AI.t0);
    }, 500);
    let r, d;
    try {
      r = await fetch('/api/assist', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ text, scope: AI.scope }),
      });
      d = await r.json().catch(() => ({}));
    } catch (e) {
      return fail(gen, 'The board did not answer.', 'Check that Roughcut is still running, then Retry.');
    }
    if (gen !== AI.gen) {
      // Stop was pressed before the job's id came back: the server is told now, so the
      // model's answer is dropped there too (and nothing waits on it)
      if (AI.stoppedGen === gen && d && d.job) cancelJob(d.job);
      return false;
    }
    if (r.status === 404 || r.status === 405) {
      return fail(gen, 'This board has no AI prompt service yet.',
        'Use Sequence › Ask for a change, or the AI menu, until the board is updated.');
    }
    if (!r.ok || !d.job) {
      return fail(gen, String(d.detail || d.error || `the board answered ${r.status}`),
        'Change the words or the scope and try again.');
    }
    AI.job = d.job;
    poll(gen);
    return true;
  }

  async function poll(gen) {
    clearTimeout(AI.timer);
    if (gen !== AI.gen || AI.state !== 'working') return;
    let d = null;
    try {
      const r = await fetch(`/api/assist/${encodeURIComponent(AI.job)}`);
      d = await r.json().catch(() => null);
      if (r.status === 404) {
        fail(gen, 'The board lost this ask (it may have restarted).', 'Ask again.');
        return;
      }
    } catch (e) { d = null; }                  // a blip is not a failure: keep waiting
    if (gen !== AI.gen || AI.state !== 'working') return;
    if (d && d.status === 'done') { finish(gen, d); return; }
    if (d && d.status === 'cancelled') {
      // stopped somewhere else (another tab's Stop, the server): nothing will come
      stopTimers();
      AI.state = 'stopped';
      paintStatus();
      return;
    }
    if (d && d.cost && d.cost.words && (!AI.runCost || AI.runCost.words !== d.cost.words
        || AI.runCost.hover !== d.cost.hover)) {
      AI.runCost = d.cost;               // the job's own estimate, from the prompt it sent
      paintStatus();
    }
    if (d && d.status === 'failed') {
      fail(gen, String(d.error || d.summary || 'the model call failed'),
        'Try other words or a smaller scope, then Retry.');
      return;
    }
    const age = Date.now() - AI.t0;
    AI.timer = setTimeout(() => poll(gen), age > 15000 ? 1500 : POLL_MS);
  }

  function stopTimers() {
    clearTimeout(AI.timer);
    clearInterval(AI.tick);
  }

  function fail(gen, error, hint) {
    if (gen !== AI.gen) return false;
    stopTimers();
    AI.state = 'failed';
    AI.error = error;
    AI.hint = hint;
    paintStatus();
    return false;
  }

  /* POST /api/assist/{job}/cancel (lane AIOPS): the job reads `cancelled` at once and
   * whatever the model returns is dropped on the server. */
  function cancelJob(job) {
    fetch(`/api/assist/${encodeURIComponent(job)}/cancel`, { method: 'POST' }).catch(() => {});
  }

  function stop() {
    if (AI.state !== 'working') return;
    const job = AI.job;
    AI.stoppedGen = AI.gen;
    AI.gen++;
    stopTimers();
    AI.state = 'stopped';
    paintStatus();
    if (job) cancelJob(job);
  }

  function finish(gen, d) {
    if (gen !== AI.gen) return;
    stopTimers();
    AI.result = { ...d, job: AI.job, asked: AI.text, scope: AI.scope, planReady: d.plan_state === 'done' };
    if (d.answer && Array.isArray(d.answer.accepted)) {            // reviewed already (another tab)
      const n = d.answer.accepted.length;
      AI.result.answered = { accepted: n, rejected: (d.answer.rejected || []).length,
        total: n + (d.answer.rejected || []).length, label: '', top: false };
    }
    AI.state = 'done';
    paintStatus();
    const r = resultLine(AI.result);
    if (r.what === 'review') review(AI.result);
    else if (r.what === 'plan') showPlan(AI.result);
    else if (r.what === 'answer' && r.act) showPop('answer');
  }

  /* The review panel answered this ask: the line says what landed, and offers Undo while
   * the accept's entry is the top of the undo stack. */
  function onAnswered(e) {
    const d = e && e.detail;
    if (!d || !AI.result || AI.result.job !== d.job) return;
    const acc = (d.accepted || []).length, rej = (d.rejected || []).length;
    AI.result.answered = { accepted: acc, rejected: rej, total: acc + rej, label: d.label || '',
      top: !!(acc && d.committed) };
    paintStatus();
  }

  /* The undo stack moved: another entry on top takes the line's Undo away; that accept's
   * own undo (the line's Undo, Ctrl+Z, the header's ↶) puts the prompt back to idle. */
  function onUndoStack(ev) {
    const a = AI.result && AI.result.answered;
    if (!ev || !a || !a.top) return;
    if (ev.kind === 'undo' && ev.label === a.label) {
      AI.state = 'idle';
      AI.result = null;
      closePop();
      paintStatus();
    } else if (ev.kind === 'commit' || ev.kind === 'undo' || ev.kind === 'redo') {
      a.top = false;
      paintStatus();
    }
  }

  function undoAccept() {
    const a = AI.result && AI.result.answered;
    if (!a || !a.top || !TL() || typeof tl.undo !== 'function') return;
    tl.undo();
  }

  function review(d) {
    if (!d) return;
    const A = window.rcActions;
    if (A && typeof A.review === 'function') A.review(d);
    else say('the review panel did not load — reload the page');
  }

  /* A whole-cut rewrite is an Ask job (`plan_ask`) the server started with the model's
   * brief, and it takes its own time. Done: the proposal opens through the first pass's
   * path (firstpass.js showResult). Still running: lane AIOPS's review follows it ("reading
   * the footage for the new cut…") and opens the proposal when it lands, while this line
   * watches the same job to say when it is ready. */
  async function askState(id) {
    try { return await (await fetch(`/api/ask/${encodeURIComponent(id)}`)).json(); } catch (e) { return null; }
  }

  function openProposal(plan) {
    if (typeof window.rcShowProposal !== 'function') return false;
    window.rcShowProposal(plan);
    if (window.dock) dock.open('ask', { focus: false });
    return true;
  }

  async function showPlan(d) {
    if (!d || !d.plan_ask) { say('the new cut has no proposal to show'); return; }
    const a = await askState(d.plan_ask);
    if (AI.result !== d) return;
    if (a && a.state === 'done' && a.plan) {
      d.planReady = true;
      paintStatus();
      openProposal(a.plan);
      return;
    }
    if (a && (a.state === 'failed' || a.state === 'cancelled')) { planFailed(d, a); return; }
    if (!a) { say('could not read the proposal — try again'); return; }
    const A = window.rcActions;
    if (A && typeof A.review === 'function') A.review(d);
    else say('the new cut is still being made — it opens here when it is ready');
    followPlan(d);
  }

  function planFailed(d, a) {
    if (AI.result !== d) return;
    AI.state = 'failed';
    AI.error = `The re-cut did not finish: ${(a && (a.detail || a.error)) || 'no detail'}`;
    AI.hint = 'Try other words, or Sequence › Ask for a change.';
    paintStatus();
  }

  function followPlan(d) {
    clearTimeout(AI.planTimer);
    const step = async () => {
      if (AI.result !== d || d.planReady) return;
      const a = await askState(d.plan_ask);
      if (AI.result !== d) return;
      if (a && a.state === 'done' && a.plan) {
        d.planReady = true;
        paintStatus();
        // the review panel opens it while it follows the job; without one, this does
        const A = window.rcActions;
        if (!A || typeof A.review !== 'function') openProposal(a.plan);
        return;
      }
      if (a && (a.state === 'failed' || a.state === 'cancelled')) { planFailed(d, a); return; }
      AI.planTimer = setTimeout(step, 1000);
    };
    AI.planTimer = setTimeout(step, 1000);
  }

  /* ------------------------------------------------------------ the keys */

  function remember(text) {
    AI.hist = [text, ...AI.hist.filter((h) => h !== text)].slice(0, HIST_MAX);
    AI.histAt = -1;
    store.set(HIST_KEY, AI.hist);
  }

  function recall(dir) {
    if (!AI.hist.length) return;
    if (AI.histAt === -1) AI.draft = input.value;
    const at = Math.max(-1, Math.min(AI.hist.length - 1, AI.histAt + dir));
    AI.histAt = at;
    input.value = at === -1 ? AI.draft : AI.hist[at];
    host.classList.toggle('typed', !!input.value);
    const n = input.value.length;
    input.setSelectionRange(n, n);
  }

  function leave() {
    closeFloating();
    closePop();
    const back = AI.back;
    AI.back = null;
    input.blur();
    if (back && back.isConnected && back !== document.body && typeof back.focus === 'function') {
      try { back.focus({ preventScroll: true }); } catch (e) { /* fine */ }
    }
  }

  function onInputKey(e) {
    if (e.isComposing) return;
    if (e.key === 'Enter' && !e.shiftKey && !e.altKey) {
      e.preventDefault();
      e.stopPropagation();
      run(input.value);
      if (AI.state === 'working') { input.value = ''; host.classList.remove('typed'); }
    } else if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      leave();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      recall(1);
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      recall(-1);
    }
  }

  // Ctrl+J / ⌘J from anywhere: on window, capture, so a panel's own keys never see it.
  function onKey(e) {
    const mod = IS_MAC ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey;
    if (mod && !e.shiftKey && !e.altKey && e.code === 'KeyJ') {
      e.preventDefault();
      e.stopImmediatePropagation();
      focus();
      return;
    }
    if (e.key === 'Escape') {
      const pop = document.querySelector('.aip-pop');
      const menus = [...document.querySelectorAll('.aip-scopemenu, .aip-ctx')].some((m) => !m.hidden);
      if (menus) { closeFloating(); e.stopImmediatePropagation(); }
      else if (pop && !pop.hidden && document.activeElement !== input) { closePop(); e.stopImmediatePropagation(); }
    }
  }

  /* ------------------------------------------------------------ the public verbs */

  function focus(text) {
    if (!input) return false;
    const a = document.activeElement;
    if (a && a !== input && !host.contains(a)) AI.back = a;
    if (text != null) {
      input.value = String(text);
      host.classList.toggle('typed', !!input.value);
    }
    input.focus();
    const n = input.value.length;
    input.setSelectionRange(n, n);
    paintScope();
    return true;
  }

  /* {kind: 'segment' | 'clip' | 'range', id?, clip?, in?, out?} → the prompt, scoped to it. */
  function askAbout(target) {
    const t = target || {};
    if (t.kind === 'segment' && t.id != null) {
      const T = TL();
      // Premiere's right-click selects the clip under it
      if (T && typeof T.select === 'function' && !(T.state.sel && T.state.sel.has(t.id) && T.state.sel.size === 1)) {
        T.select([t.id], { source: 'click' });
      }
      setOverride({ kind: 'selection', ids: [t.id] });
    } else if (t.kind === 'clip' && t.clip) {
      const s = { kind: 'clip', clip: t.clip };
      if (t.in != null) s.in = Number(t.in);
      if (t.out != null) s.out = Number(t.out);
      setOverride(s);
    } else if (t.kind === 'range' && t.in != null && t.out != null) {
      setOverride({ kind: 'range', in: Number(t.in), out: Number(t.out) });
    } else {
      setOverride({ kind: 'sequence' });
    }
    return focus();
  }

  /* ------------------------------------------------------------ the AI menu */

  function waitFor(fn, ms) {
    return new Promise((resolve) => {
      const t0 = Date.now();
      const step = () => {
        let v = null;
        try { v = fn(); } catch (e) { v = null; }
        if (v || Date.now() - t0 > ms) resolve(v || null);
        else setTimeout(step, 40);
      };
      step();
    });
  }

  function flash(el) {
    if (!el) return;
    el.classList.remove('aip-flash');
    void el.offsetWidth;
    el.classList.add('aip-flash');
    setTimeout(() => el.classList.remove('aip-flash'), 1600);
  }

  const firstId = () => selectedIds()[0] || null;

  async function firstPass() {
    const fp = window.rcFirstPass;
    if (!fp) { say('the first pass did not load — reload the page'); return false; }
    let s = fp.state();
    if (!s && typeof fp.refresh === 'function') s = await fp.refresh();
    fp.open();
    // never run: the menu item said its price ("Run first pass · free on your plan"), so
    // it runs; any other state opens the panel, which says what a re-run keeps first
    if (s && s.state === 'never-run' && !(s.would && s.would.error) && typeof fp.run === 'function') {
      await fp.run();
    }
    return true;
  }

  async function lookDeeper(id) {
    id = id || firstId();
    if (!id) { say('select a clip in the Timeline first'); return false; }
    const T = TL();
    if (T && !(T.state.sel && T.state.sel.has(id))) T.select([id], { source: 'click' });
    if (window.dock) dock.open('inspect');
    // Effect Controls rebuilds for the new selection: wait for THIS shot's Look deeper
    // (app.js keys its host by the shot's clip and range), never the last shot's
    const seg = T && typeof T.byId === 'function' ? T.byId(id) : null;
    const key = seg ? `${seg.clip}|${seg.in}|${seg.out}` : null;
    const hostOf = () => [...document.querySelectorAll('#inspector .lookhost')]
      .find((h) => h.isConnected && (!key || h.dataset.key === key));
    const host = await waitFor(hostOf, 3000);
    const box = host && host.closest('details.assist');
    if (!box) { say('the clip\'s Effect Controls did not open'); return false; }
    if (!box.open) box.open = true;
    const b = await waitFor(() => { const h = hostOf(); return h && h.querySelector('.dv-look'); }, 4000);
    if (!b) { say('Look deeper is not offered for this clip'); return false; }
    // it is disabled until its price is on it (a disabled button takes no focus)
    await waitFor(() => !b.disabled || !b.isConnected, 4000);
    b.scrollIntoView({ block: 'nearest' });
    b.focus({ preventScroll: true });
    flash(b);
    say('Look deeper: its price is on the button — click it to look', 4000);
    return true;
  }

  async function openFx() {
    if (window.dock) dock.open('fx');
    if (!firstId()) say('select a clip in the Timeline, then describe the effect you want', 5000);
    const el = await waitFor(() => document.querySelector('#fxNote') || document.querySelector('#fxAdd'), 2500);
    if (el && el.id === 'fxAdd') el.click();
    const note = await waitFor(() => document.querySelector('#fxNote'), 1500);
    if (note) { note.focus(); flash(note); }
    return !!note;
  }

  function openFind() {
    if (window.dock) dock.open('bin');
    const q = document.querySelector('#findQ');
    if (!q) return false;
    q.focus();
    q.select();
    flash(q);
    say('type the moment you want and press ↵ — "Not it? Ask the model" has the AI search for it', 5000);
    return true;
  }

  function auditState() {
    const b = document.querySelector('#auditClaims');
    if (!b) return { ok: false, why: 'the claims audit is not on this board' };
    if (!b.disabled) return { ok: true, why: '' };
    const info = document.querySelector('#auditInfo');
    return { ok: false, why: (info && info.textContent.trim()) || 'nothing to check now' };
  }

  function openAudit() {
    const a = auditState();
    if (window.dock) dock.open('bin');
    if (!a.ok) { say(`nothing to audit: ${a.why}`); return false; }
    const row = document.querySelector('#auditRow');
    if (row && getComputedStyle(row).display === 'none') row.style.display = 'flex';
    const b = document.querySelector('#auditClaims');
    b.scrollIntoView({ block: 'nearest' });
    b.focus({ preventScroll: true });
    flash(b);
    say('the audit\'s price is on its button — click it to run', 4000);
    return true;
  }

  const COMMANDS = {
    prompt: () => focus(),
    firstpass: () => firstPass(),
    ask: () => { setOverride({ kind: 'sequence' }); return focus(); },
    shot: () => {
      const id = firstId();
      if (!id) { say('select a clip in the Timeline first'); return false; }
      return askAbout({ kind: 'segment', id });
    },
    colour: () => focus(input && input.value.startsWith(COLOUR_PREFIX) ? input.value : COLOUR_PREFIX),
    fx: () => openFx(),
    deep: () => lookDeeper(),
    find: () => openFind(),
    audit: () => openAudit(),
  };

  function command(name) {
    const f = COMMANDS[name];
    return f ? f() : false;
  }

  // what each item does, when nothing else (lane TEACH's hovers) has said it
  const MENU_HOVER = {
    prompt: `Type what you want in plain words; the AI proposes edits you review before anything changes · ${KEY}`,
    firstpass: 'Index the footage, rank the picks and propose a rough cut — nothing changes until you accept it',
    ask: 'The AI prompt on the whole sequence: a change to the cut in words, reviewed before it lands',
    shot: 'The AI prompt on the selected clip: say what should change in it',
    colour: 'The AI prompt for a colour change on the selection, or the whole sequence',
    fx: 'Designed Effects: describe an effect for the selected clip; the AI designs it and you accept it',
    deep: 'A close look at the selected clip\'s frames; its price is on its button and nothing runs until you click it',
    find: 'Find a moment in the footage by words; "Not it? Ask the model" has the AI search for it',
    audit: 'A close look at the pass\'s unchecked jump / fall / crash claims; its price is on its button',
  };

  /* Each time the AI menu opens: the first pass's words for where it stands, its price,
   * and the items that need a selection greyed with the reason. */
  function paintMenu() {
    const menu = document.querySelector('#menubar .menu[data-menu=ai]');
    if (!menu) return;
    const set = (name, label, k, dis, why) => {
      const b = menu.querySelector(`button[data-cmd="ai:${name}"]`);
      if (!b) return;
      const html = `<span class="aip-ml">${esc(label)}</span>${k ? `<span class="k">${esc(k)}</span>` : ''}`;
      if (b.innerHTML !== html) b.innerHTML = html;
      if (!b.title && !('aipTitle' in b.dataset) && MENU_HOVER[name]) b.title = MENU_HOVER[name];
      if (dis) {
        if (!('aipTitle' in b.dataset)) b.dataset.aipTitle = b.title;
        b.title = why;
      } else if ('aipTitle' in b.dataset) {
        b.title = b.dataset.aipTitle;
        delete b.dataset.aipTitle;
      }
      b.disabled = !!dis;
    };
    const sel = selectedIds().length;
    const fp = window.rcFirstPass && window.rcFirstPass.state ? window.rcFirstPass.state() : null;
    const fcost = fp && fp.would && fp.would.cost ? fp.would.cost.words : '';
    const flabel = !fp || fp.state === 'never-run' ? 'Run first pass'
      : fp.state === 'running' ? 'First pass running — show it…' : 'Re-run first pass…';
    set('prompt', 'Ask the AI…', KEY);
    set('firstpass', flabel, fp && fp.state === 'never-run' ? fcost : '');
    set('ask', 'Ask for a change to the cut…', '');
    set('shot', 'Ask about the selected clip…', sel ? '' : 'select a clip', !sel, 'select a clip in the Timeline first');
    set('colour', 'Ask for a colour change…', '');
    set('fx', 'Design an effect…', 'Designed Effects');
    set('deep', 'Look deeper at the selected clip…', sel ? '' : 'select a clip', !sel, 'select a clip in the Timeline first');
    set('find', 'Find a moment…', 'Selects');
    const a = auditState();
    set('audit', 'Check the pass\'s claims…', a.ok ? '' : 'nothing to check', !a.ok, a.why);
  }

  /* ------------------------------------------------------------ "Ask AI about this…" */

  function targetOf(e) {
    const el = e.target instanceof Element ? e.target : null;
    if (!el) return null;
    if (el.closest('#aiPrompt, .aip-pop, .tl-ctx, input, textarea, [contenteditable]')) return null;
    // a V1 block, or its linked sound on A1 (the same shot)
    const blk = el.closest('#tl .blk[data-id], #tl .ablk[data-id]');
    if (blk) return { what: 'clip', target: { kind: 'segment', id: blk.dataset.id }, deep: true };
    const proj = el.closest('#pProject section[data-tool="project"] [data-clip]');
    if (proj) {
      const t = { kind: 'clip', clip: proj.dataset.clip };
      if (proj.dataset.in != null && proj.dataset.out != null) { t.in = Number(proj.dataset.in); t.out = Number(proj.dataset.out); }
      return { what: 'clip', target: t };
    }
    if (el.closest('#pSource #srcScreen, #pSource #srcBar')) {
      const s = sourceScope();
      return s ? { what: 'clip', target: s } : null;
    }
    if (el.closest('#pProgram .screen')) {
      const T = TL();
      const at = T && typeof T.shotAt === 'function' ? T.shotAt(playhead()) : null;
      return at ? { what: 'clip', target: { kind: 'segment', id: at.id }, deep: true }
        : { what: 'sequence', target: { kind: 'sequence' } };
    }
    return null;
  }

  function onContext(e) {
    if (e.defaultPrevented) return;           // a menu someone else opened (transitions, the Effects bin)
    const hit = targetOf(e);
    if (!hit) return;
    e.preventDefault();
    closeFloating();
    const m = floating('aip-ctx tl-ctx', 'ai-context-menu');
    m.setAttribute('role', 'menu');
    const deep = hit.deep && hit.target.kind === 'segment' && !!window.deep;
    m.innerHTML = `<button type="button" role="menuitem" data-ctx="ask" data-testid="ai-ctx-ask">${ICON}`
      + `<span>Ask AI about this${hit.what === 'sequence' ? ' sequence' : ''}…</span><span class="k">${esc(KEY)}</span></button>`
      + (deep ? '<button type="button" role="menuitem" data-ctx="deep" data-testid="ai-ctx-deep">'
        + '<span class="aip-noico"></span><span>Look deeper…</span></button>' : '');
    m.hidden = false;
    m.style.left = `${Math.max(8, Math.min(e.clientX, window.innerWidth - m.offsetWidth - 8))}px`;
    m.style.top = `${Math.max(8, Math.min(e.clientY, window.innerHeight - m.offsetHeight - 8))}px`;
    m.onclick = (ev) => {
      const b = ev.target.closest('button[data-ctx]');
      if (!b) return;
      closeFloating();
      if (b.dataset.ctx === 'ask') askAbout(hit.target);
      else lookDeeper(hit.target.id);
    };
  }

  /* ------------------------------------------------------------ hover cards */

  // Lane TEACH's AI hover cards (tour.js `rcTour.card`): what the context menu's items do,
  // cost and change, in section S's words.
  const CARDS = [
    ['[data-testid="ai-ctx-ask"]', {
      name: 'Ask AI about this',
      what: 'Points the AI prompt at this clip, block or monitor and puts the cursor in it: type what'
        + ' should change, in plain words.',
      change: 'Nothing by itself; the ask comes back as edits you review one by one, and nothing changes'
        + ' until you accept.',
      // what the ask will cost, from GET /api/assist/price (free on the subscription)
      price: () => (AI.price && typeof AI.price.usd === 'number' ? { usd: AI.price.usd, terms: AI.price } : null),
    }],
    ['[data-testid="ai-ctx-deep"]', {
      name: 'Look deeper',
      what: 'Opens this shot’s Look deeper, which reads a stretch of the clip frame by frame to see what the'
        + ' first look missed.',
      change: 'Nothing until you press Look deeper there (its price is on its button); it adds to what the'
        + ' AI knows about the clip, and the cut does not change.',
    }],
  ];
  function registerCards() {
    const T = window.rcTour;
    if (!T || typeof T.card !== 'function') return false;
    CARDS.forEach(([sel, spec]) => T.card(sel, spec));
    return true;
  }

  /* ------------------------------------------------------------ init */

  function init() {
    if (!mount()) return;
    AI.ready = true;
    // the panel in use, from the pointer: the prompt's own clicks do not count (the
    // Source monitor's clip stays the scope while you type about it)
    document.addEventListener('pointerdown', (e) => {
      const el = e.target instanceof Element ? e.target : null;
      if (!el || el.closest('#aiPrompt, .aip-scopemenu, .aip-ctx, .aip-pop')) return;
      const p = el.closest('.ws-panel');
      if (p && p.dataset.panel) AI.lastPanel = p.dataset.panel;
      closeFloating();
      closePop();
      setTimeout(paintScope, 0);
    }, true);
    window.addEventListener('keydown', onKey, true);
    document.addEventListener('contextmenu', onContext);
    window.addEventListener('blur', closeFloating);
    window.addEventListener('resize', () => { closeFloating(); closePop(); });
    window.addEventListener('rcsource', paintScope);
    window.addEventListener('rc:assist-answered', onAnswered);
    if (window.tl && typeof tl.on === 'function') {
      tl.on('select', paintScope);
      tl.on('markers', paintScope);
      tl.on('change', paintScope);
      tl.on('change', onUndoStack);
    }
    // the menu's words follow the board each time it opens
    const menu = document.querySelector('#menubar .menu[data-menu=ai]');
    if (menu) {
      new MutationObserver(() => { if (menu.classList.contains('open')) paintMenu(); })
        .observe(menu, { attributes: true, attributeFilter: ['class'] });
      paintMenu();
    }
    // the keys sheet's AI group (dock.js renders window.rcExtraKeys)
    window.rcExtraKeys = Array.isArray(window.rcExtraKeys) ? window.rcExtraKeys : [];
    window.rcExtraKeys.push(
      [KEY, 'the AI prompt: ask for any edit in words', 'ai'],
      ['↵ Esc ↑', 'in the AI prompt: ask · leave · an earlier ask', 'ai'],
      ['right-click', 'a clip, a block or a monitor: Ask AI about this…', 'ai'],
    );
    // the scope follows what changes without an event (playback, a Source mark): cheap
    setInterval(paintScope, 1000);
    fetchPrice();
    if (!registerCards()) window.addEventListener('load', registerCards, { once: true });
  }

  window.rcAI = {
    focus,
    ask: (text, sc) => run(text, sc),
    command,
    askAbout,
    scope,
    state: () => ({ state: AI.state, job: AI.job, text: AI.text, scope: AI.scope, result: AI.result,
      error: AI.error }),
    stop,
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
