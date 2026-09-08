# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "numpy>=2", "faster-whisper>=1.1",
#   # ctranslate2's CUDA backend links these by soname; pip-installing them
#   # avoids a system CUDA toolkit (and a sudo password we don't have).
#   "nvidia-cublas-cu12", "nvidia-cudnn-cu12>=9",
# ]
# ///
"""Dictation: one short recording in, its text out, as JSON on stdout.

The floor's note is push-to-talk (hold V, speak, release — docs/design/cutting-room-floor.html
§4.4). This is the recogniser behind it, kept out of the server process on purpose: the
server never imports faster-whisper, it shells to this file with `uv run` (roughcut/dictate.py),
so a missing model or a broken CUDA install can only ever cost one note, never the app.

Latency matters more than accuracy here — a note is a few words the editor can fix with N —
so the default model is `small`, the same family as the audio pass (which runs large-v3 on
the footage) but an order of magnitude faster to load and run. The brief's names seed the
recogniser through `initial_prompt`, which is how "Spenny" survives.

Usage:
    uv run research/tools/dictate.py IN.webm [--names Spenny,Karl] [--model small] [--device auto]

Prints one JSON object: {"text", "latency_ms", "model", "duration_s", "device"}.
Exit 2 = bad input or missing tool, 3 = recording longer than MAX_SECONDS, 4 = model failed.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

SR = 16000
MAX_SECONDS = 30.0            # a note, not a monologue; the server refuses above this too
DEFAULT_MODEL = "small"

# The same list the audio pass drops: Whisper emits these over silence with nothing said,
# and a sine tone or a breath on the mic must not become a note that says "Thank you."
HALLUCINATIONS = {
    "thank you.", "thanks for watching!", "thank you for watching.",
    "you", "bye.", ".", "so", "okay.", "oh.", "[music]", "thanks.",
    "subscribe!", "please subscribe.", "thank you very much.",
}


def decode(path: Path) -> np.ndarray:
    """The recording as mono 16 kHz float32, whatever container it arrived in.

    ffmpeg does the conversion (webm/opus from MediaRecorder, wav from anything else) and
    pipes raw PCM; that is the 16 kHz mono signal faster-whisper wants, without a temp file.
    MediaRecorder's webm carries no duration in its header, so the only trustworthy length
    is the decoded sample count — which is why the length check happens after decoding.
    """
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
         "-map", "0:a:0", "-ac", "1", "-ar", str(SR), "-f", "s16le", "pipe:1"],
        capture_output=True, check=False)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"ffmpeg could not decode {path.name}: "
                           f"{r.stderr.decode(errors='replace').strip()[-300:]}")
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0


def _preload_cuda_libs() -> None:
    """ctranslate2 dlopens cuBLAS/cuDNN by soname; pip puts them off the loader
    path. Loading them here makes the CUDA backend work without a system CUDA
    install. Failures are silent — the caller falls back to CPU."""
    import site
    for root in map(Path, site.getsitepackages()):
        for so in sorted((root / "nvidia").glob("*/lib/*.so*")):
            try:
                ctypes.CDLL(str(so), mode=ctypes.RTLD_GLOBAL)
            except OSError:
                pass


def load_model(model_size: str, device: str):
    """faster-whisper on the GPU when there is one, CPU int8 otherwise. Returns
    (model, "device/compute_type")."""
    from faster_whisper import WhisperModel
    order = ([("cuda", "float16"), ("cpu", "int8")] if device == "auto"
             else [(device, "float16" if device == "cuda" else "int8")])
    last: Exception | None = None
    for dev, ct in order:
        try:
            if dev == "cuda":
                _preload_cuda_libs()
            model = WhisperModel(model_size, device=dev, compute_type=ct)
            print(f"dictate: {model_size} on {dev}/{ct}", file=sys.stderr)
            return model, f"{dev}/{ct}"
        except Exception as exc:                    # noqa: BLE001 - reported below
            last = exc
            print(f"dictate: {dev} unavailable ({str(exc)[:120]})", file=sys.stderr)
    raise RuntimeError(f"could not load whisper: {last}")


def build_prompt(names: list[str]) -> str | None:
    """The names as Whisper context. `initial_prompt` is text the model believes it has
    already heard, so a list of the brief's names biases the spelling of what follows
    without dictating any words — "Spenny" comes back as Spenny, not "Penny"."""
    clean = [n.strip() for n in names if n and n.strip()]
    return (", ".join(clean) + ".") if clean else None


def transcribe(model, x: np.ndarray, names: list[str]) -> str:
    # vad_filter off for the same reason as the audio pass: the filter deletes quiet
    # helmet-mic speech before Whisper sees it. A note is two seconds of one voice
    # close to the mic; there is nothing to gain and a first word to lose.
    segments, _ = model.transcribe(
        x, language="en", initial_prompt=build_prompt(names), beam_size=5,
        vad_filter=False, condition_on_previous_text=False, word_timestamps=False,
    )
    parts: list[str] = []
    for s in segments:
        text = s.text.strip()
        if not text or text.lower() in HALLUCINATIONS or s.no_speech_prob > 0.6:
            continue
        parts.append(text)
    return " ".join(parts).strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="Transcribe one short recording to JSON.")
    ap.add_argument("input", type=Path, help="webm/opus, wav, or anything ffmpeg reads")
    ap.add_argument("--names", default="", help="comma-separated names to seed the recogniser")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="faster-whisper model size")
    ap.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    args = ap.parse_args()

    t0 = time.time()
    if shutil.which("ffmpeg") is None:
        print("error: ffmpeg not found on PATH", file=sys.stderr)
        return 2
    if not args.input.is_file():
        print(f"error: no such recording: {args.input}", file=sys.stderr)
        return 2
    try:
        x = decode(args.input)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    duration = len(x) / SR
    if duration > MAX_SECONDS:
        print(f"error: recording is {duration:.1f} s — notes are at most {MAX_SECONDS:.0f} s",
              file=sys.stderr)
        return 3

    names = [n for n in args.names.split(",") if n.strip()]
    try:
        model, device = load_model(args.model, args.device)
        text = transcribe(model, x, names)
    except Exception as exc:                        # noqa: BLE001 - one note, not the app
        print(f"error: {exc}", file=sys.stderr)
        return 4

    print(json.dumps({
        "text": text,
        "latency_ms": int((time.time() - t0) * 1000),
        "model": args.model,
        "duration_s": round(duration, 2),
        "device": device,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
