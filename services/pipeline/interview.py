"""Planner interview: the planner model decides what to ask the external knowledge model before it plans.

Without it the broker asks a fixed question per gap term ("What is X?"). With KNOWLEDGE_CONSULT_BY=planner and PLANNER_INTERVIEW=1 the
planner model sees the request, the facts the infrastructure already holds and the open gaps, and returns the questions itself (or none
when the facts suffice or the request concerns data only the system has). The same prompt and parser are used to build training
traces, to train the small planner and to evaluate it, so what is trained is what runs.
"""
from __future__ import annotations

import json
from typing import Optional, Sequence

INTERVIEW_SCHEMA = {
    "type": "object",
    "properties": {"questions": {"type": "array", "items": {"type": "string"}}},
    "required": ["questions"],
}


def build_interview_prompt(request: str, known_facts: str, gaps: Sequence[str], max_questions: int = 3) -> str:
    gap_line = ", ".join(gaps) if gaps else "(none)"
    return (
        "You prepare a request for planning. An external knowledge model can answer questions; the system's own knowledge is shown below.\n\n"
        f"Known to the system:\n{known_facts.strip() or '(nothing)'}\n\n"
        f"Request: {request.strip()}\n"
        f"Open gaps (terms the system knows nothing about): {gap_line}\n\n"
        f"Ask the knowledge model ONLY for what you need and cannot find in the known facts. At most {max_questions} short, "
        "self-contained questions, each answerable without the request. Ask nothing when the known facts or the data in the request "
        "are enough, and never ask about private or internal data of the system.\n"
        'Answer with JSON only: {"questions": ["...", "..."]} (an empty list if nothing is needed).'
    )


def parse_questions(text: str, max_questions: int = 3) -> Optional[list]:
    """The questions of an interview answer, or None when the answer is not parseable (the caller falls back to rule-based ones)."""
    from services.pipeline.contracts import _first_json
    try:
        data = _first_json(text or "")
    except Exception:  # noqa: BLE001
        return None
    items = data.get("questions") if isinstance(data, dict) else data if isinstance(data, list) else None
    if not isinstance(items, list):
        return None
    out = []
    for q in items:
        q = str(q).strip()
        if q and q not in out:
            out.append(q)
    return out[:max_questions]


def completion_for(questions: Sequence[str]) -> str:
    """The training target for a set of questions."""
    return json.dumps({"questions": list(questions)}, ensure_ascii=False)
