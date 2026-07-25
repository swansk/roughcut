# Labeling Guide

Ground truth an agent cannot produce for itself. Model-generated labels can't validate a model,
so this is the one part of the project that needs your time specifically.

**Do the highlights pass first, and only that.** It's what study R7 needs to reach checkpoint
CP2 — the go/no-go on whether this whole thing works. Cut lists and reference transcripts are
worth your time only after that gate passes.

---

## Pass 1 — Highlights (B1 = Copper, 26 clips, 11 min of footage)

> **Sheets are generated and waiting at `Documents\Roughcut Labeling\`** (47 sheets, 17MB,
> generated 2026-07-25). Mark from the image grids; only open the video for cases the stills
> can't settle. Asking you to scrub footage manually would violate the product's own first
> principle (SPEC §0) — the tooling exists so this is a quick visual pass.
>
> | Bin | Clips | Sheets | Orientation applied |
> |---|---|---|---|
> | B1 Copper | 17 (9 junk clips excluded) | 17 | `--orient none` (metadata spurious) |
> | B2 Killington | 9 long-form, names neutralized | 30 | `--orient auto` (flips the inverted mount) |
>
> The Killington filenames are hidden deliberately. It keeps the study honest, and it keeps
> *your* labels unanchored by the names you gave those clips when you already knew what was in
> them. The mapping lives outside the labeling folder and is applied at scoring time.

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

**Workflow: open `label.html` and click frames.** `make_label_ui.py` builds a page over a
contact-sheet directory where each frame is a clickable cell (CSS sprites into the existing
sheet JPEGs — no frames re-extracted). Click or drag to mark; contiguous marks merge into
ranges; timestamps are derived; ranges under 5s are auto-tagged `brief`; marks persist in
`localStorage`; Export produces the CSV.

This replaced an earlier instruction to read timestamps off images and type them into a
spreadsheet. That was mechanical work, which SPEC §0 principle 1 classifies as a design bug —
the fix was tooling, not a better explanation.

**Emitted format** — `benchmarks/labels/B1-highlights.csv`:

```csv
file,start_s,end_s,note
GX010494,84.0,96.0,"wipeout into powder, someone laughs off-camera"
GX010494,402.0,404.0,"brief - good reaction shot"
clip_03,88.0,120.0,"long clean run, best of the day"
```

- Times are derived from cell index × sample interval, so they're consistent by construction.
- `note` is free text and genuinely useful: it's what R7 compares its rationales against.
- `brief - ` is prefixed automatically for ranges under 5s.

**How to work through it:** go through every clip, including ones that look like nothing — the
study needs the boring stretches to actually be labeled boring, and that's only meaningful if
you looked. Copper is raw off the camera and has never been culled, which is exactly why it's
the primary bin: nothing has pre-selected the good parts for you or for the analysis.

**If you use Killington (B2) as well**, note that you named those files after what happens in
them (`bombbeginning`, `tastytrees`, …), so you already know where each good bit is. Label the
whole clip anyway — a five-minute clip named for one moment usually contains two or three
others, and those are the cases that separate a good analysis from a lucky one.

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
