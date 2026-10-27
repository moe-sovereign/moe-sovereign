"""services/routing_patterns.py — Embedding-space cold-start prior for the
Thompson bandits in services/inference.py, services/dynamic_router.py, and
services/routing_bandit.py.

Those bandits key on discrete buckets (model+category, or gate+banded fuzzy
score). Similar-but-distinct contexts share no statistics until their own
bucket independently clears its MIN_DATAPOINTS threshold. This module adds an
opt-in (config.ROUTING_PATTERN_PRIOR_ENABLED), distance-weighted Beta
pseudo-count prior drawn from the K nearest historical observations in BGE
embedding space, blended in only while a bucket's own observation count is
still below its MIN_DATAPOINTS threshold. Once a bucket has enough real
observations, callers stop consulting this module entirely — no change to
today's behaviour.

Storage is a single dedicated ChromaDB collection (moe_routing_patterns),
partitioned by a `bucket` metadata field so k-NN search never crosses bucket
boundaries. Each (namespace, key) bucket is a bounded ring buffer: a
Valkey-assigned deterministic slot means new observations overwrite old ones
in place, so no separate pruning job is needed.

Fail-open throughout: any Chroma/Valkey error returns the "no prior"/no-op
result rather than raising, matching the Thompson functions this feeds.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional, Sequence

import chromadb

import state
from config import ROUTING_PATTERN_RING_SIZE
from metrics import PROM_PATTERN_PRIOR

logger = logging.getLogger("moe.routing_patterns")

# Mirrors services/dynamic_router.py's CHROMA_HOST/CHROMA_PORT (read directly
# from the environment here, not imported, to avoid a circular import: that
# module imports this one for the Thompson-sampler cold-start prior).
CHROMA_HOST = os.getenv("CHROMA_HOST", "")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))

_chroma_client = None
_pattern_collection = None


def init_patterns() -> None:
    """Initializes the dedicated ChromaDB collection for routing patterns.

    Mirrors services/dynamic_router.py::init_router()'s ChromaDB block, but
    uses its own collection and stores raw BGE vectors directly (no
    embedding_function) since every caller already holds the embedding
    computed once per request in AgentState["query_embedding"].
    """
    global _chroma_client, _pattern_collection
    if not CHROMA_HOST:
        return
    try:
        _chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
        _pattern_collection = _chroma_client.get_or_create_collection(
            name="moe_routing_patterns",
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("connected to ChromaDB moe_routing_patterns collection.")
    except Exception as e:
        logger.error(f"❌ Failed to connect to ChromaDB moe_routing_patterns: {e}")


def _safe(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", value)


def _bucket_id(namespace: str, key: str) -> str:
    return f"{_safe(namespace)}:{_safe(key)}"


async def _next_slot(namespace: str, key: str) -> int:
    """Assigns the next ring-buffer slot for (namespace, key) via Valkey INCR.

    Falls back to slot 0 when Valkey is unavailable — observations still get
    stored (and overwrite each other), just without round-robin rotation.
    """
    if state.redis_client is None:
        return 0
    try:
        seq_key = f"moe:patseq:{_bucket_id(namespace, key)}"
        seq = await state.redis_client.incr(seq_key)
        return int(seq) % ROUTING_PATTERN_RING_SIZE
    except Exception:
        return 0


async def prior(
    namespace: str,
    key: str,
    embedding: Optional[Sequence[float]],
    k: int,
    cap: float,
) -> tuple[float, float]:
    """Distance-weighted Beta pseudo-count prior from the k nearest historical
    observations for (namespace, key).

    Returns (prior_positive, prior_total) — both 0.0 when the collection is
    unavailable, no embedding was given, no neighbors exist in this bucket,
    or on any query failure. `prior_total` never exceeds `cap`, regardless of
    how many or how close the neighbors are, so the prior can never outweigh
    `cap` real observations once they start accumulating.

    Every call that actually attempts a consultation (i.e. an embedding was
    given) increments PROM_PATTERN_PRIOR{namespace, outcome} exactly once, so
    a rollout can be watched for whether the prior is firing at all
    ("used" vs "empty") versus failing outright ("unavailable"/"error").
    """
    global _pattern_collection
    if _pattern_collection is None:
        init_patterns()
    # NOTE: `not embedding`/`if embedding` is deliberately avoided — embedding
    # may be a numpy array, whose truth value for len()>1 is ambiguous.
    if embedding is None or len(embedding) == 0:
        return 0.0, 0.0
    if _pattern_collection is None:
        PROM_PATTERN_PRIOR.labels(namespace=namespace, outcome="unavailable").inc()
        return 0.0, 0.0
    try:
        results = _pattern_collection.query(
            query_embeddings=[[float(x) for x in embedding]],
            n_results=max(1, k),
            where={"bucket": _bucket_id(namespace, key)},
        )
        ids = results.get("ids") or [[]]
        if not ids or not ids[0]:
            PROM_PATTERN_PRIOR.labels(namespace=namespace, outcome="empty").inc()
            return 0.0, 0.0
        distances = results["distances"][0]
        metadatas = results["metadatas"][0]

        weighted_pos = 0.0
        weighted_total = 0.0
        for dist, meta in zip(distances, metadatas):
            weight = max(0.0, 1.0 - float(dist))
            if weight <= 0.0:
                continue
            weighted_total += weight
            if bool(meta.get("positive")):
                weighted_pos += weight

        if weighted_total <= 0.0:
            PROM_PATTERN_PRIOR.labels(namespace=namespace, outcome="empty").inc()
            return 0.0, 0.0

        scale = (cap / weighted_total) if weighted_total > cap else 1.0
        PROM_PATTERN_PRIOR.labels(namespace=namespace, outcome="used").inc()
        return weighted_pos * scale, weighted_total * scale
    except Exception as e:
        logger.debug(f"routing_patterns.prior: query failed (fail-open): {e}")
        PROM_PATTERN_PRIOR.labels(namespace=namespace, outcome="error").inc()
        return 0.0, 0.0


async def record(
    namespace: str,
    key: str,
    embedding: Optional[Sequence[float]],
    positive: bool,
) -> None:
    """Upserts one observation into the (namespace, key) ring buffer.

    No-op when the collection is unavailable or no embedding was given —
    identical to today's behaviour for callers that never pass one.
    """
    global _pattern_collection
    if _pattern_collection is None:
        init_patterns()
    if _pattern_collection is None or embedding is None or len(embedding) == 0:
        return
    try:
        bucket = _bucket_id(namespace, key)
        slot = await _next_slot(namespace, key)
        point_id = f"{bucket}:{slot}"
        _pattern_collection.upsert(
            ids=[point_id],
            embeddings=[[float(x) for x in embedding]],
            metadatas=[{"bucket": bucket, "positive": bool(positive)}],
        )
    except Exception as e:
        logger.debug(f"routing_patterns.record: upsert failed (fail-open): {e}")
