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

Wave 20 (FIND3) adds two more, both optional and both degrading to what is above:

  * **union** — the local 3080's PE-Core search (`localvlm.search`: text against 2 fps
    frame embeddings) fused with the lexical rows: overlapping windows merge, each
    source's evidence stays on the row, the windows tighten to the scoring frames, and
    the fused score is a reciprocal-rank sum, so a moment both sources like beats one
    only either does. With no local stack it is exactly the lexical list.
  * **rerank** — one Claude call over the union's top ~10 (text + up to 6 small
    thumbnails). Priced first; on by default only in Claude only / Hybrid, never in
    Local only.

Both return windows, not cut points: a match is somewhere to *look*, played from the
full clip so the editor can scrub around it, and only becomes a segment when they add
it. Nothing here writes anything.
"""

from __future__ import annotations

from typing import Any, Callable

from pathlib import Path

from . import config, speakers
from . import telemetry_events
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
    "someone", "somebody", "anyone", "anybody", "everyone", "everybody", "something",
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

# The floors under which a hit is a guess, not a match (wave 23, FINDER-12). Chosen from the
# Copper finder trial's 13 real and 3 absent queries (run against a copy of her work dir):
#   LOCAL_FLOOR   the 3080's PE-Core cosine for a window. The three queries for things that
#                 are not in the footage (a dog, a hot tub, a wipe-out) topped out at 0.165,
#                 0.153 and 0.196; the lowest score of a window the editor audit calls right
#                 and that nothing else found was 0.224. 0.21 sits between.
#   LEXICAL_FLOOR the share of the query's words (weighted by how rare each is in this bin)
#                 a spoken line or a described moment must answer for. The absent queries'
#                 best word hit was 0.392 ("the wearer's shadow falls across the snow" for
#                 "someone wipes out and falls"); the lowest sole evidence for a real moment
#                 was 0.441 (the one clear jump, an unchecked claim). A thin margin: one bin.
LOCAL_FLOOR = 0.21
LEXICAL_FLOOR = 0.42
# A line that says every word of the query outranks a window that only looks like it.
SPOKEN_BONUS = 0.6


def _tokens(text: str) -> list[str]:
    out = []
    for raw in str(text).lower().split():
        w = "".join(c for c in raw if c.isalnum())
        if w and w not in STOPWORDS:
            out.append(w)
    return out


NEGATORS = {"no", "not", "nor", "never", "without", "isnt", "arent", "wasnt", "dont", "doesnt",
            "neither", "nobody", "nothing", "none"}


def _affirmed_tokens(text: str) -> list[str]:
    """`_tokens`, minus the words a negation just ahead of them takes back: "no jumps,
    falls or crashes" and "this is the camera being moved, not a fall" are the look saying
    the thing is NOT there, and a search for "falls" must not be answered by them. A
    negator reaches the next three words, and a comma or full stop ends its reach."""
    out: list[str] = []
    reach = 0
    for raw in str(text).lower().replace("\u2019", "").replace("'", "").split():
        w = "".join(c for c in raw if c.isalnum())
        if w in NEGATORS:
            reach = 3
        elif w in ("but", "however", "though", "although", "yet"):
            reach = 0
        elif w and w not in STOPWORDS:
            if reach <= 0:
                out.append(w)
            reach -= 1                      # a stopword ("or", "a") does not use up the reach
        if raw[-1:] in ".;:!?":
            reach = 0
        elif raw[-1:] == "," and reach > 0:
            reach = max(reach, 2)           # "no jumps, falls or crashes": the list is the negation
    return out


def _root(w: str) -> str:
    """A crude English root: drinking / drinks / drink, jumped / jumps / jump."""
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def _matched(query_tokens: list[str], text: str, *, denies: bool = False) -> set[str]:
    """Which query tokens this text answers for. Equality, or a prefix either way
    once the stem is long enough — the cheap kind of stemming, and enough for
    rock/rocks and fall/falling without a dependency. With `denies` (what the look and the
    sensors wrote about a picture), a word the text denies ("not a fall") does not count;
    speech is not read that way ("no way, I fell in the stream" is a fall)."""
    words = set(_affirmed_tokens(text) if denies else _tokens(text))
    hit: set[str] = set()
    for q in query_tokens:
        for w in words:
            if q == w or (min(len(q), len(w)) >= MIN_STEM + 1 and _root(q) == _root(w)):
                hit.add(q)
                break
            short, long_ = (q, w) if len(q) <= len(w) else (w, q)
            if len(short) >= MIN_STEM and long_.startswith(short):
                hit.add(q)
                break
    return hit


def _clip_text(c: dict) -> tuple[str, str]:
    """(what was said, what was seen and written about it) in a clip: the second is read with
    its denials taken out ("no jumps, falls or crashes" does not make a clip a jumping clip)."""
    vis = c.get("visual") or {}
    seen = " ".join([str(vis.get("description") or vis.get("summary") or "")]
                    + [str(m.get("what", "")) for m in vis.get("moments") or []]
                    + [str(n.get("text", "")) for n in c.get("notes") or []])
    return " ".join(str(u.get("text", "")) for u in c.get("transcript") or []), seen


def _weights(q: list[str], clips: dict[str, dict]) -> dict[str, float]:
    """How much each query word says, from how many clips of THIS bin it appears in: "snow"
    on a ski trip is in nearly every clip and says nothing about which moment is meant,
    "Walmart" is in one. 1 minus the share of clips that carry the word, never below 0.05,
    so a query of only common words still ranks (and falls under the floor, not out of the
    arithmetic). A bin of one or two clips has nothing to compare: every word weighs 1."""
    n = len(clips)
    if n < 3:
        return {t: 1.0 for t in q}
    texts = [_clip_text(c) for c in clips.values()]
    out = {}
    for t in q:
        df = sum(1 for said, seen in texts if _matched([t], said) or _matched([t], seen, denies=True))
        out[t] = max(0.05, 1.0 - df / n)
    return out


def _unusable_share(c: dict, lo: float, hi: float) -> float:
    """How much of [lo, hi] the look called unusable (black, covered, unreadable)."""
    span = max(hi - lo, 1e-6)
    got = 0.0
    for u in (c.get("visual") or {}).get("unusable") or []:
        try:
            got += max(0.0, min(hi, float(u["end"])) - max(lo, float(u["start"])))
        except (KeyError, TypeError, ValueError):
            continue
    return min(1.0, got / span)


# A query word that names the moment's own KIND ("jump", "fall") is firmer evidence than the
# same word somewhere in its prose ("the wearer's shadow falls across the snow").
PROSE_WEIGHT = 0.8


def _moment_match(q: list[str], m: dict, cover: Callable[[set], float]) -> tuple[set[str], float]:
    by_kind = _matched(q, str(m.get("kind", "")))
    by_prose = _matched(q, str(m.get("what", "")), denies=True) - by_kind
    return by_kind | by_prose, cover(by_kind) + PROSE_WEIGHT * cover(by_prose)


def lexical(query: str, clips: dict[str, dict], *, floor: float = 0.0) -> list[dict]:
    """Free matches: the query against what was said and what was seen.

    Returns windows sorted best-first: `{clip, start, end, what, why, source,
    score, base}` plus `kind`/`notable` when a visual moment is part of the match. `base`
    is the share of the query the window answers for (without the notable bonus); rows under
    `floor` are left out.
    """
    q = _tokens(query)
    if not q:
        return []
    weight = _weights(q, clips)
    total = sum(weight[t] for t in q) or 1.0

    def cover(got: set[str]) -> float:
        return sum(weight[t] for t in got) / total

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
                         "why": ("said here" if not speakers.who(u)
                                 else f"said here by {speakers.who(u)}"),
                         "source": "heard", "speaker": u.get("speaker"),
                         "score": len(got) / len(q), "base": cover(got), "tokens": got})
        # A jump / fall / crash the frames claim but nothing has checked is held back from the
        # board and the planner (roughcut/verify.py) - and it was held back from Find too, so the
        # one clear jump on the Copper trip was not among the eight hits for "someone jumps off a
        # kicker" (FINDER-13). Find only says where to LOOK, so those claims are searched, a
        # little under the checked ones and labelled as unchecked.
        vis_c = c.get("visual") or {}
        seen_rows = [(m, False) for m in vis_c.get("moments") or []]
        seen_rows += [(m, True) for m in vis_c.get("unsupported") or []]
        for m, unchecked in seen_rows:
            got, strength = _moment_match(q, m, cover)
            if not got:
                continue
            lo, hi = window(float(m["start"]), float(m["end"]))
            hits.append({"clip": clip, "start": lo, "end": hi,
                         "what": str(m.get("what", "")).strip(),
                         "why": "seen in the frames (not checked yet)" if unchecked else "seen in the frames",
                         "source": "seen",
                         "kind": m.get("kind", ""),
                         "notable": bool(m.get("notable")),
                         "score": len(got) / len(q) * (0.9 if unchecked else 1.0)
                         + (0.25 if m.get("notable") else 0.0),
                         "base": strength * (0.9 if unchecked else 1.0), "tokens": got})

        # What the camera felt (wave 20): the same words the planner reads, so "where did I
        # jump" and "where did I stop" find the sensor's airs and stops with no model call.
        for m in c.get("felt_rows") if "felt_rows" in c else telemetry_events.felt_words(clip):
            got, strength = _moment_match(q, m, cover)
            if not got:
                continue
            lo, hi = window(float(m["start"]) - PAD_HEAD_S, float(m["end"]) + PAD_TAIL_S)
            if _unusable_share(c, lo, hi) >= 0.5:
                continue          # a bump in a clip the look found black: nothing there to look at
            hits.append({"clip": clip, "start": lo, "end": hi,
                         "what": str(m.get("what", "")).strip(),
                         "why": "felt by the camera", "source": "felt",
                         "kind": m.get("kind", ""), "notable": bool(m.get("notable")),
                         "score": len(got) / len(q) * 0.9 + (0.25 if m.get("notable") else 0.0),
                         "base": strength * 0.9, "tokens": got})

        # What the project itself wrote about its moments (wave 23): the first pass's picks and
        # the cut's shot reasons ("he raises the jug and drinks once more"). They are the best text
        # there is about a moment, and the Copper editor's own cut described the jug while Find
        # said there was none. `c["notes"]` = [{start, end, text, kind}], built by the server.
        for n in c.get("notes") or []:
            got = _matched(q, str(n.get("text", "")), denies=True)
            if not got:
                continue
            lo, hi = window(float(n["start"]), float(n["end"]))
            hits.append({"clip": clip, "start": lo, "end": hi, "what": str(n.get("text", "")).strip()[:200],
                         "why": "the cut's own note" if n.get("kind") == "cut" else "the first pass's note",
                         "source": "noted", "score": len(got) / len(q) * 0.9,
                         "base": cover(got), "tokens": got})

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
                said = last["why"] if last["why"].startswith("said here") else h["why"]
                last["why"] = f"{said}, and seen in the frames"
            combined = last["tokens"] | h["tokens"]
            # A window whose halves answer *different* words of the query is a better
            # match than either half claims alone — "hit" said here, "rocks" seen here.
            covers_more = len(combined) > max(len(last["tokens"]), len(h["tokens"]))
            last["tokens"] = combined
            last["score"] = max(last["score"], h["score"]) + (0.15 if covers_more else 0.0)
            last["base"] = max(last["base"], h["base"]) + (0.15 if covers_more else 0.0)
            for k in ("kind", "notable"):
                if k in h and k not in last:
                    last[k] = h[k]
        else:
            merged.append(dict(h))
    # What the clip IS (the look's description, or the editor's own words): clip-level
    # evidence. It lifts the clip's moments a little, and a clip that nothing else matched is
    # offered whole, so "this is a chairlift ride" typed once makes "chairlift" find it.
    for clip, c in clips.items():
        vis = c.get("visual") or {}
        text = str(vis.get("description") or vis.get("summary") or "")
        got = _matched(q, text, denies=True) if text else set()
        if not got:
            continue
        mine = [r for r in merged if r["clip"] == clip]
        by_editor = vis.get("description_by") == "editor"
        if mine and not by_editor:
            for r in mine:
                r["score"] += 0.15 * len(got) / len(q)
            continue
        if mine:                      # the editor's own words also lift the moments they sit among
            for r in mine:
                r["score"] += 0.15 * len(got) / len(q)
        duration = float(c.get("duration") or 0.0)
        first = text.split(". ")[0].strip()
        merged.append({"clip": clip, "start": 0.0, "end": duration, "what": first,
                       "why": "the clip's description" + (" (yours)" if vis.get("description_by") == "editor" else ""),
                       "source": "described",
                       "score": 0.5 * len(got) / len(q) + (0.3 if by_editor else 0.0),
                       "base": (1.0 if by_editor else 0.5) * cover(got), "tokens": got})
    merged = [r for r in merged if r["base"] >= floor]
    for row in merged:
        row["nmatch"] = len([t for t in row.pop("tokens", ()) if t not in FILLER])
        row["nq"] = len([t for t in q if t not in FILLER])
        row["score"] = round(row["score"], 3)
        row["base"] = round(row["base"], 3)
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


# --------------------------------------------------------------- the union (wave 20)

# What a person reads next to a result. `seen` is the Claude visual pass's moments; `local` is
# the 3080's frame search; `model` is a Claude Find.
SOURCE_LABEL = {"heard": "heard", "felt": "felt", "seen": "seen in the frames", "local": "matches the picture", "noted": "the first pass's notes",
                "model": "Claude"}

# Reciprocal-rank fusion. K=10 and the 8 + 5 depths sit on a plateau of the Killington sweep
# (research/R12-understanding/eval): any K in 10..60 with 5-12 lexical and 3-8 visual rows
# gives the same hit@3, so these are the middle of the flat part, not a tuned peak.
FUSE_K = 10
FUSE_LEXICAL = 8
FUSE_VISUAL = 5
# Windows on the same clip within this many seconds of each other are one moment.
FUSE_GAP_S = 1.0


def _stem(clip: str) -> str:
    return Path(clip).stem


def _label(source: str) -> str:
    return " \u00b7 ".join(SOURCE_LABEL.get(n, n) for n in str(source).split("+"))


def _evidence(row: dict, source: str, rank: int) -> dict:
    return {"source": source, "label": _label(source), "what": row.get("what", ""),
            "start": row["start"], "end": row["end"], "score": row.get("score"), "rank": rank}


def local_rows(query: str, clips: dict[str, dict], *, k: int = FUSE_VISUAL,
               search: Callable[..., dict] | None = None) -> tuple[list[dict], str]:
    """The 3080's frame matches as Find rows, and why there are none (empty string if fine).

    `search(text, k=...)` is `localvlm.search_ex` by default (a subprocess: it loads the text
    tower under the GPU lock, so call this from a worker thread, never the event loop). A row
    for a clip the bin does not list (junk, out of the segment filter) is dropped.
    """
    if search is None:
        from . import localvlm
        search = localvlm.search_ex
    try:
        r = search(query, k=k)
    except Exception as exc:                                  # noqa: BLE001  degrade, never raise
        return [], f"the local search failed: {str(exc)[:120]}"
    if not r.get("ok"):
        return [], str(r.get("why") or "no local model")
    by_stem = {_stem(c): c for c in clips}
    rows = []
    for m in (r.get("results") or {}).get(query, []):
        clip = by_stem.get(_stem(m["clip"]))
        if clip is None:
            continue
        rows.append({"clip": clip, "start": float(m["start"]), "end": float(m["end"]),
                     "what": f"looks like \u201c{query.strip()}\u201d",
                     "why": "matches the picture on the local model", "source": "local",
                     "score": float(m.get("score", 0.0))})
    return rows, ""


def union(lexical_rows: list[dict], local: list[dict], *, k: int = FUSE_K, n_lexical: int = FUSE_LEXICAL,
          n_local: int = FUSE_VISUAL, gap: float = FUSE_GAP_S, limit: int = MAX_LEXICAL) -> list[dict]:
    """Lexical + local rows as one ranked list of windows. Pure.

    Each input is best-first. Rows on one clip whose windows touch (within `gap`) merge into
    one: the window becomes the hull of its parts (the local part is the scoring frame +-1 s,
    so a long utterance a frame match lands inside is not widened), `evidence` keeps every
    part, and the fused score is the sum of `k / (k + rank)` over the parts, so 1.0 is a lone
    top hit and a moment both sources rank first is near 2.
    """
    parts = [(dict(r), i, r.get("source") or "heard") for i, r in enumerate(lexical_rows[:n_lexical])]
    parts += [(dict(r), i, "local") for i, r in enumerate(local[:n_local])]
    parts.sort(key=lambda p: (_stem(p[0]["clip"]), p[0]["start"]))
    out: list[dict] = []
    for row, rank, source in parts:
        w = k / (k + rank)
        if source.startswith("heard") and float(row.get("base") or 0.0) >= 0.99:
            w += SPOKEN_BONUS             # every word of the query was said here: that outranks a look-alike
        ev = _evidence(row, source, rank)
        tgt = next((o for o in out if _stem(o["clip"]) == _stem(row["clip"])
                    and row["start"] <= o["end"] + gap and row["end"] >= o["start"] - gap), None)
        if tgt is None:
            out.append({**{x: row[x] for x in ("clip", "start", "end", "what", "why") if x in row},
                        "score": w, "evidence": [ev], "base": float(row.get("base") or 0.0),
                        **{x: row[x] for x in ("kind", "notable") if x in row}})
            continue
        tgt["start"], tgt["end"] = min(tgt["start"], row["start"]), max(tgt["end"], row["end"])
        tgt["score"] += w
        tgt["base"] = max(tgt.get("base", 0.0), float(row.get("base") or 0.0))
        tgt["evidence"].append(ev)
        if row.get("what") and row["what"] not in tgt["what"] and source != "local":
            tgt["what"] = f"{tgt['what']} \u00b7 {row['what']}" if tgt["what"] else row["what"]
        for x in ("kind", "notable"):
            if x in row and x not in tgt:
                tgt[x] = row[x]
    for o in out:
        names: list[str] = []
        for e in o["evidence"]:
            for part in str(e["source"]).split("+"):
                if part not in names:
                    names.append(part)
        o["source"] = "+".join(names)
        o["label"] = _label("+".join(names))
        o["why"] = ("matches here: " + o["label"]) if len(names) > 1 else o.get("why", "")
        o["score"] = round(o["score"], 3)
        o["start"], o["end"] = round(o["start"], 2), round(o["end"], 2)
    out.sort(key=lambda r: (-r["score"], r["clip"], r["start"]))
    return out[:limit]


# Prepositions that stay in the query tokens (so rank is unchanged) but do not count towards
# "answers for more than half of the query".
FILLER = {"from", "about", "over", "under", "through", "across", "near", "after", "before"}


def _convincing(row: dict) -> bool:
    """A words match that can stand on its own: it clears the weighted floor AND answers for more
    than half of a multi-word query ("someone wipes out and falls" is not answered by "the shadow
    falls across the snow")."""
    nq = int(row.get("nq") or 1)
    return row.get("base", 1.0) >= LEXICAL_FLOOR and (nq <= 1 or int(row.get("nmatch") or 0) * 2 > nq)


def search(query: str, clips: dict[str, dict], *, local: bool = True,
           local_search: Callable[..., dict] | None = None, weak: bool = False) -> dict:
    """The free Find: lexical, plus the 3080 when it is there.
    -> {matches, local: {used, why}, floor: {confident, weak_rows, ...}}.

    Nothing is filtered by score (`weak` is accepted and ignored: it only mattered when this
    removed hits). `floor.confident` says whether ANY hit clears a floor: a words match above
    LEXICAL_FLOOR, a picture match above LOCAL_FLOOR, or two weak sources agreeing. When none
    does, the board says "nothing convincing like that here" above the hits, which are then only
    the closest guesses. (A floor that REMOVED hits was tuned on Copper and cost Killington a
    real hit: its right picture matches score 0.187-0.20, Copper's absent queries 0.15-0.196,
    so no absolute picture score separates the two bins.) A picture match inside a stretch the
    look called unusable (a black frame) is dropped: it looks like everything and shows nothing."""
    lex = lexical(query, clips)
    floor = {"lexical": LEXICAL_FLOOR, "local": LOCAL_FLOOR, "confident": False, "weak_rows": 0,
             "hidden": 0, "weak": weak}
    if not local:
        floor["confident"] = any(_convincing(r) for r in lex)
        floor["weak_rows"] = len(lex)
        return {"matches": [dict(r, label=_label(r["source"]),
                                 evidence=[_evidence(r, r["source"], i)]) for i, r in enumerate(lex)],
                "local": {"used": False, "why": "not asked for"}, "floor": floor}
    vis, why = local_rows(query, clips, search=local_search)
    kept = [r for r in vis
            if _unusable_share(clips.get(r["clip"]) or {}, r["start"], r["end"]) < 0.5]

    def corroborated(row: dict) -> bool:
        return any(_stem(k["clip"]) == _stem(row["clip"]) and float(k.get("score") or 0.0) >= LOCAL_FLOOR
                   and k["start"] <= row["end"] + FUSE_GAP_S and k["end"] >= row["start"] - FUSE_GAP_S
                   for k in kept)

    floor["confident"] = (any(_convincing(r) or (r.get("base", 1.0) >= LEXICAL_FLOOR and corroborated(r)) for r in lex)
                          or any(float(k.get("score") or 0.0) >= LOCAL_FLOOR for k in kept))
    floor["weak_rows"] = len(lex) + len(kept)
    if not kept:
        return {"matches": union(lex, []), "floor": floor,
                "local": {"used": False, "why": why or "no frame matched"}}
    return {"matches": union(lex, kept), "floor": floor, "local": {"used": True, "why": ""}}


# --------------------------------------------------------------- the rerank (wave 20)

RERANK_TOP = 10
RERANK_THUMBS = 6
RERANK_ROLE = config.ROLE_VERIFY            # a look at a few frames, not a read of the bin

RERANK_SCHEMA = {
    "ranking": [{"n": 1, "fit": "yes | maybe | no", "why": "one short clause: what is there"}],
    "notes": "one sentence: how you judged, or what to try instead",
}

RERANK_SYSTEM = (
    "You help a film editor find a specific moment in raw footage. You are shown numbered "
    "candidate windows another search proposed, each with what it matched on and, for the "
    "first few, a small frame from inside it. You say which candidates really show or say "
    "what the editor described. You never add candidates and never invent timestamps.")


# The brief: ticked by default in Claude only / Hybrid. The Killington measurement says do not
# yet: one middle frame per candidate cannot see a 0.3 s air, and the rerank LOST two of the 15
# union hits (hit@3 0.789 -> 0.684, 19 calls, $1.39). Flip this when a prompt/thumbnail change
# beats the union in `tools/eval_understanding.py --rerank`.
RERANK_DEFAULT_ON = False


def rerank_policy(mode: str) -> dict:
    """Whether the rerank may run and whether it starts ticked, for the bin's index mode
    (`hybrid` | `local` | `claude`). Local only never spends a Claude call; the tick is
    on only in Claude only / Hybrid, and only while RERANK_DEFAULT_ON."""
    m = str(mode or "").lower()
    return {"allowed": m != "local", "default": RERANK_DEFAULT_ON and m in ("claude", "hybrid"), "mode": m}


def rerank_projected_usd(n_thumbs: int = RERANK_THUMBS) -> float:
    """One rerank call: the CLI harness overhead, ~700 tokens of text, a few small frames."""
    pin, pout = config.price_per_mtok(config.model_for(RERANK_ROLE))
    return round((18000 + 900 + n_thumbs * 450) / 1e6 * pin + 400 / 1e6 * pout, 3)


def build_rerank_prompt(query: str, rows: list[dict], n_thumbs: int) -> str:
    lines = []
    for i, r in enumerate(rows, 1):
        ev = "; ".join(f"{e['label']}: {str(e.get('what', ''))[:90]}" for e in r.get("evidence", [])) or r.get("what", "")
        lines.append(f"{i}. {r['clip']} {r['start']:.1f}-{r['end']:.1f} s \u2014 {ev}")
    pics = (f"The first {n_thumbs} candidates come with one frame each, in order, from the middle "
            "of their window." if n_thumbs else "No frames are attached; judge from the text.")
    return f"""The editor is looking for this moment in their footage:

## What they are looking for
{query.strip()}

## Candidates (best guess first)
{chr(10).join(lines)}

{pics} A candidate matched on "heard" is a line someone said (the thing may have happened
before or after it); "felt" is what the camera's sensors recorded; "seen by the 3080" is a
picture match by a small local model, often right about the scene and wrong about the
instant.

## What to return
For every candidate number, `fit`: "yes" if it shows or says what the editor asked for,
"maybe" if it is plausible but you cannot tell, "no" if it is something else. `why` is a
short clause. `notes` is one sentence."""


def validate_rerank(payload: Any, n: int) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("ranking"), list):
        raise ValueError("expected an object with a 'ranking' list")
    seen: dict[int, dict] = {}
    for i, r in enumerate(payload["ranking"]):
        if not isinstance(r, dict):
            raise ValueError(f"ranking {i} is not an object")
        try:
            k = int(r["n"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"ranking {i} has no candidate number") from None
        if not 1 <= k <= n:
            raise ValueError(f"ranking {i} names candidate {k}, there are {n}")
        fit = str(r.get("fit", "")).strip().lower()
        if fit not in ("yes", "maybe", "no"):
            raise ValueError(f"ranking {i} fit must be yes, maybe or no")
        seen.setdefault(k, {"fit": fit, "why": str(r.get("why", "")).strip()[:200]})
    return {"ranking": seen, "notes": str(payload.get("notes", "")).strip()[:300]}


def apply_rerank(rows: list[dict], verdict: dict) -> list[dict]:
    """Yes before maybe before unjudged before no; the union's order within each. A `no` is
    demoted, not deleted: the human decides what is gone."""
    order = {"yes": 0, "maybe": 1, None: 2, "no": 3}
    marked = []
    for i, r in enumerate(rows, 1):
        v = verdict["ranking"].get(i)
        r = dict(r, rerank=v) if v else dict(r)
        if v:
            r["source"] = r["source"] if "model" in r["source"].split("+") else r["source"] + "+model"
            if "Claude" not in r.get("label", ""):
                r["label"] = (r.get("label", "") + " \u00b7 Claude " + v["fit"]).lstrip(" \u00b7")
        marked.append(r)
    marked.sort(key=lambda r: order[(r.get("rerank") or {}).get("fit")])
    return marked


def rerank(query: str, rows: list[dict], thumb_of: Callable[[dict], Path | None] | None = None, *,
           top: int = RERANK_TOP, n_thumbs: int = RERANK_THUMBS) -> dict:
    """One Claude call over the union's top rows. -> {matches (the whole list, reordered),
    notes, usage}. Raises InferenceError like `find`; the caller falls back to `rows`."""
    cands = rows[:top]
    if not query.strip() or not cands:
        raise ValueError("nothing to rerank")
    thumbs: list[Path] = []
    for r in cands[:n_thumbs]:
        p = thumb_of(r) if thumb_of else None
        if not p:
            break                 # the prompt says "the first N candidates have a frame": a prefix only
        thumbs.append(Path(p))
    result = complete(build_rerank_prompt(query, cands, len(thumbs)), role=RERANK_ROLE, images=thumbs,
                      schema=RERANK_SCHEMA, system=RERANK_SYSTEM,
                      validate=lambda p: validate_rerank(p, len(cands)), retries=1, isolated=True)
    verdict = result.content
    return {"matches": apply_rerank(cands, verdict) + rows[top:], "notes": verdict["notes"],
            "usage": {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                      "projected_usd": result.projected_usd, "model": result.model,
                      "backend": result.backend, "latency_ms": result.latency_ms,
                      "thumbs": len(thumbs)}}


def thumb_maker(video_of: Callable[[str], Path], workdir: Path, width: int = 320) -> Callable[[dict], Path | None]:
    """A `thumb_of` for `rerank`: one frame from the middle of a row's window, as a small JPEG."""
    workdir = Path(workdir)

    def make(row: dict) -> Path | None:
        from . import verify
        t = (float(row["start"]) + float(row["end"])) / 2
        out = workdir / f"{_stem(row['clip'])}_{t:.2f}.jpg"
        if out.exists():
            return out
        try:
            frames, _ = verify.get_frames(Path(video_of(row["clip"])), [t], width)
            if not frames or frames[0] is None:
                return None
            workdir.mkdir(parents=True, exist_ok=True)
            frames[0].convert("RGB").save(out, quality=80)
            return out
        except Exception:                                    # noqa: BLE001  a missing frame is not a failure
            return None
    return make
