"""The flow (INTAKE M14): one model of where a project is, for every screen.

Karl, 2026-10-03 (#3): "make the flow through various stages make more sense in the
UI." `roughcut/flow.py` is pure — facts in, seven stages and one next action out — so
most of this file builds facts by hand and reads the answer; the last tests read
`GET /api/flow` off the suite's own project, where the facts come from the files.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from roughcut import flow  # noqa: E402

FIX = {"kind": "login", "title": "Sign the Claude CLI in",
       "why": "the CLI is signed out, so nothing priced can run",
       "command": "claude /login"}


def fresh(n: int = 12) -> dict:
    """A bin just opened: clips found, previews still building, nothing heard."""
    return {"clips": n, "junk": 0, "proxies": {"done": 3, "total": n},
            "index": {"journal": False, "listened": 0, "looked": 0, "close": 0,
                      "close_need": 0, "interval_s": 4.0, "pending_usd": 3.1,
                      "on_pass": 0},
            "brief": {}, "pass": {}, "cut": {"shots": 0}, "polish": {}, "render": {},
            "jobs": {}, "fix": None}


def indexed(n: int = 12) -> dict:
    """Every clip heard, looked at every 4 s and looked at closely; no cut yet."""
    f = fresh(n)
    f["proxies"] = {"done": n, "total": n}
    f["index"] = {"journal": True, "listened": n, "looked": n, "close": n,
                  "interval_s": 4.0, "intervals": {"4": n}, "on_pass": n,
                  "released": n, "paused": False, "pending_usd": 0.0}
    f["pass"] = {"on_pass": n, "passed": 0, "keeps": 0, "heroes": 0}
    return f


def with_cut(f: dict, shots: int = 21, length: float = 175.0) -> dict:
    f = copy.deepcopy(f)
    f["cut"] = {"shots": shots, "length_s": length, "target": [120, 180]}
    f["render"] = {"count": 1, "latest_matches": True, "matches": True}
    return f


def by_key(out: dict) -> dict:
    return {s["key"]: s for s in out["stages"]}


def test_the_stages_are_seven_in_the_order_a_film_is_made():
    out = flow.compute(fresh())
    assert [s["key"] for s in out["stages"]] == list(flow.STAGES) == [
        "footage", "index", "brief", "pass", "cut", "polish", "render"]
    for s in out["stages"]:
        assert s["state"] in flow.STATES, s
        assert s["summary"] and s["screen"] in ("/open", "/floor", "/"), s
    # the board's stages open the right dock tool
    st = by_key(out)
    assert st["cut"]["href"] == "/#tool=ask" and st["render"]["href"] == "/#tool=out"
    assert st["pass"]["href"] == "/floor" and st["index"]["href"] == "/open"


def test_a_fresh_bin_builds_previews_and_offers_the_index():
    out = flow.compute(fresh())
    st = by_key(out)
    assert st["footage"]["state"] == "running"
    assert st["footage"]["progress"] == 0.25 and "3/12" in st["footage"]["summary"]
    assert st["index"]["state"] == "ready"
    assert st["brief"]["state"] == "optional"
    assert st["pass"]["state"] == "waiting" and st["pass"]["waiting_on"] == "index"
    assert st["cut"]["state"] == "waiting" and st["cut"]["waiting_on"] == "index"
    assert st["polish"]["state"] == "waiting" and st["render"]["state"] == "waiting"
    nxt = out["next"]
    assert nxt["stage"] == "index" and nxt["screen"] == "/open"
    assert nxt["sentence"] == "Index the footage — ~$3.10" and nxt["usd"] == 3.1
    assert "click" not in nxt, "a priced action navigates to its button; it never presses it"
    assert out["running"] is True and out["blockers"] == []


def test_an_empty_folder_needs_you():
    out = flow.compute(fresh(0))
    assert by_key(out)["footage"]["state"] == "needs-you"
    assert out["next"]["stage"] == "footage"


def test_an_indexed_bin_with_no_cut_says_make_the_first_cut():
    out = flow.compute(indexed())
    st = by_key(out)
    assert st["index"]["state"] == "done"
    assert st["index"]["summary"] == ("Indexed — heard 12/12 · looked at 12/12 "
                                      "(a frame every 4 s) · close looks 12/12")
    assert st["index"]["counts"]["granularity"] == "a frame every 4 s"
    assert st["pass"]["state"] == "optional" and st["pass"].get("recommended")
    assert st["cut"]["state"] == "ready"
    nxt = out["next"]
    assert nxt["stage"] == "cut" and nxt["sentence"] == "Ask for the first cut"
    assert nxt["href"] == "/#tool=ask"
    assert out["running"] is False


def test_the_first_cut_comes_from_the_keeps_when_there_are_any():
    f = indexed()
    f["pass"].update(passed=5, keeps=7, heroes=2)
    out = flow.compute(f)
    st = by_key(out)
    assert st["pass"]["summary"] == "5/12 clips passed · 7 keeps · 2 heroes"
    assert out["next"]["sentence"] == "Make the first cut from your 7 keeps"


def test_a_pending_proposal_needs_you_and_is_the_next_thing():
    f = with_cut(indexed())
    f["cut"]["proposal"] = {"job": "ab12", "shots": 18, "colour_only": False}
    out = flow.compute(f)
    st = by_key(out)
    assert st["cut"]["state"] == "needs-you"
    assert "18 shots" in st["cut"]["needs"]["reason"]
    assert out["next"]["stage"] == "cut" and out["next"]["verb"] == "Answer"
    # a colour-only proposal is the polish stage's, and the cut itself is done
    f["cut"]["proposal"]["colour_only"] = True
    out = flow.compute(f)
    st = by_key(out)
    assert st["cut"]["state"] == "done" and st["polish"]["state"] == "needs-you"
    assert out["next"]["stage"] == "polish" and out["next"]["tool"] == "ask"


def test_an_effect_proposed_needs_you_in_the_fx_tool():
    f = with_cut(indexed())
    f["polish"] = {"fx_proposed": 2, "effects": 1}
    out = flow.compute(f)
    assert by_key(out)["polish"]["state"] == "needs-you"
    assert out["next"]["href"] == "/#tool=fx"


def test_a_render_goes_stale_after_an_edit():
    f = with_cut(indexed())
    st = by_key(flow.compute(f))
    assert st["render"]["state"] == "done"
    f["render"] = {"count": 1, "latest_matches": False, "matches": False}
    out = flow.compute(f)
    st = by_key(out)
    assert st["render"]["state"] == "ready" and st["render"]["counts"]["stale"]
    assert st["render"]["summary"].startswith("Stale")
    nxt = out["next"]
    assert nxt["stage"] == "render" and nxt["verb"] == "Render"
    # rendering is free, so the bar may press the board's own button
    assert nxt["click"] == "#render"


def test_the_cut_says_where_it_sits_against_the_target():
    st = by_key(flow.compute(with_cut(indexed(), length=195.0)))
    assert st["cut"]["summary"] == "21 shots · 3:15 · 0:15 over the 2:00–3:00 target"
    st = by_key(flow.compute(with_cut(indexed(), length=150.0)))
    assert "inside the 2:00–3:00 target" in st["cut"]["summary"]


def test_paused_priced_stages_need_your_word_with_the_price():
    f = with_cut(indexed())
    f["index"].update(close=3, released=3, paused=True,
                      waiting={"clips": 9, "looks": 0, "close": 9, "usd": 1.97})
    out = flow.compute(f)
    ix = by_key(out)["index"]
    assert ix["state"] == "needs-you"
    assert ix["summary"].startswith("Close looks are paused — 9 clips wait, ~$1.97")
    assert ix["needs"]["action"]["verb"] == "Resume"
    assert ix["needs"]["action"]["usd"] == 1.97
    # rendered and nothing else waiting: resuming is what is left to do
    assert out["next"]["stage"] == "index" and out["next"]["verb"] == "Resume"
    # but a stale render comes first — it is free, the resume is not
    f["render"] = {"count": 1, "latest_matches": False, "matches": False}
    assert flow.compute(f)["next"]["stage"] == "render"


def test_a_signed_out_cli_is_the_blocker_of_every_priced_stage():
    f = fresh()
    f["fix"] = FIX
    out = flow.compute(f)
    assert len(out["blockers"]) == 1
    b = out["blockers"][0]
    assert b["kind"] == "cli" and b["command"] == "claude /login"
    assert set(b["stages"]) == {"index", "brief", "cut", "polish"}
    st = by_key(out)
    assert st["index"].get("blocked") == "cli"
    assert "blocked" not in st["footage"], "previews are local; the CLI is not in their way"
    nxt = out["next"]
    assert nxt["kind"] == "cli" and nxt["verb"] == "Fix the CLI"
    assert nxt["command"] == "claude /login" and nxt["then"]["stage"] == "index"
    # an indexed bin: making the first cut is a model call, so the CLI comes first too
    f = indexed()
    f["fix"] = FIX
    out = flow.compute(f)
    assert out["next"]["kind"] == "cli" and out["next"]["then"]["stage"] == "cut"
    assert "blocked" not in by_key(out)["index"], "a done index is not blocked"
    # a free next action is not blocked: a stale render renders signed out
    f = with_cut(indexed())
    f["fix"] = FIX
    f["render"] = {"count": 1, "latest_matches": False, "matches": False}
    assert flow.compute(f)["next"]["stage"] == "render"


def test_while_the_index_runs_the_pass_can_start_on_what_is_released():
    f = fresh()
    f["proxies"] = {"done": 12, "total": 12}
    f["jobs"] = {"index": {"pct": 40.0, "detail": "CLIP_07 · look"}}
    f["index"].update(listened=12, looked=4, on_pass=4)
    f["pass"] = {"on_pass": 4, "passed": 0, "keeps": 0}
    out = flow.compute(f)
    ix = by_key(out)["index"]
    assert ix["state"] == "running" and ix["progress"] == 0.4
    assert "CLIP_07 · look" in ix["summary"]
    assert out["next"]["stage"] == "pass"
    assert out["next"]["sentence"] == "Start the pass on 4 clips — the rest keep indexing"
    # nothing released yet: the next thing is to wait, said as such
    f["index"]["on_pass"] = 0
    f["pass"]["on_pass"] = 0
    out = flow.compute(f)
    assert out["next"]["kind"] == "wait" and out["next"]["stage"] == "index"


def test_granularity_in_words_from_the_sidecars_intervals():
    assert flow.interval_words({"4": 12}, 4.0) == "a frame every 4 s"
    assert flow.interval_words({"1": 2}, 4.0) == "a frame every second"
    assert flow.interval_words({"4": 9, "2": 3}, 4.0) == "9 at 4 s · 3 at 2 s"
    assert flow.interval_words({}, 2.0) == "a frame every 2 s"


# ------------------------------------------------------------------ the API


def test_api_flow_reads_the_suites_project(client, project):
    """The conftest bin: three clips heard, previews built, nothing looked at, a
    two-shot cut, no renders, no journal."""
    out = client.get("/api/flow").json()
    st = by_key(out)
    assert [s["key"] for s in out["stages"]] == list(flow.STAGES)
    assert st["footage"]["state"] == "done" and st["footage"]["counts"]["clips"] == 3
    ix = st["index"]
    assert ix["counts"]["listened"] == 3 and ix["counts"]["looked"] == 0
    assert ix["state"] == "ready"
    assert st["cut"]["state"] == "done" and st["cut"]["counts"]["shots"] == 2
    assert st["cut"]["counts"]["length_s"] == 4.0
    # the suite renders elsewhere, so a render may exist — but never of this cut's seed
    assert st["render"]["state"] in ("ready", "done")
    assert out["next"]["stage"] in {s["key"] for s in out["stages"]}


def test_api_flow_carries_the_servers_own_cli_fix(client, monkeypatch):
    """The blocker is `backend_preflight()["fix"]` — the banner's content, reused."""
    import server

    real = server.backend_preflight
    monkeypatch.setattr(server, "backend_preflight", lambda: {**real(), "fix": FIX})
    out = client.get("/api/flow").json()
    assert out["blockers"][0]["fix"] == FIX
    assert by_key(out)["index"]["blocked"] == "cli"
    monkeypatch.setattr(server, "backend_preflight", lambda: {**real(), "fix": None})
    assert client.get("/api/flow").json()["blockers"] == []


def test_a_discarded_ask_is_answered_and_stops_waiting(client, project):
    import time

    import server

    asks = server.STATE["asks"]
    rec = {"job": "flowtest", "created": time.time() + 5, "note": "tighten",
           "story": "", "plan": {"segments": [{"clip": "CLIP_A.MP4", "in": 0, "out": 1}]}}
    path = asks / "flowtest.json"
    path.write_text(json.dumps(rec), encoding="utf-8")
    try:
        st = by_key(client.get("/api/flow").json())
        assert st["cut"]["state"] == "needs-you", st["cut"]
        assert client.post("/api/asks/answer", json={"answer": "discard"}).json()["job"] == "flowtest"
        assert json.loads(path.read_text(encoding="utf-8"))["answered"]["answer"] == "discard"
        assert by_key(client.get("/api/flow").json())["cut"]["state"] == "done"
        assert client.post("/api/asks/answer", json={"answer": "maybe"}).status_code == 400
    finally:
        path.unlink(missing_ok=True)
