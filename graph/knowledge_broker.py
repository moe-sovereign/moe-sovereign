"""Knowledge broker: what the infrastructure already knows about a request, collected before anything is planned.

Runs between `semantic_router` and `planner` (main.py). Without it the planner plans blind and the experts see only what the
parallel retrieval nodes later deliver. The broker queries the infrastructure sources concurrently, each under its own cap:

  graph        GraphRAGManager.query_facts()   facts with provenance (authority, valid_from, verified, trust)
  corrections  graph_rag.corrections           past mistakes for this kind of request (tenant-scoped)
  episodes     episodic_memory                 routing/tool hints from similar past tasks
  cache        cached_facts_meta               metadata of the semantic-cache entry cache_lookup_node found

and turns the result into a JSON-safe `knowledge_bundle` plus a rendered `knowledge_block` (known facts, conflicts, corrections,
open gaps). Gaps are request terms without any entity hit: only they justify asking a knowledge model (a later step).

Off by default (KNOWLEDGE_BROKER_ENABLED=0; a template can force "on"/"off" via knowledge_broker_mode). Every source degrades on
its own (timeout/error); only a spent *request* deadline propagates.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Optional

import state
from complexity_estimator import estimate_complexity
from pipeline.state import AgentState
from services.deadline import RequestDeadlineExceeded, remaining_timeout
from services.decision_log import DecisionType, log_decision
from services.helpers import _report
from services.knowledge_llm import ask_knowledge, gap_question, knowledge_config, verification_question
from services.knowledge_reconcile import MIN_LLM_CONFIDENCE, functional_relations, reconcile_facts, reconcile_with_llm
from services.model_capabilities import get_knowledge_cutoff
from services.tracking import _record_stage

logger = logging.getLogger("MOE-SOVEREIGN")

BUNDLE_SCHEMA = 1
MAX_FACTS = 40
MAX_GAPS = 5
MAX_RENDERED_GRAPH_CHARS = 12_000


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def broker_enabled(state_: dict) -> bool:
    """A template's knowledge_broker_mode ("on"/"off") beats the environment switch."""
    mode = str(state_.get("knowledge_broker_mode") or "").strip().lower()
    if mode in ("on", "off"):
        return mode == "on"
    return os.getenv("KNOWLEDGE_BROKER_ENABLED", "0").strip().lower() in ("1", "true", "yes")


async def _bounded(awaitable: Awaitable, cap: float, state_: dict, stage: str) -> tuple[Any, str]:
    """Await one source under its own cap. Returns (value, status); status is ok | timeout | error.

    RequestDeadlineExceeded is a TimeoutError, so a stage cap and a spent request budget look alike: remaining_timeout() tells
    them apart (it raises exactly when the request budget is gone, and that one must end the request).
    """
    try:
        timeout = remaining_timeout(state_, cap, stage=stage)
    except RequestDeadlineExceeded:
        getattr(awaitable, "close", lambda: None)()
        raise
    try:
        return await asyncio.wait_for(awaitable, timeout), "ok"
    except asyncio.TimeoutError:
        remaining_timeout(state_, 0.1, stage=stage)
        logger.warning("knowledge broker: %s timed out after %.1fs", stage, timeout)
        return None, "timeout"
    except Exception as exc:  # noqa: BLE001 - one failing source must not fail the request
        logger.warning("knowledge broker: %s failed: %s", stage, exc)
        return None, "error"


def _has_specific_term(text: str) -> bool:
    """A proper noun after the first word, or a token with a digit: something an infrastructure lookup could match."""
    tokens = re.findall(r"[\w+#]+", text or "")
    return any(t[:1].isupper() for t in tokens[1:]) or any(any(c.isdigit() for c in t) for t in tokens)


def _gaps(searched_terms: list, term_hits: dict) -> list:
    """Capitalised searched terms that led to no entity: knowledge the infrastructure lacks."""
    return [t for t in searched_terms if t not in term_hits and t[:1].isupper()][:MAX_GAPS]


def _date(ms: Optional[int]) -> str:
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def render_knowledge_block(bundle: dict, max_chars: int = 2500) -> str:
    """The text experts (and optionally the planner) get. Empty when the infrastructure knows nothing."""
    facts, conflicts = bundle.get("facts") or [], bundle.get("conflicts") or []
    corrections, episode = bundle.get("corrections") or [], (bundle.get("episode_hint") or "").strip()
    llm = bundle.get("llm_answers") or []
    if not (facts or corrections or episode or llm):
        return ""
    sections: list = []
    if facts:
        lines = ["[KNOWN FACTS (infrastructure, authoritative first)]"]
        for f in sorted(facts, key=lambda f: (-int(f.get("authority") or 0), -(f.get("valid_from_ms") or 0))):
            tags = ", ".join(t for t in ("verified" if f.get("verified") else "", f.get("origin") or "", _date(f.get("valid_from_ms"))) if t)
            lines.append(f"- {f['text']}" + (f" ({tags})" if tags else ""))
        sections.append(lines)
    if llm:
        cutoff = bundle.get("llm_cutoff") or ""
        lines = [f"[UNVERIFIED MODEL KNOWLEDGE ({bundle.get('llm_model') or 'external model'}" + (f", cutoff {cutoff}" if cutoff else "")
                 + "); not verified by the infrastructure, check against the facts above]"]
        for f in llm:
            tail = f" (confidence {f['confidence']:.0%}" + (f", as of {f['as_of']}" if f.get("as_of") else "") + ")"
            lines.append(f"- {f['text']}{tail}")
        sections.append(lines)
    if conflicts:
        lines = ["[CONFLICTS (several values stored; the first one is kept)]"]
        for c in conflicts:
            lines.append(f"- {c['key'].replace('|', ' ')}: kept {c.get('kept_text') or c['kept']}, also seen {c.get('dropped_text') or '-'} (rule: {c['rule']})")
        sections.append(lines)
    if corrections:
        lines = ["[CORRECTIONS (avoid repeating these past errors)]"]
        for c in corrections:
            lines.append(f"- wrong: {c.get('wrong', '')} | correct: {c.get('correct', '')}")
        sections.append(lines)
    if episode:
        sections.append(["[EPISODE HINT]", episode[:600]])
    gaps = bundle.get("gaps") or []
    if gaps:
        sections.append(["[OPEN GAPS (no infrastructure knowledge for)]", "- " + ", ".join(gaps)])
    out: list = []
    size = 0
    for section in sections:
        for line in section:
            if size + len(line) + 1 > max_chars:
                bundle["truncated"] = True
                return "\n".join(out)
            out.append(line)
            size += len(line) + 1
        out.append("")
        size += 1
    return "\n".join(out).rstrip()


def _conflict_view(conflicts: list, facts_all: list) -> list:
    """Reconcile conflicts with the texts of the kept and dropped values, for rendering and the decision log."""
    text = {f["id"]: f["text"] for f in facts_all}
    out = []
    for c in conflicts:
        kept = set(c["kept"])
        out.append({**c,
                    "kept_text": "; ".join(sorted({text[i] for i in kept if i in text})),
                    "dropped_text": "; ".join(sorted({text[i] for i in c["candidates"] if i not in kept and i in text}))})
    return out


async def knowledge_broker_node(state_: AgentState):
    """Collect infrastructure knowledge for the request. Returns {} when the broker is off or has nothing to add."""
    if state_.get("cache_hit") or (state_.get("mode") or "default") != "default" or not broker_enabled(state_):
        return {}
    text = state_.get("input") or ""
    request_id = state_.get("response_id", "")
    sources = {"graph": "disabled", "corrections": "disabled", "episodes": "disabled", "cache": "disabled", "knowledge_llm": "disabled"}
    if estimate_complexity(text) == "trivial" and not _has_specific_term(text):
        await _record_stage(request_id, "knowledge_broker", "skipped", "trivial")
        return {}

    started = time.monotonic()
    source_cap = _env_float("KNOWLEDGE_BROKER_SOURCE_TIMEOUT_S", 1.5)
    total_cap = _env_float("KNOWLEDGE_BROKER_TIMEOUT_S", 2.5)
    tenant_ids = state_.get("tenant_ids") or None
    manager = state.graph_manager
    graph_allowed = bool(state_.get("graphrag_template_allowed", state_.get("enable_graphrag", True)))
    await _report("🧭 Knowledge broker: looking up infrastructure knowledge...")

    async def graph_source():
        if manager is None or not graph_allowed:
            return None
        return await manager.query_facts(text, None, tenant_ids, MAX_FACTS)

    async def corrections_source():
        from config import CORRECTION_MEMORY_ENABLED
        if manager is None or not CORRECTION_MEMORY_ENABLED:
            return None
        from graph_rag.corrections import query_corrections
        return await query_corrections(manager.driver, text, state_.get("direct_expert") or "general", tenant_ids=tenant_ids)

    async def episodes_source():
        if manager is None:
            return None
        from episodic_memory import get_episode_hint
        return await get_episode_hint(manager.driver, text, "general", state_.get("user_id", ""))

    async def gather_all():
        return await asyncio.gather(
            _bounded(graph_source(), source_cap, state_, "knowledge_broker:graph"),
            _bounded(corrections_source(), source_cap, state_, "knowledge_broker:corrections"),
            _bounded(episodes_source(), source_cap, state_, "knowledge_broker:episodes"),
        )

    overall = remaining_timeout(state_, total_cap, stage="knowledge_broker")  # raises when the request budget is spent
    try:
        (graph, g_status), (corr, c_status), (epi, e_status) = await asyncio.wait_for(gather_all(), overall)
    except asyncio.TimeoutError:
        remaining_timeout(state_, 0.1, stage="knowledge_broker")  # raises if the request budget itself is gone
        logger.warning("knowledge broker: overall cap of %.1fs hit", total_cap)
        graph = corr = epi = None
        g_status = c_status = e_status = "timeout"

    def status(value, st, empty) -> str:
        return st if st != "ok" else ("empty" if value is None or empty else "ok")

    graph_facts = list((graph or {}).get("facts") or [])
    kept, raw_conflicts = reconcile_facts(graph_facts)
    conflicts = _conflict_view(raw_conflicts, graph_facts)
    for c in conflicts:
        try:
            log_decision(DecisionType.KNOWLEDGE_CONFLICT, request_id,
                         f"{c['key']}: kept {c['kept_text'] or c['kept']}, dropped {c['dropped_text'] or '-'} ({c['rule']})", {"conflict": c})
        except Exception as exc:  # noqa: BLE001 - logging must not fail the request
            logger.debug("knowledge broker: decision log failed: %s", exc)
    sources["graph"] = status(graph, g_status, not graph_facts and not (graph or {}).get("entity_hits"))
    sources["corrections"] = status(corr, c_status, not corr)
    sources["episodes"] = status(epi, e_status, not epi)
    cache_meta = state_.get("cached_facts_meta") or {}
    sources["cache"] = "ok" if cache_meta else "empty"

    # --- the knowledge model: only for what the infrastructure lacks (gap questions) or to check an attribute it holds -----------
    gaps = _gaps(list((graph or {}).get("searched_terms") or []), (graph or {}).get("term_hits") or {}) if g_status == "ok" and graph else []
    llm_facts: list = []
    llm_conflicts: list = []
    cfg = knowledge_config(state_)
    llm_meta = {"llm_model": "", "llm_cutoff": ""}
    if cfg is not None:
        questions: list = []
        meta: list = []
        # KNOWLEDGE_CONSULT_BY=planner: the planner model asks the gap questions itself (planner interview); the broker only checks
        if os.getenv("KNOWLEDGE_CONSULT_BY", "broker") != "planner":
            for term in gaps:
                if len(questions) < cfg["max_questions"]:
                    questions.append(gap_question(term))
                    meta.append({"subject": "", "relation": ""})
        functional = functional_relations()
        seen_subjects: set = set()
        for f in sorted(kept, key=lambda f: -int(f.get("authority") or 0)):
            if len(questions) >= cfg["max_questions"]:
                break
            if f.get("relation") in functional and f.get("subject") not in seen_subjects:
                seen_subjects.add(f["subject"])
                questions.append(verification_question(f["subject"], f["relation"]))
                meta.append({"subject": f["subject"], "relation": f["relation"]})
        if questions:
            share = (graph or {}).get("rendered") or "" if cfg["share_context"] else ""
            answers, k_status = await _bounded(ask_knowledge(state_, questions, cfg, context=share), cfg["timeout_s"] + 0.5, state_, "knowledge_broker:llm")
            answered = [{**a, **m} for a, m in zip(answers or [], meta)]
            cutoff = get_knowledge_cutoff(cfg["model"])
            llm_facts, llm_conflicts = reconcile_with_llm(kept, answered, llm_cutoff=cutoff)
            llm_meta = {"llm_model": cfg["model"], "llm_cutoff": cutoff.strftime("%Y-%m") if cutoff else ""}
            sources["knowledge_llm"] = k_status if k_status != "ok" else (
                "ok" if any(not a["unknown"] and a["confidence"] >= MIN_LLM_CONFIDENCE for a in (answers or [])) else "empty")
        else:
            sources["knowledge_llm"] = "skipped"
        for c in llm_conflicts:
            view = {**c, "candidates": c["kept"], "kept_text": str(c["infra"]), "dropped_text": f"the model said {c['llm']}"}
            conflicts.append(view)
            try:
                log_decision(DecisionType.KNOWLEDGE_CONFLICT, request_id, f"{c['key']}: infrastructure {c['infra']} vs model {c['llm']} ({c['rule']})", {"conflict": view})
            except Exception as exc:  # noqa: BLE001
                logger.debug("knowledge broker: decision log failed: %s", exc)

    bundle = {
        "schema": BUNDLE_SCHEMA,
        "terms": list((graph or {}).get("terms") or []),
        "entity_hits": dict((graph or {}).get("entity_hits") or {}),
        "gaps": gaps,
        "facts": kept[:MAX_FACTS],
        "conflicts": conflicts,
        "corrections": list(corr or [])[:3],
        "episode_hint": epi or "",
        "cache": {"hit": False, **{k: cache_meta.get(k) for k in ("ts", "confidence", "distance") if k in cache_meta}} if cache_meta else {},
        "llm_answers": llm_facts,  # only what is shown as model knowledge: this is what keeps the answer out of the graph write-back
        **llm_meta,
        "sources": sources,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "truncated": len(kept) > MAX_FACTS,
        "rendered_graph": ((graph or {}).get("rendered") or "")[:MAX_RENDERED_GRAPH_CHARS],
    }
    block = render_knowledge_block(bundle, int(_env_float("KNOWLEDGE_BLOCK_MAX_CHARS", 2500)))
    await _record_stage(request_id, "knowledge_broker", "done", ", ".join(f"{k}={v}" for k, v in sources.items() if v != "disabled"))
    return {
        "knowledge_bundle": bundle,
        "knowledge_block": block,
        "knowledge_entity_hits": list(bundle["entity_hits"]),
        "knowledge_gaps": bundle["gaps"],
        "knowledge_llm_context": "\n".join(f["text"] for f in llm_facts),
    }
