# finder — retest of my round-1 findings (new build, fresh Copper project)

`<finding id> | fixed / better / same / worse / could not retest | what I saw this time`

Round-1 ids are "R1 FINDER-nn"; my new findings this round are in out/findings.jsonl as FINDER-01…
(the kit numbers them from 01 again), written "r2 FINDER-nn" below.

**Totals (22 old findings): fixed 8 · better 12 · same 2 · worse 0 · could not retest 0.**

R1 FINDER-02 | fixed | Index › click a clip shows its description, "FOUND · n MOMENTS" (time, kind, words) and "SAID · n WORDS" (timed transcript) under the run stages, and says how the look was sampled ("read from 8 frames, one every 4 s (0:00–0:28): a sample, not the whole clip").
R1 FINDER-04 | fixed | 0 of 26 descriptions now contain "nothing notable", "not skiing", "unusable", "no jumps/falls", "ordinary" (22 of 26 last time); they say who and what is in frame.
R1 FINDER-08 | fixed | The transcript is in the Index (SAID); clicking a line cues the Source to it with In–Out over the line (GX010474 "What's the red mean?" → 00:00:12:22). Still cramped and every speaker is S1: r2 FINDER-11.
R1 FINDER-09 | fixed | A Find hit names its clip ("GX010494 · at 0:00 · 4.0 s") and one click opens it in the Source at the hit with In–Out set, plus "+ add to cut".
R1 FINDER-11 | fixed | Same as above: no hidden preview below the fold any more.
R1 FINDER-14 | fixed | "porcelain throne" → #1 GX010486 0:00 'heard' (the spoken line); black-frame picture guesses now rank below it.
R1 FINDER-18 | fixed | Source transport buttons keep their places after Mark In (Mark In 393, Mark Out 423, Insert 556 before and after); my 8 In/Out pairs all went in at the length I marked.
R1 FINDER-19 | fixed | After each Insert the timeline playhead lands at the end of the new shot (05:05, 13:04, 18:08 … 46:23), so 8 inserts came out in story order.
R1 FINDER-01 | better | Selects rows now carry the clip name, but in a 590 px panel it's cut to "G." / "GX0…" behind two chips; the row tooltip gives "GX010478 0:00.0–0:11.3".
R1 FINDER-03 | better | The Project list is wider when the Index is open, but Description is still the 9th column, off to the right; I read descriptions in the Index instead.
R1 FINDER-05 | better | GX010490 is no longer described as a chest camera; it's "filmed from a camera on the snow with a red pole". The jump (20.0–20.3 s) is still not named anywhere in the index: r2 FINDER-02.
R1 FINDER-06 | better | No "single frame" / "20-second" in the words; GX010491's footer still says "read from 1 frame" for a 5 s clip.
R1 FINDER-07 | better | GX010492's Found line now says "rides a chairlift just after loading"; its description still says "works at a chairlift terminal… yellow work gloves": r2 FINDER-03.
R1 FINDER-10 | better | Find answered in 15–37 s (most ~20 s, one 72 s) against 18–128 s last time; the box says "searching…" while it works.
R1 FINDER-12 | better | Dog, hot tub, wipe-out and pizza get "Nothing convincing like '…' in this footage. These are only the closest guesses." But "a snowball fight" got 9 confident matches and a mountain view that IS there got "nothing": r2 FINDER-06.
R1 FINDER-15 | better | Look · every 1 s on GX010492 (79 s, with a Stop button and a clear "what it changes" note) now finds "The man sits on the chairlift beside the camera… 0:13". It did not touch my hand description. Worth it this time.
R1 FINDER-17 | better | Audio Info says "48000 Hz · Stereo · + .WAV (not used)" on every clip. There's still no way to use the WAV (builders say not built).
R1 FINDER-20 | better | Re-running Look on one clip and saving a copy both left the proposal waiting and reachable (Assistant: "waiting for you … for the cut copper-02-2026"). But after switching back to main it read "accepted" though I never accepted: r2 FINDER-14.
R1 FINDER-21 | better | Keep as hero on the GX010478 pick now gives "hero · GX010478 0:00.0–0:03.0", which holds the circuit board up at the face (it was 0:04.4–0:11.0, after the board). Still "snapped to the sentences" by default.
R1 FINDER-22 | better | The index no longer talks the quiet clips down, and Find lands the waiting-on-the-slope, airport-waiting and laughing moments at #1–2. The chairlift ride is still buried (#3/#5 under the 495 wall) and the empty mountain view gets a false "nothing".
R1 FINDER-13 | same | "someone jumps off a kicker" → #1 GX010490 0:01–0:11.5 (picked by the SPOKEN "Nook's going all this jump here"), not the jump at 20.0–20.3 s. Asking the AI in the prompt box found it to the frame: r2 FINDER-02.
R1 FINDER-16 | same | Telemetry "0 peaks" on 24 of 26 clips, including GX010490's jump; only "stopped" on 495/496 and "impact 6.2 g" on a black clip, printed as tag soup; no speed anywhere: r2 FINDER-10.
