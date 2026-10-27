"""
services/routing.py — Expert template and prompt resolution.

Pure sync functions — no async, no state deps.
Called from main.py (chat completions) and routes/anthropic_compat.py.
"""

import json
import logging
from typing import Optional

from config import (
    URL_MAP, TOKEN_MAP, _WEB_SEARCH_FALLBACK_DDG, INFERENCE_SERVERS_LIST,
    AGENT_CACHE_ENABLED, AGENT_GRAPHRAG_ENABLED, AGENT_INGEST_ENABLED,
    EXPERT_THINKING_ENABLED, JMOE_DEBATE_ENABLED,
)
from services.deliberation.contracts import (
    DeliberationPolicyError,
    legacy_deliberation_policy,
    parse_deliberation_policy,
)
from services.templates import _read_expert_templates

logger = logging.getLogger("MOE-SOVEREIGN")


def _resolve_template_selection(
    permissions_json: str,
    override_tmpl_id: Optional[str] = None,
    user_templates_json: str = "{}",
    admin_override: bool = False,
) -> dict:
    """Resolve an explicit/default template without confusing names with grants.

    User templates are stored under an opaque ID but exposed to API clients by
    their display name.  An admin template may legally have the same name.  An
    authorized admin match wins; otherwise an owned match wins.  Merely knowing
    an admin template's name never grants access to it.

    The return value contains ``id``, ``template``, ``source`` and
    ``authorized``.  ``template`` is ``None`` when nothing can be resolved.
    """
    try:
        perms = json.loads(permissions_json or "{}")
        allowed_ids = perms.get("expert_template", []) or []
        templates = _read_expert_templates()
        owned: dict = json.loads(user_templates_json or "{}")
        requested = override_tmpl_id or ""

        def _owned_match(value: str):
            if value in owned:
                return value, owned[value]
            return next(
                (
                    (template_id, template)
                    for template_id, template in owned.items()
                    if template.get("name") == value
                ),
                (None, None),
            )

        def _admin_match(value: str):
            template = next(
                (
                    t for t in templates
                    if t.get("id") == value or t.get("name") == value
                ),
                None,
            )
            return (template.get("id"), template) if template else (None, None)

        if requested:
            admin_id, admin_template = _admin_match(requested)
            owned_id, owned_template = _owned_match(requested)
            if admin_template and (admin_override or admin_id in allowed_ids):
                return {
                    "id": admin_id, "template": admin_template,
                    "source": "admin", "authorized": True,
                }
            if owned_template:
                return {
                    "id": owned_id, "template": owned_template,
                    "source": "owned", "authorized": True,
                }
            if admin_template:
                return {
                    "id": admin_id, "template": admin_template,
                    "source": "admin", "authorized": False,
                }
            return {
                "id": None, "template": None,
                "source": None, "authorized": False,
            }

        for template_id in allowed_ids:
            admin_id, admin_template = _admin_match(template_id)
            if admin_template:
                return {
                    "id": admin_id, "template": admin_template,
                    "source": "admin", "authorized": True,
                }
            owned_id, owned_template = _owned_match(template_id)
            if owned_template:
                return {
                    "id": owned_id, "template": owned_template,
                    "source": "owned", "authorized": True,
                }
        return {
            "id": None, "template": None,
            "source": None, "authorized": False,
        }
    except Exception:
        logger.exception("_resolve_template_selection failed")
        return {
            "id": None, "template": None,
            "source": None, "authorized": False,
        }


def _resolve_user_experts(
    permissions_json: str,
    override_tmpl_id: Optional[str] = None,
    user_templates_json: str = "{}",
    admin_override: bool = False,
    user_connections_json: str = "{}",
) -> Optional[dict]:
    """Return an EXPERTS-compatible dict from the assigned expert template.

    Returns None when no template is assigned → global EXPERTS are used.
    override_tmpl_id: loaded directly (model-ID routing).
    admin_override: if True, override_tmpl_id is loaded without permission check.
    """
    try:
        user_templates: dict = json.loads(user_templates_json or "{}")
        user_conns: dict = json.loads(user_connections_json or "{}")
        selection = _resolve_template_selection(
            permissions_json,
            override_tmpl_id=override_tmpl_id,
            user_templates_json=user_templates_json,
            admin_override=admin_override,
        )
        tmpl = selection["template"] if selection["authorized"] else None
        if tmpl is None:
            return None

        result: dict = {}
        for cat, cat_cfg in tmpl.get("experts", {}).items():
            _sys_prompt = (cat_cfg.get("system_prompt") or "").strip() if isinstance(cat_cfg, dict) else ""
            _cat_ctx = int(cat_cfg.get("context_window") or 0) if isinstance(cat_cfg, dict) else 0
            # Per-expert MCP tools and Skills (set by dynamic_router or admin template config)
            _mcp_tools = list(cat_cfg.get("mcp_tools") or []) if isinstance(cat_cfg, dict) else []
            _skills    = list(cat_cfg.get("skills") or []) if isinstance(cat_cfg, dict) else []
            # Complementary review wave (graph/expert.py): categories whose
            # models review this category's output, opt-in per template.
            _review_lenses = [
                str(x) for x in (cat_cfg.get("review_lenses") or [])
                if isinstance(x, str) and x.strip()
            ] if isinstance(cat_cfg, dict) else []
            _review_replaces_sc = bool(cat_cfg.get("review_replaces_self_critique", False)) if isinstance(cat_cfg, dict) else False
            if isinstance(cat_cfg, dict) and "models" in cat_cfg:
                models_list = []
                for m in cat_cfg.get("models", []):
                    role = m.get("role")
                    if role is None:
                        role = "always" if m.get("required", True) else "primary"
                    if role == "always":
                        forced, model_tier = True, None
                    elif role == "fallback":
                        forced, model_tier = False, 2
                    else:
                        forced, model_tier = False, 1
                    ep    = (m.get("endpoint") or "").strip()
                    url   = URL_MAP.get(ep) if ep else None
                    token = TOKEN_MAP.get(ep, "ollama") if ep else "ollama"
                    if not url and ep in user_conns:
                        # Private user connection: URL AND api_key come from the
                        # connection — TOKEN_MAP does not know private endpoints.
                        url   = user_conns[ep]["url"]
                        token = user_conns[ep].get("api_key") or "ollama"
                    models_list.append({
                        "model":          m.get("model", ""),
                        "endpoint":       ep,
                        "url":            url,
                        "token":          token,
                        "forced":         forced,
                        "_tier":          model_tier,
                        "_system_prompt": _sys_prompt,
                        "context_window": _cat_ctx,
                        "thinking_mode":  bool(
                            m.get(
                                "thinking_mode",
                                cat_cfg.get(
                                    "thinking_mode",
                                    EXPERT_THINKING_ENABLED,
                                ),
                            )
                        ),
                        "_mcp_tools":     _mcp_tools,
                        "_skills":        _skills,
                        "_review_lenses": _review_lenses,
                        "_review_replaces_self_critique": _review_replaces_sc,
                    })
                result[cat] = models_list
            elif isinstance(cat_cfg, dict):
                # Legacy format: {model, endpoint}
                ep    = (cat_cfg.get("endpoint") or "").strip()
                url   = URL_MAP.get(ep) if ep else None
                token = TOKEN_MAP.get(ep, "ollama") if ep else "ollama"
                if not url and ep in user_conns:
                    url   = user_conns[ep]["url"]
                    token = user_conns[ep].get("api_key") or "ollama"
                result[cat] = [{
                    "model":          cat_cfg.get("model", ""),
                    "endpoint":       ep,
                    "url":            url,
                    "token":          token,
                    "forced":         True,
                    "_tier":          None,
                    "_system_prompt": _sys_prompt,
                    "context_window": _cat_ctx,
                    "thinking_mode":  bool(
                        cat_cfg.get(
                            "thinking_mode",
                            EXPERT_THINKING_ENABLED,
                        )
                    ),
                    "_mcp_tools":     _mcp_tools,
                    "_skills":        _skills,
                    "_review_lenses": _review_lenses,
                    "_review_replaces_self_critique": _review_replaces_sc,
                }]
        return result or None
    except Exception:
        logger.exception("_resolve_user_experts failed — falling back to global experts")
        return None


def _get_template_expert_catalog(template_id: str) -> dict:
    """Return {category: [model_name, ...]} for every model configured in a
    template — the "menu" of experts available to a request, independent of
    any user/API-key permission context (unlike _resolve_user_experts, which
    always needs a permissions_json to resolve against). Used by the live
    pipeline diagram (admin_ui) to show which experts a request COULD have
    used, alongside which ones the stage trace says it actually used.

    Falls back to the global EXPERT_MODELS config when template_id is empty
    or not found — matching the same fallback graph/expert.py itself uses
    at runtime (state_.get("user_experts") or EXPERTS).
    """
    from config import EXPERTS as _GLOBAL_EXPERTS

    def _flatten(experts_cfg: dict) -> dict:
        catalog: dict = {}
        for cat, cat_cfg in (experts_cfg or {}).items():
            models: list = []
            if isinstance(cat_cfg, dict) and "models" in cat_cfg:
                models = [m.get("model", "") for m in cat_cfg.get("models", []) if m.get("model")]
            elif isinstance(cat_cfg, dict):
                # Legacy format: {model, endpoint}
                if cat_cfg.get("model"):
                    models = [cat_cfg["model"]]
            elif isinstance(cat_cfg, list):
                # Already-resolved EXPERTS-style list (global config / _resolve_user_experts output)
                models = [m.get("model", "") for m in cat_cfg if isinstance(m, dict) and m.get("model")]
            if models:
                catalog[cat] = models
        return catalog

    if not template_id:
        return _flatten(_GLOBAL_EXPERTS)
    try:
        templates = _read_expert_templates()
        tmpl = next((t for t in templates if t.get("id") == template_id), None)
        if not tmpl:
            return _flatten(_GLOBAL_EXPERTS)
        return _flatten(tmpl.get("experts", {}))
    except Exception:
        logger.exception("_get_template_expert_catalog failed for template_id=%s", template_id)
        return _flatten(_GLOBAL_EXPERTS)


def _resolve_template_prompts(
    permissions_json: str,
    override_tmpl_id: Optional[str] = None,
    user_templates_json: str = "{}",
    admin_override: bool = False,
    user_connections_json: str = "{}",
) -> dict:
    """Return planner_prompt, judge_prompt and optional model overrides from the template."""
    legacy_policy = legacy_deliberation_policy(JMOE_DEBATE_ENABLED)
    empty = {
        "planner_prompt": "", "judge_prompt": "",
        "judge_model_override": "", "judge_url_override": "", "judge_token_override": "",
        "planner_model_override": "", "planner_url_override": "", "planner_token_override": "",
        "tool_expert_model_override": "", "tool_expert_url_override": "", "tool_expert_token_override": "",
        "guardrail_prompt": "",
        "guardrail_model_override": "", "guardrail_url_override": "", "guardrail_token_override": "",
        "planner_num_ctx": 0, "judge_num_ctx": 0, "tool_expert_num_ctx": 0, "guardrail_num_ctx": 0,
        "enable_cache": True, "enable_graphrag": True, "enable_web_research": True,
        "enable_habe": False,
        "search_fallback_ddg": _WEB_SEARCH_FALLBACK_DDG,
        "graphrag_max_chars": 0,
        "history_max_turns": 0, "history_max_chars": 0,
        "force_think": False, "max_agentic_rounds": 0,
        "enable_mission_context": False,
        "enable_semantic_memory": False,
        "semantic_memory_n_results": 0, "semantic_memory_ttl_hours": 0,
        "enable_cross_session_memory": False,
        "cross_session_scopes": ["private"], "cross_session_ttl_days": 0,
        "complexity_level": "",
        "causal_intervention": None,
        "deliberation_policy": legacy_policy.model_dump(mode="json"),
        # Augmented Tool Path (agentic clients) — mirrors the global AGENT_*_ENABLED
        # defaults (off), overridable per expert template. See config.py.
        "agent_cache": AGENT_CACHE_ENABLED,
        "agent_graphrag": AGENT_GRAPHRAG_ENABLED,
        "agent_ingest": AGENT_INGEST_ENABLED,
    }
    try:
        selection = _resolve_template_selection(
            permissions_json,
            override_tmpl_id=override_tmpl_id,
            user_templates_json=user_templates_json,
            admin_override=admin_override,
        )
        tmpl = selection["template"] if selection["authorized"] else None
        if tmpl is None:
            return empty

        # An explicit policy is a security/resource contract and must validate
        # fail closed. Templates predating schema 1.0 retain the established
        # global three-call J-MoE behavior until they are edited explicitly.
        if "deliberation_policy" in tmpl:
            deliberation_policy = parse_deliberation_policy(
                tmpl.get("deliberation_policy")
            )
        else:
            deliberation_policy = legacy_policy

        def _split_model_ep(val: str) -> tuple:
            if val and "@" in val:
                at = val.rindex("@")
                return val[:at], val[at + 1:]
            return val or "", ""

        judge_m,       judge_ep       = _split_model_ep(tmpl.get("judge_model", ""))
        planner_m,     planner_ep     = _split_model_ep(tmpl.get("planner_model", ""))
        tool_expert_m, tool_expert_ep = _split_model_ep(tmpl.get("tool_expert_model", ""))
        guardrail_m,   guardrail_ep   = _split_model_ep(tmpl.get("guardrail_model", ""))

        def _resolve_ep_url(ep: str) -> tuple:
            if ep in URL_MAP:
                return URL_MAP[ep], TOKEN_MAP.get(ep, "ollama")
            _uc = json.loads(user_connections_json or "{}")
            if ep in _uc:
                return _uc[ep]["url"], _uc[ep].get("api_key") or "ollama"
            return "", "ollama"

        judge_url,       judge_tok       = _resolve_ep_url(judge_ep)       if judge_ep       else ("", "ollama")
        planner_url,     planner_tok     = _resolve_ep_url(planner_ep)     if planner_ep     else ("", "ollama")
        tool_expert_url, tool_expert_tok = _resolve_ep_url(tool_expert_ep) if tool_expert_ep else ("", "ollama")
        guardrail_url,   guardrail_tok   = _resolve_ep_url(guardrail_ep)   if guardrail_ep   else ("", "ollama")
        return {
            "planner_prompt":              tmpl.get("planner_prompt", ""),
            "judge_prompt":                tmpl.get("judge_prompt", ""),
            "judge_model_override":        judge_m,
            "judge_url_override":          judge_url,
            "judge_token_override":        judge_tok,
            "planner_model_override":      planner_m,
            "planner_url_override":        planner_url,
            "planner_token_override":      planner_tok,
            "tool_expert_model_override":  tool_expert_m,
            "tool_expert_url_override":    tool_expert_url,
            "tool_expert_token_override":  tool_expert_tok,
            "guardrail_prompt":            tmpl.get("guardrail_prompt", ""),
            "guardrail_model_override":    guardrail_m,
            "guardrail_url_override":      guardrail_url,
            "guardrail_token_override":    guardrail_tok,
            "planner_num_ctx":             int(tmpl.get("planner_num_ctx") or 0),
            "judge_num_ctx":               int(tmpl.get("judge_num_ctx") or 0),
            "tool_expert_num_ctx":         int(tmpl.get("tool_expert_num_ctx") or 0),
            "guardrail_num_ctx":           int(tmpl.get("guardrail_num_ctx") or 0),
            "enable_cache":            tmpl.get("enable_cache", True),
            "enable_graphrag":         tmpl.get("enable_graphrag", True),
            "enable_web_research":     tmpl.get("enable_web_research", True),
            "enable_habe":             tmpl.get("enable_habe", False),
            "search_fallback_ddg":     tmpl.get("search_fallback_ddg", _WEB_SEARCH_FALLBACK_DDG),
            "graphrag_max_chars":      int(tmpl.get("graphrag_max_chars") or 0),
            "history_max_turns":       int(tmpl.get("history_max_turns") or 0),
            "history_max_chars":       int(tmpl.get("history_max_chars") or 0),
            "force_think":             bool(tmpl.get("force_think", False)),
            "max_agentic_rounds":      int(tmpl.get("max_agentic_rounds") or 0),
            "enable_mission_context":  bool(tmpl.get("enable_mission_context", False)),
            "enable_semantic_memory":  bool(tmpl.get("enable_semantic_memory", False)),
            "semantic_memory_n_results": int(tmpl.get("semantic_memory_n_results") or 0),
            "semantic_memory_ttl_hours": int(tmpl.get("semantic_memory_ttl_hours") or 0),
            "enable_cross_session_memory": bool(tmpl.get("enable_cross_session_memory", False)),
            "cross_session_scopes":    list(tmpl.get("cross_session_scopes", ["private"])),
            "cross_session_ttl_days":  int(tmpl.get("cross_session_ttl_days") or 0),
            "complexity_level":        tmpl.get("complexity_level", ""),
            "causal_intervention":     tmpl.get("causal_intervention"),
            "deliberation_policy":     deliberation_policy.model_dump(mode="json"),
            "agent_cache":             bool(tmpl.get("agent_cache", AGENT_CACHE_ENABLED)),
            "agent_graphrag":          bool(tmpl.get("agent_graphrag", AGENT_GRAPHRAG_ENABLED)),
            "agent_ingest":            bool(tmpl.get("agent_ingest", AGENT_INGEST_ENABLED)),
        }
    except DeliberationPolicyError:
        raise
    except Exception:
        logger.exception("_resolve_template_prompts failed — returning empty prompt config")
        return empty


def _server_info(endpoint_name: str) -> dict:
    """Return the full server configuration for a given endpoint name."""
    return next((s for s in INFERENCE_SERVERS_LIST if s["name"] == endpoint_name), {})


def _is_endpoint_error(exc: Exception) -> bool:
    """Return True when the exception signals an endpoint is unavailable or the model can't be served.

    Covers auth/quota failures (4xx) and model-loading failures (5xx).
    Model-loading errors ("unable to load model", missing blobs) are treated as
    transient availability errors so the fallback chain can activate rather than
    returning a raw 500 to the client.
    """
    s = str(exc).lower()
    # Model-loading failures from Ollama (corrupt/missing blob, GGUF read error).
    # Matched narrowly: bare "blob"/"no such file or directory" also occur in
    # unrelated error texts (e.g. tool output echoed into an exception message).
    if ("blob" in s or "no such file or directory" in s) and (
        "model" in s or "blobs/sha256" in s or "gguf" in s or "ollama" in s
    ):
        return True
    return any(k in s for k in (
        "401", "unauthorized", "403", "forbidden",
        "429", "rate limit", "quota exceeded",
        "authentication", "x-api-key",
        "402", "insufficient", "payment required",
        "unable to load model",
        "error loading model",
        "failed to load model",
    ))
