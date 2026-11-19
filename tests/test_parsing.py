"""
tests/test_parsing.py — Unit tests for parsing.py helper functions.

All functions under test are pure (no external dependencies), so no mocking or
environment setup is required. Tests are grouped by function and cover the
happy path, edge cases, and documented guard conditions.
"""

import pytest
from unittest.mock import MagicMock

from parsing import (
    _compute_routing_confidence,
    _extract_usage,
    _extract_json,
    _parse_expert_confidence,
    _parse_expert_gaps,
    _expert_category,
    _dedup_by_category,
    _truncate_history as _truncate_history_pure,
    _improvement_ratio,
    _oai_content_to_str,
    _anthropic_content_to_text,
    _extract_images,
    _extract_oai_images,
    _anthropic_to_openai_messages,
    _anthropic_tools_to_openai,
)


# ── _extract_usage ─────────────────────────────────────────────────────────────

class TestExtractUsage:
    def test_usage_metadata_path(self):
        msg = MagicMock()
        msg.usage_metadata = {"input_tokens": 100, "output_tokens": 50}
        result = _extract_usage(msg)
        assert result == {"prompt_tokens": 100, "completion_tokens": 50}

    def test_response_metadata_fallback(self):
        msg = MagicMock()
        msg.usage_metadata = None
        msg.response_metadata = {"token_usage": {"prompt_tokens": 20, "completion_tokens": 10}}
        result = _extract_usage(msg)
        assert result == {"prompt_tokens": 20, "completion_tokens": 10}

    def test_missing_all_metadata_returns_zeros(self):
        msg = MagicMock()
        msg.usage_metadata = None
        msg.response_metadata = {}
        result = _extract_usage(msg)
        assert result == {"prompt_tokens": 0, "completion_tokens": 0}


# ── _extract_json ──────────────────────────────────────────────────────────────

class TestExtractJson:
    def test_plain_json_object(self):
        assert _extract_json('{"key": "value"}') == {"key": "value"}

    def test_json_in_markdown_fence(self):
        text = '```json\n{"task": "research"}\n```'
        assert _extract_json(text) == {"task": "research"}

    def test_json_list(self):
        assert _extract_json('[1, 2, 3]') == [1, 2, 3]

    def test_json_embedded_in_text(self):
        text = 'Sure! Here is the plan: {"tasks": ["search", "analyse"]}. Done.'
        result = _extract_json(text)
        assert result == {"tasks": ["search", "analyse"]}

    def test_invalid_json_returns_none(self):
        assert _extract_json("not json at all") is None

    def test_empty_string_returns_none(self):
        assert _extract_json("") is None


# ── _parse_expert_confidence ───────────────────────────────────────────────────

class TestParseExpertConfidence:
    def test_explicit_high(self):
        result = "Some analysis...\nCONFIDENCE: high\nGAPS: none"
        assert _parse_expert_confidence(result) == "high"

    def test_explicit_medium(self):
        result = "Some analysis...\nCONFIDENCE: MEDIUM\nGAPS: some gaps"
        assert _parse_expert_confidence(result) == "medium"

    def test_explicit_low(self):
        result = "Some analysis...\nCONFIDENCE: low\nGAPS: missing data"
        assert _parse_expert_confidence(result) == "low"

    def test_empty_string_returns_low(self):
        assert _parse_expert_confidence("") == "low"

    def test_too_short_returns_low(self):
        assert _parse_expert_confidence("Short.") == "low"

    def test_error_prefix_returns_low(self):
        assert _parse_expert_confidence("[ERROR: connection failed]") == "low"

    def test_missing_confidence_tag_returns_medium(self):
        long_text = "A " * 30  # >40 chars, no error prefix, no CONFIDENCE tag
        assert _parse_expert_confidence(long_text) == "medium"


# ── _parse_expert_gaps ─────────────────────────────────────────────────────────

class TestParseExpertGaps:
    def test_gaps_and_referral_present(self):
        result = "Analysis...\nGAPS: missing recent data\nREFERRAL: research_expert"
        gaps, referral = _parse_expert_gaps(result)
        assert gaps == "missing recent data"
        assert referral == "research_expert"

    def test_gaps_none_returns_empty(self):
        result = "Analysis...\nGAPS: none\nREFERRAL: —"
        gaps, referral = _parse_expert_gaps(result)
        assert gaps == ""
        assert referral == ""

    def test_no_tags_returns_empty_pair(self):
        gaps, referral = _parse_expert_gaps("No structured output here.")
        assert gaps == ""
        assert referral == ""

    def test_trailing_bullet_stripped(self):
        result = "Analysis...\nGAPS: some gap ·"
        gaps, _ = _parse_expert_gaps(result)
        assert gaps == "some gap"


# ── _expert_category ───────────────────────────────────────────────────────────

class TestExpertCategory:
    def test_standard_header(self):
        result = "[llama3.1:70b / research]: some content"
        assert _expert_category(result) == "research"

    def test_coding_category(self):
        result = "[deepseek:33b / coding]: print('hello')"
        assert _expert_category(result) == "coding"

    def test_no_header_returns_empty(self):
        assert _expert_category("Plain response without header.") == ""


# ── _dedup_by_category ─────────────────────────────────────────────────────────

class TestDedupByCategory:
    def test_high_beats_low_for_same_category(self):
        high = "[llama3/research]: detailed answer\nCONFIDENCE: high"
        low  = "[mistral/research]: short answer\nCONFIDENCE: low"
        result = _dedup_by_category([low, high])
        assert len(result) == 1
        assert result[0] == high

    def test_different_categories_both_kept(self):
        research = "[llama3/research]: some research\nCONFIDENCE: high"
        coding   = "[deepseek/coding]: some code\nCONFIDENCE: medium"
        result = _dedup_by_category([research, coding])
        assert len(result) == 2

    def test_no_category_always_included(self):
        categorised = "[llama3/research]: answer\nCONFIDENCE: high"
        no_cat      = "Plain response with no header at all."
        result = _dedup_by_category([categorised, no_cat])
        assert no_cat in result
        assert categorised in result

    def test_all_no_category_all_returned(self):
        items = ["Plain response one.", "Plain response two.", "Plain response three."]
        result = _dedup_by_category(items)
        assert result == items


# ── _truncate_history ──────────────────────────────────────────────────────────

class TestTruncateHistory:
    def _h(self, pairs):
        """Build a history list from (user, assistant) string pairs."""
        msgs = []
        for u, a in pairs:
            msgs.append({"role": "user", "content": u})
            msgs.append({"role": "assistant", "content": a})
        return msgs

    def test_max_turns_limits_messages(self):
        history = self._h([("q1", "a1"), ("q2", "a2"), ("q3", "a3")])
        result = _truncate_history_pure(history, max_turns=1, max_chars=-1)
        # 1 turn = last 2 messages
        assert len(result) == 2
        assert result[0]["content"] == "q3"

    def test_unlimited_returns_all(self):
        history = self._h([("q1", "a1"), ("q2", "a2")])
        result = _truncate_history_pure(history, max_turns=-1, max_chars=-1)
        assert len(result) == 4

    def test_prometheus_counter_called_for_unlimited(self):
        counter = MagicMock()
        history = self._h([("q1", "a1")])
        _truncate_history_pure(history, max_turns=-1, max_chars=-1, prom_unlimited=counter)
        counter.inc.assert_called_once()

    def test_char_limit_truncates(self):
        long_assistant = "x" * 200
        history = [
            {"role": "user", "content": "short"},
            {"role": "assistant", "content": long_assistant},
        ]
        result = _truncate_history_pure(history, max_turns=-1, max_chars=100)
        # Long assistant answer should be cut or excluded
        total = sum(len(m["content"]) for m in result)
        assert total <= 100 + 1  # +1 for the ellipsis char

    def test_compression_triggers_for_old_answers(self):
        counter = MagicMock()
        # 4-turn history — older assistant answers are compressable
        history = self._h([
            ("q1", "x" * 150),
            ("q2", "x" * 150),
            ("q3", "x" * 150),
        ])
        # max_chars = 300 → total ~900 chars > 200 (2/3 of 300) → compression triggers
        _truncate_history_pure(history, max_turns=-1, max_chars=300, prom_compressed=counter)
        counter.inc.assert_called_once()

    def test_system_messages_excluded(self):
        history = [
            {"role": "system", "content": "You are an assistant"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result = _truncate_history_pure(history, max_turns=-1, max_chars=-1)
        roles = [m["role"] for m in result]
        assert "system" not in roles


# ── _improvement_ratio ─────────────────────────────────────────────────────────

class TestImprovementRatio:
    def test_identical_strings_return_zero(self):
        assert _improvement_ratio("hello world", "hello world") == 0.0

    def test_completely_different_returns_one(self):
        ratio = _improvement_ratio("aaa", "bbb")
        assert ratio == pytest.approx(1.0)

    def test_partial_change(self):
        ratio = _improvement_ratio("hello world", "hello earth")
        assert 0.0 < ratio < 1.0


# ── Content serialisation ──────────────────────────────────────────────────────

class TestOaiContentToStr:
    def test_plain_string(self):
        assert _oai_content_to_str("hello") == "hello"

    def test_none_returns_empty(self):
        assert _oai_content_to_str(None) == ""

    def test_list_of_text_parts(self):
        content = [{"type": "text", "text": "Hello"}, {"type": "text", "text": "World"}]
        assert _oai_content_to_str(content) == "Hello World"

    def test_non_text_parts_ignored(self):
        content = [{"type": "image_url", "image_url": {"url": "data:..."}}, {"type": "text", "text": "caption"}]
        assert _oai_content_to_str(content) == "caption"


class TestAnthropicContentToText:
    def test_plain_string(self):
        assert _anthropic_content_to_text("hello") == "hello"

    def test_text_block_list(self):
        content = [{"type": "text", "text": "Answer: 42"}]
        assert _anthropic_content_to_text(content) == "Answer: 42"

    def test_image_block_appends_annotation(self):
        content = [{"type": "text", "text": "See image"}, {"type": "image", "source": {}}]
        result = _anthropic_content_to_text(content)
        assert "[Bild-Eingabe vorhanden]" in result

    def test_none_returns_empty(self):
        assert _anthropic_content_to_text(None) == ""


class TestExtractImages:
    def test_extracts_base64_image(self):
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "abc123"}}
        ]
        images = _extract_images(content)
        assert len(images) == 1
        assert images[0] == {"media_type": "image/png", "data": "abc123"}

    def test_non_base64_source_skipped(self):
        content = [{"type": "image", "source": {"type": "url", "url": "https://example.com/img.png"}}]
        assert _extract_images(content) == []

    def test_non_list_returns_empty(self):
        assert _extract_images("not a list") == []


class TestExtractOaiImages:
    def test_extracts_data_uri(self):
        content = [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/abc"}}
        ]
        images = _extract_oai_images(content)
        assert len(images) == 1
        assert images[0]["media_type"] == "image/jpeg"
        assert images[0]["data"] == "/9j/abc"

    def test_non_data_uri_skipped(self):
        content = [{"type": "image_url", "image_url": {"url": "https://example.com/img.jpg"}}]
        assert _extract_oai_images(content) == []


# ── Format conversion ──────────────────────────────────────────────────────────

class TestAnthropicToOpenaiMessages:
    def test_plain_text_message(self):
        messages = [{"role": "user", "content": "Hello"}]
        result = _anthropic_to_openai_messages(messages, system=None)
        assert result == [{"role": "user", "content": "Hello"}]

    def test_system_prompt_prepended(self):
        messages = [{"role": "user", "content": "Hi"}]
        result = _anthropic_to_openai_messages(messages, system="Be helpful.")
        assert result[0] == {"role": "system", "content": "Be helpful."}

    def test_tool_use_converted_to_tool_calls(self):
        messages = [{
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "t1", "name": "search", "input": {"q": "test"}}
            ]
        }]
        result = _anthropic_to_openai_messages(messages, system=None)
        assert result[0]["role"] == "assistant"
        assert result[0]["tool_calls"][0]["function"]["name"] == "search"

    def test_tool_result_converted_to_tool_message(self):
        messages = [{
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "Result text"}
            ]
        }]
        result = _anthropic_to_openai_messages(messages, system=None)
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "t1"

    def test_mid_conversation_system_message_merged_not_duplicated(self):
        # Regression: Claude Code can send an in-band system-role message
        # (e.g. a system-reminder) inside `messages`, alongside the top-level
        # `system` field. Forwarding both as separate role:system entries
        # produces a payload some providers reject with HTTP 400 ("System
        # message must be at the beginning") — observed live on Hetzner.
        messages = [
            {"role": "user", "content": "do X"},
            {"role": "assistant", "content": "ok"},
            {"role": "system", "content": "reminder: stay on task"},
            {"role": "user", "content": "continue"},
        ]
        result = _anthropic_to_openai_messages(messages, system="Be helpful.")

        roles = [m["role"] for m in result]
        assert roles.count("system") == 1
        assert roles[0] == "system"
        assert "Be helpful." in result[0]["content"]
        assert "reminder: stay on task" in result[0]["content"]
        assert roles == ["system", "user", "assistant", "user"]

    def test_mid_conversation_system_message_without_top_level_system(self):
        # No top-level `system` at all — the in-band one must still end up
        # as the single leading system message, not wherever it appeared.
        messages = [
            {"role": "user", "content": "do X"},
            {"role": "assistant", "content": "ok"},
            {"role": "system", "content": "reminder"},
            {"role": "user", "content": "continue"},
        ]
        result = _anthropic_to_openai_messages(messages, system=None)
        assert result[0] == {"role": "system", "content": "reminder"}
        assert [m["role"] for m in result] == ["system", "user", "assistant", "user"]

    def test_multiple_mid_conversation_system_messages_all_merged(self):
        messages = [
            {"role": "user", "content": "do X"},
            {"role": "system", "content": "reminder one"},
            {"role": "assistant", "content": "ok"},
            {"role": "system", "content": "reminder two"},
        ]
        result = _anthropic_to_openai_messages(messages, system="Be helpful.")
        roles = [m["role"] for m in result]
        assert roles.count("system") == 1
        assert roles[0] == "system"
        assert "reminder one" in result[0]["content"]
        assert "reminder two" in result[0]["content"]


class TestAnthropicToolsToOpenai:
    def test_converts_input_schema_to_parameters(self):
        tools = [{
            "name": "search",
            "description": "Search the web",
            "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}
        }]
        result = _anthropic_tools_to_openai(tools)
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "search"
        assert "parameters" in result[0]["function"]
        assert "input_schema" not in result[0]["function"]

    def test_missing_input_schema_uses_default(self):
        tools = [{"name": "ping", "description": "Check connectivity"}]
        result = _anthropic_tools_to_openai(tools)
        assert result[0]["function"]["parameters"] == {"type": "object", "properties": {}}


def test_graph_router_extra_categories_raise_graph_confidence_only_when_configured(monkeypatch):
    plan = [{"task": "Retrieve the bed count of Elm12", "category": "compounding_knowledge"}]
    monkeypatch.delenv("GRAPH_ROUTER_EXTRA_CATEGORIES", raising=False)
    _, off = _compute_routing_confidence(plan, "moderate", True)
    monkeypatch.setenv("GRAPH_ROUTER_EXTRA_CATEGORIES", "compounding_knowledge, graph_query")
    _, on = _compute_routing_confidence(plan, "moderate", True)
    assert off == 0.3 and on == 0.7  # 0.3 is below the default FUZZY_GRAPH_THRESHOLD of 0.35, 0.7 above
