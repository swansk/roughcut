"""The colour core (INTAKE M10): measure, balance, LUTs, the range chain, HDR
normalise, validation. Synthetic frames and lavfi clips only — the lab that fixed the
numbers ran on Killington and Copper; these tests pin what it found."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import colour  # noqa: E402


def _frame(rgb, h=36, w=64):
    return np.tile(np.array(rgb, np.float32), (h, w, 1))


def _gradient(h=36, w=64):
    x = np.linspace(0, 1, w, dtype=np.float32)[None, :, None]
    y = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    bx = np.broadcast_to(x, (h, w, 1)); by = np.broadcast_to(y, (h, w, 1))
    return np.ascontiguousarray(np.concatenate([bx, by, 1 - bx], -1), np.float32)


# ------------------------------------------------------------------ measure

def test_grey_frame_measures_neutral_with_a_white_reference():
    m = colour.measure(_frame([0.85, 0.85, 0.85]))
    assert "white" in m and m["white_frac"] == 1.0
    assert abs(m["white"]["a"]) < 0.5 and abs(m["white"]["b"]) < 0.5
    assert m["clip"] == 0.0


def test_blue_cast_reads_as_negative_b():
    m = colour.measure(_frame([0.78, 0.80, 0.88]))
    assert "white" in m and m["white"]["b"] < -1.0


def test_no_white_reference_when_the_surface_is_small():
    f = _frame([0.3, 0.3, 0.3])
    f[:4, :, :] = 0.9                       # 11% bright: not outdoor evidence
    m = colour.measure(f)
    assert "white" not in m and m["white_frac"] < colour.WHITE_MIN_FRAC
    assert "grey_rgb" in m


def test_summary_needs_a_white_in_most_samples():
    with_w = colour.measure(_frame([0.85, 0.85, 0.85]))
    without = colour.measure(_frame([0.3, 0.3, 0.3]))
    assert colour.summarise([with_w, with_w, without])["white_source"] == "surface"
    assert colour.summarise([with_w, without, without])["white_source"] == "grey"
    dark = colour.measure(_frame([0.02, 0.02, 0.02]))
    assert colour.summarise([dark, dark])["white_source"] is None
    # A colourful scene with no white surface gives the auto no evidence: Copper's
    # airport bar averaged warm and the grey-world fallback cooled it. Stays as shot.
    warm = colour.measure(_frame([0.55, 0.40, 0.25]))
    assert "grey_rgb" in warm and warm["chroma"] > colour.GREY_MAX_CHROMA
    assert colour.summarise([warm, warm])["white_source"] is None
    assert colour.balance_params(colour.summarise([warm, warm]), "gopro") is None


# ------------------------------------------------------------------ balance

def test_balance_neutralises_the_cast_and_lifts_the_white_within_clamps():
    m = colour.measure(_frame([0.62, 0.64, 0.72]))
    P = colour.balance_params(colour.summarise([m]), "gopro")
    assert P["source"] == "surface"
    assert P["gain"][2] < 1.0 < P["gain"][0]           # blue down, red up
    assert 1.0 < P["exposure"] <= 1.5
    out = colour.apply_balance(np.array([[0.62, 0.64, 0.72]], np.float32), P)
    lab = colour.to_lab(out)[0]
    assert abs(lab[2]) < 1.0                            # cast gone
    assert 0.80 <= float(colour.luma(out)[0]) <= 0.92   # a textured white


def test_balance_is_clamped_per_family():
    # A white reference with a cast stronger than any clamp allows, handed in directly:
    # the detector would (rightly) not call this near-neutral, the clamps are the point.
    s = {"white_source": "surface", "white": {"rgb": [0.45, 0.47, 0.60], "L": 66, "a": 0, "b": -9},
         "y_lo": 0.02, "y_hi": 0.9, "y_mid": 0.5}
    g = colour.balance_params(s, "gopro")
    i = colour.balance_params(s, "iphone")
    assert g["gain"][2] == pytest.approx(0.85, abs=1e-3)
    assert i["gain"][2] == pytest.approx(0.90, abs=1e-3)
    assert g["exposure"] == 1.5 and i["exposure"] == 1.3


def test_balance_never_adds_clipping():
    m = colour.measure(_frame([0.70, 0.70, 0.70]))
    P = colour.balance_params(colour.summarise([m]), "gopro")
    out = colour.apply_balance(_gradient(), P)
    assert float(out.max()) < 1.0                       # the shoulder never reaches 1


def test_no_balance_without_anything_to_trust():
    assert colour.balance_params(colour.summarise([]), "gopro") is None
    dark = colour.measure(_frame([0.02, 0.02, 0.02]))
    assert colour.balance_params(colour.summarise([dark]), "gopro") is None


# ------------------------------------------------------------------ looks and match

def test_look_at_zero_strength_is_balance_only():
    looks = colour.load_looks(None)
    edl = {"colour": {"mode": "auto", "look": "alpine", "strength": 0.0}}
    seg = {"id": "s1", "clip": "X.MP4", "in": 0, "out": 5}
    res = colour.resolve_shot(edl, seg, None, looks)
    x = _gradient().reshape(-1, 3)
    assert np.allclose(res["fn"](x), x)
    assert res["identity"] is True


def test_off_mode_is_the_identity():
    looks = colour.load_looks(None)
    m = colour.measure(_frame([0.62, 0.64, 0.72])); m["t"] = 0.0
    cc = {"probe": {"family": "gopro"}, "samples": [m, {**m, "t": 5.0}],
          "summary": colour.summarise([m, m])}
    res = colour.resolve_shot({"colour": {"mode": "off"}}, {"id": "s", "clip": "X", "in": 0, "out": 9},
                              cc, looks)
    assert res["identity"] and res["balance"] is None


def test_a_trim_re_derives_the_auto_from_the_samples_inside():
    looks = colour.load_looks(None)
    cold = colour.measure(_frame([0.62, 0.64, 0.72])); warm = colour.measure(_frame([0.80, 0.78, 0.70]))
    samples = [{**cold, "t": 0.0}, {**cold, "t": 5.0}, {**warm, "t": 10.0}, {**warm, "t": 15.0}]
    cc = {"probe": {"family": "gopro"}, "samples": samples, "summary": colour.summarise(samples)}
    edl = {"colour": {"mode": "auto"}}
    a = colour.resolve_shot(edl, {"id": "s", "clip": "X", "in": 0, "out": 6}, cc, looks)["balance"]
    b = colour.resolve_shot(edl, {"id": "s", "clip": "X", "in": 9, "out": 16}, cc, looks)["balance"]
    assert a["gain"][2] < 1.0 < b["gain"][2]            # cold shot: blue down; warm: blue up


def test_match_moves_toward_the_reference_with_clamped_spread():
    src = colour.summarise([colour.measure(_frame([0.50, 0.50, 0.60]))])
    ref = colour.summarise([colour.measure(_frame([0.75, 0.75, 0.72]))])
    M = colour.match_params(src, ref, None, None)
    assert M["dL"] > 0 and M["db"] > 0
    assert colour.SPREAD_CLAMP[0] <= M["spread"] <= colour.SPREAD_CLAMP[1]
    out = colour.apply_match(np.array([[0.50, 0.50, 0.60]], np.float32), M)
    assert float(colour.luma(out)[0]) > 0.5


# ------------------------------------------------------------------ LUTs

def test_cube_round_trip_and_grid_order(tmp_path):
    table = colour.cube_table(lambda x: x, n=5)
    assert table[1].tolist() == pytest.approx([0.25, 0, 0])       # red fastest
    assert table[5].tolist() == pytest.approx([0, 0.25, 0])
    p = colour.write_cube(table, tmp_path / "id.cube")
    back = colour.read_cube(p)
    assert np.allclose(back, table)
    x = _gradient().reshape(-1, 3)
    assert np.allclose(colour.apply_cube(x, back), x, atol=1e-6)


def test_ffmpeg_lut3d_matches_numpy(tmp_path):
    """The lab's parity: a baked LUT applied by ffmpeg reproduces the numpy function."""
    looks = colour.load_looks(None)
    P = {"gain": [1.05, 1.0, 0.95], "exposure": 1.2, "knee": 0.8, "lift": 0.01}
    fn = lambda x: colour.apply_look(colour.apply_balance(x, P), looks["alpine"]["params"])
    cube = colour.write_cube(colour.cube_table(fn, 33), tmp_path / "t.cube")
    src = tmp_path / "grad.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "gradients=size=128x64:nb_colors=4:seed=3:duration=0.1", "-frames:v", "1",
                    "-pix_fmt", "rgb24", str(src)], check=True, capture_output=True)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], check=True, capture_output=True).stdout
    x = np.frombuffer(raw, np.uint8).reshape(64, 128, 3).astype(np.float32) / 255
    want = fn(x.reshape(-1, 3))
    got = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-vf",
                          f"format=gbrpf32le,lut3d=file={cube}:interp=tetrahedral,format=rgb24",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    y = np.frombuffer(got, np.uint8).reshape(-1, 3).astype(np.float32) / 255
    mse = float(((y - want) ** 2).mean())
    assert 10 * np.log10(1 / max(mse, 1e-12)) >= 40


def test_grey_self_test_through_the_range_chain(tmp_path):
    """The range trap: a full-range 128 grey must read 126 after the explicit chain."""
    cube = colour.write_cube(colour.cube_table(lambda x: x, 9), tmp_path / "id.cube")
    vf = colour.lut_vf(cube, "full") + ",signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-"
    r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "color=gray:s=64x64:d=0.1,format=yuvj420p", "-vf", vf, "-f", "null", "-"],
                       capture_output=True, text=True)
    yavg = [l for l in r.stdout.splitlines() if "YAVG" in l]
    assert yavg and abs(float(yavg[0].split("=")[1]) - 126) <= 1, (yavg, r.stderr[-300:])


# ------------------------------------------------------------------ probe and HDR

def _hlg_clip(path: Path) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi", "-i",
                    "testsrc2=size=160x90:rate=24:duration=1.5", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p10le",
                    "-color_primaries", "bt2020", "-color_trc", "arib-std-b67",
                    "-colorspace", "bt2020nc", "-color_range", "tv", str(path)],
                   check=True, capture_output=True)


def test_probe_reads_the_tags_and_names_the_normalise(tmp_path):
    hlg = tmp_path / "IMG_0001.MOV"
    _hlg_clip(hlg)
    p = colour.probe(hlg)
    assert p["bits"] == 10 and p["hdr"] == "hlg" and p["range"] == "limited"
    assert p["transfer"] == "arib-std-b67" and p["primaries"] == "bt2020"
    assert colour.normalise_vf(p).startswith("zscale=tin=arib-std-b67:pin=bt2020:min=bt2020nc:rin=limited")
    assert colour.in_range(p) == "limited"
    sdr = tmp_path / "GX.MP4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=160x90:rate=24:duration=1", "-c:v", "libx264",
                    "-pix_fmt", "yuvj420p", str(sdr)], check=True, capture_output=True)
    q = colour.probe(sdr)
    assert q["hdr"] is None and q["range"] == "full" and colour.normalise_vf(q) is None
    assert colour.in_range(q) == "full"


def test_hlg_clip_normalises_to_sdr_and_measures(tmp_path):
    hlg = tmp_path / "IMG_0002.MOV"
    _hlg_clip(hlg)
    p = colour.probe(hlg)
    cc = colour.measure_clip(hlg, every_s=0.5, width=64, clip_probe=p,
                             normalise=colour.normalise_vf(p))
    assert len(cc["samples"]) >= 2
    assert 0.05 < cc["summary"]["y_mid"] < 0.95        # a picture, not black or blown
    # the normalised output really is limited-range yuv420p 8-bit
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(hlg), "-vf",
                        colour.normalise_vf(p) + ",signalstats,metadata=print:key=lavfi.signalstats.YMAX:file=-",
                        "-frames:v", "3", "-f", "null", "-"], capture_output=True, text=True)
    ymax = [float(l.split("=")[1]) for l in r.stdout.splitlines() if "YMAX" in l]
    assert ymax and max(ymax) <= 235, (ymax, r.stderr[-200:])


def test_camera_family_from_tags():
    assert colour.camera_family({"firmware": "HD9.01.01.72.00"}) == "gopro"
    assert colour.camera_family({"com.apple.quicktime.make": "Apple"}) == "iphone"
    assert colour.camera_family({}) == "other"


# ------------------------------------------------------------------ validation

def test_validate_colour_rejects_the_invented_and_clamps_the_hand():
    looks = colour.load_looks(None)
    with pytest.raises(ValueError):
        colour.validate_colour({"mode": "vivid"}, looks)
    with pytest.raises(ValueError):
        colour.validate_colour({"look": "teal-orange-9000"}, looks)
    with pytest.raises(ValueError):
        colour.validate_colour({"strength": 1.5}, looks)
    with pytest.raises(ValueError):
        colour.validate_colour({"shots": {"a": {"match": "sideways"}}}, looks)
    ok = colour.validate_colour({"mode": "auto", "look": "filmic", "strength": 0.4,
                                 "reference": "s1",
                                 "shots": {"s2": {"auto": False, "balance": {"gain": [2, 1, 0.1], "exposure": 9}}}},
                                looks)
    assert ok["look"] == "filmic" and ok["reference"] == "s1"
    b = ok["shots"]["s2"]["balance"]
    assert b["gain"] == [1.15, 1.0, 0.85] and b["exposure"] == 1.5 and b["source"] == "hand"
    assert colour.validate_colour(None, looks) == {}


def test_looks_manifest_adds_a_cube_and_drops_a_broken_entry(tmp_path):
    root = tmp_path / "assets"; (root / "looks").mkdir(parents=True)
    table = colour.cube_table(lambda x: 1 - x, 5)
    colour.write_cube(table, root / "looks" / "neg.cube")
    (root / "looks" / "manifest.json").write_text(json.dumps([
        {"name": "negative", "description": "inverted", "cube": "neg.cube"},
        {"name": "ghost", "description": "no file", "cube": "missing.cube"},
        {"name": "punchy", "description": "formula", "params": {"contrast": 0.4}},
        {"name": "wild", "params": {"contrast": 9}},
    ]), encoding="utf-8")
    looks = colour.load_looks(root)
    assert looks["negative"]["kind"] == "cube" and looks["negative"]["size"] == 5
    assert looks["ghost"]["kind"] == "broken" and looks["wild"]["kind"] == "broken"
    assert looks["punchy"]["params"] == {"contrast": 0.4}
    with pytest.raises(ValueError):
        colour.validate_colour({"look": "ghost"}, looks)
    res = colour.resolve_shot({"colour": {"look": "negative", "strength": 1.0}},
                              {"id": "s", "clip": "X", "in": 0, "out": 1}, None, looks)
    assert np.allclose(res["fn"](np.array([[0.25, 0.5, 0.75]], np.float32)), [[0.75, 0.5, 0.25]], atol=0.02)
