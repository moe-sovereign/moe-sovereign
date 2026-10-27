import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services import routing_patterns

# NOTE: tests/conftest.py stubs the entire `prometheus_client` module (so
# collection never opens real network/metrics ports), which means every
# PROM_* object across the codebase — including PROM_PATTERN_PRIOR — is a
# MagicMock, not a real Counter. `.labels(...)._value.get()` is therefore not
# a real number here; these tests instead assert on the `.labels(...).inc()`
# *call*, patching PROM_PATTERN_PRIOR directly (the only way any PROM_* call
# site is verifiable under this harness — no other test in this repo reads
# real counter values for the same reason).


@pytest.mark.asyncio
async def test_prior_increments_metric_per_outcome():
    mock_coll = MagicMock()
    mock_metric = MagicMock()

    mock_coll.query.return_value = {
        "ids": [["b:0"]], "distances": [[0.1]], "metadatas": [[{"positive": True}]],
    }
    with patch("services.routing_patterns._pattern_collection", mock_coll), \
         patch("services.routing_patterns.PROM_PATTERN_PRIOR", mock_metric):
        await routing_patterns.prior("expert", "modelA:math", [0.1], k=3, cap=3.0)
    mock_metric.labels.assert_called_once_with(namespace="expert", outcome="used")
    mock_metric.labels.return_value.inc.assert_called_once()

    mock_metric.reset_mock()
    mock_coll.query.return_value = {"ids": [[]], "distances": [[]], "metadatas": [[]]}
    with patch("services.routing_patterns._pattern_collection", mock_coll), \
         patch("services.routing_patterns.PROM_PATTERN_PRIOR", mock_metric):
        await routing_patterns.prior("expert", "modelA:math", [0.1], k=3, cap=3.0)
    mock_metric.labels.assert_called_once_with(namespace="expert", outcome="empty")

    mock_metric.reset_mock()
    with patch("services.routing_patterns._pattern_collection", None), \
         patch("services.routing_patterns.init_patterns", MagicMock()), \
         patch("services.routing_patterns.PROM_PATTERN_PRIOR", mock_metric):
        await routing_patterns.prior("gate", "research:fetch:v1", [0.1], k=3, cap=3.0)
    mock_metric.labels.assert_called_once_with(namespace="gate", outcome="unavailable")

    mock_metric.reset_mock()
    mock_coll.query.side_effect = RuntimeError("boom")
    with patch("services.routing_patterns._pattern_collection", mock_coll), \
         patch("services.routing_patterns.PROM_PATTERN_PRIOR", mock_metric):
        await routing_patterns.prior("expert", "modelA:math", [0.1], k=3, cap=3.0)
    mock_metric.labels.assert_called_once_with(namespace="expert", outcome="error")


@pytest.mark.asyncio
async def test_prior_no_embedding_does_not_touch_metric():
    mock_metric = MagicMock()
    with patch("services.routing_patterns._pattern_collection", None), \
         patch("services.routing_patterns.PROM_PATTERN_PRIOR", mock_metric):
        await routing_patterns.prior("expert", "modelA:math", None, k=3, cap=3.0)
        await routing_patterns.prior("expert", "modelA:math", [], k=3, cap=3.0)
    # No embedding means no real consultation attempt — must not be counted.
    mock_metric.labels.assert_not_called()


@pytest.mark.asyncio
async def test_prior_no_collection_returns_zero():
    with patch("services.routing_patterns._pattern_collection", None), \
         patch("services.routing_patterns.init_patterns", MagicMock()):
        pos, total = await routing_patterns.prior("expert", "modelA:math", [0.1, 0.2], k=3, cap=3.0)
        assert (pos, total) == (0.0, 0.0)


@pytest.mark.asyncio
async def test_prior_no_embedding_returns_zero():
    mock_coll = MagicMock()
    with patch("services.routing_patterns._pattern_collection", mock_coll):
        pos, total = await routing_patterns.prior("expert", "modelA:math", None, k=3, cap=3.0)
        assert (pos, total) == (0.0, 0.0)
        pos, total = await routing_patterns.prior("expert", "modelA:math", [], k=3, cap=3.0)
        assert (pos, total) == (0.0, 0.0)


@pytest.mark.asyncio
async def test_prior_weights_by_distance_and_positive_flag():
    mock_coll = MagicMock()
    mock_coll.query.return_value = {
        "ids": [["expert_modelA_math:0", "expert_modelA_math:1"]],
        "distances": [[0.2, 0.6]],
        "metadatas": [[{"positive": True}, {"positive": False}]],
    }
    with patch("services.routing_patterns._pattern_collection", mock_coll):
        pos, total = await routing_patterns.prior("expert", "modelA:math", [0.1, 0.2], k=3, cap=3.0)
        # weight = max(0, 1 - dist): 0.8 for the positive neighbor, 0.4 for the negative one
        assert total == pytest.approx(1.2)
        assert pos == pytest.approx(0.8)
        # neighbor search must be scoped to this bucket only
        _, kwargs = mock_coll.query.call_args
        assert kwargs["where"] == {"bucket": "expert:modelA_math"}


@pytest.mark.asyncio
async def test_prior_caps_pseudo_observations():
    mock_coll = MagicMock()
    # 5 close positive neighbors -> weighted_total would be 5.0 without the cap
    mock_coll.query.return_value = {
        "ids": [[f"b:{i}" for i in range(5)]],
        "distances": [[0.0] * 5],
        "metadatas": [[{"positive": True}] * 5],
    }
    with patch("services.routing_patterns._pattern_collection", mock_coll):
        pos, total = await routing_patterns.prior("expert", "modelA:math", [0.1], k=5, cap=3.0)
        assert total == pytest.approx(3.0)
        assert pos == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_prior_query_failure_is_fail_open():
    mock_coll = MagicMock()
    mock_coll.query.side_effect = RuntimeError("chroma unreachable")
    with patch("services.routing_patterns._pattern_collection", mock_coll):
        pos, total = await routing_patterns.prior("expert", "modelA:math", [0.1], k=3, cap=3.0)
        assert (pos, total) == (0.0, 0.0)


@pytest.mark.asyncio
async def test_record_upserts_with_ring_buffer_slot():
    mock_coll = MagicMock()
    mock_redis = AsyncMock()
    mock_redis.incr.return_value = 7  # e.g. 7 % ring_size
    with patch("services.routing_patterns._pattern_collection", mock_coll), \
         patch("services.routing_patterns.state") as mock_state, \
         patch("services.routing_patterns.ROUTING_PATTERN_RING_SIZE", 200):
        mock_state.redis_client = mock_redis
        await routing_patterns.record("expert", "modelA:math", [0.1, 0.2], True)

    mock_coll.upsert.assert_called_once()
    _, kwargs = mock_coll.upsert.call_args
    assert kwargs["ids"] == ["expert:modelA_math:7"]
    assert kwargs["embeddings"] == [[0.1, 0.2]]
    assert kwargs["metadatas"] == [{"bucket": "expert:modelA_math", "positive": True}]


@pytest.mark.asyncio
async def test_record_no_embedding_is_noop():
    mock_coll = MagicMock()
    with patch("services.routing_patterns._pattern_collection", mock_coll):
        await routing_patterns.record("expert", "modelA:math", None, True)
        await routing_patterns.record("expert", "modelA:math", [], True)
    mock_coll.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_record_upsert_failure_is_fail_open():
    mock_coll = MagicMock()
    mock_coll.upsert.side_effect = RuntimeError("chroma unreachable")
    with patch("services.routing_patterns._pattern_collection", mock_coll), \
         patch("services.routing_patterns.state") as mock_state:
        mock_state.redis_client = None
        # Must not raise.
        await routing_patterns.record("expert", "modelA:math", [0.1], True)
