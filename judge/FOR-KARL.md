# Karl: try the redesign (10 minutes)

Only you can close K2 ("Premiere Pro quality"), K3 ("AI-first, easy to learn, footage in obviously") and K4
(the Index panel; the old pass pages folded in). The judge says K3 and K4 are delivered as far as it can test.
This page is the shortest way to look.

**Judged rev:** `799d8b8` (verdict 14: 15 of 16 dimensions at 8-9, the judge's gate green; playback at 7
because half-speed shots judder a little and 60 fps clips step on every other frame). Wave 18 is fixing what
is left (below) on the same branch.

## Open it

Double-click **Roughcut redesign.cmd** in `Karl OS/tools/Launch` (it opens `localhost:18766`, the
`roughcut-redesign` service on foxtrot, with its own copy of the Killington project, so your live board is
never touched). **The preview needs a restart on the judged rev first** (today it is an older server under
newer pages): the lead restarts it from a frozen copy of `799d8b8` once you say so.

## Try this, in order

1. **Your K4 (a): the Index panel.** Window › Index (or the Project group's Index tab). Each clip shows what
   ran: heard, looked at (and how often), close looks, telemetry, preview, with when and the cost ("free on
   your plan"). Pick a clip, set Look to every 2 s, and Run: the price shows before anything runs.
2. **Your K4 (b): the old pages are gone.** File has no "Footage and index…" or "Review the pass…" any
   more; `/open` and `/floor` open the editor. Their parts live in it: Selects ✓ / ✗ and right-click (the
   pass page's verdicts), File › Project Settings…, Window › Events.
3. **First run.** A six-step walk-through starts on its own. Follow it or Skip; Help › Learn Roughcut…
   brings it back.
4. **Bring footage in.** File › Import… (Ctrl+I), or drag files onto the Project panel. New clips get
   posters at once; the free index runs on its own, the paid look waits for you.
5. **Run the AI.** "Run first pass" in the header (or AI › Run first pass). Review its cut on the timeline:
   ✓ / ✗ each ghost shot, then Accept. "Re-run first pass…" keeps your hand edits.
6. **Ask for edits in words.** Ctrl+J, e.g. *"warm up shot 3 and put a half-second dissolve before shot
   4"*. Each edit previews; ✓ / ✗ each, Accept; one Ctrl+Z takes it back.
7. **Playback.** Plain cuts now land on the right frame (the outgoing shot's last, then the incoming's
   first), and a frame you stop on is the frame the export has (29.97 / 23.976 clips).
8. **Export.** File › Export… (Ctrl+M). Try 720p.

## What the judge still sees (wave 18 is on these)

- Half-speed shots judder a little on every play.
- On the 60 fps GoPro clips, stepping one frame at a time shows a new picture only every other press.
- Nothing points you to the Index panel yet (no tour step, no AI-menu item), and it opens narrow.
- Rejecting a pick in Selects is not on Ctrl+Z (only its right-click Undo).
- Undoing an AI effect in its first second, then redoing, brings back nothing.

## Tell me

In your own words: does it meet "Premiere Pro quality" (K2)? Is the AI the way you want to run it, and was
it easy to learn (K3)? Does the Index panel answer K4? What did you dislike first? Your words go into
REDESIGN-GOAL.md section K verbatim, and the next wave starts from them.
