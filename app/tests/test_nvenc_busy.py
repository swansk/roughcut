"""A busy box must not turn the Export size sample into an x264 sample.

NVENC refuses a session beyond the box's handful (`OpenEncodeSessionEx failed`); the
probe used to cache that as "no NVENC", the sample was then cut with x264, and the dialog
estimated ~36 % under the real (NVENC) file. The probe now asks again while the refusal
is a busy one, and still answers False at once when there is no NVENC. No ffmpeg runs."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research" / "tools"))
import assemble  # noqa: E402

BUSY = b"[h264_nvenc] OpenEncodeSessionEx failed: incompatible client key (21): (no details)"


def _probe(monkeypatch, replies):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        rc, err = replies[min(len(calls), len(replies)) - 1]
        return subprocess.CompletedProcess(cmd, rc, b"", err)

    monkeypatch.setattr(assemble.subprocess, "run", run)
    monkeypatch.setattr(assemble.time, "sleep", lambda s: None)
    monkeypatch.delenv("ROUGHCUT_GPU", raising=False)
    assemble.nvenc_ok.cache_clear()
    try:
        return assemble.nvenc_ok(), len(calls)
    finally:
        assemble.nvenc_ok.cache_clear()


def test_a_busy_refusal_is_asked_again_and_then_succeeds(monkeypatch):
    assert _probe(monkeypatch, [(187, BUSY), (187, BUSY), (0, b"")]) == (True, 3)


def test_a_box_with_no_nvenc_answers_false_at_once(monkeypatch):
    assert _probe(monkeypatch, [(1, b"Cannot load libnvidia-encode.so.1")]) == (False, 1)


def test_a_box_busy_for_good_answers_false_after_the_waits(monkeypatch):
    ok, n = _probe(monkeypatch, [(187, BUSY)])
    assert (ok, n) == (False, len(assemble.NVENC_PROBE_WAITS))
