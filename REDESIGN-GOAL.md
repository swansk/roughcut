# THE GOAL: Roughcut redesign (closed loop, started 2026-10-14)

Karl, 2026-10-13: *"Next time I open Roughcut I want to actually use the video editor and feel good
about it."*

Karl, 2026-10-14, after trying the closed-loop branch (`f4b6f41`): *"I can't say I like it, it is not
clear how to re run the first pass, and this is not something that I feel meets 'premier pro
quality'. The system might have to do a complete redesign."*

So the bar is: **a person who edits video in Premiere Pro opens Roughcut, understands it at once,
and would choose to cut in it.** The AI first pass is what Roughcut adds on top of that, never an
excuse for an editor that is below it. A redesign is allowed and expected: nothing in the current
layout is sacred except the standing decisions in section S.

A fresh, harsh judge agent re-scores the current revision against this file every ~30 minutes
(`judge/JUDGE.md`) and writes `judge/verdict-<n>.md`. The loop ends after **two consecutive PASS
verdicts**. Section K (Karl's notes) outranks every score.

**PASS = every dimension ≥ 8 AND zero blockers AND every gate green AND no open K line.**
A dimension scored without evidence (screenshot, driven interaction, gate line, file:line) scores at
most 5. "Feels like Premiere" cannot be felt by an agent: score it from what a driven session shows,
measured against the Premiere reference in each dimension, and say what you compared.

## A. The first five minutes

1. **Orientation.** Opening Roughcut lands in an editor workspace (or a project picker that goes there
   in one click). A Premiere user can name every panel from a screenshot. Within 5 s on the fixture
   there is a picture, footage in a bin, and an obvious first action. Zero page errors.
2. **The first pass is a visible, re-runnable step.** (Karl's K1.) The AI first pass (index + picks + a
   proposed rough cut) has one obvious home in the UI: it shows what it will do, what it costs on the
   current backend, its progress, and its result. **"Run first pass" / "Re-run first pass" is findable
   from the main workspace in ≤ 1 click at every state** (fresh bin, mid-pass, after a pass, after
   manual edits), and a re-run says what it will replace or keep. Proven by a driven test that
   re-runs it from each state.
3. **Footage in, no ceremony.** Import/open a folder → clips in the bin with thumbnails, durations and
   metadata; any clip reaches the timeline in ≤ 2 actions (drag, or `,`/`.` insert/overwrite like
   Premiere) without waiting on the pass or a model call.

## B. Premiere parity: the editing core

4. **Workspace.** Docked, resizable panels in the Premiere arrangement: Project/Bin, Source monitor,
   Program monitor, Timeline, and an Inspector (Effect Controls-like) for the selection. Panel sizes
   persist. One dark, dense, professional theme.
5. **Timeline craft.** Video and audio tracks (V1/V2…, A1/A2…, linked A/V), snapping, selection,
   razor/split, ripple delete, trim (ripple/roll edges), slip, move, in/out points, markers,
   zoom-to-fit and zoom with the scroll wheel, a time ruler with timecode, and Premiere's keyboard
   map for the basics (J/K/L, I/O, C razor, V select, `,` `.` insert/overwrite, ⌘/Ctrl-Z/Shift-Z,
   +/- zoom, \ fit). Each proven by a test that drives it.
6. **Monitors and playback.** Source monitor plays the selected clip with In/Out; Program monitor
   shows the frame at the playhead and plays across cuts in order without stalls; frame-accurate
   scrub; timecode readout; audio meters. Measured: first frame after a seek ≤ 250 ms on the
   fixture, play across a cut with no visible gap in a recorded frame log.
7. **Inspector, effects and colour.** Selecting a clip shows its properties (time, speed, opacity,
   volume, colour/grade, effects stack) and edits apply live to the Program monitor; the existing
   grade, FX and generated-clip features live here rather than in their own modes.
8. **Export.** One Export action opens a dialog with presets, destination and a duration/size estimate,
   shows progress, and ends with a playable file whose length matches the sequence (ffprobe).

## C. Quality of feel

9. **Looks like a shipped pro tool.** Screenshots at 1440×900 and 1920×1080: consistent spacing,
   type and iconography; no overlap, clipping, wrapped buttons, placeholder text, debug output or
   default browser styling; dense like Premiere, not a form. The judge compares side by side with
   its knowledge of Premiere Pro and names the three biggest gaps.
10. **Responsive and reliable.** Interactions answer within 100 ms (measured in the driven session);
    no layout shift; every edit is one undo; autosave within seconds; nothing lost when switching
    project or reloading; no dead ends (every empty state offers its next action); errors say what to
    do.

## D. The AI part, done like a pro feature

11. **Proposals are reviewable edits, not a separate app.** The pass's picks and proposed cut appear
    in the timeline/bin as normal clips and markers the editor can accept, reject, or adjust with the
    same tools; why each pick was made is one hover/click away; cost is honest on the current backend
    ("free on your plan" on the subscription backend).
12. **The journey.** A scripted end-to-end session passes in real Chromium on a frozen snapshot:
    open the fixture → run the first pass → accept the proposed cut → trim two cuts, razor one,
    reorder one → adjust one clip's colour → re-run the first pass and keep the manual edits (or
    see exactly what would change) → export → ffprobe the file. Recorded as PNGs per step for the
    judge, and as `tools/journey.py` in the gate.

## S. Standing decisions (do not re-litigate; a broken one is a blocker)

- All inference goes through `roughcut.inference`; no model ID literal outside `roughcut/config.py`.
- On the subscription backend (`claude_cli`) no price reads as spending money; the API-equivalent
  estimate stays in the hover. Agents never pause for approval because a price is shown.
- Karl's footage and work dirs are never written by tests or the judge (`--work` under
  `~/roughcut-loop/`).
- INTAKE "Decisions (do not re-litigate)" still hold unless section K overrides one.
- No Co-Authored-By lines in this repo; every commit carries a CHANGELOG `[Unreleased]` entry.

## K. Karl's notes (verbatim, newest first; only Karl adds here, the builder may only mark one resolved with evidence)

- **K1 (2026-10-14):** "it is not clear how to re run the first pass" → dimension 2.
- **K2 (2026-10-14):** "this is not something that I feel meets 'premier pro quality'. The system might
  have to do a complete redesign." → the whole of B and C; the judge must not PASS a build that a
  Premiere user would call a toy.

## Blockers (any one = FAIL)

- A gate red: `python3 tools/gate.py <rev>` (suite, screens, hygiene; and `journey` once it exists).
- A page error or a stuck state in the driven session.
- A rubric item that is not implemented (a doc or plan is not an implementation).
- An open K line, or a broken standing decision.

## Rubric changes log

Any change to a dimension's definition or a gate threshold is logged here with the reason BEFORE the
next judge runs, and the next judge rules it honest or lenient.

- 2026-10-14: created from ROUGHCUT-GOAL.md (2026-10-13, 8 dimensions) + Karl's 2026-10-14 notes.
  The old eight are folded into dimensions 1, 3, 5, 6, 8, 9 and 10; the old gate stays the gate.
- 2026-10-14 (wave 1, lane WORKSPACE): `app/tests/test_budget.py` words/controls-in-view
  budgets raised for the board's states only (/floor and /open unchanged), because the board
  is now Premiere's five-panel workspace (menu bar, Project list, Source transport, Program
  edit buttons, five Effect Controls tabs in place of the five-tool rail). Controls in view on
  `/` 20 → 38, `/ shot` 33 → 51, `/ film` 19 → 37, `/ jobs` 20 → 38, `/ fx` 35 → 41; words
  `/` 92 → 94, `/ shot` 143 → 154, `/ film` 74 → 104, `/ jobs` 121 → 122 (measured values
  +~10 %, so growth still fails). Kept unchanged: one header row ≤ 50 px, exactly one blue
  button, no page scroll in any state, the strip in view, the monitor centred and ≥ 425 px
  tall at 1440×900 and not resized by a selection, Next within 3 s, zero page errors.
