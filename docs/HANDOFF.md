# Handoff — read this first

Last updated: 2026-07-25, end of session 1 (spec + prototype tooling).

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

1. **Audio analysis first** — per [AUDIO.md](AUDIO.md). Local, GPU. Tier A DSP + faster-whisper.
   Read the wind section before writing any energy heuristic.
2. Selection over Copper's 17 non-junk clips, fusing audio candidates with keyframe context.
3. Assemble → loudness-normalize → render, with throwaway scripts (ffmpeg, no OTIO/SQLite yet).
4. Self-critique against [R6-rubric.md](../research/R6-rubric.md) + technical checks; iterate.
5. Bring Karl two variants.

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
- **D7** git remote — still local-only. Nine sessions of design work on one disk.
- Whether to run the first cut on Copper (6 min usable, generous 2:1 ratio) or Killington
  (40 min, forces real discrimination). Karl offered; not yet decided.
