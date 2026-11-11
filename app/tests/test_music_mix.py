"""The music bed's level and ducking, measured from real exports (wave 25, lane MUSIC).

A steady 440 Hz bed under a film whose own sound is silent, one line spoken at 4.0-6.0 s
(from the clip's sidecar: the speech the index already knows, no new analysis). Three real
exports (assemble.py through `/api/render`, the CPU path) differing in the bed's level and
dip; the bed's loudness is read off the finished files in windows before, during and after
the line. No model is called.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from conftest import _sidecar  # noqa: E402

CLIP_S = 10.0


@pytest.fixture(scope="module", autouse=True)
def cpu_only():
    mp = pytest.MonkeyPatch()
    mp.setenv("ROUGHCUT_GPU", "0")
    yield
    mp.undo()


@pytest.fixture(scope="module")
def proj(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("mix")
    footage, sidecars, work, assets = (root / d for d in ("footage", "sidecars", "work", "assets"))
    for d in (footage, sidecars, work, assets / "music"):
        d.mkdir(parents=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i", f"color=c=blue:size=320x180:rate=24:duration={CLIP_S}",
         "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo,atrim=0:{CLIP_S}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-t", f"{CLIP_S}", str(footage / "CLIP_S.MP4")], check=True, capture_output=True)
    side = _sidecar("CLIP_S")
    side["duration_s"] = CLIP_S
    side["transcript"] = [{"start": 4.0, "end": 6.0, "text": "a spoken line", "no_speech_prob": 0.1,
                           "words": [{"w": "a", "t": 4.0, "p": 0.9}]}]
    (sidecars / "CLIP_S.audio.json").write_text(json.dumps(side), encoding="utf-8")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=14", "-ac", "2", "-af", "volume=-12dB",
                    "-c:a", "pcm_s16le", str(assets / "music" / "steady.wav")], check=True, capture_output=True)
    edl = root / "edl.json"
    return {"root": root, "footage": footage, "sidecars": sidecars, "work": work, "edl": edl, "assets": assets}


SEGS = [{"id": "s1", "clip": "CLIP_S.MP4", "in": 0.0, "out": 10.0, "why": "talk"}]


@pytest.fixture
def api(proj):
    from fastapi.testclient import TestClient
    import server
    server.configure(proj["edl"], proj["footage"], proj["sidecars"], proj["work"],
                     proxies=False, visual=proj["work"] / "no-visual", assets=proj["assets"])
    proj["edl"].write_text(json.dumps({"variant": "T", "title": "mix", "orient": "none", "story": "",
                                       "target_s": [1, 30], "fps": 24.0, "segments": SEGS}), encoding="utf-8")
    with TestClient(server.app) as c:
        yield c


def _lufs(path: Path, start: float, length: float) -> float:
    r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-ss", f"{start}", "-t", f"{length}", "-i", str(path),
                        "-vn", "-af", "ebur128=framelog=quiet", "-f", "null", "-"], capture_output=True, text=True)
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    assert m, r.stderr[-300:]
    return float(m[-1])


def _export(api, music: dict, name: str) -> Path:
    assert api.put("/api/project", json={"segments": SEGS, "music": {"asset": "music/steady.wav", **music}}).status_code == 200
    r = api.post("/api/render", json={"segments": SEGS, "preset": "match", "name": name})
    assert r.status_code == 200, r.text
    job = r.json()["job"]
    for _ in range(600):
        st = api.get(f"/api/render/{job}").json()
        if st["state"] != "running":
            break
        time.sleep(0.3)
    assert st["state"] == "done", st
    return Path(r.json()["path"]), st


def test_the_beds_level_and_its_dip_under_the_known_speech_are_what_the_file_measures(api):
    # windows on the film's clock: 2.0-3.0 the bed alone (fade in over), 4.5-5.5 the line, 7.5-8.5 alone
    a, st_a = _export(api, {"bed_lufs": -24.0, "duck": True, "duck_db": 12.0, "fade_in": 1.0, "fade_out": 1.0}, "a default")
    b, _ = _export(api, {"bed_lufs": -30.0, "duck": True, "duck_db": 12.0, "fade_in": 1.0, "fade_out": 1.0}, "b six lower")
    c, _ = _export(api, {"bed_lufs": -24.0, "duck": False, "fade_in": 1.0, "fade_out": 1.0}, "c no dip")
    a_gap, a_talk = _lufs(a, 2.0, 1.0), _lufs(a, 4.5, 1.0)
    b_gap = _lufs(b, 2.0, 1.0)
    c_gap, c_talk = _lufs(c, 2.0, 1.0), _lufs(c, 4.5, 1.0)
    print(f"MEASURED bed alone: default {a_gap:.1f} LUFS, six lower {b_gap:.1f}; "
          f"under speech: ducked {a_talk:.1f} (dip {a_gap - a_talk:.1f} dB), not ducked {c_talk:.1f} (gap {c_gap:.1f})")
    assert a_gap == pytest.approx(-24.0, abs=1.5)                  # the level asked is the level written
    assert b_gap == pytest.approx(a_gap - 6.0, abs=1.0)            # six dB lower asked, six dB lower written
    assert a_gap - a_talk == pytest.approx(12.0, abs=3.0)          # the dip under the line
    assert c_talk == pytest.approx(c_gap, abs=1.0)                 # with the dip off the bed does not move
    # the finished file says its loudness and true peak, and is under the limiter's ceiling
    assert st_a["lufs"] is not None and st_a["true_peak_dbtp"] <= -1.0 + 0.05
    assert f"{st_a['lufs']:g} LUFS" in st_a["detail"] and "true peak" in st_a["detail"]


def test_an_exports_download_keeps_the_name_it_was_given(api):
    f, st = _export(api, {"bed_lufs": -24.0, "fade_in": 0.5, "fade_out": 0.5}, "my own name")
    got = api.get(f"/media/download/render/{f.name}")
    assert got.status_code == 200
    assert "my%20own%20name.mp4" in got.headers["content-disposition"]
    listed = {x["name"]: x for x in api.get("/api/renders").json()["renders"]}
    assert listed[f.name]["download_name"] == "my own name.mp4"
