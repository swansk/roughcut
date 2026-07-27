# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2"]
# ///
"""Put a music bed under a finished cut, and measure whether the ducking works.

Ear-testing a bed is a bad first check: you cannot tell "the sidechain is running" from
"the track happens to be quiet here". So this renders the bed twice — once mixed under
the film, once *alone* through the same graph — and compares the bed's own level during
the film's loud passages against its level in the quiet ones. If ducking is working the
difference is large and negative; if the filter is a no-op it is about zero.

No transcript needed: the loud passages *are* what the sidechain keys on.

Usage:
    uv run music_check.py CUT.mp4 --music TRACK.mp3 -o SCORED.mp4 [--gain -18]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import effects            # noqa: E402  (after sys.path)

SR = 16000
WIN = 0.1                               # 100 ms analysis frames


def pcm(path: Path) -> np.ndarray:
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-map", "0:a:0",
         "-f", "f32le", "-ar", str(SR), "-ac", "1", "-"],
        capture_output=True)
    if r.returncode != 0:
        raise SystemExit(f"could not read audio from {path}: {r.stderr[-300:]}")
    return np.frombuffer(r.stdout, dtype=np.float32)


def frame_db(x: np.ndarray) -> np.ndarray:
    n = int(SR * WIN)
    usable = len(x) - len(x) % n
    frames = x[:usable].reshape(-1, n)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-12)


def main() -> int:
    ap = argparse.ArgumentParser(description="Score a cut and measure the ducking.")
    ap.add_argument("cut", type=Path, help="an already-rendered film")
    ap.add_argument("--music", type=Path, required=True)
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--gain", type=float, default=None,
                    help="explicit dB. Omitted, the gain is computed from the track's "
                         "measured loudness to land at --bed-lufs")
    ap.add_argument("--bed-lufs", type=float, default=effects.TARGET_BED_LUFS,
                    help="where the bed should sit; films here master to -16 LUFS")
    ap.add_argument("--no-duck", action="store_true")
    ap.add_argument("--edl", type=Path, default=None,
                    help="the EDL this cut was rendered from. With --sidecars it lets "
                         "the bed duck on *speech* rather than on amplitude, which is "
                         "the difference between mixing and pumping")
    ap.add_argument("--sidecars", type=Path, default=None)
    ap.add_argument("--duck-db", type=float, default=effects.SPEECH_DUCK_DB,
                    help="how far the bed drops under a line, in dB. 10-14 is the "
                         "usual range; past 20 it is a mute, not a duck")
    args = ap.parse_args()

    if not args.cut.exists():
        raise SystemExit(f"no such cut: {args.cut}")
    spec = {"asset": str(args.music.expanduser()), "gain_db": args.gain,
            "bed_lufs": args.bed_lufs, "duck": not args.no_duck,
            "duck_db": args.duck_db}

    speech = None
    if args.edl and args.sidecars:
        import json
        segments = json.loads(args.edl.read_text(encoding="utf-8"))["segments"]
        speech = effects.speech_regions(segments, args.sidecars.expanduser())
        talk = sum(hi - lo for lo, hi in speech)
        print(f"speech: {len(speech)} regions, {talk:.0f}s of talking")

    applied = effects.add_music(args.cut, spec, args.out, speech=speech)
    measured = applied.get("measured_lufs")
    print(f"track measures {measured} LUFS" if measured is not None
          else "track loudness unmeasurable — falling back to a fixed gain")
    print(f"scored {args.out.name}  ({applied['film_s']:.1f}s, bed at "
          f"{applied['gain_db']}dB → {applied['bed_lufs']} LUFS, "
          f"{'ducked' if applied['duck'] else 'flat'})")

    # Same `speech` as the scored render: measuring an amplitude-keyed bed while
    # shipping a speech-keyed one would report numbers for a file nobody will hear.
    bed_path = args.out.with_name(args.out.stem + ".bedonly.m4a")
    effects.add_music(args.cut, spec, bed_path, bed_only=True, speech=speech)

    film, bed = frame_db(pcm(args.cut)), frame_db(pcm(bed_path))
    n = min(len(film), len(bed))
    film, bed = film[:n], bed[:n]

    # With speech regions known, compare the bed inside them against outside — the
    # thing the bed is supposed to get out of the way of. Without them, fall back to
    # the film's own loud and quiet thirds.
    if speech:
        t = np.arange(len(bed)) * WIN
        loud = np.zeros(len(bed), dtype=bool)
        for lo, hi in speech:
            loud |= (t >= lo) & (t < hi)
        quiet = ~loud
        label = ("under speech", "between lines")
    else:
        loud = film >= np.percentile(film, 67)
        quiet = film <= np.percentile(film, 33)
        label = ("under the loud", "in the gaps")
    under_speech, in_gaps = bed[loud].mean(), bed[quiet].mean()
    duck_db = in_gaps - under_speech

    print(f"\nfilm:  {label[0]:14s}{film[loud].mean():6.1f} dB   "
          f"{label[1]:14s}{film[quiet].mean():6.1f} dB")
    print(f"bed:   {label[0]:14s}{under_speech:6.1f} dB   "
          f"{label[1]:14s}{in_gaps:6.1f} dB")
    print(f"\nducking: {duck_db:+.1f} dB — the bed sits {duck_db:.1f} dB lower "
          f"{label[0]}")
    if duck_db > 20:
        print("that is a mute, not a duck - try a smaller --duck-db")
    if not spec["duck"]:
        print("(ducking off, so anything much beyond 0 here would be suspicious)")
    elif duck_db < 3:
        print("WARNING: less than 3 dB of duck. Either the bed is already so quiet it "
              "cannot drop further, or the sidechain is not biting on this material.")
    bed_path.unlink(missing_ok=True)
    print(f"\nlisten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
