# Wave 24: the next areas from the Copper editors (started while wave 23 is still merging)

Same loop as `judge/WAVE23.md` (read its "Lane rules": they bind here too). Lanes start as the files they need come
free; the editors' second round decides what is added.

| Lane | Area | Findings | Owns | Base |
|---|---|---|---|---|
| EYES | 3. the AI looks before it cuts on what the picture shows | WORDS-08, -12, -13, -17; the `words` editor's "it's free on the plan, so look" | `roughcut/assist.py`, a new `roughcut/look.py` if needed, `app/server.py`: the assist endpoints (DIRECT's region), `app/static/aiprompt.js`; new tests | after DIRECT (`b363a25`) |
| START | 5. start a film like a person | COLD-01 to -07, -09, -10, -20; CLUMSY-02, -08, -16; FINISH-01, -23 | `app/static/switcher.js`, `app/static/firstpass.js` + css, `app/static/tour.js`, `app/static/import.js`; `app/static/app.js`: the File and Help menus and the empty-timeline card only; `app/server.py`: the `/api/projects*` endpoints (not `orient`, which is FILM's), a folder-listing endpoint, the target-length setter; new tests | `102b725` |

## What done looks like

**EYES.** Wave 23's DIRECT made the prompt-box AI say when it has not looked ("From the written descriptions and the
transcript; I have not looked at these frames") and offer Look deeper. Now it looks. (a) When an ask rests on what the
picture shows (a wobble at 0:40, the biggest jump, "the shot where he waves", "is the horizon level"), the AI names the
ranges it needs to see, the board shows what it is about to look at and how much ("looking at shots 9-11: 24 frames;
free on your plan") with a Stop, the frames go to the model, and the proposal that follows says what it saw and where
("looked at 0:38-0:43: the camera jolts at 0:40.6 as he lands"), not a guess from the description. If it looked and
did not find the thing, it says that and proposes nothing. (b) A whole-cut picture ask ("cut every bit where the
camera points at the ground or the sky", "lose every shot where nobody is in frame") is answered by scanning the
cut's own shots at a stated density (about a frame a second, in contact sheets, bounded: say the bound) and proposing
the ranges with the frame that shows each, each one tickable like any other edit. Refusing and asking the editor to
scrub and drop markers (WORDS-12) is no longer an answer. (c) It never looks when the words and the index already
answer the ask (a length, a swap, a spoken line): looks cost time. (d) The honesty line stays for anything it did not
look at. Wiring tests with the suite's fake backend (the look step, the bound, Stop, what is stored on the record);
up to 40 live calls on the plan to replay, from the `words` editor's table
(`/mnt/roughcut/space/roughcut-loop/copper/editors/words/out/journal.md`): "at about 0:40 there is a wobble, lose it",
"slow the biggest jump to half speed", "cut every bit where the camera is pointing at the ground or the sky", and one
ask that must NOT trigger a look. Paste what it looked at and what it answered.

**START.** A newcomer with a folder of footage gets to a first cut of the length they want without guessing.
(a) File › New film from a folder… (also the first thing on the empty-timeline card and in Help): a dialog with a
folder browser served by the board (folders under the footage roots and the home folder, each with its count of video
files; a typed or pasted path still works; a path that is a file says so). It makes the project and lands where the
next step is. What a newcomer reads says "film" or "project" and "footage folder"; "bin" may stay where an editor
expects it, never as the only word. (b) Before a first pass on a project with no cut, the Run first pass popover asks
two things: how long (30 s, 1 min, 2 min, 3 min, or typed) and, optionally, what the film is and who it is for. The
length sets the project's target and the first cut is written to it (COLD-09 and CLUMSY-02: 60-120 s asked, 2:56 and
2:41 delivered); the target is shown where "aims for 2-3 min" is today and can be changed there later. (c) The
popover and the Index panel say what will happen and at what detail in plain words first (K5 stands: what it does and
how closely it looks must be said), with the model names and pipeline terms ("Qwen3.5-4B, PE-Core", "levelled 8 fps
strips") behind a Details disclosure; "unchecked" and "unsupported" on a proposed shot explain themselves on hover and
in a one-line legend. (d) A time estimate beside the price before it runs, learned from this machine's past runs
(26 short clips took 5 minutes here), and one cost wording everywhere: "free on your plan", the API-equivalent figure
only under Details. (e) The tour's first step says how to start your own film and does not pop up over an accepted
cut; Help has "Start a new film" and "What the first pass does". (f) The Export tab's Download button is reachable at
1536x864 and 1280x720 (COLD-20). Tests on the fixture with the fake backend; a real cold start on the Copper folder
with a fresh work dir in your scratch to see it end to end (one first pass: about 60 calls on the plan).

