# Future Phases — Context Tracker

Deferred scope with the *why* and the hooks Phase 1 leaves for it. **This file is a living
document:** whenever Phase 1 work discovers deferred scope or a design decision that
constrains a future phase, record it here in the same commit.

## Phase 2 — Quality layer

### P2.1 Music selection & beat-aligned cutting
- Why deferred: rough cut must prove selection quality first; music multiplies scope (licensing).
- Design hooks left in P1: OTIO `A2` track reserved by convention; shots carry `good_cut_in/out`
  flags usable for beat snapping; assembly is a pure timeline transform, so a "snap cuts to
  beat grid" pass is a timeline→timeline function.
- Open decision: user-provided files vs music-library API (product/licensing question).
- Tech notes: librosa beat/tempo extraction; cut-to-beat = quantize cut times to nearest beat
  within a tolerance, preferring shot `good_cut_*` boundaries.

### P2.2 Full audio post
Dialogue leveling per segment, noise reduction (wind!), music ducking under speech (sidechain
compression via ffmpeg `sidechaincompress`), room-tone fill. P1 already flags unusable audio
(tier-0), which becomes the trigger map. Biggest perceived-quality lever per effort — schedule
early in Phase 2.

**Raw mic-array audio is available on GoPro bins:** paired `.WAV` files carry 4-channel 32-bit
PCM from the camera's mic array (confirmed on B2). Wind is the dominant audio problem in ski
footage, and beamforming or array-based noise reduction from the raw channels should beat
post-hoc filtering of the baked stereo AAC track. Worth a study before committing.

### P2.3 Color correction
Log→Rec.709 conversion using `media.color_transfer`/`log_profile` from the P1 probe (the
metadata is captured now precisely so this phase doesn't re-ingest). LUT application via
ffmpeg `lut3d`. Shot-to-shot matching starts as histogram matching; real color science is
explicitly out of scope until proven necessary.

### P2.4 Captions & titles
Burned-in subtitles straight from `transcript_segments` (libass); lower thirds from
`entities`; intro/outro cards from the brief. Cheap because P1 built the transcript with word
timestamps.

### P2.5 Revision loop (the product-defining feature)
NL notes ("tighten the intro, lose the drone shot") → timeline operations → re-render draft.
Requirements accumulated from P1:
- Timeline lineage metadata (SPEC §4) exists for exactly this — every revision is a child timeline.
- Needs a draft-render mode (fast, proxy-based, watermarked) — assembly already renders from
  either source set, so this is a flag.
- Needs timeline diffing (OTIO→OTIO structural diff) for explainable changes.
- The agent tools built for S3 (index queries) are reused; add timeline-edit tools.

**Karl, 2026-07-25, after watching the first two cuts — this is what makes the product
compelling, and the bar is higher than "a revision API exists":**

> *"For the editor to be truly compelling (final product), it should provide an easy to use
> interface for the human to interject / ask for edits / set the scene and story… What you will
> need to do when you go to app is make that fine tuning **fun and easy**."*

Three requirements fall out, all of which outrank raw selection quality once selection is
merely decent:

1. **Interjection, not just review.** The human sets scene and story *before and during* the
   cut, rather than only reacting to a finished artifact. The agent found the milk thread in B1
   by accident of thorough review; a human who could have typed "the milk is the running joke"
   would have got there in five seconds. Cheapest highest-leverage input in the whole system —
   same argument as the brief itself in the human-in-the-loop map.
2. **Fine-tuning must be enjoyable.** Nudging a cut point, swapping a take, extending a beat —
   these have to be immediate and reversible, not a re-run of the pipeline. Implies a
   persistent timeline the UI mutates and a fast local preview, not a batch render per tweak.
3. **Story scaffolding is a first-class input.** The gap the agent could not close on its own
   was *connecting the dots* — B1's cut is a sequence of good moments, not a story. That is
   where human input goes, and the UI should ask for it explicitly rather than hoping the brief
   carries it.

### P2.6 Music-driven cutting as an explicit *mode* — Karl, 2026-07-25
> *"…along with the ability for the human to put in an audio track, which can then be used to
> cut between the scenes — this is **not the MAIN mode**, but is a mode of editing that should
> be available to the human."*

Distinct from P2.1 (which frames music as a quality layer over an existing cut). Here the
supplied track becomes the **cutting grid**: the human drops in a song and the edit is built to
its beats and sections, with selection choosing what fills each slot. The "not the main mode"
constraint is a design instruction — it must not distort the default dialogue-and-reaction-driven
path, so it belongs as an alternative assembly strategy over the same selection output, not a
fork of the pipeline.

Notes carried over: beat/tempo extraction (librosa) and quantising cuts to the beat grid are
already sketched in P2.1; what is new is that segment *durations* become slot-driven, which
changes selection's contract from "pick the good bits" to "fill these N slots of these lengths".

### P2.7 Effects — natural language to pixels — Karl, 2026-07-25

> *"How effects in general will be added to footage… Call of Duty hitmarkers on the video frames
> with the sound effect too when my skis hit a bunch of rocks, instructed via natural language."*

Design in [docs/EFFECTS.md](EFFECTS.md). The four rules that make it tractable: the model never
writes ffmpeg (closed vocabulary, validated parameters, renderer owns every string); effects are
resolution-independent so a proxy preview matches the master; assets come from a local library
with a manifest the model reads; and frame accuracy comes from narrowing — visual pass (4s) →
**onset track (100ms, already on disk since R8)** → a frame strip → the human's nudge.

Status: the music bed is built (film-wide render path, ducking). `sfx` and `overlay` with the
asset manifest are next, then onset snapping, then the board's timeline markers, then the Ask
path that turns a sentence into effect objects.

## Phase 3 — Breadth & polish

- **P3.1 External audio sync — remains unscheduled.** *(Corrected 2026-07-25.)* An earlier note
  promoted this after seeing 26 `.WAV` files paired with B2's MP4s and inferring an external
  mic. ffprobe says otherwise: `pcm_s32le`, 48kHz, **4 channels**, 32-bit, duration matching the
  paired MP4 exactly — GoPro's raw mic-array capture, same camera and same clock. There is no
  sync problem, so this goes back to unscheduled. What the WAVs *do* offer is raw multi-mic data
  for wind-noise reduction and beamforming, which is filed under P2.2 audio post instead.
- **P3.2 Vertical auto-reframe (9:16)** — subject tracking + crop path. The VLM pass could
  cheaply emit a coarse subject-position hint per keyframe; consider adding that field to the
  §5.4 schema *in Phase 1* if R1 shows marginal cost is ~zero. ← flagged during spec writing.
- **P3.3 Skeleton web UI** — visual board (keyframes, transcript, drag-to-outline) replacing
  chat-only S3. Keep S3's agent tools UI-agnostic so the web UI drives the same functions.
- **P3.4 NLE export** — OTIO → Premiere/Resolve interchange for hand-finishing. OTIO-as-spine
  makes this an exporter, not a rewrite. Known risk: OTIO→Premiere fidelity varies; budget a
  research study (R7) if/when this is scheduled.
- **P3.5 Transitions & motion library** — curated parameterized transitions/speed ramps the
  agent selects from; explicitly *not* freeform generated animation.
- **P3.6 Speed ramps / high-fps slow-mo** — probe already records true fps; conform decisions
  become clip metadata.

## Parking lot (unscheduled ideas)
- Re-enable cheap-signal gating (R4) if 20h-scale bins ever appear; deferred at 3–5h scale.
- Tier-1.5 cheap-LLM transcript scorer, if gating is ever revived and drops verbal highlights.
- Thumbnail generation from top-interestingness frames.
- Multi-video projects sharing one index (season/trip archive).
