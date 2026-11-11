# Wave 25: what the first two waves left unbuilt, and what the editors' second round finds

Same loop (`judge/WAVE23.md`, "Lane rules" bind). Lanes are added as the second round reports.

| Lane | From | Owns |
|---|---|---|
| VERTICAL | WORDS-22 (sev 4: "make a 20 second vertical version for Instagram" offered to overwrite the main film with a shorter horizontal one), FINISH-14 (no 9:16 anywhere) | the sequence's frame shape end to end: where the cut stores it, Sequence Settings, the Program monitor's frame, the shot's reframe in Effect Controls, `research/tools/assemble.py`'s frame size and crop, the export dialog's sizes, `roughcut/fx.py`'s frame for titles, the prompt-box AI's way to make a second deliverable; new tests |
| DIRECT2 | round 2 `words` (fixed 15, better 5, same 2 of 23; `judge/copper-editors/round2/words-*.{md,jsonl}`): WORDS-15, -19 (SAFE's part), -11, -06, -17, -08, -04, -02, -16, -07, -05, -03, -13, -14, -09 of round 2 | `roughcut/assist.py`, `roughcut/look.py`, `roughcut/actions.py` (ops only: VERTICAL adds its own op there), `app/server.py`: the assist endpoints, `app/static/aiprompt.js`; new tests |

## What done looks like

**VERTICAL.** (a) A cut has a frame shape: 16:9 (today's, the default, and every existing cut reads as it), 9:16 and
1:1, chosen in Sequence Settings and shown in the Program monitor as that shape (letterboxed inside the panel, never
stretched). (b) A shot in a 9:16 or 1:1 cut is filled, not pillarboxed: centre crop by default, and the shot's Effect
Controls give the reframe (where the crop sits, left to right and up and down, and a zoom), with the Program showing
it live and one undo per change; nothing about a 16:9 cut changes. (c) The export writes that shape: 1080x1920 for
9:16 and 1080x1080 for 1:1 on the quick and the final presets (say the sizes in the dialog), picture, titles and
effects in the same place the Program showed them (the monitor-versus-file rule: compare frames at five timecodes,
including a title and a reframed shot), frame rate and the -1 dBTP limiter unchanged. (d) Titles and designed
effects are laid out for the cut's own frame: the frame check, the title-safe guides, the hand controls and the
proof all use the sequence's shape, so a title that fits 16:9 is checked again when the cut becomes 9:16.
(e) A second deliverable never costs the first: "make a 20 second vertical version for Instagram" in the prompt box
proposes a NEW cut (a saved copy, named, in 9:16, shortened as asked), says so in its first line, and leaves the cut
the editor was on untouched; Sequence Settings' shape change on the current cut asks nothing but is one undo.
(f) The sideways-shot clips (a phone held upright, or Copper's 474 / 484 / 492 if the editor says they really are
vertical) fill a 9:16 frame properly: check with one. Tests on the fixture for each; a real Copper export in 9:16
(preview profile, a short cut) with its five frame comparisons saved in your scratch. No model calls except up to ten
on the plan to replay the Instagram ask.

**DIRECT2.** The director's second round says the prompt box is now "genuinely good" for words-first directing (eyes: 18
of 19 claims true; ten turns on one idea without losing the thread) and names what still breaks the conversation.
In this order: (a) **It owns its history.** A record of every change made on this cut in this session (each accepted
edit, what it was, when, and whether it was undone), kept apart from the bounded conversation, so "what did you
change?" counts all of them (WORDS-15: "only two of your requests led to changes" when twelve had), and when the
conversation it can see is shorter than the session it says so first ("my record of what we said goes back to
11:34; the list of changes is complete"). (b) **"Put it back" is something it can do**: an operation that takes the
cut back N of its own accepted changes (through the cut's undo history that lane SAFE stores), proposed and reviewed
like any edit, one undo to cancel (WORDS-07: "press Undo ×3" is not an answer from a director's assistant).
(c) **It looks when the words name something to be seen.** "The lift scene", "the second one" (of two options it
described by their pictures), "where he waves": if the ask points at a thing in the picture and the index's text
does not settle which shot or moment it is, it looks before it places an edit; the two misses in the journal
(the dissolve on the wrong shot; the opening put on two seconds of a sleeve) must not recur. Never both "I have not
looked at these frames" and "from the frames I looked at" in one answer (WORDS-08, twice). Every ask that looks shows
the looking line (two of them showed only "Working 0:37"). What a look confirmed updates what the board says about
that moment: a shot it has just looked at and confirmed is not still badged "unsupported" or "unchecked" (WORDS-04).
(d) **A length the editor says is the length**: "make it 75 seconds" sets the project's target, so "no, shorter"
three turns later starts from 75, not from "the project's target" of the first pass (WORDS-05), and the header's
length and the notes' length are the same number (WORDS-03: 1:18.4 against "74.7 s"). (e) **It can cut on a word**:
the transcript's word timings for the shots in scope reach the prompt (bounded), so "cut out on 'it's over'" lands on
the word (WORDS-16; the trim tool already snaps to words, so the timings exist). (f) **The scope chip's default**:
with nothing chosen by the editor the scope is the selection if they selected shots by hand, else the sequence;
playback and the playhead never change it (WORDS-02: pressing Play on a fresh board flipped it to "Shot 1").
(g) Small: the answer popover never covers Accept or Reject all (WORDS-13, unchanged from round 1); Recent proposals
shows the editor's words, not "⟦the plan⟧" markup, and lists prompt-box proposals too (WORDS-14); "8 shots cut by
hand" is not said of a cut nobody touched (WORDS-09). Music by words (WORDS-11) and a second deliverable as a new cut
(WORDS-18) are other lanes (MUSICOP, VERTICAL): do not build them. Tests with the fake backend; up to 30 live calls
on the plan to replay the director's failing turns from `judge/copper-editors/round2/words-journal.md`.

