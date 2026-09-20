"""A malformed planner array must not shrink to its first task."""

from services.pipeline.contracts import parse_plan

TASK = '{"task": "%s", "category": "precision_tools", "mcp_tool": "calculate", "mcp_args": {"expression": "1+1"}}'


def test_truncated_array_is_repaired_and_keeps_all_tasks():
    raw = "[" + ", ".join(TASK % n for n in ("a", "b", "c")) + ""   # closing bracket missing
    plan = parse_plan(raw)
    assert plan.valid and [t.instruction for t in plan.tasks] == ["a", "b", "c"]


def test_valid_array_is_unchanged():
    raw = "[" + ", ".join(TASK % n for n in ("a", "b")) + "]"
    assert len(parse_plan(raw).tasks) == 2


def test_array_with_a_broken_inner_object_is_rejected_not_reduced_to_the_first_task():
    raw = "[" + TASK % "a" + ', {"task": "b", "mcp_args": {"operands": [result1, result2]}}]'
    plan = parse_plan(raw)
    assert not plan.valid and plan.tasks == []


def test_array_cut_inside_a_string_is_rejected():
    raw = "[" + TASK % "a" + ', {"task": "b", "category": "prec'
    assert not parse_plan(raw).valid


def test_single_object_and_wrapped_plans_still_parse():
    assert len(parse_plan(TASK % "solo").tasks) == 1
    assert len(parse_plan('{"tasks": [%s, %s]}' % (TASK % "a", TASK % "b")).tasks) == 2
    assert len(parse_plan("Plan:\n```json\n[%s]\n```" % (TASK % "a")).tasks) == 1
