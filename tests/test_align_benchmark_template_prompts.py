"""Invariants of scripts/align_benchmark_template_prompts.py (no database access)."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "align_benchmark_template_prompts.py"


@pytest.fixture(scope="module")
def al():
    spec = importlib.util.spec_from_file_location("align_prompts", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def canon(al):
    return al.canonical_prompts()


def test_every_mapped_category_has_an_order_and_a_training_role(al, canon):
    roles, _, _ = canon
    assert set(al.CATEGORY_ROLE) == set(al.CATEGORY_ORDER)
    assert set(al.CATEGORY_ROLE.values()) <= set(roles)


def test_training_prompts_are_clean_and_long(canon):
    roles, preamble, _ = canon
    assert "<|im_" not in "".join(roles.values())
    assert all(len(v) > 200 for k, v in roles.items() if k != "judge")
    assert preamble.startswith("You are the orchestrator of MoE Sovereign")


def test_planner_prompt_is_independent_of_category_order(al, canon):
    _, preamble, planner_mod = canon
    cats = ["general", "research", "security", "governance", "data_analyst", "code_reviewer", "precision_tools", "compounding_knowledge"]
    assert al.planner_prompt(cats, preamble, planner_mod) == al.planner_prompt(list(reversed(cats)), preamble, planner_mod)


def test_long_category_names_do_not_touch_their_description(al, canon):
    _, preamble, planner_mod = canon
    text = al.planner_prompt(["general", "compounding_knowledge"], preamble, planner_mod)
    line = next(l for l in text.splitlines() if l.startswith('"compounding_knowledge"'))
    assert line.startswith('"compounding_knowledge"  ')


def test_guard_against_empty_plans_is_present(al, canon):
    _, preamble, planner_mod = canon
    assert "NEVER return an empty JSON array" in al.planner_prompt(["general"], preamble, planner_mod)


def test_unknown_category_is_rejected(al, canon):
    _, preamble, planner_mod = canon
    with pytest.raises(ValueError):
        al.planner_prompt(["general", "no_such_category"], preamble, planner_mod)
