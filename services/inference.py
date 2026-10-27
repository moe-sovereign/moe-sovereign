"""services/inference.py — Node selection, expert scoring, LLM fallback chain."""

import asyncio
import re
import threading
import time
import random
import os
import logging
from dataclasses import dataclass

import httpx
from langchain_openai import ChatOpenAI

import state
from config import (
    URL_MAP, TOKEN_MAP, API_TYPE_MAP, INFERENCE_SERVERS_LIST,
    EXPERT_MIN_DATAPOINTS,
    JUDGE_TIMEOUT, PLANNER_TIMEOUT, EXPERT_TIMEOUT,
    JUDGE_MODEL, JUDGE_URL, JUDGE_TOKEN,
    _FALLBACK_NODE, _FALLBACK_MODEL, _FALLBACK_MODEL_SECOND,
    _FALLBACK_ENABLED, _ENDPOINT_DEGRADED_TTL, _EXTERNAL_ENDPOINT_PATTERNS,
    MAX_EXPERT_OUTPUT_CHARS, MAX_JUDGE_TOKENS, THOMPSON_SAMPLING_ENABLED,
    JUDGE_NUM_CTX, PLANNER_NUM_CTX,
    MAX_PLANNER_TOKENS, PLANNER_THINKING_ENABLED,
    JUDGE_THINKING_ENABLED,
    ROUTING_PATTERN_PRIOR_ENABLED, ROUTING_PATTERN_PRIOR_K, ROUTING_PATTERN_PRIOR_CAP,
)
from context_budget import get_model_context_window as _static_ctx
from context_budget import adaptive_context_window, resolve_requested_ctx
from metrics import PROM_THOMPSON
from services import routing_patterns
from services.routing import _server_info, _is_endpoint_error
from services.tracking import _get_node_latency_stats, _get_premature_stop_rate
from typing import Dict, List, Optional, Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI  # noqa: F811 — type hints only

# The judge previously used a separate Ollama tag ("sovereign-judge:27b") whose
# only functional difference from the "qwen3.8:27b" expert weights was this
# system prompt plus a few sampling params (all already set explicitly per
# request below). Ollama tracks loaded-model identity by tag name, not by the
# underlying weight blob -- two tags sharing byte-identical weights still
# forced a full unload+reload every time a request alternated between an
# expert call and a judge call on the same node. Both tags now resolve to
# "qwen3.8:27b"; this prompt is injected explicitly so judge behavior is
# unchanged, and Ollama can keep the already-resident model warm across the
# expert<->judge switch. See agent_status/claude-code.md,
# FIX-judge-expert-shared-model-reload.
JUDGE_SYSTEM_PROMPT = (
    "You are Sovereign Judge 27B (Qwen3.8-27B fine-tuned), the primary "
    "evaluation and synthesis authority in the MoE Sovereign compound AI "
    "platform. Evaluate input quality, factual consistency, code invariants, "
    "and safety with maximum precision across up to 258,000 context tokens."
)


def _judge_messages(prompt) -> list[dict]:
    """Build the judge's message list with its identity system prompt inlined."""
    if isinstance(prompt, list):
        if prompt and prompt[0].get("role") == "system":
            return prompt
        return [{"role": "system", "content": JUDGE_SYSTEM_PROMPT}, *prompt]
    return [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]


from services.llm_instances import judge_llm, planner_llm
from services.model_capabilities import (
    apply_ollama_structured_capability,
    enforce_streaming_capability,
    get_model_caps,
    openai_response_format,
)
from services.deadline import (
    RequestDeadlineExceeded,
    bounded_output_tokens,
    remaining_timeout,
    sleep_with_budget,
    wait_for_budget,
)
from services.rate_limiter import throttle as _throttle_endpoint

_DEFAULT_OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "24h")


def _ollama_answer_content(response: dict) -> str:
    """Return public answer content, never Ollama's private thinking trace."""
    if not isinstance(response, dict):
        return ""
    message = response.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content", "")
    return content if isinstance(content, str) else str(content or "")

logger = logging.getLogger("MOE-SOVEREIGN")

# ── AI I/O Audit helper (TASK-29) ─────────────────────────────────────────────
# Imported lazily so that init-time failures (e.g. DB not yet up) don't crash
# the entire inference module.

def _audit_create(session_id: str, request_id: str, model: str, endpoint: str,
                  stage: str, request_body: dict):
    """Best-effort: create an AI I/O audit entry. Never raises."""
    try:
        from services.ai_io_audit import create_audit_entry
        return create_audit_entry(session_id, request_id, model, endpoint, stage, request_body)
    except Exception:
        return None


async def _audit_complete(entry, response_body, prompt_tokens, completion_tokens, status="completed"):
    """Best-effort: complete an AI I/O audit entry. Never raises."""
    if entry is None:
        return
    try:
        from services.ai_io_audit import complete_audit_entry
        await complete_audit_entry(entry.audit_id, response_body, prompt_tokens,
                                   completion_tokens, status)
    except Exception:
        pass


async def _audit_cancel(entry) -> None:
    """Close an audit entry even when the surrounding invocation is cancelled."""
    try:
        await asyncio.shield(
            _audit_complete(
                entry,
                {"error": "cancelled"},
                None,
                None,
                "error",
            )
        )
    except asyncio.CancelledError:
        # A second cancellation may interrupt the shield await; the shielded
        # completion task continues independently and removes the live entry.
        pass


def _audit_usage(response) -> tuple[Optional[int], Optional[int]]:
    """Extract token counts from LangChain or native-provider response shapes."""
    usage = getattr(response, "usage_metadata", None) or {}
    if not usage:
        metadata = getattr(response, "response_metadata", None) or {}
        usage = metadata.get("token_usage") or metadata.get("usage") or {}
    prompt_tokens = (
        usage.get("input_tokens")
        if isinstance(usage, dict) else None
    )
    completion_tokens = (
        usage.get("output_tokens")
        if isinstance(usage, dict) else None
    )
    if isinstance(usage, dict):
        prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
        completion_tokens = usage.get("completion_tokens", completion_tokens)
    return prompt_tokens, completion_tokens


async def _audited_ainvoke(
    llm,
    prompt,
    *,
    endpoint: str,
    model: str,
    stage: str,
    context: Optional[dict] = None,
):
    """Invoke a LangChain-compatible model with a complete audit lifecycle."""
    context = context or {}
    caps = get_model_caps(model)
    logger.debug("model=%s caps=%s stage=%s", model, caps, stage)
    entry = _audit_create(
        context.get("session_id", ""),
        context.get("response_id", ""),
        model,
        endpoint,
        stage,
        {"prompt": prompt},
    )
    try:
        await _throttle_endpoint(endpoint, context, stage=f"{stage}_rate_limit")
        response = await llm.ainvoke(prompt)
        prompt_tokens, completion_tokens = _audit_usage(response)
        await _audit_complete(
            entry,
            {"content": getattr(response, "content", "")},
            prompt_tokens,
            completion_tokens,
        )
        return response
    except asyncio.CancelledError:
        await _audit_cancel(entry)
        raise
    except Exception as exc:
        await _audit_complete(
            entry, {"error": str(exc)}, None, None, "error"
        )
        raise

# Module-level threading locks
# synchronous dict mutation (e.g. _endpoint_gpu_indices[k] = v) is NOT atomic
# under concurrent asyncio tasks on CPython once the GIL is released between
# byte-code instructions. threading.Lock is the correct primitive here because:
#   (a) all writers run in the same event-loop thread — so Lock.acquire() never
#       blocks the event loop longer than the locked section itself (a few ns);
#   (b) asyncio.Lock would require async with, which is heavier and unnecessary
#       for pure-synchronous dict updates.

_cache_lock = threading.Lock()
_gpu_lock   = threading.Lock()

# Local in-process GGUF LLM instance for local planner/SLM mode
_local_llama_instance = None
_local_llama_lock = asyncio.Lock()


async def _get_local_llama():
    """Return the cached in-process Llama instance (loaded lazily)."""
    global _local_llama_instance
    from config import PLANNER_LOCAL_GGUF_PATH, PLANNER_LOCAL_THREADS
    if not PLANNER_LOCAL_GGUF_PATH or not os.path.exists(PLANNER_LOCAL_GGUF_PATH):
        return None
    if _local_llama_instance is None:
        async with _local_llama_lock:
            if _local_llama_instance is None:
                try:
                    from llama_cpp import Llama
                    logger.info("Initializing in-process GGUF Llama model from %s", PLANNER_LOCAL_GGUF_PATH)
                    _local_llama_instance = Llama(
                        model_path=PLANNER_LOCAL_GGUF_PATH,
                        n_ctx=4096,
                        n_threads=PLANNER_LOCAL_THREADS,
                        verbose=False
                    )
                except ImportError:
                    logger.warning("llama-cpp-python not installed; in-process GGUF planner unavailable.")
                except Exception as e:
                    logger.error("Failed to load in-process GGUF Llama model: %s", e)
    return _local_llama_instance

# ---------------------------------------------------------------------------
# Model availability cache
# ---------------------------------------------------------------------------

_model_avail_cache: Dict[str, tuple] = {}  # {node: (monotonic_ts, frozenset[model_names])}
_MODEL_AVAIL_TTL = 60.0  # seconds


async def _get_available_models(node: str) -> Optional[frozenset]:
    """Queries available models of a node (60s cache).
    Returns None if the node is unreachable → request is not blocked."""
    now = time.monotonic()
    with _cache_lock:
        if node in _model_avail_cache:
            ts, models = _model_avail_cache[node]
            if now - ts < _MODEL_AVAIL_TTL:
                return models
    url = URL_MAP.get(node, "")
    if not url:
        return None
    base_url = url.rstrip("/").removesuffix("/v1")
    token = TOKEN_MAP.get(node, "ollama")
    api_type = API_TYPE_MAP.get(node, "ollama")
    try:
        async with httpx.AsyncClient(timeout=5) as _c:
            if api_type == "ollama":
                _r = await _c.get(f"{base_url}/api/tags",
                                  headers={"Authorization": f"Bearer {token}"})
                models = frozenset(m["name"] for m in _r.json().get("models", [])) \
                         if _r.status_code == 200 else None
            else:  # openai-compatible
                _r = await _c.get(f"{base_url}/v1/models",
                                  headers={"Authorization": f"Bearer {token}"})
                models = frozenset(m["id"] for m in _r.json().get("data", [])) \
                         if _r.status_code == 200 else None
        if models is not None:
            with _cache_lock:
                _model_avail_cache[node] = (now, models)
        return models
    except Exception as _e:
        logger.debug(f"Model availability check failed for {node}: {_e}")
        return None


# ---------------------------------------------------------------------------
# Per-endpoint semaphores and GPU index assignment
# ---------------------------------------------------------------------------

_endpoint_semaphores: Dict[str, asyncio.Semaphore] = {}
_endpoint_gpu_indices: Dict[str, int] = {}

async def _init_semaphores():
    """Create per-endpoint semaphores in the event-loop context, derived from INFERENCE_SERVERS_LIST."""
    global _endpoint_semaphores, _endpoint_gpu_indices
    for s in INFERENCE_SERVERS_LIST:
        name  = s["name"]
        count = int(s.get("gpu_count", 1))
        _endpoint_semaphores[name]  = asyncio.Semaphore(count)
        _endpoint_gpu_indices[name] = 0
    logger.info(f"🎮 GPU semaphores: { {s['name']: s.get('gpu_count', 1) for s in INFERENCE_SERVERS_LIST} }")

async def assign_gpu(endpoint: str = "") -> int:
    srv   = next((s for s in INFERENCE_SERVERS_LIST if s["name"] == endpoint), None)
    count = int(srv["gpu_count"]) if srv else 1
    with _gpu_lock:
        idx = _endpoint_gpu_indices.get(endpoint, 0) % max(count, 1)
        _endpoint_gpu_indices[endpoint] = idx + 1
    return idx


# ---------------------------------------------------------------------------
# Performance key and VRAM unload
# ---------------------------------------------------------------------------

def _perf_key(model: str, category: str) -> str:
    """Valkey key for expert performance: moe:perf:{model}:{category}"""
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", model)
    return f"moe:perf:{safe}:{category}"

async def _ollama_unload(model: str, base_url: str) -> None:
    """Unloads a model immediately from Ollama VRAM via native API (keep_alive=0).
    Fire-and-forget — errors are ignored, pipeline continues."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            await client.post(
                f"{base_url}/api/generate",
                json={"model": model, "keep_alive": 0, "prompt": "", "stream": False},
            )
        logger.debug(f"🗑️ VRAM: {model} unloaded")
    except Exception as e:
        logger.debug(f"⚠️ VRAM-Unload {model}: {e}")


_model_arch_cache: dict[str, dict] = {}


async def _query_model_arch(ollama_base: str, model_name: str) -> dict:
    """Return Ollama /api/show model_info for model_name, cached per (node, model).

    Returns an empty dict on failure so callers can handle gracefully.
    """
    cache_key = f"{ollama_base}|{model_name}"
    if cache_key in _model_arch_cache:
        return _model_arch_cache[cache_key]
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{ollama_base}/api/show", json={"name": model_name})
            r.raise_for_status()
            info = r.json().get("model_info", {})
    except Exception as e:
        logger.debug("⚠️ VRAM arch query: /api/show failed for %s: %s", model_name, e)
        return {}
    _model_arch_cache[cache_key] = info
    return info


def _kv_cache_gb_from_arch(arch: dict, ctx: int) -> float:
    """Compute KV cache size in GB from Ollama model_info architecture fields.

    Ollama exposes per-architecture keys such as llama4.block_count,
    llama.attention.head_count_kv, etc. Searches by suffix so it works across
    model families (llama, llama4, qwen2, mistral, …). Assumes q8_0 KV quantization
    (1 byte per element) as the conservative upper bound.
    Returns 0.0 when required fields are missing.
    """
    def _find(suffix: str) -> int:
        for key, value in arch.items():
            if not key.endswith(suffix) or value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return 0

    block_count = _find(".block_count")
    kv_heads    = _find(".attention.head_count_kv")
    key_len     = _find(".attention.key_length")
    val_len     = _find(".attention.value_length") or key_len

    if not all([block_count, kv_heads, key_len]):
        return 0.0

    kv_bytes = block_count * kv_heads * (key_len + val_len) * ctx  # q8_0: 1 byte/element
    return kv_bytes / 1e9


async def _evict_competing_models(
    ollama_base: str, keep_model: str, ctx: int = 0
) -> None:
    """Evict competing models from an Ollama node only when necessary to fit keep_model.

    All inputs are derived dynamically from Ollama's APIs:
    - /api/ps  → actual size_vram of every currently loaded model
    - /api/show → model architecture (block_count, kv_heads, key_length) for KV cache math
    - INFERENCE_SERVERS config → node total VRAM

    Keeps competing models warm when everything fits. Evicts the minimum set (largest
    first) when it doesn't. Falls back to evicting all competitors only when the target
    model's VRAM cannot be determined (neither name-parse nor /api/show succeed).
    """
    node_vram_gb = _node_vram_by_url(f"{ollama_base}/v1")
    if node_vram_gb <= 0:
        logger.debug("⚠️ VRAM pre-evict: node VRAM unknown for %s — skipping", ollama_base)
        return

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{ollama_base}/api/ps")
            r.raise_for_status()
            loaded = r.json().get("models", [])
    except Exception as e:
        logger.debug("⚠️ VRAM pre-evict: /api/ps query failed: %s", e)
        return

    target_entry = next((m for m in loaded if m.get("name", "") == keep_model), None)
    competing = [m for m in loaded if m.get("name", "") != keep_model]

    # Fast path: target already warm at the exact requested context
    if target_entry is not None and ctx > 0 and target_entry.get("context_length", 0) == ctx:
        logger.debug("✅ VRAM pre-evict: %s already warm at ctx=%d — no eviction needed", keep_model, ctx)
        return

    if not competing:
        return

    # Estimate target VRAM from actual /api/ps entry (if already loaded) or /api/show arch
    if target_entry is not None:
        # Model is loaded at a different ctx — use its actual size as the weight baseline
        weights_gb = target_entry.get("size_vram", 0) / 1e9
        # Adjust for KV cache difference between current and requested ctx
        current_ctx = target_entry.get("context_length", 0)
        if current_ctx > 0 and ctx > 0:
            arch = await _query_model_arch(ollama_base, keep_model)
            kv_current = _kv_cache_gb_from_arch(arch, current_ctx)
            kv_target  = _kv_cache_gb_from_arch(arch, ctx)
            target_gb = max(0.0, weights_gb - kv_current + kv_target)
        else:
            target_gb = weights_gb
    else:
        # Model not loaded — estimate from name (weights) + /api/show arch (KV cache)
        weights_gb = _estimate_model_vram_gb(keep_model)
        arch = await _query_model_arch(ollama_base, keep_model)
        kv_gb = _kv_cache_gb_from_arch(arch, ctx) if ctx > 0 else 0.0
        target_gb = (weights_gb + kv_gb) if weights_gb > 0 else 0.0

    competing_vram_gb = sum(m.get("size_vram", 0) / 1e9 for m in competing)

    if target_gb > 0:
        if competing_vram_gb + target_gb <= node_vram_gb:
            logger.info(
                "✅ VRAM pre-evict: %.1f GB (competing) + %.1f GB (target %s @ ctx=%d) ≤ %.0f GB — keeping warm",
                competing_vram_gb, target_gb, keep_model, ctx, node_vram_gb,
            )
            return
        needed_gb = (competing_vram_gb + target_gb) - node_vram_gb
        logger.info(
            "🗑️ VRAM pre-evict: need %.1f GB more for %s (est. %.1f GB) on %.0f GB node",
            needed_gb, keep_model, target_gb, node_vram_gb,
        )
        for m in sorted(competing, key=lambda x: x.get("size_vram", 0), reverse=True):
            name = m.get("name", "")
            if not name:
                continue
            freed_gb = m.get("size_vram", 0) / 1e9
            logger.info("🗑️ VRAM pre-evict: unloading %s (%.1f GB)", name, freed_gb)
            await _ollama_unload(name, ollama_base)
            needed_gb -= freed_gb
            if needed_gb <= 0:
                break
    else:
        # Conservative fallback: /api/show failed AND name has no parameter count
        logger.info(
            "⚠️ VRAM pre-evict: no VRAM estimate for %s — evicting all competing models on node",
            keep_model,
        )
        for m in competing:
            name = m.get("name", "")
            if name:
                logger.info("🗑️ VRAM pre-evict: unloading %s (%.1f GB)", name, m.get("size_vram", 0) / 1e9)
                await _ollama_unload(name, ollama_base)


# ---------------------------------------------------------------------------
# Endpoint degradation tracking
# ---------------------------------------------------------------------------

_degraded_endpoints: dict[str, float] = {}  # url → monotonic timestamp of degraded mark


def _mark_endpoint_degraded(url: str) -> None:
    _degraded_endpoints[url] = time.monotonic()
    logger.warning("⚠️ Endpoint marked degraded (5 min): %s", url)


def _endpoint_is_degraded(url: str) -> bool:
    ts = _degraded_endpoints.get(url)
    if ts is None:
        return False
    if time.monotonic() - ts > _ENDPOINT_DEGRADED_TTL:
        _degraded_endpoints.pop(url, None)
        return False
    return True


async def _get_fallback_llm(timeout: float = 120.0, model: str = "") -> "ChatOpenAI":
    """Return a ChatOpenAI pointing to the configured local fallback node.

    model: override which fallback model to use. Defaults to FALLBACK_MODEL.
           Raises RuntimeError when fallback is not configured (FALLBACK_NODE empty).
    """
    if not _FALLBACK_ENABLED:
        raise RuntimeError(
            "No local fallback configured. Set FALLBACK_NODE and "
            "FALLBACK_MODEL environment variables to enable."
        )
    url = URL_MAP.get(_FALLBACK_NODE)
    if not url:
        raise RuntimeError(
            f"Fallback node '{_FALLBACK_NODE}' is not in the configured "
            "inference servers (INFERENCE_SERVERS env var)."
        )
    token = TOKEN_MAP.get(_FALLBACK_NODE, "ollama")
    return ChatOpenAI(
        model=model or _FALLBACK_MODEL,
        base_url=url,
        api_key=token,
        timeout=timeout,
    )


def _is_external_endpoint_url(url: str) -> bool:
    """Return True when the URL points to an external (non-local) inference endpoint.

    An endpoint counts as external when its admin-configured api_type is not
    "ollama" (e.g. a paid OpenAI-compatible gateway like AIHUB), or when it
    matches an EXTERNAL_ENDPOINT_PATTERNS entry.
    """
    if _url_api_type(url) != "ollama":
        return True
    u = url.lower()
    return any(p and p in u for p in _EXTERNAL_ENDPOINT_PATTERNS)


# ---------------------------------------------------------------------------
# LLM invocation with fallback chain
# ---------------------------------------------------------------------------

async def _invoke_llm_with_fallback(
    primary_llm: "ChatOpenAI",
    primary_url: str,
    prompt,
    timeout: float = 120.0,
    label: str = "LLM",
    audit_context: Optional[dict] = None,
    audit_stage: str = "",
    model: str = "",
) -> tuple:
    """Invoke primary_llm; on auth/quota error or empty response, retry with fallback node.

    Handles two failure modes:
    1. Exception (401, 429, connection error) — caught in except block.
    2. Silent empty body (HTTP 200 with no content) — detected after ainvoke returns.

    Returns (result, used_fallback: bool).
    """
    from config import _ENDPOINT_RETRY_COUNT, _ENDPOINT_RETRY_DELAY

    _on_external = _is_external_endpoint_url(primary_url)

    async def _try_fallback_node(reason: str, model: str = "") -> tuple:
        """Try the configured fallback node model. Returns (res, True) on success."""
        _mark_endpoint_degraded(primary_url)
        _fb_model = model or _FALLBACK_MODEL
        logger.warning("🔄 %s: %s — falling back to %s@%s",
                       label, reason, _fb_model, _FALLBACK_NODE)
        _fallback_timeout = remaining_timeout(
            audit_context,
            timeout,
            stage=f"{label}_fallback",
        )
        fb_llm = await _get_fallback_llm(_fallback_timeout, model=_fb_model)
        fb_res = await wait_for_budget(
            _audited_ainvoke(
                fb_llm,
                prompt,
                endpoint=URL_MAP.get(_FALLBACK_NODE, _FALLBACK_NODE),
                model=_fb_model,
                stage=audit_stage or label.lower(),
                context=audit_context,
            ),
            audit_context,
            _fallback_timeout,
            stage=f"{label}_fallback",
        )
        return fb_res, True

    async def _try_fallback_chain(reason: str) -> tuple:
        """Try primary fallback model, then second-tier fallback model.

        Does nothing (re-raises) when fallback is not configured via env vars.
        """
        if not _FALLBACK_ENABLED:
            logger.warning("⚠️ %s: %s — no local fallback configured (FALLBACK_NODE/FALLBACK_MODEL not set)",
                           label, reason)
            raise RuntimeError(f"{label} failed and no fallback configured: {reason}")

        try:
            res, used = await _try_fallback_node(reason, _FALLBACK_MODEL)
            logger.info("✅ %s: Fallback (%s@%s) succeeded", label, _FALLBACK_MODEL, _FALLBACK_NODE)
            return res, used
        except Exception as fe1:
            if _FALLBACK_MODEL_SECOND:
                logger.warning("⚠️ %s: Primary fallback (%s) failed: %s — trying %s",
                               label, _FALLBACK_MODEL, str(fe1)[:60], _FALLBACK_MODEL_SECOND)
                try:
                    res2, _ = await _try_fallback_node(reason + " (2nd fallback)", _FALLBACK_MODEL_SECOND)
                    logger.info("✅ %s: Second fallback (%s) succeeded", label, _FALLBACK_MODEL_SECOND)
                    return res2, True
                except Exception as fe2:
                    logger.error("❌ %s: Both fallbacks failed. Last: %s", label, fe2)
                    raise fe2
            logger.error("❌ %s: Fallback (%s) failed, no second fallback configured: %s",
                         label, _FALLBACK_MODEL, fe1)
            raise fe1

    # ── Primary call: retry loop for external endpoints before declaring degraded ───
    if _endpoint_is_degraded(primary_url):
        return await _try_fallback_chain(f"endpoint {primary_url} is in degraded state")

    _last_exc: Exception | None = None
    for _attempt in range(_ENDPOINT_RETRY_COUNT if _on_external else 1):
        try:
            _primary_model = model
            if not _primary_model:
                for attr in ("model_name", "model"):
                    value = getattr(primary_llm, attr, "")
                    if isinstance(value, str) and value:
                        _primary_model = value
                        break
            res = await wait_for_budget(
                _audited_ainvoke(
                    primary_llm,
                    prompt,
                    endpoint=primary_url,
                    model=_primary_model or "unknown",
                    stage=audit_stage or label.lower(),
                    context=audit_context,
                ),
                audit_context,
                timeout,
                stage=label,
            )
            # Silent failure: some external endpoints return HTTP 200 with empty body
            if _on_external and (not res or not getattr(res, "content", None) or not res.content.strip()):
                _last_exc = RuntimeError("Empty response")
                if _attempt < _ENDPOINT_RETRY_COUNT - 1:
                    logger.debug("⏳ %s: Empty response, retry %d/%d in %.0fs",
                                 label, _attempt + 1, _ENDPOINT_RETRY_COUNT, _ENDPOINT_RETRY_DELAY)
                    await sleep_with_budget(
                        _ENDPOINT_RETRY_DELAY,
                        audit_context,
                        stage=f"{label}_retry",
                    )
                    continue
                # Exhausted retries → fallback
                return await _try_fallback_chain("Primary endpoint returned empty response after retries")
            return res, False
        except RequestDeadlineExceeded:
            raise
        except Exception as e:
            _last_exc = e
            if _is_endpoint_error(e) or (_on_external and "empty" in str(e).lower()):
                # 429 rate-limit: honour the upstream retry_after hint instead of
                # the global ENDPOINT_RETRY_DELAY. Also skip degraded-marking so the
                # endpoint is not blacklisted for subsequent requests.
                _e_str = str(e)
                _is_rate_limit = "429" in _e_str or "rate limit" in _e_str.lower() or "rate-limited" in _e_str.lower()
                if _is_rate_limit:
                    import re as _re
                    _m = _re.search(r'retry_after_seconds[\'\":\s]+(\d+(?:\.\d+)?)', _e_str)
                    _wait = float(_m.group(1)) + 1.0 if _m else 30.0
                    logger.info("⏳ %s: 429 rate-limit — waiting %.0fs (retry_after) then retrying", label, _wait)
                    await sleep_with_budget(
                        _wait,
                        audit_context,
                        stage=f"{label}_rate_limit",
                    )
                    # Do NOT mark endpoint as degraded — it's temporarily rate-limited, not broken.
                    if _attempt < _ENDPOINT_RETRY_COUNT - 1:
                        continue
                    return await _try_fallback_chain(f"429 rate-limit persisted after {_ENDPOINT_RETRY_COUNT} retries")
                if _attempt < _ENDPOINT_RETRY_COUNT - 1:
                    logger.debug("⏳ %s: External endpoint error, retry %d/%d in %.0fs: %s",
                                 label, _attempt + 1, _ENDPOINT_RETRY_COUNT, _ENDPOINT_RETRY_DELAY, str(e)[:60])
                    await sleep_with_budget(
                        _ENDPOINT_RETRY_DELAY,
                        audit_context,
                        stage=f"{label}_retry",
                    )
                    continue
                # Exhausted retries → fallback
                return await _try_fallback_chain(f"External endpoint error after {_ENDPOINT_RETRY_COUNT} retries: {str(e)[:60]}")
            raise  # non-retriable error — propagate immediately

    # Should not reach here but handle defensively
    if _on_external and _last_exc:
        return await _try_fallback_chain(f"Primary endpoint exhausted: {str(_last_exc)[:60]}")
    raise _last_exc


def _url_api_type(url: str) -> str:
    """Return api_type for a base URL by reverse-matching INFERENCE_SERVERS_LIST.
    Strips the trailing /v1 segment for comparison so both URL forms match.
    Defaults to 'ollama' — all internal nodes are Ollama unless explicitly configured."""
    if not url:
        return "ollama"
    base = url.rstrip("/").removesuffix("/v1")
    for s in INFERENCE_SERVERS_LIST:
        s_base = (s.get("url") or "").rstrip("/").removesuffix("/v1")
        if s_base and s_base == base:
            return s.get("api_type", "ollama")
    return "ollama"


async def _invoke_judge_with_retry(
    state: "AgentState", prompt: str, max_retries: int = 3, temperature: float | None = None,
    repeat_penalty: float | None = None, repeat_last_n: int | None = None,
):
    """Invoke the judge LLM with retry logic for empty/failed responses.
    On failure: waits 5s (model reload time), re-discovers the node, retries.
    When the primary endpoint returns 401/429, immediately falls back to the configured
    fallback node without burning retry budget on unavailable endpoints.

    For Ollama endpoints uses native /api/chat so options.num_ctx is respected.
    The OpenAI-compat /v1/chat/completions endpoint silently drops the options dict
    (Ollama ≤0.30.6), causing the model to reload at ctx=8192 on every judge call.

    temperature: when set, overrides the default judge sampling temperature.
    repeat_penalty/repeat_last_n: when set, passed through as Ollama sampling
    options to discourage degenerate repetition loops (observed live: a
    merger synthesis call fell into repeating "// I will output the SPSC
    code." dozens of times instead of emitting real code, cutting the
    response off mid code-fence). Left unset (Ollama defaults) for callers
    that don't pass them, to avoid changing behavior for stages that never
    exhibited this failure mode.
    """
    from types import SimpleNamespace as _NS
    last_error = None
    for attempt in range(max_retries):
        try:
            # Resolve judge endpoint for this attempt (cache cleared between retries)
            _jm = (state.get("judge_model_override") or "").strip() or (JUDGE_MODEL or "")
            _ju = (state.get("judge_url_override")   or "").strip() or (JUDGE_URL or "")
            _jt = (state.get("judge_token_override") or "").strip() or (JUDGE_TOKEN or "ollama")
            # Floating mode: model set but URL empty → discover best node
            if (state.get("judge_model_override") or "").strip() and not (state.get("judge_url_override") or "").strip():
                _all_eps = [s["name"] for s in INFERENCE_SERVERS_LIST]
                _node = await _select_node(_jm, _all_eps, user_id=state.get("user_id", ""))
                _ju  = _node.get("url") or URL_MAP.get(_node["name"], "")
                _jt  = _node.get("token", "ollama")
                logger.info("🌐 Floating judge: %s → %s", _jm, _node["name"])
            _j_url_base = (_ju or "").rstrip("/")
            _j_api_type = _url_api_type(_j_url_base)

            # Sovereignty guard: covers both the native-Ollama branch below and
            # the ChatOpenAI/_invoke_llm_with_fallback branch further down —
            # both dispatch to this same resolved _j_url_base. Applies equally
            # to the regular judge stage and the deliberation moderator (which
            # calls this same function).
            from services.sovereignty import assert_egress_allowed, EgressDenied
            assert_egress_allowed(_j_url_base, bool(state.get("local_only_routing")))

            if _j_api_type == "ollama" and _jm and _j_url_base:
                # Native Ollama /api/chat — respects options.num_ctx unlike /v1/chat/completions.
                _ollama_base = _j_url_base.removesuffix("/v1")
                _ctx = int(state.get("judge_num_ctx") or 0) or JUDGE_NUM_CTX or _static_ctx(_jm)
                _judge_output_limit = bounded_output_tokens(
                    state,
                    MAX_JUDGE_TOKENS,
                )
                _ctx = adaptive_context_window(
                    _ctx,
                    prompt,
                    _judge_output_limit,
                )
                # Never downgrade a warm model: if Ollama already has this model loaded
                # with a larger context window, reuse that window instead of forcing a
                # reload — llama-server can't resize a running instance's context, so a
                # smaller request for the SAME model triggers a full unload+reload cycle.
                # Confirmed live: qwen3.6:35b on N04-RTX reloading every 5-40 minutes all
                # day, alternating between an Augmented Tool Path session's large template
                # context and this judge call's smaller JUDGE_NUM_CTX default — the exact
                # same-model downgrade this check exists to prevent already protects the
                # Augmented Tool Path (services/pipeline/anthropic.py,
                # services/pipeline/chat.py's _retry_tool_agent_fallback) but was never
                # applied to the judge/planner path that competes with it on the same node.
                try:
                    async with httpx.AsyncClient(timeout=2.0) as _ps_cl:
                        _ps_r = await _ps_cl.get(
                            f"{_ollama_base}/api/ps",
                            headers={"Authorization": f"Bearer {_jt}"},
                        )
                        for _loaded in _ps_r.json().get("models", []):
                            _lname = _loaded.get("name", "").split(":")[0]
                            _ename = _jm.split(":")[0]
                            _loaded_ctx = _loaded.get("context_length", 0)
                            if _lname == _ename and _loaded_ctx >= _ctx:
                                logger.info(
                                    "judge: reusing warm model ctx=%d (requested %d, no reload needed, model=%s)",
                                    _loaded_ctx, _ctx, _jm,
                                )
                                _ctx = _loaded_ctx
                                break
                except Exception:
                    pass  # non-fatal — fall through to the configured num_ctx
                _opts: dict = {}
                if _ctx > 0:
                    _opts["num_ctx"] = _ctx
                if MAX_JUDGE_TOKENS > 0:
                    _opts["num_predict"] = _judge_output_limit
                if temperature is not None:
                    _opts["temperature"] = temperature
                if repeat_penalty is not None:
                    _opts["repeat_penalty"] = repeat_penalty
                if repeat_last_n is not None:
                    _opts["repeat_last_n"] = repeat_last_n
                _payload: dict = {
                    "model":      _jm,
                    "messages":   _judge_messages(prompt),
                    "stream":     False,
                    "think":      JUDGE_THINKING_ENABLED,
                    "keep_alive": _DEFAULT_OLLAMA_KEEP_ALIVE,
                }
                _payload["stream"] = enforce_streaming_capability(
                    _jm, bool(_payload["stream"])
                )
                if _opts:
                    _payload["options"] = _opts
                logger.debug("model=%s caps=%s", _jm, get_model_caps(_jm))
                _audit_entry = _audit_create(
                    state.get("session_id", ""), state.get("response_id", ""),
                    _jm, f"{_ollama_base}/api/chat", "judge", _payload,
                )
                _resp_json: dict = {}
                try:
                    _judge_timeout = remaining_timeout(
                        state,
                        JUDGE_TIMEOUT,
                        stage="judge",
                    )
                    async with httpx.AsyncClient(timeout=_judge_timeout) as _hc:
                        _r = await _hc.post(
                            f"{_ollama_base}/api/chat",
                            json=_payload,
                            headers={"Authorization": f"Bearer {_jt}"},
                        )
                    _r.raise_for_status()
                    _resp_json = _r.json()
                    await _audit_complete(
                        _audit_entry,
                        _resp_json,
                        _resp_json.get("prompt_eval_count"),
                        _resp_json.get("eval_count"),
                    )
                except asyncio.CancelledError:
                    await _audit_cancel(_audit_entry)
                    raise
                except Exception as _ae:
                    await _audit_complete(_audit_entry, {"error": str(_ae)}, None, None, "error")
                    raise
                res = _NS(
                    content=_ollama_answer_content(_resp_json),
                    usage_metadata={
                        "input_tokens": int(
                            _resp_json.get("prompt_eval_count", 0)
                        ),
                        "output_tokens": int(
                            _resp_json.get("eval_count", 0)
                        ),
                    },
                )
            else:
                # Non-Ollama path (AIHUB, cloud providers): use LangChain ChatOpenAI
                llm = await _get_judge_llm(state)
                llm = llm.bind(
                    max_tokens=bounded_output_tokens(
                        state,
                        MAX_JUDGE_TOKENS,
                    )
                )
                if temperature is not None:
                    llm = llm.bind(temperature=temperature)
                res, _ = await _invoke_llm_with_fallback(
                    llm, _j_url_base, prompt, timeout=JUDGE_TIMEOUT,
                    label="Judge", audit_context=state,
                    audit_stage="judge", model=_jm,
                )

            if res and hasattr(res, 'content') and res.content and len(res.content.strip()) > 0:
                if attempt > 0:
                    logger.info(f"✅ Judge retry {attempt+1}/{max_retries} succeeded")
                return res
            logger.warning(f"⚠️ Judge returned empty/short response (attempt {attempt+1}/{max_retries})")
            last_error = "Empty response"
        except RequestDeadlineExceeded:
            raise
        except EgressDenied:
            # Deterministic configuration violation, not a transient failure —
            # a fixed (non-floating) judge endpoint would fail identically on
            # every retry. Fail fast instead of burning the 5s/10s/15s backoff.
            raise
        except Exception as e:
            logger.warning(f"⚠️ Judge invoke failed (attempt {attempt+1}/{max_retries}): {e}")
            last_error = str(e)

        if attempt < max_retries - 1:
            wait = 5 * (attempt + 1)  # 5s, 10s, 15s
            logger.info(f"🔄 Judge retry in {wait}s (warming up model)...")
            await sleep_with_budget(wait, state, stage="judge_retry")
            # Clear PS cache to force fresh node discovery
            with _cache_lock:
                _ps_cache.clear()

    # All retries failed — return a minimal response
    logger.error(f"❌ Judge failed after {max_retries} attempts: {last_error}")
    from types import SimpleNamespace
    return SimpleNamespace(content=f"[Judge unavailable after {max_retries} retries: {last_error}]")


async def ainvoke_judge_llm(prompt):
    """ainvoke()-compatible call to the global judge LLM for background tasks
    (self-rating, GraphRAG entity extraction, Open WebUI internal requests) that
    have no AgentState/per-template overrides.

    `prompt` may be a plain string (wrapped as a single user message) or a list of
    `{"role", "content"}` message dicts (used as-is — e.g. Open WebUI's lc_messages).

    For Ollama endpoints, posts to native /api/chat with options.num_ctx=JUDGE_NUM_CTX
    — the same fix as _invoke_judge_with_retry. /v1/chat/completions silently drops
    `options` (Ollama <=0.30.7), which previously caused these background calls to run
    at the model's Modelfile-default ctx (e.g. 8192 for qwen3.6:35b instead of 98304),
    additionally forcing a VRAM reload between this call and the native judge path.

    The returned object exposes `.content` and `.usage_metadata` (input_tokens/
    output_tokens from Ollama's prompt_eval_count/eval_count), matching the shape
    `_extract_usage()` expects.

    Non-Ollama endpoints fall back to judge_llm (ChatOpenAI) unchanged. Raises on
    failure, like judge_llm.ainvoke() — callers already handle exceptions.
    """
    from types import SimpleNamespace
    _j_url_base = (JUDGE_URL or "").rstrip("/")
    if _url_api_type(_j_url_base) == "ollama" and JUDGE_MODEL and _j_url_base:
        _ollama_base = _j_url_base.removesuffix("/v1")
        _ctx = JUDGE_NUM_CTX or _static_ctx(JUDGE_MODEL)
        _opts: dict = {}
        if _ctx > 0:
            _opts["num_ctx"] = _ctx
        if MAX_JUDGE_TOKENS > 0:
            _opts["num_predict"] = MAX_JUDGE_TOKENS
        _messages = _judge_messages(prompt)
        _payload: dict = {
            "model":      JUDGE_MODEL,
            "messages":   _messages,
            "stream":     False,
            "think":      JUDGE_THINKING_ENABLED,
            "keep_alive": _DEFAULT_OLLAMA_KEEP_ALIVE,
        }
        _payload["stream"] = enforce_streaming_capability(
            JUDGE_MODEL, bool(_payload["stream"])
        )
        if _opts:
            _payload["options"] = _opts
        _audit_entry = _audit_create(
            "", "", JUDGE_MODEL, f"{_ollama_base}/api/chat",
            "judge_background", _payload,
        )
        try:
            async with httpx.AsyncClient(timeout=JUDGE_TIMEOUT) as _hc:
                _r = await _hc.post(
                    f"{_ollama_base}/api/chat", json=_payload,
                    headers={"Authorization": f"Bearer {JUDGE_TOKEN}"},
                )
            _r.raise_for_status()
            _data = _r.json()
            await _audit_complete(
                _audit_entry, _data,
                _data.get("prompt_eval_count"), _data.get("eval_count"),
            )
        except asyncio.CancelledError:
            await _audit_cancel(_audit_entry)
            raise
        except Exception as exc:
            await _audit_complete(
                _audit_entry, {"error": str(exc)}, None, None, "error"
            )
            raise
        return SimpleNamespace(
            content=_ollama_answer_content(_data),
            usage_metadata={
                "input_tokens":  int(_data.get("prompt_eval_count", 0)),
                "output_tokens": int(_data.get("eval_count", 0)),
            },
        )
    return await _audited_ainvoke(
        judge_llm,
        prompt,
        endpoint=JUDGE_URL,
        model=JUDGE_MODEL,
        stage="judge_background",
    )


async def ainvoke_guard_llm(
    user_input: str,
    guard_model: str = "",
    guard_url: str = "",
    guard_token: str = "",
    policy_context: str = "",
    session_id: str = "",
    request_id: str = "",
) -> tuple[bool, str]:
    """Compatibility wrapper returning ``(is_unsafe, category)``.

    The graph uses :func:`ainvoke_guard_decision` directly so it can distinguish
    a real safe verdict from an operational fail-open.
    """
    decision = await ainvoke_guard_decision(
        user_input,
        guard_model=guard_model,
        guard_url=guard_url,
        guard_token=guard_token,
        policy_context=policy_context,
        session_id=session_id,
        request_id=request_id,
    )
    return decision.is_unsafe, decision.category


@dataclass(frozen=True)
class GuardDecision:
    """Result of a guard invocation, including its operational state."""

    is_unsafe: bool
    category: str = ""
    status: str = "safe"


def _ollama_model_is_loaded(payload: dict, requested_model: str) -> bool:
    """Return whether Ollama reports the exact requested model as resident."""
    requested = requested_model.strip()
    requested_latest = requested if ":" in requested else f"{requested}:latest"
    for item in payload.get("models") or []:
        loaded = str(item.get("name") or item.get("model") or "").strip()
        loaded_latest = loaded if ":" in loaded else f"{loaded}:latest"
        if loaded == requested or loaded_latest == requested_latest:
            return True
    return False


async def ainvoke_guard_decision(
    user_input: str,
    guard_model: str = "",
    guard_url: str = "",
    guard_token: str = "",
    policy_context: str = "",
    session_id: str = "",
    request_id: str = "",
    deadline_state: Optional[dict] = None,
) -> GuardDecision:
    """Classify input and expose safe/unsafe/fail-open as distinct outcomes.

    Fail-open by design: any error (timeout, unreachable endpoint, unconfigured
    guard, unexpected model output) proceeds normally. With
    ``GUARD_WARM_ONLY=true`` a short ``/api/ps`` probe prevents a cold guard
    model from evicting or starving planner/expert workloads on shared Ollama
    endpoints. These operational fallbacks are audited and returned as explicit
    ``fail_open_*`` statuses rather than being mistaken for safe classifications.

    Uses Llama Guard's own built-in Ollama chat template (Meta's fixed
    MLCommons-hazard-taxonomy classification format, e.g. output "safe" or
    "unsafe\\nS9") — no custom SYSTEM prompt override, since Llama Guard 3 expects
    its trained format, not an arbitrary role-play system prompt like the other
    MoE Sovereign experts. `policy_context` (from a template's guardrail_prompt,
    if set) is prepended as plain context ahead of the user's message instead.
    """
    from config import (
        GUARD_URL,
        GUARD_MODEL,
        GUARD_TOKEN,
        GUARD_TIMEOUT,
        GUARD_WARM_ONLY,
        GUARD_PROBE_TIMEOUT,
        GUARD_KEEP_ALIVE,
        GUARD_NUM_CTX,
    )

    _gm = guard_model or GUARD_MODEL
    _gu = (guard_url or GUARD_URL or "").rstrip("/")
    _gt = guard_token or GUARD_TOKEN
    if not _gm or not _gu or _url_api_type(_gu) != "ollama":
        return GuardDecision(False, status="disabled")

    _ollama_base = _gu.removesuffix("/v1")
    _content = f"{policy_context}\n\n{user_input}" if policy_context else user_input
    _payload: dict = {
        "model":      _gm,
        "messages":   [{"role": "user", "content": _content}],
        "stream":     False,
        "keep_alive": GUARD_KEEP_ALIVE,
        "options":    {"num_ctx": GUARD_NUM_CTX},
    }
    _audit_entry = _audit_create(
        session_id,
        request_id,
        _gm,
        f"{_ollama_base}/api/chat",
        "guard",
        _payload,
    )
    try:
        async with httpx.AsyncClient() as _hc:
            if GUARD_WARM_ONLY:
                _probe_timeout = remaining_timeout(
                    deadline_state,
                    GUARD_PROBE_TIMEOUT,
                    stage="guard_probe",
                )
                _ps = await _hc.get(
                    f"{_ollama_base}/api/ps",
                    headers={"Authorization": f"Bearer {_gt}"},
                    timeout=_probe_timeout,
                )
                _ps.raise_for_status()
                if not _ollama_model_is_loaded(_ps.json(), _gm):
                    await _audit_complete(
                        _audit_entry,
                        {
                            "error": "guard_model_not_warm",
                            "fail_open": True,
                        },
                        None,
                        None,
                        "error",
                    )
                    logger.info(
                        "🛡️ Guard model '%s' is not resident — failing open "
                        "without a cold model load",
                        _gm,
                    )
                    return GuardDecision(False, status="fail_open_not_warm")
            _guard_timeout = remaining_timeout(
                deadline_state,
                GUARD_TIMEOUT,
                stage="guard",
            )
            _r = await _hc.post(
                f"{_ollama_base}/api/chat", json=_payload,
                headers={"Authorization": f"Bearer {_gt}"},
                timeout=_guard_timeout,
            )
        _r.raise_for_status()
        _data = _r.json()
        await _audit_complete(
            _audit_entry,
            _data,
            _data.get("prompt_eval_count"),
            _data.get("eval_count"),
        )
        _result = (_data.get("message", {}).get("content", "") or "").strip()
    except asyncio.CancelledError:
        await _audit_cancel(_audit_entry)
        raise
    except RequestDeadlineExceeded:
        await _audit_cancel(_audit_entry)
        raise
    except Exception as e:
        await _audit_complete(
            _audit_entry,
            {"error": str(e)},
            None,
            None,
            "error",
        )
        logger.warning(
            "⚠️ Guard: classification call failed [%s] (%s) — failing open",
            type(e).__name__, str(e)[:120],
        )
        return GuardDecision(False, status="fail_open_error")

    if _result.lower().startswith("unsafe"):
        _lines = _result.splitlines()
        _category = _lines[1].strip() if len(_lines) > 1 else "unspecified"
        return GuardDecision(True, _category, "unsafe")
    return GuardDecision(False, status="safe")


async def prewarm_guard_model() -> None:
    """One-time cold load of the guard model at process startup.

    GUARD_WARM_ONLY (default true) makes ainvoke_guard_decision fail open
    whenever the guard model is not already resident, specifically to avoid
    paying for a cold 8B load out of the request's own latency budget. But
    with GUARD_WARM_ONLY on, nothing ever triggers that first cold load
    either -- observed live, every single request failed open
    ("fail_open_not_warm") because the guard model was never resident even
    once. This mirrors _prewarm_bge_model's pattern (main.py): pay the cold
    load cost once, out-of-band, at startup, so real traffic afterwards
    always finds it warm and refreshes GUARD_KEEP_ALIVE via normal use.
    Fire-and-forget: any failure here must never block startup or crash the
    process -- the guard already fails open by design when unavailable.
    """
    from config import GUARD_URL, GUARD_MODEL, GUARD_TOKEN, GUARD_KEEP_ALIVE, GUARD_NUM_CTX

    _gu = (GUARD_URL or "").rstrip("/")
    if not GUARD_MODEL or not _gu or _url_api_type(_gu) != "ollama":
        return
    try:
        async with httpx.AsyncClient() as _hc:
            await _hc.post(
                f"{_gu.removesuffix('/v1')}/api/chat",
                json={
                    "model": GUARD_MODEL,
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": False,
                    "keep_alive": GUARD_KEEP_ALIVE,
                    "options": {"num_ctx": GUARD_NUM_CTX},
                },
                headers={"Authorization": f"Bearer {GUARD_TOKEN}"},
                timeout=300.0,
            )
        logger.info("🛡️ Guard model '%s' pre-warmed at startup", GUARD_MODEL)
    except Exception as exc:
        logger.warning("🛡️ Guard model pre-warm failed (will keep failing open until warm): %s", exc)


class _OllamaAwareJudgeLLM:
    """ainvoke()-compatible wrapper around ainvoke_judge_llm.

    Drop-in replacement for the raw judge_llm singleton at call sites that expect
    an object with an async .ainvoke(prompt) method (e.g. extract_and_ingest's
    `llm` parameter).
    """

    async def ainvoke(self, prompt):
        return await ainvoke_judge_llm(prompt)


judge_llm_ollama_aware = _OllamaAwareJudgeLLM()


async def _invoke_planner_with_retry(
    state: "AgentState", prompt: str, temperature: float | None = None, attempt: int = 0
) -> tuple:
    """Invoke the planner LLM, returns (res, used_fallback: bool).

    Supports PLANNER_MODE = llm | slm_local | hybrid.
    If slm_local or (hybrid and attempt == 0), attempts to run local GGUF
    in-process via llama-cpp-python, falling back to local Ollama/llama.cpp server
    if unavailable.
    """
    from types import SimpleNamespace as _NS
    from config import (
        PLANNER_MODEL, PLANNER_URL, PLANNER_TOKEN,
        PLANNER_MODE, PLANNER_LOCAL_GGUF_PATH
    )

    # 1. Try local GGUF/SLM in-process execution first if configured
    use_local_slm = (
        PLANNER_MODE == "slm_local"
        or (PLANNER_MODE == "hybrid" and attempt == 0)
    )

    if use_local_slm:
        local_llm = await _get_local_llama()
        if local_llm is not None:
            try:
                logger.info("🤖 Executing local GGUF planner (in-process)...")
                _local_audit = _audit_create(
                    state.get("session_id", ""),
                    state.get("response_id", ""),
                    PLANNER_MODEL or "local-gguf",
                    f"file://{PLANNER_LOCAL_GGUF_PATH}",
                    "planner",
                    {"prompt": prompt, "local_runtime": "llama-cpp"},
                )
                def _run_local():
                    return local_llm(
                        prompt=prompt,
                        max_tokens=MAX_PLANNER_TOKENS,
                        temperature=temperature if temperature is not None else 0.2,
                    )
                _res = await wait_for_budget(
                    asyncio.to_thread(_run_local),
                    state,
                    PLANNER_TIMEOUT,
                    stage="planner_local",
                )
                _content = _res["choices"][0]["text"].strip()
                _usage = _res.get("usage", {})
                await _audit_complete(
                    _local_audit,
                    {"content": _content},
                    _usage.get("prompt_tokens"),
                    _usage.get("completion_tokens"),
                )
                if _content:
                    return _NS(
                        content=_content,
                        usage_metadata={
                            "input_tokens":  int(_usage.get("prompt_tokens", 0)),
                            "output_tokens": int(_usage.get("completion_tokens", 0)),
                        },
                    ), False
            except asyncio.CancelledError:
                if "_local_audit" in locals():
                    await _audit_cancel(_local_audit)
                raise
            except RequestDeadlineExceeded:
                if "_local_audit" in locals():
                    await _audit_cancel(_local_audit)
                raise
            except Exception as e:
                if "_local_audit" in locals():
                    await _audit_complete(
                        _local_audit,
                        {"error": str(e)},
                        None,
                        None,
                        "error",
                    )
                logger.warning("⚠️ Local in-process GGUF planner failed: %s", e)

    _pm = (state.get("planner_model_override") or "").strip() or (PLANNER_MODEL or "")
    _pu = (state.get("planner_url_override")   or "").strip() or (PLANNER_URL or "")
    _pt = (state.get("planner_token_override") or "").strip() or (PLANNER_TOKEN or "ollama")
    # Floating mode: model set but URL empty → discover best node
    if (state.get("planner_model_override") or "").strip() and not (state.get("planner_url_override") or "").strip():
        _all_eps = [s["name"] for s in INFERENCE_SERVERS_LIST]
        _node = await _select_node(_pm, _all_eps, user_id=state.get("user_id", ""))
        _pu = _node.get("url") or URL_MAP.get(_node["name"], "")
        _pt = _node.get("token", "ollama")
        logger.info("🌐 Floating planner: %s → %s", _pm, _node["name"])
    _p_url_base = (_pu or "").rstrip("/")
    _p_api_type = _url_api_type(_p_url_base)

    # Sovereignty guard — same reasoning as _invoke_judge_with_retry: covers
    # both the native-Ollama branch below and the ChatOpenAI/
    # _invoke_llm_with_fallback branch further down, both of which dispatch
    # to this resolved _p_url_base.
    from services.sovereignty import assert_egress_allowed
    assert_egress_allowed(_p_url_base, bool(state.get("local_only_routing")))

    if _p_api_type == "ollama" and _pm and _p_url_base and not _endpoint_is_degraded(_p_url_base):
        _ollama_base = _p_url_base.removesuffix("/v1")
        _ctx = int(state.get("planner_num_ctx") or 0) or PLANNER_NUM_CTX or _static_ctx(_pm)
        _planner_output_limit = bounded_output_tokens(
            state,
            MAX_PLANNER_TOKENS,
            minimum_internal=256,
        )
        _ctx = adaptive_context_window(
            _ctx,
            prompt,
            _planner_output_limit,
        )
        if PLANNER_NUM_CTX > 0 and _ctx > PLANNER_NUM_CTX:
            _ctx = PLANNER_NUM_CTX
        # Never downgrade a warm model — same reasoning and pattern as the judge
        # branch above (_invoke_judge_with_retry) and the Augmented Tool Path
        # (services/pipeline/anthropic.py). Checked before the eviction call
        # below so reusing a warm larger-context load also correctly skips
        # evicting competing models for VRAM this request doesn't actually need.
        try:
            async with httpx.AsyncClient(timeout=2.0) as _ps_cl:
                _ps_r = await _ps_cl.get(
                    f"{_ollama_base}/api/ps",
                    headers={"Authorization": f"Bearer {_pt}"},
                )
                for _loaded in _ps_r.json().get("models", []):
                    _lname = _loaded.get("name", "").split(":")[0]
                    _ename = _pm.split(":")[0]
                    _loaded_ctx = _loaded.get("context_length", 0)
                    if _lname == _ename and _loaded_ctx >= _ctx:
                        logger.info(
                            "planner: reusing warm model ctx=%d (requested %d, no reload needed, model=%s)",
                            _loaded_ctx, _ctx, _pm,
                        )
                        _ctx = _loaded_ctx
                        break
        except Exception:
            pass  # non-fatal — fall through to the configured num_ctx
        if _ctx >= 65536:
            await _evict_competing_models(_ollama_base, _pm, ctx=_ctx)
        _opts: dict = {"num_predict": _planner_output_limit}
        if _ctx > 0:
            _opts["num_ctx"] = _ctx
        if temperature is not None:
            _opts["temperature"] = temperature
        _planner_schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "category": {"type": "string"},
                },
                "required": ["task", "category"],
            },
        }
        _payload: dict = {
            "model":      _pm,
            "messages":   [{"role": "user", "content": prompt}],
            "stream":     False,
            "think":      PLANNER_THINKING_ENABLED,
            "keep_alive": _DEFAULT_OLLAMA_KEEP_ALIVE,
            "options":    _opts,
        }
        _payload = apply_ollama_structured_capability(
            _payload, _pm, _planner_schema
        )
        logger.debug("model=%s caps=%s", _pm, get_model_caps(_pm))
        _audit_entry = _audit_create(
            state.get("session_id", ""),
            state.get("response_id", ""),
            _pm,
            f"{_ollama_base}/api/chat",
            "planner",
            _payload,
        )
        try:
            _planner_timeout = remaining_timeout(
                state,
                PLANNER_TIMEOUT,
                stage="planner",
            )
            async with httpx.AsyncClient(timeout=_planner_timeout) as _hc:
                _r = await _hc.post(
                    f"{_ollama_base}/api/chat", json=_payload,
                    headers={"Authorization": f"Bearer {_pt}"},
                )
            _r.raise_for_status()
            _data = _r.json()
            await _audit_complete(
                _audit_entry, _data,
                _data.get("prompt_eval_count"), _data.get("eval_count"),
            )
            _content = _data.get("message", {}).get("content", "")
            if _content and _content.strip():
                return _NS(
                    content=_content,
                    usage_metadata={
                        "input_tokens":  int(_data.get("prompt_eval_count", 0)),
                        "output_tokens": int(_data.get("eval_count", 0)),
                    },
                ), False
            logger.warning("⚠️ Planner: empty response from native /api/chat — falling back")
        except asyncio.CancelledError:
            await _audit_cancel(_audit_entry)
            raise
        except RequestDeadlineExceeded:
            await _audit_cancel(_audit_entry)
            raise
        except Exception as e:
            await _audit_complete(
                _audit_entry, {"error": str(e)}, None, None, "error"
            )
            logger.warning(
                "⚠️ Planner: native /api/chat failed [%s] (%s) — falling back",
                type(e).__name__, str(e)[:120],
            )

    # Non-Ollama endpoint, or native call failed/empty → ChatOpenAI path with fallback chain.
    # Bind extra_body so Ollama honours num_ctx — the OpenAI-compat endpoint silently drops
    # the options dict unless it is sent as extra_body at the top level of the request.
    llm = await _get_planner_llm(state)
    llm = llm.bind(
        max_tokens=bounded_output_tokens(
            state,
            MAX_PLANNER_TOKENS,
            minimum_internal=256,
        )
    )
    if _p_api_type == "ollama":
        # Re-derive ctx here; _ctx may be unbound when endpoint was degraded/skipped.
        _fb_ctx = int(state.get("planner_num_ctx") or 0) or PLANNER_NUM_CTX or 0
        _fb_ctx = adaptive_context_window(
            _fb_ctx,
            prompt,
            bounded_output_tokens(
                state,
                MAX_PLANNER_TOKENS,
                minimum_internal=256,
            ),
        )
        # Never downgrade a warm model — same pattern as the native-call branch
        # above. This fallback path re-derives ctx independently (see comment
        # above), so it needs its own reuse check rather than inheriting one.
        if _fb_ctx > 0:
            try:
                async with httpx.AsyncClient(timeout=2.0) as _ps_cl:
                    _ps_r = await _ps_cl.get(
                        f"{_p_url_base.removesuffix('/v1')}/api/ps",
                        headers={"Authorization": f"Bearer {_pt}"},
                    )
                    for _loaded in _ps_r.json().get("models", []):
                        _lname = _loaded.get("name", "").split(":")[0]
                        _ename = _pm.split(":")[0]
                        _loaded_ctx = _loaded.get("context_length", 0)
                        if _lname == _ename and _loaded_ctx >= _fb_ctx:
                            logger.info(
                                "planner (fallback path): reusing warm model ctx=%d (requested %d, no reload needed, model=%s)",
                                _loaded_ctx, _fb_ctx, _pm,
                            )
                            _fb_ctx = _loaded_ctx
                            break
            except Exception:
                pass  # non-fatal — fall through to the adaptive ctx
        _planner_extra_body: dict = {
            "think": PLANNER_THINKING_ENABLED,
        }
        if _fb_ctx > 0:
            _planner_extra_body["options"] = {"num_ctx": _fb_ctx}
        llm = llm.bind(extra_body=_planner_extra_body)
    _planner_schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "category": {"type": "string"},
            },
            "required": ["task", "category"],
        },
    }
    _response_format = openai_response_format(_pm, _planner_schema)
    if _response_format:
        llm = llm.bind(response_format=_response_format)
    if temperature is not None:
        llm = llm.bind(temperature=temperature)
    return await _invoke_llm_with_fallback(
        llm, _p_url_base, prompt, timeout=PLANNER_TIMEOUT,
        label="Planner", audit_context=state,
        audit_stage="planner", model=_pm,
    )


def _inject_habe_prefix_embeddings(opts: dict, state_: Optional[dict] = None) -> None:
    """No-op. HABE modulation is now performed client-side in the GraphRAG node prior to LLM call."""
    return


def _judge_model_kw(model: str, state_num_ctx: int = 0, state_: Optional[dict] = None) -> dict:
    """Return kwargs to spread (**) directly into ChatOpenAI for a judge call.

    Contains model_kwargs (max_tokens) and, when ctx is known, extra_body as a
    top-level key.  extra_body must NOT be placed inside model_kwargs — LangChain
    silently drops it from there, causing Ollama to use its Modelfile default
    num_ctx (8192) and reload the already-warm model.

    Priority: state_num_ctx (per-template) > JUDGE_NUM_CTX (global env) > static table.
    Clamps the resolved context window to the model's VRAM-safe context limit (or explicit tag in model name)
    to prevent overloading VRAM for large models (e.g. 70B).
    """
    out: dict = {"max_tokens": MAX_JUDGE_TOKENS}
    ctx = resolve_requested_ctx(model, state_num_ctx, JUDGE_NUM_CTX, label="judge")
    opts = {}
    if ctx > 0:
        opts["num_ctx"] = ctx
    if state_ and state_.get("enable_habe"):
        _inject_habe_prefix_embeddings(opts, state_)
    if opts:
        out["extra_body"] = {"options": opts}
    return out


def _planner_model_kw(model: str, state_num_ctx: int = 0, state_: Optional[dict] = None) -> dict:
    """Return kwargs to spread (**) directly into ChatOpenAI for a planner call.

    Same contract as _judge_model_kw: extra_body is a top-level key, not nested
    inside model_kwargs. Both num_ctx and num_predict are set so Ollama does not
    fall back to Modelfile defaults that may truncate the plan JSON.

    Priority: state_num_ctx (per-template) > PLANNER_NUM_CTX (global env) > static table.
    Clamps the resolved context window to the model's VRAM-safe context limit to prevent VRAM overflow.
    """
    out: dict = {"max_tokens": MAX_PLANNER_TOKENS}
    ctx = resolve_requested_ctx(model, state_num_ctx, PLANNER_NUM_CTX, label="planner")
    opts: dict = {"num_predict": MAX_PLANNER_TOKENS}
    if ctx > 0:
        opts["num_ctx"] = ctx
    if state_ and (state_.get("pin_prefix_cache") or state_.get("template_prefix_locked")):
        opts["keep_alive"] = -1  # Static Template KV-Locking (vLLM/Ollama Pinned Prefix Cache)
    if state_ and state_.get("enable_habe"):
        _inject_habe_prefix_embeddings(opts, state_)
    out["extra_body"] = {"options": opts}
    return out


async def _get_judge_llm(state: "AgentState") -> "ChatOpenAI":
    """Returns per-template judge LLM, or global judge_llm as fallback.
    Supports floating mode: if model is set but URL is empty, discovers the best node.
    When the configured endpoint is in degraded state, returns the fallback node directly.
    Respects state['judge_num_ctx'] for per-template context window override."""
    _state_num_ctx = int(state.get("judge_num_ctx") or 0)
    m = (state.get("judge_model_override") or "").strip()
    u = (state.get("judge_url_override")   or "").strip()
    t = (state.get("judge_token_override") or "ollama").strip()
    if m and u:
        if _endpoint_is_degraded(u.rstrip("/")) and _FALLBACK_ENABLED:
            logger.info("⚡ Judge endpoint degraded — returning fallback LLM directly")
            return await _get_fallback_llm(JUDGE_TIMEOUT)
        return ChatOpenAI(model=m, base_url=u, api_key=t, timeout=JUDGE_TIMEOUT,
                          **_judge_model_kw(m, _state_num_ctx, state))
    if m and not u:
        # Floating judge: discover the best node for this model
        all_eps = [s["name"] for s in INFERENCE_SERVERS_LIST]
        node = await _select_node(m, all_eps, user_id=state.get("user_id", ""))
        _url = node.get("url") or URL_MAP.get(node["name"], "")
        _tok = node.get("token", "ollama")
        logger.info(f"🌐 Floating judge: {m} → {node['name']}")
        return ChatOpenAI(model=m, base_url=_url, api_key=_tok, timeout=JUDGE_TIMEOUT,
                          **_judge_model_kw(m, _state_num_ctx, state))
    # No model override — if num_ctx differs from global, create a fresh instance
    if _state_num_ctx > 0:
        from services.llm_instances import _judge_num_ctx as _global_judge_ctx
        if _state_num_ctx != _global_judge_ctx:
            return ChatOpenAI(model=JUDGE_MODEL, base_url=JUDGE_URL, api_key=JUDGE_TOKEN,
                              timeout=JUDGE_TIMEOUT,
                              **_judge_model_kw(JUDGE_MODEL, _state_num_ctx, state))
    return judge_llm


async def _get_planner_llm(state: "AgentState") -> "ChatOpenAI":
    """Returns per-template planner LLM, or global planner_llm as fallback.
    Supports floating mode: if model is set but URL is empty, discovers the best node.
    When the configured endpoint is in degraded state, returns the fallback node directly.
    Respects state['planner_num_ctx'] for per-template context window override."""
    _state_num_ctx = int(state.get("planner_num_ctx") or 0)
    m = (state.get("planner_model_override") or "").strip()
    u = (state.get("planner_url_override")   or "").strip()
    t = (state.get("planner_token_override") or "ollama").strip()
    if m and u:
        if _endpoint_is_degraded(u.rstrip("/")) and _FALLBACK_ENABLED:
            logger.info("⚡ Planner endpoint degraded — returning fallback LLM directly")
            return await _get_fallback_llm(PLANNER_TIMEOUT)
        return ChatOpenAI(model=m, base_url=u, api_key=t, timeout=PLANNER_TIMEOUT,
                          **_planner_model_kw(m, _state_num_ctx, state))
    if m and not u:
        # Floating planner: discover the best node for this model
        all_eps = [s["name"] for s in INFERENCE_SERVERS_LIST]
        node = await _select_node(m, all_eps, user_id=state.get("user_id", ""))
        _url = node.get("url") or URL_MAP.get(node["name"], "")
        _tok = node.get("token", "ollama")
        logger.info(f"🌐 Floating planner: {m} → {node['name']}")
        return ChatOpenAI(model=m, base_url=_url, api_key=_tok, timeout=PLANNER_TIMEOUT,
                          **_planner_model_kw(m, _state_num_ctx, state))
    # No model override — if num_ctx differs from global, create a fresh instance
    if _state_num_ctx > 0:
        from services.llm_instances import _planner_num_ctx as _global_planner_ctx
        if _state_num_ctx != _global_planner_ctx:
            return ChatOpenAI(model=PLANNER_MODEL, base_url=PLANNER_URL, api_key=PLANNER_TOKEN,
                              timeout=PLANNER_TIMEOUT,
                              **_planner_model_kw(PLANNER_MODEL, _state_num_ctx, state))
    return planner_llm


async def _refine_expert_response(cat: str, gap_feedback: str, state: "AgentState") -> Optional[str]:
    """Re-calls the score-best expert for `cat`, enriched with judge gap feedback."""
    from config import EXPERTS, EXPERT_TIMEOUT
    from main import _get_expert_prompt

    # Template-scoped experts take precedence over the global fallback — same
    # precedence used everywhere else this state key is consulted (see
    # graph/expert.py and services/routing.py._get_template_expert_catalog).
    # Without this, a refinement round silently ignores the active template's
    # per-category endpoint pinning and can dispatch to a stale global
    # endpoint the template never authorized.
    _user_experts = state.get("user_experts") or {}
    experts_for_cat = _user_experts.get(cat) or EXPERTS.get(cat, [])
    if not experts_for_cat:
        return None
    scored = [(await _get_expert_score(e["model"], cat), e) for e in experts_for_cat]
    scored.sort(key=lambda x: -x[0])
    best_expert = scored[0][1]
    _refine_ep = best_expert.get("endpoints") or [best_expert.get("endpoint", "")]
    if not _refine_ep or _refine_ep == [""]:
        _refine_ep = [s["name"] for s in INFERENCE_SERVERS_LIST]
    node = await _select_node(best_expert["model"], _refine_ep)
    url      = node.get("url") or URL_MAP.get(node["name"])
    token    = node.get("token", "ollama")
    _timeout = float(node.get("timeout", EXPERT_TIMEOUT))
    sys_prompt = _get_expert_prompt(cat, state.get("user_experts"))
    task_text  = state["input"]
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user",   "content": (
            f"{task_text}\n\n"
            f"--- FEEDBACK DES JUDGES (Bitte gezielt verbessern) ---\n{gap_feedback}"
        )},
    ]
    _refine_extra: dict = {}
    if token == "ollama":
        _refine_num_ctx = int(JUDGE_NUM_CTX or 262144)
        # Never downgrade (or needlessly upgrade) a warm model: this call
        # previously always requested a fixed, large ctx regardless of what
        # was already resident, forcing an unload+reload whenever the model
        # was already loaded at a smaller context -- the same bug class
        # already fixed at the judge/planner/expert call sites (see
        # agent_status/claude-code.md, feedback_ollama_num_ctx_reuse_pattern
        # in project memory). Reuse the loaded context if it's already large
        # enough instead of forcing a resize.
        try:
            _ollama_base = url.rstrip("/").removesuffix("/v1")
            async with httpx.AsyncClient(timeout=2.0) as _ps_cl:
                _ps_r = await _ps_cl.get(
                    f"{_ollama_base}/api/ps",
                    headers={"Authorization": f"Bearer {token}"},
                )
                for _loaded in _ps_r.json().get("models", []):
                    _lname = _loaded.get("name", "").split(":")[0]
                    _ename = best_expert["model"].split(":")[0]
                    _loaded_ctx = _loaded.get("context_length", 0)
                    if _lname == _ename and _loaded_ctx >= _refine_num_ctx:
                        logger.info(
                            "expert refinement: reusing warm model ctx=%d "
                            "(requested %d, no reload needed, model=%s)",
                            _loaded_ctx, _refine_num_ctx, best_expert["model"],
                        )
                        _refine_num_ctx = _loaded_ctx
                        break
        except Exception:
            pass  # non-fatal — fall through to the configured num_ctx
        _refine_extra = {"extra_body": {"options": {"num_ctx": _refine_num_ctx}}}
    llm = ChatOpenAI(model=best_expert["model"], base_url=url, api_key=token,
                     timeout=_timeout, **_refine_extra)
    try:
        res = await _audited_ainvoke(
            llm,
            messages,
            endpoint=url,
            model=best_expert["model"],
            stage="expert_refinement",
            context=state,
        )
        return res.content[:MAX_EXPERT_OUTPUT_CHARS] if res.content else None
    except Exception as e:
        logger.warning(f"⚠️ Refinement Expert [{cat}]: {e}")
        return None


# ---------------------------------------------------------------------------
# PS cache and node load
# ---------------------------------------------------------------------------

_ps_cache: Dict[str, tuple] = {}
_PS_CACHE_TTL = 5.0  # seconds — Ollama state does not change faster


def _get_model_node_load(model: str) -> float:
    """Return the infrastructure load [0.0, 1.0] of the server currently hosting `model`.

    Reads the _ps_cache populated by _pick_inference_server (no additional API
    calls). The load score is running_models / gpu_count, identical to the
    load_score() computation in _pick_inference_server.

    Used by _get_expert_score to construct an infrastructure-aware Beta prior:
    busy nodes receive an inflated beta parameter, reducing their Thompson
    sample and steering expert selection toward less-loaded hardware.

    Returns 0.0 (no penalty) when the model is not found in any cached server,
    which is the safe fallback for cold-start situations.
    """
    model_base = model.split(":")[0]
    with _cache_lock:
        for srv_name, (_, running) in _ps_cache.items():
            if any(m.get("name", "").split(":")[0] == model_base for m in running):
                srv = next((s for s in INFERENCE_SERVERS_LIST if s["name"] == srv_name), None)
                if srv:
                    return min(1.0, len(running) / max(int(srv.get("gpu_count", 1)), 1))
    return 0.0


def _estimate_model_vram_gb(model_name: str) -> float:
    """Estimate VRAM requirement in GB from model name.

    Parses the parameter count AND quantization from the name and applies the
    correct bytes-per-parameter multiplier.  The default Q4 estimate is only
    correct for GGUF Q4_K_M models; fp16/fp32/q8 variants need different math.

    Examples
    --------
    phi4:14b-fp16     → 14 × 2.0 + 2 ≈ 30 GB   (fp16, 2 B/param)
    qwen3.6:35b       → 35 × 0.55 + 1.5 ≈ 21 GB (Q4 default)
    llama3.3:70b-q8_0 → 70 × 1.1  + 2  ≈ 79 GB  (Q8)
    gemma4:31b-fp32   → 31 × 4.0  + 2  ≈ 126 GB (fp32)

    Returns 0 when the parameter count cannot be parsed (disables filtering).
    """
    import re as _re
    name = model_name.lower()

    # Extract parameter count: "phi4:14b-fp16", "llama3.3:70b", "gemma4:31b"
    m = _re.search(r"[:\-](\d+(?:\.\d+)?)b", name)
    if not m:
        return 0.0
    params_b = float(m.group(1))

    # Quantization-aware bytes-per-parameter
    if _re.search(r"[-_]?fp32", name):
        bpp = 4.0
    elif _re.search(r"[-_]?fp16", name):
        bpp = 2.0
    elif _re.search(r"[-_]?fp8", name):
        bpp = 1.0
    elif _re.search(r"[-_]?q8", name):
        bpp = 1.1
    elif _re.search(r"[-_]?q6", name):
        bpp = 0.75
    elif _re.search(r"[-_]?q2", name):
        bpp = 0.30
    else:
        bpp = 0.55  # Q4_K_M default

    # Overhead: KV-cache, runtime tensors, activations (~1.5–2 GB for small models,
    # larger for fp16/fp32 due to bigger activation buffers)
    overhead = 2.0 if bpp >= 2.0 else 1.5
    return params_b * bpp + overhead


def _node_vram_by_url(base_url: str) -> float:
    """Return the configured vram_gb for the node that serves base_url, or 0."""
    url = base_url.rstrip("/")
    for srv in INFERENCE_SERVERS_LIST:
        if srv.get("url", "").rstrip("/") == url:
            return float(srv.get("vram_gb", 0))
    return 0.0


async def _select_node(model_name: str, allowed_endpoints: List[str],
                       user_id: str = "", priority: str = "normal") -> dict:
    """Selects the optimal node for model_name from the allowed endpoints.

    Strategy (4 phases):
    0. VRAM filter: exclude nodes where vram_gb < estimated model requirement
    1. Sticky session: if user recently used this model on a node, prefer it
    2. Check Ollama /api/ps (with 5s cache) for warm/cold models
    3. Within warm/cold: lowest load score (running/gpu_count) wins
    Priority: 'high' = pinned templates, 'normal' = standard, 'low' = floating/batch
    OpenAI nodes: always cold, neutral load.
    """
    # Phase 0: Sticky session check (warm model affinity for same user)
    if state.redis_client and user_id:
        try:
            sticky_key = f"moe:sticky:{user_id}:{model_name.split(':')[0]}"
            sticky_node = await state.redis_client.get(sticky_key)
            if sticky_node:
                sticky_name = sticky_node if isinstance(sticky_node, str) else sticky_node.decode()
                if sticky_name in allowed_endpoints:
                    srv = _server_info(sticky_name)
                    if srv:
                        # VRAM guard: verify sticky node can actually fit the model
                        est = _estimate_model_vram_gb(model_name)
                        node_vram = float(srv.get("vram_gb", 0))
                        if est > 0 and node_vram > 0 and node_vram < est:
                            await state.redis_client.delete(sticky_key)
                            logger.warning(f"🔒 Sticky override: {sticky_name} has {node_vram}GB but {model_name} needs ~{est:.1f}GB — re-routing")
                        else:
                            logger.debug(f"📌 Sticky session: {sticky_name} for {model_name}")
                            return srv
        except Exception:
            pass
    # Dynamic server exclusions stored in Redis (survive container restarts without rebuild)
    _blocked: set = set()
    _float_disabled: set = set()
    if state.redis_client is not None:
        try:
            _blocked       = {(v if isinstance(v, str) else v.decode()) for v in await state.redis_client.smembers("moe:blocked_servers")}
            _float_disabled = {(v if isinstance(v, str) else v.decode()) for v in await state.redis_client.smembers("moe:floating_disabled_servers")}
        except Exception:
            pass
    # Hard block: remove from every pool regardless of pinning
    _effective = [ep for ep in allowed_endpoints if ep not in _blocked]
    # Floating-disable: only applies when multiple endpoints are in the pool
    # (single-endpoint = explicit @node pin — always honoured)
    if len(_effective) > 1:
        _effective = [ep for ep in _effective if ep not in _float_disabled]
    candidates = [s for s in INFERENCE_SERVERS_LIST if s["name"] in _effective]
    if not candidates:
        # Fall back to the first non-blocked endpoint (preserves liveness)
        fallback_name = _effective[0] if _effective else (allowed_endpoints[0] if allowed_endpoints else "")
        return _server_info(fallback_name) or {"name": fallback_name, "url": URL_MAP.get(fallback_name, ""), "token": "ollama", "api_type": "ollama"}

    # Phase 0: VRAM filter — exclude nodes that cannot fit this model
    est_vram = _estimate_model_vram_gb(model_name)
    if est_vram > 0:
        vram_ok = [s for s in candidates if float(s.get("vram_gb", 0)) >= est_vram]
        if vram_ok:
            if len(vram_ok) < len(candidates):
                excluded = [s["name"] for s in candidates if s not in vram_ok]
                logger.info(f"🔒 VRAM filter: {model_name} needs ~{est_vram:.1f}GB — excluded {excluded}")
            candidates = vram_ok
        else:
            # Hard filter: only keep nodes WITHOUT a vram_gb limit (cloud/external).
            # Nodes with no_auto_fallback:true are excluded — they require explicit routing.
            no_limit = [s for s in candidates if not s.get("vram_gb") and not s.get("no_auto_fallback")]
            if no_limit:
                logger.warning(f"⚠️ No local node has enough VRAM for {model_name} (~{est_vram:.1f}GB) — using cloud/external nodes only")
                candidates = no_limit
            else:
                logger.error(f"🚫 VRAM hard block: {model_name} (~{est_vram:.1f}GB) exceeds ALL nodes — routing to largest available")
                candidates = sorted(candidates, key=lambda s: float(s.get("vram_gb", 0)), reverse=True)[:1]

    if len(candidates) == 1:
        return candidates[0]

    async def _get_ps(srv: dict) -> tuple:
        """Returns (srv, running_models_list, model_is_warm). Uses 5s cache."""
        if srv.get("api_type", "ollama") != "ollama":
            return srv, [], False
        now = time.monotonic()
        with _cache_lock:
            cached = _ps_cache.get(srv["name"])
        if cached and (now - cached[0]) < _PS_CACHE_TTL:
            running = cached[1]
        else:
            base = srv["url"].rstrip("/").removesuffix("/v1")
            try:
                async with httpx.AsyncClient(timeout=4.0) as client:
                    r = await client.get(f"{base}/api/ps")
                    running = r.json().get("models", []) if r.status_code == 200 else []
            except Exception:
                running = []
            with _cache_lock:
                _ps_cache[srv["name"]] = (now, running)
        is_warm = any(
            m.get("name", "").split(":")[0] == model_name.split(":")[0]
            and (not model_name.count(":") or m.get("name") == model_name)
            for m in running
        )
        return srv, running, is_warm

    # Flood-fill-style reliability weighting: a node/model combo that has
    # recently been slow (moe:latency:{node}, see services/tracking.py) or
    # prone to the tool-passthrough "premature stop" failure
    # (moe:pstop:{model}:{node}) gets its load score inflated, same
    # multiplicative-penalty shape as the existing Thompson-sampling
    # THOMPSON_LOAD_PENALTY in _get_expert_score below. Optimistic by
    # default (factor 1.0) until enough evidence accumulates — a node with
    # no data yet, or too few premature-stop samples, is never penalized on
    # a single bad observation, mirroring flood fill assuming a cell is open
    # until a wall is actually confirmed. Precomputed once per candidate
    # here (not inside load_score) because load_score itself stays a plain
    # sync function used inside min()/list comprehensions below — this is
    # the one extra async gather needed to feed it.
    _NODE_LATENCY_PENALTY = float(os.getenv("NODE_LATENCY_PENALTY", "0.3"))
    _NODE_PSTOP_PENALTY   = float(os.getenv("NODE_PSTOP_PENALTY", "2.0"))
    _LATENCY_BASELINE_MS  = 3000.0

    async def _reliability_factor(srv: dict) -> float:
        factor = 1.0
        try:
            lat = await _get_node_latency_stats(srv["name"])
            if lat["avg_ms"]:
                factor *= 1.0 + _NODE_LATENCY_PENALTY * max(0.0, lat["avg_ms"] / _LATENCY_BASELINE_MS - 1.0)
        except Exception:
            pass
        try:
            pstop_rate = await _get_premature_stop_rate(model_name, srv["name"])
            factor *= 1.0 + _NODE_PSTOP_PENALTY * pstop_rate
        except Exception:
            pass
        return factor

    _reliability = dict(zip(
        (s["name"] for s in candidates),
        await asyncio.gather(*[_reliability_factor(s) for s in candidates]),
    ))

    def load_score(srv: dict, running: list) -> float:
        """Lower score = better candidate. Factors in GPU count AND cost_factor.
        cost_factor acts as a speed/priority weight: higher = faster/preferred.
        RTX (1.0) is preferred over Tesla M10 (0.8) at equal load. Also
        folds in _reliability (latency + premature-stop penalty, see above)
        — added, not multiplied: at raw_load=0 (fully idle, the common case
        on lightly-loaded infra) a multiplicative penalty would vanish
        (0 * factor == 0), silently undoing the whole point of penalizing an
        idle-but-unreliable node. _reliability - 1.0 is 0.0 for a clean node
        (no change to existing behaviour) and > 0.0 once latency/pstop
        evidence justifies a penalty, regardless of current load."""
        raw_load = len(running) / max(int(srv.get("gpu_count", 1)), 1)
        speed = float(srv.get("cost_factor", 1.0))  # higher = faster GPU
        base = raw_load / max(speed, 0.1)  # divide by speed: fast nodes get lower scores
        return base + (_reliability.get(srv["name"], 1.0) - 1.0)

    # Select best candidate: warm preferred, then idle, then lowest load
    ps_results = await asyncio.gather(*[_get_ps(s) for s in candidates])
    warm = [(srv, running) for srv, running, is_warm in ps_results if is_warm]
    cold = [(srv, running) for srv, running, is_warm in ps_results if not is_warm]

    # Priority order: 1) warm + idle, 2) warm + busy, 3) cold + idle, 4) cold + busy
    warm_idle = [(s, r) for s, r in warm if load_score(s, r) < 0.5]
    cold_idle = [(s, r) for s, r in cold if load_score(s, r) < 0.5]

    pool = warm_idle or warm or cold_idle or cold
    best = min(pool, key=lambda x: load_score(x[0], x[1]))

    status = "🔥 warm" if warm else "❄️ cold"
    busy = "idle" if load_score(best[0], best[1]) < 0.5 else "busy"
    logger.debug(f"{status}/{busy} Node-Select: {best[0]['name']} for {model_name}")

    # Set sticky session for future requests from this user
    if state.redis_client and user_id:
        try:
            sticky_key = f"moe:sticky:{user_id}:{model_name.split(':')[0]}"
            asyncio.create_task(
                state.redis_client.setex(sticky_key, 300, best[0]["name"])  # 5 min TTL
            )
        except Exception:
            pass

    return best[0]


# ---------------------------------------------------------------------------
# Expert scoring (Thompson sampling)
# ---------------------------------------------------------------------------

# THOMPSON_SAMPLING_ENABLED imported from config.py


async def _get_expert_score(
    model: str,
    category: str,
    query_embedding: Optional[Sequence[float]] = None,
) -> float:
    """Performance score 0-1 for a model in a category.

    When ``THOMPSON_SAMPLING_ENABLED`` is true, draws from Beta(α, β) instead
    of the deterministic Laplace point estimate.  This provides natural
    exploration: experts with fewer observations have wider variance and
    occasionally score higher than their point estimate, giving them a chance
    to prove themselves.

    While the bucket is below EXPERT_MIN_DATAPOINTS and both
    THOMPSON_SAMPLING_ENABLED and ROUTING_PATTERN_PRIOR_ENABLED are true,
    blends in a capped, distance-weighted Beta prior from the k nearest
    historical observations in embedding space (services/routing_patterns.py)
    instead of returning the flat 0.5 fallback. No effect otherwise.
    """
    if state.redis_client is None:
        return 0.5
    try:
        key = _perf_key(model, category)
        data = await state.redis_client.hgetall(key)
        total = int(data.get("total", 0))
        positive = int(data.get("positive", 0))
        if total < EXPERT_MIN_DATAPOINTS:
            if not (THOMPSON_SAMPLING_ENABLED and ROUTING_PATTERN_PRIOR_ENABLED and query_embedding is not None):
                return 0.5
            prior_pos, prior_total = await routing_patterns.prior(
                "expert", f"{model}:{category}", query_embedding,
                ROUTING_PATTERN_PRIOR_K, ROUTING_PATTERN_PRIOR_CAP,
            )
            if prior_total <= 0.0:
                return 0.5
            import random
            alpha = positive + prior_pos + 1
            beta  = (total - positive) + (prior_total - prior_pos) + 1
            _LOAD_PENALTY = float(os.getenv("THOMPSON_LOAD_PENALTY", "2.0"))
            _node_load    = _get_model_node_load(model)
            beta          = beta * (1.0 + _LOAD_PENALTY * _node_load)
            score = random.betavariate(alpha, beta)
            PROM_THOMPSON.observe(score)
            return score
        if THOMPSON_SAMPLING_ENABLED:
            import random
            alpha = positive + 1
            beta  = (total - positive) + 1
            # Infrastructure-adaptive prior (Bayesian maximum-entropy principle):
            # Inflate beta by node load so busy servers draw lower Thompson
            # samples, steering selection toward less-loaded hardware.
            # At load=0.0: beta unchanged (idle node, no penalty).
            # At load=1.0: beta *= (1 + _LOAD_PENALTY) — e.g. 3× at penalty=2.
            # The Beta distribution remains well-defined for all positive (α, β).
            _LOAD_PENALTY = float(os.getenv("THOMPSON_LOAD_PENALTY", "2.0"))
            _node_load    = _get_model_node_load(model)
            beta          = beta * (1.0 + _LOAD_PENALTY * _node_load)
            score = random.betavariate(alpha, beta)
            PROM_THOMPSON.observe(score)
            return score
        return (positive + 1) / (total + 2)  # Laplace fallback
    except Exception:
        return 0.5

async def _record_expert_outcome(
    model: str,
    category: str,
    positive: bool,
    query_embedding: Optional[Sequence[float]] = None,
) -> None:
    """Increments total and optionally positive counter for a model/category pair.

    When ``query_embedding`` is given, also records the observation into the
    embedding-space pattern store (services/routing_patterns.py) so future
    cold-start buckets for similar contexts can draw on it. No-op for that
    part when no embedding is passed — identical to today's behaviour.
    """
    if state.redis_client is None:
        return
    try:
        key = _perf_key(model, category)
        pipe = state.redis_client.pipeline()
        pipe.hincrby(key, "total", 1)
        if positive:
            pipe.hincrby(key, "positive", 1)
        else:
            pipe.hincrby(key, "negative", 1)
        await pipe.execute()
    except Exception as e:
        logger.warning(f"Expert score update failed: {e}")
    if query_embedding is not None:
        await routing_patterns.record("expert", f"{model}:{category}", query_embedding, positive)

# _extract_usage, _extract_json, _parse_expert_confidence,
# _parse_expert_gaps, _expert_category — see parsing.py
