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

from typing import Any, Callable

from . import config
from .boundaries import polish_plan
from .estimate import Estimate, estimate
from .inference import complete
from .progress import milestone

PLAN_SCHEMA = {
    "segments": [{"clip": "GX010495.MP4", "in": 1.6, "out": 13.7,
                  "why": "one short sentence on why this moment, in this place"}],
    "notes": "2-3 sentences: what you changed and why, addressed to the editor",
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


def build_prompt(segments: list[dict], clips: dict[str, dict], story: str,
                 note: str, target: tuple[float, float],
                 events: list[dict] | None = None) -> str:
    current = "\n".join(
        f"  {i + 1:2d}. {s['clip']} {s['in']:.2f}-{s['out']:.2f} "
        f"({s['out'] - s['in']:.1f}s) — {s.get('why', '')}"
        for i, s in enumerate(segments))
    total = sum(s["out"] - s["in"] for s in segments)
    timeline = shot_timeline(clips)
    inventory = "\n\n".join(_clip_block(c, timeline) for c in clips.values())
    # Before the inventory, always. A ranked list that arrives after forty clip blocks
    # is a footnote; the whole point is that it is read first.
    ranked = events_section(events or [])
    ranked = f"{ranked}\n\n" if ranked else ""

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

{ranked}## Every clip available, with its transcript
{inventory}

## What to return
The full revised edit as an ordered list of segments — not a diff, not only the parts
you changed. Keep what works; the note tells you what to change. Timestamps are
seconds within the named clip. Prefer cutting on utterance boundaries visible in the
transcripts above. Each clip says when it was recorded relative to the others — use it
as information rather than as a rule. Deliberate reordering is good editing; only the
accidental kind, drifting backwards through one continuous stretch for no reason,
reads as a mistake. Say in the `why` when a move is deliberate."""


def build_first_prompt(clips: dict[str, dict], story: str, note: str,
                       target: tuple[float, float],
                       events: list[dict] | None = None) -> str:
    """The originating prompt: no current edit, so the material and the brief carry it."""
    timeline = shot_timeline(clips)
    inventory = "\n\n".join(_clip_block(c, timeline) for c in clips.values())
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

{ranked}## Every clip available, with its transcript
{inventory}

## What to return
A complete first cut as an ordered list of segments — the order they should play in,
not the order the clips were shot. Timestamps are seconds within the named clip. Cut
on utterance boundaries visible in the transcripts above. Every segment needs a `why`:
one sentence on what that moment is for. In `notes`, say what you decided this film is
about and what you would look at first if it is wrong."""


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
                      target: tuple[float, float]) -> str:
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
{block}

## What to return
The replacement for this one shot only, as `segments` — usually one segment, more if
the note asks to split it. Every segment must come from {clip}; the rest of the edit
is not yours to change here. Timestamps are seconds within the clip. Prefer cutting on
utterance boundaries visible in the transcript, and mind what plays before and after
this shot. If the note would be best served by removing the shot entirely, return it
unchanged and say so in `notes` — removing is the editor's own one-click action."""


def propose_shot(segments: list[dict], index: int, clips: dict[str, dict],
                 story: str, note: str,
                 target: tuple[float, float] = (120.0, 180.0),
                 on_partial: Callable[[str, str], None] | None = None) -> dict:
    """Ask for a revision of one shot. Returns {'segments', 'notes', 'usage'} where
    `segments` is the replacement for that shot alone — the caller splices."""
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
    return _ask(build_shot_prompt(segments, index, clips, story, note, target),
                SHOT_SYSTEM, scoped, on_partial)


def validate_plan(payload: Any, clips: dict[str, dict]) -> dict:
    """Strict. A plausible-looking plan that names a clip we do not have, or runs
    past the end of one, is worse than a loud failure — it renders as a crash or,
    worse, as silently missing footage."""
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
    return {"segments": clean, "notes": str(payload.get("notes", "")).strip()[:1200]}


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
         on_partial: Callable[[str, str], None] | None = None) -> dict:
    result = complete(
        prompt, role=config.ROLE_SKELETON, schema=PLAN_SCHEMA, system=system,
        validate=lambda payload: validate_plan(payload, clips), retries=1,
        on_partial=on_partial)
    # Boundary polish runs on the *validated* plan, so it can assume in/out are
    # real numbers inside a real clip and worry only about where they land in the
    # speech. Both callers get it: a first cut has the same clipped words as a
    # revision, and asking the model to be more careful about a timestamp it read
    # off a transcript is asking it to do arithmetic the sidecar already knows.
    plan = polish_plan(result.content, clips)
    plan["usage"] = {
        "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
        "projected_usd": result.projected_usd, "model": result.model,
        "backend": result.backend, "latency_ms": result.latency_ms,
    }
    return plan


def propose(segments: list[dict], clips: dict[str, dict], story: str, note: str,
            target: tuple[float, float] = (120.0, 180.0),
            events: list[dict] | None = None,
            on_partial: Callable[[str, str], None] | None = None) -> dict:
    """Ask for a revision. Returns {'segments', 'notes', 'usage'}."""
    if not note.strip():
        raise ValueError("empty note")
    return _ask(build_prompt(segments, clips, story, note, target, events),
                SYSTEM, clips, on_partial)


def originate(clips: dict[str, dict], story: str, note: str = "",
              target: tuple[float, float] = (120.0, 180.0),
              events: list[dict] | None = None,
              on_partial: Callable[[str, str], None] | None = None) -> dict:
    """Ask for a *first* cut. Same return shape as `propose`.

    Neither `story` nor `note` is required. Intent is the human's half of the loop and
    five minutes of it is the highest-leverage input in the project — but refusing to
    start without it would make the empty timeline a dead end again, which is the exact
    problem this removes. With no brief the model is told to infer one and to say what
    it inferred, so the guess is visible and correctable rather than silent.
    """
    if not clips:
        raise ValueError("no analysed clips to cut from")
    return _ask(build_first_prompt(clips, story, note, target, events),
                FIRST_SYSTEM, clips, on_partial)
