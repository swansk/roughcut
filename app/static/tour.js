/* The walk-through (redesign wave 12, lane TEACH; REDESIGN-GOAL dimension 16, Karl's K3 e).
 * A first run teaches the loop in at most six steps on the real workspace, skippable and
 * re-openable from Help; AI controls get hovers that say what they do, cost and change.
 * Scaffold: the contract only (judge/WAVE12.md); lane TEACH builds it. */
(function () {
  'use strict';
  window.rcTour = {
    start(which) {},    // which: undefined = the whole loop, 'ai' = the AI features
    active: false,
  };
})();
