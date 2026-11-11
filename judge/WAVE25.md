# Wave 25: what the first two waves left unbuilt, and what the editors' second round finds

Same loop (`judge/WAVE23.md`, "Lane rules" bind). Lanes are added as the second round reports.

| Lane | From | Owns |
|---|---|---|
| VERTICAL | WORDS-22 (sev 4: "make a 20 second vertical version for Instagram" offered to overwrite the main film with a shorter horizontal one), FINISH-14 (no 9:16 anywhere) | the sequence's frame shape end to end: where the cut stores it, Sequence Settings, the Program monitor's frame, the shot's reframe in Effect Controls, `research/tools/assemble.py`'s frame size and crop, the export dialog's sizes, `roughcut/fx.py`'s frame for titles, the prompt-box AI's way to make a second deliverable; new tests |

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
