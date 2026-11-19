"""GraphRAGManager.query_facts(): structured facts with provenance, and query_context() unchanged by the refactor."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from graph_rag.manager import GraphRAGManager, fact_authority, make_fact


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def __aiter__(self):
        async def gen():
            for r in self._rows:
                yield r
        return gen()


def _edge(rel, target, **kw):
    base = {"rel": rel, "target": target, "ttype": "Value", "source_model": "m", "confidence": 0.9, "version": 1,
            "valid_from": 1_700_000_000_000, "created": 1_700_000_000_000, "verified": False, "trust_score": None,
            "source": "extracted", "valid_to": None}
    return {**base, **kw}


def _manager(rows):
    """rows: what the Cypher returns for any term; `session.run` records its parameters."""
    mgr = GraphRAGManager("bolt://unused:7687", "neo4j", "unused")
    calls = []

    async def run(query, parameters=None, **kw):
        calls.append({**(parameters or {}), **kw})
        return _Rows(rows)

    session = AsyncMock()
    session.run = run
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    mgr.driver = MagicMock()
    mgr.driver.session = MagicMock(return_value=cm)
    return mgr, calls


ROWS = [{
    "root": "Elm12", "root_type": "Depot",
    "direct": [_edge("HAS_CAPACITY", "40 pallets", verified=True, source="fixture"),
               _edge("HAS_CAPACITY", "55 pallets", valid_from=1_800_000_000_000)],
    "indirect": [],
}]
TEXT = "What is the capacity of Elm12 depot?"


@pytest.mark.asyncio
async def test_query_context_output_equals_the_rendering_of_query_facts():
    mgr, _ = _manager(ROWS)
    context = await mgr.query_context(TEXT)
    mgr2, _ = _manager(ROWS)
    facts = await mgr2.query_facts(TEXT)
    assert context.startswith("[Knowledge Graph]\n• Elm12 (Depot): ") and "HAS_CAPACITY 40 pallets" in context
    assert facts["rendered"] == context


@pytest.mark.asyncio
async def test_facts_carry_provenance_and_are_json_safe():
    mgr, _ = _manager(ROWS)
    out = await mgr.query_facts(TEXT)
    json.dumps(out["facts"])  # plain types only: the bundle goes into checkpoints
    by_obj = {f["object"]: f for f in out["facts"]}
    assert by_obj["40 pallets"]["authority"] == 4 and by_obj["40 pallets"]["verified"] and by_obj["40 pallets"]["origin"] == "fixture"
    assert by_obj["55 pallets"]["authority"] == 2 and by_obj["55 pallets"]["valid_from_ms"] == 1_800_000_000_000
    assert by_obj["40 pallets"]["id"].startswith("g:") and by_obj["40 pallets"]["text"] == "Elm12 HAS_CAPACITY 40 pallets"
    assert out["entity_hits"] == {"Elm12": "Depot"}


@pytest.mark.asyncio
async def test_term_hits_name_the_terms_that_found_something_and_gaps_are_the_rest():
    mgr, _ = _manager(ROWS)
    out = await mgr.query_facts(TEXT)
    assert out["term_hits"] == {t: ["Elm12"] for t in out["term_hits"]} and out["term_hits"]
    gap_terms = [t for t in out["searched_terms"] if t not in out["term_hits"]]
    assert gap_terms == []  # the mock returns the entity for every term; with no rows every term would be a gap


@pytest.mark.asyncio
async def test_no_match_gives_empty_facts_and_no_llm_steps():
    mgr, calls = _manager([])
    mgr._reformulate_query_for_graph = AsyncMock(side_effect=AssertionError("query_facts must not call an LLM"))
    mgr._text_to_cypher = AsyncMock(side_effect=AssertionError("query_facts must not call an LLM"))
    out = await mgr.query_facts(TEXT)
    assert out["facts"] == [] and out["rendered"] == "" and out["term_hits"] == {} and calls


@pytest.mark.asyncio
async def test_tenant_ids_reach_the_query_and_max_facts_caps_the_list():
    mgr, calls = _manager(ROWS)
    out = await mgr.query_facts(TEXT, tenant_ids=["team:a"], max_facts=1)
    assert all(c["tenant_ids"] == ["team:a"] for c in calls) and len(out["facts"]) == 1


@pytest.mark.asyncio
async def test_superseded_edges_are_not_returned_as_facts():
    rows = [{"root": "Elm12", "root_type": "Depot", "indirect": [],
             "direct": [_edge("HAS_CAPACITY", "40 pallets", valid_to=1_790_000_000_000), _edge("HAS_CAPACITY", "55 pallets")]}]
    mgr, _ = _manager(rows)
    out = await mgr.query_facts(TEXT)
    assert [f["object"] for f in out["facts"]] == ["55 pallets"]


def test_authority_classes():
    assert fact_authority({"verified": True, "source": "extracted"}) == 4
    for src in ("ontology", "ontology_gap_healer", "fixture", "curated"):
        assert fact_authority({"source": src}) == 4
    assert fact_authority({"source": "extracted"}) == 2 and fact_authority({}) == 2
    assert fact_authority({"source": "community_import"}) == 1


def test_make_fact_tolerates_missing_provenance():
    fact = make_fact("A", {"rel": "R", "target": "B"})
    assert fact["valid_from_ms"] is None and fact["confidence"] is None and fact["origin"] == "?" and fact["version"] is None
