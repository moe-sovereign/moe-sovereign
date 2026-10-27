"""Tests for TASK-11: Self-Critique Iteration Loop routing logic."""
import pytest
from unittest.mock import MagicMock, patch


def _state(**kwargs):
    base = {
        "trust_verdict": "",
        "trust_score": 0.5,
        "self_critique_round": 0,
        "self_critique_max": 2,
        "max_agentic_rounds": 0,
        "agentic_iteration": 0,
        "agentic_gap": "",
    }
    base.update(kwargs)
    return base


def test_replan_has_priority_over_self_critique():
    from graph.synthesis import _should_replan
    state = _state(
        max_agentic_rounds=3,
        agentic_iteration=1,
        agentic_gap="Need more data",
        trust_verdict="PROCEED_WITH_ASSUMPTION",
        self_critique_round=0,
        self_critique_max=2,
    )
    assert _should_replan(state) == "planner"


def test_self_critique_triggers_on_assumption():
    from graph.synthesis import _should_replan
    state = _state(trust_verdict="PROCEED_WITH_ASSUMPTION", self_critique_round=0, self_critique_max=2)
    assert _should_replan(state) == "self_critique"


def test_no_self_critique_when_rounds_exhausted():
    from graph.synthesis import _should_replan
    state = _state(trust_verdict="PROCEED_WITH_ASSUMPTION", self_critique_round=2, self_critique_max=2)
    assert _should_replan(state) == "critic"


def test_no_self_critique_on_proceed():
    from graph.synthesis import _should_replan
    state = _state(trust_verdict="PROCEED", self_critique_round=0, self_critique_max=2)
    assert _should_replan(state) == "critic"


def test_self_critique_also_triggers_on_block():
    # BLOCK does not suppress or alter the response anywhere in the pipeline
    # today (services/trust_score.py's BLOCK verdict is logged only) — if it
    # skipped self-critique too, the worst-scoring responses would get LESS
    # scrutiny than the PROCEED_WITH_ASSUMPTION middle bucket. Changed
    # 2026-07-16 alongside the hallucination-risk critic check for the same
    # reason (graph/synthesis.py::critic_node).
    from graph.synthesis import _should_replan
    state = _state(trust_verdict="BLOCK", self_critique_round=0, self_critique_max=2)
    assert _should_replan(state) == "self_critique"


def test_no_self_critique_on_block_when_rounds_exhausted():
    from graph.synthesis import _should_replan
    state = _state(trust_verdict="BLOCK", self_critique_round=2, self_critique_max=2)
    assert _should_replan(state) == "critic"


def test_self_critique_round_1_still_triggers():
    from graph.synthesis import _should_replan
    state = _state(trust_verdict="PROCEED_WITH_ASSUMPTION", self_critique_round=1, self_critique_max=2)
    assert _should_replan(state) == "self_critique"


def test_second_round_skipped_without_trust_gain():
    from graph.synthesis import _should_replan
    state = _state(trust_verdict="PROCEED_WITH_ASSUMPTION", self_critique_round=1,
                   self_critique_max=2, trust_score=0.52, self_critique_prev_score=0.52)
    assert _should_replan(state) == "critic"


def test_second_round_runs_with_trust_gain():
    from graph.synthesis import _should_replan
    state = _state(trust_verdict="PROCEED_WITH_ASSUMPTION", self_critique_round=1,
                   self_critique_max=2, trust_score=0.60, self_critique_prev_score=0.52)
    assert _should_replan(state) == "self_critique"


def test_review_wave_flag_skips_self_critique():
    from graph.synthesis import _should_replan
    state = _state(trust_verdict="PROCEED_WITH_ASSUMPTION", self_critique_round=0,
                   self_critique_max=2, review_replaces_self_critique=True)
    assert _should_replan(state) == "critic"


def test_first_round_unaffected():
    from graph.synthesis import _should_replan
    state = _state(trust_verdict="PROCEED_WITH_ASSUMPTION", self_critique_round=0, self_critique_max=2)
    assert _should_replan(state) == "self_critique"
