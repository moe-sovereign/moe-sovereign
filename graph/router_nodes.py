"""graph/router_nodes.py — gatekeeper nodes (cache, semantic/fuzzy router, prototype seeding)."""

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx

import state
from config import (
    MODES, _MODEL_ID_TO_MODE, EXPERTS, EXPERT_TIMEOUT, JUDGE_TIMEOUT,
    PLANNER_TIMEOUT, MAX_EXPERT_OUTPUT_CHARS, JUDGE_MODEL, GUARD_MODEL,
    HISTORY_MAX_TURNS, HISTORY_MAX_CHARS,
    CACHE_HIT_THRESHOLD, SOFT_CACHE_THRESHOLD, SOFT_CACHE_MAX_EXAMPLES,
    KNOWLEDGE_BYPASS_ENABLED, KNOWLEDGE_BYPASS_THRESHOLD,
    KNOWLEDGE_BYPASS_MIN_CONF, KNOWLEDGE_BYPASS_TTL_DAYS,
    ROUTE_THRESHOLD, ROUTE_GAP, CACHE_MIN_RESPONSE_LEN,
    EXPERT_TIER_BOUNDARY_B, EXPERT_MIN_SCORE, EXPERT_MIN_DATAPOINTS,
    BENCHMARK_SHADOW_TEMPLATE, BENCHMARK_SHADOW_RATE,
    MCP_URL, GRAPH_VIA_MCP, MAX_GRAPH_CONTEXT_CHARS,
    LITELLM_URL, _SEARXNG_URL, _WEB_SEARCH_FALLBACK_DDG,
    _FUZZY_VECTOR_THRESHOLD, _FUZZY_GRAPH_THRESHOLD,
    _GRAPH_COMPRESS_THRESHOLD_FACTOR, _GRAPH_COMPRESS_LLM_MODEL, _GRAPH_COMPRESS_LLM_TIMEOUT,
    CORRECTION_MEMORY_ENABLED, THOMPSON_SAMPLING_ENABLED,
    JUDGE_REFINE_MAX_ROUNDS, JUDGE_REFINE_MIN_IMPROVEMENT,
    _CUSTOM_EXPERT_PROMPTS, PLANNER_MAX_TASKS, PLANNER_RETRIES,
    KAFKA_TOPIC_INGEST, NEO4J_URI, NEO4J_USER, NEO4J_PASS,
    _FALLBACK_ENABLED, PRECISION_DIRECT_RESPONSE_ENABLED,
    PRECISION_CONTRACT_MODE,
)
from metrics import (
    PROM_EXPERT_CALLS, PROM_CONFIDENCE, PROM_CACHE_HITS, PROM_CACHE_MISSES,
    PROM_SELF_EVAL, PROM_COMPLEXITY, PROM_ACTIVE_REQUESTS,
    PROM_TOOL_CALL_DURATION, PROM_TOOL_TIMEOUTS, PROM_TOOL_FORMAT_ERRORS,
    PROM_TOOL_CALL_SUCCESS, PROM_SEMANTIC_MEMORY_STORED, PROM_SEMANTIC_MEMORY_HITS,
    PROM_CORRECTIONS_INJECTED, PROM_CORRECTIONS_STORED,
    PROM_JUDGE_REFINED, PROM_EXPERT_FAILURES, PROM_SYNTHESIS_CREATED,
    PROM_HISTORY_COMPRESSED, PROM_HISTORY_UNLIMITED, PROM_KNOWLEDGE_BYPASS,
    PROM_ROUTING_BANDIT, PROM_CACHE_DISTANCE,
)
from services.inference import (
    _select_node, _invoke_llm_with_fallback, _invoke_judge_with_retry,
    _get_judge_llm, _get_planner_llm, _get_expert_score, _record_expert_outcome,
    assign_gpu, _ollama_unload, _refine_expert_response,
    _estimate_model_vram_gb, _mark_endpoint_degraded, _endpoint_is_degraded,
    ainvoke_guard_decision,
)
from services.routing import (
    _resolve_user_experts, _resolve_template_prompts, _server_info, _is_endpoint_error,
)
from services.kafka import _kafka_publish
from services.tracking import _increment_user_budget, _record_stage
from services.llm_instances import judge_llm, planner_llm, ingest_llm, search
from services.helpers import (
    _log_tool_eval,
    _update_rate_limit_headers, _check_rate_limit_exhausted,
    _conf_format_for_mode, _get_expert_prompt,
    _truncate_history, _apply_semantic_memory,
    _web_search_with_citations,
    _store_response_metadata, _self_evaluate, _neo4j_terms_exist,
    _report,
    _shadow_request, _shadow_lock,
    _entry_is_fresh,
)
from services.templates import _read_expert_templates, _read_cc_profiles
from services.skills import _build_skill_catalog
from prompts import (
    SYNTHESIS_PERSISTENCE_INSTRUCTION,
    PROVENANCE_INSTRUCTION,
    DEFAULT_PLANNER_ROLE,
)
from prompts import _ROUTE_PROTOTYPES, _RESEARCH_DETECT
from parsing import (
    _oai_content_to_str, _anthropic_content_to_text,
    _extract_images, _extract_oai_images,
    _anthropic_to_openai_messages, _anthropic_tools_to_openai,
)

logger = logging.getLogger("MOE-SOVEREIGN")

# AgentState import — defined in pipeline/state.py
from pipeline.state import AgentState
from services.pipeline.contracts import (
    build_direct_precision_plan,
    build_precision_preflight,
    apply_precision_contract_mode,
)


async def _seed_task_type_prototypes() -> None:
    """
    Fills the ChromaDB collection 'task_type_prototypes' with prototypical queries.
    Idempotent — already-present IDs are skipped.
    Called once at startup.
    """
    try:
        # Use upsert (add-or-update) directly — eliminates the TOCTOU race between
        # the existence check and the subsequent add when multiple instances start up.
        docs, ids, metas = [], [], []
        for category, queries in _ROUTE_PROTOTYPES.items():
            for i, query in enumerate(queries):
                docs.append(query)
                ids.append(f"proto_{category}_{i}")
                metas.append({"category": category})
        if docs:
            await asyncio.to_thread(state.route_collection.upsert, documents=docs, ids=ids, metadatas=metas)
            logger.info(f"🧭 Semantic Router: {len(docs)} prototypes upserted in ChromaDB")
    except Exception as e:
        logger.warning(f"⚠️ Semantic Router seeding failed: {e}")


# --- NODES ---


async def guard_node(state_: AgentState):
    """Llama Guard pre-filter — runs first, before cache, so even cache entries
    created before this node existed are not implicitly trusted as safe.

    Fail-open on any error (see ainvoke_guard_llm docstring) — a misconfigured or
    unreachable guard model must never block production traffic, only narrow it
    once demonstrably working.
    """
    logger.debug("--- [NODE] GUARD ---")
    _guard_model = state_.get("guardrail_model_override") or GUARD_MODEL
    if not _guard_model:
        return {"guard_blocked": False}  # guard not configured for this template/deployment

    await _record_stage(state_.get("response_id", ""), "guard", "started")
    _decision = await ainvoke_guard_decision(
        state_["input"],
        guard_model=_guard_model,
        guard_url=state_.get("guardrail_url_override") or "",
        guard_token=state_.get("guardrail_token_override") or "",
        policy_context=state_.get("guardrail_prompt") or "",
        session_id=state_.get("session_id", ""),
        request_id=state_.get("response_id", ""),
        deadline_state=state_,
    )
    if not _decision.is_unsafe:
        if _decision.status.startswith("fail_open"):
            await _record_stage(
                state_.get("response_id", ""),
                "guard",
                "fail_open",
                detail=_decision.status,
            )
            return {
                "guard_blocked": False,
                "guard_status": _decision.status,
            }
        await _record_stage(state_.get("response_id", ""), "guard", "passed")
        return {
            "guard_blocked": False,
            "guard_status": _decision.status,
        }

    logger.info(f"🛡️ Guard blocked request (category={_decision.category})")
    await _report(f"🛡️ Request blocked by safety filter (category {_decision.category})")
    await _record_stage(
        state_.get("response_id", ""),
        "guard",
        "blocked",
        detail=_decision.category,
    )
    return {
        "guard_blocked": True,
        "guard_reason": _decision.category,
        "guard_status": _decision.status,
        "guard_response": (
            "Diese Anfrage wurde von einem automatisierten Sicherheitsfilter als "
            "problematisch eingestuft und kann nicht bearbeitet werden. Bitte "
            "formuliere deine Anfrage anders."
        ),
    }


async def precision_preflight_node(state_: AgentState):
    """Freeze mandatory deterministic contracts before any response cache."""
    detected_preflight = build_precision_preflight(
        state_.get("input", ""), state.MCP_TOOL_SCHEMAS,
    )
    preflight = apply_precision_contract_mode(
        detected_preflight, PRECISION_CONTRACT_MODE,
    )
    required = preflight["required_precision_intents"]
    from services.precision_telemetry import record_precision_event
    for item in required:
        record_precision_event(
            "intent", "detected", tool=str(item.get("tool") or ""),
            mode=PRECISION_CONTRACT_MODE,
        )
    for item in preflight["precision_shadow_intents"]:
        record_precision_event(
            "intent", "shadow", tool=str(item.get("tool") or ""),
            mode=PRECISION_CONTRACT_MODE,
        )
    direct_plan = (
        build_direct_precision_plan(state_.get("input", ""))
        if PRECISION_DIRECT_RESPONSE_ENABLED and required
        else []
    )
    if direct_plan and {
        str(task.get("mcp_tool") or "") for task in direct_plan
    } != {
        str(item.get("tool") or "") for item in required
    }:
        direct_plan = []
    direct = bool(direct_plan)
    if required:
        record_precision_event(
            "route", "direct" if direct else "mixed",
            tool=str(required[0].get("tool") or "") if len(required) == 1 else "multi",
            mode=PRECISION_CONTRACT_MODE,
        )
    await _record_stage(
        state_.get("response_id", ""),
        "precision_preflight",
        "required" if required else "none",
        ",".join(str(item.get("tool") or "") for item in required),
    )
    if direct:
        await _record_stage(
            state_.get("response_id", ""),
            "precision_direct",
            "selected",
            str(len(direct_plan)),
        )
    return {
        **preflight,
        "precision_direct": direct,
        "precision_cache_bypassed": direct,
        **({"plan": direct_plan} if direct else {}),
    }


async def cache_lookup_node(state_: AgentState):
    logger.debug("--- [NODE] CACHE LOOKUP ---")
    # Security in depth: recompute when this node is invoked directly or a
    # future topology change omits the dedicated preflight node.  An explicit
    # precision request must never trust a legacy free-text response cache.
    detected_preflight = apply_precision_contract_mode(
        build_precision_preflight(
            state_.get("input", ""), state.MCP_TOOL_SCHEMAS,
        ),
        PRECISION_CONTRACT_MODE,
    )
    detected_required = detected_preflight["required_precision_intents"]
    existing_required = state_.get("required_precision_intents") or []
    required_precision = detected_required or existing_required
    # Never replace a snapshot created by the dedicated preceding node.  The
    # fallback is only for direct invocation/topology regressions; catalog
    # changes between nodes must be detected later as drift.
    preflight_update = (
        detected_preflight
        if "required_precision_intents" not in state_
        else {}
    )
    if required_precision:
        logger.info(
            "Precision response cache bypassed for mandatory tools: %s",
            ", ".join(str(item.get("tool") or "") for item in required_precision),
        )
        await _record_stage(
            state_.get("response_id", ""),
            "cache",
            "bypassed",
            "required_precision_intent",
        )
        from services.precision_telemetry import record_precision_event
        record_precision_event(
            "cache", "bypassed",
            tool=str(required_precision[0].get("tool") or "") if len(required_precision) == 1 else "multi",
            mode=PRECISION_CONTRACT_MODE,
        )
        return {
            **preflight_update,
            "cached_facts": "",
            "cache_hit": False,
            "soft_cache_examples": "",
            "precision_cache_bypassed": True,
        }
    # Template toggle: skip cache if disabled
    if not state_.get("enable_cache", True):
        logger.info("Cache disabled by template toggle")
        return {"cached_facts": "", "cache_hit": False}
    # Non-default modes bypass the cache — format mismatch would deliver wrong answers
    if state_.get("mode", "default") != "default":
        return {"cached_facts": "", "cache_hit": False}
    # Explicit request contract: no_cache bypasses both L0 Valkey and L1
    # ChromaDB, including soft-example lookup. It is used for benchmarks and
    # freshness-sensitive requests, so even a discarded semantic query is
    # unwanted work and can perturb latency measurements.
    if state_.get("no_cache"):
        logger.info("Cache fully bypassed by request no_cache=true")
        await _record_stage(
            state_.get("response_id", ""),
            "cache",
            "bypassed",
            "no_cache",
        )
        return {
            "cached_facts": "",
            "cache_hit": False,
            "soft_cache_examples": "",
        }
    await _report("🔍 Cache-Lookup...")
    await _record_stage(state_.get("response_id", ""), "cache", "started")
    # Normalized query for similarity search — pipeline input stays unchanged
    _cache_query = re.sub(r'\s+', ' ', state_["input"].lower().strip().rstrip('?!.,;'))

    # L0: Exact query hash cache (Valkey, instant, before ChromaDB)
    if state.redis_client:
        try:
            import hashlib as _hl
            _q_hash = _hl.sha256(_cache_query.encode()).hexdigest()[:24]
            _l0_key = f"moe:qcache:{_q_hash}"
            _l0_hit = await state.redis_client.get(_l0_key)
            if _l0_hit:
                _l0_text = _l0_hit if isinstance(_l0_hit, str) else _l0_hit.decode()
                if len(_l0_text) > 50:
                    PROM_CACHE_HITS.inc()
                    logger.info(f"⚡ L0 query-hash cache hit ({len(_l0_text)} chars)")
                    await _report(f"⚡ L0 cache hit — instant response")
                    await _record_stage(state_.get("response_id", ""), "cache", "hit_l0")
                    return {"cached_facts": _l0_text, "cache_hit": True}
        except Exception as _l0e:
            logger.debug(f"L0 cache check failed: {_l0e}")

    # L1: Semantic similarity cache (ChromaDB)
    res = await asyncio.to_thread(state.cache_collection.query, query_texts=[_cache_query], n_results=3)
    cached = ""
    hit = False
    # Telemetry: record the nearest-neighbour distance for every lookup (hit or
    # miss) so the static thresholds can be calibrated from the real distribution.
    _nearest_dist = None
    if res['documents'] and res['documents'][0]:
        _all_dists = res.get('distances', [[]])[0]
        if _all_dists:
            _nearest_dist = float(_all_dists[0])
            PROM_CACHE_DISTANCE.observe(_nearest_dist)
    if res['documents'] and res['documents'][0]:
        docs  = res['documents'][0]
        dists = res.get('distances', [[1.0] * len(docs)])[0]
        metas = res.get('metadatas', [[{}]  * len(docs)])[0]
        for doc, dist, meta in zip(docs, dists, metas):
            if meta.get("flagged"):
                continue  # skip negatively-rated entry
            cached = doc
            if dist < CACHE_HIT_THRESHOLD:
                hit = True
                PROM_CACHE_HITS.inc()
                logger.info(f"✅ Cache hit (distance={dist:.3f}) — skipping pipeline")
                await _report(f"✅ Cache hit (similarity {1-dist:.2f}) — pipeline skipped")
                await _record_stage(state_.get("response_id", ""), "cache", "hit_l1")
            elif (
                KNOWLEDGE_BYPASS_ENABLED
                and dist < KNOWLEDGE_BYPASS_THRESHOLD
                and float(meta.get("confidence", 0.0) or 0.0) >= KNOWLEDGE_BYPASS_MIN_CONF
                and _entry_is_fresh(meta.get("ts", ""), KNOWLEDGE_BYPASS_TTL_DAYS)
            ):
                # Conservative knowledge-bypass: similar (not exact) query, but the
                # prior answer was high-confidence and is still fresh → skip the LLM.
                hit = True
                PROM_KNOWLEDGE_BYPASS.inc()
                logger.info(
                    f"🧠 Knowledge bypass (distance={dist:.3f}, conf={meta.get('confidence')}) "
                    f"— skipping pipeline"
                )
                await _report(
                    f"🧠 Knowledge bypass (similarity {1-dist:.2f}, "
                    f"confidence {meta.get('confidence')}) — pipeline skipped"
                )
                await _record_stage(state_.get("response_id", ""), "cache", "hit_bypass")
            break
    if not hit:
        PROM_CACHE_MISSES.inc()
        if _nearest_dist is not None:
            logger.info(
                f"📭 Cache miss — nearest distance={_nearest_dist:.3f} "
                f"(hit<{CACHE_HIT_THRESHOLD}, bypass<{KNOWLEDGE_BYPASS_THRESHOLD})"
            )
        await _report("📭 No cache hit — starting full pipeline")
        await _record_stage(state_.get("response_id", ""), "cache", "miss")
    # Soft hits (0.15 < dist < 0.50): collect as few-shot examples
    soft_examples = []
    if res['documents'] and res['documents'][0]:
        for doc, dist, meta in zip(
            res['documents'][0], res.get('distances', [[]])[0], res.get('metadatas', [[]])[0]
        ):
            if meta.get("flagged"):
                continue
            if CACHE_HIT_THRESHOLD < dist < SOFT_CACHE_THRESHOLD:
                q = meta.get("input", "")[:120]
                a = doc[:400]
                soft_examples.append(f"Question: {q}\nAnswer: {a}")
            if len(soft_examples) >= SOFT_CACHE_MAX_EXAMPLES:
                break
    soft_ctx = "\n\n---\n\n".join(soft_examples) if soft_examples else ""
    if soft_ctx:
        await _report(f"💡 {len(soft_examples)} similar previous answer(s) loaded as context")
    return {"cached_facts": cached, "cache_hit": hit, "soft_cache_examples": soft_ctx}


async def semantic_router_node(state_: AgentState):
    """
    Semantic pre-router — runs after cache_lookup_node, before planner_node.
    Compares the user query semantically against prototypical task queries per category.
    If a clear match is found (dist < ROUTE_THRESHOLD, gap > ROUTE_GAP),
    'direct_expert' is set and a synthetic single-task plan is created.
    planner_node then skips the LLM call and uses this plan directly.
    On ambiguity or cache hit: no intervention.
    """
    # Don't route if cache hit (will be skipped anyway) or non-default mode
    if state_.get("cache_hit") or state_.get("mode", "default") != "default":
        return {"direct_expert": ""}

    _query = re.sub(r'\s+', ' ', state_["input"].lower().strip().rstrip('?!.,;'))
    try:
        res = await asyncio.to_thread(state.route_collection.query, query_texts=[_query], n_results=2)
        docs  = res.get("documents",  [[]])[0]
        dists = res.get("distances",  [[1.0, 1.0]])[0]
        metas = res.get("metadatas",  [[{}, {}]])[0]

        if len(dists) < 2 or not docs:
            return {"direct_expert": ""}

        top_dist  = dists[0]
        gap       = dists[1] - dists[0]
        category  = metas[0].get("category", "")

        if top_dist < ROUTE_THRESHOLD and gap > ROUTE_GAP and category:
            synthetic_plan = [{"task": state_["input"], "category": category}]
            logger.info(
                f"🧭 Semantic Router: direct routing → '{category}' "
                f"(dist={top_dist:.3f}, gap={gap:.3f})"
            )
            await _report(
                f"🧭 Semantic Router: Fast-Path → expert '{category}' "
                f"(similarity {1-top_dist:.2f}, uniqueness {gap:.2f})"
            )
            await _record_stage(state_.get("response_id", ""), "semantic_router", "matched", category)
            return {"direct_expert": category, "plan": synthetic_plan}
    except Exception as e:
        logger.debug(f"Semantic Router error: {e}")

    return {"direct_expert": ""}


async def fuzzy_router_node(state_: AgentState):
    """Replace heuristic binary routing flags with fuzzy t-norm conjunction scores.

    The planner currently sets skip_research and enable_graphrag as binary flags
    derived from complexity heuristics. This node replaces that decision with a
    quantitative approach: independent confidence scores are computed from the
    plan content and combined via the Godel t-norm (minimum) for a conservative
    gate — both signals must be strong to activate a retrieval node.

    Mathematical foundation:
        Fuzzy logics as the most general framework — de Vries (2007),
        arXiv:0707.2161. T-norm conjunction over [0,1]-valued truth degrees
        replaces Boolean routing. Godel t-norm T_G(a,b) = min(a,b) (Godel
        1932, discussed in de Vries 2007, §4); Lukasiewicz t-norm
        T_L(a,b) = max(0, a+b-1) (Lukasiewicz 1920, de Vries 2007, §4).

    Thresholds (configurable via env):
        FUZZY_VECTOR_THRESHOLD (default 0.30): below -> skip_research=True
        FUZZY_GRAPH_THRESHOLD  (default 0.35): below -> enable_graphrag=False
    """
    if state_.get("cache_hit"):
        return {}

    from pipeline.logic_types import goedel_tnorm, lukasiewicz_tnorm
    from parsing import _compute_routing_confidence

    plan             = state_.get("plan") or []
    complexity_level = state_.get("complexity_level") or "moderate"
    enable_graphrag  = state_.get("enable_graphrag", True)
    skip_research    = state_.get("skip_research", False)

    vector_conf, graph_conf = _compute_routing_confidence(
        plan, complexity_level, enable_graphrag
    )

    # Complexity as a second signal: map to [0,1] for t-norm input
    _complexity_weight = {"trivial": 0.1, "memory_recall": 0.0, "moderate": 0.5, "complex": 1.0}
    complexity_score   = _complexity_weight.get(complexity_level, 0.5)

    # Select the documented fuzzy conjunction. Gödel remains the conservative
    # production default; Łukasiewicz can be selected explicitly for deployments
    # that want partial evidence to combine.
    _tnorm_method = os.getenv("FUZZY_TNORM", "goedel").strip().lower()
    _tnorm = lukasiewicz_tnorm if _tnorm_method in {
        "lukasiewicz", "łukasiewicz", "luk",
    } else goedel_tnorm
    _tnorm_method = "lukasiewicz" if _tnorm is lukasiewicz_tnorm else "goedel"
    tnorm_vector = _tnorm(vector_conf, complexity_score)
    tnorm_graph  = _tnorm(graph_conf, complexity_score)

    # Heuristic gate decisions (fuzzy thresholds). These are no longer the final
    # authority — they serve as the contextual bandit's cold-start fallback and,
    # via the t-norm bands below, as its context features.
    _heur_do_research  = tnorm_vector >= _FUZZY_VECTOR_THRESHOLD   # True = fetch web research
    _heur_enable_graph = tnorm_graph  >= _FUZZY_GRAPH_THRESHOLD    # True = query knowledge graph

    # Context buckets: complexity level + discretised t-norm band, per gate.
    from services.routing_bandit import decide as _rb_decide, band as _rb_band
    _research_ctx = f"{complexity_level}|v{_rb_band(tnorm_vector)}"
    _graph_ctx    = f"{complexity_level}|g{_rb_band(tnorm_graph)}"

    _query_embedding = state_.get("query_embedding")
    _do_research,  _src_r = await _rb_decide("research", _research_ctx, _heur_do_research, query_embedding=_query_embedding)
    _enable_graph, _src_g = await _rb_decide("graphrag", _graph_ctx,    _heur_enable_graph, query_embedding=_query_embedding)

    new_skip_research   = not _do_research
    new_enable_graphrag = _enable_graph

    PROM_ROUTING_BANDIT.labels(gate="research", action="fetch" if _do_research  else "skip", source=_src_r).inc()
    PROM_ROUTING_BANDIT.labels(gate="graphrag", action="on"    if _enable_graph else "off",  source=_src_g).inc()

    scores = {
        "vector_confidence": vector_conf,
        "graph_confidence":  graph_conf,
        "tnorm_vector":      round(tnorm_vector, 3),
        "tnorm_graph":       round(tnorm_graph, 3),
        "method":            _tnorm_method,
        "vector_threshold":  _FUZZY_VECTOR_THRESHOLD,
        "graph_threshold":   _FUZZY_GRAPH_THRESHOLD,
        "research_source":   _src_r,
        "graph_source":      _src_g,
    }

    logger.info(
        f"🔀 Fuzzy Router: vector={vector_conf:.2f}→T={tnorm_vector:.2f} "
        f"({'fetch' if _do_research else 'skip'}/{_src_r}) | "
        f"graph={graph_conf:.2f}→T={tnorm_graph:.2f} "
        f"({'on' if new_enable_graphrag else 'off'}/{_src_g})"
    )
    await _report(
        f"🔀 Router ({_src_r}/{_src_g}): "
        f"web={'✓' if not new_skip_research else '✗'} (score={tnorm_vector:.2f}) | "
        f"graph={'✓' if new_enable_graphrag else '✗'} (score={tnorm_graph:.2f})"
    )
    await _record_stage(state_.get("response_id", ""), "fuzzy_router", "done")

    return {
        "vector_confidence":    vector_conf,
        "graph_confidence":     graph_conf,
        "fuzzy_routing_scores": scores,
        "skip_research":        new_skip_research,
        "enable_graphrag":      new_enable_graphrag,
        "routing_bandit_context": f"{_research_ctx}|||{_graph_ctx}",
    }


# --- GRAPH ROUTER ---
def _route_cache(state_: AgentState) -> str:
    """On cache hit go directly to merger — entire pipeline is skipped."""
    return "merger" if state_.get("cache_hit") else "semantic_router"


def _route_precision_preflight(state_: AgentState) -> str:
    """Select the model-free path only for a completely covered request."""
    return "precision_mcp" if state_.get("precision_direct") else "cache"


def _route_guard(state_: AgentState) -> str:
    """On guard block go directly to merger (same short-circuit target as cache_hit),
    which returns guard_response as final_response — see merger_node/critic_node."""
    return "merger" if state_.get("guard_blocked") else "precision_preflight"
