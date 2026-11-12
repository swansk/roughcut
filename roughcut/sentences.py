"""Never cut off a line: sentences, reactions, and where a shot may start and end.

Karl, 2026-11-11, on the Copper first pass: *"there are several clips where you cut the
audio mid sentence. never do that, extend as needed (past 2 mins is ok) so you capture
complete sentences + reaction"* — and, said out loud, *"this is something you actually
should change in general"*. So it is the built-in rule for every cut the AI makes, not a
note on one project:

  A shot with speech in it starts at the start of a sentence and ends after the
  sentence ends AND after the reaction to it (the laugh, the reply) has played out.
  When that conflicts with the target length, the SENTENCE WINS: the film runs longer
  than asked, or a whole shot goes, never a clipped line. The editor's own edits are
  never changed by it, and an ask that says mid-line cuts are fine for a shot can mark
  that shot `tight`.

`boundaries.polish` finishes a *word*; this finishes a *sentence*. Both are pure.

How a sentence is found (the transcript is ASR, so this is a reading, not a fact):

  * a word ending in `.`, `?`, `!` or `…` ends a sentence — Whisper punctuates most
    line ends (on Copper 121 of 144 utterances end in . ? or !);
  * the end of an ASR utterance ends one too, unless its last word dangles ("a",
    "the", "to", "and" …: "We need to go find a | We need to go to a gas station"),
    it ends on a comma, or the next utterance starts in lower case within half a
    second — those are one sentence broken by the decoder;
  * a pause of `PAUSE_S` or more between words ends one whatever the punctuation;
  * a change of speaker ends one, when both words carry a speaker label.

What that gets wrong, measured by reading Copper's 144 utterances: a self-correction
the decoder split on a non-dangling word reads as two sentences (a cut between them is
not flagged), and a long run-on with no punctuation and no pause reads as one long
sentence (a cut inside it is flagged even when it is a natural place to stop). The
second kind is why extension is bounded (`MAX_GROW_S`): past it, the boundary retreats
to the sentence edge inside the shot instead.

A *reaction* is the next sentence when it starts within `REACTION_GAP_S` of the line
ending and is short (`REACTION_MAX_S`) — a reply, "That's decaf?", a laugh the ASR
wrote down. A laugh the ASR did not write down is invisible here (the sidecar has no
laugh events); `research/tools/speech_cuts.py` reports voice activity after an out
point separately, as a hint, for that reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Boundary tolerance: a cut this close to a sentence edge is on it. Hundredths of a
# second of ASR timing jitter and the frame grid live inside it.
TOL_S = 0.12
# An out-point less than this after the last word sounds like it cuts the word
# (boundaries.NEAR_WORD_S, the same measurement).
BEAT_S = 0.30
# Pause that ends a sentence whatever the punctuation says.
PAUSE_S = 1.0
# The reaction: starts within this of the line ending, and is no longer than this.
REACTION_GAP_S = 1.5
REACTION_MAX_S = 3.0
# Padding, boundaries.py's numbers.
PAD_HEAD = 0.25
PAD_TAIL = 0.45
# No boundary grows by more than this to finish a line: past it the "sentence" is more
# likely an ASR run-on than a line, and the boundary retreats to the edge inside instead.
MAX_GROW_S = 8.0
MIN_SEG_S = 0.5
# Two shots of one clip that follow each other in the film and meet within this play as
# one stretch of the recording (a razor cut): the boundary between them is not a cut.
# Any wider gap skips sound, so a line across it is a clipped line.
RUN_ON_S = 0.1

TERMINAL = (".", "?", "!", "…")
DANGLING = {
    "a", "an", "the", "to", "and", "or", "but", "of", "for", "with", "in", "on", "at",
    "is", "was", "are", "i", "my", "your", "our", "their", "his", "her", "if", "so",
    "that", "this", "like", "because", "about", "from", "as", "by", "we", "they",
    "he", "she", "it's", "i'm", "gonna", "just", "be", "been", "have", "has",
}


@dataclass
class Sentence:
    start: float
    end: float
    text: str
    speaker: str | None = None
    words: list[tuple[float, float, str]] = field(default_factory=list)

    @property
    def dur(self) -> float:
        return self.end - self.start


def _words(transcript: list[dict]) -> list[dict]:
    """Every word as {t, e, w, s, last (of its utterance), next_lower}, in time order."""
    out: list[dict] = []
    utts = [u for u in transcript or [] if isinstance(u, dict)]
    for k, u in enumerate(utts):
        try:
            u_start, u_end = float(u["start"]), float(u["end"])
        except (KeyError, TypeError, ValueError):
            continue
        text = str(u.get("text", "")).strip()
        words = [w for w in u.get("words") or [] if isinstance(w, dict) and "t" in w]
        nxt = next((str(v.get("text", "")).strip() for v in utts[k + 1:]
                    if str(v.get("text", "")).strip()), "")
        next_lower = bool(nxt) and nxt[0].islower()
        if not words:
            if text:
                out.append({"t": u_start, "e": u_end, "w": text, "s": u.get("speaker"),
                            "last": True, "next_lower": next_lower, "uend": text})
            continue
        for i, w in enumerate(words):
            try:
                t = float(w["t"])
            except (TypeError, ValueError):
                continue
            e = w.get("e")
            if e is None:
                nt = words[i + 1].get("t") if i + 1 < len(words) else None
                e = min(float(nt), t + 0.8) if nt is not None else u_end
            out.append({"t": t, "e": max(float(e), t), "w": str(w.get("w", "")).strip(),
                        "s": w.get("s") or u.get("speaker"), "last": i == len(words) - 1,
                        "next_lower": next_lower, "uend": text})
    out.sort(key=lambda w: (w["t"], w["e"]))
    return out


def _ends_sentence(w: dict, nxt: dict | None) -> bool:
    if nxt is None:
        return True
    gap = nxt["t"] - w["e"]
    if gap >= PAUSE_S:
        return True
    if w.get("s") and nxt.get("s") and w["s"] != nxt["s"]:
        return True
    word = w["w"].rstrip("\"')]")
    if word.endswith(TERMINAL):
        return True
    if w["last"]:
        bare = word.rstrip(",;:-—").lower()
        if word.endswith((",", ";", ":", "-", "—")) or bare in DANGLING:
            return False
        if w["next_lower"] and gap < 0.5:
            return False
        return True
    return False


def sentences(transcript: list[dict]) -> list[Sentence]:
    """The clip's sentences, in time order (see the module docstring for the reading)."""
    words = _words(transcript)
    out: list[Sentence] = []
    cur: list[dict] = []
    for i, w in enumerate(words):
        cur.append(w)
        if _ends_sentence(w, words[i + 1] if i + 1 < len(words) else None):
            out.append(Sentence(cur[0]["t"], max(x["e"] for x in cur),
                                " ".join(x["w"] for x in cur), cur[0].get("s"),
                                [(x["t"], x["e"], x["w"]) for x in cur]))
            cur = []
    return out


def _short(text: str, n: int = 40) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _reaction(sents: list[Sentence], line: Sentence,
              taken: list[tuple[float, float]] | None = None) -> Sentence | None:
    """The sentence answering `line`, if one comes quickly and is short — and is not
    already played by another shot of the cut (`taken`: their ranges in this clip): a
    reply the editor gave its own shot is placed, not cut off."""
    k = next((i for i, s in enumerate(sents) if s is line), None)
    if k is None or k + 1 >= len(sents):
        return None
    r = sents[k + 1]
    if any(a < r.end and b > r.start for a, b in taken or ()):
        return None
    if 0 <= r.start - line.end <= REACTION_GAP_S and r.dur <= REACTION_MAX_S:
        return r
    return None


def _after(sents: list[Sentence], s: Sentence) -> Sentence | None:
    k = next((i for i, x in enumerate(sents) if x is s), None)
    return sents[k + 1] if k is not None and k + 1 < len(sents) else None


def _before(sents: list[Sentence], s: Sentence) -> Sentence | None:
    k = next((i for i, x in enumerate(sents) if x is s), None)
    return sents[k - 1] if k else None


# ------------------------------------------------------------------ the measure


def shot_problems(t_in: float, t_out: float, sents: list[Sentence], *,
                  check_in: bool = True, check_out: bool = True,
                  duration: float | None = None,
                  taken: list[tuple[float, float]] | None = None) -> list[dict]:
    """What is wrong with one shot's boundaries, as rows `{kind, at, line}`:

    `mid_in`    the shot opens after a sentence began;
    `mid_out`   it ends before a sentence ended;
    `no_beat`   it ends less than `BEAT_S` after the last word of a finished line;
    `reaction`  it ends before a short reply/reaction that starts within
                `REACTION_GAP_S` of its last line (and not inside the shot).

    A shot with no word in it has no problems; a shot that ends where the recording
    does (`duration`) has no beat to give, so it is never `no_beat`. `check_in` / `check_out` false: that
    side runs on into the next shot of the same clip, so it is not a cut."""
    inside = [s for s in sents if s.start < t_out and s.end > t_in]
    if not inside:
        return []
    rows: list[dict] = []
    if check_in:
        s = next((s for s in sents if s.start < t_in - TOL_S and s.end > t_in + TOL_S), None)
        if s is not None:
            rows.append({"kind": "mid_in", "at": t_in, "line": s.text,
                         "sentence": [round(s.start, 2), round(s.end, 2)]})
    if check_out:
        s = next((s for s in sents if s.start < t_out - TOL_S and s.end > t_out + TOL_S), None)
        if s is not None:
            rows.append({"kind": "mid_out", "at": t_out, "line": s.text,
                         "sentence": [round(s.start, 2), round(s.end, 2)]})
        else:
            done = [s for s in inside if s.end <= t_out + TOL_S]
            last = max(done, key=lambda s: s.end) if done else None
            # no beat is possible when the next line starts right after this one
            at_end = duration is not None and t_out >= float(duration) - 0.05
            if last is not None and t_out - last.end < BEAT_S and not at_end and not (
                    (r := _after(sents, last)) is not None
                    and r.start <= last.end + BEAT_S + 0.05):
                rows.append({"kind": "no_beat", "at": t_out, "line": last.text,
                             "sentence": [round(last.start, 2), round(last.end, 2)]})
            if last is not None:
                r = _reaction(sents, last, taken)
                if r is not None and r.start >= t_out - TOL_S:
                    rows.append({"kind": "reaction", "at": t_out, "line": last.text,
                                 "reply": r.text,
                                 "sentence": [round(r.start, 2), round(r.end, 2)]})
    return rows


def _runs_on(a: dict, b: dict) -> bool:
    """Shot `b` plays right after shot `a` and continues the same recording."""
    return (a.get("clip") == b.get("clip")
            and abs(float(b["in"]) - float(a["out"])) <= RUN_ON_S)


def measure(segments: list[dict], clips: dict[str, dict]) -> dict:
    """Every clipped line in a cut: `{shots, speech_shots, problems: [...], counts}`.
    `clips[name]["transcript"]` is the sidecar's. Pure."""
    cache: dict[str, list[Sentence]] = {}
    problems: list[dict] = []
    speech = 0
    for i, seg in enumerate(segments):
        c = seg.get("clip")
        clip = clips.get(c)
        if not clip:
            continue
        if c not in cache:
            cache[c] = sentences(clip.get("transcript") or [])
        sents = cache[c]
        t_in, t_out = float(seg["in"]), float(seg["out"])
        if any(s.start < t_out and s.end > t_in for s in sents):
            speech += 1
        prev = segments[i - 1] if i else None
        nxt = segments[i + 1] if i + 1 < len(segments) else None
        rows = shot_problems(t_in, t_out, sents,
                             check_in=not (prev and _runs_on(prev, seg)),
                             check_out=not (nxt and _runs_on(seg, nxt)),
                             duration=clip.get("duration"),
                             taken=[(float(o["in"]), float(o["out"])) for j, o in enumerate(segments)
                                    if j != i and o.get("clip") == c])
        for r in rows:
            problems.append({"shot": i + 1, "id": seg.get("id"), "clip": c,
                             "in": round(t_in, 2), "out": round(t_out, 2), **r})
    counts: dict[str, int] = {}
    for p in problems:
        counts[p["kind"]] = counts.get(p["kind"], 0) + 1
    clipped = sum(1 for p in problems if p["kind"] in ("mid_in", "mid_out"))
    return {"shots": len(segments), "speech_shots": speech, "clipped": clipped,
            "counts": counts, "problems": problems}


# ------------------------------------------------------------------ the fix


def _blocked(a: float, b: float, spans: list[tuple[float, float]]) -> bool:
    lo, hi = (a, b) if a <= b else (b, a)
    return any(lo < e and hi > s for s, e in spans)


def complete_segment(seg: dict, clip: dict, *, floor: float = 0.0,
                     ceiling: float | None = None, check_in: bool = True,
                     check_out: bool = True,
                     unusable: list[tuple[float, float]] | None = None,
                     taken: list[tuple[float, float]] | None = None) -> tuple[dict, list[str]]:
    """One shot, its lines finished. Returns the new segment and plain-words reasons
    (empty: nothing moved). `floor` / `ceiling`: the room it has (other shots of the same
    clip that it must not overlap); `unusable`: picture stretches it must not grow into.

    In-point inside a sentence: back to the sentence start (a breath before it); if that
    is more than `MAX_GROW_S` away or blocked, forward to the next sentence start inside
    the shot. Out-point inside a sentence, too close after the last word, or before a
    quick short reaction: out to the end of the line and its reaction plus a beat; if
    that cannot be done, back to the end of the last whole line inside the shot."""
    t_in, t_out = float(seg["in"]), float(seg["out"])
    sents = sentences(clip.get("transcript") or [])
    duration = float(clip.get("duration") or 0.0) or max(t_out, max((s.end for s in sents), default=0.0) + 1)
    hi = duration if ceiling is None else min(duration, ceiling)
    unusable = unusable or []
    reasons: list[str] = []
    if not any(s.start < t_out and s.end > t_in for s in sents):
        return dict(seg), []
    new_in, new_out = t_in, t_out

    if check_in:
        s = next((s for s in sents if s.start < t_in - TOL_S and s.end > t_in + TOL_S), None)
        if s is not None:
            prev = _before(sents, s)
            want = max(s.start - PAD_HEAD, prev.end + 0.05 if prev else 0.0, 0.0)
            if (t_in - want <= MAX_GROW_S and want >= floor - 1e-6
                    and not _blocked(want, t_in, unusable)):
                new_in = round(want, 2)
                reasons.append(f"starts {t_in - new_in:.1f}s earlier, at the start of "
                               f"'{_short(s.text)}'")
            else:
                n = _after(sents, s)
                if n is not None and n.start < t_out - MIN_SEG_S:
                    new_in = round(max(s.end + 0.05, n.start - PAD_HEAD), 2)
                    reasons.append(f"starts {new_in - t_in:.1f}s later, on "
                                   f"'{_short(n.text)}', not inside '{_short(s.text, 24)}'")

    if check_out:
        s = next((s for s in sents if s.start < t_out - TOL_S and s.end > t_out + TOL_S), None)
        line = s
        if line is None:
            done = [x for x in sents if x.start < t_out and x.end > new_in and x.end <= t_out + TOL_S]
            last = max(done, key=lambda x: x.end) if done else None
            if last is not None:
                r = _reaction(sents, last, taken)
                if (r is not None and r.start >= t_out - TOL_S) or t_out - last.end < BEAT_S:
                    line = last
        if line is not None:
            # the reply, and the reply to it, while the exchange stays quick and short
            r = None
            end_s = line
            while (nr := _reaction(sents, end_s, taken)) is not None and nr.end - t_out <= MAX_GROW_S:
                r = r or nr
                end_s = nr
            follow = _after(sents, end_s)
            want = end_s.end + PAD_TAIL
            if follow is not None:
                # a beat, but never into the next line's first word
                want = min(want, max(end_s.end, follow.start - 0.02))
            want = round(min(want, duration), 2)
            if (want > t_out and want - t_out <= MAX_GROW_S and want <= hi + 1e-6
                    and not _blocked(t_out, want, unusable)):
                new_out = want
                what = (f"'{_short(line.text)}' and the reply '{_short(r.text, 24)}'"
                        if r is not None else f"'{_short(line.text)}'")
                reasons.append(f"ends {new_out - t_out:.1f}s later, after {what}")
            elif want <= t_out or s is None:
                pass                    # already past the line, or only a reply is left out
            else:
                # cannot grow: back to the end of the last whole line before this one
                before = [x for x in sents if x.end <= line.start + 1e-6 and x.end > new_in + MIN_SEG_S]
                if s is not None and before:
                    b = before[-1]
                    back = round(min(b.end + PAD_TAIL, line.start - 0.05), 2)
                    if back - new_in >= MIN_SEG_S and back < t_out:
                        new_out = back
                        reasons.append(f"ends {t_out - new_out:.1f}s earlier, after "
                                       f"'{_short(b.text)}', not inside '{_short(line.text, 24)}'")
    if (new_in, new_out) == (t_in, t_out) or new_out - new_in < MIN_SEG_S:
        return dict(seg), []
    out = dict(seg)
    out["in"], out["out"] = new_in, new_out
    return out, reasons


def complete_lines(segments: list[dict], clips: dict[str, dict], *,
                   keep: set[str] | None = None,
                   unusable_of=None, keep_src: bool = False,
                   fixed: list[dict] | None = None) -> tuple[list[dict], list[str]]:
    """Every shot's lines finished (`complete_segment`), then shots of one clip that now
    run on into each other in the film joined into one. A shot whose id is in `keep`
    (the editor's own edits) or marked `tight` (the ask said mid-line cuts are fine) is
    left exactly as it is, and nothing grows into it. Returns (segments, the changed
    shots' reasons as lines for the notes). Pure. `keep_src`: each shot keeps `_src`
    (the index of the input shot it came from) and `sentence_why`, for the polish.
    `fixed`: ranges `{clip, in, out}` of shots that will be in the cut whatever this plan
    says (the re-run's locked shots, put back after): nothing grows into them."""
    keep = set(keep or ())
    unusable_of = unusable_of or (lambda clip: [])
    fixed_segs = [dict(s) for s in segments]
    why: dict[int, list[str]] = {}

    def frozen(s: dict) -> bool:
        return bool(s.get("tight")) or (s.get("id") is not None and s.get("id") in keep)

    for i, seg in enumerate(segments):
        clip = clips.get(seg.get("clip"))
        if not clip or frozen(seg):
            continue
        prev = segments[i - 1] if i else None
        nxt = segments[i + 1] if i + 1 < len(segments) else None
        # A neighbour in the film from the same recording that this shot may join: its
        # boundary is no limit. Every other shot of the clip is.
        joinable = {j for j in (i - 1, i + 1)
                    if 0 <= j < len(segments) and segments[j].get("clip") == seg["clip"]
                    and not frozen(segments[j])
                    and ((j == i + 1 and float(segments[j]["in"]) >= float(seg["in"]))
                         or (j == i - 1 and float(segments[j]["out"]) <= float(seg["out"])))}
        same = [(j, s) for j, s in enumerate(fixed_segs) if j != i and s.get("clip") == seg["clip"]
                and j not in joinable] + [(-1, f) for f in fixed or () if f.get("clip") == seg["clip"]]
        floor = max((float(s["out"]) for _, s in same if float(s["out"]) <= float(seg["in"]) + 0.01),
                    default=0.0)
        ceiling = min((float(s["in"]) for _, s in same if float(s["in"]) >= float(seg["out"]) - 0.01),
                      default=None)
        new, reasons = complete_segment(
            seg, clip, floor=floor, ceiling=ceiling,
            check_in=not (prev and _runs_on(prev, seg)),
            check_out=not (nxt and _runs_on(seg, nxt)),
            unusable=unusable_of(clip),
            taken=[(float(o["in"]), float(o["out"])) for j, o in enumerate(segments)
                   if j != i and o.get("clip") == seg.get("clip")]
            + [(float(f["in"]), float(f["out"])) for f in fixed or () if f.get("clip") == seg.get("clip")])
        if reasons:
            fixed_segs[i] = new
            why[i] = reasons

    # join: consecutive shots of one clip, in recording order, that now meet or overlap
    out: list[dict] = []
    lines: list[str] = []
    for i, seg in enumerate(fixed_segs):
        s = dict(seg)
        s["_src"] = i
        if i in why:
            s["sentence_why"] = "; ".join(why[i])
        # join only where finishing a line made the two overlap: shots that merely meet
        # already play as one stretch, and the model may have split them on purpose
        if out and not frozen(s) and not frozen(out[-1]) and out[-1].get("clip") == s.get("clip") \
                and float(s["in"]) >= float(out[-1]["in"]) \
                and float(s["in"]) < float(out[-1]["out"]) - 0.01 \
                and (i in why or (i - 1) in why):
            a = out[-1]
            joined = dict(a)
            joined["out"] = max(float(a["out"]), float(s["out"]))
            joined["why"] = (str(a.get("why", "")) + (" Then: " + str(s.get("why", "")) if s.get("why") else ""))[:200]
            joined["sentence_why"] = "; ".join(x for x in (
                a.get("sentence_why"), s.get("sentence_why"),
                "joined with the next shot of the same clip: the line runs on into it") if x)
            out[-1] = joined
            continue
        out.append(s)
    for n, s in enumerate(out):
        if s.get("sentence_why"):
            lines.append(f"shot {n + 1}: {s['sentence_why']}")
    if not keep_src:
        for s in out:
            s.pop("_src", None)
            s.pop("sentence_why", None)
    return out, lines


def is_line_fix(why: str) -> bool:
    """A `polish_why` that includes a line this module finished."""
    return any(p.strip().startswith(("starts ", "ends ", "joined ")) for p in (why or "").split(";"))


def _clock(t: float) -> str:
    t = int(round(t))
    return f"{t // 60}:{t % 60:02d}"


def over_aim_line(total: float, target, added: float) -> str:
    """One sentence for the notes when finishing lines left the film over the aim:
    "Runs 2:36, over the 1:48–2:12 aim, so every line finishes." `added`: the seconds
    the line pass added. Empty when the film is inside the aim or nothing was added."""
    try:
        lo, hi = float(target[0]), float(target[1])
    except (TypeError, ValueError, IndexError):
        return ""
    if added <= 0.05 or total <= hi + 0.5:
        return ""
    return (f"Runs {_clock(total)}, over the {_clock(lo)}–{_clock(hi)} aim, so every line "
            f"finishes (finishing lines added {added:.0f} s).")
