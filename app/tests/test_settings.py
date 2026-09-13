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
        assert d["budget_usd"] == 15.0 and d["source"]["budget_usd"] == "default"
        assert d["workers"] == journal.DEFAULT_WORKERS and d["defaults"]["workers"] == journal.DEFAULT_WORKERS
        assert not server.settings_path().exists(), "a read never writes"

        for bad in ({"budget_usd": 0}, {"budget_usd": "lots"}, {"workers": {"sheet": 9}},
                    {"workers": {"nope": 1}}, {"workers": {"asr": "two"}}, {"workers": [1]}):
            assert c.put("/api/settings", json=bad).status_code == 400, bad

        r = c.put("/api/settings", json={"budget_usd": 5, "workers": {"sheet": 4}})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["budget_usd"] == 5.0 and d["source"]["budget_usd"] == "settings"
        assert d["workers"]["sheet"] == 4 and d["workers"]["asr"] == 1
        on_disk = json.loads(server.settings_path().read_text(encoding="utf-8"))
        assert on_disk == {"budget_usd": 5.0, "workers": {"sheet": 4}}
        # the status line reads the same cap
        assert c.get("/api/status").json()["backend"]["budget_usd"] == 5.0


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
