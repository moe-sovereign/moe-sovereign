"""Routing telemetry must persist the executed plan (regression: planner_plan was always empty)."""

import json

import pytest

import telemetry


class _Conn:
    def __init__(self, sink):
        self.sink = sink

    async def execute(self, sql, params=None):
        self.sink.append((sql, params))


class _Pool:
    def __init__(self):
        self.calls = []

    def connection(self):
        pool = self

        class _Ctx:
            async def __aenter__(self_inner):
                return _Conn(pool.calls)

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()


@pytest.mark.asyncio
async def test_plan_key_from_commit_payload_is_persisted():
    pool = _Pool()
    plan = [{"id": "task-1", "task": "Implement", "category": "code_reviewer"}]
    await telemetry.record_routing_decision(
        pool, "req-1", {"input": "q", "plan": plan, "expert_models_used": ["m::code_reviewer"]}, wall_clock_ms=1234,
    )
    inserts = [c for c in pool.calls if "INSERT INTO routing_telemetry" in c[0]]
    assert inserts, "no telemetry insert executed"
    params = inserts[0][1]
    assert any(isinstance(p, str) and json.loads(p) == plan for p in params if isinstance(p, str) and p.startswith("["))
    assert 1234 in params
