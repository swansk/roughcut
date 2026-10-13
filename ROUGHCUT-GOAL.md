# THE GOAL: Roughcut's definition of done

Karl, 2026-10-13: *"Next time I open Roughcut I want to actually use the video editor and feel
good about it."* That is the bar. It is the editor's equivalent of the "ready for Steam" bar on
NOITA and STICKFIGHT, kept deliberately small: eight things a person cutting a video notices,
each with a pass line a fresh judge can check from `tools/gate.py`'s output (screenshots +
logs), the code, and the suite, without Karl in the room.

**PASS = the gate is green AND a fresh judge scores every dimension ≥ 8/10 AND no blocker.**
A judge that cannot find evidence for a dimension scores it at most 5. Section K (Karl's notes)
outranks everything: one unresolved line there is a FAIL whatever the scores.

Run the gate: `python3 tools/gate.py [ref]` (full, ~15 min) or `--quick` (screens + hygiene,
~1 min). It freezes the commit into `~/roughcut-loop/snap-<sha>`, writes
`~/roughcut-loop/gate-<sha>-<stamp>/summary.md` and `screens/*.png`.

## The eight dimensions

1. **Opens fast, lands somewhere.** Every screen (`/`, `/floor`, `/open`) shows a picture and
   the one Next step within 3 s on the fixture (gate: `screens`, the `ready` time per screen);
   zero uncaught page errors on any screen.
2. **Footage in, no ceremony.** A bin opens on its footage with a picture per clip. On the board,
   any clip reaches the timeline in ≤ 2 clicks **without the pass and without a model call**;
   the pass and the index make it better, never possible. (Judge: `board.png` shows footage
   in the Bin on a bin with no keeps; a test proves the click.)
3. **Cut, trim, ripple, move.** Select by click, trim by edge with the magnet, ripple delete,
   split, drag-move, J/K/L. (Suite: `test_timeline_*` green; judge: `shot.png`.)
4. **The preview is the cut.** The monitor at rest shows the frame at the playhead; play runs
   across cuts in order; a selected shot parks the monitor there. (Suite: `test_ui_flow`,
   `test_timeline_ui`.)
5. **Nothing is lost.** Every edit is one ⌘Z, redo works, an autosave lands within seconds, and
   switching cut or bin flushes it. (Suite: undo / autosave / projects tests.)
6. **A film comes out.** *Make the film* produces a playable file of this cut, with its grade and
   effects, from one button; the board says when it is ready. (Suite: `test_*_render`;
   judge: `film.png`.)
7. **Quiet and plain.** One header row ≤ 50 px, exactly one blue button, no page scroll, words
   and controls in view under `test_budget.py`'s numbers; every button says what it does in
   plain words; on the subscription backend no price reads as spending money, and every
   model call still shows its cost before it runs. (Gate: `screens`.)
8. **No dead ends.** Every empty state names the next action *and* offers it (a button or a
   link that works); no screen is reachable only by URL; an error says what to do.
   (Judge: every PNG; grep the empty-state strings.)

Hygiene, always: the suite green (a test that fails only under full-run load and passes alone is
named in the summary, not hidden), no model ID literal outside `roughcut/config.py`, all
inference through `roughcut.inference` (gate: `hygiene`).

## K. Karl's notes (verbatim, newest first; only Karl writes here)

_None yet. Until Karl writes here, INTAKE's "Decisions (do not re-litigate)" and M16's ten
decisions are the standing rules the judge enforces._
