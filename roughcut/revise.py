"""Turn a plain-language note into a revised segment list.

This is the "interject" half of the human-in-the-loop map. The board already lets a
human steer by hand; this lets them steer by *asking* — "tighten the intro", "more
skiing, less airport", "the milk is the running joke, build around it".

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

from typing import Any

from . import config
from .inference import complete

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


def _clip_block(clip: dict) -> str:
    lines = [f"### {clip['clip']}  (duration {clip['duration']:.1f}s)"]
    s = clip.get("summary") or {}
    if s:
        lines.append(
            f"speech_fraction={s.get('speech_fraction')} "
            f"wind={s.get('wind_dominant_fraction')} usable={s.get('audio_usable')}")
    if clip.get("transcript"):
        lines.append("transcript:")
        lines += [f"  {u['start']:.1f}-{u['end']:.1f}  {u['text']}"
                  for u in clip["transcript"]]
    else:
        lines.append("transcript: (no speech)")
    return "\n".join(lines)


def build_prompt(segments: list[dict], clips: dict[str, dict], story: str,
                 note: str, target: tuple[float, float]) -> str:
    current = "\n".join(
        f"  {i + 1:2d}. {s['clip']} {s['in']:.2f}-{s['out']:.2f} "
        f"({s['out'] - s['in']:.1f}s) — {s.get('why', '')}"
        for i, s in enumerate(segments))
    total = sum(s["out"] - s["in"] for s in segments)
    inventory = "\n\n".join(_clip_block(c) for c in clips.values())

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

## Every clip available, with its transcript
{inventory}

## What to return
The full revised edit as an ordered list of segments — not a diff, not only the parts
you changed. Keep what works; the note tells you what to change. Timestamps are
seconds within the named clip. Prefer cutting on utterance boundaries visible in the
transcripts above."""


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


def propose(segments: list[dict], clips: dict[str, dict], story: str, note: str,
            target: tuple[float, float] = (120.0, 180.0)) -> dict:
    """Ask for a revision. Returns {'segments', 'notes', 'usage'}."""
    if not note.strip():
        raise ValueError("empty note")
    prompt = build_prompt(segments, clips, story, note, target)
    result = complete(
        prompt, role=config.ROLE_SKELETON, schema=PLAN_SCHEMA, system=SYSTEM,
        validate=lambda payload: validate_plan(payload, clips), retries=1)
    plan = result.content
    plan["usage"] = {
        "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
        "projected_usd": result.projected_usd, "model": result.model,
        "backend": result.backend, "latency_ms": result.latency_ms,
    }
    return plan
