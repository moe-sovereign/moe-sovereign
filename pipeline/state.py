"""
pipeline/state.py — Shared LangGraph state contract for MoE Sovereign.

AgentState is the single TypedDict that flows through every node in the
pipeline. Treat it as the public API between nodes: if a field appears here,
it is intentional; if a node needs new data, it must be declared here first.

Field groups (in declaration order):
    1. Request identity & auth       — who asked, with what permissions
    2. Planning & routing            — what the planner decided
    3. Node results                  — outputs of parallel worker nodes
    4. Token accounting              — accumulated across all LLM calls
    5. Conversation context          — history, reasoning, examples
    6. Template configuration        — per-template toggles and overrides
    7. History limits                — per-template conversation pruning
    8. LLM overrides                 — per-template judge / planner models
    9. Routing & complexity flags    — skip signals from complexity estimator
   10. Causal-path & explainability  — audit trail for compliance
   11. Agentic loop                  — re-planning state across iterations
   12. Working memory hub            — structured facts + tool call log
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, Dict, List, TypedDict, Optional

from pipeline.logic_types import ConflictEntry  # noqa: F401


class AgentState(TypedDict):
    # ── 1. Request identity & auth ────────────────────────────────────────────
    input: str                          # Raw user query text
    response_id: str                    # Unique chat ID used for feedback tracking
    mode: str                           # "default"|"code"|"concise"|"agent"|"agent_orchestrated"|"research"|"report"|"plan"
    user_id: str                        # Authenticated user identifier, or "anon"
    api_key_id: str                     # API key ID written to usage_log
    session_id: str                     # Client session identifier (used for Tier-3 context index and Tier-2 memory)
    request_deadline_monotonic: float   # Absolute time.monotonic() deadline shared by every stage
    client_max_output_tokens: int       # Positive OpenAI max_tokens/max_completion_tokens budget, 0=unset
    system_prompt: str                  # System message from client (e.g. coding-agent file context)
    behavioral_directives: str          # CC profile system_prefix — injected as mandatory instructions into all nodes
    template_name: str                  # User-facing template name; empty for native/mode requests
    user_permissions: dict              # Deserialized permissions_json from Valkey
    user_experts: dict                  # Per-template expert config resolved from template
    tenant_ids: List[str]               # Graph tenant IDs for RBAC-scoped Neo4j queries
    local_only_routing: bool            # True = every outbound LLM call this request makes must
                                         # target a private/allowlisted host (services/sovereignty.py
                                         # enforces this at dispatch); resolved once per request from
                                         # permission flag > API-key flag > global env, TASK-53

    # ── 2. Planning & routing ─────────────────────────────────────────────────
    plan: List[Dict[str, Any]]          # [{id, task, category, mcp_tool?, mcp_args?, search_query?, metadata_filters?}]
    task_events: Annotated[list, operator.add]  # Append-only execution ledger events for planned tasks
    required_precision_intents: List[Dict[str, Any]]  # Pre-cache mandatory tool/argument contracts
    precision_shadow_intents: List[Dict[str, Any]]  # Detected contracts observed but not enforced
    precision_contract_mode: str      # shadow|enforce rollout mode frozen per request
    precision_contract_snapshot: Dict[str, Any]  # Immutable active schemas for required tools
    precision_contract_hash: str        # Hash over required contract IDs/schema hashes
    precision_catalog_hash: str         # Full discovered MCP schema catalog hash at preflight
    precision_cache_bypassed: bool      # Legacy response cache skipped for mandatory precision
    precision_direct: bool              # Entire request is covered by direct deterministic contracts
    complexity_level: str               # "trivial"|"moderate"|"complex" — set by planner
    deliberation_policy: dict            # Frozen DeliberationPolicyV1 template snapshot
    deliberation_capacity: dict          # Deterministic initial/reserve capacity decision
    deliberation_events: Annotated[list, operator.add]  # Bounded audit events from debate execution
    direct_expert: str                  # If set by semantic router, planner LLM is bypassed
    trivial_fast_path: bool             # Conservative one-shot path selected by planner
    pending_reports: List[str]          # Progress messages collected before pipeline starts streaming
    metadata_filters: Dict              # Optional domain filters from planner for scoped ChromaDB retrieval
    query_embedding: List[float]        # BGE embedding of `input`, computed once (see services/pipeline/chat.py);
                                         # empty when not computed (feature disabled or RouteLLM/pattern-prior off)

    # ── 3. Node results ───────────────────────────────────────────────────────
    # Annotated[list, operator.add] means LangGraph accumulates values from
    # parallel fan-out branches by concatenating rather than overwriting.
    expert_results: Annotated[list, operator.add]
    expert_models_used: Annotated[list, operator.add]   # "model::category" strings per expert call
    web_research: str                   # Formatted citations from SearXNG (research_node)
    cached_facts: str                   # L1 ChromaDB soft-cache hit content
    cache_hit: bool                     # True = L0 Redis exact match; pipeline short-circuits to merger
    guard_blocked: bool                 # True = Llama Guard flagged the input; pipeline short-circuits to merger
    guard_reason: str                   # Llama Guard hazard category (e.g. "S9") when guard_blocked
    guard_response: str                 # Fixed refusal message returned as final_response when guard_blocked
    math_result: str                    # SymPy calculation output (math_node)
    mcp_result: str                     # Precision tool results (mcp_node)
    mcp_evidence: Annotated[list, operator.add]  # Structured tool provenance/results keyed by task_id
    precision_fact_slots: List[Dict[str, Any]]  # Ordered opaque slot→typed evidence bindings
    precision_prompt_projection: str    # Value-free slot projection for merger and critic
    precision_rendered_response: str    # Direct deterministic response before binding
    precision_binding_status: str       # not_required|prepared|rendered|bound|failed
    precision_binding_errors: List[str] # Stable fail-closed binding errors
    precision_binding_hash: str         # Hash over slot/evidence/render mappings
    precision_bound_response_hash: str  # Hash sealing the post-mutation response
    precision_hybrid_composed: bool     # Mixed candidate composed without an LLM seeing typed facts
    precision_hybrid_expert_body: str   # Non-precision body isolated for a scoped critic pass
    precision_hybrid_expert_task: str   # Non-precision task contract shown to the scoped critic
    precision_hybrid_expert_confidence: str  # high|medium; medium forces scoped review
    graph_context: str                  # Neo4j retrieved context, possibly compressed (graph_rag_node)
    retrieved_graph_chunks: List[Dict]  # Identifiable Neo4j entities actually injected into synthesis
    final_response: str                 # Merger's synthesised answer (overwritten by critic if edited)
    reasoning_trace: str                # Chain-of-thought from thinking_node
    soft_cache_examples: str           # Few-shot similar Q&A pairs injected into planner/merger context
    images: List[Dict]                  # Extracted image payloads for vision-capable experts

    # ── 4. Token accounting ───────────────────────────────────────────────────
    # Both fields accumulate across all parallel node calls via operator.add.
    prompt_tokens: Annotated[int, operator.add]
    completion_tokens: Annotated[int, operator.add]
    # Tokens from user-owned API connections (not counted toward MoE budget).
    # Accumulated separately so budget call sites can deduct them from the total.
    user_conn_prompt_tokens:     Annotated[int, operator.add]
    user_conn_completion_tokens: Annotated[int, operator.add]

    # ── 5. Conversation context ───────────────────────────────────────────────
    chat_history: List[Dict]            # Prior user/assistant turns (truncated per history limits below)
    output_skill_body: str              # Resolved skill body for output formatting (planner suggestion)
    provenance_sources: List[Dict]      # [REF:entity] tags extracted from merger output for graph ingest

    # ── 6. Template feature toggles ───────────────────────────────────────────
    enable_cache: bool                  # Allow L1 ChromaDB cache reads/writes
    enable_graphrag: bool               # Allow Neo4j knowledge graph queries
    enable_web_research: bool           # Allow SearXNG web search
    search_fallback_ddg: bool           # Fall back to DuckDuckGo when SearXNG returns no results
    graphrag_max_chars: int             # Hard char budget for graph_context (0 = derive from judge model context window)
    no_cache: bool                      # If True, bypass both L0 (Redis) and L1 (ChromaDB) entirely

    # ── 7. History limits ─────────────────────────────────────────────────────
    history_max_turns: int              # Max conversation turns kept (0 = global default, -1 = unlimited)
    history_max_chars: int              # Max total history chars kept (0 = global default, -1 = unlimited)

    # ── 8. LLM overrides (per-template) ──────────────────────────────────────
    planner_prompt: str                 # Custom planner system prompt; empty = DEFAULT_PLANNER_ROLE
    judge_prompt: str                   # Custom judge/merger system prompt; empty = mode config default
    judge_model_override: str           # Override global JUDGE_MODEL for this template
    judge_url_override: str             # Override global JUDGE_URL
    judge_token_override: str           # Override global JUDGE_TOKEN
    planner_model_override: str         # Override global PLANNER_MODEL
    planner_url_override: str           # Override global PLANNER_URL
    planner_token_override: str         # Override global PLANNER_TOKEN
    planner_num_ctx: int                # Template-specific planner context window (0 = global default / auto-detect)
    judge_num_ctx: int                  # Template-specific judge context window (0 = global default / auto-detect)

    # ── 9. Routing & complexity flags ─────────────────────────────────────────
    skip_research: bool                 # Skip research_node (set by complexity estimator for trivial queries)
    skip_thinking: bool                 # Skip thinking_node (set by complexity estimator)
    query_temperature: float            # Adaptive sampling temperature: math=0.05, creative=0.70, default=0.20
    cost_tier: str                      # "local_7b"|"mid_tier"|"full" — cost classifier output
    force_tier1: bool                   # Skip Tier-2 experts to reduce cost for trivial tasks

    # ── 10. Causal-path & explainability ──────────────────────────────────────
    # These fields form an audit trail for compliance and XAI dashboards.
    graphrag_entities: List[Dict]       # Neo4j entities used: [{name, type, confidence, relations_used}]
    judge_reason: str                   # Human-readable explanation of why the judge intervened
    judge_before_after: Dict            # {before_score, after_score, delta} from judge refinement loop
    expert_inputs: Dict                 # {expert_name: {tokens_out, latency_ms}} per-expert call stats
    causal_intervention: Optional[Dict] # Causal intervention metadata if triggered

    # ── 11. Agentic re-planning loop ──────────────────────────────────────────
    # When max_agentic_rounds > 0, the merger can trigger a second planning pass
    # by setting agentic_gap. The loop exits when the gap is empty or the round
    # limit is reached.
    agentic_iteration: int              # Current loop iteration (0 = first pass)
    max_agentic_rounds: int             # Max allowed iterations from template config (0 = disabled)
    agentic_history: list               # [{iteration, findings, gap}] accumulated across rounds
    agentic_gap: str                    # What information is still missing — output of gap-detection LLM call
    attempted_queries: list             # All search queries already tried [{query, result_quality}] — injected into re-planner to prevent repetition
    search_strategy_hint: str          # Suggested next search approach from gap-detection LLM (e.g. "try site:github.com")
    discovered_domains: list           # Authoritative domains found in web results [{domain, context}] — offered to re-planner for targeted follow-up

    # ── 12. Fuzzy logic routing scores ───────────────────────────────────────
    # Confidence scores in [0.0, 1.0] for programmatic routing decisions.
    # Computed by fuzzy_router_node via t-norm conjunction (Gödel or Łukasiewicz).
    # Mathematical basis: de Vries (2007), arXiv:0707.2161, §4.
    vector_confidence: float          # Degree to which this query needs vector/semantic search
    graph_confidence: float           # Degree to which this query needs knowledge-graph retrieval
    fuzzy_routing_scores: dict        # {vector, graph, tnorm_vector, tnorm_graph, method}
    routing_bandit_context: str       # "research_ctx|||graph_ctx" buckets — set by fuzzy_router, consumed by merger to attribute the routing reward

    # ── 13. Formal logic state (Paraconsistent + Intuitionistic) ─────────────
    # conflict_registry accumulates ConflictEntry dicts when two experts in the
    # same category produce significantly divergent outputs. Entries are never
    # deleted or overwritten — paraconsistent semantics tolerate contradictions.
    # Mathematical basis: de Vries (2007), arXiv:0707.2161, Section 2.
    conflict_registry: Annotated[list, operator.add]  # List[ConflictEntry-dict]

    # ── 13. Working memory hub ────────────────────────────────────────────────
    # Structured persistence layer for tool results across agentic iterations.
    # Unlike agentic_history (unstructured prose), working_memory is keyed and
    # queryable so later iterations can reference specific facts by name.
    working_memory: dict                # {key: {value, source, confidence, ts}}
    tool_calls_log: list                # [{tool, args, result, status, ts}] — full tool invocation trace
    tool_failures: list                 # Subset of tool_calls_log: only failed calls; injected into planner prompt

    # ── 14. Cascade & constitution ────────────────────────────────────────────
    cascade_type: str                   # Typed cascade event: CONTEXT_GAP|EXPERT_FAILURE|CONTRADICTION|SCOPE_DRIFT|TOOL_FAILURE|COMPLETE
    stuck: bool                         # True when agentic loop exhausted budget without resolving the gap

    # ── 15. Trust-Score & quality gates ──────────────────────────────────────
    trust_score: float                  # Computed quality score [0.0–1.0] after expert round
    trust_verdict: str                  # PROCEED | PROCEED_WITH_ASSUMPTION | BLOCK
    trust_factors: dict                 # Per-factor breakdown (source_count, expert_count, conflict_penalty, cross_reference_coverage, unsupported_claims_penalty)
    self_critique_round: int            # Current self-critique iteration (0 = first pass)
    self_critique_max: int              # Max self-critique rounds (from SELF_CRITIQUE_MAX_ROUNDS env)
    self_critique_prev_score: float     # Trust score at the start of the last self-critique round
    review_replaces_self_critique: bool # Review wave ran and the template opted to skip self-critique
    constitution_violations: list       # [{rule_id, on_violation, detail}] from constitution enforcement

    # ── 16. Cynefin & HITL Gate ──────────────────────────────────────────────
    cynefin_domain: str                 # CLEAR | COMPLICATED | COMPLEX | CHAOTIC (TASK-15)
    hitl_gate_id: str                   # Gate ID if HITL freeze is active (TASK-14)
    hitl_gate_reason: str               # Human-readable reason returned with the 202 control response
    quality_blocked: bool               # True when no answer may leave the pipeline
    quality_block_reason: str           # Stable reason for the HTTP/SSE control response
    candidate_status: str               # normal|degraded|unavailable
    candidate_reason: str               # Why a validated degraded candidate was selected
    quality_gate_status: str            # passed|blocked|pending; sole commit router authority
    response_commit_context: dict       # Deferred synthesis/learning inputs, never written pre-gate
    response_commit_status: str         # complete|partial|reused|skipped|blocked
    response_commit_key: str            # Request/response/contract/evidence idempotency key
    response_commit_sinks: dict          # Per-sink journal state
    response_commit_errors: list         # Bounded sink error codes

    # ── 17. Strategy Review (TASK-22) ────────────────────────────────────────
    strategy_feedback: str              # Structural feedback from strategy reviewer (content-free)

    # ── 18. Structured-Output Failure Recovery (TASK-30) ─────────────────────
    structured_failure: dict            # Serialised StructuredFailure (None = no failure)
    structured_failure_round: int       # Current retry round (0 = first attempt)

    # ── 19. Hypothesis verification ───────────────────────────────────────────
    # Set by the caller, template, or planner to activate the verifier node.
    # Each entry: {input: Any, expected: Any, description: str, entry_fn: str?}
    # entry_fn names the function the expert must define (default "solve").
    verification_oracle: List[Dict[str, Any]]
    verification_result: Dict[str, Any]  # {passed, skipped?, failed_cases, code_used, error?}
