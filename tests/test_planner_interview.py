"""Planner interview: the planner model chooses the questions for the knowledge model; rule-based questions are the fallback."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
import episodic_memory
import graph.knowledge_broker as kb
import services.inference as inference
from graph_rag import corrections as corrections_mod
from services.pipeline.interview import INTERVIEW_SCHEMA, build_interview_prompt, completion_for, parse_questions
from services.pipeline.planner_schema import planner_schema_for_state

LLM_STATE = {"knowledge_model_override": "qwen3.6:35b", "knowledge_url_override": "http://127.0.0.1:9/v1"}


def fact(obj="40 pallets"):
    return {"id": "g:1", "source": "graph", "subject": "Elm12", "relation": "HAS_CAPACITY", "object": obj, "text": f"Elm12 HAS_CAPACITY {obj}",
            "valid_from_ms": 1, "created_ms": 1, "verified": True, "origin": "fixture", "source_model": "", "authority": 4}


def test_prompt_carries_request_facts_gaps_and_the_budget():
    p = build_interview_prompt("Who runs Birch7 and how many beds has Elm12?", "- Elm12 HAS_BEDS 40 beds", ["Birch7"], 2)
    assert "Request: Who runs Birch7" in p and "- Elm12 HAS_BEDS 40 beds" in p and "Open gaps (terms the system knows nothing about): Birch7" in p
    assert "At most 2 short" in p and '{"questions"' in p and "never ask about private or internal data" in p
    assert "(nothing)" in build_interview_prompt("q", "", [], 3) and "(none)" in build_interview_prompt("q", "", [], 3)


def test_parse_accepts_objects_lists_noise_and_rejects_the_rest():
    assert parse_questions('x {"questions": ["a?", "b?", "a?", "  "]} y') == ["a?", "b?"]
    assert parse_questions('["only?"]') == ["only?"]
    assert parse_questions('{"questions": []}') == []  # asking nothing is a valid answer
    assert parse_questions('{"questions": ["1", "2", "3", "4"]}', 2) == ["1", "2"]
    assert parse_questions("no json") is None and parse_questions('{"questions": "text"}') is None and parse_questions("") is None


def test_completion_is_what_the_parser_reads():
    assert parse_questions(completion_for(["What is X?", "Who is Y?"])) == ["What is X?", "Who is Y?"]
    assert parse_questions(completion_for([])) == []


def test_the_interview_schema_replaces_the_plan_schema_for_that_call():
    assert planner_schema_for_state({"planner_schema_override": INTERVIEW_SCHEMA}, ["general"], wrap_object=False) == INTERVIEW_SCHEMA
    assert planner_schema_for_state({}, ["general"], wrap_object=False)["type"] == "array"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_BROKER_ENABLED", "1")
    monkeypatch.setenv("KNOWLEDGE_CONSULT_BY", "planner")
    monkeypatch.setenv("PLANNER_INTERVIEW", "1")
    monkeypatch.setattr(kb, "_record_stage", AsyncMock())
    monkeypatch.setattr(kb, "_report", AsyncMock())
    mgr = MagicMock()
    mgr.driver = object()
    mgr.query_facts = AsyncMock(return_value={"terms": ["Elm12", "Birch7"], "searched_terms": ["Elm12", "Birch7"], "found": {},
                                              "entity_hits": {"Elm12": "Depot"}, "term_hits": {"Elm12": ["Elm12"]}, "facts": [fact()],
                                              "rendered": "[Knowledge Graph]\n• Elm12 (Depot)"})
    monkeypatch.setattr(kb.state, "graph_manager", mgr)
    monkeypatch.setattr(config, "CORRECTION_MEMORY_ENABLED", False)
    monkeypatch.setattr(episodic_memory, "get_episode_hint", AsyncMock(return_value=""))
    monkeypatch.setattr(kb, "estimate_complexity", lambda q: "moderate")
    return mgr


def _planner(monkeypatch, content):
    call = AsyncMock(return_value=(SimpleNamespace(content=content, usage_metadata={}), False))
    monkeypatch.setattr(inference, "_invoke_planner_with_retry", call)
    return call


def _asker(monkeypatch):
    asked = []

    async def fake(state, questions, cfg, **kw):
        asked.append(list(questions))
        return [{"question": q, "answer": "A ward", "confidence": 0.9, "unknown": False, "as_of": ""} for q in questions]

    monkeypatch.setattr(kb, "ask_knowledge", fake)
    return asked


@pytest.mark.asyncio
async def test_the_planner_models_questions_are_asked_instead_of_the_rule_based_ones(env, monkeypatch):
    planner = _planner(monkeypatch, json.dumps({"questions": ["Who operates the Birch7 ward?"]}))
    asked = _asker(monkeypatch)
    out = await kb.knowledge_broker_node({"input": "Who runs Birch7?", "response_id": "t", "mode": "default", **LLM_STATE})
    args = planner.await_args
    assert args.args[0]["planner_schema_override"] == INTERVIEW_SCHEMA and "Open gaps (terms the system knows nothing about): Birch7" in args.args[1]
    assert "Elm12 HAS_CAPACITY 40 pallets" in args.args[1]  # the planner sees what the infrastructure already knows
    assert asked[0][0] == "Who operates the Birch7 ward?" and not any(q.startswith("What is Birch7") for q in asked[0])
    bundle = out["knowledge_bundle"]
    assert bundle["interview"] == {"source": "planner", "questions": ["Who operates the Birch7 ward?"]}


@pytest.mark.asyncio
async def test_asking_nothing_is_a_valid_interview_and_still_checks_attributes(env, monkeypatch):
    _planner(monkeypatch, '{"questions": []}')
    asked = _asker(monkeypatch)
    out = await kb.knowledge_broker_node({"input": "Sum the column", "response_id": "t", "mode": "default", **LLM_STATE})
    assert asked == [["What is the capacity of Elm12? Answer with the value only."]]  # only the verification question
    assert out["knowledge_bundle"]["interview"]["questions"] == []


@pytest.mark.asyncio
async def test_an_unusable_interview_answer_falls_back_to_the_rule_based_questions(env, monkeypatch):
    _planner(monkeypatch, "I would ask about the ward, maybe.")
    asked = _asker(monkeypatch)
    out = await kb.knowledge_broker_node({"input": "Who runs Birch7?", "response_id": "t", "mode": "default", **LLM_STATE})
    assert asked[0][0] == "What is Birch7? Answer in one or two sentences."
    assert out["knowledge_bundle"]["interview"]["source"] == "rule_fallback"


@pytest.mark.asyncio
async def test_a_failing_planner_model_falls_back_too(env, monkeypatch):
    monkeypatch.setattr(inference, "_invoke_planner_with_retry", AsyncMock(side_effect=RuntimeError("planner down")))
    asked = _asker(monkeypatch)
    out = await kb.knowledge_broker_node({"input": "Who runs Birch7?", "response_id": "t", "mode": "default", **LLM_STATE})
    assert asked[0][0].startswith("What is Birch7") and out["knowledge_bundle"]["interview"]["status"] == "error"


@pytest.mark.asyncio
async def test_without_the_interview_switch_the_planner_model_is_not_called(env, monkeypatch):
    monkeypatch.delenv("PLANNER_INTERVIEW")
    planner = _planner(monkeypatch, '{"questions": ["x?"]}')
    _asker(monkeypatch)
    await kb.knowledge_broker_node({"input": "Who runs Birch7?", "response_id": "t", "mode": "default", **LLM_STATE})
    planner.assert_not_awaited()
