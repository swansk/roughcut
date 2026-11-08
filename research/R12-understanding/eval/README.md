# Killington footage-understanding eval set (DRAFT)

Status: draft for Karl to check. Nothing here was written into the repo, ~/footage, ~/work or /mnt/roughcut.

Files
- `killington-moments.json` - 47 labelled moments (28 positive, 19 negative), 1-3 thumbs each in `thumbs/` (480 px PNG).
- `killington-find.json` - 22 Find queries with answer windows (the answers reference moment ids and carry explicit clip/start/end).
- `build.py` - the single script that produced both JSON files and the thumbs (re-run: `python3 build.py`; needs ffmpeg, reads the proxies only).

## How it was built
1. Read R10 and R11, the 12 `CLIP_NN.audio.json` transcripts, the coarse/fine/deep sidecars (as rendered in `first_prompt.txt`) and the 10 Hz telemetry series in `/mnt/roughcut/work/app-redesign/telemetry/killington-neutral/*.telemetry.json` (accel, speed, tilt, freefall runs, impacts, GPS stops). Telemetry was only read, not re-parsed.
2. For every candidate I looked at frames myself: one accurate ffmpeg seek per frame from the 720p proxies (`work/app-redesign/proxies/...`, 59.94 fps, same timeline as the masters), contact sheets at 0.1-1 s steps, timestamp burned in. A first version used ffmpeg's `fps`+`tile` filter and its labels drifted by up to one interval; those sheets were discarded and every label here was re-checked with per-frame seeks.
3. Spans: where telemetry gives an event, start/end are pop-to-landing (freefall start minus ~0.05 s to the landing impact). Elsewhere spans are the visible span, to about 0.5 s. Do not treat spans as tighter than +-0.25 s.

## Solid vs guess
Solid (confidence high, checked by eye and/or telemetry): the five clear airs (CLIP_12 46.6 and 50.6, CLIP_02 102.1, CLIP_05 18.0 by telemetry, CLIP_10 25), the CLIP_01 tumble and burial, the CLIP_04 crash, the CLIP_11 fall, the lift ride, the trail-map stop, and all `negative` rows marked high.
Medium: CLIP_04 224.6 and CLIP_06 30.2 airs (tiny, glade or lip drops), words-only items (cheers, pocket pizza, "fallen into a stream"), scenic highlight, CLIP_11 252-260 negative.
Low / guesses: CLIP_02 9-12 (fall vs camera toss), CLIP_10 31 (someone down), CLIP_04 87 (stumble), laugh, "Good fall Spence".
Gaps I could not fill: **there are no solid laughs.** ASR marks none, I cannot hear audio, and the AST tagger (R9) is not installed and found nothing on the earlier bin. The one `laugh` row is a low guess; Karl's ears are the only source. Cheers are word-based ("Woo!").
Ski patrol (CLIP_09 110-123) is speech only; no patroller is on screen.
`needs_karl: true` marks 13 rows, mostly who/what-was-funny and the two ambiguous falls.

## Scoring rules (suggested)
- Positive moments (polarity positive, kinds air/fall/camera-into-snow/...): found if a predicted span of a compatible kind overlaps the labelled span after widening the label by 0.5 s each side. Compatible: air~air; fall/crash/camera-into-snow interchangeable; lift~lift; talk/cheer/laugh/reaction matched on span only (ignore kind among those four).
- Report recall per kind and per confidence; headline numbers on `high` only, `medium` as a second line, ignore `low` (or report separately).
- False alarms: any predicted event of kind air, fall or crash (or water) that overlaps a negative row whose `refutes` contains that kind (same +-0.5 s rule) is a false alarm; also count predicted air/fall/crash anywhere that overlaps no positive row and no low-confidence row. Rows refuting kinds: neg-* rows and the `lift-07-station`, `stopped-06-sign`, `neg-04-22` (a reaction), `water-01-none`.
- Because precision can only be measured where I looked, quote false alarms per minute over the labelled-negative minutes, not over the whole bin (unlabelled footage may contain real events I missed).
- Find: hit@3 if any of the top 3 results overlaps any answer window with IoU >= 0.3 (clip must match; compute IoU on seconds). Skip queries with confidence `low` in the headline and report separately.  Answer entries without a `moment` key are windows I added by hand (word queries, or places I viewed but did not label).
- Never score a model against the `fall-10-31`, `fall-02-10`, `other-04-87`, `laugh-02-tall` rows as required finds; they are for review.

## Find with the 3080 (wave 20, FIND3)
- `query-vectors.json`: PE-Core text vectors (4 decimals) for the 22 Find queries, written by `python3 tools/eval_understanding.py --refresh-vectors` on the 3080 (re-run it whenever a query changes). With it and the bin's `<work>/local/<bin>/*.embed.npy` the gate scores the lexical + frame-search union offline, no GPU; with either missing (or the embeddings not yet covering every clip) that line says "skipped" and cannot fail the gate.
- `baseline.json` `find_union_*`: 0.789 any-overlap hit@3, 0.474 IoU>=0.3 on the 19 non-low queries (lexical alone 0.632 / 0.421). The Claude rerank is never in the gate: `--rerank [--rerank-cache FILE]` runs it, one call per non-low query.
- Rerank measured 2026-11-08 (FIND3): Claude (verify role, isolated CLI call, 1 middle thumbnail per candidate for the top 6, 19 calls, $1.39 = $0.073 per call) over the union's top 10 scored 0.684 any-overlap / 0.474 IoU: it lost f03 and f17 (a 0.3 s air or a lens-in-snow moment is not legible in one frame). `find.RERANK_DEFAULT_ON` stays False until a variant beats 0.789.
