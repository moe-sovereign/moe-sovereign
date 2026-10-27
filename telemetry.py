"""Routing telemetry — async fire-and-forget recording of routing decisions.

Writes to ``routing_telemetry`` in the moe_userdb PostgreSQL database.
All functions are safe to call in the hot path: errors are logged and
swallowed, never propagated to the caller.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger("moe.telemetry")

_CODE_PATTERN = re.compile(
    r"```|def |class |import |function |SELECT |CREATE |const |var |let ",
    re.IGNORECASE,
)

_INSERT_SQL = """
INSERT INTO routing_telemetry (
    response_id, user_id, template_name,
    prompt_length, prompt_lang, complexity,
    has_images, has_code,
    planner_plan, experts_used, mcp_tools_used,
    cache_hit, fast_path,
    self_score, judge_refined, correction_applied,
    total_tokens, wall_clock_ms, expert_scores,
    causal_path, graphrag_entities, judge_reason,
    judge_before_after, expert_inputs,
    tool_calls_log, working_memory
) VALUES (
    %s, %s, %s,
    %s, %s, %s,
    %s, %s,
    %s, %s, %s,
    %s, %s,
    %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s,
    %s, %s,
    %s, %s
)
ON CONFLICT (response_id) DO NOTHING
"""

_ENSURE_CAUSAL_COLUMNS_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='routing_telemetry' AND column_name='causal_path'
    ) THEN
        ALTER TABLE routing_telemetry
            ADD COLUMN causal_path       JSONB,
            ADD COLUMN graphrag_entities JSONB,
            ADD COLUMN judge_reason      TEXT,
            ADD COLUMN judge_before_after JSONB,
            ADD COLUMN expert_inputs     JSONB;
    END IF;
END$$;
"""

_ENSURE_TOOL_CALLS_COLUMN_SQL = """
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_name='routing_telemetry' AND column_name='tool_calls_log')
    THEN
        ALTER TABLE routing_telemetry
            ADD COLUMN tool_calls_log JSONB,
            ADD COLUMN working_memory JSONB;
    END IF;
END$$;
"""

_ENSURE_QUALITY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS routing_quality_stats (
    template_name  TEXT    NOT NULL,
    complexity     TEXT    NOT NULL,
    avg_score      FLOAT   NOT NULL DEFAULT 0,
    sample_count   INT     NOT NULL DEFAULT 0,
    updated_at     TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (template_name, complexity)
);
"""

_UPDATE_QUALITY_STATS_SQL = """
INSERT INTO routing_quality_stats (template_name, complexity, avg_score, sample_count, updated_at)
    VALUES (%s, %s, %s, 1, now())
ON CONFLICT (template_name, complexity) DO UPDATE SET
    avg_score    = (routing_quality_stats.avg_score * routing_quality_stats.sample_count
                    + EXCLUDED.avg_score)
                   / (routing_quality_stats.sample_count + 1),
    sample_count = routing_quality_stats.sample_count + 1,
    updated_at   = now();
"""

_GET_QUALITY_HINT_SQL = """
SELECT avg_score, sample_count
FROM routing_quality_stats
WHERE template_name = %s AND complexity = %s
"""

_ENSURE_KPI_SNAPSHOT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS moe_kpi_snapshots (
    id           SERIAL       PRIMARY KEY,
    time_window  TEXT         NOT NULL,
    snapshot     JSONB        NOT NULL,
    computed_at  TIMESTAMPTZ  DEFAULT now()
);
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE tablename = 'moe_kpi_snapshots'
          AND indexname = 'moe_kpi_snapshots_window_time'
    ) THEN
        CREATE INDEX moe_kpi_snapshots_window_time
            ON moe_kpi_snapshots (time_window, computed_at DESC);
    END IF;
END$$;
"""

_INSERT_KPI_SNAPSHOT_SQL = """
INSERT INTO moe_kpi_snapshots (time_window, snapshot, computed_at)
VALUES (%s, %s, now())
"""

_GET_KPI_SNAPSHOT_SQL = """
SELECT snapshot, computed_at
FROM moe_kpi_snapshots
WHERE time_window = %s
ORDER BY computed_at DESC
LIMIT 1
"""

_UPDATE_RATING_SQL = """
UPDATE routing_telemetry SET user_rating = %s WHERE response_id = %s
"""

_UPDATE_SELF_SCORE_SQL = """
UPDATE routing_telemetry SET self_score = %s WHERE response_id = %s
"""


async def ensure_causal_columns(pool) -> None:
    """Adds causal-path, working-memory columns and quality stats table if missing.
    Idempotent — safe to call on every startup.
    """
    if pool is None:
        return
    try:
        async with pool.connection() as conn:
            await conn.execute(_ENSURE_CAUSAL_COLUMNS_SQL)
            await conn.execute(_ENSURE_TOOL_CALLS_COLUMN_SQL)
            await conn.execute(_ENSURE_QUALITY_TABLE_SQL)
            await conn.execute(_ENSURE_KPI_SNAPSHOT_TABLE_SQL)
        logger.info("✅ Causal-path, tool-calls columns, quality stats and KPI snapshot table ensured")
    except Exception as exc:
        logger.warning("Causal-path column migration failed: %s", exc)


def _detect_language(text: str) -> str:
    german_markers = ("der ", "die ", "das ", "und ", "ist ", "für ", "ein ",
                      "nicht ", "auf ", "mit ", "von ", "den ", "dem ")
    sample = text[:500].lower()
    hits = sum(1 for m in german_markers if m in sample)
    return "de" if hits >= 3 else "en"


def _has_code(text: str) -> bool:
    return bool(_CODE_PATTERN.search(text[:2000]))


async def record_routing_decision(
    pool,
    response_id: str,
    state: dict[str, Any],
    expert_scores: Optional[dict] = None,
    wall_clock_ms: int = 0,
) -> None:
    """Record a completed routing decision. Call after merger/response.

    Captures the full causal path: routing decision, per-expert stats,
    GraphRAG entity usage, and judge intervention details.
    """
    if pool is None:
        return
    try:
        user_input = state.get("input", "")
        experts_used = state.get("expert_models_used", [])
        # response_commit's payload carries the executed plan under "plan"; the
        # older keys are kept for callers that pass a raw graph state.
        plan_raw = state.get("planner_plan") or state.get("plan") or state.get("planned_tasks") or []
        mcp_tools = state.get("mcp_tools_used", [])

        # ── Causal Path: structured cognitive trace ──────────────────────────
        causal_path = {
            "routing_decision": "semantic_pre_router" if state.get("direct_expert") else "planner",
            "direct_expert":    state.get("direct_expert"),
            "experts":          experts_used,
            "judge_intervened": bool(state.get("judge_refined")),
            "judge_reason":     state.get("judge_reason"),
            "cost_tier":        state.get("cost_tier"),
            "correction_applied": bool(state.get("correction_applied")),
            "causal_graph_map":  state.get("causal_graph_map"),
        }

        graphrag_entities  = state.get("graphrag_entities")   # list of entity dicts
        judge_before_after = state.get("judge_before_after")  # {before, after, delta}
        expert_inputs      = state.get("expert_inputs")       # {name: {tokens, latency_ms}}
        judge_reason       = state.get("judge_reason")
        tool_calls_log     = state.get("tool_calls_log")      # [{tool, args, result, status, ts}]
        working_memory     = state.get("working_memory")      # {key: {value, source, confidence, ts}}

        async with pool.connection() as conn:
            await conn.execute(
                _INSERT_SQL,
                (
                    response_id,
                    state.get("user_id", ""),
                    state.get("template_name", ""),
                    len(user_input),
                    _detect_language(user_input),
                    state.get("complexity_level", ""),
                    bool(state.get("images")),
                    _has_code(user_input),
                    json.dumps(plan_raw, ensure_ascii=False) if plan_raw else None,
                    experts_used if experts_used else None,
                    mcp_tools if mcp_tools else None,
                    bool(state.get("cache_hit")),
                    bool(state.get("fast_path")),
                    None,
                    bool(state.get("judge_refined")),
                    bool(state.get("correction_applied")),
                    state.get("prompt_tokens", 0) + state.get("completion_tokens", 0),
                    wall_clock_ms,
                    json.dumps(expert_scores, ensure_ascii=False) if expert_scores else None,
                    # Causal-path columns
                    json.dumps(causal_path, ensure_ascii=False),
                    json.dumps(graphrag_entities, ensure_ascii=False) if graphrag_entities else None,
                    judge_reason,
                    json.dumps(judge_before_after, ensure_ascii=False) if judge_before_after else None,
                    json.dumps(expert_inputs, ensure_ascii=False) if expert_inputs else None,
                    # Working Memory Hub columns
                    json.dumps(tool_calls_log, ensure_ascii=False) if tool_calls_log else None,
                    json.dumps(working_memory, ensure_ascii=False) if working_memory else None,
                ),
            )
    except Exception as exc:
        logger.debug("telemetry insert failed: %s", exc)


async def record_user_feedback(pool, response_id: str, rating: int) -> None:
    """Update the user_rating column for an existing telemetry row."""
    if pool is None:
        return
    try:
        async with pool.connection() as conn:
            await conn.execute(_UPDATE_RATING_SQL, (rating, response_id))
    except Exception as exc:
        logger.debug("telemetry rating update failed: %s", exc)


async def record_self_score(
    pool,
    response_id: str,
    score: int,
    template_name: str = "",
    complexity: str = "",
) -> None:
    """Update the self_score column and accumulate quality stats for routing hints."""
    if pool is None:
        return
    try:
        async with pool.connection() as conn:
            await conn.execute(_UPDATE_SELF_SCORE_SQL, (score, response_id))
            if template_name and complexity:
                await conn.execute(_UPDATE_QUALITY_STATS_SQL, (template_name, complexity, float(score)))
    except Exception as exc:
        logger.debug("telemetry self_score update failed: %s", exc)


async def get_quality_hint(pool, template_name: str, complexity: str) -> str:
    """Return a short routing hint based on accumulated quality stats for this template/complexity.

    Returns an empty string when no data is available yet or on error.
    """
    if pool is None or not template_name or not complexity:
        return ""
    try:
        async with pool.connection() as conn:
            cur = await conn.execute(_GET_QUALITY_HINT_SQL, (template_name, complexity))
            row = await cur.fetchone()
            if row and row[1] >= 3:  # row: (avg_score, sample_count)
                avg = round(row[0], 1)
                n   = row[1]
                return f"Quality note: this template scored {avg}/5 on average for {complexity} queries ({n} samples)."
    except Exception as exc:
        logger.debug("quality hint query failed: %s", exc)
    return ""


async def save_kpi_snapshot(pool, window: str, snapshot_dict: dict) -> None:
    """Persist a KPI snapshot to moe_kpi_snapshots for historical comparison."""
    if pool is None:
        return
    try:
        async with pool.connection() as conn:
            await conn.execute(_INSERT_KPI_SNAPSHOT_SQL, (window, json.dumps(snapshot_dict, ensure_ascii=False)))
    except Exception as exc:
        logger.debug("kpi snapshot save failed: %s", exc)


async def get_last_kpi_snapshot(pool, window: str) -> dict | None:
    """Retrieve the most recent KPI snapshot for the given window, or None."""
    if pool is None:
        return None
    try:
        async with pool.connection() as conn:
            cur = await conn.execute(_GET_KPI_SNAPSHOT_SQL, (window,))
            row = await cur.fetchone()
            if row:
                return {"snapshot": row[0], "computed_at": row[1].isoformat() if row[1] else None}
    except Exception as exc:
        logger.debug("kpi snapshot load failed: %s", exc)
    return None
