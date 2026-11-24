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
    CC_CONTEXT_INDEX_ENABLED,
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
    _check_ip_rate_limit,
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
)
from services.templates import _read_expert_templates, _read_cc_profiles
from services.inference import _select_node as _select_node_svc, _get_available_models as _get_available_models_svc
from services.skills import _build_skill_catalog, _resolve_skill_secure, _detect_file_skill

logger = logging.getLogger("MOE-SOVEREIGN")
# Pydantic request models (defined here — used by chat_completions and routes)
# ---------------------------------------------------------------------------

class Message(BaseModel):
    role: str
    content: Optional[Any] = None

class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[Message]
    stream: bool = False
    tools: Optional[Any] = None
    tool_choice: Optional[Any] = None
    temperature: Optional[Any] = None
    max_tokens: Optional[Any] = None
    stream_options: Optional[Any] = None
    files: Optional[Any] = None
    no_cache: bool = False
    max_agentic_rounds: Optional[Any] = None


def _build_tool_messages(request: "ChatCompletionRequest") -> list:
    """Convert ChatCompletionRequest messages to plain dicts for upstream."""
    messages = []
    for m in request.messages:
        msg: dict = {"role": m.role}
        if m.role == "tool":
            msg["content"] = _oai_content_to_str(m.content) if m.content else ""
            if hasattr(m, "tool_call_id") and m.tool_call_id:
                msg["tool_call_id"] = m.tool_call_id
            if hasattr(m, "name") and m.name:
                msg["name"] = m.name
        elif m.role == "assistant" and hasattr(m, "tool_calls") and m.tool_calls:
            msg["tool_calls"] = m.tool_calls
            if m.content:
                msg["content"] = _oai_content_to_str(m.content)
        else:
            msg["content"] = _oai_content_to_str(m.content) if m.content else ""
        messages.append(msg)
    return messages


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

    Returns a StreamingResponse (SSE) when request.stream is True, otherwise
    a plain dict. Callers must check the type and wrap accordingly.
    """
    messages = _build_tool_messages(request)

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
        "model":    tool_model,
        "messages": messages,
        "stream":   _has_tool_results,
    }
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
                                yield f"{line}\n\n"
                            elif line == "data: [DONE]":
                                yield "data: [DONE]\n\n"
                                return
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

    if not request.stream:
        return upstream

    # Client requested streaming (SSE) — wrap the single non-streaming
    # response as a minimal SSE stream so clients using stream=True
    # (e.g. Hermes) receive the expected text/event-stream format.
    async def _sse_wrap():
        choices = upstream.get("choices", [{}])
        ch = choices[0] if choices else {}
        msg = ch.get("message", {})
        finish_reason = ch.get("finish_reason", "stop")
        created = upstream.get("created", int(time.time()))
        model_id = upstream.get("model", tool_model)

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
        usage = upstream.get("usage", {})
        yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_id, 'choices': [], 'usage': usage})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_sse_wrap(), media_type="text/event-stream")


async def chat_completions(raw_request: Request, request: ChatCompletionRequest):
    from main import stream_response, _is_openwebui_internal, _handle_internal_direct, _stream_native_llm
    async def _ol_start(*a, **kw): return None   # lineage owned by moe-codex
    async def _ol_complete(*a, **kw): pass
    async def _ol_fail(*a, **kw): pass
    def dataset_user_query(*a, **kw): return {}
    def dataset_response(*a, **kw): return {}
    chat_id    = f"chatcmpl-{uuid.uuid4()}"
    session_id = _extract_session_id(raw_request)
    _ol_run_id = await _ol_start(
        "chat_completion",
        inputs=[dataset_user_query(session_id or "")],
        extra_facets={"requestModel": {"_producer": "https://github.com/moe-sovereign/moe-sovereign",
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
    # ── Diagnostic auth log (remove after debugging missing-API-key issue) ──
    # Never logs the key itself, only its prefix shape and which header it came from.
    _auth_hdr   = raw_request.headers.get("authorization", "")
    _xapi_hdr   = raw_request.headers.get("x-api-key", "")
    _hdr_source = (
        "authorization-bearer" if _auth_hdr.lower().startswith("bearer ") else
        "x-api-key"             if _xapi_hdr else
        "authorization-other"   if _auth_hdr else
        "none"
    )
    _key_prefix = (raw_key or "")[:10]
    _key_len    = len(raw_key or "")
    _is_moe_sk  = bool(raw_key and raw_key.startswith("moe-sk-"))
    _origin_ip  = raw_request.client.host if raw_request.client else "?"
    logger.warning(
        "🔍 chat-auth-debug ip=%s hdr_source=%s key_prefix=%r key_len=%d is_moe_sk=%s model=%r",
        _origin_ip, _hdr_source, _key_prefix, _key_len, _is_moe_sk, request.model,
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

    # Template names and MoE mode IDs take precedence over native endpoints.
    # Wildcard permissions (*@node) would otherwise intercept template names and
    # route them as direct Ollama calls (model does not exist → empty response).
    _req_raw = request.model
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
    if not _tmpl_override:
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
                        "url":   URL_MAP[_ep_node],
                        "token": TOKEN_MAP.get(_ep_node, "ollama"),
                        "model": _req_model_base,
                        "node":  _ep_node,
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
                    "_user_conn": True,
                }
            elif not _req_node_hint:
                # Bare model name: check models_cache of each user connection
                for _cname, _uc in _user_conns_map.items():
                    if _req_model_base in _uc.get("models_cache", []):
                        _native_endpoint = {
                            "url":        _uc["url"],
                            "token":      _uc.get("api_key") or "ollama",
                            "model":      _req_model_base,
                            "node":       _cname,
                            "api_type":   _uc.get("api_type", "openai"),
                            "_user_conn": True,
                        }
        _user_tmpls_json = user_ctx.get("user_templates_json", "{}")

    # ── Dynamic Router Integration ───────────────────────────────────────────
    # Activate if global env var is set OR the specific API key opted in.
    _key_dynamic_routing = user_ctx.get("dynamic_routing") == "1"
    if os.getenv("DYNAMIC_ROUTER_ENABLED", "false").lower() in ("1", "true", "yes") or _key_dynamic_routing:
        is_default_moe = request.model in ("moe-orchestrator", "moe-orchestrator-code", "moe-orchestrator-concise", "moe-orchestrator-agent", "moe-orchestrator-agent-orchestrated")
        if not _tmpl_override or is_default_moe:
            from services.dynamic_router import get_dynamic_template
            user_msgs = [m for m in request.messages if m.role == "user"]
            last_prompt = _oai_content_to_str(user_msgs[-1].content) if user_msgs else ""
            local_only = os.getenv("LOCAL_ONLY_COMPLIANCE", "false").lower() in ("1", "true", "yes")
            
            dynamic_tmpl = await get_dynamic_template(last_prompt, local_only=local_only)
            if dynamic_tmpl:
                try:
                    user_tmpls = json.loads(_user_tmpls_json or "{}")
                except Exception:
                    user_tmpls = {}
                tmpl_id = dynamic_tmpl["id"]
                user_tmpls[tmpl_id] = dynamic_tmpl
                _user_tmpls_json = json.dumps(user_tmpls)
                _tmpl_override = tmpl_id
    # ──────────────────────────────────────────────────────────────────────────

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
    user_experts  = _resolve_user_experts(user_ctx.get("permissions_json", ""), override_tmpl_id=_tmpl_override,
                                           user_templates_json=_user_tmpls_json,
                                           admin_override=False,
                                           user_connections_json=_user_conns_json) or {}
    _tmpl_prompts = _resolve_template_prompts(user_ctx.get("permissions_json", ""), override_tmpl_id=_tmpl_override,
                                               user_templates_json=_user_tmpls_json,
                                               admin_override=False,
                                               user_connections_json=_user_conns_json)
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
    asyncio.create_task(_register_active_request(
        chat_id=chat_id, user_id=user_id, model=_req_model_base,
        moe_mode=("native" if _native_endpoint else mode),
        req_type=_req_type, template_name=_tmpl_name, client_ip=_client_ip,
        backend_model=_native_endpoint["model"] if _native_endpoint else "",
        backend_host=_native_endpoint["node"]  if _native_endpoint else "",
        api_key_id=api_key_id,
    ))

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
        _native_is_user_conn = bool(_native_endpoint.get("_user_conn"))
        if request.stream:
            return StreamingResponse(
                _stream_native_llm(request, chat_id, _native_endpoint, user_id, request.model,
                                   session_id=session_id, is_user_conn=_native_is_user_conn),
                media_type="text/event-stream",
            )
        # Non-streaming native: blockierender httpx-Call
        async with httpx.AsyncClient(timeout=300) as _hc:
            _nr = await _hc.post(
                _native_endpoint["url"].rstrip("/").removesuffix("/v1") + "/v1/chat/completions",
                headers={"Authorization": f"Bearer {_native_endpoint['token']}", "Content-Type": "application/json"},
                json={"model": _native_endpoint["model"],
                      "messages": [{"role": m.role, "content": m.content if m.content is not None else ""} for m in request.messages],
                      "stream": False,
                      **({"max_tokens": request.max_tokens} if request.max_tokens else {}),
                      **({"temperature": request.temperature} if request.temperature is not None else {})},
            )
        _nr.raise_for_status()
        _nj = _nr.json()
        _nu = _nj.get("usage", {})
        if user_id != "anon":
            asyncio.create_task(_log_usage_to_db(user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
                model=request.model, moe_mode="native",
                prompt_tokens=_nu.get("prompt_tokens", 0), completion_tokens=_nu.get("completion_tokens", 0),
                session_id=session_id))
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
    if _has_tools:
        _raw_judge = (_tmpl_prompts.get("judge_model_override") or "").strip()
        _tc_model, _, _tc_ep = _raw_judge.partition("@")
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
            logger.info(
                f"🔧 Tool-calling passthrough: model={_tc_model} stream={request.stream} "
                f"tools={len(request.tools or [])} tool_msgs={sum(1 for m in request.messages if getattr(m,'role','')=='tool')} "
                f"content_model={_content_model or 'none'}"
            )
            _t_tc = time.monotonic()
            _tc_resp = await _handle_tool_calls(
                request, chat_id, _tc_model, _tc_url, _tc_token,
                content_model=_content_model,
                content_url=_content_url,
                content_token=_content_token,
                content_system_prompt=_content_sys,
            )
            PROM_REQUESTS.labels(mode="tool", cache_hit="false",
                                 user_id=(user_id or "anon")).inc()
            PROM_RESPONSE_TIME.labels(mode="tool").observe(time.monotonic() - _t_tc)
            # _handle_tool_calls returns StreamingResponse for stream=True, dict otherwise
            if isinstance(_tc_resp, StreamingResponse):
                if _moe_resp_headers:
                    for _hk, _hv in _moe_resp_headers.items():
                        _tc_resp.headers[_hk] = _hv
                return _tc_resp
            if _moe_resp_headers:
                return JSONResponse(content=_tc_resp, headers=_moe_resp_headers)
            return _tc_resp
        logger.warning("⚠️ Tool-calling passthrough: no judge URL configured — falling through to pipeline")

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
                            model_name=request.model,
                            pending_reports=_pending_reports,
                            images=_user_images,
                            session_id=session_id,
                            max_agentic_rounds=(
                                request.max_agentic_rounds
                                if request.max_agentic_rounds is not None
                                else _tmpl_prompts.get("max_agentic_rounds", 0)
                            ),
                            no_cache=request.no_cache)

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
        return JSONResponse(status_code=503, content={"error": {"message": "Orchestrator graph not ready — retry in a few seconds", "type": "service_unavailable", "code": "graph_not_ready"}})
    _t_start = time.monotonic()
    result = await state.app_graph.ainvoke(
        {"input": user_input, "response_id": chat_id, "mode": mode,
         "user_id": user_id, "api_key_id": api_key_id,
         "expert_models_used": [], "prompt_tokens": 0, "completion_tokens": 0,
         "user_conn_prompt_tokens": 0, "user_conn_completion_tokens": 0,
         "chat_history": history, "reasoning_trace": "", "system_prompt": system_prompt,
         "images": _user_images,
         "user_permissions": user_perms, "user_experts": user_experts,
         # Prepend personal namespace so user-created knowledge is private by default.
         # graph_tenant permissions add team/tenant namespaces on top.
         "tenant_ids": ([f"user:{user_id}"] if user_id and user_id != "anon" else [])
                       + [t for t in user_perms.get("graph_tenant", [])
                          if t != f"user:{user_id}"],
         "provenance_sources": [],
         "output_skill_body": "",
         "enable_cache": _tmpl_prompts.get("enable_cache", True),
         "enable_graphrag": _tmpl_prompts.get("enable_graphrag", True),
         "enable_web_research": _tmpl_prompts.get("enable_web_research", True),
         "complexity_level": _tmpl_prompts.get("complexity_level", ""),
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
         "template_name":  _tmpl_name,
         "template_id":    _tmpl_override or "",
         "pending_reports": _pending_reports,
         "max_agentic_rounds": _tmpl_prompts.get("max_agentic_rounds", 0),
         "agentic_iteration": 0,
         "agentic_history": [],
         "agentic_gap": "",
         "attempted_queries": [],
         "search_strategy_hint": "",
         "conflict_registry": [],
         "vector_confidence": 0.5,
         "graph_confidence": 0.5,
         "fuzzy_routing_scores": {},
         "no_cache": request.no_cache,
         # Pass explicit temperature into state so planner, judge and experts
         # can honour it. None means "use query-adaptive detection".
         "query_temperature": request.temperature,
         },
        {"configurable": {"thread_id": str(uuid.uuid4())}},
    )
    p_tok = result.get("prompt_tokens",     0)
    c_tok = result.get("completion_tokens", 0)
    # Prometheus: the streaming path records these in its finalizer; the
    # non-streaming branch must do it too, otherwise moe_requests_total and
    # the duration histogram stay flat for stream=false clients (load tests).
    _cache_hit_flag = bool(result.get("cache_hit", False))
    PROM_REQUESTS.labels(mode=mode, cache_hit=str(_cache_hit_flag).lower(),
                         user_id=(user_id or "anon")).inc()
    PROM_RESPONSE_TIME.labels(mode=mode).observe(time.monotonic() - _t_start)
    if user_id != "anon":
        asyncio.create_task(_log_usage_to_db(
            user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
            model=MODES.get(mode, MODES["default"])["model_id"],
            moe_mode=mode, prompt_tokens=p_tok, completion_tokens=c_tok,
            session_id=session_id,
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
    asyncio.create_task(_deregister_active_request(chat_id))
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
    await _ol_complete(_ol_run_id, job_name="chat_completion",
                       outputs=[dataset_response(chat_id)])
    if _moe_resp_headers:
        return JSONResponse(content=resp, headers=_moe_resp_headers)
    return resp
