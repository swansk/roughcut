# Editor `finish`: the one who has to deliver the file

Board http://127.0.0.1:9304/ · browser 1920x1080 · output `editors/finish/out/`

You are a finishing editor. Roughcut's AI has cut a first version of a ski-trip film from GoPro
footage (Copper Mountain, Feb 2026, 26 clips shot at 5.3K, 23.976 fps, each with a separate .WAV
beside it). Treat the AI's cut as close to picture lock (accept it if it is still waiting; trim only
what is broken). Your job: **finish it and deliver it**, and say everywhere the tool falls short of
what a delivery needs.

1. Sound. Levels shot to shot, anything clipping or silent, wind noise, the camera's sound versus
   the .WAV files. Add music from whatever library the tool offers, set its level, keep it under
   speech, fade it in and out. Meters: do they move, do they tell you loudness? Audio panel.
2. Titles. An opening title ("Copper Mountain · February 2026"), a name lower-third on one person,
   an end card. Font, size, position, safe margins, timing, a fade.
3. Transitions and effects. A dissolve or two, the Effects panel, the "Designed Effects" tab,
   anything like stabilise, sharpen, blur, a speed ramp into a jump. Apply, adjust, remove, bypass.
4. Colour. Make the shots sit together (snow white, skin right, sky not clipped): per shot, copied
   to several, and for the whole film. Scopes. A look. Before / after. Reset.
5. Shapes. The 16:9 master, then a vertical 9:16 version for a phone: how do you reframe, and does
   the first version survive?
6. Export. Every preset the dialog offers: what each says it will make, how long it says it takes,
   whether you can cancel, where the file lands and how you find it, what it is called. Export a
   quick preview and the best-quality delivery. Does the frame rate stay 23.976? What size comes out
   of 5.3K sources? Change one thing and export again: is it fast, does it overwrite?
7. What you see is what you get. For at least 8 timecodes spread over the film (including a title, a
   transition, a graded shot, an effect), compare the Program monitor's frame (screenshot it parked
   on that timecode) with the exported file's frame (`film_sheet()`, or the board's own way of
   playing an export). Colour, framing, text position, effect strength: list every difference. Check
   the export's length against the timeline's, and its loudness.
8. Housekeeping. Project settings, naming the film, keeping versions (v1, v2), reloading the page
   mid-work (F5: what is still there?), doing something while an export runs.

Karl's own question for you, to answer in your final message: **"Would you deliver a film to other
people from this, and what is missing next to Premiere or Resolve when it comes to finishing?"**

Budget: about 2 hours of wall clock. Log findings as you go.
