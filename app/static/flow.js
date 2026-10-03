/* The flow bar — where the project is, the same on every screen (INTAKE M14).
 *
 * Karl, 2026-10-03 (#3): "make the flow through various stages make more sense in the
 * UI." The three screens had three navigation schemes — the open screen's six numbered
 * steps, the board's five-step strip plus `open · pass · board` pills, the pass with
 * nothing — and none of them said what was waiting on him or what to do next. This
 * replaces all three with one rail drawn from GET /api/flow (roughcut/flow.py, from the
 * files): seven stages, Footage → Index → Brief → Pass → Cut → Polish → Render, each
 *
 *   ✓ done · a thin fill while running · amber when it needs you · dim while waiting
 *
 * with this screen's stages underlined, a click on any stage going to the screen where
 * it is done (on the board, with the right dock tool open: `/#tool=ask`), and one
 * **Next** chip at the end with the single recommended action. The chip navigates —
 * to the button that carries the price, for anything priced — and presses a button
 * itself only for a free action on its own screen (Render on the board): nothing spends
 * without the click on the button that says what it costs.
 *
 * Each page gives it an empty `<div id="flow">` where it belongs and loads this after
 * /cli.js. Self-contained styles, like /cli.js, because the three pages do not share
 * a stylesheet. It repaints only when the answer changes (no flicker under a hovered
 * chip) and keeps one height, so a poll never moves the page.
 */
(function () {
  'use strict';
  const POLL_MS = 5000, FAST_MS = 1500;
  const HERE = location.pathname.startsWith('/open') ? '/open'
    : location.pathname.startsWith('/floor') ? '/floor' : '/';
  let timer = null, shown = '', last = null;

  const css = `
  #flow { display: flex; align-items: center; gap: 2px; min-width: 0; height: 24px;
    font: 12px/1 system-ui, -apple-system, "Segoe UI", sans-serif; white-space: nowrap; }
  #flow .fs { position: relative; display: inline-flex; align-items: center; gap: 5px;
    height: 22px; padding: 0 8px; border: 1px solid #2e2e38; border-radius: 999px;
    color: #9a9aa8; text-decoration: none; overflow: hidden; flex: 0 1 auto; min-width: 0;
    background: transparent; cursor: pointer; }
  #flow .fs:hover { border-color: #6ea8fe; color: #e8e8ee; }
  #flow .fs .ic { flex: none; font-size: 11px; width: 10px; text-align: center; }
  #flow .fs .nm { flex: none; font-weight: 600; }
  #flow .fs .ct { flex: none; opacity: .8;
    font-variant-numeric: tabular-nums; }
  #flow .fs .pf { position: absolute; left: 0; bottom: 0; height: 2px; width: 0;
    background: #6ea8fe; transition: width .4s; }
  #flow .sep { color: #4a4a58; flex: none; font-size: 10px; }
  #flow .fs.done { color: #64d08a; border-color: #2c4c39; }
  #flow .fs.running { color: #cfe0ff; border-color: #2f4a70; }
  #flow .fs.ready { color: #e8e8ee; }
  #flow .fs.waiting { color: #6c6c80; border-style: dashed; }
  #flow .fs.optional { color: #9a9aa8; border-style: dashed; }
  #flow .fs.needs-you { color: #1a1406; background: #e0b050; border-color: #e0b050;
    font-weight: 600; }
  #flow .fs.blocked:not(.needs-you) { border-color: #e0b050; }
  #flow .fs.here { box-shadow: inset 0 -2px 0 #6ea8fe; }
  #flow .fs.here.needs-you { box-shadow: inset 0 -2px 0 #1a1406; }
  /* the Next chip gives up its width before a stage gives up its count */
  #flow .grow { flex: 1 1 8px; min-width: 8px; }
  #flow .fnext { flex: 0 50 auto; min-width: 96px; display: inline-flex; align-items: center;
    gap: 6px; height: 22px; padding: 0 10px; border-radius: 6px; border: 1px solid #6ea8fe;
    color: #e8e8ee; background: #16223a; text-decoration: none; overflow: hidden;
    cursor: pointer; }
  #flow .fnext b { color: #6ea8fe; font-weight: 700; flex: none; }
  #flow .fnext span { overflow: hidden; text-overflow: ellipsis; }
  #flow .fnext.cli { border-color: #e0b050; background: #3a2c0c; }
  #flow .fnext.cli b { color: #ffd27a; }
  #flow .fnext.wait { border-color: #2e2e38; background: transparent; color: #9a9aa8; }
  #flow .fnext.wait b { color: #9a9aa8; }`;

  const ICON = { done: '✓', running: '◐', ready: '○', waiting: '·', optional: '○',
                 'needs-you': '!' };

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
    el.setAttribute('aria-label', 'where the project is');
    return el;
  }

  /* The few characters after the name: the count the stage rests on. The sentence
   * is the title (hover) — the rail is a glance, the summary is the read. */
  function short(s) {
    const c = s.counts || {};
    switch (s.key) {
      case 'footage': return s.state === 'running' ? `${c.previews}/${c.clips}` : `${c.clips || 0}`;
      case 'index':
        if (s.state === 'needs-you') return 'paused';
        if (s.state === 'done') return '';
        return `${Math.round(s.state === 'running' && s.progress != null ? s.progress * 100 : c.pct || 0)}%`;
      case 'brief': return c.themes ? `${c.themes}` : '';
      case 'pass':
        if (c.keeps) return `${c.keeps} kept`;
        return c.on_pass ? `${c.passed || 0}/${c.on_pass}` : '';
      case 'cut': return c.proposal ? 'proposal' : c.shots ? `${c.shots}` : '';
      case 'polish': return c.fx_proposed ? `${c.fx_proposed} proposed` : c.effects ? `${c.effects} fx` : '';
      case 'render': return c.stale ? 'stale' : '';
      default: return '';
    }
  }

  function title(s) {
    const bits = [`${s.name}: ${s.summary}`];
    if (s.why) bits.push(`waiting on ${s.waiting_on} — ${s.why}`);
    if (s.blocked === 'cli') bits.push('the Claude CLI needs you first (see the banner)');
    return bits.join('\n');
  }

  function paint(f) {
    const el = mount();
    if (!el || !f || !f.stages) return;
    last = f;
    const key = JSON.stringify([f.stages, f.next]);
    if (key === shown) return;             // nothing changed: leave a hovered chip be
    shown = key;
    const parts = [];
    f.stages.forEach((s, i) => {
      if (i) parts.push('<span class="sep" aria-hidden="true">›</span>');
      const cls = ['fs', s.state, s.screen === HERE ? 'here' : '',
                   s.blocked ? 'blocked' : ''].filter(Boolean).join(' ');
      const fill = s.state === 'running' && s.progress != null
        ? `<i class="pf" style="width:${Math.round(s.progress * 100)}%"></i>` : '';
      const ct = short(s);
      parts.push(`<a class="${cls}" data-stage="${esc(s.key)}" data-state="${esc(s.state)}"` +
        ` href="${esc(s.href)}" title="${esc(title(s))}"` +
        `${s.screen === HERE ? ' aria-current="page"' : ''}>` +
        `<span class="ic" aria-hidden="true">${ICON[s.state] || ''}</span>` +
        `<span class="nm">${esc(s.name)}</span>${ct ? `<span class="ct">${esc(ct)}</span>` : ''}` +
        `${fill}</a>`);
    });
    parts.push('<span class="grow"></span>');
    const n = f.next;
    if (n) {
      const cls = n.kind === 'cli' ? 'cli' : n.kind === 'wait' ? 'wait' : '';
      parts.push(`<a class="fnext ${cls}" id="flowNext" href="${esc(n.href)}"` +
        ` data-next="${esc(n.stage)}" title="${esc(n.sentence)}">` +
        `<b>${esc(n.kind === 'wait' ? 'Now' : 'Next')}</b><span>${esc(n.sentence)}</span></a>`);
    }
    el.innerHTML = parts.join('');
  }

  /* On this screen already: open the tool instead of reloading the page. */
  function go(screen, tool, href, e) {
    if (screen === HERE) {
      if (e) e.preventDefault();
      if (tool && window.dock) {
        window.dock.open(tool);
        try { history.replaceState(null, '', `#tool=${tool}`); } catch (err) { /* fine */ }
      }
      return;
    }
    if (!e) location.href = href;
  }

  function onClick(e) {
    const a = e.target.closest('#flow a');
    if (!a || !last) return;
    if (a.id === 'flowNext') {
      const n = last.next;
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
      // Free, on this screen: press the screen's own button (Render). Priced or
      // elsewhere: go to the button that carries the price.
      const btn = n.click && n.screen === HERE ? document.querySelector(n.click) : null;
      if (btn && !btn.disabled) {
        e.preventDefault();
        if (n.tool && window.dock) window.dock.open(n.tool);
        btn.click();
        return;
      }
      go(n.screen, n.tool, n.href, e);
      return;
    }
    const s = last.stages.find((x) => x.key === a.dataset.stage);
    if (s) go(s.screen, s.tool, s.href, e);
  }

  /* `/#tool=fx` opens that dock tool — how a click on another screen's bar lands on
   * the right panel of the board. */
  function honourHash() {
    const m = /(?:^#|&)tool=([a-z]+)/.exec(location.hash || '');
    if (m && window.dock) window.dock.open(m[1]);
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
    poll();
  }

  window.flowBar = { poll, state: () => last, here: HERE };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
