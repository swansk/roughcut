/* voice.js: hold to speak into the AI prompt (wave 17 FOLDIN, Karl's K4 b).
 *
 * The footage screen (/open) took the film's sentence by voice and the pass (/floor) took
 * a keep's note by voice: hold, speak, release, and the words land in the field (POST
 * /api/dictate, the server's recogniser; INTAKE M4). Those screens are gone (fold in, then
 * remove) and the editor's one place for words to the AI is the AI prompt (aiprompt.js),
 * so the voice is there: a mic at the field's right end, where the Ctrl+J hint sits at
 * rest — shown while the prompt has the focus (Ctrl+J, or a click in it), so nothing is
 * added to the board at rest. Hold it, speak, release: what was heard is typed into the
 * prompt, to read (and price) before ↵ sends it — speaking never asks the AI by itself.
 *
 * Only where the recogniser is installed (GET /api/dictate, a cheap check); a 501 from the
 * server hides it and says so, a 413 says the recording was too long. The browser's
 * microphone is asked for on the first hold; refused, it says so and the mic goes.
 * Test hooks: `ai-mic` (the button; `.live` while it listens), window.rcVoice.
 */
(function () {
  'use strict';
  const V = { avail: null, rec: null, stream: null, chunks: [], held: false, btn: null, sent: 0, last: '' };
  const $ = (s, r) => (r || document).querySelector(s);
  const say = (msg, ms) => { if (typeof window.toast === 'function') window.toast(msg, ms || 3000); };
  const MIC = '<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.6"'
    + ' stroke-linecap="round" aria-hidden="true"><rect x="5.5" y="1.5" width="5" height="8.5" rx="2.5"/>'
    + '<path d="M3 7.5a5 5 0 0 0 10 0M8 12.5v2"/></svg>';
  const CSS = `
#aiPrompt .aip-mic { display: none; flex: none; align-items: center; justify-content: center; width: 20px; height: 16px;
  padding: 0; border: 1px solid var(--line, #333337); border-radius: 3px; background: #1f1f23;
  color: var(--dim, #94949c); cursor: pointer; touch-action: none; user-select: none; -webkit-user-select: none; }
#aiPrompt.focus .aip-mic:not([hidden]), #aiPrompt .aip-mic.live:not([hidden]) { display: inline-flex; }
#aiPrompt .aip-mic:hover { color: var(--text, #dcdcdf); border-color: var(--line-hi, #46464c); }
#aiPrompt .aip-mic.live { color: #f0b8b8; border-color: #f0b8b8; background: #3a1f1f; }
`;

  function input() { return $('#aiPrompt [data-testid="ai-prompt-input"]'); }

  function land(text) {
    const el = input();
    if (!el) return;
    const cur = el.value.trim();
    el.value = cur ? `${cur} ${text}` : text;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.focus();
    try { el.setSelectionRange(el.value.length, el.value.length); } catch (e) { /* fine */ }
  }

  function live(on) {
    if (V.btn) {
      V.btn.classList.toggle('live', !!on);
      V.btn.title = on ? 'listening… release to stop' : 'Hold to speak: what you say is typed into the AI prompt';
    }
  }

  function gone(why) {
    V.avail = false;
    if (V.btn) V.btn.hidden = true;
    live(false);
    say(`${why} — type instead`, 4000);
  }

  async function start() {
    if (V.held || V.avail === false) return;
    V.held = true;
    live(true);
    if (!navigator.mediaDevices || !window.MediaRecorder) { stop(); gone('no microphone in this browser'); return; }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      stop();
      gone(e && e.name === 'NotAllowedError' ? 'the microphone is blocked for this site' : `no microphone (${(e && e.name) || e})`);
      return;
    }
    if (!V.held) { stream.getTracks().forEach((t) => t.stop()); live(false); return; }   // released first
    const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : '';
    V.stream = stream;
    V.chunks = [];
    V.rec = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
    V.rec.ondataavailable = (e) => { if (e.data && e.data.size) V.chunks.push(e.data); };
    V.rec.onstop = () => {
      const blob = new Blob(V.chunks, { type: mime || 'audio/webm' });
      stream.getTracks().forEach((t) => t.stop());
      V.rec = null;
      V.stream = null;
      send(blob);
    };
    V.rec.start();
  }

  function stop() {
    if (!V.held) return;
    V.held = false;
    if (V.rec && V.rec.state !== 'inactive') V.rec.stop();
    else live(false);
  }

  async function send(blob) {
    live(false);
    let r;
    try {
      r = await fetch('/api/dictate', { method: 'POST', headers: { 'content-type': blob.type || 'audio/webm' }, body: blob });
    } catch (e) {
      V.sent++;
      gone('dictation unreachable');
      return;
    }
    V.sent++;
    if (r.status === 501) { gone('dictation is not installed on this machine'); return; }
    const d = await r.json().catch(() => ({}));
    if (r.status === 413) { say(`too long — ${d.detail || 'at most 30 s'}`, 5000); return; }
    if (!r.ok) { say(`dictation failed: ${d.detail || r.status}`, 5000); return; }
    if (!d.text) { say('heard nothing — type instead'); return; }
    V.last = d.text;
    land(d.text);
    say(`heard in ${((d.latency_ms || 0) / 1000).toFixed(1)} s — read it, then ↵ asks`);
  }

  function mount() {
    const field = $('#aiPrompt .aip-field');
    if (!field) return false;
    if (field.querySelector('.aip-mic')) return true;
    if (!$('#voice-css')) {
      const st = document.createElement('style');
      st.id = 'voice-css';
      st.textContent = CSS;
      document.head.appendChild(st);
    }
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'aip-mic';
    b.dataset.testid = 'ai-mic';
    b.setAttribute('aria-label', 'hold to speak into the AI prompt');
    b.hidden = V.avail !== true;
    b.innerHTML = MIC;
    field.appendChild(b);
    V.btn = b;
    live(false);
    // the prompt keeps the focus (and the mic its place) while it is held
    b.addEventListener('pointerdown', (e) => { e.preventDefault(); e.stopPropagation(); start(); });
    for (const ev of ['pointerup', 'pointercancel', 'pointerleave']) b.addEventListener(ev, stop);
    b.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); });
    return true;
  }

  function init() {
    fetch('/api/dictate').then((r) => (r.ok ? r.json() : null)).then((d) => {
      V.avail = !!(d && d.available);
      if (V.btn) V.btn.hidden = !V.avail;
    }).catch(() => { V.avail = false; });
    // the AI prompt mounts with the board: try until its field is there
    let n = 0;
    const tryMount = () => { if (!mount() && n++ < 100) setTimeout(tryMount, 100); };
    tryMount();
    window.addEventListener('blur', stop);
  }

  window.rcVoice = { state: V, start, stop, send, land };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
