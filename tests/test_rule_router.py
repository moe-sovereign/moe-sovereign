"""PLANNER_MODE=rule: rule-based routing instead of the LLM planner (pre-registration O2)."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import graph.planner as planner
import state as runtime_state
from services.pipeline.rule_router import route_category, rule_plan

TABLE = "The database contains the table readings(id, plant, yield_kwh, reading_date). Determine the sum of yield_kwh for plant = 'Hall'."
GRAPH = "The graph contains nodes and relationships (:Plant {name}), (:Plant)-[:LINE {meters}]->(:Plant). Which Plant nodes can be reached from 'Tower'?"
CALC = "Calculate 12.5 % of 7525 and round to two decimal places."
OTHER = "Describe the history of the Rhine river."


def test_first_matching_rule_wins():
    assert route_category(TABLE) == "data_analyst"
    assert route_category(GRAPH) == "compounding_knowledge"
    assert route_category(CALC) == "precision_tools"
    assert route_category(OTHER) == "general"
    # a request with a table and a graph (cypher_sql_calc) takes the first rule
    assert route_category(TABLE + " " + GRAPH) == "data_analyst"


def test_a_category_missing_from_the_template_falls_back():
    assert route_category(TABLE, ["general", "research"]) == "general"
    assert route_category(TABLE, ["research", "security"]) == "research"
    assert route_category(TABLE, ["data_analyst", "general"]) == "data_analyst"


def test_the_plan_is_one_task_with_the_whole_request():
    p = rule_plan(CALC, ["precision_tools", "general"])
    assert p == [{"id": "task-1", "task": CALC, "category": "precision_tools"}]


@pytest.mark.asyncio
async def test_the_planner_node_makes_no_llm_call_in_rule_mode(monkeypatch):
    monkeypatch.setenv("PLANNER_MODE", "rule")
    monkeypatch.setattr(runtime_state, "redis_client", None)
    monkeypatch.setattr(planner, "TRIVIAL_FAST_PATH_ENABLED", False)
    monkeypatch.setattr(planner, "_record_stage", AsyncMock())
    llm = AsyncMock(side_effect=AssertionError("the LLM planner must not be called"))
    monkeypatch.setattr(planner, "_invoke_planner_with_retry", llm)
    state_ = {"input": TABLE, "response_id": "t1", "chat_history": [], "mode": "default", "complexity_level": "complex",
              "user_experts": {"data_analyst": {}, "general": {}, "precision_tools": {}}}
    out = await asyncio.wait_for(planner.planner_node(state_), timeout=5)
    llm.assert_not_awaited()
    assert len(out["plan"]) == 1 and out["plan"][0]["category"] == "data_analyst" and out["plan"][0]["task"] == TABLE
