# R5 — Frame-Accuracy Verification Harness

**Question:** How do we *prove* an FFmpeg-rendered timeline is frame-accurate and in sync,
automatically, in CI-style tests? (This study produces a reusable tool, not a report of
comparisons.)

**Replaces the assumption:** "FFmpeg trims are frame-accurate if you use the right flags" —
seek-mode, keyframe, and concat behaviors make this genuinely easy to get silently wrong.

## Method / build plan
1. **Synthetic source generator** (`tests/harness/gen.py`): ffmpeg-generated clips with
   (a) per-frame frame-number burn-in (drawtext with `%{n}`), (b) unique color per second,
   (c) audio: 1kHz tone with a 1-frame click + white flash every 2s (A/V alignment beacons).
   Variants: 23.976 / 25 / 29.97 / 60 fps, and one VFR variant.
2. **Verifier** (`tests/harness/verify.py`):
   - Decode rendered output frames at cut boundaries; OCR-free readout of the burned-in frame
     number (render digits as a binary bar code strip, not text — trivially machine-readable,
     no OCR dependency).
   - Assert first/last frame of each timeline clip equals the OTIO in/out point exactly.
   - A/V sync: detect click sample position vs flash frame position; report drift in ms.
3. **Calibrate:** run the verifier against known-bad renders (off-by-one trim, keyframe-seek
   copy mode) to prove it catches them (negative tests).
4. Package as `tests/harness/` importable by T10's test suite.

## Definition of Done
- [ ] Generator + verifier exist with their own tests; negative tests prove detection of
      off-by-one and sync-drift errors
- [ ] Documented usage in the harness module docstring; T10 DoD references it
- [ ] A short `R5-report.md` noting any FFmpeg flag findings (seek modes, concat demuxer vs
      filtergraph) that constrain T10's implementation, with RQ-5 marked resolved
