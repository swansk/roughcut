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

## E. AI-first, on top of the pro tools (Karl's K3)

Premiere's surface stays (section S). What changes is who can drive it: everything an editor can do
with the pro tools, they can also ask for in words, and get back as edits they review with those same
tools. The bar is a Premiere editor who has never seen Roughcut and never reads a doc.

13. **Footage in, from anywhere.** (K3 b.) File › Import (Ctrl+I, as in Premiere), an Import action in
    the Project panel (and in its empty state), a Media Browser for folders on the machine that runs
    the board, and dropping files or a folder from the viewer's OS onto the Project panel all add
    footage to the open project without leaving the workspace, restarting anything, or typing a path
    on a command line. New clips show a poster within 5 s of landing on the fixture, carry duration,
    frame rate and size, can be cut at once, and the free part of the index (posters, proxies, sound)
    starts on its own; a priced step is offered with its cost line, never run unasked (INTAKE). Footage added after a first pass is
    offered to the pass ("3 new clips: add them to the cut?") through the dimension-2 re-run, which
    says what it will keep. Proven by a driven test that imports a second folder mid-session and by
    one that drops a file.
14. **One way to ask the AI, from anywhere.** (K3 c.) One always-visible AI prompt in the workspace
    (a fixed home, a key to focus it, listed in the keys sheet) takes plain words about anything in the
    editor: "tighten the intro to 20 s", "warm up the snow shots", "dissolve between 4 and 5", "export
    a 1080p review copy". It shows its scope (the selection, the In/Out range, or the whole sequence)
    before it runs, a working state within 100 ms, honest cost (section S), and what it changed when
    it is done. An **AI menu** in the menu bar lists every AI action the app has (first pass, re-run,
    Ask, shot ask, colour Ask, Look deeper, Designed Effects, audit): none is reachable only from a
    panel you must first find. Asking about the clip under the cursor or the selected block needs no
    retyping of what it is (a right-click "Ask AI about this…" or the prompt's scope chip).
15. **The AI can do everything the pro tools do, as reviewable edits.** (K3 d.) One action vocabulary:
    the AI's answer is a list of the same operations the Timeline, Effect Controls, Lumetri, the Effects
    bin and Export apply when a human uses them, applied through the same code path. It covers at
    least: insert / overwrite / move / razor / ripple delete / trim / roll / slip, markers and In/Out,
    track add, speed, Motion / Opacity / Volume including keyframes, Lumetri (Basic, Curves, Wheels,
    HSL), transitions, Designed Effects, and an export with a preset. Every AI change shows before it
    lands (ghost blocks, a before/after on the Program monitor, a diff line per op in the proposal),
    and is accepted, rejected or adjusted per op with the same tools; one accept is one undo step;
    nothing the AI does reaches the timeline or the disk without that review (an export runs only
    when accepted). Proven by a driven test that asks, through a deterministic test backend, for one
    edit of each family and checks that the AI's op and the same op done by hand give the same EDL;
    and by the judge asking for real edits on Killington on the subscription backend.
16. **Easy to learn, the AI features above all.** (K3 e, a.) A first run teaches the loop in at most six
    short steps, each done on the real workspace, skippable, and re-openable from Help: import footage,
    run the first pass, read why a pick was made, review and adjust the proposed cut, ask the AI for an
    edit, export. Every AI control has a hover that says what it does, what it costs and what it will
    change. Every empty state names its next action (no footage → Import; footage, no pass → Run first
    pass; a pass, no cut → review the proposal). And it looks like it belongs (K3 a): the bin is
    Premiere's Project panel (List view with sortable metadata columns, Icon view with posters and
    hover-scrub), and the pass's result reads as editor objects in it: the proposed cut is a sequence
    in the bin, picks are subclips or labelled markers with their why, not a separate view. The judge,
    as a Premiere editor who has never seen Roughcut and reads no doc, does "import a folder → first
    pass → ask for two edits → adjust one by hand → export" and names every step where they had to
    guess; any guess at an AI step caps this dimension at 6.

## S. Standing decisions (do not re-litigate; a broken one is a blocker)

- All inference goes through `roughcut.inference`; no model ID literal outside `roughcut/config.py`.
- On the subscription backend (`claude_cli`) no price reads as spending money; the API-equivalent
  estimate stays in the hover. Agents never pause for approval because a price is shown.
- Karl's footage and work dirs are never written by tests or the judge (`--work` under
  `~/roughcut-loop/`).
- INTAKE "Decisions (do not re-litigate)" still hold unless section K overrides one.
- The Premiere-style pro controls built in the redesign stay (Karl, K3: "don't remove!"); AI-first means the AI
  can also do what they do, not that they go away.
- No Co-Authored-By lines in this repo; every commit carries a CHANGELOG `[Unreleased]` entry.

## K. Karl's notes (verbatim, newest first; only Karl adds here, the builder may only mark one resolved with evidence)

- **K3 (2026-10-16, after trying the paused branch; Karl said "no need to start yet"):** "off what we have
  currently, I still don't like the way the footage and pass look, there is no clear way to add new footage
  or to run the AI tooling on this - remember, it needs to have that premiere pro quality (I like having the
  controls you added! don't remove!) but with an AI-first operation. While I CAN use the pro tools here (good),
  AI should also be capable of all of it, and this whole thing needs to be easy to learn how to use,
  especially how to use those AI features" → (a) the footage and the pass's look: dimensions 1, 3, 9;
  (b) adding new footage is obvious (Premiere's Import / drag-in, not hidden): dimension 3; (c) running the AI
  tooling is obvious from anywhere: dimensions 1, 2, 11; (d) AI-first: every pro control (timeline edits,
  Effect Controls, Lumetri, transitions, export) can also be done by the AI on request, as reviewable edits:
  dimension 11 and beyond it; (e) easy to learn, the AI features above all: dimension 1. Standing: the pro
  controls stay ("don't remove!").
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
- 2026-10-15 (wave 1, lane WORKSPACE): `test_budget.py::test_the_monitor_is_centred_and_as_big_as_the_strip_leaves`
  no longer asks for a Program picture ≥ 425 px tall at 1440×900 (a number from the old
  one-monitor board, where the monitor had the whole column). In the five-panel workspace
  the Program monitor shares the top row with Project and Source, and its picture is an
  exact 16:9 box (so the grade/effect canvases and the sketch's clicks line up with the
  frame); the check is now that the picture fills its panel's room in one dimension, stays
  centred, and is not resized by selecting a shot. Panel sizes are the editor's to drag.
- 2026-10-15 (wave 1, lane WORKSPACE, after merging lane KEYS): `test_budget.py` words in view on
  `/` 94 → 105. Measured 96 once the timeline gained its timecode readout column and HH:MM:SS:FF
  ruler labels (lane KEYS) and the monitors switched to timecode; +~10 % as before.
- 2026-10-15 (wave 2, lane FINISH; verdict 2 ruled the dropped 425 px floor lenient and asked
  for a replacement): the Program monitor has a floor again, sized for the five-panel top row.
  In the **default layout at 1440×900 the Program picture is ≥ 380 px wide and its black
  letterbox is ≤ 30 % of its room's height**; persisted (dragged) sizes still win. Checked by
  `test_workspace.py::test_the_program_picture_fills_its_panel_at_1440x900` and by
  `test_budget.py::test_the_monitor_is_centred_and_as_big_as_the_strip_leaves`. Measured
  before: 389×219 in a 389×447 room (51 % letterbox; the judge saw 389×219 in a 511 px panel).
  After (the default top row is 0.42 of the height, Project 0.195 and Effect Controls 0.255
  of the width, Source = Program): 386×217 in a 386×294 room at 1440×900 (26 %), 517×291 in
  517×369 at 1920×1080 (21 %). Also new in the same test file, no threshold loosened: no
  overflowing chrome under `header`, `.ws-tab(s)`, `.plist td/th` or the monitor transports
  at 1440×900 and 1920×1080 (the app bar may ellipsize a job's name or Next's sentence only
  with a hover title), no link in the browser's blue or underline on `/`, and every `/open`
  card has a loaded picture within 10 s of opening a fresh work dir.
- 2026-10-15 (lead, wave 2): `test_budget.py` words in view `/ film` 104→107 and `/ jobs` 122→125, i.e. +3 each, exactly the words of the header control K1 requires ("Re-run first pass"). Measured on the merged branch: 106 and 124. `/ fx` unchanged (130). Controls budgets unchanged.
- 2026-10-15 (wave 2, lane TRACKS): `test_budget.py` board budgets raised for the track
  headers (Premiere's V2/V1/A1/A2 headers: lock, target, eye or mute/solo — 14 controls,
  ~9 words). Measured on the merged branch: / 103 · 49, / shot 154 · 61, / film 111 · 48,
  / jobs 129 · 49, / fx 131 · 52. Controls: / 38 → 54, / shot 51 → 67, / film 37 → 53,
  / jobs 38 → 54, / fx 41 → 57; words: / film 104 → 122, / jobs 122 → 142, / fx 130 → 144
  (measured +~10 %, so growth still fails); words on / and / shot unchanged (still under).
  /floor and /open unchanged; every other check unchanged.
- 2026-10-15 (lead, merging TRACKS onto FPUI): the two raises above are replaced by the measured counts on the merged tree, with NO headroom (TRACKS had added ~10 %): words / 106, / shot 157, / film 114, / fx 134, / jobs 132; controls / 50, / shot 62, / film 49, / fx 53, / jobs 50. /floor and /open unchanged. Cause: the four track headers (14 controls, ~9 words) and the Re-run first pass control (3 words).
- 2026-10-15 (lead, merging EFFECTS): `/ shot` (a shot selected, Effect Controls showing) words 157→196, controls 62→85, the measured counts with no headroom. Cause: Premiere-style Effect Controls (dimension 7): Motion x/y/scale/rotation, Opacity, Volume, five Lumetri values, each a scrubbable value with a reset, plus section twirls and resets. Verdict 1 noted the inherited budget pulls against Premiere density; the judge should rule whether this panel's density is earned.
- 2026-10-15 (wave 3, lane HEADER; verdict 3 ruled the FINISH entry's app-bar exemption
  lenient): **the exemption is removed.** `test_workspace.py`'s overflow check no longer lets
  anything under `header` ellipsize with a hover title (`#flowNext`'s sentence, `.fp-label`, a
  job's name): no element under `header` may have `scrollWidth > clientWidth`, checked at
  1440×900 at rest and with two jobs running (`test_no_chrome_is_cut_off`) and in the fresh,
  mid-index, mid-first-pass and exporting states (`test_the_app_bar_clips_nothing_and_a_fresh_bin_has_one_first_action`),
  which also asserts exactly one blue button on a fresh bin, "Run first pass". The bar
  collapses instead: Next shows its icon (sentence in the hover) while anything runs or when
  its sentence has no room, Next is not drawn when its action is the first pass's (index the
  footage; anything about the index or the first cut while the pass runs), and a job is an icon
  and its % with its name in the hover. `test_budget.py`: `/ jobs` words 132 → 109, the
  measured count (lowered, not raised); every other number unchanged.
- 2026-10-15 (wave 4, lane LAYOUT; verdict 4 punch 3, dimensions 4 and 9): **the Program
  floor is raised, 380 → 560 px at 1440×900, and a 1920×1080 floor is added, 800 px.** The
  default arrangement is now Premiere's Editing workspace: Source | Program share the whole
  top row (half each), Project | Timeline | Effect Controls the bottom row, the meters
  vertical beside the Program picture. Checked by
  `test_workspace.py::test_the_program_picture_fills_its_panel[1440x900, 1920x1080]` (width
  floor, letterbox ≤ 30 % of the room, centred in its room, meters mode `v` beside and as
  tall as the picture, Source = Program width, unchanged by a selection) and by
  `test_budget.py::test_the_monitor_is_centred_and_as_big_as_the_strip_leaves` (≥ 560 px,
  ≤ 30 %). Measured before: 386×217 in 386×272 (1440), 517×291 in 517×347 (1920), meters
  horizontal. After: **674×379 in 682×379 (1440), 841×473 in 922×473 (1920)**, 0 % letterbox,
  meters vertical. Two checks change shape, neither loosened in what it is for:
  (a) "centred" is now measured inside the picture's room (the screenbox), since the
  vertical meters stand beside the room and would otherwise read as a 30 px off-centre;
  (b) `test_the_selected_shot_is_in_view` asked for the whole Effect Controls strip to end
  above the fold; Effect Controls is now in the bottom row and a long strip scrolls inside
  its panel, as Premiere's does, so the check is that the strip starts in view and its
  panel shows ≥ 300 px of it (measured ~340). The words/controls budgets are LOWERED to the
  measured counts on the tree merged with POLISH, no headroom (less of Effect Controls is
  in view): words / 106 → 93, / shot 181 → 119, / film 114 → 101, / fx 134 → 124, / jobs
  108 → 102; controls / shot 85 → 61. Also new: `test_selects_opens_wide_enough_for_its_media_in_out_and_reason`
  (Selects' list ≥ 560 px at both sizes, the Timeline ≥ 460 px meanwhile) and
  `test_a_layout_saved_before_the_new_arrangement_keeps_its_widths_once` (the one-time
  migration of an unversioned saved layout).
- 2026-10-15 (lead, wave 5): `test_perf_latency.py` brings the box to about one runnable task per core (burners = cores − current load) instead of adding one burner per core on top of whatever already runs, and skips — visibly, with the load in the reason — when the box is already above 2× its cores before it starts. Why: at load ~25 on 6 cores (a judge's real first pass plus other loops) the 100 ms frame-gap limit failed on both 867f694 and the wave-5 branch with the worst gap inside one buffer, not at the cut; the gate on a quieter box passed it. The limits themselves (100 ms gap, 100 ms medians) are unchanged.
- 2026-10-15 (wave 6, lane PLAYBACK; verdict 5 ruling 3 asked for it): `test_perf_latency.py` is STRICTER. The fixture gains a 0.4 s shot between the two 59.94 shots and the play run crosses both cuts; the longest gap between frames the live buffer presents is now ≤ **67 ms** (was 100: two frame periods at 29.97, not three); new: six seeks (`tl.seek` → the live buffer presents that frame, inside a shot / into the next / onto the other clip / back) with median ≤ **250 ms** and max ≤ **400 ms**; new: on every animation frame of play the live `<video>` has a picture (readyState ≥ 2) and no `#screenMsg` / `#monWait` text or "buffering" is on the Program (a per-frame DOM check rather than one screenshot, which would perturb the timing it measures). The load-aware burners (cores − load) and the visible skip above 2× cores are unchanged, as are the 100 ms interaction limits. New beside it: `test_program_a3.py` (A3 parks on a seek, plays in step, stops after its end, follows gain / mute / solo) and `test_proxy_order.py` (the cut's clips' proxies first, in film order; a clip the human opened still first).
- 2026-10-15 (wave 6, lane KEYUI; verdict 5 punch 4, dimension 7): **the "/ shot" controls
  budget 61 → 65, measured, no headroom.** Effect Controls now has Premiere's stopwatch on
  every animatable property (Position, Scale, Rotation, Opacity, Volume Level); at 1440×900
  on the fixture four of them are in view (Position, Scale, Rotation, Opacity; Level is
  below the panel's fold), which is the +4. ◀ ◆ ▶ show only on an animated row
  (`visibility: hidden` otherwise) and the lanes' diamonds are not controls in the count, so
  an un-animated shot adds nothing else. Lumetri Color › Basic Correction (9 sliders, 4 new
  numbers, the "Basic Correction" twirl, "White Balance" / "Tone" headings) sits below the
  fold and adds 0 in view. Measured after (words · controls): / 93 · 50, /floor 52 · 7,
  /open 55 · 8, **/ shot 119 · 65**, / film 101 · 49, / jobs 102 · 51, / fx 124 · 53 — every
  other number unchanged. New checks: `test_keyframes_ui.py` (two opacity keys → the
  Program's composited picture at the midpoint is within ±10 % of the interpolated value,
  parked and while playing; a lane click → Program ≤ 150 ms; one undo per animate / add /
  move / remove; keys survive a reload; 13 bad `keys` bodies and 4 bad Lumetri values are
  400s) and in `test_effect_controls.py` highlights / shadows / whites / blacks move
  `grade.probe()` within 150 ms, a slider drag is live and one undo, and no Effect Controls
  property name is ellipsized at 1440.
- 2026-10-16 (lead, after verdict 6's ruling): Playback Resolution in grade.js (the graded picture drawn <= 640 px wide while playing, full size parked) logged here as the judge asked; wave 8 makes it a visible Program-monitor setting (Full / 1/2 / 1/4) like Premiere's.
- 2026-10-16 (wave 8, lane SMALL; verdict 6 punches 9 and 10a): the gate is STRICTER. `tools/gate.py` reads `test_perf_latency`'s record (`$ROUGHCUT_PERF_JSON`: outcome, load, burners, numbers), prints the perf test's line with the load it ran at, retries a skipped perf test once after the rest of the suite at a quieter moment, and a second skip makes the run AMBER (exit 2), never GREEN; `ROUGHCUT_PERF_FORCE_SKIP=1` exercises that path. Playback Resolution is now a Program-monitor menu (Auto = the old <= 640 px rule and the default, Full, 1/2, 1/4); `test_perf_latency` keeps asserting the default (<= 640 playing, full parked) and `test_playback_resolution.py` asserts Full = the monitor's picture width, 1/4 a quarter of it, and the choice kept across a reload. test_budget's control numbers +1 on the board screens (the menu), measured.
- 2026-10-15 (wave 10, lane PERFGATE; verdict 7 ruling 3 asked for these two, logged late): **two
  stricter gate changes from wave 8 that were never logged.** (a) `tools/gate.py`
  `JOURNEY_SLOWEST_S = 1.0` (lane SAVESAFE): the journey line now adds the slowest request
  the server answered during the journey, timed to its first byte (`GET /api/debug/slow` →
  `slowest_api`), and the journey is RED when it is over 1.0 s. Stricter: a new way for the
  journey to go red; nothing that was red before is green now. What it cannot see: only
  `/api/` paths count (not `/media`, posters or proxies); it is the journey's own 3-clip
  suite fixture with a scripted model, so a stall that needs real footage never happens in
  it (verdict 7's 27-35 s `GET /api/junk` on Killington, blocker 3, read 0.34 s here); it
  is time to first byte, not the whole transfer; it is one number (the slowest), not a
  distribution; and a rev whose server has no `/api/debug/slow` reads "not measured" and
  stays green. (b) `test_perf_latency.py`'s fixture (lane PLAY3, `f4a9016`): two 1920×1080
  59.94 testsrc2 sources became **three 3840×2160 H.264 59.94 sources with a 1 s GOP**
  (Killington's GoPro clips), grain so the 1280 proxy runs at a real proxy's ~2 Mbps (a
  clean one is 0.6), and the 0.4 s shot (wave 6) now sits between shots from DIFFERENT
  files (A, B 0.4 s, C), so both files after it must be open and parked before it plays.
  New asserts: every cut went to a parked buffer and the two cuts used two different
  buffers; the proxy test asks for a keyframe every ≤ 0.5 s (+20 ms) on the 29.97 proxy.
  Stricter: the same limits (67 ms gap, seek median 250 / max 400, 100 ms medians) on a
  heavier fixture with more asserts; the fixture's 4K encodes also make the test's own
  start-up load heavier (see the next entry).
- 2026-10-15 (wave 10, lane PERFGATE; verdict 7 punch 3): **the perf gate can turn green on a
  shared box; mixed, stricter in three places and more lenient in one, said plainly.** The
  box is shared with other loops at load ~10-20 on 6 cores, so `test_latency_under_load`
  skipped above 2× the cores and the gate could only be AMBER; the judge's three 0-burner
  runs at load 14.2 / 15.7 / 16.4 passed (worst gap 50 ms). What changed:
  (1) `test_perf_latency.py` no longer skips above 2× the cores: it runs there with **0
  burners** (cores − load is already 0 past the cores) and records `load_pre` (the
  1-minute load just before its burners), `shared` (`load_pre` > 2× cores) and
  `settled_s`. Because its own fixture (three 4K encodes + proxies) pushes the 1-minute
  average up (measured: 11.3 when launched, 21.4 after the fixture), a first reading above
  2× waits up to 75 s (`ROUGHCUT_PERF_SETTLE`) for that to drain (measured 22.8 → 16.6);
  a reading that falls to 2× the cores is not shared. The only skip left is
  `ROUGHCUT_PERF_FORCE_SKIP=1`.
  (2) `tools/gate.py` runs the perf test alone **before the suite and again after it**
  (deselected from the suite run itself, so the suite's count is one smaller); the run that
  counts is the **better-conditioned one that measured** (the lower `load_pre`; a tie goes to
  the before run), and the line says which counted and gives both runs' loads and numbers.
  The after run first waits up to 300 s for load ≤ 2× cores unless the before run already
  measured on a box that was not shared.
  (3) Status: a counted **pass is green wherever it ran**, and on a shared box the line says
  so ("passed at load 14.2 with 0 burners (shared box …)"); a counted **fail above 2× the
  cores is AMBER**, named ("it failed on a shared box … not a pass and not the app's red");
  a counted **fail at or below 2× is RED** (as before); **nothing measured is AMBER** (as
  before: `ROUGHCUT_PERF_FORCE_SKIP=1` still gives AMBER). `app/tests/test_gate_status.py`
  pins every one of these rules and the GREEN/AMBER/RED/exit-code function.
  Stricter: the test now measures where it used to skip, so a regression on a shared box is
  seen (as AMBER, with its numbers) instead of hidden behind a skip; the counted run is the
  better-conditioned one, not the better result, so a fail at the quieter moment is not
  rescued by a pass at a busier one (two chances at a quiet box, not two chances to pass);
  and the settle keeps the test's own encodes from making a box read as shared. More
  lenient: **a pass on a shared box now makes the gate GREEN**, where before nothing above
  2× could. The limits are unchanged, and a busier box is a harder test of them, but above
  2× the cores the box's load is other loops' (bursty, uncontrolled) rather than the
  test's steady one-task-per-core burners, so a GREEN there is a pass under a different,
  unrepeatable load, and the line gives that load so the judge can weigh it. A fail above 2×
  is AMBER, which is what it was before (then a skip); before wave 5 any fail was red. My
  own two standalone runs while building this both FAILED: at load 21.4 before the settle
  existed (83.3 ms inside a shot) and at 16.6 after a 75 s settle (116.7 ms at a cut, 20
  fps); the second, under these rules, reads AMBER, not GREEN.

- 2026-10-15 (wave 11, lane LUMETRI; logged by the lead): **the screens budget grows by the Lumetri
  Scopes tab: +2 words and +1 control on each board screen** (`/` 93·51 → 95·52, `/ shot` 119·66 →
  121·67, `/ film` 101·50 → 103·51, `/ fx` 124·54 → 126·55, `/ jobs` 102·52 → 104·53; `/floor` and
  `/open` unchanged). More lenient by exactly the measured count, no headroom: the tab is one
  Premiere control (Lumetri Scopes beside the Source monitor, as the Color workspace places it).
  Curves, Color Wheels and HSL Secondary sit below the fold in closed sections and add nothing to the
  first screen.

- 2026-10-15 (wave 11, lane FXBIN; logged by the lead): **the screens budget grows again by
  Premiere's Effects bin: +2 words and +1 control on each board screen** (the Project group's
  "Effects" tab, and the AI designer's tab renamed "Designed Effects"). With LUMETRI's entry above,
  the measured totals are `/` 97·53, `/ shot` 123·68, `/ film` 105·52, `/ fx` 128·56, `/ jobs`
  106·54; `/floor` and `/open` unchanged. More lenient by exactly the measured count, no headroom:
  each is one Premiere tab where Premiere has it.

- 2026-10-16 (lead, recording Karl's K3): **a new open K line, so the loop cannot PASS until K3 is resolved
  with evidence.** Stricter. K3 also adds a standing decision (the pro controls stay). No dimension's bar
  text changed yet; when the loop resumes, the lead turns K3 (a)-(e) into rubric items and logs them here.

- 2026-10-18 (lead, loop resumed on Karl's "Resume roughcut project loop"): **K3 becomes section E, four
  new dimensions 13-16; PASS now needs all 16 at ≥ 8.** (b) → 13 footage in from anywhere (Import, Ctrl+I,
  Media Browser, OS drop, mid-session, offered to the pass). (c) → 14 one always-visible AI prompt with
  a scope, plus an AI menu listing every AI action. (d) → 15 one action vocabulary shared by the pro
  tools and the AI, every family listed, each AI op reviewed before it lands, proven by an AI-op ==
  hand-op EDL test on a deterministic test backend. (e) and (a) → 16 a first-run teach-through, hovers,
  empty states, and the look: Premiere's Project panel views and the pass's result as editor objects
  (a sequence in the bin, subclips or labelled markers). Strictly stricter: nothing in 1-12 is
  relaxed, four bars are added, and 16 carries a hard cap (a guess at an AI step caps it at 6).
  Judgement call to rule on: K3 (a) "I still don't like the way the footage and pass look" names no
  specific fault, so the lead read it as "make them Premiere's own objects"; that is an interpretation,
  and K3 stays open until Karl looks, as K2 does. A deterministic test backend for the AI-op tests is
  not a leniency on the model: the judge still asks the real model for edits on Killington.

- 2026-10-18 (wave 12 scaffold, lead): **the screens budget grows by the menu bar's "AI" menu: +1 word and
  +1 control on each board screen** (`/` 98·54, `/ shot` 124·69, `/ film` 106·53, `/ fx` 129·57, `/ jobs`
  107·55; `/floor` and `/open` unchanged). More lenient by exactly the measured count, no headroom. Premiere
  has no AI menu; dimension 14 asks for one menu that lists every AI action, and this is its one title.
  Also dimension 13's index line now reads "the free part of the index starts on its own; a priced step is
  offered with its cost line, never run unasked", because INTAKE's standing decision is that the priced
  visual pass is never automatic; the earlier wording would have broken it.
