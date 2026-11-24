"""Knowledge broker node: off by default, per-source degradation, gaps, conflicts, rendered block."""
import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
import episodic_memory
import graph.knowledge_broker as kb
from graph_rag import corrections as corrections_mod
from services.deadline import RequestDeadlineExceeded

TEXT = "What is the capacity of the Elm12 depot and of Birch7?"


def fact(obj, *, authority=2, ms=1_700_000_000_000, verified=False, origin="extracted", subject="Elm12", rel="HAS_CAPACITY"):
    return {"id": f"g:{subject}{rel}{obj}", "source": "graph", "subject": subject, "relation": rel, "object": obj,
            "text": f"{subject} {rel} {obj}", "valid_from_ms": ms, "created_ms": ms, "verified": verified, "origin": origin,
            "source_model": "", "confidence": 0.9, "trust_score": None, "version": 1, "authority": authority}


def facts_result(facts, hits=None, term_hits=None, searched=("Elm12", "Birch7", "capacity")):
    return {"terms": list(searched), "searched_terms": list(searched), "found": {}, "entity_hits": {"Elm12": "Depot"} if hits is None else hits,
            "term_hits": {"Elm12": ["Elm12"], "capacity": ["Elm12"]} if term_hits is None else term_hits,
            "facts": facts, "rendered": "[Knowledge Graph]\n• Elm12 (Depot): HAS_CAPACITY 40 pallets"}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_BROKER_ENABLED", "1")
    monkeypatch.setattr(kb, "_record_stage", AsyncMock())
    monkeypatch.setattr(kb, "_report", AsyncMock())
    mgr = MagicMock()
    mgr.driver = object()
    mgr.query_facts = AsyncMock(return_value=facts_result([fact("40 pallets", authority=4, verified=True, origin="fixture")]))
    monkeypatch.setattr(kb.state, "graph_manager", mgr)
    monkeypatch.setattr(config, "CORRECTION_MEMORY_ENABLED", True)
    monkeypatch.setattr(corrections_mod, "query_corrections", AsyncMock(return_value=[{"wrong": "w", "correct": "c"}]))
    monkeypatch.setattr(episodic_memory, "get_episode_hint", AsyncMock(return_value="[Episode Hint]\n• Routing: a"))
    monkeypatch.setattr(kb, "estimate_complexity", lambda q: "moderate")
    return mgr


def state_(**extra):
    return {"input": TEXT, "response_id": "t1", "mode": "default", "tenant_ids": ["user:u1"], "user_id": "u1", **extra}


@pytest.mark.asyncio
async def test_off_by_default_and_template_switch_overrides_the_environment(env, monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_BROKER_ENABLED")
    assert await kb.knowledge_broker_node(state_()) == {}
    assert "knowledge_bundle" in await kb.knowledge_broker_node(state_(knowledge_broker_mode="on"))
    monkeypatch.setenv("KNOWLEDGE_BROKER_ENABLED", "1")
    assert await kb.knowledge_broker_node(state_(knowledge_broker_mode="off")) == {}
    env.query_facts.assert_awaited_once()


@pytest.mark.asyncio
async def test_cache_hit_and_other_modes_do_nothing(env):
    assert await kb.knowledge_broker_node(state_(cache_hit=True)) == {}
    assert await kb.knowledge_broker_node(state_(mode="agent")) == {}
    env.query_facts.assert_not_awaited()


@pytest.mark.asyncio
async def test_trivial_request_without_a_specific_term_is_skipped(env, monkeypatch):
    monkeypatch.setattr(kb, "estimate_complexity", lambda q: "trivial")
    assert await kb.knowledge_broker_node(state_(input="what is two plus two")) == {}
    env.query_facts.assert_not_awaited()
    out = await kb.knowledge_broker_node(state_(input="who is Alice"))  # a proper noun is something to look up
    assert "knowledge_bundle" in out


@pytest.mark.asyncio
async def test_bundle_block_and_state_keys(env):
    out = await kb.knowledge_broker_node(state_())
    bundle = out["knowledge_bundle"]
    json.dumps(bundle)  # plain types only: checkpointed
    assert bundle["sources"] == {"graph": "ok", "corrections": "ok", "episodes": "ok", "cache": "empty", "knowledge_llm": "disabled"}
    assert out["knowledge_entity_hits"] == ["Elm12"] and out["knowledge_gaps"] == ["Birch7"]
    block = out["knowledge_block"]
    assert "[KNOWN FACTS" in block and "Elm12 HAS_CAPACITY 40 pallets (verified, fixture, 2023-11-14)" in block
    assert "[CORRECTIONS" in block and "[OPEN GAPS (no infrastructure knowledge for)]\n- Birch7" in block
    env.query_facts.assert_awaited_once()
    assert env.query_facts.await_args.args[2] == ["user:u1"]  # tenant scoping reaches the graph


@pytest.mark.asyncio
async def test_a_slow_source_degrades_alone(env, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_BROKER_SOURCE_TIMEOUT_S", "0.05")

    async def slow(*a, **k):
        await asyncio.sleep(2)
    env.query_facts = slow
    t0 = time.monotonic()
    out = await kb.knowledge_broker_node(state_())
    assert time.monotonic() - t0 < 1.0
    sources = out["knowledge_bundle"]["sources"]
    assert sources["graph"] == "timeout" and sources["corrections"] == "ok" and sources["episodes"] == "ok"
    assert "[CORRECTIONS" in out["knowledge_block"] and out["knowledge_gaps"] == []  # no graph answer: no claim about gaps


@pytest.mark.asyncio
async def test_a_failing_source_is_recorded_not_raised(env):
    env.query_facts = AsyncMock(side_effect=RuntimeError("neo4j down"))
    out = await kb.knowledge_broker_node(state_())
    assert out["knowledge_bundle"]["sources"]["graph"] == "error" and out["knowledge_block"]


@pytest.mark.asyncio
async def test_a_spent_request_deadline_still_ends_the_request(env):
    with pytest.raises(RequestDeadlineExceeded):
        await kb.knowledge_broker_node(state_(request_deadline_monotonic=time.monotonic() - 1.0))


@pytest.mark.asyncio
async def test_a_template_that_forbids_the_graph_never_queries_it(env):
    out = await kb.knowledge_broker_node(state_(graphrag_template_allowed=False, enable_graphrag=False))
    env.query_facts.assert_not_awaited()
    assert out["knowledge_bundle"]["sources"]["graph"] == "empty" and "[CORRECTIONS" in out["knowledge_block"]


@pytest.mark.asyncio
async def test_conflicting_values_are_reconciled_logged_and_shown(env, monkeypatch):
    logged = []
    monkeypatch.setattr(kb, "log_decision", lambda t, rid, why, meta=None: logged.append((t.value, rid, why)))
    env.query_facts = AsyncMock(return_value=facts_result([fact("40 pallets", authority=4, verified=True, ms=1_000), fact("55 pallets", ms=9_000)]))
    out = await kb.knowledge_broker_node(state_())
    bundle = out["knowledge_bundle"]
    assert [f["object"] for f in bundle["facts"]] == ["40 pallets"]
    assert bundle["conflicts"][0]["rule"] == "authority" and "55 pallets" in bundle["conflicts"][0]["dropped_text"]
    assert logged and logged[0][0] == "KNOWLEDGE_CONFLICT" and "authority" in logged[0][2]
    assert "[CONFLICTS" in out["knowledge_block"] and "also seen Elm12 HAS_CAPACITY 55 pallets" in out["knowledge_block"]


@pytest.mark.asyncio
async def test_cache_metadata_comes_from_the_cache_node(env):
    out = await kb.knowledge_broker_node(state_(cached_facts_meta={"ts": "2026-11-01T00:00:00+00:00", "confidence": 0.8, "distance": 0.2}))
    assert out["knowledge_bundle"]["sources"]["cache"] == "ok"
    assert out["knowledge_bundle"]["cache"]["confidence"] == 0.8


@pytest.mark.asyncio
async def test_nothing_known_gives_an_empty_block_but_keeps_the_gaps(env, monkeypatch):
    env.query_facts = AsyncMock(return_value=facts_result([], hits={}, term_hits={}))
    monkeypatch.setattr(corrections_mod, "query_corrections", AsyncMock(return_value=[]))
    monkeypatch.setattr(episodic_memory, "get_episode_hint", AsyncMock(return_value=""))
    out = await kb.knowledge_broker_node(state_())
    assert out["knowledge_block"] == "" and out["knowledge_gaps"] == ["Elm12", "Birch7"]
    assert out["knowledge_bundle"]["sources"]["graph"] == "empty"


def test_rendered_block_respects_the_character_limit():
    bundle = {"facts": [fact(f"{i} pallets", ms=1_700_000_000_000 + i) for i in range(60)], "conflicts": [], "corrections": [], "gaps": ["X"]}
    block = kb.render_knowledge_block(bundle, max_chars=400)
    assert len(block) <= 400 and bundle["truncated"] is True and block.startswith("[KNOWN FACTS")
    assert kb.render_knowledge_block({"facts": [], "gaps": ["X"]}) == ""  # gaps alone are not worth a block


def test_render_orders_facts_by_authority_then_recency_and_golden_text():
    bundle = {"facts": [fact("a", authority=2, ms=2_000_000_000_000), fact("b", authority=4, verified=True, origin="fixture", ms=1_000_000_000_000)],
              "conflicts": [], "corrections": [{"wrong": "w", "correct": "c"}], "gaps": ["Birch7"]}
    assert kb.render_knowledge_block(bundle) == (
        "[KNOWN FACTS (infrastructure, authoritative first)]\n"
        "- Elm12 HAS_CAPACITY b (verified, fixture, 2001-09-09)\n"
        "- Elm12 HAS_CAPACITY a (extracted, 2033-05-18)\n"
        "\n"
        "[CORRECTIONS (avoid repeating these past errors)]\n"
        "- wrong: w | correct: c\n"
        "\n"
        "[OPEN GAPS (no infrastructure knowledge for)]\n"
        "- Birch7")


# ---- the knowledge model ---------------------------------------------------------------------------------------------------
LLM_STATE = {"knowledge_model_override": "qwen3.6:35b", "knowledge_url_override": "http://127.0.0.1:9/v1"}


def _answers(*items):
    async def fake(state, questions, cfg, **kw):
        return [{"question": q, "answer": a, "confidence": c, "unknown": u, "as_of": "2025-01"} for q, (a, c, u) in zip(questions, items)]
    return fake


@pytest.mark.asyncio
async def test_without_a_configured_model_nothing_is_asked(env, monkeypatch):
    for k in ("KNOWLEDGE_LLM_MODEL", "KNOWLEDGE_LLM_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(kb, "ask_knowledge", AsyncMock(side_effect=AssertionError("no model configured")))
    out = await kb.knowledge_broker_node(state_())
    assert out["knowledge_bundle"]["sources"]["knowledge_llm"] == "disabled" and out["knowledge_bundle"]["llm_answers"] == []


@pytest.mark.asyncio
async def test_gap_and_verification_questions_go_in_one_call_and_the_answers_are_reconciled(env, monkeypatch):
    asked = []
    inner = _answers(("A ward with 12 beds", 0.8, False), ("55", 0.9, False))

    async def fake(state, questions, cfg, **kw):
        asked.append(list(questions))
        return await inner(state, questions, cfg)

    monkeypatch.setattr(kb, "ask_knowledge", fake)
    logged = []
    monkeypatch.setattr(kb, "log_decision", lambda t, rid, why, meta=None: logged.append(why))
    out = await kb.knowledge_broker_node(state_(**LLM_STATE))
    assert len(asked) == 1 and asked[0] == ["What is Birch7? Answer in one or two sentences.", "What is the capacity of Elm12? Answer with the value only."]
    bundle = out["knowledge_bundle"]
    assert bundle["sources"]["knowledge_llm"] == "ok" and bundle["llm_model"] == "qwen3.6:35b"
    assert [f["object"] for f in bundle["llm_answers"]] == ["A ward with 12 beds"]  # the disagreeing capacity answer is not shown as knowledge
    assert any(c["rule"] == "infra_authority" and c["llm"] == "55" for c in bundle["conflicts"]) and any("vs model 55" in w for w in logged)
    block = out["knowledge_block"]
    assert "[UNVERIFIED MODEL KNOWLEDGE (qwen3.6:35b" in block and "A ward with 12 beds" in block and "the model said 55" in block
    assert "Elm12 HAS_CAPACITY 40 pallets" in block and out["knowledge_llm_context"] == "What is Birch7? Answer in one or two sentences. -> A ward with 12 beds"


@pytest.mark.asyncio
async def test_the_model_text_stays_out_of_the_graph_text(env, monkeypatch):
    monkeypatch.setattr(kb, "ask_knowledge", _answers(("A ward with 12 beds", 0.8, False), ("55", 0.9, False)))
    out = await kb.knowledge_broker_node(state_(**LLM_STATE))
    assert "A ward" not in out["knowledge_bundle"]["rendered_graph"] and "55" not in out["knowledge_bundle"]["rendered_graph"]


@pytest.mark.asyncio
async def test_the_number_of_questions_is_capped(env, monkeypatch):
    asked = []

    async def fake(state, questions, cfg, **kw):
        asked.append(list(questions))
        return []

    monkeypatch.setattr(kb, "ask_knowledge", fake)
    await kb.knowledge_broker_node(state_(**LLM_STATE, knowledge_max_questions=1))
    assert len(asked[0]) == 1 and asked[0][0].startswith("What is Birch7")


@pytest.mark.asyncio
async def test_unsure_answers_are_ignored_and_a_timeout_degrades_alone(env, monkeypatch):
    monkeypatch.setattr(kb, "ask_knowledge", _answers(("", 0.0, True), ("55", 0.2, False)))
    out = await kb.knowledge_broker_node(state_(**LLM_STATE))
    assert out["knowledge_bundle"]["llm_answers"] == [] and out["knowledge_bundle"]["sources"]["knowledge_llm"] == "empty"

    async def slow(*a, **k):
        await asyncio.sleep(3)

    monkeypatch.setattr(kb, "ask_knowledge", slow)
    out = await kb.knowledge_broker_node(state_(**LLM_STATE, knowledge_timeout_s=0.5))
    assert out["knowledge_bundle"]["sources"]["knowledge_llm"] == "timeout" and "[KNOWN FACTS" in out["knowledge_block"]


@pytest.mark.asyncio
async def test_when_the_planner_interviews_the_broker_only_checks_attributes(env, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_CONSULT_BY", "planner")
    asked = []

    async def fake(state, questions, cfg, **kw):
        asked.append(list(questions))
        return []

    monkeypatch.setattr(kb, "ask_knowledge", fake)
    await kb.knowledge_broker_node(state_(**LLM_STATE))
    assert asked == [["What is the capacity of Elm12? Answer with the value only."]]


@pytest.mark.asyncio
async def test_a_bundle_with_model_answers_marks_the_commit_payload(env, monkeypatch):
    from services.response_commit import build_response_commit_payload
    monkeypatch.setattr(kb, "ask_knowledge", _answers(("A ward with 12 beds", 0.8, False), ("40", 0.9, False)))
    out = await kb.knowledge_broker_node(state_(**LLM_STATE))
    assert build_response_commit_payload({"input": "q", "final_response": "a", **out})["knowledge_llm_used"] is True


def test_the_trust_score_does_not_read_model_knowledge():
    from services.trust_score import compute_trust_score
    base = {"input": "q", "plan": [{"task": "t", "category": "general"}], "expert_results": ["[m / general]: " + "x" * 80],
            "graph_context": "", "web_research": "", "mcp_evidence": [], "retrieved_graph_chunks": []}
    with_llm = {**base, "knowledge_block": "[UNVERIFIED MODEL KNOWLEDGE]\n- Birch7 -> a ward", "knowledge_llm_context": "Birch7 -> a ward",
                "knowledge_bundle": {"llm_answers": [{"text": "Birch7 -> a ward"}]}}
    a, b = compute_trust_score(dict(base)), compute_trust_score(dict(with_llm))
    assert (a.score, a.verdict) == (b.score, b.verdict)  # a model's claim is no evidence for the answer it produced
