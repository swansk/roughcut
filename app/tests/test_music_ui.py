"""The music bed on the board (wave 25, lane MUSIC), in real Chromium: the level control,
one undo each, the Program's gain following it, the prompt box's music op with a scripted
model (never a real call), and Add music…'s picker opening from a real click (FINISH-01)."""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pw_sync = pytest.importorskip("playwright.sync_api", reason="playwright not installed")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from roughcut import config, inference  # noqa: E402

SEGMENTS = [
    {"id": "e1", "clip": "CLIP_A.MP4", "in": 0.5, "out": 3.5, "why": "one"},
    {"id": "e2", "clip": "CLIP_B.MP4", "in": 0.0, "out": 3.0, "why": "two"},
]


class Model:
    """Answers 'add music that fits…' with a music op naming the first track the prompt lists,
    or, when the prompt lists none, with an answer that points at Add music…."""
    name = "music-scripted"

    def __init__(self):
        self.prompts: list[str] = []

    def complete(self, request):
        text = "{}"
        if "kind" in (request.schema or {}):
            self.prompts.append(request.prompt)
            line = next((ln for ln in request.prompt.splitlines() if ln.startswith("Music tracks:")), "")
            if "none:" in line:
                answer = {"kind": "answer", "text": "This project has no music yet: use Add music… in the Audio tab."}
            else:
                name = line.split(":", 1)[1].split("(")[0].strip()
                answer = {"kind": "ops", "summary": "add the track and keep it under the talking",
                          "ops": [{"op": "music", "track": name, "level": -10, "duck": True}]}
            text = json.dumps(answer)
        model = config.model_for(request.role)
        return inference.Result(content=text, input_tokens=100, output_tokens=50, backend=self.name, model=model,
                                projected_usd=config.projected_usd(model, 100, 50), latency_ms=1, raw=text)


MODEL = Model()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wav(path: Path, seconds: float = 2.0) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi", "-i",
                    f"sine=frequency=440:duration={seconds}", str(path)], check=True, capture_output=True)
    return path


def _seed(project) -> None:
    Path(project["edl"]).write_text(json.dumps({"variant": "T", "title": "t", "orient": "none", "story": "",
                                                "target_s": [5, 30], "segments": [dict(s) for s in SEGMENTS]}), encoding="utf-8")


@pytest.fixture(scope="module")
def live_server(project):
    import uvicorn
    import server
    original = project["edl"].read_text(encoding="utf-8")
    _seed(project)
    server.configure(project["edl"], project["footage"], project["sidecars"], project["work"],
                     proxies=False, assets=project["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in project["stems"]])
    inference.set_backend(MODEL)
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    th.join(timeout=10)
    inference.set_backend(None)
    project["edl"].write_text(original, encoding="utf-8")


@pytest.fixture(scope="module")
def browser(live_server):
    with pw_sync.sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        yield b
        b.close()


@pytest.fixture
def board(browser, live_server, project):
    import server
    _seed(project)
    mine = project["work"] / "music" / server.STATE["footage"].name
    if mine.exists():
        for p in mine.iterdir():
            p.unlink()
    MODEL.prompts.clear()
    made = []

    def page():
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.goto(live_server)
        pg.wait_for_selector("#tl .blk")
        pg.wait_for_function(f"tl.state && segs.length === {len(SEGMENTS)}", timeout=15000)
        made.append((ctx, pg))
        return pg
    yield page
    errors = [e for _, pg in made for e in pg.errors]
    for ctx, _ in made:
        ctx.close()
    assert errors == [], errors


def saved_music(project):
    return json.loads(Path(project["edl"]).read_text(encoding="utf-8")).get("effects_music")


def wait_saved(pg):
    pg.evaluate("roughcutFlush()")
    pg.wait_for_timeout(900)          # an undo's autosave (700 ms) lands before it is read
    pg.evaluate("roughcutFlush()")


def test_add_music_opens_the_browsers_file_picker_from_a_real_click(board):
    pg = board()
    pg.click('button.tool[data-tool="sound"]')
    with pg.expect_file_chooser(timeout=5000) as fc:
        pg.click("[data-testid=music-add]")
    chooser = fc.value
    assert chooser.is_multiple() is False
    assert chooser.element.get_attribute("accept") and ".mp3" in chooser.element.get_attribute("accept")


def test_the_level_is_a_control_saved_in_the_cut_and_one_undo_each(board, project, tmp_path):
    pg = board()
    pg.click('button.tool[data-tool="sound"]')
    pg.select_option("#musicTrack", "music/bed.wav")
    wait_saved(pg)
    assert saved_music(project)["bed_lufs"] == -24.0            # the default: 8 dB under the film
    assert pg.input_value("#bedLevel") == "-8"
    pg.fill("#bedLevel", "-14")
    pg.press("#bedLevel", "Tab")
    wait_saved(pg)
    assert saved_music(project)["bed_lufs"] == -30.0
    # the Program's bed plays what the export will mix: the gain follows the level (6 dB = x0.5)
    g = pg.evaluate("(() => { const seg = segs[0]; return bedGainAt(2, 4.5, seg); })()")
    pg.fill("#bedLevel", "-8")
    pg.press("#bedLevel", "Tab")
    g8 = pg.evaluate("(() => { const seg = segs[0]; return bedGainAt(2, 4.5, seg); })()")
    assert g8 / g == pytest.approx(2.0, rel=0.05) or g8 >= 0.99, (g, g8)
    pg.fill("#bedLevel", "-14")
    pg.press("#bedLevel", "Tab")
    pg.select_option("#duck", "0")
    wait_saved(pg)
    m = saved_music(project)
    assert m["bed_lufs"] == -30.0 and m["duck"] is False
    # undo (the board's own undo, as Edit › Undo calls it): the dip comes back, then the level
    pg.evaluate("tl.undo()")
    wait_saved(pg)
    assert saved_music(project)["duck"] is True and saved_music(project)["bed_lufs"] == -30.0
    assert pg.input_value("#duck") == "12"
    pg.evaluate("tl.undo()")
    wait_saved(pg)
    assert saved_music(project)["bed_lufs"] == -24.0 and pg.input_value("#bedLevel") == "-8"


def ask(pg, text):
    pg.wait_for_selector('[data-testid="ai-prompt-input"]')
    pg.locator("#tl .tl-ruler").click(position={"x": 5, "y": 5})
    pg.keyboard.press("Control+j")
    pg.keyboard.type(text)
    pg.keyboard.press("Enter")
    pg.wait_for_function("document.querySelector('[data-testid=ai-status]').dataset.state === 'done'", timeout=30000)


def test_the_prompt_box_places_a_track_the_project_has_and_one_undo_takes_it_back(board, project, tmp_path):
    pg = board()
    pg.click('button.tool[data-tool="sound"]')
    pg.set_input_files("[data-testid=music-file]", str(_wav(tmp_path / "Night Drive.wav", 3.0)))
    pg.wait_for_function("document.querySelector('#musicTrack').value === 'project/Night Drive.wav'")
    pg.select_option("#musicTrack", "")                         # the editor has no bed on, the track is in the project
    wait_saved(pg)
    assert saved_music(project) is None
    ask(pg, "add music that fits and keep it under the talking")
    prompt = MODEL.prompts[-1]
    assert "Music bed (A2): none set" in prompt and "Night Drive.wav" in prompt and "added to this project" in prompt
    row = pg.locator("[data-testid=ai-op-row]")
    assert row.count() == 1 and row.first.get_attribute("data-op") == "music"
    assert "Music bed: Night Drive.wav" in " ".join(row.first.inner_text().split())
    pg.locator("[data-testid=ai-accept-all]").click()
    pg.wait_for_function("document.querySelector('#musicTrack').value === 'project/Night Drive.wav'", timeout=10000)
    wait_saved(pg)
    m = saved_music(project)
    assert m["asset"] == "project/Night Drive.wav" and m["bed_lufs"] == -26.0 and m["duck"] is True
    assert pg.input_value("#bedLevel") == "-10"
    pg.locator("#tl .tl-ruler").click(position={"x": 5, "y": 5})
    pg.keyboard.press("Control+z")                              # one undo for the whole answer
    wait_saved(pg)
    assert saved_music(project) is None and pg.input_value("#musicTrack") == ""


def test_with_no_track_the_ai_says_so_and_points_at_add_music(board, project, monkeypatch, tmp_path):
    import server
    empty = tmp_path / "no-assets"
    (empty / "music").mkdir(parents=True)
    monkeypatch.setitem(server.STATE, "assets", empty)
    pg = board()
    ask(pg, "add music that fits and keep it under the talking")
    assert "Music tracks: none: the project has no music" in MODEL.prompts[-1]
    said = pg.inner_text("[data-testid=ai-status]") + " " + pg.inner_text("body")
    assert "Add music…" in said and pg.locator("[data-testid=ai-op-row]").count() == 0
