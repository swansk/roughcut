/* The AI prompt (redesign wave 12, lane AIPROMPT; REDESIGN-GOAL dimension 14, Karl's K3 c).
 * One always-visible place to ask the AI, scoped by the selection, plus the AI menu's
 * commands and "Ask AI about this…" on clips and blocks. Scaffold: the contract only
 * (judge/WAVE12.md); lane AIPROMPT builds it. */
(function () {
  'use strict';
  window.rcAI = {
    focus(text) {},             // focus the prompt, optionally pre-filled
    ask(text, scope) {},        // POST /api/assist and show its working / result states
    command(name) {},           // the AI menu: prompt, firstpass, ask, shot, colour, fx, deep, find, audit
    askAbout(target) {},        // {kind:'segment'|'clip'|'range', id?, clip?, in?, out?}
    scope() { return { kind: 'sequence' }; },
  };
})();
