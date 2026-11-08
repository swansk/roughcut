# Local-GPU bake-off: footage understanding (Killington bin) — 2026-11-07

Status: setup + CLIP_06/CLIP_12 measurements DONE; Find + audio tags + telemetry-triggered CHECK matrix (all 12 clips) DONE;
DENSE matrix over the labelled chunks of the other 10 clips is still RUNNING detached (see "How to read" at the end).
Scored against Karl's `killington-moments.checked.json` / `killington-find.checked.json` (camera-toss, water, neg-11-256 as per the lead's note; "karl_ok" = rows he confirmed).

## What is installed (all in ~/roughcut-loop/understand/bench, venv `.venv`, uv; HF cache ~/roughcut-loop/understand/hf; quantised models ~/roughcut-loop/understand/models)
- Python 3.12.3, torch 2.11.0+cu128, torchvision 0.26.0, torchaudio 2.11.0, transformers 5.22.2, bitsandbytes 0.50.2, accelerate 1.15.0, open_clip_torch 4.2.1, timm 1.0.31, PyNvVideoCodec 2.2.4 (NVDEC), flash-linear-attention 0.6.0, opencv-headless 5.0.0.93, numpy 2.5.4. No vLLM (see problems).
- Qwen3-VL-8B-Instruct: official `Qwen/Qwen3-VL-8B-Instruct` bf16 quantised once with bitsandbytes nf4 (double-quant, bf16 compute, vision tower + lm_head left in bf16) and saved as `models/qwen3vl-8b-bnb4` (bf16 copy deleted). key `q3vl8b`.
- Qwen3.5-4B: official `Qwen/Qwen3.5-4B`, same recipe -> `models/qwen35-4b-bnb4` (3.1 GB). key `q35_4b`. Run in transformers (not vLLM), thinking disabled.
- Find: `timm/PE-Core-L-14-336` via open_clip (= facebook PE-Core-L14-336), `google/siglip2-so400m-patch14-384`, fp16. Frames squashed to square (no centre-crop).
- Audio: EfficientAT (github fschmid56/EfficientAT, cloned into bench/EfficientAT) `dymn10_as`, 1 s windows/1 s hop, AudioSet labels.
- Disk: ~25 GB mine in total (venv 7.5, models 11, hf 7.2, frames ~2.5 GB for 12 clips x 3 caches).

## Code (bench/)
- `telemetry_lib.py` imports the repo's `research/tools/telemetry.py` read-only by path; caches ACCL |a|, GRAV roll, GPS speed; `candidates(clip)` = R11 rules (|a|<0.5 g >=0.25 s = air; >5 g impact; speed drop >=3 m/s in 3 s; GPS stop 4->1 m/s).
- `frames.py`: ONE NVDEC pass per clip (4K h264 decode ~140 fps on the 3080; ffmpeg here has no cuda hwaccel, CPU decode ran ~1.2x realtime under load), writes a 10 fps grid at 448 px: `raw` and `sharp` (sharpest Laplacian variance of the +-2 source frames); `get(clip,fps,width,levelled,sharp)` returns frames, levelled ones are counter-rotated by the GRAV roll and cached. Cache key frames/<clip>/w<width>/<raw|sharp>[_lev].
- `vlm.py`: DENSE (30 s chunks, 2 fps, video input with per-frame text timestamps, JSON-lines events + a caption every ~4 s) and CHECK (+-1.5 s at 10 fps, P(yes) from first-token logits Yes vs No, plus a coarse-to-fine yes/no scan: 1.0 s sub-windows step 0.5 s, then 0.5 s sub-windows step 0.1 s, peak = timing).
- `find.py` / `find_run.py`: embeddings at 2 fps, text->frame cosine, 2 s smoothed windows, NMS 3 s, top-k. `audio.py`, `score.py` (README rules), `run_matrix.py`, `run_all.sh`.

## Two bugs worth knowing (both found by looking at frames)
1. GoPro clips CLIP_04..12 carry a display-matrix rotation of -180 (inverted mount). NVDEC/PyNvVideoCodec ignores it, so frames come out upside down; `frames.py` reads the side data and flips. (ffmpeg autorotates, so anything built on ffmpeg is fine.)
2. Levelling sign: image must be rotated by +roll for upright-mount clips (CLIP_01-03) and -roll for inverted-mount clips (the rest); i.e. rotate by `base * roll`. Verified by eye on CLIP_12 and CLIP_01 (out/sidebyside/*.png). Roll is interpolated circularly and set to 0 where gravity is near the optical axis. Black corners are left (no crop, sky kept).
Side-by-sides (raw | levelled | sharpest-of-+-2): out/sidebyside/CLIP_12_46.8.png, CLIP_12_50.7, CLIP_12_63.5, CLIP_06_204, CLIP_06_30.3, CLIP_01_104, CLIP_07_16, CLIP_01_signcheck. Typical roll on the helmet clips is small (CLIP_12/06: <1 % of time > 45 deg), so levelling matters mostly on CLIP_01 and the camera-handled/fall moments.

## VRAM and speed (RTX 3080 shared box, bnb nf4, transformers, no flash-attn; wall-clock between CUDA syncs, GPU was otherwise idle but CPU load was 10-24)
| model / mode | peak VRAM (alloc / reserved) | GPU s per footage-minute | notes |
|---|---|---|---|
| Qwen3-VL-8B DENSE 30 s @2 fps | 7.7 / 8.8 GB | 50-66 (CLIP_06 raw 64, lev 59; CLIP_12 raw 66, lev 50) | ~30 s per 30 s chunk, ~3.9k prompt tokens, decode-bound (bnb nf4 ~10 tok/s) |
| Qwen3.5-4B DENSE | 4.1 / 5.3 GB | 53-84 (CLIP_06 raw 63, lev 53; CLIP_12 raw 84, lev 65) | first call 45 s of Triton warm-up; `causal_conv1d` kernel could not be built (no nvcc) so the linear-attention conv falls back to PyTorch |
| Qwen3-VL-8B CHECK (3 s @10 fps, P(yes) + scan only when P>=0.15) | 7.3 / 8.1 GB | 10.9 raw / 11.9 lev over all 12 clips (272 items = 0.9-1.2 s each) | |
| Qwen3.5-4B CHECK | 4.4 / 5.4 GB | 6.3 raw / 5.7 lev (0.63 s per item) | |
| PE-Core-L14-336 embed 2 fps, 43 min footage | 1.8 GB | 78-88 s total (~2 s per footage-minute) | frames already cached |
| SigLIP2 so400m embed | 3.0 GB | 116-131 s total | |
| EfficientAT dymn10_as | <0.5 GB | negligible (dominated by ffmpeg audio decode) | |
| frame extraction (NVDEC + JPEG) | ~1 GB | 12 clips took ~20 min wall under load, mostly JPEG writes | once per clip |
All under the 11 GB cap. DENSE is far slower than the research doc's estimate (6-20 GPU-min per footage-hour): measured 50-84 GPU-s per footage-minute, i.e. about one GPU-hour per footage-hour. CHECK on telemetry candidates is ~6-12 GPU-s per footage-minute (6-12 GPU-min per footage-hour, including the scan).

## Qualitative: CLIP_12 airs at ~46.6 s and ~50.6 s, CLIP_06 "crash" ~204 s, CLIP_06 air 30.2 s
- No model/mode ever produced an `air` event in DENSE, raw or levelled (all 4 combos, CLIP_06 + CLIP_12): zero air/fall claims except a Qwen3-VL levelled `fall` at CLIP_12 63.0 (the lead-in to the wearer's ~63 s handling, not labelled).
- CHECK P(yes) for "is the wearer airborne": at the R11 telemetry windows 46.6 and 50.6 it is 0.03-0.05 for both models (raw or levelled); CLIP_06 30.34 lev scan gave 0.16 (peak 29.58) on Qwen3-VL, nothing on Qwen3.5. i.e. confirms the research doc's warning: 0.3 s airs are invisible to these models at 10 fps with temporal-patch-2 merge.
- CLIP_06 204 s: NO crash claimed by any model. DENSE describes "a person in front/yellow jacket carrying skis, putting on skis, turning to face the camera" 180-210 s (matches R10's refutation: no crash). Qwen3-VL lev: "person in front puts on skis / stops" 205-209. CHECK "fall" P at impact 214.55 = 0.47 raw / 0.12 lev for Qwen3-VL (a 214.5 impact candidate, not 204), the telemetry itself has no >5 g at 204.
- DENSE text quality: captions are good (chairlift, other riders, glove, trees); timestamps are coarse and Qwen3-VL repeats lines (6-8 duplicate "skis visible" lines in a 30 s chunk), Qwen3.5 less so. Both put whole-chunk spans (30.0-60.0) on many events. Levelling made Qwen3-VL stop "carving" claims on the person-in-front section and gave a more accurate description of that scene; no measurable gain elsewhere.
- Audio tags (EfficientAT): speech 322 s, "Bang" 151 s (helmet/wind noise, not impacts), 2 Whoop (CLIP_04 2.5 s, CLIP_09 117.5 s), 1 Gasp (CLIP_07 176.5), no Laughter/Cheering/Scream/Thump at p>=0.25 anywhere; nothing at CLIP_12 46-52, CLIP_11 143-147, CLIP_05 17-19; CLIP_04 271.5 (camera into snow) = Bang 0.33. Not a usable event detector on this bin; wind-masking needed.

## Scoring (checked labels). CHECK = telemetry-triggered, 12 clips, 272 items, confirm threshold P(yes) >= 0.5
Telemetry candidates alone, no VLM (upper bound for the triggered path): recall high 0.78 (7/9), air 0.86 (6/7), fall 0.60 (3/5), karl_ok rows 0.50 (2/4), medium 1.0, false alarms on labelled negatives 7 in 2.55 neg-minutes = 2.74/min (impact + speed-drop candidates labelled "fall").
| | recall high (9) | air (7) | fall (5) | karl_ok (4) | FA on negatives /neg-min | AUROC air (tel windows 9+/59-) | AUROC air (labelled 7/38) | AUROC fall (tel 6/100) | AUROC fall (labelled 7/38) | GPU s/footage-min | VRAM |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Qwen3-VL-8B raw | 0.22 | 0.00 | 0.40 | 0.00 | 1.57 | 0.51 | 0.64 | 0.68 | 0.94 | 10.9 | 8.1 |
| Qwen3-VL-8B levelled | 0.22 | 0.00 | 0.40 | 0.00 | 0.78 | 0.49 | 0.74 | 0.64 | 0.86 | 11.9 | 8.1 |
| Qwen3.5-4B raw | 0.22 | 0.00 | 0.40 | 0.00 | 0.78 | 0.41 | 0.55 | 0.90 | 0.92 | 6.3 | 5.4 |
| Qwen3.5-4B levelled | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.41 | 0.58 | 0.86 | 0.90 | 5.7 | 5.4 |
Reading: "is the wearer airborne" is a coin flip (AUROC 0.4-0.7) for both models; "does the wearer fall/crash" separates well on the labelled windows (AUROC 0.86-0.94, mean P(yes) 0.6 on positives vs 0.09 on negatives for Qwen3-VL raw) and decently on telemetry windows (Qwen3.5 0.86-0.90). At the 0.5 threshold recall is low because P(yes) on true falls is moderate (mean 0.23-0.6): a lower threshold (~0.2) would be the right cut for fall; the routing band 0.2-0.9 in the research doc fits. Levelling: reduces false alarms (Qwen3-VL 1.57 -> 0.78/min) and lifts labelled-air AUROC (0.64 -> 0.74) but is not better for fall; n is tiny (7 positives), do not over-read. Qwen3.5-4B is as good as the 8B on fall and ~2x cheaper in VRAM and CHECK time.
Camera-toss (CLIP_02 9-12) is treated as a negative for "fall" in scoring.

## Find (19 non-low queries; low reported separately: 0 hits for every embedder)
| embedder | hit@3 strict IoU>=0.3 (README) | hit@3 any overlap with an answer window | embed 43 min of footage |
|---|---|---|---|
| PE-Core-L14-336 raw | 0.21 (4/19) | **0.68 (13/19)** | 78 s |
| PE-Core-L14-336 levelled | 0.11 | 0.53 | 88 s |
| SigLIP2 so400m raw | 0.05 | 0.37 | 131 s |
| SigLIP2 so400m levelled | 0.05 | 0.42 | 116 s |
Strict IoU is nearly unreachable because results are fixed 2 s windows vs 0.5-4 s (up to 40 s) answers; the overlap column is the practical number. PE-Core clearly beats SigLIP2 here (SigLIP2 may be under-served by squashed 16:9 frames; I did not tune its prompt template). Levelling did NOT help retrieval (hurts PE-Core; black corners). No VLM rerank was run. Per-query hits: out/score.json -> find.

## Recommended local stack (from what was measured)
1. Telemetry decides WHEN (R11 rules; recall 0.78 of high-confidence events by itself, but 2.7 false alarms per labelled-negative minute).
2. Local VLM CHECK as a cheap veto/score on those candidates: use Qwen3.5-4B (4.4 GB, 0.6 s/item) with the "falls/crashes" question and a threshold near 0.2; do not trust it on airs (P(yes) uninformative for 0.3 s airs; keep airs telemetry-led and send them to Claude).
3. Find: PE-Core-L14-336 on raw (unlevelled) 2 fps frames, ~2 GPU-s per footage-minute; take top-k and let Claude check.
4. DENSE VLM pass: poor value at this speed (~1 GPU-hour per footage-hour, whole-chunk timestamps, no air/fall recall). Use it only for captions, or run it only inside triggered windows. Levelling: keep for VLM inputs (fewer false alarms), skip for embeddings.
5. Audio tagger: not useful as is on helmet audio; needs wind masking before being an event source.

## Problems hit
- vLLM not attempted: the box has no nvcc, a very busy CPU, and 25 GB budget; transformers + bnb nf4 was reliable. This makes dense decode slow (bnb 4-bit ~10 tok/s); a vLLM/AWQ or llama.cpp GGUF build would probably be 3-5x faster. Untested.
- `unsloth/Qwen3-VL-8B-Instruct-unsloth-bnb-4bit` failed to run on transformers 5.22 (bitsandbytes assertion in the deepstack merger: module quantised but weights not packed); replaced with a locally quantised official checkpoint.
- Qwen3.5-4B has no `causal_conv1d` kernel (no nvcc); fla installed, conv falls back to PyTorch.
- PyNvVideoCodec ignores the MP4 rotation flag (see above). GoPro clips here are 30000/1001 fps, not 59.94 (the eval README says proxies are 59.94 but the timeline is shared).
- HF cache keeps orphan blobs after deleting snapshot dirs; had to remove blobs by hand (disk was at 95 %, other users are also writing to /).
- Dense prompt originally degenerated into one-line-per-second repetition; fixed with "report each event once, max 14 lines", still some duplicates.
- Eval labels switched to the checked files mid-run: CLIP_06/12 runs were produced before that, the checked files only change CLIP_01/02/11 rows, so the already-finished results stay valid, but the dense chunk set and labelled check items for later clips were generated from the checked files.
- GPU jobs ran at nice 5 not 19 (nice 19 starved them at load ~20); CPU-only steps ran at 19.

## How to read / resume
- `tail ~/roughcut-loop/understand/bench/logs/run_all.log` ; finished when `~/roughcut-loop/understand/bench/out/ALL_DONE` exists (log line `=== ALL_DONE`). Dense results: out/results/<model>_<raw|lev>_dense_CLIP_NN.json (events + raw text). Check results: ..._check_...json.
- Re-score any time: `cd ~/roughcut-loop/understand/bench && source .venv/bin/activate && python score.py` -> out/score.json (keys: telemetry_only_baseline_check, dense, check, find). The dense block is empty/partial until the runs finish; re-run this RESULTS.md table for dense after ALL_DONE (dense recall/false-alarm numbers are in score.json -> dense).
- To restart if killed: `nohup ./run_all.sh &` (idempotent, skips finished files).

## Addendum 2026-11-07 13:45: DENSE scored on all 12 clips (25.3 labelled footage-min)

| variant | recall (high, n=9) | airs (n=7) | falls (n=5) | false alarms / negative-min | GPU-s per footage-min | VRAM peak |
|---|---|---|---|---|---|---|
| Qwen3-VL-8B raw | 0.00 | 0.00 | 0.00 | 0.78 | 56 | 9.0 GB |
| Qwen3-VL-8B levelled | 0.11 | 0.14 | 0.00 | 0.00 | 51 | 9.0 GB |
| Qwen3.5-4B raw | 0.22 | 0.00 | 0.40 | 0.39 | 57 | 4.9 GB |
| Qwen3.5-4B levelled | 0.22 | 0.14 | 0.20 | 0.00 | 52 | 4.9 GB |

Confirms the wave 20 plan: a dense local pass is not an event detector (telemetry alone: 0.78); keep it for captions
inside triggered windows at most. Levelling removes its false alarms entirely on both models.

## Productised: the `local` index stage (wave 20, lane LOCALGPU) — Killington, 2026-11-08

The bake-off recipe, run as the app's own stage (`roughcut/localvlm.py`, `localgpu/rcgpu`, journal stage `local`), own server (`--work` under scratch, port 9115), RTX 3080 shared with other lanes, nice 5.
- **Time.** All 12 clips (43.1 min of footage), embeddings at 2 fps + a fall/air check at every telemetry moment (134 checks): **8.9-9.9 GPU-minutes** in the stage (embed 5.6, frame grabs 1.3, CHECK 2.0) plus ~45 s to load the two models once; two full runs, 591 s and 535 s of per-clip GPU seconds (an interrupted-then-resumed run: Stop after 3 clips, the rest in a second run; the finished clips were kept). Budget was <= 15. The bake-off took 6.3 GPU-s per footage-minute for CHECK alone and ~2 for the embeddings; the rest is the sparse NVDEC decode (about 7 s per footage-minute, every 15th frame only).
- **Fall AUROC (P(yes) of "does the wearer fall or crash").** Telemetry windows asked a fall question: **0.91** (6 positives, 100 negatives; mean P 0.22 vs 0.035; bake-off 0.86 levelled). Labelled windows (`scripts/score_local_checks.py labelled`): **0.92** (7 positives, 38 negatives; mean P 0.24 vs 0.035; bake-off 0.90). Threshold 0.2 for "likely"; an air is advisory only.
- **VRAM.** torch peak 5.2-5.8 GB allocated (5.8 reserved) per process, both models resident; the card was shared with other lanes' jobs (a diarisation run took 9.6 GB beside it at one point, which the lock does not govern: it only orders our own GPU jobs).
- **What changed from the bench.** Impacts are no longer asked "is the wearer airborne" (AUROC 0.4-0.6, a third of the GPU time), only "falls"; only the last position's logits are computed (`logits_to_keep=1`); frames for a window are grabbed by random access with neighbouring windows sharing them; the 10 fps frame cache of the bench (JPEG writes, 20 min) is gone: only the 2 fps embedding frames and the check windows are ever decoded. 1 fps embeddings (keyframes only, 4x faster decode) lost Find hits (hit@3 any-overlap 13/19 -> 10/19) so 2 fps stays.
