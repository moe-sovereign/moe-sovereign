"""Adapters between the debate-analysis runtime and the platform services.

The runtime only knows two injected callables. This module builds them from the
existing judge invocation (which enforces ``local_only`` egress rules and the
request deadline) and from the existing web search. Heavy platform modules are
imported lazily so the package stays importable without the full stack.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Awaitable, Callable, Mapping

from .contracts import DebateProfile, Transcript
from .runtime import JudgeAbort, LLMCall, ResearchCall

SearchFn = Callable[[str], Awaitable[str]]

_QUOTE_RE = re.compile(r"[„\"“]([^„\"“”]{12,200})[“\"”]")
_NUMBER_SENTENCE_RE = re.compile(r"[^.!?\n]*\d[^.!?\n]*[.!?]?")
_MAX_NOTE = 600


def _default_invoke() -> Callable[..., Awaitable[Any]]:
    from services.inference import _invoke_judge_with_retry

    return _invoke_judge_with_retry


def make_judge_llm_call(
    state_base: Mapping[str, Any],
    *,
    invoke: Callable[..., Awaitable[Any]] | None = None,
    endpoint_overrides: Mapping[str, Mapping[str, str]] | None = None,
) -> LLMCall:
    """Build the judge callable.

    ``state_base`` carries the request-scoped values the judge invocation
    honours (``local_only_routing``, ``user_id``, deadline fields). Each call
    sets the judge model; a URL/token is used only when ``endpoint_overrides``
    names one for that judge, otherwise the node is discovered as usual.
    """

    async def call(judge_id: str, prompt: str) -> str:
        fn = invoke or _default_invoke()
        state = dict(state_base)
        override = (endpoint_overrides or {}).get(judge_id) or {}
        state["judge_model_override"] = judge_id
        state["judge_url_override"] = override.get("url", "")
        state["judge_token_override"] = override.get("token", "")
        try:
            # One attempt: the analysis budget, not the judge helper, owns retries.
            res = await fn(state, prompt, max_retries=1, temperature=0.0)
        except Exception as exc:  # noqa: BLE001 - classified below
            if _is_fatal(exc):
                raise JudgeAbort(type(exc).__name__) from exc
            raise
        content = getattr(res, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("judge returned no content")
        if content.startswith("[Judge unavailable"):
            raise RuntimeError("judge unavailable")
        return content

    return call


def _is_fatal(exc: Exception) -> bool:
    # Matched by name so this module does not import the platform stack.
    return type(exc).__name__ in {"EgressDenied", "RequestDeadlineExceeded"}


def _queries_for(
    transcript: Transcript, profile: DebateProfile | None, max_queries: int
) -> list[str]:
    if profile is None:
        queries: list[str] = []
        for turn in transcript.turns:
            for quote in _QUOTE_RE.findall(turn.text):
                queries.append(f'"{quote.strip()}"')
        for turn in transcript.turns:
            for sentence in _NUMBER_SENTENCE_RE.findall(turn.text):
                if len(sentence.strip()) > 20:
                    queries.append(sentence.strip()[:160])
        return list(dict.fromkeys(queries))[:max_queries]
    topic = transcript.turns[0].text[:100].strip()
    year = (profile.frame_date or "")[:4]
    return [f"{topic} reaction criticism {year}".strip()][:max_queries]


def make_research_call(
    search_fn: SearchFn,
    *,
    max_queries: int = 4,
    today: date | None = None,
) -> ResearchCall:
    """Build the research callable on top of a plain ``query -> text`` search.

    Frame-free calls check quotations and numeric claims. Framed calls fetch
    current reception context; for a historical frame no contemporaneous source
    is retrieved and that limitation is stated instead of guessed around.
    """

    async def research(transcript: Transcript, profile: DebateProfile | None) -> list[str]:
        current_year = (today or date.today()).year
        if profile is not None and profile.frame_date:
            if int(profile.frame_date[:4]) < current_year - 1:
                return [
                    f"No contemporaneous sources retrieved for frame date {profile.frame_date}; "
                    "reception cannot be verified from retrieval."
                ]
        notes: list[str] = []
        errors = 0
        queries = _queries_for(transcript, profile, max_queries)
        for query in queries:
            try:
                text = (await search_fn(query)).strip()
            except Exception:  # noqa: BLE001 - one failed query must not abort research
                errors += 1
                continue
            if text:
                notes.append(f"[query: {query[:80]}] {text[:_MAX_NOTE]}")
        if queries and errors == len(queries):
            raise RuntimeError("all research queries failed")
        return notes

    return research
