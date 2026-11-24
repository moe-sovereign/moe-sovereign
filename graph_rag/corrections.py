"""Correction Memory — stores and retrieves past expert corrections in Neo4j.

When a judge refinement or self-correction fixes an expert response, we
persist the (wrong → correct) pair as a :Correction node linked to related
:Entity nodes. At query time, similar corrections are injected into the
expert prompt so the model avoids repeating the same mistakes.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("moe.corrections")

CORRECTION_MEMORY_ENABLED_DEFAULT = True

_ENSURE_SCHEMA = """
CREATE CONSTRAINT correction_hash_unique IF NOT EXISTS
  FOR (c:Correction) REQUIRE c.hash IS UNIQUE
"""

_STORE_CORRECTION = """
MERGE (c:Correction {hash: $hash})
ON CREATE SET
  c.prompt_pattern    = $prompt_pattern,
  c.wrong_summary     = $wrong_summary,
  c.correct_summary   = $correct_summary,
  c.category          = $category,
  c.source_model      = $source_model,
  c.correction_source = $correction_source,
  c.confidence        = $confidence,
  c.times_applied     = 0,
  c.created           = datetime(),
  c.tenant_id         = $tenant_id
ON MATCH SET
  c.times_applied = c.times_applied + 1,
  c.confidence    = CASE WHEN $confidence > c.confidence THEN $confidence ELSE c.confidence END
RETURN c.hash AS hash, c.times_applied AS times_applied
"""

_QUERY_CORRECTIONS = """
MATCH (c:Correction)
WHERE c.category = $category
  AND c.confidence >= $min_confidence
  AND ($tenant_ids = [] OR c.tenant_id IS NULL OR c.tenant_id = '' OR c.tenant_id IN $tenant_ids)
WITH c, apoc.text.sorensenDiceSimilarity(toLower(c.prompt_pattern), toLower($query)) AS sim
WHERE sim > $min_similarity
RETURN c.wrong_summary AS wrong, c.correct_summary AS correct,
       c.source_model AS model, c.correction_source AS source, sim
ORDER BY sim DESC
LIMIT $limit
"""

_QUERY_CORRECTIONS_FALLBACK = """
MATCH (c:Correction)
WHERE c.category = $category
  AND c.confidence >= $min_confidence
  AND ($tenant_ids = [] OR c.tenant_id IS NULL OR c.tenant_id = '' OR c.tenant_id IN $tenant_ids)
RETURN c.wrong_summary AS wrong, c.correct_summary AS correct,
       c.source_model AS model, c.correction_source AS source
ORDER BY c.created DESC
LIMIT $limit
"""


def _correction_hash(prompt: str, wrong: str, correct: str) -> str:
    blob = f"{prompt[:200]}|{wrong[:200]}|{correct[:200]}"
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


async def ensure_schema(driver) -> None:
    async with driver.session() as session:
        try:
            await session.run(_ENSURE_SCHEMA)
        except Exception as exc:
            logger.debug("Correction schema setup: %s", exc)


async def store_correction(
    driver,
    prompt: str,
    wrong: str,
    correct: str,
    category: str,
    source_model: str = "",
    correction_source: str = "judge_refinement",
    confidence: float = 0.7,
    tenant_id: str = "",
) -> Optional[str]:
    """Persist a correction pair. Returns the hash on success."""
    if driver is None:
        return None
    h = _correction_hash(prompt, wrong, correct)
    try:
        async with driver.session() as session:
            result = await session.run(
                _STORE_CORRECTION,
                hash=h,
                prompt_pattern=prompt[:500],
                wrong_summary=wrong[:500],
                correct_summary=correct[:500],
                category=category,
                source_model=source_model,
                correction_source=correction_source,
                confidence=confidence,
                tenant_id=tenant_id,
            )
            record = await result.single()
            if record:
                logger.info(
                    "Correction %s stored (applied=%d)",
                    record["hash"], record["times_applied"],
                )
                return record["hash"]
    except Exception as exc:
        logger.warning("store_correction failed: %s", exc)
    return None


async def query_corrections(
    driver,
    query: str,
    category: str,
    limit: int = 3,
    min_confidence: float = 0.6,
    min_similarity: float = 0.25,
    tenant_ids: Optional[list] = None,
) -> list[dict]:
    """Find corrections relevant to *query* in *category*.

    With *tenant_ids* only global corrections (no tenant) and those of the given tenants are returned; without them the
    query is unscoped, like the entity lookups in GraphRAGManager."""
    if driver is None:
        return []
    try:
        async with driver.session() as session:
            try:
                # Parameters go in a dict: session.run() has a positional argument called `query`, so passing the Cypher
                # parameter `query` as a keyword raised a TypeError that the except below swallowed, and the similarity
                # query never ran (every call used the newest-first fallback).
                result = await session.run(
                    _QUERY_CORRECTIONS,
                    {
                        "query": query[:500],
                        "category": category,
                        "limit": limit,
                        "min_confidence": min_confidence,
                        "min_similarity": min_similarity,
                        "tenant_ids": list(tenant_ids or []),
                    },
                )
                records = [dict(r) async for r in result]
            except Exception:
                result = await session.run(
                    _QUERY_CORRECTIONS_FALLBACK,
                    {
                        "category": category,
                        "limit": limit,
                        "min_confidence": min_confidence,
                        "tenant_ids": list(tenant_ids or []),
                    },
                )
                records = [dict(r) async for r in result]
            return records
    except Exception as exc:
        logger.debug("query_corrections failed: %s", exc)
        return []


def format_correction_context(corrections: list[dict]) -> str:
    """Format correction records for injection into expert prompts."""
    if not corrections:
        return ""
    lines = ["[CORRECTION MEMORY — avoid repeating these past errors]"]
    for c in corrections:
        lines.append(f"- Wrong: {c['wrong']}")
        lines.append(f"  Correct: {c['correct']}")
    return "\n".join(lines)
