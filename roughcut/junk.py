"""Junk: proposed by a measurement, confirmed by the human (HANDOFF roadmap item 5).

The last ❌ in HANDOFF's session-3 table, and what stands between the app and a bin
nobody has studied: on Copper, 44% of the footage (290 s of 662 s) is a camera in a
pocket or a bag. Every one of those seconds costs a contact sheet in the index's priced
stages and a block in every Ask's inventory, and none of it can be cut.

So the bin gets a proposal, and it is a measurement, not a model call. The numbers
come from what the index already has: the per-clip colour file (`colour.measure_clip`,
one sample every 5 s from the proxy), the audio sidecar's words, the clip's duration.
Three rules, each conservative on purpose (HANDOFF: *"Be conservative; when unsure,
keep"*), and each one only when no speech contradicts it — a pocket recording with
people talking is audio someone may want, whatever the picture is:

  * **black** — the mean luma is below the night-content floor and no sample is lit;
  * **flat** — one featureless field in every sample (a lens cap, the inside of a bag);
  * **short** — too short to cut a shot from.

A proposal is never a verdict. The human confirms (`junk`) or overrides (`keep`); the
EDL records only what the human said, keyed by clip, and the proposals are recomputed
from the files whenever they are asked for, so they cost nothing and are never stale.
A confirmed clip leaves the Ask's inventory and Find, hides behind a chip in the bin,
and has its priced index stages skipped — that last one is the money.

**Every threshold here is provisional** (CLAUDE.md, research-before-assumption): they
are chosen from one bin's measurements, B1/Copper's `benchmarks/labels/B1-luma.json`,
re-measured from the app's own colour files (the two agree within 2.4/255 on every
clip darker than 35, the only range the black rule reads), and
no study has yet run them over a second raw bin. Killington is pre-curated and has no
junk to test them on.

Pure module: no I/O, no ffmpeg — the server hands in the dicts it has already read.
"""
from __future__ import annotations

import time

VERSION = 1

# Luma on the 0–255 scale, the scale B1-luma.json was measured in. Rec.709 weights on
# the sampled frames' mean RGB — the same `colour.LUMA` the colour files are built with.
LUMA_WEIGHTS = (0.2126, 0.7152, 0.0722)

# Black: the clip's mean luma below this. B1's nine junk clips measure 1.1–10.9
# (GX010484 is the brightest, 10.9 in B1-luma, 8.5 from its colour file); the darkest
# real content is GX010477 at 16.8 (16.0), then GX010486 23.6 and GX010478 29.6 — the
# night parking lot and the dim plane interior. HANDOFF's confident band was `luma<11`,
# which sits 0.1 above GX010484 in one measurement; 12 is the same band with that
# margin, still 4 below the darkest real clip. A naive `luma<35` takes all three dim
# real clips with it — the false positive this rule exists to avoid.
LUMA_BLACK = 12.0

# ...and no single sample brighter than this. A clip dark on average with one lit
# sample has something in it (a pocket opened on a view) and is kept. The brightest
# sample of any B1 junk clip is 13.6 (GX010482); 40 sits well clear of it, so only a
# sample at a properly lit level saves a clip that is black on average.
LUMA_LIT = 40.0

# Flat: the median sample's spread between its 0.5th and 99.5th luma percentile below
# this. Real content on B1 never spreads less than 143/255 (GX010478, the plane); the
# four darkest junk clips spread 2.8–8.6. Catches a featureless frame at any
# brightness — a white bag, a lens cap — that the black rule cannot.
FLAT_SPREAD = 10.0

# Short: under this many seconds there is no shot to cut — a cut needs a beat before
# and after the line (the floor pads 0.25 s ahead and 0.45 s behind). B1's shortest
# real clip is 5.1 s (GX010491); nothing in either bin is under it, so this rule is
# unmeasured on real footage — it catches the half-second accidental press.
SHORT_S = 1.5

# Speech contradicts every rule: one transcribed word and nothing is proposed. Whisper
# does hallucinate a "Thank you." over silence, and this keeps those clips too — the
# conservative direction, and the human can still mark one junk by hand.
SPEECH_MIN_WORDS = 1

# What was considered and not built: a *frozen* rule (the same frame in every sample).
# At one sample per 5 s a tripod shot is indistinguishable from a frozen frame —
# GX010487's three samples differ by 0.9/255 in mean, and it is real content.

VERDICTS = ("junk", "keep")
STATES = ("proposed", "confirmed", "kept", "clean")
REASON_MAX = 200


def _luma(rgb) -> float:
    return sum(float(c) * w for c, w in zip(rgb, LUMA_WEIGHTS)) * 255.0


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def words_of(sidecar: dict | None) -> int | None:
    """Words heard in the clip, from its audio sidecar; None when nobody has listened."""
    if not sidecar:
        return None
    n = (sidecar.get("summary") or {}).get("n_words")
    if n is not None:
        try:
            return int(n)
        except (TypeError, ValueError):
            pass
    return sum(len(u.get("words") or str(u.get("text", "")).split())
               for u in sidecar.get("transcript") or [])


def facts(colour: dict | None, sidecar: dict | None, duration: float | None) -> dict:
    """The numbers `assess` reads, from a colour file (`colour.measure_clip`'s shape),
    an audio sidecar and the clip's duration. Any of the three may be missing; a
    missing measurement is None, never zero — zero luma would read as black."""
    samples = [s for s in ((colour or {}).get("samples") or [])
               if isinstance(s, dict) and s.get("mean_rgb")]
    out: dict = {"duration_s": None if duration is None else round(float(duration), 2),
                 "words": words_of(sidecar), "samples": len(samples),
                 "luma_mean": None, "luma_peak": None, "spread": None}
    if samples:
        lumas = [_luma(s["mean_rgb"]) for s in samples]
        out["luma_mean"] = round(sum(lumas) / len(lumas), 1)
        out["luma_peak"] = round(max(lumas), 1)
        spreads = [(float(s["y_hi"]) - float(s["y_lo"])) * 255.0 for s in samples
                   if s.get("y_hi") is not None and s.get("y_lo") is not None]
        if spreads:
            out["spread"] = round(_median(spreads), 1)
    return out


def assess(f: dict) -> dict:
    """`{"junk", "reasons", "numbers"}` for one clip's facts. `junk` is the machine's
    proposal only; `reasons` are sentences a human can check against the picture;
    `numbers` are the facts and the thresholds they were held against."""
    words = f.get("words")
    numbers = {**{k: f.get(k) for k in ("duration_s", "words", "samples", "luma_mean",
                                        "luma_peak", "spread")},
               "thresholds": {"luma_black": LUMA_BLACK, "luma_lit": LUMA_LIT,
                              "flat_spread": FLAT_SPREAD, "short_s": SHORT_S}}
    if words is not None and words >= SPEECH_MIN_WORDS:
        return {"junk": False, "numbers": numbers,
                "reasons": [f"{words} word{'s' if words != 1 else ''} heard — "
                            "speech keeps a clip, whatever its picture"]}
    reasons: list[str] = []
    mean, peak, spread = f.get("luma_mean"), f.get("luma_peak"), f.get("spread")
    if mean is not None and mean < LUMA_BLACK and (peak is None or peak < LUMA_LIT):
        lit = f", brightest sample {peak:.1f}" if peak is not None else ""
        reasons.append(f"essentially black — mean luma {mean:.1f}/255{lit} (black "
                       f"below {LUMA_BLACK:g}; the darkest real night footage "
                       "measured 16.8)")
    if spread is not None and spread < FLAT_SPREAD:
        reasons.append(f"one flat field — the frame spans {spread:.1f}/255 of luma "
                       f"(flat below {FLAT_SPREAD:g}; real footage spans 140+)")
    dur = f.get("duration_s")
    if dur is not None and dur < SHORT_S:
        reasons.append(f"too short to cut — {dur:.2f} s (under {SHORT_S:g} s)")
    fired = bool(reasons)
    if fired:
        reasons.append("no words heard" if words is not None
                       else "not listened to yet — speech would keep it")
    return {"junk": fired, "reasons": reasons, "numbers": numbers}


def status(proposed: bool, verdict: str | None) -> str:
    """One word for the board: the human's verdict wins, the machine only proposes."""
    if verdict == "junk":
        return "confirmed"
    if verdict == "keep":
        return "kept" if proposed else "clean"
    return "proposed" if proposed else "clean"


# ------------------------------------------------------------------ the EDL block
#
#   "junk": {"<clip>": {"verdict": "junk" | "keep", "at": <epoch>,
#                       "reasons": ["<the proposal's reasons when the human answered>"]}}
#
# Only the human's word is stored. A proposal is a function of files on disk and is
# recomputed on every read; storing it would make it something to keep in step.

def validate(block) -> dict:
    """The `junk` block a save may write. Raises ValueError with the sentence."""
    if block is None:
        return {}
    if not isinstance(block, dict):
        raise ValueError("junk must be an object keyed by clip")
    out: dict = {}
    for clip, rec in block.items():
        if not isinstance(clip, str) or not clip or len(clip) > 200:
            raise ValueError(f"junk: {clip!r} is not a clip name")
        if not isinstance(rec, dict):
            raise ValueError(f"junk: {clip}'s record is not an object")
        verdict = rec.get("verdict")
        if verdict not in VERDICTS:
            raise ValueError(f"junk: {clip}'s verdict must be one of {VERDICTS}, "
                             f"not {verdict!r}")
        clean: dict = {"verdict": verdict}
        at = rec.get("at")
        if at is not None:
            try:
                clean["at"] = float(at)
            except (TypeError, ValueError):
                raise ValueError(f"junk: {clip}'s 'at' is not a time") from None
        reasons = rec.get("reasons")
        if reasons is not None:
            if not isinstance(reasons, list) or not all(isinstance(r, str) for r in reasons):
                raise ValueError(f"junk: {clip}'s reasons are not a list of sentences")
            clean["reasons"] = [r[:REASON_MAX] for r in reasons[:8]]
        out[clip] = clean
    return out


def set_verdict(block: dict | None, clip: str, verdict: str | None,
                reasons: list[str] | None = None, *, now: float | None = None) -> dict:
    """The block with one clip's verdict set (`junk` / `keep`) or cleared (None)."""
    out = dict(block or {})
    if verdict is None:
        out.pop(clip, None)
        return out
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS} or null, not {verdict!r}")
    rec: dict = {"verdict": verdict, "at": round(time.time() if now is None else now, 3)}
    if reasons:
        rec["reasons"] = [str(r)[:REASON_MAX] for r in reasons[:8]]
    out[clip] = rec
    return out


def confirmed(block: dict | None) -> set[str]:
    """The clips the human has confirmed as junk — cheap, no measurement."""
    return {c for c, r in (block or {}).items()
            if isinstance(r, dict) and r.get("verdict") == "junk"}
