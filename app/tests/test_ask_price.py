"""GET /api/ask/price — the price on every Ask-family button before the click (INTAKE
M16 I16.0f): the first cut, Cut from the bin, the Ask panel's Ask and a shot's Ask.
Free (it reads records, never the model), fitted to the asks this project already
paid for, and priced for the deep role's model as it is now."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from roughcut import config, inference  # noqa: E402

from test_server import _fresh  # noqa: E402


class _Never:
    name = "never"

    def complete(self, request):
        raise AssertionError("a price must not call the model")


@pytest.fixture
def no_model(monkeypatch):
    """No model call, and the default tiers: a shell or a service that pins a role's
    model (ROUGHCUT_MODEL_SKELETON=claude-sonnet-5-5) must not move these prices."""
    for role in (config.ROLE_SKELETON, config.ROLE_ANALYSIS):
        monkeypatch.delenv(f"ROUGHCUT_MODEL_{role.upper()}", raising=False)
    inference.set_backend(_Never())
    yield
    inference.set_backend(None)


def _ask_record(server, name: str, tin: int, tout: int, *, shot: bool = False,
                model: str = "claude-opus-5") -> None:
    plan = {"segments": [], "notes": "",
            "usage": {"input_tokens": tin, "output_tokens": tout, "model": model,
                      "projected_usd": config.projected_usd(model, tin, tout)}}
    if shot:
        plan["focus"] = {"index": 0, "clip": "CLIP_A.MP4", "in": 1.0, "out": 3.0, "with": 1}
    (server.STATE["asks"] / f"{name}.json").write_text(
        json.dumps({"job": name, "note": "n", "story": "", "plan": plan}), encoding="utf-8")


def _estimate_usd() -> float:
    import server
    return config.projected_usd(config.model_for(config.ROLE_ANALYSIS),
                                *server.ASK_ESTIMATE_TOKENS)


def test_with_no_asks_on_the_bin_a_typical_one_is_the_price(tmp_path, project, no_model):
    import server

    with _fresh(tmp_path, project) as c:
        deep = config.model_for(config.ROLE_SKELETON)
        cut = config.projected_usd(deep, *server.ASK_TYPICAL_TOKENS["cut"]) + _estimate_usd()
        for mode in ("first", "bin", "full"):
            d = c.get(f"/api/ask/price?mode={mode}").json()
            assert d["usd"] == round(cut, 2) and d["fitted_on"] == 0, d
            assert "none yet" in d["basis"] and deep in d["basis"]
        shot = c.get("/api/ask/price?mode=shot").json()
        assert shot["usd"] == round(config.projected_usd(deep, *server.ASK_TYPICAL_TOKENS["shot"]), 2)
        assert 0 < shot["usd"] < d["usd"]
        assert c.get("/api/ask/price?mode=everything").status_code == 400
        # the route is not taken for a job id
        assert c.get("/api/ask/price").json()["mode"] == "full"


def test_the_price_is_fitted_to_this_projects_asks_and_follows_the_model(tmp_path, project,
                                                                          no_model, monkeypatch):
    """Killington's shape: whole-cut asks at 39–45k tokens in, 9.5–15k out (~$0.46–0.58
    on the top tier) and a shot ask at 28.5k / 0.5k (~$0.15). The middle of the last few
    of a kind, re-priced at today's deep model — a tier move moves the price with it."""
    import server

    with _fresh(tmp_path, project) as c:
        _ask_record(server, "a", 45292, 13736)
        _ask_record(server, "b", 39403, 15314)
        _ask_record(server, "c", 40388, 14682)
        _ask_record(server, "d", 44657, 9545)
        _ask_record(server, "s", 28487, 476, shot=True)
        (server.STATE["asks"] / "recovered.json").write_text(
            json.dumps({"job": "recovered", "plan": {"segments": []}}), encoding="utf-8")
        deep = config.model_for(config.ROLE_SKELETON)
        fits = sorted(config.projected_usd(deep, i, o) for i, o in
                      ((45292, 13736), (39403, 15314), (40388, 14682), (44657, 9545)))
        want = (fits[1] + fits[2]) / 2 + _estimate_usd()
        d = c.get("/api/ask/price?mode=full").json()
        assert d["usd"] == round(want, 2) and d["fitted_on"] == 4, d
        # priced at the top tier: between the cheapest and the dearest of the four there
        assert deep == config.DEEP_MODEL
        assert fits[0] <= d["usd"] - _estimate_usd() <= fits[-1] + 0.005, \
            "Killington's asks priced at the top tier"
        assert "last 4 asks about the whole cut" in d["basis"]
        s = c.get("/api/ask/price?mode=shot").json()
        assert s["usd"] == round(config.projected_usd(config.DEEP_MODEL, 28487, 476), 2)
        assert s["fitted_on"] == 1

        # the deep role moved to the mid tier: the same tokens, priced there
        monkeypatch.setenv("ROUGHCUT_MODEL_SKELETON", config.QUICK_MODEL)
        mid = c.get("/api/ask/price?mode=full").json()
        assert mid["usd"] < d["usd"] and config.QUICK_MODEL in mid["basis"]


def test_only_the_newest_five_asks_of_a_kind_set_the_price(tmp_path, project, no_model):
    """The fit is the middle of the newest ASK_FIT_LAST asks of a kind, so an old run of
    outliers (the first, unbounded asks) stops setting the price once five newer ones
    exist. The fitted test above has four records and never reached the window."""
    import os
    import time

    import server

    assert server.ASK_FIT_LAST == 5
    with _fresh(tmp_path, project) as c:
        newest = [(40000, 12000), (41000, 12500), (42000, 13000), (43000, 13500),
                  (44000, 14000)]
        old = [(200000, 60000), (210000, 61000), (220000, 62000)]   # three huge, early
        now = time.time()
        for k, (tin, tout) in enumerate(old + newest):
            _ask_record(server, f"w{k}", tin, tout)
            stamp = now - 1000 + k * 10           # in this order: the old ones oldest
            os.utime(server.STATE["asks"] / f"w{k}.json", (stamp, stamp))
        deep = config.model_for(config.ROLE_SKELETON)
        fits = sorted(config.projected_usd(deep, i, o) for i, o in newest)
        d = c.get("/api/ask/price?mode=full").json()
        assert d["fitted_on"] == 5, d
        assert d["usd"] == round(fits[2] + _estimate_usd(), 2), d
        assert "last 5 asks about the whole cut" in d["basis"]
