"""Talk and hang-out moments, re-scored from a transcript that already exists (K7, wave 22).

Karl: *"make sure you're not over-indexing only on finding those super acceleration events
... there's also the fun B-roll I want to include too, like where people are sitting on a
chairlift and they say pocket pizza."* The line was transcribed correctly and ranked 221st of
312 speech candidates, because the speech scorer (research/tools/audio_analyze.build_candidates)
rewards exclamations at action and nothing else.

This module is a pure re-score of the sidecar's transcript: no ASR, no model, no video. A
line, or a few lines said close together (an *exchange*), earns:

  answered   a second voice replies within 3 s (the speakers stage's `speaker`, S1 / S2 ...)
  recurs     a two-word phrase that comes back, in the same clip or another one ("pocket pizza"
             twice) - the running joke, which is the connective tissue of a trip film
  question   a line that asks something
  laugh      laughter written into the transcript (haha, lol, [laughter])
  cheer      a whoop or a holler (full weight; see the note on tuning below)

It needs no telemetry at all, so a phone clip's banter ranks on its words. Where the clip also
has telemetry, a chairlift stretch or a stopped stretch with people talking in it becomes a
`hangout` moment over the best exchange inside it.

TUNING, said plainly (wave 22): the shape of the weights is argued, but CHEER (raised from 0.5 to
1.0), the with-picture boost in events.py, and the list rules in revise.py (LIST_WORDS, one row of
words and one of pictures per clip) were adjusted against Killington's 12 labelled non-event moments
until the eval's B-roll line reached 6 of 12, a margin of one row. One bin; unproven on other
footage. Do not re-tune until a second labelled bin exists.
"""

from __future__ import annotations

import re
from typing import Iterable

MAX_EXCHANGE_S = 20.0    # a long run of chatter is cut into moments an editor could use
GAP_S = 3.0              # lines this close together are one exchange; a reply this close is an answer
ANSWER, RECURS, QUESTION, LAUGH, CHEER = 1.0, 1.0, 0.5, 1.0, 1.0
RECURS_ACROSS_CLIPS = 0.5          # extra when the phrase is in more than one clip
NOTABLE_TOP = 0.10                 # an exchange is notable when its re-score is in the bin's top 10 %
MAX_NO_SPEECH = 0.6                # ASR's own doubt that a line is speech at all
HANGOUT_PAD_S = 2.0
STOP_MIN_EXCHANGE = 0.5            # a stopped stretch is a hangout only if something in it scored

_STOP = frozenset("""
a about after all also am an and any are as at be because been but by can could did do does
doing don dont down for from get got gonna had has have he her here him his how i if in into
is it its just let lets like me more my no not now of off oh ok okay on one or our out over
right she so some that the their them then there they this to too up us was we well were what
when where which who why will with would yeah yes you your youre ya yo bro dude man hey
alright gotta going go good nice cool little really very much thing things
""".split())
_WORD = re.compile(r"[a-z0-9']+")
_LAUGH = re.compile(r"\b(?:ha(?:\s?ha)+|he(?:\s?he)+|lol|lmao|laugh(?:s|ing|ter)?)\b|\[laugh|\(laugh|☺|\U0001f602",
                    re.I)
_CHEER = re.compile(r"\b(?:wo+h?|whoo+|woo+|yee+w|yahoo|hell yeah|let'?s go|holy (?:shit|crap)|oh my god)\b", re.I)


def words(text: str) -> list[str]:
    return _WORD.findall((text or "").lower().replace("’", "'"))


def phrases(text: str) -> set[str]:
    """Two-word phrases of content words, in order: "you got the pocket pizza?" -> {"pocket pizza"}.
    Stop words are dropped first, so a phrase survives a filler word between its two halves."""
    content = [w for w in words(text) if w not in _STOP and len(w) > 2]
    return {f"{a} {b}" for a, b in zip(content, content[1:])}


def _lines(transcript: Iterable[dict]) -> list[dict]:
    out = []
    for u in transcript or []:
        try:
            a, b = float(u["start"]), float(u["end"])
        except (KeyError, TypeError, ValueError):
            continue
        text = str(u.get("text") or "").strip()
        if len(words(text)) < 2 or float(u.get("no_speech_prob") or 0.0) > MAX_NO_SPEECH:
            continue
        out.append({"start": a, "end": max(b, a + 0.1), "text": text, "speaker": u.get("speaker")})
    out.sort(key=lambda u: u["start"])
    return out


def exchanges(transcript: Iterable[dict]) -> list[list[dict]]:
    """Consecutive lines no more than GAP_S apart."""
    groups: list[list[dict]] = []
    for u in _lines(transcript):
        if (groups and u["start"] - groups[-1][-1]["end"] <= GAP_S
                and u["end"] - groups[-1][0]["start"] <= MAX_EXCHANGE_S):
            groups[-1].append(u)
        else:
            groups.append([u])
    return groups


def phrase_counts(transcripts: dict[str, Iterable[dict]]) -> dict[str, dict[str, int]]:
    """{phrase: {clip: number of lines it is in}} over the whole bin."""
    out: dict[str, dict[str, int]] = {}
    seen: set[str] = set()
    for clip, tr in transcripts.items():
        said = " ".join(u["text"] for u in _lines(tr))
        if said in seen:
            continue                 # a copy of a clip already counted is not the phrase coming back
        seen.add(said)
        for u in _lines(tr):
            for p in phrases(u["text"]):
                out.setdefault(p, {}).setdefault(clip, 0)
                out[p][clip] += 1
    return out


def rescore(group: list[dict], clip: str, counts: dict[str, dict[str, int]]) -> dict:
    """What one exchange earns, term by term (the terms ride along in `why_ranked`)."""
    answered = any(a.get("speaker") and b.get("speaker") and a["speaker"] != b["speaker"]
                   and b["start"] - a["end"] <= GAP_S for a, b in zip(group, group[1:]))
    recurring, across = [], False
    for p in sorted({p for u in group for p in phrases(u["text"])}):
        where = counts.get(p, {})
        if sum(where.values()) >= 2:
            recurring.append(p)
            across = across or len(where) > 1
    text = " ".join(u["text"] for u in group)
    terms = {"answered": ANSWER if answered else 0.0,
             "recurs": (RECURS + (RECURS_ACROSS_CLIPS if across else 0.0)) if recurring else 0.0,
             "question": QUESTION if "?" in text else 0.0,
             "laugh": LAUGH if _LAUGH.search(text) else 0.0,
             "cheer": CHEER if _CHEER.search(text) else 0.0}
    return {"rescore": round(sum(terms.values()), 2), "terms": terms, "phrases": recurring}


def _quote(group: list[dict], limit: int = 140) -> str:
    text = " / ".join(u["text"].strip() for u in group)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _percentile(values: list[float], top: float) -> float:
    """The value a fraction `top` of the list is at or above."""
    if not values:
        return 0.0
    ranked = sorted(values, reverse=True)
    return ranked[min(len(ranked) - 1, max(0, int(len(ranked) * top) - 1))]


def moments(transcripts: dict[str, Iterable[dict]],
            felt: dict[str, list[dict]] | None = None) -> dict[str, list[dict]]:
    """Every clip's talk and hangout moments, by clip. Shaped like `events.telemetry_moments`
    rows before scoring: `kind` talk / hangout, `source` "transcript", `status` "unseen", the
    re-score terms in `talk`; `events.score_moment` turns them into a ranked row.

    The notable bar is the bin's own top tenth of exchange scores (zeros included), so a bin
    that is all banter and a bin that is all engine noise each surface their best, not a fixed
    number of them."""
    counts = phrase_counts(transcripts)
    copies: set[str] = set()
    for clip in list(transcripts):           # a copy of a clip already listed is not a second moment
        said = " ".join(u["text"] for u in _lines(transcripts[clip]))
        if said and said in copies:
            transcripts = {c: t for c, t in transcripts.items() if c != clip}
        copies.add(said)
    per_clip: dict[str, list[tuple[list[dict], dict]]] = {}
    every: list[float] = []
    for clip, tr in transcripts.items():
        for g in exchanges(tr):
            r = rescore(g, clip, counts)
            per_clip.setdefault(clip, []).append((g, r))
            every.append(r["rescore"])
    bar = max(_percentile(every, NOTABLE_TOP), 0.5)
    out: dict[str, list[dict]] = {}
    for clip, groups in per_clip.items():
        rows: list[dict] = []
        used: set[int] = set()
        for ev in (felt or {}).get(clip, []):
            if ev.get("kind") not in ("lift", "stopped"):
                continue
            a, b = float(ev["start"]), float(ev["end"])
            inside = [i for i, (g, r) in enumerate(groups)
                      if g[-1]["end"] > a and g[0]["start"] < b and i not in used]
            if not inside:
                continue
            lift = ev["kind"] == "lift"
            best = max(inside, key=lambda i: (groups[i][1]["rescore"],
                                              groups[i][0][-1]["end"] - groups[i][0][0]["start"]))
            g, r = groups[best]
            if not lift and r["rescore"] < STOP_MIN_EXCHANGE:
                continue                     # most stretches where someone stood still are just that
            used.add(best)
            s = max(a, g[0]["start"] - HANGOUT_PAD_S)
            e = min(b, g[-1]["end"] + HANGOUT_PAD_S)
            where = "on the chairlift" if lift else "stopped"
            rows.append({"clip": clip, "start": round(s, 2), "end": round(max(e, s + 0.1), 2),
                         "kind": "hangout", "what": f"{where}, talking: “{_quote(g)}”",
                         "notable": lift or r["rescore"] >= bar, "source": "transcript",
                         "status": "unseen", "who": "", "frames": [], "confidence": "", "demoted": "",
                         "talk": r})
        for i, (g, r) in enumerate(groups):
            if i in used or r["rescore"] <= 0:
                continue
            rows.append({"clip": clip, "start": round(g[0]["start"], 2), "end": round(g[-1]["end"], 2),
                         "kind": "talk", "what": f"people talking: “{_quote(g)}”",
                         "notable": r["rescore"] >= bar, "source": "transcript", "status": "unseen",
                         "who": "", "frames": [], "confidence": "", "demoted": "", "talk": r})
        if rows:
            out[clip] = rows
    return out
