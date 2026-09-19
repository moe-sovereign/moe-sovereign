"""A template that pins its expert roster must not let the planner reach the ad-hoc "dynamic" expert builder."""

from graph.planner import _sanitize_plan

ROSTER = {"general", "security", "governance", "code_reviewer"}


def _cats(plan):
    return [t["category"] for t in plan]


def test_dynamic_is_mapped_to_general_when_the_template_pins_its_roster():
    plan = _sanitize_plan([{"task": "value a house", "category": "dynamic", "domain": "real estate"}], "q", ROSTER)
    assert _cats(plan) == ["general"]


def test_dynamic_is_kept_when_the_template_defines_it():
    plan = _sanitize_plan([{"task": "niche", "category": "dynamic"}], "q", ROSTER | {"dynamic"})
    assert _cats(plan) == ["dynamic"]


def test_dynamic_is_kept_without_a_template():
    plan = _sanitize_plan([{"task": "niche", "category": "dynamic"}], "q", None)
    assert _cats(plan) == ["dynamic"]


def test_template_categories_and_non_expert_categories_are_unchanged():
    raw = [{"task": "a", "category": "security"}, {"task": "b", "category": "precision_tools"},
           {"task": "c", "category": "research"}, {"task": "d", "category": "no_such_category"}]
    assert _cats(_sanitize_plan(raw, "q", ROSTER)) == ["security", "precision_tools", "research", "general"]
