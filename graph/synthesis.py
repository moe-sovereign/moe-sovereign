"""graph/synthesis.py — merger, thinking, conflict resolution, critic, replan router."""

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx

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
    AGENTIC_GAP_THRESHOLD_TOKENS, WM_EXTRACT_THRESHOLD_TOKENS,
    COT_MIN_CATEGORIES, COT_MIN_TASKS,
    JUDGE_NUM_CTX, JUDGE_MODEL as _JUDGE_MODEL_NAME,
)
from metrics import (
    PROM_EXPERT_CALLS, PROM_CONFIDENCE, PROM_CACHE_HITS, PROM_CACHE_MISSES,
    PROM_SELF_EVAL, PROM_COMPLEXITY, PROM_ACTIVE_REQUESTS,
    PROM_TOOL_CALL_DURATION, PROM_TOOL_TIMEOUTS, PROM_TOOL_FORMAT_ERRORS,
    PROM_TOOL_CALL_SUCCESS, PROM_SEMANTIC_MEMORY_STORED, PROM_SEMANTIC_MEMORY_HITS,
    PROM_CORRECTIONS_INJECTED, PROM_CORRECTIONS_STORED,
    PROM_JUDGE_REFINED, PROM_EXPERT_FAILURES, PROM_SYNTHESIS_CREATED,
    PROM_HISTORY_COMPRESSED, PROM_HISTORY_UNLIMITED,
    PROM_BUDGET_EXCEEDED,
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


def _judge_ctx_budget(state_num_ctx: int = 0) -> dict:
    """Derive char budgets for judge-facing content from the judge model's context window.

    Priority: state_num_ctx (per-template) > JUDGE_NUM_CTX (global env) > static model
    table. All quality-affecting truncations in merger, gap detection, working-memory
    extraction and reasoning nodes scale with the configured judge context window
    instead of being static constants. Metadata and telemetry fields stay small
    regardless (they go into Kafka / ChromaDB, not the LLM prompt).

    Reserves 35% of the window for the static judge prompt + expert responses
    already assembled before these blocks are appended.
    """
    from context_budget import get_model_context_window as _static_ctx
    ctx_tokens  = state_num_ctx or JUDGE_NUM_CTX or _static_ctx(_JUDGE_MODEL_NAME) or 8192
    avail_chars = int(ctx_tokens * 4 * 0.65)   # 4 chars/token, 65% for dynamic content
    return {
        # merger_node: context injected before expert responses
        "web_context":         min(avail_chars // 5,  40_000),   # was 1500
        "graph_context":       min(avail_chars // 8,  20_000),   # was 800
        "cached_prior":        min(avail_chars // 8,  20_000),   # was 1000
        # gap detection: question + current answer shown to judge
        "gap_question":        min(avail_chars // 6,  30_000),   # was 600
        "gap_answer":          min(avail_chars // 4,  50_000),   # was 800
        # working memory extraction: full answer text fed to judge
        "wm_extract_text":     min(avail_chars // 4,  50_000),   # was 500
        # reasoning node (thinking mode): web + graph context blocks
        "reasoning_web":       min(avail_chars // 5,  40_000),   # was 1000
        "reasoning_graph":     min(avail_chars // 8,  20_000),   # was 500
    }

# Cross-module: graph context compression helpers live in graph.research
from graph.research import _rerank_graph_context, _compress_graph_context_llm
from services.deadline import RequestDeadlineExceeded, remaining_timeout
from episodic_memory import log_episode

_RUST_CODE_FENCE_RE = re.compile(r"```rust\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)
_RUST_COMPILE_CHECK_CATEGORIES = {"systems_programming", "code_reviewer"}
_RUST_COMPILE_CHECK_TIMEOUT_S = 60.0  # PoC hardware -- above mcp_server's own _RUST_COMPILE_HTTP_TIMEOUT_S (45s)

# Loom (Phase 2) only makes sense for a response that is *already written*
# as a loom-model test (a `#[test] fn` calling `loom::model(...)` against
# loom's own Arc/Mutex/Atomic/thread shims) -- rust_compile_check's plain
# "does it compile" fence can be arbitrary code, but feeding that same
# arbitrary code straight into the loom sandbox would almost always fail to
# compile there (no loom imports, no loom::model call) and generate a
# meaningless "fix these errors" retry prompt. Gating on the literal `loom::`
# marker means this only activates when the expert/merger response already
# demonstrates concurrency verification, not on every concurrent-looking fence.
#
# Kept in sync with _RUST_COMPILE_CHECK_CATEGORIES: EXPERT_MODELS has no
# "systems_programming" key, so a planner-assigned category of that name
# falls back to the "general" expert at dispatch time (graph/expert.py) --
# without "code_reviewer" here too, the gate would silently never fire for
# the actual routed coder expert and this file would never collect examples.
_RUST_LOOM_CHECK_CATEGORIES = {"systems_programming", "code_reviewer"}
_RUST_LOOM_MARKER_RE = re.compile(r"\bloom::")
_RUST_LOOM_CHECK_TIMEOUT_S = 280.0  # PoC hardware -- above mcp_server's own _RUST_LOOM_HTTP_TIMEOUT_S (240s)

# Raw material for a future LUMI-G SFT/DPO pass on Candidate 1 (acquire-
# release memory-ordering reasoning, docs/experiments/
# lumig_posttraining_candidates.md) -- that document's own recommendation is
# a compiler/sanitizer-verified reward signal instead of pure LLM-judge
# feedback, which rust_loom_check now provides live. Written under data/
# (bind-mounted, ./data:/app/data:rw in docker-compose.yml) rather than
# docs/ or any other image-baked path, so entries survive a langgraph-app
# rebuild instead of vanishing with the old container layer.
_LOOM_TRAINING_EXAMPLES_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "loom_training_examples.jsonl"
)


def _record_loom_training_example(
    request_id: str, source: str, loom_result: dict, attempt: int, max_attempts: int,
) -> None:
    """Best-effort, append-only collection of real rust_loom_check outcomes.

    Only records a determinate verdict (compiles is not None, not timed out)
    -- a sandbox-unreachable/timeout result carries no training signal.
    `request_id` lets a later curation pass group every attempt for the same
    response and derive a (rejected, chosen) pair from a failing attempt
    followed by the eventually-accepted corrected one. Never raises: this is
    data collection, not a control path, and must never affect the actual
    merger retry logic it sits alongside.
    """
    if loom_result.get("compiles") is None or loom_result.get("timed_out"):
        return
    try:
        from datetime import datetime, timezone
        record = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "request_id": request_id,
            "attempt": attempt,
            "max_attempts": max_attempts,
            "source": source,
            "compiles": loom_result.get("compiles"),
            "passed": loom_result.get("passed"),
            "output_tail": (loom_result.get("output_tail") or "")[-2000:],
            "duration_ms": loom_result.get("duration_ms"),
        }
        os.makedirs(os.path.dirname(_LOOM_TRAINING_EXAMPLES_FILE), exist_ok=True)
        with open(_LOOM_TRAINING_EXAMPLES_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.debug("Failed recording loom training example (non-fatal): %s", exc)


async def _call_rust_compile_check(source: str) -> dict:
    """Call the rust_compile_check MCP precision tool directly via HTTP,
    bypassing the planner-mediated dispatch (mirrors graph/hypothesis_verifier.py's
    _call_sandbox pattern for python_sandbox). Fail-open: any sandbox/transport
    error returns compiles=None rather than raising, since this check is a
    quality improvement, not a hard gate, in this first increment.
    """
    try:
        async with httpx.AsyncClient(timeout=_RUST_COMPILE_CHECK_TIMEOUT_S) as client:
            resp = await client.post(
                f"{MCP_URL}/invoke",
                json={"tool": "rust_compile_check", "args": {"source": source}},
            )
            resp.raise_for_status()
            payload = resp.json()
        result_str = payload.get("result") if isinstance(payload, dict) else None
        if not result_str:
            return {"compiles": None}
        return json.loads(result_str)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.debug("rust_compile_check call failed (fail-open): %s", exc)
        return {"compiles": None}


async def _call_rust_loom_check(source: str) -> dict:
    """Call the rust_loom_check MCP precision tool directly via HTTP, same
    bypass pattern as _call_rust_compile_check above. Fail-open: any
    sandbox/transport error (including the sandbox being stopped entirely)
    returns compiles=None/passed=None rather than raising -- like the
    compile check, this is a quality improvement on top of an already-passed
    compile check, not a hard gate.
    """
    try:
        async with httpx.AsyncClient(timeout=_RUST_LOOM_CHECK_TIMEOUT_S) as client:
            resp = await client.post(
                f"{MCP_URL}/invoke",
                json={"tool": "rust_loom_check", "args": {"source": source}},
            )
            resp.raise_for_status()
            payload = resp.json()
        result_str = payload.get("result") if isinstance(payload, dict) else None
        if not result_str:
            return {"compiles": None, "passed": None}
        return json.loads(result_str)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.debug("rust_loom_check call failed (fail-open): %s", exc)
        return {"compiles": None, "passed": None}


async def merger_node(state_: AgentState):
    from datetime import datetime
    from parsing import _extract_usage, _parse_expert_confidence, _expert_category, _dedup_by_category, _collect_conflicts, _improvement_ratio
    from metrics import PROM_TOKENS
    from config import KAFKA_TOPIC_REQUESTS
    async def _ol_start(*a, **kw): return None   # lineage module removed in c2 clean-cut
    async def _ol_complete(*a, **kw): pass
    async def _ol_fail(*a, **kw): pass
    def dataset_response(*a, **kw): return {}
    _ol_merger_run = await _ol_start(
        "merger_node",
        extra_facets={"templateName": {"_producer": "https://github.com/h3rb3rn/moe-sovereign",
                                       "_schemaURL": "moe-sovereign://templateName",
                                       "name": state_.get("template_name", "default")}},
    )

    # Guard block: fixed refusal, no LLM call, no expert/judge pipeline involved
    if state_.get("guard_blocked"):
        logger.info("--- [NODE] MERGER (guard blocked, direct return) ---")
        await _report("🛡️ Merger: request blocked by safety filter")
        await _record_stage(state_.get("response_id", ""), "merger", "guard_shortcut")
        asyncio.create_task(_kafka_publish(KAFKA_TOPIC_REQUESTS, {
            "response_id":   state_.get("response_id", ""),
            "input":         state_["input"][:300],
            "guard_blocked": True,
            "guard_reason":  state_.get("guard_reason", ""),
            "ts":            datetime.now().isoformat(),
        }))
        await _ol_complete(_ol_merger_run, job_name="merger_node",
                           outputs=[dataset_response(state_.get("response_id", "guard"))])
        return {"final_response": state_.get("guard_response", "")}

    # Cache hit: direct answer, no LLM call needed
    if state_.get("cache_hit"):
        logger.info("--- [NODE] MERGER (cache hit, direct return) ---")
        await _report("💨 Merger: cached response delivered directly")
        await _record_stage(state_.get("response_id", ""), "merger", "cache_shortcut")
        asyncio.create_task(_kafka_publish(KAFKA_TOPIC_REQUESTS, {
            "response_id": state_.get("response_id", ""),
            "input":       state_["input"][:300],
            "cache_hit":   True,
            "ts":          datetime.now().isoformat(),
        }))
        await _ol_complete(_ol_merger_run, job_name="merger_node",
                           outputs=[dataset_response(state_.get("response_id", "cache"))])
        return {"final_response": state_.get("cached_facts", "")}

    logger.info("--- [NODE] MERGER & INGEST ---")
    await _report("🔀 Merger analyzing expert confidence...")
    await _record_stage(state_.get("response_id", ""), "merger", "started")

    _all_expert_raw = state_.get("expert_results") or []
    _ensemble_raw   = [r for r in _all_expert_raw if re.match(r'\[ENSEMBLE:', r)]
    _normal_raw     = [r for r in _all_expert_raw if not re.match(r'\[ENSEMBLE:', r)]
    expert_results  = _dedup_by_category(_normal_raw)
    ensemble_results = _ensemble_raw   # all ensemble results unfiltered to merger

    # Paraconsistent conflict detection: collect divergent expert outputs before
    # dedup discards them. Both propositions are preserved in conflict_registry
    # rather than silently overwritten — de Vries (2007), arXiv:0707.2161, §2.
    _new_conflicts = _collect_conflicts(_normal_raw)
    if _new_conflicts:
        _cats = sorted({c["category"] for c in _new_conflicts})
        logger.info(f"⚖️  Conflict registry: {len(_new_conflicts)} paraconsistent conflicts in {_cats}")
        await _report(f"⚖️ Paraconsistent conflicts detected: {', '.join(_cats)}")

    # ── Boundary contract check (Expert→Judge stage boundary) ─────────────────
    # This is a mandatory correctness boundary. Exporters remain best-effort,
    # but a missing/invalid contract must never turn validation into a no-op.
    from services.boundary_check import check_boundary as _check_boundary
    _boundary_valid_results = []
    for _bc_r in expert_results:
        if _bc_r and not _check_boundary(
            "expert_to_judge",
            {"content": _bc_r, "category": _expert_category(_bc_r)},
            request_id=state_.get("response_id", ""),
        ):
            _boundary_valid_results.append(_bc_r)
    expert_results = _boundary_valid_results

    web              = state_.get("web_research")      or ""
    cached           = state_.get("cached_facts")      or ""
    math_res         = state_.get("math_result")       or ""
    # Mandatory precision values are never exposed to a mutating LLM as free
    # text. The post-worker slot node replaces them with opaque markers that
    # only the deterministic post-critic binder may expand.
    mcp_res          = (
        state_.get("precision_prompt_projection") or ""
        if state_.get("required_precision_intents")
        else state_.get("mcp_result") or ""
    )
    graph_ctx        = state_.get("graph_context")     or ""
    reasoning        = state_.get("reasoning_trace")   or ""
    strategy_feedback = state_.get("strategy_feedback") or ""

    _SAFETY_CRITICAL_CATS = {"medical_consult", "legal_advisor"}

    # ── 3A: Trust-Score / Verification Substrate (TASK-10) ────────────────────
    _trust_score_result = None
    try:
        from services.trust_score import compute_trust_score, TrustVerdict
        _trust_score_result = compute_trust_score(dict(state_))
        logger.info(
            "🔍 Trust-Score: %.3f [%s]%s — %s",
            _trust_score_result.score,
            _trust_score_result.verdict,
            " HARD-BLOCK" if _trust_score_result.hard_blocked else "",
            _trust_score_result.reason,
        )
        if _trust_score_result.verdict == TrustVerdict.BLOCK or _trust_score_result.hard_blocked:
            from services.decision_log import log_decision, DecisionType
            log_decision(
                DecisionType.TRUST_BLOCK,
                state_.get("response_id", ""),
                rationale=f"Trust-Score blocked response: {_trust_score_result.reason}",
                metadata={"score": _trust_score_result.score, "factors": _trust_score_result.factors},
            )
    except Exception as _ts_e:
        logger.debug("Trust-Score skipped: %s", _ts_e)

    _trust_state = {}
    if _trust_score_result is not None:
        _trust_state = {
            "trust_score": _trust_score_result.score,
            "trust_verdict": _trust_score_result.verdict.value,
            "trust_factors": _trust_score_result.factors,
        }
        from services.request_snapshot import update_request_snapshot
        update_request_snapshot(
            state_.get("response_id", ""),
            trust_score=_trust_score_result.score,
            trust_verdict=_trust_score_result.verdict.value,
        )

    def _enforce_direct_response(response: str) -> tuple[str, list[dict]]:
        """Apply the same Constitution contract used by synthesized responses."""
        try:
            from services.constitution import enforce as _constitution_enforce

            enforced, violations = _constitution_enforce(response, dict(state_))
            return enforced, [
                {
                    "rule_id": violation.rule_id,
                    "on_violation": violation.on_violation,
                    "detail": violation.detail,
                }
                for violation in violations
            ]
        except Exception as exc:
            logger.debug("Direct-response Constitution enforcement skipped: %s", exc)
            return response, []

    # ── 3B: Confidence analysis (normal + ensemble results) ────────────────
    low_conf_critical = [
        r for r in (expert_results + ensemble_results)
        if _parse_expert_confidence(r) == "low"
        and _expert_category(r) in _SAFETY_CRITICAL_CATS
    ]
    if low_conf_critical:
        cats = sorted({_expert_category(r) for r in low_conf_critical})
        logger.info(f"⚠️ Low confidence in: {cats}")
        await _report(f"⚠️ Low confidence: {', '.join(cats)}")

    # ── Judge Refinement Loop: improve low-confidence expert responses ────────
    # Categories the judge had to step in on — collected for the judge-aware
    # Thompson reward signal recorded after the loop (see below).
    _judge_refined_cats: set = set()
    _deferred_corrections: list[dict] = list(
        (state_.get("response_commit_context") or {}).get("corrections") or []
    )
    # Cost-gate the refinement loop: simple queries get at most one round. Each
    # round costs 1 judge call + N expert re-invocations, so capping trivial/
    # moderate requests to a single pass saves LLM calls on the cheap paths
    # without hurting complex multi-task answers (which keep the full budget).
    _max_refine = JUDGE_REFINE_MAX_ROUNDS
    _is_trivial_direct = (
        state_.get("complexity_level") == "trivial"
        and bool(state_.get("trivial_fast_path"))
    )
    if (
        _is_trivial_direct
        and not expert_results
        and len(ensemble_results) == 1
    ):
        # A category with one ``forced`` model is emitted by expert_node with
        # an [ENSEMBLE:…] wrapper even though only one model actually ran.
        # For the verified trivial path this is still one answer, not a debate
        # requiring synthesis. Preserve multi-result ensembles unchanged.
        expert_results = [ensemble_results[0]]
        ensemble_results = []
    if _is_trivial_direct:
        # The planner already proved this is a context-free one-shot request.
        # Very short valid answers (for example "OK") intentionally fail the
        # generic 40-character confidence heuristic; escalating those through a
        # judge and expert retry defeats the latency-safe trivial contract.
        _max_refine = 0
    elif state_.get("complexity_level") in ("trivial", "moderate"):
        _max_refine = min(1, JUDGE_REFINE_MAX_ROUNDS)
    # Categories with an unresolved paraconsistent conflict (_new_conflicts,
    # collected above) get folded into this same refinement loop, for ANY
    # category -- not just _SAFETY_CRITICAL_CATS. Two experts disagreeing is
    # itself worth Judge attention regardless of domain: the chain shouldn't
    # end at "Judge observes and logs a dismissed conflict" when the exact
    # mechanism to feed a verdict back into a real re-generation already
    # exists (this loop). Previously, resolve_conflicts_node (which runs
    # AFTER this node has already synthesized final_response -- too late to
    # help) only arbitrated safety-critical conflicts and only ever recorded
    # the verdict as an audit trail, never regenerating anything; everything
    # else was silently dismissed ("Strategy C: no LLM cost warranted"),
    # which let contradicting expert answers erode Trust-Score round after
    # round with nothing ever correcting them.
    _pending_conflict_cats = {
        c["category"] for c in _new_conflicts if c.get("resolution") == "pending"
    }
    if _max_refine > 0 and expert_results:
        for _refine_round in range(_max_refine):
            low_conf_list = [r for r in expert_results if _parse_expert_confidence(r) == "low"]
            conflict_list = [
                r for r in expert_results
                if _expert_category(r) in _pending_conflict_cats and r not in low_conf_list
            ]
            refine_list = low_conf_list + conflict_list
            if not refine_list:
                break
            _judge_refined_cats.update(_expert_category(r) for r in refine_list)
            await _report(f"🔄 Refinement round {_refine_round + 1}/{_max_refine}: "
                          f"{len(low_conf_list)} low-confidence, {len(conflict_list)} conflicted experts")
            # Judge generates feedback — enriched with web/graph context
            _ctx_snippet = ""
            _jbudget = _judge_ctx_budget(state_.get("judge_num_ctx", 0))
            if web:
                _ctx_snippet += f"\nWEB CONTEXT (excerpt):\n{web[:_jbudget['web_context']]}"
            if graph_ctx:
                _ctx_snippet += f"\nGRAPH KNOWLEDGE (excerpt):\n{graph_ctx[:_jbudget['graph_context']]}"
            _conflict_cats_this_round = {_expert_category(r) for r in conflict_list}
            _conflict_section = ""
            if _conflict_cats_this_round:
                _conflict_parts = [
                    f"[{c['category']}] Two experts disagree:\n"
                    f"PROPOSITION A:\n{c['proposition_a']}\n\n"
                    f"PROPOSITION B:\n{c['proposition_b']}\n"
                    "Determine which approach is technically correct (or synthesize "
                    "the correct answer if both are partially right/wrong), and give "
                    "the expert concrete corrective guidance to produce the right "
                    "implementation."
                    for c in _new_conflicts
                    if c.get("resolution") == "pending" and c["category"] in _conflict_cats_this_round
                ]
                _conflict_section = (
                    "\n\nADDITIONALLY, arbitrate these expert disagreements and "
                    "include your arbitration guidance in the same [CATEGORY]: <...> "
                    "format below:\n\n" + "\n\n".join(_conflict_parts)
                )
            gap_prompt = (
                "Analyze these expert responses with CONFIDENCE: low and formulate "
                "concrete, specific improvement hints for each category (max. 3 sentences). "
                "Use available context to directly name missing facts:\n\n"
                + ("\n\n".join(low_conf_list) if low_conf_list else "(none)")
                + _conflict_section
                + _ctx_snippet
                + "\n\nFormat: [CATEGORY]: <improvement hints with concrete facts>"
            )
            try:
                await _report(f"🔄 Judge refinement prompt (round {_refine_round + 1}):\n{gap_prompt}")
                _gap_res = await _invoke_judge_with_retry(state_, gap_prompt)
                gap_feedback_text = _gap_res.content.strip()
                # Persist refinement reason in state for causal-path logging
                state_["judge_reason"] = gap_feedback_text[:500]
                state_["judge_refined"] = True
                await _report(f"🔄 Judge refinement response (round {_refine_round + 1}):\n{gap_feedback_text}")
            except Exception as _ge:
                logger.warning(f"⚠️ Refinement judge feedback round {_refine_round + 1}: {_ge}")
                break
            # Per low-confidence/conflicted category: re-invoke the best expert
            any_improvement = False
            new_expert_results = list(expert_results)
            for old_result in refine_list:
                _cat = _expert_category(old_result)
                # Extract category-specific feedback
                cat_feedback = gap_feedback_text
                for _line in gap_feedback_text.splitlines():
                    if _line.strip().startswith(f"[{_cat}]"):
                        cat_feedback = _line.split(":", 1)[-1].strip()
                        break
                refined = await _refine_expert_response(_cat, cat_feedback, state_)
                if not refined:
                    continue
                new_conf  = _parse_expert_confidence(refined)
                old_conf  = _parse_expert_confidence(old_result)
                ratio     = _improvement_ratio(old_result, refined)
                logger.info(f"🔄 Refinement [{_cat}]: {old_conf} → {new_conf}, Δ={ratio:.2f}")
                await _report(f"🔄 [{_cat}]: {old_conf} → {new_conf} (Δ{ratio:.0%})")
                if ratio >= JUDGE_REFINE_MIN_IMPROVEMENT:
                    _prefix = old_result.split("]:", 1)[0].lstrip("[")
                    new_expert_results = [
                        f"[{_prefix}]: {refined}" if r is old_result else r
                        for r in new_expert_results
                    ]
                    any_improvement = True
                    PROM_JUDGE_REFINED.labels(outcome="improved").inc()
                    if CORRECTION_MEMORY_ENABLED and state.graph_manager is not None:
                        _deferred_corrections.append({
                            "prompt": state_.get("input", "")[:500],
                            "wrong": old_result[:500],
                            "correct": refined[:500],
                            "category": _cat,
                            "source_model": state_.get("judge_model_override") or "",
                            "correction_source": "judge_refinement",
                            "tenant_id": ",".join(state_.get("tenant_ids", [])),
                        })
                    if _cat in _pending_conflict_cats:
                        # Mutating the dicts in _new_conflicts in place is
                        # sufficient: that same list object is returned
                        # verbatim as conflict_registry below, and
                        # resolve_conflicts_node only re-arbitrates entries
                        # still marked "pending".
                        for _c in _new_conflicts:
                            if _c.get("resolution") == "pending" and _c["category"] == _cat:
                                _c["resolution"] = "resolved"
                                _c["resolved_by"] = "merger_refine_arbitration"
                        _pending_conflict_cats.discard(_cat)
            expert_results = new_expert_results
            if not any_improvement:
                await _report(f"⏹️ Refinement stopped: no significant improvement "
                              f"(< {JUDGE_REFINE_MIN_IMPROVEMENT:.0%})")
                break

    # Expert rewards, retrieval-bandit rewards and policy-training records are
    # semantic learning signals. response_commit derives them from the frozen
    # state only after a final quality pass or HITL approval.

    await _report("🔀 Merger synthesizing final response...")

    # ── 3B: Confidence-aware merger instruction ────────────────────────────
    mode     = state_.get("mode", "default")
    mode_cfg = MODES.get(mode, MODES["default"])

    # Only include non-empty sections in the prompt
    # Guard: truncate very long inputs to prevent context overflow.
    # max_output is resolved dynamically (Redis → API → static table). ctx is resolved
    # via resolve_requested_ctx() — the same priority logic _judge_model_kw() uses to
    # build this call's actual num_ctx, NOT the model's currently-loaded /api/ps state
    # (which can be stale and cause spurious overflow warnings).
    _merger_overflow = False
    _merger_truncated = False
    _merger_judge_model   = state_.get("judge_model_override") or JUDGE_MODEL
    _merger_judge_url     = (state_.get("judge_url_override") or "").rstrip("/")
    _merger_judge_tok     = (state_.get("judge_token_override") or "ollama")
    _merger_judge_num_ctx = int(state_.get("judge_num_ctx") or 0)
    from context_budget import (get_model_max_output_async as _get_max_out_async,
                                resolve_requested_ctx,
                                MERGER_FIXED_TOKENS, MERGER_HEADROOM_TOKENS, CHARS_PER_TOKEN,
                                resolve_io_budget)
    _merger_ctx    = resolve_requested_ctx(_merger_judge_model, _merger_judge_num_ctx,
                                           JUDGE_NUM_CTX, label="synthesis")
    _merger_maxout = await _get_max_out_async(_merger_judge_model, _merger_judge_url,
                                              _merger_judge_tok, state.redis_client)
    # Code-heavy merger tasks (systems_programming/code_reviewer) routinely need
    # a full critique PLUS one or more corrected code blocks in one response.
    # Observed live: a 2048-token budget cut the judge off mid rewrite, leaving
    # an odd ``` count that the plausibility gate then rejected as
    # unclosed_code_block (services/quality_gate.py verify_response_plausibility)
    # -- not a prompt-wording issue, a genuine output-budget shortfall for this
    # task shape. resolve_io_budget() below still caps the final value against
    # the model's real context window, so this only widens headroom when there
    # is room to give.
    _merger_code_categories_present = (
        {_expert_category(r) for r in expert_results} & _RUST_COMPILE_CHECK_CATEGORIES
    )
    if _merger_code_categories_present:
        _merger_maxout = int(_merger_maxout * 1.75)
    if _merger_ctx > 0:
        _budget = resolve_io_budget(
            ctx_tokens=_merger_ctx, desired_max_tokens=_merger_maxout,
            static_overhead_tokens=MERGER_FIXED_TOKENS, chars_per_token=CHARS_PER_TOKEN,
            min_output_tokens=MERGER_HEADROOM_TOKENS, min_input_ratio=0.5,
        )
        if _budget["overflow"]:
            _merger_overflow = True
            logger.warning(
                "synthesis: PRE-FLIGHT merger overflow — ctx=%d, fixed=%d",
                _merger_ctx, MERGER_FIXED_TOKENS,
            )
            PROM_BUDGET_EXCEEDED.labels(
                user_id=state_.get("session_id", "unknown"), limit_type="merger_preflight"
            ).inc()
        
        # Recognize if Output Context Size is constrained/too small compared to desired maxout
        if _budget["max_output_tokens"] < _merger_maxout:
            _merger_overflow = True
            logger.warning(
                "synthesis: Output context budget downscaled from %d to %d (limited window)",
                _merger_maxout, _budget["max_output_tokens"]
            )
            
        _max_input_chars = _budget["avail_input_chars"]
        _query_in = state_["input"]
        
        # Apply filler word pruning first (content-preserving optimization)
        from context_budget import prune_filler_words
        _query_in = prune_filler_words(_query_in)
        
        if len(_query_in) > _max_input_chars:
            from context_budget import compress_prompt_to_fit
            _query_in = await compress_prompt_to_fit(
                _query_in, _max_input_chars,
                model=_merger_judge_model, url=_merger_judge_url, token=_merger_judge_tok
            )
            _merger_truncated = True
            await _report(f"⚠️ Input compressed/truncated to {_max_input_chars} chars (model ctx limit)")
    else:
        _query_in = state_["input"]
        from context_budget import prune_filler_words
        _query_in = prune_filler_words(_query_in)
    sections: List[str] = [f"REQUEST: {_query_in}"]
    if reasoning:
        sections.append(f"REASONING ANALYSIS:\n{reasoning}")
    if strategy_feedback:
        sections.append(f"STRATEGY REVIEW (structural, content-free):\n{strategy_feedback}")
    if graph_ctx:
        _gctx = graph_ctx
        # Compute per-template GraphRAG char budget: explicit override > auto from
        # judge model context window > global MAX_GRAPH_CONTEXT_CHARS.
        _judge_model = state_.get("judge_model_override") or JUDGE_MODEL
        _tpl_limit = state_.get("graphrag_max_chars", 0)
        # Use async context-window lookup (Redis-cache → Ollama → static table) for judge model
        from context_budget import get_model_ctx_async as _ctx_async_grag, graphrag_budget_chars
        _judge_url = (state_.get("judge_url_override") or "").rstrip("/")
        _judge_tok = (state_.get("judge_token_override") or "ollama")
        _judge_ctx = await _ctx_async_grag(model=_judge_model, base_url=_judge_url,
                                           token=_judge_tok, redis_client=state.redis_client)
        # Override static-table result with live value when available
        _effective_limit = graphrag_budget_chars(
            model=_judge_model,
            query_chars=len(state_.get("input", "")),
            override_chars=_tpl_limit,
        )
        # If async lookup found a larger (or known) context window, recompute
        if _judge_ctx > 0 and _tpl_limit <= 0:
            from context_budget import (MERGER_FIXED_TOKENS, CHARS_PER_TOKEN,
                                        MIN_GRAPHRAG_CHARS, get_model_max_output_async)
            _q_tok      = (len(state_.get("input", "")) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN
            _max_output = await get_model_max_output_async(
                _judge_model, _judge_url, _judge_tok, state.redis_client)
            _avail      = _judge_ctx - MERGER_FIXED_TOKENS - _max_output - _q_tok
            _effective_limit = max(MIN_GRAPHRAG_CHARS, _avail * CHARS_PER_TOKEN)
        # Final safety net: never exceed the global hard cap if set.
        if MAX_GRAPH_CONTEXT_CHARS > 0:
            _effective_limit = min(_effective_limit, MAX_GRAPH_CONTEXT_CHARS)
        _graph_raw_chars = len(_gctx)
        _compression_method = "none"
        if _effective_limit > 0 and _graph_raw_chars > _effective_limit:
            _merger_truncated = True
            threshold = _effective_limit * _GRAPH_COMPRESS_THRESHOLD_FACTOR
            if _graph_raw_chars > threshold and _GRAPH_COMPRESS_LLM_MODEL:
                # Very large context: attempt LLM-based semantic compression first
                _compressed = await _compress_graph_context_llm(
                    _gctx,
                    _effective_limit,
                    dict(state_),
                )
                if _compressed:
                    _gctx = _compressed
                    _compression_method = "llm"
                else:
                    _gctx = _rerank_graph_context(_gctx, _effective_limit)
                    _compression_method = "rerank"
            else:
                # Moderate overrun: reorder by confidence, preserve complete blocks
                _gctx = _rerank_graph_context(_gctx, _effective_limit)
                _compression_method = "rerank"
        logger.info(
            f"📊 GraphRAG compression: {_graph_raw_chars} → {len(_gctx)} chars "
            f"(method={_compression_method}, budget={_effective_limit})"
        )
        # Store compression telemetry in state for causal-path logging
        state_["graphrag_entities"] = state_.get("graphrag_entities") or []
        sections.append(f"STRUCTURED KNOWLEDGE (Ontology/Knowledge Graph):\n{_gctx}")
    if expert_results:
        # Dynamic per-expert truncation: budget scales with expert count so
        # multi-expert synthesis retains enough of each response to synthesise.
        # With 1 expert: 3500 chars. With 2: 2800 each. With 4+: 2000 each.
        # Floor is 2000 to keep merger token budget bounded.
        _n_experts       = len(expert_results)
        MAX_EXPERT_CHARS = max(2000, min(3500, 3500 - (_n_experts - 1) * 500))

        # Confidence-weighted synthesis (MoE architecture: Top-K → Weighted Combination).
        # Map confidence levels to weight tiers and sort experts high → low so the judge
        # reads the most reliable inputs first (primacy bias).
        _CONF_WEIGHT = {
            "high":   ("PRIMARY",    "★★★"),
            "medium": ("SUPPORTING", "★★☆"),
            "low":    ("BACKGROUND", "★☆☆"),
        }
        _CONF_ORDER = {"high": 0, "medium": 1, "low": 2}

        expert_results_weighted = sorted(
            expert_results,
            key=lambda r: _CONF_ORDER.get(_parse_expert_confidence(r), 1),
        )

        trimmed = []
        weight_rows = []
        for er in expert_results_weighted:
            conf              = _parse_expert_confidence(er)
            weight_label, stars = _CONF_WEIGHT.get(conf, ("SUPPORTING", "★★☆"))
            cat               = _expert_category(er) or "general"
            weight_rows.append(
                f"  {stars} [{cat:<22}]  CONFIDENCE: {conf.upper():<6}  →  {weight_label}"
            )
            if len(er) > MAX_EXPERT_CHARS:
                trimmed.append(er[:MAX_EXPERT_CHARS] + "\n[...truncated for merger efficiency]")
                _merger_truncated = True
            else:
                trimmed.append(er)

        if _n_experts > 1:
            expert_header = (
                f"EXPERT RESPONSES ({_n_experts} domains — confidence-weighted synthesis):\n"
                + "\n".join(weight_rows) + "\n"
                "Synthesis rule: PRIMARY findings anchor the answer. "
                "SUPPORTING findings refine or extend it. "
                "BACKGROUND findings fill gaps only where PRIMARY/SUPPORTING are silent.\n"
            )
        else:
            expert_header = "EXPERT RESPONSES:\n"
        sections.append(expert_header + "\n\n".join(trimmed))
    if ensemble_results:
        sections.append(
            "ENSEMBLE ANALYSIS (multiple models from different providers, run in parallel with identical prompt — "
            "treat all perspectives equally, highlight commonalities, "
            "explicitly name and classify contradictions):\n" + "\n\n".join(ensemble_results)
        )
    if mcp_res:
        sections.append(f"PRECISION CALCULATIONS (MCP — exact, authoritative):\n{mcp_res}")
    if math_res:
        sections.append(f"MATH (SymPy):\n{math_res}")
    if web:
        # Adaptive web-research compression: block/char limits scale with the
        # judge model's context window so small fallback models (gemma4:31b 8K)
        # get tighter limits than large models (gpt-120B 128K).
        from context_budget import web_research_budget
        _judge_model_for_web = state_.get("judge_model_override") or JUDGE_MODEL
        MAX_WEB_BLOCKS, MAX_BLOCK_CHARS = web_research_budget(
            model=_judge_model_for_web,
            query_chars=len(state_.get("input", "")),
            graphrag_chars_used=len(_gctx) if "_gctx" in dir() else 0,
        )
        web_blocks = [b.strip() for b in re.split(r'\n\[(?:Research|Recherche|\d+)', web) if b.strip()]
        _web_trunc = False
        if len(web_blocks) > MAX_WEB_BLOCKS:
            _web_trunc = True
        for block in web_blocks[:MAX_WEB_BLOCKS]:
            if len(block) > MAX_BLOCK_CHARS:
                _web_trunc = True
        if _web_trunc:
            _merger_truncated = True
        compressed_web = "\n\n".join(
            block[:MAX_BLOCK_CHARS] + ("…" if len(block) > MAX_BLOCK_CHARS else "")
            for block in web_blocks[:MAX_WEB_BLOCKS]
        )
        if not compressed_web:
            compressed_web = web[:MAX_BLOCK_CHARS * 2]  # fallback if split produced nothing
        sections.append(f"WEB RESEARCH (current, with sources):\n{compressed_web}")
    # Code-duplication guard: LLMs (especially gpt-4.1) interpret "integrate the strongest
    # insights" literally when they receive two similar code implementations — they interleave
    # both character-by-character, producing doubled output. Guard fires when any primary source
    # (expert_results OR ensemble_results) contains code AND a secondary source does too.
    _CODE_MARKERS = ("```", "<!DOCTYPE", "<html", "def ", "function ", "class ", "import ", "setInterval")
    _primary_sources = list(expert_results) + list(ensemble_results)
    _primary_has_code = any(any(m in s for m in _CODE_MARKERS) for s in _primary_sources)

    if cached:
        _cached_has_code = any(m in cached for m in _CODE_MARKERS)
        if _primary_has_code and _cached_has_code:
            logger.info("🛡️ PRIOR KNOWLEDGE suppressed: primary source + cache both contain code (prevents judge interleaving)")
            await _report("🛡️ Prior knowledge suppressed (code duplication guard)")
        else:
            _cached_limit = _judge_ctx_budget(state_.get('judge_num_ctx', 0))['cached_prior']
            if len(cached) > _cached_limit:
                _merger_truncated = True
            sections.append(f"PRIOR KNOWLEDGE (Cache):\n{cached[:_cached_limit]}")
    soft_examples = state_.get("soft_cache_examples") or ""
    if soft_examples:
        _soft_has_code = any(m in soft_examples for m in _CODE_MARKERS)
        if _primary_has_code and _soft_has_code:
            logger.info("🛡️ Soft-cache suppressed: primary source + cached snippet both contain code (prevents judge interleaving)")
            await _report("🛡️ Soft-cache examples suppressed (code duplication guard)")
        else:
            sections.append(f"SIMILAR PREVIOUS ANSWERS (few-shot orientation, do not use as fact):\n{soft_examples}")

    conf_note = ""
    if low_conf_critical and mode != "code":  # Code mode does not need caveats
        cats_str = ", ".join(sorted({_expert_category(r) for r in low_conf_critical}))
        conf_note = (
            f"\nWARNING: Expert categories [{cats_str}] reported CONFIDENCE: low. "
            "Explicitly point out this uncertainty in the response. "
            "Recommend professional advice (doctor/lawyer). "
            "Prioritize web research data over low-confidence expert statements."
        )

    _custom_judge = (state_.get("judge_prompt") or "").strip()
    merger_prefix = _custom_judge if _custom_judge else mode_cfg["merger_prefix"]
    _behavioral = (state_.get("behavioral_directives") or "").strip()
    if _behavioral:
        merger_prefix = f"MANDATORY RESPONSE DIRECTIVES (override all other instructions):\n{_behavioral}\n\n{merger_prefix}"
    _has_graph_ctx = bool(graph_ctx and graph_ctx.strip())
    prompt = (
        merger_prefix
        + conf_note + "\n\n"
        + "\n\n---\n\n".join(sections)
        + SYNTHESIS_PERSISTENCE_INSTRUCTION
        + (PROVENANCE_INSTRUCTION if _has_graph_ctx else "")
    )
    # Optimize final prompt token consumption without content loss by removing filler words
    from context_budget import prune_filler_words
    prompt = prune_filler_words(prompt)
    if state_.get("precision_prompt_projection"):
        # Repeat the value-free contract at the final instruction position.
        # In live mixed-path tests some judge models followed a later response
        # directive but omitted a marker that appeared only in the evidence
        # section.  The model still never sees the underlying value; the
        # deterministic post-critic binder remains the sole value authority.
        prompt += (
            "\n\nFINAL NON-NEGOTIABLE PRECISION OUTPUT CONTRACT:\n"
            "Copy every [[MOE_PRECISION:...]] marker from VERIFIED PRECISION "
            "FACT SLOTS byte-for-byte exactly once and in listed order into "
            "the final answer. Put each marker alone on its own line without "
            "prefix, suffix, numbering or punctuation. Do not spell out, infer, "
            "translate, alter or omit any represented value. Do not add a "
            "second summary, heading value, date, time, number or unit for a "
            "labelled precision item anywhere else in the answer; such a "
            "duplicate claim invalidates the whole response. A deterministic "
            "binder will replace the complete marker line after all model "
            "processing."
        )

    # Check if prompt length exceeds allowed input characters (danger of squeezing output room)
    if _merger_ctx > 0:
        _max_allowed_input_chars = (_merger_ctx - _budget["max_output_tokens"]) * CHARS_PER_TOKEN
        if len(prompt) > _max_allowed_input_chars:
            _merger_overflow = True
            logger.warning(
                "synthesis: Assembled prompt length %d exceeds max allowed input chars %d",
                len(prompt), _max_allowed_input_chars
            )

    # Inject output skill formatting instructions if planner suggested one.
    # Guard: suppress skill body when BOTH primary expert output AND the skill
    # template contain code markers — the judge LLM otherwise interleaves the
    # expert's code with identical patterns from the skill template, producing
    # visible duplication (e.g. repeated bash find commands). The skill body is
    # formatting guidance only; if the expert already produced code the format
    # hint is redundant and harmful.
    _skill_body = state_.get("output_skill_body", "")
    if _skill_body:
        _skill_has_code = any(m in _skill_body for m in _CODE_MARKERS)
        if _primary_has_code and _skill_has_code:
            logger.info("🛡️ Skill body suppressed: expert output + skill template both contain code (prevents judge interleaving)")
        else:
            prompt += (
                "\n\n--- OUTPUT FORMATTING SKILL ---\n"
                "The planner selected a specific output format for this response. "
                "Follow these formatting instructions:\n\n"
                + _skill_body[:3000]
            )

    # Determine expert domain early — used for both ChromaDB metadata and Kafka ingest payload
    _plan_cats_early = [t.get("category", "") for t in state_.get("plan", []) if isinstance(t, dict)]
    _expert_domain = next(
        (c for c in ("medical_consult", "legal_advisor", "technical_support") if c in _plan_cats_early),
        _plan_cats_early[0] if _plan_cats_early else "general",
    )

    # A bounded mixed request does not need a mutating merger pass when every
    # precision item is already represented by an opaque evidence slot and the
    # sole remaining item has one high-confidence expert result.  Compose the
    # two channels structurally; the deterministic binder still runs only after
    # the scoped critic and remains the sole authority that reveals fact values.
    if (
        state_.get("precision_prompt_projection")
        and not ensemble_results
        and not web
        and not math_res
        and not graph_ctx
        and not reasoning
        and not state_.get("output_skill_body")
    ):
        from services.precision_response import compose_mixed_precision_candidate

        _hybrid = compose_mixed_precision_candidate(dict(state_), expert_results)
        if _hybrid:
            _hybrid_response, _hybrid_body, _hybrid_task = _hybrid
            _hybrid_response, _hybrid_violations = _enforce_direct_response(
                _hybrid_response
            )
            logger.info(
                "⚙️ Precision hybrid composer: %d slot(s) + one scoped expert",
                len(state_.get("precision_fact_slots") or []),
            )
            await _report("⚙️ Precision hybrid response composed without merger mutation")
            await _record_stage(
                state_.get("response_id", ""),
                "merger",
                "precision_hybrid",
                "one_scoped_expert",
            )
            await _ol_complete(
                _ol_merger_run,
                job_name="merger_node",
                outputs=[dataset_response(state_.get("response_id", "precision-hybrid"))],
            )
            return {
                "final_response": _hybrid_response,
                "precision_hybrid_composed": True,
                "precision_hybrid_expert_body": _hybrid_body,
                "precision_hybrid_expert_task": _hybrid_task,
                "precision_hybrid_expert_confidence": (
                    _parse_expert_confidence(expert_results[0])
                ),
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "constitution_violations": _hybrid_violations,
                "response_commit_context": {
                    "fast_path": True,
                    "precision_hybrid": True,
                    "judge_refined_cats": sorted(_judge_refined_cats),
                    "corrections": _deferred_corrections,
                },
                **_trust_state,
            }

    # ── Fast path: single high-confidence expert, no additional context ─────────
    _single_expert_modes = ("default", "concise", "auto")
    logger.info(
        "⚡ Direct-response gate: trivial_fast=%s experts=%d ensemble=%d "
        "aux_context=%s mode=%s",
        _is_trivial_direct,
        len(expert_results),
        len(ensemble_results),
        bool(web or mcp_res or math_res or graph_ctx),
        mode,
    )
    if (len(expert_results) == 1
            and not ensemble_results
            and not web and not mcp_res and not math_res and not graph_ctx
            and (
                _parse_expert_confidence(expert_results[0]) == "high"
                or _is_trivial_direct
            )
            and mode in _single_expert_modes):
        _raw_fp = re.sub(r'^\[[^\]]+\]:\s*', '', expert_results[0])
        _details_m = re.search(r'DETAILS:\n?(.*)', _raw_fp, re.DOTALL)
        fast_resp = _details_m.group(1).strip() if _details_m else _raw_fp.strip()
        fast_resp, _fast_constitution_violations = _enforce_direct_response(
            fast_resp
        )
        logger.info(
            "⚡ Fast-Path: verified single expert → direct response (%d chars)",
            len(fast_resp),
        )
        await _report(
            f"⚡ Fast-Path: verified single expert ({len(fast_resp)} chars)"
        )
        await _record_stage(
            state_.get("response_id", ""),
            "merger",
            "fast_path",
            "verified_single_expert",
        )
        # Semantic writes are deferred to response_commit after quality pass.
        asyncio.create_task(_kafka_publish(KAFKA_TOPIC_REQUESTS, {
            "response_id": state_.get("response_id", ""),
            "input":       state_["input"][:300],
            "fast_path":   True,
            "ts":          datetime.now().isoformat(),
        }))
        await _ol_complete(_ol_merger_run, job_name="merger_node",
                           outputs=[dataset_response(state_.get("response_id", "fast"))])
        return {
            "final_response": fast_resp,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "constitution_violations": _fast_constitution_violations,
            "response_commit_context": {"fast_path": True},
            **_trust_state,
        }

    # Judge gate: when all expert answers agree (or only one exists) and no
    # auxiliary context needs merging, the judge call adds tokens without
    # discriminative power — take the consensus answer directly.
    # Flag: MOE_JUDGE_GATE=1 (services/judge_gate.py).
    if not ensemble_results and not web and not mcp_res and not math_res and not graph_ctx:
        from services.judge_gate import should_skip_judge as _sj_gate
        _gate_inputs = [re.sub(r'^\[[^\]]+\]:\s*', '', r or '') for r in expert_results]
        _g_skip, _g_reason, _g_idx = _sj_gate(_gate_inputs)
        if _g_skip:
            _g_raw = _gate_inputs[_g_idx]
            _g_det = re.search(r'DETAILS:\n?(.*)', _g_raw, re.DOTALL)
            _g_resp = (_g_det.group(1).strip() if _g_det else _g_raw.strip())
            if _g_resp:
                _g_resp, _gate_constitution_violations = (
                    _enforce_direct_response(_g_resp)
                )
                logger.info("⚡ judge_gate: skipping merger judge (%s)", _g_reason)
                await _report(f"⚡ Judge-Gate: {_g_reason} — Merger-Judge übersprungen")
                return {
                    "final_response": _g_resp,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "constitution_violations": _gate_constitution_violations,
                    "response_commit_context": {
                        "fast_path": True,
                        "judge_refined_cats": sorted(_judge_refined_cats),
                        "corrections": _deferred_corrections,
                    },
                    **_trust_state,
                }

    def _validated_degraded_candidate(reason: str) -> Optional[dict]:
        """Return a transparent executor-output fallback only for a complete plan."""
        from services.quality_gate import incomplete_plan_tasks

        if incomplete_plan_tasks(dict(state_)):
            return None
        plan_categories = {
            str(task.get("category") or "")
            for task in (state_.get("plan") or [])
            if isinstance(task, dict)
        }
        if plan_categories & {"medical_consult", "legal_advisor"}:
            return None

        candidate_parts = [
            part.strip()
            for part in (
                mcp_res,
                math_res,
                web,
                "\n\n".join(expert_results + ensemble_results),
            )
            if part and part.strip()
        ]
        if not candidate_parts:
            return None
        candidate = (
            "[Degraded result: synthesis budget exhausted; verified executor "
            "outputs are shown without an additional merger pass.]\n\n"
            + "\n\n".join(candidate_parts)
        )
        candidate, violations = _enforce_direct_response(candidate)
        if any(
            str(v.get("on_violation") or "").lower() == "block"
            for v in violations
        ):
            return None
        return {
            "final_response": candidate,
            "candidate_status": "degraded",
            "candidate_reason": reason,
            "constitution_violations": violations,
            "response_commit_context": {
                "fast_path": False,
                "judge_refined_cats": sorted(_judge_refined_cats),
                "corrections": _deferred_corrections,
            },
            **_trust_state,
        }

    _minimum_merger_budget = float(
        os.getenv("MIN_MERGER_REMAINING_SECONDS", "90")
    )
    _remaining_for_merger = remaining_timeout(
        state_,
        10**9,
        stage="merger_budget_gate",
        reserve_seconds=0.0,
    )
    if _remaining_for_merger < _minimum_merger_budget:
        _degraded = _validated_degraded_candidate(
            "insufficient_merger_budget"
        )
        if _degraded:
            logger.warning(
                "Merger skipped with %.1fs remaining (< %.1fs); "
                "returning validated degraded candidate",
                _remaining_for_merger,
                _minimum_merger_budget,
            )
            await _record_stage(
                state_.get("response_id", ""),
                "merger",
                "degraded_candidate",
                f"remaining={_remaining_for_merger:.1f}s",
            )
            return _degraded
        raise RequestDeadlineExceeded(
            "merger: insufficient budget and no valid degraded candidate"
        )

    await _report(f"🔀 Merger prompt ({len(prompt)} chars):\n{prompt}")
    from services.structured_failure import (
        RecoveryAction as _RecoveryAction,
        build_failure as _build_failure,
        resolve_retry_model as _resolve_retry_model,
    )
    _merger_structured_state: dict = {}
    _structured_max_retries = max(
        0, int(os.getenv("STRUCTURED_FAILURE_MAX_RETRIES", "2"))
    )
    _structured_fallback_model = os.getenv(
        "STRUCTURED_FAILURE_FALLBACK_MODEL", ""
    ).strip()
    _structured_attempts = (
        1 + _structured_max_retries + bool(_structured_fallback_model)
    )
    res = None
    _last_judge_error: Optional[Exception] = None
    _retry_model_override = ""
    _current_prompt = prompt
    _code_categories_present = {_expert_category(r) for r in expert_results} & _RUST_COMPILE_CHECK_CATEGORIES
    for _sf_attempt in range(_structured_attempts):
        _attempt_state = state_
        _using_fallback = bool(_retry_model_override)
        if _retry_model_override:
            _attempt_state = {
                **dict(state_),
                "judge_model_override": _retry_model_override,
            }
        try:
            res = await _invoke_judge_with_retry(
                _attempt_state,
                _current_prompt,
                max_retries=1,
                temperature=state_.get("query_temperature"),
                # Discourage degenerate repetition loops: observed live, the
                # merger synthesis call fell into repeating a single line
                # ("// I will output the SPSC code.") dozens of times instead
                # of emitting the actual answer, cutting the response off
                # mid code-fence. repeat_last_n widens the lookback window so
                # a repeated multi-token phrase (not just a single token) is
                # penalized too.
                # 1.3 was too aggressive: on a code-generation task, observed
                # live (4 separate runs) to push the model into a different
                # degenerate mode instead -- a multi-thousand-token chain of
                # loosely associated, topically unrelated words that reads as
                # fluent prose and produces zero actual code, one run growing
                # past 22k tokens before being manually aborted. Lowered to
                # 1.15, still enough to suppress verbatim repetition without
                # penalizing recently-used *concepts* so hard that the model
                # is forced to keep hunting for novel (unrelated) vocabulary.
                repeat_penalty=1.15,
                repeat_last_n=256,
            )
            from services.quality_gate import verify_response_plausibility
            _plausibility = verify_response_plausibility(res.content or "", task_text=state_.get("input"))
            if _plausibility["plausible"]:
                # Deterministic ground-truth check on top of the plausibility
                # heuristic: for code-generation categories, actually
                # type/borrow-check any Rust code fence via the isolated
                # rust_compile_check sandbox, instead of relying solely on
                # LLM self-review to catch lifetime/ownership/interior-
                # mutability defects (see docs/experiments/
                # lumig_posttraining_candidates.md for the motivating,
                # repeatedly-observed evidence). Fail-open on sandbox errors
                # (compiles is None) -- this is a quality improvement, not a
                # hard gate, in this first increment.
                _rust_match = _RUST_CODE_FENCE_RE.search(res.content or "") if _code_categories_present else None
                if _rust_match:
                    _compile_result = await _call_rust_compile_check(_rust_match.group(1))
                    if _compile_result.get("compiles") is False:
                        _diag_lines = [
                            f"  line {d.get('line')}: {d.get('message')}"
                            for d in (_compile_result.get("diagnostics") or [])[:10]
                        ]
                        logger.warning(
                            "Merger synthesis attempt %d/%d: Rust code fence does not compile:\n%s",
                            _sf_attempt + 1, _structured_attempts, "\n".join(_diag_lines),
                        )
                        _last_judge_error = RuntimeError("rust_compile_check: does not compile")
                        if _sf_attempt + 1 < _structured_attempts:
                            _current_prompt = (
                                prompt
                                + "\n\nYour previous answer's Rust code does not compile. "
                                "Fix these exact compiler errors and provide a corrected, "
                                "complete answer:\n" + "\n".join(_diag_lines)
                            )
                            continue
                        # Attempts exhausted -- fall through and keep the last
                        # (non-compiling) result, same policy as the
                        # plausibility check below.
                    elif (
                        _code_categories_present & _RUST_LOOM_CHECK_CATEGORIES
                        and _RUST_LOOM_MARKER_RE.search(_rust_match.group(1))
                    ):
                        # Compiled cleanly and is already written as a loom
                        # test -- actually run it to catch a memory-ordering
                        # data race, which compiling alone cannot (see
                        # docs/experiments/lumig_posttraining_candidates.md).
                        _loom_result = await _call_rust_loom_check(_rust_match.group(1))
                        _record_loom_training_example(
                            state_.get("response_id", ""), _rust_match.group(1),
                            _loom_result, _sf_attempt + 1, _structured_attempts,
                        )
                        if _loom_result.get("passed") is False:
                            logger.warning(
                                "Merger synthesis attempt %d/%d: loom found a concurrency violation:\n%s",
                                _sf_attempt + 1, _structured_attempts,
                                (_loom_result.get("output_tail") or "")[-1500:],
                            )
                            _last_judge_error = RuntimeError("rust_loom_check: concurrency violation found")
                            if _sf_attempt + 1 < _structured_attempts:
                                _current_prompt = (
                                    prompt
                                    + "\n\nYour previous answer's Rust code compiles but Loom found a "
                                    "concurrency/memory-ordering violation when actually running it. "
                                    "Fix the synchronization and provide a corrected, complete answer:\n"
                                    + (_loom_result.get("output_tail") or "")[-1500:]
                                )
                                continue
                            # Attempts exhausted -- fall through and keep the
                            # last (loom-failing) result, same policy as above.
                break
            logger.warning(
                "Merger synthesis attempt %d/%d failed plausibility check: %s",
                _sf_attempt + 1, _structured_attempts, _plausibility["reason"],
            )
            _last_judge_error = RuntimeError(
                f"implausible response: {_plausibility['reason']}"
            )
            if _sf_attempt + 1 >= _structured_attempts:
                # Attempts exhausted -- keep the last (implausible) result
                # rather than discarding it silently. Downstream checks
                # (quality_gate_node's own plausibility gate, Constitution
                # enforcement) still see it and can reject it properly;
                # this loop's job is only to reduce how OFTEN that happens,
                # not to guarantee it never does.
                break
            continue
        except RequestDeadlineExceeded:
            _degraded = _validated_degraded_candidate(
                "merger_deadline_exceeded"
            )
            if _degraded:
                await _record_stage(
                    state_.get("response_id", ""),
                    "merger",
                    "degraded_candidate",
                    "judge deadline exceeded",
                )
                return _degraded
            raise
        except Exception as exc:
            _last_judge_error = exc
            failure = _build_failure(
                exc,
                model=(
                    _structured_fallback_model if _using_fallback
                    else (state_.get("judge_model_override") or JUDGE_MODEL)
                ),
                stage="synthesis",
                fallback_model=_structured_fallback_model,
                retry_round=_sf_attempt + 1,
            )
            _merger_structured_state = {
                "structured_failure": failure.as_dict(),
                "structured_failure_round": _sf_attempt + 1,
            }
            if _sf_attempt + 1 < _structured_attempts:
                _next_action = (
                    _RecoveryAction.RETRY_FALLBACK
                    if _structured_fallback_model
                    and _sf_attempt >= _structured_max_retries
                    else _RecoveryAction.RETRY_SAME
                )
                _next_model = _resolve_retry_model(failure, _next_action)
                _retry_model_override = (
                    _next_model
                    if _next_model != (
                        state_.get("judge_model_override") or JUDGE_MODEL
                    )
                    else ""
                )
                logger.warning(
                    "Merger transport recovery round %d/%d (%s), next model=%s",
                    _sf_attempt + 1,
                    _structured_attempts,
                    failure.failure_kind.value,
                    _next_model,
                )

    if res is None:
        e = _last_judge_error or RuntimeError("judge returned no result")
        logger.error("❌ Merger Judge LLM recovery exhausted: %s", e)
        await _report(f"❌ Merger: Judge LLM unreachable ({e})")
        fallback = "\n\n".join(s for s in sections[1:] if s)  # raw sections as emergency response
        await _ol_fail(_ol_merger_run, job_name="merger_node", error=str(e))
        try:
            from services.cascade import (
                CascadeEvent,
                CascadeType,
                emit_cascade,
            )
            emit_cascade(
                CascadeEvent(
                    CascadeType.SPEC_GAP,
                    f"Judge structured recovery failed: {e}",
                    "retry with a schema-capable judge model",
                ),
                request_id=state_.get("response_id", ""),
            )
        except Exception as cascade_error:
            logger.debug("Judge failure cascade skipped: %s", cascade_error)
        return {
            "final_response": fallback or "Error: Merger could not generate a response.",
            "response_commit_context": {
                "fast_path": False,
                "judge_refined_cats": sorted(_judge_refined_cats),
                "corrections": _deferred_corrections,
            },
            **_merger_structured_state,
        }
    # Capture usage before wrapping the cleaned response. The previous wrapper
    # retained only ``content`` and silently discarded the native Ollama
    # ``usage_metadata``, so successful API responses under-reported every
    # merger/judge token.
    merger_usage = _extract_usage(res)
    # Strip thinking traces before using judge output. Thinking-mode judges
    # (qwen3.6:35b) emit <think>…</think> blocks that must not appear in the
    # final response or pollute confidence checks and SYNTH parsing.
    _judge_raw = re.sub(r'<think>.*?</think>', '', res.content, flags=re.DOTALL).strip()
    # Wrap in a simple object so downstream code can use .content uniformly.
    class _StrResult:
        def __init__(self, s): self.content = s
    res = _StrResult(_judge_raw)
    await _report(f"🔀 Merger response ({len(res.content)} chars):\n{res.content}")
    _uid = state_.get("user_id", "anon")
    PROM_TOKENS.labels(model=JUDGE_MODEL, token_type="prompt",      node="merger", user_id=_uid).inc(merger_usage.get("prompt_tokens", 0))
    PROM_TOKENS.labels(model=JUDGE_MODEL, token_type="completion",  node="merger", user_id=_uid).inc(merger_usage.get("completion_tokens", 0))
    _judge_failed = (not res.content.strip() or
                     res.content.startswith("[Judge unavailable"))
    if _judge_failed:
        logger.error("❌ Merger: Judge LLM returned empty/error response (VRAM/OOM?)")
        await _report("❌ Merger: empty or failed answer from judge — possible VRAM exhaustion")
        # Best expert response as fallback
        best = next((r for r in expert_results if _parse_expert_confidence(r) == "high"), None) \
               or (expert_results[0] if expert_results else None)
        fallback = best or "No answer available — please try again."
        await _ol_fail(_ol_merger_run, job_name="merger_node", error="judge_empty_or_error")
        return {
            "final_response": fallback,
            "response_commit_context": {
                "fast_path": False,
                "judge_refined_cats": sorted(_judge_refined_cats),
                "corrections": _deferred_corrections,
            },
            **merger_usage,
            **_merger_structured_state,
        }
    await _report(f"✅ Response complete ({len(res.content)} chars)")
    await _record_stage(state_.get("response_id", ""), "merger", "done")

    # Parse and strip any SYNTHESIS_INSIGHT block from the LLM output.
    # The clean content is shown to the user; the insight is persisted to Neo4j separately.
    _SYNTH_RE = re.compile(r"<SYNTHESIS_INSIGHT>(.*?)</SYNTHESIS_INSIGHT>", re.DOTALL)
    _synth_match = _SYNTH_RE.search(res.content)
    _synthesis_payload = None
    if _synth_match:
        try:
            _synthesis_payload = json.loads(_synth_match.group(1).strip())
        except (json.JSONDecodeError, ValueError) as initial_parse_error:
            # The answer body remains usable, but the persistent synthesis
            # record has a strict JSON contract. Repair it with bounded retries
            # and checkpoint every failure round.
            _invalid_synthesis = _synth_match.group(1).strip()
            _repair_error: Exception = initial_parse_error
            _repair_attempts = _structured_max_retries + bool(
                _structured_fallback_model
            )
            for _repair_round in range(_repair_attempts):
                _repair_fallback = (
                    bool(_structured_fallback_model)
                    and _repair_round >= _structured_max_retries
                )
                _repair_state = state_
                if _repair_fallback:
                    _repair_state = {
                        **dict(state_),
                        "judge_model_override": _structured_fallback_model,
                    }
                failure = _build_failure(
                    _repair_error,
                    model=(
                        _structured_fallback_model if _repair_fallback
                        else (state_.get("judge_model_override") or JUDGE_MODEL)
                    ),
                    stage="synthesis_insight",
                    fallback_model=_structured_fallback_model,
                    raw_text=_invalid_synthesis,
                    retry_round=_repair_round + 1,
                )
                _merger_structured_state = {
                    "structured_failure": failure.as_dict(),
                    "structured_failure_round": _repair_round + 1,
                }
                try:
                    repair_res = await _invoke_judge_with_retry(
                        _repair_state,
                        (
                            "Repair the following malformed JSON. Return only "
                            "one valid JSON object, without markdown or prose:\n"
                            f"{_invalid_synthesis[:1600]}"
                        ),
                        max_retries=1,
                        temperature=0.0,
                    )
                    repaired_text = re.sub(
                        r"^```(?:json)?\s*|\s*```$",
                        "",
                        (repair_res.content or "").strip(),
                        flags=re.IGNORECASE,
                    )
                    _synthesis_payload = json.loads(repaired_text)
                    _merger_structured_state = {
                        "structured_failure": {},
                        "structured_failure_round": _repair_round + 1,
                    }
                    break
                except Exception as repair_error:
                    _repair_error = repair_error

            if _synthesis_payload is None:
                try:
                    from services.cascade import (
                        CascadeEvent,
                        CascadeType,
                        emit_cascade,
                    )
                    emit_cascade(
                        CascadeEvent(
                            CascadeType.SPEC_GAP,
                            f"Synthesis insight JSON invalid: {_repair_error}",
                            "repair or omit the structured synthesis block",
                        ),
                        request_id=state_.get("response_id", ""),
                    )
                except Exception as cascade_error:
                    logger.debug(
                        "Synthesis parse cascade skipped: %s",
                        cascade_error,
                    )
        res_content_clean = _SYNTH_RE.sub("", res.content).rstrip()
    else:
        res_content_clean = res.content

    # Prepend context warning/compression banner if context limit thresholds were hit
    _warnings = []
    if _merger_overflow:
        _warnings.append(
            "⚠️ **Context Window Alert:** The available model context window is heavily constrained relative to the input prompt size. This limits output generation room and may result in a truncated or incomplete response."
        )
    if _merger_truncated:
        _warnings.append(
            "⚠️ **Prompt Compressed:** The input prompt exceeded context budget limits. To fit the context window, irrelevant filler words were pruned, and some input details, search results, or history may have been truncated or summarized."
        )

    if _warnings:
        warning_banner = "> [!WARNING]\n" + "\n".join(f"> * {w}" for w in _warnings) + "\n\n"
        res_content_clean = warning_banner + res_content_clean

    # ── Provenance tag extraction ──────────────────────────────────────────
    _REF_RE = re.compile(r'\[REF:([^\]]+)\]')
    _ref_matches = _REF_RE.findall(res_content_clean)
    _provenance_sources = []
    if _ref_matches:
        for ref_name in dict.fromkeys(_ref_matches):  # deduplicate, preserve order
            _provenance_sources.append({"type": "neo4j", "label": ref_name.strip()})
        # Strip REF tags from content for clean output
        res_content_clean = _REF_RE.sub('', res_content_clean).strip()

    # Strip internal merger format headers that should not reach the user
    _INTERNAL_HEADERS_RE = re.compile(
        r'^(Key findings from each expert role:|Expert consensus:|## Expert Analysis|'
        r'\[EXPERT_[A-Z_]+\]|=== EXPERT ===).*?(?=\n\n|\Z)',
        re.MULTILINE | re.DOTALL
    )
    res_content_clean = _INTERNAL_HEADERS_RE.sub('', res_content_clean).strip()

    # Strip confidence annotations that leak from expert/judge nodes into the response.
    # Covers: **LOW CONFIDENCE (30%)**, CONFIDENCE: low, Set CONFIDENCE: high, etc.
    _CONFIDENCE_TAG_RE = re.compile(
        r'(?:'
        r'\*{1,2}(?:low|medium|high)\s+confidence\s*(?:\(\s*\d+\s*%\s*\))?\*{1,2}'
        r'|(?:set\s+)?confidence\s*:\s*(?:low|medium|high|very\s+high|very\s+low)'
        r')',
        re.IGNORECASE,
    )
    res_content_clean = _CONFIDENCE_TAG_RE.sub('', res_content_clean).strip()

    # Strip all citation/reference brackets leaked from tool results:
    # covers various bracket styles used by different LLM tools.
    res_content_clean = re.sub(r'【[^】]*】', '', res_content_clean).strip()

    # Strip leading markdown bold label if the answer starts with "**Label:** value".
    # Models like qwen3 emit structured output ("**Identified Compound:** Benzene") which
    # should be reduced to just the value ("Benzene").
    _md_label = re.match(r'^\*{1,2}[^*\n]{1,40}\*{1,2}\s*[:\-]\s*(.+)', res_content_clean, re.DOTALL)
    if _md_label:
        res_content_clean = _md_label.group(1).strip()

    # Post-strip fallback: if cleaning stripped everything, use best expert result.
    # Explicitly skip expert results that are capability disclaimers (expert-leak patterns)
    # — using a leak answer as fallback is worse than returning empty.
    _LEAK_FALLBACK_RE = re.compile(
        r'\b(i (cannot|can\'t|won\'t) (access|browse|fetch)|'
        r'we need(s)? to (browse|search|fetch)|'
        r'no (direct )?access to (the )?(internet|web)|'
        r'attempt\s+(web\s+)?search|'
        r'unable to (browse|access|fetch))\b',
        re.I,
    )
    if not res_content_clean:
        _expert_results = state_.get("expert_results") or []
        _non_leak = [r for r in _expert_results if r and not _LEAK_FALLBACK_RE.search(r)]
        _best_expert = (
            next((r for r in _non_leak if _parse_expert_confidence(r) == "high"), None)
            or (_non_leak[0] if _non_leak else None)
        )
        if _best_expert:
            res_content_clean = _best_expert.strip()
            logger.warning("⚠️ Merger output empty after strip — using best non-leak expert result as fallback")

    # Reusable response, knowledge, episode and self-correction writes are
    # deferred to response_commit. Operational request audit remains immediate
    # and deliberately stores a candidate hash rather than reusable answer text.
    asyncio.create_task(_kafka_publish(KAFKA_TOPIC_REQUESTS, {
        "response_id":        state_.get("response_id", ""),
        "input":              state_["input"][:300],
        "candidate_hash":     hashlib.sha256(res_content_clean.encode()).hexdigest(),
        "expert_models_used": state_.get("expert_models_used", []),
        "cache_hit":          False,
        "quality_pending":    True,
        "ts":                 datetime.now().isoformat(),
    }))

    # ── Agentic gap detection: assess if another iteration is needed ─────────
    _agentic_max  = state_.get("max_agentic_rounds") or 0
    _agentic_iter = state_.get("agentic_iteration") or 0
    _agentic_gap  = ""
    _agentic_history = list(state_.get("agentic_history") or [])
    _agentic_extra: dict = {}
    _strategy_hint = ""
    from parsing import _parse_expert_gaps
    _declared_gaps: list[str] = []
    _declared_referrals: list[str] = []
    for _expert_result in state_.get("expert_results") or []:
        _gap, _referral = _parse_expert_gaps(_expert_result)
        if _gap:
            _declared_gaps.append(_gap)
        if _referral:
            _declared_referrals.append(_referral)

    if _agentic_max > 0 and _agentic_iter < _agentic_max:
        # Early exit: if a file was generated (SKILL_TRIGGER / download link), the answer is complete.
        # Re-planning would cause skill_detector to run again and overwrite the generated file.
        _is_skill_response = (
            "SKILL_TRIGGER" in res_content_clean
            or "/downloads/" in res_content_clean
            or "DOWNLOAD_URL" in res_content_clean
        )
        if _is_skill_response:
            # File already generated — no re-plan needed; skill_detector must not run again.
            logger.info("⚡ Agentic gap skipped: skill response detected (file already generated)")
            _agentic_gap = "COMPLETE"
        else:
            # Token-budget guard: skip gap detection if already close to limit
            _used_tokens = state_.get("prompt_tokens", 0) + merger_usage.get("prompt_tokens", 0)

            # Expert-leak detection FIRST: capability disclaimers must override confidence gate.
            # "We need to browse." is <=15 words and would pass the confidence gate falsely.
            _EXPERT_LEAK_RE = re.compile(
                r"\b(i (cannot|can't|won'?t) (access|browse|fetch|retrieve|visit|search)|"
                r"i don'?t have (web|internet|direct|real.?time)|"
                r"(we|let'?s|i'?ll|we'?ll) (will |)(browse|search|look up|fetch|navigate|check)|"
                r"(we|it) need(s)? to (browse|search|fetch|access|look up|retrieve)|"
                r"attempt\s+(web\s+)?search|"
                r"attempt\s+tool\s+(call|use)|"
                r"attempt\s+to\s+(search|browse|fetch|find|look|call)|"
                r"will\s+attempt\s+to\s+(search|browse|fetch|find)|"
                r"no (direct )?access to (the )?(internet|web|url|website|page)|"
                r"unable to (browse|access|fetch|visit|open)|"
                r"as an ai.{0,30}(cannot|can'?t)|"
                r"i('m| am) not able to (access|browse|fetch))\b",
                re.I,
            )
            _expert_results_combined = " ".join(state_.get("expert_results") or [])
            _expert_is_leak = bool(_EXPERT_LEAK_RE.search(_expert_results_combined))
            if _expert_is_leak:
                logger.info("🔍 Expert-leak detected — forcing NEEDS_MORE_INFO (skipping confidence gate)")
                _agentic_gap = (
                    "One or more experts responded with a capability disclaimer instead of "
                    "attempting to research. Use web_researcher or fetch_pdf_text to get the data directly."
                )
                _strategy_hint = "use web_researcher with a targeted search query for the missing data"

            # Confidence gate: if the answer is short, precise and all experts reported high
            # confidence, skip re-planning — the answer is almost certainly correct and further
            # searching may overwrite it with a wrong result (e.g. "backtick" → "dot").
            # Only applies when no expert-leak was detected.
            _all_high = all(
                _parse_expert_confidence(r) == "high"
                for r in (state_.get("expert_results") or []) if r
            )
            _answer_is_short = len(res_content_clean.split()) <= 5  # <=5 words: single-token answers like "backtick", "Fred", "42"
            # In research mode a short answer is NOT a reliability signal — complex research
            # questions that need web lookups should still be re-checked even when compact.
            _is_research_mode = (state_.get("mode") or "") == "research"
            _confidence_gate_passed = (
                not _expert_is_leak
                and _all_high
                and _answer_is_short
                and not _is_research_mode
            )
            if _confidence_gate_passed:
                logger.info("⚡ Agentic gap skipped: short high-confidence answer — no re-plan")
                _agentic_gap = "COMPLETE"

            # Skip gap detection when already resolved by confidence gate or expert-leak handler.
            # Running a judge LLM-call after the gate already decided COMPLETE wastes tokens
            # and risks overwriting the correct COMPLETE verdict with NEEDS_MORE_INFO.
            _jb = _judge_ctx_budget(state_.get("judge_num_ctx", 0))
            if not _confidence_gate_passed and _agentic_gap != "COMPLETE" and _used_tokens < AGENTIC_GAP_THRESHOLD_TOKENS:
                _gap_prompt = (
                    "You are a completion assessor. Based on the original question and the current answer, "
                    "determine if the answer is complete and what specific data is still missing.\n\n"
                    f"ORIGINAL QUESTION:\n{state_['input'][:_jb['gap_question']]}\n\n"
                    f"CURRENT ANSWER:\n{res_content_clean[:_jb['gap_answer']]}\n\n"
                    f"EXPERT-DECLARED GAPS:\n"
                    f"{'; '.join(dict.fromkeys(_declared_gaps)) or 'none'}\n\n"
                    f"EXPERT REFERRALS:\n"
                    f"{'; '.join(dict.fromkeys(_declared_referrals)) or 'none'}\n\n"
                    "IMPORTANT: If the answer contains phrases like 'I cannot access', 'no web browsing', "
                    "'I don't have internet access' — this is INCOMPLETE regardless of other content.\n\n"
                    "Reply ONLY in this exact format (no extra text):\n"
                    "COMPLETION_STATUS: COMPLETE | NEEDS_MORE_INFO\n"
                    "GAP: <specific fact/calculation/document still missing, or 'none'>\n"
                    "SEARCH_STRATEGY: <concrete next search — prefer domain-specific: "
                    "'web_search_domain site:semanticscholar.org <paper title>', "
                    "'web_search_domain site:webbook.nist.gov <compound name>', "
                    "'web_search_domain site:<authoritative_domain> <query>', "
                    "'use youtube_transcript with discovered video URL', "
                    "'use semantic_scholar_search <author year topic>'>"
                )
                try:
                    _gap_res = await _invoke_judge_with_retry(state_, _gap_prompt)
                    _gap_text = (_gap_res.content or "").strip()
                    _gap_match = re.search(r'GAP:\s*(.+?)(?:\n|$)', _gap_text, re.IGNORECASE)
                    _status_match = re.search(r'COMPLETION_STATUS:\s*(\w+)', _gap_text, re.IGNORECASE)
                    _strategy_match = re.search(r'SEARCH_STRATEGY:\s*(.+?)(?:\n|$)', _gap_text, re.IGNORECASE)
                    _status = (_status_match.group(1) if _status_match else "COMPLETE").upper()
                    _agentic_gap = (_gap_match.group(1).strip() if _gap_match else "").strip()
                    _strategy_hint = (_strategy_match.group(1).strip() if _strategy_match else "").strip()
                    if _status == "COMPLETE" or not _agentic_gap or _agentic_gap.lower() in ("none", ""):
                        _agentic_gap = "COMPLETE"
                        _strategy_hint = ""
                    logger.info(f"🔍 Agentic gap check: status={_status}, gap={_agentic_gap[:80]}, strategy={_strategy_hint[:60]}")
                except Exception as _ge:
                    logger.warning(f"⚠️ Agentic gap detection failed: {_ge}")
                    _agentic_gap = "COMPLETE"
                    _strategy_hint = ""
            else:
                logger.info(f"⚠️ Agentic gap skipped: token budget {_used_tokens} > 80k")
                _agentic_gap = "COMPLETE"

        # ── Typed Cascade Classification ──────────────────────────────────────
        # Convert binary gap text to a typed CascadeEvent so the planner
        # knows *why* it is re-planning, not just *that* it should.
        _cascade_event = None
        try:
            from services.cascade import (
                CascadeType,
                classify_gap as _classify_gap,
                emit_cascade as _emit_cascade,
                resolve_open_cascades as _resolve_open_cascades,
            )
            _cascade_event = _classify_gap(_agentic_gap, _strategy_hint)
            _request_id = state_.get("response_id", "")
            if _cascade_event.cascade_type == CascadeType.COMPLETE:
                _resolve_open_cascades(_request_id)
            else:
                _emit_cascade(_cascade_event, request_id=_request_id)
            if _cascade_event.replan_strategy and not _strategy_hint:
                _strategy_hint = _cascade_event.replan_strategy
            logger.info(
                "🌊 Cascade: type=%s gap=%s",
                _cascade_event.cascade_type, _agentic_gap[:60],
            )
        except Exception as _ce:
            logger.debug("Cascade classification skipped: %s", _ce)

        # Record only gap + strategy for the re-planner — not full findings text.
        # Full findings bloat the re-planner prompt (~1200 chars × rounds) without
        # adding information the planner can act on. Gap and strategy are sufficient.
        _agentic_history.append({
            "iteration":    _agentic_iter,
            "gap":          _agentic_gap[:300],
            "strategy":     _strategy_hint[:200],
            "cascade_type": _cascade_event.cascade_type if _cascade_event else "CONTEXT_GAP",
        })

        # Working Memory: LLM-based fact extraction only when:
        # (a) gap is still open — facts will feed the next re-planning round
        # (b) there are more rounds available — extraction is useless on the last iteration
        _max_rounds = state_.get("max_agentic_rounds", 2)
        _wm_merged: dict = dict(state_.get("working_memory") or {})
        _jb_wm = _judge_ctx_budget(state_.get("judge_num_ctx", 0))
        if (_agentic_gap != "COMPLETE"
                and _agentic_iter < _max_rounds - 1
                and state_.get("prompt_tokens", 0) < WM_EXTRACT_THRESHOLD_TOKENS):
            from parsing import _extract_json
            _extract_prompt = (
                "Extract the key facts from the text below as a flat JSON object "
                "{\"key\": \"value\"}. Keys must be short snake_case. "
                "Values must be concrete facts only (no opinions, no explanations). "
                "Return ONLY valid JSON, no markdown, no extra text.\n\n"
                f"TEXT:\n{res_content_clean[:_jb_wm['wm_extract_text']]}"
            )
            try:
                _fact_res = await _invoke_judge_with_retry(state_, _extract_prompt, max_retries=1)
                _facts = _extract_json(_fact_res.content or "")
                if isinstance(_facts, dict):
                    _fact_ts = datetime.utcnow().isoformat() + "Z"
                    for k, v in _facts.items():
                        _wm_merged[f"merger:{_agentic_iter}:{k}"] = {
                            "value": str(v)[:300],
                            "source": "merger_node",
                            "confidence": 0.7,
                            "ts": _fact_ts,
                        }
                    logger.info(f"📝 Working Memory: {len(_facts)} facts extracted by merger (iter {_agentic_iter})")
            except Exception as _fe:
                logger.debug(f"Merger fact extraction failed: {_fe}")

        # ── Retry Budget + STUCK detection ────────────────────────────────────
        _next_iter = _agentic_iter + 1 if _agentic_gap != "COMPLETE" else _agentic_iter
        _is_stuck = False
        _max_rounds = state_.get("max_agentic_rounds", 2)
        if _agentic_gap != "COMPLETE" and _next_iter >= _max_rounds:
            try:
                from services.retry_budget import check_and_emit_stuck as _check_stuck
                _is_stuck = await _check_stuck(
                    response_id=state_.get("response_id", ""),
                    iteration=_next_iter,
                    max_rounds=_max_rounds,
                    state_={**dict(state_), "cascade_type": (
                        _cascade_event.cascade_type if _cascade_event else "CONTEXT_GAP"
                    )},
                    redis_client=getattr(state, "redis_client", None),
                )
            except Exception as _se:
                logger.debug("Retry budget check skipped: %s", _se)

        # Increment agentic_iteration here via state return — not via direct mutation
        # in the router function (_should_replan), which is an anti-pattern in LangGraph.
        _agentic_extra = {
            "agentic_gap":          _agentic_gap,
            "agentic_history":      _agentic_history,
            "working_memory":       _wm_merged,
            "search_strategy_hint": _strategy_hint,
            "agentic_iteration":    _next_iter,
            "cascade_type":         (
                _cascade_event.cascade_type.value
                if _cascade_event else "CONTEXT_GAP"
            ),
            "stuck":                _is_stuck,
        }

    # ── System Constitution enforcement ───────────────────────────────────────
    # Deterministic check against sovereign-constitution.yaml before the
    # response leaves the orchestrator. Blocking violations replace the
    # response; warn violations are audited to Kafka only.
    _constitution_violations = []
    try:
        from services.constitution import enforce as _constitution_enforce
        res_content_clean, _constitution_violations = _constitution_enforce(
            res_content_clean, dict(state_),
        )
        if _constitution_violations:
            _viol_ids = [v.rule_id for v in _constitution_violations]
            await _report(f"⚖️ Constitution: {len(_constitution_violations)} violation(s): {_viol_ids}")
    except Exception as _coe:
        logger.debug("Constitution enforcement skipped: %s", _coe)

    await _ol_complete(_ol_merger_run, job_name="merger_node",
                       outputs=[dataset_response(state_.get("response_id", "synthesis"))])

    return {
        "final_response":         res_content_clean,
        "provenance_sources":     _provenance_sources,
        "conflict_registry":      _new_conflicts,
        "constitution_violations": [
            {"rule_id": v.rule_id, "on_violation": v.on_violation, "detail": v.detail}
            for v in _constitution_violations
        ],
        "response_commit_context": {
            "fast_path": False,
            "judge_refined_cats": sorted(_judge_refined_cats),
            "corrections": _deferred_corrections,
            "synthesis_insight": _synthesis_payload,
        },
        **merger_usage,
        **_agentic_extra,
        **_trust_state,
        **_merger_structured_state,
    }


async def thinking_node(state_: AgentState):
    """
    Simulates structured reasoning before synthesis.
    Activated for complex plans (>1 task) or when experts report low confidence.
    Magistral:24b generates explicit chain-of-thought that serves as context for the merger.
    """
    if state_.get("cache_hit"):
        return {"reasoning_trace": ""}
    # Agent mode: skip thinking node — coding agents need low latency, not CoT
    if state_.get("mode") == "agent":
        return {"reasoning_trace": ""}
    # Complexity routing: trivial/moderate requests skip thinking
    if state_.get("skip_thinking"):
        logger.info("⚡ Thinking node skipped (complexity routing)")
        return {"reasoning_trace": ""}

    from parsing import _extract_usage, _parse_expert_confidence, _expert_category

    mode     = state_.get("mode", "default")
    mode_cfg = MODES.get(mode, MODES["default"])
    force    = mode_cfg.get("force_think", False)

    plan           = state_.get("plan", [])
    expert_results = state_.get("expert_results") or []

    # Completed precision evidence plus at most one bounded non-precision task
    # already contains the facts the merger needs. A separate thinking-model
    # pass would duplicate synthesis and consume the budget needed for the
    # actual client-facing answer.
    _precision_tasks = [
        task
        for task in plan
        if isinstance(task, dict) and task.get("category") == "precision_tools"
    ]
    _non_precision_tasks = [
        task
        for task in plan
        if isinstance(task, dict) and task.get("category") != "precision_tools"
    ]
    _completed_precision_ids = {
        str(item.get("task_id") or "")
        for item in (state_.get("mcp_evidence") or [])
        if isinstance(item, dict) and item.get("status") == "completed"
    }
    if (
        _precision_tasks
        and len(_non_precision_tasks) <= 1
        and all(
            str(task.get("id") or "") in _completed_precision_ids
            for task in _precision_tasks
        )
    ):
        logger.info(
            "⚡ Thinking skipped: complete deterministic evidence and <=1 "
            "non-precision task"
        )
        await _record_stage(
            state_.get("response_id", ""),
            "thinking",
            "skipped_deterministic",
        )
        return {"reasoning_trace": ""}

    has_low_conf = any(_parse_expert_confidence(r) == "low" for r in expert_results)
    # Genuine complexity: sequential task chains (depends_on) or multi-domain expert divergence.
    # len(plan) > 1 is too broad — most research requests have >1 task but don't need CoT.
    has_sequential_chain = any(t.get("depends_on") for t in plan if isinstance(t, dict))
    has_multi_category   = len({t.get("category") for t in plan if isinstance(t, dict)}) > COT_MIN_CATEGORIES
    # Also activate for complex/research queries with multiple tasks — L3 GAIA questions
    # have only 1 category but multi-step reasoning benefits from CoT.
    has_multi_task = len([t for t in plan if isinstance(t, dict)]) > COT_MIN_TASKS
    is_complex = has_sequential_chain or has_multi_category or has_multi_task

    if not (force or is_complex or has_low_conf):
        return {"reasoning_trace": ""}

    logger.info("--- [NODE] THINKING (Chain-of-Thought) ---")
    await _report("🧠 Reasoning: strukturierte Analyse des Problems...")
    await _record_stage(state_.get("response_id", ""), "thinking", "started")

    sections = [f"QUESTION: {state_['input']}"]
    if expert_results:
        conf_summary = ", ".join(
            f"{_expert_category(r) or '?'}={_parse_expert_confidence(r)}"
            for r in expert_results
        )
        sections.append(f"EXPERT CONFIDENCE: {conf_summary}")
    _jb_r = _judge_ctx_budget(state_.get("judge_num_ctx", 0))
    if state_.get("web_research"):
        sections.append(f"WEB CONTEXT (excerpt):\n{state_['web_research'][:_jb_r['reasoning_web']]}")
    if state_.get("graph_context"):
        sections.append(f"GRAPH CONTEXT (excerpt):\n{state_['graph_context'][:_jb_r['reasoning_graph']]}")

    reasoning_prompt = (
        "You are an analytical reasoning assistant. Analyze the task in 4 steps:\n\n"
        "1. PROBLEM DECOMPOSITION: What are the core questions and sub-problems?\n"
        "2. SOURCE EVALUATION: Which information is reliable? Where are there contradictions?\n"
        "3. KNOWLEDGE GAPS: What remains uncertain or unclear?\n"
        "4. CONCLUSION: What is the most likely correct answer and why?\n\n"
        "Be precise and critical. Maximum 300 words.\n\n"
        + "\n\n".join(sections)
    )

    await _report(f"🧠 Reasoning-Prompt:\n{reasoning_prompt}")
    try:
        res   = await _invoke_judge_with_retry(state_, reasoning_prompt)
        usage = _extract_usage(res)
        trace = res.content.strip()
        await _report(f"🧠 Reasoning result ({len(trace)} chars):\n{trace}")
        await _record_stage(state_.get("response_id", ""), "thinking", "done")
        logger.info(f"🧠 Reasoning Trace: {trace[:200]}")
        return {"reasoning_trace": trace, **usage}
    except Exception as e:
        logger.warning(f"Thinking node error: {e}")
        return {"reasoning_trace": ""}


def _should_replan(state_: AgentState) -> str:
    """Router: decides whether merger should loop back to planner, run self-critique, or proceed."""
    import os

    # ── 1. Agentic re-planning (existing logic, highest priority) ────────────
    _max  = state_.get("max_agentic_rounds") or 0
    _iter = state_.get("agentic_iteration") or 0
    if _max > 0 and _iter < _max:
        _gap = (state_.get("agentic_gap") or "").strip()
        if _gap and _gap.upper() != "COMPLETE" and _gap.lower() not in ("none", ""):
            logger.info(f"🔄 Agentic router: iteration {_iter}/{_max}, gap='{_gap[:60]}'")
            return "planner"

    # A precision-hybrid candidate has already isolated its sole model-authored
    # part. Send it directly to the scoped critic; a generic self-critique sees
    # the complete request and can reintroduce an unbound precision answer.
    if state_.get("precision_hybrid_composed"):
        return "critic"

    # ── 2. Self-Critique Loop (TASK-11) ───────────────────────────────────────
    # Also covers BLOCK, not just PROCEED_WITH_ASSUMPTION: BLOCK does not
    # currently suppress or alter the response anywhere in the pipeline (it
    # only writes a decision-log entry — see services/trust_score.py) and, if
    # left out here, would get LESS scrutiny than the middle bucket even
    # though it's the worse-scoring case. Confirmed live 2026-07-16: a query
    # with zero retrieved sources landed on BLOCK (score 0.05) and skipped
    # both self-critique and the hallucination-risk critic check entirely.
    trust_verdict = (state_.get("trust_verdict") or "").upper()
    if trust_verdict in ("PROCEED_WITH_ASSUMPTION", "BLOCK"):
        sc_round = state_.get("self_critique_round") or 0
        sc_max   = state_.get("self_critique_max") or int(os.getenv("SELF_CRITIQUE_MAX_ROUNDS", "2"))
        if state_.get("review_replaces_self_critique"):
            logger.info("🔄 Self-Critique router: skipped, review wave replaces self-critique")
            return "critic"
        if sc_round >= 1:
            _prev = float(state_.get("self_critique_prev_score") or 0.0)
            _cur = float(state_.get("trust_score") or 0.0)
            _min_gain = float(os.getenv("SELF_CRITIQUE_MIN_GAIN", "0.05"))
            if _cur - _prev < _min_gain:
                logger.info(
                    "🔄 Self-Critique router: stop after round %d, trust gain %.3f < %.3f",
                    sc_round, _cur - _prev, _min_gain,
                )
                return "critic"
        if sc_round < sc_max:
            logger.info("🔄 Self-Critique router: round %d/%d, verdict=%s", sc_round + 1, sc_max, trust_verdict)
            return "self_critique"

    return "critic"


async def resolve_conflicts_node(state_: AgentState):
    """Evaluate the paraconsistent conflict registry and mark entries as resolved.

    Paraconsistent logic (de Vries 2007, arXiv:0707.2161, §2) tolerates
    contradictions — this node does not eliminate them but makes them explicit
    so downstream nodes (critic, agentic re-planner) can act on them.

    Resolution involves two sequential strategies:
    - Strategy A: auto-dismiss low-divergence conflicts (< 0.5 Jaccard distance) as formulaic variations.
    - Strategy B: escalate safety-critical conflicts (medical, legal) to the Judge LLM for Belnap-Dunn arbitration.
    """
    _SAFETY_CRITICAL_CATS = {"medical_consult", "legal_advisor"}
    conflicts = state_.get("conflict_registry") or []
    pending   = [c for c in conflicts if c.get("resolution") == "pending"]
    if not pending:
        return {}

    logger.info(f"⚖️  resolve_conflicts_node: {len(pending)} pending conflicts")
    await _report(f"⚖️ Resolving {len(pending)} paraconsistent conflict(s)...")
    await _record_stage(state_.get("response_id", ""), "resolve_conflicts", "started")

    # Strategy A: auto-dismiss low-divergence conflicts (formulaic variation, not real contradiction).
    # Strategy B: escalate safety-critical conflicts to a judge LLM call.
    # Mathematical basis: de Vries (2007), arXiv:0707.2161, §2 — paraconsistent resolution.
    _DIVERGENCE_AUTO_DISMISS = 0.5
    resolved: list = [c for c in conflicts if c.get("resolution") != "pending"]

    for c in pending:
        score    = c.get("divergence_score", 0.0)
        category = c.get("category", "")

        # Strategy A — low divergence: formulaic variation, not a real contradiction
        if score < _DIVERGENCE_AUTO_DISMISS:
            resolved.append({**c, "resolution": "dismissed", "resolved_by": "auto_low_divergence"})
            logger.info(f"⚖️  [{category}] conflict dismissed (score={score:.2f} < {_DIVERGENCE_AUTO_DISMISS})")
            continue

        # Strategy B — safety-critical with significant divergence: ask judge to arbitrate using Belnap-Dunn paraconsistent logic
        if category in _SAFETY_CRITICAL_CATS:
            arbitration_prompt = (
                f"Two experts in '{category}' produced conflicting claims.\n"
                f"Perform a paraconsistent bilattice evaluation (Belnap-Dunn logic: T, F, I, U) to arbitrate the dispute.\n\n"
                f"CLAIM A:\n{c['proposition_a']}\n\n"
                f"CLAIM B:\n{c['proposition_b']}\n\n"
                f"Format your response with a JSON conflict map inside XML tags and a final synthesis verdict:\n"
                f"<conflict_map>\n"
                f"{{\n"
                f"  \"points_of_dispute\": [\n"
                f"    {{\"point\": \"<claim description>\", \"evidence_a\": \"...\", \"evidence_b\": \"...\", \"bilattice_value\": \"<T|F|I|U>\"}}\n"
                f"  ]\n"
                f"}}\n"
                f"</conflict_map>\n"
                f"VERDICT: <A|B|SYNTHESIS> — <rationale>"
            )
            try:
                arb_res = await _invoke_judge_with_retry(state_, arbitration_prompt)
                verdict = arb_res.content.strip()
                
                # Parse conflict map JSON if present
                conflict_map = {}
                map_match = re.search(r"<conflict_map>(.*?)</conflict_map>", verdict, re.DOTALL)
                if map_match:
                    try:
                        conflict_map = json.loads(map_match.group(1).strip())
                        logger.info(f"⚖️ Parsed Belnap-Dunn Conflict Map: {json.dumps(conflict_map)}")
                    except Exception as json_err:
                        logger.warning(f"⚖️ Failed to parse conflict map JSON: {json_err}")
                
                # Extract clean verdict
                clean_verdict = verdict
                verdict_match = re.search(r"VERDICT:.*", verdict)
                if verdict_match:
                    clean_verdict = verdict_match.group(0)
                    
                resolved.append({
                    **c, 
                    "resolution": "resolved", 
                    "resolved_by": f"judge_arbitration: {clean_verdict[:200]}",
                    "conflict_map": conflict_map
                })
                logger.info(f"⚖️  [{category}] conflict resolved by paraconsistent judge: {clean_verdict[:80]}")
                await _report(f"⚖️ [{category}] Judge verdict: {clean_verdict[:120]}")
            except Exception as _arb_err:
                logger.warning(f"⚖️  [{category}] judge arbitration failed: {_arb_err}")
                resolved.append({**c, "resolution": "dismissed", "resolved_by": "judge_unavailable"})
            continue

        # Non-safety-critical, high divergence: log and dismiss — no LLM cost warranted
        resolved.append({**c, "resolution": "dismissed", "resolved_by": "unresolved_non_critical"})
        logger.info(f"⚖️  [{category}] conflict dismissed (non-critical, score={score:.2f})")

    return {"conflict_registry": resolved}


async def self_critique_node(state_: AgentState):
    """Self-Critique Iteration Loop (TASK-11).

    Triggered when trust_verdict == PROCEED_WITH_ASSUMPTION and self_critique_round
    is below self_critique_max. Runs a single LLM call reviewing existing expert
    results and emits an improved synthesis fragment appended as an extra expert_result
    so merger re-evaluates with enriched context.
    """
    import os

    round_num   = (state_.get("self_critique_round") or 0) + 1
    max_rounds  = state_.get("self_critique_max") or int(os.getenv("SELF_CRITIQUE_MAX_ROUNDS", "2"))
    request_id  = state_.get("response_id", "")
    trust_score = state_.get("trust_score", 0.0)

    logger.info("--- [NODE] SELF-CRITIQUE (round %d/%d, score=%.3f) ---", round_num, max_rounds, trust_score)
    await _report(f"🔄 Self-Critique round {round_num}/{max_rounds} (trust={trust_score:.2f})…")
    await _record_stage(request_id, "self_critique", "started", f"round {round_num}/{max_rounds}")

    try:
        from services.decision_log import log_decision, DecisionType
        log_decision(
            DecisionType.SELF_CRITIQUE_TRIGGERED, request_id,
            rationale=f"Trust-Score {trust_score:.3f} triggered self-critique (round {round_num}/{max_rounds})",
            metadata={"round": round_num, "max": max_rounds, "score": trust_score},
        )
    except Exception as _e:
        logger.debug("Self-critique decision log failed: %s", _e)

    expert_results = state_.get("expert_results") or []
    plan           = state_.get("plan") or []
    user_input     = state_.get("input", "")

    non_empty     = [r for r in expert_results if r and len(r.strip()) > 20]
    missing_count = max(len(plan) - len(non_empty), 0)

    gap_parts = [f"Trust-Score {trust_score:.2f} is below the PROCEED threshold."]
    if missing_count:
        gap_parts.append(f"{missing_count} plan task(s) have insufficient expert coverage.")
    conflicts = state_.get("conflict_registry") or []
    pending_c = [c for c in conflicts if c.get("resolution") == "pending"]
    if pending_c:
        gap_parts.append(f"{len(pending_c)} unresolved expert conflict(s) detected.")
    gap_summary = " ".join(gap_parts)

    existing_summary = "\n---\n".join(r[:400] for r in non_empty[:3]) if non_empty else "(no expert results yet)"

    critique_prompt = (
        "You are reviewing a multi-expert response with insufficient confidence.\n\n"
        f"USER REQUEST: {user_input}\n\n"
        f"QUALITY ISSUES: {gap_summary}\n\n"
        f"EXISTING EXPERT SUMMARIES (truncated):\n{existing_summary}\n\n"
        "Identify what is missing or uncertain, then provide a concise, well-grounded "
        "supplementary answer that fills the identified gaps. "
        "Be specific and factual. Maximum 400 words. Start directly with the content."
    )

    try:
        from parsing import _extract_usage
        res      = await _invoke_judge_with_retry(state_, critique_prompt)
        usage    = _extract_usage(res)
        improved = re.sub(r"<think>.*?</think>", "", res.content or "", flags=re.DOTALL).strip()

        if improved and len(improved) > 30:
            new_expert = f"[SELF_CRITIQUE_R{round_num} / judge]: {improved}"
            await _report(f"✅ Self-Critique produced {len(improved)} chars")
            await _record_stage(request_id, "self_critique", "done")
            logger.info("✅ Self-Critique round %d: %d chars added", round_num, len(improved))
            return {
                "expert_results": [new_expert],
                "self_critique_round": round_num,
                "self_critique_prev_score": float(trust_score or 0.0),
                **usage,
            }
    except Exception as _ex:
        logger.warning("⚠️ Self-Critique LLM call failed: %s", _ex)

    return {"self_critique_round": round_num, "self_critique_prev_score": float(trust_score or 0.0)}


def _log_hallucination_check(state_: AgentState, corrected: bool, upgraded: bool = False) -> None:
    """Decision-log entry for the critic_node's hallucination-risk trigger
    (see critic_node docstring) — separate from the pre-existing
    safety-critical fact-check path, which isn't logged as a distinct
    decision type. `upgraded` must reflect what the caller's return value
    actually does to trust_verdict, not be re-derived here from the prior
    verdict alone -- a non-compliant critic reply also has a prior BLOCK
    verdict but performs no upgrade, and the two must not read the same in
    the log."""
    try:
        from services.decision_log import log_decision, DecisionType
        _prior_verdict = (state_.get("trust_verdict") or "").upper()
        _verdict_note = (
            "upgraded a stale BLOCK to PROCEED_WITH_ASSUMPTION"
            if upgraded
            else f"Trust-Score stayed {_prior_verdict or 'PROCEED_WITH_ASSUMPTION'}"
        )
        log_decision(
            DecisionType.HALLUCINATION_CHECK,
            state_.get("response_id", ""),
            rationale=(
                f"{_verdict_note} — claim check "
                f"against retrieved sources {'found and corrected unsupported claims' if corrected else 'found no unsupported claims'}"
            ),
            metadata={
                "corrected": corrected,
                "trust_score": state_.get("trust_score"),
                "factors": (state_.get("trust_factors") or {}),
            },
        )
    except Exception as _e:
        logger.debug("Hallucination-check decision log failed: %s", _e)


_CRITIC_TRAILING_CONFIRMED_RE = re.compile(r'\bCONFIRMED\b\s*$', re.IGNORECASE)

# The critic prompt explicitly bans opening with meta-commentary and names
# "Factual errors were found"/"The answer contains mistakes" as examples of
# what NOT to write. Observed live, twice, across unrelated tasks: the judge
# opens a "corrected answer" with exactly this banned pattern ("The answer
# contains a critical factual error regarding the Rust implementation...",
# "The answer contains a critical technical error in its reasoning
# regarding memory orderings...") and then never gets around to providing a
# complete replacement -- just an analysis of what's wrong. This is a
# distinct failure mode from the trailing-CONFIRMED case: the model isn't
# confirming, it's diagnosing without delivering a fix, which the pure
# code-marker check below can miss when the diagnosis quotes fragments of
# the original code (e.g. inline `tail_` CAS mentions or a fenced excerpt of
# the flawed snippet), making it look like "code is still present". A fifth
# variant quotes the prompt's own "ANSWER TO CHECK:" section header back
# verbatim, optionally in quotes ('The provided "ANSWER TO CHECK" is
# severely corrupted...') instead of using a plain noun -- the quote
# handling and the "to check" suffix below cover it.
_CRITIC_PREAMBLE_RE = re.compile(
    r'^\s*the\s+(provided\s+|given\s+)?["“]?'
    r'(answer(\s+to\s+check)?|response|implementation|code)["”]?\b'
    r'|^\s*(unsupported|incorrect|critical)\s+(claim|flaw|error)\b',
    re.IGNORECASE,
)


def _critic_noncompliance_reason(critic_out: str, original: str) -> str:
    """Same rules as _critic_is_noncompliant_confirmation, returning which one fired ("" = compliant)."""
    stripped = critic_out.strip()
    if _CRITIC_TRAILING_CONFIRMED_RE.search(stripped):
        return "trailing_confirmed"
    if _CRITIC_PREAMBLE_RE.match(stripped):
        return "preamble"
    code_markers = ("`" * 3, "<!DOCTYPE", "<html", "def ", "function ", "class ", "import ", "setInterval")
    if any(m in original for m in code_markers) and not any(m in critic_out for m in code_markers):
        return "code_dropped"
    return ""


def _critic_is_noncompliant_confirmation(critic_out: str, original: str) -> bool:
    """Detect a judge reply that either reached a CONFIRMED verdict, or
    diagnosed a problem, without the required "start with CONFIRMED, or a
    direct corrected answer, no preamble" format.

    critic_node's prompt requires either the bare word CONFIRMED or a direct
    corrected answer with zero preamble. A judge that free-associates a long
    deliberation and only concludes CONFIRMED at the very end fails the
    ``.startswith("CONFIRMED")`` check below, so without this guard the
    entire deliberation trace silently replaces the real answer. Observed
    live: a correct Rust implementation replaced by an ~800-word internal
    monologue about whether the claim counts as "unsupported", ending in a
    bare "CONFIRMED" instead of a corrected answer.
    """
    stripped = critic_out.strip()
    if _CRITIC_TRAILING_CONFIRMED_RE.search(stripped):
        return True
    if _CRITIC_PREAMBLE_RE.match(stripped):
        return True
    # A real correction of a code answer still contains code. A reply with
    # none, while the original clearly had some, is deliberation/meta-
    # commentary rather than a replacement answer.
    _CODE_MARKERS = ("```", "<!DOCTYPE", "<html", "def ", "function ", "class ", "import ", "setInterval")
    if any(m in original for m in _CODE_MARKERS) and not any(m in critic_out for m in _CODE_MARKERS):
        return True
    return False


async def critic_node(state_: AgentState):
    """
    Fact-check pass over the merger answer. Two independent triggers:

      1. Safety-critical category (medical_consult, legal_advisor) — always
         checked, using the judge's own knowledge (original behaviour).
      2. Hallucination-risk check: Trust-Score verdict is still
         PROCEED_WITH_ASSUMPTION or BLOCK by the time the router reaches
         "critic" (i.e. after self-critique rounds ran their course and
         coverage is still thin, or unsupported-claims_penalty is doing the
         pulling — see services/trust_score.py). BLOCK is included
         deliberately: it doesn't suppress or alter the response anywhere
         else in the pipeline today (only logged — see trust_score.py), so
         excluding it here would mean the worst-scoring responses get LESS
         scrutiny than the middle bucket. This check is grounded against the
         actual retrieved sources (graph_context, web_research, mcp_result),
         not just the judge's own recollection, since an ungrounded judge can
         hallucinate its own "corrections" just as easily as the experts did.
    """
    if state_.get("cache_hit") or state_.get("guard_blocked"):
        return {"final_response": state_.get("final_response", "")}

    _SAFETY_CRITICAL_CATS = {"medical_consult", "legal_advisor"}
    from parsing import _extract_usage

    plan      = state_.get("plan", [])
    plan_cats = {t.get("category", "") for t in plan if isinstance(t, dict)}
    active    = plan_cats & _SAFETY_CRITICAL_CATS

    trust_verdict       = (state_.get("trust_verdict") or "").upper()
    hallucination_risk  = trust_verdict in ("PROCEED_WITH_ASSUMPTION", "BLOCK")

    # Precision-hybrid responses isolate the only model-authored fragment from
    # the deterministic markers.  If trust requires review, show the critic
    # only that fragment and its own task contract.  Giving it the complete
    # request would let it independently recalculate a precision item despite
    # never having access to the typed evidence behind the marker.
    if state_.get("precision_hybrid_composed") and not active:
        final_response = str(state_.get("final_response") or "")
        expert_body = str(state_.get("precision_hybrid_expert_body") or "")
        expert_task = str(state_.get("precision_hybrid_expert_task") or "")
        hybrid_confidence = str(
            state_.get("precision_hybrid_expert_confidence") or ""
        ).lower()
        if not hallucination_risk and hybrid_confidence == "high":
            return {"final_response": final_response}
        if (
            not final_response
            or not expert_body
            or final_response.count(expert_body) != 1
            or not expert_task
        ):
            logger.error("Precision hybrid critic boundary is incomplete")
            return {
                "final_response": "",
                "precision_binding_status": "failed",
                "precision_binding_errors": ["precision_hybrid_critic_boundary"],
            }
        logger.info("--- [NODE] CRITIC (scoped precision hybrid) ---")
        await _report("🔎 Critic: scoped review of the non-precision expert result")
        await _record_stage(
            state_.get("response_id", ""),
            "critic",
            "started",
            "precision_hybrid_scoped",
        )
        scoped_prompt = (
            "You are verifying one isolated expert answer. Review only the task "
            "and answer below. They are the complete scope: do not infer, add, "
            "summarize or answer any other task from a broader request.\n\n"
            f"TASK:\n{expert_task}\n\n"
            f"ANSWER TO CHECK:\n{expert_body}\n\n"
            "Reply with exactly CONFIRMED if the answer is correct and safe. "
            "Otherwise reply only with the fully corrected answer to this task, "
            "without preamble, confidence metadata, dates, times, unit conversions "
            "or unrelated numeric calculations."
        )
        try:
            res = await _invoke_judge_with_retry(state_, scoped_prompt)
            usage = _extract_usage(res)
            critic_out = (res.content or "").strip()
            if not critic_out or critic_out.startswith("[Judge unavailable"):
                logger.warning(
                    "Precision hybrid critic unavailable — preserving expert body"
                )
                return {"final_response": final_response}
            if critic_out.upper() == "CONFIRMED":
                await _record_stage(
                    state_.get("response_id", ""),
                    "critic",
                    "confirmed",
                    "precision_hybrid_scoped",
                )
                return {"final_response": final_response, **usage}
            corrected = final_response.replace(expert_body, critic_out, 1)
            await _record_stage(
                state_.get("response_id", ""),
                "critic",
                "corrected",
                "precision_hybrid_scoped",
            )
            return {
                "final_response": corrected,
                "precision_hybrid_expert_body": critic_out,
                **usage,
            }
        except Exception as exc:
            logger.warning("Precision hybrid critic failed: %s", exc)
            return {"final_response": final_response}

    if not active and not hallucination_risk:
        return {"final_response": state_.get("final_response", "")}

    final_response = state_.get("final_response", "")
    if not final_response or len(final_response) < 100:
        return {"final_response": final_response}

    _trigger = f"fact-check: {active}" if active else f"hallucination-risk (trust={trust_verdict})"
    logger.info(f"--- [NODE] CRITIC ({_trigger}) ---")
    await _report(f"🔎 Critic: {_trigger}...")
    await _record_stage(state_.get("response_id", ""), "critic", "started")

    if active:
        critic_prompt = (
            f"You are a critical reviewer for {', '.join(sorted(active))} answers.\n"
            "Check the following answer for factual errors, dangerous statements or misleading information.\n\n"
            f"REQUEST: {state_['input']}\n\n"
            f"ANSWER TO CHECK:\n{final_response}\n\n"
            "RESPOND IN ONE OF EXACTLY TWO WAYS — no other format is acceptable:\n\n"
            "1. If the answer is factually correct and safe:\n"
            "   Respond with exactly the single word: CONFIRMED\n\n"
            "2. If the answer contains factual errors or dangerous content:\n"
            "   Write the fully corrected answer DIRECTLY — as if you were answering the user's request yourself.\n"
            "   Do NOT begin with any preamble, error analysis, or meta-commentary such as "
            "'Factual errors were found' or 'The answer contains mistakes'.\n"
            "   Start immediately with the corrected content.\n"
            "   You may append a brief [Correction-Note: ...] at the very end only.\n"
        )
    else:
        # Source-grounded claim verification — the judge gets the same raw
        # retrieval evidence the experts had, so it verifies claims against
        # real evidence instead of substituting its own (also-fallible)
        # knowledge. Sources truncated to keep the prompt bounded; a missing
        # source pool is still useful signal (an answer with specific claims
        # but zero retrieved evidence is exactly the case worth catching).
        graph_ctx = (state_.get("graph_context") or "")[:3000]
        web_ctx   = (state_.get("web_research") or "")[:3000]
        mcp_ctx   = (
            (state_.get("precision_prompt_projection") or "")
            if state_.get("required_precision_intents")
            else (state_.get("mcp_result") or "")
        )[:2000]
        sources = "\n---\n".join(s for s in (graph_ctx, web_ctx, mcp_ctx) if s.strip()) \
            or "(no retrieved sources available for this answer)"
        critic_prompt = (
            "You are verifying an answer for unsupported claims. This response's "
            f"Trust-Score is still low (verdict={trust_verdict}) even after a "
            "self-critique pass, meaning parts of it may not be well grounded "
            "in evidence.\n\n"
            f"REQUEST: {state_['input']}\n\n"
            f"RETRIEVED SOURCES (the only evidence available for this answer):\n{sources}\n\n"
            f"ANSWER TO CHECK:\n{final_response}\n\n"
            "Check every specific factual claim (numbers, dates, names, statistics) "
            "in the answer against the retrieved sources above. General knowledge "
            "not covered by the sources is fine to leave as-is — only flag claims "
            "that are specific AND contradicted by, or absent from, the sources "
            "when the answer presents them as sourced facts.\n\n"
            "RESPOND IN ONE OF EXACTLY TWO WAYS — no other format is acceptable:\n\n"
            "1. If every specific sourced claim is supported by the sources:\n"
            "   Respond with exactly the single word: CONFIRMED\n\n"
            "2. If the answer contains claims not supported by the sources:\n"
            "   Write the fully corrected answer DIRECTLY — as if you were answering "
            "the user's request yourself, removing or hedging the unsupported claims.\n"
            "   Do NOT begin with any preamble or meta-commentary.\n"
            "   Start immediately with the corrected content.\n"
            "   You may append a brief [Correction-Note: ...] at the very end only.\n"
        )
    if state_.get("precision_prompt_projection"):
        critic_prompt += (
            "\nMANDATORY PRECISION OUTPUT CONTRACT: Preserve every "
            "[[MOE_PRECISION:...]] marker present in the answer or VERIFIED "
            "PRECISION FACT SLOTS byte-for-byte exactly once and in listed "
            "order. Put each marker alone on its own line without any prefix, "
            "suffix, numbering or punctuation. Never infer or write the "
            "represented value yourself and never add a second date, time, "
            "number, unit or summary value for that precision item elsewhere. "
            "If you rewrite the answer, include "
            "all markers unchanged.\n"
        )

    await _report(f"🔎 Critic-Prompt:\n{critic_prompt}")
    try:
        res          = await _invoke_judge_with_retry(state_, critic_prompt)
        usage        = _extract_usage(res)
        critic_out   = re.sub(r"<think>.*?</think>", "", res.content or "", flags=re.DOTALL).strip()
        if "</think>" in critic_out:
            # Unterminated/leading reasoning block: keep only the text after it.
            critic_out = critic_out.split("</think>")[-1].strip()
        await _report(f"🔎 Critic response:\n{critic_out}")

        # Guard: if the judge refused (content filter / VRAM), keep the merger answer unchanged.
        if critic_out.startswith("[Judge unavailable") or not critic_out:
            logger.warning("⚠️ Critic: judge refused — preserving merger answer unchanged")
            await _report("⚠️ Critic: judge refused (content filter?) — merger answer preserved")
            return {"final_response": final_response}

        if critic_out.upper().startswith("CONFIRMED"):
            await _report("✅ Critic: answer confirmed correct")
            await _record_stage(state_.get("response_id", ""), "critic", "confirmed")
            logger.info("✅ Critic: no errors found")
            if hallucination_risk and not active:
                # The unsupported-claims check that just ran is exactly what
                # a BLOCK verdict challenges (see the "else" critic_prompt
                # branch above, which fires specifically because trust stayed
                # low after self-critique). Finding no unsupported claims
                # addresses that reason, so a stale BLOCK from an earlier
                # merger round must not keep quality_gate_node blocking a
                # response the fact-check just cleared.
                _will_upgrade = trust_verdict == "BLOCK"
                _log_hallucination_check(state_, corrected=False, upgraded=_will_upgrade)
                if _will_upgrade:
                    return {"final_response": final_response, "trust_verdict": "PROCEED_WITH_ASSUMPTION", **usage}
            return {"final_response": final_response, **usage}

        if _critic_is_noncompliant_confirmation(critic_out, final_response):
            logger.warning(
                "⚠️ Critic: non-compliant judge format (CONFIRMED reached without "
                "the required leading format, or code dropped from the reply) — "
                "preserving merger answer instead of overwriting it with the "
                "judge's deliberation trace | reason=%s chars=%d head=%r",
                _critic_noncompliance_reason(critic_out, final_response),
                len(critic_out),
                critic_out[:160],
            )
            await _report(
                "⚠️ Critic: judge reply was a non-compliant deliberation, not a "
                "correction — merger answer preserved"
            )
            await _record_stage(
                state_.get("response_id", ""), "critic", "confirmed", "non_compliant_format"
            )
            if hallucination_risk and not active:
                _log_hallucination_check(state_, corrected=False)
            return {"final_response": final_response, **usage}

        await _report(f"⚠️ Critic: answer corrected ({len(critic_out)} chars)")
        await _record_stage(state_.get("response_id", ""), "critic", "corrected")
        logger.info(f"⚠️ Critic hat Korrekturen vorgenommen: {critic_out[:100]}")
        if hallucination_risk and not active:
            # See the CONFIRMED branch above: the critic just grounded the
            # unsupported claims that a stale BLOCK verdict was flagging, so
            # let the corrected response through instead of quality_gate_node
            # discarding it based on a trust_verdict computed before this fix.
            _will_upgrade = trust_verdict == "BLOCK"
            _log_hallucination_check(state_, corrected=True, upgraded=_will_upgrade)
            if _will_upgrade:
                return {"final_response": critic_out, "trust_verdict": "PROCEED_WITH_ASSUMPTION", **usage}
        return {"final_response": critic_out, **usage}
    except Exception as e:
        logger.warning(f"Critic node error: {e}")
        return {"final_response": final_response}


async def quality_gate_node(state_: AgentState):
    """Apply the final, non-bypassable quality/HITL decision.

    Trust is calculated in ``merger_node`` so self-critique can improve the
    evidence before this node runs. Keeping the enforcement after ``critic``
    ensures neither a corrected answer nor a later graph edge can accidentally
    bypass a BLOCK verdict.
    """
    request_id = state_.get("response_id", "")
    final_response = state_.get("final_response", "")
    required_precision = [
        item for item in state_.get("required_precision_intents") or []
        if isinstance(item, dict)
    ]

    def _record_precision_quality(outcome: str) -> None:
        if not required_precision:
            return
        from services.precision_telemetry import record_precision_event
        record_precision_event(
            "quality", outcome,
            tool=(
                str(required_precision[0].get("tool") or "")
                if len(required_precision) == 1 else "multi"
            ),
            mode=str(state_.get("precision_contract_mode") or "enforce"),
        )

    # Cynefin must see the final trust verdict. The planner-time classification
    # cannot produce CHAOTIC because trust does not exist yet at that point.
    try:
        from services.quality_gate import evaluate_quality_gate
        decision = evaluate_quality_gate(dict(state_))
    except Exception as exc:
        logger.exception("Final quality-gate evaluation failed: %s", exc)
        await _record_stage(request_id, "quality_gate", "evaluation_failed")
        _record_precision_quality("failed")
        return {
            "final_response": "",
            "quality_blocked": True,
            "quality_block_reason": "quality_gate_evaluation_failed",
            "quality_gate_status": "blocked",
        }

    if decision.action == "block":
        logger.warning("Quality gate blocked req=%s reason=%s", request_id, decision.reason)
        await _record_stage(request_id, "quality_gate", "blocked", str(decision.reason))
        _record_precision_quality("blocked")
        if required_precision and str(decision.reason).startswith("precision_"):
            from services.precision_telemetry import record_precision_event
            record_precision_event(
                "escape", "blocked",
                tool=(
                    str(required_precision[0].get("tool") or "")
                    if len(required_precision) == 1 else "multi"
                ),
                mode=str(state_.get("precision_contract_mode") or "enforce"),
            )
        return {
            "final_response": "",
            "quality_blocked": True,
            "quality_block_reason": decision.reason,
            "cynefin_domain": decision.cynefin_domain,
            "quality_gate_status": "blocked",
        }

    if decision.action == "pass":
        await _record_stage(request_id, "quality_gate", "passed")
        _record_precision_quality("passed")
        return {
            "quality_blocked": False,
            "cynefin_domain": decision.cynefin_domain,
            "quality_gate_status": "passed",
        }

    reason = decision.reason
    try:
        from services.hitl_gate import create_gate
        from services.response_commit import build_response_commit_payload
        gate_id = await asyncio.to_thread(
            create_gate,
            request_id,
            reason,
            final_response,
            user_id=state_.get("user_id", ""),
            commit_payload=build_response_commit_payload(
                dict(state_), final_response=final_response
            ),
        )
    except Exception as exc:
        logger.warning("HITL gate creation failed: %s", exc)
        gate_id = None

    if not gate_id:
        # A required human gate may never fail open by releasing the draft.
        logger.warning("HITL gate storage unavailable req=%s reason=%s", request_id, reason)
        await _record_stage(request_id, "quality_gate", "storage_unavailable", str(reason))
        _record_precision_quality("blocked")
        return {
            "final_response": "",
            "quality_blocked": True,
            "quality_block_reason": "hitl_gate_unavailable",
            "hitl_gate_reason": reason,
            "cynefin_domain": decision.cynefin_domain,
            "quality_gate_status": "blocked",
        }

    await _record_stage(request_id, "quality_gate", "pending", gate_id)
    _record_precision_quality("pending")
    return {
        # The draft remains only in the gate store; downstream HTTP/SSE code
        # cannot accidentally expose it before approval.
        "final_response": "",
        "hitl_gate_id": gate_id,
        "hitl_gate_reason": reason,
        "quality_blocked": False,
        "cynefin_domain": decision.cynefin_domain,
        "quality_gate_status": "pending",
    }
