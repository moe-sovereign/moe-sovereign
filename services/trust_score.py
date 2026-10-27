"""
services/trust_score.py — Trust-Score / Verification Substrate (TASK-10).

Computes a quantitative trust score [0.0–1.0] after each expert round.
Three outcome buckets drive downstream pipeline decisions:
  PROCEED             (≥0.65): response is trusted, proceed normally
  PROCEED_WITH_ASSUMPTION (0.30–0.65): trigger Self-Critique Loop (TASK-11)
  BLOCK               (<0.30): do not send response, emit decision log entry

Hard-blocks override the score regardless of its value:
  - Zero expert results with no fallback sources
  - Unresolvable paraconsistent contradiction (conflict_registry with no judge resolution)

All checks are deterministic — no LLM involvement.
Weights are configurable via TRUST_SCORE_WEIGHTS_JSON env var (JSON dict).
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict

logger = logging.getLogger("MOE-SOVEREIGN")

# ── Configuration ──────────────────────────────────────────────────────────────

_DEFAULT_WEIGHTS: Dict[str, float] = {
    "source_count":              0.35,   # Neo4j nodes cited or web sources found
    "expert_count":              0.25,   # Non-empty expert results
    "conflict_penalty":          0.20,   # Deducted per unresolved paraconsistent conflict
    "cross_reference_coverage":  0.20,   # Fraction of plan tasks with non-empty expert result
    "deterministic_coverage":     0.32,   # Fraction of precision tasks with completed MCP evidence
    "creative_task_fit":          0.45,   # Source-free creative tasks are judged by execution, not citations
    # Hallucination proxy, Stage 1 (cheap/deterministic): fraction of specific-
    # looking claims (numbers, proper nouns) in the expert output that don't
    # appear anywhere in the retrieved sources. Deliberately noisy — paraphrasing,
    # unit conversion and calculated values all look "unsupported" under literal
    # substring matching — so this starts at a low weight relative to the other
    # factors. Tune via TRUST_SCORE_WEIGHTS_JSON after observing real traffic
    # rather than assuming this default is right.
    "unsupported_claims_penalty": 0.10,
}

# Cheap, no-LLM extraction of specific/verifiable-looking tokens: multi-digit
# numbers and capitalized proper-noun-like phrases (1-3 words). NOT the same
# extractor as graph_rag/manager.py's _extract_terms() — that one is built for
# loosely matching GraphRAG retrieval queries (false positives there are
# harmless) and is too noisy to repurpose here; see the ontology gap-healer's
# MIN_GAP_SCORE fix (scripts/gap_healer_templates.py) for a concrete case where
# reusing it for a different purpose caused problems.
_CHECKABLE_NUMBER_RE      = re.compile(r"\b\d[\d.,]{1,}\b")
_CHECKABLE_PROPER_NOUN_RE = re.compile(r"\b[A-ZÄÖÜ][a-zäöüß]{2,}(?:\s+[A-ZÄÖÜ][a-zäöüß]{2,}){0,2}\b")
_MAX_CHECKABLE_CLAIMS = 30


def _extract_checkable_claims(text: str) -> set:
    """Extract candidate specific claims from text. False positives are
    expected and acceptable — this only nudges trust_score via a low-weight
    factor, it never hard-blocks by itself."""
    if not text:
        return set()
    numbers = _CHECKABLE_NUMBER_RE.findall(text)
    nouns   = _CHECKABLE_PROPER_NOUN_RE.findall(text)
    claims  = {c.strip() for c in (numbers + nouns) if len(c.strip()) > 2}
    return set(list(claims)[:_MAX_CHECKABLE_CLAIMS])


def _unsupported_claim_ratio(response_text: str, source_text: str) -> float:
    """Fraction of checkable claims in response_text absent from source_text.

    Returns 0.0 (no penalty) when there's nothing to check — no source
    material retrieved at all, or the text makes no specific-looking claims.
    A high ratio suggests ungrounded specifics (a hallucination proxy); a
    non-zero ratio on an otherwise-fine answer is expected noise, not a bug —
    see the factor's default weight above.
    """
    if not response_text or not source_text.strip():
        return 0.0
    claims = _extract_checkable_claims(response_text)
    if not claims:
        return 0.0
    source_lower = source_text.lower()
    unsupported = sum(1 for c in claims if c.lower() not in source_lower)
    return unsupported / len(claims)

_THRESHOLD_PROCEED      = float(os.getenv("TRUST_SCORE_PROCEED",      "0.65"))
_THRESHOLD_ASSUMPTION   = float(os.getenv("TRUST_SCORE_ASSUMPTION",   "0.30"))

_MAX_SOURCE_COUNT = 10   # Normalise source_count to [0, 1]: min(count/max, 1)
_MAX_EXPERT_COUNT = 5    # Normalise expert_count to [0, 1]: min(count/max, 1)
# expert_results entries that are appended by the pipeline itself rather than
# produced by a planned expert task.
_NON_EXPERT_RESULT_PREFIXES = ("[SELF_CRITIQUE_", "[REVIEW:")


def _load_weights() -> Dict[str, float]:
    raw = os.getenv("TRUST_SCORE_WEIGHTS_JSON", "")
    if raw:
        try:
            return {**_DEFAULT_WEIGHTS, **json.loads(raw)}
        except Exception:
            logger.warning("trust_score: invalid TRUST_SCORE_WEIGHTS_JSON — using defaults")
    return dict(_DEFAULT_WEIGHTS)


# ── Data types ─────────────────────────────────────────────────────────────────

class TrustVerdict(str, Enum):
    PROCEED               = "PROCEED"
    PROCEED_WITH_ASSUMPTION = "PROCEED_WITH_ASSUMPTION"
    BLOCK                 = "BLOCK"


@dataclass
class TrustScore:
    score:        float
    verdict:      TrustVerdict
    hard_blocked: bool
    factors:      Dict[str, float] = field(default_factory=dict)
    reason:       str = ""


# ── Core computation ───────────────────────────────────────────────────────────

def compute_trust_score(state_: dict) -> TrustScore:
    """Compute a trust score from the current AgentState snapshot.

    Args:
        state_: The AgentState dict (or any compatible dict).

    Returns:
        TrustScore with verdict and per-factor breakdown for logging.
    """
    weights = _load_weights()

    expert_results  = state_.get("expert_results") or []
    plan            = state_.get("plan") or []
    graph_context   = state_.get("graph_context") or ""
    web_research    = state_.get("web_research") or ""
    mcp_evidence    = state_.get("mcp_evidence") or []
    task_events     = state_.get("task_events") or []
    conflict_reg    = state_.get("conflict_registry") or []
    judge_before_after = state_.get("judge_before_after") or {}

    # ── Factor 1: source_count ─────────────────────────────────────────────────
    # Count only identifiable GraphRAG chunks, not arbitrary context text. A
    # non-empty but irrelevant graph response must not increase trust.
    _retrieved_graph_chunks = [
        chunk
        for chunk in (state_.get("retrieved_graph_chunks") or [])
        if isinstance(chunk, dict)
    ]
    _neo4j_hits = len(_retrieved_graph_chunks)
    _web_hits   = web_research.count("http://") + web_research.count("https://")
    _completed_mcp = [
        evidence
        for evidence in mcp_evidence
        if isinstance(evidence, dict)
        and evidence.get("status") == "completed"
        and str(evidence.get("result") or "").strip()
    ]
    source_count_raw = _neo4j_hits + _web_hits + len(_completed_mcp)
    source_count_norm = min(source_count_raw / _MAX_SOURCE_COUNT, 1.0)

    # ── Factor 2: expert_count ─────────────────────────────────────────────────
    non_empty_experts = [
        r
        for r in expert_results
        if r
        and len(r.strip()) > 20
        and " ERROR]:" not in r
        and not r.startswith("[Judge unavailable")
        # Judge-authored self-critique fragments and complementary reviews are
        # not independent expert answers; counting them inflated the score by
        # +1/_MAX_EXPERT_COUNT per round and let the loop "pass" itself.
        and not r.startswith(_NON_EXPERT_RESULT_PREFIXES)
    ]
    expert_count_norm = min(len(non_empty_experts) / _MAX_EXPERT_COUNT, 1.0)

    # ── Factor 3: conflict_penalty ─────────────────────────────────────────────
    # Unresolved conflicts reduce score; judge resolution mitigates
    judge_resolved = bool(judge_before_after.get("after_score", 0) > judge_before_after.get("before_score", 0))
    unresolved_conflicts = len(conflict_reg) if not judge_resolved else 0
    conflict_penalty_norm = min(unresolved_conflicts / 3.0, 1.0)  # ≥3 conflicts → max penalty

    # ── Factor 4: cross_reference_coverage ────────────────────────────────────
    # What fraction of planned tasks has a successful terminal execution event?
    plan_count = max(len(plan), 1)
    current_iteration = int(state_.get("agentic_iteration") or 0)
    completed_task_ids = {
        str(event.get("task_id") or "")
        for event in task_events
        if isinstance(event, dict)
        and int(event.get("iteration") or 0) == current_iteration
        and event.get("status") == "completed"
    }
    tracked_task_ids = {
        str(task.get("id") or "")
        for task in plan
        if isinstance(task, dict) and task.get("id")
    }
    if tracked_task_ids:
        covered = len(tracked_task_ids & completed_task_ids)
    else:
        # Backwards-compatible scoring for historical states/tests created
        # before the execution ledger existed.
        covered = min(len(non_empty_experts), plan_count)
    cross_ref_norm = covered / plan_count

    precision_tasks = [
        task
        for task in plan
        if isinstance(task, dict) and task.get("category") == "precision_tools"
    ]
    completed_precision_ids = {
        str(evidence.get("task_id") or "")
        for evidence in _completed_mcp
    }
    deterministic_coverage = (
        len(
            {
                str(task.get("id") or "")
                for task in precision_tasks
                if task.get("id")
            }
            & completed_precision_ids
        )
        / len(precision_tasks)
        if precision_tasks
        else 0.0
    )

    plan_categories = {
        str(task.get("category") or "")
        for task in plan
        if isinstance(task, dict)
    }
    creative_task_fit = (
        1.0
        if plan_categories
        and plan_categories <= {"creative_writer", "creative"}
        and covered == len(plan)
        and bool(non_empty_experts)
        and not unresolved_conflicts
        else 0.0
    )

    # ── Factor 5: unsupported_claims_penalty (hallucination proxy, Stage 1) ────
    combined_expert_text = " ".join(non_empty_experts)
    # The pre-synthesis expert-claim proxy may compare only like-for-like
    # retrieval evidence. MCP results ground their own precision tasks but are
    # not a textual source for unrelated expert prose (for example SQL review).
    combined_source_text = "\n".join(
        [
            graph_context if _retrieved_graph_chunks else "",
            web_research,
        ]
    )
    unsupported_ratio = _unsupported_claim_ratio(combined_expert_text, combined_source_text)

    factors = {
        "source_count":              source_count_norm,
        "expert_count":              expert_count_norm,
        "conflict_penalty":          conflict_penalty_norm,
        "cross_reference_coverage":  cross_ref_norm,
        "deterministic_coverage":    deterministic_coverage,
        "creative_task_fit":         creative_task_fit,
        "unsupported_claims_penalty": unsupported_ratio,
    }

    raw_score = (
        weights["source_count"]             * source_count_norm
        + weights["expert_count"]           * expert_count_norm
        - weights["conflict_penalty"]       * conflict_penalty_norm
        + weights["cross_reference_coverage"] * cross_ref_norm
        + weights["deterministic_coverage"] * deterministic_coverage
        + weights["creative_task_fit"] * creative_task_fit
        - weights["unsupported_claims_penalty"] * unsupported_ratio
    )
    score = max(0.0, min(1.0, raw_score))

    # A conservative trivial fast-path is deliberately a direct model
    # execution: the request has already been rejected by the shared gate when
    # it needs retrieval, exact operations, legal/current data, tools, files,
    # conversation context or a specialized mode. Requiring external source
    # count for the remaining context-free one-shot answer would classify every
    # valid response (including "OK") as BLOCK and trigger two judge loops,
    # negating the path entirely. A present expert answer with no conflict is
    # therefore sufficient for PROCEED; the Guard, Constitution and final
    # Quality-Gate remain in the graph.
    trivial_direct_verified = (
        bool(state_.get("trivial_fast_path"))
        and bool(non_empty_experts)
        and not unresolved_conflicts
    )
    if trivial_direct_verified:
        score = max(score, _THRESHOLD_PROCEED)
        factors["trivial_fast_path"] = 1.0

    # ── Hard-block conditions (override score) ─────────────────────────────────
    hard_blocked = False
    hard_reason  = ""

    if (
        not non_empty_experts
        and not _retrieved_graph_chunks
        and not web_research.strip()
        and not _completed_mcp
    ):
        hard_blocked = True
        hard_reason  = "No expert results and no retrieval context — cannot produce a grounded response"

    if hard_blocked:
        return TrustScore(
            score=0.0,
            verdict=TrustVerdict.BLOCK,
            hard_blocked=True,
            factors=factors,
            reason=hard_reason,
        )

    # ── Verdict from score thresholds ─────────────────────────────────────────
    if score >= _THRESHOLD_PROCEED:
        verdict = TrustVerdict.PROCEED
    elif score >= _THRESHOLD_ASSUMPTION:
        verdict = TrustVerdict.PROCEED_WITH_ASSUMPTION
    else:
        verdict = TrustVerdict.BLOCK

    reason_parts = [f"score={score:.3f}"]
    if verdict == TrustVerdict.BLOCK:
        reason_parts.append(f"below threshold {_THRESHOLD_ASSUMPTION}")
    elif verdict == TrustVerdict.PROCEED_WITH_ASSUMPTION:
        reason_parts.append(f"between {_THRESHOLD_ASSUMPTION}–{_THRESHOLD_PROCEED}")
    if unresolved_conflicts:
        reason_parts.append(f"{unresolved_conflicts} unresolved conflict(s)")
    if unsupported_ratio > 0.3:
        reason_parts.append(f"{unsupported_ratio:.0%} of claims unsupported by sources")
    if trivial_direct_verified:
        reason_parts.append("verified context-free direct path")
    if deterministic_coverage:
        reason_parts.append(
            f"{deterministic_coverage:.0%} deterministic task coverage"
        )
    if creative_task_fit:
        reason_parts.append("source-free creative task executed")

    return TrustScore(
        score=score,
        verdict=verdict,
        hard_blocked=False,
        factors=factors,
        reason=", ".join(reason_parts),
    )
