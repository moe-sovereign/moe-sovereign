"""graph/tool_nodes.py — deterministic data nodes (MCP, GraphRAG, math)."""

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

import state
from config import (
    MODES, _MODEL_ID_TO_MODE, EXPERTS, EXPERT_TIMEOUT, JUDGE_TIMEOUT,
    PLANNER_TIMEOUT, MAX_EXPERT_OUTPUT_CHARS, JUDGE_MODEL,
    HISTORY_MAX_TURNS, HISTORY_MAX_CHARS,
    CACHE_HIT_THRESHOLD, SOFT_CACHE_THRESHOLD, SOFT_CACHE_MAX_EXAMPLES,
    ROUTE_THRESHOLD, ROUTE_GAP, CACHE_MIN_RESPONSE_LEN,
    EXPERT_TIER_BOUNDARY_B, EXPERT_MIN_SCORE, EXPERT_MIN_DATAPOINTS,
    BENCHMARK_SHADOW_TEMPLATE, BENCHMARK_SHADOW_RATE,
    MCP_URL, GRAPH_VIA_MCP, MAX_GRAPH_CONTEXT_CHARS,
    LITELLM_URL, _SEARXNG_URL, _WEB_SEARCH_FALLBACK_DDG,
    _FUZZY_VECTOR_THRESHOLD, _FUZZY_GRAPH_THRESHOLD,
    _GRAPH_COMPRESS_THRESHOLD_FACTOR, _GRAPH_COMPRESS_LLM_MODEL, _GRAPH_COMPRESS_LLM_TIMEOUT,
    CORRECTION_MEMORY_ENABLED, THOMPSON_SAMPLING_ENABLED,
    JUDGE_REFINE_MAX_ROUNDS, JUDGE_REFINE_MIN_IMPROVEMENT,
    _CUSTOM_EXPERT_PROMPTS, PLANNER_MAX_TASKS, PLANNER_RETRIES,
    KAFKA_TOPIC_INGEST, NEO4J_URI, NEO4J_USER, NEO4J_PASS,
    _FALLBACK_ENABLED,
)
from metrics import (
    PROM_EXPERT_CALLS, PROM_CONFIDENCE, PROM_CACHE_HITS, PROM_CACHE_MISSES,
    PROM_SELF_EVAL, PROM_COMPLEXITY, PROM_ACTIVE_REQUESTS,
    PROM_TOOL_CALL_DURATION, PROM_TOOL_TIMEOUTS, PROM_TOOL_FORMAT_ERRORS,
    PROM_TOOL_CALL_SUCCESS, PROM_SEMANTIC_MEMORY_STORED, PROM_SEMANTIC_MEMORY_HITS,
    PROM_CORRECTIONS_INJECTED, PROM_CORRECTIONS_STORED,
    PROM_JUDGE_REFINED, PROM_EXPERT_FAILURES, PROM_SYNTHESIS_CREATED,
    PROM_HISTORY_COMPRESSED, PROM_HISTORY_UNLIMITED,
    PROM_MCP_TOOL_ACCESS,
)
from services.inference import (
    _select_node, _invoke_llm_with_fallback, _invoke_judge_with_retry,
    _get_judge_llm, _get_planner_llm, _get_expert_score, _record_expert_outcome,
    assign_gpu, _ollama_unload, _refine_expert_response,
    _estimate_model_vram_gb, _mark_endpoint_degraded, _endpoint_is_degraded,
)
from services.routing import (
    _resolve_user_experts, _resolve_template_prompts, _server_info, _is_endpoint_error,
)
from services.kafka import _kafka_publish
from services.tracking import _increment_user_budget, _record_stage
from services.llm_instances import judge_llm, planner_llm, ingest_llm, search
from services.helpers import (
    _log_tool_eval,
    _update_rate_limit_headers, _check_rate_limit_exhausted,
    _conf_format_for_mode, _get_expert_prompt,
    _truncate_history, _apply_semantic_memory,
    _web_search_with_citations,
    _store_response_metadata, _self_evaluate, _neo4j_terms_exist,
    _report,
    _shadow_request, _shadow_lock,
)
from services.templates import _read_expert_templates, _read_cc_profiles
from services.skills import _build_skill_catalog
from prompts import (
    SYNTHESIS_PERSISTENCE_INSTRUCTION,
    PROVENANCE_INSTRUCTION,
    DEFAULT_PLANNER_ROLE,
)
from prompts import _ROUTE_PROTOTYPES, _RESEARCH_DETECT
from parsing import (
    _oai_content_to_str, _anthropic_content_to_text,
    _extract_images, _extract_oai_images,
    _anthropic_to_openai_messages, _anthropic_tools_to_openai,
)

logger = logging.getLogger("MOE-SOVEREIGN")

# AgentState import — defined in pipeline/state.py
from pipeline.state import AgentState
from compliance_cag import get_compliance_context
from episodic_memory import get_episode_hint
from services.retrieval_attribution import graph_attribution_chunks
from services.deadline import (
    RequestDeadlineExceeded,
    remaining_timeout,
    wait_for_budget,
)
from services.pipeline.contracts import (
    canonical_json_hash,
    precision_evidence_input,
    precision_args_match,
    resolve_task_result_refs,
    tool_schema_contract_hash,
)


def _validate_tool_result(result_str: str, tool: str) -> tuple[bool, str]:
    """Sanity-check MCP tool output before it enters working memory."""
    if not result_str or len(result_str.strip()) < 3:
        return False, "empty_result"
    stripped = result_str.lstrip()
    lower = stripped.casefold()
    if lower.startswith(("error:", "fehler:")) or (
        lower.startswith("[")
        and ("error" in lower[:80] or "fehler" in lower[:80])
    ):
        return False, "error_result"
    if lower.startswith("{"):
        try:
            parsed = json.loads(stripped)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict) and parsed.get("error"):
            return False, "error_result"
    return True, ""


def _normalize_tool_args(args: dict, schema: dict) -> dict:
    """Apply only explicit schema defaults; never infer missing values."""
    normalized = dict(args)
    properties = schema.get("args", {}) if isinstance(schema, dict) else {}
    if isinstance(properties, dict):
        for name, property_schema in properties.items():
            if (
                name not in normalized
                and isinstance(property_schema, dict)
                and "default" in property_schema
            ):
                normalized[name] = property_schema["default"]
    return normalized


def _validate_tool_args(args: Any, schema: Any) -> tuple[bool, str]:
    """Validate all discovered JSON-Schema constraints before MCP invoke."""
    if not isinstance(args, dict):
        return False, "pre_call_schema_invalid:args:not_object"
    if not isinstance(schema, dict):
        return False, "pre_call_schema_invalid:schema:unavailable"
    properties = schema.get("args", {})
    required = schema.get("required", [])
    if not isinstance(properties, dict) or not isinstance(required, (list, tuple)):
        return False, "pre_call_schema_invalid:schema:malformed"
    object_schema = {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": bool(schema.get("additionalProperties", False)),
    }
    try:
        Draft202012Validator.check_schema(object_schema)
        errors = sorted(
            Draft202012Validator(object_schema).iter_errors(args),
            key=lambda item: (
                tuple(str(part) for part in item.absolute_path),
                item.message,
            ),
        )
    except SchemaError:
        return False, "pre_call_schema_invalid:schema:malformed"
    if not errors:
        return True, ""
    first = errors[0]
    path = ".".join(str(part) for part in first.absolute_path) or "args"
    validator = str(first.validator or "invalid")
    return False, f"pre_call_schema_invalid:{path}:{validator}"


def _validate_structured_mcp_result(
    data: Any,
    schema: dict,
    tool: str,
    args: dict,
) -> tuple[bool, str, dict]:
    """Validate a migrated MCP result envelope and its typed facts."""
    if not schema.get("structured_result_required"):
        return True, "", {}
    if not isinstance(data, dict) or not isinstance(data.get("structured_result"), dict):
        return False, "structured_result_missing", {}
    payload = data["structured_result"]
    contract_hash = tool_schema_contract_hash(schema)
    evidence_args = precision_evidence_input(args, schema)
    fixed_fields = {
        "status": "completed",
        "tool": tool,
        "contract_id": schema.get("contract_id"),
        "contract_version": schema.get("contract_version"),
        "contract_hash": contract_hash,
        "input_normalized": evidence_args,
        "determinism": schema.get("determinism"),
    }
    if any(payload.get(name) != expected for name, expected in fixed_fields.items()):
        return False, "structured_result_contract_mismatch", payload
    facts = payload.get("facts")
    output_schema = schema.get("output_schema")
    if not isinstance(output_schema, dict):
        return False, "structured_result_output_schema_missing", payload
    try:
        Draft202012Validator.check_schema(output_schema)
        errors = list(Draft202012Validator(output_schema).iter_errors(facts))
    except SchemaError:
        return False, "structured_result_output_schema_invalid", payload
    if errors:
        return False, "structured_result_facts_invalid", payload
    source_metadata = payload.get("source")
    if (
        not isinstance(source_metadata, dict)
        or not source_metadata.get("kind")
        or not source_metadata.get("name")
        or not source_metadata.get("version")
    ):
        return False, "structured_result_source_invalid", payload
    warnings = payload.get("warnings")
    if not isinstance(warnings, list):
        return False, "structured_result_warnings_invalid", payload
    expected_result_hash = canonical_json_hash(
        {
            "contract_hash": contract_hash,
            "input_normalized": evidence_args,
            "facts": facts,
        }
    )
    if payload.get("result_hash") != expected_result_hash:
        return False, "structured_result_hash_mismatch", payload
    return True, "", payload


def _task_result_ref_ids(args: dict) -> list[str]:
    """Collect every ``$task_result`` reference's task id inside ``args``."""
    ids: list[str] = []
    for value in args.values():
        if isinstance(value, dict) and isinstance(value.get("$task_result"), str):
            ids.append(value["$task_result"])
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and isinstance(item.get("$task_result"), str):
                    ids.append(item["$task_result"])
    return ids


def _topological_batches(
    precision_tasks: list[dict],
) -> tuple[list[list[dict]], list[dict]]:
    """Order tasks into dependency-respecting batches (Kahn's algorithm).

    A task with no ``$task_result`` reference lands in batch 0 — for a plan
    without any chaining this degenerates to a single batch identical to the
    previous unconditional parallel dispatch. Returns ``(batches,
    unscheduled)``: any remaining cycle (should already be rejected by
    contract validation, but checked defensively here) leaves the offending
    tasks in ``unscheduled`` instead of a batch; the caller must treat them
    as failed rather than silently dropping them.
    """
    by_id = {
        task.get("id"): task
        for task in precision_tasks
        if isinstance(task.get("id"), str)
    }
    remaining = list(precision_tasks)
    scheduled_ids: set[str] = set()
    batches: list[list[dict]] = []
    while remaining:
        ready_ids = {
            id(task)
            for task in remaining
            if all(
                ref_id in scheduled_ids or ref_id not in by_id
                for ref_id in _task_result_ref_ids(task.get("mcp_args") or {})
            )
        }
        if not ready_ids:
            break
        ready = [task for task in remaining if id(task) in ready_ids]
        batches.append(ready)
        for task in ready:
            task_id = task.get("id")
            if isinstance(task_id, str):
                scheduled_ids.add(task_id)
        remaining = [task for task in remaining if id(task) not in ready_ids]
    return batches, remaining


async def mcp_node(state_: AgentState):
    """Executes precision tool calls via MCP server — all in parallel."""
    if state_.get("cache_hit"):
        return {"mcp_result": ""}

    precision_tasks = [
        t
        for t in state_.get("plan", [])
        if isinstance(t, dict) and t.get("category") == "precision_tools"
    ]
    if not precision_tasks:
        return {"mcp_result": ""}

    _iteration = int(state_.get("agentic_iteration") or 0)
    _denied_events: list[dict] = []
    _denied_evidence: list[dict] = []

    # Per-User MCP-Tool Permission-Check
    allowed_mcp = state_.get("user_permissions", {}).get("mcp_tool")
    if allowed_mcp is not None and "*" not in allowed_mcp:
        _denied_tasks = [t for t in precision_tasks if t.get("mcp_tool") not in allowed_mcp]
        for _dt in _denied_tasks:
            _dtool = _dt.get("mcp_tool")
            _denied_events.append(
                {
                    "task_id": _dt.get("id", ""),
                    "category": "precision_tools",
                    "status": "denied",
                    "executor": "mcp",
                    "iteration": _iteration,
                    "reason": "user_permissions",
                }
            )
            _denied_evidence.append(
                {
                    "task_id": _dt.get("id", ""),
                    "tool": _dtool,
                    "args": _dt.get("mcp_args", {}),
                    "iteration": _iteration,
                    "status": "denied",
                    "result": "",
                    "error": "user_permissions",
                    "source": "mcp_precision",
                }
            )
            _dak = state.MCP_TOOL_SCHEMAS.get(_dtool, {}).get("access_kind", "read")
            try:
                from services.decision_log import log_decision, DecisionType
                log_decision(
                    DecisionType.MCP_TOOL_ACCESS, request_id=state_.get("response_id", ""),
                    rationale=f"MCP tool '{_dtool}' not in user_permissions.mcp_tool allowlist",
                    metadata={"tool_name": _dtool, "access_kind": _dak, "verdict": "deny", "reason": "user_permissions"},
                )
                PROM_MCP_TOOL_ACCESS.labels(tool=_dtool, access_kind=_dak, verdict="deny").inc()
            except Exception as _dle:
                logger.debug(f"decision log emit failed for {_dtool}: {_dle}")
        precision_tasks = [t for t in precision_tasks if t.get("mcp_tool") in allowed_mcp]
        if not precision_tasks:
            logger.info("⛔ MCP tools not enabled for this user")
            return {
                "mcp_result": "",
                "task_events": _denied_events,
                "mcp_evidence": _denied_evidence,
            }

    tool_names = [t.get("mcp_tool") for t in precision_tasks]
    await _report(f"⚙️ MCP Precision Tools: {', '.join(tool_names)}")
    await _record_stage(state_.get("response_id", ""), "mcp", "started", ", ".join(tool_names))
    logger.info(f"--- [NODE] MCP ({len(precision_tasks)} Tools parallel) ---")

    # Working Memory accumulators — carry over facts from previous iterations
    _wm: dict = dict(state_.get("working_memory") or {})
    _log: list = list(state_.get("tool_calls_log") or [])
    _failures: list = list(state_.get("tool_failures") or [])
    _ts_now = lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    async def call_tool(
        client: httpx.AsyncClient, task: dict, resolved_results: dict
    ) -> dict:
        tool = task.get("mcp_tool")
        args = dict(task.get("mcp_args") or {})
        desc = task.get("task", tool)
        safe_desc = (
            f"{tool} (sensitive input redacted)"
            if tool == "structured_validate"
            else desc
        )
        task_id = task.get("id", "")
        required_intents = [
            intent
            for intent in (state_.get("required_precision_intents") or [])
            if isinstance(intent, dict) and intent.get("tool") == tool
        ]
        mandatory_contract = bool(required_intents)
        contract_hash = ""

        def _outcome(
            status: str,
            text: str,
            *,
            used_args: dict,
            result: str = "",
            error: str = "",
            structured_result: dict | None = None,
        ) -> dict:
            structured = structured_result or {}
            result_facts = None
            if status == "completed" and result:
                try:
                    parsed_result = json.loads(result)
                except (json.JSONDecodeError, TypeError):
                    parsed_result = None
                if isinstance(parsed_result, dict):
                    result_facts = parsed_result
            return {
                "text": text,
                "result_facts": result_facts,
                "event": {
                    "task_id": task_id,
                    "category": "precision_tools",
                    "status": status,
                    "executor": "mcp",
                    "iteration": _iteration,
                    "reason": error[:200],
                },
                "evidence": {
                    "task_id": task_id,
                    "tool": tool,
                    "args": used_args,
                    "iteration": _iteration,
                    "status": status,
                    "result": result[:65536],
                    "error": error[:500],
                    "source": "mcp_precision",
                    "contract_hash": contract_hash,
                    "input_hash": canonical_json_hash(used_args),
                    "contract_id": structured.get("contract_id", _schema.get("contract_id", "") if isinstance(_schema, dict) else ""),
                    "contract_version": structured.get("contract_version", _schema.get("contract_version", "") if isinstance(_schema, dict) else ""),
                    "facts": structured.get("facts"),
                    "result_hash": structured.get("result_hash", ""),
                    "determinism": structured.get("determinism", _schema.get("determinism", "") if isinstance(_schema, dict) else ""),
                    "source_metadata": structured.get("source"),
                    "warnings": structured.get("warnings", []),
                },
            }

        # Fix common planner argument-naming mismatches so MCP tools don't
        # reject the call and fall back to LLM hallucination.
        if tool == "calculate" and "operation" in args and "expression" not in args:
            args["expression"] = args.pop("operation")
        if tool == "calculate" and "formula" in args and "expression" not in args:
            args["expression"] = args.pop("formula")
        # Mandatory contracts use the immutable preflight snapshot. A live
        # catalog change between preflight and dispatch is a hard error, not an
        # invitation to silently run a different contract.
        _schema = state.MCP_TOOL_SCHEMAS.get(tool, {})
        if mandatory_contract:
            _snapshot = (state_.get("precision_contract_snapshot") or {}).get(tool)
            _expected_hashes = {
                str(intent.get("schema_hash") or "") for intent in required_intents
            }
            _live_hash = tool_schema_contract_hash(_schema)
            _snapshot_hash = tool_schema_contract_hash(_snapshot)
            contract_hash = _snapshot_hash
            if (
                not isinstance(_snapshot, dict)
                or not _snapshot_hash
                or _snapshot_hash not in _expected_hashes
                or _live_hash != _snapshot_hash
            ):
                error = "precision_contract_changed"
                logger.error("MCP mandatory contract drift for %s", tool)
                return _outcome(
                    "failed",
                    f"[{safe_desc}] MCP contract error: {error}",
                    used_args=args,
                    error=error,
                )
            _schema = _snapshot
        elif isinstance(_schema, dict):
            contract_hash = tool_schema_contract_hash(_schema)

        _chained_args = resolve_task_result_refs(args, resolved_results)
        if _chained_args is None:
            error = "upstream_task_result_unavailable"
            logger.error("MCP chained-args resolution failed for %s", tool)
            return _outcome(
                "failed",
                f"[{safe_desc}] MCP contract error: {error}",
                used_args=args,
                error=error,
            )
        args = _chained_args

        args = _normalize_tool_args(args, _schema)
        valid_args, args_error = _validate_tool_args(args, _schema)
        if not valid_args:
            error = args_error
            logger.error("MCP contract drift for %s: %s", tool, error)
            return _outcome(
                "failed",
                f"[{safe_desc}] MCP contract error: {error}",
                used_args=args,
                error=error,
            )
        if mandatory_contract and not any(
            precision_args_match(args, intent.get("args") or {}, _schema)
            for intent in required_intents
        ):
            error = "precision_evidence_mismatch"
            logger.error("MCP mandatory args differ from preflight for %s", tool)
            return _outcome(
                "failed",
                f"[{safe_desc}] MCP contract error: {error}",
                used_args=args,
                error=error,
            )
        audit_args = precision_evidence_input(args, _schema)
        await _report(f"⚙️ MCP-Call: {tool}\nArgs: {json.dumps(audit_args, ensure_ascii=False, indent=2)}")
        _access_kind = _schema.get("access_kind", "read")
        try:
            from services.decision_log import log_decision, DecisionType
            log_decision(
                DecisionType.MCP_TOOL_ACCESS, request_id=state_.get("response_id", ""),
                rationale=f"MCP tool '{tool}' dispatched",
                metadata={"tool_name": tool, "access_kind": _access_kind, "verdict": "allow"},
            )
            PROM_MCP_TOOL_ACCESS.labels(tool=tool, access_kind=_access_kind, verdict="allow").inc()
        except Exception as _dle:
            logger.debug(f"decision log emit failed for {tool}: {_dle}")
        _mcp_t0 = time.monotonic()
        try:
            resp = await client.post(f"{MCP_URL}/invoke", json={"tool": tool, "args": args})
            resp.raise_for_status()
            data = resp.json()
            _mcp_dt = round(time.monotonic() - _mcp_t0, 3)
            if "error" in data:
                err_str = data['error']
                await _report(f"⚙️ MCP error [{tool}]: {err_str}")
                try:
                    from services.decision_log import log_decision, DecisionType
                    _derr_reason = "server_disabled" if data.get("reason") == "disabled" else "server_error"
                    log_decision(
                        DecisionType.MCP_TOOL_ACCESS, request_id=state_.get("response_id", ""),
                        rationale=f"MCP tool '{tool}' rejected by server: {err_str}",
                        metadata={"tool_name": tool, "access_kind": _access_kind, "verdict": "deny", "reason": _derr_reason},
                    )
                    PROM_MCP_TOOL_ACCESS.labels(tool=tool, access_kind=_access_kind, verdict="deny").inc()
                except Exception as _dle:
                    logger.debug(f"decision log emit failed for {tool}: {_dle}")
                _log_tool_eval({
                    "ts": _ts_now(), "source": "mcp_node",
                    "chat_id": state_.get("chat_id", ""), "user_id": state_.get("user_id", ""),
                    "tool": tool, "args": audit_args, "task": safe_desc, "result": None,
                    "error": err_str, "latency_s": _mcp_dt,
                    "caller": "orchestrator_pipeline", "template": state_.get("template_name", ""),
                })
                _entry = {"tool": tool, "args": audit_args, "result": None, "status": "error", "error": err_str, "ts": _ts_now()}
                _log.append(_entry)
                _failures.append(_entry)
                # Mandatory precision arguments are immutable after preflight.
                # A model may never reinterpret them after a server failure.
                if mandatory_contract or (_schema.get("evidence_policy") or {}).get("redact_input_fields"):
                    return _outcome(
                        "failed",
                        f"[{safe_desc}] Error: {err_str}",
                        used_args=args,
                        error="mandatory_precision_retry_forbidden",
                    )

                # Optional/non-mandatory tools retain one bounded correction
                # attempt, but corrected args must satisfy the full schema.
                fix_prompt = (
                    f"The MCP tool '{tool}' returned an error: {err_str}\n"
                    f"Original args: {json.dumps(args)}\n"
                    f"Return ONLY a corrected JSON object for the args. No explanation."
                )
                try:
                    from parsing import _extract_json
                    fix_res = await _invoke_judge_with_retry(state_, fix_prompt, max_retries=1)
                    corrected_args = _extract_json(fix_res.content or "")
                    if not isinstance(corrected_args, dict):
                        raise ValueError("judge returned non-dict JSON")
                    corrected_args = _normalize_tool_args(corrected_args, _schema)
                    corrected_valid, corrected_error = _validate_tool_args(
                        corrected_args,
                        _schema,
                    )
                    if not corrected_valid:
                        raise ValueError(corrected_error)
                    logger.info(f"🔄 MCP retry [{tool}] with corrected args: {corrected_args}")
                    resp2 = await client.post(f"{MCP_URL}/invoke", json={"tool": tool, "args": corrected_args})
                    resp2.raise_for_status()
                    data2 = resp2.json()
                    if "error" not in data2:
                        result_str2 = data2.get("result", "")
                        await _report(f"⚙️ MCP retry OK [{tool}]:\n{result_str2}")
                        valid, validation_reason = _validate_tool_result(result_str2, tool)
                        if len(str(result_str2)) > int((_schema.get("limits") or {}).get("max_result_chars", 65536)):
                            valid = False
                            validation_reason = "tool_result_too_large"
                        structured_valid, structured_reason, structured_result = (
                            _validate_structured_mcp_result(
                                data2,
                                _schema,
                                tool,
                                corrected_args,
                            )
                        )
                        valid = valid and structured_valid
                        if not structured_valid:
                            validation_reason = structured_reason
                        if valid:
                            corrected_audit_args = precision_evidence_input(corrected_args, _schema)
                            wm_key = f"{tool}:{json.dumps(corrected_audit_args)[:60]}"
                            _wm[wm_key] = {"value": result_str2[:500], "source": "mcp_node", "confidence": 0.8, "ts": _ts_now()}
                        _log.append({"tool": tool, "args": precision_evidence_input(corrected_args, _schema), "result": result_str2[:200], "status": "ok_retry", "ts": _ts_now()})
                        if valid:
                            return _outcome(
                                "completed",
                                f"[{safe_desc}] {result_str2}",
                                used_args=corrected_args,
                                result=result_str2,
                                structured_result=structured_result,
                            )
                        return _outcome(
                            "failed",
                            f"[{safe_desc}] Invalid MCP result",
                            used_args=corrected_args,
                            result=result_str2,
                            error=validation_reason or "invalid_tool_result",
                            structured_result=structured_result,
                        )
                except Exception as retry_exc:
                    logger.debug(f"MCP arg-correction retry failed for {tool}: {retry_exc}")
                return _outcome(
                    "failed",
                    f"[{safe_desc}] Error: {err_str}",
                    used_args=args,
                    error=err_str,
                )
            result_str = data.get('result', '')
            await _report(f"⚙️ MCP result [{tool}]:\n{result_str}")
            logger.info(f"🔧 MCP: [{safe_desc}] {result_str[:120]}")
            _log_tool_eval({
                "ts": _ts_now(), "source": "mcp_node",
                "chat_id": state_.get("chat_id", ""), "user_id": state_.get("user_id", ""),
                "tool": tool, "args": audit_args, "task": safe_desc, "result": result_str[:500],
                "error": None, "latency_s": _mcp_dt,
                "caller": "orchestrator_pipeline", "template": state_.get("template_name", ""),
            })
            _log.append({"tool": tool, "args": audit_args, "result": result_str[:200], "status": "ok", "ts": _ts_now()})
            # Write validated results to working memory
            valid, reason = _validate_tool_result(result_str, tool)
            if len(str(result_str)) > int((_schema.get("limits") or {}).get("max_result_chars", 65536)):
                valid = False
                reason = "tool_result_too_large"
            structured_valid, structured_reason, structured_result = (
                _validate_structured_mcp_result(data, _schema, tool, args)
            )
            valid = valid and structured_valid
            if not structured_valid:
                reason = structured_reason
            if valid:
                wm_key = f"{tool}:{json.dumps(audit_args)[:60]}"
                _wm[wm_key] = {"value": result_str[:500], "source": "mcp_node", "confidence": 0.9, "ts": _ts_now()}
            else:
                logger.debug(f"MCP result for {tool} failed validation: {reason}")
            return _outcome(
                "completed" if valid else "failed",
                f"[{safe_desc}] {result_str}",
                used_args=args,
                result=result_str,
                error="" if valid else reason,
                structured_result=structured_result,
            )
        except Exception as e:
            _mcp_dt = round(time.monotonic() - _mcp_t0, 3)
            logger.error(f"MCP Tool '{tool}' failed: {e}")
            await _report(f"⚙️ MCP exception [{tool}]: {e}")
            _log_tool_eval({
                "ts": _ts_now(), "source": "mcp_node",
                "chat_id": state_.get("chat_id", ""), "user_id": state_.get("user_id", ""),
                "tool": tool, "args": precision_evidence_input(args, _schema), "task": safe_desc, "result": None,
                "error": str(e)[:300], "latency_s": _mcp_dt,
                "caller": "orchestrator_pipeline", "template": state_.get("template_name", ""),
            })
            _entry = {"tool": tool, "args": precision_evidence_input(args, _schema), "result": None, "status": "exception", "error": str(e)[:200], "ts": _ts_now()}
            _log.append(_entry)
            _failures.append(_entry)
            return _outcome(
                "failed",
                f"[{safe_desc}] MCP error: {e}",
                used_args=args,
                error=str(e),
            )

    _mcp_timeout = remaining_timeout(state_, 30.0, stage="mcp")
    _resolved_task_results: dict = {}
    _results_by_id: dict = {}
    _batches, _unscheduled = _topological_batches(precision_tasks)
    if _unscheduled:
        # Defensive only: a dependency cycle should already have been
        # rejected by contract validation before the plan reached this
        # node. Running these tasks anyway (instead of dropping them) still
        # produces a deterministic per-task failure, since their reference
        # can never resolve from `_resolved_task_results`.
        logger.error(
            "MCP precision task graph left %d task(s) unscheduled (cycle?)",
            len(_unscheduled),
        )
        _batches = _batches + [_unscheduled]
    async with httpx.AsyncClient(timeout=_mcp_timeout) as client:
        for _batch in _batches:
            _batch_results = await asyncio.gather(
                *[call_tool(client, t, _resolved_task_results) for t in _batch]
            )
            for _task, _result in zip(_batch, _batch_results):
                _results_by_id[id(_task)] = _result
                _task_id = _task.get("id")
                _facts = _result.get("result_facts")
                if isinstance(_task_id, str) and isinstance(_facts, dict):
                    _resolved_task_results[_task_id] = _facts
    results = [_results_by_id[id(t)] for t in precision_tasks]
    from services.precision_telemetry import record_precision_event
    precision_mode = str(state_.get("precision_contract_mode") or "enforce")
    for task, result in zip(precision_tasks, results):
        tool_name = str(task.get("mcp_tool") or "")
        outcome = "completed" if result.get("event", {}).get("status") == "completed" else "failed"
        reason = str(result.get("event", {}).get("reason") or "")
        stage = (
            "input_schema" if "schema_invalid" in reason and "output" not in reason
            else "output_schema" if "output_schema" in reason or "structured_result" in reason
            else "contract" if "contract" in reason
            else "tool"
        )
        event_outcome = "drift" if "changed" in reason else outcome
        record_precision_event(
            stage, event_outcome, tool=tool_name, mode=precision_mode,
        )

    combined = "\n".join(result["text"] for result in results)
    await _report(f"⚙️ MCP: {len(results)} result(s) received")
    await _record_stage(state_.get("response_id", ""), "mcp", "done")
    logger.info(f"🔧 MCP: {combined[:300]}")
    if _wm:
        logger.info(f"📝 Working Memory: {len(_wm)} facts extracted")
    return {
        "mcp_result": combined,
        "working_memory": _wm,
        "tool_calls_log": _log,
        "tool_failures": _failures,
        "task_events": _denied_events + [result["event"] for result in results],
        "mcp_evidence": _denied_evidence + [
            result["evidence"] for result in results
        ],
    }


async def graph_rag_node(state_: AgentState):
    """Fetch structured graph context from Neo4j — parallel to LLM experts.
    When GRAPH_VIA_MCP=true, the MCP server is used as interface (graph-as-a-tool),
    otherwise direct access to graph_manager (fallback, backwards compatible).
    """
    if state_.get("cache_hit"):
        return {"graph_context": "", "retrieved_graph_chunks": []}
    # Template toggle: skip GraphRAG if disabled
    if not state_.get("enable_graphrag", True):
        logger.info("GraphRAG disabled by template toggle")
        return {"graph_context": "", "retrieved_graph_chunks": []}
    # Complexity routing: skip for trivial requests (complexity_estimator sets skip_graph)
    if state_.get("skip_graph"):
        logger.info("⚡ GraphRAG skipped (complexity routing: trivial/skip_graph)")
        return {"graph_context": "", "retrieved_graph_chunks": []}
    if not GRAPH_VIA_MCP and state.graph_manager is None:
        return {"graph_context": "", "retrieved_graph_chunks": []}
    plan = state_.get("plan", [])
    categories = [t.get("category", "") for t in plan if isinstance(t, dict)]

    # GraphRAG on-demand: skip the Neo4j query for queries that are clearly about
    # public external facts (papers, databases, media) rather than internal ontology.
    # We still run if the plan explicitly includes knowledge_healing (graph needed).
    _has_knowledge_healing = "knowledge_healing" in categories
    _is_public_fact_query = bool(_RESEARCH_DETECT.search(state_.get("input", "")))
    if _is_public_fact_query and not _has_knowledge_healing:
        logger.info("⚡ GraphRAG skipped (public-fact query — internal graph not relevant)")
        await _report("⚡ GraphRAG: skipped (external research query)")
        await _record_stage(state_.get("response_id", ""), "graph_rag", "skipped")
        return {"graph_context": "", "retrieved_graph_chunks": []}

    # GraphRAG-Cache (Valkey, TTL=3600s)
    import hashlib as _hashlib
    _graph_cache_key = f"moe:graph:{_hashlib.sha256((state_['input'][:200] + ''.join(sorted(categories))).encode()).hexdigest()[:16]}"
    if state.redis_client is not None:
        try:
            _cached_ctx = await state.redis_client.get(_graph_cache_key)
            if _cached_ctx:
                _cached_ctx_str = _cached_ctx if isinstance(_cached_ctx, str) else _cached_ctx.decode()
                logger.info(f"🔗 GraphRAG cache hit (Valkey) — {len(_cached_ctx_str)} chars")
                await _report(f"🔗 GraphRAG: context from Valkey cache ({len(_cached_ctx_str)} chars)")
                await _record_stage(state_.get("response_id", ""), "graph_rag", "cache_hit")
                return {
                    "graph_context": _cached_ctx_str,
                    "retrieved_graph_chunks": graph_attribution_chunks(_cached_ctx_str),
                }
        except Exception as _ge:
            logger.debug(f"GraphRAG cache read error: {_ge}")

    # CAG check: for known static compliance domains (BAIT, VAIT, DORA, KRITIS,
    # MaRisk) inject pre-loaded context directly — no Neo4j round-trip needed.
    # Chan et al. 2024: static knowledge is more reliable than retrieval for
    # stable, authoritative regulatory content.
    _cag_ctx = get_compliance_context(state_["input"], categories)
    if _cag_ctx:
        await _report(f"📋 CAG: static compliance context ({len(_cag_ctx)} chars, Neo4j skipped)")
        if state.redis_client is not None:
            asyncio.create_task(state.redis_client.setex(_graph_cache_key, 3600, _cag_ctx))
        return {"graph_context": _cag_ctx, "retrieved_graph_chunks": []}

    # Episode hint: retrieve routing context from similar past tasks.
    # Appended to graph_context so the judge can use past strategies as signal.
    _ep_task_type = categories[0] if categories else "general"
    if state.graph_manager is not None:
        try:
            _ep_hint = await wait_for_budget(
                get_episode_hint(
                    state.graph_manager.driver,
                    state_["input"],
                    _ep_task_type,
                    state_.get("user_id", ""),
                ),
                state_,
                5.0,
                stage="episode_hint",
            )
        except RequestDeadlineExceeded:
            # The 5 s stage cap also raises RequestDeadlineExceeded. A slow episode query must not end the request:
            # only a spent request budget does, and remaining_timeout() raises exactly in that case.
            remaining_timeout(state_, 0.1, stage="episode_hint")
            logger.warning("Episode hint timed out, continuing without it")
            _ep_hint = ""
    else:
        _ep_hint = ""

    await _report("🔗 GraphRAG — knowledge graph query (Neo4j)...")
    await _record_stage(state_.get("response_id", ""), "graph_rag", "started")
    try:
        if GRAPH_VIA_MCP:
            # Flange: MCP server as graph-as-a-tool (accessible to external agents)
            _graph_timeout = remaining_timeout(
                state_,
                15.0,
                stage="graph_rag_mcp",
            )
            async with httpx.AsyncClient(timeout=_graph_timeout) as _client:
                _resp = await _client.post(
                    f"{MCP_URL}/invoke",
                    json={"tool": "graph_query", "args": {"query": state_["input"], "categories": categories}},
                )
                _resp.raise_for_status()
                ctx = _resp.json().get("result", "")
        else:
            # Direct access (default, backwards compatible)
            _tenant_ids = state_.get("tenant_ids", [])
            ctx = await wait_for_budget(
                state.graph_manager.query_context(
                    state_["input"],
                    categories,
                    tenant_ids=_tenant_ids or None,
                ),
                state_,
                15.0,
                stage="graph_rag",
            )

        if ctx:
            # Annotate procedural requirements so the merger treats them as hard facts.
            if "[Procedural Requirements]" in ctx:
                ctx = (
                    "[Note: The following knowledge graph facts describe physical or "
                    "procedural requirements. Include these requirements explicitly in "
                    "your answer.]\n\n" + ctx
                )
            if _ep_hint:
                ctx = ctx + "\n\n" + _ep_hint if ctx else _ep_hint
            logger.info(f"📊 GraphRAG: {len(ctx)} chars context found (via_mcp={GRAPH_VIA_MCP})")
            await _report(f"🔗 GraphRAG: {len(ctx)} chars structured context")
            await _record_stage(state_.get("response_id", ""), "graph_rag", "done")
            if state.redis_client is not None:
                asyncio.create_task(state.redis_client.setex(_graph_cache_key, 3600, ctx))
        else:
            # No Neo4j context, but still inject episode hint if available.
            ctx = _ep_hint
            await _report("🔗 GraphRAG: no matching context found")
            await _record_stage(state_.get("response_id", ""), "graph_rag", "miss")

        # Capture the exact Neo4j entity lines before optional Chroma/HABE
        # modulation changes the context. This makes the attribution loop
        # identifiable and safe to persist after synthesis.
        _retrieved_graph_chunks = graph_attribution_chunks(ctx)

        # Domain-filtered ChromaDB retrieval using planner-extracted metadata_filters
        _meta_filters = state_.get("metadata_filters") or {}
        if _meta_filters and state.cache_collection is not None:
            try:
                _where: Dict = {k: {"$eq": v} for k, v in _meta_filters.items() if isinstance(v, str) and v}
                if len(_where) > 1:
                    _where = {"$and": [{k: v} for k, v in _where.items()]}
                _chroma_res = await asyncio.to_thread(
                    state.cache_collection.query,
                    query_texts=[state_["input"]],
                    n_results=3,
                    where=_where,
                )
                _chroma_docs = (_chroma_res.get("documents") or [[]])[0]
                _chroma_docs = [d for d in _chroma_docs if d]
                if _chroma_docs:
                    _chroma_snippet = "\n---\n".join(_chroma_docs)
                    _filter_label = ", ".join(f"{k}={v}" for k, v in _meta_filters.items())
                    ctx = (ctx + f"\n\n[Domain-Filtered Memory ({_filter_label})]\n{_chroma_snippet}"
                           if ctx else f"[Domain-Filtered Memory ({_filter_label})]\n{_chroma_snippet}")
                    logger.info(f"🔎 Filtered ChromaDB: {len(_chroma_docs)} docs ({_filter_label})")
            except Exception as _cf_exc:
                logger.debug(f"Filtered ChromaDB lookup skipped: {_cf_exc}")

        # Extract entity metadata for causal-path logging.
        # Neo4j results contain lines like: "Entity: <name> (<type>) confidence=<val>"
        _entity_meta: list = []
        if ctx:
            _ent_re = re.compile(
                r"Entity:\s*([^\(]+?)\s*\(([^)]+)\).*?confidence[=:]\s*([0-9.]+)",
                re.IGNORECASE,
            )
            for _match in _ent_re.finditer(ctx[:4000]):
                _entity_meta.append({
                    "name": _match.group(1).strip(),
                    "type": _match.group(2).strip(),
                    "confidence": float(_match.group(3)),
                })

        # VSA Background (HABE) Injection & Context Modulation
        if state_.get("enable_habe"):
            try:
                import os
                import numpy as np
                from services.vsa_background import HolographicBackgroundEngine
                
                # Resolve paths relative to tool_nodes.py directory
                script_dir = os.path.dirname(os.path.abspath(__file__))
                repo_root = os.path.dirname(script_dir)
                models_dir = os.path.join(repo_root, "models")
                vector_path = os.path.join(models_dir, "habe_vector.npy")  # numpy saves with .npy by default
                vocab_path = os.path.join(models_dir, "habe_vocab.json")
                
                if os.path.exists(vector_path) and os.path.exists(vocab_path):
                    engine = HolographicBackgroundEngine(dimension=2048)
                    if engine.load_vocab(vocab_path):
                        hav = np.load(vector_path)
                        
                        # Extract simple concepts from the query input (words >= 4 chars)
                        query_words = re.findall(r"\b\w{4,}\b", state_["input"].lower())
                        
                        # Find matching subjects in the VSA vocabulary
                        found_subjects = []
                        for word in query_words:
                            for vocab_key in engine.vocab.keys():
                                if vocab_key.startswith("subj:") and word in vocab_key.lower():
                                    found_subjects.append(vocab_key.split("subj:", 1)[1])
                        
                        # Unique subjects
                        found_subjects = list(set(found_subjects))
                        
                        # Query relations for these subjects
                        habe_facts = []
                        predicates = list({
                            k.split("pred:", 1)[1] for k in engine.vocab.keys() if k.startswith("pred:")
                        })
                        
                        for s in found_subjects:
                            for p in predicates:
                                matches = engine.query_vsa_relation(hav, s, p)
                                for obj, sim in matches:
                                    habe_facts.append(f"- (VSA Unconscious Background): {s} --[{p}]--> {obj} (similarity: {sim:.2f})")
                                    
                        # HABE 2.0 Hierarchy query
                        found_parents = []
                        for word in query_words:
                            for vocab_key in engine.vocab.keys():
                                if vocab_key.startswith("node:") and word in vocab_key.lower():
                                    found_parents.append(vocab_key.split("node:", 1)[1])
                        found_parents = list(set(found_parents))
                        
                        hierarchical_relations = ["has_child", "part_of", "contains", "member_of"]
                        for parent in found_parents:
                            for rel in hierarchical_relations:
                                h_matches = engine.query_vsa_hierarchy(hav, parent, rel)
                                for child, sim in h_matches:
                                    habe_facts.append(
                                        f"- (VSA Hierarchical Background): {parent} --[{rel}]--> {child} (similarity: {sim:.2f})"
                                    )
                                    
                        # Helper to compute VSA similarity for any text line
                        def compute_line_similarity(line_text: str) -> float:
                            l_words = re.findall(r"\b\w{4,}\b", line_text.lower())
                            vecs = []
                            for w in l_words:
                                for prefix in ["node:", "subj:", "obj:", "relation:", "pred:"]:
                                    vk = f"{prefix}{w}"
                                    if vk in engine.vocab:
                                        vecs.append(engine.vocab[vk])
                            if not vecs:
                                return 0.0
                            return float(engine.cosine_similarity(hav, engine.bundle(vecs)))
                        
                        # Pool all lines: GraphRAG + HABE facts
                        all_lines = []
                        if ctx:
                            all_lines.extend([line.strip() for line in ctx.split("\n") if line.strip()])
                        all_lines.extend(habe_facts)
                        
                        # Calculate scores and sort/filter
                        scored_lines = []
                        for line in all_lines:
                            if line.startswith("[Note:") or line.startswith("HOLOGRAPHIC") or "[Procedural" in line:
                                scored_lines.append((line, 1.0))
                            else:
                                score = compute_line_similarity(line)
                                scored_lines.append((line, score))
                        
                        # Sort by similarity descending
                        scored_lines.sort(key=lambda x: x[1], reverse=True)
                        
                        # Keep lines with similarity >= 0.12 or explicit overrides
                        filtered_lines = [line for line, sim in scored_lines if sim >= 0.12 or sim == 1.0]
                        
                        # Reconstruct the context string
                        if filtered_lines:
                            ctx = "\n".join(filtered_lines)
                            logger.info(f"🧠 HABE: Modulated context window. Retained {len(filtered_lines)}/{len(all_lines)} facts based on VSA similarity.")
                            await _report(f"🧠 HABE: Modulated context window (retained {len(filtered_lines)} facts)")
                        else:
                            ctx = ""
            except Exception as _habe_exc:
                logger.warning(f"HABE background retrieval and modulation failed: {_habe_exc}")

        return {
            "graph_context": ctx,
            "graphrag_entities": _entity_meta,
            "retrieved_graph_chunks": _retrieved_graph_chunks,
        }
    except RequestDeadlineExceeded:
        raise
    except Exception as e:
        logger.warning(f"GraphRAG query_context error: {e}")
        return {"graph_context": "", "retrieved_graph_chunks": []}


async def math_node_wrapper(state_: AgentState):
    if state_.get("cache_hit"):
        return {"math_result": ""}
    plan = state_.get("plan", [])
    math_tasks = [
        t
        for t in plan
        if isinstance(t, dict) and t.get("category") == "math"
    ]
    if not math_tasks:
        return {"math_result": ""}
    logger.debug("--- [NODE] MATH CALCULATION ---")
    await _report("🧮 Math module (SymPy)...")
    from math_node import math_node
    result = await math_node(state_)
    await _report("🧮 Math computation complete")
    math_result = result.get("math_result", "")
    return {
        "math_result": math_result,
        "task_events": [
            {
                "task_id": task.get("id", ""),
                "category": "math",
                "status": "completed" if math_result else "failed",
                "executor": "math",
                "iteration": int(state_.get("agentic_iteration") or 0),
                "reason": "" if math_result else "empty_math_result",
            }
            for task in math_tasks
        ],
    }
