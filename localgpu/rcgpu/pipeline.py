"""The index job: per clip, embed for Find and CHECK the telemetry candidates.

One process loads the two models once and walks the clips it is given, taking the GPU lock
for each clip and stepping aside if another job (the audio pass) is queued. A clip's results
land atomically — `<stem>.embed.npy`, `<stem>.embed.json`, then `<stem>.local.json` last — so
a killed run leaves whole clips and nothing half-written, and the next run skips what is done.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np

from . import config, media
from .rules import questions_for, verdict  # noqa: F401

VERSION = 1


def emit(**kw) -> None:
    print(json.dumps(kw, default=float), flush=True)


def atomic_json(path: Path, obj) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=float), encoding="utf-8")
    os.replace(tmp, path)


class GpuMeter:
    def __init__(self, torch) -> None:
        self.torch = torch
        self.t = {}

    def span(self, name):
        meter = self

        class _S:
            def __enter__(s):
                meter.torch.cuda.synchronize()
                s.t0 = time.time()

            def __exit__(s, *a):
                meter.torch.cuda.synchronize()
                meter.t[name] = meter.t.get(name, 0.0) + time.time() - s.t0
        return _S()


def window_centres(src: media.Source, t: float, half: float) -> list[int]:
    return media.grid_centres(src, t - half, t + half)


def embed_clip(src: media.Source, emb, meter: GpuMeter, out_dir: Path, stem: str) -> dict:
    """Embeddings at 2 fps from raw frames (the bake-off's best Find recipe), written as
    <stem>.embed.npy + <stem>.embed.json. Sparse access: only every 15th frame is decoded."""
    torch = meter.torch
    step = max(1, round(src.fps / config.EMBED_FPS))
    idx = list(range(0, src.n, step))
    with meter.span("embed"):
        buf = []
        vec = []
        for i in idx:
            buf.append(emb.squash(src.small(i)))
            if len(buf) == 64:
                vec.append(emb.images(torch.stack(buf)))
                buf = []
        if buf:
            vec.append(emb.images(torch.stack(buf)))
    e = np.concatenate(vec) if vec else np.zeros((0, 768), np.float16)
    np.save(out_dir / f"{stem}.embed.npy.tmp.npy", e)
    os.replace(out_dir / f"{stem}.embed.npy.tmp.npy", out_dir / f"{stem}.embed.npy")
    meta = {"clip": stem, "model": config.PE_REPO, "fps": config.EMBED_FPS, "n": int(len(e)),
            "dim": int(e.shape[1]) if len(e) else 0, "frames": idx, "src_fps": src.fps,
            "times": [round(i / src.fps, 3) for i in idx], "levelled": False}
    atomic_json(out_dir / f"{stem}.embed.json", meta)
    return {"file": f"{stem}.embed.npy", "index": f"{stem}.embed.json", "n": meta["n"],
            "fps": config.EMBED_FPS, "model": config.PE_REPO}


def check_clip(src: media.Source, chk, meter: GpuMeter, sensors, cands: list[dict],
               half: float = config.CHECK_HALF_S) -> list[dict]:
    """Ask each candidate its questions over +-half s. Frames are grabbed once per distinct
    window and shared by that candidate's questions."""
    out = []
    have: dict[int, object] = {}            # neighbouring candidates share frames
    for cand in cands:
        t = float(cand["t"])
        with meter.span("frames"):
            centres = window_centres(src, t, half)
            missing = [c for c in centres if c not in have]
            if missing:
                have.update(media.grab(src, missing, sensors))
        got = have
        grid = [(src.t_of(c), got[c]) for c in centres if c in got]
        info = {"t0": round(grid[0][0], 2) if grid else None, "t1": round(grid[-1][0], 2) if grid else None,
                "n": len(grid), "fps": config.GRID_FPS, "levelled": sensors is not None, "sharp_of": 5}
        for q in questions_for(cand):
            t0 = time.time()
            with meter.span("check"):
                r = chk.check(grid, q)
            p = r.get("p_yes")
            out.append({"t": t, "cand": cand, "q": q, "p_yes": p, "verdict": verdict(q, p),
                        "threshold": config.FALL_THRESHOLD if q == "fall" else None,
                        "settles": q == "fall", "frames": info,
                        **{k: v for k, v in r.items() if k.startswith("scan_")},
                        "gpu_s": round(time.time() - t0, 3)})
    return out


def run_index(job: dict) -> int:
    """job: {"out": dir, "clips": [{"stem", "video", "candidates": [...], "sensors": npz|None}],
    "force": bool, "check": bool, "embed": bool}"""
    import torch

    from roughcut import gpulock

    from .check import Checker
    from .embed import Embedder

    out_dir = Path(job["out"])
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = []
    for c in job["clips"]:
        done = (out_dir / f"{c['stem']}.local.json").exists()
        if done and not job.get("force"):
            emit(event="clip_skipped", clip=c["stem"], why="already done")
        else:
            todo.append(c)
    if not todo:
        emit(event="done", clips=0)
        return 0
    lock = gpulock.Lock("local")
    emit(event="waiting", why="gpu lock")
    lock.acquire()
    t_load = time.time()
    emb = Embedder() if job.get("embed", True) else None
    chk = Checker() if job.get("check", True) else None
    emit(event="loaded", seconds=round(time.time() - t_load, 1))
    peak = 0.0
    try:
        for n, c in enumerate(todo):
            stem = c["stem"]
            if n and gpulock.waiting():
                torch.cuda.empty_cache()          # the models stay loaded; the cache goes back
                if lock.yield_to_waiters():
                    emit(event="yielded", clip=stem)
            torch.cuda.reset_peak_memory_stats()
            t_clip = time.time()
            meter = GpuMeter(torch)
            try:
                src = media.Source(c["video"])
                sensors = media.Sensors.load(c.get("sensors"))
                rec = {"version": VERSION, "clip": stem, "video": c["video"], "model": "Qwen3.5-4B nf4",
                       "embed_model": config.PE_REPO, "threshold_fall": config.FALL_THRESHOLD,
                       "rotation_180": src.rot180, "levelled": sensors is not None,
                       "duration_s": round(src.duration, 3), "candidates": len(c.get("candidates") or [])}
                rec["embed"] = embed_clip(src, emb, meter, out_dir, stem) if emb else None
                rec["checks"] = check_clip(src, chk, meter, sensors, c.get("candidates") or []) if chk else []
            except Exception as exc:  # noqa: BLE001 — one clip's failure must not cost the rest
                emit(event="clip_failed", clip=stem, error=f"{type(exc).__name__}: {exc}"[:300])
                torch.cuda.empty_cache()
                continue
            peak = max(peak, torch.cuda.max_memory_allocated() / 1e9)
            del src
            rec["gpu"] = {"name": torch.cuda.get_device_name(0),
                          "seconds": {k: round(v, 2) for k, v in meter.t.items()},
                          "gpu_s": round(sum(meter.t.values()), 2),
                          "wall_s": round(time.time() - t_clip, 2),
                          "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2),
                          "peak_vram_reserved_gb": round(torch.cuda.max_memory_reserved() / 1e9, 2)}
            rec["done_at"] = time.time()
            atomic_json(out_dir / f"{stem}.local.json", rec)
            import gc
            gc.collect()
            torch.cuda.empty_cache()                  # a clip's decoder and caches go back before the next
            emit(event="clip_done", clip=stem, checks=len(rec["checks"]), gpu_s=rec["gpu"]["gpu_s"],
                 wall_s=rec["gpu"]["wall_s"], peak_vram_gb=rec["gpu"]["peak_vram_gb"], i=n + 1, of=len(todo))
    finally:
        lock.release()
    emit(event="done", clips=len(todo), peak_vram_gb=round(peak, 2))
    return 0
