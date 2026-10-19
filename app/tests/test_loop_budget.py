"""Wave 14 SERVERPERF (verdict 10, punch 1): the server never stalls.

Verdict 10 counted 145 "event loop was held" lines (up to 996 ms) and slow replies to
2.1 s while a Designed Effects check drew its proof on a server thread; a 110 MB drop
uploaded at ~1.25 MB/s. The budget: during a first pass, an export and an effect check,
`GET /api/debug/slow` reports no request over 300 ms and no loop hold over 200 ms.

These run a real uvicorn loop in-process (the TestClient bypasses the loop watcher) on
a copy of the suite's project, with the model scripted and the index's tools stubbed;
the export (assemble.py), the effect check (`roughcut.fxcheck`, a child) and the
upload's bytes are real.

A hold is the server's when the server's own threads ran through it — the loop thread
(sync work in an async handler) or another thread that kept the GIL — which the
watcher measures per hold (`where`: the loop's frame and each thread's CPU over the
hold). A hold during which nothing in the process ran is the box starving the process
(the suite runs at nice 19 beside other work); it is reported, and counted against
the budget only when the box is not overloaded.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import fx, inference  # noqa: E402

VIGNETTE = {"duration": 2.0, "size": 1.6, "anim": {},
            "shapes": [{"type": "ring", "color": "#000000", "opacity": 0.032, "width": 0.08,
                        "at": [0.0, 0.0], "r": 1.5, "r2": r2}
                       for r2 in (0.3, 0.36, 0.42, 0.48, 0.54, 0.6, 0.66, 0.71)]}



# ------------------------------------------------------------ the pieces

def test_a_sidecar_is_parsed_once_per_version_and_each_reader_gets_its_own(tmp_path):
    import server
    p = tmp_path / "x.audio.json"
    p.write_text(json.dumps({"clip": "X.MP4", "transcript": [{"t": 1, "text": "hi"}]}))
    a = server.read_json_file(p)
    a["transcript"].append("mine")                      # a reader changes its copy
    b = server.read_json_file(p)
    assert b == {"clip": "X.MP4", "transcript": [{"t": 1, "text": "hi"}]} and b is not a
    assert server._JSON_FILES[str(p)][0][1] == p.stat().st_size
    p.write_text(json.dumps({"clip": "X.MP4", "transcript": []}))   # a new version
    assert server.read_json_file(p)["transcript"] == []
    p.write_text("{not json")
    with pytest.raises(ValueError):
        server.read_json_file(p)
    with pytest.raises(OSError):
        server.read_json_file(tmp_path / "gone.json")


