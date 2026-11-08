"""What the camera felt, as timed events (wave 20, lane TELEM; R11 rules, R12 bake-off).

The bake-off decided it: GoPro telemetry finds the wearer's events (0.78 recall on the
Karl-checked Killington set, 0.86 on airs, $0) where the sampled-frame index found 0.33.
So telemetry decides *when*; the checker (roughcut/verify.py, lane VERIFY) and the local
model (roughcut/localvlm, lane LOCALGPU) settle *what*. This module is the one place the
rules live. It reads the GPMF dump the telemetry stage already wrote (or extracts it),
applies the rules and writes a per-clip sidecar `<STEM>.felt.json` next to the telemetry
summary. Pure Python, no model call.

## The function VERIFY and LOCALGPU import

    from roughcut.telemetry_events import telemetry_events
    telemetry_events(clip) -> list[dict]        # [] when there is no sidecar / no telemetry

`clip` is the clip name as the bin knows it ("CLIP_12.MP4" or "CLIP_12"). The sidecar
directory is the telemetry directory; the server registers it at configure
(`use_telemetry_dir`), or pass `directory=` explicitly. The list is sorted by `start`.
Every event is a plain dict and always carries:

    kind     "air" | "impact" | "speed_drop" | "stopped" | "lift" | "riding"
    who      "wearer"   telemetry only sees the wearer; other riders' airs come from the frames
    start, end   seconds in the clip (an air: takeoff to landing; an impact: the 0.1 s bin)
    t        the moment to look at (air: mid-flight; impact/speed_drop: the event; stopped: start)
    source   "telemetry"
    status   "felt"      a candidate: numbers only, not yet seen. VERIFY upgrades or demotes it
    check    True when a checker should look (every air; every impact; speed_drop with an impact)
    text     one compact human line, e.g. "air 0.39 s at 46.62-47.01 (landing 4.0 g)"

and, by kind:

    air         air_s, min_g, landing_g, t_takeoff, t_land
                |a| < 0.5 g for >= 0.25 s, then a landing > 2.5 g within 0.6 s (a dip without a
                landing is a dropped camera or a bump, not an air, and is not emitted)
    impact      peak_g (> 5 g), handling (True = "often handling": no speed change around it;
                a hand on the camera, not a fall)
    speed_drop  from_ms, to_ms (>= 3 m/s lost within 3 s)
    stopped     from_ms (>= 4 m/s down to <= 1 m/s) ; the stretch is start..end of standing
    lift        a sustained slow steady ride (GPS 1.5-6 m/s for >= 40 s) ; "lift?" is a guess
    riding      mean_ms, max_ms over a stretch moving >= 3 m/s for >= 10 s

Also in the sidecar and exposed here: `speed_series(clip)` -> [[t, m/s], ...] about every
1 s, and `roll_at(clip, times)` -> degrees of camera roll (GRAV, + upright mount, - inverted
mount, the camera's own auto-rotation honoured; 0 where gravity is near the optical axis)
for levelling frames. `mount(clip)` -> "upright" | "inverted".

`felt_line(clip)` -> the compact planner line(s) (roughcut/revise.py puts them in each clip's
block); `felt_words(clip)` -> the same words for Find's lexical match.
"""

from __future__ import annotations

import bisect
import importlib.util
import json
import math
import os
import statistics
import sys
from pathlib import Path
from typing import Callable

SCHEMA = 2                      # bump when the rules change: the cache is keyed on it

G = 9.80665
AIR_G = 0.5                     # |a| below this ...
AIR_MIN_S = 0.25                # ... for at least this long ...
AIR_LAND_G = 2.5                # ... then a landing above this within AIR_LAND_WITHIN_S
AIR_LAND_WITHIN_S = 0.6
IMPACT_G = 5.0
IMPACT_SEP_S = 0.5              # peaks within this are one landing (10 Hz max bins, local maxima)
DROP_MS, DROP_WITHIN_S = 3.0, 3.0
STOP_FROM_MS, STOP_TO_MS, STOP_SEP_S = 4.0, 1.0, 5.0
STILL_MS, STILL_MIN_S = 1.0, 10.0       # "stopped": the stretch standing still
RIDE_MS, RIDE_MIN_S, RIDE_GAP_S = 3.0, 10.0, 2.0
LIFT_CLIMB_M, LIFT_MIN_S = 20.0, 30.0   # "lift": moving up the mountain, GPS altitude rising
HANDLING_DROP_MS = 1.0          # an impact with less speed change than this around it is hands
GPS_MIN_FIX, GPS_MAX_DOP100 = 2, 500
SPEED_STEP_S = 1.0
GRAV_GXY_MIN = 0.35             # below this gravity is along the optical axis: roll is ill-defined

_DIR: Callable[[], Path] | Path | str | None = None
_TOOL = None


def use_telemetry_dir(provider: Callable[[], Path] | Path | str | None) -> None:
    """The server's registration: where `<STEM>.felt.json` lives (a path or a callable)."""
    global _DIR
    _DIR = provider


def _dir(directory) -> Path | None:
    d = directory if directory is not None else _DIR
    d = d() if callable(d) else d
    return Path(d) if d else None


def _tool():
    """research/tools/telemetry.py (the GPMF parser), loaded by path: it is a `uv run`
    script, not a package module, and stays one so the research tools keep working."""
    global _TOOL
    if _TOOL is None:
        p = Path(__file__).resolve().parent.parent / "research" / "tools" / "telemetry.py"
        spec = importlib.util.spec_from_file_location("rc_gpmf_telemetry", p)
        m = importlib.util.module_from_spec(spec)
        sys.modules["rc_gpmf_telemetry"] = m
        spec.loader.exec_module(m)
        _TOOL = m
    return _TOOL


def _stem(clip: str) -> str:
    return Path(clip).stem.upper()


def _r(x: float, n: int = 2) -> float:
    return round(float(x), n)


# --------------------------------------------------------------------------- series

def series_from_streams(streams: dict) -> dict:
    """ACCL |a| in g, GRAV roll and mount, GPS speed/altitude from `collect_streams`."""
    out: dict = {"ta": [], "mag": [], "tg": [], "roll": [], "mount": "upright",
                 "tp": [], "sp": [], "alt": []}
    acc = streams.get("ACCL")
    if acc and acc.get("v") and isinstance(acc["v"][0], tuple):
        out["ta"] = list(acc["t"])
        out["mag"] = [math.sqrt(a * a + b * b + c * c) / G for a, b, c in acc["v"]]
    gr = streams.get("GRAV")
    if gr and gr.get("v") and isinstance(gr["v"][0], tuple):
        gv = gr["v"]            # (lateral, vertical, forward)
        # An inverted mount: the camera rotated the image itself, so gravity reads upside down.
        base = -1.0 if statistics.median(v[1] for v in gv) < 0 else 1.0
        out["mount"] = "inverted" if base < 0 else "upright"
        out["tg"] = list(gr["t"])
        roll = []
        for lat, vert, _fwd in gv:
            if math.hypot(lat, vert) < GRAV_GXY_MIN:
                roll.append(0.0)
            else:
                roll.append(math.degrees(math.atan2(lat * base, vert * base)))
        out["roll"] = roll
    gp = streams.get("GPS5") or streams.get("GPS9")
    if gp and gp.get("v") and isinstance(gp["v"][0], tuple):
        nine = "GPS5" not in streams
        for t, v, f, d in zip(gp["t"], gp["v"], gp["fix"], gp["dop"]):
            if nine:
                f, d = v[8], v[7] * 100
            if f is not None and f >= GPS_MIN_FIX and (d is None or d < GPS_MAX_DOP100):
                out["tp"].append(t)
                out["sp"].append(v[3])
                out["alt"].append(v[2])
    return out


def _interp(xs: list[float], ys: list[float], x: float) -> float:
    i = bisect.bisect_left(xs, x)
    if i <= 0:
        return ys[0]
    if i >= len(xs):
        return ys[-1]
    x0, x1 = xs[i - 1], xs[i]
    return ys[i - 1] if x1 == x0 else ys[i - 1] + (ys[i] - ys[i - 1]) * (x - x0) / (x1 - x0)


def _speed_grid(s: dict, step: float = 0.1) -> tuple[list[float], list[float]]:
    tp, sp = s["tp"], s["sp"]
    if len(tp) < 10:
        return [], []
    n = int(tp[-1] / step)
    grid = [i * step for i in range(n)]
    return grid, [_interp(tp, sp, g) for g in grid]


# --------------------------------------------------------------------------- the rules

def find_airs(ta: list[float], mag: list[float]) -> list[dict]:
    """|a| < AIR_G for >= AIR_MIN_S, then a landing > AIR_LAND_G within AIR_LAND_WITHIN_S."""
    out, start, lo = [], None, 9.0
    n = len(ta)
    for i in range(n + 1):
        below = i < n and mag[i] < AIR_G
        if below:
            if start is None:
                start, lo = ta[i], mag[i]
            lo = min(lo, mag[i])
            continue
        if start is None:
            continue
        end = ta[i] if i < n else ta[-1]
        s0, lo0, start, lo = start, lo, None, 9.0
        if end - s0 < AIR_MIN_S:
            continue
        land = max((mag[j] for j in range(i, n) if ta[j] <= end + AIR_LAND_WITHIN_S), default=0.0) \
            if i < n else 0.0
        if land <= AIR_LAND_G:
            continue            # a dip with no landing is a dropped camera or a bump, not an air
        out.append({"kind": "air", "start": _r(s0), "end": _r(end), "t": _r((s0 + end) / 2),
                    "t_takeoff": _r(s0), "t_land": _r(end), "air_s": _r(end - s0),
                    "min_g": _r(lo0, 3), "landing_g": _r(land, 1)})
    return out


def find_impacts(ta: list[float], mag: list[float]) -> list[dict]:
    """Peaks > IMPACT_G on 10 Hz max bins, local maxima within IMPACT_SEP_S."""
    if not ta:
        return []
    nb = int(ta[-1] * 10) + 1
    mx = [0.0] * nb
    for t, m in zip(ta, mag):
        b = min(int(t * 10), nb - 1)
        if m > mx[b]:
            mx[b] = m
    out = []
    w = int(IMPACT_SEP_S * 10)
    for i in range(nb):
        if mx[i] > IMPACT_G and mx[i] == max(mx[max(0, i - w):i + w + 1]):
            out.append({"kind": "impact", "start": _r(i / 10), "end": _r(i / 10 + 0.1),
                        "t": _r(i / 10 + 0.05), "peak_g": _r(mx[i], 1)})
    return out


def _stretches(grid, v, pred, min_s, gap_s) -> list[dict]:
    runs, a, last = [], None, None
    for g, x in zip(grid, v):
        if pred(x):
            if a is None:
                a = g
            last = g
        elif a is not None and g - last > gap_s:
            runs.append((a, last))
            a = None
    if a is not None:
        runs.append((a, last))
    out = []
    for a, b in runs:
        if b - a < min_s:
            continue
        seg = [x for g, x in zip(grid, v) if a <= g <= b]
        out.append({"start": _r(a), "end": _r(b), "mean_ms": _r(sum(seg) / len(seg), 1),
                    "max_ms": _r(max(seg), 1)})
    return out


def find_speed_events(s: dict) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """(speed_drop, stops from a ride, still stretches, riding stretches) from GPS speed."""
    grid, v = _speed_grid(s)
    if not grid:
        return [], [], [], []
    step = 0.1
    nwin = int(DROP_WITHIN_S / step)
    drops, stops = [], []
    last_drop = last_stop = -99.0
    for i in range(len(grid) - nwin):
        w = v[i + 1:i + 1 + nwin]
        lo = min(w)
        if v[i] - lo >= DROP_MS and grid[i] - last_drop > DROP_WITHIN_S:
            j = i + 1 + w.index(lo)
            drops.append({"kind": "speed_drop", "start": _r(grid[i]), "end": _r(grid[j]),
                          "t": _r(grid[j]), "from_ms": _r(v[i], 1), "to_ms": _r(lo, 1)})
            last_drop = grid[i]
        if v[i] >= STOP_FROM_MS and lo <= STOP_TO_MS and grid[i] - last_stop > STOP_SEP_S:
            j = i + 1 + next(k for k, x in enumerate(w) if x <= STOP_TO_MS)
            stops.append({"start": _r(grid[i]), "end": _r(grid[j]), "from_ms": _r(v[i], 1)})
            last_stop = grid[i]
    return (drops, stops, _stretches(grid, v, lambda x: x <= STILL_MS, STILL_MIN_S, 1.0),
            _stretches(grid, v, lambda x: x >= RIDE_MS, RIDE_MIN_S, RIDE_GAP_S))


def _climb(s: dict, a: float, b: float) -> float:
    """Net GPS altitude gained over a..b (m), 0 without altitude."""
    if not s["alt"]:
        return 0.0
    return _interp(s["tp"], s["alt"], b) - _interp(s["tp"], s["alt"], a)


def _lifts(s: dict) -> list[dict]:
    """A chairlift: 1.5-6 m/s for a long stretch with the GPS altitude rising."""
    grid, v = _speed_grid(s)
    out = []
    for r in _stretches(grid, v, lambda x: 1.5 <= x <= 6.0, LIFT_MIN_S, 3.0) if grid else []:
        climb = _climb(s, r["start"], r["end"])
        if climb >= LIFT_CLIMB_M:
            out.append({"kind": "lift", "start": r["start"], "end": r["end"], "t": r["start"],
                        "mean_ms": r["mean_ms"], "max_ms": r["max_ms"], "climb_m": _r(climb, 0)})
    return out


def events_from_series(s: dict) -> list[dict]:
    """Every rule over one clip's series -> sorted events (shape in the module docstring)."""
    ev: list[dict] = []
    airs = find_airs(s["ta"], s["mag"])
    ev += airs
    drops, stops, stills, rides = find_speed_events(s)
    have_gps = bool(s["tp"])
    for im in find_impacts(s["ta"], s["mag"]):
        im["after_air"] = any(a["t_land"] - 0.3 <= im["t"] <= a["t_land"] + AIR_LAND_WITHIN_S
                              for a in airs)
        # No speed change around it = a hand on the camera, not a fall. Unknown without GPS.
        if have_gps and not im["after_air"]:
            before = _interp(s["tp"], s["sp"], im["t"] - 0.5)
            after = min(_interp(s["tp"], s["sp"], im["t"] + k * 0.5) for k in range(1, 7))
            im["handling"] = (before - after) < HANDLING_DROP_MS
        else:
            im["handling"] = None
        ev.append(im)
    for d in drops:
        # What the camera felt around the slowing, and whether it came to a stop: a fall is
        # a drop with a jolt (CLIP_01 70 s: 4.8 g, 4.8 -> 0.2 m/s), a turn is a drop without.
        d["peak_g"] = _r(max((m for t, m in zip(s["ta"], s["mag"])
                              if d["start"] - 1 <= t <= d["end"] + 1), default=0.0), 1)
        tail = [_interp(s["tp"], s["sp"], d["end"] + k * 0.5) for k in range(0, 5)]
        d["to_ms"] = _r(min(d["to_ms"], *tail), 1)
        d["stopped"] = d["to_ms"] <= STOP_TO_MS
        ev.append(d)
    lifts = _lifts(s)
    for st in stops:
        if any(abs(d["start"] - st["start"]) <= 1.5 for d in drops):
            continue                # folded into the speed_drop that says it came to a stop
        ev.append({"kind": "stopped", "start": st["start"], "end": st["end"], "t": st["start"],
                   "from_ms": st["from_ms"]})
    for sl in stills:
        # The same stop, found from standing still: do not list it twice.
        if not any(e["kind"] == "stopped" and abs(e["end"] - sl["start"]) < 3 for e in ev):
            ev.append({"kind": "stopped", "start": sl["start"], "end": sl["end"], "t": sl["start"]})
    ev += lifts
    for r in rides:
        if any(l["start"] - 2 <= r["start"] and r["end"] <= l["end"] + 2 for l in lifts):
            continue
        ev.append({"kind": "riding", "start": r["start"], "end": r["end"], "t": r["start"],
                   "mean_ms": r["mean_ms"], "max_ms": r["max_ms"]})
    for e in ev:
        e["who"] = "wearer"
        e["source"] = "telemetry"
        e["status"] = "felt"
        e["check"] = bool(e["kind"] == "air" or
                          (e["kind"] == "speed_drop" and (e["stopped"] or e["peak_g"] >= 3.5)) or
                          (e["kind"] == "impact" and not e.get("handling")))
        e["text"] = event_text(e)
    ev.sort(key=lambda e: (e["start"], e["kind"]))
    return ev


def event_text(e: dict) -> str:
    k = e["kind"]
    if k == "air":
        return (f"air {e['air_s']:.2f} s at {e['t_takeoff']:.2f}-{e['t_land']:.2f} "
                f"(landing {e['landing_g']:.1f} g)")
    if k == "impact":
        tag = ("air landing" if e.get("after_air") else
               "often handling" if e.get("handling") else "check")
        return f"impact {e['peak_g']:.1f} g at {e['t']:.1f} ({tag})"
    if k == "speed_drop":
        g = f", {e['peak_g']:.1f} g" if e.get("peak_g", 0) >= 3 else ""
        return f"speed {e['from_ms']:.1f} to {e['to_ms']:.1f} m/s at {e['start']:.0f}-{e['end']:.0f}{g}"
    if k == "stopped":
        return f"stopped {e['start']:.0f}-{e['end']:.0f}" + (
            f" (from {e['from_ms']:.0f} m/s)" if e.get("from_ms") else "")
    if k == "lift":
        return f"lift? {e['start']:.0f}-{e['end']:.0f} (climbing {e.get('climb_m', 0):.0f} m)"
    return f"riding {e['mean_ms']:.0f} m/s {e['start']:.0f}-{e['end']:.0f}"


# --------------------------------------------------------------------------- the sidecar

def sidecar_path(clip: str, directory: Path | str | None = None) -> Path | None:
    d = _dir(directory)
    return d / f"{_stem(clip)}.felt.json" if d else None


def signature(video: Path) -> list:
    try:
        st = video.stat()
        return [st.st_size, int(st.st_mtime)]
    except OSError:
        return [0, 0]


def build_sidecar(clip: str, video: Path, directory: Path | str | None = None,
                  force: bool = False) -> dict | None:
    """Compute and write `<STEM>.felt.json`, reusing the stage's `.gpmd.bin` dump when there
    is one. Cached on the file's signature and SCHEMA. None when the file has no gpmd stream."""
    d = _dir(directory)
    if d is None:
        raise ValueError("no telemetry directory registered")
    path = sidecar_path(clip, d)
    sig = signature(video)
    if not force and path.exists():
        try:
            cur = json.loads(path.read_text())
            if cur.get("schema") == SCHEMA and cur.get("signature") == sig:
                return cur
        except (ValueError, OSError):
            pass
    T = _tool()
    idx = T.gpmd_stream_index(video)
    if idx is None:
        return None
    packets = T.packet_table(video, idx)
    dump = d / f"{_stem(clip)}.gpmd.bin"
    buf = (dump.read_bytes() if dump.exists() and dump.stat().st_size == sum(p[2] for p in packets)
           else T.dump_gpmd(video, idx, dump))
    return write_sidecar(clip, sig, series_from_streams(T.collect_streams(buf, packets)["streams"]), d)


def write_sidecar(clip: str, sig: list, series: dict, directory: Path | str) -> dict:
    """Series -> events + the speed and roll tracks, written atomically (also the tests' door)."""
    d = Path(directory)
    events = events_from_series(series)
    grid, v = _speed_grid(series, SPEED_STEP_S)
    tg = series["tg"]
    side = {
        "schema": SCHEMA, "clip": clip, "signature": sig, "mount": series["mount"],
        "events": events,
        "speed": [[_r(g, 1), _r(x, 1)] for g, x in zip(grid, v)],
        "roll_hz": _r((len(tg) - 1) / (tg[-1] - tg[0]), 3) if len(tg) > 1 else 0,
        "roll_t0": _r(tg[0], 3) if tg else 0,
        "roll": [round(x, 1) for x in series["roll"]],
    }
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{_stem(clip)}.felt.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(side, separators=(",", ":")))
    os.replace(tmp, path)
    return side


def load_sidecar(clip: str, directory: Path | str | None = None) -> dict | None:
    p = sidecar_path(clip, directory)
    if not p or not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
    except (ValueError, OSError):
        return None
    return d if isinstance(d, dict) else None


def telemetry_events(clip: str, directory: Path | str | None = None) -> list[dict]:
    """The clip's felt events (shape in the module docstring); [] without a sidecar."""
    d = load_sidecar(clip, directory)
    return [dict(e) for e in (d or {}).get("events") or []]


def speed_series(clip: str, directory: Path | str | None = None) -> list[list[float]]:
    return list((load_sidecar(clip, directory) or {}).get("speed") or [])


def mount(clip: str, directory: Path | str | None = None) -> str | None:
    return (load_sidecar(clip, directory) or {}).get("mount")


def roll_at(clip: str, times: list[float], directory: Path | str | None = None) -> list[float]:
    """Camera roll in degrees at each time (circular interpolation of the ~30 Hz GRAV roll)."""
    d = load_sidecar(clip, directory)
    if not d or not d.get("roll"):
        return [0.0] * len(times)
    hz, t0, roll = d["roll_hz"], d["roll_t0"], d["roll"]
    n = len(roll)
    out = []
    for t in times:
        x = min(max((t - t0) * hz, 0.0), n - 1.0)
        i = int(x)
        j = min(i + 1, n - 1)
        f = x - i
        a, b = math.radians(roll[i]), math.radians(roll[j])
        out.append(round(math.degrees(math.atan2((1 - f) * math.sin(a) + f * math.sin(b),
                                                 (1 - f) * math.cos(a) + f * math.cos(b))), 1))
    return out


# --------------------------------------------------------------------------- planner words

def felt_line(clip: str, directory: Path | str | None = None, *, max_riding: int = 3) -> str:
    """The compact "felt:" text for a clip's planner block ('' when nothing was felt)."""
    return felt_from_events(telemetry_events(clip, directory), max_riding=max_riding)


MAX_IMPACTS = 4                 # more than this and the rest are one counted line


def _worth_saying(e: dict) -> bool:
    """A speed drop is a turn unless it came to a stop or the camera was jolted."""
    return e["kind"] != "speed_drop" or bool(e.get("stopped")) or e.get("peak_g", 0) >= 3.5


def felt_from_events(events: list[dict], *, max_riding: int = 3) -> str:
    if not events:
        return ""
    handling = [e for e in events if e["kind"] == "impact" and e.get("handling")]
    impacts = sorted((e for e in events if e["kind"] == "impact" and not e.get("handling")),
                     key=lambda e: -e["peak_g"])
    shown = {id(e) for e in impacts[:MAX_IMPACTS]}
    extra = impacts[MAX_IMPACTS:]
    rides = sorted((e for e in events if e["kind"] == "riding"),
                   key=lambda e: e["start"] - e["end"])[:max_riding]
    keep = [e for e in events if e["kind"] not in ("riding", "impact") and _worth_saying(e)]
    keep += [e for e in events if id(e) in shown] + rides
    keep.sort(key=lambda e: e["start"])
    parts = [e["text"] + (" (check)" if e["kind"] == "air" else "") for e in keep]
    if extra:
        parts.append(f"{len(extra)} more impacts over 5 g (max {max(e['peak_g'] for e in extra):.0f} g,"
                     f" {extra[0]['t']:.0f}-{max(e['t'] for e in extra):.0f})")
    if handling:
        parts.append(f"{len(handling)} bump{'s' if len(handling) > 1 else ''} over 5 g with no "
                     f"speed change (often handling, max {max(e['peak_g'] for e in handling):.0f} g)")
    return "felt: " + " · ".join(parts) if parts else ""


_WORDS = {"air": "air jump airborne flying landing", "impact": "impact crash hit fall bump",
          "speed_drop": "fall crash stop slowed down", "stopped": "stopped stop standing",
          "lift": "lift chairlift ride up", "riding": "riding skiing fast"}


def felt_words(clip: str, directory: Path | str | None = None) -> list[dict]:
    """Find's lexical rows: one {start, end, kind, notable, what} per event a query could mean."""
    out = []
    for e in telemetry_events(clip, directory):
        if e["kind"] == "impact" and e.get("handling"):
            continue
        out.append({"start": e["start"], "end": e["end"], "kind": e["kind"],
                    "notable": e["kind"] == "air", "what": f"{e['text']} {_WORDS.get(e['kind'], '')}"})
    return out
