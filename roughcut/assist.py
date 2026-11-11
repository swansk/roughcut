"""Ask the AI for anything the pro tools do (REDESIGN-GOAL dimensions 14 and 15, Karl's K3).

One model call turns the editor's words ("dissolve between 4 and 5", "warm up the snow
shots", "export a 1080p review copy") into one of three answers:

  * `ops`    — edits in the action vocabulary (roughcut/actions.py): the same
               operations the Timeline, Effect Controls, Lumetri, the Effects bin and
               Export apply for a human, reviewed op by op before any lands;
  * `plan`   — a whole-cut rewrite ("make it a 30 s teaser"): the existing Ask path
               (roughcut/revise.py through the server's Ask job) proposes the new cut and
               the existing proposal panel reviews it;
  * `answer` — a question, answered in words; nothing changes.

The prompt carries what an editor would have in front of them: the sequence (every shot's
id, clip, clip range, film time, speed and a short account of its Effect Controls and
colour), the markers, In / Out, tracks, V2 clips and transitions; the scope (the
selection, the In / Out range, the whole sequence or a Source clip — the editor sees it
before they ask); the project's clips with a bounded line of what is said and seen in
each; and the op schema. All inference goes through `roughcut.inference` on a role from
`roughcut.config` (REDESIGN-GOAL section S): no model id appears here.

Price: the call is priced before it runs from the prompt it is about to send (its
tokens at the role's model, `config.projected_usd`), and measured after; both are worded
by `firstpass.cost_words` — "free on your plan" on the subscription backend, the
API-equivalent estimate in the hover. (roughcut/estimate.py is the ETA estimator, itself
a model call; an assist is one short call, so it is not asked for a bar.)
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from . import actions, config
from . import look as lookmod
from . import tracks as tracksmod
from .firstpass import clip_name, cost_words
from .inference import complete

ROLE = config.ROLE_SKELETON            # a judgement about the footage, as the Ask is
KINDS = ("ops", "plan", "answer")
SAW_MAX = 900                          # what the frames showed, with their times, stored on the record
TIME_IN = re.compile(r"\b\d{1,3}:\d{2}")
TEXT_MAX = 2000                        # the editor's words, and an answer's text
PLAN_MAX = 6000                        # a re-cut's brief: whole, cut at a sentence if it ever must be
HISTORY_CHARS = 6000                   # earlier turns the prompt carries (~1,500 tokens), newest first
TURN_CHARS = 1100                      # one earlier turn, at most
SEG_ROWS_MAX = 400                     # shots listed in full; the rest are counted
CLIP_LINE_MAX = 220                    # characters of what is said / seen per clip
CLIPS_BLOCK_MAX = 12000                # characters for the whole clip inventory
OUT_TOKENS_TYPICAL = 1200              # an op list or a short answer (priced before the call)
ETA_S = 40.0                           # a short call on the deep role, for the working line

SYSTEM = (
    "You are the assistant inside Roughcut, a video editor laid out like Adobe Premiere "
    "Pro. The editor asks in plain words; you answer with edits made of the editor's own "
    "tools, which the editor reviews one by one before any of them lands. You never invent "
    "a shot id, a clip name, a look or a preset that is not listed. You answer with JSON only.")

OUTPUT_SCHEMA = {
    "kind": "ops | plan | answer",
    "summary": "one short line for the editor: what this does (or answers)",
    "ops": [{"op": "razor", "id": "a shot id from the list", "at": 12.5}],
    "text": "kind answer only: the answer, in a few sentences",
    "note": "kind plan only: the brief for re-cutting the whole sequence",
    "from_picture": "true when the edit depends on what the pictures show (a wobble, a jump, where "
                    "the camera points), which you know only from the written descriptions",
}

# Only when the ask rests on the picture (look_allowed): a first answer may be a request to look.
LOOK_SCHEMA = {
    **OUTPUT_SCHEMA,
    "kind": "ops | plan | answer | look",
    "looks": [{"shots": [9, 10, 11], "film_in": 38.0, "film_out": 43.0,
               "clip": "a Source clip, with in and out in clip seconds, when the cut has no such shot",
               "reason": "what you need to see there", "density": "fine | medium | coarse"}],
    "scan": "true for a question about the whole cut: every shot is looked at, about a frame a second",
}

# The answer after a look: the same shapes, no further look, and what the frames showed.
AFTER_LOOK_SCHEMA = {
    **OUTPUT_SCHEMA,
    "saw": "what the frames showed, each claim with the film time of the frame it is on (0:40.6)",
    "confirmed": [{"shot": 9, "film_time": 40.6, "what": "what the frames show of that shot, only what is true"}],
}


def fit(text: str, limit: int) -> str:
    """`text` within `limit` characters, cut at the end of a sentence or a line, never in
    the middle of one (COLD-17: a brief cut at 2,000 characters read "Lay a")."""
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind(". "), head.rfind(".\n"), head.rfind("\n"), head.rfind("; "))
    if cut < 0:
        cut = head.rfind(" ")
    return (head[:cut + 1] if cut > 0 else head).rstrip()


# What exists in the app to point the editor at (CLUMSY-12: the AI sent an editor to "the
# History panel"). Kept equal to index.html's Window menu by a test.
WINDOW_PANELS = ("Project", "Selects", "Effects", "Source Monitor", "Program Monitor", "Timeline",
                 "Effect Controls", "Designed Effects", "Audio", "Assistant", "Export", "Index",
                 "Events")

APP_GUIDE = (
    "Panels (the Window menu): " + ", ".join(WINDOW_PANELS) + ". Menus: File, Edit, Clip, Sequence, "
    "AI, Window, Help. Undo and redo are Edit › Undo (Ctrl+Z) and Edit › Redo (Ctrl+Shift+Z), "
    "and the arrows at the top right. There is no History panel, no undo list and no version "
    "list. You can take the cut back N of your own accepted changes with the take_back op (it is "
    "reviewed like any edit and one Undo cancels it); anything else the editor takes back with "
    "Undo, or asks for the opposite edit and you propose it. Name only panels and commands "
    "from this list.")

# Words that mean an edit rests on what the picture shows. The index gives the AI a written
# description of each clip and its transcript, never the frames themselves (wave 24 will let
# it look); until then a proposal on such words says so first (WORDS-08, -17).
PICTURE_WORDS = re.compile(
    r"\b(wobble|wobbly|shak(?:e|y|ing)|jitter\w*|blur\w*|out of focus|in focus|jump(?:s|ing|ed)?|"
    r"tricks?|crash\w*|falls?|flips?|spins?|horizon|tilt\w*|framing|looks? (?:like|good|bad)|"
    r"on screen|visible|snow spray|best skiing|lens|camera (?:is|was|at|on|pointing|pointed))\b", re.I)
# More cues that the ask rests on the picture, for the gate on looking (`look_allowed`).
SEEN_WORDS = re.compile(
    r"\b(ground|sky|pointing|points?|waves?|waving|smil\w+|upside|sideways|nobody|empty|in (?:the )?frame|"
    r"in shot|shows?|showing|seen|sees?|look(?:s|ing)?|faces?|people|person|trees?|lift|spray|biggest|"
    r"best|worst|brightest|darkest|level|crooked|straight|dirty|glove|finger|thumb|sunset|sunrise)\b", re.I)
# Words that mean the words and the index already answer it (a length, a swap, a spoken line):
# no look, because looks cost time (EYES (c)).
PLAIN_WORDS = re.compile(
    r"\b(swap|swapped|reorder|rearrange|shorten|tighten|longer|shorter|seconds?|minutes?|length|duration|trim|"
    r"fade|dissolve|transition|export|marker|colou?r|warmer|cooler|saturation|contrast|volume|louder|"
    r"quieter|music|title|says?|said|spoken|sentence|transcript|undo|redo|first|last|second|third|move|"
    r"delete shot|remove shot|speed up|duplicate|rename)\b", re.I)


def look_allowed(text: str, history: list[str] | None = None) -> bool:
    """Whether the AI may ask to look at frames for these words. Not when the ask is plainly
    about the cut's structure or its sound and says nothing about the picture ("swap the
    last two shots", "trim shot 3 to 5 seconds", "end on the funniest line"): the index
    answers those, and a look costs time. A short answer to the AI's own offer ("the second
    one", "do both") is about whatever the offer was about: when its last turn rested on the
    picture, it may look. Everything else the model decides."""
    text = text or ""
    pic = bool(PICTURE_WORDS.search(text) or SEEN_WORDS.search(text))
    if pic:
        return True
    if PLAIN_WORDS.search(text):
        last = (history or [""])[-1]
        return len(text.split()) <= 10 and bool(
            re.search(r"looked at|frames|picture|description|opening|opens|open on", last, re.I)
            or PICTURE_WORDS.search(last) or SEEN_WORDS.search(last))
    return True


# An ask that names something by what it shows ("the lift scene", "where he waves"): the
# descriptions rarely settle which shot that is, and a guess from the playhead is the miss
# the director caught (WORDS-17). Said about a spoken line it is not a picture.
NAMES_PICTURE = re.compile(
    r"\b(?:the|that|this) (?:[\w'-]+ ){0,2}(?:scene|moment|part|bit)\b|"
    r"\bwhere (?:he|she|they|someone|somebody|it|the \w+) \w+|"
    r"\bwhen (?:he|she|they|someone|somebody) \w+", re.I)
SPOKEN_WORDS = re.compile(r"\b(say|says|said|saying|joke|line|word|words|quote|sentence|talk\w*|speech)\b", re.I)


def names_something_seen(text: str) -> bool:
    text = text or ""
    return bool(NAMES_PICTURE.search(text)) and not SPOKEN_WORDS.search(text)


FROM_TEXT = ("From the written descriptions and the transcript; I have not looked at these frames. ")
LOOKED_WORDS = re.compile(r"not looked|have not seen|haven't looked|did not look|from the description|"
                          r"written description|frames i looked|looked at|from the frames|i looked", re.I)


# A length the editor says is the length of the whole film ("make it about 75 seconds"). Not
# a second deliverable ("a 20 second vertical version"), and not a shot's own length.
LENGTH_WORDS = re.compile(
    r"(?<![\d:.])(\d{1,3}(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?)\b|(?<![\d:])(\d{1,2}):(\d{2})(?![\d:])", re.I)
OTHER_DELIVERABLE = re.compile(r"vertical|instagram|tiktok|reel|short version|teaser|trailer|copy|another version|"
                               r"separate|new cut|shot \d|clip|trim|second shot|third shot|last shot|first shot", re.I)


def length_asked(text: str) -> float | None:
    """The film length in seconds the editor named for the whole cut, else None."""
    text = text or ""
    if OTHER_DELIVERABLE.search(text):
        return None
    m = LENGTH_WORDS.search(text)
    if not m:
        return None
    if m.group(1):
        v = float(m.group(1))
        v *= 60 if m.group(2).lower().startswith("m") else 1
    else:
        v = int(m.group(3)) * 60 + int(m.group(4))
    return v if 10 <= v <= 3600 else None


# ---------------------------------------------------------------- the prompt

def _seg_controls(seg: dict) -> str:
    c = seg.get("controls") if isinstance(seg.get("controls"), dict) else {}
    bits: list[str] = []
    for k in ("scale", "rotation", "opacity", "volume_db", "contrast", "highlights", "shadows",
              "whites", "blacks", "saturation", "vignette_amount", "vignette_midpoint",
              "vignette_roundness", "vignette_feather"):
        if k in c:
            bits.append(f"{k} {c[k]:g}" if isinstance(c[k], (int, float)) else k)
    if isinstance(c.get("position"), list):
        p = c["position"]
        bits.append(f"position ({p[0] * actions.FRAME_W:.0f}, {p[1] * actions.FRAME_H:.0f}) px")
    if isinstance(c.get("keys"), dict) and c["keys"]:
        bits.append("keyframed " + "/".join(sorted(c["keys"])))
    for k in ("curves", "wheels", "hsl"):
        if c.get(k):
            bits.append(f"lumetri {k}")
    vfx = [e.get("kind") for e in c.get("vfx") or [] if isinstance(e, dict)]
    if vfx:
        bits.append("video effects " + "/".join(str(k) for k in vfx))
    if c.get("fx_off"):
        bits.append("designed effects bypassed")
    return ", ".join(bits)


def _colour_line(sid: str, colour: dict) -> str:
    o = ((colour or {}).get("shots") or {}).get(sid) or {}
    bits = []
    if "look" in o:
        bits.append(f"look {o['look'] or 'none'}")
    if "strength" in o:
        bits.append(f"strength {o['strength']:g}")
    if o.get("match"):
        bits.append(f"matched to {o['match']}")
    if o.get("balance"):
        bits.append("hand balance")
    if o.get("auto") is False:
        bits.append("auto balance off")
    return ", ".join(bits)


def _clip_name(clip: str) -> str:
    return str(clip)


# ---------------------------------------------------------------- shots in words

# A shot's id is the server's (`g` + ten hex digits, server.py `_mint`): the model needs it
# for the ops, and the editor never sees it (verdict 12, punch 6: "The last shot
# (g5848fde311) then moves to the start of the film"). The board's `clipNamed` (app.js)
# does the same on whatever model text reaches the page.
SHOT_ID = re.compile(r"\((g[0-9a-f]{10})\)|\b([Ss]hot\s+)?(g[0-9a-f]{10})\b")


def shot_label(i: int, seg: dict) -> str:
    """A shot as the editor reads it: its row in the sequence and its clip — "shot 4
    (CLIP_02)", a generated clip by its Premiere name ("shot 9 (Black Video)")."""
    return f"shot {i + 1} ({clip_name(seg.get('clip'))})"


def name_shots(text: Any, segments: list[dict] | None) -> Any:
    """`text` with every shot id in it said as the editor says the shot: "g5848fde311" →
    "shot 22 (CLIP_05)", "Shot g5848fde311" → "Shot 22 (CLIP_05)" (the capital kept),
    "(g5848fde311)" → "(shot 22)"; an id that is not in `segments` → "a shot" / "that
    shot". Anything that is not a string comes back as it was."""
    if not isinstance(text, str) or "g" not in text:
        return text
    index = {str(s.get("id")): (i, s) for i, s in enumerate(segments or []) if s.get("id")}

    def rep(m: re.Match) -> str:
        if m.group(1):                                   # "(g…)": the number alone
            hit = index.get(m.group(1))
            return f"(shot {hit[0] + 1})" if hit else "(another shot)"
        pre, sid = m.group(2) or "", m.group(3)
        # a capital where the model wrote one, or where a sentence starts
        cap = pre[:1] == "S" or (not pre and re.search(r"(^|[.!?]\s+)$", text[:m.start()]) is not None)
        hit = index.get(sid)
        if hit is None:
            word = "that shot" if pre else "a shot"
        else:
            # the clip follows unless the model already named it: "shot g… (CLIP_05)"
            named = text[m.end():m.end() + 2] == " ("
            word = f"shot {hit[0] + 1}" if named else shot_label(*hit)
        return word[:1].upper() + word[1:] if cap else word

    return SHOT_ID.sub(rep, text)


def _named(out: dict, segments: list[dict]) -> dict:
    """An answer's words — its summary, its text, the re-cut's brief, the lines, the
    refusals and a design's note — with the shots named as the editor names them
    (`name_shots`). The ops keep their ids: they are the tools' arguments, never shown."""
    for k in ("summary", "text", "note"):
        if k in out:
            out[k] = name_shots(out[k], segments)
    if isinstance(out.get("lines"), list):
        out["lines"] = [name_shots(x, segments) for x in out["lines"]]
    for r in out.get("refused") or []:
        if isinstance(r, dict) and "why" in r:
            r["why"] = name_shots(r["why"], segments)
    for op in out.get("ops") or []:
        if isinstance(op, dict) and op.get("op") == "design_effect" and "note" in op:
            op["note"] = name_shots(op["note"], segments)
    return out


def _marker_text(m, tc) -> str:
    """One marker as the model reads it: its time, and its name, colour, duration and
    comment when it has them (an EDL marker is a bare time or {t, name?, …})."""
    t = tracksmod.marker_time(m)
    out = f"{t:.2f} s ({tc(t)})"
    if isinstance(m, dict):
        if m.get("name"):
            out += f" “{str(m['name'])[:80]}”"
        if m.get("colour") and m["colour"] != "green":
            out += f" {str(m['colour']).title()}"
        if isinstance(m.get("dur"), (int, float)) and m["dur"] > 0:
            out += f" {float(m['dur']):.2f} s long"
        if m.get("comment"):
            out += f" comment: {str(m['comment'])[:80]}"
    return out


def sequence_block(state: dict) -> str:
    fps = float(state.get("fps") or 24.0)
    tc = lambda t: actions.timecode(t, fps)  # noqa: E731
    segs = state.get("segments") or []
    colour = state.get("colour") or {}
    rows: list[str] = []
    t = 0.0
    for i, s in enumerate(segs):
        d = actions.dur(s)
        if i < SEG_ROWS_MAX:
            extra = "; ".join(x for x in (
                f"speed {actions.speed_of(s):g}×" if actions.speed_of(s) != 1 else "",
                _seg_controls(s), _colour_line(str(s.get("id")), colour),
                f"why: {str(s.get('why'))[:80]}" if s.get("why") else "") if x)
            rows.append(f"  {i + 1:>3}  {s.get('id')}  {_clip_name(s['clip'])}  clip {float(s['in']):.2f}–"
                        f"{float(s['out']):.2f} s  film {t:.2f}–{t + d:.2f} s ({tc(t)}–{tc(t + d)})"
                        + (f"  [{extra}]" if extra else ""))
        t += d
    if len(segs) > SEG_ROWS_MAX:
        rows.append(f"  … and {len(segs) - SEG_ROWS_MAX} more shots")
    lines = [f"{len(segs)} shots, {t:.2f} s ({tc(t)}) at {fps:g} fps."
             + (f" The frame is {state['frame']} (Sequence Settings)."
                if state.get("frame") and state["frame"] != "16:9" else "")]
    lines.append("   #  id  clip  clip range  film time  [effect controls, colour, why]")
    lines.extend(rows or ["  (the sequence is empty)"])
    mk = [m for m in state.get("markers") or [] if tracksmod.marker_time(m) is not None]
    lines.append("Markers: " + ("; ".join(_marker_text(m, tc) for m in mk[:40]) if mk else "none"))
    a, b = state.get("in"), state.get("out")
    lines.append("Sequence In / Out: " + (f"{'none' if a is None else f'{a:.2f} s'} / "
                                          f"{'none' if b is None else f'{b:.2f} s'}"))
    tr = state.get("tracks") or {}
    extra_tracks = sorted(n for n in tr if n not in ("V1", "V2", "A1", "A2"))
    lines.append("Tracks: V1 V2 A1 A2" + (" " + " ".join(extra_tracks) if extra_tracks else ""))
    ov = state.get("overlays") or []
    if ov:
        lines.append("Clips over the cut: " + "; ".join(
            f"{o.get('id')} {o.get('clip')} {float(o.get('in', 0)):.2f}–{float(o.get('out', 0)):.2f} s on "
            f"{o.get('track') or 'V2'} at {float(o.get('at', 0)):.2f} s" for o in ov[:30]))
    trs = state.get("transitions") or []
    if trs:
        lines.append("Transitions: " + "; ".join(
            f"{x.get('kind')} {x.get('a') or '—'}|{x.get('b') or '—'} {float(x.get('dur', 0)):.2f} s"
            for x in trs[:40]))
    film = colour or {}
    lines.append("Colour: film look " + str(film.get("look") or "none")
                 + f" at {float(film.get('strength', 0.5)):g}"
                 + (f"; reference shot {film['reference']}" if film.get("reference") else "")
                 + ("; auto balance off" if film.get("mode") == "off" else ""))
    return "\n".join(lines)


def scope_block(scope: dict | None, state: dict) -> str:
    """The scope in words, with the shots it covers — the model acts there unless the
    request plainly says otherwise."""
    sc = scope if isinstance(scope, dict) else {}
    fps = float(state.get("fps") or 24.0)
    tc = lambda t: actions.timecode(t, fps)  # noqa: E731
    segs = state.get("segments") or []
    index = {str(s.get("id")): i for i, s in enumerate(segs)}
    kind = sc.get("kind") or "sequence"
    out: list[str] = []
    if kind == "selection":
        ids = [str(x) for x in (sc.get("ids") or [])]
        named = [f"{shot_label(index[i], segs[index[i]])} [id {i}]" for i in ids if i in index]
        out.append("The selected shots: " + (", ".join(named) if named else "(none of them is in the cut any more)"))
    elif kind == "range":
        a, b = sc.get("in"), sc.get("out")
        try:
            a, b = float(a), float(b)
        except (TypeError, ValueError):
            a, b = None, None
        if a is None or b is None:
            out.append("A range of the sequence (no times given)")
        else:
            t, inside = 0.0, []
            for i, s in enumerate(segs):
                d = actions.dur(s)
                if t < b and t + d > a:
                    inside.append(f"{shot_label(i, s)} [id {s.get('id')}]")
                t += d
            out.append(f"The range {a:.2f}–{b:.2f} s ({tc(a)}–{tc(b)}) of the sequence: "
                       + (", ".join(inside) if inside else "no shots"))
    elif kind == "clip":
        clip = str(sc.get("clip") or "")
        rng = ""
        if sc.get("in") is not None and sc.get("out") is not None:
            rng = f", its In {float(sc['in']):.2f} s to Out {float(sc['out']):.2f} s"
        # the prompt's 'clip' scope is the Source monitor's clip or a Project panel clip
        # ("Ask AI about this…" on its row): a clip of the project, not a shot of the cut
        out.append(f"The clip {clip} (in the Source monitor or the Project panel; not a shot in the "
                   f"sequence){rng}")
    else:
        out.append("The whole sequence")
    ph = sc.get("playhead")
    try:
        ph = float(ph)
    except (TypeError, ValueError):
        ph = None
    if ph is not None:
        t, where = 0.0, ""
        for i, s in enumerate(segs):
            d = actions.dur(s)
            if t <= ph < t + d:
                where = f" ({shot_label(i, s)}, {(ph - t):.2f} s in)"
                break
            t += d
        out.append(f"The playhead is at {ph:.2f} s ({tc(ph)}){where}.")
    return "\n".join(out)


def _said(clip: dict) -> str:
    tr = clip.get("transcript") or []
    words = " ".join(str(u.get("text", "")).strip() for u in tr if isinstance(u, dict))
    return words.strip()


def clips_block(clips: dict) -> str:
    rows: list[str] = []
    used = 0
    names = sorted(clips)
    for k, name in enumerate(names):
        c = clips[name] or {}
        d = c.get("duration")
        head = f"  {name}  {float(d):.2f} s" if isinstance(d, (int, float)) else f"  {name}"
        bits = []
        said = _said(c)
        if said:
            bits.append(f"says: “{said}”")
        vis = c.get("visual") if isinstance(c.get("visual"), dict) else {}
        if vis.get("summary"):
            bits.append(f"seen: {vis['summary']}")
        moments = [m for m in vis.get("moments") or [] if isinstance(m, dict)]
        if moments:
            bits.append("moments: " + "; ".join(
                f"{float(m.get('t', 0)):.1f} s {str(m.get('what') or m.get('label') or m.get('kind') or '')}".strip()
                for m in moments[:4]))
        line = head + (" — " + " · ".join(bits) if bits else "")
        line = line[:CLIP_LINE_MAX]
        if used + len(line) > CLIPS_BLOCK_MAX:
            rows.append(f"  … and {len(names) - k} more clips: " + ", ".join(names[k:k + 40]))
            break
        rows.append(line)
        used += len(line) + 1
    return "\n".join(rows) if rows else "  (no clips)"


WORDS_BLOCK_MAX = 3000                 # characters of word timings in a prompt
WORDS_PER_SHOT = 70
_TOKEN = re.compile(r"[a-z0-9']+")
_STOP = {"the", "a", "an", "and", "or", "to", "of", "on", "in", "at", "it", "is", "that", "this", "out", "cut",
         "words", "word", "add", "then", "with", "for", "so", "up", "me", "my", "i", "you"}


def _norm(w: str) -> str:
    return "".join(_TOKEN.findall(str(w).lower()))


def word_timings_block(text: str, scope: dict | None, state: dict, clips: dict) -> str:
    """The transcript's word timings (clip seconds) for the shots the ask is about, bounded: the
    shots in scope, and any shot whose speech holds a run of two to four of the editor's own
    words ("cut out on 'it's over'"). Whisper timed every word; without them the AI can only
    say it has "no word timings" (WORDS-16). Empty when no such shot has timed words."""
    segs = state.get("segments") or []
    sc = scope if isinstance(scope, dict) else {}
    want: dict[int, tuple[float, float] | None] = {}      # row -> clip-second window, None = all of it
    if sc.get("kind") == "selection":
        for sid in sc.get("ids") or []:
            for i, s in enumerate(segs):
                if str(s.get("id")) == str(sid):
                    want[i] = None
    elif sc.get("kind") == "range":
        try:
            a, b = float(sc["in"]), float(sc["out"])
        except (KeyError, TypeError, ValueError):
            a = b = None
        t = 0.0
        for i, s in enumerate(segs):
            d = actions.dur(s)
            if a is not None and t < b and t + d > a:
                want[i] = None
            t += d
    asked = [_norm(w) for w in _TOKEN.findall((text or "").lower())]
    asked = [w for w in asked if w]
    grams = {tuple(asked[i:i + n]) for n in (4, 3, 2) for i in range(len(asked) - n + 1)
             if any(w not in _STOP for w in asked[i:i + n])}
    for i, s in enumerate(segs):
        if i in want and want[i] is None:
            continue
        words = _timed_words(clips, s)
        if not words or not grams:
            continue
        norm = [_norm(w["w"]) for w in words]
        for n in (4, 3, 2):
            hit = next((k for k in range(len(norm) - n + 1) if tuple(norm[k:k + n]) in grams), None)
            if hit is not None:
                want[i] = (words[hit]["t"] - 4.0, words[hit + n - 1]["e"] + 4.0)
                break
    lines: list[str] = []
    used = 0
    for i in sorted(want)[:8]:
        s = segs[i]
        words = _timed_words(clips, s)
        if want[i] is not None:
            lo, hi = want[i]
            words = [w for w in words if w["e"] >= lo and w["t"] <= hi]
        if not words:
            continue
        cells = [f"{w['w'].strip()} {w['t']:.2f}-{w['e']:.2f}" for w in words[:WORDS_PER_SHOT]]
        line = f"{shot_label(i, s)} [id {s.get('id')}]: " + " | ".join(cells)
        if used + len(line) > WORDS_BLOCK_MAX:
            break
        lines.append(line)
        used += len(line)
    if not lines:
        return ""
    return ("## Word timings (clip seconds: word start-end) for the shots this ask is about\n"
            "Every word is timed. To cut on a word, set a trim's `out` to the word's end (or `in` to its "
            "start), or razor at it, using these times. Never say you have no word timings.\n"
            + "\n".join(lines) + "\n\n")


def _timed_words(clips: dict, seg: dict) -> list[dict]:
    """The timed words of the clip that fall inside the shot's source range."""
    c = (clips or {}).get(seg.get("clip")) or {}
    try:
        a, b = float(seg["in"]), float(seg["out"])
    except (KeyError, TypeError, ValueError):
        return []
    out = []
    for u in c.get("transcript") or []:
        for w in (u.get("words") or []) if isinstance(u, dict) else []:
            try:
                if float(w["e"]) >= a and float(w["t"]) <= b:
                    out.append({"w": str(w["w"]), "t": float(w["t"]), "e": float(w["e"])})
            except (KeyError, TypeError, ValueError):
                continue
    return out


def history_block(history: list[str] | None, ctx: dict | None = None) -> str:
    """The earlier turns of this cut, oldest first, as the server wrote them, and above them the
    complete list of changes accepted on this cut (the conversation is bounded; the list is not)."""
    ctx = ctx or {}
    out = ""
    if ctx.get("changes"):
        out += ("## Every change accepted on this cut this session (complete, oldest first)\n"
                "Count from this list, not from the conversation below, when asked what you changed. "
                "\"in\" = it is in the cut now; \"undone\" and \"taken back\" = it is not.\n"
                + "\n".join(ctx["changes"]) + "\n\n")
    if history:
        out += ("## Earlier in this conversation (this cut only, oldest first)\n"
                + (f"My record of what we said goes back to {ctx['horizon']}; the list of changes above is "
                   "complete. When the editor asks about anything earlier than that, say so first.\n"
                   if ctx.get("horizon") else "")
                + "Shot numbers are as they were when each turn happened; the sequence below is how it "
                "is now. \"Both\", \"the second one\", \"put it back\", \"what did you just change\" "
                "refer to these turns.\n" + "\n".join(history) + "\n\n")
    return out


SEE_BLIND = """## What you can and cannot see
You have the transcript and a short written description of each clip. You have not looked at
any frames. When an edit depends on what the pictures show (a wobble, a jump, where the camera
points, how a shot looks), set `from_picture` to true, and say in the first words of `summary`
that it comes from the descriptions and not from the frames. When the editor states a visual
fact the descriptions do not confirm ("there is a wobble at 0:40"), do not cut on it: answer
(kind "answer") that you cannot find it in what you know, say what the descriptions do say near
that time, and offer to look closer (AI › Look deeper at the selected clip). A clip the
description marks as unconfirmed stays unconfirmed in your answer. Never say you saw something.
If the request contradicts itself ("make it longer but cut half the shots"), answer with the
conflict and ask which matters more, instead of doing one of them.
"""

SEE_CAN_LOOK = """## What you can and cannot see
You have the transcript and a short written description of each clip. You have not looked at
any frames, but you can ask to, once, and it costs the editor time (a minute or so), so only
when the answer rests on what the picture shows (a wobble, a jump, where the camera points,
"the shot where he waves", whether the horizon is level, anything the descriptions do not
already settle). When the words, the shot table or the descriptions answer the ask, do NOT
look: a length, a swap, a spoken line, a colour, a transition, an effect.
To look, answer `kind: "look"` instead of an answer, with:
  - `looks`: up to {items} ranges, each by `shots` (the row numbers above), or by `film_in` /
    `film_out` (seconds of the sequence, as the editor reads the timeline), or by `clip` with
    `in` / `out` (a Source clip not in the cut); each with a `reason` and a `density`:
    "fine" 4 frames a second (a wobble, a landing, a quick move), "medium" 2, "coarse" 1.
    Pick the smallest ranges that settle it; a range is at most {span:g} s.
  - or `scan: true` for a question about the whole cut ("every bit where the camera points at
    the ground or the sky", "every shot where nobody is in frame"): every shot is looked at,
    about a frame a second.
When the editor names a shot or moment by what it shows ("the lift scene", "where he waves",
"the second one" of two openings you described by their pictures) and the descriptions do not
settle which shot or moment it is, look first and place the edit on what the frames show; never
pick it from the playhead or from a word in the audio. Where an edit starts a shot (above all
the shot that opens the film), read what the description says is in its first frames (a sleeve,
a glove, a black frame) and start after it.
A question about "the biggest", "the best" or "every" something in the picture, with no time
or shot named, is a scan: someone saying "jump" in the audio is not a jump in the frames, so
do not pick shots from the words alone. A named time or shot is a range.
The server cuts the frames from the proxies, caps them ({cap} frames for ranges, {scan} for a
scan; it thins the density to fit and tells the editor), shows the editor what it is looking
at, and calls you again with the contact sheets and this same conversation. You then answer
for real (ops, plan or answer); you cannot ask to look a second time.
If the editor states a visual fact ("a wobble at about 0:40"), look at it before you cut on
it, and take a wide enough range: their time is approximate. If you do not look,
do not cut on it: answer (kind "answer") that you cannot find it in what you know. Any edit that rests on the
picture sets `from_picture` to true and says in the first words of `summary` that it comes from
the descriptions and not from the frames. Never say you saw something you
did not look at. Never say both that you have not looked and that you looked: if you looked
earlier in this conversation, say where and when ("from the frames I looked at at 11:20"), and
if you did not, say you did not. A clip the description marks as unconfirmed stays unconfirmed unless the
frames you look at settle it.
If the request contradicts itself ("make it longer but cut half the shots"), answer with the
conflict and ask which matters more, instead of doing one of them.
""".format(items=lookmod.MAX_ITEMS, span=lookmod.MAX_SPAN_S, cap=lookmod.LOOK_CAP, scan=lookmod.SCAN_CAP)


def looked_block(first: dict, plan_: dict, sheets_text: str, said: str) -> str:
    """What the second call is given beside the first prompt: its own request, the plan the
    server cut it down to, and the account of the attached sheets."""
    return f"""

## Your first answer (a request to look)
{said}

## What you are now looking at
The server cut your request to: {plan_['words']}. The frames are attached as contact sheets
(images), in film order. Each cell is one frame, labelled in yellow with its film time
(minutes:seconds.tenth, as the timeline reads) and its shot number, e.g. "0:40.6 s9". The
frames are stills, {('about a frame a second' if plan_['scan'] else 'some a quarter of a second apart')}: camera shake shows as
framing that jumps or smears between neighbours, not as motion you can watch.
{sheets_text}

Now answer for real. Say what the frames show, and put the film time of the frame beside
each thing you say you saw ("the camera jolts at 0:40.6"). Claim nothing about a time you
did not look at. If you looked and did not find what the editor described, answer
(kind "answer") that you looked at those times and did not find it, and say what you did see;
propose no edit. Put what the frames showed, with their times, in `saw`, and in `confirmed` each shot whose frames
you looked at with what they show (only what you saw and is true of that shot; leave out what you did not see). Your ops use the
shot ids and clip seconds from the tables above (a film time is the shot's film start plus
the offset into it). You cannot look again.
"""


def build_prompt(text: str, scope: dict | None, state: dict, clips: dict,
                 history: list[str] | None = None, look_ok: bool = False, ctx: dict | None = None) -> str:
    looks = ", ".join(state.get("looks") or []) or "(none)"
    presets = ", ".join(f"{k} ({v})" for k, v in (state.get("presets") or {}).items()) or "(none)"
    return f"""{history_block(history, ctx)}The editor asked now:
“{text}”

## Scope (what the editor had selected when they asked; act there unless the words plainly say otherwise)
{scope_block(scope, state)}

## The sequence
{sequence_block(state)}

{word_timings_block(text, scope, state, clips)}## The clips in the project (clip seconds; what is said and seen)
{clips_block(clips)}

## The library
Looks: {looks}
Export presets: {presets}

## The tools you can use (the action vocabulary)
{actions.schema_text()}
## How editors say it
- "shot 4" is row 4 of the sequence above; "the intro" is the first shots, "the end" the last.
- warmer / cooler → set temperature (about ±15 to ±40); brighter / darker → exposure (±0.1 to
  ±0.4); punchier → contrast (+10 to +30); less colour → saturation (60 to 90).
- slow motion → speed 0.5 (0.25 for very slow); faster → speed 1.5 to 2.
- tighten or shorten → trim the in or out (clip seconds, inside the clip); cut at a moment →
  razor; take out → ripple_delete; swap or reorder → move.
- a dissolve between two shots → transition cross_dissolve (audio_crossfade for the sound);
  fade in / out → fade with dip_to_black at the start / end.
- a vignette (darker or lighter edges or corners, "draw the eye to the middle") → vignette,
  Lumetri's own: amount −1 to −2 with feather 60 to 80 for a soft one, −3 or less for a
  heavy one; never a design_effect.
- sharper / crisper → effect sharpen (amount 50 to 150); softer / dreamy → effect
  gaussian_blur; crop the edges → effect crop.
- a marker (M) at a moment → marker at that film time (a shot's start is its film time in the
  table above; "at 0:12" is 12 s); "called / named X" is its `name`, a note on it its
  `comment`, "a red marker" its `colour`, "mark the whole jump" a `duration` to the moment it
  ends; naming a marker already there is a marker op at its time with the name. A chapter of
  the export is a named marker. Never a design_effect for a marker.
- a title, a graphic, hit markers, an arrow → design_effect with a note saying what and where
  (only what no tool above draws).
- a review copy, a file to upload, the final file → export with quick, youtube or master.
- a vertical / phone / Instagram / TikTok / Reels / Shorts / square version, or a shorter cut
  "for" a place → a SECOND DELIVERABLE: the first op is `new_cut` (a saved copy, named from the
  ask, `frame` "9:16" for vertical and "1:1" for square), the ops after it shorten and shape the
  copy (ripple_delete and trim to the length asked; keep the opening and the best moments; the
  copy's length is the sum of the kept shots' lengths in the table, so count it and land within
  a second of the ask). The cut the editor is on is NEVER shortened or reshaped for these, and
  the `summary` begins "A new cut, <name>:" and says that the cut they are on stays as it is.
  Only to change THIS cut's own shape ("make this sequence vertical") is the `frame` op used.

## The app (name only what exists here)
{APP_GUIDE}

{SEE_CAN_LOOK if look_ok else SEE_BLIND}
## What to answer
JSON with `kind`:
- "ops": the request is one or more edits the tools above make. `ops` lists them in the
  order to apply them; `summary` says in one line what they do. Use exact ids from the
  sequence and times from the tables. Prefer the fewest ops that do what was asked.
- "plan": the request rewrites the whole cut (a new structure, a new length for the whole
  film, "make a teaser", "cut it to the music") — something better done by re-cutting than
  by a handful of edits. (A second deliverable — a vertical, Instagram or other version — is
  never a plan: a plan replaces the cut the editor is on. It is `ops` starting with new_cut.) `note` is the brief for the re-cut, in the editor's terms: only what
  the editor asked, plus the shots and moments they named; the cutter reads the same written
  descriptions you do, so never tell it to choose "from the pictures". Under 1,500 characters, whole
  sentences, no instruction the editor did not give, and never "you asked" for your own idea.
- "answer": the request is a question; `text` answers it from what is listed above. No ops.

The editor reads `summary`, `text`, `note` and every design_effect `note`: there a shot is
"shot N (CLIP)" — its row in the sequence and its clip without the extension, "shot 4
(CLIP_02)" — never its id. Ids go only in the ops' id fields.
"""


# ---------------------------------------------------------------- the answer

def validate_answer(payload: Any, allow_look: bool = False, after_look: bool = False) -> dict:
    """The model's JSON, shaped: a kind, a summary, and the field that kind needs. The
    ops themselves are checked by `actions.validate` (a bad op is refused, not re-asked).
    `allow_look`: the first answer of an ask that may look may be `kind: "look"`.
    `after_look`: the answer to the frames: no further look, and what it saw is told with
    the film times of the frames."""
    if not isinstance(payload, dict):
        raise ValueError("expected an object with `kind`")
    kind = str(payload.get("kind") or "").strip().lower()
    if kind == "look":
        if not allow_look:
            raise ValueError("`kind` look is not available now: answer from what you have"
                             + (" and the frames you were given; you cannot look again" if after_look else
                                " (the descriptions and the transcript), as ops, plan or answer"))
        return {"kind": "look", **lookmod.clean_request(payload)}
    if kind not in KINDS:
        if isinstance(payload.get("ops"), list) and payload["ops"]:
            kind = "ops"
        else:
            raise ValueError(f"`kind` must be one of {KINDS}")
    out = {"kind": kind, "summary": str(payload.get("summary") or "").strip()[:300]}
    if kind == "ops":
        ops = payload.get("ops")
        if not isinstance(ops, list) or not ops:
            raise ValueError("kind ops needs a non-empty `ops` list")
        out["ops"] = ops[: actions.MAX_OPS * 2]
    elif kind == "plan":
        out["note"] = fit(payload.get("note") or payload.get("summary") or "", PLAN_MAX)
    else:
        txt = str(payload.get("text") or payload.get("summary") or "").strip()
        if not txt:
            raise ValueError("kind answer needs `text`")
        out["text"] = fit(txt, TEXT_MAX)
    out["from_picture"] = payload.get("from_picture") is True
    if after_look:
        saw = str(payload.get("saw") or "").strip()
        if kind == "ops" and not TIME_IN.search(saw):
            raise ValueError("`saw` must say what the frames showed with the film time of each "
                             "frame (like 0:40.6) when you propose an edit from them")
        out["saw"] = fit(saw, SAW_MAX)
        out["confirmed"] = [c for c in (payload.get("confirmed") or []) if isinstance(c, dict)][:12]
    return out


def clean_confirmed(items: Any, segments: list[dict]) -> list[dict]:
    """What a look settled about a shot, as the board can keep it: `{clip, in, out, what}` of the
    shot the frames were of (its whole clip range), from the model's `{shot, what}`."""
    out = []
    for c in items or []:
        try:
            i = int(c.get("shot")) - 1
            seg = segments[i]
        except (TypeError, ValueError, IndexError, AttributeError):
            continue
        what = fit(str(c.get("what") or ""), 160)
        if what and seg.get("clip") is not None:
            out.append({"clip": str(seg["clip"]), "in": float(seg["in"]), "out": float(seg["out"]), "what": what})
    return out


def honest_summary(out: dict, text: str, flagged: bool, looked: dict | None = None) -> dict:
    """An edit that rests on the picture, from an AI that has only the descriptions, says so
    in its first line. The model is asked to; the editor's words and its own summary are
    checked here as well, because a model that writes "so the shot picks up just after the
    wobble" has not said it (WORDS-08, -17)."""
    summary = out.get("summary") or ""
    if looked:
        # it looked: the line says where, once, and the descriptions' disclaimer is not true
        out["from_picture"] = True
        out["from_descriptions"] = False
        if "looked" not in summary[:60].lower():
            out["summary"] = f"Looked at {looked['where']}: {summary}".strip()[:420]
        return out
    rests = flagged or bool(PICTURE_WORDS.search(text or "")) or bool(PICTURE_WORDS.search(summary))
    out["from_picture"] = rests
    if rests and not LOOKED_WORDS.search(summary[:160]):
        out["summary"] = (FROM_TEXT + summary).strip()[:420]
        out["from_descriptions"] = True
    return out


def estimate_cost(prompt: str) -> dict:
    """The call priced before it runs: the prompt's tokens (≈ 4 characters each) and a
    typical answer, at the role's model."""
    model = config.model_for(ROLE)
    tin = (len(prompt) + len(SYSTEM)) // 4
    usd = config.projected_usd(model, tin, OUT_TOKENS_TYPICAL)
    return {**cost_words(usd), "projected_usd": round(usd, 4), "measured": False, "model": model,
            "basis": f"~{tin:,} tokens in and ~{OUT_TOKENS_TYPICAL:,} out, priced for {model}"}


def run(text: str, scope: dict | None, state: dict, clips: dict,
        history: list[str] | None = None, looker: Callable[[dict, str], dict | None] | None = None,
        ctx: dict | None = None) -> dict:
    """One call, or two when the ask rests on the picture. Returns `{kind, summary, ops?,
    lines?, refused?, preview?, families?, text?, note?, cost, looked?}`; raises
    `inference.InferenceError` / `ValueError` on a failed call (the server's job marks
    itself failed with the sentence).

    `looker(request, prompt)` is the server's: given the model's look request it plans the
    frames under the cap, tells the editor, cuts the sheets and returns `{plan, sheets,
    images, block, record}` (or raises `look.Stopped`). Without one, or when `look_allowed`
    says the words need no picture, the model is not offered the look at all."""
    look_ok = looker is not None and look_allowed(text, history)
    prompt = build_prompt(text, scope, state, clips, history, look_ok=look_ok, ctx=ctx)
    result = complete(prompt, role=ROLE, schema=LOOK_SCHEMA if look_ok else OUTPUT_SCHEMA, system=SYSTEM,
                      validate=lambda p: validate_answer(p, allow_look=look_ok), retries=1)
    calls = [result]
    got = dict(result.content)
    looked = None
    new_footage = got["kind"] == "ops" and any(
        isinstance(o, dict) and o.get("op") in ("insert", "overwrite", "append") for o in got.get("ops") or [])
    if look_ok and ((got["kind"] in ("ops", "plan") and names_something_seen(text)) or new_footage):
        # it named something by what it shows and went straight to an edit, or it chose seconds of new
        # footage from a description (the opening that began on a sleeve, WORDS-06): which shot or
        # moment, which seconds, was a guess from the descriptions or the playhead. Ask again, to look first.
        why = ("The editor named something by what it shows. Which shot or moment it is was not settled "
               "by the descriptions, so do not place the edit yet: answer kind \"look\" for the shots that "
               "might be it (a few shots, `density` coarse or medium)."
               if names_something_seen(text) else
               "You chose seconds of a clip from its description. Do not place them yet: answer kind "
               "\"look\" at the clip seconds you would use (`clip` with `in` and `out`, a little wider "
               "on both sides, `density` medium), so the first frames are ones you have seen.")
        again = complete(prompt + "\n\n## Your first answer\n" + json.dumps(
            {"kind": got["kind"], "summary": got.get("summary"), "ops": got.get("ops")}) + "\n\n" + why,
            role=ROLE, schema=LOOK_SCHEMA, system=SYSTEM,
            validate=lambda p: validate_answer(p, allow_look=True), retries=1)
        calls.append(again)
        if again.content.get("kind") == "look":
            got = dict(again.content)
    if got["kind"] == "look":
        req = {"scan": got["scan"], "looks": got["looks"]}
        seen = looker(req, prompt)
        if seen and seen.get("images"):
            prompt2 = prompt + looked_block(req, seen["plan"], seen["block"], json.dumps(req))
            second = complete(prompt2, role=ROLE, images=seen["images"], schema=AFTER_LOOK_SCHEMA,
                              system=SYSTEM, retries=1, isolated=True,
                              validate=lambda p: validate_answer(p, after_look=True))
            calls.append(second)
            got = dict(second.content)
            looked = {**seen["record"], "saw": got.get("saw") or "",
                      "confirmed": clean_confirmed(got.get("confirmed"), state.get("segments") or [])}
        else:
            second = complete(prompt + "\n\n## Your first answer (a request to look)\n" + json.dumps(req)
                              + "\n\nNone of the ranges you named is in the cut, so nothing was looked at. "
                                "Answer from the descriptions and the transcript, and say you have not looked.",
                              role=ROLE, schema=OUTPUT_SCHEMA, system=SYSTEM, retries=1,
                              validate=lambda p: validate_answer(p))
            calls.append(second)
            got = dict(second.content)
    usd = sum(c.projected_usd for c in calls)
    cost = {**cost_words(usd), "projected_usd": round(usd, 4),
            "measured": True, "model": calls[-1].model,
            "input_tokens": sum(c.input_tokens for c in calls),
            "output_tokens": sum(c.output_tokens for c in calls)}
    if len(calls) > 1:
        cost["calls"] = len(calls)
    out: dict = {"kind": got["kind"], "summary": got.get("summary") or "", "cost": cost}
    if got["kind"] == "ops":
        checked = actions.check(got["ops"], state)
        out.update(checked)
        if not out["summary"]:
            out["summary"] = "; ".join(checked["lines"][:2])
        if not checked["ops"]:
            # every op refused: say so as the answer, with the reasons
            out["kind"] = "answer"
            out["text"] = ("I could not make that edit: "
                           + "; ".join(r["why"] for r in checked["refused"][:4]))
    elif got["kind"] == "plan":
        out["note"] = got.get("note") or text
    else:
        out["text"] = got["text"]
    if out["kind"] == "ops" and out.get("ops"):
        out = honest_summary(out, text, bool(got.get("from_picture")), looked)
    if looked:
        out["looked"] = looked
        if out["kind"] == "answer" and "looked" not in out["text"][:80].lower():
            out["text"] = f"Looked at {looked['where']}. " + out["text"]
        if looked.get("saw"):
            looked["saw"] = name_shots(looked["saw"], state.get("segments") or [])
    # the editor's words name shots as the editor does, whatever the model wrote
    return _named(out, state.get("segments") or [])
