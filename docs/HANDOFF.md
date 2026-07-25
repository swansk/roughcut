# Handoff — read this first

Last updated: 2026-07-25, end of session 3 (the app now owns the whole path — folder of
footage → analyse → **first cut from nothing** → refine → compare versions).
**The next session starts with a human gate: Karl watches an agent-originated cut. Jump to
"START HERE NEXT SESSION".**

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

80 tests, ~34s (65 API + 15 driving real Chromium), still against the synthetic three-clip
project, so they need neither `~/footage` nor a GPU.

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

## ⇨ START HERE NEXT SESSION: Karl watches the originated cut

**This is a human gate, and it outranks everything else in this file.** The whole project
question is *"can Claude compile a compelling video with good cuts from footage?"*, and the
version of that question nobody has answered is: **is a cut the agent originated from nothing
worth anything?** Session 2 answered "can it revise well" (yes). Watch:

- `.../cuts/copper-first-cut.mp4` — **agent-originated, 2:53** ← the new one
- `.../cuts/copper-variantB-asked.mp4` — hand-selected then agent-revised, 2:09
- `.../cuts/copper-variantB.mp4` — hand-selected, 2:43

The useful comparison is the first against the other two: same bin, same joke found, one of them
reached without a human reading a single contact sheet. Notes in plain language are enough —
the board applies them.

Under each plausible answer:

- **"This is close to the hand cut."** Then the hand-selection loop is optional, and the next
  work is quality-of-originating: auto-snap (see below), then junk/orientation in-app so a bin
  nobody has studied can be cut cold.
- **"It's structurally right but rough."** Then snap-on-originate and the boundary work are the
  next session, and the answer to "is the pipeline worth building" is probably yes.
- **"It's flat."** Say why in one sentence — that sentence is worth more than any metric here,
  and it likely means the visual pass (which does not exist) is the missing half rather than the
  prompt.

### Queued behind that gate

- **Snap an originated plan.** 12 of 20 out-points landed on an utterance end when originating,
  against 17 of 18 when revising an already-snapped cut. `edl_snap.py` fixes it in one click and
  nothing runs it automatically on a fresh plan — but "snap everything by default" is exactly
  the kind of silent rewrite the board avoids elsewhere, so it wants a decision, not a default.
- **Junk and orientation proposed, human confirms** — the last row of the table still marked ❌,
  and what stands between the app and a bin nobody has studied. Both are per-bin measurements
  (`luma<11` for junk; orientation is per-clip and never generalisable), so this is a measuring
  pass plus a confirm screen, not a model call.
- **DECISION — make the API backend the app default?** HANDOFF said yes on the CLI overhead
  finding; the number is now measured on this app's own traffic and is *weaker* than it looked.
  A tiny probe billed ~27k tokens (all overhead) but a real Ask billed 27,960 input against a
  ~26k-token prompt — so on coarse calls the overhead is a rounding error, not 95%. It stays
  claude_cli until Karl decides, since switching means an API key and real per-token cost.
- Music mode (FUTURE_PHASES P2.6), still unbuilt.

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
- **An originated cut is loosely snapped** — 12 of 20 out-points on an utterance end, against
  17 of 18 when revising an already-snapped cut. The difference is not model quality, it is that
  the revision inherited hand-snapped boundaries. Anything comparing the two must control for it.
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
- **D8** backend default — the app runs on `claude_cli`. Switching it to `anthropic_api` is
  Karl's call (API key, real per-token cost); the overhead argument that motivated it is weaker
  on this app's coarse calls than it looked. See the START HERE section.

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
