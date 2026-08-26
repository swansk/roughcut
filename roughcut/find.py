"""Find a moment in the bin from a plain-language description.

The board can play a cut and revise a cut, but "where is the bit where I hit the
rocks?" had no answer short of scrubbing twelve clips. This is that answer, in two
layers with very different prices:

  * **`lexical`** — free and instant. Word-level matching of the query against every
    transcript utterance and every visual-pass moment, scored and merged into
    windows. It finds anything that was *said* or *seen* in nearly the words the
    editor used, which on this footage is most things (the words are the strongest
    signal in the bin — R8, and every cut since).
  * **`find`** — one model call over the same inventory the Ask reads. It exists for
    the misses lexical matching cannot close: a "river" said as "stream", a "jump"
    the frames call "person airborne". It costs a call, so the board offers it with
    a price and the human clicks — the same rule as the visual pass.

Both return windows, not cut points: a match is somewhere to *look*, played from the
full clip so the editor can scrub around it, and only becomes a segment when they add
it. Nothing here writes anything.
"""

from __future__ import annotations

from typing import Any, Callable

from . import config
from .inference import complete
from .revise import _clip_block, events_section

# Words that carry no evidence about *which* moment is meant. Small and honest rather
# than a real stopword list: "where I hit the rocks" must match on hit and rocks, not
# on where and the.
STOPWORDS = {
    "a", "an", "the", "i", "im", "me", "my", "we", "our", "us", "you", "your",
    "he", "she", "it", "its", "they", "them", "his", "her", "their",
    "is", "are", "was", "were", "be", "been", "am", "do", "does", "did",
    "and", "or", "but", "of", "in", "on", "at", "to", "into", "with", "for",
    "that", "this", "these", "those", "there", "here", "when", "where", "which",
    "what", "who", "how", "then", "than", "some", "one", "part", "bit", "thing",
    "moment", "clip", "video", "footage", "scene", "shot", "find", "show", "get",
    "like", "just", "really", "very", "so", "up", "down", "out", "off",
}

# A prefix match needs this much stem to mean anything: "rock"/"rocks" and
# "fall"/"falling" should match, "so"/"something" should not.
MIN_STEM = 4

# Windows on the same clip closer than this play as one moment, not two.
MERGE_GAP_S = 3.0

# Air around a matched utterance, so the playback lands a beat before the line.
PAD_HEAD_S = 0.5
PAD_TAIL_S = 0.5

MAX_LEXICAL = 20


def _tokens(text: str) -> list[str]:
    out = []
    for raw in str(text).lower().split():
        w = "".join(c for c in raw if c.isalnum())
        if w and w not in STOPWORDS:
            out.append(w)
    return out


def _matched(query_tokens: list[str], text: str) -> set[str]:
    """Which query tokens this text answers for. Equality, or a prefix either way
    once the stem is long enough — the cheap kind of stemming, and enough for
    rock/rocks and fall/falling without a dependency."""
    words = set(_tokens(text))
    hit: set[str] = set()
    for q in query_tokens:
        for w in words:
            if q == w:
                hit.add(q)
                break
            short, long_ = (q, w) if len(q) <= len(w) else (w, q)
            if len(short) >= MIN_STEM and long_.startswith(short):
                hit.add(q)
                break
    return hit


def lexical(query: str, clips: dict[str, dict]) -> list[dict]:
    """Free matches: the query against what was said and what was seen.

    Returns windows sorted best-first: `{clip, start, end, what, why, source,
    score}` plus `kind`/`notable` when a visual moment is part of the match.
    """
    q = _tokens(query)
    if not q:
        return []
    hits: list[dict] = []
    for clip, c in clips.items():
        duration = float(c.get("duration") or 0.0)

        def window(lo: float, hi: float) -> tuple[float, float]:
            return (max(0.0, lo), min(duration or hi, hi) if duration else hi)

        for u in c.get("transcript") or []:
            got = _matched(q, u.get("text", ""))
            if not got:
                continue
            lo, hi = window(float(u["start"]) - PAD_HEAD_S,
                            float(u["end"]) + PAD_TAIL_S)
            hits.append({"clip": clip, "start": lo, "end": hi,
                         "what": u.get("text", "").strip(),
                         "why": "said here", "source": "heard",
                         "score": len(got) / len(q), "tokens": got})
        for m in (c.get("visual") or {}).get("moments") or []:
            got = _matched(q, f"{m.get('kind', '')} {m.get('what', '')}")
            if not got:
                continue
            lo, hi = window(float(m["start"]), float(m["end"]))
            hits.append({"clip": clip, "start": lo, "end": hi,
                         "what": str(m.get("what", "")).strip(),
                         "why": "seen in the frames", "source": "seen",
                         "kind": m.get("kind", ""),
                         "notable": bool(m.get("notable")),
                         "score": len(got) / len(q)
                         + (0.25 if m.get("notable") else 0.0),
                         "tokens": got})

    # One moment, one row: a line and the frames that show it land within seconds of
    # each other and should reinforce a match rather than crowd the list with twins.
    hits.sort(key=lambda h: (h["clip"], h["start"]))
    merged: list[dict] = []
    for h in hits:
        last = merged[-1] if merged else None
        if last and last["clip"] == h["clip"] and h["start"] - last["end"] <= MERGE_GAP_S:
            last["end"] = max(last["end"], h["end"])
            if h["what"] and h["what"] not in last["what"]:
                last["what"] = f"{last['what']} · {h['what']}"
            if h["source"] not in last["source"]:
                last["source"] = f"{last['source']}+{h['source']}"
                last["why"] = "said here, and seen in the frames"
            combined = last["tokens"] | h["tokens"]
            # A window whose halves answer *different* words of the query is a better
            # match than either half claims alone — "hit" said here, "rocks" seen here.
            covers_more = len(combined) > max(len(last["tokens"]), len(h["tokens"]))
            last["tokens"] = combined
            last["score"] = max(last["score"], h["score"]) + (0.15 if covers_more else 0.0)
            for k in ("kind", "notable"):
                if k in h and k not in last:
                    last[k] = h[k]
        else:
            merged.append(dict(h))
    for row in merged:
        row.pop("tokens", None)
        row["score"] = round(row["score"], 3)
        row["start"] = round(row["start"], 2)
        row["end"] = round(row["end"], 2)
    merged.sort(key=lambda r: (-r["score"], r["clip"], r["start"]))
    return merged[:MAX_LEXICAL]


# --------------------------------------------------------------- the model layer

MATCHES_SCHEMA = {
    "matches": [{"clip": "GX010495.MP4", "start": 61.0, "end": 68.5,
                 "what": "one short sentence: what is there",
                 "why": "the evidence — quote the line or the seen moment"}],
    "notes": "one sentence: how you searched, or what to try instead",
}

SYSTEM = (
    "You help a film editor find a specific moment in one bin of raw footage. You "
    "are given every clip's transcript and, where anyone has looked, what is "
    "visible in sampled frames. You return the places most likely to be what the "
    "editor describes, as JSON. You never invent clips or timestamps: every match "
    "must name a clip from the inventory and lie inside that clip's duration.")

MAX_MATCHES = 12


def build_prompt(query: str, clips: dict[str, dict],
                 events: list[dict] | None = None) -> str:
    inventory = "\n\n".join(_clip_block(c) for c in clips.values())
    ranked = events_section(events or [])
    ranked = f"{ranked}\n\n" if ranked else ""
    return f"""The editor is looking for a specific moment in one bin of raw footage.

## What they are looking for
{query.strip()}

## How to search
The transcripts record what people SAID — often minutes before or after the thing
they describe actually happened — and the "what is visible" lines record what a
sampled frame showed. The moment may be narrated, visible, both, or neither. Prefer
windows where the words and the frames agree; return a strong single-source guess
over nothing. People name things loosely: a stream gets called a river, a jump a
"hit", a crash a "yard sale". Match the meaning, not the words.

{ranked}## Every clip available, with its transcript
{inventory}

## What to return
Up to 8 matches, most likely first. Each names its clip and bounds just the moment
with `start` and `end` (seconds within that clip — a couple of seconds of air either
side is fine), says in `what` what is there, and in `why` which evidence made you
pick it, quoting the line or the seen moment. An empty list means nothing plausible;
either way `notes` is one sentence on how you searched or what to try instead."""


def validate_matches(payload: Any, clips: dict[str, dict]) -> dict:
    """Strict for the same reason `validate_plan` is — a match naming a clip we do
    not have, or seconds past the end of one, hands the player a broken window.
    Unlike a plan, an empty list is a valid answer: "not found" is information."""
    if not isinstance(payload, dict) or "matches" not in payload:
        raise ValueError("expected an object with a 'matches' list")
    raw = payload["matches"]
    if not isinstance(raw, list):
        raise ValueError("'matches' must be a list")
    clean: list[dict] = []
    for i, m in enumerate(raw[:MAX_MATCHES]):
        if not isinstance(m, dict):
            raise ValueError(f"match {i} is not an object")
        clip = m.get("clip")
        if clip not in clips:
            raise ValueError(f"match {i} names unknown clip {clip!r}")
        try:
            start, end = float(m["start"]), float(m["end"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"match {i} has non-numeric start/end") from None
        duration = float(clips[clip]["duration"])
        if not (0.0 <= start < end <= duration + 0.05):
            raise ValueError(
                f"match {i} range {start}-{end} outside {clip} (0-{duration:.1f})")
        clean.append({"clip": clip, "start": round(start, 2),
                      "end": round(min(end, duration), 2),
                      "what": str(m.get("what", "")).strip()[:200],
                      "why": str(m.get("why", "")).strip()[:300],
                      "source": "model"})
    return {"matches": clean, "notes": str(payload.get("notes", "")).strip()[:400]}


def projected_usd(clips: dict[str, dict]) -> float:
    """What one model search over this bin should cost — shown on the button, the
    same courtesy the visual pass gets.

    Calibrated against the one live run there is: Killington (12 clips, all seen)
    billed 54.4k in / 2.1k out — the CLI backend's ~18k-token harness overhead is
    most of the gap between the prompt's own size and the bill, and a clip with a
    visual pass carries far more than `estimate_ask`'s 400-token guess. The first
    guess quoted $0.05 against a $0.19 call, which is the wrong side of honest.
    """
    lines = sum(len(c.get("transcript") or []) for c in clips.values())
    seen = sum(1 for c in clips.values() if (c.get("visual") or {}).get("moments"))
    tokens_in = lines * 12 + len(clips) * 120 + seen * 1200 + 18000 + 600
    pin, pout = config.price_per_mtok(config.model_for(config.ROLE_JUDGE))
    return round(tokens_in / 1e6 * pin + 2000 / 1e6 * pout, 2)


def find(query: str, clips: dict[str, dict], events: list[dict] | None = None,
         on_partial: Callable[[str, str], None] | None = None) -> dict:
    """One call. Returns {'matches', 'notes', 'usage'} — validated, never written.

    The judge role, not the skeleton: this is reading the bin's evidence and judging
    relevance, not composing an edit, and it should stay cheap enough that searching
    never feels like spending.
    """
    if not query.strip():
        raise ValueError("empty query")
    if not clips:
        raise ValueError("no analysed clips to search")
    result = complete(
        build_prompt(query, clips, events), role=config.ROLE_JUDGE,
        schema=MATCHES_SCHEMA, system=SYSTEM,
        validate=lambda payload: validate_matches(payload, clips), retries=1,
        on_partial=on_partial)
    found = result.content
    found["usage"] = {
        "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
        "projected_usd": result.projected_usd, "model": result.model,
        "backend": result.backend, "latency_ms": result.latency_ms,
    }
    return found
