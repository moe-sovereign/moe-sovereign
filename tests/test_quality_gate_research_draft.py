"""A quality-gate block withholds the draft from users; with research routes on it stays in state for gate-free scoring."""
import asyncio

import graph.synthesis as syn


def _block(monkeypatch, research: bool):
    monkeypatch.setattr(syn, "RESEARCH_ROUTES_ENABLED", research)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(syn, "_record_stage", _noop)
    state = {"response_id": "r1", "final_response": "The answer is 42.", "trust_verdict": "BLOCK", "plan": []}
    return asyncio.run(syn.quality_gate_node(state))


def test_block_clears_the_response_and_keeps_no_draft_in_production(monkeypatch):
    out = _block(monkeypatch, research=False)
    assert out["quality_blocked"] and out["final_response"] == "" and "quality_blocked_draft" not in out


def test_block_keeps_the_draft_for_research_forks(monkeypatch):
    out = _block(monkeypatch, research=True)
    assert out["quality_blocked"] and out["final_response"] == "" and out["quality_blocked_draft"] == "The answer is 42."
