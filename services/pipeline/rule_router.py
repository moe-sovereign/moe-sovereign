"""Rule-based routing instead of the LLM planner (PLANNER_MODE=rule, research switch; pre-registration O2).

F30: the planner sends 74 % of the subtasks to `precision_tools` and every pipeline failure is a planner contract error. This module replaces the planner by rules, so that the experts
can be tested with a correct route and without contract errors: one task per request (the whole request), the category by keywords, the first matching rule wins:
  1. data tables, SQL, database           -> data_analyst
  2. graph, nodes, relationships, Cypher  -> compounding_knowledge
  3. calculation of quantities            -> precision_tools
  4. otherwise                            -> general
A category that the active template does not define falls back to `general` (or the template's first category). No LLM call, no tool plan, so no contract error.
"""
from __future__ import annotations

import json
import os
import re
from typing import Sequence

RULES = (
    ("data_analyst", re.compile(r"\b(sql|database|datenbank|table|tabelle|csv|insert into|select\b|rows?\b|zeilen)\b", re.IGNORECASE)),
    ("compounding_knowledge", re.compile(r"\b(graph|graphen|nodes?|knoten|relationships?|beziehungen|cypher|edges?|kanten)\b", re.IGNORECASE)),
    ("precision_tools", re.compile(r"\b(calculate|compute|berechne|ermittle|sum|summe|average|durchschnitt|percent|prozent|%|round|runde)\b", re.IGNORECASE)),
)


def task_family(request: str) -> str:
    """Coarse task family by keywords: `sql_calc` (tables only), `cypher_calc` (graph only), `cypher_sql_calc` (both) or `other`."""
    text = request or ""
    has_table = bool(RULES[0][1].search(text))
    has_graph = bool(RULES[1][1].search(text))
    return "cypher_sql_calc" if has_table and has_graph else "sql_calc" if has_table else "cypher_calc" if has_graph else "other"


def _competence_matrix() -> dict:
    """RULE_ROUTER_MATRIX_FILE (pre-registration O6): JSON {family: category}, chosen on the dev half of the tasks by experts/build_competence_matrix.py."""
    path = os.getenv("RULE_ROUTER_MATRIX_FILE", "")
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def route_category(request: str, allowed: Sequence[str] = ()) -> str:
    """The category for a request; a category the template lacks becomes `general` (or the template's first one)."""
    chosen = _competence_matrix().get(task_family(request)) or next((cat for cat, rx in RULES if rx.search(request or "")), "general")
    if allowed and chosen not in allowed:
        chosen = "general" if "general" in allowed else list(allowed)[0]
    return chosen


def rule_plan(request: str, allowed: Sequence[str] = ()) -> list:
    """A one-task plan that hands the whole request to the routed expert."""
    return [{"id": "task-1", "task": request, "category": route_category(request, allowed)}]
