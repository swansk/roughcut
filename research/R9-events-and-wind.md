# R9 — Audio event tagging, and the wind detector it exposed as dead

**Status:** run and closed on B1 (Copper), 2026-07-25. Tooling:
`research/tools/audio_events.py`, `research/tools/wind_calibrate.py`.

R8 closed by naming Tier B event tagging (laughter, cheering, whoops) as *"the highest-value
remaining audio work"*, and Karl's review of the first cuts pointed the same way — the vocal
reactions are what make the ski clips land, and ASR catches them only by luck (it transcribed
one "Whoo" as a word and dropped everything else, because a whoop is not speech).

The study built the tagger, and then measured that **the prediction was wrong for this footage.**

Model: AST fine-tuned on AudioSet (527 classes), on the RTX 5080. AudioSet is the right
vocabulary — Laughter, Giggle, Cheering, Whoop, Yell, Shout and Wind are all first-class labels.

## Result 1 — B1 contains no detectable non-verbal reactions

Across the entire 398s bin, the **highest score for any reaction class is 0.077** (`Yell`, in
GX010496, the silent base-area clip — most likely distant crowd noise). Everything else in the
group sits below 0.035. Laughter never exceeds 0.034 anywhere.

This is not a broken model or a threshold artefact. The same forward passes score `Speech` at
0.44–0.76 with confidence, `Music` at 0.46 in the base-area clip, and `Wind` up to 0.65 — the
tagger is working and discriminating. Window length was ruled out separately: sweeping 1.5s /
2.5s / 4.0s / 10.24s windows over hand-picked moments moved Laughter by hundredths and never
above 0.034, so brief events being swamped inside a long window is not the explanation either.

**Mechanism.** The microphone is on the camera. The person holding it is the one talking, and
the people doing the skiing are fifty metres away. Laughter I had inferred from smiling faces in
the contact sheets is simply not in the audio — the sheets show a group laughing while the mic
hears one person narrating. This is the same fact R8 measured from the other side (audio points
away from the skiing), arriving again as a harder limit: **on this footage there is no
non-verbal reaction signal to extract, at any threshold, with any model.**

Consequence: AUDIO.md's and R8's "highest-value remaining audio work" claim is retired for B1.
The tagger is kept — it is correct, cheap, and a bin with crowd, cheering or close-mic banter
would pay it back immediately — but it should not be expected to improve a Copper cut.

## Result 2 — the tagger found that the Tier A wind detector was dead

The negative result on reactions came with a positive one nobody was looking for. The `quality`
class group fired confidently, and only on the on-mountain clips:

| clip | AudioSet Wind | shipped `wind_dominant_fraction` (R8) |
|---|---|---|
| GX010494 | **0.652** | 0.024 |
| GX010493 | **0.541** | 0.004 |
| GX010488 | **0.433** | 0.000 |
| GX010490 | 0.199 | 0.080 |
| GX010492 | 0.170 | 0.000 |
| all 12 others | 0.000 | ≤0.020 |

R8 had reported `wind_dominant_fraction ≤ 0.08` everywhere and concluded *"the wind trap does not
bite on B1, because the camera is usually still"*. Half right: the ordering does track camera
motion (the moving POV run scores 0.65, the static tripod-ish shot 0.199). But the DSP rule was
not measuring that — **it was never firing at all.**

### The broken constant was flatness, not the dB gap

Shipped rule: `low − speech > 12 dB AND flatness > 0.40`. Measured median flatness on the
windiest clips in the bin is **0.17–0.25**. The flatness term alone made the conjunction
unsatisfiable on real wind. The threshold was guessed in the design document and never
challenged, exactly as R8's speech thresholds had been.

### Flatness is nonetheless the right discriminator — the mechanism check passes

The plane-cabin clips (GX010477, GX010478) show **10.9 and 10.5 dB** of low-band dominance —
more than most genuinely windy clips — at flatness **0.008**. Engine rumble is low-frequency but
*tonal*; wind is low-frequency and *noise-like*. A dB-only rule would confidently label the
aircraft interior as windy. AUDIO.md's table was right about the physics and wrong about the
numbers.

### A pure ratio also breaks on silence

Recalibrating flatness alone (`>1 dB & flat >0.10`) made the detector fire, but it then scored
the silent base-area clip GX010496 at 0.42 against the tagger's 0.00. `low − speech` is a
*ratio*: when nobody is talking the speech band is empty by definition, so any ambient hiss
reads as wind dominance. The fix is an absolute floor on the low band, which is not redundant
with the ratio — it answers a different question ("is there actually energy down there").

### Adopted operating point

```
low_band > -40 dBFS  AND  low - speech > 2.0 dB  AND  flatness > 0.15
```

| rule family | hits (of 5) | false positives (of 12) |
|---|---|---|
| shipped (12 dB / 0.40) | 0 | 0 — it never fired |
| relative only, recalibrated | 5 | 5–9 |
| **adopted (three-term)** | **4** | **0** |

Every rule that caught all five carried at least five false positives; the one it misses is
GX010492 at 0.170, the weakest positive in the set. For a *quality* gate — "is this audio usable
in the edit" — trading the marginal detection for zero false alarms is the right side of that
line. Verified after re-running the bin: the four strong positives land at 0.218–0.502, every
tagger-negative clip sits at or below 0.162, and the plane clips return to 0.000.

## What this changes

- **Wind metering works now**, which matters for P2.2 audio post: the trigger map for
  noise reduction and for deciding when to duck or replace a segment's audio was previously
  empty on every clip.
- **Reaction detection is not the next lever on this bin.** The remaining candidate is the one
  Karl's review implies: excitement is **prosodic, not lexical** — an excited "yeah!" and a
  filler "yeah" are the same word, and R8 demoted the word on lexical grounds while Karl
  observed the excited ones cutting well. Pitch, energy and duration are the separating
  features, and the Tier A tracks already carry two of the three. Not built here.
- **`audio_analyze.py` no longer discards `events` on re-analysis.** Tier A and Tier B write to
  the same sidecar from different tools, and re-running the cheap pass silently wiped the
  expensive one.

## Reproduce

```
uv run research/tools/audio_events.py ~/footage/copper-02-2026 --sidecars ~/work/audio \
  --skip GX010479,GX010480,GX010481,GX010482,GX010484,GX010485,GX010497,GX010498,GX010499
uv run research/tools/wind_calibrate.py ~/work/audio
```
