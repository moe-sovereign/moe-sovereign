"""The compact retry prompt must keep asking for mcp_tool on precision tasks."""

from graph.planner import COMPACT_PRECISION_RULE


def test_compact_rule_requires_mcp_tool_and_args_for_precision_tasks():
    assert "precision_tools" in COMPACT_PRECISION_RULE
    assert "mcp_tool" in COMPACT_PRECISION_RULE and "mcp_args" in COMPACT_PRECISION_RULE


def test_compact_prompt_uses_the_rule():
    import inspect
    import graph.planner as planner

    assert "{COMPACT_PRECISION_RULE}" in inspect.getsource(planner.planner_node)
