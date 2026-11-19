"""The categories a planner plan may use: one source for the prompt, the output schema and the sanitiser.

Before this module the planner prompt offered one set of categories (`expert_categories` in graph/planner.py) while
`_sanitize_plan` accepted another and silently rewrote everything else to "general" - which is not even a category of
most templates. Both now derive from the functions below, and the output schema (services/pipeline/planner_schema.py)
constrains the model to the accepted set.

No imports from `graph`: services/inference.py needs this module too.
"""
from __future__ import annotations

import os
from typing import Iterable, Optional

NON_EXPERT = ("precision_tools", "research")  # handled by tools / web search, not by an expert model
SPECIAL = ("agentic_coder", "memory_recall")  # always accepted, offered only where they make sense
_FALLBACK_PREFERENCE = ("general", "general_assistant", "technical_support")


def _expert_builder_enabled() -> bool:
    return os.getenv("EXPERT_BUILDER_ENABLED", "true").lower() in ("true", "1", "yes")


def prompt_categories(
    user_experts: Optional[dict],
    global_experts: Iterable[str],
    *,
    mode: str = "",
    expert_builder_enabled: Optional[bool] = None,
) -> list[str]:
    """Expert categories the planner prompt offers (LLM experts only, in template order).

    A template that pins its roster is offered exactly that roster. `dynamic` hands the task to the ad-hoc expert builder,
    which floats to any model on any endpoint, so it is offered only without a pinned roster.
    """
    user_experts = user_experts or {}
    if user_experts:
        cats = [c for c in user_experts if c not in NON_EXPERT]
    else:
        cats = list(global_experts)
    if "agentic_coder" not in cats and (mode in ("agent_orchestrated", "code") or "agentic_coder" in user_experts):
        cats.append("agentic_coder")
    builder = _expert_builder_enabled() if expert_builder_enabled is None else expert_builder_enabled
    if builder and not user_experts and "dynamic" not in cats:
        cats.append("dynamic")
    return cats


def allowed_plan_categories(user_experts: Optional[dict], global_experts: Iterable[str]) -> list[str]:
    """Every category a plan task may carry (what the sanitiser accepts and what the output schema allows).

    With a template: its categories, the non-expert types and the special ones (`dynamic` only if the template defines it).
    Without: all global experts, the non-expert types, the special ones and `dynamic`.
    """
    user_cats = [c for c in (user_experts or {})]
    base = user_cats if user_cats else list(global_experts) + ["dynamic"]
    out: list[str] = []
    for c in [*base, *NON_EXPERT, *SPECIAL]:
        if c not in out:
            out.append(c)
    return out


def fallback_category(allowed: Iterable[str]) -> str:
    """The category an unknown one is rewritten to: a general one if the template has it, else its first expert category."""
    allowed = list(allowed)
    for c in _FALLBACK_PREFERENCE:
        if c in allowed:
            return c
    for c in allowed:
        if c not in NON_EXPERT and c not in SPECIAL and c != "dynamic":
            return c
    return "general"
