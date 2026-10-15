"""A film with a music bed is encoded at the preset's audio bitrate, not a fixed 192k
(wave 4: the bed's pass is the film's one AAC encode)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import effects  # noqa: E402


class _Done:
    returncode = 0
    stderr = ""


def test_the_bed_pass_encodes_at_the_bitrate_it_is_given(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, *a, **k):
        seen["cmd"] = cmd
        return _Done()

    monkeypatch.setattr(effects, "run", fake_run)
    monkeypatch.setattr(effects, "resolve_asset", lambda asset, root=None: tmp_path / "bed.wav")
    monkeypatch.setattr(effects, "resolve_gain", lambda spec, track: spec)
    monkeypatch.setattr(effects, "probe_duration", lambda p: 10.0, raising=False)
    try:
        effects.add_music(tmp_path / "film.mp4", {"asset": "music/bed.wav"}, tmp_path / "out.mp4",
                          audio_bitrate="384k")
    except Exception:
        pass   # anything after the encode command is not this test's concern
    cmd = seen.get("cmd")
    assert cmd, "add_music did not reach its encode"
    i = cmd.index("-b:a")
    assert cmd[i + 1] == "384k", cmd


def test_assemble_passes_the_presets_bitrate_to_the_bed():
    src = (Path(__file__).resolve().parents[2] / "research" / "tools" / "assemble.py").read_text()
    assert 'audio_bitrate=video["audio_bitrate"]' in src
