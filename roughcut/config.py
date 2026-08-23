"""Configuration — the only place a model ID may appear.

SPEC §6: *"Model IDs are configuration, not architecture. Every model choice lives in
config.py as a named role, overridable by environment variable. No model ID literal
may appear at a call site."* Model families change faster than this project ships;
anything that pins one at a call site is a maintenance bomb.

Everything here is overridable by environment variable so a deployment can retarget
roles, prices and caps without touching code.
"""

from __future__ import annotations

import os
from pathlib import Path

# --- roles -----------------------------------------------------------------
# Named by what the call is *for*, never by which model happens to serve it.
#   skeleton  the agent that reasons over a whole bin and proposes an edit — the
#             hardest judgement in the system, so the top tier
#   analysis  per-unit scoring, run many times, low effort
#   judge     self-critique against the rubric
ROLE_SKELETON = "skeleton"
ROLE_ANALYSIS = "analysis"
ROLE_JUDGE = "judge"

_DEFAULT_MODELS = {
    ROLE_SKELETON: "claude-opus-5",
    ROLE_ANALYSIS: "claude-haiku-4-5-20251001",
    ROLE_JUDGE: "claude-sonnet-5",
}


def model_for(role: str) -> str:
    """Resolve a role to a model ID. `ROUGHCUT_MODEL_SKELETON` etc. override."""
    if role not in _DEFAULT_MODELS:
        raise ValueError(f"unknown role {role!r}; expected one of "
                         f"{sorted(_DEFAULT_MODELS)}")
    return os.environ.get(f"ROUGHCUT_MODEL_{role.upper()}", _DEFAULT_MODELS[role])


# --- pricing ---------------------------------------------------------------
# SPEC §6.3 reference prices, USD per million tokens (input, output). These feed
# `projected_usd` and nothing else — they are a projection, never a runtime
# dependency, and they are expected to drift. Verify before quoting.
_TIER_PRICES = {
    "top": (5.0, 25.0),
    "mid": (3.0, 15.0),
    "small": (1.0, 5.0),
}
_MODEL_TIERS = {
    "opus": "top",
    "sonnet": "mid",
    "fable": "mid",
    "haiku": "small",
}


def price_per_mtok(model: str) -> tuple[float, float]:
    """(input, output) USD per million tokens. Matched on family substring so a
    version bump does not silently fall back to the wrong tier."""
    for family, tier in _MODEL_TIERS.items():
        if family in model:
            return _TIER_PRICES[tier]
    return _TIER_PRICES["mid"]          # unknown family: assume mid, never free


def projected_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = price_per_mtok(model)
    return round(input_tokens / 1e6 * pin + output_tokens / 1e6 * pout, 6)


# --- backend & budget ------------------------------------------------------

def backend_name() -> str:
    """`claude_cli` (Max subscription, the development default) or `anthropic_api`."""
    return os.environ.get("ROUGHCUT_BACKEND", "claude_cli")


def budget_usd() -> float:
    """Cap enforced on `projected_usd` on *both* backends (SPEC §7).

    On the subscription backend there is no marginal dollar cost, but the cap still
    applies — otherwise developing on a Max plan silently destroys the ability to
    answer "is this affordable in production", which is why the discipline exists.
    """
    return float(os.environ.get("ROUGHCUT_BUDGET_USD", "15.0"))


def ledger_path() -> Path:
    return Path(os.environ.get(
        "ROUGHCUT_LEDGER", Path.home() / "work" / "roughcut-ledger.jsonl")).expanduser()


def call_timeout_s() -> int:
    """Per-call wall clock. 600 rather than 300: once every clip carries visual moments
    the originating prompt is ~39k tokens and a 15k-token plan takes ~200s on the CLI
    backend; a revision of a 16-shot cut with a long note ran past 300 and was killed
    with the work unrecoverable. The visual pass tool had raised its own default for the
    same reason a session earlier."""
    return int(os.environ.get("ROUGHCUT_CALL_TIMEOUT_S", "600"))


def estimate_timeout_s() -> int:
    """Wall clock for the small call that only exists to draw a progress bar.

    Much shorter than a real call on purpose: the estimate runs *before* the work, so
    every second it spends is a second the bar is not on screen. Measured at ~6s on the
    per-unit role; 90 is a ceiling, not a target, and hitting it means falling back to
    the hard-coded estimate rather than waiting out the 600s the work itself gets."""
    return int(os.environ.get("ROUGHCUT_ESTIMATE_TIMEOUT_S", "90"))
