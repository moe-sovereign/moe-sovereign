"""An answer that rests on a knowledge model's statements is not written back into the graph by default, and never with the
confidence of infrastructure knowledge."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import services.response_commit as commit
import state as runtime_state

BASE = {"request_id": "r1", "input": "What is Birch7?", "final_response": "Birch7 is a ward with 12 beds.", "response_hash": "h",
        "expert_results": ["[m / general]: Birch7 is a ward with twelve beds on the third floor.\nCONFIDENCE: high"], "plan": [{"category": "general"}], "expert_models_used": ["smollm3"],
        "tenant_ids": ["user:u1"]}


async def _sinks(monkeypatch, **extra):
    publish = AsyncMock()
    import services.kafka as kafka
    monkeypatch.setattr(kafka, "_kafka_publish", publish)
    chroma = MagicMock()
    monkeypatch.setattr(runtime_state, "cache_collection", chroma)
    return await commit._sink_map({**BASE, **extra}), publish, chroma


def test_payload_marks_answers_that_used_model_knowledge():
    st = {"input": "q", "final_response": "a", "knowledge_bundle": {"llm_answers": [{"answer": "x"}]}}
    assert commit.build_response_commit_payload(st)["knowledge_llm_used"] is True
    assert commit.build_response_commit_payload({"input": "q", "final_response": "a"})["knowledge_llm_used"] is False


@pytest.mark.asyncio
async def test_a_normal_answer_is_ingested_as_before(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_LLM_WRITEBACK", raising=False)
    sinks, publish, _ = await _sinks(monkeypatch)
    await sinks["kafka_ingest"]()
    body = publish.await_args.args[1]
    assert body["source_model"] == "smollm3" and body["knowledge_origin"] == "" and body["confidence"] == 0.9


@pytest.mark.asyncio
async def test_a_model_derived_answer_is_not_ingested_by_default(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_LLM_WRITEBACK", raising=False)
    sinks, publish, _ = await _sinks(monkeypatch, knowledge_llm_used=True)
    await sinks["kafka_ingest"]()
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_with_write_back_enabled_it_is_capped_and_marked_as_model_origin(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_LLM_WRITEBACK", "1")
    sinks, publish, _ = await _sinks(monkeypatch, knowledge_llm_used=True)
    await sinks["kafka_ingest"]()
    body = publish.await_args.args[1]
    assert body["source_model"] == "knowledge_llm" and body["knowledge_origin"] == "llm" and body["confidence"] == 0.3
    monkeypatch.setenv("KNOWLEDGE_LLM_WRITEBACK_MAX_CONF", "0.2")
    sinks, publish, _ = await _sinks(monkeypatch, knowledge_llm_used=True)
    await sinks["kafka_ingest"]()
    assert publish.await_args.args[1]["confidence"] == 0.2


@pytest.mark.asyncio
async def test_the_semantic_cache_entry_of_a_model_derived_answer_carries_the_capped_confidence(monkeypatch):
    sinks, _, chroma = await _sinks(monkeypatch, knowledge_llm_used=True)
    await sinks["cache_chroma"]()
    assert chroma.upsert.call_args.kwargs["metadatas"][0]["confidence"] == 0.3
    sinks, _, chroma = await _sinks(monkeypatch)
    await sinks["cache_chroma"]()
    assert chroma.upsert.call_args.kwargs["metadatas"][0]["confidence"] == 0.9
