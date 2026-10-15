"""Timeline structure (verdict 4, punch 9): what a Premiere editor expects of a sequence
before and around the clips.

  * an empty sequence draws its ruler and its V2 / V1 / A1 / A2 headers, the first action
    floating over them (the cold screenshot: `ROUGHCUT_STRUCT_SHOTS=<dir>` saves it);
  * a clip dropped at 10 s on an empty V1 lands at 10 s, a 10 s gap before it; the gap is
    selectable and a ripple delete on it closes it;
  * Sequence › Add Track / Delete Track: V3 and A3 (up to V9 / A9) — the track model
    (roughcut/tracks.py) and PUT /api/project accept them, a clip laid on V3 is an overlay
    with `track: "V3"`, a clip's sound on A3 is an `audio` entry;
  * the render: the video tracks composite bottom to top (V3 covers V2), an added audio
    track mixes with A1, and a gap is black and silent.
"""

from __future__ import annotations

import json
import os
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

SEED = {"variant": "T", "title": "test cut", "orient": "none", "story": "",
        "target_s": [5, 20],
        "segments": [{"id": "st000001", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
                     {"id": "st000002", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}]}


# ------------------------------------------------------------------ the model, pure

def test_added_tracks_in_the_model():
    # an added track's entry is its existence: kept whole, even at its defaults
    assert tracksmod.validate_tracks({"V3": {}, "A3": {"gain_db": -6}}) == {
        "V3": {"lock": False, "eye": True, "target": False},
        "A3": {"lock": False, "mute": False, "solo": False, "target": False, "gain_db": -6.0}}
    full = tracksmod.resolve_tracks({"V3": {"eye": False}})
    assert "V3" in full and full["V3"]["eye"] is False and "A3" not in full
    for bad in ({"V10": {}}, {"A3": {"eye": True}}, {"V3": {"mute": True}}):
        with pytest.raises(ValueError):
            tracksmod.validate_tracks(bad)
    # a clip on V3 is an overlay with its track; V2 is the absent default
    ovs = tracksmod.validate_overlays([{"clip": "C.MP4", "in": 0, "out": 1, "at": 0, "track": "V3"},
                                       {"clip": "C.MP4", "in": 0, "out": 1, "at": 0, "track": "V2"}])
    assert ovs[0]["track"] == "V3" and "track" not in ovs[1]
    with pytest.raises(ValueError):
        tracksmod.validate_overlays([{"clip": "C.MP4", "in": 0, "out": 1, "at": 0, "track": "A3"}])
    clips = tracksmod.validate_audio_clips([{"clip": "C.MP4", "in": 0, "out": 1, "at": 2, "track": "A3"}])
    assert clips[0]["track"] == "A3" and clips[0]["at"] == 2
    with pytest.raises(ValueError):
        tracksmod.validate_audio_clips([{"clip": "C.MP4", "in": 0, "out": 1, "at": 2, "track": "A1"}])
    # the render's order: V2 under V3, a hidden track left out; solo spans the added tracks
    trk = {"V3": {}, "V4": {"eye": False}}
    lay = tracksmod.video_layers(trk, [{"clip": "x", "in": 0, "out": 1, "at": 0, "track": "V3"},
                                       {"clip": "y", "in": 0, "out": 1, "at": 5},
                                       {"clip": "z", "in": 0, "out": 1, "at": 0, "track": "V4"},
                                       {"clip": "w", "in": 0, "out": 1, "at": 0, "track": "V5"}])
    assert [o["clip"] for o in lay] == ["y", "x"]
    assert not tracksmod.audible({"A3": {"solo": True}}, "A1")
    assert tracksmod.audible({"A3": {"solo": True}}, "A3")
    assert tracksmod.audio_layers({"A3": {"gain_db": -3}}, clips) == [(clips[0], -3.0)]
    assert tracksmod.audio_layers({}, clips) == []           # A3 not added: not mixed


# ------------------------------------------------------------------ the API

def test_added_tracks_and_a_gap_round_trip(client, project):
    import server
    Path(project["edl"]).write_text(json.dumps(SEED), encoding="utf-8")
    r = client.post("/api/gap", json={"seconds": 1.5})
    assert r.status_code == 200, r.text
    gap = r.json()
    assert gap["clip"].startswith("gen_black_") and gap["record"]["clip"] == gap["clip"]
    assert (server.generated_dir() / gap["clip"]).exists()
    segs = client.get("/api/project").json()["segments"]
    body = {"segments": [{"clip": gap["clip"], "in": 0, "out": 1.5, "gap": True}, *segs],
            "tracks": {"V3": {}, "A3": {"gain_db": -3}},
            "overlays": [{"clip": "CLIP_C.MP4", "in": 1, "out": 2, "at": 0.5, "track": "V3"}],
            "audio": [{"clip": "CLIP_B.MP4", "in": 0, "out": 1, "at": 1.0, "track": "A3"}]}
    r = client.put("/api/project", json=body)
    assert r.status_code == 200, r.text
    p = client.get("/api/project").json()
    assert p["segments"][0]["gap"] is True and "gap" not in p["segments"][1]
    assert p["tracks"]["V3"]["eye"] and p["tracks"]["A3"]["gain_db"] == -3
    assert p["overlays"][0]["track"] == "V3"
    assert p["audio"][0]["track"] == "A3" and re.match(r"^g[0-9a-f]{10}$", p["audio"][0]["id"])
    # a `gap` on a footage clip is not a gap
    client.put("/api/project", json={"segments": [{**segs[0], "gap": True}]})
    assert "gap" not in client.get("/api/project").json()["segments"][0]
    for bad in ({"tracks": {"V10": {}}}, {"audio": [{"clip": "CLIP_B.MP4", "in": 0, "out": 1, "at": 0}]},
                {"overlays": [{"clip": "CLIP_C.MP4", "in": 1, "out": 2, "at": 0, "track": "A3"}]}):
        assert client.put("/api/project", json=bad).status_code == 400, bad
    assert client.post("/api/gap", json={"seconds": "soon"}).status_code == 400
    assert client.post("/api/gap", json={"seconds": 0.01}).status_code == 400
    Path(project["edl"]).write_text(json.dumps(SEED), encoding="utf-8")


# ------------------------------------------------------------------ the render

def _luma(path: Path, t: float) -> float:
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{t:.3f}", "-i", str(path),
                        "-frames:v", "1", "-vf", "scale=64:36", "-f", "rawvideo",
                        "-pix_fmt", "gray", "-"], capture_output=True)
    assert r.returncode == 0 and len(r.stdout) == 64 * 36, r.stderr[-300:]
    return sum(r.stdout) / len(r.stdout)


def _peak(path: Path, start: float, length: float) -> float:
    r = subprocess.run(["ffmpeg", "-v", "info", "-nostdin", "-ss", f"{start:.3f}", "-i", str(path),
                        "-t", f"{length:.3f}", "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"max_volume: (-?[\d.]+|-inf) dB", r.stderr)
    assert m, r.stderr[-300:]
    return -999.0 if m.group(1) == "-inf" else float(m.group(1))


def test_the_render_stacks_video_tracks_mixes_added_audio_and_plays_a_gap_black(project, tmp_path):
    import server
    server.configure(project["edl"], project["footage"], project["sidecars"],
                     project["work"], proxies=False,
                     visual=project["work"] / "no-visual", assets=project["assets"])
    gen = tmp_path / "generated"
    server.STATE["generated_dir"] = gen
    try:
        gap = server.materialise_generated({"kind": "black", "seconds": 1.0, "color": None,
                                            "from_clip": None, "at": None})
        black = server.materialise_generated({"kind": "black", "seconds": 1.5, "color": None,
                                              "from_clip": None, "at": None})
        edl = {"variant": "T", "title": "structure", "orient": "none", "target_s": [1, 20],
               "segments": [{"id": "g1", "clip": gap.name, "in": 0.0, "out": 1.0, "gap": True},
                            {"id": "s1", "clip": "CLIP_A.MP4", "in": 1.0, "out": 4.0}],
               # A1 muted: the only sound is A3's
               "tracks": {"A1": {"mute": True}, "V3": {}, "A3": {}},
               # V2 a bright clip over 1.5–2.5 s, V3 black over the same: V3 is on top
               "overlays": [{"id": "o3", "clip": black.name, "in": 0.0, "out": 1.0, "at": 1.5,
                             "track": "V3"},
                            {"id": "o2", "clip": "CLIP_C.MP4", "in": 1.0, "out": 2.0, "at": 1.5}],
               "audio": [{"id": "x1", "clip": "CLIP_B.MP4", "in": 0.0, "out": 0.8, "at": 2.6,
                          "track": "A3"}]}
        edl_path = tmp_path / "edl.json"
        edl_path.write_text(json.dumps(edl), encoding="utf-8")
        out = tmp_path / "film.mp4"
        r = subprocess.run(["uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(edl_path),
                            "--footage", str(project["footage"]), "--sidecars", str(project["sidecars"]),
                            "--assets", str(project["assets"]), "--colour-dir", str(server.colour_dir()),
                            "--generated-dir", str(gen), "-o", str(out)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        log = r.stdout
        assert "V3 00" in log or "V3 01" in log, log
        assert "A3 00 CLIP_B.MP4" in log, log
        assert _luma(out, 0.5) < 25                      # the gap: black
        assert _luma(out, 1.2) > 60                      # CLIP_A, after the gap
        assert _luma(out, 2.0) < 25                      # V3's black covers V2's bright clip
        assert _luma(out, 3.0) > 60                      # both overlays over: V1 again
        assert _peak(out, 0.0, 2.4) < -60                # A1 muted, the gap silent
        assert _peak(out, 2.7, 0.6) > -30                # A3's sound, mixed in at 2.6 s
    finally:
        server.STATE.pop("generated_dir", None)
        server.STATE.pop("gen_durations", None)


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
                     visual=project["work"] / "struct-visual", assets=project["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in project["stems"]])
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
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


@pytest.fixture
def board(live_server, project):
    def open_(edl, size=(1440, 900)):
        Path(project["edl"]).write_text(json.dumps(edl, indent=1), encoding="utf-8")
        pg = browser.new_page(viewport={"width": size[0], "height": size[1]})
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.route("**/api/backend", lambda r: r.fulfill(status=200, content_type="application/json",
                                                        body=json.dumps({"state": "ok", "fix": None})))
        pg.goto(live_server)
        pg.wait_for_function("window.tl && tl.el && tl.el.root && typeof P !== 'undefined' && !!P", timeout=15000)
        return pg
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        yield open_
        browser.close()


def on_disk(project) -> dict:
    return json.loads(Path(project["edl"]).read_text(encoding="utf-8"))


def wait_disk(project, pred, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            d = on_disk(project)
            if pred(d):
                return d
        except ValueError:
            pass
        time.sleep(0.1)
    raise AssertionError(f"the EDL on disk never matched: {on_disk(project)}")


def test_an_empty_sequence_draws_its_tracks_behind_the_drop_box(board, project):
    pg = board({**SEED, "segments": []})
    pg.wait_for_selector("#tlEmpty .empty", timeout=15000)
    pg.wait_for_timeout(500)
    assert pg.locator("#tl").is_visible()
    heads = pg.evaluate("""[...document.querySelectorAll('#tl .tl-thead')].filter(h => h.getClientRects().length
        && getComputedStyle(h).visibility !== 'hidden').map(h => h.dataset.track)""")
    assert heads == ["V2", "V1", "A1", "A2"], heads
    assert pg.locator("#tl .tl-ruler").is_visible()
    assert pg.locator("#tl .tl-ruler .tick.major label").count() >= 3
    for n in ("V2", "A1", "A2"):
        assert pg.locator(f"#tl .tl-track[data-track={n}]").is_visible(), n
    assert pg.locator("#tl .tl-lane[data-lane=V1]").is_visible()
    # the box floats over the tracks, and is still the panel's first action
    box = pg.locator("#tlEmpty .empty").bounding_box()
    tl = pg.locator("#tl").bounding_box()
    assert tl["x"] <= box["x"] and box["x"] + box["width"] <= tl["x"] + tl["width"]
    # ...with ONE AI action on it (punch 10): the first pass's, no "Make a first cut" beside it
    pg.wait_for_selector("#tlEmpty .fp-empty-btn:visible", timeout=5000)
    pg.wait_for_timeout(1200)                        # the empty state re-checks each second
    shown = pg.evaluate("""[...document.querySelectorAll('#tlEmpty button')].filter(b => b.getClientRects().length)
        .map(b => b.id || b.className)""")
    assert shown == ["fp-empty-btn"], shown
    shots = os.environ.get("ROUGHCUT_STRUCT_SHOTS")
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(Path(shots) / "empty-sequence-1440.png"))
        pg.locator("#pTimeline").screenshot(path=str(Path(shots) / "empty-sequence-timeline.png"))
    assert pg.errors == []


DROP_JS = """([clip, start, end, t]) => {
  const dt = new DataTransfer();
  dt.setData('application/x-roughcut-shot', JSON.stringify({clip, start, end, why: 'dropped'}));
  const r = tl.el.view.getBoundingClientRect();
  const x = r.left + tl.timeToX(t) - tl.el.view.scrollLeft;
  const lane = tl.el.lanes.V1, lr = lane.getBoundingClientRect();
  const y = lr.top + lr.height / 2;
  for (const type of ['dragenter', 'dragover', 'drop']) {
    lane.dispatchEvent(new DragEvent(type, {bubbles: true, cancelable: true, clientX: x, clientY: y,
                                            dataTransfer: dt}));
  }
}"""


def test_a_drop_at_10_s_on_an_empty_v1_leaves_a_gap_that_a_ripple_delete_closes(board, project):
    pg = board({**SEED, "segments": []})
    pg.wait_for_selector("#tlEmpty .empty", timeout=15000)
    pg.evaluate(DROP_JS, ["CLIP_A.MP4", 1.0, 3.0, 10.0])
    pg.wait_for_function("segs.length === 2", timeout=10000)
    shot = pg.evaluate("""(() => { const g = segs[0], s = segs[1];
        return {gap: g.gap === true, gapLen: tl.dur(g), clip: s.clip, at: tl.filmStart(s.id),
                total: tl.total(), sel: [...tl.state.sel], sid: s.id}; })()""")
    assert shot["gap"] and shot["clip"] == "CLIP_A.MP4", shot
    assert shot["gapLen"] == pytest.approx(10.0, abs=1 / 24 + 1e-6), shot
    assert shot["at"] == pytest.approx(10.0, abs=1 / 24 + 1e-6), shot
    assert shot["total"] == pytest.approx(12.0, abs=2 / 24), shot
    # on disk: the gap is a generated black clip marked as a gap
    d = wait_disk(project, lambda d: len(d.get("segments") or []) == 2)
    assert d["segments"][0].get("gap") is True and d["segments"][0]["clip"].startswith("gen_black_")
    # drawn as a gap (nothing of a clip on it), selectable
    gap = pg.locator("#tl .tl-lane[data-lane=V1] .blk.gap")
    assert gap.count() == 1
    gap.click(position={"x": 30, "y": 10})
    pg.wait_for_function("tl.state.sel.size === 1 && tl.isGap(tl.byId([...tl.state.sel][0]))", timeout=3000)
    # Premiere: ripple delete on a gap closes it
    pg.keyboard.press("Shift+Delete")
    pg.wait_for_function("segs.length === 1", timeout=5000)
    assert pg.evaluate("segs[0].clip") == "CLIP_A.MP4"
    assert pg.evaluate("tl.filmStart(segs[0].id)") == 0
    wait_disk(project, lambda d: len(d.get("segments") or []) == 1 and not d["segments"][0].get("gap"))
    # a drop inside the film is still an insert (no gap)
    pg.evaluate(DROP_JS, ["CLIP_B.MP4", 0.0, 1.0, 0.0])
    pg.wait_for_function("segs.length === 2", timeout=5000)
    assert not any(pg.evaluate("segs.map(s => !!s.gap)"))
    assert pg.errors == []


def _menu(pg, cmd):
    pg.locator("#menubar .menu[data-menu=sequence] .mtop").click()
    pg.locator(f"#menubar button[data-cmd='{cmd}']").click()


def test_sequence_add_and_delete_track(board, project):
    pg = board(SEED)
    pg.wait_for_selector("#tl .blk", timeout=15000)
    _menu(pg, "seq:addv")
    _menu(pg, "seq:adda")
    pg.wait_for_selector("#tl .tl-thead[data-track=V3]")
    pg.wait_for_selector("#tl .tl-thead[data-track=A3]")
    order = pg.evaluate("""[...document.querySelectorAll('#tl .tl-thead')]
        .sort((a, b) => parseFloat(a.style.top) - parseFloat(b.style.top)).map(h => h.dataset.track)""")
    assert order == ["V3", "V2", "V1", "A1", "A2", "A3"], order
    for n in ("V3", "A3"):
        row = pg.locator(f"#tl .tl-track[data-track={n}]")
        assert row.is_visible() and row.bounding_box()["height"] >= 20, n
    d = wait_disk(project, lambda d: "V3" in (d.get("tracks") or {}) and "A3" in (d.get("tracks") or {}))
    # a clip laid on V3 and a clip's sound on A3 (the track targets: V1 off, V3 on)
    pg.evaluate("tl.setTrack('V1', 'target', false); tl.setTrack('V3', 'target', true)")
    pg.evaluate("tl.addOverlay({clip: 'CLIP_C.MP4', in: 1, out: 2}, 0.5, 'V3')")
    pg.evaluate("tl.addOverlay({clip: 'CLIP_B.MP4', in: 0, out: 1}, 1.0, 'A3')")
    assert pg.locator("#tl .tl-track[data-track=V3] .oblk").count() == 1
    assert pg.locator("#tl .tl-track[data-track=A3] .oblk.aclip").count() == 1
    d = wait_disk(project, lambda d: any(o.get("track") == "V3" for o in d.get("overlays") or [])
                  and any(c.get("track") == "A3" for c in d.get("audio") or []))
    # Delete Track refuses a track that still holds clips; empty, it goes
    _menu(pg, "seq:delv")
    assert pg.locator("#tl .tl-thead[data-track=V3]").count() == 1
    oid = pg.evaluate("tl.overlays().find(o => o.track === 'V3').id")
    pg.evaluate(f"tl.removeOverlay('{oid}')")
    _menu(pg, "seq:delv")
    pg.wait_for_function("!document.querySelector('#tl .tl-thead[data-track=V3]')", timeout=3000)
    assert pg.locator("#tl .tl-track[data-track=V3]").count() == 0
    wait_disk(project, lambda d: "V3" not in (d.get("tracks") or {}) and "A3" in (d.get("tracks") or {}))
    # a reload keeps the added track and its clip
    pg.reload()
    pg.wait_for_selector("#tl .tl-thead[data-track=A3]", timeout=15000)
    assert pg.locator("#tl .tl-track[data-track=A3] .oblk.aclip").count() == 1
    assert pg.errors == []
