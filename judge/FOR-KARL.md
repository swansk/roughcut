# Karl: try the redesign (10 minutes)

The loop has done what K3 asked, and the judge agrees it is "substantially delivered". Only you can
close K2 ("Premiere Pro quality") and K3 ("AI-first, easy to learn, footage in obviously"). This page
is the shortest way to look.

**Judged rev:** `18944d0` (verdict 11: 15 of 16 dimensions at 8-9, the judge's gate green; only
dimension 10 at 7, and your K2 / K3). Wave 15 is fixing what is left (below) on the same branch.

## Open it

Double-click **Roughcut redesign.cmd** in `Karl OS/tools/Launch` (it opens `localhost:18766`, the
`roughcut-redesign` service on foxtrot, with its own copy of the Killington project, so your live board
is never touched). The preview needs a restart on the judged rev first: the lead will do it once you say so.

## Try this, in order

1. **First run.** A six-step walk-through starts on its own. Follow it or Skip; Help › Learn Roughcut…
   brings it back, and AI › Learn the AI features walks the AI parts.
2. **Bring footage in.** File › Import… (Ctrl+I) opens a browser of foxtrot's folders (try `/mnt/roughcut`).
   Or drag files from your laptop onto the Project panel: they upload. New clips get posters at once.
3. **The bin.** List view has Premiere's columns (scroll sideways); Icon view scrubs as you move across a
   tile. After a pass, the proposed cut is a "First pass" sequence in the bin and the AI's picks are subclips
   under each clip, with why they were picked.
4. **Run the AI.** "Run first pass" in the header (or AI › Run first pass). It is free on your plan. Review
   its cut on the timeline: ✓ / ✗ each ghost shot, then Accept. "Re-run first pass…" keeps your hand edits.
5. **Ask for edits in words.** Press **Ctrl+J** and type, e.g. *"warm up shot 3 and put a half-second
   dissolve before shot 4"*. The answer comes back as the pro tools' own edits in the Assistant: each one
   previews (ghosts on the timeline, Before / After on the Program), ✓ / ✗ each, Accept, and one Ctrl+Z
   takes it back. Right-click a block → **Ask AI about this…** scopes the ask to that shot.
6. **The AI menu** lists every AI action in one place.
7. **Export.** File › Export… (Ctrl+M): presets plus Frame Size / Frame Rate / Bitrate. Try 720p.
8. **New since the last look:** Effects › Video Effects (Crop, Transform, Gaussian Blur: drag onto a clip);
   Sequence › Sequence Settings… (the timebase stays fixed as you add clips); AI Look and Color Match
   preview on the Program before you accept; a just-imported clip plays within seconds.

## What the judge still sees (wave 15 is on these)

- Accepting a Designed Effect can't be undone yet, and one placed at a shot's very start fails its own check.
- A few thumbnail requests take up to 2 s on 4K originals; playback at 1920 can still hitch on a busy box.
- After the AI re-colours every shot, a re-run treats all of them as "edited by hand" and won't re-cut.
- No Lumetri Vignette yet (the AI fakes one with rings).

## Tell me

In your own words: does it meet "Premiere Pro quality" (K2)? Is the AI the way you want to run it, and
was it easy to learn (K3)? What did you dislike first? Your words go into REDESIGN-GOAL.md section K
verbatim, and the next wave starts from them.
