/* Next — the one "what now", the same on every screen (INTAKE M14, M16).
 *
 * Karl, 2026-10-03 (#3): "make the flow through various stages make more sense in the
 * UI." M14 answered with a seven-step bar on every screen plus one **Next** chip. Karl,
 * 2026-10-04, M16 decision 2: the step bar comes off every screen; Next stays as the
 * one "what now". So this draws only the chip, from GET /api/flow (roughcut/flow.py
 * still computes the seven stages; Next is chosen from them), into the page's
 * `<div id="flow">` in its one header row.
 *
 * The chip navigates — to the button that carries the price, for anything priced —
 * and presses a button itself only for a free action on its own screen (a quick look
 * of the film on the board): nothing spends without the click on the button that says
 * what it costs. It lands on its target (a shot, an effect, a waiting proposal).
 *
 * One blue button per screen (M16 decision 3): `.is-next` is the only primary style,
 * and this puts it on exactly one element — the first visible element marked
 * `data-next-for` with Next's stage (and, when Next names an effect, the one whose
 * `data-fx` is that effect), else on the chip itself. While a round of the pass is
 * open nothing is blue: the chip reads "N left" and never takes Karl off the pass.
 *
 * Self-contained styles, like /cli.js, because the three pages do not share a
 * stylesheet. It repaints only when the answer changes (no flicker under a hovered
 * chip) and keeps one height, so a poll never moves the page.
 *
 * One first action, nothing clipped (verdict 3, punch 8). On the board the first pass
 * (firstpass.js, the header's "Run first pass") starts by indexing the footage, so Next
 * never offers "Index the footage" beside it: when Next's action is the pass's, the
 * chip yields its slot and the blue goes on the first pass's button (none while the
 * pass runs: its label is the progress). See `passCovers`. And the chip never
 * ellipsizes: when its sentence has no room in the row, it collapses to an icon with
 * the sentence in its hover (`.compact`) — a clipped "Next Make t…" read as a broken
 * primary button — and the icon is never blue (verdict 4, punch 7: a wordless blue
 * "→" was the screen's one primary button while previews built).
 */
(function () {
  'use strict';
  const POLL_MS = 5000, FAST_MS = 1500;
  const HERE = location.pathname.startsWith('/open') ? '/open'
    : location.pathname.startsWith('/floor') ? '/floor' : '/';
  let timer = null, shown = '', last = null;

  const css = `
  #flow { display: flex; align-items: center; min-width: 0; height: 26px;
    font: 12px/1 system-ui, -apple-system, "Segoe UI", sans-serif; white-space: nowrap; }
  #flow .fnext { flex: 0 1 auto; min-width: 0; max-width: 100%; display: inline-flex;
    align-items: center; gap: 6px; height: 24px; padding: 0 10px; border-radius: 6px;
    border: 1px solid #3a3a48; color: #e8e8ee; background: transparent;
    text-decoration: none; overflow: hidden; cursor: pointer; box-sizing: border-box; }
  #flow .fnext:hover { border-color: #6ea8fe; }
  /* In a crowded header the chip gives way first: it ellipsizes before the first pass,
     the job strip or the buttons on the right shrink (verdict 2, punch 6). */
  header > #flow { flex-shrink: 100; }
  #flow .fnext b { color: #9a9aa8; font-weight: 600; flex: none; }
  #flow .fnext span { flex: none; }
  #flow .fnext .fico { display: none; width: 14px; height: 14px; flex: none; }
  #flow .fnext.compact { flex: none; padding: 0 5px; }
  #flow .fnext.compact b, #flow .fnext.compact span { display: none; }
  #flow .fnext.compact .fico { display: block; }
  #flow .fnext.cli { border-color: #e0b050; background: #3a2c0c; }
  #flow .fnext.cli b { color: #ffd27a; }
  #flow .fnext.wait, #flow .fnext.round { color: #9a9aa8; }
  #flow .fnext.round { cursor: default; font-variant-numeric: tabular-nums; }
  #flow .fnext.round:hover { border-color: #3a3a48; }
  /* The one blue thing on a screen (INTAKE M16 decision 3). */
  .is-next { background: #6ea8fe !important; color: #0b0b0f !important;
    border-color: #6ea8fe !important; font-weight: 600; }
  .is-next b, .is-next .price, .is-next .fxprice { color: #0b0b0f !important; }
  #flow .fnext.cli.is-next { background: #3a2c0c !important; color: #f6e7c4 !important;
    border-color: #e0b050 !important; }
  #flow .fnext.cli.is-next b { color: #ffd27a !important; }`;

  const ICON = '<svg class="fico" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor"'
    + ' stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 8h10M9 4.5 12.5 8 9 11.5"/></svg>';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  function mount() {
    const el = document.getElementById('flow');
    if (!el) return null;
    if (!document.getElementById('flowCss')) {
      const st = document.createElement('style');
      st.id = 'flowCss';
      st.textContent = css;
      document.head.appendChild(st);
    }
    el.setAttribute('role', 'navigation');
    el.setAttribute('aria-label', 'what to do next');
    return el;
  }

  /* The pass mid-round (INTAKE M16 C3): how many of this round's moments are still
   * undecided, or null when no round is open (or this is not the pass). The pass may
   * say it itself (`window.passRound()`); otherwise its own state is read — the round's
   * frozen queue, while the pass (not the closing card) is on screen. */
  function roundLeft() {
    if (HERE !== '/floor') return null;
    try {
      if (typeof window.passRound === 'function') {
        const n = Number(window.passRound());
        return n > 0 ? n : null;
      }
      // the pass's state object, a top-level const of /floor.js (a classic script)
      // eslint-disable-next-line no-undef
      const P = typeof F === 'object' ? F : null;
      if (P && Array.isArray(P.queue) && P.mode === 'pass') {
        const n = P.queue.filter((p) => !p.verdict).length;
        return n > 0 ? n : null;
      }
    } catch (err) { /* the pass is still loading */ }
    return null;
  }

  /* ---- the board's first pass (firstpass.js): its state, and what it covers */
  function fpHost() { return HERE === '/' ? document.getElementById('firstpass') : null; }
  function fpState() {
    try {
      const s = window.rcFirstPass && typeof window.rcFirstPass.state === 'function'
        ? window.rcFirstPass.state() : null;
      return s ? s.state : null;
    } catch (err) { return null; }
  }
  function fpButton() { const h = fpHost(); return h ? h.querySelector('.fp-btn') : null; }

  /* Next's action is one the first pass does — index the footage (verdict 3: "Next:
   * Index the footage" beside "Run first pass", and the index is the pass's first step)
   * — so the header's first pass control is that action, and the chip does not offer
   * it a second time. While the pass runs, its label is the news about the index, the
   * clips released and the first cut, so a wait on those yields too. (A wait on an
   * index started elsewhere stays: the chip's icon, "Now: …". The empty Timeline's
   * "Make the first cut" on a bin already indexed stays Next's: INTAKE M16 I16.4.) */
  function passCovers(f) {
    const n = f && f.next;
    if (!n || !fpHost() || n.kind === 'cli') return false;    // the CLI's fix is its own
    if (fpState() === 'running') {
      const cut = (f.stages || []).find((s) => s.key === 'cut');
      const noCut = !cut || !((cut.counts || {}).shots > 0);
      return n.stage === 'index' || n.stage === 'pass' || (n.stage === 'cut' && noCut);
    }
    if (n.stage === 'cut' && fpState() === 'never-run') {
      // the first pass is THE first action on a bin without a cut, indexed or not
      const cut = (f.stages || []).find((s) => s.key === 'cut');
      if (cut && cut.state === 'ready') return true;
    }
    return n.kind !== 'wait' && n.stage === 'index';
  }

  /* The chip whole when it has room, else its icon (never ellipsized). A job running in
   * the strip (a preview build can run for the whole session) is no reason to collapse
   * it: verdict 4 found the screen's one primary button a wordless blue "→" for as long
   * as previews built (punch 7). Measured in one task — whole, then compact if it
   * overflows — so nothing paints in between. A compact chip is never the blue one
   * (`mark`): when there is no room for its words, Next steps out of blue instead. */
  function fit() {
    const el = document.getElementById('flow');
    const a = document.getElementById('flowNext');
    if (!el || !a || a.classList.contains('round')) return;
    const set = (on) => {
      a.classList.toggle('compact', on);
      el.classList.toggle('compact', on);
    };
    set(false);
    if (a.scrollWidth > a.clientWidth + 0.5 || el.scrollWidth > el.clientWidth + 0.5) set(true);
  }

  function paint(f) {
    const el = mount();
    if (!el || !f) return;
    last = f;
    const left = roundLeft();
    const yielded = !left && passCovers(f);
    const key = JSON.stringify([f.next, left, yielded]);
    if (key !== shown) {                   // nothing changed: leave a hovered chip be
      shown = key;
      const n = f.next;
      if (left) {
        el.innerHTML = `<a class="fnext round" id="flowNext" href="/floor" data-next="pass"` +
          ` title="${left} still to decide in this round — P pick · X reject · U later">` +
          `<span>${left} left</span></a>`;
      } else if (n && !yielded) {
        const cls = n.kind === 'cli' ? 'cli' : n.kind === 'wait' ? 'wait' : '';
        const word = n.kind === 'wait' ? 'Now' : 'Next';
        el.innerHTML = `<a class="fnext ${cls}" id="flowNext" href="${esc(n.href)}"` +
          ` data-next="${esc(n.stage)}" title="${esc(`${word}: ${n.sentence}`)}"` +
          ` aria-label="${esc(`${word}: ${n.sentence}`)}">` +
          `<b>${esc(word)}</b><span>${esc(n.sentence)}</span>${ICON}</a>`;
      } else {
        el.innerHTML = '';
      }
      el.dataset.yielded = yielded ? 'firstpass' : '';
    }
    fit();
    mark();
  }

  function visible(el) {
    if (!el || !el.getClientRects().length) return false;
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.opacity !== '0';
  }

  /* The first visible element a lane marked as Next's (`data-next-for="cut render"`),
   * narrowed to the effect Next names when it names one. */
  function target(n) {
    if (!n || !n.stage || n.kind === 'cli') return null;
    const fx = n.target && n.target.fx;
    const all = document.querySelectorAll('[data-next-for]');
    for (const el of all) {
      if (!(el.dataset.nextFor || '').split(/\s+/).includes(n.stage)) continue;
      if (fx && el.dataset.fx !== fx) continue;
      if (visible(el)) return el;
    }
    return null;
  }

  /* One blue button (INTAKE M16 decision 3): `.is-next` on Next's target when it is on
   * this screen, else on the chip; on nothing while a round of the pass is open. */
  function mark() {
    const n = last && last.next;
    let pick = null;
    if (n && !roundLeft()) {
      // yielded to the first pass: its button is the one action (nothing while it runs)
      if (passCovers(last)) pick = fpState() === 'running' ? null : fpButton();
      else pick = target(n) || document.getElementById('flowNext');
      // a primary button says what it does: an icon-only chip is not blue (punch 7)
      if (pick && pick.id === 'flowNext' && pick.classList.contains('compact')) pick = null;
    }
    document.querySelectorAll('.is-next').forEach((el) => {
      if (el !== pick) el.classList.remove('is-next');
    });
    if (pick && !pick.classList.contains('is-next')) pick.classList.add('is-next');
  }

  // The screen changes under the chip — a tool opens, a card arrives, a verdict lands
  // on the pass — so the blue is placed again (at most every 150 ms) when it does.
  let soonT = 0;
  function soon() {
    if (soonT) return;
    soonT = setTimeout(() => { soonT = 0; if (last) paint(last); }, 150);
  }

  /* On this screen already: open the tool instead of reloading the page — and land on
   * the action's target (its shot and card) there and then. */
  function go(screen, tool, href, e, target) {
    if (screen === HERE) {
      if (e) e.preventDefault();
      if (tool && window.dock) {
        window.dock.open(tool);
        try { history.replaceState(null, '', `#tool=${tool}`); } catch (err) { /* fine */ }
        if (target) land(tool, target);
      }
      return;
    }
    if (!e) location.href = href;
  }

  /* Next lands on its target (INTAKE M16 I16.0a). `/#tool=fx&shot=<id>&fx=<id>`: the
   * shot is selected, the monitor parked at its start (paused), the FX tool opened and
   * the effect's card brought into view (fx.focus); `/#tool=ask&ask=<job>`: the waiting
   * Ask proposal is shown (the Ask tool's own "show it" link — free, it only reads the
   * proposal off disk). Next used to open the tool on whatever shot the board had
   * anchored — shot 1's accepted title while the proposal waited on shot 17. The board
   * builds its timeline after a few fetches, so this waits (up to 15 s) for it — for a
   * shot or an effect only: an Ask target waits for the "show it" link alone, since a
   * first-cut proposal waits on an empty cut, which never has a shot to wait for. */
  let landing = 0;
  function mounted() {
    try { return !!(window.tl && tl.state && Array.isArray(tl.state.segs) && tl.state.segs.length); } catch (err) { return false; }
  }
  function land(tool, target) {
    if (HERE !== '/' || !target) return;
    const gen = ++landing;
    const until = Date.now() + 15000;
    const later = (fn) => { if (gen === landing && Date.now() < until) setTimeout(fn, 120); };
    const step = () => {
      if (gen !== landing) return;
      const onCut = !!(target.shot || target.fx);
      if ((onCut && !mounted()) || (target.fx && !(window.fx && fx.ready))) { later(step); return; }
      const shot = target.shot && tl.indexOf(target.shot) >= 0 ? target.shot : null;
      if (shot) {
        tl.select([shot], { source: 'land' });   // the board's pick, not Karl's: ⌫ won't take it
        const start = tl.filmStart(shot);
        if (start >= 0) tl.seek(start);
      }
      if (tool && window.dock) window.dock.open(tool);
      if (target.fx && window.fx && typeof fx.focus === 'function') fx.focus(target.fx);
      if (target.ask) {
        const show = () => {
          const a = document.getElementById('showLast');
          if (a) a.click(); else later(show);
        };
        show();
      }
    };
    step();
  }

  /* Press a free action's own button on this screen — the first visible one marked
   * for it — once its tool has drawn it (a dock tool may build its buttons on open). */
  function press(sel) {
    const until = Date.now() + 2000;
    const tryIt = () => {
      let btn = null;
      for (const el of document.querySelectorAll(sel)) { if (visible(el)) { btn = el; break; } }
      if (btn && !btn.disabled) { btn.click(); return; }
      if (Date.now() < until) setTimeout(tryIt, 100);
    };
    tryIt();
  }

  function onClick(e) {
    const a = e.target.closest('#flow a');
    if (!a || !last) return;
    if (a.id !== 'flowNext') return;
    if (a.classList.contains('round')) {
      // Mid-round the chip is the round's count, not a way off the pass.
      e.preventDefault();
      return;
    }
    const n = last.next;
    if (!n) return;
    if (n.kind === 'cli') {
      // The fix is the CLI banner's (/cli.js): bring it into view and let it speak.
      e.preventDefault();
      window.scrollTo(0, 0);
      const b = document.getElementById('cliFix');
      if (b && !b.hidden) {
        b.animate([{ outline: '2px solid #ffd27a' }, { outline: '2px solid transparent' }],
          { duration: 900 });
      }
      return;
    }
    // Free, on this screen: open its tool and press its button (a quick look of the
    // film). Priced or elsewhere: go to the button that carries the price.
    if (n.click && n.screen === HERE) {
      e.preventDefault();
      if (n.tool && window.dock) {
        window.dock.open(n.tool);
        try { history.replaceState(null, '', `#tool=${n.tool}`); } catch (err) { /* fine */ }
      }
      press(n.click);
      return;
    }
    go(n.screen, n.tool, n.href, e, n.target);
  }

  /* `/#tool=fx` opens that dock tool — how a click on another screen's bar lands on
   * the right panel of the board; `&shot=…&fx=…` / `&ask=…` is what it lands on. The
   * target is taken off the hash once read, so a later reload does not land again on
   * a proposal that has since been answered. */
  function honourHash() {
    const h = (location.hash || '').replace(/^#/, '');
    const q = {};
    h.split('&').forEach((kv) => {
      const i = kv.indexOf('=');
      if (i > 0) {
        try { q[kv.slice(0, i)] = decodeURIComponent(kv.slice(i + 1)); } catch (err) { /* skip */ }
      }
    });
    if (!/^[a-z]+$/.test(q.tool || '') || !window.dock) return;
    window.dock.open(q.tool);
    const target = {};
    ['shot', 'fx', 'ask'].forEach((k) => { if (q[k]) target[k] = q[k]; });
    if (!Object.keys(target).length) return;
    try { history.replaceState(null, '', `#tool=${q.tool}`); } catch (err) { /* fine */ }
    land(q.tool, target);
  }

  async function poll() {
    clearTimeout(timer);
    let f = null;
    try {
      const r = await fetch('/api/flow');
      if (r.ok) f = await r.json();
    } catch (e) { /* server away: keep the last answer on screen */ }
    if (f) paint(f);
    timer = setTimeout(poll, f && f.running ? FAST_MS : POLL_MS);
  }

  function init() {
    const el = mount();
    if (!el) return;
    el.addEventListener('click', onClick);
    honourHash();
    window.addEventListener('hashchange', honourHash);
    // ...but not when the change is the chip's own (fit() and mark() toggle its classes)
    new MutationObserver((recs) => {
      if (recs.some((r) => !el.contains(r.target))) soon();
    }).observe(document.body, {
      subtree: true, childList: true, attributes: true,
      attributeFilter: ['hidden', 'class', 'style', 'disabled', 'open', 'data-next-for', 'data-fx',
                        'data-state'],
    });
    // the row's room changes with the window: whole or icon, again
    window.addEventListener('resize', soon);
    // the pass decides a moment without always touching the DOM the chip can see; it
    // says so itself after every repaint (`roughcut:pass`, /floor.js), which is also
    // when the closing card's "Back to the cut" appears, so "N left" and the blue
    // follow at once; the second's re-read stays as the safety net
    if (HERE === '/floor') {
      document.addEventListener('roughcut:pass', () => { if (last) paint(last); });
      setInterval(soon, 1000);
    }
    poll();
  }

  // `mark()`: place the blue again now — for a page that has just drawn a button it
  // marked `data-next-for` and cannot wait the observer's 150 ms (tests, mostly).
  // `yielded()`: Next's action is the first pass's, so the chip gave its slot to it.
  window.flowBar = { poll, state: () => last, here: HERE, land,
                     yielded: () => !!(last && !roundLeft() && passCovers(last)),
                     mark: () => { if (last) paint(last); } };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
