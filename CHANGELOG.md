# Changelog

All notable changes to Roughcut are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [SemVer](https://semver.org/)
once code exists; spec-phase entries go under [Unreleased].

Every commit that changes behavior or documentation adds an entry under [Unreleased], in the
same commit. Releases move entries into a dated version section.

## [Unreleased]

### Added
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
