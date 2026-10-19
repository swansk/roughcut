"""An In/Out export's A2 bed starts at the In's point in it (wave 16, lane FRAMEACC;
REDESIGN-GOAL dimensions 5 and 8).

The bed (`effects_music`) has no `at`: it runs from the sequence's 0, looped, faded in
over the sequence's first `fade_in` seconds and out over its last `fade_out` (the
Program monitor: app.js `cueBed` plays it at film time mod the track, `bedGainAt` fades
it on the sequence's clock). Lane INOUT put V2+ and A3+ on an In/Out range's own clock;
the bed was left on the film's: an export from 2.5 s opened on the bed's head, faded in
again from silence, and faded out over the range's last four seconds whether or not the
sequence ended there. `server.export_music` now gives the render where the film starts
in the sequence (`in`, `range_start`) and the sequence's length (`seq_s`), and
effects.py `_bed_chain` starts the loop there with the fades where the sequence has them.

The bed here is a track whose every second is its own tone (300 + 100·n Hz in second
n), so an FFT of any window of the film says which second of the bed it is; levels are
read with volumedetect. The renders are real (assemble.py through `/api/render`, the
Match Sequence preset, the clips silent, the bed un-ducked at 0 dB) on the CPU path
(ROUGHCUT_GPU=0). No model is called.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from conftest import _sidecar  # noqa: E402

CLIP_S = 10.0
BED_S = 12.0
SR = 48000


def _freq(n: int) -> int:
    return 300 + 100 * n


@pytest.fixture(scope="module", autouse=True)
def cpu_only():
    mp = pytest.MonkeyPatch()
    mp.setenv("ROUGHCUT_GPU", "0")
    yield
    mp.undo()


@pytest.fixture(scope="module")
def proj(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("inoutmusic")
    footage, sidecars, work, assets = (root / d for d in ("footage", "sidecars", "work", "assets"))
    for d in (footage, sidecars, work, assets / "music"):
        d.mkdir(parents=True)
    for stem, colour in (("CLIP_R", "red"), ("CLIP_G", "0x00c000"), ("CLIP_B", "blue")):
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-nostdin",
             "-f", "lavfi", "-i", f"color=c={colour}:size=320x180:rate=24:duration={CLIP_S}",
             "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo,atrim=0:{CLIP_S}",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-t", f"{CLIP_S}", str(footage / f"{stem}.MP4")],
            check=True, capture_output=True)
        side = _sidecar(stem)
        side["duration_s"] = CLIP_S
        side["transcript"] = []                    # nothing said: no speech regions
        (sidecars / f"{stem}.audio.json").write_text(json.dumps(side), encoding="utf-8")
    # the bed: second n is a 300 + 100·n Hz tone at -12 dBFS
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi", "-i",
         f"aevalsrc='0.25*sin(2*PI*(300+100*floor(t))*t)':s={SR}:d={BED_S}",
         "-ac", "2", "-c:a", "pcm_s16le", str(assets / "music" / "steps.wav")],
        check=True, capture_output=True)
    edl = root / "edl.json"
    return {"root": root, "footage": footage, "sidecars": sidecars, "work": work,
            "edl": edl, "assets": assets}


# 0-3 red, 3-6 green, 6-9 blue: a 9 s sequence; the bed un-ducked at 0 dB, faded in over
# its first 1.5 s and out over its last 4 (5-9 on the sequence)
MUSIC = {"asset": "music/steps.wav", "gain_db": 0.0, "duck": False,
         "fade_in": 1.5, "fade_out": 4.0}
EDL = {
    "variant": "T", "title": "inout music", "orient": "none", "story": "", "target_s": [1, 30],
    "fps": 24.0,
    "segments": [{"id": "s1", "clip": "CLIP_R.MP4", "in": 0.0, "out": 3.0, "why": "red"},
                 {"id": "s2", "clip": "CLIP_G.MP4", "in": 0.0, "out": 3.0, "why": "green"},
                 {"id": "s3", "clip": "CLIP_B.MP4", "in": 0.0, "out": 3.0, "why": "blue"}],
    "effects_music": MUSIC,
}


def _sent(a: float, b: float) -> list[dict]:
    """export.js `clipRange(sequence, a, b)` for the fixture (the shots inside, cut)."""
    out, t = [], 0.0
    for s in EDL["segments"]:
        lo, hi = max(a, t), min(b, t + 3.0)
        if hi > lo:
            out.append({**s, "in": round(lo - t, 4), "out": round(hi - t, 4)})
        t += 3.0
    return out


# ---------------------------------------------------------------- reading the sound

def _pcm(path: Path) -> np.ndarray:
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-map", "0:a:0",
                        "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.float32)


def _tone(pcm: np.ndarray, t: float, length: float = 0.3) -> float:
    """The loudest frequency (Hz) of the film's sound over [t, t + length)."""
    a = pcm[int(t * SR): int((t + length) * SR)]
    spec = np.abs(np.fft.rfft(a * np.hanning(len(a))))
    return float(np.fft.rfftfreq(len(a), 1 / SR)[int(np.argmax(spec))])


def _mean_db(path: Path, start: float, length: float) -> float:
    r = subprocess.run(["ffmpeg", "-v", "info", "-nostdin", "-ss", f"{start:.3f}", "-i", str(path),
                        "-t", f"{length:.3f}", "-map", "0:a:0", "-af", "volumedetect",
                        "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"mean_volume: (-?[\d.]+|-inf) dB", r.stderr)
    assert m, r.stderr[-400:]
    return -999.0 if m.group(1) == "-inf" else float(m.group(1))


# ---------------------------------------------------------------- the clock (no render)

def test_the_bed_is_put_on_the_ranges_clock():
    import server
    whole = [dict(s) for s in EDL["segments"]]
    assert server.export_music(MUSIC, whole, _sent(2.5, 7.5)) == {**MUSIC, "in": 2.5, "seq_s": 9.0}
    # a range from the head is still on the sequence's clock for its fade out
    assert server.export_music(MUSIC, whole, _sent(0.0, 4.0)) == {**MUSIC, "in": 0.0, "seq_s": 9.0}
    # the whole sequence, a range that cannot be placed, no bed: as they are
    assert server.export_music(MUSIC, whole, [dict(s) for s in whole]) == MUSIC
    assert server.export_music(MUSIC, whole, [{"id": "zz", "clip": "X", "in": 0, "out": 1}]) == MUSIC
    assert server.export_music(None, whole, _sent(2.5, 7.5)) is None


def test_the_beds_chain_starts_at_the_in_and_fades_where_the_sequence_does():
    from roughcut import effects
    s = effects._resolved({**MUSIC, "in": 2.5, "seq_s": 9.0}, 5.0)
    chain = effects._bed_chain(s, 5.0)
    assert "atrim=start=2.500000:end=7.500000" in chain
    assert "afade=t=in" not in chain and "t+" not in chain        # the In is past the fade in
    assert "afade=t=out:st=2.500:d=4.00" in chain                  # sequence 5.0 = film 2.5
    # the In inside the fade in: from part way up; an Out before the fade out: none
    chain = effects._bed_chain(effects._resolved({**MUSIC, "in": 0.5, "seq_s": 9.0}, 3.0), 3.0)
    assert "volume='min(1,(t+0.500000)/1.5000)':eval=frame" in chain and "t=out" not in chain
    # the In inside the fade out: from part way down
    chain = effects._bed_chain(effects._resolved({**MUSIC, "in": 7.0, "seq_s": 9.0}, 2.0), 2.0)
    assert "volume='max(0,min(1,(2.000000-t)/4.0000))':eval=frame" in chain
    # the whole sequence's chain is the one it always was
    whole = effects._bed_chain(effects._resolved(MUSIC, 9.0), 9.0)
    assert whole == ("[1:a]aloop=loop=-1:size=2e9,atrim=0:9.000,asetpts=N/SR/TB,volume=0.00dB,"
                     "afade=t=in:st=0:d=1.50,afade=t=out:st=5.000:d=4.00[bed]")
    with pytest.raises(ValueError):
        effects.music_spec({**MUSIC, "in": -1})
    assert effects.music_spec(MUSIC)["in"] is None                 # absent: never saved


# ---------------------------------------------------------------- the export

@pytest.fixture
def api(proj):
    from fastapi.testclient import TestClient
    import server
    server.configure(proj["edl"], proj["footage"], proj["sidecars"], proj["work"],
                     proxies=False, visual=proj["work"] / "no-visual", assets=proj["assets"])
    proj["edl"].write_text(json.dumps(EDL), encoding="utf-8")
    with TestClient(server.app) as c:
        yield c


def _export(api, sent: list[dict], name: str, timeout: float = 240.0) -> Path:
    r = api.post("/api/render", json={"segments": sent, "preset": "match", "name": name,
                                      "folder": "inout-music-tests"})
    assert r.status_code == 200, r.text
    job = r.json()["job"]
    end = time.time() + timeout
    while time.time() < end:
        st = api.get(f"/api/render/{job}").json()
        if st["state"] != "running":
            break
        time.sleep(0.3)
    assert st["state"] == "done", st
    return Path(r.json()["path"])


def test_an_in_out_export_hears_the_bed_from_the_in(api):
    """In 2.5 / Out 7.5 (a 5 s film): the film's t is the bed's 2.5 + t — its 0.1 s the
    bed's second 2 (500 Hz), 1.0 s its second 3 (600), 4.2 s its second 6 (900); before,
    the bed's head (300, 400, 700). No fade in (the sequence's was over by 1.5 s); the
    fade out from the sequence's 5.0 (film 2.5) to its 9.0: half way down at the film's
    end (seq 7.0-7.5), not the range's own four-second fade (-18 dB there before)."""
    film = _export(api, _sent(2.5, 7.5), "in out bed")
    pcm = _pcm(film)
    # (the scored film's sound reads ~0.07 s short of its picture, as it did before)
    assert abs(len(pcm) / SR - 5.0) < 0.1
    seen = {t: round(_tone(pcm, t)) for t in (0.1, 1.0, 2.0, 3.0, 4.2)}
    assert seen == {0.1: _freq(2), 1.0: _freq(3), 2.0: _freq(4), 3.0: _freq(5),
                    4.2: _freq(6)}, seen
    full = _mean_db(film, 0.6, 0.6)                          # seq 3.1-3.7: at full level
    assert _mean_db(film, 0.05, 0.3) == pytest.approx(full, abs=1.0)   # no fade in
    assert _mean_db(film, 1.7, 0.6) == pytest.approx(full, abs=1.0)    # seq 4.2-4.8
    # seq 6.9-7.1: gain (9 - t) / 4 ≈ 0.5, -6 dB
    assert _mean_db(film, 4.4, 0.2) == pytest.approx(full - 6.0, abs=1.5)


def test_an_in_inside_the_fade_in_starts_the_bed_part_way_up(api):
    """In 0.5 / Out 3.5: the bed's 0.5 s, at a third of its level (0.5 / 1.5) and rising
    to full at the film's 1.0; before, it faded in again from silence (−20 dB at 0.15)."""
    film = _export(api, _sent(0.5, 3.5), "in out bed fade")
    pcm = _pcm(film)
    assert round(_tone(pcm, 0.55)) == _freq(1)                # bed 1.05-1.35 s: second 1
    full = _mean_db(film, 1.6, 0.6)
    # film 0.05-0.25 → seq 0.55-0.75: gain ≈ 0.43, −7.3 dB
    assert _mean_db(film, 0.05, 0.2) == pytest.approx(full - 7.3, abs=1.5)
    assert _mean_db(film, 1.1, 0.4) == pytest.approx(full, abs=1.0)
