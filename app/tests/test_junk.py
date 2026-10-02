"""HANDOFF roadmap item 5 — junk proposed by a measurement, confirmed by the human.

Three layers: `roughcut/junk.py`'s rules held against B1/Copper's real per-clip
measurements (`benchmarks/labels/B1-luma.json`, the bin with nine known junk clips and
two dim real ones a naive threshold takes); the journal skipping a confirmed clip's
priced stages; the server's `/api/junk`, the Ask's inventory and the index, against the
synthetic junk bin in conftest. No model is called; the Ask runs on a scripted backend.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import junk, journal  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
B1_JUNK = {"GX010479", "GX010480", "GX010481", "GX010482", "GX010484", "GX010485",
           "GX010497", "GX010498", "GX010499"}       # HANDOFF, end: "B1's nine junk clips"
B1_DIM_REAL = {"GX010477", "GX010478", "GX010486"}    # the night parking lot, the plane


def _b1_rows() -> list[dict]:
    return json.loads((REPO / "benchmarks/labels/B1-luma.json").read_text(encoding="utf-8"))


def _b1_facts(row: dict, words: int | None = 0) -> dict:
    # B1-luma.json carries a mean and a std per clip, no per-sample peak or spread: the
    # black rule is the one it measures. Spread/peak unknown is None, never zero.
    return {"duration_s": row["dur"], "words": words, "samples": 1,
            "luma_mean": row["luma_mean"], "luma_peak": None, "spread": None}


# ------------------------------------------------------------------ the rules


def test_b1_black_clips_are_proposed_and_the_dim_real_ones_are_not():
    proposed = {r["clip"].split(".")[0] for r in _b1_rows()
                if junk.assess(_b1_facts(r))["junk"]}
    assert proposed == B1_JUNK
    # The trap the rule exists to avoid: luma<35 would have taken real content.
    naive = {r["clip"].split(".")[0] for r in _b1_rows() if r["luma_mean"] < 35}
    assert B1_DIM_REAL <= naive and not (B1_DIM_REAL & proposed)


def test_speech_keeps_a_black_clip():
    row = next(r for r in _b1_rows() if r["clip"].startswith("GX010498"))   # luma 1.1
    a = junk.assess(_b1_facts(row, words=4))
    assert a["junk"] is False and "4 words heard" in a["reasons"][0]
    # ...and an unlistened clip is still proposed, saying so
    a = junk.assess(_b1_facts(row, words=None))
    assert a["junk"] and a["reasons"][-1].startswith("not listened to yet")


def test_a_lit_sample_keeps_a_dark_clip():
    f = {"duration_s": 30, "words": 0, "samples": 6, "luma_mean": 9.0,
         "luma_peak": junk.LUMA_LIT + 5, "spread": 120.0}
    assert junk.assess(f)["junk"] is False


def test_flat_and_short_rules():
    flat = {"duration_s": 20, "words": 0, "samples": 4, "luma_mean": 200.0,
            "luma_peak": 201.0, "spread": 3.0}         # a white bag over the lens
    a = junk.assess(flat)
    assert a["junk"] and a["reasons"][0].startswith("one flat field")
    short = {"duration_s": 0.6, "words": 0, "samples": 0, "luma_mean": None,
             "luma_peak": None, "spread": None}
    a = junk.assess(short)
    assert a["junk"] and a["reasons"][0].startswith("too short to cut")
    # a real clip with none of it
    ok = {"duration_s": 30, "words": 0, "samples": 6, "luma_mean": 120.0,
          "luma_peak": 140.0, "spread": 220.0}
    a = junk.assess(ok)
    assert a["junk"] is False and a["reasons"] == []


def test_facts_from_a_colour_file():
    colour = {"samples": [{"mean_rgb": [0.02, 0.02, 0.02], "y_lo": 0.0, "y_hi": 0.03},
                          {"mean_rgb": [0.04, 0.04, 0.04], "y_lo": 0.0, "y_hi": 0.02}]}
    f = junk.facts(colour, {"summary": {"n_words": 0}}, 12.0)
    assert f["samples"] == 2 and f["words"] == 0 and f["duration_s"] == 12.0
    assert f["luma_mean"] == pytest.approx(7.65, abs=0.1)
    assert f["luma_peak"] == pytest.approx(10.2, abs=0.1)
    assert f["spread"] == pytest.approx(6.4, abs=0.1)
    assert junk.facts(None, None, None)["luma_mean"] is None


def test_the_edl_block_validates():
    assert junk.validate(None) == {}
    ok = junk.validate({"A.MP4": {"verdict": "junk", "at": 1, "reasons": ["black"]}})
    assert ok == {"A.MP4": {"verdict": "junk", "at": 1.0, "reasons": ["black"]}}
    for bad in ([], {"A.MP4": {"verdict": "maybe"}}, {"A.MP4": "junk"},
                {"A.MP4": {"verdict": "keep", "reasons": "black"}}):
        with pytest.raises(ValueError):
            junk.validate(bad)
    b = junk.set_verdict({}, "A.MP4", "junk", ["black"], now=5)
    assert junk.confirmed(b) == {"A.MP4"}
    assert junk.set_verdict(b, "A.MP4", None) == {}
    assert junk.status(True, None) == "proposed"
    assert junk.status(True, "keep") == "kept"
    assert junk.status(False, "junk") == "confirmed"


# ------------------------------------------------------------------ the journal


def _journal(clips=("A", "B", "C")) -> journal.Journal:
    j = journal.Journal(None, bin="t")
    j.add_clips(clips, now=0)
    for c in clips:
        for s in ("probe", "telemetry", "asr", "proxy"):
            j.start(c, s, now=1)
            j.finish(c, s, now=2)
    return j


def test_confirmed_junk_never_queues_a_priced_stage():
    j = _journal()
    assert j.set_junk("B", "confirmed", now=3) == ["look", "close"]
    taken = []
    while (nxt := j.next(now=4)) is not None:
        j.start(*nxt, now=4)
        j.finish(*nxt, now=5)
        taken.append(nxt)
    assert ("B", "look") not in taken and ("B", "close") not in taken
    assert ("B", "picks") in taken and j.released("B")
    assert j.state("B", "look") == "skipped"


def test_unconfirming_reopens_only_what_junk_skipped():
    j = _journal()
    j.skip("A", "close", "nothing worth a closer look", now=3)
    j.set_junk("A", "confirmed", now=3)            # look skipped by junk; close was not
    assert j.set_junk("A", None, now=4) == ["look"]
    assert j.state("A", "look") == "queued"
    assert j.state("A", "close") == "skipped"      # a fact about the clip stays


def test_proposed_junk_walks_after_the_clean_clips():
    j = _journal()
    j.set_junk("A", "proposed", now=3)
    assert j.ordered()[-1] == "A"
    assert j.next(now=4) == ("B", "look")


# ------------------------------------------------------------------ the server


@pytest.fixture
def jclient(junk_project):
    from fastapi.testclient import TestClient
    import server

    junk_project["edl"].write_text(junk_project["seed"], encoding="utf-8")
    server.configure(junk_project["edl"], junk_project["footage"], junk_project["sidecars"],
                     junk_project["work"], proxies=False,
                     visual=junk_project["work"] / "no-visual", assets=junk_project["assets"])
    server.ensure_proxies([f"{s}.MP4" for s in junk_project["stems"]])
    jp = server.journal_path()
    jp.unlink(missing_ok=True)
    with TestClient(server.app) as c:
        yield c
    jp.unlink(missing_ok=True)
    junk_project["edl"].write_text(junk_project["seed"], encoding="utf-8")


def _states(client) -> dict:
    return {r["stem"]: r["state"] for r in client.get("/api/junk").json()["clips"]}


def test_api_proposes_the_black_and_the_blip_only(jclient):
    body = jclient.get("/api/junk").json()
    states = {r["stem"]: r["state"] for r in body["clips"]}
    assert states == {"CLIP_OK": "clean", "CLIP_DARK": "proposed", "CLIP_NIGHT": "clean",
                      "CLIP_TALK": "clean", "CLIP_BLIP": "proposed"}, body
    night = next(r for r in body["clips"] if r["stem"] == "CLIP_NIGHT")
    # the dim clip sits in B1's dim-real band, under a naive 35 and over the black line
    assert junk.LUMA_BLACK < night["numbers"]["luma_mean"] < 35, night["numbers"]
    dark = next(r for r in body["clips"] if r["stem"] == "CLIP_DARK")
    assert dark["reasons"][0].startswith("essentially black") and dark["numbers"]["samples"] >= 2
    assert body["provisional"] is True and body["counts"]["proposed"] == 2


def test_confirm_drops_the_clip_from_the_ask_and_keep_clears_it(jclient, junk_project):
    from roughcut import config, inference
    import server

    seen: list = []

    class Scripted:
        name = "scripted"

        def complete(self, request):
            seen.append(request)
            text = json.dumps({"segments": [{"clip": "CLIP_OK.MP4", "in": 0.5, "out": 2.0,
                                             "why": "the greeting"}], "notes": "ok"})
            return inference.Result(content=text, input_tokens=10, output_tokens=5,
                                    backend="scripted", model=config.model_for(request.role),
                                    projected_usd=0.0001, latency_ms=1, raw=text)

    def ask(segments):
        r = jclient.post("/api/ask", json={"note": "cut it", "segments": segments})
        assert r.status_code == 200, r.text
        deadline = time.time() + 20
        while time.time() < deadline:
            s = jclient.get(f"/api/ask/{r.json()['job']}").json()
            if s["state"] not in ("estimating", "running"):
                break
            time.sleep(0.05)
        assert s["state"] == "done", s
        return seen[-1].prompt

    inference.set_backend(Scripted())
    inference.reset_spend()
    try:
        assert "### CLIP_DARK.MP4" in ask([])            # proposed only: still offered
        r = jclient.post("/api/junk", json={"clip": "CLIP_DARK.MP4", "verdict": "junk"})
        assert r.status_code == 200 and r.json()["clip"]["state"] == "confirmed"
        assert "### CLIP_DARK.MP4" not in ask([])        # the first cut
        cut = [{"clip": "CLIP_OK.MP4", "in": 1.0, "out": 3.0}]
        assert "### CLIP_DARK.MP4" not in ask(cut)       # a revision
        # Find reads the same inventory
        clips, _ = server._ask_clips(drop_junk=True)
        assert "CLIP_DARK.MP4" not in clips and "CLIP_OK.MP4" in clips
        # a confirmed clip the cut still uses stays — the plan validator needs it
        clips, _ = server._ask_clips([{"clip": "CLIP_DARK.MP4", "in": 0, "out": 2}],
                                     drop_junk=True)
        assert "CLIP_DARK.MP4" in clips
    finally:
        inference.set_backend(None)

    edl = json.loads(junk_project["edl"].read_text(encoding="utf-8"))
    assert edl["junk"]["CLIP_DARK.MP4"]["verdict"] == "junk"
    assert edl["junk"]["CLIP_DARK.MP4"]["reasons"][0].startswith("essentially black")

    jclient.post("/api/junk", json={"clip": "CLIP_DARK.MP4", "verdict": "keep"})
    assert _states(jclient)["CLIP_DARK"] == "kept"
    jclient.post("/api/junk", json={"clip": "CLIP_DARK.MP4", "verdict": None})
    assert _states(jclient)["CLIP_DARK"] == "proposed"
    assert "junk" not in json.loads(junk_project["edl"].read_text(encoding="utf-8"))


def test_post_and_put_validate(jclient):
    assert jclient.post("/api/junk", json={"clip": "NOPE.MP4", "verdict": "junk"}).status_code == 400
    assert jclient.post("/api/junk", json={"clip": "CLIP_DARK.MP4", "verdict": "bin"}).status_code == 400
    p = jclient.get("/api/project").json()
    r = jclient.put("/api/project", json={"segments": p["segments"],
                                          "junk": {"CLIP_DARK.MP4": {"verdict": "maybe"}}})
    assert r.status_code == 400 and "verdict" in r.text
    r = jclient.put("/api/project", json={"segments": p["segments"],
                                          "junk": {"CLIP_DARK.MP4": {"verdict": "junk"}}})
    assert r.status_code == 200 and _states(jclient)["CLIP_DARK"] == "confirmed"


def test_the_journal_skips_a_confirmed_clips_priced_stages(jclient):
    import server

    j = server.load_journal()
    clips = server.footage_clips()
    j.add_clips(clips, now=0)
    for c in clips:
        for s in ("probe", "telemetry", "asr", "proxy"):
            j.start(c, s, now=1)
            j.finish(c, s, now=2)
    # what the index thread does before every pick
    server.sync_junk(j)
    assert j.clips["CLIP_DARK.MP4"]["junk"] == "proposed"
    assert j.ordered()[-2:] == sorted(["CLIP_BLIP.MP4", "CLIP_DARK.MP4"]) or \
        set(j.ordered()[-2:]) == {"CLIP_BLIP.MP4", "CLIP_DARK.MP4"}

    r = jclient.post("/api/junk", json={"clip": "CLIP_DARK.MP4", "verdict": "junk"})
    assert r.json()["index"] == "applied" and r.json()["stages"] == ["look", "close"]
    j = server.load_journal()
    queued = []
    while (nxt := j.next(now=10)) is not None:
        j.start(*nxt, now=10)
        j.finish(*nxt, now=11)
        queued.append(nxt)
    assert not [q for q in queued if q[0] == "CLIP_DARK.MP4" and q[1] in journal.PRICED]
    assert ("CLIP_OK.MP4", "look") in queued

    # keep gives the stages back
    jclient.post("/api/junk", json={"clip": "CLIP_DARK.MP4", "verdict": "keep"})
    j = server.load_journal()
    # its look was skipped by junk, so it is queued again (the loop above finished
    # every other clip's stages; the kept clip's priced stages are back)
    assert j.state("CLIP_DARK.MP4", "look") == "queued"
    assert j.clips["CLIP_DARK.MP4"]["junk"] is None


def test_open_screen_and_floor_carry_the_word(jclient):
    rows = {c["stem"]: c["junk"] for c in jclient.get("/api/clips").json()["clips"]}
    assert rows["CLIP_DARK"] == "proposed" and rows["CLIP_OK"] == "clean"
