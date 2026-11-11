"""The prompt box looks before it cuts on what the picture shows (wave 24, lane EYES).

The assist (roughcut/assist.py) reads the written descriptions and the transcript; it has
never seen a frame. Findings WORDS-08, -12, -13 and -17 are all one failure: asked about
a wobble, a jump, or where the camera points, it guessed from words, or handed the
looking back to the editor. This module is the looking.

Two steps, decided by the model and bounded by the server:

  1. The model's first answer may be `kind: "look"` — "I need to see these ranges" (shots,
     or film times, or clip times, each with a reason and a density), or `scan` for a
     question about the whole cut ("every bit where the camera points at the ground").
  2. `plan()` turns that request into the frames to cut, under a cap the server owns
     (`LOOK_CAP` frames for ranges, `SCAN_CAP` for a scan; the density drops to fit, and
     the plan says so). `build_sheets()` cuts them from the proxies (the same cached
     frame cutter the deep look uses) and lays them out as labelled contact sheets. The
     server shows the editor the plan, with its frame count and its price, and a Stop;
     then calls the model once more, with the images and the same conversation.

One look round per ask: the second answer may not ask to look again. Nothing here writes
the cut; a look that is stopped leaves only frames on disk in the bin's own work folder.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from . import actions, config, deep
from .firstpass import clip_name, cost_words

# --- the bounds (the server's, not the model's) ------------------------------------------
LOOK_CAP = 45          # frames for a look at named ranges (five sheets)
SCAN_CAP = 90          # frames for a scan of the whole cut (ten sheets)
SCAN_FPS = 1.0         # about a frame a second, thinner if the cut is longer than SCAN_CAP s
MAX_ITEMS = 8          # ranges one look may name
MAX_SPAN_S = 30.0      # longest single range
MIN_FPS = 0.5          # a range is never thinned below a frame every two seconds
DENSITY = {"fine": 4.0, "medium": 2.0, "coarse": 1.0}     # frames per second
DEFAULT_DENSITY = "medium"

COLS, ROWS = 3, 3                      # nine frames a sheet
CELL_W = 512                           # three across = 1,536 px, inside what the model reads whole
SHEET_TOKENS = 1_750                   # ≈ one 1,536 × ~870 px sheet read by the model
EXTRACT_WORKERS = 4


class Stopped(Exception):
    """The editor pressed Stop while the frames were being cut: nothing is sent."""


# ------------------------------------------------------------------ words

def tlabel(t: float) -> str:
    """A film time as the editor reads it on the timeline: 0:40.6."""
    t = max(0.0, float(t))
    m, s = divmod(t, 60.0)
    return f"{int(m)}:{s:04.1f}"


def span_words(a: float, b: float) -> str:
    return tlabel(a) if abs(b - a) < 0.05 else f"{tlabel(a)}–{tlabel(b)}"


def shots_words(numbers: list[int]) -> str:
    """[9, 10, 11, 14] → "shots 9-11, 14"."""
    ns = sorted(set(n for n in numbers if n))
    if not ns:
        return ""
    runs, start, prev = [], ns[0], ns[0]
    for n in ns[1:]:
        if n == prev + 1:
            prev = n
            continue
        runs.append((start, prev))
        start = prev = n
    runs.append((start, prev))
    parts = [f"{a}" if a == b else f"{a}-{b}" for a, b in runs]
    return ("shot " if len(ns) == 1 else "shots ") + ", ".join(parts)


# ------------------------------------------------------------------ the request

def clean_request(payload: dict) -> dict:
    """The model's look request, shaped: `{scan?, looks: [{shot? | film_in, film_out |
    clip, in, out; reason, density}]}`. Raises ValueError (so the call is re-asked once)
    when there is nothing to look at."""
    scan = payload.get("scan")
    scan = bool(scan) if not isinstance(scan, dict) else True
    looks: list[dict] = []
    for raw in (payload.get("looks") or [])[:MAX_ITEMS]:
        if not isinstance(raw, dict):
            continue
        item: dict[str, Any] = {"reason": str(raw.get("reason") or "")[:200]}
        dens = str(raw.get("density") or DEFAULT_DENSITY).lower()
        item["density"] = dens if dens in DENSITY else DEFAULT_DENSITY

        def num(k: str) -> float | None:
            v = raw.get(k)
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None
        shots = raw.get("shots") if isinstance(raw.get("shots"), list) else [raw.get("shot")]
        shots = [int(s) for s in shots if isinstance(s, (int, float)) and not isinstance(s, bool) and s >= 1]
        if shots:
            item["shots"] = shots[:60]
        elif raw.get("clip"):
            a, b = num("in"), num("out")
            if a is None or b is None:
                continue
            item.update(clip=str(raw["clip"])[:200], **{"in": a, "out": b})
        else:
            a, b = num("film_in"), num("film_out")
            if a is None:
                continue
            item.update(film_in=a, film_out=b if b is not None else a)
        looks.append(item)
    if not scan and not looks:
        raise ValueError("a look needs `looks` (shots, film times or clip times) or `scan`")
    return {"scan": scan, "looks": [] if scan else looks}


# ------------------------------------------------------------------ the plan

def _shots_table(state: dict) -> list[dict]:
    """Each shot with its film start, its length and the clip seconds it plays."""
    rows, t = [], 0.0
    for i, s in enumerate(state.get("segments") or []):
        d = actions.dur(s)
        rows.append({"i": i, "n": i + 1, "id": s.get("id"), "clip": s.get("clip"),
                     "start": t, "dur": d, "in": float(s["in"]), "out": float(s["out"]),
                     "speed": actions.speed_of(s) or 1.0})
        t += d
    return rows


def _film_frame(rows: list[dict], t: float) -> dict | None:
    for r in rows:
        if r["start"] <= t < r["start"] + r["dur"] or (r is rows[-1] and t <= r["start"] + r["dur"]):
            x = min(max(t - r["start"], 0.0), r["dur"])
            ct = min(max(r["in"] + x * r["speed"], r["in"]), max(r["in"], r["out"] - 0.04))
            return {"film_t": round(t, 2), "shot": r["n"], "clip": r["clip"], "clip_t": round(ct, 2)}
    return None


def _times(a: float, b: float, fps: float) -> list[float]:
    span = max(0.0, b - a)
    n = max(1, round(span * fps))
    if span < 0.05:
        return [a]
    return [a + k * span / n for k in range(n + 1)] if n > 1 else [a + span / 2]


def _item_frames(item: dict, rows: list[dict], total: float, scale: float) -> list[dict]:
    fps = max(MIN_FPS * 0.5, DENSITY[item.get("density", DEFAULT_DENSITY)] * scale)
    out: list[dict] = []
    if "clip" in item:
        a = min(item["in"], item["out"])
        b = min(max(item["in"], item["out"]), a + MAX_SPAN_S)
        for ct in _times(a, b, fps):
            out.append({"film_t": None, "shot": None, "clip": item["clip"], "clip_t": round(ct, 2)})
        return out
    if "shots" in item:
        ranges = []
        for n in item["shots"]:
            if 1 <= n <= len(rows):
                r = rows[n - 1]
                ranges.append((r["start"], r["start"] + r["dur"] - 0.04))   # the last frame is still in this shot
    else:
        a, b = sorted((item["film_in"], item["film_out"]))
        ranges = [(a, min(b, a + MAX_SPAN_S))]
    for a, b in ranges:
        a, b = max(0.0, a), min(total, b)
        if b < a:
            continue
        b = min(b, a + MAX_SPAN_S)
        for t in _times(a, b, fps):
            f = _film_frame(rows, min(t, max(total - 0.04, 0.0)))
            if f:
                out.append(f)
    return out


def _dedupe(frames: list[dict]) -> list[dict]:
    seen, out = set(), []
    for f in frames:
        key = (str(f["clip"]), round(f["clip_t"], 1)) if f["film_t"] is None else ("film", round(f["film_t"], 1))
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def plan(state: dict, request: dict, *, look_cap: int = LOOK_CAP, scan_cap: int = SCAN_CAP) -> dict:
    """The frames a look request would cut, bounded: `{frames, ranges, capped, cap, scan,
    fps, words}`. `frames` are in film order (clip-time looks last), each `{film_t, shot,
    clip, clip_t}`. The model's density is honoured until the cap bites; then every range
    is thinned together (never below MIN_FPS) and then the last ranges are cut, and
    `capped` says so."""
    rows = _shots_table(state)
    total = sum(r["dur"] for r in rows)
    if request.get("scan"):
        return _scan_plan(rows, total, scan_cap)
    items = request.get("looks") or []
    cap = int(look_cap)
    scale, frames = 1.0, []
    for _ in range(14):
        per = [_dedupe(_item_frames(it, rows, total, scale)) for it in items]
        frames = _dedupe([f for chunk in per for f in chunk])
        if len(frames) <= cap:
            break
        scale *= 0.8
    capped = scale < 0.999
    if len(frames) > cap:
        frames, capped = frames[:cap], True
    ranges = []
    for it in items:
        mine = _dedupe(_item_frames(it, rows, total, scale))
        mine = [f for f in mine if f in frames]
        if not mine:
            continue
        shots = [f["shot"] for f in mine if f["shot"]]
        ft = [f["film_t"] for f in mine if f["film_t"] is not None]
        ranges.append({"reason": it.get("reason") or "", "density": it.get("density", DEFAULT_DENSITY), "frames": len(mine),
                       "shots": sorted(set(shots)), "film_in": min(ft) if ft else None,
                       "film_out": max(ft) if ft else None,
                       "clip": it.get("clip")})
    frames.sort(key=lambda f: (f["film_t"] is None, f["film_t"] or 0.0, str(f["clip"]), f["clip_t"]))
    return _finish(frames, ranges, cap, False, capped, None)


def _scan_plan(rows: list[dict], total: float, cap: int) -> dict:
    """Every shot at about a frame a second (a frame at the middle of a shot shorter than
    that), thinned to the cap; a cut with more shots than the cap is scanned from the start."""
    fps = min(SCAN_FPS, cap / total) if total > 0 else SCAN_FPS
    frames: list[dict] = []
    for _ in range(30):
        frames = []
        for r in rows:
            k = max(1, round(r["dur"] * fps))
            for j in range(k):
                f = _film_frame(rows, r["start"] + (j + 0.5) * r["dur"] / k)
                if f:
                    frames.append(f)
        if len(frames) <= cap:
            break
        fps *= 0.85
    capped = len(frames) > cap or fps < SCAN_FPS - 1e-6
    if len(frames) > cap:
        frames = frames[:cap]
    shots = sorted({f["shot"] for f in frames})
    ranges = [{"reason": "the whole cut", "density": "scan", "frames": len(frames), "shots": shots,
               "film_in": frames[0]["film_t"] if frames else None,
               "film_out": frames[-1]["film_t"] if frames else None, "clip": None}]
    return _finish(frames, ranges, cap, True, capped, fps)


def _finish(frames: list[dict], ranges: list[dict], cap: int, scan: bool, capped: bool,
            fps: float | None) -> dict:
    sheets = math.ceil(len(frames) / (COLS * ROWS)) if frames else 0
    out = {"frames": frames, "ranges": ranges, "capped": capped, "cap": cap, "scan": scan,
           "fps": round(fps, 2) if fps else None, "sheets": sheets}
    out["words"] = words_of(out)
    return out


def where_of(p: dict) -> str:
    """Where the frames are from, for the editor: "shots 9-11 (0:38.0–0:43.0)", or for a
    scan "every shot in the cut, about a frame every 2 s"."""
    if p["scan"]:
        fps = p.get("fps") or SCAN_FPS
        every = "a frame a second" if fps >= 0.95 else f"a frame every {1 / fps:.0f} s"
        return f"every shot in the cut, about {every}"
    bits = []
    for r in p["ranges"]:
        if r.get("clip") and r.get("film_in") is None:
            bits.append(f"{clip_name(r['clip'])}")
        else:
            w = shots_words(r["shots"])
            t = span_words(r["film_in"], r["film_out"]) if r.get("film_in") is not None else ""
            bits.append(" ".join(x for x in (w, f"({t})" if t else "") if x))
    return "; ".join(bits[:4]) + (f" and {len(bits) - 4} more" if len(bits) > 4 else "")


def words_of(p: dict) -> str:
    """"shots 9-11 (0:38.0–0:43.0): 24 frames", with the cap said when it bit."""
    n = len(p["frames"])
    if not n:
        return "nothing in the cut to look at"
    cap = f" (the most I will look at in one ask is {p['cap']})" if p["capped"] else ""
    return f"{where_of(p)}: {n} frame{'' if n == 1 else 's'}{cap}"


def price(plan_: dict, prompt_chars: int = 0) -> dict:
    """The second call priced before it runs: its prompt and one image per sheet."""
    role = config.ROLE_SKELETON
    model = config.model_for(role)
    tin = prompt_chars // 4 + plan_["sheets"] * SHEET_TOKENS + 500
    usd = config.projected_usd(model, tin, 1_200)
    return {**cost_words(usd), "projected_usd": round(usd, 4), "model": model, "measured": False,
            "basis": f"~{tin:,} tokens in ({plan_['sheets']} sheets) and ~1,200 out, priced for {model}"}


# ------------------------------------------------------------------ the sheets

def build_sheets(plan_: dict, proxy_of: Callable[[str], Path], frames_dir: Path, out_dir: Path, *,
                 stop: Callable[[], bool] | None = None,
                 on_progress: Callable[[int, int], None] | None = None) -> list[dict]:
    """Cut the plan's frames from the proxies and lay them out as labelled contact sheets
    of COLS × ROWS cells, in film order. Returns `[{file, cells: [{label, film_t, shot,
    clip, clip_t}]}]`. Raises Stopped between frames when `stop()` is true, and
    RuntimeError when a frame cannot be cut (the model is never handed a hole)."""
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    frames = plan_["frames"]
    stop = stop or (lambda: False)
    done = [0]

    def cut(f: dict) -> Path:
        if stop():
            raise Stopped()
        clip = str(f["clip"])
        p = deep.extract_frames(proxy_of(clip), [f["clip_t"]], frames_dir / Path(clip).stem, width=CELL_W)[0]
        done[0] += 1
        if on_progress:
            on_progress(done[0], len(frames))
        return p
    with ThreadPoolExecutor(EXTRACT_WORKERS) as ex:
        paths = list(ex.map(cut, frames))
    if stop():
        raise Stopped()
    try:
        font = ImageFont.load_default(size=20)
    except TypeError:                                       # old Pillow: one fixed size
        font = ImageFont.load_default()
    out_dir.mkdir(parents=True, exist_ok=True)
    per = COLS * ROWS
    sheets: list[dict] = []
    for s0 in range(0, len(frames), per):
        chunk = list(zip(frames[s0:s0 + per], paths[s0:s0 + per]))
        ims = [Image.open(p).convert("RGB") for _, p in chunk]
        ch = CELL_W * 9 // 16          # every cell 16:9: a sideways (portrait) frame is fitted in, never stretches the sheet
        sheet = Image.new("RGB", (COLS * CELL_W, ((len(chunk) + COLS - 1) // COLS) * ch), (0, 0, 0))
        d = ImageDraw.Draw(sheet)
        cells = []
        for k, ((f, _), im) in enumerate(zip(chunk, ims)):
            x, y = (k % COLS) * CELL_W, (k // COLS) * ch
            sheet.paste(ImageOps.pad(im, (CELL_W, ch), color=(0, 0, 0)), (x, y))
            label = cell_label(f)
            w = 12 + 11 * len(label)
            d.rectangle([x, y, x + w, y + 26], fill=(0, 0, 0))
            d.text((x + 6, y + 2), label, fill=(255, 255, 0), font=font)
            d.rectangle([x, y, x + CELL_W - 1, y + ch - 1], outline=(60, 60, 60))
            cells.append({"label": label, **f})
        file = out_dir / f"sheet{len(sheets) + 1}.jpg"
        sheet.save(file, "JPEG", quality=85)
        sheets.append({"file": str(file), "cells": cells})
    return sheets


def cell_label(f: dict) -> str:
    if f.get("film_t") is None:
        return f"{clip_name(f['clip'])} {f['clip_t']:.1f}s"
    return f"{tlabel(f['film_t'])} s{f['shot']}"


def sheets_block(plan_: dict, sheets: list[dict]) -> str:
    """The prompt's account of the attached images: which file is which, and what each
    cell is. The label printed on a cell is its film time (m:ss.s) and its shot."""
    lines = []
    for i, s in enumerate(sheets):
        cells = ", ".join(c["label"] for c in s["cells"])
        lines.append(f"  Sheet {i + 1} ({Path(s['file']).name}), {COLS} cells across, left to right then "
                     f"down: {cells}")
    reasons = [f"  - {r['reason']}" for r in plan_["ranges"] if r.get("reason") and r["reason"] != "the whole cut"]
    return "\n".join(lines + (["The reasons you gave for looking:"] + reasons if reasons else []))
