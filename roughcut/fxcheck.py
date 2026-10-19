"""The Designed Effects check, out of the server's process (wave 14, verdict 10 punch 1).

Verdict 10: one AI "soft vignette" check ran 12+ minutes at 100 % CPU *inside the
server*, read "0%" throughout, and held the event loop 145 times (up to 996 ms). The
proof drew 240 identical 720p frames with PIL on a job thread — each a 10 672-px canvas
per shape, the GIL held ~250 ms at a time — so every request waited on it, and a
dropped file uploaded at ~1 MB/s.

The check is now three things:

  * `proof(...)`: the one shot rendered from its proxy at 720p without the effect
    (`base.mp4`) and with it (`proof.mp4`), each ffmpeg reporting how far it is; the
    overlay drawn once per distinct frame (`fx.overlay_runs`) and a static one looped
    (`fx.part_graph`), so a vignette is one frame;
  * `check(spec, on_progress)`: the proof plus `fx.verify`'s measured checklist, with
    the frames drawn and the time each stage took (`stats`);
  * `main()`: the same in a child process — `python -m roughcut.fxcheck SPEC OUT` —
    which the server starts in its procscope and reads progress lines from
    (`{"stage", "part"}` / `{"stage", "done": true}` on stdout, the result as JSON in
    OUT). Nothing of it runs on a server thread: not the drawing, not the decode
    numpy does to measure.

The child's tools stay in its process group, so the server's Stop (a scope kill: the
group's SIGTERM) ends all of them, and the child takes its group down with it when it
is told to go or when the server dies (`setpriv --pdeathsig TERM` in front of it).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

from roughcut import fx

# The functions the child would run. A test that replaces one of them in the server's
# process (the suite's stubbed renderer) means "run what I gave you": the check then
# runs on the job's thread, where the replacement is (`unpatched`).
_ORIGINAL = {name: getattr(fx, name) for name in
             ("part_graph", "verify", "render_overlay_mov", "render_overlay_still",
              "render_overlay_frame", "synth_sound")}

STAGES = ("base", "overlay", "proof", "measure")
X264_THREADS = 3            # half the box at most: the check is not an export


def unpatched() -> bool:
    return all(getattr(fx, name) is fn for name, fn in _ORIGINAL.items())


Progress = Callable[[str, float, bool], None]      # (stage, part 0..1, done)


def _quiet(stage: str, part: float, done: bool) -> None:
    return None


def _ffmpeg(cmd: list[str], seconds: float, stage: str, on_progress: Progress) -> None:
    """Run one ffmpeg, reporting `out_time` over `seconds` as the stage's part."""
    with tempfile.TemporaryFile() as err:
        p = subprocess.Popen([cmd[0], "-progress", "pipe:1", "-nostats", *cmd[1:]],
                             stdout=subprocess.PIPE, stderr=err, text=True)
        assert p.stdout is not None
        for line in p.stdout:
            key, _, val = line.strip().partition("=")
            if key in ("out_time_us", "out_time_ms") and val.strip().lstrip("-").isdigit():
                done_s = max(0.0, int(val) / 1e6)
                on_progress(stage, min(0.99, done_s / max(seconds, 1e-3)), False)
        p.wait()
        if p.returncode != 0:
            err.seek(0)
            raise RuntimeError(f"{stage} render failed: "
                               f"{err.read().decode(errors='replace')[-300:]}")
    on_progress(stage, 1.0, True)


def proof(e: dict, seg: dict, src: Path, d: Path, *, w: int, h: int, fps,
          on_progress: Progress = _quiet) -> tuple[Path, Path, dict]:
    """The one shot rendered from `src` (its proxy) at w × h, with the effect
    (`proof.mp4`) and without (`base.mp4`), so the checks can measure a difference and
    the human can watch the result in seconds rather than after a whole render.
    Returns the two paths and `{overlay_frames, sprites_drawn, base_s, overlay_s,
    proof_s}`."""
    d.mkdir(parents=True, exist_ok=True)
    dur = float(seg["out"]) - float(seg["in"])
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p")
    af = "aresample=48000:first_pts=0,aformat=channel_layouts=stereo"
    head = ["ffmpeg", "-v", "error", "-y", "-nostdin", "-ss", f"{float(seg['in']):.3f}",
            "-i", str(src)]
    tail = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-threads", str(X264_THREADS),
            "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-movflags", "+faststart"]
    base, out = d / "base.mp4", d / "proof.mp4"
    stats: dict = {}
    t0 = time.perf_counter()
    _ffmpeg(head + ["-t", f"{dur:.3f}", "-vf", vf, "-af", af,
                    "-map", "0:v:0", "-map", "0:a:0?", "-dn"] + tail + [str(base)],
            dur, "base", on_progress)
    t1 = time.perf_counter()
    evs = fx.events_in_shot(e, seg)
    drawn, sprites = fx.RENDERED["frames"], fx.RENDERED["sprites"]
    on_progress("overlay", 0.0, False)
    extra, graph, vout, aout = fx.part_graph([(e, evs)], w, h, fps, d)
    on_progress("overlay", 1.0, True)
    stats["overlay_frames"] = fx.RENDERED["frames"] - drawn
    stats["sprites_drawn"] = fx.RENDERED["sprites"] - sprites
    t2 = time.perf_counter()
    fc = f"[0:v]{vf}[vbase];[0:a]{af}[abase]" + (";" + graph if graph else "")
    # the extra inputs sit between the source and `-t`: after `-i` a `-t` is an input
    # option for the NEXT input, and the sprites must not be cut short
    _ffmpeg(head + extra + ["-t", f"{dur:.3f}", "-filter_complex", fc,
                            "-map", f"[{vout}]", "-map", f"[{aout}]", "-dn"] + tail + [str(out)],
            dur, "proof", on_progress)
    t3 = time.perf_counter()
    stats.update(base_s=round(t1 - t0, 2), overlay_s=round(t2 - t1, 2),
                 proof_s=round(t3 - t2, 2))
    return out, base, stats


def check(spec: dict, on_progress: Progress = _quiet) -> dict:
    """The proof and the measured checklist for `spec` = `{effect, seg, src, dir, w, h,
    fps, onset, hz, impact}`. Returns `{verify, stats}`."""
    e, seg = spec["effect"], spec["seg"]
    out, base, stats = proof(e, seg, Path(spec["src"]), Path(spec["dir"]), w=int(spec["w"]),
                             h=int(spec["h"]), fps=spec["fps"], on_progress=on_progress)
    on_progress("measure", 0.0, False)
    t0 = time.perf_counter()
    v = fx.verify(e, seg, onset=spec.get("onset"), hz=float(spec.get("hz") or fx.ONSET_HZ),
                  part=out, base=base, impact=bool(spec.get("impact")))
    stats["measure_s"] = round(time.perf_counter() - t0, 2)
    on_progress("measure", 1.0, True)
    return {"verify": v, "stats": stats}


def _die_with_group(*_a) -> None:
    """Told to go (the server's Stop, or the server died: pdeathsig): every tool this
    check started goes with it."""
    try:
        os.killpg(0, signal.SIGKILL)
    finally:
        os._exit(143)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    spec_path, out_path = Path(argv[0]), Path(argv[1])
    parent = os.environ.get("ROUGHCUT_FX_PARENT")
    if parent and parent.isdigit() and os.getppid() != int(parent):
        return 3                                    # the server was gone before we began
    signal.signal(signal.SIGTERM, _die_with_group)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    last = {"t": 0.0}

    def say(stage: str, part: float, done: bool) -> None:
        now = time.monotonic()
        if done or now - last["t"] >= 0.2:
            last["t"] = now
            print(json.dumps({"stage": stage, "part": round(part, 3), "done": done}),
                  flush=True)

    tmp = out_path.with_name(out_path.name + ".tmp")
    try:
        res = check(spec, say)
        code = 0
    except Exception as exc:  # noqa: BLE001 — the server reads the sentence
        res = {"error": f"{type(exc).__name__}: {exc}"[:400]}
        code = 1
    tmp.write_text(json.dumps(res), encoding="utf-8")
    tmp.replace(out_path)
    return code


if __name__ == "__main__":
    sys.exit(main())
