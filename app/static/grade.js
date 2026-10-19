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
 *   preview(block, {untilSave})   draw every shot through the LUT baked from this colour
 *                  block (held in memory, not saved: an AI review's look or Color Match,
 *                  verdict 10 punch 7); preview(null) goes back to the saved LUTs.
 *   previewing     true while a block is under preview
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
  function Renderer(canvas, makeStage, ropts) {
    const FULL = !!(ropts && ropts.full);     // never lean (a comparison switch: ?grade=full)
    const VS = `
      attribute vec2 a_pos;
      varying vec2 v_uv;
      void main() {
        v_uv = a_pos * 0.5 + 0.5;
        gl_Position = vec4(a_pos, 0.0, 1.0);
      }`;

    /* Lumetri beyond Basic (wave 11, LUMETRI): Curves and Color Wheels as one 256-entry
     * table per channel (u_ctab, built from THE formula by LUMETRI below), then the HSL
     * Secondary (u_hsl0..3), then the Vignette (wave 15, VIGNETTE: u_vig0..1, `vigU`
     * below) — compiled only into the programs a vignetted frame is drawn with (`vig`), so
     * no other frame pays for its pow()s (a software GL runs both sides of a branch).
     * roughcut/colour.py has the formula in words and the render's twin; `tex` is the GLSL
     * texture call of the version. */
    const CREATIVE = (tex, vig) => `
      uniform sampler2D u_ctab;
      uniform float u_ctabOn;
      uniform vec4 u_hsl0;      // hue°, width°, hue softness°, sat/light softness
      uniform vec4 u_hsl1;      // sat lo, sat hi, light lo, light hi (0..1)
      uniform vec4 u_hsl2;      // cos(shift), sin(shift), saturation/100
      uniform vec4 u_hsl3;      // the Cb, Cr offsets of temperature and tint
      uniform float u_hslOn;${vig ? `
      uniform vec4 u_vig0;      // sx, p, 1/(sx^p + 1)^(1/p): the shape (THE formula's d)
      uniform vec4 u_vig1;      // c − h, c + h, |amount|/5, [amount > 0]` : ''}
      float ctabAt(float v, int ch) {
        vec4 t = ${tex}(u_ctab, vec2(clamp(v, 0.0, 1.0) * (255.0 / 256.0) + 0.5 / 256.0, 0.5));
        return ch == 0 ? t.r : (ch == 1 ? t.g : t.b);
      }
      vec3 creative(vec3 y) {
        y = clamp(y, 0.0, 1.0);
        if (u_ctabOn > 0.5) y = vec3(ctabAt(y.r, 0), ctabAt(y.g, 1), ctabAt(y.b, 2));
        if (u_hslOn > 0.5) {
          float mx = max(y.r, max(y.g, y.b)), mn = min(y.r, min(y.g, y.b));
          float C = mx - mn, L = (mx + mn) * 0.5;
          float S = C > 1e-6 ? clamp(C / max(1e-6, 1.0 - abs(2.0 * L - 1.0)), 0.0, 1.0) : 0.0;
          float Cs = max(C, 1e-6);
          float H = 0.0;
          if (C > 1e-6) {
            if (mx == y.r) H = mod((y.g - y.b) / Cs, 6.0);
            else if (mx == y.g) H = (y.b - y.r) / Cs + 2.0;
            else H = (y.r - y.g) / Cs + 4.0;
            H *= 60.0;
          }
          float d = abs(mod(H - u_hsl0.x + 540.0, 360.0) - 180.0);
          float w = 1.0 - smoothstep(u_hsl0.y, u_hsl0.y + u_hsl0.z, d);
          w *= smoothstep(u_hsl1.x - u_hsl0.w, u_hsl1.x, S) * (1.0 - smoothstep(u_hsl1.y, u_hsl1.y + u_hsl0.w, S));
          w *= smoothstep(u_hsl1.z - u_hsl0.w, u_hsl1.z, L) * (1.0 - smoothstep(u_hsl1.w, u_hsl1.w + u_hsl0.w, L));
          float Y = dot(y, vec3(0.2126, 0.7152, 0.0722));
          float cb = (y.b - Y) / 1.8556, cr = (y.r - Y) / 1.5748;
          float cb2 = (cb * u_hsl2.x - cr * u_hsl2.y) * u_hsl2.z + u_hsl3.x;
          float cr2 = (cb * u_hsl2.y + cr * u_hsl2.x) * u_hsl2.z + u_hsl3.y;
          float r2 = Y + 1.5748 * cr2, b2 = Y + 1.8556 * cb2;
          vec3 o = vec3(r2, (Y - 0.2126 * r2 - 0.0722 * b2) / 0.7152, b2);
          y = clamp(mix(y, o, w), 0.0, 1.0);
        }${vig ? `
        // Lumetri › Vignette: d from the centre (0) to a corner (1), its falloff, the move
        vec2 q = abs(v_uv * 2.0 - 1.0);
        float d = pow(pow(u_vig0.x * q.x, u_vig0.y) + pow(q.y, u_vig0.y), 1.0 / u_vig0.y) * u_vig0.z;
        float s = u_vig1.z * smoothstep(u_vig1.x, u_vig1.y, d);
        y = y * (1.0 - s) + u_vig1.w * s;` : ''}
        return y;
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
    // Two sources (wave 11 CANVAS): `rgb` reads u_video (a frame scaled into RGBA on the CPU,
    // uploaded flipped); `yuv` reads the decoder's own planes (I420: Y, U, V; NV12: Y, UV),
    // uploaded as they are, and converts them here with the frame's matrix and range
    // (u_yuv: column-major, u_yoff: the black and the chroma zero) — no CPU colour
    // conversion, no CPU scale, no CPU flip (the rows are read top-down: 1 - y).
    const YUV_SRC = `
      uniform sampler2D u_py;
      uniform sampler2D u_pu;
      uniform sampler2D u_pv;
      uniform float u_nv12;
      uniform mat3 u_yuv;
      uniform vec3 u_yoff;
      vec3 yuvAt(vec2 p) {
        vec2 q = vec2(p.x, 1.0 - p.y);
        float y = texture(u_py, q).r;
        vec2 c = u_nv12 > 0.5 ? texture(u_pu, q).rg : vec2(texture(u_pu, q).r, texture(u_pv, q).r);
        return clamp(u_yuv * (vec3(y, c) - u_yoff), 0.0, 1.0);
      }`;
    /* `lean` (wave 13 CANVAS2): the same program without the steps that are the identity for
     * the frame in hand — the balance's shoulder (knee 1), Lumetri's tone (all four 0), the
     * Curves/Wheels table and the HSL Secondary (off) — chosen per draw (`leanFor`), so the
     * usual played frame (a baked LUT, nothing on top) is not shaded through branches it
     * skips: a software GL shades both sides of a branch for every pixel. The output is the
     * full program's, value for value (each removed step is skipped there too). */
    const FS2 = (yuv, lean, vig) => `#version 300 es
      precision mediump float;
      precision mediump sampler3D;
      in vec2 v_uv;
      uniform sampler2D u_video;${yuv ? YUV_SRC : ''}
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
      ${lean ? '' : CREATIVE('texture', vig)}
      void main() {
        vec3 c = ${yuv ? 'yuvAt(v_uv)' : 'texture(u_video, v_uv).rgb'} * u_pre;${lean ? '' : `
        if (u_knee < 1.0) {
          vec3 o = u_knee + (1.0 - u_knee) * (1.0 - exp(-(c - u_knee) / (1.0 - u_knee)));
          c = mix(c, o, step(u_knee, c)) + u_lift;
        }`}
        c = clamp(c, 0.0, 1.0);
        vec3 p = c * ((u_n - 1.0) / u_n) + 0.5 / u_n;
        vec3 y = (texture(u_lut, p).rgb - 0.5) * u_con + 0.5;${lean ? '' : `
        if (u_toneOn > 0.5) {
          vec3 x = clamp(y, 0.0, 1.0), ix = 1.0 - x;
          y = x + 0.25 * u_tone.x * ix * ix * ix + 1.5 * u_tone.y * x * ix * ix
                + 1.5 * u_tone.z * x * x * ix + 0.25 * u_tone.w * x * x * x;
        }`}
        y = mix(vec3(dot(y, vec3(0.2126, 0.7152, 0.0722))), y, u_sat);
        outColor = vec4(${lean ? 'clamp(y, 0.0, 1.0)' : 'creative(y)'}, 1.0);
      }`;

    // WebGL1: the table as an n × n² image. Bilinear inside a blue slice (red across,
    // green down), the blue interpolation by hand between the two neighbouring slices.
    const FS1 = (lean, vig) => `
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
      ${lean ? '' : CREATIVE('texture2D', vig)}
      void main() {
        vec3 c = texture2D(u_video, v_uv).rgb * u_pre;${lean ? '' : `
        if (u_knee < 1.0) {
          vec3 o = u_knee + (1.0 - u_knee) * (1.0 - exp(-(c - u_knee) / (1.0 - u_knee)));
          c = mix(c, o, step(u_knee, c)) + u_lift;
        }`}
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
        vec3 y = (mix(s0, s1, t) - 0.5) * u_con + 0.5;${lean ? '' : `
        if (u_toneOn > 0.5) {
          vec3 x = clamp(y, 0.0, 1.0), ix = 1.0 - x;
          y = x + 0.25 * u_tone.x * ix * ix * ix + 1.5 * u_tone.y * x * ix * ix
                + 1.5 * u_tone.z * x * x * ix + 0.25 * u_tone.w * x * x * x;
        }`}
        y = mix(vec3(dot(y, vec3(0.2126, 0.7152, 0.0722))), y, u_sat);
        gl_FragColor = vec4(${lean ? 'clamp(y, 0.0, 1.0)' : 'creative(y)'}, 1.0);
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
    // One program per source kind (rgb always; yuv on WebGL2), the quad at attribute 0.
    function program(fs) {
      const p = gl.createProgram();
      gl.attachShader(p, compile(gl, gl.VERTEX_SHADER, gl2 ? VS2 : VS));
      gl.attachShader(p, compile(gl, gl.FRAGMENT_SHADER, fs));
      gl.bindAttribLocation(p, 0, 'a_pos');
      gl.linkProgram(p);
      if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
        throw new Error(`grade: link failed — ${gl.getProgramInfoLog(p)}`);
      }
      gl.useProgram(p);
      const U = { p, n: null };
      ['u_n', 'u_pre', 'u_con', 'u_sat', 'u_knee', 'u_lift', 'u_tone', 'u_toneOn',
       'u_ctabOn', 'u_hsl0', 'u_hsl1', 'u_hsl2', 'u_hsl3', 'u_hslOn', 'u_vig0', 'u_vig1',
       'u_yuv', 'u_yoff', 'u_nv12'].forEach((k) => { U[k] = gl.getUniformLocation(p, k); });
      // unit 2 is Lumetri's curves table (ctabTex); the planes are on 3-5
      [['u_video', 0], ['u_lut', 1], ['u_ctab', 2], ['u_py', 3], ['u_pu', 4], ['u_pv', 5]].forEach(([k, i]) => {
        const loc = gl.getUniformLocation(p, k);
        if (loc) gl.uniform1i(loc, i);
      });
      return U;
    }
    const PR = program(gl2 ? FS2(false, false) : FS1(false));
    // the lean twins (FS2's note); a failed lean compile draws with the full one
    let PRL = PR;
    try { PRL = FULL ? PR : program(gl2 ? FS2(false, true) : FS1(true)); } catch (e) { PRL = PR; }
    let PY = null, PYL = null;
    if (gl2) {
      try { PY = program(FS2(true, false)); } catch (e) { PY = null; }   // the rgb path still draws
      try { PYL = PY && !FULL ? program(FS2(true, true)) : PY; } catch (e) { PYL = PY; }
    }
    /* Whether a draw with the live-preview `a` can use the lean program: no shoulder, no
     * tone, no Curves/Wheels table, no HSL Secondary (each the identity in the full one). */
    function leanFor(a) {
      if (!a) return true;
      if ((a.knee ?? 1) < 1) return false;
      if (a.tone && a.tone.some((x) => x !== 0)) return false;
      if (a.ctab && a.ctab.key) return false;
      if (a.hsl && a.hsl.length === 16) return false;
      if (a.vig && a.vig[0]) return false;
      return true;
    }
    /* The program for a draw: 'lean', 'full', or 'vig' — the full one with Lumetri's
     * Vignette (wave 15), compiled the first time a vignetted frame is drawn (a failed
     * compile draws with the full one: the picture without its vignette). */
    const kindFor = (a) => (a && a.vig && a.vig[0] ? 'vig' : leanFor(a) ? 'lean' : 'full');
    const VIGP = {};
    function progFor(yuv, kind) {
      if (kind === 'vig') {
        const k = yuv ? 'y' : 'r';
        if (VIGP[k] === undefined) {
          try { VIGP[k] = yuv ? (PY && program(FS2(true, false, true))) : program(gl2 ? FS2(false, false, true) : FS1(false, true)); }
          catch (e) { VIGP[k] = null; }
        }
        if (VIGP[k]) return VIGP[k];
        kind = 'full';
      }
      if (yuv) return kind === 'lean' ? PYL : PY;
      return kind === 'lean' ? PRL : PR;
    }
    let U = PR;                   // the program in use, and its uniforms
    gl.useProgram(PR.p);
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    function use(P) {
      if (U !== P) { U = P; gl.useProgram(P.p); }
    }

    /* Lumetri's Curves + Wheels table (wave 11): a 256×1 RGBA texture per table key on
     * TEXTURE2 (a few kept), the identity bound when none is in use. */
    const ctabs = new Map();
    function ctabTex(key, data) {
      gl.activeTexture(gl.TEXTURE2);
      let tex = ctabs.get(key);
      if (!tex) {
        if (!data) return false;
        tex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, tex);
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
        gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE,
          data instanceof Uint8Array ? data : new Uint8Array(data));
        ctabs.set(key, tex);
        for (const [k, t] of ctabs) {
          if (ctabs.size <= 6) break;
          if (k === key || k === 'identity') continue;
          ctabs.delete(k);
          gl.deleteTexture(t);
        }
      } else gl.bindTexture(gl.TEXTURE_2D, tex);
      return true;
    }
    {
      const id = new Uint8Array(1024);
      for (let i = 0; i < 256; i++) { id[i * 4] = id[i * 4 + 1] = id[i * 4 + 2] = i; id[i * 4 + 3] = 255; }
      ctabTex('identity', id);
    }
    /* Lumetri › Vignette's uniforms from `v` = [amount, midpoint, roundness, feather] on a
     * w × h picture: THE formula's (roughcut/colour.py, "Lumetri › Vignette") shape and
     * falloff, the shader's `d` and `s` above. */
    function vigU(v, w, h) {
      const R = Math.max(-1, Math.min(1, v[2] / 100));
      const sx = 1 + Math.max(R, 0) * (w / h - 1), p = 2 + 8 * Math.max(-R, 0);
      const c = 0.25 + 0.75 * v[1] / 100, hw = Math.max(0.5 * c * v[3] / 100, 0.0005);
      return [sx, p, 1 / Math.pow(Math.pow(sx, p) + 1, 1 / p), 0,
              c - hw, c + hw, Math.abs(v[0]) / 5, v[0] > 0 ? 1 : 0];
    }
    /* The creative uniforms of `a` (its `ctab` {key, data}, `hsl` 16 numbers and `vig`)
     * for a cw × ch draw. */
    function creative(a, cw, ch) {
      const t = a && a.ctab;
      const on = !!(t && ctabTex(t.key, t.data));
      if (!on) ctabTex('identity', null);
      gl.uniform1f(U.u_ctabOn, on ? 1 : 0);
      const h = a && a.hsl;
      if (h && h.length === 16) {
        gl.uniform4f(U.u_hsl0, h[0], h[1], h[2], h[3]);
        gl.uniform4f(U.u_hsl1, h[4], h[5], h[6], h[7]);
        gl.uniform4f(U.u_hsl2, h[8], h[9], h[10], h[11]);
        gl.uniform4f(U.u_hsl3, h[12], h[13], h[14], h[15]);
        gl.uniform1f(U.u_hslOn, 1);
      } else gl.uniform1f(U.u_hslOn, 0);
      const vg = a && a.vig;
      if (U.u_vig0 && vg && vg[0] && cw > 0 && ch > 0) {      // a 'vig' program's
        const q = vigU(vg, cw, ch);
        gl.uniform4f(U.u_vig0, q[0], q[1], q[2], q[3]);
        gl.uniform4f(U.u_vig1, q[4], q[5], q[6], q[7]);
      }
    }

    function texture2d(unit) {
      const t = gl.createTexture();
      gl.activeTexture(gl.TEXTURE0 + unit);
      gl.bindTexture(gl.TEXTURE_2D, t);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      return t;
    }
    // the planes, on units 3-5 (unit 0 is the rgb frame, 1 the LUT, 2 Lumetri's curves
    // table); each remembers its size, so a frame of the same size is a texSubImage2D
    const planeTex = PY ? [texture2d(3), texture2d(4), texture2d(5)] : [];
    planeTex.forEach((t) => { t._w = 0; t._h = 0; t._f = 0; });
    const texVideo = texture2d(0);
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
    function upload(src, sw, sh, w, h, key, kind) {
      if (src && src.yuv) { frameKey = null; return planes(src, kind); }
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

    /* A decoded frame's own planes (`yuvPlanes` below: I420 or NV12, each plane `{buf, offset,
     * stride, w, h}`) into the plane textures, each at its own size: the shader converts (and
     * scales, when the picture is not drawn at the planes' size). Measured on Killington's
     * 1280x720 proxy in headless Chromium at load 13-20 (wave 11 CANVAS), the worker's own
     * time per frame: 0.4-2.7 ms, where the CPU scale into the 2D stage and its RGBA upload
     * took 10-12 ms (21 ms on the board's busier page). */
    function planes(f, kind) {
      if (!PY) return false;
      const nv12 = f.format === 'NV12';
      const filt = f.nearest ? gl.NEAREST : gl.LINEAR;
      try {
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
        gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
        f.planes.forEach((P, i) => {
          const t = planeTex[i];
          const two = nv12 && i === 1;              // NV12's UV: two bytes a texel
          const ifmt = two ? gl.RG8 : gl.R8, fmt = two ? gl.RG : gl.RED;
          gl.activeTexture(gl.TEXTURE3 + i);
          gl.bindTexture(gl.TEXTURE_2D, t);
          if (t._filt !== filt) {
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filt);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filt);
            t._filt = filt;
          }
          gl.pixelStorei(gl.UNPACK_ROW_LENGTH, two ? P.stride / 2 : P.stride);
          if (t._w === P.w && t._h === P.h && t._f === ifmt) {
            gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, P.w, P.h, fmt, gl.UNSIGNED_BYTE, P.buf, P.offset);
          } else {
            gl.texImage2D(gl.TEXTURE_2D, 0, ifmt, P.w, P.h, 0, fmt, gl.UNSIGNED_BYTE, P.buf, P.offset);
            t._w = P.w; t._h = P.h; t._f = ifmt;
          }
        });
        gl.pixelStorei(gl.UNPACK_ROW_LENGTH, 0);
      } catch (e) {
        gl.pixelStorei(gl.UNPACK_ROW_LENGTH, 0);
        return false;
      }
      use(progFor(true, kind));
      gl.uniformMatrix3fv(U.u_yuv, false, f.mat);
      gl.uniform3f(U.u_yoff, f.off[0], f.off[1], f.off[2]);
      gl.uniform1f(U.u_nv12, nv12 ? 1 : 0);
      return true;
    }

    /* Draw `src` (a <video> or a VideoFrame, sw×sh) at w×h through the LUT `key` (made
     * from n/data when this renderer has not seen it) with the live preview's `adj`. */
    function render(src, sw, sh, w, h, key, n, data, adj, parkedKey, mix) {
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
        frameKey = null;
      }
      gl.viewport(0, 0, w, h);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, texVideo);
      const kind = kindFor(adj);
      if (!upload(src, sw, sh, w, h, parkedKey, kind)) return false;   // no frame yet; the next one will be
      if (!src.yuv) use(progFor(false, kind));
      if (!lut(key, n, data)) return false;
      current = key;
      if (U.n !== n) { U.n = n; gl.uniform1f(U.u_n, n); }
      const a = adj || NO_ADJUST;
      gl.uniform3f(U.u_pre, a.pre[0], a.pre[1], a.pre[2]);
      gl.uniform1f(U.u_con, a.con);
      gl.uniform1f(U.u_sat, a.sat);
      gl.uniform1f(U.u_knee, a.knee ?? 1);
      gl.uniform1f(U.u_lift, a.lift ?? 0);
      const tone = a.tone || NO_TONE;
      gl.uniform4f(U.u_tone, tone[0], tone[1], tone[2], tone[3]);
      gl.uniform1f(U.u_toneOn, tone.some((x) => x !== 0) ? 1 : 0);
      creative(a, w, h);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      if (mix && mix.w > 0) drawMix(mix, w, h);
      return true;
    }

    /* A transition's other shot (app.js programMix, verdict 6 punch 6): its frame through
     * ITS OWN LUT, laid over the picture just drawn at constant alpha w — a Cross
     * Dissolve of the two graded pictures, as the render mixes them. */
    function drawMix(mix, w, h) {
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, texVideo);
      const kind = kindFor(mix.adj);
      if (!upload(mix.src, mix.sw, mix.sh, w, h, null, kind)) return;
      frameKey = null;                       // the texture holds the other frame now
      if (!mix.src.yuv) use(progFor(false, kind));
      if (!lut(mix.key, mix.n, mix.data)) return;
      current = mix.key;
      if (U.n !== mix.n) { U.n = mix.n; gl.uniform1f(U.u_n, mix.n); }
      const a = mix.adj || NO_ADJUST;          // its Lumetri contrast, tone, saturation
      gl.uniform3f(U.u_pre, 1, 1, 1);
      gl.uniform1f(U.u_con, a.con);
      gl.uniform1f(U.u_sat, a.sat);
      gl.uniform1f(U.u_knee, 1);
      gl.uniform1f(U.u_lift, 0);
      const tone = a.tone || NO_TONE;
      gl.uniform4f(U.u_tone, tone[0], tone[1], tone[2], tone[3]);
      gl.uniform1f(U.u_toneOn, tone.some((x) => x !== 0) ? 1 : 0);
      creative(a, w, h);                       // its Curves, Wheels, HSL Secondary, Vignette
      gl.enable(gl.BLEND);
      gl.blendColor(0, 0, 0, Math.max(0, Math.min(1, mix.w)));
      gl.blendFunc(gl.CONSTANT_ALPHA, gl.ONE_MINUS_CONSTANT_ALPHA);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      gl.disable(gl.BLEND);
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

    /* One draw of each kind a played frame takes (the planes, plain and blended over, at
     * both filters) on a 2x2 picture, so a software GL compiles its routines for them now
     * and not on the first frame of the first play (measured: a 156 ms hole at the start
     * of the first graded play at load 30). Leaves the canvas as it was. */
    function warm() {
      if (!PY) return;
      const cw0 = canvas.width, ch0 = canvas.height;
      const b = new Uint8Array(16).fill(128);
      const f = { yuv: true, format: 'I420', nearest: false, mat: [1, 1, 1, 0, -0.19, 1.86, 1.57, -0.47, 0],
                  off: [0, 0.5, 0.5], planes: [{ buf: b, offset: 0, stride: 2, w: 2, h: 2 },
                                               { buf: b, offset: 4, stride: 1, w: 1, h: 1 },
                                               { buf: b, offset: 8, stride: 1, w: 1, h: 1 }] };
      const id = new Uint8Array([0, 0, 0, 255, 0, 0, 0, 255, 0, 255, 255, 0,
                                 0, 0, 255, 255, 0, 255, 0, 255, 255, 255, 255, 255]);
      // the lean program (the usual played frame) and the full one (a tone on: Lumetri live)
      const toned = { pre: [1, 1, 1], con: 1, sat: 1, knee: 1, lift: 0, tone: [0.01, 0, 0, 0] };
      try {
        for (const a of FULL ? [null] : [null, toned]) {
          for (const nearest of [false, true]) {
            f.nearest = nearest;
            render(f, 2, 2, 2, 2, '__warm', 2, id, a, null, { src: f, w: 0.5, key: '__warm', n: 2, data: id, adj: a });
          }
        }
        gl.finish();
      } catch (e) { /* the first frame compiles them instead */ }
      if (canvas.width !== cw0 || canvas.height !== ch0) { canvas.width = cw0; canvas.height = ch0; }
      frameKey = null;
    }

    /* What was just drawn, RGBA bottom row first (same task), for the scopes. */
    function pixels() {
      const w = canvas.width, h = canvas.height;
      if (!w || !h) return null;
      const px = new Uint8Array(w * h * 4);
      gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px);
      return { w, h, data: px };
    }

    return { gl2, yuv: !!PY, lut, forget, render, mean, pixels, warm };
  }

  /* A playing frame's planes, for the renderer's yuv path (wave 11 CANVAS): copied out of
   * the VideoFrame as the decoder made them (VideoFrame.copyTo, its visible rect) into a
   * buffer kept per slot (0 the live shot, 1 a dissolve's other one), with the matrix and the
   * offsets that turn them into the RGB the browser's own conversion makes (BT.709 full range
   * on Killington's proxies: within 2 levels of drawImage's, mean 0.2). Null — the rgb path —
   * for any other layout (a GPU frame, 10-bit, alpha) or a matrix it does not know. Self-
   * contained: its source is the worker's too. */
  async function yuvPlanes(frame, slot, bufs, ow, oh) {
    const fmt = frame.format;
    if (fmt !== 'I420' && fmt !== 'NV12') return null;
    const vr = frame.visibleRect;
    if (!vr || !vr.width || !vr.height) return null;
    const cs = frame.colorSpace || {};
    const KS = { bt709: [0.2126, 0.0722], smpte170m: [0.299, 0.114], bt470bg: [0.299, 0.114],
                 'bt2020-ncl': [0.2627, 0.0593] };
    const K = KS[cs.matrix || (vr.height >= 720 ? 'bt709' : 'smpte170m')];
    if (!K) return null;
    const size = frame.allocationSize();
    let buf = bufs[slot];
    if (!buf || buf.length < size) buf = bufs[slot] = new Uint8Array(size);
    const layout = await frame.copyTo(buf);
    const W = vr.width, H = vr.height, cw = (W + 1) >> 1, ch = (H + 1) >> 1;
    const pl = [{ buf, offset: layout[0].offset, stride: layout[0].stride, w: W, h: H }];
    if (fmt === 'NV12') pl.push({ buf, offset: layout[1].offset, stride: layout[1].stride, w: cw, h: ch });
    else for (const i of [1, 2]) pl.push({ buf, offset: layout[i].offset, stride: layout[i].stride, w: cw, h: ch });
    // Drawn at half the frame's size or less (Auto while playing: 640 of a 1280 proxy): the
    // luma is boxed to the chroma's size here (2x2 means, ~1 ms of the worker at 1280x720),
    // so all the planes go up at the size the shader reads them — a third of the bytes, and
    // at exactly half size one texel a pixel (nearest), where the GPU's own bilinear
    // minification of the whole luma cost twice the shading (software GL, measured).
    let nearest = false;
    if (ow * 2 <= W + 1 && oh * 2 <= H + 1 && !(W & 1) && !(H & 1)) {
      const k = slot + 2;
      if (!bufs[k] || bufs[k].length < cw * ch) bufs[k] = new Uint8Array(cw * ch);
      const D = bufs[k], st = layout[0].stride, o0 = layout[0].offset;
      for (let y = 0; y < ch; y++) {
        let a = o0 + 2 * y * st, b = a + st;
        const o = y * cw;
        for (let x = 0; x < cw; x++, a += 2, b += 2) D[o + x] = (buf[a] + buf[a + 1] + buf[b] + buf[b + 1] + 2) >> 2;
      }
      pl[0] = { buf: D, offset: 0, stride: cw, w: cw, h: ch };
      nearest = ow === cw && oh === ch;
    }
    const [kr, kb] = K, kg = 1 - kr - kb;
    const full = !!cs.fullRange;
    const ys = full ? 1 : 255 / 219, c = full ? 1 : 255 / 224;
    // column-major: the Y, U and V columns of R, G, B
    const mat = [ys, ys, ys,
                 0, -2 * (1 - kb) * kb / kg * c, 2 * (1 - kb) * c,
                 2 * (1 - kr) * c, -2 * (1 - kr) * kr / kg * c, 0];
    return { yuv: true, format: fmt, planes: pl, nearest, mat,
             off: [full ? 0 : 16 / 255, 128 / 255, 128 / 255] };
  }

  /* The worker: the renderer on the monitor's OffscreenCanvas. One message kind each way:
   * `frame` in (drawn, then closed), `drawn` back (when, and how long the draw took: the
   * page's draw log); `lut` in makes a texture ahead of a cut; `forget` drops them all (a
   * colour save). A playing frame (`yuv`) goes up as its planes, any other as before.
   * Committed at the end of its task, off the page's main thread.
   *
   * The worker keeps its own queue (wave 13 CANVAS2): every frame is posted the moment the
   * page has it; one is drawn at a time and the newest waits (a waiting one a newer frame
   * replaces is closed undrawn, its LUT made all the same). It used to be the page that held
   * the newest frame back until the worker's `drawn` reached it, so a frame that came while
   * the worker was busy waited for the page's main thread as well — a style and layout pass
   * of the board, at load 8 the hole's other half. */
  const WORKER_SRC = `'use strict';
const Renderer = ${Renderer.toString()};
const yuvPlanes = ${yuvPlanes.toString()};
let R = null;
const bufs = [null, null, null, null];
let busy = false, next = null;
function closeFrame(m) {
  try { m.frame.close(); if (m.mix) m.mix.src.close(); } catch (err) { /* closed */ }
}
async function drawFrame(m) {
  const t0 = performance.now();
  const got = m.got || performance.timeOrigin + t0;
  let ok = false, src = m.frame, mix = m.mix, yuv = false;
  const ts = m.frame.timestamp;
  try {
    if (R && R.yuv && m.yuv) {
      // copied out, the decoder's frame goes back to it at once
      const p = await yuvPlanes(m.frame, 0, bufs, m.w, m.h).catch(() => null);
      if (p) { m.frame.close(); src = p; yuv = true; }
      if (mix) {
        const q = await yuvPlanes(mix.src, 1, bufs, m.w, m.h).catch(() => null);
        if (q) { mix.src.close(); mix = { ...mix, src: q }; }
      }
    }
    if (R) ok = R.render(src, m.sw, m.sh, m.w, m.h, m.key, m.n, m.data, m.adj, null, mix);
  } catch (err) { /* a frame that will not draw: the next one will */ }
  finally { closeFrame(m); }
  postMessage({ t: 'drawn', ok: !!ok, ts, yuv, ms: performance.now() - t0, sent: m.sent, made: m.made, got,
                start: performance.timeOrigin + t0, at: performance.timeOrigin + performance.now(),
                skipped: m.skipped || 0 });
}
async function pump(m) {
  busy = true;
  try { await drawFrame(m); } finally { busy = false; }
  if (next) { const n = next; next = null; pump(n); }
}
onmessage = (e) => {
  const m = e.data;
  if (m.t === 'init') {
    try { R = Renderer(m.canvas, (w, h) => new OffscreenCanvas(w, h), { full: !!m.full }); }
    catch (err) { R = null; }
    if (R) R.warm();
    postMessage({ t: 'ready', ok: !!R, yuv: !!(R && R.yuv) });
    return;
  }
  if (m.t === 'frame') {
    m.got = performance.timeOrigin + performance.now();
    if (!busy) { pump(m); return; }
    if (next) {
      // replaced before it was drawn: closed, but its LUT (sent once per key) is kept
      if (R && next.data && next.key !== m.key) { try { R.lut(next.key, next.n, next.data); } catch (err) { /* lost */ } }
      if (next.data && !m.data && next.key === m.key) { m.data = next.data; m.n = next.n; }
      m.skipped = (next.skipped || 0) + 1;
      closeFrame(next);
    }
    next = m;
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
    S: null,                 // sample()'s (Lumetri Scopes), on a canvas of its own
    worker: null,            // the worker holding #gradeCanvas (worker mode)
    inflight: false,         // a frame is with the worker (its queue: WORKER_SRC)
    sentAt: 0,               // when the last one went
    sentKey: null,           // the LUT key the worker was last sent the data of
    videos: [],
    shot: null,              // the segment id the monitor is on
    lut: null,               // the current shot's {n, identity, data} or null
    cache: new Map(),        // id → the LUT record, or the promise fetching it
    pcol: null,              // the colour block under preview (JSON), or null (preview below)
    pcache: new Map(),       // id → the preview's LUT record, or the promise fetching it
    pepoch: 0,               // bumps per invalidate: a preview bake from before it is not cached
    pkeep: null,             // the last block previewed and its LUTs, while ◐ shows Before
    puntil: false,           // the preview stands in until the next save lands (Accept)
    keys: 0,                 // LUT records are keyed by a serial (renderer textures)
    gen: 0,                  // bumps per setShot / invalidate: a late fetch is ignored
    frameGen: 0,             // bumps per re-arm of the frame callback
    epoch: 0,                // bumps per invalidate: a fetch from before it is not cached
    drawReq: 0,              // the pending coalesced draw (drawSoon), a rAF id
    draws: 0,                // frames drawn (tests read it): in worker mode, when it says so
    drawLog: [],             // worker mode: the last DRAW_LOG draws {t, ts, ms, yuv} (page clock)
    yuv: false,              // the worker draws playing frames from their planes
    box: { w: 0, h: 0 },     // the monitor's box (ResizeObserver), CSS px
    mix: null,               // a transition's other shot: {video, id, w, rec} (setMix)
    mixGen: 0,               // bumps per new mix video: its frame callback (armMix) stops
    drawnAt: 0,              // when the last draw was asked of the renderer (armMix)
    drawNo: 0,               // draws asked of the renderer so far (setShot's cut in a mix)
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
  /* A dissolve drawn while it plays (setMix) is two frames per frame: two uploads, two
   * passes. On Auto the pair is drawn at most MIX_W wide, so the two cost what one frame
   * at PLAY_W does (half the pixels each) and the window stays inside a single shot's
   * budget (verdict 7 punch 4: at load 16-25 the canvas held 107-258 ms through a graded
   * dissolve at 640). Paused, and on any other Playback Resolution, as before. */
  const MIX_W = Math.round(PLAY_W / Math.SQRT2);
  function drawSize(vw, vh, playing, mixing) {
    const dpr = window.devicePixelRatio || 1;
    const bw = G.box.w * dpr, bh = G.box.h * dpr;
    if (!bw || !bh) return [vw, vh];
    let s = Math.min(1, bw / vw, bh / vh);
    // (the worker's planes path uploads each frame at the size it is read, a third of the
    // RGBA bytes; wave 13 CANVAS2: a dissolve's two frames are still two copies, two uploads
    // and two passes, and on Killington at load 9-16 the worker fell a frame behind through
    // the window (a frame waited 30-35 ms for it), so on Auto the pair is MIX_W wide too)
    const mixW = mixing ? Math.min(MIX_W, G.yuv ? G.autoW : PLAY_W) : (G.yuv ? G.autoW : PLAY_W);
    if (playing) s = RES[G.res] == null ? Math.min(s, mixW / vw) : s * RES[G.res];
    if (s > 0.9) return [vw, vh];   // near full size: the scale costs more than it saves
    // within 3 % of half the frame: exactly half, where the worker's planes are one texel a
    // pixel (yuvPlanes; a 674-px monitor drew 638 of a 1280 proxy and sampled it bilinear)
    if (playing && Math.abs(s - 0.5) <= 0.015 && !(vw & 3) && !(vh & 3)) return [vw / 2, vh / 2];
    return [Math.max(2, Math.round(vw * s / 2) * 2), Math.max(2, Math.round(vh * s / 2) * 2)];
  }

  /* ------------------------------------------------------------ drawing */
  /* What a draw of the live video needs now, or null when there is nothing to draw: no
   * renderer, grade off, identity LUT, no live video, or a video with no frame yet. */
  function job(raw) {
    const v = live();
    const adj = raw ? null : adjustNow();
    const mixGraded = mixDrawn();
    if (!raw && (!G.ok || !G.enabled || !G.lut || (G.lut.identity && !adj && !mixGraded))) return null;
    if (!G.ok || !v || v.readyState < 2) return null;
    const vw = v.videoWidth, vh = v.videoHeight;
    if (!vw || !vh) return null;
    const m = G.mix;
    const mixing = !!(m && m.rec && m.w > 0 && m.video.readyState >= 2 && m.video.videoWidth);
    const playing = (!v.paused && !v.ended) || playerPlaying();
    // (near a dissolve the canvas keeps the mix's size: mixNear)
    const [w, h] = drawSize(vw, vh, playing, mixing || (playing && G.mixZone));
    const rec = raw || G.lut.identity ? IDENTITY_TABLE : G.lut;
    const parked = v.paused && !v.seeking
      ? `${v.id}|${v.currentSrc}|${v.currentTime}|${w}x${h}` : null;
    const mix = mixing
      ? { src: m.video, sw: m.video.videoWidth, sh: m.video.videoHeight,
          key: m.rec.identity ? IDENTITY_TABLE.key : m.rec.key, n: m.rec.identity ? IDENTITY_TABLE.n : m.rec.n,
          data: m.rec.identity ? IDENTITY_TABLE.data : m.rec.data, w: m.w, adj: mixAdjust() }
      : null;
    return { v, vw, vh, w, h, rec, adj, parked: mix ? null : parked, mix, playing };
  }

  function drawOn(R, j) {
    return R.render(j.v, j.vw, j.vh, j.w, j.h, j.rec.key, j.rec.n, j.rec.data, j.adj, j.parked, j.mix);
  }

  /* Draw the live video's current frame through the LUT. */
  function draw(raw) {
    const j = job(raw);
    if (!j) return;
    G.drawnAt = performance.now();
    G.drawNo++;
    if (G.worker) { toWorker(j); return; }
    if (drawOn(G.R, j)) { G.draws++; drewOn(); }
  }

  /* The frame to the worker: a VideoFrame of the live video (a reference to the decoded
   * frame), the LUT's data the first time after a change of LUT, the uniforms. Posted at
   * once; the worker draws one at a time and keeps the newest waiting (WORKER_SRC). */
  function toWorker(j) {
    let frame;
    try { frame = new VideoFrame(j.v, { timestamp: Math.round((j.v.currentTime || 0) * 1e6) }); }
    catch (e) { return; }                       // no frame to take yet: the next one
    let mix = null;
    if (j.mix) {
      try {
        const f = new VideoFrame(j.mix.src, { timestamp: Math.round((j.mix.src.currentTime || 0) * 1e6) });
        mix = { ...j.mix, src: f, sw: f.displayWidth, sh: f.displayHeight };
      } catch (e) { mix = null; }
    }
    post({ t: 'frame', frame, sw: frame.displayWidth, sh: frame.displayHeight,
           w: j.w, h: j.h, rec: j.rec, adj: j.adj, mix, playing: j.playing, made: performance.now() });
  }

  function post(m) {
    const rec = m.rec;
    const msg = { t: 'frame', frame: m.frame, sw: m.sw, sh: m.sh, w: m.w, h: m.h,
                  key: rec.key, n: rec.n, data: rec.key !== G.sentKey ? rec.data : null,
                  adj: m.adj ? JSON.parse(JSON.stringify(m.adj)) : null, mix: m.mix || null,
                  yuv: !!m.playing, sent: performance.now(), made: m.made };
    G.sentKey = rec.key;
    G.inflight = true;
    G.sentAt = performance.now();
    try { G.worker.postMessage(msg, m.mix ? [m.frame, m.mix.src] : [m.frame]); }
    catch (e) {
      G.inflight = false;
      G.sentKey = null;
      try { m.frame.close(); if (m.mix) m.mix.src.close(); } catch (e2) { /* gone */ }
    }
  }

  /* Auto, learned (wave 11 CANVAS): on the planes path the worker's frame is gated by the
   * GL's own work (the software GL of a headless box: shading and the commit's read-back,
   * 20-30 ms a frame at load 15 on Killington, where the worker's JS is 0.5-5). When a
   * played frame's draw lands more than AUTO_SLOW_MS after it was posted (a running mean),
   * Auto draws the picture one step smaller (640 → 544 → 448 px wide, the shading scales
   * with it); when it lands well inside the frame for a while, one step back up. Only Auto
   * learns (Full, 1/2 and 1/4 are what they say), and a paused frame is always full size. */
  const AUTO_STEPS = [PLAY_W, 544, 448];
  const AUTO_SLOW_MS = 28, AUTO_FAST_MS = 14;
  G.autoW = PLAY_W;
  const auto = { ema: 0, n: 0 };
  function autoLearn(m) {
    if (!m.yuv || G.res !== 'auto' || G.mix) return;
    // posted to drawn, less any time it waited in the worker's queue behind the frame before
    // (the GL's own pace, as when the page held the newest frame back until `drawn`)
    const lag = (m.at - performance.timeOrigin) - (m.sent || 0) - Math.max(0, (m.start || m.got || 0) - (m.got || 0));
    if (!(lag >= 0 && lag < 1000)) return;
    auto.ema = auto.n ? auto.ema * 0.85 + lag * 0.15 : lag;
    auto.n++;
    const i = AUTO_STEPS.indexOf(G.autoW);
    if (auto.n >= 10 && auto.ema > AUTO_SLOW_MS && i < AUTO_STEPS.length - 1) {
      G.autoW = AUTO_STEPS[i + 1]; auto.n = 0;
    } else if (auto.n >= 90 && auto.ema < AUTO_FAST_MS && i > 0) {
      G.autoW = AUTO_STEPS[i - 1]; auto.n = 0;
    }
  }

  const DRAW_LOG = 600;
  function fromWorker(e) {
    const m = e.data || {};
    if (m.t === 'drawn') {
      G.inflight = false;
      if (m.ok && G.showOnDraw) {
        G.showOnDraw = false;
        if (G.ok && G.enabled && live() && mixDrawn()) show(true);
      }
      if (m.ok) {
        G.draws++;
        drewOn();
        const r1 = (x) => Math.round(x * 10) / 10;
        G.drawLog.push({ t: r1(m.at - performance.timeOrigin), ts: m.ts, ms: r1(m.ms), yuv: !!m.yuv,
                         made: r1(m.made || 0), sent: r1(m.sent || 0), got: r1(m.got - performance.timeOrigin),
                         start: r1((m.start || m.got) - performance.timeOrigin), skipped: m.skipped || 0 });
        if (G.drawLog.length > DRAW_LOG) G.drawLog.splice(0, G.drawLog.length - DRAW_LOG);
        autoLearn(m);
      }
    } else if (m.t === 'ready' && m.ok) {
      G.yuv = !!m.yuv;
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

  /* ------------------------------------------------------------ Lumetri beyond Basic
   * Curves, Color Wheels and HSL Secondary (wave 11, lane LUMETRI): THE formula, as
   * roughcut/colour.py states it ("Lumetri beyond Basic") and the render draws it,
   * after Basic's saturation and a clamp:
   *   1. curves  y_c = curve_c(curve_master(x_c)), each a PCHIP through its points
   *   2. wheels  gain (highlights), lift (shadows), gamma (midtones) per channel
   *   3. HSL Secondary: a hue / saturation / lightness key and its correction
   * 1 and 2 are a function of each channel alone: `table` builds them as 256 codes per
   * channel (the shader's u_ctab; the render's `lutrgb` is the same table). 3 is the
   * shader's `creative()`; `hslUniforms` turns the controls into its uniforms. */
  const LUMETRI = (() => {
    const KR = 0.2126, KB = 0.0722;
    const WHEEL_K = { shadows: 0.25, midtones: 0.6, highlights: 0.5 };
    const HSL = { hue: 0, width: 30, sat_lo: 10, sat_hi: 100, light_lo: 0, light_hi: 100,
                  soft: 20, shift: 0, saturation: 100, temperature: 0, tint: 0 };
    const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
    /* a curve's evaluator: PCHIP, held flat past its ends, clamped */
    function curve(points) {
      const n = points.length;
      const px = points.map((p) => p[0]), py = points.map((p) => p[1]);
      const h = [], d = [];
      for (let k = 0; k < n - 1; k++) { h.push(px[k + 1] - px[k]); d.push((py[k + 1] - py[k]) / h[k]); }
      const m = new Array(n).fill(0);
      m[0] = d[0]; m[n - 1] = d[n - 2];
      for (let k = 1; k < n - 1; k++) {
        if (d[k - 1] * d[k] <= 0) m[k] = 0;
        else {
          const w1 = 2 * h[k] + h[k - 1], w2 = h[k] + 2 * h[k - 1];
          m[k] = (w1 + w2) / (w1 / d[k - 1] + w2 / d[k]);
        }
      }
      return (x) => {
        const xc = Math.min(px[n - 1], Math.max(px[0], x));
        let k = 0;
        while (k < n - 2 && xc >= px[k + 1]) k++;
        const t = (xc - px[k]) / h[k], t2 = t * t, t3 = t2 * t;
        return clamp01((2 * t3 - 3 * t2 + 1) * py[k] + (t3 - 2 * t2 + t) * h[k] * m[k]
          + (-2 * t3 + 3 * t2) * py[k + 1] + (t3 - t2) * h[k] * m[k + 1]);
      };
    }
    /* the puck as an RGB offset of zero luma (Cb = cb/2, Cr = cr/2, BT.709) */
    function wheelRgb(cb, cr) {
      const r = 2 * (1 - KR) * 0.5 * cr, b = 2 * (1 - KB) * 0.5 * cb;
      return [r, -(KR * r + KB * b) / (1 - KR - KB), b];
    }
    function wheelParams(wheels) {
      const out = {};
      Object.keys(WHEEL_K).forEach((nm) => {
        const [cb, cr, li] = (wheels && wheels[nm]) || [0, 0, 0];
        const k = WHEEL_K[nm];
        out[nm] = wheelRgb(cb, cr).map((o) => k * o + k * li / 100);
      });
      return out;
    }
    /* steps 1 and 2 as [[r × 256], [g × 256], [b × 256]] floats 0..1, or null */
    function channels(c) {
      const cv = (c && c.curves) || {}, wh = c && c.wheels;
      if (!Object.keys(cv).length && !(wh && Object.keys(wh).length)) return null;
      const f = {};
      ['master', 'r', 'g', 'b'].forEach((k) => { if (cv[k] && cv[k].length >= 2) f[k] = curve(cv[k]); });
      const P = wh ? wheelParams(wh) : null;
      return ['r', 'g', 'b'].map((ch, i) => {
        const row = new Array(256);
        for (let v = 0; v < 256; v++) {
          let y = v / 255;
          if (f.master) y = f.master(y);
          if (f[ch]) y = f[ch](y);
          if (P) {
            y *= 1 + P.highlights[i];
            y += P.shadows[i] * (1 - y);
            y = clamp01(y) ** (2 ** -P.midtones[i]);
          }
          row[v] = clamp01(y);
        }
        return row;
      });
    }
    /* the shader's table: 256 RGBA codes, as a plain array (it goes to the worker) */
    function table(c) {
      const rows = channels(c);
      if (!rows) return null;
      const data = new Array(1024);
      for (let v = 0; v < 256; v++) {
        for (let i = 0; i < 3; i++) data[v * 4 + i] = Math.min(255, Math.max(0, Math.floor(255 * rows[i][v] + 0.5)));
        data[v * 4 + 3] = 255;
      }
      return data;
    }
    /* the HSL Secondary's 16 uniforms, or null when its correction is neutral */
    function hslUniforms(hsl) {
      if (!hsl) return null;
      const P = { ...HSL, ...hsl };
      if (P.shift === 0 && P.saturation === 100 && P.temperature === 0 && P.tint === 0) return null;
      const soft = P.soft / 100;
      const a = P.shift * Math.PI / 180;
      const t = P.temperature / 100, n = P.tint / 100;
      return [P.hue, P.width, Math.max(0.5, 60 * soft), Math.max(0.002, 0.25 * soft),
              P.sat_lo / 100, P.sat_hi / 100, P.light_lo / 100, P.light_hi / 100,
              Math.cos(a), Math.sin(a), P.saturation / 100, 0,
              -0.1 * t + 0.1 * n, 0.1 * t + 0.1 * n, 0, 0];
    }
    return { curve, channels, table, hslUniforms, wheelRgb, HSL };
  })();

  /* `a` (effects.adjust's) with its Lumetri curves / wheels / hsl turned into the shader's
   * `ctab` {key, data} and `hsl` uniforms; the table is built once per set of values. */
  const ctabMemo = new Map();
  let ctabSerial = 0;
  function withCreative(a) {
    if (!a || (!a.curves && !a.wheels && !a.hslKey)) return a;
    const out = { ...a };
    delete out.curves; delete out.wheels; delete out.hslKey;
    if (a.curves || a.wheels) {
      const key = JSON.stringify([a.curves || null, a.wheels || null]);
      let rec = ctabMemo.get(key);
      if (!rec) {
        const data = LUMETRI.table({ curves: a.curves, wheels: a.wheels });
        rec = data ? { key: `ctab${++ctabSerial}`, data } : null;
        ctabMemo.set(key, rec);
        if (ctabMemo.size > 64) ctabMemo.delete(ctabMemo.keys().next().value);
      }
      if (rec) out.ctab = rec;
    }
    const h = a.hslKey ? LUMETRI.hslUniforms(a.hslKey) : null;
    if (h) out.hsl = h;
    return out;
  }
  const creativeOn = (a) => !!(a && (a.ctab || a.hsl || (a.vig && a.vig[0])));

  function adjustNow() {
    const fx = window.effects;
    if (!G.shot || !fx || typeof fx.adjust !== 'function') return null;
    let a = null;
    try { a = withCreative(fx.adjust(G.shot, G.lut)); } catch (e) { a = null; }
    if (!a) return null;
    const near = (x, y) => Math.abs(x - y) < 1e-4;
    const trivial = a.pre.every((x) => near(x, 1)) && near(a.con, 1) && near(a.sat, 1)
      && !((a.knee ?? 1) < 1) && near(a.lift ?? 0, 0) && (a.tone || NO_TONE).every((x) => x === 0)
      && !creativeOn(a);
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

  /* The picture the Program shows now, at most `maxW` wide (Lumetri Scopes, wave 11):
   * {w, h, data} RGBA, bottom row first — graded when the canvas is on, else the raw
   * frame through the identity, as probe() reads it. Drawn by a renderer of its own on a
   * canvas of its own (never the monitor's), in this task. */
  function sample(maxW = 256) {
    if (!G.ok) return null;
    const v = live();
    if (!v || v.readyState < 2) return null;
    if (!G.S) {
      try { G.S = Renderer(document.createElement('canvas'), stage2d); } catch (e) { G.S = null; }
      if (!G.S) return null;
    }
    const on = !!(G.canvas && G.canvas.classList.contains('on'));
    const j = job(!on) || (on ? job(true) : null);
    if (!j) return null;
    const s = Math.min(1, maxW / j.vw);
    j.w = Math.max(2, Math.round(j.vw * s));
    j.h = Math.max(2, Math.round(j.vh * s));
    if (j.parked) j.parked += `|s${j.w}`;
    if (!drawOn(G.S, j)) return null;
    return G.S.pixels();
  }

  function show(on) {
    if (!G.canvas) return;
    const was = G.canvas.classList.contains('on');
    G.canvas.classList.toggle('on', !!on);
    // the live buffer under it is unseen once the canvas has drawn (cover); off: seen at once
    if (!on) cover(false);
    else if (!was) G.coverPending = true;
    // a transition's layer is the page's while the canvas is off, the canvas's while it
    // is on (setMix): the page is told when that flips under it (a cut's LUT landing)
    if (was !== !!on && G.mix && typeof G.mix.onChange === 'function') setTimeout(G.mix.onChange, 0);
  }

  /* (wave 15 PERF6, verdict 11 punch 3 + 9) The canvas is opaque and covers the picture: while
   * it is up and has drawn, the live <video> under it is unseen (workspace.css `.rc-cover`,
   * by visibility: it still plays, calls back and gives the grade its VideoFrames, as a
   * dissolve's hidden followers do). Under the canvas the software compositor still converted
   * and drew that video every frame, and on a shot with a rotation or an opacity from Effect
   * Controls each of the two pictures was a render surface of its own: Killington's dissolve
   * into its rotated shot, 7.8 ms a frame of the display compositor for the Program alone;
   * covered, the compositor's busy share of a play went from 36-37 % to 27-32 % (traced, load
   * 0.5-0.7). Off (ungraded, the grade disabled, no renderer): at once. */
  function cover(on) {
    if (!on) G.coverPending = false;
    const scr = G.canvas && G.canvas.closest('.screen');
    if (scr && scr.classList.contains('rc-cover') !== !!on) scr.classList.toggle('rc-cover', !!on);
  }
  function drewOn() {
    if (G.coverPending && G.canvas && G.canvas.classList.contains('on')) { G.coverPending = false; cover(true); }
  }

  /* Whether the canvas should be visible right now, and make it so. */
  function settle() {
    const graded = !!(G.lut && (!G.lut.identity || adjustNow()));
    const mixGraded = mixDrawn();
    const on = !!(G.ok && G.enabled && live() && (graded || (mixGraded && G.lut)));
    // An ungraded shot's dissolve turns the canvas on: its first mixed frame is drawn before
    // it shows, never the canvas's last picture of some other shot (fromWorker shows it).
    if (on && !graded && G.worker && G.canvas && !G.canvas.classList.contains('on')) {
      G.showOnDraw = true;
      draw();
      return;
    }
    G.showOnDraw = false;
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
   * longer live simply stops.
   *
   * Playing, the frame goes to the worker from the frame callback itself (wave 15 PERF5,
   * verdict 11 punch 3), not from an animation-frame callback later in the step: those run
   * after every video frame callback and every animation-frame callback queued before them —
   * app.js's tick, the cut's advance(), the park and the inspector's rebuild that follow a
   * cut (afterFirstFrames runs them in frame callbacks) — so the post waited 0.5-9 ms behind
   * them (traced at 1920), a different wait each frame, and the canvas's draws, a frame
   * period apart, came that much early or late: a frame the compositor held for 4 vsyncs
   * (66.7 ms) read 69-78 ms on the worker's clock. Posted here, it waits for none of the
   * step's animation-frame callbacks. Through a dissolve (the mix's weight is set in the
   * tick), within CUT_NEAR_S of the shot's out-point (the cut's setShot draws the incoming
   * frame in the same step: one draw, not two), and paused, it still coalesces (drawSoon). */
  const CUT_NEAR_S = 0.08;
  function nearCut(v) {
    const t = window.tl;
    const seg = t && typeof t.byId === 'function' && G.shot ? t.byId(G.shot) : null;
    if (!seg || !Number.isFinite(seg.out)) return true;
    return seg.out - v.currentTime < CUT_NEAR_S * (Number(seg.speed) || 1);
  }
  function armFrames() {
    const v = live();
    const gen = ++G.frameGen;
    if (!v) return;
    const again = () => {
      if (gen !== G.frameGen || v !== live()) return;
      if (G.worker && !G.drawReq && !G.mix && !G.mixZone && playerPlaying() && !v.paused && !nearCut(v)) settle();
      else drawSoon();
      if ('requestVideoFrameCallback' in v) v.requestVideoFrameCallback(again);
      else requestAnimationFrame(again);
    };
    if ('requestVideoFrameCallback' in v) v.requestVideoFrameCallback(again);
    else requestAnimationFrame(again);
  }

  /* A dissolve's other half follows too (wave 15 PERF5, verdict 11 punch 9): while the canvas
   * draws a mix, that buffer's new frame draws it when the live buffer's own frame is late —
   * none drawn for MIX_LATE_MS, more than a frame period at 29.97 — so the mixed picture
   * changes when either half does. Only the live buffer's frames drew it, so a live frame the
   * compositor held (3-4 vsyncs: on Killington the incoming buffer's first frames as the live
   * one, at the cut inside the window) held the mix with it while the other half had a new
   * frame. On time, the live buffer's callback draws and this one finds the draw recent: no
   * second draw a frame (both late together, as after a main-thread stall, drawSoon makes one). */
  const MIX_LATE_MS = 40;
  function armMix(v) {
    const gen = ++G.mixGen;
    if (!v || !('requestVideoFrameCallback' in v)) return;
    const again = () => {
      if (gen !== G.mixGen || !G.mix || G.mix.video !== v) return;
      if (playerPlaying() && mixDrawn() && performance.now() - G.drawnAt >= MIX_LATE_MS) drawSoon();
      v.requestVideoFrameCallback(again);
    };
    v.requestVideoFrameCallback(again);
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

  /* The look's preview (verdict 10, punch 7). An AI review's look or Color Match is not
   * in any LUT the server has baked — those are baked from the colour block on disk, and
   * nothing under review is saved. `preview(block)` hands this module the block the board
   * holds in memory: every shot the monitor shows then takes the LUT baked from that
   * block (POST /api/lut/{id}: the server's own `shot_fn`, the one GET and the render
   * bake with, so the After picture is the one Accept saves and the export makes),
   * cached apart from the saved LUTs, which stay; `preview(null)` puts them back.
   * `{untilSave: true}` (Accept): the block stands in until the next save lands
   * (`invalidate`), so the accepted look does not blink back to Before while the
   * autosave writes it. */
  const lutCache = () => (G.pcol ? G.pcache : G.cache);
  function fetchPreview(id) {
    const cache = G.pcache, pe = G.pepoch;
    if (cache.has(id)) return Promise.resolve(cache.get(id));
    const p = fetch(`/api/lut/${encodeURIComponent(id)}?n=${LUT_N}`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: `{"colour":${G.pcol}}`,
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        const rec = d ? toRecord(d) : IDENTITY;
        if (pe === G.pepoch) cache.set(id, rec);
        return rec;
      })
      .catch(() => {
        if (pe === G.pepoch && cache.get(id) === p) cache.delete(id);
        return IDENTITY;
      });
    cache.set(id, p);
    return p;
  }
  function preview(block, opts = {}) {
    const next = block && typeof block === 'object' ? JSON.stringify(block) : null;
    G.puntil = !!(next && opts && opts.untilSave);
    if (next === G.pcol) return Promise.resolve(G.lut);
    // ◐ Before / After flips between the saved LUTs and this block: the block's LUTs are
    // kept while it is off, so After comes back without a second bake
    if (G.pcol) G.pkeep = { col: G.pcol, cache: G.pcache };
    G.pcol = next;
    if (next) G.pcache = G.pkeep && G.pkeep.col === next ? G.pkeep.cache : new Map();
    return setShot(G.shot);
  }

  function fetchLut(id) {
    if (G.pcol) return fetchPreview(id);
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
    const hit = lutCache().get(G.shot);
    const rec = hit && typeof hit.then !== 'function' ? hit : await fetchLut(G.shot);
    if (gen !== G.gen) return;          // the monitor moved on while this was loading
    G.lut = rec;
    // A draw already asked for in this step (the outgoing buffer's frame callback) draws
    // the new shot after this; otherwise now. Never both: one draw per frame at a cut.
    // A cut inside a dissolve (wave 15 PERF5): the mix still names the buffer that is now the
    // live one (app.js sets the outgoing half after this, in the same tick), so a draw now was
    // the incoming shot over itself and then, at setMix, a second mix draw (two frames each)
    // in the cut's frame. It waits for the end of this task and is dropped if setMix drew.
    if (!G.drawReq) {
      if (G.mix && playerPlaying()) {
        const n = G.drawNo;
        queueMicrotask(() => { if (gen === G.gen && !G.drawReq && G.drawNo === n) settle(); });
      } else settle();
    }
  }

  /* The player parked the buffer for the next cut on this shot (app.js armNext): its LUT
   * is fetched and its texture made now, between cuts, so the cut binds it and draws. */
  function prefetch(id) {
    if (id == null || !G.ok) return;
    const sid = String(id);
    if (sid.startsWith('tmp-')) return;
    fetchLut(sid).then((rec) => {
      if (!G.ok || !rec || rec.identity || lutCache().get(sid) !== rec) return;
      if (G.worker) {
        if (G.sentKey === rec.key) return;
        G.worker.postMessage({ t: 'lut', key: rec.key, n: rec.n, data: rec.data });
      } else {
        G.R.lut(rec.key, rec.n, rec.data);    // binds it on TEXTURE1; a draw rebinds the live one
      }
    });
  }

  /* The transition's other shot's Lumetri (contrast, tone, saturation: never baked in its
   * LUT), or null when it has none. Its balance is in its LUT already: no `pre`. */
  function mixAdjust() {
    const fx = window.effects;
    if (!G.mix || !fx || typeof fx.adjust !== 'function') return null;
    let a = null;
    try { a = withCreative(fx.adjust(G.mix.id)); } catch (e) { a = null; }
    if (!a) return null;
    const near = (x, y) => Math.abs(x - y) < 1e-4;
    const tone = a.tone || NO_TONE;
    if (near(a.con, 1) && near(a.sat, 1) && tone.every((x) => x === 0) && !creativeOn(a)) return null;
    return { pre: [1, 1, 1], con: a.con, sat: a.sat, knee: 1, lift: 0, tone, ctab: a.ctab, hsl: a.hsl, vig: a.vig };
  }

  function mixIsGraded() {
    return !!(G.mix && G.mix.w > 0 && G.mix.rec && (!G.mix.rec.identity || mixAdjust()));
  }

  /* Whether the canvas draws the transition's mix: a graded one always; an ungraded one
   * too while the Program plays (wave 14 PLAY4, verdict 10 punch 5). Shown as its own layer
   * at CSS opacity over the raw live buffer, the incoming picture was a second video for the
   * software compositor to convert and blend on every frame: on Killington the ungraded
   * dissolves held the live shot or the follower 83-100 ms in 10 of 24 plays where the same
   * dissolves graded (the canvas, through identity tables here) held none over 67 (bin/ab3).
   * The shader's mix is the CSS one (both blend R'G'B' at the incoming weight), and paused
   * the layer is shown as before. */
  /* The player is near a dissolve (app.js programMix: from MIX_ZONE_S before its incoming
   * follower starts to MIX_ZONE_S after its window): while playing, the canvas is drawn at
   * the mix's size all that time (drawSize), so it is not resized where the mix begins and
   * ends. A resize there coincided with the mix's first frames, the cut's work and the park
   * of the next shot, and held the canvas 80-110 ms and the live frames up to 100 ms on
   * Killington (wave 14 PLAY4: every canvas hole over 67 ms in a graded dissolve's play sat
   * on a 640 → 454 or 454 → 640 width change). */
  function mixNear(on) {
    G.mixZone = !!on;
  }

  function mixDrawn() {
    if (!G.mix || !(G.mix.w > 0) || !G.mix.rec) return false;
    return mixIsGraded() || playerPlaying();
  }

  /* A transition's other shot (app.js programMix): `{video, id, w}` — that buffer's frame
   * is drawn through its shot's LUT over the live one at weight w — or null. Returns
   * whether the canvas draws the mix (then the page hides that layer; else the page shows
   * it at CSS opacity w, which is exact while both shots are ungraded). */
  function setMix(m) {
    const was = G.mix;
    if (!m || !m.video || !(m.w > 0) || m.id == null || !G.ok) {
      G.mix = null;
      if (was) { G.mixGen++; drawSoon(); }
      return false;
    }
    const id = String(m.id);
    const hit = G.cache.get(id);
    const rec = hit && typeof hit.then !== 'function' ? hit : (was && was.id === id ? was.rec : null);
    G.mix = { video: m.video, id, w: m.w, rec, onChange: m.onChange };
    if (!was || was.video !== m.video) armMix(m.video);
    // Playing, the live buffer's frame callback draws every frame at the newest weight: a
    // draw here too (programMix runs every animation frame) was a second graded frame per
    // video frame, two uploads each, through the whole window — the worker fell behind
    // and the canvas held 80-210 ms at load 16-22 (verdict 7 punch 4, measured). Only a
    // new mix (another shot, another follower, its LUT in) or a parked monitor draws here.
    const lv = live();
    const same = was && was.id === id && was.video === m.video && !!was.rec === !!rec;
    if (same && lv && !lv.paused && playerPlaying()) return !!(G.canvas && G.enabled && G.canvas.classList.contains('on'));
    if (!rec && !id.startsWith('tmp-')) {
      // its LUT is still on its way: once here the canvas may turn on, and the page has to
      // hide the layer it showed meanwhile (`onChange`; also when the canvas flips)
      fetchLut(id).then((r) => {
        if (!G.mix || G.mix.id !== id) return;
        G.mix.rec = r;
        settle();
        if (typeof m.onChange === 'function') m.onChange();
      });
    }
    settle();
    return !!(G.canvas && G.enabled && G.canvas.classList.contains('on'));
  }

  function invalidate() {
    G.cache.clear();
    G.epoch++;
    // the preview's LUTs were baked against the cut on disk too; Accept's stands in no more
    G.pcache = new Map();
    G.pkeep = null;
    G.pepoch++;
    if (G.puntil) { G.pcol = null; G.puntil = false; }
    // the records are new objects with new keys: the old textures are only weight now
    if (G.worker) G.worker.postMessage({ t: 'forget' });
    else if (G.R) G.R.forget();
    if (G.P) G.P.forget();
    if (G.S) G.S.forget();
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
    // ?grade=full (or window.ROUGHCUT_GRADE_FULL): never the lean programs, for comparison
    const full = !!(window.ROUGHCUT_GRADE_FULL || /[?&]grade=full\b/.test(location.search));
    w.postMessage({ t: 'init', canvas: oc, full }, [oc]);
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
    preview,
    get previewing() { return !!G.pcol; },
    setMix,
    mixNear,
    get mix() { return G.mix ? { id: G.mix.id, w: G.mix.w, graded: mixIsGraded() } : null; },
    draw: settle,
    probe,
    sample,
    lumetri: LUMETRI,
    // the adjust the shader draws the shot with now (tests read its table and uniforms)
    get adjust() { return adjustNow(); },
    get draws() { return G.draws; },
    get drawLog() { return G.drawLog; },
    get yuv() { return G.yuv; },
    get autoW() { return G.autoW; },
    get mode() { return G.worker ? 'worker' : (G.R ? 'main' : 'off'); },
    get playRes() { return G.res; },
    set playRes(v) { setRes(String(v)); },
  };
})();
