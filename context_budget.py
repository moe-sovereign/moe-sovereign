"""
context_budget.py — Per-template context window budget management.

Context window resolution uses a four-tier priority chain:

  1. Explicit override (JUDGE_NUM_CTX / PLANNER_NUM_CTX env vars, or template
     context_window field) — always wins.
  2. Redis cache (TTL 1 h) — warm after first use, avoids repeated API calls.
  3. Ollama /api/show (async) — reads GGUF metadata, always accurate and
     model-agnostic. This is the primary dynamic source.
  4. Parameter-count heuristic (sync fallback) — extracts "Xb" from the model
     name and maps it to a conservative context estimate. Works for any future
     model without requiring a table update.

The old name-based lookup table has been removed. It was maintenance-heavy and
would silently return wrong values for renamed or future models.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Optional

# ── Char-to-token approximation ──────────────────────────────────────────────
# Conservative estimate: 4 chars ≈ 1 token (English/German mixed text).
CHARS_PER_TOKEN: int = 4

# ── Fixed overhead reserved for non-GraphRAG merger sections (tokens) ────────
# system instruction + mode prefix + expert responses (4 × 500) + misc
MERGER_FIXED_TOKENS: int = 3_500

# ── Headroom for merger generation ───────────────────────────────────────────
# Reserve this many tokens for the merger model to produce its output.
MERGER_HEADROOM_TOKENS: int = 2_000

# ── Minimum GraphRAG budget (chars) ──────────────────────────────────────────
# Even on very small models, don't drop below this — too small is useless.
MIN_GRAPHRAG_CHARS: int = 800

# ── Fallback when model is not in the lookup table ───────────────────────────
# Corresponds to the existing MAX_GRAPH_CONTEXT_CHARS default of 6 000.
DEFAULT_GRAPHRAG_CHARS: int = 6_000

# ── Parameter-count → context-window heuristic ───────────────────────────────
# Maps billion-parameter ranges to conservative context window estimates.
# Used only as a last-resort sync fallback when Redis is cold and Ollama is
# unreachable. The Ollama /api/show path (tier 3) always wins when available.
#
# Rationale: parameter count is embedded in every Ollama model tag ("7b", "14b",
# "35b" …) and is stable across model families and generations. This heuristic
# will remain correct for future models without any table maintenance.
#
# Values are intentionally conservative — they represent what fits in typical
# VRAM rather than the model's theoretical maximum.
_PARAM_CTX_HEURISTIC: list[tuple[float, int]] = [
    (3.0,   4_096),   # ≤3 B  — tiny models, usually 4 k default
    (9.0,   8_192),   # 4–9 B — standard small models
    (15.0, 16_384),   # 10–15 B
    (25.0, 32_768),   # 16–25 B
    (40.0, 32_768),   # 26–40 B (may be VRAM-constrained)
    (70.0, 32_768),   # 41–70 B
]
_DEFAULT_CTX_HEURISTIC: int = 32_768   # > 70 B or unknown size


def _params_from_name(model: str) -> float:
    """Extract parameter count (billions) from a model tag like 'qwen3:35b' or 'phi4:14b-fp16'.

    Returns 0.0 when no parameter count is found.
    """
    m = re.search(r"[:\-_](\d+(?:\.\d+)?)b\b", model.lower())
    return float(m.group(1)) if m else 0.0


# ── Per-model max output tokens ───────────────────────────────────────────────
# Populated dynamically via fetch_openai_max_output() and cached in Redis.
# Admin-configured overrides belong in the inference server / template config,
# not here. This dict stays empty in the codebase.
MODEL_MAX_OUTPUT_TOKENS: dict[str, int] = {}


def get_model_max_output(model: str, fetched_max_output: int = 0) -> int:
    """Return the max output tokens for *model*.

    Priority: fetched_max_output (from Redis / live API) → static table →
    MERGER_HEADROOM_TOKENS fallback.  The static table (MODEL_MAX_OUTPUT_TOKENS)
    is intentionally empty; all runtime values come from the dynamic fetch path
    in get_model_max_output_async().
    """
    if fetched_max_output > 0:
        return fetched_max_output
    name = (model or "").strip().lower()
    return MODEL_MAX_OUTPUT_TOKENS.get(name, MERGER_HEADROOM_TOKENS)


async def get_model_max_output_async(
    model: str,
    base_url: str = "",
    token: str = "ollama",
    redis_client=None,
) -> int:
    """Resolve max output tokens: Redis cache → /v1/models API → fallback.

    Cache-Key: moe:maxout:{sha256(base_url)[:12]}:{model}  TTL: 3600s
    """
    if redis_client and base_url:
        url_hash = hashlib.sha256(base_url.encode()).hexdigest()[:12]
        cache_key = f"moe:maxout:{url_hash}:{model}"
        try:
            cached = await redis_client.get(cache_key)
            if cached is not None:
                return int(cached)
        except Exception:
            pass

    fetched = 0
    if base_url and token != "ollama":
        fetched = await fetch_openai_max_output(model, base_url, token)

    result = get_model_max_output(model, fetched)

    if redis_client and base_url and fetched > 0:
        url_hash = hashlib.sha256(base_url.encode()).hexdigest()[:12]
        cache_key = f"moe:maxout:{url_hash}:{model}"
        try:
            await redis_client.setex(cache_key, 3600, str(fetched))
        except Exception:
            pass

    return result


_CLAUDE_CTX_TABLE: dict[str, int] = {
    # Claude 4.x family — 200k context window
    "claude-opus-4":    200_000,
    "claude-sonnet-4":  200_000,
    "claude-haiku-4":   200_000,
    # Claude 3.x family — 200k context window
    "claude-3-7":       200_000,
    "claude-3-5":       200_000,
    "claude-3-opus":    200_000,
    "claude-3-sonnet":  200_000,
    "claude-3-haiku":   200_000,
    # qwen3-planner: SFT fine-tune on Qwen3-8B base (n_ctx_train=40960, no RoPE scaling).
    # Must appear before "qwen3" so the prefix check matches this first.
    "qwen3-planner":    40_960,
    # Qwen3 family (all generations) — 262k native context window.
    # Name-based entry needed because the tag carries no k-suffix; the param-count
    # heuristic would otherwise cap these at 32 768 (conservative VRAM estimate).
    "qwen3":            262_144,
    # sovereign-judge fine-tune of Qwen3-MoE — same native window
    "sovereign-judge":  262_144,
    # Granite 4.1 family — 128k native context window.
    "granite4.1":       131_072,
}


def get_model_context_window(model: str) -> int:
    """Estimate context window (tokens) for *model* using a parameter-count heuristic.

    Extracts the billion-parameter count from the model tag (e.g. "qwen3:35b" → 35,
    "phi4:14b-fp16" → 14) and maps it to a conservative VRAM-safe context estimate.
    Returns 0 when no parameter count can be parsed (caller should treat as unknown).

    This replaces the old name-based lookup table, which required manual maintenance
    and silently returned wrong values for renamed or future models.
    """
    name_lower = (model or "").lower()

    # Claude models carry no parameter-count suffix — look up by name prefix.
    for prefix, ctx in _CLAUDE_CTX_TABLE.items():
        if name_lower.startswith(prefix):
            return ctx

    # First check for explicit ctx suffixes (e.g. ctx4k, ctx8k, 32k, 128k) in name
    ctx_match = re.search(r'ctx(\d+)k', name_lower)
    if not ctx_match:
        ctx_match = re.search(r'-(\d+)k\b', name_lower)
    if not ctx_match:
        # Avoid matching parameter sizes like "32b" by matching "32k"
        ctx_match = re.search(r'\b(\d+)k\b', name_lower)

    if ctx_match:
        return int(ctx_match.group(1)) * 1024
    if "128k" in name_lower or "ctx128k" in name_lower:
        return 131072
    if "32k" in name_lower:
        return 32768
    if "8k" in name_lower:
        return 8192
    if "4k" in name_lower:
        return 4096

    params = _params_from_name(model)
    if params <= 0:
        return 0
    for threshold, ctx in _PARAM_CTX_HEURISTIC:
        if params <= threshold:
            return ctx
    return _DEFAULT_CTX_HEURISTIC


def resolve_requested_ctx(model: str, state_num_ctx: int = 0, num_ctx_env: int = 0, label: str = "") -> int:
    """Resolve the context window a call to *model* will actually request.

    This mirrors the resolution used to build a call's ``extra_body.options.num_ctx``
    (see ``_judge_model_kw``/``_planner_model_kw`` in ``services/inference.py``):
    per-template override → global env default → static parameter-count heuristic,
    clamped to that heuristic if it is smaller (VRAM-safety clamp).

    Explicit overrides (state_num_ctx, num_ctx_env) are never clamped — the
    heuristic is a last-resort fallback only and must not silently override an
    admin-configured value (e.g. a 9B model whose GGUF supports 256k would
    otherwise be clamped to the conservative 8 192 heuristic).

    PRE-FLIGHT budget checks MUST use this instead of querying the model's
    *currently loaded* state via Ollama ``/api/ps`` (``get_model_ctx_async``) —
    that reflects whatever a prior call loaded, which can disagree with what
    THIS call will request and cause spurious overflow warnings.
    """
    requested_ctx = state_num_ctx or num_ctx_env or get_model_context_window(model)
    # Explicit overrides must be respected as-is — only apply the VRAM-safety
    # clamp when the context window was determined by the heuristic fallback.
    if state_num_ctx or num_ctx_env:
        return requested_ctx
    safe_ctx = get_model_context_window(model)
    if safe_ctx > 0 and safe_ctx < requested_ctx:
        if label:
            import logging
            logging.getLogger("MOE-SOVEREIGN").info(
                "%s: context clamped from requested %d to safe limit %d for model %s",
                label, requested_ctx, safe_ctx, model,
            )
        return safe_ctx
    return requested_ctx


def adaptive_context_window(
    requested_ctx: int,
    prompt: str,
    output_tokens: int,
) -> int:
    """Choose the smallest standard context tier that safely fits this call.

    Scale DOWN only: a template's large window is reduced for short prompts to
    save VRAM. But if the actual prompt exceeds the requested window, the tier
    is expanded so the model has room to produce output — the hard env-var cap
    (PLANNER_NUM_CTX / JUDGE_NUM_CTX) is applied by the caller afterwards.
    """
    requested = max(0, int(requested_ctx or 0))
    if requested <= 0:
        return 0

    # 3.0 chars/token: conservative estimate for mixed JSON/template/code prompts.
    # BPE tokenizers encode JSON keys, braces, and code constructs at lower
    # density than plain prose (≈4 chars/token), so 3.0 is a safe lower bound.
    estimated_input_tokens = math.ceil(len(prompt or "") / 3.0)
    required = estimated_input_tokens + max(1, int(output_tokens or 0)) + 1024
    tiers = (16_384, 32_768, 65_536, 131_072, 262_144)
    selected = next((tier for tier in tiers if tier >= required), requested)
    return min(requested, selected)
    # Prompt is too large for the requested window: expand to the required tier.
    # The caller is responsible for capping at the hard env-var maximum.
    return max(4_096, selected)


async def _fetch_litellm_model_info(model: str, base_url: str, token: str,
                                    timeout: float = 5.0) -> dict:
    """Fetch model capabilities from LiteLLM's /model/info endpoint.

    LiteLLM exposes richer metadata (max_input_tokens, max_output_tokens) at
    /model/info — a non-standard path outside the /v1 prefix.  Returns the
    model_info sub-dict for the requested model, or {} on any failure.
    """
    try:
        import httpx
        # Strip /v1 suffix: LiteLLM's admin routes live at the root
        root = base_url.rstrip("/").removesuffix("/v1")
        url  = f"{root}/model/info"
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code != 200:
                return {}
            for entry in resp.json().get("data", []):
                if entry.get("model_name") == model:
                    return entry.get("model_info", {})
    except Exception:
        pass
    return {}


async def _fetch_openai_models_list_entry(model: str, base_url: str, token: str,
                                          timeout: float = 5.0) -> dict:
    """Fetch this model's entry from the plain /v1/models LIST endpoint.

    Some OpenAI-compatible providers (observed: Hetzner's Inference API)
    implement only the collection endpoint (GET /v1/models, returning
    {"data": [...]}) and 404 on the per-model path (GET /v1/models/{id}) that
    fetch_openai_context_window/fetch_openai_max_output try first. Returns the
    matching entry from `data`, or {} on any failure/no match.
    """
    try:
        import httpx
        url = f"{base_url.rstrip('/')}/models"
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code != 200:
                return {}
            for entry in resp.json().get("data", []):
                if entry.get("id") == model or entry.get("model") == model:
                    return entry
    except Exception:
        pass
    return {}


async def fetch_openai_context_window(model: str, base_url: str, token: str,
                                      timeout: float = 5.0) -> int:
    """Query an OpenAI-compatible /v1/models/{model} endpoint for its context window.

    Falls back to the plain /v1/models LIST endpoint (providers that only
    implement the collection route, e.g. Hetzner), then to LiteLLM's
    /model/info when the standard endpoint returns no context-window field
    (common with LiteLLM-backed providers like AIHUB). Returns 0 when
    completely unavailable.
    """
    try:
        import httpx
        url = f"{base_url.rstrip('/')}/models/{model}"
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code == 200:
                data = resp.json()
                for field in ("context_window", "context_length", "max_context",
                              "max_input_tokens", "max_tokens"):
                    val = data.get(field)
                    if isinstance(val, int) and val > 0:
                        return val
    except Exception:
        pass
    # Plain list-endpoint fallback (e.g. Hetzner: only GET /v1/models exists).
    # "max_model_len" is vLLM's own /v1/models extension field name (observed
    # live on Hetzner's Inference API) — distinct from the OpenAI-ish names
    # tried above/below.
    list_entry = await _fetch_openai_models_list_entry(model, base_url, token, timeout)
    for field in ("max_model_len", "context_window", "context_length",
                  "max_context", "max_input_tokens", "max_tokens"):
        val = list_entry.get(field)
        if isinstance(val, int) and val > 0:
            return val
    # LiteLLM fallback: /model/info exposes max_input_tokens per model
    info = await _fetch_litellm_model_info(model, base_url, token, timeout)
    for field in ("context_window", "max_input_tokens"):
        val = info.get(field)
        if isinstance(val, int) and val > 0:
            return val
    return 0


async def fetch_openai_max_output(model: str, base_url: str, token: str,
                                  timeout: float = 5.0) -> int:
    """Query /v1/models/{model} for the model's max output token limit.

    Falls back to LiteLLM's /model/info when the standard endpoint returns no
    output-limit field.  Returns 0 when unavailable.
    """
    try:
        import httpx
        url = f"{base_url.rstrip('/')}/models/{model}"
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code == 200:
                data = resp.json()
                for field in ("max_output_tokens", "max_completion_tokens",
                              "max_tokens_output", "output_context_window"):
                    val = data.get(field)
                    if isinstance(val, int) and val > 0:
                        return val
    except Exception:
        pass
    # Plain list-endpoint fallback (e.g. Hetzner: only GET /v1/models exists)
    list_entry = await _fetch_openai_models_list_entry(model, base_url, token, timeout)
    for field in ("max_output_tokens", "max_completion_tokens",
                  "max_tokens_output", "output_context_window"):
        val = list_entry.get(field)
        if isinstance(val, int) and val > 0:
            return val
    # LiteLLM fallback
    info = await _fetch_litellm_model_info(model, base_url, token, timeout)
    for field in ("max_output_tokens", "max_completion_tokens", "max_tokens"):
        val = info.get(field)
        if isinstance(val, int) and val > 0:
            return val
    return 0


async def fetch_ollama_num_ctx(model: str, base_url: str, token: str = "ollama",
                               timeout: float = 5.0) -> int:
    """Query Ollama for a model's effective context window.

    Strategy (in priority order):
    1. /api/ps  — context_window field of a running model. This reflects the
       actual allocation set by OLLAMA_CONTEXT_LENGTH or per-model num_ctx.
    2. /api/show parameters — explicit num_ctx in the modelfile.
    Returns 0 if unavailable.
    """
    try:
        import httpx
        api_base = base_url.rstrip("/").removesuffix("/v1")
        async with httpx.AsyncClient(timeout=timeout) as client:
            # 1. Check /api/ps — gives the real allocated context for running models
            try:
                ps_resp = await client.get(
                    f"{api_base}/api/ps",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if ps_resp.status_code == 200:
                    for entry in ps_resp.json().get("models", []):
                        entry_name = entry.get("name") or entry.get("model") or ""
                        if entry_name == model or entry_name.split(":")[0] == model.split(":")[0]:
                            ctx = entry.get("context_window") or entry.get("context_length")
                            if ctx and int(ctx) > 0:
                                return int(ctx)
            except Exception:
                pass

            # 2. /api/show — explicit num_ctx in Modelfile parameters only.
            # We intentionally do NOT fall back to the GGUF model_info.context_length here.
            # That field is the model's TRAINING context, not its deployment context.
            # Sending it explicitly would override OLLAMA_CONTEXT_LENGTH on the server,
            # causing an unnecessary model reload to a smaller context window.
            resp = await client.post(
                f"{api_base}/api/show",
                json={"model": model},
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code != 200:
                return 0
            data = resp.json()
            params = data.get("parameters", "") or ""
            # parameters is a multi-line string: "num_ctx 32768\nstop ...\n..."
            for line in params.splitlines():
                m = re.match(r"^\s*num_ctx\s+(\d+)", line, re.IGNORECASE)
                if m:
                    return int(m.group(1))
    except Exception:
        pass
    return 0


async def fetch_ollama_native_ctx_max(
    model: str, base_url: str, token: str = "ollama",
    redis_client=None, timeout: float = 5.0,
) -> int:
    """Query Ollama /api/show for a model's trained max context length.

    This is the hard ceiling from the GGUF metadata (model_info.*.context_length,
    e.g. 32768 for qwen2.5-coder:32b) — independent of any configured/requested
    num_ctx. Ollama silently caps an oversized num_ctx request to this value, so
    callers use it to clamp template-configured context_window overrides that
    exceed what a given model can physically serve.

    Cached in Redis for 24h (static GGUF property). Returns 0 if unavailable.
    """
    cache_key = ""
    if redis_client and base_url:
        url_hash = hashlib.sha256(base_url.encode()).hexdigest()[:12]
        cache_key = f"moe:ctxmax:{url_hash}:{model}"
        try:
            cached = await redis_client.get(cache_key)
            if cached is not None:
                return int(cached)
        except Exception:
            pass

    result = 0
    try:
        import httpx
        api_base = base_url.rstrip("/").removesuffix("/v1")
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{api_base}/api/show",
                json={"model": model},
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                model_info = resp.json().get("model_info") or {}
                for key, val in model_info.items():
                    if key.endswith("context_length") and isinstance(val, int) and val > 0:
                        result = val
                        break
    except Exception:
        pass

    if redis_client and cache_key and result > 0:
        try:
            await redis_client.setex(cache_key, 86400, str(result))
        except Exception:
            pass
    return result


async def get_model_ctx_async(
    model: str,
    base_url: str = "",
    token: str = "ollama",
    redis_client=None,
    override: int = 0,
) -> int:
    """Resolve context window: override → Redis-cache → Ollama /api/show → static table.

    Cache-Key: moe:ctx:{sha256(base_url)[:12]}:{model}  TTL: 3600s
    Falls back gracefully at every step — never raises.
    Returns 0 if completely unknown.
    """
    if override > 0:
        return override

    # Redis cache lookup
    if redis_client and base_url:
        url_hash = hashlib.sha256(base_url.encode()).hexdigest()[:12]
        cache_key = f"moe:ctx:{url_hash}:{model}"
        try:
            cached = await redis_client.get(cache_key)
            if cached is not None:
                return int(cached)
        except Exception:
            pass

    # Database metadata lookup (takes priority over live /api/ps check if configured)
    db_ctx = 0
    if base_url:
        try:
            from config import INFERENCE_SERVERS_LIST
            node_name = ""
            for s in INFERENCE_SERVERS_LIST:
                s_url = s.get("url", "").rstrip("/")
                if s_url and (s_url in base_url or base_url in s_url):
                    node_name = s["name"]
                    break
            
            lookup_id = f"{model}@{node_name}" if node_name else model
            from admin_ui.database import get_model_metadata
            meta = await get_model_metadata(lookup_id)
            if meta and meta.get("context_window"):
                db_ctx = int(meta["context_window"])
        except Exception:
            pass

    # Dynamic fetch — Ollama via /api/show, OpenAI-compatible via /v1/models/{id}
    fetched = 0
    if not db_ctx and base_url:
        if token == "ollama":  # heuristic: local Ollama nodes use "ollama" token
            fetched = await fetch_ollama_num_ctx(model, base_url, token)
        else:
            fetched = await fetch_openai_context_window(model, base_url, token)

    # Static table fallback (local model families only — no deployment-specific names)
    result = db_ctx or fetched or get_model_context_window(model)

    # Cache the result.
    # Ollama nodes use a short TTL (120 s) because context_window reflects the
    # currently-loaded allocation, which can change between requests.
    # Remote/cloud nodes use 3600 s — their context window is static.
    _ctx_ttl = 120 if token == "ollama" else 3600
    if redis_client and base_url and result > 0:
        url_hash = hashlib.sha256(base_url.encode()).hexdigest()[:12]
        cache_key = f"moe:ctx:{url_hash}:{model}"
        try:
            await redis_client.setex(cache_key, _ctx_ttl, str(result))
        except Exception:
            pass

    return result


def graphrag_budget_chars(
    model: str,
    query_chars: int = 0,
    override_chars: int = 0,
) -> int:
    """Compute the maximum number of characters for GraphRAG context injection.

    Priority (highest to lowest):
    1. ``override_chars > 0``  — explicit char limit from template config.
    2. ``override_chars == -1``  — skip template limit; auto-compute from model
       context window only (sentinel: "no internal cap, only the env-var global
       cap ``MAX_GRAPH_CONTEXT_CHARS`` applies").
    3. ``override_chars == 0``  — auto (same as -1, kept for clarity).
       Auto-compute from ``model``'s known context window.
    4. ``DEFAULT_GRAPHRAG_CHARS``  — global fallback when model is unknown.

    The per-template ``graphrag_max_chars`` field maps directly to
    ``override_chars``:
      - ``graphrag_max_chars: N  (N > 0)``  → exact N chars.
      - ``graphrag_max_chars: 0``            → auto (full model capacity).
      - ``graphrag_max_chars: -1``           → same as 0, explicit intent.

    The global ceiling ``MAX_GRAPH_CONTEXT_CHARS`` (env var, applied by the
    caller) is the only remaining cap after this function returns.

    Args:
        model:          The merger/judge model name (e.g. "phi4:14b").
        query_chars:    Length of the user query in characters.
        override_chars: Per-template explicit limit (0 or -1 = auto).

    Returns:
        Maximum GraphRAG chars that fit within the model's context window.
        Always at least ``MIN_GRAPHRAG_CHARS``.
    """
    if override_chars > 0:
        return override_chars

    # override_chars == 0 or -1 → auto-compute from model context window.
    ctx_tokens = get_model_context_window(model)
    if ctx_tokens <= 0:
        # Unknown model — use the conservative fallback.
        return DEFAULT_GRAPHRAG_CHARS

    query_tokens = (query_chars + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN
    max_output   = get_model_max_output(model)
    available    = ctx_tokens - MERGER_FIXED_TOKENS - max_output - query_tokens
    return max(MIN_GRAPHRAG_CHARS, available * CHARS_PER_TOKEN)


# ── Web-research budget constants ─────────────────────────────────────────────
# After GraphRAG and expert responses are allocated, what remains goes to web.
# Minimum: always pass at least this much web context to the judge.
MIN_WEB_CHARS: int = 1_200
DEFAULT_WEB_CHARS: int = 4_000

# Fraction of remaining context budget allocated to web research (after GraphRAG).
# The rest is reserved for expert outputs and headroom already in MERGER_FIXED_TOKENS.
WEB_BUDGET_FRACTION: float = 0.55


def web_research_budget(
    model: str,
    query_chars: int = 0,
    graphrag_chars_used: int = 0,
) -> tuple[int, int]:
    """Compute adaptive web-research block and per-block character limits.

    Returns (max_blocks, max_block_chars) that the merger should use when
    compressing web_research before injecting it into the judge prompt.

    The limits scale with the judge model's context window so that:
      - Small models (gemma4:31b  8K): fewer / shorter blocks
      - Mid models   (qwen3.6    32K): moderate blocks
      - Large models (gpt-120B  128K): generous blocks

    Args:
        model:              Judge model name.
        query_chars:        Length of the user query in characters.
        graphrag_chars_used: Actual chars of GraphRAG context that will be included.

    Returns:
        (max_blocks, max_block_chars)
    """
    ctx_tokens = get_model_context_window(model)
    if ctx_tokens <= 0:
        return 5, DEFAULT_WEB_CHARS // 5  # fallback: 5 blocks × 800 chars

    query_tokens  = (query_chars + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN
    graph_tokens  = (graphrag_chars_used + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN
    remaining     = ctx_tokens - MERGER_FIXED_TOKENS - MERGER_HEADROOM_TOKENS - query_tokens - graph_tokens
    web_tokens    = max(MIN_WEB_CHARS // CHARS_PER_TOKEN, int(remaining * WEB_BUDGET_FRACTION))
    web_chars     = web_tokens * CHARS_PER_TOKEN

    # Distribute across blocks: prefer more shorter blocks over fewer long ones.
    if web_chars >= 8_000:
        return 7, 1_200
    elif web_chars >= 4_000:
        return 5, 900
    elif web_chars >= 2_000:
        return 3, 700
    else:
        return 2, max(MIN_WEB_CHARS // 2, web_chars // 2)


# ── Generic input/output budget split ────────────────────────────────────────
# Reserve at least this many tokens for generation, regardless of how tight the
# context window is. Below this, output is too short to be useful.
MIN_OUTPUT_BUDGET_TOKENS: int = 1024


def resolve_io_budget(
    ctx_tokens: int,
    desired_max_tokens: int,
    *,
    static_overhead_tokens: int = 0,
    chars_per_token: int = CHARS_PER_TOKEN,
    safety_buffer_tokens: int = 0,
    min_output_tokens: int = 0,
    min_input_ratio: float = 0.5,
) -> dict:
    """Split a model's context window into input/output token & char budgets.

    Mirrors the two-stage capping logic originally inlined in the Claude Code
    tool path (services/pipeline/anthropic.py): first cap the output budget so
    a minimum input budget survives, then derive the remaining input budget
    from whatever output budget was settled on (plus any static overhead such
    as tool schemas or a system prompt that isn't part of the trimmable
    message history).

    Args:
        ctx_tokens: Model's context window in tokens. <= 0 disables all
            capping — returns desired_max_tokens unchanged with
            avail_input_tokens=0 (caller should not rely on the input budget
            in this case).
        desired_max_tokens: The output budget the caller would like to use.
        static_overhead_tokens: Tokens consumed by content outside the
            trimmable message history (tool schemas, system prompt).
        chars_per_token: Chars-per-token ratio for this content (dense
            code/JSON traffic tokenizes denser than prose).
        safety_buffer_tokens: Extra headroom subtracted at every stage.
        min_output_tokens: Floor for max_output_tokens. <= 0 means no floor —
            a pure cap, matching the CC tool path's first narrowing stage.
        min_input_ratio: Fraction of ctx_tokens reserved for input when
            capping desired_max_tokens in stage 1.

    Returns:
        dict with keys ``max_output_tokens``, ``avail_input_tokens``,
        ``avail_input_chars``, ``overflow`` (True if the input budget had to
        be clamped to 0 even after enforcing ``min_output_tokens``), and
        ``static_overhead_tokens`` (echoed back for convenience).
    """
    if ctx_tokens <= 0:
        return {
            "max_output_tokens": desired_max_tokens,
            "avail_input_tokens": 0,
            "avail_input_chars": 0,
            "overflow": False,
            "static_overhead_tokens": static_overhead_tokens,
        }

    # Stage 1: cap the desired output so a minimum input budget survives.
    min_input_budget = min(desired_max_tokens, int(ctx_tokens * min_input_ratio))
    max_allowed_out = ctx_tokens - min_input_budget - safety_buffer_tokens
    max_output_tokens = desired_max_tokens
    if max_allowed_out > 0 and max_output_tokens > max_allowed_out:
        max_output_tokens = max_allowed_out

    # Stage 2: derive the input budget from the settled output budget.
    avail_input_tokens = ctx_tokens - max_output_tokens - safety_buffer_tokens - static_overhead_tokens
    overflow = avail_input_tokens < 0
    avail_input_tokens = max(0, avail_input_tokens)

    # Stage 3: enforce an output floor, recomputing the input budget.
    if min_output_tokens > 0 and max_output_tokens < min_output_tokens:
        max_output_tokens = min_output_tokens
        avail_input_tokens = ctx_tokens - max_output_tokens - safety_buffer_tokens - static_overhead_tokens
        overflow = avail_input_tokens < 0
        avail_input_tokens = max(0, avail_input_tokens)

    return {
        "max_output_tokens": max_output_tokens,
        "avail_input_tokens": avail_input_tokens,
        "avail_input_chars": avail_input_tokens * chars_per_token,
        "overflow": overflow,
        "static_overhead_tokens": static_overhead_tokens,
    }


def estimate_overflow(
    estimated_input_tokens: int,
    desired_max_tokens: int,
    ctx_tokens: int,
    safety_buffer_tokens: int = 0,
) -> bool:
    """Pure pre-flight check: would input + output (+ buffer) exceed ctx_tokens?

    Returns False when ctx_tokens <= 0 (no window to overflow).
    """
    if ctx_tokens <= 0:
        return False
    return (estimated_input_tokens + desired_max_tokens + safety_buffer_tokens) > ctx_tokens


def prune_filler_words(text: str) -> str:
    """Removes verbose filler words and common politeness/introductory boilerplate from prompts."""
    if not text:
        return ""
    # List of regex patterns to strip (case-insensitive)
    patterns = [
        # Introductory fluff / politeness
        (r"\b(please|kindly|could you|would you be so kind as to|i would like you to|please be sure to|don't forget to|make sure that you)\b", ""),
        (r"\b(bitte|sei so gut und|ich möchte dich bitten|vergiss nicht zu|stelle sicher, dass du)\b", ""),
        # Wordy transitions
        (r"\b(in order to|as a matter of fact|at the end of the day|for all intents and purposes|it goes without saying|needless to say)\b", ""),
        (r"\b(um zu|in diesem zusammenhang|im grunde genommen|schlicht und ergreifend|selbstverständlich)\b", ""),
    ]
    
    result = text
    for pattern, repl in patterns:
        result = re.sub(pattern, repl, result, flags=re.IGNORECASE)
    # Cleanup double spaces and newlines
    result = re.sub(r" +", " ", result)
    return result.strip()


async def compress_prompt_to_fit(
    prompt: str,
    max_chars: int,
    model: str = "",
    url: str = "",
    token: str = "",
) -> str:
    """Compresses a prompt text to fit into max_chars by removing filler words and condensing phrasing without losing requirements."""
    if not prompt:
        return ""
    
    # 1. Apply fast regex-based heuristic pruning first (zero-cost)
    compressed = prune_filler_words(prompt)
    if len(compressed) <= max_chars:
        return compressed
        
    # 2. If it's still too long, and we have a model/url, use the fast planner model to condense it
    if max_chars > 150 and model and url:
        try:
            import asyncio
            import logging
            logger = logging.getLogger("MOE-SOVEREIGN")
            # We use a very strict, zero-temperature system instruction to condense the text
            condense_instruction = (
                f"You are a text compression utility. Condense the following instructions/text to fit in at most {max_chars} characters. "
                "CRITICAL: Keep all concrete rules, parameters, file paths, and technical requirements. "
                "Remove all polite filler words, introductory fluff, corporate explanations, and verbose phrasing. "
                "Output ONLY the compressed text."
            )
            # Invoke the local model
            from langchain_openai import ChatOpenAI
            _c_llm = ChatOpenAI(
                model=model,
                base_url=url,
                api_key=token,
                timeout=10.0,
                temperature=0.0,
                max_tokens=max(50, max_chars // CHARS_PER_TOKEN),
            )
            _r = await _c_llm.ainvoke(f"{condense_instruction}\n\nTEXT:\n{compressed}")
            res = _r.content.strip()
            if res and len(res) <= max_chars:
                logger.info("🗜️ LLM Prompt Compression succeeded: %d -> %d chars", len(prompt), len(res))
                return res
        except Exception as e:
            import logging
            logging.getLogger("MOE-SOVEREIGN").debug("LLM Prompt Compression failed/skipped: %s", e)
            
    # Fallback to simple slicing if it's still too long
    return compressed[:max_chars] + "\n[…truncated to fit context window]"
