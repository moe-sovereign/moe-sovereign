"""The planner sees the knowledge block only behind KNOWLEDGE_TO_PLANNER, in both prompt variants, and caches accordingly."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import graph.planner as planner
import state as runtime_state

BLOCK = "[KNOWN FACTS (infrastructure, authoritative first)]\n- Elm12 HAS_CAPACITY 40 pallets (verified)\n\n[OPEN GAPS]\n- Birch7"
PLAN = '[{"task": "Explain the Rhine in detail", "category": "general"}]'


class _Redis:
    def __init__(self):
        self.gets = []

    async def get(self, key):
        self.gets.append(key)
        return None

    async def setex(self, *a):
        pass


async def _plan(monkeypatch, *, complexity="complex", text="Describe the history of the Rhine river in detail", **extra):
    redis = _Redis()
    monkeypatch.setattr(runtime_state, "redis_client", redis)
    monkeypatch.setattr(planner, "TRIVIAL_FAST_PATH_ENABLED", False)
    monkeypatch.setattr(planner, "_record_stage", AsyncMock())
    llm = AsyncMock(return_value=(SimpleNamespace(content=PLAN, usage_metadata={}), False))
    monkeypatch.setattr(planner, "_invoke_planner_with_retry", llm)
    await planner.planner_node({"input": text, "response_id": "t", "chat_history": [], "mode": "default",
                                "complexity_level": complexity, "user_experts": {}, **extra})
    return llm.await_args.args[1], redis.gets[0]


@pytest.mark.asyncio
async def test_flag_off_leaves_the_prompt_and_the_cache_key_alone(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_TO_PLANNER", raising=False)
    prompt, key = await _plan(monkeypatch, knowledge_block=BLOCK, tenant_ids=["a"])
    assert "KNOWLEDGE ALREADY AVAILABLE" not in prompt
    _, key_other_tenant = await _plan(monkeypatch, knowledge_block="other", tenant_ids=["b"])
    assert key == key_other_tenant


@pytest.mark.asyncio
async def test_flag_on_puts_the_block_right_before_the_request_in_the_full_prompt(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_TO_PLANNER", "1")
    prompt, _ = await _plan(monkeypatch, knowledge_block=BLOCK)
    assert "do NOT create lookup or research tasks" in prompt and "Elm12 HAS_CAPACITY 40 pallets" in prompt
    request_at = prompt.rindex("Request: Describe the history")
    assert prompt.rindex("=== END KNOWLEDGE ===") < request_at and request_at - prompt.rindex("=== END KNOWLEDGE ===") < 40


@pytest.mark.asyncio
async def test_flag_on_also_reaches_the_compact_prompt(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_TO_PLANNER", "1")
    prompt, _ = await _plan(monkeypatch, complexity="trivial", text="Explain Docker", knowledge_block=BLOCK)
    assert "IMPORTANT: Answer EXCLUSIVELY with a JSON array" in prompt  # the compact variant
    assert "Elm12 HAS_CAPACITY 40 pallets" in prompt


@pytest.mark.asyncio
async def test_no_block_means_no_section_even_with_the_flag_on(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_TO_PLANNER", "1")
    prompt, _ = await _plan(monkeypatch)
    assert "KNOWLEDGE ALREADY AVAILABLE" not in prompt


@pytest.mark.asyncio
async def test_cache_key_changes_with_the_block_and_with_the_tenant_when_a_block_is_injected(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_TO_PLANNER", "1")
    _, k1 = await _plan(monkeypatch, knowledge_block=BLOCK, tenant_ids=["a"])
    _, k2 = await _plan(monkeypatch, knowledge_block=BLOCK + "\n- more", tenant_ids=["a"])
    _, k3 = await _plan(monkeypatch, knowledge_block=BLOCK, tenant_ids=["b"])
    _, k4 = await _plan(monkeypatch, tenant_ids=["a"])  # nothing injected: the plain key
    assert len({k1, k2, k3, k4}) == 4
