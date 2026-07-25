# Audio Analysis Design

> **Measured on B1 — read [R8](../research/R8-audio-signals.md) alongside this document.**
> Three of its design assumptions did not survive contact with real footage: the Tier A speech
> detector is only marginally better than assuming constant speech (F1 0.63 vs 0.56), ASR is
> cheap enough at 13× realtime that holding it back as Tier B buys nothing, and on B1 audio
> flags the *non-skiing* footage at twice the density — the opposite of what the brief wants.
> The sections below are annotated where R8 overrides them.

Audio is the **fast pass**. A one-dimensional signal at 16kHz is trivial to process compared to
4K video: a whole bin's audio can be characterised in seconds, where the video takes minutes and
the VLM costs money. So audio runs first, produces a temporal map of *where* things happen, and
the expensive visual analysis is pointed only at what that map flags.

Everything here runs **locally** — no API cost, which further weakens any argument for gating.

## The wind trap — read this before writing any energy-based heuristic

**Naive RMS energy is actively misleading on ski footage.** A GoPro moving at speed produces
enormous broadband low-frequency wind noise. A detector that ranks "loud = interesting" will
rank every traverse and every windy lift ride above a quiet moment containing the best line
anyone said all day. This is the single most likely way to build an audio stage that looks
principled and performs worse than random.

Wind is separable, because it does not look like voice:

| | Wind | Speech / laughter / shouting |
|---|---|---|
| Spectrum | concentrated below ~200 Hz, broadband above | energy in 300 Hz–3.4 kHz |
| Structure | noise-like — **high spectral flatness** | harmonic/formant structure — low flatness |
| Envelope | sustained, slowly varying | syllabic, rapid modulation (~4 Hz) |

So the useful primitives are **band-limited energy** (speech band, not full band), **spectral
flatness** as a wind discriminator, and **modulation rate**. Full-band RMS on its own should not
appear in a scoring function.

> **R8:** each of these three separates speech from non-speech, but only weakly (AUC 0.62–0.65)
> and they are correlated enough that ANDing them adds little. Measured thresholds are
> `speech_band > -30 dBFS`, `flatness < 0.10`, `modulation > 0.35` — note the flatness value is
> 3.5× lower than the guess this document originally implied. The wind branch itself is
> **untested**: B1's ski footage is shot from a static or slow camera, so nothing in the bin is
> wind-dominated. The trap is still real for at-speed helmet footage; it just isn't B1.
>
> **[R9](../research/R9-events-and-wind.md) corrects that.** B1 *is* windy — AudioSet scores
> Wind at 0.65 on the moving POV run — and the reason R8 measured none is that the wind rule
> never fired at all. Real wind here sits at flatness **0.17–0.25** against a guessed threshold
> of 0.40, so the conjunction was unsatisfiable. The physics in the table above is nonetheless
> vindicated: the plane-cabin clips carry *more* low-band dominance (10.9 dB) than most windy
> clips at flatness 0.008, because engine rumble is tonal where wind is noise-like — flatness is
> what stops a dB-only rule calling an aircraft interior windy. Measured rule:
> `low > -40 dBFS AND low − speech > 2 dB AND flatness > 0.15`, with the absolute term needed
> because a pure ratio inflates whenever nobody is speaking.

Note also that wind level is a **quality** signal, not an interest signal — it tells us the
moment's audio is unusable in the edit, which is a different question from whether the moment is
worth including. Keep the two separate.

## Signal tiers

### Tier A — DSP, no model, ~real-time × 100
Cheap enough to run over everything, always.

| Signal | Computed from | Tells us |
|---|---|---|
| Speech-band energy | 300 Hz–3.4 kHz band RMS | someone may be talking |
| Spectral flatness | per-frame FFT | wind vs. structured sound |
| Low/high band ratio | band energies | wind dominance |
| Onset strength | spectral flux | impacts, crashes, sudden events |
| Modulation (4 Hz) energy | envelope spectrum | syllabic rhythm ⇒ speech-like |
| Silence / near-silence | broadband level | pocket footage, dead audio |

### Tier B — small local models, GPU-accelerated
The RTX 5080 makes these effectively free.

- **VAD** (e.g. Silero) — speech / no-speech segments. Cheaper than transcription; good first cut.
- **ASR** (faster-whisper, `large-v3` on GPU) — transcript with word timestamps. **The highest
  value audio signal in this project**: "did you see that", "oh my god", "that was sick" are
  direct interest markers and no visual analysis finds them.
- **Audio event tagging** (YAMNet / PANNs, AudioSet classes) — `Laughter`, `Shout`, `Cheering`,
  `Whoop`, `Screaming`, `Wind`, `Music`, `Speech`. This is what catches the non-verbal reactions
  that matter most and that ASR either drops or mangles.

Start with Tier A + ASR, which is quick to stand up. Add event tagging once we can see what it
is missing — laughter and whoops are the expected gap.

> **R8:** the tiering is real but the *gating* rationale is not. `large-v3` runs at ~13×
> realtime on the 5080, so ASR over a whole 3h project is ~14 minutes and there is no reason to
> spend Tier A signals deciding where to point it. Run ASR over everything; Tier A earns its
> place as quality metering (wind, silence, clipping, loudness) and onset detection, not as a
> gate. The predicted gap is confirmed: **event tagging is now the highest-value remaining audio
> work**, because the non-verbal reactions are what the ski clips actually contain.
>
> **[R9](../research/R9-events-and-wind.md) retires that last sentence.** Event tagging was
> built and B1 turns out to contain no detectable non-verbal reactions at all — the best
> reaction score anywhere in 398s is 0.077, while the same passes score Speech at 0.44–0.76 and
> Wind at 0.65. The mic is on the camera, the operator is the one talking, and the skiers are
> fifty metres away; the laughter visible in the contact sheets is not in the audio. The tagger
> is kept for bins with crowd or close-mic audio, but it will not improve a Copper cut. What it
> *did* find is that the wind branch below had never fired even once — see the next note.

## Output

Per clip, a JSON sidecar:

```json
{
  "clip": "GX010494.MP4",
  "sample_rate": 16000,
  "frame_hz": 10,
  "tracks": {
    "speech_band_db": [...], "flatness": [...], "onset": [...], "vad": [...]
  },
  "transcript": [{"start": 12.4, "end": 15.1, "text": "did you see that", "words": [...]}],
  "events": [{"start": 31.2, "end": 32.8, "label": "Laughter", "conf": 0.81}],
  "summary": {
    "speech_fraction": 0.22, "wind_dominant_fraction": 0.61,
    "audio_usable": true, "peak_dbfs": -3.2, "integrated_lufs": -21.4
  },
  "candidates": [{"t": 31.2, "why": "laughter + speech onset", "score": 0.78}]
}
```

`candidates` is the actionable output — the timestamps worth pointing the visual analysis at.

## How this composes with the visual pass

The two modalities answer different questions, and pairing them is the whole point:

- **Audio says *when*** — cheap, temporally precise, catches things invisible on screen.
- **Vision says *what*** — expensive, semantically rich, catches things silent on the mic.

Neither is sufficient. A crash with a shout is trivially findable by audio and hard to
distinguish visually from a turn. A beautiful empty vista is invisible to audio. The selection
score should fuse both, and a moment flagged by *either* modality deserves attention.

Practical consequence for the analysis policy: run audio over the whole bin first, then let the
audio candidate list drive where the contact-sheet/VLM refinement goes. That makes the
coarse-to-fine approach cheaper and better targeted than a purely visual search.

> **R8 — the important correction.** On B1 that last paragraph is backwards. Letting the audio
> candidate list *drive* refinement points the expensive pass at the plane, the hotel room and
> the milk joke (12.3 candidates/min, 87 words/min) and away from the skiing the brief asks for
> (6.0/min, 23/min). The skiing is quiet because the subject is fifty metres from the mic, not
> because nothing is happening. So: run audio everywhere and use it as *evidence*, never as the
> gate on where vision looks. Quiet clips need **more** visual attention, not less. Audio's win
> on this bin is precision, not coverage — its top hits ("That goes so fucking hard", the "Whoo"
> over the jump) are genuinely the emotional peaks, and there are only a handful of them.

## Integration with the review UI

The per-clip feature tracks should render as a **strip beneath the frame grid** in `label.html`
— speech in one colour, laughter/shouts marked, wind shaded. A reviewer then sees at a glance
where the talking and reacting happened, which is precisely the information a grid of stills
cannot convey, and which prompted this design.

## Implementation risks

- **CUDA in WSL2** — faster-whisper needs a working CUDA build of ctranslate2. It works, but is
  the most likely setup snag. Fall back to `int8` CPU for small bins; it is slow but not blocking.
- **Model download size** — `large-v3` is ~3GB. Fine locally, but pin the model and cache it.
- **GoPro audio is 2-channel AAC from a mic array** (see FUTURE_PHASES P2.2); downmix to mono
  16kHz for analysis and keep the original for the edit.
