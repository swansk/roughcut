/* The Claude CLI needs you — one banner, every screen.
 *
 * Karl, 2026-10-03: "make it easier for me to realize I need to grant claude cli
 * permissions (when needed) from the app". The board's header pill said it in forty
 * characters of red; the pass and the open screen said nothing at all, and a job that
 * hit a signed-out CLI said it in its own log. This polls /api/backend and, when the
 * server has a `fix` — sign in, update, allow a tool, wait out the plan's window — puts
 * it across the top of the page in words: what is wrong, the one command to run — for a
 * Windows prompt when the board runs in WSL, on the board's own box (foxtrot) when it runs
 * natively; the server words it for its host — with a copy button, and *Check again*, which re-probes and clears the
 * banner the moment the CLI answers. It sits in the page's flow, never over it, so it
 * cannot cover a control — and above the header, never in it, on one line of its own:
 * every screen's header is one row (INTAKE M16), and the banner is there only while the
 * CLI needs Karl. A long reason is cut short on the line; the whole of it is its title.
 *
 * Self-contained (styles inline) because the three pages do not share a stylesheet.
 */
(function () {
  const POLL_MS = 15000, FAST_MS = 2000;
  let timer = null, shown = null, last = null;

  const css = `
  #cliFix { position: relative; z-index: 50; display: flex; gap: 14px;
    align-items: center; flex-wrap: nowrap; white-space: nowrap; padding: 6px 16px;
    background: #3a2c0c; color: #f6e7c4; border-bottom: 1px solid #e0b050;
    font: 13px/1.4 system-ui, sans-serif; box-sizing: border-box; }
  #cliFix[hidden] { display: none; }
  #cliFix .t { flex: none; font-weight: 600; color: #ffd27a; }
  #cliFix .why { flex: 1 1 0; min-width: 0; overflow: hidden; text-overflow: ellipsis;
    color: #e9d9b4; }
  #cliFix code { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
  #cliFix button { flex: none; }
  #cliFix code { background: #14110a; color: #ffe1a0; border: 1px solid #6b5520;
    border-radius: 4px; padding: 3px 8px; font: 13px ui-monospace, monospace; }
  #cliFix button { background: #e0b050; color: #1a1406; border: 0; border-radius: 5px;
    padding: 5px 10px; font: 600 12px system-ui, sans-serif; cursor: pointer; }
  #cliFix button.ghost { background: transparent; color: #ffd27a;
    border: 1px solid #e0b050; }
  #cliFix button:disabled { opacity: .6; cursor: default; }`;

  function ensure() {
    let el = document.getElementById('cliFix');
    if (el) return el;
    const style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);
    el = document.createElement('div');
    el.id = 'cliFix';
    el.setAttribute('role', 'alert');
    el.hidden = true;
    document.body.insertBefore(el, document.body.firstChild);
    return el;
  }

  function paint(b) {
    last = b;
    const el = ensure();
    const fix = b && b.fix;
    if (!fix) {
      if (!el.hidden) { el.hidden = true; el.innerHTML = ''; announce(); }
      shown = null;
      return;
    }
    const key = fix.kind + '|' + fix.command + '|' + b.state;
    if (key === shown) return;          // nothing changed: leave a hovered button be
    shown = key;
    const checking = b.state === 'checking';
    const appearing = el.hidden;
    el.hidden = false;
    el.innerHTML =
      `<span class="t">⚠ ${esc(fix.title)}</span>` +
      `<span class="why" title="${esc(fix.why)}">${esc(fix.why)}</span>` +
      (fix.command ? `<code>${esc(fix.command)}</code>` +
        `<button class="ghost" data-act="copy">Copy</button>` : '') +
      `<button data-act="check" ${checking ? 'disabled' : ''}>` +
      `${checking ? 'Checking…' : 'Check again'}</button>`;
    el.title = fix.detail || '';
    if (appearing) announce();
    el.querySelector('[data-act="check"]').onclick = check;
    const copy = el.querySelector('[data-act="copy"]');
    if (copy) copy.onclick = async () => {
      try { await navigator.clipboard.writeText(fix.command); copy.textContent = 'Copied'; }
      catch (e) { copy.textContent = 'select it ↑'; }
    };
  }

  // Pages that size themselves to the viewport (the board's dock) re-measure on this.
  function announce() { window.dispatchEvent(new Event('clifix')); }

  function esc(s) {
    return String(s || '').replace(/[&<>"]/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  async function poll() {
    clearTimeout(timer);
    let b = null;
    try { b = await (await fetch('/api/backend')).json(); } catch (e) { /* server away */ }
    if (b) paint(b);
    timer = setTimeout(poll, b && b.state === 'checking' ? FAST_MS : POLL_MS);
  }

  async function check() {
    paint({ ...(last || {}), state: 'checking' });
    try { await fetch('/api/backend/probe', { method: 'POST' }); } catch (e) {}
    shown = null;
    poll();
  }

  window.cliFix = { poll, check };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', poll);
  else poll();
})();
