"""Dictation: a spoken note on a moment, as text.

The floor's note is a push-to-talk gesture (hold V), and this is what turns the recording
into the text that rides with the keep. The real implementation lands in the `dictate`
lane (docs/INTAKE.md, M4): ffmpeg to 16 kHz mono, faster-whisper on the GPU when there is
one, the brief's names seeding the prompt so "Spenny" survives. Until then the server
answers 501 and the floor's key line says so.
"""

from __future__ import annotations

from pathlib import Path


class NotAvailable(RuntimeError):
    """Dictation is not implemented or its model is not installed."""


MAX_SECONDS = 30.0


def available() -> bool:
    return False


def transcribe(path: Path, *, names: list[str] | None = None) -> dict:
    """Return {"text": str, "latency_ms": int, "model": str}. Raises NotAvailable."""
    raise NotAvailable("dictation is not built yet — see docs/INTAKE.md M4")
