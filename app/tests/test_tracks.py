"""The sequence's tracks: V1/V2 + A1/A2, linked A/V and the track headers (REDESIGN-GOAL
dimension 5, verdict 2 punch 3; roughcut/tracks.py).

Three layers, each judged on what it produces rather than on its own word:

  * the API — GET/PUT /api/project carry `tracks`, `overlays` (V2), `markers`, `in`,
    `out` and the sequence `fps`; an old single-track EDL loads as V1 + linked A1;
    every bad value is a 400 that writes nothing.
  * the render — assemble.py over the suite's synthetic clips (440 Hz in every clip):
    A1 muted is a silent film (volumedetect), an unlinked A1 offset moves the sound
    (where the first second goes quiet), V2 composites over V1 (the pixels change in
    its range and only there).
  * the board — a real browser: a clip in makes a V1 block and its linked A1 block, C
    cuts both, Alt-click unlinks, A1 then drags alone, the headers toggle and save, and
    at 1440×900 the track rows fill the Timeline panel.
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
TOOLS = Path(__file__).resolve().parents[2] / "research" / "tools"

from roughcut import tracks as tracksmod  # noqa: E402

OLD_EDL = {
    "variant": "T", "title": "test cut", "orient": "none", "story": "",
    "target_s": [5, 20],
    "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
                 {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}],
}


# ------------------------------------------------------------------ the model, pure

def test_the_model_defaults_and_validation():
    assert tracksmod.validate_tracks(None) == {}
    # only differences are stored; a full default round-trips to nothing
    assert tracksmod.validate_tracks(tracksmod.default_tracks()) == {}
    assert tracksmod.validate_tracks({"A1": {"mute": True, "gain_db": -6.04}}) == {
        "A1": {"mute": True, "gain_db": -6.0}}
    for bad in ({"A10": {}}, {"A1": {"loud": True}}, {"A1": {"mute": "yes"}},
                {"A1": {"gain_db": 40}}, "x"):
        with pytest.raises(ValueError):
            tracksmod.validate_tracks(bad)
    full = tracksmod.resolve_tracks({"A2": {"solo": True}})
    assert full["A2"]["solo"] and not full["A1"]["solo"] and full["V1"]["eye"]
    # solo: every audio track not soloed is silent
    assert not tracksmod.audible({"A2": {"solo": True}}, "A1")
    assert tracksmod.audible({"A2": {"solo": True}}, "A2")
    assert not tracksmod.audible({"A1": {"mute": True}}, "A1")


def test_a1_plan_is_only_needed_when_a1_differs_from_the_parts():
    dur = lambda s: s["out"] - s["in"]   # noqa: E731
    segs = [{"in": 0, "out": 2}, {"in": 0, "out": 3}]
    assert not tracksmod.a1_plan(segs, {}, dur)["needed"]
    p = tracksmod.a1_plan([{"in": 0, "out": 2, "a_off": -0.5}, {"in": 0, "out": 3, "a_off": 1}], {}, dur)
    assert p["needed"] and p["placements"] == [(0.0, 0.5), (3.0, 0.0)] and p["total"] == 5
    assert tracksmod.a1_plan(segs, {"A1": {"mute": True}}, dur)["needed"]
    assert tracksmod.a1_plan(segs, {"A1": {"gain_db": -3}}, dur)["needed"]


# ------------------------------------------------------------------ the API

def test_an_old_edl_loads_as_v1_with_linked_a1(client, project):
    Path(project["edl"]).write_text(json.dumps(OLD_EDL), encoding="utf-8")
    p = client.get("/api/project").json()
    assert set(p["tracks"]) == {"V1", "V2", "A1", "A2"}
    assert p["tracks"]["V1"]["target"] and p["tracks"]["A1"]["target"]
    assert not p["tracks"]["A1"]["mute"] and p["tracks"]["A1"]["gain_db"] == 0
    assert p["overlays"] == [] and p["markers"] == [] and p["in"] is None and p["out"] is None
    # every shot is linked and in sync: no link / a_off keys at all
    assert all("link" not in s and "a_off" not in s for s in p["segments"])
    # the sequence rate is the clips' own (the fixture's clips are 24 fps)
    assert p["fps"] == 24.0


def test_tracks_overlays_markers_in_out_round_trip(client, project):
    Path(project["edl"]).write_text(json.dumps(OLD_EDL), encoding="utf-8")
    segs = client.get("/api/project").json()["segments"]
    segs[1]["link"] = False
    segs[1]["a_off"] = 0.5
    body = {"segments": segs,
            "tracks": {"A1": {"mute": True, "gain_db": -3}, "V2": {"lock": True}},
            "overlays": [{"clip": "CLIP_C.MP4", "in": 1, "out": 2.5, "at": 0.5}],
            "markers": [1.5, 0.25, 1.5], "in": 0.5, "out": 3.25}
    r = client.put("/api/project", json=body)
    assert r.status_code == 200, r.text
    p = client.get("/api/project").json()
    assert p["tracks"]["A1"]["mute"] is True and p["tracks"]["A1"]["gain_db"] == -3
    assert p["tracks"]["V2"]["lock"] is True and p["tracks"]["V1"]["eye"] is True
    assert p["segments"][1]["link"] is False and p["segments"][1]["a_off"] == 0.5
    assert "link" not in p["segments"][0]
    assert len(p["overlays"]) == 1 and p["overlays"][0]["at"] == 0.5
    assert re.match(r"^g[0-9a-f]{10}$", p["overlays"][0]["id"])
    assert p["markers"] == [0.25, 1.5] and p["in"] == 0.5 and p["out"] == 3.25
    disk = json.loads(Path(project["edl"]).read_text(encoding="utf-8"))
    assert disk["tracks"] == {"A1": {"mute": True, "gain_db": -3.0}, "V2": {"lock": True}}

    # a save with only the tracks leaves the cut alone; null clears the rest
    before = client.get("/api/project").json()["segments"]
    r = client.put("/api/project", json={"tracks": {}, "overlays": None, "markers": [],
                                         "in": None, "out": None})
    assert r.status_code == 200, r.text
    p = client.get("/api/project").json()
    assert p["segments"] == before
    assert not p["tracks"]["A1"]["mute"] and p["overlays"] == [] and p["markers"] == []
    assert p["in"] is None and p["out"] is None
    disk = json.loads(Path(project["edl"]).read_text(encoding="utf-8"))
    assert not {"tracks", "overlays", "markers", "in", "out"} & set(disk)


@pytest.mark.parametrize("bad", [
    {"tracks": {"A10": {}}},
    {"tracks": {"A1": {"gain_db": 99}}},
    {"overlays": [{"clip": "CLIP_C.MP4", "in": 1, "out": 1.05, "at": 0}]},
    {"overlays": [{"clip": "../etc", "in": 1, "out": 2, "at": 0}]},
    {"markers": [-1]},
    {"in": 3, "out": 2},
    {"segments": [{"clip": "CLIP_A.MP4", "in": 1, "out": 3, "a_off": "soon"}]},
    {"segments": [{"clip": "CLIP_A.MP4", "in": 1, "out": 3, "link": "no"}]},
])
def test_a_bad_track_value_is_a_400_and_writes_nothing(client, project, bad):
    Path(project["edl"]).write_text(json.dumps(OLD_EDL), encoding="utf-8")
    before = Path(project["edl"]).read_text(encoding="utf-8")
    r = client.put("/api/project", json=bad)
    assert r.status_code == 400, r.text
    assert Path(project["edl"]).read_text(encoding="utf-8") == before


# ------------------------------------------------------------------ the render

def _render(project, edl: dict, root: Path, name: str) -> tuple[Path, str]:
    import server
    edl_path = root / f"{name}.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    out = root / f"{name}.mp4"
    cmd = ["uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(edl_path),
           "--footage", str(project["footage"]), "--sidecars", str(project["sidecars"]),
           "--assets", str(project["assets"]),
           "--colour-dir", str(server.colour_dir()),
           "--generated-dir", str(server.generated_dir()), "-o", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return out, r.stdout


def _volume(path: Path, start: float | None = None, length: float | None = None) -> dict:
    cmd = ["ffmpeg", "-v", "info", "-nostdin"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(path)]
    if length is not None:
        cmd += ["-t", f"{length:.3f}"]
    cmd += ["-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    mean = re.search(r"mean_volume: (-?[\d.]+|-inf) dB", r.stderr)
    peak = re.search(r"max_volume: (-?[\d.]+|-inf) dB", r.stderr)
    assert mean and peak, r.stderr[-400:]
    f = lambda m: -999.0 if m.group(1) == "-inf" else float(m.group(1))   # noqa: E731
    return {"mean": f(mean), "max": f(peak)}


def _duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return float(r.stdout.strip())


def _luma(path: Path, t: float) -> list[float]:
    """Mean Y of one frame at film time t, scaled small."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{t:.3f}", "-i", str(path),
                        "-frames:v", "1", "-vf", "scale=64:36", "-f", "rawvideo",
                        "-pix_fmt", "gray", "-"], capture_output=True)
    assert r.returncode == 0 and len(r.stdout) == 64 * 36, r.stderr[-300:]
    return list(r.stdout)


@pytest.fixture(scope="module")
def renders(project, tmp_path_factory):
    import server
    server.configure(project["edl"], project["footage"], project["sidecars"],
                     project["work"], proxies=False,
                     visual=project["work"] / "no-visual", assets=project["assets"])
    root = tmp_path_factory.mktemp("tracksrender")
    server.STATE["generated_dir"] = root / "generated"
    black = server.materialise_generated({"kind": "black", "seconds": 2.0, "color": None,
                                          "from_clip": None, "at": None})
    base = {"variant": "T", "title": "tracks", "orient": "none", "target_s": [1, 20],
            "segments": [{"id": "s1", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0},
                         {"id": "s2", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0}]}
    out = {}
    out["plain"] = _render(project, base, root, "plain")
    out["muted"] = _render(project, {**base, "tracks": {"A1": {"mute": True}}}, root, "muted")
    # the first shot's sound unlinked and moved 1.5 s later: the film's first 1.5 s is quiet
    moved = json.loads(json.dumps(base))
    moved["segments"][0].update(link=False, a_off=1.5)
    out["offset"] = _render(project, moved, root, "offset")
    # V2: a black slide over 1.0–2.0 s of the film
    out["v2"] = _render(project, {**base, "overlays": [
        {"id": "o1", "clip": black.name, "in": 0.0, "out": 1.0, "at": 1.0}]}, root, "v2")
    yield out
    server.STATE.pop("generated_dir", None)
    server.STATE.pop("gen_durations", None)


def test_mute_a1_makes_the_render_silent(renders):
    plain = _volume(renders["plain"][0])
    muted = _volume(renders["muted"][0])
    assert plain["max"] > -20, plain                 # the clips' 440 Hz is there
    assert muted["max"] < -80, muted                 # and A1 muted is silence
    assert "A1 muted" in renders["muted"][1]
    assert abs(_duration(renders["muted"][0]) - 4.0) < 0.1


def test_an_unlinked_a1_offset_moves_the_sound(renders):
    film, log = renders["offset"]
    head = _volume(film, 0.0, 1.3)
    later = _volume(film, 1.7, 1.0)
    assert head["max"] < -60, head                   # nothing there before the moved sound
    assert later["max"] > -20, later
    assert "A1 rebuilt" in log
    plain_head = _volume(renders["plain"][0], 0.0, 1.3)
    assert plain_head["max"] > -20, plain_head
    assert abs(_duration(film) - 4.0) < 0.1          # the film's length is V1's


def test_v2_composites_over_v1_in_its_range_only(renders):
    film, log = renders["v2"]
    plain = renders["plain"][0]
    inside = _luma(film, 1.5)
    assert sum(inside) / len(inside) < 25, sum(inside) / len(inside)   # the black slide
    for t in (0.4, 2.6):
        a, b = _luma(film, t), _luma(plain, t)
        diff = sum(abs(x - y) for x, y in zip(a, b)) / len(a)
        assert diff < 6, (t, diff)                   # V1 untouched outside V2's range
    assert "V2 00" in log
    assert abs(_duration(film) - 4.0) < 0.1
    assert _volume(film)["max"] > -20                # V2 is picture only: A1 still plays


# ------------------------------------------------------------------ the board

playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(project):
    import uvicorn
    import server

    original = project["edl"].read_text(encoding="utf-8")
    server.configure(project["edl"], project["footage"], project["sidecars"],
                     project["work"], proxies=False,
                     visual=project["work"] / "tracks-visual", assets=project["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in project["stems"]])
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started, "server did not start"
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=10)
    project["edl"].write_text(original, encoding="utf-8")


def _open(pw, live_server, project, edl: dict, size=(1280, 900)):
    Path(project["edl"]).write_text(json.dumps(edl, indent=1), encoding="utf-8")
    browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    pg = browser.new_page(viewport={"width": size[0], "height": size[1]})
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(live_server)
    pg.wait_for_selector("#tl .blk")
    return browser, pg, errors


@pytest.fixture
def page(live_server, project):
    with sync_playwright() as pw:
        browser, pg, errors = _open(pw, live_server, project, OLD_EDL)
        yield pg
        assert not errors, errors
        browser.close()


def wait_saved(page) -> None:
    page.wait_for_function(
        "document.querySelector('#saveState').textContent.startsWith('saved')", timeout=8000)


def on_disk(project) -> dict:
    return json.loads(Path(project["edl"]).read_text(encoding="utf-8"))


def a1_blocks(page) -> list[dict]:
    return page.evaluate("""[...document.querySelectorAll('#tl .tl-track[data-track=A1] .ablk')]
        .map(b => ({id: b.dataset.id, left: parseFloat(b.style.left), width: parseFloat(b.style.width),
                    linked: !b.classList.contains('unlinked')}))""")


def v1_blocks(page) -> list[dict]:
    return page.evaluate("""[...document.querySelectorAll('#tl .tl-lane[data-lane=V1] .blk')]
        .map(b => ({id: b.dataset.id, left: parseFloat(b.style.left), width: parseFloat(b.style.width)}))""")


def test_every_v1_shot_has_its_linked_a1_clip_and_a_clip_name(page):
    v1, a1 = v1_blocks(page), a1_blocks(page)
    assert len(v1) == 2 and [b["id"] for b in a1] == [b["id"] for b in v1]
    for v, a in zip(v1, a1):
        assert abs(v["left"] - a["left"]) < 0.5 and abs(v["width"] - a["width"]) < 0.5 and a["linked"]
    # blocks are labelled with the clip name; the transcript is the hover
    names = page.evaluate("[...document.querySelectorAll('#tl .tl-lane[data-lane=V1] .blk .name')].map(n => n.textContent)")
    assert names == ["CLIP_A", "CLIP_B"]
    title = page.evaluate("document.querySelector('#tl .tl-lane[data-lane=V1] .blk').title")
    assert "hello there" in title
    # the headers: V2 V1 A1 A2, each with its toggles
    heads = page.evaluate("[...document.querySelectorAll('#tl .tl-thead')].map(h => h.dataset.track)")
    assert heads == ["V2", "V1", "A1", "A2"]
    for t, kinds in (("V1", ["target", "lock", "eye"]), ("A1", ["target", "lock", "mute", "solo"])):
        got = page.evaluate(f"[...document.querySelectorAll('#tl .tl-thead[data-track={t}] button')].map(b => b.dataset.toggle)")
        assert set(kinds) <= set(got), (t, got)


def test_adding_a_clip_makes_v1_and_a1_blocks(page):
    page.evaluate("tl.insertAt({clip: 'CLIP_C.MP4', in: 0.5, out: 2.0, why: 'third'}, tl.total())")
    page.wait_for_function("document.querySelectorAll('#tl .tl-track[data-track=A1] .ablk').length === 3")
    v1, a1 = v1_blocks(page), a1_blocks(page)
    assert [b["id"] for b in a1] == [b["id"] for b in v1]
    assert abs(v1[2]["left"] - a1[2]["left"]) < 0.5


def test_add_edit_cuts_both_halves(page, project):
    page.evaluate("tl.seek(1.0)")
    page.click("#tl .tl-ruler", position={"x": 2, "y": 2})   # focus the timeline
    page.evaluate("tl.seek(1.0); tl.select([])")
    page.keyboard.press("Control+k")                 # C only picks the razor (wave 6)
    page.wait_for_function("segs.length === 3")
    v1, a1 = v1_blocks(page), a1_blocks(page)
    assert len(a1) == 3 and [b["id"] for b in a1] == [b["id"] for b in v1]
    for v, a in zip(v1, a1):
        assert abs(v["left"] - a["left"]) < 0.5 and abs(v["width"] - a["width"]) < 0.5
    wait_saved(page)
    assert len(on_disk(project)["segments"]) == 3


def test_alt_click_unlinks_then_a1_moves_alone(page, project):
    first = v1_blocks(page)[0]
    a = page.locator("#tl .tl-track[data-track=A1] .ablk").first
    a.click(modifiers=["Alt"])
    page.wait_for_function("segs[0].link === false")
    assert not a1_blocks(page)[0]["linked"]
    # drag the A1 half 1 s later: V1 stays, A1 moves, one undo entry
    box = a.bounding_box()
    z = page.evaluate("tl.state.zoom")
    x0, y0 = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x0, y0)
    page.mouse.down()
    for k in range(1, 11):
        page.mouse.move(x0 + z * 1.0 * k / 10, y0)
    page.mouse.up()
    page.wait_for_function("Math.abs((segs[0].a_off || 0) - 1.0) < 0.06")
    assert abs(v1_blocks(page)[0]["left"] - first["left"]) < 0.5
    assert abs(a1_blocks(page)[0]["left"] - (first["left"] + z)) < z * 0.06 + 1
    wait_saved(page)
    seg = on_disk(project)["segments"][0]
    assert seg["link"] is False and abs(seg["a_off"] - 1.0) < 0.06
    # undo puts the sound back in sync; Alt-click again relinks (the offset kept)
    page.evaluate("tl.undo()")
    page.wait_for_function("!segs[0].a_off")
    page.locator("#tl .tl-lane[data-lane=V1] .blk").first.click(modifiers=["Alt"])
    page.wait_for_function("segs[0].link !== false")


def test_a_linked_a1_drag_moves_the_pair(page):
    before = [s for s in page.evaluate("segs.map(s => s.id)")]
    a = page.locator("#tl .tl-track[data-track=A1] .ablk").first
    box = a.bounding_box()
    end = page.evaluate("(() => { const v = document.querySelector('#tl .tl-view');"
                        " return v.getBoundingClientRect().left + tl.timeToX(tl.total()) - v.scrollLeft; })()")
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    for k in range(1, 11):
        page.mouse.move(box["x"] + box["width"] / 2 + (end - box["x"] - box["width"] / 2) * k / 10 + 3,
                        box["y"] + box["height"] / 2)
    page.mouse.up()
    page.wait_for_function(f"segs[0].id === {json.dumps(before[1])}")
    assert page.evaluate("segs.map(s => s.id)") == [before[1], before[0]]


def test_headers_toggle_save_and_lock_refuses_edits(page, project):
    page.click("#tl .tl-thead[data-track=A1] button[data-toggle=mute]")
    page.wait_for_function("tl.tracks().A1.mute === true")
    page.wait_for_function("document.querySelector('#tl .tl-track[data-track=A1]').classList.contains('muted')")
    deadline = time.time() + 8
    while time.time() < deadline and not (on_disk(project).get("tracks") or {}).get("A1", {}).get("mute"):
        time.sleep(0.1)
    assert on_disk(project)["tracks"]["A1"]["mute"] is True
    # lock V1: an edit is refused, the cut unchanged
    page.click("#tl .tl-thead[data-track=V1] button[data-toggle=lock]")
    n = page.evaluate("segs.length")
    out = page.evaluate("tl.split(segs[0].id, 1.0)")
    assert out is None and page.evaluate("segs.length") == n
    page.click("#tl .tl-thead[data-track=V1] button[data-toggle=lock]")
    assert page.evaluate("tl.split(segs[0].id, 1.0)") is not None
    # as in Premiere, a header toggle is not an edit: ⌘Z undoes the split, not the mute
    n = page.evaluate("segs.length")
    page.evaluate("tl.undo()")
    assert page.evaluate("segs.length") == n - 1
    assert page.evaluate("tl.tracks().A1.mute") is True


def test_music_bed_is_on_a2(page):
    page.evaluate("""(() => { music = {asset: 'music/bed.wav', duck: true, duck_db: 12, fade_in: 0.5, fade_out: 0.5};
                      touch(); tl.render(); })()""")
    page.wait_for_selector("#tl .tl-track[data-track=A2] .bed", timeout=5000)
    assert page.evaluate("document.querySelectorAll('#tl .tl-track[data-track=A1] .bed').length") == 0


def test_v2_overlay_from_the_api_draws_and_saves(page, project):
    page.evaluate("tl.addOverlay({clip: 'CLIP_C.MP4', in: 1, out: 2}, 0.5)")
    page.wait_for_selector("#tl .tl-track[data-track=V2] .oblk")
    left = page.evaluate("parseFloat(document.querySelector('#tl .tl-track[data-track=V2] .oblk').style.left)")
    assert abs(left - page.evaluate("tl.timeToX(0.5)")) < 0.5
    deadline = time.time() + 8
    while time.time() < deadline and not on_disk(project).get("overlays"):
        time.sleep(0.1)
    ov = on_disk(project)["overlays"]
    assert len(ov) == 1 and ov[0]["clip"] == "CLIP_C.MP4" and ov[0]["at"] == 0.5
    page.evaluate("tl.undo()")
    page.wait_for_function("!document.querySelector('#tl .tl-track[data-track=V2] .oblk')")


def test_markers_reach_the_server(page, project):
    page.evaluate("tl.addMarker(1.0); tl.setSeqIn(0.5); tl.setSeqOut(3.0)")
    deadline = time.time() + 8
    while time.time() < deadline and on_disk(project).get("out") is None:
        time.sleep(0.1)
    d = on_disk(project)
    assert d.get("markers") == [1.0] and d.get("in") == 0.5 and d.get("out") == 3.0


def test_the_lanes_fill_the_timeline_panel_at_1440x900(live_server, project):
    with sync_playwright() as pw:
        browser, pg, errors = _open(pw, live_server, project, OLD_EDL, size=(1440, 900))
        try:
            m = pg.evaluate("""(() => {
                const panel = document.querySelector('#pTimeline').getBoundingClientRect();
                const rows = [...document.querySelectorAll('#tl .tl-track, #tl .tl-lane[data-lane=V1]')]
                  .map(r => r.getBoundingClientRect()).filter(r => r.height > 0);
                const top = Math.min(...rows.map(r => r.top)), bottom = Math.max(...rows.map(r => r.bottom));
                return {panel: panel.height, rows: rows.length, span: bottom - top,
                        sum: rows.reduce((a, r) => a + r.height, 0),
                        inside: rows.every(r => r.bottom <= panel.bottom + 0.5 && r.top >= panel.top)};
            })()""")
            assert m["rows"] == 4, m
            assert m["span"] >= 0.8 * m["panel"], m
            assert m["inside"], m
            # the headers line up with their rows
            for t in ("V2", "V1", "A1", "A2"):
                hy = pg.evaluate(f"document.querySelector('#tl .tl-thead[data-track={t}]').getBoundingClientRect().top")
                sel = "#tl .tl-lane[data-lane=V1]" if t == "V1" else f"#tl .tl-track[data-track={t}]"
                ry = pg.evaluate(f"document.querySelector('{sel}').getBoundingClientRect().top")
                assert abs(hy - ry) < 1.5, (t, hy, ry)
            assert not errors, errors
        finally:
            browser.close()
