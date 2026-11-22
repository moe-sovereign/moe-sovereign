"""
services/decision_log.py — Append-only Decision Log with mandatory rationale.

Every non-trivial runtime decision (constitution block, DoR fail, trust block,
replan, stuck loop, self-critique trigger) is persisted here with a mandatory
rationale field. This satisfies EU AI Act Art. 13 (transparency) and enables
post-mortem analysis without relying on ephemeral application logs.

Backend: Kafka topic moe.decisions (primary) + decision_log.jsonl (fallback).
Fail-open: errors in persistence never block the pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from enum import Enum
from typing import Optional

logger = logging.getLogger("MOE-SOVEREIGN")

KAFKA_TOPIC_DECISIONS = "moe.decisions"
_FALLBACK_LOG_PATH = os.getenv("DECISION_LOG_PATH", "/app/logs/decision_log.jsonl")


class DecisionType(str, Enum):
    JUDGE_OVERRIDE          = "JUDGE_OVERRIDE"
    CONSTITUTION_BLOCK      = "CONSTITUTION_BLOCK"
    CONSTITUTION_WARN       = "CONSTITUTION_WARN"
    DOR_FAIL                = "DOR_FAIL"
    TRUST_BLOCK             = "TRUST_BLOCK"
    REPLAN                  = "REPLAN"
    STUCK_LOOP              = "STUCK_LOOP"
    SELF_CRITIQUE_TRIGGERED = "SELF_CRITIQUE_TRIGGERED"
    HALLUCINATION_CHECK     = "HALLUCINATION_CHECK"
    BOUNDARY_VIOLATION      = "BOUNDARY_VIOLATION"
    SCOPE_VIOLATION         = "SCOPE_VIOLATION"
    MCP_TOOL_ACCESS         = "MCP_TOOL_ACCESS"
    KNOWLEDGE_CONFLICT      = "KNOWLEDGE_CONFLICT"
    VERIFIER_SCORE          = "VERIFIER_SCORE"
    FALLBACK_DIRECT         = "FALLBACK_DIRECT"


def log_decision(
    decision_type: DecisionType,
    request_id: str,
    rationale: str,
    metadata: Optional[dict] = None,
) -> None:
    """Persist a decision entry synchronously to the fallback log and schedule Kafka emit.

    Args:
        decision_type: Type of decision from DecisionType enum.
        request_id:    Unique request identifier for correlation.
        rationale:     Non-empty explanation of WHY the decision was made.
        metadata:      Optional additional context (rule_id, task_id, score, …).

    Raises:
        ValueError: If rationale is empty — the rationale is the whole point.
    """
    if not rationale or not rationale.strip():
        raise ValueError("decision_log: rationale must not be empty")

    entry = {
        "decision_type": decision_type.value,
        "request_id":    request_id,
        "rationale":     rationale.strip(),
        "ts":            time.time(),
        **(metadata or {}),
    }

    # Primary: emit to Kafka asynchronously
    try:
        from services.kafka import _kafka_publish
        # Resolve the loop before creating the coroutine. Passing
        # ``_kafka_publish(...)`` directly to asyncio.create_task constructs a
        # coroutine first; when no loop exists create_task raises and leaves
        # that coroutine un-awaited, producing RuntimeWarning and losing the
        # intended publish.
        loop = asyncio.get_running_loop()
        loop.create_task(_kafka_publish(KAFKA_TOPIC_DECISIONS, entry))
    except RuntimeError:
        # No running event loop (e.g. during sync test) — skip Kafka
        pass
    except Exception as e:
        logger.debug("decision_log: Kafka emit failed: %s", e)

    # Fallback: append to JSONL file (always runs, so log survives Kafka outage)
    try:
        log_dir = os.path.dirname(_FALLBACK_LOG_PATH)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        with open(_FALLBACK_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("decision_log: fallback write failed: %s", e)

    # Local ACID persistence if DECISION_LOG_DB_PATH is set
    db_path = os.getenv("DECISION_LOG_DB_PATH")
    if db_path:
        try:
            initialize_wal_db(db_path)
            log_decision_acid(db_path, {"task_id": request_id, "decision": decision_type.value, "metadata": entry})
        except Exception as e:
            logger.warning("decision_log: SQLite WAL acid logging failed: %s", e)

    logger.info(
        "📋 Decision[%s] req=%s: %s",
        entry["decision_type"], request_id, rationale[:120],
    )

import sqlite3
import uuid

def initialize_wal_db(db_path: str) -> None:
    """Initialize SQLite database with WAL mode and create decisions table."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute('''
            CREATE TABLE IF NOT EXISTS decisions (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                metadata TEXT,
                created_at REAL NOT NULL
            )
        ''')

def log_decision_acid(db_path: str, decision_event: dict) -> str:
    """Log a decision atomically to the SQLite database."""
    task_id = decision_event.get('task_id')
    decision = decision_event.get('decision')
    
    if not task_id or not decision:
        raise ValueError("task_id and decision must be provided")
        
    record_id = str(uuid.uuid4())
    metadata = decision_event.get('metadata')
    metadata_json = json.dumps(metadata) if metadata is not None else None
    
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO decisions (id, task_id, decision, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
            (record_id, task_id, decision, metadata_json, time.time())
        )
    return record_id
