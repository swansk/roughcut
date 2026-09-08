"""Themes proposed from the transcripts — the listen-first step of the intake.

The design (docs/design/cutting-room-floor.html §2): the audio pass is free and fast, and
the transcripts alone have found the spine of every bin so far, so the machine proposes
what the film might be about *before* the priced look pass runs — chips with clip counts
the editor confirms, edits or ignores. Themes are a brief and a filter, never a score
(INTAKE Decisions): `picks.build` tags and lifts picks that match them, the journal's
priority reads `theme_hits`, and changing them later is free because the sheets never
saw them.

It also proposes the names it hears, because a note dictated on the floor survives
"Spenny" only if the recogniser was told to expect it (M4).

One cheap call on the judge role; validated like a plan; nothing here writes the EDL.
"""

from __future__ import annotations

from typing import Any, Callable

from . import config
from .inference import complete

PROPOSAL_SCHEMA = {
    "themes": [{"theme": "three to six words, the way the editor would say it",
                "why": "one sentence: what recurs and where",
                "clips": ["GX010495.MP4"],
                "lines": ["a short quoted line that shows it"]}],
    "names": ["people the transcripts call by name"],
    "notes": "one sentence on what the film seems to be about",
}

SYSTEM = (
    "You help a film editor decide what a short film from one bin of raw footage might be "
    "about, from the transcripts alone. You look for what recurs across clips — a running "
    "joke, a phrase, a dare and its payoff, a place, a person who keeps appearing — and "
    "propose it as themes the editor can confirm with one click. You answer with JSON. "
    "You never invent clips: every clip you cite must be in the inventory.")

MAX_THEMES = 8
MAX_NAMES = 12
MAX_LINES_PER_CLIP = 60      # a 5-minute helmet-mic clip is ~40 lines; cap the long ones


def _clip_block(clip: dict) -> str:
    lines = [f"### {clip['clip']}  (duration {float(clip.get('duration') or 0):.0f}s)"]
    transcript = clip.get("transcript") or []
    if not transcript:
        lines.append("  (no speech)")
    for u in transcript[:MAX_LINES_PER_CLIP]:
        lines.append(f"  {float(u.get('start', 0)):.1f}  {str(u.get('text', '')).strip()}")
    if len(transcript) > MAX_LINES_PER_CLIP:
        lines.append(f"  … {len(transcript) - MAX_LINES_PER_CLIP} more lines")
    return "\n".join(lines)


def build_prompt(clips: dict[str, dict], story: str = "") -> str:
    inventory = "\n\n".join(_clip_block(c) for c in clips.values())
    brief = story.strip()
    return f"""The editor has a bin of {len(clips)} clips and has not yet said what the film is \
about{' beyond this: ' + brief if brief else ''}. Nothing has been looked at; these are the \
transcripts.

## What to propose
Between three and {MAX_THEMES} **themes** — things that recur, in the order you would show
them to the editor. A theme is a phrase the editor would recognise ("hitting rocks", "the
milk joke", "look at the cinematography"), not a category ("skiing"). For each: which clips
carry it (only clips listed below), one line quoted as evidence, and one sentence on why it
might be the spine. Prefer things that appear in more than one clip; a single great line
can be a theme only if it sounds like a payoff.

Also list the **names** people are called by in these transcripts (first names, nicknames —
"Spenny", "Eric"), so a note the editor speaks later is recognised. Skip words that are not
names.

## Every clip, with its transcript
{inventory}

## What to return
JSON with `themes` (each: theme, why, clips, lines), `names`, and `notes` — one sentence on
what this film seems to be about. No theme may cite a clip that is not above."""


def validate_proposal(payload: Any, clips: dict[str, dict]) -> dict:
    """Strict: a theme citing a clip that is not in the bin is a bad theme."""
    if not isinstance(payload, dict) or not isinstance(payload.get("themes"), list):
        raise ValueError("expected an object with a 'themes' list")
    themes: list[dict] = []
    seen: set[str] = set()
    for i, t in enumerate(payload["themes"][:MAX_THEMES]):
        if not isinstance(t, dict):
            raise ValueError(f"theme {i} is not an object")
        name = str(t.get("theme", "")).strip()[:60]
        if not name or name.lower() in seen:
            continue
        cited = [str(c) for c in (t.get("clips") or []) if isinstance(c, str)]
        bad = [c for c in cited if c not in clips]
        if bad:
            raise ValueError(f"theme {i} ({name!r}) cites unknown clip {bad[0]!r}")
        seen.add(name.lower())
        themes.append({
            "theme": name,
            "why": str(t.get("why", "")).strip()[:240],
            "clips": sorted(set(cited)),
            "lines": [str(x).strip()[:160] for x in (t.get("lines") or [])
                      if isinstance(x, str)][:4],
        })
    if not themes:
        raise ValueError("no usable theme proposed")
    names = []
    for n in (payload.get("names") or [])[:MAX_NAMES]:
        s = str(n).replace(",", " ").strip()[:30]
        if s and s.lower() not in {x.lower() for x in names}:
            names.append(s)
    return {"themes": themes, "names": names,
            "notes": str(payload.get("notes", "")).strip()[:300]}


def projected_usd(clips: dict[str, dict]) -> float:
    """Transcripts only — no sheets, no moments.

    Calibrated against the one live run there is: Killington (12 clips, 323 capped
    lines) billed 42.8k in / 4.4k out for a projected $0.09 against a $0.20 call — the
    wrong side of honest. The judge role's harness overhead is nearer 36k tokens than
    the 18k the finder sees, and the answer (six themes with quoted lines, twelve
    names, a sentence) is four times the 1.2k first guessed."""
    lines = sum(min(len(c.get("transcript") or []), MAX_LINES_PER_CLIP)
                for c in clips.values())
    tokens_in = lines * 20 + len(clips) * 60 + 36000
    pin, pout = config.price_per_mtok(config.model_for(config.ROLE_JUDGE))
    return round(tokens_in / 1e6 * pin + 4400 / 1e6 * pout, 2)


def propose(clips: dict[str, dict], story: str = "",
            on_partial: Callable[[str, str], None] | None = None) -> dict:
    """One call. Returns {'themes', 'names', 'notes', 'usage'} — never written."""
    if not clips:
        raise ValueError("no analysed clips to read")
    result = complete(
        build_prompt(clips, story), role=config.ROLE_JUDGE,
        schema=PROPOSAL_SCHEMA, system=SYSTEM,
        validate=lambda payload: validate_proposal(payload, clips), retries=1,
        on_partial=on_partial)
    out = result.content
    out["usage"] = {
        "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
        "projected_usd": result.projected_usd, "model": result.model,
        "backend": result.backend, "latency_ms": result.latency_ms,
    }
    return out
