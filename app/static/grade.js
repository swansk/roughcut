/* Roughcut — the monitor shows the grade without a render (INTAKE M10, I10.4; decision 4).
 *
 * A <canvas> sits over the monitor's <video> buffers (`#player video.pvbuf`) and mirrors
 * whichever one is live through a WebGL shader that applies the shot's 3D LUT — the
 * same 17³ table `/api/lut/{id}` bakes from the same function the render bakes its 33³
 * from, so what the board shows and what the render makes agree by construction.
 *
 * The videos are never replaced: they keep playing, keep their audio, keep the clock
 * (app.js reads their currentTime for the playhead and the cut points). The canvas is
 * a picture of the live one, drawn on every new frame (`requestVideoFrameCallback`,
 * rAF where that is missing) and once more whenever a paused video seeks, so a parked
 * monitor is graded too. It is hidden — the raw video shows — when the shot's LUT is
 * the identity, when the grade is toggled off (`G`), when the shot is not in the cut
 * (Find's clip player and the compare players are never graded), or when WebGL is not
 * to be had.
 *
 * WebGL2 uses a real sampler3D. WebGL1 gets the same flat table as a 2D texture
 * n wide × n² tall — the .cube order (red fastest, then green, then blue) *is* that
 * image, row = blue·n + green — and the shader interpolates between the two blue
 * slices by hand (the packing from blog.frost.kiwi/WebGL-LUTS-made-simple, without
 * re-packing: the table is already laid out that way).
 *
 * API (window.grade):
 *   enabled        true when the grade is shown (G flips it); while false, #ungraded
 *                  is shown on the monitor
 *   toggle()       flip it; returns the new state
 *   setShot(id)    the monitor moved to this shot: fetch (or take from cache) its LUT
 *                  and draw. null / an id that 404s → identity, canvas hidden.
 *   invalidate()   the colour block was saved: drop every cached LUT and refetch the
 *                  current shot's.
 *   prefetch(id)   the player parked this shot for the next cut (app.js armNext): fetch
 *                  its LUT and put it in its own texture now, so the cut only binds it.
 *   ready          true once the canvas has a context (tests read it)
 *   draw()         redraw now (a paused monitor gets no frame callback) — Effect Controls
 *                  calls it after every change so its preview is on the next frame
 *   probe()        the mean [r, g, b] of the picture shown now (tests and the judge)
 *   draws          frames drawn so far
 *
 * The live preview (/modules/effects.js, `effects.adjust(id)`): what the shot's colour
 * is now but the LUT was not baked with — a gain before the table, the balance's
 * shoulder and lift when the table has none, Lumetri contrast and saturation after it —
 * drawn at once; the save's refetch bakes it in. /api/lut says which balance it baked.
 *
 * Where it draws (wave 7). On a software GL — headless Chromium, the judge's and the
 * gate's box — a WebGL canvas costs the main thread twice a frame: the draw (the frame
 * scaled, uploaded, shaded: 15-30 ms at load 10-20) and, at the compositor commit, a
 * synchronous read-back of the canvas that waits for the GPU process to finish it
 * (GLES2::ReadPixels, 15-37 ms). Traced, that was ~50 ms of every played frame, the
 * frame rate halved and the holes after a cut. So the monitor's canvas is handed to a
 * worker (`transferControlToOffscreen`): each new frame goes to it as a `VideoFrame`
 * (a reference, not a copy), with the LUT and the live-preview uniforms, and the worker
 * scales, uploads, shades and commits off the main thread — at most one frame in flight,
 * the newest waiting. The renderer is one function (`Renderer` below) the worker runs
 * from its own source and the page runs for probe() (on a canvas of its own, drawn and
 * read in the same task) and as the fallback where a worker, OffscreenCanvas or
 * VideoFrame is not to be had.
 */
(function () {
  'use strict';

  const LUT_N = 17;

  /* ------------------------------------------------------------ the renderer
   * Self-contained: its source is also the worker's (Function.prototype.toString), so it
   * closes over nothing. `canvas` is a <canvas> or an OffscreenCanvas; `makeStage(w, h)`
   * the 2D canvas a frame is scaled into before its upload. */
  function Renderer(canvas, makeStage) {
    const VS = `
      attribute vec2 a_pos;
      varying vec2 v_uv;
      void main() {
        v_uv = a_pos * 0.5 + 0.5;
        gl_Position = vec4(a_pos, 0.0, 1.0);
      }`;

    const VS2 = `#version 300 es
      in vec2 a_pos;
      out vec2 v_uv;
      void main() {
        v_uv = a_pos * 0.5 + 0.5;
        gl_Position = vec4(a_pos, 0.0, 1.0);
      }`;

    // WebGL2: a 3D texture, trilinear by the sampler; the half-texel offset puts 0 and 1
    // on the first and last grid points rather than half a cell inside them.
    const FS2 = `#version 300 es
      precision mediump float;
      precision mediump sampler3D;
      in vec2 v_uv;
      uniform sampler2D u_video;
      uniform sampler3D u_lut;
      uniform float u_n;
      uniform vec3 u_pre;
      uniform float u_con;
      uniform float u_sat;
      uniform float u_knee;
      uniform float u_lift;
      uniform vec4 u_tone;      // Lumetri blacks, shadows, highlights, whites, each /100
      uniform float u_toneOn;
      out vec4 outColor;
      void main() {
        vec3 c = texture(u_video, v_uv).rgb * u_pre;
        if (u_knee < 1.0) {
          vec3 o = u_knee + (1.0 - u_knee) * (1.0 - exp(-(c - u_knee) / (1.0 - u_knee)));
          c = mix(c, o, step(u_knee, c)) + u_lift;
        }
        c = clamp(c, 0.0, 1.0);
        vec3 p = c * ((u_n - 1.0) / u_n) + 0.5 / u_n;
        vec3 y = (texture(u_lut, p).rgb - 0.5) * u_con + 0.5;
        if (u_toneOn > 0.5) {
          vec3 x = clamp(y, 0.0, 1.0), ix = 1.0 - x;
          y = x + 0.25 * u_tone.x * ix * ix * ix + 1.5 * u_tone.y * x * ix * ix
                + 1.5 * u_tone.z * x * x * ix + 0.25 * u_tone.w * x * x * x;
        }
        y = mix(vec3(dot(y, vec3(0.2126, 0.7152, 0.0722))), y, u_sat);
        outColor = vec4(clamp(y, 0.0, 1.0), 1.0);
      }`;

    // WebGL1: the table as an n × n² image. Bilinear inside a blue slice (red across,
    // green down), the blue interpolation by hand between the two neighbouring slices.
    const FS1 = `
      precision mediump float;
      varying vec2 v_uv;
      uniform sampler2D u_video;
      uniform sampler2D u_lut;
      uniform float u_n;
      uniform vec3 u_pre;
      uniform float u_con;
      uniform float u_sat;
      uniform float u_knee;
      uniform float u_lift;
      uniform vec4 u_tone;
      uniform float u_toneOn;
      void main() {
        vec3 c = texture2D(u_video, v_uv).rgb * u_pre;
        if (u_knee < 1.0) {
          vec3 o = u_knee + (1.0 - u_knee) * (1.0 - exp(-(c - u_knee) / (1.0 - u_knee)));
          c = mix(c, o, step(u_knee, c)) + u_lift;
        }
        c = clamp(c, 0.0, 1.0);
        float bf = c.b * (u_n - 1.0);
        float b0 = floor(bf);
        float b1 = min(b0 + 1.0, u_n - 1.0);
        float t = bf - b0;
        float x = (c.r * (u_n - 1.0) + 0.5) / u_n;
        float y0 = (b0 * u_n + c.g * (u_n - 1.0) + 0.5) / (u_n * u_n);
        float y1 = (b1 * u_n + c.g * (u_n - 1.0) + 0.5) / (u_n * u_n);
        vec3 s0 = texture2D(u_lut, vec2(x, y0)).rgb;
        vec3 s1 = texture2D(u_lut, vec2(x, y1)).rgb;
        vec3 y = (mix(s0, s1, t) - 0.5) * u_con + 0.5;
        if (u_toneOn > 0.5) {
          vec3 x = clamp(y, 0.0, 1.0), ix = 1.0 - x;
          y = x + 0.25 * u_tone.x * ix * ix * ix + 1.5 * u_tone.y * x * ix * ix
                + 1.5 * u_tone.z * x * x * ix + 0.25 * u_tone.w * x * x * x;
        }
        y = mix(vec3(dot(y, vec3(0.2126, 0.7152, 0.0722))), y, u_sat);
        gl_FragColor = vec4(clamp(y, 0.0, 1.0), 1.0);
      }`;


    const NO_TONE = [0, 0, 0, 0];
    const NO_ADJUST = { pre: [1, 1, 1], con: 1, sat: 1, knee: 1, lift: 0, tone: NO_TONE };
    const TEX_KEEP = 8;

    function compile(gl, type, src) {
      const sh = gl.createShader(type);
      gl.shaderSource(sh, src);
      gl.compileShader(sh);
      if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
        const log = gl.getShaderInfoLog(sh);
        gl.deleteShader(sh);
        throw new Error(`grade: shader failed — ${log}`);
      }
      return sh;
    }

    const opts = { alpha: false, antialias: false, premultipliedAlpha: false,
                   preserveDrawingBuffer: false };
    let gl = null, gl2 = false;
    try { gl = canvas.getContext('webgl2', opts); gl2 = !!gl; } catch (e) { gl = null; }
    if (!gl) {
      try { gl = canvas.getContext('webgl', opts) || canvas.getContext('experimental-webgl', opts); }
      catch (e) { gl = null; }
    }
    if (!gl) return null;
    const prog = gl.createProgram();
    gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, gl2 ? VS2 : VS));
    gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, gl2 ? FS2 : FS1));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      throw new Error(`grade: link failed — ${gl.getProgramInfoLog(prog)}`);
    }
    gl.useProgram(prog);
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const aPos = gl.getAttribLocation(prog, 'a_pos');
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);
    gl.uniform1i(gl.getUniformLocation(prog, 'u_video'), 0);
    gl.uniform1i(gl.getUniformLocation(prog, 'u_lut'), 1);
    const U = {};
    ['u_n', 'u_pre', 'u_con', 'u_sat', 'u_knee', 'u_lift', 'u_tone', 'u_toneOn'].forEach((k) => {
      U[k] = gl.getUniformLocation(prog, k);
    });

    const texVideo = gl.createTexture();
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, texVideo);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    const lutTarget = gl2 ? gl.TEXTURE_3D : gl.TEXTURE_2D;

    const texs = new Map();       // LUT key → texture, oldest first (LRU, TEX_KEEP)
    let current = null;           // the key the shader reads now
    let stage = null, stageCtx = null;
    let frameKey = null;          // the parked frame in texVideo now: no second upload of it

    /* A LUT's texture, made and filled once per key, then only bound (a 17³ RGB table is
     * 14 KB, measured at 0.1 ms to fill; kept so a cut never deletes and re-creates one,
     * and so the next shot's can be made before its cut). Leaves TEXTURE1 bound to it. */
    function lut(key, n, data) {
      gl.activeTexture(gl.TEXTURE1);
      let tex = texs.get(key);
      if (tex) {
        texs.delete(key);           // most recently used last
        texs.set(key, tex);
        gl.bindTexture(lutTarget, tex);
        return tex;
      }
      if (!data) return null;
      tex = gl.createTexture();
      gl.bindTexture(lutTarget, tex);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
      gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
      gl.texParameteri(lutTarget, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(lutTarget, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texParameteri(lutTarget, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(lutTarget, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      if (gl2) {
        gl.texParameteri(lutTarget, gl.TEXTURE_WRAP_R, gl.CLAMP_TO_EDGE);
        gl.texImage3D(lutTarget, 0, gl.RGB8, n, n, n, 0, gl.RGB, gl.UNSIGNED_BYTE, data);
      } else {
        gl.texImage2D(lutTarget, 0, gl.RGB, n, n * n, 0, gl.RGB, gl.UNSIGNED_BYTE, data);
      }
      texs.set(key, tex);
      for (const [old, t] of texs) {
        if (texs.size <= TEX_KEEP) break;
        if (old === key || old === current) continue;
        texs.delete(old);
        gl.deleteTexture(t);
      }
      return tex;
    }

    function forget() {
      for (const t of texs.values()) gl.deleteTexture(t);
      texs.clear();
      current = null;
    }

    /* The frame into texVideo at w×h: whole when that is its size, else scaled first in
     * a CPU 2D canvas (verdict 4 punch 5: the 1280×720 proxy went up whole on every
     * frame to be shown 386 px wide; an accelerated stage round-trips through the GL
     * process, measured 3 ms against 7-14 ms a frame at 384 px wide). A parked frame
     * already there (`key`) is not sent again: an Effect Controls change on a paused
     * monitor redraws with new uniforms only. */
    function upload(src, sw, sh, w, h, key) {
      if (key && key === frameKey) return true;
      frameKey = null;
      try {
        if (w === sw && h === sh) {
          gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
          gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, src);
        } else {
          if (!stage) {
            stage = makeStage(w, h);
            stageCtx = stage.getContext('2d', { alpha: false, willReadFrequently: true });
          }
          if (stage.width !== w || stage.height !== h) { stage.width = w; stage.height = h; }
          stageCtx.drawImage(src, 0, 0, w, h);
          gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
          gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, stage);
        }
      } catch (e) {
        return false;
      }
      frameKey = key || null;
      return true;
    }

    /* Draw `src` (a <video> or a VideoFrame, sw×sh) at w×h through the LUT `key` (made
     * from n/data when this renderer has not seen it) with the live preview's `adj`. */
    function render(src, sw, sh, w, h, key, n, data, adj, parkedKey) {
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
        frameKey = null;
      }
      gl.viewport(0, 0, w, h);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, texVideo);
      if (!upload(src, sw, sh, w, h, parkedKey)) return false;   // no frame yet; the next one will be
      if (!lut(key, n, data)) return false;
      if (current !== key) { current = key; gl.uniform1f(U.u_n, n); }
      const a = adj || NO_ADJUST;
      gl.uniform3f(U.u_pre, a.pre[0], a.pre[1], a.pre[2]);
      gl.uniform1f(U.u_con, a.con);
      gl.uniform1f(U.u_sat, a.sat);
      gl.uniform1f(U.u_knee, a.knee ?? 1);
      gl.uniform1f(U.u_lift, a.lift ?? 0);
      const tone = a.tone || NO_TONE;
      gl.uniform4f(U.u_tone, tone[0], tone[1], tone[2], tone[3]);
      gl.uniform1f(U.u_toneOn, tone.some((x) => x !== 0) ? 1 : 0);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      return true;
    }

    /* The mean [r, g, b] of what was just drawn (same task: no preserved buffer). */
    function mean() {
      const w = canvas.width, h = canvas.height;
      if (!w || !h) return null;
      const px = new Uint8Array(w * h * 4);
      gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px);
      const m = [0, 0, 0];
      for (let i = 0; i < px.length; i += 4) { m[0] += px[i]; m[1] += px[i + 1]; m[2] += px[i + 2]; }
      const k = px.length / 4;
      return m.map((x) => Math.round((x / k) * 100) / 100);
    }

    return { gl2, lut, forget, render, mean };
  }

  /* The worker: the renderer on the monitor's OffscreenCanvas. One message kind each way:
   * `frame` in (drawn, then closed), `drawn` back; `lut` in makes a texture ahead of a cut;
   * `forget` drops them all (a colour save). Committed at the end of its task, off the
   * page's main thread. */
  const WORKER_SRC = `'use strict';
const Renderer = ${Renderer.toString()};
let R = null;
onmessage = (e) => {
  const m = e.data;
  if (m.t === 'init') {
    try { R = Renderer(m.canvas, (w, h) => new OffscreenCanvas(w, h)); }
    catch (err) { R = null; }
    postMessage({ t: 'ready', ok: !!R });
    return;
  }
  if (m.t === 'frame') {
    try { if (R) R.render(m.frame, m.sw, m.sh, m.w, m.h, m.key, m.n, m.data, m.adj, null); }
    catch (err) { /* a frame that will not draw: the next one will */ }
    finally { m.frame.close(); }
    postMessage({ t: 'drawn' });
    return;
  }
  if (!R) return;
  if (m.t === 'lut') R.lut(m.key, m.n, m.data);
  else if (m.t === 'forget') R.forget();
};
`;

  const G = {
    enabled: true,
    ready: false,
    ok: false,               // a renderer is up (on the main thread or in the worker)
    canvas: null,
    R: null,                 // the main-thread renderer on #gradeCanvas (fallback mode)
    P: null,                 // probe()'s renderer, on a canvas of its own (worker mode)
    worker: null,            // the worker holding #gradeCanvas (worker mode)
    inflight: false,         // a frame is with the worker
    sentAt: 0,               // when it went
    pending: null,           // the newest frame waiting for it
    sentKey: null,           // the LUT key the worker was last sent the data of
    videos: [],
    shot: null,              // the segment id the monitor is on
    lut: null,               // the current shot's {n, identity, data} or null
    cache: new Map(),        // id → the LUT record, or the promise fetching it
    keys: 0,                 // LUT records are keyed by a serial (renderer textures)
    gen: 0,                  // bumps per setShot / invalidate: a late fetch is ignored
    frameGen: 0,             // bumps per re-arm of the frame callback
    epoch: 0,                // bumps per invalidate: a fetch from before it is not cached
    drawReq: 0,              // the pending coalesced draw (drawSoon), a rAF id
    draws: 0,                // frames drawn (tests read it)
    box: { w: 0, h: 0 },     // the monitor's box (ResizeObserver), CSS px
  };

  function $(s) { return document.querySelector(s); }

  function live() {
    return G.videos.find((v) => v.classList.contains('live')) || null;
  }

  function stage2d() { return document.createElement('canvas'); }

  /* ------------------------------------------------------------ the size */
  /* The size the frame is drawn at: the picture as the monitor shows it (object-fit:
   * contain in the monitor's box, times the device pixel ratio), never above the video's
   * own, so the upload and the shader work on the pixels that are seen. */
  /* Premiere's Playback Resolution: while the picture plays, the grade is drawn at most
   * PLAY_W wide (CSS scales it up); paused, it is drawn at the monitor's full size again, so
   * a parked frame is always sharp. The per-frame upload and shader cost scale with the
   * drawn pixels: at 674-841 px wide (the wave-4 monitors) a software-GL frame cost enough
   * to open 100 ms holes mid-shot under load (the wave-6 perf gate). */
  const PLAY_W = 640;
  /* The setting itself, as in Premiere's Program monitor (verdict 6 punch 10): the
   * transport's menu (#playRes) — Auto (the rule above: at most PLAY_W wide), Full (the
   * monitor's own size), 1/2 or 1/4 of it — while playing; paused is always full. Kept per
   * browser; `grade.playRes` reads and sets it. */
  const RES_KEY = 'roughcut.playRes';
  const RES = { auto: null, full: 1, '1/2': 0.5, '1/4': 0.25 };
  G.res = (() => {
    try { const v = localStorage.getItem(RES_KEY); return v in RES ? v : 'auto'; } catch (e) { return 'auto'; }
  })();
  function setRes(v) {
    if (!(v in RES)) return G.res;
    G.res = v;
    try { localStorage.setItem(RES_KEY, v); } catch (e) { /* private mode: this page only */ }
    const sel = $('#playRes');
    if (sel && sel.value !== v) sel.value = v;
    settle();
    return v;
  }
  /* Playing is the player's state, not the live buffer's: at a cut the outgoing buffer is
   * paused but stays on screen until the incoming one has its frame (app.js showWhenReady),
   * and drawn as a parked frame it went up at full size — the canvas reallocated to 674 px
   * and back to 640 a frame later, in the frames right after the cut (wave 7). */
  function playerPlaying() {
    try { return typeof player === 'object' && !!player && !!player.playing; } catch (e) { return false; }
  }
  function drawSize(vw, vh, playing) {
    const dpr = window.devicePixelRatio || 1;
    const bw = G.box.w * dpr, bh = G.box.h * dpr;
    if (!bw || !bh) return [vw, vh];
    let s = Math.min(1, bw / vw, bh / vh);
    if (playing) s = RES[G.res] == null ? Math.min(s, PLAY_W / vw) : s * RES[G.res];
    if (s > 0.9) return [vw, vh];   // near full size: the scale costs more than it saves
    return [Math.max(2, Math.round(vw * s / 2) * 2), Math.max(2, Math.round(vh * s / 2) * 2)];
  }

  /* ------------------------------------------------------------ drawing */
  /* What a draw of the live video needs now, or null when there is nothing to draw: no
   * renderer, grade off, identity LUT, no live video, or a video with no frame yet. */
  function job(raw) {
    const v = live();
    const adj = raw ? null : adjustNow();
    if (!raw && (!G.ok || !G.enabled || !G.lut || (G.lut.identity && !adj))) return null;
    if (!G.ok || !v || v.readyState < 2) return null;
    const vw = v.videoWidth, vh = v.videoHeight;
    if (!vw || !vh) return null;
    const [w, h] = drawSize(vw, vh, (!v.paused && !v.ended) || playerPlaying());
    const rec = raw || G.lut.identity ? IDENTITY_TABLE : G.lut;
    const parked = v.paused && !v.seeking
      ? `${v.id}|${v.currentSrc}|${v.currentTime}|${w}x${h}` : null;
    return { v, vw, vh, w, h, rec, adj, parked };
  }

  function drawOn(R, j) {
    return R.render(j.v, j.vw, j.vh, j.w, j.h, j.rec.key, j.rec.n, j.rec.data, j.adj, j.parked);
  }

  /* Draw the live video's current frame through the LUT. */
  function draw(raw) {
    const j = job(raw);
    if (!j) return;
    if (G.worker) { toWorker(j); return; }
    if (drawOn(G.R, j)) G.draws++;
  }

  /* The frame to the worker: a VideoFrame of the live video (a reference to the decoded
   * frame), the LUT's data the first time after a change of LUT, the uniforms. One in
   * flight; a newer frame replaces the one waiting, which is closed unsent. */
  function toWorker(j) {
    let frame;
    try { frame = new VideoFrame(j.v, { timestamp: Math.round((j.v.currentTime || 0) * 1e6) }); }
    catch (e) { return; }                       // no frame to take yet: the next one
    const m = { t: 'frame', frame, sw: frame.displayWidth, sh: frame.displayHeight,
                w: j.w, h: j.h, rec: j.rec, adj: j.adj };
    // a worker that never answered (it died) does not hold the picture for good
    if (G.inflight && performance.now() - G.sentAt < 1000) {
      if (G.pending) G.pending.frame.close();
      G.pending = m;
      return;
    }
    post(m);
  }

  function post(m) {
    const rec = m.rec;
    const msg = { t: 'frame', frame: m.frame, sw: m.sw, sh: m.sh, w: m.w, h: m.h,
                  key: rec.key, n: rec.n, data: rec.key !== G.sentKey ? rec.data : null,
                  adj: m.adj ? JSON.parse(JSON.stringify(m.adj)) : null };
    G.sentKey = rec.key;
    G.inflight = true;
    G.sentAt = performance.now();
    try { G.worker.postMessage(msg, [m.frame]); }
    catch (e) { G.inflight = false; try { m.frame.close(); } catch (e2) { /* gone */ } return; }
    G.draws++;
  }

  function fromWorker(e) {
    const m = e.data || {};
    if (m.t === 'drawn') {
      G.inflight = false;
      if (G.pending) { const p = G.pending; G.pending = null; post(p); }
    } else if (m.t === 'ready' && !m.ok) {
      // no WebGL in the worker: the canvas is the worker's, so the raw video shows
      G.ok = false;
      G.ready = false;
      show(false);
    }
  }

  /* ------------------------------------------------------------ the live preview
   * Effect Controls (/modules/effects.js) answers within a frame, not after a save and a
   * LUT bake: `effects.adjust(id, lut)` says how the picture differs from what the LUT
   * was baked with — a per-channel gain before the table (the balance's change: exact,
   * since the balance is the table's first step; with the balance's shoulder and lift
   * when the table was baked with none) and the Lumetri contrast, tone and saturation after
   * it. The save's refetch then bakes the balance in and its difference goes to nothing, so
   * the picture never jumps (contrast, tone and saturation are never baked: always here).
   *
   * Lumetri Basic Correction's tone (wave 6, verdict 5 punch 4), per channel in RGB 0..1,
   * after the LUT and the contrast, before the saturation — research/tools/assemble.py
   * renders the same formula:
   *
   *   y  = (lut(c) − 0.5) · (1 + contrast/200) + 0.5                       contrast
   *   x  = clamp(y, 0, 1)                         (only when a tone value is not 0)
   *   y' = x + 0.25·b·(1−x)³ + 1.5·s·x(1−x)² + 1.5·h·x²(1−x) + 0.25·w·x³     tone
   *        b, s, h, w = blacks/100, shadows/100, highlights/100, whites/100 (−1..1)
   *   y'' = mix(luma709(y'), y', saturation/100)                            saturation
   *   out = clamp(y'', 0, 1)
   *
   * The weights are the cubic Bernstein basis (b·B0 + s·B1 + h·B2 + w·B3, with B1 and B2
   * at twice the strength of B0 and B3): blacks moves the black point (±0.25 at 0, nothing
   * at 1), whites the white point (±0.25 at 1), shadows peaks at x = 1/3 (±0.222 at ±100),
   * highlights at x = 2/3 (±0.222). With all four at 0 the step (and its clamp) is
   * skipped, so contrast and saturation render exactly as before. */
  const NO_TONE = [0, 0, 0, 0];
  const NO_ADJUST = { pre: [1, 1, 1], con: 1, sat: 1, knee: 1, lift: 0, tone: NO_TONE };
  function adjustNow() {
    const fx = window.effects;
    if (!G.shot || !fx || typeof fx.adjust !== 'function') return null;
    let a = null;
    try { a = fx.adjust(G.shot, G.lut); } catch (e) { a = null; }
    if (!a) return null;
    const near = (x, y) => Math.abs(x - y) < 1e-4;
    const trivial = a.pre.every((x) => near(x, 1)) && near(a.con, 1) && near(a.sat, 1)
      && !((a.knee ?? 1) < 1) && near(a.lift ?? 0, 0) && (a.tone || NO_TONE).every((x) => x === 0);
    return trivial ? null : a;
  }

  /* The mean colour of the picture the monitor shows now (tests and the judge read it):
   * the grade's when the canvas is on, else the raw video's drawn through an identity
   * table. Drawn and read in the same task, on the canvas itself in fallback mode and on
   * a canvas of probe's own in worker mode (the same renderer, the same inputs). */
  function probe() {
    if (!G.ok || !G.canvas) return null;
    const v = live();
    if (!v || v.readyState < 2) return null;
    const on = G.canvas.classList.contains('on');
    let R = G.R;
    if (G.worker) {
      if (!G.P) {
        try { G.P = Renderer(document.createElement('canvas'), stage2d); } catch (e) { G.P = null; }
      }
      R = G.P;
      if (!R) return null;
    }
    const j = job(!on) || (on ? job(true) : null);
    if (!j || !drawOn(R, j)) return null;
    if (!G.worker) G.draws++;
    return R.mean();
  }

  function show(on) {
    if (!G.canvas) return;
    G.canvas.classList.toggle('on', !!on);
  }

  /* Whether the canvas should be visible right now, and make it so. */
  function settle() {
    const on = !!(G.ok && G.enabled && G.lut && (!G.lut.identity || adjustNow()) && live());
    show(on);
    if (on) draw();
  }

  /* ------------------------------------------------------------ frames */
  /* One graded draw per rendering step, after the player's tick. The frame callback and a
   * cut's setShot both ask for it; it runs as an animation-frame callback in the same
   * step (video frame callbacks run before animation frame callbacks, and app.js's tick is
   * one of those, queued a frame earlier). At a cut the outgoing buffer's frame callback
   * used to draw its last frame, then the tick's advance() → setShot drew the incoming
   * one: two draws on the frame of the cut. */
  function drawSoon() {
    if (G.drawReq) return;
    G.drawReq = requestAnimationFrame(() => { G.drawReq = 0; settle(); });
  }

  /* Follow the live video: a new frame → draw. Re-armed on every setShot so the
   * callback rides the element that is live now; a callback from a video that is no
   * longer live simply stops. */
  function armFrames() {
    const v = live();
    const gen = ++G.frameGen;
    if (!v) return;
    const again = () => {
      if (gen !== G.frameGen || v !== live()) return;
      drawSoon();
      if ('requestVideoFrameCallback' in v) v.requestVideoFrameCallback(again);
      else requestAnimationFrame(again);
    };
    if ('requestVideoFrameCallback' in v) v.requestVideoFrameCallback(again);
    else requestAnimationFrame(again);
  }

  /* ------------------------------------------------------------ LUTs */
  function toRecord(d) {
    const n = d.size | 0;
    const t = d.table || [];
    const data = new Uint8Array(n * n * n * 3);
    for (let i = 0; i < data.length && i < t.length; i++) {
      data[i] = Math.max(0, Math.min(255, Math.round(t[i] * 255)));
    }
    return { id: d.id, key: ++G.keys, n, identity: !!d.identity, look: d.look,
             strength: d.strength, balance: d.balance || null, data };
  }

  const IDENTITY = { id: null, key: 'none', n: 2, identity: true, data: null, balance: null };
  // the table an identity shot is drawn through when only the live preview changes it
  const IDENTITY_TABLE = { id: null, key: 'identity', n: 2, identity: true,
    data: new Uint8Array([0, 0, 0, 255, 0, 0, 0, 255, 0, 255, 255, 0,
                          0, 0, 255, 255, 0, 255, 0, 255, 255, 255, 255, 255]) };

  function fetchLut(id) {
    if (G.cache.has(id)) return Promise.resolve(G.cache.get(id));
    const epoch = G.epoch;            // a save (invalidate) while this is in flight: not cached
    const p = fetch(`/api/lut/${encodeURIComponent(id)}?n=${LUT_N}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        const rec = d ? toRecord(d) : IDENTITY;
        if (epoch === G.epoch) G.cache.set(id, rec);
        return rec;
      })
      .catch(() => {
        if (epoch === G.epoch && G.cache.get(id) === p) G.cache.delete(id);
        return IDENTITY;
      });
    G.cache.set(id, p);
    return p;
  }

  async function setShot(id) {
    G.shot = id == null ? null : String(id);
    const gen = ++G.gen;
    armFrames();
    // Motion, opacity and volume follow the live shot too (/modules/effects.js)
    if (window.effects && typeof window.effects.onShot === 'function') {
      try { window.effects.onShot(G.shot); } catch (e) { /* the picture still draws */ }
    }
    if (!G.shot || !G.ok) {
      G.lut = null;
      settle();
      return;
    }
    if (G.shot.startsWith('tmp-')) {
      // Laid in a moment ago: no grade yet, and the server has never heard of `tmp-N`
      // (a 404 per insert, verdict 3 punch 9). Draw it plain and take the LUT once
      // the save has re-keyed it (timeline.js `afterSave`; tl.byId resolves the old id).
      G.lut = null;
      settle();
      const tmp = G.shot;
      let tries = 0;
      const wait = () => {
        if (gen !== G.gen || G.shot !== tmp) return;
        const s = window.tl && typeof tl.byId === 'function' ? tl.byId(tmp) : null;
        if (s && s.id && !String(s.id).startsWith('tmp-')) { setShot(s.id); return; }
        if (++tries < 60) setTimeout(wait, 250);
      };
      setTimeout(wait, 250);
      return;
    }
    // Prefetched (the usual cut while playing): the record is here, the texture made —
    // taken in this task, so the cut's first graded frame has its own shot's LUT.
    const hit = G.cache.get(G.shot);
    const rec = hit && typeof hit.then !== 'function' ? hit : await fetchLut(G.shot);
    if (gen !== G.gen) return;          // the monitor moved on while this was loading
    G.lut = rec;
    // A draw already asked for in this step (the outgoing buffer's frame callback) draws
    // the new shot after this; otherwise now. Never both: one draw per frame at a cut.
    if (!G.drawReq) settle();
  }

  /* The player parked the buffer for the next cut on this shot (app.js armNext): its LUT
   * is fetched and its texture made now, between cuts, so the cut binds it and draws. */
  function prefetch(id) {
    if (id == null || !G.ok) return;
    const sid = String(id);
    if (sid.startsWith('tmp-')) return;
    fetchLut(sid).then((rec) => {
      if (!G.ok || !rec || rec.identity || G.cache.get(sid) !== rec) return;
      if (G.worker) {
        if (G.sentKey === rec.key) return;
        G.worker.postMessage({ t: 'lut', key: rec.key, n: rec.n, data: rec.data });
      } else {
        G.R.lut(rec.key, rec.n, rec.data);    // binds it on TEXTURE1; a draw rebinds the live one
      }
    });
  }

  function invalidate() {
    G.cache.clear();
    G.epoch++;
    // the records are new objects with new keys: the old textures are only weight now
    if (G.worker) G.worker.postMessage({ t: 'forget' });
    else if (G.R) G.R.forget();
    if (G.P) G.P.forget();
    G.sentKey = null;
    return setShot(G.shot);
  }

  /* While the grade is off the picture says so: an "ungraded" tag on the monitor
   * (INTAKE M16 I16.1), where G's state used to be only a toast that faded. */
  function tag() {
    const t = $('#ungraded');
    if (t) t.hidden = G.enabled;
  }

  function toggle() {
    G.enabled = !G.enabled;
    tag();
    settle();
    return G.enabled;
  }

  /* ------------------------------------------------------------ mount */
  function mount() {
    const canvas = $('#gradeCanvas');
    G.videos = [...document.querySelectorAll('#player video.pvbuf')];   // app.js player.vids
    if (!canvas || !G.videos.length) return;
    G.canvas = canvas;
    if (!startWorker(canvas)) {
      try {
        G.R = Renderer(canvas, stage2d);
      } catch (e) {
        console.warn(String(e && e.message || e));
        G.R = null;
      }
      G.ok = G.ready = !!G.R;
    }
    // A paused monitor that seeks (the timeline's scrub, a cue) gets no frame
    // callback; these draw the parked frame. `timeupdate` covers a background tab,
    // where rAF and the frame callback both stop.
    G.videos.forEach((v) => {
      ['seeked', 'loadeddata', 'timeupdate', 'play'].forEach((ev) => {
        v.addEventListener(ev, () => {
          if (v !== live()) return;
          // playing in a visible tab, the frame callback draws every frame already: a
          // timeupdate draw on top of it was a second upload four times a second
          if (ev === 'timeupdate' && !v.paused && !document.hidden) return;
          settle();
        });
      });
    });
    // Playback Resolution's menu on the transport; a choice gives the keys back to the board
    const res = $('#playRes');
    if (res) {
      res.value = G.res;
      res.addEventListener('change', () => { setRes(res.value); res.blur(); });
    }
    // the monitor's box, for drawSize: read here, never per frame (a layout read)
    const box = canvas.parentElement;
    if (box && typeof ResizeObserver === 'function') {
      new ResizeObserver((ents) => {
        const r = ents[ents.length - 1].contentRect;
        G.box = { w: r.width, h: r.height };
      }).observe(box);
    }
  }

  /* The worker mode, where the page can: the canvas goes to a worker for good, so this is
   * decided once, before any context is taken on it. `?grade=main` (or
   * window.ROUGHCUT_GRADE_MAIN) keeps the renderer on the main thread, for comparison. */
  function startWorker(canvas) {
    const mainOnly = window.ROUGHCUT_GRADE_MAIN || /[?&]grade=main\b/.test(location.search);
    if (mainOnly || typeof Worker !== 'function' || typeof VideoFrame !== 'function'
        || typeof OffscreenCanvas !== 'function' || !canvas.transferControlToOffscreen) return false;
    let url = null, w = null;
    try {
      url = URL.createObjectURL(new Blob([WORKER_SRC], { type: 'text/javascript' }));
      w = new Worker(url);
    } catch (e) {
      return false;
    }
    let oc;
    try { oc = canvas.transferControlToOffscreen(); } catch (e) { w.terminate(); return false; }
    w.onmessage = fromWorker;
    w.onerror = () => { G.ok = false; G.ready = false; show(false); };
    w.postMessage({ t: 'init', canvas: oc }, [oc]);
    G.worker = w;
    G.ok = G.ready = true;          // the worker says otherwise if its WebGL fails
    return true;
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();

  // Paused: the parked frame is drawn at the monitor's full size again (PLAY_W above).
  document.addEventListener('pause', (e) => { if (e.target === live()) settle(); }, true);

  window.grade = {
    get enabled() { return G.enabled; },
    set enabled(v) { G.enabled = !!v; tag(); settle(); },
    get ready() { return G.ready; },
    get shot() { return G.shot; },
    get lut() { return G.lut; },
    toggle,
    setShot,
    prefetch,
    invalidate,
    draw: settle,
    probe,
    get draws() { return G.draws; },
    get mode() { return G.worker ? 'worker' : (G.R ? 'main' : 'off'); },
    get playRes() { return G.res; },
    set playRes(v) { setRes(String(v)); },
  };
})();
