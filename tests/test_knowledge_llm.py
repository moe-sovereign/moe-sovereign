"""Knowledge-model client: configuration, contract parsing, sovereignty and failure behaviour."""
import json
import time

import httpx
import pytest

from services import knowledge_llm as kl
from services.deadline import RequestDeadlineExceeded

CFG = {"model": "qwen3.6:35b", "url": "http://127.0.0.1:9/v1", "token": "t", "max_questions": 3, "timeout_s": 5.0, "share_context": False}
QS = ["What is Birch7?", "What is the capacity of Elm12? Answer with the value only."]


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _reply(answers):
    return lambda req: httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"answers": answers})}}]})


def test_config_from_state_or_environment_and_off_without_both(monkeypatch):
    for k in ("KNOWLEDGE_LLM_MODEL", "KNOWLEDGE_LLM_URL", "KNOWLEDGE_LLM_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert kl.knowledge_config({}) is None
    assert kl.knowledge_config({"knowledge_model_override": "m"}) is None  # a model without an endpoint is not configured
    cfg = kl.knowledge_config({"knowledge_model_override": "m", "knowledge_url_override": "http://h/v1", "knowledge_max_questions": 5,
                               "knowledge_share_context": True})
    assert cfg["model"] == "m" and cfg["max_questions"] == 5 and cfg["timeout_s"] == 8.0 and cfg["share_context"] is True
    monkeypatch.setenv("KNOWLEDGE_LLM_MODEL", "env-model")
    monkeypatch.setenv("KNOWLEDGE_LLM_URL", "http://e/v1")
    assert kl.knowledge_config({})["model"] == "env-model" and kl.knowledge_config({"knowledge_model_override": "m"})["model"] == "m"


def test_question_builders():
    assert kl.gap_question("Birch7") == "What is Birch7? Answer in one or two sentences."
    assert kl.verification_question("Elm12", "HAS_CAPACITY") == "What is the capacity of Elm12? Answer with the value only."


def test_parse_aligns_by_position_and_defaults_to_unknown():
    out = kl.parse_answers('noise {"answers": [{"question": "x", "answer": "A ward", "confidence": 0.8, "unknown": false, "as_of": "2025-01"}]} tail', QS)
    assert out[0]["answer"] == "A ward" and out[0]["confidence"] == 0.8 and out[0]["as_of"] == "2025-01" and out[0]["question"] == QS[0]
    assert out[1]["unknown"] is True and out[1]["answer"] == ""  # the model answered fewer questions than asked
    assert all(o["unknown"] for o in kl.parse_answers("not json at all", QS))
    clamp = kl.parse_answers(json.dumps({"answers": [{"answer": "x", "confidence": 7}, {"answer": "y", "confidence": "high"}]}), QS)
    assert clamp[0]["confidence"] == 1.0 and clamp[1]["confidence"] == 0.0


@pytest.mark.asyncio
async def test_one_call_carries_all_questions_and_returns_aligned_answers():
    seen = {}

    def handler(req):
        seen["body"], seen["url"], seen["auth"] = json.loads(req.content), str(req.url), req.headers["authorization"]
        return _reply([{"question": QS[0], "answer": "A ward", "confidence": 0.8, "unknown": False},
                       {"question": QS[1], "answer": "55", "confidence": 0.7, "unknown": False}])(req)

    out = await kl.ask_knowledge({}, QS, CFG, client=_client(handler))
    assert [o["answer"] for o in out] == ["A ward", "55"]
    assert seen["url"].endswith("/v1/chat/completions") and seen["auth"] == "Bearer t"
    user = seen["body"]["messages"][1]["content"]
    assert "1. What is Birch7?" in user and "2. What is the capacity" in user
    assert seen["body"]["model"] == "qwen3.6:35b" and seen["body"]["temperature"] == 0
    assert seen["body"]["response_format"]["type"] == "json_schema"  # qwen3.6:35b has the json_schema capability


@pytest.mark.asyncio
async def test_a_local_only_request_never_reaches_a_non_local_host():
    calls = []
    cfg = {**CFG, "url": "https://1.1.1.1/v1"}
    out = await kl.ask_knowledge({"local_only_routing": True}, QS, cfg, client=_client(lambda r: calls.append(r) or _reply([])(r)))
    assert out == [] and calls == []


@pytest.mark.asyncio
async def test_infrastructure_context_is_sent_only_to_local_hosts_and_only_on_opt_in():
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content)["messages"][1]["content"])
        return _reply([])(req)

    await kl.ask_knowledge({}, QS, {**CFG, "share_context": True}, context="SECRET FACT", client=_client(handler))
    await kl.ask_knowledge({}, QS, {**CFG, "share_context": False}, context="SECRET FACT", client=_client(handler))
    await kl.ask_knowledge({}, QS, {**CFG, "share_context": True, "url": "https://1.1.1.1/v1"}, context="SECRET FACT", client=_client(handler))
    assert "SECRET FACT" in bodies[0] and "SECRET FACT" not in bodies[1] and "SECRET FACT" not in bodies[2]


@pytest.mark.asyncio
async def test_failures_return_empty_instead_of_raising():
    assert await kl.ask_knowledge({}, QS, CFG, client=_client(lambda r: httpx.Response(500))) == []
    assert await kl.ask_knowledge({}, QS, CFG, client=_client(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")))) == []
    assert await kl.ask_knowledge({}, [], CFG) == []


@pytest.mark.asyncio
async def test_a_spent_request_deadline_still_propagates():
    with pytest.raises(RequestDeadlineExceeded):
        await kl.ask_knowledge({"request_deadline_monotonic": time.monotonic() - 1}, QS, CFG, client=_client(_reply([])))
