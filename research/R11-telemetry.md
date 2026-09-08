# R11 — What the camera felt: is GoPro telemetry any good as a witness?

**Status:** run and closed on the Killington bin (B2, 12 clips, 43.1 min), 2026-09-07.
Tooling: `research/tools/telemetry.py` (PEP 723, no dependencies), `research/tools/contact_sheet.py`
for the by-eye checks. Per-clip summaries in `benchmarks/labels/B2-telemetry.json`; the 10 Hz
series and raw GPMF dumps in `~/work/app/telemetry/killington-neutral/`.

**Question.** docs/INTAKE.md M6 lets telemetry into the intake only as an *optional* witness,
weighted with the audio and visual passes and never on its own, and Karl's rule is that it must
not be over-indexed because *"it could be noisy or bad."* Nobody had looked. This study asks
three things: does the footage carry the data at all; do the two shapes the design named —
freefall runs and impact peaks — separate the events R10 adjudicated from random footage; and
does the camera's own orientation say anything about the claims R10 refuted as *"the camera is
inverted, not the person."*

**Corpus.** All 12 clips, HERO9 Black, 43.1 min. **Reference.** R10's nine by-eye adjudications
plus the one Karl called correct (ten windows); R10's unaudited top-15 (reported, not scored);
360 random 8 s windows (30 per clip, seed 11) for the base rate; and **19 further contact
sheets** at telemetry's *own* strongest detections, read by my eyes at 1 s and 0.5 s — the step
R10 said it most wanted repeated, and the only way to learn what a 9 g peak actually is.

---

## Result 1 — the data is there, rich, and free

Every clip carries a `gpmd` data stream (index 3, handler "GoPro MET"), one packet per
1.001 s, 1.2 MB per 200 s. A pure-Python KLV walk recovers 20 streams; the ones that matter:

| stream | rate | what | conditions |
|---|---|---|---|
| ACCL, GYRO | 198.5 Hz | m/s², rad/s, `SCAL` 417 / 939, `ORIN` ZXY | timed by the per-stream `STMP` µs clock; drift against packet pts ≤ 0.037 s over 5 min |
| GPS5 + GPSF/GPSP | 18.2 Hz | lat, lon, alt, 2D/3D speed; fix and DOP per second | 3D fix 76–100 % of samples on 10 clips; **0 % on CLIP_02 (259 s, never locked)**, 18 % on CLIP_03 |
| GRAV, CORI, IORI | 29.97 Hz | gravity unit vector; camera and image quaternions | GRAV agrees with a 1 s low-pass of ACCL, r = 0.81–0.99 on the moving clips; IORI is the identity (p90 rotation 4–6°) — **horizon levelling was off**, so the picture tilts exactly as the body does |
| AALP, WNDM, MWET | 10 Hz | the camera's own audio level, wind meter (0–100), wet-mic flag | not used here; see *Discovered* |

Parsing the whole bin takes **7 s wall, $0**. ffmpeg's `-codec copy -f rawvideo` dumps the
stream byte-exact (packet sizes from `ffprobe -show_packets` sum to the dump length on every
clip), so the split into packets needs no heuristics.

Two things the raw numbers say before any detector runs. |a| has a median of 1.00–1.06 g on
every clip — the sensor is calibrated. And its p99 is **1.2 g on a static clip, 3.0–3.5 g on
the POV runs**: three g is not an impact on a helmet, it is Tuesday.

## Result 2 — the design's two thresholds are wrong in opposite directions

| rule (INTAKE M6 wording) | fires per minute, whole bin | fires in random 8 s windows |
|---|---|---|
| freefall: \|a\| < 0.3 g for ≥ 0.25 s | **0.00** (zero runs in 43 min) | 0 / 360 |
| freefall, loosened: \|a\| < 0.5 g for ≥ 0.25 s | 0.56 (24 runs) | 26 / 360 (7.2 %) |
| impact: peak > 3 g, prominence ≥ 1 g | **6.24** (269 peaks) | 135 / 360 (37.5 %) |
| impact > 5 g | 1.18 (51) | 45 / 360 (12.5 %) |
| impact > 7 g | 0.49 (21) | 26 / 360 (7.2 %) |
| GPS stop: ≥ 4 m/s → ≤ 1 m/s within 3 s | 0.32 (14) | 11 / 360 (3.1 %) |

The 0.3 g freefall rule **never fires**, on a bin that contains at least six visible airs
(Result 4). A skier's helmet in flight is not in freefall: the head is moving on a body that is
still absorbing, and the six confirmed airs bottom out at 0.03–0.15 g for 0.25–0.4 s but pass
through 0.3–0.5 g on the way. At 0.5 g the rule fires 24 times in the bin and every one of them
is worth a look (Result 4). The **robust z-score** used for the motion and audio tracks is
useless here — the MAD of the 10 Hz |a|-max series is so small that a routine 3 g bump scores
z ≈ 12 — so the impact rungs below are absolute, in g.

The random-window base rates over-count the short clips: 30 windows are drawn per clip
regardless of length, so the 35 s CLIP_05 contributes 17 of the 45 `> 5 g` firings from 27 s of
footage. The per-minute column is the fair base.

## Result 3 — against R10's adjudications: the shape holds, the sample is two

The ten adjudicated windows, with what the camera felt inside each:

| window | R10 verdict | \|a\| max | min g | ff < 0.5 g | roll max (p90) | speed, drop | camera-felt? |
|---|---|---|---|---|---|---|---|
| CLIP_01 104-108 | person down in snow — *correct* | 2.5 | 0.41 | — | 44° (23 % > 30°) | 0.7, 0.5 | no — a filmed subject; the camera is planted at 25–45° roll |
| CLIP_04 19-28 | a reaction, not a fall | 2.9 | 0.23 | 0.24 s | 35° | 4.5, 1.0 | no |
| CLIP_04 249-259 | rider over a roller; a glove; no backflip | 1.8 | 0.30 | — | 15° | 0.5, 0.4 | no |
| **CLIP_04 263-274** | **camera goes into the snow 271-272** | **5.0** | 0.21 | — | 17° | 6.8, **3.0** | **yes** — 5.0 g at 269.8, 4.1 at 270.5, 3.9 at 271.4; speed 6.8 → 2.5 m/s and falling |
| CLIP_07 12-21 | camera unmounted and handled | 1.9 | 0.40 | — | **74°** | 1.9, 1.1 | no — handling shows as roll, not g |
| CLIP_07 27-34 | glove adjusting the camera | 1.7 | 0.34 | — | 15° | 4.0, 0.4 | no |
| CLIP_07 168-196 | follow-cam "tilted ~40°" | 4.0 | 0.45 | — | **19° (8°)** | 0.6, 0.5 | no |
| **CLIP_11 144-158** | "horizon rolls, rider does not" | **6.7** | 0.21 | 0.15 s | 30° (20°) | 3.0, **2.4** | **yes — see below** |
| CLIP_11 199-208 | POV, tilted horizon | 2.0 | 0.57 | — | 19° (14°) | 0.5, 0.2 | no |
| CLIP_11 256-265 | a glove, then a chairlift passenger | 4.6 | 0.26 | — | 18° | 0.4, 0.2 | no |

**R10 missed one, and telemetry found it.** The accelerometer put a 6.7 g peak at CLIP_11
144.3 inside a window R10 had scored as a rolled POV with no event. Re-read at 0.5 s: a glove
and pole across the lens at 143.5, spray at 144.0, the wearer's **skis against the sky at
145.0–145.5**, tree canopy from below until 149, a pole strap being sorted at 155. The wearer
fell; speed decays 3.0 → 0.2 m/s over four seconds and stays there; the body pitches to 58°
from its mount. The coarse pass's *"upside-down mid-air 148-156"* was the camera on its back,
and R10's eye read the canopy frames as trees on a rolled POV. Both were wrong about what it
was; the sensor was right that something happened. The adjudicated set now has **two**
camera-felt events, and that correction is R11's, made after the sensor pointed at it.

Precision / recall on those two, and the base rate the same rule pays for it:

| rule | tp | fp | fn | P | R | random 8 s windows |
|---|---|---|---|---|---|---|
| impact > 3 g | 2 | 2 | 0 | 0.50 | 1.00 | 37.5 % |
| **impact > 5 g** | **2** | **0** | **0** | **1.00** | **1.00** | **12.5 %** |
| impact > 7 g | 0 | 0 | 2 | — | 0.00 | 7.2 % |
| freefall < 0.5 g ≥ 0.25 s | 0 | 0 | 2 | — | 0.00 | 7.2 % |
| GPS stop | 0 | 0 | 2 | — | 0.00 | 3.1 % |
| speed drop ≥ 3 m/s in 3 s | 1 | 0 | 1 | 1.00 | 0.50 | 14.2 % |

Read this table for its shape, not its decimals: n = 2. What it does say is that *within R10's
hard cases*, a > 5 g peak sits inside both real events and inside none of the eight refuted
claims — the vision model's hallucinated flips, falls and backflips have nothing under them in
the accelerometer. Freefall finds neither, because neither was an air. And the two events the
telemetry does mark are exactly the two the pipeline had no way to rank: both are unusable or
unreadable on the sheet (R10: *"those frames are unreadable"*), which is where a second sense
is worth having.

## Result 4 — telemetry's own top detections, checked by eye: the biggest numbers are hands

If > 5 g fires 51 times in the bin and the pipeline were to rank by it, what would it be
ranking? Nineteen sheets, chosen by the sensor, read by me.

**The eight largest peaks in the bin (13.8 → 7.9 g):**

| peak | clip, s | what is there |
|---|---|---|
| 13.8 g | CLIP_02 12 | camera taken off, pointed at boots, turned over, lens covered, then a selfie. **Handling** |
| 10.3 g | CLIP_01 78 | a hard bump following a skier; no fall |
| 9.5 g | CLIP_04 87 | the wearer's arm hits the camera mid-run. **Handling** |
| 9.3 g + GPS stop | CLIP_04 290 | skier stops and grabs the camera. **Handling** |
| 8.8 g | CLIP_08 230 | a skid on a groomer; no fall |
| 8.5 g | CLIP_05 10 | a roller at 12 m/s |
| 8.3 g | CLIP_05 17.6 | the kicker pop of a **real air** (next table) |
| 7.9 g | CLIP_08 215 | rollers, gloves in frame |

**One real event in eight**, and three of the eight are a hand on the camera. This is R10's
finding arriving from a third direction: the largest frame differences were lens wipes, the
largest audio onsets were nothing (R8 Result 5), and the largest accelerations are handling.
*A peak says "worth checking", never "therefore real"* — R10's corroboration cap, verbatim,
applies to the accelerometer too. A > 5 g rule must never promote alone.

**Ten of the 24 loose freefall runs (< 0.5 g for ≥ 0.25 s):**

| run | clip, s | dur, min g, speed | what is there |
|---|---|---|---|
| ✔ | CLIP_05 18.0 | 0.30 s, 0.03 g, 14.7 m/s | **a kicker**: 8.3 g pop, the skier ahead airborne at 17, the wearer's tips in frame at 18, 3.0 g landing at 18.5 |
| ✔ | CLIP_12 46.6 | 0.39 s, 0.03 g, 2.2 m/s | a drop off a mound under the lift, skis in frame at 47, 4.0 g landing |
| ✔ | CLIP_12 50.6 | 0.28 s, 0.06 g, 5.9 m/s | a second mound, 7.7 g landing |
| ✔ | CLIP_02 102.1 | 0.34 s, 0.06 g, no GPS | air over a roller, skis in frame, 4.9 g landing |
| ✔ | CLIP_04 224.6 | 0.28 s, 0.10 g, 2.3 m/s | a low-speed drop off a lip, skis up, 7.7 g landing |
| ✔ | CLIP_06 30.2 | 0.32 s, 0.06 g, 2.2 m/s | dropping out of a glade onto the trail, skis up at 29, soft landing |
| ~ | CLIP_08 76.0 | 0.48 s, 0.13 g, 5.0 m/s | a float over a roller; nothing visible at 1 s |
| ~ | CLIP_08 31.0, 32.0 | 0.35 / 0.30 s | two floats on a steep face; nothing visible |
| ~ | CLIP_11 59.6 | 0.30 s, 0.15 g, 12.4 m/s | a roll-over at speed; nothing visible |

**Six of ten show the wearer visibly leaving the snow; four are floats over rollers; zero are
handling or junk.** A hand cannot fake a third of a second at 0.03 g. That makes the freefall
run the *cleaner* of the two shapes by a wide margin — and the more modest: apart from the
CLIP_05 kicker these are mound drops and glade exits, not the *"cool jumps"* Karl was missing.
Where it lands, though, it lands to the tenth of a second (the 8 s coarse-pass window that
ranks 14th in R10, *"CLIP_05 4-12 jump"*, holds the approach; the air is at 17.6–18.5).

Recall of the freefall rule is **unmeasured**: there is no labelled set of airs, and this study
checked only the runs it fired on. It cannot say how many airs it missed.

## Result 5 — orientation: the camera was never inverted, and it knows it

The gravity direction from a 1 s low-pass of ACCL (a resting accelerometer reads +g upward),
expressed against each clip's own mount, since seven of these clips are mounted upside-down
and auto-rotated (`B2-orientation.json`):

- **Mount orientation agrees with the visually confirmed rotation metadata on all 9 labelled
  clips** — vertical component +0.94 / +0.88 on the two `rotation=0` clips, −0.88 to −0.96 on
  the seven `rotation=−180` clips. It also calls the three unlabelled: CLIP_03 upright,
  CLIP_05 inverted, CLIP_10 inverted and pitched 74° (a camera lying nearly flat).
- The three windows R10 attributed to a rolled camera: **body roll never exceeded 19° / 30° /
  19°** (p90: 8° / 20° / 14°); total tilt from the mount 31° / 58° / 38°, the 58° being the
  fall. With IORI at identity the picture rolls exactly as the body does, so the 40°+ horizon
  R10 saw in CLIP_07 168-196 is not the camera rolling; on a POV clip through trees the
  "horizon" is a sidehill and the trees are vertical, and the eye reads a slope as a roll.
- Where the body *does* roll past 45° (0.78 s per minute of the bin), it is handling (CLIP_07
  12-21, 74°; CLIP_02 and CLIP_06 openings, 140–180°) or a planted camera (CLIP_01 104-136,
  ~80°: the camera lying in the snow filming the person down in it).

So the orientation series can **refute a claim that the wearer or camera inverted** — none of
the three "inverted rider" claims had the body more than 58° from its mount, and that one was
lying down — but it cannot say a word about a person *in frame*. A filmed skier can backflip in
front of a perfectly level camera. What it can add to a `seen` witness is one number: how far
the camera was from level in the window.

---

## Decision

Karl's rules stand, and the study lets one small step past "numbers only":

- **Yes, the `felt` witness may carry a weight — for one shape, and a small one.** A freefall
  run (< 0.5 g for ≥ 0.25 s; *not* the 0.3 g the design guessed, which finds nothing) is clean
  (0 of 10 checked were artefacts) and precise in time. Let it act as a **corroboration term
  capped at the motion track's** (R10: 1.0 → 1.4), never a kind, never a detector: its text is
  `0.30 s at 0.03 g`, and a pick still needs a `heard` or `seen` witness to exist.
- **Impact peaks stay numbers only, weight 1.0.** Show `6.7 g` on the witness; do not let it
  move the rank. Two-for-two inside R10's hard cases is the right sign, but the bin-wide look
  says the largest values are hands on the camera and the rule fires 51 times for a handful
  of events. If a bonus is ever fitted it should start at > 5 g and never at 3 g.
- **Orientation goes on the `seen` witness as a number, not a state**: `camera tilt ≤ 31°` in
  the window. The Ask prompt's tilted-camera trap can then say something checkable instead of
  something general. It must not write `contradicted` — it does not know about the person.
- **The journal's `telemetry_peaks` priority input** (M3, `priority(clip)`) should count loose
  freefall runs and > 5 g peaks, not > 3 g peaks: at 6 per minute the latter is noise dressed
  as a count.
- **GPS is a nice-to-have** — speed under the picture when there is a fix — and nothing else
  yet. CLIP_02 has no fix for four minutes and the stop rule, fitted on one event, found a
  skier stopping to touch the camera the only time it was checked.

## What NOT to conclude

- **Not that telemetry finds the cool jumps.** It found one kicker and five mound drops. The
  jumps in this bin are mostly *other people's*, filmed, and the accelerometer is bolted to
  the person filming.
- **Not that a big g is a big moment.** The biggest g in the bin is a selfie.
- **Not a precision or recall.** Two positives make a sign, not a rate; freefall recall is
  unmeasured; impact precision bin-wide is roughly one in eight at the top and unknown below.
- **Not that the spec thresholds were close.** 0.3 g would have found none of six confirmed
  airs; 3 g fires six times a minute.
- **Not that the stop rule means crashes.** n = 1, and its one audited firing was a stop.
- **Not that orientation audits people.** It audits the camera. The CLIP_11 fall shows the
  useful direction: it can turn *"the camera inverted, not the person"* into a measurement.
- **Not that this generalises past a HERO9 on a helmet or chest.** Every number above is that
  camera, that mount, that snow. A hand-held phone clip has none of it, and the pipeline must
  run identically without it (Karl's first rule; the tool returns `gpmd: false` and nothing).
- **Not that GPS is present** — check `GPSF` per second; one clip in twelve never locked.

## What is provisional

- **Two camera-felt positives**, one of them adjudicated in this study after the sensor pointed
  at it. The honest next step is a labelled set of *wearer* airs and falls across the bin —
  telemetry's freefall runs are the cheapest place to start one, since every one of them is
  worth a 0.5 s sheet.
- **The freefall floor (0.5 g) and minimum (0.25 s) were chosen by looking**, not fitted: 0.3 g
  finds nothing, 0.5 s finds nothing, 0.5 g / 0.25 s finds 24 things of which the ten checked
  were all real motion. Nothing in between was tried.
- **Axis assignment.** Vertical = ACCL[0] is verified against nine labelled mounts. Lateral =
  ACCL[1] and forward = ACCL[2] follow the gpmf-parser README's HERO6+ order (Y, −X, Z) and
  the GRAV cross-correlation; every clip's median off-vertical component is on axis 2 (a camera
  pitched 9–25° down), which is the physically plausible one. The sign of roll is unverified
  and unused.
- **Random windows over-sample the short clips** (Result 2); the per-minute rates are the ones
  to quote.

## Discovered on the way

The GPMF carries the camera's own **wind meter** (`WNDM`, 0–100 at 10 Hz), a wet-microphone
flag (`MWET`) and an AGC audio level (`AALP`). R8 left the wind detector *"unvalidated"* and
R9 rebuilt it against an AudioSet tagger; the camera has been writing a wind number into every
file all along. Worth a look next time wind matters — not this study.

## What it cost

| | wall | $ |
|---|---|---|
| extract + derive, 12 clips | 7 s | 0 |
| score (360 random windows, 25 R10 windows) | < 1 s | 0 |
| 19 contact sheets off the proxies, read by eye | ~2 min | 0 |
| **total** | **~3 min** | **$0** |

## Reproduce

```
uv run research/tools/telemetry.py ~/footage/killington-neutral \
  -o ~/work/app/telemetry/killington-neutral \
  --score --labels-out benchmarks/labels/B2-telemetry.json

# the by-eye step, e.g. the CLIP_11 fall at half-second sampling
uv run research/tools/contact_sheet.py ~/work/app/proxies/killington-neutral/CLIP_11.mp4 \
  -o /tmp/eye --interval 0.5 --cols 5 --rows 2 --width 400 --start 143 --end 148
```
