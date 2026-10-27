import ast


def test_expert_module_parses_and_contains_review_wave():
    src = open("graph/expert.py", encoding="utf-8").read()
    ast.parse(src)
    assert "async def _run_review_wave(" in src
    assert "[REVIEW:" in src
    assert '"review_replaces_self_critique": bool(review_replaces_sc and review_results)' in src


def test_review_prefix_excluded_from_trust():
    from services.trust_score import _NON_EXPERT_RESULT_PREFIXES
    assert "[REVIEW:" in _NON_EXPERT_RESULT_PREFIXES


# ── behavioural tests: real expert_worker with mocked LLM calls ────────────────
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from graph.expert import expert_worker


class _Msg:
    def __init__(self, content):
        self.content = content
        self.usage_metadata = {"input_tokens": 10, "output_tokens": 20}


def _state(lens_cfg=True, replaces=False, complexity="complex"):
    primary = {"model": "coder-3b", "endpoint": "EP1", "enabled": True}
    if lens_cfg:
        primary["_review_lenses"] = ["security"]
        primary["_review_replaces_self_critique"] = replaces
    return {
        "input": "Implement a lock-free MPSC ring buffer",
        "plan": [{"id": "task-1", "category": "code_reviewer", "task": "Implement the ring buffer"}],
        "user_experts": {
            "code_reviewer": [primary],
            "security": [{"model": "security-3b", "endpoint": "EP2", "enabled": True}],
        },
        "chat_history": [],
        "mode": "default",
        "conflict_registry": [],
        "complexity_level": complexity,
    }


def _patches(responses):
    invoke = AsyncMock(side_effect=[(_Msg(r), False) for r in responses])
    return invoke, [
        patch("graph.expert._invoke_llm_with_fallback", invoke),
        patch("graph.expert._get_expert_score", AsyncMock(return_value=0.8)),
        patch("graph.expert.assign_gpu", AsyncMock(return_value=0)),
        patch("context_budget.get_model_ctx_async", AsyncMock(return_value=32768)),
        patch("graph.expert._select_node", AsyncMock(return_value={
            "name": "EP", "url": "http://test/v1", "token": "t", "api_type": "openai", "timeout": 1,
        })),
    ]


async def _run(state, responses):
    invoke, ps = _patches(responses)
    for p in ps:
        p.start()
    try:
        return await asyncio.wait_for(expert_worker(state), timeout=5.0), invoke
    finally:
        for p in ps:
            p.stop()


@pytest.mark.asyncio
async def test_review_wave_adds_marked_review_and_conflict(monkeypatch):
    monkeypatch.setenv("MOE_REVIEW_WAVE_ENABLED", "1")
    primary = "CONFIDENCE: high\nfn push() { tail.store(1, Ordering::Relaxed); }"
    review = "CONFIDENCE: high\nData race: the tail store must use Release ordering; consumers read stale slots."
    result, invoke = await _run(_state(replaces=True), [primary, review])
    assert invoke.await_count == 2
    texts = result["expert_results"]
    assert len(texts) == 2
    assert texts[0].startswith("[CODER-3B / code_reviewer]")
    assert texts[1].startswith("[REVIEW:security→code_reviewer / security]:")
    assert "Release ordering" in texts[1]
    assert result["review_replaces_self_critique"] is True
    assert any(c["category"] == "code_reviewer" and c["resolution"] == "pending" for c in result["conflict_registry"])


@pytest.mark.asyncio
async def test_review_wave_flag_false_when_template_does_not_opt_in(monkeypatch):
    monkeypatch.setenv("MOE_REVIEW_WAVE_ENABLED", "1")
    result, _ = await _run(_state(replaces=False), ["CONFIDENCE: high\nprimary answer text " * 3, "CONFIDENCE: high\nreview finding text " * 3])
    assert len(result["expert_results"]) == 2
    assert result["review_replaces_self_critique"] is False


@pytest.mark.asyncio
async def test_no_review_without_lenses(monkeypatch):
    monkeypatch.setenv("MOE_REVIEW_WAVE_ENABLED", "1")
    result, invoke = await _run(_state(lens_cfg=False), ["CONFIDENCE: high\nprimary answer text " * 3])
    assert invoke.await_count == 1
    assert len(result["expert_results"]) == 1
    assert result["review_replaces_self_critique"] is False


@pytest.mark.asyncio
async def test_kill_switch_disables_review(monkeypatch):
    monkeypatch.setenv("MOE_REVIEW_WAVE_ENABLED", "0")
    result, invoke = await _run(_state(), ["CONFIDENCE: high\nprimary answer text " * 3])
    assert invoke.await_count == 1
    assert len(result["expert_results"]) == 1


@pytest.mark.asyncio
async def test_trivial_complexity_skips_review(monkeypatch):
    monkeypatch.setenv("MOE_REVIEW_WAVE_ENABLED", "1")
    result, invoke = await _run(_state(complexity="trivial"), ["CONFIDENCE: high\nprimary answer text " * 3])
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_no_findings_review_is_dropped(monkeypatch):
    monkeypatch.setenv("MOE_REVIEW_WAVE_ENABLED", "1")
    result, invoke = await _run(_state(replaces=True), ["CONFIDENCE: high\nprimary answer text " * 3, "NO_FINDINGS"])
    assert invoke.await_count == 2
    assert len(result["expert_results"]) == 1
    assert result["review_replaces_self_critique"] is False


@pytest.mark.asyncio
async def test_one_review_call_per_lens_even_with_many_primary_tasks(monkeypatch):
    """Three code_reviewer tasks must produce ONE security review call (no new port hotspot)."""
    monkeypatch.setenv("MOE_REVIEW_WAVE_ENABLED", "1")
    state = _state(replaces=True)
    state["plan"] = [
        {"id": f"task-{i}", "category": "code_reviewer", "task": f"Implement part {i}"} for i in (1, 2, 3)
    ]
    primary = "CONFIDENCE: high\nprimary answer text for the part " * 3
    review = "CONFIDENCE: high\nFinding: missing bounds check in part handling code."
    result, invoke = await _run(state, [primary, primary, primary, review])
    assert invoke.await_count == 4  # 3 primaries + exactly 1 review
    reviews = [t for t in result["expert_results"] if t.startswith("[REVIEW:")]
    assert len(reviews) == 1
