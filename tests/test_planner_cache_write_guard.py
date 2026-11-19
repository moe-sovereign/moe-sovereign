"""The plan cache must only be written for a first-pass plan: a replan plan is built for a gap context and `no_cache`
requests must leave no trace in the cache."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import graph.planner as planner
import state as runtime_state

PLAN_JSON = json.dumps([{"task": "Describe the history of the Rhine river in detail", "category": "general"}])


class _FakeRedis:
    def __init__(self):
        self.setex_calls = []

    async def get(self, key):
        return None

    async def setex(self, key, ttl, value):
        self.setex_calls.append((key, ttl, value))


def _run(monkeypatch, **extra):
    redis = _FakeRedis()
    monkeypatch.setattr(runtime_state, "redis_client", redis)
    monkeypatch.setattr(planner, "TRIVIAL_FAST_PATH_ENABLED", False)
    monkeypatch.setattr(planner, "_record_stage", AsyncMock())
    monkeypatch.setattr(
        planner, "_invoke_planner_with_retry",
        AsyncMock(return_value=(SimpleNamespace(content=PLAN_JSON, usage_metadata={}), False)),
    )
    state_ = {"input": "Describe the history of the Rhine river in detail", "response_id": "t1", "chat_history": [],
              "mode": "default", "complexity_level": "complex", "user_experts": {}, **extra}
    out = asyncio.run(planner.planner_node(state_))

    async def _drain():  # the write is scheduled with create_task
        await asyncio.sleep(0)
    asyncio.run(_drain())
    return out, redis


def test_first_pass_plan_is_cached(monkeypatch):
    out, redis = _run(monkeypatch)
    assert out["plan"] and len(redis.setex_calls) == 1


def test_agentic_replan_does_not_write_the_cache(monkeypatch):
    out, redis = _run(monkeypatch, agentic_iteration=1, max_agentic_rounds=2, agentic_gap="missing figure")
    assert out["plan"] and redis.setex_calls == []


def test_no_cache_request_does_not_write_the_cache(monkeypatch):
    out, redis = _run(monkeypatch, no_cache=True)
    assert out["plan"] and redis.setex_calls == []
