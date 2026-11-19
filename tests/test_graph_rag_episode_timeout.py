"""A slow episodic-memory query must not end the request; a spent request deadline still must."""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

import graph.tool_nodes as tool_nodes
from services.deadline import RequestDeadlineExceeded, wait_for_budget as real_wait_for_budget

STATE = {"input": "What is the capacity of Elm12 depot?", "response_id": "t1", "plan": [{"task": "x", "category": "general"}],
         "enable_graphrag": True, "tenant_ids": []}


def _setup(monkeypatch):
    mgr = MagicMock()
    mgr.driver = object()
    mgr.query_context = AsyncMock(return_value="[Knowledge Graph]\n• Elm12 (Depot): HAS_CAPACITY 40 pallets")
    monkeypatch.setattr(tool_nodes.state, "graph_manager", mgr)
    monkeypatch.setattr(tool_nodes.state, "redis_client", None)
    monkeypatch.setattr(tool_nodes, "_record_stage", AsyncMock())
    monkeypatch.setattr(tool_nodes, "get_episode_hint", AsyncMock(return_value="hint"))
    monkeypatch.setattr(tool_nodes, "GRAPH_VIA_MCP", False)


def test_episode_hint_stage_timeout_is_not_fatal(monkeypatch):
    _setup(monkeypatch)

    async def wait(aw, state_, cap, *, stage):
        if stage == "episode_hint":
            aw.close()
            raise RequestDeadlineExceeded("episode_hint: stage cap")
        return await real_wait_for_budget(aw, state_, cap, stage=stage)

    monkeypatch.setattr(tool_nodes, "wait_for_budget", wait)
    out = asyncio.run(tool_nodes.graph_rag_node(dict(STATE)))
    assert "Elm12" in out["graph_context"]


def test_spent_request_deadline_still_raises(monkeypatch):
    _setup(monkeypatch)
    expired = dict(STATE, request_deadline_monotonic=time.monotonic() - 1.0)
    with pytest.raises(RequestDeadlineExceeded):
        asyncio.run(tool_nodes.graph_rag_node(expired))
