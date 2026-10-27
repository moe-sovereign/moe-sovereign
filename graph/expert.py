"""graph/expert.py — expert worker (two-tier MoE execution with dependency levels)."""

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
    PLANNER_TIMEOUT, MAX_EXPERT_OUTPUT_CHARS, MAX_EXPERT_TOKENS,
    MAX_EXPERT_TOKENS_CODE, MAX_EXPERT_OUTPUT_CHARS_CODE,
    HISTORY_MAX_TURNS, HISTORY_MAX_CHARS,
    EXPERT_OUTPUT_DIVISOR, EXPERT_INPUT_MIN_CHARS, EXPERT_CHARS_PER_TOKEN,
    CACHE_HIT_THRESHOLD, SOFT_CACHE_THRESHOLD, SOFT_CACHE_MAX_EXAMPLES,
    ROUTE_THRESHOLD, ROUTE_GAP, CACHE_MIN_RESPONSE_LEN,
    EXPERT_TIER_BOUNDARY_B, EXPERT_MIN_SCORE, EXPERT_MIN_DATAPOINTS,
    TRIVIAL_LOW_CONF_RESCUE_ENABLED,
    BENCHMARK_SHADOW_TEMPLATE, BENCHMARK_SHADOW_RATE,
    MCP_URL, GRAPH_VIA_MCP, MAX_GRAPH_CONTEXT_CHARS,
    LITELLM_URL, _SEARXNG_URL, _WEB_SEARCH_FALLBACK_DDG,
    _FUZZY_VECTOR_THRESHOLD, _FUZZY_GRAPH_THRESHOLD,
    _GRAPH_COMPRESS_THRESHOLD_FACTOR, _GRAPH_COMPRESS_LLM_MODEL, _GRAPH_COMPRESS_LLM_TIMEOUT,
    CORRECTION_MEMORY_ENABLED, THOMPSON_SAMPLING_ENABLED,
    JUDGE_REFINE_MAX_ROUNDS, JUDGE_REFINE_MIN_IMPROVEMENT,
    _CUSTOM_EXPERT_PROMPTS, PLANNER_MAX_TASKS, PLANNER_RETRIES,
    KAFKA_TOPIC_INGEST, NEO4J_URI, NEO4J_USER, NEO4J_PASS,
    _FALLBACK_ENABLED, JUDGE_NUM_CTX, EXPERT_THINKING_ENABLED,
)
from metrics import (
    PROM_EXPERT_CALLS, PROM_CONFIDENCE, PROM_CACHE_HITS, PROM_CACHE_MISSES,
    PROM_TIER_ESCALATION,
    PROM_SELF_EVAL, PROM_COMPLEXITY, PROM_ACTIVE_REQUESTS,
    PROM_TOOL_CALL_DURATION, PROM_TOOL_TIMEOUTS, PROM_TOOL_FORMAT_ERRORS,
    PROM_TOOL_CALL_SUCCESS, PROM_SEMANTIC_MEMORY_STORED, PROM_SEMANTIC_MEMORY_HITS,
    PROM_CORRECTIONS_INJECTED, PROM_CORRECTIONS_STORED,
    PROM_JUDGE_REFINED, PROM_EXPERT_FAILURES, PROM_SYNTHESIS_CREATED,
    PROM_HISTORY_COMPRESSED, PROM_HISTORY_UNLIMITED,
    PROM_BUDGET_EXCEEDED,
)
from services.inference import (
    _select_node, _invoke_llm_with_fallback, _invoke_judge_with_retry,
    _get_judge_llm, _get_planner_llm, _get_expert_score, _record_expert_outcome,
    assign_gpu, _refine_expert_response,
    _mark_endpoint_degraded, _endpoint_is_degraded,
    _audit_create, _audit_complete, _ollama_answer_content,
)
from services.routing import (
    _resolve_user_experts, _resolve_template_prompts, _server_info, _is_endpoint_error,
)
from services.kafka import _kafka_publish
from services.tracking import _increment_user_budget, _record_stage, _record_node_latency
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

# Cross-module: dependency-level helpers live in graph.planner
from graph.planner import _topological_levels, _inject_prior_results
from services.deadline import (
    RequestDeadlineExceeded,
    bounded_output_tokens,
    remaining_timeout,
)
from services.deliberation.capacity import CapacityInputs, plan_deliberation_capacity
from services.deliberation.contracts import (
    legacy_deliberation_policy,
    parse_deliberation_policy,
)
from services.deliberation.runtime import (
    DeliberationExecutionError,
    ModeratorDecision,
    build_moderator_prompt,
    build_role_roster,
    build_turn_task,
    compact_transcript,
    max_role_repetition,
    parse_moderator_decision,
)


def _tier2_escalation_decision(cost_tier_t1: bool, t1_confs: list, has_tier2: bool) -> str:
    """Decide the two-tier outcome from the T1 confidences. Pure function (unit-tested).

    t1_confs: per-T1-expert confidences ("high"/"medium"/"low"/None for unparseable).
    Returns one of:
      "t1_high_skip" — a T1 expert was high-confidence; T2 skipped (T2 was available)
      "t1_cost_kept" — cost-tier trivial task kept a good-enough (medium) T1 answer
                       even though T2 was available — the deliberate cost saving
      "t1_only"      — kept T1 because no T2 tier was available at all
      "t2_escalated" — escalate to T2

    Rules:
      • normal task      → escalate on anything below 'high'
      • cost-tier trivial → escalate only on a low/empty answer ('medium' keeps the saving)
    """
    t1_has_high = "high" in t1_confs
    t1_is_weak = (not t1_confs) or any(c == "low" for c in t1_confs)
    should_escalate = t1_is_weak if cost_tier_t1 else (not t1_has_high)
    if t1_has_high:
        return "t1_high_skip" if has_tier2 else "t1_only"
    if not has_tier2:
        return "t1_only"
    if not should_escalate:
        # T2 was available but not needed — only reachable on a cost-tier task
        # with a good-enough (medium) answer.
        return "t1_cost_kept"
    return "t2_escalated"


async def expert_worker(state_: AgentState):
    if state_.get("cache_hit"):
        return {"expert_results": []}

    NON_EXPERT_CATEGORIES = {"precision_tools", "math"}
    # "research" is normally owned exclusively by research_node (live web
    # search) -- excluded here to avoid answering it twice. But research_node
    # itself skips web search (enable_web_research=False template toggle, or
    # skip_research complexity routing) and returns the task with status
    # "skipped", never "completed". With research also excluded here, such a
    # task never gets ANY output, and quality_gate_node's incomplete_plan_tasks
    # check then permanently blocks the whole response with
    # "incomplete_task_execution:<id>:skipped" -- observed live, every request
    # containing a research-category task on a research-disabled template.
    # Only exclude "research" here when research_node is actually going to
    # answer it; otherwise fall back to the category's configured expert model
    # (e.g. smollm3-expert-research-3b) so the task still gets a real answer.
    if state_.get("enable_web_research", True) and not state_.get("skip_research"):
        NON_EXPERT_CATEGORIES = NON_EXPERT_CATEGORIES | {"research"}
    local_conflicts = []
    plan         = state_.get("plan", [])
    chat_history = state_.get("chat_history") or []
    expert_tasks = [
        (i, t) for i, t in enumerate(plan)
        if isinstance(t, dict) and t.get("category", "general") not in NON_EXPERT_CATEGORIES
    ]
    if not expert_tasks:
        return {"expert_results": []}

    from config import JMOE_DEBATE_ENABLED

    raw_deliberation_policy = state_.get("deliberation_policy")
    deliberation_policy = (
        parse_deliberation_policy(raw_deliberation_policy)
        if raw_deliberation_policy
        else legacy_deliberation_policy(JMOE_DEBATE_ENABLED)
    )
    effective_expert_catalog = state_.get("user_experts") or EXPERTS
    distinct_models = {
        (str(model.get("model") or ""), str(model.get("endpoint") or ""))
        for models in effective_expert_catalog.values()
        if isinstance(models, list)
        for model in models
        if isinstance(model, dict) and model.get("enabled", True) and model.get("model")
    }
    deadline = state_.get("request_deadline_monotonic")
    remaining_seconds = None
    if deadline not in (None, ""):
        try:
            remaining_seconds = max(0.0, float(deadline) - time.monotonic())
        except (TypeError, ValueError) as exc:
            raise DeliberationExecutionError(
                "invalid request deadline for deliberation"
            ) from exc
    cynefin_domain = str(state_.get("cynefin_domain") or "")
    if not cynefin_domain:
        from services.cynefin import classify_cynefin

        cynefin_domain = classify_cynefin(dict(state_)).value
    deliberation_capacity = plan_deliberation_capacity(
        deliberation_policy,
        CapacityInputs(
            complexity_level=str(state_.get("complexity_level") or "trivial"),
            cynefin_domain=cynefin_domain,
            trust_verdict=str(state_.get("trust_verdict") or ""),
            plan=plan,
            remaining_seconds=remaining_seconds,
            available_models=len(distinct_models),
        ),
    )
    local_deliberation_events: list[dict[str, Any]] = [
        {
            "event": "capacity_planned",
            "mode": deliberation_capacity.selected_mode,
            "active": deliberation_capacity.active,
            "initial_agents": deliberation_capacity.initial_agents,
            "reserve_agents": deliberation_capacity.reserve_agents,
            "initial_rounds": deliberation_capacity.initial_rounds,
            "reserve_rounds": deliberation_capacity.reserve_rounds,
            "reason": deliberation_capacity.activation_reason,
            "budget_limited": deliberation_capacity.budget_limited,
        }
    ]
    if (
        deliberation_policy.activation == "required"
        and not deliberation_capacity.active
        and deliberation_policy.fallback == "fail"
    ):
        raise DeliberationExecutionError(
            f"required deliberation unavailable: {deliberation_capacity.activation_reason}"
        )

    logger.info(f"--- [NODE] EXPERTS ({len(expert_tasks)} Tasks, Two-Tier) ---")

    from langchain_openai import ChatOpenAI
    from parsing import _extract_usage, _parse_expert_confidence
    from config import LITELLM_URL, INFERENCE_SERVERS_LIST, URL_MAP
    from services.inference import _endpoint_semaphores
    from cache_aligner import is_anthropic_native, call_anthropic_cached
    from context_budget import adaptive_context_window

    # Micro debates execute inside task groups that may run concurrently. Keep
    # a request-wide reservation counter so max_model_calls cannot silently be
    # multiplied by the number of planner tasks.
    micro_budget_lock = asyncio.Lock()
    micro_calls_reserved = 0

    async def run_single(model_cfg: dict, task_item: dict, t_idx: int, e_idx: int) -> dict:
        model_name = model_cfg["model"]
        if model_cfg.get("_user_conn_url"):
            # User-owned API connection: URL/token pre-resolved, bypass node selection
            url       = model_cfg["_user_conn_url"]
            token     = model_cfg["_user_conn_token"]
            api_type  = model_cfg.get("_user_conn_api_type", "openai")
            endpoint  = model_cfg.get("endpoint", "user-conn")
            semaphore = asyncio.Semaphore(4)
        elif LITELLM_URL:
            # Flange: LiteLLM gateway handles endpoint selection, retries, circuit breaker
            url        = f"{LITELLM_URL}/v1"
            token      = "sk-litellm-internal"
            api_type   = "openai"
            endpoint   = "litellm-proxy"
            semaphore  = asyncio.Semaphore(999)  # LiteLLM manages its own concurrency
        else:
            # Direct access (default): _select_node() selects based on tier/warm/load
            raw_ep     = model_cfg.get("endpoints") or [model_cfg.get("endpoint", "")]
            # Floating mode: if endpoint is empty, search ALL nodes for the model
            if not raw_ep or raw_ep == [""] or raw_ep == [""]:
                raw_ep = [s["name"] for s in INFERENCE_SERVERS_LIST]
                logger.info(f"🌐 Floating mode: searching all {len(raw_ep)} nodes for {model_name}")
            selected   = await _select_node(model_name, raw_ep, user_id=state_.get("user_id", ""))
            endpoint   = selected["name"]
            url        = selected.get("url") or URL_MAP.get(endpoint) or ""
            if not url:
                raise ValueError(
                    f"Expert '{model_name}' on endpoint '{endpoint}' has no URL configured. "
                    "Check the inference server settings in the Admin UI."
                )
            token      = selected.get("token", "ollama")
            api_type   = selected.get("api_type", "ollama")
            semaphore  = _endpoint_semaphores.get(endpoint, asyncio.Semaphore(1))

        # Sovereignty guard: local_only requests must never reach a non-local
        # endpoint, regardless of which branch above resolved `url` (static
        # template category, dynamically materialized expert, or a
        # moderated-debate panel participant — run_moderated_request()
        # dispatches turns through this same function). Checked here, once,
        # right before `url` is used for anything network-facing, rather than
        # only at candidate-selection time.
        from services.sovereignty import assert_egress_allowed, EgressDenied
        try:
            assert_egress_allowed(url, bool(state_.get("local_only_routing")))
        except EgressDenied as _egress_exc:
            PROM_EXPERT_FAILURES.labels(model=model_name, reason="local_only_blocked").inc()
            logger.error("🚫 Expert %s blocked by local_only routing: %s", model_name, _egress_exc)
            await _report(f"🚫 Expert {model_name}: blocked (local_only routing)")
            await _record_stage(state_.get("response_id", ""), "expert", "error", model_name)
            return {"res": f"[{model_name} ERROR]: {_egress_exc}", "model_cat": None}

        from services.node_load import track as _track_node_load
        _queue_wait_t0 = time.monotonic()
        async with semaphore, _track_node_load(endpoint):
            _queue_wait_ms = int((time.monotonic() - _queue_wait_t0) * 1000)
            if _queue_wait_ms >= 1000:
                logger.info(f"⏳ Endpoint queue wait: {endpoint} {_queue_wait_ms} ms ({model_name})")
            task_text  = task_item.get("task", str(task_item))
            cat        = task_item.get("category", "general")
            is_deliberation_turn = bool(task_item.get("_deliberation_turn"))

            # ── Web-Research-Kontext in Task-Text injizieren ───────────────────
            # Kategorien die rein linguistisch / visuell arbeiten profitieren nicht.
            _WEB_SKIP_CATS = {"vision", "creative_writer", "translation"}
            _web_research_raw = (
                task_item.get("_inline_web_research") or  # dynamic-expert inline search
                state_.get("web_research") or ""
            ).strip()
            if _web_research_raw and cat not in _WEB_SKIP_CATS:
                # Budget: 20 % des Expert-Kontextfensters, maximal 6000 Zeichen.
                _ctx_est = int(model_cfg.get("context_window") or 0)
                _web_budget = min(6000, max(1000, int(_ctx_est * 0.20 * 4))) if _ctx_est else 4000
                _web_snippet = _web_research_raw[:_web_budget]
                task_text = (
                    f"[WEB-RECHERCHE-KONTEXT — nutze diese Fakten, halluziniere keine eigenen Quellen]:\n"
                    f"{_web_snippet}\n\n"
                    f"[AUFGABE]:\n{task_text}"
                )

            gpu        = await assign_gpu(endpoint)
            PROM_EXPERT_CALLS.labels(model=model_name, category=cat, node=endpoint).inc()

            mode        = state_.get("mode", "default")
            mode_cfg    = MODES.get(mode, MODES["default"])
            # _base_static_sys: identical for every call with the same category + mode.
            # Used as the cacheable block when the endpoint is Anthropic-native.
            _base_static_sys = (
                _get_expert_prompt(cat, state_.get("user_experts"))
                + mode_cfg["expert_suffix"]
                + _conf_format_for_mode(mode)
            )
            sys_prompt = _base_static_sys
            # _dynamic_sys_parts: per-session or per-query additions — not cached.
            _dynamic_sys_parts: list = []

            # CC profile behavioral directives: session-constant but user-specific → dynamic.
            _behavioral = (state_.get("behavioral_directives") or "").strip()
            if _behavioral:
                _behav_block = f"MANDATORY RESPONSE DIRECTIVES (override all other instructions):\n{_behavioral}"
                sys_prompt = f"{_behav_block}\n\n" + sys_prompt
                _dynamic_sys_parts.append(_behav_block)

            # Agent mode: embed file/code context from the client's system message.
            # When the session has a Tier-3 context index (large system_prompt chunked into
            # ChromaDB), retrieve only the semantically relevant slice for this task instead
            # of truncating to an arbitrary char limit.
            agent_ctx = state_.get("system_prompt", "")
            if agent_ctx and mode in ("agent", "agent_orchestrated"):
                _session_id = state_.get("session_id", "")
                _ctx_snippet = ""
                if _session_id and state.redis_client:
                    try:
                        from services.context_index import (
                            is_context_indexed as _ctx_indexed,
                            retrieve_context_for_task as _ctx_retrieve,
                            FALLBACK_CONTEXT_CHARS as _FALLBACK_CHARS,
                        )
                        if await _ctx_indexed(_session_id, state.redis_client):
                            _ctx_snippet = await _ctx_retrieve(
                                session_id=_session_id,
                                task_text=task_text,
                                redis_client=state.redis_client,
                            )
                    except Exception as _cie:
                        logger.debug("expert: context retrieval failed: %s", _cie)
                if not _ctx_snippet:
                    from services.context_index import FALLBACK_CONTEXT_CHARS as _FALLBACK_CHARS
                    _ctx_snippet = agent_ctx[:_FALLBACK_CHARS]
                if _ctx_snippet:
                    _ctx_block = f"--- USER CODE CONTEXT ---\n{_ctx_snippet}"
                    sys_prompt += f"\n\n{_ctx_block}"
                    _dynamic_sys_parts.append(_ctx_block)

            # Inject correction memory for this category (avoids repeat mistakes)
            if CORRECTION_MEMORY_ENABLED and state.graph_manager is not None:
                try:
                    from graph_rag.corrections import query_corrections as _query_corrections, format_correction_context as _format_correction_context
                    _driver = state.graph_manager.driver if hasattr(state.graph_manager, 'driver') else None
                    _corr = await _query_corrections(_driver, state_.get("input", ""), cat)
                    _corr_ctx = _format_correction_context(_corr)
                    if _corr_ctx:
                        PROM_CORRECTIONS_INJECTED.labels(category=cat).inc(len(_corr))
                        sys_prompt += f"\n\n{_corr_ctx}"
                        _dynamic_sys_parts.append(_corr_ctx)
                except Exception:
                    pass
            # Resolve per-expert context window: template override → Ollama API (cached) → static table
            from context_budget import get_model_ctx_async as _ctx_async, _params_from_name as _pfn
            _expert_ctx_override = int(model_cfg.get("context_window", 0) or 0)
            _expert_ctx_window = await _ctx_async(
                model=model_name,
                base_url=url or "",
                token=token,
                redis_client=state.redis_client,
                override=_expert_ctx_override,
            )
            # Pin context to min(resolved_window, JUDGE_NUM_CTX) for all large local
            # Ollama models (>=25B params). This aligns expert calls with the CC-tool
            # keepalive-loop warmup context (preventing Ollama reload-thrashing)
            # while still respecting a smaller explicit template `context_window`
            # override. Only models whose resolved window EXCEEDS JUDGE_NUM_CTX
            # (e.g. a 262144 GGUF native default) get pinned DOWN to JUDGE_NUM_CTX;
            # an unresolved window (0) falls back to JUDGE_NUM_CTX as a best guess.
            # Skip JUDGE_NUM_CTX cap when template explicitly sets a larger context_window.
            if token == "ollama" and JUDGE_NUM_CTX > 0 and _pfn(model_name) >= 25.0 and _expert_ctx_override <= 0:
                from context_budget import get_model_context_window as _static_ctx
                _safe_ctx = _static_ctx(model_name)
                _pinned_ctx = (
                    JUDGE_NUM_CTX if _expert_ctx_window <= 0
                    else min(_expert_ctx_window, JUDGE_NUM_CTX)
                )
                if _safe_ctx > 0 and _pinned_ctx > _safe_ctx:
                    _pinned_ctx = _safe_ctx
                # Never downgrade a warm model: this pin's own stated purpose is
                # avoiding reload-thrashing, but it assumed every large-model
                # caller (CC-tool path included) warms up at JUDGE_NUM_CTX — no
                # longer true once a template's own context_window (e.g. 262144)
                # differs from the global default. Confirmed live: qwen3.6:35b on
                # N04-RTX reloading from a 262144-ctx OpenCode session down to
                # 32768 for an expert call minutes later, well before its 24h
                # keep_alive — this exact pin was the cause, unconditionally
                # capping down regardless of what was already loaded. Same
                # check/pattern as _invoke_judge_with_retry / _invoke_planner_with_retry
                # (services/inference.py) and the Augmented Tool Path.
                if url:
                    try:
                        async with httpx.AsyncClient(timeout=2.0) as _ps_cl:
                            _ps_r = await _ps_cl.get(
                                f"{url.rstrip('/').removesuffix('/v1')}/api/ps",
                                headers={"Authorization": f"Bearer {token}"},
                            )
                            for _loaded in _ps_r.json().get("models", []):
                                _lname = _loaded.get("name", "").split(":")[0]
                                _ename = model_name.split(":")[0]
                                _loaded_ctx = _loaded.get("context_length", 0)
                                if _lname == _ename and _loaded_ctx >= _pinned_ctx:
                                    logger.info(
                                        "expert: reusing warm model ctx=%d (pin would have requested %d, no reload needed, model=%s)",
                                        _loaded_ctx, _pinned_ctx, model_name,
                                    )
                                    _pinned_ctx = _loaded_ctx
                                    break
                    except Exception:
                        pass  # non-fatal — fall through to the pinned ctx
                if _pinned_ctx != _expert_ctx_window:
                    logger.info(
                        "expert: ctx pinned to min(resolved=%d, JUDGE_NUM_CTX=%d, safe=%d)=%d model=%s",
                        _expert_ctx_window, JUDGE_NUM_CTX, _safe_ctx, _pinned_ctx, model_name,
                    )
                    _expert_ctx_window = _pinned_ctx
            # Clamp to the model's native max context length. Ollama silently caps
            # an oversized num_ctx request to the GGUF's trained context_length
            # (e.g. 32768 for qwen2.5-coder:32b, regardless of a 98304/262144
            # template setting or the JUDGE_NUM_CTX pin above). Without this clamp,
            # _max_input_chars below would assume more context than Ollama actually
            # allocates, risking silent input overflow.
            if token == "ollama" and url and _expert_ctx_window > 0:
                from context_budget import fetch_ollama_native_ctx_max as _native_ctx_max
                _native_max = await _native_ctx_max(model_name, url, token, state.redis_client)
                if _native_max > 0 and _expert_ctx_window > _native_max:
                    logger.info(
                        "expert[%s]: ctx clamped to model native max=%d (requested %d, model=%s)",
                        cat, _native_max, _expert_ctx_window, model_name,
                    )
                    _expert_ctx_window = _native_max
            # Categories that generate large code artifacts need higher token/output limits.
            # Defined here (before first use) to avoid Python's "referenced before assignment"
            # error that occurs when the name appears anywhere in the enclosing scope.
            _CODE_GEN_CATS = {"code_reviewer", "devops_sre", "frontend", "backend", "fullstack"}
            # Derive per-expert output and input limits from context window.
            # Code-generation categories use a much higher output cap so that
            # large HTML/JS/Python files are not truncated mid-function.
            _max_output_cap = (
                MAX_EXPERT_OUTPUT_CHARS_CODE if cat in _CODE_GEN_CATS else MAX_EXPERT_OUTPUT_CHARS
            )
            if is_deliberation_turn:
                _max_output_cap = min(
                    _max_output_cap,
                    deliberation_policy.max_turn_tokens * EXPERT_CHARS_PER_TOKEN,
                )
            _expert_max_output = _max_output_cap
            _max_input_chars = 0
            if _expert_ctx_window > 0:
                # Reserve 1/EXPERT_OUTPUT_DIVISOR of window for output, capped at category cap
                from context_budget import resolve_io_budget as _resolve_io_budget
                _budget = _resolve_io_budget(
                    ctx_tokens=_expert_ctx_window,
                    desired_max_tokens=_max_output_cap // EXPERT_CHARS_PER_TOKEN,
                    chars_per_token=EXPERT_CHARS_PER_TOKEN,
                    min_output_tokens=EXPERT_INPUT_MIN_CHARS // EXPERT_CHARS_PER_TOKEN,
                    min_input_ratio=1 - (1.0 / EXPERT_OUTPUT_DIVISOR),
                )
                if _budget["overflow"]:
                    logger.warning(
                        "expert[%s]: PRE-FLIGHT overflow — ctx=%d too small (model=%s)",
                        cat, _expert_ctx_window, model_name,
                    )
                    PROM_BUDGET_EXCEEDED.labels(
                        user_id=state_.get("session_id", "unknown"), limit_type="expert_preflight"
                    ).inc()
                _expert_max_output = min(_max_output_cap, max(EXPERT_INPUT_MIN_CHARS, _budget["max_output_tokens"] * EXPERT_CHARS_PER_TOKEN))
                # EXPERT_CHARS_PER_TOKEN chars/token conservative estimate for mixed content
                _max_input_chars = max(EXPERT_INPUT_MIN_CHARS, _expert_ctx_window * EXPERT_CHARS_PER_TOKEN)
                _available_task_chars = _max_input_chars - len(sys_prompt)
                if len(task_text) > _available_task_chars > 0:
                    from context_budget import compress_prompt_to_fit
                    task_text = await compress_prompt_to_fit(
                        task_text, _available_task_chars,
                        model=model_name, url=url, token=token
                    )

            # Context-aware history trimming: keep as much history as fits within the
            # model's actual context window after system prompt and task are accounted for.
            # This prevents context flooding without hardcoding a static character limit.
            _local_history: List[Dict] = list(chat_history)
            if _max_input_chars > 0 and _local_history:
                _hist_budget = max(0, _max_input_chars - len(sys_prompt) - len(task_text))
                _hist_total = sum(len(str(m.get("content", ""))) for m in _local_history)
                if _hist_total > _hist_budget > 0:
                    _local_history = _truncate_history(
                        _local_history,
                        max_turns=-1,
                        max_chars=_hist_budget,
                    )
                    logger.info(
                        f"🗜️ Expert [{cat}] history trimmed: {_hist_total} → {_hist_budget} chars "
                        f"(ctx={_expert_ctx_window//1024}K)"
                    )

            logger.info(f"🚀 Expert {t_idx}.{e_idx} GPU#{gpu} [{model_name} / {cat}]")

            # Build messages list: system + history + user turn (with optional image blocks)
            messages: List[Dict] = [{"role": "system", "content": sys_prompt}]
            if _local_history:
                messages.extend(_local_history)

            # Attach images as multimodal content when present (OpenAI image_url format).
            expert_images = state_.get("images") or []
            if expert_images:
                user_content: List[Dict] = [{"type": "text", "text": task_text}]
                for img in expert_images:
                    user_content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{img['media_type']};base64,{img['data']}"},
                    })
                messages.append({"role": "user", "content": user_content})
            else:
                messages.append({"role": "user", "content": task_text})

            await _report(
                f"🚀 Expert [{model_name} / {cat}] GPU#{gpu}\n"
                f"  Task: {task_text}"
                + (f" | ctx={_expert_ctx_window//1024}K" if _expert_ctx_window else "")
            )
            await _record_stage(state_.get("response_id", ""), "expert", "started", f"{model_name}/{cat}")
            await _report(
                f"📤 Expert [{model_name} / {cat}] System-Prompt:\n{sys_prompt}"
            )
            expert_base_url = url.rstrip("/").removesuffix("/v1")
            _expert_node_timeout = float(
                selected.get("timeout", EXPERT_TIMEOUT)
                if not LITELLM_URL and not model_cfg.get("_user_conn_url")
                else EXPERT_TIMEOUT
            )
            _expert_node_timeout = remaining_timeout(
                state_,
                _expert_node_timeout,
                stage=f"expert:{cat}",
            )
            _expert_temp = state_.get("query_temperature")  # None = API default
            # max_tokens must be passed via model_kwargs so LangChain forwards it
            # verbatim to Ollama as "max_tokens". Using the top-level max_tokens
            # parameter causes LangChain to rename it to "max_completion_tokens",
            # which Ollama ignores — resulting in unlimited generation.
            # Code-generation categories need much higher token limits than
            # factual-lookup categories. A full browser game or backend service
            # can easily exceed 16k tokens; 4096 would truncate mid-function.
            # _CODE_GEN_CATS is already defined above (before first use).
            _expert_max_tokens = (
                MAX_EXPERT_TOKENS_CODE if cat in _CODE_GEN_CATS else MAX_EXPERT_TOKENS
            )
            _expert_max_tokens = bounded_output_tokens(
                state_,
                _expert_max_tokens,
                minimum_internal=128,
            )
            if is_deliberation_turn:
                _expert_max_tokens = min(
                    _expert_max_tokens,
                    deliberation_policy.max_turn_tokens,
                )
            _model_kw: dict = {"max_tokens": _expert_max_tokens}
            # extra_body must be a direct ChatOpenAI constructor parameter, NOT inside
            # model_kwargs — LangChain warns and silently drops extra_body from model_kwargs,
            # which causes Ollama to use the Modelfile default (8192) instead of JUDGE_NUM_CTX
            # (32768), triggering a reload of the already-warm model on every expert call.
            # Only send num_ctx when the template explicitly sets context_window.
            # Without an explicit override, rely on OLLAMA_CONTEXT_LENGTH / Modelfile defaults —
            # sending the GGUF training context would downgrade and reload an already-warm model.
            _expert_ctx_for_api = (
                adaptive_context_window(
                    _expert_ctx_window,
                    f"{sys_prompt}\n\n{task_text}",
                    _expert_max_tokens,
                )
                if _expert_ctx_override > 0
                else 0
            )
            # Never downgrade a warm model: adaptive_context_window() scales DOWN
            # to save VRAM on short prompts, but llama-server can't resize a
            # running instance's context -- a smaller request for the SAME model
            # forces a full unload+reload cycle. Same check/pattern as the
            # JUDGE_NUM_CTX pin above and _invoke_judge_with_retry /
            # _invoke_planner_with_retry (services/inference.py). Unlike that
            # pin, this applies whenever a template sets context_window
            # (_expert_ctx_override > 0), not just the >=25B/no-override case.
            if _expert_ctx_for_api > 0 and token == "ollama" and url:
                try:
                    async with httpx.AsyncClient(timeout=2.0) as _ps_cl:
                        _ps_r = await _ps_cl.get(
                            f"{url.rstrip('/').removesuffix('/v1')}/api/ps",
                            headers={"Authorization": f"Bearer {token}"},
                        )
                        for _loaded in _ps_r.json().get("models", []):
                            _lname = _loaded.get("name", "").split(":")[0]
                            _ename = model_name.split(":")[0]
                            _loaded_ctx = _loaded.get("context_length", 0)
                            if _lname == _ename and _loaded_ctx >= _expert_ctx_for_api:
                                logger.info(
                                    "expert: reusing warm model ctx=%d (adaptive would have requested %d, no reload needed, model=%s)",
                                    _loaded_ctx, _expert_ctx_for_api, model_name,
                                )
                                _expert_ctx_for_api = _loaded_ctx
                                break
                except Exception:
                    pass  # non-fatal — fall through to the adaptive ctx
            _extra_body = {"options": {"num_ctx": _expert_ctx_for_api}} if _expert_ctx_for_api > 0 else {}
            if state_.get("enable_habe"):
                from services.inference import _inject_habe_prefix_embeddings
                _opts = _extra_body.setdefault("options", {})
                _inject_habe_prefix_embeddings(_opts, state_)
            if not _extra_body:
                _extra_body = None
            _llm_kwargs: dict = {"model": model_name, "base_url": url, "api_key": token,
                                 "timeout": _expert_node_timeout,
                                 "model_kwargs": _model_kw}
            if _extra_body is not None:
                _llm_kwargs["extra_body"] = _extra_body
            if _expert_temp is not None:
                _llm_kwargs["temperature"] = _expert_temp
            # thinking_mode=False: inject /no_think directive into the last human message.
            # Ollama 0.24 does not support think=false via the OpenAI-compatible API;
            # the /no_think prefix in the user message is the reliable cross-version method.
            # qwen3 respects this directive and skips the <think>…</think> block entirely,
            # saving ~30k tokens and 10+ minutes for factual-lookup categories.
            _thinking_enabled = bool(
                model_cfg.get(
                    "thinking_mode",
                    EXPERT_THINKING_ENABLED,
                )
            )
            if not _thinking_enabled and messages:
                from langchain_core.messages import HumanMessage
                _patched = list(messages)
                for _i in reversed(range(len(_patched))):
                    if hasattr(_patched[_i], "type") and _patched[_i].type == "human":
                        _orig = _patched[_i].content
                        _patched[_i] = HumanMessage(content=f"/no_think\n{_orig}")
                        break
                messages = _patched
            from metrics import PROM_TOKENS
            _expert_call_t0 = time.monotonic()
            try:
                _primary_url = url.rstrip("/")
                # Ollama native /api/chat: the only path that reliably passes options.num_ctx.
                # The OpenAI-compatible /v1/chat/completions endpoint discards the options dict
                # in Ollama ≤0.30.6, causing every cold expert call to load qwen3.6:35b at the
                # Modelfile default (8192) instead of 32768 — evicting the CC tool model and
                # forcing a 90-second reload on the next CC request.
                if api_type == "ollama":
                    _native_msgs = []
                    for _m in messages:
                        _role = ("assistant" if (hasattr(_m, "type") and _m.type == "ai") else
                                 "system"    if (hasattr(_m, "type") and _m.type == "system") else
                                 "user")
                        _native_msgs.append({"role": _role,
                                             "content": _m.content if hasattr(_m, "content") else str(_m)})
                    _ollama_base = url.rstrip("/").removesuffix("/v1")
                    _native_opts: dict = {"num_predict": _expert_max_tokens}
                    if _expert_ctx_for_api > 0:
                        _native_opts["num_ctx"] = _expert_ctx_for_api
                    _native_payload: dict = {
                        "model":      model_name,
                        "messages":   _native_msgs,
                        "stream":     False,
                        "options":    _native_opts,
                        # No explicit keep_alive — respects each Ollama
                        # instance's own server-configured OLLAMA_KEEP_ALIVE
                        # default instead of silently overriding it.
                    }
                    from services.model_capabilities import (
                        enforce_streaming_capability,
                        get_model_caps,
                    )
                    _native_payload["stream"] = enforce_streaming_capability(
                        model_name, bool(_native_payload["stream"])
                    )
                    logger.debug(
                        "model=%s caps=%s stage=expert",
                        model_name,
                        get_model_caps(model_name),
                    )
                    if state_.get("enable_habe"):
                        from services.inference import _inject_habe_prefix_embeddings
                        _inject_habe_prefix_embeddings(_native_payload["options"], state_)
                    if not _thinking_enabled:
                        _native_payload["think"] = False
                    _expert_audit = _audit_create(
                        state_.get("session_id", ""),
                        state_.get("response_id", ""),
                        model_name,
                        f"{_ollama_base}/api/chat",
                        "expert",
                        {
                            **_native_payload,
                            "expert_category": cat,
                        },
                    )
                    try:
                        async with httpx.AsyncClient(timeout=_expert_node_timeout) as _acl:
                            _r = await _acl.post(
                                f"{_ollama_base}/api/chat",
                                json=_native_payload,
                                headers={"Authorization": f"Bearer {token}"},
                            )
                        _r.raise_for_status()
                        _rdata = _r.json()
                        await _audit_complete(
                            _expert_audit,
                            _rdata,
                            _rdata.get("prompt_eval_count"),
                            _rdata.get("eval_count"),
                        )
                    except Exception as exc:
                        await _audit_complete(
                            _expert_audit,
                            {"error": str(exc)},
                            None,
                            None,
                            "error",
                        )
                        raise
                    from types import SimpleNamespace as _NS
                    _native_answer = _ollama_answer_content(_rdata)
                    if not _native_answer.strip():
                        _thinking_chars = len(
                            str(_rdata.get("message", {}).get("thinking", ""))
                        )
                        raise RuntimeError(
                            "expert returned no public answer content"
                            + (
                                f" (thinking-only trace: {_thinking_chars} chars)"
                                if _thinking_chars
                                else ""
                            )
                        )
                    res = _NS(
                        content=_native_answer,
                        usage_metadata={
                            "input_tokens":  _rdata.get("prompt_eval_count", 0),
                            "output_tokens": _rdata.get("eval_count", 0),
                        },
                    )
                    _used_fallback = False
                elif is_anthropic_native(api_type):
                    # Anthropic Messages API with prompt caching on the static system block.
                    _dynamic_sys = "\n\n".join(_dynamic_sys_parts)
                    _anthr_msgs  = [m for m in messages if (
                        m.get("role") != "system"
                        if isinstance(m, dict)
                        else getattr(m, "type", "") not in ("system",)
                    )]
                    _expert_audit = _audit_create(
                        state_.get("session_id", ""),
                        state_.get("response_id", ""),
                        model_name,
                        url,
                        "expert",
                        {
                            "messages": _anthr_msgs,
                            "system": [_base_static_sys, _dynamic_sys],
                            "expert_category": cat,
                        },
                    )
                    try:
                        _anthr_text, _anthr_usage = await call_anthropic_cached(
                            url=url,
                            token=token,
                            model=model_name,
                            messages_oai=_anthr_msgs,
                            static_system=_base_static_sys,
                            dynamic_system=_dynamic_sys,
                            max_tokens=_expert_max_tokens,
                            timeout=_expert_node_timeout,
                        )
                        await _audit_complete(
                            _expert_audit,
                            {"content": _anthr_text},
                            _anthr_usage.get("prompt_tokens"),
                            _anthr_usage.get("completion_tokens"),
                        )
                    except Exception as exc:
                        await _audit_complete(
                            _expert_audit,
                            {"error": str(exc)},
                            None,
                            None,
                            "error",
                        )
                        raise
                    from types import SimpleNamespace as _NS
                    res = _NS(
                        content=_anthr_text,
                        usage_metadata={
                            "input_tokens":  (
                                _anthr_usage["prompt_tokens"]
                                + _anthr_usage.get("cache_creation_input_tokens", 0)
                                + _anthr_usage.get("cache_read_input_tokens", 0)
                            ),
                            "output_tokens": _anthr_usage["completion_tokens"],
                        },
                    )
                    _used_fallback = False
                else:
                    llm = ChatOpenAI(**_llm_kwargs)
                    res, _used_fallback = await _invoke_llm_with_fallback(
                        llm, _primary_url, messages,
                        timeout=_expert_node_timeout,
                        label=f"Expert[{cat}]",
                        audit_context=state_,
                        audit_stage="expert",
                        model=model_name,
                    )
                # Interactive-pipeline latency was never recorded before —
                # only the Agent Tool Path (services/pipeline/chat.py) fed
                # moe:latency:{node}, so _select_node's future latency-aware
                # weighting would otherwise start blind for the dominant
                # planner→expert→judge traffic path. Common to all three
                # branches above (ollama-native/anthropic-native/openai).
                # Skipped when _used_fallback: the call actually ran against
                # _FALLBACK_NODE (services/inference.py), not `url` — recording
                # it under `url` would misattribute a degraded-primary latency
                # sample to a node that never handled this request.
                if not _used_fallback:
                    asyncio.create_task(_record_node_latency(
                        url, model_name, (time.monotonic() - _expert_call_t0) * 1000,
                    ))
                if _used_fallback:
                    await _report(f"⚠️ Expert [{cat}]: used local fallback (primary endpoint degraded)")
                usage = _extract_usage(res)
                # Strip thinking traces before truncation so the actual answer
                # is captured instead of the thinking preamble. Thinking-mode
                # models (qwen3.6:35b) output <think>...</think> first which
                # would otherwise fill the entire _expert_max_output window.
                import re as _re
                _raw_content = _re.sub(
                    r'<think>.*?</think>', '', res.content, flags=_re.DOTALL
                ).strip()
                content = _raw_content[:_expert_max_output]
                if len(_raw_content) > _expert_max_output:
                    content += "\n[…truncated]"
                await _report(f"✅ Expert [{model_name} / {cat}]:\n{content}\n---")
                await _record_stage(state_.get("response_id", ""), "expert", "done", f"{model_name}/{cat}")
                # Token metrics
                _uid = state_.get("user_id", "anon")
                PROM_TOKENS.labels(model=model_name, token_type="prompt",      node=endpoint, user_id=_uid).inc(usage.get("prompt_tokens", 0))
                PROM_TOKENS.labels(model=model_name, token_type="completion",  node=endpoint, user_id=_uid).inc(usage.get("completion_tokens", 0))
                # Confidence automatically as performance signal → no waiting for user feedback needed
                conf = _parse_expert_confidence(content)
                await _report(
                    f"  → [{model_name}/{cat}] Confidence: {conf or '?'} | "
                    f"{usage.get('prompt_tokens', 0)}→{usage.get('completion_tokens', 0)} tok"
                )
                # Structured per-call line (greppable for load-test analysis): which
                # tier/node handled this category, with token cost and confidence.
                logger.info(
                    "📊 expert_call model=%s node=%s tier=%s cat=%s tokens=%s->%s conf=%s%s",
                    model_name, endpoint, model_cfg.get("_tier", "?"), cat,
                    usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
                    conf or "unknown",
                    " fallback" if _used_fallback else "",
                )
                PROM_CONFIDENCE.labels(level=conf or "unknown", category=cat).inc()
                # Thompson-sampling reward is no longer recorded here from the
                # expert's *self-reported* confidence — a confidently-wrong answer
                # would be rewarded. The outcome is recorded later in merger_node
                # (graph/synthesis.py), where the judge's verdict is available:
                # a category the judge had to refine counts as negative. Self-
                # confidence is used only as a fallback when refinement is disabled.
                # VRAM management is left entirely to Ollama's own automatic
                # LRU eviction (evicts the least-recently-used loaded model
                # only when a newly requested model genuinely doesn't fit)
                # plus each endpoint's own OLLAMA_KEEP_ALIVE/keep_alive
                # setting. This code used to proactively unload the expert
                # model here "just in case" after every single invocation,
                # regardless of whether any other model actually needed the
                # freed VRAM — which silently overrode a longer keep_alive
                # (e.g. 4h) with an immediate forced unload on every turn,
                # including mid-conversation gaps of an unrelated long-lived
                # agentic tool session sharing the same model+node. Removed;
                # see git history for the old _can_coexist_on_node /
                # _is_model_busy_elsewhere-gated proactive-unload logic if
                # reintroducing anything here for genuinely VRAM-constrained
                # nodes.
                res_prefix = f"ENSEMBLE: {model_name.upper()}" if model_cfg.get("forced") else model_name.upper()
                result = {"res": f"[{res_prefix} / {cat}]: {content}", "model_cat": f"{model_name}::{cat}", **usage}
                # User-owned connection tokens are tracked separately for budget exclusion.
                if model_cfg.get("_user_conn_url"):
                    result["user_conn_prompt_tokens"]     = usage.get("prompt_tokens", 0)
                    result["user_conn_completion_tokens"] = usage.get("completion_tokens", 0)
                    result["prompt_tokens"]     = 0
                    result["completion_tokens"] = 0
                return result
            except RequestDeadlineExceeded:
                raise
            except Exception as e:
                err = str(e)
                # Distinguish real GPU errors (VRAM/CUDA) from network/timeout errors
                is_vram = any(x in err.lower() for x in
                              ("cudamalloc", "out of memory", "oom", "transfer encoding",
                               "not enough data", "cuda error"))
                if is_vram:
                    PROM_EXPERT_FAILURES.labels(model=model_name, reason="vram").inc()
                    logger.error(f"❌ VRAM/HTTP error GPU#{gpu} {model_name}: {e}")
                    await _report(f"❌ Expert {model_name}: GPU/HTTP error")
                    await _record_stage(state_.get("response_id", ""), "expert", "error", model_name)
                    return {"res": f"[{model_name} ERROR]: VRAM/HTTP", "model_cat": None}
                PROM_EXPERT_FAILURES.labels(model=model_name, reason="error").inc()
                logger.error(f"❌ Expert {model_name}: {e}")
                await _report(f"❌ Expert {model_name}: error")
                await _record_stage(state_.get("response_id", ""), "expert", "error", model_name)
                return {"res": f"[{model_name} ERROR]: {e}", "model_cat": None}

    async def run_task(i: int, task: dict) -> List[dict]:
        """Two-Tier-Logik + Forced-Parallel-Ensemble.

        Forced models always run — independent of score/tier — simultaneously with T1.
        Intended for ensemble approaches: evaluate different providers/training data in parallel.
        """
        cat              = task.get("category", "general")

        # ── Scope Guard (TASK-17) ──────────────────────────────────────────────
        try:
            from services.scope_guard import check_scope
            _scope_violation = check_scope(task, cat)
            if _scope_violation:
                logger.warning("🚫 Scope Guard blocked expert task: %s", _scope_violation.message)
                return []
        except Exception as _sg_e:
            logger.debug("scope_guard: check failed (fail-open): %s", _sg_e)

        effective_experts = state_.get("user_experts") or EXPERTS

        # ── Dynamic Expert Materialization ─────────────────────────────────────
        if cat == "dynamic" and not effective_experts.get("dynamic"):
            try:
                from services.expert_builder import build_expert_for_task as _build_dyn
                _dyn_models, _dyn_web = await _build_dyn(
                    task,
                    local_only=bool(state_.get("local_only_routing")),
                    user_connections=state_.get("user_connections"),
                    existing_web_research=state_.get("web_research") or "",
                )
                if _dyn_models:
                    effective_experts = dict(effective_experts)
                    effective_experts["dynamic"] = _dyn_models
                    # Inline-Recherche des expert_builders in den Task-Dict einfalten,
                    # damit run_single() sie via task_item["_inline_web_research"] sieht.
                    if _dyn_web and not state_.get("web_research"):
                        task = dict(task)
                        task["_inline_web_research"] = _dyn_web
                else:
                    logger.warning("⚠️ expert_builder: no model — falling back to 'general'")
                    cat = "general"
            except Exception as _dyn_e:
                logger.warning("⚠️ expert_builder failed (%s) — falling back to 'general'", _dyn_e)
                cat = "general"

        all_experts = [e for e in effective_experts.get(cat, effective_experts.get("general", EXPERTS.get(cat, EXPERTS.get("general", [])))) if e.get("enabled", True)]
        if state_.get("local_only_routing"):
            # Defense-in-depth: run_single()'s egress guard blocks any cloud
            # dispatch anyway, but filtering here avoids burning a forced/
            # parallel-ensemble call slot on a candidate that is guaranteed
            # to be rejected, and lets a local sibling candidate take its
            # place when one exists for this category.
            #
            # `endpoint` is a symbolic node name (e.g. "openrouterai"), not a
            # URL — resolve it through URL_MAP first, exactly like run_single()
            # does at dispatch time. _is_local_url() treats any dot-free,
            # unresolved string as local (internal short hostname), so
            # checking the raw endpoint name directly would silently accept
            # every symbolically-named cloud gateway (the TASK-51 incident's
            # exact shape: endpoint="openrouterai").
            from services.dynamic_router import _is_local_url
            _local_experts = [
                e for e in all_experts
                if _is_local_url(URL_MAP.get(e.get("endpoint", ""), e.get("endpoint", "")))
            ]
            if _local_experts:
                all_experts = _local_experts

        forced_experts = [e for e in all_experts if e.get("forced", False)]
        normal_experts = [e for e in all_experts if not e.get("forced", False)]

        scored = []
        for e in normal_experts:
            score = await _get_expert_score(e["model"], cat, query_embedding=state_.get("query_embedding"))
            scored.append((score, e))
        scored.sort(key=lambda x: -x[0])

        micro_slot_reserved = False
        if (
            deliberation_capacity.active
            and deliberation_capacity.selected_mode == "micro"
            and len(scored) >= 2
        ):
            nonlocal micro_calls_reserved
            async with micro_budget_lock:
                if (
                    micro_calls_reserved + 3
                    <= deliberation_capacity.model_call_budget
                ):
                    micro_calls_reserved += 3
                    micro_slot_reserved = True
            if not micro_slot_reserved:
                local_deliberation_events.append({
                    "event": "micro_debate_budget_exhausted",
                    "category": cat,
                    "model_calls_reserved": micro_calls_reserved,
                    "model_call_budget": deliberation_capacity.model_call_budget,
                })
                if deliberation_policy.fallback == "fail":
                    raise DeliberationExecutionError(
                        f"micro deliberation call budget exhausted before {cat}"
                    )

        # Compatibility micro-debate, now governed by the frozen per-template
        # policy and the shared request budget rather than the global flag alone.
        if (
            deliberation_capacity.active
            and deliberation_capacity.selected_mode == "micro"
            and len(scored) >= 2
            and micro_slot_reserved
        ):
            proponent = scored[0][1]
            skeptic = scored[1][1]
            local_deliberation_events.append({
                "event": "micro_debate_started",
                "category": cat,
                "agents": 2,
                "rounds": 1,
            })
            logger.info(f"⚖️ Starting J-MoE Debate in category '{cat}' between Proponent ({proponent['model']}) and Skeptic ({skeptic['model']})")
            await _report(f"⚖️ Debate [{cat}]: Proponent ({proponent['model']}) vs Skeptic ({skeptic['model']})")
            
            # 1. Proponent Initial Answer
            proponent_task = dict(task)
            proponent_task["_deliberation_turn"] = True
            prop_res = await run_single(proponent, proponent_task, i + 1, 1)
            prop_ans = prop_res.get("res", "")
            
            # 2. Skeptic Critique
            skeptic_task = task.copy()
            skeptic_task["_deliberation_turn"] = True
            # run_single consumes the canonical planner field ``task``. The
            # former ``input`` assignment was never read, so the skeptic saw
            # only the original user task and not the proponent answer it was
            # supposed to critique.
            skeptic_task["task"] = (
                f"[User Query]\n{task.get('task', '')}\n\n"
                f"[Proponent Initial Answer]\n{prop_ans}\n\n"
                f"[Task]\n"
                f"You are an adversarial Skeptic/Opponent in a formal debate. Critique the Proponent's answer above. "
                f"Identify logical fallacies, errors, omissions, or assumptions. Be critical, objective, and precise."
            )
            sk_res = await run_single(skeptic, skeptic_task, i + 1, 2)
            sk_critique = sk_res.get("res", "")
            
            # 3. Proponent Rebuttal & Refined Answer
            rebuttal_task = task.copy()
            rebuttal_task["_deliberation_turn"] = True
            rebuttal_task["task"] = (
                f"[User Query]\n{task.get('task', '')}\n\n"
                f"[Your Initial Answer]\n{prop_ans}\n\n"
                f"[Skeptic Critique]\n{sk_critique}\n\n"
                f"[Task]\n"
                f"You are the Proponent. Review the Skeptic's critique. Defend your correct claims, accept "
                f"valid corrections, and output a refined, high-quality final response."
            )
            rebuttal_res = await run_single(proponent, rebuttal_task, i + 1, 3)
            rebutted_ans = rebuttal_res.get("res", "")
            
            debate_transcript = (
                f"[DEBATE] Proponent: {proponent['model']} | Skeptic: {skeptic['model']}\n"
                f"### Proponent Initial Answer:\n{prop_ans}\n\n"
                f"### Skeptic Critique:\n{sk_critique}\n\n"
                f"### Proponent Final Rebutted Answer:\n{rebutted_ans}"
            )
            
            # Record paraconsistent conflicts
            from parsing import _improvement_ratio
            div_score = _improvement_ratio(prop_ans, sk_critique)
            if div_score >= 0.35:
                conflict_entry = {
                    "category": cat,
                    "proposition_a": prop_ans[:600],
                    "proposition_b": sk_critique[:600],
                    "divergence_score": round(div_score, 3),
                    "resolution": "pending",
                    "resolved_by": ""
                }
                local_conflicts.append(conflict_entry)
                logger.info(f"⚖️ Registered paraconsistent conflict in J-MoE debate: div_score={div_score:.2f}")
            
            final_res = {
                "res": f"[{cat}]: {debate_transcript}",
                "model_cat": f"{proponent['model']}::Debate::{cat}",
                "prompt_tokens": prop_res.get("prompt_tokens", 0) + sk_res.get("prompt_tokens", 0) + rebuttal_res.get("prompt_tokens", 0),
                "completion_tokens": prop_res.get("completion_tokens", 0) + sk_res.get("completion_tokens", 0) + rebuttal_res.get("completion_tokens", 0),
                "user_conn_prompt_tokens": prop_res.get("user_conn_prompt_tokens", 0) + sk_res.get("user_conn_prompt_tokens", 0) + rebuttal_res.get("user_conn_prompt_tokens", 0),
                "user_conn_completion_tokens": prop_res.get("user_conn_completion_tokens", 0) + sk_res.get("user_conn_completion_tokens", 0) + rebuttal_res.get("user_conn_completion_tokens", 0),
            }
            return [final_res]

        if (
            deliberation_capacity.active
            and deliberation_capacity.selected_mode == "micro"
            and len(scored) < 2
        ):
            local_deliberation_events.append({
                "event": "micro_debate_unavailable",
                "category": cat,
                "reason": "fewer_than_two_models",
            })
            if deliberation_policy.fallback == "fail":
                raise DeliberationExecutionError(
                    f"micro deliberation for {cat} requires two models"
                )

        tier1 = [(s, e) for s, e in scored if e.get("_tier", 1) == 1 and s >= EXPERT_MIN_SCORE]
        tier2 = [(s, e) for s, e in scored if e.get("_tier", 2) == 2 and s >= EXPERT_MIN_SCORE]

        # GPU-load-aware tie-break: among candidates with an IDENTICAL score
        # (true ties only — never reorders by score), prefer the endpoint with
        # fewer in-flight requests. Flag: MOE_LOAD_AWARE_ROUTING=1.
        if os.getenv("MOE_LOAD_AWARE_ROUTING", "0") == "1":
            from services.node_load import inflight as _nl_inflight

            def _tiebreak_by_load(pairs: list) -> list:
                out, i = [], 0
                while i < len(pairs):
                    j = i
                    while j < len(pairs) and pairs[j][0] == pairs[i][0]:
                        j += 1
                    group = pairs[i:j]
                    if len(group) > 1:
                        group = sorted(group, key=lambda se: _nl_inflight(se[1].get("endpoint", "")))
                    out.extend(group)
                    i = j
                return out

            tier1 = _tiebreak_by_load(tier1)
            tier2 = _tiebreak_by_load(tier2)

        # If no T1 results available, treat all normal results as T1
        if not tier1:
            tier1, tier2 = tier2, []

        # Cost-tier enforcement: trivial tasks use at most one T1 expert to reduce
        # token consumption. T2 is normally skipped — but when the low-confidence
        # rescue is enabled it stays available as a fallback that only fires if the
        # single T1 answer comes back low-confidence/empty (see escalation below).
        cost_tier_t1 = bool(state_.get("force_tier1")) and bool(tier1)
        if cost_tier_t1:
            tier1 = tier1[:1]  # only the top-scored T1 expert
            if TRIVIAL_LOW_CONF_RESCUE_ENABLED:
                tier2 = tier2[:1]  # rescue uses at most the single best T2 expert
            else:
                tier2 = []         # strict mode: T2 never runs for trivial cost-tier tasks

        task_results: List[dict] = []

        # Forced + T1 start in parallel in one batch
        parallel_batch = (
            [run_single(e, task, i + 1, j + 1) for j, e in enumerate(forced_experts)] +
            [run_single(e, task, i + 1, len(forced_experts) + j + 1) for j, (_, e) in enumerate(tier1)]
        )

        if forced_experts:
            forced_names = ", ".join(e["model"] for e in forced_experts)
            await _report(f"🔀 Forced-Ensemble [{cat}]: {forced_names}")
        if tier1:
            t1_names = ", ".join(e["model"] for _, e in tier1)
            await _report(f"⚡ T1 [{cat}]: {t1_names}")

        if parallel_batch:
            combined = await asyncio.gather(*parallel_batch)
            n_forced      = len(forced_experts)
            forced_results = list(combined[:n_forced])
            t1_results     = list(combined[n_forced:])
            task_results.extend(forced_results)
            task_results.extend(t1_results)

            if t1_results:
                t1_confs = [_parse_expert_confidence(r.get("res", "")) for r in t1_results if r.get("res")]
                decision = _tier2_escalation_decision(cost_tier_t1, t1_confs, bool(tier2))
                if decision != "t2_escalated":
                    PROM_TIER_ESCALATION.labels(category=cat, decision=decision).inc()
                    if decision == "t1_high_skip":
                        logger.info(f"✅ T1 [{cat}]: high confidence — T2 skipped")
                        await _report(f"✅ T1 [{cat}]: high confidence — T2 skipped")
                    elif decision == "t1_cost_kept":
                        logger.info(f"💰 T1 [{cat}]: cost-tier kept (medium conf) — T2 rescue not needed")
                    return task_results
            elif not tier2:
                return task_results

        if tier2:
            PROM_TIER_ESCALATION.labels(category=cat, decision="t2_escalated").inc()
            t2_names = ", ".join(e["model"] for _, e in tier2)
            _why = "low-confidence T1 rescue" if cost_tier_t1 else "no T1 high confidence"
            logger.info(f"🔬 T2 [{cat}]: {t2_names} — escalated ({_why})")
            await _report(f"🔬 T2 [{cat}]: {t2_names} (T1 insufficient)")
            t2_results = await asyncio.gather(
                *[run_single(e, task, i + 1, len(forced_experts) + len(tier1) + j + 1)
                  for j, (_, e) in enumerate(tier2)]
            )
            task_results.extend(t2_results)

        return task_results

    async def run_moderated_request() -> Optional[List[dict]]:
        """Run one request-level moderated debate across planned domains."""

        candidate_pool: list[tuple[float, str, dict]] = []
        seen_candidates: set[tuple[str, str, str]] = set()
        specialist_categories: list[str] = []
        effective_experts = state_.get("user_experts") or EXPERTS
        local_only_routing = bool(state_.get("local_only_routing"))
        if local_only_routing:
            from services.dynamic_router import _is_local_url

        for _, planned_task in expert_tasks:
            category = str(planned_task.get("category") or "general")
            if category not in specialist_categories:
                specialist_categories.append(category)
            category_models = effective_experts.get(category)
            if not isinstance(category_models, list) or not category_models:
                category_models = effective_experts.get("general", EXPERTS.get("general", []))
            for model_cfg in category_models or []:
                if not isinstance(model_cfg, dict) or not model_cfg.get("enabled", True):
                    continue
                model_name = str(model_cfg.get("model") or "")
                if not model_name:
                    continue
                _ep_name = str(model_cfg.get("endpoint") or "")
                if local_only_routing and not _is_local_url(URL_MAP.get(_ep_name, _ep_name)):
                    # Defense-in-depth: run_single() (called below for every
                    # debate turn) blocks any cloud dispatch anyway, but
                    # excluding these candidates here keeps the debate panel
                    # itself restricted to models that can actually run,
                    # instead of reserving a role/round budget slot for a
                    # participant guaranteed to fail at dispatch.
                    continue
                key = (category, model_name, str(model_cfg.get("endpoint") or ""))
                if key in seen_candidates:
                    continue
                seen_candidates.add(key)
                score = (
                    2.0
                    if model_cfg.get("forced")
                    else await _get_expert_score(model_name, category, query_embedding=state_.get("query_embedding"))
                )
                candidate_pool.append((score, category, model_cfg))

        candidate_pool.sort(
            key=lambda item: (-item[0], item[1], str(item[2].get("model") or ""))
        )
        if not candidate_pool:
            local_deliberation_events.append({
                "event": "moderated_debate_unavailable",
                "reason": "no_models",
            })
            if deliberation_policy.fallback == "fail":
                raise DeliberationExecutionError(
                    "required moderated deliberation has no available models"
                )
            return None

        roles = build_role_roster(
            deliberation_capacity.max_agents,
            specialist_categories,
        )
        participants: list[dict[str, Any]] = []
        generic_index = 0
        for role in roles:
            selected: tuple[float, str, dict] | None = None
            if role.specialist_category:
                selected = next(
                    (
                        candidate
                        for candidate in candidate_pool
                        if candidate[1] == role.specialist_category
                    ),
                    None,
                )
            if selected is None:
                selected = candidate_pool[generic_index % len(candidate_pool)]
                generic_index += 1
            participants.append({
                "role": role,
                "category": selected[1],
                "model_cfg": selected[2],
            })

        active_participants = participants[:deliberation_capacity.initial_agents]
        reserve_participants = participants[deliberation_capacity.initial_agents:]
        distinct_selected_models = {
            (
                str(participant["model_cfg"].get("model") or ""),
                str(participant["model_cfg"].get("endpoint") or ""),
            )
            for participant in active_participants
        }
        if len(distinct_selected_models) < len(active_participants):
            local_deliberation_events.append({
                "event": "model_diversity_degraded",
                "agents": len(active_participants),
                "distinct_models": len(distinct_selected_models),
            })

        plan_summary = json.dumps(
            [
                {
                    "id": task.get("id", f"task-{index + 1}"),
                    "category": task.get("category", "general"),
                    "task": str(task.get("task") or "")[:800],
                    "depends_on": task.get("depends_on") or [],
                }
                for index, (_, task) in enumerate(expert_tasks)
            ],
            ensure_ascii=False,
        )[:8000]
        turns: list[dict[str, Any]] = []
        moderator_records: list[ModeratorDecision] = []
        model_calls = 0
        usage_totals = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "user_conn_prompt_tokens": 0,
            "user_conn_completion_tokens": 0,
        }

        def add_usage(result: dict[str, Any]) -> None:
            for key in usage_totals:
                usage_totals[key] += int(result.get(key, 0) or 0)

        correction = ""
        round_number = 1
        round_limit = deliberation_capacity.initial_rounds
        activated_reserve_rounds = 0
        activated_reserve_agents = 0
        loop_stopped = False
        early_consensus = False
        last_decision: ModeratorDecision | None = None

        local_deliberation_events.append({
            "event": "moderated_debate_started",
            "agents": len(active_participants),
            "rounds": round_limit,
            "model_call_budget": deliberation_capacity.model_call_budget,
        })
        await _report(
            "⚖️ Moderated deliberation: "
            f"{len(active_participants)} agents, {round_limit} initial rounds, "
            f"{deliberation_capacity.reserve_agents}/{deliberation_capacity.reserve_rounds} reserve"
        )

        while round_number <= round_limit:
            round_failures = 0
            max_repetition = 0.0
            for participant_index, participant in enumerate(list(active_participants), start=1):
                if model_calls >= deliberation_capacity.model_call_budget:
                    local_deliberation_events.append({
                        "event": "model_call_budget_exhausted",
                        "round": round_number,
                        "model_calls": model_calls,
                    })
                    break
                transcript = compact_transcript(turns)
                role = participant["role"]
                role_task = {
                    "id": f"deliberation-r{round_number}-{participant_index}",
                    "category": participant["category"],
                    "_deliberation_turn": True,
                    "task": build_turn_task(
                        user_query=str(state_.get("input") or ""),
                        plan_summary=plan_summary,
                        role=role,
                        round_number=round_number,
                        transcript=transcript,
                        correction=correction,
                    ),
                }
                result = await run_single(
                    participant["model_cfg"],
                    role_task,
                    round_number,
                    participant_index,
                )
                model_calls += 1
                add_usage(result)
                content = str(result.get("res") or "").strip()
                successful = bool(result.get("model_cat")) and " ERROR]" not in content
                if not successful:
                    round_failures += 1
                turns.append({
                    "round": round_number,
                    "role": role.role_id,
                    "category": participant["category"],
                    "model": str(participant["model_cfg"].get("model") or ""),
                    "content": content,
                    "status": "completed" if successful else "failed",
                })
                max_repetition = max(max_repetition, max_role_repetition(turns))
                local_deliberation_events.append({
                    "event": "turn_completed" if successful else "turn_failed",
                    "round": round_number,
                    "role": role.role_id,
                    "category": participant["category"],
                })

            if max_repetition >= deliberation_policy.repetition_threshold:
                loop_stopped = True
                local_deliberation_events.append({
                    "event": "repetition_stopped",
                    "round": round_number,
                    "score": round(max_repetition, 3),
                })
                break

            decision: ModeratorDecision | None = None
            moderation_due = (
                round_number % deliberation_policy.moderator_interval == 0
                or round_number >= round_limit
            )
            if (
                turns
                and moderation_due
                and model_calls < deliberation_capacity.model_call_budget
            ):
                moderator_prompt = build_moderator_prompt(
                    user_query=str(state_.get("input") or ""),
                    transcript=compact_transcript(turns, max_chars=20000),
                    convergence_threshold=deliberation_policy.convergence_threshold,
                )
                parse_failure = ""
                for attempt in range(2):
                    if model_calls >= deliberation_capacity.model_call_budget:
                        break
                    prompt = moderator_prompt
                    if attempt and parse_failure:
                        prompt += (
                            "\n\nYour previous response violated the JSON contract. "
                            f"Repair it once. Validation error: {parse_failure[:500]}"
                        )
                    try:
                        # Reserve before dispatch. Failed/invalid moderator calls
                        # still consume request capacity and must not enable an
                        # unbounded retry path.
                        model_calls += 1
                        moderator_result = await _invoke_judge_with_retry(
                            state_, prompt, max_retries=1, temperature=0.0
                        )
                        moderator_usage = _extract_usage(moderator_result)
                        add_usage(moderator_usage)
                        decision = parse_moderator_decision(
                            str(getattr(moderator_result, "content", "") or "")
                        )
                        break
                    except RequestDeadlineExceeded:
                        raise
                    except asyncio.CancelledError:
                        raise
                    except DeliberationExecutionError as exc:
                        parse_failure = str(exc)
                        local_deliberation_events.append({
                            "event": "moderator_contract_invalid",
                            "round": round_number,
                            "attempt": attempt + 1,
                        })
                    except Exception as exc:
                        logger.warning("deliberation moderator failed: %s", exc)
                        parse_failure = "moderator_call_failed"
                        local_deliberation_events.append({
                            "event": "moderator_failed",
                            "round": round_number,
                        })
                        break

                if decision is None:
                    if deliberation_policy.fallback == "fail":
                        raise DeliberationExecutionError(
                            parse_failure or "moderator unavailable"
                        )
                    decision = ModeratorDecision(
                        status="CONTINUE",
                        reason="Moderator unavailable; preserving explicit unresolved state.",
                        correction="",
                        direction="Proceed to bounded synthesis with limitations.",
                        convergence_score=0.0,
                        unresolved_conflicts=1,
                        missing_perspectives=[],
                    )

                moderator_records.append(decision)
                last_decision = decision
                local_deliberation_events.append({
                    "event": "moderator_decision",
                    "round": round_number,
                    "status": decision.status,
                    "convergence_score": decision.convergence_score,
                    "unresolved_conflicts": decision.unresolved_conflicts,
                    "missing_perspectives": len(decision.missing_perspectives),
                })
                correction = decision.correction if decision.status == "CORRECTION" else ""

                if (
                    decision.status == "CONSENSUS"
                    and decision.convergence_score >= deliberation_policy.convergence_threshold
                    and decision.unresolved_conflicts == 0
                    and round_number >= deliberation_policy.min_rounds
                ):
                    early_consensus = True
                    local_deliberation_events.append({
                        "event": "early_consensus",
                        "round": round_number,
                        "convergence_score": decision.convergence_score,
                    })
                    break

            if model_calls >= deliberation_capacity.model_call_budget:
                break

            needs_extension = bool(
                round_failures
                or (
                    decision
                    and (
                        decision.status in {"CONTINUE", "CORRECTION"}
                        and (
                            decision.unresolved_conflicts > 0
                            or bool(decision.correction)
                            or bool(decision.missing_perspectives)
                        )
                    )
                )
            )
            if round_number >= round_limit:
                can_extend = (
                    needs_extension
                    and activated_reserve_rounds < deliberation_capacity.reserve_rounds
                )
                if not can_extend:
                    break

                if reserve_participants and (
                    round_failures or (decision and decision.missing_perspectives)
                ):
                    requested = max(
                        1,
                        round_failures,
                        len(decision.missing_perspectives) if decision else 0,
                    )
                    available = deliberation_capacity.reserve_agents - activated_reserve_agents
                    activate_count = min(requested, available, len(reserve_participants))
                    if activate_count:
                        active_participants.extend(reserve_participants[:activate_count])
                        del reserve_participants[:activate_count]
                        activated_reserve_agents += activate_count
                        local_deliberation_events.append({
                            "event": "reserve_agents_activated",
                            "round": round_number + 1,
                            "count": activate_count,
                            "reason": (
                                "turn_failure" if round_failures else "missing_perspective"
                            ),
                        })

                activated_reserve_rounds += 1
                round_limit += 1
                local_deliberation_events.append({
                    "event": "reserve_round_activated",
                    "round": round_limit,
                    "reason": (
                        "turn_failure"
                        if round_failures
                        else (decision.status.lower() if decision else "unresolved")
                    ),
                })
            round_number += 1

        successful_turns = [turn for turn in turns if turn["status"] == "completed"]
        if not successful_turns:
            if deliberation_policy.fallback == "fail":
                raise DeliberationExecutionError(
                    "moderated deliberation produced no successful turns"
                )
            return None

        if last_decision and last_decision.unresolved_conflicts > 0 and len(successful_turns) >= 2:
            from parsing import _improvement_ratio

            left = successful_turns[0]["content"]
            right = successful_turns[1]["content"]
            local_conflicts.append({
                "category": "deliberation",
                "proposition_a": left[:600],
                "proposition_b": right[:600],
                "divergence_score": round(_improvement_ratio(left, right), 3),
                "resolution": "pending",
                "resolved_by": "moderator_unresolved",
            })

        transcript = compact_transcript(
            successful_turns,
            max_chars=32000,
            recent_full_turns=8,
        )
        moderator_summary = "\n".join(
            (
                f"- Round {index}: {decision.status}; convergence="
                f"{decision.convergence_score:.2f}; unresolved="
                f"{decision.unresolved_conflicts}; reason={decision.reason}"
            )
            for index, decision in enumerate(moderator_records, start=1)
        ) or "- No valid moderator decision; limitations remain explicit."
        model_names = sorted({turn["model"] for turn in successful_turns if turn["model"]})
        local_deliberation_events.append({
            "event": "moderated_debate_completed",
            "turns": len(successful_turns),
            "rounds": max((int(turn["round"]) for turn in successful_turns), default=0),
            "agents": len({turn["role"] for turn in successful_turns}),
            "model_calls": model_calls,
            "early_consensus": early_consensus,
            "loop_stopped": loop_stopped,
            "reserve_agents_used": activated_reserve_agents,
            "reserve_rounds_used": activated_reserve_rounds,
        })
        return [{
            "res": (
                "[MODERATED DELIBERATION]\n"
                f"Agents: {len({turn['role'] for turn in successful_turns})}; "
                f"Rounds: {max((int(turn['round']) for turn in successful_turns), default=0)}; "
                f"Model calls: {model_calls}\n\n"
                f"{transcript}\n\n[MODERATOR DECISIONS]\n{moderator_summary}"
            ),
            "model_cat": f"{'+'.join(model_names)}::ModeratedDeliberation",
            **usage_totals,
        }]

    if (
        deliberation_capacity.active
        and deliberation_capacity.selected_mode == "moderated"
    ):
        moderated_results = await run_moderated_request()
        if moderated_results is not None:
            used = [
                result["model_cat"]
                for result in moderated_results
                if result.get("model_cat")
            ]
            completed_models = [model for model in used if model]
            deliberation_task_events = [
                {
                    "task_id": task.get("id", f"task{index}"),
                    "category": task.get("category", "general"),
                    "status": "completed" if completed_models else "failed",
                    "executor": "moderated_deliberation",
                    "iteration": int(state_.get("agentic_iteration") or 0),
                    "reason": "" if completed_models else "no_deliberation_result",
                    "models": completed_models,
                }
                for index, (_, task) in enumerate(expert_tasks)
            ]
            return {
                "expert_results": [
                    result["res"] for result in moderated_results if "res" in result
                ],
                "expert_models_used": used,
                "prompt_tokens": sum(
                    result.get("prompt_tokens", 0) for result in moderated_results
                ),
                "completion_tokens": sum(
                    result.get("completion_tokens", 0) for result in moderated_results
                ),
                "user_conn_prompt_tokens": sum(
                    result.get("user_conn_prompt_tokens", 0)
                    for result in moderated_results
                ),
                "user_conn_completion_tokens": sum(
                    result.get("user_conn_completion_tokens", 0)
                    for result in moderated_results
                ),
                "conflict_registry": local_conflicts,
                "task_events": deliberation_task_events,
                "deliberation_capacity": deliberation_capacity.as_dict(),
                "deliberation_events": local_deliberation_events,
            }

    async def _run_review_wave(primary_results: List[dict]) -> Tuple[List[dict], List[dict], bool]:
        """One parallel wave of complementary-lens reviews over primary expert outputs.

        Opt-in per template category via ``review_lenses``. Each lens category is
        dispatched at most once per wave, so a single-slot endpoint never gets
        more than one review call. Reviews are prefixed ``[REVIEW:`` so the trust
        score does not count them as experts (services/trust_score.py).
        Returns (results, conflicts, replaces_self_critique).
        """
        if os.getenv("MOE_REVIEW_WAVE_ENABLED", "1") != "1":
            return [], [], False
        if state_.get("force_tier1") or str(state_.get("complexity_level") or "") in ("trivial", "memory_recall"):
            return [], [], False
        catalog = state_.get("user_experts") or {}
        if not catalog:
            return [], [], False
        max_reviewers = int(os.getenv("MOE_REVIEW_WAVE_MAX_REVIEWERS", "4"))
        max_input_chars = int(os.getenv("MOE_REVIEW_INPUT_CHARS", "6000"))

        lens_targets: Dict[str, List[Tuple[str, str]]] = {}
        replaces_sc = False
        for result in primary_results:
            model_cat = str(result.get("model_cat") or "")
            text = str(result.get("res") or "")
            if "::" not in model_cat or " ERROR]" in text or not text.strip():
                continue
            primary_cat = model_cat.rsplit("::", 1)[-1]
            cfgs = catalog.get(primary_cat) or []
            if not cfgs:
                continue
            if cfgs[0].get("_review_replaces_self_critique"):
                replaces_sc = True
            for lens in cfgs[0].get("_review_lenses") or []:
                if lens == primary_cat or not catalog.get(lens):
                    continue
                lens_targets.setdefault(lens, []).append((primary_cat, text))
        if not lens_targets:
            return [], [], False

        selected = list(lens_targets.items())[:max_reviewers]
        logger.info(
            "--- [NODE] REVIEW-WAVE (%d reviewer(s): %s) ---",
            len(selected), ", ".join(lens for lens, _ in selected),
        )
        await _report(f"🔍 Review wave: {', '.join(lens for lens, _ in selected)}")
        user_query = str(state_.get("input") or "")[:4000]

        async def _one(index: int, lens: str, targets: List[Tuple[str, str]]) -> dict:
            reviewed = "\n\n".join(
                f"[Expert output ({pcat})]\n{ptext[:max_input_chars]}" for pcat, ptext in targets
            )
            review_task = {
                "id": f"review-{lens}",
                "category": lens,
                "allowed_domains": [lens] + [pcat for pcat, _ in targets],
                "_deliberation_turn": True,  # caps output at deliberation max_turn_tokens
                "task": (
                    f"[User Query]\n{user_query}\n\n{reviewed}\n\n[Task]\n"
                    f"You are a reviewer from the '{lens}' discipline. Review the expert output(s) "
                    "above strictly from the perspective of your discipline. List concrete defects, "
                    "risks, missing requirements or wrong claims, each with a one-sentence "
                    "justification and, where possible, the exact fix. Do NOT rewrite or "
                    "re-implement the full solution. If you find no defect in your discipline, "
                    "answer exactly: NO_FINDINGS"
                ),
            }
            return await run_single(catalog[lens][0], review_task, 900 + index, 1)

        raw = await asyncio.gather(
            *[_one(i, lens, targets) for i, (lens, targets) in enumerate(selected)],
            return_exceptions=True,
        )
        results: List[dict] = []
        conflicts: List[dict] = []
        from parsing import _improvement_ratio
        for (lens, targets), res in zip(selected, raw):
            if isinstance(res, BaseException) or not isinstance(res, dict):
                logger.warning("Review wave: %s failed: %s", lens, res)
                continue
            text = str(res.get("res") or "")
            if not res.get("model_cat") or " ERROR]" in text:
                continue
            content = text.split("]: ", 1)[1] if "]: " in text else text
            if not content.strip() or "NO_FINDINGS" in content[:200]:
                logger.info("Review wave: %s reported no findings", lens)
                continue
            primaries = ",".join(sorted({pcat for pcat, _ in targets}))
            results.append({**res, "res": f"[REVIEW:{lens}→{primaries} / {lens}]: {content}"})
            for pcat, ptext in targets:
                div_score = _improvement_ratio(ptext[:1200], content[:1200])
                if div_score >= 0.35:
                    conflicts.append({
                        "category": pcat,
                        "proposition_a": ptext[:600],
                        "proposition_b": content[:600],
                        "divergence_score": round(div_score, 3),
                        "resolution": "pending",
                        "resolved_by": "",
                    })
        return results, conflicts, replaces_sc

    # Dynamic parallel/sequential execution via dependency levels.
    # Tasks with no 'depends_on' run in parallel (level 0).
    # Tasks whose 'depends_on' points to a level-N task run in level N+1.
    # Within each level, all tasks run in parallel (asyncio.gather).
    levels = _topological_levels(expert_tasks)
    has_deps = any(t.get("depends_on") for _, t in expert_tasks)
    if has_deps:
        logger.info(f"⛓️ Expert execution: {len(levels)} dependency level(s) "
                    f"({[len(lvl) for lvl in levels]} tasks per level)")

    all_results: List[dict] = []
    prior_outputs: dict[str, str] = {}  # task_id → trimmed expert output for placeholder injection
    task_events: list[dict] = []

    for lvl_idx, level in enumerate(levels):
        # Inject results from prior levels into {result_of:id} placeholders
        injected_level = [(i, _inject_prior_results(t, prior_outputs)) for i, t in level]

        if has_deps and len(levels) > 1:
            level_ids = [t.get("id", f"task{i}") for i, t in level]
            await _report(
                f"⛓️ Dependency level {lvl_idx + 1}/{len(levels)}: "
                f"running {len(level)} task(s) in parallel — [{', '.join(level_ids)}]"
            )

        level_groups = await asyncio.gather(
            *[run_task(i, task) for i, task in injected_level]
        )
        level_results = [r for group in level_groups for r in group]
        all_results.extend(level_results)

        # Collect outputs for downstream placeholder injection
        for (orig_idx, orig_task), group in zip(injected_level, level_groups):
            tid = orig_task.get("id", "")
            if tid:
                combined = " | ".join(r.get("res", "") for r in group if r.get("res"))
                prior_outputs[tid] = combined[:400]
                successful_models = [
                    r.get("model_cat")
                    for r in group
                    if r.get("model_cat") and r.get("res")
                ]
                task_events.append(
                    {
                        "task_id": tid,
                        "category": orig_task.get("category", "general"),
                        "status": "completed" if successful_models else "failed",
                        "executor": "workers",
                        "iteration": int(state_.get("agentic_iteration") or 0),
                        "reason": "" if successful_models else "no_expert_result",
                        "models": successful_models,
                    }
                )

    review_results, review_conflicts, review_replaces_sc = await _run_review_wave(all_results)
    all_results.extend(review_results)
    local_conflicts.extend(review_conflicts)

    used = [r["model_cat"] for r in all_results if r.get("model_cat")]
    return {
        "review_replaces_self_critique": bool(review_replaces_sc and review_results),
        "expert_results":              [r["res"] for r in all_results if "res" in r],
        "expert_models_used":          used,
        "prompt_tokens":               sum(r.get("prompt_tokens",               0) for r in all_results),
        "completion_tokens":           sum(r.get("completion_tokens",           0) for r in all_results),
        "user_conn_prompt_tokens":     sum(r.get("user_conn_prompt_tokens",     0) for r in all_results),
        "user_conn_completion_tokens": sum(r.get("user_conn_completion_tokens", 0) for r in all_results),
        "conflict_registry":           local_conflicts,
        "task_events":                 task_events,
        "deliberation_capacity":       deliberation_capacity.as_dict(),
        "deliberation_events":         local_deliberation_events,
    }
