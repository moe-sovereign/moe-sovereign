"""services/pipeline/anthropic.py — Anthropic Messages API (/v1/messages)."""

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
    CLAUDE_CODE_TOOL_CHOICE,
    _CLAUDE_CODE_TOOL_URL, _CLAUDE_CODE_TOOL_TOKEN, _CLAUDE_CODE_REASONING_URL,
    JUDGE_TIMEOUT, EXPERT_TIMEOUT, PLANNER_TIMEOUT,
    JUDGE_MODEL, JUDGE_URL, JUDGE_TOKEN, JUDGE_NUM_CTX,
    PLANNER_MODEL, PLANNER_URL, PLANNER_TOKEN, PLANNER_NUM_CTX,
    URL_MAP, TOKEN_MAP, API_TYPE_MAP, INFERENCE_SERVERS_LIST,
    MODES, _MODEL_ID_TO_MODE, _CLAUDE_PRETTY_NAMES, _model_display_name,
    MAX_GRAPH_CONTEXT_CHARS, MCP_URL, GRAPH_VIA_MCP, EXPERTS,
    CACHE_HIT_THRESHOLD, SOFT_CACHE_THRESHOLD, SOFT_CACHE_MAX_EXAMPLES,
    CACHE_MIN_RESPONSE_LEN, ROUTE_THRESHOLD, ROUTE_GAP,
    EXPERT_TIER_BOUNDARY_B, EXPERT_MIN_SCORE, EXPERT_MIN_DATAPOINTS,
    HISTORY_MAX_TURNS, HISTORY_MAX_CHARS,
    CC_HISTORY_COMPRESS_THRESHOLD, CC_HISTORY_COMPRESS_KEEP_TURNS,
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
    CC_SAFETY_BUFFER_TOKENS,
    CC_PREANALYSIS_DELAY_SECS,
    CC_CONTEXT_INDEX_ENABLED,
    CC_HISTORY_COMPRESS_LLM,
    CC_HISTORY_COMPRESS_LLM_TIMEOUT,
    AGENT_GRAPHRAG_MAX_CHARS,
    AGENT_GRAPHRAG_TIMEOUT_S,
    ORCHESTRATION_TIMEOUT,
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
    PROM_BUDGET_EXCEEDED,
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
)
from services.llm_instances import judge_llm, planner_llm, ingest_llm, search
from services.helpers import (
    _log_tool_eval, _tool_eval_logger,
    _update_rate_limit_headers, _check_rate_limit_exhausted,
    _conf_format_for_mode, _get_expert_prompt,
    _truncate_history, _apply_semantic_memory,
    _web_search_with_citations,
    _store_response_metadata, _self_evaluate, _neo4j_terms_exist,
    _report,
    _shadow_request, _shadow_lock,
    _progress_queue,
    current_chat_id,
)
from services.templates import _read_expert_templates, _read_cc_profiles
from services.inference import _select_node as _select_node_svc, _get_available_models as _get_available_models_svc, _get_expert_score
from services.model_capabilities import model_supports_native_tool_calling
from services.rate_limiter import throttle as _throttle_endpoint
from services.skills import _build_skill_catalog, _resolve_skill_secure
from parsing import (
    _anthropic_content_to_text,
    _extract_images,
    _extract_oai_images,
    _anthropic_to_openai_messages,
    _anthropic_tools_to_openai,
)
from context_budget import (
    get_model_ctx_async as _get_tool_ctx_async,
    get_model_context_window as _get_static_ctx_window,
    CHARS_PER_TOKEN as _CHARS_PER_TOKEN,
    resolve_io_budget,
    estimate_overflow,
    MIN_OUTPUT_BUDGET_TOKENS,
    graphrag_budget_chars,
)
from services.agent_enrichment import (
    classify_turn as _classify_agent_turn,
    agent_graph_context,
    agent_writeback,
    agent_cache_lookup,
    extract_file_touches,
)
from services.pipeline.contracts import detect_required_precision_intents


async def _agent_writeback_traced(chat_id: str, *args, **kwargs) -> None:
    """Wraps agent_writeback() with started/done stage-trace markers.

    agent_writeback() itself has no chat_id (only session_id, which is the CC
    session ID, not the per-request live-monitoring key) — traced here at the
    call site instead of threading chat_id through agent_enrichment.py. Runs
    via asyncio.create_task, so "done" can legitimately appear in the trace
    after the client already received its response.
    """
    await _record_stage(chat_id, "agent_writeback", "started")
    try:
        await agent_writeback(*args, **kwargs)
    finally:
        await _record_stage(chat_id, "agent_writeback", "done")


# CC tool conversations contain code, JSON, shell output and tool results —
# all denser than prose. Use 3 chars/token for input estimation to avoid
# running the total context into num_ctx and getting done_reason=length.
_CC_TOOL_CHARS_PER_TOKEN = 3
from services.pipeline.cc_session import CCSession, _resolve_cc_session
from services.deliberation.contracts import DeliberationPolicyError

logger = logging.getLogger("MOE-SOVEREIGN")


def _trim_oai_to_budget_impl(oai_msgs: list, available_input_tokens: int) -> tuple:
    """Remove oldest non-system message groups until history fits the token budget.

    Groups are defined by: one user turn + every assistant/tool message up to
    (not including) the next user message. This keeps a whole tool-calling
    round-trip (assistant tool_call + its tool result, possibly repeated)
    bundled atomically with the user message that triggered it — dropping a
    group can never strand a message set with no user turn in it. Providers
    that validate for at least one user-role message (observed: Hetzner's
    inference API, "No user query found in messages") reject a payload where
    that guarantee doesn't hold; grouping by assistant messages too (the
    previous behavior) could leave the trailing group as pure
    assistant-tool_call/tool-result with no user message once trimmed down to
    a single group.
    The last group (current user turn, with its full tool-call chain) is
    never dropped.

    Returns (kept_messages, dropped_flag, dropped_groups) where dropped_groups is
    the list of message-groups removed, oldest first.
    """
    budget_chars = int(available_input_tokens * _CC_TOOL_CHARS_PER_TOKEN)
    total_chars  = sum(len(json.dumps(m)) for m in oai_msgs)
    if total_chars <= budget_chars:
        return oai_msgs, False, []

    sys_msgs  = [m for m in oai_msgs if m.get("role") == "system"]
    conv_msgs = [m for m in oai_msgs if m.get("role") != "system"]
    if len(conv_msgs) <= 1:
        return oai_msgs, False, []

    # Build groups: each starts at a user message (or at the very first
    # message, whatever its role, so nothing is ever silently dropped) and
    # collects every following assistant/tool message up to the next user one.
    groups: list = []
    for msg in conv_msgs:
        if msg.get("role") == "user" or not groups:
            groups.append([msg])
        else:
            groups[-1].append(msg)
    if len(groups) <= 1:
        return oai_msgs, False, []

    sys_chars     = sum(len(json.dumps(m)) for m in sys_msgs)
    kept_groups   = list(groups)
    dropped       = False
    dropped_groups: list = []
    while len(kept_groups) > 1:
        cand = sys_chars + sum(len(json.dumps(m)) for g in kept_groups for m in g)
        if cand <= budget_chars:
            break
        dropped_groups.append(kept_groups.pop(0))
        dropped = True

    return sys_msgs + [m for g in kept_groups for m in g], dropped, dropped_groups


async def _summarize_dropped_groups_llm(dropped_groups: list, budget_hint_chars: int = 1500) -> Optional[str]:
    """Summarise message groups dropped by history trimming using a small local LLM.

    Returns a compact summary string on success, or None on missing config,
    timeout, or any error — callers fall back to silent dropping (the
    pre-existing behaviour).
    """
    from langchain_openai import ChatOpenAI
    if not CC_HISTORY_COMPRESS_LLM or not dropped_groups or judge_llm is None:
        return None
    try:
        lines: list = []
        for group in dropped_groups:
            for msg in group:
                role = msg.get("role", "")
                content = msg.get("content")
                if not isinstance(content, str):
                    content = json.dumps(content) if content is not None else ""
                lines.append(f"[{role}] {content[:500]}")
        flattened = "\n".join(lines)[:8000]
        if not flattened.strip():
            return None

        compress_prompt = (
            f"Summarise the following conversation history in at most {budget_hint_chars} characters. "
            "Preserve concrete facts: file paths touched, decisions made, commands run, "
            "and outstanding next steps. Output plain text only — no JSON, no headers.\n\n"
            f"{flattened}"
        )
        _compress_llm = ChatOpenAI(
            model=CC_HISTORY_COMPRESS_LLM,
            base_url=judge_llm.openai_api_base,
            api_key=judge_llm.openai_api_key,
            timeout=CC_HISTORY_COMPRESS_LLM_TIMEOUT,
        )
        result = await asyncio.wait_for(
            _compress_llm.ainvoke(compress_prompt),
            timeout=CC_HISTORY_COMPRESS_LLM_TIMEOUT + 0.5,
        )
        summary = result.content.strip()
        if summary:
            logger.info(
                "cc_tool: summarized %d dropped group(s) into %d chars",
                len(dropped_groups), len(summary),
            )
            return summary
    except (asyncio.TimeoutError, Exception) as e:
        logger.debug("cc_tool: dropped-history summarization skipped: %s", e)
    return None


async def _trim_oai_to_budget_async(
    oai_msgs: list,
    available_input_tokens: int,
    redis_client,
    session_id: str | None,
) -> tuple:
    """Trim history to budget; on drop, summarize dropped groups into cc:work.

    Falls back to pure dropping (no summary) when CC_HISTORY_COMPRESS_LLM is
    unset, redis_client/session_id are unavailable, or summarization fails.
    """
    kept_msgs, dropped, dropped_groups = _trim_oai_to_budget_impl(oai_msgs, available_input_tokens)
    if dropped and dropped_groups and redis_client and session_id:
        summary = await _summarize_dropped_groups_llm(dropped_groups)
        if summary:
            await _append_dropped_history_summary(redis_client, session_id, summary)
    return kept_msgs, dropped


import hashlib as _hashlib


_CC_WORK_TTL = 4 * 3600  # 4 h — long enough to survive context loss + model reload

# Track active Ollama streaming tasks per CC session so zombie requests from
# previous CC retries can be cancelled when a new request arrives for the same session.
_cc_active_ollama_tasks: dict[str, asyncio.Task] = {}


async def _cc_expert_preanalysis(
    session_id: str,
    user_query: str,
    planner_model: str,
    planner_url: str,
    planner_token: str,
    planner_prompt: str,
    redis_client,
    delay_s: float = 20.0,
) -> None:
    """Background: call the planner with Tier-3 codebase context to pre-analyse the task.

    Waits delay_s seconds so the first tool-call (qwen3.6:35b) completes before
    phi4:14b-fp16 loads — avoids simultaneous VRAM pressure on the same node.

    The planner receives:
      1. The Tier-3 TOC (table-of-contents of the indexed codebase/document)
      2. Semantically relevant Tier-3 chunks for the user's query
    Together these give it full awareness of the codebase without exceeding its
    16k context window, enabling a meaningful plan that references real files.

    The result is stored in cc:work:{session_id}:task_plan and injected on turn 2+.
    """
    if delay_s > 0:
        await asyncio.sleep(delay_s)
    try:
        # ── Build Tier-3 context block for the planner ────────────────────────
        _ctx_block = ""
        try:
            from services.context_index import (
                is_context_indexed as _ctx_indexed,
                get_context_toc as _get_toc,
                retrieve_context_for_task as _ctx_retrieve,
            )
            if await _ctx_indexed(session_id, redis_client):
                _toc = await _get_toc(session_id, redis_client)
                _chunks = await _ctx_retrieve(
                    session_id=session_id,
                    task_text=user_query[:500],
                    redis_client=redis_client,
                    n_results=4,
                )
                if _toc:
                    _ctx_block += f"\n\nCODEBASE OVERVIEW (table of contents):\n{_toc}"
                if _chunks:
                    _ctx_block += f"\n\nRELEVANT CODEBASE SECTIONS:\n{_chunks}"
        except Exception:
            pass

        _sys = (
            planner_prompt
            or "You are a senior software engineer with full access to the codebase index below. "
               "Analyse the task and create a concise step-by-step plan. "
               "Identify: exact file paths involved, tools needed, risks, and the correct execution order. "
               "Reference specific files and functions from the codebase overview. "
               "Be specific and brief — max 500 words."
        )
        _user_content = f"Task:\n{user_query[:2000]}{_ctx_block}"
        _planner_base = planner_url.rstrip("/").removesuffix("/v1")
        async with httpx.AsyncClient(timeout=120.0) as _cl:
            _resp = await _cl.post(
                f"{_planner_base}/v1/chat/completions",
                json={
                    "model":      planner_model,
                    "messages":   [
                        {"role": "system", "content": _sys},
                        {"role": "user",   "content": _user_content},
                    ],
                    "stream":     False,
                    "max_tokens": 800,
                    "extra_body": {"options": {"num_ctx": PLANNER_NUM_CTX or 16384}},
                },
                headers={"Authorization": f"Bearer {planner_token}"},
            )
        if _resp.status_code != 200:
            return
        _plan_text = _resp.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if not _plan_text:
            return

        # Store in cc:work so _inject_cc_work_context picks it up on turn 2+
        _key = f"cc:work:{session_id}"
        _raw = await redis_client.get(_key)
        _work: dict = json.loads(_raw) if _raw else {"steps": [], "files_read": [], "findings": []}
        _work["task_plan"] = _plan_text[:4000]
        await redis_client.set(_key, json.dumps(_work, ensure_ascii=False), ex=_CC_WORK_TTL)
        logger.info(
            "cc_tool: expert pre-analysis stored for session=%s (%d chars, ctx_indexed=%s, model=%s)",
            session_id[:8], len(_plan_text), bool(_ctx_block), planner_model,
        )
    except Exception as _e:
        logger.debug("cc_tool: expert pre-analysis failed: %s", _e)


async def _update_cc_work_summary(
    redis_client,
    session_id: str,
    role: str,
    content: str,
    tool_name: str | None = None,
) -> None:
    """Accumulate structured work-progress in cc:work:{session_id} (Valkey, TTL 4h).

    Called whenever a message is compressed so that the information is not silently
    discarded. On the next request _inject_cc_work_context() can read this back and
    prepend it as a system block so the model knows what was already accomplished.
    """
    if not redis_client or not session_id:
        return
    key = f"cc:work:{session_id}"
    try:
        raw = await redis_client.get(key)
        work: dict = json.loads(raw) if raw else {"steps": [], "files_read": [], "findings": []}

        if role == "tool" and tool_name:
            entry = f"[{tool_name}] {content[:300]}"
            if entry not in work["steps"]:
                work["steps"].append(entry)
            # Track file reads specifically for deduplication
            if tool_name in ("Read", "read_file_chunked") and content and len(content) > 10:
                path_hint = content.split("\n")[0][:120]
                if path_hint not in work["files_read"]:
                    work["files_read"].append(path_hint)
        elif role == "assistant":
            # Extract the first meaningful sentence as a finding
            first = content.strip().split("\n")[0][:200]
            if first and first not in work["findings"]:
                work["findings"].append(first)

        # Cap list sizes to prevent unbounded growth
        work["steps"]     = work["steps"][-30:]
        work["files_read"] = work["files_read"][-20:]
        work["findings"]  = work["findings"][-15:]

        await redis_client.set(key, json.dumps(work, ensure_ascii=False), ex=_CC_WORK_TTL)
    except Exception as _e:
        logger.debug("cc_work: update failed: %s", _e)


async def _append_dropped_history_summary(redis_client, session_id: str, summary: str) -> None:
    """Append an LLM-generated summary of dropped history to cc:work:{session_id}.

    Keeps only the last 5 summaries (each capped at 1500 chars) so the
    [CONTEXT TRIMMED] block injected by _inject_cc_work_context stays bounded.
    """
    if not redis_client or not session_id or not summary:
        return
    key = f"cc:work:{session_id}"
    try:
        raw = await redis_client.get(key)
        work: dict = json.loads(raw) if raw else {"steps": [], "files_read": [], "findings": []}
        history = work.get("dropped_history_summary", [])
        history.append(summary[:1500])
        work["dropped_history_summary"] = history[-5:]
        await redis_client.set(key, json.dumps(work, ensure_ascii=False), ex=_CC_WORK_TTL)
    except Exception as _e:
        logger.debug("cc_work: dropped-history summary append failed: %s", _e)


async def _inject_cc_work_context(
    oai_msgs: list,
    redis_client,
    session_id: str | None,
) -> list:
    """Prepend a [CC_WORK_CONTEXT] system block if prior work is recorded for this session.

    Injected BEFORE the first user message so the model always knows what it already
    did — even after the history compressor discarded old tool-result turns.
    """
    if not redis_client or not session_id:
        return oai_msgs
    try:
        raw = await redis_client.get(f"cc:work:{session_id}")
        if not raw:
            return oai_msgs
        work = json.loads(raw)
        if not any(work.get(k) for k in ("steps", "files_read", "findings", "task_plan", "dropped_history_summary")):
            return oai_msgs

        lines = ["[CC_WORK_CONTEXT — already completed in this session]"]
        if work.get("task_plan"):
            lines.append("[EXPERT ANALYSIS — task plan from specialist model]")
            lines.append(work["task_plan"])
            lines.append("[End of expert analysis]")
        if work.get("dropped_history_summary"):
            lines.append("[CONTEXT TRIMMED — summary of removed conversation history]")
            for s in work["dropped_history_summary"]:
                lines.append(f"  • {s}")
            lines.append("[End of trimmed-history summary]")
        if work.get("files_read"):
            lines.append("Files already read: " + ", ".join(work["files_read"]))
        if work.get("steps"):
            lines.append("Steps completed:")
            for s in work["steps"][-10:]:
                lines.append(f"  • {s}")
        if work.get("findings"):
            lines.append("Key findings:")
            for f in work["findings"][-5:]:
                lines.append(f"  → {f}")
        lines.append("[Do NOT repeat completed steps. Continue from where you left off.]")

        ctx_text = "\n".join(lines)
        # Merge into the existing system message (if any) to avoid a second role:system block.
        # LiteLLM-backed endpoints (e.g. AIHUB/qwen) reject messages with more than one
        # system entry, returning HTTP 400 "System message must be at the beginning".
        sys_idx = next((i for i, m in enumerate(oai_msgs) if m.get("role") == "system"), -1)
        if sys_idx >= 0:
            existing = oai_msgs[sys_idx].get("content") or ""
            merged = oai_msgs[:]
            merged[sys_idx] = {**oai_msgs[sys_idx], "content": existing + "\n\n" + ctx_text}
            return merged
        # No existing system message — insert one at the front.
        return [{"role": "system", "content": ctx_text}] + oai_msgs
    except Exception as _e:
        logger.debug("cc_work: inject failed: %s", _e)
        return oai_msgs


async def _compress_history_responses(
    oai_msgs: list,
    redis_client,
    session_id: str | None,
    *,
    threshold: int,
    keep_turns: int,
) -> list:
    """Condense long assistant/tool messages in history and cache originals in Redis.

    Messages older than the last keep_turns turns are compressed when their content
    exceeds threshold chars. The full content is stored in Redis (TTL 3600 s) under
    cc:hist:<session_id>:<sha1[:12]> so it can be retrieved if needed. The last
    keep_turns * 2 non-system messages are always left untouched to preserve
    immediate context.

    When a message is compressed, its key information is also accumulated in
    cc:work:<session_id> via _update_cc_work_summary so the model retains awareness
    of completed steps across context-window resets.
    """
    if not oai_msgs or threshold <= 0:
        return oai_msgs

    non_sys = [i for i, m in enumerate(oai_msgs) if m.get("role") != "system"]
    protected = set(non_sys[-(keep_turns * 2):]) if non_sys else set()

    result: list = []
    compressed_count = 0
    for idx, msg in enumerate(oai_msgs):
        role = msg.get("role", "")
        if idx in protected or role in ("system", "user"):
            result.append(msg)
            continue

        content = msg.get("content")
        if not isinstance(content, str) or len(content) <= threshold:
            result.append(msg)
            continue

        orig_len = len(content)
        if redis_client and session_id:
            key = f"cc:hist:{session_id}:{_hashlib.sha1(content.encode()).hexdigest()[:12]}"
            try:
                await redis_client.set(key, content, ex=3600)
            except Exception:
                pass
            # Persist work-progress so the model can resume after context loss
            tool_name = msg.get("name") or (
                msg.get("tool_calls", [{}])[0].get("function", {}).get("name")
                if msg.get("tool_calls") else None
            )
            asyncio.create_task(_update_cc_work_summary(
                redis_client, session_id, role, content, tool_name
            ))
            # Store evicted turn in Tier-2 Semantic Memory (ChromaDB) for later retrieval
            try:
                from memory_retrieval import get_memory_store as _get_mem_store_e
                _mem_e = _get_mem_store_e()
                if _mem_e is not None:
                    asyncio.create_task(_mem_e.store_turns(
                        session_id=session_id,
                        turns=[{"role": role, "content": content[:2000]}],
                        base_turn_index=idx,
                    ))
            except Exception:
                pass

        head = content[:800]
        tail = content[-200:] if orig_len > 1000 else ""
        sep  = f"\n[…{orig_len} chars — condensed for context window]\n"
        result.append({**msg, "content": f"{head}{sep}{tail}" if tail else f"{head}{sep}"})
        compressed_count += 1

    if compressed_count:
        logger.info(
            "cc_hist: compressed %d history message(s) above %d chars (session=%s)",
            compressed_count, threshold, session_id or "none",
        )
    return result


# ============================================================
#  ANTHROPIC MESSAGES API  (/v1/messages)
#  Enables Claude Code CLI and other Anthropic API clients
#  to use the MoE Orchestrator.
#
#  Routing:
#    - Requests WITH tools / tool_results → judge_llm (magistral:24b via Ollama)
#      with OpenAI→Anthropic format conversion
#    - Pure text requests                 → MoE Agent-Pipeline (mode="agent")
# ============================================================


def _moe_response_headers(model: str, node: str, retry_used: bool = False) -> dict:
    """Transparency headers: which backend actually answered this request.

    X-MoE-Backend-Model / X-MoE-Node identify the real model+node (not the
    claude-* alias); X-MoE-Required-Retry marks answers produced by the
    tool_choice=required enforcement retry. Silent degradation must be
    observable for sovereignty auditing.
    """
    return {
        "X-Accel-Buffering":    "no",
        "Cache-Control":        "no-cache",
        "X-MoE-Backend-Model":  str(model or "unknown"),
        "X-MoE-Node":           str(node or "unknown"),
        "X-MoE-Required-Retry": "1" if retry_used else "0",
    }


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _wrap_deregister_on_stream_end(body_iterator, chat_id: str):
    """Safety-net wrapper: passes an SSE chunk stream through unchanged and
    guarantees `_deregister_active_request(chat_id)` fires once the stream
    ends — including on early client disconnect (Starlette calls `aclose()`
    on the body iterator in that case, which runs this generator's `finally`
    block the same as a normal completion).

    _live_ollama_sse / _live_openai_sse already call _deregister_active_request
    on each of their known exit paths (success, timeout, tool_choice=required
    retry failure, HTTP error) — this wrapper is a belt-and-suspenders backstop
    for any exit path that turns out to skip it (deregistration is idempotent:
    calling it twice, once explicitly and once here, is a safe no-op the
    second time). Mirrors services/pipeline/chat.py::_wrap_deregister_on_stream_end.

    Also marks the tool_model_call stage-trace entry done — same backstop
    reasoning: an early client disconnect (aclose() before the generator
    reaches its own "done" marker) would otherwise leave the pipeline
    diagram showing that node as permanently "active".
    """
    try:
        async for chunk in body_iterator:
            yield chunk
    finally:
        asyncio.create_task(_deregister_active_request(chat_id))
        asyncio.create_task(_record_stage(chat_id, "tool_model_call", "done"))


async def _anthropic_content_blocks_to_sse(
    content_blocks: list, chat_id: str, model_id: str,
    input_tokens: int, output_tokens: int, stop_reason: str
):
    """Emit finished content blocks as Anthropic SSE stream."""
    yield _sse_event("message_start", {
        "type": "message_start",
        "message": {
            "id": chat_id, "type": "message", "role": "assistant",
            "content": [], "model": model_id,
            "stop_reason": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": 1}
        }
    })
    for idx, block in enumerate(content_blocks):
        if block["type"] == "text":
            yield _sse_event("content_block_start", {
                "type": "content_block_start", "index": idx,
                "content_block": {"type": "text", "text": ""}
            })
            if idx == 0:
                yield _sse_event("ping", {"type": "ping"})
            text = block.get("text", "")
            for i in range(0, max(len(text), 1), SSE_CHUNK_SIZE):
                yield _sse_event("content_block_delta", {
                    "type": "content_block_delta", "index": idx,
                    "delta": {"type": "text_delta", "text": text[i:i+SSE_CHUNK_SIZE]}
                })
                await asyncio.sleep(0.005)
            yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": idx})
        elif block["type"] == "thinking":
            yield _sse_event("content_block_start", {
                "type": "content_block_start", "index": idx,
                "content_block": {"type": "thinking", "thinking": ""}
            })
            if idx == 0:
                yield _sse_event("ping", {"type": "ping"})
            thinking_text = block.get("thinking", "")
            for i in range(0, max(len(thinking_text), 1), SSE_CHUNK_SIZE):
                yield _sse_event("content_block_delta", {
                    "type": "content_block_delta", "index": idx,
                    "delta": {"type": "thinking_delta", "thinking": thinking_text[i:i+SSE_CHUNK_SIZE]}
                })
                await asyncio.sleep(0.005)
            yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": idx})
        elif block["type"] == "tool_use":
            yield _sse_event("content_block_start", {
                "type": "content_block_start", "index": idx,
                "content_block": {
                    "type": "tool_use", "id": block["id"],
                    "name": block["name"], "input": {}
                }
            })
            args_json = json.dumps(block.get("input", {}))
            yield _sse_event("content_block_delta", {
                "type": "content_block_delta", "index": idx,
                "delta": {"type": "input_json_delta", "partial_json": args_json}
            })
            yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": idx})
    yield _sse_event("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": output_tokens}
    })
    yield _sse_event("message_stop", {"type": "message_stop"})


async def _anthropic_tool_handler(
    body: dict,
    chat_id: str,
    session: CCSession,
    user_id: str = "anon",
    api_key_id: str = "",
    session_id: str | None = None,
):
    """Forwards tool-capable requests to an inference server and converts formats.

    All routing, credential, and prompt-prefix configuration is read from session.
    Tokens are not charged to the MoE budget when session.is_user_conn is True.
    """
    current_chat_id.set(chat_id)
    await _record_stage(chat_id, "tool_entry", "started")
    model_id   = body.get("model", "moe-orchestrator-agent")
    messages   = body.get("messages", [])
    # Claude Code sends system as a list of Anthropic content blocks (with cache_control).
    # Normalize to plain string before converting to OpenAI format.
    _system_raw = body.get("system") or ""
    if isinstance(_system_raw, list):
        system = "\n".join(
            b.get("text", "") for b in _system_raw
            if isinstance(b, dict) and b.get("type") == "text"
        )
    else:
        system = _system_raw
    _eff_sys_prefix = session.system_prefix
    if session.tool_system_prefix:
        _eff_sys_prefix = (
            f"{session.tool_system_prefix}\n\n{_eff_sys_prefix}"
            if _eff_sys_prefix else session.tool_system_prefix
        )
    if _eff_sys_prefix and system:
        system = f"{_eff_sys_prefix}\n\n{system}"
    elif _eff_sys_prefix:
        system = _eff_sys_prefix
    tools      = body.get("tools", [])
    do_stream  = body.get("stream", False)

    effective_model = session.tool_model or JUDGE_MODEL
    effective_url   = session.tool_url   or JUDGE_URL
    effective_token = session.tool_token or JUDGE_TOKEN
    effective_node  = session.tool_endpoint or "unknown"

    # Synthesis turns (tool_results present) may be handled by a stronger
    # template expert than the tool_agent — flag-gated, see tool_turn_router.
    from services.tool_turn_router import pick_synthesis_expert as _pse
    _syn_exp = _pse(messages, session.experts, effective_model)
    if _syn_exp:
        logger.info(
            "cc_tool: synthesis turn re-routed %s -> %s@%s",
            effective_model, _syn_exp["model"], _syn_exp.get("endpoint", "?"),
        )
        effective_model = _syn_exp["model"]
        effective_url   = _syn_exp["url"]
        effective_token = _syn_exp.get("token", "ollama")
        effective_node  = _syn_exp.get("endpoint", effective_node)

    # The caller registers before the tool model/endpoint is resolved. Patch
    # the real backend into live monitoring once it is known.
    asyncio.create_task(_patch_active_request_backend(chat_id, effective_model, effective_url))

    _node_timeout   = float(session.tool_timeout)

    # Self-imposed outbound throttling: a user-owned (BYOK) connection carries its
    # own rate limit(s) (admin_ui → My Connections), scoped per user+connection so
    # different users' identically named connections never share a bucket. Falls
    # through to the admin-configured per-endpoint limit (config.RATE_LIMITS,
    # keyed by effective_node) when the connection has none configured. Multiple
    # limits (e.g. 20/minute AND 1000/24h) all apply simultaneously — see
    # services/rate_limiter.py's _TokenBucketGroup.
    _rl_override = None
    _rl_key = effective_node
    if session.is_user_conn and session.tool_rate_limit:
        from config import rate_limits_to_list as _rate_limits_to_list
        _rl_override = _rate_limits_to_list(session.tool_rate_limit.get("limits", []))
        if _rl_override:
            _rl_key = f"user-conn:{user_id}:{effective_node}"

    # Context budget guard: trim history and cap max_tokens so input + output ≤ ctx_window.
    # Fetched from Redis cache (TTL 3600s) — negligible overhead on warm path.
    # Use the system token for model-info lookup: user tokens often lack /v1/models access
    # on LiteLLM-backed providers (e.g. AIHUB returns 401 for user keys on that endpoint).
    _info_token = TOKEN_MAP.get(effective_node, "") or effective_token
    # An explicit context_window is a real override, not merely a fallback for
    # total detection failure: it wins even when live auto-detection
    # "succeeds" with a different value — including a wrong one (e.g. a
    # provider's static parameter-count heuristic guessing low because its
    # /v1/models/{id} 404s and only the plain /v1/models list exists).
    # Priority: profile.context_window (explicit per-profile override) >
    # session.template_num_ctx (template's declared context_window) > live
    # auto-detect. Without this, an admin's explicit value could be silently
    # ignored whenever detection returned *something*, however wrong.
    _ctx_override = session.context_window or session.template_num_ctx or 0
    _tool_ctx = await _get_tool_ctx_async(
        effective_model, effective_url, _info_token, state.redis_client,
        override=_ctx_override,
    )
    # Last-resort fallback: no override configured AND auto-detection returned
    # nothing at all (API timeout, 401, cache miss, and even the static
    # parameter-count heuristic couldn't parse this model's name).
    if _tool_ctx == 0:
        _ctx_static = _get_static_ctx_window(effective_model)
        if _ctx_static > 0:
            logger.warning(
                "cc_tool: context_window lookup returned 0 — "
                "falling back to static model context_window=%d (model=%s)",
                _ctx_static, effective_model,
            )
            _tool_ctx = _ctx_static
        else:
            logger.warning(
                "cc_tool: context_window lookup returned 0 and no static "
                "context_window — falling back to TOOL_MAX_TOKENS*4 = %d "
                "(model=%s)", TOOL_MAX_TOKENS * 4, effective_model,
            )
            _tool_ctx = TOOL_MAX_TOKENS * 4
    # For Ollama endpoints: the loaded num_ctx may be smaller than the model's native
    # context window (e.g. 8192 default vs 32768 for qwen3-coder:30b).  Claude Code's
    # 26-tool schemas alone consume ~7 800 tokens, leaving only 1 output token at 8192.
    # Store the desired num_ctx now so (a) the budget guard uses the correct window and
    # (b) we can inject it into the Ollama payload later.
    _ollama_num_ctx: int = 0
    if session.tool_api_type == "ollama":
        # Derive num_ctx with the CC profile's context_window taking priority.
        # Fallback order: context_window → tool_max_tokens → template_num_ctx → JUDGE_NUM_CTX → 32768
        # tool_max_tokens is used as a proxy for moe_reasoning profiles: the user's
        # explicit output-token limit signals how large a KV-cache they want Ollama to
        # allocate. In moe_orchestrated, this value is already capped by judge_num_ctx in
        # cc_session.py so tool_max_tokens correctly reflects the template constraint.
        _ollama_num_ctx = (
            session.context_window
            or session.tool_max_tokens
            or session.template_num_ctx
            or JUDGE_NUM_CTX
            or 32768
        )
        # Never downgrade a warm model: if Ollama already has the model loaded with a
        # larger context window, reuse that window instead of forcing a costly reload.
        # A model loaded at 32768 can serve any request that needs ≤32768 tokens.
        _ollama_base = effective_url.rstrip("/").removesuffix("/v1")
        try:
            async with httpx.AsyncClient(timeout=2.0) as _ps_cl:
                _ps_r = await _ps_cl.get(
                    f"{_ollama_base}/api/ps",
                    headers={"Authorization": f"Bearer {effective_token}"},
                )
                for _loaded in _ps_r.json().get("models", []):
                    _lname = _loaded.get("name", "").split(":")[0]
                    _ename = effective_model.split(":")[0]
                    _loaded_ctx = _loaded.get("context_length", 0)
                    if _lname == _ename and _loaded_ctx >= _ollama_num_ctx:
                        logger.info(
                            "cc_tool: reusing warm model ctx=%d (requested %d, no reload needed, model=%s)",
                            _loaded_ctx, _ollama_num_ctx, effective_model,
                        )
                        _ollama_num_ctx = _loaded_ctx
                        break
        except Exception:
            pass  # non-fatal — fall through to the configured num_ctx
        if _ollama_num_ctx != (_tool_ctx or 0):
            logger.info(
                "cc_tool: Ollama num_ctx=%d (from CC profile/template context window, model=%s)",
                _ollama_num_ctx, effective_model,
            )
            _tool_ctx = _ollama_num_ctx

    _req_max_tokens = body.get("max_tokens", TOOL_MAX_TOKENS)
    max_tokens = min(_req_max_tokens, session.tool_max_tokens) if session.tool_max_tokens else _req_max_tokens
    # Cap max_tokens to reserve at least a healthy input context budget.
    # Claude Code requests max_tokens=32000 which in a 32768-window leaves only
    # 268 tokens for input (32768 - 32000 - 500), causing _trim_oai_to_budget
    # to strip all conversation history — model receives "3" with no context.
    # When context window is small (e.g. 8192), split the context budget 50/50
    # to ensure history is not trimmed to nothing.
    _budget1 = resolve_io_budget(
        ctx_tokens=_tool_ctx, desired_max_tokens=max_tokens,
        chars_per_token=_CC_TOOL_CHARS_PER_TOKEN,
        safety_buffer_tokens=CC_SAFETY_BUFFER_TOKENS,
        min_output_tokens=0, min_input_ratio=0.5,
    )
    if _tool_ctx > 0 and _budget1["max_output_tokens"] < max_tokens:
        logger.info(
            "cc_tool: capping max_tokens %d → %d (ctx=%d, reserving input budget)",
            max_tokens, _budget1["max_output_tokens"], _tool_ctx,
        )
    max_tokens = _budget1["max_output_tokens"]

    # Eval timing + input classification
    _eval_t0 = time.monotonic()
    _last_msg = messages[-1] if messages else {}
    _last_content = _last_msg.get("content", "")
    _last_msg_type = (
        "tool_result" if (
            isinstance(_last_content, list)
            and any(b.get("type") == "tool_result" for b in _last_content)
        )
        else "tool_use_request" if tools
        else "text"
    )

    oai_messages = _anthropic_to_openai_messages(messages, system)
    logger.info(
        "cc_tool: checkpoint after anthropic->openai conversion — roles=%s (session=%s, raw_system_present=%s)",
        [m.get("role") for m in oai_messages], session_id[:8] if session_id else "none", bool(system),
    )

    # Extract user query early — used by memory tiers and episodic write-back.
    _cc_ep_query = next(
        (m.get("content", "") for m in reversed(oai_messages)
         if m.get("role") == "user" and isinstance(m.get("content"), str)),
        "",
    )

    # Augmented Tool Path: classify this turn once (initial_task vs mid_loop)
    # for the GraphRAG injection below (AP4) and the write-back hook (AP5).
    # Cheap, pure — safe to always compute.
    _agent_turn = _classify_agent_turn(
        messages, tools, api="anthropic", user_id=user_id, system_text=system,
    )
    _agent_precision_required = bool(
        detect_required_precision_intents(_agent_turn.query)
    )

    # ── Augmented Tool Path: cache-read hook (opt-in, off by default) ─────────
    # Only served on the initial task turn for a genuinely informational query
    # (TurnInfo.cacheable) — tool_use decisions are never cache-served. Also
    # excluded whenever the profile forces tool_choice='required': the client
    # contract there expects a tool_use response, never a plain text answer.
    if (
        session.agent_cache and _agent_turn.kind == "initial_task"
        and _agent_turn.cacheable
        and (session.tool_choice or CLAUDE_CODE_TOOL_CHOICE) != "required"
        and not _agent_precision_required
    ):
        _agent_cached = await agent_cache_lookup(
            _agent_turn.query, _agent_turn.scope,
            state.redis_client, state.agent_cache_collection, path="anthropic",
        )
        await _record_stage(chat_id, "agent_cache", "hit" if _agent_cached else "miss")
        if _agent_cached:
            logger.info(
                "cc_tool: Augmented Tool Path cache hit (scope=%s, chars=%d)",
                _agent_turn.scope, len(_agent_cached),
            )
            asyncio.create_task(_deregister_active_request(chat_id))
            if body.get("stream", False):
                return StreamingResponse(
                    _anthropic_content_blocks_to_sse(
                        [{"type": "text", "text": _agent_cached}],
                        chat_id, body.get("model", "moe-orchestrator-agent"),
                        0, len(_agent_cached) // 4, "end_turn",
                    ),
                    media_type="text/event-stream",
                    headers={
                        **_moe_response_headers(effective_model, effective_node),
                        "X-MoE-Agent-Cache": "hit",
                    },
                )
            return {
                "id": chat_id, "type": "message", "role": "assistant",
                "content": [{"type": "text", "text": _agent_cached}],
                "model": body.get("model", "moe-orchestrator-agent"),
                "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }
    elif session.agent_cache and _agent_precision_required:
        await _record_stage(
            chat_id,
            "agent_cache",
            "bypassed",
            "required_precision_intent",
        )

    # ── Tier-3 Context Index: chunk large system_prompt into ChromaDB ────────────
    # When the client ships a large system_prompt (codebase, documents, long files),
    # index it once per session so that every expert can later retrieve only the
    # semantically relevant slice — the infrastructure-level 1M+ context window.
    if CC_CONTEXT_INDEX_ENABLED and session.long_memory and session_id and system and state.redis_client:
        try:
            from services.context_index import ensure_indexed as _ensure_ctx
            await _ensure_ctx(session_id, system, state.redis_client)
        except Exception as _cie:
            logger.debug("cc_tool: context indexing skipped: %s", _cie)

    # ── Tier 2-4 Long-Term Memory Retrieval ──────────────────────────────────
    # Collect context from all memory tiers and merge into the EXISTING system
    # message (or insert one if absent) — always producing exactly one system
    # entry. Multiple separate role:system blocks would cause HTTP 400 on
    # LiteLLM-backed endpoints (e.g. AIHUB/qwen).
    #
    # Tier 2 (Warm)  — ChromaDB: semantically similar past turns of this session
    # Tier 3 (Cold)  — ChromaDB/context_index: chunked codebase/document slices
    # Tier 4 (Perm.) — Neo4j: structured knowledge-graph facts + episodic hints
    # Disable per CC profile via long_memory=false.
    if CC_CONTEXT_INDEX_ENABLED and session.long_memory and session_id:
        _lm_query = _cc_ep_query
        _lm_blocks: list[str] = []

        # ── Tier-2: semantic warm memory (ChromaDB) ──
        try:
            from memory_retrieval import get_memory_store as _get_mem_store
            _mem_store = _get_mem_store()
            if _mem_store is not None and _lm_query:
                _warm_turns = await _mem_store.retrieve_relevant(
                    session_id=session_id,
                    query=_lm_query[:500],
                    n_results=5,
                )
                if _warm_turns:
                    _warm_block = _mem_store.build_warm_context_block(_warm_turns)
                    _lm_blocks.append(
                        f"[SEMANTIC MEMORY — relevant past context]\n{_warm_block}\n[End of past context.]"
                    )
                    logger.info("cc_tool: Tier-2 %d warm turns (session=%s)", len(_warm_turns), session_id[:8])
        except Exception as _me:
            logger.debug("cc_tool: Tier-2 skipped: %s", _me)

        # ── Tier-3: context-index chunks (ChromaDB) ──
        if state.redis_client:
            try:
                from services.context_index import (
                    is_context_indexed as _ctx_indexed_t3,
                    retrieve_context_for_task as _ctx_retrieve_t3,
                )
                if _lm_query and await _ctx_indexed_t3(session_id, state.redis_client):
                    _t3_chunks = await _ctx_retrieve_t3(
                        session_id=session_id,
                        task_text=_lm_query[:500],
                        redis_client=state.redis_client,
                    )
                    if _t3_chunks:
                        _lm_blocks.append(
                            f"[RELEVANT CODEBASE CONTEXT — retrieved for current task]\n{_t3_chunks}\n"
                            "[End of retrieved context. Use this to inform your next action.]"
                        )
                        logger.debug("cc_tool: Tier-3 %d chars (session=%s)", len(_t3_chunks), session_id[:8])
            except Exception as _t3e:
                logger.debug("cc_tool: Tier-3 skipped: %s", _t3e)

        # ── Tier-4: Neo4j knowledge-graph + episodic hints (Augmented Tool Path) ──
        # Gated on session.agent_graphrag (opt-in per CC profile, default off) —
        # a separate flag from the Tier 2-3 CC_CONTEXT_INDEX_ENABLED path above.
        # Neo4j is only queried on the initial task turn; mid-loop tool_result
        # turns re-inject the per-session cached result (cc:graphctx:{session_id})
        # at zero Neo4j/L2 cost instead of re-querying on every tool round-trip.
        # See services/agent_enrichment.py::agent_graph_context for the tenant
        # scoping and L2 cache-key fixes relative to the old inline version.
        if _lm_query and state.graph_manager is not None and session.agent_graphrag:
            try:
                _sess_ctx_key = f"cc:graphctx:{session_id}"
                if _agent_turn.kind == "initial_task":
                    _agent_tenant_ids = (
                        ([f"user:{user_id}"] if user_id and user_id != "anon" else [])
                        + [t for t in session.user_perms.get("graph_tenant", [])
                           if t != f"user:{user_id}"]
                    )
                    _t4_max_chars = min(
                        AGENT_GRAPHRAG_MAX_CHARS,
                        graphrag_budget_chars(effective_model, query_chars=len(_lm_query)),
                    )
                    _t4_ctx = await agent_graph_context(
                        _lm_query, _agent_tenant_ids, session_id,
                        state.redis_client, state.graph_manager,
                        max_chars=_t4_max_chars, timeout_s=AGENT_GRAPHRAG_TIMEOUT_S,
                        user_id=user_id,
                    )
                    await _record_stage(chat_id, "agent_graphrag", "queried" if _t4_ctx else "queried_empty")
                    if state.redis_client:
                        if _t4_ctx:
                            asyncio.create_task(state.redis_client.setex(_sess_ctx_key, 3600, _t4_ctx))
                        else:
                            asyncio.create_task(state.redis_client.delete(_sess_ctx_key))
                elif state.redis_client:
                    _cached = await state.redis_client.get(_sess_ctx_key)
                    _t4_ctx = (_cached if isinstance(_cached, str) else _cached.decode()) if _cached else ""
                    await _record_stage(chat_id, "agent_graphrag", "cached" if _t4_ctx else "cached_empty")
                else:
                    _t4_ctx = ""
                if _t4_ctx:
                    _lm_blocks.append(
                        f"[KNOWLEDGE GRAPH — structured facts & past strategies]\n{_t4_ctx}\n"
                        "[End of knowledge graph context.]"
                    )
                    logger.info("cc_tool: Tier-4 %d chars graph context (session=%s)", len(_t4_ctx), session_id[:8])
            except Exception as _t4e:
                logger.debug("cc_tool: Tier-4 skipped: %s", _t4e)

        # ── Merge all tiers into single system message ──
        if _lm_blocks:
            _lm_combined = "\n\n".join(_lm_blocks)
            _sys_idx = next((i for i, m in enumerate(oai_messages) if m.get("role") == "system"), -1)
            if _sys_idx >= 0:
                _existing_sys = oai_messages[_sys_idx].get("content") or ""
                oai_messages = list(oai_messages)
                oai_messages[_sys_idx] = {**oai_messages[_sys_idx], "content": _existing_sys + "\n\n" + _lm_combined}
            else:
                oai_messages = [{"role": "system", "content": _lm_combined}] + oai_messages
            logger.info(
                "cc_tool: checkpoint after Tier2-4 merge — roles=%s (session=%s)",
                [m.get("role") for m in oai_messages], session_id[:8] if session_id else "none",
            )

    # ── Expert-Template pre-analysis (first turn only) ────────────────────────
    # On the first user turn (no tool_results yet), fire a background planner call
    # using the expert template's planner_model. The result is stored in
    # cc:work:{session_id} and injected by _inject_cc_work_context on turn 2+.
    # This makes the expert template meaningful for Claude Code without blocking
    # the immediate tool-call response.
    if session_id and _last_msg_type == "tool_use_request" and state.redis_client:
        try:
            _work_key = f"cc:work:{session_id}"
            _existing_work = await state.redis_client.get(_work_key)
            if not _existing_work:
                # First turn: no prior work recorded → trigger expert pre-analysis
                _planner_model   = (session.planner_cfg.get("planner_model_override")   or PLANNER_MODEL   or "")
                _planner_url     = (session.planner_cfg.get("planner_url_override")     or PLANNER_URL     or "")
                _planner_token   = (session.planner_cfg.get("planner_token_override")   or PLANNER_TOKEN   or "ollama")
                _planner_prompt  = session.planner_cfg.get("planner_prompt", "")
                _user_q = next(
                    (m.get("content", "") for m in reversed(oai_messages)
                     if m.get("role") == "user" and isinstance(m.get("content"), str)),
                    "",
                )
                # Skip preanalysis if the planner runs on the same node as the CC tool
                # model — loading the planner model would evict the active CC tool model
                # from VRAM (Ollama evicts LRU on model switch, regardless of free VRAM).
                _cc_tool_base = (session.tool_url or JUDGE_URL or "").rstrip("/").removesuffix("/v1")
                _planner_base_cmp = _planner_url.rstrip("/").removesuffix("/v1")
                _same_node = bool(_planner_url and _planner_base_cmp == _cc_tool_base)
                if _same_node:
                    logger.info(
                        "cc_tool: preanalysis skipped — planner on same node as CC tool model (%s)",
                        _cc_tool_base,
                    )
                if _planner_model and _planner_url and _user_q and not _same_node:
                    # Resolve per-server model_load_delay; fall back to global default
                    _preanalysis_delay: float = float(CC_PREANALYSIS_DELAY_SECS)
                    _planner_base = _planner_url.rstrip("/").removesuffix("/v1")
                    _planner_srv = next(
                        (s for s in INFERENCE_SERVERS_LIST
                         if s.get("url", "").rstrip("/") == _planner_base),
                        None,
                    )
                    if _planner_srv and _planner_srv.get("model_load_delay") is not None:
                        _preanalysis_delay = float(_planner_srv["model_load_delay"])
                    asyncio.create_task(_cc_expert_preanalysis(
                        session_id=session_id,
                        user_query=_user_q,
                        planner_model=_planner_model,
                        planner_url=_planner_url,
                        planner_token=_planner_token,
                        planner_prompt=_planner_prompt,
                        redis_client=state.redis_client,
                        delay_s=_preanalysis_delay,
                    ))
        except Exception as _pae:
            logger.debug("cc_tool: expert pre-analysis skipped: %s", _pae)

    logger.info(
        "cc_tool: checkpoint before compress — roles=%s (session=%s)",
        [m.get("role") for m in oai_messages], session_id[:8] if session_id else "none",
    )
    oai_messages = await _compress_history_responses(
        oai_messages, state.redis_client, session_id,
        threshold=CC_HISTORY_COMPRESS_THRESHOLD,
        keep_turns=CC_HISTORY_COMPRESS_KEEP_TURNS,
    )
    logger.info(
        "cc_tool: checkpoint after compress — roles=%s (session=%s)",
        [m.get("role") for m in oai_messages], session_id[:8] if session_id else "none",
    )
    oai_messages = await _inject_cc_work_context(
        oai_messages, state.redis_client, session_id,
    )
    logger.info(
        "cc_tool: checkpoint after work-context inject — roles=%s (session=%s)",
        [m.get("role") for m in oai_messages], session_id[:8] if session_id else "none",
    )
    oai_tools    = _anthropic_tools_to_openai(tools) if tools else None

    if _tool_ctx > 0:
        # CRITICAL FIX: Tool-Schemas (~7800 Token) und System-Prompt fressen Kontext,
        # werden aber von oai_messages chars NICHT erfasst. Sie vom Budget abziehen
        # BEVOR das History-Trimming beginnt — sonst trimmt der Algorithmus zu wenig.
        # NOTE: JSON-Schemas tokenisieren effizienter als Text (~3 chars/token gilt NICHT).
        # Der Code-Comment (line 560) dokumentiert: 26-tool schemas ≈ 7800 Token.
        # Use this as a hard-coded overhead estimate for schema token count.
        _schema_overhead_tok = 7800 if oai_tools else 0
        _system_prompt_tok = len(system) // _CC_TOOL_CHARS_PER_TOKEN if system else 0
        _static_overhead = _schema_overhead_tok + _system_prompt_tok

        _budget2 = resolve_io_budget(
            ctx_tokens=_tool_ctx, desired_max_tokens=max_tokens,
            static_overhead_tokens=_static_overhead,
            chars_per_token=_CC_TOOL_CHARS_PER_TOKEN,
            safety_buffer_tokens=CC_SAFETY_BUFFER_TOKENS,
            min_output_tokens=MIN_OUTPUT_BUDGET_TOKENS, min_input_ratio=0.5,
        )

        # PRE-FLIGHT overflow check (before trimming): would the untouched
        # payload + desired output exceed the context window? If so, this is
        # a strong signal to offload the system prompt into the Tier-3
        # context index regardless of CONTEXT_INDEX_THRESHOLD.
        _input_est_preflight = sum(len(json.dumps(m)) for m in oai_messages) / _CC_TOOL_CHARS_PER_TOKEN \
            + _static_overhead
        if estimate_overflow(int(_input_est_preflight), max_tokens, _tool_ctx, CC_SAFETY_BUFFER_TOKENS):
            logger.warning(
                "cc_tool: PRE-FLIGHT overflow — est_input=%d tok + max_out=%d tok + buffer=%d > ctx=%d "
                "(session=%s, model=%s)",
                int(_input_est_preflight), max_tokens, CC_SAFETY_BUFFER_TOKENS, _tool_ctx,
                session_id[:8] if session_id else "none", effective_model,
            )
            PROM_BUDGET_EXCEEDED.labels(user_id=session_id or "unknown", limit_type="cc_tool_preflight").inc()
            if CC_CONTEXT_INDEX_ENABLED and session_id and system and state.redis_client:
                try:
                    from services.context_index import ensure_indexed as _ensure_ctx_preflight
                    await _ensure_ctx_preflight(session_id, system, state.redis_client)
                except Exception:
                    pass

        oai_messages, _history_trimmed = await _trim_oai_to_budget_async(
            oai_messages, _budget2["avail_input_tokens"], state.redis_client, session_id,
        )
        if _history_trimmed:
            logger.info(
                "cc_tool: trimmed message history to fit ctx_window=%d (max_out=%d, model=%s)",
                _tool_ctx, max_tokens, effective_model,
            )
        # Cap max_tokens: prevent output overflow even after trimming.
        # Use _CC_TOOL_CHARS_PER_TOKEN=3 (not the prose default of 4) because CC
        # conversations are code-heavy — tool results, JSON, shell output tokenize
        # at ~3 chars/token. Underestimating input causes num_ctx to be hit mid-response.
        # Recompute against the ACTUAL trimmed payload size so _safe_max_out
        # accounts for the FULL payload (schema + system prompt + history).
        _input_est_tok = sum(len(json.dumps(m)) for m in oai_messages) / _CC_TOOL_CHARS_PER_TOKEN \
            + _static_overhead
        _safe_max_out = max(MIN_OUTPUT_BUDGET_TOKENS, _tool_ctx - int(_input_est_tok) - CC_SAFETY_BUFFER_TOKENS)
        if max_tokens > _safe_max_out:
            logger.info(
                "cc_tool: capping max_tokens %d → %d (ctx=%d, est_input=%d tok, schema=%d tok, "
                "sys=%d tok, model=%s)",
                max_tokens, _safe_max_out, _tool_ctx, int(_input_est_tok),
                _schema_overhead_tok, _system_prompt_tok, effective_model,
            )
            max_tokens = _safe_max_out

    # Guard: empty model name causes inference servers to return HTTP 400.
    # Happens when CLAUDE_CODE_TOOL_MODEL is unset and no CC profile overrides it.
    if not effective_model or not effective_model.strip():
        _no_model_err = (
            "⚠️ No tool model configured. Set CLAUDE_CODE_TOOL_MODEL in .env "
            "or configure a CC profile with a tool_model."
        )
        logger.warning("⚠️ _anthropic_tool_handler: effective_model is empty — returning config error")
        asyncio.create_task(_deregister_active_request(chat_id))
        if body.get("stream", False):
            return StreamingResponse(
                _anthropic_content_blocks_to_sse(
                    [{"type": "text", "text": _no_model_err}],
                    chat_id, body.get("model", "moe-orchestrator-agent"), 0, 0, "end_turn"
                ),
                media_type="text/event-stream",
                headers=_moe_response_headers(effective_model, effective_node)
            )
        return {
            "id": chat_id, "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": _no_model_err}],
            "model": body.get("model", "moe-orchestrator-agent"),
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}
        }

    payload: dict = {
        "model":      effective_model,
        "messages":   oai_messages,
        "stream":     False,        # collect tool calls completely, then convert
        "max_tokens": max_tokens,
    }
    if oai_tools:
        payload["tools"] = oai_tools
        # Guard: if the last message is a tool_result (synthesis turn), don't force tool_use
        _has_tool_results = any(
            isinstance(m.get("content"), list)
            and any(b.get("type") == "tool_result" for b in m.get("content", []))
            for m in messages
        )
        _effective_tool_choice = (
            "auto" if (_has_tool_results and not tools) else (session.tool_choice or CLAUDE_CODE_TOOL_CHOICE)
        )
        payload["tool_choice"] = _effective_tool_choice
    else:
        _effective_tool_choice = "auto"

    # tool_choice=required enforcement for the Ollama-native path: Ollama's
    # /api/chat has no tool_choice parameter, so "required" would otherwise be
    # silently dropped — the model may answer with an announcement text and no
    # tool call, CC receives a clean end_turn and closes the turn while the
    # user expected tool execution. When set, text deltas are buffered and a
    # missing tool call triggers one retry against the template's tool_agent.
    # Never enforced on synthesis turns (tool_results in history) — those must
    # be allowed to produce the final text answer.
    _require_tools = (
        bool(oai_tools)
        and _effective_tool_choice == "required"
        and not any(
            isinstance(m.get("content"), list)
            and any(b.get("type") == "tool_result" for b in m.get("content", []))
            for m in messages
        )
    )

    # For Ollama nodes use the native /api/chat endpoint instead of /v1/chat/completions.
    # The OpenAI-compatible endpoint silently discards the "options" dict (including
    # num_ctx), so per-request context expansion only works on the native API.
    _use_ollama_native = _ollama_num_ctx > 0
    if _use_ollama_native:
        _ollama_base = effective_url.rstrip("/").removesuffix("/v1")
        _call_url = f"{_ollama_base}/api/chat"

        def _normalize_for_ollama(msg: dict) -> dict:
            """Normalize one OpenAI-format message for Ollama's native /api/chat.

            Key differences between OpenAI and Ollama native format:
            - content must be a string (not an array of content blocks)
            - tool messages must not carry 'tool_call_id' (unsupported field)
            - assistant tool_calls: arguments must be a dict, not a JSON string
            - assistant tool_calls: 'id' and 'type' fields should be removed
            """
            role    = msg.get("role", "")
            content = msg.get("content")
            # Flatten content arrays to plain string
            if isinstance(content, list):
                content = "\n".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ) or ""
            out = {**msg, "content": content or ""}
            # tool messages: strip tool_call_id (unsupported in Ollama native)
            if role == "tool":
                out.pop("tool_call_id", None)
            # assistant messages: convert tool_calls to Ollama native format
            # OpenAI: [{"id": "...", "type": "function", "function": {"name": "...", "arguments": "{...}"}}]
            # Ollama: [{"function": {"name": "...", "arguments": {...}}}]  (arguments = dict, not string)
            if role == "assistant" and out.get("tool_calls"):
                _native_tcs = []
                for _tc in out["tool_calls"]:
                    _fn = _tc.get("function", {})
                    _args = _fn.get("arguments", {})
                    if isinstance(_args, str):
                        try:
                            _args = json.loads(_args)
                        except (json.JSONDecodeError, ValueError):
                            _args = {}
                    _native_tcs.append({"function": {"name": _fn.get("name", ""), "arguments": _args}})
                out["tool_calls"] = _native_tcs
            return out

        _ollama_messages = [_normalize_for_ollama(m) for m in oai_messages]
        # Use streaming when CC requests it so tokens flow back immediately,
        # preventing CC connection timeouts while the model generates.
        # Treat "stream": null (CC retry) identically to "stream": true — Ollama must
        # stream tokens so the first byte arrives after model-load (~90 s), not after
        # full generation (load + gen > 120 s httpx timeout → false timeout on retries).
        _do_ollama_stream = body.get("stream") is not False
        _call_payload: dict = {
            "model":      effective_model,
            "messages":   _ollama_messages,
            "stream":     _do_ollama_stream,
            # No explicit keep_alive — respects each Ollama instance's own
            # server-configured OLLAMA_KEEP_ALIVE default instead of
            # silently overriding it.
            "options":    {"num_ctx": max(_ollama_num_ctx, 32768), "num_predict": max_tokens},
        }
        # NATIVE OLLAMA TOOL CALLING: pass tools directly to Ollama's /api/chat instead
        # of injecting a format schema into the system prompt. Native tool calling uses
        # the model's trained function-calling behavior, which is more reliable for long
        # contexts (50k+ tokens) where format-schema injection was frequently ignored.
        # During tool-call generation, Ollama streams empty content chunks → we send pings
        # to CC to keep the connection alive. Tool calls appear in the done chunk.
        #
        # Some models' Ollama grammar/sampler compiler cannot handle Claude Code's
        # larger tool schemas (e.g. /init sends ~26 tools) and fail the whole request
        # with HTTP 400 "Failed to initialize samplers: failed to parse grammar" before
        # generation even starts. For those (flagged native_tool_calling: false in
        # model_capabilities.yaml), omit `tools` entirely and fall back to text-mode:
        # the model gets no native schema, but the existing JSON-in-text detection
        # below (_apply_tool_candidates / bare-args fallback) still recovers tool calls
        # from its free-text output.
        _text_mode = bool(oai_tools) and not model_supports_native_tool_calling(effective_model)
        if oai_tools and not _text_mode:
            _call_payload["tools"] = oai_tools
            _tool_names_log = [
                ((_ot.get("function") or _ot).get("name", "?")) for _ot in oai_tools
            ]
            logger.info(
                "cc_tool: native tool calling — %d tools (names=%s)",
                len(oai_tools), _tool_names_log,
            )
        elif _text_mode:
            logger.info(
                "cc_tool: text-mode tool calling (native_tool_calling=false for %s) — %d tools",
                effective_model, len(oai_tools),
            )
        # Use Ollama's native think:false flag (Ollama ≥0.30) to disable thinking mode.
        # The previous /no_think prefix approach was unreliable — qwen3.6:35b ignored it
        # and consumed the entire num_predict budget on thinking, returning empty content.
        # think:false is a top-level parameter (not inside options) per Ollama 0.30 API.
        if not session.stream_think:
            _call_payload["think"] = False
        _msg_count = len(_ollama_messages)
        _total_chars = sum(len(json.dumps(m)) for m in _ollama_messages)
        _first_role = _ollama_messages[0].get("role") if _ollama_messages else "none"
        _last_role = _ollama_messages[-1].get("role") if _ollama_messages else "none"
        logger.info(
            "cc_tool: Ollama native /api/chat — num_ctx=%d num_predict=%d model=%s think=%s msg_count=%d total_chars=%d first=%s last=%s",
            _ollama_num_ctx, max_tokens, effective_model, session.stream_think, _msg_count, _total_chars, _first_role, _last_role,
        )
    else:
        _call_url = f"{effective_url}/chat/completions"
        _call_payload = payload
        # Diagnostic for provider-side message-shape rejections (observed: Hetzner
        # 400 "System message must be at the beginning", "No user query found in
        # messages") — cheap, always-on: logs role sequence + system-message count
        # so a future occurrence has the actual payload shape instead of guesswork.
        _oai_roles = [m.get("role") for m in oai_messages]
        _sys_positions = [i for i, r in enumerate(_oai_roles) if r == "system"]
        if _sys_positions != ([0] if _sys_positions else []):
            logger.warning(
                "cc_tool: unexpected system-message position(s) %s in payload to %s "
                "(roles=%s, session=%s)",
                _sys_positions, effective_node, _oai_roles, session_id[:8] if session_id else "none",
            )
        logger.debug(
            "cc_tool: OpenAI payload — node=%s msg_count=%d roles=%s",
            effective_node, len(_oai_roles), _oai_roles,
        )

    # Pre-check: if this endpoint is known to be rate-limited, fail fast instead of timing out.
    # This prevents Claude Code CLI from making 10 retry attempts and risking a DDoS ban.
    _tool_ep = CLAUDE_CODE_TOOL_ENDPOINT if (effective_url == _CLAUDE_CODE_TOOL_URL) else None
    if _tool_ep and _check_rate_limit_exhausted(_tool_ep):
        _rl_entry = _provider_rate_limits.get(_tool_ep, {})
        _reset_str = ""
        if _rl_entry.get("reset_time"):
            import datetime as _dt
            _reset_str = f" Reset: {_dt.datetime.fromtimestamp(_rl_entry['reset_time']).strftime('%H:%M:%S')}."
        _err_body = {"type": "error", "error": {"type": "overloaded_error",
            "message": f"Provider token limit exhausted.{_reset_str} Remaining: {_rl_entry.get('remaining_tokens', 0)}"}}
        asyncio.create_task(_deregister_active_request(chat_id))
        if do_stream:
            async def _rl_err_stream():
                yield f"data: {json.dumps(_err_body)}\n\n"
            return StreamingResponse(_rl_err_stream(), media_type="text/event-stream", status_code=529, headers=_moe_response_headers(effective_model, effective_node))
        from fastapi.responses import JSONResponse as _JSONResponse
        return _JSONResponse(content=_err_body, status_code=529)

    # ── Live-streaming path (Ollama native, CC streaming request) ─────────────
    # When Claude Code requests a streaming response AND the endpoint is Ollama
    # native, pipe tokens directly back to CC as they arrive. This prevents CC
    # from timing out while waiting for a complete non-streaming Ollama response
    # (which can take 60–120 s for complex tool-call reasoning at 35B).
    await _record_stage(chat_id, "tool_model_call", "started", effective_model)
    logger.info(
        "cc_tool: routing — ollama_native=%s stream=%s session=%s",
        _use_ollama_native, body.get("stream"), session_id[:8] if session_id else "?",
    )
    # CC retries send "stream": null instead of true — treat None as True for Ollama native.
    # Non-streaming Ollama calls always timeout CC anyway (full generation before first byte).
    if _use_ollama_native and body.get("stream") is not False:
        logger.info("cc_tool: entering live streaming path (model=%s stream=%s)", effective_model, body.get("stream"))
        _model_id_out = body.get("model", effective_model)

        async def _live_ollama_sse():
            _text_acc: str = ""
            _tool_calls_acc: list = []
            _sent_text_block: bool = False
            _block_idx: int = 0
            _in_tok: int = 0
            _out_tok: int = 0
            _stream_error: str = ""
            _last_yield_time: float = time.monotonic()

            # Emit message_start immediately — CC sees the stream open and won't retry
            yield _sse_event("message_start", {
                "type": "message_start",
                "message": {
                    "id": chat_id, "type": "message", "role": "assistant",
                    "content": [], "model": _model_id_out,
                    "stop_reason": None,
                    "usage": {"input_tokens": 0, "output_tokens": 1},
                },
            })
            yield _sse_event("ping", {"type": "ping"})

            # Bridge Ollama streaming and SSE via a queue so we can send keep-alive
            # SSE comment lines while waiting for the model to load (up to 90s for
            # 35B models). Without this, CC would see silence and disconnect.
            _q: asyncio.Queue = asyncio.Queue()

            async def _ollama_fetch() -> None:
                try:
                    # Proactive self-throttle: waits here (behind the message_start/
                    # ping keep-alive already streaming to the client) instead of
                    # firing and risking a provider 429 that aborts the CC session.
                    await _throttle_endpoint(effective_node, stage="cc_tool_stream_rate_limit",
                                              limit=_rl_override, key=_rl_key)
                    async with httpx.AsyncClient(timeout=_node_timeout) as _scl:
                        async with _scl.stream(
                            "POST", _call_url, json=_call_payload,
                            headers={"Authorization": f"Bearer {effective_token}"},
                        ) as _sr:
                            if _sr.status_code == 400:
                                await _q.put(("error", f"Ollama 400: {(await _sr.aread()).decode()[:200]}"))
                            else:
                                async for _ln in _sr.aiter_lines():
                                    await _q.put(("line", _ln))
                except (httpx.ReadTimeout, httpx.ConnectTimeout, asyncio.TimeoutError) as _te:
                    await _q.put(("error", f"timeout after {_node_timeout}s"))
                except Exception as _se:
                    await _q.put(("error", str(_se)))
                finally:
                    await _q.put(("done", None))

            # Cancel zombie task from a previous CC retry for the same session before
            # starting a new fetch. Without this, stale Ollama requests keep running and
            # queue up, blocking the next CC request from ever getting a first token.
            if session_id:
                _old_task = _cc_active_ollama_tasks.get(session_id)
                if _old_task and not _old_task.done():
                    _old_task.cancel()
                    logger.info("cc_tool: cancelled zombie Ollama task (session=%s)", session_id[:8])
            _fetch_task = asyncio.create_task(_ollama_fetch())
            if session_id:
                _cc_active_ollama_tasks[session_id] = _fetch_task
                # Compact: drop finished entries so the dict doesn't grow unboundedly
                # across days of uptime with many sessions.
                if len(_cc_active_ollama_tasks) > 50:
                    _done_keys = [k for k, t in _cc_active_ollama_tasks.items() if t.done()]
                    for _dk in _done_keys:
                        _cc_active_ollama_tasks.pop(_dk, None)

            try:
                while True:
                    try:
                        _kind, _val = await asyncio.wait_for(_q.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        yield _sse_event("ping", {"type": "ping"})
                        continue
                    if _kind == "done":
                        break
                    if _kind == "error":
                        _stream_error = _val
                        break
                    _line = _val
                    if not _line.strip():
                        continue
                    try:
                        _chunk = json.loads(_line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    _cmsg = _chunk.get("message", {})
                    _delta = _cmsg.get("content", "") or ""
                    _chunk_tcs = _cmsg.get("tool_calls")
                    _done = _chunk.get("done", False)

                    if _delta:
                        _text_acc += _delta
                        if _text_mode or _require_tools:
                            # Buffer internally — text-mode parses tool JSON at stream
                            # end; required-mode must not stream a possibly non-compliant
                            # announcement before knowing whether tool calls arrive.
                            # Queue-timeout pings (15s) never fire when tokens arrive
                            # continuously, so send pings here to keep the SSE stream live.
                            if time.monotonic() - _last_yield_time > 10.0:
                                yield _sse_event("ping", {"type": "ping"})
                                _last_yield_time = time.monotonic()
                        else:
                            if not _sent_text_block:
                                yield _sse_event("content_block_start", {
                                    "type": "content_block_start", "index": 0,
                                    "content_block": {"type": "text", "text": ""},
                                })
                                _sent_text_block = True
                            yield _sse_event("content_block_delta", {
                                "type": "content_block_delta", "index": 0,
                                "delta": {"type": "text_delta", "text": _delta},
                            })
                            _last_yield_time = time.monotonic()

                    if _chunk_tcs:
                        # Ollama streams tool calls incrementally across chunks —
                        # accumulate instead of overwriting (overwrite dropped all
                        # but the last chunk's calls on multi-chunk tool streams).
                        for _new_tc in _chunk_tcs:
                            if _new_tc not in _tool_calls_acc:
                                _tool_calls_acc.append(_new_tc)

                    # When Ollama streams tool-call tokens, intermediate chunks carry
                    # empty content and partial tool_calls JSON — the queue never
                    # drains and no content is yielded, so neither the queue-timeout
                    # nor the text-delta path sends anything to the client. Send a
                    # real Anthropic ping every 10s so the SDK resets its idle timer.
                    # NOTE: do NOT gate on `not _chunk_tcs` — that is False exactly
                    # when tool-call chunks are accumulating, which is the case that
                    # needs the keep-alive most.
                    if not _delta and not _done:
                        if time.monotonic() - _last_yield_time > 10.0:
                            yield _sse_event("ping", {"type": "ping"})
                            _last_yield_time = time.monotonic()

                    if _done:
                        _in_tok = _chunk.get("prompt_eval_count", 0)
                        _out_tok = _chunk.get("eval_count", 0)
                        _done_reason = _chunk.get("done_reason", "")
                        _cmsg_content = _cmsg.get("content", "") or ""
                        logger.info(
                            "cc_tool: Ollama done — done_reason=%s text_chars=%d in_tok=%d out_tok=%s msg_preview=%.100r",
                            _done_reason, len(_cmsg_content), _in_tok, _out_tok, _cmsg_content,
                        )
            except Exception as _loop_err:
                _stream_error = str(_loop_err)
                logger.warning("cc_tool stream loop error: %s", _loop_err)
            finally:
                # Only remove our own entry — a new CC retry may have already replaced it.
                if session_id and _cc_active_ollama_tasks.get(session_id) is _fetch_task:
                    _cc_active_ollama_tasks.pop(session_id, None)
                if not _fetch_task.done():
                    _fetch_task.cancel()
            if _stream_error and "timeout" in _stream_error.lower():
                PROM_TOOL_TIMEOUTS.labels(node=effective_node, model=effective_model).inc()

            if _stream_error:
                _err_txt = f"⚠️ Inference server '{effective_node}' error: {_stream_error}"
                if not _sent_text_block:
                    yield _sse_event("content_block_start", {
                        "type": "content_block_start", "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    })
                    _sent_text_block = True
                yield _sse_event("content_block_delta", {
                    "type": "content_block_delta", "index": 0,
                    "delta": {"type": "text_delta", "text": _err_txt},
                })

            # Close text block if one was opened
            if _sent_text_block:
                yield _sse_event("content_block_stop", {
                    "type": "content_block_stop", "index": _block_idx,
                })
                _block_idx += 1

            # JSON-in-text fallback: detect tool call JSON in accumulated text.
            # Used in text-mode (tools injected as system prompt) and as safety net.
            if not _tool_calls_acc and _text_acc and tools and not _stream_error:
                _known = {t.get("name", "") for t in tools}
                _probe = re.sub(r"^```(?:json)?\s*|\s*```$", "", _text_acc.strip(), flags=re.DOTALL).strip()

                def _apply_tool_candidates(_parsed):
                    _cands = [_parsed] if isinstance(_parsed, dict) else (_parsed if isinstance(_parsed, list) else [])
                    for _c in _cands:
                        if isinstance(_c, dict):
                            _tn = _c.get("name") or _c.get("tool") or _c.get("function")
                            _ta = _c.get("arguments") or _c.get("parameters") or _c.get("input") or {}
                            if _tn and _tn in _known:
                                _tool_calls_acc.append({"function": {"name": _tn, "arguments": _ta}})

                try:
                    _apply_tool_candidates(json.loads(_probe))
                except (json.JSONDecodeError, ValueError):
                    # Secondary: raw_decode finds first complete JSON object even with
                    # preamble text (e.g. "I'll call the tool now.\n{\"name\": ...}").
                    _dec = json.JSONDecoder()
                    _pos = _probe.find("{")
                    while _pos >= 0 and not _tool_calls_acc:
                        try:
                            _found, _ = _dec.raw_decode(_probe, _pos)
                            _apply_tool_candidates(_found)
                        except (json.JSONDecodeError, ValueError):
                            pass
                        _pos = _probe.find("{", _pos + 1)

                # Tertiary (text-mode only): model output bare arguments dict without the
                # {"name":..., "arguments":...} wrapper — detect by matching keys to tool params.
                if not _tool_calls_acc and _text_mode and len(tools) == 1:
                    _only = tools[0]
                    _only_name = _only.get("name", "")
                    _only_params = set(
                        (_only.get("input_schema") or {}).get("properties", {}).keys()
                    )
                    try:
                        _bare = json.loads(_probe)
                        if (isinstance(_bare, dict)
                                and not any(k in _bare for k in ("name", "tool", "function", "arguments"))
                                and _only_params
                                and any(k in _only_params for k in _bare.keys())):
                            _tool_calls_acc.append({"function": {"name": _only_name, "arguments": _bare}})
                            logger.info("cc_tool: bare-args fallback matched tool '%s'", _only_name)
                    except (json.JSONDecodeError, ValueError):
                        pass

            # Diagnose text-mode result
            if _text_mode:
                logger.info(
                    "cc_tool: text-mode result — text_chars=%d tool_calls=%d out_tok=%d stream_error=%r",
                    len(_text_acc), len(_tool_calls_acc), _out_tok, bool(_stream_error),
                )
                # Thinking-only guard: model generated tokens but no content or tool calls.
                # This happens when think=false is ignored in streaming mode (long context).
                # Retry synchronously without format schema — warm KV cache makes this fast.
                if not _text_acc and not _tool_calls_acc and _out_tok > 0 and not _stream_error:
                    logger.warning(
                        "cc_tool: thinking-only output (%d tokens, no content) — "
                        "retrying without format schema (non-streaming)",
                        _out_tok,
                    )
                    _retry_payload = {k: v for k, v in _call_payload.items() if k != "format"}
                    _retry_payload["stream"] = False
                    try:
                        async def _do_thinking_retry():
                            async with httpx.AsyncClient(timeout=300.0) as _rcl:
                                return await _rcl.post(
                                    _call_url, json=_retry_payload,
                                    headers={"Authorization": f"Bearer {effective_token}"},
                                )
                        _retry_task = asyncio.create_task(_do_thinking_retry())
                        while not _retry_task.done():
                            try:
                                await asyncio.wait_for(asyncio.shield(_retry_task), timeout=10.0)
                            except asyncio.TimeoutError:
                                yield _sse_event("ping", {"type": "ping"})
                                _last_yield_time = time.monotonic()
                        _rr = await _retry_task
                        _rj = _rr.json()
                        _rmsg = _rj.get("message", {})
                        _retry_content = _rmsg.get("content", "") or ""
                        _retry_tcs = _rmsg.get("tool_calls")
                        _retry_eval = _rj.get("eval_count", 0)
                        logger.info(
                            "cc_tool: thinking-retry done — content_chars=%d tool_calls=%s eval_count=%d",
                            len(_retry_content), bool(_retry_tcs), _retry_eval,
                        )
                        if _retry_tcs:
                            _tool_calls_acc = _retry_tcs
                            _out_tok += _retry_eval
                        elif _retry_content:
                            _text_acc = _retry_content
                            _out_tok += _retry_eval
                    except Exception as _re:
                        logger.warning("cc_tool: thinking-retry failed: %s", _re)

                if _text_acc and not _tool_calls_acc and not _stream_error:
                    # JSON parsing failed — model ignored the format schema and generated
                    # plain text. Emit the actual text so CC sees what the model said
                    # instead of receiving an empty block that triggers "API returned an
                    # empty or malformed response (HTTP 200)".
                    _clean_acc = re.sub(r"<think>.*?</think>", "", _text_acc, flags=re.S).strip()
                    logger.warning(
                        "cc_tool: text-mode JSON parse failed — emitting raw text (%d chars, preview=%.200r)",
                        len(_text_acc), _text_acc,
                    )
                    yield _sse_event("content_block_start", {
                        "type": "content_block_start", "index": _block_idx,
                        "content_block": {"type": "text", "text": ""},
                    })
                    yield _sse_event("content_block_delta", {
                        "type": "content_block_delta", "index": _block_idx,
                        "delta": {"type": "text_delta", "text": _clean_acc or _text_acc},
                    })
                    yield _sse_event("content_block_stop", {
                        "type": "content_block_stop", "index": _block_idx,
                    })
                    _block_idx += 1
                    _sent_text_block = True

            # ── tool_choice=required enforcement retry ────────────────────────
            # The profile demands a tool call but the model returned none (Ollama
            # native cannot enforce tool_choice). Retry ONCE against the expert
            # template's tool_agent before degrading to a plain text turn. Without
            # this, CC receives an announcement ("I will now analyze…") with a
            # clean end_turn and closes the stream while nothing was executed.
            if _require_tools and not _tool_calls_acc and not _stream_error:
                _fb_exp = next(
                    (
                        _c for _c in ((session.experts or {}).get("tool_agent") or [])
                        if _c.get("model") and _c.get("url") and _c.get("model") != effective_model
                    ),
                    None,
                )
                if _fb_exp:
                    logger.warning(
                        "cc_tool: tool_choice=required violated by %s (text_chars=%d, no tool call)"
                        " — retrying with template tool_agent %s@%s",
                        effective_model, len(_text_acc),
                        _fb_exp["model"], _fb_exp.get("endpoint", "?"),
                    )
                    _fb_base = _fb_exp["url"].rstrip("/").removesuffix("/v1")
                    _fb_payload = dict(_call_payload)
                    _fb_payload["model"]  = _fb_exp["model"]
                    _fb_payload["stream"] = False
                    _fb_payload["options"] = {
                        "num_ctx": max(int(_fb_exp.get("context_window") or 0), _ollama_num_ctx, 32768),
                        "num_predict": max_tokens,
                    }
                    try:
                        async def _do_required_retry():
                            async with httpx.AsyncClient(timeout=300.0) as _rcl:
                                return await _rcl.post(
                                    f"{_fb_base}/api/chat", json=_fb_payload,
                                    headers={"Authorization": f"Bearer {_fb_exp.get('token', 'ollama')}"},
                                )
                        _req_retry_task = asyncio.create_task(_do_required_retry())
                        while not _req_retry_task.done():
                            try:
                                await asyncio.wait_for(asyncio.shield(_req_retry_task), timeout=10.0)
                            except asyncio.TimeoutError:
                                yield _sse_event("ping", {"type": "ping"})
                        _rr_json = (await _req_retry_task).json()
                        _rr_msg  = _rr_json.get("message", {})
                        _rr_tcs  = _rr_msg.get("tool_calls")
                        logger.info(
                            "cc_tool: required-retry done — model=%s tool_calls=%s content_chars=%d",
                            _fb_exp["model"], bool(_rr_tcs), len(_rr_msg.get("content", "") or ""),
                        )
                        if _rr_tcs:
                            _tool_calls_acc = list(_rr_tcs)
                            _out_tok += _rr_json.get("eval_count", 0)
                            # Replace the non-compliant announcement with the
                            # compliant model's own accompanying text (may be empty).
                            _text_acc = _rr_msg.get("content", "") or ""
                    except Exception as _fre:
                        logger.warning("cc_tool: required-retry failed: %s", _fre)
                else:
                    logger.warning(
                        "cc_tool: tool_choice=required violated by %s and no template"
                        " tool_agent fallback available — degrading to text turn",
                        effective_model,
                    )

            # Buffered text from required-mode (live streaming was suppressed):
            # emit it now. After a successful retry this is the retry's own text;
            # after a failed/absent retry it is the original announcement so CC
            # at least sees what the model said.
            if _require_tools and _text_acc and not _sent_text_block and not _stream_error:
                _clean_req = re.sub(r"<think>.*?</think>", "", _text_acc, flags=re.S).strip()
                if _clean_req:
                    yield _sse_event("content_block_start", {
                        "type": "content_block_start", "index": _block_idx,
                        "content_block": {"type": "text", "text": ""},
                    })
                    yield _sse_event("content_block_delta", {
                        "type": "content_block_delta", "index": _block_idx,
                        "delta": {"type": "text_delta", "text": _clean_req},
                    })
                    yield _sse_event("content_block_stop", {
                        "type": "content_block_stop", "index": _block_idx,
                    })
                    _block_idx += 1
                    _sent_text_block = True

            # Tool call blocks
            _stop_reason = "end_turn"
            for _ntc in _tool_calls_acc:
                _fn = _ntc.get("function", {})
                _args = _fn.get("arguments", {})
                _tc_id = f"call_{uuid.uuid4().hex[:12]}"
                _args_json = json.dumps(_args) if isinstance(_args, dict) else str(_args)
                # File-touch extraction for the live-pipeline-visualization's
                # "which files did this session touch" view — fire-and-forget,
                # never affects the tool_use block emitted below.
                _fh_args = _args
                if not isinstance(_fh_args, dict):
                    try:
                        _fh_args = json.loads(_args) if isinstance(_args, str) else {}
                    except Exception:
                        _fh_args = {}
                for _touch in extract_file_touches(_fn.get("name", ""), _fh_args):
                    asyncio.create_task(_record_file_touch(
                        chat_id, _touch["path"], _touch["action"], _touch["tool"],
                    ))
                yield _sse_event("content_block_start", {
                    "type": "content_block_start", "index": _block_idx,
                    "content_block": {
                        "type": "tool_use", "id": _tc_id,
                        "name": _fn.get("name", ""), "input": {},
                    },
                })
                yield _sse_event("content_block_delta", {
                    "type": "content_block_delta", "index": _block_idx,
                    "delta": {"type": "input_json_delta", "partial_json": _args_json},
                })
                yield _sse_event("content_block_stop", {
                    "type": "content_block_stop", "index": _block_idx,
                })
                _block_idx += 1
                _stop_reason = "tool_use"

            # Empty response guard
            if not _sent_text_block and not _tool_calls_acc:
                yield _sse_event("content_block_start", {
                    "type": "content_block_start", "index": 0,
                    "content_block": {"type": "text", "text": ""},
                })
                yield _sse_event("content_block_stop", {
                    "type": "content_block_stop", "index": 0,
                })

            yield _sse_event("message_delta", {
                "type": "message_delta",
                "delta": {"stop_reason": _stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": _out_tok},
            })
            yield _sse_event("message_stop", {"type": "message_stop"})

            PROM_TOOL_CALL_DURATION.labels(
                node=effective_node, model=effective_model, phase="llm_call"
            ).observe(time.monotonic() - _llm_t0)
            asyncio.create_task(_record_node_latency(
                effective_url, effective_model, (time.monotonic() - _llm_t0) * 1000,
            ))
            if _tool_calls_acc:
                PROM_TOOL_CALL_SUCCESS.labels(node=effective_node, model=effective_model).inc()
            if user_id != "anon":
                asyncio.create_task(_log_usage_to_db(
                    user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
                    model=effective_model, moe_mode="cc_tool",
                    prompt_tokens=_in_tok, completion_tokens=_out_tok,
                    session_id=session_id,
                ))
                if not session.is_user_conn:
                    asyncio.create_task(_increment_user_budget(
                        user_id, _in_tok + _out_tok,
                        prompt_tokens=_in_tok, completion_tokens=_out_tok,
                    ))
            asyncio.create_task(_deregister_active_request(chat_id))
            asyncio.create_task(_record_stage(chat_id, "tool_model_call", "error" if _stream_error else "done"))

            # ── Tier-4 Episodic Write-Back (fire-and-forget) ──────────────────
            # Parity fix: this path previously lacked the episodic write-back
            # that _live_openai_sse already has (native-Ollama CC sessions never
            # logged an episode). Same gate as there.
            if (CC_CONTEXT_INDEX_ENABLED and session.long_memory
                    and state.graph_manager is not None and _cc_ep_query
                    and _stop_reason == "end_turn" and not _stream_error):
                try:
                    from episodic_memory import log_episode as _log_ep
                    _ep_state = {
                        "input": _cc_ep_query,
                        "user_id": user_id or "anon",
                        "expert_models_used": [effective_model],
                        "prompt_tokens": _in_tok,
                        "completion_tokens": _out_tok,
                        "plan": [{"category": "general"}],
                        "final_response": _text_acc[:600],
                    }
                    asyncio.create_task(_log_ep(state.graph_manager.driver, _ep_state))
                except Exception:
                    pass

            # ── Augmented Tool Path: write-back (fire-and-forget) ─────────────
            # Opt-in (session.agent_ingest, default off). Only on a clean turn
            # end: end_turn stop reason, no tool_use emitted, no stream error.
            if (session.agent_ingest and _cc_ep_query and _text_acc
                    and _stop_reason == "end_turn" and not _stream_error):
                asyncio.create_task(_agent_writeback_traced(
                    chat_id,
                    _cc_ep_query, _text_acc, _agent_turn.scope,
                    f"user:{user_id}" if user_id and user_id != "anon" else None,
                    user_id, effective_model, session_id or "",
                    state.redis_client, state.agent_cache_collection,
                    path="anthropic",
                ))

        _llm_t0 = time.monotonic()
        return StreamingResponse(_wrap_deregister_on_stream_end(_live_ollama_sse(), chat_id), media_type="text/event-stream", headers=_moe_response_headers(effective_model, effective_node))

    # ── Live-streaming path for OpenAI-compatible external endpoints ──────────
    # When the endpoint is not Ollama (e.g. AIHUB) but CC requests streaming,
    # pipe tokens back to CC as they arrive instead of blocking on a non-streaming
    # call. Without this, CC waits for the first SSE byte for 30–120 s and times out
    # even though the backend is healthy.
    if not _use_ollama_native and body.get("stream") is not False:
        _model_id_out = body.get("model", effective_model)
        logger.info(
            "cc_tool: entering OpenAI live-streaming path (model=%s node=%s stream=%s)",
            effective_model, effective_node, body.get("stream"),
        )

        async def _live_openai_sse():
            _text_acc: str = ""
            _tool_calls_acc: dict = {}   # {oai_index: {id, name, arguments}}
            _sent_text_block: bool = False
            _block_idx: int = 0
            _in_tok: int = 0
            _out_tok: int = 0
            _stream_error: str = ""

            yield _sse_event("message_start", {
                "type": "message_start",
                "message": {
                    "id": chat_id, "type": "message", "role": "assistant",
                    "content": [], "model": _model_id_out,
                    "stop_reason": None,
                    "usage": {"input_tokens": 0, "output_tokens": 1},
                },
            })
            yield _sse_event("ping", {"type": "ping"})

            _q: asyncio.Queue = asyncio.Queue()
            _streaming_payload = {**_call_payload, "stream": True, "stream_options": {"include_usage": True}}

            async def _openai_fetch() -> None:
                try:
                    # Proactive self-throttle — see _ollama_fetch above.
                    await _throttle_endpoint(effective_node, stage="cc_tool_stream_rate_limit",
                                              limit=_rl_override, key=_rl_key)
                    async with httpx.AsyncClient(timeout=_node_timeout) as _scl:
                        async with _scl.stream(
                            "POST", _call_url, json=_streaming_payload,
                            headers={"Authorization": f"Bearer {effective_token}"},
                        ) as _sr:
                            if _sr.status_code >= 400:
                                _body_err = await _sr.aread()
                                await _q.put(("error", f"HTTP {_sr.status_code}: {_body_err.decode()[:200]}"))
                            else:
                                async for _ln in _sr.aiter_lines():
                                    await _q.put(("line", _ln))
                except (httpx.ReadTimeout, httpx.ConnectTimeout, asyncio.TimeoutError):
                    await _q.put(("error", f"timeout after {_node_timeout}s"))
                except Exception as _se:
                    await _q.put(("error", str(_se)))
                finally:
                    await _q.put(("done", None))

            if session_id:
                _old_task = _cc_active_ollama_tasks.get(session_id)
                if _old_task and not _old_task.done():
                    _old_task.cancel()
                    logger.info("cc_tool: cancelled zombie OpenAI task (session=%s)", session_id[:8])
            _fetch_task = asyncio.create_task(_openai_fetch())
            if session_id:
                _cc_active_ollama_tasks[session_id] = _fetch_task
                if len(_cc_active_ollama_tasks) > 50:
                    _done_keys = [k for k, t in _cc_active_ollama_tasks.items() if t.done()]
                    for _dk in _done_keys:
                        _cc_active_ollama_tasks.pop(_dk, None)

            try:
                while True:
                    try:
                        _kind, _val = await asyncio.wait_for(_q.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        yield _sse_event("ping", {"type": "ping"})
                        continue
                    if _kind == "done":
                        break
                    if _kind == "error":
                        _stream_error = _val
                        break
                    _line = _val
                    if not _line.strip() or not _line.startswith("data:"):
                        continue
                    _data_str = _line[5:].strip()
                    if _data_str == "[DONE]":
                        break
                    try:
                        _chunk = json.loads(_data_str)
                    except (json.JSONDecodeError, ValueError):
                        continue

                    if _chunk.get("usage"):
                        _in_tok = _chunk["usage"].get("prompt_tokens", 0)
                        _out_tok = _chunk["usage"].get("completion_tokens", 0)

                    for _ch in _chunk.get("choices", []):
                        _d = _ch.get("delta", {})
                        _text = _d.get("content") or ""
                        if _text:
                            if not _sent_text_block:
                                yield _sse_event("content_block_start", {
                                    "type": "content_block_start", "index": 0,
                                    "content_block": {"type": "text", "text": ""},
                                })
                                _sent_text_block = True
                            yield _sse_event("content_block_delta", {
                                "type": "content_block_delta", "index": 0,
                                "delta": {"type": "text_delta", "text": _text},
                            })
                            _text_acc += _text

                        for _tc in (_d.get("tool_calls") or []):
                            _i = _tc.get("index", 0)
                            if _i not in _tool_calls_acc:
                                _tool_calls_acc[_i] = {"id": "", "name": "", "arguments": ""}
                            _e = _tool_calls_acc[_i]
                            if _tc.get("id"):
                                _e["id"] = _tc["id"]
                            _fn = _tc.get("function", {})
                            if _fn.get("name"):
                                _e["name"] = _fn["name"]
                            if _fn.get("arguments"):
                                _e["arguments"] += _fn["arguments"]
            except Exception as _le:
                _stream_error = str(_le)
                logger.warning("cc_tool OpenAI stream loop error: %s", _le)
            finally:
                if session_id and _cc_active_ollama_tasks.get(session_id) is _fetch_task:
                    _cc_active_ollama_tasks.pop(session_id, None)
                if not _fetch_task.done():
                    _fetch_task.cancel()

            if _stream_error and "timeout" in _stream_error.lower():
                PROM_TOOL_TIMEOUTS.labels(node=effective_node, model=effective_model).inc()

            if _stream_error:
                _err_txt = f"⚠️ Inference server '{effective_node}' error: {_stream_error}"
                if not _sent_text_block:
                    yield _sse_event("content_block_start", {
                        "type": "content_block_start", "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    })
                    _sent_text_block = True
                yield _sse_event("content_block_delta", {
                    "type": "content_block_delta", "index": 0,
                    "delta": {"type": "text_delta", "text": _err_txt},
                })

            if _sent_text_block:
                yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": 0})
                _block_idx = 1

            _stop_reason = "end_turn"
            for _i in sorted(_tool_calls_acc):
                _e = _tool_calls_acc[_i]
                _tc_id = _e["id"] or f"call_{uuid.uuid4().hex[:12]}"
                yield _sse_event("content_block_start", {
                    "type": "content_block_start", "index": _block_idx,
                    "content_block": {
                        "type": "tool_use", "id": _tc_id,
                        "name": _e["name"], "input": {},
                    },
                })
                yield _sse_event("content_block_delta", {
                    "type": "content_block_delta", "index": _block_idx,
                    "delta": {"type": "input_json_delta", "partial_json": _e["arguments"]},
                })
                yield _sse_event("content_block_stop", {
                    "type": "content_block_stop", "index": _block_idx,
                })
                _block_idx += 1
                _stop_reason = "tool_use"

            if not _sent_text_block and not _tool_calls_acc:
                yield _sse_event("content_block_start", {
                    "type": "content_block_start", "index": 0,
                    "content_block": {"type": "text", "text": ""},
                })
                yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": 0})

            yield _sse_event("message_delta", {
                "type": "message_delta",
                "delta": {"stop_reason": _stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": _out_tok},
            })
            yield _sse_event("message_stop", {"type": "message_stop"})

            PROM_TOOL_CALL_DURATION.labels(
                node=effective_node, model=effective_model, phase="llm_call"
            ).observe(time.monotonic() - _llm_t0)
            asyncio.create_task(_record_node_latency(
                effective_url, effective_model, (time.monotonic() - _llm_t0) * 1000,
            ))
            if _tool_calls_acc:
                PROM_TOOL_CALL_SUCCESS.labels(node=effective_node, model=effective_model).inc()
            if user_id != "anon":
                asyncio.create_task(_log_usage_to_db(
                    user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
                    model=effective_model, moe_mode="cc_tool",
                    prompt_tokens=_in_tok, completion_tokens=_out_tok,
                    session_id=session_id,
                ))
                if not session.is_user_conn:
                    asyncio.create_task(_increment_user_budget(
                        user_id, _in_tok + _out_tok,
                        prompt_tokens=_in_tok, completion_tokens=_out_tok,
                    ))
            asyncio.create_task(_deregister_active_request(chat_id))
            asyncio.create_task(_record_stage(chat_id, "tool_model_call", "error" if _stream_error else "done"))

            # ── Tier-4 Episodic Write-Back (fire-and-forget) ──────────────────
            if (CC_CONTEXT_INDEX_ENABLED and session.long_memory
                    and state.graph_manager is not None and _cc_ep_query
                    and _stop_reason == "end_turn" and not _stream_error):
                try:
                    from episodic_memory import log_episode as _log_ep
                    _ep_state = {
                        "input": _cc_ep_query,
                        "user_id": user_id or "anon",
                        "expert_models_used": [effective_model],
                        "prompt_tokens": _in_tok,
                        "completion_tokens": _out_tok,
                        "plan": [{"category": "general"}],
                        "final_response": _text_acc[:600],
                    }
                    asyncio.create_task(_log_ep(state.graph_manager.driver, _ep_state))
                except Exception:
                    pass

            # ── Augmented Tool Path: write-back (fire-and-forget) ─────────────
            # Opt-in (session.agent_ingest, default off). Only on a clean turn
            # end: end_turn stop reason, no tool_use emitted, no stream error.
            if (session.agent_ingest and _cc_ep_query and _text_acc
                    and _stop_reason == "end_turn" and not _stream_error):
                asyncio.create_task(_agent_writeback_traced(
                    chat_id,
                    _cc_ep_query, _text_acc, _agent_turn.scope,
                    f"user:{user_id}" if user_id and user_id != "anon" else None,
                    user_id, effective_model, session_id or "",
                    state.redis_client, state.agent_cache_collection,
                    path="anthropic",
                ))

        _llm_t0 = time.monotonic()
        return StreamingResponse(_wrap_deregister_on_stream_end(_live_openai_sse(), chat_id), media_type="text/event-stream", headers=_moe_response_headers(effective_model, effective_node))

    # ── Non-streaming fallback (non-Ollama or non-streaming request) ───────────
    _llm_t0 = time.monotonic()
    try:
        # Proactive self-throttle — see _ollama_fetch above; no keep-alive stream
        # to hide the wait behind here, so keep endpoint rate limits realistic.
        await _throttle_endpoint(effective_node, stage="cc_tool_rate_limit",
                                  limit=_rl_override, key=_rl_key)
        async with httpx.AsyncClient(timeout=_node_timeout) as client:
            resp = await client.post(
                _call_url,
                json=_call_payload,
                headers={"Authorization": f"Bearer {effective_token}"}
            )
            # Parse and cache rate limit headers from the provider response
            if _tool_ep:
                _update_rate_limit_headers(_tool_ep, resp.headers, resp.status_code)
            if resp.status_code == 429:
                _rl_entry = _provider_rate_limits.get(_tool_ep or "", {})
                _reset_str = ""
                if _rl_entry.get("reset_time"):
                    import datetime as _dt
                    _reset_str = f" Reset: {_dt.datetime.fromtimestamp(_rl_entry['reset_time']).strftime('%H:%M:%S')}."
                _err_body = {"type": "error", "error": {"type": "overloaded_error",
                    "message": f"Provider Rate-Limit (429).{_reset_str}"}}
                asyncio.create_task(_deregister_active_request(chat_id))
                from fastapi.responses import JSONResponse as _JSONResponse
                return _JSONResponse(content=_err_body, status_code=529)
            # Ollama native /api/chat returns 400 on certain message structures
            # (e.g. complex tool schemas, unsupported fields). Fall back to the
            # OpenAI-compatible endpoint so Claude Code still receives a response.
            if resp.status_code == 400 and _use_ollama_native:
                logger.warning(
                    "cc_tool: Ollama native /api/chat 400 — falling back to /v1/chat/completions "
                    "(error: %s)", resp.text[:200],
                )
                _fallback_url = f"{effective_url}/chat/completions"
                resp = await client.post(
                    _fallback_url,
                    json=payload,                   # original OpenAI-format payload
                    headers={"Authorization": f"Bearer {effective_token}"},
                )
                _use_ollama_native = False          # parse response as OpenAI format
            resp.raise_for_status()
            oai_resp = resp.json()
    except (httpx.ReadTimeout, httpx.ConnectTimeout, asyncio.TimeoutError) as _tex:
        _llm_latency = time.monotonic() - _llm_t0
        PROM_TOOL_TIMEOUTS.labels(node=effective_node, model=effective_model).inc()
        PROM_TOOL_CALL_DURATION.labels(node=effective_node, model=effective_model, phase="llm_call").observe(_llm_latency)
        _tool_eval_logger.warning(json.dumps({
            "ts": datetime.utcnow().isoformat() + "Z",
            "chat_id": chat_id, "model": effective_model, "node": effective_node,
            "phase": "tool_call", "event": "timeout",
            "timeout_s": _node_timeout, "elapsed_s": round(_llm_latency, 3),
        }))
        logger.warning(f"⏱️ Tool handler timeout after {_llm_latency:.1f}s on {effective_node} (limit={_node_timeout}s)")
        # Return valid Anthropic response — Claude Code can abort or restart the prompt
        _timeout_text = (
            f"⚠️ The inference server '{effective_node}' did not respond within "
            f"{int(_node_timeout)}s. Please simplify the prompt or "
            f"choose a faster server."
        )
        _timeout_resp = {
            "id": chat_id, "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": _timeout_text}],
            "model": body.get("model", "moe-orchestrator-agent"),
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}
        }
        asyncio.create_task(_deregister_active_request(chat_id))
        if body.get("stream", False):
            return StreamingResponse(
                _anthropic_content_blocks_to_sse(
                    [{"type": "text", "text": _timeout_text}],
                    chat_id, body.get("model", "moe-orchestrator-agent"), 0, 0, "end_turn"
                ),
                media_type="text/event-stream",
                headers=_moe_response_headers(effective_model, effective_node)
            )
        return _timeout_resp
    except httpx.HTTPStatusError as _hex:
        _llm_latency = time.monotonic() - _llm_t0
        _status_code = _hex.response.status_code
        # Capture response body (first 2000 chars) to surface provider error details in logs
        try:
            _err_body_raw = _hex.response.text[:2000]
        except Exception:
            _err_body_raw = "<unreadable>"
        logger.warning(
            "⚠️ Tool handler HTTP error %s from %s: %s | provider_body: %s",
            _status_code, effective_node, _hex, _err_body_raw,
        )
        _tool_eval_logger.warning(json.dumps({
            "ts": datetime.utcnow().isoformat() + "Z",
            "chat_id": chat_id, "model": effective_model, "node": effective_node,
            "phase": "tool_call", "event": "http_error",
            "status_code": _status_code, "elapsed_s": round(_llm_latency, 3),
            "provider_error": _err_body_raw[:500],
        }))
        _err_text = (
            f"⚠️ The inference server '{effective_node}' returned an error "
            f"(HTTP {_status_code}). Please try again."
        )
        asyncio.create_task(_deregister_active_request(chat_id))
        if body.get("stream", False):
            return StreamingResponse(
                _anthropic_content_blocks_to_sse(
                    [{"type": "text", "text": _err_text}],
                    chat_id, body.get("model", "moe-orchestrator-agent"), 0, 0, "end_turn"
                ),
                media_type="text/event-stream",
                headers=_moe_response_headers(effective_model, effective_node)
            )
        return {
            "id": chat_id, "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": _err_text}],
            "model": body.get("model", "moe-orchestrator-agent"),
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}
        }

    _llm_latency = time.monotonic() - _llm_t0
    PROM_TOOL_CALL_DURATION.labels(node=effective_node, model=effective_model, phase="llm_call").observe(_llm_latency)

    # Normalize response: Ollama native /api/chat and OpenAI /v1/chat/completions differ.
    if _use_ollama_native:
        # Ollama native: {"message": {"role": ..., "content": ..., "tool_calls": [...]},
        #                 "prompt_eval_count": N, "eval_count": M}
        _native_msg = oai_resp.get("message", {})
        in_tok  = oai_resp.get("prompt_eval_count", 0)
        out_tok = oai_resp.get("eval_count", 0)
        # Convert Ollama tool_calls to OpenAI format (add synthetic id, stringify args)
        _native_tcs = _native_msg.get("tool_calls") or []
        _oai_tcs = []
        for _ntc in _native_tcs:
            _nfn = _ntc.get("function", {})
            _args = _nfn.get("arguments", {})
            _oai_tcs.append({
                "id":   f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name":      _nfn.get("name", "unknown"),
                    "arguments": json.dumps(_args) if isinstance(_args, dict) else str(_args),
                },
            })
        msg = {
            "role":       _native_msg.get("role", "assistant"),
            "content":    _native_msg.get("content") or "",
            "tool_calls": _oai_tcs or None,
        }
    else:
        choice  = oai_resp["choices"][0]
        msg     = choice["message"]
        usage   = oai_resp.get("usage", {})
        in_tok  = usage.get("prompt_tokens", 0)
        out_tok = usage.get("completion_tokens", 0)

    if user_id != "anon":
        asyncio.create_task(_log_usage_to_db(
            user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
            model=effective_model, moe_mode="cc_tool",
            prompt_tokens=in_tok, completion_tokens=out_tok, session_id=session_id))
        if not session.is_user_conn:
            asyncio.create_task(_increment_user_budget(user_id, in_tok + out_tok, prompt_tokens=in_tok, completion_tokens=out_tok))

    # Format detection: raw Qwen tags in text content indicate missing tool schema
    _raw_text = msg.get("content") or ""
    _has_qwen_tags = "<|invoke|>" in _raw_text or "<|plugin|>" in _raw_text
    _has_tool_calls = bool(msg.get("tool_calls"))
    _format_detected = "unknown"

    # Fallback detection: some models (e.g. Gemma, Mistral) return tool calls as
    # JSON in the content field instead of the tool_calls field.
    # Detection: content is valid JSON with "name"+"arguments"/"parameters"/"input" keys
    # and the name matches a known tool — then synthesize into tool_calls.
    if not _has_tool_calls and not _has_qwen_tags and _raw_text and tools:
        _known_tool_names = {t.get("name", "") for t in tools}
        _extracted_tcs: list = []
        # Strip optional markdown code fences
        _probe = re.sub(r"^```(?:json)?\s*|\s*```$", "", _raw_text.strip(), flags=re.DOTALL).strip()
        _json_candidates: list = []
        try:
            _parsed = json.loads(_probe)
            _json_candidates = [_parsed] if isinstance(_parsed, dict) else (_parsed if isinstance(_parsed, list) else [])
        except (json.JSONDecodeError, ValueError):
            # Try to find individual JSON objects (handles trailing text)
            for _match in re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', _probe):
                try:
                    _json_candidates.append(json.loads(_match.group()))
                except Exception:
                    pass
        for _cand in _json_candidates:
            if not isinstance(_cand, dict):
                continue
            _tc_name = _cand.get("name") or _cand.get("tool") or _cand.get("function")
            _tc_args = _cand.get("arguments") or _cand.get("parameters") or _cand.get("input") or {}
            if _tc_name and _tc_name in _known_tool_names:
                _extracted_tcs.append({
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "type": "function",
                    "function": {
                        "name": _tc_name,
                        "arguments": json.dumps(_tc_args) if isinstance(_tc_args, dict) else str(_tc_args),
                    },
                })
        if _extracted_tcs:
            msg["tool_calls"] = _extracted_tcs
            msg["content"] = None   # No text — tool call only
            _has_tool_calls = True
            _format_detected = "json_in_text"
            PROM_TOOL_CALL_SUCCESS.labels(node=effective_node, model=effective_model).inc()
            PROM_TOOL_FORMAT_ERRORS.labels(node=effective_node, model=effective_model, format="json_in_text").inc()
            _tool_eval_logger.warning(json.dumps({
                "ts": datetime.utcnow().isoformat() + "Z",
                "chat_id": chat_id, "model": effective_model, "node": effective_node,
                "phase": "tool_call", "event": "format_recovered", "format": "json_in_text",
                "tools": [t["function"]["name"] for t in _extracted_tcs],
                "snippet": _raw_text[:200],
            }))
            logger.info(f"🔧 JSON-in-text tool call detected and converted: {[t['function']['name'] for t in _extracted_tcs]}")

    if _has_qwen_tags:
        _format_detected = "qwen_raw"
        PROM_TOOL_FORMAT_ERRORS.labels(node=effective_node, model=effective_model, format="qwen_raw").inc()
        _tool_eval_logger.warning(json.dumps({
            "ts": datetime.utcnow().isoformat() + "Z",
            "chat_id": chat_id, "model": effective_model, "node": effective_node,
            "phase": "tool_call", "event": "format_error", "format": "qwen_raw",
            "snippet": _raw_text[:200],
        }))
        logger.warning(f"⚠️ Qwen raw format detected on {effective_node} — tools were not passed correctly!")
    elif _has_tool_calls and not (_format_detected == "json_in_text"):
        _format_detected = "json_tool_use"
        PROM_TOOL_CALL_SUCCESS.labels(node=effective_node, model=effective_model).inc()
    elif not _has_tool_calls:
        _format_detected = "text_only" if _raw_text else "empty"
        if not _raw_text:
            PROM_TOOL_FORMAT_ERRORS.labels(node=effective_node, model=effective_model, format="empty").inc()

    PROM_TOOL_CALL_DURATION.labels(node=effective_node, model=effective_model, phase="total").observe(time.monotonic() - _eval_t0)

    # OpenAI-Response → Anthropic Content-Blocks
    logger.info(
        "cc_tool: Ollama response — content_len=%d tool_calls=%d done=%s eval=%s",
        len(msg.get("content") or ""),
        len(msg.get("tool_calls") or []),
        oai_resp.get("done_reason", "?"),
        oai_resp.get("eval_count", "?"),
    )
    content_blocks: list = []
    if msg.get("content"):
        content_blocks.append({"type": "text", "text": msg["content"]})
    stop_reason = "end_turn"
    if msg.get("tool_calls"):
        stop_reason = "tool_use"
        for tc in msg["tool_calls"]:
            fn = tc.get("function", {})
            raw_args = fn.get("arguments", {})
            # Ollama native /api/chat returns arguments as a dict already.
            # OpenAI-compat endpoints return arguments as a JSON string.
            if isinstance(raw_args, dict):
                args = raw_args
            else:
                try:
                    args = json.loads(raw_args) if raw_args else {}
                except Exception:
                    args = {}
            content_blocks.append({
                "type":  "tool_use",
                "id":    tc["id"],
                "name":  fn.get("name", "unknown"),
                "input": args
            })
    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

    # Extract <think>...</think> from text content and surface as thinking blocks
    if session.stream_think:
        _think_blocks: list = []
        _remain_blocks: list = []
        for _blk in content_blocks:
            if _blk.get("type") == "text":
                _txt = _blk.get("text", "")
                _tm = re.search(r"<think>(.*?)</think>", _txt, re.S)
                if _tm:
                    _think_txt = _tm.group(1).strip()
                    _clean_txt = re.sub(r"<think>.*?</think>", "", _txt, flags=re.S).strip()
                    if _think_txt:
                        _think_blocks.append({"type": "thinking", "thinking": _think_txt})
                    if _clean_txt:
                        _remain_blocks.append({"type": "text", "text": _clean_txt})
                else:
                    _remain_blocks.append(_blk)
            else:
                _remain_blocks.append(_blk)
        content_blocks = _think_blocks + _remain_blocks

    # Tool-eval structured log
    _log_tool_eval({
        "ts":              datetime.utcnow().isoformat() + "Z",
        "chat_id":         chat_id,
        "model":           effective_model,
        "node":            effective_node,
        "input_type":      _last_msg_type,
        "tools_available": len(tools),
        "output_type":     stop_reason,
        "tools_called":    [b["name"] for b in content_blocks if b.get("type") == "tool_use"],
        "tool_call_count": sum(1 for b in content_blocks if b.get("type") == "tool_use"),
        "has_text":        any(b["type"] == "text" and b.get("text") for b in content_blocks),
        "latency_s":       round(time.monotonic() - _eval_t0, 3),
        "llm_latency_s":   round(_llm_latency, 3),
        "input_tokens":    in_tok,
        "output_tokens":   out_tok,
        "tool_choice_sent": _effective_tool_choice,
        "format_detected": _format_detected,
    })

    asyncio.create_task(_deregister_active_request(chat_id))
    asyncio.create_task(_record_stage(chat_id, "tool_model_call", "done"))

    # ── Tier-4 Episodic Write-Back (non-streaming path) ───────────────────────
    if (CC_CONTEXT_INDEX_ENABLED and session.long_memory
            and state.graph_manager is not None and _cc_ep_query
            and stop_reason == "end_turn"):
        try:
            from episodic_memory import log_episode as _log_ep
            _ep_text = next(
                (b.get("text", "") for b in content_blocks if b.get("type") == "text"), ""
            )
            asyncio.create_task(_log_ep(state.graph_manager.driver, {
                "input": _cc_ep_query,
                "user_id": user_id or "anon",
                "expert_models_used": [effective_model],
                "prompt_tokens": in_tok,
                "completion_tokens": out_tok,
                "plan": [{"category": "general"}],
                "final_response": _ep_text[:600],
            }))
        except Exception:
            pass

    # ── Augmented Tool Path: write-back (fire-and-forget, non-streaming path) ──
    # Opt-in (session.agent_ingest, default off). Only on a clean turn end:
    # end_turn stop reason, no tool_use emitted.
    if session.agent_ingest and _cc_ep_query and stop_reason == "end_turn":
        _agent_answer_text = next(
            (b.get("text", "") for b in content_blocks if b.get("type") == "text"), ""
        )
        if _agent_answer_text:
            asyncio.create_task(_agent_writeback_traced(
                chat_id,
                _cc_ep_query, _agent_answer_text, _agent_turn.scope,
                f"user:{user_id}" if user_id and user_id != "anon" else None,
                user_id, effective_model, session_id or "",
                state.redis_client, state.agent_cache_collection,
                path="anthropic",
            ))

    if not do_stream:
        return {
            "id": chat_id, "type": "message", "role": "assistant",
            "content": content_blocks, "model": model_id,
            "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": in_tok, "output_tokens": out_tok}
        }

    return StreamingResponse(
        _anthropic_content_blocks_to_sse(
            content_blocks, chat_id, model_id, in_tok, out_tok, stop_reason
        ),
        media_type="text/event-stream",
        headers=_moe_response_headers(effective_model, effective_node)
    )


async def _anthropic_reasoning_handler(
    body: dict,
    chat_id: str,
    session: CCSession,
    user_id: str = "anon",
    api_key_id: str = "",
    session_id: str | None = None,
):
    """Text requests via reasoning expert (deepseek-r1/qwq) with <think> parsing.

    Returns responses in Anthropic Extended Thinking format. A thinking block is
    included in the response only when session.stream_think is True.
    """
    model_id  = body.get("model", "claude-sonnet-4-6")
    messages  = body.get("messages", [])
    system    = body.get("system") or ""
    do_stream = body.get("stream", False)

    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    if not last_user:
        empty_resp = {
            "id": chat_id, "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": ""}],
            "model": model_id, "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}
        }
        if do_stream:
            async def _empty():
                async for chunk in _anthropic_content_blocks_to_sse(
                    [{"type": "text", "text": ""}], chat_id, model_id, 0, 0, "end_turn"
                ):
                    yield chunk
            return StreamingResponse(_empty(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})
        return empty_resp

    # Apply system_prompt_prefix from CC profile (same logic as _anthropic_tool_handler)
    if session.system_prefix and system:
        system = f"{session.system_prefix}\n\n{system}"
    elif session.system_prefix:
        system = session.system_prefix

    oai_messages = _anthropic_to_openai_messages(messages, system)
    oai_messages = await _compress_history_responses(
        oai_messages, state.redis_client, session_id,
        threshold=CC_HISTORY_COMPRESS_THRESHOLD,
        keep_turns=CC_HISTORY_COMPRESS_KEEP_TURNS,
    )

    # Determine model/node — explicit override takes precedence over dynamic selection
    _reasoning_node_name = "unknown"
    _reasoning_api_type  = "openai"
    if CLAUDE_CODE_REASONING_MODEL and _CLAUDE_CODE_REASONING_URL:
        reasoning_model = CLAUDE_CODE_REASONING_MODEL
        reasoning_url   = _CLAUDE_CODE_REASONING_URL
        reasoning_token = "ollama"
        _reasoning_node_name = CLAUDE_CODE_REASONING_ENDPOINT or "unknown"
        _reasoning_api_type  = API_TYPE_MAP.get(CLAUDE_CODE_REASONING_ENDPOINT, "openai")
        _reasoning_timeout = float(_server_info(_reasoning_node_name).get("timeout", EXPERT_TIMEOUT))
        logger.info(f"🧠 Reasoning: Override {reasoning_model} @ {CLAUDE_CODE_REASONING_ENDPOINT}")
    elif session.tool_model and session.tool_api_type == "ollama" and session.tool_url:
        # Reuse the already-loaded tool model for reasoning turns.
        # With OLLAMA_MAX_LOADED_MODELS=1, switching to a separate reasoning expert
        # evicts the tool model and causes ~120 s cold-reload cycles on every
        # tool↔reasoning turn boundary.
        reasoning_model      = session.tool_model
        reasoning_url        = session.tool_url
        reasoning_token      = session.tool_token or "ollama"
        _reasoning_node_name = session.tool_endpoint or "unknown"
        _reasoning_api_type  = session.tool_api_type
        _reasoning_timeout   = float(_server_info(_reasoning_node_name).get("timeout", EXPERT_TIMEOUT))
        logger.info("🧠 Reasoning: reusing loaded tool model %s @ %s (avoids eviction)", reasoning_model, _reasoning_node_name)
    else:
        reasoning_experts = EXPERTS.get("reasoning", [])
        if reasoning_experts:
            scored = []
            for e in reasoning_experts:
                score = await _get_expert_score(e["model"], "reasoning")
                scored.append((score, e))
            scored.sort(key=lambda x: -x[0])
            best = scored[0][1]
            reasoning_model = best["model"]
            node = await _select_node_svc(reasoning_model, best.get("endpoints") or [best.get("endpoint", "")])
            reasoning_url   = node.get("url") or URL_MAP.get(node["name"])
            reasoning_token = node.get("token", "ollama")
            _reasoning_node_name = node.get("name", "unknown")
            _reasoning_api_type  = node.get("api_type", "openai")
            _reasoning_timeout = float(node.get("timeout", EXPERT_TIMEOUT))
            logger.info(f"🧠 Reasoning: Dynamic {reasoning_model} @ {node.get('name','?')} (score={scored[0][0]:.2f})")
        else:
            reasoning_model = CLAUDE_CODE_TOOL_MODEL
            reasoning_url   = _CLAUDE_CODE_TOOL_URL
            reasoning_token = "ollama"
            _reasoning_node_name = CLAUDE_CODE_TOOL_ENDPOINT or "unknown"
            _reasoning_api_type  = API_TYPE_MAP.get(CLAUDE_CODE_TOOL_ENDPOINT, "openai")
            _reasoning_timeout = float(_server_info(_reasoning_node_name).get("timeout", EXPERT_TIMEOUT))
            logger.info(f"🧠 Reasoning: Fallback to tool model {reasoning_model}")

    _reasoning_max_tok = body.get("max_tokens", session.reasoning_max_tokens or REASONING_MAX_TOKENS)
    payload = {
        "model":      reasoning_model,
        "messages":   oai_messages,
        "stream":     False,
        "max_tokens": _reasoning_max_tok,
    }
    # For Ollama endpoints, per-request context sizing only works on the native
    # /api/chat path — /v1/chat/completions silently discards "options" (same
    # limitation documented in _anthropic_tool_handler). Without this, a
    # reasoning-mode CC profile whose model isn't already warm loads at
    # Ollama's Modelfile-default context instead of the profile's configured
    # window (observed: 32k instead of the intended 256k after the model had
    # been evicted and reloaded cold).
    _call_url = f"{reasoning_url}/chat/completions"
    if _reasoning_api_type == "ollama":
        _reasoning_num_ctx = (
            session.context_window
            or session.reasoning_max_tokens
            or session.tool_max_tokens
            or session.template_num_ctx
            or JUDGE_NUM_CTX
            or 32768
        )
        _reasoning_ollama_base = reasoning_url.rstrip("/").removesuffix("/v1")
        try:
            async with httpx.AsyncClient(timeout=2.0) as _ps_cl:
                _ps_r = await _ps_cl.get(
                    f"{_reasoning_ollama_base}/api/ps",
                    headers={"Authorization": f"Bearer {reasoning_token}"},
                )
                for _loaded in _ps_r.json().get("models", []):
                    _lname = _loaded.get("name", "").split(":")[0]
                    _ename = reasoning_model.split(":")[0]
                    _loaded_ctx = _loaded.get("context_length", 0)
                    if _lname == _ename and _loaded_ctx >= _reasoning_num_ctx:
                        _reasoning_num_ctx = _loaded_ctx
                        break
        except Exception:
            pass  # non-fatal — fall through to the configured num_ctx
        _call_url = f"{_reasoning_ollama_base}/api/chat"
        payload = {
            "model":    reasoning_model,
            "messages": oai_messages,
            "stream":   False,
            "options":  {"num_ctx": _reasoning_num_ctx, "num_predict": _reasoning_max_tok},
        }
        if not session.stream_think:
            payload["think"] = False
        logger.info(
            "cc_reasoning: Ollama native /api/chat — num_ctx=%d num_predict=%d model=%s",
            _reasoning_num_ctx, _reasoning_max_tok, reasoning_model,
        )
    try:
        async with httpx.AsyncClient(timeout=_reasoning_timeout) as client:
            resp = await client.post(
                _call_url,
                json=payload,
                headers={"Authorization": f"Bearer {reasoning_token}"}
            )
            resp.raise_for_status()
            oai_resp = resp.json()
    except (httpx.ReadTimeout, httpx.ConnectTimeout, asyncio.TimeoutError):
        asyncio.create_task(_deregister_active_request(chat_id))
        _err_text = f"⚠️ The reasoning server '{_reasoning_node_name}' did not respond in time."
        if do_stream:
            return StreamingResponse(
                _anthropic_content_blocks_to_sse(
                    [{"type": "text", "text": _err_text}], chat_id, model_id, 0, 0, "end_turn"
                ),
                media_type="text/event-stream",
                headers=_moe_response_headers(reasoning_model, _reasoning_node_name)
            )
        return {"id": chat_id, "type": "message", "role": "assistant",
                "content": [{"type": "text", "text": _err_text}], "model": model_id,
                "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}}
    except httpx.HTTPStatusError as _hex:
        asyncio.create_task(_deregister_active_request(chat_id))
        _err_text = f"⚠️ The reasoning server '{_reasoning_node_name}' returned HTTP {_hex.response.status_code}."
        if do_stream:
            return StreamingResponse(
                _anthropic_content_blocks_to_sse(
                    [{"type": "text", "text": _err_text}], chat_id, model_id, 0, 0, "end_turn"
                ),
                media_type="text/event-stream",
                headers=_moe_response_headers(reasoning_model, _reasoning_node_name)
            )
        return {"id": chat_id, "type": "message", "role": "assistant",
                "content": [{"type": "text", "text": _err_text}], "model": model_id,
                "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}}

    if _reasoning_api_type == "ollama":
        # Native /api/chat response shape differs from OpenAI's.
        raw     = oai_resp.get("message", {}).get("content", "") or ""
        in_tok  = oai_resp.get("prompt_eval_count", 0)
        out_tok = oai_resp.get("eval_count", 0)
    else:
        raw   = oai_resp["choices"][0]["message"].get("content", "") or ""
        usage = oai_resp.get("usage", {})
        in_tok  = usage.get("prompt_tokens", 0)
        out_tok = usage.get("completion_tokens", 0)

    if user_id != "anon":
        asyncio.create_task(_log_usage_to_db(
            user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
            model=reasoning_model, moe_mode="cc_reasoning",
            prompt_tokens=in_tok, completion_tokens=out_tok, session_id=session_id))
        asyncio.create_task(_increment_user_budget(user_id, in_tok + out_tok, prompt_tokens=in_tok, completion_tokens=out_tok))

    # Parse <think>...</think> blocks (deepseek-r1, qwq)
    think_match = re.search(r'<think>(.*?)</think>', raw, re.S)
    if think_match:
        thinking_text = think_match.group(1).strip()
        answer_text   = raw[think_match.end():].strip()
    else:
        thinking_text = ""
        answer_text   = raw.strip()

    content_blocks = []
    if thinking_text and session.stream_think:
        content_blocks.append({"type": "thinking", "thinking": thinking_text})
    content_blocks.append({"type": "text", "text": answer_text or ""})

    asyncio.create_task(_deregister_active_request(chat_id))
    if not do_stream:
        return {
            "id": chat_id, "type": "message", "role": "assistant",
            "content": content_blocks, "model": model_id,
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": in_tok, "output_tokens": out_tok}
        }

    return StreamingResponse(
        _anthropic_content_blocks_to_sse(content_blocks, chat_id, model_id, in_tok, out_tok, "end_turn"),
        media_type="text/event-stream",
        headers=_moe_response_headers(reasoning_model, _reasoning_node_name)
    )


async def _anthropic_moe_handler(
    body: dict,
    chat_id: str,
    session: CCSession,
    user_id: str = "anon",
    api_key_id: str = "",
    session_id: str | None = None,
    local_only: bool = False,
):
    """Route pure text requests through the MoE agent pipeline."""
    from services.deliberation.runtime import summarize_deliberation_telemetry

    request_started = time.monotonic()
    try:
        client_max_output_tokens = max(0, int(body.get("max_tokens") or 0))
    except (TypeError, ValueError):
        client_max_output_tokens = 0
    model_id   = body.get("model", "moe-orchestrator-agent")
    messages   = body.get("messages", [])
    system     = body.get("system") or ""
    do_stream  = body.get("stream", False)

    # Last user message as pipeline input
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    if not last_user:
        empty_resp = {
            "id": chat_id, "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": ""}],
            "model": model_id, "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}
        }
        if do_stream:
            async def _empty():
                async for chunk in _anthropic_content_blocks_to_sse(
                    [{"type": "text", "text": ""}], chat_id, model_id, 0, 0, "end_turn"
                ):
                    yield chunk
            return StreamingResponse(_empty(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})
        return empty_resp

    last_user_content = last_user.get("content", "")
    allowed_skills = session.user_perms.get("skill")
    _raw_cc_input = _anthropic_content_to_text(last_user_content)
    user_input  = await _resolve_skill_secure(_raw_cc_input, allowed_skills, user_id=user_id, session_id=session_id)
    _cc_pending_reports: List[str] = []
    if user_input != _raw_cc_input:
        _csm = re.match(r"^/([a-zA-Z0-9][a-zA-Z0-9\-]*)", _raw_cc_input)
        _csname = _csm.group(1) if _csm else "?"
        _csargs = _raw_cc_input[len(_csname)+1:].strip() if _csm else ""
        _cc_pending_reports.append(
            f"🎯 Skill /{_csname} resolved (args: '{_csargs}', {len(user_input)} chars):\n{user_input}"
        )
    user_images = _extract_images(last_user_content)
    history_raw = [
        {"role": m["role"],
         "content": _anthropic_content_to_text(m.get("content", ""))}
        for m in messages[:-1]
        if m.get("role") in ("user", "assistant")
    ]
    history = _truncate_history(history_raw)
    _sm_team_ids_h: List[str] = []
    _sm_prefs_h:   dict      = {}
    _sm_enable        = session.planner_cfg.get("enable_semantic_memory", False)
    _sm_cross_session = session.planner_cfg.get("enable_cross_session_memory", False)
    _sm_scopes        = session.planner_cfg.get("cross_session_scopes", ["private"])
    if _sm_enable and user_id and user_id != "anon":
        try:
            from admin_ui.database import (
                get_user_teams        as _gut_h,
                get_user_memory_prefs as _gmp_h,
            )
            if _sm_cross_session:
                _sm_team_ids_h = await _gut_h(user_id)
            _sm_prefs_h = await _gmp_h(user_id)
        except Exception:
            pass
    history = await _apply_semantic_memory(
        history_raw, history, user_input, session_id,
        enabled=_sm_enable,
        user_id=user_id,
        team_ids=_sm_team_ids_h,
        cross_session_enabled=_sm_cross_session,
        cross_session_scopes=_sm_scopes,
        n_results=0,   # anthropic handler: use template default (passed via caller)
        ttl_hours=0,
        prefer_fresh=_sm_prefs_h.get("prefer_fresh", False),
        share_with_team=_sm_prefs_h.get("share_with_team", False),
    )

    # ── Tier-3 Context Index: chunk large system_prompt before dispatching to MoE ─
    if CC_CONTEXT_INDEX_ENABLED and session_id and system and state.redis_client:
        try:
            from services.context_index import ensure_indexed as _ensure_ctx_moe
            await _ensure_ctx_moe(session_id, system, state.redis_client)
        except Exception as _cie:
            logger.debug("moe_handler: context indexing skipped: %s", _cie)

    _pcfg = session.planner_cfg
    invoke_state = {
        "input": user_input, "response_id": chat_id,
        "mode": "agent_orchestrated" if session.mode == "moe_orchestrated" else "agent",
        "expert_models_used": [], "prompt_tokens": 0, "completion_tokens": 0,
        "user_conn_prompt_tokens": 0, "user_conn_completion_tokens": 0,
        "chat_history": history, "reasoning_trace": "", "system_prompt": system,
        "session_id": session_id or "",
        "behavioral_directives": session.system_prefix,
        "images": user_images,
        "user_permissions": session.user_perms,
        "user_experts": session.experts,
        "planner_prompt": _pcfg.get("planner_prompt", ""),
        "judge_prompt":   _pcfg.get("judge_prompt", ""),
        "judge_model_override":   _pcfg.get("judge_model_override", ""),
        "judge_url_override":     _pcfg.get("judge_url_override", ""),
        "judge_token_override":   _pcfg.get("judge_token_override", ""),
        "planner_model_override": _pcfg.get("planner_model_override", ""),
        "planner_url_override":   _pcfg.get("planner_url_override", ""),
        "planner_token_override": _pcfg.get("planner_token_override", ""),
        "planner_num_ctx":        _pcfg.get("planner_num_ctx", 0),
        "judge_num_ctx":          _pcfg.get("judge_num_ctx", 0),
        "deliberation_policy":     _pcfg.get("deliberation_policy", {}),
        "deliberation_capacity":   {},
        "deliberation_events":     [],
        "local_only_routing":      local_only,
        "request_deadline_monotonic": request_started + ORCHESTRATION_TIMEOUT,
        "client_max_output_tokens": client_max_output_tokens,
        "guardrail_prompt":            _pcfg.get("guardrail_prompt", ""),
        "guardrail_model_override":    _pcfg.get("guardrail_model_override", ""),
        "guardrail_url_override":      _pcfg.get("guardrail_url_override", ""),
        "guardrail_token_override":    _pcfg.get("guardrail_token_override", ""),
        "guardrail_num_ctx":           _pcfg.get("guardrail_num_ctx", 0),
        "template_name":          body.get("model", "") or "",
        "pending_reports": _cc_pending_reports,
        "agentic_iteration": 0,
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
        "quality_gate_status": "",
        "response_commit_context": {},
        "response_commit_status": "not_started",
        "response_commit_key": "",
        "response_commit_sinks": {},
        "response_commit_errors": [],
    }
    invoke_cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}

    if do_stream:
        async def _moe_stream():
            _did_deregister = False
            try:
                # Set up progress queue so _report() calls don't hang
                progress_q: asyncio.Queue = asyncio.Queue()
                ctx_token = _progress_queue.set(progress_q)
                result_box: dict = {}

                async def _run():
                    try:
                        if state.app_graph is None:
                            result_box["error"] = "Orchestrator graph not ready — retry in a few seconds"
                            return
                        result_box["data"] = await state.app_graph.ainvoke(invoke_state, invoke_cfg)
                    except Exception as e:
                        result_box["error"] = str(e)
                    finally:
                        await progress_q.put(None)

                _pipeline_task = asyncio.create_task(_run())

                # Immediately send message_start + content_block_start
                # → client sees response start, keeps connection open
                yield _sse_event("message_start", {
                    "type": "message_start",
                    "message": {
                        "id": chat_id, "type": "message", "role": "assistant",
                        "content": [], "model": model_id,
                        "stop_reason": None,
                        "usage": {"input_tokens": 0, "output_tokens": 1}
                    }
                })
                # When stream_think: open a thinking block first, stream progress into it,
                # then close it and open the text block at the next index.
                _text_block_index = 0
                if session.stream_think:
                    _text_block_index = 1
                    yield _sse_event("content_block_start", {
                        "type": "content_block_start", "index": 0,
                        "content_block": {"type": "thinking", "thinking": ""}
                    })
                    yield _sse_event("ping", {"type": "ping"})

                    while True:
                        try:
                            msg = await asyncio.wait_for(progress_q.get(), timeout=20.0)
                            if msg is None:
                                break
                            if msg:
                                yield _sse_event("content_block_delta", {
                                    "type": "content_block_delta", "index": 0,
                                    "delta": {"type": "thinking_delta", "thinking": f"{msg}\n"}
                                })
                        except asyncio.TimeoutError:
                            yield _sse_event("ping", {"type": "ping"})

                    yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": 0})
                else:
                    # Drain progress queue without emitting (pipeline still uses it internally)
                    while True:
                        try:
                            msg = await asyncio.wait_for(progress_q.get(), timeout=20.0)
                            if msg is None:
                                break
                        except asyncio.TimeoutError:
                            yield _sse_event("ping", {"type": "ping"})

                yield _sse_event("content_block_start", {
                    "type": "content_block_start", "index": _text_block_index,
                    "content_block": {"type": "text", "text": ""}
                })
                if not session.stream_think:
                    yield _sse_event("ping", {"type": "ping"})

                _progress_queue.reset(ctx_token)

                # Stream result
                data    = result_box.get("data", {})
                content = data.get("final_response", "") if "data" in result_box \
                          else f"Error: {result_box.get('error', 'Unknown')}"
                in_tok  = data.get("prompt_tokens", 0)
                out_tok = data.get("completion_tokens", 0)

                if data.get("quality_blocked"):
                    yield _sse_event(
                        "content_block_stop",
                        {"type": "content_block_stop", "index": _text_block_index},
                    )
                    yield _sse_event("error", {
                        "type": "error",
                        "error": {
                            "type": "quality_blocked",
                            "message": "The response was withheld by the quality gate.",
                            "code": data.get(
                                "quality_block_reason", "quality_blocked"
                            ),
                            "request_id": chat_id,
                        },
                    })
                    return

                # Online quality probe (sampled): pipeline vs. single best expert.
                if "data" in result_box:
                    try:
                        from services.quality_probe import run_probe as _qp_run
                        asyncio.create_task(_qp_run(
                            query=user_input, pipeline_answer=content,
                            experts=session.experts, planner_cfg=session.planner_cfg,
                            request_id=chat_id, user_id=user_id,
                            pipeline_tokens=out_tok,
                            graph_context=data.get("graph_context", ""),
                            web_research=data.get("web_research", ""),
                            mcp_result=data.get("mcp_result", ""),
                        ))
                    except Exception:
                        pass

                if user_id != "anon":
                    asyncio.create_task(_log_usage_to_db(
                        user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
                        model="moe-orchestrator", moe_mode="cc_moe",
                        prompt_tokens=in_tok, completion_tokens=out_tok, session_id=session_id))
                    _uc_p = data.get("user_conn_prompt_tokens", 0)
                    _uc_c = data.get("user_conn_completion_tokens", 0)
                    _bill_p = max(0, in_tok - _uc_p)
                    _bill_c = max(0, out_tok - _uc_c)
                    asyncio.create_task(_increment_user_budget(user_id, _bill_p + _bill_c, prompt_tokens=_bill_p, completion_tokens=_bill_c))

                for i in range(0, max(len(content), 1), SSE_CHUNK_SIZE):
                    yield _sse_event("content_block_delta", {
                        "type": "content_block_delta", "index": _text_block_index,
                        "delta": {"type": "text_delta", "text": content[i:i+SSE_CHUNK_SIZE]}
                    })
                    await asyncio.sleep(0.005)

                yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": _text_block_index})
                yield _sse_event("message_delta", {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": out_tok}
                })
                yield _sse_event("message_stop", {"type": "message_stop"})
                asyncio.create_task(_deregister_active_request(
                    chat_id,
                    summarize_deliberation_telemetry(
                        data.get("deliberation_capacity"),
                        data.get("deliberation_events"),
                    ),
                ))
                _did_deregister = True
            finally:
                # Cancel the pipeline task if the client disconnected before it finished.
                # Without this, LangGraph keeps running expert/judge calls on the GPU
                # even after Open-WebUI (or any SSE client) closes the connection.
                if not _pipeline_task.done():
                    _pipeline_task.cancel()
                    logger.info("moe_stream: pipeline task cancelled (client disconnected, chat_id=%s)", chat_id)
                if not _did_deregister:
                    asyncio.create_task(_deregister_active_request(chat_id))

        return StreamingResponse(_moe_stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})

    # Non-Streaming
    if state.app_graph is None:
        return JSONResponse(status_code=503, content={"type": "error", "error": {"type": "api_error", "message": "Orchestrator graph not ready — retry in a few seconds"}})
    result  = await state.app_graph.ainvoke(invoke_state, invoke_cfg)
    content = result.get("final_response", "")
    _p_tok = result.get("prompt_tokens", 0)
    _c_tok = result.get("completion_tokens", 0)

    if result.get("quality_blocked"):
        asyncio.create_task(_deregister_active_request(chat_id))
        return JSONResponse(
            status_code=422,
            headers={"X-MoE-Quality": "blocked"},
            content={"error": {
                "type": "quality_blocked",
                "message": "The response was withheld by the quality gate.",
                "code": result.get("quality_block_reason", "quality_blocked"),
                "request_id": chat_id,
            }},
        )

    # Online quality probe (sampled): pipeline vs. single best expert.
    try:
        from services.quality_probe import run_probe as _qp_run
        asyncio.create_task(_qp_run(
            query=user_input, pipeline_answer=content,
            experts=session.experts, planner_cfg=session.planner_cfg,
            request_id=chat_id, user_id=user_id,
            pipeline_tokens=_c_tok,
            graph_context=result.get("graph_context", ""),
            web_research=result.get("web_research", ""),
            mcp_result=result.get("mcp_result", ""),
        ))
    except Exception:
        pass

    if user_id != "anon":
        asyncio.create_task(_log_usage_to_db(
            user_id=user_id, api_key_id=api_key_id, request_id=chat_id,
            model="moe-orchestrator", moe_mode="cc_moe",
            prompt_tokens=_p_tok, completion_tokens=_c_tok, session_id=session_id))
        _uc_p = result.get("user_conn_prompt_tokens", 0)
        _uc_c = result.get("user_conn_completion_tokens", 0)
        _bill_p = max(0, _p_tok - _uc_p)
        _bill_c = max(0, _c_tok - _uc_c)
        asyncio.create_task(_increment_user_budget(user_id, _bill_p + _bill_c, prompt_tokens=_bill_p, completion_tokens=_bill_c))
    asyncio.create_task(_deregister_active_request(
        chat_id,
        summarize_deliberation_telemetry(
            result.get("deliberation_capacity"),
            result.get("deliberation_events"),
        ),
    ))
    return {
        "id": chat_id, "type": "message", "role": "assistant",
        "content": [{"type": "text", "text": content}],
        "model": model_id, "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {
            "input_tokens":  _p_tok,
            "output_tokens": _c_tok
        }
    }


async def anthropic_messages(request: Request):
    """Anthropic Messages API — drop-in compatible with Claude Code CLI and Anthropic SDK.

    Routing:
    - Claude Code sessions (claude-* model ID):
        - tool_use / tool_result  → devstral:24b (code specialist, robust function calling)
        - pure text requests      → MoE agent pipeline (mode=agent)
    - Standard MoE sessions:
        - tools / tool_results    → judge_llm (magistral:24b)
        - pure text requests      → MoE agent pipeline

    Configuration for Claude Code:
        ANTHROPIC_BASE_URL=http://<server>:8002
        ANTHROPIC_API_KEY=<any>        # not validated
        CLAUDE_MODEL=claude-sonnet-4-6  (or other claude-* ID)
    """
    body       = await request.json()
    chat_id    = f"msg_{uuid.uuid4().hex[:24]}"
    session_id = _extract_session_id(request)
    messages   = body.get("messages", [])
    tools      = body.get("tools", [])
    model     = body.get("model", "")
    _ol_run_id = None  # lineage emitted by moe-codex when deployed

    # Auth
    raw_key  = _extract_api_key(request)
    user_ctx = await _validate_api_key(raw_key) if raw_key else {"error": "invalid_key"}
    if "error" in user_ctx:
        if user_ctx["error"] == "budget_exceeded":
            return JSONResponse(status_code=429, content={"error": {
                "message": f"Budget exceeded ({user_ctx.get('limit_type', 'unknown')} limit reached)",
                "type": "insufficient_quota", "code": "budget_exceeded"
            }})
        return JSONResponse(status_code=401, content={"error": {
            "message": "Invalid or missing API key", "type": "invalid_request_error", "code": "invalid_api_key"
        }})
    _user_id    = user_ctx.get("user_id", "anon")
    _api_key_id = user_ctx.get("key_id", "")

    # ─── Resolve per-request CC session (profile, endpoint, template) ─────────
    _profile_ids = json.loads(user_ctx.get("permissions_json", "") or "{}").get("cc_profile", [])
    try:
        session = _resolve_cc_session(
            user_ctx,
            _profile_ids,
            requested_model=model,
        )
    except DeliberationPolicyError as exc:
        return JSONResponse(status_code=422, content={"error": {
            "message": str(exc),
            "type": "invalid_request_error",
            "code": "deliberation_policy_invalid",
        }})

    # Sovereignty guard: local_only keys must never reach non-local endpoints.
    from services.sovereignty import assert_egress_allowed, resolve_local_only, EgressDenied
    _user_perms_for_sovereignty = json.loads(user_ctx.get("permissions_json", "") or "{}")
    _local_only = resolve_local_only(_user_perms_for_sovereignty, user_ctx)
    try:
        if session.tool_url:
            assert_egress_allowed(session.tool_url, _local_only)
    except EgressDenied as _ed:
        return JSONResponse(status_code=403, content={"error": {
            "message": str(_ed), "type": "permission_error", "code": "local_only_violation",
        }})

    if session.profile_not_found:
        logger.warning(
            "CC profile not found for key=%s profiles=%s — returning 422 to suppress retries",
            _api_key_id, _profile_ids,
        )
        return JSONResponse(status_code=422, content={"error": {
            "message": (
                "No active Claude Code profile matched this API key. "
                "Please check your profile configuration in the admin panel."
            ),
            "type": "invalid_request_error",
            "code": "cc_profile_not_found",
        }})

    has_tool_results = any(
        isinstance(m.get("content"), list)
        and any(b.get("type") == "tool_result" for b in m.get("content", []))
        for m in messages
    )

    # Live monitoring
    from services.cc_fastpath import (
        is_fastpath_request as _is_fp,
        requires_precision_pipeline as _requires_precision_pipeline,
    )
    _force_precision_pipeline = _requires_precision_pipeline(body)

    _cc_moe_mode = (
        "cc_moe"       if _force_precision_pipeline else
        "cc_tool"      if (tools or has_tool_results or session.mode == "native") else
        "cc_reasoning" if session.mode == "moe_reasoning" else
        "cc_moe"
    )
    _cc_backend_model = session.tool_model if _cc_moe_mode != "cc_moe" else "MoE"
    # Await registration so every subsequent error/return path can reliably
    # remove the exact entry; fire-and-forget registration races used to
    # recreate keys after a fast failure had already deregistered them
    # (see services/pipeline/chat.py for the same fix applied earlier).
    await _register_active_request(
        chat_id=chat_id, user_id=_user_id, model=model,
        moe_mode=_cc_moe_mode, req_type="streaming" if body.get("stream") else "batch",
        client_ip=request.client.host if request.client else "",
        backend_model=_cc_backend_model,
        api_key_id=_api_key_id,
        # Previously omitted entirely — the admin "Laufende API-Anfragen" table's
        # "Template" column was always blank for CC sessions even when the
        # profile's expert_template_id was actively driving tool_model
        # auto-derivation (see cc_session.py Phase 5.5).
        template_name=session.expert_template_id,
        resolved_tmpl_id=session.expert_template_id,
    )

    # Fast-path triage: CC utility calls and trivially short prompts bypass the
    # MoE pipeline entirely — a 5-line topic-detection request must never run
    # planner+experts+judge for minutes (observed: 5 min pipeline for one
    # CC-internal side request). Flag: CC_FASTPATH=1.
    _fp_reason = _is_fp(body)
    if _fp_reason:
        logger.info("cc_dispatch: fast-path (%s) — bypassing MoE pipeline", _fp_reason)
    if _force_precision_pipeline:
        logger.info(
            "cc_dispatch: mandatory precision contract — forcing evidence-bound pipeline"
        )

    try:
        # Mandatory deterministic plain-text turns take precedence over the
        # profile's native/reasoning mode so every API facade enforces the same
        # typed evidence and final-response binding contract. Real client tool
        # turns are excluded by requires_precision_pipeline().
        if _force_precision_pipeline:
            _result = await _anthropic_moe_handler(
                body, chat_id, session, _user_id, _api_key_id, session_id,
                local_only=_local_only,
            )
        # Mode 1: Native or tool/tool_result turns → tool handler (precise function calling)
        elif session.mode == "native" or tools or has_tool_results or _fp_reason:
            _result = await _anthropic_tool_handler(
                body, chat_id, session, _user_id, _api_key_id, session_id,
            )
        # Mode 2: MoE Reasoning — reasoning expert with <think> parsing and thinking blocks
        elif session.mode == "moe_reasoning":
            _result = await _anthropic_reasoning_handler(
                body, chat_id, session, _user_id, _api_key_id, session_id,
            )
        # Mode 3 + fallback: MoE Orchestrated — full planner, all experts
        else:
            _result = await _anthropic_moe_handler(
                body, chat_id, session, _user_id, _api_key_id, session_id,
                local_only=_local_only,
            )
        return _result
    except Exception as _exc:
        logger.error("Messages-Endpoint unhandled exception (chat_id=%s): %s", chat_id, _exc, exc_info=True)
        asyncio.create_task(_deregister_active_request(chat_id))
        return JSONResponse(status_code=500, content={"error": {
            "type": "api_error", "message": f"Internal server error: {_exc}"
        }})


# _ONTOLOGY_RUN_KEY and _ONTOLOGY_RUNS_HISTORY_KEY moved to services/healer.py
