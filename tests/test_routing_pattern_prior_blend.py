"""Cold-start pattern-prior blending in the three Thompson bandits.

Covers services/inference.py::_get_expert_score, services/dynamic_router.py::
_get_thompson_score, and services/routing_bandit.py::decide — the flag-off
(default) path is already covered by the existing regression suites
(tests/test_dynamic_router.py, tests/test_causal_credit.py); these tests only
exercise the additive ROUTING_PATTERN_PRIOR_ENABLED=True behaviour.
"""
import pytest
from unittest.mock import AsyncMock, patch


# ---------------------------------------------------------------------------
# services/inference.py::_get_expert_score
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_expert_score_cold_start_uses_prior_when_enabled():
    from services.inference import _get_expert_score

    mock_redis = AsyncMock()
    mock_redis.hgetall.return_value = {"total": "2", "positive": "1"}  # below EXPERT_MIN_DATAPOINTS=5

    with patch("services.inference.state") as mock_state, \
         patch("services.inference.THOMPSON_SAMPLING_ENABLED", True), \
         patch("services.inference.ROUTING_PATTERN_PRIOR_ENABLED", True), \
         patch("services.inference.routing_patterns.prior", AsyncMock(return_value=(2.0, 3.0))), \
         patch("services.inference._get_model_node_load", return_value=0.0), \
         patch("random.betavariate", return_value=0.77) as mock_beta:
        mock_state.redis_client = mock_redis
        score = await _get_expert_score("modelA", "math", query_embedding=[0.1, 0.2])

    assert score == 0.77
    # alpha = positive(1) + prior_pos(2) + 1 = 4; beta = (2-1) + (3-2) + 1 = 3
    mock_beta.assert_called_once_with(4, 3)


@pytest.mark.asyncio
async def test_get_expert_score_cold_start_falls_back_to_uniform_without_neighbors():
    from services.inference import _get_expert_score

    mock_redis = AsyncMock()
    mock_redis.hgetall.return_value = {"total": "2", "positive": "1"}

    with patch("services.inference.state") as mock_state, \
         patch("services.inference.THOMPSON_SAMPLING_ENABLED", True), \
         patch("services.inference.ROUTING_PATTERN_PRIOR_ENABLED", True), \
         patch("services.inference.routing_patterns.prior", AsyncMock(return_value=(0.0, 0.0))):
        mock_state.redis_client = mock_redis
        score = await _get_expert_score("modelA", "math", query_embedding=[0.1, 0.2])

    assert score == 0.5


@pytest.mark.asyncio
async def test_get_expert_score_cold_start_ignores_prior_when_flag_disabled():
    from services.inference import _get_expert_score

    mock_redis = AsyncMock()
    mock_redis.hgetall.return_value = {"total": "2", "positive": "1"}

    with patch("services.inference.state") as mock_state, \
         patch("services.inference.THOMPSON_SAMPLING_ENABLED", True), \
         patch("services.inference.ROUTING_PATTERN_PRIOR_ENABLED", False), \
         patch("services.inference.routing_patterns.prior", AsyncMock(return_value=(2.0, 3.0))) as mock_prior:
        mock_state.redis_client = mock_redis
        score = await _get_expert_score("modelA", "math", query_embedding=[0.1, 0.2])

    assert score == 0.5
    mock_prior.assert_not_called()


@pytest.mark.asyncio
async def test_get_expert_score_warm_bucket_never_consults_prior():
    from services.inference import _get_expert_score

    mock_redis = AsyncMock()
    mock_redis.hgetall.return_value = {"total": "50", "positive": "40"}  # well above MIN_DATAPOINTS

    with patch("services.inference.state") as mock_state, \
         patch("services.inference.THOMPSON_SAMPLING_ENABLED", True), \
         patch("services.inference.ROUTING_PATTERN_PRIOR_ENABLED", True), \
         patch("services.inference.routing_patterns.prior", AsyncMock()) as mock_prior, \
         patch("services.inference._get_model_node_load", return_value=0.0):
        mock_state.redis_client = mock_redis
        await _get_expert_score("modelA", "math", query_embedding=[0.1, 0.2])

    mock_prior.assert_not_called()


# ---------------------------------------------------------------------------
# services/dynamic_router.py::_get_thompson_score
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_thompson_score_cold_start_uses_prior_when_enabled():
    from services.dynamic_router import _get_thompson_score

    mock_redis = AsyncMock()
    mock_redis.hgetall.return_value = {"total": "1", "positive": "0"}
    mock_redis.get.return_value = None  # no node-load entry

    with patch("services.dynamic_router.state") as mock_state, \
         patch("services.dynamic_router.ROUTING_PATTERN_PRIOR_ENABLED", True), \
         patch("services.dynamic_router.routing_patterns.prior", AsyncMock(return_value=(1.5, 2.0))), \
         patch("random.betavariate", return_value=0.9) as mock_beta:
        mock_state.redis_client = mock_redis
        score = await _get_thompson_score("modelA", "math", query_embedding=[0.1])

    assert score == 0.9
    # alpha = 0 + 1.5 + 1 = 2.5; beta = (1-0) + (2.0-1.5) + 1 = 2.5; no node load
    mock_beta.assert_called_once_with(2.5, 2.5)


@pytest.mark.asyncio
async def test_thompson_score_cold_start_returns_uniform_when_flag_off():
    from services.dynamic_router import _get_thompson_score

    mock_redis = AsyncMock()
    mock_redis.hgetall.return_value = {"total": "1", "positive": "0"}

    with patch("services.dynamic_router.state") as mock_state, \
         patch("services.dynamic_router.ROUTING_PATTERN_PRIOR_ENABLED", False), \
         patch("services.dynamic_router.routing_patterns.prior", AsyncMock()) as mock_prior:
        mock_state.redis_client = mock_redis
        score = await _get_thompson_score("modelA", "math", query_embedding=[0.1])

    assert score == 0.5
    mock_prior.assert_not_called()


# ---------------------------------------------------------------------------
# services/routing_bandit.py::decide
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_routing_bandit_decide_promotes_arm_out_of_cold_start_via_prior():
    from services import routing_bandit

    async def fake_arm_stats(gate, context, action):
        # "fetch" arm has plenty of real data; "skip" arm is still cold.
        if action == "fetch":
            return 15, 25  # positive, total >= MIN_DATAPOINTS(20)
        return 1, 2  # positive, total < MIN_DATAPOINTS

    async def fake_prior(namespace, key, embedding, k, cap):
        assert namespace == "gate"
        return (2.0, 3.0)

    with patch("services.routing_bandit.state") as mock_state, \
         patch("services.routing_bandit.ROUTING_BANDIT_ENABLED", True), \
         patch("services.routing_bandit._arm_stats", fake_arm_stats), \
         patch("services.routing_bandit.ROUTING_PATTERN_PRIOR_ENABLED", True), \
         patch("services.routing_bandit.routing_patterns.prior", fake_prior), \
         patch("random.betavariate", return_value=0.5):
        mock_state.redis_client = object()  # only needs to be non-None
        run_rich, source = await routing_bandit.decide("research", "moderate|v1", True, query_embedding=[0.1])

    assert source == "bandit"


@pytest.mark.asyncio
async def test_routing_bandit_decide_stays_heuristic_when_prior_finds_nothing():
    from services import routing_bandit

    async def fake_arm_stats(gate, context, action):
        return 0, 0  # both arms fully cold

    async def fake_prior(namespace, key, embedding, k, cap):
        return (0.0, 0.0)  # no neighbors

    with patch("services.routing_bandit.state") as mock_state, \
         patch("services.routing_bandit.ROUTING_BANDIT_ENABLED", True), \
         patch("services.routing_bandit._arm_stats", fake_arm_stats), \
         patch("services.routing_bandit.ROUTING_PATTERN_PRIOR_ENABLED", True), \
         patch("services.routing_bandit.routing_patterns.prior", fake_prior):
        mock_state.redis_client = object()
        run_rich, source = await routing_bandit.decide("research", "moderate|v1", True, query_embedding=[0.1])

    assert (run_rich, source) == (True, "heuristic")


@pytest.mark.asyncio
async def test_routing_bandit_decide_unchanged_when_flag_disabled():
    from services import routing_bandit

    async def fake_arm_stats(gate, context, action):
        return 15, 25  # both arms actually warm

    with patch("services.routing_bandit.state") as mock_state, \
         patch("services.routing_bandit.ROUTING_BANDIT_ENABLED", True), \
         patch("services.routing_bandit._arm_stats", fake_arm_stats), \
         patch("services.routing_bandit.ROUTING_PATTERN_PRIOR_ENABLED", False), \
         patch("services.routing_bandit.routing_patterns.prior", AsyncMock()) as mock_prior, \
         patch("random.betavariate", return_value=0.5):
        mock_state.redis_client = object()
        run_rich, source = await routing_bandit.decide("research", "moderate|v1", False, query_embedding=[0.1])

    assert source == "bandit"
    mock_prior.assert_not_called()
