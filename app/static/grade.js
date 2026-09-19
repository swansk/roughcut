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
 *   enabled        true when the grade is shown (G flips it)
 *   toggle()       flip it; returns the new state
 *   setShot(id)    the monitor moved to this shot: fetch (or take from cache) its LUT
 *                  and draw. null / an id that 404s → identity, canvas hidden.
 *   invalidate()   the colour block was saved: drop every cached LUT and refetch the
 *                  current shot's.
 *   ready          true once the canvas has a context (tests read it)
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
    out vec4 outColor;
    void main() {
      vec3 c = clamp(texture(u_video, v_uv).rgb, 0.0, 1.0);
      vec3 p = c * ((u_n - 1.0) / u_n) + 0.5 / u_n;
      outColor = vec4(texture(u_lut, p).rgb, 1.0);
    }`;

  // WebGL1: the table as an n × n² image. Bilinear inside a blue slice (red across,
  // green down), the blue interpolation by hand between the two neighbouring slices.
  const FS1 = `
    precision mediump float;
    varying vec2 v_uv;
    uniform sampler2D u_video;
    uniform sampler2D u_lut;
    uniform float u_n;
    void main() {
      vec3 c = clamp(texture2D(u_video, v_uv).rgb, 0.0, 1.0);
      float bf = c.b * (u_n - 1.0);
      float b0 = floor(bf);
      float b1 = min(b0 + 1.0, u_n - 1.0);
      float t = bf - b0;
      float x = (c.r * (u_n - 1.0) + 0.5) / u_n;
      float y0 = (b0 * u_n + c.g * (u_n - 1.0) + 0.5) / (u_n * u_n);
      float y1 = (b1 * u_n + c.g * (u_n - 1.0) + 0.5) / (u_n * u_n);
      vec3 s0 = texture2D(u_lut, vec2(x, y0)).rgb;
      vec3 s1 = texture2D(u_lut, vec2(x, y1)).rgb;
      gl_FragColor = vec4(mix(s0, s1, t), 1.0);
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

  /* Draw the live video's current frame through the LUT. A no-op while there is
   * nothing to draw: no context, grade off, identity LUT, no live video, or a video
   * that has no frame yet. */
  function draw() {
    const v = live();
    if (!G.gl || !G.enabled || !G.lut || G.lut.identity || !v || v.readyState < 2) return;
    const w = v.videoWidth, h = v.videoHeight;
    if (!w || !h) return;
    const gl = G.gl;
    if (G.canvas.width !== w || G.canvas.height !== h) {
      G.canvas.width = w;
      G.canvas.height = h;
    }
    gl.viewport(0, 0, w, h);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, G.texVideo);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
    try {
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, gl.RGB, gl.UNSIGNED_BYTE, v);
    } catch (e) {
      return;                       // a frame that is not there yet; the next one will be
    }
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(G.gl2 ? gl.TEXTURE_3D : gl.TEXTURE_2D, G.texLut);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  function show(on) {
    if (!G.canvas) return;
    G.canvas.classList.toggle('on', !!on);
  }

  /* Whether the canvas should be visible right now, and make it so. */
  function settle() {
    const on = !!(G.gl && G.enabled && G.lut && !G.lut.identity && live());
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
    return { id: d.id, n, identity: !!d.identity, look: d.look, strength: d.strength, data };
  }

  const IDENTITY = { id: null, n: 2, identity: true, data: null };

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
    if (!G.shot || !G.gl) {
      G.lut = null;
      settle();
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

  function toggle() {
    G.enabled = !G.enabled;
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
        v.addEventListener(ev, () => { if (v === live()) settle(); });
      });
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();

  window.grade = {
    get enabled() { return G.enabled; },
    set enabled(v) { G.enabled = !!v; settle(); },
    get ready() { return G.ready; },
    get shot() { return G.shot; },
    get lut() { return G.lut; },
    toggle,
    setShot,
    invalidate,
    draw: settle,
  };
})();
