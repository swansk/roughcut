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

from pathlib import Path
from typing import Callable

SCHEMA = 1                      # bump when the rules change: the cache is keyed on it


def use_telemetry_dir(provider: Callable[[], Path] | Path | str | None) -> None:
    """The server's registration: where `<STEM>.felt.json` lives (a path or a callable)."""
    global _DIR
    _DIR = provider


_DIR: Callable[[], Path] | Path | str | None = None


def telemetry_events(clip: str, directory: Path | str | None = None) -> list[dict]:
    """The clip's felt events (shape in the module docstring). STUB: filled in next commit."""
    return []
