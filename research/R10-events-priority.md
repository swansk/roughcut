# R10 — Sorting what was seen, and what a closer look is actually worth

**Status:** run and closed on the Killington bin, 2026-08-23. Tooling:
`roughcut/events.py`, `research/tools/event_scan.py`,
`research/tools/visual_pass.py --windows`.

**Question.** Karl, on the 20-shot revision proposal
(`~/work/app/asks/killington-neutral/ed8134bb.json`): *"You missed some cool jumps — this is
likely due to limited keyframe analysis and the lack of a workflow / algorithm that applies
sort / priority following a granular keyframe analysis on the first pass."* And, of the same
proposal: *"Person down in snow was CORRECT — this is a good example of proper keyframe
analysis."*

Two claims, two studies in one. Does denser sampling find the jumps the 4s visual pass missed
(**the miss**)? And does a priority order over what was seen put the right things first
(**the sort**)?

**Corpus.** Three Killington clips chosen for airs — CLIP_04, CLIP_07, CLIP_11, 956s of
footage — plus the whole 12-clip bin for the ranking. All reads are against the 720p proxies:
the thumbnails are 480px either way, the proxy is already oriented, and seeking a 5.3K HEVC
master for an eight-second window costs more than the entire motion scan.

**Reference.** My own eyes on 1s contact sheets, for nine adjudications. There is no labelled
ground truth for this bin and this study did not have the budget to make one; nine hand-checked
windows is a small, deliberately hard sample (every contested or newly claimed event), not a
recall estimate.

---

## Result 1 — a motion track is free, and it finds the busy seconds

`scdet`'s mean absolute frame difference over the proxy at 10 Hz, one ffmpeg pass, no model
call. Combined with the R8 onset track by `max()` rather than a sum — a chase-cam jump barely
moves the audio and a landing heard as a crunch can happen just off frame, so demanding both
finds neither — and both as **robust z-scores**, because a POV ski run is high-motion
everywhere and a lift cabin is not, so an absolute threshold would find every event in one clip
and none in the other.

| | |
|---|---|
| scan cost, 12 clips (44 min) | ~4 min wall, **$0** |
| scan cost, 3 clips | 62s including ffmpeg |
| track rate | 10 Hz, matching the audio sidecars sample-for-sample |
| candidate windows at `--limit 8` | 8 / 7 / 7 on CLIP_04 / 07 / 11 |

The peaks land where something changes: CLIP_04 at 254.1s (z3.4) and 271.3s (z3.9), CLIP_11 at
259.1s (z4.9), CLIP_07 at 32.6s (z4.4). What changes there is a different question, which is
Result 3.

## Result 2 — the fine pass finds events the 4s pass never reported

23 windows read at 1s sampling, one sheet per window, 480px thumbnails, 3×5 grid.

| clip | coarse (4s) | fine (1s, 8 windows) |
|---|---|---|
| CLIP_04 | 17 moments, 3 jump/fall, $0.222 / 3 sheets | 5 moments, 1 jump/fall, 4 junk, 5 unusable, $0.569 / 8 sheets |
| CLIP_07 | 14 moments, 5 jump/fall, $0.236 / 3 sheets | 6 moments, 2 jump/fall, 3 junk, 6 unusable, $0.508 / 7 sheets |
| CLIP_11 | 20 moments, 5 jump/fall, $0.235 / 3 sheets | 8 moments, 1 jump/fall, 5 junk, 6 unusable, $0.600 / 8 sheets |

**Four events at timestamps the coarse pass never mentioned**: CLIP_04 22-23, CLIP_07 14-16,
CLIP_07 28-30 (where the coarse pass had only *"28-32 dark silhouette, detail obscured by
backlighting"*), CLIP_11 260-262. On the face of it that answers Karl's first claim: denser
sampling does surface things the 4s grid steps over.

**A fine sheet costs the same as a coarse one** — $0.073 against $0.077 — even though it covers
8 seconds instead of 120. The image is not what is being paid for; the system prompt and the
instructions are. That is the single most important cost fact in this study: *density is not
the expensive axis, coverage is.* Reading the whole bin at 1s would be ~$70; reading three
windows per clip is $2.6.

## Result 3 — but none of the eleven adjudicated events is what either pass said it was

Nine windows, checked by eye against 1s frames.

| window | coarse said | fine said | what is actually there |
|---|---|---|---|
| CLIP_04 19-28 | — | fall 22-23 | a face fills frame, arms out, walking up to the camera. A **reaction**, not a fall |
| CLIP_04 249-259 | jump/backflip 252-256 | glove over lens 253-255 | a rider over a roller at 250-251; a glove at 254. **No backflip** |
| CLIP_04 263-274 | fall 264-272 | perspective shift / glare 270-271 | the camera goes into the snow at **271-272**; those frames are unreadable |
| CLIP_07 12-21 | — | fall/flip 14-16 | the camera being unmounted and handled; a face at 17 |
| CLIP_07 27-34 | action 28-32 | fall 28-30 | a gloved hand adjusting the camera at the top of a lift |
| CLIP_07 168-196 | jump, *"airborne across 8 consecutive frames"* | not scanned | two people traversing a bank on a follow-cam tilted ~40° |
| CLIP_11 144-158 | jump 136-160, *"clearly upside-down mid-air"* 148-156 | not scanned | helmet-cam POV through trees; the horizon rolls, the rider does not |
| CLIP_11 199-208 | jump, *"another inverted aerial trick"* | abrupt helmet-cam cut 201-202 | POV with a pole and ski in frame, tilted horizon |
| CLIP_11 256-265 | fall/wipeout 252-260 | jump 260-262 | a glove at 256-258, then a low-angle bank with **a passenger on the chairlift** |

**Eleven claims. Zero survive as described.** One real impact exists (CLIP_04 271-272) and its
frames are unusable.

The mechanism is one thing, and it explains every false positive in the table: **it is the
camera that is inverted, not the person.** A helmet or chest mount on a POV run produces frames
where the horizon sits at forty-five degrees or more, trees hang from the top of frame and sky
fills the bottom. Thirty such frames on one sheet read as *"person inverted mid-air"* to a model
at 4s, and the same frames at 1s read as *"skier rotates or flips."* Denser sampling does not
fix it, because the failure is not temporal resolution.

Two corollaries worth keeping:

- **Karl's diagnosis was half right, and the wrong half is the expensive one.** The sampling
  *is* limited; buying more of it buys more confident misreadings.
- **The one thing Karl praised is the one shape that holds.** *"Person down in snow"*
  (CLIP_01 104-108) is a **static** subject in a **static** frame — no rotation to misread. The
  claims that fail are all motion claims made from tilted frames.

What the fine pass *is* reliable at is the negative: of its 12 `junk` and unusable calls, every
one I checked was right — the glove at CLIP_04 254, the black frames at CLIP_11 256-259, the
helmet-cam cut at CLIP_11 201. **A close look is a good auditor and a poor detector**, and the
ranking is built on that asymmetry.

## Result 4 — the sort, and what it is allowed to believe

```
score = kind_weight × notable × corroboration × confirmation × usable
```

| term | values | why |
|---|---|---|
| kind | fall/crash 1.0, jump 0.9, reaction 0.6, faces 0.5, action 0.3, scenery 0.1, **junk 0.0** | the brief's order, with events above people because events are what a transcript structurally cannot see. `junk` is a floor, not a low score: an artefact must never be offered |
| notable | 1.0 / 0.55 | the reader's own flag |
| corroboration | 1.0 → 1.4 on the excitement z inside the window | capped low **because of Result 3**: on POV footage the biggest frame differences *are* the artefacts, so a peak says "worth checking", never "therefore real" |
| confirmation | confirmed 1.5, unseen 1.0, unsupported 0.6, contradicted 0.35 | the only term carrying real evidence — two independent looks at the same seconds agreeing or disagreeing |
| usable | 0.4 when >50% of the moment is inside an unusable stretch | you cannot cut there, however good it was |

A fine window must cover ≥50% of a moment before its silence counts as evidence. At 0.6 the
CLIP_04 *"fall 264-272"* escaped audit by one tenth of a second of coverage, and it is exactly
the claim the audit refutes.

The rank lives in **one `events.json` per bin** beside the visual sidecars, not as an `events`
key inside each sidecar. Four reasons, in the order they mattered: the question is bin-wide and
cannot be assembled from per-clip files without re-deriving the sort on every read; the sidecars
are *paid observations* and this is *derived*, so rewriting them to change a multiplier would
put the "this clip has been read" cache at risk; the join has three sources and only one is per
clip; and it can be deleted and rebuilt for nothing.

## Result 5 — the ranking on Killington, against the proposal Karl was reacting to

134 events over 12 clips. Only 23 of ~90 candidate windows have been audited, so 121 events are
`unseen` — the rank is doing what it can with mostly unaudited evidence.

| # | clip | seconds | kind | score | evidence | in the 20-shot proposal? |
|---|---|---|---|---|---|---|
| 1 | CLIP_06 | 204.0-220.0 | crash | 1.40 | unaudited | — |
| 2 | CLIP_04 | 22.0-23.0 | fall | 1.33 | unaudited | — (**by eye: a reaction, not a fall**) |
| 3 | CLIP_07 | 14.0-16.0 | fall | 1.31 | unaudited | — (**by eye: camera handling**) |
| 4 | CLIP_09 | 192.0-200.0 | crash | 1.26 | unaudited | **shot 12** (8.0s) |
| 5 | CLIP_09 | 120.0-128.0 | jump | 1.26 | unaudited | — |
| 6 | CLIP_11 | 136.0-160.0 | jump | 1.26 | unaudited | — (**by eye: tilted POV**) |
| 7 | CLIP_11 | 260.0-262.0 | jump | 1.26 | unaudited | — (**by eye: a chairlift passenger**) |
| 8 | CLIP_09 | 68.0-76.0 | fall | 1.25 | unaudited | — |
| 9 | CLIP_06 | 8.0-16.0 | fall | 1.20 | unaudited | **shot 7** (4.8s) |
| 10 | CLIP_04 | 4.0-8.0 | jump | 1.16 | unaudited | — |
| 11 | CLIP_01 | 104.0-108.0 | fall | 1.16 | unaudited | **shot 1** (3.0s) — *the one Karl called correct* |
| 12 | CLIP_06 | 304.0-312.0 | jump | 1.13 | unaudited | **shot 20** (1.1s) |
| 13 | CLIP_07 | 28.0-30.0 | fall | 1.12 | unaudited | — (**by eye: camera handling**) |
| 14 | CLIP_05 | 4.0-12.0 | jump | 1.12 | unaudited | — |
| 15 | CLIP_11 | 100.0-108.0 | jump | 1.11 | unaudited | — |

**4 of the top 15 are in the cut**, and the moment Karl singled out as correct is #11. Six of
the fifteen are the shape Result 3 says not to trust, and four of those I have personally
checked and refuted. So the honest reading of this table is *not* "here are eleven jumps you
missed" — it is that a ranking over unaudited kinds ranks unaudited kinds.

The audit does work where it has run. Of the five `contradicted` events, three are claims the
previous proposal **cut on**:

| event | was | now |
|---|---|---|
| CLIP_04 252-256 *"aerial flip or backflip"* | proposal **shot 9** | row 38 of the board's *seen* tab, `unconfirmed`, score 0.42 |
| CLIP_11 200-208 *"another inverted aerial trick"* | proposal **shot 14**, whose `why` claims *"the picture actually delivers the inverted trick at 204-208"* | row 37, `unconfirmed`, score 0.44 |
| CLIP_04 264-272 *"person tumbling or falling"* | proposal **shot 10** | row 36, `unconfirmed`, score 0.49 |

Verified live on the board (`--port 8768`): project panel reads *events ranked 134*, the *seen*
tab is ordered by score and those three sit near the bottom of the visible forty marked
UNCONFIRMED.

## Decision

- **Adopt the ranking**, with confirmation as the load-bearing term and `junk` at zero. It is in
  `roughcut/events.py`, the Ask prompt (`## Events, ranked`, before the inventory), the board's
  *seen* tab, and `/api/project`.
- **Adopt the two-stage pass** at three windows per clip by default: $0.22/clip on top of a
  $0.23 coarse pass. Its value is auditing, not finding.
- **State the tilted-camera trap in the prompt**, next to the transcript-density trap, and carry
  the evidence word on every ranked line. Neither pass's `kind` may be presented as fact.
- **Do not buy density for its own sake.** A whole-bin 1s pass would be ~$70 and Result 3 says
  it would buy more confident misreadings.
- **RQ-1/RQ-7 stay open**, and this study narrows them: the axis that matters for event
  recognition on POV footage is not sheet interval, it is frames-per-sheet and thumbnail size —
  30 tilted frames at 320px is where the misreadings come from.

## What is provisional

- **Every weight is argued, not fitted.** There is no labelled set to fit them against, and
  making one is the obvious next study.
- **Nine adjudications, all of them hard cases.** This measures that the failure mode exists and
  what causes it. It does not measure a rate.
- **CLIP_07 168-196 and CLIP_11 136-160 were refuted by eye but never audited by the fine pass**,
  because the motion scan's top-8 windows did not cover them. They still rank 6th and 20th.
  A scan that spent its windows on the *coarse pass's own claims* rather than only on motion
  peaks would have caught both — that is a one-line change to the window source and the first
  thing to try next.
- **The fine pass's own positives are unaudited by anything.** Rows 2, 3, 7 and 13 of the top 15
  are fine-pass claims that nothing cross-checks; three of the four are wrong. `source: close
  look` should probably not inherit `unseen`'s neutral 1.0.

## What it cost

| | calls | projected |
|---|---|---|
| fine reads, 23 windows over 3 clips | 23 | **$1.676** |
| motion scan, 12 clips | 0 | $0 |
| ranking, whole bin, rebuilt many times | 0 | $0 |
| eyeball contact sheets, 9 windows | 0 | $0 |
| **total** | **23** | **$1.676** |

The coarse pass over the same three clips had cost $0.693. Every number above came from
`~/work/roughcut-ledger.jsonl` and the sidecars' own `projected_usd`.

## Reproduce

```
uv run research/tools/event_scan.py ~/work/app/proxies/killington-neutral \
  --sidecars ~/work/app/audio/killington-neutral \
  --visual ~/work/app/visual/killington-neutral \
  --only CLIP_04,CLIP_07,CLIP_11 --limit 8 --windows-out /tmp/windows-fine.json

uv run research/tools/visual_pass.py ~/work/app/proxies/killington-neutral \
  -o ~/work/app/visual/killington-neutral \
  --interval 1 --cols 3 --rows 5 --width 480 --orient auto \
  --windows /tmp/windows-fine.json

uv run research/tools/event_scan.py ~/work/app/proxies/killington-neutral \
  --sidecars ~/work/app/audio/killington-neutral \
  --visual ~/work/app/visual/killington-neutral --rank --top 15
```

To check a claim by eye, which is the step this study most wants repeated:

```
uv run research/tools/contact_sheet.py ~/work/app/proxies/killington-neutral/CLIP_04.mp4 \
  -o /tmp/eye --interval 1 --cols 5 --rows 3 --width 420 --start 249 --end 259
```
