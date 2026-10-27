"""services/pipeline/chat.py — OpenAI-compatible chat completions endpoint."""

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
import starfleet_config as _starfleet
import mission_context as _mission_context
from parsing import _oai_content_to_str, _extract_oai_images
from config import (
    KAFKA_TOPIC_INGEST, KAFKA_TOPIC_REQUESTS, KAFKA_TOPIC_FEEDBACK,
    CLAUDE_CODE_MODELS, CLAUDE_CODE_TOOL_MODEL, CLAUDE_CODE_TOOL_ENDPOINT,
    CLAUDE_CODE_MODE, CLAUDE_CODE_REASONING_MODEL, CLAUDE_CODE_REASONING_ENDPOINT,
    _CLAUDE_CODE_TOOL_URL, _CLAUDE_CODE_TOOL_TOKEN, _CLAUDE_CODE_REASONING_URL,
    JUDGE_TIMEOUT, EXPERT_TIMEOUT, PLANNER_TIMEOUT, ORCHESTRATION_TIMEOUT,
    JUDGE_MODEL, JUDGE_URL, JUDGE_TOKEN,
    PLANNER_MODEL, PLANNER_URL, PLANNER_TOKEN,
    URL_MAP, TOKEN_MAP, API_TYPE_MAP, TIMEOUT_MAP, INFERENCE_SERVERS_LIST,
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
    CC_CONTEXT_INDEX_ENABLED,
    AGENT_GRAPHRAG_MAX_CHARS, AGENT_GRAPHRAG_TIMEOUT_S,
    TRIVIAL_FAST_PATH_ENABLED,
    MOE_AUTO_DELIBERATION_ACTIVATION,
    ROUTING_PATTERN_PRIOR_ENABLED,
)
from context_budget import graphrag_budget_chars
from services.agent_enrichment import (
    classify_turn as _classify_agent_turn,
    agent_graph_context,
    agent_writeback,
    agent_cache_lookup,
    extract_file_touches,
    accumulate_stream_tool_call_delta,
    finalize_stream_tool_calls,
    looks_like_premature_stop,
    record_and_classify_tool_ending,
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
from services.tracking import (
    _log_usage_to_db, _register_active_request,
    _deregister_active_request, _increment_user_budget,
    _check_ip_rate_limit, _record_stage, _patch_active_request_backend,
    _record_file_touch, _record_node_latency,
    _record_premature_stop_outcome,
)
from services.conversation_log import log_conversation
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
    current_chat_id,
)
from services.templates import _read_expert_templates, _read_cc_profiles
from services.inference import (
    _audit_cancel,
    _audit_complete,
    _audit_create,
    _get_available_models as _get_available_models_svc,
    _select_node as _select_node_svc,
)
from services.skills import _build_skill_catalog, _resolve_skill_secure, _detect_file_skill
from services.trivial_fast_path import is_moe_auto_preflight_eligible
from services.pipeline.contracts import detect_required_precision_intents
from services.deliberation.contracts import DeliberationPolicyError

logger = logging.getLogger("MOE-SOVEREIGN")
# Pydantic request models (defined here — used by chat_completions and routes)
# ---------------------------------------------------------------------------

class Message(BaseModel):
    role: str
    content: Optional[Any] = None
    # Tool-calling fields (OpenAI spec)
    tool_call_id: Optional[str] = None   # required for role="tool"
    tool_calls: Optional[Any] = None     # list of tool calls for role="assistant"
    name: Optional[str] = None           # participant name (all roles)
    refusal: Optional[str] = None        # refusal text (assistant only)

class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[Message]
    stream: bool = False
    tools: Optional[Any] = None
    tool_choice: Optional[Any] = None
    temperature: Optional[Any] = None
    max_tokens: Optional[Any] = None
    max_completion_tokens: Optional[Any] = None  # OpenAI alias for max_tokens
    stream_options: Optional[Any] = None
    files: Optional[Any] = None
    no_cache: bool = False
    max_agentic_rounds: Optional[Any] = None
    # Standard OpenAI parameters (passed through to backends)
    top_p: Optional[float] = None
    n: Optional[int] = None
    stop: Optional[Any] = None               # str | list[str]
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    seed: Optional[int] = None
    user: Optional[str] = None
    response_format: Optional[Any] = None
    logprobs: Optional[bool] = None
    top_logprobs: Optional[int] = None
    logit_bias: Optional[Any] = None
    parallel_tool_calls: Optional[bool] = None
    service_tier: Optional[str] = None
    store: Optional[bool] = None
    metadata: Optional[Any] = None
    reasoning_effort: Optional[str] = None


def _build_tool_messages(request: "ChatCompletionRequest") -> list:
    """Convert ChatCompletionRequest messages to plain dicts for upstream."""
    messages = []
    for m in request.messages:
        msg: dict = {"role": m.role}
        if m.role == "tool":
            msg["content"] = _oai_content_to_str(m.content) if m.content else ""
            if m.tool_call_id:
                msg["tool_call_id"] = m.tool_call_id
            if m.name:
                msg["name"] = m.name
        elif m.role == "assistant" and m.tool_calls:
            msg["tool_calls"] = m.tool_calls
            if m.content:
                msg["content"] = _oai_content_to_str(m.content)
            else:
                msg["content"] = ""
        else:
            msg["content"] = _oai_content_to_str(m.content) if m.content else ""
            if m.name:
                msg["name"] = m.name
        messages.append(msg)
    return messages


def _normalize_messages_for_ollama_native(messages: list) -> list:
    """Normalize OpenAI-format messages (as built by _build_tool_messages)
    for Ollama's native /api/chat endpoint.

    Mirrors services/pipeline/anthropic.py's _normalize_for_ollama — same
    underlying bug, same fix, needed at every call site that posts to
    Ollama's native /api/chat rather than its OpenAI-compatible /v1/...
    endpoint (the Hermes3 tool-agent path and the premature-stop retry
    fallback below). Ollama's native wire format differs from the OpenAI
    format the rest of this file works in:
    tool_calls[].function.arguments must be a parsed JSON object, not a
    JSON-encoded string, and 'id'/'type'/'tool_call_id' fields are not part
    of Ollama's shape. Sending the raw OpenAI-format messages fails
    immediately with Ollama's "Value looks like object, but can't find
    closing '}' symbol" — a request-validation rejection, not a generation
    failure (confirmed live: the error returns in ~20ms, far too fast for
    the model to have run at all, let alone on a 35B model with 200+ tool
    messages of history). Returns a new list; never mutates the input,
    since callers also forward the original OpenAI-format messages
    elsewhere (client-facing /v1 responses, diagnostics).
    """
    out = []
    for m in messages:
        role = m.get("role", "")
        nm = dict(m)
        if role == "tool":
            nm.pop("tool_call_id", None)
        elif role == "assistant" and nm.get("tool_calls"):
            _native_tcs = []
            for tc in nm["tool_calls"]:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function", {}) if isinstance(tc.get("function"), dict) else {}
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except (json.JSONDecodeError, ValueError):
                        args = {}
                _native_tcs.append({"function": {"name": fn.get("name", ""), "arguments": args}})
            nm["tool_calls"] = _native_tcs
        out.append(nm)
    return out


def _normalise_tool_calls_response(upstream: dict, chat_id: str, tool_model: str) -> dict:
    """Normalise an upstream non-streaming response for OpenAI clients.

    - Overrides id with the gateway chat_id.
    - Ensures content="" (not null) when tool_calls are present.
    - Strips the non-standard 'reasoning' field (Qwen3 thinking trace).
    - Extracts tool calls from content when model writes them as text
      (e.g. qwen2.5 outputs {"name": "...", "arguments": {...}} in content).
    """
    upstream["id"] = chat_id
    upstream.setdefault("object", "chat.completion")
    upstream.setdefault("model", tool_model)

    for choice in upstream.get("choices", []):
        msg = choice.get("message", {})
        msg.pop("reasoning", None)

        if msg.get("tool_calls"):
            _content = (msg.get("content") or "").strip()
            for tc in msg["tool_calls"]:
                fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
                if fn.get("name") == "kanban_complete":
                    try:
                        _args = json.loads(fn.get("arguments") or "{}")
                    except (json.JSONDecodeError, TypeError):
                        _args = {}
                    if "result" not in _args or not _args.get("result"):
                        if _args and "result" not in _args:
                            # Map first non-result arg (summary, answer, etc.) → result
                            _first_val = next(iter(_args.values()), "")
                            if _first_val:
                                _args = {"result": _first_val}
                                fn["arguments"] = json.dumps(_args)
                                logger.info("💡 Normalized kanban_complete arg→result (len=%d)", len(_first_val))
                                continue
                        if _content:
                            # Model wrote answer as text content + called kanban_complete()
                            _args["result"] = _content
                            fn["arguments"] = json.dumps(_args)
                            logger.info("💡 Injected content as kanban_complete result (len=%d)", len(_content))
            if msg.get("content") is None:
                msg["content"] = ""
            continue

        # Fallback: extract tool calls embedded as JSON text in content.
        # ONLY active for Kanban workers (tool_model starts with hermes3 or qwen2.5
        # AND the model is being used as a tool-calling agent, not as a general LLM).
        # This prevents false positives in Open-WebUI / OpenCode responses where
        # code snippets like `read_file(path="...")` could be misinterpreted.
        _is_kanban_model = tool_model.startswith(("hermes3", "qwen2.5"))
        if not _is_kanban_model:
            if msg.get("content") is None:
                msg["content"] = ""
            continue

        raw = (msg.get("content") or "").strip()
        if not raw:
            continue

        import re as _re
        extracted = []

        # Pattern 1: JSON style — {"name": "fn", "arguments": {...}}
        for m in _re.finditer(r'\{[^{}]*"name"\s*:\s*"([^"]+)"[^{}]*"arguments"\s*:\s*(\{[^}]*\})[^{}]*\}', raw, _re.DOTALL):
            fn_name = m.group(1)
            try:
                args = json.loads(m.group(2))
            except (json.JSONDecodeError, ValueError):
                args = {}
            extracted.append({
                "id": f"call_{chat_id[:8]}_{len(extracted)}",
                "type": "function",
                "function": {"name": fn_name, "arguments": json.dumps(args)},
            })

        # Pattern 2: Python call style — fn_name(key="val", key2={...})
        if not extracted:
            # Match: word_chars( ... ) spanning multiple lines
            for m in _re.finditer(r'(\b[a-z][a-z0-9_]*)\s*\(\s*((?:[^()]*|\{[^}]*\})*)\)', raw, _re.DOTALL):
                fn_name = m.group(1)
                if fn_name in ("if", "for", "while", "def", "class", "print", "return"):
                    continue
                args_raw = m.group(2).strip()
                # Parse keyword args: key="value" or key={...}
                args = {}
                for kv in _re.finditer(r'(\w+)\s*=\s*(?:"([^"]*?)"|\'([^\']*?)\'|\{([^}]*)\}|(\S+))', args_raw):
                    key = kv.group(1)
                    val = kv.group(2) or kv.group(3) or kv.group(4) or kv.group(5) or ""
                    args[key] = val
                if args or not args_raw:
                    # Normalize kanban_complete: models often use 'summary' or
                    # other parameter names instead of the required 'result'.
                    if fn_name == "kanban_complete" and "result" not in args and args:
                        args = {"result": next(iter(args.values()), "")}
                    extracted.append({
                        "id": f"call_{chat_id[:8]}_{len(extracted)}",
                        "type": "function",
                        "function": {"name": fn_name, "arguments": json.dumps(args)},
                    })

        if extracted:
            msg["tool_calls"] = extracted
            msg["content"] = ""
            choice["finish_reason"] = "tool_calls"
            logger.info("🔧 Extracted %d tool call(s) from content text: %s",
                        len(extracted), [t["function"]["name"] for t in extracted])

    return upstream


async def _wrap_completion_as_sse(completion: dict, chat_id: str, model: str):
    """Wrap a single non-streaming chat-completion dict as an SSE chunk stream.

    Factored out of _handle_tool_calls (previously an inline `_sse_wrap`
    closure) so the Augmented Tool Path cache-hit response (see
    services/agent_enrichment.py::agent_cache_lookup) can reuse the exact
    same, already-client-tested chunk sequence instead of hand-rolling a new
    one. Behaviour is unchanged from the original inline version.
    """
    choices = completion.get("choices", [{}])
    ch = choices[0] if choices else {}
    msg = ch.get("message", {})
    finish_reason = ch.get("finish_reason", "stop")
    created = completion.get("created", int(time.time()))
    model_id = completion.get("model", model)

    # Opening delta (role)
    yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_id, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]})}\n\n"

    # If tool_calls: emit as a single delta chunk
    tool_calls = msg.get("tool_calls")
    if tool_calls:
        yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_id, 'choices': [{'index': 0, 'delta': {'tool_calls': tool_calls, 'content': msg.get('content', '')}, 'finish_reason': None}]})}\n\n"
    elif msg.get("content"):
        # Plain text response — emit content
        yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_id, 'choices': [{'index': 0, 'delta': {'content': msg['content']}, 'finish_reason': None}]})}\n\n"

    # Finish chunk
    yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_id, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish_reason}]})}\n\n"

    # Usage chunk (separate, choices=[])
    usage = completion.get("usage", {})
    yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_id, 'choices': [], 'usage': usage})}\n\n"
    yield "data: [DONE]\n\n"


async def _wrap_deregister_on_stream_end(body_iterator, chat_id: str):
    """Passes an SSE chunk stream through unchanged, deregistering the
    request from live monitoring (`moe:active:{chat_id}`) once the stream
    ends — including on early client disconnect, since Starlette calls
    `aclose()` on the body iterator in that case too, which fires this
    generator's `finally` block just as it would on a normal completion.

    Pre-existing gap this closes: the tool-calling passthrough branch in
    chat_completions() registers every request via _register_active_request
    but previously had no matching deregister call on any of its return
    paths — entries sat in the "Laufende API-Anfragen" admin table until
    their 2h Redis TTL expired, even though the response had long since
    been fully delivered to the client.
    """
    try:
        async for chunk in body_iterator:
            yield chunk
    finally:
        asyncio.create_task(_deregister_active_request(chat_id))
        asyncio.create_task(_record_stage(chat_id, "tool_model_call", "done"))


async def _agent_writeback_traced(chat_id: str, *args, **kwargs) -> None:
    """Wraps agent_writeback() with started/done stage-trace markers for the
    live-pipeline-visualization diagram. See the anthropic.py counterpart of
    this helper for the rationale (agent_writeback has no chat_id param)."""
    await _record_stage(chat_id, "agent_writeback", "started")
    try:
        await agent_writeback(*args, **kwargs)
    finally:
        await _record_stage(chat_id, "agent_writeback", "done")


async def _wrap_agent_writeback_sse(
    body_iterator, chat_id: str, query: str, scope: str, tenant_id, user_id: str,
    source_model: str, session_id: str,
):
    """Passes an SSE chunk stream through byte-for-byte unchanged while
    accumulating the assistant's text content, firing agent_writeback() once
    at [DONE] — only if the turn ended cleanly (finish_reason=='stop', no
    tool_calls emitted). Works uniformly for both SSE producers used by
    _handle_tool_calls (_stream_tool_synthesis's raw upstream passthrough and
    _sse_wrap's single-response wrapper) since both emit the same
    `data: {...}\\n\\n` / `data: [DONE]\\n\\n` chunk format.

    Parsing failures on individual chunks are swallowed — the passthrough to
    the client must never be affected by the write-back accumulation.
    """
    content_parts: list = []
    saw_tool_calls = False
    finish_reason = None
    async for chunk in body_iterator:
        yield chunk
        try:
            text = chunk.decode() if isinstance(chunk, (bytes, bytearray)) else chunk
            for line in text.split("\n"):
                line = line.strip()
                if not line.startswith("data: "):
                    continue
                payload = line[len("data: "):]
                if payload == "[DONE]":
                    continue
                data = json.loads(payload)
                choices = data.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                if delta.get("tool_calls"):
                    saw_tool_calls = True
                if delta.get("content"):
                    content_parts.append(delta["content"])
                fr = choices[0].get("finish_reason")
                if fr:
                    finish_reason = fr
        except Exception:
            pass

    if not saw_tool_calls and finish_reason == "stop" and content_parts:
        asyncio.create_task(_agent_writeback_traced(
            chat_id,
            query, "".join(content_parts), scope, tenant_id, user_id, source_model,
            session_id or "", state.redis_client, state.agent_cache_collection,
            path="openai",
        ))


async def _retry_tool_agent_fallback(
    user_experts: dict, primary_model: str, messages: list, tools, num_ctx: int, max_tokens,
):
    """Retry once against a different template tool_agent model when the
    primary model didn't call a tool on a turn that needed one — either an
    explicit tool_choice=required violation, or (tool_choice=auto) a
    text-only reply that reads like a premature "I will now..." announcement
    instead of a real answer (see agent_enrichment.looks_like_premature_stop).

    An async generator so the caller can forward SSE keepalive pings to the
    client while this extra network round trip is in flight — mirrors the
    tool_choice=required retry-wait pattern already used in
    services/pipeline/anthropic.py's Claude-Code tool path. Yields
    ("ping", None) tuples while waiting, then exactly one
    ("result", {...} | None) tuple: None means no fallback model was
    configured or the retry itself failed — callers must fall back to the
    original (already-buffered) response in that case, never raise.

    Prefers a genuinely different tool_agent model/endpoint (a different
    model is more likely to behave differently on a retry). If the template
    only configures one tool_agent model — confirmed live: this is the
    common case, e.g. a template with a single qwen3.6:35b entry and no
    second model at all — falls back to retrying the SAME model instead of
    giving up, with an explicit corrective instruction appended to the
    conversation ("you only announced intent, call the tool now"). Without
    this, retry was a no-op for every template that doesn't happen to define
    a second tool_agent model, which turned out to be the deployed template
    that triggered this whole investigation.
    """
    _pool = (user_experts or {}).get("tool_agent", [])
    fb_exp = next(
        (e for e in _pool if e.get("model") and e.get("url") and e.get("model") != primary_model),
        None,
    )
    _retry_messages = messages
    if not fb_exp:
        fb_exp = next((e for e in _pool if e.get("model") == primary_model and e.get("url")), None)
        if fb_exp:
            _retry_messages = messages + [{
                "role": "user",
                "content": (
                    "[SYSTEM CORRECTION] Your previous response only described or "
                    "announced what you were about to do, without actually calling "
                    "a tool. Do not repeat that announcement or explain again — call "
                    "the appropriate tool now, as an actual tool call."
                ),
            }]
    if not fb_exp:
        yield ("result", None)
        return
    fb_base = fb_exp["url"].rstrip("/")
    if fb_base.endswith("/v1"):
        fb_base = fb_base[:-3]

    # Never downgrade a warm model — same defensive check already used in
    # services/pipeline/anthropic.py's own num_ctx resolution. Confirmed live
    # as a real risk: for a same-model retry in particular (the common case
    # per the docstring above), the primary call may have loaded the model at
    # a large template-configured context (e.g. 262144), and a retry that
    # blindly falls back to a hardcoded default here would force Ollama to
    # reload the SAME model at a much smaller window — destroying the warm
    # KV-cache that keeping the model loaded is meant to preserve, and doing
    # so on every single retry.
    _retry_num_ctx = num_ctx or 32768
    try:
        async with httpx.AsyncClient(timeout=2.0) as _ps_cl:
            _ps_r = await _ps_cl.get(
                f"{fb_base}/api/ps",
                headers={"Authorization": f"Bearer {fb_exp.get('token', 'ollama')}"},
            )
            for _loaded in _ps_r.json().get("models", []):
                _lname = _loaded.get("name", "").split(":")[0]
                _ename = fb_exp["model"].split(":")[0]
                _loaded_ctx = _loaded.get("context_length", 0)
                if _lname == _ename and _loaded_ctx >= _retry_num_ctx:
                    _retry_num_ctx = _loaded_ctx
                    break
    except Exception:
        pass  # non-fatal — fall through to the configured/default num_ctx

    fb_payload = {
        "model":    fb_exp["model"],
        # Retry always targets Ollama's native /api/chat (see _do_call below),
        # never the OpenAI-compatible endpoint — must be normalized or a
        # long tool-call history (confirmed live: 234 tool messages) fails
        # every retry with an immediate 400, leaving the client with nothing
        # but the original premature-stop text and no working fallback.
        "messages": _normalize_messages_for_ollama_native(_retry_messages),
        "stream":   False,
        "tools":    tools,
        # No explicit keep_alive — a hardcoded "4h" here used to fix a real
        # bug (omitting it silently fell back to Ollama's ~5min default,
        # evicting a warm 35B model a few minutes after a *successful*
        # retry). That gap is now closed at the infrastructure level: every
        # Ollama instance has its own operator-configured OLLAMA_KEEP_ALIVE
        # server default (e.g. 24h), and a hardcoded "4h" here would
        # actively cap it *below* that — silently overriding an
        # intentionally-set operator value the same way the old omission
        # silently fell back to a too-short one.
        "options": {
            "num_ctx":     _retry_num_ctx,
            "num_predict": max_tokens or 4096,
        },
    }

    async def _do_call():
        async with httpx.AsyncClient(timeout=300.0) as _rcl:
            return await _rcl.post(
                f"{fb_base}/api/chat", json=fb_payload,
                headers={"Authorization": f"Bearer {fb_exp.get('token', 'ollama')}"},
            )

    task = asyncio.create_task(_do_call())
    while not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=10.0)
        except asyncio.TimeoutError:
            yield ("ping", None)
    try:
        resp = await task
        resp.raise_for_status()
        data = resp.json()
        msg = data.get("message", {})
        yield ("result", {
            "content":    msg.get("content", "") or "",
            "tool_calls": msg.get("tool_calls") or [],
            "model":      fb_exp["model"],
        })
    except httpx.HTTPStatusError as e:
        # Body included — a bare "400 Bad Request" tells us nothing about
        # WHY Ollama rejected the retry payload (bad tool schema, bad
        # message shape, ...); confirmed live this happens in production and
        # the response body is the only way to find out what's actually
        # wrong without reproducing it synthetically.
        logger.warning(
            "tool-agent fallback retry failed: %s — body: %s",
            e, e.response.text[:1000],
        )
        yield ("result", None)
    except Exception as e:
        logger.warning("tool-agent fallback retry failed: %s", e)
        yield ("result", None)


async def _handle_tool_calls(
    request: "ChatCompletionRequest",
    chat_id: str,
    tool_model: str,
    tool_base_url: str,
    tool_token: str,
    content_model: str = "",
    content_url: str = "",
    content_token: str = "ollama",
    content_system_prompt: str = "",
    num_ctx: int = 0,
    system_augment: str = "",
    user_experts: dict = None,
    user_id: str = "anon",
    api_key_id: str = "",
    session_id: Optional[str] = None,
):
    """Direct LLM passthrough that preserves tool_calls in the response.

    Bypasses the planner/experts/merger pipeline entirely so the model's
    tool_calls (or final text answer) reach the client intact. Called when
    request.tools is present or messages contain role='tool' entries.

    When content_model/content_url are provided, kanban tasks use a two-phase
    approach: Phase 1 returns a synthetic kanban_show call without LLM (regex
    parses the task ID); Phase 2 calls content_model with a clean prompt
    (no KANBAN_GUIDANCE) to generate the actual answer, then wraps it in
    a synthetic kanban_complete call.

    system_augment (Augmented Tool Path, opt-in): extra text merged into the
    single existing system message (or inserted as a new one) — used to inject
    GraphRAG context ahead of the tool model. See services/agent_enrichment.py.

    user_experts: template expert config (category -> [{"model","url","token",
    "endpoint"}, ...]) — only used for the tool_choice=required retry fallback
    below (category "tool_agent"). Optional; the retry is skipped without it.

    user_id/api_key_id/session_id: only used to write a usage_log row on
    completion (fast/stream path via _log_and_finalize) — this path used to
    never log usage at all, unlike _anthropic_tool_handler's equivalent
    (services/pipeline/anthropic.py), so every OpenCode/OpenAI-format
    tool-calling turn was invisible in the User Portal's usage history.
    Token counts are logged as 0 here (unlike the Anthropic path, this
    passthrough proxy doesn't parse the forwarded SSE stream for exact
    prompt/completion counts) — this restores audit visibility (who, what
    model, when) rather than precise cost accounting for this specific path.

    Returns a StreamingResponse (SSE) when request.stream is True, otherwise
    a plain dict. Callers must check the type and wrap accordingly.
    """
    _handle_tool_calls_t0 = time.monotonic()
    messages = _build_tool_messages(request)
    if system_augment:
        _sys_idx = next((i for i, m in enumerate(messages) if m.get("role") == "system"), -1)
        if _sys_idx >= 0:
            messages[_sys_idx] = {
                **messages[_sys_idx],
                "content": (messages[_sys_idx].get("content") or "") + "\n\n" + system_augment,
            }
        else:
            messages = [{"role": "system", "content": system_augment}] + messages

    # When the conversation already contains tool-result turns (role='tool'), the
    # model must synthesise those results into a text response — not call more
    # tools. Omitting `tools` from the follow-up request forces a text response
    # and prevents models that lack strong instruction-following from looping.
    _has_tool_results = any(m.get("role") == "tool" for m in messages)

    # --- Two-phase kanban handling (when a content model is available) ---
    # Phase 1: Initial kanban dispatch — parse the task ID from the user message
    # and return a synthetic kanban_show tool call without invoking any LLM.
    # This avoids KANBAN_GUIDANCE overwhelming the tool model on the first turn.
    #
    # Phase 2: Synthesis — when kanban_show result is present, call content_model
    # with a clean minimal prompt (no KANBAN_GUIDANCE, no system prompt clutter)
    # and wrap the response in a synthetic kanban_complete call.
    if content_model and content_url:
        _kanban_show_in_tools = any(
            (t.get("function", {}).get("name") == "kanban_show"
             if isinstance(t, dict)
             else getattr(getattr(t, "function", None), "name", "") == "kanban_show")
            for t in (request.tools or [])
        )
        _kanban_complete_in_tools = any(
            (t.get("function", {}).get("name") == "kanban_complete"
             if isinstance(t, dict)
             else getattr(getattr(t, "function", None), "name", "") == "kanban_complete")
            for t in (request.tools or [])
        )

        # Phase 1: Return synthetic kanban_show without LLM
        if not _has_tool_results and _kanban_show_in_tools:
            _task_id = None
            for _m in messages:
                if _m.get("role") == "user":
                    _km = re.search(
                        r'work kanban task[:\s]+(\S+)',
                        _m.get("content", ""),
                        re.IGNORECASE,
                    )
                    if _km:
                        _task_id = _km.group(1)
                        break
            if _task_id:
                logger.info("🎯 Kanban Phase 1: synthetic kanban_show for task %s", _task_id)
                _ks_resp: dict = {
                    "id": chat_id, "object": "chat.completion",
                    "created": int(time.time()), "model": tool_model,
                    "choices": [{
                        "index": 0, "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant", "content": "",
                            "tool_calls": [{
                                "id": f"call_{chat_id[:8]}_ks",
                                "type": "function",
                                "function": {
                                    "name": "kanban_show",
                                    "arguments": json.dumps({"id": _task_id}),
                                },
                            }],
                        },
                    }],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 5, "total_tokens": 5},
                }
                if not request.stream:
                    return _ks_resp
                async def _ks_sse():
                    _tc = _ks_resp["choices"][0]["message"]["tool_calls"]
                    _cr = int(time.time())
                    yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': _cr, 'model': tool_model, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]})}\n\n"
                    yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': _cr, 'model': tool_model, 'choices': [{'index': 0, 'delta': {'tool_calls': _tc, 'content': ''}, 'finish_reason': None}]})}\n\n"
                    yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': _cr, 'model': tool_model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}]})}\n\n"
                    yield "data: [DONE]\n\n"
                return StreamingResponse(_ks_sse(), media_type="text/event-stream")

        # Phase 2: Synthesise using content model with clean prompt.
        # Guard: skip re-synthesis if kanban_complete was already dispatched
        # (i.e. an assistant message already contains a kanban_complete tool call).
        # This prevents looping when Hermes sends a 3rd request after the tool result.
        _kc_already_dispatched = any(
            m.get("role") == "assistant" and any(
                (
                    (tc.get("function", {}).get("name") if isinstance(tc, dict) else
                     getattr(getattr(tc, "function", None), "name", ""))
                    == "kanban_complete"
                )
                for tc in (m.get("tool_calls") or [])
            )
            for m in messages
        )
        # Terminal guard: once kanban_show + kanban_complete have both been called
        # (tool_results_count >= 2), the task is done. Return an empty stop response
        # immediately so Hermes closes the session. This is more reliable than
        # checking tool_calls in assistant messages because the Message Pydantic model
        # does not declare a tool_calls field and drops it on deserialisation.
        if _has_tool_results and _kanban_complete_in_tools:
            _tool_results_count = sum(1 for m in messages if m.get("role") == "tool")
            if _tool_results_count >= 2:
                logger.info("🔒 Kanban done (tool_results=%d) — returning terminal stop", _tool_results_count)
                _stop_resp = {
                    "id": chat_id, "object": "chat.completion",
                    "created": int(time.time()), "model": tool_model,
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": ""}}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                }
                if not request.stream:
                    return _stop_resp
                async def _stop_sse():
                    _ts = int(time.time())
                    yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': _ts, 'model': tool_model, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]})}\n\n"
                    yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': _ts, 'model': tool_model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
                    yield "data: [DONE]\n\n"
                return StreamingResponse(_stop_sse(), media_type="text/event-stream")

        if _has_tool_results and _kanban_complete_in_tools and not _kc_already_dispatched:
            # Extract kanban_show result (task description) — use the FIRST tool result
            # that comes before any kanban_complete in the assistant turn.
            _task_content = ""
            for _m in messages:
                if _m.get("role") == "tool":
                    _task_content = _m.get("content", "").strip()
                    break
            if _task_content:
                # Determine workspace path for writing the result as a file.
                # HERMES_KANBAN_WORKSPACES is set by docker-compose when
                # HERMES_KANBAN_WORKSPACES_HOST is configured; empty = disabled.
                import os as _os
                _ws_base = _os.getenv("HERMES_KANBAN_WORKSPACES", "").rstrip("/")
                _ws_task_id2 = ""
                if _ws_base:
                    for _m in messages:
                        if _m.get("role") == "user":
                            _m2 = re.search(
                                r'work kanban task[:\s]+(\S+)', _m.get("content", ""), re.IGNORECASE
                            )
                            if _m2:
                                _ws_task_id2 = _m2.group(1)
                                break
                _ws_path = f"{_ws_base}/{_ws_task_id2}" if (_ws_base and _ws_task_id2) else ""
                logger.info(
                    "🎯 Kanban Phase 2: content synthesis via %s (task_len=%d, workspace=%s)",
                    content_model, len(_task_content), _ws_path or "disabled",
                )
                _sys = (content_system_prompt or
                        "You are a knowledgeable expert. Answer the task below completely "
                        "and thoroughly. Write only the answer — no meta-commentary.")
                _clean_msgs = [
                    {"role": "system", "content": _sys},
                    {"role": "user", "content": _task_content},
                ]
                _content_endpoint = content_url.rstrip("/").removesuffix("/v1") + "/v1/chat/completions"
                if request.temperature is not None:
                    _content_extra = {"temperature": request.temperature}
                else:
                    _content_extra = {}
                _content_headers = {
                    "Authorization": f"Bearer {content_token}",
                    "Content-Type": "application/json",
                }
                # Phase 2 always streams back to the caller so TCP keepalive
                # events prevent Hermes from timing out during long generations.
                if not request.stream:
                    # Non-streaming client: buffer full response synchronously.
                    try:
                        async with httpx.AsyncClient(timeout=1800) as _c:
                            _cr = await _c.post(
                                _content_endpoint,
                                json={"model": content_model, "messages": _clean_msgs,
                                      "stream": False, **_content_extra},
                                headers=_content_headers,
                            )
                            _cr.raise_for_status()
                            _cr_data = _cr.json()
                        _result_text = ""
                        for _ch in _cr_data.get("choices", []):
                            _t = ((_ch.get("message") or {}).get("content") or "").strip()
                            _t = re.sub(r'<think>.*?</think>', '', _t, flags=re.DOTALL).strip()
                            if _t:
                                _result_text = _t
                                break
                        if _result_text:
                            logger.info("✅ Kanban Phase 2 complete (non-stream): len=%d", len(_result_text))
                            return {
                                "id": chat_id, "object": "chat.completion",
                                "created": int(time.time()), "model": tool_model,
                                "choices": [{"index": 0, "finish_reason": "tool_calls",
                                             "message": {"role": "assistant", "content": None,
                                                         "tool_calls": [{"id": f"call_{chat_id[:8]}_kc2",
                                                                          "type": "function",
                                                                          "function": {"name": "kanban_complete",
                                                                                       "arguments": json.dumps({"result": _result_text})}}]}}],
                                "usage": {"prompt_tokens": 0, "completion_tokens": 10, "total_tokens": 10},
                            }
                    except Exception as _e:
                        logger.error("Kanban Phase 2 (non-stream) failed: %s", _e)
                else:
                    # Streaming path: pipe qwen3.6:35b chunks as SSE keepalives,
                    # then emit kanban_complete as the final event.
                    # This prevents Hermes from timing out during long generations.
                    async def _kc2_stream():
                        _parts: list = []
                        _in_think = False
                        try:
                            async with httpx.AsyncClient(timeout=1800) as _c:
                                async with _c.stream(
                                    "POST", _content_endpoint,
                                    json={"model": content_model, "messages": _clean_msgs,
                                          "stream": True, **_content_extra},
                                    headers=_content_headers,
                                ) as _resp:
                                    _resp.raise_for_status()
                                    async for _line in _resp.aiter_lines():
                                        if _line.startswith("data: "):
                                            _d = _line[6:]
                                            if _d == "[DONE]":
                                                break
                                            try:
                                                _tok = json.loads(_d)
                                                _delta = (_tok.get("choices") or [{}])[0].get("delta", {})
                                                _tok_content = _delta.get("content") or ""
                                                if _tok_content:
                                                    _parts.append(_tok_content)
                                            except Exception:
                                                pass
                                            # SSE comment keepalive — ignored by clients,
                                            # but keeps the TCP connection alive.
                                            yield ": k\n\n"
                        except Exception as _e:
                            logger.error("Kanban Phase 2 streaming failed: %s", _e)
                        # Strip thinking traces from accumulated text
                        _result_text = re.sub(
                            r'<think>.*?</think>', '', "".join(_parts), flags=re.DOTALL
                        ).strip()
                        if not _result_text:
                            logger.warning("⚠️ Kanban Phase 2: empty result after streaming")
                            yield "data: [DONE]\n\n"
                            return
                        logger.info("✅ Kanban Phase 2 complete (stream): len=%d", len(_result_text))
                        # Write result to workspace file so it's accessible outside the DB.
                        # Extension is detected from content: HTML → result.html, else result.md
                        if _ws_path:
                            try:
                                import os as _os
                                _os.makedirs(_ws_path, mode=0o775, exist_ok=True)
                                # Ensure group-write on existing dirs created with wrong perms.
                                _os.chmod(_ws_path, 0o775)
                                _is_html = _result_text.lstrip().startswith(("<!DOCTYPE", "<html", "<HTML"))
                                _fname = "result.html" if _is_html else "result.md"
                                _fpath = f"{_ws_path}/{_fname}"
                                with open(_fpath, "w", encoding="utf-8") as _f:
                                    _f.write(_result_text)
                                logger.info("📄 Workspace file written: %s", _fpath)
                            except Exception as _we:
                                logger.warning("Could not write workspace file: %s", _we)
                        _tc2 = [{"id": f"call_{chat_id[:8]}_kc2", "type": "function",
                                  "function": {"name": "kanban_complete",
                                               "arguments": json.dumps({"result": _result_text})}}]
                        _ts = int(time.time())
                        yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': _ts, 'model': tool_model, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]})}\n\n"
                        yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': _ts, 'model': tool_model, 'choices': [{'index': 0, 'delta': {'tool_calls': _tc2, 'content': ''}, 'finish_reason': None}]})}\n\n"
                        yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': _ts, 'model': tool_model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}]})}\n\n"
                        yield "data: [DONE]\n\n"
                    return StreamingResponse(_kc2_stream(), media_type="text/event-stream")
    # --- End two-phase kanban handling ---

    # Hermes3 models require the native Ollama /api/chat endpoint for reliable
    # tool calling. The OpenAI-compatible /v1/chat/completions endpoint causes
    # Hermes3 to write tool calls as text instead of structured tool_calls JSON.
    _is_hermes3 = tool_model.startswith("hermes3")
    if _is_hermes3:
        # Use base URL without /v1 for Ollama's native /api/chat endpoint
        _ollama_base = tool_base_url.rstrip("/")
        if _ollama_base.endswith("/v1"):
            _ollama_base = _ollama_base[:-3]
        _tool_endpoint = _ollama_base + "/api/chat"
    else:
        _tool_endpoint = tool_base_url.rstrip("/").removesuffix("/v1") + "/v1/chat/completions"

    payload: dict = {
        "model":      tool_model,
        # Hermes3 targets Ollama's native /api/chat (see _tool_endpoint above),
        # which requires tool_calls[].function.arguments as a parsed object,
        # not the OpenAI-format JSON string _build_tool_messages produces —
        # see _normalize_messages_for_ollama_native for why sending it raw
        # fails immediately on any turn with prior tool-call history.
        "messages":   _normalize_messages_for_ollama_native(messages) if _is_hermes3 else messages,
        "stream":     _has_tool_results,
        # No explicit keep_alive — respects each Ollama instance's own
        # server-configured OLLAMA_KEEP_ALIVE default (e.g. 24h) instead of
        # silently overriding it with a shorter app-level value.
    }
    # Never downgrade a warm model — same defensive check as the retry
    # fallback above and services/inference.py / graph/expert.py /
    # services/quality_probe.py. Templates commonly leave tool_expert_num_ctx
    # at 0 (confirmed live for moe-n04-rtx-qwen3.6:35b-256k), which used to
    # mean "send no options.num_ctx at all" — Ollama then falls back to the
    # model's Modelfile default instead of preserving whatever large context
    # is already loaded, forcing a full reload on the very next tool-calling
    # turn (observed: repeated unloads of qwen3.6:35b on N04-RTX during
    # multi-turn agentic tool use, e.g. after a web-search tool result was
    # appended and the model needed its already-loaded large context again).
    _tc_ctx = num_ctx
    try:
        async with httpx.AsyncClient(timeout=2.0) as _ps_cl:
            _tc_base = tool_base_url.rstrip("/").removesuffix("/v1")
            _ps_r = await _ps_cl.get(
                f"{_tc_base}/api/ps",
                headers={"Authorization": f"Bearer {tool_token}"},
            )
            for _loaded in _ps_r.json().get("models", []):
                _lname = _loaded.get("name", "").split(":")[0]
                _ename = tool_model.split(":")[0]
                _loaded_ctx = _loaded.get("context_length", 0)
                if _lname == _ename and _loaded_ctx >= _tc_ctx:
                    _tc_ctx = _loaded_ctx
                    break
    except Exception:
        pass  # non-fatal — fall through to the configured/default num_ctx
    if _tc_ctx:
        payload["options"] = {"num_ctx": _tc_ctx}
    # Always pass tools so the model can call kanban_complete (or any other tool)
    # after synthesising tool results.
    if request.tools:
        payload["tools"] = request.tools
        if request.tool_choice:
            payload["tool_choice"] = request.tool_choice

    # After at least 2 tool-result turns (e.g. kanban_show + write_file), force
    # kanban_complete so the worker always closes the task and never exits with
    # a protocol violation. Models reliably write the file but forget to call
    # the completion tool — this guardrail ensures they always do.
    #
    # _kc_available must default to False here: follow-up role="tool" turns
    # legitimately omit `tools` from the request (see _handle_tool_calls'
    # docstring), so _has_tool_results can be True while request.tools is
    # empty — without this default, the read below (`_kc_available if
    # _has_tool_results else False`) raised UnboundLocalError on every such
    # turn (confirmed live: repeated 500s on /v1/chat/completions).
    _kc_available = False
    if _has_tool_results and request.tools:
        tool_results_count = sum(1 for m in messages if m.get("role") == "tool")
        _kc_available = any(
            (t.get("function", {}).get("name") == "kanban_complete"
             if isinstance(t, dict)
             else getattr(getattr(t, "function", None), "name", "") == "kanban_complete")
            for t in (request.tools or [])
        )
        # Synthetic kanban_complete: if the model already produced a text answer
        # (assistant content after tool results), skip the LLM and return a
        # synthetic kanban_complete tool call using that text as the result.
        # This prevents infinite "Would this be satisfactory?" loops where the
        # model never calls the tool despite repeated injections.
        if _kc_available:
            # Find last pure-text assistant response (no tool_calls).
            # Skip messages that have tool_calls — those are planning/dispatch
            # messages that come BEFORE the tool results, not the actual answer.
            # The answer appears in a pure-text assistant message AFTER the
            # tool results in the conversation.
            _last_assistant_text = ""
            _tool_result_seen = False
            for _m in messages:
                if _m.get("role") == "tool":
                    _tool_result_seen = True
                elif (_m.get("role") == "assistant" and
                      _m.get("content") and
                      not _m.get("tool_calls") and
                      _tool_result_seen):
                    _last_assistant_text = _m["content"].strip()
            # Skip planning/orientation text that mentions calling kanban tools.
            # These are navigation messages, not actual task answers.
            _planning_keywords = ("kanban_show", "let's orient", "i will call",
                                  "will now call", "will call kanban", "step 1:",
                                  "orient myself", "orient ourselves")
            _is_planning = any(kw in _last_assistant_text.lower()
                               for kw in _planning_keywords)
            if _last_assistant_text and not _is_planning and tool_results_count >= 2:
                _synthetic = {
                    "id": chat_id, "object": "chat.completion",
                    "created": int(time.time()), "model": tool_model,
                    "choices": [{
                        "index": 0, "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant", "content": None,
                            "tool_calls": [{
                                "id": f"call_{chat_id[:8]}_kc",
                                "type": "function",
                                "function": {
                                    "name": "kanban_complete",
                                    "arguments": json.dumps({"result": _last_assistant_text}),
                                },
                            }],
                        },
                    }],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 10, "total_tokens": 10},
                }
                logger.info("🎯 Synthetic kanban_complete from assistant text (len=%d)", len(_last_assistant_text))
                if not request.stream:
                    return _synthetic
                # Re-use _sse_wrap logic inline for streaming
                async def _kc_sse():
                    _ch = _synthetic["choices"][0]
                    _tc = _ch["message"]["tool_calls"]
                    _cr = int(time.time())
                    yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': _cr, 'model': tool_model, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]})}\n\n"
                    yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': _cr, 'model': tool_model, 'choices': [{'index': 0, 'delta': {'tool_calls': _tc, 'content': ''}, 'finish_reason': None}]})}\n\n"
                    yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': _cr, 'model': tool_model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}]})}\n\n"
                    yield "data: [DONE]\n\n"
                return StreamingResponse(_kc_sse(), media_type="text/event-stream")
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.max_tokens:
        payload["max_tokens"] = request.max_tokens

    error_response = {
        "id": chat_id, "object": "chat.completion",
        "created": int(time.time()), "model": tool_model,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant",
                                 "content": "[Tool-calling error — check logs]"}}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }

    # Fast path: upstream streaming for tool-result synthesis.
    # DISABLED for kanban_complete-capable requests: the normalisation of
    # kanban_complete arguments (summary→result, empty→content) only runs in
    # the slow path. Using streaming here bypasses it and results in NULL result.
    _has_kanban_complete = _kc_available if _has_tool_results else False
    if _has_tool_results and request.stream and not _has_kanban_complete:
        async def _stream_tool_synthesis():
            # Forwards tool_calls deltas to the client live, as they arrive —
            # once a tool_calls delta is seen, the rest of the stream is pure
            # live pass-through (this is the successful/common case, per
            # production diagnostics, and never needs a retry). Content-only
            # deltas are instead held back (not yet forwarded) until either a
            # tool_calls delta arrives (then flushed immediately, in order)
            # or the response concludes with no tool_calls at all: only then
            # can a text-only "premature stop" be reliably detected, and only
            # then is it still possible to retry before anything reaches the
            # client — a live pass-through can't do this, since once text
            # chunks are forwarded, they can't be un-sent. This was the
            # actual reason the tool_choice=required retry never fired in
            # earlier attempts: raw live streaming had already delivered a
            # "finished" turn to OpenCode by the time a violation could even
            # be detected.
            _diag_saw_tc = False
            _diag_fr = None
            _diag_chars = 0
            _diag_chunks = 0
            _diag_done_seen = False
            _diag_exit_extra = ""
            _file_tc_acc: dict = {}
            _content_acc: list = []
            _held_lines: list = []
            _flushed_live = False
            _t_stream_start = time.monotonic()

            def _log_and_finalize(reason: str):
                # Shared by every exit path so the diagnostic and file-touch
                # extraction actually fire regardless of how the stream ends
                # (clean [DONE], loop-exhausted-without-DONE, cancelled,
                # retried) — previously only the "data: [DONE]" branch did
                # this, so a client disconnect or an upstream that closes the
                # connection without sending [DONE] silently skipped both.
                #
                # Level: INFO only for exits worth an admin's attention while
                # scanning logs (client bailed, or the model stopped without
                # ever calling a tool — the exact silent-stop failure shape
                # this whole diagnostic was built to catch); DEBUG for the
                # routine successful case, which now fires on every single
                # tool-calling turn and would otherwise flood production logs.
                _notable = reason == "client-disconnected" or (not _diag_saw_tc and _diag_fr == "stop")
                _log = logger.info if _notable else logger.debug
                _log(
                    "tool-passthrough result (fast/stream path, exit=%s%s): tool_choice=%r "
                    "has_tool_results=%s finish_reason=%r has_tool_calls=%s "
                    "content_chars=%d chunks=%d tool_msgs=%d",
                    reason, _diag_exit_extra, request.tool_choice, _has_tool_results, _diag_fr,
                    _diag_saw_tc, _diag_chars, _diag_chunks,
                    sum(1 for m in messages if m.get("role") == "tool"),
                )
                _finalized_tool_calls = finalize_stream_tool_calls(_file_tc_acc)
                for _ftc in _finalized_tool_calls:
                    for _touch in extract_file_touches(_ftc["name"], _ftc["arguments"]):
                        asyncio.create_task(_record_file_touch(
                            chat_id, _touch["path"], _touch["action"], _touch["tool"],
                        ))
                # Tool-eval structured log — mirrors services/pipeline/anthropic.py's
                # call (_anthropic_tool_handler). Confirmed live: this OpenAI-format
                # passthrough (Odysseus, OpenCode, any tools-array client) never wrote
                # to tool_eval.jsonl at all despite importing _log_tool_eval, so a
                # user whose tool-calling traffic goes entirely through this path saw
                # zero entries in the Admin UI's "Tool Call Evaluation" page — the
                # Anthropic/Claude-Code path was the only one actually logging.
                if reason != "client-disconnected":
                    _log_tool_eval({
                        "ts":              datetime.utcnow().isoformat() + "Z",
                        "chat_id":         chat_id,
                        "model":           tool_model,
                        "node":            tool_base_url,
                        "input_type":      "text",
                        "tools_available": len(request.tools or []),
                        "output_type":     _diag_fr,
                        "tools_called":    [t["name"] for t in _finalized_tool_calls],
                        "tool_call_count": len(_finalized_tool_calls),
                        "has_text":        bool(_diag_chars),
                        "latency_s":       round(time.monotonic() - _t_stream_start, 3),
                        "tool_choice_sent": request.tool_choice,
                        "format_detected": "openai_tool_passthrough",
                    })
                # Only for turns that actually completed — a client-disconnect
                # exit isn't a real "how long did the model take" sample.
                if reason != "client-disconnected":
                    asyncio.create_task(_record_node_latency(
                        tool_base_url, tool_model, (time.monotonic() - _t_stream_start) * 1000,
                    ))
                # Confirmed live: this path never wrote to usage_log at all
                # (unlike the Claude Code tool path), so a user whose daily
                # traffic is entirely OpenCode/OpenAI-format tool calls saw
                # zero entries in the Portal's usage history from the moment
                # they switched away from native/interactive requests.
                # prompt/completion tokens are logged as 0 — see the
                # user_id/api_key_id/session_id docstring note above.
                if reason != "client-disconnected" and user_id != "anon":
                    asyncio.create_task(_log_usage_to_db(
                        user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
                        model=tool_model, moe_mode="tool_passthrough",
                        prompt_tokens=0, completion_tokens=0,
                        session_id=session_id,
                        status="ok" if (_diag_saw_tc or _diag_chars) else "empty",
                    ))

            async def _resolve_text_only_ending():
                # Called once, only for a turn that ended with no tool_calls
                # anywhere in the stream — decide whether to retry, and yield
                # whatever should reach the client (held-back original lines,
                # or a retried result, or a ping keepalive while retrying).
                nonlocal _diag_exit_extra
                _full_text = "".join(_content_acc)
                _violated_required = bool(request.tools) and request.tool_choice == "required"
                _premature = looks_like_premature_stop(_full_text)
                # Confirmed live: a turn can end with finish_reason=stop, no
                # tool_calls, AND zero characters of content — the model
                # returned literally nothing. looks_like_premature_stop always
                # returns False on an empty string (nothing to pattern-match
                # against), so this was silently passing through with no
                # retry at all — arguably a worse failure than a premature
                # announcement, since the client gets no signal whatsoever
                # that anything happened. This path always has request.tools
                # set (Augmented Tool Path only), so "no text, no tool call"
                # is never a valid outcome here.
                _empty_response = not _full_text.strip()
                # Persist regardless of whether a retry fires — the rate needs
                # both premature and clean turns to be meaningful, and this is
                # the only place this signal exists at all today (see
                # services/tracking.py::_record_premature_stop_outcome).
                asyncio.create_task(_record_premature_stop_outcome(
                    tool_model, tool_base_url, _premature or _empty_response,
                ))
                if _violated_required or _premature or _empty_response:
                    logger.warning(
                        "tool_choice=%r violated by %s (%s, content_chars=%d)"
                        " — retrying with template tool_agent",
                        request.tool_choice, tool_model,
                        "required" if _violated_required else
                        ("empty-response" if _empty_response else "premature-stop-heuristic"),
                        len(_full_text),
                    )
                    _retry_result = None
                    async for _kind, _val in _retry_tool_agent_fallback(
                        user_experts, tool_model, messages, request.tools, num_ctx,
                        request.max_tokens or request.max_completion_tokens,
                    ):
                        if _kind == "ping":
                            yield ": ping\n\n"
                        else:
                            _retry_result = _val
                    if _retry_result and (_retry_result["tool_calls"] or _retry_result["content"]):
                        logger.info(
                            "tool-choice retry done — model=%s tool_calls=%s content_chars=%d",
                            _retry_result["model"], bool(_retry_result["tool_calls"]),
                            len(_retry_result["content"]),
                        )
                        _fr = "tool_calls" if _retry_result["tool_calls"] else "stop"
                        _completion = {
                            "id": chat_id, "created": int(time.time()), "model": _retry_result["model"],
                            "choices": [{"index": 0, "finish_reason": _fr, "message": {
                                "role": "assistant", "content": _retry_result["content"],
                                "tool_calls": [
                                    {
                                        "id": f"call_{chat_id[:8]}_{i}", "type": "function",
                                        "function": {
                                            "name": (tc.get("function") or {}).get("name", ""),
                                            "arguments": json.dumps((tc.get("function") or {}).get("arguments", {})),
                                        },
                                    }
                                    for i, tc in enumerate(_retry_result["tool_calls"])
                                ],
                            }}],
                            "usage": {},
                        }
                        async for _sse_line in _wrap_completion_as_sse(_completion, chat_id, tool_model):
                            yield _sse_line
                        for _tc in _completion["choices"][0]["message"]["tool_calls"]:
                            _fn = _tc["function"]
                            try:
                                _args = json.loads(_fn["arguments"])
                            except Exception:
                                _args = {}
                            for _touch in extract_file_touches(_fn["name"], _args):
                                asyncio.create_task(_record_file_touch(
                                    chat_id, _touch["path"], _touch["action"], _touch["tool"],
                                ))
                        _diag_exit_extra = ",retried-ok"
                        return
                    _diag_exit_extra = ",retry-failed-or-unavailable"
                elif _full_text.strip():
                    # Confirmed live: a text-only ending that matches no known
                    # premature-stop pattern and isn't empty still passes
                    # through unretried — this is exactly the gap a *new*,
                    # not-yet-catalogued announcement phrasing would slip
                    # through. Log the actual text (never done elsewhere —
                    # only content_chars counts are logged) so a genuine
                    # silent-stop here can be diagnosed and turned into a new
                    # /patterns entry, instead of only ever seeing "164
                    # chars, no retry" with no way to tell what happened.
                    logger.info(
                        "tool-passthrough text-only ending, no retry triggered — model=%s content=%r",
                        tool_model, _full_text[:500],
                    )
                    asyncio.create_task(record_and_classify_tool_ending(
                        chat_id, tool_model, _full_text,
                        sum(1 for m in messages if m.get("role") == "tool"),
                    ))
                # No retry needed, or none available/it didn't help — flush
                # the original response as-is (never silently drop a reply).
                for _hl in _held_lines:
                    yield _hl
                yield "data: [DONE]\n\n"

            try:
                async with httpx.AsyncClient(timeout=1800) as client:
                    async with client.stream(
                        "POST",
                        _tool_endpoint,
                        json=payload,
                        headers={"Authorization": f"Bearer {tool_token}",
                                 "Content-Type": "application/json"},
                    ) as resp:
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if line.startswith("data: "):
                                _diag_chunks += 1
                                _payload = line[len("data: "):]
                                _has_tc_this_line = False
                                if _payload != "[DONE]":
                                    try:
                                        _d = json.loads(_payload)
                                        _ch0 = (_d.get("choices") or [{}])[0]
                                        _delta = _ch0.get("delta", {})
                                        if _delta.get("tool_calls"):
                                            _diag_saw_tc = True
                                            _has_tc_this_line = True
                                            for _tcd in _delta["tool_calls"]:
                                                accumulate_stream_tool_call_delta(
                                                    _file_tc_acc, _tcd.get("index", 0), _tcd,
                                                )
                                        if _delta.get("content"):
                                            _diag_chars += len(_delta["content"])
                                            _content_acc.append(_delta["content"])
                                        if _ch0.get("finish_reason"):
                                            _diag_fr = _ch0["finish_reason"]
                                    except Exception:
                                        pass
                                if _flushed_live or _has_tc_this_line:
                                    if not _flushed_live and _held_lines:
                                        for _hl in _held_lines:
                                            yield _hl
                                        _held_lines = []
                                    _flushed_live = True
                                    yield f"{line}\n\n"
                                else:
                                    _held_lines.append(f"{line}\n\n")
                            elif line == "data: [DONE]":
                                _diag_done_seen = True
                                if _flushed_live:
                                    yield "data: [DONE]\n\n"
                                else:
                                    async for _out in _resolve_text_only_ending():
                                        yield _out
                                _log_and_finalize("done")
                                return
                        # The upstream line iterator exhausted (connection closed
                        # by the server, aiter_lines() returning normally) without
                        # ever sending a literal "data: [DONE]" — this is Ollama's
                        # normal behaviour on this endpoint (confirmed live: every
                        # successful turn observed in production exits this way,
                        # never via an explicit [DONE]), NOT a truncated read. The
                        # full response was received either way, so the exact same
                        # retry decision applies as the "done" branch — this was a
                        # real, confirmed-live bug: the very first production case
                        # of finish_reason=stop/no-tool-calls exited through this
                        # branch and skipped the retry entirely because it used to
                        # just flush-and-return here without ever considering it.
                        if not _diag_done_seen:
                            if _flushed_live:
                                for _hl in _held_lines:
                                    yield _hl
                                # Upstream never sent an explicit [DONE] on this
                                # exit path — our own contract with the client is
                                # standard SSE termination, so always supply one
                                # regardless of what upstream did.
                                yield "data: [DONE]\n\n"
                            else:
                                async for _out in _resolve_text_only_ending():
                                    yield _out
                            _log_and_finalize("stream-ended-no-done")
            except (asyncio.CancelledError, GeneratorExit):
                # The client (OpenCode/Hermes/...) disconnected or gave up before
                # the stream finished — Starlette cancels this generator. Without
                # this handler that cancellation was completely invisible: no
                # diagnostic, no error log, nothing — which is indistinguishable
                # from "everything worked" when scanning logs for problems. Must
                # re-raise so asyncio's own cancellation bookkeeping still happens.
                _log_and_finalize("client-disconnected")
                raise
            except Exception as e:
                logger.error(f"Tool-synthesis stream failed: {e}")
                err_chunk = {
                    "id": chat_id, "object": "chat.completion.chunk",
                    "created": int(time.time()), "model": tool_model,
                    "choices": [{"index": 0, "delta": {
                        "content": f"[Stream error: {e}]"}, "finish_reason": "stop"}],
                }
                yield f"data: {json.dumps(err_chunk)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(_stream_tool_synthesis(), media_type="text/event-stream")

    # Slow path: buffer full response (used for tool-call turns and non-streaming clients).
    # Timeout is generous (1800s) to accommodate large models like qwen3.6:35b
    # which run at ~55 tok/s — quality over speed.
    try:
        async with httpx.AsyncClient(timeout=1800) as client:
            r = await client.post(
                _tool_endpoint,
                json=payload,
                headers={"Authorization": f"Bearer {tool_token}",
                         "Content-Type": "application/json"},
            )
            r.raise_for_status()
            upstream = r.json()
            # Ollama /api/chat returns {"message": {...}} not {"choices": [{"message": {...}}]}
            # Convert to OpenAI format so downstream processing works correctly.
            if _is_hermes3 and "message" in upstream and "choices" not in upstream:
                upstream = {
                    "id": chat_id,
                    "object": "chat.completion",
                    "model": tool_model,
                    "choices": [{
                        "index": 0,
                        "finish_reason": "stop",
                        "message": upstream["message"],
                    }],
                    "usage": upstream.get("usage", {}),
                }
                logger.info("🔄 Converted Ollama /api/chat response to OpenAI format")
    except Exception as e:
        logger.error(f"Tool-calls passthrough failed: {e}")
        upstream = error_response

    upstream = _normalise_tool_calls_response(upstream, chat_id, tool_model)

    # ── Diagnostic: visibility into every passthrough turn's outcome.
    # INFO only for the notable case (model stopped without ever calling a
    # tool — the exact silent-stop failure shape this diagnostic exists to
    # catch); DEBUG otherwise, now that it fires on every tool-calling turn.
    _diag_choice = (upstream.get("choices") or [{}])[0]
    _diag_msg = _diag_choice.get("message", {})
    _diag_notable = _diag_choice.get("finish_reason") == "stop" and not _diag_msg.get("tool_calls")
    (logger.info if _diag_notable else logger.debug)(
        "tool-passthrough result: tool_choice=%r has_tool_results=%s finish_reason=%r "
        "has_tool_calls=%s content_chars=%d tool_msgs=%d",
        request.tool_choice, _has_tool_results, _diag_choice.get("finish_reason"),
        bool(_diag_msg.get("tool_calls")), len(_diag_msg.get("content") or ""),
        sum(1 for m in messages if m.get("role") == "tool"),
    )
    for _tc in (_diag_msg.get("tool_calls") or []):
        _tc_fn = _tc.get("function") or {}
        try:
            _tc_args = json.loads(_tc_fn.get("arguments") or "{}")
        except Exception:
            _tc_args = {}
        for _touch in extract_file_touches(_tc_fn.get("name", ""), _tc_args):
            asyncio.create_task(_record_file_touch(
                chat_id, _touch["path"], _touch["action"], _touch["tool"],
            ))

    # ── tool_choice=required / premature-stop retry ────────────────────────
    # Ollama does not enforce tool_choice — a model can ignore "required" and
    # answer with a plain-text announcement ("I will now …") plus a clean
    # finish_reason=stop instead of a real tool call. Under tool_choice=auto
    # (confirmed via the diagnostic above to be what OpenCode actually sends)
    # a text-only reply is technically valid, but the SAME announcement
    # pattern still occurs — looks_like_premature_stop catches that case too.
    # An OpenAI-format client (OpenCode, Hermes, ...) then treats either as a
    # normal completed turn and stops silently, with no error anywhere.
    # Mirrors the retry already used by the Claude-Code tool path in
    # services/pipeline/anthropic.py. NOT restricted to the initial task turn
    # — OpenCode keeps sending the full tool list on every follow-up turn
    # too, deep into a long multi-step session (observed tool_msgs in the
    # 100s), so a text-only reply on a mid-loop turn is just as much a
    # silent-stop bug as on the first turn.
    if request.tools and not _diag_msg.get("tool_calls"):
        _violated_required = request.tool_choice == "required"
        _diag_content = _diag_msg.get("content") or ""
        _premature = looks_like_premature_stop(_diag_content)
        # See the matching comment in the fast/stream path above — an empty
        # response (no text, no tool call) never matches any premature-stop
        # pattern by design, so it was silently passing through unretried.
        _empty_response = not _diag_content.strip()
        asyncio.create_task(_record_premature_stop_outcome(
            tool_model, tool_base_url, _premature or _empty_response,
        ))
        if _violated_required or _premature or _empty_response:
            logger.warning(
                "tool_choice=%r violated by %s (%s, content_chars=%d)"
                " — retrying with template tool_agent",
                request.tool_choice, tool_model,
                "required" if _violated_required else
                ("empty-response" if _empty_response else "premature-stop-heuristic"),
                len(_diag_content),
            )
            _rr_result = None
            async for _kind, _val in _retry_tool_agent_fallback(
                user_experts, tool_model, messages, request.tools, num_ctx,
                request.max_tokens or request.max_completion_tokens,
            ):
                if _kind == "result":
                    _rr_result = _val
                # No client to ping yet on this (non-streaming-to-client-so-far)
                # path — pings are only meaningful once we've started sending
                # bytes, which happens below via _wrap_completion_as_sse.
            if _rr_result and (_rr_result["tool_calls"] or _rr_result["content"]):
                logger.info(
                    "tool-choice retry done — model=%s tool_calls=%s content_chars=%d",
                    _rr_result["model"], bool(_rr_result["tool_calls"]), len(_rr_result["content"]),
                )
                if _rr_result["tool_calls"]:
                    # Ollama native returns arguments as a dict, not a JSON string.
                    _diag_msg["tool_calls"] = [
                        {
                            "id":   f"call_{chat_id[:8]}_{i}",
                            "type": "function",
                            "function": {
                                "name": (tc.get("function") or {}).get("name", ""),
                                "arguments": json.dumps((tc.get("function") or {}).get("arguments", {})),
                            },
                        }
                        for i, tc in enumerate(_rr_result["tool_calls"])
                    ]
                    _diag_choice["finish_reason"] = "tool_calls"
                _diag_msg["content"] = _rr_result["content"]
        elif _diag_content.strip():
            # See the matching branch in the fast/stream path above.
            logger.info(
                "tool-passthrough text-only ending, no retry triggered — model=%s content=%r",
                tool_model, _diag_content[:500],
            )
            asyncio.create_task(record_and_classify_tool_ending(
                chat_id, tool_model, _diag_content,
                sum(1 for m in messages if m.get("role") == "tool"),
            ))

    # Slow-path usage logging — same gap as the fast/stream path's
    # _log_and_finalize (services/pipeline/anthropic.py's tool handler has
    # always done this; this passthrough proxy never did). Real token
    # counts when the upstream response carries them (OpenAI-compatible
    # /chat/completions does; Ollama's native /api/chat does not — see the
    # Hermes3 conversion above, which only copies a "usage" key that
    # Ollama's native response doesn't actually have, so this is 0 for
    # Hermes3 turns and real for everything else).
    if user_id != "anon":
        _slow_usage = upstream.get("usage") or {}
        asyncio.create_task(_log_usage_to_db(
            user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
            model=tool_model, moe_mode="tool_passthrough",
            prompt_tokens=_slow_usage.get("prompt_tokens", 0),
            completion_tokens=_slow_usage.get("completion_tokens", 0),
            session_id=session_id,
        ))

    # Tool-eval structured log for the non-streaming (slow) path — same gap
    # as the fast/stream path's _log_and_finalize above: this passthrough
    # proxy never wrote to tool_eval.jsonl at all.
    _slow_choice = (upstream.get("choices") or [{}])[0]
    _slow_msg = _slow_choice.get("message") or {}
    _log_tool_eval({
        "ts":              datetime.utcnow().isoformat() + "Z",
        "chat_id":         chat_id,
        "model":           tool_model,
        "node":            tool_base_url,
        "input_type":      "text",
        "tools_available": len(request.tools or []),
        "output_type":     _slow_choice.get("finish_reason"),
        "tools_called":    [
            (tc.get("function") or {}).get("name", "")
            for tc in (_slow_msg.get("tool_calls") or [])
        ],
        "tool_call_count": len(_slow_msg.get("tool_calls") or []),
        "has_text":        bool(_slow_msg.get("content")),
        "latency_s":       round(time.monotonic() - _handle_tool_calls_t0, 3),
        "tool_choice_sent": request.tool_choice,
        "format_detected": "openai_tool_passthrough",
    })

    if not request.stream:
        return upstream

    # Client requested streaming (SSE) — wrap the single non-streaming
    # response as a minimal SSE stream so clients using stream=True
    # (e.g. Hermes) receive the expected text/event-stream format.
    return StreamingResponse(
        _wrap_completion_as_sse(upstream, chat_id, tool_model), media_type="text/event-stream",
    )


def _build_diagnostic_metadata(result: dict) -> dict:
    """Additive, non-standard response metadata for benchmarking/observability.

    Callers must merge this via resp.setdefault("metadata", {}).update(...),
    never a direct assignment, so it can never clobber the "sources" or
    "candidate" metadata keys set elsewhere in chat_completions().
    """
    return {
        "self_critique_round": int(result.get("self_critique_round") or 0),
        "trust_score": result.get("trust_score"),
        "trust_verdict": result.get("trust_verdict") or None,
    }


async def chat_completions(raw_request: Request, request: ChatCompletionRequest):
    # The non-streaming orchestration timeout is an end-to-end request budget,
    # including auth, template resolution and dynamic routing before LangGraph.
    _request_started = time.monotonic()
    try:
        _client_max_output_tokens = int(
            request.max_tokens or request.max_completion_tokens or 0
        )
    except (TypeError, ValueError):
        _client_max_output_tokens = 0
    if _client_max_output_tokens < 0:
        _client_max_output_tokens = 0
    from main import stream_response, _is_openwebui_internal, _handle_internal_direct, _stream_native_llm
    async def _ol_start(*a, **kw): return None   # lineage owned by moe-codex
    async def _ol_complete(*a, **kw): pass
    async def _ol_fail(*a, **kw): pass
    def dataset_user_query(*a, **kw): return {}
    def dataset_response(*a, **kw): return {}
    chat_id    = f"chatcmpl-{uuid.uuid4()}"
    current_chat_id.set(chat_id)
    session_id = _extract_session_id(raw_request)
    _ol_run_id = await _ol_start(
        "chat_completion",
        inputs=[dataset_user_query(session_id or "")],
        extra_facets={"requestModel": {"_producer": "https://github.com/h3rb3rn/moe-sovereign",
                                       "_schemaURL": "moe-sovereign://requestModel",
                                       "model": request.model}},
    )

    # IP-based rate limit (pre-auth, guards against credential bruteforce & DoS)
    if not await _check_ip_rate_limit(raw_request):
        return JSONResponse(status_code=429, content={"error": {
            "message": "Rate limit exceeded — too many requests from this IP",
            "type": "rate_limit_error", "code": "rate_limit_exceeded",
        }}, headers={"Retry-After": "60"})

    # Auth
    raw_key  = _extract_api_key(raw_request)
    # ── Diagnostic auth log ──────────────────────────────────────────────────
    # key_id is a truncated SHA-256 of the raw key: enough to correlate log
    # lines for "the same key" across requests without ever writing out any
    # part of the actual secret. A raw prefix (the previous version of this
    # block) is partial key material, not a safe identifier — a moe-sk- key's
    # first 10 chars meaningfully narrows the brute-force space, and
    # PROJECT_COMPLIANCE.md forbids logging credential fragments regardless
    # (GAP_REPORT_2026-09-11.md, GAP-11).
    import hashlib as _hashlib
    _auth_hdr   = raw_request.headers.get("authorization", "")
    _xapi_hdr   = raw_request.headers.get("x-api-key", "")
    _hdr_source = (
        "authorization-bearer" if _auth_hdr.lower().startswith("bearer ") else
        "x-api-key"             if _xapi_hdr else
        "authorization-other"   if _auth_hdr else
        "none"
    )
    _key_id     = _hashlib.sha256(raw_key.encode()).hexdigest()[:12] if raw_key else "none"
    _key_len    = len(raw_key or "")
    _is_moe_sk  = bool(raw_key and raw_key.startswith("moe-sk-"))
    _origin_ip  = raw_request.client.host if raw_request.client else "?"
    logger.warning(
        "🔍 chat-auth-debug ip=%s hdr_source=%s key_id=%s key_len=%d is_moe_sk=%s model=%r",
        _origin_ip, _hdr_source, _key_id, _key_len, _is_moe_sk, request.model,
    )
    # ── End diagnostic block ───────────────────────────────────────────────
    user_ctx = await _validate_api_key(raw_key) if raw_key else {"error": "invalid_key"}
    if "error" in user_ctx:
        if user_ctx["error"] == "budget_exceeded":
            return JSONResponse(status_code=429, content={"error": {
                "message": f"Budget exceeded ({user_ctx.get('limit_type', 'unknown')} limit reached)",
                "type": "insufficient_quota", "code": "budget_exceeded"
            }})
        # Diagnostic: surface why _validate_api_key rejected the key
        logger.warning(
            "🔍 chat-auth-rejected ip=%s reason=%s key_len=%d is_moe_sk=%s",
            _origin_ip, user_ctx.get("error", "?"), _key_len, _is_moe_sk,
        )
        return JSONResponse(status_code=401, content={"error": {
            "message": "Invalid or missing API key", "type": "invalid_request_error", "code": "invalid_api_key"
        }})
    user_id      = user_ctx.get("user_id", "anon")
    api_key_id   = user_ctx.get("key_id", "")
    user_perms   = json.loads(user_ctx.get("permissions_json", "{}"))
    # Resolved once per request (permission flag > key flag > global env) and
    # frozen onto AgentState — every outbound LLM call the graph makes for this
    # request must respect it, not just dynamic-router template compilation.
    from services.sovereignty import resolve_local_only
    local_only = resolve_local_only(user_perms, user_ctx)

    # Template names and MoE mode IDs take precedence over native endpoints.
    # Wildcard permissions (*@node) would otherwise intercept template names and
    # route them as direct Ollama calls (model does not exist → empty response).
    _req_raw = request.model
    # Strip optional " [tag1, tag2]" suffix that /v1/models appends to the 'name' field
    # for Open-WebUI display. If Open-WebUI sends the 'name' value as model ID, the suffix
    # would break routing because it hides the @connname and doesn't match models_cache IDs.
    _req_raw = re.sub(r'\s+\[.*?\]\s*$', '', _req_raw).strip()
    _req_at = _req_raw.rindex("@") if "@" in _req_raw else -1
    _req_model_base = _req_raw[:_req_at] if _req_at >= 0 else _req_raw
    _req_node_hint  = _req_raw[_req_at + 1:] if _req_at >= 0 else None
    _all_tmpls = _read_expert_templates()
    _matched_tmpl = next((t for t in _all_tmpls if t.get("name") == request.model), None)
    _tmpl_override: Optional[str] = _matched_tmpl["id"] if _matched_tmpl else None
    mode = _MODEL_ID_TO_MODE.get(request.model, "default")

    # User-owned templates (stored in Valkey, not in admin DB) are invisible to the
    # admin template lookup above. When a user selects their own template, _tmpl_override
    # stays None and the native-endpoint block below incorrectly intercepts the request,
    # routing it as a direct LLM call (native mode) instead of through the MoE pipeline.
    # Fix: detect user templates early and set _tmpl_override so the pipeline is used.
    #
    # Also runs when an ADMIN template happens to share the exact same name but was
    # never actually granted to this key — confirmed live: user "horndev"
    # (moe-sk-e24f2c9eb) owned a template named identically to an unrelated,
    # ungranted admin template ("moe-n04-rtx-qwen3.6:35b-256k"). The name match
    # above unconditionally claimed _tmpl_override, silently shadowing the
    # user's own working template and failing authorization against the
    # inaccessible admin one instead ("Template '...' is not authorized for
    # this API key"). A same-named admin template this key isn't authorized
    # for is, from this key's point of view, indistinguishable from one that
    # doesn't exist — so an owned template match always takes priority over
    # an unauthorized same-name admin collision. If no owned template matches
    # either, _tmpl_override is left untouched (the admin match, if any),
    # preserving the existing "not authorized" 403 for the case where the
    # collision isn't actually resolvable by an owned template.
    _tmpl_override_authorized = bool(_tmpl_override) and _tmpl_override in user_perms.get("expert_template", [])
    if not _tmpl_override_authorized:
        _early_user_tmpls: dict = {}
        try:
            _early_user_tmpls = json.loads(user_ctx.get("user_templates_json", "{}") or "{}")
        except Exception:
            pass
        if _early_user_tmpls:
            # Match by template name (display name) or by template ID.
            # Also try _req_model_base (the name without any "@node" suffix) because
            # Open WebUI appends "@connectionname" to model IDs when a user selects a
            # template — e.g. "nff-test@MY_ENDPOINT" → base name is "nff-test".
            for _ut_id, _ut_cfg in _early_user_tmpls.items():
                _ut_name = _ut_cfg.get("name", "")
                if (
                    _ut_name == request.model          # exact match with full model string
                    or _ut_name == _req_model_base     # match without @node suffix
                    or _ut_id  == request.model        # match by template ID
                    or _ut_id  == _req_model_base
                ):
                    _tmpl_override = _ut_id
                    logger.debug("User template detected: '%s' (req='%s') → override=%s",
                                 _ut_name or _ut_id, request.model, _ut_id)
                    break
            # Also check templates referenced in the user's expert_template permissions
            if not _tmpl_override:
                _early_perms = json.loads(user_ctx.get("permissions_json", "{}") or "{}")
                for _perm_tid in _early_perms.get("expert_template", []):
                    if _perm_tid in _early_user_tmpls:
                        _ut_cfg = _early_user_tmpls[_perm_tid]
                        _ut_name = _ut_cfg.get("name", "")
                        if (
                            _ut_name == request.model
                            or _ut_name == _req_model_base
                            or _perm_tid == request.model
                            or _perm_tid == _req_model_base
                        ):
                            _tmpl_override = _perm_tid
                            logger.debug("User template detected via permissions: %s", _perm_tid)
                            break

    # Benchmark node reservation: reject non-benchmark requests when nodes are locked.
    # Fails open if Redis is unavailable so a Redis outage never blocks all traffic.
    if state.redis_client is not None:
        try:
            _bench_reserved = await redis_client.smembers("moe:benchmark_reserved")
            if _bench_reserved:
                _bench_meta = await redis_client.hgetall("moe:benchmark_lock_meta") or {}
                _bench_tmpl = _bench_meta.get("template", "")
                if request.model != _bench_tmpl:
                    # Allow templates whose experts run exclusively on non-reserved nodes.
                    # Collect the endpoint names used by this template's experts.
                    _tmpl_nodes: set = set()
                    if _matched_tmpl:
                        for _exp in (_matched_tmpl.get("experts") or {}).values():
                            for _em in (_exp.get("models") or []):
                                _ep = _em.get("endpoint", "")
                                if _ep:
                                    _tmpl_nodes.add(_ep)
                    # Block only if the template uses reserved nodes, or has no node info
                    # (unknown template — block by default for safety).
                    if not _tmpl_nodes or _tmpl_nodes.intersection(_bench_reserved):
                        return JSONResponse(status_code=503, content={"error": {
                            "message": (
                                f"Service temporarily unavailable: inference nodes are reserved "
                                f"for benchmark run (template: {_bench_tmpl}). "
                                "Please retry after the benchmark completes."
                            ),
                            "type": "service_unavailable",
                            "code": "benchmark_lock_active",
                            "benchmark_template": _bench_tmpl,
                        }})
        except Exception:
            pass  # Fail open — never block traffic due to Redis errors

    # If the matched template sets force_think=true and no explicit mode was requested,
    # upgrade to agent_orchestrated so the thinking_node activates before routing.
    # (Resolved after _tmpl_prompts is populated below; pre-read here to avoid circular dep.)
    _user_tmpls_json = user_ctx.get("user_templates_json", "{}")
    if _tmpl_override:
        _early_tmpl = next((t for t in _all_tmpls if t.get("id") == _tmpl_override), None)
        if _early_tmpl and _early_tmpl.get("force_think") and mode == "default":
            mode = "agent_orchestrated"

    # Native LLM? check model_endpoint permission — only if no template and no MoE mode
    # Supports "model_name" (legacy) and "model_name@node" (new, OpenWebUI format)
    _native_endpoint: Optional[dict] = None
    _user_conns_map: dict = {}
    try:
        _user_conns_map = json.loads(user_ctx.get("user_connections_json", "{}") or "{}")
    except Exception:
        pass
    if not _tmpl_override and request.model not in _MODEL_ID_TO_MODE:
        # Two-pass: exact matches first, wildcards second — prevents *@AIHUB from shadowing
        # specific entries like qwen2.5:7b-ctx128k@N04-RTX that appear later in the list.
        _ep_entries = user_perms.get("model_endpoint", [])
        for _wildcard_pass in (False, True):
            for _ep_entry in _ep_entries:
                _ep_model, _, _ep_node = _ep_entry.partition("@")
                if _ep_node not in URL_MAP:
                    continue
                _is_wildcard = _ep_model == "*"
                if _is_wildcard != _wildcard_pass:
                    continue
                if (_ep_model == _req_model_base or _is_wildcard) and \
                   (_req_node_hint is None or _req_node_hint == _ep_node):
                    _native_endpoint = {
                        "url":      URL_MAP[_ep_node],
                        "token":    TOKEN_MAP.get(_ep_node, "ollama"),
                        "model":    _req_model_base,
                        "node":     _ep_node,
                        "api_type": API_TYPE_MAP.get(_ep_node, "ollama"),
                        "timeout":  TIMEOUT_MAP.get(_ep_node, 300),
                    }
                    break
            if _native_endpoint:
                break
        # Fallback: user-owned private connections (lower priority than global URL_MAP)
        if not _native_endpoint and _user_conns_map:
            if _req_node_hint and _req_node_hint in _user_conns_map:
                _uc = _user_conns_map[_req_node_hint]
                _native_endpoint = {
                    "url":        _uc["url"],
                    "token":      _uc.get("api_key") or "ollama",
                    "model":      _req_model_base,
                    "node":       _req_node_hint,
                    "api_type":   _uc.get("api_type", "openai"),
                    "timeout":    _uc.get("timeout", 300),
                    "_user_conn": True,
                }
            elif not _req_node_hint:
                # Bare model name: check models_cache of each user connection.
                # models_cache stores rich dicts {id, tags, ...} — match on the 'id' field.
                for _cname, _uc in _user_conns_map.items():
                    _mc = _uc.get("models_cache", [])
                    _mc_ids = {
                        (m.get("id", "") if isinstance(m, dict) else str(m))
                        for m in _mc
                    }
                    if _req_model_base in _mc_ids:
                        _native_endpoint = {
                            "url":        _uc["url"],
                            "token":      _uc.get("api_key") or "ollama",
                            "model":      _req_model_base,
                            "node":       _cname,
                            "api_type":   _uc.get("api_type", "openai"),
                            "timeout":    _uc.get("timeout", 300),
                            "_user_conn": True,
                        }
                        break  # first matching connection wins


    # Resolved template info for live monitoring (populated when dynamic routing fires).
    _resolved_tmpl_name: str = ""
    _resolved_tmpl_id:   str = ""

    # ── Dynamic Router Integration ───────────────────────────────────────────
    # Triggers when:
    #   • global DYNAMIC_ROUTER_ENABLED env-var is set, OR
    #   • this API key has dynamic_routing=1 in its context, OR
    #   • the requested model is the explicit "moe-auto" virtual model
    # Explicit "model@node" selections (Open WebUI native-model picker) express
    # clear native-LLM intent and must never be hijacked into a dynamically
    # generated expert template — even when the per-key dynamic_routing flag
    # is set. Only the literal "moe-auto" virtual model may still trigger
    # dynamic routing for such requests.
    _key_dynamic_routing = user_ctx.get("dynamic_routing") == "1"
    _is_moe_auto = request.model == "moe-auto"
    _native_model_selected = _req_node_hint is not None and not _is_moe_auto
    _moe_auto_trivial_preflight = False
    if _is_moe_auto and TRIVIAL_FAST_PATH_ENABLED:
        from complexity_estimator import estimate_complexity

        _user_positions = [
            index for index, message in enumerate(request.messages)
            if message.role == "user"
        ]
        _last_user_position = _user_positions[-1] if _user_positions else -1
        _last_user_message = (
            request.messages[_last_user_position]
            if _last_user_position >= 0
            else None
        )
        _preflight_prompt = (
            _oai_content_to_str(_last_user_message.content)
            if _last_user_message
            else ""
        )
        _has_conversation_history = any(
            message.role in ("user", "assistant", "tool")
            for message in request.messages[:_last_user_position]
        )
        _has_multimodal_content = any(
            message.role == "user" and not isinstance(message.content, str)
            for message in request.messages
        )
        _request_system_prompt = "\n".join(
            _oai_content_to_str(message.content)
            for message in request.messages
            if message.role == "system"
        )
        _moe_auto_trivial_preflight = is_moe_auto_preflight_eligible(
            _preflight_prompt,
            estimate_complexity(_preflight_prompt),
            mode=mode,
            has_history=_has_conversation_history,
            has_multimodal=_has_multimodal_content,
            system_prompt=_request_system_prompt,
            tools=request.tools,
            files=request.files,
        )
        if _moe_auto_trivial_preflight:
            logger.info(
                "⚡ moe-auto trivial preflight: dynamic template compilation skipped"
            )
    # Computed at most once below (dynamic-router branch only) and reused for
    # both the AgentState field and the RouteLLM step inside get_dynamic_template
    # — see services/routing_patterns.py for what consumes it downstream.
    _query_embedding: List[float] = []
    if (
        not _native_model_selected
        and not _moe_auto_trivial_preflight
        and (
            os.getenv("DYNAMIC_ROUTER_ENABLED", "false").lower() in ("1", "true", "yes")
            or _key_dynamic_routing
            or _is_moe_auto
        )
    ):
        is_default_moe = request.model in ("moe-orchestrator", "moe-orchestrator-code", "moe-orchestrator-concise", "moe-orchestrator-agent", "moe-orchestrator-agent-orchestrated")
        if not _tmpl_override or is_default_moe or _is_moe_auto:
            from services.dynamic_router import get_dynamic_template, get_bge_embedding, ROUTELLM_ENABLED
            user_msgs = [m for m in request.messages if m.role == "user"]
            last_prompt = _oai_content_to_str(user_msgs[-1].content) if user_msgs else ""

            # ── Constraint resolution (priority: permission > key-flag > global env-var) ──
            _moe_modes_granted = set(user_perms.get("moe_mode", []))
            # local_only is resolved once for the whole request above (via
            # resolve_local_only) and reused here unchanged.
            # global_only: restrict router to global admin connections only
            _perm_global_only = "moe-auto:global-only" in _moe_modes_granted
            # user_conns_only: restrict router to user-created private connections only
            _perm_user_conns_only = "moe-auto:user-conns-only" in _moe_modes_granted
            _auto_deliberation_activation = MOE_AUTO_DELIBERATION_ACTIVATION
            if "moe-auto:no-deliberation" in _moe_modes_granted:
                _auto_deliberation_activation = "disabled"
            elif "moe-auto:deliberation-required" in _moe_modes_granted:
                _auto_deliberation_activation = "required"

            _user_conns_for_router: dict = {}
            if not _perm_global_only:
                try:
                    _user_conns_for_router = json.loads(
                        user_ctx.get("user_connections_json", "{}") or "{}"
                    )
                except Exception:
                    _user_conns_for_router = {}
            # ─────────────────────────────────────────────────────────────────────

            # Computed once and reused for both RouteLLM (inside
            # get_dynamic_template) and the Thompson-sampler cold-start prior
            # (services/routing_patterns.py) — avoids a second moe-embed call.
            if last_prompt and (ROUTING_PATTERN_PRIOR_ENABLED or ROUTELLM_ENABLED):
                _embed = await get_bge_embedding(last_prompt)
                if _embed is not None:
                    _query_embedding = _embed.tolist()

            dynamic_tmpl = await get_dynamic_template(
                last_prompt,
                local_only=local_only,
                user_connections=_user_conns_for_router,
                global_only=_perm_global_only,
                user_conns_only=_perm_user_conns_only,
                deliberation_activation=_auto_deliberation_activation,
                query_embedding=_query_embedding or None,
            )
            if dynamic_tmpl:
                try:
                    user_tmpls = json.loads(_user_tmpls_json or "{}")
                except Exception:
                    user_tmpls = {}
                tmpl_id = dynamic_tmpl["id"]
                user_tmpls[tmpl_id] = dynamic_tmpl
                _user_tmpls_json = json.dumps(user_tmpls)
                _tmpl_override = tmpl_id
                _resolved_tmpl_name = dynamic_tmpl.get("name", "")
                _resolved_tmpl_id   = tmpl_id
    # ──────────────────────────────────────────────────────────────────────────

    # For direct template requests (not via moe-auto routing), look up the display name.
    if _tmpl_override and not _resolved_tmpl_id:
        _resolved_tmpl_id = _tmpl_override
        _direct_t = next((t for t in _all_tmpls if t.get("id") == _tmpl_override), None)
        if _direct_t:
            _resolved_tmpl_name = _direct_t.get("name", "")

    # Template name match is NOT authorization — check expert_template permissions explicitly.
    # Exception: user-owned templates (present in user_templates_json) are authorized by ownership.
    if _tmpl_override:
        _allowed_tmpls = user_perms.get("expert_template", [])
        _owned_tmpls: dict = {}
        try:
            _owned_tmpls = json.loads(_user_tmpls_json or "{}")
        except Exception:
            pass
        if _tmpl_override not in _allowed_tmpls and _tmpl_override not in _owned_tmpls:
            return JSONResponse(status_code=403, content={"error": {
                "message": f"Template '{request.model}' is not authorized for this API key",
                "type": "permission_denied",
                "code": "template_not_authorized",
            }})
    _user_conns_json = user_ctx.get("user_connections_json", "{}")
    try:
        if _moe_auto_trivial_preflight:
            # An absent override normally selects the first template granted to the
            # API key.  That implicit default would repopulate ``user_experts`` and
            # negate the preflight which deliberately skipped dynamic compilation.
            # Resolve against empty permissions so the graph receives the global,
            # context-free defaults expected by its trivial fast-path contract.
            user_experts = {}
            _tmpl_prompts = _resolve_template_prompts(
                "{}",
                user_templates_json="{}",
                user_connections_json=_user_conns_json,
            )
        else:
            user_experts = _resolve_user_experts(
                user_ctx.get("permissions_json", ""),
                override_tmpl_id=_tmpl_override,
                user_templates_json=_user_tmpls_json,
                admin_override=False,
                user_connections_json=_user_conns_json,
            ) or {}
            _tmpl_prompts = _resolve_template_prompts(
                user_ctx.get("permissions_json", ""),
                override_tmpl_id=_tmpl_override,
                user_templates_json=_user_tmpls_json,
                admin_override=False,
                user_connections_json=_user_conns_json,
            )
    except DeliberationPolicyError as exc:
        logger.warning("Rejected invalid deliberation policy for template=%s", _tmpl_override or "default")
        return JSONResponse(status_code=422, content={"error": {
            "message": str(exc),
            "type": "invalid_template",
            "code": "deliberation_policy_invalid",
        }})
    # Ghost-template detection: template in permissions but absent from DB and not user-owned
    if _tmpl_override and not user_experts and not user_perms.get("expert_template") == []:
        _ghost_check = next((t for t in _all_tmpls if t.get("id") == _tmpl_override), None)
        if _ghost_check is None and _tmpl_override not in _owned_tmpls:
            logger.error("Ghost template detected: %s in permissions but not in DB", _tmpl_override)
            return JSONResponse(status_code=422, content={"error": {
                "message": f"Template '{_tmpl_override}' is configured but no longer exists in the database",
                "type": "template_not_found",
                "code": "ghost_template",
            }})

    # Extract system message (coding agents send file/codebase context here)
    system_msgs   = [m for m in request.messages if m.role == "system"]
    system_prompt = _oai_content_to_str(system_msgs[0].content) if system_msgs else ""

    # Mission Context injection — prepend compact project summary to the system prompt
    # when enabled system-wide AND the active template does not opt out.
    if (
        _starfleet.is_feature_enabled_sync("mission_context")
        and _tmpl_prompts.get("enable_mission_context", False)
    ):
        try:
            _mc = await _mission_context.get_context()
            _mc_title = (_mc.get("title") or "").strip()
            if _mc_title:
                _mc_lines = [f"## Mission Context: {_mc_title}"]
                if _mc.get("description"):
                    _mc_lines.append(_mc["description"].strip())
                if _mc.get("open_tasks"):
                    _mc_lines.append("Open tasks: " + "; ".join(_mc["open_tasks"][:5]))
                if _mc.get("recent_decisions"):
                    last = _mc["recent_decisions"][-1]
                    _mc_lines.append(f"Last decision: {last.get('text', '')}")
                _mc_block = "\n".join(_mc_lines)
                system_prompt = f"{_mc_block}\n\n{system_prompt}" if system_prompt else _mc_block
        except Exception:
            pass

    # Tier-3 Context Index: trigger background indexing for large system prompts.
    # Hermes and OpenCode reach the MoE graph via this path, so this is the
    # single place that ensures all agentic tools share the same context gateway.
    if CC_CONTEXT_INDEX_ENABLED and session_id and system_prompt and state.redis_client:
        try:
            from services.context_index import ensure_indexed as _ensure_ctx_indexed
            await _ensure_ctx_indexed(session_id, system_prompt, state.redis_client)
        except Exception as _ci_exc:
            logger.debug("chat: context indexing skipped: %s", _ci_exc)

    # Last user message as the actual query
    user_msgs  = [m for m in request.messages if m.role in ("user", "assistant")]
    last_user  = next((m for m in reversed(request.messages) if m.role == "user"), None)
    _user_images = _extract_oai_images(last_user.content) if last_user else []
    allowed_skills = user_perms.get("skill")  # None = all allowed (backwards compatible)
    _raw_user_input = _oai_content_to_str(last_user.content) if last_user else ""
    user_input = await _resolve_skill_secure(_raw_user_input, allowed_skills, user_id=user_id, session_id=session_id)
    # Shadow-Mode: sample every BENCHMARK_SHADOW_RATE-th request to the candidate template.
    # Fire-and-forget — never blocks the production response.
    if BENCHMARK_SHADOW_TEMPLATE:
        import services.helpers as _h
        with _shadow_lock:
            _h._shadow_request_counter += 1
            _fire_shadow = (_h._shadow_request_counter % BENCHMARK_SHADOW_RATE == 0)
        if _fire_shadow:
            asyncio.create_task(_shadow_request(_raw_user_input, user_id, raw_key or ""))
            logger.debug(f"🔬 Shadow request enqueued (counter={_h._shadow_request_counter})")

    _pending_reports: List[str] = []
    if user_input != _raw_user_input:
        # Match both /skill and the API-escape form $$/skill
        _sm = re.match(r"^\$?\$?/([a-zA-Z0-9][a-zA-Z0-9\-]*)", _raw_user_input)
        _sname = _sm.group(1) if _sm else "?"
        _sargs = _raw_user_input[len(_sname)+1:].strip() if _sm else ""
        _pending_reports.append(
            f"🎯 Skill /{_sname} resolved (args: '{_sargs}', {len(user_input)} chars):\n{user_input}"
        )
    else:
        # No manual skill command → auto-detect whether a known file is attached.
        # Skip file-skill detection when the message already contains an explicit routing
        # directive (e.g. from the GAIA benchmark runner or structured data injection).
        # The directive "[ROUTE TO: reasoning OR general" signals that the caller has
        # already classified the content and no automatic skill dispatch should occur.
        _routing_bypass = (
            "[ROUTE TO: reasoning OR general" in _raw_user_input
            or "[ROUTING: Use reasoning or general expert" in _raw_user_input
        )
        _auto_skill = None if _routing_bypass else _detect_file_skill(
            request.files, _raw_user_input, allowed_skills
        )
        if _auto_skill:
            _auto_input = f"/{_auto_skill} {_raw_user_input}"
            _resolved = await _resolve_skill_secure(_auto_input, allowed_skills, user_id=user_id, session_id=session_id)
            if _resolved != _auto_input:  # skill exists and was resolved
                user_input = _resolved
                _pending_reports.append(f"📎 File skill /{_auto_skill} triggered automatically")

    # Open WebUI internal requests (follow-ups, title, autocomplete) directly without pipeline
    # Skip in agent mode — coding tools do not send OpenWebUI-internal markers
    if mode != "agent" and _is_openwebui_internal(request.messages):
        return await _handle_internal_direct(request.messages, chat_id, request.stream)

    # Model availability check: does the requested model actually exist on the target node?
    # Prevents hanging requests when a model is requested via wildcard permission,
    # but is not present on the host (e.g. gemma4:31b@MY_ENDPOINT even though it is not installed).
    # Skipped for user-owned connections — their models_cache already served that check.
    if _native_endpoint and not _native_endpoint.get("_user_conn"):
        _avail_models = await _get_available_models_svc(_native_endpoint["node"])
        if _avail_models is not None and _native_endpoint["model"] not in _avail_models:
            _avail_list = sorted(_avail_models)
            logger.warning(
                "Model-Not-Found: '%s' not available on %s. Available: %s",
                _native_endpoint["model"], _native_endpoint["node"], _avail_list,
            )
            return JSONResponse(
                status_code=404,
                content={"error": {
                    "message": (
                        f"Model '{_native_endpoint['model']}' is not available on "
                        f"{_native_endpoint['node']}. "
                        f"Available models: {_avail_list}"
                    ),
                    "type":  "invalid_request_error",
                    "code":  "model_not_found",
                }},
            )

    # Live monitoring: register request as active
    _tmpl_name = request.model if _tmpl_override else ""
    _req_type  = "streaming" if request.stream else "batch"
    _req_type  = "native"    if _native_endpoint else _req_type
    _client_ip = raw_request.client.host if raw_request.client else ""
    # Await registration so every subsequent error/return path can reliably
    # remove the exact entry; fire-and-forget registration races used to
    # recreate keys after a fast failure had already deregistered them.
    await _register_active_request(
        chat_id=chat_id, user_id=user_id, model=_req_model_base,
        moe_mode=("native" if _native_endpoint else mode),
        req_type=_req_type, template_name=_tmpl_name, client_ip=_client_ip,
        backend_model=_native_endpoint["model"] if _native_endpoint else "",
        backend_host=_native_endpoint["node"]  if _native_endpoint else "",
        api_key_id=api_key_id,
        resolved_tmpl_name=_resolved_tmpl_name,
        resolved_tmpl_id=_resolved_tmpl_id,
    )

    # Conversation history: only user/assistant messages (no system messages)
    # Multimodal content is extracted as text (history for MoE pipeline is text-only)
    raw_history = [
        {"role": m.role, "content": _oai_content_to_str(m.content)}
        for m in request.messages
        if m.role in ("user", "assistant") and m != last_user
    ]
    _hist_turns = _tmpl_prompts.get("history_max_turns", 0) or None
    _hist_chars = _tmpl_prompts.get("history_max_chars", 0) or None
    history = _truncate_history(raw_history, max_turns=_hist_turns, max_chars=_hist_chars)
    _sm_team_ids: List[str] = []
    _sm_user_prefs: dict = {}
    if _tmpl_prompts.get("enable_semantic_memory") and user_id:
        try:
            from admin_ui.database import (
                get_user_teams        as _get_user_teams,
                get_user_memory_prefs as _get_mem_prefs,
            )
            if _tmpl_prompts.get("enable_cross_session_memory"):
                _sm_team_ids = await _get_user_teams(user_id)
            _sm_user_prefs = await _get_mem_prefs(user_id)
        except Exception:
            pass
    history = await _apply_semantic_memory(
        raw_history, history, user_input, session_id,
        enabled=_tmpl_prompts.get("enable_semantic_memory", False),
        user_id=user_id,
        team_ids=_sm_team_ids,
        cross_session_enabled=_tmpl_prompts.get("enable_cross_session_memory", False),
        cross_session_scopes=_tmpl_prompts.get("cross_session_scopes", ["private"]),
        n_results=_tmpl_prompts.get("semantic_memory_n_results", 0),
        ttl_hours=_tmpl_prompts.get("semantic_memory_ttl_hours", 0),
        prefer_fresh=_sm_user_prefs.get("prefer_fresh", False),
        share_with_team=_sm_user_prefs.get("share_with_team", False),
    )

    # Native LLM: forward directly to endpoint, no MoE pipeline
    if _native_endpoint:
        # Sovereignty guard: this "native model@node" passthrough dispatches
        # directly via httpx, entirely outside the app_graph pipeline (see
        # _stream_native_llm below and the non-streaming httpx call further
        # down) — the local_only enforcement wired into graph/expert.py and
        # services/inference.py does not cover it. Checked once here, before
        # either the streaming or non-streaming branch can reach the network.
        from services.sovereignty import assert_egress_allowed, EgressDenied
        try:
            assert_egress_allowed(_native_endpoint["url"], local_only)
        except EgressDenied as _native_egress_exc:
            return JSONResponse(status_code=403, content={"error": {
                "message": str(_native_egress_exc),
                "type": "permission_error",
                "code": "local_only_violation",
            }})
        _native_started = time.monotonic()
        _native_is_user_conn = bool(_native_endpoint.get("_user_conn"))
        if request.stream:
            from services.model_capabilities import (
                enforce_streaming_capability,
            )
            if not enforce_streaming_capability(
                _native_endpoint["model"], True
            ):
                logger.info(
                    "Model %s does not support streaming; forcing stream=false",
                    _native_endpoint["model"],
                )
                request.stream = False
        # Per-key Ollama num_ctx override — 0 means use the model's Modelfile default.
        _native_num_ctx = int(user_ctx.get("native_num_ctx") or 0)
        if request.stream:
            return StreamingResponse(
                _stream_native_llm(request, chat_id, _native_endpoint, user_id, request.model,
                                   session_id=session_id, is_user_conn=_native_is_user_conn,
                                   num_ctx=_native_num_ctx),
                media_type="text/event-stream",
            )
        # Non-streaming native: blockierender httpx-Call
        _ep_api_type = _native_endpoint.get("api_type", "ollama")
        _ns_msgs = []
        for _nsm in request.messages:
            if _nsm.role == "tool":
                _nd = {"role": "tool", "content": _oai_content_to_str(_nsm.content) if _nsm.content else ""}
                if _nsm.tool_call_id:
                    _nd["tool_call_id"] = _nsm.tool_call_id
                if _nsm.name:
                    _nd["name"] = _nsm.name
            elif _nsm.role == "assistant" and _nsm.tool_calls:
                def _norm_tc_ns(tc):
                    if not isinstance(tc, dict):
                        return tc
                    fn = tc.get("function", {})
                    args = fn.get("arguments")
                    if isinstance(args, str):
                        try:
                            import json as _j; args = _j.loads(args)
                        except Exception:
                            pass
                    return {**tc, "function": {**fn, "arguments": args}}
                _nd = {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [_norm_tc_ns(tc) for tc in (_nsm.tool_calls or [])],
                }
                if _nsm.content:
                    _nd["content"] = _oai_content_to_str(_nsm.content)
            else:
                _nd = {"role": _nsm.role, "content": _nsm.content if _nsm.content is not None else ""}
                if _nsm.name:
                    _nd["name"] = _nsm.name
            _ns_msgs.append(_nd)
        _NS_THINKING_PREFIXES = ("qwen3", "gemma4", "qwq")
        _ns_model = _native_endpoint["model"].lower()
        _ns_use_native = (
            _ep_api_type == "ollama"
            and any(t in _ns_model for t in _NS_THINKING_PREFIXES)
        )
        if _ns_use_native:
            # Native Ollama /api/chat with think:false — mirrors the streaming path.
            # Ollama ≤ 0.30.x ignores think:false on the OpenAI-compat /v1 path.
            _ns_base = _native_endpoint["url"].rstrip("/")
            if _ns_base.endswith("/v1"):
                _ns_base = _ns_base[:-3]
            # Inject tool-use directive (mirrors streaming path logic).
            _ns_has_tools = bool(getattr(request, "tools", None))
            _ns_has_tool_results = any(m.role == "tool" for m in request.messages)
            if _ns_has_tools:
                _ns_avail_names = ", ".join(
                    t.get("function", {}).get("name") or t.get("name", "")
                    for t in (request.tools or [])
                    if isinstance(t, dict)
                ) or "the tools listed above"
                _ns_directive = (
                    "CRITICAL TOOL-USE RULES:\n"
                    f"1. ONLY these tools exist: {_ns_avail_names}. "
                    "Services like PDFSpark, ConvertAPI, pdftemp.vip, pdf.co or ANY external PDF/file API "
                    "DO NOT EXIST in this environment.\n"
                    "2. For PDF creation: use `generate_file` with format='pdf'. "
                    "For HTML pages: format='html'. For Word: format='docx'. "
                    "The returned URL starts with https://files.moe-sovereign.org — the ONLY valid download domain.\n"
                    "3. NEVER invent a download URL. Any URL not returned by a real tool call is fabricated.\n"
                    "4. Make tool calls IMMEDIATELY — no preamble, no 'I will now...'.\n"
                    "5. If a needed capability is not in the tool list, state this clearly."
                )
                if not _ns_has_tool_results:
                    if _ns_msgs and _ns_msgs[0].get("role") == "system":
                        _ns_msgs[0] = {**_ns_msgs[0], "content": _ns_directive + "\n\n" + _ns_msgs[0]["content"]}
                    else:
                        _ns_msgs.insert(0, {"role": "system", "content": _ns_directive})
                _ns_prev_text = sum(
                    1 for _m in _ns_msgs
                    if _m.get("role") == "assistant" and not _m.get("tool_calls")
                    and len((_m.get("content") or "")) > 200
                )
                for _ns_di in range(len(_ns_msgs) - 1, -1, -1):
                    if _ns_msgs[_ns_di].get("role") == "user":
                        _ns_user_txt = (_ns_msgs[_ns_di].get("content") or "").strip()
                        _ns_short_fu = _ns_prev_text >= 1 and len(_ns_user_txt) < 50
                        if _ns_short_fu:
                            _ns_rule = (
                                "[AGENT RULE: The previous turn already contained a complete answer. "
                                "Respond DIRECTLY and briefly — no tools needed.]"
                            )
                        else:
                            _ns_rule = (
                                "[AGENT RULE: Choose ONE of these two options right now:\n"
                                "A) Call a tool immediately (no announcement, just the call).\n"
                                "B) Write the complete final answer right now.\n"
                                "FORBIDDEN: Writing 'I will...', 'Let me...', 'I'm going to...' without an actual tool call. "
                                "Only use tools from the approved list — never invent tool or API names.]"
                            )
                        _ns_msgs[_ns_di] = {
                            **_ns_msgs[_ns_di],
                            "content": _ns_user_txt + "\n\n" + _ns_rule,
                        }
                        break
            _ns_payload: dict = {
                "model":      _native_endpoint["model"],
                "messages":   _ns_msgs,
                "stream":     False,
                "think":      False,
                # No explicit keep_alive — respects each Ollama instance's
                # own server-configured OLLAMA_KEEP_ALIVE default instead of
                # silently overriding it.
            }
            _ns_opts: dict = {}
            if _native_num_ctx > 0:
                _ns_opts["num_ctx"] = _native_num_ctx
            _ns_eff_max = request.max_tokens or request.max_completion_tokens
            if _ns_eff_max:
                _ns_opts["num_predict"] = _ns_eff_max
            if request.temperature is not None:
                _ns_opts["temperature"] = request.temperature
            if request.top_p is not None:
                _ns_opts["top_p"] = request.top_p
            if request.seed is not None:
                _ns_opts["seed"] = request.seed
            if request.stop is not None:
                _ns_opts["stop"] = request.stop if isinstance(request.stop, list) else [request.stop]
            if request.frequency_penalty is not None:
                _ns_opts["repeat_penalty"] = 1.0 + request.frequency_penalty
            if request.presence_penalty is not None:
                _ns_opts["presence_penalty"] = request.presence_penalty
            if _ns_opts:
                _ns_payload["options"] = _ns_opts
            if _ns_has_tools:
                _ns_payload["tools"] = request.tools
            _native_audit = _audit_create(
                session_id or "",
                chat_id,
                _native_endpoint["model"],
                _ns_base + "/api/chat",
                "native_direct",
                _ns_payload,
            )
            async def _ns_do_post() -> httpx.Response:
                # Shielded: a client disconnect must not cancel an in-flight
                # model load on the shared Ollama node. Ollama's GPU-discovery
                # startup does not handle a cancelled load cleanly and gets
                # stuck ("GPU discovery watchdog timed out") for every
                # subsequent request until the container is restarted -- this
                # keeps our own cancellation from ever triggering that.
                async with httpx.AsyncClient(timeout=float(_native_endpoint.get("timeout", 300))) as _hc:
                    return await _hc.post(
                        _ns_base + "/api/chat",
                        headers={"Authorization": f"Bearer {_native_endpoint['token']}", "Content-Type": "application/json"},
                        json=_ns_payload,
                    )
            try:
                _nr = await asyncio.shield(_ns_do_post())
                _nr.raise_for_status()
                _rdata = _nr.json()
                await _audit_complete(
                    _native_audit,
                    _rdata,
                    _rdata.get("prompt_eval_count"),
                    _rdata.get("eval_count"),
                )
            except asyncio.CancelledError:
                await _audit_cancel(_native_audit)
                raise
            except Exception as _native_exc:
                await _audit_complete(
                    _native_audit,
                    {"error": str(_native_exc)},
                    None,
                    None,
                    "error",
                )
                raise
            # Convert native Ollama response to OpenAI format
            _rmsg = _rdata.get("message", {})
            _tool_calls_native = _rmsg.get("tool_calls") or []
            _oai_tool_calls = None
            if _tool_calls_native:
                import uuid as _uuid_mod
                _oai_tool_calls = []
                for _tci, _tc in enumerate(_tool_calls_native):
                    _fn = _tc.get("function", {})
                    _args = _fn.get("arguments", {})
                    _oai_tool_calls.append({
                        "id": f"call_{_uuid_mod.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": _fn.get("name", ""),
                            "arguments": json.dumps(_args) if isinstance(_args, dict) else str(_args),
                        },
                    })
            _nj = {
                "id":      chat_id,
                "object":  "chat.completion",
                "created": int(time.time()),
                "model":   _native_endpoint["model"],
                "choices": [{
                    "index":         0,
                    "message":       {
                        "role":    "assistant",
                        "content": _rmsg.get("content") or None,
                        **({"tool_calls": _oai_tool_calls} if _oai_tool_calls else {}),
                    },
                    "finish_reason": "tool_calls" if _oai_tool_calls else _rdata.get("done_reason", "stop"),
                }],
                "usage": {
                    "prompt_tokens":     _rdata.get("prompt_eval_count", 0),
                    "completion_tokens": _rdata.get("eval_count", 0),
                    "total_tokens":      (_rdata.get("prompt_eval_count", 0) + _rdata.get("eval_count", 0)),
                },
            }
        else:
            _non_stream_extra: dict = {}
            if _native_num_ctx > 0 and _ep_api_type == "ollama":
                _non_stream_extra["options"] = {"num_ctx": _native_num_ctx}
            if request.tools:
                _non_stream_extra["tools"] = request.tools
                if request.tool_choice:
                    _non_stream_extra["tool_choice"] = request.tool_choice
            # Forward all standard OpenAI parameters to the backend
            _ns_oai_params: dict = {}
            _ns_max_tok = request.max_tokens or request.max_completion_tokens
            if _ns_max_tok:
                _ns_oai_params["max_tokens"] = _ns_max_tok
            if request.temperature is not None:
                _ns_oai_params["temperature"] = request.temperature
            for _pname in ("top_p", "n", "stop", "presence_penalty", "frequency_penalty",
                           "seed", "user", "response_format", "logprobs", "top_logprobs",
                           "logit_bias", "parallel_tool_calls"):
                _pval = getattr(request, _pname, None)
                if _pval is not None:
                    _ns_oai_params[_pname] = _pval
            _native_oai_payload = {
                "model": _native_endpoint["model"],
                "messages": _ns_msgs,
                "stream": False,
                **_ns_oai_params,
                **_non_stream_extra,
            }
            _native_oai_url = (
                _native_endpoint["url"].rstrip("/").removesuffix("/v1") + "/v1/chat/completions"
            )
            _native_audit = _audit_create(
                session_id or "",
                chat_id,
                _native_endpoint["model"],
                _native_oai_url,
                "native_direct",
                _native_oai_payload,
            )
            async def _native_oai_do_post() -> httpx.Response:
                # Shielded for the same reason as the native-Ollama branch
                # above: a client disconnect must not cancel an in-flight
                # model load on the shared node.
                async with httpx.AsyncClient(timeout=float(_native_endpoint.get("timeout", 300))) as _hc:
                    return await _hc.post(
                        _native_oai_url,
                        headers={"Authorization": f"Bearer {_native_endpoint['token']}", "Content-Type": "application/json"},
                        json=_native_oai_payload,
                    )
            try:
                _nr = await asyncio.shield(_native_oai_do_post())
                _nr.raise_for_status()
                _nj = _nr.json()
                _native_usage = _nj.get("usage") or {}
                await _audit_complete(
                    _native_audit,
                    _nj,
                    _native_usage.get("prompt_tokens"),
                    _native_usage.get("completion_tokens"),
                )
            except asyncio.CancelledError:
                await _audit_cancel(_native_audit)
                raise
            except Exception as _native_exc:
                await _audit_complete(
                    _native_audit,
                    {"error": str(_native_exc)},
                    None,
                    None,
                    "error",
                )
                raise
        _nu = _nj.get("usage", {})
        _native_latency_ms = round(
            (time.monotonic() - _native_started) * 1000
        )
        if user_id != "anon":
            asyncio.create_task(_log_usage_to_db(user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
                model=request.model, moe_mode="native",
                prompt_tokens=_nu.get("prompt_tokens", 0), completion_tokens=_nu.get("completion_tokens", 0),
                session_id=session_id, latency_ms=_native_latency_ms))
            # User-owned connections are billed by the user's own provider — exclude from MoE budget.
            if not _native_is_user_conn:
                asyncio.create_task(_increment_user_budget(user_id, _nu.get("total_tokens", 0), prompt_tokens=_nu.get("prompt_tokens", 0), completion_tokens=_nu.get("completion_tokens", 0)))
        asyncio.create_task(_deregister_active_request(chat_id))
        _nj["id"] = chat_id
        await _ol_complete(_ol_run_id, job_name="chat_completion",
                           outputs=[dataset_response(chat_id)])
        return _nj

    _moe_resp_headers = {}
    if _tmpl_override:
        _moe_resp_headers["X-MoE-Template-Id"]   = _tmpl_override
        _moe_resp_headers["X-MoE-Template-Name"]  = _tmpl_name or request.model

    # Expose user token budget so callers can track their quota without a separate API call.
    if user_id and user_id != "anon" and state.redis_client is not None:
        try:
            from datetime import date as _date
            _today = _date.today().strftime("%Y-%m-%d")
            _used_tok = int(await redis_client.get(f"user:{user_id}:tokens:daily:{_today}") or 0)
            _moe_resp_headers["X-MoE-Budget-Daily-Used"] = str(_used_tok)
            _daily_limit = user_ctx.get("budget_daily")
            if _daily_limit:
                _moe_resp_headers["X-MoE-Budget-Daily-Limit"] = str(_daily_limit)
        except Exception:
            pass

    # --- Tool-Calling Passthrough ---
    # When the request carries `tools` (function definitions) or messages with
    # role='tool' (tool-result turns), skip the planner/experts/merger pipeline
    # and forward directly to the template's judge model. The judge (e.g.
    # qwen3.6:35b) natively supports OpenAI function-calling and returns
    # tool_calls that the client (e.g. Hermes) can act on.
    _has_tools = bool(request.tools) or any(
        getattr(m, "role", None) == "tool" for m in request.messages
    )
    # Augmented Tool Path: classify the turn once (initial_task vs mid_loop) and,
    # on the initial turn only, fetch GraphRAG context to inject into the tool
    # model's system prompt — opt-in per expert template (agent_graphrag),
    # default off. Mirrors the Anthropic-path wiring in services/pipeline/anthropic.py.
    _agent_system_augment = ""
    if _has_tools:
        await _record_stage(chat_id, "tool_entry", "started")
        _agent_sys_text = next(
            (getattr(m, "content", "") for m in request.messages if getattr(m, "role", None) == "system"),
            "",
        )
        _agent_turn = _classify_agent_turn(
            request.messages, request.tools, api="openai",
            user_id=user_id, system_text=_agent_sys_text if isinstance(_agent_sys_text, str) else "",
        )
        _agent_precision_required = bool(
            detect_required_precision_intents(_agent_turn.query)
        )

        # ── Augmented Tool Path: cache-read hook (opt-in, off by default) ──
        # Only served on the initial task turn for a genuinely informational
        # query (TurnInfo.cacheable) — tool_use decisions are never
        # cache-served. Excluded whenever the client forces tool use via
        # tool_choice, since it then expects a tool_use response, not text.
        if (
            _tmpl_prompts.get("agent_cache") and _agent_turn.kind == "initial_task"
            and _agent_turn.cacheable
            and request.tool_choice in (None, "auto")
            and not _agent_precision_required
        ):
            _agent_cache_hit = await agent_cache_lookup(
                _agent_turn.query, _agent_turn.scope,
                state.redis_client, state.agent_cache_collection, path="openai",
            )
            await _record_stage(chat_id, "agent_cache", "hit" if _agent_cache_hit else "miss")
            if _agent_cache_hit:
                logger.info(
                    "chat_completions: Augmented Tool Path cache hit (scope=%s, chars=%d)",
                    _agent_turn.scope, len(_agent_cache_hit),
                )
                _agent_hit_completion = {
                    "id": chat_id, "object": "chat.completion",
                    "created": int(time.time()), "model": request.model,
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": _agent_cache_hit}}],
                    "usage": {"prompt_tokens": 0,
                              "completion_tokens": len(_agent_cache_hit) // 4,
                              "total_tokens": len(_agent_cache_hit) // 4},
                }
                _agent_hit_headers = dict(_moe_resp_headers or {})
                _agent_hit_headers["X-MoE-Agent-Cache"] = "hit"
                if request.stream:
                    return StreamingResponse(
                        _wrap_completion_as_sse(_agent_hit_completion, chat_id, request.model),
                        media_type="text/event-stream", headers=_agent_hit_headers,
                    )
                return JSONResponse(content=_agent_hit_completion, headers=_agent_hit_headers)
        elif _tmpl_prompts.get("agent_cache") and _agent_precision_required:
            await _record_stage(
                chat_id,
                "agent_cache",
                "bypassed",
                "required_precision_intent",
            )

        if _tmpl_prompts.get("agent_graphrag") and state.graph_manager is not None and _agent_turn.query:
            _agent_sess_ctx_key = f"cc:graphctx:{session_id}" if session_id else ""
            try:
                if _agent_turn.kind == "initial_task":
                    _agent_tenant_ids = (
                        ([f"user:{user_id}"] if user_id and user_id != "anon" else [])
                        + [t for t in user_perms.get("graph_tenant", []) if t != f"user:{user_id}"]
                    )
                    _agent_tool_model = (
                        (_tmpl_prompts.get("tool_expert_model_override") or "").strip()
                        or (_tmpl_prompts.get("judge_model_override") or "").strip()
                        or JUDGE_MODEL
                    )
                    _agent_max_chars = min(
                        AGENT_GRAPHRAG_MAX_CHARS,
                        graphrag_budget_chars(_agent_tool_model, query_chars=len(_agent_turn.query)),
                    )
                    _agent_system_augment = await agent_graph_context(
                        _agent_turn.query, _agent_tenant_ids, session_id,
                        state.redis_client, state.graph_manager,
                        max_chars=_agent_max_chars, timeout_s=AGENT_GRAPHRAG_TIMEOUT_S,
                        user_id=user_id,
                    )
                    await _record_stage(chat_id, "agent_graphrag", "queried" if _agent_system_augment else "queried_empty")
                    logger.info(
                        "chat_completions: Augmented Tool Path GraphRAG %d chars (session=%s, tenant_ids=%s)",
                        len(_agent_system_augment), (session_id or "")[:8], _agent_tenant_ids,
                    )
                    if state.redis_client and _agent_sess_ctx_key:
                        if _agent_system_augment:
                            asyncio.create_task(
                                state.redis_client.setex(_agent_sess_ctx_key, 3600, _agent_system_augment)
                            )
                        else:
                            asyncio.create_task(state.redis_client.delete(_agent_sess_ctx_key))
                elif state.redis_client and _agent_sess_ctx_key:
                    _agent_cached = await state.redis_client.get(_agent_sess_ctx_key)
                    _agent_system_augment = (
                        (_agent_cached if isinstance(_agent_cached, str) else _agent_cached.decode())
                        if _agent_cached else ""
                    )
                    await _record_stage(chat_id, "agent_graphrag", "cached" if _agent_system_augment else "cached_empty")
            except Exception as _agent_ge:
                logger.debug("chat_completions: agent graph context skipped: %s", _agent_ge)
                _agent_system_augment = ""
        if _agent_system_augment:
            _agent_system_augment = (
                f"[KNOWLEDGE GRAPH — structured facts & past strategies]\n{_agent_system_augment}\n"
                "[End of knowledge graph context.]"
            )
    if _has_tools:
        # Prefer dedicated tool_expert over judge for tool-call passthrough.
        # tool_expert_model supports the same model@endpoint syntax as judge/planner
        # and can point to any agentic backend (Ollama, OpenCode, CC CLI endpoint).
        _raw_tool_expert = (_tmpl_prompts.get("tool_expert_model_override") or "").strip()
        _raw_judge       = (_tmpl_prompts.get("judge_model_override") or "").strip()
        _raw_tc          = _raw_tool_expert or _raw_judge
        _tc_model, _, _tc_ep = _raw_tc.partition("@")
        if _raw_tool_expert:
            _tc_url   = _tmpl_prompts.get("tool_expert_url_override") or URL_MAP.get(_tc_ep.strip(), "")
            _tc_token = _tmpl_prompts.get("tool_expert_token_override") or TOKEN_MAP.get(_tc_ep.strip(), "ollama")
        else:
            _tc_url   = _tmpl_prompts.get("judge_url_override") or URL_MAP.get(_tc_ep.strip(), "")
            _tc_token = _tmpl_prompts.get("judge_token_override") or TOKEN_MAP.get(_tc_ep.strip(), "ollama")
        if _tc_url and _tc_model:
            # Look up content model from template experts (prefer "general" expert).
            # Used by the two-phase kanban handler to synthesise answers via a
            # larger, cleaner model instead of the tool-calling model.
            _content_model, _content_url, _content_token = "", "", "ollama"
            _content_sys = ""
            if user_experts:
                for _exp_cat, _exp_models in user_experts.items():
                    if _exp_cat in ("tool_agent", "code"):
                        continue
                    if isinstance(_exp_models, list) and _exp_models:
                        _em = _exp_models[0]
                        if _em.get("model") and _em.get("url"):
                            _content_model = _em["model"]
                            _content_url   = _em["url"]
                            _content_token = _em.get("token", "ollama")
                            _content_sys   = _em.get("_system_prompt", "")
                            break
            _tc_slot = "tool_expert" if _raw_tool_expert else "judge"
            logger.info(
                f"🔧 Tool-calling passthrough ({_tc_slot}): model={_tc_model} stream={request.stream} "
                f"tools={len(request.tools or [])} tool_msgs={sum(1 for m in request.messages if getattr(m,'role','')=='tool')} "
                f"content_model={_content_model or 'none'}"
            )
            # The initial registration cannot know the resolved tool backend;
            # expose it in live monitoring once selection is complete.
            asyncio.create_task(_patch_active_request_backend(chat_id, _tc_model, _tc_url))
            _t_tc = time.monotonic()
            _tc_num_ctx = (
                _tmpl_prompts.get("tool_expert_num_ctx") or 0
                if _raw_tool_expert else
                _tmpl_prompts.get("judge_num_ctx") or 0
            )
            await _record_stage(chat_id, "tool_model_call", "started", _tc_model)
            _tc_resp = await _handle_tool_calls(
                request, chat_id, _tc_model, _tc_url, _tc_token,
                content_model=_content_model,
                content_url=_content_url,
                content_token=_content_token,
                content_system_prompt=_content_sys,
                num_ctx=int(_tc_num_ctx),
                system_augment=_agent_system_augment,
                user_experts=user_experts,
                user_id=user_id,
                api_key_id=api_key_id,
                session_id=session_id,
            )
            PROM_REQUESTS.labels(mode="tool", cache_hit="false",
                                 user_id=(user_id or "anon")).inc()
            PROM_RESPONSE_TIME.labels(mode="tool").observe(time.monotonic() - _t_tc)
            if not request.stream:
                # Streaming responses reach this line right after
                # _handle_tool_calls() returns a StreamingResponse wrapper —
                # before the client has consumed any of the actual
                # generation, so timing here would only measure setup, not
                # the real latency. The accurate streaming measurement is
                # taken inside _stream_tool_synthesis's _log_and_finalize
                # instead (services/pipeline/chat.py, _handle_tool_calls).
                asyncio.create_task(_record_node_latency(
                    _tc_url, _tc_model, (time.monotonic() - _t_tc) * 1000,
                ))

            # ── Augmented Tool Path: write-back (fire-and-forget) ─────────────
            # Opt-in (template agent_ingest, default off). Only on a clean turn
            # end: finish_reason=='stop', no tool_calls emitted.
            _agent_ingest_on = bool(_tmpl_prompts.get("agent_ingest") and _agent_turn.query)
            if _agent_ingest_on:
                _agent_tenant_id = f"user:{user_id}" if user_id and user_id != "anon" else None
                if isinstance(_tc_resp, StreamingResponse):
                    _tc_resp.body_iterator = _wrap_agent_writeback_sse(
                        _tc_resp.body_iterator, chat_id, _agent_turn.query, _agent_turn.scope,
                        _agent_tenant_id, user_id, _tc_model, session_id,
                    )
                else:
                    _tc_choice = (_tc_resp.get("choices") or [{}])[0]
                    _tc_msg = _tc_choice.get("message", {})
                    if (_tc_choice.get("finish_reason") == "stop"
                            and not _tc_msg.get("tool_calls") and _tc_msg.get("content")):
                        asyncio.create_task(_agent_writeback_traced(
                            chat_id,
                            _agent_turn.query, _tc_msg["content"], _agent_turn.scope,
                            _agent_tenant_id, user_id, _tc_model, session_id or "",
                            state.redis_client, state.agent_cache_collection, path="openai",
                        ))

            # ── Live-monitoring deregistration (fixes pre-existing gap) ───────
            # This branch previously had no _deregister_active_request call on
            # any return path — entries stayed "active" in the admin monitoring
            # table until their 2h TTL, even after the response was fully sent.
            if isinstance(_tc_resp, StreamingResponse):
                _tc_resp.body_iterator = _wrap_deregister_on_stream_end(
                    _tc_resp.body_iterator, chat_id,
                )
            else:
                asyncio.create_task(_deregister_active_request(chat_id))
                asyncio.create_task(_record_stage(chat_id, "tool_model_call", "done"))

            # _handle_tool_calls returns StreamingResponse for stream=True, dict otherwise
            if isinstance(_tc_resp, StreamingResponse):
                if _moe_resp_headers:
                    for _hk, _hv in _moe_resp_headers.items():
                        _tc_resp.headers[_hk] = _hv
                return _tc_resp
            if _moe_resp_headers:
                return JSONResponse(content=_tc_resp, headers=_moe_resp_headers)
            return _tc_resp
        logger.warning("⚠️ Tool-calling passthrough: no tool_expert or judge URL configured — falling through to pipeline")

    if request.stream:
        _inner = stream_response(user_input, chat_id, mode, chat_history=history,
                            system_prompt=system_prompt, user_id=user_id,
                            user_permissions=user_perms, user_experts=user_experts,
                            planner_prompt=_tmpl_prompts["planner_prompt"],
                            judge_prompt=_tmpl_prompts["judge_prompt"],
                            judge_model_override=_tmpl_prompts["judge_model_override"],
                            judge_url_override=_tmpl_prompts["judge_url_override"],
                            judge_token_override=_tmpl_prompts["judge_token_override"],
                            planner_model_override=_tmpl_prompts["planner_model_override"],
                            planner_url_override=_tmpl_prompts["planner_url_override"],
                            planner_token_override=_tmpl_prompts["planner_token_override"],
                            planner_num_ctx=_tmpl_prompts.get("planner_num_ctx", 0),
                            judge_num_ctx=_tmpl_prompts.get("judge_num_ctx", 0),
                            guardrail_prompt=_tmpl_prompts.get("guardrail_prompt", ""),
                            guardrail_model_override=_tmpl_prompts.get("guardrail_model_override", ""),
                            guardrail_url_override=_tmpl_prompts.get("guardrail_url_override", ""),
                            guardrail_token_override=_tmpl_prompts.get("guardrail_token_override", ""),
                            guardrail_num_ctx=_tmpl_prompts.get("guardrail_num_ctx", 0),
                            model_name=request.model,
                            pending_reports=_pending_reports,
                            images=_user_images,
                            session_id=session_id,
                            max_agentic_rounds=(
                                request.max_agentic_rounds
                                if request.max_agentic_rounds is not None
                                else _tmpl_prompts.get("max_agentic_rounds", 0)
                            ),
                            deliberation_policy=_tmpl_prompts.get("deliberation_policy", {}),
                            no_cache=request.no_cache,
                            client_max_output_tokens=_client_max_output_tokens,
                            local_only=local_only,
                            query_embedding=_query_embedding)

        async def _lineage_wrapped_stream():
            """Wrap the upstream generator so COMPLETE/FAIL fires when streaming ends."""
            try:
                async for _chunk in _inner:
                    yield _chunk
            except Exception as _stream_err:
                await _ol_fail(_ol_run_id, job_name="chat_completion",
                               error=str(_stream_err))
                raise
            else:
                await _ol_complete(_ol_run_id, job_name="chat_completion",
                                   outputs=[dataset_response(chat_id)])

        return StreamingResponse(
            _lineage_wrapped_stream(),
            media_type="text/event-stream",
            headers=_moe_resp_headers or None,
        )
    if state.app_graph is None:
        logger.warning("APP-GRAPH-NONE: state id=%d, app_graph=%s", id(state), state.app_graph)
        await _deregister_active_request(
            chat_id, {"status": "failed", "error_code": "graph_not_ready"}
        )
        return JSONResponse(status_code=503, content={"error": {"message": "Orchestrator graph not ready — retry in a few seconds", "type": "service_unavailable", "code": "graph_not_ready"}})
    _t_start = _request_started
    _remaining_timeout = max(
        0.001,
        ORCHESTRATION_TIMEOUT - (time.monotonic() - _request_started),
    )
    try:
        result = await asyncio.wait_for(state.app_graph.ainvoke(
        {"input": user_input, "response_id": chat_id, "mode": mode,
         "user_id": user_id, "api_key_id": api_key_id,
         "request_deadline_monotonic": _request_started + ORCHESTRATION_TIMEOUT,
         "client_max_output_tokens": _client_max_output_tokens,
         "expert_models_used": [], "prompt_tokens": 0, "completion_tokens": 0,
         "user_conn_prompt_tokens": 0, "user_conn_completion_tokens": 0,
         "chat_history": history, "reasoning_trace": "", "system_prompt": system_prompt,
         "images": _user_images,
         "query_embedding": _query_embedding,
         "user_permissions": user_perms, "user_experts": user_experts,
         "local_only_routing": local_only,
         # Prepend personal namespace so user-created knowledge is private by default.
         # graph_tenant permissions add team/tenant namespaces on top.
         "tenant_ids": ([f"user:{user_id}"] if user_id and user_id != "anon" else [])
                       + [t for t in user_perms.get("graph_tenant", [])
                          if t != f"user:{user_id}"],
         "provenance_sources": [],
         "retrieved_graph_chunks": [],
         "output_skill_body": "",
         "enable_cache": _tmpl_prompts.get("enable_cache", True),
         "enable_graphrag": _tmpl_prompts.get("enable_graphrag", True),
         "enable_web_research": _tmpl_prompts.get("enable_web_research", True),
         "complexity_level": _tmpl_prompts.get("complexity_level", ""),
         "deliberation_policy": _tmpl_prompts.get("deliberation_policy", {}),
         "deliberation_capacity": {},
         "deliberation_events": [],
         "search_fallback_ddg": _tmpl_prompts.get("search_fallback_ddg", _WEB_SEARCH_FALLBACK_DDG),
         "graphrag_max_chars": _tmpl_prompts.get("graphrag_max_chars", 0),
         "history_max_turns": _tmpl_prompts.get("history_max_turns", 0),
         "history_max_chars": _tmpl_prompts.get("history_max_chars", 0),
         "planner_prompt": _tmpl_prompts["planner_prompt"],
         "judge_prompt":   _tmpl_prompts["judge_prompt"],
         "judge_model_override":   _tmpl_prompts["judge_model_override"],
         "judge_url_override":     _tmpl_prompts["judge_url_override"],
         "judge_token_override":   _tmpl_prompts["judge_token_override"],
         "planner_model_override": _tmpl_prompts["planner_model_override"],
         "planner_url_override":   _tmpl_prompts["planner_url_override"],
         "planner_token_override": _tmpl_prompts["planner_token_override"],
         # Confirmed live: unlike the streaming branch above (stream_response(...,
         # planner_num_ctx=..., judge_num_ctx=...)), this non-streaming ainvoke()
         # dict never forwarded the template's context-window override at all —
         # state.get("judge_num_ctx"/"planner_num_ctx") was always empty here, so
         # _invoke_judge_with_retry/_invoke_planner_with_retry silently fell through
         # to the global JUDGE_NUM_CTX/PLANNER_NUM_CTX env default or the static
         # parameter-count heuristic, ignoring an explicit template override (e.g.
         # a 262144 context_window template loading its model at 16384 instead).
         # Every stream=False caller hit this — notably the Ontology Gap-Healer
         # (scripts/gap_healer_templates.py always sends stream=False).
         "planner_num_ctx": _tmpl_prompts.get("planner_num_ctx", 0),
         "judge_num_ctx":   _tmpl_prompts.get("judge_num_ctx", 0),
         "guardrail_prompt":         _tmpl_prompts.get("guardrail_prompt", ""),
         "guardrail_model_override": _tmpl_prompts.get("guardrail_model_override", ""),
         "guardrail_url_override":   _tmpl_prompts.get("guardrail_url_override", ""),
         "guardrail_token_override": _tmpl_prompts.get("guardrail_token_override", ""),
         "guardrail_num_ctx":        _tmpl_prompts.get("guardrail_num_ctx", 0),
         "template_name":  _tmpl_name,
         "template_id":    _tmpl_override or "",
         "causal_intervention": _tmpl_prompts.get("causal_intervention"),
         "pending_reports": _pending_reports,
         "max_agentic_rounds": _tmpl_prompts.get("max_agentic_rounds", 0),
         "agentic_iteration": 0,
         "agentic_history": [],
         "agentic_gap": "",
         "attempted_queries": [],
         "search_strategy_hint": "",
         "conflict_registry": [],
         "task_events": [],
         "mcp_evidence": [],
         "required_precision_intents": [],
         "precision_contract_snapshot": {},
         "precision_contract_hash": "",
         "precision_catalog_hash": "",
         "precision_cache_bypassed": False,
         "precision_direct": False,
         "precision_fact_slots": [],
         "precision_prompt_projection": "",
         "precision_rendered_response": "",
         "precision_binding_status": "not_required",
         "precision_binding_errors": [],
         "precision_binding_hash": "",
         "precision_bound_response_hash": "",
         "precision_hybrid_composed": False,
         "precision_hybrid_expert_body": "",
         "precision_hybrid_expert_task": "",
         "precision_hybrid_expert_confidence": "",
         "vector_confidence": 0.5,
         "graph_confidence": 0.5,
         "fuzzy_routing_scores": {},
         "no_cache": request.no_cache,
         # Pass explicit temperature into state so planner, judge and experts
         # can honour it. None means "use query-adaptive detection".
         "query_temperature": request.temperature,
         "trust_score": 0.0,
         "trust_verdict": "",
         "trust_factors": {},
         "self_critique_round": 0,
         "self_critique_max": int(os.getenv("SELF_CRITIQUE_MAX_ROUNDS", "2")),
         "constitution_violations": [],
         "cynefin_domain": "",
         "hitl_gate_id": "",
         "hitl_gate_reason": "",
         "quality_blocked": False,
         "quality_block_reason": "",
         "candidate_status": "normal",
         "candidate_reason": "",
         "quality_gate_status": "",
         "response_commit_context": {},
         "response_commit_status": "not_started",
         "response_commit_key": "",
         "response_commit_sinks": {},
         "response_commit_errors": [],
         },
        {"configurable": {"thread_id": str(uuid.uuid4())}},
        ), timeout=_remaining_timeout)
    except asyncio.TimeoutError:
        _elapsed_ms = round((time.monotonic() - _t_start) * 1000)
        from services.ai_io_audit import aggregate_request_usage
        from services.request_snapshot import consume_request_snapshot
        _partial_usage = await aggregate_request_usage(chat_id)
        _progress = consume_request_snapshot(chat_id)
        logger.error(
            "Orchestration timeout request=%s after %ss",
            chat_id, ORCHESTRATION_TIMEOUT,
        )
        await _deregister_active_request(
            chat_id,
            {
                "status": "timeout",
                "error_code": "orchestration_timeout",
                "latency_ms": _elapsed_ms,
                **_progress,
            },
        )
        if user_id != "anon":
            await _log_usage_to_db(
                user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
                model=MODES.get(mode, MODES["default"])["model_id"],
                moe_mode=mode,
                prompt_tokens=_partial_usage["prompt_tokens"],
                completion_tokens=_partial_usage["completion_tokens"],
                status="timeout", session_id=session_id,
                latency_ms=_elapsed_ms,
                complexity_level=_progress.get("complexity_level", ""),
                expert_domains=_progress.get("expert_domains", ""),
                trust_score=_progress.get("trust_score"),
                trust_verdict=_progress.get("trust_verdict"),
                cynefin_domain=_progress.get("cynefin_domain"),
            )
        await _ol_fail(
            _ol_run_id, job_name="chat_completion",
            error=f"orchestration timeout after {ORCHESTRATION_TIMEOUT}s",
        )
        return JSONResponse(
            status_code=504,
            content={"error": {
                "message": (
                    "The orchestrated request exceeded its configured "
                    f"{ORCHESTRATION_TIMEOUT}s execution limit."
                ),
                "type": "timeout_error",
                "code": "orchestration_timeout",
                "request_id": chat_id,
            }},
        )
    except Exception as exc:
        _elapsed_ms = round((time.monotonic() - _t_start) * 1000)
        from services.ai_io_audit import aggregate_request_usage
        from services.request_snapshot import consume_request_snapshot
        _partial_usage = await aggregate_request_usage(chat_id)
        _progress = consume_request_snapshot(chat_id)
        logger.exception("Orchestration failed request=%s", chat_id)
        await _deregister_active_request(
            chat_id,
            {
                "status": "failed",
                "error_code": "orchestration_failed",
                "error": str(exc)[:500],
                "latency_ms": _elapsed_ms,
                **_progress,
            },
        )
        if user_id != "anon":
            await _log_usage_to_db(
                user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
                model=MODES.get(mode, MODES["default"])["model_id"],
                moe_mode=mode,
                prompt_tokens=_partial_usage["prompt_tokens"],
                completion_tokens=_partial_usage["completion_tokens"],
                status="error", session_id=session_id,
                latency_ms=_elapsed_ms,
                complexity_level=_progress.get("complexity_level", ""),
                expert_domains=_progress.get("expert_domains", ""),
                trust_score=_progress.get("trust_score"),
                trust_verdict=_progress.get("trust_verdict"),
                cynefin_domain=_progress.get("cynefin_domain"),
            )
        await _ol_fail(_ol_run_id, job_name="chat_completion", error=str(exc))
        return JSONResponse(
            status_code=500,
            content={"error": {
                "message": "The orchestration pipeline failed.",
                "type": "server_error",
                "code": "orchestration_failed",
                "request_id": chat_id,
            }},
        )
    from services.request_snapshot import clear_request_snapshot
    clear_request_snapshot(chat_id)
    p_tok = result.get("prompt_tokens",     0)
    c_tok = result.get("completion_tokens", 0)
    # Prometheus: the streaming path records these in its finalizer; the
    # non-streaming branch must do it too, otherwise moe_requests_total and
    # the duration histogram stay flat for stream=false clients (load tests).
    _cache_hit_flag = bool(result.get("cache_hit", False))
    PROM_REQUESTS.labels(mode=mode, cache_hit=str(_cache_hit_flag).lower(),
                         user_id=(user_id or "anon")).inc()
    _elapsed_ms = round((time.monotonic() - _t_start) * 1000)
    PROM_RESPONSE_TIME.labels(mode=mode).observe(_elapsed_ms / 1000)
    if _resolved_tmpl_id.startswith("moe-dyn-"):
        from admin_ui.database import update_dynamic_template_feedback_metrics
        asyncio.create_task(update_dynamic_template_feedback_metrics(
            _resolved_tmpl_id, _elapsed_ms, p_tok + c_tok
        ))
    if user_id != "anon":
        asyncio.create_task(_log_usage_to_db(
            user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
            model=MODES.get(mode, MODES["default"])["model_id"],
            moe_mode=mode, prompt_tokens=p_tok, completion_tokens=c_tok,
            session_id=session_id,
            latency_ms=_elapsed_ms,
            complexity_level=result.get("complexity_level", ""),
            expert_domains=",".join(sorted({
                t.get("category", "")
                for t in (result.get("plan") or [])
                if isinstance(t, dict) and t.get("category")
            })),
            cache_hit=bool(result.get("cache_hit", False)),
            agentic_rounds=int(result.get("agentic_iteration") or 0),
            dynamic_tmpl_id=_resolved_tmpl_id if _resolved_tmpl_id.startswith("moe-dyn-") else "",
            trust_score=result.get("trust_score"),
            trust_verdict=result.get("trust_verdict"),
            cynefin_domain=result.get("cynefin_domain"),
            self_critique_round=int(result.get("self_critique_round") or 0),
            cascade_type=result.get("cascade_type"),
            structured_failure_round=int(
                result.get("structured_failure_round") or 0
            ),
        ))
        _uc_p = result.get("user_conn_prompt_tokens", 0)
        _uc_c = result.get("user_conn_completion_tokens", 0)
        _bill_p = max(0, p_tok - _uc_p)
        _bill_c = max(0, c_tok - _uc_c)
        asyncio.create_task(_increment_user_budget(user_id, _bill_p + _bill_c, prompt_tokens=_bill_p, completion_tokens=_bill_c))
        _plan = result.get("plan") or []
        _expert_domains = ",".join(sorted({
            t.get("category", "") for t in _plan if isinstance(t, dict) and t.get("category")
        }))
        asyncio.create_task(log_conversation(
            user_id=user_id,
            request_id=chat_id,
            messages=[{"role": m.role, "content": m.content or ""} for m in request.messages],
            response=result.get("final_response", ""),
            model=MODES.get(mode, MODES["default"])["model_id"],
            moe_mode=mode,
            session_id=session_id,
            prompt_tokens=p_tok,
            completion_tokens=c_tok,
            expert_domains=_expert_domains,
            cache_hit=bool(result.get("cache_hit", False)),
            agentic_rounds=int(result.get("agentic_round", 0)),
        ))
    from services.deliberation.runtime import summarize_deliberation_telemetry
    await _deregister_active_request(
        chat_id,
        summarize_deliberation_telemetry(
            result.get("deliberation_capacity"),
            result.get("deliberation_events"),
        ),
    )
    _gate_id = result.get("hitl_gate_id") or ""
    if _gate_id:
        _moe_resp_headers.update({
            "X-MoE-Gate-Id": _gate_id,
            "X-MoE-Quality": "pending",
        })
        await _ol_complete(
            _ol_run_id, job_name="chat_completion",
            outputs=[dataset_response(chat_id)],
        )
        return JSONResponse(
            status_code=202,
            headers=_moe_resp_headers,
            content={
                "id": chat_id,
                "object": "moe.hitl_gate",
                "status": "pending_human_approval",
                "gate_id": _gate_id,
                "reason": result.get("hitl_gate_reason", ""),
            },
        )
    if result.get("quality_blocked"):
        _moe_resp_headers["X-MoE-Quality"] = "blocked"
        await _ol_fail(
            _ol_run_id, job_name="chat_completion",
            error=result.get("quality_block_reason", "quality_blocked"),
        )
        return JSONResponse(
            status_code=422,
            headers=_moe_resp_headers,
            content={"error": {
                "message": "The response was withheld by the quality gate.",
                "type": "quality_blocked",
                "code": result.get("quality_block_reason", "quality_blocked"),
                "request_id": chat_id,
            }},
        )
    if result.get("candidate_status") == "degraded":
        _moe_resp_headers["X-MoE-Quality"] = "degraded"
        _moe_resp_headers["X-MoE-Candidate-Reason"] = (
            result.get("candidate_reason") or "budget"
        )[:120]
    resp = {
        "id":      chat_id,
        "object":  "chat.completion",
        "created": int(time.time()),
        "model":   MODES.get(mode, MODES["default"])["model_id"],
        "choices": [{"index": 0, "message": {"role": "assistant", "content": result["final_response"]}, "finish_reason": "stop"}],
        "usage":   {
            "prompt_tokens":     p_tok,
            "completion_tokens": c_tok,
            "total_tokens":      p_tok + c_tok,
        },
    }
    # Add provenance metadata if available (non-standard, backward-compatible)
    _prov = result.get("provenance_sources")
    if _prov:
        resp["metadata"] = {"sources": _prov}
    if result.get("candidate_status") == "degraded":
        resp.setdefault("metadata", {})["candidate"] = {
            "status": "degraded",
            "reason": result.get("candidate_reason", ""),
        }
    # Additive, non-standard diagnostic metadata for benchmarking/observability
    # (self-critique round count, trust score/verdict). Merged via update() so
    # it can never clobber the sources/candidate keys set above.
    resp.setdefault("metadata", {}).update(_build_diagnostic_metadata(result))
    if not request.no_cache:
        # Online quality probe (sampled): pipeline vs. single best expert.
        # Skipped for no_cache traffic so benchmark latency is not distorted.
        try:
            from services.quality_probe import run_probe as _qp_run
            asyncio.create_task(_qp_run(
                query=user_input, pipeline_answer=result["final_response"],
                experts=user_experts, planner_cfg=_tmpl_prompts,
                request_id=chat_id, user_id=user_id,
                pipeline_tokens=c_tok,
                graph_context=result.get("graph_context", ""),
                web_research=result.get("web_research", ""),
                mcp_result=result.get("mcp_result", ""),
            ))
        except Exception:
            pass
    await _ol_complete(_ol_run_id, job_name="chat_completion",
                       outputs=[dataset_response(chat_id)])
    if _moe_resp_headers:
        return JSONResponse(content=resp, headers=_moe_resp_headers)
    return resp
