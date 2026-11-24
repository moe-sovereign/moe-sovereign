import json
from unittest.mock import AsyncMock, patch

import pytest

from types import SimpleNamespace

import routes.debate_analysis as route
from services.debate_analysis.contracts import DebateAnalysisPolicy
from services.debate_analysis.rubric import load_rubric

RUBRIC = load_rubric("v1")
ENABLED = DebateAnalysisPolicy(
    activation="enabled", judge_count=1, samples_per_judge=1, max_model_calls=4
).model_dump(mode="json")
BODY = {
    "template": "t1",
    "transcript": "Anna: Ich behaupte X, weil Y.\nBernd: Das stimmt nicht, weil Z.",
    "profile": {"frame_date": "2026-09-01"},
}
JUDGE = json.dumps({"speakers": {
    s: {"criteria": {c.id: {"score": 6.0, "evidence": ["r"]} for c in RUBRIC.criteria}}
    for s in ("Anna", "Bernd")
}})


class _Client:
    """Calls the framework-free handler; the suite's conftest stubs FastAPI."""

    async def post(self, _path, json):
        body = route.DebateAnalysisRequest.model_validate(json)
        status, content, _ = await route.handle_analysis(
            body, SimpleNamespace(headers={}, client=None)
        )
        return SimpleNamespace(status_code=status, json=lambda: content, text=str(content))


def _client():
    return _Client()


@pytest.fixture
def platform(monkeypatch):
    monkeypatch.setenv("DEBATE_ANALYSIS_JUDGE_MODELS", "judge-a")

    async def fake_llm(judge, prompt):
        return JUDGE

    record = AsyncMock(return_value=True)
    with patch.object(route, "_check_ip_rate_limit", AsyncMock(return_value=True)), \
         patch.object(route, "_extract_api_key", lambda r: "k"), \
         patch.object(route, "_validate_api_key", AsyncMock(return_value={
             "user_id": "u1", "permissions_json": "{}", "user_templates_json": "{}"})), \
         patch.object(route, "make_judge_llm_call", lambda *a, **k: fake_llm), \
         patch.object(route, "record_analysis", record), \
         patch("services.routing._resolve_template_selection", return_value={
             "authorized": True, "template": {"id": "t1", "debate_analysis": ENABLED}}), \
         patch("services.routing._resolve_template_prompts", return_value={
             "enable_web_research": False}), \
         patch("services.sovereignty.resolve_local_only", return_value=False):
        yield record


@pytest.mark.asyncio
async def test_happy_path_returns_analysis_and_persists(platform):
    res = await _client().post("/v1/debate-analysis", json=BODY)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "in_development"
    assert data["analyses"][0]["rubric_version"] == "v1"
    platform.assert_awaited_once()


@pytest.mark.asyncio
async def test_unauthenticated_is_rejected():
    with patch.object(route, "_check_ip_rate_limit", AsyncMock(return_value=True)), \
         patch.object(route, "_extract_api_key", lambda r: None):
        assert (await _client().post("/v1/debate-analysis", json=BODY)).status_code == 401


@pytest.mark.asyncio
async def test_unauthorised_template_is_rejected(platform):
    with patch("services.routing._resolve_template_selection", return_value={
            "authorized": False, "template": None}):
        assert (await _client().post("/v1/debate-analysis", json=BODY)).status_code == 403


@pytest.mark.asyncio
async def test_disabled_policy_is_rejected(platform):
    with patch("services.routing._resolve_template_selection", return_value={
            "authorized": True, "template": {"id": "t1"}}):
        assert (await _client().post("/v1/debate-analysis", json=BODY)).status_code == 409


@pytest.mark.asyncio
async def test_invalid_policy_and_input_are_422(platform):
    with patch("services.routing._resolve_template_selection", return_value={
            "authorized": True, "template": {"id": "t1", "debate_analysis": {"bogus": 1}}}):
        assert (await _client().post("/v1/debate-analysis", json=BODY)).status_code == 422
    bad = dict(BODY, transcript="only one turn: x")
    assert (await _client().post("/v1/debate-analysis", json=bad)).status_code == 422
    both = dict(BODY, profiles=[{}])
    assert (await _client().post("/v1/debate-analysis", json=both)).status_code == 422


@pytest.mark.asyncio
async def test_no_judge_configured_is_409(platform, monkeypatch):
    monkeypatch.delenv("DEBATE_ANALYSIS_JUDGE_MODELS")
    assert (await _client().post("/v1/debate-analysis", json=BODY)).status_code == 409


def test_unknown_field_is_rejected_by_the_request_model():
    with pytest.raises(Exception):
        route.DebateAnalysisRequest.model_validate(dict(BODY, extra=1))
