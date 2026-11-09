# K7 audit: is the first pass over-indexing on GoPro acceleration events?

Karl, 2026-11-09 (REDESIGN-GOAL K7): *"make sure you're not over-indexing only on finding those super acceleration
events … There's also the fun B-roll I want to include too, like where people are sitting on a chairlift and they say
pocket pizza … So be careful of the heuristic."*

Measured on 2026-11-09 against `c6fa602` (= `a8f707a` + K7), read-only: the Killington bin as judges 10-16 left it
(`~/roughcut-loop/judge1*/work`), the labelled test set (`research/R12-understanding/eval/killington-moments.checked.json`,
which already has Karl's exact example: `talk-03-pizza`, CLIP_03 18.3-21.0, and `lift-03-ride`), no model call, no GPU.
Script: `research/R12-understanding/eval/k7_cut_mix.py`.

## Short answer

- **The cut itself is not telemetry-heavy; the machinery around it is.** Of the seconds in seven fresh first cuts, 5-14 %
  sit within 1 s of a telemetry event (the bin as a whole: 9 %), 27-44 % are under speech that is not an event, and
  48-66 % are other footage. The model building the cut leans on the transcripts.
- **But the ranked list the cut is told to read first is 100 % telemetry, and on a mixed bin a phone clip can never
  reach it.** The prompt's "Events, ranked" section (14 rows, placed above the inventory, "prefer these") was 13
  "fall" rows that are speed drops (mostly ordinary stops: 2 of the 14 overlap a labelled moment) and one air. The first
  non-telemetry event is rank 60; the payload is cut at 60 and the prompt reads 14.
- **Nothing promotes the B-roll Karl means.** "Pocket pizza" is transcribed correctly, yet the speech scorer ranks it
  221st of 312 lines (it scores exclamations), no list carries talk / lift / hang-out moments, the guidance warns the
  model away from talk, and the understanding eval scores only airs and falls. CLIP_03 (the chairlift) reached 2 of 7
  first cuts; the pocket-pizza line reached 1 of 7. The labelled B-roll moments reached: 1-5 of 12 per cut.
- **Non-GoPro clips do get in** (any `.mp4/.mov/...` is ingested, heard and offered to the cut; nothing requires
  telemetry), but they have no "felt" events, the Claude check says "nothing to check" on a phone-only bin, and in a
  mixed bin their best moment is outranked by every unchecked GoPro slowdown.

So: not over-indexed in what ends up on the timeline today, but every recent wave (TELEM, VERIFY, LOCALGPU, CHECKER,
OTHERAIR, the gate's understanding line) measured and tuned events only. What is measured is what gets built, and the
B-roll has no measure. The fix below adds one and stops telemetry from owning the ranked list.

## Numbers

### Where the first cut's seconds come from

`provenance.bases[0]` of each judge's Killington EDL (the first proposal accepted on a fresh bin; generated title shots
left out). Telemetry = within 1 s of an event the checker would look at (`check: true`); speech = under a transcript line
and not telemetry; other = the rest. Labels: high + medium positives; "events" = air, fall, camera-into-snow,
camera-toss, water (13); "B-roll" = lift ride, chairlift talk, ski-patrol talk, cheers, the trail-map stop, the selfie,
the scenic view, the retelling, the bitten-by-a-bump reaction (12).

| first cut | shots | length | telemetry | speech | other | clips used | events reached | B-roll reached | pocket pizza |
|---|---|---|---|---|---|---|---|---|---|
| whole bin | | 2589 s | 9 % | 20 % | 71 % | | | | |
| judge 10 | 14 | 178 s | 6 % | 43 % | 51 % | 9 | 4/13 | 3/12 | no |
| judge 11 | 21 | 192 s | 6 % | 42 % | 52 % | 11 | 3/13 | 5/12 | **yes** |
| judge 12 | 10 | 167 s | 5 % | 30 % | 65 % | 5 | 3/13 | 3/12 | no |
| judge 13 | 11 | 170 s | 7 % | 27 % | 66 % | 7 | 4/13 | 3/12 | no |
| judge 14 | 12 | 152 s | 8 % | 34 % | 58 % | 7 | 1/13 | 1/12 | no |
| judge 15 | 15 | 156 s | 8 % | 44 % | 48 % | 9 | 5/13 | 4/12 | no |
| judge 16 | 15 | 154 s | 14 % | 36 % | 50 % | 8 | 6/13 | 2/12 | no |

(Clips used counts the bin's 12 Killington clips plus 3 the judges imported.) Judge 16 is the first cut after wave 20's
TELEM lane put telemetry into the prompt: its telemetry share doubled (6-8 % → 14 %) and its B-roll fell to 2/12. One
cut is not a trend, but it is the direction K7 warns about. Judge 16's cut spends 6 of 15 shots on CLIP_01 (the river
fall and its retelling): the model builds its "through-line" around the one big event.

### What the cut is told to read first (judge 16's bin, as the Ask reads it)

`verify.demote_ranked(events.load(...), verify state)` with the bin's telemetry registered, i.e. `payload["events"]`:

- top 14 (all the prompt shows): 14 telemetry, 0 anything else; 13 `fall` (speed drops) + 1 `jump`.
- against the labels: 2 of 14 overlap a labelled moment (CLIP_04 271 the crash into the sun, CLIP_06 30 the glade drop).
- against Claude's own verdicts on that bin: 3 confirmed, **4 rejected**, 1 unsure, 6 unchecked; the rejected ones
  (e.g. CLIP_08 229.0, rank 3) still read `felt` at the top of the list.
- every telemetry row scores ≥ 1.2 (52 of them ≥ 1.0); the best non-telemetry row on the bin scores 0.758. The first
  non-telemetry asserted row is **rank 60**.

Score bounds (`events.score_moment`, no motion peak / full peak): an unchecked telemetry slowdown 1.20 / 1.68; a
reaction two looks **confirmed** 0.90 / 1.26; confirmed faces 0.75 / 1.05; a deep-look reaction 0.78 / 1.09. A phone
clip's best possible people moment cannot outrank an unchecked GoPro stop.

### The speech side

R8's speech candidates (`audio.json` `candidates`, which feed the floor's picks): 312 on the bin. The top 8 are all
exclamations at action ("whoo yeah boy", "That was rowdy … Holy shit!", "Oh my god, it bit!"). CLIP_03's five lines,
pocket pizza included, sit at 220-224 with the flat 0.45 any 3-word line gets. Banter, a running joke or a deadpan
line scores like "Keeping the same resolution."

## Gaps, with file:line

1. **Telemetry owns the ranked list.** `roughcut/events.py:350` `FELT = 1.2` multiplies `KIND_WEIGHT` `fall` 1.0
   (`events.py:215-224`, via `FELT_KIND` `speed_drop → fall` at `events.py:383`) with `notable` true for every non-impact
   (`events.py:386-400`). A sensor number that has not been looked at outranks anything a person or a model has seen
   that is not an air or a fall (`reaction` 0.6, `faces` 0.5, `scenery` 0.1).
2. **The prompt shows only the top 14, with no diversity.** `roughcut/revise.py:196` `events_section(top=14)`, fed by
   `app/server.py:1578` `asserted_events[:60]`. No per-source, per-kind or per-clip cap, so one sensor fills it.
3. **`EVENTS_HEADER` never explains `felt`** (`revise.py:169-194` lists confirmed / unaudited / deep / fine-only /
   unsupported), and calls the list "the biggest things anyone has seen in this footage": 13 stops, read as falls.
4. **A Claude rejection does not move a telemetry row.** `roughcut/verify.py:1215-1253` `demote` holds a telemetry
   row only when the 3080 settles it and Claude did not confirm; a checker `reject` on a speed drop leaves it asserted,
   `felt`, at its rank (4 of the top 14 on judge 16's bin).
5. **No candidate type for "people having fun".** Event kinds are air / fall / crash / reaction / faces / action /
   scenery; nothing ranks talk, laughter, banter, the lift, a group stop. The only path for the chairlift is the
   transcript inside a 57 kB inventory.
6. **The speech scorer rewards exclamations only.** `research/tools/audio_analyze.py:305-329` `build_candidates`:
   0.45 base, +0.35 per strong marker (woo, oh my god, holy …), nothing for a repeated phrase, a question and answer
   between two voices, or laughter. The pick's speech weight is `roughcut/picks.py:66` `HEARD_GAIN = 0.8`.
7. **The guidance leans away from talk.** `revise.py:120-122` "Quiet does not mean boring … Do not rank shots by how
   much is said" was right for the airport film (B1) but has no counterweight saying banter on the lift is the film
   too. `revise.py:123-126` "look for a through-line" names a running joke, which is right, but on Killington the
   model reads the through-line as the one big event (judge 16: CLIP_01's fall and retelling, 6 of 15 shots), and a
   joke said once on a 40 s lift clip never qualifies as one.
8. **What is measured is events only.** `tools/eval_understanding.py:77` `EVENT_KINDS = ("air", "fall", "crash",
   "camera-into-snow", "camera-toss", "water")`: the 12 labelled B-roll moments (talk, lift, cheer, stop, selfie,
   scenic) are in the test set and scored by nothing. The gate's understanding line cannot see a cut that drops them.
9. **No-telemetry bins.** `verify.py:1495`: a phone-only bin skips the Claude check ("No telemetry events here");
   events come only from 4 s contact sheets (0.33 recall in R12). Phone clips still reach the inventory, so they can be
   cut on their words, but not on their pictures. (A gap for K6's understanding work more than for K7.)

## Proposed fix: wave 22 lane BROLL

**Punch (K7).** The first pass treats a GoPro event as one kind of good moment among several, not the only ranked one,
and a test proves a speech-only chairlift clip and a non-GoPro clip both reach the first cut.

**Owns.** `roughcut/events.py` (scoring constants, a `moment` source for talk), `roughcut/revise.py` (`events_section`,
`EVENTS_HEADER`, `FIRST_GUIDANCE`), `research/tools/audio_analyze.py` `build_candidates` (or a pure re-score in
`roughcut/picks.py`, no re-run of ASR), `tools/eval_understanding.py` (a B-roll line), `app/tests/test_events.py`,
`app/tests/test_revise*.py` / a new `app/tests/test_broll.py`. Not verify.py's routing (CHECKER's) beyond reading
verdicts; not server.py except the `events` slice if it must widen.

**Changes.**

1. *Telemetry is a witness, not a winner.* `FELT` 1.2 → 1.0 (a sensor number is as good as one unaudited look, not
   better), and a speed drop with no impact ≥ 3.5 g and no frames/checker agreement scores as `kind: stop` (0.3), not
   `fall`. A telemetry row Claude **rejected** drops to `UNSUPPORTED` (0.6×); one Claude confirmed goes to `CONFIRMED`.
   (Airs keep CHECKER's `unsure` rule.) DECISION for Karl only if the eval's event recall falls (see DoD).
2. *A balanced list.* `events_section` shows 16 rows in two blocks: "**Action**" (at most 8, at most 2 per clip,
   telemetry at most 6 of them) and "**People and B-roll**" (at most 8, at most 2 per clip): talk and banter lines,
   laughter, reactions, faces, the lift ride, group stops, a scenic hold. `EVENTS_HEADER` explains `felt` ("a sensor
   number nobody has looked at yet") and stops calling the list "the biggest things anyone has seen".
3. *Talk moments as candidates.* A pure re-score of the existing transcript (no ASR re-run): + for a line with a second
   voice answering within 3 s (speakers stage `S2`), + for a phrase that recurs within the clip or across clips
   ("pocket pizza" ×2), + for a question, + for laughter tokens; the lift (`telemetry lift` or a sheet `lift` claim)
   and a `stopped` stretch with speech become `kind: hangout` moments. Weight `talk`/`hangout` ≈ `reaction` (0.6),
   notable when the re-score is in the bin's top 10 %. Works the same with no telemetry, so a phone clip's banter ranks
   on its words.
4. *Guidance with a counterweight.* After "Quiet does not mean boring", add one bullet: *"Loud does not mean the
   only thing worth keeping, either. The film is the people as much as the riding: a line on the chairlift, a running
   joke, the group standing at a sign. A cut that is only jumps and falls is a highlight reel, not a trip. Aim for
   roughly a third of the cut on moments like that."* (The "a third" is provisional, `done*`, until Karl watches one.)
5. *Measure it.* `eval_understanding.py` gains a **B-roll line**: recall of the 12 non-event positive labels by the
   ranked list's top 16 (offline, no call), and, when a cut is given (`--cut EDL`), by the cut. Baseline today: the
   list's top 14 reaches **0/12** (all telemetry; CLIP_03 is absent). The gate reports it AMBER below the bar, never
   RED, until Karl has watched one cut made with it.

**Tests (mocked backends, no model call).**

- `test_broll.py::test_chairlift_talk_reaches_the_list` — a fixture bin of two clips: a GoPro clip with three felt
  speed drops and an air, and a 40 s chairlift clip with only a transcript (two voices, "you got the pocket pizza?" /
  "pocket pizza!") and a `lift` telemetry stretch. `events_section` must list the chairlift line in "People and B-roll",
  and at most 6 telemetry rows overall.
- `test_broll.py::test_phone_clip_reaches_the_list` — the same with the chairlift clip as `IMG_0420.MOV`, no telemetry
  sidecar at all: its line still ranks, and `telemetry_events` returning `[]` changes nothing else.
- `test_broll.py::test_first_cut_keeps_both` — `revise.originate` on that bin with a scripted backend that returns a
  plan using whatever the prompt ranks first in each block; `validate_plan` passes, and the plan has a shot on the
  chairlift (or phone) clip. Plus a prompt assertion: the chairlift line appears before the inventory.
- `test_events.py` — a Claude-rejected speed drop falls below a confirmed reaction; a speed drop without an impact is
  `kind: stop`; an air stays a jump either way.

**DoD (fresh output in the lane).**

- The three `test_broll.py` tests and `test_events.py` / `test_revise*` green.
- Offline eval on the frozen Killington inputs: B-roll top-16 recall ≥ 6/12 (from 0/12); air recall and event recall
  **not lower** than the current baseline (0.80 / 0.89 in the replay); 1 of 19 negatives asserted, not more.
- One live first pass on a Killington work copy (Hybrid, within CHECKER's call ceiling): `k7_cut_mix.py` on its EDL
  shows the telemetry share ≤ 20 %, B-roll reached ≥ 4/12, and CLIP_03 in the cut. Karl watches it before the "a third"
  in the guidance loses its asterisk.

**Not in this lane.** A real laughter detector (needs the AST tagger R9 never installed; the labels have no solid
laugh), music-led B-roll, and the phone-only Claude check (K6's understanding work).

## What I did not do

No code change to the pipeline: the scoring and prompt changes move every first cut, so they need the eval line and a
live A/B, which is a lane's work, not an unattended audit's. Nothing ran on the GPU; no model was called; no other
lane's worktree, the 8766 preview or Karl's work dir was written.
