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

## Registered bins (D1 resolved)

| ID | Bin | Size / files | Notes | Used by |
|---|---|---|---|---|
| **B1** | Killington 01-2026 | ~31GB, 12 MP4 | GoPro: `.LRV` low-res proxies and `.THM` thumbnails already present — T2 should check whether the LRVs can be used directly instead of transcoding | R7, R1, R2, R3, T3–T7, T13 |
| B2 | Copper 02-2026 | ~8.1GB, 26 MP4 | 26 paired `.WAV` files, one per clip — likely an external mic. Audio-sync stress case; confirm with ffprobe at T1 | R2, secondary eval |
| B3 | Mt. Marcy 02-2025 | ~5.3GB, 7 MP4 | held out from tuning entirely | final eval only |

Source library currently at `C:\Users\karl\Documents\Video Footage\` on the Windows host.
**D5 open:** whether B1 is copied into the WSL2 ext4 filesystem or the library moves to the
Linux SSD box. Only `bins/*.json` changes either way — nothing in code or tests knows the path.

Labeling priority (**D6**): start with **highlights only over 30–45 min of B1** — that is
enough to run R7 and reach checkpoint CP2. Cut lists (R2) and the reference transcript (R3)
are worth doing only after the thesis gate passes.

Run records (cost reports, eval sheets) go in `benchmarks/runs/<date>-<bin>/`.
