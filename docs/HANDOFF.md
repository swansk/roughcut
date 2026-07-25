# Handoff — read this first

Last updated: 2026-07-25, session 2 (audio analysis pass over B1).

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
5. **→ Karl watches the two variants.** This is the blocking step; nothing else should start
   until there are notes to act on.
   - `/mnt/c/Users/karl/Documents/Roughcut Labeling/cuts/copper-variantA.mp4` — "The trip", 1:54
   - `.../copper-variantB.mp4` — "The legend", 1:50

**After Karl's notes:** revision pass (closed loop — notes in plain language, agent applies
them). Optional independent work: audio event tagging (laughter / cheering / whoops), which R8
confirms is the biggest audio gap and targets the non-verbal reactions the ski clips contain.

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
- **The Tier A DSP speech detector is weak** (F1 0.63 vs 0.56 for "assume constant speech").
  Speech candidates come from the ASR transcript; the DSP tracks are quality metering and a
  no-ASR fallback. Don't rebuild it as a selector.
- **ASR is cheap**: `large-v3` on the 5080 runs ~13× realtime, so a 3h project is ~14 min.
  Run it over everything. CUDA works via pip `nvidia-*` wheels preloaded with `ctypes.CDLL` —
  no system CUDA, no sudo.
- **`yeah` and `dude` are filler in this footage, not reactions.** Scoring them as interest
  markers put banter at the top of the candidate list. Markers must be surprising to be evidence.
- **`drawtext` is not compiled into the installed ffmpeg** — do text composition in Pillow.

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
- **D7** git remote — **still local-only, 19 commits on one disk with no backup.** The single
  standing risk to the project. Raise it again.

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
