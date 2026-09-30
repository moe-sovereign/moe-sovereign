"""services/pipeline/ollama.py — Ollama protocol helpers and streaming."""

import asyncio
import json
import hashlib
import os
import re
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

import state
from config import (
    KAFKA_TOPIC_INGEST, KAFKA_TOPIC_REQUESTS, KAFKA_TOPIC_FEEDBACK,
    CLAUDE_CODE_MODELS, CLAUDE_CODE_TOOL_MODEL, CLAUDE_CODE_TOOL_ENDPOINT,
    CLAUDE_CODE_MODE, CLAUDE_CODE_REASONING_MODEL, CLAUDE_CODE_REASONING_ENDPOINT,
    _CLAUDE_CODE_TOOL_URL, _CLAUDE_CODE_TOOL_TOKEN, _CLAUDE_CODE_REASONING_URL,
    JUDGE_TIMEOUT, EXPERT_TIMEOUT, PLANNER_TIMEOUT,
    JUDGE_MODEL, JUDGE_URL, JUDGE_TOKEN,
    PLANNER_MODEL, PLANNER_URL, PLANNER_TOKEN,
    URL_MAP, TOKEN_MAP, API_TYPE_MAP, INFERENCE_SERVERS_LIST,
    MODES, _MODEL_ID_TO_MODE, _CLAUDE_PRETTY_NAMES, _model_display_name,
    MAX_GRAPH_CONTEXT_CHARS, MCP_URL, GRAPH_VIA_MCP,
    CACHE_HIT_THRESHOLD, SOFT_CACHE_THRESHOLD, SOFT_CACHE_MAX_EXAMPLES,
    CACHE_MIN_RESPONSE_LEN, ROUTE_THRESHOLD, ROUTE_GAP,
    EXPERT_TIER_BOUNDARY_B, EXPERT_MIN_SCORE, EXPERT_MIN_DATAPOINTS,
    HISTORY_MAX_TURNS, HISTORY_MAX_CHARS,
    JUDGE_REFINE_MAX_ROUNDS, JUDGE_REFINE_MIN_IMPROVEMENT,
    TOOL_MAX_TOKENS, REASONING_MAX_TOKENS,
    PLANNER_RETRIES, PLANNER_MAX_TASKS, SSE_CHUNK_SIZE,
    EVAL_CACHE_FLAG_THRESHOLD, FEEDBACK_POSITIVE_THRESHOLD, FEEDBACK_NEGATIVE_THRESHOLD,
    BENCHMARK_SHADOW_TEMPLATE, BENCHMARK_SHADOW_RATE,
    _FALLBACK_NODE, _FALLBACK_MODEL, _FALLBACK_MODEL_SECOND,
    _FALLBACK_ENABLED, _ENDPOINT_RETRY_COUNT, _ENDPOINT_RETRY_DELAY, _ENDPOINT_DEGRADED_TTL,
    LITELLM_URL, _SEARXNG_URL, _WEB_SEARCH_FALLBACK_DDG,
    CORRECTION_MEMORY_ENABLED, GRAPH_INGEST_MODEL, GRAPH_INGEST_URL, GRAPH_INGEST_TOKEN,
    _CUSTOM_EXPERT_PROMPTS, THOMPSON_SAMPLING_ENABLED,
    _FUZZY_VECTOR_THRESHOLD, _FUZZY_GRAPH_THRESHOLD,
    _GRAPH_COMPRESS_THRESHOLD_FACTOR, _GRAPH_COMPRESS_LLM_MODEL, _GRAPH_COMPRESS_LLM_TIMEOUT,
)
from metrics import (
    PROM_TOKENS, PROM_REQUESTS, PROM_EXPERT_CALLS, PROM_CONFIDENCE,
    PROM_CACHE_HITS, PROM_CACHE_MISSES, PROM_RESPONSE_TIME,
    PROM_SELF_EVAL, PROM_COMPLEXITY,
    PROM_ACTIVE_REQUESTS, PROM_TOOL_CALL_DURATION, PROM_TOOL_TIMEOUTS,
    PROM_TOOL_FORMAT_ERRORS, PROM_TOOL_CALL_SUCCESS,
    PROM_HISTORY_COMPRESSED, PROM_HISTORY_UNLIMITED,
    PROM_SEMANTIC_MEMORY_STORED, PROM_SEMANTIC_MEMORY_HITS,
    PROM_CORRECTIONS_INJECTED, PROM_CORRECTIONS_STORED,
    PROM_JUDGE_REFINED, PROM_EXPERT_FAILURES,
    PROM_SYNTHESIS_CREATED, PROM_THOMPSON,
)
from services.auth import _validate_api_key, _extract_api_key, _extract_session_id
from services.kafka import _kafka_publish
from services.routing import (
    _resolve_user_experts, _resolve_template_prompts,
    _server_info, _is_endpoint_error,
)
from services.deliberation.contracts import DeliberationPolicyError
from services.tracking import (
    _log_usage_to_db, _register_active_request,
    _deregister_active_request, _increment_user_budget,
    _check_ip_rate_limit,
)
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
from services.inference import _select_node as _select_node_svc, _get_available_models as _get_available_models_svc
from services.skills import _build_skill_catalog
def _ollama_now() -> str:
    """Return current UTC time in Ollama's nanosecond ISO8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f000Z")


def _ollama_model_entry(tmpl: dict, *, now_iso: str) -> dict:
    """Convert an admin template dict to an Ollama /api/tags model entry."""
    return {
        "name":        tmpl.get("name", tmpl["id"]),
        "model":       tmpl.get("name", tmpl["id"]),
        "modified_at": now_iso,
        "size":        0,
        "digest":      hashlib.sha256(tmpl["id"].encode()).hexdigest(),
        "details": {
            "parent_model":       "",
            "format":             "gguf",
            "family":             "moe",
            "families":           ["moe"],
            "parameter_size":     tmpl.get("description", ""),
            "quantization_level": "MoE",
        },
    }


def _ollama_messages_to_oai(messages: list) -> list:
    """Translate Ollama messages (with optional base64 images) to OpenAI format."""
    out = []
    for m in messages:
        content = m.get("content", "")
        images  = m.get("images", [])
        if images:
            parts = [{"type": "text", "text": content}]
            for img in images:
                parts.append({
                    "type":      "image_url",
                    "image_url": {"url": f"data:image/png;base64,{img}"},
                })
            content = parts
        out.append({"role": m.get("role", "user"), "content": content})
    return out


async def _ollama_resolve_template(model_name: str, user_perms: dict, user_templates_json: str = "{}"):
    """Return (tmpl_id, tmpl_dict | None, error_response | None) for an Ollama model name."""
    all_tmpls   = _read_expert_templates()
    matched     = next((t for t in all_tmpls if t.get("name") == model_name or t.get("id") == model_name), None)
    owned_tmpls: dict = {}
    try:
        owned_tmpls = json.loads(user_templates_json or "{}")
    except Exception:
        pass
    if not matched:
        # Check user-owned templates before giving up
        for ut_id, ut_cfg in owned_tmpls.items():
            ut_name = ut_cfg.get("name", "")
            if ut_name == model_name or ut_id == model_name:
                return ut_id, ut_cfg, None
        return None, None, None  # No template match — will fall through to default MoE mode
    tmpl_id      = matched["id"]
    allowed      = user_perms.get("expert_template", [])
    if tmpl_id not in allowed and tmpl_id not in owned_tmpls:
        return tmpl_id, matched, JSONResponse(status_code=403, content={
            "error": f"Template '{model_name}' is not authorized for this API key"
        })
    return tmpl_id, matched, None


async def _ollama_internal_stream(
    user_ctx: dict,
    model_name: str,
    oai_messages: list,
    extra_params: dict,
):
    from main import stream_response
    """Async generator: yields raw SSE lines from the MoE pipeline for an Ollama request.

    Performs full template resolution and calls stream_response() directly,
    identical to /v1/chat/completions after auth, so no pipeline logic is duplicated.
    """
    chat_id   = f"chatcmpl-{uuid.uuid4()}"
    user_id   = user_ctx.get("user_id", "anon")
    api_key_id = user_ctx.get("key_id", "")
    user_perms = json.loads(user_ctx.get("permissions_json", "{}"))
    from services.sovereignty import resolve_local_only
    local_only = resolve_local_only(user_perms, user_ctx)

    tmpl_id, matched_tmpl, err = await _ollama_resolve_template(
        model_name, user_perms, user_ctx.get("user_templates_json", "{}")
    )
    if err is not None:
        yield f"data: {json.dumps({'error': 'template_not_authorized'})}\n\n"
        return

    _user_tmpls_json = user_ctx.get("user_templates_json", "{}")
    _user_conns_json = user_ctx.get("user_connections_json", "{}")
    user_experts  = _resolve_user_experts(
        user_ctx.get("permissions_json", ""), override_tmpl_id=tmpl_id,
        user_templates_json=_user_tmpls_json, admin_override=False,
        user_connections_json=_user_conns_json) or {}
    try:
        tmpl_prompts = _resolve_template_prompts(
            user_ctx.get("permissions_json", ""), override_tmpl_id=tmpl_id,
            user_templates_json=_user_tmpls_json, admin_override=False,
            user_connections_json=_user_conns_json)
    except DeliberationPolicyError:
        yield f"data: {json.dumps({'error': 'deliberation_policy_invalid'})}\n\n"
        return

    mode = _MODEL_ID_TO_MODE.get(model_name, "default")
    if matched_tmpl and matched_tmpl.get("force_think") and mode == "default":
        mode = "agent_orchestrated"

    system_msgs   = [m for m in oai_messages if m.get("role") == "system"]
    system_prompt = system_msgs[0]["content"] if system_msgs else ""

    user_msgs = [m for m in oai_messages if m.get("role") in ("user", "assistant")]
    last_user = next((m for m in reversed(oai_messages) if m.get("role") == "user"), None)
    user_input = last_user["content"] if last_user else ""
    if isinstance(user_input, list):
        user_input = " ".join(p.get("text", "") for p in user_input if isinstance(p, dict))

    _user_images: list = []
    if last_user:
        raw_content = last_user.get("content", "")
        if isinstance(raw_content, list):
            _user_images = _extract_oai_images(raw_content)

    raw_history = [
        {"role": m.get("role"), "content": m.get("content", "") if isinstance(m.get("content"), str) else ""}
        for m in oai_messages
        if m.get("role") in ("user", "assistant") and m is not last_user
    ]
    _hist_turns = tmpl_prompts.get("history_max_turns", 0) or None
    _hist_chars = tmpl_prompts.get("history_max_chars", 0) or None
    history = _truncate_history(raw_history, max_turns=_hist_turns, max_chars=_hist_chars)
    raw_num_predict = 0
    if isinstance(extra_params, dict):
        options = extra_params.get("options")
        if isinstance(options, dict):
            raw_num_predict = options.get("num_predict", 0)
        raw_num_predict = extra_params.get("num_predict", raw_num_predict)
    try:
        client_max_output_tokens = max(0, int(raw_num_predict or 0))
    except (TypeError, ValueError):
        client_max_output_tokens = 0

    # Await registration so every subsequent error/return path can reliably
    # remove the exact entry; fire-and-forget registration races used to
    # recreate keys after a fast failure had already deregistered them
    # (see services/pipeline/chat.py for the same fix applied earlier).
    await _register_active_request(
        chat_id=chat_id, user_id=user_id, model=model_name,
        moe_mode=mode, req_type="streaming", template_name=model_name,
        client_ip="", backend_model="", backend_host="", api_key_id=api_key_id,
    )

    async for sse_line in stream_response(
        user_input, chat_id, mode,
        chat_history=history,
        system_prompt=system_prompt,
        user_id=user_id,
        api_key_id=api_key_id,
        user_permissions=user_perms,
        user_experts=user_experts,
        planner_prompt=tmpl_prompts["planner_prompt"],
        judge_prompt=tmpl_prompts["judge_prompt"],
        judge_model_override=tmpl_prompts["judge_model_override"],
        judge_url_override=tmpl_prompts["judge_url_override"],
        judge_token_override=tmpl_prompts["judge_token_override"],
        planner_model_override=tmpl_prompts["planner_model_override"],
        planner_url_override=tmpl_prompts["planner_url_override"],
        planner_token_override=tmpl_prompts["planner_token_override"],
        planner_num_ctx=tmpl_prompts.get("planner_num_ctx", 0),
        judge_num_ctx=tmpl_prompts.get("judge_num_ctx", 0),
        guardrail_prompt=tmpl_prompts.get("guardrail_prompt", ""),
        guardrail_model_override=tmpl_prompts.get("guardrail_model_override", ""),
        guardrail_url_override=tmpl_prompts.get("guardrail_url_override", ""),
        guardrail_token_override=tmpl_prompts.get("guardrail_token_override", ""),
        guardrail_num_ctx=tmpl_prompts.get("guardrail_num_ctx", 0),
        model_name=model_name,
        pending_reports=[],
        images=_user_images,
        session_id=None,
        max_agentic_rounds=tmpl_prompts.get("max_agentic_rounds", 0),
        deliberation_policy=tmpl_prompts.get("deliberation_policy", {}),
        client_max_output_tokens=client_max_output_tokens,
        no_cache=False,
        local_only=local_only,
    ):
        yield sse_line
