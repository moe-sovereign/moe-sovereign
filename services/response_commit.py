"""Quality-atomic, retryable persistence for completed graph responses.

The merger only constructs a candidate.  This module is the sole owner of
reusable response/knowledge/episode/learning writes and is called only after a
final quality pass or an explicit HITL approval.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping

import state
from config import (
    CACHE_MIN_RESPONSE_LEN,
    ORCHESTRATION_TIMEOUT,
    JUDGE_MODEL,
    KAFKA_TOPIC_INGEST,
    PRECISION_CACHE_POLICY,
)
from parsing import _expert_category, _parse_expert_confidence
from services.pipeline.contracts import canonical_json_hash

logger = logging.getLogger("MOE-SOVEREIGN")

_JOURNAL_PREFIX = "moe:response_commit:"
_LOCK_TTL_SECONDS = 300


def _json_safe(value: Any) -> Any:
    """Round-trip a state subset into checkpointer/gate-safe JSON values."""
    return json.loads(json.dumps(value, default=str))


def _elapsed_ms(state_: Mapping[str, Any]) -> int:
    """Milliseconds since request start, derived from the shared request deadline."""
    deadline = float(state_.get("request_deadline_monotonic") or 0.0)
    if deadline <= 0:
        return 0
    started = deadline - float(ORCHESTRATION_TIMEOUT)
    return max(0, int((time.monotonic() - started) * 1000))


def build_response_commit_payload(
    state_: Mapping[str, Any],
    *,
    final_response: str | None = None,
) -> dict[str, Any]:
    """Freeze only data needed by post-quality persistence and HITL resume."""
    response = (
        str(final_response)
        if final_response is not None
        else str(state_.get("final_response") or "")
    )
    current_iteration = int(state_.get("agentic_iteration") or 0)
    evidence = [
        item
        for item in (state_.get("mcp_evidence") or [])
        if isinstance(item, Mapping)
        and int(item.get("iteration") or 0) == current_iteration
    ]
    payload = {
        "request_id": str(state_.get("response_id") or ""),
        "user_id": str(state_.get("user_id") or ""),
        "input": str(state_.get("input") or ""),
        "final_response": response,
        "response_hash": canonical_json_hash(response),
        "precision_contract_hash": str(
            state_.get("precision_contract_hash") or ""
        ),
        "precision_catalog_hash": str(
            state_.get("precision_catalog_hash") or ""
        ),
        "precision_binding_hash": str(
            state_.get("precision_binding_hash") or ""
        ),
        "precision_bound_response_hash": str(
            state_.get("precision_bound_response_hash") or ""
        ),
        "evidence_hash": canonical_json_hash(
            [
                {
                    "task_id": item.get("task_id"),
                    "tool": item.get("tool"),
                    "contract_hash": item.get("contract_hash"),
                    "input_hash": item.get("input_hash"),
                    "result_hash": item.get("result_hash"),
                }
                for item in evidence
            ]
        ) if evidence else "",
        "no_cache": bool(state_.get("no_cache")),
        "cache_hit": bool(state_.get("cache_hit")),
        "guard_blocked": bool(state_.get("guard_blocked")),
        "plan": state_.get("plan") or [],
        "expert_results": state_.get("expert_results") or [],
        "expert_models_used": state_.get("expert_models_used") or [],
        "cost_tier": str(state_.get("cost_tier") or ""),
        "template_id": str(state_.get("template_id") or ""),
        "template_name": str(state_.get("template_name") or ""),
        "complexity_level": str(state_.get("complexity_level") or ""),
        "causal_intervention": state_.get("causal_intervention"),
        "tenant_ids": state_.get("tenant_ids") or [],
        "prompt_tokens": int(state_.get("prompt_tokens") or 0),
        "completion_tokens": int(state_.get("completion_tokens") or 0),
        "graph_context": str(state_.get("graph_context") or ""),
        "mcp_result": str(state_.get("mcp_result") or ""),
        "math_result": str(state_.get("math_result") or ""),
        "web_research": str(state_.get("web_research") or ""),
        "cached_facts": str(state_.get("cached_facts") or ""),
        "retrieved_graph_chunks": state_.get("retrieved_graph_chunks") or [],
        "routing_bandit_context": str(
            state_.get("routing_bandit_context") or ""
        ),
        "skip_research": bool(state_.get("skip_research")),
        "enable_graphrag": bool(state_.get("enable_graphrag", True)),
        "tier_escalations": int(state_.get("tier_escalations") or 0),
        "response_commit_context": state_.get("response_commit_context") or {},
        "query_embedding": state_.get("query_embedding") or [],
        "wall_clock_ms": _elapsed_ms(state_),
    }
    return _json_safe(payload)


def response_commit_key(payload: Mapping[str, Any]) -> str:
    """Stable logical commit identity required for graph resume/idempotency."""
    material = {
        "request_id": payload.get("request_id"),
        "response_hash": payload.get("response_hash"),
        "contract_hash": payload.get("precision_contract_hash"),
        "binding_hash": payload.get("precision_binding_hash"),
        "evidence_hash": payload.get("evidence_hash"),
    }
    return canonical_json_hash(material)


def _cache_ids(payload: Mapping[str, Any]) -> tuple[str, str]:
    normalized = re.sub(
        r"\s+", " ", str(payload.get("input") or "").lower().strip().rstrip("?!.,;")
    )
    query_hash = hashlib.sha256(normalized.encode()).hexdigest()[:24]
    contract_scope = canonical_json_hash(
        {
            "catalog": payload.get("precision_catalog_hash") or "legacy",
            "contract": payload.get("precision_contract_hash") or "legacy",
            "input": normalized,
        }
    )[:24]
    document_id = canonical_json_hash(
        {
            "response": payload.get("response_hash"),
            "contract_scope": contract_scope,
        }
    )[:32]
    if payload.get("precision_contract_hash"):
        query_key = f"moe:qcache:v2:{contract_scope}:{query_hash}"
    else:
        query_key = f"moe:qcache:{query_hash}"
    return document_id, query_key


async def _read_journal(key: str) -> dict[str, Any]:
    # Fail-open like every other Redis-backed feature in this codebase (rate
    # limiter, planner cache, node reservation): a Redis outage must degrade
    # dedup/idempotency, never take down an otherwise-complete response.
    # commit_response_payload's callers already re-run sinks idempotently
    # when the journal comes back empty, so "no journal" is a safe default.
    if state.redis_client is None:
        return {}
    try:
        raw = await state.redis_client.get(key)
    except Exception as exc:
        logger.debug("Response-commit journal read failed (fail-open): %s", exc)
        return {}
    if not raw:
        return {}
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


async def _write_journal(key: str, journal: Mapping[str, Any]) -> None:
    if state.redis_client is None:
        return
    try:
        await state.redis_client.set(key, json.dumps(journal, default=str), ex=604800)
    except Exception as exc:
        logger.debug("Response-commit journal write failed (fail-open): %s", exc)


async def _run_learning_signals(payload: Mapping[str, Any]) -> None:
    expert_results = [str(item) for item in payload.get("expert_results") or []]
    refined = set(
        str(item)
        for item in (
            (payload.get("response_commit_context") or {}).get("judge_refined_cats")
            or []
        )
    )
    rank = {"low": 0, "medium": 1, "high": 2}
    best: dict[str, str] = {}
    for result in expert_results:
        category = _expert_category(result)
        confidence = _parse_expert_confidence(result)
        if rank.get(confidence, 0) > rank.get(best.get(category, "low"), -1):
            best[category] = confidence
    from services.inference import _record_expert_outcome

    query_embedding = payload.get("query_embedding") or None

    for model_category in payload.get("expert_models_used") or []:
        if "::" not in str(model_category):
            continue
        model, category = str(model_category).split("::", 1)
        if category in refined:
            await _record_expert_outcome(model, category, positive=False, query_embedding=query_embedding)
        elif best.get(category) == "high":
            await _record_expert_outcome(model, category, positive=True, query_embedding=query_embedding)
        elif best.get(category) == "low":
            await _record_expert_outcome(model, category, positive=False, query_embedding=query_embedding)

    routing_context = str(payload.get("routing_bandit_context") or "")
    if "|||" in routing_context:
        from services.routing_bandit import record

        research_context, graph_context = routing_context.split("|||", 1)
        joined = " ".join(expert_results)
        research_ok = not bool(re.search(
            r"(?i)cannot access|no web|don't have (?:internet|web|access)|unable to (?:browse|access)",
            joined,
        ))
        await record(
            "research", research_context,
            not bool(payload.get("skip_research")), research_ok,
            query_embedding=query_embedding,
        )
        await record(
            "graphrag", graph_context,
            bool(payload.get("enable_graphrag", True)), not bool(refined),
            query_embedding=query_embedding,
        )

    from services.policy_log import log_policy_event

    await log_policy_event(
        chat_id=str(payload.get("request_id") or ""),
        query=str(payload.get("input") or ""),
        complexity=str(payload.get("complexity_level") or "unknown"),
        plan_categories=[
            item.get("category", "")
            for item in payload.get("plan") or []
            if isinstance(item, dict)
        ],
        experts_called=payload.get("expert_models_used") or [],
        confidence_map=best,
        judge_refined_cats=refined,
        refinement_rounds=len(refined),
        fast_path=bool(
            (payload.get("response_commit_context") or {}).get("fast_path")
        ),
        cache_hit=False,
        web_research_used=bool(payload.get("web_research")),
        graphrag_used=bool(payload.get("graph_context")),
        template_id=str(payload.get("template_name") or ""),
        latency_s=0.0,
        tier_escalations=int(payload.get("tier_escalations") or 0),
    )

    corrections = (
        (payload.get("response_commit_context") or {}).get("corrections") or []
    )
    if corrections and state.graph_manager is not None:
        from graph_rag.corrections import store_correction

        for correction in corrections:
            await store_correction(
                state.graph_manager.driver,
                **dict(correction),
            )


async def _sink_map(payload: Mapping[str, Any]) -> dict[str, Callable[[], Awaitable[None]]]:
    from episodic_memory import log_episode
    from services.helpers import _self_evaluate, _store_response_metadata
    from services.kafka import _kafka_publish

    response = str(payload.get("final_response") or "")
    document_id, query_key = _cache_ids(payload)
    confidence_map = {"high": 0.9, "medium": 0.6, "low": 0.3}
    confidences = [
        confidence_map.get(_parse_expert_confidence(str(item)), 0.5)
        for item in payload.get("expert_results") or []
    ]
    ingest_confidence = sum(confidences) / len(confidences) if confidences else 0.5
    expert_domain = next(
        (
            str(item.get("category") or "")
            for item in payload.get("plan") or []
            if isinstance(item, dict) and item.get("category") != "precision_tools"
        ),
        "precision_tools" if payload.get("precision_contract_hash") else "general",
    )
    commit_key = response_commit_key(payload)

    async def cache_chroma() -> None:
        if state.cache_collection is None:
            raise RuntimeError("cache_collection_unavailable")
        await asyncio.to_thread(
            state.cache_collection.upsert,
            ids=[document_id],
            documents=[response],
            metadatas=[{
                "ts": datetime.now(timezone.utc).isoformat(),
                "input": str(payload.get("input") or "")[:200],
                "flagged": False,
                "expert_domain": expert_domain,
                "confidence": round(ingest_confidence, 3),
                "commit_key": commit_key,
                "contract_hash": str(payload.get("precision_contract_hash") or ""),
            }],
        )

    async def cache_l0() -> None:
        if state.redis_client is None:
            raise RuntimeError("redis_unavailable")
        await state.redis_client.setex(query_key, 1800, response)

    async def response_metadata() -> None:
        await _store_response_metadata(
            str(payload.get("request_id") or ""),
            str(payload.get("input") or ""),
            list(payload.get("expert_models_used") or []),
            document_id,
            plan=list(payload.get("plan") or []),
            cost_tier=str(payload.get("cost_tier") or ""),
            template_id=str(payload.get("template_id") or ""),
            causal_intervention=payload.get("causal_intervention"),
        )

    async def routing_telemetry() -> None:
        import telemetry

        await telemetry.record_routing_decision(
            state._userdb_pool,
            str(payload.get("request_id") or ""),
            dict(payload),
            wall_clock_ms=int(payload.get("wall_clock_ms") or 0),
        )

    async def episode() -> None:
        driver = getattr(state.graph_manager, "driver", None)
        if driver is not None:
            await log_episode(driver, dict(payload))

    async def kafka_ingest() -> None:
        markers = {
            "requires", "must", "necessary", "prerequisite", "needed",
            "location", "on-site", "on premises", "physically", "necessitates",
        }
        await _kafka_publish(KAFKA_TOPIC_INGEST, {
            "response_id": payload.get("request_id"),
            "input": payload.get("input"),
            "answer": response,
            "domain": expert_domain,
            "source_expert": expert_domain,
            "source_model": (
                (payload.get("expert_models_used") or [JUDGE_MODEL])[0]
            ),
            "template_name": payload.get("template_name"),
            "confidence": round(ingest_confidence, 2),
            "knowledge_type": (
                "procedural" if any(word in response.casefold() for word in markers)
                else "factual"
            ),
            "synthesis_insight": (
                (payload.get("response_commit_context") or {}).get("synthesis_insight")
            ),
            "tenant_id": (
                (payload.get("tenant_ids") or [None])[0]
            ),
            "commit_key": commit_key,
        })

    async def self_correction() -> None:
        from self_correction import process_merger_output

        await process_merger_output(
            query=str(payload.get("input") or ""),
            expert_results=list(payload.get("expert_results") or []),
            final_response=response,
            plan=list(payload.get("plan") or []),
            redis_client=state.redis_client,
            state_data=dict(payload),
        )

    async def retrieval_attribution() -> None:
        from services.retrieval_attribution import record_attribution

        await record_attribution(
            getattr(state.graph_manager, "driver", None),
            list(payload.get("retrieved_graph_chunks") or []),
            response,
        )

    async def self_evaluate_queued() -> None:
        asyncio.create_task(_self_evaluate(
            str(payload.get("request_id") or ""),
            str(payload.get("input") or ""),
            response,
            document_id,
            template_name=str(payload.get("template_name") or ""),
            complexity=str(payload.get("complexity_level") or ""),
        ))

    sinks = {
        "cache_chroma": cache_chroma,
        "cache_l0": cache_l0,
        "response_metadata": response_metadata,
        "routing_telemetry": routing_telemetry,
        "episode": episode,
        "kafka_ingest": kafka_ingest,
        "self_correction": self_correction,
        "retrieval_attribution": retrieval_attribution,
        "learning_signals": lambda: _run_learning_signals(payload),
        "self_evaluate_queued": self_evaluate_queued,
    }
    if payload.get("precision_contract_hash") and PRECISION_CACHE_POLICY != "typed":
        for name in (
            "cache_chroma", "cache_l0", "response_metadata",
            "self_evaluate_queued",
        ):
            sinks.pop(name, None)
    return sinks


async def _record_routing_telemetry_only(payload: Mapping[str, Any]) -> None:
    """Write routing telemetry for requests whose other commit sinks are skipped. Never raises."""
    try:
        import telemetry

        await telemetry.record_routing_decision(
            state._userdb_pool,
            str(payload.get("request_id") or ""),
            dict(payload),
            wall_clock_ms=int(payload.get("wall_clock_ms") or 0),
        )
    except Exception as exc:
        logger.debug("Routing telemetry (no-cache path) failed: %s", exc)


async def commit_response_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Commit all sinks once; preserve per-sink progress across retries."""
    frozen = _json_safe(dict(payload))
    response = str(frozen.get("final_response") or "")
    expected_hash = canonical_json_hash(response)
    if not response or frozen.get("response_hash") != expected_hash:
        return {"status": "blocked", "errors": ["response_commit_hash_mismatch"]}
    if (
        frozen.get("precision_bound_response_hash")
        and frozen.get("precision_bound_response_hash") != expected_hash
    ):
        return {"status": "blocked", "errors": ["response_commit_binding_mismatch"]}
    if frozen.get("precision_contract_hash") and (
        not frozen.get("precision_binding_hash")
        or not frozen.get("precision_bound_response_hash")
    ):
        return {"status": "blocked", "errors": ["response_commit_unbound_precision"]}
    if frozen.get("guard_blocked") or frozen.get("cache_hit"):
        return {"status": "skipped", "errors": []}
    if frozen.get("no_cache") or len(response) <= CACHE_MIN_RESPONSE_LEN:
        # Caches, knowledge ingest and learning sinks stay skipped (benchmark
        # isolation), but routing telemetry is observability, not learning.
        await _record_routing_telemetry_only(frozen)
        return {"status": "skipped", "errors": []}

    commit_key = response_commit_key(frozen)
    journal_key = f"{_JOURNAL_PREFIX}{commit_key}"
    journal = await _read_journal(journal_key)
    if journal.get("status") == "complete":
        return {
            "status": "reused",
            "commit_key": commit_key,
            "sinks": journal.get("sinks", {}),
            "errors": [],
        }

    lock_key = f"{journal_key}:lock"
    lock_token = uuid.uuid4().hex
    if state.redis_client is not None:
        # Fail-open: a Redis error here must not discard an otherwise-complete
        # pipeline run (this node sits last before END, so raising here throws
        # away already-finished planner/expert/merger/judge work). Without
        # Redis to coordinate, we cannot detect a concurrent duplicate commit
        # -- proceeding anyway (redundant idempotent sink writes) is far
        # cheaper than losing the response outright.
        try:
            acquired = await state.redis_client.set(
                lock_key, lock_token, nx=True, ex=_LOCK_TTL_SECONDS
            )
        except Exception as exc:
            logger.debug("Response-commit lock acquisition failed (fail-open): %s", exc)
            acquired = True
        if not acquired:
            journal = await _read_journal(journal_key)
            return {
                "status": "reused" if journal.get("status") == "complete" else "busy",
                "commit_key": commit_key,
                "sinks": journal.get("sinks", {}),
                "errors": [],
            }

    try:
        journal = journal or {
            "commit_key": commit_key,
            "request_id": frozen.get("request_id"),
            "response_hash": expected_hash,
            "status": "in_progress",
            "sinks": {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        runners = await _sink_map(frozen)
        errors: list[str] = []
        for name, runner in runners.items():
            if (journal.get("sinks") or {}).get(name, {}).get("status") == "done":
                continue
            try:
                await runner()
                journal.setdefault("sinks", {})[name] = {
                    "status": "done",
                    "at": datetime.now(timezone.utc).isoformat(),
                }
            except Exception as exc:
                error = f"{name}:{type(exc).__name__}"
                errors.append(error)
                journal.setdefault("sinks", {})[name] = {
                    "status": "failed",
                    "error": str(exc)[:300],
                    "at": datetime.now(timezone.utc).isoformat(),
                }
            await _write_journal(journal_key, journal)
        journal["status"] = "complete" if not errors else "partial"
        journal["updated_at"] = datetime.now(timezone.utc).isoformat()
        await _write_journal(journal_key, journal)
        return {
            "status": journal["status"],
            "commit_key": commit_key,
            "sinks": journal.get("sinks", {}),
            "errors": errors,
        }
    finally:
        if state.redis_client is not None:
            # Fail-open: the lock has _LOCK_TTL_SECONDS and will expire on its
            # own; failing to release it early is a missed optimization, not
            # a correctness issue, so it must not mask the return value above.
            try:
                current = await state.redis_client.get(lock_key)
                if isinstance(current, bytes):
                    current = current.decode("utf-8")
                if current == lock_token:
                    await state.redis_client.delete(lock_key)
            except Exception as exc:
                logger.debug("Response-commit lock release failed (fail-open, TTL will expire it): %s", exc)


async def response_commit_node(state_: Mapping[str, Any]) -> dict[str, Any]:
    """LangGraph node: only a passed final gate may enter this function."""
    request_id = str(state_.get("response_id") or "")
    if state_.get("quality_gate_status") != "passed":
        return {
            "response_commit_status": "blocked",
            "response_commit_errors": ["response_commit_without_quality_pass"],
        }
    payload = build_response_commit_payload(state_)
    result = await commit_response_payload(payload)
    required_precision = [
        item for item in state_.get("required_precision_intents") or []
        if isinstance(item, Mapping)
    ]
    if required_precision:
        from services.precision_telemetry import record_precision_event
        raw_status = str(result.get("status") or "failed")
        record_precision_event(
            "commit",
            raw_status if raw_status in {"complete", "reused", "skipped", "partial", "blocked", "busy"} else "failed",
            tool=(
                str(required_precision[0].get("tool") or "")
                if len(required_precision) == 1 else "multi"
            ),
            mode=str(state_.get("precision_contract_mode") or "enforce"),
        )
    try:
        from services.tracking import _record_stage

        await _record_stage(
            request_id,
            "response_commit",
            str(result.get("status") or "unknown"),
            ",".join(result.get("errors") or [])[:500],
        )
    except Exception:
        pass
    return {
        "response_commit_status": str(result.get("status") or "unknown"),
        "response_commit_key": str(result.get("commit_key") or ""),
        "response_commit_sinks": result.get("sinks") or {},
        "response_commit_errors": result.get("errors") or [],
    }
