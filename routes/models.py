"""routes/models.py — GET /v1/models model-listing endpoint."""

import hashlib
import json
import logging
import time
from typing import Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("MOE-SOVEREIGN")

# Per-node model-listing cache: {node: (monotonic_ts, [model_ids])}
# Shared across all /v1/models requests so wildcard node listings are not
# re-fetched on every call — particularly important for paid cloud endpoints.
_node_listing_cache: dict = {}
_NODE_LISTING_TTL = 60.0  # seconds

from config import (
    CLAUDE_CODE_MODELS, CLAUDE_CODE_TOOL_MODEL,
    INFERENCE_SERVERS_LIST, URL_MAP, TOKEN_MAP,
    MODES, _model_display_name,
)
from services.auth import _extract_api_key, _validate_api_key
from services.routing import _resolve_template_selection
from services.templates import _read_expert_templates


def _template_context_length(template: dict) -> Optional[int]:
    """Return the largest explicitly configured context window in a template."""
    windows: list[int] = []
    for field in ("planner_num_ctx", "judge_num_ctx", "tool_expert_num_ctx"):
        try:
            value = int(template.get(field) or 0)
            if value > 0:
                windows.append(value)
        except (TypeError, ValueError):
            pass
    for category in (template.get("experts") or {}).values():
        if not isinstance(category, dict):
            continue
        try:
            value = int(category.get("context_window") or 0)
            if value > 0:
                windows.append(value)
        except (TypeError, ValueError):
            pass
    return max(windows) if windows else None


def _template_model_entry(template_id: str, template: dict, created: int) -> dict:
    model_id = template.get("name") or template_id
    description = template.get("description") or model_id
    entry = {
        "id": model_id,
        "object": "model",
        "owned_by": "moe-sovereign",
        "created": created,
        "description": description,
        "display_name": _model_display_name(model_id, description),
    }
    context_length = _template_context_length(template)
    if context_length:
        # Different OpenAI-compatible clients use either spelling.
        entry["context_length"] = context_length
        entry["context_window"] = context_length
    return entry


async def _context_window_for_model(model_id: str) -> Optional[int]:
    """Look up context_window from model_metadata for a given model ID."""
    try:
        from admin_ui.database import _get_pool
        from psycopg.rows import dict_row as _dict_row
        async with _get_pool().connection() as conn:
            async with conn.cursor(row_factory=_dict_row) as cur:
                await cur.execute(
                    "SELECT context_window FROM model_metadata WHERE model_id = %s", (model_id,)
                )
                row = await cur.fetchone()
                if row and row["context_window"] and row["context_window"] > 4096:
                    return row["context_window"]
    except Exception:
        pass
    return None


async def _get_public_models(created: int) -> list:
    """Return all models from model_metadata for unauthenticated /v1/models calls."""
    try:
        from admin_ui.database import _get_pool
        from psycopg.rows import dict_row as _dict_row
        async with _get_pool().connection() as conn:
            async with conn.cursor(row_factory=_dict_row) as cur:
                await cur.execute(
                    "SELECT model_id, context_window FROM model_metadata WHERE context_window > 4096 ORDER BY model_id"
                )
                rows = await cur.fetchall()
        result = []
        for r in rows:
            entry: dict = {
                "id":             r["model_id"],
                "object":         "model",
                "owned_by":       "moe-sovereign",
                "created":        created,
                "context_length": r["context_window"],
            }
            result.append(entry)
        return result
    except Exception as _e:
        logger.warning("_get_public_models failed: %s", _e)
    return []

# Per-node loaded-context cache: {node: (monotonic_ts, {model_name: num_ctx})}
_node_loaded_ctx_cache: dict = {}
_NODE_LOADED_CTX_TTL = 30.0  # seconds — a reload may change the allocation


async def _node_model_ids(node: str) -> list:
    """List the model IDs a configured node serves (60s cached)."""
    now = time.monotonic()
    cached = _node_listing_cache.get(node)
    if cached and (now - cached[0]) < _NODE_LISTING_TTL:
        return cached[1]
    base = URL_MAP[node].rstrip("/").removesuffix("/v1")
    headers = {"Authorization": f"Bearer {TOKEN_MAP.get(node, 'ollama')}"}
    api_type = {s["name"]: s.get("api_type", "ollama") for s in INFERENCE_SERVERS_LIST}.get(node, "ollama")
    async with httpx.AsyncClient(timeout=5) as client:
        if api_type == "ollama":
            r = await client.get(f"{base}/api/tags", headers=headers)
            models = [m["name"] for m in (r.json().get("models") or [])] if r.status_code == 200 else []
        else:
            r = await client.get(f"{base}/v1/models", headers=headers)
            models = [m["id"] for m in (r.json().get("data") or [])] if r.status_code == 200 else []
    _node_listing_cache[node] = (now, models)
    return models


async def _node_loaded_ctx(node: str) -> dict:
    """Return {model_name: allocated num_ctx} for models currently loaded on an Ollama node.

    Uses /api/ps, i.e. the context the model was actually loaded with (not the
    GGUF training maximum). OpenAI-compatible clients such as Hermes read
    ``context_length`` from /v1/models; without it they fall back to a default
    window. Returns {} for unknown/non-Ollama nodes or on any error.
    """
    if node not in URL_MAP:
        return {}
    api_type = {s["name"]: s.get("api_type", "ollama") for s in INFERENCE_SERVERS_LIST}.get(node, "ollama")
    if api_type != "ollama":
        return {}
    now = time.monotonic()
    cached = _node_loaded_ctx_cache.get(node)
    if cached and (now - cached[0]) < _NODE_LOADED_CTX_TTL:
        return cached[1]
    loaded: dict = {}
    try:
        base = URL_MAP[node].rstrip("/").removesuffix("/v1")
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(
                f"{base}/api/ps",
                headers={"Authorization": f"Bearer {TOKEN_MAP.get(node, 'ollama')}"},
            )
        if r.status_code == 200:
            for m in r.json().get("models") or []:
                name = m.get("name") or m.get("model")
                ctx = m.get("context_length") or m.get("context_window")
                if name and isinstance(ctx, int) and ctx > 0:
                    loaded[name] = ctx
    except Exception as exc:
        logger.debug("loaded-context lookup failed for node %s: %s", node, exc)
    _node_loaded_ctx_cache[node] = (now, loaded)
    return loaded


def _endpoint_allowed(allowed_endpoints: list, model_n: str, node: str) -> bool:
    """True if the key's model_endpoint grants list contains model@node or *@node."""
    return any(e in (f"{model_n}@{node}", f"*@{node}") for e in allowed_endpoints or [])


async def _native_model_entry(model_n: str, node: str, created: int) -> Optional[dict]:
    """Build the /v1/models/{model@node} entry incl. the best-known context window."""
    ctx = (await _node_loaded_ctx(node)).get(model_n) or await _context_window_for_model(f"{model_n}@{node}")
    if not ctx:
        api_type = {s["name"]: s.get("api_type", "ollama") for s in INFERENCE_SERVERS_LIST}.get(node, "ollama")
        if api_type == "ollama":
            from context_budget import fetch_ollama_native_ctx_max
            ctx = await fetch_ollama_native_ctx_max(model_n, URL_MAP[node], TOKEN_MAP.get(node, "ollama")) or None
    entry: dict = {
        "id": f"{model_n}@{node}", "object": "model",
        "owned_by": "moe-sovereign", "created": created,
        "description": f"Direkt via {node}",
        "display_name": f"{model_n} ({node})",
    }
    if ctx:
        entry["context_length"] = ctx
        entry["context_window"] = ctx
    return entry


router = APIRouter()


@router.get("/v1/models/{model_id}")
async def get_model(model_id: str, raw_request: Request):
    """Return info for a single model by ID (OpenAI-compatible).

    Hermes and other OpenAI-compatible clients call this endpoint to probe
    model availability and capabilities before or during tool-call continuation.
    A 404 here causes Hermes to abort the in-flight API call with
    'Interrupted during API call', so we must return a valid model object
    for any model that appears in the /v1/models list.
    """
    raw_key = _extract_api_key(raw_request)
    user_ctx = await _validate_api_key(raw_key) if raw_key else {"error": "missing_key"}
    if "error" in user_ctx:
        return JSONResponse(status_code=401, content={"error": {
            "message": "Invalid or missing API key",
            "type":    "invalid_request_error",
            "code":    "invalid_api_key",
        }})

    _created = int(time.time())

    selection = _resolve_template_selection(
        user_ctx.get("permissions_json", "{}"),
        override_tmpl_id=model_id,
        user_templates_json=user_ctx.get("user_templates_json", "{}"),
    )
    if selection["template"] is not None and selection["authorized"]:
        return _template_model_entry(
            selection["id"], selection["template"], _created
        )
    if selection["template"] is not None:
        return JSONResponse(status_code=403, content={"error": {
            "message": f"Model '{model_id}' is not authorized for this API key",
            "type": "permission_denied",
            "code": "model_not_authorized",
        }})

    # Check MODES (built-in moe-* template IDs).
    for cfg in MODES.values():
        if cfg["model_id"] == model_id:
            return {
                "id": model_id, "object": "model", "owned_by": "moe-sovereign",
                "created": _created,
                "description": cfg["description"],
                "display_name": _model_display_name(model_id, cfg["description"]),
            }

    # Native model@node (Ollama/OpenAI-compatible node passthrough).
    model_n, _, node = model_id.rpartition("@")
    if model_n and node in URL_MAP:
        allowed = json.loads(user_ctx.get("permissions_json", "{}")).get("model_endpoint") or []
        if _endpoint_allowed(allowed, model_n, node):
            try:
                served = model_n in await _node_model_ids(node)
            except Exception:
                served = False
            # Explicit grants stay resolvable while the node is unreachable.
            if served or f"{model_n}@{node}" in allowed:
                return await _native_model_entry(model_n, node, _created)

    return JSONResponse(status_code=404, content={"error": {
        "message": f"Model '{model_id}' not found",
        "type":    "invalid_request_error",
        "code":    "model_not_found",
    }})


@router.get("/v1/models")
async def list_models(raw_request: Request):
    raw_key  = _extract_api_key(raw_request)
    # ── Diagnostic auth log ──────────────────────────────────────────────────
    # key_id is a truncated SHA-256 of the raw key — safe to log (see the
    # matching block in services/pipeline/chat.py for the full rationale).
    # This block previously logged a raw 10-char key prefix, which is partial
    # key material, not a safe identifier (GAP_REPORT_2026-09-11.md, GAP-11).
    _auth_hdr   = raw_request.headers.get("authorization", "")
    _xapi_hdr   = raw_request.headers.get("x-api-key", "")
    _hdr_source = (
        "authorization-bearer" if _auth_hdr.lower().startswith("bearer ") else
        "x-api-key"             if _xapi_hdr else
        "authorization-other"   if _auth_hdr else
        "none"
    )
    _key_id     = hashlib.sha256(raw_key.encode()).hexdigest()[:12] if raw_key else "none"
    _key_len    = len(raw_key or "")
    _is_moe_sk  = bool(raw_key and raw_key.startswith("moe-sk-"))
    _origin_ip  = raw_request.client.host if raw_request.client else "?"
    logger.warning(
        "🔍 models-auth-debug ip=%s hdr_source=%s key_id=%s key_len=%d is_moe_sk=%s",
        _origin_ip, _hdr_source, _key_id, _key_len, _is_moe_sk,
    )
    # ── End diagnostic block ───────────────────────────────────────────────
    user_ctx = await _validate_api_key(raw_key) if raw_key else {"error": "missing_key"}
    if "error" in user_ctx:
        # Unauthenticated: return public model list from model_metadata (for context-window discovery)
        _pub_created = int(time.time())
        _pub_models  = await _get_public_models(_pub_created)
        return JSONResponse(content={"object": "list", "data": _pub_models})

    user_perms        = json.loads(user_ctx.get("permissions_json", "{}"))
    allowed_modes     = user_perms.get("moe_mode")
    allowed_templates = user_perms.get("expert_template")
    _user_cc_json_m   = user_ctx.get("user_cc_profiles_json", "")
    has_cc            = (bool(user_perms.get("cc_profile"))
                         or bool(_user_cc_json_m and _user_cc_json_m not in ("{}", "")))

    _model_created = int(time.time())

    main_models = []
    if allowed_templates:
        all_templates = _read_expert_templates()
        main_models.extend(
            _template_model_entry(t["id"], t, _model_created)
            for t in all_templates if t.get("id") in allowed_templates
        )

    _has_other_perms = bool(
        user_perms.get("model_endpoint") or user_perms.get("cc_profile")
    )
    if allowed_modes is not None or (not allowed_templates and not _has_other_perms):
        main_models.extend([
            {
                "id":           cfg["model_id"],
                "object":       "model",
                "owned_by":     "moe-sovereign",
                "created":      _model_created,
                "description":  cfg["description"],
                "display_name": _model_display_name(cfg["model_id"], cfg["description"]),
            }
            for cfg in MODES.values()
            if allowed_modes is None or cfg["model_id"] in allowed_modes or (cfg["model_id"] == "moe-auto" and any(m.startswith("moe-auto") for m in allowed_modes))
        ])

    existing_ids = {m["id"] for m in main_models}

    _user_tmpls_m: dict = {}
    try:
        _user_tmpls_m = json.loads(user_ctx.get("user_templates_json", "{}") or "{}")
    except Exception:
        pass
    user_tmpl_models = [
        _template_model_entry(uid, cfg, _model_created)
        for uid, cfg in _user_tmpls_m.items()
        if cfg.get("name", uid) not in existing_ids
    ]
    existing_ids |= {m["id"] for m in user_tmpl_models}

    claude_models = [
        {
            "id":           mid,
            "object":       "model",
            "owned_by":     "moe-sovereign",
            "created":      _model_created,
            "description":  f"Claude Code compatible → MoE ({CLAUDE_CODE_TOOL_MODEL} for tools)",
            "display_name": _model_display_name(mid),
        }
        for mid in sorted(CLAUDE_CODE_MODELS) if mid not in existing_ids
    ] if has_cc else []

    native_models: list = []
    _api_type_map = {s["name"]: s.get("api_type", "ollama") for s in INFERENCE_SERVERS_LIST}
    allowed_endpoints = user_perms.get("model_endpoint")
    if allowed_endpoints:
        seen: set = set()
        for entry in allowed_endpoints:
            model_n, _, node = entry.partition("@")
            if not model_n:
                continue
            if model_n == "*":
                if node not in URL_MAP:
                    continue
                try:
                    _wc_models = await _node_model_ids(node)
                    _wc_loaded = await _node_loaded_ctx(node)
                    for _wc_m in _wc_models:
                        mid = f"{_wc_m}@{node}"
                        if mid not in seen and mid not in existing_ids:
                            seen.add(mid)
                            _wc_entry: dict = {
                                "id": mid, "object": "model",
                                "owned_by": "moe-sovereign", "created": _model_created,
                                "description": f"Direkt via {node}",
                                "display_name": f"{_wc_m} ({node})",
                            }
                            _wc_ctx = _wc_loaded.get(_wc_m)
                            if _wc_ctx:
                                _wc_entry["context_length"] = _wc_ctx
                                _wc_entry["context_window"] = _wc_ctx
                            native_models.append(_wc_entry)
                except Exception:
                    pass
                continue
            model_id = f"{model_n}@{node}" if node else model_n
            if model_id not in seen and model_id not in existing_ids:
                seen.add(model_id)
                _ctx_len = (
                    (await _node_loaded_ctx(node)).get(model_n) if node else None
                ) or await _context_window_for_model(model_id)
                _native_entry: dict = {
                    "id":           model_id,
                    "object":       "model",
                    "owned_by":     "moe-sovereign",
                    "created":      _model_created,
                    "description":  f"Direkt via {node}" if node else "Direktzugriff",
                    "display_name": f"{model_n} ({node})" if node else model_n,
                }
                if _ctx_len:
                    _native_entry["context_length"] = _ctx_len
                    _native_entry["context_window"] = _ctx_len
                native_models.append(_native_entry)

    _user_conns_m: dict = {}
    try:
        _user_conns_m = json.loads(user_ctx.get("user_connections_json", "{}") or "{}")
    except Exception:
        pass
    conn_models: list = []
    _seen_conn: set = existing_ids | {m["id"] for m in native_models}
    for _conn_name, _conn_cfg in _user_conns_m.items():
        for _cm in _conn_cfg.get("models_cache", []):
            # models_cache may contain rich dicts {id, ...} or legacy plain strings.
            # Strip any existing @node suffix so we don't produce model@node@conn double-suffixes.
            if isinstance(_cm, dict):
                _raw  = _cm.get("id") or ""
                _tags = _cm.get("tags") or []
            else:
                _raw  = str(_cm)
                _tags = []
            _base = _raw.rsplit("@", 1)[0] if "@" in _raw else _raw
            if not _base:
                continue
            _mid = f"{_base}@{_conn_name}"
            if _mid not in _seen_conn:
                _seen_conn.add(_mid)
                # 'name' is the field Open-WebUI displays in the model selector.
                # Tags are appended to 'name' so users can filter by capability.
                # 'id' stays clean (model@conn) so API calls remain valid.
                _disp = f"{_base} [{', '.join(_tags)}]" if _tags else _base
                conn_models.append({
                    "id":           _mid,
                    "object":       "model",
                    "owned_by":     "moe-sovereign",
                    "created":      _model_created,
                    "name":         _disp,          # Open-WebUI uses 'name' for display
                    "display_name": _disp,          # MoE-internal compat
                    "description":  f"Via {_conn_name}" + (f" [{', '.join(_tags)}]" if _tags else ""),
                })

    return {
        "object": "list",
        "data": main_models + user_tmpl_models + claude_models + native_models + conn_models,
    }
