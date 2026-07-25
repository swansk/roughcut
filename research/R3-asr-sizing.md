# R3 — ASR Model Sizing

**Question:** Which faster-whisper model size (tiny/base/small/medium/large-v3, plus
distil-large-v3) gives acceptable WER on real footage audio (wind, distance, crosstalk) at
acceptable runtime on Karl's hardware — and what WER target should T4 assert?

**Replaces the assumption:** provisional WER ≤ 15% and "whisper runs fine locally".

## Method
1. **Reference:** 10 minutes of B1 audio hand-corrected to a reference transcript (mix of
   clean speech, outdoor/wind, and background-music segments). Depends on **D3** (GPU y/n)
   for which compute types to test (float16 GPU vs int8 CPU).
2. **Run** each model size over the reference audio; compute WER (jiwer, standard
   normalization) overall and per audio-condition class; record wall-clock and RTF
   (real-time factor) per model on the actual dev machine.
3. Also measure word-timestamp drift on 20 sampled words (needed for caption/cut alignment
   later) — median abs error in ms.
4. **Decision rule:** smallest model within 2 WER points of the best, with RTF ≤ 0.5 (20h of
   audio must transcribe in ≤ 10h unattended, ideally overnight).

## Definition of Done
- [ ] WER/RTF/timestamp-drift table per model in `R3-report.md`
- [ ] Chosen model + compute type recorded in SPEC (RQ-3); T4 provisional WER target replaced
- [ ] 20h-bin projected transcription wall-clock documented
