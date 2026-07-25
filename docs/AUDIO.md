# Audio Analysis Design

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
