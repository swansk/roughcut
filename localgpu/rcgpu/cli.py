"""python -m rcgpu <command> — every command prints one JSON document (index prints JSON lines)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import config


def _out(obj) -> None:
    print(json.dumps(obj, default=float), flush=True)


def cmd_status(a) -> int:
    config.setup_env()
    info = {"ok": False, "models_dir": str(config.models_dir()),
            "qwen": config.qwen_path().is_dir(), "pe_core": config.pe_cached()}
    try:
        import torch
        info["torch"] = torch.__version__
        info["cuda"] = bool(torch.cuda.is_available())
        if info["cuda"]:
            p = torch.cuda.get_device_properties(0)
            info.update(gpu=p.name, vram_gb=round(p.total_memory / 1e9, 1))
        import PyNvVideoCodec  # noqa: F401
        info["nvdec"] = True
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"{type(exc).__name__}: {exc}"[:200]
    info["ok"] = bool(info.get("cuda") and info.get("nvdec") and info["qwen"] and info["pe_core"])
    _out(info)
    return 0 if info["ok"] else 1


def _sensors(path):
    from . import media
    return media.Sensors.load(path)


def cmd_frames(a) -> int:
    """Extract + level the 10 fps sharpest frames of [start, end] and cache them as JPEGs."""
    import cv2

    from roughcut import gpulock

    from . import media
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with gpulock.hold("local-frames"):
        src = media.Source(a.video)
        centres = media.grid_centres(src, a.start, a.end)
        got = media.grab(src, centres, _sensors(a.sensors), level=not a.raw)
        files = []
        for c, img in got.items():
            p = out / f"{c:07d}.jpg"
            cv2.imwrite(str(p), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
            files.append({"t": round(src.t_of(c), 3), "file": p.name})
    _out({"frames": files, "levelled": bool(a.sensors) and not a.raw, "rotation_180": src.rot180})
    return 0


def cmd_check(a) -> int:
    """P(yes) per question over a window. A window longer than 3.2 s is read in 3 s pieces
    (stride 1.5 s) and each question reports the highest piece."""
    from roughcut import gpulock

    from . import media
    from .check import Checker, QUESTIONS
    qs = [q for q in a.questions.split(",") if q]
    bad = [q for q in qs if q not in QUESTIONS]
    if bad:
        _out({"ok": False, "error": f"unknown question(s): {bad}", "known": sorted(QUESTIONS)})
        return 2
    with gpulock.hold("local-check"):
        import torch
        chk = Checker()
        src = media.Source(a.video)
        sens = _sensors(a.sensors)
        half = config.CHECK_HALF_S
        pieces = []
        if a.end - a.start <= 2 * half + 0.2:
            c = (a.start + a.end) / 2
            pieces.append((c - half, c + half))
        else:
            s = a.start
            while True:
                pieces.append((s, s + 2 * half))
                if s + 2 * half >= a.end:
                    break
                s += half
        res = {q: {"p_yes": None, "pieces": []} for q in qs}
        n_frames = 0
        for (s, e) in pieces:
            centres = media.grid_centres(src, s, e)
            got = media.grab(src, centres, sens)
            grid = [(src.t_of(c), got[c]) for c in centres if c in got]
            n_frames += len(grid)
            for q in qs:
                r = chk.check(grid, q, scan=a.scan)
                res[q]["pieces"].append({"start": round(s, 2), "end": round(e, 2), **r})
        for q in qs:
            ps = [p["p_yes"] for p in res[q]["pieces"] if p["p_yes"] is not None]
            res[q]["p_yes"] = max(ps) if ps else None
        _out({"ok": True, "p_yes": {q: res[q]["p_yes"] for q in qs}, "detail": res,
              "frames": {"n": n_frames, "levelled": sens is not None},
              "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2)})
    return 0


def cmd_embed(a) -> int:
    """Embeddings only (no checks) for the clips of a job file; same files as `index`."""
    from . import pipeline
    job = json.loads(Path(a.job).read_text(encoding="utf-8"))
    job["check"] = False
    return pipeline.run_index(job)


def cmd_index(a) -> int:
    from . import pipeline
    job = json.loads(Path(a.job).read_text(encoding="utf-8"))
    return pipeline.run_index(job)


def cmd_search(a) -> int:
    import numpy as np

    from roughcut import gpulock

    from .embed import Embedder, search_windows
    d = Path(a.dir)
    items = []
    for j in sorted(d.glob("*.embed.json")):
        meta = json.loads(j.read_text(encoding="utf-8"))
        npy = d / j.name.replace(".embed.json", ".embed.npy")
        if not npy.exists():
            continue
        if a.clips and meta["clip"] not in a.clips:
            continue
        items.append((meta["clip"], np.array(meta["times"]), np.load(npy)))
    with gpulock.hold("local-search"):
        emb = Embedder()
        qs = emb.texts(a.text)
    _out({"results": {t: search_windows(qs[i], items, a.k) for i, t in enumerate(a.text)},
          "clips": [c for c, _, _ in items]})
    return 0


def cmd_encode(a) -> int:
    """Text -> PE-Core vectors ({"model", "dim", "vectors": {text: [floats]}}). The eval caches
    these so the gate can score Find from the embeddings with no GPU."""
    from roughcut import gpulock

    from .embed import Embedder
    with gpulock.hold("local-encode"):
        qs = Embedder().texts(a.text)
    _out({"model": config.PE_REPO, "dim": int(qs.shape[1]),
          "vectors": {t: [round(float(x), 6) for x in qs[i]] for i, t in enumerate(a.text)}})
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rcgpu")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    p = sub.add_parser("frames")
    p.add_argument("--video", required=True); p.add_argument("--start", type=float, required=True)
    p.add_argument("--end", type=float, required=True); p.add_argument("--out", required=True)
    p.add_argument("--sensors"); p.add_argument("--raw", action="store_true")
    p.set_defaults(fn=cmd_frames)
    p = sub.add_parser("check")
    p.add_argument("--video", required=True); p.add_argument("--start", type=float, required=True)
    p.add_argument("--end", type=float, required=True); p.add_argument("--questions", default="fall")
    p.add_argument("--sensors"); p.add_argument("--scan", action="store_true")
    p.set_defaults(fn=cmd_check)
    for name, fn in (("embed", cmd_embed), ("index", cmd_index)):
        p = sub.add_parser(name)
        p.add_argument("job", help="job JSON: {out, clips:[{stem, video, candidates, sensors}], force}")
        p.set_defaults(fn=fn)
    p = sub.add_parser("search")
    p.add_argument("--dir", required=True); p.add_argument("--text", action="append", required=True)
    p.add_argument("--k", type=int, default=3); p.add_argument("--clips", action="append")
    p.set_defaults(fn=cmd_search)
    p = sub.add_parser("encode")
    p.add_argument("--text", action="append", required=True)
    p.set_defaults(fn=cmd_encode)
    a = ap.parse_args(argv)
    return a.fn(a)
