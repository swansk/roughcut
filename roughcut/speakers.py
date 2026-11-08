"""Who said what: align diarization turns to the audio sidecar's Whisper words.

The turns come from `research/tools/diarize.py` (pyannote community-1, its own uv
environment, run out of process). This module is the cheap, pure part: it labels every
word and utterance with a speaker (`S1`, `S2`, … in order of first speech), measures
each speaker's talk time and loudness from the sidecar's own speech-band track, and picks
the **wearer** — on a helmet camera the person wearing the mic is the loudest voice in
the speech band, so that is the speaker with the highest level (wind frames masked).

Stdlib only, so the app's environment can import it.

    apply(sidecar, diarization) -> sidecar   # adds utterance.speaker/.wearer, word.s,
                                             # and sidecar["speakers"]
"""
from __future__ import annotations

from statistics import median

# Speakers who talk less than this cannot be trusted to be "the loudest voice": one
# shouted word from a stranger would beat the wearer's sentence.
MIN_TALK_S = 1.0
# ...nor can one who talks under this share of the main talker's time: on Killington CLIP_01
# a 2.7 s burst from a bystander was 3.5 dB louder than the wearer's 23 s monologue.
MIN_SHARE = 0.2
# The wearer's own voice is loud on their mic (Killington: -21 to -28 dBFS in the speech
# band); a best candidate quieter than this is somebody far away, so nobody is the wearer
# (CLIP_10: one 2.9 s voice at -41.7 dB).
WEARER_MIN_DB = -36.0
# A word with no turn under it takes the nearest turn within this many seconds.
NEAR_S = 0.5
# Wearer margin under which the pick is flagged as a close call.
CLOSE_DB = 1.5
# An utterance is "mixed" when another speaker owns at least this share of its words.
MIXED_SHARE = 0.25


def label(n: int) -> str:
    return f"S{n}"


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _word_owner(t: float, e: float, turns: list[dict]) -> str | None:
    """The turn label owning most of [t, e], else the nearest turn within NEAR_S."""
    best, best_ov = None, 0.0
    near, near_d = None, NEAR_S
    for tr in turns:
        if tr["start"] > e + NEAR_S:
            break
        ov = _overlap(t, e, tr["start"], tr["end"])
        if ov > best_ov:
            best, best_ov = tr["speaker"], ov
        elif ov == 0.0:
            d = min(abs(tr["start"] - e), abs(t - tr["end"]))
            if d < near_d:
                near, near_d = tr["speaker"], d
    return best or near


def _level(tracks: dict, hz: float, spans: list[tuple[float, float]]) -> float | None:
    """Median speech-band level (dBFS) over the 10 Hz frames inside `spans`, wind frames
    masked unless nothing else is left."""
    sp = tracks.get("speech_band_db") or []
    wind = tracks.get("wind_dominant") or []
    clean, every = [], []
    for a, b in spans:
        for i in range(max(0, int(a * hz)), min(len(sp), int(b * hz) + 1)):
            every.append(sp[i])
            if not (i < len(wind) and wind[i]):
                clean.append(sp[i])
    pool = clean if len(clean) >= 5 else every
    return round(float(median(pool)), 1) if pool else None


def pick_wearer(speakers: list[dict]) -> str | None:
    """The wearer's id: the loudest of the speakers who talk enough to judge; a lone
    speaker is the wearer (the mic is on them). Sets `wearer` on every row and
    `wearer_margin_db` on the winner."""
    for s in speakers:
        s["wearer"] = False
    if not speakers:
        return None
    top = max(s["talk_s"] for s in speakers)
    pool = [s for s in speakers
            if s["talk_s"] >= max(MIN_TALK_S, MIN_SHARE * top)] or speakers
    ranked = sorted((s for s in pool if s.get("loudness_db") is not None),
                    key=lambda s: -s["loudness_db"])
    if not ranked:
        win = max(pool, key=lambda s: s["talk_s"])
    else:
        win = ranked[0]
        if win["loudness_db"] < WEARER_MIN_DB:
            return None
        win["wearer_margin_db"] = (round(win["loudness_db"] - ranked[1]["loudness_db"], 1)
                                   if len(ranked) > 1 else None)
        win["wearer_close_call"] = bool(len(ranked) > 1
                                        and win["wearer_margin_db"] < CLOSE_DB)
    win["wearer"] = True
    return win["id"]


def apply(sidecar: dict, diar: dict) -> dict:
    """Label the sidecar's words and utterances from a diarization result and add the
    `speakers` summary. Idempotent: it recomputes from the diarization every time."""
    turns = sorted((diar.get("exclusive") or diar.get("turns") or []),
                   key=lambda t: (t["start"], t["end"]))
    transcript = sidecar.get("transcript") or []
    hz = float(sidecar.get("frame_hz") or 10)
    ids: dict[str, str] = {}               # pyannote label -> S1, S2 by first speech

    def sid(raw: str) -> str:
        if raw not in ids:
            ids[raw] = label(len(ids) + 1)
        return ids[raw]

    owners: list[list[str | None]] = []
    for u in transcript:
        row = []
        for w in u.get("words") or []:
            t0 = float(w["t"])
            # a sidecar from before word ends were kept has points, not spans
            raw = _word_owner(t0, max(float(w.get("e", t0)), t0 + 0.05), turns)
            row.append(sid(raw) if raw else None)
        owners.append(row)
    # a word no turn reaches takes its neighbour's speaker (the line it is part of)
    for row in owners:
        for i, v in enumerate(row):
            if v is None:
                left = next((row[j] for j in range(i - 1, -1, -1) if row[j]), None)
                right = next((x for x in row[i + 1:] if x), None)
                row[i] = left or right

    spans: dict[str, list[tuple[float, float]]] = {}
    for u, row in zip(transcript, owners):
        words = u.get("words") or []
        tally: dict[str, float] = {}
        for w, s in zip(words, row):
            if s:
                w["s"] = s
                d = max(0.05, float(w.get("e", w["t"])) - float(w["t"]))
                tally[s] = tally.get(s, 0.0) + d
                spans.setdefault(s, []).append((float(w["t"]), float(w.get("e", w["t"]))))
        if tally:
            top = max(tally, key=tally.get)
            u["speaker"] = top
            total = sum(tally.values())
            if len(tally) > 1 and (total - tally[top]) / total >= MIXED_SHARE:
                u["mixed"] = True
            else:
                u.pop("mixed", None)
        else:
            u.pop("speaker", None)

    # talk time: the diarizer's own turns, but only where the transcript says someone
    # spoke (a turn over wind noise with no words is not talk)
    utter = [(float(u["start"]), float(u["end"])) for u in transcript]
    inv = {v: k for k, v in ids.items()}
    speakers = []
    for s in sorted(ids.values(), key=lambda x: int(x[1:])):
        talk = 0.0
        for tr in turns:
            if ids.get(tr["speaker"]) != s:
                continue
            talk += sum(_overlap(tr["start"], tr["end"], a, b) for a, b in utter)
        speakers.append({"id": s, "talk_s": round(talk, 1),
                         "loudness_db": _level(sidecar.get("tracks") or {}, hz,
                                               spans.get(s, [])),
                         "wearer": False, "label": inv[s]})
    wearer = pick_wearer(speakers)
    for u in transcript:
        if u.get("speaker"):
            u["wearer"] = u["speaker"] == wearer
    sidecar["speakers"] = speakers
    sidecar["diarization"] = {k: diar.get(k) for k in
                              ("model", "device", "gpu_s", "peak_vram_mb")}
    return sidecar


def render_tag(u: dict) -> str:
    """The transcript line's speaker prefix: `wearer: `, `S2: `, or nothing when the
    clip was never diarized."""
    s = u.get("speaker")
    if not s:
        return ""
    return "wearer: " if u.get("wearer") else f"{s}: "


def who(u: dict) -> str | None:
    """`the wearer` / `someone else` for an utterance, or None when unknown."""
    if not u.get("speaker"):
        return None
    return "the wearer" if u.get("wearer") else "someone else"


# ------------------------------------------------------------------ the optional tool

import os
import shutil
from pathlib import Path

NOT_AVAILABLE_RC = 3        # research/tools/diarize.py's exit code for "no token"


def token_path() -> Path:
    env = os.environ.get("ROUGHCUT_HF_TOKEN_FILE")
    return Path(env) if env else Path.home() / ".config" / "roughcut" / "hf-token"


def available() -> tuple[bool, str]:
    """Can diarization run on this machine? Checks only that a token exists (the file or
    $HF_TOKEN) and `uv` is on PATH — the token's value is never read here, never logged.
    `(False, why)` makes the index stage *skip* with that reason; nothing else depends
    on it."""
    if os.environ.get("ROUGHCUT_NO_DIARIZE"):
        return False, "switched off (ROUGHCUT_NO_DIARIZE)"
    if shutil.which("uv") is None:
        return False, "uv is not installed"
    if not (os.environ.get("HF_TOKEN", "").strip()):
        try:
            if token_path().stat().st_size == 0:
                return False, "add a HuggingFace token"
        except OSError:
            return False, "add a HuggingFace token"
    return True, ""
