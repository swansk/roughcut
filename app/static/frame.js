/* The frame's shape in the browser (wave 25, lane VERTICAL; roughcut/frame.py is its twin).
 *
 * A cut has a frame shape: 16:9 (every cut with none stored), 9:16 or 1:1. The timeline holds
 * it (tl.frameShape(), changed by Sequence Settings and an undo of that); this file is what the
 * rest of the page asks:
 *
 *   rcFrame.shape()                  the cut's shape
 *   rcFrame.aspect(shape?)           width / height of it
 *   rcFrame.wide(shape?)             true for 16:9: nothing about such a cut changes
 *   rcFrame.reframe(controls)        {x, y, zoom} of a shot (0..1, 0..1, ≥ 1)
 *   rcFrame.window(sw, sh, shape, x, y, zoom)
 *                                    [left, top, width, height] of the source the frame shows,
 *                                    in fractions of it — THE function: roughcut/frame.py
 *                                    `window` is the same arithmetic, the render's crop is cut
 *                                    from it, and a test holds the two to the same numbers
 *   rcFrame.canvasSize(vw, vh)       the frame's own pixel size for a canvas drawn over the
 *                                    picture (the effects'): the video's size at 16:9, else the
 *                                    shape with the video's short side
 *   rcFrame.paint()                  the Program's box in the shape (CSS vars on `.screen`)
 */
(function () {
  'use strict';

  const SHAPES = { '16:9': [16, 9], '9:16': [9, 16], '1:1': [1, 1] };
  const DEFAULT = '16:9';

  function shape() {
    try {
      if (window.tl && typeof window.tl.frameShape === 'function') return window.tl.frameShape() || DEFAULT;
      // eslint-disable-next-line no-undef
      if (typeof P !== 'undefined' && P && SHAPES[P.frame]) return P.frame;
    } catch (e) { /* the board is not up yet */ }
    return DEFAULT;
  }
  const aspect = (s) => { const [w, h] = SHAPES[s || shape()] || SHAPES[DEFAULT]; return w / h; };
  const wide = (s) => (s || shape()) === DEFAULT;

  function reframe(c) {
    const n = (k, d) => { const v = Number(c && c[k]); return Number.isFinite(v) ? v : d; };
    return {
      x: Math.min(1, Math.max(0, n('reframe_x', 50) / 100)),
      y: Math.min(1, Math.max(0, n('reframe_y', 50) / 100)),
      zoom: Math.min(4, Math.max(1, n('reframe_zoom', 100) / 100)),
    };
  }

  function win(sw, sh, s, x = 0.5, y = 0.5, zoom = 1) {
    const sa = sh ? sw / sh : 16 / 9, fa = aspect(s);
    let ww, wh;
    if (sa >= fa) { ww = fa / sa; wh = 1; } else { ww = 1; wh = sa / fa; }
    const z = Math.max(1, Number(zoom) || 1);
    ww /= z; wh /= z;
    return [Math.min(1, Math.max(0, x)) * (1 - ww), Math.min(1, Math.max(0, y)) * (1 - wh), ww, wh];
  }

  function canvasSize(vw, vh) {
    if (wide()) return [vw, vh];
    const short = Math.max(2, Math.min(vw, vh));
    const a = aspect();
    return a < 1 ? [short, Math.round(short / a)] : [Math.round(short * a), short];
  }

  function paint() {
    const [w, h] = SHAPES[shape()] || SHAPES[DEFAULT];
    const el = document.querySelector('.screen');
    if (!el) return;
    if (el.style.getPropertyValue('--fw') !== String(w)) el.style.setProperty('--fw', String(w));
    if (el.style.getPropertyValue('--fh') !== String(h)) el.style.setProperty('--fh', String(h));
    const want = wide() ? '' : shape();
    if ((el.getAttribute('data-frame') || '') !== want) {
      if (want) el.setAttribute('data-frame', want); else el.removeAttribute('data-frame');
    }
  }

  window.rcFrame = { SHAPES, shape, aspect, wide, reframe, window: win, canvasSize, paint };
})();
