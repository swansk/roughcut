# R8 — Audio signals on B1: what the Tier A DSP is actually worth

**Status:** run and closed on B1 (Copper), 2026-07-25. Tooling:
`research/tools/audio_analyze.py`, `research/tools/audio_calibrate.py`.

**Question the study had to answer before selection could use audio:** the Tier A detector in
[AUDIO.md](../docs/AUDIO.md) has thresholds, and they cannot be guessed. The first pass over
Copper called 90%+ of nearly every clip "speech", which is obviously wrong and was the prompt
for measuring rather than tuning by feel.

**Reference:** the ASR transcript from the same sidecars. Where Whisper found words, someone
was talking. It is a good reference, not a gold standard — it misses quiet or wind-buried
speech (so measured precision is pessimistic) and its segment spans include intra-sentence
pauses (so measured recall is optimistic at the frame level).

**Corpus:** the 17 non-junk B1 clips, 398s, 3,964 frames at 10 Hz. ASR-speech covers 39.1%.

## Result 1 — the DSP speech detector is weak, and ASR makes it redundant

Per-feature separation, as AUC (0.5 = the feature carries no information):

| Feature | AUC | speech p10/50/90 | other p10/50/90 |
|---|---|---|---|
| spectral flatness (inverted) | 0.650 | -0.199 / -0.047 / -0.008 | -0.320 / -0.103 / -0.008 |
| speech-band level (dBFS) | 0.641 | -32.6 / -25.9 / -18.9 | -45.8 / -27.6 / -22.4 |
| modulation (2–8 Hz share) | 0.615 | 0.35 / 0.59 / 0.79 | 0.26 / 0.51 / 0.73 |
| onset (spectral flux) | 0.577 | 0.18 / 0.23 / 0.36 | 0.17 / 0.22 / 0.33 |

Grid search over the AND rule (`db > a & flatness < b & modulation > c`), 13×17×15 points:

| Operating point | P | R | F1 |
|---|---|---|---|
| best F1: `db>-35, flat<0.10, mod>0.10` | 0.537 | 0.751 | 0.626 |
| best precision at R≥0.60: `db>-30, flat<0.10, mod>0.35` | 0.586 | 0.623 | 0.604 |
| baseline — call every frame speech | 0.391 | 1.000 | 0.562 |

**The detector's whole edge over "assume everyone is always talking" is F1 0.63 vs 0.56.** The
three discriminators are each real but weak, and they are correlated enough that ANDing them
buys little. The measured flatness optimum (0.10) is also 3.5× lower than AUDIO.md's design
guess of 0.35 — the first grid pinned against its own lower edge and had to be re-run wider,
which is the failure mode of picking a range around an assumed answer.

**Decision: speech candidates come from the transcript, not from the DSP.** At P=0.59 the DSP
detector would add roughly one false candidate per true one while telling us strictly less than
the words do. It stays as a *track* (it is nearly free, and the review-UI strip in AUDIO.md
wants it) and as the fallback when ASR is off, but it no longer generates candidates. Adopted
operating point is the precision-oriented one, since a strip that says "talking" everywhere
conveys nothing.

The cost of losing ASR is concrete: run with `--no-asr`, `GX010489` yields **zero** candidates.
That is the clip containing the jump and the "Whoo" that ranks second in the whole bin. The
utterance is too short and too distant to clear the DSP threshold, and the landing makes no
sound at a camera that far away. On a talky clip the fallback behaves sensibly (10 candidates on
`GX010474`, aligning with the real transcript timings to within a few tenths), which is the
point — the DSP detector finds conversation, and conversation is not what we are looking for.

## Result 2 — the economics that motivated tiering do not hold here

`large-v3` on the RTX 5080 (float16, CUDA in WSL2) transcribed all 398s **in ~30s of GPU time,
about 13× realtime**, model load included. A 3h project is therefore ~14 minutes of ASR.

AUDIO.md tiers Tier A as the thing cheap enough to run over everything with ASR held back as
Tier B. At 13× realtime that distinction stops paying for itself: ASR is affordable over
everything, and it answers the question directly instead of by proxy. This mirrors the decision
already recorded for VLM cost gating — **gating a cheap-enough pass costs more in quality than
it saves.**

CUDA setup was the predicted snag and is solved: ctranslate2 finds cuBLAS/cuDNN via pip-provided
`nvidia-*` wheels preloaded with `ctypes.CDLL` — no system CUDA toolkit and no sudo.

## Result 3 — the wind trap does not bite on B1, because the camera is usually still

`wind_dominant_fraction` is ≤0.08 on every clip and 0.00 on most. The bin's ski footage is shot
from a static or slowly-moving camera (a skier riding *toward* a planted camera, a distant jump,
a following POV run), so there is little relative airflow at the mic.

This is not evidence that the wind trap was wrong — it is untested here. Killington, and any
helmet-mounted footage at speed, remain the case it was written for. Two things follow: the wind
threshold (`low − speech > 12 dB` and flatness > 0.40) is **unvalidated**, and wind must not be
assumed to be the reason a clip is quiet.

## Result 4 — audio attention is biased *away* from the skiing

The finding with the most consequence for selection. Split by what the contact sheets show,
**all 17 reviewed** (a first pass over 7 sheets misclassified the split badly — it put the base
area and lift clips in "travel" and concluded the bin was mostly non-skiing, which is wrong;
the corrected figures are below and the direction of the finding is unchanged but sharper):

| | clips | duration | candidates | words |
|---|---|---|---|---|
| on-mountain | 10 | 277.0s (**70%**) | 34 (**7.4/min**) | 161 (**34.9/min**) |
| travel / airport / plane | 7 | 121.2s (30%) | 34 (**16.8/min**) | 281 (**139.1/min**) |

**Audio flags the non-skiing footage at 2.3× the candidate density and 4× the word rate**, and
splits its candidates 50/50 across a bin that is 70/30 on-mountain by duration. The trip's
talking happens on the plane, at the airport bar, and around a running joke about a gallon of
milk; the skiing is quiet, because the people in it are 50 metres from the microphone.

The agreed brief asks for emphasis on the skiing. An audio-led selection would deliver the
opposite. Consequences:

- Audio must not be the primary selector on this bin. It says *when* something was said, and on
  B1 that is mostly the travel material the brief wants only a little of.
- The fusion must **up-weight the visual pass on quiet clips** rather than treating absence of
  audio candidates as absence of interest — the "a beautiful empty vista is invisible to audio"
  case in AUDIO.md, arriving as the dominant case rather than an edge case.
- The best audio moments are still worth having: the top-ranked candidates are
  `GX010495 34.1s "That goes so fucking hard"` and `GX010489 1.2s "Whoo"` — the latter is the
  reaction to the jump that clip is *about*. Audio found the emotional peak of a silent-looking
  clip, which is exactly its job.

## Result 5 — two detector defects found by looking at the output

Both were caught by reading the ranked candidate list rather than by any metric:

- **Onset spikes flooded.** At `z>3` the rule fired **15.2×/min** and attached itself to nearly
  every candidate. MAD collapses in steady audio, so an unremarkable ripple scores a large z.
  Fixed with `z>8` plus a 97th-percentile absolute floor → **1.2×/min**, 8 hits over the bin.
  There are no impact labels, so this is set conservatively by rate, not at a measured optimum.
- **The marker lexicon promoted filler.** `\byeah\b` and `\bdude\b` are the ambient register of
  this trip; scoring them as reactions put "It's very cute / Is that going to work?" at the top
  of the list with a perfect 1.00. Markers are now split strong/weak (0.35 / 0.10), with the
  filler dropped. A marker has to be surprising to be evidence.

Candidate count over the bin fell from 130 (one per 3.1s) to 68 (one per 5.9s), and the top of
the list became reactions instead of banter.

## Gaps this study did not close

- **Non-verbal reactions are still unhandled.** Tier B audio event tagging (laughter, cheering,
  whoops) is not built. Whisper caught one "Whoo" as a word, which is luck rather than coverage.
  This is the expected gap AUDIO.md predicted, and it is the highest-value remaining audio work.
- **The wind discriminator is unvalidated** (Result 3) — needs B2 or any at-speed footage.
- **Onset precision is unmeasured** (Result 5) — needs impact labels, which do not exist.
- **Whisper hallucination handling is a blocklist**, not a measurement: segments with
  `no_speech_prob > 0.6` and a small set of known phrases ("Thank you.", "Thanks for watching!")
  are dropped. Fine for a prototype; it will silently eat a real utterance eventually.

## Reproduce

```
uv run research/tools/audio_analyze.py ~/footage/copper-02-2026 -o ~/work/audio \
  --skip GX010479,GX010480,GX010481,GX010482,GX010484,GX010485,GX010497,GX010498,GX010499
uv run research/tools/audio_calibrate.py ~/work/audio -o ~/work/audio/R8-calibration.json
```
