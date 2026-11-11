"""Wave 25, lane FIND2: the description's last step (roughcut/reconcile.py) and the model step of Find
(roughcut/findthink.py), with a scripted backend: no live call.

The Copper finder's second audit: GX010492's description said "works at a chairlift terminal" while its
Found line said he rides the chair; Find could not reach "porcelain throne" from "restroom"; "matches the
picture" was on every row; a whole-clip hit floated to #1 for unrelated questions; and the prompt-box AI found
a jump to the frame by looking where Find missed it.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from roughcut import config, find, findthink, inference, reconcile  # noqa: E402


class Fake:
    """Routes by what the prompt asks. `calls` records (kind, prompt, images)."""
    name = "fake"

    def __init__(self, *, terms=("bathroom", "toilet"), about_picture=False, strong=(), nothing=False,
                 scan=None, desc="Two friends say hello at a car park, one holds the milk jug."):
        self.calls: list = []
        self.terms, self.about_picture, self.strong, self.nothing, self.scan, self.desc = terms, about_picture, set(strong), nothing, scan, desc
        self.hold = None

    def complete(self, request):
        p = request.prompt
        if "Write the description again" in p:
            kind, body = "reconcile", {"description": self.desc}
        elif "Return `terms`" in p:
            kind, body = "expand", {"terms": list(self.terms), "about_picture": self.about_picture, "notes": ""}
        elif "## Candidates" in p:
            kind = "judge"
            n = p.count("\n   ")  # one detail line per candidate
            body = {"ranking": [{"n": i, "strength": "strong" if i in self.strong else "weak", "why": f"line {i}"}
                                for i in range(1, n + 1)], "nothing": self.nothing, "notes": "ok"}
        elif "The attached sheets" in p:
            kind, body = "scan", self.scan or {"found": [], "nothing": True}
        else:
            kind, body = "other", {}
        if self.hold is not None and kind == "judge":
            self.hold.wait(10)
        self.calls.append((kind, p, tuple(request.images)))
        text = json.dumps(body)
        return inference.Result(content=text, input_tokens=10, output_tokens=5, backend="fake",
                                model=config.model_for(request.role), projected_usd=1e-4, latency_ms=1, raw=text)

    def kinds(self):
        return [k for k, _, _ in self.calls]


@pytest.fixture
def fake():
    m = Fake()
    inference.set_backend(m)
    inference.reset_spend()
    yield m
    inference.set_backend(None)


def strip(text):
    return text, 0


# ------------------------------------------------------------------ 1. the reconcile step

def test_the_reconcile_prompt_carries_the_moments_and_the_words(fake):
    out = reconcile.reconcile(
        "GX010492.MP4", 30.0, "A man works at a chairlift terminal.",
        [{"start": 16.0, "kind": "action", "what": "rides a chairlift just after loading"},
         {"start": 19.4, "kind": "jump", "what": "a skier is in the air", "unchecked": True}],
        [{"start": 0.0, "text": "He's so milked, that's it."}], strip)
    kind, prompt, imgs = fake.calls[0]
    assert kind == "reconcile" and imgs == ()                               # text only: no frames
    assert "rides a chairlift just after loading" in prompt and "(unchecked)" in prompt
    assert "He's so milked" in prompt and "works at a chairlift terminal" in prompt
    assert "believe the moments and the" in prompt
    assert out["description"].startswith("Two friends")


def test_a_verdict_only_rewrite_is_refused():
    with pytest.raises(ValueError):
        reconcile.validate({"description": "Nothing notable happens."},
                           lambda t: ("", 1))


def test_stale_means_the_close_looks_or_words_changed():
    a = reconcile.inputs_sig("coarse", [{"start": 1, "kind": "x", "what": "w"}], [{"start": 0, "text": "hi"}])
    assert a == reconcile.inputs_sig("coarse", [{"start": 1, "kind": "x", "what": "w"}], [{"start": 0, "text": "hi"}])
    assert a != reconcile.inputs_sig("coarse", [{"start": 1, "kind": "x", "what": "w2"}], [{"start": 0, "text": "hi"}])
    assert a != reconcile.inputs_sig("coarse", [{"start": 1, "kind": "x", "what": "w"}], [])
    assert not reconcile.has_new_evidence([], [])


@pytest.fixture
def described(project):
    import server
    from fastapi.testclient import TestClient
    vis = project["work"] / "visual-think"
    vis.mkdir(exist_ok=True)
    for f in vis.glob("*"):
        f.unlink()
    (vis / "CLIP_A.visual.json").write_text(json.dumps({
        "clip": "CLIP_A.MP4", "description": "A man holds a small bag at a car park.",
        "summary": "A man holds a small bag at a car park.",
        "moments": [{"start": 1.0, "end": 2.0, "what": "he drinks from the milk jug", "kind": "action",
                     "notable": True, "confidence": "high", "frames": [1.0]}],
        "unusable": [], "frames_sampled": [0.0, 4.0], "params": {"prompt_version": 4, "interval_s": 4.0}}), encoding="utf-8")
    original = project["edl"].read_text(encoding="utf-8")
    import shutil
    shutil.rmtree(project["work"] / "find", ignore_errors=True)       # the cached expansions of other tests
    server.configure(project["edl"], project["footage"], project["sidecars"], project["work"],
                     proxies=False, visual=vis, assets=project["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in project["stems"]])
    with TestClient(server.app) as c:
        yield c, vis, server
    project["edl"].write_text(original, encoding="utf-8")
    for f in vis.glob("*"):
        f.unlink()


def test_a_clip_is_rewritten_from_its_moments_and_marked(described, fake):
    c, vis, server = described
    assert c.get("/api/found/CLIP_A.MP4").json()["reconcile"] == "stale"
    r = c.post("/api/found/CLIP_A.MP4/reconcile", json={}).json()
    assert r["state"] == "written" and r["description_by"] == "index"
    assert r["description"].startswith("Two friends") and r["description_look"].startswith("A man holds a small bag")
    assert (vis / "CLIP_A.reconciled.json").exists()
    rec = json.loads((vis / "CLIP_A.reconciled.json").read_text())
    assert rec["version"] == reconcile.VERSION and rec["coarse"].startswith("A man holds")
    # the project's own reads see it, and nothing needs rewriting again until the inputs change
    clip = c.get("/api/project").json()["clips"]["CLIP_A.MP4"]
    assert clip["visual"]["summary"].startswith("Two friends") and clip["visual"]["description_by"] == "index"
    assert c.get("/api/found/CLIP_A.MP4").json()["reconcile"] == "fresh"
    n = len(fake.calls)
    assert c.post("/api/found/CLIP_A.MP4/reconcile", json={}).json()["state"] == "fresh"
    assert len(fake.calls) == n                                                  # fresh: no second call
    # a re-look changes the coarse text: stale again
    side = vis / "CLIP_A.visual.json"
    j = json.loads(side.read_text())
    j["description"] = "A different look."
    side.write_text(json.dumps(j))
    assert c.get("/api/found/CLIP_A.MP4").json()["reconcile"] == "stale"


def test_a_description_the_editor_wrote_is_never_touched(described, fake):
    c, vis, server = described
    c.put("/api/found/CLIP_A.MP4/description", json={"text": "Mine, not the model's."})
    before = len(fake.calls)
    r = c.post("/api/found/CLIP_A.MP4/reconcile", json={"force": True}).json()
    assert r["state"] == "mine" and len(fake.calls) == before
    assert c.get("/api/found/CLIP_A.MP4").json()["description"] == "Mine, not the model's."
    assert not (vis / "CLIP_A.reconciled.json").exists()
    assert server.reconcile_stale(None, ["CLIP_A.MP4"]) == {"written": 0, "failed": [], "usd": 0.0}


def test_a_clip_with_nothing_new_is_left_alone(described, fake):
    c, vis, server = described
    j = json.loads((vis / "CLIP_A.visual.json").read_text())
    j["moments"] = []
    (vis / "CLIP_A.visual.json").write_text(json.dumps(j))
    # CLIP_B / C are unlooked; CLIP_A has words (the suite's transcript), so it is stale, but B has no description
    assert server.reconcile_state("CLIP_B.MP4") == "none"
    assert c.post("/api/found/CLIP_B.MP4/reconcile", json={}).json()["state"] == "none"


def test_the_index_job_writes_the_descriptions_at_its_end(described, fake):
    c, vis, server = described
    out = server.reconcile_stale(None, ["CLIP_A.MP4", "CLIP_B.MP4"])
    assert out["written"] == 1 and (vis / "CLIP_A.reconciled.json").exists()


# ------------------------------------------------------------------ 2. Find that thinks

def _clips():
    return {
        "GX1.MP4": {"clip": "GX1.MP4", "duration": 40.0, "summary": {},
                    "transcript": [{"start": 2.0, "end": 6.0, "text": "he is on the porcelain throne"}],
                    "visual": {"moments": [], "unusable": [], "description": "A man in a corridor."}},
        "GX2.MP4": {"clip": "GX2.MP4", "duration": 30.0, "summary": {}, "transcript": [],
                    "visual": {"moments": [{"start": 10, "end": 12, "kind": "scenery", "notable": False,
                                            "what": "a wide valley with peaks, no person in the foreground"}],
                               "unusable": [], "description": "Skiers on a run."}},
        "GX3.MP4": {"clip": "GX3.MP4", "duration": 60.0, "summary": {}, "transcript": [], "visual": {"moments": [], "unusable": [],
                    "description": "A group lounges."}},
    }


def test_the_expansion_is_one_call_and_cached_per_query(fake, tmp_path):
    a = findthink.expand("restroom on the plane", tmp_path)
    b = findthink.expand("Restroom  on the plane", tmp_path)
    assert a["terms"] == ["bathroom", "toilet"] and a["cached"] is False and b["cached"] is True
    assert fake.kinds() == ["expand"]
    assert (tmp_path / "expansions.json").exists()


def test_the_widened_query_reaches_a_story_only_spoken_and_a_found_moment():
    clips = _clips()
    rows = find.lexical("restroom on the plane", clips)
    assert not any(r["clip"] == "GX1.MP4" for r in rows)
    wide = findthink.widen(rows, "restroom on the plane", ["porcelain throne"], clips)
    hit = next(r for r in wide if r["clip"] == "GX1.MP4")
    assert hit["via"] == "porcelain throne" and hit["source"].startswith("heard")
    view = findthink.widen([], "a mountain view", ["scenery"], clips)
    assert any(r["clip"] == "GX2.MP4" for r in view)


def test_a_judged_row_replaces_matches_the_picture_with_a_strength_and_a_line(fake):
    clips = _clips()
    rows = findthink.prepare(find.lexical("porcelain throne", clips), clips)
    assert rows[0]["strength"] == "strong" and "porcelain throne" in rows[0]["line"]
    assert all("matches the picture" not in r["line"] for r in rows)
    fake.strong = {1}
    got = findthink.judge("porcelain throne", rows, clips, [])
    assert got["rows"][0]["strength"] == "strong" and got["rows"][0]["line"] == "line 1" and got["rows"][0]["judged"]
    assert got["nothing"] is False


def test_nothing_is_said_only_when_the_model_agrees_and_never_over_a_strong_hit(fake):
    clips = _clips()
    rows = findthink.prepare(find.lexical("porcelain throne", clips), clips)
    fake.nothing, fake.strong = True, {1}
    assert findthink.judge("q", rows, clips, [])["nothing"] is False             # a strong hit wins
    fake.nothing, fake.strong = True, set()
    assert findthink.judge("q", rows, clips, [])["nothing"] is True              # read, and agreed
    fake.nothing = False
    assert findthink.judge("q", rows, clips, [])["nothing"] is False             # the model did not say it


def test_a_whole_clip_hit_ranks_under_a_moment_hit_of_the_same_strength():
    clips = _clips()
    whole = {"clip": "GX3.MP4", "start": 0.0, "end": 60.0, "strength": "possible", "what": "x", "source": "seen"}
    moment = {"clip": "GX1.MP4", "start": 2.0, "end": 4.0, "strength": "possible", "what": "y", "source": "heard"}
    rows = findthink.prepare([whole, moment], clips)
    assert [r["clip"] for r in rows] == ["GX1.MP4", "GX3.MP4"] and rows[1]["whole"] is True
    # the whole clip is the only strong one: it keeps the top over a possible moment hit
    strong_whole = dict(whole, strength="strong", whole=True)
    assert [r["clip"] for r in findthink.demote_whole([dict(moment, whole=False), strong_whole])] == ["GX3.MP4", "GX1.MP4"]
    # a strong moment hit takes it back from a strong whole clip
    strong_moment = dict(moment, strength="strong", whole=False)
    assert findthink.demote_whole([strong_whole, strong_moment])[0]["clip"] == "GX1.MP4"


def test_the_scan_plan_is_bounded_and_counts_its_frames():
    clips = _clips()
    p = findthink.scan_plan(["GX1.MP4", "GX2.MP4", "GX3.MP4"], clips)
    assert p["frames"] and len(p["frames"]) <= findthink.SCAN_FRAMES_CAP
    assert {f["clip"] for f in p["frames"]} <= {"GX1.MP4", "GX2.MP4", "GX3.MP4"}
    one = findthink.scan_plan(["GX2.MP4"], clips)
    assert 28 <= len(one["frames"]) <= 31                                         # about a frame a second


def test_the_scans_answer_is_validated_against_the_clips():
    d = {"GX2.MP4": 30.0}
    got = findthink.validate_scan({"found": [
        {"clip": "GX2.MP4", "start": 20.0, "end": 20.0, "strength": "strong", "why": "a skier is in the air"},
        {"clip": "NOPE.MP4", "start": 1, "end": 2}, {"clip": "GX2.MP4", "start": 99, "end": 100}]}, d)
    assert len(got["found"]) == 1 and got["found"][0]["start"] == pytest.approx(19.5) and got["found"][0]["end"] == pytest.approx(20.5)


# ------------------------------------------------------------------ the endpoints

def _wait(c, job, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        s = c.get(f"/api/job/{job}").json()
        if s["state"] != "running":
            return s
        time.sleep(0.05)
    raise AssertionError("never finished")


def test_find_returns_the_instant_hits_then_a_judged_list(described, fake):
    c, vis, server = described
    fake.strong = {1}
    r = c.post("/api/find", json={"query": "goodbye", "local": False, "think": True}).json()
    assert r["think_job"] and r["matches"]                      # the instant hits come back at once
    assert all("strength" in m and m["line"] for m in r["matches"])
    s = _wait(c, r["think_job"])
    assert s["state"] == "done", s
    f = s["found"]
    assert f["terms"] == ["bathroom", "toilet"] and f["matches"][0]["strength"] == "strong" and f["matches"][0]["judged"]
    assert f["nothing"] is False and f["seconds"] >= 0
    assert fake.kinds()[:2] == ["expand", "judge"]


def test_the_model_step_can_be_stopped_and_drops_its_answer(described, fake):
    import threading
    c, vis, server = described
    fake.hold = threading.Event()
    r = c.post("/api/find", json={"query": "goodbye", "local": False, "think": True}).json()
    time.sleep(0.3)
    st = c.post(f"/api/jobs/{r['think_job']}/stop").json()
    assert st["state"] == "cancelled" and st["stopped"] is True
    fake.hold.set()
    time.sleep(0.5)
    s = c.get(f"/api/job/{r['think_job']}").json()
    assert s["state"] == "cancelled" and not s.get("found")


def test_a_plain_find_never_calls_the_model(described, fake):
    c, vis, server = described
    r = c.post("/api/find", json={"query": "goodbye", "local": False}).json()       # the suite's env turns it off
    assert r["think_job"] is None and fake.calls == []


def test_a_picture_question_with_nothing_strong_offers_a_scan_and_the_scan_lands_on_the_frames(described, fake):
    c, vis, server = described
    fake.about_picture, fake.strong = True, set()
    r = c.post("/api/find", json={"query": "hello", "local": False, "think": True}).json()
    s = _wait(c, r["think_job"])
    offer = s["found"]["scan_offer"]
    assert offer and offer["frames"] > 0 and offer["clips"] and offer["cost"]
    fake.scan = {"found": [{"clip": offer["clips"][0], "start": 2.0, "end": 2.0, "strength": "strong",
                            "why": "someone is in the air"}], "nothing": False}
    sc = c.post("/api/find/scan", json={"query": "hello", "clips": offer["clips"]}).json()
    assert sc["frames"] == offer["frames"]
    done = _wait(c, sc["job"], 60)
    assert done["state"] == "done", done.get("detail")
    row = done["found"]["matches"][0]
    assert row["source"] == "looked" and row["strength"] == "strong" and row["line"] == "someone is in the air"
    kind, prompt, imgs = [x for x in fake.calls if x[0] == "scan"][0]
    assert imgs and all(Path(i).exists() for i in imgs)                              # real contact sheets went with it
    assert "The attached sheets" in prompt


def test_a_spoken_question_offers_no_scan(described, fake):
    c, vis, server = described
    fake.about_picture = False
    r = c.post("/api/find", json={"query": "how are you", "local": False, "think": True}).json()
    assert _wait(c, r["think_job"])["found"]["scan_offer"] is None


def test_a_scan_with_no_clips_is_refused(described, fake):
    c, vis, server = described
    assert c.post("/api/find/scan", json={"query": "x", "clips": []}).status_code == 400
