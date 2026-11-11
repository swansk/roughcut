"""Which way up a clip is, decided per clip (wave 23, lane FILM; COLD-08, COLD-13, WORDS-23,
FINISH-22), and the true-peak limiter every film ends in (COLD-19, FINISH-20).

Copper's GX010474 / 484 / 492 carry a rotation flag of +-90 and were shot upright, so
honouring the flag laid people on their side in the proxy, preview, poster, Source, Program
and export; 497-499 are flagged 180 and truly inverted. Killington's flags are all right.
The telemetry pass already measures gravity in the camera's own axes: where it contradicts
the flag, the flag is ignored; where there is no telemetry the flag is honoured as before;
the editor can overrule either by hand.

  * the decision, on the numbers of the real sidecars (Copper's 26 clips' six flagged ones,
    Killington's twelve: not one of Killington's changes);
  * the pipeline on a fixture bin whose clips carry the same four cases: the proxy, the
    poster, the served picture and the export agree with the decision; a clip whose decision
    did not change is not rebuilt; telemetry that lands after the first proxy fixes the
    picture by itself; the hand override;
  * the limiter on a hot film, on every path that writes the sound.

    uv run --with pytest --with fastapi --with uvicorn --with httpx --with numpy \
        pytest app/tests/test_clip_orientation.py -q
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

from conftest import _sidecar
from test_preview_orientation import (_fetch, _make_pattern, _quads, _same, _stored,
                                      _upright)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from roughcut import limiter, orient  # noqa: E402

# ------------------------------------------------------------- the decision, on real numbers
# (flag as ffprobe reports it, the telemetry sidecar's orientation.median_up_vector)

COPPER = {                      # flagged clips of /mnt/roughcut/footage/copper-02-2026
    "GX010474": (-90, [0.994, -0.052, 0.056]),
    "GX010484": (90, [0.912, -0.102, 0.319]),
    "GX010492": (-90, [0.949, 0.166, 0.115]),
    "GX010497": (-180, [-0.943, 0.312, 0.015]),
    "GX010498": (-180, [-0.944, 0.243, 0.12]),
    "GX010499": (-180, [-0.861, -0.496, 0.121]),
}
COPPER_UNFLAGGED = {"GX010475": [0.999, -0.023, 0.01], "GX010478": [0.988, 0.018, 0.1],
                    "GX010482": [0.897, 0.112, 0.304], "GX010495": [0.971, -0.015, -0.236]}
KILLINGTON = {                  # /mnt/roughcut/footage/killington-neutral, all twelve
    "CLIP_01": (0, [0.939, -0.04, 0.194]), "CLIP_02": (0, [0.877, -0.032, 0.375]),
    "CLIP_03": (0, [0.981, 0.117, 0.122]),
    "CLIP_04": (-180, [-0.955, -0.074, -0.155]), "CLIP_05": (-180, [-0.985, -0.049, -0.077]),
    "CLIP_06": (-180, [-0.905, -0.005, -0.347]), "CLIP_07": (-180, [-0.915, -0.002, -0.386]),
    "CLIP_08": (-180, [-0.958, -0.082, -0.141]), "CLIP_09": (-180, [-0.903, -0.06, -0.374]),
    "CLIP_10": (-180, [-0.274, -0.015, -0.962]), "CLIP_11": (-180, [-0.883, -0.063, -0.424]),
    "CLIP_12": (-180, [-0.908, -0.066, -0.364]),
}
CONV = {"vertical": 0, "lateral": 1, "forward": 2}


def test_copper_three_spurious_flags_are_ignored_and_three_right_ones_are_kept():
    got = {c: orient.decide(f, up, None, CONV) for c, (f, up) in COPPER.items()}
    assert {c: d["mode"] for c, d in got.items()} == {
        "GX010474": "none", "GX010484": "none", "GX010492": "none",
        "GX010497": "auto", "GX010498": "auto", "GX010499": "auto"}
    assert {d["why"] for c, d in got.items() if c in ("GX010474", "GX010484", "GX010492")} == {"sensor"}
    assert {d["why"] for c, d in got.items() if c in ("GX010497", "GX010498", "GX010499")} == {"agrees"}


def test_an_unflagged_clip_is_never_turned_whatever_the_sensor_says():
    for c, up in COPPER_UNFLAGGED.items():
        d = orient.decide(0, up, "upright", CONV)
        assert (d["mode"], d["why"]) == ("auto", "noflag"), c
    # the sensor can only veto a flag: an inverted camera with no flag is left as the file says
    assert orient.decide(0, [-0.95, 0, 0.1], "inverted", CONV)["mode"] == "auto"


def test_killington_is_not_changed_by_any_of_its_twelve_decisions():
    for clip, (flag, up) in KILLINGTON.items():
        d = orient.decide(flag, up, None, CONV)
        assert d["mode"] == "auto", (clip, d)
        assert d["flag"] == orient.flag_degrees(flag)
    # CLIP_10 is pitched at the ground: the sensor is not conclusive, so the flag it always
    # had is trusted rather than overruled
    assert orient.decide(*KILLINGTON["CLIP_10"], None, CONV)["why"] == "unclear"
    assert [orient.decide(*KILLINGTON[c], None, CONV)["why"] for c in ("CLIP_04", "CLIP_12")] \
        == ["agrees", "agrees"]


def test_without_telemetry_the_flag_is_honoured_as_before():
    d = orient.decide(-90, None, None, None)
    assert (d["mode"], d["why"]) == ("auto", "nosensor")
    # the mount word alone (a felt sidecar without the vector) is enough to decide
    assert orient.decide(90, None, "upright", None)["mode"] == "none"
    assert orient.decide(180, None, "inverted", None)["mode"] == "auto"


def test_a_clip_on_its_side_keeps_its_quarter_turn():
    # gravity along the lateral axis: a camera genuinely held sideways
    d = orient.decide(90, [0.1, 0.97, 0.05], "upright", CONV)
    assert (d["mode"], d["why"]) == ("auto", "unclear")


def test_the_editors_word_beats_the_sensor_both_ways():
    assert orient.decide(-90, [0.99, 0, 0.05], None, CONV, hand="auto")["mode"] == "auto"
    assert orient.decide(-180, [-0.95, 0, 0.1], None, CONV, hand="none")["mode"] == "none"
    assert orient.decide(-90, [0.99, 0, 0.05], None, CONV, hand="bogus")["why"] == "sensor"


def test_the_decision_is_said_in_plain_words():
    ign = orient.words(orient.decide(-90, COPPER["GX010474"][1], None, CONV))
    assert ign.startswith("Rotation flag ignored") and "sensor says" in ign and "upright" in ign
    kept = orient.words(orient.decide(-180, COPPER["GX010497"][1], None, CONV))
    assert "180°" in kept and "upside down" in kept and "agrees" in kept
    assert "no sensor reading" in orient.words(orient.decide(90, None, None, None))
    assert orient.words(orient.decide(0, [0.9, 0, 0], None, CONV)) == ""
    assert "by hand" in orient.words(orient.decide(90, None, None, None, hand="none"))


# ------------------------------------------------------------------- the pipeline, on a fixture bin

CASES = {          # stem: (flag, telemetry up vector | None)
    "SENS90": (90, [0.97, 0.05, 0.1]),        # flag the sensor contradicts: ignored
    "SENS180": (180, [0.96, 0.02, 0.2]),      # the same at 180 (an export keeps its aspect)
    "INV180": (180, [-0.95, 0.05, 0.1]),      # flag the sensor agrees with: kept
    "NOTEL90": (90, None),                    # no telemetry: kept
    "FLAT": (0, [0.98, 0.0, 0.1]),            # no flag
}


def _telemetry(tdir: Path, stem: str, up) -> None:
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / f"{stem.upper()}.telemetry.json").write_text(json.dumps({
        "gpmd": True, "orientation": {"convention": CONV, "median_up_vector": up,
                                      "mount": "upright" if up[0] > 0 else "inverted"}}),
        encoding="utf-8")


@pytest.fixture(scope="module")
def rbin(tmp_path_factory):
    root = tmp_path_factory.mktemp("clip-orient")
    footage, sidecars = root / "bin", root / "sidecars"
    footage.mkdir()
    sidecars.mkdir()
    for stem, (flag, _) in CASES.items():
        _make_pattern(footage / f"{stem}.MP4", flag)
        (sidecars / f"{stem}.audio.json").write_text(json.dumps(_sidecar(stem)), encoding="utf-8")
    for stem in ("SENS90", "SENS180", "INV180", "FLAT"):
        assert np.abs(_upright(footage / f"{stem}.MP4") - _stored(footage / f"{stem}.MP4")).max() > 100 \
            or stem == "FLAT", f"{stem}: the fixture's turn is not visible"
    return {"root": root, "footage": footage, "sidecars": sidecars}


def _open(server, rbin, work: Path, orient_arg: str = "auto", telemetry: dict | None = None):
    """Open the fixture bin the way /api/projects/open does (orient auto); `telemetry`
    (stem -> up vector) is written first, as the index's telemetry stage would have."""
    server.MEDIA["failed"].clear()
    server.MEDIA["wanted"].clear()
    server.STATE.pop("orient_map", None)
    tdir = work / "telemetry" / rbin["footage"].name
    for stem, up in (telemetry if telemetry is not None else
                     {s: v[1] for s, v in CASES.items() if v[1] is not None}).items():
        _telemetry(tdir, stem, up)
    server.configure(None, rbin["footage"], rbin["sidecars"], work, proxies=False,
                     orient=orient_arg, visual=work / "no-visual")


@pytest.fixture
def board(rbin, tmp_path):
    from fastapi.testclient import TestClient
    import server
    work = tmp_path / "work"
    _open(server, rbin, work)
    with TestClient(server.app) as c:
        yield {"client": c, "server": server, "work": work, "footage": rbin["footage"]}
    server.MEDIA["wanted"].clear()
    server.STATE.pop("orient_map", None)


def _proxy(server, stem: str) -> Path:
    return server.STATE["proxy_dir"] / f"{stem}.mp4"


def test_each_clip_gets_the_decision_its_flag_and_sensor_make(board):
    server = board["server"]
    rows = {Path(c).stem: server.orient_row(c) for c in server.footage_clips()}
    assert {s: r["mode"] for s, r in rows.items()} == {
        "SENS90": "none", "SENS180": "none", "INV180": "auto", "NOTEL90": "auto", "FLAT": "auto"}
    assert rows["SENS90"]["why"] == "sensor" and "upright" in rows["SENS90"]["text"]
    assert rows["NOTEL90"]["why"] == "nosensor" and rows["NOTEL90"]["turned"] is True
    assert rows["FLAT"]["text"] == "" and rows["FLAT"]["turned"] is False
    # it is stored with the project, and served to the Project panel and the Index panel
    stored = json.loads((board["work"] / "orient" / f"{board['footage'].name}.json").read_text())
    assert stored["clips"]["SENS90"]["mode"] == "none"
    api = {r["stem"]: r for r in board["client"].get("/api/clips").json()["clips"]}
    assert api["SENS90"]["orient"]["mode"] == "none" and "sensor" in api["SENS90"]["orient"]["text"]
    ix = {r["stem"]: r for r in board["client"].get("/api/index/clips").json()["rows"]}
    assert ix["INV180"]["orient"]["why"] == "agrees"


def test_proxy_poster_and_served_picture_all_follow_the_decision(board, tmp_path):
    server, client = board["server"], board["client"]
    clips = server.footage_clips()
    server.ensure_proxies(clips)
    want = {"SENS90": "stored", "SENS180": "stored", "INV180": "upright", "NOTEL90": "upright",
            "FLAT": "upright"}
    posters = {Path(c["clip"]).stem: c["poster"] for c in client.get("/api/clips").json()["clips"]}
    for stem, how in want.items():
        src = board["footage"] / f"{stem}.MP4"
        ref = _stored(src) if how == "stored" else _upright(src)
        _same(_quads(_proxy(server, stem)), ref, f"{stem}: the proxy")
        got = _fetch(client, f"/media/proxy/{stem}.mp4", tmp_path / f"{stem}.served.mp4")
        _same(_quads(got), ref, f"{stem}: the served proxy")
        post = _fetch(client, posters[stem].split("?")[0] + "?t=0.5", tmp_path / f"{stem}.jpg")
        _same(_quads(post), ref, f"{stem}: the poster")
        # a poster cut from the original, before there is a proxy
        dest = tmp_path / f"{stem}.orig.jpg"
        server.build_poster(src, dest, 0.5, original=True)
        _same(_quads(dest), ref, f"{stem}: the poster cut from the original")


def test_a_preview_is_made_under_the_clips_own_rule(board, tmp_path, monkeypatch):
    server = board["server"]
    monkeypatch.setenv("ROUGHCUT_PREVIEW_GPU", "0")
    for stem, how in (("SENS90", "stored"), ("INV180", "upright")):
        src = board["footage"] / f"{stem}.MP4"
        dest = tmp_path / f"{stem}.prev.mp4"
        server.build_preview(src, dest, server.clip_orient(f"{stem}.MP4"))
        _same(_quads(dest), _stored(src) if how == "stored" else _upright(src), f"{stem}: preview")


def test_telemetry_that_lands_after_the_first_proxy_fixes_the_picture_alone(rbin, tmp_path):
    from fastapi.testclient import TestClient
    import server
    work = tmp_path / "work"
    # the project as it was made before the index ran: no telemetry for anything
    _open(server, rbin, work, telemetry={})
    clips = server.footage_clips()
    server.ensure_proxies(clips)
    src = rbin["footage"] / "SENS90.MP4"
    assert server.clip_orient("SENS90.MP4") == "auto"            # no sensor yet: the flag is trusted
    _same(_quads(_proxy(server, "SENS90")), _upright(src), "built under the flag")
    url_before = server.poster_url("SENS90")
    built = {s: _proxy(server, s).stat().st_mtime_ns for s in CASES}

    # the index's telemetry stage lands (the stage calls refresh_orient for its clip)
    tdir = work / "telemetry" / rbin["footage"].name
    for stem, (flag, up) in CASES.items():
        if up is not None:
            _telemetry(tdir, stem, up)
    changed = server.refresh_orient(["SENS90.MP4"], rebuild=False)
    assert changed == ["SENS90.MP4"]
    assert not _proxy(server, "SENS90").exists(), "the stale proxy must go"
    assert server.poster_url("SENS90") != url_before, "its poster URL changes (served immutable)"
    assert server.clip_orient("SENS90.MP4") == "none"
    # ...and the others, whose decision did not change, are untouched: not rebuilt, not renamed
    changed = server.refresh_orient(rebuild=False)               # the whole bin, as at open
    assert sorted(changed) == ["SENS180.MP4"]
    for stem in ("INV180", "NOTEL90", "FLAT"):
        assert _proxy(server, stem).stat().st_mtime_ns == built[stem], stem
        assert server.poster_url(stem) == f"/media/poster/{stem}.v2.jpg", stem
    assert server.refresh_orient(rebuild=False) == [], "deciding again changes nothing"
    server.ensure_proxies(clips)                                  # what the background rebuild does
    _same(_quads(_proxy(server, "SENS90")), _stored(src), "rebuilt under the sensor")
    for stem in ("INV180", "NOTEL90", "FLAT"):
        assert _proxy(server, stem).stat().st_mtime_ns == built[stem], stem


def test_the_index_telemetry_stage_decides_the_clip_it_just_read(rbin, tmp_path, monkeypatch):
    """The stage that writes the sensor's sidecars decides that clip's rotation as its last
    step, so the picture is right with nobody asking."""
    import server
    work = tmp_path / "work"
    _open(server, rbin, work, telemetry={})
    server.ensure_proxies(server.footage_clips())
    assert _proxy(server, "SENS90").exists()

    def fake_tool(cmd):                 # the telemetry tool: writes the sidecar it would
        for stem, (flag, up) in CASES.items():
            if up is not None and stem in " ".join(cmd):
                _telemetry(server.telemetry_dir(), stem, up)
        return ""

    class J:
        clips: dict = {}

    monkeypatch.setattr(server, "_run_tool", fake_tool)
    monkeypatch.setattr(server, "has_telemetry", lambda clip: True)
    monkeypatch.setattr(server, "_orient_rebuild", lambda clips: None)
    server._index_stage(J(), "SENS90.MP4", "telemetry")
    assert server.clip_orient("SENS90.MP4") == "none"
    assert not _proxy(server, "SENS90").exists(), "the proxy made under the flag is dropped"
    assert _proxy(server, "INV180").exists(), "a clip this stage did not touch keeps its proxy"


def test_a_project_made_before_this_loses_only_the_pictures_that_were_wrong(rbin, tmp_path):
    """Existing work dir: proxies, posters and sprites made under the flag, no orient file.
    Opening it again decides each clip once; only the three clips whose flag the sensor
    contradicts are rebuilt, the others keep their files and their URLs."""
    import server
    work = tmp_path / "work"
    _open(server, rbin, work, telemetry={})
    server.ensure_proxies(server.footage_clips())
    for p in (work / "orient").glob("*.json"):
        p.unlink()                                            # a project from before the rule
    server.STATE.pop("orient_map", None)
    for stem, (flag, up) in CASES.items():
        if up is not None:
            _telemetry(work / "telemetry" / rbin["footage"].name, stem, up)
    keep = {s: _proxy(server, s).stat().st_mtime_ns for s in ("INV180", "NOTEL90", "FLAT")}
    _open(server, rbin, work, telemetry={})
    changed = server.refresh_orient(rebuild=False)
    assert sorted(changed) == ["SENS180.MP4", "SENS90.MP4"]
    for stem, t in keep.items():
        assert _proxy(server, stem).stat().st_mtime_ns == t
        assert server.poster_url(stem) == f"/media/poster/{stem}.v2.jpg"
    # a second open decides nothing new
    _open(server, rbin, work, telemetry={})
    assert server.refresh_orient(rebuild=False) == []


def test_the_editors_override_is_a_clip_level_setting_that_rebuilds_that_clip(board, tmp_path):
    server, client = board["server"], board["client"]
    server.ensure_proxies(server.footage_clips())
    other = _proxy(server, "FLAT").stat().st_mtime_ns
    src = board["footage"] / "INV180.MP4"
    r = client.post("/api/clips/INV180.MP4/orient", json={"mode": "none"})
    assert r.status_code == 200 and r.json()["changed"] is True
    row = r.json()["orient"]
    assert (row["mode"], row["why"], row["hand"]) == ("none", "hand", "none")
    assert "by hand" in row["text"]
    assert not _proxy(server, "INV180").exists() and _proxy(server, "FLAT").stat().st_mtime_ns == other
    server.ensure_proxies(["INV180.MP4"])
    _same(_quads(_proxy(server, "INV180")), _stored(src), "the override reaches the proxy")
    assert client.post("/api/clips/INV180.MP4/orient", json={"mode": "none"}).json()["changed"] is False
    # back to the sensor: the flag the sensor agrees with turns the picture again
    r = client.post("/api/clips/INV180/orient", json={"mode": "sensor"})
    assert r.json()["orient"]["mode"] == "auto" and r.json()["orient"]["hand"] is None
    server.ensure_proxies(["INV180.MP4"])
    _same(_quads(_proxy(server, "INV180")), _upright(src), "back under the sensor")
    # the other way: a hand that keeps a flag the sensor overruled
    assert client.post("/api/clips/SENS90.MP4/orient", json={"mode": "auto"}).json()["orient"]["mode"] == "auto"
    assert client.post("/api/clips/INV180.MP4/orient", json={"mode": "sideways"}).status_code == 400
    assert client.post("/api/clips/NOPE.MP4/orient", json={"mode": "none"}).status_code == 404


def test_a_project_that_ignores_every_flag_still_does(rbin, tmp_path):
    import server
    _open(server, rbin, tmp_path / "work", orient_arg="none")
    assert {server.clip_orient(c) for c in server.footage_clips()} == {"none"}
    assert server.orient_args() == []
    assert server.poster_url("INV180") == "/media/poster/INV180.v2.jpg"


def test_the_export_turns_each_clip_as_the_monitor_does(board, tmp_path):
    server = board["server"]
    args = server.orient_args()
    assert args[0] == "--orient-clips" and Path(args[1]).is_file()
    decided = ROOT / "research" / "tools"
    sys.path.insert(0, str(decided))
    import assemble
    assert assemble.load_orient_clips(Path(args[1])) == {
        "SENS90": "none", "SENS180": "none", "INV180": "auto", "NOTEL90": "auto", "FLAT": "auto"}

    edl = {"variant": "O", "title": "orient", "orient": "auto", "story": "", "target_s": [1, 9],
           "segments": [{"clip": "SENS180.MP4", "in": 0.2, "out": 1.6},
                        {"clip": "INV180.MP4", "in": 0.2, "out": 1.6}]}
    (tmp_path / "edl.json").write_text(json.dumps(edl), encoding="utf-8")

    def film(name: str, *extra: str) -> Path:
        out = tmp_path / name
        r = subprocess.run(
            ["uv", "run", "--quiet", str(decided / "assemble.py"), str(tmp_path / "edl.json"),
             "--footage", str(board["footage"]), "--profile", "preview", "--size", "320x192",
             "--jobs", "1", "-o", str(out), *extra],
            capture_output=True, text=True, env={**os.environ, "ROUGHCUT_GPU": "0"})
        assert r.returncode == 0, r.stdout[-1200:] + r.stderr[-1200:]
        return out

    decided_film = film("decided.mp4", *args)
    _same(_quads(decided_film, t=0.7), _stored(board["footage"] / "SENS180.MP4"),
          "export, flag ignored (sensor: upright)")
    _same(_quads(decided_film, t=2.1), _upright(board["footage"] / "INV180.MP4"),
          "export, flag kept (sensor: upside down)")
    # the file is what makes the difference: without it every flag turns, as it always did
    plain = film("plain.mp4")
    _same(_quads(plain, t=0.7), _upright(board["footage"] / "SENS180.MP4"), "export, no decisions")


def test_every_assemble_the_server_starts_is_given_the_decisions():
    src = (ROOT / "app" / "server.py").read_text()
    assert src.count("*orient_args()") >= 3          # the render job, the export sample, the look pass
    assert "orient_args()" in src[src.index("def _render_job"):src.index("def _render_job") + 2500]


# ------------------------------------------------------------------------------ the limiter

# wind and shouting: pink noise with a loud tenth of every second, over full scale between
# the samples (+2 dBTP) in 32-bit float, so it is the limiter's job and not a clipped square wave
HOT = "anoisesrc=color=pink:amplitude=1.0:duration={d}:seed=11:sample_rate=48000"
HOT_AF = "volume=0.75,volume=enable='lt(mod(t,1),0.1)':volume=2.2"


def _measure(path: Path) -> dict:
    """volumedetect's max / mean and ebur128's true peak and integrated loudness."""
    v = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
                        "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
    e = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
                        "ebur128=peak=true:framelog=quiet", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    summary = e[e.index("Summary:"):]
    return {"max": float(re.search(r"max_volume: (-?[\d.]+) dB", v).group(1)),
            "mean": float(re.search(r"mean_volume: (-?[\d.]+) dB", v).group(1)),
            "tp": float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", summary).group(1)),
            "lufs": float(re.search(r"I:\s+(-?[\d.]+) LUFS", summary).group(1))}


def _hot_clip(path: Path, seconds: float = 4.0) -> None:
    """Wind and shouting: a low noise floor (mean about -20 dB) with loud bursts that reach
    full scale (and past it, so the camera's own AAC clipped them)."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin",
         "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=24:duration={seconds}",
         "-f", "lavfi", "-i", HOT.format(d=seconds), "-af", HOT_AF,
         "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k", "-t", str(seconds), str(path)],
        check=True, capture_output=True)


def test_the_limiter_chain_takes_a_zero_dbfs_signal_to_minus_one_dbtp_and_keeps_its_level(tmp_path):
    wav = tmp_path / "hot.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi", "-i", HOT.format(d=8),
                    "-af", HOT_AF, "-c:a", "pcm_f32le", str(wav)], check=True)
    before = _measure(wav)
    assert before["max"] > -0.2 and before["tp"] > 1.0, before
    out = tmp_path / "limited.m4a"
    for br in ("192k", "128k"):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-af", limiter.audio_filter(),
                        "-c:a", "aac", "-b:a", br, str(out)], check=True)
        after = _measure(out)
        assert after["tp"] <= limiter.CEILING_DBTP + 1e-6 and after["max"] <= limiter.CEILING_DBTP, (br, after)
        assert abs(after["mean"] - before["mean"]) < 1.0 and abs(after["lufs"] - before["lufs"]) < 1.0, \
            (before, after)


def test_a_film_too_loud_for_one_pass_is_limited_again_until_it_is_inside(tmp_path):
    """The guarantee behind the limiter: a dense, hard-clipped film whose AAC encode overshoots
    the limiter's output further than the 1 dB it leaves is limited again, harder."""
    sys.path.insert(0, str(ROOT / "research" / "tools"))
    import assemble
    film = tmp_path / "loud.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi", "-i", "color=c=gray:s=160x90:r=24:d=5",
         "-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.6:duration=5:seed=2",
         "-af", "volume=14dB,alimiter=limit=1:level=0", "-c:v", "libx264", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-t", "5", str(film)], check=True)
    # the film as the join would have written it: through the chain once, AAC at 128k
    once = tmp_path / "once.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(film), "-c:v", "copy", "-af",
                    limiter.audio_filter(), "-c:a", "aac", "-b:a", "128k", str(once)], check=True)
    before = _measure(once)
    video = {"audio_bitrate": "128k", "timescale": 24000}
    said = assemble.enforce_ceiling(once, video)
    after = _measure(once)
    assert after["tp"] <= limiter.CEILING_DBTP and after["max"] <= limiter.CEILING_DBTP, (before, after, said)
    if before["tp"] > limiter.CEILING_DBTP:
        assert any("limited again" in line for line in said), said
    assert once.exists() and assemble.true_peak_db(once) == after["tp"]


def test_a_quiet_film_is_not_touched_by_the_limiter(tmp_path):
    wav = tmp_path / "quiet.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=500:duration=3",
                    "-af", "volume=-10dB", "-ar", "48000", "-c:a", "pcm_s16le", str(wav)], check=True)
    out = tmp_path / "q.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-af", limiter.audio_filter(),
                    "-c:a", "pcm_s16le", str(out)], check=True)
    a, b = _measure(wav), _measure(out)
    assert abs(a["max"] - b["max"]) < 0.15 and abs(a["lufs"] - b["lufs"]) < 0.15


@pytest.fixture(scope="module")
def hot_bin(tmp_path_factory):
    root = tmp_path_factory.mktemp("hot")
    footage, assets = root / "bin", root / "assets"
    (assets / "music").mkdir(parents=True)
    footage.mkdir()
    _hot_clip(footage / "HOT.MP4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "anoisesrc=color=brown:amplitude=0.6:duration=6:seed=5", "-c:a", "pcm_s16le",
                    "-ar", "48000", str(assets / "music" / "bed.wav")], check=True)
    # the sidecar's loudness is what the levelling lifts the shot by (about 4 dB here: past full scale)
    sc = root / "sidecars"
    sc.mkdir()
    side = _sidecar("HOT")
    side["summary"]["integrated_lufs"] = -20.0
    side["duration_s"] = 4.0
    (sc / "HOT.audio.json").write_text(json.dumps(side), encoding="utf-8")
    return {"root": root, "footage": footage, "assets": assets, "sidecars": sc}


def _hot_render(hot, name: str, profile: str, **edl_extra) -> Path:
    edl = {"variant": "H", "title": "hot", "orient": "none", "story": "", "target_s": [1, 9],
           "segments": [{"clip": "HOT.MP4", "in": 0.0, "out": 3.5}], **edl_extra}
    d = hot["root"] / name
    d.mkdir()
    (d / "edl.json").write_text(json.dumps(edl), encoding="utf-8")
    out = d / "film.mp4"
    r = subprocess.run(
        ["uv", "run", "--quiet", str(ROOT / "research" / "tools" / "assemble.py"), str(d / "edl.json"),
         "--footage", str(hot["footage"]), "--sidecars", str(hot["sidecars"]),
         "--assets", str(hot["assets"]), "--profile", profile, "--size", "320x180", "--jobs", "1",
         "-o", str(out)], capture_output=True, text=True, env={**os.environ, "ROUGHCUT_GPU": "0"})
    assert r.returncode == 0, r.stdout[-1200:] + r.stderr[-1200:]
    return out


@pytest.mark.parametrize("profile", ["preview", "delivery", "youtube", "match"])
def test_every_profile_ends_in_the_limiter(hot_bin, profile):
    m = _measure(_hot_render(hot_bin, f"p_{profile}", profile))
    assert m["tp"] <= -1.0 and m["max"] <= -1.0, (profile, m)


def test_a_re_laid_a1_and_a_music_bed_are_limited_too(hot_bin):
    # A1 re-laid with +9 dB of track gain: the sound pass writes the film's sound
    loud = _measure(_hot_render(hot_bin, "tracks", "preview",
                                tracks={"A1": {"gain_db": 9.0}}))
    assert loud["tp"] <= -1.0 and loud["max"] <= -1.0, loud
    # a music bed mixed under it: the bed's pass is the last stage
    bed = _measure(_hot_render(hot_bin, "bed", "preview",
                               effects_music={"asset": "music/bed.wav", "gain_db": 0.0, "duck": False}))
    assert bed["tp"] <= -1.0 and bed["max"] <= -1.0, bed


# ---------------------------------------------------------------------- the panels, in Chromium

@pytest.fixture
def served(rbin, tmp_path):
    import socket
    import threading
    import uvicorn
    import server
    _open(server, rbin, tmp_path / "work")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started
    yield {"url": f"http://127.0.0.1:{port}", "server": server}
    srv.should_exit = True
    th.join(timeout=10)
    server.STATE.pop("orient_map", None)


def test_the_project_and_index_panels_say_which_way_up_and_take_the_editors_word(served):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    import httpx
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(served["url"] + "/")
        pg.wait_for_function("window.rcIndex && window.dock && window.tl", timeout=30000)
        # the Project panel: a flag the sensor overruled is said on its row, one that is obeyed
        # is a quiet glyph, an unflagged clip says nothing
        pg.wait_for_selector('#projList [data-clip="SENS90.MP4"] .rot', timeout=15000)
        ign = pg.locator('#projList [data-clip="SENS90.MP4"] .rot')
        assert "shot upright" in ign.first.get_attribute("title")
        # the clip's name is never replaced: a glyph after it, the sentence in its hover
        name = pg.locator('#projList [data-clip="SENS90.MP4"] .nm').first
        assert name.inner_text().strip() == "SENS90.MP4"
        assert ign.first.inner_text().strip() in ("⟲", "↻") and "flag ignored" not in pg.locator(
            '#projList [data-clip="SENS90.MP4"]').first.inner_text()
        assert "upside down" in pg.locator('#projList [data-clip="INV180.MP4"] .rot').first.get_attribute("title")
        assert pg.locator('#projList [data-clip="FLAT.MP4"] .rot').count() == 0

        # the Index panel: the rotation line of the focused clip, and its three choices
        pg.locator("#menubar .mtop", has_text="Window").click()
        pg.locator('#menubar button[data-cmd="w:index"]').click()
        pg.wait_for_selector('[data-testid="index-panel"]', state="visible", timeout=5000)
        pg.wait_for_function("document.querySelectorAll('[data-testid=index-row]').length === 5",
                             timeout=15000)
        pg.locator('[data-testid="index-row"][data-clip="INV180.MP4"]').click()
        line = pg.locator('[data-testid="index-rotation"]')
        line.wait_for(timeout=5000)
        assert "upside down" in line.inner_text()
        sel = line.locator("select")
        assert sel.input_value() == "sensor"
        sel.select_option("none")                     # the editor disagrees
        pg.wait_for_function(
            "() => document.querySelector('[data-testid=index-rotation]')"
            " && document.querySelector('[data-testid=index-rotation]').innerText.includes('you set this by hand')",
            timeout=10000)
        api = {r["stem"]: r for r in httpx.get(served["url"] + "/api/clips", timeout=20).json()["clips"]}
        assert api["INV180"]["orient"]["mode"] == "none" and api["INV180"]["orient"]["hand"] == "none"
        assert api["SENS90"]["orient"]["mode"] == "none"        # nothing else moved
        assert errors == [], errors
        b.close()


# ----------------------------------------------------------------- the cold start's first clip

def test_a_decision_that_moves_while_a_proxy_is_encoding_never_leaves_a_turned_proxy(rbin, tmp_path, monkeypatch):
    """The ordering the first cold start hit: the proxy build began under the flag (no sensor
    yet), the telemetry stage decided 'ignore the flag' while ffmpeg ran, and the build
    finished AFTER the decision and wrote a turned proxy over the cleared one. The result of a
    build that finished under another rule than holds now is thrown away and made again."""
    import server
    work = tmp_path / "work"
    _open(server, rbin, work, telemetry={})
    src = rbin["footage"] / "SENS90.MP4"
    real = server.build_proxy
    seen = []

    def racing(s_, dest, mode):
        seen.append(mode)
        if len(seen) == 1:                       # the telemetry stage lands while ffmpeg runs
            _telemetry(server.telemetry_dir(), "SENS90", CASES["SENS90"][1])
            assert server.refresh_orient(["SENS90.MP4"], rebuild=False) == ["SENS90.MP4"]
        return real(s_, dest, mode)

    monkeypatch.setattr(server, "build_proxy", racing)
    server.ensure_proxies(["SENS90.MP4"])
    assert seen == ["auto", "none"], seen        # built under the flag, found stale, built again
    _same(_quads(_proxy(server, "SENS90")), _stored(src), "the proxy after the race")
    assert server.orient_record("SENS90")["built"] == "none"


def test_a_wrong_picture_on_disk_is_found_and_rebuilt_at_open_and_when_the_telemetry_lands(rbin, tmp_path):
    """A project already hit: the record says its pictures were made under 'none', but the
    proxy on disk is the turned one. Opening (or the telemetry stage) compares the disk with
    the decision and makes it again, with nobody asking."""
    import server
    work = tmp_path / "work"
    _open(server, rbin, work, telemetry={})
    server.ensure_proxies(["SENS90.MP4", "INV180.MP4"])           # made under the flag
    src = rbin["footage"] / "SENS90.MP4"
    _same(_quads(_proxy(server, "SENS90")), _upright(src), "hit: turned")
    _telemetry(server.telemetry_dir(), "SENS90", CASES["SENS90"][1])
    _telemetry(server.telemetry_dir(), "INV180", CASES["INV180"][1])
    # the record as the cold start left it: decided 'none', built 'none', proxy turned
    server.STATE.pop("orient_map", None)
    (work / "orient").mkdir(exist_ok=True)
    (work / "orient" / f"{rbin['footage'].name}.json").write_text(json.dumps({"v": 1, "clips": {
        "SENS90": {"mode": "none", "why": "sensor", "flag": 90, "expect": 0, "sensor": "upright",
                   "hand": None, "built": "none", "rev": 1}}}))
    keep = _proxy(server, "INV180").stat().st_mtime_ns
    changed = server.refresh_orient(["SENS90.MP4", "INV180.MP4"], rebuild=False)
    assert changed == ["SENS90.MP4"], changed
    assert not _proxy(server, "SENS90").exists()
    assert _proxy(server, "INV180").stat().st_mtime_ns == keep
    assert server.poster_url("SENS90") == "/media/poster/SENS90.v2r2.jpg"    # a new immutable URL
    server.ensure_proxies(["SENS90.MP4"])
    _same(_quads(_proxy(server, "SENS90")), _stored(src), "repaired")
    assert server.refresh_orient(rebuild=False) == [], "and then it is left alone"


def test_a_flagged_clips_sensor_is_read_before_its_first_picture_is_made(rbin, tmp_path, monkeypatch):
    import server
    work = tmp_path / "work"
    _open(server, rbin, work, telemetry={})
    calls = []

    def fake_tool(cmd):                                       # the telemetry tool, for one clip
        stem = next(x for x in CASES if x in " ".join(cmd))
        calls.append(stem)
        if CASES[stem][1] is not None:
            _telemetry(server.telemetry_dir(), stem, CASES[stem][1])
        return ""

    monkeypatch.setattr(server, "_run_tool", fake_tool)
    monkeypatch.setattr(server, "has_telemetry", lambda clip: True)
    # a clip list asked first decides nothing and stores nothing: "pending", the flag honoured
    assert "SENS90" not in server._orient_map() and calls == []
    # the first picture's rule is asked: the sensor is read first, so the very first proxy is right
    assert server.clip_orient("SENS90.MP4") == "none"
    assert calls == ["SENS90"]
    server.ensure_proxies(["SENS90.MP4", "FLAT.MP4"])
    _same(_quads(_proxy(server, "SENS90")), _stored(rbin["footage"] / "SENS90.MP4"), "first proxy")
    assert calls == ["SENS90"], "an unflagged clip is not read early, and a clip is read once"
    # a flagged clip whose read gives nothing keeps its flag
    assert server.clip_orient("NOTEL90.MP4") == "auto" and calls == ["SENS90", "NOTEL90"]


def test_rotate_clip_by_hand_follows_every_picture_and_the_export(board, tmp_path):
    server, client = board["server"], board["client"]
    src = board["footage"] / "FLAT.MP4"
    server.ensure_proxies(["FLAT.MP4", "INV180.MP4"])
    other = _proxy(server, "INV180").stat().st_mtime_ns
    for word, deg in (("left", 90), ("right", 270), ("turn180", 180)):
        r = client.post("/api/clips/FLAT.MP4/orient", json={"mode": word}).json()
        assert r["changed"] and r["orient"]["mode"] == f"turn{deg}" and r["orient"]["hand"] == f"turn{deg}"
        assert r["orient"]["text"].startswith("Turned") and "by you" in r["orient"]["text"]
        server.ensure_proxies(["FLAT.MP4"])
        ref = _quads(src, pre=("-display_rotation", str(deg)))
        _same(_quads(_proxy(server, "FLAT")), ref, f"proxy turned {deg}")
        dest = tmp_path / f"flat{deg}.jpg"
        server.build_poster(src, dest, 0.5, original=True)
        _same(_quads(dest), ref, f"poster turned {deg}")
        prev = tmp_path / f"p{deg}.mp4"
        server.build_preview(src, prev, server.clip_orient("FLAT.MP4"))
        _same(_quads(prev), ref, f"preview turned {deg}")
        assert not server.browser_playable(src)
    assert _proxy(server, "INV180").stat().st_mtime_ns == other, "no other clip is touched"
    # the export: the decisions file carries the turn and assemble applies it (180 keeps the aspect)
    sys.path.insert(0, str(ROOT / "research" / "tools"))
    import assemble
    args = server.orient_args()
    assert assemble.load_orient_clips(Path(args[1]))["FLAT"] == "turn180"
    (tmp_path / "edl.json").write_text(json.dumps({
        "variant": "T", "title": "t", "orient": "auto", "story": "", "target_s": [1, 9],
        "segments": [{"clip": "FLAT.MP4", "in": 0.2, "out": 1.6}]}))
    out = tmp_path / "turned.mp4"
    r = subprocess.run(["uv", "run", "--quiet", str(ROOT / "research" / "tools" / "assemble.py"),
                        str(tmp_path / "edl.json"), "--footage", str(board["footage"]), "--profile", "preview",
                        "--size", "320x192", "--jobs", "1", "-o", str(out), *args],
                       capture_output=True, text=True, env={**os.environ, "ROUGHCUT_GPU": "0"})
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-800:]
    _same(_quads(out, t=0.7), _quads(src, pre=("-display_rotation", "180")), "export, turned 180 by hand")
    # as decided again: the clip is back to what its flag and sensor say, one step
    back = client.post("/api/clips/FLAT.MP4/orient", json={"mode": "sensor"}).json()
    assert back["previous"] == "turn180" and back["orient"]["mode"] == "auto"


def test_media_is_revalidated_so_a_tab_takes_a_rebuilt_file(board):
    server, client = board["server"], board["client"]
    server.ensure_proxies(["FLAT.MP4"])
    assert client.get("/media/proxy/FLAT.mp4").headers["cache-control"] == "no-cache"
    assert client.get("/media/proxy/FLAT.mp4", headers={"range": "bytes=0-99"}).headers["cache-control"] == "no-cache"
