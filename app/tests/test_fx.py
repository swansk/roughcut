"""INTAKE M12 — the effect renderer, the synth, the part graph, the model calls and the
checks (lane fx-core), on synthetic material.

The sprite is judged by reading the PNGs back (where the alpha mass sits, how wide it
is, that a flash tints the corners); the synth by reading the WAV back (its dominant
frequency, its decay, the -1 dBFS ceiling); the part graph by rendering a part of the
suite's synthetic clip and measuring it with the same ffmpeg pipes `verify` uses. The
model is a scripted backend, the way `test_find.py` does it: what is under test is
the prompt's evidence and how the answer becomes a validated effect.
"""

from __future__ import annotations

import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import config, fx, inference  # noqa: E402

SEG = {"id": "s1", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"}
SEGS = [SEG, {"id": "s2", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}]
CLIPS = {"CLIP_A.MP4": {"clip": "CLIP_A.MP4", "duration": 6.0},
         "CLIP_B.MP4": {"clip": "CLIP_B.MP4", "duration": 6.0}}

X_LINES = [{"type": "line", "from": [-1, -1], "to": [-0.3, -0.3], "width": 0.1},
           {"type": "line", "from": [1, -1], "to": [0.3, -0.3], "width": 0.1},
           {"type": "line", "from": [-1, 1], "to": [-0.3, 0.3], "width": 0.1},
           {"type": "line", "from": [1, 1], "to": [0.3, 0.3], "width": 0.1}]

HIT = {
    "shot": "s1", "name": "hit markers", "why": "two rock strikes",
    "events": [{"t": 1.5, "x": 0.5, "y": 0.7}, {"t": 2.4, "x": 0.55, "y": 0.72}],
    "overlay": {"duration": 0.35, "size": 0.12, "shapes": X_LINES,
                "anim": {"scale": [[0, 1.4], [0.06, 1.0]], "opacity": [[0, 1], [0.23, 1], [0.35, 0]]},
                "flash": {"color": "#ff0000", "opacity": 0.15, "duration": 0.08}},
    "sound": {"duration": 0.18, "gain_db": -6,
              "layers": [{"type": "tone", "freq": 1800, "wave": "square", "decay": 0.06},
                         {"type": "noise", "color": "white", "hp": 1500, "decay": 0.09}]},
}

# the suite's onset track is a flat 0.2 bed; two spikes make two impacts
ONSET = [0.2] * 60
ONSET[15] = 0.9        # 1.5 s
ONSET[24] = 0.7        # 2.4 s


def _effect(**over) -> dict:
    raw = json.loads(json.dumps(HIT))
    raw.update(over)
    return fx.validate_effect(raw, SEGS, CLIPS)


# ------------------------------------------------------------ validation edges

@pytest.mark.parametrize("bad, message", [
    ({"overlay": {"shapes": [{"type": "ring", "r": 0.5, "r2": 0.9}]}}, "ring.r2"),
    ({"overlay": {"shapes": [{"type": "polygon", "points": [[0, 0], [1, 1]]}]}}, "polygon.points"),
    ({"overlay": {"shapes": [{"type": "text", "text": "x" * 81}]}}, "text.text"),
    ({"overlay": {"shapes": [{"type": "text", "text": "a\nb\nc\nd\ne"}]}}, "text.text needs"),
    ({"overlay": {"shapes": [{"type": "text", "text": "hi", "reveal": "spin"}]}}, "text.reveal"),
    ({"overlay": {"shapes": [{"type": "rect", "start": 2.0, "end": 1.0}]}}, "shape.end"),
    ({"sound": {"layers": [{"type": "click"}], "repeat": {"every": 5}}}, "repeat.every"),
    ({"overlay": {"shapes": X_LINES, "anim": {"spin": [[0, 1]]}}}, "unknown anim track"),
    ({"overlay": {"shapes": X_LINES, "duration": 0.2, "anim": {"scale": [[0.5, 1]]}}}, "anim.scale.t"),
    ({"overlay": {"shapes": [{"type": "line", "from": [-2, 0], "to": [1, 0]}]}}, "line.from"),
    ({"events": [{"t": 7.0}]}, "event.t"),
    ({"shot": "nope"}, "not in the cut"),
    ({"clip": "CLIP_B.MP4"}, "not the shot's clip"),
    ({"sound": {"layers": [{"type": "tone", "wave": "pulse"}]}}, "unknown wave"),
    ({"sound": {"layers": [{"type": "noise", "color": "brown"}]}}, "unknown noise colour"),
    ({"sound": {"layers": [{"type": "tone", "hp": 5}]}}, "layer.hp"),
])
def test_validation_refuses_with_a_sentence(bad, message):
    with pytest.raises(ValueError, match=message):
        _effect(**bad)


def test_validation_fills_defaults_and_sorts_events():
    e = _effect(events=[{"t": 2.4}, {"t": 1.5, "x": 0.1, "y": 0.9, "label": "  first  "}])
    assert [ev["t"] for ev in e["events"]] == [1.5, 2.4]
    assert e["events"][0]["label"] == "first" and e["events"][1] == {"t": 2.4, "x": 0.5, "y": 0.5}
    assert e["overlay"]["shapes"][0]["color"] == "#ffffff"
    assert e["sound"]["layers"][0]["attack"] == 0.002
    assert e["status"] == "proposed" and e["id"].startswith("fx_")


# ------------------------------------------------------------ onset peaks

def test_onset_peaks_find_the_two_impacts_and_nearest_snaps():
    peaks = fx.onset_peaks(ONSET, 10, 1.0, 3.0)
    assert [p["t"] for p in peaks] == [1.5, 2.4]
    assert peaks[0]["strength"] == 0.9
    assert fx.onset_peaks(ONSET, 10, 3.5, 5.5) == []          # the flat bed alone is no peak
    assert fx.nearest_onset(ONSET, 10, 1.62) == 1.5
    assert fx.nearest_onset(ONSET, 10, 2.33) == 2.4
    e = _effect()
    assert [ev["t_part"] for ev in fx.events_in_shot(e, SEG)] == [0.5, 1.4]
    assert fx.events_in_shot(e, {"in": 2.0, "out": 3.0}) [0]["t"] == 2.4


# ------------------------------------------------------------ the rasteriser

def _alpha(p: Path) -> np.ndarray:
    from PIL import Image
    return np.asarray(Image.open(p).convert("RGBA"))[:, :, 3]


def _mass(alpha: np.ndarray) -> tuple[float, float, int, int]:
    """(cx, cy, width, height) of the opaque pixels."""
    ys, xs = np.nonzero(alpha > 128)
    return float(xs.mean()), float(ys.mean()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)


def test_sprite_sits_on_the_anchor_at_the_box_size(tmp_path):
    overlay = fx.validate_overlay({"duration": 0.1, "size": 0.1,
                                   "shapes": [{"type": "circle", "at": [0, 0], "r": 1, "fill": True}]})
    frames = fx.render_overlay_frames(overlay, 640, 360, 24, tmp_path / "a", anchor=(0.25, 0.5))
    assert len(frames) == 3 and frames[0].name == "f_0000.png"
    a = _alpha(frames[0])
    assert a.shape == (360, 640)
    cx, cy, wd, ht = _mass(a)
    assert abs(cx - 160) < 1.5 and abs(cy - 180) < 1.5        # 0.25 × 640, 0.5 × 360
    assert abs(wd - 64) <= 3 and abs(ht - 64) <= 3             # r = 1 unit = the box, 0.1 × 640
    assert a[0, 0] == 0 and a[359, 639] == 0                   # transparent elsewhere
    # the same effect on a wider frame is the same fraction of it
    big = fx.render_overlay_frames(overlay, 1280, 720, 24, tmp_path / "b", anchor=(0.25, 0.5))
    bx, by, bw, bh = _mass(_alpha(big[0]))
    assert abs(bx - 320) < 2 and abs(by - 360) < 2 and abs(bw - 128) <= 4


def test_pose_offsets_scales_rotates_and_fades(tmp_path):
    line = [{"type": "line", "from": [-1, 0], "to": [1, 0], "width": 0.05}]
    # dx moves the sprite by a fraction of the frame width
    moved = fx.validate_overlay({"duration": 0.05, "size": 0.1, "shapes": line, "anim": {"dx": [[0, 0.1]]}})
    cx, _, wd, ht = _mass(_alpha(fx.render_overlay_frames(moved, 640, 360, 24, tmp_path / "dx")[0]))
    assert abs(cx - (320 + 64)) < 2 and wd > ht * 3
    # rotate 90° turns the line vertical (clockwise positive, like a canvas)
    turned = fx.validate_overlay({"duration": 0.05, "size": 0.1, "shapes": line, "anim": {"rotate": [[0, 90]]}})
    _, _, wd, ht = _mass(_alpha(fx.render_overlay_frames(turned, 640, 360, 24, tmp_path / "rot")[0]))
    assert ht > wd * 3
    # scale 2 doubles the extent
    scaled = fx.validate_overlay({"duration": 0.05, "size": 0.1, "shapes": line, "anim": {"scale": [[0, 2.0]]}})
    _, _, wd2, _ = _mass(_alpha(fx.render_overlay_frames(scaled, 640, 360, 24, tmp_path / "sc")[0]))
    _, _, wd1, _ = _mass(_alpha(fx.render_overlay_frames(turned, 640, 360, 24, tmp_path / "one")[0]))
    assert abs(wd2 - 2 * ht) <= 6
    # opacity fades the alpha, keyframed
    fade = fx.validate_overlay({"duration": 0.1, "size": 0.1, "shapes": line,
                                "anim": {"opacity": [[0, 1], [0.1, 0]]}})
    frames = fx.render_overlay_frames(fade, 640, 360, 24, tmp_path / "fade")
    first, last = _alpha(frames[0]).max(), _alpha(frames[-1]).max()
    assert first >= 250 and 20 < last < 80                       # t = 2/24 → opacity ≈ 0.17


def test_flash_tints_the_whole_frame_for_its_duration(tmp_path):
    overlay = fx.validate_overlay({"duration": 0.2, "size": 0.1, "shapes": X_LINES,
                                   "flash": {"color": "#ff0000", "opacity": 0.2, "duration": 0.05}})
    frames = fx.render_overlay_frames(overlay, 320, 180, 24, tmp_path / "fl")
    assert len(frames) == 5
    from PIL import Image
    corner0 = Image.open(frames[0]).convert("RGBA").getpixel((2, 2))
    corner2 = Image.open(frames[2]).convert("RGBA").getpixel((2, 2))
    assert corner0 == (255, 0, 0, 51)                            # 0.2 × 255, frames at 0 and 1/24
    assert corner2[3] == 0                                       # 2/24 s is past 50 ms


def test_text_and_shapes_draw_and_the_mov_carries_alpha(tmp_path):
    overlay = fx.validate_overlay({
        "duration": 0.35, "size": 0.2,
        "shapes": [{"type": "text", "text": "HIT", "at": [0, -1.2], "h": 0.5, "color": "#ffdd00"},
                   {"type": "ring", "at": [0, 0], "r": 1, "r2": 0.8, "color": "#ff0000"},
                   {"type": "rect", "at": [0, 0], "w": 0.4, "h": 0.4, "rotate": 45, "fill": True},
                   {"type": "polygon", "points": [[-1, 1], [1, 1], [0, 1.3]], "fill": True, "opacity": 0.5}]})
    frame = fx.render_overlay_frames(overlay, 640, 360, 24, tmp_path / "t")[0]
    a = _alpha(frame)
    assert a[180, 320] > 200                                     # the filled rect at the centre
    assert a[180, 320 - 64] > 200 and a[180, 320 - 50] < 20      # the ring: r = 64 px, its hole
    assert a[180 - int(1.2 * 64), 320] > 100                     # the text above
    mov = fx.render_overlay_mov(overlay, 640, 360, 24, tmp_path / "s.mov", anchor=(0.9, 0.9))
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=codec_name,pix_fmt,nb_frames,width,height", "-of", "json", str(mov)],
                       capture_output=True, text=True)
    s = json.loads(r.stdout)["streams"][0]
    assert (s["codec_name"], s["pix_fmt"], s["width"], s["height"]) == ("png", "rgba", 640, 360)
    assert int(s["nb_frames"]) == 9                              # ceil(0.35 × 24)
    assert not list(tmp_path.glob("s_frames_*"))                 # the frames were cleaned up


# ------------------------------------------------------------ the synth

def _read_wav(p: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(p)) as w:
        assert (w.getnchannels(), w.getsampwidth()) == (2, 2)
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2)
        return pcm.astype(np.float64) / 32768.0, w.getframerate()


def _dominant_hz(x: np.ndarray, sr: int) -> float:
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.fft.rfftfreq(len(x), 1 / sr)[int(np.argmax(spec))])


def test_synth_tone_is_at_its_frequency_stereo_16_bit(tmp_path):
    sound = fx.validate_sound({"duration": 0.5, "gain_db": -6,
                               "layers": [{"type": "tone", "freq": 1800, "wave": "sine", "attack": 0, "decay": 3.0, "gain": 1}]})
    x, sr = _read_wav(fx.synth_sound(sound, tmp_path / "t.wav"))
    assert sr == 48000 and len(x) == 24000
    assert np.array_equal(x[:, 0], x[:, 1])                      # the same signal both sides
    assert abs(_dominant_hz(x[:, 0], sr) - 1800) < 5
    peak_db = 20 * np.log10(np.abs(x).max())
    assert -7 < peak_db < -5                                     # gain 1 × -6 dB, no limiting


def test_synth_envelope_decays_to_minus_60_db_and_the_click_is_a_transient(tmp_path):
    sound = fx.validate_sound({"duration": 0.3, "gain_db": 0,
                               "layers": [{"type": "tone", "freq": 1000, "attack": 0, "decay": 0.1, "gain": 1}]})
    x, sr = _read_wav(fx.synth_sound(sound, tmp_path / "d.wav"))
    head = np.sqrt(np.mean(x[:240, 0] ** 2))                     # the first 5 ms
    at_decay = np.sqrt(np.mean(x[4800:5040, 0] ** 2))            # 5 ms at t = 0.1
    assert 20 * np.log10(at_decay / head) < -50                  # -60 dB nominal, some window slop
    click = fx.validate_sound({"duration": 0.1, "gain_db": 0,
                               "layers": [{"type": "click", "decay": 0.02, "gain": 1}]})
    c, _ = _read_wav(fx.synth_sound(click, tmp_path / "c.wav"))
    assert c[0, 0] > 0.8 and np.abs(c[2400:, 0]).max() < 0.01   # loud at once, gone by 50 ms


def test_synth_filters_sweep_noise_and_the_ceiling(tmp_path):
    hp = fx.validate_sound({"duration": 0.5, "gain_db": 0,
                            "layers": [{"type": "noise", "color": "white", "hp": 4000, "attack": 0, "decay": 3.0, "gain": 1}]})
    x, sr = _read_wav(fx.synth_sound(hp, tmp_path / "hp.wav"))
    spec = np.abs(np.fft.rfft(x[:, 0])) ** 2
    f = np.fft.rfftfreq(len(x), 1 / sr)
    assert spec[f > 8000].mean() > 4 * spec[f < 1000].mean()     # the one-pole hp took the lows
    lp = fx.validate_sound({"duration": 0.5, "gain_db": 0,
                            "layers": [{"type": "noise", "color": "white", "lp": 500, "attack": 0, "decay": 3.0, "gain": 1}]})
    y, _ = _read_wav(fx.synth_sound(lp, tmp_path / "lp.wav"))
    spec = np.abs(np.fft.rfft(y[:, 0])) ** 2
    assert spec[f < 300].mean() > 4 * spec[f > 8000].mean()
    pink = fx.validate_sound({"duration": 0.5, "gain_db": 0,
                              "layers": [{"type": "noise", "color": "pink", "attack": 0, "decay": 3.0, "gain": 1}]})
    p, _ = _read_wav(fx.synth_sound(pink, tmp_path / "pk.wav"))
    spec = np.abs(np.fft.rfft(p[:, 0])) ** 2
    assert spec[(f > 100) & (f < 400)].mean() > 3 * spec[(f > 4000) & (f < 16000)].mean()
    sweep = fx.validate_sound({"duration": 0.4, "gain_db": 0,
                               "layers": [{"type": "sweep", "freq": 4000, "freq_end": 250, "attack": 0, "decay": 3.0, "gain": 1}]})
    s, _ = _read_wav(fx.synth_sound(sweep, tmp_path / "sw.wav"))
    assert _dominant_hz(s[:2400, 0], sr) > 2500 and _dominant_hz(s[-2400:, 0], sr) < 600
    loud = fx.validate_sound({"duration": 0.2, "gain_db": 6,
                              "layers": [{"type": "tone", "wave": "square", "freq": 500, "attack": 0, "decay": 3.0, "gain": 1}]})
    z, _ = _read_wav(fx.synth_sound(loud, tmp_path / "loud.wav"))
    peak_db = 20 * np.log10(np.abs(z).max())
    assert -1.05 < peak_db <= -0.99                              # limited to -1 dBFS, not clipped


# ------------------------------------------------------------ the part graph

def test_part_graph_strings_one_overlay_per_event_and_one_amix(tmp_path):
    e = _effect()
    evs = fx.events_in_shot(e, SEG)
    extra, graph, vout, aout = fx.part_graph([(e, evs)], 320, 180, 24, tmp_path)
    assert (vout, aout) == ("vout", "aout")
    assert extra[::2] == ["-i"] * 3 and len(extra) == 6           # two sprites + one wav
    assert Path(extra[1]).suffix == ".mov" and Path(extra[5]).suffix == ".wav"
    assert graph.count("overlay=0:0") == 2 and graph.count("amix=") == 1
    assert "setpts=PTS-STARTPTS+0.5000/TB" in graph and "between(t,1.4000,1.7500)" in graph
    assert "adelay=24000S|24000S" in graph and "adelay=67200S|67200S" in graph
    assert "amix=inputs=3:normalize=0:duration=first[aout]" in graph
    assert graph.startswith("[1:v]") and "[vbase]" in graph and "[abase]" in graph
    # silent effect: the audio passes straight through
    silent = _effect(sound=None)
    extra2, graph2, vout2, aout2 = fx.part_graph([(silent, evs)], 320, 180, 24, tmp_path / "s")
    assert (vout2, aout2) == ("vout", "abase") and "amix" not in graph2 and len(extra2) == 4
    # nothing in the shot: nothing to do
    assert fx.part_graph([(e, [])], 320, 180, 24, tmp_path / "n") == ([], "", "vbase", "abase")
    assert fx.part_graph([], 320, 180, 24, tmp_path / "n") == ([], "", "vbase", "abase")


# ------------------------------------------------------------ the checks on a proof

def _render_part(src: Path, seg: dict, effect: dict | None, dest: Path, w=320, h=180, fps=24) -> Path:
    """The proof render the server does, in miniature: base or part."""
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
          f"fps={fps},format=yuv420p")
    af = "aresample=48000:first_pts=0"
    dur = seg["out"] - seg["in"]
    head = ["ffmpeg", "-v", "error", "-y", "-nostdin", "-ss", f"{seg['in']:.3f}", "-i", str(src)]
    tail = ["-dn", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "128k",
            "-ac", "2", str(dest)]
    if effect is None:
        cmd = head + ["-t", f"{dur:.3f}", "-vf", vf, "-af", af, "-map", "0:v:0", "-map", "0:a:0"] + tail
    else:
        evs = fx.events_in_shot(effect, seg)
        extra, graph, vout, aout = fx.part_graph([(effect, evs)], w, h, fps, dest.parent / "wd")
        fc = f"[0:v]{vf}[vbase];[0:a]{af}[abase];{graph}"
        cmd = head + extra + ["-t", f"{dur:.3f}", "-filter_complex", fc, "-map", f"[{vout}]", "-map", f"[{aout}]"] + tail
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-500:]
    return dest


@pytest.fixture(scope="module")
def parts(project, tmp_path_factory) -> dict:
    d = tmp_path_factory.mktemp("parts")
    src = project["footage"] / "CLIP_A.MP4"
    e = _effect()
    return {"effect": e, "base": _render_part(src, SEG, None, d / "base.mp4"),
            "part": _render_part(src, SEG, e, d / "part.mp4")}


def test_part_renders_with_the_sound_and_the_marker_measured(parts):
    part, base = parts["part"], parts["base"]
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,nb_frames",
                        "-of", "json", str(part)], capture_output=True, text=True)
    info = json.loads(r.stdout)
    assert abs(float(info["format"]["duration"]) - 2.0) < 0.1
    assert int(next(s for s in info["streams"] if s["codec_type"] == "video")["nb_frames"]) == 48
    # the sound: a transient at each event in the part, none in the base
    for t_part in (0.5, 1.4):
        m = fx.audio_transient_at(part, t_part)
        b = fx.audio_transient_at(base, t_part)
        assert m["rise_db"] >= 6.0, m
        assert m["peak_db"] - b["peak_db"] >= 6.0, (m, b)
        assert abs(b["rise_db"]) < 2.0, b
    assert abs(fx.audio_transient_at(part, 1.0)["rise_db"]) < 2.0     # between the events: nothing
    # the picture: a change at the anchor while the marker shows, none before it
    m = fx.frame_change_at(part, base, 0.5 + 0.1)
    assert m["changed"] >= 0.0002 and m["bbox"] is not None
    x0, y0, x1, y1 = m["bbox"]
    assert x0 <= 0.5 <= x1 and y0 <= 0.7 <= y1
    # before the event only x264's own noise between two encodes remains (measured
    # ~0.3 % on testsrc2's random blocks at 320x180); the marker is well above it
    before = fx.frame_change_at(part, base, 0.25)
    assert before["changed"] < 0.01 and m["changed"] > 2 * before["changed"], (m, before)
    # the flash: the first frame of the event differs across the whole frame
    flash = fx.frame_change_at(part, base, 0.5)
    assert flash["changed"] > 0.5


def test_verify_runs_every_check_on_the_proof(parts):
    e = parts["effect"]
    v = fx.verify(e, SEG, onset=ONSET, hz=10, part=parts["part"], base=parts["base"], impact=True)
    keys = [c["key"] for c in v["checks"]]
    assert keys == ["in_shot", "in_frame", "sync", "on_onset", "audio_landed", "picture_landed"]
    assert v["ok"] is True, v
    assert all(c["ok"] is True for c in v["checks"]), v
    assert v["at"][:4] == "2026" or len(v["at"]) >= 19


def test_verify_skips_what_it_cannot_measure_and_fails_what_it_can():
    e = _effect()
    v = fx.verify(e, SEG, onset=None, impact=True)
    by = {c["key"]: c for c in v["checks"]}
    assert v["ok"] is True
    assert by["on_onset"]["ok"] is None and by["on_onset"]["detail"].startswith("skipped:")
    assert by["audio_landed"]["ok"] is None and by["picture_landed"]["ok"] is None
    assert by["sync"]["ok"] is True
    # not an impact: on_onset is skipped even with a track
    assert {c["key"]: c["ok"] for c in fx.verify(e, SEG, onset=ONSET, impact=False)["checks"]}["on_onset"] is None
    # off the peaks
    off = _effect(events=[{"t": 1.9, "x": 0.5, "y": 0.5}])
    v = fx.verify(off, SEG, onset=ONSET, hz=10)
    assert v["ok"] is False and {c["key"]: c["ok"] for c in v["checks"]}["on_onset"] is False
    # outside the shot
    out = _effect(events=[{"t": 4.0, "x": 0.5, "y": 0.5}])
    assert {c["key"]: c["ok"] for c in fx.verify(out, SEG)["checks"]}["in_shot"] is False
    # a sound that outlasts the marker
    long = _effect(sound={"duration": 1.0, "layers": [{"type": "click"}]})
    assert {c["key"]: c["ok"] for c in fx.verify(long, SEG)["checks"]}["sync"] is False
    # an offset that pushes the box off the frame
    away = _effect(events=[{"t": 1.5, "x": 0.9, "y": 0.5}],
                   overlay={"shapes": X_LINES, "anim": {"dx": [[0, 0], [0.3, 0.5]]}})
    assert {c["key"]: c["ok"] for c in fx.verify(away, SEG)["checks"]}["in_frame"] is False
    # a silent effect: sync and audio are skipped, not failed
    silent = _effect(sound=None)
    by = {c["key"]: c for c in fx.verify(silent, SEG)["checks"]}
    assert by["sync"]["ok"] is None and by["audio_landed"]["ok"] is None


# ------------------------------------------------------------ frames and strips

def test_frames_for_and_contact_strip(project, tmp_path):
    from PIL import Image
    proxy = project["footage"] / "CLIP_A.MP4"
    frames = fx.frames_for(proxy, [1.5, 2.4], tmp_path / "f", width=320)
    assert [p.name for p in frames] == ["t_1.50.jpg", "t_2.40.jpg"]
    assert Image.open(frames[0]).size == (320, 180)
    strip = fx.contact_strip(frames, ["[0] t=1.50s", "[1] t=2.40s"], tmp_path / "strip.jpg", cols=4)
    im = Image.open(strip)
    assert im.format == "JPEG" and im.width > 640 and im.height >= 180


# ------------------------------------------------------------ the model calls

class Scripted:
    """A backend that answers from a queue and keeps every request."""
    name = "scripted"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.seen: list = []

    def complete(self, request):
        self.seen.append(request)
        text = json.dumps(self.answers.pop(0) if len(self.answers) > 1 else self.answers[0])
        return inference.Result(content=text, input_tokens=10, output_tokens=5, backend="scripted",
                                model=config.model_for(request.role), projected_usd=1e-4,
                                latency_ms=1, raw="```json\n" + text + "\n```")


DESIGNED = {k: HIT[k] for k in ("name", "why", "events", "overlay", "sound")}
TRANSCRIPT = [{"start": 0.5, "end": 2.0, "text": "hello there"},
              {"start": 2.4, "end": 4.0, "text": "how are you"},
              {"start": 5.0, "end": 5.6, "text": "goodbye"}]
SIDECAR = {"frame_hz": 10, "tracks": {"onset": ONSET}, "transcript": TRANSCRIPT}
CLIP = {"clip": "CLIP_A.MP4", "duration": 6.0, "transcript": TRANSCRIPT}


@pytest.fixture
def scripted():
    def install(*answers):
        b = Scripted(*answers)
        inference.set_backend(b)
        inference.reset_spend()
        return b
    yield install
    inference.set_backend(None)


def test_design_prompt_carries_the_evidence_and_the_system_the_vocabulary():
    peaks = fx.onset_peaks(ONSET, 10, 1.0, 3.0)
    system, prompt = fx.build_design_prompt("hit markers where my skis hit the rocks", SEG, CLIP, peaks=peaks)
    assert "hit markers where my skis hit the rocks" in prompt
    assert "CLIP_A.MP4" in prompt and "1.00s" in prompt and "3.00s" in prompt and "first" in prompt
    assert "hello there" in prompt and "how are you" in prompt and "goodbye" not in prompt
    assert "t=1.50" in prompt and "t=2.40" in prompt
    for token in (f"{fx.MIN_SIZE}", f"{fx.MAX_SIZE}", f"{fx.MAX_EVENTS}", f"{fx.MAX_SHAPES}",
                  f"{fx.MIN_DURATION}", f"{fx.MAX_DURATION}", "top-left", "-1 to 1", "JSON only",
                  "3200", "triangle", "click", "#ff0000", '"hp": 4000'):
        assert token in system, token
    for kind in fx.SHAPES + fx.LAYERS + fx.TRACKS:
        assert f'"{kind}"' in system, kind
    ref = {"goal": "the skis", "marks": [[0.3, 0.6]], "t": 1.55}
    _, p2 = fx.build_design_prompt("hit markers", SEG, CLIP, peaks=peaks, reference=ref)
    assert "DREW A REFERENCE" in p2 and "(0.300, 0.600)" in p2 and "the skis" in p2
    _, p3 = fx.build_place_prompt("hit markers", HIT, [{"t": 1.5}, {"t": 2.4}], Path("strip.jpg"))
    assert "[0] t=1.50s" in p3 and "[1] t=2.40s" in p3 and '"hit"' in p3
    s4, p4 = fx.build_revise_prompt(HIT, "make them red")
    assert "make them red" in p4 and '"hit markers"' in p4 and "WORKED EXAMPLE" in s4


def test_design_makes_a_validated_proposal_through_the_judge_role(scripted):
    b = scripted(DESIGNED)
    e = fx.design("hit markers where my skis hit the rocks", SEG, CLIP, SIDECAR, segments=SEGS, clips=CLIPS)
    assert len(b.seen) == 1 and b.seen[0].role == config.ROLE_JUDGE
    assert b.seen[0].system and "WORKED EXAMPLE" in b.seen[0].system and b.seen[0].images == ()
    assert e["status"] == "proposed" and e["shot"] == "s1" and e["clip"] == "CLIP_A.MP4"
    assert [ev["t"] for ev in e["events"]] == [1.5, 2.4]
    assert e["name"] == "hit markers" and e["note"].startswith("hit markers where")
    assert e["created"] and e["history"] == [{"note": "hit markers where my skis hit the rocks", "at": e["created"]}]
    assert e["overlay"]["flash"]["color"] == "#ff0000" and e["sound"]["layers"][1]["hp"] == 1500
    # the model's answer is validated, not trusted: an invented shape fails loudly
    scripted({**DESIGNED, "overlay": {"shapes": [{"type": "sparkle"}]}})
    with pytest.raises(ValueError, match="unknown shape type"):
        fx.design("hit markers", SEG, CLIP, SIDECAR, segments=SEGS, clips=CLIPS)


def test_design_takes_the_anchors_from_a_reference(scripted):
    scripted(DESIGNED)
    ref = {"goal": "the skis", "marks": [[0.3, 0.6], [0.7, 0.65]], "t": 1.58}
    e = fx.design("hit markers on the skis", SEG, CLIP, SIDECAR, reference=ref, segments=SEGS, clips=CLIPS)
    # the marks are the anchors; t is the onset peak nearest the reference frame
    assert [(ev["t"], ev["x"], ev["y"]) for ev in e["events"]] == [(1.5, 0.3, 0.6), (1.5, 0.7, 0.65)]


def test_design_places_by_looking_and_drops_what_is_not_a_hit(scripted, project, tmp_path):
    placed = {"events": [{"t": 1.5, "x": 0.31, "y": 0.62, "hit": True, "why": "skis on the rock"},
                         {"t": 2.4, "x": 0.5, "y": 0.5, "hit": False, "why": "just snow"}]}
    b = scripted(DESIGNED, placed)
    wd = tmp_path / "fx_x"
    e = fx.design("hit markers where my skis hit the rocks", SEG, CLIP, SIDECAR, place=True,
                  proxy=project["footage"] / "CLIP_A.MP4", workdir=wd, segments=SEGS, clips=CLIPS)
    assert len(b.seen) == 2
    assert (wd / "strip.jpg").exists() and sorted(p.name for p in (wd / "frames").iterdir()) == ["t_1.50.jpg", "t_2.40.jpg"]
    assert list(b.seen[1].images) == [wd / "strip.jpg"] and "[1] t=2.40s" in b.seen[1].prompt
    assert [(ev["t"], ev["x"], ev["y"]) for ev in e["events"]] == [(1.5, 0.31, 0.62)]
    assert e["events"][0]["label"] == "skis on the rock"
    # every candidate refused: the strongest one is kept rather than none
    scripted(DESIGNED, {"events": [{"t": 1.5, "x": 0.3, "y": 0.6, "hit": False},
                                   {"t": 2.4, "x": 0.5, "y": 0.5, "hit": False}]})
    e2 = fx.design("hit markers", SEG, CLIP, SIDECAR, place=True, proxy=project["footage"] / "CLIP_A.MP4",
                   workdir=tmp_path / "fx_y", segments=SEGS, clips=CLIPS)
    assert len(e2["events"]) == 1


def test_revise_keeps_id_and_events_unless_the_note_moves_them(scripted):
    e = _effect()
    red = json.loads(json.dumps(DESIGNED))
    for s in red["overlay"]["shapes"]:
        s["color"] = "#ff0000"
    red.pop("events")                                            # the model left them alone
    b = scripted(red)
    e["history"] = [{"note": "hit markers", "at": "2026-09-20T12:00:00"}]
    new = fx.revise(e, "make them red", SEGS, CLIPS)
    assert b.seen[0].role == config.ROLE_JUDGE and "make them red" in b.seen[0].prompt
    assert new["id"] == e["id"] and new["events"] == e["events"]
    assert all(s["color"] == "#ff0000" for s in new["overlay"]["shapes"])
    assert new["status"] == "proposed" and new["note"] == "make them red"
    assert [h["note"] for h in new["history"]] == ["hit markers", "make them red"]
    one = {**DESIGNED, "events": [{"t": 1.5, "x": 0.5, "y": 0.7}]}
    scripted(one)
    fewer = fx.revise(new, "one hit only, the big one", SEGS, CLIPS)
    assert [ev["t"] for ev in fewer["events"]] == [1.5] and len(fewer["history"]) == 3


# ---------------------------------------------------------------- the title slide's lessons (I12.9)

def test_text_lines_reveal_and_the_shapes_clock(tmp_path):
    """Karl's title slide: three text shapes at one spot, sized twice as big on the
    monitor as on the master, no typewriter, a rumble for a clatter. One text shape
    holds the lines; h is a fraction of the box on both sides; a typewriter reveal
    grows with time; a shape has a start, an end and a fade."""
    e = _effect(overlay={"duration": 4.0, "size": 1.6, "shapes": [
        {"type": "rect", "at": [0, 0], "w": 3, "h": 3, "fill": True, "color": "#000000", "end": 4.0, "fade": 1.0},
        {"type": "text", "text": " 2026 BLIZZARD \n Killington \n\n", "h": 0.08, "reveal": "typewriter", "cps": 10,
         "start": 0.5}]})
    txt = e["overlay"]["shapes"][1]
    assert txt["text"] == "2026 BLIZZARD\nKillington" and txt["reveal"] == "typewriter" and txt["cps"] == 10
    assert txt["start"] == 0.5 and txt["fit"] is True
    assert fx.text_at(txt, 0.3) == ("", 1.0)
    assert fx.text_at(txt, 1.0)[0] == "2026 "                 # 5 chars at 10 cps after 0.5 s
    assert fx.text_at(txt, 2.0)[0] == "2026 BLIZZARD\nK"        # the break counts as one
    assert fx.shape_alpha(e["overlay"]["shapes"][0], 3.5, 4.0) == pytest.approx(0.5)
    assert fx.shape_alpha(e["overlay"]["shapes"][0], 4.5, 4.0) == 0.0
    # rasterised: the ink grows as the text types, the slide fades at the end
    ink = []
    for t_ in (0.2, 1.0, 2.5, 3.9):
        d = tmp_path / f"t{t_}"
        one = {**e["overlay"], "duration": 4.0}
        frames = fx.render_overlay_frames(one, 640, 360, 2, d)      # 2 fps: frame k is t=k/2
        import numpy as np
        from PIL import Image
        k = min(len(frames) - 1, int(t_ * 2))                         # 3.9 → frame 7, t=3.5: mid-fade
        a = np.asarray(Image.open(frames[k]))
        ink.append(int((a[..., :3].max(axis=2) > 128).sum()))       # bright pixels = text
        if t_ == 3.9:
            assert 100 < np.median(a[..., 3]) < 200                # the slide is mid-fade (the text has no fade of its own)
    assert ink[0] == 0 < ink[1] < ink[2]


def test_a_repeating_sound_is_many_hits(tmp_path):
    s = fx.validate_sound({"gain_db": -6, "layers": [{"type": "click", "decay": 0.02, "gain": 0.9}],
                           "repeat": {"every": 0.1, "count": 12, "jitter": 0.0}})
    tail = s["layers"][0]["attack"] + s["layers"][0]["decay"]          # the click's default 2 ms attack
    assert s["duration"] == pytest.approx(0.1 * 11 + tail + 0.05, abs=1e-3)
    p = fx.synth_sound(s, tmp_path / "clatter.wav")
    import wave
    import numpy as np
    with wave.open(str(p), "rb") as wf:
        x = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2")[::2].astype(float)
        sr = wf.getframerate()
    env = np.abs(x)
    # twelve onsets, 100 ms apart: count the rises above a tenth of the peak that
    # follow at least 50 ms of quiet
    thr = env.max() * 0.1
    loud = env > thr
    onsets = ([0] if loud[0] else []) + [i for i in range(1, len(loud))
                                          if loud[i] and not loud[max(0, i - int(0.05 * sr)):i].any()]
    assert len(onsets) == 12, len(onsets)
    assert abs((onsets[1] - onsets[0]) / sr - 0.1) < 0.005


def test_limits_travel_with_the_effect():
    e = _effect(limits="an effect cannot add two seconds to the shot")
    assert e["limits"].startswith("an effect cannot")
    assert "limits" not in _effect()
