"""services/routing_bandit.py — contextual Thompson bandit for retrieval gates.

The routing gates ``skip_research`` and ``enable_graphrag`` used to be decided by
fixed fuzzy thresholds (``tnorm < 0.30`` / ``< 0.35``). Those constants were
ungrounded. This module replaces the *authority* of that decision with a learned
contextual bandit while keeping the fuzzy/complexity heuristic as the context
(features) and as the cold-start fallback.

Design (mirrors the expert Thompson sampler in ``services/inference.py``):
    * Each binary gate has two arms — a "rich" action that runs the retrieval
      node (fetch / on) and a "cheap" action that skips it (skip / off).
    * Per ``(gate, context, action)`` we keep Beta-Bernoulli counts in Valkey:
      ``total`` and ``positive`` (a positive = the request was answered
      adequately under that action).
    * At decision time we Thompson-sample a success probability per arm and pick
      the higher sample — no threshold constant. A configurable cost prior gives
      the cheap arm an optimistic head-start so ties resolve toward saving
      inference.

Cold start: until BOTH arms of a ``(gate, context)`` have at least
``ROUTING_BANDIT_MIN_DATAPOINTS`` observations, the caller's heuristic default is
returned unchanged. The gate therefore never performs worse than the fuzzy
baseline while the bandit accumulates evidence.
"""

from __future__ import annotations

import random
import re

from typing import Optional, Sequence

import state
from services import routing_patterns
from config import (
    ROUTING_BANDIT_ENABLED,
    ROUTING_BANDIT_MIN_DATAPOINTS,
    ROUTING_BANDIT_COST_PRIOR,
    ROUTING_BANDIT_CONTEXT_BANDS,
    ROUTING_PATTERN_PRIOR_ENABLED,
    ROUTING_PATTERN_PRIOR_K,
    ROUTING_PATTERN_PRIOR_CAP,
)

# Per-gate arm labels. "rich" = run the retrieval node; "cheap" = skip it.
_RICH = {"research": "fetch", "graphrag": "on"}
_CHEAP = {"research": "skip", "graphrag": "off"}


def band(score: float, bands: int = ROUTING_BANDIT_CONTEXT_BANDS) -> int:
    """Discretise a [0,1] fuzzy score into one of ``bands`` buckets (clamped)."""
    if bands < 1:
        bands = 1
    return max(0, min(bands - 1, int(score * bands)))


def _key(gate: str, context: str, action: str) -> str:
    """Valkey key for one bandit arm: moe:routebandit:{gate}:{context}:{action}."""
    safe = re.sub(r"[^a-zA-Z0-9_\-|]", "_", f"{gate}:{context}:{action}")
    return f"moe:routebandit:{safe}"


async def _arm_stats(gate: str, context: str, action: str) -> tuple[int, int]:
    """Return (positive, total) counts for one arm, or (0, 0) when unavailable."""
    if state.redis_client is None:
        return 0, 0
    try:
        data = await state.redis_client.hgetall(_key(gate, context, action))
        return int(data.get("positive", 0)), int(data.get("total", 0))
    except Exception:
        return 0, 0


async def decide(
    gate: str,
    context: str,
    heuristic_default: bool,
    query_embedding: Optional[Sequence[float]] = None,
) -> tuple[bool, str]:
    """Decide whether to run the rich (retrieval) action for ``gate``.

    Args:
        gate:              "research" or "graphrag".
        context:           discretised context bucket (e.g. "moderate|v1").
        heuristic_default: the fuzzy/complexity decision (True = run retrieval).
        query_embedding:   BGE embedding of the request, when available. Used
                            only as an embedding-space cold-start prior
                            (services/routing_patterns.py) for whichever arm
                            is still below ROUTING_BANDIT_MIN_DATAPOINTS, when
                            ROUTING_PATTERN_PRIOR_ENABLED. No effect otherwise.

    Returns:
        (run_rich, source) where source is "bandit" or "heuristic". ``run_rich``
        uses the same polarity as ``heuristic_default`` (True = fetch/on).
    """
    if not ROUTING_BANDIT_ENABLED or state.redis_client is None or not context:
        return heuristic_default, "heuristic"

    pos_r, tot_r = await _arm_stats(gate, context, _RICH[gate])
    pos_c, tot_c = await _arm_stats(gate, context, _CHEAP[gate])

    alpha_r, beta_r = pos_r + 1, (tot_r - pos_r) + 1
    alpha_c, beta_c = pos_c + 1, (tot_c - pos_c) + 1
    r_ready = tot_r >= ROUTING_BANDIT_MIN_DATAPOINTS
    c_ready = tot_c >= ROUTING_BANDIT_MIN_DATAPOINTS

    if ROUTING_PATTERN_PRIOR_ENABLED and query_embedding is not None:
        # Only an arm that is itself still below MIN_DATAPOINTS consults the
        # prior, and only a *non-empty* prior can promote it out of cold
        # start — an embedding with zero neighbors changes nothing, same as
        # the flag being off.
        if not r_ready:
            prior_pos, prior_total = await routing_patterns.prior(
                "gate", f"{gate}:{_RICH[gate]}:{context}", query_embedding,
                ROUTING_PATTERN_PRIOR_K, ROUTING_PATTERN_PRIOR_CAP,
            )
            if prior_total > 0.0:
                alpha_r += prior_pos
                beta_r += (prior_total - prior_pos)
                r_ready = True
        if not c_ready:
            prior_pos, prior_total = await routing_patterns.prior(
                "gate", f"{gate}:{_CHEAP[gate]}:{context}", query_embedding,
                ROUTING_PATTERN_PRIOR_K, ROUTING_PATTERN_PRIOR_CAP,
            )
            if prior_total > 0.0:
                alpha_c += prior_pos
                beta_c += (prior_total - prior_pos)
                c_ready = True

    if not (r_ready and c_ready):
        # Cold start: unchanged from today, whether because the prior is off,
        # no embedding was given, or it found no neighbors for this bucket.
        return heuristic_default, "heuristic"

    theta_rich = random.betavariate(alpha_r, beta_r)
    # Cost prior: optimistic α head-start for the cheaper arm steers ties toward
    # skipping retrieval (the lower-cost outcome) without a hard rule.
    theta_cheap = random.betavariate(alpha_c + ROUTING_BANDIT_COST_PRIOR, beta_c)
    return (theta_rich > theta_cheap), "bandit"


async def record(
    gate: str,
    context: str,
    ran_rich: bool,
    success: bool,
    query_embedding: Optional[Sequence[float]] = None,
) -> None:
    """Record the outcome of a gate decision into the arm that was actually taken.

    When ``query_embedding`` is given, also records the observation into the
    embedding-space pattern store (services/routing_patterns.py). No-op for
    that part when no embedding is passed — identical to today's behaviour.
    """
    if state.redis_client is None or not context:
        return
    action = _RICH[gate] if ran_rich else _CHEAP[gate]
    try:
        pipe = state.redis_client.pipeline()
        key = _key(gate, context, action)
        pipe.hincrby(key, "total", 1)
        if success:
            pipe.hincrby(key, "positive", 1)
        await pipe.execute()
    except Exception:
        pass
    if query_embedding is not None:
        await routing_patterns.record("gate", f"{gate}:{action}:{context}", query_embedding, success)
