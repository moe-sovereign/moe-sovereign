"""One source for planner categories: prompt, sanitiser and (later) output schema agree."""
import graph.planner as planner
from services.pipeline.plan_categories import (
    NON_EXPERT, allowed_plan_categories, fallback_category, prompt_categories,
)

GLOBAL = ["general", "security", "coder"]


def test_template_mode_offers_exactly_the_pinned_roster_without_dynamic():
    tmpl = {"omni": {}, "research": {}, "precision_tools": {}, "coder": {}}
    assert prompt_categories(tmpl, GLOBAL) == ["omni", "coder"]  # non-expert types are not "LLM experts"
    assert "dynamic" not in allowed_plan_categories(tmpl, GLOBAL)


def test_global_mode_offers_experts_plus_dynamic_only_when_the_builder_is_enabled():
    assert prompt_categories({}, GLOBAL, expert_builder_enabled=True) == ["general", "security", "coder", "dynamic"]
    assert prompt_categories({}, GLOBAL, expert_builder_enabled=False) == GLOBAL
    assert "dynamic" in allowed_plan_categories({}, GLOBAL)


def test_agentic_coder_is_offered_in_code_modes_and_when_the_template_defines_it():
    assert "agentic_coder" in prompt_categories({"omni": {}}, GLOBAL, mode="code")
    assert "agentic_coder" in prompt_categories({"omni": {}, "agentic_coder": {}}, GLOBAL)
    assert "agentic_coder" not in prompt_categories({"omni": {}}, GLOBAL)


def test_allowed_set_always_contains_the_non_expert_types_and_special_ones():
    allowed = allowed_plan_categories({"omni": {}}, GLOBAL)
    assert set(NON_EXPERT) <= set(allowed) and {"agentic_coder", "memory_recall"} <= set(allowed)
    assert len(allowed) == len(set(allowed))


def test_fallback_is_always_an_allowed_category():
    for tmpl in ({"omni": {}}, {"general": {}, "omni": {}}, {"technical_support": {}}, {}):
        allowed = allowed_plan_categories(tmpl, GLOBAL)
        assert fallback_category(allowed) in allowed
    assert fallback_category(["omni", "coder", "precision_tools"]) == "omni"  # no general one: first real expert
    assert fallback_category(["general", "omni"]) == "general"


def test_sanitiser_rewrites_an_unknown_category_to_one_the_template_has():
    raw = [{"task": "do it", "category": "invented"}, {"task": "calc", "category": "precision_tools"}]
    out = planner._sanitize_plan(raw, "q", ["omni", "coder"])  # template order: omni first
    assert [t["category"] for t in out] == ["omni", "precision_tools"]
    # a set has no order; the result must not depend on the hash seed
    again = planner._sanitize_plan([{"task": "do it", "category": "invented"}], "q", {"omni", "coder"})
    assert again[0]["category"] == "coder"


def test_sanitiser_accepts_what_the_prompt_offers():
    for cats in ({"omni", "coder"}, None):
        offered = prompt_categories(dict.fromkeys(cats) if cats else None, GLOBAL, expert_builder_enabled=True)
        raw = [{"task": f"t-{c}", "category": c} for c in offered]
        planner_cats = {c for c in planner.EXPERTS} if cats is None else set()
        out = planner._sanitize_plan(raw, "q", cats)
        kept = [t["category"] for t in out]
        # in template mode every offered category survives; in global mode those that are real experts or dynamic do
        assert all(c in kept for c in offered if cats or c in planner_cats | {"dynamic"})
