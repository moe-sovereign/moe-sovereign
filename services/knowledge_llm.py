"""Client for the external knowledge model: the swappable holder of what the infrastructure does not know.

The orchestrator keeps its own knowledge (graph, corrections, episodes) and asks this model only for what is missing, or to check
an attribute the graph holds. The model is configured per template (`knowledge_model_override` / `knowledge_url_override` /
`knowledge_token_override`, parsed in services/routing.py) or by environment (KNOWLEDGE_LLM_MODEL, KNOWLEDGE_LLM_URL,
KNOWLEDGE_LLM_TOKEN); without either the feature is off. Swapping the model is changing those three values: the contract below is
all the rest of the system depends on.

Contract (OpenAI-compatible `/chat/completions`, JSON answer):
  {"answers": [{"question", "answer", "confidence" 0..1, "unknown": bool, "as_of": "YYYY-MM" or ""}]}

Sovereignty: `assert_egress_allowed` applies (a `local_only` request never reaches a non-local host). For a non-local host the
request carries only the questions, never the infrastructure context, whatever the template says: facts of the system and of
its tenants are not sent to an external service because a switch allows the call.

Never raises for a model or network failure (the broker degrades to "no model knowledge"); a spent request deadline does propagate.
"""
from __future__ import annotations

import logging
import os
from typing import Mapping, Optional, Sequence
from urllib.parse import urlparse

import httpx

from services.deadline import remaining_timeout
from services.model_capabilities import openai_response_format
from services.sovereignty import EgressDenied, _host_is_local, assert_egress_allowed

logger = logging.getLogger("MOE-SOVEREIGN")

KNOWLEDGE_SCHEMA = {
    "type": "object",
    "properties": {"answers": {"type": "array", "items": {
        "type": "object",
        "properties": {"question": {"type": "string"}, "answer": {"type": "string"}, "confidence": {"type": "number"},
                       "unknown": {"type": "boolean"}, "as_of": {"type": "string"}},
        "required": ["question", "answer", "confidence", "unknown"]}}},
    "required": ["answers"],
}

SYSTEM_PROMPT = (
    "You are the knowledge source of an orchestration system. Answer each question briefly and factually, in the order given. "
    "Set unknown=true and confidence below 0.3 when you do not know it, when it concerns private or internal data, or when it is "
    "likely to have changed since your training. as_of is the latest month your knowledge of the answer reaches (YYYY-MM), or empty. "
    "Reply with JSON only: {\"answers\": [{\"question\", \"answer\", \"confidence\", \"unknown\", \"as_of\"}]}."
)


def knowledge_config(state_: Mapping) -> Optional[dict]:
    """The knowledge model of this request, or None when none is configured (the feature is off)."""
    model = state_.get("knowledge_model_override") or os.getenv("KNOWLEDGE_LLM_MODEL", "")
    url = state_.get("knowledge_url_override") or os.getenv("KNOWLEDGE_LLM_URL", "")
    if not model or not url:
        return None
    def num(key, env, default, cast):
        raw = state_.get(key) or os.getenv(env) or default
        try:
            return cast(raw)
        except (TypeError, ValueError):
            return cast(default)
    return {"model": str(model), "url": str(url),
            "token": state_.get("knowledge_token_override") or os.getenv("KNOWLEDGE_LLM_TOKEN", "none"),
            "max_questions": max(1, num("knowledge_max_questions", "KNOWLEDGE_LLM_MAX_QUESTIONS", 3, int)),
            "timeout_s": max(0.5, num("knowledge_timeout_s", "KNOWLEDGE_LLM_TIMEOUT_S", 8.0, float)),
            "share_context": bool(state_.get("knowledge_share_context"))}


def gap_question(term: str) -> str:
    return f"What is {term}? Answer in one or two sentences."


def verification_question(subject: str, relation: str) -> str:
    label = relation.lower().replace("has_", "").replace("_", " ")
    return f"What is the {label} of {subject}? Answer with the value only."


def build_messages(questions: Sequence[str], context: str = "") -> list:
    user = "Questions:\n" + "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions))
    if context:
        user += f"\n\nContext known to the system (may help to disambiguate):\n{context}"
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def parse_answers(text: str, questions: Sequence[str]) -> list:
    """Align the model's JSON with the questions (by position, falling back to unknown); never raises."""
    from services.pipeline.contracts import _first_json
    try:
        data = _first_json(text or "")
    except Exception:  # noqa: BLE001 - a malformed answer is "the model did not answer"
        data = None
    items = data.get("answers") if isinstance(data, dict) else data if isinstance(data, list) else None
    items = items if isinstance(items, list) else []
    out = []
    for i, q in enumerate(questions):
        raw = items[i] if i < len(items) and isinstance(items[i], dict) else {}
        try:
            conf = min(1.0, max(0.0, float(raw.get("confidence", 0.0))))
        except (TypeError, ValueError):
            conf = 0.0
        answer = str(raw.get("answer") or "").strip()
        out.append({"question": q, "answer": answer, "confidence": conf,
                    "unknown": bool(raw.get("unknown", not answer)) or not answer, "as_of": str(raw.get("as_of") or "")})
    return out


async def ask_knowledge(state_: Mapping, questions: Sequence[str], cfg: dict, *, context: str = "",
                        client: Optional[httpx.AsyncClient] = None) -> list:
    """One call with all questions. Returns one answer dict per question, or [] when the call could not be made or failed."""
    if not questions:
        return []
    url = cfg["url"].rstrip("/")
    try:
        assert_egress_allowed(url, bool(state_.get("local_only_routing")))
    except EgressDenied:
        logger.warning("knowledge llm: egress to %s denied by local_only routing", urlparse(url).hostname)
        return []
    share = context if (cfg.get("share_context") and _host_is_local(urlparse(url).hostname or "")) else ""
    body = {"model": cfg["model"], "messages": build_messages(questions, share), "temperature": 0, "max_tokens": 700}
    fmt = openai_response_format(cfg["model"], KNOWLEDGE_SCHEMA, strict=False)
    if fmt:
        body["response_format"] = fmt
    timeout = remaining_timeout(state_, cfg["timeout_s"], stage="knowledge_llm")  # raises when the request budget is spent
    own = client is None
    http = client or httpx.AsyncClient(timeout=timeout)
    try:
        r = await http.post(f"{url}/chat/completions", json=body, headers={"Authorization": f"Bearer {cfg['token']}"}, timeout=timeout)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"].get("content") or ""
    except Exception as exc:  # noqa: BLE001 - the broker degrades to "no model knowledge"
        logger.warning("knowledge llm: call failed: %s: %s", type(exc).__name__, exc)
        return []
    finally:
        if own:
            await http.aclose()
    return parse_answers(content, questions)
