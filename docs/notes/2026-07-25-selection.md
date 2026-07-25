# Selection pass over B1 (Copper) — working log

Live document. Updated as the pass runs, not written up afterwards.

**Goal:** an ordered segment list for the agreed brief — a 2–3 minute edit for the friends who
were there, emphasis on the skiing, just enough travel at the top to establish the trip, loose
and fun rather than cinematic, favouring faces and reactions over scenery.

**Method.** Visual analysis is done by reading the contact sheets directly (2s sampling, built
last session, `--orient none` so B1's spurious rotation metadata is ignored). This is R7's
Policy B prototyped by hand: coarse visual sample first, refine where something looks worth it.
Each clip is judged on the sheet *and* its transcript together — R8 showed neither modality
alone gets this bin right, since the skiing is quiet and the talking is indoors.

**Inputs**
- Contact sheets: `/mnt/c/Users/karl/Documents/Roughcut Labeling/B1-copper/*.jpg`
- Audio sidecars: `~/work/audio/*.audio.json` (transcript, candidates, loudness)
- Junk exclusions: the 9 dark clips in `benchmarks/labels/B1-luma.json`

## Status

| Step | State |
|---|---|
| Review 17 sheets + transcripts | **done** — all 17 |
| Per-clip inventory | **done** |
| Segment selection | **done** — EDLs in `research/edl/` |
| Ordering / structure | **done** — two variants |
| Assemble + render | **done** — both rendered and verified |
| Self-critique vs R6 | **done** — one iteration applied |
| Karl watches two variants | **waiting on Karl** |

## The correction that changed the plan

A first pass over 7 of 17 sheets concluded the bin was mostly travel footage and that the
brief's "emphasis on the skiing" might not be supportable. **That was wrong.** With all 17
reviewed, the split is:

- **on-mountain: 10 clips, 277.0s (70%)**
- travel/airport/plane: 7 clips, 121.2s (30%)

The error was classifying the base-area and chairlift clips (488, 491, 492, 495, 496) as
"lodge/hangout" from their audio, which is all banter. They are on the mountain, in ski gear,
mid-day. R8's Result 4 table has been corrected; its conclusion (audio points away from the
skiing) survives and is in fact sharper: audio splits its candidates 50/50 across a bin that is
70/30 on-mountain by duration.

## The actual spine of this footage

Reviewing all 17 sheets surfaced something no single clip shows: **there is a running joke about
a gallon of milk, and it runs the length of the trip.** It appears in the transcript of 6 clips
and on screen in 5:

| clip | the milk |
|---|---|
| 488 | "How's your milk, Spenny? Let's see if you take a good sip" — jug in hand at the base |
| 491 | "I'm milked out. He's milked out." |
| 492 | riding the chairlift holding the jug — "He's so milked, that's it" |
| 494 | POV run through trees **ends** on him holding the jug up — "He's got the milk" / "high five" |
| 495 | the payoff: 60s of chugging it in a chair at the base — "I became the milkman today. **We created a legend.**" |

This matters because the brief says *loose and fun rather than cinematic* and *the people in it
are the point*. A pure ski montage would throw away the one thread that actually connects the
trip and is the thing the group will remember. It also isn't in tension with "emphasis on the
skiing" — the milk is *carried through* the skiing (lift, tree run, base), so the thread and the
sport occupy the same footage rather than competing for screen time.

## Per-clip inventory

`place`: A = airport/plane, M = on-mountain. Quality flags note anything that constrains use.

| clip | dur | place | what happens | quality | keep |
|---|---|---|---|---|---|
| 474 | 31.5s | A | Airport bar. Group at the counter, drinks, a laptop open. Faces to camera, laughing. "Look at these silly guys doing their little program thing" / "company secrets" | well lit, faces clear | **yes** — trip opener |
| 475 | 21.2s | A | Same bar, Spencer arrives with backpack. "Spencer, welcome" / "You didn't give me a beer" | good | **yes** — arrival beat |
| 476 | 18.6s | A | On the plane, laughing at a laptop while the safety PA plays. "You need to put that away" / "I'm consulting right now" | good, funny | **yes** — best travel beat |
| 477 | 16.6s | A | Dark night cabin, blue lighting. "Hey, hey, hey, you got any games on that?" | **motion-blurred, dark** | audio only |
| 478 | 11.3s | A | Dark cabin, laptop again. No speech | **dark**, redundant with 476 | no |
| 483 | 10.8s | A | Airport walkway, two of them walking at camera with luggage. "B-roll right there" | clean, faces | **yes** — transition |
| 486 | 11.2s | A | Night parking lot, walking to the van. "He's on a journey to the porcelain throne" | dark but real (the clip the luma threshold false-positives on) | short use |
| 487 | 16.7s | M | Skier traversing a ridge away from camera, big open scenery, camera static | clean, scenic | **yes** — mountain establisher |
| 488 | 15.2s | M | Base area, brown hoodie + helmet holding the milk jug, people milling. "How's your milk, Spenny?" | good | **yes** — milk beat 1 |
| 489 | 13.9s | M | Skier hits a small jump and lands. **"Whoo" at 1.2s** — 2nd-ranked audio candidate in the bin | good, distant subject | **yes** — the one clear trick |
| 490 | 31.4s | M | Static camera, rider carving down toward and past the lens, lift line behind | **best pure ski shot** | **yes** |
| 491 | 5.0s | M | Short. Walking on snow. "I'm milked out. He's milked out." | good | **yes** — milk beat 2 |
| 492 | 20.9s | M | Chairlift ride, jug in hand, trees below. "He's so milked, that's it" | good | **yes** — milk beat 3 |
| 493 | 25.8s | M | Moving POV following skiers down a groomer | **glove/lens obstruction at 0–2s and ~20s** | middle only |
| 494 | 41.4s | M | POV run through trees, **ends on him holding the jug up** — "He's got the milk" / "Yeah, high five" | good; a blown-out frame at ~40s | **yes** — motion + payoff |
| 495 | 60.3s | M | The milk chug. 60s in a chair at the base, friends around. "I became the milkman today. We created a legend" / "That goes so fucking hard" (top audio candidate) / "this is so nasty" | good | **yes** — centrepiece |
| 496 | 46.4s | M | Lounging in base-area chairs. No speech at all, static, nothing develops | fine but inert | 2–3s at most |

## Notes and decisions

- **Two variants, not one.** HANDOFF's stated mitigation against iterating into something that
  passes every check and is still flat. The two readings of the brief differ materially, so
  build both and let Karl react to a choice:
  - **Variant A — "the trip":** brief-literal. Travel cold open, then skiing, milk as recurring
    texture rather than subject. Skiing gets the most screen time.
  - **Variant B — "the legend":** the milk thread is the spine and the skiing is what happens
    around it. Ends on "we created a legend".
  Both draw from the same segment pool below; they differ in ordering and in how much of 495 survives.
- **496 is the only real filler in the bin.** 46s of nothing developing. Everything else earns
  at least a few seconds.
- **477/478 are the only clips lost to quality** (dark + motion blur). 477's line is good enough
  to use as audio over another shot if a variant wants it.
- Loudness varies −22.1 to −14.6 LUFS across the bin, so levels must be matched at assembly —
  measured per clip in the sidecars, no need to re-derive.

## Render

`research/tools/assemble.py` cuts an EDL, re-encoding every segment to identical parameters
(1920×1080, 23.976, yuv420p, AAC 48k stereo) and concatenating with the concat demuxer. Two
non-obvious choices are load-bearing and commented in the source: `-noautorotate` on every
input (B1's rotation side-data is spurious), and loudness matched by a **fixed per-clip gain**
derived from the R8 sidecars rather than per-segment `loudnorm` — five consecutive segments come
from GX010495, and a per-segment normaliser would re-level each one independently and pump.

```
uv run research/tools/assemble.py research/edl/B1-variantA.json \
  --footage ~/footage/copper-02-2026 --sidecars ~/work/audio -o ~/work/cuts/copper-variantA.mp4
```

Outputs (also copied to `/mnt/c/Users/karl/Documents/Roughcut Labeling/cuts/`):

| variant | title | duration | segments |
|---|---|---|---|
| A | The trip | **1:54** (114.59s) | 20 |
| B | The legend | **1:50** (110.57s) | 18 |

### Technical verification (fresh output, both variants)

| check | result |
|---|---|
| A/V sync | 18 ms drift, under one frame at 23.976 |
| planned vs rendered duration | +0.19s / +0.17s |
| integrated loudness | −15.5 LUFS both (target −16) |
| loudness range | 3.5 / 3.9 LU |
| black or frozen runs > 0.5s | none |
| stream layout | video + audio only, telemetry and timecode tracks dropped |

## Self-critique against R6 (draft rubric — the bar is provisional)

Scored on variant A. Timestamps are what the next iteration acts on, per the rubric's own note.

| # | criterion | score | what drove it |
|---|---|---|---|
| 1 | Coverage | 4 | Every beat I could find is represented. Can't score 5 — I can't know what Karl remembers that the footage under-shows. |
| 2 | Selection | 4 | The 490 carve uses the only window without the red pole across frame; 493 avoids the glove at 0–2s. Not 5: GX010496 (46s) contributes only 3.5s and nothing else was available to replace the flat stretch at 0:44. |
| 3 | Pacing | 3 | **The weakest criterion.** Two long POV segments (493, 494) ran back-to-back; trimmed by 3.5s and 1.6s in the iteration. The payoff still holds one framing from 1:20 to 1:50. |
| 4 | Structure | 4 | Travel → mountain → payoff, and the milk thread pays off in the last act rather than being scattered. |
| 5 | Cut cleanliness | 3 | Cuts land on transcript boundaries where speech drove the choice, but ski cuts land on 0.5s sheet resolution and are not frame-checked against the action. |
| 6 | Audio comfort | 4 | −15.5 LUFS, LRA 3.5, no level jumps by construction. Not 5 — no wind-suppression work, and one segment's room tone differs audibly from its neighbour. |
| 7 | Goal fit | 4 | Skiing leads by screen time, travel is a short top, faces and reactions are favoured over scenery. |
| 8 | Length discipline | 4 | 1:54 against a 2–3 min brief — 5% under the bottom of the window. |

Mean **3.75**, no criterion below 2 → clears the provisional bar. That bar is uncalibrated, so
it means "not obviously broken", not "good".

### The iteration this drove
- Dropped GX010477 entirely (dark **and** motion-blurred; its line is funny but the shot is
  unusable, and 476 already covers the plane).
- Trimmed 493 by 3.5s and 494 by 1.6s — the two back-to-back POV runs were the pacing problem.
- Took 495's opening out at 10.0s, ending on "we created a legend" rather than trailing into
  "and now… it's over".
- Added 475's arrival beat (3.8s) so tightening didn't drop the cut below the brief's window.

Stopped after one pass, deliberately. HANDOFF's stated failure mode is iterating into something
that satisfies every checkable rule and is still flat; past this point the open questions are
taste, which is Karl's half of the loop.

## What Karl needs to decide

1. **A or B** — do you want the trip with the milk in it, or the milk story that happens on a
   ski trip? Reacting to the pair is the point; notes on either come back as a revision pass.
2. Both land ~1:50–1:54, just under the 2–3 min brief. There is material to extend with
   (more of 495's chug, more lift and base-area footage) if you want it nearer 2:30 — I held
   back because it would be padding, not because the footage ran out.
3. **D7 is still open**: 21 commits, one disk, no remote.
