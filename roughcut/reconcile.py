"""The last step of the look: write a clip's description from everything the index knows (wave 25, FIND2).

The coarse look writes a clip's description from one contact sheet, before the close looks and without
the words. The clip's Found moments (which include the close looks) and its transcript then know more,
and the two disagreed: GX010492's description said a man "works at a chairlift terminal" while its own
Found line said he rides the chair; GX010488's gallon milk jug was "a small bag" in the description and
"the milk jug" in Found and Said. This is a text-only step per clip: no new frames, one small call, run
when a clip's close looks or transcript land after its description.

The result is stored beside the sidecar as `<stem>.reconciled.json` (never inside it, so a re-look cannot
erase it and the look's own text stays readable), versioned, and marked in the record. A description the
editor wrote is never touched or replaced; it is not even asked for.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from . import config, inference

VERSION = 1
SUFFIX = ".reconciled.json"
MAX_TRANSCRIPT_CHARS = 2400       # the words handed to the model: a clip of speech is clipped, never the verdict
MAX_MOMENTS = 14

SYSTEM = (
    "You write the log line for one clip of raw footage, for the editor who will search it and read it "
    "in a list. You are given the description a first look wrote from one contact sheet, the moments the "
    "index found in the clip (the close looks read it again, second by second, and are the more reliable "
    "where they disagree), and the words said in it. You write nothing the evidence does not say.")

SCHEMA = {"description": "two to four sentences: who is there, where, what happens (in order), what is said"}

PROMPT = """## The clip
{clip}, {duration}.

## The first look's description (written from one sheet of frames, before anything below)
{coarse}

## What the close looks and sensors found in it (time, kind, what)
{moments}

## What is said in it (time, words)
{said}

## Write the description again
Two to four plain sentences, in this order: who is there, where, what happens, what is said.
- Where the moments or the words disagree with the first description, believe the moments and the
  words, and drop the first description's claim ("works at a lift terminal" when the moments say he
  rides the chair; "a small bag" when the words and the moments say a milk jug).
- Keep what the first description has that nothing contradicts (clothes, places, signs, the camera).
- Name the objects and running jokes the words and moments name (a jug, a sign, a prop).
- A moment marked `unchecked` is a claim nobody has confirmed: say it with "looks like" or "appears to".
- Say what is said in a few words ("they joke about the milk"), do not copy the transcript.
- No verdicts ("nothing notable", "unusable", "not skiing", "no events"), no lengths of the clip,
  no "the first description". Do not mention that you were given a description.
- If there is no picture evidence at all (a black clip), say so in one clause and describe the words."""


def _clock(t: float) -> str:
    t = max(0.0, float(t))
    return f"{int(t // 60)}:{t % 60:04.1f}"


def moments_text(moments: list[dict]) -> str:
    rows = sorted((m for m in moments if m.get("what")), key=lambda m: m.get("start", 0))
    # the notable and the event rows first if the list has to be cut
    rows = sorted(rows, key=lambda m: (not (m.get("notable") or m.get("unchecked")), m.get("start", 0)))[:MAX_MOMENTS]
    rows.sort(key=lambda m: m.get("start", 0))
    out = []
    for m in rows:
        tag = " (unchecked)" if m.get("unchecked") else ""
        out.append(f"- {_clock(m.get('start', 0))} {m.get('kind') or m.get('source') or ''}{tag}: "
                   f"{str(m['what'])[:260]}")
    return "\n".join(out) or "(nothing found yet)"


def said_text(lines: list[dict]) -> str:
    out, used = [], 0
    for u in lines:
        t = str(u.get("text", "")).strip()
        if not t:
            continue
        row = f"- {_clock(u.get('start', 0))} {t}"
        if used + len(row) > MAX_TRANSCRIPT_CHARS:
            out.append("- (more lines not shown)")
            break
        out.append(row)
        used += len(row) + 1
    return "\n".join(out) or "(nobody says anything)"


def inputs_sig(coarse: str, moments: list[dict], transcript: list[dict]) -> str:
    """What the description was written from: change any of it and the description is stale."""
    blob = json.dumps([coarse, [(m.get("start"), m.get("kind"), m.get("what"), bool(m.get("unchecked"))) for m in moments],
                       [(u.get("start"), u.get("text")) for u in transcript]], sort_keys=True)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def has_new_evidence(moments: list[dict], transcript: list[dict]) -> bool:
    """A clip with no close look and no words has nothing the coarse description lacks."""
    return bool([m for m in moments if m.get("what") and m.get("source") != "felt"]) or bool(
        [u for u in transcript if str(u.get("text", "")).strip()])


def validate(payload, strip_verdicts) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("expected an object")
    text, _ = strip_verdicts(str(payload.get("description") or ""))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 20:
        raise ValueError("the description is empty or only verdicts")
    return {"description": text[:900]}


def reconcile(clip: str, duration: float, coarse: str, moments: list[dict], transcript: list[dict],
              strip_verdicts) -> dict:
    """One text-only call. -> {description, usage}. Raises inference.InferenceError / ValueError."""
    prompt = PROMPT.format(clip=Path(clip).stem, duration=f"{duration:.0f} s" if duration else "length unknown",
                           coarse=coarse.strip() or "(none)", moments=moments_text(moments), said=said_text(transcript))
    r = inference.complete(prompt, role=config.ROLE_ANALYSIS, schema=SCHEMA, system=SYSTEM,
                           validate=lambda p: validate(p, strip_verdicts), retries=1, isolated=True)
    out = dict(r.content)
    out["usage"] = {"input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
                    "projected_usd": r.projected_usd, "model": r.model}
    return out


def path_of(visual_dir: Path, stem: str) -> Path:
    return Path(visual_dir) / f"{stem}{SUFFIX}"


def load(visual_dir: Path, stem: str) -> dict | None:
    p = path_of(visual_dir, stem)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("description") and int(d.get("version") or 0) >= 1 else None


def store(visual_dir: Path, stem: str, clip: str, result: dict, sig: str, coarse: str) -> dict:
    rec = {"clip": clip, "version": VERSION, "description": result["description"], "coarse": coarse[:1200],
           "inputs": sig, "model": (result.get("usage") or {}).get("model"), "at": round(time.time(), 1),
           "note": "written from the coarse description, the close looks and the transcript; text only"}
    p = path_of(visual_dir, stem)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    tmp.replace(p)
    return rec
