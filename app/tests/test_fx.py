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
    ({"overlay": {"shapes": [{"type": "text", "text": "x" * 25}]}}, "text.text"),
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
