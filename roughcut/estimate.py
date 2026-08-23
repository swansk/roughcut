"""Ask the model how long its own next call will take, before making it.

Karl: *"the first step the AI must complete is an estimate of how long it will take to
apply the changes. This will (when estimate is complete) start a progress bar against
the AI workflow… The initial assessment must include milestones, which the agent will
complete, and then follow back up with the app on."*

So every model-backed operation opens with one cheap call on the per-unit role that
answers two questions — **how long**, and **what the checkpoints are** — and the bar
appears the moment that lands. Three rules govern it, and they are the whole design:

  * **An estimate must never block the real work.** Anything that goes wrong here —
    a timeout, garbage JSON, a budget refusal, a backend that is not logged in —
    returns the caller's hard-coded fallback and the job runs anyway. This module
    raises nothing.
  * **The keys are the app's, the labels and the weights are the model's.** A
    milestone is only worth drawing if something can *complete* it, so the caller
    offers the vocabulary of checkpoints it knows how to observe and the model chooses
    which of them apply, what to call them, and what share of the time each takes. A
    returned key nobody can observe would be a bar that stops moving, which is the
    failure this replaces.
  * **Validated the way a plan is** (`revise.validate_plan`): bounded milestone count,
    weights that are positive and finite, an ETA inside sane limits, one bounded
    re-ask, then the fallback. A plausible-looking estimate that says 4 seconds for a
    four-minute call is worse than no estimate, because the bar built on it lies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from . import config
from .inference import BudgetExceeded, InferenceError, complete
from .progress import milestone

ESTIMATE_SCHEMA = {
    "eta_s": 150,
    "milestones": [{"key": "one of the keys offered above", "weight": 2.0,
                    "label": "at most six words, for a person watching a bar"}],
}

SYSTEM = (
    "You estimate how long a job is about to take and what its checkpoints are, so a "
    "progress bar can be drawn before the job starts. You answer with JSON only. You "
    "are terse, you never invent checkpoint keys, and you would rather be honest about "
    "a long job than optimistic — a bar that finishes early is a bar nobody trusts.")

# Bounds. Below the floor an estimate is not worth a bar; above the ceiling it is past
# the per-call timeout the work itself is subject to, so it cannot be true.
ETA_MIN_S = 5.0
ETA_MAX_S = 3600.0
MAX_MILESTONES = 12
MIN_MILESTONES = 2


@dataclass
class Estimate:
    """What a bar needs before anything has happened."""
    eta_s: float
    milestones: list[dict]
    source: str = "model"          # "model" | "fallback"
    detail: str = ""               # why it fell back, when it did
    usage: dict = field(default_factory=dict)


def _finite(x: Any) -> float | None:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def validate_estimate(payload: Any, keys: Sequence[str] | None = None, *,
                      eta_limits: tuple[float, float] = (ETA_MIN_S, ETA_MAX_S),
                      max_milestones: int = MAX_MILESTONES,
                      min_milestones: int = MIN_MILESTONES) -> dict:
    """Strict, and for the same reason `validate_plan` is.

    Returns `{"eta_s": float, "milestones": [...]}` with the milestones in the order
    the caller offered them — the offered order is the order things actually happen in,
    and a model that returns "polish" before "read" is describing a job that does not
    exist. Reordering rather than rejecting, because the ordering is the app's fact and
    not the model's opinion.
    """
    if not isinstance(payload, dict):
        raise ValueError("expected an object with 'eta_s' and 'milestones'")
    eta = _finite(payload.get("eta_s"))
    if eta is None:
        raise ValueError(f"eta_s is not a number: {payload.get('eta_s')!r}")
    lo, hi = eta_limits
    if not (lo <= eta <= hi):
        raise ValueError(f"eta_s {eta:.0f}s outside {lo:.0f}-{hi:.0f}s")

    raw = payload.get("milestones")
    if not isinstance(raw, list) or not raw:
        raise ValueError("'milestones' must be a non-empty list")
    if len(raw) > max_milestones:
        raise ValueError(f"{len(raw)} milestones, at most {max_milestones}")

    allowed = list(keys) if keys is not None else None
    seen: dict[str, dict] = {}
    for i, m in enumerate(raw):
        if not isinstance(m, dict):
            raise ValueError(f"milestone {i} is not an object")
        key = str(m.get("key", "")).strip()
        if not key:
            raise ValueError(f"milestone {i} has no key")
        if allowed is not None and key not in allowed:
            raise ValueError(f"milestone {i} invents key {key!r}; "
                             f"expected one of {allowed}")
        if key in seen:
            raise ValueError(f"milestone {i} repeats key {key!r}")
        weight = _finite(m.get("weight", 1.0))
        if weight is None or not (0.0 < weight <= 1000.0):
            raise ValueError(f"milestone {key!r} has weight {m.get('weight')!r}")
        label = str(m.get("label", "")).strip()[:60] or key
        seen[key] = milestone(key, label, weight)

    if len(seen) < min_milestones:
        raise ValueError(f"{len(seen)} usable milestones, at least {min_milestones}")
    order = allowed if allowed is not None else list(seen)
    return {"eta_s": round(eta, 1),
            "milestones": [seen[k] for k in order if k in seen]}


def build_prompt(what: str, facts: str, checkpoints: Sequence[tuple[str, str]]) -> str:
    offered = "\n".join(f"* `{k}` — {meaning}" for k, meaning in checkpoints)
    return f"""A tool is about to run this job and wants to draw an honest progress bar \
for it before it starts.

## The job
{what}

{facts.strip()}

## The checkpoints the tool can actually observe
Only these. Each one is something the tool can detect happening; anything else would be
a bar that stops moving.

{offered}

## What to return
`eta_s` — how many seconds the whole job will take, wall clock, end to end.
`milestones` — the checkpoints above that apply, each with a `weight` (any positive
number; they are normalised into shares of the total time) and a `label` of at most six
words that a person watching a progress bar would understand. Drop a checkpoint that
does not apply to this job. Do not invent keys, and do not explain yourself."""


def estimate(what: str, facts: str, checkpoints: Sequence[tuple[str, str]], *,
             fallback: Estimate, role: str = config.ROLE_ANALYSIS,
             eta_limits: tuple[float, float] = (ETA_MIN_S, ETA_MAX_S)) -> Estimate:
    """One cheap call for a bar. Never raises: a bad estimate falls back and runs on.

    The call is bounded by its own short timeout rather than the 600s the real work
    gets — an estimate that takes minutes has already cost more than it saves.
    """
    keys = [k for k, _ in checkpoints]
    try:
        result = complete(
            build_prompt(what, facts, checkpoints), role=role,
            schema=ESTIMATE_SCHEMA, system=SYSTEM,
            validate=lambda p: validate_estimate(p, keys, eta_limits=eta_limits),
            retries=1, timeout_s=config.estimate_timeout_s())
    except (InferenceError, BudgetExceeded, ValueError) as exc:
        return Estimate(eta_s=fallback.eta_s, milestones=fallback.milestones,
                        source="fallback", detail=str(exc)[:200])
    except Exception as exc:                                   # pragma: no cover
        # Deliberately total. Whatever else can go wrong in a call made only to draw a
        # bar, it must not be the reason the real work never starts.
        return Estimate(eta_s=fallback.eta_s, milestones=fallback.milestones,
                        source="fallback", detail=f"{type(exc).__name__}: {exc}"[:200])
    got = result.content
    return Estimate(
        eta_s=float(got["eta_s"]), milestones=got["milestones"], source="model",
        usage={"input_tokens": result.input_tokens,
               "output_tokens": result.output_tokens,
               "projected_usd": result.projected_usd, "model": result.model,
               "latency_ms": result.latency_ms})
