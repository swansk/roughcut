"""Fixtures for the cut board tests.

The suite builds its own tiny project — three 6-second synthetic clips with
fabricated sidecars — rather than pointing at Copper. Three reasons, all of which
matter more than realism here:

  * speed: the whole suite runs in seconds, so it is cheap enough to actually run
  * determinism: transcripts are fabricated at known timestamps, so snap results
    can be asserted exactly instead of "roughly longer"
  * portability: no dependency on Karl's ~/footage, which the container target and
    any future CI will not have

Run with:
    uv run --with pytest --with fastapi --with uvicorn --with httpx \
        pytest app/tests -q
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CLIP_S = 6.0

# Fabricated so that snap's three passes each have something to bite on:
#   head    a segment starting at 1.0 opens inside "hello there" (0.5-2.0)
#   tail    a segment ending at 3.0 lands inside "how are you"   (2.4-4.0)
#   closure "goodbye" starts 1.0s after that utterance ends, inside the 1.2s gap
TRANSCRIPT = [
    {"start": 0.5, "end": 2.0, "text": "hello there", "no_speech_prob": 0.1,
     "words": [{"w": "hello", "t": 0.5, "p": 0.9}, {"w": "there", "t": 1.2, "p": 0.9}]},
    {"start": 2.4, "end": 4.0, "text": "how are you", "no_speech_prob": 0.1,
     "words": [{"w": "how", "t": 2.4, "p": 0.9}, {"w": "are", "t": 3.0, "p": 0.9},
               {"w": "you", "t": 3.5, "p": 0.9}]},
    {"start": 5.0, "end": 5.6, "text": "goodbye", "no_speech_prob": 0.1,
     "words": [{"w": "goodbye", "t": 5.0, "p": 0.9}]},
]


def _make_clip(path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=24:duration={CLIP_S}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={CLIP_S}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True)


def _sidecar(stem: str) -> dict:
    return {
        "clip": f"{stem}.MP4", "duration_s": CLIP_S, "sample_rate": 16000,
        "frame_hz": 10,
        "tracks": {"speech_band_db": [-30.0] * 60, "flatness": [0.05] * 60,
                   "onset": [0.2] * 60, "vad": [0] * 60},
        "transcript": TRANSCRIPT,
        "events": [],
        "summary": {"speech_fraction": 0.5, "wind_dominant_fraction": 0.0,
                    "integrated_lufs": -18.0, "peak_dbfs": -3.0,
                    "audio_usable": True, "n_words": 6, "analysis_s": 0.1},
        "candidates": [
            {"t": 0.5, "end": 2.0, "why": "speech: hello there", "score": 0.55},
            {"t": 5.0, "end": 5.6, "why": "interest marker: goodbye", "score": 0.8},
        ],
    }


@pytest.fixture(scope="session")
def project(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("proj")
    footage, sidecars, work = root / "footage", root / "sidecars", root / "work"
    for d in (footage, sidecars, work):
        d.mkdir()
    stems = ["CLIP_A", "CLIP_B", "CLIP_C"]
    for stem in stems:
        _make_clip(footage / f"{stem}.MP4")
        (sidecars / f"{stem}.audio.json").write_text(
            json.dumps(_sidecar(stem), indent=1), encoding="utf-8")

    edl = {
        "variant": "T", "title": "test cut", "orient": "none", "story": "",
        "target_s": [5, 20],
        "segments": [
            {"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
            {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"},
        ],
    }
    edl_path = root / "edl.json"
    edl_path.write_text(json.dumps(edl, indent=1), encoding="utf-8")

    # An asset library with one 2s track, so the music panel has something to offer.
    assets = root / "assets"
    (assets / "music").mkdir(parents=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi",
         "-i", "sine=frequency=900:duration=2", str(assets / "music" / "bed.wav")],
        check=True, capture_output=True)
    return {"root": root, "footage": footage, "sidecars": sidecars,
            "work": work, "edl": edl_path, "stems": stems, "assets": assets}


@pytest.fixture
def client(project):
    """TestClient wired through the server's own configure(), proxies built inline."""
    from fastapi.testclient import TestClient
    import server

    # Restore the EDL between tests — several of them save to it on purpose.
    original = project["edl"].read_text(encoding="utf-8")
    # visual sidecars point at an empty dir on purpose: the suite must not pick up
    # whatever real bins happen to sit in ~/work/visual on this machine.
    server.configure(project["edl"], project["footage"], project["sidecars"],
                     project["work"], proxies=False,
                     visual=project["work"] / "no-visual", assets=project["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in project["stems"]])
    with TestClient(server.app) as c:
        yield c
    project["edl"].write_text(original, encoding="utf-8")
