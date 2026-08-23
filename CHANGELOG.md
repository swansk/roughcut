# Changelog

All notable changes to Roughcut are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [SemVer](https://semver.org/)
once code exists; spec-phase entries go under [Unreleased].

Every commit that changes behavior or documentation adds an entry under [Unreleased], in the
same commit. Releases move entries into a dated version section.

## [Unreleased]

### Added
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

### Fixed
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
  frame is **0.88 s / 0.92 s**. Trimming an in-point moves the poster too, debounced 450 ms, so
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
