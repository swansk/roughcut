# Claude side of the footage-understanding bake-off (Killington bin) — 2026-11-07

Files (all in ~/roughcut-loop/understand/claude-side): taskA.py/.json, windows.py, sheets.py + sheets/ (17 levelled strips), runb.py, calls.jsonl (every call: usage, cost, latency, answer), scoreb.py, taskB.json.
Nothing outside this folder was written (footage, ~/work, /mnt/roughcut, repo all read-only; bench code imported by path). GPU untouched, CPU under nice 19.

## Task A — today's Claude index scored with bench/score.py rules (no model calls)
"Today's index" = the merged per-clip moments the board/Ask/Find read (visual.json coarse + fine close looks + CLIP_11 deep look, merged with the repo's own `events.merge_moments`) and the ranked `visual/killington-neutral/events.json` (188 events; 37 are jump/fall/crash). jump -> air, fall/crash -> fall. Same ±0.5 s rule, same 2.55 labelled-negative minutes, same 19 negative rows (18 + CLIP_02 camera-toss, as score.py treats it).

| | recall high (9) | air (7) | fall (5) | karl_ok (4) | FA on negatives /neg-min | negative rows asserted (of 19) | preds |
|---|---|---|---|---|---|---|---|
| Claude index: all jump/fall/crash moments (merged) | 0.33 | 0.29 | 0.60 | 0.50 | **5.09** (13 hits) | 13 | 35 |
| Claude index: notable-only | 0.22 | 0.29 | 0.20 | 0.00 | 4.70 | 12 | 28 |
| events.json ranked, all | 0.33 | 0.29 | 0.60 | 0.50 | 5.48 | 14 | 37 |
| events.json, demoted rows removed (what the board shows) | 0.22 | 0.29 | 0.20 | 0.00 | 5.09 | 13 | 31 |
| events.json, confidence=high only | 0.22 | 0.29 | 0.20 | 0.00 | 0.0 | 0 | 3 |
| *Local: telemetry candidates only (RESULTS.md)* | 0.78 | 0.86 | 0.60 | 0.50 | 2.74 | - | - |
| *Local: Qwen3-VL-8B CHECK raw / lev (thr 0.5)* | 0.22 / 0.22 | 0.00 | 0.40 | 0.00 | 1.57 / 0.78 | - | - |
| *Local: Qwen3.5-4B CHECK raw / lev* | 0.22 / 0.00 | 0.00 | 0.40 / 0.00 | 0.00 | 0.78 / 0.00 | - | - |

Reading:
- Claude's index asserts 13 of the 19 negative rows (12 of the 18 true negatives, plus the camera toss as a fall). The unasserted ones are the four neg-ride rows, neg-04-254 and neg-11-204 (merged view). Every R10-style false claim is still in the inventory: CLIP_05 jump at 8 s, CLIP_06 crash 204-220, CLIP_04 fall 22-23, CLIP_11 252-260 "wipeout", CLIP_07 4-8/14-16/28-30/168-196, CLIP_12 1.4 crash, CLIP_09 120/192.
- The only high-confidence-tagged jump/fall/crash events (3) are right but cover 2 of 9 high positives; the unflagged rest is noise. "Confidence" is therefore a usable filter for precision but destroys recall.
- Recall numbers are flattered: median predicted span is 4.0 s (25 of 37 >= 4 s) against 0.4-1 s airs. It is span-luck, not localisation. Misses are all the real telemetry airs (CLIP_12 46.6/50.6, CLIP_05 18, CLIP_02 102, CLIP_06 30); the 2 air hits (CLIP_04 224, CLIP_10 25) are 4 s windows. The coarse 1 s sampling cannot see a 0.3 s air.
- Local telemetry alone beats today's Claude index on recall (0.78 vs 0.33) and false alarms (2.74 vs 5.09 per neg-min); local VLM CHECK lowers FA but also recall.

### Find (today's, offline)
The model Find (`find.find`, role judge = Opus, one call over the whole inventory) needs a model call, so it was not run. The free layer that ships with it, `find.lexical` (word match over transcripts + visual moments), was run over the real inventory for all 22 queries (non-low 19):
| | hit@3 strict IoU>=0.3 | hit@3 any overlap |
|---|---|---|
| Today's lexical Find (non-low, 19) | 0.37 (7/19) | 0.58 (11/19) |
| Today's lexical Find (low, 3) | 0.00 | 0.33 |
| *Local PE-Core-L14 raw (RESULTS.md)* | 0.21 | **0.68** |
| *Local SigLIP2 raw* | 0.05 | 0.37 |
Lexical wins on strict IoU (its windows follow the sentence/moment spans) and is within one query of PE-Core on overlap; they miss different queries, so lexical-then-embedding (or union of top-3s) is the obvious combo. Note: no 'karl' field exists in the Find checked file, so no owner-confirmed split for Find.

## Task B — Claude as the CHECKER (23 calls, $0.445 total, ~3.7 min wall)
Setup: one image per window: 24 frames at 8 fps (nearest cached 10 fps grid frame, labelled with its true clip time), ±1.5 s around the candidate, GRAV-levelled with the bench's roll correction, 6x4 grid of 261 px cells = 1566x584 px. `claude -p --output-format json --model ... --tools Read --allowedTools Read --strict-mcp-config --mcp-config '{"mcpServers":{}}' --disable-slash-commands --no-session-persistence` with `inference.cli_env()` (the repo's OAuth token); image passed by path and read with the Read tool (the repo's pattern), so each call is 2 turns. JSON verdict {confirm|reject|retime, kind, start, end, confidence, why}. The claim shown to Claude comes from telemetry or the old Claude index, never from the labels. 17 windows: 7 airs, 4 falls/toss (fall-01-68, camera-into-snow 04-270, fall-11-143, camera-toss 02-10), 6 negatives (CLIP_05 8, CLIP_06 204, CLIP_04 22, CLIP_11 256, CLIP_07 6, CLIP_12 1.5). "With telemetry" adds: accelerometer min/max, free-fall runs + landing peak, >5 g impacts, GPS speed list, per-frame roll list. "Without" keeps the claimed kind/time but drops all numbers.

Correct = positive: not rejected, right kind family, span within ±0.5 s of the label; negative/toss: rejected or reclassified to non-claimed kind.

| window | truth | Sonnet + tel | Sonnet no tel | Opus + tel |
|---|---|---|---|---|
| air-05-18 | air | confirm 17.97-18.3 OK | | |
| air-12-46 | air | confirm 46.62-47.01 OK | confirm 46.05-47.35 OK | confirm OK |
| air-12-50 | air | confirm OK | | |
| air-02-102 | air (karl ok) | confirm 102.1-102.5 OK | retime -> **fall** 102.6-103.9 WRONG | |
| air-04-224 | air (medium) | confirm OK | | |
| air-06-30 | air (medium) | confirm 30.17-30.5 OK ("not conclusive from frames") | | confirm OK |
| air-10-25 | air (no telemetry) | **reject** WRONG (see note) | | |
| fall-01-68 (karl ok) | fall | retime fall 70.3-71.0 OK (onset earlier than claim) | | |
| cam-snow-04-270 | camera-into-snow | retime fall 269.85-271.87 OK | | |
| fall-11-143 | fall | confirm high OK | confirm high OK | |
| toss-02-10 | camera toss (not a fall) | retime camera-toss OK | | |
| neg-05-8 | no air | reject high OK | reject OK | |
| neg-06-204 | no crash | reject high OK | | |
| neg-04-22 | no fall | reject OK | | |
| neg-11-256 | lying down (karl ok) | **confirm fall, low conf** WRONG (its own why: "aftermath, onset not in window") | | |
| neg-07-6 | no air | reject high OK | | |
| neg-12-1 | no crash | reject high OK | | |

Accuracy, Sonnet with telemetry: airs 6/7 (86 %), falls/camera-into-snow/toss 4/4, negatives 5/6 (83 %); overall 15/17 (88 %). 5 of the 6 negative claims that the old index made are killed, including the R10 ones (CLIP_05 8, CLIP_06 204, CLIP_04 22, CLIP_07, CLIP_12 1.4), and the camera toss was correctly named a toss.
Without telemetry (4 windows): 3/4 (air-12-46 OK, fall-11-143 OK, neg-05-8 OK, air-02-102 wrong). With telemetry the same 4: 4/4. Too few to be significant, but the one flip is the interesting one: on air-02-102 the frames alone do not show the skis leaving the snow and Claude reads the tumble 0.5 s later as the event; with the 0.34 s free-fall number it confirms the air.
Opus (2 airs, with telemetry): 2/2, same spans as Sonnet, confidence medium both, 2.0x cost ($0.034-0.050 vs $0.018-0.026) and +1.5-6 s latency. No evidence Opus buys anything on these; not enough to prove it doesn't (no hard negatives run on Opus).

Notes on the two Sonnet misses:
- air-10-25: label = "skier in the air directly ahead of the camera" (air by another rider; no free-fall in the wearer's accelerometer, 0.72 g min). My SYSTEM prompt defined air as the wearer's skis leaving the snow and warned about other riders, so Claude correctly said "the airborne skier is another person, wearer not airborne". Scored wrong against the label as written; arguably a label-scope issue (is a rider ahead an 'air' moment for the edit?) and a prompt-design effect. Without that line it likely confirms. Worth Karl deciding whether other-rider airs count.
- neg-11-256: Claude read a real "down in snow, gloves over lens" scene as a fall aftermath but said low confidence and that the fall onset wasn't in the window. Karl's check says lying-down; the frames of a person lying down are visually ambiguous with a just-completed fall. This is the case where an asserted-low-confidence answer must be treated as "unsure", not as a claim.
- Telemetry anchoring: in the confirm answers the "why" frequently quotes the telemetry numbers. With telemetry, Claude never rejected a telemetry-supplied air; whether it would reject a false telemetry air was not tested (no negative window had a free-fall run; the 7 bench false alarms are impact/speed candidates, not airs). That is the main untested risk of the hybrid.

Cost/latency per call (Sonnet): $0.013-0.026 (median ~$0.016), 5-13 s wall, 2 turns, ~2.9k cache-creation + ~8.3k cache-read tokens (system prompt + tool schema + image), 230-720 output tokens. Telemetry text adds ~600 input tokens (+~$0.003). Opus $0.034-0.050. Total spend $0.445 on the owner's subscription (nominal API-equivalent).
Projection: at ~$0.016 and ~9 s per window, checking every telemetry candidate in 12 clips (~43 min footage, 134 candidates: 48 impacts, 48 speed drops, 24 airs, 14 stops) is ~$2.1 and ~20 min sequential (merging near-duplicate candidates would cut it); checking only the ~20 airs-and-falls worth keeping is ~$0.30.

## Read on the hybrid routing rule
1. Telemetry decides WHEN (it alone had 0.78 high recall; the Claude index had 0.33 and local VLM 0.22). Never rely on the coarse/fine index to find airs: it asserts 13 of 19 negatives and misses every 0.3 s air.
2. Airs: telemetry free-fall run (>=0.25 s below 0.5 g followed by a >2.5 g landing) is the detector; local VLM P(yes) is a coin flip and cannot veto; send to Claude only as a cheap sanity strip when the label will be shown to the user, and expect "confirm" (6/6 telemetry airs confirmed). Airs with no free-fall (CLIP_10 25, other-rider airs) need a Claude pass over frames, with a decision on whether other riders count.
3. Falls/crashes: telemetry impact + speed-drop candidates are noisy (2.7 FA per neg-minute; camera handling gives 10-14 g impacts like a real fall). Local Qwen3.5-4B CHECK at ~0.2 as a first filter (AUROC ~0.9 on labelled windows), then Claude on whatever survives or whenever the band is 0.2-0.9: Claude separated fall vs camera-toss vs scene-of-someone-else and rejected the old false claims 5/6.
4. Everything the old Claude index claims (jump/fall/crash with no telemetry support) must be re-checked or dropped before it reaches the board: Claude-as-checker with the strip rejects them cheaply (5 of 6 here at $0.016 each), whereas the claim alone is wrong 12 of 18 times.
5. Low-confidence Claude answers ("aftermath", "not conclusive") should be treated as unsure and surfaced to Karl, not as claims. Use a 3 s strip with the telemetry text by default; Sonnet is enough, Opus only for escalation.
6. Find: run lexical first (free, best strict hit), PE-Core top-k second, Claude only to rerank/verify the union.
Caveats: n=17 windows, 7 airs, one rater (me) built the windows, windows were built from the label geometry (centre within ±1 s of truth) which makes the checker's job easier than a blind scan; no false telemetry air was tested; no-telemetry n=4.
