"""Dictation: the recording-to-text seam behind `POST /api/dictate`.

Everything but the last test runs against a stub in place of the recogniser: the command
builder `dictate.dictate_cmd` is swapped for a script that prints whatever the test wants,
so the plumbing — decode, the 30 s refusal, argument passing, parsing — is exercised without
a GPU or a model download. The one live test runs the real tool on a 2 s sine tone and only
asks that it comes back in time; it is skipped unless ROUGHCUT_LIVE=1.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import dictate  # noqa: E402


def _tone(path: Path, seconds: float) -> Path:
    """A wav of a sine tone: the shape of a recording, without any speech in it."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={seconds}", "-ar", "16000", "-ac", "1",
         str(path)],
        check=True, capture_output=True)
    return path


def _stub(tmp_path: Path, body: str) -> Path:
    """A stand-in for research/tools/dictate.py. Records its argv beside itself so a test
    can see exactly what the module passed, then runs `body`."""
    script = tmp_path / "dictate_stub.py"
    script.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "Path(__file__).with_suffix('.argv.json').write_text(json.dumps(sys.argv[1:]))\n"
        + body, encoding="utf-8")
    return script


def _use(monkeypatch, script: Path) -> None:
    monkeypatch.setattr(
        dictate, "dictate_cmd",
        lambda path, names: [sys.executable, str(script), str(path),
                             *(["--names", ",".join(names)] if names else [])])


def _argv(script: Path) -> list[str]:
    return json.loads(script.with_suffix(".argv.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------- plumbing

def test_available_in_the_test_env():
    assert dictate.available(), "ffmpeg, uv and research/tools/dictate.py are all expected"
    assert dictate.TOOL.name == "dictate.py" and dictate.TOOL.parent.name == "tools"


def test_the_command_runs_the_tool_with_uv_and_passes_names(tmp_path):
    cmd = dictate.dictate_cmd(tmp_path / "note.webm", ["Spenny", "Karl"])
    assert cmd[:3] == ["uv", "run", "--quiet"] and cmd[3] == str(dictate.TOOL)
    assert cmd[4] == str(tmp_path / "note.webm")
    assert cmd[-2:] == ["--names", "Spenny,Karl"]
    assert "--names" not in dictate.dictate_cmd(tmp_path / "note.webm", [])


def test_transcribe_parses_the_tools_json_and_measures_latency(tmp_path, monkeypatch):
    wav = _tone(tmp_path / "note.wav", 1.0)
    script = _stub(tmp_path, "print(json.dumps({'text': ' hold on his face after ', "
                             "'latency_ms': 7, 'model': 'small', 'duration_s': 1.0, "
                             "'device': 'cuda/float16'}))\n")
    _use(monkeypatch, script)
    out = dictate.transcribe(wav, names=["Spenny", " Karl ", "", "a,b"])
    assert out["text"] == "hold on his face after"
    assert out["model"] == "small" and out["device"] == "cuda/float16"
    assert out["duration_s"] == 1.0
    assert isinstance(out["latency_ms"], int) and out["latency_ms"] >= 0
    # names are trimmed, empties dropped, commas (the separator) replaced
    assert _argv(script) == [str(wav), "--names", "Spenny,Karl,a b"]


def test_no_names_means_no_names_flag(tmp_path, monkeypatch):
    wav = _tone(tmp_path / "note.wav", 0.5)
    script = _stub(tmp_path, "print(json.dumps({'text': '', 'model': 'small'}))\n")
    _use(monkeypatch, script)
    assert dictate.transcribe(wav)["text"] == ""
    assert dictate.transcribe(wav, names=None)["text"] == ""
    assert _argv(script) == [str(wav)]


# ------------------------------------------------------------------- refusals

def test_a_recording_over_30_s_is_refused_before_the_tool_runs(tmp_path, monkeypatch):
    long = _tone(tmp_path / "long.wav", dictate.MAX_SECONDS + 1.0)
    called = []
    monkeypatch.setattr(dictate, "dictate_cmd",
                        lambda path, names: called.append(path) or ["false"])
    with pytest.raises(dictate.TooLong) as exc:
        dictate.transcribe(long)
    assert "30" in str(exc.value) and "31." in str(exc.value)
    assert issubclass(dictate.TooLong, dictate.NotAvailable), "the server catches NotAvailable"
    assert called == [], "the model must never be loaded for a note we will refuse"
    # right at the limit is still a note
    ok = _tone(tmp_path / "ok.wav", dictate.MAX_SECONDS)
    assert dictate.duration_s(ok) <= dictate.MAX_SECONDS


def test_malformed_output_raises_not_available(tmp_path, monkeypatch):
    wav = _tone(tmp_path / "note.wav", 0.5)
    _use(monkeypatch, _stub(tmp_path, "print('asr: small on cuda/float16')\n"))
    with pytest.raises(dictate.NotAvailable, match="not JSON"):
        dictate.transcribe(wav)
    _use(monkeypatch, _stub(tmp_path, "print(json.dumps({'latency_ms': 3}))\n"))
    with pytest.raises(dictate.NotAvailable, match="without text"):
        dictate.transcribe(wav)
    _use(monkeypatch, _stub(tmp_path, "print(json.dumps(['not', 'a', 'dict']))\n"))
    with pytest.raises(dictate.NotAvailable):
        dictate.transcribe(wav)


def test_a_failing_tool_reports_its_last_stderr_line(tmp_path, monkeypatch):
    wav = _tone(tmp_path / "note.wav", 0.5)
    _use(monkeypatch, _stub(tmp_path,
                            "print('dictate: cuda unavailable', file=sys.stderr)\n"
                            "print('error: could not load whisper: no model', file=sys.stderr)\n"
                            "sys.exit(4)\n"))
    with pytest.raises(dictate.NotAvailable, match="could not load whisper"):
        dictate.transcribe(wav)


def test_bytes_that_are_not_a_recording_raise_not_available(tmp_path, monkeypatch):
    junk = tmp_path / "junk.webm"
    junk.write_bytes(b"\x00\x01\x02\x03")
    _use(monkeypatch, _stub(tmp_path, "print(json.dumps({'text': 'x', 'model': 'small'}))\n"))
    with pytest.raises(dictate.NotAvailable, match="could not be decoded"):
        dictate.transcribe(junk)
    with pytest.raises(dictate.NotAvailable, match="no such recording"):
        dictate.transcribe(tmp_path / "missing.webm")


def test_unavailable_when_the_tool_file_is_gone(tmp_path, monkeypatch):
    monkeypatch.setattr(dictate, "TOOL", tmp_path / "nowhere" / "dictate.py")
    assert not dictate.available()
    with pytest.raises(dictate.NotAvailable):
        dictate.transcribe(_tone(tmp_path / "note.wav", 0.5))


# ------------------------------------------------------------------- live

@pytest.mark.live
@pytest.mark.skipif(os.environ.get("ROUGHCUT_LIVE") != "1",
                    reason="real faster-whisper; set ROUGHCUT_LIVE=1")
def test_live_tool_returns_within_60_s_on_a_tone(tmp_path):
    """No speech can be synthesised here, so the check is that the real tool loads its
    model, runs on the GPU or CPU, and answers — with any text, possibly none — in time.
    The first run also downloads the model; run it once by hand before trusting the clock."""
    wav = _tone(tmp_path / "tone.wav", 2.0)
    t0 = time.time()
    out = dictate.transcribe(wav, names=["Spenny"])
    wall = time.time() - t0
    print(f"\nlive dictate: {out!r} in {wall:.1f} s")
    assert wall < 60.0
    assert isinstance(out["text"], str) and out["model"] == "small"
    assert out["duration_s"] == 2.0 and out["latency_ms"] > 0
