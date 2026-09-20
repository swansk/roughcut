/* Roughcut — effects on the board (INTAKE M12). Filled by lane agent/fx-ui: the FX
 * tool in the dock (#fx), the monitor overlay (#fxCanvas), the sketch. This stub keeps
 * the page whole until the lane lands: it says so in the tool and exposes the API
 * shape the lead's server and app.js can already count on. */
(() => {
  'use strict';
  const el = document.querySelector('#fx');
  if (el) el.innerHTML = '<div class="hint">effects: the FX tool is not built yet (lane fx-ui)</div>';
  window.fx = { ready: false, refresh: () => {}, badge: () => {} };
})();
