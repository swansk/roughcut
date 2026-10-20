/* The walk-through and the AI hovers (redesign wave 12, lane TEACH; REDESIGN-GOAL
 * dimension 16, Karl's K3 e: "this whole thing needs to be easy to learn how to use,
 * especially how to use those AI features").
 *
 * 1. The walk-through. On a browser's first open of the board (no `roughcut.tour` in
 *    localStorage) a callout walks the loop in six steps ON the real workspace: import
 *    footage, run the first pass, why a pick was made, review the proposed cut, ask the AI,
 *    export. Each step is a small dark callout beside the real control, with that control
 *    outlined; Next / Back / Skip, step dots. Esc closes what Show me opened (a dialog, a
 *    menu, the first pass's panel) and never ends the walk-through: only Skip tour does
 *    (verdict 10: an Esc to clear the AI prompt ended it). Nothing is locked or dimmed: the
 *    workspace stays usable under it. "Show me" opens the thing (the Import dialog, the
 *    first pass's panel, Selects, the AI prompt…) and never starts a model call or spends
 *    anything itself. A step whose control is not on screen (a closed panel, a control
 *    another module has not drawn) points at the menu that opens it. Help › Learn Roughcut…
 *    replays it; AI › Learn the AI features walks the AI half alone (`start('ai')`).
 *    The suite's browsers set the flag in an init script (app/tests/conftest.py,
 *    tools/journey.py), so the first run never covers what another test clicks.
 *
 * 2. AI hovers. Every AI control gets a card after ~500 ms (Premiere's tooltip timing):
 *    what it does, what it costs on this backend (cli.js's `rcCost`: "free on your Claude
 *    plan — about $0.27 estimated cost if using the API" on the subscription, REDESIGN-GOAL
 *    S) and what it will change ("nothing until you accept"). Attached here by selector,
 *    by event delegation, so no other module's file changes; a module can add its own
 *    with `rcTour.card(selector, {name, what, change, price})`.
 *
 * 3. `[data-rc-import]` anywhere (the empty Timeline, Program and Source) opens Import
 *    (`window.rcImport.open()`, File › Import's own).
 *
 *   window.rcTour = { start(which), active, stop(), card(sel, spec) }
 */
(function () {
  'use strict';

  const KEY = 'roughcut.tour';            // set once the walk-through has been shown
  const HOVER_MS = 500;                   // Premiere's tooltip delay
  const WARM_MS = 400;                    // a card shown this recently: the next one at once

  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const seen = () => { try { return localStorage.getItem(KEY); } catch (e) { return 'unknown'; } };
  const remember = (v) => { try { localStorage.setItem(KEY, v); } catch (e) { /* private mode: fine */ } };
  /* eslint-disable no-undef -- app.js's top-level bindings (classic scripts share them),
     read by name and guarded so this file is inert without the board */
  const appSegs = () => { try { return typeof segs !== 'undefined' && Array.isArray(segs) ? segs : []; } catch (e) { return []; } };
  const appPlan = () => { try { return typeof pendingPlan !== 'undefined' ? pendingPlan : null; } catch (e) { return null; } };
  const appPrice = (mode) => { try { return typeof askPrice !== 'undefined' && askPrice ? askPrice[mode] : null; } catch (e) { return null; } };
  const appFirstMode = () => { try { return typeof firstMode === 'function' ? firstMode() : 'first'; } catch (e) { return 'first'; } };
  /* eslint-enable no-undef */
  const fpState = () => (window.rcFirstPass && typeof rcFirstPass.state === 'function' ? rcFirstPass.state() : null);

  const SPARK = '<svg class="hc-ai" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor"'
    + ' stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 1.8l1.5 3.6 3.7 1.1'
    + '-3.7 1.2L8 11.3 6.5 7.7 2.8 6.5l3.7-1.1z"/></svg>';

  /* ------------------------------------------------------------ what is on screen */

  /* The part of `el` a person can see: its box cut by every scrolling or clipping
   * ancestor and by the window. null when nothing of it shows. */
  function seenRect(el) {
    if (!el || !el.isConnected || !el.getClientRects().length) return null;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none') return null;
    const r = el.getBoundingClientRect();
    let x0 = r.left, y0 = r.top, x1 = r.right, y1 = r.bottom;
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      const ps = getComputedStyle(p);
      if (ps.display === 'none' || ps.visibility === 'hidden' || ps.opacity === '0') return null;
      if (ps.overflowX !== 'visible' || ps.overflowY !== 'visible') {
        const q = p.getBoundingClientRect();
        x0 = Math.max(x0, q.left); y0 = Math.max(y0, q.top);
        x1 = Math.min(x1, q.right); y1 = Math.min(y1, q.bottom);
      }
    }
    x0 = Math.max(x0, 0); y0 = Math.max(y0, 0);
    x1 = Math.min(x1, innerWidth); y1 = Math.min(y1, innerHeight);
    if (x1 - x0 < 2 || y1 - y0 < 2) return null;
    return { left: x0, top: y0, right: x1, bottom: y1, width: x1 - x0, height: y1 - y0 };
  }
  const visible = (el) => !!seenRect(el);
  const first = (sel) => $$(sel).find(visible) || null;

  /* ------------------------------------------------------------ menus */

  const menuTop = (name) => $(`#menubar .menu[data-menu="${name}"] .mtop`);
  function openMenu(name) {
    const m = $(`#menubar .menu[data-menu="${name}"]`);
    if (!m) return null;
    $$('#menubar .menu.open').forEach((x) => { if (x !== m) x.classList.remove('open'); });
    m.classList.add('open');
    return m;
  }

  /* ------------------------------------------------------------ cost words */

  // the backend's terms (`charged` / `backend`): GET /api/backend, once, when first needed
  let terms = null, termsAsk = null;
  function backendTerms() {
    if (terms) return Promise.resolve(terms);
    if (!termsAsk) {
      termsAsk = fetch('/api/backend').then((r) => (r.ok ? r.json() : null))
        .then((b) => { if (b && 'charged' in b) terms = { backend: b.backend, charged: b.charged }; return terms; })
        .catch(() => null)
        .finally(() => { termsAsk = null; });
    }
    return termsAsk;
  }
  const isFree = (t) => !!(window.rcCost && t && rcCost.free(t));

  /* One model call's cost in REDESIGN-GOAL S's words (cli.js's rcCost): free on the plan
   * with the API's estimate beside it, when known; otherwise the bill. */
  function costWords(usd, t) {
    t = t || terms;
    if (usd != null && isFinite(usd) && window.rcCost) return rcCost.note(Number(usd), t);
    return isFree(t) ? 'free on your Claude plan — one model call'
      : 'one model call, billed to the API key — its price shows on the button before it runs';
  }
  // a price already written into a control's own hover ("… about $0.27 …"), when it has one
  function dollarsIn(el) {
    if (!el) return null;
    const own = [el.getAttribute('title'), hover.el === el ? hover.title : null]
      .concat($$('[title]', el).map((x) => x.getAttribute('title')));
    for (const t of own) {
      const m = /\$(\d+(?:\.\d+)?)/.exec(t || '');
      if (m) return Number(m[1]);
    }
    if (el.dataset && el.dataset.usd != null && el.dataset.usd !== '') return Number(el.dataset.usd);
    return null;
  }

  /* ------------------------------------------------------------ the hover cards */

  const fpPrice = () => {
    const s = fpState();
    const c = s && s.would && s.would.cost;
    return c && typeof c.usd === 'number' ? { usd: c.usd, terms: c } : null;
  };
  const askPriced = (mode) => () => {
    const p = appPrice(typeof mode === 'function' ? mode() : mode);
    return p && typeof p.usd === 'number' ? { usd: p.usd, terms: p } : null;
  };
  const NO_CALL = () => ({ none: 'none — no model call' });

  const FIRSTPASS = {
    name: () => (window.rcFirstPass && rcFirstPass.label ? rcFirstPass.label() : 'Run first pass'),
    what: 'Hears and looks at every clip, picks the best moments and proposes a rough cut.',
    change: 'Nothing until you accept: the cut it proposes waits on a Proposal track. A re-run keeps the shots you or the AI cut, and every kept shot keeps its grade.',
    price: fpPrice,
  };
  const PROMPT = {
    name: 'Ask the AI',
    what: 'Ask for any edit in plain words: “tighten the intro to 20 s”, “warm up the snow shots”, “dissolve between 4 and 5”.',
    change: 'Comes back as edits shown before they land; accept or reject each one. Nothing changes until you accept.',
  };
  // since wave 13 AIUX both buttons only point the AI prompt (Ctrl+J): the ask is the prompt's
  const ASK = {
    name: 'Ask for a change to the cut',
    what: 'Puts the cursor in the AI prompt (Ctrl+J), scoped to the whole sequence: type what you want and press Enter.',
    change: 'Nothing by itself; the prompt answers with edits, and nothing changes until you accept.',
    price: NO_CALL,
  };
  const SHOT = {
    name: 'Ask about this shot',
    what: 'Puts the cursor in the AI prompt scoped to this shot: start later, hold through the reaction, keep only the punchline.',
    change: 'Nothing by itself; the prompt answers with edits, and nothing changes until you accept.',
    price: NO_CALL,
  };
  const COLOUR = {
    name: 'Ask for a colour change',
    what: 'Describe a look (“warmer”, “match the snow shots”) and the AI proposes a grade.',
    change: 'A proposed grade you see on the Program monitor first; nothing changes until you accept.',
    price: askPriced('full'),
  };
  const DEEP = {
    name: 'Look deeper',
    what: 'Reads a stretch of the clip frame by frame (keyframes) to see what the first look missed.',
    change: 'Adds what it saw to the clip’s index (the picks and their why); the cut does not change.',
  };
  const FIND = {
    name: 'Find a moment',
    what: 'Finds a moment anywhere in the footage by what was said or seen. Typing searches the transcripts; the model search reads everything the index saw.',
    change: 'Lists the moments it finds in Selects; the cut does not change.',
  };
  const AUDIT = {
    name: 'Check the pass’s claims',
    what: 'A one-second close look at the highest-ranked jump, fall and crash claims nobody has checked.',
    change: 'Re-ranks the picks in Selects; the cut does not change.',
  };
  const FX = {
    name: 'Design an effect',
    what: 'The AI designs an effect from your words (a title, hit markers, motion) and checks it on the frames.',
    change: 'A proposed effect card for this shot; nothing renders until you accept.',
  };
  const FIRSTCUT = {
    name: () => { const b = $('#firstCut'); return b ? b.textContent.replace(/\s*·.*$/, '') : 'Make a first cut'; },
    what: 'Writes a first cut from your keeps, or from the whole index when there are none, to the length shown.',
    change: 'A proposed cut on a Proposal track; nothing lands until you accept.',
    price: askPriced(appFirstMode),
  };
  const ACCEPT = {
    name: 'Accept',
    what: 'Puts the proposed shots you ticked (✓) on the Timeline.',
    change: 'One edit: one Ctrl+Z undoes it. Shots you rejected (✗) stay as they were.',
    price: NO_CALL,
  };
  const OP_ACCEPT = {
    name: 'Accept this edit',
    what: 'Applies the AI’s edit with the same tool a hand edit uses.',
    change: 'The edit lands; one Ctrl+Z undoes it.',
    price: NO_CALL,
  };
  const OP_REJECT = {
    name: 'Reject this edit',
    what: 'Leaves this part of the AI’s answer out.',
    change: 'Nothing changes.',
    price: NO_CALL,
  };

  /* A menu item's card is titled by the item's own words as the menu shows them now, less
   * its trailing "…" and its shortcut (verdict 10: the AI menu's "Re-run first pass…" had a
   * card titled "Run first pass"). */
  function itemLabel(el) {
    const host = el && (el.querySelector('.aip-ml') || el);
    if (!host) return '';
    const words = Array.from(host.childNodes)
      .filter((n) => !(n.nodeType === 1 && n.classList.contains('k')))
      .map((n) => n.textContent).join('');
    return words.replace(/\s+/g, ' ').trim().replace(/\s*…$/, '');
  }
  const item = (spec) => ({ ...spec, name: (el) => itemLabel(el) || val(spec.name, el) });

  // [selector, spec]; from the hovered element outwards, the first match wins
  const CARDS = [
    ['#firstpass .fp-btn', FIRSTPASS],
    ['#firstpass .fp-run', FIRSTPASS],
    ['[data-fp-open]', FIRSTPASS],
    ['#firstCut', FIRSTCUT],
    ['[data-testid="ai-prompt-input"]', PROMPT],
    ['[data-testid="ai-scope"]', {
      name: 'What the AI may change',
      what: 'The scope of the next ask: the selected clips, the In/Out range, or the whole sequence.',
      change: 'Nothing by itself; it limits what an ask can touch.',
      price: NO_CALL,
    }],
    ['#ask', ASK],
    ['#inspector button[data-act="ask"]', SHOT],
    ['.dv-look', DEEP],
    ['#findDeep', { ...FIND, name: 'Ask the model to find it',
      what: 'One model call that reads every transcript and everything the visual pass saw, to find what word-matching cannot.' }],
    ['#auditClaims', AUDIT],
    ['#fxDesign', FX],
    ['#fx button[data-act="revise"]', { name: 'Change this effect', what: 'Sends your note to the AI to revise this effect.',
      change: 'A new proposal; the effect stays as it is until you accept the change.' }],
    ['#acceptProposal', ACCEPT],
    ['[data-testid="ai-op-accept"]', OP_ACCEPT],
    ['[data-testid="ai-accept-all"]', { ...OP_ACCEPT, name: 'Accept all', what: 'Applies every edit in the AI’s answer.',
      change: 'All of it lands as one edit; one Ctrl+Z undoes it.' }],
    ['[data-testid="ai-op-reject"]', OP_REJECT],
    ['[data-testid="ai-reject-all"]', { ...OP_REJECT, name: 'Reject all', what: 'Leaves the whole answer out.' }],
    // the menus: the AI menu's actions, and the Sequence menu's two AI entries, each card
    // titled by its item (`item`)
    ['#menubar [data-cmd="ai:prompt"]', item(PROMPT)],
    ['#menubar [data-cmd="ai:firstpass"]', item(FIRSTPASS)],
    ['#menubar [data-cmd="ai:ask"]', item(ASK)],
    ['#menubar [data-cmd="ai:shot"]', item(SHOT)],
    ['#menubar [data-cmd="ai:colour"]', item(COLOUR)],
    ['#menubar [data-cmd="ai:fx"]', item(FX)],
    ['#menubar [data-cmd="ai:deep"]', item(DEEP)],
    ['#menubar [data-cmd="ai:find"]', item(FIND)],
    ['#menubar [data-cmd="ai:audit"]', item(AUDIT)],
    ['#menubar [data-cmd="ask"]', item({ ...ASK,
      change: 'Puts the cursor in the AI prompt; nothing is asked until you press Enter.' })],
    ['#menubar [data-cmd="fx"]', item({ ...FX,
      change: 'Opens Designed Effects; nothing is designed until you press Design.' })],
  ];
  function cardFor(el) {
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      if (n.id === 'tourCallout' || n.classList.contains('rc-hc')) return null;
      for (const [sel, spec] of CARDS) {
        try { if (n.matches(sel)) return { el: n, spec }; } catch (e) { /* a bad selector added later */ }
      }
    }
    return null;
  }

  let card = null;                 // the card element (one, reused)
  const hover = { el: null, timer: 0, check: 0, shownAt: -1e9, title: null, watch: null, gen: 0, quiet: null };

  function cardEl() {
    if (!card) {
      card = document.createElement('div');
      card.className = 'rc-hc';
      card.setAttribute('role', 'tooltip');
      card.dataset.testid = 'ai-hover';
      card.hidden = true;
      document.body.appendChild(card);
    }
    return card;
  }

  const val = (v, el) => (typeof v === 'function' ? v(el) : v);

  function cardHtml(spec, el) {
    const p = spec.price ? spec.price(el) : null;
    let cost;
    if (p && p.none) cost = p.none;
    else if (p && typeof p.usd === 'number') cost = costWords(p.usd, p.terms || terms);
    else cost = costWords(dollarsIn(el), terms);
    const name = val(spec.name, el) || (el.textContent || '').trim();
    return `<div class="hc-name">${SPARK}<span>${esc(name)}</span></div>`
      + `<div class="hc-what">${esc(val(spec.what, el))}</div>`
      + `<div class="hc-row"><span class="hc-k">Cost</span><span class="hc-v" data-testid="ai-hover-cost">${esc(cost)}</span></div>`
      + `<div class="hc-row"><span class="hc-k">Changes</span><span class="hc-v" data-testid="ai-hover-change">${esc(val(spec.change, el))}</span></div>`;
  }

  /* Beside its control: below it (above when there is no room), or beside a menu's
   * drop-down; never off the window. */
  function placeCard(el) {
    const r = el.getBoundingClientRect();
    const c = cardEl();
    const w = c.offsetWidth, h = c.offsetHeight, gap = 6, m = 8;
    const drop = el.closest('.mdrop');
    let x, y;
    if (drop) {
      const d = drop.getBoundingClientRect();
      x = d.right + gap; y = r.top - 4;
      if (x + w > innerWidth - m) x = d.left - gap - w;
    } else {
      x = r.left + Math.min(r.width / 2, 40) - 16; y = r.bottom + gap;
      if (y + h > innerHeight - m) y = r.top - gap - h;
    }
    x = Math.max(m, Math.min(x, innerWidth - m - w));
    y = Math.max(m, Math.min(y, innerHeight - m - h));
    c.style.left = `${Math.round(x)}px`;
    c.style.top = `${Math.round(y)}px`;
  }

  /* The browser's own tooltip would come up over the card a moment later: while the card
   * shows, the control's title is held here and put straight back when it goes. Not under
   * automation, where no native tooltip is drawn and a test may read the title. */
  const STASH = !navigator.webdriver;
  function stashTitle(el) {
    if (!STASH) return;
    const take = () => {
      const t = el.getAttribute('title');
      if (t) { hover.title = t; el.removeAttribute('title'); }
    };
    take();
    hover.watch = new MutationObserver(take);
    hover.watch.observe(el, { attributes: true, attributeFilter: ['title'] });
  }
  function restoreTitle(el) {
    if (hover.watch) { hover.watch.disconnect(); hover.watch = null; }
    if (el && hover.title != null && !el.hasAttribute('title')) el.setAttribute('title', hover.title);
    hover.title = null;
  }

  async function showCard(el, spec) {
    const gen = hover.gen;
    if (!terms) await backendTerms();
    if (gen !== hover.gen || hover.el !== el || !el.isConnected || !visible(el)) return;
    const c = cardEl();
    c.innerHTML = cardHtml(spec, el);
    c.dataset.for = el.id || (el.dataset && (el.dataset.cmd || el.dataset.testid)) || el.className || el.tagName;
    c.hidden = false;
    placeCard(el);
    stashTitle(el);
    // a control redrawn or hidden under a still pointer sends no pointerout: its card goes
    // with it rather than staying over whatever took its place
    clearInterval(hover.check);
    hover.check = setInterval(() => {
      if (!hover.el || !hover.el.isConnected || !visible(hover.el)) hideCard();
    }, 250);
  }

  function hideCard() {
    clearTimeout(hover.timer);
    clearInterval(hover.check);
    hover.gen++;
    if (card && !card.hidden) { card.hidden = true; hover.shownAt = performance.now(); }
    restoreTitle(hover.el);
    hover.el = null;
  }

  /* A control just clicked says nothing more until the pointer leaves it (verdict 12, punch
   * 8: the first pass's card came back over the panel its click opened, and hid the line
   * under Stop): the click took the card down, and a move inside the control, or the control
   * redrawn under a still pointer, does not bring it back. */
  const quieted = (hit) => !!(hover.quiet && (hit.el === hover.quiet.el
    || (hit.spec === hover.quiet.spec && !hover.quiet.el.isConnected)));
  function onDown(e) {
    const hit = cardFor(e.target);
    hover.quiet = hit ? { el: hit.el, spec: hit.spec } : null;
    if (hover.el) hideCard();
  }

  function onOver(e) {
    const hit = cardFor(e.target);
    if (!hit && hover.el && !hover.el.contains(e.target)) { hideCard(); return; }
    if (hit && quieted(hit)) return;
    if (!hit || hit.el === hover.el) return;
    const warm = (card && !card.hidden) || performance.now() - hover.shownAt < WARM_MS;
    hideCard();
    // a field being typed in keeps its words to itself
    if (hit.el === document.activeElement && /^(INPUT|TEXTAREA)$/.test(hit.el.tagName)) return;
    hover.el = hit.el;
    const gen = hover.gen;
    hover.timer = setTimeout(() => { if (gen === hover.gen) showCard(hit.el, hit.spec); }, warm ? 0 : HOVER_MS);
  }
  function onOut(e) {
    const to = e.relatedTarget;
    if (hover.quiet) {
      const into = to instanceof Element ? cardFor(to) : null;
      if (!(into && quieted(into))) hover.quiet = null;
    }
    if (!hover.el) return;
    if (to && hover.el.contains(to)) return;
    hideCard();
  }

  /* ------------------------------------------------------------ the walk-through */

  const costShort = () => {
    const p = fpPrice();
    if (p && window.rcCost) return rcCost.free(p.terms) ? 'free on your plan' : `about $${p.usd.toFixed(2)}, billed to the API key`;
    return isFree(terms) ? 'free on your plan' : 'priced before it runs';
  };

  /* Lane AIOPS's review of the AI's edits (actions.js), by its test hooks. */
  const AI_REVIEW = '[data-testid="ai-review"]';
  const AI_ROW = '[data-testid="ai-review"] [data-testid="ai-op-row"]';
  const AI_BEFORE_AFTER = '[data-testid="ai-review"] [data-testid="ai-before-after"]';
  const AI_ACCEPT = '[data-testid="ai-review"] [data-testid="ai-accept-all"]';

  /* A step's `anchor` is tried in order (a selector, or a function); the first one on
   * screen is pointed at, else `fallback` (the menu that opens it). `show` is "Show me":
   * it only ever opens things. */
  const LOOP = [
    {
      key: 'import', title: 'Bring in footage',
      body: () => 'Your clips live in the Project panel. Add more with File › Import… or <kbd>Ctrl+I</kbd>,'
        + ' or drop files or a folder onto the panel.',
      anchor: ['#pProject'], fallback: () => menuTop('file'),
      show: showImport,
    },
    {
      key: 'firstpass', title: 'Run the first pass',
      body: () => 'It hears and looks at every clip, picks the best moments and proposes a rough cut'
        + ` (${esc(costShort())}). Nothing changes until you accept.`,
      anchor: ['#firstpass .fp-btn', '[data-fp-open]'], fallback: () => menuTop('ai'),
      show: () => { if (window.rcFirstPass) rcFirstPass.open(); },
      avoid: () => $('#firstpass .fp-panel'),
    },
    {
      key: 'why', title: 'See why a pick was made',
      // the pass's review is Selects' (wave 17 FOLDIN): its verdicts are on the rows
      body: () => 'Every pick carries its reason. Hover a pick in Selects, or an amber mark on a shot,'
        + ' to read what was heard and seen there. ✓ keeps a pick, ✗ rejects it; right-click it for'
        + ' a hero, later, a note or its takes.',
      anchor: ['#tl .tl-pick', '#pProject .ws-tab[data-tool="bin"]'], fallback: () => menuTop('window'),
      show: () => { if (window.dock) dock.open('bin', { remember: false }); },
    },
    {
      key: 'review', title: 'Review the proposed cut',
      // the AI's edits waiting in lane AIOPS's review are the same step: ✓ / ✗, then one accept
      body: () => (T.anchorWhy === AI_REVIEW
        ? 'The AI’s edits wait here, each shown before it lands (the new cut under V1, ◐ Before / After on'
          + ' the Program): ✓ or ✗ each one, then Accept all, which <kbd>Ctrl+Z</kbd> undoes in one step.'
        : 'The proposed cut lands on a Proposal track under V1: ✓ or ✗ each shot, ▶ Play it, then'
          + ' Accept, which <kbd>Ctrl+Z</kbd> undoes in one step. Then trim, razor and move it like any sequence.'),
      anchor: [AI_REVIEW, '#tl .tl-xlane[data-lane="ghost"]', '#proposal', '#pTimeline'],
      fallback: () => menuTop('window'),
      show: () => {
        if (!window.dock) return;
        dock.open('ask', { remember: false });
        if (first(AI_REVIEW)) { T.point = first(AI_ACCEPT); return; }
        dock.reveal('#proposal');
      },
      canShow: () => !!appPlan() || !!first(AI_REVIEW),
    },
    {
      key: 'ask', title: 'Ask the AI for an edit',
      body: (fb) => (fb ? 'In the AI menu, Ask the AI… takes a change in plain words' : 'Type a change in plain words')
        + ': “tighten the intro”, “warmer on the snow shots”. It works on the selection, the In/Out range or'
        + ' the whole sequence, and comes back as edits you accept or reject.',
      anchor: ['[data-testid="ai-prompt-input"]', '#aiPrompt'], fallback: () => menuTop('ai'),
      show: showPrompt,
    },
    {
      key: 'export', title: 'Export',
      body: (fb) => (fb ? 'Once there is a cut, File › Export… or <kbd>Ctrl+M</kbd>' : 'Export or <kbd>Ctrl+M</kbd>')
        + ' picks a preset, a name and a folder, shows the file’s size and renders in the background.',
      anchor: ['#makeFilm'], fallback: () => menuTop('file'),
      show: () => { if (window.exportDialog) exportDialog.open(); },
      canShow: () => appSegs().length > 0 && !!window.exportDialog,
    },
  ];

  const AI_STEPS = [
    {
      key: 'prompt', title: 'Ask in plain words',
      body: (fb) => (fb ? 'AI › Ask the AI… takes any edit in plain words' : 'Type any edit in plain words')
        + ': “dissolve between 4 and 5”, “export a 1080p review copy”. Its scope says what it may touch:'
        + ' the selection, the In/Out range or the whole sequence.',
      anchor: ['[data-testid="ai-scope"]', '[data-testid="ai-prompt-input"]', '#aiPrompt'],
      fallback: () => menuTop('ai'),
      show: showPrompt,
    },
    {
      key: 'review', title: 'Review each change',
      // on lane AIOPS's review when one is up: its first row, the ◐ Before / After, Accept all
      body: () => (T.anchorWhy === AI_ROW || T.anchorWhy === AI_REVIEW
        ? 'Each row is one edit, shown before it lands: the new cut under V1, colour and Effect Controls'
          + ' changes on the Program (◐ flips Before / After). ✓ keeps it, ✗ leaves it out; Accept all lands'
          + ' the ones you kept as one <kbd>Ctrl+Z</kbd>.'
        : 'Every answer is a list of edits, shown before they land. Accept or reject each one, or adjust'
          + ' it with the normal tools; an accept is one <kbd>Ctrl+Z</kbd>.'),
      anchor: [AI_ROW, AI_REVIEW, '#tl .tl-xlane[data-lane="ghost"]', '#proposal',
               '#pInspector .ws-tab[data-tool="ask"]'],
      fallback: () => menuTop('window'),
      show: () => {
        if (window.dock) dock.open('ask', { remember: false });
        // with a review up: where to compare (◐ Before / After), else where it lands
        const at = first(AI_BEFORE_AFTER) || first(AI_ACCEPT);
        if (at) T.point = at;
      },
    },
    {
      key: 'menu', title: 'Every AI action, one menu',
      body: () => 'The AI menu lists them all: the first pass, Ask, a shot or colour ask, Designed Effects,'
        + ' Look deeper, Find and the claims check.',
      anchor: ['[data-testid="menu-ai"]'], fallback: () => menuTop('ai'),
      show: () => { openMenu('ai'); },
    },
    {
      key: 'deep', title: 'Look deeper',
      body: (fb) => `When a pick’s reason looks wrong, Look deeper${fb ? ' (here, or in a selected shot’s Assistant section)' : ''}`
        + ' reads that stretch frame by frame. It adds to what the AI knows about the clip; your cut does not change.',
      anchor: ['.dv-look', '[data-testid="ec-assistant"]'], fallback: () => menuTop('ai'),
      show: () => { openMenu('ai'); T.point = $('#menubar [data-cmd="ai:deep"]'); },
    },
    {
      key: 'cost', title: 'What it costs',
      body: () => (isFree(terms)
        ? 'On your Claude plan every AI action is free; the API-equivalent estimate is in each hover.'
        : 'Each AI action is billed to your API key, and its price is on its button before you click.')
        + ' Hover any AI control to see what it does, what it costs and what it changes.',
      anchor: ['#firstpass .fp-btn'], fallback: () => menuTop('ai'),
    },
  ];

  function showImport() {
    if (window.rcImport && typeof rcImport.open === 'function') rcImport.open();
    // no dialog came up (Import not built here): File › Import… itself, opened and pointed at
    setTimeout(() => {
      if (!T.root || $$('dialog[open], [data-testid="import-dialog"]').some(visible)) return;
      openMenu('file');
      T.point = $('[data-testid="menu-import"]');
      place();
    }, 350);
  }
  function showPrompt() {
    if (first('[data-testid="ai-prompt-input"]') && window.rcAI) { rcAI.focus(); return; }
    openMenu('ai');
    T.point = $('#menubar [data-cmd="ai:prompt"]');
  }

  const T = { steps: null, which: null, i: 0, root: null, ring: null, tick: 0, point: null,
              anchorWhy: '', anchorEl: null, bodyKey: null, natW: 304 };

  function anchorOf(step) {
    for (const a of step.anchor || []) {
      const el = typeof a === 'function' ? a() : first(a);
      if (el && visible(el)) { T.anchorWhy = typeof a === 'string' ? a : 'fn'; return el; }
    }
    T.anchorWhy = 'fallback';
    return step.fallback ? step.fallback() : null;
  }

  function build() {
    const root = document.createElement('div');
    root.id = 'tourCallout';
    root.className = 'rc-tour';
    root.dataset.testid = 'tour';
    root.setAttribute('role', 'dialog');
    root.setAttribute('aria-modal', 'false');
    root.setAttribute('aria-labelledby', 'tourTitle');
    root.innerHTML = '<i class="rt-arrow" aria-hidden="true"></i>'
      + '<div class="rt-top"><span class="rt-dots"></span><span class="rt-count"></span></div>'
      + '<div class="rt-title" id="tourTitle"></div><div class="rt-body"></div>'
      + '<div class="rt-foot"><button type="button" class="rt-skip ghost" data-testid="tour-skip">Skip tour</button>'
      + '<span class="grow"></span>'
      + '<button type="button" class="rt-show" data-testid="tour-show">Show me</button>'
      + '<button type="button" class="rt-back" data-testid="tour-back">Back</button>'
      + '<button type="button" class="rt-next" data-testid="tour-next">Next</button></div>';
    root.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      e.stopPropagation();
      if (b.classList.contains('rt-skip')) stop('skipped');
      else if (b.classList.contains('rt-next')) go(T.i + 1);
      else if (b.classList.contains('rt-back')) go(T.i - 1);
      else if (b.classList.contains('rt-show')) showMe();
      else if (b.dataset.dot != null) go(Number(b.dataset.dot));
    });
    const ring = document.createElement('div');
    ring.className = 'rc-tour-ring';
    ring.dataset.testid = 'tour-ring';
    ring.setAttribute('aria-hidden', 'true');
    document.body.append(ring, root);
    T.root = root;
    T.ring = ring;
    T.natW = root.offsetWidth || 304;        // the stylesheet's width (a dock may narrow it)
  }

  function paint() {
    const step = T.steps[T.i];
    const n = T.steps.length;
    const root = T.root;
    root.dataset.which = T.which;
    root.dataset.step = String(T.i + 1);
    root.dataset.key = step.key;
    $('.rt-dots', root).innerHTML = T.steps.map((s, k) =>
      `<button type="button" class="rt-dot${k === T.i ? ' on' : ''}${k < T.i ? ' done' : ''}" data-dot="${k}"`
      + ` data-testid="tour-step" data-step="${k + 1}" title="${esc(s.title)}" aria-label="step ${k + 1}: ${esc(s.title)}"`
      + `${k === T.i ? ' aria-current="step"' : ''}></button>`).join('');
    $('.rt-count', root).textContent = `${T.i + 1} of ${n}`;
    $('#tourTitle', root).textContent = step.title;
    T.bodyKey = null;                        // place() writes it, for the anchor it finds
    $('.rt-back', root).hidden = T.i === 0;
    $('.rt-next', root).textContent = T.i === n - 1 ? 'Done' : 'Next';
    $('.rt-show', root).hidden = !step.show || (step.canShow ? !step.canShow() : false);
    T.point = null;
    place();
  }

  /* What the callout never sits on, whatever it points at: the Timeline's track headers
   * (V1 / V2's lock, eye and target), which step 1 covered on a first land (verdict 9,
   * dimensions 1 and 16), and the monitors' transport rows. */
  const KEEP_CLEAR = ['#tl .tl-side', '#pSource .transport', '#pProgram .transport'];
  /* …and what it is never placed over at all, wherever it ends up (verdict 12, punch 8: step
   * 5's callout covered the Assistant's Accept, "ccept", and step 1's the Source's
   * "preparing preview…"): the proposal's and the AI review's Accept / Discard, and the
   * monitors' status lines. Verdict 13, punch 8: and the AI's own line (aiprompt.js), which
   * step 5's callout, under the prompt it points at, covered in its row under the Timeline's
   * header ("2 changes ready · Review", the Review unclickable): that row, the line and its
   * Review / Show, wherever the line is. `watchAI` re-places the callout as they come and go. */
  const AI_LINE = ['#aiUnder', '[data-testid="ai-status"]', '[data-testid="ai-result-open"]'];
  const NEVER_COVER = ['#acceptProposal', '#acceptAll', '#rejectProposal', '#playProposal',
    '[data-testid="ai-accept-all"]', '[data-testid="ai-reject-all"]', '.monWait span', '#screenMsg',
    ...AI_LINE];
  const DOCK_MIN_W = 200;                 // px: narrowest the callout gets beside a dialog

  /* The modal dialog on top (Import, Export…), if one is up. A modal is in the top layer,
   * above every z-index, and the page outside it is inert: the callout goes INSIDE it
   * while it is up (fixed, so still placed against the window), docked to its edge, and
   * its buttons answer there; back to the body when the dialog closes. */
  function topModal() {
    const open = $$('dialog[open]').filter((d) => {
      try { return d.matches(':modal') && visible(d); } catch (e) { return false; }
    });
    return open.length ? open[open.length - 1] : null;
  }
  function hostIn(modal) {
    const want = modal || document.body;
    if (T.root.parentNode === want) return;
    want.appendChild(T.root);
    if (modal) modal.addEventListener('close', () => { if (T.root) place(); }, { once: true });
  }

  /* The callout beside its anchor (below, right, left or above: the first side it fits
   * on, clear of whatever the step opened and of the track headers), its arrow on the
   * anchor; the ring on the anchor itself. Both re-placed while the workspace moves under
   * them. With a modal dialog up (Show me's Import, Export) the callout sits beside the
   * dialog, above its backdrop, narrower if that is what the room beside it allows. */
  function place() {
    if (!T.root) return;
    const step = T.steps[T.i];
    const modal = topModal();
    hostIn(modal);
    const pointed = T.point && visible(T.point);
    const own = pointed ? T.point : anchorOf(step);
    const docked = !!(modal && !(own && modal.contains(own)));
    const anchor = docked ? modal : own;
    T.anchorEl = anchor;
    T.root.dataset.anchor = docked ? 'dialog' : pointed ? 'show' : T.anchorWhy;
    // the words follow what is pointed at: the control itself, or the menu that opens it
    const fb = !pointed && T.anchorWhy === 'fallback';
    const bodyKey = `${T.i}:${fb}:${T.anchorWhy}:${terms ? terms.charged : '?'}`;
    if (T.bodyKey !== bodyKey) { T.bodyKey = bodyKey; $('.rt-body', T.root).innerHTML = step.body(fb); }
    const ar = anchor ? seenRect(anchor) : null;
    const ring = T.ring;
    const gap = 12, m = 10;
    // beside a dialog: the stylesheet's width, or the room beside it when that is less
    let width = '';
    if (docked && ar) {
      const room = Math.floor(Math.max(ar.left - gap - m, innerWidth - m - ar.right - gap));
      if (room < T.natW && room >= DOCK_MIN_W) width = `${room}px`;
    }
    if (T.root.style.width !== width) T.root.style.width = width;
    const w = T.root.offsetWidth, h = T.root.offsetHeight;
    if (!ar) {
      ring.hidden = true;
      T.root.style.left = `${Math.round(innerWidth / 2 - w / 2)}px`;
      T.root.style.top = '80px';
      T.root.dataset.side = 'none';
      return;
    }
    // the anchor behind a dialog's backdrop: no ring (the ring is not in the top layer)
    ring.hidden = docked;
    const pad = 3;
    const rl = Math.max(1, ar.left - pad), rt = Math.max(1, ar.top - pad);
    ring.style.left = `${Math.round(rl)}px`;
    ring.style.top = `${Math.round(rt)}px`;
    ring.style.width = `${Math.round(Math.min(innerWidth - 1, ar.right + pad) - rl)}px`;
    ring.style.height = `${Math.round(Math.min(innerHeight - 1, ar.bottom + pad) - rt)}px`;
    // clear of the anchor and of what "Show me" opened (the first pass's panel, a menu)
    let box = { left: ar.left, top: ar.top, right: ar.right, bottom: ar.bottom };
    const extra = docked ? []
      : [step.avoid && step.avoid(), $('#menubar .menu.open .mdrop')].filter((x) => x && visible(x));
    for (const x of extra) {
      const r = x.getBoundingClientRect();
      box = { left: Math.min(box.left, r.left), top: Math.min(box.top, r.top),
              right: Math.max(box.right, r.right), bottom: Math.max(box.bottom, r.bottom) };
    }
    // and never on the track headers (behind a dialog's backdrop they do not matter)
    const clear = docked ? [] : KEEP_CLEAR.map((sel) => seenRect($(sel))).filter(Boolean);
    // nor ever on an Accept or a status line (the anchor itself excepted: it is beside it)
    const hard = docked ? [] : NEVER_COVER.flatMap((sel) => $$(sel))
      .filter((e) => e !== anchor && !e.contains(anchor)).map(seenRect).filter(Boolean);
    const hit = (rects, x, y) => rects.find((r) => x < r.right && x + w > r.left && y < r.bottom && y + h > r.top);
    const onClear = (x, y) => !!hit(clear, x, y) || !!hit(hard, x, y);
    const inWindow = (x, y) => x >= m && y >= m && x + w <= innerWidth - m && y + h <= innerHeight - m;
    const fits = (x, y) => inWindow(x, y) && !onClear(x, y);
    // what the arrow points at: a control's middle; a whole panel's upper part (below its
    // tabs, so the callout beside it leaves the neighbour's tabs readable)
    const tall = ar.bottom - ar.top > 160, wide = ar.right - ar.left > 400;
    const cx = wide ? ar.left + 60 : (ar.left + ar.right) / 2;
    const cy = tall ? ar.top + 72 : (ar.top + ar.bottom) / 2;
    const clampX = (x) => Math.max(m, Math.min(x, innerWidth - m - w));
    const clampY = (y) => Math.max(m, Math.min(y, innerHeight - m - h));
    const vx = clampX(wide ? ar.left + 12 : cx - w / 2), vy = clampY(tall ? ar.top + 44 : cy - h / 2);
    const tries = [
      ['below', vx, box.bottom + gap],
      ['right', box.right + gap, vy],
      ['left', box.left - gap - w, vy],
      ['above', vx, box.top - gap - h],
    ];
    // a side that lands on a keep-clear row: moved on along its own axis, past the row
    // (above the Source's transport rather than on it), the arrow still on the anchor
    const slide = ([sd, tx, ty]) => {
      let x0 = tx, y0 = ty;
      for (let k = 0; k < 4; k++) {
        const r = hit(clear, x0, y0) || hit(hard, x0, y0);
        if (!r) break;
        if (sd === 'above') y0 = r.top - gap / 2 - h;
        else if (sd === 'below') y0 = r.bottom + gap / 2;
        else if (sd === 'right') x0 = r.right + gap / 2;
        else x0 = r.left - gap / 2 - w;
      }
      return [sd, x0, y0];
    };
    const bySide = Object.fromEntries(tries.map((t) => [t[0], t]));
    // no side clear of them: the first that is on screen; a panel almost as big as the
    // window: inside it, in its top-right corner (a dialog: its bottom-left, over the
    // places list, clear of its title bar and its buttons)
    const free = (x0, y0) => inWindow(x0, y0) && !hit(hard, x0, y0);
    let [side, x, y] = tries.find(([, tx, ty]) => fits(tx, ty))
      || ['above', 'below', 'left', 'right'].map((sd) => slide(bySide[sd])).find(([, tx, ty]) => fits(tx, ty))
      || tries.find(([, tx, ty]) => free(tx, ty))
      || (docked ? ['inside', clampX(ar.left + gap), clampY(ar.bottom - h - 56)]
        : ['inside', clampX(ar.right - w - gap), clampY(ar.top + gap)]);
    if (hit(hard, x, y)) {
      // the last resort landed on an Accept or a status line: the nearest spot off them (and
      // off the anchor), its arrow dropped (it no longer sits beside what it points at)
      const off = (x0, y0) => free(x0, y0) && !(x0 < ar.right && x0 + w > ar.left && y0 < ar.bottom && y0 + h > ar.top);
      const near = [];
      for (const r of hard) {
        near.push([clampX(x), r.bottom + gap / 2], [clampX(x), r.top - gap / 2 - h],
          [r.right + gap / 2, clampY(y)], [r.left - gap / 2 - w, clampY(y)]);
      }
      for (let yy = m; yy + h <= innerHeight - m; yy += 24) {
        for (let xx = m; xx + w <= innerWidth - m; xx += 24) near.push([xx, yy]);
      }
      const d = (p) => Math.hypot(p[0] - x, p[1] - y);
      const best = near.filter(([x0, y0]) => off(x0, y0)).sort((a, b) => d(a) - d(b))[0];
      if (best) [side, x, y] = ['inside', best[0], best[1]];
    }
    T.root.style.left = `${Math.round(x)}px`;
    T.root.style.top = `${Math.round(y)}px`;
    T.root.dataset.side = side;
    const arrow = $('.rt-arrow', T.root);
    arrow.hidden = side === 'inside';
    if (side === 'below' || side === 'above') {
      arrow.style.left = `${Math.round(Math.max(14, Math.min(w - 14, cx - x)))}px`;
      arrow.style.top = '';
    } else {
      arrow.style.top = `${Math.round(Math.max(14, Math.min(h - 14, cy - y)))}px`;
      arrow.style.left = '';
    }
  }

  /* While the Program plays (app.js `player`), the ring holds still and the callout is
   * re-placed twice a second, not eight times (wave 13 CANVAS2): the glow's breathing is a
   * box-shadow animation, which repainted the board on every display frame — traced on the
   * perf fixture with the walk-through up, the display compositor redrew 1435x831 px of
   * the window on 132 of 215 frames in 4 s, the cost that held the graded picture 70-115 ms
   * on a shared box. At rest it breathes as before. */
  const appPlaying = () => { try { return typeof player === 'object' && !!player && !!player.playing; } catch (e) { return false; } };  // eslint-disable-line no-undef
  function tick() {
    const playing = appPlaying();
    if (T.ring && T.ring.classList.contains('still') !== playing) T.ring.classList.toggle('still', playing);
    const now = performance.now();
    if (playing && now - (T.placed || 0) < 480) return;
    T.placed = now;
    place();
  }

  /* The AI's line appears, changes or moves (its row under the header shown or hidden, the
   * prompt moved to its own row, an answer landing): the callout is placed again on the
   * next frame, not on the tick's next turn, so it is never left over the line. */
  function watchAI() {
    const els = [$('#aiUnder'), $('#aiRow'), $('[data-testid="ai-status"]')].filter(Boolean);
    if (!els.length || !('MutationObserver' in window)) return null;
    let queued = false;
    const again = () => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => { queued = false; if (T.root) place(); });
    };
    const mo = new MutationObserver(again);
    els.forEach((el) => mo.observe(el, { attributes: true, attributeFilter: ['hidden'], childList: true, subtree: true }));
    return mo;
  }

  function go(i) {
    if (!T.steps) return;
    if (i >= T.steps.length) { stop('done'); return; }
    T.i = Math.max(0, i);
    paint();
  }

  function showMe() {
    const step = T.steps[T.i];
    if (!step.show) return;
    step.show();
    // what it opened lays out a frame later
    requestAnimationFrame(() => requestAnimationFrame(tick));
  }

  /* Esc: the timeline's keys take Escape's keydown first (and stop it), so this listens on
   * keyup, as the first pass's panel does. Esc belongs to what it closes — the dialog, the
   * menu or the panel Show me opened, the AI prompt it cleared, the timeline's selection —
   * and never ends the walk-through (verdict 10, punch 9: Esc pressed to clear the prompt
   * ended the whole tour). Skip tour ends it. The callout follows what is still open. */
  function onKey(e) {
    if (!T.root || e.key !== 'Escape') return;
    place();
  }

  function start(which) {
    if (T.root) stop(null);
    T.which = which === 'ai' ? 'ai' : 'loop';
    T.steps = T.which === 'ai' ? AI_STEPS : LOOP;
    T.i = 0;
    if (seen() == null) remember('shown');
    build();
    paint();
    // the cost words depend on the backend: say them once it has answered
    if (!terms) backendTerms().then(() => { if (T.root) place(); });
    window.rcTour.active = true;
    window.addEventListener('keyup', onKey, true);
    window.addEventListener('resize', place);
    T.tick = setInterval(tick, 120);
    T.aiWatch = watchAI();
  }

  function stop(how) {
    if (how) remember(how);
    clearInterval(T.tick);
    if (T.aiWatch) { T.aiWatch.disconnect(); T.aiWatch = null; }
    window.removeEventListener('keyup', onKey, true);
    window.removeEventListener('resize', place);
    if (T.root) T.root.remove();
    if (T.ring) T.ring.remove();
    const ended = !!(T.steps && how);
    T.root = null; T.ring = null; T.steps = null; T.point = null; T.anchorEl = null;
    window.rcTour.active = false;
    if (ended && typeof window.toast === 'function') {
      window.toast(T.which === 'ai' ? 'AI › Learn the AI features shows this again'
        : 'Help › Learn Roughcut… shows this again', 3500);
    }
  }

  /* The first open of the board in this browser: once the workspace has drawn. */
  function firstRun() {
    if (seen() != null) return;
    const t0 = performance.now();
    const ready = () => !!(window.tl && $('#tl') && $('#tl').childElementCount && fpState());
    const wait = () => {
      if (seen() != null || window.rcTour.active) return;
      if (ready() || performance.now() - t0 > 12000) {
        setTimeout(() => { if (seen() == null && !window.rcTour.active) start(); }, 600);
      } else setTimeout(wait, 250);
    };
    wait();
  }

  function init() {
    document.addEventListener('pointerover', onOver, true);
    document.addEventListener('pointerout', onOut, true);
    document.addEventListener('pointerdown', onDown, true);
    document.addEventListener('keydown', () => { if (hover.el) hideCard(); }, true);
    window.addEventListener('blur', () => { if (hover.el) hideCard(); });
    // a scroll moves the control from under its card: the card goes (one still waiting
    // for its delay is placed where the control is when it comes up)
    const scrolled = () => { if (hover.el && card && !card.hidden) hideCard(); };
    document.addEventListener('wheel', scrolled, { capture: true, passive: true });
    window.addEventListener('scroll', scrolled, true);
    // the empty states' Import (app.js emptyState, the Program's line, the Source's)
    document.addEventListener('click', (e) => {
      const b = e.target.closest && e.target.closest('[data-rc-import]');
      if (!b) return;
      e.preventDefault();
      if (window.rcImport && typeof rcImport.open === 'function') rcImport.open();
    });
    firstRun();
  }

  window.rcTour = {
    start,
    active: false,
    stop: () => stop('skipped'),
    /* what the callout points at now (its ring's element): for the suite */
    anchor: () => (T.root ? T.anchorEl : null),
    /* another module's AI control: rcTour.card('#mine', {name, what, change, price?}) */
    card(sel, spec) { if (sel && spec) CARDS.unshift([sel, spec]); },
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
