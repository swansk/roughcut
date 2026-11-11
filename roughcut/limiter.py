"""The film's last audio stage: a true-peak limiter (wave 23, lane FILM; COLD-19, FINISH-20).

`assemble.py` levels every shot by measured loudness and had nothing after that, so a film
whose wind and shouting peaked at 0.0 dBFS (372 samples at full scale in the trial's
export; +0.4 dBTP between the samples) left the building that way. This is the stage after
the levelling and the music bed: whatever the last pass writes is limited at -1 dBTP.

True peak, not sample peak: the signal is oversampled to 192 kHz so the limiter sees the
peaks between the samples, then brought back. The film's sound is AAC, and an AAC encode of
a loud, noisy signal overshoots its input by 0.4-0.7 dB (measured on the trial's own mix at
192 and 128 kbit/s), so the limiter works `CODEC_HEADROOM_DB` under the ceiling and the
*delivered file* comes out at or under it. Peaks only: a film that never gets near the
ceiling is not touched, and one that does keeps its level (the mean moves by < 0.2 dB).
"""

from __future__ import annotations

CEILING_DBTP = -1.0           # the delivered file's true peak, at most
CODEC_HEADROOM_DB = 1.0       # what the limiter keeps under the ceiling for the AAC encode
OVERSAMPLE_HZ = 192000
ATTACK_MS = 3.0               # lookahead: the gain is down before the peak arrives
RELEASE_MS = 60.0


def linear(db: float) -> float:
    return 10.0 ** (db / 20.0)


def audio_filter(rate: int = 48000, ceiling_db: float = CEILING_DBTP,
                 headroom_db: float = CODEC_HEADROOM_DB) -> str:
    """The ffmpeg filter chain: oversample, look-ahead limit, back to `rate`."""
    lim = max(0.0625, min(1.0, linear(ceiling_db - headroom_db)))
    return (f"aresample={OVERSAMPLE_HZ},alimiter=limit={lim:.4f}:attack={ATTACK_MS:g}:"
            f"release={RELEASE_MS:g}:level=0:latency=1,aresample={rate}")
