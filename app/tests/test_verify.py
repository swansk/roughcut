"""Wave 20 VERIFY: routing, modes, demotion, verdict handling, the strip, the first-pass
hook and the mode endpoint. The model is a scripted backend; the strip is built from the
synthetic test clip by the real ffmpeg path."""

from __future__ import annotations

import json
import time
from pathlib import Path

import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import config, inference, verify  # noqa: E402


def air(clip="CLIP_A.MP4", t=2.0, **kw):
    e = {"clip": clip, "kind": "air", "who": "wearer", "start": t - 0.2, "end": t + 0.2,
         "t": t, "source": "telemetry", "status": "felt", "check": True, "air_s": 0.4,
         "min_g": 0.3, "landing_g": 4.0, "text": f"air 0.40 s at {t - 0.2}-{t + 0.2}"}
    e.update(kw)
    e["event_id"] = verify.event_id(e)
    return e


def impact(clip="CLIP_A.MP4", t=3.0, **kw):
    e = {"clip": clip, "kind": "impact", "who": "wearer", "start": t, "end": t + 0.1, "t": t,
         "source": "telemetry", "status": "felt", "check": True, "peak_g": 9.0,
         "handling": False, "text": f"impact 9 g at {t}"}
    e.update(kw)
    e["event_id"] = verify.event_id(e)
    return e


def riding(clip="CLIP_A.MP4", t=1.0):
    e = {"clip": clip, "kind": "riding", "start": t, "end": t + 5, "t": t, "check": False}
    e["event_id"] = verify.event_id(e)
    return e


# ------------------------------------------------------------------- routing

def test_hybrid_routes_every_air_and_only_doubtful_falls():
    a = air()
    sure_no = impact(t=10, local={"p_yes": 0.05}, handling=True)       # local+sensor agree: no
    band = impact(t=20, local={"p_yes": 0.5})                           # in the 0.2-0.9 band
    agree = impact(t=30, local={"p_yes": 0.95})                         # settled yes, sensor yes
    disagree = impact(t=40, local={"p_yes": 0.97}, handling=True)        # local yes, sensor: handling
    rows = verify.route([a, sure_no, band, agree, disagree, riding()], mode="hybrid",
                        local_ok=True, heroes=[])
    ids = [r["event_id"] for r in rows]
    assert a["event_id"] in ids and band["event_id"] in ids and disagree["event_id"] in ids
    assert sure_no["event_id"] not in ids and agree["event_id"] not in ids
    assert all(r["route_reasons"] for r in rows)


def test_used_by_the_cut_and_heroes_are_always_routed():
    settled = impact(t=10, local={"p_yes": 0.97})
    hero = impact(t=20, local={"p_yes": 0.97}, peak_g=20.0)
    rows = verify.route([settled, hero], mode="hybrid", local_ok=True,
                        used=[settled["event_id"]], heroes=[hero["event_id"]])
    reasons = {r["event_id"]: r["route_reasons"] for r in rows}
    assert "the cut uses it" in reasons[settled["event_id"]]
    assert "one of the top shots" in reasons[hero["event_id"]]
    # used first, then hero
    assert [r["event_id"] for r in rows][0] == settled["event_id"]


def test_local_only_routes_nothing_and_marks_unchecked():
    a, i = air(), impact()
    assert verify.route([a, i], mode="local", local_ok=True) == []
    row = verify.annotate_event(a, {"verdicts": {}}, mode="local", local_ok=True)
    assert row["certainty"] == "unchecked" and row["asserted"]


def test_hybrid_without_a_local_stack_acts_as_claude_only_and_says_so():
    assert verify.effective_mode("hybrid", False) == "claude"
    assert verify.effective_mode("hybrid", True) == "hybrid"
    assert verify.effective_mode("local", False) == "local"
    p = verify.mode_payload("hybrid", False)
    assert p["effective"] == "claude" and "No local model" in p["note"]
    rows = verify.route([impact(t=10), impact(t=20, check=False)], mode="hybrid",
                        local_ok=False, heroes=[])
    assert [r["event_id"] for r in rows] == [impact(t=10)["event_id"]]
    assert "no local model" in rows[0]["route_reasons"][0]


def test_route_respects_the_limit_and_priority_and_skips_known_verdicts():
    evs = [air(t=float(i)) for i in range(1, 60)]
    done = {evs[0]["event_id"]: {"verdict": "confirm"}}
    rows = verify.route(evs, mode="claude", local_ok=False, limit=40, verdicts=done)
    assert len(rows) == 40 and evs[0]["event_id"] not in {r["event_id"] for r in rows}
    used = evs[50]["event_id"]
    rows = verify.route(evs, mode="claude", local_ok=False, used=[used], limit=5)
    assert rows[0]["event_id"] == used


def test_price_is_per_call_and_carries_the_cost_terms():
    p = verify.price(40)
    assert p["n"] == 40 and p["usd"] == pytest.approx(40 * verify.per_call_usd(), abs=1e-3)
    assert p["charged"] is False and p["backend"] == "claude_cli"
    assert "40" in p["label"]
    assert config.model_for(config.ROLE_VERIFY) == config.QUICK_MODEL


# ------------------------------------------------------------------ verdicts

def test_validate_and_low_confidence_becomes_unsure():
    v = verify.validate({"verdict": "Confirm", "kind": "air", "who": "other", "start": "1.5",
                         "end": 2, "confidence": "medium", "why": "x"})
    assert v["verdict"] == "confirm" and v["who"] == "other" and v["start"] == 1.5
    low = verify.settle({**v, "confidence": "low"})
    assert low["verdict"] == "unsure" and low["raw_verdict"] == "confirm"
    with pytest.raises(ValueError):
        verify.validate({"verdict": "maybe"})
    assert verify.validate({"verdict": "reject", "kind": "banana"})["kind"] == "other"


def test_certainty_follows_the_verdict():
    a = air()
    st = {"verdicts": {a["event_id"]: {"verdict": "confirm", "who": "other", "kind": "air"}}}
    r = verify.annotate_event(a, st, mode="hybrid", local_ok=False)
    assert r["certainty"] == "claude" and r["who"] == "other" and r["asserted"]
    assert r["evidence"] == ["telemetry", "claude"]
    for verdict, cert, asserted in (("reject", "rejected", False), ("unsure", "unsure", False),
                                    ("retime", "claude", True)):
        st = {"verdicts": {a["event_id"]: {"verdict": verdict, "kind": "fall"}}}
        r = verify.annotate_event(a, st, mode="hybrid", local_ok=False)
        assert (r["certainty"], r["asserted"]) == (cert, asserted)
    plain = verify.annotate_event(impact(local={"p_yes": 0.97}), {"verdicts": {}},
                                  mode="hybrid", local_ok=True)
    assert plain["certainty"] == "local"
    assert verify.annotate_event(a, {"verdicts": {}}, mode="hybrid",
                                 local_ok=True)["certainty"] == "telemetry"


# ------------------------------------------------------------------- demotion

def test_unsupported_claims_are_demoted_not_deleted():
    moments = [
        {"start": 8.0, "end": 8.0, "kind": "jump", "what": "airborne mid-trick"},
        {"start": 22.0, "end": 23.0, "kind": "fall", "what": "falls"},
        {"start": 50.0, "end": 52.0, "kind": "scenery", "what": "a valley"},
        {"start": 60.0, "end": 61.0, "kind": "action", "what": "skis inverted over a cliff"},
        {"start": 100.0, "end": 100.4, "kind": "jump", "what": "jump with sensor behind it"},
    ]
    tel = [air(t=100.2)]
    asserted, held = verify.demote(moments, "CLIP_A.MP4", {"verdicts": {}}, tel)
    assert [m["kind"] for m in asserted] == ["scenery", "jump"]
    assert asserted[1]["claim_status"] == "supported"
    assert asserted[1]["supported_by"] == ["telemetry:air"]
    assert {m["start"] for m in held} == {8.0, 22.0, 60.0}
    assert all(m["claim_status"] == "unsupported" and not m["asserted"] for m in held)


def test_a_claim_is_supported_by_its_own_family_only_and_not_by_handling():
    jump = {"start": 5.0, "end": 5.5, "kind": "jump", "what": "x"}
    fall = {"start": 5.0, "end": 5.5, "kind": "fall", "what": "x"}
    tel = [impact(t=5.2)]
    assert verify.demote([jump], "CLIP_A.MP4", {"verdicts": {}}, tel)[0] == []
    assert len(verify.demote([fall], "CLIP_A.MP4", {"verdicts": {}}, tel)[0]) == 1
    hand = [impact(t=5.2, handling=True)]
    assert verify.demote([fall], "CLIP_A.MP4", {"verdicts": {}}, hand)[0] == []


def test_checker_verdicts_settle_claims_and_overrule_the_sensor():
    jump = {"start": 5.0, "end": 5.5, "kind": "jump", "what": "x"}
    cid = verify.claim_id("CLIP_A.MP4", "jump", 5.0)
    a = air(t=5.2)
    # confirmed by Claude with no sensor
    ok = {"verdicts": {cid: {"verdict": "confirm"}}}
    asserted, held = verify.demote([jump], "CLIP_A.MP4", ok, [])
    assert asserted and asserted[0]["supported_by"] == ["claude"] and not held
    # rejected: kept, not asserted
    no = {"verdicts": {cid: {"verdict": "reject"}}}
    asserted, held = verify.demote([jump], "CLIP_A.MP4", no, [a])
    assert not asserted and held[0]["claim_status"] == "rejected"
    # the sensor event was itself rejected by Claude: it no longer backs the claim
    st = {"verdicts": {a["event_id"]: {"verdict": "reject"}}}
    assert verify.demote([jump], "CLIP_A.MP4", st, [a])[0] == []
    # unsure is not support
    st = {"verdicts": {cid: {"verdict": "unsure"}}}
    assert verify.demote([jump], "CLIP_A.MP4", st, [])[0] == []


def test_claim_rows_list_only_unchecked_unsupported_claims():
    moments = {"CLIP_A.MP4": [
        {"start": 8.0, "end": 8.0, "kind": "jump", "what": "a"},
        {"start": 22.0, "end": 23.0, "kind": "fall", "what": "b"},
        {"start": 30.0, "end": 31.0, "kind": "crash", "what": "c"}]}
    st = {"verdicts": {verify.claim_id("CLIP_A.MP4", "fall", 22.0): {"verdict": "reject"}}}
    rows = verify.claim_rows(moments, st, tel_of=lambda c: [impact(t=30.05)])
    assert [r["start"] for r in rows] == [8.0]          # fall already checked, crash backed
    assert rows[0]["kind"] == "air" and rows[0]["event_id"].startswith("CLIP_A:claim:")


# ------------------------------------------------------------ the strip + checks

class Scripted:
    name = "scripted"

    def __init__(self, answers):
        self.answers, self.seen = list(answers), []

    def complete(self, request):
        self.seen.append(request)
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        text = json.dumps(a)
        return inference.Result(content=text, input_tokens=11000, output_tokens=300,
                                backend="scripted", model=config.model_for(request.role),
                                projected_usd=0.04, latency_ms=1, raw=text)


@pytest.fixture
def scripted():
    made = []

    def install(answers):
        b = Scripted(answers)
        inference.set_backend(b)
        made.append(b)
        return b
    yield install
    inference.set_backend(None)


def test_strip_is_one_labelled_image_inside_the_size_limit(project, tmp_path):
    from PIL import Image
    video = project["footage"] / "CLIP_A.MP4"
    s = verify.build_strip(video, "CLIP_A.MP4", 2.0, 2.4, tmp_path / "s.jpg", duration=6.0)
    im = Image.open(s.path)
    assert max(im.size) <= verify.MAX_SIDE and len(s.times) == 24
    assert s.times[0] == pytest.approx(0.7, abs=0.01) and s.source in ("ffmpeg", "localvlm")
    assert im.size[0] == 6 * 261            # 6 columns of cells


def test_a_claude_check_sends_one_image_the_facts_and_stores_the_verdict(project, tmp_path,
                                                                         scripted):
    b = scripted([{"verdict": "confirm", "kind": "air", "who": "wearer", "start": 1.9,
                   "end": 2.3, "confidence": "high", "why": "skis clear of snow 2.0-2.3"}])
    a = air(local={"p_yes": 0.4})
    st = {"verdicts": {}, "mode": "claude"}
    saved = []
    out = verify.run_checks([a], video_of=lambda c: project["footage"] / c, workdir=tmp_path,
                            state=st, save=lambda s: saved.append(dict(s["verdicts"])),
                            duration_of=lambda c: 6.0)
    assert out["calls"] == 1 and out["confirmed"] == 1 and saved
    req = b.seen[0]
    assert req.role == config.ROLE_VERIFY and req.isolated is True and len(req.images) == 1
    assert "air 0.40 s" in req.prompt and "min" in req.prompt and "P(yes)=0.40" in req.prompt
    assert "Reading strength: WEAK" in req.prompt and "roll" in req.prompt.lower()
    assert st["verdicts"][a["event_id"]]["verdict"] == "confirm"


def test_a_low_confidence_answer_is_stored_as_unsure(project, tmp_path, scripted):
    scripted([{"verdict": "confirm", "kind": "fall", "who": "wearer", "start": 1, "end": 2,
               "confidence": "low", "why": "aftermath only"}])
    st = {"verdicts": {}}
    verify.run_checks([impact()], video_of=lambda c: project["footage"] / c, workdir=tmp_path,
                      state=st)
    v = next(iter(st["verdicts"].values()))
    assert v["verdict"] == "unsure" and v["raw_verdict"] == "confirm"


def test_a_failed_call_is_reported_and_the_batch_goes_on(project, tmp_path, scripted):
    class Boom(Scripted):
        def complete(self, request):
            if len(self.seen) == 0:
                self.seen.append(request)
                raise inference.InferenceError("claude CLI timed out")
            return super().complete(request)
    b = Boom([{"verdict": "reject", "kind": "none", "who": "wearer", "confidence": "high",
               "why": "nothing"}])
    inference.set_backend(b)
    st = {"verdicts": {}}
    rows = [air(t=2.0), air(t=3.0)]
    out = verify.run_checks(rows, video_of=lambda c: project["footage"] / c, workdir=tmp_path,
                            state=st)
    assert out["calls"] == 1 and len(out["errors"]) == 1


def test_first_pass_checks_the_events_the_cut_uses_and_retimes(project, tmp_path, scripted,
                                                              monkeypatch):
    a1, a2 = air(t=2.0), air(clip="CLIP_B.MP4", t=1.0)
    verify.set_providers(telemetry=lambda c: [a1] if c == "CLIP_A.MP4" else [a2])
    try:
        scripted([
            {"verdict": "retime", "kind": "air", "who": "wearer", "start": 3.5, "end": 4.0,
             "confidence": "high", "why": "lands later"},
            {"verdict": "reject", "kind": "none", "who": "wearer", "start": None, "end": None,
             "confidence": "high", "why": "ordinary riding"}])
        plan = {"segments": [
            {"id": "s1", "clip": "CLIP_A.MP4", "in": 1.5, "out": 3.0},
            {"id": "s2", "clip": "CLIP_B.MP4", "in": 0.5, "out": 2.0}]}
        st = {"mode": "claude", "verdicts": {}}
        rec = verify.verify_cut(plan, ["CLIP_A.MP4", "CLIP_B.MP4"], st, mode="claude",
                                video_of=lambda c: project["footage"] / c, workdir=tmp_path,
                                duration_of=lambda c: 6.0, local_ok=False)
    finally:
        verify.set_providers()
    assert rec["calls"] == 2 and rec["retimed"] + rec["rejected"] == 2
    verdict_of = {k: v["verdict"] for k, v in st["verdicts"].items()}
    assert set(verdict_of.values()) == {"retime", "reject"}
    flagged = [s for s in plan["segments"] if s.get("unverified")]
    widened = [s for s in plan["segments"] if s["out"] > 3.0]
    assert len(flagged) == 1 and len(widened) == 1
    assert "Claude checked 2 moments" in rec["said"]


def test_a_locked_shot_is_not_retimed(project, tmp_path, scripted):
    a1 = air(t=2.0)
    verify.set_providers(telemetry=lambda c: [a1])
    try:
        scripted([{"verdict": "retime", "kind": "air", "who": "wearer", "start": 4.0,
                   "end": 4.5, "confidence": "high", "why": "x"}])
        plan = {"segments": [{"id": "s1", "clip": "CLIP_A.MP4", "in": 1.5, "out": 3.0}]}
        verify.verify_cut(plan, ["CLIP_A.MP4"], {"mode": "claude", "verdicts": {}},
                          mode="claude", video_of=lambda c: project["footage"] / c,
                          workdir=tmp_path, locked=["s1"], local_ok=False)
    finally:
        verify.set_providers()
    assert plan["segments"][0]["out"] == 3.0


def test_local_only_first_pass_makes_no_call(project, tmp_path, scripted):
    b = scripted([{"verdict": "confirm"}])
    verify.set_providers(telemetry=lambda c: [air()])
    try:
        rec = verify.verify_cut({"segments": []}, ["CLIP_A.MP4"], {"mode": "local",
                                "verdicts": {}}, mode="local",
                                video_of=lambda c: project["footage"] / c, workdir=tmp_path)
    finally:
        verify.set_providers()
    assert b.seen == [] and rec["calls"] == 0 and "Local only" in rec["said"]
    assert verify.first_pass_price("local", ["CLIP_A.MP4"], {"verdicts": {}})["usd"] == 0.0


def test_first_pass_price_is_capped_at_forty_calls():
    verify.set_providers(telemetry=lambda c: [air(clip=c, t=float(i)) for i in range(1, 80)])
    try:
        p = verify.first_pass_price("hybrid", ["CLIP_A.MP4"], {"verdicts": {}}, local_ok=False)
    finally:
        verify.set_providers()
    assert p["n"] == 40


def test_state_roundtrip_and_merge(tmp_path):
    path = verify.state_path(tmp_path, "bin")
    assert verify.load_state(path)["mode"] == "hybrid"
    verify.set_mode(path, "local")
    verify.merge_verdicts(path, {"a": {"verdict": "reject"}})
    verify.merge_verdicts(path, {"b": {"verdict": "confirm"}})
    st = verify.load_state(path)
    assert st["mode"] == "local" and set(st["verdicts"]) == {"a", "b"}
    with pytest.raises(ValueError):
        verify.set_mode(path, "turbo")


# ---------------------------------------------------------------------- the API

def test_mode_endpoint_defaults_to_hybrid_and_persists(client):
    r = client.get("/api/index/mode")
    assert r.status_code == 200
    j = r.json()
    assert j["mode"] == "hybrid" and j["default"] == "hybrid" and j["modes"] == [
        "hybrid", "local", "claude"]
    assert j["effective"] in ("hybrid", "claude") and j["note"]
    r = client.put("/api/index/mode", json={"mode": "local"})
    assert r.status_code == 200 and r.json()["mode"] == "local" and r.json()["effective"] == "local"
    assert client.get("/api/index/mode").json()["mode"] == "local"
    assert client.get("/api/index").json()["mode"]["mode"] == "local"
    assert client.get("/api/verify").json()["mode"]["mode"] == "local"
    assert client.put("/api/index/mode", json={"mode": "claude"}).json()["effective"] == "claude"
    assert client.put("/api/index/mode", json={"mode": "turbo"}).status_code == 400
    assert client.put("/api/index/mode", json={}).status_code == 400
    client.put("/api/index/mode", json={"mode": "hybrid"})


def test_unsupported_claims_never_reach_the_payload_and_a_batch_can_settle_them(
        client, project, scripted):
    import server
    vis = project["work"] / "verify-visual"
    vis.mkdir(exist_ok=True)
    (vis / "CLIP_A.visual.json").write_text(json.dumps({
        "clip": "CLIP_A.MP4", "summary": "", "unusable": [],
        "moments": [
            {"start": 1.0, "end": 1.2, "kind": "jump", "notable": True, "what": "airborne"},
            {"start": 3.0, "end": 4.0, "kind": "scenery", "notable": False, "what": "trees"}]}),
        encoding="utf-8")
    old = server.STATE["visual"]
    server.STATE["visual"] = vis
    server.verifymod.save_state(server.verify_path(), {"mode": "claude", "verdicts": {}})
    try:
        clip = client.get("/api/clips").json() if False else None   # noqa: F841
        payload = server.project_payload()
        v = payload["clips"]["CLIP_A.MP4"]["visual"]
        assert [m["kind"] for m in v["moments"]] == ["scenery"]
        assert [m["claim_status"] for m in v["unsupported"]] == ["unsupported"]
        summary = client.get("/api/verify").json()
        assert summary["counts"]["claims_to_check"] == 1
        assert summary["claims_check"]["n"] == 1 and summary["claims_check"]["usd"] > 0
        scripted([{"verdict": "reject", "kind": "none", "who": "wearer", "start": None,
                   "end": None, "confidence": "high", "why": "ordinary riding"}])
        r = client.post("/api/verify/claims", json={})
        assert r.status_code == 200 and r.json()["price"]["n"] == 1
        for _ in range(100):
            j = client.get("/api/verify").json()["job"]
            if j and j["state"] in ("done", "failed"):
                break
            time.sleep(0.1)
        assert j["state"] == "done", j
        after = client.get("/api/verify").json()
        assert after["unsupported"][0]["claim_status"] == "rejected"
        assert after["counts"]["claims_to_check"] == 0
        assert client.post("/api/verify/claims", json={}).status_code == 400
    finally:
        server.STATE["visual"] = old
        verifymod_path = server.verify_path()
        if verifymod_path.exists():
            verifymod_path.unlink()


def test_reading_strength_separates_a_true_free_fall_from_an_unweighting():
    assert verify.strength(air(min_g=0.03, landing_g=4.0)).startswith("STRONG")
    assert verify.strength(air(min_g=0.31, landing_g=3.4)).startswith("WEAK")
    assert verify.strength(air(min_g=0.05, landing_g=1.2)).startswith("WEAK")
    assert verify.strength(impact()) == ""


def test_a_deep_looks_own_events_are_not_claims_to_demote():
    deep = {"start": 3.0, "end": 4.0, "kind": "fall", "deep": True, "what": "deep look: fell"}
    asserted, held = verify.demote([deep], "CLIP_A.MP4", {"verdicts": {}}, [])
    assert asserted == [deep] and held == []


# ------------------------------------------------- the 3080's checks join the events

def lc(kind, t, p, **kw):
    return {"t": t, "cand": {"kind": kind, "start": t, "end": t + 0.1, "t": t}, "q": "fall",
            "p_yes": p, "verdict": "x", "threshold": 0.2, "frames": {"n": 30}, **kw}


def test_local_checks_join_by_kind_family_and_time_and_settle_falls():
    no, band, yes = impact(t=10.0), impact(t=20.0), impact(t=30.0)
    a = air(t=40.0)
    checks = [lc("impact", 10.2, 0.04), lc("speed_drop", 20.0, 0.5), lc("impact", 30.0, 0.95),
              lc("air", 40.0, 0.02), lc("impact", 50.0, 0.9)]
    verify.set_providers(telemetry=lambda c: [dict(no), dict(band), dict(yes), dict(a)],
                         local=lambda c: checks)
    try:
        rows = {r["event_id"]: r for r in verify.telemetry_events_for(["CLIP_A.MP4"])}
    finally:
        verify.set_providers()
    assert rows[no["event_id"]]["local"]["p_yes"] == 0.04
    assert rows[band["event_id"]]["local"]["p_yes"] == 0.5     # impact joins a speed_drop (same family)
    assert rows[a["event_id"]]["local"]["advisory"] is True
    st = {"verdicts": {}}
    cert = {k: verify.annotate_event(r, st, mode="hybrid", local_ok=True) for k, r in rows.items()}
    n, b, y, ai = (cert[x["event_id"]] for x in (no, band, yes, a))
    assert (n["certainty"], n["asserted"]) == ("local", False)         # not a fall
    assert (y["certainty"], y["asserted"]) == ("local", True)
    assert b["certainty"] == "telemetry"                               # goes to Claude
    assert ai["certainty"] == "telemetry"                              # an air is never settled locally
    routed = verify.route(list(rows.values()), mode="hybrid", local_ok=True, heroes=[])
    ids = {r["event_id"] for r in routed}
    assert band["event_id"] in ids and a["event_id"] in ids
    assert no["event_id"] not in ids and yes["event_id"] not in ids


def test_a_local_no_does_not_overrule_two_sensor_signals():
    drop = {"clip": "CLIP_A.MP4", "kind": "speed_drop", "start": 8.0, "end": 10.0, "t": 10.0,
            "check": True, "source": "telemetry", "from_ms": 11.0, "to_ms": 2.0}
    stop = {"clip": "CLIP_A.MP4", "kind": "stopped", "start": 10.0, "end": 14.0, "t": 10.0,
            "check": True, "source": "telemetry"}
    verify.set_providers(telemetry=lambda c: [dict(drop), dict(stop)],
                         local=lambda c: [lc("speed_drop", 10.0, 0.04)])
    try:
        rows = verify.telemetry_events_for(["CLIP_A.MP4"])
    finally:
        verify.set_providers()
    r = verify.annotate_event(rows[0], {"verdicts": {}}, mode="hybrid", local_ok=True)
    assert r["certainty"] == "telemetry" and r["asserted"]
    assert verify.route(rows, mode="hybrid", local_ok=True, heroes=[])


def test_demote_holds_a_telemetry_row_the_local_model_settled_as_no():
    f = impact(t=10.0)
    row = {"clip": "CLIP_A.MP4", "start": 10.0, "end": 10.1, "kind": "crash", "source": "telemetry",
           "status": "felt", "felt": f}
    verify.set_providers(telemetry=lambda c: [dict(f)], local=lambda c: [lc("impact", 10.0, 0.05)])
    try:
        asserted, held = verify.demote([row], "CLIP_A.MP4", {"verdicts": {}})
        ok, _ = verify.demote([row], "CLIP_A.MP4",
                              {"verdicts": {f["event_id"]: {"verdict": "confirm"}}})
    finally:
        verify.set_providers()
    assert asserted == [] and held[0]["certainty"] == "local" and not held[0]["asserted"]
    assert len(ok) == 1 and ok[0]['asserted']                           # Claude overrules the local no


def test_local_available_reads_the_dict(monkeypatch):
    from roughcut import localvlm
    monkeypatch.setattr(localvlm, "available", lambda *a, **k: {"ok": False, "why": "x"})
    assert verify.local_available() is False
    monkeypatch.setattr(localvlm, "available", lambda *a, **k: {"ok": True})
    assert verify.local_available() is True
