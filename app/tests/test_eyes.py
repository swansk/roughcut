"""Wave 24, lane EYES: the prompt-box AI looks before it cuts on what the picture shows
(WORDS-08, -12, -13, -17). The model is the suite's scripted backend: it asks to look, the
server plans the frames under its cap, cuts contact sheets from the proxies, calls it a
second time with the images and the same conversation, and the record keeps what was
looked at. Stop mid-look leaves nothing; a plain ask never looks."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import assist, deep, inference, look  # noqa: E402

from test_assist import Scripted, ask, board, wait  # noqa: E402,F401


class Eyes(Scripted):
    """First asks to look; once the frames are attached (or on any ask that cannot look)
    answers with `then`. Keeps the images each call carried."""
    name = "eyes-scripted"

    def __init__(self, request=None, then=None, **kw):
        super().__init__(answer=self._pick, **kw)
        self.request = request or {"kind": "look", "looks": [{"shots": [1, 2], "reason": "see it", "density": "medium"}]}
        self.then = then or {"kind": "answer", "text": "x", "saw": ""}
        self.images: list[tuple] = []

    def _pick(self, prompt):
        return self.then if "## What you are now looking at" in prompt else self.request

    def complete(self, request):
        self.images.append(tuple(request.images))
        return super().complete(request)


@pytest.fixture
def eyes(board):
    """The assist board with proxies cut for its clips (the looks are cut from them)."""
    import server
    server.ensure_proxies(["CLIP_A.MP4", "CLIP_B.MP4"])
    return board


def assist_calls(m):
    return [i for i, s in enumerate(m.schemas) if "kind" in s]


def state_of(c):
    import server
    return server.assist_state()


# ---------------------------------------------------------------- the plan and its bounds

def seg(clip, i, o, **kw):
    return {"id": f"g{abs(hash((clip, i))) % 10**10:010d}", "clip": clip, "in": i, "out": o, **kw}


def cut_of(n=12, each=5.0):
    return {"segments": [seg(f"CLIP_{k:02d}.MP4", 0.0, each) for k in range(n)], "fps": 24.0}


def test_a_look_at_named_shots_is_cut_to_the_cap_and_says_so():
    st = cut_of(12, 5.0)                                     # 60 s
    p = look.plan(st, {"scan": False, "looks": [{"shots": [9, 10, 11], "density": "fine", "reason": "r"}]})
    assert len(p["frames"]) <= look.LOOK_CAP and p["capped"] is True      # 15 s at 4/s is 60: thinned
    assert {f["shot"] for f in p["frames"]} == {9, 10, 11}
    assert "the most I will look at in one ask is 45" in p["words"] and "shots 9-11" in p["words"]
    small = look.plan(st, {"scan": False, "looks": [{"film_in": 38.0, "film_out": 43.0, "density": "fine"}]})
    assert small["capped"] is False and len(small["frames"]) == 21        # 5 s at 4/s, ends included
    assert small["frames"][0]["film_t"] == 38.0 and small["frames"][-1]["film_t"] == 43.0
    assert "0:38.0–0:43.0" in small["words"] and "21 frames" in small["words"]


def test_many_ranges_never_pass_the_cap_and_the_last_are_dropped_if_they_must():
    st = cut_of(40, 6.0)
    items = [{"shots": [k], "density": "fine"} for k in range(1, 9)]
    p = look.plan(st, {"scan": False, "looks": items})
    assert len(p["frames"]) <= look.LOOK_CAP and p["capped"]
    wide = look.plan(st, {"scan": False, "looks": [{"film_in": 0, "film_out": 200, "density": "fine"}]})
    assert len(wide["frames"]) <= look.LOOK_CAP
    assert max(f["film_t"] for f in wide["frames"]) <= look.MAX_SPAN_S + 0.01      # a range is at most 30 s


def test_a_scan_looks_at_every_shot_at_about_a_frame_a_second_and_is_bounded():
    short = cut_of(6, 5.0)                                   # 30 s: a frame a second
    p = look.plan(short, {"scan": True, "looks": []})
    assert p["scan"] and len(p["frames"]) == 30 and not p["capped"]
    assert {f["shot"] for f in p["frames"]} == set(range(1, 7))
    assert "every shot in the cut, about a frame a second: 30 frames" == p["words"]
    long = cut_of(60, 5.0)                                   # 300 s: thinned to the cap, and says so
    q = look.plan(long, {"scan": True, "looks": []})
    assert len(q["frames"]) <= look.SCAN_CAP and q["capped"]
    assert "a frame every" in q["words"] and "the most I will look at in one ask is 90" in q["words"]
    assert q["sheets"] == -(-len(q["frames"]) // 9)


def test_film_time_maps_into_the_clip_through_speed_and_the_shot_in_point():
    st = {"segments": [seg("A.MP4", 10.0, 20.0), seg("B.MP4", 4.0, 8.0, speed=2.0)], "fps": 24.0}
    p = look.plan(st, {"scan": False, "looks": [{"film_in": 12.0, "film_out": 12.0}]})
    f = p["frames"][0]
    assert f["shot"] == 2 and f["clip"] == "B.MP4" and 4.0 <= f["clip_t"] <= 8.0
    g = look.plan(st, {"scan": False, "looks": [{"film_in": 3.0, "film_out": 3.0}]})["frames"][0]
    assert g["shot"] == 1 and g["clip_t"] == 13.0
    out = look.plan(st, {"scan": False, "looks": [{"film_in": 900.0, "film_out": 905.0}]})
    assert out["frames"] == [] and "nothing in the cut" in out["words"]          # past the end: nothing to look at


def test_a_clip_look_uses_clip_time_and_a_bad_request_is_refused():
    st = cut_of(2)
    p = look.plan(st, {"scan": False, "looks": [{"clip": "CLIP_09.MP4", "in": 3.0, "out": 5.0, "density": "medium"}]})
    assert [f["clip_t"] for f in p["frames"]] == [3.0, 3.5, 4.0, 4.5, 5.0] and all(f["film_t"] is None for f in p["frames"])
    with pytest.raises(ValueError):
        look.clean_request({"kind": "look", "looks": [], "scan": False})
    with pytest.raises(ValueError):
        look.clean_request({"kind": "look", "looks": [{"reason": "no where"}]})
    assert look.clean_request({"looks": [{"shot": 3, "density": "weird"}]})["looks"][0]["density"] == "medium"


# ---------------------------------------------------------------- the two steps through the server

def test_look_request_sheets_second_call_with_images_and_the_record(eyes):
    c, (a, b) = eyes
    m = Eyes(then={"kind": "ops", "summary": "Drops the 1 s after the jolt.", "from_picture": True,
                   "saw": "The frame at 0:01.0 is smeared sideways; 0:02.0 is steady.",
                   "ops": [{"op": "razor", "id": a, "at": 1.5}]})
    inference.set_backend(m)
    d = ask(c, "at about 0:01 there is a wobble, lose it")
    assert d["status"] == "done" and d["kind"] == "ops", d
    calls = assist_calls(m)
    assert len(calls) == 2
    assert m.images[calls[0]] == () and len(m.images[calls[1]]) >= 1          # the second call carries the sheets
    first, second = m.prompts[calls[0]], m.prompts[calls[1]]
    assert second.startswith(first) and "## What you are now looking at" in second
    assert "Each cell is one frame" in second and "0:01.0 s1" in second            # the labels it will read
    for img in m.images[calls[1]]:
        assert Path(img).exists() and Path(img).suffix == ".jpg" and Path(img).stat().st_size > 2000
    lk = d["looked"]
    assert lk["frames"] == len(lk["cells"]) and lk["sheets"] and lk["saw"].startswith("The frame at 0:01.0")
    assert lk["where"] in d["summary"] and d["summary"].startswith("Looked at ")
    assert d.get("from_descriptions") in (None, False) and "have not looked" not in d["summary"]
    assert d["cost"]["calls"] == 2 and "free on your plan" in d["cost"]["words"]
    rec = json.loads(next(Path(__import__("server").assist_home()).glob(f"{d['job']}.json")).read_text())
    assert rec["result"]["looked"]["request"]["looks"][0]["shots"] == [1, 2]
    assert Path(rec["result"]["looked"]["dir"], rec["result"]["looked"]["sheets"][0]).exists()


def test_the_running_job_shows_what_it_looks_at_with_the_frames_and_the_price(eyes):
    c, _ = eyes
    gate = threading.Event()
    m = Eyes(gate=None)
    orig = deep.extract_frames
    seen = {}

    def slow(*a, **k):
        gate.wait(10)
        return orig(*a, **k)
    import server
    deep_extract = deep.extract_frames
    deep.extract_frames = slow
    try:
        inference.set_backend(m)
        job = c.post("/api/assist", json={"text": "the horizon looks tilted", "scope": {"kind": "sequence"}}).json()["job"]
        end = time.time() + 10
        while time.time() < end and "look" not in (v := c.get(f"/api/assist/{job}").json()):
            time.sleep(0.05)
        lk = v["look"]
        seen.update(lk)
        assert lk["frames"] >= 1 and lk["stage"] == "cutting" and "frame" in lk["words"]
        assert lk["cost"]["words"] == "free on your plan" and lk["cap"] == look.LOOK_CAP
        gate.set()
        assert wait(c, job)["status"] == "done"
    finally:
        deep.extract_frames = deep_extract
        gate.set()


def test_stop_during_the_look_leaves_nothing_half_done(eyes):
    c, (a, b) = eyes
    import server
    m = Eyes(then={"kind": "ops", "summary": "x", "saw": "0:01.0 x", "ops": [{"op": "razor", "id": a, "at": 1.0}]})
    inference.set_backend(m)
    entered, gate = threading.Event(), threading.Event()
    orig = deep.extract_frames

    def blocking(*args, **kw):
        entered.set()
        gate.wait(10)
        return orig(*args, **kw)
    deep.extract_frames = blocking
    try:
        job = c.post("/api/assist", json={"text": "is the horizon level", "scope": {"kind": "sequence"}}).json()["job"]
        assert entered.wait(10)
        assert c.post(f"/api/assist/{job}/cancel").json()["status"] == "cancelled"
        gate.set()
        time.sleep(1.0)
    finally:
        deep.extract_frames = orig
        gate.set()
    d = c.get(f"/api/assist/{job}").json()
    assert d["status"] == "cancelled" and "ops" not in d and "looked" not in d
    assert len(assist_calls(m)) == 1                                   # the second call never went out
    rec = json.loads(server.assist_home().joinpath(f"{job}.json").read_text())
    assert rec["status"] == "cancelled" and "result" not in rec
    assert not server.looks_home(job).exists()                          # no half-built sheets
    assert server.assist_history(server.assist_cut()) == []             # not in the conversation either
    assert c.get("/api/project").json()["segments"][0]["id"] == a       # the cut is as it was


def test_one_look_round_only_a_second_look_is_refused_and_failed(eyes):
    c, _ = eyes
    inference.set_backend(Scripted({"kind": "look", "scan": True}))
    d = ask(c, "cut every bit where the camera points at the ground")
    assert d["status"] == "failed" and "cannot look again" in d["error"] or "validation failed" in d["error"]


def test_the_scan_is_one_kind_of_look_and_proposes_nothing_when_it_finds_none(eyes):
    c, _ = eyes
    m = Eyes(request={"kind": "look", "scan": True},
             then={"kind": "answer", "text": "I looked at every shot and none points at the ground or the sky.",
                   "saw": "Every frame, 0:00.0 to 0:03.5, is level."})
    inference.set_backend(m)
    d = ask(c, "cut every bit where the camera is pointing at the ground or the sky")
    assert d["kind"] == "answer" and "ops" not in d
    assert d["looked"]["scan"] is True and d["looked"]["frames"] >= 2
    assert "looked at every shot" in d["text"].lower()
    assert "every shot in the cut, about a frame a second" in d["looked"]["words"]


def test_an_edit_from_the_frames_must_say_when_it_saw_it(eyes):
    c, (a, b) = eyes
    m = Eyes(then={"kind": "ops", "summary": "Cuts it.", "saw": "a wobble somewhere", "ops": [{"op": "razor", "id": a, "at": 1.0}]})
    inference.set_backend(m)
    d = ask(c, "there is a wobble in shot 1, lose it")
    assert d["status"] == "failed" and "film time" in d["error"]
    assert len(assist_calls(m)) == 3                                    # look, answer, one re-ask


def test_the_next_turn_is_told_what_was_looked_at_and_what_was_seen(eyes):
    c, (a, b) = eyes
    m = Eyes(then={"kind": "answer", "text": "Looked, nothing jolts.", "saw": "0:01.0 steady; 0:02.0 steady."})
    inference.set_backend(m)
    ask(c, "is there a wobble in shot 1")
    m2 = Scripted({"kind": "answer", "text": "ok"})
    inference.set_backend(m2)
    ask(c, "and the second shot?")
    p = [p for p, s in zip(m2.prompts, m2.schemas) if "kind" in s][-1]
    assert "The AI looked at shots 1-2" in p and "and saw: 0:01.0 steady; 0:02.0 steady." in p


# ---------------------------------------------------------------- when it does not look

PLAIN = ["swap the last two shots", "trim shot 3 to 5 seconds", "end on the funniest line anyone says",
         "move shot 2 to the start", "make it 30 seconds", "put a 1 second dissolve between 1 and 2",
         "export a review copy", "what does he say in the first shot?", "add a marker at 0:12 called intro",
         "warmer colour on the last shot", "make the music quieter"]
PICTURE = ["at about 0:40 there is a wobble, lose it", "slow the biggest jump to half speed",
           "cut every bit where the camera is pointing at the ground or the sky",
           "lose every shot where nobody is in frame", "is the horizon level in shot 4",
           "find the shot where he waves", "the shaky bit near the end"]


def test_plain_asks_are_never_offered_a_look():
    for t in PLAIN:
        assert assist.look_allowed(t) is False, t
    for t in PICTURE:
        assert assist.look_allowed(t) is True, t


def test_a_plain_ask_gets_no_look_prompt_one_call_no_images(eyes):
    c, (a, b) = eyes
    for t in PLAIN[:6]:
        m = Eyes(then={"kind": "answer", "text": "done"})
        m.request = {"kind": "answer", "text": "plain answer"}              # a sensible model
        inference.set_backend(m)
        d = ask(c, t)
        assert d["status"] == "done" and "looked" not in d, t
        assert len(assist_calls(m)) == 1 and m.images[assist_calls(m)[0]] == (), t
        p = m.prompts[assist_calls(m)[0]]
        assert 'kind: "look"' not in p and "You have not looked at" in p, t


def test_a_model_that_insists_on_looking_at_a_plain_ask_gets_no_frames(eyes):
    c, _ = eyes
    import server
    m = Eyes()
    inference.set_backend(m)
    d = ask(c, "swap the last two shots")
    assert d["status"] == "failed"
    assert all(i == () for i in m.images)                                # no sheet was ever attached
    assert not (server.STATE["work"] / "looks").exists() or not any((server.STATE["work"] / "looks").rglob("sheet*.jpg"))


def test_a_picture_ask_is_offered_the_look_with_its_bounds(eyes):
    c, _ = eyes
    m = Scripted({"kind": "answer", "text": "x"})
    inference.set_backend(m)
    ask(c, "slow the biggest jump to half speed")
    p = [p for p, s in zip(m.prompts, m.schemas) if "kind" in s][-1]
    assert 'answer `kind: "look"`' in p and f"{look.LOOK_CAP} frames for ranges" in p and f"{look.SCAN_CAP} for a" in p
    assert "do NOT\nlook" in p or "do NOT look" in p.replace("\n", " ")
    assert "do not cut on it" in p                                      # the honesty line stays for what it does not look at


def test_no_look_on_a_picture_ask_keeps_the_honesty_line(eyes):
    c, (a, b) = eyes
    inference.set_backend(Scripted({"kind": "ops", "summary": "Slows shot 1.", "ops": [{"op": "razor", "id": a, "at": 1.5}]}))
    d = ask(c, "slow the biggest jump to half speed")
    assert d["summary"].startswith("From the written descriptions and the transcript; I have not looked")


def test_the_price_endpoint_still_answers_for_a_picture_ask(eyes):
    c, _ = eyes
    r = c.get("/api/assist/price", params={"text": "is the horizon level", "scope": json.dumps({"kind": "sequence"})})
    assert r.status_code == 200 and r.json()["words"] == "free on your plan"
