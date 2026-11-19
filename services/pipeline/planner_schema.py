"""JSON schema that constrains a planner's output to the real categories and the discovered tool catalog.

The schema used to be `[{task: string, category: string}]`: a fine-tuned planner that had learned a catalog with a `sql_query`
tool kept writing it into plans although the stack's catalog had none (5.8 % of the held-out episodes failed at the plan
contract). With an `enum` for `category` and `mcp_tool` the decoder cannot produce a name that is not in the catalog; the
contract in services/pipeline/contracts.py stays the second line of defence.

Two shapes, because the two call paths differ:
  - native Ollama `format` takes the array as it is (`wrap_object=False`);
  - OpenAI-compatible `response_format=json_schema` wants an object at the top level (`wrap_object=True`), the plan is then
    `{"tasks": [...]}`, which `contracts.parse_plan` accepts.

The item schema stays open (`additionalProperties` not set): the prompt also asks for `id`, `depends_on`, `search_query`,
`domain`, `requires`, `privacy`, `no_search`, `metadata_filters` and `output_skill`.

Switch: PLANNER_SCHEMA_ENUMS=0 returns the old unconstrained schema.
"""
from __future__ import annotations

import os
from typing import Mapping, Optional, Sequence


def build_planner_schema(categories: Sequence[str], tool_names: Sequence[str], *, wrap_object: bool) -> dict:
    """Schema of a plan. Empty `categories` / `tool_names` leave the field a plain string (an empty enum is invalid)."""
    category: dict = {"type": "string"}
    if categories:
        category["enum"] = list(dict.fromkeys(categories))
    mcp_tool: dict = {"type": "string"}
    if tool_names:
        mcp_tool["enum"] = list(dict.fromkeys(tool_names))
    item = {
        "type": "object",
        "properties": {
            "task": {"type": "string"},
            "category": category,
            "mcp_tool": mcp_tool,
            "mcp_args": {"type": "object"},
            "search_query": {"type": "string"},
            "depends_on": {"anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
            "metadata_filters": {"type": "object"},
            "id": {"type": "string"},
        },
        "required": ["task", "category"],
    }
    array = {"type": "array", "items": item}
    if wrap_object:
        return {"type": "object", "properties": {"tasks": array}, "required": ["tasks"]}
    return array


def planner_schema_for_state(state: Mapping, global_experts: Sequence[str], *, wrap_object: bool) -> dict:
    """The schema for one planner call: categories from the template/global experts, tools from the request's catalog
    (`planner_tool_names`, set by planner_node)."""
    from services.pipeline.plan_categories import allowed_plan_categories

    if os.getenv("PLANNER_SCHEMA_ENUMS", "1") == "0":
        return build_planner_schema([], [], wrap_object=wrap_object)
    categories = allowed_plan_categories(state.get("user_experts"), global_experts)
    tools: Optional[Sequence[str]] = state.get("planner_tool_names")
    return build_planner_schema(categories, list(tools or []), wrap_object=wrap_object)
