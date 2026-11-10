"""Rollout driver against a mocked orchestrator: quality-gate and HITL outcomes are episode results, not errors."""
import asyncio
import importlib.util
import io
import json
import types
from pathlib import Path

import httpx

_PATH = Path(__file__).resolve().parents[1] / "benchmarks" / "orchestration_suite" / "rollout_driver.py"
_spec = importlib.util.spec_from_file_location("rollout_driver", _PATH)
drv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(drv)

TASK = {"id": "sql_calc-x-000000000001", "family": "sql_calc", "lang": "en",
        "prompts": {"en": "q"}, "final": {"ref": 42.0, "tol": 0.005}}
CPS = [{"checkpoint_id": "c2", "step": 2, "next": ["merger"], "values": {}},
       {"checkpoint_id": "c1", "step": 1, "next": ["planner"], "values": {}}]


def run(chat_response, fork_responses):
    forks = iter(fork_responses)
    approved = []

    def handler(req: httpx.Request):
        path = req.url.path
        if path == "/v1/chat/completions":
            thread = req.headers["x-moe-research-thread"]
            status, body = chat_response(thread)
            return httpx.Response(status, json=body)
        if path.endswith("/history"):
            return httpx.Response(200, json={"checkpoints": CPS})
        if path.endswith("/fork"):
            return httpx.Response(200, json=next(forks))
        if path.startswith("/gates/") and path.endswith("/approve"):
            approved.append(path.split("/")[2])
            return httpx.Response(200, json={"status": "approved", "response_draft": "The result is 42."})
        return httpx.Response(404)

    cli = drv.Client("http://x", "user", "admin", 5)
    cli.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    args = types.SimpleNamespace(inline=False, template="t", temperature=0.7, forks=1)
    rec = asyncio.run(drv.run_task(cli, TASK, args, asyncio.Semaphore(1), io.StringIO()))
    return rec, approved


def fork(final="", **kw):
    return {"final_checkpoint_id": "f", "final_response": final, **kw}


def test_answer_and_forks_are_scored_in_chronological_order():
    rec, _ = run(lambda t: (200, {"moe_thread_id": t, "choices": [{"message": {"content": "= 42"}}]}),
                 [fork("41"), fork("42")])
    assert rec["errors"] == [] and rec["base"]["outcome"] == "answer" and rec["base"]["correct"]
    assert [p["checkpoint_id"] for p in rec["decision_points"]] == ["c1", "c2"]
    assert [p["value"] for p in rec["decision_points"]] == [0.0, 1.0]


def test_quality_block_is_a_wrong_outcome_not_an_error():
    blocked = {"error": {"type": "quality_blocked", "code": "trust_score_block"}}
    rec, _ = run(lambda t: (422, blocked), [fork(quality_blocked=True, quality_block_reason="trust_score_block"), fork("42")])
    assert rec["errors"] == []
    assert rec["base"]["outcome"] == "blocked:trust_score_block" and not rec["base"]["correct"]
    assert rec["decision_points"][0]["forks"][0]["outcome"] == "blocked:trust_score_block"


def test_hitl_gates_are_auto_approved_and_labelled():
    rec, approved = run(lambda t: (202, {"object": "moe.hitl_gate", "gate_id": "g0"}),
                        [fork(hitl_gate_id="g1"), fork("42")])
    assert rec["errors"] == [] and approved == ["g0", "g1"]
    assert rec["base"]["outcome"] == "hitl" and rec["base"]["correct"]
    assert rec["decision_points"][0]["forks"][0]["outcome"] == "hitl"


def test_missing_thread_id_on_plain_answer_is_an_error():
    rec, _ = run(lambda t: (200, {"choices": [{"message": {"content": "42"}}]}), [])
    assert rec["base"] is None and "not honoured" in rec["errors"][0]


def test_pipeline_failures_are_wrong_outcomes():
    failed = {"error": {"type": "server_error", "code": "orchestration_failed"}}
    rec, _ = run(lambda t: (500, failed), [fork(pipeline_error="PlannerContractError: MCP tool 'sql_query' ..."), fork("42")])
    assert rec["errors"] == [] and rec["base"]["outcome"] == "failed:orchestration_failed"
    assert rec["decision_points"][0]["forks"][0]["outcome"] == "failed:PlannerContractError"
    assert rec["decision_points"][1]["value"] == 1.0


def test_timeouts_are_logged_with_their_type():
    def chat(t):
        raise httpx.ReadTimeout("")
    rec, _ = run(chat, [])
    assert rec["errors"] == ["episode: ReadTimeout: "]


def test_fork_records_keep_the_executed_plan():
    rec, _ = run(lambda t: (200, {"moe_thread_id": t, "choices": [{"message": {"content": "= 42"}}]}),
                 [fork("42", plan=[{"task": "a"}]), fork("41", plan=[{"task": "b"}])])
    assert [f["plan"] for p in rec["decision_points"] for f in p["forks"]] == [[{"task": "a"}], [{"task": "b"}]]
