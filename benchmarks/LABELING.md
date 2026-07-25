# Labeling Guide

Ground truth an agent cannot produce for itself. Model-generated labels can't validate a model,
so this is the one part of the project that needs your time specifically.

**Do the highlights pass first, and only that.** It's what study R7 needs to reach checkpoint
CP2 — the go/no-go on whether this whole thing works. Cut lists and reference transcripts are
worth your time only after that gate passes.

---

## Pass 1 — Highlights (do this now: 30–45 min of B1, ~1 hour of your time)

**What you're marking:** every moment you would *consider* including in an edit of this trip.
Not "the best bits" — the candidate pool. If you'd think about it, mark it.

**Be inclusive.** This is measuring whether the analysis *finds* things, so a moment you mark
and the agent misses is a real miss, while a moment you mark and don't ultimately use costs
nothing. Under-labeling makes a mediocre analysis look good.

**Include the unobvious ones**, because they're where automated analysis fails and where the
study gets its signal:
- Moments that are only interesting because of **audio** — someone shouting, laughing, a good line
- **Brief** moments, under 5 seconds. These get their own metric in R7 precisely because
  coarse sampling tends to miss them.
- Moments interesting for **context** rather than action — a look between people, an establishing
  view that sets up what follows
- Near-misses and failures, which are often better footage than clean runs

**Format** — `benchmarks/labels/B1-highlights.csv`:

```csv
file,start_s,end_s,note
GX010042.MP4,124.5,131.0,"wipeout into powder, someone laughs off-camera"
GX010042.MP4,402.0,405.5,"brief - good reaction shot"
GX010045.MP4,88.0,120.0,"long clean run, best of the day"
```

- Times in seconds from the start of that file. Rough is fine — ±1s doesn't affect the metrics.
- `note` is free text and genuinely useful: it's what R7 compares its rationales against.
- Prefix a note with `brief -` when it's under 5s, so the brief-highlight metric is easy to compute.

**How to work through it:** pick a contiguous 30–45 minutes rather than sampling across the
whole bin — contiguous footage lets the study measure whether the analysis handles *transitions
between* interesting and boring stretches, which sampled clips can't show. Scrub at 2–4× and
mark as you go; don't agonize over exact boundaries.

**Before you start**, skim [../research/R6-rubric.md](../research/R6-rubric.md). It's the
rubric for judging finished rough cuts, and reading it first tends to sharpen what you notice
while labeling.

---

## Pass 2 — Cut lists (only after CP2 passes)

For R2 (shot-detection tuning). `benchmarks/labels/B1-cuts.csv`:

```csv
file,frame
GX010042.MP4,1893
```

One row per true cut — a hard camera cut or a recording boundary, not a change of subject
within a continuous take. Include some deliberately hard cases: at least a few clips shot on a
phone (variable frame rate) and two long continuous takes with no cuts at all, which is how
false positives get caught.

## Pass 3 — Reference transcript (only after CP2 passes)

For R3 (ASR sizing). Pick a contiguous 10 minutes with a mix of clean speech, wind/outdoor
audio, and background noise. Hand-correct a machine transcript rather than typing from scratch;
note the exact file and time span in `benchmarks/bins/B1.json`. Plain text,
`benchmarks/labels/B1-transcript-10min.txt`.

---

## What happens to these

Highlight labels drive R7's recall metrics and CP2's go/no-go, then R1's subset labeling. Cut
lists set R2's F1 target. The transcript sets R3's WER target. All three are committed to the
repo (they're small text files); the footage itself never is.
