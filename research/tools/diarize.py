# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2", "pyannote.audio>=4.0,<5", "torch==2.11.0", "torchaudio==2.11.0"]
#
# [tool.uv.sources]
# torch = { index = "pytorch-cu128" }
# torchaudio = { index = "pytorch-cu128" }
#
# [[tool.uv.index]]
# name = "pytorch-cu128"
# url = "https://download.pytorch.org/whl/cu128"
# explicit = true
# ///
"""Who spoke when: pyannote community-1 speaker turns for one clip, as JSON.

Its own uv environment (this inline block), so neither the app's env nor the local-GPU
project's gets pyannote's pins. The owner's HuggingFace token is read at run time from
`$HF_TOKEN` or `~/.config/roughcut/hf-token` and handed to the library in memory; it is
never printed, logged or written to the output. With no token the tool exits 3 with
"not available: add a HuggingFace token" and writes nothing.

GPU: one job at a time on the 12 GB card, through the same flock file the other GPU
stages use (`$ROUGHCUT_GPU_LOCK` or ~/.cache/roughcut/gpu.lock). The model is ~1.5 GB
resident; this tool logs the peak VRAM it saw and refuses to start a second pipeline.
`--device cpu` works and is slow (several times real time for a long clip).

Models cache under $ROUGHCUT_MODELS/hf (default ~/.cache/roughcut/models/hf).

    uv run diarize.py CLIP.MP4 -o CLIP.speakers.json [--device auto|cuda|cpu]
        [--num-speakers N | --max-speakers N]
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

SR = 16000
MODEL = "pyannote/speaker-diarization-community-1"
NOT_AVAILABLE = 3
BATCH = 8
CAP_GB = 3.0            # VRAM ceiling for this process, GB
TOKEN_FILE = Path.home() / ".config" / "roughcut" / "hf-token"


def token() -> str | None:
    t = os.environ.get("HF_TOKEN", "").strip()
    if t:
        return t
    with contextlib.suppress(OSError):
        t = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if t:
            return t
    return None


def lock_path() -> Path:
    env = os.environ.get("ROUGHCUT_GPU_LOCK")
    return Path(env) if env else Path.home() / ".cache" / "roughcut" / "gpu.lock"


@contextlib.contextmanager
def gpu_lock(owner: str):
    """flock on the shared lock file (same protocol as roughcut/gpulock.py: the holder
    writes {owner, pid, since}; the kernel drops it if we die)."""
    p = lock_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o644)
    t0 = time.monotonic()
    fcntl.flock(fd, fcntl.LOCK_EX)
    os.ftruncate(fd, 0)
    os.pwrite(fd, json.dumps({"owner": owner, "pid": os.getpid(),
                              "since": time.time()}).encode(), 0)
    print(f"gpu lock held after {time.monotonic() - t0:.1f}s wait", file=sys.stderr)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def decode(path: Path):
    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-map", "0:a:0",
                        "-ac", "1", "-ar", str(SR), "-f", "s16le", "pipe:1"],
                       capture_output=True)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("ffmpeg could not decode audio: "
                           + r.stderr.decode(errors="replace")[-200:])
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0


def turns_of(annotation) -> list[dict]:
    out = [{"start": round(float(seg.start), 3), "end": round(float(seg.end), 3),
            "speaker": str(label)} for seg, _, label in annotation.itertracks(yield_label=True)]
    out.sort(key=lambda t: (t["start"], t["end"]))
    return out


def run(path: Path, device: str, num: int | None, mx: int | None, batch: int) -> dict:
    import torch
    tok = token()
    if tok is None:
        raise SystemExit(NOT_AVAILABLE)
    models = Path(os.environ.get("ROUGHCUT_MODELS") or Path.home() / ".cache/roughcut/models")
    os.environ.setdefault("HF_HOME", str(models / "hf"))
    from pyannote.audio import Pipeline
    x = decode(path)
    dur = len(x) / SR
    use = "cuda" if device == "cuda" or (device == "auto" and torch.cuda.is_available()) else "cpu"
    t0 = time.time()
    pipe = Pipeline.from_pretrained(MODEL, token=tok)
    del tok
    if pipe is None:
        raise RuntimeError("could not load the pipeline (token without access to "
                           f"{MODEL}? accept its terms on huggingface.co)")
    pipe.to(torch.device(use))
    # the shipped 32/32 batches peaked at ~9 GB on a 4-minute clip; the card is shared
    pipe.segmentation_batch_size = batch
    pipe.embedding_batch_size = batch
    load_s = time.time() - t0
    if use == "cuda":
        # A hard ceiling on this process (the card is shared): some conv shapes make cuDNN
        # reserve a ~9 GB autotune workspace; with a cap it picks an algorithm that fits.
        cap = float(os.environ.get("ROUGHCUT_DIARIZE_VRAM_GB") or CAP_GB)
        total = torch.cuda.get_device_properties(0).total_memory / 2**30
        torch.cuda.set_per_process_memory_fraction(min(1.0, cap / total))
        torch.cuda.reset_peak_memory_stats()
    wave = torch.from_numpy(x).unsqueeze(0)
    kw = {k: v for k, v in (("num_speakers", num), ("max_speakers", mx)) if v}
    t1 = time.time()
    out = pipe({"waveform": wave, "sample_rate": SR}, **kw)
    if use == "cuda":
        torch.cuda.synchronize()
    run_s = time.time() - t1
    excl = getattr(out, "exclusive_speaker_diarization", None)
    full = getattr(out, "speaker_diarization", out)
    return {
        "clip": path.name, "duration_s": round(dur, 2), "model": MODEL,
        "device": use, "load_s": round(load_s, 1), "gpu_s": round(run_s, 1),
        "peak_vram_mb": (round(torch.cuda.max_memory_allocated() / 2**20)
                         if use == "cuda" else None),
        "peak_vram_reserved_mb": (round(torch.cuda.max_memory_reserved() / 2**20)
                                  if use == "cuda" else None),
        "turns": turns_of(full),
        # one speaker at a time; what the transcript alignment prefers
        "exclusive": turns_of(excl) if excl is not None else None,
        "speakers": sorted({str(l) for _, _, l in full.itertracks(yield_label=True)}),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="pyannote community-1 speaker turns.")
    ap.add_argument("input", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    ap.add_argument("--num-speakers", type=int)
    ap.add_argument("--max-speakers", type=int)
    ap.add_argument("--batch", type=int, default=BATCH,
                    help="segmentation and embedding batch size (VRAM scales with it)")
    ap.add_argument("--no-lock", action="store_true", help="skip the GPU lock (CPU runs)")
    a = ap.parse_args()
    if token() is None:
        print("diarization not available: add a HuggingFace token "
              f"(~/.config/roughcut/hf-token or $HF_TOKEN)", file=sys.stderr)
        return NOT_AVAILABLE
    cpu = a.device == "cpu"
    ctx = contextlib.nullcontext() if (cpu or a.no_lock) else gpu_lock("diarize")
    with ctx:
        try:
            res = run(a.input, a.device, a.num_speakers, a.max_speakers, a.batch)
        except SystemExit as e:
            return int(e.code or 0)
        except Exception as exc:                         # noqa: BLE001 - one line, no token
            msg = str(exc).replace(token() or "\0", "***")
            print(f"error: {type(exc).__name__}: {msg[:300]}", file=sys.stderr)
            return 1
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(f"{a.input.name}: {res['duration_s']}s  {len(res['speakers'])} speakers  "
          f"{res['device']} {res['gpu_s']}s  vram {res['peak_vram_mb']} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
