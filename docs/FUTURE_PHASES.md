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
