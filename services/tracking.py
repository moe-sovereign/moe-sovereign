"""
services/tracking.py — Request lifecycle tracking and user budget management.

All functions are fire-and-forget async helpers:
  - Usage logging to Postgres (usage_log table)
  - Active request registration in Redis (live monitoring)
  - User token budget increments in Redis
"""

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import Request

import state
from config import HISTORY_MAX_ENTRIES, MAX_REQUESTS_PER_MINUTE

logger = logging.getLogger("MOE-SOVEREIGN")


async def _log_usage_to_db(
    user_id: str, api_key_id: str, request_id: str,
    model: str, moe_mode: str,
    prompt_tokens: int, completion_tokens: int,
    status: str = "ok", session_id: str = None,
    latency_ms: Optional[int] = None,
    complexity_level: str = "",
    expert_domains: str = "",
    cache_hit: bool = False,
    agentic_rounds: int = 0,
    dynamic_tmpl_id: str = "",
    trust_score: Optional[float] = None,
    trust_verdict: Optional[str] = None,
    cynefin_domain: Optional[str] = None,
    self_critique_round: int = 0,
    cascade_type: Optional[str] = None,
    structured_failure_round: int = 0,
) -> None:
    """Fire-and-forget Postgres usage log. Never raises exceptions."""
    try:
        if state._userdb_pool is None:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        async with state._userdb_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO usage_log "
                    "(id,user_id,api_key_id,request_id,session_id,model,moe_mode,prompt_tokens,"
                    "completion_tokens,total_tokens,status,requested_at,"
                    "latency_ms,complexity_level,expert_domains,cache_hit,agentic_rounds,dynamic_tmpl_id,"
                    "trust_score,trust_verdict,cynefin_domain,self_critique_round,cascade_type,"
                    "structured_failure_round) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                    (uuid.uuid4().hex, user_id, api_key_id or None, request_id,
                     session_id or None, model, moe_mode, prompt_tokens, completion_tokens,
                     prompt_tokens + completion_tokens, status, now_iso,
                     latency_ms, complexity_level or None, expert_domains or None,
                     cache_hit, agentic_rounds, dynamic_tmpl_id or None,
                     trust_score, trust_verdict or None, cynefin_domain or None,
                     self_critique_round, cascade_type or None,
                     structured_failure_round),
                )
                await cur.execute(
                    "UPDATE api_keys SET last_used_at=%s WHERE user_id=%s AND is_active=TRUE",
                    (now_iso, user_id),
                )
    except Exception as e:
        logger.warning("Usage log failed: %s", e)


async def _register_active_request(
    chat_id: str, user_id: str, model: str,
    moe_mode: str, req_type: str,
    template_name: str = "", client_ip: str = "",
    backend_model: str = "", backend_host: str = "",
    api_key_id: str = "",
    resolved_tmpl_name: str = "", resolved_tmpl_id: str = "",
) -> None:
    """Register a running request in Redis for live monitoring."""
    if state.redis_client is None:
        return
    try:
        _key_label = ""
        _key_prefix = ""
        if api_key_id and state._userdb_pool is not None:
            try:
                from psycopg.rows import dict_row
                async with state._userdb_pool.connection() as _conn:
                    async with _conn.cursor(row_factory=dict_row) as _cur:
                        await _cur.execute(
                            "SELECT label, key_prefix FROM api_keys WHERE id=%s",
                            (api_key_id,),
                        )
                        _row = await _cur.fetchone()
                        if _row:
                            _key_label  = _row["label"] or ""
                            _key_prefix = _row["key_prefix"] or ""
            except Exception:
                pass
        meta = {
            "chat_id":            chat_id,
            "user_id":            user_id,
            "model":              model,
            "moe_mode":           moe_mode,
            "type":               req_type,
            "template_name":      template_name,
            "resolved_tmpl_name": resolved_tmpl_name,
            "resolved_tmpl_id":   resolved_tmpl_id,
            "client_ip":          client_ip,
            "backend_model":      backend_model,
            "backend_host":       backend_host,
            "api_key_id":         api_key_id,
            "key_label":          _key_label,
            "key_prefix":         _key_prefix,
            "started_at":         datetime.utcnow().isoformat() + "Z",
        }
        await state.redis_client.set(f"moe:active:{chat_id}", json.dumps(meta), ex=7200)
    except Exception as e:
        logger.debug("Active request registration failed: %s", e)


async def _record_stage(chat_id: str, stage: str, status: str = "started", detail: str = "") -> None:
    """Append a pipeline-stage trace entry for live visualization. Fire-and-forget, never raises.

    Stored separately from moe:active:{chat_id} (as a bounded Redis list) so the
    unfiltered live-monitoring table poll stays untouched; only read on-demand
    when an admin/user opens the per-request diagram panel. Also used by the
    Augmented Tool Path (services/pipeline/anthropic.py, chat.py,
    services/agent_enrichment.py) to trace tool_entry/agent_cache/
    agent_graphrag/tool_model_call/agent_writeback stages.
    """
    if not chat_id or state.redis_client is None:
        return
    try:
        key = f"moe:active:{chat_id}:trace"
        entry = json.dumps({
            "stage":  stage,
            "status": status,
            "ts":     datetime.now(timezone.utc).timestamp(),
            "detail": detail,
        })
        pipe = state.redis_client.pipeline()
        pipe.rpush(key, entry)
        pipe.ltrim(key, -30, -1)
        pipe.expire(key, 7200)
        await pipe.execute()
    except Exception as e:
        # WARNING (not debug): a user report of "interactive-pipeline replay
        # always shows 0/0" traced back to zero trace entries ever reaching
        # Redis for a confirmed-real, confirmed-completed interactive-pipeline
        # request, despite the write path, Redis connectivity, and read path
        # all checking out individually in isolation — this call itself must
        # be silently failing, and debug-level logging was hiding exactly
        # why. Still fire-and-forget: never raises, caller is unaffected.
        logger.warning("Stage trace record failed for chat_id=%s stage=%s: %s", chat_id, stage, e)


async def _record_file_touch(chat_id: str, path: str, action: str, tool: str) -> None:
    """Append a file-touch entry for the live-pipeline-visualization's
    "which files did this Agent Tool Path session touch" view. Same pattern
    as _record_stage but a separate Redis key/cap: a long OpenCode/Claude
    Code coding session easily touches more files than pipeline stages, and
    keeping the two lists separate means the diagram's stage-trace fetch
    (polled while the panel is open) never has to filter file-touch noise
    out of it. Fire-and-forget, never raises.
    """
    if not chat_id or not path or state.redis_client is None:
        return
    try:
        key = f"moe:active:{chat_id}:files"
        entry = json.dumps({
            "path":   path,
            "action": action,
            "tool":   tool,
            "ts":     datetime.now(timezone.utc).timestamp(),
        })
        pipe = state.redis_client.pipeline()
        pipe.rpush(key, entry)
        pipe.ltrim(key, -200, -1)
        pipe.expire(key, 7200)
        await pipe.execute()
    except Exception as e:
        logger.debug("File-touch record failed: %s", e)


def _norm_base_url(url: str) -> str:
    """Same normalization used at every Ollama call site (graph/planner.py,
    graph/expert.py, services/inference.py) — strip trailing slash and an
    optional /v1 suffix — so backend_host values are comparable regardless
    of which form the caller happened to have on hand."""
    u = (url or "").rstrip("/")
    return u[:-3] if u.endswith("/v1") else u


async def _patch_active_request_backend(chat_id: str, backend_model: str, backend_host: str) -> None:
    """Fill in backend_model/backend_host on an already-registered active
    request, once the tool-calling passthrough resolves which model/node it
    actually dispatched to (unlike the native-model path, this isn't known
    yet at initial _register_active_request() time — see chat_completions()).

    The fields make the actual model/node visible in live monitoring and
    request history. Fire-and-forget, never raises.
    """
    if not chat_id or state.redis_client is None:
        return
    try:
        key = f"moe:active:{chat_id}"
        raw = await state.redis_client.get(key)
        if not raw:
            return
        meta = json.loads(raw)
        meta["backend_model"] = backend_model
        meta["backend_host"]  = _norm_base_url(backend_host)
        await state.redis_client.set(key, json.dumps(meta), ex=7200)
    except Exception as e:
        logger.debug("Active request backend patch failed: %s", e)


async def _deregister_active_request(chat_id: str, extra_meta: dict | None = None) -> None:
    """Remove a completed request from Redis live monitoring and write to history.

    Args:
        extra_meta: Optional fields merged into the history entry (e.g. trust signals).
    """
    if state.redis_client is None:
        return
    try:
        key = f"moe:active:{chat_id}"
        raw = await state.redis_client.get(key)
        if raw:
            try:
                meta = json.loads(raw)
                meta["status"]   = "completed"
                meta["ended_at"] = datetime.now(timezone.utc).isoformat()
                if extra_meta:
                    meta.update(extra_meta)
                score = datetime.now(timezone.utc).timestamp()
                await state.redis_client.zadd(
                    "moe:admin:completed", {json.dumps(meta, default=str): score}
                )
                await state.redis_client.zremrangebyrank(
                    "moe:admin:completed", 0, -(HISTORY_MAX_ENTRIES + 1)
                )
            except Exception as _he:
                logger.warning("History entry failed: %s", _he)
        await state.redis_client.delete(key)
    except Exception as e:
        logger.debug("Active request deregister failed: %s", e)


async def _increment_user_budget(
    user_id: str, tokens: int,
    prompt_tokens: int = 0, completion_tokens: int = 0,
) -> None:
    """Increment Redis budget counters for a user. Fire-and-forget."""
    if not user_id or user_id == "anon" or state.redis_client is None:
        return
    from datetime import date
    today = date.today().strftime("%Y-%m-%d")
    month = date.today().strftime("%Y-%m")
    try:
        cost_factor = 1.0
        cf_raw = await state.redis_client.get(f"user:{user_id}:cost_factor")
        if cf_raw:
            cost_factor = float(cf_raw)
        effective   = max(1, round(tokens * cost_factor))
        eff_prompt  = round(prompt_tokens * cost_factor)
        eff_out     = round(completion_tokens * cost_factor)
        pipe = state.redis_client.pipeline()
        pipe.incrby(f"user:{user_id}:tokens:daily:{today}", effective)
        pipe.expire(f"user:{user_id}:tokens:daily:{today}", 48 * 3600)
        pipe.incrby(f"user:{user_id}:tokens:monthly:{month}", effective)
        pipe.expire(f"user:{user_id}:tokens:monthly:{month}", 35 * 86400)
        pipe.incrby(f"user:{user_id}:tokens:total", effective)
        if eff_prompt > 0:
            pipe.incrby(f"user:{user_id}:tokens:daily:{today}:input", eff_prompt)
            pipe.expire(f"user:{user_id}:tokens:daily:{today}:input", 48 * 3600)
            pipe.incrby(f"user:{user_id}:tokens:monthly:{month}:input", eff_prompt)
            pipe.expire(f"user:{user_id}:tokens:monthly:{month}:input", 35 * 86400)
            pipe.incrby(f"user:{user_id}:tokens:total:input", eff_prompt)
        if eff_out > 0:
            pipe.incrby(f"user:{user_id}:tokens:daily:{today}:output", eff_out)
            pipe.expire(f"user:{user_id}:tokens:daily:{today}:output", 48 * 3600)
            pipe.incrby(f"user:{user_id}:tokens:monthly:{month}:output", eff_out)
            pipe.expire(f"user:{user_id}:tokens:monthly:{month}:output", 35 * 86400)
            pipe.incrby(f"user:{user_id}:tokens:total:output", eff_out)
        await pipe.execute()
    except Exception as e:
        logger.warning("Budget counter failed: %s", e)
    if state._userdb_pool is not None:
        try:
            from admin_ui.database import get_user_teams as _get_user_teams
            from admin_ui.database import deduct_team_budget as _deduct_team_budget
            for _team_id in await _get_user_teams(user_id):
                await _deduct_team_budget(_team_id, effective)
        except Exception as _tbe:
            logger.debug("Team budget deduct failed: %s", _tbe)


_IP_RATE_WINDOW_S = 60


async def _check_ip_rate_limit(request: Request) -> bool:
    """Fixed-window rate limit per client IP using Redis. Fails open on error."""
    if state.redis_client is None:
        return True
    try:
        _ip = (
            request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or request.headers.get("x-real-ip", "")
            or (request.client.host if request.client else "unknown")
        )
        _key = f"moe:ratelimit:ip:{_ip}"
        # SET NX EX, INCR and TTL run in one MULTI/EXEC transaction, so a
        # cancelled request or a dropped connection can no longer leave a
        # counter behind that has no expiry (a separate INCR + EXPIRE did, and
        # that locked a client IP out permanently).
        _pipe = state.redis_client.pipeline(transaction=True)
        _pipe.set(_key, 0, ex=_IP_RATE_WINDOW_S, nx=True)
        _pipe.incr(_key)
        _pipe.ttl(_key)
        _, _count, _ttl = await _pipe.execute()
        if _ttl == -1:
            # Self-heal a counter written by older code without an expiry.
            await state.redis_client.expire(_key, _IP_RATE_WINDOW_S)
        return _count <= MAX_REQUESTS_PER_MINUTE
    except Exception:
        return True


# ── Per-node latency trend ──────────────────────────────────────────────────
# A genuine "GPU queue depth" indicator turned out not to be honestly
# buildable: _endpoint_semaphores (services/inference.py) is only acquired by
# the interactive-pipeline's expert calls (graph/expert.py) — the dominant
# real traffic path, the Agent Tool Path passthrough (OpenCode/Claude Code),
# calls Ollama directly with no concurrency gate at all, and Ollama itself
# exposes no queue-depth API. A rolling recent-latency-per-node view is the
# honest alternative: not a queue signal, but "this node has been slow for
# the last N requests" is real, observable data already being measured at
# each call site (just never persisted anywhere before now).

_NODE_LATENCY_SAMPLES = 20
_NODE_LATENCY_TTL_S = 3600


def _latency_key(node: str) -> str:
    # Callers pass either a base_url or an endpoint name — normalize both to
    # the same comparable form (same helper admin_ui uses when matching
    # against each configured server's own url) and sanitize into a safe key.
    safe = re.sub(r"[^a-zA-Z0-9_\-.:]", "_", _norm_base_url(node) or node)
    return f"moe:latency:{safe}"


async def _record_node_latency(node: str, model: str, latency_ms: float) -> None:
    """Append one (model, latency_ms) sample for `node` (a base_url or
    endpoint name) — bounded rolling window, read by admin_ui's
    /api/live/llm-instances to show a recent average per inference server.
    Fire-and-forget, never raises."""
    if not node or state.redis_client is None:
        return
    try:
        key = _latency_key(node)
        entry = json.dumps({
            "model": model,
            "ms":    round(latency_ms),
            "ts":    datetime.now(timezone.utc).timestamp(),
        })
        pipe = state.redis_client.pipeline()
        pipe.rpush(key, entry)
        pipe.ltrim(key, -_NODE_LATENCY_SAMPLES, -1)
        pipe.expire(key, _NODE_LATENCY_TTL_S)
        await pipe.execute()
    except Exception as e:
        logger.debug("Node latency record failed: %s", e)


async def _get_node_latency_stats(node: str) -> dict:
    """Read back the rolling latency window written by _record_node_latency.
    Returns {"count": 0, "avg_ms": None} when there's no data yet — never
    raises, so a Redis hiccup just makes the admin UI show "no data" instead
    of breaking the whole llm-instances view."""
    if not node or state.redis_client is None:
        return {"count": 0, "avg_ms": None}
    try:
        raw_entries = await state.redis_client.lrange(_latency_key(node), 0, -1)
        samples = []
        for raw in raw_entries:
            try:
                samples.append(json.loads(raw)["ms"])
            except Exception:
                continue
        if not samples:
            return {"count": 0, "avg_ms": None}
        return {"count": len(samples), "avg_ms": round(sum(samples) / len(samples))}
    except Exception as e:
        logger.debug("Node latency read failed: %s", e)
        return {"count": 0, "avg_ms": None}


# Premature-stop rate per (model, node) — same Redis-hash counter shape as
# services/inference.py's moe:perf:{model}:{category} Thompson-sampling
# counters, but keyed on the (model, node) pair instead of (model, category):
# looks_like_premature_stop (services/agent_enrichment.py) is a per-request
# signal about how a specific model behaved on a specific node right now,
# and today that result is only used for the in-request retry decision, then
# discarded — this persists it so _select_node (services/inference.py) can
# eventually weigh a node/model combo that keeps stalling, the same way node
# load already does. Fed by services/pipeline/chat.py's two premature-stop
# detection sites.
_PSTOP_TTL_S = 7 * 86400            # 7-day rolling window, not a hard cap
_PSTOP_MIN_SAMPLES = 3              # optimistic below this — flood-fill-style:
                                     # don't penalize on the first observation


def _pstop_key(model: str, node: str) -> str:
    safe_model = re.sub(r"[^a-zA-Z0-9_\-]", "_", model)
    safe_node  = re.sub(r"[^a-zA-Z0-9_\-.:]", "_", _norm_base_url(node) or node)
    return f"moe:pstop:{safe_model}:{safe_node}"


async def _record_premature_stop_outcome(model: str, node: str, was_premature: bool) -> None:
    """Increments total and, if this turn was a premature stop, the
    premature counter for a model/node pair. Fire-and-forget, never raises —
    exact mirror of services/inference.py's _record_expert_outcome."""
    if not model or not node or state.redis_client is None:
        return
    try:
        key = _pstop_key(model, node)
        pipe = state.redis_client.pipeline()
        pipe.hincrby(key, "total", 1)
        if was_premature:
            pipe.hincrby(key, "premature", 1)
        pipe.expire(key, _PSTOP_TTL_S)
        await pipe.execute()
    except Exception as e:
        logger.debug("Premature-stop outcome record failed: %s", e)


async def _get_premature_stop_rate(model: str, node: str) -> float:
    """Fraction of recent turns on this model/node pair that were flagged as
    a premature stop. Returns 0.0 (optimistic — flood-fill style: assume no
    wall until enough evidence says otherwise) when there's no data yet or
    fewer than _PSTOP_MIN_SAMPLES observations. Never raises."""
    if not model or not node or state.redis_client is None:
        return 0.0
    try:
        data = await state.redis_client.hgetall(_pstop_key(model, node))
        total = int(data.get("total", 0))
        if total < _PSTOP_MIN_SAMPLES:
            return 0.0
        return int(data.get("premature", 0)) / total
    except Exception as e:
        logger.debug("Premature-stop rate read failed: %s", e)
        return 0.0
