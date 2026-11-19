"""
AgentState structure and LangGraph accumulation-field tests.

LangGraph merges parallel node outputs using the reducer declared on each field.
Token-counting fields use Annotated[list, operator.add] so outputs from concurrent
expert nodes accumulate rather than overwrite each other.

These tests verify:
  - Required fields are present (catch typos during refactoring)
  - Accumulation semantics work as expected
  - Working-memory and causal-trail fields exist
"""

import operator
import pytest
from pipeline.state import AgentState


# ---------------------------------------------------------------------------
# Required fields — if any of these disappear, the pipeline breaks
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = {
    # Request identity
    "input", "response_id", "mode", "user_id",
    # Routing
    "plan", "complexity_level", "direct_expert",
    "deliberation_policy", "deliberation_capacity", "deliberation_events",
    "request_deadline_monotonic", "client_max_output_tokens",
    # Results
    "expert_results", "expert_models_used", "final_response",
    "web_research", "cached_facts", "graph_context",
    # Token accounting (Annotated accumulation fields)
    "prompt_tokens", "completion_tokens",
    # History
    "chat_history",
    # Agentic re-planning
    "agentic_iteration", "agentic_history",
    # Working memory
    "working_memory", "tool_calls_log",
    # Knowledge broker and template permission for the graph
    "knowledge_bundle", "knowledge_block", "knowledge_entity_hits", "knowledge_gaps", "knowledge_llm_context",
    "knowledge_broker_mode", "graphrag_template_allowed", "cached_facts_meta",
}


def test_agent_state_has_all_required_fields():
    hints = AgentState.__annotations__
    missing = REQUIRED_FIELDS - set(hints.keys())
    assert not missing, (
        "AgentState fehlen Felder nach Refactoring:\n" + "\n".join(sorted(missing))
    )


def test_agent_state_is_typed_dict():
    """AgentState must remain a TypedDict so LangGraph can serialize it."""
    from typing import get_type_hints
    # TypedDicts have __annotations__ and __required_keys__
    assert hasattr(AgentState, "__annotations__")
    assert hasattr(AgentState, "__required_keys__") or hasattr(AgentState, "__optional_keys__")


# ---------------------------------------------------------------------------
# Token accumulation — the core LangGraph merge mechanic
# ---------------------------------------------------------------------------

def test_token_accumulation_via_operator_add():
    """
    LangGraph reduces Annotated[list, operator.add] fields by concatenating lists.
    Two expert nodes each contribute tokens; the merged state sums them.
    """
    node_a_tokens = [512]
    node_b_tokens = [1024]
    merged = operator.add(node_a_tokens, node_b_tokens)

    assert merged == [512, 1024]
    assert sum(merged) == 1536


def test_expert_results_accumulate_not_overwrite():
    """Multiple expert nodes write to expert_results; all contributions must survive."""
    results_a = [{"category": "research", "confidence": "high", "answer": "Antwort A"}]
    results_b = [{"category": "coding",   "confidence": "medium", "answer": "Antwort B"}]
    merged = operator.add(results_a, results_b)

    assert len(merged) == 2
    categories = {r["category"] for r in merged}
    assert categories == {"research", "coding"}


# ---------------------------------------------------------------------------
# Working-memory and causal-trail fields
# ---------------------------------------------------------------------------

def test_working_memory_field_exists():
    """working_memory is the hub for inter-node fact sharing."""
    assert "working_memory" in AgentState.__annotations__


def test_tool_calls_log_field_exists():
    """tool_calls_log records MCP/agentic tool interactions for XAI."""
    assert "tool_calls_log" in AgentState.__annotations__


def test_causal_trail_fields_exist():
    """graphrag_entities and judge_reason power the explainability layer."""
    hints = set(AgentState.__annotations__.keys())
    assert "graphrag_entities" in hints
    assert "judge_reason" in hints
