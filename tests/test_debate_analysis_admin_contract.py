from __future__ import annotations

import pytest

from admin_ui.debate_analysis_policy import validate_debate_analysis_policy
from services.debate_analysis.contracts import parse_debate_analysis_policy


def test_admin_and_runtime_policy_defaults_stay_identical():
    assert validate_debate_analysis_policy(None) == parse_debate_analysis_policy(
        None
    ).model_dump(mode="json")


def test_admin_and_runtime_normalize_same_explicit_policy():
    raw = {
        "activation": "enabled",
        "rubric_version": "v1",
        "judge_count": 2,
        "samples_per_judge": 2,
        "max_model_calls": 30,
        "max_turns": 40,
        "research_enabled": False,
        "fallback": "fail",
    }
    assert validate_debate_analysis_policy(raw) == parse_debate_analysis_policy(
        raw
    ).model_dump(mode="json")


@pytest.mark.parametrize(
    "raw",
    [
        {"activation": "sometimes"},
        {"activation": "enabled", "unknown": True},
        {"judge_count": "2"},
        {"judge_count": 3, "samples_per_judge": 5, "max_model_calls": 10},
        {"rubric_version": "../v1"},
        {"max_turns": 1},
    ],
)
def test_admin_and_runtime_both_reject_invalid_policy(raw):
    with pytest.raises(ValueError):
        validate_debate_analysis_policy(raw)
    with pytest.raises(ValueError):
        parse_debate_analysis_policy(raw)
