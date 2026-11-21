"""Answer verifier: modes, thresholds, failure handling and the quality-gate integration."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

import graph.synthesis as synthesis
from services import answer_verifier as av
from services.quality_gate import evaluate_quality_gate

URL = "http://127.0.0.1:8120"
BASE = {"input": "What is 2+2?", "final_response": "4", "response_id": "t1", "trust_verdict": "PROCEED"}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k in ("VERIFIER_MODE", "VERIFIER_URL", "VERIFIER_PASS", "VERIFIER_BLOCK", "VERIFIER_TIMEOUT_S"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("services.decision_log.log_decision", lambda *a, **k: None)


def client(score=0.9, status=200, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx.Response(status, json={"scores": [score]})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def set_env(monkeypatch, mode):
    monkeypatch.setenv("VERIFIER_MODE", mode)
    monkeypatch.setenv("VERIFIER_URL", URL)


def test_modes_and_config(monkeypatch):
    assert av.verifier_mode() == "off"
    monkeypatch.setenv("VERIFIER_MODE", "GATE")
    assert av.verifier_mode() == "gate"
    monkeypatch.setenv("VERIFIER_MODE", "nonsense")
    assert av.verifier_mode() == "off"
    assert av.verifier_config() is None
    monkeypatch.setenv("VERIFIER_URL", URL + "/")
    monkeypatch.setenv("VERIFIER_PASS", "0.3")
    monkeypatch.setenv("VERIFIER_BLOCK", "0.6")  # swapped: normalised, block never above pass
    cfg = av.verifier_config()
    assert cfg["url"] == URL and cfg["block"] == 0.3 and cfg["pass"] == 0.6


def test_verdict_thresholds():
    cfg = {"block": 0.2, "pass": 0.7}
    assert [av.verdict_for(s, cfg) for s in (0.0, 0.19, 0.2, 0.69, 0.7, 1.0)] == [
        "BLOCK", "BLOCK", "PROCEED_WITH_ASSUMPTION", "PROCEED_WITH_ASSUMPTION", "PROCEED", "PROCEED"]


def test_text_format_matches_the_server():
    spec = importlib.util.spec_from_file_location("verifier_server", Path(__file__).resolve().parents[1] / "scripts" / "verifier_server.py")
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    assert server.text_of("p", "a") == av.text_of("p", "a") == "[TASK]\np\n[ANSWER]\na"
    assert server.length_order(["a", "abc", "ab"]) == [1, 2, 0]
    assert server.sigmoid(0.0) == 0.5 and server.sigmoid(-1000) == 0.0 and server.sigmoid(1000) == 1.0


@pytest.mark.asyncio
async def test_off_calls_nothing(monkeypatch):
    c = client(seen=[])
    assert await av.apply_verifier(dict(BASE), client=c) == {}
    set_env(monkeypatch, "off")
    assert await av.apply_verifier(dict(BASE), client=c) == {}


@pytest.mark.asyncio
async def test_nothing_to_score_and_no_server(monkeypatch):
    set_env(monkeypatch, "shadow")
    assert await av.apply_verifier({**BASE, "final_response": "  "}, client=client()) == {}
    monkeypatch.delenv("VERIFIER_URL")
    assert await av.apply_verifier(dict(BASE)) == {"verifier_status": "disabled"}


@pytest.mark.asyncio
async def test_shadow_records_the_score_and_sends_prompt_and_answer(monkeypatch):
    set_env(monkeypatch, "shadow")
    seen = []
    out = await av.apply_verifier(dict(BASE), client=client(0.31, seen=seen))
    assert out == {"verifier_status": "ok", "verifier_score": 0.31}  # no verdict: the gate does not use it
    assert seen == [{"items": [{"prompt": "What is 2+2?", "answer": "4"}]}]


@pytest.mark.asyncio
async def test_gate_mode_sets_the_verdict(monkeypatch):
    set_env(monkeypatch, "gate")
    for score, verdict in ((0.05, "BLOCK"), (0.5, "PROCEED_WITH_ASSUMPTION"), (0.95, "PROCEED")):
        out = await av.apply_verifier(dict(BASE), client=client(score))
        assert out["verifier_verdict"] == verdict and out["verifier_score"] == score


@pytest.mark.asyncio
async def test_failures_leave_the_gate_alone(monkeypatch):
    set_env(monkeypatch, "gate")
    assert await av.apply_verifier(dict(BASE), client=client(status=500)) == {"verifier_status": "unavailable"}

    def boom(request):
        raise httpx.ReadTimeout("slow")
    slow = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    assert await av.apply_verifier(dict(BASE), client=slow) == {"verifier_status": "timeout"}
    bad = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"oops": 1})))
    assert await av.apply_verifier(dict(BASE), client=bad) == {"verifier_status": "unavailable"}


@pytest.mark.asyncio
async def test_local_only_never_reaches_a_remote_verifier(monkeypatch):
    monkeypatch.setenv("VERIFIER_MODE", "gate")
    monkeypatch.setenv("VERIFIER_URL", "https://verifier.example.org")
    seen = []
    out = await av.apply_verifier({**BASE, "local_only_routing": True}, client=client(seen=seen))
    assert out == {"verifier_status": "unavailable"} and seen == []


GATE = {"response_id": "req-q", "user_id": "u", "input": "Analyse a complex system", "final_response": "A reasoned answer.",
        "trust_verdict": "PROCEED", "complexity_level": "moderate", "plan": [{"id": "task-1", "category": "code", "task": "analyse"}],
        "task_events": [{"task_id": "task-1", "category": "code", "status": "completed", "executor": "workers", "iteration": 0}],
        "constitution_violations": [], "enable_graphrag": False}


def test_the_gate_uses_the_verdict_only_when_present():
    assert evaluate_quality_gate(dict(GATE)).action == "pass"
    blocked = evaluate_quality_gate({**GATE, "trust_verdict": "PROCEED", "verifier_verdict": "BLOCK"})
    assert (blocked.action, blocked.reason) == ("block", "verifier_block")
    trust_blocked = evaluate_quality_gate({**GATE, "trust_verdict": "BLOCK"})
    assert (trust_blocked.action, trust_blocked.reason) == ("block", "trust_score_block")
    # in gate mode the verifier replaces the trust verdict, in both directions
    assert evaluate_quality_gate({**GATE, "trust_verdict": "BLOCK", "verifier_verdict": "PROCEED"}).action == "pass"
    # a score alone (shadow) changes nothing
    assert evaluate_quality_gate({**GATE, "trust_verdict": "BLOCK", "verifier_score": 0.99}).reason == "trust_score_block"


@pytest.mark.asyncio
async def test_the_node_wraps_the_unchanged_gate(monkeypatch):
    core = AsyncMock(return_value={"quality_blocked": False, "quality_gate_status": "passed"})
    monkeypatch.setattr(synthesis, "_quality_gate_core", core)
    state = dict(BASE)
    assert await synthesis.quality_gate_node(state) == {"quality_blocked": False, "quality_gate_status": "passed"}
    assert core.await_args.args[0] is state  # verifier off: the very same state, no copy, no extra keys
    monkeypatch.setattr(av, "apply_verifier", AsyncMock(return_value={"verifier_status": "ok", "verifier_score": 0.4, "verifier_verdict": "BLOCK"}))
    out = await synthesis.quality_gate_node(state)
    assert out["verifier_score"] == 0.4 and out["quality_gate_status"] == "passed"
    assert core.await_args.args[0]["verifier_verdict"] == "BLOCK"
