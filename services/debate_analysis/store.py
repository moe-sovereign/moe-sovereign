"""Persistence of debate-analysis results.

Only scores, versions and the transcript hash are stored: no transcript text
and no model output. Speaker labels are kept because the scores cannot be read
without them; callers should pass pseudonyms when the labels are personal data.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from .results import DebateAnalysis

logger = logging.getLogger("MOE-SOVEREIGN")

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS debate_analysis_log (
    id                TEXT PRIMARY KEY,
    created_at        TIMESTAMPTZ DEFAULT now(),
    request_id        TEXT,
    user_id           TEXT,
    template_id       TEXT,
    rubric_version    TEXT NOT NULL,
    rubric_status     TEXT,
    catalog_version   TEXT,
    transcript_sha256 TEXT NOT NULL,
    profile           JSONB,
    judge_ids         JSONB,
    speakers          JSONB,
    winners           JSONB,
    overall_winner    TEXT,
    runs_valid        INT,
    runs_total        INT,
    model_calls       INT,
    mean_dispersion   DOUBLE PRECISION,
    not_assessable    JSONB,
    notes             JSONB
);
"""

# Additive migrations for columns introduced after first deployment go here as
# ALTER TABLE ... ADD COLUMN IF NOT EXISTS statements.
_MIGRATE_SQL = ""


def build_log_row(
    analysis: DebateAnalysis,
    *,
    request_id: str = "",
    user_id: str = "",
    template_id: str = "",
) -> dict[str, Any]:
    """Return the row to insert. Contains no transcript or model text."""

    speakers = {
        name: {
            "axes": {
                axis: {
                    "score": s.score,
                    "confidence": s.confidence,
                    "not_assessable": s.not_assessable,
                }
                for axis, s in result.axes.items()
            },
            "criteria": {cid: c.model_dump(mode="json") for cid, c in result.criteria.items()},
            "fallacies": [
                {"id": f.fallacy_id, "turns": f.turn_indices, "agreement": f.agreement}
                for f in result.fallacies
            ],
            "misstep_turns": [m.turn_index for m in result.missteps],
            "overall": result.overall,
        }
        for name, result in analysis.speakers.items()
    }
    return {
        "id": uuid.uuid4().hex,
        "request_id": request_id,
        "user_id": user_id,
        "template_id": template_id,
        "rubric_version": analysis.rubric_version,
        "rubric_status": analysis.rubric_status,
        "catalog_version": analysis.catalog_version,
        "transcript_sha256": analysis.transcript_sha256,
        "profile": json.dumps(analysis.profile.model_dump(mode="json")),
        "judge_ids": json.dumps(analysis.judge_ids),
        "speakers": json.dumps(speakers),
        "winners": json.dumps(analysis.winners),
        "overall_winner": analysis.overall_winner,
        "runs_valid": analysis.runs_valid,
        "runs_total": analysis.runs_total,
        "model_calls": analysis.model_calls,
        "mean_dispersion": analysis.mean_dispersion,
        "not_assessable": json.dumps(analysis.not_assessable_criteria),
        "notes": json.dumps(analysis.notes),
    }


async def record_analysis(
    analysis: DebateAnalysis,
    *,
    request_id: str = "",
    user_id: str = "",
    template_id: str = "",
) -> bool:
    """Insert the row. Telemetry is non-authoritative: failure is logged, not raised."""

    row = build_log_row(
        analysis, request_id=request_id, user_id=user_id, template_id=template_id
    )
    try:
        from config import MOE_USERDB_URL
        import psycopg

        async with await psycopg.AsyncConnection.connect(MOE_USERDB_URL) as conn:
            async with conn.cursor() as cur:
                await cur.execute(_TABLE_SQL)
                if _MIGRATE_SQL:
                    await cur.execute(_MIGRATE_SQL)
                cols = ",".join(row.keys())
                placeholders = ",".join(["%s"] * len(row))
                await cur.execute(
                    f"INSERT INTO debate_analysis_log ({cols}) VALUES ({placeholders})",
                    list(row.values()),
                )
            await conn.commit()
        return True
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("debate_analysis: DB insert failed: %s", type(exc).__name__)
        return False
