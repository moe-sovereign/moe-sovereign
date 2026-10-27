"""tests/test_agent_enrichment.py — Unit tests for services/agent_enrichment.py.

classify_turn, is_informational, extract_workspace and make_scope are pure,
deterministic functions operating on plain dicts/strings only — no I/O.
agent_graph_context, agent_writeback and agent_cache_lookup are exercised
with AsyncMock/MagicMock stand-ins for redis_client, graph_manager and the
ChromaDB collection (no live Redis/Neo4j/Chroma). Kafka publish and the async
judge call are patched at their import sites in services.agent_enrichment.
"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import agent_enrichment as _ae
from services.agent_enrichment import (
    TurnInfo,
    classify_turn,
    is_informational,
    extract_workspace,
    make_scope,
    agent_cache_lookup,
    agent_graph_context,
    agent_writeback,
    classify_file_action,
    extract_file_touches,
    accumulate_stream_tool_call_delta,
    finalize_stream_tool_calls,
    looks_like_premature_stop,
    _compile_pattern_rows,
    _SEED_PREMATURE_STOP_PATTERNS,
    _classify_tool_ending,
    record_and_classify_tool_ending,
)


# ── classify_turn — Anthropic content-block format ───────────────────────────

class TestClassifyTurnAnthropic:

    def test_initial_task_with_tools_no_tool_result(self):
        messages = [{"role": "user", "content": "Fix the bug in main.py"}]
        info = classify_turn(messages, tools=[{"name": "bash"}], api="anthropic")
        assert info.kind == "initial_task"

    def test_mid_loop_with_tool_result_block(self):
        messages = [
            {"role": "user", "content": "Fix the bug in main.py"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "bash", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "ok"}]},
        ]
        info = classify_turn(messages, tools=[{"name": "bash"}], api="anthropic")
        assert info.kind == "mid_loop"

    def test_tool_result_in_middle_message_still_detected(self):
        messages = [
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "ok"}]},
            {"role": "assistant", "content": "done for now"},
            {"role": "user", "content": "what's next?"},
        ]
        info = classify_turn(messages, tools=[{"name": "bash"}], api="anthropic")
        assert info.kind == "mid_loop"

    def test_pure_text_chat_no_tools(self):
        messages = [{"role": "user", "content": "What is a monad?"}]
        info = classify_turn(messages, tools=None, api="anthropic")
        assert info.kind == "initial_task"

    def test_empty_tools_list(self):
        messages = [{"role": "user", "content": "hi"}]
        info = classify_turn(messages, tools=[], api="anthropic")
        assert info.kind == "initial_task"


# ── classify_turn — OpenAI role format ────────────────────────────────────────

class TestClassifyTurnOpenAI:

    def test_initial_task_no_tool_role(self):
        messages = [{"role": "user", "content": "Explain this repo"}]
        info = classify_turn(messages, tools=[{"type": "function"}], api="openai")
        assert info.kind == "initial_task"

    def test_mid_loop_with_role_tool(self):
        messages = [
            {"role": "user", "content": "Refactor the parser"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
            {"role": "tool", "tool_call_id": "1", "content": "done"},
        ]
        info = classify_turn(messages, tools=[{"type": "function"}], api="openai")
        assert info.kind == "mid_loop"

    def test_object_style_messages_with_role_attribute(self):
        class Msg:
            def __init__(self, role, content):
                self.role = role
                self.content = content

        messages = [Msg("user", "hello"), Msg("tool", "result")]
        info = classify_turn(messages, tools=[{"type": "function"}], api="openai")
        assert info.kind == "mid_loop"


# ── classify_turn — cacheable flag ────────────────────────────────────────────

class TestClassifyTurnCacheable:

    def test_initial_informational_question_is_cacheable(self):
        messages = [{"role": "user", "content": "What is dependency injection?"}]
        info = classify_turn(messages, tools=[{"name": "bash"}], api="anthropic")
        assert info.cacheable is True

    def test_initial_mutation_task_is_not_cacheable(self):
        messages = [{"role": "user", "content": "Fix the failing test in auth.py"}]
        info = classify_turn(messages, tools=[{"name": "bash"}], api="anthropic")
        assert info.cacheable is False

    def test_mid_loop_never_cacheable_even_if_informational(self):
        messages = [
            {"role": "user", "content": "What does this function do?"},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "ok"}]},
        ]
        info = classify_turn(messages, tools=[{"name": "bash"}], api="anthropic")
        assert info.kind == "mid_loop"
        assert info.cacheable is False


# ── is_informational ──────────────────────────────────────────────────────────

class TestIsInformational:

    @pytest.mark.parametrize("query", [
        "What is a monad?",
        "Why does this test fail?",
        "How does the LangGraph state machine work?",
        "Explain the caching layer.",
        "Was macht diese Funktion?",
        "Warum schlägt der Test fehl?",
        "Wie funktioniert der Router?",
        "Erkläre die Architektur dieses Projekts",
    ])
    def test_positive_cases(self, query):
        assert is_informational(query) is True

    @pytest.mark.parametrize("query", [
        "Fix the bug in main.py",
        "Implement a new endpoint for /v1/foo",
        "Refactor the parser module",
        "Ändere die Konfigurationsdatei",
        "Schreibe einen neuen Test",
        "How do I fix this failing test?",   # question form but mutation verb wins
        "Wie behebe ich diesen Fehler?",
        "",
        "   ",
    ])
    def test_negative_cases(self, query):
        assert is_informational(query) is False


# ── extract_workspace / make_scope ────────────────────────────────────────────

class TestExtractWorkspace:

    def test_from_system_text(self):
        system = "You are Claude Code.\nWorking directory: /opt/project/foo\nOther stuff."
        assert extract_workspace(system, []) == "/opt/project/foo"

    def test_falls_back_to_first_user_message(self):
        messages = [{"role": "user", "content": "Working directory: /home/dev/bar\nHelp me."}]
        assert extract_workspace("", messages) == "/home/dev/bar"

    def test_falls_back_to_global(self):
        assert extract_workspace("", [{"role": "user", "content": "hi"}]) == "global"

    def test_no_system_no_messages(self):
        assert extract_workspace("", None) == "global"


class TestMakeScope:

    def test_same_user_same_workspace_same_scope(self):
        assert make_scope("u1", "/proj/a") == make_scope("u1", "/proj/a")

    def test_different_workspace_different_scope(self):
        assert make_scope("u1", "/proj/a") != make_scope("u1", "/proj/b")

    def test_different_user_different_scope(self):
        assert make_scope("u1", "/proj/a") != make_scope("u2", "/proj/a")

    def test_scope_is_16_hex_chars(self):
        scope = make_scope("u1", "/proj/a")
        assert len(scope) == 16
        int(scope, 16)  # raises if not valid hex


# ── agent_cache_lookup ─────────────────────────────────────────────────────────

class TestAgentCacheLookup:

    @pytest.mark.asyncio
    async def test_no_collection_is_a_safe_miss(self):
        result = await agent_cache_lookup("q", "scope", None, None)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_query_is_a_safe_miss(self):
        collection = MagicMock()
        result = await agent_cache_lookup("", "scope", None, collection)
        assert result is None
        collection.query.assert_not_called()

    @pytest.mark.asyncio
    async def test_l0_exact_hit_skips_l1_query(self):
        redis_client = AsyncMock()
        redis_client.get.return_value = "x" * 100
        collection = MagicMock()
        result = await agent_cache_lookup("what is X?", "scope1", redis_client, collection)
        assert result == "x" * 100
        collection.query.assert_not_called()

    @pytest.mark.asyncio
    async def test_l0_short_value_is_ignored_falls_through_to_l1(self):
        redis_client = AsyncMock()
        redis_client.get.return_value = "short"  # <= 50 chars, per interactive-cache convention
        collection = MagicMock()
        collection.query.return_value = {"documents": [[]], "distances": [[]], "metadatas": [[]]}
        result = await agent_cache_lookup("what is X?", "scope1", redis_client, collection)
        assert result is None
        collection.query.assert_called_once()

    @pytest.mark.asyncio
    async def test_l1_hit_high_confidence_fresh_not_flagged(self):
        collection = MagicMock()
        fresh_ts = datetime.now().isoformat()
        collection.query.return_value = {
            "documents": [["cached answer text"]],
            "distances": [[0.10]],
            "metadatas": [[{"confidence": 0.9, "ts": fresh_ts, "flagged": False}]],
        }
        result = await agent_cache_lookup("what is X?", "scope1", None, collection)
        assert result == "cached answer text"
        call_kwargs = collection.query.call_args.kwargs
        assert call_kwargs["where"] == {"scope": "scope1"}

    @pytest.mark.asyncio
    async def test_l1_miss_low_confidence(self):
        collection = MagicMock()
        fresh_ts = datetime.now().isoformat()
        collection.query.return_value = {
            "documents": [["cached answer text"]],
            "distances": [[0.10]],
            "metadatas": [[{"confidence": 0.5, "ts": fresh_ts, "flagged": False}]],
        }
        result = await agent_cache_lookup("what is X?", "scope1", None, collection)
        assert result is None

    @pytest.mark.asyncio
    async def test_l1_miss_stale_entry(self):
        collection = MagicMock()
        stale_ts = (datetime.now() - timedelta(days=30)).isoformat()
        collection.query.return_value = {
            "documents": [["cached answer text"]],
            "distances": [[0.10]],
            "metadatas": [[{"confidence": 0.9, "ts": stale_ts, "flagged": False}]],
        }
        result = await agent_cache_lookup("what is X?", "scope1", None, collection)
        assert result is None

    @pytest.mark.asyncio
    async def test_l1_miss_distance_too_far(self):
        collection = MagicMock()
        fresh_ts = datetime.now().isoformat()
        collection.query.return_value = {
            "documents": [["cached answer text"]],
            "distances": [[0.40]],  # above KNOWLEDGE_BYPASS_THRESHOLD (0.25)
            "metadatas": [[{"confidence": 0.9, "ts": fresh_ts, "flagged": False}]],
        }
        result = await agent_cache_lookup("what is X?", "scope1", None, collection)
        assert result is None

    @pytest.mark.asyncio
    async def test_l1_flagged_entry_never_served(self):
        collection = MagicMock()
        fresh_ts = datetime.now().isoformat()
        collection.query.return_value = {
            "documents": [["bad cached answer"]],
            "distances": [[0.05]],
            "metadatas": [[{"confidence": 0.95, "ts": fresh_ts, "flagged": True}]],
        }
        result = await agent_cache_lookup("what is X?", "scope1", None, collection)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_documents_is_a_miss(self):
        collection = MagicMock()
        collection.query.return_value = {"documents": [[]], "distances": [[]], "metadatas": [[]]}
        result = await agent_cache_lookup("what is X?", "scope1", None, collection)
        assert result is None

    @pytest.mark.asyncio
    async def test_timeout_is_a_safe_miss(self):
        collection = MagicMock()

        def _slow_query(*a, **kw):
            import time as _time
            _time.sleep(1)
            return {"documents": [[]], "distances": [[]], "metadatas": [[]]}

        collection.query.side_effect = _slow_query
        with patch("services.agent_enrichment.AGENT_CACHE_MAX_LOOKUP_MS", 10):
            result = await agent_cache_lookup("what is X?", "scope1", None, collection)
        assert result is None

    @pytest.mark.asyncio
    async def test_query_exception_is_a_safe_miss(self):
        collection = MagicMock()
        collection.query.side_effect = Exception("chroma down")
        result = await agent_cache_lookup("what is X?", "scope1", None, collection)
        assert result is None


# ── agent_writeback ────────────────────────────────────────────────────────────

class TestAgentWriteback:

    @pytest.mark.asyncio
    async def test_short_answer_skipped_no_kafka_no_upsert(self):
        collection = MagicMock()
        with patch("services.agent_enrichment._kafka_publish", new=AsyncMock()) as mock_kafka:
            result = await agent_writeback(
                "what is X?", "a", "scope1", "user:u1", "u1", "model-a", "sess1",
                None, collection,
            )
        assert result is None
        collection.upsert.assert_not_called()
        mock_kafka.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_collection_skipped(self):
        with patch("services.agent_enrichment._kafka_publish", new=AsyncMock()) as mock_kafka:
            result = await agent_writeback(
                "what is X?", "x" * 200, "scope1", "user:u1", "u1", "model-a", "sess1",
                None, None,
            )
        assert result is None
        mock_kafka.assert_not_called()

    @pytest.mark.asyncio
    async def test_clean_answer_upserts_at_initial_confidence_and_publishes_kafka(self):
        collection = MagicMock()
        answer = "x" * 200
        with patch("services.agent_enrichment._kafka_publish", new=AsyncMock()) as mock_kafka, \
             patch("services.agent_enrichment.AGENT_INGEST_JUDGE", False):
            await agent_writeback(
                "what is X?", answer, "scope1", "user:u1", "u1", "model-a", "sess1",
                None, collection,
            )
        collection.upsert.assert_called_once()
        upsert_kwargs = collection.upsert.call_args.kwargs
        assert upsert_kwargs["documents"] == [answer]
        assert upsert_kwargs["metadatas"][0]["confidence"] == 0.6
        assert upsert_kwargs["metadatas"][0]["flagged"] is False
        assert upsert_kwargs["metadatas"][0]["scope"] == "scope1"

        mock_kafka.assert_called_once()
        topic, payload = mock_kafka.call_args.args
        assert payload["input"] == "what is X?"
        assert payload["answer"] == answer
        assert payload["source_model"] == "model-a"
        assert payload["tenant_id"] == "user:u1"
        assert payload["confidence"] == 0.6

    @pytest.mark.asyncio
    async def test_kafka_failure_does_not_raise_and_still_reports_written(self):
        collection = MagicMock()
        answer = "x" * 200
        with patch("services.agent_enrichment._kafka_publish", new=AsyncMock(side_effect=Exception("boom"))), \
             patch("services.agent_enrichment.AGENT_INGEST_JUDGE", False):
            result = await agent_writeback(
                "what is X?", answer, "scope1", "user:u1", "u1", "model-a", "sess1",
                None, collection,
            )
        assert result is None
        collection.upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_upsert_failure_does_not_raise_and_skips_kafka(self):
        collection = MagicMock()
        collection.upsert.side_effect = Exception("chroma down")
        answer = "x" * 200
        with patch("services.agent_enrichment._kafka_publish", new=AsyncMock()) as mock_kafka:
            result = await agent_writeback(
                "what is X?", answer, "scope1", "user:u1", "u1", "model-a", "sess1",
                None, collection,
            )
        assert result is None
        mock_kafka.assert_not_called()

    @pytest.mark.asyncio
    async def test_judge_promotion_updates_confidence_and_writes_l0(self):
        collection = MagicMock()
        redis_client = AsyncMock()
        answer = "x" * 200

        judge_result = MagicMock()
        judge_result.content = "SELF_RATING: 5"

        with patch("services.agent_enrichment._kafka_publish", new=AsyncMock()), \
             patch("services.agent_enrichment.AGENT_INGEST_JUDGE", True), \
             patch("services.inference.ainvoke_judge_llm", new=AsyncMock(return_value=judge_result)):
            await agent_writeback(
                "what is X?", answer, "scope1", "user:u1", "u1", "model-a", "sess1",
                redis_client, collection,
            )
            # The judge promotion runs as a fire-and-forget asyncio.create_task that
            # itself awaits asyncio.to_thread() — give the thread pool a moment.
            await asyncio.sleep(0.05)

        assert collection.upsert.call_count == 2  # initial write + promotion
        promoted_metadata = collection.upsert.call_args.kwargs["metadatas"][0]
        assert promoted_metadata["confidence"] == 0.9
        redis_client.setex.assert_called_once()
        l0_key = redis_client.setex.call_args.args[0]
        assert l0_key.startswith("moe:agent:qcache:scope1:")

    @pytest.mark.asyncio
    async def test_judge_low_score_flags_entry(self):
        collection = MagicMock()
        answer = "x" * 200

        judge_result = MagicMock()
        judge_result.content = "SELF_RATING: 1"

        with patch("services.agent_enrichment._kafka_publish", new=AsyncMock()), \
             patch("services.agent_enrichment.AGENT_INGEST_JUDGE", True), \
             patch("services.inference.ainvoke_judge_llm", new=AsyncMock(return_value=judge_result)):
            await agent_writeback(
                "what is X?", answer, "scope1", "user:u1", "u1", "model-a", "sess1",
                None, collection,
            )
            await asyncio.sleep(0.05)

        assert collection.upsert.call_count == 2
        flagged_metadata = collection.upsert.call_args.kwargs["metadatas"][0]
        assert flagged_metadata["flagged"] is True

    @pytest.mark.asyncio
    async def test_judge_failure_does_not_raise(self):
        collection = MagicMock()
        answer = "x" * 200

        with patch("services.agent_enrichment._kafka_publish", new=AsyncMock()), \
             patch("services.agent_enrichment.AGENT_INGEST_JUDGE", True), \
             patch("services.inference.ainvoke_judge_llm", new=AsyncMock(side_effect=Exception("llm down"))):
            result = await agent_writeback(
                "what is X?", answer, "scope1", "user:u1", "u1", "model-a", "sess1",
                None, collection,
            )
            await asyncio.sleep(0.05)

        assert result is None
        # Only the initial write — judge failed before any promotion/flag upsert.
        assert collection.upsert.call_count == 1

    @pytest.mark.asyncio
    async def test_judge_promote_registers_in_live_monitoring(self):
        """Regression: _agent_judge_promote() posts directly to the judge
        model's node and can evict whatever model the client just used (e.g.
        on a VRAM-constrained node hosting both the tool model and the
        judge) — but ran as a bare asyncio.create_task with no
        _register_active_request call, so it never showed up in MoE-Admin's
        "Laufende API-Anfragen" table. A model vanishing right after a clean
        response, with no visible cause, looked like an unexplained bug.
        """
        collection = MagicMock()
        answer = "x" * 200

        judge_result = MagicMock()
        judge_result.content = "SELF_RATING: 5"

        with patch("services.agent_enrichment._kafka_publish", new=AsyncMock()), \
             patch("services.agent_enrichment.AGENT_INGEST_JUDGE", True), \
             patch("services.inference.ainvoke_judge_llm", new=AsyncMock(return_value=judge_result)), \
             patch("services.agent_enrichment._register_active_request", new=AsyncMock()) as mock_reg, \
             patch("services.agent_enrichment._deregister_active_request", new=AsyncMock()) as mock_dereg:
            await agent_writeback(
                "what is X?", answer, "scope1", "user:u1", "u1", "model-a", "sess1",
                None, collection, api_key_id="key-123",
            )
            await asyncio.sleep(0.05)

        mock_reg.assert_called_once()
        reg_kwargs = mock_reg.call_args.kwargs
        assert reg_kwargs["user_id"] == "u1"
        assert reg_kwargs["api_key_id"] == "key-123"
        assert reg_kwargs["moe_mode"] == "agent_judge_promote"
        mock_dereg.assert_called_once_with(reg_kwargs["chat_id"])


# ── agent_graph_context ────────────────────────────────────────────────────────

class TestAgentGraphContext:

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty_without_touching_graph(self):
        graph_manager = MagicMock()
        graph_manager.query_context = AsyncMock(return_value="should not be called")
        result = await agent_graph_context(
            "", ["user:u1"], "sess", None, graph_manager, max_chars=1000, timeout_s=1.0
        )
        assert result == ""
        graph_manager.query_context.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_graph_manager_returns_empty(self):
        result = await agent_graph_context(
            "what is this?", ["user:u1"], "sess", None, None, max_chars=1000, timeout_s=1.0
        )
        assert result == ""

    @pytest.mark.asyncio
    async def test_l2_cache_hit_skips_graph_query(self):
        redis_client = AsyncMock()
        redis_client.get.return_value = "cached graph context"
        graph_manager = MagicMock()
        graph_manager.query_context = AsyncMock(return_value="should not be called")
        result = await agent_graph_context(
            "what is X?", ["user:u1"], "sess", redis_client, graph_manager,
            max_chars=1000, timeout_s=1.0,
        )
        assert result == "cached graph context"
        graph_manager.query_context.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_miss_queries_graph_and_writes_back(self, monkeypatch):
        redis_client = AsyncMock()
        redis_client.get.return_value = None
        graph_manager = MagicMock()
        graph_manager.driver = MagicMock()
        graph_manager.query_context = AsyncMock(return_value="fresh graph facts")
        import episodic_memory
        monkeypatch.setattr(episodic_memory, "get_episode_hint", AsyncMock(return_value=""))
        result = await agent_graph_context(
            "what is X?", ["user:u1"], "sess", redis_client, graph_manager,
            max_chars=1000, timeout_s=1.0,
        )
        assert result == "fresh graph facts"
        graph_manager.query_context.assert_called_once()
        call_kwargs = graph_manager.query_context.call_args
        assert call_kwargs.kwargs["tenant_ids"] == ["user:u1"]

    @pytest.mark.asyncio
    async def test_result_capped_to_max_chars(self, monkeypatch):
        redis_client = AsyncMock()
        redis_client.get.return_value = None
        graph_manager = MagicMock()
        graph_manager.driver = MagicMock()
        graph_manager.query_context = AsyncMock(return_value="x" * 5000)
        import episodic_memory
        monkeypatch.setattr(episodic_memory, "get_episode_hint", AsyncMock(return_value=""))
        result = await agent_graph_context(
            "what is X?", ["user:u1"], "sess", redis_client, graph_manager,
            max_chars=100, timeout_s=1.0,
        )
        assert len(result) == 100

    @pytest.mark.asyncio
    async def test_timeout_returns_empty_string(self):
        redis_client = AsyncMock()
        redis_client.get.return_value = None
        graph_manager = MagicMock()
        graph_manager.driver = MagicMock()

        async def _slow(*a, **kw):
            import asyncio as _asyncio
            await _asyncio.sleep(10)
            return "too slow"

        graph_manager.query_context = _slow
        result = await agent_graph_context(
            "what is X?", ["user:u1"], "sess", redis_client, graph_manager,
            max_chars=1000, timeout_s=0.01,
        )
        assert result == ""

    @pytest.mark.asyncio
    async def test_different_tenants_get_different_cache_keys(self, monkeypatch):
        redis_client = AsyncMock()
        seen_keys = []

        async def _get(key):
            seen_keys.append(key)
            return None

        redis_client.get.side_effect = _get
        graph_manager = MagicMock()
        graph_manager.driver = MagicMock()
        graph_manager.query_context = AsyncMock(return_value="")
        import episodic_memory
        monkeypatch.setattr(episodic_memory, "get_episode_hint", AsyncMock(return_value=""))
        await agent_graph_context(
            "same query", ["user:u1"], "sess", redis_client, graph_manager,
            max_chars=1000, timeout_s=1.0,
        )
        await agent_graph_context(
            "same query", ["user:u2"], "sess", redis_client, graph_manager,
            max_chars=1000, timeout_s=1.0,
        )
        assert len(seen_keys) == 2
        assert seen_keys[0] != seen_keys[1]


# ── classify_file_action / extract_file_touches — file-touch view (live-
# pipeline-visualization "which files did this Agent Tool Path session
# touch") ─────────────────────────────────────────────────────────────────

class TestClassifyFileAction:
    @pytest.mark.parametrize("name,expected", [
        ("Write", "write"), ("Edit", "write"), ("MultiEdit", "write"),
        ("NotebookEdit", "write"), ("str_replace_editor", "write"),
        ("Read", "read"), ("View", "read"), ("cat_file", "read"),
        ("Grep", "search"), ("Glob", "search"), ("ls", "search"),
        ("Bash", "exec"), ("run_command", "exec"),
        ("TodoWrite", "write"),  # write-verb wins even though it's not a file tool — acceptable heuristic imprecision
        ("WebFetch", "other"),
    ])
    def test_classifies_known_tool_names(self, name, expected):
        assert classify_file_action(name) == expected

    def test_empty_name_is_other(self):
        assert classify_file_action("") == "other"


class TestExtractFileTouches:
    def test_extracts_file_path_key(self):
        touches = extract_file_touches("Edit", {"file_path": "/repo/main.py", "old_string": "a", "new_string": "b"})
        assert touches == [{"path": "/repo/main.py", "action": "write", "tool": "Edit"}]

    def test_extracts_path_key_for_read(self):
        touches = extract_file_touches("Read", {"path": "/repo/README.md"})
        assert touches == [{"path": "/repo/README.md", "action": "read", "tool": "Read"}]

    def test_notebook_path_key(self):
        touches = extract_file_touches("NotebookEdit", {"notebook_path": "/repo/nb.ipynb"})
        assert touches[0]["path"] == "/repo/nb.ipynb"
        assert touches[0]["action"] == "write"

    def test_no_path_key_yields_empty(self):
        assert extract_file_touches("Bash", {"command": "ls -la"}) == []

    def test_url_values_are_skipped(self):
        assert extract_file_touches("Read", {"path": "https://example.com/file.py"}) == []

    def test_non_dict_arguments_never_raises(self):
        assert extract_file_touches("Read", "not-a-dict") == []
        assert extract_file_touches("Read", None) == []

    def test_empty_tool_name_yields_empty(self):
        assert extract_file_touches("", {"path": "/x"}) == []

    def test_multiple_matching_keys_all_recorded(self):
        touches = extract_file_touches("Move", {"path": "/a.py", "target_file": "/b.py"})
        paths = {t["path"] for t in touches}
        assert paths == {"/a.py", "/b.py"}


# ── accumulate_stream_tool_call_delta / finalize_stream_tool_calls ─────────
# OpenAI-style streamed tool_calls: id/name arrive once, arguments arrive as
# JSON-string fragments spread across many delta chunks, keyed by index.

class TestStreamToolCallAccumulation:
    def test_single_call_assembled_across_fragments(self):
        acc: dict = {}
        accumulate_stream_tool_call_delta(acc, 0, {"id": "call_1", "function": {"name": "Read"}})
        accumulate_stream_tool_call_delta(acc, 0, {"function": {"arguments": '{"path": '}})
        accumulate_stream_tool_call_delta(acc, 0, {"function": {"arguments": '"/repo/x.py"}'}})
        result = finalize_stream_tool_calls(acc)
        assert result == [{"name": "Read", "arguments": {"path": "/repo/x.py"}}]

    def test_two_parallel_calls_kept_separate_by_index(self):
        acc: dict = {}
        accumulate_stream_tool_call_delta(acc, 0, {"function": {"name": "Read", "arguments": '{"path":"/a"}'}})
        accumulate_stream_tool_call_delta(acc, 1, {"function": {"name": "Read", "arguments": '{"path":"/b"}'}})
        result = finalize_stream_tool_calls(acc)
        assert {r["arguments"]["path"] for r in result} == {"/a", "/b"}

    def test_incomplete_json_is_dropped_not_raised(self):
        acc: dict = {}
        accumulate_stream_tool_call_delta(acc, 0, {"function": {"name": "Read", "arguments": '{"path": "/x.py"'}})
        assert finalize_stream_tool_calls(acc) == []

    def test_entry_without_name_is_dropped(self):
        acc: dict = {}
        accumulate_stream_tool_call_delta(acc, 0, {"function": {"arguments": '{}'}})
        assert finalize_stream_tool_calls(acc) == []

    def test_malformed_delta_never_raises(self):
        acc: dict = {}
        accumulate_stream_tool_call_delta(acc, 0, {})  # no "function" key at all
        accumulate_stream_tool_call_delta(acc, 0, None)  # type: ignore[arg-type]
        assert finalize_stream_tool_calls(acc) == []


# ── looks_like_premature_stop — the tool_choice=auto silent-stop retry
# heuristic (services/pipeline/chat.py's mid-loop retry) ────────────────────

class TestLooksLikePrematureStop:
    @pytest.fixture(autouse=True)
    def _use_seed_patterns(self):
        """looks_like_premature_stop is now DB-backed (admin_premature_stop_patterns,
        admin-editable without a rebuild) — these tests exercise the same seed data
        that ships as the table's initial content, compiled directly instead of via
        a live Postgres connection, so the detection-shape assertions below stay
        independent of any test DB."""
        rows = [
            {"pattern": pattern, "pattern_type": ptype, "category": cat}
            for pattern, ptype, _lang, cat, _desc in _SEED_PREMATURE_STOP_PATTERNS
        ]
        compiled = _compile_pattern_rows(rows)
        with patch.object(_ae, "_read_premature_stop_patterns", return_value=compiled):
            yield

    @pytest.mark.parametrize("text", [
        "I will now read the file and analyze it.",
        "Let me now check the configuration.",
        "I'll call the search tool to find that.",
        "Step 1: identify the relevant files.",
        "First, I will look at the test suite.",
    ])
    def test_detects_announcement_phrasing(self, text):
        assert looks_like_premature_stop(text) is True

    @pytest.mark.parametrize("text", [
        "The bug was in the off-by-one index on line 42; fixed and tests pass.",
        "This function returns the sum of all even numbers in the list.",
        "Done — the file has been updated and the tests are green.",
        "",
    ])
    def test_does_not_flag_real_answers(self, text):
        assert looks_like_premature_stop(text) is False

    @pytest.mark.parametrize("text", [
        "Lass mich die JWT-Funktion komplett neu schreiben mit garantierter Determinism.",
        "Prüfen wir woher das kommt.",
        "Lass mich alles am Stück fixen:",
        "Ich werde jetzt die Konfigurationsdatei anpassen.",
        "Zunächst muss ich verstehen, warum der Token verloren geht.",
        "Schauen wir uns die Logs genauer an.",
    ])
    def test_detects_german_announcement_phrasing(self, text):
        assert looks_like_premature_stop(text) is True

    @pytest.mark.parametrize("text", [
        "Der Fehler lag in der Token-Validierung; jetzt behoben und Tests laufen grün.",
        "Die Funktion gibt die Summe aller geraden Zahlen zurück.",
    ])
    def test_does_not_flag_real_german_answers(self, text):
        assert looks_like_premature_stop(text) is False

    @pytest.mark.parametrize("text", [
        "Ich fix das jetzt mit einem saubereren Ansatz:",
        "Ich mache das jetzt anders.",
        "Ich löse das jetzt.",
        "Ich probiere es jetzt nochmal.",
    ])
    def test_detects_casual_ich_verb_jetzt_regex(self, text):
        # Confirmed live: doesn't match the literal "ich werde..." phrases —
        # covered by the _PREMATURE_STOP_REGEX fallback instead.
        assert looks_like_premature_stop(text) is True

    @pytest.mark.parametrize("text", [
        "Die Funktion fixt den Fehler korrekt.",
        "Das Ergebnis ist jetzt korrekt.",
        "Der Bug wurde jetzt behoben.",
    ])
    def test_ich_verb_jetzt_regex_does_not_flag_real_answers(self, text):
        assert looks_like_premature_stop(text) is False

    def test_detects_literal_tool_call_markup(self):
        # Confirmed live: qwen3.6:35b writing its tool call as text markup
        # instead of populating the structured tool_calls delta.
        text = (
            "Der $TOKEN ist im Bash-Kontext verloren! Ich lade den Login neu:\n\n"
            "<tool_call>\n<function=Bash>\n<parameter=command>\n"
            "echo test\n</parameter>\n</function>\n</tool_call>"
        )
        assert looks_like_premature_stop(text) is True

    @pytest.mark.parametrize("marker", [
        "<tool_call>", "</tool_call>", "<function=Bash>", "</function>",
        "<parameter=command>", "</parameter>",
    ])
    def test_detects_each_malformed_tool_call_marker(self, marker):
        assert looks_like_premature_stop(f"some text {marker} more text") is True

    def test_does_not_flag_prose_mentioning_functions_generically(self):
        assert looks_like_premature_stop(
            "This function computes the checksum and returns a hex string."
        ) is False

    def test_detects_unclosed_code_fence(self):
        # Confirmed live: qwen3.6:35b announcing a plan, opening a ```mermaid
        # block, then stopping without closing it or calling a tool.
        text = (
            "Alles klar - du willst eine komplett lokale WebUI. Ich erstelle "
            "die komplette Lösung aufgesetzt aus:\n\n```mermaid\ngraph LR\n"
            "    A[Komplette Single-File UI] --> B[Lokales HTML]"
        )
        assert looks_like_premature_stop(text) is True

    def test_does_not_flag_closed_code_fence(self):
        text = (
            "Hier ist die Funktion:\n\n```python\ndef add(a, b):\n"
            "    return a + b\n```\n\nDas war's, alles fertig."
        )
        assert looks_like_premature_stop(text) is False


# ── _classify_tool_ending / record_and_classify_tool_ending — the admin-
# configurable LLM judge for text-only endings that matched no known
# premature-stop pattern (admin_unclassified_tool_endings review queue) ──────

class TestClassifyToolEnding:
    @pytest.mark.asyncio
    async def test_returns_none_when_classifier_disabled(self):
        with patch.object(_ae, "_read_classifier_config",
                           return_value={"model": "gemma4:12b", "base_url": "http://x/v1",
                                         "token": "ollama", "enabled": False}):
            assert await _classify_tool_ending("some text") is None

    @pytest.mark.asyncio
    async def test_returns_none_when_config_unavailable(self):
        with patch.object(_ae, "_read_classifier_config", return_value=None):
            assert await _classify_tool_ending("some text") is None

    @pytest.mark.asyncio
    async def test_parses_valid_json_verdict(self):
        cfg = {"model": "gemma4:12b", "base_url": "http://192.168.155.224:11435/v1",
               "token": "ollama", "enabled": True}
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "message": {"content": '{"premature": true, "reasoning": "announces a plan, never executes"}'}
        }
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch.object(_ae, "_read_classifier_config", return_value=cfg), \
             patch.object(_ae.httpx, "AsyncClient", return_value=mock_client):
            result = await _classify_tool_ending("Ich erstelle die Loesung aus: ```mermaid")
        assert result == {"premature": True, "reasoning": "announces a plan, never executes"}
        mock_client.post.assert_awaited_once()
        call_url = mock_client.post.await_args.args[0]
        assert call_url == "http://192.168.155.224:11435/api/chat"

    @pytest.mark.asyncio
    async def test_returns_none_on_malformed_json(self):
        cfg = {"model": "gemma4:12b", "base_url": "http://x/v1", "token": "ollama", "enabled": True}
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"message": {"content": "not json at all"}}
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch.object(_ae, "_read_classifier_config", return_value=cfg), \
             patch.object(_ae.httpx, "AsyncClient", return_value=mock_client):
            assert await _classify_tool_ending("some text") is None

    @pytest.mark.asyncio
    async def test_returns_none_when_http_call_raises(self):
        cfg = {"model": "gemma4:12b", "base_url": "http://x/v1", "token": "ollama", "enabled": True}
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=ConnectionError("unreachable"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch.object(_ae, "_read_classifier_config", return_value=cfg), \
             patch.object(_ae.httpx, "AsyncClient", return_value=mock_client):
            assert await _classify_tool_ending("some text") is None


class TestRecordAndClassifyToolEnding:
    @pytest.mark.asyncio
    async def test_inserts_then_updates_on_positive_verdict(self):
        with patch.object(_ae, "_insert_unclassified_tool_ending_sync") as mock_insert, \
             patch.object(_ae, "_update_tool_ending_classification_sync") as mock_update, \
             patch.object(_ae, "_classify_tool_ending",
                           AsyncMock(return_value={"premature": True, "reasoning": "test"})):
            await record_and_classify_tool_ending("chat-1", "qwen3.6:35b", "Ich erstelle...", 12)
        mock_insert.assert_called_once()
        assert mock_insert.call_args.args[1:5] == ("chat-1", "qwen3.6:35b", "Ich erstelle...", 12)
        mock_update.assert_called_once()
        assert mock_update.call_args.args[1] == {"premature": True, "reasoning": "test"}

    @pytest.mark.asyncio
    async def test_skips_update_when_classifier_returns_none(self):
        with patch.object(_ae, "_insert_unclassified_tool_ending_sync") as mock_insert, \
             patch.object(_ae, "_update_tool_ending_classification_sync") as mock_update, \
             patch.object(_ae, "_classify_tool_ending", AsyncMock(return_value=None)):
            await record_and_classify_tool_ending("chat-1", "qwen3.6:35b", "text", 3)
        mock_insert.assert_called_once()
        mock_update.assert_not_called()
