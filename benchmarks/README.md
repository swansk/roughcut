# Benchmarks

Real-footage bins used for quality measurement (R-studies and T-task DoDs). **Footage never
enters git** — bins live on disk and are referenced by manifest.

## Registering a bin

1. Put the footage in a folder (e.g. `D:\Video Footage\ski-2026\`). Karl's existing
   `Documents\Video Footage` is a natural source.
2. Add `benchmarks/bins/B1.json`:

```json
{
  "id": "B1",
  "name": "ski-trip-2026",
  "path": "C:/Users/karl/Documents/Video Footage/<folder>",
  "description": "what it is, cameras used, rough duration",
  "labels": {
    "cuts": "benchmarks/labels/B1-cuts.csv",
    "highlights": "benchmarks/labels/B1-highlights.csv",
    "transcript_ref": "benchmarks/labels/B1-transcript-10min.txt"
  }
}
```

Manifests and label files ARE committed (they're small text). A checksum list
(`B1.checksums`) is generated on registration so runs can detect footage drift.

**How to produce labels: [LABELING.md](LABELING.md).** Start with the highlights pass on B1 —
it is the only labeling needed to reach checkpoint CP2.

## Label formats

- **Cuts** (`R2`): CSV `file,frame` — one row per true cut, labeled by scrubbing.
- **Highlights** (`R4`, `R1`): CSV `file,start_s,end_s,note` — every moment Karl would
  consider including in an edit. Be inclusive; the gate study needs the full set.
- **Reference transcript** (`R3`): plain text, hand-corrected, for a chosen contiguous 10-min
  audio span (note the span in the manifest).
- **R1 subset labels**: CSV `shot_id,description,highlight,usable` — produced after T3 has
  assigned shot IDs.

## Registered bins (D1 resolved; measured 2026-07-25 with ffprobe)

| ID | Bin | Measured | Format | Notes |
|---|---|---|---|---|
| **B1** | Killington 01-2026 | **43 min**, 12 MP4 | H.264 4K (3840×2160) 29.97fps | ⚠️ **Curated** — see below. 11 `.LRV` proxies + 16 `.THM` present (counts don't match the MP4s, so the bin is a mix of renamed exports and GoPro originals) |
| B2 | Copper 02-2026 | **11 min**, 26 MP4 | HEVC 5.3K (5120×2880) 23.976fps | Raw GoPro naming. 26 paired `.WAV` = **4-channel 32-bit PCM mic-array data**, not an external mic (see below) |
| B3 | Mt. Marcy 02-2025 | **8 min**, 7 MP4 | HEVC 5.3K 23.976fps | Raw GoPro naming; held out from tuning entirely |

**Total available footage: ~1 hour.** The *design* target remains 3h typical / 5h max (SPEC §7)
— that's the workload Karl wants supported, and future trips will supply it. But the benchmark
bins are smaller than the design target, which limits R7's statistical power, not the architecture.

### ⚠️ B1 is pre-curated — this compromises its use for R7

Killington's files are hand-named after their content: `bombbeginning`, `cleanduckin`,
`goodliftlineme`, `pocketpizza`, `rockhitmarkers`, `rowdy`, `spencerunderbomb`,
`spenny-bigair-begin`, `spenny-me-air`, `streamfall`, `tastytrees`, `walk`. A human has already
found the interesting moments and labeled each file with what happens in it.

**Measuring "does the analysis find the interesting parts" on footage where the boring parts
were already removed would produce a falsely positive result at exactly the checkpoint meant to
be an honest go/no-go.** Two mitigations, both required:

1. **Use only the long-form files.** Nine of the twelve run 190–320s — long enough to contain
   substantial boring stretches around the moment they're named for, which is the real task.
   The three short files (35–40s) are trimmed highlights and are excluded from R7.
2. **Neutralize filenames.** The study operates on `clip_01.mp4 …` copies; the model must never
   see `bombbeginning.MP4`, which would hand it the answer.

**Better option if it exists:** raw, unculled footage straight off an SD card. If Karl has any
(other drives, cards, another machine), it should replace B1 for R7.

### The Copper `.WAV` files are not an external mic

Probed: `pcm_s32le`, 48kHz, **4 channels**, 32-bit, duration exactly matching the paired MP4.
That is GoPro's raw mic-array capture, written alongside the video for wind-noise processing —
same camera, same clock. There is **no sync problem to solve**; the opportunity is better
wind-noise reduction and beamforming from the raw array, which is an audio-quality enhancement
rather than a sync requirement. (Corrects an earlier inference from filename pairing alone.)

Source library currently at `C:\Users\karl\Documents\Video Footage\` on the Windows host.
**D5 open:** whether B1 is copied into the WSL2 ext4 filesystem or the library moves to the
Linux SSD box. Only `bins/*.json` changes either way — nothing in code or tests knows the path.

Labeling priority (**D6**): start with **highlights only over 30–45 min of B1** — that is
enough to run R7 and reach checkpoint CP2. Cut lists (R2) and the reference transcript (R3)
are worth doing only after the thesis gate passes.

Run records (cost reports, eval sheets) go in `benchmarks/runs/<date>-<bin>/`.
