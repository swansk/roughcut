"""Dictation: a spoken note on a moment, as text.

The floor's note is a push-to-talk gesture (hold V), and this is what turns the recording
into the text that rides with the keep (docs/design/cutting-room-floor.html §4.4). The
recogniser lives in `research/tools/dictate.py` — ffmpeg to 16 kHz mono, faster-whisper on
the GPU when there is one, the brief's names seeding the prompt so "Spenny" survives — and
this module only shells to it. faster-whisper is never imported here: the server process
stays free of CUDA state, and a broken model install costs one note, never the app.

Every failure surfaces as `NotAvailable` with a message the floor can show; the server maps
it to 501. `TooLong` is the one refinement the lead may want to map to 413 instead.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path


class NotAvailable(RuntimeError):
    """Dictation is not implemented, its model is not installed, or this note failed."""


class TooLong(NotAvailable):
    """The recording is longer than MAX_SECONDS. A note, not a monologue."""


MAX_SECONDS = 30.0
TIMEOUT_S = 180.0              # generous: the first call after a fresh install pulls the model
SR = 16000

TOOL = Path(__file__).resolve().parents[1] / "research" / "tools" / "dictate.py"


def available() -> bool:
    """ffmpeg and `uv` on PATH and the tool file where we expect it. Says nothing about
    the model — that is only known by running it, and the floor learns that from a 501."""
    return (shutil.which("ffmpeg") is not None and shutil.which("uv") is not None
            and TOOL.is_file())


def dictate_cmd(path: Path, names: list[str]) -> list[str]:
    """The recogniser, as a command. A function so the tests can replace it: running
    faster-whisper for real means a GPU and a model download, neither of which says
    anything about whether the plumbing around it works."""
    cmd = ["uv", "run", "--quiet", str(TOOL), str(path)]
    if names:
        cmd += ["--names", ",".join(names)]
    return cmd


def duration_s(path: Path) -> float:
    """Seconds of audio in the recording, by decoding it. ffprobe cannot be trusted here:
    MediaRecorder's webm has no duration in its header. Decoding 30 s to 16 kHz mono is
    a few milliseconds and doubles as the check that the bytes are a recording at all."""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
         "-map", "0:a:0", "-ac", "1", "-ar", str(SR), "-f", "s16le", "pipe:1"],
        capture_output=True, check=False)
    if r.returncode != 0 or not r.stdout:
        lines = r.stderr.decode(errors="replace").strip().splitlines()
        raise NotAvailable("the recording could not be decoded: "
                           + (lines[-1].strip()[:160] if lines else "no audio"))
    return len(r.stdout) / (2 * SR)


def transcribe(path: Path, *, names: list[str] | None = None) -> dict:
    """Return {"text": str, "latency_ms": int, "model": str, "duration_s": float}.

    `latency_ms` is the wall clock of the whole call — process start, model load and the
    recognition — because that is what the editor waits for after releasing V. Raises
    NotAvailable (or its subclass TooLong) on any failure.
    """
    if not available():
        raise NotAvailable("dictation needs ffmpeg, uv and research/tools/dictate.py")
    path = Path(path)
    if not path.is_file():
        raise NotAvailable(f"no such recording: {path}")
    seconds = duration_s(path)
    if seconds > MAX_SECONDS:
        raise TooLong(f"recording is {seconds:.1f} s — notes are at most {MAX_SECONDS:.0f} s")
    clean = [n.strip().replace(",", " ") for n in (names or []) if isinstance(n, str)]
    clean = [n for n in clean if n]

    t0 = time.time()
    try:
        r = subprocess.run(dictate_cmd(path, clean), capture_output=True,
                           check=False, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise NotAvailable(f"dictation timed out after {TIMEOUT_S:.0f} s")
    except OSError as exc:
        raise NotAvailable(f"dictation could not start: {exc}")
    if r.returncode != 0:
        err = r.stderr.decode(errors="replace").strip().splitlines()
        raise NotAvailable("dictation failed: " + (err[-1] if err else f"exit {r.returncode}"))
    try:
        out = json.loads(r.stdout.decode(errors="replace"))
    except ValueError:
        raise NotAvailable("dictation returned something that is not JSON")
    if not isinstance(out, dict) or not isinstance(out.get("text"), str) \
            or not isinstance(out.get("model"), str):
        raise NotAvailable("dictation returned JSON without text and model")
    return {
        "text": out["text"].strip(),
        "latency_ms": int((time.time() - t0) * 1000),
        "model": out["model"],
        "duration_s": round(seconds, 2),
        "device": out.get("device"),
    }
