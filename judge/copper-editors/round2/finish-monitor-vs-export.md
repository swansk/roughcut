# Monitor vs export, round 2 (Quick 1080p, copper-v1-quick.mp4)

Program monitor captured parked on each timecode (typed into the Program timecode, 5–6 s settle),
picture area 840×472. Export frame pulled at the same frame index n (t = n·1001/24000; seek just before
the frame). Frame alignment checked by matching ±4 frames: 0-frame offset at the title, dissolve and dip.
`wysiwyg/side-<tc>.png` = monitor LEFT, export RIGHT; `wysiwyg/sheet.png` = all ten.

| # | Timecode | What | Framing / rotation | Text position | Mean RGB monitor → file | Other differences |
|---|---|---|---|---|---|---|
| 1 | 00:00:02:07 | opening title over shot 1 (GX010474) | same; upright (after I reset the first pass's -90°) | same (x 50 %, y 30 %) | 146/126/96 → 144/125/94 | none visible |
| 2 | 00:00:04:00 | shot 1, GX010474 (sideways last round) | same; upright in both | – | 149/127/93 → 147/125/90 | none |
| 3 | 00:00:09:00 | Spencer lower third (shot 2) | same | same, bottom-left inside title-safe; letters clean | 142/118/74 → 140/116/72 | none |
| 4 | 00:00:30:00 | shot 5, night | same | – | 14/11/4 → 13/10/2 | none |
| 5 | 00:00:40:00 | shot 7 snow | same | – | 151/168/181 → 148/166/181 | export a touch darker in red |
| 6 | 00:00:50:00 | shot 8 graded (cooler ×2, darker) | same | – | 133/149/165 → 130/147/164 | grade strength looks identical |
| 7 | 00:01:06:18 | Cross Dissolve 9→10 (AI-added, 12 f) | same | – | 109/121/130 → 106/120/129 | mix identical |
| 8 | 00:01:13:00 | shot 11 | monitor 150 % / file 100 % | – | 95/100/103 → 102/109/114 | expected: I scaled it to 150 % while the file rendered; the file is the cut as of Export (as the dialog promised) |
| 9 | 00:01:29:20 | Dip to Black (AI-added, 1 s) | – | – | 0/0/0 → 0/0/0 | black frame in both; 00:01:29:14 also matches (57/66/76 → 53/64/74) |
| 10 | 00:01:34:20 | end card, two lines | same | same, inside title-safe, both lines | 50/57/64 → 47/54/61 | none |

Colour: on every frame the file is 2–3 levels darker in red and 1–2 in green than the monitor (same as round 1, FINISH-19).

GX010484 and GX010492 are not in this cut (the first pass did not use them), so I could not compare them in the film.

Length: timeline 00:01:35:11 = 2291 frames = 95.554 s; file 95.554 s. Exact.
Frame rate 24000/1001 kept. 1920×1080 from 5120×2880 sources.
Sound (ffmpeg ebur128 read of the file): integrated -15.8 LUFS, LRA 4.7 LU, true peak -1.4 dBFS (was 0.0 last round); AAC 48 kHz stereo ~195 kbps.
