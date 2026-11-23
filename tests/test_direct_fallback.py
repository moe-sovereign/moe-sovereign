"""Direct-judge fallback when the pipeline fails (PIPELINE_FALLBACK_DIRECT, default off)."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import services.inference as inference
from services import direct_fallback as df
from services.pipeline import chat as chat_mod


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k in ("PIPELINE_FALLBACK_DIRECT", "PIPELINE_FALLBACK_ON"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("services.decision_log.log_decision", lambda *a, **k: None)


def test_off_by_default_and_filtered_by_kind(monkeypatch):
    assert not df.fallback_enabled("error") and not df.fallback_enabled("timeout")
    monkeypatch.setenv("PIPELINE_FALLBACK_DIRECT", "1")
    assert df.fallback_enabled("error") and df.fallback_enabled("timeout")
    monkeypatch.setenv("PIPELINE_FALLBACK_ON", "error")
    assert df.fallback_enabled("error") and not df.fallback_enabled("timeout")
    monkeypatch.setenv("PIPELINE_FALLBACK_DIRECT", "true")  # only the literal 1 switches it on
    assert not df.fallback_enabled("error")


def test_judge_state_takes_the_template_overrides():
    st = df.judge_state({"judge_model_override": "m", "judge_url_override": "http://x/v1", "judge_token_override": "t", "judge_num_ctx": 65536, "other": 1}, "u1")
    assert st == {"judge_model_override": "m", "judge_url_override": "http://x/v1", "judge_token_override": "t", "judge_num_ctx": 65536, "user_id": "u1"}
    assert df.judge_state({})["judge_num_ctx"] == 0


@pytest.mark.asyncio
async def test_direct_answer_never_raises():
    ok = AsyncMock(return_value=SimpleNamespace(content="  42  "))
    assert await df.direct_answer({"user_id": "u"}, "q", ok) == "42"
    ok.assert_awaited_once()
    assert ok.await_args.kwargs == {"max_retries": 1, "temperature": 0.0}
    assert await df.direct_answer({}, "q", AsyncMock(return_value=SimpleNamespace(content="   "))) is None
    assert await df.direct_answer({}, "q", AsyncMock(side_effect=RuntimeError("judge down"))) is None


def test_payload_is_a_chat_completion_with_the_marker():
    p = df.completion_payload("chat-1", "moe-orchestrator", "ans", "error")
    assert p["object"] == "chat.completion" and p["choices"][0]["message"] == {"role": "assistant", "content": "ans"}
    assert p["moe_fallback"] == "direct_judge:error" and p["id"] == "chat-1" and p["model"] == "moe-orchestrator"


@pytest.mark.asyncio
async def test_the_chat_helper_answers_only_when_enabled(monkeypatch):
    invoke = AsyncMock(return_value=SimpleNamespace(content="direct answer"))
    monkeypatch.setattr(inference, "_invoke_judge_with_retry", invoke)
    monkeypatch.setattr(chat_mod, "JSONResponse", lambda status_code, content: SimpleNamespace(status_code=status_code, body=json.dumps(content)))  # fastapi is stubbed in the suite
    tmpl = {"judge_model_override": "m"}
    assert await chat_mod._direct_fallback_response("error", "c1", "default", "What is 2+2?", tmpl, "u") is None
    invoke.assert_not_awaited()
    monkeypatch.setenv("PIPELINE_FALLBACK_DIRECT", "1")
    resp = await chat_mod._direct_fallback_response("timeout", "c1", "default", "What is 2+2?", tmpl, "u")
    body = json.loads(resp.body)
    assert resp.status_code == 200 and body["choices"][0]["message"]["content"] == "direct answer" and body["moe_fallback"] == "direct_judge:timeout"
    assert invoke.await_args.args[1] == "What is 2+2?" and invoke.await_args.args[0]["judge_model_override"] == "m"
    monkeypatch.setattr(inference, "_invoke_judge_with_retry", AsyncMock(side_effect=RuntimeError("down")))
    assert await chat_mod._direct_fallback_response("error", "c1", "default", "q", tmpl, "u") is None  # the original 500 / 504 is returned instead


@pytest.mark.asyncio
async def test_the_response_can_carry_extra_fields_such_as_the_research_thread(monkeypatch):
    monkeypatch.setattr(inference, "_invoke_judge_with_retry", AsyncMock(return_value=SimpleNamespace(content="direct answer")))
    monkeypatch.setattr(chat_mod, "JSONResponse", lambda status_code, content: SimpleNamespace(status_code=status_code, body=json.dumps(content)))
    monkeypatch.setenv("PIPELINE_FALLBACK_DIRECT", "1")
    resp = await chat_mod._direct_fallback_response("error", "c1", "default", "q", {}, "u", {"moe_thread_id": "research-abc"})
    assert json.loads(resp.body)["moe_thread_id"] == "research-abc" and json.loads(resp.body)["moe_fallback"] == "direct_judge:error"
