"""graph_rag_node reuses what the knowledge broker already fetched, and never lets model knowledge into graph_context."""
from unittest.mock import AsyncMock, MagicMock

import pytest

import graph.tool_nodes as tool_nodes

RENDERED = "[Knowledge Graph]\n• Elm12 (Depot): HAS_CAPACITY 40 pallets"
LIVE = "[Knowledge Graph]\n• Elm12 (Depot): HAS_CAPACITY 99 pallets"
STATE = {"input": "What is the capacity of Elm12 depot?", "response_id": "t1", "enable_graphrag": True,
         "plan": [{"task": "x", "category": "general"}], "tenant_ids": []}


def _bundle(**over):
    b = {"sources": {"graph": "ok", "episodes": "ok", "corrections": "ok", "cache": "empty", "knowledge_llm": "disabled"},
         "rendered_graph": RENDERED, "episode_hint": "[Episode Hint]\n• Routing: a", "llm_answers": []}
    b.update(over)
    return b


@pytest.fixture
def mgr(monkeypatch):
    m = MagicMock()
    m.driver = object()
    m.query_context = AsyncMock(return_value=LIVE)
    monkeypatch.setattr(tool_nodes.state, "graph_manager", m)
    monkeypatch.setattr(tool_nodes.state, "redis_client", None)
    monkeypatch.setattr(tool_nodes, "_record_stage", AsyncMock())
    monkeypatch.setattr(tool_nodes, "get_episode_hint", AsyncMock(return_value="[Episode Hint]\n• live"))
    monkeypatch.setattr(tool_nodes, "GRAPH_VIA_MCP", False)
    return m


@pytest.mark.asyncio
async def test_bundle_text_is_used_and_neo4j_and_episodes_are_not_queried_again(mgr):
    out = await tool_nodes.graph_rag_node({**STATE, "knowledge_bundle": _bundle(), "knowledge_entity_hits": ["Elm12"]})
    mgr.query_context.assert_not_awaited()
    tool_nodes.get_episode_hint.assert_not_awaited()
    assert out["graph_context"] == RENDERED + "\n\n[Episode Hint]\n• Routing: a"
    assert [c["id"] for c in out["retrieved_graph_chunks"]] == ["Elm12"]  # attribution still works on the same line format


@pytest.mark.asyncio
async def test_entity_hits_override_the_complexity_shortcut(mgr):
    out = await tool_nodes.graph_rag_node({**STATE, "skip_graph": True, "knowledge_bundle": _bundle(), "knowledge_entity_hits": ["Elm12"]})
    assert RENDERED in out["graph_context"]
    skipped = await tool_nodes.graph_rag_node({**STATE, "skip_graph": True})  # no hits: shortcut unchanged
    assert skipped == {"graph_context": "", "retrieved_graph_chunks": []}


@pytest.mark.asyncio
async def test_without_a_usable_bundle_the_old_path_runs(mgr):
    for bundle in ({}, _bundle(sources={"graph": "timeout"}), _bundle(rendered_graph="")):
        mgr.query_context.reset_mock()
        out = await tool_nodes.graph_rag_node({**STATE, "knowledge_bundle": bundle})
        mgr.query_context.assert_awaited_once()
        assert LIVE in out["graph_context"]


@pytest.mark.asyncio
async def test_knowledge_model_answers_never_enter_the_graph_context(mgr):
    bundle = _bundle(llm_answers=[{"answer": "99 pallets"}])
    out = await tool_nodes.graph_rag_node({**STATE, "knowledge_bundle": bundle, "knowledge_llm_context": "MODEL SAYS 99 pallets",
                                           "knowledge_block": "[UNVERIFIED MODEL KNOWLEDGE]\n- 99 pallets"})
    assert "MODEL SAYS" not in out["graph_context"] and "UNVERIFIED" not in out["graph_context"]
