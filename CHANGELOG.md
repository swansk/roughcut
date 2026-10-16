# Changelog

All notable changes to Roughcut are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [SemVer](https://semver.org/)
once code exists; spec-phase entries go under [Unreleased].

Every commit that changes behavior or documentation adds an entry under [Unreleased], in the
same commit. Releases move entries into a dated version section.

## [Unreleased]

### Added
- **Through a Cross Dissolve the buffer that will play the incoming shot plays its head as the dissolve's incoming picture, and the next shot's park waits for the dissolve to end (redesign wave 11, lane CANVAS; verdict 8 punch 3, TXHITCH's queued requests).** *Cause:* around a dissolve's edit four decoders ran at once — the live shot, `#pvTxIn` (the incoming shot's head), the cut's preroll of the same incoming shot in its parked buffer (the same frames a second time, from 0.3 s before the edit) and `#pvTxOut` from 0.2 s before it — and right after the edit the buffer just left was re-armed for the shot after (a seek, a decode from the keyframe) inside the second half, with Effect Controls' rebuild and the timeline repaint (~100 ms of main thread at load 20) on the same frames. *The fix* (`app.js`): a shot a transition comes into is parked where the incoming follower would start, its half + TX_PRE before its in-point (`txLeadIn`, `prerollAt`; `armNext` keeps such a park only when it is exact), and `preroll()` starts it there; while it plays at the film's frame, `programMix` hands it the incoming role (`txHand`: `prog.tx.in` is that buffer, steered by `txFollow`, its sound the crossfade's sin through rcAudio's `xfade`, laid out like a follower — display block, hidden by visibility unless it is the ungraded layer at CSS opacity — and drawn by the grade's mix when graded), `#pvTxIn` (`prog.tx.inOwn`) stays paused, and at the edit it is the rolled buffer `advance()` takes, already on its in-point (`txUnhand` gives back its styles). A cue inside the window, or a park that did not land, uses `#pvTxIn` as before. After a cut inside a dissolve, `afterTxWindow` holds the next shot's park and the inspector/timeline settle until the second half is over (at once when the monitor stops or the shot has too little left after it). `test_tx_playback` follows whichever element plays each half (`tx_el`), asserts no seek on it (`tx_seeks`) and each follower's longest gap inside its half <= 67 ms, and the graded canvas through the dissolve and across a plain graded cut <= 67 ms (it was "a plain cut + a frame"). Measured on the fixture's 1 s dissolve at load 24-35 (the box shared with five lanes and a tourney): the incoming half played by the handed buffer (`pv1`) in every run, 0 follower seeks, followers 50-67 ms in 6 of 7 halves (95 ms once at load 34.7), the deferred park's seek lands after the window (film 3.59 s against a window ending at 3.5). test_transitions 13 passed.
- **A graded shot plays at the video's rate: the grade worker draws a playing frame from its decoded planes, the buffers under the canvas are not drawn, the playhead moves a frame at a time (redesign wave 11, lane CANVAS; verdict 8 punch 2).** *Measured causes* (Chromium traces of a 3 s graded play across a Killington cut, own snapshot server on 8795, `--work` a copy of the judge's; a stand-alone bench page on a Killington 1280x720 proxy): (1) the worker was saturated, 96 % busy at load 10: 21 ms a frame of JS (`drawImage` of the VideoFrame into the 2D stage, a CPU YUV→RGB conversion and scale, 8-10 ms of it alone on the bench, then the flipped RGBA upload) plus 17 ms blocked in the commit's software-GL read-back (`GetResourceForPushFrame` → `GLES2::ReadPixels`), 38 ms against a 33 ms frame; (2) the live `<video>` under the opaque canvas was still converted for the software compositor every frame (`VideoResourceUpdater::CreateForSoftwareFrame`, 73-84 ms of CPU per 3 s) and drawn by the display compositor under a picture that hides it; (3) the main thread re-rendered the board at the display's 60 Hz for a 29.97 sequence (170 BeginMainFrames per 3 s: the playhead, readout and Program followers at every animation frame), 63 % of a core, and it is the thread that feeds the worker its frames. *The fix* (`grade.js`): while playing, the worker copies the frame's own I420/NV12 planes out of the VideoFrame (`yuvPlanes`, `VideoFrame.copyTo`, the decoder's frame closed at once), boxes the luma 2x2 to the chroma's size in JS when the picture is drawn at half size or less (~1 ms at 1280x720), uploads the planes at the size they are read (`texSubImage2D` into kept R8 / RG8 textures: a third of the RGBA bytes) and converts them in the shader with the frame's own matrix and range (`u_yuv`, `u_yoff`; BT.709 full range on Killington's proxies: within 2 levels of the browser's `drawImage`, mean 0.2; 601 / 2020 / limited range by `VideoFrame.colorSpace`; any other layout, a parked frame and probe() stay on the RGB path); a second program (`FS2(true)`) beside the RGB one, both warmed with one draw of each kind at worker start (`warm()`: a software GL compiles them on first use, measured as a 156 ms hole at the first play's start at load 30). Auto snaps a picture within 3 % of half the frame to exactly half (a 674-px monitor drew 638 of 1280: now 640, one texel a pixel, nearest); a dissolve on this path is drawn at PLAY_W, not MIX_W (its second frame costs a third of what it did). `show()` puts `rc-graded` on the screen while the canvas is on and a rule hides the buffers under it by visibility (they keep decoding, presenting and calling back, as the followers do). The draw count moves when the worker says it drew (`drawn.ok`), and `grade.drawLog` keeps the last 600 draws with the page-clock time each frame was made, posted, received and drawn, for the judge. `app.js` `paintPos`: while playing, the playhead, readout and the Program's followers move once per sequence frame (`tl.gridRate()`), as Premiere's does. *Measured* (worker JS per frame, Killington, load 13-20): 21 ms → 0.4-4.6 ms median; tracked renderer CPU per drawn frame 31.1 → 21.6 ms; the bench's whole per-frame cost (CPU + software GL) 17.4 ms on the RGB path, 11-13 ms on the boxed planes. Killington graded plays across a cut at 1440, base load 9.5-10.6: canvas 67.5-91 ms, 22.6-27.7 fps; fix at load 13.4-14.7 (the box rose): 30.2-30.4 fps in 2 of 3 plays, holes 76-110 ms where the live buffer's own frames held 67-100 ms (the decoders starved: frame timestamps 73-83 ms apart). Perf fixture A/B at load 30-38: canvas 19.5 → 28.5 fps (1440), 19.6 → 24.7 fps (1920). test_playback_resolution 1, test_colour_ui 9, test_effect_controls 26, test_keyframes_anchor 13, test_keyframes_ui 8, test_render_keyframes 38, test_overlay_controls 10, test_program_a3 3, test_program_tracks 6, test_transitions 13: all passed.
- **The perf test measures the graded canvas, the picture a coloured shot shows (redesign wave 11, lane CANVAS; verdict 8 punch 2).** `test_latency_under_load` timed only the live `<video>` buffer's frames; on a graded shot the grade canvas over the buffers is what the viewer sees, and the judge measured it on Killington at 24-27 fps with 70-114 ms holes at load 10-12 while the perf test said 50-67 ms. `measure_play` now also samples `grade.draws` on every animation frame with whether `#gradeCanvas` is on (`canvas_gaps`: the longest time between two draws while the canvas is on, and after the last one, from the first draw of the play; the draw rate; the holes over the limit), across both cuts of the 4K 59.94 fixture (every shot hand-balanced), on the 1440 board and again on a 1920 one (`page.set_viewport_size`, then back to 1440 for the rest). Both sizes must hold <= GAP_LIMIT_MS (67 ms), the same limit as the buffers. The perf record carries them as `facts.canvas` (`{"1440": …, "1920": …}`) and `tools/gate.py`'s perf line says "graded canvas worst gap X ms at N fps (1440), Y ms at M fps (1920)" (or "not measured"); `test_gate_status` pins the wording. *On the base `02fc382`, A/B against the fix on the same box minutes apart:* load 20.3: canvas 112.5 ms, 17.6 fps, 13 holes over 67 (1440) — fix 99.9 ms, 25.5 fps, 5 holes; load 30-38: base 94.8 ms / 19.5 fps (1440), 109.5 ms / 19.6 fps (1920) — fix 156.3 ms (one hole, at the play's first frames) / 28.5 fps and 100.5 ms / 24.7 fps. At load 11.9 (0 burners) the base passed on the fixture (59.0 ms, 30.4 fps) while Killington failed at load 10 (67.5-91 ms, 22.6-27.7 fps): the fixture's board is lighter than a 21-shot cut, so the check bites from about load 15 on this box.
- **Rubric log + budget for both wave 11 tabs together** (lead's merge of FXBIN over LUMETRI): each lane measured +2 words / +1 control from the same base; the merged board measures `/` 97·53, `/ shot` 123·68, `/ film` 105·52, `/ fx` 128·56, `/ jobs` 106·54, now the limits exactly; FXBIN's share logged in REDESIGN-GOAL.md.
- **Rubric log: the screens budget's +2 words / +1 control for the Lumetri Scopes tab** (REDESIGN-GOAL.md, logged by the lead for lane LUMETRI, measured counts, no headroom).
- **Lumetri Color beyond Basic Correction: Curves, Color Wheels, HSL Secondary and Lumetri Scopes (redesign wave 11, lane LUMETRI; verdict 8 punch 4, verdict 7 punch 5, REDESIGN-GOAL dimension 7).** *Effect Controls › Lumetri Color* now has Premiere's sections after Basic Correction, closed until twirled open: **Curves** (RGB Curves: a square graph with its grid and diagonal, the master / R / G / B channel dots, the other channels' curves faint behind the active one; a press off the points adds one there and drags it, a press on one drags it between its neighbours, Ctrl/⌘-click deletes it, a double-click puts the channel back to the diagonal), **Color Wheels & Match** (Shadows, Midtones, Highlights: a hue wheel laid out as the vectorscope is, a puck dragged by the pointer's travel — Shift for fine — a double-click centres it, and a vertical lightness slider beside each), **HSL Secondary** (Key: Hue, Hue range, Sat min/max, Light min/max, Softness; Correction: Hue shift, Saturation, Temperature, Tint — each a slider and a scrubbable number). Every gesture is one undo entry (`add curve point`, `move curve point`, `delete curve point`, `midtones wheel`, `shadows lightness`, `hsl hue shift`…) and Lumetri's ↺ resets all of it. *Schema* (`controls`, validated by `server.clean_controls` through `colour.clean_curves / clean_wheels / clean_hsl`): `curves: {master|r|g|b: [[x, y], …]}` (2–16 points on 0..1, x strictly increasing; the identity dropped), `wheels: {shadows|midtones|highlights: [cb, cr, lightness]}` (puck in the unit disc, lightness −100..100; a wheel at rest dropped), `hsl: {hue, width, sat_lo, sat_hi, light_lo, light_hi, soft, shift, saturation, temperature, tint}` (only what differs from its default; a min above its max is a 400); a V2+ clip's controls refuse all three (V2EC's rule, also in the board's API). *THE formula* is written once in words in `roughcut/colour.py` ("Lumetri beyond Basic") with its Python implementation, and step for step in `grade.js` (`LUMETRI`, the shader's `creative()`): after Basic's saturation and a clamp, (1) curves `y_c = curve_c(curve_master(x_c))`, each a PCHIP through its points (harmonic-mean slopes, flat past the ends); (2) wheels per channel, the puck as a zero-luma BT.709 offset `o`: gain `x·(1 + 0.5·o_h + 0.5·l_h)`, lift `y + (0.25·o_s + 0.25·l_s)(1 − y)`, gamma `y^(2^−(0.6·o_m + 0.6·l_m))`; (3) the HSL key (hue distance and saturation / lightness bands with smoothstep edges) and its correction in YCbCr (chroma turned and scaled, temperature and tint as Cb/Cr offsets, luma kept). Steps 1–2 are a function of each channel alone: the shader samples a 256×1 table of them (built in JS once per set of values, a texture per key), the export runs the same table as one `lutrgb` (`assemble.creative_lutrgb`); step 3 is the shader's code on the board and a content-addressed 33³ cube through `lut3d` in the export (`assemble.hsl_cube`). `adjust_vf` chains Basic → saturation → curves/wheels → HSL in 8-bit planar RGB. A test checks the JS table against the Python one code for code (≤ 1). *Lumetri Scopes:* a tab beside the Source monitor's (`/modules/scopes.js`; Premiere's Color workspace place, so the scopes and the Program show together; the Source tab brings the monitor back): Waveform (Luma) in IRE with a graticule every 10, and Vectorscope YUV with the 75 % targets and the skin-tone line, drawn from `grade.sample()` — the Program's graded frame through a renderer of its own (the raw frame when the grade is off) — on every Program draw while parked and at most 5 times a second while playing. New grade.js API: `grade.sample(maxW)`, `grade.lumetri`, `grade.adjust`; effects.js `effects.curve(ch, pts?)`, `effects.wheel(name, v?)`. Measured (new `test_lumetri_ui.py`, real Chromium, flat-colour clips encoded with the BT.709 matrix they are tagged with): a curve point dragged from (0.5, 0.5) to (0.5, 0.72) moves `grade.probe()` within 150 ms and by Δ [57 56 56], the export of the saved cut by [58 56 56]; a Midtones push toward red Δ [29 8 9] preview, [28 7 9] export; HSL hue shift 120 on red: preview (0, 95, 0), export (0, 93, 0) (THE formula: 95.6); a 0x808080 grey's waveform is one line at 50.2 IRE (every column within 1 IRE), red lands at 102.9° and blue at 354.8° on the vectorscope (±3°), a curve on the grey moves the line within 150 ms, 2 s of play take 3–13 samples. Tests: new `test_lumetri.py` (34: the validators, the curve's properties, the wheels' directions, the key's edges, flat greys and oranges through five cases of the real export within 2.5 code values of the formula, the HSL cube within 3 %, `adjust_vf`'s order) and `test_lumetri_ui.py` (12); `test_effect_controls.py` reads Lumetri's four sections in Premiere's order. `test_budget.py`: the Scopes tab is 2 words and 1 control on each board screen, measured, no headroom (/ 95 · 52, / shot 121 · 67, / film 103 · 51, / fx 126 · 55, / jobs 104 · 53).
- **An Effects bin as Premiere has it: seven transitions to drag onto an edit or a clip's start or end, a default for Ctrl+D / Shift+D, and an edit without handles takes a transition with repeated frames instead of refusing it (redesign wave 11, lane FXBIN; verdict 8 punch 5, verdict 7 punch 6).** *The bin* (`dock.js`, `index.html`): the Project group's third tab, "Effects", holds Premiere's tree in Premiere's order — Audio Transitions › Crossfade (Constant Gain, Constant Power, Exponential Fade) and Video Transitions › Dissolve (Cross Dissolve, Dip to Black, Dip to White, Film Dissolve) — folders that fold (kept per browser), a search box that filters items by name (a folder's name shows its items; "no effect matches …" otherwise), the default transitions' icons outlined, and a right-click "Set Selected as Default Transition". The AI designer's tab is now "Designed Effects" (tool key still `fx`; the Window menu lists both; Effect Controls' five tabs are 3 px tighter so the strip still fits 365 px at 1440 with two badges). *Dropping* (`timeline.js`): an item dragged onto the Timeline within 14 px of an edit is centred on it at Premiere's 1 s; anywhere else inside a shot it is one-sided at the shot's nearer end — a video kind fades from (start) or to (end) black (white for Dip to White), an audio kind fades in or out along its curve; a box previews where it lands; each drop is one undo entry. A right-click on a transition offers "Set Default Transition" (and Clear); Ctrl+D lays the default video kind, Shift+D the audio one (Premiere's Cross Dissolve and Constant Power until changed; kept per browser). *Insufficient media*: a centred dissolve or crossfade keeps its length where a shot has too little handle; the outgoing shot's last frame or the incoming shot's first is repeated (silence for sound), the box is hatched and its tooltip and the toast say "insufficient media — repeated frames" (it was refused with no media and shortened with some). A transition is still cut down to the shots' own lengths. *The model* (`roughcut/tracks.py`, validated by `PUT /api/project`): `kind` is one of `cross_dissolve`, `dip_to_black`, `dip_to_white`, `film_dissolve` (video) or `audio_crossfade` (Constant Power), `constant_gain`, `exponential_fade` (audio); `align` is `center` (a and b), `start` (b only: the shot's first dur) or `end` (a only: its last dur), any other pairing a 400; one video and one audio transition per edge, a later one on an edge replacing the earlier; `transition_halves` returns the kinds and `repeat` (film seconds of repeated frames per side) instead of shortening for media, and `transition_fades` the one-sided ones. *The maths, exact in the export* (`assemble.py`; x the incoming weight, 0 → ½ on the edit's frame → 1): Cross Dissolve (1 − x)·A + x·B per Y'CbCr plane; Film Dissolve the same weights in linear light, g⁻¹((1 − x)·g(A) + x·g(B)) with g(v) = v^2.2 on BT.709 R'G'B' (only the window's frames leave Y'CbCr: split, mixed in gbrp, laid back); Dip to Black / White needs no handle — each part blends its own frames toward K (Y' 16 / 235, Cb Cr 128) by 2x before the edit and 2 − 2x after, K alone on the edit's frame; a one-sided video fade blends toward K from its first frame / to its last (Film Dissolve's by p^(1/2.2), the exact linear-light fade from black); Constant Power cos(πx/2) / sin(πx/2), Constant Gain 1 − x / x (−6.02 dB per side on the edit), Exponential Fade e(1 − x) / e(x) with e(u) = (100^u − 1)/99 (−20.8 dB per side). *The Program* (`app.js`, one hunk in programMix: `TX_DIPS`, `txGains`, `txLayer`): a dip and every one-sided video fade is a black (or white) `#pvDip` layer over V1 and the grade's canvas (under V2) whose opacity follows the render's K weight frame for frame (CSS composites in R'G'B', so it is exact, Film Dissolve's fade included); no follower runs for a dip; crossfades and one-sided sound fades set rcAudio's levels by the kind's curve; a follower past its media holds its first / last frame, as the render repeats it. Film Dissolve's two-shot mix is drawn with the Cross Dissolve's weights (the CSS and grade-worker mixes are in R'G'B'; only the export is linear light). New `app/tests/test_effects_bin_render.py` (10) and `app/tests/test_effects_bin_ui.py` (6); `test_transitions.py`'s insufficient-media cases now expect repeated frames; `test_budget.py` raised to the measured counts (no headroom: one tab, two words).
- **Redesign verdict 8: FAIL, Export 9 (was 5), Responsive 7 (was 5), Monitors 7** (`judge/verdict-8.md` on `1e99342`, judge's gate GREEN). Verdict 7's checks on Killington: the cut shows 0.3 s after a proxy lands (was 30 s); a one-key export succeeds and a 4:41 export runs in 108 s; dissolves still hold 83-100 ms in 3 of 7 plays. New blocker: the Program is plain black for 11.5 s+ with no word on a shot whose preview is still building (`/media/proxy` holds the request 20 s before its 503). Rulings: the late-logged journey limit and 4K fixture honest; PERFGATE honest on balance, but the perf test never measures the graded canvas (70-114 ms holes on Killington).
- **Loop log for wave 10** (`judge/LOOP.md`): what each of BOOT, EXPORTOK, CRAFT, TXHITCH and PERFGATE changed, the measured numbers (cut on screen 0.8 s after a proxy lands, was 28.9 s; Killington dissolve worst hole 83 ms, was 275 ms), and the follow-ups each lane queued.
- **The perf gate can turn green on a box shared with other loops (redesign wave 10, lane PERFGATE; verdict 7 punch 3).** At load ~14 on 6 cores `test_latency_under_load` skipped (above 2× the cores) and the gate stayed AMBER. `test_perf_latency.py` now runs there with 0 burners instead of skipping and records `load_pre`, `shared` and `settled_s`; a first reading above 2× waits up to 75 s (`ROUGHCUT_PERF_SETTLE`) for its own fixture's 4K encodes to drain out of the 1-minute load (measured 22.8 → 16.6), so the test's own work does not make a box read as shared. `tools/gate.py` runs the test alone before the suite and again after it (deselected from the suite run; the after run waits up to 300 s for a quieter box unless the before run measured on an unshared one), counts the better-conditioned run that measured (lower `load_pre`, ties to the before run) and says which: a pass is green wherever it ran (the line says "shared box" and the load), a fail above 2× the cores is AMBER and named, a fail at or below it RED, nothing measured (`ROUGHCUT_PERF_FORCE_SKIP=1`) AMBER. `perf_verdict` / `perf_conditions` / `perf_shared` / `run_status` are pure functions, pinned by the new `app/tests/test_gate_status.py` (14). REDESIGN-GOAL's rubric log records it (mixed: stricter in three places, lenient in one) and the two wave-8 gate changes never logged: `JOURNEY_SLOWEST_S` and the perf test's 4K 59.94 fixture.
- **Redesign verdict 7: FAIL, Export and Responsive at 5, the rest 8-9** (`judge/verdict-7.md` on `273b1f1`). Blockers: K2; a single Opacity keyframe makes every export fail (an empty sendcmd file); the board shows nothing for ~30 s after a background proxy lands (boot waits on `/api/junk`, which measures colour inside the GET); the gate AMBER (perf skipped at load 14 from other loops). Verified since verdict 6: saves survive a reload 60 ms after an edit; real-footage cut switches 17-34 ms (was 200-600); a 4:24 Quick export in 108 s; dissolves exact in Program and export; keys follow a razor; V2+ Effect Controls; background encodes stop during play.
- **Transitions: Ctrl+D Cross Dissolve, Shift+D Constant Power crossfade, Ctrl+Shift+D both, at the selected edit (redesign wave 9, lane TRANSITIONS; verdict 6 punch 6).** *The model* (`roughcut/tracks.py`): the EDL's `transitions: [{a, b, kind: cross_dissolve | audio_crossfade, dur, align: center}]` names an edit by its two shots, so it rides trims and edits elsewhere and leaves with a move or a delete that parts them; one per kind per edit; centred only, so the film's length never changes and each shot gives dur/2 of handle. `PUT /api/project` validates it against the cut it writes (a bad kind, a duration outside 0.04-10 s or another alignment is a 400; `tmp-N` ids renamed as the shots' are; a pair no longer adjacent dropped), a save of the cut without them keeps the ones still on an edit, and `GET` lists them. `transition_halves` cuts a half down to the media past the outgoing shot's out, before the incoming shot's in and half of a shot with a transition at its other edge ("insufficient media"). *The timeline* (`timeline.js`, `timeline-keys.js`): ⌘D / ⇧D / ⌘⇧D lay the 1 s default (an even number of frames) at the selected transition's edit, else the edit point selected by a click on its column (outlined), else the edit nearest the playhead, one undo entry for both; shortened to the media with a toast saying so, refused with the reason when a shot has none. The box sits on the cut (purple "Cross Dissolve" on V1, grey "Constant Power" on A1, hatched when a later trim ate its handle), under the trim lane's edge column; a click selects it, its ends drag its length (both move: centred; one undo entry), a double-click types it (`1.5` or `12f`), Del / ⌫ removes it. Duplicate moved from ⌘D to ⌘⌥D (⌘D is Premiere's); the key map lists both. *The Program* (`app.js` programMix, `grade.js` setMix): the cut's buffers still cut at the edit; two followers (`#pvTxIn`, `#pvTxOut`, like #pvV2) play the incoming shot's head before the edit and the outgoing shot's tail after it, started 0.35 s ahead so they are running when they show, at the render's weight x = (t − (C − h)) / 2h (exactly ½ on the edit's frame), playing or parked. The graded picture dissolves in the grade worker: the follower's frame drawn through its own shot's LUT and Lumetri over the live shot's graded frame at constant alpha, so a dissolve between graded shots mixes what the render mixes; the follower is shown at CSS opacity only while neither shot is graded (then that is exact), and grade.js tells the page when the canvas flips under it. A crossfade sets `rcAudio` levels: outgoing cos(πx/2), incoming sin(πx/2). *The export* (`assemble.py`): each part mixes in its half — the neighbour's handle through its own conform, LUT, Lumetri and speed, `blend` by the frame's time on the grid over the window's frames in that part, and its sound under an `aeval` cos/sin crossfade summed with `amix` — so parts stay one per shot, stream-copied, PCM until the one AAC; the log names each transition and any shortening. Not carried into the handle: the neighbour's Motion, Opacity and designed effects. New `app/tests/test_transitions.py` (13): Ctrl+D on a clicked cut makes a 1 s dissolve drawn on the cut and on disk, undo removes it; Shift+D, Ctrl+Shift+D (one undo), Delete; an end dragged +0.25 s of zoom makes it 1.5 s (one undo), `12f` typed makes it 0.5 s; insufficient media shortens (8 frames) or refuses, and a later trim hatches it; the export's frame on the edit is the mean of red and blue within ±10 % (25 % / 75 % a quarter either side, the shot alone outside), its length unchanged to the frame and the sound's to 30 ms; the crossfade measured by volumedetect windows (−3 dB on the edit, cos(3π/8) at +0.25 s, silence after the window); the Program's pixel on the edit and a quarter either side is the mix within ±10 % on ungraded shots (CSS) and with a graded shot (the grade worker), and playing through the window runs the follower; the server's validation.
- **Effect Controls for V2+ clips (redesign wave 9, lane V2EC; RENDER3's request).** A click on a clip on V2, V3… (`tl.state.osel`, no V1 shot selected) shows ITS Effect Controls: the head reads "V2 clip · <clip> · In – Out", the strip its still and name, and the panel Motion (Position, Scale, Rotation) and Opacity with their stopwatches, ◀ ◆ ▶ and lanes, timed from the clip's own first frame (`at`). Lumetri Color, Volume and Time Remapping stay V1's and the panel says so ("apply to V1 shots: a clip on V2+ renders as shot, picture only"): the render conforms an overlay without a grade, and it carries no sound. Every change writes `overlays[i].controls` (validated by `clean_controls` as before), is one undo entry (the overlays ride the undo snapshot), saves through the sequence's PUT (`tl.saveSeq`; the cut's autosave does not carry V2), and the Program's #pvV2 follows at once (`programSync` → `v2Look`); an edit with the playhead outside the clip parks it on the clip's first frame, as a V1 edit does. `app.js`: `selectedOverlay`, `buildOverlay` / `fillOverlay`, `renderInspector` keyed `ov:<id>` (a V2 edit or an undo replaces the clip's object, not the panel), `paintEcHead`'s clip head. `effects.js`: `cur()` finds an overlay by id (`tl.overlayById`) and falls back to the selected one, `span()` is `{at, dur}` for it, `preview()` parks and runs `programSync`, `saved()` after every commit. New test in `test_overlay_controls.py`: click the V2 clip → Motion + Opacity only and the note; type Opacity 50 → "undo: opacity", #pvV2 at 0.5, V1's controls untouched; the Scale stopwatch at 1 s in, then 50 at 2 s in → "undo: animate scale", "undo: scale", two diamonds, keys [[1,100],[2,50]], undo / redo of the key; on disk `{"opacity": 50, "keys": {"scale": [[1, 100], [2, 50]]}}`; the Program's screenshot at 2.5 s is the red/blue mix inside the half-size box and V1 outside; a V1 shot selected brings V1's panel back; the export of the EDL the board saved is the mix inside the box at 2.5 s, V1 outside, and the mix over the whole frame at 1.5 s (scale 100).

### Changed
- **The grade costs 2.5x less CPU for a balance, 1.2x less through a cube (redesign wave 9, lane V2EC; RENDER3's request).** `colour.lut_vf` was ~40 % of the 4K Match size sample: yuv → float RGB (spline chroma) → a 33³ tetrahedral `lut3d` → yuv, 172-179 ms of CPU per 4K frame. The cube's interpolation itself was half of that, and the auto's grade (and any hand balance) is a balance alone, which is per channel (gain × exposure, the shoulder, the lift on each of r, g, b). Now `colour.balance_only(res)` names such shots, and `lut_vf(cube, src_range, balance=)` draws them with one `lutrgb` (`balance_lutrgb`: three 1D tables ffmpeg builds once per part, exact to `apply_balance` at 16 bits; no `.cube` is written for them); a match or a look still goes through the cube's `lut3d`, both now on 16-bit planar RGB with bilinear chroma instead of float with spline. Measured on 20 4K Killington frames (CLIP_01 at 157 s, its own auto balance, the GoPro's bt709 tags), CPU per frame net of reading and writing: old 174.1 ms; balance 71.2 ms (×2.45), |ΔY| mean 0.27 (max 1), |ΔC| mean 0.24 (max 3), signed ΔY −0.06; cube 145.4 ms (×1.20), |ΔY| 0.26 (max 1), |ΔC| 0.24 (max 3). Exports against the old code: the 4K Match size sample (8 slices, 120 frames) |ΔY| mean 0.73 (signed −0.02; worst frame 1.31: x264 crf 18 re-deciding 4K detail), |ΔC| 0.34; the suite's fixture (testsrc2 + snow colour files) auto 0.42 / 0.45, auto + alpine 0.20 / 0.33, a hand balance 0.35 / 0.40 (|ΔY| / |ΔC| means, every frame ≤ 0.45). The 4K Match size sample on Killington (judge 6's work dir, 8 jobs), interleaved runs: old 30.7 s (93.3 CPU-s, load 12→20) and 28.6 s (85.6 CPU-s, load 16→24); new 20.9 s (66.5 CPU-s, load 10→16) and 24.6 s (68.6 CPU-s, load 24→26). `test_colour_render`'s auto render now asserts no `.cube` for a balance alone.

### Fixed
- **Two labels FXBIN's rename left behind.** Effect Controls' empty-stack link read "Design one in Effects ▸" although that tab is now "Designed Effects" (and "Effects" is Premiere's bin), and the keys sheet's Ctrl+D row still named Cross Dissolve though Ctrl+D / Shift+D now lay the default video / audio transition: the row reads "the default video · audio · both transitions, 1 s at the selected edit".
- **Escape closes the finished Export dialog, and Ctrl+M paints it in one frame (redesign wave 11, lane SMALL2; verdict 8 punch 8).** *Escape:* the done screen replaced the dialog's body under the focused Export button, so focus fell to `<body>`; there the timeline's keys (window, capture) took Escape as "clear the selection" and prevented it, and the dialog never heard its `cancel` (export.js:577 meant it to close). The done screen now focuses Close (Enter is Close, Premiere's default), and an Escape that lands outside the open dialog closes it on keyup. *Ctrl+M:* profiled on a 20-shot board, the key's 16 ms are `showModal`'s whole-page style pass (inertness, ~8 ms on 1.2k nodes; it grows with the board) plus the dialog's own paint, and a second ~8 ms style pass followed when `/api/export` landed and the settings were painted again. The shell is now built once; a second open paints the settings from the last answer in the same frame as `showModal`, and the fresh answer repaints them only if it changed (and only the figures, if a field was touched). Tests: `test_export_dialog.py` +2: Escape straight after `export-done` (no click first) closes it and the next Ctrl+M opens on its settings; Ctrl+M → dialog painted, median of 5 at the suite's load: 39 ms wall with the Playwright round trip (34-43), 37 ms in the page from the keydown's timestamp (gate ≤ 100 ms both).
- **The "building proxies" toast is said once per pass, by the server, on the Program (redesign wave 11, lane SMALL2; verdict 8 punch 7).** Judge 8 saw it on every page load for the hour Killington's proxies took, parked over the Timeline. *The cause:* CRAFT kept "already said" in the viewer's `localStorage` (`rc.proxyToast`), and every fresh browser starts without it: each of the judge's scripts launched its own Chromium (a new profile), as would another machine or a private window. The key was also the pass's proxy count, which a restarted proxy job resets to 0 (that read as a new pass). Now the server remembers it (`POST /api/proxies/toast`, `server.PROXY_TOAST`): the first board of a pass is told to say it, every other load of the same pass is told no; a pass ends when an ask finds the previews ready, and a clip newly missing its proxy (new footage) is a new pass even if no board saw the last one end. The toast sits inside the Program picture's bottom-left (`toast(msg, ms, { at })`), never over the Timeline's tracks; every other toast is where it was. Test: `test_proxy_toast_once.py` (2): one pass, the first board says it, three reloads and two brand-new browsers do not; the shown toast is inside `#pProgram` and meets no `#tl .tl-lane`, `.tl-track` or the Timeline panel; the pass's end and a new pass say it again once; the endpoint alone answers yes once per pass and yes again for new footage.
- **Labels fit their blocks on real footage: a name, a number or nothing, never "0…" or "CLI…" (redesign wave 11, lane SMALL2; verdict 8 punch 6).** Judge 8 still saw a ghost read `0…` at fit zoom on Killington's first proposal, and V1's blocks "CLI…"/"CL…". *The cause, for the ghosts:* the canvas measure was right about "05" (it agrees with the DOM to 0.01 px in the board's own stack); the band it measured against was not what it assumed. Every ghost carried an empty `.dur` span whose 5 px `margin-left` still took room, so a label that fitted by less than 5 px overflowed and CSS's ellipsis cut it. An empty badge now takes no room (`.dur:empty { display: none }`), the "!" is counted at its measured bold width, and the font is read from a real `.name` in the lane (style, weight, size and the board's family stack, as the browser resolves it), not assumed. *V1 had no fallback at all*: its label is now fitted the same way (`tl.fitLabel`, shared with the lanes through `tl.textWidth` / `tl.fontOf`): the clip's name when it fits beside the speed and sync badges, else its number ("02"), else nothing; the tooltip names it. Test: `test_label_fit.py` (2): a bin named as Killington's (CLIP_01 … CLIP_12) with the first proposal's 17 shot lengths (2:56) at 1440 — at fit zoom and zoomed in, no ghost's or V1 block's label band overflows (the browser's own `scrollWidth`), none ends in "…", each is a name, a two-digit number or empty, and the canvas width equals the DOM's to < 0.75 px. Before: the ghost band overflowed by 4 px with no text in it.
- **Two tests that flaked in the gate of `1e99342` wait for the page, not a proxy of it.** `test_floor_ui::test_a_clicked_verdict_drops_a_pending_batch_question` waited for the clicked verdict on disk, but the server writes it before the page reads the reply (`verdict()` marks the pick after its POST returns), so a ⇧X in that gap still counted the clicked pick ("reject 3 picks?" for 2); it now waits for `floor.state.queue` to hold the verdict. `test_perf_latency::test_the_grade_draws_in_a_worker…` (and the same wait in `test_perf_latency`'s latency test and `test_playback_resolution`) waited for `C.shots` and then read `segs[0]`, which can still be empty a moment longer since BOOT mounts before the junk marks; they now also wait for `segs.length`. Both passed alone; neither is a product bug.
- **`test_tx_playback` collects again.** It imported `_too_busy` from `test_perf_latency`, which PERFGATE removed in the same wave (the perf test now measures at any load); the import error stopped the whole suite in the lead's gate of `321ec31` (0 passed, 1 broken). The helper now lives in the dissolve test, with the same rule.
- **A whole-clip drop ends on the clip's frame grid (redesign wave 10, lane CRAFT; verdict 7 punch 9).** Dropping all of Killington's CLIP_05 (29.97) on a 59.94 grid gave 0–35.1851: 1054.5 of its frames, and the 29.97 export ran 16 ms shorter than `tl.total()`. The board was told the clip lasts its sound's 35.2 s (the sidecar's `duration_s`), floored only to the sequence's grid, after the lanes' drop had rounded it to 0.01 (35.19, past the end). Now `server.on_clip_grid` serves every clip's length as whole frames of its own rate, floor(d × fps) / fps (`/api/clips`, the project's clip records — which now carry `fps` — and an unheard clip's record; the rate from the facts already probed, never a probe inside a GET); `tl.insert`/`placeAt` keep a shot inside `clipEnd(clip)`, the media's end on the clip's own grid; the lanes' drop no longer rounds the range. Test: `test_whole_clip_grid.py` (2): the judge's 35.1851 s clip dragged from the Project panel onto a 59.94 sequence ends at 1054 frames of 29.97, and the 29.97 export's frame count / fps equals `tl.total()`.
- **The proposal state's small chrome holds at 1440×900 (redesign wave 10, lane CRAFT; verdict 7 punch 8).** *The Source transport:* at the 343 px a waiting proposal leaves the Source, the timecode overlapped Mark In by 26 px and Insert the duration; its buttons stood 1 px apart. They stand 4 px apart now, and below 440 px of transport the duration goes first (`@container`), so no two controls are closer than 4 px down to 340 px. *The ghosts' names:* measured on a canvas (no layout read), a ghost's label is the clip's name when it fits beside the speed badge and the notes' "!", else its number ("05" for CLIP_05), else nothing (the tooltip names it) — never "C…" (`tlLanes.ghostLabel`). *The "building proxies" toast* shows once per pass: a reload while the same pass runs (its count only grows; a status not yet landed counts as the same pass) stays quiet, a page that sees the previews ready ends the pass, the next pass says it again (per viewer, `localStorage` `rc.proxyToast`; without storage it says it every time, as before). Tests: `test_proposal_chrome.py` (3; each takes its waiting-proposal record away after it, or every later module's board opened on it).
- **Turning a stopwatch off over keys asks first, as Premiere does (redesign wave 10, lane CRAFT; verdict 7 punch 7).** One click on a keyed property's stopwatch threw its keys away without a word. Now Effect Controls opens a modal of the board's own (`#ecConfirm`, drawn like the Export dialog, never `window.confirm`): "This action will delete existing keyframes. Do you want to continue?" with the property and its key count, OK the default button. Cancel or Esc keeps every key and makes no undo entry; OK removes them as ONE undo entry (`stop animating <prop>`), the value at the playhead becoming the static value; one Ctrl+Z brings them all back. `effects.animate()` asks the same way; a locked track refuses at once instead of asking a question it would then refuse. Tests: `test_keyframe_safety.py` (3); `test_keyframes_ui`'s undo test clicks OK.
- **Playing through a Cross Dissolve no longer hitches: the transition's followers are never seeked while they play (redesign wave 10, lane TXHITCH; verdict 7 punch 4).** *The cause, measured* (new `app/tests/test_tx_playback.py` on a 4K 59.94 fixture, and the judge's CLIP_02 → CLIP_03 dissolve on Killington, both before the fix): `programMix` drove `#pvTxIn` / `#pvTxOut` with V2's `progFollow` — the start was `currentTime = t; play()` (a seek on the frame it starts), the follower then trailed the film by its start latency, and every re-seek past the 0.08 s slack took longer than the slack, so it re-seeked every 110-150 ms through its whole half: 8-11 follower seeks per play on Killington (6 + 5 on the fixture), each a decode from the keyframe; each follower presented 4-8 frames in its half instead of ~15 and held **133-275 ms** (Killington, load 12.8-15.4) / **160-240 ms** (fixture, load 11-20); it was seeking (`readyState` 1) at the moment its half began. The judge's 132 ms was this, seen through the union of all buffers' frames, which hides most of it (52-84 ms by that metric both before and after). Also the outgoing follower parked on the NEXT window during the first half of the current one, and came back with a seek. *The fix* (`app.js`): `txFollow` — a follower parked TX_PRE (0.25 s; the outgoing one TX_PRE_OUT, 0.2 s) ahead of its half starts with a bare `play()` from its park (its first frame decoded there) and is steered by its rate (±60 %, steps of 5 %, a 1/60 s dead band, drift taken out over ~0.3 s of film); only a follower more than 0.5 s off (a shuttle, a stall) is seeked, once, aimed where the film will be when the seek lands. The outgoing follower waits for the window it is in. Both shots' LUTs are prefetched once per window (`grade.prefetch`) before it begins. Leads measured on Killington, load 16-19, 3 runs each: 0.35 / 0.35 s held 67-100 ms, 0.25 / 0.2 s <= 67 ms (a fourth decoder around the edit costs). `grade.js`: `setMix` no longer draws on every call while playing (programMix calls it every animation frame: that was a second graded frame per video frame, two uploads each, through the whole window); on Playback Resolution Auto a playing mix is drawn about 454 px wide (PLAY_W / √2: the two uploads cost one frame at 640; measured in the worker, the upload is 10-50 ms of each frame at load 15, the shading 0-10). `server.py`: the range proxy covers the handles — `transition_handles` widens a V1 shot's span by the half past its out and by the half + `RANGE_TX_LEAD_S` (0.35 s) before its in (at its speed), then RANGE_PAD_S on top as before. *Re-keying:* `range_key` hashes the merged ranges, so a transition that reaches past what the file holds (e.g. a 4 s dissolve: 2 s of handle past a 1 s pad) is a new key, `ranges_cover` says the old file no longer covers and the walk builds the new one; a transition removed or shortened, or one inside the old pad (a 2.6 s one), is still covered and the file stays. *After* (fixture, 0 burners, load 16-25: the box held 12-29 from other loops the whole session): no follower seek in any of ~25 plays, each follower running (not paused, not seeking, its frame decoded) when its half began, 15-24 / 9-19 frames per half, follower holes mostly 33-67 ms (worst 100-117 at load 17-18), live buffer 33-100; graded canvas (worker draws) 67-205 against a plain graded cut's 73-80 on the same page. Killington (6 plays, load 12.4-14.8): followers 33-83 ms (11 of 12 halves <= 67), the live shot in the window 50-83 (5 of 6 <= 67), 0 follower seeks; the graded canvas 82-178 ms — a plain cut's canvas on Killington holds 89-228 ms at the same load (the perf test reads buffer frames, never the canvas). The Program on the edit's frame is still the mean of both shots: (127.5, 18.5, 127.2) against (127.9, 18.8, 126.9) ungraded and (122.6, 19.7, 117.8) against (122.4, 19.5, 117.6) graded. New `app/tests/test_tx_playback.py` (5; reuses test_perf_latency's burners, load, port and GAP_LIMIT_MS): an ungraded and a graded play across the fixture's 1 s dissolve (parked, rolled, no follower seek in the window, each follower running when its half begins, the live shot's and each drawn follower's frames <= 67 ms, the graded canvas no worse than a plain cut's + a frame, s2's LUT fetched before the window) — on a box already past twice its cores a gap over the limit is a skip that names the load and the numbers, the structural checks still assert; the midpoint is the mean of both shots (±10 %) ungraded and graded; the range proxy's widening and re-keying.
- **The board shows its cut at once after a proxy lands, and says what it is still checking (redesign wave 10, lane BOOT; verdict 7 blocker 3, punch 2).** A reload after a background proxy landed showed an empty Timeline and Program for 27-35 s with no word: boot awaited `GET /api/junk`, and that GET measured the new 1280 proxy's colour inline. *Boot* (`app/static/app.js`): the timeline and the Program mount first; `fetchJunk()` is asked for and painted when it lands (`junkLanded`: the bin's list and line). Until the marks are in, the Selects tab says "checking for junk…", and while any clip's colour is pending it says "checking colour… N clips" and reads `/api/junk` again every 2 s until none is. *Server* (`app/server.py`): `api_junk` and `junk_assess` never measure; a row carries `colour: measured | pending | none` and the body a `pending` count. Colour is measured in `ensure_proxies` right after each proxy lands (the index's proxy stage already did), and anything still missing goes to a new colour queue (`queue_colour`, one clip at a time in a background `procscope` scope: nice 19, SIGSTOPped while the Program plays, killed with the server; it skips a clip another thread is measuring). *Measured* (`app/tests/test_boot_wait.py`, `measure_colour` patched to sleep 10 s, a fresh page at load 15.6 on 6 cores): the cut (`#tl .blk` + the Program's frame) in **0.75-0.83 s**, the junk marks at 10.4 s, `GET /api/junk` 0.07 s; the base `875c8db` took **11.9 s** to show the cut on the same fixture. Unpatched, with a real 10-minute 1280×720 proxy just landed and no colour file (`~/roughcut-loop/lane-boot/demo_landed.py`): the base showed the cut after **28.9 s** (`GET /api/junk` 27.9 s, no wait text), this branch after **0.84 s** with "checking colour… 1 clip" in the bin; the niced background measurement then took 155 s at load 15 on 6 cores and the line cleared. *Gate* (`tools/gate.py` `boot_check`): a new line "board shows its cut N s after a proxy lands", green at ≤ 2 s from a passed test, read from `$ROUGHCUT_BOOT_JSON` (run alone when the suite did not write one).
- **A keyed property that never changes value renders; a failed export names its shot, with Go to shot and Show log (redesign wave 10, lane EXPORTOK; verdict 7 blocker 2, punch 1).** *Root cause* (`research/tools/assemble.py`): one click on a stopwatch stores ONE key (Premiere's "start animating"); for Opacity, `opacity_commands` wrote a `sendcmd` file only for the frames where the value changes, so a value that never changed left the file empty, `controls_graph` (V1) and `overlay_controls_graph` (V2+, alpha) still put `sendcmd=f=<empty file>` in the graph, and ffmpeg refused it ("Error parsing global options: Invalid argument"): every export with such a shot failed. *The fix:* `held_controls(controls, a, b)` folds every keyed property whose value is the same at every instant of the part's window — one key, keys of equal value, keys that all lie before or after the part (a razor half, an In/Out range) — into its static control (`held_value` is exact for piecewise-linear keys: both ends, every key inside, both sides of a step), so it renders exactly as the static control with no per-frame warp or command file; `controls_graph` folds over the part's frames, `overlay_controls_graph` over the piece's (from its own `t0`), `encode_part` over the part's sound for Volume. Behind it, `opacity_commands` never writes an empty file: it returns `(lutyuv, changes)` and the callers draw the `lutyuv` alone when nothing changes. Audited for the one-key and constant cases: position x/y, scale, rotation (the warp's `lerp_expr` was already valid, now the exact static path), opacity (V1 blend toward black and V2+ alpha: the bug), volume_db (`volume` was valid, now the static gain). *The failure, said* (`assemble.py` `say_failed_shot` / `ffmpeg_reason`, `app/server.py` `failed_shot` / `failure_detail` / `log_tail`, `app/static/export.js` `paintFailed` / `goToShot`, `export.css`): a part that fails prints `failed shot: {index, id, clip, in, out, film_s, overlays, reason}` with ffmpeg's first specific line (the quoted filter graph cut out, not the generic "Error parsing global options"); the job keeps `failed_shot` and a `log_tail` (Python's traceback frames left out) and says "shot 3 (CLIP_C.MP4 1.00–5.00 s) did not render: …" instead of "the render failed — see the log". The Export dialog shows a red box over its settings (which keep the name, folder, preset and range it was asked with): "Export failed · Shot 3 · CLIP_C.MP4 · 00:00:08:00 did not render." (the timecode where the failing part starts in the sequence, In/Out ranges included), ffmpeg's word, **Go to shot** (closes the dialog, selects the shot on V1 by id, or the shot under that time, and parks the playhead there) and **Show log** (the log's last 40 lines, in the dialog). *Tests:* `test_render_keyframes.py` +25: on V1 one key / two equal keys / keys held past the shot's end for Opacity (60 %, 100 %, 0 %), Scale, Rotation, Position x and y, Scale + Opacity together, each the static control's picture within 1 code value at 0.1 / 1.0 / 1.9 s and not the plain shot, Opacity 60 % of the luma; Volume one key / equal / held, the static loudness within 0.5 dB and 15+ dB under the plain shot; on V2+ over V1 (blue over red) one key / equal / held / one key at 100 / Scale + Opacity / Scale, each the static overlay within 1.5 code values, a mix and never a blend toward black, V1 outside a half-size picture; `held_controls` and `opacity_commands` unit cases (the EDL's own dict untouched, a ramp or a step inside the window stays keyed, no file when nothing changes). All red on `273b1f1` ("Invalid argument"). `test_export_dialog.py` +1: an assemble whose CLIP_C parts fail in ffmpeg (`server.TOOLS` pointed at a stand-in around the real `assemble.py`): the dialog names shot 3, CLIP_C.MP4, `tl.tc(8)` and "Invalid argument"; the job's `failed_shot` and detail; Show log shows the tail with no traceback and Hide log hides it; the settings keep "broken" / "failed"; Go to shot closes the dialog, `tl.state.sel` is shot 3 and the playhead 8.0 s. *Journey:* new step 18 `opacity-key` (one click on Opacity's stopwatch on shot 1: one key at 100 %, on disk), so the export step that follows exports it; the export step now stops at once on a failed export with the dialog's words (the red box, or an older board's "The export failed" note); ffprobe checks the keyed shot's frame is not dark. Red on `273b1f1` (the export fails), green here.
- **The export's review copy dies with the server (redesign wave 10, lane EXPORTOK; verdict 7 punch 10).** The 720p copy the versions list makes of a finished film (`build_review`) was the one ffmpeg the server started outside every `procscope` scope: in the server's own process group, no PR_SET_PDEATHSIG, not ended by the shutdown hook, so a SIGTERMed server left it writing into `--work` (the judge saw it outlive the server by seconds). It now runs in `review_scope()` (`procscope.Scope("review copies")`: its own session, `setpriv --pdeathsig KILL`, killed by `_shutdown_children` → `procscope.kill_all`; fresh after a kill; not background, so a render's own copy is neither niced nor paused), and its command is `review_cmd(src, tmp)`. New `test_yield.py::test_a_sigtermed_server_takes_its_review_copy_with_it`: a real server process (`server.main`) with a finished 20 s film in its renders folder, its review copy started by `GET /api/renders` and slowed to real time (`-re`), SIGTERMed while that ffmpeg runs: 1 s later no ffmpeg on the box names a path under its `--work`, the server has exited and no review copy was left. Red on `273b1f1` (the ffmpeg was still writing), green here.
- **The keys sheet (?) fits on a laptop again.** TRANSITIONS' new row (Ctrl+D / Shift+D / Ctrl+Shift+D) read four lines, and with it the left column (Play + Trim and cut) ran 97 px past the panel at 1280×900, cutting its last rows (`test_the_keys_are_an_overlay_on_question_mark`, red in the gate of `64687de`). The row now reads one line ("1 s Cross Dissolve · audio crossfade · both, at the selected edit": the box's own handles say the rest), the panel is 1120 px wide (was 900, so the descriptions wrap less), 92vh tall (was 86vh), with 2 px row padding (was 3). Measured: no overflow, two columns, at 1280×900, 1366×768, 1100×800 and 1920×1080.
- **`test_the_gate_reads_the_journeys_line` expects the gate's measurement suffix.** Since SAVESAFE the gate appends " · slowest request …" to the journey's line; the test compared the whole line to journey.log's and failed in the gate of `64687de`. It now splits the suffix off, requires it to say "not measured" (an empty snapshot has no server), and compares the rest.
- **`test_l_plays_the_cut_and_stacks_the_rate_and_k_pauses` counts the player's buffers instead of assuming two.** It asserted `[2, 2]` for the buffers' rates, but PLAY3 made the player three buffers, so it failed on the branch itself (TRANSITIONS found it on an unmodified base). It now requires every buffer, however many, at 2× in both `playbackRate` and `defaultPlaybackRate`.
- **Effect Controls refuse edits on a locked track, as the timeline does (redesign wave 9, lead; V2EC's report).** A locked V1 refused every cut edit on the timeline but not a Motion, Opacity or Lumetri change in the panel; a locked V2 did not stop its clips' controls either. Every Effect Controls edit (a value typed, dragged or stepped, a slider, a stopwatch, a keyframe added, removed or moved, a reset, the fx stack's on/off and order) now opens through `effects.js` `opens()`, which asks `tl.refuseIfLocked(track)` first: the clip's own track for a V2+ clip (V2 when it has no `track` field), A1 for a V1 shot's Volume, V1 for the rest. A refused edit shows the timeline's toast ("V1 is locked — unlock it in its track header to edit") and makes no undo entry and no change. New test `test_a_locked_track_refuses_effect_controls`.
- **`test_off_is_the_identity_byte_for_byte` pins the CPU decoder.** Since wave 8 a render decodes on the GPU when it can and falls back to the CPU when the decoder is busy; the two agree to ~1 code value, not to the byte, so the test failed once while a GPU export ran beside it. It is about the off switch, so both renders now run with `ROUGHCUT_GPU=0`.
- **`test_b_an_edit_waiting_for_its_autosave_is_flushed_on_pagehide` no longer races itself.** It required a keepalive flush every time, but beforeunload starts the waiting save first; under load that save sometimes landed before pagehide, leaving nothing pending, so correctly no flush went (the edit was on disk both times SAVESAFE and YIELD saw it fail). The test now requires the edit on disk and checks the flush's headers only when one was sent; the flush delivering an edit by itself stays pinned by the held-save test.

### Changed
- **The app's own background work yields to playback; whole proxies on the GPU; the filmstrip no longer starves the board's requests; encodes die with the server (redesign wave 8, lane YIELD; verdict 6 punch 3, SAVESAFE's and RANGEPROXY's requests).** (1) Every background encode (the index's scope: whole proxies, close looks; the media walk's scope: previews, range proxies, walk posters; the analysis job's proxies) is a `procscope.Scope(background=True)`: its ffmpeg starts at nice 19 (`nice -n 19` in front, so every ffmpeg thread has it; a close look's own ffmpeg is reniced by the yield thread). New `POST /api/playing {on, tab}`: while any board's Program (or Source monitor) plays, a yield thread (every 0.2 s) SIGSTOPs those ffmpegs and SIGCONTs them on pause, or `PLAY_LAPSE_S` (5 s) after the tab's last beat, so a closed tab or a crashed browser can never leave them stopped. Never paused: the export (its own group, no scope), a poster the board asked for (`poster_scope`, not background), a preview a monitor asked for (`start_preview` now builds in a foreground `ask_scope`, normal priority), and a background build somebody is waiting on (`media_waiting(key)`: a monitor holding /media/proxy for its preview, a card waiting on the walk's poster, an asked preview waiting for a `PREVIEW_SLOTS` slot the walk's paused builds hold). A Stop (`scope.kill`) sends SIGCONT after SIGTERM, so a paused encode dies at once. Client: one self-contained notifier at the end of `app/static/app.js` reads `player.playing` (and `#srcVideo`) every 200 ms: `on` at once and every 2 s while playing, `off` after 0.8 s stopped (a J/K/L burst does not flap the encodes), `credentials: 'omit'` + keepalive like the save, and `off` on pagehide. (2) Whole-clip proxies on the GPU (`proxy_gpu_args`, `PROXY_V` v4; v3 is PLAY3's 0.5 s keyframes, which the GPU path keeps via `PROXY_GOP_S`): NVDEC → `fps` → `scale_cuda` → NVENC (the range proxy's encoder settings), the orient rule as gpu-turn (matrix overridden, the turn done on the 1280 frame on the CPU); an HDR / not-8-bit source or no NVENC takes today's CPU path, and a GPU failure falls back to it. **Measured on Killington `cleanduckin.MP4`** (318 s, 4K H.264 29.97, −180°; read-only footage, own output; box at load 17-22 from other lanes): GPU **75.4 s wall, 25.2 s CPU** (upright: 44.9 dB PSNR against the autorotated source, 10.7 against the unturned); the CPU path (today's, x264 veryfast) **329.5 s wall, 730.2 s CPU**: 4.4× faster and 29× less CPU, the CPU left to the player (the GPU file is bigger, 7.0 against 2.6 Mb/s, as the range proxy's). (3) The filmstrip tiles (V1's `paintStrip` tiles after the head, the ghost lane's after the head) load through `tl.posterLoad`, a queue in timeline.js: at most 2 in flight, started only within the timeline's view ± 1250 px (an IntersectionObserver on `.tl-view`), a frame already loaded this session set at once; heads and every other picture load as before. (4) Orphans: an ffmpeg started in any scope runs under `setpriv --pdeathsig KILL` (PR_SET_PDEATHSIG; its parent is the thread that waits on it), so a SIGKILLed server takes it down; a clean shutdown (`app.router.on_shutdown`) resumes paused encodes, kills the export's group and every scope's groups (`procscope.kill_all`). New `app/tests/test_yield.py` (5): a mocked 60 s index "proxy" encode is nice 19 and the export is not; play → every background ffmpeg in state T within 1 s (measured 141-472 ms) while the export runs, held through 7 s of heartbeats; an asked-for preview runs at nice 0 through play; a waited-on walk build resumes while the rest stay stopped; pause → running in ~150 ms; a lapsed heartbeat resumes them at ~5 s; the index's × still stops a paused encode at once. In Chromium `player.playing` reaches the server within a beat, the heartbeat holds past the lapse, a 300 ms pause sends no `off`, a closed page lets the work run. A SIGKILLed parent's scoped ffmpeg dies within 3 s; the shutdown hook kills a paused encode in < 2 s. With ≥ 8 filmstrip tiles waiting on 3 s poster builds, a credentialed `GET /api/firstpass` answers in 7-15 ms (≤ 2 builds at once from the strip), and every tile in view gets its frame.

### Docs
- **LOOP.md: RENDER3 merged; colour-LUT cost and V2 Effect Controls queued.**

### Docs
- **LOOP.md: RANGEPROXY and PLAY3 merged; the perf measurement still waits on a quiet box.**

### Added
- **A parked Program buffer swaps the preview for the range or whole proxy (wave 8, lane PLAY3; RANGEPROXY's request).** `/media/proxy/<stem>.mp4` is a redirect, so a buffer that opened a clip as the 360p preview kept it for as long as it held that clip, every shot of it, after the 1280 range proxy or the whole proxy had landed. A buffer now notes what `/media/status` said it was served when it opened the URL (`_kind`: preview < range < proxy / original), and at a park (`armNext`, `cueAt`, `pauseCut`; never the buffer on screen while playing, never one prerolling; at most one ask per buffer every 3 s) re-opens it at the same clip time when the server serves a better kind. New test in `test_perf_latency.py`: parked on shot 1 of the 4K fixture, the next (0.4 s, file B) and the one after (file C) sit in two buffers of their own, each parked <= 0.4 s ahead of its in-point; a live buffer marked `preview` is re-opened once /media/status says `proxy`, at the same time (+-0.05 s), and a later park leaves it alone. `test_latency_under_load` also asserts that both cuts went to the buffer parked for them (`cut_log[].parked`, two different buffers).

### Docs
- **LOOP.md: SAVESAFE's root cause, the poster-starvation follow-up, per-lane scratch dirs.**

### Fixed
- **A save never vanishes on a reload (redesign wave 8, lane SAVESAFE; verdict 6 punch 1).** Verdict 6: on the first Accept after a real first pass the save had not answered 3 s later, and a reload ~5 s after the Accept left an empty sequence, the EDL never written and the ask never answered. **The cause, measured:** the Accept's PUT never left the browser. Chrome runs six connections per host, and right after a pass they were all held by `/media/poster` requests (ghost and filmstrip frames nobody had built, which the server makes three ffmpeg at a time, 1.6-6.6 s each on this box during the suite); the PUT queued behind them in the page and the reload cancelled it unsent. Reproduced with the suite's footage and 2 s poster builds: a 17-shot Accept's PUT had Playwright `requestStart` **11 480 ms** after it was issued and was served in **11 ms** once sent; the server-side suspects (the PUT's own work, `firstpass_state`, the journal, the event loop) were not it: the handler is milliseconds and the new loop watcher saw no hold. **Fixes:** (1) every `PUT /api/project` (the cut's `saveOnce`, the tracks' `saveSeqNow`, the keepalive flush) and `POST /api/asks/answer` go with `credentials: 'omit'`, which Chrome keeps in a separate connection group from the page's images and video, so a save is never queued behind posters (same reproduction: `requestStart` 1.5 ms, answered in 26 ms; control with credentialed PUTs: 2.8 s). (2) The unload protocol (`app.js`): while an edit waits for its autosave, a save is in flight, the last one failed, the tracks are unsaved or an Accept's answer has not gone (`savePending()`), `beforeunload` asks (and starts the waiting save), and `pagehide` / `visibilitychange: hidden` send the latest cut + tracks + the pending answer as ONE `fetch(…, {keepalive: true})` PUT, which the browser delivers after the page is gone. **A body over 60 000 bytes cannot ride a keepalive (the browser's 64 KB budget for keepalive bodies): then the `beforeunload` prompt is the only guard.** (3) Ordering on the server: every board PUT carries `x-roughcut-save: <page>:<n>` (`tl.saveHeaders`, n counts up per body); a PUT older than one already applied for its page is answered `{stale: true}` and writes nothing, so a keepalive flush landing while an earlier PUT of the page is still held is never written over; `mint_board_ids` remembers the ids it minted per page, so the shot both carried under `tmp-N` gets one id (the stale reply names it). A PUT without the tag is applied as before. A save body may carry `answer` (`answer_ask`, the same work as `POST /api/asks/answer`), applied after the cut is written. (4) Read your own save: `GET /` and `GET /api/project` wait (≤ 5 s) while a `PUT /api/project` is being received or applied, so a reload that sends its flush and asks for the page at once is served the flushed cut. (5) Request timing: a pure-ASGI middleware times every request to its first byte; any over 500 ms is printed (`!! slow request: 2355 ms GET /media/poster/CLIP_A.v2.jpg (media_poster)`) and kept (the last 50), with the slowest overall and slowest `/api/` request, at read-only `GET /api/debug/slow`; a watcher records the event loop held ≥ 250 ms. Test hook: `server.PUT_DELAY_S` / `ROUGHCUT_TEST_PUT_DELAY_S` holds every PUT after its body is read (`server.put_delay(request)` for a per-request hold). (6) `tools/journey.py` saves `/api/debug/slow` into journey.json (`{error: "404: …"}` for a server that predates it), and **`tools/gate.py`'s journey line ends "· slowest request N s METHOD path" (the slowest `/api/` request, to first byte), red over 1 s** (media builds are printed in the journey log but not gated: a poster is an ffmpeg run). New `app/tests/test_save_reload.py` (5): (a) PUTs held 3 s, Accept, reload at 1 s → `beforeunload` fired, the accepted cut on disk with server ids, the ask answered, the reloaded board shows it; (b) an insert inside the 700 ms autosave wait, then navigate away → a keepalive flush (`x-roughcut-flush`) reached the server and the shot is on disk under a minted id; a PUT held 2.5 s while the flush lands never writes over it (4 shots, one id each); the tag ordering at the API; (c) after a mocked first pass with the index still writing (close looks / proxies 4 s each) and 8 slow posters holding the page's connections, the Accept's PUT answers ≤ 1 s after the click with `requestStart` < 500 ms (fails at 2.8 s with credentialed PUTs).

### Added
- **A third Program buffer, proxies with a keyframe every 0.5 s, and a 4K perf fixture (redesign wave 8, lane PLAY3; verdict 6 punch 2).** *The player:* `player.vids` is the markup's `#player video.pvbuf` list (pv0-pv2), not a fixed pair. Which buffer holds which upcoming shot is recorded on it (`_for`, set by `armNext`) and found by `bufFor(seg)` (the one armed for it, else one with its file open not holding another upcoming shot, else an empty one, else the least recently used); `armUpcoming(i)` parks the next shot and, when that one is shorter than 1 s, the shot after it in the third buffer once the first park has landed, so a 0.4 s shot between two other files no longer leaves the second file to be opened and seeked inside the short shot (the verdict's 200-600 ms frozen hole on Killington: the preroll never ran, `rolled: false`). `advance()` takes the buffer armed for the shot (not `1 - cur`), `preroll()` the buffer holding the next shot, `pickBuffer()` any buffer with the clip open; `armNext` leaves a buffer that is already prerolling for the same shot alone and keeps a park up to PREROLL_S ahead instead of seeking again. `player.cutLog` records `parked` and `buf`; `upcomingVideo(k)` names the buffer holding shot idx+k. grade.js, fx.js and effects.js read the buffer list from the markup (`video.pvbuf`) instead of `#pv0/#pv1`; programSync hides `player.vids`. *Proxies:* `PROXY_GOP_S` 1.0 → 0.5 and `PROXY_V` v2 → v3, so an existing work dir rebuilds its 1280 proxies once (the old `<work>/proxies/<bin>/v2/` can be deleted by hand). Measured on a 4K-sourced proxy: +17 % bytes (2.29 → 2.69 Mbps), a random seek decodes 7.5 frames on average instead of 15 (1280 decode ≈ 2.1 ms CPU a frame single-threaded: ~16 ms instead of ~31, worst 31 instead of 62). *The fixture:* `test_perf_latency` now builds three 3840×2160 59.94 H.264 sources with a 1 s GOP (testsrc2 with grain, so the proxy runs at a real Killington proxy's ~2.3 Mbps), proxied by the real `ensure_proxies` → `build_proxy`, cut A 1-4 / B 1-1.4 (0.4 s) / C 5-7 / A 5-7, and asserts the proxies' keyframes ≤ 0.5 s apart; it reports `mid_gap_ms` (the longest hole inside a shot) beside the cut gaps. Before/after on that fixture, `ROUGHCUT_PERF_BURNERS=0` (the box was at 4.8-8x its 6 cores from other lanes' gates, so the limits did not hold in any variant): base (two buffers, 1 s GOP), load 38-42, 3 runs: cut out of the 0.4 s shot **333-450 ms, `rolled: false`**, held 344-405 ms, mid-shot 83-117 ms, 17-20 fps, seeks median 266-299 ms (max 411); three buffers, 1 s GOP, load 36-48: cut out of the short shot **50-67 ms, rolled at both cuts**, held 0-1 ms, mid-shot 100-133 ms, 19-24 fps, seeks median 200-282; three buffers + 0.5 s GOP, load 29-32: cut out of the short shot **17-100 ms, rolled at both cuts**, mid-shot 100-150 ms, 20-23 fps, seeks median 136-172 ms (max 278).
- **Exports on the GPU and in parallel; V2+ clips take their own Effect Controls (redesign wave 8, lane RENDER3; verdict 6 punch 7 and punch 8).** *Speed* (`research/tools/assemble.py`): most of a Quick export was not the encoder but decoding 4K GoPro and scaling it to 1080p on the CPU (6 s of CLIP_01 at load 25: 25.3 s in software, 2.9 s with NVDEC + `scale_cuda`). A part's source now decodes and scales on the GPU when it can (`gpu_input`: H.264/HEVC 4:2:0 8/10-bit, no HDR to tone map, rotation 0 or 180, CUDA present), with what the CPU path did implicitly stated (ffmpeg skips autorotate on hardware frames, so a 180° camera gets `hflip,vflip`; a full-range source no LUT touches goes to limited by `FULL_TO_LIMITED` and is re-tagged, or the next filter converts it again: `overlay` did, 4.7 code values darker). Quick and YouTube encode with `h264_nvenc` (p5, hq, VBR CQ, 250-frame GOP, 3 B-frames) at the CQ that measured no worse than the x264 setting it replaces against a lossless conform of two Killington clips (SSIM / PSNR): Quick cq 27 = 0.984 / 43.4 dB at 12-17 Mbps against veryfast crf 20's 0.982 / 42.7 dB at 10-14; YouTube cq 25 = 0.987 / 44.6-45.1 dB against medium crf 18's 0.987 / 44.7 dB at +5-10 % size. Match Sequence and Master stay x264 (the quality presets; their decode still goes to the GPU, and a Match sample came out byte-identical). Parts encode in parallel, half the cores at a time (`--jobs`, `ROUGHCUT_RENDER_JOBS`); their sound stays PCM (one AAC per film). Robust: NVENC and CUDA are probed once (`ROUGHCUT_GPU=0` turns both off); a part whose GPU decode fails is cut again on the CPU; NVENC failing twice re-cuts the whole film with x264 (one film, one encoder: the join stream-copies the parts). The log's profile line names the encoder. Measured, the judge's 3:58 Killington Quick 1080p (21 shots, a V2 clip, 59.94): **1420.7 s (5.97× real time) → 426.8 s (1.79×)**, both at load median ~29 (19-53) on 6 cores, 14265 frames, the same length to the µs, clean decode; frames within 1 code value of the CPU path's mean. *The size sample* (`app/server.py`): its 8 slices encode all at once (`--jobs`): Killington at load ~25, Quick 43 → 12 s, Match 4K 80 → 30 s; still 2 s of output. *Overlay controls:* a V2+ clip's `controls` (position, scale, rotation, opacity, and keys, timed from its own first frame) are kept by a save (`apply_sequence`, by V1's `clean_controls`) and composited OVER what is under it with alpha (`overlay_controls_graph`: yuva420p, transparent outside the moved picture, Opacity scaling alpha; keyed motion through the same perspective warp with a transparent border, keyed Opacity by `sendcmd` on alpha), not blended toward black as V1's are. The Program's #pvV2 takes the same as CSS transform / opacity (`v2Look` in `programSync`, keys evaluated). New `app/tests/test_overlay_controls.py` (9): a V2 clip at Opacity 50 % / Scale 50 % over V1 is the mix of the two within ±10 % in the middle and V1 outside the half-size box, in the export and in the Program's pixels; keys on an overlay run from its first frame; rotation/position; the server keeps and validates an overlay's controls; the GPU path (decode, scale, NVENC) writes what the CPU path does within 3 code values at the same frame count.
- **A test for verdict 6 punch 10b: double-clicking a Selects pick opens it in Source with its In and Out on the clip's own frames.** `test_double_clicking_a_pick_marks_its_in_and_out_on_the_clips_own_frames` (test_picks_ui) serves the pass's real picks with one moved off the 24 fps grid (CLIP_B 6.53-8.97) and double-clicks its row twice, plainly and with the bin's rows rebuilt between the two clicks: `rcSource.inPoint/outPoint` = 6.5417 / 8.9583 (= `dock.source.snap`), not auto, the Source titled CLIP_B and parked on the In, its range shaded. It passes on the code as it is (app.js `openPickInSource`, unchanged since `54c275f`): the judge's `rcSource.inPoint: null` on the real 12-clip bin was not reproduced on the fixture, so no code changed for it.
- **Playback Resolution is a visible setting on the Program monitor, as in Premiere (verdict 6 punch 10a).** A small menu on the Program's transport, beside the duration (`#playRes`: Auto / Full / 1/2 / 1/4), sets how big the graded picture is drawn while it plays: Auto is the old rule and the default (at most 640 px wide), Full the monitor's own size, 1/2 and 1/4 of it; paused is always full. Kept per browser (`localStorage roughcut.playRes`); `grade.playRes` reads and sets it (the worker path unchanged: grade.js sizes the frame it sends). The judge: "a hidden constant: at 1920 the 640-px picture is stretched to 840 px while playing". New `test_playback_resolution.py` (1920x1080, 1280x720 proxies, a hand balance on both shots): Auto 640 while playing, Full 840 = the monitor's picture (840.9), 1/4 210, paused full, the choice survives a reload, the setter moves the menu. test_budget: one control more on every board screen, measured, no headroom (/ 50 → 51, / shot 65 → 66, / film 49 → 50, / fx 53 → 54, / jobs 51 → 52; words unchanged).

### Changed
- **The perf gate cannot pass by skipping (verdict 6 punch 9).** `test_perf_latency::test_latency_under_load` writes `$ROUGHCUT_PERF_JSON` through a new `perf_record` fixture (its outcome passed / failed / skipped, whether the skip was forced, the reason, the load at setup, when the burners started and stopped and at teardown, the burners, every measured number), and `ROUGHCUT_PERF_FORCE_SKIP=1` skips it on purpose. `tools/gate.py` names the perf test on its own line under the suite with the load it ran at; a skip (load-aware or forced) or no record is retried once after the rest of the suite, once the load is under 2x the cores (up to `ROUGHCUT_PERF_RETRY_WAIT`, 300 s); a retry that skips again makes the suite and the run **AMBER** (exit 2), not GREEN; a retry that fails is broken. `ROUGHCUT_GATE_SUITE` narrows the suite for checking the gate itself, and a narrowed suite is AMBER too.

### Added
- **Range proxies: the cut's shots play from a 1280 source minutes after open, not after ~37 min of whole-clip proxies (wave 7, lane RANGEPROXY).** PLAYBACK measured the cut's 9 Killington clips at ~37 min of one proxy worker (1.53 s of encode per second of 59.94 HEVC, the whole of each 4K master decoded on the CPU) while the Program played the soft 360p preview. Now every clip the cut plays (V1, then V2+, then a waiting proposal's shots; A3+ is sound only) gets a **range proxy** first: one 1280 file on the clip's own timeline (starts at 0, lasts the clip, so every clip-time seek the player makes lands as it would in the proxy, with no client change) whose frames inside the cut's ranges, each shot's [in − 1 s, out + 1 s] merged where they come within 2 s (at most 8 per clip), are decoded from the original, and whose frames outside them are the 360p preview's scaled up (never worse than what played before). Only the ranges touch the master. Profile: the proxy's width cap, its rate rule (59.94 → 29.97, the same `fps` filter, so a range frame at clip time t is the whole proxy's frame at t), the orient rule (matrix overridden, the turn done explicitly as `gpu-turn`), a keyframe every second. On a GPU it is all on the card (NVDEC → `scale_cuda` → `overlay_cuda` → NVENC, ~3-7 s of CPU a clip); an HDR / 10-bit source or no GPU takes the CPU path with the proxy's filters (x264 veryfast without a GPU). Each range's frames are put back at their own times with `setpts=PTS+round(a/TB)`: a float sum truncates a tick short and every 59.94 frame half-way between two 29.97 slots then lands a source frame early (found in the tests, which go red with it). Cache: `<proxy_dir>/ranges/<stem>.<key>.mp4` + `.json`, key = hash(RANGE_V, PROXY_V, PICTURE_V, orient, source size + mtime, ranges). `/media/proxy/<stem>.mp4` 307s to the newest current one (`x-roughcut-media: range`) after the whole proxy and a playable original, before the preview; new `/media/range/<name>` serves it; `/media/status` adds `range`, `ranges` and `cut_sharp` (are all of this clip's shots in the cut sharp now). The media walk (GPU boxes) does, per cut clip in film order (a cut clip the human just opened first), its preview then its range proxy, then the rest of the bin's previews, and then stays, looking at the cut every 2 s: a trim past a range (the old file keeps playing, soft past the range) or a new proposal gets a new range proxy once the cut has been still 1.5 s. The whole-clip proxies continue as before and replace the range proxy the moment one lands; range files go 5 min after that, and all but a clip's two newest go at each build. `ROUGHCUT_RANGE_PROXIES=0` turns it off. **Measured on Killington** (the judged 15-shot cut: 9 clips, 2,248 s of footage, 148 s of shots; own `--work`, port 8783; box shared, load 17 → 8 over the run): from a cold open (no previews) every shot sharp at **537 s (9.0 min)**, ~13 s of range build per clip after its preview (previews 408 s of it, ranges 111 s); with the previews already made, **110 s**. Before: ~37 min of proxy encoding (PLAYBACK, load 5-14), and only once the index runs. In real Chromium on that board all 15 shots play at 1280×720 from `/media/range/…` (each `/media/proxy` a 307 `range`). New `app/tests/test_range_proxy.py` (14): the cut's ranges, merge and cover rules; the profile on the GPU and CPU paths (1280, 29.97 from 59.94, upright with no matrix, keyframes ≤ 1 s, full length); seeks inside the shots land on the whole proxy's frame (not one either side: the fixture's bar moves 16 px a frame) and are as sharp as it (a 1-px grid the preview loses), outside the ranges the right frame from the preview, for a flat and a −180° HEVC 59.94 clip on both paths; frame-for-frame on the proxy's grid across range edges; the 307 / status contract; the whole proxy replacing it and the prune; a trim past the range (falls back, then a new range); turned off = today; the walk's order (preview, range per cut clip, then the rest; no whole proxy touched) and its watch of the cut; Chromium on `/media/proxy` following the 307 to the 1280 file, seeks matching the whole proxy in the same browser, upright.
- **Redesign verdict 6: FAIL, everything 8-9 but 6:7 and 10:6** (`judge/verdict-6.md` on `ee3f15e`; 2, 5, 12 at 9). Real 12-clip first pass 13.3 min at load 21. Blockers: K2, and an accepted cut lost once on a reload (save stalled, no beforeunload/keepalive). The judge's K2 read: "a harsh Premiere editor would no longer call this a toy, but would not choose it over Premiere yet". Rulings: load-aware burners honest but a skip leaving the gate green is lenient; Playback Resolution honest (belongs in the rubric log, and should be a visible setting).

### Changed
- **A real-length proposal shows its rows, its checks whole, and plays on L / K / Space (redesign wave 8, lane REVIEW3; verdict 6, punch 4).** On real output (19 shots, six checks of 150-200 characters) only 3 rows were in view at 1440×900 and a check read "…the viewer can ide". Each check in "Things to check" is now one line (its whole text on the line's hover; a click opens it in place), three shown and "+ N more". While a proposal waits and the Assistant is its group's open tab, the Effect Controls column runs the window's full height (`#ws.ptall`, toggled by app.js, never stored): the Program monitor keeps its width, the Source monitor gives up the column (never under 180 px, its splitter at rest), and answering the proposal or opening another tab puts the editor's arrangement back. A 19-shot first cut and re-run now show all 19 rows. `revise.trim_check` caps a check at 300 characters at a sentence end, else at a whole word with "…" (never inside a word); a check saved cut at exactly 200 characters mid-word is shown to its last whole word. On an empty sequence with a proposal waiting, L plays the proposal, K pauses it, Space toggles (`window.rcProposalPlayer`, the hook timeline-keys.js calls; resumes from the frame it stopped on), instead of "nothing to play yet". `test_proposal_review.py` +3 (a real-length first cut and re-run: ≥ 5 rows in view, one-line checks, panels tile with no scroll, L/K/Space; no check cut mid-word server-side and on screen).
- **The audio meters draw their scale once.** meters.js set a canvas font and drew the dB labels every frame, which made Chromium resolve the font through style: a forced style recalc on every played frame (LUTCACHE's trace: 3-10 ms per frame at load 20, the next-largest main-thread cost after the grade, which now draws in a worker). The background, ticks and labels go into a cached canvas rebuilt only on a size, orientation or pixel-ratio change; each frame copies it and draws the bars. test_timeline_audio + workspace + program_a3: 43 passed.

### Fixed
- **The strip's trim keys (`[` `]` `{` `}`) go through `tl.setRange`.** app.js `nudge` wrote `seg.in`/`seg.out` itself, rounded to 0.01 s: off the frame grid, keyframes left where they were (KEYANCHOR's request). It now calls the timeline like every other trim: clamped, on the grid, keys kept on the media, posters re-taken, one undo. New `test_the_trim_keys_keep_the_keys_on_the_media_and_the_grid`; ui_flow + colour_ui + keyframes_anchor: 88 passed.

### Fixed
- **Keyframes stay anchored to the media, as in Premiere (redesign wave 7, lane KEYANCHOR; LOOP.md's wave-7 queue).** A shot's keys (`controls.keys = {prop: [[t, v], ...]}`, t film seconds from the shot's start) used to stay put while the shot under them moved: a razor copied them unchanged to the second half (and shared one `controls` object between the halves), an in-trim slid the animation off the picture it was made for, a speed change left it at the old timing. Now one rule, `keysWindow(keys, a, b, scale)` in `app/static/timeline.js` and its twin `edits.anchor_keys` in `roughcut/edits.py`: the shot becomes the old shot's film window [a, b] (times first scaled by `scale`), keys inside move a earlier, and where keys fall outside a side they go and a key on that edge holds the value they gave there (a key already on the edge is kept, not doubled; no edge key is invented where nothing was cut off). *Razor / split / Ctrl+K* (`tl.split`, and `cutAt` under insert / overwrite) at film offset d: the first half keeps the keys before d plus a key AT d holding the interpolated value; the second half has the rest d earlier plus a key at 0 holding the same value; each half gets its own deep copy of `controls`. *Trims* (`tl.setRange`: edge trim, roll, slip, nudge, Q/W, and the overwrite's edge trims): an in moved Δ clip seconds is Δ / speed film seconds, every key that much earlier; an out-trim drops keys past the end for one at the new end; a slip shifts them by its delta. A drag calls setRange once a frame inside one begin … commit, so the keys are computed from the shot as the gesture found it: dragging past a key and back within one drag loses nothing. *Speed* (`tl.setSpeed`): key times × old / new rate, clipped to the new length. V2+ clips' `trimOverlay` does the same when they carry keys. Undo restores the keys exactly (they ride the cut's snapshot). *Server* (`edits.apply_ops`): `split`, `freeze` and a ranged `speed` (which split) cut the keys the same way, `extend` / `set_range` shift them, `speed` scales them; `_copy` deep-copies `controls.keys`. New `app/tests/test_keyframes_anchor.py` (12: opacity 100 % at 0 s → 0 % at 2 s on a 4 s shot; Ctrl+K at 1 s → half A ends at 50 % and half B starts at 50 % and reaches 0 at its 1 s, the Program's CSS opacity and composited pixels on the line at the cut −1 frame, on it and +1 frame, one `split` undo restoring the keys; an in-trim of 0.5 s → keys [0, 75], [1.5, 0] and the same media time shows the same opacity and pixels; a drag past a key and back equals the direct trim, one undo; out-trim and slip; 2× → keys at half times, undo / redo; the cut keys survive save + reload; the server's split (at 1× and 2×), freeze, ranged speed, extend, set_range (slip), speed, position edges, and `clean_keys` accepting every result). With the fix reverted, 11 of the 12 fail.

### Changed
- **The grade draws in a worker; the cut no longer pays for it (wave 7, the perf hole after a cut).** Measured first: the queued hypothesis was wrong. The per-shot LUT's upload is 0.1 ms (a 17³ table is 14 KB) and its fetch, parse and upload at a cut 0.1-0.2 ms of main thread. A Chromium trace of `test_perf_latency`'s play showed every played frame costing the main thread ~50-60 ms at load 15-20: grade.js's draw (scale, upload, shade: 15-30 ms) and, at the compositor commit, a synchronous read-back of the WebGL canvas waiting on the software GL (`GLES2::ReadPixels`, 15-37 ms). The frame of a cut carried two draws (the outgoing buffer's last frame, then the incoming one from `advance()` → `setShot`), and when the cut held the outgoing buffer paused on screen, a full-size draw that resized the canvas 640 → 674 → 640. Now: `#gradeCanvas` goes to a worker (`transferControlToOffscreen`); each new frame is sent as a `VideoFrame` with the LUT and the live-preview uniforms (one in flight, the newest waiting), and the worker scales, uploads, shades and commits off the main thread. The renderer is one self-contained function the worker runs from its own source; `probe()` runs it on a canvas of its own (screen = probe, asserted), and it is the fallback where Worker / OffscreenCanvas / VideoFrame are missing (`?grade=main` forces it). Also: one coalesced draw per rendering step; Playback Resolution follows the player, not the paused outgoing buffer; LUT textures cached per record (LRU 8); the next shot's LUT fetched and its texture made when the player parks its buffer (`grade.prefetch`, called from app.js `armNext`), so the cut's first frame has its own LUT (it used to show the previous shot's for 70-200 ms while the fetch ran); a colour save while a fetch is in flight no longer caches the stale table. Interleaved A/B on the perf fixture at load ~15 (2 runs each): base 22-27 fps, max gap 100-133 ms; worker 29.5 fps, max gap 66.7 ms. New test: `test_the_grade_draws_in_a_worker_shows_what_probe_reads_and_prefetches`.
- **Premiere's Playback Resolution for the grade.** While the picture plays, `grade.js` draws the graded frame at most 640 px wide (CSS scales it up); paused, it redraws at the monitor's full size, so a parked frame is always sharp. The per-frame upload and shader cost scale with the drawn pixels, and at the wave-4 monitor sizes a software-GL frame opened ~100 ms holes mid-shot under load (the wave-6 gate's perf failure). `test_perf_latency` now asserts <= 640 px playing and > 640 px parked; it passed 5 of 6 runs at load ~9-10 after the change (the remaining failure: an 83 ms hole ~45 ms after the second cut, inside the new shot's first frames; the lead's hypothesis, queued for wave 7: the per-shot LUT's texImage3D upload at the cut).

### Fixed
- **A video that stops fetching after a seek gets nudged instead of freezing.** Instrumented on /floor's band replay (the recurring "flaky" `test_0_restarts_the_clip_and_shift_0_restarts_the_band`, 1 run in 5 alone): after the seek to the band's start the element sat at readyState 1 with networkState IDLE for 9 s, nominally playing, fetching nothing. floor.js's tick re-issues the seek once the picture has not moved for over a second while the element is idle and starved; the board's Program monitor does the same in its 1.5 s stall timer. The floor test: 10/10 (was ~4/5).

### Fixed
- **`test_ingest`'s cold-land test expects no Program copy of Run first pass**, which the lead's `1d48347` removed (the wave-6 gate found it broken).

### Fixed
- **`test_highlights_and_shadows_move_the_preview_pixels` waits for the redraw it measures.** Effect Controls redraws on the next animation frame (`effects.drawSoon` -> `grade.draw`, i.e. `settle()`, which turns the canvas on); the test probed in the same task and read the raw frame (PLAYBACK saw it fail 7 of 8 on the base). Instrumented: the preview is right 50 ms after the change (shadows +100 lifts the mean ~5 levels). Product unchanged; 5/5 now.

### Docs
- **LOOP.md: wave 6 merged.**

### Changed
- **The Program monitor's "Run first pass" copy is gone, not hidden.** BINPOLISH hid it with CSS; firstpass.js no longer mounts it or watches #progEmpty, and the hiding rule is removed. test_firstpass_ui + workspace + ui_flow: 102 passed.
### Fixed
- **A cue that stays in the same buffer re-points the grade at once again (wave 6, lane PLAYBACK).** The frame hold (`showWhenReady`) also skipped `showLive()` when no buffer changed, so a click that cued a different shot in the buffer already on screen never told the grade the new shot (the journey's step 12, "warmer", waited 15 s for `grade.shot`). Only a change of buffer waits for its frame now; the same buffer goes live at once, as before, so the grade has the new shot's LUT before its frame. Journey 19/19 green, 55 s at load 9-10.
- **A preroll that starts late catches up instead of arriving behind the cut (wave 6, lane PLAYBACK).** After a short shot the park of the shot after it can land with less time left than its lead needs (a seek of ~0.12-0.25 s at load 10-18); the prerolled buffer then ran at the shot's rate and reached its in-point up to ~0.1 s after the cut, which the frame hold covered (46-76 ms holds measured). `preroll()` now runs it up to 4× until the cut (lead ÷ the time left), and `advance()` puts the shot's rate back as before. Perf fixture at load 10.7-12.2, no burners, three runs: cut holes 33-50 ms, holds ≤ 1 ms.

### Docs
- **LOOP.md: wave 6 merges so far and the wave-7 queue (keyframes across a split, overlay controls).**

### Docs
- **LOOP.md: KEYS2 and KEYUI merged; keyframes-on-split/trim queued for wave 7.**

### Added
- **Playback holds the picture through short shots; seeks open nothing they need not; the cut's proxies first; the Program plays A3+ (redesign wave 6, lane PLAYBACK; verdict 5 punch 2 + punch 5's A3).** A sub-second shot (verdict 5: a 12-frame Killington shot held a black "buffering…" for 400-483 ms) now parks the shot after it the moment it is on screen, as far ahead of its in-point as the short shot leaves room for (`prerollAt(seg, room)`, with the park's seek time learned like the start latency), and a buffer is put on screen only once it holds the shot's first frame (`showWhenReady`): until then the outgoing frame stays up, never black, and mid-play stalls say nothing for 1.5 s (then the calm line under the picture, never "buffering…"). A cue seeks once, straight to the frame (it parked on the in-point and then seeked again), before the inspector's work, in the buffer that already has the clip open (`pickBuffer`: a cue into the next shot seeks the parked buffer instead of opening the file in the live one); the other buffer is re-armed after the frame is up, and not at all when it is already paused just ahead of the next in-point (a cutaway and back). The server builds 720p proxies (and GPU previews) for the clips the cut plays first, in film order, then the rest (`cut_clips`, `first_wanted`, the index walk's proxy pool once the first pass's coarse tier is through); a waiting proposal's clips count. A3-A9 clips (EDL `audio`) play in the Program from their own hidden `<audio>`, parked 3 s ahead, at their track's mute / solo / gain through `rcAudio.setLevel`, metered. `test_perf_latency` adds a 0.4 s shot between the two 59.94 shots and asserts the live-buffer hole ≤ 67 ms across both cuts, six seeks median ≤ 250 / max ≤ 400 ms, and no black or wait text on any frame of play; new `test_program_a3.py` (3) and `test_proxy_order.py` (5). Measured on the perf fixture, box at load 8-10, no burners: before, cut 2 hole 67-100 ms with "buffering…" on 3-5 frames, seeks 28-96 ms; after, cut holes 17-50 ms, nothing on screen, seeks 27-71 ms. With 6 burners on top (load ~10-16): before 133 ms at cut 2 + "buffering…" on 6 frames, seeks 98-175 ms; after 33-67 ms at the cuts (a 46 ms hold, no text), seeks 66-95 ms (the longest gap, 133 ms, is inside one buffer before any cut). Killington proxies at load 5-14: 1.53 s per second of 59.94 HEVC footage, 0.82 s/s of 29.97 H.264; the judged 15-shot cut plays 9 of the 12 clips (2,250 s of footage, ~37 min of one proxy worker), its first clip CLIP_01 sharp after ~5 min.
- **The export renders keyframes and Lumetri's tone sliders, composites V2+ inside the parts, and never exports on a guessed size (redesign wave 6, lane RENDER2; verdict 5, the render half of punch 4 and punch 6).** *Keyframes* (`research/tools/assemble.py`; schema `controls.keys = {prop: [[t, v], ...]}`, t seconds from the shot's start in film time, linear between keys, held outside; keys win over the static value): every keyed value is `lerp_expr` = first value + Σ (v₁−v₀)·`clip((t−t₀)/(t₁−t₀),0,1)` (a step where two keys share a time), with `value_at` its Python twin. Volume: `volume=volume='pow(10,<dB lerp of t>/20)':eval=frame` after the retime, so `t` is film time. Opacity: the blend toward black (`dim_vf`'s maths) re-set on each frame it changes by a `sendcmd` file driving `lutyuv@kop`. Position X/Y, Scale, Rotation: one per-frame `perspective` warp (sense=source, eval=frame) whose four source corners are the inverse of the monitor's `translate·rotate·scale` at the frame's film time `(in−1)/rate`, over an 8 px black border cropped off after (so outside the picture is black), with pixel-centre offsets so a keyed identity is bit-exact and a keyed 180° matches the static one. Un-keyed shots keep the static graph unchanged. *Lumetri tone:* the render uses grade.js's formula (lane KEYUI), per RGB channel after the LUT and Contrast, before Saturation: x = clamp(y,0,1), y = x + 0.25·b·(1−x)³ + 1.5·s·x(1−x)² + 1.5·h·x²(1−x) + 0.25·w·x³ (b, s, h, w = blacks, shadows, highlights, whites / 100), then the mix toward Rec.709 luma and the clamp; skipped when all four are 0. `tone_curve` / `grade_channel` hold it in Python; with a tone slider set the part goes to RGB (BT.709, limited in), through one `lutrgb` table of `grade_channel` (a balanced `if(lt(val,m),…)` tree) and a `colorchannelmixer` saturation, and back to limited 4:2:0; without one, contrast/saturation stay the exact `lutyuv` they were. A flat grey at RGB 1/3 exports at the formula's luma within 2 code values (shadows ±100: ±0.222). *Overlays inside parts:* `overlay_pieces` lays every V2+ clip on the film's frame grid and gives each part the source range for exactly the frames it covers; `encode_part` decodes it as an extra input through the same conform and overlays it from its frame, after V1's motion and effects (V1's eye off is a black fill in every part). `tracks_pass` is now sound only (`.sound.mp4`, picture stream-copied): no whole-film re-encode for an overlay. Measured, 19 s sequence at 1080p, load ~20: a 1 s V3 clip took 1.63-1.77× the plain render before, 0.97-1.16× after. *Export never guesses* (`app/static/export.js`): while the size reads "measuring…" Export is disabled (title says why; Enter does nothing) and the repaint when the sample lands enables it; an In/Out range that trims a shot's head shifts its keys (and so does the size sample's slice, `server.sample_segments`). Tests: new `test_render_keyframes.py` (opacity 0→100 is 50 % ±5 % by luma at the midpoint, black at the first key, 25 % at a quarter; a scale key 100→50 animates and ends as Scale 50; rotation and position keys; keyed motion keeps a static opacity; a volume key −30→0 dB ramps by volumedetect; highlights −100 darkens the bright end and shadows +100 lifts the dark end of the export's histogram; the curve's identity, endpoints and ±0.222 at 1/3 and 2/3; a grey at 1/3 through the export under shadows ±100, highlights +100, blacks +100; `lerp_expr` evaluates to `value_at`; 15 in all); `test_export_dialog.py` +3 (Export disabled and a forced click/Enter starts nothing while a held sample measures, then within ±25 % of the file: −2.0 %; a 1 s V3 clip writes no second file beside the film and renders in ≤ 1.3× the plain sequence, composited in its range only, CFR at the sequence's rate; V1 hidden is black under V2 across two parts). A mutation that leaves Export enabled while measuring fails the new test.
- **The journey on real-footage shapes, and the id race as a test (wave 6, lane JOURNEY2; verdict 5 punches 1, 9 and 10).** New `app/tests/test_id_race.py`: drop a Project clip on V1 (HTML5 drag events), `effects.set('temperature', 40)` within 100 ms of it landing, wait for idle, reload: the shot's server id carries the warm balance, `colour.shots` has no `tmp-` key, Effect Controls shows 40.0; Accept on a waiting proposal sends exactly one PUT; across the session no response >= 400, no request for a `tmp-` id, every `/api/lut/<id>` names a shot on disk. Passes now; on `867f694` it fails with judge 5's evidence (`colour.shots keeps temporary keys ['tmp-1']`, the balance under `tmp-1` only). `tools/journey.py` is 19 steps: its fixture gains CLIP_R, HEVC at 59.94 with a −180° display matrix (`ffmpeg -display_rotation 180`), the pixels stored upside down and painted in four coloured quadrants, so a turn, a flip or a mirror each read wrong (the fixture is ffprobed at setup). New steps: `02 preview` (CLIP_R in Source before any index ran plays the server's 360p preview, upright), `15 drop-grade` (the punch-1 session above, in the journey), `16 upright` (CLIP_R's graded frame in Program, and Source on its proxy, upright), `17 short-shot` (a 0.4 s range marked I/O in Source, `,` at the first cut, ▶ plays shot 1 → the short shot → shot 3); `18 export` is now a full-sequence Quick 1080p export whose estimate is read once it stops "measuring…", and `19 ffprobe` checks the file's size within ±25 % of it and CLIP_R upright in the file. The review copy the board starts for the export is waited for at teardown instead of killed half-made (that kill was every gate's "!! review copy failed for journey.mp4"); a copy that is not ready fails the journey. Proven not vacuous: the journey goes red at `15 drop-grade` on `867f694` (orphan `tmp-7`), at `02 preview` with ORIENT's all-GPU rule restored in a snapshot copy (`tl looks br …`), and at `16 upright` with build_proxy ignoring the matrix (copies, not committed). `test_journey_tool.py` +1: the fixture reads upright displayed and fails the check as stored. A selects-step failure now says why (the board's picks read before the pass made them is never re-read).
- **A pending proposal owns the Assistant, and its checks are data (redesign wave 6, lane PROPOSAL2; verdict 5 punch 3).** The proposal sits at the top of the Assistant; the story and change boxes under it fold to one line ("▸ Ask for a change · What is this film about?") while it waits, and its counts ride the title's line. At 1440×900 a pending re-run proposal shows its first row and at least 5 rows without scrolling (the wave-4 4-row concession is gone); past 3 checks the rest fold behind "+ N more". The plan carries `checks: [{text, clip, in, out}]` written by the model (schema, every Ask prompt, forgiving validation in `revise.validate_checks`), and `revise.check_notes` merges the notes check's flags and the rejected ranges into the same list (`plan_checks`: `kind` flag / rejected / model, `shot` = the proposed row); "Things to check" renders from it, each ranged check naming its clip and range and opening its row. Plans saved before the list still render through the old reading of the notes. Pick markers sit under the name band (never over a ghost's name); on an empty sequence the ghost is drawn in V1's own row and the Program monitor parks on the proposal's first frame. Tests: `test_proposal_review.py` 9 (re-run rows ≥ 5, the 5-row first cut, checks from the list, picks clear of names, Program frame, server-side checks).
- **Premiere's C, and Add / Delete Track on the undo stack (redesign wave 6, lane KEYS2; verdict 5, punch 5).** (1) C picks the Razor tool and nothing else, as in Premiere: a click with the razor cuts the block there, and Ctrl+K (Add Edit) is the cut at the playhead (it was: C picked the razor AND cut at the playhead). The key map reads "razor (click to cut) … ⌘K cut at the playhead". (2) Sequence › Add Track and Delete Track are one undo entry each: the added tracks' names ride the undo snapshot (`xtracks`), so Ctrl+Z after Add Video Track removes V3 (and redo brings it back). Delete Track no longer refuses a track that holds clips: the track and its clips go in the same single step, as Premiere's Delete Tracks does, and Ctrl+Z brings back both, with the header settings it had (no confirm: the step is undoable and Premiere asks none). A locked added track refuses deletion. (3) Clips on V3+ and A3+ drag like V2's, snapping their start or end to a cut, the playhead or another track's clip edge within 8 px (one `move V3` entry); a press within 6 px of a clip's edge trims that edge (`tl.trimOverlay`, one `trim V3` entry, clamped to the media and 0.2 s). `tl.moveOverlay` now moves A3+ clips too and honours the clip's own track lock (it checked V2's). (4) `tools/journey.py`'s razor step presses C, checks the shot count held, then clicks shot 2 at the playhead with the razor tool (its step list says so). Tests: `test_keys_premiere.py` (C changes the tool with the segment count unchanged; the razor click cuts; Ctrl+Z after Add Video Track removes V3, on disk too, redo restores; Delete Track with a clip then Ctrl+Z restores both and the header; a V3 clip drags +2 s on disk and snaps onto a cut), `test_timeline_structure.py` (Delete Track takes the clip; an A3 clip drags, a V3 clip trims by both edges, two undos restore it); the cut-at-the-playhead tests in `test_timeline_keys.py` / `test_tracks.py` use Ctrl+K.
- **Keyframes and Lumetri Basic Correction in Effect Controls (redesign wave 6, lane KEYUI; verdict 5 punch 4, dimension 7).** *Keyframes:* Position, Scale, Rotation, Opacity and Volume Level each have Premiere's stopwatch. On, the property is animated (a key at the playhead holding the value it had); the row shows ◀ ◆ ▶ (previous key, add / remove at the playhead, next key) and its lane of a mini timeline on the right of the panel (the shot's span, the playhead, a diamond per key; a click moves the playhead, a drag scrubs, a diamond drags along the shot on frames, a click on one parks on it). With the stopwatch on, a changed value (typed, scrubbed, arrowed, reset) is a key at the playhead. Off, the keys go and the value at the playhead stays (Premiere's rule). Position is one property with one stopwatch: its x and y keys share their times. The Program monitor shows the keyed values at the playhead, parked and while playing (the board's `playhead` event; measured lane click → Program in < 150 ms). Each stopwatch, add, remove and move is one undo entry (`animate opacity`, `add keyframe`, `remove keyframe`, `move keyframe`, `stop animating opacity`). *Data* (the wave-6 schema, LOOP.md): `segments[i].controls.keys = {prop: [[t, v], ...]}`, prop in `position_x`, `position_y` (fractions of the frame, 0.5 = centre, as `controls.position`), `scale` (%), `rotation` (degrees), `opacity` (%), `volume_db` (dB); t = seconds from the shot's start in film time, on the sequence's frame grid, strictly increasing; linear between keys, held before the first and after the last; **a prop with keys wins over its static value**. `server.py clean_keys` (inside `clean_controls`) 400s an unknown prop, a non-list, a non-[t, v] key, a bool / string / non-finite number, t < 0, unsorted or duplicate times, a value outside the prop's range (position −10..10, scale 0..1000, rotation ±3600, opacity 0..100, volume −60..6) and more than 64 keys; an empty list is dropped. *Lumetri Color › Basic Correction:* Premiere's order, each a slider and a scrubbable number — White Balance (Temperature, Tint), Tone (Exposure, Contrast, Highlights, Shadows, Whites, Blacks), Saturation; a slider drag is live and one undo entry. New `controls` keys `highlights`, `shadows`, `whites`, `blacks` (−100..100, 0 = off, validated in `CONTROL_RANGES`), previewed by `grade.js`'s shader **per channel in RGB 0..1, after the LUT and the contrast, before the saturation**: `y = (lut(c) − 0.5)·(1 + contrast/200) + 0.5`; when any tone value ≠ 0: `x = clamp(y, 0, 1)`, `y = x + 0.25·b·(1−x)³ + 1.5·s·x(1−x)² + 1.5·h·x²(1−x) + 0.25·w·x³` with b, s, h, w = blacks, shadows, highlights, whites / 100; then `y = mix(luma709(y), y, saturation/100)`, `out = clamp(y, 0, 1)` (with all four 0 the step and its clamp are skipped, so contrast and saturation are unchanged). Shadows peak at x = 1/3 (±0.222 at ±100), highlights at 2/3, blacks / whites move the end points by ±0.25. The render's side (assemble.py) is lane RENDER2's. New `app/tests/test_keyframes_ui.py` (8: stopwatch and lane per property, lanes in one column; two opacity keys → the Program's CSS opacity and its composited pixels at the midpoint within ±10 % of the interpolated 60 %; lane click → Program < 150 ms; playback follows the line; scale and position keys; one undo per animate / add / move / remove / stop, ◀ ▶; keys survive a reload; 13 bad keys bodies and 4 bad Lumetri values are 400s). `test_effect_controls.py` gains the Basic Correction order, no ellipsized property name at 1440, highlights / shadows / whites / blacks moving `grade.probe()` within 150 ms and in the right direction, a slider drag, and the four values surviving a reload (25 passed). `test_budget.py`: "/ shot" controls 61 → 65 (the four stopwatches in view, measured, no headroom; REDESIGN-GOAL rubric log). Position's Effect Controls range is now −10..10 frames, the server's (it allowed 11, which the server refused).
- **Redesign verdict 5: FAIL, closest yet** (`judge/verdict-5.md` on `867f694`; 1:8 2:9 3:8 4:8 5:8 6:7 7:7 8:8 9:7 10:6 11:8 12:8). Real 12-clip first pass 811 s at load ~20; wave 4 held up on real footage. Blockers: K2 and the lost grade on a just-added clip (the double save, fixed by SAVE after the judged rev). Rulings: LAYOUT floors and budgets honest; PERF's 100 ms gap limit and the proposal-rows trade-off lenient (the judge sets 67 ms and >= 5 rows). Punch list drives wave 6.

### Changed
- **The playback latency test measures the board, not the box.** It tops the machine up to about one runnable task per core instead of stacking a burner per core on top of other jobs, and skips with the measured load in its reason when the box is already past twice its cores. Limits unchanged; logged in REDESIGN-GOAL.md's rubric log for the judge.

### Fixed

- **The cold screen: two "Run first pass", "press , (Insert)", no stripe under V1 (wave 6, lane BINPOLISH; verdict 5 punch 9).** The empty Program monitor no longer draws its own Run first pass (firstpass.js still mounts it; workspace.css hides it), so the header's and the Timeline card's are the two; both empty states say "press , (Insert)"; the track header column is one surface to the panel's foot (the 8 px seam between V1 and A1 showed the sequence area's near-black); the empty Timeline's box is centred right of the header column instead of under it. `test_workspace.py::test_the_cold_screen_offers_run_first_pass_twice_and_says_what_comma_does`; `test_firstpass_ui.py` now checks the Program copy is not drawn.
- **Source In/Out land on the clip's own frames; `,` / `.` past the film's end leave a gap (wave 6, lane BINPOLISH; verdict 5 punch 8).** A Source monitor mark (I / O, and a pick opened in Source) is snapped to the clip's exact rate (29.97 -> 30000/1001, `dock.source.snap`), so a 29.97 clip in a 59.94 sequence is no longer marked 0.4 of its own frame off. The playhead may sit past the end, as Premiere's does (up to an hour; the canvas grows to it; nothing is cued there), and `,` / `.` there go through `tl.placeAt`: a gap of exactly that length on V1, then the clip, one undo. `test_workspace.py`: `in × 29.97` integral from the Source bar and after `,`; `.` and `,` 10 s past the end leave a 10 s gap.
- **Selects shows the first pass's picks as soon as they land (wave 6, lane BINPOLISH; JOURNEY2's flaky `selects` step).** `fetchPicks` kept a read made before the pass had picked (`firstpass_picked` false, no rows) for its 4 s TTL, and showing the tab did not re-read, so Selects could say "nothing kept yet" after a pass. A "not picked yet" read is never cached now, a forced read asked for while one is in flight runs again after it, and `window.rcPicksStale()` forces one; firstpass.js calls it when a run's result lands, and `showProposal` when a proposal opens. `test_picks_ui.py::test_selects_shows_the_picks_opened_a_second_after_they_land`.
- **Selects reads like a bin at 1440 (redesign wave 6, lane BINPOLISH; verdict 5 punch 7).** Every row is one line: the name is the kind chip and the quote, ellipsized, the whole of it (and the reason) in the row's hover; no thumbnail in the list view. Every column but Name has a fixed width (Media In / Duration 82 px, Reason 186 px, Usage 58 px) so Reason is never the one squeezed; Media Out (In plus Duration, and in the hover) shows from a 640 px list, Clip from 740. "review on the pass" and "more found" are the panel's own buttons, and no link in Selects wears the browser's blue, the accent or an underline. `test_picks_ui.py`: every row <= 2 text lines, the name one line, Reason >= 180 px, no timecode cut, no link-coloured link; `_all_tab` waits out the tab's bin re-read (a row measured across it was detached). `test_workspace.py`'s Selects-width check and `test_ui_flow.py`'s still check follow the new columns.
- **One save in flight; a new shot gets one id (redesign wave 5, lane SAVE; LOOP.md "the double save").** The board PUT the cut whenever asked: an Accept saved at once and its timeline commit saved again 700 ms later (a hand trim PUT between the two was overwritten, the race REVIEW's test had to wait out), and a save sent before the first reply re-keyed the `tmp-N` ids went with those ids stripped, so the server minted a second id for the same shot (judge 4's trim read "added by hand"); a per-shot grade set on a shot laid in a moment ago was written under `tmp-N`, a name no shot had. Now: (1) `app.js save()` runs one PUT at a time; a save asked for while one is in flight is folded into ONE follow-up, built after the reply landed, carrying the latest cut; every caller gets the promise of the whole run, so `roughcutFlush` / `roughcutRefresh` (`flushSave`) wait for what is in flight and what they asked for. The timeline's own PUT (`saveSeq`, tracks/V2/markers) takes the same turn (`tl.putTurn`), so the board never has two `PUT /api/project` in flight. (2) New shots go to the server under their `tmp-N`; `PUT /api/project` mints one id per tmp id (`mint_board_ids`), renames the colour block's per-shot keys and reference to it in the same body (a `tmp-` key no shot carries is dropped), and the reply's `ids` names them; the board re-keys exactly from it (`tl.applyIds`, no GET-and-match-by-position) and moves its own `colour.shots` keys with it. A tmp id already named goes under its real id (`tl.forSave` / `tl.idFor`), so an undo snapshot from before the re-key saves the same id on redo. (3) Accept saves once: its save runs on the microtask after the commit's touch and clears that timer; the answer (`POST /api/asks/answer`) goes once the cut is on disk, with the rejected ranges read at the click. The kept tab's add does the same. `saveSays('saved')` waits until the run is drained and no edit is waiting. New `app/tests/test_save_once.py` (5: two inserts during a held Accept save → one follow-up, never two in flight, the follow-up sends exactly the minted ids, disk one id per shot, a later save sends no `tmp-`, undo/redo keeps the id; the Accept is one PUT; a trim PUT from outside between the Accept and its old autosave survives, and a board trim during the held save lands; a grade set on a `tmp-N` shot, in the same task and during the held save, is on disk under the minted id; `roughcutFlush` waits for the follow-up; the server's id map). All five fail on `867f694`.
- **The timeline's render no longer forces a layout per A1 block (wave 5, lane SAVE; LOOP.md "onScreen()/keepInView layout thrash").** `onScreen()` read the view's `scrollLeft` / `clientWidth` after each A1 block's `left` / `width` write, and `keepInView` read them right after `positionHead`'s write, so every render and playhead move forced layouts. `tl.render()` now reads the scroll, the view width and V1's height once, at its top, before it writes; the view's size is kept by the ResizeObserver (read in its callback, where the layout is fresh; `viewWidth()` / `viewHeight()` fall back to a read only before it reports); `keepInView` uses that width, `state.scrollX` and the canvas width the render wrote to clamp, and reads nothing; `layoutTracks` uses the cached height. Measured with a 60-shot cut at 1440×900, zoomed in and scrolled, 6 CPU burners (one per core) on a box already at load ~20: `tl.render()` median 547-598 ms (p90 719-721) on `867f694` → 8.6 ms (p90 14); `setPlayhead` + render 472-583 ms → 17.6 ms (p90 24).
- **The timeline's live-shot highlight moves at the cut again.** PERF deferred the whole timeline repaint past a cut to keep the cut's frame light, and the `.live` block highlight rode along: it still marked the previous shot 0.2 s after the cut (caught by the wave-4 gate, `test_the_playhead_follows_playback_across_the_whole_film`, instrumented). `advance()` now flips the class itself at the cut, a class change with no layout; the repaint stays deferred.
- **Three tests caught up with merged changes** the wave-4 gate found broken: a render part's sound is PCM since EXPORTSTOP (`test_edits_render`); the index listing's poster is the versioned URL (`test_index`); the proposal table's "rows on screen" check counts the 4 rows that fit in the bottom-row Assistant of LAYOUT's arrangement (`test_proposal_review`; logged as a trade-off for the judge). The four files: 31 passed.

### Fixed
- **`test_firstpass.py` no longer depends on file order.** Index jobs that `test_firstpass_ui.py` cancels stayed in `server.INDEXES`, so `test_run_then_hand_trim_then_rerun_keeps_the_trim` failed whenever that file ran first (found by REVIEW). The `fp` fixture clears `INDEXES` like it clears `FIRSTPASS`; firstpass_ui then firstpass in one process: 35 passed.

### Docs
- **LOOP.md: wave 4 merged.**

### Changed
- **Bigger monitors: Premiere's Editing arrangement** (wave 4, lane LAYOUT; verdict 4 punch 3: "Source and Program each get a quarter of the screen, a 386-pixel-wide picture on a laptop"). The Source and Program monitors now share the whole top row, half each; the bottom row is Project | Timeline | Effect Controls (every group keeps its tabs, Window reaches every panel, every splitter still drags, double-clicks back and persists). Default Program picture: **674×379 at 1440×900 (was 386×217) and 841×473 at 1920×1080 (was 517×291)**, 0 % letterbox. The audio meters stand vertical beside the Program picture (`meters.js`; a strip under it only in a Program panel dragged under ~230 px). While Selects is the Project group's open tab the group is at least 592 px wide (taken from the Timeline, which keeps ≥ 460 px; a narrower window keeps its Timeline whole), so its list shows Media In, Media Out and Reason at 1440 (with Reason shown, Name takes 31 % of the list so a short line is not wrapped over five rows beside its who-tag); the Project tab gives the width back. **Saved layouts:** the layout key is now versioned (`v: 2`). A layout saved before this (no version) keeps the Project and Effect Controls widths the editor dragged; its top-row height and Source width are dropped once so the bigger monitors reach that browser too, and the whole old layout is kept under `roughcut.ws.layout.v1`. `test_workspace.py` checks the new arrangement, the Program floor at both sizes (≥ 560 / ≥ 800 px, letterbox ≤ 30 %, meters vertical beside it), the one-time migration and the Selects width; `test_budget.py`'s floor is 380 → 560 px, the picture is checked centred in its room, the strip check asks for ≥ 300 px of Effect Controls in view, and the words/controls budgets are lowered to the measured counts (rubric log, 2026-10-15 wave 4). **Posters:** the Source monitor's poster and the Project panel's icons are built from the clip's own versioned `poster` URL (POLISH's `<stem>.v2.jpg`), swapping only `?t=`, never an unversioned `/media/poster/<stem>.jpg` (new `test_the_source_monitors_poster_is_the_versioned_one`).
### Docs
- **LOOP.md: PERF merged; timeline layout thrash queued for wave 5.**

### Fixed
- **`test_project_offers_a_poster_url_per_clip` expects the versioned poster URL** (`/media/poster/CLIP_A.v2.jpg`) that POLISH's `poster_url()` hands out; it still expected the old name and failed on the integration branch (found by PERF's sweep).
### Fixed
- **test_firstpass_ui's re-run test no longer races the board's second save.** After an Accept the board saves at once and its timeline commit queues the 700 ms autosave of the same cut; the test's hand trim (a PUT) landed between them 1 run in 3 on the review lane and was overwritten (`state` read `done`, not `stale-after-edits`; traced: two `PUT /api/project` from the page, 0.7 s apart, the second after the test's). `_accept` now waits for the answer to land and for 1.2 s without a board save. The double save itself is LOOP.md's wave-5 item. 6/6 green after.

### Changed
- **Proposal review where the cut is (verdict 4, punch 4).** A waiting proposal opens on page load (the ghost lane under V1 and the review in the Assistant) instead of a "last proposal · show it" link, and the Assistant's "No cut yet: …" line goes while one is up. The ghost lane is V1's height (≥ 80 px; 109 px at 1440×900, was 40), each ghost a filmstrip like V1's with its name in a top band, ✓ / ✗ at 20×18 px (stacked on a narrow shot), and the lane's name ("Proposal · 4 of 5 ✓ · 0:08.0") in the track-header column instead of over the first shot. The Assistant: ▶ Play it · Accept N of M · Accept all · Discard on one sticky row, "Things to check" on top (the notes check's flags, then the checks the model's notes ask for; a click plays that shot), the model's notes folded to their first sentence (or paragraph), and a table with one row per proposed shot (✓ / ✗, #, clip, Source In / Out timecode at the clip's rate, ± / + and the why; a click opens the row and plays the shot) with removed shots struck at the end. The first pass's "First pass: …" line is the panel's own now, so it shows on a reload too. Lane RERUN's remembered rejections show too: the first-pass panel says "Won't re-propose N ranges you rejected" (the ranges on its hover; `would.rejected` from `GET /api/firstpass`), and a re-run's proposal lists what it left out under Things to check and greys it at the end of the table ("you rejected this — left out"), with the server's "Check:" / "Left out:" lines taken out of the folded notes. New `test_proposal_review.py` (4); test_ui_flow's last-proposal and Next-landing tests and test_timeline_lanes' label check follow the new behaviour.

### Docs
- **LOOP.md: wave 4 progress and POLISH's open items queued for wave 5.**

### Fixed
- **A film with a music bed is encoded at its preset's audio bitrate.** `effects.add_music` hard-coded `-b:a 192k`, so Master (256k) and YouTube (384k) exports with a bed came out at 192k. Since EXPORTSTOP the bed's pass is the film's one AAC encode, so it now takes the preset's `audio_bitrate` from assemble. `test_music_bitrate.py`: 2 passed.
- **A drop past the film's end leaves a gap only on V1 itself, beyond the snap zone** (lane POLISH, after merging EXPORTSTOP and PICKS2). A clip dropped on the ruler, or within 8 px of the end, still appends as before (test_timeline_lanes); dropped on V1's row further out, it lands where it was dropped with a gap before it. test_budget's numbers re-measured on the merged tree, no headroom: `/ shot` 181 words, `/ jobs` 108 words and 51 controls (EXPORTSTOP's × on the render pill is the +1).

### Added
- **Timeline structure: an empty sequence draws its tracks, gaps, Add / Delete Track** (wave 4 lane POLISH, verdict 4 punch 9). An empty sequence now draws its ruler and its V2 / V1 / A1 / A2 headers, with the first action floating over them (it used to hide the whole timeline, so a clip could not even be dropped on it). A clip dropped past the film's end lands where it was dropped with a gap before it: a gap is a generated black clip marked `gap: true` (new `POST /api/gap`), so the monitor and the render play black and silence; it is drawn as empty track, is selectable, and a ripple delete (X / Shift+Del) closes it, as in Premiere. Sequence › Add Video Track / Add Audio Track / Delete Video Track / Delete Audio Track make V3…V9 and A3…A9 (`roughcut/tracks.py`: an added track's entry in `tracks` is its existence; a clip on V3 is an overlay with `track: "V3"`; an added audio track's clips are the EDL's new `audio` list; PUT/GET `/api/project` carry both). A clip goes there with `,` / `.` when that track is targeted and V1 is not. The render (`assemble.py` `tracks_pass`) composites the video tracks bottom to top (V3 covers V2), mixes the added audio tracks with A1 at their gain, and honours their eye / mute / solo. Not yet: dragging a clip along V3 / A3 (V2's drag only), and the Program monitor does not play A3's sound (the export does). Delete Track refuses a track that still holds clips.

### Fixed
- **No wordless primary button; one AI action in the empty Timeline; the strip's AI chrome folded away** (wave 4 lane POLISH, verdict 4 punches 7 and 10). A running job (the preview build ran a whole Killington session) no longer collapses Next to a blue "→": the chip keeps its words when they fit, and when they do not it becomes its icon and is not blue. Effect Controls opens on the shot's properties: ▶ play, Ask about this shot, Look deeper, Remove, the why, the first line spoken and "why? ▸" sit in a collapsed "Assistant" section at its foot (its open state carries from shot to shot), and the spoken line wraps instead of ending in "…". The empty Timeline offers exactly one AI action at every first-pass state: the first pass's until a pass has run, then "Make a first cut" (never both). Undo reads "Ctrl+Z" off macOS in the strip's titles, the accept / snap toasts and /floor. Selects' name column wraps instead of cutting a clip's line off. Poster URLs from `/api/clips` and `/api/project` carry `v=<PICTURE_V>`, so a browser holding a pre-ORIENT upside-down poster (served immutable for a year) asks again. Tests: test_budget's `/ jobs` fixture answers the running Ask (`/api/ask/<id>`) as running, so the gate's jobs.png no longer shows "the ask that was running failed: no such job" (a new check fails on any failure toast); test_budget runs in its own work dir, so it no longer fails after test_ui_flow in one process (test_ui_flow's renders left "the film" done, Next became "Index what isn't done", the first pass covered it and no chip was drawn); `/ shot` and `/ jobs` budgets set to the measured counts.
- **The journey rejects a shot and checks the re-run remembers it (RERUN's fix in the gate).** New step 04 `reject`: ✗ on the first proposal's 5th shot (its ghost's `.rv.no`), the button reads "Accept 4 of 5"; `accept` applies exactly the 4 and waits for the EDL's `provenance.rejected` to hold the 5th shot's range. The journey's stub model now proposes that shot (CLIP_C 4.4–5.6, shaped to 4.4167–6.0) and RE-PROPOSES it on the re-run; `rerun-accept` asserts the re-run proposal leaves it out, names it in `plan.firstpass.rejected` (`dropped`) and says "you rejected" in the notes. Mutation-checked: with `drop_rejected` disabled (a snapshot copy, not committed) the journey goes red at `13 rerun-accept: the re-run proposes the rejected range again`. Journey ✓ 15 steps · 52 s.

### Docs
- **LOOP.md: the double-save id race queued for wave 5.**

### Fixed
- **A re-run keeps what it says it keeps, and remembers what you rejected** (verdict 4, punch 2). On Killington a re-run after a hand trim rounded 12 "kept" shots to hundredths (off the frame grid: CLIP_02 1.4014 → 1.4), brought back CLIP_01 120.9-128.6 a minute after the editor ✗-rejected it, and labelled the trim "added by hand". Now: a shot the model types back from the cut (the prompt prints hundredths) snaps to the cut's exact in/out by id (`revise.to_sequence`, echo tolerance 0.006 s, under half a frame at 60 fps), and boundary polish leaves it alone; the panel's rows and the provenance base keep the cut's own 4-place values (`firstpass._row`, `record_base`). A partial Accept records the ranges it turned down on the cut (`provenance.rejected: [{clip, in, out, at, job}]`, from the answer's shot numbers and the board's `ranges`, whichever of the save and the answer lands first; a shot the proposal only kept or moved is not one). The re-run's brief lists them as "do not propose", and its proposal is held to it: a re-proposed rejected range is dropped (a rejected change to a cut shot leaves that shot as it is), said in the notes ("Left out: … you rejected this shot before") and in `firstpass.rejected` / `notes_check.rejected`; a range in the cut now is never touched, and accepting the exact range later forgets the rejection. The trim label: when the board saved again before re-keying its temporary ids, every shot got a fresh id and the trimmed one read "added by hand"; a base shot whose id left the cut is now found again by its footage (`firstpass._realias`), so it reads "trimmed". The board sends the rejected ranges with the Accept (`tlLanes.reviewed().rejectedRanges`, `answerProposal`). Tests: `test_firstpass.py` +5 (a mocked run → accept minus 2 rejected → trim → a re-run that echoes in hundredths and re-adds a rejected range, in both save/answer orders; the pure rules; re-minted ids; the echo snap). Also: `GET /api/firstpass` reads the live run and its record under the run's lock, so a poll in the instant a run ends no longer reports "the last run was interrupted (the server restarted)" (an intermittent test_firstpass failure).

### Added
- **The first pass's picks are editor objects (verdict 4, punch 1).** After a pass that found 81 moments, Selects said "nothing kept yet" and the cut had 0 pick markers: both read only the keeps a /floor review makes. Now, once a first pass has made its picks (`/api/picks` says so in its new `firstpass_picked`), Selects lists every pick beside the editor's keeps, one list: clip, Media In / Out and Duration in the clip's timecode, the reason, hero ★, and a tag saying who it is from (the pass's kind, e.g. "speech", or "yours" on a keep; a pick a keep covers is the keep's row, one rejected on /floor is gone). The rows are clips like a keep's: `,` / `.` insert / overwrite exactly the pick's range, a drag lays it on V1, a double-click opens it in Source with its In and Out. The timeline draws an amber marker wherever a pick or keep overlaps a shot, the why and who on hover. /floor stays as the optional detail view ("review on the pass ↗" in Selects' line). Before any first pass Selects is the keeps alone, as before. Every pick the pass counted is listed, not only the clips /floor's rounds have released (on the journey fixture that gating showed 1 of 3 while previews were still building). Tests: `app/tests/test_picks_ui.py` (4: 5 mocked picks → 5 rows with In/Out, `,` on row 2 inserts exactly its range, `#tl .tl-pick` = the picks over the cut, keeps and picks told apart, drag + double-click); the journey gains step 05 `selects` after the first pass's accept (Selects lists the pass's picks, count > 0, one marker per pick over a shot) and now has 14 steps.
- **Stop everything: `/api/index/cancel` and an × on the job pill (verdict 4, punch 8; lane EXPORTSTOP).** Once the first pass had moved on, the background index (proxies, close looks) and the preview builds kept encoding with nothing on screen able to stop them ("Stop leaves the preview ffmpeg running"). `POST /api/index/cancel` kills the index's tools by their process groups (`procscope`), ends the background media walk and kills every preview and poster encode (they now run in a `media_scope`; `stop_media`), stops a first pass still waiting on the index, and returns once the index has ended. The first pass's own Stop now stops the preview encodes too. Statuses stay truthful: a killed stage goes back in the queue (never running, done or failed), nothing reads as proxied or looked at that is not, and a killed preview is "not built", never "failed" (a monitor waiting on that clip asks for it again). The next index run restarts the walk. In the header, an index or render job carries an × (`data-testid=job-cancel`) calling its own cancel; a cancelled job's pill goes at once. Test `test_index_cancel`: with a mocked 60 s index stage and a mocked 60 s preview build running, the × leaves no ffmpeg child of the server within 5 s (pgrep), the pill is gone, the killed stages are queued, no clip reads proxied and the preview is not failed; a mutation that skips the preview kill fails it.

### Fixed
- **The sampled size estimate counts the sound the encoder really writes (lane EXPORTSTOP).** The first-open sample now records its AAC stream's measured bitrate and the estimate uses it instead of the preset's nominal one: on a small frame the sound is most of the file, and AAC at "384k" on simple sound comes in well under. The suite's first YouTube export went from +24.7 % (at the edge of the ±25 % test) to -1.5 %.
- **Export says what it writes (verdict 4, punch 6; lane EXPORTSTOP).** Three things the dialog said that the file did not do. (1) **One rate for any range:** an In/Out export said "29.970 fps" and wrote 60000/1001, because `sequence_fps` counted only the shots inside the range; `/api/render` now takes the whole sequence's rate (`export_rate`: the dialog's FRAME rate, which it now sends, else `sequence_fps` over the saved sequence before the range replaces it). (2) **The sound is AAC-encoded once:** every part carries 16-bit PCM (`assemble.AUDIO_PCM`) and the one pass that last lays the sound encodes it: the join, or the tracks pass when it rebuilds A1 from the parts, or the music bed's pass (the join and the tracks pass then write PCM). It was AAC per part, again at the join, and a third time under a bed. (3) **The size estimate is right on the first export:** the dialog's first open for a preset the bin has never exported with encodes a 2 s sample of the sequence with it (`POST/GET /api/export/sample`: 8 quarter-second slices spread over the film, the preset's own profile and rate, cached per preset and bin under `--work/export-samples`), and the estimate lays the sample's mean keyframe and mean other frame over the range (a keyframe per shot and per 250 frames). The size shows "measuring…" until it lands; a file of the preset still wins once there is one. Measured on 40 s of Killington (6 shots, Quick 1080p): 8 slices +12 %, against +97 % for the preset's typical bitrate. An export starting kills any sample still encoding. Tests (`test_export_dialog`): an In/Out range over a 30 fps clip in a 24 fps sequence writes 24/1; the first YouTube export lands within ±25 % of its sampled estimate; parts are `pcm_s16le` and the film one `aac` stream, with and without a bed.

### Fixed
- **Playback and interaction latency on a loaded box (verdict 4 punch 5, lane PERF).** New harness `app/tests/test_perf_latency.py`: two 1080p 59.94 fixture clips proxied by the real `build_proxy`, a hand balance on every shot (so the grade canvas runs as on Killington), one CPU burner per core, and in-page timing of `requestVideoFrameCallback` across a cut, `effects.set` → 2 rAF, click shot → Effect Controls and ↑/↓. Before (load 17-19): **4.3 fps with an 817 ms hole at the cut; set 210-368 ms; click 72-240 ms; keys 51-175 ms**. After (load 17-21, three runs): **26-28 fps, longest gap 83 ms, cut 17-50 ms; set 17-56 ms; click 35-119 ms (median ~55); keys 30-121 ms (median ~60)**. What it took: (1) the grade (grade.js) uploaded the whole 1280×720 frame through WebGL on every frame and then some (timeupdate, every Effect Controls repaint): it now scales the frame into a CPU 2D canvas at the size the monitor shows it, never re-uploads a parked frame that is already up, and skips timeupdate draws while frames are flowing; effects.js asks for one draw per animation frame instead of drawing synchronously three times per change. (2) The cut: `advance()` re-armed the buffer it was about to show, which re-seeked a buffer already parked on its in-point (a decode from the keyframe); `arm()` now leaves a buffer that is already there alone, the next buffer is parked 0.3 s ahead of its in-point and prerolled muted and hidden so it is running when the cut comes, and the re-arm of the buffer left behind, the Effect Controls rebuild and the timeline repaint wait for the new shot's first frames, one per frame. (3) `paintPos` asks the timeline to keep the playhead in view four times a second while playing rather than every frame (that check was a forced layout per tick). (4) **Proxies of sources above 30.5 fps are made at a whole fraction of the rate** (59.94 → 29.97, `proxy_rate`), preview only: the export, the clip facts and the frame grid read the original. The proxy folder carries `PROXY_V = "v2"` (`<work>/proxies/<bin>/v2/`), so an existing work dir rebuilds its proxies once (the 360p preview or the original plays meanwhile) and the old `<work>/proxies/<bin>/*.mp4` can be deleted by hand.

### Added
- **Redesign verdict 4: FAIL, every dimension now 6-8** (`judge/verdict-4.md` on `c1c5d6a`; 1:7 2:8 3:8 4:7 5:7 6:7 7:7 8:7 9:6 10:7 11:6 12:8). First real 12-clip Killington first pass: a proposal in 758 s at load ~19, Stop in 106 ms, a re-run after a hand trim kept the trim exactly. K1 resolved on real footage; K2 open. Punch list drives wave 4.

### Fixed
- **The bin-switch test checks the leak it is about, not a race.** `test_opening_another_bin_repoints_everything_without_a_relaunch` asserted `durations` was absent right after opening another bin; since INGEST, opening a bin starts probing ITS clips in the background, so the cache was sometimes already back (the second bin's own) and the test failed under full-suite load in every gate this loop. It now asserts the first bin's cache object is gone and that what is cached names only the open bin's clips. Checked the product side: a first-bin probe still in flight re-checks the bin before writing, and `clip_duration` always reads the current bin, so no stale duration can land.

### Fixed
- **A music track picked in the first second is no longer ignored.** The music controls' change handlers were bound at the very end of `boot()`, after the versions, the last proposal and the jobs had loaded; the timeline was already on screen, so a track chosen in that window showed in the menu while `music` stayed null: no bed on A2 and nothing saved for the render. Instrumented: 2 runs in 3 of the gate's music-lane test (it had been "flaky" in three gates and broken in the last). They are bound with the other first-paint controls now; that test passes 6/6.

### Fixed
- **A fix rounds to the frame grid away from what it fixes.** Since edits snap to frames (wave 3), "Trim it out" on a covered lens rounded its new In to the NEAREST frame, 0.6 s -> 0.5833 at 24 fps: one frame of the covered lens stayed in the shot. "Cuts a line off" could likewise round inward and clip the line it meant to keep. `gridAway` in app.js rounds a trim-out past the bad stretch and a line fix outward (0.6 -> 0.625; 4.45 -> 4.4583); drags still snap to the nearest frame. test_ui_flow's two expectations follow (they were left at the pre-grid values by the merges).

### Docs
- **LOOP.md: wave 3 merged.**

### Fixed
- **One owner for every Program element's level, and saves that keep the frame grid** (the lead's integration of TLAUDIO's requests). Effect Controls' Volume and the moved-sound element `#pvA1` now set their level through `rcAudio.setLevel` (meters.js) instead of writing `volume`, so a shot's Volume and A1's gain multiply instead of the last writer winning, and both can go above 0 dB through the gain node. `roughcut/tracks.py` keeps A1 offsets, V2 in/out/at, markers and the sequence In/Out to 4 places (was 2), so frame-grid values at 29.97 survive a save. The filmstrip's tiles are retried after a failed load like the posters. `test_program_tracks.py` reads the heard level (`rcAudio.levelOf`), not the element's raw `volume`. test_tracks + effect_controls + program_tracks + timeline_audio green.
- **A partial Accept records provenance (lane PASS, after PICKS merged).** Accepting some of a proposal's shots saved a cut that was not the plan verbatim, so `is_accept` failed, no base was recorded, and a later re-run counted the accepted AI shots as the editor's own hand edits. `/api/asks/answer` now keeps the board's `partial` ({accepted, of, rejected, kept}) on the record, and whichever of the save and the answer lands second records the base: shots the editor took from the proposal (changed or new in its diff) are the machine's; every other shot (kept by the proposal, a rejected change, a refused removal) keeps the provenance it had (`firstpass.partial_hand`, `plan_changes`). `test_firstpass.py` +2 (2 of 5 accepted, in both arrival orders: the re-run's keep list is exactly the prior hand edits).

### Changed
- **The first pass is the one first action on every bin without a cut, indexed or not (lane PASS, the lead's decision after HEADER merged).** `flow.js` `passCovers` also yields Next to the first pass when Next's stage is the cut, the cut stage is `ready` and the pass has never run, so the header's "Run first pass" is the screen's one blue button there too. `test_firstpass_ui.py`'s fresh-bin test now asserts the button IS `is-next` (and the only one) and `flowBar.yielded()`; `test_ui_flow.py`'s empty-timeline test waits on `flowBar.yielded()` instead of a "first cut" chip and still makes and accepts its cut. The first-pass label's `title` copy and the CSS comment about ellipsizing are gone (the header never ellipsizes now).

### Fixed
- **A coarse look decodes keyframes, not the whole master (lane PASS, verdict 3 punch 5).** Measured on Killington CLIP_01 (3:24, HEVC 3840×2160 59.94) at load 20-28: the look took 734 s, nearly all of it ffmpeg decoding every frame to keep one in 240. GoPro masters carry a keyframe every 1.001 s, so `contact_sheet.py` now samples a sheet at an interval of 2 s or more from the keyframe nearest each sample time (`-skip_frame nokey`, about 1 frame in 60 decoded) and labels the cell with that keyframe's own time (0.0, 4.0, 8.01, 12.01 …), never a time it was not taken at; the same 51 samples as before. A file whose keyframes are sparser than half an interval is decoded in full as before, and windows (the close look) always are. Frame extraction for CLIP_01: 9+ min → 77 s; the whole look stage 734 s → 128 s. The index's sampling is recorded on the sheet index (`sampling: keyframes | decode`). `test_index.py` +1 (a 1 s-GOP clip samples keyframes at 0/4/8; an 11 s-GOP clip decodes in full; a 1 s interval never takes the keyframe path).
- **The close look waits for its proxy in the first pass's coarse order.** A coarse look no longer waits on the proxy, so the close look (which reads the proxy) now names the proxy itself: in the first live run it skipped "nothing worth a closer look" for want of a proxy. `test_firstpass.py`'s never-finishing-proxies test now also asserts every close look is still queued.
- **The first pass stops now, remembers that it ran, and reaches a proposal without waiting for previews (redesign wave 3, lane PASS; verdict 3 punch 5 and the journey's findings).** Verdict 3: on Killington Stop took 5 min 25 s and then said "Not run on this bin yet", and after 15 minutes the pass had looked at 1 clip of 12. Now:
  - **Stop lands at once.** A job thread inside `roughcut/procscope.py`'s scope starts every tool (`subprocess.run` included, so the index's ffmpeg / contact-sheet / audio tools and the CLI backend's `claude -p`) in its own process group, and `/api/firstpass/cancel` kills those groups (SIGTERM, SIGKILL after 1.5 s) the way the export's cancel kills its render, records the stop and answers `state: stopped` before it returns. A killed stage goes back in the queue without counting an attempt (`Journal.requeue`), so three Stops never park a clip.
  - **A stopped or failed run is a state:** `GET /api/firstpass` gives `stopped` (or `failed`) with `when`, `step`, `finished` ("heard 12/12 · looked 1/12") and `resumes` ("looks at 11 more, then picks, then the cut"); the label is "Re-run first pass" and the panel says "Stopped 3 min ago · heard 12/12 · looked 1/12. Re-run first pass looks at 11 more, then picks, then the cut."
  - **A proposal sooner.** The first pass runs the index in the journal's new coarse order (`Journal.next(coarse=True)`: probe, telemetry, the audio pass and one coarse look for every clip before any proxy, close look or picks; a coarse look waits on the probe only, since it reads the master) and starts the picks and the cut as soon as every clip is heard and looked at (`Journal.coarse_ready`). Proxies and close looks are not prerequisites: the index carries on with them in the background. The index also runs its stages concurrently now, each pool up to its configured workers (one proxy encode beside two contact-sheet reads beside the audio pass), where it used to run one clip-stage at a time.
  - **A shot only colour-graded by hand is the editor's own ("graded").** A base records each shot's grade (its colour-block override and the Lumetri values in Effect Controls) from the colour the accepting save writes, so a later hand balance or Lumetri change makes the shot a hand edit that a re-run keeps exactly.
  - **Proposal ranges on the frame grid.** Every range an Ask or first-pass proposal makes (padded by boundary polish or not) lands on the sequence's frames (`revise.to_sequence`, 59.94 → 60000/1001), at least one frame long; a range copied from the cut is left exactly as it is.
  - **Effect Controls survive a re-run.** A proposed shot that continues one of the cut's (same id) carries its `controls` over exactly; the model never sees them, so a re-run's Accept used to drop every kept shot's Motion / Opacity / Volume.
  - **The running label is short:** "First pass · 3/12" (clips heard and looked at), "First pass · picks", "First pass · the cut"; the counts go in the hover and the panel. The control no longer ellipsizes.
  - Tests: `test_firstpass.py` +6 (a proposal forms while proxies never finish; a graded-only shot is kept by a re-run, balance and Lumetri; proposal ranges on the frame grid; controls survive a re-run; a stopped run's state, finished and resumes words, killed looks re-queued with no attempt counted; the resume wording); `test_firstpass_ui.py` +1 (Stop during a 60 s look with a child of its own: not running within 5 s, `pgrep` finds no child, the label "Re-run first pass" and the panel naming what finished) and the stop-while-writing test now expects "stopped"; `test_server.py`'s two Ask range expectations are on the 24 fps grid.

### Docs
- **LOOP.md: wave 3 progress and the lead's wave-4 queue** (single AAC encode, client cut-sameness on controls, first-open size calibration, versioned poster URLs, V2 grade, overlapping moved sounds, the journey's review-copy warning).

### Fixed
- **A trim never moves the edge it did not touch (lane TLAUDIO, found by the journey).** The frame grid snapped both edges of a trimmed shot, so a proposal's 0.01 s in moved when its out was trimmed (and 4-place rounding could move it by 0.0001 s on every drag step). Now the dragged edge goes to its nearest frame (at 1×; retimed, it makes the shot's film length whole frames) and the other stays exactly; an insert or a slip puts both on frames. test_timeline_audio: an out-trim keeps the in, an in-trim keeps a pre-grid out.
### Fixed
- **One owner for a Program element's level (lane TLAUDIO, lane PROGRAM's request).** `window.rcAudio.setLevel(el, key, linear)` in meters.js multiplies named factors per element ('track': A1 mute / solo / gain_db, which timeline.js's applyMonitorAudio now writes through it; 'clip': Effect Controls' Volume once effects.js calls it) into the element's WebAudio gain node, so A1 above 0 dB is heard above 0 dB; before the graph exists the product, capped at 1, is the element's `volume`. `#pvA1` (an unlinked shot's moved sound) is metered like pv0/pv1. Tests: test_timeline_audio.py +2 (A1 -12 / +6 dB move the meter by -12 / +6 dB within 2 dB; a moved A1 sound on #pvA1 is in the mix, > -30 dBFS); with test_program_tracks + timeline_* + tracks + keys_premiere + modules_route 120 passed; workspace + budget + effect_controls 70 passed, budget counts unchanged.
- **Rotation-flagged clips are upright in every monitor, poster and filmstrip, as they are in the export (redesign wave 3, lane ORIENT; verdict 3 punch 1).** `build_preview` took the all-GPU path (NVDEC + scale_cuda + NVENC) for a −180° clip because its rule only looked at quarter turns, and ffmpeg's autorotate does nothing to CUDA frames, so 9 of Killington's 12 GoPro clips played upside down until their proxy existed, and the posters cut from those previews were upside down too. Now the all-GPU path is taken only for a clip that needs no turn; a flagged SDR clip takes a new `gpu-turn` path (scale on the card, a 360p frame down, `hflip,vflip` / `transpose` from `TURN_VF`, matrix overridden so none is left on the file), and HDR or fallback clips take `gpu-decode` / `cpu`, where autorotate applies. The rule is build_proxy's and the export's: `orient: auto` applies the matrix to the pixels, `orient: none` overrides it to 0. A rotation-tagged original is served raw only under `orient: auto` and a quarter-turn flag (the browser turns it as the export does); otherwise it goes through a preview. Previews and posters carry a picture-cache version in their names (`<stem>.v2.mp4`, `<stem>@<t>.v2.jpg`, `PICTURE_V`), so an old `--work`'s wrong ones are rebuilt rather than served, and `/media/preview/` serves only current-version names. Measured on Killington CLIP_05 (−180°, 4K h264): preview by `gpu-turn` in 12.8 s on a loaded box, upright next to the autorotated source. `app/tests/test_preview_orientation.py` (17): every preview path × 180/90/−90 by quadrant colour against `ffmpeg -i src` autorotated, `orient: none` against the stored pixels, `/media/proxy` before and after the 720p proxy, posters from the original / preview / proxy, the export's part encode, a stale v1 preview and poster rebuilt, and real Chromium screenshots of `<video>` and poster `<img>`.
### Changed
- **One first action, and an app bar that clips nothing (redesign wave 3, lane HEADER; verdict 3 punch 8, dimensions 1 and 9).** Verdict 3: the blue button read "Next Make t…" and the job pills "Now Indexin…", "Rendering — p…" whenever anything ran, and a fresh bin offered both "Next: Index the footage" and "Run first pass". Now (`flow.js`, `workspace.css`): when Next's action is the first pass's (index the footage; anything about the index or the first cut while the pass runs) the chip is not drawn and the blue goes on "Run first pass"; while a job or the pass runs, or when its sentence has no room, Next is its icon with "Next: …" in the hover (measured whole, then compact, in one task: never ellipsized); a running job is a pill of an icon for its kind (waveform, eye, frame, spark, magnifier) and its %, its name in the hover, a failed one red; the first pass's control never shrinks. The FINISH ellipsis exemption is gone from `test_workspace.py` (REDESIGN-GOAL rubric log); new `test_the_app_bar_clips_nothing_and_a_fresh_bin_has_one_first_action` checks the fresh, mid-index, mid-first-pass and exporting bars at 1440×900. `test_budget.py` `/ jobs` words 132 → 109 (measured).

### Fixed
- **A quiet console on inserts (redesign wave 3, lane HEADER; verdict 3 punch 9, dimension 10).** A shot laid in by `,` or a drag carries a temporary `tmp-N` id until its save re-keys it, and Effects and the grade asked the server about it at once: `400 /api/fx/peaks?shot=tmp-1`, `400 /api/fx/price?…shot=tmp-1`, `404 /api/lut/tmp-1`, three console errors per insert. `fx.js` asks nothing per-shot for an unsaved id and fetches when the re-keyed anchor arrives; `grade.js` draws a fresh shot plain and takes its LUT once `tl.byId` resolves the re-key. New `app/tests/test_quiet_console.py`: three inserts (two `,`, one drag) with Effects open, 0 requests naming a `tmp-` id, 0 responses >= 400, 0 console errors, and Effects priced the shot by its real id after.

### Added
- **Sound on the timeline and the Program, and edit points on frames (redesign wave 3, lane TLAUDIO; verdict 3 punch 6 and the timeline half of 10, the journey's VFR finding).** *A1 waveforms:* `GET /api/peaks/<stem>?pps=` (10/20/25/50/100 min/max pairs a second, or `spp` samples per peak at 16 kHz; int8, the whole clip) decodes the clip's sound once with ffmpeg to raw mono PCM (proxy, else preview, else original), reduces it to 100 pairs/s and caches it under `--work/audio/<bin>/<stem>.<size>-<mtime>.peaks` (a 35 s 4K Killington clip: 1.5 s cold). Each A1 block draws the shot's range of it as one filled SVG path, at the shot's speed and scaled by A1's gain, fetched once per clip and only for blocks on screen; the transcript's speech is a faint band under it. *Meters:* `meters.js` puts a stereo peak meter (one canvas: -60..0 dBFS, green/yellow/red, 1.5 s peak hold, clip lights) in `#meters`; every media element in `#player` plus `#bed` joins a WebAudio mix bus on its first `play` (createMediaElementSource owned there; `window.rcAudio.gainFor(el)` hands out its gain node, which carries A1/A2 gain above 0 dB; mute/solo/gain below 0 dB arrive through the element's volume). It sits under the picture while the picture is as wide as the Program panel (1440×900: the 380 px floor holds) and beside it, vertical, when the panel has room. *Frame grid:* setRange (every trim, nudge, slip, roll), split (razor, C), insert/overwrite, setSpeed, the A1 offset and V2 commit on the sequence's frames (`tl.gridRate()`: 29.97 → 30000/1001; a shot's in on a frame, its film length whole frames), and the server keeps 4 places instead of 2 (`clean_segments`), so the cut's edit points are on frames. *Filmstrip:* V1 blocks are frames edge to edge (head poster, then a frame per tile, tile = V1 height × 16/9; the border drawn over the strip), so a trimmed block has no dark tail. Tests: `test_timeline_audio.py` (8: peaks real and cached; A1 path tall over a 1 kHz tone and flat over `anullsrc` silence; meters > -30 dBFS on the tone, < -60 in the silence and while A1 is muted; meters beside/under without overlapping; a drag-trim at 29.97 lands on a frame and on disk; razor/roll/slip/insert/speed on frames; tiles cover >= 95 % of every V1 block after a trim and a zoom). test_timeline_trim / test_timeline_ui / test_keys_premiere expectations moved from 0.01 s to the 24 fps grid (e.g. a sentence snap at 4.45 lands on 4.4583).
- **The Program monitor shows the sequence: V2 over V1, and a moved A1 sound at its offset (redesign wave 3, lane PROGRAM; verdict 3 punch 4, dimension 6).** The judge: *"the Program monitor hides anything on V2"* while the export composites it. Two followers now ride beside the pv0/pv1 pair, driven from the playhead (tl's `playhead` event, which every play tick, cue, scrub and shuttle step emits), so V1 stays the clock. `#pvV2` (a new `<video>` in the Program screenbox, above the grade canvas, under the fx canvas): inside a V2 overlay with V2's eye on it shows the overlay's clip at `in + (t − at) × speed`, plays and pauses with the monitor (re-seeked only past 0.2 s of drift) and hides outside it, parked on the next overlay's in-point so entering one is a seek in an open file. It is ungraded, and V1's Effect Controls do not move it. V1's eye off hides the pair, the grade and the rest poster: black under V2. `#pvA1` (an `<audio>`): a shot with `a_off` (unlinked and moved; relinking keeps the offset, as the render does) has its picture element muted, and its sound plays from this element at `in + (t − start − a_off) × speed` while the film time is inside the moved sound. The gap before the moved sound is silent and its tail sounds over the next shot's own, the way `assemble.py`'s amix lays A1. Its level follows A1's mute, solo and gain_db. `app/tests/test_program_tracks.py` (6 tests, red CLIP_A and blue CLIP_B, Program pixels read from screenshots): blue inside the overlay at clip time 2.0, red with V2's eye off and outside it, black with V1's eye off, seek to presented frame ≤ 250 ms on both layers (measured 33–142 ms), V2 playing in step, and the unlinked shot's audio element exactly 1.0 s behind its picture on a seek and within 0.15 s while playing, with A1 gain −6 dB and mute applied.
- **Picks and proposals as editor objects (verdict 3, punch 7; REDESIGN-GOAL dimension 11).** *Per-shot review:* a proposal on the ghost lane has ✓ / ✗ on every proposed shot (Enter accepts the focused shot, ⌫ rejects it, ← → move between them) and a ↺ on every shot of the cut it would remove; every shot starts accepted. Accept applies only what was accepted, in one undo (`tlLanes.reviewed`: a rejected new shot is not added, a rejected change stays as it is now, a rejected move and a refused removal stay where they are) and reads "Accept 2 of 5" with **Accept all** beside it once something is rejected; Discard is unchanged. Each ghost's hover carries its why, its polish, what it is to the cut (the first pass's kept / replaces / new, "your edit, locked") and the notes check's flags; a flagged shot wears a "!" mark, and the panel's rows carry the same ✓ / ✗ and ⚠. A first cut's proposal on an empty sequence now shows on the ghost lane instead of behind the empty state. *Selects as a bin:* the Selects tab is a list like Project's (Name, Duration and Usage always; Media In / Out, Reason and Clip as the panel widens, the rest in the row's hover), sorted by a click on a column head; its rows drag onto the timeline, `,` / `.` insert / overwrite the selected row's range, and a double-click opens the pick in the Source monitor with its In and Out set (then `,` inserts that). Usage reads "shot 2" (the link, titled "in the cut · shot 2") or "+ add" on hover. *Picks as markers:* the pass's picks are amber markers on V1 and on the proposed cut at the film time each pick starts inside a shot using its footage, the label and reason on hover (drawn, never saved — the M markers stay the editor's). Tests: `app/tests/test_picks_editor.py` (5); test_ui_flow / test_dock_ui / test_timeline_lanes assertions moved from the card grid to the list ("shot 2", duration as timecode, double-click → Source).
- **Redesign verdict 3: FAIL, most dimensions now 6-7** (`judge/verdict-3.md` on `28eca35`; 1:7 2:6 3:7 4:7 5:7 6:5 7:5 8:7 9:6 10:6 11:3 12:2). K1 resolved on evidence (for Karl to confirm); K2 open. Its top finding: 9 of Killington's 12 clips (rotated 180°) preview upside down while the export is upright. Punch list drives wave 3.
- **Wave 3 scaffold:** a `#meters` slot beside the Program picture and `meters.js` / `meters.css` stubs served from `/modules/`. `test_modules_route.py` covers them; test_workspace + test_budget unchanged and green (54 passed).
- **The export renders what Effect Controls shows, constant frame rate at the sequence's rate (redesign wave 3, lane RENDER; verdict 3 punch 3 and the export half of punch 10, REDESIGN-GOAL dimensions 7 and 8).** The judge: *"a property that previews and does not export is worse than no property"*; the journey: exports were variable frame rate (r_frame_rate 120/1, avg 23.89 on a 24 fps sequence). `research/tools/assemble.py` cuts every part through one `encode_part` filter graph: the conform and grade, then Lumetri contrast/saturation as grade.js's shader does them (`(c-0.5)·(1+contrast/200)+0.5`, then a mix toward Rec.709 luma by saturation/100; in the part's limited 709 YUV that is exactly a `lutyuv` of luma about 125.5 by k and chroma by k·s, clipped legal), the designed effects in the shot's stack (`fx_off` not drawn, `fx_order` ranked as effects.js ranks it, last on top), then Motion and Opacity as the monitor's CSS has them (`translate((x-0.5)W,(y-0.5)H) rotate(r) scale(s)` about the centre: only the part of the source that can land in the frame is cropped, scaled, rotated with black corners and overlaid on black where its centre lands, so 1000 % never builds a 19200-wide frame; opacity a blend toward black), and `volume_db` added to the shot's own level. Every part is a whole number of frames off the film's cumulative frame grid (`frame_spans`; the last frame held if the source runs short) with its sound exactly that long, and `join_parts` lays the pictures end to end by those lengths (concat `duration`) and re-joins the sound as one stream (the picture's timestamps reset to start at zero: the concat demuxer started it 0.021 s, the parts' AAC priming, after the sound), so the film is CFR, its video and audio streams the same length, and its length is the sequence's to half a frame; the tracks pass re-encodes with `-fps_mode cfr -r`. `--fps` sets the film's rate: every Export preset now renders at the sequence's rate (`/api/project` `fps`, passed as a rational: 29.97 → 30000/1001). New preset **Match Sequence** (profile `match`: the sequence's own frame size and rate, crf 18 medium). The size estimate is calibrated: once the bin has a file of a preset (an export names it; a film-tool render counts for its profile's), the estimate is that file's measured bits per pixel per frame (median of the newest 3), the hover says so; before that the preset's figure, scaled by the rate. The Location has a recent-folders chooser (folders under `--work/renders`, newest first, with film counts) beside the name field. `/api/render` validates each segment's `controls` with `clean_controls` and records them in the render's shot list, so a film made before a shot was scaled no longer counts as that cut (`render_facts`). `roughcut/edits.py` copies a shot's `controls` (not shares them) into every op's result, so a razor's halves change one at a time. `effects.js`'s rows no longer say "the export does not include it yet": each says the export renders it. New `app/tests/test_render_controls.py` (14: scale/position/rotation/opacity/contrast/saturation by pixels against an uncontrolled render, volume −6 dB by volumedetect, fx bypass and order by the effect's pixels, CFR 24/1 with exactly 24 frames a shot, the filter pieces, edits carrying controls); `test_export_dialog.py` takes "one frame" from the sequence's rate and asserts r_frame_rate == avg_frame_rate == the sequence's, Match Sequence at the source's rate and frame, the estimate within 25 % of the real file, the recent folders.

### Docs
- **LOOP.md: the journey's findings queued for wave 3** (VFR exports from off-grid edit points, grade-only shots not kept by a re-run, a trimmed block's dark thumbnail tail, a Status flicker).

### Added
- **The journey: `tools/journey.py`, and a `journey` line in the gate (redesign wave 3, lane JOURNEY; REDESIGN-GOAL dimension 12, verdict 2 punch 2).** One scripted editing session in real Chromium on the gate's frozen snapshot, the suite's synthetic fixture (built fresh by the snapshot's own conftest under the journey's out dir, with a fresh `--work` beside it) and a scripted model (no real model call: the ledger is checked for any other backend afterwards). Thirteen steps, one 1440×900 PNG each: open the board → the header's Run first pass → its panel → Run → the proposal opens → Accept → a ripple trim of one shot's out and another's in on the cut edge columns → a ruler click and C razors a shot (then V, as in Premiere) → a body drag moves the last shot to the head → Effect Controls › Temperature 40 on a trimmed shot (saved balance warmer, and the Program pixels when WebGL is there) → "Re-run first pass", whose panel must name both trimmed shots as kept → Run, Accept: every shot it listed as kept has the same clip/in/out, the warmth too → Ctrl+M, name, Export, the bar to 100, `export-done[data-path]` → ffprobe: video and audio, the file and its video stream within one *sequence* frame of `tl.total()`. Zero page errors (a page error fails the step it happened in). `journey.json` holds each step's name, ok, ms, PNG and why. Every process it started (the server's ffmpeg review copy included) is stopped before it exits. `python3 tools/gate.py` runs it after `screens`, in `--quick` too (~40–60 s): `journey ✓ 13 steps · T s` or `journey ✗ step K <name>: <why>`, red fails the gate. Proven not vacuous by twelve product mutants, each red at its own step. `app/tests/test_journey_tool.py` checks the journey/gate contract without a browser.

### Fixed
- **"Preparing preview…" no longer vanishes the moment it appears.** A monitor's wait line had two owners: app.js put it up when the video failed, and dock.js took it down when the poster at the playhead loaded. A cached poster loads at once, so on a media error the line showed and hid in the same millisecond (instrumented: 3 runs in 4) and the person saw nothing. The wait now holds its reasons (`poster`, `media`) separately and shows while either holds (`dock.wait`), on the Program and the Source monitor. Found by the wave-2 gate (`test_a_media_error_is_shown_on_the_monitor_not_swallowed` passed and failed on alternate commits); the test also now breaks the live buffer, not a fixed `#pv0`, and holds `/media/status` at "building". 8/8 runs pass; test_ui_flow + workspace + dock_ui + ingest + media_first: 112 passed.

### Docs
- **LOOP.md: wave 2 merged** (FINISH, EXPORT, FPUI, TRACKS, EFFECTS, INGEST) and the open list for wave 3.

### Fixed
- **Footage goes on the timeline before the AI has heard it, and a cold open no longer says the bin is empty (redesign wave 2, lane INGEST; verdict 2 punch 7 and 8).** Insert, `,`, `.` and a drag from the Project panel refused any clip without a transcript ("still being read — it can go in the sequence once it is indexed"). Now the dock lists such a clip in the board's clips from the folder's facts (`window.rcEnsureClip`: empty transcript, `heard: false`, the media route's proxy), so all four put it on V1 at once; `/api/project` lists an unheard clip that a shot uses the same way, so it plays and survives a reload; once its sidecar lands, its transcript replaces the stub on the Project panel's next look, without waiting for the index to end. The Status column says heard / looked / indexed / not indexed (it said "indexed" once a clip was merely heard) (column widths are lane FINISH's, which fit "not indexed"). `/api/clips` gains `fps`, `width`, `height`, `codec` (MEDIA's cached `media_facts`) and `probing`. Its probes run on a 4-thread pool started at `configure()`: one ffprobe for length + creation time + telemetry instead of three, then the colour tags `/api/colour` reads. A request waits at most 1 s for them, so a cold bin answers at once and the board asks again a second later. The Project list shows Frame Rate from 400 px and Resolution from 470 px. While `/api/clips` loads, the panel says "Reading the footage folder…" (never "No footage in this bin yet"). The Source monitor opens the last clip, else the first, so its poster shows at once (a keep then picked in the bin still wins `,`). The empty Timeline reads "Drag a clip here or press , · Run first pass" (the link calls lane FPUI's `rcFirstPass.open()` when that exists) instead of "Nothing indexed yet · The footage →", and the empty Program says how to fill it. Measured on killington-neutral with a fresh `--work` at load 12-25: Project rows 0.53-0.81 s after open (verdict 2: 5.7 s), a Source picture at 0.82-1.21 s, the timeline mounted at 1.67 s (was 5.25 s, `/api/colour` 2.8 s → 0.9 s). `app/tests/test_ingest.py` (6 tests).
- **A clip's card picture is 1 s in, not its first frame (lane INGEST, the lead's request).** Many camera clips open on black (Killington CLIP_12 is black for its first three frames), and the Project panel and /open share `/api/clips`' `poster` URL. `POSTER_AT_S = 1.0` (half a shorter clip; `poster_time()` reads only cached lengths, so it never probes and costs no decode); the background walk pre-builds that frame instead of 0 s. The Source monitor shows the card frame as its placeholder when no In is marked, so its picture is the pre-built one.
- **The empty Timeline defers to the first pass (lane INGEST, after lane FPUI merged).** The box reads "Drag a clip here or press ," with FPUI's own Run first pass button under it (no second link of ours), the empty Program keeps FPUI's button when it says how to fill it, and "Make a first cut" is disabled, with a hover saying why, while `rcFirstPass.state().state` is `running`, so the two cannot start overlapping cuts. `test_ui_flow`'s "one priced button" checks now count the board's own buttons (not `.fp-empty-btn`); `test_ingest.py` gains the disable check (7 tests).
- **dock.js no longer sizes the timeline's lane (lane INGEST, after lane TRACKS merged).** `fitTimeline` wrote `--tl-lane` on `#tl` from the panel's height, which the track rows no longer read (they use `--tl-v1h` and explicit heights, and `timeline.js` sets `--tl-lane` itself); it and its two observers are gone. `test_ingest.py`'s inserts now assert a V1 block and its linked A1 partner.
### Changed
- **Screen budgets set to the measured counts after wave 2's TRACKS and EFFECTS merges, with no headroom.** Words / 106, / shot 196, / film 114, / fx 134, / jobs 132; controls / 50, / shot 85, / film 49, / fx 53, / jobs 50 (TRACKS had proposed ~10 % above measured). Causes and numbers in REDESIGN-GOAL.md's rubric log: track headers, the Re-run first pass control, Effect Controls.

### Changed
- **Screen word budget +3 for the first-pass control.** `/ film` 104→107 and `/ jobs` 122→125 in `test_budget.py`: the header's "Re-run first pass" (K1) is three words that are now always in view. Measured 106 and 124 on the merged branch; logged in REDESIGN-GOAL.md's rubric changes log for the judge to rule on.

### Docs
- **LOOP.md: wave 2 launched** (FPUI, TRACKS, EFFECTS, EXPORT, INGEST, FINISH from `f371199`), verdict 2's rubric rulings, and the fixed `data-testid` hooks the wave-3 journey test will drive.
### Changed
- **The workspace's finish pass (redesign wave 2, lane FINISH; verdict 2 punch 9, REDESIGN-GOAL dimension 9).** No chrome is cut off any more at 1440×900 or 1920×1080: the Project panel's Status column fits "not indexed" (list type 11 px, Duration 80 / Status 78 px columns); panel tabs are 11.5 px with an accent underline on the active one and never ellipsize (Effect Controls' five tabs with two badges fit its default width); the app bar's running jobs keep their numbers whole ("9% · ~7 min left"), drop their bars before their names when two share a narrow row (a container query on `#progress`), and Next steps back to a shorter chip while something runs below 1700 px; the monitor readouts are never cut ("In–Out 00:0…"), and the Program's shot line shows only where its panel has room; the Roughcut mark goes icon-only below 1600 px. Links are the app's own text buttons (accent, no underline); an empty state's next action is a button. The ▶ on the Program picture shows only on hover over a paused picture. The default layout is rebalanced (top row 0.42 of the height, Project 0.195, Effect Controls 0.255; dragged sizes still win) so the Program picture is 386×217 with 26 % letterbox at 1440×900 (was 389×219, 51 %), and the Timeline's empty sequence area is drawn in the track colour. /open's cards use the Project panel's own `/media/poster` URL (from the original before a proxy exists), so no card is blank stripes. The inline `<style id="workspace-css">` moved to `app/static/workspace.css`, served at `/modules/workspace.css` and linked in its place (same cascade order). `test_workspace.py` gains the overflow, link-colour, Program letterbox (logged in REDESIGN-GOAL's rubric log as the replacement for the 425 px floor) and /open poster checks; `test_budget.py`'s monitor test gets the same floor; `test_open_ui.py` expects the poster instead of the stripes.

### Added
- **The first pass has a home in the workspace: Run / Re-run first pass in the header (redesign wave 2, punch 1; Karl's K1).** Verdict 2: the server had `GET/POST /api/firstpass` and no file under `app/static` called it. `/modules/firstpass.js` puts one control in the header's `#firstpass`: "Run first pass" (never run), "First pass · heard 3/12 · looked 0/12" with a × to stop it (running: the API's counts, never a %; "ranking the picks" / "writing the cut" on the later steps), "Re-run first pass" (done; a dot when shots were edited by hand since). One click opens its panel: the steps a run takes now, each with its price on this backend ("free on your plan", the API estimate in the hover), "Keeps N shots you edited by hand · may replace M" with the kept shots named ("shot 1 · CLIP_A 0:00.5–0:02.1 · trimmed") and a box to let it replace them too, then Run. When the result lands it opens in the Assistant as an ordinary proposal (Play it / Accept / Discard), with the re-run's one-line diff above the notes; app.js gains one hook for that, `window.rcShowProposal(plan)`. The Timeline's and the Program monitor's empty states offer the same button; anything can open the panel with `window.rcFirstPass.open()`. New `POST /api/firstpass/cancel`: it stops between steps (an index it is on stops after the clip-stage in hand and ends `cancelled`, the journal keeping what finished), a proposal being written when it is stopped is discarded rather than offered, and the pass's record goes back to what it was before the run. Provenance: the board's Accept saves the cut and answers the proposal in two racing requests, and when the answer landed first the accept recorded no base, so a later trim read as "added by hand"; an "accept" answer now still counts as that save's. `app/tests/test_firstpass_ui.py` (4) drives a fresh bin, mid-index (mocked), a stop while the cut is written, and run → accept → hand trim → re-run → accept, asserting the control on `/` with its panel one click away in each state, the trimmed shot named as kept, and its in/out unchanged in the accepted re-run.

### Fixed
- **A render's header pill shows its work, not the clock (redesign wave 2, punch 6, after lane EXPORT).** Render jobs carry a work-based `progress_pct` (from what the encoder has written) that the Export dialog draws, while the header pill drew the job's clock-creep `pct` (23 % beside the dialog's 28 %, and able to run ahead of the encoder). `progress.Job.percent()` now holds any job that carries `progress_pct` to it, so the pill and the dialog agree and no pill runs on a clock. firstpass.css no longer overrides the job strip's sizes (lane FINISH's workspace.css owns them). `test_progress_honest_ui.py::test_a_render_pill_shows_its_work_not_the_clock`; `test_firstpass_ui.py`'s waits doubled after one timeout at load 16 (passed 4 runs since); test_progress, test_export_dialog and the render tests in test_server / test_ui_flow green.
- **Progress never runs ahead of the work (redesign wave 2, punch 6).** Verdict 2: the header's job pill said "18 % · ~1 min left" 7 s into an index that had heard nothing and "90 %" for 12 minutes (the index job carried a fixed 60 s estimate labelled "measured"), and /open said "about 0:07 left" at 6 min with 0 of 12 ready. The index job has no clock estimate now; `progress.Job` gains `bound_fn` (a ceiling the counts prove) and `eta_fn`, and `flow.honest_index_job` holds the job's % to `flow.honest_pct` of the journal's settled clip-stages (heard, looked at, looked at closely, previewed; never probes or telemetry), and its time left to the journal's measured per-stage means, hidden until every counted stage has finished on a clip and never under the run's own pace (`elapsed · (1 − f) / f`). /open's progress bar uses the same share and its "left" is the job's. In the header the Now chip gives way first (`flex-shrink: 100`), and the job strip keeps a 200 px basis instead of collapsing to 0 and spilling over the buttons. `app/tests/test_progress_honest_ui.py` (2): with listening held at heard 0/3, every % and bar on `/` and `/open` is ≤ the honest share, no ETA shows, and the header's children do not overlap at 1440×900 (it fails on the old estimate: a 19 % bar at 0 %).

### Added
- **The Export dialog (redesign wave 2, verdict 2 punch 5, REDESIGN-GOAL dimension 8).** The judge: *"Ctrl+M does nothing: there is no export dialog with a name, folder or size."* Ctrl+M (⌘M on a Mac), File › Export… and the header's Export now open one modal (`app/static/export.js` + `export.css`): a preset (Quick 1080p, Master 4K, YouTube 1080p), a file name defaulting to the bin and cut, a folder under `--work/renders` (the bin's own by default, any other one name; never an arbitrary path), the range (the whole sequence, or its In/Out when set), the duration in timecode and an estimated size from the preset's bitrate at the frame the bin renders at. Export shows a real % (`aria-valuenow`, from ffmpeg's own `-progress` against the sequence's length, never the clock) and a Cancel Export that kills the encoder's process group and leaves nothing on disk; it ends with Play and Show file (the path). Escape closes it before the start and after the end. Server: `GET /api/export` (presets, default name and folder, existing folders); `POST /api/render` also takes `{preset, name, folder}`, sanitises name and folder to one path component each, writes `--work/renders/<folder>/<name>.mp4` (a clash becomes `-2`, `-3`; written under a hidden `.partial` name and renamed when whole) and answers `{job, path, name, folder, preset, planned_s}`; `GET /api/render/<job>` adds `progress_pct`, `encoded_s` and `path`; `POST /api/render/<job>/cancel`; `/media/export/<folder>/<name>` for Play. `assemble.py` gains the `youtube` profile (1080p cap at the bin's own rate, crf 18 medium, AAC 384k) and `--progress` (ffmpeg `-progress` beside each part). Exports into the bin's folder are listed with the other films. Stable test hooks: `export-dialog`, `export-preset`, `export-name`, `export-go`, `export-progress`, `export-done` (`data-path`), `export-cancel`.

### Changed
- **The header's Export always opens the Export dialog.** It used to turn into "↓ Download · 1080p · 0:04" once a film of the cut existed; the Download is the Export panel's (and the dialog's Show file), and the button's hover names the cut's last film. The film tool's Quick look / Final 4K stay in the Export panel (Window › Export). `test_ui_flow` and the budget's "/ film" state follow (the budget opens the panel through Window › Export).

### Added
- **Real tracks: V2 V1 A1 A2 with linked camera sound and track headers (redesign wave 2, verdict 2 punch 3).** The judge: *"The timeline is one video track with no sound tracks… Two-thirds of the timeline panel is empty grey."* The cut is still one magnetic list, and each shot is now two clips: its picture on V1 and, linked, its clip's own sound on A1 (green, the transcript's speech drawn as bands), so a trim, the razor (C), a ripple delete and a move act on both. ⌥-click on either half unlinks the pair (again relinks, keeping any offset); an unlinked sound drags alone along A1 (`a_off`, frame-snapped, back into sync near 0) and both halves show Premiere's red offset. The music bed moved to A2. V2 holds clips laid over the cut (`overlays`, picture only): `,` / `.` lay the Source clip there when V2 is targeted and V1 is not; drag moves one, X / ⌫ removes it. Track headers in the side column: lock · name (target) · eye for V1/V2, lock · name · M · S for A1/A2; a locked V1/A1 refuses edits of the cut with a toast, mute/solo/eye dim their rows, the Program monitor's videos follow A1's level and the bed follows A2; header toggles are not undo entries (as in Premiere), V2 edits are. The rows fill the Timeline panel and resize with it (at 1440×900 the four rows span ≥ 80 % of the panel). Blocks are labelled with the clip name (the transcript line is the hover). Model and validation: new `roughcut/tracks.py`.
- **`/api/project` carries the track model.** GET adds `tracks` (the headers in full), `overlays`, `markers`, `in`, `out` and `fps` (the most common frame rate of the cut's clips, ffprobe via the cached `media_facts`). PUT validates and stores `tracks` (only what differs from the defaults), `overlays` (ids minted), `markers`, `in`, `out`, and a shot's `link` / `a_off`; every bad value is a 400 that writes nothing; a body without `segments` leaves the cut alone (the timeline saves its headers, V2 and markers that way, debounced). An old single-track EDL loads unchanged as V1 + linked A1.
- **The render honours the tracks** (`research/tools/assemble.py` `tracks_pass`, one extra pass only when a track is not the default): A1 muted or soloed out is a silent film, A1's gain applies, unlinked offsets are laid by delaying each part's own sound and mixing; V2 clips are cut like parts and composited over V1 for their range; V1's eye off is black under V2; A2 muted drops the bed and A2's gain adds to it. Video is stream-copied when only the sound changed.
- **The timeline's keys leave a modal alone, and its type sits at 11–12 px.** `timeline-keys.js` and the foundation's keys ignore events inside an open `<dialog>` (Enter / Space on the Export dialog's buttons drove the timeline — lane EXPORT's request); timecode 12 px, tool button 12, ruler labels, headers and clip labels 11. `test_budget.py`'s board budgets re-based for the 14 header controls and ~9 words (logged in REDESIGN-GOAL's rubric log, measured +~10 %).
- `app/tests/test_tracks.py` (25): model validation, old-EDL load, PUT/GET round trip of tracks/V2/markers/in/out/link, eight 400s, render (A1 mute → max_volume < −80 dB; a 1.5 s offset → the first 1.3 s silent; V2 black over 1–2 s only), and the board (V1+A1 per shot, insert makes both, C cuts both, ⌥-click unlink then A1 moves alone and saves, linked A1 drag moves the pair, headers save and lock refuses, bed on A2, V2 via the API, markers reach the server, rows ≥ 80 % of the panel at 1440×900 with headers aligned).

### Added
- **Effect Controls as Premiere has it (redesign wave 2, lane EFFECTS; verdict 2 punch 4).** Selecting a shot shows twirl-down sections: Motion (Position x/y in a 1920×1080 frame, Scale, Rotation), Opacity, Time Remapping (the shot's speed), Volume (Level dB), Lumetri Color (Temperature, Tint, Exposure, Contrast, Saturation; the warmer/cooler nudges and "Creative & match" for the auto, look, strength and match) and the designed effects' stack (fx bypass and order). Every value is a blue number you drag sideways, click to type or step with ↑/↓, with a reset; each change is one undo entry. The head reads the clip and its source In/Out in timecode. The Program monitor answers on the next frame: Motion and Opacity as a CSS transform / opacity on the picture's layers, Volume on the live video, and the colour through `grade.js`'s shader, which now previews the balance the LUT was not baked with (a gain before the table, plus its shoulder and lift) and contrast/saturation after it. Verdict 2's "warmer" took 1012 ms because it waited on the 700 ms autosave, a refetch and a LUT bake; it now shows within a frame and does not jump when the baked LUT lands (`/api/lut` says which balance it baked). Temperature/Tint/Exposure are the colour block's hand balance (the render already honours them); the rest ride the segment as `controls` (only non-defaults), validated by `server.py`'s `clean_controls`, and do not render yet (their rows say so on hover). `fx.js` skips bypassed effects and draws in the stack's order. New `app/static/effects.js` / `effects.css` (`/modules/`). `test_effect_controls.py`: 19 passed (each property within 150 ms of the input, one undo per change, a drag scrub, a reload keeps every value, the server rejects bad controls, the fx stack); `test_colour_ui.py` + `test_fx_ui.py` + `test_colour_api.py` + `test_modules_route.py` 42 passed; `test_ui_flow.py` 67 passed.

### Added
- **`/modules/<name>`: one route for the workspace's new modules (redesign wave 2 scaffold).** Every static file had its own hand-written route, so a new panel meant a `server.py` edit (the WORKSPACE lane had to inline its CSS). `MODULE_FILES` is a fixed list, served by name like `/timeline/<name>`, never by directory. The board now loads `firstpass.js/.css` and `export.js/.css` (stubs for the wave-2 lanes) and carries their mount points: `#firstpass` in the header after `#flow`, and `<dialog id="exportDialog">`. `test_modules_route.py`: 3 passed; `test_workspace.py` still green.

### Added
- **Redesign verdict 2: FAIL, up from the baseline.** `judge/verdict-2.md` on `548e3b4`: workspace 6, monitors 6, orientation 5, footage-in 5, keys/timeline 5; still lowest: first pass re-runnable (K1) 2 (the API exists, no UI calls it), journey 2, Inspector 3. Its punch list drives wave 2.

### Fixed
- **The bin menu no longer says "spent" on the plan.** `#placeSpent` in `switcher.js` read "spent $x on this project" on every backend; it now uses `rcCost.total` / `totalNote` like every other price ("~$0.27 API-equivalent · free on your plan on this project", the estimate in the hover; "spent" only on the API key). `test_price_wording.py`'s bin-menu test is no longer an xfail, and `test_flow_ui.py` expects the backend's own words. 17 passed.

### Changed
- **The workspace on the merged lanes (KEYS, MEDIA, MONEY, FIRSTPASS).** The Program and Source monitors and the Project panel's durations read Premiere's HH:MM:SS:FF (`tl.tc`); the save carries the timeline's markers and sequence In/Out; the keys sheet takes each timeline row's group from the row itself and drops "O · bins and cuts" (O is Mark Out now). A monitor whose media is not ready keeps the poster at the playhead with "preparing preview…" and polls `/media/status/<stem>.mp4`, reloading the same `/media/proxy` URL once it is ready (a play pressed meanwhile waits rather than failing); the Project panel no longer gates anything on the proxy flag. The monitors are exact 16:9 boxes centred in their panels, so the grade and effect canvases and the sketch's clicks line up with the frame, and the effect sketch's HUD is one line along the top. UI tests follow the workspace (Selects opened where they drive the bin, timecode readouts, the calm media wait); the budget's monitor check and `/` word count are re-based and logged in REDESIGN-GOAL.

### Fixed
- **The pre-commit hook is executable in git, not only on one disk.** `.githooks/pre-commit` was tracked as 100644, so every fresh checkout or worktree ignored it (the FIRSTPASS lane saw git's "hook was ignored" hint). Now 100755. An earlier entry wrongly said this was already done.
- **Index progress is never ahead of its counts (verdict 1: "Indexing — 87%" at heard 0/12).** The running index took the journal's own % (settled stages over all stages, which counts probes and skipped telemetry as work done). The flow's index progress is now the smaller of that and the share the counts prove — clips heard, looked at, looked at closely and previewed (`flow.honest_pct`); the stage's counts carry `proxied`, and the wait line reads "Indexing — heard 0/12 · looked at 0/12 · previews 0/12 (0%)". `test_index_progress_is_never_ahead_of_the_counts`, plus a 300-case bound test.

### Added
- **The board is a Premiere-style workspace (redesign wave 1, punch 3 and the client half of punch 5).** Five titled panels in Premiere's editing arrangement: Project top left (every clip in the footage folder from `/api/clips`, list view with name / duration / status / when shot — frame rate and resolution when the server reports them — or icon view, a search box, drag a clip onto the timeline; the pass's keeps are its Selects tab), the Source monitor top centre (the clip picked in Project, its own transport, In/Out marks kept per clip with I / O / space / ← → while it is focused, Insert into the sequence, `window.rcSource = {clip, inPoint, outPoint}` and an `rcsource` event for the insert/overwrite keys), the Program monitor top right, the Timeline along the bottom (re-parented, its lane height fitted to the panel), and Effect Controls down the right with Effects · Audio · Assistant · Export as neighbouring tabs where the BIN / ASK / SOUND / FX / FILM rail was. Every splitter drags, double-click resets, and the sizes persist per browser; a menu bar (File · Edit · Clip · Sequence · Window · Help) names every panel and reaches every tool; the focused panel carries Premiere's blue outline. One dark, dense theme; no page scroll at 1440×900 or 1920×1080. The empty sequence's first cut lives in the Timeline panel; a proposal opens the Assistant tab instead of scrolling the page. Monitors never show raw media errors: a video that will not play keeps the poster at the playhead with "preparing preview…" and asks `/media/status/<stem>.mp4` every ~3 s, reloading the same `/media/proxy` URL when it is ready; no `<img>` is ever drawn broken (a failed one shows a neutral frame until its picture loads). New `app/tests/test_workspace.py`; `test_budget.py`'s board budgets raised (logged in REDESIGN-GOAL's rubric log) and `test_dock_ui.py` follows the tabs.
- **The first pass is one re-runnable step on the server: `GET` / `POST /api/firstpass` (REDESIGN-GOAL dimension 2, Karl's K1).** Karl: *"it is not clear how to re run the first pass."* The pass was the index on /open, the culling on /floor and a "Make a first cut" that vanished once a clip was on the timeline. `GET /api/firstpass` says where it stands (`never-run` / `running` {stage, steps, counts} / `done` {when, produced, proposal} / `stale-after-edits` / `failed`), what a run would do step by step (index what isn't done, picks, a proposed cut), which shots a re-run keeps and which it may replace, and the cost on this backend ("free on your plan" on `claude_cli` with the API estimate in `hover`; "~$x.xx" on `anthropic_api`). `POST /api/firstpass {mode: run|rerun, keep: [ids]|"manual"|"none", note}` runs index → picks → proposed cut as one job, reusing the index job, the picks builder and the Ask's own call. The result is an ordinary Ask proposal (Accept / Discard / Play it work unchanged) whose plan carries `firstpass.diff`: per shot `kept` (with `locked`) / `replaced` / `new`, plus `removed` and a one-line `sentence`. A re-run locks every shot edited by hand by default and puts each back exactly (same in/out) whatever the model returns (`firstpass.enforce_locks`). Which shots are the editor's own is a diff, not a log (`roughcut/firstpass.py`): every Accept records a base in the EDL's `provenance` block, and a shot trimmed, razored, moved, re-sped or added since is the editor's; an undo back to the base makes it the machine's again.
- **The AI's notes are held to the diff (verdict 1, punch item 10, server side).** Before any proposal is saved (Ask, first cut, re-run), `revise.check_notes` checks every "kept / unchanged / as it was" sentence against the ranges: a named shot (by clip or number) that changed or was removed, or for a broad claim ("the rest is unchanged") any change the notes do not mention, is flagged in `plan.notes_check` and corrected in the notes in numbers ("Check: shot 3 (CLIP_11) changes 2:39.2–2:46.2 → 2:39.2–2:44.9 (out -1.34 s) — the notes call it unchanged."); the model's words stay in `notes_model`. Every plan carries `diff` (per shot kept / changed / new / removed). The cause verdict 1 saw is fixed too: boundary polish no longer moves a shot the model returned verbatim from the cut (`revise.polish_kept_alone`).
- **Picture at once: posters and playable media before any proxy exists (redesign verdict 1, punch 5, server half).** Verdict 1: every bin card was a broken image and the monitor showed a raw MEDIA_ELEMENT_ERROR until each clip's 720p proxy finished, about 8 min a clip on Killington under load. Now `/media/poster/<stem>.jpg[?t=]` cuts its frame from the proxy, else the new 360p preview, else the original (same rotation rule and HDR tone map as the proxy, CUDA decode for HEVC/above-1080p), and never 404s for a clip in the bin. A walk that starts when a bin opens makes the t=0 posters two at a time, and a card asking for a poster the walk is making waits for that build. `/media/proxy/<stem>.mp4` with no proxy yet answers 307 to `/media/original/<stem>.mp4` when the browser can play the file as it is (h264/aac mp4, 8-bit 4:2:0, up to 1080p and 25 Mb/s, one cached ffprobe), else to `/media/preview/<stem>.mp4`. That is a 360p h264 file with a keyframe every 0.5 s, built on the GPU (NVDEC + scale_cuda + NVENC, about 5.5x realtime; libx264 ultrafast when there is no GPU). The request waits up to 20 s for it, then gives a 503 with retry-after. A redirect, so a player never gets proxy bytes at preview offsets when the proxy lands. The new `/media/status/<stem>.mp4` says what plays now. The clip a monitor asks for goes first in the preview queue and in `ensure_proxies`. 720p proxies get a keyframe every 1 s (was x264's 250 frames). Measured on killington-neutral at load 16: 12/12 posters from a cold work dir in 6.2 s (first seven in 2.7 s). An asked-for 4K HEVC clip is playable in 62 s, against about 8 min for its proxy. Chromium on the preview: first frame 120 ms, seeks 41-122 ms. Cost: about 1.5 s of CPU per 20 s of footage on the GPU path, so the pass's CPU stages are not slowed. Without a GPU, previews are built only for clips the user asks for. Tests: `app/tests/test_media_first.py` (12).
- **Premiere's keymap, markers and timecode on the timeline (REDESIGN-GOAL dimension 5; verdict 1 punch 7, and the markers and timecode of punch 6).** `,` inserts and `.` overwrites at the playhead from the source: the Source monitor's `window.rcSource = {clip, inPoint, outPoint}` when the workspace publishes one, else a clip open in Find with both marks, else the bin's selected keep (the range "+ add" uses). Insert splits the shot under the playhead and pushes the rest later; overwrite covers the film from the playhead for the clip's length (shots inside removed, the two at its edges trimmed); each is one undo entry and the playhead lands after the new shot (new foundation API `tl.insertAt` / `tl.overwrite`). The 1-frame edge nudge moved from `,` `.` to Alt+←/→ and Ctrl+←/→ on the selected edit, a frame at the sequence rate, Shift for five. The tools: V selection, C razor (and still a cut at the playhead), B ripple edit (both halves of an edge trim), N rolling edit (both roll), Y slip (drag the body, no Alt), A track select forward; Ctrl+K adds an edit at the playhead; Shift+Del ripple delete stays. The timeline's new side column shows the playhead's timecode and the tool in hand (one button; the six are its menu). I / O mark the sequence's in / out at the playhead, shaded on the ruler (a clip open in Find is still the source while the timeline was not the last thing clicked); Alt+I / Alt+O / Ctrl+Shift+X clear. M adds a marker on the ruler, Shift+M / Ctrl+Shift+M jump to the next / previous, Ctrl+Alt+M clears; a click on one goes there. The ruler, the end mark and the readout read HH:MM:SS:FF at the sequence rate (23.976 unless the project sends `fps`; frame steps when zoomed in). A plain wheel scrolls the timeline, Alt or Ctrl + wheel zooms. The arrows step a frame at the sequence rate, Shift five. The key map, the undo tooltips and the trim hints say Ctrl / Alt / Shift off a Mac and ⌘ ⌥ ⇧ only on one. Markers and in / out persist per project and cut in localStorage until the EDL carries them (server and app.js requests in the lane report). `app/tests/test_keys_premiere.py` (14 tests); the timeline suites follow the new keys and the timecode.
- **Redesign verdict 1 (baseline): FAIL.** `judge/verdict-1.md`: scores 1-4 of 10 across the twelve dimensions; the three biggest gaps to Premiere are the layout (one monitor, one video track, no Source monitor), no picture until proxies exist, and a first pass with no home or re-run. Its punch list drives wave 1.
- **The redesign loop: REDESIGN-GOAL.md, judge/JUDGE.md, judge/LOOP.md (branch `redesign/2026-10-14`).** Karl tried the closed-loop build on 2026-10-14: *"it is not clear how to re run the first pass, and this is not something that I feel meets 'premier pro quality'. The system might have to do a complete redesign."* The new bar: a Premiere Pro user opens Roughcut, understands it at once and would choose to cut in it, with the AI first pass as a visible, re-runnable step inside the editor. Twelve dimensions (first five minutes, Premiere parity of the editing core, quality of feel, the AI part as reviewable edits, a scripted end-to-end journey), section S standing decisions and section K for Karl's notes. A fresh, harsh judge agent scores a frozen rev every ~30 minutes by driving a snapshot server on port 8781 with Playwright; two consecutive PASS verdicts close the loop. ROUGHCUT-GOAL.md is marked superseded and carries Karl's note.

### Docs
- **HANDOFF points at the closed loop** (ROUGHCUT-GOAL.md, tools/gate.py, LOOP-LOG.md on `closed-loop/2026-10-13`) and corrects its 2026-10-05 entry: M16 is on main and live, not waiting on a deploy.

### Fixed
- **The gate gets its own Chromium before the suite (verdict 1, gate row).** A Playwright bump wanted Chromium rev 1248, which was not on the box, and 37 `test_budget.py` cases "failed in the full run, passed alone": counted as flaky when the cause was the machine. `tools/gate.py` now runs a `browser` check first: `uv run --with playwright python -m playwright install chromium` (the same resolution as the suite's `--with playwright`), then one headless launch, all logged to `gate-*/browser.log`. If it cannot (no uv, the download fails or times out after 900 s, the browser will not launch) the gate is red with one line, "cannot get a Chromium for this Playwright: …", and suite and screens are marked not run instead of producing a wall of browser failures. Measured: from an empty browsers dir it downloaded rev 1248 and launched in 27 s; with it already there the step is a no-op.
- **No "spent" on the subscription backend (REDESIGN-GOAL S; verdict 1 punch item 1, a blocker).** /open's index progress line read "Indexing · 0 of 12 ready · about 0:10 left · spent $0.27" on `claude_cli`, where nothing is billed (`charged: false`); the how line at rest and the paused-at-cap words said "spent" too. On the plan they now read "~$0.27 API-equivalent · free on your plan" (with a cap: "~$0.49 of $15.00 cap (API-equivalent) · free on your plan"), and the hover says what the estimate is; on `anthropic_api` "spent $0.27" stays. The settings drawer's cap hint says the same. Starting an index re-reads the backend first, so the progress line uses the current terms from its first paint. `rcCost.total` / `planTail` / `totalNote` in cli.js are the shared words. `app/tests/test_price_wording.py` serves the fixture with $0.27 of project spend and asserts no visible "spent" on /, /open (mid-index and at rest) and /floor, and that the API backend still says it; the bin menu's "spent $x on this project" (switcher.js, another lane's file) is an xfail until its owner fixes it. `test_open_ui.py::test_resume_releases_every_clip_and_the_page_goes_quiet` asserted the old "spent" on the plan and needs its expected line updated (requested of that file's owner).
- **The board's Bin is the footage until something is kept (ROUGHCUT-GOAL.md dimension 2).** A bin nobody had passed opened the editor on "nothing kept yet — the pass is where you keep things" and nothing to put on the timeline: the editor could not edit without the pass first. With no keeps the Bin now lists every clip (junk-confirmed and generated ones aside) as a whole-clip card — the same row, + add at the playhead, drag, Enter — with "keep moments on the pass →" as one line above. `test_an_empty_bin_offers_the_footage_and_says_where_to_keep_things` adds one and undoes it. `test_budget.py`'s words in view are raised for it, in review: / 80→92, / shot 130→143, / jobs 108→121 (the three fixture cards' first lines, +12 words; controls unchanged — + add shows on hover).

- **The footage cards read as footage.** The judge's look at the gate's `board.png`: the "nothing kept yet" line took a card's slot in the Bin's two-column grid, and the tab said "not in the cut · 0" over three cards. A hint in the grid spans both columns now, and with no keeps the tab reads "footage · N".
- **A click on a clip on the footage screen plays it there (ROUGHCUT-GOAL.md dimension 2).** /open showed every clip as a picture and none of them did anything when clicked. A click on a picture plays the clip's proxy in its card, with controls; another click puts the first picture back (one plays at a time). A clip without a proxy yet stays a picture. `test_a_click_on_a_clip_plays_it_in_its_card`.
- **The board's header buttons work from the first paint.** Make the film, undo, render, Ask, the proposal's Play / Accept / Discard and Find's buttons were bound only after three more awaits (the renders, the last proposal, the jobs): a click in that window did nothing. The gate's "/ film" state timed out on it twice at load 22, and over a network the window is longer. They are bound with the tabs, before the awaits; `test_make_the_film_works_before_the_renders_have_loaded` holds /api/renders forever and still opens the film tool (fails without the move).
- **The seen tab's empty state links to the footage screen (ROUGHCUT-GOAL.md dimension 8).** It said "or look at more of the footage (Project panel)" — a panel INTAKE M16 removed, so the line named a place that is not there. It links to /open, where the look's granularity and the index are.
- **A footage card names its clip.** Three cards all labelled “hello there” (the fixture; on a real bin, any clips that open on the same words) could not be told apart: a whole-clip card reads `CLIP_A · “hello there”`.

### Added
- **ROUGHCUT-GOAL.md, the closed-loop bar, and `tools/gate.py` (branch `closed-loop/2026-10-13`).** Karl, 2026-10-13: *"next time I open Roughcut I want to actually use the video editor and feel good about it"* — the editor's equivalent of NOITA's and STICKFIGHT's "ready for Steam" bar: eight dimensions (opens fast, footage in, cut/trim, preview, nothing lost, a film comes out, quiet and plain, no dead ends), each with a pass line a fresh judge checks from the gate's screenshots and the suite. `python3 tools/gate.py [ref] [--quick]` freezes a commit into `~/roughcut-loop/snap-<sha>` and runs the suite (a full-run failure that passes alone is named as flaky), `test_budget.py` with a PNG per screen, and a hygiene grep (model literals, `anthropic` imports, Windows paths); `summary.md` per run. `LOOP-LOG.md` is the commit-by-commit record. `test_budget.py` now times each screen to its Next (≤ 3 s, dimension 1; 285–457 ms measured) and fails on any uncaught page error.

### Changed
- **On the subscription backend no price reads as spending money (Karl, 2026-10-05).** The
  default `claude_cli` backend runs `claude -p` on Karl's Claude plan and bills nothing, but
  the audit, Ask, Look deeper, Find's *Ask the model*, FX and Index buttons said "~$0.58" —
  enough for an agent driving the board to read the audit as a real-world purchase and stop
  for a click. Now: `config.charges_money()` / `config.cost_terms()`; every price response
  (`/api/ask/price`, `/api/find/price`, `/api/fx/price`, `/api/visual/audit`, the deep quote,
  `/api/status`'s visual pass and audit, `/api/backend`) carries `charged` and `backend`; one
  client helper, `rcCost` in cli.js, words every button: "free on your plan", with the
  estimate in the title as "about $0.58 estimated cost if using the API". On `anthropic_api`
  the dollars stay. The next-action sentences (flow.py) follow the same rule. CLAUDE.md gains
  the standing rule: running these is part of the loop, never a pause for approval.

### Docs
- **INTAKE M16: I16.0–I16.5 ticked on the integration branch** with their merge commits, the fixture measurements in the Verification log (board 197→72 words in view, the pass 350→52, /open 454→53; 861 passed), and the two questions the review left for Karl (Cut from the bin once a cut exists; junk answered in two places). I16.6–I16.7 wait on Karl deploying main; HANDOFF gives the two deploy commands.

### Added
- **`test_budget.py`, the screen-size ratchet (INTAKE M16 I16.1).** Every earlier answer
  to "it is hard to use" added a surface; this fails when one grows back. At 1440×900 on
  the fixture bin, read-only (GETs, and Look deeper's two price-only POSTs so the shot's
  strip reads its price as the screen does): one header row ≤ 50 px holding the
  switcher and Next side by side, exactly one `.is-next` and the expected one (none
  while a round of the pass is open), the board with no page scroll, no step bar, and
  words / controls in view under each screen's and state's number. It measures the
  three screens and the board in the states the M16 end state names — a shot selected
  (its strip in view), the film tool open, FX after Next (landed on a waiting slow
  motion) — and with jobs running. Measured on the merged M16 screens, words · controls
  in view: / 72 · 18, /floor 52 · 7, /open 53 · 8, a shot 118 · 30, the film 67 · 17,
  two jobs running 98 · 18, FX after Next 118 · 31 (before stage 1: / 197 · 26, /floor
  350 · 12, /open 454 · 19); the limits are those plus 10 %, so growth fails. `AWAITS`
  (expected failures while a lane was in flight) is empty. Jobs another module finished
  (they lingered 12 s) are cleared before measuring — after `test_ask_*` they made the
  board's header 243 px and +87 words. `ROUGHCUT_BUDGET_SHOTS=<dir>` saves a screenshot
  of each state.

### Changed
- **The open screen's one blue is tested on the merged page (INTAKE M16 integration,
  C2).** Lane open marked *Index the footage* `data-next-for="index footage"`; lane nav
  placed the blue. The contact-sheet test now checks both: with every clip heard Next
  is the first cut on the board, so the chip is the blue; with Next on the index, *Index
  the footage* is, and nothing else.
- **A waiting proposal's ▶ Play it is tested as the board's one blue (INTAKE M16
  integration, C2).** Lane shell marked *▶ Play it* `data-next-for="cut"` and lane nav
  placed the blue, each tested alone. The Play it test now checks, merged, that with an
  Ask proposal waiting Next's stage is the cut and *▶ Play it* — not the chip, not a
  first-cut button — is the single `.is-next`.
- **The CLI banner is one line above the header (INTAKE M16 I16.1, C1).** It wrapped to
  two or three lines at the top of every screen when the reason was long. It still sits
  above the page, never in the one header row, and only while the CLI needs Karl; now on
  one line (≤ 44 px at 1440 wide), the reason cut short with the whole of it on hover,
  the command and Copy / Check again always in view.
- **The bin · cut menu is the way between the screens (INTAKE M16 I16.1, decision 2).**
  With the step bar gone, its first rows are the places — *The footage* (/open, "3 clips ·
  indexed"), *The pass* (/floor, "40 not watched · 44 kept") and *The cut* (/, "19 shots ·
  3:09"), the current screen marked — carrying the facts the board's Project ▾ held (the
  index summary and the footage and project paths on hover; "the footage folder is not
  there" or a missing tool only when so). A bin nothing has been heard or looked at in
  opens on /open, where indexing starts. Taken away from the menu: the two paragraphs
  explaining copies and paths (~60 words), the "journal" flag on a bin that is fine, the
  "opened before, or a folder of video next door" sub-line, and Save copy's blue (a plain
  button: the one blue is Next's). The header names the bin it moved to from the
  server's own answer, before the page reloads.
- **On the pass mid-round, Next is the round: "N left" (INTAKE M16 I16.1, C3).** Next
  on /floor said what waited on another screen ("1 effect proposed — accept or
  discard"), pulling Karl off a round he was in. While the round has undecided moments
  the chip reads "3 left", plain, nothing on the pass is blue (P / X / U are the choice)
  and a click on the chip stays put; when the round closes, Next is Next again. The count
  is the pass's own (`window.passRound()` when the pass offers it, else its frozen
  queue), read again each second and whenever the pass repaints.
- **One blue button per screen, chosen by Next (INTAKE M16 I16.1, decision 3).** Up to
  four buttons per screen were blue (Render, ▶ Play cut, Ask, Accept, Index, Open the
  pass…), and the Next chip was a fifth. `.is-next` is now the only primary style, and
  `/flow.js` puts it on exactly one element: the first visible one a screen marks
  `data-next-for="<Next's stage>"` (narrowed to `data-fx` when Next names an effect),
  else the Next chip, which is plain otherwise. It is placed again when the screen
  changes under it (a tool opens, a card arrives). Next's free action on the board is
  now the film tool's quick look — `click: '[data-next-for~="render"]'`, worded "Make
  the film — quick look" — pressed after the tool opens; the header's Render is going
  (lane shell).

### Removed
- **The ghost lane's "play proposal / play cut" buttons (INTAKE M16 review, decision 1:
  anything new names what it replaces).** ▶ Play it (I16.4) beside Accept and Discard
  plays the proposed cut — the same `playPlan(0)` the lane's *play proposal* called — and
  the monitor's own ▶ / space plays the cut, which is what *play cut* did; a waiting
  proposal showed two controls for each. ▶ Play it replaces them; the lane says
  "proposal". Its ghost blocks are named the way I16.4 names V1's (`tl.blockName`: the
  first words, a slide by its kind) instead of "CLIP_01 12.9s"; the length and the file
  stay in the tooltip, and a retimed shot keeps its "2×". The lanes test checks the
  label, the names and that ▶ Play it and the monitor's ▶ do the two jobs.
- **The seven-step bar, on every screen (INTAKE M16 I16.1, decision 2).** M14 drew
  Footage › Index › Brief › Pass › Cut › Polish › Render as seven chips with ticks,
  counts and "stale" at the top of /, /floor and /open; on Killington it was the third
  header row on the board and Karl answered Q1 "the bar comes off". `/flow.js` now draws
  only the **Next** chip into `#flow` (the one "what now"), keeping its landing
  (`honourHash`, `land`, `go(target)`) and its free-action press. `GET /api/flow` still
  computes the stages: Next is chosen from them. On the fixture bin at 1440×900 this takes
  7 controls and ~20 words off each screen. Tests read a stage's state from
  `flowBar.state()` instead of the bar.

### Docs
- **INTAKE M16, take things away (Karl, 2026-10-04).** The fourth report that the flow is hard (*"a bunch of buttons and text from the footage -> the other steps"*) gets the opposite of the last three answers: remove, don't add. M16 records Karl's eight answers from the proposal page (Subtract; the step bar off; Next lands on its target; the two-first-cuts test before the pass changes; clickable P/X/U; click selects and parks; no budget cap by default; themes dropped; one line for how the machine saw), the measured baseline, the end state the check-ins measure against, and items I16.0–I16.8. HANDOFF points at it.

### Fixed
- **/open and the bin · cut menu use one word for a bin heard but not indexed (INTAKE M16
  review, I16.2).** A bin whose words had been heard (sidecars on disk) but that no index
  had run on read "3 clips · 0:18 · not indexed yet" on /open's headline while the menu's
  footage row said "3 clips · partly indexed" for the same bin. The headline now says
  "not indexed yet" only when nothing has been heard or looked at — the menu's rule — and
  "partly indexed" otherwise. Which button is blue there is Next's precedence (M14) and
  is unchanged. The menu test reads both on one server (the headline fails on the old
  page).
- **The board's keys sheet fits on screen (INTAKE M16 review, I16.4).** Grouped by task in
  one column it ran 959 px inside a 772 px box: "THE BIN" sat over a half-drawn row at
  the bottom edge, and nothing said the box scrolled. The four groups now sit in two
  columns, each group whole, as the pass's key map does, in a 900 px sheet. The keys
  test checks the sheet has nothing below its fold and that the groups take two columns
  (it overflowed by 187 px before).
- **The board's monitor is centred and a little bigger (INTAKE M16 review, I16.4).** At
  1440×900 the picture was 716 × 403, left-aligned in a 1012 px panel — a ~270 px
  gutter on its right — with ~185 px empty under the film row at rest; today's monitor
  was ~832 × 467 and the mock's fills the panel. The room kept under it was 450 px, for
  a selected shot's strip whose bottom measured ~80 px short of the window's. It is
  centred now, and the reserve is 420 px (the cut lane's own suggestion) under a 48 %
  cap: 768 × 432 on the fixture, the same size with a shot selected, and the board still
  never scrolls. `test_budget.py` checks the centring, the height and that selecting a
  shot does not resize it (the centring fails on the old CSS).
- **A Bin find's rows can be told apart, and an empty keep grid says so once (INTAKE M16
  review, I16.4).** The same line heard in three clips came back as three identical
  rows ("goodbye · 1.5 s" — the clip and the times went to the tooltip in I16.4), and
  under them the keeps said "0 of 1 keeps match" and then "no keep matches — clear the
  chip or the words above" with no chip set. Each find row now carries the picture at
  its moment and where in its clip it is ("at 0:05 · 1.5 s"), as the keep cards carry a
  picture; the keep count shows only when some match, and the empty grid names what is
  set (the words, the chip, or both). New test in `test_dock_ui.py`; it fails on the old
  rows.
- **Timeline block names keep their words whole, two lines at most (INTAKE M16 review,
  I16.4).** A block's name (the first words spoken in it) broke inside words and
  stacked three lines: on a ~45 px block "goodbye" read "goodb / ye", and "how are you"
  stood three high — Killington fits ~20 shots in ~990 px, so most blocks are that
  narrow. Names now break only between words, clamp at two lines, and a word wider than
  its block is cut at the edge (the whole line is the tooltip). The block-name test lays
  both names out on a 45 px block: 1 and 2 lines (it measured 2 and 3 before).
- **A waiting proposal says on its card what it did not do (INTAKE M16 review, I16.5).**
  The model's own shortfall (`limits`, "couldn't: …") moved behind *why? ▸* in I16.5,
  while the card's "✓ checked" sits in view — and the free check measures what was made
  (in the shot, in the frame, in sync), not whether it is what the note asked for. A
  proposal that skipped half the note read as checked and complete, and that is what
  Accept vs Change turns on. A proposed card with `limits` now carries one line,
  "didn't: …", above the check; *why? ▸* no longer repeats it, and accepted cards keep
  it behind *why?* as before. The waiting-proposal test reads the line (it fails on the
  old card).
- **One priced Look deeper on the selected shot (INTAKE M16 review, decision 9).** The
  shot strip's top row carries *Look deeper · ~$x* for the shot; opening its "how the
  machine saw" line drew the full strip, whose action area priced the very same seconds
  again ("this shot · 1.0–3.0 s · Look deeper · ~$x") — two priced buttons for one
  look. The opened strip now offers Look deeper only for seconds dragged across it
  (I15.4's drag, kept); the shot's own range is the top row's. `deep.line` takes
  `markLook: false` for a host that prices the range itself; the pass's compact strip
  is unchanged. The inspector test counts one visible Look deeper and drags for the
  second (it fails on the old strip).
- **"What is this film about?" is asked once where the first cut is made (INTAKE M16
  review, I16.4).** Next for the first cut lands on `/#tool=ask` (so does the pass's
  *Make the first cut →*); on an empty board the Ask tool hid its change box but kept its
  story panel, and the empty state under the timeline asks the same sentence with its
  own field — two "What is this film about?" on one screen. The Ask tool's field now
  steps aside while the board is empty, and one line says where the sentence and the
  first cut are (a blank tool would read as broken); with a cut on the board the field
  is back. The two fields were already one sentence underneath. The empty-board and
  Ask-tool tests count one question on the screen.
- **The film tool's Download is readable (INTAKE M16 review, I16.1).** `#filmNewest`'s
  "↓ Download · 2 MB" was a bare link in the browser's own dark blue, underlined, on the
  near-black panel — about 2:1 (sampled rgb(16, 9, 211)) — the payoff of the tool, and
  the hardest thing in it to read. It is a full-width button in the accent colour now,
  as the mock draws it. The download test checks its contrast (≥ 4.5:1), no underline
  and the full width; it measured 2.2:1 before.
- **"unsaved…" shows only when a save is late (INTAKE M16 review, decision 1).** The
  header said "unsaved…" for the autosave's own 700 ms wait after every edit — a word
  flashing in the one header row each trim, though nothing was wrong. It shows now only
  when the save is two seconds late (a hung request, a slow disk); "save failed" is
  unchanged, and the state still moves at once underneath for whatever waits on
  "saved". The save-state test checks the quiet wait, a failed save and a hung one (it
  fails on the old header).
- **The board's header stays one row while work runs (INTAKE M16 review, C1).** The
  progress strip was a full-width second row inside the header, one row per job, and a
  job that finished well lingered 12 s saying "done": a Quick look made the header
  102 px from the click until 12 s after the film landed, the monitor shrank and the
  board jumped 55 px when the strip cleared (Killington's renders run for minutes). Each
  running job is now one compact line in the header's own row, beside Next — "▸ Rendering
  — preview ▬ 32% · ~10s left" — with what it is on as its hover and its steps under ▸
  in a panel over the page; a job that finished well leaves at once (its result is on
  the screen), a failed one stays and says why on its line. `test_budget.py` measures a
  new board state, two jobs running and one just finished: header 47 px, one row, the
  finished one gone, 98 words · 18 controls (limits 108 · 20). It fails on the old strip.
- **The one money number is on screen while the index spends, and readable from every
  screen (INTAKE M16 review, decision 7).** /open's "spent $X" rides on the how line,
  which is hidden while the index runs; the board's only spend readout was the backend
  pill's title, which M16 removed, and the pass had none. With no cap by default, a
  Killington-size index ran with no running total anywhere. The running line now ends
  "· spent $X" (and the cap when there is one), and the bin · cut menu — the way between
  the three screens — says "spent $X on this project" under its places, from the same
  `/api/status` number. The index test and the menu test check both (they fail without).
- **A backend problem with no command for it is said again, in the banner (INTAKE M16
  review, decision 1).** Two surfaces said what is wrong with the model backend: the
  board's header pill ("… · not usable", "… · unreachable") and the open screen's backend
  line. M16 took both away, and the CLI banner draws only when the server has a command
  for Karl — so "ANTHROPIC_API_KEY is not set.", an unknown `ROUGHCUT_BACKEND`, or a
  probe that failed for a reason nothing diagnoses (a timeout, the network) showed
  nowhere, and Next kept pointing at priced buttons that failed only after the click.
  The banner now says those too, on the same one line, with *Check again* and no command
  to copy. New test in `test_flow_ui.py` (no key on `/`, a timed-out probe on `/floor`);
  it fails on the old banner. The suite's per-test reset also clears a failed probe.
- **How closely to look can be chosen before Resume, not only before Index (INTAKE M16
  review, I16.2 keep-list).** The "Looks at a frame every N s · change" line and its
  slider lived only in the setup box, which is hidden while the looks are paused — the
  paused box held its title, the reason and Resume, and Resume spent at the project's
  stop. On `b620ff7` the slider was on screen whenever a clip waited to be looked at,
  paused ones included; on Killington (9 clips paused, ~$2) the choice was gone. The look
  line now moves into whichever box is up, and Resume is priced at the stop chosen (the
  journal's price for what waits, moved by what the stop changes in the coarse sheets).
  The pause test opens the slider in the paused box and checks Resume's price follows it
  (it fails on the old page).
- **A trim keeps the shot's "how the machine saw" line as it was (INTAKE M16 review,
  decision 9).** The line opens per item and closes on the next; a trim is the same
  item, but once it settled (450 ms) the strip rebuilt the line — every build renders
  closed — and removed any Look deeper result drawn under the top row. Now a trim of the
  same shot moves the line's mark to the new range (open or closed as it was), keeps a
  drawn result, and re-prices only the top row's Look deeper; a new shot builds both
  afresh. New test in `test_deep_ui.py`; it fails without the fix.
- **A clicked P / X / U drops a pending batch question, as a key does (INTAKE M16
  review, decision 5).** Any key but the one that asked drops "reject 3 picks? ⇧X again ·
  Esc"; the new keycap buttons called the verdict directly and left it armed, so after
  a clicked X the header still asked, and the next single ⇧X rejected every match
  without asking again. The click drops it now. New test in `test_floor_ui.py`; it
  fails without the fix.
- **Discarding a proposal while its check renders is not a failed check (INTAKE M16
  review, I16.5).** The check guarded its first read of the effect ("discarded while it
  waited") but not the second, after the proof render; since every design now starts a
  check by itself, a quick Discard landed in that window and the job ended `failed —
  HTTPException: 404: no effect …` — a red row in the board's progress strip (a second
  header row) and a "… failed" toast for a proposal Karl had thrown away. That read is
  guarded too: the job ends `done`, "the effect is gone". New test in
  `test_fx_api.py`; it fails without the guard.
- **The bin · cut menu closes on the board and the pass (INTAKE M16 review, decision 2).**
  Esc and a click outside set the menu's `hidden`, but its own `#picker { display: flex }`
  outranks the browser's `[hidden]` rule, and only the open screen has a global
  `[hidden] { display: none }`: on `/` and `/floor` the 325 px menu stayed drawn over the
  monitor, the pass's picture and the keys sheet, while it said it was closed. It
  predates M16, but the menu is now the way between the screens. `#picker[hidden]` is
  `display: none`; the menu test closes it with Esc and with a click outside on the
  board and on the pass (it fails without this).
- **A proposal already waiting when the board opens is checked by itself (INTAKE M16
  review, I16.5).** The FX tool checks a proposal the server has not checked (one made
  before the server began checking every design), but it read `/api/fx` at start-up
  before the board had loaded the cut: no proposal was on a shot of it, none was
  checked, the rail counted none, and nothing read again until some job finished. Next
  landed on the waiting card and it never said "✓ checked" (22 s probed; Killington's
  I13.5 proposals are exactly this case). The board re-reads the effects once the cut is
  drawn. New test in `test_fx_ui.py` that never calls `fx.refresh()` itself; it fails
  without the fix.
- **The pass's closing card no longer hides the screen's one blue under its scrim (INTAKE
  M16 review, C2 / I16.3).** When Next is not the cut — the index or a look paused, an
  effect waiting, the film not made — the blue goes on the header's Next chip, and the
  card's full-screen overlay (z-index 30, `inset: 0`) covered the header: the chip was
  dimmed to 28 %, `elementFromPoint` at its centre returned the overlay, and a click on
  it (or on the bin · cut menu) landed on the scrim. The header now sits above the
  scrim while the card is up. The closing-card test checks that the one blue, the chip
  and the switcher are each the element under their own centre (it fails without this).
- **The header stops offering the old film's Download after a shot's speed changes
  (INTAKE M16 review, I16.1).** The board's `isThisCut` compared clip, in and out; the
  server's `_same_shots`, which calls itself "the board's own isThisCut rule", also
  compares speed, and renders record it. Since I16.1 the header's *Make the film*
  becomes *↓ Download* when `isThisCut` finds a film, so after a Quick look and a shot
  set to 2× the header read "↓ Download · 1080p · 0:04" — the 4 s film of a cut that is
  now 3 s — while Next said *Make the film*. Speed is part of the rule now; the film
  test sets 2× and back (it fails with the old rule).
- **⌘Z after an in-place Accept can no longer take the accepted edit back (INTAKE M16
  review, C5).** Accepting an effect with an edit re-reads the cut through the board's
  `roughcutRefresh()` instead of reloading the page, but the timeline's undo and redo
  stacks still held snapshots of the cut from before it. Trim a shot, accept a slow
  motion, press ⌘Z: the toast said "undone: trim" and the autosave wrote the old cut —
  the slow motion was gone from disk while its effect stayed *applied*, so it never came
  back to be accepted. The refresh now empties the history (`tl.clearHistory()`), as the
  reload did; ⌘Z says "nothing to undo" and the cut on disk keeps both changes. New test
  in `test_fx_ui.py`; it fails without the fix.
- **A paid Look deeper on a Bin row always shows its result (INTAKE M16 integration).**
  The finished deep job does two things a second apart: the button's own poll draws the
  beats under its row, and the board's job strip reloads the project, which redraws the
  Bin's list. When the redraw landed first the row was gone and the result was never
  drawn — the look was paid for and the screen showed nothing.
  `test_deep_ui::test_look_deeper_from_a_seen_row_is_priced_then_runs_and_shows_the_beats`,
  listed as a flake by lanes cut and pass, failed 3 of 3 alone on the merge. The result
  now opens under the redrawn row (same span, same key); 3 of 3 pass.
- **The Bin's tabs and *more found ▸* answer from the first paint (INTAKE M16
  integration, I16.4).** Lane cut put *heard* and *seen* under *more found ▸*; the
  handlers for it and the tabs were bound at the end of the board's start-up, after the
  versions, the last proposal and the jobs had loaded — so a click in that first second
  did nothing. `test_timeline_ui::test_shift_click_selects_the_range_and_cmd_click_toggles`
  failed every time it ran after `test_timeline_lanes` (2 of 2 suite chunks, 1 of 1
  pair run; the later module's page loads slower): its click opened nothing and the
  heard tab never showed. They are bound before the first paint now; the pair passes.
- **Accepting a proposal is one undo entry for the cut and the grade, and its toast
  stops naming the inspector (INTAKE M16 integration, I16.4).** Lane cut put the colour
  block on every undo entry and took the inspector away (the grade is behind a shot's
  *colour & look ▸*); lane shell's Accept still said "⌘Z undoes the cut; the grade is in
  the inspector", and a colour-only proposal pushed no undo entry at all, so ⌘Z after
  it did nothing. Accept now pushes one entry whenever the cut or the grade changes,
  and says "applied, cut and colour — ⌘Z undoes both" / "colour applied — ⌘Z undoes it".
  The colour-proposal board test now presses ⌘Z and finds the grade gone (it fails with
  the old undo rule).
- **A passed effect check leaves no row in the progress strip (INTAKE M16 integration,
  decision 1).** Lane fx made the free check run after every design, Change and nudge;
  lane shell made the board's header one row, with the progress strip its second row
  only while a job is worth showing. Merged, every nudge left "Checking slow motion ·
  done" in the strip for 12 s more — two header rows for a check that found nothing.
  A check that finished and passed now has no row (its card says "✓ checked"); one that
  is running, or failed, keeps its row. New test in `test_fx_ui.py`; it fails on the
  merge's app.js.
- **Next hears the pass repaint (INTAKE M16 integration, C2 / C3).** Lane pass announces
  every repaint of /floor with a `roughcut:pass` event — the round's count, and the
  closing card's *Back to the cut* the moment it is drawn — and lane nav's /flow.js
  re-read the pass only once a second or on a DOM change it happened to see. It listens
  for the event now and places "N left" and the one blue at once; the second's re-read
  stays as a safety net. The closing-card test now also checks the screen's one blue is
  Next's own (the card's way back when Next is the cut, else the chip).
- **The in-place Accept test no longer calls its own stub (INTAKE M16 integration,
  C5).** `test_fx_ui::test_accepting_an_edit_changes_the_cut_in_place` installed its
  counting `window.roughcutRefresh` with an `evaluate` whose expression ended in the
  function, and Playwright calls a function an expression evaluates to: the count was 1
  before the click. On the lane branch the Accept was slow enough for the wait to see 1;
  merged, the shell's board answered faster, the count reached 2 and the wait for 1 timed
  out (3 of 3 runs). The stub is installed inside a function body now; the test passes.
- **The shot strip's machine line marks the range as "this shot" (INTAKE M16
  integration, C4).** Lane cut called `deep.line` on the strip before lane pass's
  `deep.line` existed and drew its own fallback; merged, the real line opened a strip
  whose band and priced Look deeper read "this range · 1.0–3.0 s" where the inspector's
  strip had said "this shot". The board now passes `markLabel: 'this shot'`.
  `test_deep_ui::test_the_inspector_shows_how_the_shot_s_clip_was_seen` failed on the
  merge and passes.
- **An empty folder offers nothing to index (INTAKE M16 I16.2, review).** With no clips
  the new setup state still showed *Index the footage*, which the server refuses with a
  400 ("no footage to index"). An empty folder is its own state now: the "No footage in
  this folder yet" line, no Index button.
- **Next's blue Preview survives the FX tool's rebuilds (INTAKE M16 I16.5, contract C2).**
  The tool rebuilds its cards when a job ends or a check lands; the `.is-next` class
  flow.js puts on the waiting proposal's ▶ Preview went with the old element until
  flow.js's next pass. A rebuild now carries it to the new Preview of the same effect.
- **A change to an accepted effect can be seen and answered (INTAKE M16 I16.5).** Change
  (Iterate) on an accepted effect wrote the revision to the fx dir under the same id
  while `GET /api/fx` listed only the EDL's accepted copy, so the paid-for change could
  not be seen, previewed or accepted from the board — and the check, a nudge or a second
  Change worked on the accepted copy instead (a nudge even overwrote the revision). The
  revision is now listed in the accepted copy's place as a proposal, and the check, a
  nudge (`PUT /api/fx/{id}`) and Change work on it; Accept keeps the old version for
  Revert as before, and Discard drops the revision and leaves the accepted copy as it was
  (it answered 400 "remove it instead").
- **A finished check no longer takes the caret from the design note (INTAKE M16 I16.5).**
  The FX tool rebuilds itself when a job ends, and the server's own check now ends a few
  seconds after every design and nudge — so typing the next note (or a Change line) lost
  its focus mid-word. A rebuild now puts the caret back where it was. A design that came
  back also empties the note, so "+ design another effect" starts blank instead of with
  the last design's words.
- **Accepting an edit-only proposal changes the cut once (INTAKE M16 I16.5).** The slow
  motion on Killington has nothing to draw or hear. `POST /api/fx/accept` applied its
  edits to the cut, then asked `validate_effect` to keep an effect with no overlay, no
  sound and no edits left, which it refuses — so Accept answered 500 *after* changing the
  cut, and the proposal stayed waiting to be accepted, and applied, again. Now the cut
  changes once and the proposal leaves every list: its file stays on disk as the record
  (`status: applied`, with its sentence and `applied.before`, what an undo takes back),
  and nothing is added to the EDL's `effects`. No test had accepted a proposal with edits.
- **Next lands on the waiting effect (INTAKE M16 I16.0a).** On Killington, Next opened
  the FX tool on whatever shot the board had anchored — shot 1's accepted typed title —
  while the slow-motion proposal `fx_d644520f` waited on shot 17 (`g1ce00e2b83`): the
  action carried only `{tool: 'fx'}`. `GET /api/flow` now gives the action a `target`
  (`{shot, fx}` — of the waiting proposals, the one on the earliest shot of the cut;
  `{ask}` for a waiting Ask proposal) and carries it in the href,
  `/#tool=fx&shot=<id>&fx=<id>`. The board's hash handler (`/flow.js`, in place when
  Next is clicked on the board) selects the shot, parks the monitor at its start, paused,
  opens FX and calls the new `fx.focus(id)`, which scrolls the dock (not the page) until
  the proposal's card is in view; an `ask` target opens the Ask tool's "show it". The
  target leaves the hash once read, so a reload does not land on an answered proposal.
  The FX rail badge counts the proposals waiting across the cut — the Polish stage's
  number — instead of the selected shot's effects, which changed with every click. A
  proposal still outranks a stale render (M14 decision 2).
- **Proposed themes stop counting as "needs you" (INTAKE M16 I16.0b).** Killington's
  themes proposal from Sep 8 kept the flow's Brief stage amber and could take Next,
  though M14 decision 1 says the brief never blocks anything. A waiting themes proposal
  now leaves Brief *optional* ("Proposed themes wait — keep or discard them whenever you
  like", `counts.proposal`), with no `needs`, and Next never routes to it: a waiting cut
  or effect, the index, the first cut, a stale render come first as before.
- **The launch-preflight test follows "no cap by default" (M16 stage 0 integration).**
  `test_server.py::test_preflight_names_the_backend_and_model_without_calling_it` still
  asserted `budget_usd > 0` on `/api/status`, and failed with `None > 0` once I16.0g made
  no cap the default (the money lane did not run `test_server.py`). It now asserts the
  cap is either absent or positive, and that `spent_usd` (this project's, from disk) is
  a number.
- **⌫ does not take out the shot Next landed on (M16 stage 0 integration, I16.0a × n).**
  Next's landing (`flow.js` `land()`, `fx.focus`) selected the shot with a plain
  `tl.select`, which the select event reports as `api`, and I16.0n counts anything but
  `app` as a shot somebody chose — so, merged, Next selected shot 2 and a ⌫ from the
  pass's habit took it out of the cut (the landing test with ⌫ added: shot 2 gone).
  The landing now selects with `source: 'land'`: app.js still paints (the inspector
  follows the anchor), and `timeline-keys.js` treats it like the board's own pick, so ⌫
  says "click a shot to choose it". The landing test presses ⌫ after Next and checks the
  cut is whole.
- **Next lands on a waiting first-cut proposal (M16 stage 0 review, I16.0a).** With the
  cut empty and a first-cut proposal waiting, Next carried `{ask: job}`, but the landing
  (`flow.js` `land()`) waited for the timeline to hold a shot before it did anything —
  an empty cut never does — so it gave up after 15 s and the proposal was never shown.
  The landing now waits for the timeline only when its target is a shot or an effect; an
  Ask target waits for the "show it" link alone. A new UI test goes from /open to the
  empty board through Next and finds the proposal open (it timed out before the fix).
- **Next's landing on a waiting Ask proposal has a browser test (M16 stage 0 review,
  I16.0a).** Only the href string (`/#tool=ask&ask=<job>`) was tested: with the Ask
  landing switched off in `flow.js`, 63 flow, Next and proposal UI tests still passed.
  A new test writes an unanswered proposal, clicks Next on the board itself (in place)
  and again from /open, and checks the proposal opens and the hash drops `ask=` both
  times; it fails with the landing switched off.
- **⌫ does not take out the shot handed on after the inspector's remove (M16 stage 0
  review, I16.0n).** I16.0n says ⌫ never takes "the one handed on after a delete", and
  the X key kept that promise only because `rippleDelete` reset its own flag: `tl.remove`
  handed the selection on as an ordinary `api` select, which counts as chosen. Removing a
  shot with the inspector's *remove* (or *remove* on a multi-selection) and pressing ⌫
  took out the next shot, which nobody had clicked. `tl.remove`, and `pruneSel` when the
  anchor leaves the cut, now hand on with `source: 'heir'`, and `timeline-keys.js`
  treats `heir` like the board's own pick. A new test removes shot 1 from the inspector,
  presses ⌫ and Del, and finds shot 2 still in the cut (it was gone before the fix).
- **An effect proposal whose shot left the cut no longer holds Next or the FX badge (M16
  stage 0 review, I16.0a).** Design an effect on shot 2, take shot 2 out with X: Next
  stayed on "1 effect proposed — accept or discard" and the rail badge read 1, but the
  FX tool lists cards only for shots of the cut, so the landing found no card and the
  proposal could not be answered — and a waiting proposal outranks everything else, so
  Next was stuck there. Proposals from a sibling cut of the same bin (different shot
  ids) did the same. `flow_facts` and the badge now count only proposals whose shot, or
  the shot they are anchored to, is in the cut. The proposal stays on disk and counts
  again when its shot comes back (⌘Z). `test_flow.py`'s landing test now expects the
  orphan to be left out, and a new FX UI test removes the shot, sees the badge empty
  and Next elsewhere, then undoes and sees it count again (both failed before).
- **A project cap no longer makes the backend pill call a working CLI failed (M16
  stage 0 review, I16.0g).** The new per-project gate asks before every model call,
  and that included the backend probe (at startup and on *Check again*). With a $5 cap
  and $4.97 spent — the index pauses as soon as one more sheet would pass the cap, so a
  project can stop that close — the probe's $0.05 pre-flight was refused, and the pill
  read red: "claude-sonnet-5-5: ~$0.05 more would pass this project's budget cap…". The
  probe proves the CLI answers, for a fraction of a cent, and is not project work: the
  gate now lets it through on the thread it runs on, and refuses every other call as
  before. A new test probes at $4.97 of $5 and gets `ok`, then checks an ordinary call
  is still refused (it read `failed` before the fix).
- **Padded one-frame picks no longer overlap their neighbour (M16 stage 0 review,
  I16.0c).** I16.0c pads a pick that rests on one sampled frame to the 4 s look
  interval (±2 s), but witnesses join into one moment only within 3 s. Two one-frame
  picks 3 to ~4 s apart (30.0 and 33.5) became (28, 32) and (31.5, 35.5): separate
  picks that overlap, and a verdict on one cleared the other's (`apply_verdict` clears
  any verdict touching 1 % of its range), so the pass could not finish — the I16.0c
  symptom by another route. A padded pick now reaches at most halfway into the gap to
  the clip's next pick on either side ((28, 31.75) and (31.75, 35.25)); with room, it
  is still 4 s. Not seen on Killington today (its three short picks have room). A new
  test answers three such picks and finds every verdict kept.
- **"Cap this project at $X" caps this project only (M16 stage 0 review, I16.0g).** The
  settings drawer offers *cap this project at $__* and enforces it against this
  project's spend, but the cap itself was one `budget_usd` in `settings.json` under
  --work, shared by every bin: capping Killington at $5 capped copper at $5 too (each
  against its own spend). The cap is now kept per bin, by the bin's name as the spend
  file and the journal are (`settings.json` `budget_usd_by_bin`); `/api/settings` and
  `/api/status` read the open bin's, and *No cap* removes only that bin's. The workers
  stay one setting for the machine, and `ROUGHCUT_BUDGET_USD` still pins every bin. A
  new test caps one bin, opens another under the same --work and finds it uncapped
  (it read $5 before).
- **A $15 cap saved by the old settings drawer is not a cap (M16 stage 0 review,
  I16.0g).** Before M16 the drawer sent the cap field on every Save, so a Save that only
  changed the workers wrote the $15 default into `settings.json`, and the M16 code
  honoured any saved `budget_usd` — such an install kept a $15 cap after the upgrade,
  against decision 7's "no cap by default". Since the cap moved per bin (above) that
  global key is no longer read, and the next save of the settings now drops it, so the
  file does not carry a cap that looks live. Foxtrot's live work dir has no
  `settings.json`; the laptop's could not be checked. A new test starts from the old
  file and finds no cap, then saves workers and finds the old key gone.
- **The board's index line carries no second money number (M16 stage 0 review,
  I16.0g).** I16.0g asks for one money number, and /open dropped "$ spent by the
  index", but the board's index line still appended the journal's own cost —
  "indexed · 12 of 12 released · $3.20" — beside the backend pill's "$X spent on this
  project", a different number. The line now says where the index is and nothing about
  money. A new test reads the line in all four states and finds no `$` (each carried
  "$3.20" before).
- **The Bin's *Ask the model* shows its price before it can be clicked (M16 stage 0
  review, I16.0f).** I16.0f and decision 7 say every model button shows its price
  first, and the end state counts buttons that spend without one. The Bin's Find button
  *Ask the model* read just that and was enabled at boot; typing a query and clicking
  it started the model search in the same request whose response brought the price —
  priced only after the spend began. A new free `GET /api/find/price` returns the same
  `find.projected_usd` before anything is typed; the button is served disabled, and
  enabled only once it reads "Ask the model · ~$0.19". If the price cannot be fetched
  it says "price unavailable" and stays disabled. New API and UI tests (both failed
  before).
- **The refused-play test waits for the refusal, not for any monitor message (M16 stage
  0 review, test flake).** `test_a_refused_play_is_named_on_the_monitor` waited for
  `#screenMsg` to show and read it at once; when the proxy was still opening at the
  click, the monitor first says "opening CLIP_A…" and names the refusal only once the
  metadata is in, so the assertion read the wrong line (seen twice in full-file runs
  during the review fixes, never alone). It now waits for the refusal's own words.
- **Ask, Cut from the bin, a shot's Ask, FX Design and Go wait for their price (M16
  stage 0 review, I16.0f).** A button whose price had not arrived kept its plain name
  and stayed enabled ("rather than a guess"), so a click in the first second after
  load — before `GET /api/ask/price` or `/api/fx/price` answered — spent with no price
  shown, and a failed fetch left the button unpriced for good. Each of them is now
  disabled until its price is on it (the Ask panel's *Ask* is served disabled; the
  empty board's and the inspector's are built disabled), and enabled again only if the
  price is what held it; with no price to be had it reads "· price unavailable" and
  stays disabled. `ask()`, Design and Go also refuse an unpriced call themselves (a key
  or a script gets no further than a toast). FX's Design no longer shows the previous
  shot's price while the new shot's is on its way. New UI tests abort the price
  requests and find every such button disabled and no model call sent (both failed
  before).
- **A cap is checked at the call's own price before an Ask, a model search, themes or an
  FX design / revision starts (M16 stage 0 review, I16.0g).** The index, the audit and
  the deep look checked their real price against the cap; everything else met only the
  gate before every model call, which sees a $0.05 pre-flight. With a $5 cap and $4.90
  spent, 4.90 + 0.05 passed and a whole-cut Ask (~$0.58 by `/api/ask/price`) landed
  the project near $5.48. `POST /api/ask` (whole cut or one shot), the model search
  (`/api/find` with `deep`), `/api/themes/propose`, `/api/fx/design` and
  `/api/fx/revise` now refuse with a 409 in the cap's words — "this ask (~$0.58) would
  pass this project's budget cap of $5.00 ($4.43 spent) — raise or remove the cap in
  settings" — when their price would pass it; the board shows that as the call's
  failure. A new test puts each call half a cent past the cap and finds all six refused
  with nothing reaching the model (the Ask started before the fix).
- **The sign-in script speaks for the host it runs on (M16 stage 0 review, I16.0m).**
  On foxtrot the banner now says `bash <repo>/scripts/claude-signin.sh`, "Run this on
  foxtrot", but with no CLI there the script answered "The Claude CLI is not installed
  in WSL", and its header said to run it from Windows by double-clicking the .cmd. It
  now names the host — "not installed on foxtrot" — and says WSL only when the kernel
  is WSL's; the header covers the native box first and the laptop's WSL second. A new
  test runs the script with no `claude` reachable (it stops at that check, before any
  sign-in) on a native and a WSL kernel string (it said WSL for both before).
- **Next's landing test proves the card is scrolled into view (M16 stage 0 review,
  I16.0a, test strength).** The live bug was a waiting card deep in the dock (shot 17
  on Killington), but in the landing test the proposal's card was already in view at
  1280×640, so "the card's head is in view" held with `fx.focus`'s scroll removed. The
  test now puts two accepted effects ahead of the proposal on its shot, checks the card
  starts below the dock's fold, and then that Next brings it into view; with the scroll
  line removed it fails.
- **Next landing on a proposal for a shot the edits will make is tested (M16 stage 0
  review, I16.0a, test strength).** `fx_target` reads a proposal's `anchor_shot` before
  its `shot` — a `new:1` shot sits on the shot it is anchored to until Accept — but no
  test built such a proposal, and reading `shot` alone still passed. A new flow API test
  puts a `new:1` proposal anchored on shot 1 beside a proposal on shot 2 and expects
  Next on shot 1; it fails when the anchor is ignored.
- **The conflict line of a demoted, contradicted claim is tested (M16 stage 0 review,
  I16.0d, test strength).** I16.0d quotes a contradicted claim in the conflict line as
  the sheet wrote it (`_claim`), not as its "maybe: … · not checked" wording, but none
  of the I16.0d test's cases had a contradicted witness, so quoting the maybe wording
  passed. The test now adds one and checks the conflict quotes the bare claim; quoting
  the witness text fails it.
- **The closing card's A key is tested on the no-cut route (M16 stage 0 review, I16.0e,
  test strength).** With no cut, the card's way out is "Make the first cut →" to
  `/#tool=ask`, by its button or by A; only the button was tested, and A sending the
  board to `/` still passed. A new pass UI test presses A on the card and expects the
  Ask.
- **The Ask-price tests no longer depend on the shell's model pins (M16 stage 0 review,
  I16.0f, test strength).** With `ROUGHCUT_MODEL_SKELETON=claude-sonnet-5-5` in the
  environment (a developer shell or a service that pins a tier), `test_ask_price.py`
  failed: "assert 0.45 <= 0.36 — Killington's asks priced at the top tier", and the shot
  price's literal `0.15` assumed the default deep model too. The tests now clear both
  role pins first and derive the band and the shot price from `config.DEEP_MODEL`'s
  prices instead of literal dollars; they pass with the pins set.
- **The Ask price's "newest five" window is tested (M16 stage 0 review, I16.0f, test
  strength).** The price is the middle of the newest five asks of a kind, but the fitted
  test wrote four, so removing the window still passed. A new test writes three early,
  very large asks and five newer ones, and expects the price of the newest five alone
  ("last 5 asks"); without the window it is fitted on eight and fails.
- **The render-rows test passes in any locale (M16 stage 0 review, I16.0k, test
  strength).** The board writes a render's day and time in the viewer's locale
  (`toLocaleString([], …)`), and the test's browser inherits the host's `LANG`, but the
  test expected the en-US words: under `LANG=en_GB.UTF-8` it failed on "'Sep 8, 10:05
  PM' not in '… 8 Sept, 22:05 · main · ×3 …'". The expected strings are now made in the
  page with the same options as the rows; it passes under en_GB and en_US.
- **The picker's "one bin, two names" test reaches the bin on the board (M16 stage 0
  review, I16.0h, test strength).** Its second registry entry — the current bin
  remembered under the link — was overwritten when the board opened (opening
  re-registers the bin by its own name and spelling), so the test never exercised it:
  marking the current row by its spelled path instead of its real path, or keeping the
  first record instead of the most recently opened, both still passed. The entry is now
  written after the board opens, under a second name and newer; the test expects one
  row, that record's name, and `current` true through the link. Both mutations fail.
- **`fx.focus`'s own selection is tested as the board's pick (M16 stage 0 review,
  I16.0a × n, test strength).** The seam fix made both Next's landing and `fx.focus`
  select with `source: 'land'`, but in the landing test `land()` has already selected
  the shot when `fx.focus` runs, so reverting `fx.focus` alone still passed. A new FX UI
  test chooses shot 1, calls `fx.focus` on a proposal on shot 2, presses ⌫ and finds
  the cut whole; a plain `select` in `fx.focus` fails it.

### Changed
- **The FX landing test starts its card below the fold again.** `test_fx_ui`'s
  Next-landing test sized its window (1280×640) so the waiting effect's card began
  below the dock's fold; with the board's header one row (INTAKE M16 I16.1) the dock grew
  by 94 px and the card started in view, so the precondition failed. The window is
  1280×540; the landing it tests is unchanged.
- **`window.roughcutRefresh()` (INTAKE M16, the lanes' contract C5).** Re-reads
  /api/project and repaints the timeline, the inspector, the bin and the film tool in
  place, keeping the selection and the playhead; returns a Promise. For a change made on
  the server — the FX tool's Accept of an edit, which reloaded the page.
- **The keys sheet is one table grouped by task (INTAKE M16 I16.4).** It was the board's
  own run-on line of keys, then the timeline's section with module tags ("· foundation",
  "· trim", "· edges") and rows said twice (x, ⌘Z, + − \). Now one table — Play · Trim
  and cut · Move around · The bin — drawn by /dock.js from the board's keys and the
  timeline's own table (`window.tlKeys.KEYS`, read, never copied), each row once.
- **Sound in one line (INTAKE M16 I16.4).** Removed: the MUSIC heading, the 0–24 dB
  slider and its "12 dB" label, and the hint paragraphs ("Heard under the cut in the
  monitor…", "A bed sits under the cut and ducks…"). Now: the track; one line only when it
  says something — "loops once at 1:59 under a 3:09 cut" when the track is shorter than the
  cut (it loops in the monitor and the render, and nothing said so); "dip under talk: off /
  a little / a lot" (0 / 6 / 12 dB, the duck the render applies — a depth saved as anything
  else keeps its own entry); the fades behind "more".
- **One sentence about the film, one first-cut button, and ▶ Play it (INTAKE M16
  I16.4).** The empty board and the Ask tool both ask "What is this film about?" — one
  field, the EDL's story (typing in the empty board's box is the story). The empty board
  has ONE priced button, "Make the first cut from your N keeps · ~$x" (Cut from the bin's
  fixed note) or "Make a first cut · ~$x" with no keeps, marked for Next
  (`data-next-for="cut"`), with "aims for 2–3 min" beside it. Removed: the look-alike
  "Ask for a first cut" + "Cut from the bin" pair, "Say what this film is about — a
  sentence is enough — and ask for a first cut…", the "N clips nobody has looked at yet"
  paragraph, the Ask tool's Story heading, "The thing the agent is worst at…", "no cut
  yet — the board's empty state is where…", and Cut from the bin as a second priced button
  beside Ask once a cut exists. The Ask tool reads "Ask for a change · ~$x" with "aims for
  2–3 min" (the EDL's target, which sat in the header). A proposed cut gets "▶ Play it"
  beside Accept and Discard: it plays the proposal's ghost lane in the monitor, a first
  cut's too (the monitor shows while one waits); the readable diff stays.
- **The monitor at rest shows the picture (INTAKE M16 I16.1).** It was a black box until
  something played, with a help line under the transport ("click a shot below to jump ·
  the inspector under it holds its why and trims · space play / pause · …"). Now it shows
  the frame under the playhead (the poster endpoint, following the playhead while nothing
  plays) with a big ▶ — the screen is the button — and, while G has the grade off, an
  "ungraded" tag on the picture (/grade.js) where a toast faded. The help line is gone
  (the keys are on ?); "▶ Play cut" is "▶ Play".
- **The board's header is one row, and *Make the film* is how a film is made (INTAKE
  M16 I16.1).** Removed from the header: "Cut board" and the second bin name, the length
  and "target 2:00.0–3:00.0", Project ▾ and its popover (clips, analysed, the index line,
  the bin line — the switcher's menu carries the project's facts), the model pill
  ("opus-5-5 · ready" — the CLI banner speaks only when the CLI needs Karl), "saved" (now
  only "unsaved…" or "save failed"), "Fix N cut points" (the fix moves onto the warned
  shot) and Render with its preview / delivery select. Left: bin · cut ▾, Next, ↶ ↷, ?,
  *Make the film* — 47 px tall at 1440×900 (was 141, three rows). *Make the film* opens the
  film tool (the rail's Out is now Film; its key stays `out`): "This cut · main · 19 shots
  · 3:09" and whether it changed since the last film; *Quick look · 1080p · ~1 min* (free —
  the button Next may press, `#render`) and *Final 4K · ~17 min* (Karl's own click); ONE
  player of this cut's newest film with its Download; every other film dated and folded
  behind "Older films (N)", with Compare two inside. Gone from the tool: "idle", its own
  progress bar (the header's strip is the one progress surface), the two players that
  loaded on open, A / B on every row, and the Out badge counting every film. Once a film
  of this cut exists the header's button reads "↓ Download · 1080p · 3:09" and downloads
  it; a dot on *Make the film* says the cut changed since its last film. The board's own
  blue (`button.primary`) is gone: Next's `is-next` is the one blue.
- **Board tests choose a shot before acting on one (INTAKE M16 I16.4).** With nothing
  selected at start, the proposal-accept and save-a-copy tests select shot 1 first.
- **The Bin: one find box, "not in the cut" first, cards named in plain words (INTAKE M16
  I16.4).** The Find button is gone — Enter in the box finds a moment anywhere, and typing
  still filters the keeps. Under any find result sits a plain `Not it? Ask the model ·
  ~$x` (hidden until a find has run). The tabs are `not in the cut · N` (the default, so
  `/#tool=bin` opens there) and `all N`; two cards a row, each labelled by your note,
  else the line spoken in it, else what was seen, with its length — no clip name, no
  `0:37.6 → 0:44.3`, no detector title (`onset spike (impact/sudden event)`), no chips;
  the clip and range are the card's tooltip. `+ add` shows on the card under the pointer
  or the selected one. The instruction paragraph is gone. The evidence chips and the
  heard / seen tabs moved under `more found ▸`; the `in the cut` / `not yet` chips went
  (the tabs say it). The Audit button stays in view until it has been clicked once on
  this browser, then lives with the seen tab. The Bin rail badge counts the keeps not in
  the cut. A chosen tab is plain, not blue.
- **The board's selected shot is a strip under the timeline, in view with the picture
  (INTAKE M16 I16.4).** The inspector sat below the fold at y≈953 on Killington with ~27
  controls and ~290 words per shot. The picture now takes about 46 % of the window (never
  so much that the strip leaves the screen) and the strip reads: the still, `Shot 2 ·
  12.9 s`, ▶ play, `Ask about this shot · ~$x`, `Look deeper · ~$x` (the shot's own
  range), Remove, the why (editable), the first line spoken, any warning with its own
  fix, one speed control, warmer / cooler / brighter / darker / reset, `colour & look ▸`
  (the shot's look, strength, auto-balance, matching, reference) and `why? ▸` (where the
  shot came from, the timed lines, what was seen, the colour in words), and one line for
  how the machine saw the clip. Both disclosures start closed on every shot. Gone from
  the main view: the clip-time header, "starts at … of the film", timestamps on lines,
  five speed controls (now one), the four in/out ± buttons (trims are the block's edges
  and `[ ] { }`, decision 5), the white-point numbers, the film's look in every shot and
  the six-lane strip with its legend. A generated slide has no colour controls and a
  human name (`Shot 1 · black · 3.0 s`). Nothing is selected at start: the strip shows
  the film — `19 shots · 3:09 · look ▾ · subtle / medium / strong · auto-balance on ·
  N warnings ▸`, the last stepping to the next warned shot. Warnings sit on their shot
  with a Fix that is one undo entry and never automatic (`⚠ starts mid-sentence · Fix`
  moves the in-point to the sentence's start less a breath, the snap tool's rule;
  `0:01–0:05 goes dark · Trim it out`). Every colour change — a nudge, a look, the film
  row — is now one ⌘Z entry: the colour block rides the timeline's undo stack.
- **The `magnet · on` chip shows only while an edge is dragged (INTAKE M16 I16.4).** It sat
  on the ruler at all times. S still toggles the magnet, and says `magnet on` / `magnet
  off` in a toast when nothing is being dragged.
- **Timeline blocks are named by their first words; heroes wear a ★; the markers lane is
  gone (INTAKE M16 I16.4).** A block read `CLIP_01 12.9s` over a dim line of speech; it
  now reads the first words spoken in it (else the shot's why, else what was seen), up to
  three lines, and a generated slide reads its kind (`black`, `title`). The clip, range
  and film length are the tooltip's. The markers lane above V1 and its legend
  (`★ hero · event · available keep`) are gone: a block holding a hero keep wears ★ on its
  name. The dashed outlines of keeps not in the cut stay — they are a drag path — without
  the `available` label or a clip name on them.
- **A click on a timeline block selects it and parks the monitor there, paused (INTAKE M16
  I16.4, decision 6).** A click used to play the cut from the block (the strip's old
  promise), so looking at a shot started its sound. Now the block is selected, the
  playhead and the monitor sit on its first frame, and nothing plays; space or a
  double-click on the block plays from there. ⇧-click and ⌘-click are unchanged.
- **The open screen's popovers hang under the header, and the slider says its interval
  once (INTAKE M16 I16.2, review).** The settings and keys popovers were fixed at
  `top: 50px`, which lands on the header itself when the CLI banner sits above it in
  the page; they now open under the header wherever it is drawn, and scroll inside when
  taller than the window. The open slider repeated the line above it ("Looks at a frame
  every 4 s" / "a frame every 4 s · sees the run, misses the moment"); under it now is
  only what the stop sees ("sees the run, misses the moment").
- **The footage screen, in two states with one header row (INTAKE M16 I16.2).**
  Measured headless at 1440×900 on the suite's 3-clip bin: setup 19 controls · 460
  words in view → 15 · 74; indexed 17 · 314 in view (480 on the page, 2 blue buttons)
  → 14 · 78, one screen, no blue (Next's is-next comes from /flow.js) — both counts
  still carry the step bar, which the nav lane removes. **Header** (C1): `#hdBin`,
  `#flow`, then `?` (the page's four keys in a small popover) and *settings*; gone are
  "ROUGHCUT", the second bin name and the telemetry count. **Setup** (no journal, new
  clips, or a stage still to run): clips by day, *What is this film about?*, "Looks at
  a frame every 4 s · change" (the 4/3/2/1 s slider opens on *change* and re-prices the
  button), and *Index the footage · ~$X* (`data-next-for="index footage"`), which
  becomes one progress line ("Indexing · 2 of 3 ready · about 1:40 left") once
  clicked. **Done**: "12 clips · 43:08 · Jan 18 + Jan 27", the pictures taken from
  mid-clip (the poster's `?t=` at half the length — CLIP_12's first frame is black), a
  badge only when something is wrong (not heard yet · look paused · parked · missing ·
  junk?, with *Junk* / *Keep* answered on the card through `POST /api/junk`), and one
  line — "every word heard · a frame every 4 s · close looks on all 12 · spent $4.38 ▸"
  — that opens to the per-clip table and the journal. When the looks are paused, one
  box: "Looks paused on 9 clips", why, *Resume · ~$X*. **Removed**: the 12-row table of
  7 stage chips + PRIORITY + STATE from the main view, RELEASED ×12, the listened /
  telemetry / looked / on-the-pass flags, the ~155-word flags legend, the per-card
  coverage minis (`/deep.js` no longer loads here), "claude_cli · claude-opus-5-5" (the
  CLI banner speaks when the CLI needs Karl), the settings summary line, "runs on its
  own · survives a crash…", the price detail and slider hints, "Open the pass on 12
  clips →", "← the cut board", "close this tab…", and the themes step whole (Propose
  themes, theme and people chips, add your own, Keep / Discard, the transcripts' one
  sentence, two hints). The order moved into the settings drawer beside the cap and the
  workers; no `.primary` style is left on the page.
- **The film's one sentence tags moments, in place of themes (INTAKE M16 I16.2,
  decision 8).** The themes step goes from the open screen; what it fed — tags and the
  +0.25 lift on the pass's moments (`picks.theme_hits`), the index's `theme_hits`
  priority term — now reads the EDL's `story`, for free: `picks.sentence_words` keeps
  its content words (find.py's stopwords out, plus words any sentence about a film
  carries — "about", "film", "being"; one tag per stem), and `picks.brief_tags` puts
  any themes kept before the step went first. Killington's sentence, as the proposal quotes it, gives
  *friends · skiing · killington · silly · hitting · rocks · skis*. `GET /api/picks`
  passes `story` to `picks.build`; `index_facts` counts the same tags. Names stay in the
  EDL as dictation's vocabulary; the themes endpoints stay.
- **The pass's closing card is one line and one way back (INTAKE M16 I16.3).** *Round 3
  done* · *44 kept · 5:15 if strung out · 2 later*, then **Back to the cut — 24 keeps
  aren't in it yet →** (`data-next-for="cut"`, the button Next may light; it opens the
  board's Bin, which opens on the keeps not in the cut) — or *Back to the cut →* when the
  cut has them all, *Make the first cut →* with no cut — then plain *▶ Play the keeps*,
  *Next round · N* and *Revisit N later* (each only when there is something), and *By
  clip*. Removed: the four-number grid, *N picks · N decided · N clips*, *you could
  assemble now — your call*, *since this round started: 0 new picks* when nothing
  arrived (said only when something did, as "N new moments"), the round ETA, every key
  letter on the buttons, the C and L keys on the card (the razor and the shuttle on the
  board; ↵, R and A still work) and the pass's own blue button style.
- **A refused autoplay is a big ▶ on the pass's picture (INTAKE M16 I16.3).** When the
  browser wants a gesture before it plays, the picture shows a large ▶ that a click (or
  space) answers, instead of the small *the browser wants a key first — press space or
  L to play* on its bottom edge. Any other refusal is still said.
- **The pass is the picture, one strip and three keys (INTAKE M16 I16.3).** The picture
  is the largest 16:9 the rows under it leave (a size container replaced the fixed
  367 px budget that predated the machine strip) — about 60 % of a 1440×900 window,
  from 26 %. Under it: one whole-clip strip named by the clip (*CLIP_04 · 5:19*, its
  marks, this moment boxed, the kept range in green), the words around the playhead with
  the kept part lit and the green band to drag, then the kept range said once beside the
  band — *keeping 3:06.8 – 3:15.8 · 9.0 s* — with a **} next line** chip (the `}` key),
  and on the right the machine's one line (`deep.line`, closed on every new moment).
  For a moment only the machine saw, one line under the picture says what it saw and
  its state in a word — *maybe: skier airborne · not checked* / *… · checked* / *… · a
  closer look did not see it* — never the claim as fact; a spoken moment has no line
  (its words are on the strip). **P / X / U are buttons** at twice a keycap's size
  (decision 5; the keys are unchanged), then the dim *space play · V note · E why? · ?
  keys*. Removed: the left column (CLIP, THIS PICK, the *63 % in* line, the ON THE TAPE
  legend, the looked-frames legend, KEEPING with the snap arithmetic and the *} extend to
  the next line: "…"* sentence, the green-band hint), the WITNESSES column, the WHY
  line with *rank 47 · seen*, the tape's instruction label and its *LOOKED · 80 frames
  · every 4 s · 3 sheets*, the looked-frame ticks, the telemetry trace, the fake
  waveform, the closer strip's label and its second keeping readout, the felt numbers on
  the words, *sentence end + 0.45* (now *end of line*), the More menu (two placeholders
  and an O that duplicated 0), *[ ] { } in / out by sentence*, *← the cut board*, *hold
  V · V will ask for the microphone the first time · N to type* (words now only when the
  microphone is blocked or dictation is missing; the V key wears the state) and the
  empty *YOUR NOTE · no note yet* box (a note shows once made). why? (E) keeps the
  evidence in plain words — *HEARD*, *THE MACHINE SAW "JUMP" · NOT CHECKED*, *THE CAMERA
  FELT* — with every time a link and the window's frame coverage, without rank, score
  or the JSON dump; the strip's legend moved into ?.
- **The pass has one header row (INTAKE M16 I16.3, C1).** The bin · cut switcher, Next and
  a real **?** button on the right, ≤ 50 px; the step bar's row of its own under it is
  gone, and so are "THE PASS", *round 3 · pick 2 of 2 · queue frozen for this round*,
  the progress bar and *bin 44 moments · 0 hero · if strung out 5:14.8 · 2 later*. How
  many are left is Next's to say: the page dispatches `roughcut:pass` (`{left, round,
  mode, queue}`) on every repaint and exposes `floor.left()`, so `/flow.js` can read
  "3 left" without polling. A filter's *N of M match* stays in the filter line, and the
  batch verdict's question moved there too.
- **How the machine saw a clip is one line (INTAKE M16 I16.3, decision 9).** `deep.line(host,
  clip, {range, onSeek, compact})` writes one line from the clip's sidecars — *every word
  heard · a frame every 4 s · 3 close looks ▸* — and a click opens the whole coverage
  strip under it, in the same host (the range marked, the playhead carried); a second
  click closes it, and every call renders closed, so the next item never inherits an
  open strip. The pass and the board's shot strip call it; `deep.strip`, `inspector`
  and `rowButton` are unchanged, and the pass's old hook `deep.floor` is gone. In the compact strip the deep lane's empty text no
  longer says "drag to choose seconds" (a drag there does nothing) and the pointer to
  the board is four words, *Look deeper: on the board*.
- **An accepted effect's sentence folds to two lines (INTAKE M16 I16.5).** The fold kept
  the model's whole why — 26 words on Killington's rock hit markers, five lines in the
  dock. An accepted (or removed) card now shows two lines at most, the rest on hover; the
  waiting proposal's sentence is never cut.
- **Accepting an edit updates the cut in place (INTAKE M16 I16.5).** Accept on a proposal
  that changes the cut (the slow motion) toasted "the cut changed — reloading" and
  reloaded the whole page. It now awaits `window.roughcutRefresh()` — the board's own
  repaint of the timeline, shot strip and bin, keeping the selection and the playhead
  (contract C5) — and the card leaves the tool; the reload stays only as the fallback
  where the board has no `roughcutRefresh`, or it fails.
- **The design box is behind "+ design another effect", and the window is the range bar
  (INTAKE M16 I16.5).** The design box sat open under every shot's cards with a heading,
  a five-example placeholder cut off mid-sentence, a "where [156.00] ◀ playhead to
  [175.56] ◀ playhead · the whole shot ✕" row (clip seconds, a third time format), end
  labels doubling the handles', "click the strip to park the monitor on this shot" under
  the bar, and "Design ≈ $0.21 · 8 frames". On a shot with an effect the box is now one
  line, "+ design another effect" (open by itself on a shot with none, while a reference
  is drawn or held, and while a design runs; closed again on another shot). The window
  is the range bar's handles plus one "set start/end at playhead" control that moves the
  nearer end to the parked monitor; the handles, the playhead, the reference line and the
  timeline band (now blank for the whole shot) read film time; the button reads
  "Design · ~$0.21". Design, *Use it* and the sketch HUD's *Use it* are plain buttons.
- **The FX tool shows the waiting answer first (INTAKE M16 I16.5).** After Next, the
  slow motion's card was ~180 words under an accepted card: Karl's note quoted back, the
  model's paraphrase of it, an amber "could not:" paragraph in clip seconds, "CHANGES THE
  CUT WHEN ACCEPTED · CLIP_08.MP4 at 0.4× from 170.85 to 171.50s", two "x 0.50 y 0.50"
  rows with nudges that move nothing, and five buttons (Preview, Verify, Iterate, Accept,
  Discard). The proposed card is now first on its shot: its name, one sentence of what
  changes in the film (the server's `says`, else the model's why), "✓ checked" or what is
  wrong in plain words ("✗ it does not show where expected") or "checking…", then
  ▶ Preview · Accept · Discard · Change · ~$0.05 · why? ▸. Overlays and sounds keep their
  moment rows in view, in film time (◀ 0:00.5 rock left ▶ ✕; the anchor on hover); an
  edit-only proposal shows none. An accepted effect folds to its name, sentence, Remove ·
  Change · Revert (when a previous version is kept) and "adjust the moments ▸"; a passed
  check is not repeated on it. Behind why? ▸: the note, the model's why, what it could not
  do, the checklist in plain words (the measurements on hover), the window in film time,
  the reference, proof and strip. Removed: the Verify button (the check runs by itself),
  Iterate (now Change, priced), "N moments", the edit words, the "FX · shot 1 ·
  GEN_BLACK_3FB88607" title (now "Shot 17 · 19.6 s"), "N effects in this cut", and the
  blue Accept and Restore — ▶ Preview carries `data-next-for="polish"` and `data-fx`, so
  Next's one blue button lands on it. A finished check is no longer a toast.
- **An effect proposal says what changes in the film, in film time (INTAKE M16 I16.5).**
  The slow motion's card read "CLIP_08.MP4 at 0.4× from 170.85 to 171.50s" — a file name
  and clip seconds that look like film time on a 3:09 film where shot 17 starts at
  2:35. `GET /api/fx` now gives a proposal with edits `says`, one or two plain sentences
  worked out from its ops against the cut: "Slows 0.65 s to 0.4×. Shot 17 gets 1.0 s
  longer, at 2:50." (also *Adds a 3 s black slide before shot 1*, *Holds a frame of
  shot 4 for 2 s*, *Takes shot 9 out*, *The film gets 3.0 s longer*, or "This change no
  longer fits the cut."), and every effect `film` — `{n, start, in, speed}`, where its
  shot sits in the film after the proposal's own edits — so the FX tool can show clip
  seconds as film time, even for a shot the edits have yet to make.
- **The free check on an effect runs by itself (INTAKE M16 I16.5).** Proposals arrived
  unchecked — the slow motion on Killington (`fx_d644520f`) never had a check run —
  because the check waited for a Verify button on every card. The server now queues it
  after every design, revise, nudge (`PUT /api/fx/{id}`) and revert: a proof render of
  the one shot and the measured checklist, never a model call. It waits behind any
  render of the cut ("waiting for the render to finish") and behind another check; a
  check already waiting for an effect covers a newer change, because it reads the effect
  when it starts, and `POST /api/fx/verify` joins the same queue. A check whose effect
  changed while its proof rendered no longer writes the old copy back over the nudge.
  Each check's job carries `fx_id`, so the FX tool can say "checking…" on its card.
- **Every Ask-family button shows its price before the click (INTAKE I16.0f).** Six
  buttons called the model with no price: the empty board's *Ask for a first cut* and
  *Cut from the bin*, the Ask panel's *Ask* and *Cut from the bin*, a shot's *Ask*, and
  the FX card's Iterate *Go*. A new free `GET /api/ask/price?mode=first|bin|full|shot`
  returns `{usd, basis}`: the median of this project's last few asks of that kind
  (their recorded tokens, re-priced at the deep role's model now, so a tier move moves
  the price), else a typical Killington-sized call, plus the quick estimate call that
  opens a whole-cut ask — Killington today: whole-cut ~$0.58, a shot ~$0.15. The
  buttons read "Ask · ~$0.58"; Go reads the fx price ("Go · ~$0.05"). The after-the-run
  token line ("claude-opus-5 · 39403→15314 tok · $0.5799 projected") is one short
  "cost $0.58".
- **No budget cap by default; spend is per project, one number (INTAKE I16.0g, Karl,
  2026-10-04: *"Do not have a cap … 'no cap' — this should be on by default"*).** The
  cap was a $15 default against `inference.spent_usd()`, a process-lifetime counter: a
  restart reset it, every bin shared it, and the visual pass's subprocess spend never
  reached it. Now `config.budget_usd()` is None unless `ROUGHCUT_BUDGET_USD` pins one;
  the settings drawer offers *No cap* (the default) or *cap this project at $__*
  (`PUT /api/settings {"budget_usd": null}` removes it). `server.project_spent()` reads
  this bin's spend from disk — the visual sidecars (looks, close looks, audits) plus
  `spend/<bin>.jsonl`, one row per in-process call written by a new
  `inference.add_spend_listener` hook, its first row the asks / themes / deep records
  already on disk — and a cap is enforced against it at the index, the audit, the deep
  look and, through `inference.set_budget_gate`, before every model call. /open shows
  one money number, "spent on this project $X" (+ "of $Y cap" only when one is set);
  the index line's own "$ spent by the index" is gone; the paused box says the cap is
  reached and that raising or removing it lets Resume carry on.
- **The board listens on this machine only (Karl, 2026-10-04, ahead of an open-source
  release).** `app/server.py` bound `0.0.0.0` with no login, so on a shared network
  anyone could open the board and its footage. A new `--host` defaults to `127.0.0.1`
  (WSL2 still forwards it to Windows `localhost:8765`); `--host 0.0.0.0` is the opt-in
  for when forwarding breaks and the board is reached at the WSL IP, and the launch
  prints a warning when it is used. The parser moved into `parse_args()` so a test
  pins the default.
- **The CLI stays signed in, and the sign-in runs from Windows (Karl, 2026-10-04: *"I
  keep losing access to claude in roughcut and then struggling to run auth in my command
  prompt (cmd not found)"*).** The WSL credentials file was found with both OAuth tokens
  emptied: the login's access token is short-lived, its refresh token rotates, and
  several concurrent `claude -p` refreshing at once can wipe it. `scripts/claude-signin.cmd`
  (double-click, or paste its path into cmd/PowerShell) runs `scripts/claude-signin.sh`
  in WSL, which makes a year-long `claude setup-token` token, checks it with one call,
  saves it to `~/.config/roughcut/claude-token` (mode 600; `ROUGHCUT_CLAUDE_TOKEN_FILE`
  overrides), and re-probes the board. `inference.cli_env()` hands that token to every
  `claude` child as `CLAUDE_CODE_OAUTH_TOKEN` — never refreshed, so nothing to race.
  The banner's commands now paste into a Windows prompt: the sign-in is that `.cmd`'s
  Windows path, and update/permissions are wrapped in `wsl -e bash -lc "…"`.
- **M15 names no model ID outside config.py** (CLAUDE.md model-version policy): the deep
  tests take `config.DEEP_MODEL`, and `deep.js` describes the name pattern it prettifies.
- **The sheets record which model read them (Karl, 2026-10-03 #4).** `visual_pass.py`
  writes `params.model` — the model the backend reported for the sheets it read — beside
  the role it already wrote, so the coverage strip can say what read a clip rather than
  what the role maps to today (the close look moved tiers on 2026-10-03). Sidecars
  written before this carry no model and are shown as *model not recorded*.
- **Deep work on Opus 5.5, quick work on Sonnet 5.5 (Karl, 2026-10-03).** `config.DEEP_MODEL`
  (`claude-opus-5-5`) serves the skeleton and judge roles — the Ask, effect design, the
  model's Find, the themes — and now the close look and the audit too (`FINE_ROLE`, passed
  as `--role` to the visual pass); `config.QUICK_MODEL` (`claude-sonnet-5-5`) serves the
  analysis role — the coarse sheets, the estimates, the probe. Haiku is out. The visual
  pass's per-sheet prices were measured on the small tier and now scale by
  `config.price_scale` (sheets ×3, close-look windows ×5), so priced buttons read higher:
  Killington's 12-claim audit is ≈ $4.38 projected, not $0.88. Opus 5.5 needs Claude Code
  2.1.280+; the WSL CLI was 2.1.278 and was updated to 2.1.288.

### Added
- **Docs for INTAKE M15** (Karl, 2026-10-03 #4): app/README.md — the coverage strip in
  *What's on screen*, a section on how the agent sees and Look deeper with its endpoints;
  INTAKE M15 with its decisions and items by commit; HANDOFF roadmap item 1 notes the deep
  look exists.
- **How the agent sees a clip, and Look deeper (Karl, 2026-10-03 #4; INTAKE M15).**
  A **coverage strip** (`/deep.js`) per clip: lanes for what was heard, the coarse sheets'
  frames, the close look's windows, the deep looks' keyframes and the free motion track,
  the claims marked by evidence (confirmed / deep / unaudited / one look / contradicted),
  a legend that states each layer's granularity from the sidecar's own record (*a frame
  every 4 s, read 30 to a sheet … by role analysis, model not recorded*), hover for what
  was seen at a second, drag to choose seconds. On the board's inspector (the shot's
  range marked), under the pass's tape (the playhead live; no buttons — the pass is keys)
  and as a mini strip on the open screen's cards. `GET /api/coverage[/{clip}]`, free.
  **Look deeper** (`roughcut/deep.py`): keyframes where the picture changes (the 10 Hz
  motion track's peaks and turns, a uniform floor, the rest of the cap on the gaps with
  the most change; ≤ 24 frames, ≤ 20 s, 640 px, each its own file) read in order on the
  deep tier with the motion between them as numbers; beats marked `seen` on a frame or
  `inferred` between two named bracketing frames with why; events, the camera (the
  camera-roll guard: "unless the ground under the skis says otherwise"), what it was
  unsure of; at most one follow-up for up to 8 frames it asks for. Validated strictly;
  stored as `<stem>.deep.json`; a span already read is never re-bought. Priced by a dry
  run on the button (`POST /api/deep`, `POST /api/deep/quote`), one at a time, refused
  past the cap, a `deep` job in the top bar; on a seen-tab row, in the inspector for the
  shot, or by dragging on the strip. The result shows in place: the keyframes as a
  filmstrip, the beats under it (seen solid, inferred hatched between their frames), the
  frames it asked for marked. **The rank believes it**: a hot claim a deep span covers is
  `confirmed` (same family found) or `contradicted` (camera roll, nothing, or another
  family — R11's "upside-down mid-air" that was a fall); a deep event nobody claimed
  ranks at `DEEP` 1.3 (argued: the editor chose the seconds, every claim tied to a
  frame; one reader, so below two agreeing); the Ask's inventory carries the deep beats
  for those seconds. Provisional: the cap, floor, width and the price (≈ $0.51 for 24
  frames on Opus 5.5, ≤ $1.07 with the follow-up — no deep call has been measured).
- **The CLI tells you when it needs you, on every screen (Karl, 2026-10-03).**
  `inference.diagnose` turns a CLI failure into the thing to do — sign in
  (`claude auth login`), update (`claude update`), allow a blocked tool, wait out the
  plan's usage window, fix PATH — and every call records it (`last_problem`, cleared by
  the next clean call; a `permission_denials` inside a successful reply counts too). The
  server adds the free checks at launch (`claude --version`, `claude auth status`), a
  probe that calls each distinct model once (a quick-model probe said "ready" over a CLI
  that rejected every Ask), and the visual pass subprocess's error lines.
  `GET /api/backend` carries `fix`; `/cli.js` puts it across the top of the board, the
  pass and the open screen — what is wrong, the command with Copy, *Check again* — in
  the page's flow, never over a control. The header pill names the fix and both models.
- **The flow: where the project is, as one model (Karl 2026-10-03 #3, INTAKE M14).**
  `roughcut/flow.py` (pure) turns facts the server reads off the files into seven stages —
  Footage · Index · Brief · Pass · Cut · Polish · Render — each `done` / `running` (with
  progress) / `ready` / `waiting` (on which stage, why) / `needs-you` (the reason and the
  action) / `optional`, a one-line summary in the editor's words, the screen and dock tool
  where it is done, and the counts it rests on; plus **one** `next` action and `blockers`
  (the server's own `backend_fix`, so a signed-out CLI stands in front of every priced
  stage). `GET /api/flow`; `POST /api/asks/answer` marks the newest Ask proposal answered so
  a discarded one stops waiting on you. The index stage says its granularity in words from
  the coarse sidecars' own `interval_s`. The journal gains a row word, `waiting` — free
  stages settled, priced ones held by the pause — and `GET /api/index` carries what the
  pause holds, priced (`waiting`), from the index job's own arithmetic.
- **One flow bar on all three screens (INTAKE M14).** `/flow.js` draws `/api/flow` as a
  rail in each header — ✓ done, a fill while running, amber when it needs you, this
  screen's stages underlined — and a **Next** chip with the one recommended action. A click
  goes to the screen where a stage is done (the board honours `#tool=ask|fx|out` and opens
  that dock tool); a priced action navigates to the button that carries its price, a free
  one on its own screen is pressed (Render). It replaces the open screen's six numbered
  steps, the board's five-step strip and its `open · pass · board` pills; the pass gains
  it (a 31 px row, paid for in the frame's height).

### Fixed
- **The pass can finish: a moment on one frame is a range (INTAKE I16.0c).** A pick
  resting on one sampled frame was built with start == end (Killington: CLIP_04
  200.0–200.0, CLIP_10 25.0–25.0). X and U posted that zero range and the server
  answered 400 "is not a range", and `_overlap_ratio` divided by ~0 so no stored keep
  re-attached — the floor opened on "round 3 · pick 2 of 2" for ever while the bar said
  the pass was done. `picks._pick_from` now pads any pick shorter than 1 s to the look
  interval around its frame (4 s, clamped to the clip), and a zero-length range is
  matched as a point inside the other as a safety net. The 0.1 s keep a P left on
  CLIP_04 at 3:20 re-attaches; it is not removed.
- **A demoted claim reads "maybe: … · not checked", never "— not a claim: …" (INTAKE
  I16.0d).** A claim the look pass's own rules demoted (hedged wording, no frame, too
  long for a jump, medium confidence) put its rule into WHY — "Skier possibly airborne …
  — not a claim: hedged wording — a guess, not a claim (frames 3:20.0)" — on 4 of
  Killington's 92 moments, and on one of them it outranked a claim that stood. The
  moments stay (recall: *"You missed some cool jumps"*). WHY is built from claims that
  stand first; a pick with only a demoted claim says it short and plainly — "maybe:
  Skier possibly airborne or jumping on slope, framed with trees · not checked" — and its
  witness line reads the same way. The rule's reason stays on the witness's `demoted`.
- **The pass's closing card has no fake price (INTAKE I16.0e).** Its way out read
  "Assemble · $0.60", styled as priced, on a button whose only action was to open the
  board. It now reads "Back to the cut →" when a cut exists and "Make the first cut →"
  when none does, with no price; the second (button or `A`) opens the board with the Ask
  tool (`/#tool=ask`), where the first cut is priced before the click. Play the bin,
  Next round, By clip and Revisit later are unchanged.
- **A generated slide is never a keep (INTAKE I16.0i).** `selects.sync_timeline` adopts
  every shot no keep covers as a hand keep, and it adopted the black title slide
  (Killington: k_ca3d7755 `gen_black_3fb88607`), so the Bin said 43, the pass 44, and
  the Bin showed a "footage missing" card for it. Generated clips (`gen_*`) are now
  skipped by `sync_timeline`, a `pick` on one is refused, and `selects.ensure` leaves
  any such keep already in the EDL out of what it hands on — the Bin, its counts, the
  pass's and the flow's — so they agree. A read never rewrites the EDL; the old keep
  leaves the file on the next write made for another reason.
- **On the board plain U no longer undoes, and ⌫ takes out only a shot you chose (INTAKE
  I16.0 n).** U means "later" on the pass and meant "undo" on the board; undo is ⌘Z
  (the keys sheet, the undo button's tooltip and the proposal toasts say so). ⌫ / Del
  ripple-deleted whatever was selected, and the board selects on its own — the first
  shot at load, the shot under the playhead as the cut plays, the shot handed on after a
  delete — so a pass habit (⌫ = previous moment) took a shot out of the cut. ⌫ / Del now
  need a selection somebody made (a click on a block, a key that selects); otherwise
  they say "click a shot to choose it". X is unchanged.
- **The CLI banner's commands are right for the box the board runs on (INTAKE I16.0 m).**
  The board runs natively on foxtrot now, and the banner still said `wsl -e bash -lc
  "claude update"` and "Double-click this file in Explorer", for a path that exists only
  on foxtrot. `inference.HOST` reads the host once (WSL when `/proc/version` says
  microsoft; the hostname): under WSL the words are unchanged; on a native box the
  commands are plain — `claude update`, `claude` then /permissions, and the sign-in as
  `bash <repo>/scripts/claude-signin.sh` — with "Run this on foxtrot (from another
  machine, `ssh foxtrot` first)". Tests inject the host.
- **"Last proposal" is offered only while it waits (INTAKE I16.0 l).** The board offered
  "last proposal — 21 shots, 40 days ago · show it" on every load, from an Ask answered
  weeks ago. `GET /api/asks/latest` now says whether the record is `pending` — the flow's
  own `ask_pending` rule (not answered, newer than the cut on disk) — and the link shows
  only then; a Discard hides it at once. A waiting paid proposal is never hidden.
- **Rendered films say when, from which cut, and once (INTAKE I16.0 k).** Killington's
  ten renders span Jul 25 – Sep 8 and each row showed only a clock time; three were the
  same 17-shot preview; none was the cut on the board and nothing said so. Rows read
  "Sep 8, 10:05 PM · main · …" (`GET /api/renders` now carries each render's `cut`),
  renders of the same cut, shots, profile, music and note fold into one row with "×3"
  (the newest plays; the others are named on hover; renders too old to carry a shot
  list never fold), and when no row is the cut on the board one line says "this cut
  hasn't been made yet". "proposal … — not accepted" and "· this cut" are unchanged.
- **A typed title's check looks once the title has typed (INTAKE I16.0 j).** The accepted
  title slide fx_b9e6a61c showed a red "not drawn where expected: 0.00s changed 1.153%
  bbox [0.4313, 0.1889, 0.5719, 0.3361]": `fx.verify` sampled 0.1 s after the event, when
  a typewriter has drawn two characters high on the first line, and tested the block's
  centre (0.5, 0.5) against them. A text shape with a reveal is now sampled when it has
  revealed (`len(text) / cps` after its start for a typewriter, 0.4 s for a fade, plus a
  frame), kept inside the overlay, before the shape fades out and inside the shot; other
  overlays are sampled as before. Re-run locally on that title's own proof and base
  (fetched read-only from the board): picture_landed passes.
- **The bin · cut name reads right from the start, and each bin is listed once (INTAKE
  I16.0 h).** The switcher's stylesheet was injected only when its panel first opened,
  so the header read "killington-neutralmain" with no ▾ until then; it now loads at
  boot. `/api/projects` keyed bins by the path as written, and on foxtrot `~/footage` is
  a symlink to `/mnt/roughcut/footage`, so copper and killington were listed twice each;
  rows are keyed by the real path now (the most recently opened record wins).
- **The open screen stopped contradicting itself (INTAKE M14).** With the looks paused its
  header said "3 released · 9 queued" while its footer said every clip was released and
  every card wore QUEUED beside a lit `released` flag: two definitions of released —
  the journal's (every stage done) and the pass's (free stages done, picks from the
  words while paused). They are now two words: a card's flag is *on the pass*, a clip
  held only by the pause is *look paused* (badge, table row, counts), the title is
  *Looks paused*, and the pass hint says how many are on the pass from their words only.
  The paused box speaks to the editor — *Looks are paused — 9 clips, ~$X*, **Resume ·
  ~$X** — and repeats the journal's reason only when the app wrote it (the budget cap, or
  you), never a log line like "paused by the lead after the live kill test".

### Fixed
- **Look deeper's price is fitted to a measured run.** The first live deep look
  (CLIP_11 141–155 s, 24 frames, Opus 5.5) billed 108k in / 6.3k out, $0.70 against the
  $0.51 quoted — the CLI re-sends earlier frames on every Read turn (~3.6k in per frame,
  not 307) and the beats grow with the frames (~260 out each). `deep.price` is now
  base + per-frame tokens through `config.projected_usd`; the button reads $0.70, the
  bound with the follow-up $1.59. It read R11's fall correctly, by the skis against the sky.
- **The themes price test scales its calibration by tier.** The one live run ($0.20)
  was on the mid tier; themes are deep work now, so the bounds scale by the judge
  role's input price over Sonnet's (5/3 on Opus 5.5) rather than being loosened.
- **The CLI banner no longer pushes the board's dock off the screen.** The dock sizes
  itself to the header (`--hd`); the banner arrives later, above the header, so the
  dock overflowed the viewport by the banner's height. `cli.js` announces a `clifix`
  event when it comes or goes and `dock.js` counts it; the banner is in the page's
  flow (relative, not sticky). The suite resets the process-wide CLI problem before
  every test (`conftest.no_cli_problem_carried_over`): a scripted signed-out CLI in
  test_server left the banner up for the browser tests after it (found by the flow
  lane's run order).

### Documentation
- **INTAKE M14 · The flow** — the milestone, its decisions and items ticked by commit.
- **HANDOFF: the 2026-10-03 block** — Karl's four asks built, merged and live; the
  CLI update Opus 5.5 needs; the first live deep look and its measured price.
- **HANDOFF: the 2026-10-02 block** — the colour Ask, the claims audit and junk proposals
  merged; measured read-only on the real bins (Copper's junk proposals are exactly B1's
  nine; Killington's top-12 audit ≈ $0.88, unspent); suite 678 passed, 1 skipped.

### Added
- **The Ask reaches colour (INTAKE M10, I10.5)** — a note about the picture ("warmer",
  "less blue on the lift shot", "make it pop", "match the lift shot to the summit")
  answers with a `colour` patch on the EDL's block: the film's look / strength / mode /
  reference and per-shot overrides keyed by segment id, validated by
  `colour.validate_patch` against the real looks library — an invented look, an unknown
  key or a balance outside the clamps fails the plan and gets the one bounded re-ask. The
  prompt's *Colour* section names the looks with their descriptions, the vocabulary and
  its limits, and each shot's measured white L\*/cast, clipped % and current balance; it
  says to leave `colour` out unless the note is about the picture. A colour-only answer
  may omit `segments` (the cut verbatim, not polished). The proposal panel shows the grade
  in words (`colour.describe_patch`); Accept merges it over the board's block and saves it
  with the segments in one PUT; Discard drops both. The ✎ shot ask carries the clause for
  its own shot only and rejects one that touches the film or another shot.
- **A proposal's shots keep their ids** — `revise.carry_ids` gives each planned shot the id
  of the cut's shot it continues (same clip, ≥ half the shorter range), so Accept no longer
  mints new ids for every shot and strands their colour overrides.
- **INTAKE I10.5 ticked** with the commit and the lane's decisions (patch not block,
  reject-not-drop on the shot ask, no second inference path).
- **Docs for junk (HANDOFF roadmap item 5)** — HANDOFF's session-3 table row and roadmap
  item 5 say what is built and that the thresholds are provisional; app/README gains a
  *Junk* row with the two endpoints and loses the "junk not proposed" gap; INTAKE I5.1
  notes the junk band is on the wire.
- **Junk in the dock's bin and on the open screen (HANDOFF roadmap item 5)** — a proposed
  clip gets a card of its own at the head of the bin grid (a black clip rarely has a
  keep, and a proposal nobody sees is never answered), its keeps wear `junk?` with
  **Confirm** / **Keep**, and the bin opens on the grid when there is a proposal.
  Confirmed clips leave the grid's default view and the heard / seen rows; a `junk N`
  chip shows them, with **Keep** to undo. `/open`'s cards carry the `junk?` / `junk`
  flag I5.1 left off the wire. `test_junk_ui.py` (2).
- **Junk is proposed, the editor confirms (HANDOFF roadmap item 5)** — `roughcut/junk.py`
  reads the colour file the proxy stage already wrote, the audio sidecar's words and the
  duration, and proposes a clip as junk when it is essentially black (mean luma < 12/255
  and no sample above 40), one flat field (luma spread < 10/255) or under 1.5 s — and
  never when a word was heard. Held against B1/Copper's per-clip measurements it takes
  exactly the nine known junk clips and none of the dim real ones a naive `luma<35`
  takes (the night parking lot, the plane); every threshold is provisional, one bin's
  numbers. The EDL's `junk` block holds only the human's `junk` / `keep` (validated on
  PUT); `GET /api/junk` / `POST /api/junk`. A confirmed clip leaves the Ask's inventory
  (first cut and revision) and Find, and the index journal skips its look and close
  look — the money; a proposed one waits behind the clean clips in the index queue.
- **Audit the claims (HANDOFF roadmap item 1, R10's follow-up)** — the close look's windows
  go to the coarse pass's own unaudited jump/fall/crash claims first (notable, then score),
  then to motion peaks, inside the same per-clip budget; a window already read or a claim
  already covered to `COVERED` is never re-bought (`events.choose_windows`, used by
  `event_scan.py`). A claim window is the claim's span padded to ±4 s and capped at one
  sheet (14 s), so a "jump 136-160" comes back audited instead of still `unseen`. The
  whole-bin pass now scans only clips never looked at closely, so it cannot buy windows its
  price did not count.
- **`POST /api/visual/audit` and the seen tab's *Audit N claims · ~$x* button** — the close
  look on the bin's top 12 unaudited hot claims only, then the rank is rebuilt; `dry_run`
  prices it without spending, `/api/status` carries the same price, the button is off when
  nothing is unaudited or a pass is running. Not spent by anyone but the editor.
- **`fine-only` evidence (`FINE_ONLY` 0.7)** — a close-look moment no coarse claim agrees
  with no longer inherits `unseen`'s neutral 1.0; R10: "a good auditor and a poor detector",
  three of four such rows in its top fifteen were wrong by eye. Shown as *one look* on the
  seen tab, `claimed` on the floor, explained in the Ask's events header; `events.json`
  params carry it. Provisional — argued, not fitted.
- **The camera-inverted guard in both sheet prompts** — on a helmet or chest mount the
  camera rolls, not the rider: find the snow beneath the skis before calling a jump or an
  inversion; a tilted horizon alone is camera roll; unsure is `action`, not `jump`.
  `validate` keeps an event that rests on inversion words (*inverted, upside-down, flip*)
  but demotes it to not-notable until a second look agrees. `PROMPT_VERSION` 3.
- **Docs: roadmap item 1's audit half is recorded as built** — HANDOFF says what was built
  and what remains (the spend is Karl's; the weights are still argued, not fitted; no
  horizon-angle measurement), INTAKE's Discovered bullet points at the fix, and R10 has a
  "follow-up built" section.
- **A proposal on a shot the edits will create shows on the board (INTAKE M13)** — live,
  the typed-title-on-a-slide proposal (shot `new:1`) was on disk but on no shot's card;
  the list now carries `anchor_shot` (the first op's shot, or the shot the slide goes
  before / after) and the FX tool shows the proposal there until Accept.
- **The two lanes meet (INTAKE M13)** — a generated clip's `summary` is the server's object
  (`summary.generated` is the sentence); the block and the inspector read either shape.
  The FX card clears the ghost on Accept and Discard, and after an Accept that changed
  the cut it reloads the board so the timeline reads the new shots.
- **A generated slide's name is the renderer's, not the model's (INTAKE M13)** — live on
  Killington the model copied the example's placeholder clip name for the slide it
  generated and validation refused it against the real `gen_black_<key>.mp4`; for a
  `new:n` shot the segment's clip wins now. An edit-only design keeps no fallback
  moments, and the job's detail counts moments and changes to the cut.
- **The model proposes edits to the cut, and the card applies them (INTAKE M13, I13.3)**
  — an effect may carry `edits` (roughcut/edits.py's operations) beside or instead of an
  overlay: a slow-motion range with no picture at all, a black slide generated before a
  shot with the typed title on the slide it creates (`shot: new:1`). The vocabulary in
  the prompt says which is which (edits change the cut; the overlay and the sound change
  pixels and the mix), lists the ops with ranges, and adds two examples; the prompt
  describes THE CUT with every shot's id, range, film length and speed so ops can name
  shots and anchors. `validate_effect` checks the ops against the cut and the effect
  against the cut as it would be after them; `edit_words` say them in words. The card
  lists the changes, Accept reads `Accept · N changes` and applies them through the
  edits section's `apply_edits` (one write; the effect re-keyed from `new:n`), Preview
  shows the proposed cut on the ghost lane (`tlLanes.showGhost`) when the board has
  it, and Verify carries an `edits_apply` check (the proof is skipped for a shot that
  does not exist yet). `test_fx.py` +2.
- **The render retimes a shot and cuts a generated clip (INTAKE M13)** — in
  `assemble.py` a part whose segment has `speed` ≠ 1 is retimed: `setpts` on the video
  ahead of the `fps` conform (the frame count changes, the rate does not — and a 60 fps
  source slowed to half keeps its own frames instead of doubling decimated ones),
  `atempo` on the audio as a chain of factors inside its 0.5–2 window (0.25 is
  `atempo=0.5,atempo=0.5`), `-t` from `edits.dur`, an accepted effect's events moved
  to where the picture now is; the concat still copies. A segment whose clip is
  `gen_*.mp4` reads its source from the new `--generated-dir` (the board's
  `_render_job` passes `generated_dir()`), takes no normalise and no grade (a black
  slide stays the black it was asked for) and casts no vote in the delivery profile's
  frame size. The planned length and the drift check sum `edits.dur`. Also fixed: a
  segment with no `why` (a split's second half has none) no longer crashes the part
  log. `test_edits_render.py`: a 0.5× part of a 2 s range is 4 s at 24000/1001 and its
  3 s frame is the source's 2.5 s frame; a black part is 1920×1080 with luma at black
  and the film is the four parts end to end; a freeze still is the frame it names and
  holds; the atempo / setpts / source helpers.
- **Preview, apply and undo for the edits (INTAKE M13)** — `POST /api/edits/preview
  {ops}` is the cut as it would be (`segments`, `id_map`, `generated`, `changed`,
  `words`) from `edits.validate_ops` + `apply_ops` against the cut on disk, nothing
  written, no file made, the new shots still `new:n`; a bad op is a 400 with the
  sentence. `POST /api/edits/apply {ops}` is `apply_edits(ops)` — the module-level
  function Accept calls — which mints every `new:n` with `segment_id()` (the `id_map`
  filled in), materialises every generated clip *before* anything is written (an
  ffmpeg that fails leaves the cut as it was), re-keys the accepted effects whose shot
  was split to the piece that holds their events (events across two pieces make a
  copy per extra piece; an effect whose shot left the cut is dropped) and writes the
  EDL's segments once. **The board's undo does not cover this write**: the response
  carries `before` (the segments as they were, and `before_effects` when the effects
  changed) and `POST /api/edits/undo {before, effects?}` restores them, validated the
  way a save is (the save's segment cleaning is now `clean_segments`, shared).
  `test_edits_api.py` +8.
- **Generated clips are real files (INTAKE M13)** — a black slide, a colour or a freeze
  frame the model proposes is made by the server, never by the model:
  `materialise_generated(spec)` writes `gen_<kind>_<key>.mp4` under
  `work/generated/<bin>/` (`generated_dir()`) with ffmpeg — lavfi `color` + `anullsrc`
  for black and colour, one frame of the source clip's proxy at `at` looped over
  silence for a still — 1280×720 libx264 yuv420p + aac, the shape of a proxy, and
  reuses the file when it exists. Every generated file (and any a segment names that
  is not there yet, listed `missing`) is a clip in `GET /api/project` with a
  `duration`, a `proxy` that plays it (`/media/generated/{name}`, ranged like a
  proxy), a `poster` (`/media/poster/` falls back to the generated dir for a `gen_`
  stem), an empty transcript, a summary whose `generated` line says what it is and the
  spec under `generated` — so the monitor plays it, the kept tab does not break on it
  and the Ask's clip map carries it for an effect to be designed on. `test_edits_api.py`
  +4.
- **A shot's speed survives a save (INTAKE M13)** — the board can now put `speed`
  on a segment and `PUT /api/project` keeps it: validated by `edits.validate_speed`
  (a 400 with its sentence on a bad one; absent or 1 is not stored), returned as
  stored by `GET /api/project`, and every place the server sums a cut's length
  (`/api/render`'s `planned_s`, the shot list a render records) uses `edits.dur`, so
  a 2 s range at 0.5× plans as 4 s of film. `test_edits.py` covers the pure module —
  every op, every refusal, the words; `test_edits_api.py` the save and the plan.
- **`tlLanes.showGhost(segments)` / `tlLanes.clearGhost()` (INTAKE M13, I13.5)** — a
  public way to draw a proposed cut on the ghost lane: the same drawing the Ask's pending
  plan gets (`matchPlan` — unchanged shots dim, added green, moved with an arrow,
  removed struck out on V1), fed an explicit segments list. An id `new:n` is a shot the
  edit would make and is drawn as added whatever footage it takes (never matched to a
  shot of the cut by overlap); a segment's `speed` sets its ghost's width and shows
  beside its length; a generated clip is named by its kind. `playPlan` plays it, each
  shot at its rate. While an explicit list is up it wins over a pending Ask's plan;
  `clearGhost()` takes it down and returns whether there was one; `tlLanes.shown()`
  reads it. The lead's FX card calls this to preview an edit before Accept. Test: a
  list with a kept shot and a `new:1` at 2× — the classes, the positions, the strike,
  `playPlan`, a generated slide in the list, and `clearGhost`.
- **Generated clips on the board (INTAKE M13, I13.4)** — a shot whose clip is
  `gen_<kind>_<key>.mp4` (a black, a colour or a still the server makes for an edit,
  listed among the clips with a `proxy`, a `poster` and a `summary` line but no
  sidecar) draws a block that wears its kind as the name (`black`, `colour`, `still`),
  the clip's `summary` in place of a transcript line (`black · 2.0 s`, `colour
  #1a2b3c`, `still of CLIP_08 at 4:31`), a dashed edge, its poster from the clip, and
  plays in the monitor from its proxy like any other. Nothing that reads a clip assumes
  a sidecar any more: `linesFor`, `boundaryWarning`, the block's line and warnings, the
  inspector (the summary where the lines would be), the library's heard tab
  (`candidates`), the bin and the kept tab, and `tl.snapsFor` (an empty payload with the
  clip's length rather than a 404 per drag). The ghost lane names a generated shot by
  its kind too. Test: a black 2 s file in the proxies dir, `P.clips` carrying the
  server lane's shape — the block, the poster route, every tab, the inspector and the
  monitor.
- **A speed badge on the block and a speed row in the inspector (INTAKE M13, I13.3)** —
  a retimed block wears `0.5×` (nothing at 1×, so a plain cut looks as it did) and its
  tooltip says the film length it makes from the clip range; the inspector's header
  says `4.0 s at 0.5×`. Under the transcript lines a speed row: chips `¼× ½× 1× 2×` and
  a number box (0.1–4), the lit chip the shot's rate, a line saying what the range
  becomes in the film. They write through the new `tl.setSpeed(id, rate)` — one undo
  entry labelled `speed`, clamped to edits.py's bounds, rounded to 0.01, and 1 deletes
  the key from the segment so a 1× shot saves the way it always did; the block, the
  total and the monitor's rate follow. The box commits on change (a half-typed `0.`
  never retimes a shot) and shows the clamped value; a duplicate (⌘D) keeps its rate.
  Test: the chip writes `speed`, `#total` and the badge follow, undo / redo walk it,
  `tl.forSave()` carries it and the page keeps it through the autosave.
- **The monitor plays a shot at its speed (INTAKE M13, I13.2)** — `arm()` and the
  `play()` calls in `playFrom` / `advance` set the buffer's `playbackRate` to the shot's
  `speed` (after `load()`, which resets it) and keep it on `dataset.speed`; the JKL
  shuttle rides on `defaultPlaybackRate` and multiplies the shot's rate rather than
  overwriting it (a 0.5× shot under L twice plays at 1), and it reads its own rate from
  `defaultPlaybackRate`, not the composite. `boundary()` still ends the shot at `out` in
  clip time; a speed changed under a playing shot reaches the buffer through
  `syncPlayer`. The frame callbacks in grade.js and fx.js read clip time and are
  untouched. Test: `playbackRate` is 0.5 through the slow shot, the hand-over comes at
  the clip's out (4 s of film), the next shot plays at 1, L L reads 2 on the default
  and 1 on the buffer.
- **A shot's speed is film time on the board (INTAKE M13, I13.1)** — edits.py says a shot
  has a `speed` and its length in the film is `(out − in) / speed`; the board summed
  `out − in` in forty-one places. `tl.dur(seg)` (and `tl.speedOf`) on the foundation is
  now the one place that arithmetic lives, and every film-time site goes through it:
  `filmStart`, `total`, `shotAt` (whose `clipT` is `in + (t − start) × speed`), the
  block widths and labels, the ruler's extent, `split` at a film time, the lanes' sums
  (`matchPlan`, `slotAt`, the duck regions, the bin's outlines, the markers and events
  mapped at the rate), the keys' cut-walking, razor and Q/W, the reverse shuttle's
  clip-time drive, and the trims — a drag's travel is film seconds applied to the edge
  at the shot's rate, a roll's delta lands on each side at that side's rate so the
  film's length holds, and the magnet measures its reach in pixels of film. The monitor
  reports its film position at the rate (`filmAt`). Of the forty-one `.out - ` sites,
  twenty-six were film time and use `tl.dur`; fifteen are clip time (clamps, the resume
  and boundary checks, the trim tooltips, the trimmed / this-cut comparisons) and stay.
  `speed` rides the segment through `tl.forSave()` and `afterSave` (the server lane
  keeps it on disk). `test_timeline_speed.py`: a 2 s shot at 0.5× is 4 s on the ruler
  and in `#total`; the lanes, the keys and a handle drag all move at the rate.
- **Edits foundation (INTAKE M13, I13.0)** — Karl, 2026-09-20: *"the effect tool itself …
  needs to be able to make changes like this and even broader ones like slow motion or
  extension or creating new clips."* `roughcut/edits.py` is the closed vocabulary of
  operations on the cut the model can propose beside an effect — `extend`, `set_range`,
  `split`, `speed` (a whole shot or a range of it), `generate` (a black, colour or still
  clip into the cut), `freeze`, `insert`, `remove`, `move` — keyed by shot id in clip
  seconds, validated against the cut and the clips, and applied by a pure `apply_ops`
  that returns the new segments (new shots as `new:n`), the generated clips to make and
  one line per op in words. Two facts the app learns from it: a shot has a `speed`
  (`dur = (out − in) / speed`) and a generated clip is a real file `gen_<kind>_<key>.mp4`.
  Nothing writes yet: the server, the render and the board follow in two lanes.
- **The vocabulary grows where the title slide failed (INTAKE M12, I12.9)** — Karl's
  request *"fade in … this is the title slide … '2026 Blizzard Killington Vt with the
  Boys' in typewriter effect using the sound"* came back as three text shapes on one
  spot, letters twice the size on the monitor as on the master, no typing and a 20 Hz
  rumble. Now: a text shape holds up to four lines (`\n`), its `h` is one line's height
  as a fraction of the box on BOTH renderers, and it fits the box's width; every shape
  can carry `start`, `end` and `fade`; a text can `reveal` as `typewriter` (`cps`) or
  `fade`; a sound can `repeat` (`every`, `count`, `jitter`, `gain_end`) so a clatter, a
  heartbeat or a ticking is one hit many times; the model says in `limits` what the
  note asked that an effect cannot do (a shot's length, speed or framing) and the card
  shows it; the prompt carries sizes that read well and a third example, a typed title
  slide with a click per character. `test_fx.py` +3.
- **The range bar is the video (INTAKE M12)** — Karl: *"it's not clear how it connects
  to the current video / playhead."* The bar is now a filmstrip of the shot (eight
  stills from the proxy), the window is the lit part with the rest dimmed, each handle
  carries its time, a white labelled playhead on the strip follows the monitor on every
  frame, a drag on the strip scrubs the monitor (paused) and a line under the bar says
  where the monitor is — or that it is on another shot and a click brings it here.
- **The range bar's drag holds (INTAKE M12)** — Karl: *"the drag select for the effect
  is not working / doesn't move much after I click on it to drag, seems like playhead
  moving / jumping clip is glitching the tool itself."* Every move rebuilt the tool's
  DOM (and so the handle being held), and each parked playhead rebuilt it again. The
  bar, the inputs, the label and the timeline band now update in place while a handle
  is held, the tool never repaints during a drag and repaints once on release, and the
  moves are read on the window so a fast drag that leaves the 12-px handle still
  counts. `test_fx_ui.py` drags the right handle to the middle across eight moves and
  checks the handle is the same element, the window landed, the monitor is parked.
- **Effects for everything, not hit markers on skis (INTAKE M12, I12.8)** — Karl,
  2026-09-20: *"ensure that the tool also does not overindex on just putting hit
  markers on skis, we will use this FOR ALL EFFECTS."* The vocabulary's limits let a
  title hold for seconds and a tint cover the frame (`MAX_DURATION` 30 s, `MAX_SIZE`
  1.6, a flash as long as the effect); the system text says what an effect can be —
  instant (a marker, a flash, a stamp), continuous (a title, a tint, a vignette, a
  caption: one event at the start, a duration that holds, the centre at size 1.6 for the
  whole frame), following (a ring or a glow with dx / dy keyframes) — and carries a
  second worked example, a SEND IT title with a rising whoosh; the audio's peaks are
  offered as candidates for instant effects only; the placing call asks where the
  effect sits, the centre when it belongs to the whole frame; the checks say event,
  anchor and picture rather than hit and marker; the tool's copy follows (moments, a
  placeholder with five kinds of effect, "one stroke per place").
- **Effects, round two (INTAKE M12, I12.7)** — Karl's four points, 2026-09-20. (1) *Place
  on the frames* is gone as a choice: Design always places (the price is on the Design
  button) and a drawn reference skips the call because its marks are the anchors. (2)
  Picking the window no longer fights the timeline: a **range bar** under the where row
  shows the shot with the onset peaks, the hits and the playhead, its two handles drag the
  window and **park** the monitor on the frame (never play), a click on it parks too; and
  while the FX tool is open the window and the hits are drawn **on the timeline** as a
  band with ticks. (3) Reversible for good: **Remove** keeps the effect (status
  `removed`, files intact) with **Restore**; **Accept** over an accepted version keeps
  the old one under `previous` (five deep) with **Revert**; several effects stack on one
  shot in order; `GET /api/fx/peaks`, `POST /api/fx/restore|revert`. (4) The sketch is
  visible: amber strokes over a dark halo with a numbered dot per mark, a HUD on the
  monitor itself (count, goal, Use it, Cancel) instead of controls a screen away, and
  the reference as a thumbnail in the design box and on the card; the price on the Design
  button reads on its blue. Tests:
  `test_fx_api.py` remove / restore / previous / revert / peaks.
- **The window is the human's, a hit can be deleted, the tick is clicky (INTAKE M12,
  I12.6)** — Karl, after the first live effect: *"you added hit markers across the full
  footage — during most of which I was not hitting rocks, it was just part at the end …
  selection / human annotation must be part of the tool"* and *"the hit noise is not the
  clicky one from COD."* The FX tool's design box has a **where** row — from / to in clip
  seconds, each end set from the playhead while the monitor is parked on the moment,
  the whole shot by default; the onset search and the model are confined to it
  (`fx.design(window=)`, `POST /api/fx/design {window}` validated inside the shot), hits
  outside are dropped, and the window travels with the effect (`window`, shown on the
  card). Every hit on a card has a ✕ (not a hit — take it out; the last one says to
  remove the effect instead). The worked example's sound is the Call-of-Duty tick — an
  impulse, a 3.2 kHz triangle ping decaying in 35 ms and a breath of high noise, 90 ms —
  not a 1.8 kHz beep, and the system text says to mark only what the note names, fewer
  when in doubt. Tests: `test_fx_api.py` the window (confined, refused outside the
  shot, kept through accept and a nudge).
- **Effects live on Killington (INTAKE M12, I12.4)** — the two lanes merged (`a1d9b14`,
  the UI merge after it): on the rocks shot the model designed six hit markers with the
  tick, the proof rendered in seconds and every check passed; the FX tool, the monitor
  overlay and the sketch work on the live board. INTAKE M12 ticked through I12.4, HANDOFF
  and the README's screen table updated; app.js's job strip refreshes the FX tool when an
  fx job finishes; `test_dock_ui.py` counts five rail tools.
- **Effects directions (INTAKE M12, I12.3)** — `docs/design/effects-directions.html`: the
  seven ways an effect could be made (a program the model writes, generative image /
  video, generative audio, an asset library, model-written code rendered headless,
  vision tracking, an NLE), the one built and why, the loop and the checklist as the
  machine runs it, the alternatives with the free online / AI tools that fit each and
  what each would change in the vocabulary, and the costs per step. EFFECTS.md gains
  the 2026-09-20 addendum: Rule 4 turned inside out — the asset is generated, not fetched.
- **Effects in the render (INTAKE M12, lane fx-core)** — `assemble.py` folds a shot's
  accepted effects into its own part: `cut_with_effects` puts `[0:v]<video_filter>[vbase]`
  and `[0:a]volume,aresample[abase]` plus `part_graph`'s fragment in one `-filter_complex`,
  the sprite inputs between the source `-i` and `-t` (after an `-i`, a `-t` is an input
  option for the next input and would cut the sprites short), and everything else about the
  part identical, so the concat still copies. A proposal is not rendered; an event outside
  the shot is not drawn; the log says `fx: <name> × n at …`. Script deps gain pillow. Tests:
  `test_fx_render.py` — a two-shot EDL with one accepted and one proposed effect rendered
  through `assemble.py` twice (with / without): the first part rises ≥ 6 dB at each event
  and changes at each anchor, the second is untouched, the parts share stream properties,
  the film probes at 4.0 s with no drift warning.
- **The checks (INTAKE M12, lane fx-core)** — `fx.verify` runs the definition of done as a
  checklist: `in_shot`, `in_frame` (the box no more than half off, with the pose's offsets),
  `sync`, `on_onset` (skipped when the note names no impact or there is no track), and with
  a proof render `audio_landed` and `picture_landed`, measured through ffmpeg pipes into
  numpy: `audio_transient_at` (the loudest 5 ms inside ±40 ms against the 300 ms before; a
  rise ≥ 6 dB either against the part's own before or against the base at the same instant)
  and `frame_change_at` (pixels differing by > 24/255, the bbox of the *dense* change so
  encode noise cannot stretch it, the anchor inside it). A skipped check is `ok: None`;
  `ok` is every measured check passing. Tests: a 320×180 proof of the synthetic clip
  rendered through `part_graph` — +12 dB at each event, none between, the marker's bbox on
  the anchor, the flash across the frame — the whole checklist green on it, and each check
  failing on the effect that should fail it.
- **The model calls (INTAKE M12, lane fx-core)** — `fx.design` and `fx.revise` through
  `roughcut.inference` (judge role; never `anthropic`, never `claude`). The design prompt
  carries the note, the shot, the transcript lines inside it, the onset peaks as candidate
  impact times and — when the human drew one — the reference's goal and marks, which then
  *are* the anchors (t the nearest peak to the reference frame); the system text states the
  vocabulary with its ranges from the constants, the frame's (0,0) top-left, the -1..1 box,
  JSON only, and the worked hit-marker example. `place=True` cuts `frames_for` at the
  candidates, tiles a `contact_strip` (labels burnt in) to `workdir/strip.jpg`, asks where
  the skis are in each frame, re-anchors the events and drops the frames the model says are
  not hits (keeping at least one). The answer is validated, never trusted. Tests on a
  scripted backend: the evidence in the prompt and the vocabulary in the system, the
  proposal's shape, the reference anchors, placing by looking, and a revise that keeps id
  and events unless the note moves them.
- **The part graph (INTAKE M12, lane fx-core)** — `fx.part_graph` renders one sprite `.mov`
  per event and one `.wav` per effect into the workdir and returns the extra `-i`s plus a
  `filter_complex` fragment: `setpts=PTS-STARTPTS+t/TB` delays each sprite to its clip
  second in the part, `overlay=0:0:eof_action=pass:enable='between(t,…)'` draws it, the WAV
  is resampled and formatted to 48 kHz stereo float, `asplit` to one `adelay=<samples>S`
  per event, and one `amix=normalize=0:duration=first` mixes them under the base; labels
  `[vout]` / `[aout]`; a silent effect passes `[abase]` through; no events → nothing. The
  fps may be the profile's `24000/1001` string. Tests: the strings, the input counts, the
  pass-through cases.
- **The sprite rasteriser (INTAKE M12, lane fx-core)** — `fx.render_overlay_frames` draws a
  sprite's shapes (line with round caps, circle, ring, rect with its own rotate, polygon,
  text in DejaVu Sans Bold when present) in the unit box at 2× and downsamples with LANCZOS
  into full-frame RGBA PNGs, one per frame for `ceil(duration × fps)`: the box `size × w`
  square on the anchor, `pose_at` scaling, fading, rotating (clockwise positive, as a
  canvas) and offsetting it, a `flash` tinting the whole frame. `render_overlay_mov` wraps
  them as a PNG-coded `.mov` with alpha and cleans the frames up. Nothing in pixels: the
  same overlay on a 640- and a 1280-wide frame is the same fraction of it. Tests: the alpha
  mass sits on the anchor at the box size, dx / rotate / scale / opacity each move it as
  they should, the flash's corner pixel, text and shapes, the mov's codec / pix_fmt / frames.
- **The synth (INTAKE M12, lane fx-core)** — `fx.synth_sound` renders a patch to a 16-bit
  stereo WAV with numpy alone: tone (sine / square / saw / triangle), sweep (an exponential
  glide freq → freq_end), noise (white; pink through Kellet's three-pole approximation) and
  click (a one-sample impulse through the decay) layers, each under a linear attack and an
  exponential decay to -60 dB at `decay`, an optional one-pole hp / lp, summed with their
  gains, then `gain_db` and a -1 dBFS peak limit; the same samples both sides; the noise is
  seeded so a patch renders the same bytes twice. Fixed on the way: `onset_peaks` returned
  every sample of a flat window as a peak (floor == mean over zero variance) — the suite's
  0.2 bed came back as eight impacts; a flat window now has none. Tests: `test_fx.py` — the
  validation edges, the onset peaks on a synthetic track, the synth read back (frequency by
  FFT, the -60 dB decay, the click, the filters, the sweep, the ceiling).
- **Live nudging on the monitor (INTAKE M12, lane fx-ui, 4 of 4)** — EFFECTS.md, rule
  4: *"Two clicks beats any amount of inference."* A hit clicked on its card is
  selected (the row lights, the card says `hit 2 selected · click the monitor (paused)
  to move it there`) and the monitor parks on its frame; a click on the paused
  monitor then moves that hit's anchor to the click — `PUT /api/fx/{id}` with the
  changed events, fractions of the frame with the picture's letterbox subtracted, the
  server clearing the checklist — and the overlay draws it where it went. The
  listener is capture-phase on the screen, so the screen's own click (play / pause)
  does not fire for a move; with nothing selected, or while playing, the click stays
  the screen's. The same hit clicked again deselects. Tested with a real click at
  (0.25, 0.60): the server's copy moves within 0.03, the other hit and the time stay,
  the sprite's alpha is found at the new anchor, playback did not start, and the two
  pass-through cases still play and pause.
- **Draw a reference — the sketch on the frame (INTAKE M12, lane fx-ui, 3 of 4)** —
  Karl: *"options for human to draw references on a keyframe."* Draw a reference in
  the FX tool pauses the monitor (parking it on the selected shot when it is
  elsewhere) and hands `#fxCanvas` the pointer (`.sketch`, crosshair): strokes are
  drawn as a 3-px accent line on screen whatever the proxy's size, several of them,
  ⌫ undoes the last and Esc cancels — on window, capture, registered at load, so
  while drawing neither reaches the timeline's ripple delete or the dock's overlay —
  and the tool shows a small toolbar: the stroke count, a goal text (`the skis — put
  the markers here`), Use it / Cancel. A stroke's points are fractions of the frame
  (the picture is object-fit: contain inside the screen, so the letterbox is
  subtracted), each stroke's centroid is a mark, and Use it builds `{t: the live clip
  time, goal, strokes, marks, png}` — the png a data URL of the frame drawn into an
  offscreen canvas at the video's own size with the strokes on it — which the design
  box shows as `reference · 2 marks at 0:01.5 · the skis…` (with a ✕ to forget it)
  until the next Design carries it; the server keeps the marks as the anchors, no
  placing call. Clicks on the monitor while drawing do not start playback. Tests draw
  with the mouse on the real canvas and check the marks against the drag's midpoints,
  the fractions, the PNG, the ⌫ and Esc paths, and the designed effect's anchors.
- **The monitor shows and sounds the effects (INTAKE M12, lane fx-ui, 2 of 4)** —
  `#fxCanvas`, a 2D canvas over the monitor's live video (the grade.js pattern:
  `requestVideoFrameCallback` on the live element, re-armed when it changes; a parked
  video draws on `seeked`), draws every accepted or proposed effect of the live shot
  at the live clip time from the same JSON the render draws from, sized to the proxy's
  `videoWidth × videoHeight` so the same fractions land the same way on the master:
  the sprite box is `overlay.size × width` px square, centred on (x·width, y·height)
  plus (dx·width, dy·width), shapes in -1..1 across the box (a coordinate × half the
  box; a stroke `width` and a text `h` × the box), `poseAt(overlay, t)` — the same
  rules as `fx.pose_at`, checked against it in the browser at nine times — scales,
  fades, rotates and offsets the whole sprite, `flash` tints the whole frame for its
  duration, and an event draws only while `t ≤ clipT < t + duration` and only inside
  the shot's range. The sound: one `Audio` per effect from `/api/fx/{id}/sound.wav`,
  started when the live clip time crosses an event's `t` while playing, once per
  event per pass (reset on seek and on play; two hits inside one WAV overlap through
  a clone). Effects always show — no toggle. Tests park the monitor on a hit and read
  the canvas's pixels back: the sprite's alpha at the anchor, the flash's 15 % in a
  corner, nothing a frame earlier, not live on a shot without effects; Preview plays
  the shot and the sound starts twice for two hits, once more after a seek.
- **The FX tool on the board (INTAKE M12, lane fx-ui, 1 of 4)** — `app/static/fx.js`
  fills the dock's FX tool (`#fx`) for the selected shot (`tl.state.anchor`, followed
  through `tl.on('select')`): a header `FX · shot 1 · CLIP_A`, the shot's effects as
  cards — name, `n hits`, a `proposed` (amber) / `accepted` (green) chip, the note and
  the why, the events as `0:01.5 · x 0.50 y 0.70` rows with ◀ ▶ one-frame nudges
  (`PUT /api/fx/{id}` with the changed events; the server's copy is what the card
  shows back), the machine's checklist when Verify ran (✓ / ✗ / – per check with its
  detail), and Preview (the shot plays in the monitor, this shot only) / Verify /
  Iterate (a one-line box, Enter or Go → `POST /api/fx/revise`) / Accept / Discard for a
  proposal, Remove for an accepted one — and the design box: a textarea, a `place on
  the frames` checkbox whose label carries the price from `GET /api/fx/price?place=1`
  (`≈ $0.21 · 8 frames`), Draw a reference and Design (`POST /api/fx/design`). Jobs
  (`kind: fx`) are followed on the same `/api/jobs` registry the strip paints; a job
  that finishes — including one seen finished for the first time after a reload —
  refetches `GET /api/fx` and repaints, and the tool repaints only when what it is
  built from changed, so a poll never interrupts typing. The rail badge counts the
  shot's effects. Accept is the human's click; nothing here writes the EDL except
  through the endpoints. `app/static/fx.css` styles it from the page's tokens.
  `app/tests/test_fx_ui.py` drives it in a real browser with `fx.design` / `revise` /
  `synth_sound` / `part_graph` / `verify` monkeypatched for the module (the render
  lane's Python is not on this branch) and a FakeBackend on `roughcut.inference`; the
  EDL seed carries fixed shot ids because a read mints ids in memory and never writes
  them, and the fx endpoints look a shot up by id in the file.
- **Effects foundation (INTAKE M12, I12.0)** — AI-designed video + audio effects, the
  contract and the loop. Karl, 2026-09-20: *"Add call of duty hit markers where my skis
  are with the sound effect … AI then goes and adds separate overlaid video with the
  effect (which it also generates itself) and the audio … AI also tests / verifies that
  the DoD is complete … options for human to draw references on a keyframe."*
  `roughcut/fx.py` is the vocabulary (a procedural sprite — shapes in a unit box with
  keyframed scale / opacity / rotate / offset and a flash — and a procedural synth patch
  — tone / noise / click / sweep layers with envelopes — both drawn by code from one
  JSON; Rule 1 and Rule 2 of EFFECTS.md hold, Rule 4 turns inside out: the asset is
  generated, never fetched), the validation, the pure helpers (`onset_peaks`,
  `pose_at`, `events_in_shot`, `price`) and the signatures the two lanes fill (render,
  synth, `part_graph`, the model calls, `verify`). Effects key to a **shot** and sit at
  clip seconds. The server: `GET /api/fx`, `POST /api/fx/design|revise|verify` as jobs
  (`kind: fx`), `accept` / `discard` / `remove`, `PUT /api/fx/{id}` for the human's
  nudges, the files an effect owns (`sound.wav`, `proof.mp4`, `base.mp4`, `strip.jpg`,
  `ref.png`), `GET /api/fx/price`, `effects` on the project GET and re-validated on
  PUT; a proposal lives in `work/fx/<bin>/` and reaches the EDL only on Accept; Verify
  renders the one shot from its proxy with and without the effect (`_fx_proof`) and
  runs the checklist. The board: an FX rail tool, `#fxCanvas` over the monitor,
  `/fx.js` + `/fx.css` (stubs until lane fx-ui). `test_fx_api.py` (9) drives the loop
  with the model and the renderer stubbed.
- **The dock resizes and wears icons (INTAKE M11, I11.6)** — drag the dock's left edge
  to set its width (300–720 px; the main column and the timeline re-fit; remembered per
  browser; double-click resets; `dock.width()`), an icon and a label on every rail tool,
  accent badges, rounded controls and a filled selected tab in the panel. Karl,
  2026-09-20: *"make it so we can drag / change the size of the right tool area … icons
  for bin ask, sound, out."* One more `test_dock_ui.py` test drives the drag.
- **The dock (INTAKE M11)** — the board's right column is a rail of tools and one
  panel the height of the viewport that scrolls inside itself (`index.html`, `/dock.js`),
  in place of eight panels stacked 2,900 px tall beside a 900 px viewport. Karl's calls
  (2026-09-20, from the board-directions review): the Dock and stop there; renders a tool
  on the board; Find as the bin's search box; the pass's labels; a grid with filter chips;
  adding inserts at the playhead. Tools: **Bin** (the default — a card per keep with its
  still, range, reason, note and the pass's labels as chips — hero, its tags, and the kinds
  of evidence its witnesses carry (heard / seen / felt, the only labels Killington's keeps
  have today); the same chips as a filter
  with counts plus hero / in the cut / not yet; the search box filters as you type with
  Find and Ask the model one keypress on; click selects, `enter` adds at the playhead,
  `space` or a double-click plays the clip in the bin's own player; heard / seen as
  before), **Ask** (the ask, Cut from the bin, the story), **Sound** (music), **Out**
  (renders and versions); badges count keeps and versions; the open tool is remembered.
  Adding — the bin's button, its Enter, a heard / seen row, a Find match, a drop — lands at
  the cut point nearest the playhead through the lanes' `insertAt` (one undo entry each).
  Project facts are a header popover, the keys an overlay on `?` that the timeline's map
  renders into, and the header keeps `--hd` current so the dock's height follows the
  steps and progress strips. The lanes read a keep off its row (`row._keep`) now that the
  grid filters, and a click on the music lane reveals the Sound tool. Tests:
  `test_dock_ui.py` (8); the browser tests that reach into a closed tool open it first.
- **`/grade.js` served (INTAKE M10, I10.4)** — the one route the UI lane could not add
  (`server.py` is the lead's): mirrors `/switcher.js`. I10.3 and I10.4 ticked in INTAKE
  with the lanes' hashes; M10 is built through I10.4, I10.5 (the Ask) and I10.6 (Karl's
  look) remain.
- **I10.1 ticked in INTAKE** with the lane's hashes and the live Killington check: the
  21-shot cut rendered as shot (106 s) and auto + alpine 0.5 (114 s), every shot balanced
  from its snow, film YAVG 144.8 → 164.2, comparison sheet kept in `roughcut-lab/out/`.
- **`test_colour_render.py` measures a real balance.** After the grey-scene rule the test
  pattern (colourful, no white surface) gave the auto no evidence, so the fixture now
  stamps every sample with the facts of an overcast snow frame; the balance is `surface`
  and the pixel hash carries the "reached the pixels" claim (mean luma moves only 0.77
  on the bright pattern because the shoulder and the lift nearly cancel).
- **The grey-world fallback needs a nearly-grey scene (INTAKE M10, `colour.GREY_MAX_CHROMA`
  = 10).** Measured on Copper: the ski clips find a snow white reference and the auto nudges
  them ×0.99–1.16; the night and indoor clips stay as shot; but the daylight airport-bar
  clips (chroma 16–26) fell to the grey-world fallback and were cooled by the whole
  half-clamp — Karl's "auto WB fails indoors" case. A colourful scene now gives the auto no
  evidence and renders as shot. One test added (a warm frame → no balance).
- **I10.0 ticked in INTAKE (`d84082d`)** with the decision that colour has no journal stage
  (measured right after each proxy; files are the truth), and the looks library's first
  manifest: `assets/looks/manifest.json` with `soft`, `punchy` and `cold` beside the three
  formula looks in the code; `assets/README.md` says how a `.cube` joins it.
- **The grade reaches the render (INTAKE M10, I10.1 + the render half of I10.3).**
  `research/tools/assemble.py` resolves every shot's colour the way the board's inspector
  does (`colour.resolve_shot` over `--colour-dir/<stem>.colour.json`, the film's
  `colour.reference` shot first, else the first measured shot; `previous` for `match`),
  bakes one 33³ `part_NNN.cube` per graded shot next to its part and applies it with the
  explicit-range `lut_vf` chain inside the part's own `-vf` — no extra generation. An HDR
  source (iPhone HLG) is tone mapped to SDR 709 *before* the scale whatever `colour.mode`
  says (normalising is not a grade); every part is tagged `tv`/bt709 so the concat sees
  identical streams (verified pixel-identical on ffmpeg 7.0.2); the render log gets one
  `colour:` line per part (`balance grey ×1.03 · look alpine 0.50`, `as shot`,
  `· normalised hlg`). `--colour-dir` absent or a clip unmeasured → look only, no balance.
  Script deps gain numpy. Tests: `test_colour_render.py` (5): auto + alpine bakes cubes and
  moves luma per stage and saturation overall; `mode: off` is byte-identical to the pre-M10
  render (no block + no colour dir) while no block *with* colour files is the auto
  (decision 5); an HLG shot under `mode: off` lands bt709/tv with YMAX ≤ 235; a per-shot
  look override bakes only that shot; a `.cube` look from a manifest inverts the picture.
- **The monitor shows the grade, the inspector controls it (INTAKE M10, I10.4; decision
  4).** `app/static/grade.js` (new): a `<canvas>` over the monitor's live `<video>`,
  drawn on every frame (`requestVideoFrameCallback`, rAF fallback, plus a redraw on
  seek so a parked monitor is graded too) through a WebGL shader that applies the shot's
  17³ LUT from `GET /api/lut/{id}` — a `sampler3D` on WebGL2, the flat .cube table as an
  n × n² 2D texture with the blue slices interpolated by hand on WebGL1 — cached by id,
  dropped on every save. The videos are never replaced: audio, the clock and the cut
  points are untouched; an identity LUT, a shot outside the cut and a missing WebGL all
  show the raw video. `G` toggles it with a toast (before / after); the keys line under
  the monitor and the Keys panel say so, and the transport's keys line now clips with an
  ellipsis rather than wrapping so a short window still fits the whole monitor. The
  inspector gains a Colour block between the trims and the scoped ask: the witness line
  (`white: surface · L 72 · cast b −1.8 · auto ×1.21`, `no white reference · as shot`,
  `hand-set`, `clipped 1.5 %`), the per-shot auto checkbox, look select (the film's,
  none, every library look — a broken manifest entry disabled with its reason) and
  strength, warmer / cooler (gain r ±0.02, b ∓0.02 from the balance the shot resolves
  to), brighter / darker (exposure ±0.05), reset, match ← previous / reference, set as
  reference, and the film row (mode, look, strength). Every change rides the one save
  body (`colour` beside `segments`, `story`, `music`), then `/api/colour` is refetched
  and the block refills in place — a why being typed is never interrupted — and the
  monitor's LUT is invalidated. `app/tests/test_colour_ui.py` (8 browser tests): the
  canvas and `G`, the LUT the monitor holds is the server's, the block and its looks,
  a per-shot look and strength saved by id and baked by `/api/lut`, film `off` making
  every shot the identity, warmer / brighter / reset on the balance override, match and
  reference by id, and a trim keeping the block. Not on the undo stack, like the music.
  `server.py` needs a one-line `GET /grade.js` route mirroring `/switcher.js` (this
  lane does not own the server file; the test registers a stand-in). A named-element
  trap found on the way: a `<canvas id="grade">` is `window.grade`, so app.js checks
  the module by shape and the board paints without it.
- **The colour core and its server wiring (INTAKE M10, I10.0 and the contract for
  I10.1–I10.4).** `roughcut/colour.py`: the probe (pixel format, bit depth, transfer,
  primaries, range, rotation, camera family from the tags), `normalise_vf` (HLG/PQ → SDR
  709 limited by zscale + Hable, input tags stated), `sample_frames` / `measure` /
  `summarise` / `measure_clip` (luma percentiles, clipped fraction, chroma, the white
  reference — bright near-neutral ≥ 20 % of the frame — or the shades-of-grey fallback),
  `balance_params` clamped per camera family with the shoulder that never adds clipping,
  `match_params` (Lab means toward a reference, spread clamped 0.85–1.15), three formula
  looks + `load_looks` over `assets/looks/manifest.json` (`.cube` drop-ins, broken entries
  reported not raised), `bake_grid` / `cube_table` / `write_cube` / `read_cube` /
  `apply_cube`, `lut_vf` (the explicit-range chain that passes the grey self-test),
  `validate_colour` (the EDL's `colour` block: mode, look, strength, reference, per-shot
  overrides keyed by id) and `resolve_shot` (balance → match → look at strength, one
  callable per shot). Server: per-clip colour files under `work/colour/<bin>/` measured
  from each proxy as it is built (no new journal stage — files are the truth), an HDR
  source normalised into its proxy, `GET /api/colour` (looks, every shot resolved with its
  witness), `GET /api/lut/{id}` (a 17³ table for the monitor), `PUT /api/project` accepting
  `colour` validated like music, `--colour-dir` passed to assemble.py. Tests:
  `test_colour.py` (20, the lab's numbers pinned: 45 dB lut3d parity, grey 128 → 126, an
  HLG lavfi clip normalised to SDR) and `test_colour_api.py` (5). numpy joins the deps.
- **INTAKE M10 takes Karl's two constraints:** colour state is per project (per-clip
  measurements, per-shot auto, look/reference in the EDL, a white reference that does
  not assume snow with a shades-of-grey fallback, clamps per camera family), and mixed
  GoPro + iPhone bins normalise first (iPhone HLG 10-bit → SDR 709 from the probe's
  tags, proxies included, one synthetic HLG clip in the test project).
- **INTAKE M10 — colour: correct, match, look — planned as the next priority after I9.6.**
  Session 15 researched what colourists do with GoPro footage and ran a local lab on
  both bins (16 frames, numpy/OpenCV, ffmpeg 7.0): a snow-referenced auto-balance with
  clamped gains, exposure and a soft shoulder brings Killington's blue-grey snow to
  white and adds no clipping on any frame; one 33³ `.cube` per shot applied with
  `lut3d` reproduces numpy at 45 dB; the full→limited range trap is measured and the
  explicit-range chain fixed; the grade costs +20 % on a preview encode and +33 % on
  delivery; Reinhard matching must clamp the spread. Six decisions (closed vocabulary,
  keyed to segment ids, one LUT per part on the existing re-encode, WebGL preview on the
  monitor, an auto that can only nudge, looks as a library) and items I10.0–I10.6.
  HANDOFF points at it. Lab artefacts kept outside the repo in `Projects/roughcut-lab/`.
- **I9.3 ticked in INTAKE (M9, keyboard editing)** with the two commits (`6801ae3`,
  `7505826`), the counts (13 tests; 71 with the foundation's and the flow suite) and the
  lane's decisions: the `window`-capture listener, `o` left to the switcher until a clip
  is open in Find, `j`/`k` no longer moving the card selection.
- **Keyboard editing on the timeline, 2 of 2 — the editing keys (INTAKE M9, I9.3; lane
  `agent/tl-keys`).** In `timeline-keys.js`: `C` razor — `tl.split` at the playhead on the
  selected shot when the playhead is inside it, else the shot under the playhead, both
  halves selected with the anchor on the first, a toast when the cut point is within
  0.2 s of an edge; `Q` / `W` trim the selected shot's in / out to the playhead through
  `tl.setRange` (Premiere's trim-to-playhead), the playhead then parked on the new edge,
  a toast and no edit when the playhead is outside the shot or the trim would leave less
  than 0.2 s; `X` / `Delete` / `Backspace` ripple-delete the whole selection through
  `tl.remove` (app.js's `x` removed one — it is taken over), the shot that takes the
  place selected; `⌘A` selects every shot; `⌘D` duplicates the selection after itself as
  one undo entry (`tl.begin('duplicate')` … `tl.insert` per shot … `tl.commit`), the copies
  selected and re-keyed by the next save; `Esc` clears the selection as well as the
  marks; ⌘Z / ⌘⇧Z stay the foundation's, bound once. The map's table gains the rows.
  `test_timeline_keys.py` grows to thirteen: `C` at 1 s into shot 1 makes two shots whose
  lengths sum to 2.0 and the EDL on disk has three distinct ids after the save, an
  unselected cut goes to the shot under the playhead, an edge cut is refused; `Q` then
  `W` trim to the playhead with `undo: trim` in the tooltip and a toast outside the shot;
  `⌘A` + `X` empties the cut and one ⌘Z brings both back, Delete / Backspace hand the
  selection to the heir; `⌘D` duplicates one and then a pair as a single undo entry;
  `Esc` clears the selection; the map lists every editing key; `c` / `x` / Delete typed
  into an input, the story and a card's why do nothing to the cut.
- **Keyboard editing on the timeline, 1 of 2 — transport and navigation (INTAKE M9, I9.3;
  lane `agent/tl-keys`).** `app/static/timeline-keys.js`, loaded after the foundation and
  registered by wrapping `tl.mount` (the foundation has no mounted event); a keydown
  listener on `window` in the capture phase that stops only the keys it owns, so space,
  `u`, the brackets, the foundation's `+ - \` / ⌘Z / ⌘⇧Z and the switcher's `o` keep
  their owners; nothing fires while typing. **JKL** shuttle on the monitor: `L` plays,
  again for 2× / 4× / 8× (`playbackRate` and `defaultPlaybackRate` on both buffers, so the
  hand-over at a cut keeps the speed); `J` reverses by driving `currentTime` from rAF as
  the pass does, stacking to −8×, parking on the previous shot's out-point at a cut and
  holding until the reload's park has run; `K` pauses; K held with L or J plays at 1× while
  held; space or a click after a shuttle plays at 1×. `↑` / `↓` previous / next cut (the
  playhead to the boundary, that shot selected — what `j` / `k` used to do on the cards),
  `Home` / `End`, `←` / `→` one frame (`⇧` one second) — the playhead only, so the trim
  lane's `,` / `.` stay the edge nudges. `I` / `O` mark in / out on the clip in Find (the
  board's only whole-clip player) shown as two ticks on a bar beside the transport's
  position; `↵` with both marks inserts the range after the selected shot with the clip's
  transcript line as why; `⌥I` / `⌥O` and `Esc` clear; playing the cut, `I` says to open
  a clip and `O` stays the switcher's. A **Timeline** section in the Keys panel rendered
  from the module's one table, with the trim lane's `,` `.` `S` and the foundation's
  `+ − \`; `?` brings it up. `timeline.css` gains the marks bar and the map's rules.
  `test_timeline_keys.py`: eight playwright tests (L stacks to 8× and K resets both
  buffers; J moves `currentTime` back over 300 ms, stacks, crosses the cut onto CLIP_A's
  out-point; K+L; `↓` from shot 1 lands at 2.0 s with shot 2 selected, `↑` / Home / End;
  frame steps leave the selection alone; I / O / ↵ on the Find clip insert `[1.0, 2.5]`
  with "hello there" as why while `o` without a clip opens the picker and Esc closes it;
  the map lists JKL row for row from the table; keys are ignored while typing).
- **I9.2 ticked in INTAKE (M9, lane `agent/tl-trim`)** with the two commits, the counts
  and the lane's decisions.
- **The magnet — snapping while an edge or a cut is dragged (INTAKE M9, I9.2; lane
  `agent/tl-trim`).** Within 8 px, in priority: another cut point (for a roll — on a
  ripple every other cut either lies on the far side of the block or moves with the film
  as it closes up), the playhead (where it stood when the edge was taken: parking the
  monitor on the dragged edge goes through the foundation's seek, which moves the
  playhead with it), a sentence `cut_in` for an in edge / `cut_out` for an out edge, a
  word start, an onset — the clip's points from `tl.snapsFor`, so Roughcut's own dividend
  is what the hand lands on. A vertical snap line across the ruler and the lanes at the
  taken point, labelled `sentence` / `word` / `onset` / `cut` / `playhead`, and the
  tooltip says what it took. `S` toggles the magnet (a `magnet · on/off` indicator by the
  ruler's right end, also a button; kept in `localStorage`); ⌘/ctrl held while dragging
  suspends it for that drag. The block being dragged shows its clip's sentence cut points
  and onsets as faint ticks inside it, mapped to film time and re-laid as its in point
  moves, so the editor sees what there is to snap to. `timeline.css` gains the ticks,
  the snap line and the indicator under the same `/* ---- trim (I9.2) */` banner.
  `test_timeline_trim.py` grows to nine: an out handle released 4 px short of a
  sentence `cut_out` (4.45 s) lands on it exactly with the line saying `sentence`, the
  ticks drawn, and one ⌘Z back; the playhead is taken before a sentence and a word
  before nothing, and an in edge takes a `cut_in`; `S` turns the magnet off (the
  indicator, the store, no line, the raw value) and on again, ⌘/ctrl suspends one drag,
  and the indicator's click toggles too.
- **Trims by drag on the timeline (INTAKE M9, I9.2; lane `agent/tl-trim`).**
  `app/static/timeline-trim.js`, built on the foundation's API and never editing it: every
  V1 block wears two 8 px edge handles (`ew-resize`) — drag one and the shot's `in` or `out`
  moves with the film closing up behind it (ripple), a tooltip over the edge giving the new
  clip time and the shot's new length while the ruler and the total follow live; a 10 px
  zone straddling every cut (`col-resize`) rolls it — the left shot's `out` and the right
  shot's `in` move together, the film's length unchanged; ⌥/alt + drag on a block's body
  slips it — `in` and `out` together, the length kept, clamped to the clip. One
  `tl.begin` on pointerdown, `setRange` per move (rAF-throttled; the posters' settle rule
  is the foundation's), one `tl.commit` on pointerup — a whole drag is one undo entry —
  `tl.cancel` on Esc mid-drag; pointer capture; less than 3 px of travel is a click
  (selects, does not play, does not trim); clamped to the clip and the 0.2 s minimum; the
  monitor parks on the edge being dragged through `tl.seek` (an out edge a hair inside
  its own shot, since a film time on a cut belongs to the next shot). `,` / `.` nudge the
  *active edge* — the last one dragged, else the selected shot's out — by a frame
  (1/30 s, at the foundation's 0.01 s), `⇧,` / `⇧.` by a second; these two keys are this
  lane's. The pointer maps to seconds at the zoom of pointerdown, deliberately: the
  foundation refits the cut after every change until someone zooms. The clip's length
  comes from the snaps payload (warmed per clip at mount), since app.js's clips are not
  on the foundation's API. `timeline.css` gains the handles, the roll zones and the
  tooltip under a `/* ---- trim (I9.2) */` banner. `test_timeline_trim.py`: six playwright
  tests — an out-handle drag trims, the totals and the EDL on disk follow, one ⌘Z
  restores; an in-handle drag closes the film up and cannot cross the out; a boundary
  drag rolls with the film length held (and stops where a shot would begin before 0);
  ⌥-drag slips with the length kept and clamps to the clip; a press without travel is a
  click and Esc cancels a drag with nothing on the stack; `,` / `.` / `⇧.` / `⇧,` nudge,
  the dragged edge becomes the active one, and an input keeps its own keys.
- **I9.4 ticked in INTAKE** (lane `agent/tl-lanes`): the two commits, the counts, the
  decisions the brief left to the lane, and what the foundation lacked.
- **Drag and drop on the timeline (INTAKE M9, I9.4, part 2).** In `timeline-lanes.js`:
  **move by drag** on V1 — a pointer-drag on a block's body (not within 8 px of an edge
  and without alt, which are the trim lane's) carries the block, or the whole selection
  when the block is in it, with a **drop line** at the cut point it will land on (the
  nearest one, snapping), the film closing up behind it through `tl.begin('move')` →
  `tl.move` → `tl.commit()` — one undo entry, ids travelling; under 4 px it is the
  foundation's click. **Drop from outside**: the kept tab's rows, Find's result rows and
  the bin lane's `available` outlines are draggable and carry
  `application/x-roughcut-shot` = `{clip, start, end, why}`; dropped anywhere on the
  timeline they insert at the same drop line (`tl.insert`, then `tl.move` before the
  shot at the line — one `insert` entry), past the end of the film they append. The
  lanes hold still from the press on an outline until its drag ends, since Chromium
  ends a drag whose source is rebuilt out of the DOM. Kept rows and Find rows carry
  their data only in app.js closures, so a kept row is read as the n-th of
  `binOrder(bin.selects)` and a Find row is loaded the way its click loads it and read
  from `findSel`. `test_timeline_lanes.py` gains three tests: a body drag past the next
  shot flips the order in the EDL on disk with the ids travelling (a 2 px move stays a
  click, a whole-selection drag moves together); an available outline dropped between
  the shots becomes the third shot with the keep's range and stops being available; a
  kept row dropped at the top becomes shot 1 and past the end appends, and a Find row's
  payload is what its drop inserts.
- **The timeline's lanes (INTAKE M9, I9.4, part 1).** `app/static/timeline-lanes.js`, built
  on the foundation's API and hung inside `#tl` beside V1 (V1 moves down / the view grows
  through `--tl-above` / `--tl-below`, rules appended to `timeline.css`): **A1**, the music
  bed when `effects_music` is set — the track's name, a bar the length of the film, the
  fades as ramps, a dip under every speech region in the cut (the same padded, merged
  regions the monitor ducks by, the depth the asked dB as a gain ratio) and the monitor's
  own `bedGainAt` curve on top; a click scrolls the music panel in, nothing here edits
  (decision 5). **Markers**, a thin lane above V1: `★` at every hero keep in the cut (clip
  + overlap ≥ 0.5, the kept tab's rule), a tick per ranked event inside a shot (kind-
  coloured, tooltip = what + rank), a legend at the left; and a **bin** lane below with
  the pass's keeps not in the cut as faint `available` outlines after the last shot of
  their clip (else after the end), width to length. **The proposal ghost lane** while the
  diff panel is up: the proposed timeline under V1 by its own film time — unchanged
  shots dim, added ones green, moved ones with an arrow from where they are now, removed
  ones struck out on V1 (matched by id when the plan carries one, else clip + overlap);
  click a ghost → the monitor plays *that* range (a shot that need not be in the cut,
  without playing the cut); `play proposal` / `play cut` at the lane's left; the panel's
  own Accept / Discard stay, and the lane goes when it closes. Lane labels stick to the
  viewport's left edge as the canvas scrolls. `test_timeline_lanes.py`: three playwright
  tests — the A1 lane's bar, dips, fades and curve against the seed's speech; a hero
  star, an event tick and an available outline at the right x (and following the zoom);
  a scripted proposal's ghosts wear the right classes, a click cues the monitor to the
  ghost's clip and time, the toggle hands the monitor back, discard clears, and a
  proposal that drops a shot strikes it out.
- **The timeline's three lane files are loaded by the board** (`timeline-trim.js`,
  `timeline-keys.js`, `timeline-lanes.js`, after the foundation) — served by name at
  `/timeline/{name}`, a 404 until each lane lands, so the tags cost nothing before then.
- **Docs for the promoted timeline (INTAKE M9, I9.1).** `app/README.md` gains a *Timeline*
  row (the ruler, the V1 blocks, scrub, zoom, the one undo / redo stack, the module's API
  as the next lanes' contract); the row that described the cards is now *Shot cards*, and
  the *Monitor* row no longer describes the strip it lost. `docs/INTAKE.md` ticks I9.1 with
  the commits and the counts.
- **`test_timeline_ui.py` — the promoted timeline in a real browser (INTAKE M9, I9.1).**
  Thirteen playwright tests against the synthetic bin: the ruler's labels and end mark
  agree with the seed EDL and `#posTotal`; blocks carry the ids the page was handed and
  the first save persists, and at the fit zoom the two 2 s shots share the viewport
  equally; `+` doubles the px/s and `\` fits; a click selects the block and its card
  (and a card click selects the block), ⇧-click ranges, ⌘-click toggles, the empty ruler
  clears; a click on the ruler at 3 s parks the monitor on shot 2 at 1.0 s, paused, with
  the playhead at 3 s and space resuming from there; the playhead follows playback;
  `tl.setRange` through `begin`/`commit` reaches the disk inside the autosave debounce
  and ⌘Z / ⌘⇧Z walk it back and forth on disk, clamped as nudge clamps; `tl.split`
  makes two shots that survive a save with distinct server ids (the old `tmp-` id still
  resolves); `tl.move` reorders with the ids travelling; `remove` / `insert` keep the
  cards in step; `tl.snapsFor` resolves with the clip's sentences and is cached; a card's
  trim and a timeline move share one stack, in order, with the label in the tooltip.
- **The promoted timeline — the foundation (INTAKE M9, I9.1; lane `agent/timeline`).**
  The proportional strip under the monitor is a real timeline: `app/static/timeline.js`
  (`window.tl`) and `timeline.css`, mounted in `#tl` by app.js once the project loads. A
  ruler with labelled ticks whose step follows the zoom (1/2/5/10/15/30/60 s, minor ticks
  between), a scrollable viewport, a V1 lane of shot blocks placed by film time — poster at
  the in-point (lazy, one frame per settled trim, never one per nudge), the clip's hue, the
  stem, the duration and the shot's strongest line, a mark on any edge that opens
  mid-sentence or cuts a line off — and the playhead as one line across the whole cut at its
  film time. `+`/`-` zoom (⌘/ctrl + wheel around the cursor), `\` fits, ⇧ + wheel pans.
  Blocks carry the server's `data-id`, never an index; the module's selection is a set of
  ids mapped onto app.js's index `sel` at the boundary (click selects and plays from there,
  ⇧-click ranges, ⌘-click toggles, a click on the empty ruler or lane clears — the cards
  follow both ways). Click or drag on the ruler scrubs: the monitor parks on the shot under
  that film time at the right offset, paused, and space resumes from it. One undo / **redo**
  stack for the whole board: `tl.begin(label)` / `tl.commit()` make one entry however many
  mutations sit between them, `pushUndo()` is a thin wrapper over it, ⌘Z / ⌘⇧Z (`u` kept)
  and the header's **Undo** / new **Redo** buttons carry the label in their tooltip
  (`undo: trim`). The edit API the other lanes build on — `setRange` (clamped as nudge
  clamps), `move`, `split`, `remove`, `insert`, `select`, `seek`, `zoomTo`, `fit`,
  `snapsFor` (cached `GET /api/snaps/{clip}`), `on(change|select|playhead|zoom)` — is the
  comment block at the top of `timeline.js`. A shot the board makes before a save carries a
  `tmp-` id, stripped on save and re-keyed from the reply; every mutation goes through
  `touch()`, so autosave and `roughcutFlush` behave exactly as before. The old `.strip` /
  `.blk` rules left the inline style; `test_ui_flow.py` names `#tl .blk` where it named
  `#strip .blk`.
- **The promoted timeline's server side (INTAKE M9, I9.0).** Shots have stable ids: minted
  once at open for a cut written before ids existed, kept through every `PUT /api/project`
  (a duplicate or missing id gets a fresh one), so the bin's `used_in`, undo, reorder and a
  proposal can name a shot instead of a position. `GET /api/snaps/{clip}` gives the magnet
  what a cut can snap to — sentence starts and ends raw and padded the way the floor cuts
  them (−0.25 s / +0.45 s), word starts, and the strongest audio onsets (local maxima above
  the track's mean + one standard deviation, half a second apart). `/timeline/{name}` serves
  the module's files by name. Four tests; three existing tests now ignore the minted ids.
- **The settings drawer on `/open` — workers and the budget cap (INTAKE I5.3's other half,
  the screen; I8.3, `c06f360`).** A gear next to **Index the footage** (or `,` outside a field) opens a
  drawer in the right column, as Fig. 1's "workers 2 · cap $15 · settings ▾"; `Esc` or the
  gear closes it. The **budget cap** is a dollar field with the spend beside it (`$0.03
  spent`) and where the cap comes from in a hint — when `ROUGHCUT_BUDGET_USD` is set the
  field is disabled and says the environment wins. **Workers** are one stepper per stage
  (probe · asr · proxy · sheet · picks), 1–8, clamped in the UI, each with a one-line hint in
  words (asr: *one — the model loads once per run and walks the folder*; sheet: *two sheets
  in flight at once — more spends faster, not better*; proxy: *one — two 4K encodes share the
  same cores*). **Save** is one `PUT /api/settings`; the toast says what changed (`cap $15.00
  → $5.00 · sheet workers 2 → 3`), the budget line in the index controls re-reads, and the
  server's 400 is said on the drawer. **Reset to defaults** fills the form from `defaults` —
  Save still applies it. While the index is running the drawer says changes apply to the
  next run; a line under the button always reads the cap and the workers. `test_open_ui.py`
  20 passed (3 new), and its `budget()` helper now pins `server.budget_cap` — the pause rule
  reads that, not `config.budget_usd`, once a cap is saved.
- **Settings: the budget cap and the index's workers (INTAKE I5.3's other half, server
  side).** `GET /api/settings` reads them and `PUT /api/settings` writes them to
  `--work/settings.json`, validated (a positive cap; 1–8 workers on a stage the journal
  knows). The cap the index pauses on and the status line shows now comes from one
  `budget_cap()`: the environment's `ROUGHCUT_BUDGET_USD` if set, else the saved setting, else
  the default — so a production pin still wins. Workers apply to the journal at the next run.
  `test_settings.py` 3 tests. The drawer on `/open` lands with the settings lane.
- **A filter on the pass, and batch reject through it** (I2.4's other half: *"batch reject
  via filter"* — a round of 40 on Killington carries ten silent scenery claims and six
  unaudited sheet claims an editor wants gone in one gesture, not forty). `/` opens a
  filter line under the HUD: chips for every **kind** in this round with its count (jump ·
  fall · speech · …), four **states** (*undecided* · *claimed only* = a sheet claim nobody
  audited and nobody heard · *has words* · *has telemetry*), the **clips** in the round, and a
  box for a word that matches the reason and every witness's text, case-insensitively.
  Chips of one group OR, groups AND. While a filter is on the HUD says `12 of 40 match`, the
  tape dims the marks outside it, `↵` / `⌫` step only through what matches (the queue is
  never changed — the round stays frozen), and the pass moves to the first matching
  undecided pick when this one falls outside. `⇧X` then asks once in the HUD (`reject 12
  picks? ⇧X again · Esc`) and rejects every undecided matching pick — one `POST
  /api/floor/verdict` per pick in queue order with `why: filtered out: <the filter in
  words>`, one undo entry for the lot, so one `⌘Z` brings them all back and lands on the
  first — then clears the filter and moves on to the next undecided pick. `⇧U` does the
  same with `later`. Without a filter `⇧X` keeps its meaning (the rest of this clip) and
  `⇧U` gains the same for `later`. `Esc` clears the filter; the six-key line is untouched
  and the map gains `/`, `⇧U` and `⇧X`'s second meaning. Four browser tests: a kind narrows
  the count, the marks and the stepping; the box matches a witness line and the terms AND
  together; `⇧X` with a filter asks, writes exactly the matching undecided verdicts with the
  reason, leaves decided and non-matching picks alone, one `⌘Z` restores all, `⇧U` likewise;
  `⇧X` without a filter still rejects the rest of the clip. (`262e301`; INTAKE I2.4 amended
  and I8.2 ticked under M8.)
- **INTAKE M8 · I8.1 ticked** — the pass visibly feeds the cut board (Option B, stage 1):
  commits `39fbfe4` (the `kept` tab, the Project line, `N heroes waiting`) and `2d71140`
  (*Cut from the bin*), with the two calls made where the brief was silent and the next
  human look on Killington. Docs only.
- **Cut from the bin** — a second button under the Ask's note, and beside *Ask for a first
  cut* when the timeline is empty (the panel is hidden there, and it is where someone
  arriving from the pass lands). The same Ask with a fixed note — *build the cut from the
  editor's selects: every hero must appear, use the other keeps where they serve the story,
  and take nothing else unless it is needed to make a keep land* — and the current story,
  then the usual proposal / accept / discard loop; the prompt already carries the bin
  (`revise.py`'s *The editor's selects*), so this is the one click that asks for exactly
  that instead of a sentence somebody has to know to type. Disabled with a hint while the
  bin is empty. The fixed note is the app's words, not the editor's, so unlike a typed
  first-cut note it never becomes the story. `test_ui_flow.py` 45 passed (1 new).
- **The bin on the board — a `kept` tab under "Add a moment"** (Karl, 2026-09-08, after
  using the pass: *"what should I expect going from the pass to the cut board here? Cut
  board looks exactly the same as before."*). The pass wrote keeps into the EDL's `selects`
  and the board showed nothing of it — the bin reached the cut only through the Ask's
  prompt. Option B's Source panel, first stage, on the board as it is: **kept** is the first
  tab and the one the library opens on when the bin has keeps — one row per keep, heroes
  first then by clip and start, a still at its start, `CLIP_04 · 3:44.0 → 3:48.2 · 4.2 s`,
  `★ HERO`, the pass's reason (with its `(frames …)`) and the editor's note in quotes, and
  either *in the cut · shot N* (click selects the shot) or *+ add to cut*, which inserts a
  shot with the keep's range and reason after the selected one, the way the heard/seen rows
  do, and saves straight away so the bin learns the use. *In the cut* is decided on the
  board against the live timeline with `used_in`'s own rule (half the shorter range), so a
  row flips the moment a keep is added and un-flips when its shot is removed. A keep whose
  footage left says so and cannot be added; an empty bin says *nothing kept yet — the pass
  is where you keep things* with a link to `/floor`. The Project panel gets one line from
  the server's summary — `bin · 7 moments · 2 heroes · 1:12 if strung out` — and the Steps
  strip's *first cut* says `N heroes waiting` while a hero the pass kept is not in the cut.
  The bin is re-read when the tab is shown and after every save while it is up.
  `test_ui_flow.py` 44 passed (2 new) · suite 393 passed, 1 skipped.
- **One bin, several cuts — open, switch, save a copy** (Karl, 2026-09-08: *"work on
  multiple projects at the same time … switching easily between projects, and saving copies
  so we can try different things"*). The header of every screen now carries the bin **and the
  cut** it is on; click it (or `O`) for one panel that lists the bin's cuts and the bins the
  board knows. *Save copy* writes the whole project file under a name — timeline, story,
  music and the pass's picks, so nothing ever merges and `assemble.py` renders a copy exactly
  as it renders the original — and moves to it, or stays where you are as a checkpoint
  (*work on the copy* unticked); a row opens a cut; *rename* keeps the file where it is (the
  name lives inside it); *delete* moves it to the bin's `trash/`, never the one on the board.
  Copies live under `projects/<bin>/<slug>.edl.json`; the bin's own file stays where it was.
  The board flushes its autosave before any switch, so a trim made in the last 700 ms lands
  in the cut it was made on. The registry now *reads* the cut each bin was last on — a cut
  named with `--edl` was written to it and never read back, so it was forgotten the moment
  you switched bins — and the picker's rows say which cut a bin is on and how many it has.
  Renders record their cut and the download name carries it. `GET /api/cuts`,
  `POST /api/cuts/{copy,open,rename,delete}`, `edl` on `POST /api/projects/open`, `cut` on
  `/api/status`, `/api/project` and `/api/projects`. The control is `app/static/switcher.js`,
  shared by `/open`, `/floor` and `/`; the open screen's own picker moved into it, and the
  pass's "no buttons in the flow" rule exempts it (it is the way out of the flow, on `O` and
  `Esc`, keys the pass does not use). Seven API tests and two browser tests: a copy saved from the open screen, the way back and its
  deletion; the board's flush before a copy, the reload onto the copy, and the way back.

### Fixed
- **A drop past the end of the cut landed on the magnet button and was lost** once the trim
  lane's button sat top-right of the timeline and a row's mousedown had smooth-scrolled the
  page under the drag. Drops are accepted on the timeline's root now (any child is
  drop-transparent, the film time comes from the pointer), and a drag from a kept or Find row
  freezes the page scroll first. Found by the lanes test in the merged suite.
- **Esc mid-drag cancels the trim again.** The keyboard lane's window-level Esc (clear the
  selection and the marks) ran first and stopped the event, so the trim lane's Esc never saw a
  drag in progress once both were on the board — found by the trim test in the merged suite.
  Esc now yields to a drag (`tl.trim.dragging`) the way it yields to the switcher's picker.
- **The contact-sheet prompt claimed things that were not there** (Karl, 2026-09-08: CLIP_04
  "person in dark clothing appears to be inverted or airborne"; CLIP_07 too). Adjudicated
  against the rebuilt sheets: the "inverted person" is the wearer's glove over the lens, the
  "backflip captured inverted mid-air" a ski binding, the "28-second airborne sequence" two
  people standing on a slope shot from below. The prompt never said the camera is a helmet
  POV, primed for events, accepted hedges as notable and asked for no frame references.
  `visual_pass.py` prompt version 2: the camera and the wearer's gear are named, every moment
  must cite the sampled `frames` that show it with a `confidence`, an event needs another
  person visibly in the air or down in a named frame and can span at most two frames,
  `pov-gear` is a kind, and hedged wording is a guess. `validate()` snaps moment edges to the
  sampled frames (no more `1.0–1.1`) and demotes any notable claim that breaks a rule, with
  the reason in `demoted`. The sidecar records `frames_sampled` and `prompt_version`.
  Seven tests; the live re-read of CLIP_04 is in INTAKE's verification log.

### Added
- **The frames a claim rests on, all the way to the pass.** Events carry `frames`,
  `confidence` and `demoted` from the sheet; `seen` witnesses carry them; the pick's reason
  cites the frames as `m:ss.s` (`picks.stamp`), which the floor turns into links; and
  `/api/picks` carries `looked` per clip — the sample times the look pass read, the sheet
  count and the prompt version — so the pass can show how much of a clip was indexed by
  keyframe. Decision 5 in INTAKE: a clip's start and end stay adjustable after effects.
  A claim the sheet's own rules demoted reaches the pass with no event kind and its reason
  on the line (`— not a claim: hedged wording`), so it lifts nothing and says why.
- **INTAKE M7 — I7.2 and I7.3 ticked** with the commits that prove them (`df0e2d2` the
  looked-at ticks and jumpable times, `453e43c` the restart keys; `test_floor_ui.py` 27
  passed, suite 374 passed, 1 skipped) and the next human look on Killington.
- **`0` restarts the clip on the pass; `⇧0` restarts the band (INTAKE I7.3).** Karl's
  third report: *"add a restart from beginning of clip in the pass /floor."* From anywhere
  on the pass — any overlay open — `0` (and `Home`) plays the whole clip from 0 with no
  stop at the preview end behind you (the same as `.` O); `⇧0` plays the kept range from
  its start and stops at its end, wherever a trim left it, and never moves the band. Both
  in the `?` map; the tape's label mentions `0`; the six-key line is unchanged.
  `test_floor_ui.py` 27 passed (1 new).
- **The pass shows what the look pass read, and every time is a link (INTAKE I7.2).**
  Karl's third report: *"indicate how much of the clip was indexed by keyframe … the
  indexed keyframes should be referenced in the why and timestamps should be jumpable."*
  On the WHOLE CLIP tape a thin tick under the ruler at every frame the look pass sampled
  (`/api/picks` `looked` per clip), the frames a `seen` witness of this pick cites brighter
  and taller, the count in the tape's label (`LOOKED · 80 frames · every 4 s · 3 sheets`,
  or `not looked at yet`) and a line in the margin legend. In WHY, on each witness line
  and in the evidence drawer every `m:ss.s` — and a witness's cited `frames` (`· frames
  2:20 · 2:24`, with its `confidence`) — is a chip: click it and the picture parks on that
  frame (paused, the strip re-centred, the whole clip opened if it lies outside the
  preview), hover it and its tick lights on the tape. The drawer adds one line per pick:
  `this window: 4 of 4 frames looked at`, so a claim with no frame under it reads as
  what it is. A pick without `looked` or `frames` renders as before. `test_floor_ui.py`
  26 passed (1 new).
- **INTAKE ticks I5.3's slider and I5.4** — the tracker names the commits and their
  verification (`621925a` the slider, `aaf43df` the picker; `test_open_ui.py` 15 passed,
  suite 362 passed, 1 skipped).
- **`/open` — the project picker (INTAKE I5.4, the screen).** The bin's name in the header
  is a control: click it (or press `O`) for a panel over `GET /api/projects` — the current
  bin first, then the rest by last opened — each row its name, `N clips`, and the facts as
  small flags (`cut · 17 shots`, `journal`, `new`, `missing`), the folder path in the
  tooltip. A row is `POST /api/projects/open {footage}`; on 200 the page reloads everything
  for the new bin (sheet, themes, index state, the slider) and toasts `opened <name>`
  (`· new project` when the EDL was scaffolded); a 409 (a job still running) or a 400 (no
  video there) is toasted and the panel stays. A field at the bottom takes a folder path
  typed or pasted — there is no browsing dialog in a page, and the hint says so. `Esc`
  closes; a click anywhere else closes. `test_open_ui.py` 15 passed.
- **`/open` — the granularity slider (INTAKE I5.3, the other half).** The "not wired yet"
  hint in **Look — how closely?** is now the design's one control: a four-stop slider
  (4 · 3 · 2 · 1 s, coarse to fine, resting on the project's `interval_s`), each stop said in
  words under it (`a frame every 4 s · sees the run, misses the moment` … `every 1 s · sees
  the landing`), and the price line — and the button's price — re-pricing from
  `/api/status`'s `by_interval` as the thumb moves, with no request per move. **Index the
  footage** (and Resume) send the chosen `interval_s` with `POST /api/index`; the EDL keeps
  it, so after a run the slider shows the project's word. When some clips have been looked
  at the hint says the interval applies to the rest and the ones done stay as they are; when
  every clip has, the slider is off and says so. `test_open_ui.py` 13 passed.
- **The look interval and the project picker, server side (INTAKE I5.3's slider, I5.4).**
  The open screen's one slider now has something to move: `look.interval_s` in the EDL (a
  project setting, so a resumed index looks at the rest of the bin the same way), read by
  `visual_cmd` (`--interval`) and by the look stage's cost; `/api/status`'s `visual` carries
  `interval_s`, the allowed `intervals` (4 · 3 · 2 · 1 s) and `by_interval` — every
  interval's price for the same pending clips, so the slider re-prices without a round trip;
  `POST /api/index {interval_s}` sets it (anything off the slider is 400). And a bin is
  something you point at without a relaunch: `GET /api/projects` lists every bin this board
  has opened (a small registry under `--work`) and every folder of video next to the current
  one, with clip counts, whether it has been cut and whether it has a journal;
  `POST /api/projects/open {footage}` re-points the board through the same `configure()`
  `main()` uses — per-bin paths re-derived, per-clip caches dropped, 409 while any job runs.
  `test_projects.py` 5 tests. The two screens' UI for both lands with the open screen's next
  lane.
- **`/open` — themes as chips, and the story with dictation (INTAKE I5.2, the screen).**
  The right column opens on Fig. 1's question, above the look's price: **what is this film
  about?** — a story field that is the EDL's brief (saved when it settles; the proposal and
  the ask both read it) with a **hold-to-speak** mic — hold the button, or hold `V` in the
  field (a tap of `V` still types the letter; held anywhere outside an input it speaks, like
  the floor) → MediaRecorder → `POST /api/dictate` → the text lands in the field on release;
  a 501 hides the mic and leaves typing, a 413 is said. Under it the price before the button
  (`/api/themes`' `projected_usd` — "~$X · one call over the transcripts of N clips"), and
  **Propose themes** → `POST /api/themes/propose` → a "listening…" line with the job's own
  detail → **chips**: one per theme with its clip count, the quoted line and the why on hover,
  each a toggle that starts kept; the names in a smaller row ("people: Spenny"), also toggles;
  the transcripts' one-sentence read; a **+ add** input for a theme of the editor's own.
  **Keep** is one `PUT /api/themes` of exactly the ticked ones (with the story); **Discard**
  writes nothing, and the section says so. Kept themes are the resting state — chips, with
  **change** (they come back as toggles, no counts: the counts were the proposal's) and
  **propose again ~$X**. A bin the audio pass has not heard says so with the button disabled.
  One hint line: themes lift and tag picks on the pass and order the index; they never score.
  The step strip's `3 themes` is done once the EDL has themes. Browser tests with the backend
  scripted the way `test_themes.py` scripts it: the price before the click, chips with counts,
  untick + add + Keep writes exactly the kept ones to the EDL on disk, Discard writes
  nothing, change re-opens and re-keeps, the mic hides on a 501 with `V` typing again, and a
  hold of `V` with the recogniser stubbed on the server lands its text in the field.
  INTAKE I5.2 ticked (`cce429c`).
- **Compare takes on the floor — the Survey view (INTAKE I2.4).** When the pick is one of
  a cluster the THIS PICK panel says `take n of N · T to compare`. `T` opens the takes side
  by side in time order — a still at the anchor (the poster the open screen uses), the
  range, the kind, the strongest witness's line, the felt numbers, any verdict, `★` on this
  pick; a take decided in an earlier round shows greyed with the reason, like the tape's
  marks. `←` `→` choose, `↵` or a click goes to that pick (the mark-jump path), `P` keeps
  the chosen take and rejects the cluster's other undecided takes — one POST per verdict
  in time order, each reject's reason `other take of <cluster>`, then the pass moves on
  past everything just decided — `X` rejects the chosen take only, `Esc` closes. `⌘Z` after
  a P takes every verdict it wrote back off the EDL in one step: the batch is one undo
  entry. The `?` map has a `T` row; the key line keeps its six things. One browser test,
  the cluster injected through `floor.state` (the synthetic bin cannot form one).
- **Take clusters in the picks (INTAKE I2.4, the data side).** The design's Survey view
  ("three takes of the same jump, side by side — keep the one that landed") needs the picks
  to know they are takes. `picks.takes()` marks them: per clip, picks of one kind that do
  not overlap and sit at most `TAKE_GAP_S` = 90 s apart chain into a cluster — 90 s because
  the hike back up between two hits at a kicker is one to two minutes, a fall and its
  replay are seconds, and two jumps ten minutes apart in a long clip are not takes of each
  other. Speech is the exception: two lines in one clip are not takes of anything unless
  they share a theme tag — the theme says what the thing is. Every pick in a cluster of two
  or more carries `take: {id: "<clip>:<kind>:<n>", n, of, others}`; every other pick
  `take: null`. Computed after the verdicts re-attach; rank and order are untouched, takes
  never score. `/api/picks` carries it through unchanged. Three tests: a cluster forms and
  a gap over the limit splits it (and opens a second), different kinds never cluster and
  overlaps are one pick already, speech needs the shared theme.

### Changed
- **Docs:** INTAKE records I9.7 merged and checked live on Killington, and names M10 as the
  priority after Karl's look.
- **Docs:** I9.7 ticked in INTAKE (M9, extend vs roll by height) with the two commits
  (`60bbb0b` the zones, `f85bca0` the cues), the counts (15 tests; 47 across the four
  timeline files; suite 459 passed with the music-lane test passing alone) and the lane's
  decisions: the ⇧ flip as cancel + begin under the new label, the tooltip's signed length
  change and `the end moves`, the ghost for a right-hand in, the hint's editor-level count
  and its overlay placement, the cursors, film-order `.tl-first` / `.tl-last`.
- **Extend vs roll by height, 2 of 2 — the cues (INTAKE M9, I9.7; lane `agent/tl-edges`).**
  The "seamless" part: what a press would do, shown before the hand commits. Hovering an
  edge column lights the half under the pointer — classes `.lit .top|.bot .left|.right
  .ext|.roll` on the column, drawn by the CSS banner at the end of `timeline.css`: an
  extend in the shot's own hue on its side of the line with `⟶` / `⟵` pointing into the
  neighbour, a roll as a neutral bar across the cut with `⇄`; `col-resize` for both.
  Behind an extend a ghost strip (`.tl-edge-ghost`, in the canvas over the lane) outlines
  the first shot that would move, 0.5 s later — or, when nothing follows the shot, a strip
  past its out for the film's end — so "the rest moves" is visible before the drag; it
  goes during the drag, when the real blocks move. A one-line hint (`.tl-hint`, appended
  inside `#tl` and overlaid in the gap the monitor card leaves under it — its padding and
  margin — so it costs no layout: a first cut reserved a line, and
  `test_playing_from_the_inspector_brings_the_monitor_into_view` at 900×380 showed the
  monitor-plus-timeline fit is a budget those 17 px broke) reads `top edge ·
  extend or shorten this shot, the rest moves · bottom edge · roll the cut into the next ·
  ⇧ flips` — at once for the first five cut hovers, counted in `localStorage`
  `roughcut.tl.edgeHintSeen` (the editor's count, not the page load's), then only after a
  600 ms dwell. ⇧ while hovering flips the cue live, and during a drag the column follows
  the drag's mode, so a flip shows on the column as well as in the tooltip. The `?` map's
  Timeline section gains three rows through the keys lane's table (`tlKeys.KEYS`, then its
  `renderMap()`): `▲ edge`, `▼ edge`, `⇧ drag`. `tl.trim` gains `HINT_KEY` and a `hover`
  getter. `test_timeline_trim.py`: a `hover` helper; one test added — the lit classes per
  half and side, the ghost's left and width against shot 2's block, `.end` on the right
  quadrant, no ghost on a roll, ⇧ flipping the cue, the hint's text and counter, nothing
  lit off the column, the dwell after five; the flip test also reads the column's cue.
  15 tests; the four timeline files 47 passed.
- **Extend vs roll by height, 1 of 2 — the zones (INTAKE M9, I9.7; lane `agent/tl-edges`).**
  Karl (2026-09-18): *"extend a clip while offsetting the next instead of cutting into the
  next … maybe if I do it at the top of the video clip it extends, bottom cuts in?"* Until
  now which gesture a drag got hung on a few pixels: the block's 8 px edge handle rippled,
  the 10 px zone over the cut rolled. In `timeline-trim.js` both fold into one edge column
  per interior cut — `EDGE_PX` 16, centred on the cut line, the lane's full height, four
  quadrant hit targets (`.tl-edge > .q.t|b.l|r`). Top half: extend or shorten, the rest
  moves — left of the line the left shot's `out`, right of it the right shot's `in`, a
  ripple trim through `tl.setRange` so the shot changes length and everything after it
  slides, one entry labelled `trim`; tooltip `out 3.30s · +0.30s · the rest moves` (`the
  end moves` when nothing follows; the signed number is the change in the shot's length).
  Bottom half: today's roll, both `setRange`s in one entry, the film's length held;
  tooltip `roll · CLIP_A out 3.30s · CLIP_B in 0.70s`. ⇧ flips the mode before or during
  the drag without letting go: the entry is cancelled (`tl.cancel`) and begun again under
  the other label, the travel so far re-applied, so the one undo entry says what the drag
  ended as. The film's first in and last out keep the block's own full-height handle
  (`.tl-first` / `.tl-last`, set by the module in film order; the interior handles are
  hidden by CSS). The magnet in both modes, Esc, the 3 px click rule, the `,`/`.` nudges,
  the monitor's park and pointer capture are unchanged; `tl.trim` gains `EDGE_PX`.
  `test_timeline_trim.py`: the helpers `out_handle` / `in_handle` resolve an interior edge
  to the column's top quadrant (the outer edges to the block's handle), `roll_zone` is the
  column's bottom half, the fixture waits for `.tl-h.in` and `.tl-edge .q.t.l`; five tests
  added — the top half extends and pushes (shot 2's range untouched, its film start slides,
  one ⌘Z restores), the bottom half rolls, ⇧ before and during a drag with the labels
  following, the last out over the whole height with `the end moves`, the tooltips (`+`
  and `−`). 14 tests; the four timeline files 46 passed.
- **Docs:** INTAKE's verification log records the inspector's live check on Killington.
- **Session 14 close-out (docs).** INTAKE M9 ticked through I9.5 with the merge hashes,
  "Where we are" names Karl's look as the next step and the three items deferred from the
  lanes' reports; the lanes table and verification log carry the inspector merge and the
  452-test run; HANDOFF's opener describes the cutting room.
- **Docs:** I9.5 ticked in INTAKE (M9, the inspector) with the two commits (`ac404db`,
  `f6ed711`), the counts (5 new tests; 91 across the five timeline + flow files; suite 452
  with the two music tests passing alone) and the lane's decisions: `m:ss.s` header times
  with the exact seconds in the tooltip, the scoped ask's send button reading `Ask`, no
  invented provenance, and the one convenience the foundation could add (`tl.reveal`).
- **The inspector's tests and docs (INTAKE M9, I9.5; lane `agent/inspector`).**
  `test_ui_flow.py`: the fixture waits for `#tl .blk`; every test that read `.seg` cards
  reads the timeline's blocks and `#inspector` instead (the poster test, the settle test,
  the trim / undo tests, the boundary-warning test now checks the inspector's header and
  the *Fix cut points* button, remove and reload, the proposal tests, the scoped ask — now
  *the inspector asks about the selected shot* — Find's insert, the monitor's block-click
  and space / enter tests, playing from the inspector with the monitor scrolled off, the
  versions list, what the visual pass saw, the reattached ask, the kept tab, the empty bin,
  cut from the bin and the switcher); a `select_shot` helper selects by id without playing.
  Five new tests: selecting a block fills the header and the why (and ↑ follows), editing
  the why saves to the EDL and repaints the block's tooltip, the ±0.25 s buttons are one
  undo entry each (⇧ a second; three ⌘Z walk back in order), the kept tab's *in the cut ·
  shot N* selects the block and the inspector from a cleared selection, and the empty state
  after Esc plus the multi-selection with remove for all. `test_timeline_ui.py` (a block
  click and the index both way, the ruler click, the split / move / remove counts, the
  shared undo stack), `test_timeline_keys.py` (the razor count, ⌘A+X undo, Esc, keys while
  typing in the why) and `test_timeline_lanes.py` (the drag's first block) where a selector
  named a card. README: the *Shot cards* row is *Inspector*; the monitor row and the
  visual-pass paragraph say where the moments show now.
- **The inspector replaces the shot cards (INTAKE M9, I9.5; lane `agent/inspector`).**
  `#inspector`, under the timeline in the DOM place the card list had: for the anchor shot,
  `SHOT n of N · CLIP_04 · 3:44.0 → 3:48.2 · 4.2 s` (the exact clip seconds in the tooltip),
  the boundary warning and any unusable stretch, where it starts in the film; the still at
  the in-point (the same poster URL, catching up to a trimmed in-point on the 450 ms settle,
  never a frame per nudge); the transcript lines and what the visual pass saw inside the
  cut; the why as the same contenteditable, saved on blur and the block's tooltip repainted;
  `↳ polished from a–b · why` and `act · …` only when the segment carries them — nothing is
  invented, and the save keeps only clip / in / out / act / why / id, so a proposal's polish
  shows until the next reload; the ±0.25 s trims (⇧ for 1 s) through `tl.begin('trim')` /
  `tl.setRange` / `tl.commit()` so each press is one undo entry; *▶ play* this shot only;
  *✎ ask about this shot* — the scoped ask unchanged, its send button now reads `Ask`;
  *remove* through `tl.remove`. A multi-selection shows `N shots selected · d s` and remove
  for all; no selection says *select a shot on the timeline — or press ↑ / ↓* with the cut's
  totals; no cut shows the board's empty state as before. It is one element built once per
  shot and filled in place on the timeline's `select` and `change` and from `paint()`, so a
  scrub or a shot advance never rebuilds it or steals the focus from a why being typed — and
  a why still being typed when the inspector moves on is committed first, because Chrome does
  not blur an element that leaves the DOM. `render()` no longer builds cards; the kept tab's
  *in the cut · shot N* selects by id through `tl.select` (a cleared selection left the index
  where it was and `syncSel` had nothing to adopt); `scrollSel` brings the shot's block into
  view; `waitForProxies` re-points the inspector's and the blocks' posters; the card's own
  drag-to-reorder is gone (the timeline moves shots). `index.html`: the `.seg` styles become
  the inspector's, `#timeline` is `#inspector`. Decided by the lane: header times in the
  ruler's `m:ss.s` per the brief, with two-decimal clip seconds in the tooltip for trimming.
- **Docs:** INTAKE's lanes table and verification log carry the three timeline lanes merged,
  the two merge-only fixes, and the live check of the timeline on Killington.
- **Docs:** INTAKE M9's four lane lines are the ticked ones only, after the lanes merge
  brought the placeholders back.
- **Docs:** INTAKE M9's I9.2 and I9.3 lines are the ticked ones only — the keep-both merge had
  left the unticked placeholders beside them.
- **Docs:** INTAKE's lanes table and verification log carry M9's foundation merge and the
  three timeline lanes in flight; the board is live on the foundation.
- **Session 13 close-out (docs).** INTAKE M8 ticked in full (I8.1 the bin on the board, I8.2
  the filter, I8.3 settings, I8.4 the cuts live check), "Where we are" says nothing is
  deferred and names what waits on Karl and the design's stage two; the lanes table and the
  verification log carry the four lanes and the 404-test run; HANDOFF's opener updated.
- **Docs:** INTAKE's verification log records the 401-test run and the filter's live check on
  Killington.
- **Settings defaults say the code's default.** `GET /api/settings`'s `defaults.budget_usd` echoed
  the environment's pin when one was set, so Reset could never show the real default; it is
  15.0 now. Tests that force a pause pin `server.budget_cap`, not `config.budget_usd`, since a
  saved cap sits between the two.
- **Docs:** INTAKE I8.4 records the live check of the cuts control on Killington (Karl had
  already saved a copy of his own, `killington-future`).
- **Session 11 close-out (docs).** INTAKE M7 ticked in full (I7.1 with the live CLIP_04
  re-read, I7.2/I7.3 from lane `agent/floor4`, I7.4 Decision 5), "Where we are" names what
  waits on Karl (his look, the themes, the ~$2 re-look of the other 11 clips), the lanes
  table and verification log carry floor4 and the 382-test run; HANDOFF's opener updated.
- **Session 10 close-out (docs).** INTAKE's "Where we are" says every milestone item is
  ticked and names the two deferred halves; the lanes table lists open2, board, open3 and
  takes as merged; the verification log records the 372-test run and the live checks of
  `/`, `/open` and `/floor` on Killington. HANDOFF's opener names Karl's third look at the
  pass and the waiting themes proposal as the next step.
- **INTAKE I2.4 ticked** — take clusters (`7719b72`) and the Survey view (`be992ea`), the
  cluster rule and the 90 s spelled out, the filter half of the item left with the filter,
  the `selects.py` one-liner that would put a reject's reason in the EDL, the next look.
- **INTAKE I2.8 ticked** — Karl's second look at the pass, his three sentences quoted, the
  three commits that answer them (`b58e2c8`, `1a2815c`, `911b8b7`) and what to look at next.
- **A verdict moves on — always (INTAKE I2.8, 1c).** Karl: *"when I pick, reject, or later
  a clip, it should move on to the next one. Right now, I'm not sure how you move on"*. Until
  now a verdict advanced only with Caps Lock on ("CAPS · AUTO-ADVANCE" in the HUD) and ↵
  otherwise. Auto-advance is now the only behaviour: after the stamp lands, P / X / U / 1
  go to the next pick, and to the closing card after the last; the Caps Lock switch, its
  HUD pill and its map row are gone. ↵ still skips for now (the next pick without a
  verdict), ⌫ goes back, and a decided pick revisited by ⌫ or a mark click shows its stamp
  and can be re-decided, which moves on again. Tests that pressed ↵ after a verdict or read
  the CAPS text rewritten; one added: X, U, P in a row with nothing else pressed land on
  the closing card with the right counts.
- **Where the subclip sits in the whole clip is painted, bridged and said (INTAKE I2.8,
  1b).** Karl: *"it is unclear where the subclip is within the whole clip timeline on the
  pass"*. The tape now carries the kept range as a solid green band — the closer strip's
  green, the same edges, following the handles while a drag is in progress — inside the
  hollow bracket that still marks this pick's full window; a 16 px bridge between the two
  strips fans the lens's edges on the tape out to the closer strip's full width and runs
  the playhead's line from its place on the tape to its place on the strip, so the eye
  reads "these 16 s of that 5 min"; and the THIS PICK panel says it in words: `2:20 → 2:27
  of 5:18 · 44 % in`. The legend names the band. One browser test: the band's left and
  width follow `keepRange()` through a drag of each handle, the bridge follows the lens and
  the playhead, the words read right.
- **The audio note works in a browser (INTAKE I2.8, 1a).** Karl: *"the audio note doesn't
  work"* — and the server was fine (a real recording came back in 4 s). The browser flow
  in `floor.js` was the failure: Chrome's first-time permission prompt let V come up before
  the stream arrived, and the stream was then thrown away in silence; a refused microphone
  toasted `NotAllowedError` and dropped into the typed editor as if that were the feature.
  Now the permission is learned at boot (`navigator.permissions`) and the hint under the
  note says what V will do — *V will ask for the microphone the first time*, or *microphone
  blocked for this site — allow it in the address bar, or N to type* — with the V key
  coloured by the state; a stream that lands after the key is up is kept and announced
  (*microphone ready — hold V and speak*), the editor left closed; one stream stays open
  for the page (tracks stop on hide/unload), so the second note never asks again and
  records at once; a hold under 0.4 s is dropped with *held too briefly — hold V while you
  speak*; the failures are told apart — a 501 is *dictation is not installed on the server*
  and the only case that opens the typed editor, anything else shows the server's reason
  and leaves the note as it was; `#dictState` reads listening… → transcribing… → landed.
  Three browser tests: the blocked microphone (named, editor closed, never asked twice),
  the late stream (kept, next hold records on it, too-brief hold dropped), and the 501
  path rewritten to the new words plus the boot hint after a reload.

### Added
- **Themes proposed from the transcripts (INTAKE I5.2, the data side).** Listen first, then
  propose: `POST /api/themes/propose` is one judge-role call over the audio pass's transcripts
  — no sheets, no moments — that returns three to eight themes as phrases the editor would
  recognise ("hitting rocks", not "skiing"), each with the clips that carry it, a quoted line
  and a sentence on why it might be the spine, plus the names people are called by (they seed
  dictation). Validated like a plan: a theme citing a clip not in the bin fails the job. Nothing
  is the EDL's word until the editor keeps it: `PUT /api/themes` writes `themes` and `names`
  (deduplicated, capped), `GET /api/themes` reads them with the price of proposing more. The
  picks tag and lift what matches, and the journal's priority counts `theme_hits`, from that
  moment on. `roughcut/themes.py`; five tests. The chips UI lands on the open screen.
- **Telemetry is wired on R11's terms (INTAKE I6.2).** The index's `telemetry` stage runs
  `research/tools/telemetry.py` on clips that carry a `gpmd` stream and skips — honestly, with
  the reason — on clips that don't (a phone clip loses nothing). From the summary, two things
  and only two reach the floor as `felt` witnesses, numbers only and never a state: loose
  freefall runs of at least 0.25 s under 0.5 g (`0.30 s freefall at 0.03 g`) and impact peaks
  above 5 g (`6.7 g`) — not the 3 g the design guessed, which fires six times a minute on
  this footage. A freefall run may add the small cross-kind corroboration bonus to a pick
  that already has a heard or seen witness; an impact moves nothing. The journal's
  `telemetry_peaks` priority fact counts exactly those, and is refreshed once the sensor has
  spoken on a bin whose sidecars already existed. Nothing here can make a pick alone.
- **`/open` — the index controls: one button, priced, unattended (INTAKE I5.3).** The
  open screen's right column, as Fig. 1: the price before the button (`/api/status`'s
  `visual.projected_usd` — "~$X for the N clips not yet looked at", with the sheet and
  window counts under it), the budget line (spent of cap), the order toggle (most
  promising first / capture order), and **Index the footage** → `POST /api/index` —
  disabled while a run is going, "Index what isn't done" once the bin has a journal, a
  409 said in a toast and never doubled. When the cap has paused the priced stages the
  screen says so with the journal's reason and **Resume priced stages** (→ `resume_priced`).
  While a run is going the page polls `GET /api/index` every 2 s and shows the journal's
  own numbers: the overall bar, released n of N, cost, ETA (or "once a stage has been
  timed" — never a guess), the journal's path and its last log lines, the running job's
  one-liner, and — above the grids, as Fig. 2 — the per-clip table: clip · the seven
  stages as chips (done / running / failed ✕ n / skipped / queued) · priority · state
  (released · indexing · queued · retrying with the error · parked with the reason ·
  missing). **Open the pass →** enables on the first released clip and says how many;
  the cut board is one link away. The granularity slider is deferred and the page says
  why — the visual pass's sample interval is not plumbed through the index yet; nothing
  is faked. Browser tests run a real journal walk with the index's tools stubbed
  (`test_index._stub_tools`): the cap at $0 pauses the priced stages and the notice
  appears, resume releases all three clips and enables the link, the 409 is said.
  INTAKE M5 ticked for I5.1 and I5.3 with the slider deferred and the settings drawer
  not built.
- **`/open` — the folder as a contact sheet, before anything is spent (INTAKE I5.1).**
  `app/static/open.html` + `open.js`, on `GET /api/clips`: the bin's name and its line
  (clips · total length · sessions · telemetry x/N), one grid per session in capture order
  (the 4-hour-gap rule), a card per clip with its first frame (a placeholder until the proxy
  exists — never a broken image), its length, and the free flags: `listened` / `not yet`
  (the audio pass has heard it; the sidecar's candidates are not on the wire, so "no words"
  waits), `telemetry` / `no telemetry` (the `gpmd` stream, a fact shown), `looked`,
  `released`. Once the bin has a journal, its word sits on the picture — released ·
  indexing · queued · retrying · parked (with the reason on hover) · missing — derived from
  the stage states the way `journal.progress()` derives a row's state, so the card and the
  index table cannot disagree. A legend says what every flag is and is not ("a silent clip
  is not a dim clip"). The six-step strip from the design heads the page: listen is done
  when every clip is heard, the pass opens on what is released. No model calls. Tested in a
  real browser (`test_open_ui.py`, playwright) on the synthetic bin.
- **`GET /api/clips` — the folder as a contact sheet would show it (INTAKE I5.1's data).**
  Every clip with the free facts: length, when it was shot and which session that makes it
  (the 4-hour-gap rule), what exists for it on disk (proxy, audio sidecar, coarse look, close
  look), whether the file carries a GoPro `gpmd` telemetry stream (one ffprobe, cached — a fact
  shown, never assumed, per Karl's rule), whether the floor may show it, and the journal's word
  on it once the bin has been indexed (priority, stage states, parked). No model calls. The
  open screen (M5) is built on this.
- **The index runs unattended: `POST /api/index` drives the journal (INTAKE I3.2, I3.3).**
  One thread walks `journal.next()` and runs each (clip, stage) with the tools the two old
  buttons already drove — probe (ffprobe, all clips first so the audio pass can run as one
  batch), asr (`audio_analyze.py` over every clip still waiting), proxy (`build_proxy`), look
  (`visual_pass.py --only`), close (the motion scan, then the close look, or `skipped` when
  the scan finds no window worth paying for), picks (`rebuild_events`) — reporting back with
  `start` / `finish` / `fail`, cost recorded per priced stage. Telemetry is `skipped` as
  "not integrated yet" (I6.2), never silently done. Before every priced stage the budget cap
  is checked and the priced stages pause while the free ones finish. A second POST after a
  crash is a resume: the journal is reconciled against the sidecars on disk (files are truth;
  a stage found running with no output re-queues with its attempts kept; nothing paid for is
  bought twice) and the walk continues. Footage dropped into the folder later is the same
  path — new clips join the queue by priority and are released whole. `GET /api/index`
  returns the journal's own progress (per-clip rows for the Fig. 2 table, counts, cost,
  released list, paused state). `released_clips()` — what the floor may show — now comes
  from the journal on a journaled bin (decision 3 exactly: every applicable stage done) and
  keeps the sidecar-plus-proxy rule for bins indexed before journals existed. Five tests in
  `test_index.py` with the tools stubbed: a full unattended run releasing every clip, resume
  after a simulated crash without re-buying CLIP_A's sheets, footage added after a run, the
  budget cap pausing priced stages only, and a scan with nothing to look at.
- **I2.7 ticked in docs/INTAKE.md** with its three commits (`a822b6b`, `4036b6a`, `46ee3d4`),
  the verification counts, and what Karl's second look should check on Killington.
- **The strips explain themselves, and less is on screen (docs/INTAKE.md I2.7, moves 3 and
  4).** "It's not clear how it fits into the bigger picture, or what the markers are above in
  the whole clip." The tape now carries a time ruler (a tick and a time every second on a
  short clip, every 30 s on a five-minute one — no more than a dozen labels), this pick as a
  bright bracket over its window, the other picks as marks with a **legend** in the left
  margin (this · picked · later · undecided, swatches with words) and a tooltip with the
  rank, the state and the reason, and a **lens**: a translucent frame showing exactly the
  seconds the zoomed strip holds, moving with the playhead — so the two tiers visibly relate,
  and the legend says so ("the lens: what CLOSER shows"). The strips are labelled for what
  they are: `WHOLE CLIP · name · length` with "click to seek · a mark is a pick — click one to
  jump to it", and `CLOSER · ±8 s around the playhead · the green band is the clip — drag its
  edges to trim, its middle to slide`. The key line collapses to the six things that matter —
  `P X U · space · [ ] { } · V · ?` — and the full map, now with a line for the mouse, stays
  behind `?`. The caption line and the transcript are as they were. Browser tests: the ruler,
  lens, legend and labels are there; the lens tracks a seek on either strip; the key line has
  ten keys and none of the words the map still has.
- **Playing never changes the selection (docs/INTAKE.md I2.7, move 2).** The green band used
  to follow the furthest point watched — "watching the green bar increase as I play … it's
  fairly nonintuitive", and it looked like the tool deciding. Now the band is the pick's
  preview when the pick loads, snapped outward to the sentence as before (decision 1's intent —
  never the machine's whole window blind — still holds, because the preview is the default),
  and it moves only by hand: a drag, `[ ] { } ← →`, or `}` to the next line. Holding space
  just keeps watching. Karl's open question — should hold-space extend the band visibly, with
  a dashed follow that commits on release? — is answered **never** by default, the simplest
  rule; flipping it is one place, `tick()`, where the watched extent used to be recorded. The
  "Keeping" margin no longer says "what you've watched": it shows the band and where it came
  from (the preview as offered · the preview snapped out to a line · trimmed by hand). Two
  browser tests that encoded the old rule are rewritten to the new one: hold space past the
  preview, press P, and the range on disk is the preview, snapped.
- **Trim by drag on the floor (docs/INTAKE.md I2.7, move 1).** Karl's first report on the
  pass: trimming by keys and by watching was "fairly nonintuitive". The green band on the
  zoomed strip is now the thing you handle: drag either handle to trim (a magnet within 10 px
  takes the sentence start − 0.25, the sentence end + 0.45 or a word start — the same places
  the keys go — the tick lights while it holds, the time reads out under the handle, and the
  picture parks on the edge frame); drag the band's middle to slide the whole range, length
  kept, clamped to the clip; click either strip to seek (playing stays playing, paused stays
  parked), drag to scrub; click a mark on the tape to jump to that pick. Marks in this round's
  queue jump, decided or not, so a verdict can be revisited; a pick decided in an earlier
  round is drawn but says so in its tooltip and stays put, because the queue is frozen and
  only undo puts a pick back. While a pointer is down the zoomed strip stops following the
  playhead, otherwise every park would slide it out from under the finger. Handles are 14 px
  hit areas; this pick's own mark takes no pointer, so a mark it overlaps is still reachable.
  The keys `[ ] { } ← →` are unchanged. Browser tests: a handle drag lands on the EDL
  snapped, a band drag slides it, a mark jumps, a click seeks.
- **The server's side of M1 (docs/INTAKE.md I1.1–I1.4 wired).** An Ask passes the EDL's
  `selects` through, so a first cut is asked *from the bin* — heroes must appear, keeps are
  bounds — and a revision reads it as context; a keep made on the floor records its clip's
  length (`clip_duration`) so `relink` can find its footage after a rename; `GET /api/selects`
  relinks on every read and writes back only when something changed; and a select flagged
  `missing` round-trips through `PUT /api/selects` instead of failing validation, so the bin
  editor's save never drops a keep whose file wandered. I1.2 (`used_in` on every save) was
  already `sync_timeline` in the foundations.
- **The index is a journal, not a job (INTAKE I3.1).** `roughcut/journal.py` is the pure model
  behind decision 3 — unattended, resumable, priority-ordered, released per clip — and
  decision 4 — clips added at any time. Per clip, seven stages (probe, telemetry, asr, proxy,
  look, close, picks) each queued / running / done / failed / skipped / parked with attempts,
  cost and timings, persisted as one JSON per bin written atomically. `reconcile(files)` bends
  the plan to the disk (files are truth: a sidecar that exists is done, a stage found running
  with no output is a crash and re-queues with its attempts kept, cost only ever recorded on
  `finish` so nothing is double-counted); `next()` walks clips in priority order and stages in
  pipeline order — so the top clip releases whole before the second starts — honouring
  dependencies (picks wait for the close look too), per-pool worker limits (proxies and sheets
  separately), backoffs and the priced pause; three failures park the clip with its reason
  while the rest continues; `released(clip)` is every applicable stage done. The priority
  score is documented in the module: candidates 0.40, theme hits 0.30, words 0.15, duration
  0.10, telemetry 0.05 — the telemetry term can never outweigh one theme hit or two speech
  candidates, per Karl's rule. `progress()` reports counts, cost, and an ETA from rolling-mean
  measured durations only. No server wiring yet (I3.2); tracked in `docs/INTAKE.md` M3.
- **Dictation is real: hold V, speak, and the note comes back as text.** `roughcut/dictate.py`
  now answers `available()` and `transcribe(path, names=)` for real, so `POST /api/dictate`
  stops saying 501. The recogniser is `research/tools/dictate.py` (PEP 723): ffmpeg turns the
  recording — MediaRecorder's webm/opus or a wav — into 16 kHz mono, faster-whisper `small`
  (the audio pass's family, chosen for latency: a note is a few words the editor can fix with
  N) transcribes it on the GPU without a system CUDA install, and the brief's `names` seed
  `initial_prompt` so "Spenny" survives. The server never imports faster-whisper: the module
  shells to the tool with `uv run`, and every failure — undecodable bytes, a missing model, a
  timeout, output that is not JSON — is one `NotAvailable` for one note, never the app; a
  recording over 30 s is refused before the model loads (`TooLong`, a `NotAvailable`). Measured
  on the RTX 5080: 1.3 s for the tool warm, 4.7 s end to end through the module. A recording
  over 30 s answers 413 from the endpoint; the `live` pytest marker is registered in conftest.
- **Tracker: M1 on `agent/bin` is built.** docs/INTAKE.md ticks I1.4 with its commit; I1.1,
  I1.3 and I1.4 are done on the lane, I1.2 and the three server call-sites (`selects=` in
  `_ask_job`, `relink` in `GET /api/selects`, `clip_duration=` in the verdict endpoint) are
  the lead's, named on their lines.
- **Relink: a select survives its clip being renamed or moved (docs/INTAKE.md I1.4).** A
  select now records its clip's length at creation (`clip_duration`, via
  `new_select(..., clip_duration=)`, `apply_verdict(..., clip_duration=)` and
  `validate_selects`, which knows it). `selects.relink(edl, clips)` — `clips` being
  `{clip: {"duration"}}` for the folder as it is now — flags a select whose clip is gone
  `missing: True` rather than dropping it (the keep is the editor's work; the file wandered),
  and when exactly one clip on disk is within 0.05 s of the recorded length re-points the
  select at it and clears the flag, id and `used_in` intact. No match or several stays
  missing: guessing between two same-length clips would put the wrong footage in the film.
  A select whose clip is present is untouched, and one that never learned its length learns
  it. Pure; the server is not wired yet (`GET /api/selects` is the place, the lead's file).
- **Hand-added shots become keeps (docs/INTAKE.md I1.3).** The bin has its round trip from the
  timeline: `selects.sync_timeline` (already called on every save) now adopts any shot no keep
  covers — no select sharing half of the shorter range — as a keep with `source: "hand"`, the
  shot's own range and `why`, no note, clearing a reject or later on those seconds. A shot
  placed by hand in the cutting room is in the bin like any other and the next ask knows it;
  idempotent, and a shot trimmed inside a keep stays that keep's use rather than a second keep.
- **The ask reads the bin (docs/INTAKE.md M1).** `revise.originate` and `revise.propose` take
  `selects=` (the EDL's bin) and the prompt gains a **"## The editor's selects"** section ahead
  of the ranked events and the inventory — one line per keep with its reason, the editor's
  note quoted, and `HERO` on the ones that must appear. For a first cut the section is the
  contract: heroes must appear (the model may trim inside a hero's range, never drop one
  silently), keeps are bounds to trim inside, the rest of the inventory is connective tissue
  only; `validate_plan(..., heroes=)` rejects a plan in which no single shot covers half of a
  hero unless `notes` names that hero's clip, so a dropped hero is always explained and the
  bounded re-ask tells the model which one. In a revision the same section is context, not a
  constraint, and the prompt says so. The server does not pass `selects` through yet (one
  keyword in `_ask_job`, the lead's file); the tests wire it the same way to prove the 502.
- **R11 — telemetry measured (research/R11-telemetry.md, research/tools/telemetry.py).** Every
  Killington clip carries a GPMF stream (HERO9: ACCL/GYRO 198.5 Hz, GPS5 18 Hz, GRAV/CORI/IORI);
  a dependency-free parser recovers it in 7 s for the bin. The design's thresholds were wrong
  both ways — freefall at 0.3 g never fires, impacts at 3 g fire six times a minute — and the
  largest accelerations in the bin are hands on the camera. A loosened freefall run (< 0.5 g
  for ≥ 0.25 s) is clean (10 of 10 checked by eye were real motion, six visible airs) and may
  carry a small corroboration weight; impact peaks stay numbers only. The camera's orientation
  agrees with the nine labelled mounts and shows the body never rolled past 30° in R10's
  "camera-inverted" windows — and a 6.7 g peak inside one of them turned out to be a fall R10
  had missed. Per-clip summaries in `benchmarks/labels/B2-telemetry.json`. INTAKE I6.1 closed
  on commit `f806e13`.
- **The pass: `/floor` is a screen now.** `app/static/floor.html` + `floor.js` (framework-free,
  like the board) build the cull exactly as the design's §4 draws it: the pick's proxy plays
  large from the moment (`preload="metadata"`, `#t=` fragment), context in the left margin,
  witnesses as seals in the right — three states, colour *and* the word (CLAIMED / AUDITED /
  CONTRADICTED; FELT shows numbers only) — the reason on a caption line with any conflict in
  red, the whole clip as a tape with every pick marked (a telemetry trace only when a `felt`
  witness exists), and a zoomed strip ±8 s around the playhead with words where they fit and
  sentence bars where they don't. **Keep = what you watched**: the contiguous extent from the
  preview start to the furthest frame reached, snapped outward to sentence ends with
  `boundaries.py`'s pads (start −0.25, end +0.45) and shown as a green band with snap ticks;
  `[ ] { }` move an edge to the previous/next sentence (`}` extends to the reaction), `← →`
  step a frame at the active edge and park the picture on it. Every action is a key and there
  are no buttons in the flow: P / X / U / 1 (a stamp on the picture that fades), `⇧X` rejects
  the rest of the clip, `⌘Z` undoes the verdict with its trim and note (re-instating whatever
  verdict was there before), J-K-L with K = pause, hold `space` to keep watching, `V` hold to
  speak (MediaRecorder → `POST /api/dictate`, the clip ducked; a 501 says "not built yet — N
  to type" once and opens the typed note), `N` note, `E` evidence drawer, `.` more, `?` map,
  Caps Lock = auto-advance, `↵` next. Rounds of 40 from released clips with the queue frozen
  for the round; the position is saved on every verdict and resumed on load; the closing card
  shows counts only (never "enough"), what arrived since the round started, and offers `↵`
  play the bin (every select in order in the same picture), next round, by clip, revisit
  later, and Assemble with its price on `A`. `app/tests/test_floor_ui.py` drives all of it in
  real Chromium (with a fake microphone for `V`) and asserts the kept ranges on the EDL on disk.
  (Commit `bed7a9c`; the tracker's M2 boxes point at it.)
- **The floor's foundations: picks, the bin in the EDL, and every floor endpoint.** The intake
  design (docs/design/cutting-room-floor.html, tracked in docs/INTAKE.md) needs three things
  before any screen exists. `roughcut/picks.py` derives **picks** — windows with witnesses —
  from the R8 speech candidates, the ranked events file (each event's confirmation becoming a
  witness *state*: claimed / audited / contradicted, a contradiction kept as `conflict` rather
  than resolved into the reason), optional telemetry peaks (numbers only, never able to promote
  a pick alone) and the editor's themes; overlapping witnesses merge into one moment with a
  corroboration bonus; a pick under 8 s plays whole, a longer one previews its anchor ±3 s.
  `roughcut/selects.py` puts the human's selection in the EDL as ranges on clip time —
  `selects` (keeps, merging overlaps, hero, note, `used_in` recomputed from the timeline) and
  `floor` (rejects, laters, the pass position) — so a finer index never orphans a verdict
  (`picks.attach_verdicts` re-attaches by overlap, keeps winning over rejects). The server gains
  `GET /api/picks`, `POST /api/floor/verdict`, `POST /api/floor/note`, `PUT /api/floor/position`,
  `GET/PUT /api/selects`, `POST /api/dictate` (501 until M4) and the `/floor` route with a
  placeholder page. Nothing here spends a model call or touches `segments`.

### Documentation
- **Karl's first report on the pass, recorded as the next item (I2.7).** Trimming must be
  click-and-drag rather than keys; the green band must not grow while he watches; the tape's
  markers and the zoom strip's relation to it must explain themselves; less on screen. The
  transcript stays. The tracker carries the four-move plan, the DoD, and one open question
  (should hold-space ever extend the band). HANDOFF's opener names it as next.
- **Session 8 closed out in the tracker.** All five intake lanes are merged on `main`;
  `docs/INTAKE.md` records the live verification of `/floor` on Killington (I2.6: 92 picks,
  a verdict written and undone, Karl's EDL left as found), the lane table, the verification
  log, and six discoveries — among them that the bin's events file holds no confirmed event,
  that GPMF carries a wind meter, and that R11 corrected an R10 label. The floor's dictation
  fallback test now simulates dictation being unavailable, since it is real on this tree.
- **M4 checked off in the tracker.** `docs/INTAKE.md` I4.1 and I4.2 carry their commit and the
  measured numbers; the one thing a tone cannot prove — a spoken sentence coming back — is
  left to the live verification on Killington (I2.6).
- **The intake workstream has a tracker.** `docs/INTAKE.md` carries Karl's four decisions, the
  milestones with checkboxes, the lanes in flight and where to pick up after an interruption;
  HANDOFF points at it and roadmap item 8 (telemetry) moved into it. Both design documents are
  now in `docs/design/`.
- **Roadmap item 8: telemetry (accel/gyro/GPS) as an optional, weighted, never-trusted-alone
  witness.** Karl's backlog note, recorded with his three rules: it is not always present, so the
  pipeline must run identically without it; when present the AI weights it alongside the visual
  and audio passes; and *be very careful not to over-index — it could be noisy or bad*, so it
  gets no weight until a study (R11) measures it against the adjudicated events, and until then
  it corroborates and never promotes. GPMF is in the GoPro files today and dropped unread by
  `assemble.py -dn`.

### Added
- **Ask about one shot: a scoped revision on every card, seconds instead of
  minutes.** Karl asked for "a feature that lets me request specific changes to
  individual clips". Every shot card carries **✎ ask**, which opens a note box for
  that shot alone; the call it makes is `revise.propose_shot` — the film's story,
  the full edit list with the focused shot marked, and the *complete detail of only
  that shot's clip* (transcript, visual moments, unusable stretches), instead of the
  whole bin inventory. That scope is the point: a full Ask is ~40k tokens and 3-4
  minutes, the right price for restructuring a film and the wrong one for "let the
  line finish" — live on Killington, a note on the pocket-pizza shot came back in
  **10.7s** at $0.15 projected, holding 1.6s of reaction after "pizza" and arguing
  it from the transcript. The model may only return segments from the focused
  shot's clip (the validator enforces it; cross-clip swaps are what the full Ask
  and the finder are for), its replacement is boundary-polished like any proposal,
  and the server splices it into the untouched timeline **before** writing the ask
  record — so the proposal the human reads, the accept, and the on-disk recovery
  path all see a whole cut, never a one-shot fragment that would eat the film on
  accept. Same loop as everything else: an accept/discard proposal, undoable, the
  EDL untouched until the human says so. The estimate ahead of it is computed
  rather than asked for (a model call to size a 30-60s job would cost more than it
  informs — the analyse/render rule), and a stale shot index is a 400 the UI shows,
  not a thread that dies estimating.
- **Find a moment: natural-language search over the whole bin, with the answer as
  playable video.** Karl asked for a way to "use natural language to search for a
  specific clip through the whole thing, and then present options along with their
  full video". Two layers, two prices. `roughcut/find.py`'s **lexical layer** is free
  and instant: the query word-matched (with just enough stemming for rock/rocks and
  fall/falling) against every transcript utterance and every visual-pass moment,
  scored, and merged into windows — a window whose halves answer *different* words of
  the query ("hit" said here, "rocks" seen here) outranks either alone. Live on
  Killington, "hit the rocks" puts CLIP_08 270.9s ("I hit some rocks at the end
  there") first at score 1.0. **"Ask the model"** is one judge-role call over the same
  inventory the Ask reads, offered with a projected price on the button (the visual
  pass's rule) for the misses word-matching cannot close: live, "me falling into the
  river" — words the transcripts never say — returned the fall itself (CLIP_01
  90-108, agreeing with the visual pass's 104-108 window), the narrated confession at
  187-199.5, and the aftermath, in 25s for $0.19. Matches are validated as strictly
  as a plan (unknown clip or out-of-range seconds fail loudly; an *empty* list is a
  valid answer — "not found" is information). Every result row plays the **full
  clip's proxy** in the panel's player, seeked to the match by media fragment, so the
  moment is judged in context and the whole clip is scrubbable; **+ add to cut**
  inserts it after the selected shot, undoable like any edit. `POST /api/find`
  answers lexically in-request; the model call is a job (one at a time, 409) with
  milestones on the top bar and its matches recoverable via `/api/job/{id}` after a
  reload. Nothing here writes the EDL.

### Documentation
- **HANDOFF opens on the state Karl actually left, not the state session 6 closed.** He kept
  editing on 2026-08-24 after the docs were written: ask `a038b42c` accepted (the rocks-runner
  revision of his six hand-adds), a 22-shot 4K delivery render (`cut_d25d19d5`), then one more
  hand deletion — so the board holds a 21-shot, 175.1s cut. Session 7 verified the board live,
  rendered that state as a preview (`cut_c912b763`, +0.22s drift) so *· this cut* has a render
  again, and rewrote HANDOFF's top block to match.

### Added
- **One render at a time, and a button that says so.** Karl asked whether *"hitting the button
  multiple times can break the system state of the render"*. It cannot: every job has its own
  id, its own parts directory and its own output file, so two renders write two files and
  neither touches the other — nothing was ever at risk of corruption. What was at risk was the
  clock. His delivery render took ~17 minutes with the machine otherwise idle, and two 4K
  encodes competing for the same cores make both crawl; nothing stopped a second one from
  starting. `POST /api/render` now answers **409** naming the job that is running and what it
  is doing, the same rule `/api/analyze` and `/api/visual` have always had, and the Render
  control reads *Rendering…* and is disabled while one is going. The control is driven from
  `/api/jobs` rather than from the click that started it, so it is right after a reload and
  right when the render was started in another tab — and with the top strip now carrying the
  render's progress, a disabled button next to a moving bar says the whole thing.
- **One progress model, and a bar up top that every long operation drives.** Karl:
  *"Several progress bars need help… consider a progress tracking bar up top for anything which
  may take time to complete — re-use across app."* The board had four long operations and four
  unrelated notions of progress — the audio pass counted sidecars into `done`/`total`, the visual
  pass counted two different things into two keys, a render counted parts on disk, and an **Ask
  counted nothing at all** and showed an elapsed clock next to the word "thinking" — so no single
  bar could exist. `roughcut/progress.py` is the shape they now share: `kind, label, state,
  started, elapsed_s, pct, detail, eta_s` and an ordered list of `milestones` with weights and
  `done_at`. `pct` comes from milestones where an operation has them and from **counted work on
  disk** where it does not (`_run_counted` is untouched as the source for analyse, visual and
  render); it never dips (a filesystem count racing a rename can) and never reads 100% before the
  job is over. `Job` subclasses `dict`, so every existing mutation and status field in
  `app/server.py` survives verbatim and this is an addition rather than a rewrite. Two new
  endpoints: `GET /api/jobs` — the heartbeat the top strip polls, payload-free — and
  `GET /api/job/{id}` for the whole record. The strip sits under the step strip, holds **more
  than one operation at a time** (a render and an Ask overlap routinely), **re-attaches after a
  reload** because the registry is server-side, and disappears when everything is idle.
- **Every AI task now estimates itself before it starts, then reports milestones as it goes.**
  Karl: *"the first step the AI must complete is an estimate of how long it will take to apply
  the changes. This will (when estimate is complete) start a progress bar against the AI
  workflow… The initial assessment must include milestones, which the agent will complete, and
  then follow back up with the app on."* An Ask opens with one cheap `ROLE_ANALYSIS` call
  (`roughcut/estimate.py`) returning `{eta_s, milestones}`, validated the way `validate_plan`
  validates a plan — bounded count, positive finite weights, an ETA inside sane limits, one
  bounded re-ask — and **the module raises nothing**: a garbage estimate, a timeout, a budget
  refusal or a logged-out backend all fall back to a measured hard-coded estimate and the work
  runs anyway. The keys are the app's and the labels and weights are the model's, because a
  milestone nobody can *observe* completing is a bar that stops moving. Each completed milestone
  **recalibrates the ETA** from elapsed-vs-expected, trusting measurement more the further in it
  is, so the bar reports "about 2 min left" and which checkpoint it is on rather than repeating
  a guess made before anything ran.
- **Streaming, so a four-minute `claude -p` call can report where it is.** A CLI call returns
  once, at the end, which is why the board could only ever show a clock. `ClaudeCliBackend` takes
  an optional `on_partial(kind, text_so_far)` and, when given one, runs
  `--output-format stream-json --verbose --include-partial-messages` — verified on this machine
  (CLI 2.1.2) before anything was built on it: the flags are accepted together, `stream_event`
  lines carry `thinking_delta` and `text_delta`, and the closing `result` object is **identical**
  to the one `--output-format json` returns, so the ledger, `projected_usd` and the token
  accounting are the same on both paths. The non-streaming path is untouched, which is what keeps
  the scripted test backends working. An Ask's plan is a JSON list of segments, so counting the
  `"clip"` keys written so far is real progress against a real denominator: the bar says
  **"12 of ~18 shots decided"**, not a spinner.
- **The changelog rule is checked now, not just written down.** Karl, after a session of agent
  work: *"Ensure all changes are getting a changelog."* An audit of the session's 20 non-merge
  commits found 18 carrying an entry and two without - both of them docs commits rewriting
  `docs/HANDOFF.md` at the end of a long stretch, which this file's own header ("every commit
  that changes behavior **or** documentation") and CLAUDE.md both require. The two are written
  up under Changed below, and `.githooks/pre-commit` now refuses any commit that touches
  something other than the changelog and the hooks without also touching the changelog. Enable
  it per clone with `git config core.hooksPath .githooks`; if it fires, the fix is an entry,
  never `--no-verify`. A rule nobody checks is a rule that decays.
- **[R10](research/R10-events-priority.md) — sorting what was seen, and what a closer look is
  actually worth.** Answering Karl's *"you missed some cool jumps"* with numbers, and finding
  that only half the diagnosis holds. A 1s read of 23 motion-picked windows did surface four
  events at timestamps the 4s pass never mentioned — and then eleven claimed events, checked by
  eye against the frames, produced **zero that were what either pass said they were**. The
  mechanism is one thing: on a helmet or chest mount the horizon sits at forty-five degrees, so
  *the camera* is inverted and both densities read it as *"rider inverted mid-air"*. The three
  claims the previous proposal cut on — CLIP_04's backflip and crash, CLIP_11's inverted trick —
  are among them. Two cost facts fall out: a fine sheet costs the same as a coarse one ($0.073
  vs $0.077) because the prompt and not the image is what is paid for, so **density is cheap and
  coverage is expensive**; and a close look is a good *auditor* and a poor *detector* — every
  `junk` call it made was right. The ranking is built on that asymmetry. R8/R9/R10 are now
  listed in research/README.md, which had only ever carried the planned studies.
- **Events, ranked — and the rank believes a second look over a first one.** Karl, on the
  Killington revision: *"You missed some cool jumps — this is likely due to limited keyframe
  analysis and the lack of a workflow / algorithm that applies sort / priority following a
  granular keyframe analysis on the first pass."* Both halves were right and they were
  different faults. The sort is now real: `roughcut/events.py` scores every moment as **kind ×
  notable × corroboration × confirmation × usable** and writes the bin's ranked list to one
  `events.json` beside the visual sidecars — one file per bin, not an `events` key inside each
  sidecar, because the question ("the twelve biggest things in this footage") is bin-wide,
  because the sidecars are paid observations and this is derived, and because it can be deleted
  and rebuilt for nothing. It reaches the Ask as a **`## Events, ranked` section before the clip
  inventory**, the board's *seen* tab (sorted by that score, junk never offered), and
  `/api/project`. The load-bearing term is **confirmation**, not kind: [R10](research/R10-events-priority.md)
  adjudicated eleven claimed events against the frames and *none* survived as described — a 45°
  helmet-cam horizon reads as "rider inverted mid-air" to both sampling densities — so a claim
  two independent looks agree on scores ×1.5, one a closer look contradicts scores ×0.35, and
  `junk` is a floor of zero rather than a low score. The prompt now states that measured trap
  where it already states the transcript-density one, and every ranked line carries its evidence
  word so an unaudited `jump` reads as the guess it is.
- **The visual pass has a second stage, priced like the first.** `POST /api/visual` runs the
  coarse pass, then the free motion scan, then a close look at the busiest three windows per
  clip (`fine: false` turns it off, `fine_windows` moves the number), then rebuilds the rank —
  for free. `/api/status` splits `coarse_calls` and `fine_calls` and totals the price, because a
  button that silently grew 40% dearer because a default changed is not offering a price. A
  failed second stage keeps the sheets already paid for and says so; the two stages tick
  separate counters after ticking both into `done` made a finished job report 0/3.
- **A free motion track, and a visual pass that can look closely at one moment.** The visual
  pass samples every 4s; a jump lasts one or two seconds, so whether an air is seen at all is a
  coin toss on phase, and sampling a 44-minute bin at 1s would cost four times as much for
  frames that are almost all snow. `roughcut/events.py` adds the cheap half of the answer:
  `scdet`'s frame difference over the 720p proxy at 10 Hz — one ffmpeg pass, ~6s per 5-minute
  clip, no model call — combined with the 10 Hz onset track every audio sidecar has carried
  since R8, as robust z-scores so a POV run and a lift cabin are each judged against their own
  noise floor. Its peaks are **candidate windows**: `research/tools/event_scan.py` writes them
  out (motion tracks cached beside the visual sidecars), and `visual_pass.py --windows` reads
  one sheet per window at any interval, writing `<stem>.fine.json` beside the coarse
  `<stem>.visual.json` and never over it — two paid observations of the same clip, cached per
  window so a second stage can add three more without re-buying the five already on disk.
  `contact_sheet.py` grew `--start/--end` to make that possible, and keeps labelling cells in
  **clip** seconds through the input seek, which is the whole value of a fine read.
- **`assemble.py --profile preview|delivery`, and the board can ask for either.** `preview` is
  exactly today's fixed 1920×1080 @ 24000/1001, veryfast/crf 20 — unchanged, still what quick
  versions use. `delivery` conforms every clip to the bin's own majority frame rate (measured,
  not guessed: Killington splits 9 clips at 30000/1001 against 3 at 60000/1001) and up to
  3840×2160, capped so a bin shot smaller than 4K — or the test suite's synthetic 320×180 clips —
  is never upscaled, at preset slow / crf 18 / 256k audio. The numbers behind those choices are
  in the research commit just before this one. The Render button gets a `preview` /
  `delivery (4K)` select next to it (default preview, so nothing already using the board changes
  behaviour); `/api/render` accepts `profile`, rejects anything else with a 400, and records it
  plus the rendered resolution in the render's metadata; the versions list shows e.g. "· 4K" for
  a delivery render. Renders made before this feature carry neither key and still list correctly,
  as preview. Five new tests: the pure sizing rule (never upscale, cap at 4K), the
  default-is-preview path, delivery's no-upscale guarantee against the synthetic clips (verified
  against the file on disk, not just the metadata), an unknown-profile 400, and pre-existing
  metadata without the new keys. Verified live against the real Killington footage too, mixing a
  60fps and two 30fps clips through one delivery render: resolved to 3840×2160 @ 30000/1001 as
  measured, not 24000/1001 or an upscaled canvas. A full delivery render of the 178s cut is
  estimated at ~17 minutes from one shot's measured rate and was not attempted — over the
  ~10-minute bar for actually running one.
- **The visual pass runs from the board, and what it saw is on the board.** Karl, on the first
  Killington cut: *"the analysis missed some critical moments that would have required video
  analysis — like me falling into a river."* The pass that finds those has existed since the
  prototype (`visual_pass.py`) but only in a terminal, and nothing on screen ever showed its
  output — 3 of Killington's 12 clips had been looked at and the board could not say which.
  The Project panel now offers **Look at N clips · ~$X**, priced from the clip durations before
  it runs ($0.09 per 120s sheet, measured on CLIP_01 and provisional like everything about that
  pass), never started on the app's own initiative, with the same sidecar-counted progress as
  the audio pass. Once a clip has been seen: every shot card lists what is visible inside it
  (`FALL 104.0 Person appears to be down…`), a shot overlapping a stretch the pass called
  unusable says so in red, and *Add a moment* grows a **seen** tab beside **heard** — notable
  moments only, events first, one click to insert. The empty timeline suggests looking before
  asking for a first cut, because a first cut that knows about the fall is the point. Visual
  sidecars are **per bin** under `--work` now, the third time that lesson has applied (proxies,
  renders, these); Killington's three existing ones were moved across.
- **Music, in the board.** The bed built last session existed only as `assemble.py --music`.
  A Music panel lists `assets/music/` (served like the proxies, each track with its measured
  loudness — the number the first inaudible bed was missing), and the chosen track with its
  duck depth and fades is saved into the EDL as `effects_music` the moment it changes, so
  Render picks it up with nothing else to do; versions that carry a bed are marked ♪. And the
  **monitor plays the bed under the cut** before anything is rendered: the render's balance
  (−24 LUFS under a −16 film, transposed onto each proxy's own loudness), ducked by the asked
  depth across the same padded-and-merged speech regions `effects.speech_regions` keys the
  render on, with its 80 ms / 900 ms envelope. Karl's note on the first bed — *"fading in and
  out… people are talking and the music is distracting"* — cost a render and a listen per
  attempt to hear; it is a slider and a press of space now.
- **A render can carry a label, and the versions list shows it.** `POST /api/render` already
  accepted a `label`; nothing displayed it. Used this session to render a *proposal* without
  accepting it — the Killington revision, so it could be watched against the current cut before
  the decision — which is exactly the version that must not read as if it had been the cut.
- **A monitor: the cut plays from the proxies, without a render.** The loop for judging an
  edit was trim → Render → wait two minutes → watch → repeat, because the board could play one
  shot at a time and nothing else. The main column now opens on a player that runs the whole
  timeline shot after shot from the 720p proxies — two `<video>` elements take turns, one
  playing the current shot while the other is already parked on the next in-point, so a cut
  costs a swap rather than a file open. Under it, a strip: every shot as a block, width
  proportional to its length, coloured by clip, with the playhead moving through the live one;
  click a block to play from there. `space` plays or pauses the cut from the selected shot,
  `enter` plays only that shot, and clicking a card's poster jumps the monitor to it. The
  strip, the cards and the monitor share one selection, so trimming while watching acts on the
  shot you are looking at. Not gapless, and a rough cut does not need to be — but the rhythm of
  an edit now reads before anything is rendered. Three browser tests drive it: the hand-over
  between shots, a jump from the strip, and the two keys.
- **Effects design, and the first one: a music bed.** [docs/EFFECTS.md](docs/EFFECTS.md) sets out
  how a plain-language note reaches the pixels. Four rules carry it: **the model never writes
  ffmpeg** (a closed vocabulary of effect kinds with validated parameters — an invented filter
  string either crashes the render or silently does the wrong thing, and it is executable text
  arriving by way of a transcript); effects are **resolution-independent** (fractions of frame
  width, so a 720p proxy preview matches the 5.3K master); **assets are a local library** with a
  manifest the model reads like the clip inventory; and frame accuracy comes from **narrowing** —
  the visual pass locates a 4-second window, the R8 **onset track pins the impact to 100ms with
  no model call**, a frame strip confirms it, the human nudges. That onset track has existed since
  R8 and had never been read: R9 found it useless for *classifying* events, and nobody noticed it
  is exactly right for *timing* one you already know about.

  Built: `assemble.py --music` and `effects_music` in the EDL, mixed after the concat with the
  **video stream copied**, so a bed costs nothing in picture quality. Ducked by default via
  `sidechaincompress` keyed on the film's own audio — R8 established that the words carry these
  films, and a bed at a flat level buries them.
- **A prototype visual pass — and it finds what the transcripts cannot.** Karl, on the first
  Killington cut: *"the analysis missed some critical moments that would have required video
  analysis (and some sound) — like me falling into a river."* The evidence was sharper than the
  complaint: the cut opened on him **talking about** falling in, three minutes after it happened,
  from the same clip that contains the fall. `research/tools/visual_pass.py` samples a clip onto
  contact sheets and asks a model what happens in each, writing `<stem>.visual.json` beside the
  audio sidecars. On CLIP_01 it reported *"person appears to be down or lying in snow"* at
  **104–108s**, notable, plus scattered gear at 108–112s — and the frames confirm it: helmet and
  goggles at the bottom of frame, body buried. It said *snow* rather than *water* because the
  stream is snow-covered and no water is visible, which is the correct call from the evidence.
  Sheet density, thumbnail size and model tier are RQ-1/RQ-7 and unmeasured, so every number here
  is provisional. Cost at 4s sampling: $0.18 for a 204s clip.

### Changed
- **A proposal survives a reload until it is answered.** The open screen shows
  `GET /api/themes`'s `last` proposal as chips when the page loads with no themes kept, and
  hides the mic outright when the server's `dictation` flag is false. Keep (`PUT /api/themes`
  with `themes`) and Discard (new `POST /api/themes/discard`) are the two answers to a
  proposal; either makes the server forget it, so a reload after Discard asks afresh instead
  of offering the discarded chips again. One browser test covers the round trip.
  The proposal is also written to `--work/themes/<bin>.proposal.json` when it lands, so a
  server restart does not lose what was paid for (the first live one was lost exactly so).
- **Docs:** INTAKE's I3.2 note and HANDOFF's session-6 paragraph no longer say the old Analyse /
  Look buttons are still on the board — lane `agent/board` retired them (merge `6c4d249`).
- **`GET /api/themes` carries `last` and `dictation`** — the last finished proposal (id +
  proposal), so the open screen reloaded after the call answered shows the chips instead of
  pricing again, and whether the recogniser is installed, so the mic can hide before the
  first hold rather than on a 501. Asked for by the open lane.
- **The cut board's two old buttons are retired in favour of `/open`** (INTAKE decision 3:
  the index is one unattended, resumable run started from the open screen). **Analyse
  audio** and **Look at the footage** — with their state lines, progress bars, click
  handlers and the polling of `/api/analyze/{job}` and `/api/visual/{job}` — are gone from
  `/`; two ways to spend money on one bin was one too many. In their place, one line in the
  Project panel reads the journal's own word from `GET /api/index` (`indexed · 12 of 12
  released`, `indexing · 3 of 12 released · $0.03`, `index paused · 9 clips wait`, `not
  indexed yet`), polled every 3 s while a run is going and repainted only when the words
  change, with **open the footage →** to `/open`. The empty-timeline hints point there
  too, and the header gains a small `open · pass · board` nav in the open screen's pill
  style. The "looked at" and "events ranked" counts stay. `POST /api/analyze` and
  `POST /api/visual` are untouched — tests and tools use them. One browser test.
- **Space is play / pause on the pass** (Karl, 2026-09-08: "space needs to be play / pause").
  It was hold-to-watch-on. Now a press pauses a playing picture and plays a paused one; when
  playback has stopped on its own at the green band's end, the next press watches on past it
  (the whole clip opens, as `.` O does) instead of rewinding. The band still never moves on its
  own — watching is not keeping; extending is `}` or a drag of the out handle. Hints, the key
  line and the map say so; the hold-space test is rewritten as the toggle test.
- **Session 9 close-out (docs).** INTAKE's "Where we are" reflects the merged open and
  floor2 lanes (`6ba1ec0`, `bc090e0`), the lanes table marks every lane merged with its
  worktree removed, the verification log records the 348-test run and the live checks of
  `/open` and `/floor` on Killington; HANDOFF's opener points at Karl's second look at the
  pass as the next step.
- **`docs/HANDOFF.md` records the rest of session 6.** Karl accepted the revision built on the
  corrected transcripts — 17 shots, 3:01, with the ski-patrol beat that no transcript in this
  project contained until that morning — rendered it at 4K, and reported three more problems
  (the progress bars, the blank preview, the stalling renders). START HERE now says what he
  accepted, what each of those turned out to be, and what is left to judge; two findings join the
  must-not-relitigate list: a heavy render reads as a broken one (the 4K file is structurally
  perfect and simply costs 92.8 s of CPU per 181 s of video), and a UI measurement without the
  machine's condition written next to it cannot be re-checked.
- **`docs/HANDOFF.md` was rewritten three times as session 6 ran**, and these are the entries
  those commits owed. First (`11e8fec`): START HERE replaced with sessions 4-6 in a paragraph,
  where the Killington bin actually stands on disk, and a roadmap reordered around the monitor,
  the visual pass and music. Then (`42a763d`): the bin fully seen, the two proposals that
  followed, and the finding that looking is what makes a note actionable rather than a
  replacement for one. Last (`e4ef0cb`): Karl's six notes on the proposal render, each with what
  was found and what changed, five additions to the must-not-relitigate list (Whisper's VAD, the
  early word-end timestamps, the camera-inverted false flips, the 4K sources behind a 1080p24
  preview, and the cost shape of a contact sheet), and the roadmap reordered around R10's
  follow-ups.

### Fixed
- **A reject keeps its reason.** `selects.apply_verdict` stored only the note on a reject or
  later, so the Survey's batch reject (`other take of <cluster>`) was on the wire and never in
  the EDL. The verdict record now carries `why`. Found by the takes lane; one assertion added.
- **The themes price was the wrong side of honest.** The first live proposal on Killington
  (12 clips, 323 capped lines) was quoted at $0.09 and billed $0.20 — 42.8k tokens in, 4.4k
  out: the judge role's harness overhead is nearer 36k than the finder's 18k, and the answer
  (six themes with quoted lines, twelve names, a sentence) is four times the first guess.
  `themes.projected_usd` is recalibrated on that run; its test pins the Killington shape to
  $0.16–0.26.
- **The render-progress test waits for what it asserts instead of racing it.**
  `test_render_reports_which_shot_it_is_on` polled a real three-shot render every 50 ms and
  then asserted it had *caught* the counting. The shots are 1.5–2 s of 6-second clips, so a
  busy box could finish the whole render between two polls: it failed once under load and then
  passed five times in a row, which is the worst kind of test — one that reports on the
  machine's mood. The render is paced now. A stand-in for `assemble.py` writes its next part
  only once the board has been *seen* reporting the last one through `/api/render/{job}`, so
  every state the test asserts is one it waited for. The assertions got stronger rather than
  weaker in the process: all three counts and all three "cutting shot N of 3" lines, where it
  used to accept "at least one count above zero". It still asserts the two things it existed
  for — that a render says which shot it is on, and that a finished one does not read as 0 of
  3 — and every other render in the suite still runs the real encoder end to end. Verified 8/8
  on an idle box (~3.2 s each, down from a real render) and 4/4 with twelve spinners loading it.
- **A render is only finished when there is something to watch.** Merging the progress work
  with the preview work exposed a seam neither branch could see alone: the 720p review copy —
  the file the A/B players actually stream — was derived *after* the render job reported done,
  so the top bar sat at 100% and said "done" while the thing being waited for was still being
  made. Measured on this box for a 181s cut, that silence is **39.7 s** off the 1080p preview
  master and **95.4 s** off the 4K delivery one. The copy is the render's third phase now:
  `cutting · joining · review`, weighted in seconds-of-work per second of cut (a fifth of a
  preview render, a twelfth of a delivery one), the job stays `running` through it with its own
  detail line, and it reaches 100% only when the copy exists **or has definitively failed** —
  a failure ends the phase too, and says in the job's own words that the players will fall
  back to the master. The Renders panel's bar now reads the job's `pct` rather than the shot
  count, which hit 100% the moment the last part landed. Preview's safety net is untouched:
  the players still fall back to the master, still say "making a review copy to play", and
  renders made before the copies existed are still built lazily off the versions list — which
  is also the one path that can now race a render for the same copy, so a render waits on
  whoever already owns the build instead of starting a second encode.
- No behaviour change: dropped an import `app/server.py` no longer needs (the Ask's estimate
  goes through `revise.estimate_ask`), and said in `AnthropicApiBackend`'s docstring that it
  ignores `on_partial` on purpose — the progress bar degrades on that backend, the call does not.
  `ruff check roughcut app` is back to the two pre-existing findings it has on `main`.
- **The visual pass's two progress writers stopped overwriting each other.** Found on the first
  live sheet: the useful line — *"reading CLIP_05 — 1 sheet"*, lifted from the tool's own
  chatter — appeared for a second and was then replaced by *"0 of 1 clips seen"* by the
  two-second ticker, over and over, because both wrote the same field. They compose now: the
  count answers "how far", the line answers "on what", and a bar watching a twenty-minute pass
  needs both. `app/README.md` gains the section describing the shared model and the measured
  numbers behind its defaults.
- **The ETA stopped inflating while the model was thinking, and the estimate's weights are
  measured now.** Both found by watching three real Killington Asks rather than the suite. (1)
  The stream driver calls `complete("read")` on *every* thinking delta — a hundred times in a
  ninety-second phase — and each call re-derived "how long is the whole job" from a fraction
  that had not moved and an elapsed that had: the ETA **climbed from 85s to 129s while the call
  was twenty seconds from finishing**. `Job.complete` now recalibrates only when something
  actually changed. (2) Past its own estimate with nothing new completed, `eta_s` is `null` —
  "nobody knows" — rather than 0, which reads as finished. (3) The first live estimate split the
  call 8/42/33/8/8 across its phases when the truth, timed, is **5/72/19/3/1** — the reasoning
  is nearly three quarters of an Ask — so the bar sat at 31% when the model was seconds from
  writing. The measured split is given to the estimator as evidence; on the next two runs it
  adopted it and the checkpoints landed within four points of the timings. Render throughput is
  measured the same way: a 17-shot, 181s preview took 160.8s (0.89x), not the 1.4x guessed.
- **A render stopped claiming to be finished while it was still joining.** The bar was
  `done/total` over the shots, so it hit 100% the moment the last part landed on disk and then
  sat there for the whole join — minutes of it on a 4K delivery render, and exactly the
  "got like no response — and just see rendering…" complaint. Cutting and joining are two
  weighted milestones now (0.75 / 0.25), and no snapshot in state `running` may report 100%.
- **What the board's playback numbers above depend on, measured properly.** The 6.8–9.5 s first
  frame that started this was real and reproduced three times, but it was measured on a box that
  had just finished a 4K delivery render — and a claim that only holds under conditions nobody
  wrote down is a claim the next session cannot check. Re-run back to back on the same warm,
  idle machine, main and this branch paint at **0.74–0.79 s** and **0.89–0.91 s**: with 1.4 GB
  of proxies in the page cache and eight free cores, seventeen card streams cost nothing, and
  the branch is a hair *slower* because a poster is one more thing to fetch before the monitor
  is asked for anything. Put the box back under a render-shaped load (one `libx264 -preset slow`
  encode of the 4K master, ~8 cores) and the difference returns: **5.6 / 5.7 / 3.1 s** before
  against **3.2 / 2.6 / 3.6 s** after, with the same 19-vs-15 requests and 93 KB of media every
  single run against 0.2–2.5 MB. So the honest statement is not "the board was ten times
  slower"; it is that **the cards' streams cost nothing when the machine is idle and seconds
  when it is not**, and an editor whose first frame degrades with whatever else the box is doing
  is the defect. The buffered-range fix is the one that holds unconditionally: playing a shot at
  188.2 s, main buffers `0–15 s` in every condition measured and this branch never does.
- **The version players streamed the master render, and there was no way to download one.**
  Karl, watching the finished 4K delivery render on the board: *"it looks like it already
  crashed.. or at least has an issue with the render - jumping all around the place, looks
  bad"*, and *"they seem to get stuck in this loading forever place and also only have played
  for like 3s before video buffers / pauses."* The file is not broken — 3840x2160, 181.11 s,
  5427 frames whose presentation intervals are a clean 1/29.97 throughout with zero anomalies.
  It is **987 MB at 43.6 Mbps**, and the A/B players were pointed straight at it. Measured in
  a player for 15 s: the master pulls **157 MB** (≈84 Mbps demanded) against **5.3 MB** for a
  720p copy, and with both slots loaded plus the monitor playing, a page on masters moves
  **199 MB** in 15 s against **23 MB** on review copies, painting its first frame in 243 ms
  against 187 ms. So a finished render now gets a **720p review copy** — the same argument as
  the source proxies, one directory over, at `--work/reviews/<bin>/`, built one at a time in
  the background after the render reports done, never blocking it. The players play that; the
  master is untouched. The 4K master's copy is 35 MB, built in 70.7 s; the five 1080p ones are
  ~33 MB and ~30 s each. While a copy is building the row says *review copy building…* and the
  player says so under itself rather than being handed a 987 MB file; if the copy cannot be
  made the row falls back to the master and warns that it will stutter, because nothing in the
  versions list may become unplayable.
- **A finished cut could not be got off the board.** Karl: *"Make it clear how to download the
  renders."* The only render URL the board exposed is served inline, so clicking it played the
  file in a tab, and `cut_110ecb13.mp4` says nothing on a desktop full of downloads. Every
  version row now carries a **↓ download** control on `/media/download/render/<name>`, served
  `content-disposition: attachment` as `killington-neutral-17shots-3m01-4K.mp4` — bin, shots,
  duration, quality — and the row states the size and the resolution next to it (`942 MB ·
  3840x2160`), because 987 MB is worth knowing about before the click. Renders made before the
  profile field existed had no dimensions at all and listed as blanks; they are probed off the
  file now and cached by mtime, so an old preview reads as `1920x1080` and can never read as 4K.
- **A stalled version player looked exactly like a working one.** The monitor learned to say
  what it was doing on its own screen; the A/B players had not, so an encoding review copy, a
  buffering stream and a media error were the same black rectangle — which is what *"stuck in
  this loading forever place"* was made of. Each slot has a line under it now that names which
  of those is happening, with the standing warning restored after a transient one rather than
  cleared by it.
- **The monitor downloaded the top of the file and then waited to seek.** Its two `<video>`
  elements were `preload="auto"` and `arm()` set their `src` before anything said where the
  shot starts, so Chrome did the only thing it could and fetched from byte 0. Measured on the
  live board: playing a shot that begins at **188.2 s**, the buffered range was **0–15 s** —
  it was pulling a part of the file nobody was going to watch, and the seek to the in-point
  queued behind it. The elements are `preload="metadata"` now and `arm()` puts the in-point in
  the URL as a `#t=` media fragment, so the first request after the header lands on the shot;
  `dataset.src` deliberately keeps the bare proxy URL, because that is how the rest of the
  monitor asks which clip a buffer is holding. Buffered after arming is the in-point's range
  (`184.3–203.8` for the 188.2 s shot) plus at most a couple of seconds of header, against
  15 s of head before. The two-element hand-over is untouched: `arm` / `playFrom` / `advance`
  and their browser tests still pass, plus a new one that pins both elements to
  `preload="metadata"` and asserts the fragment reaches the URL. Together with the shot cards
  no longer streaming, the first non-black frame after `playFrom(0)` is **1.03 s / 0.82 s /
  0.96 s** against **6.8 s / 9.5 s / 7.9 s** before — press play, see a frame. 146 API + 32
  browser tests pass.
- **The monitor played the sound and showed a black screen, because sixteen shot cards were
  each streaming an 85 MB proxy.** Karl, on the Killington board: *"I can hear the videos when
  I click play, but the preview window still shows up blank."* Neither the range serving nor
  the layout was at fault — `curl` gets exact 206s with correct `content-range` in 3 ms to
  first byte, five concurrent open-ended requests each deliver all 85 MB in under a second,
  `.screen` measures 924x520 with the live `<video>` filling it, and the console is clean. It
  was **starvation in the browser**: every shot card was an autonomous
  `<video preload="metadata">` pointed at a proxy, so a 17-shot cut opened 17 streams plus two
  render previews against Chrome's ~6 connections per host, and the monitor's own request
  queued behind all of them. Timed from `playFrom(0)` against the live bin, reading pixels back
  off the element into a canvas: the first non-black frame (mean > 5) arrived at **6.8 s /
  9.5 s / 7.9 s** over three runs — readyState 0 → 1 with a mean pixel of 0.0 until then, while
  the audio had long since started. A card only ever showed one frame, so it is an `<img>` now.
  `GET /media/poster/<stem>.jpg?t=<seconds>` cuts a 320 px JPEG out of the proxy with
  `ffmpeg -ss`, caches it under `--work/posters/<bin>/` keyed by clip and time, and serves it
  `immutable` — the same containment as the other media routes (the name is a stem inside
  `proxy_dir`; four traversal shapes 404). Measured: **2–13 KB** per poster, **322 ms** cold and
  **3 ms** warm, all 17 built in **1.27 s** at six at a time, 180 KB of disk for the bin. One
  page load now moves **93 KB** of media instead of opening 19 streams, and the first painted
  frame is under a second. Trimming an in-point moves the poster too, debounced 450 ms, so
  holding the button costs one frame and not one per press. Verified: 146 API + 32 browser
  tests pass (was 138 + 28), including a browser test that reads the monitor's pixels back and
  fails if it is still black 5 s after play, one that a card carries a poster `<img>` and no
  `<video>` at all, and one that six 0.25 s nudges cost at most two poster requests.
- **The test suite was writing invented spend into the real inference ledger.**
  `config.ledger_path()` falls back to `~/work/roughcut-ledger.jsonl` — the file the
  project's cost claims are read out of — and `inference._log()` appends to it after every
  completion, including the ones returned by the *scripted* backends `test_server.py` and
  `test_ui_flow.py` install. Only `test_inference.py` ever set `ROUGHCUT_LEDGER`, so one
  `pytest app/tests` run added **6 rows** of fabricated calls to the ledger (476 → 482 lines,
  measured before and after on this machine); every run since the scripted backends existed
  has been quietly diluting the record. A session-scoped autouse fixture in
  `app/tests/conftest.py` now points the variable at the run's own tmp root — session-scoped
  because asks and renders run from module-scoped fixtures and from background threads where
  a per-test patch is no longer in force, and inherited by the tools the server shells out
  to. Verified by running both suites with no `ROUGHCUT_LEDGER` set and re-counting: 138 API
  + 28 browser tests pass and the real ledger is byte-identical before and after (same line
  count, same md5), where the same run previously added 6 lines.
- **Nothing said which render was the cut on the board.** Karl: *"Cut board doesn't seem to
  reflect the render"* — after watching `cut_91e0b993`, a render of the 20-shot *proposal*,
  against a board holding the 16-shot cut. That render was correctly labelled *proposal
  ed8134bb — not accepted*, but the versions list is three files whose names are hashes and
  nothing marked the one the board *did* reflect. Renders record the shot list they were made
  from now, and a version matching the timeline is labelled **· this cut** — recomputed on
  every edit, so it disappears the moment the timeline moves away from it. Renders made
  before this fall back to matching on shot count and total length. (Verified against the two
  Killington renders: `cut_29ed8c3f` is the 16-shot cut on the board, `cut_91e0b993` is the
  proposal. Frames extracted from the render at three shot boundaries match the proxies at
  the EDL's clip times to 0 frames at the head and −4 frames by shot 13 — the render runs
  0.161 s long over 177.4 s, ~10 ms a shot, already recorded as `planned_s` vs `duration_s`.)
- **Playing a shot from its card played it in a monitor that was off the screen.** The
  monitor is at the top of the column and the shot list runs a long way below it; on the
  16-shot Killington cut, clicking shot 12's poster started playback 3,163 px above the
  viewport — measured — where nothing about it could be seen. The board played and the page
  sat still, which is indistinguishable from a board that will not play. A play started from
  a card, or with <kbd>space</kbd>/<kbd>enter</kbd> from down the list, scrolls the monitor
  back into view first.
- **The board served its own code with no cache headers of any kind.** No `Cache-Control`,
  no `ETag`, no `Last-Modified` — nothing telling a browser either to keep `index.html` and
  `app.js` or to check them. Chrome does refetch such a response (measured: on a reload both
  came back 200 from the network, with no conditional headers), but that is a browser's
  choice rather than a promise, and the two files have to agree with each other: a page
  holding a monitor that the cached script has never heard of paints a board that will not
  play, which is the shape of the report this came out of. Both are `no-store` now; they are
  64 KB over loopback.
- **A byte range was answered by reading the whole file into memory.** `bytes=0-` is the
  first thing every `<video>` sends, and `fh.read(end - start + 1)` on it pulled an entire
  proxy into RAM before a byte reached the browser — 85 MB for a Killington clip, sixteen of
  them plus two 150 MB render previews on one load of the board. Measured against the live
  server: RSS went from 187 MB to 674 MB on a single page load, all-time peak 1.15 GB, for
  files it only ever had to copy. Ranges stream a megabyte at a time now (taking 2 MB off the
  front of a 20 MB file costs 2 MB, asserted). In passing: `bytes=-500` meant the *first* 501
  bytes rather than the last 500 — harmless while every proxy is written `+faststart` and no
  player has to hunt for a trailing moov atom, and a silently wrong answer the day one does.
- **The monitor had one way of reporting anything: a black rectangle.** A proxy still
  opening, a proxy that will not open at all, and a browser refusing to start an unmuted
  video looked identical to each other and identical to a broken board — `play()`'s rejection
  was thrown away by an empty `.catch(() => {})`, and a media error was reported only if it
  struck the live buffer mid-play, and then only as a guess (*"previews build in the
  background"*) rather than as what the browser had actually said. The screen carries the
  answer now: *opening CLIP_04…*, *buffering…*, `CLIP_A: that proxy would not open — it may
  still be building (code 4)`, or *the browser refused to play — click the monitor, then press
  play again* — and clicking the monitor does play it, which is what makes that advice
  followable. Errors also stop the transport instead of leaving it reading ❚❚ Pause.
- **Pressing play twice while a shot was still opening left the monitor playing behind its
  own transport.** Karl, on Killington: *"the timeline LOOKS good, but I cannot play it
  seems?"* On the synthetic test project a proxy opens in milliseconds; on a real bin the
  sixteen shot cards hold every connection the browser gives one origin, and the monitor's
  first `play()` waits 2–3 seconds on `loadedmetadata` (measured against the live server:
  first frame 3.0s after the click, with nothing on screen in between). Press play again in
  that window — the obvious thing to do when a button flips to ❚❚ Pause and nothing happens —
  and the second press paused a monitor that had not started, after which the first press's
  deferred callback fired and played the video anyway. Sound came out of a board whose clock
  read 0:00.0, whose playhead never moved, which never reached the shot's out-point and never
  handed over to the next one. The monitor counts its commands now; a deferred callback that
  finds the count moved on does nothing, and a buffer re-pointed at another clip no longer
  parks the old shot's in-point on the new one.
- **Karl's "the video quality seems like there may have been an encoding issue" feeling on the
  Killington renders was measured, not guessed — and it's real, though not a bug.**
  `cut_91e0b993.mp4` / `cut_29ed8c3f.mp4` come from 4K sources (3840×2160 — corrected
  `benchmarks/README.md`'s B2 row below, which recorded a uniform H.264 29.97fps and missed that
  3 of the 12 clips are actually HEVC 59.94fps) down to `assemble.py`'s fixed 1920×1080 @
  24000/1001 preview target. On one high-motion 9.9s shot (CLIP_11's ski-jump dare): the `fps`
  filter drops a clean, regular 20% of frames on the 30fps clips and 60% on the 60fps ones —
  exact ratios, not erratic ones, because GoPro's `/1001` denominator cancels on both — and the
  2×/2× downscale is the dominant *visible* loss: a matched-frame crop of fine detail (bare
  branches, a chairlift cable) is clearly softer at 1080p than at native 4K, while a low-detail
  snow crop barely differs. `-preset veryfast -crf 20` is a minor contributor by comparison —
  already near-transparent at its own target size (VMAF 99.2–99.4 against a same-size lossless
  reference, via the static ffmpeg build's own `libvmaf`). Colour is fine: the sources are 8-bit
  Rec.709, full-range, not HLG/Log as guessed, and the full→limited range retag `format=yuv420p`
  performs does not clip anything (matching luma min/max/avg on extracted frames, source vs.
  render). The concat `-c copy` path and the music mix never touch the video bitstream — re-ran
  the music pass a second time on both real renders and got byte-identical video MD5s and frame
  counts. No code defect: the loss is the preview profile's speed/size tradeoff, real and now
  documented instead of felt.
- **Proposals arrive with their boundaries polished: the word finishes, and the shot stops
  trailing off.** Karl, on the Killington revision proposal: *"Dual issue on clip length,
  both with similar frequency: (A) clip is too long and we trail off on conversation; (B)
  clip is too short and you clip words, like POCKET PI[CLIP] — should finish PIZZA."* Both
  are the same defect: the model picks its in/out points by reading a transcript, and a
  transcript timestamp says when a word was *decoded*, not when the sound of it stops.
  Measured on this bin over eight isolated line-ends, a word's own energy falls 12 dB below
  its peak a median 0.12s after the end the sidecar gives it, and up to 0.26s — so the gag
  shot, CLIP_03 18.4–20.9, cut 0.12s past `pizza!` and sounded like a cut through it. A new
  pure function (`roughcut/boundaries.py`) runs over every validated plan from both
  `propose()` and `originate()`: an out-point inside a word — or within 0.30s after one —
  extends to that word's end plus a 0.45s tail pad; an in-point inside a word moves back to
  its start less 0.25s; and an out-point stranded more than 1.5s past the last word spoken in
  the shot loses the dead air. Every move is capped at 1.2s, recorded on the segment as
  `polished_from` plus a one-line reason that the proposal panel shows under the shot's
  `why`, and summarised in the plan's notes. **This is not auto-snap** — HANDOFF's *do not
  auto-snap an originated plan* stands; nothing here reaches for the next utterance, which is
  what made snap swallow whole lines. Three things are never touched: a shot with no speech
  in it at all (it was chosen for its picture), a boundary that would move into a stretch the
  visual pass called unusable, and dead air held over a moment the visual pass marked
  notable. Run over the two proposals on disk: `ed8134bb` 11 of 20 shots adjusted, 178.0s →
  184.8s, with CLIP_03 18.40–20.90 → **18.09–21.23** (`pizza!` ends at 20.78); `c710983b` 8
  of 16, 176.9s → 181.7s. The three longest-trailing shots — CLIP_04 261.7–271.0 (7.1s past
  the last word), CLIP_09 192–200 (6.5s), CLIP_01 92.2–107.0 (5.9s) — are all correctly left
  alone, because each is holding on a fall or a crash the frames call notable. The EDL on
  disk is never touched, and `edl_snap.py` is unchanged. 20 new tests.
- **The ASR was deleting speech before Whisper ever saw it, and the ski-patrol talk was
  part of what it deleted.** Karl, on the Killington revision proposal: *"I THINK there were
  some interesting discussions on running from ski patrol — you also seem to have missed
  these; ensure the transcript is generated correctly from the original video."* The pass ran
  faster-whisper with `vad_filter=True`, and Silero at its default threshold treats
  helmet-mic speech under wind as non-speech: **"Is there ski patrol?" and "Yo, be careful,
  there's ski patrol over there" (CLIP_09, 109.9-115.8s) were absent from the sidecar
  entirely**, along with "All right, let's get out of here" at 105.3 — the four seconds that
  make the following conversation about not antagonising the jump-builders ("they could let
  someone know down below… four guys coming down wearing a red helmet") legible as what it
  is. What survived the filter was stitched across the removed silence, so segments claimed
  spans they did not have: CLIP_07 carried one "utterance" of six words over 92.8 seconds, and
  `asr_speech_fraction` counted the silence inside it as speech. The filter is off now, which
  is what the existing `no_speech_prob > 0.6` and hallucination filters were already there to
  make safe. Re-running the bin (12 clips, 3m02s on the 5080, sidecars backed up to
  `killington-neutral.bak/`) takes the transcripts from **1,361 words to 1,656** (+21.7%) with
  11 of 12 clips up or level — and the pass now also records each word's **end** (`e`), not
  only its start, because the segment end is a decoder artefact and word ends are what a cut
  point has to respect. One regression, reported rather than hidden: CLIP_06 loses 14 words of
  overlapping chatter.
- **A revision with the visual pass behind it ran past the 300s call timeout and was
  killed.** With every Killington clip looked at, the originating prompt is ~39k tokens and
  the plan it returns ~15k, which took 199s on the CLI backend; the revision of the 16-shot
  cut that followed, carrying a 178-word note on top, crossed 300s and the board reported
  *"claude CLI timed out after 300s"* — the work gone, since the CLI writes its transcript at
  the end. `visual_pass.py` had already raised its own default to 600 for the same reason.
  The default in `config.py` is 600 now (`ROUGHCUT_CALL_TIMEOUT_S` still overrides).
- **A save could be observed half-written.** The board autosaves on every edit while its own
  status polls, the monitor and any render job read the same EDL, and a plain write truncates
  the file before filling it — a reader landing in that gap got an empty file and a parse
  error. Found by a new test polling the EDL during a save. Writes go to a temp name and are
  renamed into place now, the same fix the proxies got; twenty hammered saves are never seen
  unparseable.
- **The CLI backend could not actually read an image.** SPEC §6.1's "images by path" rule was
  never true in practice: `claude -p` answers *"I need your permission to read the image"* and
  then fails schema validation twice, spending two calls to learn it. It now passes
  `--allowedTools Read` when, and only when, a request carries images — the narrowest tool that
  does the job, and never a standing capability on text calls.
- **The board no longer loses your work on refresh.** Karl: *"I start the project and create some
  cuts — but then it resets the cut board as soon as I refresh the page."* The working edit lived
  in browser memory and only the Save EDL button wrote it, so a reload discarded everything
  accepted and trimmed — he lost a 16-shot cut that way. Every mutation now autosaves (debounced,
  immediate on accepting a proposal), the header shows `saved 20:41` rather than a button, and
  Save EDL is gone because it no longer means anything. The EDL on disk was always meant to be
  the source of truth; it actually is one now.
- **The buttons that did not earn their place are gone or renamed.** *Snap to speech* → **Fix 3
  cut points**, disabled and reading `Cut points OK` when there is nothing to fix. *Analyse audio*
  hides itself when everything is analysed instead of sitting there greyed out. The header empties
  on a project with no cut, since nothing in it applies. The B comparison slot appears when
  there is a second version rather than showing a black rectangle. *Add a moment* leads with what
  is said instead of a ranking score. The proposal moved out of the 340px sidebar into the main
  column at full width, listing every shot with the reason it was chosen — and, on an empty
  timeline, the sidebar Ask panel hides so there are not two boxes asking for the same sentence.
- **Every long operation now says where it is.** Karl, clicking Render: *"got like no response —
  and just see rendering…"*. `assemble.py` gained `--parts-dir` so the per-shot files it already
  writes land somewhere the server can count, and the render job reports `cutting 7/16` then
  `joining`, with elapsed time, like the audio pass. Snap moved off the event loop too (`uv run`
  alone costs the better part of a second, and blocking there stalls every other request). The
  audit behind this: Ask, analyse, previews, render and snap were the five operations that can
  outlast a click — all five report state now, and every one shows a clock, because a number that
  moves is the only difference between "working" and "hung".
- **Ask is a job, not a two-minute request.** Karl, stuck on `building a first cut — about a
  minute…` while the model had in fact answered: `/api/ask` was an `async def` running the call
  synchronously, so a 112-second Killington ask **froze the entire server** — status, media,
  previews, everything — and the plan existed only in that one HTTP response, so anything that
  disturbed it spent the call for nothing. (His was recovered from the CLI's own session
  transcript: 16 segments, 2:56, two runners found cold.) Now: `POST /api/ask` returns a job id,
  the call runs in a thread, `GET /api/ask/{job}` reports state and **elapsed seconds** so the UI
  counts up instead of showing a frozen string, and the plan is written to disk *before* the job
  says done. `GET /api/asks/latest` offers the most recent proposal on load, so a reload or a
  closed tab costs a click rather than another call.
- **Renders are per-bin, and preview building is visible.** Both found by Karl opening the app on
  Killington: it announced *"✓ render 1 version"* and played a **Copper** cut in the A slot,
  because proxies were per-bin but the renders directory was not. And nothing anywhere said the
  remaining previews were still encoding — *"need more indication of what is actually going on in
  the tool UI itself"* — so a half-hour background job looked like an idle app. `/api/status` now
  reports `proxies: {done, total, ready}`, the Project panel shows a live count with a bar and
  says the Ask does not wait on it, and renders made before the metadata sidecar existed are
  labelled "older render" rather than `0:00.0 · ? shots`.
- **A render announced itself before writing its own metadata.** The UI refreshes the versions
  list the moment a job reports done, so the new render could be listed unlabelled. Metadata is
  written first now — the same ordering lesson as the previews stage, found by a test that only
  failed under full-suite load.
- **Analysis progress no longer depends on the tool saying anything.** Caught by watching a real
  12-clip run sit at `0/12` for two and a half minutes and then jump straight to done: the
  counter was refreshed once per line of the child's stdout, and `audio_analyze.py` prints
  without flushing, so a pipe held all of it until exit. The count was right and the trigger was
  wrong — indistinguishable from a hung job, which is the exact confusion this session set out to
  remove. A ticker now polls the sidecars on its own clock, and the child runs with
  `PYTHONUNBUFFERED` so the log streams too. The preview stage reports its own `proxy_done` /
  `proxy_total` as well, because encoding is the *longer* of the two stages on a real bin (about
  half an hour against two and a half minutes) and a bar parked at 100% for that long says
  nothing. Both regression tests were confirmed to fail against the old code.

### Added
- **Shot order is information, not a rule.** Karl, once the fix landed: *"note that we don't
  ALWAYS need to go chronological."* The first wording read as a constraint and the cut that came
  back was almost strictly in order. Rebalanced: the film's order is a choice, a strictly
  chronological cut is usually the dullest one available, and only the *accidental* kind of
  backwards move — drifting through one continuous stretch for no reason — is the mistake.
  Deliberate moves just have to be named in the `why`.
- **The Killington bin has a neutral view.** Its files are hand-named after their content
  (`spenny-bigair-begin`, `rockhitmarkers`, `tastytrees`) — those names *are* selection work a
  human already did, and clip names reach the prompt, so an in-app run would not have been a
  blind test. `~/footage/killington-neutral/` holds `CLIP_01…12` symlinked in capture order with
  a mapping file beside them. 12 clips, 35s–319s, ~44 min; rotation side data present and
  correct on the later clips, so `--orient auto` (the default) is right for this bin.
- **The model is told when each clip was shot, and the ordering defect goes away.** Karl on the
  first originated cut: *"some weirdness where airport footage was cut seemingly out of order in
  a way that didn't make sense."* He was right, and it was not judgement — the model had never
  been given a capture time, so it read a 20:43 clip, a 21:40 clip and a 22:17 clip as
  interchangeable and cut back to the first after the third. Clips now carry `captured` (container
  `creation_time`, mtime as fallback, cached), and both prompts render it as **relative** position
  — `recorded #3 of 17, session 1, 47 min after the previous`. Relative on purpose: GoPro writes
  UTC, the trip was not in UTC, and a time of day seven hours out is worse than none; sessions are
  inferred from gaps over 4h, which recovers "different day" without knowing the timezone.

  Same bin, same brief, one call: **in-session backwards cuts 5 → 1**, and the survivor is
  deliberate and declared — it puts the last-shot run before the lodge debrief *"because the
  debrief is the film's ending and nothing should follow it"*, then names it as one of two things
  to check first. The travel section is now in order. It also found a payoff the first pass
  missed: Spencer drinking the day-old milk, so the legend ends on a gag. 19 segments, 136.4s,
  $0.42. Rendered: 136.71s, no rotation, −15.9 LUFS, no black runs.
- **D7 closed: the repo has a remote.** `origin` is github.com/swansk/roughcut (private), `main`
  tracking it, 34 commits pushed. Three sessions of work had been sitting on one disk with no
  backup — the only artifact in the project that is not regenerable.
- **A sense of order, and renders as versions.** The board was flat — Ask, Snap, Undo, Save and
  Render as peers with nothing saying what to do first. A five-step strip (footage · analyse ·
  first cut · refine · render) marks where the project is, read from state rather than tracked,
  so it cannot drift from the files on disk. `GET /api/renders` lists every version newest-first
  with duration and shot count, metadata written next to each file so the list survives a
  restart, and any two load into side-by-side players — the newest into A and the previous into B
  after each render. Judging an edit is comparative; showing only the newest file meant hunting
  for mp4s on disk to compare.
- **Backend problems appear at launch, not 80 seconds into an Ask.** Preflight reports what is
  knowable for free — which backend and model, a `claude` CLI missing from PATH because the
  server was started from a non-login shell, an API backend with no key — at startup and in the
  header. One deliberately tiny call (the cheap per-unit role, in the background, `--no-probe` to
  skip) answers the part nothing free can see: whether the backend is authenticated. The header
  pill shows ready / checking / the actual error, and clicking it re-checks. Startup prints now
  flush, since stdout to a pipe is block-buffered and diagnostics would otherwise sit unseen
  behind `uvicorn.run` for the life of the process. Measured: the probe replies "OK" in ~5s and
  bills ~27k tokens on the CLI backend, which is the harness-overhead finding again.
- **Ask can originate a cut, not only revise one — and it works.** `revise.originate()` is the
  same call addressed to an empty timeline, and `/api/ask` routes to it when there is nothing to
  revise. This removes the hand-authored-EDL prerequisite, the step that most made the app
  expert-only. The originating prompt states the two traps a model reading clips one at a time
  cannot rediscover — transcript density points *away* from the action here, and the connective
  tissue is usually a running joke rather than a topic — and tells the model plainly that it
  cannot see the frame.

  Live on Copper, from an empty EDL and the agreed brief (which never mentions milk): 20
  segments, 172.7s, 16 of 17 clips used, 98s, $0.32 projected. It found the milk joke by itself,
  built "the day Spenny became the milkman" as the spine with the setup indoors and the payoff on
  the hill, kept travel to four cuts totalling ~31s as the brief asked, and closed on *"we
  created a legend — and now it's over"*. It also declared its own blind spots without being
  asked: the 18s held shot with no transcript that "may be a glove or a lift queue", and the one
  clip it refused to gamble on. Rendered: 172.97s, 18ms A/V drift, no rotation, −16.1 LUFS, no
  black runs.
- **The audio pass runs in-app, with real progress.** `POST /api/analyze` runs
  `audio_analyze.py` over the bin in the background and reports `done`/`total` counted from the
  sidecars on disk — the tool writes one per clip as it finishes, so the filesystem is the honest
  progress bar and stays right if the log format moves. Proxies for the newly analysed clips are
  built *before* the job reports done, because a job that says "ready" while its own previews are
  still being written hands the UI a `<video>` that stays broken until reload. Junk clips can be
  excluded via `skip`, one analysis runs at a time, and a failure surfaces its log instead of
  disappearing.
- **A folder of footage is enough to open the board.** `--edl` and `--sidecars` are now optional:
  a missing EDL is scaffolded (empty segments, title from the bin, `orient` an explicit choice
  because it is per-bin and never generalisable) under `--work`, and sidecars default to a
  per-bin path. This removes the hand-authored-JSON prerequisite from *opening* the app; letting
  Ask originate the cut removes it from *using* the app. New `GET /api/status` reports where the
  project actually is — clips in the folder, how many are analysed, what is still pending, shots
  in the cut, tools on PATH — and the UI shows it as a Project panel plus an empty-timeline state
  that says what to do next instead of showing a blank. Proxy directories are now per-bin, since
  two bins can hold the same GoPro stem.
- **Ask proven against a live model, and the answer is yes for structural work.** One call on
  variant B (74s, $0.27 projected) went 20 segments/162.5s → 18/129.2s: dropped the luggage
  walkway with the reason *"no faces"* (the brief's own criterion, applied correctly), dropped
  the inert lounging tag, moved 32s from the opening into the middle exactly as the note asked,
  extended the groomer POV to pick up dialogue the hand cut discarded, and reordered three beats
  within one clip to build dare → deed → reaction. **17 of 18 out-points land exactly on an
  utterance end**, and the single exception is declared in its own notes rather than hidden.
  Rendered and verified: 129.38s, 5ms A/V drift, no rotation, −15.6 LUFS, no black runs. Two
  caveats recorded in HANDOFF: it avoided the known visual traps only because it inherited the
  `why` fields from the hand-authored EDL, and its reorder may invent a chronology the footage
  contradicts — which text cannot settle.

### Known
- **The app cannot take a user from start to finish** (Karl, after using it). It is a refinement
  tool that assumes five prior terminal steps — footage copied, sidecars analysed, junk list
  known, **an EDL hand-authored**, server launched with three path flags — so it owns the middle
  of the workflow and neither end. Diagnosis and the shape of the fix are in
  docs/HANDOFF.md; the highest-value single piece is letting Ask *originate* a cut rather than
  only revise one, which removes the hand-authored-EDL prerequisite.
- **The interject loop: "Ask for a change".** A plain-language note ("tighten the intro", "build
  it around the milk joke") returns a revised timeline, shown as a **diff you accept or discard**
  and undoable once applied — a model edit that applied itself is how an editor learns to stop
  trusting the tool. The model receives every clip's transcript, the current edit, the story text
  and the target length, because R8/R9 established the words are the strongest signal in this
  footage; a revision prompt without them asks the model to edit blind. Plans are validated hard
  (unknown clip, inverted range, timestamp past the end of a clip) with one **bounded** re-ask —
  an invented timestamp that renders as missing footage is worse than a visible error.
- **`roughcut.inference` — the single doorway for model calls** (SPEC §6), and `roughcut.config`,
  the only place a model ID may appear. Both backends behind one interface
  (`ROUGHCUT_BACKEND=claude_cli|anthropic_api`), images addressed **by path** never base64,
  schema as a required *outcome* rather than mechanism (prompt-and-validate on the CLI backend,
  native on the API). Every call is logged with tokens and `projected_usd` **on both backends**,
  and the budget cap is enforced against the projection *before* the call — on the Max
  subscription there is no marginal cost, but the projection is what preserves the ability to
  answer "is this affordable in production", which is the whole reason the discipline exists.
- **21 more tests (50 total, ~20s)** covering the inference contract and the ask loop against a
  scripted backend — no live calls, per CLAUDE.md. They pin the guardrails specifically: budget
  refusal happens *before* the model is called, the retry is bounded at one re-ask rather than
  looping, an unknown model family is never priced as free, and a failed ask surfaces in the UI
  instead of failing silently.

### Known
- **Ask cannot run live yet:** the Claude CLI inside WSL is not logged in (`claude /login`), so
  a real revision has never been generated and proposal *quality* is unmeasured. The endpoint
  returns 502 with the actual reason and the UI displays it.
- **The app wrapper exists** (`app/`) — a local web "cut board" over the EDL, and now the active
  workstream after Karl chose it over hardening the T0–T13 pipeline. CLAUDE.md's "no Phase 2
  features" rule was amended in the same decision rather than quietly ignored. Timeline of
  segment cards with previews parked on the in-point, the transcript lines falling inside each
  cut, drag-reorder, trim by button or keyboard, a story panel saved into the EDL, one-click
  insert from ranked audio candidates, Snap-to-speech shown as an undoable proposal, and
  background render played back inline. It is a *view over the EDL file*, not a new source of
  truth — the same JSON `assemble.py` renders and `edl_snap.py` rewrites — so hand edits stay
  scriptable and scripted edits stay visible.
  - **Latency is the design constraint**, because "fun and easy" reduces to "the loop is tight":
    the browser never touches the 5.3K masters (7.6 GB of Copper → 85 MB of proxies, ~90×
    smaller), and media is served with byte ranges, without which a `<video>` cannot seek at all.
  - **Live boundary warnings** flag a cut that opens mid-sentence or clips a line off — the
    defect Karl reported — while trimming, not only when asked. The seeded B variant showed 5;
    Snap cleared all 5.
- **29 tests for the app** (`app/tests/`) — 19 API plus 10 driving real Chromium via playwright,
  ~18s, against a synthetic three-clip project with fabricated transcripts so they are fast,
  deterministic, and independent of `~/footage` and the GPU. `install_browser_deps.sh` makes
  headless Chromium runnable without sudo, the same pattern already used for ffmpeg and uv.

### Fixed
- **Proxies were served while still being written** — a `<video>` received a truncated stream
  and cached the failure until reload. Built to `.part.mp4` and renamed atomically.
- **`edl_snap.py` grew silent segments.** A shot chosen as a quiet beat (a ski pass, a held
  landscape) would reach past its own end and absorb the next utterance, turning it into
  dialogue nobody asked for — the exact "silently rewrites your timeline" behaviour the tool was
  supposed not to have. Closure now requires the segment to already carry speech. Found by
  `test_snap_leaves_clean_boundaries_alone`, and re-verified against the real B cut: output is
  byte-identical, so this closed a latent footgun without changing the current edit.
- **The proxy filter upscaled sources smaller than the target**, spending bytes on detail that
  does not exist. Capped by width, so anything under 1280 is passed through untouched.
- **Tier B audio event tagging** (`research/tools/audio_events.py`, AST/AudioSet on the GPU) and
  its study, **[R9](research/R9-events-and-wind.md)** — which measured that the feature R8 and
  AUDIO.md both named "the highest-value remaining audio work" **does not pay off on B1**.
  The best score for any reaction class (Laughter, Whoop, Cheering, Yell) across the whole 398s
  bin is **0.077**, from passes that simultaneously score Speech at 0.44–0.76 and Wind at 0.65,
  so the model is working and discriminating. Window length was ruled out separately (1.5s to
  10.24s moved Laughter by hundredths). The mic is on the camera, the operator is the one
  talking, and the skiers are fifty metres away — the laughter visible in the contact sheets is
  simply not in the audio. The tagger is kept for bins with crowd or close-mic audio; it will
  not improve a Copper cut.

### Fixed
- **The Tier A wind detector had never fired once**, which R8 misread as "B1 isn't windy". The
  event tagger scores Wind at 0.652 on the moving POV run and 0.541 on the following shot, while
  R8's `wind_dominant_fraction` reported ≤0.08 on every clip. The broken constant was flatness:
  real wind here sits at **0.17–0.25** against a guessed threshold of 0.40, making the
  conjunction unsatisfiable. Flatness is nonetheless the right discriminator — the plane-cabin
  clips carry *more* low-band dominance (10.9 dB) than most windy clips at flatness 0.008,
  because engine rumble is tonal where wind is noise-like, so a dB-only rule would call an
  aircraft interior windy. Recalibrating the ratio alone then broke on silence (an empty speech
  band makes any hiss look wind-dominant, scoring the silent base-area clip 0.42 against the
  tagger's 0.00), so the rule now carries an absolute term:
  `low > -40 dBFS AND low − speech > 2 dB AND flatness > 0.15` — 4 of 5 tagger-positive clips
  with **0 false positives** on the other 12, where every rule catching all 5 carried at least 5.
  This matters beyond bookkeeping: the trigger map for P2.2 audio post was previously empty.
- **`audio_analyze.py` no longer discards `events` when re-run.** Tier A and Tier B write to the
  same sidecar from different tools, so re-running the cheap pass silently wiped the expensive
  one.
- **A rendered cut could play fully rotated.** `-noautorotate` suppresses *applying* B1's
  spurious rotation but the display matrix is still copied to the output stream, and concat with
  `-c copy` inherits stream properties from the first part — so one clip's bad metadata rotated
  an entire film. Variant A opened on `GX010474` (`rotation=-90`) and played sideways end to
  end; variant B opened on a clip with no side data and was fine, which is why every automated
  check passed. Fixed with `-display_rotation 0` before each input (`-metadata:s:v:0 rotate=0`
  was measured and is a no-op against a display matrix in ffmpeg 7), plus `assert_no_rotation()`
  in `assemble.py`, which fails the render if any part or the final file carries rotation side
  data. Verified by decoding a frame from the fixed file with no flags — as a player would.

### Added
- **`research/tools/edl_snap.py`** — derives cut boundaries from the transcript instead of the
  sample grid: moves `in` back when a shot opens mid-utterance, extends `out` rather than cutting
  a line off mid-delivery, and absorbs a following utterance within `--gap` so an exchange gets
  its reply, bounded by `--max-extend`. On variant B it adjusted 9 of 20 segments (+25.9s) and
  caught the cold open cutting "And now… it's over" in half. This is also what carries the cut
  to length without padding.
- **Phase 2 scope from Karl's review of the first cuts** (FUTURE_PHASES P2.5, new P2.6): an
  interface for the human to interject, set scene and story, and fine-tune in a way that is *fun
  and easy*; and human-supplied music as a **non-default** cutting grid, where segment durations
  become slot-driven — which changes selection's contract from "pick the good bits" to "fill
  these N slots of these lengths".

### Changed
- **Variant B recut to Karl's note and is now the direction** ("B is better than A — you
  identified the core theme (milk) and then edited well around it"). It cold-opens on the title
  drop — "I became the milkman today. We created a legend. And now… it's over" — then hard-cuts
  to the airport and earns it back; "that goes so fucking hard" moves to the final act where it
  has context. With transcript-snapped boundaries and wider dialogue segments it runs **2:43**,
  inside the 2–3 min brief. Variant A, upright and re-rendered, runs 2:24 and is kept for
  comparison only.
- **The first two rough cuts exist.** `research/tools/assemble.py` renders an EDL
  (`research/edl/B1-variant{A,B}.json`) into a finished file, and both variants of the Copper
  edit are cut, rendered and verified: A "The trip" at 1:54, B "The legend" at 1:50. Selection
  reasoning, per-clip inventory and the R6 self-critique are in
  `docs/notes/2026-07-25-selection.md`. Verified on fresh output: 18 ms A/V drift (under one
  frame), −15.5 LUFS integrated against a −16 target, no black or frozen runs, planned-vs-actual
  duration within 0.2s. Two assembly choices are load-bearing rather than incidental:
  `-noautorotate` on every input, since B1's rotation side-data is spurious, and loudness matched
  by fixed per-clip gain from the R8 sidecars rather than per-segment `loudnorm` — five
  consecutive segments come from one clip, and a per-segment normaliser pumps across them.
- **Visual analysis of all 17 non-junk B1 clips**, read from the contact sheets against each
  clip's transcript. This corrected two things a partial pass had wrong: B1 is **70% on-mountain**
  (277s of 398s), not mostly travel — the base-area and chairlift clips had been misread as
  "lodge" from their banter — and R8's Result 4 table has been updated accordingly (its finding
  survives and sharpens: audio splits candidates 50/50 across a bin that is 70/30 on-mountain).
  It also surfaced what no single clip shows: **a running joke about a gallon of milk that runs
  the length of the trip**, present in 6 transcripts and on screen in 5 clips, which is why
  there are two variants rather than one.
- **Audio analysis pass** (`research/tools/audio_analyze.py`, `research/tools/audio_calibrate.py`)
  and its study, **[R8](research/R8-audio-signals.md)**, run over B1's 17 non-junk clips. Tier A
  DSP (speech-band level, spectral flatness, low/high ratio, spectral-flux onsets, 4 Hz
  modulation, silence) plus faster-whisper `large-v3` on the GPU, emitting the AUDIO.md sidecar
  schema: tracks at 10 Hz, transcript with word timestamps, summary with loudness, and a ranked
  `candidates` list. CUDA in WSL2 solved without a system toolkit or sudo (pip `nvidia-*` wheels
  preloaded via `ctypes.CDLL`); ASR runs ~13× realtime.

### Changed
- **Speech candidates now come from the transcript, not the DSP detector.** R8 measured that
  detector against ASR at F1 0.63, versus 0.56 for the null rule "assume everyone is always
  talking" — an edge too thin to spend candidate slots on when the words themselves are
  available. Thresholds were also measured rather than guessed (`flatness < 0.10`, 3.5× lower
  than AUDIO.md's design value, which the first grid pinned against its own edge). The DSP
  tracks stay as quality metering and as the no-ASR fallback.
- **AUDIO.md's coarse-to-fine premise is reversed for B1.** Audio flags the travel and lodge
  footage at 12.3 candidates/min against 6.0/min for the on-mountain clips, and four times the
  word rate — the skiing is quiet because the subject is fifty metres from the microphone. Audio
  is therefore evidence, never the gate on where the visual pass looks; quiet clips need *more*
  attention, not less. Documented inline in AUDIO.md and in HANDOFF's do-not-re-litigate list.
- Onset spikes required `z>8` plus a 97th-percentile floor: at the initial `z>3` the rule fired
  15.2×/min and attached itself to nearly every candidate, because MAD collapses in steady audio.
  Interest markers split strong/weak after `yeah` and `dude` — the ambient register of this
  particular trip — promoted banter to a perfect score. Both defects were found by reading the
  ranked output, not by a metric; candidate count fell from 130 to 68 and the top of the list
  became reactions.
- **Audio analysis design** (docs/AUDIO.md): audio as the *fast pass* — 1D, seconds per bin,
  fully local — producing the temporal map that points the expensive visual stages. Documents
  the wind trap prominently (full-band RMS on ski footage measures wind, not interest, and would
  rank a windy traverse above the best line of the day), the DSP/model signal tiers, the output
  schema with a `candidates` list, and how audio ("when") composes with vision ("what").
- **docs/HANDOFF.md** — session state, the agreed brief, the human-in-the-loop map, findings that
  must not be re-litigated, environment setup, and open decisions.
- **`benchmarks/labels/B2-orientation.json`** — and the finding that settles the orientation
  design: **B1 and B2 require opposite handling.** Killington's long-form clips were shot on an
  inverted mount, so 7 of 9 are stored upside down and their `rotation=-180` side-data is
  *correct and must be applied*; Copper's side-data is *spurious and must be ignored*. Same
  owner, same sport, same season. Any global policy is silently wrong on one of the two bins,
  which is the whole case for resolving orientation per clip from the pixels.
- **`research/tools/orient_audit.py`** and the resulting
  `benchmarks/labels/B1-orientation.json`: one representative frame per clip on a single sheet
  with rotation metadata labelled, reviewed by eye, decisions committed. Six of B1's 26 clips
  carry rotation side-data across three values (−90, +90, −180) and **all six are spurious** —
  every judgeable clip is upright as stored. Clips too dark to judge are recorded as
  `unverified_too_dark` rather than assumed.
- **Empirical junk-detection data for B1**: 44% of the bin (290s of 662s) is dark, with a clean
  separation between real content (luma 92–174) and black (luma 1–11). `GX010479.MP4` is the
  bin's longest clip at 74s and sits at luma 5.3 — the camera-left-running case, present in the
  first real bin. Also recorded a naive threshold's false positives on dark-but-real footage,
  which is why the T5 detector must stay conservative.
- Ordering, junk, and orientation-correction constraints from Karl recorded in SPEC: chronology
  is the default spine but deliberate, explained departures are correct (§3 S3); junk detection
  is a quality filter that stays on, distinct from cost gating which stays off (§3 S2);
  mis-shot footage must be *corrected*, not passed through (§3 S0).
- **Product intent as design principles** (SPEC §0), from Karl's framing: human time goes to
  taste and judgment never mechanical work; fast feedback or the human disengages; present
  options rather than demand specifications. Recorded with a self-check — the manual labeling
  pass violates principle 1, which is why the contact-sheet index exists.
- **`research/tools/contact_sheet.py`** — timestamped contact sheets from video (ffmpeg extract
  + Pillow composite, since static ffmpeg builds ship without `drawtext`). Emits a JSON index
  that stays authoritative if labels are unreadable, supports `--neutralize` for R7's filename
  control (writing the real mapping outside the session-visible directory) and `--orient` for
  rotation handling. Verified on real B1 footage.
- WSL2 development environment provisioned without sudo: static ffmpeg 7.0.2, uv 0.11.32 and
  the claude CLI installed to `~/.local/bin`, PATH persisted, both bins copied to ext4.
- **R7 dataset integrity controls**: exclude pre-curated clips, and neutralize filenames before
  any policy session sees the footage.
- Draft quality rubric (research/R6-rubric.md): 8 criteria with anchored 1/3/5 descriptions,
  scoped to rough cuts, plus a provisional pass bar pending calibration against real edits.
- Labeling guide (benchmarks/LABELING.md): the highlights pass needed to reach CP2, aimed at
  the failure modes the studies measure (audio-only moments, sub-5s highlights, watching past
  the boring stretches rather than skipping to remembered ones).
- **Start-here sequencing in docs/TASKS.md**: run R7 *before* building the pipeline. R7 needs
  only ffmpeg, a contact-sheet script, footage and labels — so the thesis go/no-go (CP2) is
  reachable in a fraction of the work T0–T6 would cost, and the scaffold gets built on a
  validated premise.
- **Inference abstraction with two backends** (SPEC §6, task T0c): a single `Backend` protocol
  over a Claude Code CLI implementation (Claude Max subscription — the development default,
  zero marginal cost) and an Anthropic API implementation (per-token, Batch-capable — the
  production path), selected by `ROUGHCUT_BACKEND`. The interface is a deliberate lowest common
  denominator: images passed by path rather than base64, and schema conformance defined as a
  required outcome rather than a required mechanism, so the CLI backend can satisfy it by
  prompt-and-validate while the API backend uses native structured outputs. A contract
  conformance suite runs against both backends.
- Cost ledger records `backend`, tokens, `projected_usd` (API-rate equivalent) and nullable
  `actual_usd`; **budget caps are enforced on `projected_usd` on both backends**, so developing
  on a subscription doesn't lose the ability to answer whether the pipeline is affordable in
  production. The CLI backend adds a per-run call ceiling, since a subscription's scarce
  resource is requests-per-window rather than dollars.
- Four review checkpoints (CP1 after ingest, CP2 after R7 as an explicit thesis go/no-go,
  CP3 after first skeleton, CP4 after first rendered cut), with defined agent stop-and-report
  behavior in CLAUDE.md.
- Research study R7 (analysis policy bake-off): tests uniform per-shot sampling against a
  coarse-to-fine adaptive search (contact sheets + recursive deep-dive, prototyped via Claude
  Code CLI sessions) and a hybrid; includes a conditional model-landscape arm. Sequenced
  before R1.
- Agent operating manual (CLAUDE.md): closed-loop session protocol — pick task → implement →
  run DoD verification → iterate until green → commit with changelog — plus commit discipline,
  research-before-assumption rule, and inference/cost guardrails.
- Future-phase context tracker (docs/FUTURE_PHASES.md), benchmark registration guide,
  research studies R1–R6, task board T0–T13 with per-task Definitions of Done, the Phase 1
  technical specification, and the repository scaffold.

### Changed
- **Linux-first, container-ready** (SPEC §9): development in WSL2/Linux, production target a
  Linux box. Portability rules (no absolute media paths, one subprocess wrapper, pathlib only,
  env-var config, pinned ffmpeg) plus task T0b enforcing them via a Dockerfile and an
  in-container test run.
- **Design scale reduced to 3h typical / 5h max** (was 20h). Consequences: budget cap $15
  default / $40 hard fail (was $50/$100); cheap-signal **gating disabled by default** —
  analyzing everything costs less than the recall risk is worth; T5 rescoped from "signals +
  gate" to "signals"; study R4 deferred with its protocol preserved.
- **Model IDs are configuration, not architecture**: choices resolve through named roles in
  `config.py` from env vars; no model ID literal outside that file, and no model-version
  attribution recorded in commits or docs.
- Research studies R1 and R7 budget and report in **tokens**, with dollars as a projection.
  R7 gains a control for dev-backend bias: policy A needs ~20× the calls of the coarse
  policies, painful on a subscription but a non-issue on the API's Batch endpoint, so policies
  are ranked on tokens and recall with call count reported separately.
- Benchmark bins registered (D1): B1 = Killington 01-2026, B2 = Copper 02-2026, B3 = Mt. Marcy
  02-2025 held out. GoPro `.LRV` files may serve as free proxies (to check at T2).

### Fixed
- **Caught misleading rotation metadata in B1 before any pipeline code existed.**
  `GX010474.MP4` carries `rotation=-90`; honouring it (ffmpeg's default) renders a sideways
  portrait frame, while `-noautorotate` gives the correct upright 16:9 — confirmed visually.
  `GX010475.MP4` carries none, so the bin is mixed *and* wrong. Added `rotation` and
  `orient_override` to the media schema, a verify-by-looking requirement at ingest (SPEC §3 S0),
  and orientation assertions to the T1/T2 DoDs against the real clip. A blanket
  `-noautorotate` is explicitly rejected — portrait phone footage must keep working.
- **B1 is now Copper 02-2026** (raw, 11 min), with Killington demoted to B2 (43 min but
  pre-curated). Raw beats large: highlight-finding measured against pre-culled footage flatters
  the result at exactly the checkpoint meant to be honest.
- **Corrected the B2 `.WAV` inference.** ffprobe shows `pcm_s32le` 48kHz **4-channel** 32-bit
  audio with duration matching the paired MP4 — GoPro's raw mic-array capture, same camera and
  clock, not an external mic. External-audio sync returns to unscheduled; the raw array is
  refiled under P2.2 audio post as a wind-noise-reduction opportunity.
- **Measured real bin durations** (previously estimated from file size): Killington 43 min,
  Copper 11 min, Mt. Marcy 8 min — ~1 hour total, well under the 3h/5h design target. Formats:
  B1 is H.264 4K 29.97fps, B2/B3 are HEVC 5.3K 23.976fps.
- **Flagged B1 as pre-curated** — its files are hand-named after their content
  (`bombbeginning`, `tastytrees`, …), so a human already removed the boring material. Measuring
  highlight-finding on it would have produced a falsely positive CP2. Mitigated by restricting
  R7 to the nine long-form clips and neutralizing filenames; raw unculled footage would be
  strictly better if any is available.
