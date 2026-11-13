"""Finer looks (wave 27, GRAIN): the whole-clip look down to 0.1 s.

The owner, 2026-11-11: *"why did the first pass not do a more granular look (every 1s or
ideally every 0.25s) and why can I still not do granular pass easily using the app ...
I want full ability to even say that I want down to .1s if it really helps."*

Pinned here: the stops (and nothing else) are accepted; frames at 0.25 s and 0.1 s really
are that far apart and all there (a clip whose brightness is its own clock); the tool names
what it read and never calls a partial read the whole clip; the first-pass panel's choice
reaches the look and the estimate follows it before the click; the Index panel says "all N
clips" and what a fine look costs in time. No real model call: the suite's stubs.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "research" / "tools"
sys.path.insert(0, str(ROOT))

from roughcut import inference  # noqa: E402

from test_firstpass import fp, fp_lines, model, _wait, _wait_indexes  # noqa: E402,F401
from test_index_panel import stub_looker  # noqa: E402
from test_server import _fresh  # noqa: E402

STOPS = [4.0, 3.0, 2.0, 1.0, 0.5, 0.25, 0.1]


@pytest.fixture(scope="module")
def ramp(tmp_path_factory) -> Path:
    """Six seconds at 30 fps whose brightness is the frame number (lossless), so a frame
    says exactly which moment of the clip it is."""
    out = tmp_path_factory.mktemp("ramp") / "RAMP.MP4"
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "nullsrc=s=64x64:r=30:d=6,geq=lum='N+16':cb=128:cr=128",
         "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return out


def _sheets(video: Path, out: Path, interval: float) -> dict:
    r = subprocess.run(["uv", "run", "--quiet", str(TOOLS / "contact_sheet.py"), str(video),
                        "-o", str(out), "--interval", f"{interval:g}"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads((out / "index.json").read_text(encoding="utf-8"))["clips"][0]


@pytest.mark.parametrize("iv,n", [(0.25, 24), (0.1, 60)])
def test_frames_are_really_that_far_apart_and_all_there(ramp, tmp_path, iv, n):
    clip = _sheets(ramp, tmp_path / "s", iv)
    cells = [c for s in clip["sheets"] for c in s["cells"]]
    assert clip["n_samples"] == n == len(cells), "nothing thinned, nothing missing"
    assert [c["t"] for c in cells] == pytest.approx([round(i * iv, 2) for i in range(n)])
    assert len(clip["sheets"]) == -(-n // 30), "as many sheets as it takes"
    # and the picture agrees with the label: brightness is the clock (the tool's own extraction)
    script = tmp_path / "probe.py"
    script.write_text(
        "import sys, json\nfrom pathlib import Path\nsys.path.insert(0, sys.argv[1])\n"
        "import contact_sheet as cs\nfrom PIL import Image, ImageStat\n"
        "w = Path(sys.argv[2]); w.mkdir()\n"
        "fr = cs.extract_frames(Path(sys.argv[3]), w, float(sys.argv[4]), 64, 'auto')\n"
        "print(json.dumps([ImageStat.Stat(Image.open(f).convert('L')).mean[0] for f in fr]))\n")
    r = subprocess.run(["uv", "run", "--quiet", "--with", "pillow", "python", str(script), str(TOOLS),
                        str(tmp_path / "w"), str(ramp), f"{iv:g}"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    means = json.loads(r.stdout)
    assert len(means) == n
    # luma of source frame k is (k + 16) -> 1.164 * k after the jpeg's range expansion: read
    # each extracted frame's own number back and compare with where the label says it is
    for i, m in enumerate(means):
        frame_no = (m - means[0]) / 1.164
        assert frame_no == pytest.approx(i * iv * 30, abs=1.6), (i, frame_no)


def test_the_tail_of_a_clip_is_not_dropped(tmp_path):
    """5.088 s at 23.976 fps (a real Copper clip's shape): samples at 0 ... 5.0, the last inside
    the final frame, not a read that stops at 4.75."""
    odd = tmp_path / "ODD.MP4"
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=64x64:r=24000/1001",
                        "-frames:v", "122", "-pix_fmt", "yuv420p", str(odd)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    clip = _sheets(odd, tmp_path / "s", 0.25)
    cells = [c for s in clip["sheets"] for c in s["cells"]]
    assert len(cells) == 21 and cells[-1]["t"] == 5.0, [c["t"] for c in cells][-3:]


def test_labels_carry_the_decimals_the_interval_needs(tmp_path):
    script = tmp_path / "labels.py"
    script.write_text(
        "import sys, json\nsys.path.insert(0, sys.argv[1])\nimport contact_sheet as cs\n"
        "print(json.dumps([[cs.hms(65.0, 4.0), cs.hms(65.0, 1.0)],\n"
        "  [cs.hms(t, 0.25) for t in (0.0, 0.25, 0.5, 1.25)], [cs.hms(t, 0.1) for t in (0.0, 0.1, 0.2, 1.3)],\n"
        "  len({cs.hms(i * 0.1, 0.1) for i in range(30)})]))\n")
    r = subprocess.run(["uv", "run", "--quiet", "--with", "pillow", "python", str(script), str(TOOLS)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    coarse, q, t, distinct = json.loads(r.stdout)
    assert coarse == ["01:05", "01:05"]
    assert q == ["00:00.00", "00:00.25", "00:00.50", "00:01.25"]
    assert t == ["00:00.0", "00:00.1", "00:00.2", "00:01.3"] and distinct == 30


@pytest.fixture(scope="module")
def vp():
    spec = importlib.util.spec_from_file_location("visual_pass", TOOLS / "visual_pass.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Res:
    def __init__(self, content):
        self.content, self.input_tokens, self.output_tokens = content, 1, 1
        self.projected_usd, self.model, self.latency_ms = 0.0, "stub", 1


def test_the_sidecar_says_exactly_what_was_read(vp, ramp, tmp_path, monkeypatch):
    prompts, calls = [], []

    def fake(prompt, **kw):
        prompts.append(prompt)
        calls.append(1)
        return _Res({"moments": [], "unusable": [], "description": f"part {len(calls)}"})
    monkeypatch.setattr(vp.inference, "complete", fake)
    side = vp.analyse(ramp, tmp_path, 0.1, 6, 5, "auto", "analysis", 320)
    assert side["params"]["interval_s"] == 0.1
    assert side["sheets_total"] == side["sheets_read"] == 2 == len(prompts)
    assert len(side["frames_sampled"]) == 60 == side["coverage"]["frames"]
    assert side["frames_sampled"][:4] == [0.0, 0.1, 0.2, 0.3]
    cov = side["coverage"]
    assert cov["whole_clip"] is True and (cov["from_s"], cov["to_s"]) == (0.0, 5.9)
    assert "sampled every 0.1s" in prompts[0] and "0.3, 0.4" in prompts[0]
    assert "covering 0s to 2.9s" in prompts[0], "the span keeps its decimals"
    assert side["description"] == "part 1 part 2"


def test_a_partial_read_is_never_called_the_whole_clip(vp, ramp, tmp_path, monkeypatch):
    n = []

    def fake(prompt, **kw):
        n.append(1)
        if len(n) == 2:
            raise inference.InferenceError("stalled")
        return _Res({"moments": [], "unusable": [], "description": "ok"})
    monkeypatch.setattr(vp.inference, "complete", fake)
    side = vp.analyse(ramp, tmp_path, 0.1, 6, 5, "auto", "analysis", 320)
    assert side["sheets_read"] == 1 and side["sheets_total"] == 2 and side["failed_sheets"]
    assert side["coverage"]["whole_clip"] is False and side["coverage"]["sheets"] == 2


def test_a_long_clip_keeps_a_description_from_the_whole_of_it(vp):
    parts = [f"Sheet {i:03d}: " + "x" * 200 for i in range(80)]
    text, kept = vp._describe_whole(parts)
    assert len(text) <= 1200 and kept < 80
    assert "Sheet 000" in text and "Sheet 079" in text and "Sheet 040" in text or "Sheet 039" in text


def test_a_jump_is_held_to_seconds_not_to_two_frames(vp):
    assert vp.max_event_frames(4.0) == 2 and vp.max_event_frames(1.0) == 2
    assert vp.max_event_frames(0.25) == 8 and vp.max_event_frames(0.1) == 20
    times = [round(i * 0.25, 2) for i in range(40)]
    row = {"start": 2.0, "end": 3.25, "what": "skier mid-air, skis level", "kind": "jump",
           "frames": [2.0, 2.25, 2.5, 2.75, 3.0, 3.25], "confidence": "high", "notable": True}
    m = vp.validate({"moments": [row], "unusable": []}, 0.0, 10.0, times)["moments"][0]
    assert m["notable"] is True, m
    assert (m["start"], m["end"]) == (2.0, 3.25), "no rounding to a tenth"


# ------------------------------------------------------------------ the server


def test_the_new_stops_are_accepted_and_nothing_else(tmp_path, project):
    import server
    assert list(server.VISUAL_INTERVALS) == STOPS
    with _fresh(tmp_path, project, sidecars=project["sidecars"], visual=None) as c:
        for iv in STOPS:
            r = c.post("/api/index/price", json={"stages": ["look"], "interval_s": iv})
            assert r.status_code == 200, (iv, r.text)
            assert r.json()["interval_s"] == iv
        for bad in (0.05, 0.3, 5, 0, -1, "fast"):
            assert c.post("/api/index/price", json={"stages": ["look"], "interval_s": bad}).status_code == 400
            assert c.post("/api/firstpass", json={"look_interval_s": bad}).status_code == 400, bad
        assert c.post("/api/index", json={"interval_s": 0.3}).status_code == 400


def test_the_price_of_a_fine_look_says_frames_sheets_and_time(tmp_path, project):
    import server
    with _fresh(tmp_path, project, sidecars=project["sidecars"], visual=None) as c:
        server.STATE.setdefault("durations", {})["CLIP_B.MP4"] = 600.0
        q = {iv: c.post("/api/index/price", json={"clips": None, "stages": ["look"],
                                                  "interval_s": iv}).json() for iv in (4, 0.25, 0.1)}
        assert q[0.25]["fine"] is True and q[4]["fine"] is False
        # 600 s + two 6 s clips: frames are duration / interval, sheets are 30 frames each
        assert q[0.25]["frames"] >= 2400 and q[0.1]["frames"] >= 6000
        assert q[0.1]["sheets"] >= 200 and q[0.1]["eta_s"] > q[0.25]["eta_s"] > q[4]["eta_s"]
        assert "frames in" in q[0.1]["look_words"] and "sheets" in q[0.1]["look_words"]


def test_the_first_pass_estimate_follows_the_choice(fp):
    import server
    server.STATE.setdefault("durations", {})["CLIP_B.MP4"] = 600.0
    w = fp.get("/api/firstpass").json()["would"]["look"]
    assert w["choice"] == 4.0
    opts = {o["interval_s"]: o for o in w["options"]}
    assert list(opts) == [4.0, 1.0, 0.5, 0.25, 0.1]
    assert opts[4.0]["label"].startswith("Quick: a frame every 4 s")
    assert [o["label"] for o in w["options"][1:]] == [
        "Every second: the whole of every clip at 1 s", "Every 0.5 s", "Every 0.25 s", "Every 0.1 s"]
    secs = [opts[i]["seconds"] for i in (4.0, 1.0, 0.5, 0.25, 0.1)]
    assert secs == sorted(secs) and secs[-1] > 10 * secs[0]
    assert opts[0.1]["frames"] >= 6000 and "frames in" in opts[0.1]["words"]
    assert "frames" not in opts[4.0]["words"], "the quick one is today's sentence"
    assert opts[1.0]["line"] != opts[4.0]["line"] and "0.25 second" in opts[0.25]["line"]


def test_the_choice_reaches_the_look_and_is_remembered(fp, model, monkeypatch, tmp_path):
    import server
    calls: list = []
    stub_looker(server, monkeypatch, tmp_path, calls)
    r = fp.post("/api/firstpass", json={"mode": "run", "look_interval_s": 0.25})
    assert r.status_code == 200, r.text
    s = _wait(fp)
    assert s["state"] == "done", s
    assert calls and {x["interval"] for x in calls} == {0.25}, calls
    _wait_indexes(server)               # the close looks finish in the background
    assert server.look_interval() == 0.25 and server.read_edl()["look"]["first_pass"] == 0.25
    assert fp.get("/api/firstpass").json()["would"]["look"]["choice"] == 0.25, "remembered per bin"
    rows = fp.get("/api/index/clips").json()["rows"]
    assert all(x["stages"]["close"]["state"] == "skipped" for x in rows), \
        "a whole-clip look at 1 s or finer needs no close look"
    assert all("every 0.25 s, whole clip" in x["got"] for x in rows), [x["got"] for x in rows]
    # a finer choice reads the clips already looked at again, before the index
    calls.clear()
    r = fp.post("/api/firstpass", json={"mode": "rerun", "look_interval_s": 0.1})
    assert r.status_code == 200, r.text
    _wait(fp)
    assert {x["interval"] for x in calls} == {0.1} and len(calls) == 3 and all(x["force"] for x in calls), calls
    assert all(x["interval_s"] == 0.1 for x in fp.get("/api/index/clips").json()["rows"])


def test_without_a_choice_the_pass_is_what_it_was(fp, model, monkeypatch, tmp_path):
    import server
    calls: list = []
    stub_looker(server, monkeypatch, tmp_path, calls)
    assert fp.post("/api/firstpass", json={"mode": "run"}).status_code == 200
    assert _wait(fp)["state"] == "done"
    assert {x["interval"] for x in calls} == {4.0}
    _wait_indexes(server)
    assert "first_pass" not in (server.read_edl().get("look") or {})
    rows = fp.get("/api/index/clips").json()["rows"]
    assert all(x["got"].startswith("every 4 s") for x in rows)
    assert all(x["stages"]["close"]["state"] == "done" for x in rows), "the close look still runs"


def test_what_a_clip_got_names_both_looks(tmp_path, project):
    import server
    with _fresh(tmp_path, project, sidecars=project["sidecars"], visual=None) as c:
        v = server.STATE["visual"]
        v.mkdir(parents=True, exist_ok=True)
        (v / "CLIP_A.visual.json").write_text(json.dumps({
            "params": {"interval_s": 4.0}, "sheets_read": 1, "sheets_total": 1,
            "coverage": {"whole_clip": True}}))
        (v / "CLIP_A.fine.json").write_text(json.dumps({
            "params": {"interval_s": 1.0}, "windows_read": [[32.0, 46.0]], "sheets_read": 1, "sheets_total": 1}))
        assert server.look_got("CLIP_A") == "every 4 s; every 1 s on 0:32–0:46"
        (v / "CLIP_A.visual.json").write_text(json.dumps({
            "params": {"interval_s": 0.25}, "sheets_read": 3, "sheets_total": 3,
            "coverage": {"whole_clip": True}}))
        assert server.look_got("CLIP_A").startswith("every 0.25 s, whole clip")
        (v / "CLIP_A.visual.json").write_text(json.dumps({
            "params": {"interval_s": 0.25}, "sheets_read": 2, "sheets_total": 3,
            "failed_sheets": [{"sheet": "x"}], "coverage": {"whole_clip": False}}))
        assert "partial: 2 of 3 sheets read" in server.look_got("CLIP_A")
