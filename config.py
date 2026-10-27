"""
config.py — Application configuration from environment variables.

All os.getenv() calls that were previously scattered through main.py live here.
Imports: only stdlib (os, json). No logging, no heavy deps — this module is
imported before logging is configured in main.py.
"""

import json
import os

# =============================================================================
# Runtime flags
# =============================================================================

CORRECTION_MEMORY_ENABLED = os.getenv("CORRECTION_MEMORY_ENABLED", "true").lower() in ("1", "true", "yes")
_EDGE_MODE = os.getenv("ENVIRONMENT") == "edge_mobile"
LOG_LEVEL  = os.getenv("LOG_LEVEL", "INFO").upper()

# =============================================================================
# Kafka
# =============================================================================

KAFKA_BOOTSTRAP      = os.getenv("KAFKA_URL", "kafka://moe-kafka:9092").replace("kafka://", "")
KAFKA_TOPIC_INGEST   = "moe.ingest"
KAFKA_TOPIC_REQUESTS = "moe.requests"
KAFKA_TOPIC_FEEDBACK = "moe.feedback"
KAFKA_TOPIC_LINTING  = "moe.linting"
KAFKA_TOPIC_AUDIT      = "moe.audit"
KAFKA_TOPIC_STUCK      = "moe.stuck"
KAFKA_TOPIC_DECISIONS  = "moe.decisions"

# =============================================================================
# Database & cache
# =============================================================================

REDIS_URL               = os.getenv("REDIS_URL", "redis://terra_cache:6379")
POSTGRES_CHECKPOINT_URL = os.getenv("POSTGRES_CHECKPOINT_URL", "")
MOE_USERDB_URL          = os.getenv(
    "MOE_USERDB_URL",
    "postgresql://moe_admin@terra_checkpoints:5432/moe_userdb",
)

# =============================================================================
# Enterprise Data Stack (optional, default OFF)
# =============================================================================

_ENTERPRISE_ENABLED = os.getenv("INSTALL_ENTERPRISE_DATA_STACK", "false").lower() == "true"
NIFI_URL            = os.getenv("NIFI_URL", "")
MARQUEZ_URL         = os.getenv("MARQUEZ_URL", "")
LAKEFS_ENDPOINT     = os.getenv("LAKEFS_ENDPOINT", "")

# =============================================================================
# OIDC / Authentik
# =============================================================================

AUTHENTIK_URL = os.getenv("AUTHENTIK_URL", "")
OIDC_JWKS_URL = os.getenv(
    "OIDC_JWKS_URL",
    f"{AUTHENTIK_URL}/application/o/moe-sovereign/jwks/" if AUTHENTIK_URL else "",
)
OIDC_ISSUER   = os.getenv(
    "OIDC_ISSUER",
    f"{AUTHENTIK_URL}/application/o/moe-sovereign/" if AUTHENTIK_URL else "",
)
OIDC_CLIENT_ID = os.getenv("OIDC_CLIENT_ID", "moe-infra")
OIDC_ENABLED   = bool(AUTHENTIK_URL)

# =============================================================================
# Neo4j
# =============================================================================

NEO4J_URI  = os.getenv("NEO4J_URI",  "bolt://neo4j-knowledge:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASS")

# =============================================================================
# Inference servers
# =============================================================================

_INF_SERVERS_RAW = os.getenv("INFERENCE_SERVERS", "")
try:
    INFERENCE_SERVERS_LIST: list = json.loads(_INF_SERVERS_RAW) if _INF_SERVERS_RAW.strip() else []
except json.JSONDecodeError:
    INFERENCE_SERVERS_LIST = []  # invalid JSON — logged in main.py after logger is ready

INFERENCE_SERVERS_LIST = [s for s in INFERENCE_SERVERS_LIST if s.get("enabled", True)]
URL_MAP      = {s["name"]: s["url"]                 for s in INFERENCE_SERVERS_LIST if s.get("url")}
TOKEN_MAP    = {s["name"]: s.get("token", "ollama")  for s in INFERENCE_SERVERS_LIST}
API_TYPE_MAP = {s["name"]: s.get("api_type", "ollama") for s in INFERENCE_SERVERS_LIST}
TIMEOUT_MAP  = {s["name"]: s.get("timeout", 300)     for s in INFERENCE_SERVERS_LIST}


# "N requests per <amount> <unit>" — the unit and its multiplier are both
# configurable (e.g. 60/minute, 4/second, 1000/hour, or 200 per 5 minutes).
RATE_LIMIT_UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3600}


def rate_limit_to_rps(max_requests, period_amount=1, period_unit="second", burst=None):
    """(requests_per_second, burst) from a max_requests/period_amount/period_unit
    triple, or None when max_requests is unset/invalid. `burst` is an optional
    advanced override; it defaults to max_requests (the full quota may fire at
    once, then refills smoothly — standard "N per period" semantics)."""
    try:
        requests = float(max_requests)
    except (TypeError, ValueError):
        return None
    if requests <= 0:
        return None
    try:
        amount = float(period_amount) if period_amount else 1.0
    except (TypeError, ValueError):
        amount = 1.0
    if amount <= 0:
        amount = 1.0
    unit_seconds = RATE_LIMIT_UNIT_SECONDS.get(period_unit, 1)
    rps = requests / (amount * unit_seconds)
    try:
        burst_val = float(burst) if burst else requests
    except (TypeError, ValueError):
        burst_val = requests
    return (rps, max(1.0, burst_val))


def rate_limits_to_list(limits):
    """[(rps, burst), ...] from a list of {requests|max_requests, period_amount,
    period_unit, burst} dicts, or None when empty/all-invalid. Multiple entries
    all apply simultaneously — e.g. OpenRouter free models: 20/minute AND
    1000/24h — see services.rate_limiter._TokenBucketGroup."""
    if not isinstance(limits, list):
        return None
    result = []
    for entry in limits:
        if not isinstance(entry, dict):
            continue
        parsed = rate_limit_to_rps(
            entry.get("requests", entry.get("max_requests")),
            entry.get("period_amount", 1),
            entry.get("period_unit", "second"),
            entry.get("burst"),
        )
        if parsed is not None:
            result.append(parsed)
    return result or None


def _parse_rate_limit(s: dict):
    """[(rps, burst), ...] for one INFERENCE_SERVERS entry, or None when
    unconfigured/invalid. "rate_limit_limits" (a list — see rate_limits_to_list)
    takes precedence when present; otherwise falls back to the single
    rate_limit_requests/rate_limit_period_amount/rate_limit_period_unit fields."""
    if isinstance(s.get("rate_limit_limits"), list):
        return rate_limits_to_list(s["rate_limit_limits"])
    if not s.get("rate_limit_requests"):
        return None
    single = rate_limit_to_rps(
        s.get("rate_limit_requests"),
        s.get("rate_limit_period_amount", 1),
        s.get("rate_limit_period_unit", "second"),
        s.get("rate_limit_burst"),
    )
    return [single] if single is not None else None


# Self-imposed outbound throttling toward a provider: {name: [(requests_per_second, burst), ...]}.
# Proactively paces requests below the provider's own rate limit instead of
# reacting to 429s after the fact — see services/rate_limiter.py. An endpoint
# absent from this map is unlimited (opt-in via admin UI → Inference Servers).
RATE_LIMITS = {
    s["name"]: _parse_rate_limit(s)
    for s in INFERENCE_SERVERS_LIST
    if _parse_rate_limit(s) is not None
}

JUDGE_ENDPOINT_NAME = os.getenv("JUDGE_ENDPOINT", "")
JUDGE_URL           = URL_MAP.get(JUDGE_ENDPOINT_NAME) if JUDGE_ENDPOINT_NAME else None
JUDGE_TOKEN         = TOKEN_MAP.get(JUDGE_ENDPOINT_NAME, "ollama") if JUDGE_ENDPOINT_NAME else "ollama"
JUDGE_MODEL         = os.getenv("JUDGE_MODEL", "")

# Global fallback for the Llama-Guard pre-filter (guard_node, graph/router_nodes.py),
# overridable per-template via guardrail_model_override etc. (services/routing.py).
GUARD_ENDPOINT_NAME = os.getenv("GUARD_ENDPOINT", "")
GUARD_URL           = URL_MAP.get(GUARD_ENDPOINT_NAME) if GUARD_ENDPOINT_NAME else None
GUARD_TOKEN         = TOKEN_MAP.get(GUARD_ENDPOINT_NAME, "ollama") if GUARD_ENDPOINT_NAME else "ollama"
GUARD_MODEL         = os.getenv("GUARD_MODEL", "")
# The guard shares finite Ollama capacity with planner/expert models. By default
# it may therefore run only when already resident; a cold 8B model swap must not
# consume most of the orchestration budget. Operators with dedicated guard
# capacity can set GUARD_WARM_ONLY=false.
GUARD_WARM_ONLY     = os.getenv("GUARD_WARM_ONLY", "true").lower() in ("1", "true", "yes")
GUARD_PROBE_TIMEOUT = float(os.getenv("GUARD_PROBE_TIMEOUT", "2"))
GUARD_TIMEOUT       = float(os.getenv("GUARD_TIMEOUT", "15"))
GUARD_KEEP_ALIVE    = os.getenv("GUARD_KEEP_ALIVE", "30m")
# Llama Guard classifies a single exchange (safe/unsafe + category) — it never
# needs a large context window. Left at the endpoint's OLLAMA_CONTEXT_LENGTH
# default (32768 here), the KV cache alone made the resident model ~12GB,
# large enough to contend with Judge/Planner/Expert models sharing the same
# endpoint (observed live: 422s during a benchmark run right after guard
# pre-warming was added). 4096 comfortably covers policy_context + user_input.
GUARD_NUM_CTX       = int(os.getenv("GUARD_NUM_CTX", "4096"))

GRAPH_INGEST_MODEL    = os.getenv("GRAPH_INGEST_MODEL", "")
_GRAPH_INGEST_EP_NAME = os.getenv("GRAPH_INGEST_ENDPOINT", "")
GRAPH_INGEST_URL      = URL_MAP.get(_GRAPH_INGEST_EP_NAME) if _GRAPH_INGEST_EP_NAME else None
GRAPH_INGEST_TOKEN    = TOKEN_MAP.get(_GRAPH_INGEST_EP_NAME, "ollama") if _GRAPH_INGEST_EP_NAME else "ollama"

PLANNER_MODEL    = os.getenv("PLANNER_MODEL", "")
PLANNER_ENDPOINT = os.getenv("PLANNER_ENDPOINT", os.getenv("JUDGE_ENDPOINT", ""))
PLANNER_URL      = URL_MAP.get(PLANNER_ENDPOINT) if PLANNER_ENDPOINT else None
PLANNER_TOKEN    = TOKEN_MAP.get(PLANNER_ENDPOINT, "ollama") if PLANNER_ENDPOINT else "ollama"

# ── Planner Execution Mode (llm, slm_local, hybrid) ──────────────────────────
PLANNER_MODE            = os.getenv("PLANNER_MODE", "llm").lower()
PLANNER_LOCAL_GGUF_PATH = os.getenv("PLANNER_LOCAL_GGUF_PATH", "")
PLANNER_LOCAL_THREADS   = int(os.getenv("PLANNER_LOCAL_THREADS", "4"))


_JUDGE_BASE   = (JUDGE_URL   or "").rstrip("/").removesuffix("/v1")
_PLANNER_BASE = (PLANNER_URL or "").rstrip("/").removesuffix("/v1")

EXPERTS             = json.loads(os.getenv("EXPERT_MODELS", "{}"))
MCP_URL             = os.getenv("MCP_URL", "http://mcp-precision:8003")
PRECISION_DIRECT_RESPONSE_ENABLED = os.getenv(
    "PRECISION_DIRECT_RESPONSE_ENABLED", "false"
).lower() in ("1", "true", "yes")
PRECISION_CACHE_POLICY = os.getenv("PRECISION_CACHE_POLICY", "bypass").strip().lower()
if PRECISION_CACHE_POLICY not in {"bypass", "typed"}:
    PRECISION_CACHE_POLICY = "bypass"
PRECISION_CONTRACT_MODE = os.getenv("PRECISION_CONTRACT_MODE", "enforce").strip().lower()
if PRECISION_CONTRACT_MODE not in {"shadow", "enforce"}:
    PRECISION_CONTRACT_MODE = "shadow"
MCP_STRUCTURED_RESULT_REQUIRED = os.getenv(
    "MCP_STRUCTURED_RESULT_REQUIRED", "true"
).strip().lower() in {"1", "true", "yes"}
GRAPH_VIA_MCP       = os.getenv("GRAPH_VIA_MCP", "false").lower() in ("1", "true", "yes")
MAX_GRAPH_CONTEXT_CHARS: int = int(os.getenv("MAX_GRAPH_CONTEXT_CHARS", "6000"))

# Text-to-Cypher fallback: when term-matching returns no entities, a small LLM
# generates a targeted Cypher query from natural language.
# Requires GRAPH_INGEST_ENDPOINT + GRAPH_INGEST_MODEL to be configured.
GRAPHRAG_T2C_ENABLED:   bool  = os.getenv("GRAPHRAG_T2C_ENABLED", "1") not in ("0", "false", "no")
GRAPHRAG_T2C_TIMEOUT:   float = float(os.getenv("GRAPHRAG_T2C_TIMEOUT", "8.0"))
GRAPHRAG_T2C_MAX_NODES: int   = int(os.getenv("GRAPHRAG_T2C_MAX_NODES", "8"))

# Query reformulation: when term-matching returns nothing, ask a small LLM to
# rephrase the query (shorter terms, English equivalent, synonyms) and retry
# term-matching once before falling back to Text-to-Cypher.
GRAPHRAG_REFORMULATE_ENABLED: bool  = os.getenv("GRAPHRAG_REFORMULATE_ENABLED", "1") not in ("0", "false", "no")
GRAPHRAG_REFORMULATE_TIMEOUT: float = float(os.getenv("GRAPHRAG_REFORMULATE_TIMEOUT", "5.0"))
LITELLM_URL         = os.getenv("LITELLM_URL", "").rstrip("/")

# =============================================================================
# Shadow/benchmark mode
# =============================================================================

BENCHMARK_SHADOW_TEMPLATE: str = os.getenv("BENCHMARK_SHADOW_TEMPLATE", "")
BENCHMARK_SHADOW_RATE: int     = int(os.getenv("BENCHMARK_SHADOW_RATE", "20"))

# =============================================================================
# Claude Code integration
# =============================================================================

_DEFAULT_CLAUDE_CODE_MODELS = (
    "claude-opus-4-6,claude-sonnet-4-6,claude-haiku-4-5-20251001,"
    "claude-opus-4-5,claude-sonnet-4-5,claude-3-5-sonnet-20241022,"
    "claude-3-5-haiku-20241022,claude-3-7-sonnet-20250219"
)
CLAUDE_CODE_MODELS = {
    m.strip()
    for m in os.getenv("CLAUDE_CODE_MODELS", _DEFAULT_CLAUDE_CODE_MODELS).split(",")
    if m.strip()
}
CLAUDE_CODE_TOOL_MODEL         = os.getenv("CLAUDE_CODE_TOOL_MODEL", "").strip().rstrip("*")
CLAUDE_CODE_TOOL_ENDPOINT      = os.getenv("CLAUDE_CODE_TOOL_ENDPOINT", os.getenv("JUDGE_ENDPOINT", ""))
CLAUDE_CODE_MODE               = os.getenv("CLAUDE_CODE_MODE", "moe_orchestrated")
CLAUDE_CODE_REASONING_MODEL    = os.getenv("CLAUDE_CODE_REASONING_MODEL", "")
CLAUDE_CODE_REASONING_ENDPOINT = os.getenv("CLAUDE_CODE_REASONING_ENDPOINT", "")
CLAUDE_CODE_TOOL_CHOICE        = os.getenv("CLAUDE_CODE_TOOL_CHOICE", "auto")

_CLAUDE_CODE_TOOL_URL      = URL_MAP.get(CLAUDE_CODE_TOOL_ENDPOINT) or JUDGE_URL
_CLAUDE_CODE_TOOL_TOKEN    = TOKEN_MAP.get(CLAUDE_CODE_TOOL_ENDPOINT, "ollama")
_CLAUDE_CODE_REASONING_URL = (
    URL_MAP.get(CLAUDE_CODE_REASONING_ENDPOINT) if CLAUDE_CODE_REASONING_ENDPOINT else None
)

# =============================================================================
# Expert-routing & cache thresholds
# =============================================================================

MAX_EXPERT_OUTPUT_CHARS   = int(os.getenv("MAX_EXPERT_OUTPUT_CHARS",    "2400"))
# Hard generation limit for expert LLM calls. Prevents thinking-mode models
# (qwen3.6:35b) from generating massive token traces (32k+ tokens) that block
# the pipeline for 10+ minutes. Thinking traces are stripped before use —
# 4096 tokens captures ample reasoning + answer for any expert category.
MAX_EXPERT_TOKENS         = int(os.getenv("MAX_EXPERT_TOKENS",          "4096"))
# Per-category token limits override the global MAX_EXPERT_TOKENS.
# Code generation needs much higher limits than factual lookups.
MAX_EXPERT_TOKENS_CODE    = int(os.getenv("MAX_EXPERT_TOKENS_CODE",     "16384"))
MAX_EXPERT_OUTPUT_CHARS_CODE = int(os.getenv("MAX_EXPERT_OUTPUT_CHARS_CODE", "48000"))
# Judge generation limit — prevents thinking-mode judges from generating
# massive traces. 8192 is generous for synthesis while still bounding cost.
MAX_JUDGE_TOKENS          = int(os.getenv("MAX_JUDGE_TOKENS",           "32768"))
MAX_PLANNER_TOKENS        = int(os.getenv("MAX_PLANNER_TOKENS",         "16384"))
# Ollama num_ctx for judge and planner — 0 means auto-detect from static model table.
# Set explicitly when the auto-detected value differs from Ollama's actual allocation.
JUDGE_NUM_CTX   = int(os.getenv("JUDGE_NUM_CTX",   "262144"))
PLANNER_NUM_CTX = int(os.getenv("PLANNER_NUM_CTX", "0"))
# ── Agentic loop token budgets ────────────────────────────────────────────────
# Gap detection and working-memory extraction are skipped when accumulated token
# usage exceeds these thresholds. Default matches practical qwen3.6:35b limits;
# raise when using a model+node with larger context windows.
AGENTIC_GAP_THRESHOLD_TOKENS    = int(os.getenv("AGENTIC_GAP_THRESHOLD_TOKENS",    "200000"))
WM_EXTRACT_THRESHOLD_TOKENS     = int(os.getenv("WM_EXTRACT_THRESHOLD_TOKENS",     "220000"))

# ── Chain-of-Thought trigger thresholds ───────────────────────────────────────
# CoT reasoning is activated when the plan has at least this many distinct
# categories or sequential tasks. Lower = more CoT, higher = less CoT.
COT_MIN_CATEGORIES  = int(os.getenv("COT_MIN_CATEGORIES", "2"))
COT_MIN_TASKS       = int(os.getenv("COT_MIN_TASKS",      "2"))

# ── Expert context-window allocation ratios ───────────────────────────────────
# Output cap = min(MAX_EXPERT_OUTPUT, max(EXPERT_INPUT_MIN_CHARS, ctx // EXPERT_OUTPUT_DIVISOR))
# Input max  = max(EXPERT_INPUT_MIN_CHARS, ctx * EXPERT_CHARS_PER_TOKEN)
EXPERT_OUTPUT_DIVISOR   = int(os.getenv("EXPERT_OUTPUT_DIVISOR",   "4"))    # reserve 25% for output
EXPERT_INPUT_MIN_CHARS  = int(os.getenv("EXPERT_INPUT_MIN_CHARS",  "2000")) # floor for very small ctx
EXPERT_CHARS_PER_TOKEN  = int(os.getenv("EXPERT_CHARS_PER_TOKEN",  "3"))    # conservative mixed estimate

# ── CC pipeline safety buffer ─────────────────────────────────────────────────
# Token headroom reserved for message framing overhead in Claude Code tool calls.
CC_SAFETY_BUFFER_TOKENS = int(os.getenv("CC_SAFETY_BUFFER_TOKENS", "1000"))

CACHE_HIT_THRESHOLD       = float(os.getenv("CACHE_HIT_THRESHOLD",      "0.15"))
SOFT_CACHE_THRESHOLD      = float(os.getenv("SOFT_CACHE_THRESHOLD",     "0.50"))
SOFT_CACHE_MAX_EXAMPLES   = int(os.getenv("SOFT_CACHE_MAX_EXAMPLES",    "2"))
ROUTE_THRESHOLD           = float(os.getenv("ROUTE_THRESHOLD",          "0.18"))
ROUTE_GAP                 = float(os.getenv("ROUTE_GAP",                "0.10"))
CACHE_MIN_RESPONSE_LEN    = int(os.getenv("CACHE_MIN_RESPONSE_LEN",     "150"))

# Knowledge-bypass: skip the LLM pipeline for a query that is *similar* (but not
# an exact L1 hit) to a prior answer, IF that prior answer was high-confidence
# and is still fresh. Conservative by construction — the threshold stays above
# CACHE_HIT_THRESHOLD and below SOFT_CACHE_THRESHOLD, and both the confidence
# floor and TTL must hold. This is the lever for "less LLM over time": accumulated
# high-trust knowledge answers progressively more queries without inference.
KNOWLEDGE_BYPASS_ENABLED   = os.getenv("KNOWLEDGE_BYPASS_ENABLED", "true").lower() in ("1", "true", "yes")

# ── Judicial Mixture of Experts (J-MoE) ───────────────────────────────────────
JMOE_DEBATE_ENABLED = os.getenv("JMOE_DEBATE_ENABLED", "true").lower() in ("1", "true", "yes")
MOE_AUTO_DELIBERATION_ACTIVATION = os.getenv(
    "MOE_AUTO_DELIBERATION_ACTIVATION", "adaptive"
).strip().lower()
if MOE_AUTO_DELIBERATION_ACTIVATION not in {"disabled", "adaptive", "required"}:
    raise ValueError(
        "MOE_AUTO_DELIBERATION_ACTIVATION must be disabled, adaptive, or required"
    )
KNOWLEDGE_BYPASS_THRESHOLD = float(os.getenv("KNOWLEDGE_BYPASS_THRESHOLD", "0.25"))
KNOWLEDGE_BYPASS_MIN_CONF  = float(os.getenv("KNOWLEDGE_BYPASS_MIN_CONF",  "0.85"))
KNOWLEDGE_BYPASS_TTL_DAYS  = int(os.getenv("KNOWLEDGE_BYPASS_TTL_DAYS",    "14"))

# Routing bandit: a contextual Thompson bandit learns the retrieval gates
# (skip_research / enable_graphrag) from request outcomes, replacing the fixed
# fuzzy thresholds as the *decision authority*. The fuzzy t-norm scores and the
# complexity level survive as the context (features) AND as the cold-start
# fallback: until BOTH arms of a (gate, context) have MIN_DATAPOINTS observations,
# the heuristic decision is used unchanged — so routing never regresses below the
# fuzzy baseline while it learns. COST_PRIOR gives the cheaper arm (skip/off) an
# optimistic head-start so ties resolve toward saving inference. CONTEXT_BANDS is
# how finely each t-norm score is discretised (2 = low/high → fast learning).
ROUTING_BANDIT_ENABLED        = os.getenv("ROUTING_BANDIT_ENABLED", "true").lower() in ("1", "true", "yes")
ROUTING_BANDIT_MIN_DATAPOINTS = int(os.getenv("ROUTING_BANDIT_MIN_DATAPOINTS", "20"))
ROUTING_BANDIT_COST_PRIOR     = float(os.getenv("ROUTING_BANDIT_COST_PRIOR",   "0.5"))
ROUTING_BANDIT_CONTEXT_BANDS  = int(os.getenv("ROUTING_BANDIT_CONTEXT_BANDS",  "2"))

# Embedding-space prior for cold-start bridging (services/routing_patterns.py).
# Both Thompson bandits above key on discrete buckets (category string / banded
# fuzzy score); similar-but-distinct contexts share no statistics until their
# own bucket clears MIN_DATAPOINTS. When enabled and a bucket is still below
# its MIN_DATAPOINTS threshold, the k nearest historical observations in BGE
# embedding space are blended in as a capped, distance-weighted Beta
# pseudo-count prior. Disabled by default: additive, opt-in, and a no-op once
# a bucket has real observations — existing behaviour is unchanged either way.
ROUTING_PATTERN_PRIOR_ENABLED = os.getenv("ROUTING_PATTERN_PRIOR_ENABLED", "false").lower() in ("1", "true", "yes")
ROUTING_PATTERN_PRIOR_K       = int(os.getenv("ROUTING_PATTERN_PRIOR_K",   "3"))
ROUTING_PATTERN_PRIOR_CAP     = float(os.getenv("ROUTING_PATTERN_PRIOR_CAP", "3.0"))
ROUTING_PATTERN_RING_SIZE     = int(os.getenv("ROUTING_PATTERN_RING_SIZE", "200"))

# Trivial fast-path: a conservative eligibility gate in graph/planner.py excludes
# calculations, legal/current/research/file/image and conversation-context work.
# Only the remaining unambiguous one-shot requests skip the planner LLM.
TRIVIAL_FAST_PATH_ENABLED  = os.getenv("TRIVIAL_FAST_PATH_ENABLED", "true").lower() in ("1", "true", "yes")
TRIVIAL_FAST_PATH_CATEGORY = os.getenv("TRIVIAL_FAST_PATH_CATEGORY", "general")
# When a trivial query is cost-tier-limited to T1 (force_tier1), still allow a
# Tier-2 rescue if the T1 answer comes back low-confidence/empty. Medium/high T1
# answers keep the cost saving; only genuine garbage escalates. Disable to keep
# the strict "trivial never touches T2" behaviour.
TRIVIAL_LOW_CONF_RESCUE_ENABLED = os.getenv("TRIVIAL_LOW_CONF_RESCUE_ENABLED", "true").lower() in ("1", "true", "yes")
EXPERT_TIER_BOUNDARY_B    = float(os.getenv("EXPERT_TIER_BOUNDARY_B",   "20"))
EXPERT_MIN_SCORE          = float(os.getenv("EXPERT_MIN_SCORE",         "0.3"))
EXPERT_MIN_DATAPOINTS     = int(os.getenv("EXPERT_MIN_DATAPOINTS",      "5"))

# =============================================================================
# Conversation history limits
# =============================================================================

HISTORY_MAX_TURNS   = int(os.getenv("HISTORY_MAX_TURNS",   "4"))
HISTORY_MAX_CHARS   = int(os.getenv("HISTORY_MAX_CHARS",   "3000"))
HISTORY_MAX_ENTRIES = int(os.getenv("HISTORY_MAX_ENTRIES", "5000"))

# CC history compression: long assistant/tool messages in the conversation history
# are condensed and their full content cached in Redis (TTL 3600 s) to prevent
# context flooding without dropping entire turns.
CC_HISTORY_COMPRESS_THRESHOLD  = int(os.getenv("CC_HISTORY_COMPRESS_THRESHOLD",  "8000"))
CC_HISTORY_COMPRESS_KEEP_TURNS = int(os.getenv("CC_HISTORY_COMPRESS_KEEP_TURNS", "8"))
# Fallback delay (seconds) before the CC pre-analysis planner fires.
# Overridden per-server via model_load_delay in Admin UI → Servers.
CC_PREANALYSIS_DELAY_SECS = float(os.getenv("CC_PREANALYSIS_DELAY_SECS", "20"))

# Master kill-switch for the infrastructure-side "1M+ context" path on CC tool
# requests: ChromaDB context indexing (Tier-3), Tier-2 semantic-memory injection
# and Tier-3 retrieval. Default OFF — this path previously caused per-request OOM
# kills of the orchestrator (4 GiB cgroup limit) because the ChromaDB embedding
# function loaded an in-process ONNX model. services/context_index.py now uses
# the moe-embed sidecar over HTTP instead, removing that memory load. Re-enable
# via .env (CC_CONTEXT_INDEX_ENABLED=true) after verifying orchestrator memory
# stays stable under docker stats. When disabled, CC tool requests use the plain
# pass-through path (system prompt forwarded verbatim, budget-trimmed).
CC_CONTEXT_INDEX_ENABLED = os.getenv("CC_CONTEXT_INDEX_ENABLED", "false").lower() in ("1", "true", "yes")

# ── Augmented Tool Path (agentic clients: Claude Code / OpenCode) ────────────
# Opt-in enrichment layer for the tool-call fast path (_handle_tool_calls /
# _anthropic_tool_handler), which today bypasses cache, GraphRAG and ingestion
# entirely. All flags default OFF; enabled per CC profile / expert template.
# See moe-infra/UMSETZUNGSPLAN_AGENT_ENRICHMENT_2026-07-09.md for the design.
AGENT_CACHE_ENABLED       = os.getenv("AGENT_CACHE_ENABLED", "false").lower() in ("1", "true", "yes")
AGENT_CACHE_MIN_CONF      = float(os.getenv("AGENT_CACHE_MIN_CONF", str(KNOWLEDGE_BYPASS_MIN_CONF)))
AGENT_CACHE_TTL_DAYS      = int(os.getenv("AGENT_CACHE_TTL_DAYS", str(KNOWLEDGE_BYPASS_TTL_DAYS)))
AGENT_CACHE_L0_TTL        = int(os.getenv("AGENT_CACHE_L0_TTL", "1800"))
AGENT_CACHE_MAX_LOOKUP_MS = int(os.getenv("AGENT_CACHE_MAX_LOOKUP_MS", "300"))
AGENT_GRAPHRAG_ENABLED    = os.getenv("AGENT_GRAPHRAG_ENABLED", "false").lower() in ("1", "true", "yes")
AGENT_GRAPHRAG_TIMEOUT_S  = float(os.getenv("AGENT_GRAPHRAG_TIMEOUT_S", "2.0"))
AGENT_GRAPHRAG_MAX_CHARS  = int(os.getenv("AGENT_GRAPHRAG_MAX_CHARS", "4000"))
AGENT_INGEST_ENABLED      = os.getenv("AGENT_INGEST_ENABLED", "false").lower() in ("1", "true", "yes")
AGENT_INGEST_JUDGE        = os.getenv("AGENT_INGEST_JUDGE", "true").lower() in ("1", "true", "yes")

# =============================================================================
# Timeouts & LLM call limits
# =============================================================================

JUDGE_TIMEOUT         = int(os.getenv("JUDGE_TIMEOUT",         "600"))
EXPERT_TIMEOUT        = int(os.getenv("EXPERT_TIMEOUT",        "600"))
PLANNER_TIMEOUT       = int(os.getenv("PLANNER_TIMEOUT",       "180"))
# Hard ceiling for a non-streaming graph request. Individual model timeouts
# remain useful diagnostics, but no combination of retries may keep a client
# request alive indefinitely or leave it registered as active.
ORCHESTRATION_TIMEOUT = int(os.getenv("ORCHESTRATION_TIMEOUT", "300"))

JUDGE_REFINE_MAX_ROUNDS      = int(os.getenv("JUDGE_REFINE_MAX_ROUNDS",       "2"))
JUDGE_REFINE_MIN_IMPROVEMENT = float(os.getenv("JUDGE_REFINE_MIN_IMPROVEMENT", "0.15"))

TOOL_MAX_TOKENS      = int(os.getenv("TOOL_MAX_TOKENS",      "8192"))
REASONING_MAX_TOKENS = int(os.getenv("REASONING_MAX_TOKENS", "16384"))

PLANNER_RETRIES   = int(os.getenv("PLANNER_RETRIES",   "2"))
# Maximum planner tasks per request (increased to 8 for multi-step composite workloads)
PLANNER_MAX_TASKS = int(os.getenv("PLANNER_MAX_TASKS", "8"))
# Structured planning needs the executable JSON plan, not a hidden reasoning
# transcript that can consume the entire output budget before ``content`` is
# emitted. Operators can opt back in for a planner model proven to benefit.
PLANNER_THINKING_ENABLED = os.getenv(
    "PLANNER_THINKING_ENABLED", "false"
).lower() in ("1", "true", "yes")
# Executor and judge calls need an answer inside their bounded output budget.
# Reasoning-capable Ollama models expose hidden reasoning separately and can
# otherwise consume the entire limit before emitting any ``content``.
EXPERT_THINKING_ENABLED = os.getenv(
    "EXPERT_THINKING_ENABLED", "false"
).lower() in ("1", "true", "yes")
JUDGE_THINKING_ENABLED = os.getenv(
    "JUDGE_THINKING_ENABLED", "false"
).lower() in ("1", "true", "yes")

SSE_CHUNK_SIZE = int(os.getenv("SSE_CHUNK_SIZE", "50"))

# =============================================================================
# Feedback & self-evaluation
# =============================================================================

EVAL_CACHE_FLAG_THRESHOLD   = int(os.getenv("EVAL_CACHE_FLAG_THRESHOLD",   "2"))
FEEDBACK_POSITIVE_THRESHOLD = int(os.getenv("FEEDBACK_POSITIVE_THRESHOLD", "4"))
FEEDBACK_NEGATIVE_THRESHOLD = int(os.getenv("FEEDBACK_NEGATIVE_THRESHOLD", "2"))

# =============================================================================
# Web search
# =============================================================================

_SEARXNG_URL            = os.getenv("SEARXNG_URL", "").strip()
_WEB_SEARCH_FALLBACK_DDG: bool = (
    os.getenv("WEB_SEARCH_FALLBACK_DDG", "true").strip().lower() not in ("0", "false", "no")
)

# =============================================================================
# Primary endpoint fallback
# When the primary (external) inference endpoint is degraded (auth/quota errors),
# the orchestrator falls back to a configured local node.
# Generic env vars: FALLBACK_NODE / FALLBACK_MODEL / FALLBACK_MODEL_SECOND
# Legacy env var names (AIHUB_FALLBACK_*) are still accepted for backward compat.
# =============================================================================

_FALLBACK_NODE         = (
    os.getenv("FALLBACK_NODE") or os.getenv("AIHUB_FALLBACK_NODE", "")
)
_FALLBACK_MODEL        = (
    os.getenv("FALLBACK_MODEL") or os.getenv("AIHUB_FALLBACK_MODEL", "")
)
_FALLBACK_MODEL_SECOND = (
    os.getenv("FALLBACK_MODEL_SECOND") or os.getenv("AIHUB_FALLBACK_MODEL_SECOND", "")
)
_FALLBACK_ENABLED      = bool(_FALLBACK_NODE and _FALLBACK_MODEL)

# URL substring patterns that identify external (non-local) endpoints.
# Used to decide whether to apply the retry + fallback logic.
# Set EXTERNAL_ENDPOINT_PATTERNS to a comma-separated list to override.
_EXTERNAL_ENDPOINT_PATTERNS: list[str] = [
    p.strip().lower()
    for p in os.getenv("EXTERNAL_ENDPOINT_PATTERNS", "").split(",")
    if p.strip()
]

# Retry / degraded-window settings for any external endpoint.
# Tune via env vars; defaults are conservative for transient instability.
_ENDPOINT_RETRY_COUNT  = int(os.getenv("ENDPOINT_RETRY_COUNT",  "3"))
_ENDPOINT_RETRY_DELAY  = float(os.getenv("ENDPOINT_RETRY_DELAY", "2.0"))
_ENDPOINT_DEGRADED_TTL = int(os.getenv("ENDPOINT_DEGRADED_TTL", "300"))

# =============================================================================
# Thompson sampling
# =============================================================================

THOMPSON_SAMPLING_ENABLED = os.getenv("THOMPSON_SAMPLING_ENABLED", "true").lower() in ("1", "true", "yes")

# =============================================================================
# Fuzzy logic routing & graph compression
# =============================================================================

_FUZZY_VECTOR_THRESHOLD          = float(os.getenv("FUZZY_VECTOR_THRESHOLD",          "0.30"))
_FUZZY_GRAPH_THRESHOLD           = float(os.getenv("FUZZY_GRAPH_THRESHOLD",           "0.35"))
_GRAPH_COMPRESS_THRESHOLD_FACTOR = float(os.getenv("GRAPH_COMPRESS_THRESHOLD_FACTOR", "2.0"))
_GRAPH_COMPRESS_LLM_MODEL        = os.getenv("GRAPH_COMPRESS_LLM", "")
_GRAPH_COMPRESS_LLM_TIMEOUT      = float(os.getenv("GRAPH_COMPRESS_LLM_TIMEOUT", "3.0"))

# Background summarizer for messages dropped by the CC tool path's history
# trimmer (_trim_oai_to_budget). Falls back to GRAPH_COMPRESS_LLM so a single
# configured "small summarizer" model covers both compression paths. Empty
# value disables summarization-on-drop — dropped history is silently discarded
# (the original behavior).
CC_HISTORY_COMPRESS_LLM         = os.getenv("CC_HISTORY_COMPRESS_LLM", _GRAPH_COMPRESS_LLM_MODEL)
CC_HISTORY_COMPRESS_LLM_TIMEOUT = float(os.getenv("CC_HISTORY_COMPRESS_LLM_TIMEOUT", str(_GRAPH_COMPRESS_LLM_TIMEOUT)))

# =============================================================================
# HTTP limits & CORS (used in middleware setup)
# =============================================================================

MAX_REQUEST_BODY_MB     = int(os.getenv("MAX_REQUEST_BODY_MB", "16"))
CORS_ALL_ORIGINS        = os.getenv("CORS_ALL_ORIGINS", "0") == "1"
CORS_ORIGINS_RAW        = os.getenv("CORS_ORIGINS", "")
MAX_REQUESTS_PER_MINUTE = int(os.getenv("MAX_REQUESTS_PER_MINUTE", "60"))

# =============================================================================
# Conversation audit logging
# =============================================================================

CONVERSATION_LOG_ENABLED              = os.getenv("CONVERSATION_LOG_ENABLED", "true").lower() in ("1", "true", "yes")
CONVERSATION_LOG_RETENTION_DAYS_DEFAULT = int(os.getenv("CONVERSATION_LOG_RETENTION_DAYS_DEFAULT", "90"))
CONVERSATION_LOG_RETENTION_MAX        = int(os.getenv("CONVERSATION_LOG_RETENTION_MAX", "365"))
CONVERSATION_LOG_DIR                  = os.getenv("CONVERSATION_LOG_DIR", "/app/logs/users")

# =============================================================================
# Custom expert prompts (admin override, rarely set)
# =============================================================================

_CUSTOM_EXPERT_PROMPTS: dict = json.loads(os.getenv("CUSTOM_EXPERT_PROMPTS", "{}"))

# =============================================================================
# Output modes (model ID → pipeline behaviour)
# =============================================================================

MODES: dict = {
    "default": {
        "model_id":      "moe-orchestrator",
        "description":   "Complete answers with explanations (default)",
        "expert_suffix": "",
        "merger_prefix": (
            "Synthesize the following information into a clear, complete answer.\n"
            "Priority: MCP calculations > knowledge graph > experts (high/medium) > web > experts (low) > cache.\n"
            "Ignore contradictory expert statements when MCP or graph data are available.\n"
            "Act as cross-domain validator: compare all numerical values in the expert answers "
            "with the original request. In case of discrepancies, the original value has absolute priority. "
            "If an expert ignored a subtask, name the gap explicitly instead of filling it with "
            "hallucinations.\n"
            "CRITICAL: If the original question has multiple parts (a), (b), (c), you MUST address "
            "EVERY part in your synthesis. Each expert may have answered a different part — combine "
            "them all. When an MCP tool provided a calculation result, copy the result VERBATIM.\n"
            "MULTI-EXPERT SYNTHESIS: When multiple expert responses are present, treat them as "
            "complementary domain perspectives — do NOT pick one and ignore the others. "
            "Each expert covers a different angle; your job is to integrate ALL angles into a "
            "single coherent answer. If experts contradict each other, name the contradiction "
            "explicitly and state which view is better supported by available evidence. "
            "If an expert's domain is irrelevant to the question, skip it silently.\n"
            "LANGUAGE: Always answer in the same language as the original question."
        ),
    },
    "code": {
        "model_id":      "moe-orchestrator-code",
        "description":   "Source code only — no explanations, no prose",
        "expert_suffix": (
            "\n\nIMPORTANT: Answer EXCLUSIVELY with runnable source code. "
            "No introduction, no explanations, no conclusion. "
            "Inline comments in code are allowed. "
            "Add as first line: # CONFIDENCE: high | medium | low"
        ),
        "merger_prefix": (
            "Return ONLY the finished, complete, runnable source code. "
            "Absolutely no prose, no introduction, no explanations, no conclusion. "
            "Combine and deduplicate the expert code suggestions into the best result. "
            "Code only."
        ),
    },
    "concise": {
        "model_id":      "moe-orchestrator-concise",
        "description":   "Short, precise answers without rambling",
        "expert_suffix": (
            "\n\nAnswer very briefly and precisely. Maximum 4 sentences. "
            "No repetitions, no introduction, no conclusion."
        ),
        "merger_prefix": (
            "Synthesize into a short, precise answer. "
            "Maximum 120 words. No introduction, no conclusion, no repetitions. "
            "Priority: MCP > knowledge graph > experts > web."
        ),
        "skip_think": False,
    },
    "agent": {
        "model_id":        "moe-orchestrator-agent",
        "description":     "Coding agent mode — for OpenCode, Continue.dev and AI coding tools (OpenAI-compatible)",
        "expert_suffix":   (
            "\n\nYou are a precise coding agent. Answer technically exact and directly actionable. "
            "Use markdown code blocks for all code. No unnecessary explanations unless explicitly asked."
        ),
        "merger_prefix":   (
            "Synthesize the expert code analyses into one precise, actionable response. "
            "Use markdown code blocks for all code. Be concise and direct. "
            "Do not add preamble or boilerplate. Start with the solution immediately. "
            "Priority: code_reviewer > technical_support."
        ),
        "skip_think":       True,
        "force_categories": ["code_reviewer", "technical_support"],
    },
    "agent_orchestrated": {
        "model_id":    "moe-orchestrator-agent-orchestrated",
        "description": "Claude Code — full planner, all experts, force_think (maximum quality)",
        "expert_suffix": (
            "\n\nYou are a precise coding agent. Answer technically exact and directly actionable. "
            "Use markdown code blocks for all code. No unnecessary explanations unless explicitly asked."
        ),
        "merger_prefix": (
            "Synthesize the expert analyses into one precise, actionable response. "
            "Use markdown code blocks for all code. Be concise and direct. "
            "Do not add preamble or boilerplate. Start with the solution immediately. "
            "Priority: code_reviewer > technical_support > general."
        ),
        "skip_think":  True,
        "force_think": True,
    },
    "research": {
        "model_id":    "moe-orchestrator-research",
        "description": "Deep research — multiple parallel web searches, structured research report",
        "expert_suffix": (
            "\n\nYou support a structured deep research. "
            "Analyze the topic from your domain perspective. "
            "Explicitly name: established knowledge, open questions and research gaps. "
            "Cite sources when known."
        ),
        "merger_prefix": (
            "Create a structured research report from the following sources.\n"
            "Use exactly this outline:\n\n"
            "## Summary\n[2-3 sentence key statement]\n\n"
            "## Main Findings\n[Most important insights as bullet points]\n\n"
            "## Details\n[In-depth analysis with all relevant information]\n\n"
            "## Sources\n[All cited web sources with [N] numbers]\n\n"
            "Priority: web research > knowledge graph > experts (high/medium) > web > experts (low).\n"
            "Ignore contradictory expert statements when web research data is available."
        ),
        "force_think": True,
    },
    "report": {
        "model_id":    "moe-orchestrator-report",
        "description": "Professional report — full pipeline, structured markdown report",
        "expert_suffix": (
            "\n\nYou deliver inputs for a professional technical report. "
            "Write objectively, precisely and in report style. "
            "Clearly separate facts from evaluations. "
            "Structure your contribution with short paragraphs."
        ),
        "merger_prefix": (
            "Create a professional technical report in Markdown format.\n"
            "Use exactly this structure:\n\n"
            "# [Meaningful title]\n\n"
            "## Executive Summary\n[3-5 sentences — key message for decision makers]\n\n"
            "## Background\n[Context, relevance, initial situation]\n\n"
            "## Main Findings\n[Most important insights, backed by experts and research]\n\n"
            "## Analysis\n[In-depth evaluation, connections, implications]\n\n"
            "## Conclusion\n[Conclusions and, if applicable, recommendations for action]\n\n"
            "## Sources\n[All cited sources]\n\n"
            "Tone: objective, professional, suitable for authorities or companies.\n"
            "Priority: MCP calculations > knowledge graph > experts (high/medium) > web > experts (low)."
        ),
        "force_think": True,
    },
    "plan": {
        "model_id":    "moe-orchestrator-plan",
        "description": "Plan & Execute — shows execution plan, runs all tasks in parallel",
        "expert_suffix": (
            "\n\nYou deliver inputs for comprehensive planning. "
            "Be complete and precise. Explicitly name what you know and what is uncertain."
        ),
        "merger_prefix": (
            "Synthesize all expert inputs and research into a complete, "
            "structured answer.\n"
            "Consider all available sources. Prioritize accuracy over brevity.\n"
            "Priority: MCP calculations > knowledge graph > experts (high/medium) > web > experts (low).\n"
            "Ignore contradictory expert statements when MCP or graph data are available."
        ),
        "force_think": True,
    },
    "auto": {
        "model_id":    "moe-auto",
        "description": "Auto — AI dynamically selects the optimal experts for each prompt",
        "expert_suffix": "",
        "merger_prefix": (
            "Synthesize the following information into a clear, complete answer.\n"
            "Priority: MCP calculations > knowledge graph > experts (high/medium) > web > experts (low).\n"
            "LANGUAGE: Always answer in the same language as the original question."
        ),
        # Sentinel: this model ID always triggers the dynamic router in chat.py.
        "_is_dynamic_auto": True,
    },
}

_MODEL_ID_TO_MODE: dict = {v["model_id"]: k for k, v in MODES.items()}

# =============================================================================
# Claude model display names (for model picker UI)
# =============================================================================

_CLAUDE_PRETTY_NAMES: dict = {
    "claude-opus-4-7":            "Claude Opus 4.7",
    "claude-opus-4-6":            "Claude Opus 4.6",
    "claude-sonnet-4-6":          "Claude Sonnet 4.6",
    "claude-haiku-4-5-20251001":  "Claude Haiku 4.5",
    "claude-opus-4-5":            "Claude Opus 4.5",
    "claude-sonnet-4-5":          "Claude Sonnet 4.5",
    "claude-3-5-sonnet-20241022": "Claude 3.5 Sonnet",
    "claude-3-5-haiku-20241022":  "Claude 3.5 Haiku",
    "claude-3-7-sonnet-20250219": "Claude 3.7 Sonnet",
}


def _model_display_name(model_id: str, description: str = "") -> str:
    """Human-readable label for the model picker (display_name field)."""
    if model_id in _CLAUDE_PRETTY_NAMES:
        return f"{_CLAUDE_PRETTY_NAMES[model_id]} → MoE (Gateway)"
    return description or model_id
