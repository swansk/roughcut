"""The Effects bin's transition kinds, rendered (verdict 8 punch 5; verdict 7 punch 6).

Each kind's maths is tracks.py's, measured on the export of solid-colour clips (CLIP_A
red + 440 Hz, CLIP_B blue + 880 Hz, CLIP_C green and silent) and one ramp clip whose
brightness rises with time (CLIP_R), so a repeated frame is told from a moving one:

  * Dip to Black: K alone on the edit's frame (luma <= 5 %), A at 1 - 2x before it;
    Dip to White: white on the edit;
  * Film Dissolve: the mix in linear light, 255·((g(A) + g(B)) / 2)^(1/2.2) on the edit;
  * a one-sided video fade at the sequence's first frame starts at black, one at its last
    frame ends at black;
  * Constant Gain is -6.02 dB per side on the edit, Exponential Fade -20.8 dB
    (volumedetect), and a one-sided audio fade starts silent;
  * an edit with no handle keeps its length: the incoming shot's first frame is repeated
    before the edit, the log says "insufficient media".
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

import numpy as np
import pytest

import test_transitions as tt
from test_transitions import CUT, EDL, FPS, H, SEGS, W, mid, mean_db, rgb, within

from roughcut import edits, tracks as tracksmod  # noqa: E402


def _ramp(path: Path) -> None:
    # luma 20 + 30·t: each frame brighter than the one before
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i", f"nullsrc=s={W}x{H}:r={FPS}:d={tt.CLIP_S},"
                              "geq=lum='20+30*T':cb=128:cr=128",
         "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={tt.CLIP_S}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "12",
         "-c:a", "aac", "-t", str(tt.CLIP_S), str(path)], check=True, capture_output=True)


@pytest.fixture(scope="module")
def proj(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("fxbin")
    footage = root / "footage"
    footage.mkdir()
    for stem, colour, hz in (("CLIP_A", "red", 440), ("CLIP_B", "blue", 880),
                             ("CLIP_C", "green", None)):
        tt._clip(footage / f"{stem}.MP4", colour, hz)
    _ramp(footage / "CLIP_R.MP4")
    return {"root": root, "footage": footage}


def film(proj, name: str, transitions: list[dict], segs=SEGS):
    return tt.render(proj, {**EDL, "segments": segs, "transitions": transitions}, name)


def tr(kind: str, a="s1", b="s2", dur=1.0, align="center") -> dict:
    return {"a": a, "b": b, "kind": kind, "dur": dur, "align": align}


def y_at(path: Path, t: float) -> float:
    """The mean Y' (limited range, as encoded) of the middle of the frame under second t."""
    n = int(t * FPS + 1e-6)
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
                        "-vf", f"select=eq(n\\,{n})", "-frames:v", "1", "-vsync", "0",
                        "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
                       capture_output=True, check=True)
    y = np.frombuffer(r.stdout[:W * H], np.uint8).astype(np.float64).reshape(H, W)
    return float(y[H // 2 - 8:H // 2 + 8, W // 2 - 8:W // 2 + 8].mean())


def luma(c) -> float:
    return float(0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2])


@pytest.fixture(scope="module")
def plain(proj):
    out, _ = film(proj, "plain", [])
    return {"red": rgb(out, mid(1.0)), "blue": rgb(out, mid(3.0))}


def test_dip_to_black_is_black_on_the_edit(proj, plain):
    out, log = film(proj, "dipb", [tr("dip_to_black")])
    red, blue = plain["red"], plain["blue"]
    assert luma(rgb(out, mid(CUT))) <= 0.05 * 255, rgb(out, mid(CUT))
    # a quarter in (x = .25): A at 1 - 2x = 50 %; three quarters (x = .75): B at 50 %
    assert within(rgb(out, mid(CUT - 0.25)), 0.5 * red + 0.5 * np.array([0, 0, 0]), 4)
    assert within(rgb(out, mid(CUT + 0.25)), 0.5 * blue, 4)
    assert within(rgb(out, mid(1.3)), red, 3) and within(rgb(out, mid(2.7)), blue, 3)
    assert "Dip to Black 1.00s at 2.00s" in log and "insufficient" not in log, log
    assert tt.frames_of(out) == 4 * FPS


def test_dip_to_white_is_white_on_the_edit(proj):
    out, log = film(proj, "dipw", [tr("dip_to_white")])
    got = rgb(out, mid(CUT))
    assert np.all(got >= 240), got
    assert "Dip to White 1.00s" in log, log


def test_film_dissolve_mixes_in_linear_light(proj, plain):
    out, log = film(proj, "film", [tr("film_dissolve")])
    red, blue = plain["red"], plain["blue"]
    g = tracksmod.FILM_GAMMA
    want = 255 * ((0.5 * (red / 255) ** g + 0.5 * (blue / 255) ** g) ** (1 / g))
    got = rgb(out, mid(CUT))
    assert within(got, want, 4), (got, want)
    # brighter than a Cross Dissolve's mean on the channel each shot owns
    assert got[0] > 0.5 * (red[0] + blue[0]) + 30 and got[2] > 0.5 * (red[2] + blue[2]) + 30
    # outside the window untouched (the R'G'B' round trip is only the window's frames)
    assert within(rgb(out, mid(1.3)), red, 3) and within(rgb(out, mid(2.7)), blue, 3)
    assert "Film Dissolve 1.00s" in log, log


def test_a_fade_on_the_first_clip_starts_at_black_and_one_at_the_end_ends_there(proj, plain):
    fades = [tr("cross_dissolve", a=None, b="s1", align="start", dur=0.5),
             tr("dip_to_black", a="s2", b=None, align="end", dur=0.5)]
    out, log = film(proj, "fades", fades)
    assert luma(rgb(out, 0.5 / FPS)) <= 0.05 * 255, rgb(out, 0.5 / FPS)
    # 6 frames in of 12: half the picture
    assert within(rgb(out, mid(6 / FPS)), 0.5 * plain["red"], 4), rgb(out, mid(6 / FPS))
    assert within(rgb(out, mid(0.7)), plain["red"], 3)
    assert luma(rgb(out, mid(4.0 - 1 / FPS))) <= 0.05 * 255
    assert within(rgb(out, mid(3.3)), plain["blue"], 3)
    assert "fades from black" in log and "fades to black" in log, log
    assert tt.frames_of(out) == 4 * FPS


def test_a_film_dissolve_fade_scales_by_the_linear_light_root(proj, plain):
    out, _ = film(proj, "filmfade", [tr("film_dissolve", a=None, b="s1", align="start",
                                        dur=0.5)])
    # Y' - 16 scales with R'G'B' (an affine map): the picture's share at p = 6/12 is
    # p^(1/2.2) = 0.73, not a Cross Dissolve's 0.5 (red's R' clips, so Y' is measured)
    s = 0.5 ** (1 / tracksmod.FILM_GAMMA)
    whole = y_at(out, mid(1.0))
    got = (y_at(out, mid(6 / FPS)) - 16) / (whole - 16)
    assert abs(got - s) < 0.03, (got, s)


SILENT_B = [SEGS[0], {"id": "s3", "clip": "CLIP_C.MP4", "in": 2.0, "out": 4.0}]
SILENT_A = [{"id": "s3", "clip": "CLIP_C.MP4", "in": 1.0, "out": 3.0}, SEGS[1]]


@pytest.mark.parametrize("kind, db", [("constant_gain", 20 * math.log10(0.5)),
                                      ("exponential_fade", 20 * math.log10(9 / 99))])
def test_the_audio_kinds_on_the_edit(proj, kind, db):
    cut, _ = tt.render(proj, {**EDL, "segments": SILENT_B}, "hard-a")
    full_a = mean_db(cut, 0.6, 1.4)
    cut_b, _ = tt.render(proj, {**EDL, "segments": SILENT_A}, "hard-b")
    full_b = mean_db(cut_b, 2.6, 3.4)
    out_a, log = film(proj, f"{kind}-a", [tr(kind, b="s3")], SILENT_B)
    out_b, _ = film(proj, f"{kind}-b", [tr(kind, a="s3")], SILENT_A)
    # each side on the edit, alone with silence: the outgoing A and the incoming B
    got_a = mean_db(out_a, 1.98, 2.02) - full_a
    got_b = mean_db(out_b, 1.98, 2.02) - full_b
    assert abs(got_a - db) < 1.0 and abs(got_b - db) < 1.0, (kind, got_a, got_b, db)
    # a quarter past the edit (x = .75): the outgoing side at its curve
    g_out = tracksmod.audio_gains(kind, 0.75)[0]
    q = mean_db(out_a, 2.23, 2.27) - full_a
    assert abs(q - 20 * math.log10(g_out)) < 1.5, (kind, q, g_out)
    assert mean_db(out_a, 2.6, 3.0) < -60
    assert f"{tracksmod.TRANSITION_KINDS[kind]} 1.00s at 2.00s" in log, log


def test_a_one_sided_audio_fade_starts_silent(proj):
    cut, _ = tt.render(proj, EDL, "hard")
    full = mean_db(cut, 0.6, 1.4)
    out, log = film(proj, "afade", [tr("constant_gain", a=None, b="s1", align="start")])
    assert mean_db(out, 0.0, 0.02) - full < -25
    assert abs(mean_db(out, 0.48, 0.52) - full - 20 * math.log10(0.5)) < 1.0
    assert abs(mean_db(out, 1.2, 1.8) - full) < 0.5
    assert "fade in" in log, log


def test_no_handle_repeats_the_first_frame(proj, plain):
    # the ramp from 0: no media before its in, so B's half is its first frame repeated
    segs = [SEGS[0], {"id": "s2", "clip": "CLIP_R.MP4", "in": 0.0, "out": 2.0}]
    plain_r, _ = film(proj, "ramp-plain", [], segs)
    first = y_at(plain_r, mid(CUT))                   # the ramp's first frame, on the edit
    red = y_at(plain_r, mid(1.0))
    out, log = film(proj, "ramp", [tr("cross_dissolve")], segs)
    assert "insufficient media: 12 repeated frames" in log, log
    for f in (40, 47):                                # inside the first half: B frozen
        x = (f - 36) / 24
        moving = 20 + 30 * (f - 36) / FPS             # what a playing head would show
        got = y_at(out, mid(f / FPS))
        assert abs(got - ((1 - x) * red + x * first)) < 2, (f, got, red, first)
        if f == 47:                                   # a playing head would be 6 levels off
            assert abs(got - ((1 - x) * red + x * moving)) > 4, (f, got)
    assert tt.frames_of(out) == 4 * FPS


def test_the_model_validates_kinds_and_one_sided_ones():
    got = tracksmod.validate_transitions(
        [tr("cross_dissolve"), tr("dip_to_black"),                    # the later wins
         tr("constant_gain", a=None, b="s1", align="start"),
         {"a": "s2", "b": None, "kind": "film_dissolve", "dur": 9}],  # cut to the shot
        SEGS, dur=edits.dur)
    assert [(t["kind"], t["align"]) for t in got] == [
        ("constant_gain", "start"), ("dip_to_black", "center"), ("film_dissolve", "end")]
    assert got[2]["dur"] == 2.0 and got[2]["b"] is None
    # a fade on an edge a centred one holds replaces it, and the reverse
    got = tracksmod.validate_transitions(
        [tr("cross_dissolve"), tr("dip_to_white", a="s1", b=None, align="end")], SEGS)
    assert [t["kind"] for t in got] == ["dip_to_white"]
    for bad in (tr("wipe"), tr("dip_to_black", a=None, align="center"),
                tr("dip_to_black", b=None, align="start")):
        with pytest.raises(ValueError):
            tracksmod.validate_transitions([bad], SEGS)
    # the halves: a dip never repeats frames, a dissolve with no head does
    clip_len = {"CLIP_A.MP4": 6.0, "CLIP_B.MP4": 6.0}.get
    s0 = [SEGS[0], {**SEGS[1], "in": 0.0}]
    assert tracksmod.transition_halves(s0, [tr("dip_to_black")], edits.dur, clip_len)[0][
        "repeat"] == {}
    assert tracksmod.transition_halves(s0, [tr("film_dissolve")], edits.dur, clip_len)[0][
        "repeat"] == {"film_dissolve": {"a": 0.0, "b": 0.5}}
    assert json.dumps(got)
