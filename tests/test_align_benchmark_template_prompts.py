"""Invariants of scripts/align_benchmark_template_prompts.py (no database access)."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "align_benchmark_template_prompts.py"
SPUR1 = ["general", "research", "security", "governance", "data_analyst", "code_reviewer", "precision_tools", "compounding_knowledge"]


@pytest.fixture(scope="module")
def al():
    spec = importlib.util.spec_from_file_location("align_prompts", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_mapped_category_has_an_order_a_description_and_a_training_role(al):
    roles = al.canonical_prompts()
    assert set(al.CATEGORY_ROLE) == set(al.CATEGORY_ORDER)
    assert set(al.CATEGORY_ROLE.values()) <= set(roles)
    assert set(al.PLANNER_DESCRIPTIONS) == set(al.CATEGORY_ORDER) - {"dynamic"}


def test_training_prompts_are_clean_and_long(al):
    roles = al.canonical_prompts()
    assert "<|im_" not in "".join(roles.values())
    assert all(len(v) > 200 for k, v in roles.items() if k != "judge")
    assert roles["judge"].startswith("You are the MoE Sovereign Paraconsistent Quality Gate")


def test_planner_prompt_is_independent_of_category_order(al):
    assert al.planner_prompt(SPUR1) == al.planner_prompt(list(reversed(SPUR1)))


def test_spur1_planner_prompt_is_the_validated_original(al):
    """The A/B-validated original prompt (1635 chars): list format, original order, empty-plan guard."""
    text = al.planner_prompt(SPUR1)
    assert len(text) == 1635
    assert text.startswith("You are a specialized planner model in a Mixture of Experts (MoE) system.")
    order = [line.split(":")[0][2:] for line in text.splitlines() if line.startswith("- ")]
    assert order == ["general", "security", "research", "governance", "compounding_knowledge", "precision_tools", "data_analyst", "code_reviewer"]
    assert "NEVER return an empty JSON array" in text
    assert "MULTI-DISCIPLINARY" not in text


def test_planner_prompt_lists_every_category_but_dynamic(al):
    text = al.planner_prompt(list(al.CATEGORY_ORDER))
    for cat in al.CATEGORY_ORDER:
        assert (f"- {cat}:" in text) == (cat != "dynamic")


def test_unknown_category_is_rejected(al):
    with pytest.raises(ValueError):
        al.planner_prompt(["general", "no_such_category"])


def test_expert_set_is_the_eight_spur1_categories(al):
    assert len(al.EXPERT_SET) == 8
    assert set(al.EXPERT_SET) == set(SPUR1)
    assert set(al.EXPERT_SET) <= set(al.CATEGORY_ROLE)


def test_fifteen_expert_template_is_reduced_to_the_eight(al):
    fifteen = {c: {"models": [c]} for c in [
        "code_reviewer", "systems_programming", "research", "web_researcher", "general", "reasoning", "math", "data_analyst",
        "tool_expert", "technical_support", "dynamic", "security", "governance", "science", "graphrag"]}
    out = al.reduce_to_expert_set(fifteen, "t")
    assert list(out) == list(al.EXPERT_SET)
    assert out["precision_tools"] is fifteen["tool_expert"] and out["compounding_knowledge"] is fifteen["graphrag"]
    assert al.planner_prompt(list(out)) == al.planner_prompt(SPUR1)


def test_template_that_already_has_the_eight_is_untouched(al):
    eight = {c: {} for c in SPUR1}
    assert al.reduce_to_expert_set(eight, "t") is eight


def test_missing_source_expert_is_rejected(al):
    with pytest.raises(SystemExit):
        al.reduce_to_expert_set({"general": {}}, "t")


def test_instance_placement_is_one_expert_per_ascending_m60_instance(al):
    assert set(al.EXPERT_ENDPOINTS) == set(SPUR1)
    ports = sorted(int(v.rsplit("-", 1)[1]) for v in al.EXPERT_ENDPOINTS.values())
    assert ports == list(range(2, 10)) and len(set(al.EXPERT_ENDPOINTS.values())) == 8
    assert (al.PLANNER_ENDPOINT, al.JUDGE_ENDPOINT) == ("N04-RGTX", "N04-RTX")


def test_context_windows_follow_the_per_model_maximum_rule(al):
    assert al.CONTEXT["spur1"] == {"expert": 65536, "planner": 65536, "judge": 65536}   # OLMo 3 / 3.1 native maximum, SmolLM3 native maximum
    assert al.CONTEXT["spur2"] == {"expert": 98304, "planner": 262144, "judge": 262144}  # Qwen3.5/3.8 native maximum, expert = VRAM limit
    assert al.track_of("LUMI-G Base (Pre-Finetune)") == "spur1" and al.track_of("Open-Weight Finetuned Ensemble") == "spur2"
