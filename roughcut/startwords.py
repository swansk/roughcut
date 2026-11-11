"""Starting a film like a person (wave 24 START): the pure half.

Three small things a newcomer meets before the first cut, kept here so the tests can
check them without a server:

* the **target length**: "1 min" becomes the range the cut-writer is told (`target_for`),
  and a range is said back the way a person says it (`target_words`);
* the **time a first pass will take**, learned from this machine's earlier passes
  (`estimate`, `history_row`) with an honest first guess when there are none;
* the **plain words** for what a first pass does and how closely it looks (`plain`), the
  first layer of the popover. The models and the pipeline terms stay in the Details
  under it; nothing is dropped, it is moved.
"""

from __future__ import annotations

import json
from pathlib import Path

# 10 s .. 30 min: under that there is no film to cut, over it the prompt is not built for it
MIN_S, MAX_S = 10.0, 1800.0
# "1 min" is a range the writer aims inside: 10 % under to 10 % over
SPREAD = 0.10
DEFAULT_RANGE = (120.0, 180.0)           # what a new project is scaffolded with

# The first guess before this machine has a history: the Copper trial (26 clips, 31 min of
# footage) took about 3.5-4 min to hear and look at and about 1.5 min to rank, write and
# check the cut, with the GPU free.
FIRST_GUESS_PER_CLIP_S = 9.0
FIRST_GUESS_FIXED_S = 100.0


def target_for(seconds: float) -> list[int]:
    """The [low, high] seconds a cut aimed at about `seconds` is written to."""
    s = float(seconds)
    if not (MIN_S <= s <= MAX_S) or s != s:
        raise ValueError(f"a length between {int(MIN_S)} seconds and {int(MAX_S // 60)} "
                         "minutes is needed")
    return [int(round(s * (1 - SPREAD))), int(round(s * (1 + SPREAD)))]


def clean_range(raw) -> list[int]:
    """A [low, high] pair the editor sent, validated."""
    try:
        lo, hi = float(raw[0]), float(raw[1])
    except (TypeError, ValueError, IndexError, KeyError):
        raise ValueError("target is two numbers, low and high, in seconds") from None
    if not (MIN_S <= lo < hi <= MAX_S * 1.5):
        raise ValueError("target needs 10 seconds <= low < high")
    return [int(round(lo)), int(round(hi))]


def _clock(s: float) -> str:
    s = int(round(s))
    return f"{s // 60}:{s % 60:02d}"


def target_words(target, want_s: float | None = None) -> str:
    """"about 1 min", "about 30 s", "2–3 min", "1:45–2:15"."""
    try:
        lo, hi = float(target[0]), float(target[1])
    except (TypeError, ValueError, IndexError):
        lo, hi = DEFAULT_RANGE
    if want_s:
        w = float(want_s)
        if w < 90 and w % 60:
            return f"about {int(round(w))} s"
        if w % 60 == 0:
            return f"about {int(w // 60)} min"
        return f"about {_clock(w)}"
    if lo >= 60 and lo % 60 == 0 and hi % 60 == 0:
        return f"{int(lo // 60)}–{int(hi // 60)} min"
    return f"{_clock(lo)}–{_clock(hi)}"


# ------------------------------------------------------------------ how long it takes


def history_row(*, clips: int, indexed: int, index_s: float, total_s: float) -> dict:
    """What one finished pass adds to the machine's history: how many clips there were,
    how many of them it had to hear and look at, and the seconds each half took."""
    return {"clips": int(clips), "indexed": int(indexed), "index_s": round(float(index_s), 1),
            "rest_s": round(max(0.0, float(total_s) - float(index_s)), 1),
            "total_s": round(float(total_s), 1)}


def _median(xs: list[float]) -> float:
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def load_history(path: Path) -> list[dict]:
    rows = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for ln in lines[-200:]:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("total_s"):
            rows.append(r)
    return rows


def estimate(history: list[dict], to_index: int) -> dict:
    """How long a pass that must hear and look at `to_index` clips will take: the median
    seconds a clip took here times the clips to do, plus the median of the rest (picks,
    the cut, the check). With no history, the Copper trial's figures, said to be a guess."""
    per = [r["index_s"] / r["indexed"] for r in history
           if r.get("indexed") and r.get("index_s", 0) > 0]
    rest = [r["rest_s"] for r in history if r.get("rest_s", 0) > 0]
    learned = bool(per or rest)
    per_clip = _median(per) if per else FIRST_GUESS_PER_CLIP_S
    fixed = _median(rest) if rest else FIRST_GUESS_FIXED_S
    seconds = max(0, int(to_index)) * per_clip + fixed
    n = len(history)
    return {"seconds": round(seconds), "words": minutes_words(seconds),
            "learned": learned, "runs": n,
            "basis": (f"from {n} earlier first pass{'es' if n != 1 else ''} on this machine"
                      if learned else "a first guess; it learns from the passes run here")}


def minutes_words(seconds: float) -> str:
    s = float(seconds)
    if s < 45:
        return "under a minute"
    if s < 90:
        return "about a minute"
    m = int(round(s / 60))
    if m < 60:
        return f"about {m} minutes"
    h, mm = divmod(m, 60)
    return f"about {h} h {mm} min" if mm else f"about {h} h"


# ------------------------------------------------------------------ plain words


def plain(*, clips: int, minutes: float | None, to_index: int, look_s: float,
          target, want_s: float | None, will_replace: int = 0) -> dict:
    """What a first pass does and how closely it looks, in a newcomer's words. Always says
    the three things Karl's K5 asks for: what it does, how closely it looks (a frame every
    N seconds, a closer look at the busiest moments) and that nothing changes until the
    editor accepts. The model names and the pipeline terms are in the Details."""
    n = int(clips)
    clipw = f"{n} clip{'s' if n != 1 else ''}"
    span = f", {minutes:.0f} min of footage" if minutes and minutes >= 1 else ""
    if to_index > 0:
        listen = (f"Listens to every clip and writes down what is said "
                  f"({to_index} of {clipw} still to do{span}).") if to_index < n else \
            f"Listens to every clip and writes down what is said ({clipw}{span})."
        look = (f"Looks at a frame every {look_s:g} seconds, then takes a closer look at the "
                "busiest moments (falls, jumps, anything that moves a lot).")
    else:
        listen = f"Every clip is already heard and looked at ({clipw}{span})."
        look = "It uses what it already found; nothing is watched again."
    cut = (f"Picks the best moments and proposes a rough cut {target_words(target, want_s)} "
           "long.")
    safe = ("Nothing in your timeline changes until you accept it."
            if not will_replace else
            "Nothing changes until you accept it; your own edits can be kept.")
    return {"lines": [listen, look, cut], "safe": safe,
            "look_s": look_s, "aims": target_words(target, want_s)}
