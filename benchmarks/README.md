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
it is the only labeling needed to reach checkpoint CP2, and it should be a fast visual pass over
a contact-sheet index, not a video-scrubbing session (SPEC §0, principle 1).

## Label formats

- **Cuts** (`R2`): CSV `file,frame` — one row per true cut, labeled by scrubbing.
- **Highlights** (`R4`, `R1`): CSV `file,start_s,end_s,note` — every moment Karl would
  consider including in an edit. Be inclusive; the gate study needs the full set.
- **Reference transcript** (`R3`): plain text, hand-corrected, for a chosen contiguous 10-min
  audio span (note the span in the manifest).
- **R1 subset labels**: CSV `shot_id,description,highlight,usable` — produced after T3 has
  assigned shot IDs.

## Registered bins (D1 resolved; measured 2026-07-25 with ffprobe)

| ID | Bin | Measured | Format | Role |
|---|---|---|---|---|
| **B1** | **Copper 02-2026** | **11 min**, 26 MP4 | HEVC 5.3K (5120×2880) 23.976fps | **Prototype / primary.** Raw GoPro naming (`GX0104xx`) — never curated, so highlight-finding measured on it is honest. Short, which limits statistical power |
| B2 | Killington 01-2026 | **43 min**, 12 MP4 | H.264 4K (3840×2160) 29.97fps | Secondary — ⚠️ **pre-curated**, usable only under the controls below. More footage, less trustworthy |
| B3 | Mt. Marcy 02-2025 | **8 min**, 7 MP4 | HEVC 5.3K 23.976fps | Held out from tuning entirely |

**B1 is Copper** — raw beats large. Killington has four times the footage but a human already
removed its boring material, so measuring the analysis against it flatters the result. Copper's
26 clips came straight off the camera. Where more material is needed, add Killington's long-form
clips under the controls below and report the two separately rather than pooling them.

**Sidecar `.WAV` files are out of scope** (Karl, 2026-07-25) — audio comes from the MP4's
embedded track. Only MP4s are copied into the working bins. The mic-array opportunity stays
filed under FUTURE_PHASES P2.2.

### ⚠️ B1 has mixed and misleading rotation metadata

`GX010474.MP4` carries `rotation=-90`; applying it (ffmpeg's default) produces a **sideways
portrait frame**, while `-noautorotate` yields the correct upright 16:9 image — verified by
eye. `GX010475.MP4` in the same bin carries no rotation at all. So the bin is mixed, and the
metadata is actively wrong on at least one clip.

Consequences: contact sheets for this bin need `--orient none`; ingest must record rotation and
resolve a per-clip override with visual confirmation (SPEC §3 S0, T1/T2 DoDs). A blanket
`-noautorotate` is **not** a general fix — phone footage genuinely shot in portrait needs its
metadata honoured. This is why orientation is verified by looking rather than believed.

**Total available footage: ~1 hour.** The *design* target remains 3h typical / 5h max (SPEC §7)
— that's the workload Karl wants supported, and future trips will supply it. But the benchmark
bins are smaller than the design target, which limits R7's statistical power, not the architecture.

### ⚠️ B2 (Killington) is pre-curated — controls required if used

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

**Resolved:** Copper is raw and is now B1, so Killington is only needed if the study wants more
material than 11 minutes. Use it under both controls above, and report it separately.

### Appendix: what the Copper `.WAV` files are (now out of scope)

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
