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

## Label formats

- **Cuts** (`R2`): CSV `file,frame` — one row per true cut, labeled by scrubbing.
- **Highlights** (`R4`, `R1`): CSV `file,start_s,end_s,note` — every moment Karl would
  consider including in an edit. Be inclusive; the gate study needs the full set.
- **Reference transcript** (`R3`): plain text, hand-corrected, for a chosen contiguous 10-min
  audio span (note the span in the manifest).
- **R1 subset labels**: CSV `shot_id,description,highlight,usable` — produced after T3 has
  assigned shot IDs.

## Target bins (DECISION D1)

| ID | Suggested content | Used by |
|---|---|---|
| B1 | Ski trip bin (mixed action cam + phone, outdoor audio) | R1 R2 R3 R4, T3–T7, T13 |
| B2 | Indoor event (birthday/family — speech-heavy, low light) | R2, secondary eval |
| B3 | (optional) travel vlog style | held-out final eval |

Run records (cost reports, eval sheets) go in `benchmarks/runs/<date>-<bin>/`.
