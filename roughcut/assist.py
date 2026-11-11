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

import re
from typing import Any

from . import actions, config
from . import tracks as tracksmod
from .firstpass import clip_name, cost_words
from .inference import complete

ROLE = config.ROLE_SKELETON            # a judgement about the footage, as the Ask is
KINDS = ("ops", "plan", "answer")
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
    "list; you cannot undo or redo anything yourself. To take an accepted edit back the editor "
    "uses Undo, or asks for the opposite edit and you propose it. Name only panels and commands "
    "from this list.")

# Words that mean an edit rests on what the picture shows. The index gives the AI a written
# description of each clip and its transcript, never the frames themselves (wave 24 will let
# it look); until then a proposal on such words says so first (WORDS-08, -17).
PICTURE_WORDS = re.compile(
    r"\b(wobble|wobbly|shak(?:e|y|ing)|jitter\w*|blur\w*|out of focus|in focus|jump(?:s|ing|ed)?|"
    r"tricks?|crash\w*|falls?|flips?|spins?|horizon|tilt\w*|framing|looks? (?:like|good|bad)|"
    r"on screen|visible|snow spray|best skiing|lens|camera (?:is|was|at|on|pointing|pointed))\b", re.I)
FROM_TEXT = ("From the written descriptions and the transcript; I have not looked at these frames. ")
LOOKED_WORDS = re.compile(r"not looked|have not seen|haven't looked|did not look|from the description|"
                          r"written description", re.I)


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
    lines = [f"{len(segs)} shots, {t:.2f} s ({tc(t)}) at {fps:g} fps."]
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


def history_block(history: list[str] | None) -> str:
    """The earlier turns of this cut, oldest first, as the server wrote them."""
    if not history:
        return ""
    return ("## Earlier in this conversation (this cut only, oldest first)\n"
            "Shot numbers are as they were when each turn happened; the sequence below is how it "
            "is now. \"Both\", \"the second one\", \"put it back\", \"what did you just change\" "
            "refer to these turns.\n" + "\n".join(history) + "\n\n")


def build_prompt(text: str, scope: dict | None, state: dict, clips: dict,
                 history: list[str] | None = None) -> str:
    looks = ", ".join(state.get("looks") or []) or "(none)"
    presets = ", ".join(f"{k} ({v})" for k, v in (state.get("presets") or {}).items()) or "(none)"
    return f"""{history_block(history)}The editor asked now:
“{text}”

## Scope (what the editor had selected when they asked; act there unless the words plainly say otherwise)
{scope_block(scope, state)}

## The sequence
{sequence_block(state)}

## The clips in the project (clip seconds; what is said and seen)
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

## The app (name only what exists here)
{APP_GUIDE}

## What you can and cannot see
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

## What to answer
JSON with `kind`:
- "ops": the request is one or more edits the tools above make. `ops` lists them in the
  order to apply them; `summary` says in one line what they do. Use exact ids from the
  sequence and times from the tables. Prefer the fewest ops that do what was asked.
- "plan": the request rewrites the whole cut (a new structure, a new length for the whole
  film, "make a teaser", "cut it to the music") — something better done by re-cutting than
  by a handful of edits. `note` is the brief for the re-cut, in the editor's terms: only what
  the editor asked, plus the shots and moments they named; the cutter reads the same written
  descriptions you do, so never tell it to choose "from the pictures". Under 1,500 characters, whole
  sentences, no instruction the editor did not give, and never "you asked" for your own idea.
- "answer": the request is a question; `text` answers it from what is listed above. No ops.

The editor reads `summary`, `text`, `note` and every design_effect `note`: there a shot is
"shot N (CLIP)" — its row in the sequence and its clip without the extension, "shot 4
(CLIP_02)" — never its id. Ids go only in the ops' id fields.
"""


# ---------------------------------------------------------------- the answer

def validate_answer(payload: Any) -> dict:
    """The model's JSON, shaped: a kind, a summary, and the field that kind needs. The
    ops themselves are checked by `actions.validate` (a bad op is refused, not re-asked)."""
    if not isinstance(payload, dict):
        raise ValueError("expected an object with `kind`")
    kind = str(payload.get("kind") or "").strip().lower()
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
    return out


def honest_summary(out: dict, text: str, flagged: bool) -> dict:
    """An edit that rests on the picture, from an AI that has only the descriptions, says so
    in its first line. The model is asked to; the editor's words and its own summary are
    checked here as well, because a model that writes "so the shot picks up just after the
    wobble" has not said it (WORDS-08, -17)."""
    summary = out.get("summary") or ""
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
        history: list[str] | None = None) -> dict:
    """One call. Returns `{kind, summary, ops?, lines?, refused?, preview?, families?,
    text?, note?, cost}`; raises `inference.InferenceError` / `ValueError` on a failed
    call (the server's job marks itself failed with the sentence)."""
    prompt = build_prompt(text, scope, state, clips, history)
    result = complete(prompt, role=ROLE, schema=OUTPUT_SCHEMA, system=SYSTEM,
                      validate=validate_answer, retries=1)
    got = dict(result.content)
    cost = {**cost_words(result.projected_usd), "projected_usd": round(result.projected_usd, 4),
            "measured": True, "model": result.model,
            "input_tokens": result.input_tokens, "output_tokens": result.output_tokens}
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
        out = honest_summary(out, text, bool(got.get("from_picture")))
    # the editor's words name shots as the editor does, whatever the model wrote
    return _named(out, state.get("segments") or [])
