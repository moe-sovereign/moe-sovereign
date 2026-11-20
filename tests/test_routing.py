"""
test_routing.py — Unit tests for the deterministic hardware-routing logic in main.py.

Covers:
  - assign_gpu(): round-robin GPU index assignment within an endpoint
  - _resolve_user_experts(): expert template → category→model-config mapping
  - _select_node(): warm/cold preference and load-score-based node selection

No live connections are required. HTTP calls inside _select_node are mocked via
unittest.mock.AsyncMock.
"""

import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# conftest.py has already stubbed all heavy deps and set env vars,
# so importing main is safe at collection time.
import main  # still needed for assign_gpu, _select_node
from services import routing as _routing  # _resolve_user_experts moved here


# ── Helpers ───────────────────────────────────────────────────────────────────


def _reset_gpu_counter(endpoint: str, value: int = 0) -> None:
    """Reset the round-robin counter for an endpoint to a known value."""
    main._endpoint_gpu_indices[endpoint] = value


def _clear_ps_cache() -> None:
    """Discard all cached Ollama /api/ps results so tests are independent."""
    main._ps_cache.clear()


def _make_ps_response(model_names: list) -> MagicMock:
    """Build a mock httpx response whose .json() returns an Ollama /api/ps payload."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"models": [{"name": n} for n in model_names]}
    return resp


def _make_tools_response(tools: list[dict]) -> MagicMock:
    response = MagicMock()
    response.json.return_value = {"tools": tools}
    return response


@pytest.mark.asyncio
async def test_mcp_catalog_reload_removes_disabled_and_stale_tools():
    original_schemas = dict(main.state.MCP_TOOL_SCHEMAS)
    original_descriptions = dict(main.state._MCP_TOOLS_DICT)
    original_text = main.state.MCP_TOOLS_DESCRIPTION
    original_agentic_text = main.state.AGENTIC_CODE_TOOLS_DESCRIPTION
    main.state.MCP_TOOL_SCHEMAS.clear()
    main.state.MCP_TOOL_SCHEMAS["stale_tool"] = {"required": []}
    main.state._MCP_TOOLS_DICT.clear()
    main.state._MCP_TOOLS_DICT["stale_tool"] = "stale"
    response = _make_tools_response(
        [
            {
                "name": "calendar_facts",
                "description": "Calendar facts",
                "enabled": True,
                "required_args": ["date_str"],
                "args": {"date_str": {"type": "string"}},
                "inputSchema": {
                    "type": "object",
                    "properties": {"date_str": {"type": "string"}},
                    "required": ["date_str"],
                    "additionalProperties": False,
                },
                "outputSchema": {"type": "object"},
                "contract_id": "moe.precision.calendar_facts",
                "contract_version": "1.0.0",
                "contract_hash": "contract-hash",
                "determinism": "input_only",
                "source_policy": {"kind": "python_stdlib"},
                "structured_result": True,
                "access_kind": "read",
            },
            {
                "name": "disabled_tool",
                "description": "Must not be planned",
                "enabled": False,
                "required_args": [],
                "args": {},
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "outputSchema": {"type": "string"},
            },
        ]
    )
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    client.get.return_value = response

    try:
        with patch("main.httpx.AsyncClient", return_value=client):
            await main._load_mcp_tool_descriptions()

        assert set(main.state.MCP_TOOL_SCHEMAS) == {"calendar_facts"}
        assert main.state.MCP_TOOL_SCHEMAS["calendar_facts"]["contract_hash"] == "contract-hash"
        assert main.state.MCP_TOOL_SCHEMAS["calendar_facts"]["structured_result_required"] is True
        assert set(main.state._MCP_TOOLS_DICT) == {"calendar_facts"}
        assert "disabled_tool" not in main.state.MCP_TOOLS_DESCRIPTION
        assert "stale_tool" not in main.state.MCP_TOOLS_DESCRIPTION
    finally:
        main.state.MCP_TOOL_SCHEMAS.clear()
        main.state.MCP_TOOL_SCHEMAS.update(original_schemas)
        main.state._MCP_TOOLS_DICT.clear()
        main.state._MCP_TOOLS_DICT.update(original_descriptions)
        main.state.MCP_TOOLS_DESCRIPTION = original_text
        main.state.AGENTIC_CODE_TOOLS_DESCRIPTION = original_agentic_text


@pytest.mark.asyncio
async def test_mcp_catalog_failure_clears_stale_executable_schemas():
    original_schemas = dict(main.state.MCP_TOOL_SCHEMAS)
    original_descriptions = dict(main.state._MCP_TOOLS_DICT)
    original_text = main.state.MCP_TOOLS_DESCRIPTION
    original_agentic_text = main.state.AGENTIC_CODE_TOOLS_DESCRIPTION
    main.state.MCP_TOOL_SCHEMAS.clear()
    main.state.MCP_TOOL_SCHEMAS["stale_tool"] = {"required": []}
    main.state._MCP_TOOLS_DICT.clear()
    main.state._MCP_TOOLS_DICT["stale_tool"] = "stale"
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    client.get.side_effect = OSError("MCP unavailable")

    try:
        with patch("main.httpx.AsyncClient", return_value=client):
            await main._load_mcp_tool_descriptions()

        assert main.state.MCP_TOOL_SCHEMAS == {}
        assert main.state._MCP_TOOLS_DICT == {}
        # Static prompt hints may remain, but no stale schema can make them
        # pass the executable planner contract.
        assert "stale_tool" not in main.state.AGENTIC_CODE_TOOLS_DESCRIPTION
    finally:
        main.state.MCP_TOOL_SCHEMAS.clear()
        main.state.MCP_TOOL_SCHEMAS.update(original_schemas)
        main.state._MCP_TOOLS_DICT.clear()
        main.state._MCP_TOOLS_DICT.update(original_descriptions)
        main.state.MCP_TOOLS_DESCRIPTION = original_text
        main.state.AGENTIC_CODE_TOOLS_DESCRIPTION = original_agentic_text


# ── assign_gpu tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_assign_gpu_round_robin():
    """Indices should cycle 0→1→2→3→0→1→2→3 for a 4-GPU endpoint."""
    _reset_gpu_counter("RTX", 0)
    results = [await main.assign_gpu("RTX") for _ in range(8)]
    assert results == [0, 1, 2, 3, 0, 1, 2, 3]


@pytest.mark.asyncio
async def test_assign_gpu_single_gpu():
    """A single-GPU endpoint always returns index 0."""
    # Temporarily add a 1-GPU server to INFERENCE_SERVERS_LIST.
    single_srv = {"name": "SINGLE", "url": "http://single:11434", "gpu_count": 1}
    main.INFERENCE_SERVERS_LIST.append(single_srv)
    _reset_gpu_counter("SINGLE", 0)
    try:
        results = [await main.assign_gpu("SINGLE") for _ in range(4)]
        assert results == [0, 0, 0, 0]
    finally:
        main.INFERENCE_SERVERS_LIST.remove(single_srv)
        main._endpoint_gpu_indices.pop("SINGLE", None)


@pytest.mark.asyncio
async def test_assign_gpu_unknown_endpoint_defaults_to_index_zero():
    """An endpoint not in INFERENCE_SERVERS_LIST still returns a valid index."""
    idx = await main.assign_gpu("GHOST")
    assert isinstance(idx, int)
    assert idx >= 0


# ── _resolve_user_experts tests ───────────────────────────────────────────────


def test_resolve_user_experts_primary_tier(fake_template, fake_perms_with_template):
    """The 'primary' role maps to _tier=1; 'fallback' maps to _tier=2."""
    with patch("services.routing._read_expert_templates", return_value=fake_template):
        result = _routing._resolve_user_experts(fake_perms_with_template)

    assert result is not None
    research_models = result["research"]
    primary   = next(m for m in research_models if m["model"] == "llama2")
    fallback  = next(m for m in research_models if m["model"] == "mistral")

    assert primary["_tier"]  == 1
    assert primary["forced"] is False
    assert fallback["_tier"] == 2
    assert fallback["forced"] is False


def test_resolve_user_experts_always_role(fake_template, fake_perms_with_template):
    """The 'always' role sets forced=True and has no _tier key."""
    with patch("services.routing._read_expert_templates", return_value=fake_template):
        result = _routing._resolve_user_experts(fake_perms_with_template)

    assert result is not None
    coding_models = result["coding"]
    always_model = next(m for m in coding_models if m["model"] == "deepseek")

    assert always_model["forced"] is True
    assert always_model.get("_tier") is None  # always = no tier priority needed


def test_resolve_user_experts_propagates_explicit_thinking_mode(
    fake_template,
    fake_perms_with_template,
):
    fake_template[0]["experts"]["research"]["thinking_mode"] = True
    with patch("services.routing._read_expert_templates", return_value=fake_template):
        result = _routing._resolve_user_experts(fake_perms_with_template)

    assert result is not None
    assert all(model["thinking_mode"] is True for model in result["research"])
    assert all(model["thinking_mode"] is False for model in result["coding"])


def test_resolve_user_experts_no_template_returns_none(fake_perms_no_template):
    """When no template is assigned, the function returns None (global EXPERTS used)."""
    with patch("services.routing._read_expert_templates", return_value=[]):
        result = _routing._resolve_user_experts(fake_perms_no_template)

    assert result is None


def test_resolve_template_prompts_propagates_explicit_disabled_deliberation(
    fake_template,
    fake_perms_with_template,
):
    templates = json.loads(json.dumps(fake_template))
    templates[0]["deliberation_policy"] = {
        "schema_version": "1.0",
        "activation": "disabled",
    }
    with patch("services.routing._read_expert_templates", return_value=templates):
        result = _routing._resolve_template_prompts(fake_perms_with_template)

    assert result["deliberation_policy"]["activation"] == "disabled"
    assert result["deliberation_policy"]["schema_version"] == "1.0"


def test_resolve_template_prompts_rejects_invalid_deliberation_policy(
    fake_template,
    fake_perms_with_template,
):
    templates = json.loads(json.dumps(fake_template))
    templates[0]["deliberation_policy"] = {
        "activation": "sometimes",
    }
    with patch("services.routing._read_expert_templates", return_value=templates):
        with pytest.raises(ValueError, match="invalid deliberation_policy"):
            _routing._resolve_template_prompts(fake_perms_with_template)


def test_resolve_user_experts_missing_template_id_returns_none(fake_template):
    """A permissions string referencing a non-existent template ID → None."""
    perms = json.dumps({"expert_template": ["nonexistent-id"]})
    with patch("services.routing._read_expert_templates", return_value=fake_template):
        result = _routing._resolve_user_experts(perms)

    assert result is None


def test_resolve_user_experts_invalid_json_returns_none():
    """Malformed permissions JSON must not raise; it must return None."""
    result = _routing._resolve_user_experts("{not valid json!!!")
    assert result is None


def test_owned_template_name_wins_over_unauthorized_admin_collision():
    """A same-named, ungranted admin template must not shadow an owned one."""
    admin_template = {
        "id": "admin-id",
        "name": "shared-name",
        "experts": {"coding": {"models": [{"model": "admin-model"}]}},
    }
    owned_template = {
        "name": "shared-name",
        "experts": {"coding": {"models": [{"model": "owned-model"}]}},
    }
    with patch(
        "services.routing._read_expert_templates",
        return_value=[admin_template],
    ):
        selection = _routing._resolve_template_selection(
            json.dumps({"expert_template": []}),
            override_tmpl_id="shared-name",
            user_templates_json=json.dumps({"owned-id": owned_template}),
        )
        experts = _routing._resolve_user_experts(
            json.dumps({"expert_template": []}),
            override_tmpl_id="shared-name",
            user_templates_json=json.dumps({"owned-id": owned_template}),
        )

    assert selection["id"] == "owned-id"
    assert selection["source"] == "owned"
    assert selection["authorized"] is True
    assert experts["coding"][0]["model"] == "owned-model"


def test_authorized_admin_template_wins_over_owned_name_collision():
    admin_template = {
        "id": "admin-id",
        "name": "shared-name",
        "experts": {"coding": {"models": [{"model": "admin-model"}]}},
    }
    owned_template = {
        "name": "shared-name",
        "experts": {"coding": {"models": [{"model": "owned-model"}]}},
    }
    with patch(
        "services.routing._read_expert_templates",
        return_value=[admin_template],
    ):
        selection = _routing._resolve_template_selection(
            json.dumps({"expert_template": ["admin-id"]}),
            override_tmpl_id="shared-name",
            user_templates_json=json.dumps({"owned-id": owned_template}),
        )

    assert selection["id"] == "admin-id"
    assert selection["source"] == "admin"
    assert selection["authorized"] is True


# ── _resolve_template_prompts: Augmented Tool Path toggles ────────────────────


def test_resolve_template_prompts_no_template_uses_global_agent_defaults(fake_perms_no_template):
    """No template assigned → agent_* keys must mirror the global AGENT_*_ENABLED
    config defaults (all False out of the box), same as the CC-profile path."""
    with patch("services.routing._read_expert_templates", return_value=[]):
        result = _routing._resolve_template_prompts(fake_perms_no_template)

    assert result["agent_cache"] is False
    assert result["agent_graphrag"] is False
    assert result["agent_ingest"] is False


def test_resolve_template_prompts_template_can_opt_in(fake_template, fake_perms_with_template):
    tmpl = [{**fake_template[0], "agent_cache": True, "agent_graphrag": True, "agent_ingest": True}]
    with patch("services.routing._read_expert_templates", return_value=tmpl):
        result = _routing._resolve_template_prompts(fake_perms_with_template)

    assert result["agent_cache"] is True
    assert result["agent_graphrag"] is True
    assert result["agent_ingest"] is True


def test_resolve_template_prompts_template_default_off_when_unset(fake_template, fake_perms_with_template):
    """A template that doesn't mention agent_* at all must still default to off,
    not silently inherit True from unrelated defaults like enable_cache/enable_graphrag."""
    with patch("services.routing._read_expert_templates", return_value=fake_template):
        result = _routing._resolve_template_prompts(fake_perms_with_template)

    assert result["agent_cache"] is False
    assert result["agent_graphrag"] is False
    assert result["agent_ingest"] is False


def test_resolve_template_prompts_accepts_nullable_numeric_fields():
    """Portal JSON may persist optional numeric inputs as null."""
    template = {
        "id": "nullable-template",
        "name": "nullable",
        "experts": {},
        "planner_num_ctx": None,
        "judge_num_ctx": None,
        "max_agentic_rounds": None,
        "history_max_turns": None,
    }
    permissions = json.dumps({"expert_template": ["nullable-template"]})
    with patch(
        "services.routing._read_expert_templates",
        return_value=[template],
    ):
        result = _routing._resolve_template_prompts(
            permissions, override_tmpl_id="nullable-template"
        )

    assert result["planner_num_ctx"] == 0
    assert result["judge_num_ctx"] == 0
    assert result["max_agentic_rounds"] == 0
    assert result["history_max_turns"] == 0


# ── _select_node tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_select_node_prefers_warm_model():
    """When RTX already has llama2 loaded, it is preferred over cold TESLA."""
    _clear_ps_cache()
    rtx_response   = _make_ps_response(["llama2"])
    tesla_response = _make_ps_response([])          # TESLA is openai → always cold anyway

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value  = None
    mock_client.get.return_value        = rtx_response

    with patch("main.httpx.AsyncClient", return_value=mock_client):
        chosen = await main._select_node("llama2", ["RTX", "TESLA"])

    assert chosen["name"] == "RTX"


@pytest.mark.asyncio
async def test_select_node_lowest_load_wins_when_all_cold():
    """When all servers are cold, the one with the lowest load score wins.

    RTX has 2 running models on 4 GPUs (score 0.5).
    TESLA is openai (always cold, 0 models, 2 GPUs → score 0.0).
    Expected winner: TESLA.
    """
    _clear_ps_cache()
    # RTX reports 2 loaded models (not llama2 → cold for our query)
    rtx_response = _make_ps_response(["phi4:14b", "magistral:24b"])

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value  = None
    mock_client.get.return_value        = rtx_response

    with patch("main.httpx.AsyncClient", return_value=mock_client):
        chosen = await main._select_node("llama2", ["RTX", "TESLA"])

    assert chosen["name"] == "TESLA"


@pytest.mark.asyncio
async def test_select_node_single_candidate_skips_scoring():
    """With only one allowed endpoint, it is returned directly without HTTP calls."""
    _clear_ps_cache()
    with patch("main.httpx.AsyncClient") as mock_cls:
        chosen = await main._select_node("llama2", ["RTX"])

    # No HTTP call should have been made.
    mock_cls.assert_not_called()
    assert chosen["name"] == "RTX"


@pytest.mark.asyncio
async def test_select_node_unknown_endpoint_returns_fallback():
    """An endpoint not in INFERENCE_SERVERS_LIST returns a dict, not an exception."""
    _clear_ps_cache()
    result = await main._select_node("llama2", ["UNKNOWN_GPU"])
    assert isinstance(result, dict)
    assert result.get("name") == "UNKNOWN_GPU"


# ── _select_node reliability weighting (latency + premature-stop) ─────────────
# _select_node calls services.inference._get_node_latency_stats /
# _get_premature_stop_rate (services/tracking.py) to fold a flood-fill-style
# penalty into load_score — patched at their point of use (services.inference),
# not at services.tracking, since that's where _select_node's closures resolve
# the names from.

@pytest.mark.asyncio
async def test_select_node_premature_stop_penalty_breaks_a_tie():
    """RTX and TESLA are both genuinely idle (empty /api/ps for RTX, TESLA
    always reports 0 running since it's api_type=openai) — tied
    load_score=0.0 pre-penalty, so RTX would normally win as the first
    candidate in a min() tie (see the sibling "no data" test below). A
    recorded premature-stop rate on RTX for this model must be enough to tip
    the choice to TESLA even though RTX's raw load is still tied at 0."""
    _clear_ps_cache()
    rtx_response = _make_ps_response([])   # RTX idle: 0 running models
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value  = None
    mock_client.get.return_value        = rtx_response

    async def _fake_latency(node):
        return {"count": 0, "avg_ms": None}

    async def _fake_pstop(model, node):
        return 0.5 if node == "RTX" else 0.0

    with patch("main.httpx.AsyncClient", return_value=mock_client), \
         patch("services.inference._get_node_latency_stats", side_effect=_fake_latency), \
         patch("services.inference._get_premature_stop_rate", side_effect=_fake_pstop):
        chosen = await main._select_node("llama2", ["RTX", "TESLA"])

    assert chosen["name"] == "TESLA"


@pytest.mark.asyncio
async def test_select_node_no_pstop_data_keeps_original_tie_winner():
    """Same genuinely-tied-at-0.0 setup as above, but with zero
    premature-stop data for either node — behaviour must be unchanged from
    before this feature existed (RTX wins the tie as the first candidate,
    confirming the new reliability term contributes exactly 0.0 when there's
    no evidence either way)."""
    _clear_ps_cache()
    rtx_response = _make_ps_response([])
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value  = None
    mock_client.get.return_value        = rtx_response

    async def _fake_latency(node):
        return {"count": 0, "avg_ms": None}

    async def _fake_pstop(model, node):
        return 0.0

    with patch("main.httpx.AsyncClient", return_value=mock_client), \
         patch("services.inference._get_node_latency_stats", side_effect=_fake_latency), \
         patch("services.inference._get_premature_stop_rate", side_effect=_fake_pstop):
        chosen = await main._select_node("llama2", ["RTX", "TESLA"])

    assert chosen["name"] == "RTX"


@pytest.mark.asyncio
async def test_select_node_latency_penalty_prefers_faster_node():
    """RTX and TESLA genuinely tied on raw load (both idle, 0.0 pre-penalty
    — RTX would win the tie per the sibling test above), but RTX has a much
    higher recorded average latency than the 3s baseline — TESLA must win
    instead despite the tied load."""
    _clear_ps_cache()
    rtx_response = _make_ps_response([])
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value  = None
    mock_client.get.return_value        = rtx_response

    async def _fake_latency(node):
        return {"count": 20, "avg_ms": 15000} if node == "RTX" else {"count": 20, "avg_ms": 500}

    async def _fake_pstop(model, node):
        return 0.0

    with patch("main.httpx.AsyncClient", return_value=mock_client), \
         patch("services.inference._get_node_latency_stats", side_effect=_fake_latency), \
         patch("services.inference._get_premature_stop_rate", side_effect=_fake_pstop):
        chosen = await main._select_node("llama2", ["RTX", "TESLA"])

    assert chosen["name"] == "TESLA"


@pytest.mark.asyncio
async def test_select_node_reliability_lookup_failure_never_breaks_selection():
    """If services.tracking's Redis-backed helpers raise for any reason,
    _select_node must still return a valid choice (defensive try/except
    inside _reliability_factor) instead of propagating the exception."""
    _clear_ps_cache()
    rtx_response = _make_ps_response([])
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value  = None
    mock_client.get.return_value        = rtx_response

    with patch("main.httpx.AsyncClient", return_value=mock_client), \
         patch("services.inference._get_node_latency_stats", side_effect=ConnectionError("redis down")), \
         patch("services.inference._get_premature_stop_rate", side_effect=ConnectionError("redis down")):
        chosen = await main._select_node("llama2", ["RTX", "TESLA"])

    assert chosen["name"] in ("RTX", "TESLA")


@pytest.mark.asyncio
async def test_select_node_single_candidate_skips_reliability_lookup():
    """The len(candidates)==1 fast path must still return immediately without
    ever calling the new reliability helpers — no behavioural change for the
    current production config, where every category has exactly one endpoint."""
    _clear_ps_cache()
    with patch("services.inference._get_node_latency_stats") as mock_lat, \
         patch("services.inference._get_premature_stop_rate") as mock_pstop:
        chosen = await main._select_node("llama2", ["RTX"])

    mock_lat.assert_not_called()
    mock_pstop.assert_not_called()
    assert chosen["name"] == "RTX"


def test_knowledge_model_fields_are_resolved_like_the_other_endpoints(fake_template, fake_perms_with_template):
    tmpl = [{**fake_template[0], "knowledge_model": "qwen3.6:35b@RTX", "knowledge_max_questions": 4, "knowledge_timeout_s": 6,
             "knowledge_broker": "ON", "knowledge_share_context": True}]
    with patch("services.routing._read_expert_templates", return_value=tmpl):
        out = _routing._resolve_template_prompts(fake_perms_with_template)
    assert out["knowledge_model_override"] == "qwen3.6:35b" and out["knowledge_url_override"]
    assert out["knowledge_max_questions"] == 4 and out["knowledge_timeout_s"] == 6.0
    assert out["knowledge_broker"] == "on" and out["knowledge_share_context"] is True


def test_knowledge_fields_default_to_off(fake_perms_no_template):
    out = _routing._resolve_template_prompts(fake_perms_no_template)
    assert out["knowledge_model_override"] == "" and out["knowledge_broker"] == "" and out["knowledge_share_context"] is False
