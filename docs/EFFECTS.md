# Effects and music — how a natural-language note reaches the pixels

Karl, 2026-07-25:

> *"Let's focus on adding in music, and how effects in general will be added to footage.
> One good example — I have a scene where I want to add Call of Duty hitmarkers on the video
> frames with the sound effect too when my skis hit a bunch of rocks, that I would like to
> instruct via natural language for that frame."*

Everything the tool does today is **selection**: which moments, in what order, cut where. Effects
are the first thing that changes the pixels rather than choosing them, and that is a different
kind of problem with a different failure mode. Selection fails visibly — a boring cut. Effects
fail *invisibly and expensively*: a filter string that renders black, an overlay that lands two
seconds late, a music bed that buries the one line the film is about.

## Rule 1 — the model never writes ffmpeg

The tempting design is to let the model emit filter graphs. It is wrong, for the same reason
`validate_plan` is strict about timestamps: an invented clip name fails loudly, but an invented
filter string either crashes the render or silently produces something nobody asked for. Worse,
a filter graph is executable — accepting model-authored ffmpeg from a note that itself came from
a transcript is an injection path into a subprocess.

So: **a closed vocabulary of effect kinds with validated parameters.** The model chooses from a
registry and fills in numbers; the renderer owns every string that reaches ffmpeg. An unknown
`kind` is a validation error, not a render.

```jsonc
// in the EDL, alongside "segments"
"effects": [
  {"kind": "sfx",     "asset": "hitmarker", "at": 152.34, "gain_db": -4},
  {"kind": "overlay", "asset": "hitmarker", "at": 152.34, "duration": 0.35,
   "anchor": "center", "scale": 0.12},
  {"kind": "music",   "asset": "cold-open", "gain_db": -18, "duck": true,
   "fade_in": 1.5, "fade_out": 4.0}
]
```

Effect times are **film-relative seconds** — where it lands in the finished cut, not inside a
source clip. Trimming a shot earlier in the timeline moves everything after it, so the renderer
resolves film time against the assembled parts. (The alternative, anchoring to `clip + t`,
survives re-ordering but breaks when the shot is trimmed out entirely. Film-relative is simpler
and matches what a person means by "here".)

## Rule 2 — effects are resolution-independent

The board previews on 720p proxies; delivery renders from 5.3K masters. The same effect spec has
to produce the same *looking* result on both, which means no pixel geometry anywhere:

- `scale: 0.12` = 12% of frame width, not 230px
- `anchor: center | top-left | …` plus optional fractional offset
- font sizes as a fraction of frame height

Getting this wrong is not subtle — a hitmarker sized for the proxy is a postage stamp on the
master — but it is only visible at the very end, after an expensive render. Hence a rule rather
than a convention.

## Rule 3 — the precision problem is the interesting one

"Put a hitmarker where my skis hit the rocks" needs **frame accuracy** (±40ms at 24fps). Nothing
the tool currently produces is anywhere near that:

| signal | resolution | what it knows |
|---|---|---|
| transcript | ~1s, and **wrong by minutes** | that someone *said* "I hit some rocks" — usually well after |
| visual pass | 4s (sheet interval) | that an impact happens somewhere in this window |
| **onset track** | **100ms** (10 Hz, already in every audio sidecar) | that a sharp transient happened *here* |

The answer is to use all three, in that order, narrowing:

1. **Locate the window** — the note plus the visual pass and transcript give a candidate span
   ("rocks under the skis, 148–156s").
2. **Snap to the impact** — the R8 onset track is already sampled at 10 Hz across every clip. A
   rock strike is exactly what it measures: a broadband transient against a wind bed. Take the
   largest onset peak inside the window; that is 100ms accuracy for free, with no model call.
3. **Confirm by looking** — extract frames at 10fps across ±0.5s of the peak into a strip and
   ask which frame is the impact. One cheap call, ~40ms accuracy.
4. **Let the human nudge it.** The board already has trim controls; an effect marker is the same
   gesture. Two clicks beats any amount of inference, and the preview makes it obvious.

Step 2 is the part worth emphasising: **the data needed to hit the frame already exists and has
never been read.** R8 built the onset track and R9 found the event tagger useless for
*classifying* what happened; neither noticed that for *timing a known event* the onset track is
exactly right.

## Rule 4 — assets are a library, not a prompt

The model cannot invent a hitmarker PNG. Effects reference assets by name from
`assets/`, described by a manifest the model reads the way it reads the clip inventory:

```jsonc
{"name": "hitmarker", "kinds": ["overlay", "sfx"],
 "overlay": "assets/overlay/hitmarker.png",   // transparent PNG, square, white
 "sfx": "assets/sfx/hitmarker.wav",           // 180ms, sharp attack
 "description": "Call-of-Duty style X hit indicator and its click. Reads as a hit
                 landing; comic, not cinematic. Pairs with an impact you can see."}
```

An effect naming an asset that does not exist fails validation, like a segment naming a clip that
does not exist. The manifest's `description` is what lets the model choose sensibly between three
whooshes — the same job the `why` field does for shots.

**Licensing is the human's problem, deliberately.** The tool will not fetch assets from the
internet. Karl drops files in `assets/`, the manifest describes them, and what is in there is
what can be used.

## How it renders

`assemble.py` already cuts every shot to its own part with a full re-encode (x264 CRF 20, fixed
geometry) and then concatenates with `-c copy`. That structure is lucky: effects slot in without
a second generation of loss.

- **Per-shot effects** (overlay, sfx, speed) become filter-graph additions on the part that
  contains them. The part was being encoded anyway.
- **Film-wide effects** (music) apply after the concat, as a second pass that **copies the video
  stream** and only re-encodes audio. A music bed costs no picture quality at all.
- **Ducking** uses `sidechaincompress` keyed on the film's own audio, so the bed drops under
  dialogue and comes back up in the gaps. This is the single most important thing about music in
  a film where the words carry the story — R8 established that for this footage the words *are*
  the signal, and a bed at a flat level buries them.

## Music, specifically

Two different features share the word:

1. **A bed** — put a track under the cut, duck it under speech, fade it in and out. This is what
   "add music" usually means, it is the 80%, and it is cheap: no selection changes, one asset,
   four numbers. **Built first.**
2. **Cutting to the beat** (FUTURE_PHASES P2.6) — the track drives the edit: detect beats, then
   ask for shots that fill N slots of given lengths. That is a different *selection* problem, not
   an effects problem, and it belongs with the skeleton agent rather than here.

## The loop, end to end

Identical in shape to Ask, because that shape works:

1. Human types *"hitmarker and the sound when my skis hit the rocks"*.
2. Model gets the current cut, the visual moments, the transcript, and the asset manifest;
   returns **effect objects**, validated hard.
3. The renderer snaps each effect to the nearest onset peak inside its window.
4. Proposal shows what will be added, in film time, with the reason.
5. **Preview renders on the proxy** — seconds, not minutes — so timing is checked by watching.
6. Human nudges, accepts, renders.

## Build order

1. **Music bed with ducking** — the whole render path for film-wide effects, and the thing Karl
   asked for first.
2. **The effect vocabulary and validation** — `sfx` and `overlay`, with the asset manifest.
3. **Onset snapping** — the 100ms precision, from data already on disk.
4. **Effects in the board** — markers on the timeline, drag to nudge, preview on the proxy.
5. **Natural language → effects** — the Ask path, once the first four make a proposal checkable.

Nothing here needs the T0–T13 pipeline, and nothing here should wait for it.
