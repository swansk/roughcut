# Monitor vs export (Quick 1080p, copper-v1-quick.mp4)

Program monitor captured parked (after a reload so the monitor drew picture), export frame pulled at the
same timecode (t = frames / 23.976). Each `*-side.png` = monitor LEFT, export RIGHT, both 840×472.
Frame alignment checked by matching ±1.2 s of export frames against the monitor: 0-frame offset on 5 of 6,
-1 frame on shot 10 (within noise).

| # | Timecode | What | Side by side | Differences |
|---|---|---|---|---|
| 1 | 00:00:02:21 | opening title over shot 1 | title-side.png | title ~3 px lower in export (≈7 px at 1080); export slightly darker (mean RGB 120/131/140 → 116/128/139) |
| 2 | 00:00:05:21 | shot 2, sideways selfie (GX010474) | shot2-rotated-side.png | same sideways pillarbox in both (the clip is rotated 90° and nobody fixed it); monitor softer, export sharper |
| 3 | 00:00:17:10 | shot 3 (GX010474, sideways) | shot3-side.png | none visible |
| 4 | 00:00:26:12 | Spencer lower-third | lowerthird-side.png, lowerthird-zoom.png | pale vertical stripe through the "n": on the monitor it stays inside the bar, in the export it reaches above the letter so it reads "Spehcer"; otherwise same position/size/bar |
| 5 | 00:01:31:01 | Cross Dissolve shot 9→10 | dissolve-1-31-01-side.png | export's outgoing (night) layer fainter and light streaks in different places: mix not identical |
| 6 | 00:01:34:23 | shot 10 with Sharpen 100 | sharpen-shot10-side.png | sharpen strength looks the same; export darker in red by ~4 levels; sky banding present in both |
| 7 | 00:01:56:21 | graded shot 12, alpine look | alpine-shot12-side.png | export slightly cooler/denser (153/167/180 → 149/164/179) |
| 8 | 00:02:12:00 | shot 14 | shot14-side.png | export slightly darker (105/114/120 → 102/112/118) |
| 9 | 00:02:53:20 | end card | endcard-side.png, sheet-c.png | same in both: title far too big, "Copp" and "2026" cut off the frame edges; 2nd line "Thanks for watching" missing; export darker (24/28/31 → 19/22/25) |

Length: timeline 00:02:55:12 = 4212 frames = 175.675 s; file 175.6755 s. Exact.
Frame rate: 24000/1001 kept. Size 1920×1080 from 5120×2880 sources.
Loudness (volumedetect): mean -18.1 dB, max 0.0 dB (372 samples in the 0 dB bin).
