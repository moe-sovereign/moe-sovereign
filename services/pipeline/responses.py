"""services/pipeline/responses.py — OpenAI Responses API (/v1/responses)."""

import asyncio
import json
import logging
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
    _resolve_template_selection, _resolve_user_experts, _resolve_template_prompts,
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

logger = logging.getLogger("MOE-SOVEREIGN")


class _ResponsesPipelineError(RuntimeError):
    """A request error that can be represented as a Responses API failure."""

    def __init__(self, message: str, code: str, status_code: int = 500):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class _ResponsesRequest(BaseModel):
    model: str
    input: Any                        # str or list of {role, content} items
    instructions: Optional[str] = None
    stream: bool = False
    max_output_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    previous_response_id: Optional[str] = None
    # passthrough fields silently accepted
    tools: Optional[Any] = None
    text: Optional[Any] = None
    include: Optional[Any] = None
    background: Optional[bool] = None
    context_management: Optional[Any] = None
    max_agentic_rounds: Optional[int] = None
    no_cache: bool = False


def _flatten_responses_content(content: Any) -> str:
    """Flatten Responses API content blocks to a plain string for Ollama."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                btype = block.get("type", "")
                if btype in ("text", "input_text", "output_text"):
                    parts.append(block.get("text", ""))
                elif btype == "input_file":
                    fname = block.get("filename", "attachment")
                    fdata = block.get("file_data") or block.get("text", "")
                    if fdata:
                        parts.append(f"\n\n--- {fname} ---\n{fdata}\n---")
                elif btype == "image_url":
                    pass  # text-only experts skip images
                else:
                    text = block.get("text", "") or str(block.get("value", ""))
                    if text:
                        parts.append(text)
        return "\n".join(p for p in parts if p)
    return str(content) if content else ""


def _responses_input_to_messages(inp: Any, instructions: Optional[str]) -> list:
    """Convert Responses API input to Chat Completions messages list.

    Handles both simple strings and the richer Responses API format:
    items may be plain message dicts, type='message' wrappers, or direct
    text/file content blocks. Content blocks are flattened to strings so
    downstream Ollama experts receive only plain text.
    """
    messages: list = []
    if instructions:
        messages.append({"role": "system", "content": instructions})
    if isinstance(inp, str):
        messages.append({"role": "user", "content": inp})
    elif isinstance(inp, list):
        for item in inp:
            if not isinstance(item, dict):
                continue
            itype = item.get("type", "")
            # type="message" wrapper or plain role/content dict
            if itype == "message" or "role" in item:
                role = item.get("role", "user")
                if role == "developer":
                    role = "system"
                content = _flatten_responses_content(item.get("content", ""))
                if content:
                    messages.append({"role": role, "content": content})
            elif itype in ("input_text", "text"):
                # top-level text item without role wrapper → treat as user
                text = item.get("text", "")
                if text:
                    messages.append({"role": "user", "content": text})
    return messages


async def _invoke_pipeline_for_responses(
    raw_request: Request,
    request: "_ResponsesRequest",
    messages: list,
) -> tuple[str, int, int]:
    from main import stream_response
    """Call the MoE pipeline directly and return (full_text, prompt_tokens, completion_tokens).

    Consumes stream_response() as an async generator so no HTTP self-call is needed.
    Collects only the final synthesised chat.completion chunk; skips all
    status/progress/debug delta lines emitted by the pipeline.
    """
    raw_key = _extract_api_key(raw_request)
    user_ctx = await _validate_api_key(raw_key) if raw_key else {"error": "missing_key"}
    if "error" in user_ctx:
        raise _ResponsesPipelineError(
            "Invalid or missing API key", "invalid_api_key", 401
        )

    user_id = user_ctx.get("user_id", "anon")
    api_key_id = user_ctx.get("key_id", "")
    try:
        user_perms = json.loads(user_ctx.get("permissions_json", "{}") or "{}")
    except Exception:
        user_perms = {}
    from services.sovereignty import resolve_local_only
    local_only = resolve_local_only(user_perms, user_ctx)
    chat_id = f"chatcmpl-{uuid.uuid4()}"

    # Separate system messages from conversation history
    sys_msgs = [m for m in messages if m["role"] == "system"]
    conv = [m for m in messages if m["role"] != "system"]
    system_prompt = sys_msgs[-1]["content"] if sys_msgs else ""
    user_input = ""
    history: list = []
    if conv:
        *history, last = conv
        user_input = last.get("content", "") if isinstance(last.get("content"), str) else ""

    mode = _MODEL_ID_TO_MODE.get(request.model, "default")
    _tmpl_requested = (
        request.model
        if request.model and request.model != "moe-orchestrator"
        else None
    )
    _selection = _resolve_template_selection(
        user_ctx.get("permissions_json", ""),
        override_tmpl_id=_tmpl_requested,
        user_templates_json=user_ctx.get("user_templates_json", "{}"),
        admin_override=False,
    )
    if _selection["template"] is not None and not _selection["authorized"]:
        raise _ResponsesPipelineError(
            f"Template '{request.model}' is not authorized for this API key",
            "template_not_authorized",
            403,
        )
    _tmpl_id = _selection["id"]
    user_experts = _resolve_user_experts(
        user_ctx.get("permissions_json", ""),
        override_tmpl_id=_tmpl_id,
        user_templates_json=user_ctx.get("user_templates_json", "{}"),
        admin_override=False,
        user_connections_json=user_ctx.get("user_connections_json", "{}"),
    ) or {}
    try:
        _tp = _resolve_template_prompts(
            user_ctx.get("permissions_json", ""),
            override_tmpl_id=_tmpl_id,
            user_templates_json=user_ctx.get("user_templates_json", "{}"),
            admin_override=False,
            user_connections_json=user_ctx.get("user_connections_json", "{}"),
        )
    except DeliberationPolicyError as exc:
        raise _ResponsesPipelineError(
            str(exc), "deliberation_policy_invalid", 422
        ) from exc
    _max_rounds = (
        request.max_agentic_rounds
        if request.max_agentic_rounds is not None
        else _tp.get("max_agentic_rounds", 0)
    )

    _client_ip = raw_request.client.host if raw_request.client else ""
    asyncio.create_task(_register_active_request(
        chat_id=chat_id, user_id=user_id, model=request.model,
        moe_mode=mode, req_type="responses",
        template_name=(
            (_selection["template"] or {}).get("name")
            or (_tmpl_id or "")
        ),
        resolved_tmpl_name=(_selection["template"] or {}).get("name", ""),
        resolved_tmpl_id=_tmpl_id or "",
        client_ip=_client_ip, api_key_id=api_key_id,
    ))

    full_text = ""
    prompt_tokens = 0
    completion_tokens = 0
    _text_parts: list[str] = []
    _in_think = False  # skip everything between <think> and </think> (pipeline internals)
    try:
        async for sse_line in stream_response(
            user_input=user_input,
            chat_id=chat_id,
            mode=mode,
            chat_history=history,
            system_prompt=system_prompt,
            user_id=user_id,
            api_key_id=api_key_id,
            user_permissions=user_perms,
            user_experts=user_experts,
            planner_prompt=_tp.get("planner_prompt", ""),
            judge_prompt=_tp.get("judge_prompt", ""),
            judge_model_override=_tp.get("judge_model_override", ""),
            judge_url_override=_tp.get("judge_url_override", ""),
            judge_token_override=_tp.get("judge_token_override", ""),
            planner_model_override=_tp.get("planner_model_override", ""),
            planner_url_override=_tp.get("planner_url_override", ""),
            planner_token_override=_tp.get("planner_token_override", ""),
            planner_num_ctx=_tp.get("planner_num_ctx", 0),
            judge_num_ctx=_tp.get("judge_num_ctx", 0),
            guardrail_prompt=_tp.get("guardrail_prompt", ""),
            guardrail_model_override=_tp.get("guardrail_model_override", ""),
            guardrail_url_override=_tp.get("guardrail_url_override", ""),
            guardrail_token_override=_tp.get("guardrail_token_override", ""),
            guardrail_num_ctx=_tp.get("guardrail_num_ctx", 0),
            model_name=request.model,
            session_id=_extract_session_id(raw_request),
            max_agentic_rounds=_max_rounds,
            deliberation_policy=_tp.get("deliberation_policy", {}),
            client_max_output_tokens=max(0, int(request.max_output_tokens or 0)),
            no_cache=request.no_cache,
            local_only=local_only,
        ):
            if not isinstance(sse_line, str) or not sse_line.startswith("data:"):
                continue
            payload = sse_line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except Exception:
                continue
            # Usage chunk has choices=[] — extract token counts
            u = chunk.get("usage") or {}
            if u.get("prompt_tokens"):
                prompt_tokens = u["prompt_tokens"]
            if u.get("completion_tokens"):
                completion_tokens = u["completion_tokens"]
            # Content chunks carry delta.content — skip pipeline internals inside <think>
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            piece = delta.get("content", "")
            if not piece:
                continue
            if "<think>" in piece:
                _in_think = True
                continue
            if "</think>" in piece:
                _in_think = False
                continue
            if not _in_think:
                _text_parts.append(piece)
        full_text = "".join(_text_parts)
    except Exception:
        logger.exception(
            "Responses API pipeline error model=%s template_id=%s",
            request.model,
            _tmpl_id,
        )
        raise

    return full_text, prompt_tokens, completion_tokens


async def _stream_responses_api(
    raw_request: Request,
    request: "_ResponsesRequest",
    response_id: str,
) -> AsyncGenerator[str, None]:
    """Stream Responses API SSE events matching OpenAI spec exactly.

    Uses sequence_number, output_index, content_index as required by Codex CLI.
    Sends keepalive SSE comments every 15 s while the pipeline runs.
    """
    messages = _responses_input_to_messages(request.input, request.instructions)
    ts = int(time.time())
    item_id = f"msg_{response_id}"
    seq = 0

    def _ev(event_type: str, data: dict) -> str:
        nonlocal seq
        data["sequence_number"] = seq
        seq += 1
        return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

    yield _ev("response.created", {
        "type": "response.created",
        "response": {"id": response_id, "object": "response", "status": "in_progress",
                     "created_at": ts, "output": []},
    })
    yield _ev("response.in_progress", {
        "type": "response.in_progress",
        "response": {
            "id": response_id, "object": "response", "status": "in_progress",
            "created_at": ts, "model": request.model, "output": [],
            "error": None, "incomplete_details": None, "usage": None,
        },
    })
    yield _ev("response.output_item.added", {
        "type": "response.output_item.added",
        "output_index": 0,
        "item": {"id": item_id, "type": "message", "role": "assistant",
                 "status": "in_progress", "content": []},
    })
    yield _ev("response.content_part.added", {
        "type": "response.content_part.added",
        "output_index": 0,
        "content_index": 0,
        "part": {"type": "output_text", "text": ""},
    })

    # Run pipeline as a background task.  Codex ignores bare SSE comments for
    # its stream-idle timer, so emit a spec-defined in-progress event while a
    # slow local model loads or reasons.
    pipeline_task = asyncio.create_task(
        _invoke_pipeline_for_responses(raw_request, request, messages)
    )
    while not pipeline_task.done():
        try:
            await asyncio.wait_for(asyncio.shield(pipeline_task), timeout=10.0)
        except asyncio.TimeoutError:
            yield _ev("response.in_progress", {
                "type": "response.in_progress",
                "response": {
                    "id": response_id, "object": "response",
                    "status": "in_progress", "created_at": ts,
                    "model": request.model, "output": [],
                    "error": None, "incomplete_details": None, "usage": None,
                },
            })
        except Exception:
            # The task's exception is converted into a spec-shaped
            # response.failed event below; do not let wait_for tear down SSE.
            break
    try:
        full_text, prompt_tokens, completion_tokens = pipeline_task.result()
    except Exception as exc:
        code = getattr(exc, "code", "pipeline_error")
        yield _ev("response.failed", {
            "type": "response.failed",
            "response": {
                "id": response_id,
                "object": "response",
                "created_at": ts,
                "status": "failed",
                "model": request.model,
                "output": [],
                "error": {"code": code, "message": str(exc)},
            },
        })
        return

    for i in range(0, max(len(full_text), 1), 50):
        piece = full_text[i:i + 50]
        if piece:
            yield _ev("response.output_text.delta", {
                "type": "response.output_text.delta",
                "item_id": item_id,
                "output_index": 0,
                "content_index": 0,
                "delta": piece,
            })

    yield _ev("response.output_text.done", {
        "type": "response.output_text.done",
        "item_id": item_id,
        "output_index": 0,
        "content_index": 0,
        "text": full_text,
    })
    yield _ev("response.content_part.done", {
        "type": "response.content_part.done",
        "output_index": 0,
        "content_index": 0,
        "part": {"type": "output_text", "text": full_text, "annotations": []},
    })
    yield _ev("response.output_item.done", {
        "type": "response.output_item.done",
        "output_index": 0,
        "item": {"id": item_id, "type": "message", "role": "assistant",
                 "status": "completed",
                 "content": [{"type": "output_text", "text": full_text, "annotations": []}]},
    })
    yield _ev("response.completed", {
        "type": "response.completed",
        "response": {
            "id": response_id, "object": "response", "created_at": ts,
            "status": "completed", "model": request.model,
            "output": [{"id": item_id, "type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": full_text,
                                     "annotations": []}]}],
            "usage": {"input_tokens": prompt_tokens, "output_tokens": completion_tokens,
                      "total_tokens": prompt_tokens + completion_tokens,
                      "output_tokens_details": {"reasoning_tokens": 0}},
        },
    })


async def responses_api(raw_request: Request, request: _ResponsesRequest):
    """OpenAI Responses API compatibility endpoint for Codex CLI."""
    async def _ol_start(*a, **kw): return None   # lineage owned by moe-codex
    async def _ol_complete(*a, **kw): pass
    async def _ol_fail(*a, **kw): pass
    def dataset_user_query(*a, **kw): return {}
    def dataset_response(*a, **kw): return {}
    response_id = f"resp_{uuid.uuid4().hex}"
    _ol_run_id = await _ol_start(
        "responses_api",
        inputs=[dataset_user_query("")],
        extra_facets={"requestModel": {"_producer": "https://github.com/moe-sovereign/moe-sovereign",
                                       "_schemaURL": "moe-sovereign://requestModel",
                                       "model": request.model, "stream": request.stream}},
    )

    if request.stream:
        # Streaming: COMPLETE/FAIL fires when the inner generator drains.
        _inner = _stream_responses_api(raw_request, request, response_id)

        async def _lineage_wrapped_stream():
            try:
                async for _chunk in _inner:
                    yield _chunk
            except Exception as _se:
                await _ol_fail(_ol_run_id, job_name="responses_api", error=str(_se))
                raise
            else:
                await _ol_complete(_ol_run_id, job_name="responses_api",
                                   outputs=[dataset_response(response_id)])

        return StreamingResponse(
            _lineage_wrapped_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Non-streaming: call pipeline directly (no HTTP self-call to avoid deadlock)
    messages = _responses_input_to_messages(request.input, request.instructions)
    try:
        full_text, prompt_tokens, completion_tokens = await _invoke_pipeline_for_responses(
            raw_request, request, messages
        )
        ts = int(time.time())
        await _ol_complete(_ol_run_id, job_name="responses_api",
                           outputs=[dataset_response(response_id)])
        return JSONResponse({
            "id": response_id,
            "object": "response",
            "created_at": ts,
            "status": "completed",
            "model": request.model,
            "output": [
                {
                    "id": f"msg_{response_id}",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": full_text, "annotations": []}],
                }
            ],
            "usage": {
                "input_tokens": prompt_tokens,
                "output_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "output_tokens_details": {"reasoning_tokens": 0},
            },
        })
    except Exception as _ie:
        logger.exception("Responses API non-streaming pipeline failed")
        await _ol_fail(_ol_run_id, job_name="responses_api", error=str(_ie))
        return JSONResponse(
            status_code=getattr(_ie, "status_code", 500),
            content={"error": {
                "message": str(_ie),
                "type": "invalid_request_error",
                "code": getattr(_ie, "code", "pipeline_error"),
            }},
        )
