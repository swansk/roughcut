# Handoff — read this first

**Active workstream (2026-09-08): the intake — tracked in [docs/INTAKE.md](INTAKE.md).** Read
that file first; it carries the decisions (five now — the fifth: a clip's start and end stay
adjustable after effects), the milestones M0–M7 with their checkboxes, the lanes and where to
pick up. The design it implements is in `docs/design/`. **State:** everything is ticked and
live on Killington: `/open` (contact sheet, themes, slider, picker, one Index button),
`/floor` (the pass: drag-trim, a verdict that moves on, compare takes, keyframe ticks and
jumpable frames, `0` restarts), the unattended index, and a contact-sheet prompt that no
longer calls a glove a backflip. **Next is Karl:** his look at the pass, Keep / Discard on the
seven proposed themes, and whether to re-look the other 11 clips with the new prompt (~$2) —
INTAKE's "Where we are" has the exact list. Everything below this line is the history that
led there, and the editing-room roadmap.

Last updated: 2026-08-25 (session 7 open). **Karl kept editing on his own on 2026-08-24**, past
everything below: he hand-added six shots to the accepted 17-shot cut, then asked for a revision
(note: drop the conquistador bit, find the rocks hit, restore covered-in-snow — ask `a038b42c`,
$0.57, 170s). The model built a **rocks runner** as the spine ("oh no, I hit one of those
earlier" → "I hit some rocks at the end there" → "Oh, hello! That's a rock!"), kept three of his
six adds (dropping the three silent ones as unaudited — say the word to restore them), and Karl
**accepted the 22-shot plan and rendered it at 4K delivery** (`cut_d25d19d5`, 2:59, ~1.0 GB,
review copy ready). Then he deleted one more shot by hand — the CLIP_12 debrief closer ("no, you
were huge, man") — so **the cut on the board is 21 shots, 175.1s**, ending rock! → "it bit!" →
"that's just showbiz, baby". Session 7 rendered that state as a preview (`cut_c912b763`, 175.33s,
+0.22s drift, marked *· this cut*), so the timeline and the newest 1080p render agree again; the
newest 4K render is one shot stale. Open question unchanged: is the cut good, and what does Karl
report next? Roadmap item 1 (audit what the sheets claim) is still the biggest lever.

**Session 7 then built the two features Karl asked for** (his words: *"request specific changes
to individual clips"* and *"use natural language to search for a specific clip through the whole
thing, and then present options along with their full video"*):

- **✎ ask on every shot card** — a note about one shot runs a *scoped* revision
  (`revise.propose_shot`: the film for context, only that shot's clip in detail, replacement
  confined to that clip and spliced server-side into the untouched timeline). Live: 10.7s and
  $0.15 against a full Ask's 3-4 minutes. Same accept/discard loop as everything else.
- **Find a moment** (sidebar panel) — free word-level search over transcripts + visual moments
  answers instantly; *Ask the model* (priced on the button, judge role, `roughcut/find.py`) reads
  the whole inventory for what word-matching cannot reach. Live: *"me falling into the river"* —
  words no transcript contains — returned the fall itself (CLIP_01 90-108, agreeing with the
  visual pass), the confession, and the aftermath, in 25s for $0.19. Every match plays the full
  clip proxy seeked to the moment, with *+ add to cut*.

Both verified live on the Killington board and under the suite (244 tests). One live-QA residue:
the *last proposal* banner now offers a discarded shot-ask proposal on the pocket-pizza shot
(holds 1.6s of reaction after "pizza") — accept it or ignore it, it is only a proposal. The two
scoped prompts are new surface for the next session's judgement calls: `SHOT_SYSTEM` /
`build_shot_prompt` and `find.SYSTEM` / `find.build_prompt`.

Previous update: 2026-08-23, session 6 continued — Karl ran a revision with the corrected
transcripts and **accepted it** (17 shots, 3:01, the ski-patrol beat in it), rendered it at 4K,
and reported three more things: the progress bars said nothing useful, the monitor showed blank
while the audio played, and the renders stalled with no way to download them. Two more agent
branches answered those and are merged.

Earlier the same session: the board became something you can edit in — a
**monitor** that plays the cut from the proxies, the **visual pass** runnable from the board with
what it saw on the cards, and **music** under the cut — all verified live on Killington; then, once Karl logged the CLI in
mid-session, the visual pass ran over all 12 clips from the board and two proposals were made,
neither applied; Karl then watched the proposal and gave notes, and four agent branches answered
them — all merged. Sessions 4–5 are summarised under "START HERE". Jump there, then "The roadmap
after that".

Repo: [github.com/swansk/roughcut](https://github.com/swansk/roughcut), private, `main`.

## Where we are

The project pivoted during session 1 and the task board hasn't caught up. **Read this before
docs/TASKS.md**, which still describes the pre-pivot plan for T0–T13.

**The plan is no longer "build the pipeline, then validate it."** It is:

> Build one real video out of Copper with **throwaway tooling**, judge it, and only then decide
> whether the architected pipeline is worth building.

Karl's framing of the core question: *"Can Claude compile a compelling video with good cuts from
footage?"* That is answered by making a video and watching it — not by measuring agreement with a
human's highlight labels, which is a proxy that fails in both directions (a cut can match the
labels and be boring; it can miss them and be good, because two editors differ legitimately).

## The agreed brief

> A 2–3 minute edit of the Copper trip for the friends who were there. Emphasis on the skiing,
> with just enough of the travel at the top to establish the trip. Loose and fun rather than
> cinematic. The people in it are the point — favour moments with faces and reactions over pure
> scenery.

Agreed by Karl. Not yet elaborated with names/who's-who.

## Human-in-the-loop map (agreed)

| Stage | Owner | Why |
|---|---|---|
| Brief / intent | **Human** | Not derivable from pixels. Five minutes, highest leverage in the project. |
| Ingest, orientation, junk, analysis, selection, assembly, audio, render | **Closed loop** | Objectively verifiable, or self-critiquable against the rubric |
| "Is it compelling?" | **Human** | The one judgment the agent is weakest at |
| Revision from notes | **Closed loop** | Human gives notes in plain language; agent applies them |

Known failure mode to guard against: the agent iterates into something that satisfies every
checkable rule and is still flat. Mitigation — keep the self-critique loop **short** before
showing Karl, and bring **two variants** rather than one (reacting to a choice is faster and more
informative than judging a single artifact).

## Next actions

1. ~~Audio analysis~~ — **done**, see [R8](../research/R8-audio-signals.md). Sidecars for all 17
   non-junk clips at `~/work/audio/*.audio.json`; 68 candidates over the bin.
2. ~~Selection~~ — **done**. All 17 sheets reviewed against their transcripts; inventory and
   reasoning in [notes/2026-07-25-selection.md](notes/2026-07-25-selection.md), EDLs in
   `research/edl/`.
3. ~~Assemble → loudness-match → render~~ — **done**. `research/tools/assemble.py`; both
   variants verified (18 ms A/V drift, −15.5 LUFS, no black runs).
4. ~~Self-critique against [R6-rubric.md](../research/R6-rubric.md)~~ — **done**, mean 3.75,
   one iteration applied (pacing). Stopped there on purpose.
5. ~~Karl watches the two variants~~ — **done, and round 1 of revision is applied.**
   **Verdict: B is the direction.** Karl: *"you identified the core theme (milk) and then edited
   well around it"*; the vocal-marker-driven cutting works; what's missing is story, which is
   the human's half.
   - `.../cuts/copper-variantB.mp4` — **"The legend", 2:43** ← the one to build on
   - `.../cuts/copper-variantA.mp4` — "The trip", 2:24 (kept for comparison only)

6. ~~Direction call~~ — **done. Karl chose the app wrapper** (2026-07-25). CLAUDE.md's "no
   Phase 2 features" rule was amended in the same decision; the T0–T13 pipeline stays on hold,
   because throwaway tooling produced a cut Karl endorsed and hardening it is not what unblocks
   the project.

**The app wrapper is the active workstream.** The cut board is built, tested and running:

```
uv run app/server.py --footage ~/footage/copper-02-2026
```
→ `http://localhost:8765`. A folder of footage is the only required argument; `--edl` and
`--sidecars` are optional and derived under `--work` when omitted. To open the hand-authored
variant B instead, pass `--edl research/edl/B1-variantB.json --sidecars ~/work/audio --orient
none`. See [app/README.md](../app/README.md) for the design and the latency argument behind
proxies.

80 tests (65 API + 15 driving real Chromium), ~34s, against a synthetic three-clip project so
they need neither `~/footage` nor a GPU. Browser layer skips cleanly if playwright is absent.

~~Close the interject loop~~ — **built and proven live.** "Ask for a change" takes a
plain-language note and returns a revised timeline as an accept/discard diff, through
`roughcut.inference` (SPEC §6). With an empty timeline the same call **originates** the cut.

**Does asking beat dragging? For structural moves, yes.** One live call on variant B (74s,
$0.27 projected) took 20 segments/162.5s → 18/129.2s: dropped the luggage walkway with the
reason *"no faces"* — the brief's own criterion, applied correctly — dropped the inert lounging
tag, moved 32s out of the opening into the middle as asked, extended the groomer POV to pick up
dialogue the hand cut had discarded, and reordered three beats inside GX010495 to build
dare → deed → reaction. **17 of 18 out-points land exactly on an utterance end**; the one
exception it declares in its own notes. Rendered and verified: 129.38s, 5ms A/V drift, no
rotation, −15.6 LUFS, no black runs →
`Documents\Roughcut Labeling\cuts\copper-variantB-asked.mp4`.

Two caveats worth carrying forward. It stayed clear of visual traps (the red pole in GX010490,
the glove in GX010493) **only because it inherited the `why` fields from the hand-authored EDL** —
the rationale field is load-bearing memory for a model that cannot see. And its dare → deed →
reaction reorder may invent a chronology the footage contradicts; that needs eyes, not text.

The originating prompt answers the first caveat as far as text can: it states plainly that the
model cannot see the frame and that its `why` is what the human checks the reasoning against.
The live run bore that out — it flagged its own riskiest choice (18s with no transcript, *"may
be a glove or a lift queue"*) without being asked. It does not answer the second caveat at all.

---

## Session 3: the app now owns the whole path

Karl, after using the first version: *"App is pretty hard to use right now — unclear how to go
from start to finish."* The diagnosis was that the app was a refinement tool assuming five
terminal steps had already happened, the hardest of which was **hand-authoring an EDL**. That is
closed:

| step | was | now |
|---|---|---|
| new project | — | `--footage` alone; a missing EDL is scaffolded ✅ |
| analyse | `audio_analyze.py` in a terminal | in-app, progress counted from sidecars on disk ✅ |
| junk / orientation | manual `--skip`, prior study | **still not proposed** ❌ (`--orient` at scaffold, `skip` on the analyse call) |
| **first cut** | **hand-authored JSON** | **Ask originates it** ✅ |
| refine | the board, and it works | ✅ plus a five-step strip saying where you are |
| render & compare | one file, newest only | versions list, A/B players ✅ |

Also done: **startup preflight + backend status in the header** — a missing CLI on PATH or an
absent API key is reported for free at launch, and one tiny call (cheap role, background,
`--no-probe` to skip) answers "is it authenticated", which nothing free can see.

90 tests, ~41s (75 API + 15 driving real Chromium), still against the synthetic three-clip
project, so they need neither `~/footage` nor a GPU.

### Three defects that only appeared when a human used it

All three were invisible to a green suite, and all three are the same shape — **the tests drive
stubs that behave better than the real thing**:

- **Progress rode on the child's stdout.** `audio_analyze.py` prints without flushing, so a pipe
  held everything until exit: a real 12-clip run sat at `0/12` for two and a half minutes and
  then jumped to done. The count was right, the trigger was wrong, and it is indistinguishable
  from a hung job. A ticker polls on its own clock now. *(The suite's stub printed per clip,
  promptly, so it could never have caught this.)*
- **Renders were not per-bin.** Killington opened announcing *"✓ render 1 version"* and played a
  **Copper** cut in the A slot. Proxies had been made per-bin; renders had not.
- **A render announced `done` before writing its metadata**, and the UI refreshes its versions
  list on exactly that signal — so a fresh cut could appear unlabelled. Same ordering lesson as
  the previews stage, one place further along.

The pattern is worth more than the three fixes: **run the real thing and watch it.** Each of
these took one look at real output and none of them would have surfaced from the test suite.

### What originating produced, live

One call, empty EDL, the agreed brief as the story — a brief that **never mentions milk**:

> 20 segments, 172.7s, 16 of 17 clips, 98s, $0.32 projected (27,960 in / 7,250 out).

It found the milk joke by itself and built *"the day Spenny became the milkman"* as the spine —
setup indoors (*"How's your milk, Spenny"*), payoff on the hill, *"I'm milked out"* as a chant,
closing on *"we created a legend — and now it's over."* Travel held to four cuts / ~31s, as the
brief asked. It declared its own blind spots unprompted: the 18s held shot with no transcript
that *"may be a glove or a lift queue"*, and the one clip it would not gamble on. Rendered:
172.97s, 18ms A/V drift, no rotation, −16.1 LUFS, no black runs →
`Documents\Roughcut Labeling\cuts\copper-first-cut.mp4`.

Session 2 found the milk joke too — but that was a human reading 17 contact sheets and
transcripts. This was one call from an empty timeline.

## The verdict on originating: yes (Karl, 2026-07-25)

> *"I kind of liked the copper-first-cut.mp4 better than the other ones — I think a large part
> of that was it just seemed like the cuts worked better / were longer with the right amount of
> words in it, not cutting off sentences like some of the others. Some weirdness where airport
> footage was cut seemingly out of order in a way that didn't make sense, but it wasn't too bad,
> and I feel like I could refine it if I worked with the tool at all."*

**The agent-originated cut beat both hand-selected ones.** Three things follow, and none of them
is what the metrics predicted:

1. **Do not chase the snap metric.** 12 of 20 out-points landed on an utterance end when
   originating, against 17 of 18 when revising — and Karl praised the originated cut's
   boundaries specifically. Longer segments carrying whole thoughts beat tighter snapping. The
   number was measuring the wrong thing. **Auto-snap on an originated plan is off the list.**
2. **The chronology defect was real and is fixed.** It was not judgement — the model had never
   been given a capture time. Clips now carry one, both prompts render it as relative position,
   and a re-run took in-session backwards cuts **5 → 1**, with the survivor deliberate and
   declared in its own notes. `.../cuts/copper-first-cut-v2.mp4`, 2:16, unwatched.
3. **The loop is good enough to work in.** *"I could refine it if I worked with the tool"* is the
   answer to the question that opened session 3.

**D8 decided: stay on `claude_cli`** — Karl, same session, *"we still need to use the cli since
I am developing locally."*

**Chronology is a lever, not a rule** (Karl, same session: *"we don't ALWAYS need to go
chronological"*). The first wording read as a constraint and the cut that came back was almost
strictly in order. The guidance now says the film's order is a choice, that a strictly
chronological cut is usually the dullest one available, and that only the *accidental* kind of
backwards move is a mistake.

## Killington is ready to run, in the app, by hand

```
uv run app/server.py --footage ~/footage/killington-neutral
```

Everything the run needs is prepared and verified:

- **`~/footage/killington-neutral/`** — `CLIP_01…12` symlinked in capture order, `mapping.json`
  beside them. The real filenames are hand-named after their content
  (`spenny-bigair-begin`, `rockhitmarkers`), and clip names reach the prompt, so running the
  original folder would not be a blind test — it would be scoring the agent on a human's
  selection work.
- 12 clips, 35s–319s, ~44 min. No junk pass needed: every clip is long-form, and the LRV/THM
  files are filtered by extension already.
- `--orient auto` is the default and is right here — rotation side data is present and correct
  (unlike Copper, where it is spurious).
- **Fully warmed and verified**: 12/12 sidecars, 12/12 proxies in the default `--work`; every
  proxy decodes and matches its source length. The launch is instant.
- Run it from a **WSL login shell** — `uv`, `ffmpeg` and `~/footage` all live there, and a
  non-login shell misses `~/.local/bin`. From Windows:
  `wsl -d Ubuntu -- bash -lc "cd /mnt/c/Users/karl/Documents/Projects/roughcut && uv run app/server.py --footage ~/footage/killington-neutral"`,
  then `http://localhost:8765`.

**Karl ran this bin by hand at the end of session 3; what he reported shaped sessions 4–6** —
see START HERE for where the bin stands now.

The interesting question this answers: **does any of this generalise, or have the prompts been
fitted to one trip?** Every judgement so far is on Copper.

## ⇨ START HERE NEXT SESSION

**Sessions 4–6 (2026-07-25 → 2026-08-22) in one paragraph.** Karl ran Killington in the app,
and his report drove session 4: Ask became a background job with a clock (a 112s call had frozen
the whole server), the board autosaves (he lost a 16-shot cut to a refresh — recovered from the
CLI's transcript into `~/work/app/asks/killington-neutral/recovered.json`), the buttons that meant
nothing went, and *"the analysis missed… me falling into a river"* produced the prototype visual
pass, which found the fall at 104–108s of CLIP_01. Session 5 built the music bed and tuned its
ducking to speech (docs/EFFECTS.md). Session 6 turned the board into something you can edit in:
a **monitor** that plays the whole cut from the proxies (no render), a **Look at the footage**
button (since retired for `/open`'s one Index button, 2026-09-08) that ran the visual pass with
its price on it and put what it saw on the shot cards and under *Add a moment → seen*, and a **Music** panel whose bed is heard under the monitor and
rendered by assemble.py. All three were verified live on Killington as well as under the suite
(116 tests, 94 API + 22 browser). The commits are `3b8caff`, `b77cf0b` and the one after.

**Where Killington stands, on disk:**

- `~/work/app/projects/killington-neutral.edl.json` — **the 16-shot cut is back** (2:57.4,
  restored from the recovered proposal through the board's own recovery path), story set,
  `effects_music` = `music/whatever.mp3` at 12 dB of duck. Renders in
  `~/work/app/renders/killington-neutral/`: `cut_adf8ce95` (Jul 25, no music) and the one made
  this session with the bed under it, marked ♪ in the versions list.
- The visual pass has seen **all 12 clips** — the last 9 run from the board's own button after
  Karl logged in: 22 sheets, 22 read, $1.70 projected against the $1.98 quoted, 20:46 wall
  clock. Sidecars in `~/work/app/visual/killington-neutral/` (per bin now; the three older ones
  copied from the old shared `~/work/visual`, which is left in place). Across the bin it found
  what the transcripts could not: a backflip and a tumbling crash in CLIP_04, sustained airs in
  CLIP_07, a hard inverted crash in CLIP_09, both CLIP_11 flips and a wipeout, a crash aftermath
  in CLIP_06 — and the 16-shot cut, chosen from words, used two of them.
- **The board already found something with what it has.** The cold open, CLIP_01 188.2–199.6
  (*"I fell in and I got completely buried"*), overlaps the pass's unusable stretch 188–196 —
  *"nearly black — lens obstruction"*, *"severely motion-blurred"*. The first shot of the cut is
  mostly unusable picture, chosen for its line: the blind-selection defect, caught on a card
  instead of in a render.
- **Two proposals exist on disk, neither applied** (`~/work/app/asks/killington-neutral/`):
  - `c710983b` — a *blind* origination, story only, no note, after all 12 clips were seen: 16
    shots, 176.9s, $0.58, 199s. It **no longer opens on black** — the confession became a coda
    at 195.5s, past the blurred stretch; CLIP_02's opener ends "before the black/white frames";
    the one unusable tail it kept (CLIP_08's last frame) it named and asked to trim the picture
    under held audio. The middle is built from the events. But it **still left out the river
    fall**: the frames call 104–108 only "person down in snow", and nothing in the story says
    that moment matters.
  - `ed8134bb` — a *revision* of the restored 16-shot cut with a 178-word note naming the fall
    and the unusable cold open: 20 shots, 178.0s, $0.57, 178s. Opens on the fall itself (CLIP_01
    92.2–107.0), keeps the confession only on its 3.4s of usable frames as the payoff, drops the
    20s straight-liner that had been chosen for two lines over undescribed picture, and spends
    it on the CLIP_04 backflip and crash, CLIP_09's crash with "that was close" as its reaction,
    the CLIP_11 wipeout out-pointed at exactly 256 to clear the unreadable stretch, and CLIP_07's
    air by extending the conquistador bit rather than adding a shot. **The board offers this one
    on open** (*last proposal — 20 shots · show it*). It is rendered as `cut_91e0b993` — labelled
    *proposal — not accepted* in the versions list — and copied to
    `Documents\Roughcut Labeling\cuts\killington-revision-proposal.mp4` so it can be watched
    against `killington-first-cut-music.mp4` before deciding.

**Karl watched the proposal render and gave notes (2026-08-22, evening). Four agent branches
answered them and are merged into `main`** — `agent/boundaries`, `agent/render`,
`agent/playback`, `agent/events`; every change has a CHANGELOG bullet. In his order:

- *"Missed the ski patrol discussions — ensure the transcript is generated correctly."* **The
  pipeline was at fault.** `audio_analyze.py` ran faster-whisper with Silero VAD on, which deletes
  helmet-mic speech under wind before Whisper decodes it. With VAD off, CLIP_09 gains "Is there
  ski patrol?" / "be careful, there's ski patrol over there" (109.9–115.8s), lines no transcript
  had, and the bin gains 22% more words (1361 → 1656). Sidecars were re-run (the old ones are in
  `~/work/app/audio/killington-neutral.bak/`). A blind origination on the corrected transcripts
  chose "the ski patrol scramble" unprompted (`~/work/retx/live_originate.json`, kept out of the
  board's asks).
- *"Clips too long (trailing off) and too short (POCKET PI[ZZA])."* One defect: a transcript
  timestamp records when a word was decoded, not when its sound stops — measured on eight line
  ends, a word keeps sounding a median 0.12s (max 0.26s) past its `e`. `roughcut/boundaries.py`
  now polishes every proposal: out-point inside or within 0.3s after a word → word end + 0.45s;
  in-point inside a word → word start − 0.25s; more than 1.5s of dead air after the last word →
  trimmed to last word + 0.45s. Wordless shots, unusable stretches and dead air held on a
  *notable* visual moment are never touched; moves are capped at 1.2s and recorded as
  `polished_from` (shown under the shot's `why` in the proposal panel). Not the old snap: nothing
  reaches for the next utterance. On ed8134bb: 11 of 20 shots adjusted, CLIP_03 18.40–20.90 →
  18.09–21.23, so "pizza!" finishes.
- *"The cut board doesn't seem to allow me to play videos."* **Reproduced, two defects.** The
  monitor sits at the top of the column, so playing from a card far down played correctly
  off-screen — a play now scrolls the monitor into view. And on the real bin the first frame
  arrived ~3s after the click (16 card previews each opening an 85 MB proxy ahead of it), so a
  second press paused a monitor that had not started while the first press's deferred play then
  ran behind a dead transport — monitor commands are counted now, and a refused play or media
  error is written on the screen. Ranges stream in 1 MiB chunks (server peak memory 1.15 GB →
  90 MB); `/` and `/app.js` are `no-store`. Firefox is untested.
- *"Cut board doesn't reflect the render."* Expected: the watched file was the proposal,
  rendered but never accepted, against a board holding the cut. The versions list now says
  **· this cut** on renders that match the EDL and **not accepted** on the proposal, and the
  board matches its own render to within 4 frames (the renderer's known +0.16s over 16 pieces).
- *"Quality loss from the raw footage."* Right, and a tradeoff rather than a bug. Killington's
  sources are true **4K** (3840×2160, 8-bit Rec.709 full-range — `benchmarks/README.md` had it
  wrong and is fixed: 9 clips H.264 30000/1001, 3 clips HEVC 60000/1001), and the preview
  profile's 1080p/23.976 target is where the loss is: the 2× downscale is the dominant softening,
  and the fps conversion drops every 5th (30p) or 3 of 5 (60p) frames in a regular cadence.
  Encoding is near-transparent (VMAF 99+), and neither the concat nor the music pass re-encodes
  video (MD5-verified). `assemble.py --profile delivery` — native fps, up to 4K, slow/CRF 18 —
  is selectable next to Render; a full delivery render of this cut is **~17 minutes**.
- *"Missed cool jumps — limited keyframe analysis and no sort/priority."* **Half right.** A free
  motion track (scdet over the 720p proxy at 10 Hz) × the R8 onset track picks candidate
  windows, and a 1s close look at 23 of them ($1.68) found four events the 4s pass missed. But
  adjudicating eleven claimed jumps/flips by eye, **none survived as described**: on a helmet or
  chest mount the horizon sits at 40–45°, so the *camera* is inverted and both passes read it as
  a person inverted mid-air — three of those were shots the proposal cut on (9, 10, 14). A close
  look is a good auditor and a poor detector. A fine sheet costs the same as a coarse one
  (~$0.07 — the prompt is what is paid for), so density is cheap and coverage expensive
  (whole-bin 1s ≈ $70). Built: `events.json` per bin ranking kind × notable × corroboration ×
  confirmation × usable; a "## Events, ranked" section before the inventory in both prompts; the
  seen tab sorted by score with a confirmed/unconfirmed seal; the in-app visual pass as two
  stages (coarse → scan → close look at 3 windows per clip, priced separately). Study:
  [research/R10-events-priority.md](../research/R10-events-priority.md).

**He did that, and it worked.** The revision he asked for was accepted and is the cut on disk:
**17 shots, 180.98s**, and `CLIP_09 105.3–116.71` is in it — the ski-patrol beat, which no
transcript in this project contained until the VAD fix earlier the same day. The boundary polish
shows in the same file (`CLIP_03 18.09–21.23`, so "pizza!" finishes instead of clipping at
20.90). He rendered it at `delivery`: `cut_110ecb13.mp4`, 3840×2160, 3:01, 987 MB, 17 shots,
with the bed.

**Then he reported three things, all now answered** (branches `agent/progress` and
`agent/preview`, merged; every fix has its CHANGELOG bullet):

- *"Several progress bars need help… when I ask for changes from the AI the first step must be an
  estimate of how long it will take… milestones… a progress tracking bar up top, re-use across
  app."* Built. `roughcut/progress.py` is one job shape behind analyse, visual, render and Ask;
  `roughcut/estimate.py` makes one cheap call before an Ask that returns an ETA and milestones
  (validated like a plan, one bounded re-ask, and it can never block the work); the CLI backend
  streams (`--output-format stream-json`) so milestones complete on **real events** — "17 of ~17
  shots decided" is the plan being parsed as it arrives, not a timer — and the ETA recalibrates
  at each one. Live: predicted **80s**, actual **79.1s**. One strip under the step strip carries
  every long operation, draws two at once, and re-attaches after a reload. Analyse, visual and
  render take *computed* estimates rather than model ones, deliberately: their length is
  arithmetic the app already does, so a call there would spend $0.03 to be less accurate.
- *"I can hear the videos when I click play, but the preview window still shows up blank."* Two
  causes, both fixed. Shot cards were 17 autonomous `<video>` elements each streaming an 85 MB
  proxy, so the monitor's own request queued behind them (page-load media traffic **0.2–2.5 MB →
  93 KB**); they are JPEG posters now. And the monitor was `preload="auto"` with the src set
  before anything said where the shot starts, so Chrome fetched from **byte 0** — playing a shot
  at 188.2s it buffered 0–15s. It is `preload="metadata"` with a `#t=` fragment now and buffers
  at the shot. Live after the merge: first painted frame **631 ms**.
- *"Renders get stuck loading forever… only played for like 3s before video buffers. Make it
  clear how to download."* See roadmap item 2 below: review copies, the download control, and
  the render job that no longer claims to be finished before there is something to watch. Also
  **one render at a time** now — he asked whether clicking Render repeatedly could break the
  state; it could not corrupt output (every job owns its id, parts dir and file) but nothing
  stopped two 4K encodes competing for the box, so `/api/render` answers 409 and the control
  reads *Rendering…*.

**Karl's next move:** open the board (8765 runs the merged code) and watch the cut he accepted —
in the monitor for the edit, or the A/B players for the renders, which now stream a 720p copy
and offer `↓ download` for the master. The open question is the film itself: is the 17-shot cut
good? If it is a highlight reel, that is the finding — do not fix it by hand. Beyond that,
roadmap item 1 (auditing what the sheets claim) is the biggest lever left.

Whatever Karl reports is the first input of the next session.

## The roadmap after that

Ordered by what changes most, not by effort:

1. **Events: audit what the sheets claim, then fit the rank.** R10's first follow-up is one
   line: spend the close-look windows on the coarse pass's *own claims* (two refuted events still
   rank 6th and 20th because the motion scan never covered them), not only on motion peaks; and
   fine-pass positives should not inherit a neutral 1.0. The weights are argued, not fitted —
   there is no labelled set; nine adjudications measured the failure mode, not a rate. The
   camera-inverted false positive needs its own guard (horizon angle from the frame, or "is the
   *ground* at 45°?" in the sheet prompt). Junk and orientation could be proposed from the same
   sidecars. RQ-1/RQ-7 (sheet density, thumbnail size, model tier) remain unmeasured.
2. ~~**The board's first frame.**~~ — **done.** Karl: *"I can hear the videos when I click play,
   but the preview window still shows up blank."* Two faults, both measured on the Killington
   bin: the cards' 17 `<video preload="metadata">` proxies took every connection Chrome allows,
   so the monitor's own request queued behind them (first painted frame **6.8–9.5 s** after
   `playFrom(0)`, mean pixel 0.0 until then, audio playing throughout); and the monitor's own
   elements were `preload="auto"` and had no in-point in their URL, so Chrome downloaded from
   byte 0 — buffered `0–15 s` while playing at 188.2 s. Cards are `<img>` posters from
   `/media/poster/<stem>.jpg?t=<in>` (~6 KB, cached under `--work`, immutable) and the monitor
   is `preload="metadata"` with a `#t=` fragment. First frame **~0.9 s**, media bytes per page
   load 93 KB. Still untested: Firefox.

   **Read those numbers with their conditions.** The 6.8–9.5 s was measured on a box that had
   just finished a 4K delivery render. Re-run back to back on the same warm, idle machine, main
   paints in 0.74–0.79 s and this branch in 0.89–0.91 s — with the proxies in the page cache and
   the cores free, seventeen card streams cost nothing and a poster is one extra fetch. Under a
   render-shaped load (one `libx264 -preset slow` pass over the 4K master) the gap comes back:
   5.6 / 5.7 / 3.1 s before against 3.2 / 2.6 / 3.6 s after, 19 requests against 15, and 93 KB
   of media every run against 0.2–2.5 MB. The defect is not "the board is ten times slower", it
   is that **the first frame used to degrade with whatever else the machine was doing** — which
   is the machine Karl edits on while it renders. Only the buffered-range fix is
   condition-independent: main buffers `0–15 s` for a shot at 188.2 s in every condition
   measured and this branch never does. Anyone re-checking these should say what the box was
   doing at the time.

   The same weight closed the render complaints in the same pass. Karl, on the finished 4K
   delivery render: *"jumping all around the place, looks bad"* and *"stuck in this loading
   forever place... only have played for like 3s before video buffers"*. The file is sound —
   5427 frames at a clean 1/29.97, no anomalies — but it is 987 MB at 43.6 Mbps and the A/B
   players streamed it: 157 MB pulled in 15 s of watching, against 5.3 MB for a 720p copy of
   the same cut. Renders get a review copy under `--work/reviews/<bin>/` now, one at a time
   and as the render's own last phase — merging this with the progress work made that the
   honest shape, since the players stream the copy and the top bar was reading 100% while it
   was still being made (39.7 s off a 1080p master, 95.4 s off the 4K one, measured). The
   players play that, each row states its size and resolution, and a Download serves the
   master as an attachment named for the bin, the shots, the length and the quality.
   **Caveat worth carrying:** with the cards no longer
   streaming, the 4K master played 14.5 s of film in 15 s of wall clock with zero dropped
   frames on this box, so what Karl saw was contention rather than the master being
   unplayable on its own. The review copy is an 8.6x cut in page traffic and the right fix
   for the A/B case; it is not a proven cure for a symptom that no longer reproduces alone.
3. **ASR.** A tuned low-threshold VAD might recover the 14 words CLIP_06 lost without losing the
   patrol lines; CLIP_05 transcribes to zero words at `speech_fraction` 0.52 — that is the weak
   Tier A detector, not Whisper.
4. **Effects, per docs/EFFECTS.md's build order** — the bed is done; next the `sfx` / `overlay`
   vocabulary with the asset manifest, then onset snapping (100ms, from data already on disk —
   `roughcut/events.py` now reads it), then markers in the board (the monitor and the strip now
   exist to carry them), then the Ask path that turns "hitmarker when my skis hit the rocks" into
   effect objects.
5. **Junk and orientation proposed, human confirms** — the last ❌ in the table above, and all
   that stands between the app and a bin nobody has studied. Standalone it is a per-bin
   *measurement*, not a model call: `luma<11` for junk (be conservative — a naive `luma<35`
   false-positives on the night parking lot and the dim plane interior, both real content),
   orientation per-clip from a sheet the human confirms — or from the visual sidecars, now that
   they exist.
6. **Music mode (P2.6)** — a bed is built; *cutting to* a track is not. It is a genuinely
   different selection problem ("fill these N slots of these lengths"), which is why it has not
   been picked up casually. Karl's *"a cut that works perfect with a jump and the music"* was
   luck; nothing aligns a cut to a beat yet.
7. **Decide the fate of T0–T13.** The pipeline has been on hold for six sessions while throwaway
   tooling produced cuts Karl endorsed. The honest question is no longer "is the pipeline worth
   building" but "is *anything* in it worth building that the app does not already do" — and the
   answer may be a much smaller list than thirteen tasks. Worth an explicit decision rather than
   indefinite hold.
8. **Telemetry** — moved to [docs/INTAKE.md](INTAKE.md) M6, with Karl's rules (optional,
   weighted with the other passes, never over-indexed; numbers only until R11 measures it).

Smaller, whenever: a project picker (one bin per launch today); scrub-to-trim on the monitor
instead of ±0.25s buttons; `complete_many` has no caller.

Independent work: ~~audio event tagging~~ — **done, and it is a dead end on B1**; see
[R9](../research/R9-events-and-wind.md). The remaining audio lever is a **prosodic** rather than
lexical read of excitement markers — see the open thread in the selection note.

## Findings that must not be re-litigated

## Findings that must not be re-litigated

These were measured, cost real effort, and are easy to accidentally undo:

- **A finished render is not a watchable file, and "it looks broken" is usually contention.**
  Karl's 4K delivery render read as *"jumping all around the place"*; the file is structurally
  perfect (5427 frames, every interval exactly 1/29.97, no gaps or duplicates) and simply heavy —
  987 MB at 43.6 Mbps, **92.8 s of CPU to decode 181 s of video** against 16.1 s for the 1080p
  render on the same box. Packet-order PTS looks non-monotonic on a `preset slow` encode; that is
  B-frame decode order, not damage — do not chase it. Anything the browser has to *play* gets a
  720p copy; the master is for downloading.
- **Say what the box was doing when you measure the UI.** The 6.8–9.5 s first frame that
  justified the poster work reproduced three times — on a machine that had just finished a 4K
  render. Idle and warm, the old code was already 0.79 s. Both readings are true and only one is
  the defect (an editor whose first frame degrades with whatever else the box is doing, which is
  exactly how Karl works). A number without its conditions is a number the next session cannot
  check.
- **The preview render's "quality loss" is real, measured, and not a bug** (2026-08-23, prompted
  by Karl watching `cut_91e0b993`/`cut_29ed8c3f` and suspecting an encoding problem). Killington
  is 4K (3840×2160; 9 clips H.264 29.97fps, 3 clips HEVC 59.94fps — `benchmarks/README.md`'s B2
  row had this wrong, recorded as uniform 29.97fps), and `assemble.py`'s preview profile has
  always targeted a fixed 1920×1080 @ 24000/1001 for board-refresh speed. On one high-motion 9.9s
  shot, the dominant *visible* cost is the 2×/2× downscale (a matched-frame crop of fine detail —
  bare branches, a chairlift cable — is clearly softer at 1080p than at native 4K; a low-detail
  snow crop barely differs), not the frame-rate conversion (which drops a clean, regular 20%/60%
  of frames on the 30/60fps clips — exact ratios, since GoPro's `/1001` denominator cancels — not
  an erratic pattern) and not `-preset veryfast -crf 20` (already near-transparent at its own
  target size: VMAF 99.2–99.4 against a same-size lossless reference). Colour is fine — 8-bit
  Rec.709 full-range, not HLG/Log, and the full→limited range retag does not clip. The concat
  `-c copy` path and the music mix never touch the video bitstream (verified with a second music
  pass on both real renders: identical MD5 and frame count). `assemble.py --profile delivery`
  now exists for when the loss matters: the bin's own majority frame rate, up to 4K without
  upscaling, preset slow, crf 18 — estimated ~17 minutes for the full 178s Killington cut from
  one shot's measured rate (untested at full length; over the ~10-minute bar for actually running
  one).
- **Run Whisper without the VAD filter on this footage.** Silero at its default threshold
  classifies helmet-mic speech under wind as non-speech and deletes it before Whisper decodes
  anything: the ski-patrol lines in CLIP_09 were absent from every transcript, surviving text
  was stitched across removed silence (a 6-word, 92.8s "utterance" in CLIP_07), and
  `asr_speech_fraction` was inflated 4×. With VAD off the bin gains 22% more words and the
  hallucinations it adds are caught by the existing `no_speech_prob > 0.6` filters. Measured,
  session 6 (agent/boundaries).
- **A transcript's end timestamp is early.** A word keeps sounding a median 0.12s (max 0.26s)
  past the `e` the sidecar gives it — measured band-limited 300–3400 Hz on eight line ends. Cut
  at word end + 0.45s, never at the decoded end; that is "POCKET PI[ZZA]".
- **A contact sheet's `kind` is a claim, not evidence.** Eleven claimed jumps/flips on Killington,
  adjudicated by eye at 1s: none as described — the camera is inverted on a helmet or chest
  mount, not the person. Corroborate with the motion track and the onset track; use a close look
  to audit (its junk/unusable calls were all right) rather than to detect. And a sheet costs
  ~$0.07 whatever it spans, so sampling density is cheap and coverage is expensive (R10).
- **Killington's sources are 4K 8-bit Rec.709 full-range, 9 clips at 30000/1001 and 3 at
  60000/1001 — not 5.3K** (B2's manifest row was wrong). The preview render is 1080p/23.976 by
  choice: the 2× downscale is the visible loss, the fps conversion drops frames in a regular 5:4
  / 5:2 cadence, the encoder is near-transparent, and nothing after the part encode touches the
  video. `delivery` exists for the real thing and costs ~17 minutes per 3-minute cut.
- **The visual pass is necessary and not sufficient, and the event that mattered most was the
  least legible one** (session 6). With every Killington clip looked at, a blind first cut
  stopped opening on black frames and found the backflips and crashes the words-only cut had
  missed — and still left out the river fall, because sampled frames describe it only as
  "person down in snow" and the story never said it mattered. A 178-word note fixed that in one
  revision. Looking is what makes the note *actionable*; it does not replace it (P2.5).
- **Once clips carry visual moments, every Ask is ~40k tokens in, ~15k out, and 3–4 minutes on
  the CLI backend.** A 300s call timeout killed a revision with the work unrecoverable (the CLI
  writes its transcript at the end); the default is 600 now. Do not "tidy" it back down.
- **Orientation is per-clip and cannot be generalised.** B1/Copper's rotation side-data is
  *spurious* (ignore it, `--orient none`); B2/Killington's is *correct* (apply it, `--orient
  auto`, 7 of 9 clips are stored upside down). Same owner, same sport, opposite handling.
  Decisions committed in `benchmarks/labels/B{1,2}-orientation.json`.
- **44% of Copper is dark/junk** (290s of 662s). Confident junk band is ~`luma<11`. A naive
  `luma<35` threshold **false-positives** on the night parking lot and dim plane interior, both
  of which are real content. Be conservative; when unsure, keep.
- **Killington is pre-curated** — files are hand-named after their content. Only its nine
  long-form clips are usable, with filenames neutralized. Copper is raw, which is why it's B1.
- **Full-band audio RMS measures wind, not interest.** See AUDIO.md.
- **B1 is 70% on-mountain** (277s of 398s), not mostly travel. A first pass over 7 of 17 contact
  sheets concluded the opposite by reading the base-area and chairlift clips as "lodge" from
  their banter. Classify from the sheet, not the transcript.
- **There is a running joke about a gallon of milk** and it is the connective tissue of the
  trip — it appears in 6 transcripts and on screen in 5 clips, from the base area to the
  chairlift to a 60s payoff in GX010495 ("we created a legend"). Any cut that treats B1 as a
  pure ski montage throws away the thing the group will actually remember.
- **Audio points away from the skiing on B1** (R8 Result 4): 16.8 candidates/min on the
  travel footage vs 7.4/min on the on-mountain clips, and 4× the word rate. The skiing is
  quiet because the subject is far from the mic. Never gate the visual pass on audio interest.
- **B1 has no non-verbal reactions to detect** (R9). Best score for Laughter/Whoop/Cheering
  anywhere in 398s is 0.077, from a model that scores Speech 0.44–0.76 and Wind 0.65 on the same
  passes. The mic is on the camera and the skiers are 50m away. Don't rebuild this expecting a
  different answer on Copper; do run it on a bin with crowd or close-mic audio.
- **B1 *is* windy, and the R8 wind rule never fired once** (R9). Real wind sits at flatness
  0.17–0.25 against a guessed 0.40 threshold. Now `low > -40 dBFS AND low − speech > 2 dB AND
  flatness > 0.15` — the absolute term matters because a pure low-vs-speech ratio inflates
  whenever nobody is talking. Plane-cabin rumble is the trap a dB-only rule falls into: 10.9 dB
  of low-band dominance at flatness 0.008, because engine hum is tonal and wind is not.
- **The Tier A DSP speech detector is weak** (F1 0.63 vs 0.56 for "assume constant speech").
  Speech candidates come from the ASR transcript; the DSP tracks are quality metering and a
  no-ASR fallback. Don't rebuild it as a selector.
- **ASR is cheap**: `large-v3` on the 5080 runs ~13× realtime, so a 3h project is ~14 min.
  Run it over everything. CUDA works via pip `nvidia-*` wheels preloaded with `ctypes.CDLL` —
  no system CUDA, no sudo.
- **`yeah` and `dude` are filler in this footage, not reactions.** Scoring them as interest
  markers put banter at the top of the candidate list. Markers must be surprising to be evidence.
- **`drawtext` is not compiled into the installed ffmpeg** — do text composition in Pillow.
- **The transcripts alone are enough to find the spine of a bin.** An originating call with no
  edit to inherit, and a brief that never says "milk", found the milk joke across six clips and
  built the film around it (session 3). The words are the signal; R8 said so and this is the
  strongest evidence yet. It does not follow that the *pictures* are optional — the same call
  could not tell whether its 18s held shot was a run or a glove.
- **A green suite says nothing about a tool a human has not used.** Three defects shipped past 85
  passing tests and appeared within minutes of Karl opening the app: progress that only updated
  when a child process flushed (it doesn't), renders leaking between bins, and a job announcing
  itself before writing what the UI reads. Every one was invisible because the tests drive stubs
  that behave better than the real thing. Run the real thing and watch its output.
- **The snap metric disagreed with the human, and the human was right.** An originated cut lands
  12 of 20 out-points on an utterance end against 17 of 18 for a revision of an already-snapped
  cut — and Karl preferred the originated one *for its cutting*, praising segments that were
  "longer with the right amount of words in it". Whole thoughts beat tight boundaries. Do not
  optimise the boundary count, and do not auto-snap an originated plan.
- **A model with no capture times will scramble chronology, and it is not a judgement failure.**
  The first originated cut ran 20:43 → 21:40 → 22:17 → back to 21:04 → back to 20:53 through the
  travel section, because nothing in the prompt said what "before" meant. Giving it relative shot
  order took in-session backwards cuts from 5 to 1. Any new prompt path that lists clips must
  carry it.
- **Every Claude CLI call carries ~15–21k tokens of harness overhead**, whatever you ask.
  Measured: `claude -p 'Reply with exactly: OK'` billed 2 input + 5,558 cache-creation +
  15,273 cache-read for a four-token reply. A real revision billed 25,146 input against a ~2,100
  token prompt. Two consequences: token accounting **must** sum all three input fields or it
  reports near-zero (fixed in `inference.py`), and per-unit workloads on this backend would be
  ~95% overhead — which is SPEC §6.2's warning, now with a number, and the argument for making
  the API backend the app's default.
- **Prose passed through `wsl -- bash -lc '...'` from Windows is truncated at the first space,
  and a `;` inside it ends the command.** A live run silently received the one-word note
  "Tighten" and produced a plausible answer to it — nothing looked wrong. Always use a script
  file with heredocs (`research/tools/live_ask_b.sh` is the pattern), and echo prose back with a
  word count so truncation is visible.
- **`-noautorotate` does not stop rotation metadata reaching the output.** It suppresses
  *applying* the rotation, but the display matrix is still copied to the output stream, and
  concat with `-c copy` inherits stream properties from the **first part** — so one spurious
  matrix rotates an entire film. This shipped once (variant A, opening on GX010474's
  `rotation=-90`, played sideways end to end). Use **`-display_rotation 0`** before `-i`;
  `-metadata:s:v:0 rotate=0` is a no-op against a display matrix in ffmpeg 7. Six B1 clips carry
  spurious rotation: 474, 484, 492, 497, 498, 499. `assemble.py` asserts against it now.

## Environment

WSL2 Ubuntu 24.04, RTX 5080 (16GB, visible to WSL), 953GB free on ext4.

- Installed to `~/.local/bin` **without sudo** (sudo needs a password; static builds avoid it):
  ffmpeg/ffprobe 7.0.2, uv 0.11.32, claude CLI. PATH persisted in `.bashrc`.
- Footage: `~/footage/copper-02-2026` (26 MP4s, MP4-only — sidecar WAVs are out of scope),
  `~/footage/killington-01-2026` (39 files incl. LRV/THM).
- Sheets + label UI delivered to `/mnt/c/Users/karl/Documents/Roughcut Labeling/`.
- **Quoting gotcha:** inline `wsl.exe -- bash -c '...'` gets mangled by Git Bash PATH expansion.
  Always write a script file and run `wsl.exe -d Ubuntu -- bash /mnt/c/.../script.sh`.
- **From Claude Code on Windows (session 6):** the Bash tool also mangles backslashes in inline
  commands and in heredocs that mix quotes and backticks. Write every script to a file with the
  Write tool, run it with `MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu -- bash -l /mnt/c/.../script.sh`
  (`-l` for the login PATH), and patch repo files with a Python script that writes **bytes** —
  `Path.write_text(newline="\n")` produced CRLF on this host — then check for `\r`. The repo
  is `* text=auto eol=lf`, so a CRLF file would be rewritten on commit, but the tests run it
  from /mnt/c before that.

## Tooling built (all standalone, PEP 723 — `uv run <script>`)

| Tool | Does |
|---|---|
| `research/tools/contact_sheet.py` | Timestamped contact sheets; `--orient`, `--neutralize`; JSON index |
| `research/tools/orient_audit.py` | One frame per clip on one sheet, for orientation review |
| `research/tools/make_label_ui.py` | Click-to-label web page over a sheet dir; CSS sprites, CSV export |

## Open decisions

- **D5** footage location — resolved in practice (copied to WSL ext4); library still on Windows.
- **D6** labeling — **deferred**, possibly permanently. Superseded by analysis-first review.
- **D7** git remote — **resolved 2026-07-25.** `origin` is
  [github.com/swansk/roughcut](https://github.com/swansk/roughcut), private, `main` tracking it;
  34 commits pushed. The standing risk of three sessions is gone. Media stays out of git and
  stays regenerable from `research/tools/`.
- **D8** backend default — **decided 2026-07-25: stay on `claude_cli`.** Karl: *"we still need
  to use the cli since I am developing locally."* The overhead argument for switching was weaker
  than it looked anyway — a real Ask bills ~28k input against a ~26k-token prompt, so the harness
  overhead is a rounding error on calls this coarse, not the 95% it is for per-unit scoring.

**Decided:** the first cut runs on **Copper only** (Karl, 2026-07-25). Killington stays
untouched — it is pre-curated, and holding it back keeps it available as a cleaner second test
later.

## Artifacts outside the repo

| Path | What | Regenerable? |
|---|---|---|
| `~/work/audio/*.audio.json` | R8 audio sidecars, 17 clips + `index.json` | yes — `audio_analyze.py`, ~1 min |
| `~/footage/copper-02-2026` | B1, 26 MP4s, 7.6GB (MP4-only) | yes, from the Windows library |
| `~/footage/killington-01-2026` | B2, 39 files, 31GB | yes, same |
| `/mnt/c/Users/karl/Documents/Roughcut Labeling/` | 47 contact sheets + `label.html` per bin, 17MB | yes — `contact_sheet.py` then `make_label_ui.py` |

None of these are in git (media is gitignored) and none are precious — every one is reproducible
from the tools in `research/tools/`. The sheets remain useful as **analysis input** even though
the labeling workflow they were built for is deferred.

**B1's nine junk clips** (excluded from the sheets, and from the cut): `GX010479`, `GX010480`,
`GX010481`, `GX010482`, `GX010484`, `GX010485`, `GX010497`, `GX010498`, `GX010499`. Per-clip
measurements in [`../benchmarks/labels/B1-luma.json`](../benchmarks/labels/B1-luma.json).
