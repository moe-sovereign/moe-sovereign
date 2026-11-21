"""MERGER_MODE (research switch, pre-registration B): how the judge uses the experts' answers. Default behaviour is unchanged."""
import pytest

from tests.test_knowledge_block_consumers import _run_merger
import graph.synthesis as synthesis

TWO = ["[m / general]: 40 pallets\nCONFIDENCE: high\nDETAILS:\nElm12 holds 40 pallets.",
       "[m / data]: 55 pallets\nCONFIDENCE: medium\nDETAILS:\nElm12 holds 55 pallets."]


def test_mode_parsing(monkeypatch):
    assert synthesis._merger_mode() == "default"
    for m in ("judge_solo", "evidence", "best_member"):
        monkeypatch.setenv("MERGER_MODE", m.upper())
        assert synthesis._merger_mode() == m
    monkeypatch.setenv("MERGER_MODE", "average")
    assert synthesis._merger_mode() == "default"


@pytest.mark.asyncio
async def test_default_prompt_is_unchanged(monkeypatch):
    judge = await _run_merger(monkeypatch, expert_results=TWO)
    p = str(judge.await_args_list[0].args[1])
    assert "EXPERT RESPONSES (2 domains" in p and "Elm12 holds 40 pallets" in p and "EXPERT HINTS" not in p


@pytest.mark.asyncio
async def test_judge_solo_hides_the_experts_but_keeps_the_request(monkeypatch):
    monkeypatch.setenv("MERGER_MODE", "judge_solo")
    p = str(( await _run_merger(monkeypatch, expert_results=TWO)).await_args_list[0].args[1])
    assert "REQUEST: What is the capacity of Elm12?" in p
    assert "Elm12 holds 40 pallets" not in p and "Elm12 holds 55 pallets" not in p and "EXPERT RESPONSES" not in p and "EXPERT HINTS" not in p
    assert p.startswith("Answer the request completely") and "integrate ALL angles" not in p


@pytest.mark.asyncio
async def test_evidence_mode_frames_the_answers_as_possibly_wrong_hints(monkeypatch):
    monkeypatch.setenv("MERGER_MODE", "evidence")
    p = str((await _run_merger(monkeypatch, expert_results=TWO)).await_args_list[0].args[1])
    assert "EXPERT HINTS" in p and "may be wrong" in p and "Elm12 holds 55 pallets" in p
    assert "confidence-weighted" not in p and "PRIMARY" not in p and "integrate ALL angles" not in p


@pytest.mark.asyncio
async def test_best_member_asks_for_one_unchanged_answer(monkeypatch):
    monkeypatch.setenv("MERGER_MODE", "best_member")
    p = str((await _run_merger(monkeypatch, expert_results=TWO)).await_args_list[0].args[1])
    assert p.startswith("You are given the request") and "Do not merge them" in p and "Elm12 holds 55 pallets" in p


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["judge_solo", "evidence", "best_member"])
async def test_the_single_expert_fast_path_is_skipped_in_every_research_mode(monkeypatch, mode):
    monkeypatch.setenv("MERGER_MODE", mode)
    judge = await _run_merger(monkeypatch)  # one confident expert: default would skip the judge
    judge.assert_awaited()
