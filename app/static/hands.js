/* Roughcut — the hands (wave 24, lane HANDS): where the keys go, and the timecode you type.
 *
 * Loaded right after dock.js, BEFORE the timeline's keys, so its window keydown listener
 * (capture) hears every key first. It owns three things and nothing else:
 *
 *  1. The focus rule. After a choice in a panel control (a dropdown, a slider let go, a
 *     checkbox) the keys are the timeline's again: the control lets go of the keyboard.
 *     A control that holds the keyboard without anyone having clicked or tabbed into it
 *     (a script's focus(), a panel redrawn under the caret) hands every key to the board
 *     and never changes its value (HANDS-14: ½× then Home made it ¼×, three Downs 1×).
 *     Enter in a dropdown confirms it and does nothing else (FINISH-13: it played the
 *     shot).
 *  2. Menus. While a menu is open it owns the keyboard: Esc closes it (dock.js), arrows
 *     walk it, Enter / Space press the item, and every other key — Delete, X, Ctrl+Z —
 *     does nothing to the cut behind it (CLUMSY-20: Delete removed a shot).
 *  3. The timecode fields. A click on the Timeline's or the Program's timecode (or Ctrl+G)
 *     opens a small field over it: type 40. or 1:05 or 00:00:40:00 or +2., Enter jumps,
 *     Esc leaves it (timeline-keys.js `tlKeys.goToTimecode` does the parsing and the jump).
 */
(function () {
  'use strict';

  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => [...(r || document).querySelectorAll(s)];
  const say = (m) => (typeof window.toast === 'function' ? window.toast(m) : console.log(m));

  const CONTROL = 'select, input[type=range], input[type=checkbox], input[type=radio]';
  let ptrEl = null;                     // the control the pointer went down on, until a key is pressed
  let tabAt = 0;                        // when Tab was last pressed

  const inDialog = (el) => !!(el.closest && el.closest('dialog[open]'));

  /* ------------------------------------------------------------ 1. the focus rule */
  document.addEventListener('pointerdown', (e) => {
    const t = e.target instanceof Element ? e.target : null;
    ptrEl = t ? t.closest(CONTROL) : null;
  }, true);

  // a control the user went to (a click, a Tab) keeps the keyboard until it is let go of;
  // any other holder is a stray
  document.addEventListener('focusin', (e) => {
    const t = e.target;
    if (!(t instanceof Element) || !t.matches(CONTROL)) return;
    t._handsIntent = ptrEl === t || Date.now() - tabAt < 500;
  }, true);
  document.addEventListener('focusout', (e) => { if (e.target) e.target._handsIntent = false; }, true);

  // a choice made with the pointer ends the control's hold on the keys
  document.addEventListener('change', (e) => {
    const t = e.target;
    if (!(t instanceof Element) || !t.matches(CONTROL) || t !== ptrEl || inDialog(t)) return;
    setTimeout(() => { if (document.activeElement === t) t.blur(); }, 0);
  }, true);

  function redispatch(e) {
    document.body.dispatchEvent(new KeyboardEvent('keydown', {
      key: e.key, code: e.code, shiftKey: e.shiftKey, ctrlKey: e.ctrlKey, altKey: e.altKey,
      metaKey: e.metaKey, repeat: e.repeat, bubbles: true, cancelable: true,
    }));
  }

  const MODIFIER_ONLY = new Set(['Shift', 'Control', 'Alt', 'Meta', 'CapsLock']);
  const EDIT_COMBOS = /^[zyxcvadksg]$/i;

  function controlKey(e, t) {
    const k = e.key;
    if (k === 'Tab') { tabAt = Date.now(); return; }
    ptrEl = null;                                   // a key after the click: the keyboard's now
    if (MODIFIER_ONLY.has(k) || k === 'Escape') return;
    if (e.metaKey || e.ctrlKey || e.altKey) return; // undo and the like stay the board's
    if (inDialog(t)) return;
    if (!t._handsIntent) {
      // nobody went to this control: the key was meant for the timeline
      e.preventDefault();
      e.stopImmediatePropagation();
      t.blur();
      redispatch(e);
      return;
    }
    if (t.tagName === 'SELECT' && k === 'Enter') {
      // Enter confirms the choice and lets go; it does not play the shot behind it
      e.preventDefault();
      e.stopImmediatePropagation();
      t.blur();
      return;
    }
    if (k === 'Enter' || k === ' ') e.stopImmediatePropagation();   // the control's own, not "play"
  }

  /* ------------------------------------------------------------ 2. menus */
  function menuKey(e, menu) {
    const k = e.key;
    if (k === 'Escape' || k === 'Tab' || MODIFIER_ONLY.has(k)) return;
    if ((e.ctrlKey || e.metaKey) && !EDIT_COMBOS.test(k)) return;     // reload, devtools, zoom
    if (/^F\d+$/.test(k)) return;
    if (k === 'Enter' || k === ' ') { e.stopImmediatePropagation(); return; }   // the focused button's own click
    e.preventDefault();
    e.stopImmediatePropagation();
    if (k === 'ArrowDown' || k === 'ArrowUp') {
      const items = $$('.mdrop button', menu).filter((b) => b.offsetParent !== null);
      if (!items.length) return;
      const at = items.indexOf(document.activeElement);
      const next = at < 0 ? (k === 'ArrowDown' ? 0 : items.length - 1)
        : (at + (k === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
      items[next].focus();
    } else if (k === 'ArrowLeft' || k === 'ArrowRight') {
      const menus = $$('#menubar .menu');
      const at = menus.indexOf(menu);
      const to = menus[(at + (k === 'ArrowRight' ? 1 : -1) + menus.length) % menus.length];
      menus.forEach((m) => m.classList.remove('open'));
      to.classList.add('open');
      const top = $('.mtop', to);
      if (top) top.focus();
    }
  }

  /* Typing with no text box focused (CLUMSY-15, and again in round 2: a path typed after the box
   * had lost the focus set a marker, In and Out, the rolling tool, the zoom, and switched the
   * grade off). Two signs that letters are text, not shortcuts:
   *   - the context: a text box had the keyboard in the last 3 s and the editor has not clicked
   *     the timeline or the Program since. Then THREE plain letters in a row (each within 350 ms
   *     of the last) are typing.
   *   - the rhythm alone: eight in a row, each within 150 ms (a person aims a shortcut; the
   *     deliberate ones a suite or an editor presses leave more room than that).
   * When it trips, what the first letters of the run did is put back (the undo stack, the tool,
   * the zoom, the grade, playback) and one line says so; the rest of the run does nothing.
   * (J K L and Space do not count: held and tapped together they are a chord.) */
  const TEXT = 'input:not([type=range]):not([type=checkbox]):not([type=radio]):not([type=button]):not([type=submit]),'
    + 'textarea, [contenteditable=""], [contenteditable=true]';
  let textAt = -1e9;
  const textActive = (e) => { if (e.target instanceof Element && e.target.matches(TEXT)) textAt = performance.now(); };
  document.addEventListener('focusin', textActive, true);
  document.addEventListener('input', textActive, true);
  document.addEventListener('keydown', (e) => {
    textActive(e);
    // leaving a box on purpose (Esc, Enter, Tab) is not a slip: the keys after it are shortcuts
    if (['Escape', 'Enter', 'Tab'].includes(e.key) && e.target instanceof Element && e.target.matches(TEXT)) textAt = -1e9;
  }, true);
  document.addEventListener('pointerdown', (e) => {
    // a click on the timeline, the monitors or a button (a dialog's Save) is the editor choosing where the keys go
    if (e.target instanceof Element && e.target.closest('#tl, .screen, .transport, button, a, select, summary')) textAt = -1e9;
  }, true);

  const run = { n: 0, at: -1e9, snap: null, tripped: false };
  function snapshot() {
    const g = typeof gradeApi === 'function' ? gradeApi() : null;
    return {
      tool: window.tl ? tl.state.tool : null,
      zoom: window.tl ? tl.state.zoom : null,
      depth: window.tl && typeof tl.undoDepth === 'function' ? tl.undoDepth() : null,
      grade: g && 'enabled' in g ? !!g.enabled : null,
      playing: window.player ? !!player.playing : false,
    };
  }
  function putBack(s) {
    if (!s || !window.tl) return;
    try {
      while (s.depth != null && tl.undoDepth() > s.depth) tl.undo();
      if (s.tool && tl.state.tool !== s.tool) tl.setTool(s.tool);
      if (s.zoom && Math.abs(tl.state.zoom - s.zoom) > 1e-6 && typeof tl.zoomTo === 'function') tl.zoomTo(s.zoom);
      const g = typeof gradeApi === 'function' ? gradeApi() : null;
      if (g && s.grade != null && !!g.enabled !== s.grade && typeof g.toggle === 'function') g.toggle();
      if (window.player && player.playing && !s.playing && typeof pauseCut === 'function') pauseCut();
    } catch (_) { /* a restore that fails leaves what it could */ }
  }
  function typingBurst(e) {
    if (e.repeat || e.ctrlKey || e.metaKey || e.altKey || e.key.length !== 1 || /^[jkl ]$/i.test(e.key)) return false;
    const t = e.target;
    if (t instanceof Element && (t.closest('input, textarea, select, [contenteditable=""], [contenteditable=true], dialog[open]'))) return false;
    const now = performance.now();
    const hint = now - textAt < 3000;
    const gap = hint ? 350 : 150, need = hint ? 3 : 8;
    if (now - run.at < gap) run.n += 1;
    else { run.n = 1; run.snap = snapshot(); run.tripped = false; }
    run.at = now;
    if (run.n < need) return false;
    if (!run.tripped) {
      run.tripped = true;
      putBack(run.snap);
      const k = run.n - 1;
      say(`that looked like typing: ${k} shortcut${k === 1 ? '' : 's'} undone; click the timeline to use keys`);
    }
    return true;
  }

  /* ------------------------------------------------------------ dialogs and popovers */
  /* While a dialog or popover the board opened is up (the Export dialog, New film, Sequence
   * Settings, the marker dialog, Project Settings, the key sheet, the first pass's panel), keys
   * that did not start inside it are not the board's: FINISH-12, "O" opened the cut switcher
   * behind the Export dialog. Escape closes a modal dialog from anywhere. */
  function openLayer() {
    const all = [...document.querySelectorAll('dialog[open], #keysOverlay:not([hidden]), [role=dialog]')];
    const live = all.filter((d) => d.tagName === 'DIALOG' || d.id === 'keysOverlay'
      || (!d.hidden && d.getAttribute('aria-modal') !== 'false' && d.offsetParent !== null));
    return live.length ? live[live.length - 1] : null;
  }
  function layerKey(e, layer) {
    const t = e.target;
    if (t instanceof Element && layer.contains(t)) return false;        // its own keys
    const k = e.key;
    if (MODIFIER_ONLY.has(k) || k === 'Tab' || /^F\d+$/.test(k)) return false;
    if ((e.ctrlKey || e.metaKey) && !EDIT_COMBOS.test(k)) return false;  // reload, zoom, devtools
    if (layer.closest('#keysOverlay') && k === '?') return false;           // the sheet's own toggle
    if (k === 'Escape') {
      if (layer.tagName === 'DIALOG') {
        e.preventDefault();
        e.stopImmediatePropagation();
        if (layer.dispatchEvent(new Event('cancel', { cancelable: true }))) layer.close();
      }
      return false;                                                       // the others close themselves
    }
    e.preventDefault();
    e.stopImmediatePropagation();
    return true;
  }

  window.addEventListener('keydown', (e) => {
    const menu = $('#menubar .menu.open');
    if (menu) { menuKey(e, menu); return; }
    const layer = openLayer();
    if (layer && layerKey(e, layer)) return;
    if (typingBurst(e)) { e.preventDefault(); e.stopImmediatePropagation(); return; }
    const t = e.target;
    if (t instanceof Element && t.matches(CONTROL)) controlKey(e, t);
    else if (e.key === 'Tab') tabAt = Date.now();
  }, true);

  /* ------------------------------------------------------------ 3. the timecode fields */
  let field = null;

  function closeField() {
    if (!field) return;
    const f = field;
    field = null;
    f.remove();
  }

  /* Open the field over `which`: 'timeline' (.tl-tc) or 'program' (#pos). */
  function editTimecode(which) {
    const host = which === 'program' ? $('#pos') : which === 'source' ? $('#srcPos') : $('#tl .tl-tc');
    if (!host || !window.tlKeys || typeof tlKeys.goToTimecode !== 'function') return false;
    closeField();
    const r = host.getBoundingClientRect();
    const cs = getComputedStyle(host);
    const inp = document.createElement('input');
    inp.type = 'text';
    inp.className = 'hands-tc';
    inp.dataset.testid = `tc-field-${which}`;
    inp.setAttribute('aria-label', 'go to timecode');
    inp.spellcheck = false;
    inp.autocomplete = 'off';
    inp.value = host.textContent.trim();
    inp.title = 'Type a time and press Enter: 40. or 0.5s (seconds) · 12 or 12f (frames) · 1:00 (one second) · 00:00:40:00 · +/- from here · Esc leaves';
    inp.style.cssText = `position:fixed;left:${Math.round(r.left - 3)}px;top:${Math.round(r.top - 2)}px;`
      + `width:${Math.max(Math.round(r.width) + 24, 104)}px;height:${Math.max(Math.round(r.height) + 4, 22)}px;`
      + `font:${cs.font};z-index:90`;
    document.body.appendChild(inp);
    field = inp;
    inp.focus();
    inp.select();
    inp.addEventListener('keydown', (e) => {
      e.stopPropagation();
      if (e.key === 'Escape') { e.preventDefault(); closeField(); }
      else if (e.key === 'Enter') {
        e.preventDefault();
        const res = which === 'source' && window.dock && dock.source && typeof dock.source.goTo === 'function'
          ? (() => { const r = dock.source.goTo(inp.value); say(r.ok ? r.said : r.why); return r; })()
          : tlKeys.goToTimecode(inp.value);
        if (res && res.ok) closeField();
        else { inp.classList.add('bad'); inp.select(); }
      }
    });
    inp.addEventListener('input', () => inp.classList.remove('bad'));
    inp.addEventListener('blur', () => { if (field === inp) closeField(); });
    return true;
  }

  document.addEventListener('click', (e) => {
    const t = e.target instanceof Element ? e.target : null;
    if (!t) return;
    if (t.closest('#srcPos')) { e.preventDefault(); editTimecode('source'); }
    else if (t.closest('#tl .tl-tc')) { e.preventDefault(); editTimecode('timeline'); }
    else if (t.closest('#pos')) { e.preventDefault(); editTimecode('program'); }
  }, true);

  function decorate() {
    const a = $('#tl .tl-tc'), b = $('#pos'), c = $('#srcPos');
    for (const el of [a, b, c]) {
      if (!el || el.dataset.handsTc) continue;
      el.dataset.handsTc = '1';
      el.classList.add('tc-typable');
      // no role=button: it is the same readout, typable, not one more control on the screen (the
      // screens budget counts controls in view; the hover and the cursor say it can be typed into)
      const was = el.title;
      el.title = `${was ? was + ' · ' : ''}click to type a timecode (Ctrl+G)`;
    }
  }
  setInterval(decorate, 1000);
  document.addEventListener('DOMContentLoaded', decorate);

  window.rcHands = { editTimecode, closeField, field: () => field };
})();
