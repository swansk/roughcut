"""How the agent sees, and looking deeper (INTAKE M15) — `roughcut.deep` and its API.

Karl, 2026-10-03 (#4): *"make it clearer how the videos are indexed by the agent (e.g.
showing granularity), and make it easier to run deeper keyframe-based analysis (w/ AI
interpolating as needed between frames to really understand what is going on)."*

No live calls. The model is a scripted backend (`inference.set_backend`) that reads the
frame times out of the prompt and answers on them, so what is tested is everything
around the call: which frames are chosen, what the prompt says about them, what an
answer may claim, that the follow-up for more frames happens at most once and only when
asked, that a paid span is never bought twice, and how the rank believes it.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import config, deep, events, inference  # noqa: E402


def track_with_spikes(seconds: float = 60.0, hz: float = 10.0,
                      spikes: dict[float, float] | None = None) -> list[float]:
    t = [5.0 + 0.3 * ((i * 7) % 5) for i in range(int(seconds * hz))]
    for at, v in (spikes or {}).items():
        t[int(round(at * hz))] = v
    return t


# ------------------------------------------------------------------ keyframes

def test_keyframes_land_on_the_motion_peaks_and_keep_a_floor():
    track = track_with_spikes(spikes={20.3: 40.0, 24.0: 30.0})
    kf = deep.keyframes(track, 18.0, 30.0)
    times = [f["t"] for f in kf]
    assert times == sorted(times)
    assert all(18.0 <= t <= 30.0 for t in times)                 # inside the span
    assert len(kf) <= deep.FRAME_CAP
    # a frame on each spike (the impact), whatever the floor did
    assert any(abs(t - 20.3) <= 0.1 for t in times)
    assert any(abs(t - 24.0) <= 0.1 for t in times)
    assert any(f["why"] == "peak" for f in kf)
    # the floor: the quiet start and end are still sampled, and no gap is a hole
    assert times[0] == 18.0 and times[-1] >= 29.5
    assert max(b - a for a, b in zip(times, times[1:])) <= 1.01 * max(
        deep.FLOOR_MIN_S, 12.0 / int(deep.FRAME_CAP * deep.FLOOR_SHARE))
    # never two frames on the same instant
    assert min(b - a for a, b in zip(times, times[1:])) >= deep.MIN_GAP_S - 1e-6


def test_keyframes_respect_the_cap_and_still_take_the_biggest_change():
    track = track_with_spikes(spikes={20.3: 40.0})
    kf = deep.keyframes(track, 18.0, 30.0, cap=8)
    assert len(kf) == 8
    assert any(abs(f["t"] - 20.3) <= 0.1 for f in kf)


def test_keyframes_without_a_motion_track_are_even():
    kf = deep.keyframes(None, 2.0, 12.0)
    times = [f["t"] for f in kf]
    assert 2 <= len(kf) <= deep.FRAME_CAP and times[0] == 2.0 and times[-1] <= 12.0
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert max(gaps) - min(gaps) <= 0.6


def test_the_span_is_capped_and_kept_inside_the_clip():
    assert deep.clamp_span(10.0, 60.0, 300.0) == (10.0, 10.0 + deep.MAX_SPAN_S)
    assert deep.clamp_span(-3.0, 4.0, 6.0) == (0.0, 4.0)
    assert deep.clamp_span(5.8, 5.9, 6.0) == (5.0, 6.0)          # min span, clip end
    assert deep.clamp_span(8.0, 2.0, 6.0) == (2.0, 6.0)          # reversed, clamped


# ------------------------------------------------------------------ the prompt

def test_the_prompt_lists_the_frames_in_order_with_the_motion_between():
    track = track_with_spikes(spikes={20.3: 40.0})
    kf = deep.keyframes(track, 18.0, 30.0)
    prompt = deep.build_prompt("CLIP_X.MP4", 18.0, 30.0, kf, track)
    pos = [prompt.index(f"t={f['t']:.2f}s  file {deep.frame_name(f['t'])}") for f in kf]
    assert pos == sorted(pos)                                    # ordered, every one
    assert "peak 40.0 at 20.30s" in prompt                       # the spike, as a number
    assert "need_frames" in prompt and "camera roll" in prompt
    final = deep.build_prompt("CLIP_X.MP4", 18.0, 30.0, kf, track,
                              previous={"beats": []}, asked=[kf[1]["t"]])
    assert "**asked for**" in final and "must be empty" in final


# ------------------------------------------------------------------ validation

TIMES = [10.0, 11.0, 12.5, 14.0]


def answer(**over) -> dict:
    a = {"beats": [{"start": 10.0, "end": 10.5, "basis": "seen", "frame": 10.0,
                    "what": "rider on the slope"},
                   {"start": 11.2, "end": 12.3, "basis": "inferred",
                    "between": [11.0, 12.5], "what": "the rider goes down",
                    "why": "upright at 11.0, on the snow at 12.5, a 30-point change"}],
         "events": [{"kind": "fall", "start": 11.2, "end": 12.5, "confidence": "high",
                     "notable": True, "what": "rider down", "frames": [12.5]}],
         "camera": {"mount": "helmet", "roll": "level", "evidence": "11.0"},
         "unsure": [], "need_frames": [], "summary": "a fall"}
    a.update(over)
    return a


def test_a_good_answer_passes_and_is_snapped_to_the_frames():
    got = deep.validate(answer(need_frames=[11.7, 11.7, 13.0]), 10.0, 14.0, TIMES)
    assert [b["basis"] for b in got["beats"]] == ["seen", "inferred"]
    assert got["beats"][1]["between"] == [11.0, 12.5]
    assert got["need_frames"] == [11.7, 13.0]                    # deduped
    final = deep.validate(answer(need_frames=[11.7]), 10.0, 14.0, TIMES, allow_need=False)
    assert final["need_frames"] == []                            # never on the final


@pytest.mark.parametrize("bad, why", [
    ({"beats": [{"start": 11.2, "end": 12.0, "basis": "inferred", "what": "x",
                 "why": "y"}]}, "bracketing"),
    ({"beats": [{"start": 11.2, "end": 12.0, "basis": "inferred", "what": "x",
                 "between": [11.0, 11.7], "why": "y"}]}, "not one of the frames"),
    ({"beats": [{"start": 11.2, "end": 13.5, "basis": "inferred", "what": "x",
                 "between": [11.0, 12.5], "why": "y"}]}, "not between"),
    ({"beats": [{"start": 11.2, "end": 12.0, "basis": "inferred", "what": "x",
                 "between": [11.0, 12.5]}]}, "must say why"),
    ({"beats": [{"start": 9.0, "end": 10.2, "basis": "seen", "frame": 10.0,
                 "what": "x"}]}, "outside the span"),
    ({"beats": [{"start": 10.0, "end": 10.2, "basis": "seen", "frame": 10.4,
                 "what": "x"}]}, "not one of the frames"),
    ({"beats": [{"start": 10.0, "end": 10.2, "basis": "guessed", "frame": 10.0,
                 "what": "x"}]}, "basis"),
    ({"events": [{"kind": "backflip", "start": 11.0, "end": 12.0}]}, "not one of"),
    ({"events": [{"kind": "jump", "start": 13.0, "end": 15.0}]}, "outside the span"),
    ({"need_frames": [16.0]}, "outside the span"),
    ({"beats": []}, "non-empty"),
])
def test_validation_refuses_what_blurs_seen_and_inferred(bad, why):
    with pytest.raises(ValueError, match=why):
        deep.validate(answer(**bad), 10.0, 14.0, TIMES)


# ------------------------------------------------------------------ the look

class Scripted:
    """Answers on whatever frames the prompt lists; asks for more when told to."""

    def __init__(self, ask: list[list[float]] | None = None, events_: list | None = None):
        self.ask = list(ask or [])
        self.events = events_ or []
        self.seen: list = []

    def complete(self, request):
        self.seen.append(request)
        times = [float(t) for t in re.findall(r"t=([0-9.]+)s  file", request.prompt)]
        lo, hi = times[0], times[-1]
        payload = {
            "beats": [{"start": lo, "end": lo, "basis": "seen", "frame": lo,
                       "what": "the run-in"},
                      {"start": times[0], "end": times[1], "basis": "inferred",
                       "between": [times[0], times[1]], "what": "something moves",
                       "why": "the two frames differ"},
                      {"start": hi, "end": hi, "basis": "seen", "frame": hi,
                       "what": "after"}],
            "events": [{**e} for e in self.events],
            "camera": {"mount": "helmet", "roll": "the horizon rolls with the head",
                       "evidence": f"{lo}"},
            "unsure": ["the middle"],
            "need_frames": self.ask.pop(0) if self.ask else [],
            "summary": "scripted"}
        text = json.dumps(payload)
        return inference.Result(content=text, input_tokens=1000, output_tokens=100,
                                backend="scripted", model=config.model_for(request.role),
                                projected_usd=0.01, latency_ms=1, raw=text)


@pytest.fixture
def scripted():
    holder: dict = {}

    def use(backend):
        holder["b"] = backend
        inference.set_backend(backend)
        inference.reset_spend()
        return backend

    yield use
    inference.set_backend(None)


def test_the_follow_up_happens_once_and_only_when_asked(project, tmp_path, scripted):
    clip = project["footage"] / "CLIP_A.MP4"
    track = track_with_spikes(seconds=6.0, spikes={2.5: 40.0})

    quiet = scripted(Scripted())
    rec = deep.look(clip, "CLIP_A.MP4", 1.0, 5.0, frames_dir=tmp_path / "f1", mafd=track)
    assert len(quiet.seen) == 1 and not rec["followup"] and rec["asked"] == []
    assert rec["calls"] == 1 and rec["role"] == deep.DEEP_ROLE
    # each frame is its own file, at the frame's own resolution, handed over in order
    files = [Path(p).name for p in quiet.seen[0].images]
    assert files == [deep.frame_name(f["t"]) for f in rec["frames"]]
    assert all((tmp_path / "f1" / f).stat().st_size > 0 for f in files)

    # asks every time — still exactly one follow-up, carrying the frames it asked for:
    # the middles of the two widest gaps, which no chosen frame sits on
    times = [f["t"] for f in rec["frames"]]
    gaps = sorted(zip(times, times[1:]), key=lambda g: g[0] - g[1])[:2]
    want = sorted(round((a + b) / 2, 2) for a, b in gaps)
    greedy = scripted(Scripted(ask=[want, [want[0] + 0.1], [want[1] + 0.1]]))
    rec = deep.look(clip, "CLIP_A.MP4", 1.0, 5.0, frames_dir=tmp_path / "f2", mafd=track)
    assert len(greedy.seen) == 2 and rec["followup"] and rec["asked"] == want
    assert len(greedy.seen[1].images) == len(greedy.seen[0].images) + 2
    assert "**asked for**" in greedy.seen[1].prompt
    assert "need_frames` must be empty" in greedy.seen[1].prompt
    assert any(f["why"] == "asked" for f in rec["frames"])

    # asked, but the budget says no: one call, and the ask is recorded, not lost
    refused = scripted(Scripted(ask=[want[:1]]))
    rec = deep.look(clip, "CLIP_A.MP4", 1.0, 5.0, frames_dir=tmp_path / "f3", mafd=track,
                    may_follow_up=lambda usd: False)
    assert len(refused.seen) == 1 and rec["asked_but_refused"] == want[:1]


def test_a_stored_span_is_found_again_and_other_spans_are_kept(tmp_path):
    rec = {"start": 1.0, "end": 5.0, "beats": [], "events": [], "projected_usd": 0.5}
    deep.store(tmp_path, "CLIP_A", "CLIP_A.MP4", rec)
    deep.store(tmp_path, "CLIP_A", "CLIP_A.MP4", {**rec, "start": 10.0, "end": 14.0})
    d = deep.load(tmp_path, "CLIP_A")
    assert [s["start"] for s in d["spans"]] == [1.0, 10.0] and d["projected_usd"] == 1.0
    assert deep.covering(d, 1.1, 4.9) and deep.covering(d, 1.0, 5.2)
    assert deep.covering(d, 4.0, 11.0) is None


# ------------------------------------------------------------------ the rank

COARSE_JUMP = {"clip": "CLIP_X.MP4", "moments": [
    {"start": 10.0, "end": 12.0, "kind": "jump", "notable": True, "confidence": "high",
     "what": "skier upside-down mid-air", "frames": [10.0]}], "unusable": []}


def deep_file(events_: list[dict]) -> dict:
    return {"clip": "CLIP_X.MP4", "spans": [
        {"start": 8.0, "end": 16.0, "frames": [{"t": 8.0}], "events": events_,
         "beats": [{"start": 8.0, "end": 9.0, "basis": "seen", "frame": 8.0,
                    "what": "the horizon rolls"},
                   {"start": 9.0, "end": 11.0, "basis": "inferred", "between": [8.0, 12.0],
                    "what": "the camera keeps rolling", "why": "snow stays under the skis"}],
         "camera": {"mount": "helmet", "roll": "camera roll"}}]}


def test_a_deep_look_that_found_camera_roll_contradicts_the_jump_claim():
    ranked = events.rank_clip("CLIP_X.MP4", COARSE_JUMP, {}, None, deep=deep_file([]))
    jump = [e for e in ranked if e["source"] == "sheet"]
    assert len(jump) == 1 and jump[0]["why_ranked"]["confirmation"] == "contradicted"
    unseen = events.rank_clip("CLIP_X.MP4", COARSE_JUMP, {}, None)[0]
    assert jump[0]["score"] < unseen["score"]
    # and the audit has nothing left to buy there
    assert events.unaudited_claims(ranked) == []


def test_a_deep_look_that_found_the_jump_confirms_it_and_takes_its_slot():
    found = [{"kind": "jump", "start": 10.4, "end": 11.4, "confidence": "high",
              "notable": True, "what": "skier off the lip, skis level", "frames": [10.4]}]
    ranked = events.rank_clip("CLIP_X.MP4", COARSE_JUMP, {}, None, deep=deep_file(found))
    assert [e["source"] for e in ranked] == ["deep look"]
    assert ranked[0]["why_ranked"]["confirmation"] == "confirmed"
    assert events.deep_verdict(COARSE_JUMP["moments"][0], deep_file(found)) == "confirmed"
    # a fall under a jump claim is a refutation of the claim, not a confirmation
    fell = [{**found[0], "kind": "fall"}]
    assert events.deep_verdict(COARSE_JUMP["moments"][0], deep_file(fell)) == "contradicted"


def test_a_deep_event_nobody_claimed_ranks_on_its_own_weight():
    found = [{"kind": "fall", "start": 14.0, "end": 15.0, "confidence": "high",
              "notable": True, "what": "skis against the sky", "frames": [14.0]}]
    ranked = events.rank_clip("CLIP_X.MP4", {"moments": []}, {}, None,
                              deep=deep_file(found))
    assert ranked[0]["why_ranked"]["confirmation"] == "deep"
    assert ranked[0]["score"] == pytest.approx(
        events.KIND_WEIGHT["fall"] * events.NOTABLE * events.DEEP)
    assert events.UNSEEN < events.DEEP < events.CONFIRMED


def test_the_inventory_carries_the_deep_beats_for_those_seconds():
    merged = events.merge_moments(COARSE_JUMP["moments"], {}, deep_file([]))
    assert not [m for m in merged if m.get("kind") == "jump"]     # the claim is replaced
    beats = [m for m in merged if m.get("kind") == "beat"]
    assert [m["basis"] for m in beats] == ["seen", "inferred"]
    assert "inferred between 8s and 12s" in beats[1]["what"]
    assert "snow stays under the skis" in beats[1]["what"]


# ------------------------------------------------------------------ the API

def _fresh(tmp_path, project):
    from fastapi.testclient import TestClient
    import server

    server.configure(None, project["footage"], project["sidecars"], tmp_path,
                     proxies=False, visual=tmp_path / "visual")
    server.ensure_proxies(["CLIP_A.MP4"])
    return TestClient(server.app)


def _sidecars(visual: Path) -> None:
    visual.mkdir(parents=True, exist_ok=True)
    (visual / "CLIP_A.visual.json").write_text(json.dumps({
        "clip": "CLIP_A.MP4", "moments": [
            {"start": 2.0, "end": 3.0, "kind": "jump", "notable": True,
             "what": "rider mid-air", "frames": [2.0]}],
        "unusable": [], "summary": "", "sheets_read": 1, "sheets_total": 1,
        "params": {"interval_s": 2.0, "cols": 6, "rows": 5, "role": "analysis"}}),
        encoding="utf-8")
    (visual / "CLIP_A.fine.json").write_text(json.dumps({
        "clip": "CLIP_A.mp4", "mode": "fine", "windows_read": [[0.0, 4.0]],
        "frames_sampled": [0.0, 1.0, 2.0, 3.0, 4.0], "moments": [], "unusable": [],
        "params": {"interval_s": 1.0, "cols": 3, "rows": 5, "width": 480,
                   "role": "judge", "model": config.DEEP_MODEL}}), encoding="utf-8")


def test_coverage_says_what_each_layer_looked_at_and_how(tmp_path, project):
    with _fresh(tmp_path, project) as c:
        import server
        _sidecars(server.STATE["visual"])
        cov = c.get("/api/coverage/CLIP_A.MP4").json()
        assert cov["clip"] == "CLIP_A.MP4" and cov["duration"] == pytest.approx(6.0)
        L = cov["layers"]
        assert set(L) == {"heard", "coarse", "close", "deep", "motion"}
        assert L["heard"]["utterances"][0] == [0.5, 2.0] and L["heard"]["words"] == 6
        # coarse: no frame times recorded — derived from the interval, and said so
        assert L["coarse"]["interval_s"] == 2.0 and L["coarse"]["frames"] == [0, 2, 4]
        assert L["coarse"]["frames_recorded"] is False
        assert L["coarse"]["model"] is None and L["coarse"]["role"] == "analysis"
        assert L["coarse"]["width"] is None                       # not recorded, not guessed
        assert L["close"]["windows"] == [[0.0, 4.0]] and L["close"]["frames_recorded"]
        assert L["close"]["model"] == config.DEEP_MODEL
        assert L["deep"]["read"] is False and L["deep"]["cap"] == deep.FRAME_CAP
        assert L["motion"]["cached"] is False
        # the claim and the close look's verdict on it
        assert cov["moments"][0]["kind"] == "jump"
        assert cov["moments"][0]["status"] == "unsupported"
        # the bin form, for the open screen's cards
        b = c.get("/api/coverage").json()["clips"]
        assert set(b) == {"CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"}
        assert b["CLIP_A.MP4"]["close"] == [[0.0, 4.0]] and b["CLIP_B.MP4"]["close"] == []
        assert c.get("/api/coverage/NOPE.MP4").status_code == 404


def test_the_dry_run_prices_and_the_cap_refuses(tmp_path, project, monkeypatch):
    calls: list = []

    class Never:
        def complete(self, request):
            calls.append(request)
            raise AssertionError("a dry run must not call the model")

    inference.set_backend(Never())
    try:
        with _fresh(tmp_path, project) as c:
            dry = c.post("/api/deep", json={"clip": "CLIP_A.MP4", "start": 1.0,
                                            "end": 5.0, "dry_run": True}).json()
            assert dry["job"] is None and dry["cached"] is False
            assert dry["frames"] == deep.FRAME_CAP           # no motion cached: the bound
            q = deep.quote(deep.FRAME_CAP)
            assert dry["projected_usd"] == q["projected_usd"] > 0
            assert dry["max_usd"] == q["max_usd"] > dry["projected_usd"]
            long = c.post("/api/deep", json={"clip": "CLIP_A", "start": 0,
                                             "end": 99, "dry_run": True}).json()
            assert long["end"] == pytest.approx(6.0)        # clamped to the clip
            many = c.post("/api/deep/quote", json={"spans": [
                {"clip": "CLIP_A.MP4", "start": 1, "end": 3},
                {"clip": "NOPE", "start": 1, "end": 3}]}).json()["quotes"]
            assert many[0]["projected_usd"] > 0 and "error" in many[1]
            monkeypatch.setenv("ROUGHCUT_BUDGET_USD", "0.01")
            r = c.post("/api/deep", json={"clip": "CLIP_A.MP4", "start": 1.0, "end": 5.0})
            assert r.status_code == 409 and "budget cap" in r.json()["detail"]
            assert not calls
    finally:
        inference.set_backend(None)


def _wait(c, job: str) -> dict:
    for _ in range(300):
        s = c.get(f"/api/job/{job}").json()
        if s["state"] in ("done", "failed"):
            return s
        time.sleep(0.05)
    raise AssertionError("the deep look did not finish")


def test_a_deep_look_runs_as_a_job_writes_its_file_and_is_never_bought_twice(
        tmp_path, project, scripted):
    import server

    found = [{"kind": "fall", "start": 2.0, "end": 3.0, "confidence": "high",
              "notable": True, "what": "rider down on the snow", "frames": []}]
    backend = scripted(Scripted(events_=found))
    with _fresh(tmp_path, project) as c:
        _sidecars(server.STATE["visual"])
        r = c.post("/api/deep", json={"clip": "CLIP_A.MP4", "start": 1.0, "end": 5.0})
        assert r.status_code == 200, r.text
        s = _wait(c, r.json()["job"])
        assert s["state"] == "done", s
        assert s["kind"] == "deep" and "beats" in s["detail"]
        assert len(backend.seen) == 1

        d = json.loads((server.STATE["visual"] / "CLIP_A.deep.json").read_text())
        span = d["spans"][0]
        assert (span["start"], span["end"]) == (1.0, 5.0)
        assert span["model"] == config.model_for(deep.DEEP_ROLE)
        assert span["frames"] and all((server.deep_frames_dir("CLIP_A") / f["file"]).exists()
                                      for f in span["frames"])
        # the motion track was measured on the way (free) and cached for next time
        assert (server.STATE["visual"] / "CLIP_A.motion.json").exists()
        # the frames are served, and nothing else is
        f0 = span["frames"][0]["file"]
        assert c.get(f"/media/deep/CLIP_A/{f0}").headers["content-type"] == "image/jpeg"
        assert c.get("/media/deep/CLIP_A/..%2Fsecret.jpg").status_code == 404

        # the same span again: cached, free, no job, no call
        again = c.post("/api/deep", json={"clip": "CLIP_A.MP4", "start": 1.0,
                                          "end": 5.0}).json()
        assert again["cached"] is True and again["job"] is None
        assert again["projected_usd"] == 0.0 and len(backend.seen) == 1

        # the rank believes it: the coarse jump claim at 2-3 was a fall, so contradicted
        ranked = c.get("/api/project").json()["events"]
        assert [e for e in ranked if e["source"] == "deep look"][0][
            "why_ranked"]["confirmation"] == "deep"
        assert [e for e in ranked if e["source"] == "sheet"][0][
            "why_ranked"]["confirmation"] == "contradicted"
        # the Ask's inventory carries the beats for those seconds
        moments = c.get("/api/project").json()["clips"]["CLIP_A.MP4"]["visual"]["moments"]
        assert any(m.get("kind") == "beat" and m.get("basis") == "inferred"
                   for m in moments)
        # and the coverage strip shows the span
        cov = c.get("/api/coverage/CLIP_A.MP4").json()
        assert cov["layers"]["deep"]["spans"][0]["inferred"] == 1
        got = c.get("/api/deep/CLIP_A.MP4").json()
        assert got["spans"][0]["beats"] and got["frames_url"] == "/media/deep/CLIP_A/"
