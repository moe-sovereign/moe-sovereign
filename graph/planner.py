"""graph/planner.py — planner node, plan sanitization, dependency-level helpers."""

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
    MAX_EXPERT_OUTPUT_CHARS, JUDGE_MODEL,
    HISTORY_MAX_TURNS, HISTORY_MAX_CHARS, PLANNER_NUM_CTX, PLANNER_MODEL,
    EXPERT_CHARS_PER_TOKEN,
    CACHE_HIT_THRESHOLD, SOFT_CACHE_THRESHOLD, SOFT_CACHE_MAX_EXAMPLES,
    ROUTE_THRESHOLD, ROUTE_GAP, CACHE_MIN_RESPONSE_LEN,
    EXPERT_TIER_BOUNDARY_B, EXPERT_MIN_SCORE, EXPERT_MIN_DATAPOINTS,
    BENCHMARK_SHADOW_TEMPLATE, BENCHMARK_SHADOW_RATE,
    MCP_URL, GRAPH_VIA_MCP, MAX_GRAPH_CONTEXT_CHARS,
    LITELLM_URL, _SEARXNG_URL, _WEB_SEARCH_FALLBACK_DDG,
    _FUZZY_VECTOR_THRESHOLD, _FUZZY_GRAPH_THRESHOLD,
    _GRAPH_COMPRESS_THRESHOLD_FACTOR, _GRAPH_COMPRESS_LLM_MODEL, _GRAPH_COMPRESS_LLM_TIMEOUT,
    CORRECTION_MEMORY_ENABLED, THOMPSON_SAMPLING_ENABLED,
    JUDGE_REFINE_MAX_ROUNDS, JUDGE_REFINE_MIN_IMPROVEMENT,
    TRIVIAL_FAST_PATH_ENABLED, TRIVIAL_FAST_PATH_CATEGORY,
    _CUSTOM_EXPERT_PROMPTS, PLANNER_MAX_TASKS, PLANNER_RETRIES,
    KAFKA_TOPIC_INGEST, NEO4J_URI, NEO4J_USER, NEO4J_PASS,
    _FALLBACK_ENABLED,
)
from metrics import (
    PROM_EXPERT_CALLS, PROM_CONFIDENCE, PROM_CACHE_HITS, PROM_CACHE_MISSES,
    PROM_SELF_EVAL, PROM_COMPLEXITY, PROM_ACTIVE_REQUESTS,
    PROM_TOOL_CALL_DURATION, PROM_TOOL_TIMEOUTS, PROM_TOOL_FORMAT_ERRORS,
    PROM_TOOL_CALL_SUCCESS, PROM_SEMANTIC_MEMORY_STORED, PROM_SEMANTIC_MEMORY_HITS,
    PROM_CORRECTIONS_INJECTED, PROM_CORRECTIONS_STORED,
    PROM_JUDGE_REFINED, PROM_EXPERT_FAILURES, PROM_SYNTHESIS_CREATED,
    PROM_HISTORY_COMPRESSED, PROM_HISTORY_UNLIMITED,
)
from services.inference import (
    _select_node, _invoke_judge_with_retry,
    _invoke_planner_with_retry,
    _get_judge_llm, _get_expert_score, _record_expert_outcome,
    assign_gpu, _refine_expert_response,
    _mark_endpoint_degraded, _endpoint_is_degraded,
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
)
from services.templates import _read_expert_templates, _read_cc_profiles
from services.skills import _build_skill_catalog
from services.trivial_fast_path import (
    MATH_SIGNAL_PATTERN as _MATH_TEMP_PATTERN,
    is_trivial_fast_path_eligible as _trivial_fast_path_eligible,
)
from prompts import (
    SYNTHESIS_PERSISTENCE_INSTRUCTION,
    PROVENANCE_INSTRUCTION,
    DEFAULT_PLANNER_ROLE,
)
from parsing import (
    _oai_content_to_str, _anthropic_content_to_text,
    _extract_images, _extract_oai_images,
    _anthropic_to_openai_messages, _anthropic_tools_to_openai,
)

logger = logging.getLogger("MOE-SOVEREIGN")

# AgentState import — defined in pipeline/state.py
from pipeline.state import AgentState


def _planner_ctx_budget(state_num_ctx: int = 0) -> dict:
    """Derive agentic context-block char budgets from the planner model's context window.

    Priority: state_num_ctx (per-template) > PLANNER_NUM_CTX (global env) > static model
    table. All caps scale proportionally so a larger planner model automatically gets
    more room for working memory and gap context.

    Uses EXPERT_CHARS_PER_TOKEN (env: EXPERT_CHARS_PER_TOKEN, default 3) for the
    chars/token estimate and reserves 40% of the window for the static instruction
    prompt + user query.
    """
    from context_budget import get_model_context_window as _static_ctx
    ctx_tokens = state_num_ctx or PLANNER_NUM_CTX or _static_ctx(PLANNER_MODEL) or 4096
    # Characters available after reserving 40% for instruction prompt + user query
    available_chars = int(ctx_tokens * EXPERT_CHARS_PER_TOKEN * 0.60)
    return {
        # working_memory JSON — largest share, structured facts are most valuable
        "working_memory":    min(available_chars // 2, 24_000),
        # prose fallback when working_memory is empty
        "prev_findings":     min(available_chars // 3, 16_000),
        # gap description — concise by nature, small cap is fine
        "gap":               min(available_chars // 8,  4_000),
        # per-query line in the search history block
        "query_line":        200,
        # how many past queries to show
        "max_queries":       20,
        # how many past failures to show
        "max_failures":      10,
        # how many discovered domains to show
        "max_domains":       12,
    }


def _sanitize_plan(raw: list, fallback_input: str,
                   user_expert_cats: set | None = None) -> list:
    """
    Ensures all plan entries are valid task dicts.
    Strings, empty dicts or dicts without 'task' key are discarded.
    Returns at least one fallback task.

    user_expert_cats: categories defined in the active user template.
    When non-empty, routing is restricted to those categories so global
    expert fallbacks (with different models) cannot be triggered.
    """
    NON_EXPERT_CATEGORIES = {"precision_tools", "research"}
    _special = {"agentic_coder", "memory_recall", "dynamic"}
    if user_expert_cats:
        # Template active: only allow template categories + non-expert types.
        # Global EXPERTS categories are excluded to prevent silent model substitution.
        valid_cats = user_expert_cats | NON_EXPERT_CATEGORIES | _special
    else:
        valid_cats = set(EXPERTS.keys()) | NON_EXPERT_CATEGORIES | _special
    result = []
    for item in raw:
        if not isinstance(item, dict):
            logger.warning(f"⚠️ Planner: invalid task entry skipped: {item!r}")
            continue
        task_text = (item.get("task") or item.get("task_description") or item.get("instruction") or item.get("description") or "").strip()
        if not task_text:
            continue
        item["task"] = task_text
        cat = item.get("category") or item.get("task_type") or item.get("type") or "general"
        if cat not in valid_cats:
            logger.warning(f"⚠️ Planner: unknown category '{cat}' → 'general'")
            cat = "general"
        item["category"] = cat
        result.append(item)
    if not result:
        logger.warning("⚠️ Planner: no valid task after sanitization — fallback")
        return [{"task": fallback_input, "category": "general"}]
    return result


def _compact_planner_role(role: str, max_chars: int) -> str:
    """Bound user/template planner policy while preserving its start and end."""
    text = (role or "").strip()
    limit = max(1_000, int(max_chars))
    if len(text) <= limit:
        return text
    head_len = int(limit * 0.7)
    tail_len = limit - head_len
    return (
        text[:head_len].rstrip()
        + "\n\n[... planner role compacted to runtime budget ...]\n\n"
        + text[-tail_len:].lstrip()
    )


_CREATIVE_TEMP_PATTERN = re.compile(
    r'\b(entwirf|erstelle?|schreibe?|gestalte?|verfasse?|dichte?|erdichte?|'
    r'create|write|generate|design|compose|brainstorm|ideen|kreativ|story|poem|'
    r'imagine|vorstellen|erfinde?|invent)\b',
    re.I,
)


def _detect_query_temperature(query: str) -> float:
    """Infer optimal sampling temperature from query type.

    Math/factual queries need deterministic output (low temp).
    Creative queries benefit from variability (high temp).
    """
    if _MATH_TEMP_PATTERN.search(query):
        return 0.05
    if _CREATIVE_TEMP_PATTERN.search(query):
        return 0.70
    return 0.20  # factual / neutral default


def _build_agent_mode_plan(
    state_: AgentState,
    tool_schemas: dict,
) -> tuple[list[dict], str]:
    """Build the no-LLM agent plan without dropping precision contracts."""
    from services.pipeline.contracts import (
        _numbered_query_items,
        recover_explicit_supported_plan,
    )

    required = [
        item
        for item in (state_.get("required_precision_intents") or [])
        if isinstance(item, dict)
    ]
    if not required:
        return [
            {"task": state_["input"], "category": "code_reviewer"},
            {"task": state_["input"], "category": "technical_support"},
        ], "default_code_pair"

    recovered, _ = recover_explicit_supported_plan(
        state_.get("input", ""),
        tool_schemas,
        max_tasks=PLANNER_MAX_TASKS,
    )
    if recovered:
        return recovered, "explicit_precision_recovery"

    # Unknown non-precision work retains one bounded agent expert, while every
    # frozen precision intent is still materialized exactly. This fallback may
    # be synthesized only through the normal fail-closed binding path; the
    # narrow hybrid composer additionally proves item-level task isolation.
    numbered_items = _numbered_query_items(state_.get("input", ""))
    precision_tasks: list[dict] = []
    for intent in required:
        source_item = intent.get("source_item")
        instruction = state_.get("input", "")
        if (
            numbered_items
            and isinstance(source_item, int)
            and 0 <= source_item < len(numbered_items)
        ):
            instruction = numbered_items[source_item]
        precision_tasks.append({
            "task": instruction,
            "category": "precision_tools",
            "mcp_tool": intent.get("tool"),
            "mcp_args": dict(intent.get("args") or {}),
        })
    return precision_tasks + [
        {"task": state_["input"], "category": "code_reviewer"}
    ], "precision_preserving_fallback"


async def planner_node(state_: AgentState):
    # Lazy import to avoid circular: main imports from graph/nodes; this call happens at runtime.
    from main import _build_filtered_tool_desc
    from services.boundary_check import check_boundary as _check_boundary
    from services.pipeline.contracts import (
        PlannerContractError as _PlannerContractError,
        PlannerContractIssue as _PlannerContractIssue,
        assign_stable_task_ids as _assign_stable_task_ids,
        normalize_task_dependencies as _normalize_task_dependencies,
        canonical_tool_catalog_hash as _canonical_tool_catalog_hash,
        parse_plan as _parse_plan_contract,
        recover_explicit_supported_plan as _recover_explicit_supported_plan,
        repair_precision_task_contracts as _repair_precision_task_contracts,
        validate_plan_or_raise as _validate_plan_or_raise,
    )

    _plan_iteration = int(state_.get("agentic_iteration") or 0)

    def _request_tool_schemas() -> dict:
        """Use the preflight snapshot for required tools within this request."""
        schemas = dict(state.MCP_TOOL_SCHEMAS)
        required = state_.get("required_precision_intents") or []
        snapshots = state_.get("precision_contract_snapshot") or {}
        for intent in required:
            if not isinstance(intent, dict):
                continue
            tool = str(intent.get("tool") or "")
            frozen = snapshots.get(tool)
            if isinstance(frozen, dict):
                schemas[tool] = frozen
            elif tool:
                # Unavailable at preflight must remain unavailable for the
                # lifetime of the request even if a reload happens later.
                schemas.pop(tool, None)
        return schemas

    _handoff_tool_schemas = _request_tool_schemas()

    def _prepare_handoff_plan(tasks: list[dict]) -> tuple[list[dict], list[dict]]:
        """Apply every mandatory planner handoff contract on every plan path."""
        tasks, deterministic_repairs = _repair_precision_task_contracts(
            tasks,
            state_.get("input", ""),
            _handoff_tool_schemas,
        )
        if deterministic_repairs:
            logger.info(
                "Planner precision contract normalized: %s",
                json.dumps(deterministic_repairs, ensure_ascii=False),
            )
        prepared = _assign_stable_task_ids(tasks)
        prepared, _dep_repairs = _normalize_task_dependencies(prepared)
        if _dep_repairs:
            logger.info(
                "Planner depends_on normalized: %s",
                json.dumps(_dep_repairs, ensure_ascii=False),
            )
        _validate_plan_or_raise(
            prepared,
            _handoff_tool_schemas,
            max_tasks=PLANNER_MAX_TASKS,
            input_query=state_.get("input", ""),
        )
        request_id = state_.get("response_id", "")
        for index, task in enumerate(prepared):
            violations = _check_boundary(
                "planner_to_expert",
                task,
                request_id=request_id,
            )
            if violations:
                raise _PlannerContractError(
                    [
                        _PlannerContractIssue(
                            index,
                            "boundary_violation",
                            violation,
                        )
                        for violation in violations
                    ]
                )
        planned_events = [
            {
                "task_id": task["id"],
                "category": task["category"],
                "status": "planned",
                "executor": "planner",
                "iteration": _plan_iteration,
            }
            for task in prepared
        ]
        return prepared, planned_events

    _output_skill = ""  # Initialize early to prevent UnboundLocalError
    # Cache hit: no LLM call needed
    if state_.get("cache_hit"):
        logger.info("📋 Planner skipped (cache hit)")
        return {"plan": []}
    # Semantic pre-routing: direct expert path without LLM call
    if state_.get("direct_expert") and state_.get("plan"):
        logger.info(f"📋 Planner skipped (semantic router → '{state_['direct_expert']}')")
        from complexity_estimator import estimate_complexity
        from services.cynefin import classify_cynefin
        _direct_plan, _direct_events = _prepare_handoff_plan(
            list(state_["plan"])
        )
        _early_complexity = (
            state_.get("complexity_level")
            or estimate_complexity(state_["input"])
        )
        return {
            "plan": _direct_plan,
            "task_events": _direct_events,
            "complexity_level": _early_complexity,
            "cynefin_domain": classify_cynefin({
                **dict(state_),
                "complexity_level": _early_complexity,
                "plan": _direct_plan,
            }).value,
        }

    # Emit pending reports (e.g. skill resolution) from state
    for _pr in (state_.get("pending_reports") or []):
        await _report(_pr)

    # ── Agentic loop: read config from template state ───────────────────────
    _agentic_iteration  = state_.get("agentic_iteration") or 0
    _agentic_max_rounds = state_.get("max_agentic_rounds") or 0
    _is_agentic_replan  = _agentic_iteration > 0 and _agentic_max_rounds > 0

    # Planner result cache: same request → same plan (Valkey, TTL=30 min)
    # Skip cache entirely during agentic re-planning or when the caller requests no cache.
    # no_cache=True bypasses both L0 LLM cache and planner cache to ensure a fresh plan
    # is generated — important for benchmark runs that follow cache pre-warming.
    import hashlib as _hashlib
    _no_cache_flag  = state_.get("no_cache", False)
    # Include a short config fingerprint so the plan cache auto-invalidates when
    # the MCP tool set or planner prompt changes between deployments.
    _tool_contract_fp = _canonical_tool_catalog_hash(_handoff_tool_schemas)
    _cfg_fp = _hashlib.sha256(
        json.dumps(
            {
                "contract_version": 5,
                "tools": _tool_contract_fp,
                "planner_prompt": state_.get("planner_prompt") or "",
                "role_limit": os.getenv("PLANNER_ROLE_MAX_CHARS", "8000"),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()[:12]
    # Include chat_history presence in key: same query needs different plan
    # in conversation context (memory_recall) vs. standalone (research).
    _has_history = "h" if len(state_.get("chat_history") or []) >= 2 else "n"
    _plan_cache_key = f"moe:plan:{_cfg_fp}:{_has_history}:{_hashlib.sha256(state_['input'][:300].encode()).hexdigest()[:16]}"
    if state.redis_client is not None and not _is_agentic_replan and not _no_cache_flag:
        try:
            _cached_plan_raw = await state.redis_client.get(_plan_cache_key)
            if _cached_plan_raw:
                _cached_plan = json.loads(_cached_plan_raw)
                try:
                    _cached_plan, _cached_events = _prepare_handoff_plan(
                        _cached_plan
                    )
                except _PlannerContractError as _cache_contract_error:
                    logger.warning(
                        "Planner cache contract invalid — ignoring cached plan: %s",
                        _cache_contract_error,
                    )
                    _cached_plan = None
                if not _cached_plan:
                    raise ValueError("cached planner contract is invalid")
                from complexity_estimator import estimate_complexity
                from services.cynefin import classify_cynefin
                _cached_complexity = (
                    state_.get("complexity_level")
                    or estimate_complexity(state_["input"])
                )
                logger.info(f"📋 Planner cache hit (Valkey) — skipping LLM")
                await _report("📋 Planner: plan loaded from Valkey cache")
                await _record_stage(state_.get("response_id", ""), "planner", "cache_hit")
                return {
                    "plan": _cached_plan,
                    "task_events": _cached_events,
                    "complexity_level": _cached_complexity,
                    "cynefin_domain": classify_cynefin({
                        **dict(state_),
                        "complexity_level": _cached_complexity,
                        "plan": _cached_plan,
                    }).value,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                }
        except Exception as _pe:
            logger.debug(f"Planner cache read error: {_pe}")

    # Complexity estimation: determine routing hints before LLM planner call
    from complexity_estimator import estimate_complexity, complexity_routing_hint
    _complexity = state_.get("complexity_level") or estimate_complexity(state_["input"])
    # Day-2 upgrade: factual questions inside a multi-turn conversation are
    # almost always asking about something the user stated earlier — not
    # web-searchable facts. Upgrade trivial AND moderate to memory_recall
    # when substantive chat_history is present. Complex/research queries are
    # already routed differently by estimate_complexity before we reach here,
    # so upgrading moderate is safe for recall-heavy conversation patterns.
    _chat_hist = state_.get("chat_history") or []
    if _complexity in ("trivial", "moderate") and len(_chat_hist) >= 2:
        _prev_complexity = _complexity
        _complexity = "memory_recall"
        logger.info("🧠 Day-2 upgrade: %s→memory_recall (chat_history present)", _prev_complexity)
    _routing    = complexity_routing_hint(_complexity)

    # Multi-fact memory_recall: when the question asks for multiple facts
    # (contains conjunctions like "und X und Y" or multiple interrogatives),
    # allow 2 tasks so the planner can create separate recall tasks per fact.
    if _complexity == "memory_recall":
        _multi_fact = bool(re.search(
            r'\b(und|and|sowie|als auch|außerdem|additionally|also)\b',
            state_["input"], re.I,
        ))
        if _multi_fact and _routing.get("max_tasks", 1) < 2:
            _routing = dict(_routing)
            _routing["max_tasks"] = 2
            logger.info("🧠 Multi-fact memory_recall: max_tasks=2")
    PROM_COMPLEXITY.labels(level=_complexity).inc()
    logger.info(f"📊 Complexity: {_complexity} → {_routing}")
    # Map complexity to cost tier for OpEx tracking and expert-tier enforcement.
    # local_7b → trivial tasks: single T1 expert max, no research, no thinking node
    # mid_tier  → moderate tasks: standard MoE, no thinking node
    # full      → complex tasks: all capabilities active
    _cost_tier_map = {"trivial": "local_7b", "moderate": "mid_tier", "complex": "full"}
    _cost_tier = _cost_tier_map.get(_complexity, "mid_tier")
    logger.info(f"💰 Cost-Tier: {_cost_tier} (complexity={_complexity})")

    # Store routing hints in state for downstream nodes
    # Use explicit request temperature when set (e.g. GAIA benchmark temperature=0.0);
    # fall back to query-adaptive detection when None.
    _explicit_temp = state_.get("query_temperature")  # set by HTTP handler from request
    _query_temp    = _explicit_temp if _explicit_temp is not None else _detect_query_temperature(state_["input"])
    # memory_recall: T=0 for deterministic exact-value recall (prevents
    # stochastic drift where model picks old vs. new value unpredictably).
    if _complexity == "memory_recall" and _explicit_temp is None:
        _query_temp = 0.0
    logger.info(f"🌡️ Temperature: {_query_temp} ({'explicit' if _explicit_temp is not None else 'adaptive'})")
    # ── Cynefin classification (TASK-15) ─────────────────────────────────────
    def _classify_cynefin_for(plan_: list) -> str:
        """Classify with this invocation's new complexity and plan."""
        from services.cynefin import classify_cynefin
        return classify_cynefin({
            **dict(state_),
            "complexity_level": _complexity,
            "plan": plan_,
        }).value

    try:
        _cynefin_domain = _classify_cynefin_for(state_.get("plan") or [])
    except Exception as _ce:
        logger.debug("Cynefin classification failed: %s", _ce)
        _cynefin_domain = ""
    if _cynefin_domain:
        logger.info("🧩 Cynefin domain: %s", _cynefin_domain)

    _complexity_state_update = {
        "complexity_level":   _complexity,
        "skip_research":      _routing["skip_research"],
        "skip_graph":         _routing["skip_graph"],
        "skip_thinking":      _routing["skip_thinking"],
        "cost_tier":          _cost_tier,
        "force_tier1":        _routing.get("force_tier1", False),
        "query_temperature":  _query_temp,
        "cynefin_domain":     _cynefin_domain,
    }
    from services.request_snapshot import update_request_snapshot
    update_request_snapshot(
        state_.get("response_id", ""),
        complexity_level=_complexity,
        cynefin_domain=_cynefin_domain,
    )

    # ── Trivial fast-path ──────────────────────────────────────────────────────
    # Only conservative one-shot requests bypass the planner. Exact operations,
    # current/research/legal/file work and conversation context retain it.  The
    # complete routing-state update must be returned as well; otherwise the
    # downstream graph would still execute GraphRAG/thinking despite this gate.
    if (
        TRIVIAL_FAST_PATH_ENABLED
        and _trivial_fast_path_eligible(state_, _complexity)
        and not state_.get("direct_expert")
        and not _is_agentic_replan
    ):
        _ft_cat = (
            TRIVIAL_FAST_PATH_CATEGORY
            if TRIVIAL_FAST_PATH_CATEGORY in EXPERTS
            else next(iter(EXPERTS), "general")
        )
        _fast_plan = [{"task": state_["input"], "category": _ft_cat}]
        _fast_plan, _fast_events = _prepare_handoff_plan(_fast_plan)
        logger.info("⚡ Trivial fast-path: planner LLM skipped → '%s'", _ft_cat)
        await _report(f"⚡ Trivial fast-path → expert '{_ft_cat}' (planner skipped)")
        await _record_stage(
            state_.get("response_id", ""),
            "planner",
            "fast_path",
            _ft_cat,
        )
        return {
            **_complexity_state_update,
            "plan": _fast_plan,
            "task_events": _fast_events,
            "direct_expert": _ft_cat,
            "trivial_fast_path": True,
            "cynefin_domain": _classify_cynefin_for(_fast_plan),
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }

    # Agent mode: deterministic handoff, no LLM planner. Mandatory precision
    # contracts must survive this shortcut just as they do every LLM path.
    if state_.get("mode") == "agent":
        _agent_plan, _agent_plan_reason = _build_agent_mode_plan(
            state_, _handoff_tool_schemas
        )
        logger.info("📋 Agent mode deterministic plan: %s", _agent_plan_reason)
        await _report(f"📋 Agent mode: {_agent_plan_reason}")
        _agent_plan, _agent_events = _prepare_handoff_plan(_agent_plan)
        return {
            **_complexity_state_update,
            "cynefin_domain": _classify_cynefin_for(_agent_plan),
            "plan": _agent_plan,
            "task_events": _agent_events,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }

    # memory_recall fast-path: if complexity is memory_recall AND the template has a
    # dedicated memory_recall expert configured, skip the LLM planner entirely.
    # The planner LLM would misroute recall questions (e.g. routing to "research")
    # because it cannot distinguish facts from this conversation vs. external knowledge.
    # This bypass is template-driven — only activates when memory_recall is in user_experts.
    _user_experts_map = state_.get("user_experts") or {}
    if _complexity == "memory_recall" and "memory_recall" in _user_experts_map:
        logger.info("🧠 memory_recall fast-path: dedicated expert configured, LLM planner skipped")
        await _report("🧠 Memory Expert: Analysiere Konversationshistorie...")
        _memory_plan, _memory_events = _prepare_handoff_plan(
            [{"task": state_["input"], "category": "memory_recall"}]
        )
        return {
            **_complexity_state_update,
            "cynefin_domain": _classify_cynefin_for(
                _memory_plan
            ),
            "plan": _memory_plan,
            "task_events": _memory_events,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }

    logger.debug("--- [NODE] PLANNER ---")
    await _report("📋 Planner analyzing request...")
    await _record_stage(state_.get("response_id", ""), "planner", "started")
    # When a template defines its own expert set, restrict routing to those categories so
    # the planner cannot accidentally route to a global expert not wired in the template.
    # Fall back to the global EXPERTS list when no template experts are active.
    _NON_EXPERT = {"precision_tools", "research"}
    _user_experts_for_cats = state_.get("user_experts") or {}
    if _user_experts_for_cats:
        expert_categories = [c for c in _user_experts_for_cats.keys()
                             if c not in _NON_EXPERT]
    else:
        expert_categories = list(EXPERTS.keys())
    if "agentic_coder" not in expert_categories and (
        state_.get("mode") in ("agent_orchestrated", "code")
        or "agentic_coder" in _user_experts_for_cats
    ):
        expert_categories = expert_categories + ["agentic_coder"]
    import os as _os
    # "dynamic" hands the task to expert_builder's ad-hoc model discovery
    # (graph/expert.py, services/expert_builder.py), which floats to whatever
    # endpoint currently reports the requested capability -- bypassing every
    # fixed model@endpoint pin the template author set. Offering it
    # unconditionally defeats a template that already pins a complete expert
    # roster: observed live, the planner picked "dynamic" for GraphRAG/topology
    # tasks even though "compounding_knowledge" was pinned and available,
    # silently answering with qwen3.6:35b/gemma4-31b on N04-RTX instead of the
    # template's own SmolLM3 experts. Only offer the escape hatch when the
    # template itself has no pinned roster (global-EXPERTS fallback path,
    # mirroring the expert_categories source distinction directly above).
    if _os.getenv("EXPERT_BUILDER_ENABLED", "true").lower() in ("true", "1", "yes"):
        if not _user_experts_for_cats and "dynamic" not in expert_categories:
            expert_categories = expert_categories + ["dynamic"]

    # Annotate images in planner input so routing triggers 'vision'
    # The marker must be exactly "[BILD-EINGABE vorhanden]" — as specified in the planner rule
    images = state_.get("images") or []
    if images:
        state_ = dict(state_)
        _img_hint = f"[BILD-EINGABE vorhanden] ({len(images)} Bild(er))"
        if "[BILD-EINGABE vorhanden]" not in state_["input"]:
            state_["input"] = f"{_img_hint} {state_['input']}"

    # SELF_EVAL quality hint — informs planner about historical performance
    _quality_hint = ""
    try:
        from telemetry import get_quality_hint as _get_quality_hint
        _quality_hint = await _get_quality_hint(
            state._userdb_pool, state_.get("template_name", ""), _complexity
        )
        if _quality_hint:
            _quality_hint = f"\n{_quality_hint}\n"
    except Exception:
        pass

    # Load proven plan patterns from positive user feedback
    success_hint = ""
    if state.redis_client is not None:
        try:
            patterns = await state.redis_client.zrevrange("moe:planner_success", 0, 4, withscores=True)
            if patterns:
                top = [f"  {sig} ({int(score)}×)" for sig, score in patterns]
                success_hint = (
                    "\nPROVEN EXPERT COMBINATIONS (from positive user feedback — prefer these):\n"
                    + "\n".join(top)
                    + "\n"
                )
        except Exception:
            pass

    # Load few-shot context from self-correction loop (OBJ 3)
    _few_shot_hint = ""
    try:
        from self_correction import get_few_shot_context as _get_fsc
        _plan_categories = list(EXPERTS.keys())  # All categories as hint sources
        _few_shot_hint = await _get_fsc(
            _plan_categories, state.redis_client, max_per_cat=2,
            query=state_.get("input", ""),
        )
    except Exception:
        pass

    # Show agentic code tools only when mode matches or agentic_coder category is active
    _inject_agentic = (
        state_.get("mode") in ("agent_orchestrated", "code")
        or "agentic_coder" in (state_.get("user_experts") or {})
    )
    _agentic_code_block = (
        f"\nCODE NAVIGATION TOOLS (only for 'agentic_coder' category — NOT for other experts!):\n"
        f"Use these tools when code files are to be analyzed/edited.\n"
        f"{state.AGENTIC_CODE_TOOLS_DESCRIPTION}\n"
        f"Format: {{\"task\": \"...\", \"category\": \"precision_tools\", "
        f"\"mcp_tool\": \"repo_map|read_file_chunked|lsp_query\", \"mcp_args\": {{...}}}}\n"
        f"THEN use agentic_coder expert for analysis/implementation.\n"
    ) if _inject_agentic and state.AGENTIC_CODE_TOOLS_DESCRIPTION else ""

    _planner_role = (state_.get("planner_prompt") or "").strip() or DEFAULT_PLANNER_ROLE
    _planner_role_limit = int(os.getenv("PLANNER_ROLE_MAX_CHARS", "8000"))
    _original_planner_role_len = len(_planner_role)
    _planner_role = _compact_planner_role(
        _planner_role,
        _planner_role_limit,
    )
    if len(_planner_role) < _original_planner_role_len:
        logger.warning(
            "Planner role compacted from %d to %d chars",
            _original_planner_role_len,
            len(_planner_role),
        )

    # ── Tier-3 Context TOC: inject table-of-contents for indexed large contexts ──
    # When the session carries a 1M+ context indexed into ChromaDB, prepend a compact
    # TOC so the planner knows what domains/files are available to the experts.
    _context_toc_block = ""
    _session_id_plan = state_.get("session_id", "")
    if _session_id_plan and state.redis_client:
        try:
            from services.context_index import get_context_toc as _get_toc, is_context_indexed as _ctx_indexed
            if await _ctx_indexed(_session_id_plan, state.redis_client):
                _toc_raw = await _get_toc(_session_id_plan, state.redis_client)
                if _toc_raw:
                    _context_toc_block = (
                        f"\n\n[INDEXED CONTEXT OVERVIEW — available to all experts]\n{_toc_raw}\n"
                        "[End of overview. Experts will receive the relevant sections automatically.]\n"
                    )
        except Exception as _tie:
            logger.debug("planner: context TOC injection skipped: %s", _tie)

    # ── Agentic re-plan: inject gap context and clear stale single-string results ──
    _agentic_context_block = ""
    _agentic_state_reset: dict = {}
    if _is_agentic_replan:
        _gap            = (state_.get("agentic_gap") or "").strip()
        _history        = state_.get("agentic_history") or []
        _prev_found     = _history[-1].get("findings", "") if _history else ""
        _wm             = state_.get("working_memory") or {}
        _failures       = state_.get("tool_failures") or []
        _tried_queries  = state_.get("attempted_queries") or []
        _strategy_hint  = (state_.get("search_strategy_hint") or "").strip()

        _budget = _planner_ctx_budget(state_.get("planner_num_ctx", 0))

        # Prefer structured working memory over truncated prose when available
        if _wm:
            _context_facts = "ESTABLISHED FACTS (structured):\n" + json.dumps(_wm)[:_budget["working_memory"]]
        else:
            _context_facts = f"Previously established facts:\n{_prev_found[:_budget['prev_findings']]}"

        # Build search-history block to prevent query repetition
        if _tried_queries:
            _query_lines = "\n".join(
                f"  • [{q.get('quality','?')}] {q.get('query','?')[:_budget['query_line']]}"
                for q in _tried_queries[-_budget["max_queries"]:]
            )
            _search_history_block = (
                f"\nSEARCH QUERIES ALREADY TRIED (do NOT repeat these or near-identical variants):\n"
                f"{_query_lines}\n"
            )
        else:
            _search_history_block = ""

        _fail_block = (
            f"\nFAILED TOOL CALLS (do NOT retry with identical args):\n{json.dumps(_failures[-_budget['max_failures']:])}"
            if _failures else ""
        )

        # Progressive depth hints based on iteration number
        _depth = _agentic_iteration
        if _depth == 1:
            _depth_hint = (
                "SEARCH STRATEGY (Depth 1 — be more specific):\n"
                "  • Use domain-restricted queries: add 'site:wikipedia.org', 'site:github.com', 'site:arxiv.org', 'site:pubchem.ncbi.nlm.nih.gov'\n"
                "  • Try the exact title/name in quotes for precise matches\n"
                "  • Use wikipedia_get_section MCP tool for Wikipedia data with exact section names\n"
                "  • Use github_search_issues MCP tool if querying GitHub repositories\n"
            )
        elif _depth == 2:
            _depth_hint = (
                "SEARCH STRATEGY (Depth 2 — use specialized tools directly):\n"
                "  • Use youtube_transcript MCP tool for video content\n"
                "  • Use chess_analyze_position MCP tool for chess positions — extract FEN from image first, then call the tool\n"
                "  • Use pubchem_compound_search MCP tool for chemical/compound data\n"
                "  • Use orcid_works_count MCP tool for academic publication counts\n"
                "  • Use fetch_pdf_text MCP tool with a direct DOI or PDF URL\n"
                "  • Use python_sandbox MCP tool to run calculations if needed\n"
            )
        else:
            _depth_hint = (
                "SEARCH STRATEGY (Depth 3 — alternative angles):\n"
                "  • Try synonyms, abbreviations, or alternative spellings of the key term\n"
                "  • Search for the source publication/author directly\n"
                "  • Use fetch_pdf_text with any relevant paper URL found\n"
            )

        if _strategy_hint:
            _depth_hint += f"  • Suggested approach from gap analysis: {_strategy_hint}\n"

        # Domains discovered in previous searches — offered as targeted follow-up targets
        _disc_domains: list = state_.get("discovered_domains") or []
        if _disc_domains:
            _domain_lines = "\n".join(
                f"  • {d['domain']}" + (f" — {d['context']}" if d.get("context") else "")
                for d in _disc_domains[:_budget["max_domains"]]
            )
            _discovered_block = (
                "\nSOURCES FOUND IN PREVIOUS SEARCHES (consider using web_search_domain with these):\n"
                f"{_domain_lines}\n"
            )
        else:
            _discovered_block = ""

        _agentic_context_block = (
            f"\n=== AGENTIC ITERATION {_agentic_iteration}/{_agentic_max_rounds} ===\n"
            f"{_context_facts}\n\n"
            f"Still unresolved:\n{_gap[:_budget['gap']]}\n"
            f"{_search_history_block}"
            f"{_discovered_block}"
            f"{_fail_block}\n"
            f"{_depth_hint}\n"
            "Instructions: Focus ONLY on resolving the gap above. "
            "Do NOT repeat subtasks already answered. "
            "Generate DIFFERENT search queries or use specialized MCP tools instead of repeating web search.\n"
            "=== END AGENTIC CONTEXT ===\n"
        )
        await _report(
            f"🔄 Agentic Loop — Iteration {_agentic_iteration}/{_agentic_max_rounds} (Depth {_depth})\n"
            f"📌 Still open: {_gap[:120]}"
        )
        # Clear single-string result fields so old results don't bleed into new iteration.
        # NOTE: working_memory / tool_calls_log / tool_failures / attempted_queries are intentionally preserved.
        _agentic_state_reset = {"web_research": "", "mcp_result": "", "math_result": ""}
        logger.info(f"🔄 Agentic re-plan iteration {_agentic_iteration}/{_agentic_max_rounds} depth={_depth}: gap={_gap[:80]}")

    # ── Advice Taker: declarative rule constraints ──
    from services.advice_store import get_active_advice
    _advice_list = get_active_advice(state_["input"])
    _advice_block = ""
    if _advice_list:
        _advice_items = "\n".join(f"- {rule}" for rule in _advice_list)
        _advice_block = f"\n\n[DECLARATIVE CONSTRAINTS / RULES - you must follow these!]\n{_advice_items}\n"

    # Pick an LLM-expert category for prompt examples so the example never
    # names a category absent from VALID CATEGORIES, which confuses the model.
    _example_cat = next(
        (c for c in expert_categories if c in {"technical_support", "general_assistant", "general"}),
        expert_categories[0] if expert_categories else "general",
    )

    # Compact prompt: the full instruction set below (category rules, dynamic-expert
    # guidance, legal-research pattern, vision rules, skill catalog, 5 worked examples)
    # reliably overwhelms small planner models. Used for trivial requests, and reused
    # on retry for non-trivial requests -- see the retry loop below, where reusing the
    # full prompt verbatim across all attempts has been observed to make output *worse*
    # rather than better (the model pattern-matches on prompt structure instead of the
    # actual task; see agent_status/claude-code.md, FINDING-planner-nontrivial-retry-prompt).
    def _build_compact_prompt(task_budget_text: str) -> str:
        return (
            f"{_planner_role}"
            f"{_context_toc_block}"
            f"{_advice_block}"
            f"\n\nIMPORTANT: Answer EXCLUSIVELY with a JSON array of objects. "
            f"No text, no explanations, no markdown.\n"
            f"Each object MUST have \"task\" (string) and \"category\" (string).\n"
            f"TASK BUDGET: {task_budget_text}.\n\n"
            f"VALID CATEGORIES FOR LLM EXPERTS: {expert_categories}\n"
            f"NOTE: \"precision_tools\" is ALWAYS a valid category for any calculation "
            f"or exact tool call — it is NOT listed above. "
            f"MANDATORY for arithmetic, dates, units, subnet, conversions.\n\n"
            f"PRECISION TOOLS:\n"
            f"  - calculate: arithmetic and math\n"
            f"  - date_diff: date calculations\n"
            f"  - calendar_facts: weekday, ISO calendar facts\n"
            f"  - unit_convert: unit conversions\n"
            f"Format: {{\"task\": \"...\", \"category\": \"precision_tools\", "
            f"\"mcp_tool\": \"<tool>\", \"mcp_args\": {{...}}}}\n\n"
            f"EXAMPLE arithmetic:\n"
            f"Request: \"What is 47+53?\"\n"
            f"Correct: [{{\"task\": \"Calculate 47+53\", \"category\": \"precision_tools\", "
            f"\"mcp_tool\": \"calculate\", \"mcp_args\": {{\"expression\": \"47+53\"}}}}]\n\n"
            f"EXAMPLE general question:\n"
            f"Request: \"What is Docker?\"\n"
            f"Correct: [{{\"task\": \"Explain what Docker is and what it is used for\", "
            f"\"category\": \"{_example_cat}\"}}]\n\n"
            f"Request: {state_['input']}\n\n"
            f"JSON array:"
        )

    # For trivial (non-agentic) requests, use the compact prompt from the start to
    # avoid overwhelming the planner model with irrelevant instructions.
    if _complexity == "trivial" and not _is_agentic_replan:
        prompt = _build_compact_prompt("exactly 1 task")
    else:
        prompt = f"""{_planner_role}{_context_toc_block}{_advice_block}{_agentic_context_block}

IMPORTANT: Answer EXCLUSIVELY with a JSON array of objects. No text, no explanations, no markdown.
Each object MUST contain the fields "task" (string) and "category" (string).
Runtime configuration is authoritative for model assignment. Ignore any model
names or node assignments embedded in a custom role prompt; select categories
and tools only.
TASK BUDGET: Aim for at most {_routing["max_tasks"]} executable tasks for this
request and never exceed the absolute runtime maximum of {PLANNER_MAX_TASKS}.
Combine compatible non-precision work when necessary, but never omit a
separately requested outcome or remove/downgrade a required precision tool.

VALID CATEGORIES FOR LLM EXPERTS: {expert_categories}
NOTE: "precision_tools" is ALWAYS a valid category for any calculation or tool call — it is NOT an LLM expert and is NOT listed above. You MUST use it for arithmetic, dates, units, etc.

DYNAMIC EXPERT — for highly specialised domains not covered by the categories above:
Use "dynamic" when the task requires deep domain expertise in a field absent from the standard expert list
(e.g. Immobilienwertermittlung, Chemische Prozessoptimierung, Notfallmedizin, Schiffbaurecht).
REQUIRED additional fields: "domain" (human-readable domain name, German or English) and "task" (concrete subtask).
Optional: "requires" (list of scoring-category hints, e.g. ["legal_advisor", "math"]).
Optional: "privacy": "local_only" to restrict model selection to local-only endpoints.
Optional: "no_search": true to suppress automatic inline web research for the domain.
Example: {{"task": "Berechne den Verkehrswert eines Einfamilienhauses nach ImmoWertV", "category": "dynamic", "domain": "Immobilienwertermittlung", "requires": ["math"]}}
Only use "dynamic" when NO existing category covers the domain adequately.
RESEARCH + DYNAMIC: When the dynamic expert needs current information (prices, regulations, studies, standards),
add a "research" task BEFORE the dynamic task so the expert receives fresh web context:
[{{"task": "Aktuelle ImmoWertV Richtlinien und Sachwertfaktoren recherchieren", "category": "research", "search_query": "ImmoWertV 2024 Sachwertfaktoren aktuell"}},
 {{"task": "Verkehrswert berechnen...", "category": "dynamic", "domain": "Immobilienwertermittlung", "requires": ["math"]}}]

KNOWLEDGE STORAGE / MEMORY REQUESTS — when the user asks to store, persist, register, or remember information in the knowledge graph:
Do NOT hand-encode the data as a JSON string inside "task", and do NOT invent extra fields for this. Every committed response is automatically fact-extracted and written to the knowledge graph in the background — a single plain-language task that restates and acknowledges the information is sufficient and correct.
Format: {{"task": "Acknowledge the following information and confirm it is noted: <restate the key facts in plain prose, not JSON>", "category": "{_example_cat}"}}
WRONG: a task whose "task" field contains escaped JSON, code fences, or a nested string re-encoding the input.

WEB RESEARCH — for current/external info OR for domain specifications in implementation tasks:
{{"task": "task description", "category": "research", "search_query": "short optimized search term"}}
Use for: game rules · algorithm specifications · protocols/standards · anything where correct logic is critical for implementation.

PRECISION TOOLS — MANDATORY for all exact calculations (LLMs calculate WRONG!):
REQUIRED for: arithmetic · subnet/IP/CIDR · date/time · units · hashes · regex · statistics
{_build_filtered_tool_desc(state_["input"], enable_graphrag=state_.get("enable_graphrag", False))}
Format: {{"task": "task description", "category": "precision_tools", "mcp_tool": "<toolname>", "mcp_args": {{<args>}}}}

CHAINED CALCULATIONS — when one calculation needs the RESULT of a PREVIOUS calculation (e.g. multi-year escalation, running totals):
Give each precision_tools task a stable "id" and reference an earlier task's result as {{"$task_result": "<id>"}} instead of computing or guessing the intermediate value yourself.
Example: "Tariff is 0.10 EUR in year 1, +5% in year 2":
[{{"id": "year1", "task": "Year 1 tariff", "category": "precision_tools", "mcp_tool": "decimal_finance", "mcp_args": {{"operation": "add", "operands": ["0.10", "0"], "currency": "EUR", "scale": 4, "rounding": "half_even"}}}},
 {{"id": "year2", "task": "Year 2 tariff (+5% on year 1)", "category": "precision_tools", "mcp_tool": "decimal_finance", "mcp_args": {{"operation": "percentage", "operands": [{{"$task_result": "year1"}}, "105"], "currency": "EUR", "scale": 4, "rounding": "half_even"}}}}]
A reference MUST point to an earlier task in the same list — never to itself or to a later task.
{_agentic_code_block}
LEGAL RESEARCH — for questions about German law (laws, paragraphs, legal norms):
Use the legal_* tools to retrieve exact legal texts; ALWAYS combine with legal_advisor expert for interpretation.
Typical pattern:
  1. legal_search_laws → finds relevant laws when abbreviation is unknown
  2. legal_get_law_overview → shows all §§ when paragraph is unknown
  3. legal_get_paragraph → retrieves exact legal text (REQUIRED for §-questions!)
  4. legal_fulltext_search → keyword search within a law
  5. legal_advisor expert → interprets, explains, applies

EXAMPLE legal question:
Request: "What does § 242 BGB say?"
Correct: [{{"task": "Get § 242 BGB legal text", "category": "precision_tools", "mcp_tool": "legal_get_paragraph", "mcp_args": {{"law": "BGB", "paragraph": "242"}}}}, {{"task": "Explain § 242 BGB (good faith) — meaning, elements, legal consequences, case examples", "category": "legal_advisor"}}]
WRONG: [{{"task": "What does § 242 BGB say?", "category": "legal_advisor"}}]
← ERROR: legal text missing — LLM hallucinate legal text!

VISION EXPERT — for image and document processing:
- "vision": REQUIRED when [IMAGE INPUT present] is in the input or the user explicitly wants images/photos/screenshots/diagrams/documents analyzed.
- For combined requests (image + code/text): vision task FIRST, then further experts with vision task result as context.

RULES:
- precision_tools has ABSOLUTE PRIORITY — NEVER use "math" or "technical_support" for calculations!
- Legal questions → ALWAYS get legal_get_paragraph AND legal_advisor expert for interpretation
- Subnet mask / IP / CIDR / gateway for ONE network → ALWAYS subnet_calc, NEVER technical_support
- VLSM: splitting ONE parent CIDR into MULTIPLE named subnets sized by required host counts → ALWAYS vlsm_subnet_calc with mcp_args {{"cidr": "...", "subnets": [{{"id": "...", "hosts": N}}, ...]}} — NEVER pass a "subnets" list to subnet_calc, it only accepts a single "cidr"
- Regex extraction from text → ALWAYS regex_extract, NEVER technical_support
- For implementations with domain-specific logic (games, algorithms, protocols): research task FIRST, then code tasks
- Task descriptions for code experts MUST contain all known rules/specifications (logic, constraints, algorithm details) — experts only see their task description!
- Simple requests → exactly one task, no overengineering
- NEVER just keywords or questions as tasks — always concrete task descriptions!
- OPTIONAL: Add a "metadata_filters" key to the FIRST task object when the domain is unambiguous, to scope downstream memory retrieval. Use string values only. Omit when unsure.
  Example: {{"task": "...", "category": "code_reviewer", "metadata_filters": {{"expert_domain": "code_reviewer", "project": "frontend"}}}}
{_build_skill_catalog()}
{_quality_hint}{success_hint}{_few_shot_hint}
EXAMPLE arithmetic:
Request: "What is 47+53?"
Correct: [{{"task": "Calculate 47+53", "category": "precision_tools", "mcp_tool": "calculate", "mcp_args": {{"expression": "47+53"}}}}]
WRONG:   [{{"task": "Berechne 47+53", "category": "math"}}]

EXAMPLE subnet calculation:
Request: "What subnet mask for 10.42.155.160/27 with 14 hosts?"
Correct: [{{"task": "Subnet info for 10.42.155.160/27", "category": "precision_tools", "mcp_tool": "subnet_calc", "mcp_args": {{"cidr": "10.42.155.160/27"}}}}]
WRONG:   [{{"task": "Calculate subnet mask", "category": "technical_support"}}]

EXAMPLE VLSM (multiple named subnets from one parent block):
Request: "Split 10.180.0.0/19 into subnets A(2000 hosts), B(1000 hosts), C(250 hosts)"
Correct: [{{"task": "VLSM-allocate 10.180.0.0/19 for subnets A, B, C", "category": "precision_tools", "mcp_tool": "vlsm_subnet_calc", "mcp_args": {{"cidr": "10.180.0.0/19", "subnets": [{{"id": "A", "hosts": 2000}}, {{"id": "B", "hosts": 1000}}, {{"id": "C", "hosts": 250}}]}}}}]
WRONG:   [{{"task": "...", "category": "precision_tools", "mcp_tool": "subnet_calc", "mcp_args": {{"cidr": "10.180.0.0/19", "subnets": [...]}}}}]
← ERROR: subnet_calc's schema only accepts "cidr" — a "subnets" list is rejected (additionalProperties)

EXAMPLE game implementation with domain logic:
Request: "Create a Connect Four game as HTML5 page"
Correct: [
  {{"task": "Research Connect Four rules and correct implementation details (column click, gravity, win detection)", "category": "research", "search_query": "Connect Four rules implementation falling pieces column click win detection algorithm"}},
  {{"task": "Implement Connect Four in HTML5/CSS/JS. MANDATORY RULES: 7 columns × 6 rows; click on column → piece falls to LOWEST free row (not inserted at top!); win = 4 in a row horizontal/vertical/diagonal; move invalid when column full", "category": "code_reviewer"}}
]
WRONG: [{{"task": "Implement HTML5 base structure", "category": "code_reviewer"}}, {{"task": "Write JS game logic", "category": "code_reviewer"}}]
← ERROR: game rules missing from task description, no research task, logic will be implemented incorrectly

EXAMPLE simple request:
Request: "What is Docker?"
Correct: [{{"task": "Explain what Docker is and what it is used for", "category": "{_example_cat}"}}]
WRONG:   ["Docker", "Container", "Virtualization"]

Request: {state_['input']}

JSON array:"""
    await _report(f"📋 Planner prompt ({len(prompt)} chars):\n{prompt}")
    total_usage: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
    plan: Optional[list] = None
    _planned_task_events: list[dict] = []
    _extracted_filters: Dict = {}
    _structured_failure_state: dict = {}
    from parsing import _extract_usage, _extract_json
    from config import PLANNER_URL, PLANNER_MODEL, PLANNER_TOKEN
    from services.structured_failure import (
        RecoveryAction as _RecoveryAction,
        build_failure as _build_failure,
        resolve_retry_model as _resolve_retry_model,
    )

    _structured_max_retries = max(
        0, int(os.getenv("STRUCTURED_FAILURE_MAX_RETRIES", "2"))
    )
    _structured_fallback_model = os.getenv(
        "STRUCTURED_FAILURE_FALLBACK_MODEL", ""
    ).strip()
    # Initial attempt + configured same-model retries + one fallback attempt.
    _structured_attempts = max(
        2,
        1 + _structured_max_retries + bool(_structured_fallback_model),
    )

    _retry_model_override = ""
    _contract_repair_used = False
    _contract_repair_hint = ""
    for attempt in range(_structured_attempts):
        _attempt_state = state_
        _using_structured_fallback = bool(_retry_model_override)
        if _retry_model_override:
            _attempt_state = {
                **dict(state_),
                "planner_model_override": _retry_model_override,
            }
        res = None
        try:
            # On retry, non-trivial requests switch to the compact prompt too: reusing
            # the full prompt verbatim (just growing it with a repair hint) has been
            # observed to make a small planner model's output worse, not better -- see
            # _build_compact_prompt above. Trivial requests already use it from attempt 0.
            _is_compact_already = _complexity == "trivial" and not _is_agentic_replan
            if attempt >= 1 and not _is_compact_already:
                _retry_task_budget = (
                    f"aim for at most {_routing['max_tasks']} executable tasks, "
                    f"never exceed {PLANNER_MAX_TASKS}"
                )
                _attempt_prompt = _build_compact_prompt(_retry_task_budget) + _contract_repair_hint
            else:
                _attempt_prompt = prompt + _contract_repair_hint
            res, _planner_fb = await _invoke_planner_with_retry(
                _attempt_state,
                _attempt_prompt,
                temperature=_query_temp,
                attempt=attempt,
            )
            if _planner_fb:
                await _report(
                    "⚠️ Planner: used local fallback (primary endpoint degraded)"
                )
            u = _extract_usage(res)
            total_usage["prompt_tokens"] += u["prompt_tokens"]
            total_usage["completion_tokens"] += u["completion_tokens"]

            # Use the shared tolerant contract parser; it accepts an array or
            # {"tasks": [...]} and preserves task-specific routing fields.
            _plan_text = res.content.strip()
            logger.info("PLANNER RAW OUTPUT: %r", _plan_text)
            _contract_plan = _parse_plan_contract(_plan_text)
            logger.info("CONTRACT PLAN VALID: %s, TASKS: %d", _contract_plan.valid, len(_contract_plan.tasks))
            if not _contract_plan.valid:
                raw, _explicit_recovery_events = (
                    _recover_explicit_supported_plan(
                        state_["input"],
                        _handoff_tool_schemas,
                        max_tasks=PLANNER_MAX_TASKS,
                    )
                )
                if raw:
                    logger.warning(
                        "Planner returned no executable tasks; recovered fully "
                        "explicit supported task list: %s",
                        json.dumps(
                            _explicit_recovery_events,
                            ensure_ascii=False,
                        ),
                    )
                else:
                    raise _PlannerContractError(
                        [
                            _PlannerContractIssue(
                                -1,
                                "invalid_json_plan",
                                "planner response does not contain an executable JSON task array",
                                "tasks",
                            )
                        ]
                    )
            else:
                raw = [task.payload for task in _contract_plan.tasks]

            # Extract optional metadata_filters from first task before sanitizing
            if raw and isinstance(raw[0], dict) and "metadata_filters" in raw[0]:
                _extracted_filters = raw[0].pop("metadata_filters", {})
                if not isinstance(_extracted_filters, dict):
                    _extracted_filters = {}
            # Extract optional output_skill suggestion from any task
            _output_skill = ""
            for _raw_task in raw:
                if isinstance(_raw_task, dict) and _raw_task.get("output_skill"):
                    _skill_name = str(_raw_task.pop("output_skill")).strip().lstrip("/")
                    from services.skills import _load_skill_body
                    _skill_body = _load_skill_body(_skill_name)
                    if _skill_body:
                        _output_skill = _skill_body
                        logger.info(f"🎯 Planner suggested skill: /{_skill_name}")
                    break
            _user_expert_cats = set((state_.get("user_experts") or {}).keys())
            from services.advice_store import enforce_advice_rules
            plan = _sanitize_plan(raw, state_["input"], _user_expert_cats)
            plan = enforce_advice_rules(
                state_["input"], plan, _handoff_tool_schemas,
            )
            plan, _planned_task_events = _prepare_handoff_plan(plan)
            categories = [t.get("category", "?") for t in plan]
            logger.info(f"📋 Plan ({len(plan)} Tasks): {json.dumps(plan, ensure_ascii=False)}")
            await _report(f"📋 Plan: {len(plan)} Task(s) → {', '.join(categories)}")
            await _record_stage(state_.get("response_id", ""), "planner", "done", ", ".join(categories))
            for _pt in plan:
                _desc = (_pt.get("task") or "")[:80]
                _ptcat = _pt.get("category", "?")
                _extra = "…" if len(_pt.get("task", "")) > 80 else ""
                await _report(f"  • [{_ptcat}] {_desc}{_extra}")
            await _report(
                f"📋 Planner done — {total_usage['prompt_tokens']} prompt tok / "
                f"{total_usage['completion_tokens']} completion tok"
            )
            _structured_failure_state = {
                "structured_failure": {},
                "structured_failure_round": attempt,
            }
            break
        except Exception as exc:
            _is_contract_failure = isinstance(exc, _PlannerContractError)
            _raw_text = getattr(res, "content", "") or ""
            _failure = _build_failure(
                exc,
                model=(
                    _structured_fallback_model
                    if _using_structured_fallback else
                    (state_.get("planner_model_override") or PLANNER_MODEL)
                ),
                stage="planner",
                fallback_model=_structured_fallback_model,
                raw_text=_raw_text,
                retry_round=attempt + 1,
            )
            _structured_failure_state = {
                "structured_failure": _failure.as_dict(),
                "structured_failure_round": attempt + 1,
            }
            # Contract failures used to get exactly one repair retry
            # (gated on _contract_repair_used) regardless of the remaining
            # _structured_attempts budget, so two consecutive hallucinated
            # non-JSON replies exhausted recovery after only 2 of the 3
            # configured attempts and raised (-> HTTP 500) even though a
            # third, still-budgeted attempt was available. Observed live: a
            # complex eBPF/XDP task failed this way -- attempt 1 (full
            # prompt) echoed the planner's own category-reference catalog
            # instead of a task list, attempt 2 (compact retry prompt)
            # produced a different but equally non-JSON reply, and recovery
            # gave up rather than trying a 3rd time. temperature=0.7 makes
            # attempts genuinely stochastic, so spending the full configured
            # budget before giving up (same bound already used for
            # non-contract failures) meaningfully raises the chance of
            # eventually getting valid JSON, without changing the
            # deliberate "raise rather than silently mask" behavior once
            # that full budget is actually exhausted (see below).
            _can_retry_contract = (
                _is_contract_failure
                and attempt + 1 < _structured_attempts
            )
            _can_retry_other = (
                not _is_contract_failure
                and attempt + 1 < _structured_attempts
            )
            if _can_retry_contract or _can_retry_other:
                if _is_contract_failure and not _contract_repair_used:
                    _contract_repair_used = True
                    _contract_repair_hint = exc.repair_instruction()
                _next_action = (
                    _RecoveryAction.RETRY_FALLBACK
                    if _structured_fallback_model
                    and attempt >= _structured_max_retries
                    else _RecoveryAction.RETRY_SAME
                )
                _next_model = _resolve_retry_model(_failure, _next_action)
                _retry_model_override = (
                    _next_model
                    if _next_model != (
                        state_.get("planner_model_override") or PLANNER_MODEL
                    )
                    else ""
                )
                logger.warning(
                    "Planner structured failure round %d/%d (%s) — retrying with %s",
                    attempt + 1,
                    _structured_attempts,
                    _failure.failure_kind.value,
                    _next_model,
                )
                await _report(
                    "⚠️ Planner: contract invalid — one bounded repair"
                    if _is_contract_failure
                    else "⚠️ Planner: structured output invalid — retrying"
                )
                continue

            logger.error(
                "Planner structured recovery exhausted after %d attempts: %s",
                _structured_attempts,
                exc,
            )
            await _report("⚠️ Planner-Fallback: general (recovery exhausted)")
            try:
                from services.cascade import (
                    CascadeEvent,
                    CascadeType,
                    emit_cascade,
                )
                emit_cascade(
                    CascadeEvent(
                        CascadeType.SPEC_GAP,
                        f"Planner structured output failed: {exc}",
                        "retry with a schema-capable planner model",
                    ),
                    request_id=state_.get("response_id", ""),
                )
            except Exception as cascade_error:
                logger.debug(
                    "Planner structured failure cascade skipped: %s",
                    cascade_error,
                )
            if _is_contract_failure:
                # A malformed executable plan must not silently become a
                # generic LLM task: that would make precision/research work
                # disappear while returning an apparently successful answer.
                raise
            plan, _planned_task_events = _prepare_handoff_plan(
                [{"id": "task-1", "task": state_["input"], "category": "general"}]
            )
            _extracted_filters = {}
    # VRAM management is left entirely to Ollama's own automatic LRU eviction
    # (evicts the least-recently-used loaded model only when a newly
    # requested model genuinely doesn't fit) plus each endpoint's own
    # OLLAMA_KEEP_ALIVE/keep_alive setting. This code used to proactively
    # unload the planner model here "just in case" after every single
    # invocation, regardless of whether any other model actually needed the
    # freed VRAM — which silently overrode a longer keep_alive (e.g. 4h) with
    # an immediate forced unload on every turn, including mid-conversation
    # gaps of an unrelated long-lived agentic tool session sharing the same
    # model+node. Removed; see git history for the old _can_coexist_on_node /
    # _is_model_busy_elsewhere-gated proactive-unload logic if reintroducing
    # anything here for genuinely VRAM-constrained nodes.
    # ── Deterministic DoR checks ───────────────────────────────────────────────
    # Validate each task before it is dispatched to an expert.
    try:
        from services.dor_check import check_dor as _check_dor, log_dor_result as _log_dor
        _req_id = state_.get("response_id", "")
        for _dor_idx, _dor_task in enumerate(plan or []):
            _violations = _check_dor(_dor_task, dict(state_), task_index=_dor_idx)
            _log_dor(_dor_task, _violations, task_index=_dor_idx, request_id=_req_id)
    except Exception as _dor_e:
        logger.debug("DoR check skipped: %s", _dor_e)
    # A successful replan resolves the open failure events that triggered it.
    if _is_agentic_replan and plan:
        try:
            from services.cascade import resolve_open_cascades
            resolve_open_cascades(state_.get("response_id", ""))
        except Exception as _resolve_error:
            logger.debug("Cascade resolution after replan failed: %s", _resolve_error)

    try:
        _complexity_state_update["cynefin_domain"] = _classify_cynefin_for(plan or [])
    except Exception as _ce:
        logger.debug("Final planner Cynefin classification failed: %s", _ce)
    update_request_snapshot(
        state_.get("response_id", ""),
        complexity_level=_complexity_state_update.get("complexity_level"),
        cynefin_domain=_complexity_state_update.get("cynefin_domain"),
        expert_domains=",".join(
            sorted(
                {
                    str(task.get("category") or "")
                    for task in (plan or [])
                    if isinstance(task, dict) and task.get("category")
                }
            )
        ),
    )

    # Validate DAG using Kahn's algorithm
    if plan and isinstance(plan, list):
        dag_dict = {
            t["id"]: [t["depends_on"]] if t.get("depends_on") else []
            for t in plan if isinstance(t, dict) and t.get("id")
        }
        if not validate_dag_kahn(dag_dict):
            logger.warning("planner_node: Generated plan contains cycles according to Kahn's algorithm")

    # Cache plan in Valkey for reuse (fail-safe)
    if state.redis_client is not None and plan:
        asyncio.create_task(state.redis_client.setex(_plan_cache_key, 1800, json.dumps(plan)))
    if _extracted_filters:
        logger.info(f"📋 Planner metadata_filters: {_extracted_filters}")
    _skill_state = {"output_skill_body": _output_skill} if _output_skill else {}
    return {"plan": plan, "task_events": _planned_task_events,
            "metadata_filters": _extracted_filters,
            **total_usage, **_complexity_state_update, **_skill_state,
            **_agentic_state_reset, **_structured_failure_state}


def _topological_levels(tasks: list[tuple[int, dict]]) -> list[list[tuple[int, dict]]]:
    """Group (index, task) pairs into dependency levels for mixed parallel/sequential execution.

    Tasks within the same level have no dependency on each other and run in parallel.
    A task in level N+1 depends on at least one task in level <= N.

    Tasks with no 'depends_on' field (or with an unresolvable dependency) are placed
    in level 0 and run immediately in parallel with other independent tasks.
    """
    id_to_idx: dict[str, int] = {}
    for orig_idx, t in tasks:
        tid = t.get("id", "")
        if tid:
            id_to_idx[tid] = orig_idx

    levels: list[list[tuple[int, dict]]] = []
    placed: set[str] = set()          # task IDs that have been scheduled
    placed_orig: set[int] = set()     # original indices of placed tasks
    remaining = list(tasks)

    while remaining:
        # A task is ready if its dependency is already placed (or it has none)
        ready = []
        still_waiting = []
        for item in remaining:
            orig_idx, t = item
            dep = t.get("depends_on", "")
            if not dep or dep in placed:
                ready.append(item)
            else:
                still_waiting.append(item)

        if not ready:
            # Circular or unresolvable dependency — place all remaining as independent
            levels.append(still_waiting)
            break

        levels.append(ready)
        for orig_idx, t in ready:
            tid = t.get("id", "")
            if tid:
                placed.add(tid)
            placed_orig.add(orig_idx)
        remaining = still_waiting

    return levels


def _inject_prior_results(task: dict, prior_outputs: dict[str, str]) -> dict:
    """Substitute {result_of:task_id} placeholders in task fields with prior expert outputs.

    Creates a shallow copy of task with placeholders replaced so the dependent
    expert receives concrete context from its predecessor.

    prior_outputs: {task_id: expert_output_text (trimmed to ~400 chars)}
    """
    if not prior_outputs:
        return task

    def _sub(text: str) -> str:
        import re as _re
        def _replace(m: re.Match) -> str:
            tid = m.group(1).strip()
            val = prior_outputs.get(tid, "")
            return val[:400] if val else f"[result_of:{tid} — not available]"
        return _re.sub(r'\{result_of:([^}]+)\}', _replace, text)

    out = dict(task)
    for field in ("task", "search_query", "mcp_args"):
        if isinstance(out.get(field), str):
            out[field] = _sub(out[field])
        elif isinstance(out.get(field), dict):
            out[field] = {k: _sub(v) if isinstance(v, str) else v
                          for k, v in out[field].items()}
    return out

def validate_dag_kahn(dag_dict: dict) -> bool:
    """
    Validates whether an execution plan represented as an adjacency dictionary
    is a cycle-free Directed Acyclic Graph (DAG) using Kahn's Algorithm.
    
    Args:
        dag_dict: Adjacency dict (e.g. {'node_a': ['node_b'], 'node_b': []})
        
    Returns:
        True if valid (no cycles), False otherwise.
    """
    import collections
    
    in_degree = {u: 0 for u in dag_dict}
    for u in dag_dict:
        for v in dag_dict[u]:
            if v not in in_degree:
                in_degree[v] = 0
            in_degree[v] += 1
            
    queue = collections.deque([u for u in in_degree if in_degree[u] == 0])
    visited_count = 0
    
    while queue:
        u = queue.popleft()
        visited_count += 1
        
        for v in dag_dict.get(u, []):
            in_degree[v] -= 1
            if in_degree[v] == 0:
                queue.append(v)
                
    return visited_count == len(in_degree)

def verify_cot_step_z3(step_context: str, deduction: str) -> dict:
    """
    Lightweight rule-based heuristic to check if a Chain-of-Thought
    deduction logically fits the context.
    
    Args:
        step_context: The context string.
        deduction: The deduction string to check.
        
    Returns:
        dict with 'is_valid', 'step', and 'diagnostic_error'
    """
    ctx_lower = step_context.lower()
    ded_lower = deduction.lower()
    
    # Extract key terms from context (words with length > 4)
    key_terms = [w.strip('.,!?') for w in ctx_lower.split() if len(w.strip('.,!?')) > 4]
    
    # Check if any key term is referenced
    references_context = any(term in ded_lower for term in key_terms)
    if not references_context and key_terms:
        return {
            'is_valid': False,
            'step': deduction,
            'diagnostic_error': 'Deduction does not reference context keywords.'
        }
        
    # Check for simple negation contradictions
    if "not" in ded_lower.split() and "not" not in ctx_lower.split():
        return {
            'is_valid': False,
            'step': deduction,
            'diagnostic_error': 'Contradiction: negation found.'
        }
                
    return {
        'is_valid': True,
        'step': deduction,
        'diagnostic_error': None
    }
