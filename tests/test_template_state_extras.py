"""Template toggles and tenant namespaces reach the pipeline state on every entry point, and the fuzzy router honours them."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import graph.router_nodes as router_nodes
from services.pipeline.responses import _ResponsesRequest, _invoke_pipeline_for_responses
from services.routing import request_tenant_ids, template_state_extras
from types import SimpleNamespace


def test_template_state_extras_defaults_and_overrides():
    assert template_state_extras({}) == {
        "enable_cache": True, "enable_graphrag": True, "graphrag_template_allowed": True,
        "enable_web_research": True, "graphrag_max_chars": 0,
    }
    out = template_state_extras({"enable_graphrag": False, "enable_cache": False, "graphrag_max_chars": 900})
    assert out["enable_graphrag"] is False and out["graphrag_template_allowed"] is False
    assert out["enable_cache"] is False and out["graphrag_max_chars"] == 900
    assert template_state_extras(None)["enable_graphrag"] is True


def test_request_tenant_ids_puts_the_private_namespace_first_without_duplicates():
    assert request_tenant_ids("u1", {"graph_tenant": ["team:a", "user:u1"]}) == ["user:u1", "team:a"]
    assert request_tenant_ids("anon", {"graph_tenant": ["team:a"]}) == ["team:a"]
    assert request_tenant_ids("", None) == []


@pytest.mark.asyncio
async def test_streaming_entry_point_forwards_the_template_toggles_and_tenants():
    template = {"name": "no-graph", "experts": {}, "enable_graphrag": False, "enable_web_research": False}
    user_ctx = {"user_id": "user-1", "key_id": "key-1", "permissions_json": json.dumps({"graph_tenant": ["team:a"]}),
                "user_templates_json": json.dumps({"tid": template}), "user_connections_json": "{}"}
    captured = {}

    async def fake_stream_response(*args, **kwargs):
        captured.update(kwargs)
        yield 'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
        yield "data: [DONE]\n\n"

    request = _ResponsesRequest(model="no-graph", input="test", stream=True)
    raw = SimpleNamespace(headers={"authorization": "Bearer moe-sk-test"}, client=SimpleNamespace(host="127.0.0.1"))
    with (
        patch("services.pipeline.responses._validate_api_key", new=AsyncMock(return_value=user_ctx)),
        patch("services.pipeline.responses._read_expert_templates", return_value=[]),
        patch("services.routing._read_expert_templates", return_value=[]),
        patch("services.pipeline.responses._register_active_request", new=AsyncMock()),
        patch("main.stream_response", new=fake_stream_response),
    ):
        await _invoke_pipeline_for_responses(raw, request, [{"role": "user", "content": "test"}])

    ts = captured["template_state"]
    assert ts["enable_graphrag"] is False and ts["graphrag_template_allowed"] is False and ts["enable_web_research"] is False
    assert ts["tenant_ids"] == ["user:user-1", "team:a"]


def _router_state(**extra):
    return {"plan": [{"task": "t", "category": "legal_advisor"}], "complexity_level": "complex", "input": "q", **extra}


@pytest.fixture
def router(monkeypatch):
    monkeypatch.setattr(router_nodes, "_FUZZY_GRAPH_THRESHOLD", 0.0)  # any plan passes the heuristic gate
    monkeypatch.setattr(router_nodes, "_FUZZY_VECTOR_THRESHOLD", 0.0)
    return router_nodes.fuzzy_router_node


@pytest.mark.asyncio
async def test_template_that_forbids_the_graph_stays_off_whatever_the_router_decides(router):
    out = await router(_router_state(graphrag_template_allowed=False, enable_graphrag=False))
    assert out["enable_graphrag"] is False


@pytest.mark.asyncio
async def test_a_second_router_pass_does_not_read_its_own_off_as_the_template(router, monkeypatch):
    monkeypatch.setattr(router_nodes, "_FUZZY_GRAPH_THRESHOLD", 0.0)
    # state of a replan: the first pass wrote enable_graphrag=False, but the template allows the graph
    out = await router(_router_state(graphrag_template_allowed=True, enable_graphrag=False))
    assert out["enable_graphrag"] is True
