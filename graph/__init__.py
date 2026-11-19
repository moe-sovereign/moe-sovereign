"""LangGraph nodes — split into thematic submodules.

This package re-exports the node functions previously in graph/nodes.py
so existing `from graph import X` (and historic `from graph.nodes import X`)
imports keep working.
"""

# Router/gatekeeper nodes
from graph.router_nodes import (
    _seed_task_type_prototypes,
    guard_node,
    _route_guard,
    precision_preflight_node,
    cache_lookup_node,
    semantic_router_node,
    fuzzy_router_node,
    _route_cache,
    _route_precision_preflight,
)

# Knowledge broker (before the planner)
from graph.knowledge_broker import (
    knowledge_broker_node,
)

# Deterministic tool nodes
from graph.tool_nodes import (
    _validate_tool_result,
    mcp_node,
    graph_rag_node,
    math_node_wrapper,
)

# Planner + dependency-level helpers
from graph.planner import (
    _sanitize_plan,
    _detect_query_temperature,
    planner_node,
    _topological_levels,
    _inject_prior_results,
)

# Expert worker
from graph.expert import (
    expert_worker,
)

# Research + fallback + graph-context compression
from graph.research import (
    research_node,
    _extract_authoritative_domains,
    _rerank_graph_context,
    _compress_graph_context_llm,
    research_fallback_node,
)

# Synthesis + critic + replan router
from graph.synthesis import (
    merger_node,
    thinking_node,
    _should_replan,
    resolve_conflicts_node,
    critic_node,
    self_critique_node,
    quality_gate_node,
)
from graph.strategy_review_node import strategy_review_node
from graph.hypothesis_verifier import hypothesis_verifier_node, _route_after_strategy_review

# Deterministic direct rendering + final evidence binding
from graph.precision import (
    deterministic_precision_renderer_node,
    precision_bind_node,
    precision_slot_prepare_node,
)

# Quality-atomic persistence
from graph.commit import _route_quality_gate, response_commit_node
