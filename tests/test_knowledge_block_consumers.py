"""Experts and the merger see the broker's knowledge block; with it present the merger does not skip the judge."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import graph.synthesis as synthesis
from graph.expert import expert_worker

BLOCK = "[KNOWN FACTS (infrastructure, authoritative first)]\n- Elm12 HAS_CAPACITY 40 pallets (verified, fixture, 2026-11-01)"


def _msg(text):
    return SimpleNamespace(content=text, usage_metadata={"input_tokens": 1, "output_tokens": 1})


def _expert_state(**extra):
    return {"input": "What is the capacity of Elm12?", "plan": [{"category": "general", "task": "capacity of Elm12"}],
            "user_experts": {"general": [{"model": "m", "endpoint": "local", "enabled": True, "_tier": 1}]},
            "chat_history": [], "mode": "default", "conflict_registry": [], **extra}


async def _run_expert(monkeypatch, **extra):
    invoke = AsyncMock(return_value=(_msg("40 pallets"), False))
    with patch("config.JMOE_DEBATE_ENABLED", False), \
         patch("graph.expert._invoke_llm_with_fallback", invoke), \
         patch("graph.expert._get_expert_score", AsyncMock(return_value=0.8)), \
         patch("graph.expert.assign_gpu", AsyncMock(return_value=0)), \
         patch("context_budget.get_model_ctx_async", AsyncMock(return_value=32768)), \
         patch("graph.expert._select_node", AsyncMock(return_value={
             "name": "local", "url": "http://127.0.0.1:9/v1", "token": "t", "api_type": "openai", "timeout": 1})):
        await asyncio.wait_for(expert_worker(_expert_state(**extra)), timeout=3.0)
    return str(invoke.await_args.args[2])


@pytest.mark.asyncio
async def test_expert_prompt_contains_the_block_when_the_broker_found_something(monkeypatch):
    prompt = await _run_expert(monkeypatch, knowledge_block=BLOCK)
    assert "INFRASTRUCTURE KNOWLEDGE" in prompt and "Elm12 HAS_CAPACITY 40 pallets" in prompt


@pytest.mark.asyncio
async def test_expert_prompt_is_unchanged_without_a_block_or_with_the_switch_off(monkeypatch):
    assert "INFRASTRUCTURE KNOWLEDGE" not in await _run_expert(monkeypatch)
    monkeypatch.setenv("KNOWLEDGE_TO_EXPERTS", "0")
    assert "INFRASTRUCTURE KNOWLEDGE" not in await _run_expert(monkeypatch, knowledge_block=BLOCK)


def _merger_state(**extra):
    return {"input": "What is the capacity of Elm12?", "response_id": "t1", "mode": "default",
            "plan": [{"category": "general", "task": "capacity of Elm12", "id": "task-1"}],
            "expert_results": ["[m / general]: 40 pallets\nCONFIDENCE: high\nDETAILS:\nElm12 holds 40 pallets."],
            "chat_history": [], "conflict_registry": [], "graph_context": "", "web_research": "", "mcp_result": "",
            "cached_facts": "", "prompt_tokens": 0, "completion_tokens": 0, "task_events": [],
            "trust_score": 0.0, "trust_verdict": "", **extra}


async def _run_merger(monkeypatch, **extra):
    judge = AsyncMock(return_value=(_msg("Elm12 holds 40 pallets.\n\nCOMPLETION_STATUS: COMPLETE"), False))
    monkeypatch.setattr(synthesis, "_invoke_judge_with_retry", judge)
    monkeypatch.setattr(synthesis, "_record_stage", AsyncMock())
    try:
        await asyncio.wait_for(synthesis.merger_node(_merger_state(**extra)), timeout=5.0)
    except Exception as exc:  # the node needs more of the runtime than this test fakes; what matters is the judge call
        if not judge.await_args_list:
            raise AssertionError(f"merger did not reach the judge: {type(exc).__name__}: {exc}") from exc
    return judge


@pytest.mark.asyncio
async def test_merger_judge_sees_the_block_and_the_fast_path_is_not_taken(monkeypatch):
    judge = await _run_merger(monkeypatch, knowledge_block=BLOCK)
    judge.assert_awaited()
    prompt = str(judge.await_args_list[0].args[1])
    assert "INFRASTRUCTURE KNOWLEDGE" in prompt and "Elm12 HAS_CAPACITY 40 pallets" in prompt


@pytest.mark.asyncio
async def test_without_a_block_a_single_confident_expert_still_takes_the_fast_path(monkeypatch):
    judge = await _run_merger(monkeypatch)
    judge.assert_not_awaited()  # unchanged behaviour: no extra context, no judge call
