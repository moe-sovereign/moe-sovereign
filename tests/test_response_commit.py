"""Quality-atomic response persistence and retry/idempotency contracts."""

from __future__ import annotations

import inspect

import pytest

import graph.synthesis as synthesis
import services.response_commit as commit


class _Redis:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def setex(self, key, ttl, value):
        self.values[key] = value

    async def delete(self, key):
        return int(self.values.pop(key, None) is not None)


def _payload():
    return commit.build_response_commit_payload(
        {
            "response_id": "req-commit-1",
            "input": "Explain the persistence contract in enough detail.",
            "final_response": "A" * 240,
            "plan": [{"id": "task-1", "category": "general"}],
            "expert_results": ["[general / model]: DETAILS:\nA\nCONFIDENCE: high"],
            "expert_models_used": ["model::general"],
        }
    )


@pytest.mark.asyncio
async def test_commit_is_logically_once_and_reused(monkeypatch):
    redis = _Redis()
    monkeypatch.setattr(commit.state, "redis_client", redis)
    calls = {"cache": 0, "episode": 0}

    async def cache_sink():
        calls["cache"] += 1

    async def episode_sink():
        calls["episode"] += 1

    async def sinks(_payload):
        return {"cache": cache_sink, "episode": episode_sink}

    monkeypatch.setattr(commit, "_sink_map", sinks)

    first = await commit.commit_response_payload(_payload())
    second = await commit.commit_response_payload(_payload())

    assert first["status"] == "complete"
    assert second["status"] == "reused"
    assert calls == {"cache": 1, "episode": 1}


@pytest.mark.asyncio
async def test_partial_retry_runs_only_failed_sink(monkeypatch):
    redis = _Redis()
    monkeypatch.setattr(commit.state, "redis_client", redis)
    calls = {"stable": 0, "flaky": 0}

    async def stable():
        calls["stable"] += 1

    async def flaky():
        calls["flaky"] += 1
        if calls["flaky"] == 1:
            raise RuntimeError("injected")

    async def sinks(_payload):
        return {"stable": stable, "flaky": flaky}

    monkeypatch.setattr(commit, "_sink_map", sinks)

    first = await commit.commit_response_payload(_payload())
    retry = await commit.commit_response_payload(_payload())

    assert first["status"] == "partial"
    assert first["errors"] == ["flaky:RuntimeError"]
    assert retry["status"] == "complete"
    assert calls == {"stable": 1, "flaky": 2}


@pytest.mark.asyncio
async def test_node_never_commits_without_explicit_quality_pass(monkeypatch):
    async def forbidden(_payload):
        raise AssertionError("sink construction must not run")

    monkeypatch.setattr(commit, "_sink_map", forbidden)
    result = await commit.response_commit_node(
        {"quality_gate_status": "blocked", "response_id": "req-blocked"}
    )

    assert result["response_commit_status"] == "blocked"
    assert result["response_commit_errors"] == [
        "response_commit_without_quality_pass"
    ]


@pytest.mark.asyncio
async def test_bound_response_hash_is_revalidated_before_commit():
    payload = _payload()
    payload["precision_bound_response_hash"] = "tampered"

    # This pure precondition fails before any runtime sink is resolved.
    result = await commit.commit_response_payload(payload)

    assert result["status"] == "blocked"
    assert result["errors"] == ["response_commit_binding_mismatch"]


@pytest.mark.asyncio
async def test_precision_response_without_binding_is_never_committed():
    payload = _payload()
    payload["precision_contract_hash"] = "a" * 64
    payload["precision_catalog_hash"] = "b" * 64

    result = await commit.commit_response_payload(payload)

    assert result["status"] == "blocked"
    assert result["errors"] == ["response_commit_unbound_precision"]


def test_precision_cache_identity_is_contract_scoped():
    payload = _payload()
    payload["precision_contract_hash"] = "a" * 64
    payload["precision_catalog_hash"] = "b" * 64

    _, key = commit._cache_ids(payload)

    assert key.startswith("moe:qcache:v2:")


def test_merger_contains_no_reusable_semantic_write_calls():
    source = inspect.getsource(synthesis.merger_node)
    forbidden = (
        "cache_collection.upsert",
        "KAFKA_TOPIC_INGEST",
        "log_episode(",
        "_store_response_metadata(",
        "_self_evaluate(",
        "_record_expert_outcome(",
        "record_attribution(",
        "process_merger_output(",
    )
    assert not [needle for needle in forbidden if needle in source]


def test_quality_router_only_selects_commit_after_pass():
    from graph.commit import _route_quality_gate

    assert _route_quality_gate({"quality_gate_status": "passed"}) == "response_commit"
    assert _route_quality_gate({"quality_gate_status": "blocked"}) == "end"
    assert _route_quality_gate({"quality_gate_status": "pending"}) == "end"


class _FailingRedis(_Redis):
    """Raises on every call -- simulates a Redis outage (e.g. Valkey's
    stop-writes-on-bgsave-error MISCONF state), not merely a missing client."""

    async def get(self, key):
        raise ConnectionError("simulated redis outage")

    async def set(self, key, value, ex=None, nx=False):
        raise ConnectionError("simulated redis outage")

    async def delete(self, key):
        raise ConnectionError("simulated redis outage")


@pytest.mark.asyncio
async def test_commit_completes_despite_redis_outage(monkeypatch):
    """response_commit_node sits last before END in the graph -- a Redis
    error here must never discard an otherwise-complete pipeline run
    (planner/expert/merger/judge already finished). Every Redis touch in
    commit_response_payload must fail open, matching the rate limiter,
    planner cache, and node-reservation checks elsewhere in the codebase."""
    monkeypatch.setattr(commit.state, "redis_client", _FailingRedis())
    calls = {"cache": 0}

    async def cache_sink():
        calls["cache"] += 1

    async def sinks(_payload):
        return {"cache": cache_sink}

    monkeypatch.setattr(commit, "_sink_map", sinks)

    result = await commit.commit_response_payload(_payload())

    assert result["status"] == "complete"
    assert calls == {"cache": 1}


@pytest.mark.asyncio
async def test_journal_read_failure_returns_empty_not_raises():
    async def _boom():
        raise ConnectionError("simulated redis outage")

    class _R:
        async def get(self, key):
            raise ConnectionError("simulated redis outage")

    import state as _state_mod
    original = _state_mod.redis_client
    _state_mod.redis_client = _R()
    try:
        journal = await commit._read_journal("moe:response_commit:some-key")
    finally:
        _state_mod.redis_client = original
    assert journal == {}


@pytest.mark.asyncio
async def test_journal_write_failure_does_not_raise():
    class _R:
        async def set(self, key, value, ex=None):
            raise ConnectionError("simulated redis outage")

    import state as _state_mod
    original = _state_mod.redis_client
    _state_mod.redis_client = _R()
    try:
        await commit._write_journal("moe:response_commit:some-key", {"status": "complete"})  # must not raise
    finally:
        _state_mod.redis_client = original


@pytest.mark.asyncio
async def test_no_cache_request_still_writes_routing_telemetry(monkeypatch):
    calls = []

    async def _fake_record(pool, request_id, payload, wall_clock_ms=0):
        calls.append((request_id, wall_clock_ms))

    import telemetry
    monkeypatch.setattr(telemetry, "record_routing_decision", _fake_record)
    response = "x" * 400
    payload = {
        "request_id": "req-nc", "final_response": response,
        "response_hash": commit.canonical_json_hash(response),
        "no_cache": True, "wall_clock_ms": 1234,
    }
    result = await commit.commit_response_payload(payload)
    assert result["status"] == "skipped"
    assert calls == [("req-nc", 1234)]


def test_elapsed_ms_from_deadline():
    import time as _time
    state_ = {"request_deadline_monotonic": _time.monotonic() + commit.ORCHESTRATION_TIMEOUT - 2.0}
    assert 1500 <= commit._elapsed_ms(state_) <= 5000
    assert commit._elapsed_ms({}) == 0
