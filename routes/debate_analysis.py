"""routes/debate_analysis.py — rubric-based analysis of a debate transcript.

Status: in development. Endpoint:

  POST /v1/debate-analysis

The template named in the request must carry an enabled ``debate_analysis``
policy and be authorised for the caller; otherwise the request is rejected.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from services.auth import _extract_api_key, _validate_api_key
from services.debate_analysis.adapters import make_judge_llm_call, make_research_call
from services.debate_analysis.contracts import (
    DebateAnalysisError,
    DebateProfile,
    parse_debate_analysis_policy,
    parse_debate_profile,
)
from services.debate_analysis.rubric import load_fallacy_catalog, load_rubric
from services.debate_analysis.runtime import (
    DebateAnalysisExecutionError,
    JudgeAbort,
    analyze_debate_frames,
)
from services.debate_analysis.store import record_analysis
from services.debate_analysis.transcript import normalize_transcript
from services.tracking import _check_ip_rate_limit

logger = logging.getLogger("MOE-SOVEREIGN")

router = APIRouter(prefix="/v1/debate-analysis", tags=["debate-analysis"])

# Hard ceiling for one request, independent of the policy call budget.
_REQUEST_TIMEOUT_S = 900.0
_MAX_FRAMES = 4


class DebateAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template: str = Field(min_length=1, max_length=200)
    transcript: str | dict | list
    profile: dict | None = None
    profiles: list[dict] | None = Field(default=None, max_length=_MAX_FRAMES)


# (status code, JSON body, extra headers). Plain data keeps the handler testable
# without the web framework.
Outcome = tuple[int, dict, dict | None]


def _error(status: int, code: str, message: str, headers: dict | None = None) -> Outcome:
    return (
        status,
        {"error": {"message": message, "type": "debate_analysis_error", "code": code}},
        headers,
    )


def _configured_judges() -> list[str]:
    """Judge models from validated configuration; empty entries are ignored."""

    raw = os.getenv("DEBATE_ANALYSIS_JUDGE_MODELS", "")
    return [m.strip() for m in raw.split(",") if m.strip()]


@router.post("")
async def analyze(body: DebateAnalysisRequest, request: Request) -> JSONResponse:
    status, content, headers = await handle_analysis(body, request)
    return JSONResponse(status_code=status, content=content, headers=headers)


async def handle_analysis(body: DebateAnalysisRequest, request: Any) -> Outcome:
    if not await _check_ip_rate_limit(request):
        return _error(429, "rate_limit_exceeded", "Rate limit exceeded", {"Retry-After": "60"})

    raw_key = _extract_api_key(request)
    user_ctx = await _validate_api_key(raw_key) if raw_key else {"error": "invalid_key"}
    if not user_ctx or "error" in user_ctx:
        if user_ctx and user_ctx.get("error") == "budget_exceeded":
            return _error(429, "budget_exceeded", "Budget exceeded")
        return _error(401, "invalid_api_key", "Invalid or missing API key")
    user_id = user_ctx.get("user_id", "anon")

    # Deferred: these modules import the full platform stack.
    from services.routing import _resolve_template_prompts, _resolve_template_selection
    from services.sovereignty import resolve_local_only

    permissions_json = user_ctx.get("permissions_json", "{}")
    try:
        user_perms = json.loads(permissions_json or "{}")
    except json.JSONDecodeError:
        user_perms = {}
    local_only = resolve_local_only(user_perms, user_ctx)

    user_templates_json = user_ctx.get("user_templates_json", "{}")
    selection = _resolve_template_selection(
        permissions_json,
        override_tmpl_id=body.template,
        user_templates_json=user_templates_json,
        admin_override=False,
    )
    if not selection["authorized"] or selection.get("template") is None:
        return _error(403, "template_not_authorized", "Template not available")
    raw_policy = selection["template"].get("debate_analysis")

    try:
        policy = parse_debate_analysis_policy(raw_policy)
        if policy.activation != "enabled":
            return _error(409, "not_enabled", "Debate analysis is not enabled for this template")
        if body.profile is not None and body.profiles is not None:
            raise DebateAnalysisError("send either profile or profiles, not both")
        raw_profiles = body.profiles if body.profiles is not None else [body.profile]
        profiles: list[DebateProfile] = [parse_debate_profile(p) for p in raw_profiles]
        transcript = normalize_transcript(body.transcript)
        rubric = load_rubric(policy.rubric_version)
        catalog = load_fallacy_catalog("v1")
    except DebateAnalysisError as exc:
        return _error(422, "invalid_request", str(exc))

    tmpl_prompts = _resolve_template_prompts(
        permissions_json,
        override_tmpl_id=body.template,
        user_templates_json=user_templates_json,
        admin_override=False,
        user_connections_json=user_ctx.get("user_connections_json", "{}"),
    )
    judges = _configured_judges()
    template_judge = (tmpl_prompts.get("judge_model_override") or "").strip()
    if not judges and template_judge:
        judges = [template_judge]
    if not judges:
        return _error(409, "no_judge_configured", "No judge model configured for debate analysis")
    overrides = {}
    if template_judge and tmpl_prompts.get("judge_url_override"):
        overrides[template_judge] = {
            "url": tmpl_prompts["judge_url_override"],
            "token": tmpl_prompts.get("judge_token_override", ""),
        }

    llm = make_judge_llm_call(
        {"local_only_routing": local_only, "user_id": user_id},
        endpoint_overrides=overrides,
    )
    research = None
    if policy.research_enabled and tmpl_prompts.get("enable_web_research", True):
        from services.llm_instances import search
        from web_search import _web_search_with_citations

        # The public-DDG fallback leaves the platform, so local_only disables it.
        ddg = bool(tmpl_prompts.get("search_fallback_ddg")) and not local_only

        async def _search(query: str) -> str:
            return await _web_search_with_citations(query, search, ddg)

        research = make_research_call(_search)

    request_id = uuid.uuid4().hex
    try:
        analyses = await analyze_debate_frames(
            transcript, profiles, policy, rubric, catalog, llm, judges,
            research=research, deadline=time.monotonic() + _REQUEST_TIMEOUT_S,
        )
    except JudgeAbort as exc:
        logger.warning("debate_analysis aborted: %s", exc)
        return _error(403 if "Egress" in str(exc) else 504, "aborted",
                      "Analysis aborted (egress policy or deadline)")
    except DebateAnalysisExecutionError:
        return _error(503, "no_valid_judge_output", "No valid judge output was obtained")
    except DebateAnalysisError as exc:
        return _error(422, "invalid_request", str(exc))

    for analysis in analyses:
        await record_analysis(
            analysis, request_id=request_id, user_id=user_id, template_id=body.template
        )
    return (
        200,
        {
            "status": "in_development",
            "request_id": request_id,
            "analyses": [a.model_dump(mode="json") for a in analyses],
        },
        None,
    )
