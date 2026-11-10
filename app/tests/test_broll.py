"""K7 (wave 22, lane BROLL): the first pass is not only the GoPro's acceleration events.

Karl: *"make sure you're not over-indexing only on finding those super acceleration events ...
there's also the fun B-roll ... people sitting on a chairlift and they say pocket pizza."*

A bin of two clips: a GoPro clip with three felt speed drops and an air, and a 40 s chairlift
clip with only a transcript (two voices, "you got the pocket pizza?" / "pocket pizza!") and,
in one test, a `lift` telemetry stretch. Scripted backend, no model call, no video.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import config, events, inference, revise, telemetry_events as te  # noqa: E402

GOPRO = "GX010001.MP4"
LIFT = "GX010002.MP4"
PHONE = "IMG_0420.MOV"


def _felt(kind, start, end, **kw):
    return {"kind": kind, "who": "wearer", "start": start, "end": end, "t": start, "source": "telemetry",
            "status": "felt", "check": True, "text": f"{kind} at {start}", **kw}


def _line(a, b, text, speaker=None):
    u = {"start": a, "end": b, "text": text, "no_speech_prob": 0.05}
    if speaker:
        u["speaker"] = speaker
    return u


CHAIRLIFT = [
    _line(2.0, 4.0, "Nice day for it.", "S1"),
    _line(18.3, 19.8, "Yo bro, you got the pocket pizza?", "S1"),
    _line(19.9, 20.8, "Pocket pizza!", "S2"),
]


def _bin(tmp_path: Path, chair: str, telemetry_for_chair: bool = True) -> tuple[Path, Path, Path]:
    vis, aud, tel = tmp_path / "visual", tmp_path / "audio", tmp_path / "telemetry"
    for d in (vis, aud, tel):
        d.mkdir()
    drops = [_felt("speed_drop", t, t + 3.0, peak_g=2.0, stopped=False, check=True,
                   text=f"speed 8 to 2 m/s at {t:.0f}-{t + 3:.0f}") for t in (30.0, 90.0, 150.0)]
    (tel / f"{Path(GOPRO).stem}.felt.json").write_text(json.dumps({"schema": 2, "clip": GOPRO, "events": [
        *drops, _felt("air", 200.0, 200.4, text="air 0.4 s at 200.0-200.4 (landing 5.0 g)")]}))
    if telemetry_for_chair:
        (tel / f"{Path(chair).stem}.felt.json").write_text(json.dumps({"schema": 2, "clip": chair, "events": [
            {"kind": "lift", "who": "wearer", "start": 0.0, "end": 40.0, "t": 0.0, "source": "telemetry",
             "status": "felt", "check": False, "text": "lift? 0-40 (climbing 29 m)"}]}))
    (aud / f"{Path(GOPRO).stem}.audio.json").write_text(json.dumps({
        "clip": GOPRO, "duration_s": 240.0, "tracks": {"onset": []}, "transcript": []}))
    (aud / f"{Path(chair).stem}.audio.json").write_text(json.dumps({
        "clip": chair, "duration_s": 40.0, "tracks": {"onset": []}, "transcript": CHAIRLIFT}))
    return vis, aud, tel


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUGHCUT_LEDGER", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setenv("ROUGHCUT_BUDGET_USD", "15.0")
    inference.reset_spend()
    prev = te._DIR
    yield
    te.use_telemetry_dir(prev)
    inference.set_backend(None)


def _ranked(vis: Path, aud: Path, tel: Path) -> list[dict]:
    te.use_telemetry_dir(tel)
    return events.rank_bin(events.gather(vis, aud))


def _blocks(section: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    key = None
    for ln in section.splitlines():
        if ln.startswith("### "):
            key = ln[4:].strip()
            out[key] = []
        elif key and re.match(r"\s*\d+\. ", ln):
            out[key].append(ln)
    return out


def test_chairlift_talk_reaches_the_list(tmp_path):
    vis, aud, tel = _bin(tmp_path, LIFT)
    section = revise.events_section(_ranked(vis, aud, tel))
    blocks = _blocks(section)
    assert set(blocks) == {"Action", "People and B-roll"}
    assert any(LIFT in ln and "pocket pizza" in ln.lower() for ln in blocks["People and B-roll"]), section
    assert not any("pocket pizza" in ln.lower() for ln in blocks["Action"])
    rows = [ln for b in blocks.values() for ln in b]
    assert sum(1 for ln in rows if "felt" in ln.split("—")[0]) <= 6
    # the sensor's slowdowns are stops, not falls, and the header says what `felt` means
    flat = " ".join(section.split())
    assert "a sensor number (a speed drop, an air time) that nobody has looked at yet" in flat
    assert "[stop]" in section and "[fall]" not in section
    assert "biggest things anyone has seen" not in section


def test_phone_clip_reaches_the_list(tmp_path):
    """The same bin with the chairlift clip as a phone video: no telemetry sidecar at all."""
    vis, aud, tel = _bin(tmp_path, PHONE, telemetry_for_chair=False)
    assert te.telemetry_events(PHONE, tel) == []
    section = revise.events_section(_ranked(vis, aud, tel))
    people = _blocks(section)["People and B-roll"]
    assert any(PHONE in ln and "pocket pizza" in ln.lower() for ln in people), section
    # and the GoPro clip's action block is untouched by the phone clip being there
    assert any(GOPRO in ln for ln in _blocks(section)["Action"])


def test_first_cut_keeps_both(tmp_path):
    vis, aud, tel = _bin(tmp_path, LIFT)
    section = revise.events_section(_ranked(vis, aud, tel))
    blocks = _blocks(section)

    def first(block: str) -> dict:
        m = re.search(r"(\S+\.(?:MP4|MOV)) ([\d.]+)-([\d.]+)", blocks[block][0])
        return {"clip": m.group(1), "in": float(m.group(2)), "out": float(m.group(3)), "why": f"top of {block}"}

    class Scripted:
        name = "scripted"

        def __init__(self):
            self.requests = []

        def complete(self, request):
            self.requests.append(request)
            plan = json.dumps({"segments": [first("Action"), first("People and B-roll")]})
            model = config.model_for(request.role)
            return inference.Result(content=plan, input_tokens=1000, output_tokens=200, backend=self.name,
                                    model=model, projected_usd=config.projected_usd(model, 1000, 200),
                                    latency_ms=5, raw=plan)
    backend = Scripted()
    inference.set_backend(backend)
    clips = {GOPRO: {"clip": GOPRO, "duration": 240.0, "summary": {}, "transcript": []},
             LIFT: {"clip": LIFT, "duration": 40.0, "summary": {}, "transcript": CHAIRLIFT}}
    plan = revise.originate(clips, story="", note="", events=_ranked(vis, aud, tel))
    assert {s["clip"] for s in plan["segments"]} == {GOPRO, LIFT}     # validate_plan passed, both clips are in
    prompt = backend.requests[0].prompt
    assert prompt.index("pocket pizza?") < prompt.index("## Every clip available")
    assert "Loud does not mean the only thing worth keeping" in prompt
