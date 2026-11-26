"""
tests/test_tracking.py — services.tracking._record_stage unit tests.

Covers the fire-and-forget pipeline-stage trace helper used by the
live-pipeline-visualization diagram (Redis list moe:active:{chat_id}:trace,
bounded to 30 entries, TTL refreshed on every write).
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import state
from services.tracking import (
    _record_stage, _record_file_touch,
    _pstop_key, _record_premature_stop_outcome, _get_premature_stop_rate,
    _PSTOP_MIN_SAMPLES,
)


def _mock_redis_with_pipeline():
    mock_pipe = MagicMock()
    mock_pipe.execute = AsyncMock()
    mock_redis = MagicMock()
    mock_redis.pipeline = MagicMock(return_value=mock_pipe)
    return mock_redis, mock_pipe


@pytest.mark.asyncio
async def test_record_stage_writes_rpush_ltrim_expire():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    with patch.object(state, "redis_client", mock_redis):
        await _record_stage("chat123", "cache", "hit_l0", "detail-text")

    mock_redis.pipeline.assert_called_once()
    assert mock_pipe.rpush.call_count == 1
    key, entry_json = mock_pipe.rpush.call_args[0]
    assert key == "moe:active:chat123:trace"
    entry = json.loads(entry_json)
    assert entry["stage"] == "cache"
    assert entry["status"] == "hit_l0"
    assert entry["detail"] == "detail-text"
    assert isinstance(entry["ts"], float)

    # Bounded to the 30 most recent entries
    mock_pipe.ltrim.assert_called_once_with("moe:active:chat123:trace", -30, -1)
    # TTL refreshed on every write, matching moe:active:{chat_id}'s 7200s TTL
    mock_pipe.expire.assert_called_once_with("moe:active:chat123:trace", 7200)
    mock_pipe.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_stage_default_status_started():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    with patch.object(state, "redis_client", mock_redis):
        await _record_stage("chat123", "planner")

    entry = json.loads(mock_pipe.rpush.call_args[0][1])
    assert entry["status"] == "started"
    assert entry["detail"] == ""


@pytest.mark.asyncio
async def test_record_stage_noop_without_chat_id():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    with patch.object(state, "redis_client", mock_redis):
        await _record_stage("", "cache", "started")

    mock_redis.pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_record_stage_noop_without_redis_client():
    with patch.object(state, "redis_client", None):
        # Must not raise even though there is no Redis connection.
        await _record_stage("chat123", "cache", "started")


@pytest.mark.asyncio
async def test_record_stage_never_raises_on_redis_error():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    mock_pipe.execute.side_effect = ConnectionError("redis down")
    with patch.object(state, "redis_client", mock_redis):
        # Fire-and-forget: errors are swallowed (logged, not raised).
        await _record_stage("chat123", "cache", "started")


# ── _record_file_touch — live-pipeline-visualization "which files did this
# Agent Tool Path session touch" view (Redis list moe:active:{chat_id}:files,
# bounded to 200 entries — a long coding session easily touches more files
# than pipeline stages) ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_file_touch_writes_rpush_ltrim_expire():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    with patch.object(state, "redis_client", mock_redis):
        await _record_file_touch("chat123", "/repo/main.py", "write", "Edit")

    key, entry_json = mock_pipe.rpush.call_args[0]
    assert key == "moe:active:chat123:files"
    entry = json.loads(entry_json)
    assert entry["path"] == "/repo/main.py"
    assert entry["action"] == "write"
    assert entry["tool"] == "Edit"
    assert isinstance(entry["ts"], float)

    mock_pipe.ltrim.assert_called_once_with("moe:active:chat123:files", -200, -1)
    mock_pipe.expire.assert_called_once_with("moe:active:chat123:files", 7200)
    mock_pipe.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_file_touch_noop_without_path():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    with patch.object(state, "redis_client", mock_redis):
        await _record_file_touch("chat123", "", "read", "Read")

    mock_redis.pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_record_file_touch_never_raises_on_redis_error():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    mock_pipe.execute.side_effect = ConnectionError("redis down")
    with patch.object(state, "redis_client", mock_redis):
        await _record_file_touch("chat123", "/x.py", "read", "Read")


# ── _record_premature_stop_outcome / _get_premature_stop_rate — persists the
# looks_like_premature_stop() signal per (model, node) pair so a future
# _select_node() weighting can avoid a node/model combo that keeps stalling.
# Same Redis-hash counter shape as services/inference.py's
# moe:perf:{model}:{category} Thompson-sampling counters.

def test_pstop_key_sanitizes_model_and_node():
    key = _pstop_key("qwen3.6:35b", "http://192.168.155.224:11434")
    assert key == "moe:pstop:qwen3_6_35b:http:__192.168.155.224:11434"


def test_pstop_key_same_node_different_url_forms_collapse():
    # _norm_base_url should make "http://host:port" and "http://host:port/v1"
    # collide on the same key, same as _latency_key already relies on.
    key_a = _pstop_key("qwen3.6:35b", "http://192.168.155.224:11434")
    key_b = _pstop_key("qwen3.6:35b", "http://192.168.155.224:11434/v1")
    assert key_a == key_b


@pytest.mark.asyncio
async def test_record_premature_stop_outcome_increments_total_and_premature():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    with patch.object(state, "redis_client", mock_redis):
        await _record_premature_stop_outcome("qwen3.6:35b", "http://192.168.155.224:11434", True)

    key = _pstop_key("qwen3.6:35b", "http://192.168.155.224:11434")
    mock_pipe.hincrby.assert_any_call(key, "total", 1)
    mock_pipe.hincrby.assert_any_call(key, "premature", 1)
    mock_pipe.expire.assert_called_once()
    mock_pipe.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_premature_stop_outcome_clean_turn_only_increments_total():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    with patch.object(state, "redis_client", mock_redis):
        await _record_premature_stop_outcome("qwen3.6:35b", "http://192.168.155.224:11434", False)

    key = _pstop_key("qwen3.6:35b", "http://192.168.155.224:11434")
    mock_pipe.hincrby.assert_called_once_with(key, "total", 1)


@pytest.mark.asyncio
async def test_record_premature_stop_outcome_noop_without_model_or_node():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    with patch.object(state, "redis_client", mock_redis):
        await _record_premature_stop_outcome("", "http://x:11434", True)
        await _record_premature_stop_outcome("qwen3.6:35b", "", True)
    mock_redis.pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_record_premature_stop_outcome_never_raises_on_redis_error():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    mock_pipe.execute.side_effect = ConnectionError("redis down")
    with patch.object(state, "redis_client", mock_redis):
        await _record_premature_stop_outcome("qwen3.6:35b", "http://x:11434", True)


@pytest.mark.asyncio
async def test_get_premature_stop_rate_optimistic_below_min_samples():
    mock_redis = MagicMock()
    mock_redis.hgetall = AsyncMock(return_value={"total": str(_PSTOP_MIN_SAMPLES - 1), "premature": "1"})
    with patch.object(state, "redis_client", mock_redis):
        rate = await _get_premature_stop_rate("qwen3.6:35b", "http://x:11434")
    assert rate == 0.0


@pytest.mark.asyncio
async def test_get_premature_stop_rate_computes_fraction_once_enough_samples():
    mock_redis = MagicMock()
    mock_redis.hgetall = AsyncMock(return_value={"total": "10", "premature": "3"})
    with patch.object(state, "redis_client", mock_redis):
        rate = await _get_premature_stop_rate("qwen3.6:35b", "http://x:11434")
    assert rate == 0.3


@pytest.mark.asyncio
async def test_get_premature_stop_rate_no_data_returns_zero():
    mock_redis = MagicMock()
    mock_redis.hgetall = AsyncMock(return_value={})
    with patch.object(state, "redis_client", mock_redis):
        rate = await _get_premature_stop_rate("qwen3.6:35b", "http://x:11434")
    assert rate == 0.0


@pytest.mark.asyncio
async def test_get_premature_stop_rate_never_raises_on_redis_error():
    mock_redis = MagicMock()
    mock_redis.hgetall = AsyncMock(side_effect=ConnectionError("redis down"))
    with patch.object(state, "redis_client", mock_redis):
        rate = await _get_premature_stop_rate("qwen3.6:35b", "http://x:11434")
    assert rate == 0.0


# ── _check_ip_rate_limit: counter must always carry an expiry ───────────────

class _FakeRedis:
    """Minimal in-memory Redis: SET NX EX, INCR, TTL, EXPIRE, MULTI pipeline."""

    def __init__(self):
        self.values = {}
        self.ttls = {}

    def pipeline(self, transaction=True):
        return _FakePipe(self)

    async def expire(self, key, seconds):
        self.ttls[key] = seconds


class _FakePipe:
    def __init__(self, redis):
        self.redis = redis
        self.ops = []

    def set(self, key, value, ex=None, nx=False):
        self.ops.append(("set", key, value, ex, nx))

    def incr(self, key):
        self.ops.append(("incr", key))

    def ttl(self, key):
        self.ops.append(("ttl", key))

    async def execute(self):
        out = []
        for op in self.ops:
            if op[0] == "set":
                _, key, value, ex, nx = op
                if nx and key in self.redis.values:
                    out.append(None)
                else:
                    self.redis.values[key] = value
                    self.redis.ttls[key] = ex if ex is not None else -1
                    out.append(True)
            elif op[0] == "incr":
                self.redis.values[op[1]] += 1
                out.append(self.redis.values[op[1]])
            else:
                out.append(self.redis.ttls.get(op[1], -2))
        return out


def _request(ip="10.0.0.5"):
    req = MagicMock()
    req.headers = {}
    req.client.host = ip
    return req


@pytest.mark.asyncio
async def test_ip_rate_limit_counter_always_has_expiry():
    from services.tracking import _check_ip_rate_limit
    fake = _FakeRedis()
    with patch.object(state, "redis_client", fake):
        assert await _check_ip_rate_limit(_request()) is True
    assert fake.values["moe:ratelimit:ip:10.0.0.5"] == 1
    assert fake.ttls["moe:ratelimit:ip:10.0.0.5"] == 60


@pytest.mark.asyncio
async def test_ip_rate_limit_blocks_above_threshold():
    from services.tracking import _check_ip_rate_limit
    import services.tracking as tr
    fake = _FakeRedis()
    with patch.object(state, "redis_client", fake), \
         patch.object(tr, "MAX_REQUESTS_PER_MINUTE", 3):
        results = [await _check_ip_rate_limit(_request()) for _ in range(5)]
    assert results == [True, True, True, False, False]


@pytest.mark.asyncio
async def test_ip_rate_limit_heals_counter_without_expiry():
    from services.tracking import _check_ip_rate_limit
    fake = _FakeRedis()
    fake.values["moe:ratelimit:ip:10.0.0.5"] = 686
    fake.ttls["moe:ratelimit:ip:10.0.0.5"] = -1   # legacy stuck counter
    with patch.object(state, "redis_client", fake):
        await _check_ip_rate_limit(_request())
    assert fake.ttls["moe:ratelimit:ip:10.0.0.5"] == 60


@pytest.mark.asyncio
async def test_ip_rate_limit_fails_open_on_redis_error():
    from services.tracking import _check_ip_rate_limit
    mock_redis = MagicMock()
    mock_redis.pipeline.side_effect = RuntimeError("down")
    with patch.object(state, "redis_client", mock_redis):
        assert await _check_ip_rate_limit(_request()) is True
