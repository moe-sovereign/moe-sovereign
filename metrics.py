"""
metrics.py — Prometheus metrics registry for MoE Sovereign.

All PROM_* objects are created once at import time (Prometheus requirement:
duplicate metric names raise a ValueError). Import this module everywhere
metrics are needed instead of re-creating them.
"""

from prometheus_client import Counter, Gauge, Histogram

# ---------------------------------------------------------------------------
# Request & token accounting
# ---------------------------------------------------------------------------

PROM_TOKENS          = Counter('moe_tokens_total',             'Total tokens processed',      ['model', 'token_type', 'node', 'user_id'])
PROM_REQUESTS        = Counter('moe_requests_total',           'Total requests',              ['mode', 'cache_hit', 'user_id'])
PROM_BUDGET_EXCEEDED = Counter('moe_budget_exceeded_total',    'Budget limit exceeded',       ['user_id', 'limit_type'])
PROM_EXPERT_CALLS    = Counter('moe_expert_calls_total',       'Expert model calls',          ['model', 'category', 'node'])
PROM_CONFIDENCE      = Counter('moe_expert_confidence_total',  'Expert confidence level',     ['level', 'category'])
# Two-tier escalation outcome per category. decision:
#   t1_high_skip  — a T1 expert returned high confidence; T2 was skipped (cheap path won)
#   t2_escalated  — no T1 high confidence; T2 (large fallback models) ran
#   t1_only       — no T2 tier configured, only T1 ran
PROM_TIER_ESCALATION = Counter('moe_tier_escalation_total',    'Two-tier escalation decisions', ['category', 'decision'])
PROM_CACHE_HITS      = Counter('moe_cache_hits_total',         'Cache hits')
PROM_CACHE_MISSES    = Counter('moe_cache_misses_total',       'Cache misses')
PROM_KNOWLEDGE_BYPASS = Counter('moe_knowledge_bypass_total',  'LLM pipeline skipped via high-confidence fresh prior answer')
PROM_ROUTING_BANDIT  = Counter('moe_routing_bandit_total',     'Routing-gate decisions', ['gate', 'action', 'source'])
# Augmented Tool Path (agentic clients — Claude Code / OpenCode tool-call fast
# path). result: hit_l0 | hit_l1 | miss | timeout for cache; written | skipped |
# error for write-back. path: anthropic | openai.
PROM_AGENT_CACHE      = Counter('moe_agent_cache_total',       'Agent tool-path cache lookups', ['path', 'result'])
PROM_AGENT_WRITEBACK  = Counter('moe_agent_writeback_total',   'Agent tool-path write-backs',   ['path', 'result'])
PROM_RESPONSE_TIME   = Histogram('moe_response_duration_seconds', 'Response duration',        ['mode'],
                                 buckets=[1, 2, 5, 10, 20, 30, 60, 120])
# Nearest cache-entry cosine distance per lookup — the empirical distance
# distribution to calibrate the static thresholds (hit 0.15 / bypass 0.25 /
# soft 0.50). Buckets are dense around those decision boundaries.
PROM_CACHE_DISTANCE  = Histogram('moe_cache_query_distance', 'Nearest cache entry cosine distance per lookup',
                                 buckets=[0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.70, 1.0])
PROM_SELF_EVAL       = Histogram('moe_self_eval_score',        'Self-evaluation score',       buckets=[1,2,3,4,5,6])
PROM_FEEDBACK        = Histogram('moe_feedback_score',         'User feedback score',         buckets=[1,2,3,4,5,6])
PROM_COMPLEXITY      = Counter('moe_complexity_routing_total', 'Request complexity routing',  ['level'])

# ---------------------------------------------------------------------------
# Knowledge graph
# ---------------------------------------------------------------------------

PROM_CHROMA_DOCS          = Gauge('moe_chroma_documents_total',          'Documents in ChromaDB cache')
PROM_GRAPH_ENTITIES       = Gauge('moe_graph_entities_total',            'Entities in Neo4j')
PROM_GRAPH_RELATIONS      = Gauge('moe_graph_relations_total',           'Relations in Neo4j')
PROM_GRAPH_DENSITY        = Gauge('moe_graph_density_ratio',             'Relations per entity (graph connectivity measure)')
PROM_SYNTHESIS_NODES      = Gauge('moe_graph_synthesis_nodes_total',     'Synthesis-Nodes in Neo4j (:Synthesis)')
PROM_FLAGGED_RELS         = Gauge('moe_graph_flagged_relations_total',   'Flagged relations in Neo4j (r.flagged=true)')
PROM_SYNTHESIS_CREATED    = Counter('moe_synthesis_persisted_total',     'Synthesis nodes persisted', ['domain', 'insight_type'])
PROM_LINTING_RUNS         = Counter('moe_linting_runs_total',            'Total graph linting runs')
PROM_LINTING_ORPHANS      = Counter('moe_linting_orphans_deleted_total', 'Orphaned nodes deleted by linting')
PROM_LINTING_CONFLICTS    = Counter('moe_linting_conflicts_resolved_total', 'Conflicts resolved by linting')
PROM_LINTING_DECAY        = Counter('moe_linting_decay_deleted_total',   'Relations deleted by confidence decay')
PROM_QUARANTINE_ADDED     = Counter('moe_quarantine_added_total',        'Triples quarantined by blast-radius check')

# ---------------------------------------------------------------------------
# Inference server live status
# ---------------------------------------------------------------------------

PROM_ACTIVE_REQUESTS         = Gauge('moe_active_requests',                    'Active requests in progress')
PROM_SERVER_UP               = Gauge('moe_inference_server_up',                'Inference server reachable (1=up, 0=down)', ['server'])
PROM_SERVER_MODELS           = Gauge('moe_available_models_total',             'Available models per inference server',     ['server'])
PROM_SERVER_LOADED_MODELS    = Gauge('moe_inference_server_loaded_models',     'Currently loaded models in VRAM (Ollama /api/ps)', ['server'])
PROM_SERVER_VRAM_BYTES       = Gauge('moe_inference_server_vram_bytes',        'Total VRAM bytes used by loaded models',    ['server'])
PROM_SERVER_MODEL_VRAM_BYTES = Gauge('moe_inference_server_model_vram_bytes',  'VRAM bytes used by a specific loaded model', ['server', 'model'])
PROM_PLANNER_PATS            = Gauge('moe_planner_patterns_total',             'Successful planner patterns')
PROM_ONTOLOGY_GAPS           = Gauge('moe_ontology_gaps_total',                'Ontology gap terms')
PROM_ONTOLOGY_GAPS_ELIGIBLE  = Gauge('moe_ontology_gaps_eligible_total',       'Ontology gap terms at/above the healer classification-eligibility score threshold (MIN_GAP_SCORE) — excludes one-off extraction noise (SQL keywords, generic words) that will never be attempted')
PROM_ONTOLOGY_ENTS           = Gauge('moe_ontology_entities_total',            'Ontology entities (static)')

# ---------------------------------------------------------------------------
# Tool model calls
# ---------------------------------------------------------------------------

PROM_TOOL_CALL_DURATION = Histogram(
    'moe_tool_call_duration_seconds', 'Duration of individual tool model calls',
    ['node', 'model', 'phase'],
    buckets=[1, 2, 5, 10, 20, 30, 60, 120, 300, 600],
)
PROM_TOOL_TIMEOUTS      = Counter('moe_tool_call_timeout_total',        'Timeout errors in tool model calls',   ['node', 'model'])
PROM_TOOL_FORMAT_ERRORS = Counter('moe_tool_call_format_errors_total',  'Malformed tool call responses',        ['node', 'model', 'format'])
PROM_TOOL_CALL_SUCCESS  = Counter('moe_tool_call_success_total',        'Successful tool call responses',       ['node', 'model'])

# ---------------------------------------------------------------------------
# MCP precision-tool access classification (visibility only, no enforcement)
# ---------------------------------------------------------------------------

PROM_MCP_TOOL_ACCESS = Counter(
    'moe_mcp_tool_access_total', 'MCP precision-tool calls by access classification and verdict',
    ['tool', 'access_kind', 'verdict'],
)
PROM_PRECISION_EVENTS = Counter(
    'moe_precision_events_total',
    'Low-cardinality precision-contract lifecycle events',
    ['contract', 'stage', 'outcome', 'mode'],
)

# ---------------------------------------------------------------------------
# RL flywheel, context window, memory
# ---------------------------------------------------------------------------

PROM_HISTORY_COMPRESSED     = Counter('moe_history_compressed_total',     'History compression events (turns truncated)')
PROM_HISTORY_UNLIMITED      = Counter('moe_history_unlimited_total',      'Requests with compression disabled (per-template)')
PROM_SEMANTIC_MEMORY_STORED = Counter('moe_semantic_memory_stored_total', 'Conversation turns stored in Tier-2 semantic memory')
PROM_SEMANTIC_MEMORY_HITS   = Counter('moe_semantic_memory_hits_total',   'Warm context blocks injected from semantic memory')
PROM_CORRECTIONS_STORED     = Counter('moe_corrections_stored_total',     'Correction memory entries written to Neo4j',          ['source'])
PROM_CORRECTIONS_INJECTED   = Counter('moe_corrections_injected_total',   'Correction memory entries injected into expert prompts', ['category'])
PROM_JUDGE_REFINED          = Counter('moe_judge_refinement_total',       'Judge refinement rounds executed',    ['outcome'])
PROM_EXPERT_FAILURES        = Counter('moe_expert_failures_total',        'Expert invocation failures (VRAM, timeout, error)', ['model', 'reason'])

# ---------------------------------------------------------------------------
# Thompson sampling
# ---------------------------------------------------------------------------

PROM_THOMPSON = Histogram(
    "moe_thompson_sample", "Thompson-sampled expert score",
    buckets=[.1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0],
)

# ---------------------------------------------------------------------------
# Routing pattern prior (services/routing_patterns.py — embedding-space
# cold-start bridging for the Thompson bandits above, ROUTING_PATTERN_PRIOR_ENABLED)
# ---------------------------------------------------------------------------

# One increment per prior() consultation (i.e. only while a bucket is still
# below its own MIN_DATAPOINTS threshold and an embedding was available).
# outcome:
#   used        — k-NN found neighbors with nonzero weight; alpha/beta blended in
#   empty       — collection reachable but no (or too-distant) neighbors for this bucket yet
#   unavailable — ChromaDB collection not initialized/reachable
#   error       — the query itself raised (fail-open path)
PROM_PATTERN_PRIOR = Counter(
    'moe_routing_pattern_prior_total',
    'Embedding-space cold-start prior consultations, by namespace and outcome',
    ['namespace', 'outcome'],
)
