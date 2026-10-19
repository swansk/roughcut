"""Turn a plain-language note into a segment list — a revised one, or the first one.

This is the "interject" half of the human-in-the-loop map. The board already lets a
human steer by hand; this lets them steer by *asking* — "tighten the intro", "more
skiing, less airport", "the milk is the running joke, build around it".

`originate` is the same call with nothing to revise. It exists because the app's
hardest prerequisite was a **hand-authored EDL**: the board could refine an edit but
never start one, so reaching it at all meant writing JSON in a terminal. Originating
is not a different capability, it is the same one addressed to an empty timeline.

Two design rules, both learned the hard way earlier in this project:

  * **The model gets the transcript, not just the metadata.** R8 and R9 established
    that what people *say* is the strongest signal in this footage and that the audio
    features around it are weak. A revision prompt without the words is asking the
    model to edit blind.

  * **The proposal is validated hard before it reaches the UI.** A model that invents
    a clip name or a timestamp past the end of a clip must fail loudly and get one
    bounded re-ask, not produce an EDL that explodes at render time. `validate_plan`
    is deliberately strict for that reason.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Callable

from . import colour as colourmod
from . import config, edits
from .boundaries import polish, polish_plan, summarise  # noqa: F401 — polish_plan re-exported
from .estimate import Estimate, estimate
from .firstpass import clip_name
from .inference import complete
from .progress import milestone

PLAN_SCHEMA = {
    "segments": [{"clip": "GX010495.MP4", "in": 1.6, "out": 13.7,
                  "why": "one short sentence on why this moment, in this place"}],
    "notes": "2-3 sentences: what you changed and why, addressed to the editor",
    # Verdict 5, punch 3: the model's "check X first … also check Y" lived in `notes`,
    # where the board could only find them with a regex on one literal phrase. They are
    # data now, each tied to the range it is about.
    "checks": [{"text": "one short sentence: what the editor should look at before "
                        "accepting, and why",
                "clip": "GX010495.MP4", "in": 1.6, "out": 13.7}],
}

# What the prompts say about `checks` (every Ask's "What to return").
CHECKS_GUIDANCE = (
    "Anything the editor should look at before accepting — a moment you could not see, "
    "a cut point you are unsure of, a claim in your notes they should verify — goes in "
    "`checks`, one item each with the clip and the range it is about (seconds within "
    "the clip; leave the range out when it is about the whole film), not in `notes`. "
    "An empty list is fine when there is nothing to check.")
CHECKS_MAX = 6
# A check is shown on one line with the whole of it on hover, so the cap is a guard
# against a runaway answer, not a layout limit; it is applied at a sentence end, else a
# word's (verdict 6, punch 4: "…someone the viewer can ide" was cut at 200 characters).
CHECK_TEXT_MAX = 300

# What a re-run says about footage imported since the last pass (verdict 9, punch 2: "it
# used none of the 4 new clips, and its notes say nothing about them"). Asked for only
# when the prompt lists new clips (`new_clips_section`); one item per clip, `used` and a
# sentence the editor reads beside it — the board words the verb from the plan itself.
NEW_CLIPS_SHAPE = [{"clip": "GX010495.MP4", "used": True,
                    "note": "one short sentence: the shot you used it in (by its number "
                            "in your edit) and what for, or why you left it out"}]
NEW_NOTE_MAX = 200

# The same plan when the prompt carries the colour section (INTAKE I10.5). The key is
# described, not exemplified: an example patch in the shape would be copied into answers
# whose note never mentioned the picture.
PLAN_SCHEMA_COLOUR = {
    **PLAN_SCHEMA,
    "colour": "OPTIONAL — leave this key out unless the note asks for a change to the "
              "picture; when present, a patch on the colour block (see '## Colour')",
}

SYSTEM = (
    "You are an assistant film editor assembling a rough cut from a single bin of "
    "raw footage. You are given every clip's transcript and the current edit. You "
    "revise the edit in response to the editor's note. You never invent clips or "
    "timestamps: every segment must name a clip from the inventory and lie inside "
    "that clip's duration. You favour moments with people, reactions and speech over "
    "empty scenery, and you cut on complete thoughts rather than mid-sentence."
)

FIRST_SYSTEM = (
    "You are an assistant film editor building the FIRST rough cut from a single bin "
    "of raw footage. Nothing has been cut yet. You are given every clip's transcript "
    "and audio summary, and nothing else — you cannot see the pictures, so the words "
    "and the shape of the material are your only evidence, and a human will watch "
    "what you assemble before it goes anywhere. You never invent clips or timestamps: "
    "every segment must name a clip from the inventory and lie inside that clip's "
    "duration. You favour moments with people, reactions and speech over empty "
    "scenery, and you cut on complete thoughts rather than mid-sentence."
)

# The two traps a first cut walks into on this material, both measured rather than
# guessed (docs/HANDOFF.md, "findings that must not be re-litigated"):
#
#   * transcript density points *away* from the action. On B1 the travel footage
#     carries 16.8 speech candidates/min against 7.4 on the mountain, and 4x the word
#     rate, because the camera mic is on the skier's helmet and everyone else is 50m
#     away. A model ranking clips by how much is said in them builds an airport film.
#   * the connective tissue is a running joke, not a topic. B1's gallon of milk shows
#     up in six transcripts and pays off 60s into one clip; a cut that treats the bin
#     as a montage of the sport throws away the thing the group will remember.
#
# Neither is inferable from a single clip, which is why they are stated rather than
# left to be rediscovered per call.
FIRST_GUIDANCE = """\
Things that are true of this kind of footage, and easy to get wrong from transcripts:

* **Quiet does not mean boring.** The camera is usually on the person doing the
  thing, so the most active footage is often the least spoken over, while standing
  around talking transcribes densely. Do not rank shots by how much is said in them.
* **Look for a through-line across clips** — a running joke, a phrase that recurs, a
  dare and its payoff, a person who keeps appearing. Something that shows up in
  several transcripts is usually the spine of the film, and it is worth more than any
  single good line.
* **Give it a shape**: something to establish where we are, a middle that builds, and
  an ending that pays off rather than just stops. Order is yours to choose; the clips
  are not obliged to appear in the order they were shot.
* **Order is a choice, not a record.** Each clip says when it was recorded relative to
  the others. Use that as information, not as a rule: opening on a moment from late in
  the trip, holding a reaction back until it pays something off, or grouping by theme
  rather than by day are all good edits, and a strictly chronological cut is usually
  the dullest one available. What reads as a *mistake* is only the accidental kind —
  drifting backwards through one continuous stretch, so the same people, place and
  light return in the wrong order for no reason. Move things deliberately; just say in
  the `why` when you have, so the editor can tell the difference.
* **Spread the load.** A first cut drawn from two clips is a clip reel, not a film.
* **You cannot see the frame.** A shot may be dark, upside down, pointed at a glove,
  or ruined in a way the words do not reveal. So say what each moment is *for* in its
  `why` — that sentence is what the human uses to check your reasoning against the
  picture, and it is what any later pass inherits as memory of the choice.
"""

# Appended only when some clip actually carries visual moments — telling a model to
# trust lines that are not there is noise, and noise in a prompt this long is not free.
VISUAL_GUIDANCE = """
* **Where a clip lists "what is visible", trust it over the transcript for events.**
  Those lines come from looking at sampled frames, and they are the only account you
  have of things that happen without being spoken: a fall, a crash, a landing. People
  narrate those minutes later if at all, so the words sit nowhere near the moment. A
  line marked `!` is one the reader thought notable; a stretch listed as unusable is
  one to cut around, not through.
* **But "inverted", "upside-down" and "flip" usually describe the camera, not the
  person.** Measured, not cautionary (R10): every claim of that shape checked against
  the frames on this bin turned out to be a helmet or chest mount on a POV run, where
  the horizon sits at forty-five degrees and the trees hang from the top of frame.
  Treat a trick as real when the ranked events list says a closer look **confirmed**
  it, and as a guess otherwise — and never write a `why` promising that the picture
  delivers a trick unless it is confirmed."""


# The ranked list, ahead of the inventory. Karl, on the revision this replaces: *"the
# lack of a workflow / algorithm that applies sort / priority following a granular
# keyframe analysis on the first pass."* Forty undifferentiated "what is visible" lines
# per clip give a reader no way to tell a backflip from a wide shot of a valley; this
# says which ones to look at first, and — as important — how much each one is worth
# believing.
EVENTS_HEADER = """## Events, ranked

The biggest things anyone has seen in this footage, in priority order. The score
combines what kind of event it is, whether the reader marked it notable, whether a
motion or audio peak corroborates it, and whether a second, closer look agreed.

Read the evidence word, not just the kind:

* `confirmed` — two independent looks at those seconds agreed. This is the only strong
  evidence available; prefer these.
* `unaudited` — nobody has looked closely yet. The kind is one reader's guess from
  frames four seconds apart, and on this footage that guess is often wrong.
* `deep` — a deep look (individual frames at the seconds the editor chose, read in
  order, every claim tied to a frame) found it and no earlier pass claimed it: one
  careful look, stronger than unaudited, weaker than confirmed. The clip's inventory
  below carries the deep look's beats for those seconds, `seen` or `inferred`.
* `fine-only` — only the 1s close look saw it; the first pass saw nothing there. One
  look at the busiest seconds of a clip, which is where camera artefacts live: weaker
  than unaudited.
* `unsupported` / `contradicted` — a closer look at 1s found nothing, or found a
  camera artefact where the first pass claimed an event. Do not build a shot on these.

Timestamps are seconds within the named clip."""


def events_section(events: list[dict], top: int = 14) -> str:
    """The ranked events as prompt text, or "" when nobody has looked at the bin."""
    if not events:
        return ""
    lines = []
    for e in events[:top]:
        why = e.get("why_ranked") or {}
        status = why.get("confirmation", "unseen")
        status = "unaudited" if status == "unseen" else status
        corroborated = ""
        if why.get("corroboration_z", 0) >= 2.0:
            corroborated = f", peak at {why.get('peak_at')}s"
        lines.append(
            f"{e.get('rank', 0):3d}. {e['clip']} {e['start']:.1f}-{e['end']:.1f}  "
            f"[{e.get('kind', '')}] score {e.get('score', 0):.2f}, {status}"
            f"{corroborated} — {e.get('what', '')}")
    return f"{EVENTS_HEADER}\n\n" + "\n".join(lines)


# The bin, ahead of the events and the inventory. The design (docs/design/
# cutting-room-floor.html §5): the first cut is asked *from the bin* — "build the film
# from these 14 moments, in this spirit" — which is a different and better prompt than
# "here is 44 minutes, find the film." Heroes are fixed, keeps are bounds the model may
# trim inside, and the rest of the inventory is there for connective tissue only.
SELECTS_HEADER = "## The editor's selects"

SELECTS_FIRST_GUIDANCE = """\
The editor has watched the footage and kept these moments. They are the film's raw
material, and this section outranks everything below it.

* A line marked **HERO** must appear in the cut. You may trim inside a hero's range;
  you may never drop one silently. If you truly cannot place a hero, name its clip in
  `notes` and say why — a plan that drops a hero without saying so is rejected.
* Every other keep is a bound: build the cut from these ranges, trimming inside them
  as the rhythm asks, and do not reach outside a keep for more of the same moment. You
  may leave a keep out; say which in `notes` when you do.
* The rest of the inventory below is for connective tissue only — an establishing
  beat, a bridge, a reaction that pays a keep off — never a substitute for what the
  editor kept.
* The editor's note on a moment is the closest thing you have to being in the room.
  Read each one against its moment."""

SELECTS_REVISION_GUIDANCE = """\
The editor kept these moments when they culled the footage, with their notes. In a
revision they are context, not constraints: the current edit and the note above are
what you answer to. Use them to know what the editor valued — a keep that is not in
the edit is a good place to look when the note asks for more, and a shot that drops a
line marked **HERO** deserves a word in `notes`."""


def selects_section(selects: list[dict] | None, clips: dict[str, dict], *,
                    first: bool) -> str:
    """The bin as prompt text, or "" when the editor has kept nothing.

    One line per keep — `clip start-end (dur) — why — editor's note: "..." — HERO` —
    the words `HERO` and `editor's note` spelled the same way the guidance uses them,
    so the model can find them. A keep whose clip is not in the inventory (never
    analysed, or its footage missing) is left out: the model could not cut it anyway,
    and a hero it cannot see must not be a constraint it cannot satisfy.
    """
    rows = [s for s in (selects or [])
            if s.get("clip") in clips and not s.get("missing")]
    if not rows:
        return ""
    lines = []
    for s in sorted(rows, key=lambda s: (str(s["clip"]), float(s["start"]))):
        start, end = float(s["start"]), float(s["end"])
        parts = [f"{s['clip']} {start:.1f}-{end:.1f} ({end - start:.1f}s)"]
        if s.get("why"):
            parts.append(str(s["why"]).strip())
        if s.get("note"):
            parts.append(f"editor's note: \"{str(s['note']).strip()}\"")
        if s.get("hero"):
            parts.append("HERO")
        lines.append(" — ".join(parts))
    guidance = SELECTS_FIRST_GUIDANCE if first else SELECTS_REVISION_GUIDANCE
    return f"{SELECTS_HEADER}\n\n{guidance}\n\n" + "\n".join(lines)


def heroes_of(selects: list[dict] | None, clips: dict[str, dict]) -> list[dict]:
    """The hero ranges the validator holds a first cut to — only those the prompt
    actually showed (same filter as `selects_section`)."""
    return [{"clip": s["clip"], "start": float(s["start"]), "end": float(s["end"])}
            for s in (selects or [])
            if s.get("hero") and s.get("clip") in clips and not s.get("missing")]


SESSION_GAP_S = 4 * 3600


def shot_timeline(clips: dict[str, dict]) -> dict[str, str]:
    """When each clip was recorded, relative to the others.

    Karl, watching the first originated cut: *"some weirdness where airport footage
    was cut seemingly out of order in a way that didn't make sense."* He was right and
    the cause was not judgement — the model had never been told when anything was
    shot, so it read a 20:43 clip, a 21:40 clip and a 22:17 clip as interchangeable
    and cut back to the first after the third.

    Reported as **relative** position rather than wall-clock, deliberately: GoPro
    writes UTC, the trip was not in UTC, and a time of day that is seven hours out
    would be worse than no time of day at all. Sessions are inferred from gaps
    (> 4h starts a new one), which recovers "these are different days" without
    needing to know the timezone.
    """
    stamped = [(c, float(v["captured"])) for c, v in clips.items()
               if v.get("captured")]
    if not stamped:
        return {}
    stamped.sort(key=lambda cv: cv[1])
    out: dict[str, str] = {}
    session, prev = 1, None
    for i, (clip, when) in enumerate(stamped, 1):
        gap = None if prev is None else when - prev
        if gap is not None and gap > SESSION_GAP_S:
            session += 1
        if gap is None:
            rel = "first thing recorded"
        elif gap > SESSION_GAP_S:
            rel = f"{gap / 3600:.0f}h later — a different session"
        elif gap >= 3600:
            rel = f"{gap / 3600:.1f}h after the previous"
        else:
            rel = f"{gap / 60:.0f} min after the previous"
        out[clip] = f"recorded #{i} of {len(stamped)}, session {session}, {rel}"
        prev = when
    return out


def _clip_block(clip: dict, timeline: dict[str, str] | None = None) -> str:
    lines = [f"### {clip['clip']}  (duration {clip['duration']:.1f}s)"]
    when = (timeline or {}).get(clip["clip"])
    if when:
        lines.append(when)
    s = clip.get("summary") or {}
    if s:
        lines.append(
            f"speech_fraction={s.get('speech_fraction')} "
            f"wind={s.get('wind_dominant_fraction')} usable={s.get('audio_usable')}")

    # What is visibly happening, when anyone has looked. Karl: the first Killington
    # cut opened on him *talking about* falling into a river, three minutes after it
    # happened, from the clip that contains the fall — because a transcript records
    # people narrating events and never the events. These lines are what fix that.
    visual = clip.get("visual") or {}
    if visual.get("moments"):
        lines.append("what is visible (from sampled frames — no audio):")
        for m in visual["moments"]:
            mark = "!" if m.get("notable") else " "
            lines.append(f" {mark} {m['start']:.1f}-{m['end']:.1f}  "
                         f"[{m.get('kind', '')}] {m.get('what', '')}")
    if visual.get("unusable"):
        lines.append("unusable stretches (do not cut here): " + "; ".join(
            f"{u['start']:.1f}-{u['end']:.1f} {u.get('why', '')}"
            for u in visual["unusable"]))

    if clip.get("transcript"):
        lines.append("transcript:")
        lines += [f"  {u['start']:.1f}-{u['end']:.1f}  {u['text']}"
                  for u in clip["transcript"]]
    else:
        lines.append("transcript: (no speech)")
    return "\n".join(lines)


# ------------------------------------------------------------------ colour (I10.5)
#
# The Ask reaches the grade through the vocabulary M10 decided (decision 1: the model
# never writes filter strings): a patch on the EDL's colour block, validated against the
# real looks library by `colour.validate_patch`, and an invented look is a failed plan
# that gets the bounded re-ask like an invented clip. The section is short on purpose —
# a full Ask is already tens of thousands of tokens — and per-shot numbers are given
# only for the shots in the cut, since a shot outside it has no id to be graded by.

COLOUR_HEADER = "## Colour"

COLOUR_GUIDANCE = """\
The picture is graded through a closed vocabulary; you never write filters. **Leave
`colour` out of your answer unless the note asks for a change to the picture** — colour,
warmth, blue or yellow, brightness, contrast, "pop", a look, one shot matching another.
When it does, `colour` is a patch: only the keys that change.

  {"look": name or null, "strength": 0-1, "mode": "auto" or "off", "reference": shot id,
   "shots": {"<shot id>": {"balance": {"gain": [r, g, b], "exposure": x, "knee": k,
                                       "lift": l} or null,
                            "auto": true/false, "look": name or null, "strength": 0-1,
                            "match": "previous" or "reference" or null}}}

* `look` / `strength` — the film's look, by a name from the list below and nothing else;
  0.4-0.6 reads as a look, above 0.8 as a filter. Choose by description ("make it pop"
  is the one whose description says contrast and vibrance). A shot's own `look` (null:
  none on that shot) overrides the film's.
* `balance` replaces the shot's auto balance, so start from the shot's numbers below and
  move them: warmer = gain r up and b down by 0.02-0.04 a step, "less blue" = b down,
  brighter = exposure ×1.05 a step. Limits: each gain 0.85-1.15, exposure 0.75-1.5,
  lift ±0.04, knee 0.6-1.0 — outside them the plan is rejected. `balance: null` returns
  the shot to the auto; `auto: false` shows it as the camera shot it.
* `match: "previous"` moves a shot's colour toward the shot before it; `"reference"`
  toward the film's reference shot, which `reference` sets — "match the lift shot to the
  summit" is `reference` = the summit's id and `match: "reference"` on the lift shot.
* Shots are named by the ids below; an id that is not listed is rejected."""

COLOUR_FACTS = """\
Each shot, measured on sampled frames: `white` is the bright near-neutral surface (snow,
sky, a wall) — L* its brightness, a*/b* its cast (b* below 0 is blue, above 0 yellow; a*
below 0 green, above 0 magenta); `clipped` the share of pixels at 98 % or more, which no
grade brings back; `balance` what the shot resolves to now (auto or a hand's)."""


def _shot_colour_line(n: int, row: dict, over: dict | None) -> str:
    w = row.get("witness") or {}
    parts = [f"  {n:2d}. {row['id']}  {Path(row['clip']).stem}"]
    if w.get("white_source") == "surface" and w.get("white"):
        wh = w["white"]
        parts.append(f"white L {wh['L']:.0f} a {wh['a']:+.1f} b {wh['b']:+.1f}")
    elif w.get("white_source") == "grey":
        parts.append("no white surface (grey-world)")
    elif w.get("n"):
        parts.append("no white reference")
    else:
        parts.append("not measured")
    if w.get("clip") is not None and w.get("n"):
        parts.append(f"clipped {100 * float(w['clip']):.1f}%")
    if w.get("chroma") is not None:
        parts.append(f"chroma {float(w['chroma']):.1f}")
    b = row.get("balance")
    if b:
        g = b["gain"]
        parts.append(f"balance {b.get('source', 'auto')} gain {g[0]:.2f}/{g[1]:.2f}/{g[2]:.2f} "
                     f"×{float(b['exposure']):.2f} lift {float(b.get('lift', 0)):+.3f}")
    else:
        parts.append("balance none (as shot)")
    look = row.get("look")
    parts.append(f"look {look} at {row.get('strength', 0):g}" if look else "no look")
    if over:
        parts.append("override " + json.dumps(over, separators=(",", ":")))
    return " · ".join(parts)


def colour_section(ctx: dict | None, segments: list[dict],
                   only: str | None = None) -> str:
    """The colour vocabulary, the film's block, the looks by name and description, and
    one line of numbers per shot in the cut — or "" with nothing to grade. `only` (the
    scoped ask) lists that one shot and says the patch may touch nothing else."""
    if not ctx:
        return ""
    film = ctx.get("film") or {}
    looks = [(k, v.get("description", "")) for k, v in (ctx.get("looks") or {}).items()
             if v.get("kind") != "broken"]
    rows = {r["id"]: r for r in ctx.get("shots") or [] if r.get("id")}
    overs = film.get("shots") or {}
    lines = []
    for i, s in enumerate(segments):
        sid = s.get("id")
        if sid in rows and (only is None or sid == only):
            lines.append(_shot_colour_line(i + 1, rows[sid], overs.get(sid)))
    if only is not None and not lines:
        return ""
    now = (f"mode {film.get('mode', 'auto')}, "
           + (f"look {film['look']} at {film.get('strength', colourmod.DEFAULT_STRENGTH):g}"
              if film.get("look") else "no look")
           + (f", reference {film['reference']}" if film.get("reference") else
              ", reference: the first shot"))
    scope = ("" if only is None else
             f"\n\nThis note is about ONE shot: `colour` may only set `shots.{only}` — the "
             "film's look, mode and reference, and every other shot, are not yours to "
             "change here.")
    return (f"{COLOUR_HEADER}\n\n{COLOUR_GUIDANCE}{scope}\n\nThe film now: {now}.\n\n"
            "Looks (the only names that exist):\n"
            + "\n".join(f"* {k} — {d}" for k, d in looks)
            + (f"\n\n{COLOUR_FACTS}\n" + "\n".join(lines) if lines else ""))


def carry_ids(new: list[dict], current: list[dict]) -> list[dict]:
    """The plan's shots take the ids of the cut's shots they continue — same clip and
    overlapping at least half the shorter of the two (the ghost lane's `matchPlan` rule),
    each id claimed once. Without it every Accept minted new ids, and every per-shot
    colour override, hand or proposed, was keyed to a shot that no longer existed."""
    out = [dict(s) for s in new]
    used: set[str] = set()
    for s in out:
        best, best_r = None, 0.5
        for c in current:
            cid = c.get("id")
            if not cid or cid in used or c.get("clip") != s.get("clip"):
                continue
            a0, a1 = float(s["in"]), float(s["out"])
            b0, b1 = float(c["in"]), float(c["out"])
            shorter = max(1e-6, min(a1 - a0, b1 - b0))
            r = max(0.0, min(a1, b1) - max(a0, b0)) / shorter
            if r >= best_r:
                best, best_r = cid, r
        if best:
            s["id"] = best
            used.add(best)
    return out


LOCKED_MARK = "[LOCKED — the editor's own work: keep exactly]"

LOCKED_GUIDANCE = """\
Shots marked LOCKED are the editor's own work (trimmed, split, moved or added by hand).
Return each of them exactly as it is — the same clip, the same in and out, to the
hundredth of a second — and keep the locked shots in the same order relative to each
other. Everything else is yours to replace, re-order or drop. Do not describe a shot as
kept or unchanged unless its in and out are exactly what they were."""


def new_clips_section(new_clips: list[str] | None) -> str:
    """The re-run's paragraph on footage imported since the last pass: which clips are
    new, that each is worth a look, and that each gets an item in `new_clips`."""
    if not new_clips:
        return ""
    rows = "\n".join(f"  - {c}" for c in new_clips)
    return f"""## New footage since the last pass
The editor added these clips to the bin after the last first pass. Consider each of
them for the cut on its merits — use one where it beats what is there, leave it out
where it does not:
{rows}

For EVERY clip in this list, add one item to `new_clips`: the `clip`, `used` (true when
a segment of your edit draws on it) and a `note` — one short sentence naming the shot
you used it in (its number in your edit) and what it is for, or why you left it out (a
near-duplicate of another clip, nothing usable in it, off the story)."""


def validate_new_clips(raw: Any, new_clips: list[str]) -> list[dict]:
    """The model's `new_clips`, forgiving like `checks`: an item is kept when it names a
    clip of the list (exactly, or by its name without the extension, any case) and has
    words; the first item per clip wins; nothing here costs a re-ask — a clip the model
    says nothing about gets the board's own line (`firstpass.new_clip_rows`). Each is
    `{clip, used, note}`."""
    if not isinstance(raw, list) or not new_clips:
        return []
    by_name = {c: c for c in new_clips}
    by_name.update({Path(c).stem.lower(): c for c in new_clips})
    by_name.update({c.lower(): c for c in new_clips})
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("clip") or "").strip()
        clip = by_name.get(name) or by_name.get(name.lower()) \
            or by_name.get(Path(name).stem.lower())
        note = trim_check(item.get("note"), NEW_NOTE_MAX)
        if clip is None or clip in seen or not note:
            continue
        seen.add(clip)
        out.append({"clip": clip, "used": bool(item.get("used")), "note": note})
    return out


def build_prompt(segments: list[dict], clips: dict[str, dict], story: str,
                 note: str, target: tuple[float, float],
                 events: list[dict] | None = None,
                 selects: list[dict] | None = None,
                 colour: dict | None = None,
                 locked: set[str] | None = None,
                 new_clips: list[str] | None = None) -> str:
    locked = set(locked or ())
    current = "\n".join(
        f"  {i + 1:2d}. {s['clip']} {s['in']:.2f}-{s['out']:.2f} "
        f"({edits.dur(s):.1f}s) — {s.get('why', '')}"
        + (f"  {LOCKED_MARK}" if s.get("id") in locked else "")
        for i, s in enumerate(segments))
    if locked:
        current = f"{current}\n\n{LOCKED_GUIDANCE}"
    total = sum(edits.dur(s) for s in segments)
    timeline = shot_timeline(clips)
    inventory = "\n\n".join(_clip_block(c, timeline) for c in clips.values())
    # Before the inventory, always. A ranked list that arrives after forty clip blocks
    # is a footnote; the whole point is that it is read first. The bin, when there is
    # one, goes ahead of even that: it is what the editor chose, not what a pass found.
    kept = selects_section(selects, clips, first=False)
    kept = f"{kept}\n\n" if kept else ""
    ranked = events_section(events or [])
    ranked = f"{ranked}\n\n" if ranked else ""
    # After the note and before the bin: short, and only read when the note is about
    # the picture — but it must be seen before the inventory buries it.
    graded = colour_section(colour, segments)
    graded = f"{graded}\n\n" if graded else ""
    colour_only = ("\n\nIf the note asks only for a change to the picture, you may leave "
                   "out `segments` entirely: the cut then stays exactly as it is and only "
                   "`colour` changes." if graded else "")
    fresh = new_clips_section(new_clips)
    fresh = f"{fresh}\n\n" if fresh else ""

    return f"""The editor is cutting a short film from one bin of footage.

## What this film is about
{story.strip() or "(the editor has not written this yet — infer it from the material)"}

## Target length
{target[0]:.0f}-{target[1]:.0f} seconds. The current edit is {total:.1f}s over \
{len(segments)} shots.

## The current edit, in order
{current}

## The editor's note
{note.strip()}

{fresh}{graded}{kept}{ranked}## Every clip available, with its transcript
{inventory}

## What to return
The full revised edit as an ordered list of segments — not a diff, not only the parts
you changed. Keep what works; the note tells you what to change. Timestamps are
seconds within the named clip. Prefer cutting on utterance boundaries visible in the
transcripts above. Each clip says when it was recorded relative to the others — use it
as information rather than as a rule. Deliberate reordering is good editing; only the
accidental kind, drifting backwards through one continuous stretch for no reason,
reads as a mistake. Say in the `why` when a move is deliberate.
{CHECKS_GUIDANCE}{colour_only}"""


def build_first_prompt(clips: dict[str, dict], story: str, note: str,
                       target: tuple[float, float],
                       events: list[dict] | None = None,
                       selects: list[dict] | None = None,
                       new_clips: list[str] | None = None) -> str:
    """The originating prompt: no current edit, so the material and the brief carry it
    — and the bin, when the editor has culled, which then outranks both. `new_clips`
    (a re-run that starts over): the footage imported since the last pass, each to be
    accounted for in `new_clips`."""
    timeline = shot_timeline(clips)
    inventory = "\n\n".join(_clip_block(c, timeline) for c in clips.values())
    kept = selects_section(selects, clips, first=True)
    kept = f"{kept}\n\n" if kept else ""
    ranked = events_section(events or [])
    ranked = f"{ranked}\n\n" if ranked else ""
    total = sum(float(c["duration"]) for c in clips.values())
    guidance = FIRST_GUIDANCE + (
        VISUAL_GUIDANCE if any((c.get("visual") or {}).get("moments")
                               for c in clips.values()) else "")
    brief = story.strip() or (
        "(the editor has not written this yet — infer what this film is about from "
        "the material, and say what you inferred in your notes so they can correct it)")
    ask = note.strip() or "(no further direction — use your judgement)"
    fresh = new_clips_section(new_clips)
    fresh = f"{fresh}\n\n" if fresh else ""

    return f"""The editor is starting a short film from one bin of footage. There is no \
edit yet; you are making the first one.

## What this film is about
{brief}

## The editor's direction
{ask}

## Target length
{target[0]:.0f}-{target[1]:.0f} seconds, drawn from {len(clips)} clips totalling \
{total:.0f}s of material.

## How to read this material
{guidance}

{fresh}{kept}{ranked}## Every clip available, with its transcript
{inventory}

## What to return
A complete first cut as an ordered list of segments — the order they should play in,
not the order the clips were shot. Timestamps are seconds within the named clip. Cut
on utterance boundaries visible in the transcripts above. Every segment needs a `why`:
one sentence on what that moment is for. In `notes`, say what you decided this film is
about\
{" — and name any of the editor's selects you left out, heroes first." if kept else "."}
What you would look at first if it is wrong goes in `checks`. {CHECKS_GUIDANCE}"""


SHOT_SYSTEM = (
    "You are an assistant film editor revising ONE shot of a rough cut in response "
    "to the editor's note about that shot. You return only that shot's replacement — "
    "usually one segment, sometimes more if the note asks to split it — drawn from "
    "the same clip the shot comes from. You never invent timestamps: every segment "
    "must lie inside that clip's duration. You cut on complete thoughts rather than "
    "mid-sentence, and you keep the shot's place in the film in mind: what precedes "
    "and follows it is shown to you.")

# What the bar assumes when a shot-scoped ask starts. Computed, not asked for: the
# prompt is one clip block instead of a bin inventory, so the call is far shorter than
# a full Ask, and a model estimate would spend a call to guess a number this small.
SHOT_FALLBACK_ETA_S = 60.0


def build_shot_prompt(segments: list[dict], index: int, clips: dict[str, dict],
                      story: str, note: str,
                      target: tuple[float, float],
                      colour: dict | None = None) -> str:
    """The scoped prompt: the whole film for context, one clip in full detail.

    Deliberately NOT the bin inventory. A full Ask is ~40k tokens and 3-4 minutes on
    the CLI backend, which is the right price for restructuring a film and the wrong
    one for "let this line finish". The model sees every shot's place and reason, but
    the only clip it can cut from is the one the note is about — cross-clip changes
    are what the full Ask and the finder are for, and the validator enforces it.
    """
    seg = segments[index]
    current = "\n".join(
        f"  {i + 1:2d}. {s['clip']} {float(s['in']):.2f}-{float(s['out']):.2f} "
        f"({float(s['out']) - float(s['in']):.1f}s) — {s.get('why', '')}"
        + ("   ← the shot this note is about" if i == index else "")
        for i, s in enumerate(segments))
    total = sum(float(s["out"]) - float(s["in"]) for s in segments)
    clip = seg["clip"]
    # No shot_timeline here: relative capture order is chronology *across* clips, and
    # this call is confined to one.
    block = _clip_block(clips[clip])
    # The colour clause (I10.5): this shot's numbers only, and a patch confined to its id.
    graded = colour_section(colour, segments, only=seg.get("id")) if seg.get("id") else ""
    graded = f"\n\n{graded}" if graded else ""
    colour_only = ("\n\nIf the note is only about the picture, leave out `segments`: the "
                   "shot keeps its range and only `colour` changes." if graded else "")

    return f"""The editor is cutting a short film from one bin of footage and has a note \
about ONE shot of the current edit.

## What this film is about
{story.strip() or "(the editor has not written this yet)"}

## Target length
{target[0]:.0f}-{target[1]:.0f} seconds. The current edit is {total:.1f}s over \
{len(segments)} shots.

## The current edit, in order
{current}

## The shot the note is about
Shot {index + 1} of {len(segments)}: {clip} {float(seg['in']):.2f}-{float(seg['out']):.2f} \
({float(seg['out']) - float(seg['in']):.1f}s)
Why it is there: {seg.get('why') or '(no reason recorded)'}

## The editor's note about this shot
{note.strip()}

## The clip this shot comes from
{block}{graded}

## What to return
The replacement for this one shot only, as `segments` — usually one segment, more if
the note asks to split it. Every segment must come from {clip}; the rest of the edit
is not yours to change here. Timestamps are seconds within the clip. Prefer cutting on
utterance boundaries visible in the transcript, and mind what plays before and after
this shot. If the note would be best served by removing the shot entirely, return it
unchanged and say so in `notes` — removing is the editor's own one-click action.
{CHECKS_GUIDANCE}{colour_only}"""


def propose_shot(segments: list[dict], index: int, clips: dict[str, dict],
                 story: str, note: str,
                 target: tuple[float, float] = (120.0, 180.0),
                 on_partial: Callable[[str, str], None] | None = None,
                 colour: dict | None = None) -> dict:
    """Ask for a revision of one shot. Returns {'segments', 'notes', 'usage'} where
    `segments` is the replacement for that shot alone — the caller splices — and
    `colour`, when the note asked for one, a patch that touches that shot's id only.

    `colour` is the grading context (`colour_section`'s `ctx`); the clause is offered
    only when the shot has an id to key it by."""
    if not note.strip():
        raise ValueError("empty note")
    if not segments:
        raise ValueError("no shots to revise")
    if not (0 <= index < len(segments)):
        raise ValueError(f"no shot {index} in a {len(segments)}-shot cut")
    clip = segments[index]["clip"]
    if clip not in clips:
        raise ValueError(f"{clip} has no analysis — run the audio pass first")
    scoped = {clip: clips[clip]}
    sid = segments[index].get("id")
    ctx = colour if sid else None
    return _ask(build_shot_prompt(segments, index, clips, story, note, target, ctx),
                SHOT_SYSTEM, scoped, on_partial, colour=ctx,
                current=[segments[index]], focus_id=sid)


# A hero counts as placed when one shot covers at least this much of its range — the
# model may trim inside a hero, and half of it is still the moment.
HERO_MIN_OVERLAP = 0.5


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def _names_clip(notes: str, clip: str) -> bool:
    """Whether `notes` mentions the clip — by file name or by stem, since a model that
    writes "left CLIP_06 out" has explained itself as well as one that writes the
    extension."""
    text = notes.lower()
    return clip.lower() in text or Path(clip).stem.lower() in text


def dropped_heroes(segments: list[dict], heroes: list[dict]) -> list[dict]:
    """The heroes no single segment covers to HERO_MIN_OVERLAP of their length."""
    out = []
    for h in heroes:
        span = (float(h["start"]), float(h["end"]))
        length = max(1e-6, span[1] - span[0])
        best = max((_overlap(span, (float(s["in"]), float(s["out"])))
                    for s in segments if s.get("clip") == h.get("clip")), default=0.0)
        if best / length < HERO_MIN_OVERLAP:
            out.append(h)
    return out


def trim_check(text: Any, limit: int = CHECK_TEXT_MAX) -> str:
    """A check's words, whitespace collapsed, at most `limit` characters and never cut
    inside a word: past the limit it ends at the last whole sentence that fits (when that
    keeps at least half of it), else at the last whole word, with "…"."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    head = text[:limit + 1]
    ends = [m.end() for m in re.finditer(r'[.!?][)"\'”’]*(?=\s)', head) if m.end() <= limit]
    if ends and ends[-1] >= limit // 2:
        return text[:ends[-1]]
    cut = head.rfind(" ", 0, limit)
    if cut <= 0:                       # one word longer than the limit: nothing to keep whole
        return text[:limit - 1] + "…"
    return text[:cut].rstrip(" ,;:—–-") + "…"


def validate_checks(raw: Any, clips: dict[str, dict]) -> list[dict]:
    """The model's `checks`, forgiving where the plan is strict: a check is advice to
    look, so a bad one is trimmed (an unknown clip or a range outside it loses its
    range, keeps its words) or dropped (no words), never a re-ask. Each is `{text, clip,
    in, out, kind: "model"}`, clip/in/out None when it is about the whole film."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for c in raw:
        if isinstance(c, str):
            c = {"text": c}
        if not isinstance(c, dict):
            continue
        text = trim_check(c.get("text"))
        if not text:
            continue
        clip, t_in, t_out = c.get("clip"), None, None
        if clip in clips:
            try:
                t_in, t_out = float(c["in"]), float(c["out"])
            except (KeyError, TypeError, ValueError):
                t_in = t_out = None
            dur = float(clips[clip].get("duration") or 0.0)
            if t_in is not None and not (0.0 <= t_in < t_out <= dur + 0.05):
                t_in = t_out = None
            if t_in is not None:
                t_in, t_out = round(t_in, 2), round(min(t_out, dur), 2)
        else:
            clip = None
        out.append({"text": text, "clip": clip, "in": t_in, "out": t_out, "kind": "model"})
        if len(out) >= CHECKS_MAX:
            break
    return out


def validate_plan(payload: Any, clips: dict[str, dict],
                  heroes: list[dict] | None = None, *,
                  colour: dict | None = None, current: list[dict] | None = None,
                  focus_id: str | None = None,
                  new_clips: list[str] | None = None) -> dict:
    """Strict. A plausible-looking plan that names a clip we do not have, or runs
    past the end of one, is worse than a loud failure — it renders as a crash or,
    worse, as silently missing footage.

    `heroes` — ranges `{clip, start, end}` the editor marked must-appear — extends the
    contract: a plan that covers less than half of a hero with any one shot is rejected
    unless `notes` names that hero's clip. The model may drop a hero; it must say so,
    because "hero = must appear, model may trim inside" (docs/INTAKE.md, defaults) is
    only honest if a dropped one is visible to the editor rather than quietly gone.

    `colour` (the grading context, I10.5) admits a `colour` patch, held to the real
    looks library and to the ids of `current` — the cut's shots, or with `focus_id` the
    one shot a scoped ask is about, and then no film-level keys. With it, a plan may
    leave `segments` out to mean "the cut as it is": a colour-only note need not make
    the model re-type every shot. Without it a `colour` key is dropped — the model was
    never shown the vocabulary, so nothing it wrote there can be trusted.

    `new_clips` (a re-run with footage imported since the last pass) admits the
    model's per-clip word on them, forgivingly (`validate_new_clips`), as `new_clips`.
    """
    said = ({"new_clips": validate_new_clips(payload.get("new_clips"), new_clips)}
            if new_clips and isinstance(payload, dict) else {})
    patch: dict = {}
    if isinstance(payload, dict) and colour is not None and payload.get("colour"):
        ids = ({focus_id} if focus_id else
               {s["id"] for s in current or [] if s.get("id")})
        try:
            patch = colourmod.validate_patch(payload["colour"], colour.get("looks") or {},
                                             ids=ids, film=focus_id is None)
        except ValueError as exc:
            raise ValueError(f"colour: {exc}") from None
    if (isinstance(payload, dict) and payload.get("segments") in (None, [])
            and patch and current):
        # Colour only: the shots verbatim (ids, speed and all), and nothing to polish.
        return {"segments": [dict(s) for s in current],
                "notes": str(payload.get("notes", "")).strip()[:1200],
                "checks": validate_checks(payload.get("checks"), clips),
                "colour": patch, "unchanged": True, **said}
    if not isinstance(payload, dict) or "segments" not in payload:
        raise ValueError("expected an object with a 'segments' list")
    segments = payload["segments"]
    if not isinstance(segments, list) or not segments:
        raise ValueError("'segments' must be a non-empty list")

    clean: list[dict] = []
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            raise ValueError(f"segment {i} is not an object")
        clip = seg.get("clip")
        if clip not in clips:
            raise ValueError(f"segment {i} names unknown clip {clip!r}")
        try:
            t_in, t_out = float(seg["in"]), float(seg["out"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"segment {i} has non-numeric in/out") from None
        duration = float(clips[clip]["duration"])
        if not (0.0 <= t_in < t_out <= duration + 0.05):
            raise ValueError(
                f"segment {i} range {t_in}-{t_out} outside {clip} (0-{duration:.1f})")
        clean.append({"clip": clip, "in": round(t_in, 2),
                      "out": round(min(t_out, duration), 2),
                      "why": str(seg.get("why", "")).strip()[:200]})
    notes = str(payload.get("notes", "")).strip()[:1200]
    for h in dropped_heroes(clean, heroes or []):
        if not _names_clip(notes, str(h["clip"])):
            raise ValueError(
                f"the plan drops the editor's hero {h['clip']} "
                f"{float(h['start']):.1f}-{float(h['end']):.1f} without saying why: "
                f"keep it (trimming inside its range is fine) or name {h['clip']} "
                f"in notes and explain")
    plan: dict = {"segments": clean, "notes": notes,
                  "checks": validate_checks(payload.get("checks"), clips), **said}
    if current:
        plan["segments"] = carry_ids(clean, current)
    if patch:
        # A shot the patch grades but the plan no longer contains (cut, or retrimmed
        # past recognition) has nothing to carry the change: it is left out and said,
        # the way polish declares what it moved — rejecting it would spend a whole
        # re-ask on a line the editor can redo with one nudge.
        kept = {s.get("id") for s in plan["segments"] if s.get("id")}
        gone = [sid for sid in (patch.get("shots") or {}) if sid not in kept]
        for sid in gone:
            patch["shots"].pop(sid)
        if "shots" in patch and not patch["shots"]:
            patch.pop("shots")
        if patch.get("reference") and patch["reference"] not in kept:
            patch.pop("reference")
            gone.append("the reference")
        if gone:
            line = (f"Colour: left out the change for {', '.join(gone)} — the plan no "
                    "longer has that shot.")
            plan["notes"] = f"{notes}\n\n{line}".strip()
        if patch:
            plan["colour"] = patch
    return plan


# ------------------------------------------------------------------ progress
#
# An Ask is one `claude -p` call three to four minutes long, and a call like that
# returns nothing until it is over — which is why the board could only ever show an
# elapsed clock next to the word "thinking". These are the five things the app can
# actually *observe* happening inside it, given the CLI's streamed deltas: they are
# offered to the estimator as the vocabulary it may weight, and completed from the
# stream as they really occur. A milestone nobody can observe would be a bar that
# stops moving, which is the problem, not the fix.
ASK_CHECKPOINTS = [
    ("read", "the prompt is sent and the model has begun reasoning — on a bin whose "
             "inventory runs to tens of thousands of tokens this is not instant"),
    ("think", "it stops reasoning and starts writing the answer"),
    ("shots", "it writes the shots, one JSON object each — the long stretch, and the "
              "one where progress can be counted as they appear"),
    ("notes", "it writes its closing note to the editor, after the last shot"),
    ("polish", "the app validates the plan, snaps the cut points to speech and saves "
               "it — local work, no model call"),
]

# Measured on this bin, and stated to the estimator rather than left to be guessed:
# a 20-shot revision of variant B took 74s, a 17-shot revision of the Killington cut
# 79s, and a first cut from a 39k-token inventory ~200s.
FALLBACK_ETA_S = 160.0

# And the *shape* of the call, which matters more to a bar than its length: timed on
# the Killington revision, the reasoning is nearly three quarters of it and the shots
# — the only part with a countable denominator — are a fifth. The first estimate the
# model produced split it 8%/42%/33%/8%/8% and the bar consequently sat at 31% when
# the model was seconds from writing, so the measured split is now given to the
# estimator as evidence rather than left to be guessed at.
PHASE_SHARE = (("read", "reading the footage", 0.05),
               ("think", "working out the shape", 0.72),
               ("shots", "choosing the shots", 0.19),
               ("notes", "writing its reasoning", 0.03),
               ("polish", "snapping cuts to speech", 0.01))
FALLBACK_WEIGHTS = PHASE_SHARE


def fallback_estimate(eta_s: float = FALLBACK_ETA_S) -> Estimate:
    """What the bar uses when the estimate call fails. Never optional — the estimate
    is a nicety and the work is not, so the work must be able to start without one."""
    return Estimate(eta_s=eta_s,
                    milestones=[milestone(k, label, w)
                                for k, label, w in FALLBACK_WEIGHTS],
                    source="fallback")


def count_shots(text: str) -> int:
    """How many shots the model has written so far, from a half-finished answer.

    Counting `"clip"` rather than parsing: the text is mid-object most of the time, so
    there is nothing valid to parse, and every segment carries exactly one `clip` key.
    Wrong only if the model writes the word in its prose, which costs a slightly eager
    bar and nothing else.
    """
    return text.count('"clip"')


def expected_shots(segments: list[dict], target: tuple[float, float]) -> int:
    """A prior for how many shots the answer will have, so "8 of ~18" can be said
    before the answer exists. The current cut when there is one — a revision keeps most
    of what works — otherwise the target length over a typical shot."""
    if segments:
        return max(1, len(segments))
    return max(4, round((float(target[0]) + float(target[1])) / 2 / 8.0))


def estimate_ask(clips: dict[str, dict], segments: list[dict], note: str,
                 target: tuple[float, float], *, shots: int | None = None) -> Estimate:
    """The cheap call that draws the bar, made before the expensive one that fills it."""
    shots = shots if shots is not None else expected_shots(segments, target)
    material = sum(float(c.get("duration") or 0.0) for c in clips.values())
    lines = sum(len(c.get("transcript") or []) for c in clips.values())
    seen = sum(1 for c in clips.values() if (c.get("visual") or {}).get("moments"))
    first = not segments
    shape = ", ".join(f"{k} {share:.0%}" for k, _label, share in PHASE_SHARE)
    what = ("An assistant film editor is about to write the FIRST cut of a short film "
            "from one bin of raw footage." if first else
            "An assistant film editor is about to revise a short film in response to "
            "one note from the editor.")
    facts = f"""It is a single call to a top-tier model through the Claude Code CLI, with
thinking enabled, and it must return the whole edit as JSON.

* {len(clips)} clips, {material:.0f}s of material, {lines} transcript lines\
{f', {seen} clips with a visual pass' if seen else ''}
* the prompt is roughly {int((lines * 12 + len(clips) * 120 + seen * 400) / 1000)}k tokens
* the answer will be about {shots} shots, each a JSON object with a one-sentence reason
* the editor's note is {len(note.split())} words
* target length {target[0]:.0f}-{target[1]:.0f}s
* measured on this machine: a 20-shot revision took 74s end to end, a 17-shot revision
  of a 12-clip bin 79s, and a first cut from a 39k-token inventory about 200s. Calls
  are killed at 600s.
* measured shape of one such call, wall clock: {shape}. Weight the checkpoints like
  that unless something about this job says otherwise — the reasoning really is most
  of it, and a bar that reaches a third of the way while the model is seconds from
  writing has misled the person watching it."""
    return estimate(what, facts, ASK_CHECKPOINTS,
                    fallback=fallback_estimate(),
                    eta_limits=(20.0, float(config.call_timeout_s())))


def _ask(prompt: str, system: str, clips: dict[str, dict],
         on_partial: Callable[[str, str], None] | None = None,
         heroes: list[dict] | None = None, *, colour: dict | None = None,
         current: list[dict] | None = None, focus_id: str | None = None,
         new_clips: list[str] | None = None) -> dict:
    schema = PLAN_SCHEMA_COLOUR if colour is not None else PLAN_SCHEMA
    if new_clips:
        schema = {**schema, "new_clips": NEW_CLIPS_SHAPE}
    result = complete(
        prompt, role=config.ROLE_SKELETON, schema=schema, system=system,
        validate=lambda payload: validate_plan(payload, clips, heroes, colour=colour,
                                               current=current, focus_id=focus_id,
                                               new_clips=new_clips),
        retries=1, on_partial=on_partial)
    # Boundary polish runs on the *validated* plan, so it can assume in/out are
    # real numbers inside a real clip and worry only about where they land in the
    # speech. Both callers get it: a first cut has the same clipped words as a
    # revision, and asking the model to be more careful about a timestamp it read
    # off a transcript is asking it to do arithmetic the sidecar already knows.
    # A colour-only plan is the cut as it stands: nothing the model chose to polish.
    plan = (dict(result.content) if result.content.get("unchanged")
            else polish_kept_alone(result.content, clips, current))
    plan["usage"] = {
        "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
        "projected_usd": result.projected_usd, "model": result.model,
        "backend": result.backend, "latency_ms": result.latency_ms,
    }
    return plan


def propose(segments: list[dict], clips: dict[str, dict], story: str, note: str,
            target: tuple[float, float] = (120.0, 180.0),
            events: list[dict] | None = None,
            on_partial: Callable[[str, str], None] | None = None,
            selects: list[dict] | None = None,
            colour: dict | None = None,
            locked: set[str] | None = None,
            new_clips: list[str] | None = None) -> dict:
    """Ask for a revision. Returns {'segments', 'notes', 'usage'}, plus `colour` (a
    patch on the EDL's colour block) when the note asked for a change to the picture.

    `selects` — the bin (`edl["selects"]`) — is context here, not a constraint: the
    editor has already accepted a cut, and the note is what they are steering by now.
    `colour` — `{"film": the EDL's block, "looks": the library, "shots": the server's
    resolved rows}` — offers the grade (INTAKE I10.5); without it the Ask is cut-only.
    The plan's shots carry the ids of the cut's shots they continue (`carry_ids`).
    `locked` — shot ids the model is told to return exactly (the first pass's re-run
    keeps the editor's hand edits); the caller enforces it (`firstpass.enforce_locks`),
    the prompt only asks. `new_clips` — footage imported since the last pass (a
    re-run): the prompt lists it and the answer says, per clip, what it did with it
    (`new_clips` in the plan).
    """
    if not note.strip():
        raise ValueError("empty note")
    return _ask(build_prompt(segments, clips, story, note, target, events, selects,
                             colour, locked, new_clips),
                SYSTEM, clips, on_partial, colour=colour, current=segments,
                new_clips=new_clips)


# ------------------------------------------------------------------ the truth of a plan
#
# Judge verdict 1 (punch item 10): an Ask's notes said "I've kept your CLIP_11 razor cut
# exactly as it was" and "the second half is unchanged" while the diff showed CLIP_11
# 159.2-166.2 become 159.2-164.8. Two causes, both fixed here: boundary polish moved a
# shot the model had copied verbatim (`polish_kept_alone`), and nothing compared what
# the notes claim with what the plan does (`check_notes`).

SAME_EPS = 0.005


def _same_range(a: dict, b: dict) -> bool:
    return (a.get("clip") == b.get("clip")
            and abs(float(a["in"]) - float(b["in"])) < SAME_EPS
            and abs(float(a["out"]) - float(b["out"])) < SAME_EPS)


# The prompt prints the cut in hundredths, so a shot the model copies comes back up to
# 0.005 s off (the validator's rounding adds float noise): within this it is an echo.
# Under half a frame at 60 fps, so a deliberate one-frame trim is never mistaken for one.
ECHO_EPS = 0.006


def _echoed(a: dict, b: dict) -> bool:
    return (a.get("clip") == b.get("clip")
            and abs(float(a["in"]) - float(b["in"])) <= ECHO_EPS
            and abs(float(a["out"]) - float(b["out"])) <= ECHO_EPS)


# ------------------------------------------------------------------ the frame grid
#
# The journey (wave 3): the export came out VFR (r_frame_rate 120/1) because a
# proposal's cut points sat between frames — boundary polish pads a sentence by 0.15 s,
# the model writes hundredths. An editor's in and out are frames, so a proposal's are
# too: every range a proposal *makes* lands on the sequence's frame grid. A range it
# copies from the cut is left exactly as it is (the editor's own, or an accepted
# earlier proposal's), and a shot that continues one of the cut's keeps its Effect
# Controls exactly — the model never sees `controls`, so its plan never carries them.

# The broadcast rates ffprobe reports as 29.97 / 59.94 / 23.976 are N*1000/1001.
NTSC = {23.976: 24000 / 1001, 29.97: 30000 / 1001, 47.952: 48000 / 1001,
        59.94: 60000 / 1001, 119.88: 120000 / 1001}


def _speed(seg: dict) -> float:
    try:
        v = float(seg.get("speed") or 1.0)
    except (TypeError, ValueError):
        return 1.0
    return v if 0.1 <= v <= 4.0 else 1.0


def exact_rate(fps: float | None) -> float | None:
    """The sequence rate as the frame grid uses it: 59.94 → 60000/1001."""
    try:
        f = float(fps or 0.0)
    except (TypeError, ValueError):
        return None
    if f <= 0:
        return None
    for k, v in NTSC.items():
        if abs(f - k) < 0.01:
            return v
    return f


def on_frames(t: float, fps: float) -> float:
    """`t` on the nearest frame boundary of `fps`, to 1/10000 s (well inside half a
    frame at any rate; `round(t * fps)` recovers the frame number exactly)."""
    return round(round(float(t) * fps) / fps, 4)


def to_sequence(plan: dict, current: list[dict] | None, fps: float | None,
                durations: dict[str, float] | None = None) -> dict:
    """A proposal as the timeline takes it: each new range on the frame grid of `fps` —
    the SEQUENCE's timebase, never a clip's (verdict 10, punch 3) — at least one frame
    long, a retimed shot's length whole frames at its speed, and never past its clip's
    media (`durations`, clip seconds by name, when known); each range copied from
    `current` — exactly, or typed back in hundredths (`_echoed`) — set to the cut's own
    in and out, and each shot that continues one of the cut's (same id) carrying its
    `controls` over exactly. Returns a new plan; `plan` is not modified."""
    rate = exact_rate(fps)
    cur = [s for s in (current or []) if isinstance(s, dict) and "in" in s and "out" in s]
    by_id = {c.get("id"): c for c in cur if c.get("id")}
    out = dict(plan)
    segs = []
    for s in plan.get("segments") or []:
        if not (isinstance(s, dict) and "in" in s and "out" in s):
            segs.append(s)
            continue
        s = dict(s)
        # A shot the model typed back from the cut is the cut's shot: its in and out
        # snap back to the cut's own values, exactly (verdict 4: the prompt shows
        # hundredths, the model echoed CLIP_02 1.4014 as 1.4, and 12 "kept" shots fell
        # off the frame grid). Same id when both have one, else the same clip.
        src = next((c for c in cur if _echoed(s, c) and (
            not s.get("id") or not c.get("id") or s.get("id") == c.get("id"))), None)
        copied = src is not None
        if copied:
            s["in"], s["out"] = src["in"], src["out"]
        if rate and not copied:
            a, b = on_frames(s["in"], rate), on_frames(s["out"], rate)
            end = (durations or {}).get(s.get("clip"))
            if end and b > float(end) + 1e-6:          # the nearest frame past the media
                b = round(math.floor(float(end) * rate + 1e-6) / rate, 4)
            if b - a < 1.0 / rate - 1e-6:
                b = on_frames(a + 1.0 / rate, rate)
            spd = _speed(s)
            if spd != 1.0:
                # retimed: its length in the film whole frames at its speed (the
                # timeline's gridRange), so every cut after it stays on a frame too
                n = max(1, round((b - a) / spd * rate))
                b = round(a + n / rate * spd, 4)
            s["in"], s["out"] = a, b
        was = by_id.get(s.get("id"))
        if was is not None and was.get("controls"):
            s["controls"] = json.loads(json.dumps(was["controls"]))
        segs.append(s)
    out["segments"] = segs
    return out


def polish_kept_alone(plan: dict, clips: dict[str, dict],
                      current: list[dict] | None) -> dict:
    """`polish_plan`, except a shot the model returned exactly as it is in the current
    cut is left exactly as it is. Polish exists to fix cut points the model read off a
    transcript; a range copied verbatim from the cut was the editor's (or an accepted
    earlier plan's), and snapping it is a change nobody asked for — the source of the
    "unchanged" shot that lost 1.34 s."""
    before = [dict(s) for s in plan.get("segments") or []]
    cur = [s for s in (current or []) if isinstance(s, dict) and "in" in s and "out" in s]
    after = polish(before, clips)
    for i, s in enumerate(before):
        if any(_echoed(s, c) and (not s.get("id") or not c.get("id")
                                      or s.get("id") == c.get("id")) for c in cur):
            after[i] = dict(s)
    out = dict(plan)
    out["segments"] = after
    line = summarise(before, after)
    if line:
        notes = (plan.get("notes") or "").strip()
        out["notes"] = f"{notes}\n\n{line}".strip() if notes else line
    return out


def shot_diff(current: list[dict], proposed: list[dict]) -> dict:
    """What a plan does to the cut, shot by shot, from the ranges alone — the machine's
    account the notes are held to. A proposed shot is `kept` (same id or same range,
    identical in and out), `changed` (it continues a current shot — same id — with a
    different range; `was` holds the old one) or `new`; a current shot no proposed shot
    continues is `removed`. Positions are 1-based."""
    by_id = {c.get("id"): (i, c) for i, c in enumerate(current) if c.get("id")}
    claimed: set[int] = set()
    rows = []
    for j, s in enumerate(proposed):
        hit = by_id.get(s.get("id")) if s.get("id") else None
        if hit is None:
            hit = next(((i, c) for i, c in enumerate(current)
                        if i not in claimed and _same_range(s, c)), None)
        row = {"pos": j + 1, "id": s.get("id"), "clip": s.get("clip"),
               "in": round(float(s["in"]), 2), "out": round(float(s["out"]), 2)}
        if hit is None or hit[0] in claimed:
            row["status"] = "new"
        else:
            i, c = hit
            claimed.add(i)
            row["was_pos"] = i + 1
            if _same_range(s, c):
                row["status"] = "kept"
            else:
                row["status"] = "changed"
                row["was"] = {"in": round(float(c["in"]), 2), "out": round(float(c["out"]), 2)}
                row["delta_in"] = round(float(s["in"]) - float(c["in"]), 2)
                row["delta_out"] = round(float(s["out"]) - float(c["out"]), 2)
        rows.append(row)
    removed = [{"pos": i + 1, "id": c.get("id"), "clip": c.get("clip"),
                "in": round(float(c["in"]), 2), "out": round(float(c["out"]), 2),
                "status": "removed"}
               for i, c in enumerate(current) if i not in claimed]
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in ("kept", "changed", "new")}
    counts["removed"] = len(removed)
    return {"shots": rows, "removed": removed, "counts": counts}


# A sentence claims a shot was left alone when it says one of these.
KEPT_CLAIM = re.compile(
    r"\b(unchanged|untouched|as it was|as they were|as-is|as is|left (?:it |them )?alone|"
    r"kept (?:it |them |your |the |that |those |this )?[^.;]*?(?:exactly|intact|as it|"
    r"as they|the same|unchanged|in place)|"
    r"(?:kept|keep|keeping|left|leaving) [^.;]*?(?:exactly|intact|in place)|"
    r"did(?:n't| not) (?:touch|change)|no changes? to|same as before)\b",
    re.IGNORECASE)
# …and covers more than the shots it names when it says one of these.
BROAD_CLAIM = re.compile(
    r"\b(everything else|the rest|all (?:the )?other|otherwise|remaining|"
    r"(?:first|second|last|back|front) half|the ending|the opening)\b", re.IGNORECASE)
SHOT_NUMBER = re.compile(r"\bshots? (\d{1,3})(?:\s*(?:-|–|to|and)\s*(\d{1,3}))?", re.IGNORECASE)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    return [p.strip() for p in parts if p.strip()]


def _clock(t: float) -> str:
    m, s = divmod(max(0.0, float(t)), 60)
    return f"{int(m)}:{s:04.1f}"


def _named_rows(sentence: str, rows: list[dict], n_current: int) -> list[dict]:
    """The rows (of `shot_diff`, current-side positions) a sentence names: by clip
    file name or stem, or by shot number ("shot 3", "shots 4-6"), read as the cut's
    positions before the change."""
    low = sentence.lower()
    out = [r for r in rows if r.get("clip") and (
        str(r["clip"]).lower() in low or Path(str(r["clip"])).stem.lower() in low)]
    for m in SHOT_NUMBER.finditer(sentence):
        a = int(m.group(1))
        b = int(m.group(2) or a)
        for r in rows:
            pos = r.get("was_pos") or (r["pos"] if r.get("status") == "removed" else 0)
            if a <= int(pos) <= b and r not in out:
                out.append(r)
    return out


def check_notes(plan: dict, current: list[dict]) -> dict:
    """Hold the plan's notes to its diff. Every sentence that says a shot was kept or
    left unchanged is checked against the ranges: a named shot (by clip or number) that
    changed or was removed, or — for a broad claim ("the rest is unchanged", "second
    half … unchanged") — any changed shot the notes do not mention elsewhere, makes the
    claim false. False claims are flagged and the notes get one correcting line per
    shot, in numbers; the model's words are kept in `notes_model`.

    Adds `diff` (the `shot_diff`) and `notes_check` {ok, flags: [{claim, shots, why}]}
    to a copy of the plan. A plan with nothing to compare (a first cut) passes."""
    out = dict(plan)
    cur = [s for s in (current or []) if isinstance(s, dict) and "in" in s and "out" in s]
    proposed = [s for s in plan.get("segments") or [] if isinstance(s, dict)]
    diff = shot_diff(cur, proposed)
    out["diff"] = diff
    # the ranges the editor rejected before, which the first pass left out (verdict 4)
    left = (plan.get("firstpass") or {}).get("rejected") or []
    seen_rejected = {"rejected": left} if left else {}
    if not cur:
        out["notes_check"] = {"ok": True, "flags": [], "checked": 0, **seen_rejected}
        out["checks"] = plan_checks(out)
        return out
    changed = [r for r in diff["shots"] if r["status"] == "changed"]
    gone = diff["removed"]
    # current-side rows: what each shot of the cut became
    side = changed + gone
    notes = str(plan.get("notes") or "")
    sentences = _sentences(notes)
    claims = [s for s in sentences if KEPT_CLAIM.search(s)]
    plain = [s for s in sentences if s not in claims]
    mentioned_elsewhere = {id(r) for s in plain for r in _named_rows(s, side, len(cur))}
    flags = []
    for sentence in claims:
        named = _named_rows(sentence, side, len(cur))
        broad = bool(BROAD_CLAIM.search(sentence))
        bad = list(named)
        if broad:
            for r in side:
                if r not in bad and id(r) not in mentioned_elsewhere:
                    bad.append(r)
        if not bad:
            continue
        shots = []
        for r in bad:
            if r["status"] == "removed":
                shots.append({"id": r.get("id"), "clip": r["clip"], "pos": r["pos"],
                              "was": {"in": r["in"], "out": r["out"]}, "now": None})
            else:
                shots.append({"id": r.get("id"), "clip": r["clip"], "pos": r["was_pos"],
                              "was": r["was"], "now": {"in": r["in"], "out": r["out"]},
                              "delta_in": r["delta_in"], "delta_out": r["delta_out"]})
        flags.append({"claim": sentence, "shots": shots,
                      "why": "says kept/unchanged, but the ranges differ"})
    lines = []
    seen: set = set()
    for f in flags:
        for s in f["shots"]:
            key = (s.get("id"), s["clip"], s["pos"])
            if key in seen:
                continue
            seen.add(key)
            stem = clip_name(s["clip"])          # "Black Video", never gen_black_<hash>
            was = f"{_clock(s['was']['in'])}–{_clock(s['was']['out'])}"
            if s["now"] is None:
                lines.append(f"Check: shot {s['pos']} ({stem} {was}) is removed by this "
                             "plan — the notes call it kept.")
            else:
                now = f"{_clock(s['now']['in'])}–{_clock(s['now']['out'])}"
                bits = [f"{k} {v:+.2f} s" for k, v in (("in", s["delta_in"]),
                                                       ("out", s["delta_out"])) if abs(v) >= 0.01]
                lines.append(f"Check: shot {s['pos']} ({stem}) changes {was} → {now} "
                             f"({', '.join(bits)}) — the notes call it unchanged.")
    if lines:
        out["notes_model"] = notes
        out["notes"] = (notes.strip() + "\n\n" + "\n".join(lines)).strip()
    out["notes_check"] = {"ok": not flags, "flags": flags, "checked": len(claims),
                          **seen_rejected}
    out["checks"] = plan_checks(out)
    return out


def _shot_of(segments: list[dict], clip, t_in, t_out, *, exact: bool = False) -> int | None:
    """The proposed shot a range is about: the same range (`exact`), else the shot of
    that clip it overlaps most; None when no shot is."""
    if clip is None or t_in is None or t_out is None:
        return None
    best, most = None, 0.0
    for i, s in enumerate(segments):
        if not isinstance(s, dict) or s.get("clip") != clip or "in" not in s:
            continue
        if exact:
            if abs(float(s["in"]) - t_in) < 0.02 and abs(float(s["out"]) - t_out) < 0.02:
                return i
            continue
        o = _overlap((float(s["in"]), float(s["out"])), (float(t_in), float(t_out)))
        if o > most:
            best, most = i, o
    return best


def plan_checks(plan: dict) -> list[dict]:
    """"Things to check" as data (verdict 5, punch 3): the notes check's flags (`kind`
    flag), the ranges the editor rejected before that this proposal touched (`kind`
    rejected), then the model's own checks (`kind` model) — each `{text, clip, in, out,
    kind, shot}`, `shot` the proposed shot's index it is about or None. Built after
    every pass that moves shots (polish, the frame grid, the first pass's rejections),
    so `shot` points at the plan the board shows."""
    segs = [s for s in plan.get("segments") or [] if isinstance(s, dict)]
    out: list[dict] = []
    seen: set = set()
    for f in (plan.get("notes_check") or {}).get("flags") or []:
        for s in f.get("shots") or []:
            key = (s.get("id"), s.get("clip"), s.get("pos"))
            if key in seen:
                continue
            seen.add(key)
            stem = Path(str(s.get("clip"))).stem
            was = f"{_clock(s['was']['in'])}–{_clock(s['was']['out'])}" if s.get("was") else ""
            if not s.get("now"):
                out.append({"text": f"shot {s.get('pos')} ({stem} {was}) is removed — the notes "
                                    f"say “{f.get('claim', '')}”",
                            "clip": s.get("clip"), "in": (s.get("was") or {}).get("in"),
                            "out": (s.get("was") or {}).get("out"), "kind": "flag", "shot": None})
                continue
            now = s["now"]
            k = next((i for i, p in enumerate(segs) if s.get("id") and p.get("id") == s["id"]), None)
            if k is None:
                k = _shot_of(segs, s.get("clip"), float(now["in"]), float(now["out"]), exact=True)
            out.append({"text": f"shot {s.get('pos')} ({stem}) changes {was} → "
                                f"{_clock(now['in'])}–{_clock(now['out'])} — the notes say "
                                f"“{f.get('claim', '')}”",
                        "clip": s.get("clip"), "in": now["in"], "out": now["out"],
                        "kind": "flag", "shot": k})
    left = (plan.get("firstpass") or {}).get("rejected") \
        or (plan.get("notes_check") or {}).get("rejected") or []
    for r in left if isinstance(left, list) else []:
        if not isinstance(r, dict) or not r.get("clip"):
            continue
        what = f"{Path(str(r['clip'])).stem} {_clock(r['in'])}–{_clock(r['out'])}"
        k = None
        if r.get("action") == "flagged":
            text = f"{what} — you rejected this shot before, and it is proposed again"
            k = _shot_of(segs, r["clip"], float(r["in"]), float(r["out"]), exact=True)
        elif r.get("action") == "kept as it is":
            text = f"{what} — you rejected this change before: left out, the shot stays as it is"
        else:
            text = f"{what} — you rejected this shot before: left out"
        out.append({"text": text, "clip": r["clip"], "in": r["in"], "out": r["out"],
                    "kind": "rejected", "shot": k})
    for c in plan.get("checks") or []:
        if not isinstance(c, dict) or c.get("kind", "model") != "model" or not c.get("text"):
            continue
        out.append({"text": c["text"], "clip": c.get("clip"), "in": c.get("in"),
                    "out": c.get("out"), "kind": "model",
                    "shot": _shot_of(segs, c.get("clip"), c.get("in"), c.get("out"))})
    return out


def originate(clips: dict[str, dict], story: str, note: str = "",
              target: tuple[float, float] = (120.0, 180.0),
              events: list[dict] | None = None,
              on_partial: Callable[[str, str], None] | None = None,
              selects: list[dict] | None = None,
              new_clips: list[str] | None = None) -> dict:
    """Ask for a *first* cut. Same return shape as `propose`.

    Neither `story` nor `note` is required. Intent is the human's half of the loop and
    five minutes of it is the highest-leverage input in the project — but refusing to
    start without it would make the empty timeline a dead end again, which is the exact
    problem this removes. With no brief the model is told to infer one and to say what
    it inferred, so the guess is visible and correctable rather than silent.

    `selects` — the bin (`edl["selects"]`) — turns this from "find the film in 44
    minutes" into "build the film from these moments": heroes are held to by the
    validator, keeps are bounds, the inventory is connective tissue.
    """
    if not clips:
        raise ValueError("no analysed clips to cut from")
    return _ask(build_first_prompt(clips, story, note, target, events, selects, new_clips),
                FIRST_SYSTEM, clips, on_partial, heroes=heroes_of(selects, clips),
                new_clips=new_clips)
