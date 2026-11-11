# Wave 24: the next areas from the Copper editors (started while wave 23 is still merging)

Same loop as `judge/WAVE23.md` (read its "Lane rules": they bind here too). Lanes start as the files they need come
free; the editors' second round decides what is added.

| Lane | Area | Findings | Owns | Base |
|---|---|---|---|---|
| EYES | 3. the AI looks before it cuts on what the picture shows | WORDS-08, -12, -13, -17; the `words` editor's "it's free on the plan, so look" | `roughcut/assist.py`, a new `roughcut/look.py` if needed, `app/server.py`: the assist endpoints (DIRECT's region), `app/static/aiprompt.js`; new tests | after DIRECT (`b363a25`) |

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
