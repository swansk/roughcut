# Wave 23: the Copper editors' top areas, built closed loop

Karl, 2026-11-10 evening, after `judge/COPPER-EDITORS.md`: "I will be gone for the rest of the day so continue working
on this closedloop". So: build the ranked areas, gate, merge, put it on 8766, and send the same simulated editors back
in to see whether the pain is gone. No judge 17 (still held for his K2-K7 look); the editors are this loop's judge.
Main, 8765 and pushing main stay Karl's.

Base: `82e38b6`. Read first: `judge/COPPER-EDITORS.md` (the areas and what was verified), then your findings in
`judge/copper-editors/findings.jsonl` (`python3 research/editor-trial/merge.py --full` needs the scratch dir; the
jsonl has the same text: `wanted / did / happened / expected / suggestion`).

| Lane | Area | Findings | Owns |
|---|---|---|---|
| SAFE | 1. the AI's work cannot be lost | CLUMSY-01, -04, -05, -06, -09, -11, -13, -17; FINDER-20; WORDS-03, -14; HANDS-07; FINISH-10 | `app/static/app.js`: the proposal functions (`rejectProposal`, `answerProposal`, `proposalAnswered`, the proposal / "new cut is ready" banner, the header undo / redo buttons); `app/server.py`: `ask_pending`, the `/api/asks/*` and ask-job endpoints; `app/static/timeline.js`: the undo / redo stacks; `app/static/flow.js` (Next); new tests |
| TRUTH | 2. the monitor tells the truth | COLD-11, -12, -16; HANDS-01, -05; CLUMSY-18; WORDS-02; FINISH-05; the black monitor (all six) | `app/static/grade.js` (context loss); `app/static/meters.js`; `app/static/app.js`: the Program's frame and timecode (`showLive`, `cueAt`, the proposal's Play it path, what the Program shows after an edit); `app/static/timeline-lanes.js` (`playPlan`); new tests |
| DIRECT | 3. an AI you can direct | WORDS-01, -05, -06, -08, -10, -17, -18; COLD-17; CLUMSY-12 | `roughcut/actions.py`, `roughcut/assist.py`, `roughcut/revise.py`: the note the cut is written from; `app/server.py`: the prompt-box / Ask endpoints (not `ask_pending`, not the fx endpoints); `app/static/aiprompt.js`; new tests |
| TITLE | 4. titles a person can ship | HANDS-17; WORDS-15, -19, -20, -21; FINISH-07, -09, -17, -18 | `roughcut/fx.py`, `roughcut/fxcheck.py`, `app/static/fx.js`, `app/static/fx.css`; `app/server.py`: the `_fx_*` functions and `/api/fx/*` endpoints; new tests |
| FILM | 2 and 7. what goes in the file is right | COLD-08, -13, -19; FINISH-20, -22; WORDS-23; HANDS (0 dB note) | `research/tools/assemble.py`; `app/server.py`: `build_proxy`, `proxy_gpu_args`, the poster / preview / sprite builders, `scaffold_edl`, `configure`'s orient handling, `api_projects_open`'s `orient`; `roughcut/telemetry_events.py` (read; add a helper only); new tests |
| DESCRIBE | 6. describe the footage as it is, and let the editor read it | FINDER-01, -02, -04, -05, -06, -07, -08, -11, -12, -13, -14, -15, -22 | `research/tools/visual_pass.py` (the look prompt and its fields); `roughcut/find.py`; `app/static/indexpanel.js` + css; the Selects tab's Find results (their file); `app/server.py`: the find and transcript endpoints; new tests |

Two files are shared by region (`app.js`: SAFE the proposal, TRUTH the Program; `server.py`: four lanes, four regions).
Stay in your region; if you must touch another lane's, say so in your hand-back with the reason.

## What done looks like, per lane

**SAFE.** (a) Discard is undoable: a toast with Undo and Ctrl+Z bring the proposal back exactly as it was; Discard no
longer looks like Accept's twin. (b) A proposal stays waiting until the editor answers it: saving the cut, saving a
copy, re-running an index stage or a later AI job never drops it. When a second proposal arrives the first is kept;
the Assistant lists the last few proposals (waiting, accepted, discarded, replaced) and any of them can be reopened
and accepted. (c) Every AI job the editor can start has a Stop that stops it (the whole-film re-cut today has only
"Close"); what Stop did is said in a line. (d) After Accept nothing still says "a new cut is ready · Review" and Next
does not say "Ask for the first cut" over a cut. (e) Undo and redo survive a page reload and a switch to another bin
and back, for the cut they belong to; the undo and redo buttons do not vanish on an empty sequence. Tests for each.

**TRUTH.** (a) When the grade's WebGL context is lost (test it with `WEBGL_lose_context`, page and worker), the
Program shows the ungraded picture or the restored grade within a second, never black, the worker never reports a
draw as good on a lost context, and one line says what happened if the grade could not come back. (b) While a
proposal waits, the Program follows the playhead (ruler click, arrow keys, the proposal's shots one by one) and its
timecode runs during Play it. (c) After a ripple delete, an Accept, an undo or an AI edit, the Program's frame and
shot label are of the cut as it now is (no old frame under a new label; Program and timeline agree where the playhead
is). (d) The meters fall to rest when playback stops. Tests for each; (b) and (c) on the suite's fixture.

**DIRECT.** (a) The prompt box remembers: each ask carries the last turns (what the editor said, what the AI answered
or proposed, what was accepted, rejected, undone), so "what did you just change?", "put it back", "ok do both", "the
second one" work; the cap is by tokens, not five words. (b) The AI never presents its own text as the editor's: the
planner's expansion is labelled as the plan, is not cut mid-sentence (COLD-17: `note` was truncated at 2,000
characters and the cut-writer answered "your note is cut off"), and notes do not quote instructions the editor did not
give (the 120-180 s target is "the project's target", not something "you asked"). (c) Honest about its eyes: when an
edit rests on what the picture shows (a wobble, the biggest jump, the camera at the ground) and the AI has only the
index's text, the proposal says so in its own first line ("from the description; I have not looked at these frames")
instead of writing as if it saw it. Looking first is wave 24; honesty is this wave. (d) A scope the editor picked on
the chip stays until they change it or the thing it names is gone; playback alone never changes it; the chip says when
it is following the playhead. (e) The AI only names panels and commands that exist (CLUMSY-12 "open the History
panel"): give it the real list. A handful of live calls on the plan (`claude_cli`, at most 20) to see it work; tests
with the suite's fake backend for the wiring.

**TITLE.** (a) The effect check fails a title whose text (or any shape) leaves the frame or the title-safe area (5 %
by default), and the designer is asked again with the measured overflow, up to two times, before the editor sees it;
"✓ checked" means inside the frame. Reproduce the three failures first (the fx records are in the scratch work dirs,
e.g. `/mnt/roughcut/space/roughcut-loop/copper/editors/words/work/fx/copper-02-2026/`). (b) Text is never drawn with
a stray stripe or a chopped bar behind it (FINISH-17, HANDS-17): find the cause in the renderer, fix it, pin it with
an image test. (c) The card has hand controls: the words (editable), size, position (x, y, with title-safe guides in
the Program while dragging or nudging), and in / out; a change by hand needs no model call and re-checks at once.
(d) Asking for a change to a title replaces it, never stacks a second one; a title needs one Accept, not two in two
panels; when an edit moves the clip a title sits on, the proposal says where the title goes. (e) "✗ the sound is not
heard" does not appear on an effect that has no sound. Monitor and export must still match: the renderer is shared.

**FILM.** (a) Rotation is decided per clip, not per bin: where the telemetry pass has a gravity vector, a rotation
flag that disagrees with it is ignored (Copper: 474, 484, 492 are flagged ±90 and were shot upright; 497-499 are
flagged 180 and truly inverted, so their flag is right), where there is no telemetry the flag is honoured as today,
and the per-clip answer is stored with the project and shown (Project panel or Index: "rotation flag ignored: the
camera's sensor says upright"). Proxy, preview, poster, sprite, Source, Program and export all agree (bump the cache
versions that need it; say which). A project made through `/api/projects/open` gets the same rule. Killington's bin,
whose flags are right, must not change: prove it on the telemetry sidecars. (b) Every export goes through a true-peak
limiter (ceiling -1 dBTP) after the loudness levelling; a film that was peaking at 0.0 dBFS now peaks at or under
-1.0, mean level within 1 dB of before; the preview profile too. Measure on a real render of a Copper cut
(`/mnt/roughcut/space/roughcut-loop/copper/editors/finish/work`, copy it first). Tests for both on the fixture.

**DESCRIBE** (added an hour into the wave; its files are disjoint from the other five). (a) The look no longer
assumes "an action camera worn on the skier's helmet or chest" and no longer writes a verdict on action: each clip's
description says who is there, where, what happens and what is said or funny, in that order, and events (jump, fall,
crash) stay a separate field under the same strict rules as today. On Copper the descriptions of 488, 490, 491, 492,
493 and 494 name the milk jug, the planted camera and the jump at 19.6-20.2 s, and the chairlift
(`judge/copper-editors/finder-audit.md` is the truth table: score before and after against it). Event recall must not
fall: re-look a labelled Killington subset with the old and new prompt and compare against
`research/R12-understanding/eval/killington-moments.checked.json`; up to 150 live calls on the plan for both bins
together, logged to your scratch ledger, and no change to `baseline.json`. (b) The editor can read what the index
found: the transcript of a clip (with times, click to cue) and its found moments, in the Index panel and from the
Source monitor's clip; a description the editor corrects by hand sticks and is marked as theirs. (c) Find: each hit
names its clip and time, a click opens the clip in the Source monitor at that moment (not a preview below the fold),
a query with nothing above a score floor answers "nothing like that in this footage" (choose the floor from the
finder's 12 real and 3 absent queries, and say how it was chosen), a spoken phrase outranks a black frame, and a
running search shows that it is running.

## Lane rules (as wave 22, plus two)

- Your own worktree and branch (given in your brief). Commit there: conventional prefix, a CHANGELOG `[Unreleased]`
  entry per commit (the hook enforces it; never `--no-verify`), **no Co-Authored-By or any model attribution**.
  Never `git checkout / restore / stash / reset --hard` outside your worktree. Do not merge, do not push.
- Never write `~/footage`, `/mnt/roughcut/footage`, `~/work`, `/mnt/roughcut/work`. Never touch ports 8765 / 8766 or
  their services. Your ports are in your brief; set `ROUGHCUT_LEDGER=<your scratch>/ledger.jsonl` and
  `env -u ANTHROPIC_API_KEY` on any server you start; stop what you start.
- A real project to try things on: copy a trial work dir into your scratch
  (`cp -a /mnt/roughcut/space/roughcut-loop/copper/editors/<name>/work <scratch>/work`, then replace
  `editors/<name>/work` with your scratch path inside `<scratch>/work/projects.json`), and serve it with
  `--footage /mnt/roughcut/footage/copper-02-2026 --work <scratch>/work --port <yours>`.
- Tests: `nice -n 19 uv run --with pytest --with fastapi --with uvicorn --with httpx --with numpy --with playwright
  pytest <files> -q`. Run your new tests and the existing files that cover what you touched; the lead runs the gate.
  Other lanes are running: one Playwright run at a time per lane, and do not judge a timing test on a busy box.
- NEW: test in the browser the gate uses (Playwright's default). The GPU launch (`--use-angle=gl-egl`) leaks and loses
  its GPU process after about two minutes on any page; do not use it for anything longer than a minute.
- NEW: when you fix a finding, try the editor's own steps again (they are in the finding) before you call it done.
- Hand back (your last message): the branch and its commits; per finding id, fixed / partly / not, in one line each;
  the tests you added and their result (the counts, pasted); what you did not do and why; what the lead should look
  at by eye. Under 60 lines.

## After the lanes

The lead merges in the order SAFE, TRUTH, DIRECT, TITLE, FILM, DESCRIBE, runs the quick gate on the merged tip, restarts
`roughcut-redesign` (8766), and sends the editors back in on fresh Copper copies with the software browser:
`clumsy` (SAFE), `words` (DIRECT), `finish` (TITLE, FILM), `hands` (TRUTH), `finder` (DESCRIBE), each told what changed and asked for
what still hurts. Their second reports decide wave 24, which otherwise takes the next areas in order: starting a
film (5), music and background export (7), the hands' focus rule, copy / paste
and typed timecode (8), and the AI looking before it cuts (3).
