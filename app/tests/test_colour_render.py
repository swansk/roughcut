"""The grade reaches the render (INTAKE M10, I10.1 + I10.3's render half).

`assemble.py` is run the way the board runs it — as a subprocess, with
`--colour-dir` pointing at colour files measured from the suite's synthetic clips —
and the output is judged with ffmpeg's own instruments (`signalstats`, the video
md5, ffprobe's tags), never by inspecting the command line.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import colour  # noqa: E402

TOOLS = Path(__file__).resolve().parents[2] / "research" / "tools"


# ------------------------------------------------------------------ instruments

def _stat(path: Path, key: str) -> list[float]:
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-map", "0:v:0", "-vf",
         f"signalstats,metadata=print:key=lavfi.signalstats.{key}:file=-",
         "-f", "null", "-"], capture_output=True, text=True)
    vals = [float(l.split("=")[1]) for l in r.stdout.splitlines() if key in l]
    assert vals, r.stderr[-300:]
    return vals


def _yavg(path: Path) -> float:
    """Mean luma over the whole film, 0–255 in the encoded (limited) scale."""
    return float(np.mean(_stat(path, "YAVG")))


def _video_md5(path: Path) -> str:
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
                        "-map", "0:v", "-f", "md5", "-"], capture_output=True, text=True)
    assert r.stdout.startswith("MD5="), r.stderr[-300:]
    return r.stdout.strip()


def _duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return float(r.stdout.strip().strip(","))


def _tags(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=pix_fmt,color_range,color_space,"
                        "color_transfer,color_primaries", "-of", "json", str(path)],
                       capture_output=True, text=True)
    return json.loads(r.stdout)["streams"][0]


def _render(edl: dict, edl_path: Path, out: Path, project: dict, colour_dir: Path | None,
            parts_dir: Path, assets: Path | None = None) -> str:
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    cmd = ["uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(edl_path),
           "--footage", str(project["footage"]), "--sidecars", str(project["sidecars"]),
           "--parts-dir", str(parts_dir), "--keep-parts", "-o", str(out)]
    if colour_dir is not None:
        cmd += ["--colour-dir", str(colour_dir)]
    if assets is not None:
        cmd += ["--assets", str(assets)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def _edl(segments: list[dict], colour_block: dict | None = None) -> dict:
    edl = {"variant": "G", "title": "grade test", "orient": "none", "target_s": [1, 20],
           "segments": segments}
    if colour_block is not None:
        edl["colour"] = colour_block
    return edl


TWO_SHOTS = [{"id": "s1", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
             {"id": "s2", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}]


@pytest.fixture(scope="module")
def colour_dir(project, tmp_path_factory) -> Path:
    """Colour files for the synthetic clips, the way `server.measure_colour` writes
    them: dense sampling so a 2 s shot has samples inside its range."""
    d = tmp_path_factory.mktemp("colour")
    for stem in project["stems"]:
        p = project["footage"] / f"{stem}.MP4"
        cc = colour.measure_clip(p, every_s=0.5, width=64, clip_probe=colour.probe(p))
        cc["clip"] = f"{stem}.MP4"
        (d / f"{stem}.colour.json").write_text(json.dumps(cc), encoding="utf-8")
    return d


# ------------------------------------------------------------------ tests

def test_auto_with_a_look_bakes_a_cube_and_moves_the_picture(project, colour_dir, tmp_path):
    """`mode: auto` + `look: alpine` at 0.5: a cube per shot, the duration intact, and
    the picture measurably moved from the `off` render.

    Measured per stage, because on `testsrc2` the two stages pull luma opposite
    ways: the grey-fallback balance (x1.03, from a synthetic picture with no white
    surface) moves YAVG -5.4 and alpine at 0.5 moves it +4.3, so the combined luma
    delta is +0.7 while saturation drops 19 points and the stream hashes differ. A
    single YAVG threshold on the combined render would pass or fail on that
    cancellation, not on whether the grade reached the pixels."""
    parts = tmp_path / "parts"
    graded = tmp_path / "graded.mp4"
    log = _render(_edl(TWO_SHOTS, {"mode": "auto", "look": "alpine", "strength": 0.5}),
                  tmp_path / "graded.json", graded, project, colour_dir, parts)
    assert (parts / "part_000.cube").exists() and (parts / "part_001.cube").exists()
    assert "balance grey" in log and "look alpine 0.50" in log, log
    assert _duration(graded) == pytest.approx(4.0, abs=0.15)

    off = tmp_path / "off.mp4"
    _render(_edl(TWO_SHOTS, {"mode": "off"}), tmp_path / "off.json", off, project,
            colour_dir, tmp_path / "parts_off")
    balance = tmp_path / "balance.mp4"
    _render(_edl(TWO_SHOTS, {"mode": "auto"}), tmp_path / "balance.json", balance,
            project, colour_dir, tmp_path / "parts_balance")

    y_off, y_bal, y_graded = _yavg(off), _yavg(balance), _yavg(graded)
    assert abs(y_bal - y_off) > 1, (y_bal, y_off)            # the balance reached the pixels
    assert abs(y_graded - y_bal) > 1, (y_graded, y_bal)      # the look did too
    sat_off = float(np.mean(_stat(off, "SATAVG")))
    sat_graded = float(np.mean(_stat(graded, "SATAVG")))
    assert sat_off - sat_graded > 5, (sat_off, sat_graded)   # alpine desaturates highlights
    assert _video_md5(graded) != _video_md5(off)
    assert _duration(graded) == pytest.approx(_duration(off), abs=0.05)


def test_off_is_the_identity_byte_for_byte(project, colour_dir, tmp_path):
    """`colour: {"mode": "off"}` with colour files reproduces today's render — an EDL
    with no `colour` block and no colour dir — byte for byte (INTAKE I10.1).

    Not "no block with colour files": `colour.resolve_shot` reads a missing block as
    `mode: auto` (decision 5 — the auto is on by default and the off switch beats
    it), and the inspector resolves it the same way, so that render grades. The
    identity is the off switch, and the pre-M10 render is what it must equal."""
    off = tmp_path / "off.mp4"
    log = _render(_edl(TWO_SHOTS, {"mode": "off"}), tmp_path / "off.json", off, project,
                  colour_dir, tmp_path / "parts_off")
    today = tmp_path / "today.mp4"
    _render(_edl(TWO_SHOTS), tmp_path / "today.json", today, project,
            None, tmp_path / "parts_today")
    assert _video_md5(off) == _video_md5(today)
    assert not list((tmp_path / "parts_off").glob("*.cube"))
    assert not list((tmp_path / "parts_today").glob("*.cube"))
    assert "colour: as shot" in log, log
    # and the stream is tagged as what it always was: limited-range bt709
    t = _tags(today)
    assert t["color_range"] == "tv" and t["color_transfer"] == "bt709"
    assert t["color_space"] == "bt709" and t["color_primaries"] == "bt709"

    # No block *with* colour files is the auto, the same as saying so.
    auto = tmp_path / "auto.mp4"
    _render(_edl(TWO_SHOTS, {"mode": "auto"}), tmp_path / "auto.json", auto, project,
            colour_dir, tmp_path / "parts_auto")
    plain = tmp_path / "plain.mp4"
    _render(_edl(TWO_SHOTS), tmp_path / "plain.json", plain, project,
            colour_dir, tmp_path / "parts_plain")
    assert _video_md5(plain) == _video_md5(auto) != _video_md5(off)
    assert (tmp_path / "parts_plain" / "part_000.cube").exists()


def _hlg_clip(path: Path) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin",
                    "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p10le",
                    "-color_primaries", "bt2020", "-color_trc", "arib-std-b67",
                    "-colorspace", "bt2020nc", "-color_range", "tv",
                    "-c:a", "aac", "-shortest", str(path)],
                   check=True, capture_output=True)


def test_an_hlg_shot_is_normalised_even_with_colour_off(project, tmp_path):
    """Normalise always, grade optionally: an iPhone HLG clip under `mode: off` still
    reaches the concat as SDR 709 limited — tagged bt709/tv and no luma above 235."""
    hlg = project["footage"] / "IMG_HLG.MOV"
    _hlg_clip(hlg)
    try:
        segs = [{"id": "h1", "clip": "IMG_HLG.MOV", "in": 0.0, "out": 2.0, "why": "hlg"},
                {"id": "s1", "clip": "CLIP_A.MP4", "in": 0.0, "out": 1.0, "why": "sdr"}]
        out = tmp_path / "hlg.mp4"
        log = _render(_edl(segs, {"mode": "off"}), tmp_path / "hlg.json", out, project,
                      None, tmp_path / "parts")
        assert "normalised hlg" in log, log
        assert not (tmp_path / "parts" / "part_000.cube").exists()
        t = _tags(out)
        assert t["color_transfer"] == "bt709" and t["color_range"] == "tv"
        assert t["pix_fmt"] == "yuv420p"
        assert max(_stat(out, "YMAX")) <= 235
        assert _duration(out) == pytest.approx(3.0, abs=0.15)
    finally:
        hlg.unlink(missing_ok=True)


def test_a_per_shot_look_override_bakes_only_that_shot(project, tmp_path):
    """Film look null, `shots.s2.look = filmic`, no colour dir at all: shot 1 is the
    identity (no cube), shot 2 gets its look without any balance."""
    parts = tmp_path / "parts"
    out = tmp_path / "override.mp4"
    log = _render(_edl(TWO_SHOTS, {"mode": "auto", "look": None,
                                   "shots": {"s2": {"look": "filmic"}}}),
                  tmp_path / "override.json", out, project, None, parts)
    assert not (parts / "part_000.cube").exists()
    assert (parts / "part_001.cube").exists()
    assert "colour: as shot" in log and "look filmic 0.50" in log, log
    assert _duration(out) == pytest.approx(4.0, abs=0.15)


def test_a_cube_look_from_the_manifest_inverts_the_picture(project, tmp_path):
    """A `.cube` dropped into `assets/looks/manifest.json` is a look like any other:
    `negative` at strength 1.0 under `mode: off` bakes the inverse table, and the
    rendered luma mirrors the plain render around mid-grey (16 + 235 = 251)."""
    assets = tmp_path / "assets"
    (assets / "looks").mkdir(parents=True)
    colour.write_cube(colour.cube_table(lambda x: 1 - x, 9), assets / "looks" / "neg.cube")
    (assets / "looks" / "manifest.json").write_text(json.dumps(
        [{"name": "negative", "description": "inverted", "cube": "neg.cube"}]),
        encoding="utf-8")
    one = [{"id": "s1", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "one"}]

    parts = tmp_path / "parts"
    neg = tmp_path / "neg.mp4"
    _render(_edl(one, {"mode": "off", "look": "negative", "strength": 1.0}),
            tmp_path / "neg.json", neg, project, None, parts, assets=assets)
    table = colour.read_cube(parts / "part_000.cube")
    assert len(table) == 33 ** 3
    assert np.allclose(table, 1 - colour.bake_grid(33), atol=0.02)

    plain = tmp_path / "plain.mp4"
    _render(_edl(one, {"mode": "off"}), tmp_path / "plain.json", plain, project, None,
            tmp_path / "parts_plain", assets=assets)
    assert abs(_yavg(neg) + _yavg(plain) - 251) <= 6, (_yavg(neg), _yavg(plain))
