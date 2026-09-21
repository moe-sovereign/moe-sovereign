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


def test_one_closing_brace_too_many_after_a_long_expression_is_repaired():
    raw = "[" + (TASK % "a")[:-1] + "}, " + TASK % "b" + "]"   # task a ends with an extra }
    plan = parse_plan(raw)
    assert plan.valid and [t.instruction for t in plan.tasks] == ["a", "b"]


def test_array_bracket_written_before_the_object_is_closed_is_repaired():
    raw = "[" + TASK % "a" + ', {"task": "b", "category": "precision_tools", "mcp_tool": "calculate", "mcp_args": {"expression": "2*3"}]'
    plan = parse_plan(raw)
    assert plan.valid and [t.instruction for t in plan.tasks] == ["a", "b"]


def _calc(n):
    return {"task": f"c{n}", "category": "precision_tools", "mcp_tool": "calculate", "mcp_args": {"expression": f"{n}+1"}}


def test_plan_limit_counts_model_tasks_and_lets_deterministic_mcp_calls_exceed_it():
    from services.pipeline.contracts import validate_plan_tasks

    schemas = {"calculate": {"required": ["expression"]}}
    nine_calcs = [_calc(i) for i in range(9)]
    assert not [i for i in validate_plan_tasks(nine_calcs, schemas, max_tasks=8) if i.code == "too_many_tasks"]
    too_many_models = [{"task": f"m{i}", "category": "general"} for i in range(9)]
    assert [i for i in validate_plan_tasks(too_many_models, schemas, max_tasks=8) if i.code == "too_many_tasks"]
    absurd = [_calc(i) for i in range(25)]
    assert [i for i in validate_plan_tasks(absurd, schemas, max_tasks=8) if i.code == "too_many_tasks"]
