# Handoff — read this first

Last updated: 2026-08-22, end of session 6 (the board became something you can edit in: a
**monitor** that plays the cut from the proxies, the **visual pass** runnable from the board with
what it saw on the cards, and **music** under the cut — all verified live on Killington).
Sessions 4–5 are summarised under "START HERE". Jump there, then "The roadmap after that".

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
button that runs the visual pass with its price on it and puts what it saw on the shot cards and
under *Add a moment → seen*, and a **Music** panel whose bed is heard under the monitor and
rendered by assemble.py. All three were verified live on Killington as well as under the suite
(116 tests, 94 API + 22 browser). The commits are `3b8caff`, `b77cf0b` and the one after.

**Where Killington stands, on disk:**

- `~/work/app/projects/killington-neutral.edl.json` — **the 16-shot cut is back** (2:57.4,
  restored from the recovered proposal through the board's own recovery path), story set,
  `effects_music` = `music/whatever.mp3` at 12 dB of duck. Renders in
  `~/work/app/renders/killington-neutral/`: `cut_adf8ce95` (Jul 25, no music) and the one made
  this session with the bed under it, marked ♪ in the versions list.
- The visual pass has seen **3 of 12** clips — sidecars in `~/work/app/visual/killington-neutral/`
  (per bin now; copied from the old shared `~/work/visual`, which is left in place). The other 9
  are one click in the Project panel: *Look at 9 clips · ~$1.08*, about 20 calls.
- **The board already found something with what it has.** The cold open, CLIP_01 188.2–199.6
  (*"I fell in and I got completely buried"*), overlaps the pass's unusable stretch 188–196 —
  *"nearly black — lens obstruction"*, *"severely motion-blurred"*. The first shot of the cut is
  mostly unusable picture, chosen for its line: the blind-selection defect, caught on a card
  instead of in a render.
- The river fall itself (CLIP_01 104–108, `fall`, notable) is the first item under *seen* and is
  not in the cut yet.

**The first thing to do is Karl's, not the agent's:** `claude /login` inside WSL. The header pill
read *"OAuth session expired and could not be refreshed"* this session, so live Ask and the
visual pass could not run (everything else could and did). Then, in the board:

1. Look at the other 9 clips (~$1, ~20 calls; the button says so).
2. Fix the cold open — the line is right and the picture is not; the *seen* list and the card
   warnings exist to choose with.
3. Put the fall in. Press space. Listen to the bed. Ask for changes.

Whatever Karl reports is the first input of the next session — same rule as before: if the cut
is a highlight reel, that is the finding; do not fix it by hand.

## The roadmap after that

Ordered by what changes most, not by effort:

1. ~~The visual pass~~ — **built, in the board, priced.** Still provisional (sheet density,
   thumbnail size and model tier are RQ-1/RQ-7, unmeasured), and the board only *shows* it so far:
   junk and orientation could be proposed from the same sidecars, and the originating prompt
   already reads the moments. The open question is whether a first cut made *after* looking at
   all 12 clips still opens on a black frame.
2. **Effects, per docs/EFFECTS.md's build order** — the bed is done; next the `sfx` / `overlay`
   vocabulary with the asset manifest, then onset snapping (100ms, from data already on disk),
   then markers in the board (the monitor and the strip now exist to carry them), then the Ask
   path that turns "hitmarker when my skis hit the rocks" into effect objects.
3. **Junk and orientation proposed, human confirms** — the last ❌ in the table above, and all
   that stands between the app and a bin nobody has studied. Standalone it is a per-bin
   *measurement*, not a model call: `luma<11` for junk (be conservative — a naive `luma<35`
   false-positives on the night parking lot and the dim plane interior, both real content),
   orientation per-clip from a sheet the human confirms — or from the visual sidecars, now that
   they exist.
4. **Music mode (P2.6)** — a bed is built; *cutting to* a track is not. It is a genuinely
   different selection problem ("fill these N slots of these lengths"), which is why it has not
   been picked up casually. Karl's *"a cut that works perfect with a jump and the music"* was
   luck; nothing aligns a cut to a beat yet.
5. **Decide the fate of T0–T13.** The pipeline has been on hold for six sessions while throwaway
   tooling produced cuts Karl endorsed. The honest question is no longer "is the pipeline worth
   building" but "is *anything* in it worth building that the app does not already do" — and the
   answer may be a much smaller list than thirteen tasks. Worth an explicit decision rather than
   indefinite hold.

Smaller, whenever: a project picker (one bin per launch today); scrub-to-trim on the monitor
instead of ±0.25s buttons; `complete_many` has no caller.

Independent work: ~~audio event tagging~~ — **done, and it is a dead end on B1**; see
[R9](../research/R9-events-and-wind.md). The remaining audio lever is a **prosodic** rather than
lexical read of excitement markers — see the open thread in the selection note.

## Findings that must not be re-litigated

These were measured, cost real effort, and are easy to accidentally undo:

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
