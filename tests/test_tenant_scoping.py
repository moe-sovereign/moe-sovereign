"""Tenant scoping of correction memory and of the GraphRAG context cache."""
import asyncio
import hashlib
from unittest.mock import AsyncMock, MagicMock

import graph.tool_nodes as tool_nodes
from graph_rag import corrections


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def __aiter__(self):
        async def gen():
            for r in self._rows:
                yield r
        return gen()


def _driver(calls, fail_first=False):
    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def run(self, query, parameters=None, **kwargs):  # the real neo4j signature: `query` is taken
            params = {**(parameters or {}), **kwargs}
            calls.append((query, params))
            if fail_first and len(calls) == 1:
                raise RuntimeError("apoc missing")
            return _Result([{"wrong": "a", "correct": "b", "model": "m", "source": "s"}])

    drv = MagicMock()
    drv.session = lambda: _Session()
    return drv


def test_corrections_query_is_tenant_scoped_for_both_variants():
    calls = []
    out = asyncio.run(corrections.query_corrections(_driver(calls, fail_first=True), "q", "general", tenant_ids=["acme"]))
    assert out and len(calls) == 2
    assert "sorensenDice" in calls[0][0] and calls[0][1]["query"] == "q"  # the similarity query really ran first
    for query, params in calls:
        assert params["tenant_ids"] == ["acme"]
        assert "c.tenant_id IN $tenant_ids" in query


def test_corrections_without_tenants_pass_an_empty_list_meaning_unscoped():
    calls = []
    asyncio.run(corrections.query_corrections(_driver(calls), "q", "general"))
    assert calls[0][1]["tenant_ids"] == []


def _graph_key(monkeypatch, tenant_ids):
    seen = []

    class _Redis:
        async def get(self, key):
            seen.append(key)
            return "[Knowledge Graph]\n• X (T): R y"

    mgr = MagicMock()
    mgr.driver = object()
    monkeypatch.setattr(tool_nodes.state, "graph_manager", mgr)
    monkeypatch.setattr(tool_nodes.state, "redis_client", _Redis())
    monkeypatch.setattr(tool_nodes, "_record_stage", AsyncMock())
    state_ = {"input": "What is the capacity of Elm12 depot?", "response_id": "t", "enable_graphrag": True,
              "plan": [{"task": "x", "category": "general"}]}
    if tenant_ids is not None:
        state_["tenant_ids"] = tenant_ids
    asyncio.run(tool_nodes.graph_rag_node(state_))
    return seen[0]


def test_graph_cache_key_differs_per_tenant_and_is_unchanged_without_tenants(monkeypatch):
    a, b, none = (_graph_key(monkeypatch, t) for t in (["a"], ["b"], None))
    assert a != b and a != none
    legacy = "moe:graph:" + hashlib.sha256(("What is the capacity of Elm12 depot?" + "general").encode()).hexdigest()[:16]
    assert none == legacy


def test_similarity_query_runs_with_the_real_neo4j_run_signature():
    calls = []
    out = asyncio.run(corrections.query_corrections(_driver(calls), "how to size a pool", "general"))
    assert out and len(calls) == 1 and "sorensenDice" in calls[0][0]
    assert calls[0][1]["query"] == "how to size a pool" and calls[0][1]["min_similarity"] == 0.25
