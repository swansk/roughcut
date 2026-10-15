/* Roughcut — the workspace (redesign wave 1; was the dock, INTAKE M11).
 *
 * Karl, 2026-10-14: "this is not something that I feel meets 'premier pro quality'". The
 * board was one monitor, one timeline and a rail of five tools down the right; a Premiere
 * user could name none of it. This module lays the board out the way Premiere's editing
 * workspace is laid out, and owns everything about that layout and nothing about the cut:
 *
 *   - the panels, in Premiere's Editing arrangement (wave 4): Source (top left) and
 *     Program (top right) share the top row, half each; Project (bottom left, with
 *     Selects as its second tab), the Timeline (bottom centre) and Effect Controls
 *     (bottom right, with Effects · Audio · Assistant · Export as its neighbouring tabs)
 *     share the bottom row. Each has
 *     a title tab; the splitters between them drag, double-click resets, and the sizes
 *     are kept per browser (localStorage) as fractions of the window, so they survive a
 *     reload and a resize;
 *   - which tab each panel group shows (`section[data-tool]` inside the group, one shown);
 *     `dock.open(name)`, `dock.reveal(el)`, `dock.badge(name, n)` and `dock.current()` keep
 *     the names the board, the flow and the effects already use (bin, ask, sound, fx, out);
 *   - the menu bar (File · Edit · Clip · Sequence · Window · Help), which names every panel
 *     and reaches every tool;
 *   - the focused panel (Premiere's blue outline): the Source monitor's keys (I, O, space,
 *     ← →) act on it only while it is focused;
 *   - the Project panel: every clip in the footage folder from GET /api/clips, as a list
 *     (name, duration, frame rate and size when the server knows them, when it was shot,
 *     its state) or as icons; a click opens the clip in the Source monitor, a drag puts its
 *     In–Out range on the timeline;
 *   - the Source monitor: the clip, its own transport, and In/Out marks kept per clip.
 *     `window.rcSource = {clip, inPoint, outPoint}` is the live answer for the insert and
 *     overwrite keys (another lane binds them); `rcsource` fires on window when it changes;
 *   - no image is ever drawn broken: one that fails is swapped for a neutral frame and
 *     put back once its picture loads (punch 5, client half);
 *   - the header's height as `--hd`, and the keys overlay on `?`.
 *
 * Loaded before app.js and the timeline modules, so its window keydown listener (capture)
 * hears a key before theirs do. It never touches the EDL. */
(() => {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode: fine */ } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* fine */ } },
  };
  const esc = (x) => String(x == null ? '' : x).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  // Premiere's HH:MM:SS:FF, the timeline's own (tl.tc), at the clip's rate when known
  const tcode = (t, rate) => (window.tl && typeof tl.tc === 'function'
    ? tl.tc(t, Number(rate) > 0 ? Number(rate) : undefined) : clockOf(t));
  const clockOf = (t) => {
    t = Math.max(0, Number(t) || 0);
    const m = Math.floor(t / 60), s = t - m * 60;
    return `${m}:${s.toFixed(1).padStart(4, '0')}`;
  };

  /* ================================================================ tabs */
  const TABS_KEY = 'roughcut.ws.tabs';
  let cur = null;

  function sections() { return $$('.ws-body > section[data-tool]'); }
  function groupOf(sec) { return sec.closest('.ws-panel'); }

  /* Show one tool in its panel group; returns false for a name the board does not have. */
  function open(name, opts = {}) {
    const sec = sections().find((s) => s.dataset.tool === name);
    if (!sec) return false;
    const panel = groupOf(sec);
    const body = sec.parentElement;
    const changed = !sec.hidden ? false : true;
    body.querySelectorAll(':scope > section[data-tool]').forEach((s) => { s.hidden = s !== sec; });
    panel.querySelectorAll('.ws-tab[data-tool]').forEach((b) => b.classList.toggle('on', b.dataset.tool === name));
    if (changed) body.scrollTop = 0;
    cur = name;
    const view = panel.querySelector('#projView');
    if (view) view.hidden = name !== 'project';
    // Selects is wider than Project's own width (SELECTS_W): the row re-lays when it opens or closes
    if (panel.dataset.panel === 'project' && changed) layout();
    if (opts.remember !== false) {
      const t = store.get(TABS_KEY, {});
      t[panel.dataset.panel] = name;
      store.set(TABS_KEY, t);
    }
    if (opts.focus !== false) focusPanel(panel);
    return true;
  }

  function toolOf(el) {
    const s = el && el.closest ? el.closest('.ws-body > section[data-tool]') : null;
    return s ? s.dataset.tool : null;
  }

  /* Make sure the tab holding `target` is the open one in its group. True when the element
   * exists (in a tab or not); false when there is no such element. */
  function reveal(target) {
    const el = typeof target === 'string' ? $(target) : target;
    if (!el) return false;
    const t = toolOf(el);
    if (t) {
      const sec = el.closest('.ws-body > section[data-tool]');
      if (sec.hidden || cur !== t) open(t);
    }
    return true;
  }

  function isOpen(name) {
    const sec = sections().find((s) => s.dataset.tool === name);
    return !!sec && !sec.hidden;
  }

  function badge(name, n) {
    const b = $(`.ws-tab[data-tool="${name}"] .badge`);
    if (b) b.textContent = n ? String(n) : '';
  }

  /* ================================================================ the header's height */
  function fit() {
    const h = $('header');
    if (!h) return;
    // The CLI banner (/cli.js) sits above the header when the CLI needs Karl; the
    // workspace gives up its height too, or the page would scroll by exactly that much.
    const b = document.getElementById('cliFix');
    const banner = b && !b.hidden ? b.offsetHeight : 0;
    document.documentElement.style.setProperty('--hd', `${h.offsetHeight + banner}px`);
    layout();
  }

  /* ================================================================ panels and splitters */
  /* Wave 4 (lane LAYOUT, verdict 4 punch 3: "Source and Program each get a quarter of the
   * screen — a 386-pixel-wide picture on a laptop"). Premiere's Editing workspace gives the
   * two monitors the whole top row, half each, and puts the bins bottom left beside the
   * Timeline. So the top row is Source | Program across the full width, and the bottom
   * row is Project | Timeline | Effect Controls (each group keeps its tabs). The default
   * top row is tall enough for a Program picture of >= 560 px at 1440x900 and >= 800 px at
   * 1920x1080 with its meters beside it (rubric log, 2026-10-15 wave 4).
   *
   * Fractions of the workspace's width (proj, insp: the bottom row's side panels; src:
   * the Source monitor, null = half the top row) and height (top). A dragged one keeps
   * its own size when a neighbour moves; the Program monitor and the Timeline take up the
   * change.
   *
   * Version 2. A layout saved before it (no `v`) was dragged in the old arrangement,
   * where the top row was the monitors' quarter-width strip: its Project and Effect
   * Controls widths still mean the same panels' widths and are kept; its top-row height
   * and Source width are dropped once, so the bigger monitors reach that browser too. The
   * old layout is kept whole under `roughcut.ws.layout.v1` (nothing is lost), and Window ›
   * Reset workspace layout gives the new defaults. */
  const LAYOUT_KEY = 'roughcut.ws.layout';
  const LAYOUT_V = 2;
  // Effect Controls keeps its wave-2 width (0.255: its five tabs whole, and fx.js's cards
  // as wide as they were), only moved to the bottom row.
  const DEFAULTS = { proj: 0.2, insp: 0.255, src: null, top: 0.52 };
  // tlw: the Timeline's least width in the bottom row
  const MIN = { proj: 190, insp: 260, src: 180, prog: 220, top: 170, tl: 150, tlw: 360 };
  // The Selects list (app.js SEL_COLS) shows Media In / Out from a 440 px list and Reason
  // from 560: while Selects is the Project group's open tab the group is at least this
  // wide (the list, the section's padding, the panel's border and a scrollbar), taken
  // from the Timeline when the Timeline keeps SEL_TLW after it (1440 px windows and up;
  // narrower ones keep the Timeline whole and Selects shows the columns it has room for).
  // The Project tab gives the width back.
  const SELECTS_W = 592, SEL_TLW = 460;
  const SPLIT = 4;
  function loadLayout() {
    const raw = store.get(LAYOUT_KEY, null);
    if (!raw || typeof raw !== 'object') return {};
    if (raw.v === LAYOUT_V) return raw;
    if (store.get(`${LAYOUT_KEY}.v1`, null) == null) store.set(`${LAYOUT_KEY}.v1`, raw);
    const kept = { v: LAYOUT_V };
    ['proj', 'insp'].forEach((k) => { if (Number.isFinite(raw[k])) kept[k] = raw[k]; });
    store.set(LAYOUT_KEY, kept);
    return kept;
  }
  let frac = { ...DEFAULTS, ...loadLayout() };
  let px = {};

  function area() {
    const ws = $('#ws');
    if (!ws) return null;
    const cs = getComputedStyle(ws);
    const padX = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
    const padY = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom);
    const r = ws.getBoundingClientRect();
    return { ws, r, W: ws.clientWidth - padX, H: ws.clientHeight - padY,
             left: r.left + parseFloat(cs.paddingLeft), top: r.top + parseFloat(cs.paddingTop) };
  }
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(Math.max(lo, hi), v));

  /* Fractions → pixels, within each panel's minimum, written as the grid's variables. */
  function layout() {
    const a = area();
    if (!a || a.W <= 0) return;
    const root = document.documentElement.style;
    // the top row: Source | Program, the whole width
    const src = Math.round(clamp(frac.src == null ? (a.W - SPLIT) / 2 : frac.src * a.W,
      MIN.src, a.W - SPLIT - MIN.prog));
    const top = Math.round(clamp(frac.top * (a.H - SPLIT), MIN.top, a.H - SPLIT - MIN.tl));
    // the bottom row: Project | Timeline | Effect Controls
    const insp = Math.round(clamp(frac.insp * a.W, MIN.insp, a.W * 0.42));
    const room = a.W - 2 * SPLIT - insp;
    let proj = Math.round(clamp(frac.proj * a.W, MIN.proj, Math.min(a.W * 0.42, room - MIN.tlw)));
    if (selectsOpen() && room - SELECTS_W >= SEL_TLW) proj = Math.max(proj, SELECTS_W);
    px = { proj, insp, src, top, prog: a.W - SPLIT - src, tl: a.H - SPLIT - top, tlw: room - proj };
    root.setProperty('--c-proj', `${proj}px`);
    root.setProperty('--c-insp', `${insp}px`);
    root.setProperty('--c-src', `${src}px`);
    root.setProperty('--r-top', `${top}px`);
  }

  function saveLayout() { store.set(LAYOUT_KEY, { ...frac, v: LAYOUT_V }); }
  const selectsOpen = () => isOpen('bin');

  function resetLayout(key) {
    if (key) frac[key] = DEFAULTS[key]; else frac = { ...DEFAULTS };
    if (key) saveLayout(); else store.del(LAYOUT_KEY);
    layout();
  }

  function initSplitters() {
    $$('.ws-split').forEach((h) => {
      const key = h.dataset.split;
      h.addEventListener('pointerdown', (e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        h.setPointerCapture(e.pointerId);
        h.classList.add('drag');
        document.body.classList.add('ws-resizing', key === 'top' ? 'row' : 'col');
        const a = area();
        const srcLeft = $('#pSource').getBoundingClientRect().left;
        const move = (ev) => {
          if (key === 'proj') frac.proj = (ev.clientX - a.left - SPLIT / 2) / a.W;
          else if (key === 'insp') frac.insp = (a.left + a.W - ev.clientX - SPLIT / 2) / a.W;
          else if (key === 'src') frac.src = (ev.clientX - srcLeft - SPLIT / 2) / a.W;
          else if (key === 'top') frac.top = (ev.clientY - a.top - SPLIT / 2) / (a.H - SPLIT);
          layout();
          // what the clamp let through is what is kept
          if (key === 'proj') frac.proj = px.proj / a.W;
          else if (key === 'insp') frac.insp = px.insp / a.W;
          else if (key === 'src') frac.src = px.src / a.W;
          else frac.top = px.top / (a.H - SPLIT);
        };
        const up = () => {
          h.removeEventListener('pointermove', move);
          h.classList.remove('drag');
          document.body.classList.remove('ws-resizing', 'row', 'col');
          saveLayout();
        };
        h.addEventListener('pointermove', move);
        h.addEventListener('pointerup', up, { once: true });
        h.addEventListener('pointercancel', up, { once: true });
      });
      h.addEventListener('dblclick', () => resetLayout(key));
    });
  }

  /* ================================================================ the focused panel */
  let focused = null;
  function focusPanel(panel) {
    if (!panel || focused === panel) return;
    if (focused) focused.classList.remove('focus');
    focused = panel;
    panel.classList.add('focus');
  }
  const focusedName = () => (focused ? focused.dataset.panel : null);

  /* ================================================================ menus */
  function closeMenus() { $$('#menubar .menu.open').forEach((m) => m.classList.remove('open')); }

  function flashPanel(sel) {
    const p = $(sel);
    if (p) focusPanel(p);
  }

  const COMMANDS = {
    open: () => { const b = $('#hdBin'); if (b) b.click(); },
    footage: () => { location.href = '/open'; },
    pass: () => { location.href = '/floor'; },
    export: () => open('out'),
    undo: () => { const b = $('#undo'); if (b) b.click(); },
    redo: () => { const b = $('#redo'); if (b) b.click(); },
    keys: () => keys(true),
    markin: () => source.mark('in'),
    markout: () => source.mark('out'),
    insert: () => source.insert(),
    ec: () => open('inspect'),
    play: () => { if (typeof window.toggleCut === 'function') window.toggleCut(); },
    ask: () => open('ask'),
    music: () => open('sound'),
    fx: () => open('fx'),
    reset: () => resetLayout(),
    'w:source': () => flashPanel('#pSource'),
    'w:program': () => flashPanel('#pProgram'),
    'w:timeline': () => flashPanel('#pTimeline'),
  };

  function initMenus() {
    const bar = $('#menubar');
    if (!bar) return;
    bar.addEventListener('click', (e) => {
      const top = e.target.closest('.mtop');
      if (top) {
        const m = top.parentElement;
        const was = m.classList.contains('open');
        closeMenus();
        if (!was) m.classList.add('open');
        return;
      }
      const item = e.target.closest('.mdrop button[data-cmd]');
      if (!item) return;
      closeMenus();
      const cmd = item.dataset.cmd;
      if (COMMANDS[cmd]) COMMANDS[cmd]();
      else if (cmd.startsWith('w:')) open(cmd.slice(2));
    });
    bar.addEventListener('mouseover', (e) => {
      const top = e.target.closest('.mtop');
      if (!top || !bar.querySelector('.menu.open') || top.parentElement.classList.contains('open')) return;
      closeMenus();
      top.parentElement.classList.add('open');
    });
    document.addEventListener('pointerdown', (e) => { if (!e.target.closest('#menubar')) closeMenus(); }, true);
  }

  /* ================================================================ no broken images */
  /* A still that 404s (no proxy yet, a clip being re-read) is swapped for a neutral frame
   * and the real one is put back once it loads, probed off-screen so the swap never shows
   * a broken frame. Nothing shows alt text or a torn-image icon. */
  const BLANK = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" viewBox="0 0 320 180">'
    + '<rect width="320" height="180" fill="#151517"/>'
    + '<g fill="none" stroke="#38383e" stroke-width="4" stroke-linejoin="round">'
    + '<rect x="128" y="66" width="64" height="48" rx="5"/><path d="M128 78h64M142 66v12M160 66v12M178 66v12"/></g></svg>');
  const failed = new Map();      // img → the src that failed
  function onImgError(img) {
    const src = img.getAttribute('src');
    if (!src || src === BLANK) return;
    failed.set(img, src);
    img.dataset.wait = '1';
    img.setAttribute('src', BLANK);
    if (img.id === 'monPoster') waitOn('#monWait', true);
    if (img.id === 'srcPoster') waitOn('#srcWait', true);
  }
  function onImgLoad(img) {
    if (img.getAttribute('src') === BLANK) return;
    failed.delete(img);
    delete img.dataset.wait;
    if (img.id === 'monPoster') waitOn('#monWait', false);
    if (img.id === 'srcPoster') waitOn('#srcWait', false);
  }
  function retryImages() {
    failed.forEach((src, img) => {
      if (!img.isConnected || img.getAttribute('src') !== BLANK) { failed.delete(img); return; }
      const probe = new Image();
      probe.onload = () => {
        if (img.getAttribute('src') === BLANK && failed.get(img) === src) img.setAttribute('src', src);
      };
      probe.src = src;
    });
  }
  /* A monitor's "preparing preview…" has more than one reason to be up: its poster still
   * loading ('poster') and its video not playable yet ('media'). Each reason is set and
   * cleared on its own and the line shows while any is held; a poster that loads (cached,
   * at once) must not take down the wait a failed video put up (the gate's
   * test_a_media_error_is_shown_on_the_monitor_not_swallowed, flaky by exactly that).
   * on === null clears every reason (a fresh clip, a live picture). */
  function waitOn(sel, on, why = 'poster') {
    const el = $(sel);
    if (!el) return;
    const held = el._waitWhy || (el._waitWhy = new Set());
    if (on === null) held.clear();
    else if (on) held.add(why);
    else held.delete(why);
    el.hidden = held.size === 0;
  }
  function initImages() {
    document.addEventListener('error', (e) => {
      if (e.target && e.target.tagName === 'IMG') onImgError(e.target);
    }, true);
    document.addEventListener('load', (e) => {
      if (e.target && e.target.tagName === 'IMG') onImgLoad(e.target);
    }, true);
    setInterval(retryImages, 5000);
  }

  /* ================================================================ media that is not ready */
  /* The server's contract (wave 1, lane MEDIA): /media/proxy/<stem>.mp4 serves a proxy, or
   * redirects to the original or a 360p preview, or answers 503 while one builds; GET
   * /media/status/<stem>.mp4 says {serves, ready, …}. A monitor whose video errored asks
   * every ~3 s and calls `ready()` when it may load the same URL again; `failed(text)`
   * gets a calm line for a clip that cannot be previewed. Returns a stop function. A
   * server without the status route (an older board) is simply tried again. */
  function mediaWait(url, ready, failed) {
    const name = String(url).split('#')[0].split('?')[0].split('/').pop();
    let stop = false, misses = 0, t = 0;
    const tick = async () => {
      if (stop) return;
      let j = null;
      try {
        const r = await fetch(`/media/status/${encodeURIComponent(name)}`);
        if (r.ok) j = await r.json(); else misses++;
      } catch (e) { misses++; }
      if (stop) return;
      if (j && j.serves === 'failed') {
        if (failed) failed('no preview for this clip — it can still be cut');
        return;
      }
      if ((j && j.ready) || misses >= 2) { ready(); return; }
      t = setTimeout(tick, 3000);
    };
    t = setTimeout(tick, 3000);
    return () => { stop = true; clearTimeout(t); };
  }

  /* ================================================================ the project panel */
  const VIEW_KEY = 'roughcut.ws.projview';
  // `read` is false until /api/clips has answered once: before that the panel says it is
  // reading the folder, never that the bin is empty (verdict 2, punch 8).
  const project = { clips: [], view: store.get(VIEW_KEY, 'list'), sel: null, q: '', timer: 0,
                    read: false, failed: false };

  async function loadClips() {
    clearTimeout(project.timer);
    let j = null;
    try {
      const r = await fetch('/api/clips');
      if (!r.ok) throw new Error(String(r.status));
      j = await r.json();
      project.clips = Array.isArray(j.clips) ? j.clips : [];
      project.read = true;
      project.failed = false;
    } catch (e) {
      if (!project.read) { project.failed = true; paintProject(); }
      project.timer = setTimeout(loadClips, project.read ? 10000 : 2000);
      return;
    }
    // a newly listed clip gets its facts into the board's clips, where one was stubbed
    project.clips.forEach((c) => { if (stubs.has(c.clip)) ensureClip(c.clip, true); });
    heardSince();
    paintProject();
    // facts still being probed: ask again in a second; a clip still being indexed: now and then
    if (j && j.probing) project.timer = setTimeout(loadClips, 1000);
    else if (project.clips.some((c) => !c.analysed || !c.looked)) project.timer = setTimeout(loadClips, 15000);
    // the Source monitor opens on the clip it showed last, else the first one, so there
    // is a picture (its poster) the moment the bin is read
    if (!source.clip && project.clips.length) {
      const want = store.get('roughcut.ws.source', null);
      const c = project.clips.find((x) => x.clip === want) || project.clips[0];
      source.load(c, { quiet: true, auto: true });
    }
  }

  /* ---------------------------------------------------------- a clip the AI has not heard
   * The board's clips (`P.clips`, /api/project) are the clips with a sidecar; a clip the
   * index has not heard yet is still footage and goes in the sequence at once (verdict 2,
   * punch 7). This lists it there from the folder's facts — no transcript, so its "why"
   * is empty and its trims snap to nothing until the sidecar lands — and returns the
   * entry; null when the clip is not in the folder. /api/project lists it the same way
   * once a shot uses it. */
  const stubs = new Set();
  function ensureClip(name, refresh) {
    if (typeof P === 'undefined' || !P || !P.clips || !name) return null;   // eslint-disable-line no-undef
    const have = P.clips[name];                                              // eslint-disable-line no-undef
    if (have && !(refresh && have.heard === false)) return have;
    const c = clipByName(name);
    if (!c) return have || null;
    const stem = c.stem || name.replace(/\.[^.]+$/, '');
    const entry = {
      ...(have || {}),
      clip: name, stem, duration: Number(c.duration) || (have && have.duration) || 0,
      proxy: `/media/proxy/${stem}.mp4`, poster: c.poster ? String(c.poster).split('?')[0] : `/media/poster/${stem}.jpg`,
      transcript: [], visual: (have && have.visual) || {}, captured: c.captured || null,
      candidates: [], summary: { speech_fraction: null, wind_dominant_fraction: null,
                                 integrated_lufs: null, audio_usable: null },
      used: !!(have && have.used), heard: false, fps: c.fps || null,
    };
    P.clips[name] = entry;                                                   // eslint-disable-line no-undef
    stubs.add(name);
    return entry;
  }
  window.rcEnsureClip = (name) => ensureClip(name, false);

  /* A clip that went in unheard and has been heard since: its transcript (what trims
   * snap to, what the Ask reads) replaces the stub, without waiting for the whole
   * index to finish and the board to reload. */
  async function heardSince() {
    if (typeof P === 'undefined' || !P || !P.clips) return;                // eslint-disable-line no-undef
    const now = project.clips.filter((c) => c.analysed && P.clips[c.clip]   // eslint-disable-line no-undef
      && P.clips[c.clip].heard === false).map((c) => c.clip);               // eslint-disable-line no-undef
    if (!now.length) return;
    try {
      const p = await (await fetch('/api/project')).json();
      now.forEach((name) => {
        const fresh = p.clips && p.clips[name];
        if (fresh && fresh.heard !== false) {
          P.clips[name] = { ...fresh, used: P.clips[name].used || fresh.used };   // eslint-disable-line no-undef
          stubs.delete(name);
        }
      });
    } catch (e) { /* the next look tries again */ }
  }

  /* A clip's poster at `t` s: the URL /api/clips handed out (`poster`, versioned in its file
   * name by the server's poster_url(), lane POLISH) with only its `?t=` swapped, so a
   * browser holding an older (pre-ORIENT, upside-down) frame never asks for it again. */
  function posterAt(c, t) {
    const stem = c.stem || String(c.clip || '').replace(/\.[^.]+$/, '');
    const base = c.poster ? String(c.poster).split('?')[0] : `/media/poster/${stem}.jpg`;
    return `${base}?t=${(Number(t) || 0).toFixed(2)}`;
  }

  const FILM_ICON = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><rect x="1.5" y="3" width="13" height="10" rx="1.2"/><path d="M1.5 6h13M5 3v3M8 3v3M11 3v3"/></svg>';

  function capturedText(t) {
    if (!t) return '';
    const d = new Date(Number(t) * 1000);
    if (Number.isNaN(d.getTime())) return '';
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }
  // every clip plays (the server serves the original or a preview until the proxy is
  // built), so the state is the index's, not the proxy's
  // heard = the sound is transcribed, looked = the pictures are read, indexed = both
  function stateOf(c) {
    if (c.analysed && c.looked) return ['indexed', 'ok'];
    if (c.analysed) return ['heard', 'part'];
    if (c.looked) return ['looked', 'part'];
    return ['not indexed', ''];
  }
  const fpsOf = (c) => (c.fps ? `${Number(c.fps).toFixed(Number(c.fps) % 1 ? 2 : 0)} fps` : '');
  const sizeOf = (c) => (c.width && c.height ? `${c.width}×${c.height}` : '');

  function columns(width) {
    const cols = [{ k: 'name', h: 'Name' }, { k: 'dur', h: 'Duration', w: 80 }];
    if (width > 220) cols.push({ k: 'state', h: 'Status', w: 78 });
    if (width >= 400 && project.clips.some((c) => c.fps)) cols.push({ k: 'fps', h: 'Frame Rate', w: 76 });
    if (width >= 470 && project.clips.some((c) => c.width)) cols.push({ k: 'size', h: 'Resolution', w: 88 });
    if (width > 560) cols.push({ k: 'shot', h: 'Captured', w: 118 });
    return cols;
  }

  function cell(k, c) {
    if (k === 'name') return `<td class="name"><span class="n">${FILM_ICON}<span>${esc(c.clip)}</span></span></td>`;
    if (k === 'dur') return `<td>${c.duration ? tcode(c.duration, c.fps) : ''}</td>`;
    if (k === 'state') { const [t, cls] = stateOf(c); return `<td class="st ${cls}">${t}</td>`; }
    if (k === 'fps') return `<td>${fpsOf(c)}</td>`;
    if (k === 'size') return `<td>${sizeOf(c)}</td>`;
    return `<td>${capturedText(c.captured)}</td>`;
  }

  function shown() {
    const q = project.q.trim().toLowerCase();
    return q ? project.clips.filter((c) => c.clip.toLowerCase().includes(q)) : project.clips;
  }

  function paintProject() {
    const box = $('#projList');
    if (!box) return;
    const list = shown();
    const n = project.clips.length;
    $('#projCount').textContent = n ? `${list.length === n ? n : `${list.length} of ${n}`} item${n === 1 ? '' : 's'}` : '';
    $$('#projView button').forEach((b) => b.classList.toggle('on', b.dataset.view === project.view));
    if (!n) {
      box.innerHTML = !project.read
        ? `<div class="pempty">${project.failed ? 'Could not read the footage folder — trying again…' : 'Reading the footage folder…'}</div>`
        : '<div class="pempty">No footage in this bin yet. <a href="/open">Open a footage folder</a></div>';
      return;
    }
    if (project.view === 'icon') {
      box.innerHTML = `<div class="picons">${list.map((c) => `
        <div class="picon${project.sel === c.clip ? ' sel' : ''}" data-clip="${esc(c.clip)}" draggable="true" title="${esc(c.clip)}">
          <div class="thumb"><img alt="" loading="lazy" decoding="async" draggable="false" src="${esc(c.poster || posterAt(c, 0))}"><span class="d">${c.duration ? tcode(c.duration, c.fps) : ''}</span></div>
          <div class="nm">${esc(c.clip)}</div></div>`).join('')}</div>`;
      return;
    }
    const cols = columns(box.clientWidth || 300);
    box.innerHTML = `<table class="plist"><colgroup>${cols.map((c) => `<col${c.w ? ` style="width:${c.w}px"` : ''}>`).join('')}</colgroup>
      <thead><tr>${cols.map((c) => `<th>${c.h}</th>`).join('')}</tr></thead>
      <tbody>${list.map((c) => `<tr class="row${project.sel === c.clip ? ' sel' : ''}" data-clip="${esc(c.clip)}" draggable="true" title="${esc(c.clip)}">${cols.map((k) => cell(k.k, c)).join('')}</tr>`).join('')}</tbody></table>`;
  }

  function clipByName(name) { return project.clips.find((c) => c.clip === name) || null; }

  function selectClip(name, play) {
    project.sel = name;
    $$('#projList [data-clip]').forEach((r) => r.classList.toggle('sel', r.dataset.clip === name));
    const c = clipByName(name);
    if (c) source.load(c, { play });
  }

  function initProject() {
    const box = $('#projList');
    if (!box) return;
    box.addEventListener('click', (e) => {
      const r = e.target.closest('[data-clip]');
      if (r) selectClip(r.dataset.clip, false);
    });
    box.addEventListener('dblclick', (e) => {
      const r = e.target.closest('[data-clip]');
      if (r) selectClip(r.dataset.clip, true);
    });
    box.addEventListener('dragstart', (e) => {
      const r = e.target.closest('[data-clip]');
      if (!r) return;
      const c = clipByName(r.dataset.clip);
      if (!c) return;
      ensureClip(c.clip);                  // heard or not, it can go in the sequence now
      const [a, b] = source.marksFor(c);
      e.dataTransfer.setData('application/x-roughcut-shot',
        JSON.stringify({ clip: c.clip, start: a, end: b, why: '' }));
      e.dataTransfer.effectAllowed = 'copy';
    });
    $('#projQ').addEventListener('input', (e) => { project.q = e.target.value; paintProject(); });
    $('#projView').addEventListener('click', (e) => {
      const b = e.target.closest('button[data-view]');
      if (!b) return;
      project.view = b.dataset.view;
      store.set(VIEW_KEY, project.view);
      paintProject();
    });
    if ('ResizeObserver' in window) {
      let w = 0;
      new ResizeObserver(() => {
        const nw = box.clientWidth;
        if (Math.abs(nw - w) > 30) { w = nw; if (project.view === 'list') paintProject(); }
      }).observe(box);
    }
    loadClips();
  }

  /* ================================================================ the source monitor */
  const MARKS_KEY = 'roughcut.ws.marks';
  const source = {
    clip: null, marks: store.get(MARKS_KEY, {}), retry: 0,
    v: null,
    fps(c) { return Number((c || this.clip || {}).fps) || 24; },
    marksFor(c) {
      const m = this.marks[c.clip] || [];
      const dur = Number(c.duration) || 0;
      const a = Number.isFinite(m[0]) ? m[0] : 0;
      const b = Number.isFinite(m[1]) ? m[1] : dur;
      return [Math.max(0, Math.min(a, dur)), Math.max(0, Math.min(Math.max(b, a), dur || b))];
    },
    publish() {
      const c = this.clip;
      const m = c ? (this.marks[c.clip] || []) : [];
      if (!window.rcSource) window.rcSource = { clip: null, inPoint: null, outPoint: null, duration: 0 };
      window.rcSource.clip = c ? c.clip : null;
      window.rcSource.auto = !!(c && this.auto);
      window.rcSource.inPoint = c && Number.isFinite(m[0]) ? m[0] : null;
      window.rcSource.outPoint = c && Number.isFinite(m[1]) ? m[1] : null;
      window.rcSource.duration = c ? Number(c.duration) || 0 : 0;
      window.dispatchEvent(new CustomEvent('rcsource', { detail: { ...window.rcSource } }));
    },
    load(c, opts = {}) {
      const v = this.v;
      if (!v) return;
      const same = this.clip && this.clip.clip === c.clip;
      const keepAt = same || opts.keepTime ? v.currentTime : null;
      this.clip = c;
      // opened by the board on landing (so there is a picture), not by the editor: a
      // keep the editor then selects in the bin is what `,` takes instead
      this.auto = !!opts.auto;
      store.set('roughcut.ws.source', c.clip);
      $('#srcTitle .lbl').textContent = `Source: ${c.clip}`;
      $('#srcEmpty').hidden = true;
      const [a] = this.marksFor(c);
      const poster = $('#srcPoster');
      const url = `/media/proxy/${c.stem || c.clip.replace(/\.[^.]+$/, '')}.mp4`;
      if (v.dataset.src !== url || !same) {
        if (this.stopWait) { this.stopWait(); this.stopWait = null; }
        v.dataset.src = url;
        v.src = url;
        v.load();
        clearTimeout(this.retry);
        const at = keepAt != null ? keepAt : a;
        v.addEventListener('loadedmetadata', () => { v.currentTime = at; }, { once: true });
      }
      // no In marked: the card's own frame (pre-built, and not the black first frame
      // many clips open on) until the video has its own; else the frame at the In
      const marked = Number.isFinite((this.marks[c.clip] || [])[0]);
      poster.setAttribute('src', c.poster && !marked ? c.poster : posterAt(c, a));
      poster.hidden = false;
      waitOn('#srcWait', null);
      this.paint();
      this.publish();
      if (opts.play) this.play();
    },
    play() {
      const v = this.v;
      if (!v || !this.clip) return;
      if (!v.paused) { v.pause(); return; }
      const [, b] = this.marksFor(this.clip);
      if (v.currentTime >= b - 0.05) v.currentTime = this.marksFor(this.clip)[0];
      v.play().catch(() => { /* the monitor shows its wait; nothing to shout */ });
    },
    step(n) {
      const v = this.v;
      if (!v || !this.clip) return;
      v.pause();
      v.currentTime = Math.max(0, Math.min((v.duration || this.clip.duration || 0), v.currentTime + n / this.fps()));
      this.paint();
    },
    mark(which) {
      const c = this.clip, v = this.v;
      if (!c || !v) return;
      const m = this.marks[c.clip] || [null, null];
      const t = Math.round(v.currentTime * 1000) / 1000;
      if (which === 'in') { m[0] = t; if (Number.isFinite(m[1]) && m[1] <= t) m[1] = null; }
      else { m[1] = t; if (Number.isFinite(m[0]) && m[0] >= t) m[0] = null; }
      this.marks[c.clip] = m;
      store.set(MARKS_KEY, this.marks);
      this.paint();
      this.publish();
    },
    insert() {
      const c = this.clip;
      if (!c) return toastSay('pick a clip in the Project panel first');
      if (typeof window.rcInsertFromSource === 'function') return window.rcInsertFromSource(window.rcSource);
      const [a, b] = this.marksFor(c);
      if (b - a < 0.2) return toastSay('mark an In and an Out first');
      // any clip goes in at once: one the index has not heard yet goes in with an empty
      // "why", and its trims snap to the transcript once that arrives (verdict 2, punch 7)
      if (!ensureClip(c.clip) || typeof window.insertShot !== 'function') {
        return toastSay('the sequence is still loading — try again in a moment');
      }
      window.insertShot({ clip: c.clip, start: a, end: b, why: '' });
      if (typeof window.save === 'function') window.save();
      return true;
    },
    paint() {
      const v = this.v, c = this.clip;
      if (!v) return;
      const dur = (c && Number(c.duration)) || v.duration || 0;
      const t = v.currentTime || 0;
      $('#srcPos').textContent = tcode(t, c && c.fps);
      const bar = $('#srcBar');
      const head = bar.querySelector('.head'), range = bar.querySelector('.range');
      head.style.left = dur ? `${(t / dur) * 100}%` : '0';
      const m = c ? (this.marks[c.clip] || []) : [];
      const has = c && (Number.isFinite(m[0]) || Number.isFinite(m[1]));
      range.hidden = !has;
      if (has) {
        const [a, b] = this.marksFor(c);
        range.style.left = `${(a / dur) * 100}%`;
        range.style.width = `${Math.max(0, (b - a) / dur) * 100}%`;
        $('#srcDur').textContent = `In–Out ${tcode(b - a, c.fps)}`;
      } else {
        $('#srcDur').textContent = c ? tcode(dur, c.fps) : '';
      }
      const play = $('#srcPlay');
      if (play) play.innerHTML = v.paused
        ? '<svg viewBox="0 0 16 16" fill="currentColor"><path d="M4 2.5v11L13 8z"/></svg>'
        : '<svg viewBox="0 0 16 16" fill="currentColor"><path d="M4 3h3v10H4zM9 3h3v10H9z"/></svg>';
    },
    init() {
      const v = this.v = $('#srcVideo');
      if (!v) return;
      const live = () => { v.classList.add('live'); $('#srcPoster').hidden = true; waitOn('#srcWait', null); };
      v.addEventListener('loadeddata', live);
      v.addEventListener('seeked', () => { if (v.readyState >= 2) live(); this.paint(); });
      v.addEventListener('timeupdate', () => {
        this.paint();
        const c = this.clip;
        if (!c || v.paused) return;
        const m = this.marks[c.clip] || [];
        if (Number.isFinite(m[1]) && v.currentTime >= m[1]) { v.pause(); v.currentTime = m[1]; }
      });
      v.addEventListener('play', () => this.paint());
      v.addEventListener('pause', () => this.paint());
      // A proxy that is not there yet is a wait, never an error on the screen: the poster
      // (or a dark frame) and "preparing preview…", and another look in a few seconds.
      v.addEventListener('error', () => {
        v.classList.remove('live');
        const c = this.clip;
        const poster = $('#srcPoster');
        if (c) poster.setAttribute('src', posterAt(c, v.currentTime || this.marksFor(c)[0]));
        poster.hidden = false;
        $('#srcWait span').textContent = 'preparing preview…';
        waitOn('#srcWait', true, 'media');
        if (this.stopWait) this.stopWait();
        this.stopWait = mediaWait(v.dataset.src, () => {
          this.stopWait = null;
          if (this.clip === c && c) { v.dataset.src = ''; this.load(c, { quiet: true, keepTime: true }); }
        }, (why) => { $('#srcWait span').textContent = why; });
      });
      $('#source .btns').addEventListener('click', (e) => {
        const b = e.target.closest('button[data-src]');
        if (!b) return;
        const k = b.dataset.src;
        if (k === 'play') this.play();
        else if (k === 'in' || k === 'out') this.mark(k);
        else if (k === 'back') this.step(-1);
        else if (k === 'fwd') this.step(1);
        else if (k === 'insert') this.insert();
      });
      $('#srcScreen').addEventListener('click', () => this.play());
      const bar = $('#srcBar');
      const seek = (e) => {
        const c = this.clip;
        if (!c) return;
        const r = bar.getBoundingClientRect();
        const dur = Number(c.duration) || v.duration || 0;
        v.currentTime = clamp(((e.clientX - r.left) / r.width) * dur, 0, dur);
        this.paint();
      };
      bar.addEventListener('pointerdown', (e) => {
        if (e.button !== 0) return;
        bar.setPointerCapture(e.pointerId);
        seek(e);
        const mv = (ev) => seek(ev);
        bar.addEventListener('pointermove', mv);
        bar.addEventListener('pointerup', () => bar.removeEventListener('pointermove', mv), { once: true });
      });
      this.paint();
    },
  };
  window.rcSource = { clip: null, inPoint: null, outPoint: null, duration: 0 };

  function toastSay(msg) {
    if (typeof window.toast === 'function') window.toast(msg, 3500);
    return false;
  }

  /* ================================================================ keys overlay */
  function keys(show) {
    const o = $('#keysOverlay');
    if (!o) return false;
    o.hidden = show === undefined ? !o.hidden : !show;
    if (!o.hidden) keyTable();
    return !o.hidden;
  }

  /* One table, grouped by task: the board's own keys and the timeline's
   * (`window.tlKeys.KEYS`, read, never copied), each row once. */
  const GROUPS = [['play', 'Play'], ['trim', 'Trim and cut'], ['move', 'Move around'],
                  ['bin', 'The bin'], ['source', 'Source monitor']];
  const BOARD_KEYS = [
    ['space', 'play / pause the cut from here', 'play'],
    ['↵', 'play this shot only', 'play'],
    ['G', 'the grade on / off — "ungraded" shows on the picture while it is off', 'play'],
    ['[ ]', 'trim the in-point (Shift: a second)', 'trim'],
    ['{ }', 'trim the out-point (Shift: a second)', 'trim'],
    ['click', 'a keep selects it', 'bin'],
    ['↵', 'adds the selected keep at the playhead', 'bin'],
    ['space', 'plays the selected keep in the bin', 'bin'],
    ['drag', 'a keep or a Project clip onto the timeline', 'bin'],
    ['I O', 'mark In / Out (Source focused)', 'source'],
    ['space', 'play / stop the source clip (Source focused)', 'source'],
    ['← →', 'step a frame (Source focused)', 'source'],
  ];
  function keyTable() {
    const box = $('#keyTable');
    if (!box) return;
    const tlk = (window.tlKeys && Array.isArray(window.tlKeys.KEYS)) ? window.tlKeys.KEYS : [];
    const rows = BOARD_KEYS.map(([k, what, g]) => ({ k, what, g }));
    const seen = new Set(rows.map((r) => `${r.k}|${r.what}`));
    // a timeline row carries its group as its 4th field (lane KEYS)
    tlk.forEach((row) => {
      const [k, what] = row;
      if (k === '?' || seen.has(`${k}|${what}`)) return;
      seen.add(`${k}|${what}`);
      rows.push({ k, what, g: GROUPS.some(([g]) => g === row[3]) ? row[3] : 'move' });
    });
    box.innerHTML = GROUPS.map(([g, name]) => {
      const mine = rows.filter((r) => r.g === g);
      if (!mine.length) return '';
      return `<table><tr><th colspan="2">${name}</th></tr>` + mine.map((r) =>
        `<tr><td class="k">${r.k.split(' ').map((t) => `<kbd>${esc(t)}</kbd>`).join(' ')}</td>`
        + `<td>${esc(r.what)}</td></tr>`).join('') + '</table>';
    }).join('');
  }

  function inField(t) {
    return !!t && (['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName) || t.isContentEditable);
  }

  /* ================================================================ init */
  function init() {
    if (!$('#ws')) return;
    initMenus();
    initImages();
    initSplitters();
    source.init();
    initProject();
    // the editor picked a keep or a Find match while the Source showed only the clip the
    // board opened on landing: `,` / `.` take the pick (timeline-keys' sourceShot order)
    document.addEventListener('click', (e) => {
      if (!source.auto || !window.rcSource || !e.target.closest) return;
      if (!e.target.closest('#library .keep, #findResults .cand')) return;
      source.auto = false;
      window.rcSource.clip = null;
      window.rcSource.auto = false;
    }, true);

    // each group's tab strip; each group opens on the tab it was left on
    $$('.ws-tabs').forEach((tabs) => tabs.addEventListener('click', (e) => {
      const b = e.target.closest('.ws-tab[data-tool]');
      if (b) open(b.dataset.tool);
    }));
    const want = store.get(TABS_KEY, {});
    $$('.ws-panel').forEach((p) => {
      const first = p.querySelector('.ws-body > section[data-tool]');
      if (!first) return;
      const w = want[p.dataset.panel];
      if (!w || !p.querySelector(`.ws-body > section[data-tool="${w}"]`) || !open(w, { remember: false, focus: false })) {
        open(first.dataset.tool, { remember: false, focus: false });
      }
    });
    cur = want.project || 'project';
    if (!sections().some((s) => s.dataset.tool === cur)) cur = 'project';
    focusPanel($('#pTimeline'));
    $('#ws').addEventListener('pointerdown', (e) => {
      const p = e.target.closest('.ws-panel');
      if (p) focusPanel(p);
    }, true);

    fit();
    const h = $('header');
    if (h && 'ResizeObserver' in window) new ResizeObserver(fit).observe(h);
    window.addEventListener('resize', fit);
    window.addEventListener('clifix', fit);
    // the timeline sizes its own tracks (lane TRACKS: --tl-v1h and explicit heights)

    const kb = $('#keysBtn');
    if (kb) kb.addEventListener('click', () => keys());
    const ko = $('#keysOverlay');
    if (ko) ko.addEventListener('click', (e) => { if (e.target === ko) keys(false); });
    // On window, capture, and loaded first: timeline-keys.js listens the same way and
    // stops what it handles before a document listener would hear it.
    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        if ($('#menubar .menu.open')) { closeMenus(); e.stopImmediatePropagation(); return; }
        if (ko && !ko.hidden) { keys(false); e.stopImmediatePropagation(); }
        return;
      }
      if (inField(e.target)) return;
      if (e.key === '?' && !e.metaKey && !e.ctrlKey) { keys(); return; }
      // The Source monitor's keys, while it is the focused panel (Premiere's rule: a key
      // acts on the panel with the blue outline).
      if (focusedName() === 'source' && !e.metaKey && !e.ctrlKey && !e.altKey) {
        const k = e.key.toLowerCase();
        const act = k === 'i' ? () => source.mark('in') : k === 'o' ? () => source.mark('out')
          : k === ' ' ? () => source.play() : k === 'arrowleft' ? () => source.step(-1)
            : k === 'arrowright' ? () => source.step(1) : null;
        if (act) { e.preventDefault(); e.stopImmediatePropagation(); act(); }
      }
    }, true);
    // the Program transport's edit-to-edit buttons drive the timeline's own ↑ / ↓
    const pb = $('#progBack'), pf = $('#progFwd');
    const edit = (d) => {
      if (!window.tl || !tl.state || typeof tl.seek !== 'function') return;
      const at = tl.state.playhead || 0;
      const cuts = [0];
      let t = 0;
      (tl.state.segs || []).forEach((s) => { t += tl.dur(s); cuts.push(t); });
      const next = d > 0 ? cuts.find((c) => c > at + 0.02) : [...cuts].reverse().find((c) => c < at - 0.02);
      if (next != null) tl.seek(Math.min(next, Math.max(0, t - 0.001)));
    };
    if (pb) pb.addEventListener('click', () => edit(-1));
    if (pf) pf.addEventListener('click', () => edit(1));
  }

  /* The panel titles follow the cut: "Program: <cut>", "Timeline: <cut>". */
  function setTitles(name) {
    const n = name ? `: ${name}` : '';
    const p = $('#progTitle .lbl'), t = $('#tlTitle .lbl');
    if (p) p.textContent = `Program${n}`;
    if (t) t.textContent = `Timeline${n}`;
  }

  window.dock = {
    wait: waitOn,
    open, reveal, badge, keys, fit, isOpen, setTitles, mediaWait,
    current: () => cur,
    focused: focusedName,
    layout: () => ({ ...px, frac: { ...frac } }),
    resetLayout,
    project: { reload: loadClips, select: selectClip, clips: () => project.clips.slice(),
               view: (v) => { if (v) { project.view = v; store.set(VIEW_KEY, v); paintProject(); } return project.view; } },
    source,
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
