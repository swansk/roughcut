/* Roughcut — the monitor shows the grade without a render (INTAKE M10, I10.4; decision 4).
 *
 * A <canvas> sits over the monitor's two <video> elements (#pv0 / #pv1) and mirrors
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
 */
(function () {
  'use strict';

  const LUT_N = 17;

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

  const G = {
    enabled: true,
    ready: false,
    canvas: null,
    gl: null,
    gl2: false,
    prog: null,
    uN: null,
    texVideo: null,
    texLut: null,
    videos: [],
    shot: null,              // the segment id the monitor is on
    lut: null,               // the current shot's {n, identity, data} or null
    cache: new Map(),        // id → the LUT record, or the promise fetching it
    gen: 0,                  // bumps per setShot / invalidate: a late fetch is ignored
    frameGen: 0,             // bumps per re-arm of the frame callback
    uploaded: null,          // the LUT record in texLut now
    draws: 0,                // frames drawn (tests read it)
    // verdict 4 punch 5: the frame goes up at the size the monitor shows it, once
    stage: null,             // a 2D canvas the frame is scaled into before the upload
    stageCtx: null,
    box: { w: 0, h: 0 },     // the monitor's box (ResizeObserver), CSS px
    frameKey: null,          // the parked frame in texVideo now: no second upload of it
  };

  function $(s) { return document.querySelector(s); }

  function live() {
    return G.videos.find((v) => v.classList.contains('live')) || null;
  }

  /* ------------------------------------------------------------ WebGL */
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

  function setup(canvas) {
    const opts = { alpha: false, antialias: false, premultipliedAlpha: false,
                   preserveDrawingBuffer: false };
    let gl = null, gl2 = false;
    try { gl = canvas.getContext('webgl2', opts); gl2 = !!gl; } catch (e) { gl = null; }
    if (!gl) {
      try { gl = canvas.getContext('webgl', opts) || canvas.getContext('experimental-webgl', opts); }
      catch (e) { gl = null; }
    }
    if (!gl) return false;
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
    G.uN = gl.getUniformLocation(prog, 'u_n');
    G.uPre = gl.getUniformLocation(prog, 'u_pre');
    G.uCon = gl.getUniformLocation(prog, 'u_con');
    G.uSat = gl.getUniformLocation(prog, 'u_sat');
    G.uKnee = gl.getUniformLocation(prog, 'u_knee');
    G.uLift = gl.getUniformLocation(prog, 'u_lift');
    G.uTone = gl.getUniformLocation(prog, 'u_tone');
    G.uToneOn = gl.getUniformLocation(prog, 'u_toneOn');

    G.texVideo = gl.createTexture();
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, G.texVideo);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);

    G.gl = gl;
    G.gl2 = gl2;
    G.prog = prog;
    G.canvas = canvas;
    return true;
  }

  /* Upload a LUT record's bytes as the sampler the shader expects. */
  function uploadLut(rec) {
    const gl = G.gl;
    if (!gl) return;
    G.uploaded = rec;
    if (G.texLut) gl.deleteTexture(G.texLut);
    G.texLut = gl.createTexture();
    gl.activeTexture(gl.TEXTURE1);
    const n = rec.n;
    if (G.gl2) {
      gl.bindTexture(gl.TEXTURE_3D, G.texLut);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
      gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
      gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_WRAP_R, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texImage3D(gl.TEXTURE_3D, 0, gl.RGB8, n, n, n, 0, gl.RGB, gl.UNSIGNED_BYTE, rec.data);
    } else {
      gl.bindTexture(gl.TEXTURE_2D, G.texLut);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
      gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, n, n * n, 0, gl.RGB, gl.UNSIGNED_BYTE, rec.data);
    }
    gl.uniform1f(G.uN, n);
  }

  /* The size the frame is drawn at: the picture as the monitor shows it (object-fit:
   * contain in the monitor's box, times the device pixel ratio), never above the video's
   * own. Verdict 4 punch 5: the 1280×720 proxy went up whole on every frame — through
   * WebGL's video upload, ~25 ms of CPU a frame on a software GL, ~80 ms on a box at
   * three times its cores — to be shown 386 px wide. Scaled first in a 2D canvas, the
   * upload and the shader both work on the pixels that are seen. */
  /* Premiere's Playback Resolution: while the picture plays, the grade is drawn at most
   * PLAY_W wide (CSS scales it up); paused, it is drawn at the monitor's full size again, so
   * a parked frame is always sharp. The per-frame upload and shader cost scale with the
   * drawn pixels: at 674-841 px wide (the wave-4 monitors) a software-GL frame cost enough
   * to open 100 ms holes mid-shot under load (the wave-6 perf gate). */
  const PLAY_W = 640;
  function drawSize(vw, vh, playing) {
    const dpr = window.devicePixelRatio || 1;
    const bw = G.box.w * dpr, bh = G.box.h * dpr;
    if (!bw || !bh) return [vw, vh];
    let s = Math.min(1, bw / vw, bh / vh);
    if (playing) s = Math.min(s, PLAY_W / vw);
    if (s > 0.9) return [vw, vh];   // near full size: the scale costs more than it saves
    return [Math.max(2, Math.round(vw * s / 2) * 2), Math.max(2, Math.round(vh * s / 2) * 2)];
  }

  /* The frame into texVideo, at w×h. A parked frame that is already there is not sent
   * again: an Effect Controls change on a paused monitor redraws with new uniforms only. */
  function upload(v, w, h) {
    const gl = G.gl;
    const parked = v.paused && !v.seeking;
    const key = parked ? `${v.id}|${v.currentSrc}|${v.currentTime}|${w}x${h}` : null;
    if (key && key === G.frameKey) return true;
    G.frameKey = null;
    try {
      if (w === v.videoWidth && h === v.videoHeight) {
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, v);
      } else {
        if (!G.stage) {
          G.stage = document.createElement('canvas');
          // a CPU canvas (willReadFrequently): the frame is scaled where it was decoded
          // and handed up small; an accelerated one round-trips through the GL process,
          // measured 3 ms against 7-14 ms a frame at 384 px wide
          G.stageCtx = G.stage.getContext('2d', { alpha: false, willReadFrequently: true });
        }
        if (G.stage.width !== w || G.stage.height !== h) { G.stage.width = w; G.stage.height = h; }
        G.stageCtx.drawImage(v, 0, 0, w, h);
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, G.stage);
      }
    } catch (e) {
      return false;
    }
    G.frameKey = key;
    return true;
  }

  /* Draw the live video's current frame through the LUT. A no-op while there is
   * nothing to draw: no context, grade off, identity LUT, no live video, or a video
   * that has no frame yet. */
  function draw(raw) {
    const v = live();
    const adj = raw ? null : adjustNow();
    if (!raw && (!G.gl || !G.enabled || !G.lut || (G.lut.identity && !adj))) return;
    if (!G.gl || !v || v.readyState < 2) return;
    const vw = v.videoWidth, vh = v.videoHeight;
    if (!vw || !vh) return;
    const [w, h] = drawSize(vw, vh, !v.paused && !v.ended);
    const gl = G.gl;
    if (G.canvas.width !== w || G.canvas.height !== h) {
      G.canvas.width = w;
      G.canvas.height = h;
      G.frameKey = null;
    }
    gl.viewport(0, 0, w, h);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, G.texVideo);
    if (!upload(v, w, h)) return;   // a frame that is not there yet; the next one will be
    const rec = raw || G.lut.identity ? IDENTITY_TABLE : G.lut;
    if (G.uploaded !== rec) uploadLut(rec);
    const a = adj || NO_ADJUST;
    gl.uniform3f(G.uPre, a.pre[0], a.pre[1], a.pre[2]);
    gl.uniform1f(G.uCon, a.con);
    gl.uniform1f(G.uSat, a.sat);
    gl.uniform1f(G.uKnee, a.knee ?? 1);
    gl.uniform1f(G.uLift, a.lift ?? 0);
    const tone = a.tone || NO_TONE;
    gl.uniform4f(G.uTone, tone[0], tone[1], tone[2], tone[3]);
    gl.uniform1f(G.uToneOn, tone.some((x) => x !== 0) ? 1 : 0);
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(G.gl2 ? gl.TEXTURE_3D : gl.TEXTURE_2D, G.texLut);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    G.draws++;
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
   * the canvas's when it is on, else the raw video's drawn through an identity table on
   * the hidden canvas. Drawn and read in the same task, so it needs no preserved buffer. */
  function probe() {
    if (!G.gl || !G.canvas) return null;
    const v = live();
    if (!v || v.readyState < 2) return null;
    if (G.canvas.classList.contains('on')) draw(); else draw(true);
    const gl = G.gl, w = G.canvas.width, h = G.canvas.height;
    if (!w || !h) return null;
    const px = new Uint8Array(w * h * 4);
    gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px);
    const m = [0, 0, 0];
    for (let i = 0; i < px.length; i += 4) { m[0] += px[i]; m[1] += px[i + 1]; m[2] += px[i + 2]; }
    const n = px.length / 4;
    return m.map((x) => Math.round((x / n) * 100) / 100);
  }

  function show(on) {
    if (!G.canvas) return;
    G.canvas.classList.toggle('on', !!on);
  }

  /* Whether the canvas should be visible right now, and make it so. */
  function settle() {
    const on = !!(G.gl && G.enabled && G.lut && (!G.lut.identity || adjustNow()) && live());
    show(on);
    if (on) draw();
  }

  /* ------------------------------------------------------------ frames */
  /* Follow the live video: a new frame → draw. Re-armed on every setShot so the
   * callback rides the element that is live now; a callback from a video that is no
   * longer live simply stops. */
  function armFrames() {
    const v = live();
    const gen = ++G.frameGen;
    if (!v) return;
    const again = () => {
      if (gen !== G.frameGen || v !== live()) return;
      settle();
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
    return { id: d.id, n, identity: !!d.identity, look: d.look, strength: d.strength,
             balance: d.balance || null, data };
  }

  const IDENTITY = { id: null, n: 2, identity: true, data: null, balance: null };
  // the table an identity shot is drawn through when only the live preview changes it
  const IDENTITY_TABLE = { id: null, n: 2, identity: true,
    data: new Uint8Array([0, 0, 0, 255, 0, 0, 0, 255, 0, 255, 255, 0,
                          0, 0, 255, 255, 0, 255, 0, 255, 255, 255, 255, 255]) };

  function fetchLut(id) {
    if (G.cache.has(id)) return Promise.resolve(G.cache.get(id));
    const p = fetch(`/api/lut/${encodeURIComponent(id)}?n=${LUT_N}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        const rec = d ? toRecord(d) : IDENTITY;
        G.cache.set(id, rec);
        return rec;
      })
      .catch(() => {
        G.cache.delete(id);
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
    if (!G.shot || !G.gl) {
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
    const rec = await fetchLut(G.shot);
    if (gen !== G.gen) return;          // the monitor moved on while this was loading
    G.lut = rec;
    if (!rec.identity) uploadLut(rec);
    settle();
  }

  function invalidate() {
    G.cache.clear();
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
    G.videos = [$('#pv0'), $('#pv1')].filter(Boolean);
    if (!canvas || !G.videos.length) return;
    try {
      G.ready = setup(canvas);
    } catch (e) {
      console.warn(String(e && e.message || e));
      G.ready = false;
      G.gl = null;
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
    // the monitor's box, for drawSize: read here, never per frame (a layout read)
    const box = canvas.parentElement;
    if (box && typeof ResizeObserver === 'function') {
      new ResizeObserver((ents) => {
        const r = ents[ents.length - 1].contentRect;
        G.box = { w: r.width, h: r.height };
      }).observe(box);
    }
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
    invalidate,
    draw: settle,
    probe,
    get draws() { return G.draws; },
  };
})();
