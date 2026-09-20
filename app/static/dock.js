/* Roughcut — the dock (INTAKE M11).
 *
 * The board's right column was eight panels stacked 2,900 px tall beside a 900 px
 * viewport, "sticky" at its top: Renders, the bin and the keys were reached only by
 * scrolling the page, which scrolls the monitor away — and the bin, the thing a cut is
 * built from, was panel seven. This module makes the column a dock: a rail of tools and
 * ONE panel exactly the viewport's height that scrolls inside itself.
 *
 * What it owns, and nothing more:
 *   - which tool is open (`#tools > section[data-tool]`, one shown, the rest `hidden`);
 *     the rail button of the open tool carries `.on`; the choice is remembered per
 *     browser (localStorage) and restored on the next load;
 *   - the header's height as `--hd` on :root (a ResizeObserver — the steps strip and
 *     the progress strip change it), so the dock's `top` and `height` stay right;
 *   - the rail badges (`dock.badge(name, n)` — app.js says how many keeps, how many
 *     versions);
 *   - the keys overlay on `?` / the `?` button / Esc, and the Project popover.
 *
 * Adding a tool is one button in `#rail` and one `<section data-tool="…">` in
 * `#tools`; nothing here needs to know its name. Other modules reach a control that
 * may be in a closed tool with `dock.reveal(el | selector)` — the lanes do this when a
 * click on the music lane should show the music panel.
 *
 * Loaded before app.js and the timeline modules; it touches only its own elements and
 * never the EDL. */
(() => {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const KEY = 'roughcut.dock.tool';
  let cur = null;

  function sections() { return $$('#tools > section[data-tool]'); }

  /* Show one tool; returns false for a name the dock does not have. */
  function open(name, opts = {}) {
    const secs = sections();
    const sec = secs.find((s) => s.dataset.tool === name);
    if (!sec) return false;
    const changed = cur !== name;
    secs.forEach((s) => { s.hidden = s !== sec; });
    $$('#rail .tool').forEach((b) => b.classList.toggle('on', b.dataset.tool === name));
    cur = name;
    if (changed) { const t = $('#tools'); if (t) t.scrollTop = 0; }
    if (opts.remember !== false) {
      try { localStorage.setItem(KEY, name); } catch (e) { /* private mode: fine */ }
    }
    return true;
  }

  function toolOf(el) {
    const s = el && el.closest ? el.closest('#tools > section[data-tool]') : null;
    return s ? s.dataset.tool : null;
  }

  /* Make sure the tool holding `target` is the open one. True when the element exists
   * (in a tool or not); false when there is no such element. */
  function reveal(target) {
    const el = typeof target === 'string' ? $(target) : target;
    if (!el) return false;
    const t = toolOf(el);
    if (t && t !== cur) open(t);
    return true;
  }

  function badge(name, n) {
    const b = $(`#rail .tool[data-tool="${name}"] .badge`);
    if (b) b.textContent = n ? String(n) : '';
  }

  /* ------------------------------------------------------------ the header's height */
  function fit() {
    const h = $('header');
    if (!h) return;
    document.documentElement.style.setProperty('--hd', `${h.offsetHeight}px`);
  }

  /* ------------------------------------------------------------ keys overlay + popover */
  function keys(show) {
    const o = $('#keysOverlay');
    if (!o) return false;
    o.hidden = show === undefined ? !o.hidden : !show;
    return !o.hidden;
  }
  function project(show) {
    const p = $('#projectPop');
    if (!p) return false;
    p.hidden = show === undefined ? !p.hidden : !show;
    return !p.hidden;
  }

  function inField(t) {
    return !!t && (['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName) || t.isContentEditable);
  }

  function init() {
    const rail = $('#rail');
    if (!rail) return;
    rail.addEventListener('click', (e) => {
      const b = e.target.closest('.tool');
      if (b && b.dataset.tool) open(b.dataset.tool);
    });
    let want = null;
    try { want = localStorage.getItem(KEY); } catch (e) { want = null; }
    if (!want || !open(want, { remember: false })) open('bin', { remember: false });

    fit();
    const h = $('header');
    if (h && 'ResizeObserver' in window) new ResizeObserver(fit).observe(h);
    window.addEventListener('resize', fit);

    const kb = $('#keysBtn');
    if (kb) kb.addEventListener('click', () => keys());
    const ko = $('#keysOverlay');
    if (ko) ko.addEventListener('click', (e) => { if (e.target === ko) keys(false); });
    const pb = $('#projectBtn');
    if (pb) pb.addEventListener('click', (e) => { e.stopPropagation(); project(); });
    document.addEventListener('click', (e) => {
      const p = $('#projectPop');
      if (p && !p.hidden && !p.contains(e.target)) project(false);
    });
    // On window, capture, and loaded first: timeline-keys.js listens the same way and
    // stops what it handles (`?` and Esc among them) before a document listener would
    // hear it. An Esc that closes the overlay or the popover ends here; `?` opens the
    // overlay and passes on, so the timeline's map renders its section into it.
    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        if (ko && !ko.hidden) { keys(false); e.stopImmediatePropagation(); return; }
        const p = $('#projectPop');
        if (p && !p.hidden) { project(false); e.stopImmediatePropagation(); }
        return;
      }
      if (e.key === '?' && !inField(e.target) && !e.metaKey && !e.ctrlKey) keys();
    }, true);
  }

  window.dock = { open, reveal, badge, keys, project, fit, current: () => cur };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
