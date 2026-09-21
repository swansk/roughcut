"""The edits reach the render (INTAKE M13): a part at a speed is retimed, a generated
clip is cut from `--generated-dir` and concatenates like footage, a freeze still is
the frame it names.

`assemble.py` runs the way the board runs it — a subprocess over the suite's three
synthetic clips, with the generated files made by the server's own
`materialise_generated` — and the output is judged with ffprobe and the pixels,
never by reading the command line.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
TOOLS = Path(__file__).resolve().parents[2] / "research" / "tools"
sys.path.insert(0, str(TOOLS))


# ------------------------------------------------------------------ instruments

def _probe(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration:stream=codec_type,codec_name,width,height,pix_fmt,"
                        "r_frame_rate,sample_rate,channels,nb_frames",
                        "-of", "json", str(path)], capture_output=True, text=True)
    return json.loads(r.stdout)


def _duration(path: Path) -> float:
    return float(_probe(path)["format"]["duration"])


def _video(path: Path) -> dict:
    return next(s for s in _probe(path)["streams"] if s["codec_type"] == "video")


def _gray(path: Path, t: float | None = None, size: str = "320x180") -> np.ndarray:
    """One frame as an 8-bit luma array, scaled to `size` so a 1080p part and a
    320×180 source compare like with like."""
    w, h = (int(x) for x in size.split("x"))
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if t is not None:
        cmd += ["-ss", f"{t:.3f}"]
    cmd += ["-i", str(path), "-frames:v", "1", "-vf", f"scale={w}:{h}",
            "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    r = subprocess.run(cmd, capture_output=True)
    assert r.returncode == 0 and len(r.stdout) == w * h, r.stderr[-300:]
    return np.frombuffer(r.stdout, dtype=np.uint8).reshape(h, w).astype(np.float64)


def _mad(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(np.abs(a - b)))


def _yavg(path: Path) -> float:
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-map", "0:v:0", "-vf",
         "signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-",
         "-f", "null", "-"], capture_output=True, text=True)
    vals = [float(l.split("=")[1]) for l in r.stdout.splitlines() if "YAVG" in l]
    assert vals, r.stderr[-300:]
    return float(np.mean(vals))


# ------------------------------------------------------------------ the cut

@pytest.fixture(scope="module")
def cut(project, tmp_path_factory) -> dict:
    import server
    server.configure(project["edl"], project["footage"], project["sidecars"],
                     project["work"], proxies=False,
                     visual=project["work"] / "no-visual", assets=project["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in project["stems"]])
    root = tmp_path_factory.mktemp("editsrender")
    # a generated dir of this module's own: the session's work dir is shared, and
    # every file in the project's generated dir is a clip in its payload
    server.STATE["generated_dir"] = root / "generated"
    black = server.materialise_generated({"kind": "black", "seconds": 2.0, "color": None,
                                          "from_clip": None, "at": None})
    still = server.materialise_generated({"kind": "still", "seconds": 1.0, "color": None,
                                          "from_clip": "CLIP_A.MP4", "at": 2.5})
    segments = [
        {"id": "s1", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "speed": 0.5, "why": "slow"},
        {"id": "s2", "clip": black.name, "in": 0.0, "out": 2.0, "why": "a breath"},
        {"id": "s3", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "speed": 2},
        {"id": "s4", "clip": still.name, "in": 0.0, "out": 1.0},          # no `why`: a split's second half has none
    ]
    edl = {"variant": "E", "title": "edits test", "orient": "none", "target_s": [1, 20],
           "segments": segments}
    edl_path = root / "cut.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    out, parts = root / "cut.mp4", root / "parts"
    cmd = ["uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(edl_path),
           "--footage", str(project["footage"]), "--sidecars", str(project["sidecars"]),
           "--colour-dir", str(server.colour_dir()),
           "--generated-dir", str(server.generated_dir()),
           "--parts-dir", str(parts), "--keep-parts", "-o", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    yield {"film": out, "parts": parts, "log": r.stdout, "segments": segments,
           "black": black, "still": still,
           "proxy_a": server.STATE["proxy_dir"] / "CLIP_A.mp4"}
    server.STATE.pop("generated_dir", None)
    server.STATE.pop("gen_durations", None)


def test_a_half_speed_part_is_twice_as_long(cut):
    slow = cut["parts"] / "part_000.mp4"
    assert abs(_duration(slow) - 4.0) < 0.15, _duration(slow)      # 2 s of clip at 0.5×
    v = _video(slow)
    assert v["r_frame_rate"] == "24000/1001"                       # the rate held; the frames doubled
    assert abs(int(v["nb_frames"]) - 96) <= 3
    fast = cut["parts"] / "part_002.mp4"
    assert abs(_duration(fast) - 1.0) < 0.15, _duration(fast)      # 2 s of clip at 2×
    # the picture really is slower: 3 s into the slow part is 1.5 s of clip past the
    # in-point (2.5 s of source), not 3 s past it (4.0 s of source)
    frame = _gray(slow, 3.0)
    same = _mad(frame, _gray(cut["proxy_a"], 2.5))
    other = _mad(frame, _gray(cut["proxy_a"], 4.0))
    assert same < other, (same, other)
    a = next(s for s in _probe(slow)["streams"] if s["codec_type"] == "audio")
    assert (a["codec_name"], a["sample_rate"], a["channels"]) == ("aac", "48000", 2)
    assert "×0.5" in cut["log"] and "×2" in cut["log"]


def test_a_black_generated_part_cuts_and_concatenates(cut):
    black = cut["parts"] / "part_001.mp4"
    v = _video(black)
    assert (v["width"], v["height"], v["pix_fmt"], v["r_frame_rate"]) == (1920, 1080, "yuv420p", "24000/1001")
    assert abs(_duration(black) - 2.0) < 0.1
    assert _yavg(black) < 20                                       # black, limited range (16)
    assert "generated — as made" in cut["log"]
    # and the film is the four parts end to end: 4 + 2 + 1 + 1
    assert abs(_duration(cut["film"]) - 8.0) < 0.3, _duration(cut["film"])
    assert "planned 8.0s" in cut["log"]
    fv = _video(cut["film"])
    assert (fv["width"], fv["height"], fv["r_frame_rate"]) == (1920, 1080, "24000/1001")


def test_a_freeze_still_is_the_frame_it_names(cut):
    part = cut["parts"] / "part_003.mp4"
    assert abs(_duration(part) - 1.0) < 0.1
    frame = _gray(part, 0.5)
    same = _mad(frame, _gray(cut["proxy_a"], 2.5))
    earlier = _mad(frame, _gray(cut["proxy_a"], 0.5))
    later = _mad(frame, _gray(cut["proxy_a"], 5.0))
    assert same < 12, same
    assert same < earlier and same < later, (same, earlier, later)
    # a still holds: its last frame is its first
    assert _mad(frame, _gray(part, 0.9)) < 2


def test_the_retime_helpers():
    import assemble
    assert assemble.atempo_chain(1) == ""
    assert assemble.atempo_chain(0.5) == "atempo=0.5"
    assert assemble.atempo_chain(0.25) == "atempo=0.5,atempo=0.5"
    assert assemble.atempo_chain(0.1) == "atempo=0.5,atempo=0.5,atempo=0.5,atempo=0.8"   # 0.125 × 0.8
    assert assemble.atempo_chain(4) == "atempo=2,atempo=2"
    assert assemble.atempo_chain(3) == "atempo=2,atempo=1.5"
    assert assemble.audio_filter(-2.0, 0.25) == "volume=-2.00dB,atempo=0.5,atempo=0.5,aresample=48000:first_pts=0"
    assert assemble.audio_filter(0.0) == "volume=0.00dB,aresample=48000:first_pts=0"
    assert assemble.part_seconds(1.0, 3.0, 0.5) == 4.0
    video = {"w": 1920, "h": 1080, "fps": "24000/1001"}
    vf = assemble.video_filter(video, speed=0.5)
    assert vf.index("setpts=(PTS-STARTPTS)/0.5") < vf.index("fps=24000/1001")
    assert "setpts" not in assemble.video_filter(video)
    evs = [{"t": 1.5, "t_part": 0.5}]
    assert assemble.retimed_events(evs, 0.5) == [{"t": 1.5, "t_part": 1.0}]
    assert assemble.retimed_events(evs, 1.0) is evs
    assert assemble.source_of(Path("/f"), Path("/g"), "gen_black_abc.mp4") == Path("/g/gen_black_abc.mp4")
    assert assemble.source_of(Path("/f"), Path("/g"), "CLIP_A.MP4") == Path("/f/CLIP_A.MP4")
    assert assemble.source_of(Path("/f"), None, "gen_black_abc.mp4") == Path("/f/gen_black_abc.mp4")
