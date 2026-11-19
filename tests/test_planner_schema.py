"""The planner's output schema constrains category and mcp_tool to the real catalog."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jsonschema
import pytest

import graph.planner as planner
import state as runtime_state
from services.model_capabilities import apply_ollama_structured_capability, openai_response_format
from services.pipeline.contracts import parse_plan
from services.pipeline.planner_schema import build_planner_schema, planner_schema_for_state

CATS = ["general", "coder", "precision_tools"]
TOOLS = ["calculate", "subnet_calc"]


def _valid(schema, plan):
    jsonschema.validate(plan, schema)


def test_category_and_tool_are_enums_of_the_catalog():
    schema = build_planner_schema(CATS, TOOLS, wrap_object=False)
    props = schema["items"]["properties"]
    assert props["category"]["enum"] == CATS and props["mcp_tool"]["enum"] == TOOLS
    assert schema["items"]["required"] == ["task", "category"]
    assert "additionalProperties" not in schema["items"]  # id, depends_on, ... stay allowed


def test_an_invented_tool_or_category_is_rejected_by_the_schema():
    schema = build_planner_schema(CATS, TOOLS, wrap_object=False)
    ok = [{"task": "x", "category": "precision_tools", "mcp_tool": "calculate", "mcp_args": {"expression": "1+1"}, "id": "a"}]
    _valid(schema, ok)
    for bad in ({"mcp_tool": "sql_query"}, {"category": "invented"}):
        with pytest.raises(jsonschema.ValidationError):
            _valid(schema, [{**ok[0], **bad}])


def test_wrapped_variant_is_an_object_and_parse_plan_reads_it():
    schema = build_planner_schema(CATS, TOOLS, wrap_object=True)
    assert schema["type"] == "object" and schema["required"] == ["tasks"]
    plan = {"tasks": [{"task": "x", "category": "general"}]}
    _valid(schema, plan)
    assert [t.payload["task"] for t in parse_plan(json.dumps(plan)).tasks] == ["x"]


def test_empty_catalog_leaves_plain_strings_not_an_invalid_empty_enum():
    props = build_planner_schema([], [], wrap_object=False)["items"]["properties"]
    assert "enum" not in props["category"] and "enum" not in props["mcp_tool"]


def test_schema_for_state_uses_the_template_roster_and_the_request_catalog(monkeypatch):
    monkeypatch.delenv("PLANNER_SCHEMA_ENUMS", raising=False)
    state_ = {"user_experts": {"omni": [], "coder": []}, "planner_tool_names": TOOLS}
    props = planner_schema_for_state(state_, ["general", "security"], wrap_object=False)["items"]["properties"]
    assert props["category"]["enum"][:2] == ["omni", "coder"] and "security" not in props["category"]["enum"]
    assert "dynamic" not in props["category"]["enum"]  # a pinned roster is not left through the expert builder
    assert props["mcp_tool"]["enum"] == TOOLS


def test_switch_returns_the_unconstrained_schema(monkeypatch):
    monkeypatch.setenv("PLANNER_SCHEMA_ENUMS", "0")
    props = planner_schema_for_state({"planner_tool_names": TOOLS}, ["general"], wrap_object=False)["items"]["properties"]
    assert "enum" not in props["category"] and "enum" not in props["mcp_tool"]


def test_the_schema_reaches_both_provider_formats():
    schema = build_planner_schema(CATS, TOOLS, wrap_object=False)
    assert apply_ollama_structured_capability({}, "qwen3.6:35b", schema)["format"]["items"]["properties"]["category"]["enum"] == CATS
    wrapped = build_planner_schema(CATS, TOOLS, wrap_object=True)
    fmt = openai_response_format("qwen3.6:35b", wrapped, strict=False)
    assert fmt["json_schema"]["strict"] is False and fmt["json_schema"]["schema"] == wrapped
    assert openai_response_format("qwen3.6:35b", wrapped)["json_schema"]["strict"] is True  # default unchanged


@pytest.mark.asyncio
async def test_planner_node_hands_its_tool_catalog_to_the_llm_call(monkeypatch):
    monkeypatch.setattr(runtime_state, "redis_client", None)
    monkeypatch.setattr(planner, "TRIVIAL_FAST_PATH_ENABLED", False)
    monkeypatch.setattr(planner, "_record_stage", AsyncMock())
    monkeypatch.setattr(runtime_state, "MCP_TOOL_SCHEMAS", {"calculate": {"required": ["expression"], "args": {}}})
    call = AsyncMock(return_value=(SimpleNamespace(content='[{"task": "Explain the Rhine", "category": "general"}]', usage_metadata={}), False))
    monkeypatch.setattr(planner, "_invoke_planner_with_retry", call)
    await planner.planner_node({"input": "Explain the history of the Rhine river in detail", "response_id": "t", "chat_history": [],
                                "mode": "default", "complexity_level": "complex", "user_experts": {}})
    assert call.call_args.args[0]["planner_tool_names"] == ["calculate"]
