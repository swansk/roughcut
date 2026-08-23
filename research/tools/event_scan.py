# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Find the places in a bin worth looking at closely — free, before any model call.

The visual pass samples every 4s and a jump lasts one or two seconds, so whether an
air is seen at all is a coin toss on phase. Sampling the whole bin at 1s would cost
four times as much for frames that are almost all snow. This is the cheap half of the
answer: a motion track off the 720p proxy (`scdet`'s frame difference, ~6s per clip)
combined with the 10 Hz onset track every audio sidecar has carried since R8, giving a
short list of *candidate windows* — the places where the picture or the sound does
something unusual for this clip.

Nothing here is a claim that something happened. A whiteout, a lens wipe and a backflip
spike the same track; the fine visual read (`visual_pass.py --windows`) is what tells
them apart. This only has to put the backflip on the list.

Usage:
    uv run event_scan.py PROXY_DIR --sidecars AUDIO_DIR --visual VISUAL_DIR \\
        [--only CLIP_07,CLIP_11] [--limit 8] [--windows-out windows.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import events                       # noqa: E402  (after sys.path)

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".mts", ".webm"}


def onset_track(sidecars: Path, stem: str) -> list[float]:
    p = sidecars / f"{stem}.audio.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8")).get("tracks", {}).get("onset", [])


def load_track(proxy: Path, cache_dir: Path, force: bool = False) -> list[float]:
    """The motion track, cached beside the visual sidecars.

    Cheap enough to recompute (~6s) and expensive enough to be annoying over a whole
    bin (~90s), so it is cached for the same reason the proxies are: nothing about the
    footage changed between two runs of the same scan.
    """
    cache = cache_dir / f"{proxy.stem}.motion.json"
    if cache.exists() and not force:
        d = json.loads(cache.read_text(encoding="utf-8"))
        if d.get("hz") == events.MOTION_HZ and d.get("mafd"):
            return d["mafd"]
    mafd = [round(v, 3) for v in events.motion_track(proxy)]
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"clip": proxy.name, "hz": events.MOTION_HZ,
                                 "width": events.MOTION_WIDTH, "mafd": mafd}),
                     encoding="utf-8")
    return mafd


def scan(proxies: Path, sidecars: Path, visual: Path, only: set[str], limit: int,
         around: float, force: bool) -> dict[str, dict]:
    out: dict[str, dict] = {}
    clips = sorted(p for p in proxies.iterdir()
                   if p.suffix.lower() in VIDEO_SUFFIXES
                   and (not only or p.stem.upper() in only))
    for proxy in clips:
        mafd = load_track(proxy, visual, force)
        track = events.excitement(mafd, onset_track(sidecars, proxy.stem))
        windows = events.candidate_windows(track, limit=limit, half_width_s=around)
        out[proxy.stem.upper()] = {"track": track, "windows": windows}
        print(f"{proxy.stem}: {len(track)} samples, {len(windows)} window(s) — "
              + ", ".join(f"{w['at']:.1f}s(z{w['z']:.1f})" for w in windows),
              flush=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("proxies", type=Path, help="directory of 720p proxies")
    ap.add_argument("--sidecars", type=Path, required=True,
                    help="audio sidecars, for the 10 Hz onset track")
    ap.add_argument("--visual", type=Path, required=True,
                    help="visual sidecar directory — motion tracks cache here")
    ap.add_argument("--only", default="", help="comma-separated stems")
    ap.add_argument("--limit", type=int, default=8,
                    help="candidate windows per clip, most unusual first")
    ap.add_argument("--around", type=float, default=events.WINDOW_HALF_S,
                    help="half-width of a window, seconds")
    ap.add_argument("--force", action="store_true", help="recompute motion tracks")
    ap.add_argument("--windows-out", type=Path, default=None,
                    help="write the windows as JSON for visual_pass.py --windows")
    args = ap.parse_args()

    if not args.proxies.is_dir():
        print(f"error: no proxies at {args.proxies}", file=sys.stderr)
        return 2
    only = {s.strip().upper() for s in args.only.split(",") if s.strip()}
    found = scan(args.proxies, args.sidecars, args.visual, only, args.limit,
                 args.around, args.force)
    if args.windows_out:
        args.windows_out.parent.mkdir(parents=True, exist_ok=True)
        args.windows_out.write_text(json.dumps(
            {stem: [[w["start"], w["end"]] for w in d["windows"]]
             for stem, d in found.items() if d["windows"]}, indent=1),
            encoding="utf-8")
        print(f"wrote {args.windows_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
