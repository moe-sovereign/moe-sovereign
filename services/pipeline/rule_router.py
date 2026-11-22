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

import re
from typing import Sequence

RULES = (
    ("data_analyst", re.compile(r"\b(sql|database|datenbank|table|tabelle|csv|insert into|select\b|rows?\b|zeilen)\b", re.IGNORECASE)),
    ("compounding_knowledge", re.compile(r"\b(graph|graphen|nodes?|knoten|relationships?|beziehungen|cypher|edges?|kanten)\b", re.IGNORECASE)),
    ("precision_tools", re.compile(r"\b(calculate|compute|berechne|ermittle|sum|summe|average|durchschnitt|percent|prozent|%|round|runde)\b", re.IGNORECASE)),
)


def route_category(request: str, allowed: Sequence[str] = ()) -> str:
    """The category for a request; a category the template lacks becomes `general` (or the template's first one)."""
    chosen = next((cat for cat, rx in RULES if rx.search(request or "")), "general")
    if allowed and chosen not in allowed:
        chosen = "general" if "general" in allowed else list(allowed)[0]
    return chosen


def rule_plan(request: str, allowed: Sequence[str] = ()) -> list:
    """A one-task plan that hands the whole request to the routed expert."""
    return [{"id": "task-1", "task": request, "category": route_category(request, allowed)}]
