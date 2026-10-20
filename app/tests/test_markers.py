"""Markers as Premiere has them (verdict 12, punch 4; REDESIGN-GOAL dimension 5).

Verdict 12: "markers have no name, comment or colour; the AI said it couldn't name 'best
trick', and you can't name one by hand either." A sequence marker now carries a name,
comments, a colour (Premiere's eight: Green Red Purple Orange Yellow White Blue Cyan) and
a duration:

  * the EDL (roughcut/tracks.py `validate_markers`) keeps a plain marker as its bare time,
    as every EDL before it did, and any other as `{t, name?, comment?, colour?, dur?}`;
  * the AI's `marker {at, name?, comment?, colour?, duration?}` op (roughcut/actions.py)
    and the ask's prompt (roughcut/assist.py) name one, or name the one already there;
  * the timeline (app/static/timeline.js) draws it in its colour with its name beside it
    when there is room and everything in its hover; M on a marker, or a double-click,
    opens the Marker dialog (Name, In, Duration, Comments, Marker Color; Delete, Cancel,
    OK), and OK is one undo entry; names survive a reload;
  * an export writes the named markers as the file's chapters (research/tools/assemble.py),
    an In/Out range's moved onto its own clock (server.export_markers).

No model is called: the ask is scripted (`inference.set_backend`)."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research" / "tools"))
from roughcut import actions, assist, config, inference  # noqa: E402
from roughcut import tracks as tracksmod  # noqa: E402

TOOLS = ROOT / "research" / "tools"
FPS = 24.0
SEGS = [{"id": "s1", "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
        {"id": "s2", "clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"},
        {"id": "s3", "clip": "CLIP_C.MP4", "in": 2.0, "out": 5.0, "why": "third"}]


def state(**over) -> dict:
    base = {"segments": [dict(s) for s in SEGS],
            "clips": {"CLIP_A.MP4": {"duration": 6.0}, "CLIP_B.MP4": {"duration": 6.0},
                      "CLIP_C.MP4": {"duration": 6.0}},
            "fps": FPS, "markers": [], "in": None, "out": None, "tracks": {}, "overlays": [],
            "transitions": [], "colour": {}, "looks": [], "presets": {}}
    base.update(over)
    return base


# ---------------------------------------------------------------- the EDL's shape

def test_a_plain_marker_stays_a_bare_time_and_a_named_one_keeps_what_it_says():
    v = tracksmod.validate_markers
    assert v([1.5, 0.25, 1.5]) == [0.25, 1.5]                  # as every EDL before
    assert v([{"t": 2}]) == [2.0]                              # an object with nothing: bare
    assert v([{"t": 2, "colour": "Green", "name": "  ", "dur": 0}]) == [2.0]
    got = v([{"t": 3.25, "name": " best   trick ", "comment": " the 360 \n", "colour": "Red",
              "duration": 1.0}, 0.5])
    assert got == [0.5, {"t": 3.25, "name": "best trick", "comment": "the 360", "colour": "red",
                         "dur": 1.0}]
    assert v([{"t": 1, "color": "CYAN"}]) == [{"t": 1.0, "colour": "cyan"}]
    # a named marker is never lost to a bare duplicate at its time; two named: the later
    assert v([{"t": 1, "name": "a"}, 1.0]) == [{"t": 1.0, "name": "a"}]
    assert v([{"t": 1, "name": "a"}, {"t": 1, "name": "b"}]) == [{"t": 1.0, "name": "b"}]
    for bad, why in ((["x"], "not a number"), ([{"t": -1}], "before the film"),
                     ([{"t": 1, "colour": "pink"}], "Green, Red, Purple, Orange, Yellow, White, Blue, Cyan"),
                     ([{"t": 1, "name": 5}], "not text"),
                     ([{"t": 1, "name": "n" * 121}], "longer than 120"),
                     ([{"t": 1, "comment": "c" * 1001}], "longer than 1000"),
                     ([{"t": 1, "dur": -1}], "duration")):
        with pytest.raises(ValueError, match=why):
            v(bad)
    assert tracksmod.marker_time({"t": 4.5, "name": "x"}) == 4.5
    assert tracksmod.marker_time(2) == 2.0 and tracksmod.marker_time({"name": "x"}) is None


def test_named_markers_round_trip_through_the_project_and_bare_ones_still_do(client, project):
    marks = [0.5, {"t": 2.0, "name": "best trick", "comment": "the 360", "colour": "red", "dur": 1.0},
             {"t": 3.0, "colour": "blue"}]
    r = client.put("/api/project", json={"tracks": {}, "markers": marks})
    assert r.status_code == 200, r.text
    assert client.get("/api/project").json()["markers"] == marks
    disk = json.loads(Path(project["edl"]).read_text(encoding="utf-8"))
    assert disk["markers"] == marks
    r = client.put("/api/project", json={"markers": [{"t": 1, "colour": "mauve"}]})
    assert r.status_code == 400 and "Green, Red" in r.text
    assert client.put("/api/project", json={"markers": [1.0, 0.25]}).status_code == 200
    assert client.get("/api/project").json()["markers"] == [0.25, 1.0]


# ---------------------------------------------------------------- the AI's op

def accepted(op, st=None) -> dict:
    ok, bad = actions.validate([op], st or state())
    assert bad == [] and len(ok) == 1, bad
    return ok[0]


def refused(op, st=None) -> str:
    ok, bad = actions.validate([op], st or state())
    assert ok == [] and len(bad) == 1, ok
    return bad[0]["why"]


def test_the_marker_op_takes_a_name_comment_colour_and_duration():
    op = accepted({"op": "marker", "at": 2.0, "name": " best  trick ", "comment": "the 360",
                   "colour": "Red", "duration": 1.0})
    assert op == {"op": "marker", "at": 2.0, "name": "best trick", "colour": "red", "duration": 1.0,
                  "comment": "the 360"}
    assert actions.describe(op, state()) == \
        "Marker at 00:00:02:00: “best trick”, Red, 1.00 s long, comment “the 360”"
    assert accepted({"op": "marker", "at": 1.0}) == {"op": "marker", "at": 1.0}
    assert actions.describe({"op": "marker", "at": 1.0}, state()) == "Marker at 00:00:01:00"
    assert accepted({"op": "marker", "at": 1.0, "color": "cyan"})["colour"] == "cyan"
    assert "Green, Red, Purple" in refused({"op": "marker", "at": 1.0, "colour": "pink"})
    assert "runs past the end" in refused({"op": "marker", "at": 6.5, "duration": 1.0})
    assert "is text" in refused({"op": "marker", "at": 1.0, "name": 3})
    # a name over the limit is cut, not refused (the model's long name still lands)
    assert len(accepted({"op": "marker", "at": 1.0, "name": "x" * 300})["name"]) == 120


def test_the_op_names_a_marker_already_there():
    st = state(markers=[2.0, {"t": 4.0, "name": "old"}])
    assert "a marker at 00:00:02:00 already" in refused({"op": "marker", "at": 2.0}, st)
    op = accepted({"op": "marker", "at": 2.0, "name": "best trick"}, st)
    assert actions.describe(op, st) == "The marker at 00:00:02:00: “best trick”"
    # the sim reads both shapes: a named marker's time is a marker there too
    assert "already" in refused({"op": "marker", "at": 4.0}, st)
    out = actions.check([{"op": "marker", "at": 5.0, "name": "a"},
                         {"op": "marker", "at": 5.0, "colour": "red"}], st)
    assert out["lines"] == ["Marker at 00:00:05:00: “a”", "The marker at 00:00:05:00: Red"]


def test_the_ask_shows_the_model_marker_names_and_how_editors_say_it():
    st = state(markers=[1.0, {"t": 2.0, "name": "best trick", "colour": "red", "dur": 1.0,
                               "comment": "the 360"}])
    block = assist.sequence_block(st)
    assert ("Markers: 1.00 s (00:00:01:00); 2.00 s (00:00:02:00) “best trick” Red 1.00 s long "
            "comment: the 360") in block
    prompt = assist.build_prompt("drop a marker at the start of shot 3 called best trick",
                                 {"kind": "sequence"}, st, {})
    assert '{"op":"marker","at","name"?,"comment"?,"colour"?,"duration"?}' in prompt
    assert "Green, Red, Purple, Orange, Yellow, White, Blue, Cyan" in prompt
    assert '"called / named X" is its `name`' in prompt


class Scripted:
    """The assist call answers with `answer` (no model is called)."""
    name = "markers-scripted"

    def __init__(self, answer):
        self.answer = answer
        self.prompts: list[str] = []

    def complete(self, request):
        self.prompts.append(request.prompt)
        text = json.dumps(self.answer)
        model = config.model_for(request.role)
        return inference.Result(content=text, input_tokens=4000, output_tokens=200, backend=self.name,
                                model=model, projected_usd=config.projected_usd(model, 4000, 200),
                                latency_ms=1, raw=text)


def test_drop_a_marker_at_the_start_of_shot_3_called_best_trick(client, project):
    """Verdict 12's own ask, scripted the way the real model answered it (In / Out and a
    marker), now with the name: the answer's op carries it and its line says it."""
    body = {"variant": "T", "title": "test cut", "orient": "none", "story": "", "target_s": [5, 20],
            "segments": [dict(s) for s in SEGS]}
    Path(project["edl"]).write_text(json.dumps(body), encoding="utf-8")
    model = Scripted({"kind": "ops", "summary": "In/Out around shots 2-4 and a marker",
                      "ops": [{"op": "in_out", "in": 2.0, "out": 7.0},
                              {"op": "marker", "at": 4.0, "name": "best trick"}]})
    inference.set_backend(model)
    try:
        job = client.post("/api/assist", json={
            "text": "Set In and Out around shots 2 through 3 and drop a marker at the start "
                    "of shot 3 called best trick", "scope": {"kind": "sequence"}}).json()["job"]
        for _ in range(200):
            d = client.get(f"/api/assist/{job}").json()
            if d["status"] != "running":
                break
            time.sleep(0.05)
    finally:
        inference.set_backend(None)
    assert d["status"] == "done", d
    assert d["refused"] == [], d["refused"]
    assert {"op": "marker", "at": 4.0, "name": "best trick"} in d["ops"]
    assert "Marker at 00:00:04:00: “best trick”" in d["lines"]
    assert "called / named X" in model.prompts[0]


# ---------------------------------------------------------------- the export's chapters

def test_chapters_are_the_named_markers_each_to_the_next():
    import assemble
    ch = assemble.chapters_of([0.5, {"t": 3.0, "name": "landing"}, {"t": 1.0, "name": "best trick"},
                               {"t": 2.0, "colour": "red"}, {"t": 9.0, "name": "past the end"}], 7.0)
    # an MP4's chapters run from its first frame: the opening before the first is "Start"
    assert ch == [{"start": 0.0, "end": 1.0, "title": "Start"},
                  {"start": 1.0, "end": 3.0, "title": "best trick"},
                  {"start": 3.0, "end": 7.0, "title": "landing"}]
    assert assemble.chapters_of([{"t": 0.0, "name": "cold open"}, {"t": 2.0, "name": "b"}], 4.0) == [
        {"start": 0.0, "end": 2.0, "title": "cold open"}, {"start": 2.0, "end": 4.0, "title": "b"}]
    meta = assemble.ffmetadata([{"start": 0.0, "end": 1.5, "title": "a = b; #1 \\ c"}])
    assert meta == (";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1500\n"
                    "title=a \\= b\\; \\#1 \\\\ c\n")
    assert assemble.chapters_of([1.0, 2.0], 7.0) == []


def test_an_in_out_range_moves_its_markers_onto_its_own_clock():
    import server
    whole = [dict(s) for s in SEGS]                               # 0-2, 2-4, 4-7
    marks = [0.5, {"t": 2.5, "name": "in range", "dur": 3.0}, 4.0, {"t": 6.9, "name": "past"}]
    assert server.export_markers(marks, whole, [dict(s) for s in whole]) == marks
    # In 1.0 / Out 5.0: CLIP_A 2.0-3.0, CLIP_B 0-2, CLIP_C 2.0-3.0 (export.js clipRange)
    sent = [{**SEGS[0], "in": 2.0}, dict(SEGS[1]), {**SEGS[2], "out": 3.0}]
    assert server.range_start(whole, sent) == 1.0
    assert server.export_markers(marks, whole, sent) == [{"t": 1.5, "name": "in range", "dur": 2.5}, 3.0]
    # a range whose first shot is not in the sequence cannot be placed: no chapters
    assert server.export_markers(marks, whole, [{"id": "zz", "clip": "X", "in": 0, "out": 1}]) == []


def _ffprobe_chapters(path: Path) -> list[dict]:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_chapters", "-of", "json", str(path)],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)["chapters"]


def _ffprobe_video(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                        "-show_entries", "stream=r_frame_rate,nb_read_frames", "-of", "json", str(path)],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)["streams"][0]


def test_an_in_out_export_carries_its_named_markers_as_chapters(client, project, monkeypatch, tmp_path):
    """POST /api/render with an In/Out range (the shots the Export dialog sends): the EDL it
    writes for assemble.py has the range's markers on its own clock, and assemble.py's file
    lists the named ones as chapters in ffprobe, its frames untouched."""
    import server
    body = {"variant": "T", "title": "test cut", "orient": "none", "story": "", "target_s": [5, 20],
            "segments": [dict(s) for s in SEGS],
            "markers": [0.5, {"t": 2.0, "name": "best trick", "colour": "red"},
                        {"t": 3.5, "name": "landing", "comment": "clean"}, {"t": 6.5, "name": "out"}]}
    Path(project["edl"]).write_text(json.dumps(body), encoding="utf-8")
    seen: dict = {}
    ran = threading.Event()

    def fake_render(job, edl_path, out_path, meta):
        seen["edl"] = json.loads(Path(edl_path).read_text(encoding="utf-8"))
        server.RENDERS[job].finish("done")
        ran.set()

    monkeypatch.setattr(server, "_render_job", fake_render)
    sent = [{**SEGS[0], "in": 2.0}, dict(SEGS[1]), {**SEGS[2], "out": 3.0}]   # In 1.0 / Out 5.0
    r = client.post("/api/render", json={"segments": sent, "preset": "quick", "name": "chapters",
                                          "folder": "markers-test"})
    assert r.status_code == 200, r.text
    # /api/render answers once it has started the job's thread (server.py `threading.Thread(
    # target=_render_job)`), not once the job ran: read what it wrote when it has (lane TESTS2,
    # wave 17: `KeyError: 'edl'` 1 run in 20 at load 9-10; 3 of 3 with the thread 0.3 s late)
    assert ran.wait(10), "the render job's thread never ran"
    assert seen["edl"]["markers"] == [{"t": 1.0, "name": "best trick", "colour": "red"},
                                      {"t": 2.5, "name": "landing", "comment": "clean"}]
    edl_path = tmp_path / "range.json"
    edl_path.write_text(json.dumps(seen["edl"]), encoding="utf-8")
    out = tmp_path / "range.mp4"
    res = subprocess.run(["uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(edl_path),
                          "--footage", str(project["footage"]), "--profile", "match", "-o", str(out)],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "chapters: 3 — Start 0.00s, best trick 1.00s, landing 2.50s" in res.stdout
    ch = _ffprobe_chapters(out)
    assert [(c["tags"]["title"], float(c["start_time"]), float(c["end_time"])) for c in ch] == [
        ("Start", 0.0, 1.0), ("best trick", 1.0, 2.5), ("landing", 2.5, pytest.approx(4.0, abs=0.05))]
    v = _ffprobe_video(out)
    assert v["r_frame_rate"] == "24/1" and int(v["nb_read_frames"]) == 96     # 4.00 s, untouched
    # no named marker, no chapters (and no extra pass)
    plain = dict(seen["edl"], markers=[1.0])
    edl_path.write_text(json.dumps(plain), encoding="utf-8")
    res = subprocess.run(["uv", "run", "--quiet", str(TOOLS / "assemble.py"), str(edl_path),
                          "--footage", str(project["footage"]), "--profile", "match", "-o", str(out)],
                         capture_output=True, text=True)
    assert res.returncode == 0 and "chapters:" not in res.stdout
    assert _ffprobe_chapters(out) == []


# ---------------------------------------------------------------- the board

pw_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(project):
    import uvicorn
    import server

    original = project["edl"].read_text(encoding="utf-8")
    server.configure(project["edl"], project["footage"], project["sidecars"], project["work"],
                     proxies=False, assets=project["assets"])
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


@pytest.fixture(scope="module")
def browser(live_server):
    with pw_api.sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        yield b
        b.close()


@pytest.fixture
def page(browser, live_server, project):
    body = {"variant": "T", "title": "test cut", "orient": "none", "story": "", "target_s": [5, 20],
            "segments": [dict(s) for s in SEGS]}
    Path(project["edl"]).write_text(json.dumps(body, indent=1), encoding="utf-8")
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(live_server)
    pg.wait_for_selector("#tl .blk")
    pg.wait_for_selector("#tlKeys", state="attached")
    pg.evaluate("localStorage.clear(); tl.setMarkers([]); tl.clearHistory()")
    yield pg
    ctx.close()
    assert errors == [], errors


def disk(project) -> dict:
    return json.loads(Path(project["edl"]).read_text(encoding="utf-8"))


def settle(page, project) -> dict:
    page.evaluate("async () => { await roughcutFlush(); await tl.saveSeq(); }")
    return disk(project)


def wait_disk_markers(project, want, timeout=8.0):
    end = time.time() + timeout
    got = None
    while time.time() < end:
        got = disk(project).get("markers", [])
        if got == want:
            return got
        time.sleep(0.1)
    assert got == want
    return got


def undo_title(page) -> str:
    return page.locator("#undo").get_attribute("title") or ""


def fill_dialog(page, *, name=None, comment=None, colour=None, duration=None, at=None):
    dlg = page.locator("[data-testid=marker-dialog]")
    if name is not None:
        dlg.locator("[data-testid=marker-name]").fill(name)
    if comment is not None:
        dlg.locator("[data-testid=marker-comment]").fill(comment)
    if colour is not None:
        dlg.locator(f"[data-testid=marker-colour][data-colour={colour}]").click()
    if duration is not None:
        dlg.locator("[data-testid=marker-duration]").fill(duration)
    if at is not None:
        dlg.locator("[data-testid=marker-in]").fill(at)


def test_m_on_a_marker_opens_the_marker_dialog_and_ok_is_one_undo(page, project):
    page.evaluate("tl.seek(1.0)")
    page.keyboard.press("m")
    assert page.evaluate("tl.markers()") == [1.0]
    assert undo_title(page).startswith("undo: add marker")
    page.keyboard.press("m")                                  # M again: Premiere's dialog
    dlg = page.locator("[data-testid=marker-dialog]")
    dlg.wait_for(state="visible")
    assert page.evaluate("document.activeElement.id") == "mkName"
    assert dlg.locator("[data-testid=marker-in]").input_value() == "00:00:01:00"
    assert dlg.locator("[data-testid=marker-duration]").input_value() == "00:00:00:00"
    assert dlg.locator(".mk-sw.on").get_attribute("data-colour") == "green"
    assert dlg.locator("[data-testid=marker-colour]").count() == 8
    assert [b.get_attribute("aria-label") for b in dlg.locator("[data-testid=marker-colour]").all()] == \
        ["Green", "Red", "Purple", "Orange", "Yellow", "White", "Blue", "Cyan"]
    # typing in it is the dialog's: an "m" in the name adds no marker
    page.keyboard.type("best trick m")
    assert page.evaluate("tl.markers().length") == 1
    fill_dialog(page, name="best trick", comment="the 360, clean", colour="red", duration="00:00:01:00")
    dlg.locator("[data-testid=marker-ok]").click()
    assert not dlg.is_visible()
    want = [{"t": 1.0, "name": "best trick", "comment": "the 360, clean", "colour": "red", "dur": 1.0}]
    assert page.evaluate("tl.markers()") == want
    assert undo_title(page).startswith("undo: edit marker"), undo_title(page)
    wait_disk_markers(project, want)
    # the ruler: the flag in its colour, a bar to its end, the name, all of it in the hover
    flag = page.locator("#tl .tl-ruler .tl-marker")
    assert flag.get_attribute("data-colour") == "red"
    assert page.evaluate("getComputedStyle(document.querySelector('#tl .tl-marker')).backgroundColor") \
        == "rgb(229, 72, 77)"
    hover = flag.get_attribute("title")
    assert hover.startswith("best trick · 00:00:01:00 · Duration 00:00:01:00 · Red\nthe 360, clean\n"), hover
    assert "double-click to edit" in hover
    name = page.locator("#tl .tl-ruler .tl-marker-name")
    assert name.inner_text() == "best trick" and name.get_attribute("title") == hover
    bar = page.evaluate("""() => { const b = document.querySelector('#tl .tl-marker-dur');
      return b ? parseFloat(b.style.width) : null; }""")
    assert bar == pytest.approx(page.evaluate("tl.state.zoom"), abs=1)
    # one Ctrl+Z takes the dialog's edit back whole (the marker stays), redo brings it back
    page.keyboard.press("Control+z")
    assert page.evaluate("tl.markers()") == [1.0]
    assert page.locator("#tl .tl-ruler .tl-marker-name").count() == 0
    page.keyboard.press("Control+Shift+z")
    assert page.evaluate("tl.markers()") == want
    # names survive a reload
    wait_disk_markers(project, want)
    page.reload()
    page.wait_for_selector("#tl .tl-ruler .tl-marker-name")
    assert page.evaluate("tl.markers()") == want
    assert page.locator("#tl .tl-ruler .tl-marker-name").inner_text() == "best trick"
    assert page.evaluate("tl.markerAt(1.0)") == {"t": 1.0, "name": "best trick",
                                                  "comment": "the 360, clean", "colour": "red", "dur": 1.0}


def test_a_double_click_opens_it_cancel_changes_nothing_and_delete_is_one_undo(page, project):
    page.evaluate("tl.addMarker(2.0, {name: 'jump'}); tl.addMarker(4.0); tl.clearHistory()")
    page.evaluate("document.querySelector('#tl').scrollIntoView({block: 'end'})")
    before = undo_title(page)
    page.locator("#tl .tl-ruler .tl-marker[data-t='4']").dblclick()
    dlg = page.locator("[data-testid=marker-dialog]")
    dlg.wait_for(state="visible")
    assert dlg.locator("[data-testid=marker-name]").input_value() == ""
    fill_dialog(page, name="changed my mind", colour="blue")
    dlg.locator("[data-testid=marker-cancel]").click()
    assert not dlg.is_visible()
    assert page.evaluate("tl.markers()") == [{"t": 2.0, "name": "jump"}, 4.0]
    assert undo_title(page) == before, "Cancel made an undo entry"
    # a double-click on the name opens its marker; Esc closes it unchanged
    page.locator("#tl .tl-ruler .tl-marker-name").dblclick()
    dlg.wait_for(state="visible")
    assert dlg.locator("[data-testid=marker-name]").input_value() == "jump"
    page.keyboard.press("Escape")
    assert not dlg.is_visible()
    assert page.evaluate("tl.markers()") == [{"t": 2.0, "name": "jump"}, 4.0]
    # Enter in the name is OK; the In moves it (never onto another marker)
    page.locator("#tl .tl-ruler .tl-marker[data-t='2']").dblclick()
    dlg.wait_for(state="visible")
    fill_dialog(page, at="00:00:04:00")
    dlg.locator("[data-testid=marker-ok]").click()
    assert dlg.is_visible() and "already at 00:00:04:00" in dlg.locator("#mkNote").inner_text()
    fill_dialog(page, at="00:00:03:00", duration="1.5")
    dlg.locator("[data-testid=marker-name]").press("Enter")
    assert not dlg.is_visible()
    assert page.evaluate("tl.markers()") == [{"t": 3.0, "name": "jump", "dur": 1.5}, 4.0]
    # Delete: gone, one Ctrl+Z puts it back
    page.locator("#tl .tl-ruler .tl-marker[data-t='3']").dblclick()
    dlg.wait_for(state="visible")
    dlg.locator("[data-testid=marker-delete]").click()
    assert page.evaluate("tl.markers()") == [4.0]
    assert undo_title(page).startswith("undo: clear marker"), undo_title(page)
    # the closed dialog keeps no focus, so the board's keys work at once (a Delete then
    # Ctrl+Z did nothing 2 times in 3 when the focus stayed on the dialog's button)
    assert page.evaluate("!document.querySelector('[data-testid=marker-dialog]').contains(document.activeElement)")
    page.keyboard.press("Control+z")
    assert page.evaluate("tl.markers()") == [{"t": 3.0, "name": "jump", "dur": 1.5}, 4.0]
    settle(page, project)
    wait_disk_markers(project, [{"t": 3.0, "name": "jump", "dur": 1.5}, 4.0])


def test_a_name_never_reads_through_the_timecode_and_shows_only_with_room(page):
    page.evaluate("""() => { tl.fit(); tl.addMarker(1.0, {name: 'a long marker name that runs on'});
      tl.addMarker(1.0 + 2 / 24, {name: 'crowded'}); tl.addMarker(5.0, {name: 'end', colour: 'cyan'}); }""")
    page.evaluate("document.querySelector('#tl').scrollIntoView({block: 'end'})")
    names = page.evaluate("""() => [...document.querySelectorAll('#tl .tl-marker-name')].map((n) => {
      const r = n.getBoundingClientRect(); return {text: n.textContent, l: r.left, r: r.right,
      cut: n.scrollWidth > n.clientWidth}; })""")
    texts = [n["text"] for n in names]
    # 2 frames apart at fit zoom: the first has no room before the second, so it is not drawn
    assert "a long marker name that runs on" not in texts and "crowded" in texts and "end" in texts
    # every timecode still shown lies clear of every name
    labels = page.evaluate("""() => [...document.querySelectorAll('#tl .tick.major label')]
      .filter((l) => getComputedStyle(l).visibility !== 'hidden')
      .map((l) => { const r = l.getBoundingClientRect(); return [r.left, r.right]; })""")
    for n in names:
        for a, b in labels:
            assert b <= n["l"] or a >= n["r"], (n, a, b)
    # the hidden one's marker still says its name in the hover
    hover = page.locator("#tl .tl-ruler .tl-marker[data-t='1']").get_attribute("title")
    assert hover.startswith("a long marker name that runs on · 00:00:01:00")
    assert page.locator("#tl .tl-ruler .tl-marker[data-t='5']").get_attribute("data-colour") == "cyan"


def test_a_marker_at_a_shots_first_frame_parks_on_that_shot_at_59_94(page):
    """At 59.94 a frame is 0.0167 s. Frame 98 starts at 1.63497 s: kept to 0.01 s, the marker
    a 59.94 shot's first frame got was 1.63 — frame 97, the shot before (Killington's "drop a
    marker at the start of shot 3" parked the Program on shot 2). A marker sits on the edit
    grid now, as every cut does: the frame's own start at 4 places."""
    got = page.evaluate("() => { tl.setFps(59.94); const m = tl.addMarker(1.635); "
                        "const n = tl.nextMarker(0); tl.setFps(null); return [m, n]; }")
    assert got == [1.635, 1.635]
