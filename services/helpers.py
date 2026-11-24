"""services/helpers.py — Shared runtime helpers for prompts, progress reporting, rate limiting, and request observability."""

import asyncio
import contextvars
import hashlib
import json
import logging
import logging.handlers as _log_handlers
import os
import re
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

import state
import telemetry as _telemetry
from config import (
    HISTORY_MAX_TURNS, HISTORY_MAX_CHARS,
    NEO4J_URI, NEO4J_USER, NEO4J_PASS,
    BENCHMARK_SHADOW_TEMPLATE, BENCHMARK_SHADOW_RATE,
    MAX_GRAPH_CONTEXT_CHARS,
    EVAL_CACHE_FLAG_THRESHOLD,
    KAFKA_TOPIC_REQUESTS,
    _CUSTOM_EXPERT_PROMPTS,
)
from metrics import (
    PROM_HISTORY_COMPRESSED, PROM_HISTORY_UNLIMITED,
    PROM_SELF_EVAL,
    PROM_SEMANTIC_MEMORY_STORED, PROM_SEMANTIC_MEMORY_HITS,
)
from parsing import _truncate_history as _truncate_history_pure
from services.templates import _read_expert_templates
from services.kafka import _kafka_publish
from services.llm_instances import search
from services.inference import ainvoke_judge_llm
from web_search import _web_search_with_citations as _web_search_with_citations_impl

logger = logging.getLogger("MOE-SOVEREIGN")


# ─── TOOL-EVAL LOGGING ───────────────────────────────────────────────────────
# Rotating JSONL — one record per tool handler call.  The path is configurable
# so imports/tests outside the container do not require a writable /app mount.
_tool_eval_log_path = os.getenv("TOOL_EVAL_LOG_PATH", "/app/logs/tool_eval.jsonl")
os.makedirs(os.path.dirname(_tool_eval_log_path) or ".", exist_ok=True)
_tool_eval_logger = logging.getLogger("tool-eval")
_tool_eval_logger.setLevel(logging.INFO)
_tool_eval_logger.propagate = False
if not _tool_eval_logger.handlers:
    _teh = _log_handlers.RotatingFileHandler(
        _tool_eval_log_path, maxBytes=50 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    _teh.setFormatter(logging.Formatter("%(message)s"))
    _tool_eval_logger.addHandler(_teh)


def _log_tool_eval(record: dict) -> None:
    """Append a structured JSON record to tool_eval.jsonl. Never raises."""
    try:
        _tool_eval_logger.info(json.dumps(record, ensure_ascii=False, default=str))
    except Exception:
        pass


# ─── SHADOW REQUEST STATE ────────────────────────────────────────────────────
# asyncio does not share a thread with other coroutines while awaiting, but
# synchronous dict mutation is NOT atomic under concurrent asyncio tasks on
# CPython once the GIL is released between byte-code instructions. threading.Lock
# is the correct primitive here because all writers run in the same event-loop
# thread, so Lock.acquire() never blocks the event loop longer than the locked
# section itself.
_shadow_lock = threading.Lock()  # guards _shadow_request_counter increment
_shadow_request_counter: int = 0


# ─── RATE LIMIT TRACKING ─────────────────────────────────────────────────────

def _update_rate_limit_headers(endpoint: str, headers, status_code: int = 200) -> None:
    """Parse OpenAI-style x-ratelimit-* headers and cache rate limit state per endpoint."""
    import time as _time, re as _re
    now = _time.time()
    entry = state._provider_rate_limits.get(endpoint, {})
    remaining_raw = headers.get("x-ratelimit-remaining-tokens")
    limit_raw     = headers.get("x-ratelimit-limit-tokens")
    reset_raw     = headers.get("x-ratelimit-reset-tokens")
    if remaining_raw is not None:
        try: entry["remaining_tokens"] = int(float(remaining_raw))
        except (ValueError, TypeError): pass
    if limit_raw is not None:
        try: entry["limit_tokens"] = int(float(limit_raw))
        except (ValueError, TypeError): pass
    if reset_raw is not None:
        try:
            m = _re.match(r'P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?$', reset_raw)
            if m:
                d, h, mi, s = (float(x or 0) for x in m.groups())
                entry["reset_time"] = now + d*86400 + h*3600 + mi*60 + s
            else:
                ts = float(reset_raw)
                entry["reset_time"] = ts if ts > 1e9 else now + ts
        except (ValueError, TypeError): pass
    if status_code == 429:
        entry["exhausted"] = True
        if "reset_time" not in entry:
            entry["reset_time"] = now + 60  # default: retry after 60s
    else:
        if entry.get("remaining_tokens", 1) > 0:
            entry["exhausted"] = False
    entry["updated_at"] = now
    state._provider_rate_limits[endpoint] = entry


def _check_rate_limit_exhausted(endpoint: str) -> bool:
    """Return True if endpoint is known rate-limited and reset time has not yet passed."""
    import time as _time
    entry = state._provider_rate_limits.get(endpoint)
    if not entry or not entry.get("exhausted"):
        return False
    return _time.time() < entry.get("reset_time", 0)


def _entry_is_fresh(ts_iso: str, ttl_days: int) -> bool:
    """True if an ISO-8601 timestamp is within ttl_days of now. Missing/garbage ts → stale.

    Shared by the interactive knowledge-bypass gate (graph/router_nodes.py::
    cache_lookup_node) and the Augmented Tool Path agent-cache gate
    (services/agent_enrichment.py::agent_cache_lookup) — moved here so both
    can import it without graph/router_nodes.py <-> services/agent_enrichment.py
    creating a dependency in either direction.
    """
    if not ts_iso:
        return False
    try:
        ts = datetime.fromisoformat(ts_iso)
        if ts.tzinfo is None:  # agent_enrichment writes naive local time, response_commit timezone-aware UTC
            ts = ts.astimezone()
        return datetime.now(timezone.utc) - ts <= timedelta(days=ttl_days)
    except Exception:
        return False


# ─── EXPERT PROMPT RESOLUTION ────────────────────────────────────────────────

def _conf_format_for_mode(mode: str) -> str:
    """Return the CONFIDENCE-block instruction snippet for a given output mode."""
    from prompts import _CONF_FORMAT_DEFAULT, _CONF_FORMAT_CODE, _CONF_FORMAT_CONCISE
    if mode == "code":    return _CONF_FORMAT_CODE
    if mode == "concise": return _CONF_FORMAT_CONCISE
    if mode in ("agent", "agent_orchestrated"):  return ""   # No CONFIDENCE block — coding agents need clean output
    if mode == "research": return ""   # Research uses its own report structure
    return _CONF_FORMAT_DEFAULT


def _inject_skills_into_prompt(base: str, skill_names: list) -> str:
    """Append a compact skill-availability note to an expert system prompt.

    Reads each skill's description line from the frontmatter only (≤100 chars),
    so the injected block stays small. Skills whose files cannot be found are
    silently skipped — callers must not assume every name resolves.
    """
    import re as _re
    from pathlib import Path as _Path
    _DESC_RE = _re.compile(r"description:\s*(.+?)(?:\n|$)")
    _SKILLS_DIR = _Path("/app/skills")
    lines: list[str] = []
    for name in (skill_names or []):
        for d in (_SKILLS_DIR, _SKILLS_DIR / "community"):
            candidate = d / f"{name}.md"
            if candidate.exists():
                try:
                    header = candidate.read_text(encoding="utf-8")[:500]
                    m = _DESC_RE.search(header)
                    desc = m.group(1).strip()[:100] if m else ""
                    lines.append(f"• /{name}" + (f": {desc}" if desc else ""))
                except OSError:
                    pass
                break
    if not lines:
        return base
    block = (
        "\nAvailable Output Skills (add output_skill field to your response when applicable):\n"
        + "\n".join(lines)
    )
    if "Available Output Skills" in base:
        return base
    return base + block


def _get_expert_prompt(cat: str, user_experts: Optional[dict] = None) -> str:
    """Returns the system prompt for a category.

    Priority: Template system prompt > Custom (Admin UI) > Default > general fallback.
    Tool injection: explicit MCP tool list from template > category-based default.
    Skill injection: per-expert skill list from template (description-only, not full body).
    """
    import tool_injector as _tool_injector
    from prompts import DEFAULT_EXPERT_PROMPTS
    inject_tools = _tool_injector.inject_tools
    inject_tools_explicit = getattr(_tool_injector, "inject_tools_explicit", None)
    if user_experts:
        cat_models = user_experts.get(cat, [])
        if cat_models and cat_models[0].get("_system_prompt"):
            base = cat_models[0]["_system_prompt"]
            mcp_tools = cat_models[0].get("_mcp_tools") or []
            skills    = cat_models[0].get("_skills") or []
            # Explicit tool list from dynamic template overrides category heuristic
            if mcp_tools:
                if inject_tools_explicit is None:
                    # A coherent image always provides this symbol. Retain a
                    # fail-safe for rolling upgrades so source skew cannot
                    # crash a request after a costly planner run.
                    logger.error(
                        "tool_injector lacks inject_tools_explicit; "
                        "skipping explicit tool injection"
                    )
                else:
                    base = inject_tools_explicit(base, mcp_tools)
            else:
                base = inject_tools(base, cat)
            if skills:
                base = _inject_skills_into_prompt(base, skills)
            return base
    base = (_CUSTOM_EXPERT_PROMPTS.get(cat)
            or DEFAULT_EXPERT_PROMPTS.get(cat)
            or DEFAULT_EXPERT_PROMPTS["general"])
    return inject_tools(base, cat)


# ─── HISTORY TRUNCATION ──────────────────────────────────────────────────────

def _truncate_history(messages: List[Dict], max_turns: int = None, max_chars: int = None) -> List[Dict]:
    """Wrapper: forwards to parsing._truncate_history_pure with app-level defaults and Prometheus counters."""
    return _truncate_history_pure(
        messages, max_turns, max_chars,
        default_max_turns=HISTORY_MAX_TURNS,
        default_max_chars=HISTORY_MAX_CHARS,
        prom_unlimited=PROM_HISTORY_UNLIMITED,
        prom_compressed=PROM_HISTORY_COMPRESSED,
    )


# ─── SEMANTIC MEMORY (Tier-2) ────────────────────────────────────────────────

async def _apply_semantic_memory(
    raw_history: List[Dict],
    kept_history: List[Dict],
    user_input: str,
    session_id: Optional[str],
    enabled: bool,
    user_id: str = "",
    team_ids: Optional[List[str]] = None,
    cross_session_enabled: bool = False,
    cross_session_scopes: Optional[List[str]] = None,
    # Template-level memory tuning
    n_results: int = 0,
    ttl_hours: int = 0,
    # User preference overrides (loaded from DB)
    prefer_fresh: bool = False,
    share_with_team: bool = False,
) -> List[Dict]:
    """Store evicted turns (Tier-2 write) and inject relevant past turns as warm context.

    Privacy hierarchy:
      - session: only current session turns (default)
      - private cross-session: all sessions owned by user_id (scope=private)
      - team cross-session: sessions shared within user's teams (scope=team)
      - shared cross-session: tenant-visible knowledge (scope=shared)

    User flags:
      prefer_fresh     — disables cross-session; each session starts clean
      share_with_team  — stores turns as scope=team so team members can retrieve them
    """
    if not enabled or not session_id:
        return kept_history

    from memory_retrieval import (
        get_memory_store, compute_evicted_turns,
        SCOPE_PRIVATE, SCOPE_TEAM, _N_RESULTS,
    )
    store      = get_memory_store()
    _team_ids  = team_ids or []
    _scopes    = cross_session_scopes or [SCOPE_PRIVATE]
    _n         = n_results or _N_RESULTS
    store_scope = SCOPE_TEAM if (share_with_team and _team_ids) else SCOPE_PRIVATE

    # Write: embed evicted turns in background (non-blocking)
    evicted = compute_evicted_turns(raw_history, kept_history)
    if evicted:
        async def _store_bg() -> None:
            n = await store.store_turns(
                session_id, evicted,
                user_id=user_id,
                team_id=_team_ids[0] if _team_ids else "",
                scope=store_scope,
                ttl_hours=ttl_hours,
            )
            if n:
                PROM_SEMANTIC_MEMORY_STORED.inc(n)
        asyncio.create_task(_store_bg())

    # Read: current-session retrieval (always)
    current_turns = await store.retrieve_relevant(session_id, user_input, n_results=_n)

    # Read: cross-session retrieval (if enabled, user_id known, and user hasn't opted out)
    cross_turns: List[Dict] = []
    if cross_session_enabled and user_id and not prefer_fresh:
        cross_turns = await store.retrieve_cross_session(
            session_id=session_id,
            query=user_input,
            user_id=user_id,
            team_ids=_team_ids,
            allowed_scopes=_scopes,
        )

    # Merge
    retrieved = store.merge_session_results(current_turns, cross_turns)
    if not retrieved:
        return kept_history

    warm_block = store.build_warm_context_block(retrieved)
    if not warm_block:
        return kept_history

    PROM_SEMANTIC_MEMORY_HITS.inc()
    cross_count = sum(1 for t in retrieved if t.get("cross"))
    logger.info(
        f"💾 Semantic memory: {len(retrieved)} warm turns "
        f"({len(retrieved)-cross_count} session, {cross_count} cross-session) "
        f"session={session_id[:8]}…"
    )

    warm_messages: List[Dict] = [
        {"role": "user",      "content": warm_block},
        {"role": "assistant", "content": "[Acknowledged: I have access to these earlier conversation turns.]"},
    ]
    return warm_messages + kept_history


# ─── WEB SEARCH WRAPPER ──────────────────────────────────────────────────────

async def _web_search_with_citations(query: str, ddg_fallback: Optional[bool] = None) -> str:
    """Wrapper: forwards to web_search._web_search_with_citations_impl with app-level search singleton.

    ddg_fallback: None = use global WEB_SEARCH_FALLBACK_DDG env var;
                  True/False = explicit per-call override (from template state).
    """
    # Lazy import to read the latest runtime value (toggleable via admin UI).
    from config import _WEB_SEARCH_FALLBACK_DDG
    use_ddg = _WEB_SEARCH_FALLBACK_DDG if ddg_fallback is None else ddg_fallback
    return await _web_search_with_citations_impl(query, search, ddg_fallback=use_ddg)


# ─── RESPONSE METADATA & SELF-EVAL ───────────────────────────────────────────

async def _store_response_metadata(
    response_id: str,
    user_input: str,
    expert_models_used: List[str],
    chroma_doc_id: str,
    plan: Optional[List[Dict]] = None,
    cost_tier: str = "",
    template_id: str = "",
    causal_intervention: Optional[dict] = None,
) -> None:
    """Stores response metadata for later feedback in Valkey (TTL 7 days)."""
    if state.redis_client is None:
        return
    try:
        meta = {
            "input":               user_input[:300],
            "expert_models_used":  json.dumps(expert_models_used),
            "chroma_doc_id":       chroma_doc_id,
            "ts":                  datetime.now().isoformat(),
            "plan_cats":           json.dumps([t.get("category", "") for t in (plan or [])]),
            "cost_tier":           cost_tier,
            "template_id":         template_id,
        }
        if causal_intervention:
            meta["causal_intervention"] = json.dumps(causal_intervention)
        key = f"moe:response:{response_id}"
        await state.redis_client.hset(key, mapping=meta)
        await state.redis_client.expire(key, 60 * 60 * 24 * 7)  # 7 Tage
    except Exception as e:
        logger.warning(f"Failed to save response metadata: {e}")


async def _self_evaluate(
    response_id: str,
    question: str,
    answer: str,
    chroma_id: str,
    template_name: str = "",
    complexity: str = "",
) -> None:
    """Judge LLM evaluates its own response async — does not block the response to the user."""
    try:
        eval_prompt = (
            "Rate the following answer on a scale of 1–5.\n"
            "1=incomplete/wrong, 3=adequate, 5=complete/correct\n\n"
            f"QUESTION: {question[:200]}\n\n"
            f"ANSWER: {answer[:600]}\n\n"
            "Reply ONLY with: SELF_RATING: N"
        )
        eval_res = await ainvoke_judge_llm(eval_prompt)
        m = re.search(r'SELF_RATING:\s*([1-5])', eval_res.content)
        score = int(m.group(1)) if m else 3
        PROM_SELF_EVAL.observe(score)
        if state.redis_client:
            await state.redis_client.hset(f"moe:response:{response_id}", "self_score", score)
        asyncio.create_task(_telemetry.record_self_score(
            state._userdb_pool, response_id, score, template_name=template_name, complexity=complexity
        ))
        # Patch self_eval_score into the policy training record (arrives async).
        try:
            from services.policy_log import update_policy_event as _upd_policy
            asyncio.create_task(_upd_policy(response_id, score))
        except Exception:
            pass
        # Cost-tier learning loop: track low-quality responses routed as local_7b.
        # If the downgrade rate > 20% for a category, it signals the tier is too low.
        if state.redis_client and score <= 2:
            try:
                _meta = await state.redis_client.hgetall(f"moe:response:{response_id}")
                _tier = _meta.get("cost_tier", "") if isinstance(_meta, dict) else ""
                if _tier == "local_7b":
                    _plan_cats = json.loads(_meta.get("plan_cats", "[]") if isinstance(_meta, dict) else "[]")
                    for _cat in set(_plan_cats):
                        if _cat:
                            await state.redis_client.zincrby("moe:cost:downgrade", 1, f"local_7b:{_cat}")
                            await state.redis_client.expire("moe:cost:downgrade", 60 * 60 * 24 * 180)
                    logger.info(f"📉 Cost-tier downgrade recorded for local_7b (score={score})")
            except Exception:
                pass
        # Down-weight low-rated answers in the cache
        if score <= EVAL_CACHE_FLAG_THRESHOLD and chroma_id:
            await asyncio.to_thread(state.cache_collection.update, ids=[chroma_id], metadatas=[{"flagged": True, "self_score": score}])
            logger.info(f"⚠️ Self-rating {score}/5 — cache entry {chroma_id} flagged")
        else:
            logger.debug(f"🧐 Self-rating {score}/5 for response {response_id}")
    except Exception as e:
        logger.debug(f"Self-evaluation failed: {e}")


# ─── NEO4J TERM LOOKUP ───────────────────────────────────────────────────────

async def _neo4j_terms_exist(terms: List[str]) -> set:
    """Checks which terms are already present as entities in Neo4j (batch check)."""
    if state.graph_manager is None:
        return set()
    try:
        async with state.graph_manager.driver.session() as session:
            result = await session.run(
                "UNWIND $terms AS t "
                "MATCH (e:Entity) WHERE toLower(e.name) = toLower(t) OR toLower(e.aliases_str) CONTAINS toLower(t) "
                "RETURN DISTINCT toLower(t) AS found",
                {"terms": terms}
            )
            return {r["found"] async for r in result}
    except Exception as e:
        logger.debug(f"_neo4j_terms_exist failed: {e}")
        return set()


# ─── PROGRESS REPORTING ──────────────────────────────────────────────────────
# Any node can call _report() → message appears in the <think> block of the stream.

_progress_queue: contextvars.ContextVar[Optional[asyncio.Queue]] = \
    contextvars.ContextVar("_progress_queue", default=None)


async def _report(msg: str) -> None:
    """Forward a progress message to the current request's progress queue, if any."""
    q = _progress_queue.get()
    if q is not None:
        await q.put(msg)


# ─── PER-REQUEST LOG CORRELATION ─────────────────────────────────────────────
# Set once at each API entry point (main.py, services/pipeline/chat.py,
# services/pipeline/anthropic.py) to the request's chat_id. A logging.Filter
# installed on the root handler (see main.py) reads this on every log record,
# so every existing logger.info/warning/... call across the whole pipeline
# gets tagged with the originating request automatically — no need to thread
# chat_id through the ~270 individual log call sites. Powers the live
# pipeline diagram's per-request log view (admin_ui/app.py
# api_live_process_logs). asyncio.create_task() copies the current context,
# so fire-and-forget tasks spawned from within a request (write-back, judge
# scoring, etc.) still get the correct chat_id in their own log lines.
current_chat_id: contextvars.ContextVar[str] = \
    contextvars.ContextVar("current_chat_id", default="")


# ─── SHADOW REQUEST ──────────────────────────────────────────────────────────

async def _shadow_request(user_input: str, user_id: str, api_key: str) -> None:
    """Sends a fire-and-forget shadow request to the BENCHMARK_SHADOW_TEMPLATE.

    The response is discarded from the user perspective but stored in Kafka
    (moe.requests) with shadow=True for quality gate analysis.
    Called every BENCHMARK_SHADOW_RATE-th production request.
    """
    if not BENCHMARK_SHADOW_TEMPLATE or not api_key:
        return
    try:
        payload = {
            "model":    BENCHMARK_SHADOW_TEMPLATE,
            "messages": [{"role": "user", "content": user_input[:1000]}],
            "stream":   False,
        }
        timeout = httpx.Timeout(120.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                "http://localhost:8002/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            shadow_resp = ""
            if r.status_code == 200:
                data = r.json()
                shadow_resp = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        # Log shadow result to Kafka for comparator analysis
        asyncio.create_task(_kafka_publish(KAFKA_TOPIC_REQUESTS, {
            "shadow":          True,
            "shadow_template": BENCHMARK_SHADOW_TEMPLATE,
            "user_id":         user_id,
            "input":           user_input[:300],
            "answer":          shadow_resp[:500],
            "ts":              datetime.now().isoformat(),
        }))
        logger.debug(f"🔬 Shadow request completed ({len(shadow_resp)} chars)")
    except Exception as e:
        logger.debug(f"Shadow request failed (non-critical): {e}")
