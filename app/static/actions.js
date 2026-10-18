/* Actions (redesign wave 12, lane AIOPS; REDESIGN-GOAL dimension 15, Karl's K3 d).
 * One vocabulary for every edit: an op list ({op: …}, judge/WAVE12.md) is applied through the
 * same window.tl / window.effects calls a human edit makes, inside one tl.begin/commit (one
 * undo), and an AI's op list is reviewed here (preview, per-op accept / reject) before it
 * lands. Scaffold: the contract only; lane AIOPS builds it. */
(function () {
  'use strict';
  window.rcActions = {
    apply(ops, opts) { return { applied: 0, refused: [] }; },   // one undo entry, label opts.label
    review(result) {},                                          // an /api/assist "ops" result
    describe(op) { return String(op && op.op || ''); },         // one line per op, for the diff
  };
})();
