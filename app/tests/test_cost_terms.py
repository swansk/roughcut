"""The audit, Ask and Look deeper never read as spending money on the subscription
backend (Karl, 2026-10-05: *"the audit doesn't spend money — it runs on Claude Code"*).

On `claude_cli` every price is the estimated cost *if* the calls went through the API.
Every response carrying a price says so (`charged: false`, `backend`), and the board's
buttons say "free on your plan" with the estimate in the title, labelled as the API's.
Only `anthropic_api` shows a bare dollar figure. A bare "~$0.58" on the audit made an
agent driving the board stop for a human before every audit."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import config  # noqa: E402

from test_ask_price import no_model  # noqa: E402,F401
from test_server import _fresh  # noqa: E402


@pytest.mark.parametrize("backend, charged", [("claude_cli", False), ("anthropic_api", True)])
def test_only_the_api_backend_charges(monkeypatch, backend, charged):
    monkeypatch.setenv("ROUGHCUT_BACKEND", backend)
    assert config.charges_money() is charged
    assert config.cost_terms() == {"backend": backend, "charged": charged}


def test_the_default_backend_charges_nothing(monkeypatch):
    monkeypatch.delenv("ROUGHCUT_BACKEND", raising=False)
    assert config.backend_name() == "claude_cli" and not config.charges_money()


@pytest.mark.parametrize("backend, charged", [("claude_cli", False), ("anthropic_api", True)])
def test_every_price_says_whether_it_is_a_bill(tmp_path, project, no_model, monkeypatch,
                                                backend, charged):
    monkeypatch.setenv("ROUGHCUT_BACKEND", backend)
    want = {"backend": backend, "charged": charged}
    with _fresh(tmp_path, project) as c:
        priced = {
            "ask": c.get("/api/ask/price?mode=full").json(),
            "find": c.get("/api/find/price").json(),
            "fx": c.get("/api/fx/price").json(),
            "audit (dry run)": c.post("/api/visual/audit", json={"dry_run": True}).json(),
            "backend": c.get("/api/backend").json(),
        }
        status = c.get("/api/status").json()
        priced["visual pass"] = status["visual"]
        priced["audit button"] = status["visual"]["audit"]
    for what, d in priced.items():
        assert d.get("charged") is charged, what
        if what != "backend":
            assert d.get("backend") == backend, what
