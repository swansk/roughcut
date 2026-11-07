# Footage understanding, hybrid local + Claude — VLM research (2026-11-07)

Research only: nothing downloaded, installed, or run. Numbers marked *est.* are my arithmetic, not measurements; the bake-off has to replace them.

## Verdict (plain language)

1. **Telemetry and sound decide *when* to look; the local model decides *what* is there; Claude only checks moments the edit is about to use.** R11 already shows the accelerometer pins airs to 0.1 s and finds falls the eyes missed. No VLM paper I found beats that for 0.25–0.4 s events, and MotionBench (CVPR 2025) reports that VLMs are poor at fine-grained motion, with most models below 60%. So the VLM should never be the only witness to an air.
2. **Local layer 1, a dense pass at 2 fps over everything, on the 3080:** **Qwen3.5-4B** (Apache-2.0, released ~Mar 2026), or **Qwen3-VL-8B-Instruct** in 4-bit, through vLLM. Feed it 30 s chunks at ~448×252, horizon-levelled. It outputs timestamped event lines (`<t0–t1> what`) plus a one-line scene caption every ~4 s. Both families put text timestamps on every frame group (Qwen3-VL: "text-timestamp alignment", e.g. `<3.0 seconds>`).
3. **Local layer 2, a close read at 8–10 fps, only inside triggered windows:** these are windows of about ±1.5 s around every telemetry candidate (|a| < 0.5 g for ≥ 0.25 s, impact > 5 g, a speed drop of ≥ 3 m/s in 3 s) and every audio or motion z-peak from R10. Ask yes/no questions ("is the wearer airborne in frames X–Y?") and score them with first-token probabilities, not free text. A 2026 paper (arXiv 2608.08315) shows that asking a VLM to type timestamps is "confidently wrong", while a coarse-to-fine yes/no scan adds 28–50 R@0.5 points on Charades-STA. **Why the dense pass cannot see airs:** Qwen-family encoders merge each pair of frames into one temporal token (temporal patch = 2), so at 2 fps one token covers ~1 s. A 0.3 s air is mostly blur averaged away. The close read needs ≥ 8 fps.
4. **The planner still reads text**, but better text: dense captions, verified events with confidence, telemetry facts ("airborne 0.32 s, landed 3.1 g, 11 m/s"), sound tags, and speaker turns. Caption-then-reason is the right shape for a 14k-token planner. VideoThinker (Jan 2026) finds caption-only agents have a ceiling (+3.5–3.9% from also giving the reasoner frames). That is exactly what the Claude check below supplies.
5. **Claude in Hybrid mode checks, it does not scan.** After the planner picks its shots, Claude (via `claude -p` with images) gets **one levelled 8 fps strip** (2–3 s, 480 px, ~16–24 frames on one sheet) for each picked moment whose claim is an event (air, fall, crash, reaction), plus the local model's claim and the telemetry numbers. It answers confirm / reject / retime. That is ~20–40 sheets per edit, not the whole bin. R10's cost fact holds: density is cheap and coverage is expensive. **Claude only** = today's pipeline plus the check. **Local only** = no check; the planner sees `unverified`.
6. **Routing rule (cascade):** send to Claude when (a) the moment is in the cut, AND (b) it is an event claim, AND (c) local yes-probability is between 0.2 and 0.9 *or* local and telemetry disagree. When both agree strongly, do not send it, unless it is the hero shot (top 3 of the edit), which always goes. This is the FrugalGPT/AutoMix pattern: a cheap model, a scorer, then escalation. FrugalGPT reports up to 98% cost cut at matched accuracy. The scorer is the hard part, which is why telemetry agreement is part of it.
7. **Fallback** if Qwen3.5-4B and Qwen3-VL-8B both disappoint on the events: drop the VLM from event detection completely. Keep it for scene captions plus Find (embeddings), let telemetry + audio be the event detector, and give Claude every triggered window that the edit uses. The second fallback model is **TimeLens-8B** (a grounding-tuned Qwen3-VL-8B) for Find-style "when does X happen" queries.

## Local VLMs to bake off (pick 2–3)

| model | HF repo | VRAM on 12 GB *est.* | throughput *est.* | licence | why |
|---|---|---|---|---|---|
| **Qwen3.5-4B** | `Qwen/Qwen3.5-4B` (community AWQ/GGUF; or BF16 ≈ 9 GB) | 4-bit ≈ 3–4 GB weights + KV; BF16 fits tight | dense pass ≈ 6–12 GPU-min per footage-hour | Apache-2.0 | newest small Qwen. Model card: VideoMME 76.9 (no subs), MVBench 71.2, LVBench 66.4, beating Qwen3-VL-30B-A3B. Hybrid linear attention keeps long video cheap. vLLM needs a nightly build |
| **Qwen3-VL-8B-Instruct** | `Qwen/Qwen3-VL-8B-Instruct` (AWQ via community quants; GGUF Q4_K_M/Q8 in llama.cpp since 2025-10-30) | 4-bit ≈ 6 GB + KV; Q8 ≈ 10.3 GB (too tight with video KV) | ≈ 10–20 GPU-min per footage-hour | Apache-2.0 | proven text timestamps. Zero-shot Charades-STA R@0.5 ≈ 49.8 (Instruct) / 57.9 (Thinking), per the TaRO paper. The base model of TimeLens-8B, so the same stack serves Find |
| **Marlin-2B** (wildcard) | `nemostation/marlin-2b` (gated) | ≈ 5 GB BF16 | fastest | **unclear**: card says Apache-2.0, a mirror says BSL-1.1. Check before shipping | Qwen3.5-2B tuned for dense captions plus `find()` timestamps. Claims +6.4 mIoU over Qwen2.5-VL-7B on TimeLens-Bench. Self-reported; 240-frame cap; "cannot say an event is absent" |

Not first-choice, and why:
- **Gemma 4 (E4B/26B-A4B):** video is reported as limited to ~60 s at 1 fps. Too sparse.
- **MiniCPM-V 4.5:** its 3D-resampler packs 6 frames into 64 tokens, good for "high-FPS (up to 10 fps)". I found no grounding numbers. A reasonable third candidate for the 8–10 fps close read if Qwen merges too hard.
- **Molmo2 4B/8B (Ai2, Dec 2025):** strong at points, tracks and counting, not at event timing. Licence not verified.
- **VideoChat-Flash / MiMo-VL-7B:** Charades mIoU 45.2 / 50.0 as reported by their own papers, re-scored lower on TimeLens-Bench. Superseded by the Qwen3 family.
- **VideoChat3-4B (Jul 2026):** I could not confirm released weights.
- **InternVL3.5, Eagle/NVILA, Phi-4-MM, SmolVLM2, Florence-2, Apollo, LLaVA-Video:** no evidence they beat Qwen3/3.5 at this size on temporal tasks. Not worth bake-off slots.
- **Benchmark caveat:** TimeLens found 20.6% of Charades-STA queries non-unique and 34.9% badly annotated, so legacy grounding scores are noisy. None of these models has been measured on egocentric ski footage; the Killington set is the only test that counts.

## Other local layers (exact models)

- **Embeddings for Find / retrieval:** `facebook/PE-Core-L14-336` (Meta Perception Encoder, Apache-2.0, ~0.6B; K400 zero-shot 73.4; trained on recaptioned video). Alternative: `google/siglip2-so400m-patch14-384` (Apache-2.0). Embed 2 fps frames → FAISS → top-k windows → the VLM yes/no rerank → hit@3. Both are a few hundred MB; frame embedding is seconds per minute of footage. Skip DETR-style moment retrieval (QD-DETR/CG-DETR/UniVTG): they are trained on QVHighlights vlogs with CLIP+SlowFast features, and the VLM yes/no scan replaces them.
- **Audio tags:** EfficientAT `dymn10_as` (AudioSet mAP 0.478, ~10M params, CPU-cheap) for wind/carving/crash/cheer/laughter/scream tags at 1 Hz. Upgrade: CED (10M, mAP 49.0) or BEATs (50.6; heavier). CLAP is only needed if you want free-text sound queries.
- **Speech:** keep Whisper large-v3. **Diarization:** `pyannote/speaker-diarization-community-1` (pyannote.audio 4.0, CC-BY-4.0). Its "exclusive" mode aligns speakers to transcript words; the claimed gains over 3.1 are in speaker counting and assignment. Wind noise on helmet mics is the risk; use the camera's own MWET/WNDM flags to mask it.
- **Telemetry fusion (already parsed):** turn R11 into first-class events, not ranking nudges:
  - air = |a| < 0.5 g for ≥ 0.25 s, followed by an impact;
  - crash = > 5 g + a speed drop of ≥ 3 m/s;
  - stop and lift = GPS;
  - camera-handling = roll/gyro spikes with low g (R11: "handling shows as roll, not g").

  Each event gets a timestamp, so the VLM and Claude are asked "confirm this", never "find something". No 2025 paper I found does IMU+video fusion for sports highlights, so this layer is our own design, grounded in R11.
- **Horizon levelling:** run the Gyroflow CLI, batch with `--preset` and `-p "{'output_width':…}"`, on the 720p proxies, using GRAV/CORI (IORI was identity, so levelling was off in-camera). Horizon lock needs an accelerometer-aware integrator (Complementary is the most drift-free, per its docs). I did not confirm a horizon-lock preset key in the CLI docs; test it. Cheaper alternative: counter-rotate each sampled frame by GRAV roll in ffmpeg/PIL before tiling. Evidence that this helps: a COLM 2026 paper finds rotation is the hardest transform for VLMs, and Qwen2-VL-7B drops 78→72% on R-Bench at 90°. R10's "inverted person" claims were all camera roll. Expect the biggest single precision win from this. Do not crop away the sky, which is the air cue.
- **Motion blur and resolution:** at 59.94 fps a GoPro frame is mostly sharp in daylight. Choose frames by sharpness (Laplacian variance) within ±2 frames of each sample, not blindly. 448 px on the long side is the Marlin/Qwen default and enough to tell skis-in-sky from snow. Claude's check strip at 480 px matches what R10 used.

## Risks / unknowns

- Small VLMs may simply not see 0.3 s airs, even at 8 fps; MotionBench says fine motion is weak. That is why telemetry leads.
- The 12 GB budget is KV-cache bound: a 30 s chunk at 2 fps × 448 px ≈ 30 groups × ~100–200 tokens ≈ 3–6k tokens *est.* That fits. A 120 s chunk may not.
- Qwen3.5 needs vLLM nightly; quant quality of community AWQ builds varies; the 6-core CPU is shared, so decode on the GPU (NVDEC) and pre-extract frames once.
- The Marlin licence conflicts between sources; Molmo2 and VideoChat3 weights/licences are unverified.
- Gyroflow's CLI horizon-lock control is unverified.
- No published throughput for these models on a 3080; every speed number above is an estimate.
- Claude subscription quota (not dollars) is the real cap on Hybrid. Log sheets per edit.

## Bake-off on the ~25-moment Killington set — measure

Label ~25 moments by eye at 0.5 s: the 6 confirmed airs, the 2 camera-felt falls (CLIP_04 271, CLIP_11 144), the R10 false-claim windows, a few reactions/talking, plus ~10 empty control windows. Then for each model × {levelled, raw} × {2 fps dense, 8 fps close read}:

1. **Event recall** within ±0.5 s of the labelled start (and the same with telemetry candidates as the window source).
2. **False event rate** per footage-minute, and specifically "inverted/flip" claims on the levelled vs raw input.
3. **Find hit@3** on ~15 text queries ("the big air", "someone falls", "laughing on the lift"), PE-Core vs SigLIP 2, each with and without the VLM yes/no rerank.
4. **GPU minutes per footage-hour** (dense pass and close read separately), plus peak VRAM.
5. **Calibration:** does the yes-probability separate true from false events (AUROC)? This sets the Claude routing band.
6. **Claude agreement:** on the routed moments, how often does Claude overturn the local claim, and is Claude right (eye-checked)? Also count sheets per edit.

## Sources

- Qwen3.5-9B/4B model card (Apache-2.0, video benchmarks, vLLM): https://huggingface.co/Qwen/Qwen3.5-9B
- Qwen3-VL-8B-Instruct card: https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct · Qwen3-VL tech report (text timestamps): https://arxiv.org/abs/2511.21631
- Qwen3-VL GGUF/llama.cpp: https://unsloth.ai/docs/models/qwen3-vl-how-to-run-and-fine-tune
- TaRO (Qwen3-VL-8B zero-shot Charades-STA): https://arxiv.org/pdf/2606.09248
- TimeLens paper + TimeLens-8B: https://arxiv.org/abs/2512.14698 · https://huggingface.co/TencentARC/TimeLens-8B
- Yes/no temporal grounding (FV-Action): https://arxiv.org/abs/2608.08315
- Marlin-2B: https://huggingface.co/nemostation/marlin-2b · licence note: https://huggingface.co/lunahr/Marlin-2B-ungated · https://huggingface.co/datasets/uv-scripts/video
- Molmo2: https://www.businesswire.com/news/home/20251216910167/en · https://alphaxiv.org/overview/2601.10611v1
- MiniCPM-V 4.5: https://huggingface.co/openbmb/MiniCPM-V-4_5-int4
- Gemma 4: https://huggingface.co/blog/gemma4 · https://datature.io/blog/gemma-4-what-computer-vision-engineers-actually-need-to-know
- VideoChat3: https://arxiv.org/abs/2607.14935
- MotionBench: https://arxiv.org/abs/2501.02955
- Frame selection: BOLT https://arxiv.org/html/2503.21483v1 · Q-Frame https://arxiv.org/html/2506.22139v2 · F2C https://arxiv.org/pdf/2510.02262 · AdaRD-Key https://arxiv.org/html/2510.02778v1 · EFS https://arxiv.org/pdf/2603.00983
- Caption agents vs end-to-end: VideoThinker https://arxiv.org/html/2601.15724v1 · VideoAgent2 https://arxiv.org/html/2504.04471v1
- Cascades: FrugalGPT https://arxiv.org/pdf/2305.05176 · TREACLE/AutoMix discussion https://arxiv.org/pdf/2404.13082v2.pdf
- PE-Core: https://huggingface.co/facebook/PE-Core-L14-336 · SigLIP 2: https://arxiv.org/pdf/2502.14786
- EfficientAT: https://github.com/fschmid56/efficientat · CED: https://arxiv.org/abs/2308.11957
- pyannote community-1: https://pyannote.ai/blog/community-1
- Egocentric fast motion: https://arxiv.org/abs/2601.07154 · video+IMU MAE: https://arxiv.org/abs/2407.06628
- Gyroflow CLI: https://docs.gyroflow.xyz/app/advanced-usage/command-line-cli · settings: https://old-docs.gyroflow.xyz/guide/settings/
- VLM rotation fragility: https://arxiv.org/abs/2604.01848 · https://arxiv.org/pdf/2605.05045v2
