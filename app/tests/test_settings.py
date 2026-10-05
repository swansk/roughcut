"""GET/PUT /api/settings — the budget cap and the index's workers (INTAKE I5.3's other
half). Under --work, validated, the environment winning for the cap."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import journal  # noqa: E402

from test_index import _stub_tools, _wait_index  # noqa: E402
from test_server import _fresh  # noqa: E402


def test_settings_read_defaults_and_write_validated(tmp_path, project, monkeypatch):
    import server

    monkeypatch.delenv("ROUGHCUT_BUDGET_USD", raising=False)
    with _fresh(tmp_path, project) as c:
        d = c.get("/api/settings").json()
        # no cap by default (INTAKE M16 decision 7)
        assert d["budget_usd"] is None and d["source"]["budget_usd"] == "default"
        assert d["defaults"]["budget_usd"] is None
        assert d["workers"] == journal.DEFAULT_WORKERS and d["defaults"]["workers"] == journal.DEFAULT_WORKERS
        assert not server.settings_path().exists(), "a read never writes"

        for bad in ({"budget_usd": 0}, {"budget_usd": -3}, {"budget_usd": "lots"},
                    {"workers": {"sheet": 9}},
                    {"workers": {"nope": 1}}, {"workers": {"asr": "two"}}, {"workers": [1]}):
            assert c.put("/api/settings", json=bad).status_code == 400, bad

        r = c.put("/api/settings", json={"budget_usd": 5, "workers": {"sheet": 4}})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["budget_usd"] == 5.0 and d["source"]["budget_usd"] == "settings"
        assert d["workers"]["sheet"] == 4 and d["workers"]["asr"] == 1
        on_disk = json.loads(server.settings_path().read_text(encoding="utf-8"))
        # the cap is this project's, kept by the bin's name; the workers are the machine's
        assert on_disk == {"budget_usd_by_bin": {server.STATE["footage"].name: 5.0},
                           "workers": {"sheet": 4}}
        # the status line reads the same cap
        assert c.get("/api/status").json()["backend"]["budget_usd"] == 5.0
        # null takes the cap away again: nothing kept, the default's word
        d = c.put("/api/settings", json={"budget_usd": None}).json()
        assert d["budget_usd"] is None and d["source"]["budget_usd"] == "default"
        assert json.loads(server.settings_path().read_text(encoding="utf-8")) == {"workers": {"sheet": 4}}
        assert c.get("/api/status").json()["backend"]["budget_usd"] is None
        assert server.budget_cap() is None and not server._over_budget(1e6)


def test_the_environment_pins_the_cap_over_the_setting(tmp_path, project, monkeypatch):
    import server

    with _fresh(tmp_path, project) as c:
        c.put("/api/settings", json={"budget_usd": 5})
        monkeypatch.setenv("ROUGHCUT_BUDGET_USD", "40")
        d = c.get("/api/settings").json()
        assert d["budget_usd"] == 40.0 and d["source"]["budget_usd"] == "env"
        assert server.budget_cap() == 40.0


def test_the_cap_and_the_workers_reach_the_index(tmp_path, project, monkeypatch):
    """A saved cap pauses the priced stages exactly as the environment's did; saved
    workers are the journal's at the next run."""
    import server
    from roughcut import inference

    monkeypatch.delenv("ROUGHCUT_BUDGET_USD", raising=False)
    with _fresh(tmp_path, project, sidecars=project["sidecars"], visual=None) as c:
        _stub_tools(server, monkeypatch, tmp_path)
        inference.reset_spend()
        assert c.put("/api/settings", json={"budget_usd": 0.01, "workers": {"sheet": 3}}).status_code == 200
        s = _wait_index(c, c.post("/api/index", json={}).json()["job"])
        assert s["state"] == "done", s
        j = journal.Journal.load(server.journal_path())
        assert j.workers["sheet"] == 3 and j.workers["asr"] == 1
        assert j.paused_priced and "0.01" in str(j.data.get("paused_reason")), j.data.get("paused_reason")
        for clip in ("CLIP_A.MP4", "CLIP_B.MP4", "CLIP_C.MP4"):
            assert j.state(clip, "look") == "queued", "the priced stage waited on the cap"
            assert j.state(clip, "asr") == "done"


def test_a_cap_on_this_project_leaves_every_other_bin_uncapped(tmp_path, project, monkeypatch):
    """Review of I16.0g: the drawer says "cap this project at $__", but the cap was one
    setting under --work — capping Killington at $5 capped copper too."""
    import shutil

    import server

    monkeypatch.delenv("ROUGHCUT_BUDGET_USD", raising=False)
    with _fresh(tmp_path, project, visual=None) as c:
        assert c.put("/api/settings", json={"budget_usd": 5}).json()["budget_usd"] == 5.0
    other = tmp_path / "elsewhere" / "other-bin"
    shutil.copytree(project["footage"], other)
    with _fresh(tmp_path, {**project, "footage": other}, visual=None) as c:
        d = c.get("/api/settings").json()
        assert d["budget_usd"] is None and d["source"]["budget_usd"] == "default"
        assert server.budget_cap() is None and not server._over_budget(1e6)
        assert c.get("/api/status").json()["backend"]["budget_usd"] is None
        # its own cap, and taking it away again, leave the first project's alone
        assert c.put("/api/settings", json={"budget_usd": 2}).json()["budget_usd"] == 2.0
        assert c.put("/api/settings", json={"budget_usd": None}).json()["budget_usd"] is None
    with _fresh(tmp_path, project, visual=None) as c:
        assert c.get("/api/settings").json()["budget_usd"] == 5.0


def test_a_cap_the_old_drawer_saved_is_not_a_cap(tmp_path, project, monkeypatch):
    """Review of I16.0g: before M16 the drawer sent the cap field on every Save — a Save
    that only changed the workers wrote the $15 default too. That file must not keep a
    $15 cap after the upgrade (decision 7: no cap by default); the next save drops it."""
    import server

    monkeypatch.delenv("ROUGHCUT_BUDGET_USD", raising=False)
    with _fresh(tmp_path, project, visual=None) as c:
        server.settings_path().parent.mkdir(parents=True, exist_ok=True)
        server.settings_path().write_text(json.dumps(
            {"budget_usd": 15.0, "workers": {"sheet": 3}}), encoding="utf-8")
        d = c.get("/api/settings").json()
        assert d["budget_usd"] is None and d["source"]["budget_usd"] == "default"
        assert d["workers"]["sheet"] == 3
        assert server.budget_cap() is None
        assert c.get("/api/status").json()["backend"]["budget_usd"] is None
        assert c.put("/api/settings", json={"workers": {"asr": 2}}).status_code == 200
        on_disk = json.loads(server.settings_path().read_text(encoding="utf-8"))
        assert on_disk == {"workers": {"sheet": 3, "asr": 2}}, on_disk


# ------------------------------------------------- spend per project (INTAKE I16.0g)

def _spend_records(server, *, ask=0.0, fine=0.0, deep=0.0, themes=0.0) -> None:
    """What a bin's records on disk say it cost: an ask, a close look, a deep look and
    the themes proposal — the shapes the real writers use."""
    if ask:
        (server.STATE["asks"] / "a1.json").write_text(json.dumps(
            {"job": "a1", "plan": {"segments": [], "usage": {"projected_usd": ask}}}), encoding="utf-8")
    vis = server.STATE["visual"]
    vis.mkdir(parents=True, exist_ok=True)
    if fine:
        (vis / "CLIP_Z.fine.json").write_text(json.dumps(
            {"clip": "CLIP_Z.MP4", "moments": [], "projected_usd": fine}), encoding="utf-8")
    if deep:
        (vis / "CLIP_Z.deep.json").write_text(json.dumps(
            {"clip": "CLIP_Z.MP4", "spans": [], "projected_usd": deep}), encoding="utf-8")
    if themes:
        server.proposal_path().parent.mkdir(parents=True, exist_ok=True)
        server.proposal_path().write_text(json.dumps(
            {"proposal": {"themes": [], "usage": {"projected_usd": themes}}}), encoding="utf-8")


class _Paid:
    """A backend whose every call costs `usd`, and remembers that it was asked."""
    name = "paid"

    def __init__(self, usd: float):
        self.usd, self.calls = usd, 0

    def complete(self, request):
        from roughcut import config, inference
        self.calls += 1
        return inference.Result(content="ok", input_tokens=10, output_tokens=5,
                                backend=self.name, model=config.model_for(request.role),
                                projected_usd=self.usd, latency_ms=1, raw="ok")


def test_spend_is_this_projects_from_disk_and_outlives_the_process(tmp_path, project, monkeypatch):
    """One money number, per bin: the records on disk, not a counter a restart resets
    and every bin shares. In-process calls land on the bin's spend file, the history
    of what was spent before it existed carried once as its first row."""
    import shutil

    import server
    from roughcut import inference

    monkeypatch.delenv("ROUGHCUT_BUDGET_USD", raising=False)
    with _fresh(tmp_path, project, visual=None) as c:
        _spend_records(server, ask=0.6, fine=0.5, deep=0.3, themes=0.2)
        inference.reset_spend()                     # what a restart does to the counter
        assert c.get("/api/settings").json()["spent_usd"] == pytest.approx(1.6)
        assert c.get("/api/status").json()["backend"]["spent_usd"] == pytest.approx(1.6)
        assert not server.spend_path().exists(), "a read never writes"

        paid = _Paid(0.25)
        inference.set_backend(paid)
        try:
            inference.complete("one", role="judge")
            inference.complete("two", role="judge")
        finally:
            inference.set_backend(None)
        rows = [json.loads(x) for x in server.spend_path().read_text(encoding="utf-8").splitlines()]
        assert [r.get("kind") for r in rows] == ["before", None, None]
        assert rows[0]["usd"] == pytest.approx(1.1), "the asks, themes and deep looks, once"
        # a record written after the file exists is already a row there — not counted twice
        _spend_records(server, ask=0.25)
        assert server.project_spent() == pytest.approx(1.6 + 0.5)
        inference.reset_spend()
        assert c.get("/api/status").json()["backend"]["spent_usd"] == pytest.approx(2.1)

    # another bin has spent nothing of this one's
    other = tmp_path / "elsewhere" / "other-bin"
    shutil.copytree(project["footage"], other)
    with _fresh(tmp_path, {**project, "footage": other}, visual=None) as c:
        assert c.get("/api/status").json()["backend"]["spent_usd"] == 0.0


def test_a_cap_is_on_this_project_and_removing_it_lets_resume_carry_on(tmp_path, project, monkeypatch):
    """A cap holds against what this project has spent even when this process has spent
    nothing (the counter a restart zeroes); a model call past it is refused before the
    backend hears of it; taking the cap away and resuming finishes the looks."""
    import server
    from roughcut import inference

    monkeypatch.delenv("ROUGHCUT_BUDGET_USD", raising=False)
    with _fresh(tmp_path, project, sidecars=project["sidecars"], visual=None) as c:
        _stub_tools(server, monkeypatch, tmp_path)
        _spend_records(server, ask=2.5)
        inference.reset_spend()
        assert c.put("/api/settings", json={"budget_usd": 1}).status_code == 200
        st = c.get("/api/status").json()["backend"]
        assert st["budget_usd"] == 1.0 and st["spent_usd"] == pytest.approx(2.5)

        s = _wait_index(c, c.post("/api/index", json={}).json()["job"])
        assert s["state"] == "done" and "budget cap" in s["detail"], s
        j = journal.Journal.load(server.journal_path())
        assert j.paused_priced and j.state("CLIP_A.MP4", "look") == "queued"
        assert "$1.00" in j.data["paused_reason"] and "$2.50 spent" in j.data["paused_reason"]

        paid = _Paid(0.1)
        inference.set_backend(paid)
        try:
            with pytest.raises(inference.BudgetExceeded, match="this project's"):
                inference.complete("past the cap", role="judge")
        finally:
            inference.set_backend(None)
        assert paid.calls == 0, "refused before the call, not after"

        assert c.put("/api/settings", json={"budget_usd": None}).json()["budget_usd"] is None
        s = _wait_index(c, c.post("/api/index", json={"resume_priced": True}).json()["job"])
        assert s["state"] == "done" and s["detail"].startswith("3 of 3 clips released"), s
        assert c.get("/api/index").json()["paused_priced"] is False


def test_the_backend_probe_is_not_refused_by_the_projects_cap(tmp_path, project, monkeypatch):
    """Review of I16.0g: the startup probe and *Check again* asked the project's cap like
    any model call; within $0.05 of the cap — where the index's own pause leaves a
    project — it was refused, and the pill called a working CLI "failed" in the cap's
    words. The probe is not project work; it is not gated. Everything else still is."""
    import server
    from roughcut import inference

    monkeypatch.delenv("ROUGHCUT_BUDGET_USD", raising=False)
    with _fresh(tmp_path, project, visual=None) as c:
        _spend_records(server, ask=4.97)
        inference.reset_spend()
        assert c.put("/api/settings", json={"budget_usd": 5}).status_code == 200
        assert server.project_spent() == pytest.approx(4.97)
        paid = _Paid(0.0001)
        inference.set_backend(paid)
        try:
            server._probe_job()          # what startup and "Check again" run
            b = c.get("/api/status").json()["backend"]
            assert b["state"] == "ok", b
            assert paid.calls >= 1, "the probe reached the backend"
            # the gate is back on for this thread afterwards
            before = paid.calls
            with pytest.raises(inference.BudgetExceeded, match="this project's"):
                inference.complete("past the cap", role="judge")
            assert paid.calls == before
        finally:
            inference.set_backend(None)


def test_a_priced_call_is_checked_against_the_cap_at_its_price(tmp_path, project, monkeypatch):
    """Review of I16.0g: the gate every model call passes sees a $0.05 pre-flight, so
    with a $5 cap and $4.90 spent a whole-cut Ask (~$0.58) started and the project
    landed near $5.48. An Ask, a shot's Ask, the model search, proposing themes, an FX
    design and an FX revision are each checked at their own price before they start,
    as the index, the audit and the deep look already were."""
    import server
    from roughcut import find, fx, inference, themes

    monkeypatch.delenv("ROUGHCUT_BUDGET_USD", raising=False)
    edl = tmp_path / "cut.json"
    edl.write_text(json.dumps({
        "variant": "T", "title": "t", "orient": "none", "story": "",
        "segments": [{"clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "why": "first"},
                     {"clip": "CLIP_B.MP4", "in": 0.0, "out": 2.0, "why": "second"}]}),
        encoding="utf-8")
    with _fresh(tmp_path, project, edl=edl, sidecars=project["sidecars"], visual=None) as c:
        segs = c.get("/api/project").json()["segments"]
        sid = segs[0]["id"]
        home = server.fx_home()
        home.mkdir(parents=True, exist_ok=True)
        (home / "fx_capped.json").write_text(json.dumps(
            {"id": "fx_capped", "shot": sid, "clip": "CLIP_A.MP4", "status": "proposed",
             "name": "t", "events": []}), encoding="utf-8")
        clips, _ = server._ask_clips(drop_junk=True)
        cases = [
            ("this ask", server.ask_price("full")["usd"],
             lambda: c.post("/api/ask", json={"note": "tighten it", "segments": segs})),
            ("this ask", server.ask_price("shot")["usd"],
             lambda: c.post("/api/ask", json={"note": "later", "segments": segs, "focus": 0})),
            ("the model search", find.projected_usd(clips),
             lambda: c.post("/api/find", json={"query": "goodbye", "deep": True})),
            ("proposing themes", themes.projected_usd(clips),
             lambda: c.post("/api/themes/propose", json={})),
            ("the design", fx.price(place_frames=0),
             lambda: c.post("/api/fx/design", json={"shot": sid, "note": "a title"})),
            ("the revision", fx.price(place_frames=0),
             lambda: c.post("/api/fx/revise", json={"id": "fx_capped", "note": "bigger"})),
        ]
        assert c.put("/api/settings", json={"budget_usd": 5}).status_code == 200
        paid = _Paid(0.0)
        inference.set_backend(paid)

        def settle():
            """A call that started anyway (the bug) runs out on the stub, never on
            whatever backend is set once this test lets go of it."""
            import time

            from roughcut import progress
            deadline = time.time() + 20
            while time.time() < deadline and any(
                    j["state"] not in progress.TERMINAL
                    for reg in (server.ASKS, server.FINDS, server.THEMES, server.FX)
                    for j in reg.values()):
                time.sleep(0.05)
        try:
            for what, usd, call in cases:
                # spent so the call's own price passes the cap by half a cent
                _spend_records(server, ask=round(5.0 - usd + 0.005, 4))
                inference.reset_spend()
                r = call()
                if r.status_code != 409:
                    settle()
                assert r.status_code == 409, (what, r.status_code, r.text)
                assert r.json()["detail"].startswith(f"{what} (~${usd:.2f}) would pass this "
                                                     "project's budget cap of $5.00"), r.json()
            assert paid.calls == 0, "refused before anything reached the model"
            # with room for it, the check passes (no job is started here: it would call
            # whatever backend is set once this test lets go of the stub)
            _spend_records(server, ask=1.0)
            for what, usd, _ in cases:
                server.refuse_past_cap(what, usd)
        finally:
            inference.set_backend(None)
